import json
from fastapi import APIRouter, Request
from typing import Dict, Any, List
from app.database import get_db_connection
from app.engine.discovery import (
    discover_leads_for_icp,
    search_companies_by_domains,
    search_people_by_domains,
)
from app.engine.verifier import enrich_person_email, verify_email_smtp
from app.engine.warmup import check_domain_dns_health
from app.engine.sequencer import dispatch_campaign_batch

router = APIRouter(tags=["mcp"])

MCP_TOOLS = [
    {
        "name": "search_companies",
        "description": "Discover B2B companies from a natural-language ICP description via web search and live scraping ($0 cost).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "definition": {"type": "string", "description": "Target company description"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["definition"],
        },
    },
    {
        "name": "search_companies_by_domains",
        "description": "Enrich company profiles from a list of domains.",
        "inputSchema": {
            "type": "object",
            "properties": {"domains": {"type": "array", "items": {"type": "string"}}},
            "required": ["domains"],
        },
    },
    {
        "name": "search_people_by_domains",
        "description": "Find decision-makers at target domains and enrich their verified work emails.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "domains": {"type": "array", "items": {"type": "string"}},
                "job_titles": {"type": "array", "items": {"type": "string"}},
                "people_per_company": {"type": "integer", "default": 2},
            },
            "required": ["domains"],
        },
    },
    {
        "name": "enrich_email",
        "description": "Find a verified corporate email from first_name, last_name, and company_domain via DNS MX + live SMTP handshake.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "company_domain": {"type": "string"},
            },
            "required": ["first_name", "last_name", "company_domain"],
        },
    },
    {
        "name": "verify_email",
        "description": "Run 4-tier verification (syntax, disposable, DNS MX, SMTP handshake) on an email address.",
        "inputSchema": {
            "type": "object",
            "properties": {"email": {"type": "string"}},
            "required": ["email"],
        },
    },
    {
        "name": "add_suppression",
        "description": "Add an email or company domain to the suppression/dedup list so OpenExplee never contacts it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_type": {"type": "string", "enum": ["email", "domain"], "default": "domain"},
                "value": {"type": "string"},
                "reason": {"type": "string", "default": "mcp_dedup"},
            },
            "required": ["value"],
        },
    },
    {
        "name": "list_suppression",
        "description": "List all suppressed emails and domains.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "check_domain_health",
        "description": "Audit MX, SPF, and DMARC records on a sending domain before outreach.",
        "inputSchema": {
            "type": "object",
            "properties": {"domain": {"type": "string"}},
            "required": ["domain"],
        },
    },
    {
        "name": "dispatch_campaign",
        "description": "Send a queued batch of verified emails for a campaign across rotating safe inboxes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "integer"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["campaign_id"],
        },
    },
]


async def _execute_tool(name: str, args: Dict[str, Any]) -> Any:
    if name == "search_companies":
        return await discover_leads_for_icp(args["definition"], max_companies=int(args.get("limit", 5)))
    if name == "search_companies_by_domains":
        return await search_companies_by_domains(args["domains"])
    if name == "search_people_by_domains":
        return await search_people_by_domains(
            args["domains"],
            job_titles=args.get("job_titles"),
            people_per_company=int(args.get("people_per_company", 2)),
        )
    if name == "enrich_email":
        return await enrich_person_email(args["first_name"], args["last_name"], args["company_domain"])
    if name == "verify_email":
        return await verify_email_smtp(args["email"])
    if name == "add_suppression":
        val = args["value"].strip().lower().lstrip("@")
        entry_type = args.get("entry_type", "domain")
        conn = await get_db_connection()
        await conn.execute(
            "INSERT OR IGNORE INTO suppression_list (entry_type, value, reason) VALUES (?, ?, ?)",
            (entry_type, val, args.get("reason", "mcp_dedup")),
        )
        await conn.commit()
        await conn.close()
        return {"ok": True, "entry_type": entry_type, "value": val}
    if name == "list_suppression":
        conn = await get_db_connection()
        cur = await conn.execute("SELECT * FROM suppression_list ORDER BY id DESC")
        rows = [dict(r) for r in await cur.fetchall()]
        await conn.close()
        return rows
    if name == "check_domain_health":
        return await check_domain_dns_health(args["domain"])
    if name == "dispatch_campaign":
        return await dispatch_campaign_batch(int(args["campaign_id"]), limit=int(args.get("limit", 10)))
    raise ValueError(f"Unknown tool: {name}")


@router.post("/mcp")
async def mcp_endpoint(request: Request):
    """Streamable HTTP JSON-RPC 2.0 MCP endpoint replacing paid Explee MCP."""
    body = await request.json()
    req_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "openexplee-mcp", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            },
        }

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS}}

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}
        try:
            output = await _execute_tool(tool_name, tool_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(output, indent=2, default=str)}]},
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True},
            }

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
