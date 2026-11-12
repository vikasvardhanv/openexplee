from typing import Dict, Any
from app.engine.scraper import scrape_company_website
from app.engine.llm import call_llm_json


async def analyze_my_product(company_url: str) -> Dict[str, Any]:
    """
    Deep-crawls the user's company website (e.g. https://viktron.ai),
    extracts all product features & positioning, and generates targeted ICP campaigns.
    Uses Claude Code subscription / Thor Ollama / configured cloud LLM.
    """
    intel = await scrape_company_website(company_url)
    content_text = (
        f"Title: {intel.get('title')}\n"
        f"Description: {intel.get('description')}\n"
        f"Headlines: {', '.join(intel.get('headlines', []))}\n"
        f"Subpages Crawled: {', '.join(intel.get('subpages', []))}\n"
        f"Summary:\n{intel.get('content_summary', '')[:4500]}"
    )

    prompt = f"""
You are a world-class Chief Revenue Officer & B2B Go-To-Market strategist.

Analyze our company website:
{content_text}

Extract and generate the following in valid JSON format:
1. "product_name": Clean name of the primary product.
2. "value_proposition": A crisp 1-sentence value proposition explaining what it solves and for whom.
3. "key_features": List of 3-5 core capabilities extracted from the site.
4. "target_icps": A list of 4 high-converting target market segments (Ideal Customer Profiles), each with:
   - "campaign_name": Short name (e.g. "Enterprise AI Builders", "Agentic Startups", "AI Ops Leaders")
   - "icp_query": Natural language search query to find these target companies on the web
   - "pain_point": Specific pain point they experience that our product solves
   - "cold_pitch": A 2-sentence conversational cold email hook

Return strictly valid JSON with keys: "product_name", "value_proposition", "key_features", "target_icps".
"""

    parsed = await call_llm_json(prompt)
    if isinstance(parsed, dict) and parsed.get("target_icps"):
        parsed["crawled_subpages"] = intel.get("subpages", [])
        return parsed

    return {
        "product_name": intel.get("title") or "Enterprise Platform",
        "value_proposition": intel.get("description") or "Enterprise AI agent runtime and orchestration platform.",
        "key_features": intel.get("headlines", [])[:4],
        "crawled_subpages": intel.get("subpages", []),
        "target_icps": [
            {
                "campaign_name": "Enterprise AI Builders",
                "icp_query": "Companies deploying autonomous AI agents and LLM production workflows in enterprise",
                "pain_point": "Agent policy enforcement, governance, and audit trails",
                "cold_pitch": "Saw your team is shipping autonomous LLM workflows. We built the enterprise governance runtime that prevents rogue agent actions.",
            },
            {
                "campaign_name": "Agentic Startups",
                "icp_query": "Seed and Series A AI startups building multi-agent systems and developer platforms",
                "pain_point": "Multi-agent coordination and delegation security",
                "cold_pitch": "Loving what you are building with agent teams. We provide the tamper-evident runtime layer so enterprises can adopt your agents safely.",
            },
            {
                "campaign_name": "AI Ops Leaders",
                "icp_query": "Heads of AI, VP Engineering, and Chief AI Officers at B2B tech companies",
                "pain_point": "Compliance, SOC 2 for AI agents, and production observability",
                "cold_pitch": "Reaching out because AI compliance is becoming the top blocker for enterprise LLM rollouts. We solve policy verification out of the box.",
            },
        ],
    }
