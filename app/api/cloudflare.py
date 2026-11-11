from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from app.engine.cloudflare import list_cloudflare_zones, auto_setup_cloudflare_domain

router = APIRouter(prefix="/api/cloudflare", tags=["cloudflare"])


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
