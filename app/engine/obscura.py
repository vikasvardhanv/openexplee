import asyncio
import os
import re
import shutil
from typing import Dict, Any, Optional, Set
from urllib.parse import urlparse
from app.config import settings

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
IGNORED_EMAIL_PATTERNS = (".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", "sentry", "example", "wixpress")

# macOS CA bundle locations for BoringSSL in Obscura
MACOS_CA_CERTS = [
    "/opt/homebrew/etc/ca-certificates/cert.pem",
    "/opt/homebrew/etc/openssl@3/cert.pem",
    "/opt/homebrew/etc/openssl@4/cert.pem",
    "/etc/ssl/cert.pem",
]


def get_obscura_bin() -> Optional[str]:
    """Resolves path to obscura binary."""
    # Check configured path relative to project root
    root_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    local_path = os.path.join(root_dir, settings.obscura_bin_path)
    if os.path.isfile(local_path) and os.access(local_path, os.X_OK):
        return local_path

    # Check system PATH
    which_path = shutil.which("obscura")
    if which_path:
        return which_path

    return None


def get_obscura_env() -> Dict[str, str]:
    """Configures environment variables for Obscura (such as SSL CA certificates)."""
    env = os.environ.copy()
    try:
        import certifi
        env["SSL_CERT_FILE"] = certifi.where()
    except ImportError:
        for ca in MACOS_CA_CERTS:
            if os.path.isfile(ca) and ca != "/etc/ssl/cert.pem":
                env["SSL_CERT_FILE"] = ca
                break
    return env


async def fetch_with_obscura(
    url: str,
    stealth: bool = True,
    timeout: float = 25.0,
) -> Dict[str, Any]:
    """
    Fetches and renders a page using Obscura with anti-fingerprint stealth mode.
    Returns clean text, extracted title, and discovered email addresses.
    """
    bin_path = get_obscura_bin()
    if not bin_path:
        raise FileNotFoundError("Obscura binary not found. Run ./scripts/install_obscura.sh")

    if not url.startswith("http"):
        url = "https://" + url

    domain = urlparse(url).netloc.replace("www.", "")
    args = [bin_path, "fetch", url, "--dump", "text"]
    if stealth:
        args.append("--stealth")

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=get_obscura_env(),
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        stdout_str = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr_str = stderr_bytes.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            raise RuntimeError(f"Obscura exited with code {proc.returncode}: {stderr_str}")

        # Extract title from first line if present
        title = ""
        lines = stdout_str.splitlines()
        for line in lines[:5]:
            if " - \"" in line and line.endswith("\""):
                parts = line.split(" - \"", 1)
                title = parts[1].rstrip("\"").strip()
                break

        # Extract emails
        found_emails: Set[str] = set()
        for match in EMAIL_REGEX.findall(stdout_str):
            m_lower = match.lower()
            if not any(bad in m_lower for bad in IGNORED_EMAIL_PATTERNS):
                found_emails.add(m_lower)

        return {
            "domain": domain,
            "url": url,
            "title": title or domain,
            "content_summary": stdout_str[:4500],
            "found_emails": sorted(list(found_emails)),
            "crawler": "obscura_stealth",
            "success": True,
        }
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        raise TimeoutError(f"Obscura fetch timed out after {timeout} seconds")
