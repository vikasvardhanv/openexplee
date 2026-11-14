from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
from app.database import get_db_connection
from app.engine.discovery import discover_leads_for_icp
from app.engine.verifier import verify_email_smtp
from app.engine.copywriter import generate_cold_email
from app.engine.sequencer import dispatch_campaign_batch
from app.engine.analyzer import analyze_my_product

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


class ProductAnalyzeRequest(BaseModel):
    url: str


class AutopilotRequest(BaseModel):
    url: str
    auto_send: bool = False
    max_campaigns: int = 3
    leads_per_campaign: int = 5


@router.post("/analyze-product")
async def analyze_product_endpoint(req: ProductAnalyzeRequest):
    return await analyze_my_product(req.url)


@router.post("/autopilot")
async def launch_autopilot(req: AutopilotRequest, background_tasks: BackgroundTasks):
    """
    Explee-style 1-click autopilot:
    1. Crawls the submitted website & extracts product + ICP segments
    2. Creates campaigns for each ICP segment
    3. Discovers leads, verifies mailboxes, drafts AI copy, and optionally auto-sends
    """
    analysis = await analyze_my_product(req.url)
    value_prop = analysis.get("value_proposition") or f"Solutions by {req.url}"
    product_name = analysis.get("product_name", req.url)
    icps = (analysis.get("target_icps") or [])[: max(1, min(req.max_campaigns, 6))]

    created = []
    conn = await get_db_connection()
    try:
        for icp in icps:
            camp_name = icp.get("campaign_name") or "Target ICP"
            icp_query = icp.get("icp_query") or value_prop
            cursor = await conn.execute(
                """
                INSERT INTO campaigns (name, icp_prompt, target_industry, value_proposition, status)
                VALUES (?, ?, ?, ?, 'discovering')
                """,
                (camp_name, icp_query, product_name[:80], value_prop),
            )
            cid = cursor.lastrowid
            created.append({"id": cid, "name": camp_name, "icp_query": icp_query})
            background_tasks.add_task(
                run_discovery_task,
                cid,
                icp_query,
                value_prop,
                req.auto_send,
                max(1, min(req.leads_per_campaign, 15)),
            )
        await conn.commit()
    finally:
        await conn.close()

    return {
        "product_name": product_name,
        "value_proposition": value_prop,
        "key_features": analysis.get("key_features", []),
        "crawled_subpages": analysis.get("crawled_subpages", []),
        "auto_send": req.auto_send,
        "campaigns_created": created,
    }


class CampaignCreate(BaseModel):
    name: str
    icp_prompt: str
    target_industry: Optional[str] = "B2B SaaS"
    value_proposition: str
    auto_send: bool = False


