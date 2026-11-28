from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AliasChoices, Field
from pathlib import Path
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    database_path: str = "data/openexplee.db"

    # LLM Settings (Defaults to Thor GPU Ollama + fallback to Claude CLI)
    default_llm_provider: str = Field(
        default="thor_ollama",
        description="thor_ollama | claude_cli | gemini | openai | anthropic | groq | ollama",
    )
    default_llm_model: str = "gemini-2.5-flash"
    gemini_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    ollama_base_url: str = "http://localhost:11434"
    thor_ollama_url: str = "http://100.114.48.60:11434"
    # Scored 40/43 against Claude Opus on logged people-extraction calls (2026-11-26); llama3.2:3b scored 32/43.
    thor_ollama_model: str = "qwen3.8:27b-q4_K_M"
    thor_cdp_url: str = "http://100.114.48.60:9222"

    # Obscura Rust Headless Browser Settings (Anti-bot stealth & lightweight CDP)
    obscura_bin_path: str = "bin/obscura"
    obscura_enabled: bool = True
    obscura_stealth: bool = True
    obscura_cdp_url: str = "http://127.0.0.1:9222"

    # Booking Link (Self-Hosted Cal.com on VPS)
    booking_url: str = "https://cal.viktron.ai/vikash/15min"

    # Search & Enrichment
    serper_api_key: Optional[str] = None

    # Sending & Deliverability Safeguards
    protected_primary_domains: str = "viktron.ai"
    max_daily_sends_per_inbox: int = 35
    min_delay_seconds: int = 120
    max_delay_seconds: int = 300
    # Compliance footer, added under every cold email and follow-up (CAN-SPAM)
    opt_out_line: str = 'Not interested? Reply "no thanks" and I won\'t email again.'
    postal_address: str = ""

    # Reply handling: read replies over IMAP from the mailbox that Reply-To points at
    # The reply mailbox has one login for SMTP and IMAP on one host (mail.privateemail.com),
    # so the SMTP_* names in .sops.env also fill these fields. An IMAP_* name has priority.
    imap_host: str = Field(default="", validation_alias=AliasChoices("imap_host", "smtp_host"))
    imap_port: int = 993
    imap_user: str = Field(default="", validation_alias=AliasChoices("imap_user", "smtp_user"))
    imap_password: str = Field(default="", validation_alias=AliasChoices("imap_password", "smtp_pass"))
    auto_reply_enabled: bool = True
    max_auto_replies_per_lead: int = 2
    inbound_webhook_token: str = ""

    warmup_enabled: bool = True
    warmup_daily_target: int = 15


settings = Settings()

