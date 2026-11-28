import asyncio
import traceback
from app.config import settings
from app.database import get_db_connection
from app.engine.sequencer import dispatch_campaign_batch, get_next_available_inbox, send_single_email
from app.engine.replies import booking_link, poll_replies, sync_relay_bounces


async def reset_daily_inbox_quotas() -> int:
    """Resets sent_today to 0 for inboxes whose last send was prior to today (UTC)."""
    conn = await get_db_connection()
    try:
        cur = await conn.execute("""
            UPDATE inboxes
            SET sent_today = 0
            WHERE sent_today > 0
              AND (last_sent_at IS NULL OR DATE(last_sent_at) < DATE('now'))
        """)
        await conn.commit()
        return cur.rowcount
    finally:
        await conn.close()


async def process_due_followups(limit: int = 10) -> int:
    """Sends scheduled Day-3 / Day-7 follow-ups for leads that have not replied yet."""
    conn = await get_db_connection()
    try:
        cur = await conn.execute("""
            SELECT l.*, c.booking_url FROM leads l
            LEFT JOIN campaigns c ON c.id = l.campaign_id
            LEFT JOIN inboxes i ON i.id = c.inbox_id
            WHERE l.status = 'sent'
              -- A campaign pinned to an inbox that is full today waits. Without this, its leads
              -- fill the batch on every tick and no other campaign sends.
              AND (c.inbox_id IS NULL OR (i.status = 'active' AND i.sent_today < i.daily_quota))
              AND l.is_hot_lead = 0
              AND l.followup_step IN (1, 2)
              AND l.next_followup_at IS NOT NULL
              AND l.next_followup_at <= CURRENT_TIMESTAMP
              AND l.verification_status IN ('verified', 'catch_all')
              AND l.email IS NOT NULL AND l.email != ''
            LIMIT ?
        """, (limit,))
        leads = [dict(r) for r in await cur.fetchall()]
    finally:
        await conn.close()

    sent_count = 0
    for lead in leads:
        inbox = await get_next_available_inbox(lead.get("campaign_id"))
        if not inbox:
            continue  # this lead's inbox filled up during the batch; the next lead can use another

        orig_subj = lead.get("personalized_subject") or f"quick question for {lead.get('company_name', 'you')}"
        subj = orig_subj if orig_subj.lower().startswith("re:") else f"Re: {orig_subj}"
        first_name = (lead.get("contact_name") or "there").split()[0]

        if lead.get("followup_step") == 1:
            link = booking_link(lead)
            cal_line = f"\n\nHappy to jump on a quick 15-min call — pick any slot here: {link}" if link else "\n\nOpen to a quick 5-minute chat this week?"
            body = (
                f"{{Hi|Hey}} {first_name},\n\n"
                f"Just bumping this to the top of your inbox in case it got buried.{cal_line}"
            )
        else:
            body = (
                f"Hi {first_name},\n\n"
                f"Haven't heard back, so I'll assume now isn't the right time and won't follow up again.\n\n"
                f"Feel free to reply anytime if this becomes a priority."
            )

        # A "Re:" subject with no thread headers reads as a faked reply. Point at the first email.
        conn = await get_db_connection()
        try:
            cur = await conn.execute(
                "SELECT message_id FROM messages WHERE lead_id = ? AND direction = 'outbound' "
                "AND message_id IS NOT NULL ORDER BY id LIMIT 1", (lead["id"],))
            first = await cur.fetchone()
        finally:
            await conn.close()
        res = await send_single_email(
            inbox, lead["email"], subj, body, lead_id=lead["id"],
            in_reply_to=first["message_id"] if first else None,
        )
        if res["success"]:
            sent_count += 1

    return sent_count


async def send_first_emails() -> int:
    """Sends one first email for each ready campaign that has a verified lead with no email yet."""
    # Hold first emails until the app can read opt-outs and the footer has a postal address.
    if not (settings.postal_address.strip() and settings.imap_host and settings.imap_user and settings.imap_password):
        return 0
    conn = await get_db_connection()
    try:
        # ponytail: a campaign stays manual if its name contains "(drafts)".
        # Upgrade: an auto_send column on campaigns, set from the dashboard.
        cur = await conn.execute(
            "SELECT id FROM campaigns WHERE status = 'ready' AND name NOT LIKE '%(drafts)%'"
        )
        campaign_ids = [r["id"] for r in await cur.fetchall()]
    finally:
        await conn.close()

    sent = 0
    for campaign_id in campaign_ids:
        # limit=1: the batch function waits min_delay_seconds or more after each send,
        # so one lead for each campaign keeps the gap between emails.
        sent += (await dispatch_campaign_batch(campaign_id, limit=1))["dispatched"]
    return sent


async def run_scheduler_loop(interval_seconds: int = 300) -> None:
    """Background loop for daily quota resets and automated follow-up sequences."""
    while True:
        try:
            await reset_daily_inbox_quotas()
            # Bounces first, and before the reply check, which can end the tick early.
            # A follow-up to a refused address costs sender reputation.
            try:
                await sync_relay_bounces()
            except Exception as exc:
                print(f"[Scheduler] Relay bounce check failed: {exc}")
            # Replies next: someone who answered or opted out must not get a follow-up.
            try:
                await poll_replies()
            except Exception as exc:
                # ponytail: if the mailbox can't be read we can't know who replied, so
                # hold follow-ups this tick. Upgrade: alert the owner after N failed ticks.
                print(f"[Scheduler] Reply check failed, follow-ups held this tick: {exc}")
                await asyncio.sleep(interval_seconds)
                continue
            await process_due_followups(limit=5)
            await send_first_emails()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"[Scheduler] Background tick error: {exc!r}")
            traceback.print_exc()
        await asyncio.sleep(interval_seconds)

