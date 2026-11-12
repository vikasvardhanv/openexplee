import asyncio
import random
from email.message import EmailMessage
from email.utils import make_msgid, formatdate
from datetime import datetime
import aiosmtplib
import httpx
from typing import Dict, Any, Optional
from app.database import get_db_connection
from app.engine.copywriter import resolve_spintax
from app.config import settings


async def get_next_available_inbox() -> Optional[Dict[str, Any]]:
    """Retrieves an active inbox that has not exceeded its daily send quota."""
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("""
            SELECT * FROM inboxes 
            WHERE status = 'active' AND sent_today < daily_quota 
            ORDER BY last_sent_at ASC NULLS FIRST, id ASC 
            LIMIT 1
        """)
        row = await cursor.fetchone()
        await conn.close()
        if row:
            return dict(row)
    return None


async def _record_send_success(inbox_id: int, inbox_email: str, lead_id: Optional[int]) -> None:
    """Updates inbox daily quota, lead status, and campaign counters after delivery."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                UPDATE inboxes 
                SET sent_today = sent_today + 1, last_sent_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (inbox_id,))

            if lead_id:
                await cursor.execute("""
                    UPDATE leads 
                    SET status = 'sent', sent_at = CURRENT_TIMESTAMP, sent_from_inbox = ? 
                    WHERE id = ?
                """, (inbox_email, lead_id))

                await cursor.execute("""
                    UPDATE campaigns 
                    SET sent_count = sent_count + 1 
                    WHERE id = (SELECT campaign_id FROM leads WHERE id = ?)
                """, (lead_id,))

            await conn.commit()
    finally:
        await conn.close()


async def _send_via_cf_worker(
    inbox: Dict[str, Any], to_email: str, subject: str, body: str
) -> None:
    """Dispatches email via the OpenExplee Cloudflare Email Worker (/send)."""
    base_url = inbox["smtp_host"].rstrip("/")
    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"

    payload: Dict[str, Any] = {
        "from": inbox["email"],
        "to": to_email,
        "subject": subject,
        "text": body,
    }
    if inbox.get("display_name"):
        payload["fromName"] = inbox["display_name"]
    if inbox.get("cc_email"):
        payload["cc"] = inbox["cc_email"]
    if inbox.get("reply_to"):
        payload["replyTo"] = inbox["reply_to"]

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{base_url}/send",
            headers={"Authorization": f"Bearer {inbox['smtp_password']}"},
            json=payload,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"CF Worker HTTP {resp.status_code}: {resp.text}")


async def _send_via_mailflare(
    inbox: Dict[str, Any], to_email: str, subject: str, body: str
) -> None:
    """Dispatches email via Mailflare's authenticated REST API (/api/v1/send)."""
    base_url = inbox["smtp_host"].rstrip("/")
    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"

    from_addr = f"{inbox['display_name']} <{inbox['email']}>" if inbox.get("display_name") else inbox["email"]
    payload: Dict[str, Any] = {
        "from": from_addr,
        "to": [to_email],
        "subject": subject,
        "text": body,
        "mailboxId": inbox["smtp_user"],
    }
    if inbox.get("cc_email"):
        payload["cc"] = inbox["cc_email"]

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{base_url}/api/v1/send",
            headers={"Authorization": f"Bearer {inbox['smtp_password']}"},
            json=payload,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Mailflare HTTP {resp.status_code}: {resp.text}")


async def _send_via_smtp(
    inbox: Dict[str, Any], to_email: str, subject: str, body: str
) -> None:
    """Dispatches email over standard SMTP (Google Workspace, M365, Hyvor Relay, Custom)."""
    msg = EmailMessage()
    from_header = f"{inbox['display_name']} <{inbox['email']}>" if inbox.get("display_name") else inbox["email"]
    msg["From"] = from_header
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=inbox["email"].split("@")[-1])

    if inbox.get("reply_to"):
        msg["Reply-To"] = inbox["reply_to"]
    if inbox.get("cc_email"):
        msg["Cc"] = inbox["cc_email"]

    unsubscribe_mail = f"mailto:unsubscribe@{inbox['email'].split('@')[-1]}?subject=Unsubscribe"
    msg["List-Unsubscribe"] = f"<{unsubscribe_mail}>"
    msg.set_content(body)

    port = inbox.get("smtp_port", 587)
    use_tls = (port == 465)
    start_tls = (port == 587 or bool(inbox.get("use_tls", 1)))

    smtp_client = aiosmtplib.SMTP(
        hostname=inbox["smtp_host"],
        port=port,
        use_tls=use_tls,
        start_tls=start_tls,
        timeout=15.0
    )
    await smtp_client.connect()
    await smtp_client.login(inbox["smtp_user"], inbox["smtp_password"])
    await smtp_client.send_message(msg)
    await smtp_client.quit()


