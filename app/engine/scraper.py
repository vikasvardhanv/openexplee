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

            # Find internal links to About/Team/Product/Pricing pages
            about_urls = []
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                text = a.get_text(strip=True).lower()
                full_url = urljoin(url, href)
                if urlparse(full_url).netloc == parsed.netloc:
                    if any(kw in text or kw in href.lower() for kw in ["about", "team", "leadership", "founder", "contact", "product", "feature", "pricing", "platform"]):
                        if full_url not in about_urls and full_url != url:
                            about_urls.append(full_url)

            # Crawl up to 4 subpages for deep product & team extraction
            for sub_url in about_urls[:4]:
                try:
                    sub_resp = await client.get(sub_url)
                    if sub_resp.status_code == 200:
                        sub_soup = BeautifulSoup(sub_resp.text, "html.parser")
                        sub_text = " ".join([p.get_text(strip=True) for p in sub_soup.find_all(["p", "li"]) if len(p.get_text(strip=True)) > 20])
                        intel["content_summary"] += f"\n[{sub_url}]: {sub_text[:1500]}"
                        intel["subpages"].append(sub_url)
                        for match in EMAIL_REGEX.findall(sub_resp.text):
                            if not any(ext in match.lower() for ext in [".png", ".jpg", ".svg", ".webp", "sentry", "example"]):
                                intel["found_emails"].add(match.lower())
                except Exception:
                    pass

        # Thor Kernel Browser fallback for JS-heavy SPAs when raw HTML text is sparse
        if len(intel["content_summary"]) < 250:
            try:
                from playwright.async_api import async_playwright
                from app.config import settings

                async with async_playwright() as pw:
                    browser = await pw.chromium.connect_over_cdp(settings.thor_cdp_url, timeout=4000)
                    context = await browser.new_context()
                    page = await context.new_page()
                    await page.goto(url, wait_until="domcontentloaded", timeout=12000)
                    await page.wait_for_timeout(1500)
                    rendered_text = await page.inner_text("body")
                    intel["content_summary"] = (rendered_text or "")[:4000]
                    for match in EMAIL_REGEX.findall(await page.content()):
                        if not any(ext in match.lower() for ext in [".png", ".jpg", ".svg", ".webp", "sentry", "example"]):
                            intel["found_emails"].add(match.lower())
                    await context.close()
                    await browser.close()
            except Exception:
                pass

    except Exception as e:
        intel["error"] = str(e)

    intel["found_emails"] = list(intel["found_emails"])
    return intel

