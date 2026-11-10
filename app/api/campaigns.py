from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
from app.database import get_db_connection
from app.engine.discovery import discover_leads_for_icp
from app.engine.verifier import verify_email_smtp
from app.engine.copywriter import generate_cold_email
from app.engine.sequencer import dispatch_campaign_batch

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


class CampaignCreate(BaseModel):
    name: str
    icp_prompt: str
    target_industry: Optional[str] = "B2B SaaS"
    value_proposition: str


async def run_discovery_task(campaign_id: int, icp_prompt: str, value_prop: str):
    conn = await get_db_connection()
    try:
        await conn.execute("UPDATE campaigns SET status = 'discovering' WHERE id = ?", (campaign_id,))
        await conn.commit()

        leads = await discover_leads_for_icp(icp_prompt, max_companies=8)

        for l in leads:
            cursor = await conn.execute("""
                INSERT INTO leads (
                    campaign_id, company_name, company_domain, website_url,
                    contact_name, job_title, email, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'discovered')
            """, (
                campaign_id, l["company_name"], l["company_domain"], l["website_url"],
                l["contact_name"], l["job_title"], l["email"]
            ))
            lead_id = cursor.lastrowid

            # Auto-verify candidate email
            if l.get("email"):
                ver_res = await verify_email_smtp(l["email"])
                await conn.execute("""
                    UPDATE leads 
                    SET verification_status = ?, verification_reason = ?
                    WHERE id = ?
                """, (ver_res["status"], ver_res.get("details", ""), lead_id))

            # Auto-draft AI personalized email
            email_draft = await generate_cold_email(
                company_name=l["company_name"],
                company_domain=l["company_domain"],
                contact_name=l["contact_name"],
                job_title=l["job_title"],
                company_summary=l.get("summary", ""),
                value_proposition=value_prop
            )
            await conn.execute("""
                UPDATE leads 
                SET personalized_subject = ?, personalized_body = ?, status = 'enriched'
                WHERE id = ?
            """, (email_draft.get("subject", ""), email_draft.get("body", ""), lead_id))

        await conn.execute("""
            UPDATE campaigns 
            SET total_leads = (SELECT COUNT(*) FROM leads WHERE campaign_id = ?),
                status = 'ready'
            WHERE id = ?
        """, (campaign_id, campaign_id))
        await conn.commit()

    except Exception as e:
        print(f"[Campaign Task Error]: {e}")
        await conn.execute("UPDATE campaigns SET status = 'error' WHERE id = ?", (campaign_id,))
        await conn.commit()
    finally:
        await conn.close()


@router.get("")
async def list_campaigns():
    conn = await get_db_connection()
    cursor = await conn.execute("""
        SELECT c.*, 
            (SELECT COUNT(*) FROM leads l WHERE l.campaign_id = c.id AND l.verification_status IN ('verified', 'deliverable')) as verified_count
        FROM campaigns c
        ORDER BY c.created_at DESC
    """)
    rows = await cursor.fetchall()
    await conn.close()
    return [dict(r) for r in rows]


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
    background_tasks.add_task(run_discovery_task, campaign_id, req.icp_prompt, req.value_proposition)

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


@router.post("/{campaign_id}/launch")
async def launch_campaign(campaign_id: int):
    res = await dispatch_campaign_batch(campaign_id, limit=20)
    return res


@router.delete("/{campaign_id}")
async def delete_campaign(campaign_id: int):
    conn = await get_db_connection()
    await conn.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
    await conn.commit()
    await conn.close()
    return {"ok": True}
