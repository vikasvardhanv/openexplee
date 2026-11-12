import httpx
import json
import re
from urllib.parse import urlparse, quote_plus
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional, Set
from app.config import settings
from app.database import get_db_connection
from app.engine.scraper import scrape_company_website
from app.engine.verifier import enrich_person_email

DUCKDUCKGO_LITE_URL = "https://html.duckduckgo.com/html/"

PERSON_TITLE_REGEX = re.compile(
    r"\b([A-Z][a-z]{1,20}\s+[A-Z][a-z]{1,20})\s*[,|\-–—]\s*"
    r"(CEO|CTO|COO|CFO|Founder|Co-Founder|Head of [A-Za-z]+|VP [A-Za-z]+|Director of [A-Za-z]+)"
)


async def _get_suppressed_domains() -> Set[str]:
    """Loads suppressed company domains from SQLite so they are never re-targeted."""
    conn = await get_db_connection()
    try:
        cur = await conn.execute("SELECT LOWER(value) AS val FROM suppression_list WHERE entry_type = 'domain'")
        rows = await cur.fetchall()
        return {r["val"].lstrip("@") for r in rows}
    except Exception:
        return set()
    finally:
        await conn.close()


async def _extract_people_with_llm(domain: str, text: str, job_titles: Optional[List[str]] = None) -> List[Dict[str, str]]:
    """Uses Gemini (if configured) or regex heuristics to extract decision-makers."""
    if settings.gemini_api_key and text:
        titles_hint = f"Target roles: {', '.join(job_titles)}." if job_titles else "Focus on Founders, CEO, CTO, VP/Head of Engineering, AI, or Sales."
        prompt = (
            f"Extract real person names and job titles associated with domain {domain} from this text. "
            f"{titles_hint}\nText:\n{text[:3000]}\n\n"
            'Return ONLY valid JSON array of objects: [{"first_name": "...", "last_name": "...", "job_title": "..."}]. '
            "Return [] if no real human names are present."
        )
        try:
            model = settings.default_llm_model or "gemini-2.5-flash"
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={settings.gemini_api_key}"
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    url,
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"response_mime_type": "application/json"},
                    },
                )
                if resp.status_code == 200:
                    raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return [p for p in parsed if p.get("first_name") and p.get("last_name")]
        except Exception:
            pass

    people = []
    seen = set()
    for match in PERSON_TITLE_REGEX.finditer(text or ""):
        full_name, title = match.group(1).strip(), match.group(2).strip()
        parts = full_name.split()
        if len(parts) == 2 and full_name not in seen:
            seen.add(full_name)
            people.append({"first_name": parts[0], "last_name": parts[1], "job_title": title})
    return people[:5]


async def search_duckduckgo(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    """
    Free, zero-credential web search via DuckDuckGo HTML.
    Extracts organic search results (title, snippet, URL).
    """
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Referer": "https://html.duckduckgo.com/",
    }

    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=headers) as client:
            resp = await client.post(DUCKDUCKGO_LITE_URL, data={"q": query, "b": ""})
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                links = soup.find_all("a", class_="result__snippet")
                titles = soup.find_all("a", class_="result__url")
                
                # Alternate parsing for DDG HTML layout
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    # DDG uddg redirect format
                    if "uddg=" in href:
                        real_url = href.split("uddg=")[-1].split("&")[0]
                        from urllib.parse import unquote
                        real_url = unquote(real_url)
                        parsed = urlparse(real_url)
                        if parsed.netloc and not any(skip in parsed.netloc for skip in ["duckduckgo", "google", "bing", "yahoo", "youtube", "facebook", "twitter", "linkedin.com/posts"]):
                            title = a.get_text(strip=True)
                            if len(title) > 3 and not any(r["url"] == real_url for r in results):
                                results.append({
                                    "title": title,
                                    "url": real_url,
                                    "domain": parsed.netloc.replace("www.", "")
                                })
                    if len(results) >= max_results:
                        break
    except Exception as e:
        print(f"[Discovery] DuckDuckGo search error: {e}")

    return results


async def search_serper(query: str, api_key: str, max_results: int = 15) -> List[Dict[str, str]]:
    """Google Search via Serper API if user has configured SERPER_API_KEY."""
    results = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": query, "num": max_results}
            )
            if resp.status_code == 200:
                data = resp.json()
                for org in data.get("organic", []):
                    link = org.get("link", "")
                    parsed = urlparse(link)
                    if parsed.netloc:
                        results.append({
                            "title": org.get("title", ""),
                            "url": link,
                            "domain": parsed.netloc.replace("www.", ""),
                            "snippet": org.get("snippet", "")
                        })
    except Exception as e:
        print(f"[Discovery] Serper search error: {e}")
    return results


