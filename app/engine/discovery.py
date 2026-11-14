import httpx
import json
import re
import base64
from urllib.parse import urlparse, quote_plus, parse_qs
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
    """Uses Claude CLI / Thor Ollama / LLM or regex heuristics to extract decision-makers."""
    if text:
        from app.engine.llm import call_llm_json

        titles_hint = f"Target roles: {', '.join(job_titles)}." if job_titles else "Focus on Founders, CEO, CTO, VP/Head of Engineering, AI, or Sales."
        prompt = (
            f"Extract real person names and job titles associated with domain {domain} from this text. "
            f"{titles_hint}\nText:\n{text[:3000]}\n\n"
            'Return ONLY a valid JSON array of objects: [{"first_name": "...", "last_name": "...", "job_title": "..."}]. '
            "Return [] if no real human names are present."
        )
        parsed = await call_llm_json(prompt)
        if isinstance(parsed, list):
            extracted = [p for p in parsed if isinstance(p, dict) and p.get("first_name") and p.get("last_name")]
            if extracted:
                return extracted[:5]

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
                
                # Extract from result__url and standard links
                skip_hosts = {"duckduckgo.com", "bing.com", "doubleclick.net", "google.com", "yahoo.com", "youtube.com", "facebook.com", "twitter.com", "x.com"}
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    real_url = None
                    if "uddg=" in href:
                        raw = href.split("uddg=")[-1].split("&")[0]
                        from urllib.parse import unquote
                        real_url = unquote(raw)
                    elif href.startswith("http://") or href.startswith("https://"):
                        real_url = href

                    if real_url:
                        parsed = urlparse(real_url)
                        domain = parsed.netloc.replace("www.", "").lower()
                        if domain and not any(skip in domain for skip in skip_hosts) and not domain.endswith(".edu") and not domain.endswith(".gov"):
                            title = a.get_text(strip=True) or domain
                            if not any(r["domain"] == domain for r in results):
                                results.append({
                                    "title": title[:100],
                                    "url": f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
                                    "domain": domain
                                })
                    if len(results) >= max_results:
                        break
    except Exception as e:
        print(f"[Discovery] DuckDuckGo search error: {e}")

    return results


async def search_bing(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    """
    Organic web search fallback via Bing HTML with redirect decoding.
    """
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    encoded = quote_plus(query)
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=headers) as client:
            resp = await client.get(f"https://www.bing.com/search?q={encoded}")
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for li in soup.find_all("li", class_="b_algo"):
                    h2 = li.find("h2")
                    a = h2.find("a") if h2 else None
                    if not a or not a.get("href"):
                        continue
                    href = a["href"]
                    title = a.get_text(strip=True)
                    real_url = href
                    if "/ck/a?" in href:
                        parsed_q = parse_qs(urlparse(href).query)
                        u_val = parsed_q.get("u", [""])[0]
                        if u_val and len(u_val) > 2:
                            try:
                                b64 = u_val[2:] + "=" * (-len(u_val[2:]) % 4)
                                decoded = base64.b64decode(b64).decode("utf-8", errors="ignore")
                                if decoded.startswith("http"):
                                    real_url = decoded
                            except Exception:
                                pass
                    parsed_dest = urlparse(real_url)
                    domain = parsed_dest.netloc.replace("www.", "").lower()
                    if domain and "bing.com" not in domain:
                        results.append({
                            "title": title[:100],
                            "url": f"{parsed_dest.scheme}://{parsed_dest.netloc}{parsed_dest.path}",
                            "domain": domain
                        })
                    if len(results) >= max_results:
                        break
    except Exception as e:
        print(f"[Discovery] Bing search error: {e}")

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


# Hosts that list or write about companies. A lead must be the company's own site.
NON_COMPANY_HOSTS = (
    "linkedin.", "indeed.", "glassdoor.", "ziprecruiter.", "builtin.", "wellfound.", "angel.co", "startup.jobs",
    "joinhandshake.", "monster.", "simplyhired.", "lever.co", "greenhouse.io", "ashbyhq.", "workable.",
    "ycombinator.", "crunchbase.", "g2.com", "capterra.", "producthunt.", "zoominfo.", "getlatka.", "clutch.co",
    "forbes.", "techcrunch.", "medium.", "substack.", "reddit.", "quora.", "wikipedia.", "github.",
    "nytimes.", "wsj.", "bloomberg.", "businessinsider.", "inc.com", "entrepreneur.", "cnbc.",
    "google.", "bing.", "yahoo.", "duckduckgo.", "msn.", "ibm.", "microsoft.", "apple.", "amazon.",
    "merriam-webster.", "cambridge.", "thesaurus.", "britannica.", ".edu", ".gov", ".ac.uk",
)


