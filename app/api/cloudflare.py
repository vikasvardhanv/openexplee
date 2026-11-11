from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import re
from app.database import get_db_connection
from app.engine.cloudflare import list_cloudflare_zones, auto_setup_cloudflare_domain

router = APIRouter(prefix="/api/cloudflare", tags=["cloudflare"])


class InboundWebhookPayload(BaseModel):
    from_email: str
    to_email: str
    subject: Optional[str] = ""


def _extract_email(raw: str) -> str:
    match = re.search(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", raw or "")
    return match.group(0).lower() if match else (raw or "").strip().lower()


@router.post("/inbound-webhook")
async def handle_inbound_reply(payload: InboundWebhookPayload):
    """Receives inbound reply notifications from the Cloudflare Worker and marks hot leads."""
    sender = _extract_email(payload.from_email)
    if not sender:
        return {"ok": False, "matched": 0}

    conn = await get_db_connection()
    try:
        cur = await conn.execute(
            "SELECT id, campaign_id FROM leads WHERE LOWER(email) = ?", (sender,)
        )
        rows = await cur.fetchall()
        matched_ids = [r["id"] for r in rows]
        campaign_ids = {r["campaign_id"] for r in rows if r["campaign_id"]}

        if matched_ids:
            await conn.execute(
                "UPDATE leads SET status = 'replied', is_hot_lead = 1 WHERE LOWER(email) = ?",
                (sender,),
            )
            for cid in campaign_ids:
                await conn.execute(
                    """
                    UPDATE campaigns
                    SET hot_leads_count = (
                        SELECT COUNT(*) FROM leads WHERE campaign_id = ? AND is_hot_lead = 1
                    )
                    WHERE id = ?
                    """,
                    (cid, cid),
                )
            await conn.commit()
    finally:
        await conn.close()

    return {"ok": True, "sender": sender, "matched": len(matched_ids)}


class CloudflareAuthRequest(BaseModel):
    api_token: str


class CloudflareSetupRequest(BaseModel):
    api_token: str
    zone_id: str
    domain_name: str
    forward_to_email: Optional[str] = "vikash@viktron.ai"
    redirect_target_url: Optional[str] = "https://viktron.ai"


@router.post("/zones")
async def get_zones(req: CloudflareAuthRequest):
    zones = await list_cloudflare_zones(req.api_token)
    if not zones:
        raise HTTPException(status_code=400, detail="Invalid Cloudflare API token or no domains found")
    return zones


@router.post("/setup")
async def setup_domain(req: CloudflareSetupRequest):
    result = await auto_setup_cloudflare_domain(
        api_token=req.api_token,
        zone_id=req.zone_id,
        domain_name=req.domain_name,
        forward_to_email=req.forward_to_email or "vikash@viktron.ai",
        redirect_target_url=req.redirect_target_url or "https://viktron.ai"
    )
    return result

