import asyncio
import random
import ssl
from email.message import EmailMessage
from email.utils import make_msgid, formatdate
from datetime import datetime
import aiosmtplib
import httpx
from typing import Dict, Any, Optional
from app.database import get_db_connection
from app.engine.copywriter import resolve_spintax
from app.config import settings


async def get_next_available_inbox(campaign_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Retrieves an active inbox under its daily quota. A campaign can pin its own sender inbox, Reply-To and CC."""
    conn = await get_db_connection()
    try:
        camp = None
        if campaign_id is not None:
            cur = await conn.execute(
                "SELECT inbox_id, reply_to_email, cc_email FROM campaigns WHERE id = ?", (campaign_id,))
            camp = await cur.fetchone()
        pinned = camp["inbox_id"] if camp else None
        cur = await conn.execute("""
            SELECT * FROM inboxes
            WHERE status = 'active' AND sent_today < daily_quota AND (? IS NULL OR id = ?)
            ORDER BY last_sent_at ASC NULLS FIRST, id ASC
            LIMIT 1
        """, (pinned, pinned))
        row = await cur.fetchone()
        if row:
            inbox = dict(row)
            if camp and camp["reply_to_email"]:
                inbox["reply_to"] = camp["reply_to_email"]
            if camp and camp["cc_email"]:
                inbox["cc_email"] = camp["cc_email"]
            return inbox
        return None
    finally:
        await conn.close()


def compliance_footer() -> str:
    """Opt-out line plus postal address, added under every cold email and follow-up."""
    lines = [settings.opt_out_line.strip(), settings.postal_address.strip()]
    return "\n\n".join(line for line in lines if line)


async def _record_send_success(
    inbox_id: int, inbox_email: str, lead_id: Optional[int], is_reply: bool = False,
    subject: str = "", body: str = "", message_id: Optional[str] = None,
) -> None:
    """Updates inbox daily quota, lead status, and campaign counters after delivery."""
    conn = await get_db_connection()
    try:
        await conn.execute("""
            UPDATE inboxes
            SET sent_today = sent_today + 1, last_sent_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (inbox_id,))

        if lead_id:
            await conn.execute(
                "INSERT OR IGNORE INTO messages (lead_id, direction, message_id, subject, body) "
                "VALUES (?, 'outbound', ?, ?, ?)",
                (lead_id, message_id, subject, body),
            )

        # A reply in a live conversation must not put the lead back into the cold sequence.
        if lead_id and not is_reply:
            await conn.execute("""
                UPDATE leads 
                SET status = 'sent',
                    sent_at = CURRENT_TIMESTAMP,
                    sent_from_inbox = ?,
                    followup_step = COALESCE(followup_step, 0) + 1,
                    next_followup_at = CASE
                        WHEN COALESCE(followup_step, 0) = 0 THEN datetime('now', '+3 days')
                        WHEN COALESCE(followup_step, 0) = 1 THEN datetime('now', '+4 days')
                        ELSE NULL
                    END
                WHERE id = ?
            """, (inbox_email, lead_id))

            await conn.execute("""
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
    inbox: Dict[str, Any], to_email: str, subject: str, body: str,
    in_reply_to: Optional[str] = None,
) -> str:
    """Dispatches email over standard SMTP (Google Workspace, M365, Hyvor Relay, Custom)."""
    msg = EmailMessage()
    from_header = f"{inbox['display_name']} <{inbox['email']}>" if inbox.get("display_name") else inbox["email"]
    msg["From"] = from_header
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=inbox["email"].split("@")[-1])
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to

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

    # Configure TLS context to accept self-signed certificates on private/VPS relays
    tls_context = ssl.create_default_context()
    tls_context.check_hostname = False
    tls_context.verify_mode = ssl.CERT_NONE

    smtp_client = aiosmtplib.SMTP(
        hostname=inbox["smtp_host"],
        port=port,
        use_tls=use_tls,
        start_tls=start_tls,
        tls_context=tls_context,
        timeout=15.0
    )
    await smtp_client.connect()
    if inbox.get("smtp_user") and inbox["smtp_user"] != "none" and inbox.get("smtp_password") and inbox["smtp_password"] != "none":
        await smtp_client.login(inbox["smtp_user"], inbox["smtp_password"])
    await smtp_client.send_message(msg)
    await smtp_client.quit()
    return msg["Message-ID"]


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
    lead_id: Optional[int] = None,
    is_reply: bool = False,
    in_reply_to: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sends an outbound email via SMTP, Cloudflare Email Worker, or Mailflare API.
    Enforces suppression checks to keep domain bounce rates near 0%.
    Cold emails and follow-ups get the compliance footer; replies do not.
    """
    if await _is_suppressed(to_email):
        return {"success": False, "inbox_email": inbox["email"], "error": f"Suppressed recipient/domain: {to_email}"}

    local_part = to_email.strip().lower().split("@")[0]
    if any(bad in local_part for bad in ("notpresent", "notfound", "unknown.", "john.doe", "jane.doe", "noreply", "no-reply")):
        await _auto_suppress_bounce(to_email, lead_id, "blocked_placeholder_email")
        return {"success": False, "inbox_email": inbox["email"], "error": f"Blocked placeholder address: {to_email}"}

    clean_subject = resolve_spintax(subject)
    clean_body = resolve_spintax(body)
    footer = "" if is_reply else compliance_footer()
    if footer and footer not in clean_body:
        clean_body = f"{clean_body.rstrip()}\n\n{footer}"
    result = {"success": False, "inbox_email": inbox["email"], "error": None}

    try:
        provider = inbox.get("provider", "custom")
        message_id = None
        if provider == "cf_worker":
            await _send_via_cf_worker(inbox, to_email, clean_subject, clean_body)
        elif provider == "mailflare":
            await _send_via_mailflare(inbox, to_email, clean_subject, clean_body)
        else:
            message_id = await _send_via_smtp(inbox, to_email, clean_subject, clean_body, in_reply_to)

        result["success"] = True
        await _record_send_success(
            inbox["id"], inbox["email"], lead_id, is_reply=is_reply,
            subject=clean_subject, body=clean_body, message_id=message_id,
        )

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
    try:
        cur = await conn.execute("""
            SELECT * FROM leads 
            WHERE campaign_id = ?
              AND status IN ('enriched', 'queued', 'discovered')
              AND verification_status IN ('verified', 'deliverable', 'catch_all')
              AND email IS NOT NULL AND email != ''
            LIMIT ?
        """, (campaign_id, limit))
        leads = [dict(r) for r in await cur.fetchall()]
    finally:
        await conn.close()

    dispatched = 0
    errors = []

    if not leads:
        conn = await get_db_connection()
        try:
            cur_all = await conn.execute("SELECT COUNT(*) AS c FROM leads WHERE campaign_id = ?", (campaign_id,))
            row_all = await cur_all.fetchone()
            total_in_camp = row_all["c"] if row_all else 0

            cur_sent = await conn.execute("SELECT COUNT(*) AS c FROM leads WHERE campaign_id = ? AND status = 'sent'", (campaign_id,))
            row_sent = await cur_sent.fetchone()
            sent_leads = row_sent["c"] if row_sent else 0
        finally:
            await conn.close()

        if total_in_camp > 0 and sent_leads >= total_in_camp:
            return {
                "dispatched": 0,
                "errors": [f"All {total_in_camp} leads in this campaign have already been dispatched! Click 'Find Leads' to discover more."]
            }
        return {
            "dispatched": 0,
            "errors": ["No queued or verified leads in this campaign yet. Click 'Find Leads' first to discover prospects."]
        }

    for lead in leads:
        inbox = await get_next_available_inbox(campaign_id)
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
            # Wait the configured gap between sends. It was capped at 5 seconds, which sent a
            # whole batch in about a minute and tripped the host's outbound mail limit.
            # ponytail: a caller that awaits this inline (the MCP tool) blocks for the whole
            # batch. Upgrade: run that path as a background task like the launch endpoint.
            await asyncio.sleep(random.randint(settings.min_delay_seconds, settings.max_delay_seconds))
        else:
            errors.append(f"Failed to send to {lead['email']}: {send_res.get('error')}")

    return {"dispatched": dispatched, "errors": errors}