async def run_discovery_task(
    campaign_id: int,
    icp_prompt: str,
    value_prop: str,
    auto_send: bool = False,
    max_companies: int = 8,
):
    # Set status to discovering
    conn = await get_db_connection()
    try:
        await conn.execute("UPDATE campaigns SET status = 'discovering' WHERE id = ?", (campaign_id,))
        await conn.commit()
    finally:
        await conn.close()

    try:
        leads = await discover_leads_for_icp(icp_prompt, max_companies=max_companies)

        enriched_leads = []
        for l in leads:
            ver_status = "unverified"
            ver_reason = ""
            if l.get("email"):
                try:
                    ver_res = await verify_email_smtp(l["email"])
                    ver_status = ver_res.get("status", "unverified")
                    ver_reason = ver_res.get("details", "")
                except Exception as ve:
                    ver_reason = str(ve)

            # Auto-draft AI personalized email
            subject = f"quick question for {l['company_name']}"
            body = f"Hi {l['contact_name'].split()[0] if l.get('contact_name') else 'there'},\n\nSaw what you are building at {l['company_name']} and wanted to connect."
            try:
                email_draft = await generate_cold_email(
                    company_name=l["company_name"],
                    company_domain=l["company_domain"],
                    contact_name=l["contact_name"],
                    job_title=l["job_title"],
                    company_summary=l.get("summary", ""),
                    value_proposition=value_prop
                )
                if email_draft.get("subject"):
                    subject = email_draft["subject"]
                if email_draft.get("body"):
                    body = email_draft["body"]
            except Exception as ge:
                print(f"[Discovery] Copy generation note: {ge}")

            enriched_leads.append({
                **l,
                "verification_status": ver_status,
                "verification_reason": ver_reason,
                "personalized_subject": subject,
                "personalized_body": body,
            })

        # Save to DB in one clean transaction
        conn = await get_db_connection()
        try:
            for l in enriched_leads:
                await conn.execute("""
                    INSERT INTO leads (
                        campaign_id, company_name, company_domain, website_url,
                        contact_name, job_title, email, status,
                        verification_status, verification_reason,
                        personalized_subject, personalized_body
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'enriched', ?, ?, ?, ?)
                """, (
                    campaign_id, l["company_name"], l["company_domain"], l["website_url"],
                    l["contact_name"], l["job_title"], l["email"],
                    l["verification_status"], l["verification_reason"],
                    l["personalized_subject"], l["personalized_body"]
                ))

            await conn.execute("""
                UPDATE campaigns 
                SET total_leads = (SELECT COUNT(*) FROM leads WHERE campaign_id = ?),
                    status = 'ready'
                WHERE id = ?
            """, (campaign_id, campaign_id))
            await conn.commit()
        finally:
            await conn.close()

        if auto_send:
            await dispatch_campaign_batch(campaign_id, limit=max_companies)

    except Exception as e:
        print(f"[Campaign Task Error]: {e}")
        conn = await get_db_connection()
        try:
            await conn.execute("UPDATE campaigns SET status = 'error' WHERE id = ?", (campaign_id,))
            await conn.commit()
        finally:
            await conn.close()


@router.get("")
async def list_campaigns():
    conn = await get_db_connection()
    cursor = await conn.execute("""
        SELECT c.*, 
            (SELECT COUNT(*) FROM leads l WHERE l.campaign_id = c.id AND l.verification_status IN ('verified', 'deliverable')) as verified_count,
            (SELECT COUNT(*) FROM leads l WHERE l.campaign_id = c.id AND l.status = 'sent') as sent_leads_count,
            (SELECT COUNT(*) FROM leads l WHERE l.campaign_id = c.id AND l.status IN ('enriched', 'queued', 'discovered') AND l.verification_status != 'invalid' AND l.email IS NOT NULL AND l.email != '') as ready_leads_count
        FROM campaigns c
        ORDER BY c.created_at DESC
    """)
    rows = await cursor.fetchall()
    await conn.close()

    result = []
    for r in rows:
        d = dict(r)
        total = d.get("total_leads") or 0
        sent = d.get("sent_count") or d.get("sent_leads_count") or 0
        raw_status = d.get("status", "draft")

        # Determine real-time display status
        if raw_status in ("discovering", "sending"):
            d["display_status"] = raw_status
        elif total == 0:
            d["display_status"] = "no_leads"
        elif total > 0 and sent >= total:
            d["display_status"] = "completed"
        elif total > 0 and sent < total:
            d["display_status"] = "ready"
        else:
            d["display_status"] = raw_status

        result.append(d)
    return result


@router.post("")
async def create_campaign(req: CampaignCreate, background_tasks: BackgroundTasks):
    conn = await get_db_connection()
    cursor = await conn.execute("""
        INSERT INTO campaigns (name, icp_prompt, target_industry, value_proposition, status)
        VALUES (?, ?, ?, ?, 'draft')
    """, (req.name, req.icp_prompt, req.target_industry, req.value_proposition))
    campaign_id = cursor.lastrowid
    await conn.commit()
    await conn.close()

    # Automatically trigger discovery in the background
    background_tasks.add_task(run_discovery_task, campaign_id, req.icp_prompt, req.value_proposition, req.auto_send)

    return {"id": campaign_id, "name": req.name, "status": "discovering"}


@router.get("/{campaign_id}")
async def get_campaign(campaign_id: int):
    conn = await get_db_connection()
    cursor = await conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
    camp = await cursor.fetchone()
    if not camp:
        await conn.close()
        raise HTTPException(status_code=404, detail="Campaign not found")

    leads_cur = await conn.execute("SELECT * FROM leads WHERE campaign_id = ? ORDER BY id DESC", (campaign_id,))
    leads = [dict(r) for r in await leads_cur.fetchall()]
    await conn.close()

    result = dict(camp)
    result["leads"] = leads
    return result


