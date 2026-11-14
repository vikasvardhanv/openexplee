from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import aiosmtplib
import httpx
from app.config import settings
from app.database import get_db_connection
from app.engine.warmup import check_domain_dns_health, simulate_warmup_tick

router = APIRouter(prefix="/api/inboxes", tags=["inboxes"])


class InboxCreate(BaseModel):
    email: str
    display_name: str
    provider: str  # 'google', 'outlook', 'hyvor', 'cf_worker', 'mailflare', 'custom'
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = 587
    smtp_user: str
    smtp_password: str
    reply_to: Optional[str] = None
    cc_email: Optional[str] = None
    daily_quota: Optional[int] = 35
    allow_primary_domain: bool = False


class CheckDnsRequest(BaseModel):
    domain: str


@router.get("")
async def list_inboxes():
    conn = await get_db_connection()
    cursor = await conn.execute("SELECT id, email, display_name, provider, smtp_host, smtp_port, reply_to, cc_email, daily_quota, sent_today, last_sent_at, status, health_score, error_message, created_at FROM inboxes ORDER BY id DESC")
    rows = await cursor.fetchall()
    await conn.close()
    return [dict(r) for r in rows]


@router.post("")
async def create_inbox(req: InboxCreate):
    # Primary Domain Shield: prevent accidentally sending cold outreach from your main brand domain
    sender_domain = req.email.split("@")[-1].lower().strip()
    protected = {d.strip().lower() for d in settings.protected_primary_domains.split(",") if d.strip()}
    if sender_domain in protected and not req.allow_primary_domain:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Primary domain '{sender_domain}' is protected to keep your main brand reputation safe! "
                f"Use a secondary lookalike domain (e.g. try{sender_domain}) as Sender Email, "
                f"and put your real email in Reply-To / Master CC."
            ),
        )

    # Auto-fill standard provider SMTP hosts
    smtp_host = req.smtp_host
    smtp_port = req.smtp_port or 587

    if req.provider == "google":
        smtp_host = "smtp.gmail.com"
        smtp_port = 587
    elif req.provider == "outlook":
        smtp_host = "smtp.office365.com"
        smtp_port = 587
    elif req.provider == "hyvor":
        smtp_host = smtp_host or "mail.hyvor.com"
        smtp_port = 587
    elif req.provider in ("cf_worker", "mailflare"):
        if not smtp_host:
            raise HTTPException(status_code=400, detail="Worker / Mailflare base URL is required")
        if not smtp_host.startswith("http"):
            smtp_host = f"https://{smtp_host}"
        smtp_port = 443

    if not smtp_host:
        raise HTTPException(status_code=400, detail="SMTP host or endpoint URL is required")

    conn = await get_db_connection()
    try:
        cursor = await conn.execute("""
            INSERT INTO inboxes (
                email, display_name, provider, smtp_host, smtp_port,
                smtp_user, smtp_password, reply_to, cc_email, daily_quota, status, health_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 100)
        """, (
            req.email, req.display_name, req.provider, smtp_host, smtp_port,
            req.smtp_user, req.smtp_password, req.reply_to, req.cc_email, req.daily_quota
        ))
        inbox_id = cursor.lastrowid
        await conn.commit()
    except Exception as e:
        await conn.close()
        raise HTTPException(status_code=400, detail=f"Failed to add inbox: {str(e)}")

    await conn.close()
    return {"id": inbox_id, "email": req.email, "status": "active"}


@router.post("/{inbox_id}/test")
async def test_inbox_connection(inbox_id: int):
    conn = await get_db_connection()
    cursor = await conn.execute("SELECT * FROM inboxes WHERE id = ?", (inbox_id,))
    row = await cursor.fetchone()
    if not row:
        await conn.close()
        raise HTTPException(status_code=404, detail="Inbox not found")

    inbox = dict(row)
    await conn.close()

    try:
        provider = inbox.get("provider", "custom")
        if provider == "cf_worker":
            base_url = inbox["smtp_host"].rstrip("/")
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{base_url}/health")
                if resp.status_code == 200 and resp.json().get("ok"):
                    return {"connected": True, "message": "Cloudflare Email Worker is online and reachable!"}
                return {"connected": False, "error": f"Worker returned HTTP {resp.status_code}"}

        if provider == "mailflare":
            base_url = inbox["smtp_host"].rstrip("/")
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{base_url}/api/v1/mailboxes",
                    headers={"Authorization": f"Bearer {inbox['smtp_password']}"}
                )
                if resp.status_code == 200:
                    return {"connected": True, "message": "Mailflare API authenticated successfully!"}
                return {"connected": False, "error": f"Mailflare returned HTTP {resp.status_code}: {resp.text}"}

        port = inbox.get("smtp_port", 587)
        use_tls = (port == 465)
        start_tls = (port == 587 or inbox.get("use_tls", 1))

        smtp_client = aiosmtplib.SMTP(
            hostname=inbox["smtp_host"],
            port=port,
            use_tls=use_tls,
            start_tls=start_tls,
            timeout=10.0
        )
        await smtp_client.connect()
        if inbox.get("smtp_user") and inbox["smtp_user"] != "none" and inbox.get("smtp_password") and inbox["smtp_password"] != "none":
            await smtp_client.login(inbox["smtp_user"], inbox["smtp_password"])
        await smtp_client.quit()

        return {"connected": True, "message": "SMTP Relay connection successful & verified!"}
    except Exception as e:
        return {"connected": False, "error": str(e)}


@router.post("/{inbox_id}/warmup-tick")
async def trigger_warmup_tick(inbox_id: int):
    return await simulate_warmup_tick(inbox_id)


@router.post("/check-dns")
async def check_dns(req: CheckDnsRequest):
    return await check_domain_dns_health(req.domain)


@router.delete("/{inbox_id}")
async def delete_inbox(inbox_id: int):
    conn = await get_db_connection()
    await conn.execute("DELETE FROM inboxes WHERE id = ?", (inbox_id,))
    await conn.commit()
    await conn.close()
    return {"ok": True}

