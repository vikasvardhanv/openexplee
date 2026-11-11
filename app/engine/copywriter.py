import httpx
import json
import random
import re
from typing import Dict, Any, Optional
from app.config import settings


def resolve_spintax(text: str) -> str:
    """Resolves spintax patterns like {Hi|Hello|Hey} into randomized variants."""
    pattern = re.compile(r"\{([^{}]+)\}")
    while pattern.search(text):
        text = pattern.sub(lambda m: random.choice(m.group(1).split("|")), text)
    return text


async def generate_cold_email(
    company_name: str,
    company_domain: str,
    contact_name: str,
    job_title: str,
    company_summary: str,
    value_proposition: str,
    sender_name: str = "Vikas",
    provider: Optional[str] = None,
    api_key: Optional[str] = None
) -> Dict[str, str]:
    """
    Generates personalized cold outreach based on live website intel.
    Supports Gemini, OpenAI, Claude, Groq, and local Ollama.
    """
    provider = provider or settings.default_llm_provider
    contact_first = contact_name.split()[0] if contact_name else "there"

    prompt = f"""
You are an elite B2B sales development representative writing a high-converting, hyper-personalized cold email.

Prospect Information:
- Contact Name: {contact_name} (First: {contact_first})
- Job Title: {job_title}
- Company: {company_name} ({company_domain})
- Live Website Summary: {company_summary}

Our Product / Value Proposition:
{value_proposition}

Instructions:
1. Subject line: 3 to 6 words, lower-case style, conversational (e.g. "quick question about {company_name}", "idea for your {company_name} team").
2. Body: Maximum 75-100 words.
3. Sentence 1: Genuine observation based on what their company actually does.
4. Sentence 2: Specific pain point related to our value proposition.
5. Sentence 3: Low-friction call to action (e.g., "Open to a quick 5-min chat next Tuesday?").
6. No cheesy flattery, no buzzwords ("revolutionary", "synergy").

Respond in valid JSON with exactly two fields:
{{"subject": "...", "body": "..."}}
"""

    # 1. Google Gemini
    if provider == "gemini":
        key = api_key or settings.gemini_api_key
        if key:
            try:
                model = settings.default_llm_model or "gemini-2.5-flash"
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"response_mime_type": "application/json"}
                }
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.post(url, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        text = data["candidates"][0]["content"]["parts"][0]["text"]
                        return json.loads(text)
            except Exception as e:
                print(f"[Copywriter] Gemini error: {e}")

    # 2. OpenAI / Groq / DeepSeek / Ollama (OpenAI-compatible)
    if provider in ["openai", "groq", "ollama"]:
        base_url = "https://api.openai.com/v1"
        model_name = "gpt-4o-mini"
        auth_header = {}

        if provider == "openai":
            key = api_key or settings.openai_api_key
            auth_header = {"Authorization": f"Bearer {key}"}
        elif provider == "groq":
            key = api_key or settings.groq_api_key
            base_url = "https://api.groq.com/openai/v1"
            model_name = "llama-3.3-70b-versatile"
            auth_header = {"Authorization": f"Bearer {key}"}
        elif provider == "ollama":
            base_url = f"{settings.ollama_base_url.rstrip('/')}/v1"
            model_name = "llama3.2"

        if (provider == "ollama") or auth_header:
            try:
                async with httpx.AsyncClient(timeout=25.0) as client:
                    resp = await client.post(
                        f"{base_url}/chat/completions",
                        headers={"Content-Type": "application/json", **auth_header},
                        json={
                            "model": model_name,
                            "messages": [{"role": "user", "content": prompt}],
                            "response_format": {"type": "json_object"} if provider != "ollama" else None
                        }
                    )
                    if resp.status_code == 200:
                        content = resp.json()["choices"][0]["message"]["content"]
                        # Extract JSON block
                        m = re.search(r"\{.*\}", content, re.DOTALL)
                        if m:
                            return json.loads(m.group(0))
            except Exception as e:
                print(f"[Copywriter] OpenAI-compatible error: {e}")

    # 3. Anthropic Claude
    if provider == "anthropic":
        key = api_key or settings.anthropic_api_key
        if key:
            try:
                async with httpx.AsyncClient(timeout=25.0) as client:
                    resp = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": key,
                            "anthropic-version": "2023-06-01",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": "claude-3-5-haiku-latest",
                            "max_tokens": 512,
                            "messages": [{"role": "user", "content": prompt}],
                        },
                    )
                    if resp.status_code == 200:
                        content = resp.json()["content"][0]["text"]
                        m = re.search(r"\{.*\}", content, re.DOTALL)
                        if m:
                            return json.loads(m.group(0))
            except Exception as e:
                print(f"[Copywriter] Anthropic error: {e}")

    # Fallback Smart Dynamic Template
    subject = f"question regarding {company_name}"
    body = (
        f"Hi {contact_first},\n\n"
        f"Saw what {company_name} is building in your space and wanted to reach out directly.\n\n"
        f"We've been helping teams like yours with {value_proposition.lower()[:80]}.\n\n"
        f"Are you open to a brief 5-minute chat next week to see if there's a fit?\n\n"
        f"Best,\n{sender_name}"
    )

    return {"subject": subject, "body": body}

