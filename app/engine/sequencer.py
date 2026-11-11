import asyncio
import random
from email.message import EmailMessage
from email.utils import make_msgid, formatdate
from datetime import datetime
import aiosmtplib
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


async def send_single_email(
    inbox: Dict[str, Any],
    to_email: str,
    subject: str,
    body: str,
    lead_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Sends an outbound email via the selected inbox account with deliverability headers.
    """
    msg = EmailMessage()
    clean_subject = resolve_spintax(subject)
    clean_body = resolve_spintax(body)

    from_header = f"{inbox['display_name']} <{inbox['email']}>" if inbox.get("display_name") else inbox["email"]
    msg["From"] = from_header
    msg["To"] = to_email
    msg["Subject"] = clean_subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=inbox["email"].split("@")[-1])

    # CC & Reply-To Routing (e.g. your primary vikash@viktron.ai email)
    if inbox.get("reply_to"):
        msg["Reply-To"] = inbox["reply_to"]
    if inbox.get("cc_email"):
        msg["Cc"] = inbox["cc_email"]
    
    # Unsubscribe header for cold outreach compliance
    unsubscribe_mail = f"mailto:unsubscribe@{inbox['email'].split('@')[-1]}?subject=Unsubscribe"
    msg["List-Unsubscribe"] = f"<{unsubscribe_mail}>"
    msg.set_content(clean_body)

    result = {"success": False, "inbox_email": inbox["email"], "error": None}

    try:
        # Determine port & TLS configuration
        port = inbox.get("smtp_port", 587)
        use_tls = (port == 465)
        start_tls = (port == 587 or inbox.get("use_tls", 1))

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

        result["success"] = True

        # Update database counters
        conn = await get_db_connection()
        async with conn.cursor() as cursor:
            await cursor.execute("""
                UPDATE inboxes 
                SET sent_today = sent_today + 1, last_sent_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (inbox["id"],))

            if lead_id:
                await cursor.execute("""
                    UPDATE leads 
                    SET status = 'sent', sent_at = CURRENT_TIMESTAMP, sent_from_inbox = ? 
                    WHERE id = ?
                """, (inbox["email"], lead_id))

                # Increment campaign count
                await cursor.execute("""
                    UPDATE campaigns 
                    SET sent_count = sent_count + 1 
                    WHERE id = (SELECT campaign_id FROM leads WHERE id = ?)
                """, (lead_id,))

            await conn.commit()
            await conn.close()

    except Exception as e:
        result["error"] = str(e)
        print(f"[Sequencer] Send failed via {inbox['email']}: {e}")

    return result


async def dispatch_campaign_batch(campaign_id: int, limit: int = 10) -> Dict[str, Any]:
    """
    Sends queued and verified leads for a given campaign using rotating inboxes.
    """
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("""
            SELECT * FROM leads 
            WHERE campaign_id = ? AND status IN ('enriched', 'queued') AND email IS NOT NULL AND email != ''
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

