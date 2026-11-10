import asyncio
import re
import dns.resolver
from email_validator import validate_email, EmailNotValidError
from typing import Dict, Any, List, Optional
import aiosmtplib

DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "tempmail.com", "10minutemail.com",
    "sharklasers.com", "getairmail.com", "dispostable.com", "yopmail.com"
}


def generate_email_permutations(first_name: str, last_name: str, domain: str) -> List[str]:
    """Generates standard B2B email permutations from name and domain."""
    f = re.sub(r"[^a-zA-Z]", "", first_name.lower().strip())
    l = re.sub(r"[^a-zA-Z]", "", last_name.lower().strip())
    d = domain.lower().replace("http://", "").replace("https://", "").replace("www.", "").strip("/ ")

    if not f and not l:
        return [f"contact@{d}", f"hello@{d}", f"info@{d}"]
    if not l:
        return [f"{f}@{d}", f"contact@{d}"]
    if not f:
        return [f"{l}@{d}", f"contact@{d}"]

    return [
        f"{f}.{l}@{d}",
        f"{f}@{d}",
        f"{f[0]}{l}@{d}",
        f"{f}{l}@{d}",
        f"{f[0]}.{l}@{d}",
        f"{l}@{d}",
    ]


async def get_mx_records(domain: str) -> List[str]:
    """Performs async DNS MX record lookup, sorted by preference."""
    loop = asyncio.get_running_loop()
    try:
        def _resolve():
            answers = dns.resolver.resolve(domain, "MX")
            records = sorted(answers, key=lambda r: r.preference)
            return [str(r.exchange).rstrip(".") for r in records]

        return await loop.run_in_executor(None, _resolve)
    except Exception:
        return []


async def verify_email_smtp(email: str, timeout: float = 6.0) -> Dict[str, Any]:
    """
    Validates email address through 4 tiers:
    1. Syntax (RFC standard)
    2. Disposable domain check
    3. DNS MX record check
    4. Non-intrusive SMTP Handshake (EHLO -> MAIL FROM -> RCPT TO -> QUIT)
    """
    result = {
        "email": email,
        "status": "unverified",  # 'verified', 'deliverable', 'catch_all', 'invalid', 'risky'
        "score": 0,
        "mx_host": None,
        "is_disposable": False,
        "details": ""
    }

    # Tier 1: Syntax Validation
    try:
        valid = validate_email(email, check_deliverability=False)
        email = valid.normalized
        domain = email.split("@")[1].lower()
    except EmailNotValidError as e:
        result["status"] = "invalid"
        result["details"] = f"Invalid email format: {str(e)}"
        return result

    # Tier 2: Disposable Check
    if domain in DISPOSABLE_DOMAINS:
        result["status"] = "invalid"
        result["is_disposable"] = True
        result["details"] = "Disposable burner email provider"
        return result

    # Tier 3: DNS MX Check
    mx_hosts = await get_mx_records(domain)
    if not mx_hosts:
        result["status"] = "invalid"
        result["details"] = f"Domain {domain} has no valid MX mail servers."
        return result

    primary_mx = mx_hosts[0]
    result["mx_host"] = primary_mx

    # Tier 4: Direct SMTP Handshake check
    try:
        smtp_client = aiosmtplib.SMTP(
            hostname=primary_mx,
            port=25,
            timeout=timeout,
            use_tls=False
        )
        await smtp_client.connect()
        await smtp_client.ehlo("verify.openexplee.local")
        
        # Test Sender
        code, _ = await smtp_client.mail("check@verify.openexplee.local")
        if code >= 400:
            result["status"] = "deliverable"
            result["details"] = f"MX server active ({primary_mx}), handshake limited by greylisting."
            result["score"] = 80
            await smtp_client.quit()
            return result

        # Test Target Recipient
        rcpt_code, rcpt_msg = await smtp_client.rcpt(email)
        
        if rcpt_code == 250:
            # Test Catch-All by checking a definitely random address
            random_test = f"check_catchall_987654@{domain}"
            c_code, _ = await smtp_client.rcpt(random_test)
            if c_code == 250:
                result["status"] = "catch_all"
                result["details"] = "Domain accepts all mailboxes (Catch-all)."
                result["score"] = 70
            else:
                result["status"] = "verified"
                result["details"] = "Mailbox exists and is confirmed deliverable."
                result["score"] = 99
        elif rcpt_code in [550, 551, 552, 553, 554]:
            result["status"] = "invalid"
            result["details"] = f"Mailbox rejected by server: {rcpt_msg}"
            result["score"] = 0
        else:
            result["status"] = "deliverable"
            result["details"] = f"MX answered with code {rcpt_code}"
            result["score"] = 85

        try:
            await smtp_client.quit()
        except Exception:
            pass

    except Exception as e:
        # If port 25 is blocked by local ISP or timeout occurs, MX is still validated
        result["status"] = "deliverable"
        result["score"] = 85
        result["details"] = f"MX verified ({primary_mx}). Direct SMTP check skipped ({type(e).__name__})."

    return result

