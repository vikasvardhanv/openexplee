import asyncio
import json
import os
import shutil
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse
from app.config import settings

CLI_HUB_BIN = shutil.which("cli-hub") or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "venv", "bin", "cli-hub")


async def run_cli_command(
    args: List[str],
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    """
    Executes a CLI command asynchronously.
    Parses output as JSON if formatted as JSON, otherwise returns clean string.
    """
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=merged_env,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        stdout_str = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr_str = stderr_bytes.decode("utf-8", errors="replace").strip()

        # Try to parse stdout as JSON if possible
        parsed_json = None
        if stdout_str:
            try:
                parsed_json = json.loads(stdout_str)
            except Exception:
                pass

        return {
            "success": proc.returncode == 0,
            "exit_code": proc.returncode,
            "command": " ".join(args),
            "data": parsed_json if parsed_json is not None else stdout_str,
            "raw_output": stdout_str,
            "error": stderr_str if proc.returncode != 0 else "",
        }
    except asyncio.TimeoutError:
        return {
            "success": False,
            "exit_code": -1,
            "command": " ".join(args),
            "data": None,
            "raw_output": "",
            "error": f"Command timed out after {timeout} seconds",
        }
    except Exception as exc:
        return {
            "success": False,
            "exit_code": -1,
            "command": " ".join(args),
            "data": None,
            "raw_output": "",
            "error": str(exc),
        }


async def search_clihub(query: str = "") -> List[Dict[str, str]]:
    """Searches CLI-Anything's CLI-Hub for matching app CLIs."""
    cmd = [CLI_HUB_BIN, "search", query] if query else [CLI_HUB_BIN, "list"]
    res = await run_cli_command(cmd, timeout=15.0)

    results: List[Dict[str, str]] = []
    lines = (res.get("raw_output") or "").splitlines()
    current_cat = "general"

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if line.startswith("  ") and not line.startswith("     "):
            current_cat = stripped.lower()
            continue
        if "[" in stripped and "]" in stripped:
            parts = stripped.split("[", 1)
            name = parts[0].strip()
            rest = parts[1].split("]", 1)
            cat = rest[0].strip() if len(rest) > 1 else current_cat
            desc = rest[1].lstrip(" —-").strip() if len(rest) > 1 else ""
            if name:
                results.append({"name": name, "category": cat, "description": desc})
        elif line.startswith("     "):
            cols = stripped.split(None, 1)
            if cols:
                name = cols[0]
                desc = cols[1] if len(cols) > 1 else ""
                results.append({"name": name, "category": current_cat, "description": desc})

    return results


async def install_clihub_tool(tool_name: str) -> Dict[str, Any]:
    """Installs a CLI tool from CLI-Anything's hub."""
    clean_name = tool_name.strip().lower()
    return await run_cli_command([CLI_HUB_BIN, "install", clean_name], timeout=120.0)


async def execute_clihub_tool(tool_name: str, tool_args: List[str]) -> Dict[str, Any]:
    """Launches an installed CLI-Anything tool with arguments."""
    clean_name = tool_name.strip()
    cmd = [CLI_HUB_BIN, "launch", clean_name, "--"] + tool_args
    return await run_cli_command(cmd, timeout=60.0)


class EmailCLIConnector:
    """Connects to email sending and fetching via CLI or native engine."""

    @staticmethod
    async def send_email(to_email: str, subject: str, body: str, reply_to: Optional[str] = None) -> Dict[str, Any]:
        from app.engine.sequencer import send_single_email, get_next_available_inbox

        inbox = await get_next_available_inbox()
        if not inbox:
            return {"success": False, "error": "No active inboxes available with daily quota remaining"}

        return await send_single_email(
            inbox=inbox,
            to_email=to_email,
            subject=subject,
            body=body,
            reply_to=reply_to,
        )

    @staticmethod
    async def poll_inbound_replies() -> Dict[str, Any]:
        from app.engine.replies import poll_replies
        return await poll_replies()


class ScraperCLIConnector:
    """Scrapes any website using Crawl4AI."""

    @staticmethod
    async def scrape(url: str, max_depth: int = 1) -> Dict[str, Any]:
        from app.engine.scraper import scrape_company_website
        return await scrape_company_website(url=url, max_depth=max_depth)

