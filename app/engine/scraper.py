import asyncio
import re
from typing import Dict, Any, List, Optional, Set
from urllib.parse import urljoin, urlparse
import httpx
from bs4 import BeautifulSoup

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SUBPAGE_KEYWORDS = (
    "about",
    "team",
    "leadership",
    "founder",
    "contact",
    "product",
    "feature",
    "pricing",
    "platform",
    "careers",
    "people",
)

IGNORED_EMAIL_PATTERNS = (".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", "sentry", "example", "wixpress")


def _clean_emails(raw_text: str) -> Set[str]:
    """Finds and filters valid email addresses from text or markup."""
    valid = set()
    for match in EMAIL_REGEX.findall(raw_text or ""):
        m_lower = match.lower()
        if not any(bad in m_lower for bad in IGNORED_EMAIL_PATTERNS):
            valid.add(m_lower)
    return valid


async def _scrape_crawl4ai(url: str, max_depth: int = 1) -> Dict[str, Any]:
    """
    Crawls a target website using Crawl4AI (Chromium/Patchright engine).
    Produces clean LLM-friendly markdown, metadata, contact emails, and crawls key subpages.
    """
    from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode

    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")

    intel: Dict[str, Any] = {
        "domain": domain,
        "url": url,
        "title": "",
        "description": "",
        "headlines": [],
        "content_summary": "",
        "found_emails": set(),
        "team_leads": [],
        "subpages": [],
        "crawler": "crawl4ai",
    }

    config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        word_count_threshold=5,
        page_timeout=15000,
    )

    async with AsyncWebCrawler(verbose=False) as crawler:
        result = await crawler.arun(url=url, config=config)

        if not result.success:
            raise RuntimeError(f"Crawl4AI failed for {url}: {result.error_message}")

        # Metadata extraction
        if result.metadata:
            intel["title"] = (result.metadata.get("title") or "").strip()
            intel["description"] = (result.metadata.get("description") or "").strip()

        # Markdown content
        raw_md = result.markdown or ""
        intel["content_summary"] = raw_md[:4500]

        # Extract headlines (#, ##, ###)
        headlines = re.findall(r"^#{1,3}\s+(.+)$", raw_md, flags=re.MULTILINE)
        intel["headlines"] = [h.strip() for h in headlines if len(h.strip()) > 3][:8]

        # Extract emails from markdown and cleaned HTML
        intel["found_emails"].update(_clean_emails(raw_md))
        if result.cleaned_html:
            intel["found_emails"].update(_clean_emails(result.cleaned_html))

        # Detect high-value internal subpages (About, Team, Pricing, Contact, etc.)
        subpage_candidates: List[str] = []
        internal_links = result.links.get("internal", []) if result.links else []
        for link in internal_links:
            href = link.get("href") or ""
            link_text = (link.get("text") or "").lower()
            if not href.startswith("http"):
                href = urljoin(url, href)

            dest_parsed = urlparse(href)
            if dest_parsed.netloc.replace("www.", "") == domain:
                href_clean = f"{dest_parsed.scheme}://{dest_parsed.netloc}{dest_parsed.path}"
                if href_clean != url and href_clean not in subpage_candidates:
                    if any(kw in link_text or kw in dest_parsed.path.lower() for kw in SUBPAGE_KEYWORDS):
                        subpage_candidates.append(href_clean)

        # Deep-crawl up to 4 relevant subpages if max_depth > 0
        if max_depth > 0 and subpage_candidates:
            for sub_url in subpage_candidates[:4]:
                try:
                    sub_res = await crawler.arun(url=sub_url, config=config)
                    if sub_res.success:
                        intel["subpages"].append(sub_url)
                        sub_md = sub_res.markdown or ""
                        intel["content_summary"] += f"\n\n--- Subpage: {sub_url} ---\n" + sub_md[:2000]
                        intel["found_emails"].update(_clean_emails(sub_md))
                        if sub_res.cleaned_html:
                            intel["found_emails"].update(_clean_emails(sub_res.cleaned_html))
                except Exception:
                    pass

    intel["found_emails"] = sorted(list(intel["found_emails"]))
    return intel


