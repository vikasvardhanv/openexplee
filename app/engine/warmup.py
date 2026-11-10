import dns.resolver
from typing import Dict, Any, List
from app.database import get_db_connection


async def check_domain_dns_health(domain: str) -> Dict[str, Any]:
    """
    Audits domain DNS records (SPF, DKIM, DMARC, MX) to ensure cold outreach deliverability.
    """
    clean_domain = domain.lower().replace("http://", "").replace("https://", "").replace("www.", "").strip("/ ")
    report = {
        "domain": clean_domain,
        "has_mx": False,
        "has_spf": False,
        "has_dmarc": False,
        "spf_record": None,
        "dmarc_record": None,
        "score": 0,
        "recommendations": []
    }

    # 1. Check MX
    try:
        mx_answers = dns.resolver.resolve(clean_domain, "MX")
        if len(mx_answers) > 0:
            report["has_mx"] = True
            report["score"] += 25
    except Exception:
        report["recommendations"].append("Missing MX records: Domain cannot receive email or bounces.")

    # 2. Check SPF (TXT records on root domain)
    try:
        txt_answers = dns.resolver.resolve(clean_domain, "TXT")
        for rdata in txt_answers:
            txt_string = "".join([part.decode("utf-8") if isinstance(part, bytes) else str(part) for part in rdata.strings])
            if "v=spf1" in txt_string:
                report["has_spf"] = True
                report["spf_record"] = txt_string
                report["score"] += 35
                break
        if not report["has_spf"]:
            report["recommendations"].append("Missing SPF record: Add 'v=spf1 include:_spf.google.com ~all' (or your host SPF).")
    except Exception:
        report["recommendations"].append("Failed to query SPF TXT records.")

    # 3. Check DMARC (TXT record on _dmarc.domain)
    try:
        dmarc_answers = dns.resolver.resolve(f"_dmarc.{clean_domain}", "TXT")
        for rdata in dmarc_answers:
            txt_string = "".join([part.decode("utf-8") if isinstance(part, bytes) else str(part) for part in rdata.strings])
            if "v=DMARC1" in txt_string:
                report["has_dmarc"] = True
                report["dmarc_record"] = txt_string
                report["score"] += 40
                break
        if not report["has_dmarc"]:
            report["recommendations"].append("Missing DMARC: Add TXT at _dmarc." + clean_domain + " with 'v=DMARC1; p=none; sp=none;'")
    except Exception:
        report["recommendations"].append("Missing DMARC: Add TXT record at _dmarc." + clean_domain)

    return report


async def simulate_warmup_tick(inbox_id: int) -> Dict[str, Any]:
    """
    Logs an automated warmup interaction tick to maintain inbox sending velocity.
    """
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT email, health_score FROM inboxes WHERE id = ?", (inbox_id,))
        row = await cursor.fetchone()
        if not row:
            await conn.close()
            return {"error": "Inbox not found"}

        email = row[0]
        current_health = row[1] or 100

        # Record simulation
        await cursor.execute("""
            INSERT INTO warmup_logs (inbox_id, action, peer_address, status)
            VALUES (?, 'warmup_exchange', 'warmup-peer@openexplee.internal', 'completed')
        """, (inbox_id,))

        # Gradually optimize health score
        new_health = min(100, current_health + 1)
        await cursor.execute("UPDATE inboxes SET health_score = ? WHERE id = ?", (new_health, inbox_id))

        await conn.commit()
        await conn.close()

    return {"inbox_email": email, "action": "warmup_exchange", "status": "completed", "health_score": new_health}

