import asyncio
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from app.database import get_db_connection
from app.engine.sequencer import send_single_email
from app.config import settings
from app.engine.replies import booking_link

router = APIRouter(prefix="/api/mailbox", tags=["mailbox"])


class ManualReplyRequest(BaseModel):
    lead_id: int
    body: str
    subject: Optional[str] = None
    cc_email: Optional[str] = "vikash@viktron.ai"


class UpdateThreadTagRequest(BaseModel):
    tag: str  # 'hot', 'needs_reply', 'not_interested', 'meeting_booked', 'cleared'


class DecisionActionRequest(BaseModel):
    lead_id: int
    action: str  # 'send_now', 'monday', 'not_needed'
    custom_reply: Optional[str] = None
    cc_email: Optional[str] = "vikash@viktron.ai"


@router.get("/decisions")
async def get_pending_decisions():
    """
    Returns high-priority decision cards for today (Explee '1 decision for today' flow).
    Surfaces hot replies and leads that have gone quiet and need a 1-click nudge.
    """
    conn = await get_db_connection()
    try:
        # Find leads that replied OR went quiet for >= 3 days after an outreach send
        query = """
            SELECT l.*, c.name as campaign_name, c.value_proposition, c.booking_url,
                   CAST(ROUND(julianday('now') - julianday(COALESCE(l.sent_at, l.created_at))) AS INTEGER) as days_quiet
            FROM leads l
            JOIN campaigns c ON l.campaign_id = c.id
            WHERE (l.status = 'replied' OR l.is_hot_lead = 1 OR (l.status = 'sent' AND (julianday('now') - julianday(COALESCE(l.sent_at, l.created_at))) >= 3))
              AND l.status NOT IN ('opted_out', 'bounced')
            ORDER BY l.is_hot_lead DESC, days_quiet DESC
            LIMIT 10
        """
        cur = await conn.execute(query)
        rows = await cur.fetchall()
        decisions = []

        for r in rows:
            d = dict(r)
            booking_url = booking_link(d)
            slot_line = f"Easiest is to grab a 15-minute slot here:\n{booking_url}\n\n" if booking_url else ""
            first_name = d.get("first_name") or (d.get("contact_name", "").split()[0] if d.get("contact_name") else "there")
            days_quiet = max(1, d.get("days_quiet") or 1)

            # Get latest prospect reply if available
            cur_msg = await conn.execute(
                "SELECT body, created_at FROM messages WHERE lead_id = ? AND direction = 'inbound' ORDER BY created_at DESC LIMIT 1",
                (d["id"],)
            )
            inbound_msg = await cur_msg.fetchone()
            prospect_quote = dict(inbound_msg)["body"] if inbound_msg else ""

            if d.get("is_hot_lead") or d.get("status") == "replied":
                suggested_reply = (
                    f"Hi {first_name},\n\n"
                    f"Thanks for the reply.\n\n{slot_line}"
                    "Best,\n"
                    "Vikash"
                )
            else:
                suggested_reply = (
                    f"Hi {first_name},\n\n"
                    f"Following up on my earlier note.\n\n{slot_line}"
                    "Best,\n"
                    "Vikash"
                )

            decisions.append({
                "lead_id": d["id"],
                "contact_name": d.get("contact_name") or "Decision Maker",
                "email": d.get("email"),
                "company_name": d.get("company_name") or d.get("company_domain"),
                "job_title": d.get("job_title") or "Executive",
                "campaign_name": d.get("campaign_name"),
                "days_quiet": days_quiet,
                "is_reply": bool(inbound_msg),
                "prospect_quote": prospect_quote,
                "suggested_reply": suggested_reply,
            })

        return {"total_decisions": len(decisions), "decisions": decisions}
    finally:
        await conn.close()


