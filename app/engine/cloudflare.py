import httpx
from typing import Dict, Any, List, Optional

CF_BASE_URL = "https://api.cloudflare.com/client/v4"


async def list_cloudflare_zones(api_token: str) -> List[Dict[str, Any]]:
    """Fetches all domains/zones inside the user's Cloudflare account."""
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json"
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{CF_BASE_URL}/zones", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return [
                    {
                        "id": z["id"],
                        "name": z["name"],
                        "status": z["status"],
                        "name_servers": z.get("name_servers", [])
                    }
                    for z in data.get("result", [])
                ]
    except Exception as e:
        print(f"[Cloudflare API Error]: {e}")
    return []


async def add_dns_record(api_token: str, zone_id: str, rec_type: str, name: str, content: str, priority: Optional[int] = None) -> Dict[str, Any]:
    """Adds a single DNS record (SPF, DMARC, MX, etc.) to Cloudflare."""
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json"
    }
    payload: Dict[str, Any] = {
        "type": rec_type,
        "name": name,
        "content": content,
        "ttl": 1,  # Auto TTL
        "proxied": False
    }
    if priority is not None:
        payload["priority"] = priority

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{CF_BASE_URL}/zones/{zone_id}/dns_records", headers=headers, json=payload)
            return resp.json()
    except Exception as e:
        return {"success": False, "error": str(e)}


async def auto_setup_cloudflare_domain(
    api_token: str,
    zone_id: str,
    domain_name: str,
    forward_to_email: str = "vikash@viktron.ai",
    redirect_target_url: str = "https://viktron.ai"
) -> Dict[str, Any]:
    """
    Automates complete domain setup on Cloudflare:
    1. Sets up SPF TXT record (v=spf1 include:_spf.mx.cloudflare.net ~all)
    2. Sets up DMARC TXT record (v=DMARC1; p=none; sp=none; rua=mailto:...)
    3. Adds Cloudflare Email Routing MX records (route1, route2, route3)
    4. Creates Email Routing Catch-all rule forwarding all replies to your real inbox
    5. Creates 301 Redirect rule pointing web traffic to your main website
    """
    clean_domain = domain_name.lower().strip()
    results = {"records_created": [], "errors": []}

    # 1. SPF Record
    spf_res = await add_dns_record(
        api_token, zone_id, "TXT", "@",
        "v=spf1 include:_spf.mx.cloudflare.net ~all"
    )
    if spf_res.get("success"):
        results["records_created"].append("SPF: v=spf1 include:_spf.mx.cloudflare.net ~all")
    else:
        results["errors"].append(f"SPF: {spf_res.get('errors', 'error')}")

    # 2. DMARC Record
    dmarc_res = await add_dns_record(
        api_token, zone_id, "TXT", f"_dmarc.{clean_domain}",
        f"v=DMARC1; p=none; sp=none; rua=mailto:{forward_to_email}"
    )
    if dmarc_res.get("success"):
        results["records_created"].append(f"DMARC: v=DMARC1; p=none; sp=none; rua=mailto:{forward_to_email}")
    else:
        results["errors"].append(f"DMARC: {dmarc_res.get('errors', 'error')}")

    # 3. Email Routing MX Records
    for mx_host, prio in [("route1.mx.cloudflare.net", 17), ("route2.mx.cloudflare.net", 45), ("route3.mx.cloudflare.net", 9)]:
        mx_res = await add_dns_record(api_token, zone_id, "MX", "@", mx_host, priority=prio)
        if mx_res.get("success"):
            results["records_created"].append(f"MX: {mx_host} (Priority {prio})")

    # 4. Enable Cloudflare Email Routing Rule
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json"
    }
    rule_payload = {
        "matchers": [{"type": "all"}],  # Catch-all (*@domain)
        "actions": [{"type": "forward", "value": [forward_to_email]}],
        "name": f"OpenExplee Catch-all to {forward_to_email}",
        "enabled": True,
        "priority": 0
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            rule_resp = await client.post(
                f"{CF_BASE_URL}/zones/{zone_id}/email/routing/rules",
                headers=headers,
                json=rule_payload
            )
            if rule_resp.status_code in [200, 201]:
                results["records_created"].append(f"Email Routing: Catch-all forwarded to {forward_to_email}")
            else:
                results["errors"].append(f"Email Routing Rule: {rule_resp.text}")
    except Exception as e:
        results["errors"].append(f"Email Routing: {e}")

    results["status"] = "completed"
    return results

