from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from app.config import settings
from app.database import get_db_connection

router = APIRouter(prefix="/api/settings", tags=["settings"])

PERSISTED_FIELDS = (
    "default_llm_provider",
    "default_llm_model",
    "gemini_api_key",
    "openai_api_key",
    "anthropic_api_key",
    "groq_api_key",
    "ollama_base_url",
    "serper_api_key",
    "max_daily_sends_per_inbox",
)


class SettingsUpdate(BaseModel):
    default_llm_provider: Optional[str] = None
    default_llm_model: Optional[str] = None
    gemini_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    ollama_base_url: Optional[str] = None
    serper_api_key: Optional[str] = None
    max_daily_sends_per_inbox: Optional[int] = None


def mask_key(k: Optional[str]) -> str:
    if not k:
        return ""
    if len(k) <= 8:
        return "••••••••"
    return k[:4] + "••••••••" + k[-4:]


async def load_persisted_settings() -> None:
    """Restores saved settings from SQLite into the in-memory settings object on boot."""
    conn = await get_db_connection()
    try:
        cur = await conn.execute("SELECT key, value FROM app_settings")
        rows = await cur.fetchall()
        for row in rows:
            k, v = row["key"], row["value"]
            if k not in PERSISTED_FIELDS:
                continue
            if k == "max_daily_sends_per_inbox":
                setattr(settings, k, int(v))
            else:
                setattr(settings, k, v)
    finally:
        await conn.close()


@router.get("")
async def get_settings():
    return {
        "default_llm_provider": settings.default_llm_provider,
        "default_llm_model": settings.default_llm_model,
        "gemini_api_key": mask_key(settings.gemini_api_key),
        "openai_api_key": mask_key(settings.openai_api_key),
        "anthropic_api_key": mask_key(settings.anthropic_api_key),
        "groq_api_key": mask_key(settings.groq_api_key),
        "ollama_base_url": settings.ollama_base_url,
        "serper_api_key": mask_key(settings.serper_api_key),
        "max_daily_sends_per_inbox": settings.max_daily_sends_per_inbox,
    }


@router.post("")
async def update_settings(req: SettingsUpdate):
    updates = {}
    for field in PERSISTED_FIELDS:
        val = getattr(req, field)
        if val is None:
            continue
        if isinstance(val, str) and ("••" in val or val == ""):
            continue
        setattr(settings, field, val)
        updates[field] = str(val)

    if updates:
        conn = await get_db_connection()
        try:
            for k, v in updates.items():
                await conn.execute(
                    "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (k, v),
                )
            await conn.commit()
        finally:
            await conn.close()

    return {"ok": True, "provider": settings.default_llm_provider}
