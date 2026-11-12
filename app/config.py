from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from pathlib import Path
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    database_path: str = "data/openexplee.db"

    # LLM Settings (Defaults to local Claude Code subscription + Thor GPU Ollama)
    default_llm_provider: str = Field(
        default="claude_cli",
        description="claude_cli | thor_ollama | gemini | openai | anthropic | groq | ollama",
    )
    default_llm_model: str = "gemini-2.5-flash"
    gemini_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    ollama_base_url: str = "http://localhost:11434"
    thor_ollama_url: str = "http://100.114.48.60:11434"
    thor_ollama_model: str = "qwen3.8:27b-q4_K_M"
    thor_cdp_url: str = "http://100.114.48.60:9222"

    # Booking Link (Self-Hosted Cal.com on VPS)
    booking_url: str = "https://cal.viktron.ai/vikash/15min"

    # Search & Enrichment
    serper_api_key: Optional[str] = None

    # Sending & Deliverability Safeguards
    protected_primary_domains: str = "viktron.ai"
    max_daily_sends_per_inbox: int = 35
    min_delay_seconds: int = 120
    max_delay_seconds: int = 300
    warmup_enabled: bool = True
    warmup_daily_target: int = 15


settings = Settings()

