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


def greeting_name(contact_name: Optional[str]) -> str:
    """The first name to greet, or "there" when discovery stored a placeholder and not a person."""
    words = (contact_name or "").split()
    if not words or "/" in contact_name or any(w.lower() in ("none", "unknown", "null") for w in words):
        return "there"
    return words[0]


async def generate_cold_email(
    company_name: str,
    company_domain: str,
    contact_name: str,
    job_title: str,
    company_summary: str,
    value_proposition: str,
    sender_name: str = "Vikash",
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    booking_url: Optional[str] = None,
) -> Dict[str, str]:
    """
    Generates personalized cold outreach with your VPS Cal.com booking link
    using Claude Code subscription, Thor Ollama, or configured cloud APIs.
    """
    contact_first = greeting_name(contact_name)
    # A campaign's own link wins; an empty string means this campaign sends no link.
    link = settings.booking_url if booking_url is None else booking_url
    booking_line = f"Booking link to include cleanly as a low-friction CTA: {link}" if link else ""
    cta_rule = f"inviting a 15-min chat with the booking link ({link})" if link else "asking if they are open to a short chat"

    prompt = f"""
You write one short, plain cold email. Every statement in it must be true.

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
3. Paragraph 1: one fact about their company, taken only from the website summary. No praise and no flattery.
4. Paragraph 2: what our product does, taken only from the value proposition above. Do not offer any other service.
5. Paragraph 3: Low-friction call to action {cta_rule}.
6. Sign off as {sender_name}. No cheesy buzzwords ("revolutionary", "synergy").
7. Layout: a greeting line ("Hi {contact_first},"), then short paragraphs with a blank line (\\n\\n) between them, then the sign-off name on its own line. Never one block of text.
8. The website summary can hold page noise: cookie banners, browser warnings, error pages, login prompts. Never mention those. If the summary says nothing real about the company, write one plain sentence about what the company does from its name and domain.
9. Never invent numbers, revenue, customers, job openings, roles or achievements, for them or for us. If a fact is not written above, leave it out.
10. Do not guess the reader's role. If the first name is "there", write to the team and ask who handles this.

Respond strictly in valid JSON with two fields:
{{"subject": "...", "body": "..."}}
"""

    parsed = await call_llm_json(prompt, provider=provider, api_key=api_key)
    if isinstance(parsed, dict) and parsed.get("subject") and parsed.get("body"):
        return {"subject": str(parsed["subject"]), "body": str(parsed["body"])}

    # Smart Dynamic Fallback Template (includes live VPS Cal.com link)
    subject = f"quick question about {company_name}"
    cta = (
        f"Open to a quick 15-min chat next week? Grab any slot here: {link}"
        if link
        else "Are you open to a brief 5-minute chat next week to see if there's a fit?"
    )
    body = (
        f"Hi {contact_first},\n\n"
        f"I am writing to {company_name} about {value_proposition.split(". ")[0].rstrip(".")}.\n\n"
        f"If someone else handles this, please pass this on.\n\n"
        f"{cta}\n\n"
        f"Best,\n{sender_name}"
    )

    return {"subject": subject, "body": body}