@router.post("/{campaign_id}/discover")
async def trigger_campaign_discovery(campaign_id: int, background_tasks: BackgroundTasks):
    conn = await get_db_connection()
    try:
        cursor = await conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
        camp = await cursor.fetchone()
        if not camp:
            raise HTTPException(status_code=404, detail="Campaign not found")
        camp_dict = dict(camp)
    finally:
        await conn.close()

    background_tasks.add_task(
        run_discovery_task,
        campaign_id,
        camp_dict["icp_prompt"],
        camp_dict.get("value_proposition") or "",
        False
    )
    return {"id": campaign_id, "status": "discovering"}


async def _run_campaign_dispatch_background(campaign_id: int, limit: int = 20):
    try:
        await dispatch_campaign_batch(campaign_id, limit=limit)
    except Exception as e:
        print(f"[Sequencer Background Error]: {e}")
    finally:
        conn = await get_db_connection()
        try:
            cur = await conn.execute("SELECT COUNT(*) as tot, (SELECT COUNT(*) FROM leads WHERE campaign_id = ? AND status = 'sent') as snt FROM leads WHERE campaign_id = ?", (campaign_id, campaign_id))
            r = await cur.fetchone()
            tot = r["tot"] if r else 0
            snt = r["snt"] if r else 0
            new_status = "completed" if (tot > 0 and snt >= tot) else "ready"
            await conn.execute("UPDATE campaigns SET status = ?, sent_count = ? WHERE id = ?", (new_status, snt, campaign_id))
            await conn.commit()
        finally:
            await conn.close()


@router.post("/{campaign_id}/launch")
async def launch_campaign(campaign_id: int, background_tasks: BackgroundTasks):
    conn = await get_db_connection()
    try:
        cur = await conn.execute("""
            SELECT 
                (SELECT COUNT(*) FROM leads WHERE campaign_id = ? AND status IN ('enriched', 'queued', 'discovered') AND verification_status != 'invalid' AND email IS NOT NULL AND email != '') as sendable,
                (SELECT COUNT(*) FROM leads WHERE campaign_id = ?) as total,
                (SELECT COUNT(*) FROM leads WHERE campaign_id = ? AND status = 'sent') as sent
        """, (campaign_id, campaign_id, campaign_id))
        row = await cur.fetchone()
        sendable = row["sendable"] if row else 0
        total = row["total"] if row else 0
        sent = row["sent"] if row else 0

        camp_cur = await conn.execute("SELECT status FROM campaigns WHERE id = ?", (campaign_id,))
        camp_row = await camp_cur.fetchone()
        current_status = camp_row["status"] if camp_row else "draft"
    finally:
        await conn.close()

    if current_status == "sending":
        return {"status": "sending", "message": "Campaign is already dispatching emails in the background!"}
    if current_status == "discovering":
        return {"status": "discovering", "message": "Campaign is currently discovering leads. Please wait until discovery finishes."}

    if sendable == 0:
        if total > 0 and sent >= total:
            return {
                "dispatched": 0,
                "status": "completed",
                "message": f"All {total} leads in this campaign have already been dispatched!"
            }
        return {
            "dispatched": 0,
            "status": "no_leads",
            "message": "No leads in this campaign yet. Click 'Find Leads' first to discover prospects."
        }

    conn = await get_db_connection()
    try:
        await conn.execute("UPDATE campaigns SET status = 'sending' WHERE id = ?", (campaign_id,))
        await conn.commit()
    finally:
        await conn.close()

    background_tasks.add_task(_run_campaign_dispatch_background, campaign_id, 20)
    return {
        "status": "sending",
        "message": f"Started dispatching {sendable} emails in the background!",
        "queue_count": sendable
    }


@router.delete("/{campaign_id}")
async def delete_campaign(campaign_id: int):
    conn = await get_db_connection()
    await conn.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
    await conn.commit()
    await conn.close()
    return {"ok": True}

