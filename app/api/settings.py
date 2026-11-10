from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from app.config import settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


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
    if req.default_llm_provider is not None:
        settings.default_llm_provider = req.default_llm_provider
    if req.default_llm_model is not None:
        settings.default_llm_model = req.default_llm_model
    if req.gemini_api_key and "••" not in req.gemini_api_key:
        settings.gemini_api_key = req.gemini_api_key
    if req.openai_api_key and "••" not in req.openai_api_key:
        settings.openai_api_key = req.openai_api_key
    if req.anthropic_api_key and "••" not in req.anthropic_api_key:
        settings.anthropic_api_key = req.anthropic_api_key
    if req.groq_api_key and "••" not in req.groq_api_key:
        settings.groq_api_key = req.groq_api_key
    if req.ollama_base_url is not None:
        settings.ollama_base_url = req.ollama_base_url
    if req.serper_api_key and "••" not in req.serper_api_key:
        settings.serper_api_key = req.serper_api_key
    if req.max_daily_sends_per_inbox is not None:
        settings.max_daily_sends_per_inbox = req.max_daily_sends_per_inbox

    return {"ok": True, "provider": settings.default_llm_provider}