async def search_companies_by_domains(domains: List[str]) -> List[Dict[str, Any]]:
    """Bulk-enriches company profiles by domain name (replaces Explee's search_companies_by_domains)."""
    suppressed = await _get_suppressed_domains()
    companies = []
    for raw_domain in domains:
        domain = raw_domain.lower().replace("https://", "").replace("http://", "").replace("www.", "").strip("/ ")
        if not domain or domain in suppressed:
            continue
        intel = await scrape_company_website(f"https://{domain}")
        companies.append({
            "company_name": (intel.get("title") or domain).split("-")[0].split("|")[0].strip(),
            "company_domain": intel["domain"],
            "website_url": intel["url"],
            "description": intel.get("description", ""),
            "summary": intel.get("content_summary", "")[:1000],
            "found_emails": intel.get("found_emails", []),
        })
    return companies


async def search_people_by_domains(
    domains: List[str], job_titles: Optional[List[str]] = None, people_per_company: int = 2
) -> List[Dict[str, Any]]:
    """Finds real decision-makers at target domains and enriches their work emails ($0)."""
    suppressed = await _get_suppressed_domains()
    contacts = []
    role_query = " OR ".join(job_titles[:3]) if job_titles else "CEO OR Founder OR CTO"

    for raw_domain in domains:
        domain = raw_domain.lower().replace("https://", "").replace("http://", "").replace("www.", "").strip("/ ")
        if not domain or domain in suppressed:
            continue

        intel = await scrape_company_website(f"https://{domain}")
        search_hits = await (
            search_serper(f'"{domain}" ({role_query})', settings.serper_api_key, 5)
            if settings.serper_api_key
            else search_duckduckgo(f'"{domain}" ({role_query})', 5)
        )
        combined_text = intel.get("content_summary", "") + "\n" + "\n".join(
            f"{h.get('title', '')} {h.get('snippet', '')}" for h in search_hits
        )

        people = await _extract_people_with_llm(domain, combined_text, job_titles)
        for person in people[:people_per_company]:
            enriched = await enrich_person_email(person["first_name"], person["last_name"], domain)
            contacts.append({
                "company_domain": domain,
                "first_name": person["first_name"],
                "last_name": person["last_name"],
                "contact_name": f"{person['first_name']} {person['last_name']}",
                "job_title": person.get("job_title", "Decision Maker"),
                "email": enriched.get("email"),
                "verification_status": enriched.get("status", "unverified"),
                "verification_details": enriched.get("details", ""),
            })
    return contacts


async def discover_leads_for_icp(icp_prompt: str, max_companies: int = 10) -> List[Dict[str, Any]]:
    """
    Takes a natural-language ICP (e.g. 'B2B SaaS companies in California using Stripe')
    and discovers target companies, decision-makers, and verified emails.
    """
    suppressed_domains = await _get_suppressed_domains()
    search_query = f"{icp_prompt} company -directory -jobs -wikipedia"

    # 1. Execute Search
    raw_results = []
    if settings.serper_api_key:
        raw_results = await search_serper(search_query, settings.serper_api_key, max_companies * 2)

    if not raw_results:
        raw_results = await search_duckduckgo(search_query, max_companies * 2)

    # 2. Filter, Deduplicate & Enforce Suppression List
    seen_domains = set()
    candidate_sites = []
    for r in raw_results:
        d = r.get("domain", "").lower()
        if (
            d
            and d not in seen_domains
            and d not in suppressed_domains
            and not any(skip in d for skip in ["medium.com", "crunchbase", "reddit", "quora", "github"])
        ):
            seen_domains.add(d)
            candidate_sites.append(r)
        if len(candidate_sites) >= max_companies:
            break

    # 3. Deep Crawl Each Site & Extract Decision-Makers
    discovered_leads = []
    for site in candidate_sites:
        intel = await scrape_company_website(site["url"])
        domain = intel["domain"]
        company_name = site.get("title", "").split("-")[0].split("|")[0].strip() or domain

        people = await _extract_people_with_llm(domain, intel.get("content_summary", ""))
        if people:
            p = people[0]
            contact_name = f"{p['first_name']} {p['last_name']}"
            job_title = p.get("job_title", "Decision Maker")
            enriched = await enrich_person_email(p["first_name"], p["last_name"], domain)
            email_candidate = enriched.get("email") or (intel["found_emails"][0] if intel.get("found_emails") else f"hello@{domain}")
        else:
            contact_name = "Founder / Growth Lead"
            job_title = "Decision Maker"
            email_candidate = intel["found_emails"][0] if intel.get("found_emails") else f"hello@{domain}"

        discovered_leads.append({
            "company_name": company_name,
            "company_domain": domain,
            "website_url": intel["url"],
            "contact_name": contact_name,
            "job_title": job_title,
            "email": email_candidate,
            "found_emails": intel.get("found_emails", []),
            "summary": intel.get("content_summary", "")[:1000],
            "description": intel.get("description", ""),
        })

    return discovered_leads

