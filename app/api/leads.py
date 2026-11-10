from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from app.database import get_db_connection
from app.engine.verifier import verify_email_smtp, generate_email_permutations
from app.engine.copywriter import generate_cold_email

router = APIRouter(prefix="/api/leads", tags=["leads"])


class VerifyEmailRequest(BaseModel):
    email: str


class GenerateCopyRequest(BaseModel):
    company_name: str
    company_domain: str
    contact_name: str
    job_title: str
    company_summary: Optional[str] = ""
    value_proposition: str


class UpdateLeadRequest(BaseModel):
    email: Optional[str] = None
    contact_name: Optional[str] = None
    personalized_subject: Optional[str] = None
    personalized_body: Optional[str] = None
    status: Optional[str] = None


@router.get("")
async def list_leads(campaign_id: Optional[int] = None, limit: int = 50):
    conn = await get_db_connection()
    if campaign_id:
        cursor = await conn.execute("SELECT * FROM leads WHERE campaign_id = ? ORDER BY id DESC LIMIT ?", (campaign_id, limit))
    else:
        cursor = await conn.execute("SELECT * FROM leads ORDER BY id DESC LIMIT ?", (limit,))
    rows = await cursor.fetchall()
    await conn.close()
    return [dict(r) for r in rows]


@router.post("/verify")
async def verify_email_endpoint(req: VerifyEmailRequest):
    return await verify_email_smtp(req.email)


@router.post("/generate-copy")
async def generate_copy_endpoint(req: GenerateCopyRequest):
    return await generate_cold_email(
        company_name=req.company_name,
        company_domain=req.company_domain,
        contact_name=req.contact_name,
        job_title=req.job_title,
        company_summary=req.company_summary or "",
        value_proposition=req.value_proposition
    )


@router.put("/{lead_id}")
async def update_lead(lead_id: int, req: UpdateLeadRequest):
    conn = await get_db_connection()
    cursor = await conn.execute("SELECT id FROM leads WHERE id = ?", (lead_id,))
    if not await cursor.fetchone():
        await conn.close()
        raise HTTPException(status_code=404, detail="Lead not found")

    updates = []
    params = []
    if req.email is not None:
        updates.append("email = ?")
        params.append(req.email)
    if req.contact_name is not None:
        updates.append("contact_name = ?")
        params.append(req.contact_name)
    if req.personalized_subject is not None:
        updates.append("personalized_subject = ?")
        params.append(req.personalized_subject)
    if req.personalized_body is not None:
        updates.append("personalized_body = ?")
        params.append(req.personalized_body)
    if req.status is not None:
        updates.append("status = ?")
        params.append(req.status)

    if updates:
        params.append(lead_id)
        await conn.execute(f"UPDATE leads SET {', '.join(updates)} WHERE id = ?", tuple(params))
        await conn.commit()

    await conn.close()
    return {"ok": True}


@router.delete("/{lead_id}")
async def delete_lead(lead_id: int):
    conn = await get_db_connection()
    await conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    await conn.commit()
    await conn.close()
    return {"ok": True}

