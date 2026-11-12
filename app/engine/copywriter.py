import random
import re
from typing import Dict, Optional
from app.config import settings
from app.engine.llm import call_llm_json


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
    sender_name: str = "Vikash",
    provider: Optional[str] = None,
    api_key: Optional[str] = None
) -> Dict[str, str]:
    """
    Generates personalized cold outreach with your VPS Cal.com booking link
    using Claude Code subscription, Thor Ollama, or configured cloud APIs.
    """
    contact_first = contact_name.split()[0] if contact_name and contact_name != "Founder / Growth Lead" else "there"
    booking_line = f"Booking link to include cleanly as a low-friction CTA: {settings.booking_url}" if settings.booking_url else ""

    prompt = f"""
You are an elite B2B sales development representative writing a high-converting, hyper-personalized cold email.

Prospect Information:
- Contact Name: {contact_name} (First: {contact_first})
- Job Title: {job_title}
- Company: {company_name} ({company_domain})
- Live Website Summary: {company_summary}

Our Product / Value Proposition:
{value_proposition}

{booking_line}

Instructions:
1. Subject line: 3 to 6 words, lowercase style, conversational (e.g. "quick question about {company_name}").
2. Body: Maximum 80-110 words.
3. Sentence 1: Genuine observation based on what their company actually does.
4. Sentence 2: Specific pain point related to our value proposition.
5. Sentence 3: Low-friction call to action inviting a 15-min chat with the booking link ({settings.booking_url}).
6. Sign off as {sender_name}. No cheesy buzzwords ("revolutionary", "synergy").

Respond strictly in valid JSON with two fields:
{{"subject": "...", "body": "..."}}
"""

    parsed = await call_llm_json(prompt, provider=provider, api_key=api_key)
    if isinstance(parsed, dict) and parsed.get("subject") and parsed.get("body"):
        return {"subject": str(parsed["subject"]), "body": str(parsed["body"])}

    # Smart Dynamic Fallback Template (includes live VPS Cal.com link)
    subject = f"quick question about {company_name}"
    cta = (
        f"Open to a quick 15-min chat next week? Grab any slot here: {settings.booking_url}"
        if settings.booking_url
        else "Are you open to a brief 5-minute chat next week to see if there's a fit?"
    )
    body = (
        f"{{Hi|Hey}} {contact_first},\n\n"
        f"Saw what {company_name} is building and wanted to reach out directly.\n\n"
        f"We've been helping teams like yours with {value_proposition[:120]}.\n\n"
        f"{cta}\n\n"
        f"Best,\n{sender_name}"
    )

    return {"subject": subject, "body": body}
