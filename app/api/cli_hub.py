from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from app.engine.cli_connectors import (
    search_clihub,
    install_clihub_tool,
    execute_clihub_tool,
    run_cli_command,
    EmailCLIConnector,
    ScraperCLIConnector,
)

router = APIRouter(prefix="/api/cli", tags=["cli-anything"])


class InstallToolRequest(BaseModel):
    tool_name: str


class ExecuteToolRequest(BaseModel):
    tool_name: str
    args: List[str] = []


class GenericCommandRequest(BaseModel):
    command: List[str]
    timeout: Optional[float] = 30.0


class SendEmailCliRequest(BaseModel):
    to_email: str
    subject: str
    body: str
    reply_to: Optional[str] = None


@router.get("/tools")
async def list_available_tools(query: Optional[str] = ""):
    """Discovers available agent-native CLI tools from CLI-Anything's CLI-Hub."""
    tools = await search_clihub(query=query or "")
    return {"count": len(tools), "tools": tools}


@router.post("/install")
async def install_tool_endpoint(req: InstallToolRequest):
    """Installs a CLI tool from CLI-Anything's Hub."""
    if not req.tool_name:
        raise HTTPException(status_code=400, detail="tool_name is required")
    res = await install_clihub_tool(req.tool_name)
    return res


@router.post("/execute")
async def execute_tool_endpoint(req: ExecuteToolRequest):
    """Executes an installed CLI-Anything tool with arguments."""
    if not req.tool_name:
        raise HTTPException(status_code=400, detail="tool_name is required")
    return await execute_clihub_tool(req.tool_name, req.args)


@router.post("/run-command")
async def run_arbitrary_cli_endpoint(req: GenericCommandRequest):
    """Executes a CLI command safely and returns structured output."""
    if not req.command:
        raise HTTPException(status_code=400, detail="command is required")
    return await run_cli_command(req.command, timeout=req.timeout or 30.0)


@router.post("/email/send")
async def cli_send_email_endpoint(req: SendEmailCliRequest):
    """Sends an email through OpenExplee's CLI email bridge."""
    return await EmailCLIConnector.send_email(
        to_email=req.to_email,
        subject=req.subject,
        body=req.body,
        reply_to=req.reply_to,
    )


@router.post("/email/poll")
async def cli_poll_replies_endpoint():
    """Polls inbound replies via the IMAP/CLI connector."""
    return await EmailCLIConnector.poll_inbound_replies()


@router.post("/scrape")
async def cli_scrape_endpoint(url: str, max_depth: int = 1):
    """Scrapes any URL using Crawl4AI."""
    return await ScraperCLIConnector.scrape(url=url, max_depth=max_depth)


class ObscuraFetchRequest(BaseModel):
    url: str
    stealth: bool = True
    timeout: Optional[float] = 25.0


@router.post("/obscura/fetch")
async def cli_obscura_fetch_endpoint(req: ObscuraFetchRequest):
    """Scrapes any URL using Obscura's Rust anti-fingerprint stealth engine."""
    from app.engine.obscura import fetch_with_obscura
    return await fetch_with_obscura(req.url, stealth=req.stealth, timeout=req.timeout or 25.0)


