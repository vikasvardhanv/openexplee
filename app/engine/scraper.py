import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
from typing import Dict, Any, List, Optional

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


async def scrape_company_website(url: str, max_depth: int = 1) -> Dict[str, Any]:
    """
    Crawls target company website and extracts company summary,
    value proposition, and any public contact information.
    """
    if not url.startswith("http"):
        url = "https://" + url

    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")

    intel = {
        "domain": domain,
        "url": url,
        "title": "",
        "description": "",
        "headlines": [],
        "content_summary": "",
        "found_emails": set(),
        "team_leads": [],
        "subpages": [],
    }

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=DEFAULT_HEADERS) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                intel["error"] = f"HTTP {resp.status_code}"
                return intel

            soup = BeautifulSoup(resp.text, "html.parser")

            # Extract basic metadata
            if soup.title:
                intel["title"] = soup.title.string.strip() if soup.title.string else ""

            meta_desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
            if meta_desc and meta_desc.get("content"):
                intel["description"] = meta_desc["content"].strip()

            # Extract major headlines
            for h in soup.find_all(["h1", "h2"])[:6]:
                txt = h.get_text(strip=True)
                if txt and len(txt) > 5:
                    intel["headlines"].append(txt)

            # Extract text content
            paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 20]
            intel["content_summary"] = " ".join(paragraphs[:8])[:2500]

            # Find emails
            for match in EMAIL_REGEX.findall(resp.text):
                if not any(ext in match.lower() for ext in [".png", ".jpg", ".svg", ".webp", "sentry", "example"]):
                    intel["found_emails"].add(match.lower())

            # Find internal links to About/Team pages
            about_urls = []
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                text = a.get_text(strip=True).lower()
                full_url = urljoin(url, href)
                if urlparse(full_url).netloc == parsed.netloc:
                    if any(kw in text or kw in href.lower() for kw in ["about", "team", "leadership", "founder", "contact"]):
                        if full_url not in about_urls and full_url != url:
                            about_urls.append(full_url)

            # Crawl up to 2 subpages (e.g. /about or /team)
            for sub_url in about_urls[:2]:
                try:
                    sub_resp = await client.get(sub_url)
                    if sub_resp.status_code == 200:
                        sub_soup = BeautifulSoup(sub_resp.text, "html.parser")
                        sub_text = " ".join([p.get_text(strip=True) for p in sub_soup.find_all("p") if len(p.get_text(strip=True)) > 20])
                        intel["content_summary"] += f"\n[About/Team Page]: {sub_text[:1500]}"
                        for match in EMAIL_REGEX.findall(sub_resp.text):
                            if not any(ext in match.lower() for ext in [".png", ".jpg", ".svg", ".webp", "sentry", "example"]):
                                intel["found_emails"].add(match.lower())
                except Exception:
                    pass

    except Exception as e:
        intel["error"] = str(e)

    intel["found_emails"] = list(intel["found_emails"])
    return intel
