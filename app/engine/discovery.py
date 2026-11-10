import httpx
import re
from urllib.parse import urlparse, quote_plus
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
from app.config import settings
from app.engine.scraper import scrape_company_website

DUCKDUCKGO_LITE_URL = "https://html.duckduckgo.com/html/"


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


async def discover_leads_for_icp(icp_prompt: str, max_companies: int = 10) -> List[Dict[str, Any]]:
    """
    Takes a natural-language ICP (e.g. 'B2B SaaS companies in California using Stripe')
    and discovers target companies, websites, and contacts.
    """
    search_query = f"{icp_prompt} company -directory -jobs -wikipedia"
    
    # 1. Execute Search
    raw_results = []
    if settings.serper_api_key:
        raw_results = await search_serper(search_query, settings.serper_api_key, max_companies * 2)
    
    if not raw_results:
        raw_results = await search_duckduckgo(search_query, max_companies * 2)

    # 2. Filter & Deduplicate Domains
    seen_domains = set()
    candidate_sites = []
    for r in raw_results:
        d = r.get("domain", "")
        if d and d not in seen_domains and not any(skip in d for skip in ["medium.com", "crunchbase", "reddit", "quora", "github"]):
            seen_domains.add(d)
            candidate_sites.append(r)
        if len(candidate_sites) >= max_companies:
            break

    # 3. Deep Crawl Each Site to Extract Intelligence
    discovered_leads = []
    for site in candidate_sites:
        intel = await scrape_company_website(site["url"])
        company_name = site.get("title", "").split("-")[0].split("|")[0].strip() or intel.get("domain", "")
        
        # Candidate contact name (fallback or extracted)
        contact_name = "Founder / Growth Lead"
        job_title = "Decision Maker"
        email_candidate = intel["found_emails"][0] if intel.get("found_emails") else f"hello@{intel['domain']}"

        discovered_leads.append({
            "company_name": company_name,
            "company_domain": intel["domain"],
            "website_url": intel["url"],
            "contact_name": contact_name,
            "job_title": job_title,
            "email": email_candidate,
            "found_emails": intel.get("found_emails", []),
            "summary": intel.get("content_summary", "")[:1000],
            "description": intel.get("description", "")
        })

    return discovered_leads
