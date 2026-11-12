import asyncio
from app.database import get_db_connection
from app.engine.sequencer import get_next_available_inbox, send_single_email


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
            SELECT * FROM leads
            WHERE status = 'sent'
              AND is_hot_lead = 0
              AND followup_step IN (1, 2)
              AND next_followup_at IS NOT NULL
              AND next_followup_at <= CURRENT_TIMESTAMP
              AND verification_status != 'invalid'
              AND email IS NOT NULL AND email != ''
            LIMIT ?
        """, (limit,))
        leads = [dict(r) for r in await cur.fetchall()]
    finally:
        await conn.close()

    sent_count = 0
    for lead in leads:
        inbox = await get_next_available_inbox()
        if not inbox:
            break

        orig_subj = lead.get("personalized_subject") or f"quick question for {lead.get('company_name', 'you')}"
        subj = orig_subj if orig_subj.lower().startswith("re:") else f"Re: {orig_subj}"
        first_name = (lead.get("contact_name") or "there").split()[0]

        if lead.get("followup_step") == 1:
            body = (
                f"{{Hi|Hey}} {first_name},\n\n"
                f"Just bumping this to the top of your inbox in case it got buried.\n\n"
                f"Open to a quick 5-minute chat this week?"
            )
        else:
            body = (
                f"Hi {first_name},\n\n"
                f"Haven't heard back, so I'll assume now isn't the right time and won't follow up again.\n\n"
                f"Feel free to reply anytime if this becomes a priority."
            )

        res = await send_single_email(inbox, lead["email"], subj, body, lead_id=lead["id"])
        if res["success"]:
            sent_count += 1

    return sent_count


async def run_scheduler_loop(interval_seconds: int = 300) -> None:
    """Background loop for daily quota resets and automated follow-up sequences."""
    while True:
        try:
            await reset_daily_inbox_quotas()
            await process_due_followups(limit=5)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"[Scheduler] Background tick error: {exc}")
        await asyncio.sleep(interval_seconds)