@router.post("/decisions/act")
async def act_on_decision(req: DecisionActionRequest):
    """
    Executes a 1-click decision (Send now, Monday morning, Not needed) matching Explee.
    """
    conn = await get_db_connection()
    try:
        cur = await conn.execute(
            "SELECT l.*, c.booking_url FROM leads l LEFT JOIN campaigns c ON c.id = l.campaign_id WHERE l.id = ?",
            (req.lead_id,))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Lead not found")
        lead = dict(row)

        if req.action == "send_now":
            cur_inbox = await conn.execute("SELECT * FROM inboxes WHERE status = 'active' ORDER BY id ASC LIMIT 1")
            inbox_row = await cur_inbox.fetchone()
            if not inbox_row:
                raise HTTPException(status_code=400, detail="No active sending inbox configured.")
            inbox = dict(inbox_row)
            if req.cc_email:
                inbox["cc_email"] = req.cc_email

            body = req.custom_reply or (
                f"Hi {lead.get('first_name') or 'there'},\n\n"
                f"Following up on my previous note.\n\n"
                + (f"Easiest is to grab 15 minutes here:\n{booking_link(lead)}\n\n" if booking_link(lead) else "") +
                "Best,\nVikash"
            )
            subject = f"Re: {lead.get('personalized_subject') or 'quick question'}"

            res = await send_single_email(
                inbox=inbox,
                to_email=lead["email"],
                subject=subject,
                body=body,
                lead_id=lead["id"],
                is_reply=True,
            )
            if not res.get("success"):
                raise HTTPException(status_code=500, detail=f"Send failed: {res.get('error')}")

            await conn.execute(
                "UPDATE leads SET status = 'sent', followup_step = COALESCE(followup_step, 0) + 1, sent_at = CURRENT_TIMESTAMP WHERE id = ?",
                (lead["id"],)
            )
            await conn.commit()
            return {"status": "dispatched", "lead_id": lead["id"], "action": "send_now"}

        elif req.action == "monday":
            await conn.execute(
                "UPDATE leads SET next_followup_at = datetime('now', '+3 days') WHERE id = ?",
                (lead["id"],)
            )
            await conn.commit()
            return {"status": "snoozed", "lead_id": lead["id"], "action": "monday"}

        elif req.action == "not_needed":
            await conn.execute(
                "UPDATE leads SET is_hot_lead = 0, followup_step = COALESCE(followup_step, 0) + 1 WHERE id = ?",
                (lead["id"],)
            )
            await conn.commit()
            return {"status": "dismissed", "lead_id": lead["id"], "action": "not_needed"}

        raise HTTPException(status_code=400, detail=f"Invalid action: {req.action}")
    finally:
        await conn.close()


@router.get("/threads")
async def list_mailbox_threads(folder: str = "all", search: Optional[str] = None, workspace: Optional[str] = None):
    """
    Returns outbound and inbound email conversation threads.
    Matches the Explee / Smartlead unified mailbox architecture.
    """
    conn = await get_db_connection()
    try:
        query = """
            SELECT l.*, c.name as campaign_name, c.value_proposition, COALESCE(c.workspace, 'Viktron.ai') as workspace
            FROM leads l
            JOIN campaigns c ON l.campaign_id = c.id
            WHERE l.status IN ('sent', 'replied', 'auto_replied', 'opted_out')
        """
        params = []
        if folder == "hot":
            query += " AND (l.is_hot_lead = 1 OR l.status = 'replied')"
        elif folder == "needs_reply":
            query += " AND l.status = 'replied'"

        if workspace and workspace != "all":
            if workspace.lower() == "findmyfuture":
                query += " AND (LOWER(c.workspace) = 'findmyfuture' OR c.name LIKE '%FindMyFuture%')"
            else:
                query += " AND (LOWER(c.workspace) = LOWER(?) AND c.name NOT LIKE '%FindMyFuture%')"
                params.append(workspace)

        if search:
            query += " AND (l.contact_name LIKE ? OR l.company_name LIKE ? OR l.email LIKE ?)"
            s_param = f"%{search}%"
            params.extend([s_param, s_param, s_param])

        query += " ORDER BY COALESCE(l.sent_at, l.created_at) DESC LIMIT 100"

        cur = await conn.execute(query, params)
        rows = await cur.fetchall()
        threads = []
        for r in rows:
            d = dict(r)
            threads.append({
                "id": d["id"],
                "contact_name": d.get("contact_name") or "Decision Maker",
                "job_title": d.get("job_title") or "Executive",
                "company_name": d.get("company_name") or d.get("company_domain"),
                "company_domain": d.get("company_domain"),
                "email": d.get("email"),
                "status": d.get("status"),
                "is_hot_lead": bool(d.get("is_hot_lead")),
                "campaign_name": d.get("campaign_name"),
                "workspace": d.get("workspace") or ("FindMyFuture" if "findmyfuture" in (d.get("campaign_name") or "").lower() else "Viktron.ai"),
                "sent_from_inbox": d.get("sent_from_inbox") or "outreach@getagentirl.online",
                "sent_at": d.get("sent_at") or d.get("created_at"),
                "subject": d.get("personalized_subject") or "Outreach Inquiry",
                "snippet": (d.get("personalized_body") or "")[:120].replace("\n", " "),
                "full_body": d.get("personalized_body"),
                "followup_step": d.get("followup_step") or 1,
            })
        return threads
    finally:
        await conn.close()


