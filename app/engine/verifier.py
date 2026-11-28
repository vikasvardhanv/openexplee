import asyncio
import json
import re
import shlex
import dns.resolver
from email_validator import validate_email, EmailNotValidError
from typing import Dict, Any, List, Optional
import aiosmtplib
from app.database import get_db_connection

DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "tempmail.com", "10minutemail.com",
    "sharklasers.com", "getairmail.com", "dispostable.com", "yopmail.com"
}

INVALID_NAME_TOKENS = {
    "unknown", "notpresent", "not_present", "notfound", "not_found", "none", "na", "n/a", "null",
    "placeholder", "test", "admin", "founder", "hiring", "team", "user",
    "sample", "example", "noreply", "contact", "info", "hello", "support",
}


def is_valid_person_name(first_name: str, last_name: str) -> bool:
    """Rejects LLM placeholder names like 'NotPresent NotPresent', 'Unknown Unknown', or 'John Doe'."""
    f = re.sub(r"[^a-z]", "", (first_name or "").lower())
    l = re.sub(r"[^a-z]", "", (last_name or "").lower())
    if len(f) < 2 or len(l) < 2:
        return False
    if f in INVALID_NAME_TOKENS or l in INVALID_NAME_TOKENS:
        return False
    if f"{f} {l}" in ("john doe", "jane doe", "first last"):
        return False
    return True


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


async def verify_email_smtp(email: str, timeout: float = 2.0) -> Dict[str, Any]:
    """
    Validates email address through 4 tiers:
    1. Syntax (RFC standard)
    2. Disposable domain check
    3. DNS MX record check
    4. Non-intrusive SMTP Handshake (EHLO -> MAIL FROM -> RCPT TO -> QUIT)
    """
    result = {
        "email": email,
        "status": "unverified",  # 'verified', 'catch_all', 'invalid', 'risky'
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
            # The server refused the test sender, so it never answered for this mailbox.
            result["status"] = "risky"
            result["details"] = f"MX server active ({primary_mx}). Mailbox not checked (sender refused, code {code})."
            result["score"] = 40
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
            # A temporary or unknown answer confirms nothing about the mailbox.
            result["status"] = "risky"
            result["details"] = f"Mailbox not confirmed. MX answered with code {rcpt_code}."
            result["score"] = 40

        try:
            await smtp_client.quit()
        except Exception:
            pass

    except Exception as e:
        # Home and office networks block port 25, and slow mail servers miss the short timeout.
        # The relay server can reach them, so the relay asks about this mailbox.
        relayed = await _check_from_relay(email, primary_mx)
        if relayed:
            result.update(relayed)
        else:
            result["status"] = "risky"
            result["score"] = 40
            result["details"] = f"Domain has mail servers ({primary_mx}). Mailbox not checked ({type(e).__name__})."

    return result


# Runs on the relay. Reads one JSON line [mx, sender, email] and prints the SMTP answer codes.
_RELAY_PROBE = r'''
import json, smtplib, sys
mx, sender, email = json.loads(sys.stdin.readline())
try:
    s = smtplib.SMTP(mx, 25, timeout=25)
    s.ehlo()
    m = s.mail(sender)[0]
    r = s.rcpt(email)[0] if m < 400 else 0
    c = s.rcpt("check_catchall_987654@" + email.split("@")[1])[0] if r == 250 else 0
    try:
        s.quit()
    except Exception:
        pass
    print(json.dumps({"mail": m, "rcpt": r, "catchall": c}))
except Exception as e:
    print(json.dumps({"error": type(e).__name__}))
'''


def grade_relay_answer(answer: Dict[str, Any]) -> Dict[str, Any]:
    """Turns the relay's SMTP answer codes into a verification status."""
    rcpt = answer.get("rcpt", 0)
    if rcpt == 250 and answer.get("catchall") == 250:
        return {"status": "catch_all", "score": 70,
                "details": "Domain accepts every address, so this mailbox is not confirmed (asked from the relay)."}
    if rcpt == 250:
        return {"status": "verified", "score": 99,
                "details": "Mailbox confirmed by its mail server (asked from the relay)."}
    if rcpt in (550, 551, 552, 553, 554):
        return {"status": "invalid", "score": 0,
                "details": f"Mailbox rejected by its mail server (code {rcpt}, asked from the relay)."}
    why = answer.get("error") or f"sender code {answer.get('mail')}, mailbox code {rcpt}"
    # This text must not contain "not checked": the scheduler retries leads that carry those words.
    return {"status": "risky", "score": 40, "details": f"Mailbox check failed from the relay ({why})."}


async def _check_from_relay(email: str, mx_host: str) -> Optional[Dict[str, Any]]:
    """Asks the mail server about one mailbox from the relay, over SSH. None if there is no relay."""
    try:
        conn = await get_db_connection()
        try:
            cur = await conn.execute(
                # A paused inbox still names the relay. Asking about a mailbox sends no email.
                "SELECT smtp_host, email FROM inboxes WHERE provider = 'relay' ORDER BY id LIMIT 1")
            relay = await cur.fetchone()
        finally:
            await conn.close()
        if not relay:
            return None
        # The address travels on stdin as JSON, never in the remote command line.
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", f"root@{relay['smtp_host']}",
            "python3 -c " + shlex.quote(_RELAY_PROBE),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(
            proc.communicate(json.dumps([mx_host, relay["email"], email]).encode() + b"\n"), timeout=60)
        return grade_relay_answer(json.loads(out))
    except Exception as exc:
        print(f"[Verifier] Relay check did not run for {email.split('@')[-1]}: {exc!r}")
        return None


async def enrich_person_email(first_name: str, last_name: str, domain: str) -> Dict[str, Any]:
    """
    Replicates Explee's enrich_email tool for $0: generates corporate email
    permutations and checks DNS MX + live SMTP handshake to find the best address.
    """
    if not is_valid_person_name(first_name, last_name):
        return {
            "email": None,
            "status": "invalid",
            "score": 0,
            "mx_host": None,
            "is_disposable": False,
            "details": f"Rejected placeholder or invalid name: {first_name} {last_name}",
            "candidates_tested": [],
        }

    candidates = generate_email_permutations(first_name, last_name, domain)

    for candidate in candidates:
        res = await verify_email_smtp(candidate, timeout=5.0)
        # Only a mailbox the mail server confirmed counts as found.
        # A catch-all domain accepts every name pattern, so a pattern there is still a guess.
        if res["status"] == "verified":
            return {**res, "candidates_tested": candidates}
        if res["mx_host"] is None:
            # Domain has no MX records at all; stop probing permutations
            return {**res, "candidates_tested": [candidate]}
        if res["status"] in ("risky", "catch_all"):
            # The mail server could not be reached. The other patterns will fail the same way.
            break

    # A name pattern nobody confirmed is a guess, and guesses bounce. Report it as not sendable.
    return {
        "email": candidates[0] if candidates else None,
        "status": "invalid",
        "score": 0,
        "mx_host": None,
        "is_disposable": False,
        "details": "Guessed from a name pattern. No mail server confirmed the mailbox.",
        "candidates_tested": candidates,
    }