async def _search_queries_for_icp(icp_prompt: str) -> List[str]:
    """Turns a long plain-English ICP into short web searches that return company homepages.
    A whole ICP paragraph as one query returns list articles and job boards, not companies."""
    from app.engine.llm import call_llm_json

    parsed = await call_llm_json(
        "Write 4 short web search queries (3 to 7 words each) that return the homepages of companies "
        f"matching this target customer description:\n{icp_prompt}\n\n"
        "Each query names one kind of company or product, not people and not the word 'companies'. "
        'Return ONLY a JSON array of strings, for example ["ai code review tool startup"].'
    )
    queries = [q.strip() for q in parsed if isinstance(q, str) and q.strip()][:4] if isinstance(parsed, list) else []
    # No LLM answer: the first words of the ICP still beat the whole paragraph.
    return queries or [" ".join(icp_prompt.split()[:10])]


async def _suggest_domains_via_llm(icp_prompt: str, count: int = 8) -> List[Dict[str, str]]:
    """Identifies real active company domains matching the ICP using LLM intelligence."""
    from app.engine.llm import call_llm_json

    prompt = (
        f"You are a sales intelligence engine. Given this Ideal Customer Profile:\n'{icp_prompt}'\n\n"
        f"Name {count} real, active companies or startups (no job boards, no aggregators, no universities) that match.\n"
        'Return ONLY a JSON array of objects: [{"company_name": "...", "domain": "..."}].'
    )
    parsed = await call_llm_json(prompt)
    if isinstance(parsed, list):
        clean = []
        for item in parsed:
            if isinstance(item, dict) and item.get("domain"):
                d = item["domain"].lower().replace("https://", "").replace("http://", "").replace("www.", "").strip("/ ")
                name = item.get("company_name") or d
                clean.append({"title": name, "domain": d, "url": f"https://{d}"})
        return clean
    return []


async def discover_leads_for_icp(icp_prompt: str, max_companies: int = 10) -> List[Dict[str, Any]]:
    """
    Takes a natural-language ICP (e.g. 'B2B SaaS companies in California using Stripe')
    and discovers target companies, decision-makers, and verified emails.
    """
    suppressed_domains = await _get_suppressed_domains()

    # 1. Gather candidate domains from AI domain intelligence and web search
    raw_results = []
    llm_domains = await _suggest_domains_via_llm(icp_prompt, count=max_companies)
    raw_results.extend(llm_domains)

    # 2. Execute Web Search across search engines to discover additional organic hits
    for search_query in await _search_queries_for_icp(icp_prompt):
        found = []
        if settings.serper_api_key:
            found = await search_serper(search_query, settings.serper_api_key, max_companies * 2)
        if not found:
            found = await search_bing(search_query, max_companies * 2)
        if not found:
            found = await search_duckduckgo(search_query, max_companies * 2)
        raw_results.extend(found)

    # 3. Filter, Deduplicate & Enforce Suppression List
    seen_domains = set()
    candidate_sites = []
    for r in raw_results:
        d = r.get("domain", "").lower()
        if (
            d
            and d not in seen_domains
            and d not in suppressed_domains
            and not any(skip in d for skip in NON_COMPANY_HOSTS)
        ):
            seen_domains.add(d)
            candidate_sites.append(r)
        if len(candidate_sites) >= max_companies:
            break

    # 4. Deep Crawl Each Site & Extract Decision-Makers
    discovered_leads = []
    for site in candidate_sites:
        intel = await scrape_company_website(site["url"])
        domain = intel["domain"]
        # Sites carry placeholder and third-party addresses (you@yourcompany.com). Keep the company's own.
        intel["found_emails"] = [e for e in intel.get("found_emails", []) if e.endswith("@" + domain) or e.endswith("." + domain)]
        company_name = site.get("title", "").split("-")[0].split("|")[0].strip() or domain

        people = await _extract_people_with_llm(domain, intel.get("content_summary", ""))
        if people:
            p = people[0]
            contact_name = f"{p['first_name']} {p['last_name']}"
            job_title = p.get("job_title", "Decision Maker")
            enriched = await enrich_person_email(p["first_name"], p["last_name"], domain)
            email_candidate = enriched.get("email") or (intel["found_emails"][0] if intel.get("found_emails") else f"hello@{domain}")
        elif intel.get("found_emails"):
            contact_name = "Founder / Growth Lead"
            job_title = "Decision Maker"
            email_candidate = intel["found_emails"][0]
        else:
            # Fall back to role-based address at the verified domain
            contact_name = f"Founder / Hiring Lead"
            job_title = "Decision Maker"
            email_candidate = f"contact@{domain}"

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