async def _is_suppressed(email: str) -> bool:
    """Checks if an email or its domain is on the suppression/dedup list."""
    clean_email = email.strip().lower()
    domain = clean_email.split("@")[-1] if "@" in clean_email else ""
    conn = await get_db_connection()
    try:
        cur = await conn.execute(
            "SELECT 1 FROM suppression_list WHERE LOWER(value) IN (?, ?) LIMIT 1",
            (clean_email, domain),
        )
        return (await cur.fetchone()) is not None
    finally:
        await conn.close()


async def _auto_suppress_bounce(email: str, lead_id: Optional[int], reason: str) -> None:
    """Adds hard-bounced addresses to the suppression list so future sends never hit them."""
    clean_email = email.strip().lower()
    conn = await get_db_connection()
    try:
        await conn.execute(
            "INSERT OR IGNORE INTO suppression_list (entry_type, value, reason) VALUES ('email', ?, ?)",
            (clean_email, reason[:200]),
        )
        if lead_id:
            await conn.execute("UPDATE leads SET status = 'bounced' WHERE id = ?", (lead_id,))
        await conn.commit()
    finally:
        await conn.close()


async def send_single_email(
    inbox: Dict[str, Any],
    to_email: str,
    subject: str,
    body: str,
    lead_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Sends an outbound email via SMTP, Cloudflare Email Worker, or Mailflare API.
    Enforces suppression checks to keep domain bounce rates near 0%.
    """
    if await _is_suppressed(to_email):
        return {"success": False, "inbox_email": inbox["email"], "error": f"Suppressed recipient/domain: {to_email}"}

    clean_subject = resolve_spintax(subject)
    clean_body = resolve_spintax(body)
    result = {"success": False, "inbox_email": inbox["email"], "error": None}

    try:
        provider = inbox.get("provider", "custom")
        if provider == "cf_worker":
            await _send_via_cf_worker(inbox, to_email, clean_subject, clean_body)
        elif provider == "mailflare":
            await _send_via_mailflare(inbox, to_email, clean_subject, clean_body)
        else:
            await _send_via_smtp(inbox, to_email, clean_subject, clean_body)

        result["success"] = True
        await _record_send_success(inbox["id"], inbox["email"], lead_id)

    except Exception as e:
        err_str = str(e)
        result["error"] = err_str
        if any(code in err_str.lower() for code in ("550", "551", "553", "554", "user unknown", "no such user")):
            await _auto_suppress_bounce(to_email, lead_id, f"hard_bounce: {err_str}")
        print(f"[Sequencer] Send failed via {inbox['email']} ({inbox.get('provider')}): {e}")

    return result


async def dispatch_campaign_batch(campaign_id: int, limit: int = 10) -> Dict[str, Any]:
    """
    Sends queued and verified leads for a given campaign using rotating inboxes.
    Blocks invalid emails automatically to protect sender domain reputation.
    """
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("""
            SELECT * FROM leads 
            WHERE campaign_id = ?
              AND status IN ('enriched', 'queued')
              AND verification_status != 'invalid'
              AND email IS NOT NULL AND email != ''
            LIMIT ?
        """, (campaign_id, limit))
        leads = [dict(r) for r in await cursor.fetchall()]
        await conn.close()

    dispatched = 0
    errors = []

    for lead in leads:
        inbox = await get_next_available_inbox()
        if not inbox:
            errors.append("No active inboxes available with remaining daily quota.")
            break

        subj = lead.get("personalized_subject") or f"quick question for {lead.get('company_name', 'you')}"
        body = lead.get("personalized_body") or f"Hi,\n\nSaw what you are building and wanted to connect."

        send_res = await send_single_email(
            inbox=inbox,
            to_email=lead["email"],
            subject=subj,
            body=body,
            lead_id=lead["id"]
        )

        if send_res["success"]:
            dispatched += 1
            # Random jitter between sends to mimic human behavior
            delay = random.randint(settings.min_delay_seconds, settings.max_delay_seconds)
            await asyncio.sleep(min(delay, 5))  # Sleep up to 5s in batch loop for responsiveness
        else:
            errors.append(f"Failed to send to {lead['email']}: {send_res.get('error')}")

    return {"dispatched": dispatched, "errors": errors}