async def _scrape_httpx_fallback(url: str, max_depth: int = 1) -> Dict[str, Any]:
    """
    Fast HTTP fallback using httpx + BeautifulSoup if browser-based Crawl4AI is unavailable.
    """
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")

    intel: Dict[str, Any] = {
        "domain": domain,
        "url": url,
        "title": "",
        "description": "",
        "headlines": [],
        "content_summary": "",
        "found_emails": set(),
        "team_leads": [],
        "subpages": [],
        "crawler": "httpx_fallback",
    }

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=DEFAULT_HEADERS) as client:
        resp = await client.get(url)
        if resp.status_code >= 400:
            intel["error"] = f"HTTP {resp.status_code}"
            return intel

        soup = BeautifulSoup(resp.text, "html.parser")
        if soup.title and soup.title.string:
            intel["title"] = soup.title.string.strip()

        meta_desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
        if meta_desc and meta_desc.get("content"):
            intel["description"] = meta_desc["content"].strip()

        for h in soup.find_all(["h1", "h2"])[:6]:
            txt = h.get_text(strip=True)
            if txt and len(txt) > 5:
                intel["headlines"].append(txt)

        paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 20]
        intel["content_summary"] = " ".join(paragraphs[:8])[:2500]
        intel["found_emails"].update(_clean_emails(resp.text))

        about_urls = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            text = a.get_text(strip=True).lower()
            full_url = urljoin(url, href)
            if urlparse(full_url).netloc == parsed.netloc:
                if any(kw in text or kw in href.lower() for kw in SUBPAGE_KEYWORDS):
                    if full_url not in about_urls and full_url != url:
                        about_urls.append(full_url)

        if max_depth > 0:
            for sub_url in about_urls[:4]:
                try:
                    sub_resp = await client.get(sub_url)
                    if sub_resp.status_code == 200:
                        sub_soup = BeautifulSoup(sub_resp.text, "html.parser")
                        sub_text = " ".join([p.get_text(strip=True) for p in sub_soup.find_all(["p", "li"]) if len(p.get_text(strip=True)) > 20])
                        intel["content_summary"] += f"\n[{sub_url}]: {sub_text[:1500]}"
                        intel["subpages"].append(sub_url)
                        intel["found_emails"].update(_clean_emails(sub_resp.text))
                except Exception:
                    pass

    intel["found_emails"] = sorted(list(intel["found_emails"]))
    return intel


async def scrape_company_website(url: str, max_depth: int = 1) -> Dict[str, Any]:
    """
    Crawls target company website using Crawl4AI with automatic HTTP fallback.
    Extracts structured markdown, metadata, headlines, emails, and subpage content.
    """
    if not url.startswith("http"):
        url = "https://" + url

    try:
        return await _scrape_crawl4ai(url, max_depth=max_depth)
    except Exception as exc:
        print(f"[Scraper] Crawl4AI attempt on {url} encountered: {exc}. Trying Obscura stealth browser.")
        try:
            from app.engine.obscura import fetch_with_obscura
            return await fetch_with_obscura(url, stealth=True)
        except Exception as obs_exc:
            print(f"[Scraper] Obscura attempt on {url} encountered: {obs_exc}. Falling back to HTTP.")
            try:
                return await _scrape_httpx_fallback(url, max_depth=max_depth)
            except Exception as e:
                return {
                    "domain": urlparse(url).netloc.replace("www.", ""),
                    "url": url,
                    "title": "",
                    "description": "",
                    "headlines": [],
                    "content_summary": "",
                    "found_emails": [],
                    "team_leads": [],
                    "subpages": [],
                    "error": str(e),
                    "crawler": "failed",
                }
