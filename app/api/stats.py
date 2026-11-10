from fastapi import APIRouter
from app.database import get_db_connection

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("")
async def get_dashboard_stats():
    conn = await get_db_connection()

    # Total campaigns
    cur = await conn.execute("SELECT COUNT(*) FROM campaigns")
    total_campaigns = (await cur.fetchone())[0]

    # Total leads
    cur = await conn.execute("SELECT COUNT(*) FROM leads")
    total_leads = (await cur.fetchone())[0]

    # Verified leads
    cur = await conn.execute("SELECT COUNT(*) FROM leads WHERE verification_status IN ('verified', 'deliverable')")
    verified_leads = (await cur.fetchone())[0]

    # Emails sent
    cur = await conn.execute("SELECT COUNT(*) FROM leads WHERE status = 'sent'")
    emails_sent = (await cur.fetchone())[0]

    # Active inboxes
    cur = await conn.execute("SELECT COUNT(*), AVG(health_score) FROM inboxes WHERE status = 'active'")
    inbox_row = await cur.fetchone()
    active_inboxes = inbox_row[0] or 0
    avg_health = round(inbox_row[1] or 100, 1)

    await conn.close()

    return {
        "total_campaigns": total_campaigns,
        "total_leads": total_leads,
        "verified_leads": verified_leads,
        "emails_sent": emails_sent,
        "active_inboxes": active_inboxes,
        "avg_health_score": avg_health
    }