@router.get("/threads/{lead_id}")
async def get_thread_detail(lead_id: int):
    """
    Retrieves full conversation details, history, and suggested next nudge for a lead.
    """
    conn = await get_db_connection()
    try:
        cur = await conn.execute("""
            SELECT l.*, c.name as campaign_name, c.value_proposition, c.reply_to_email, c.cc_email
            FROM leads l
            JOIN campaigns c ON l.campaign_id = c.id
            WHERE l.id = ?
        """, (lead_id,))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Thread not found")
        lead = dict(row)

        booking_url = settings.booking_url or "https://cal.viktron.ai/vikash/15min"

        # Generate intelligent contextual nudge copy (Explee-style "Nudge Tyler?")
        first_name = lead.get("first_name") or (lead.get("contact_name", "").split()[0] if lead.get("contact_name") else "there")
        suggested_nudge = (
            f"Hi {first_name},\n\n"
            f"Sounds good. Easiest is to grab a quick 15-minute slot that suits you here:\n"
            f"{booking_url}\n\n"
            "Looking forward to speaking,\n"
            "Alex | AgentIRL"
        )

        our_address = lead.get("sent_from_inbox") or "outreach@getagentirl.online"
        cur = await conn.execute(
            "SELECT * FROM messages WHERE lead_id = ? ORDER BY created_at ASC, id ASC", (lead_id,)
        )
        messages = [
            {
                "direction": m["direction"],
                "sender": our_address if m["direction"] == "outbound" else lead.get("email"),
                "recipient": lead.get("email") if m["direction"] == "outbound" else our_address,
                "subject": m["subject"],
                "body": m["body"],
                "intent": m["intent"],
                "timestamp": m["created_at"],
            }
            for m in await cur.fetchall()
        ]
        # Emails sent before the messages table existed have only the lead row.
        if not any(m["direction"] == "outbound" for m in messages):
            messages.insert(0, {
                "direction": "outbound",
                "sender": our_address,
                "recipient": lead.get("email"),
                "subject": lead.get("personalized_subject"),
                "body": lead.get("personalized_body"),
                "timestamp": lead.get("sent_at") or lead.get("created_at"),
            })

        return {
            "lead": lead,
            "booking_url": booking_url,
            "suggested_nudge": suggested_nudge,
            "messages": messages,
        }
    finally:
        await conn.close()


@router.post("/reply")
async def send_manual_reply(req: ManualReplyRequest):
    """
    Sends a custom response or follow-up email directly to the prospect via VPS Relay,
    automatically CCing vikash@viktron.ai and setting Reply-To: vikash@viktron.ai.
    """
    conn = await get_db_connection()
    try:
        cur = await conn.execute("SELECT * FROM leads WHERE id = ?", (req.lead_id,))
        lead_row = await cur.fetchone()
        if not lead_row:
            raise HTTPException(status_code=404, detail="Lead not found")
        lead = dict(lead_row)

        cur_inbox = await conn.execute("SELECT * FROM inboxes WHERE status = 'active' ORDER BY id ASC LIMIT 1")
        inbox_row = await cur_inbox.fetchone()
        if not inbox_row:
            raise HTTPException(status_code=400, detail="No active sending inbox configured.")
        inbox = dict(inbox_row)

        subject = req.subject or f"Re: {lead.get('personalized_subject') or 'quick question'}"
        inbox_copy = dict(inbox)
        if req.cc_email:
            inbox_copy["cc_email"] = req.cc_email

        send_res = await send_single_email(
            inbox=inbox_copy,
            to_email=lead["email"],
            subject=subject,
            body=req.body,
            lead_id=req.lead_id,
            is_reply=True,
        )

        if send_res.get("success"):
            return {"status": "sent", "recipient": lead["email"], "subject": subject}
        else:
            raise HTTPException(status_code=500, detail=f"Relay error: {send_res.get('error')}")
    finally:
        await conn.close()


@router.post("/threads/{lead_id}/tag")
async def update_thread_tag(lead_id: int, req: UpdateThreadTagRequest):
    """
    Updates the prospect status tag (Hot, Replied, Not Interested, Meeting Booked).
    """
    conn = await get_db_connection()
    try:
        is_hot = 1 if req.tag in ("hot", "needs_reply", "meeting_booked") else 0
        new_status = req.tag if req.tag != "cleared" else "sent"
        await conn.execute("""
            UPDATE leads
            SET is_hot_lead = ?,
                status = ?
            WHERE id = ?
        """, (is_hot, new_status, lead_id))
        await conn.commit()
        return {"status": "updated", "lead_id": lead_id, "tag": req.tag}
    finally:
        await conn.close()
