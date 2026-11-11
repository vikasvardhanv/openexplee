import json
import httpx
from bs4 import BeautifulSoup
from typing import Dict, Any, List
from app.config import settings
from app.engine.scraper import scrape_company_website


async def analyze_my_product(company_url: str) -> Dict[str, Any]:
    """
    Analyzes the user's own product/company website (e.g. https://viktron.ai),
    extracts the core offerings, and generates targeted ICP campaigns with hooks.
    """
    intel = await scrape_company_website(company_url)
    content_text = f"Title: {intel.get('title')}\nDescription: {intel.get('description')}\nHeadlines: {', '.join(intel.get('headlines', []))}\nSummary: {intel.get('content_summary', '')[:2500]}"

    prompt = f"""
You are a world-class Chief Revenue Officer & B2B Go-To-Market strategist.

Analyze our company website:
{content_text}

Extract and generate the following in valid JSON format:
1. "product_name": Clean name of the primary product (e.g. "AgentIRL™ by Viktron AI")
2. "value_proposition": A crisp 1-sentence value proposition explaining what it solves and for whom.
3. "target_icps": A list of 4-6 high-converting target market segments (Ideal Customer Profiles), each with:
   - "campaign_name": Short name (e.g. "Enterprise AI Builders", "Agentic Startups", "AI Ops Leaders")
   - "icp_query": Natural language search query to find these target companies
   - "pain_point": Specific pain point they experience that our product solves
   - "cold_pitch": A 2-sentence conversational cold email hook

Return strictly valid JSON with keys: "product_name", "value_proposition", "target_icps".
"""

    key = settings.gemini_api_key
    model = settings.default_llm_model or "gemini-2.5-flash"
    
    if key:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"response_mime_type": "application/json"}
            }
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    res_text = data["candidates"][0]["content"]["parts"][0]["text"]
                    return json.loads(res_text)
        except Exception as e:
            print(f"[Product Analyzer] Gemini error: {e}")

    # Fallback smart extraction
    return {
        "product_name": intel.get("title") or "Enterprise Platform",
        "value_proposition": intel.get("description") or "Enterprise AI agent runtime and orchestration platform.",
        "target_icps": [
            {
                "campaign_name": "Enterprise AI Builders",
                "icp_query": "Companies deploying autonomous AI agents and LLM production workflows in enterprise",
                "pain_point": "Agent policy enforcement, governance, and audit trails",
                "cold_pitch": "Saw your team is shipping autonomous LLM workflows. We built the enterprise governance runtime that prevents rogue agent actions."
            },
            {
                "campaign_name": "Agentic Startups",
                "icp_query": "Seed and Series A AI startups building multi-agent systems and developer platforms",
                "pain_point": "Multi-agent coordination and delegation security",
                "cold_pitch": "Loving what you are building with agent teams. We provide the tamper-evident runtime layer so enterprises can adopt your agents safely."
            },
            {
                "campaign_name": "AI Ops Leaders",
                "icp_query": "Heads of AI, VP Engineering, and Chief AI Officers at B2B tech companies",
                "pain_point": "Compliance, SOC 2 for AI agents, and production observability",
                "cold_pitch": "Reaching out because AI compliance is becoming the top blocker for enterprise LLM rollouts. We solve policy verification out of the box."
            }
        ]
    }

