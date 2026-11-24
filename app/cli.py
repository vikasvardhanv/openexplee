import asyncio
import json
import sys
from typing import Optional
import click
from app.database import get_db_connection, init_db


def output_result(data: any, json_mode: bool):
    """Outputs data as formatted JSON or human-readable text."""
    if json_mode:
        click.echo(json.dumps(data, indent=2, default=str))
    else:
        if isinstance(data, list):
            for item in data:
                click.echo(item)
        elif isinstance(data, dict):
            for k, v in data.items():
                click.echo(f"{k}: {v}")
        else:
            click.echo(str(data))


@click.group()
def cli():
    """OpenExplee CLI — Agent-Native Outreach, Scraping & Automation Harness."""
    pass


# ----------------- CAMPAIGNS -----------------
@cli.group()
def campaigns():
    """Manage ICP outreach campaigns."""
    pass


@campaigns.command("list")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
def campaigns_list(json_mode: bool):
    """List all campaigns."""
    async def _run():
        conn = await get_db_connection()
        cur = await conn.execute("SELECT id, name, status, total_leads, sent_count FROM campaigns ORDER BY id DESC")
        rows = [dict(r) for r in await cur.fetchall()]
        await conn.close()
        return rows

    res = asyncio.run(_run())
    output_result(res, json_mode)


@campaigns.command("launch")
@click.argument("campaign_id", type=int)
@click.option("--limit", default=10, help="Max emails to dispatch in this batch")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
def campaigns_launch(campaign_id: int, limit: int, json_mode: bool):
    """Launch batch email dispatch for a campaign."""
    async def _run():
        from app.engine.sequencer import dispatch_campaign_batch
        return await dispatch_campaign_batch(campaign_id, limit=limit)

    res = asyncio.run(_run())
    output_result(res, json_mode)


# ----------------- LEADS -----------------
@cli.group()
def leads():
    """Inspect and verify target leads."""
    pass


@leads.command("list")
@click.option("--campaign-id", type=int, default=None, help="Filter by campaign ID")
@click.option("--limit", default=25, help="Limit results")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
def leads_list(campaign_id: Optional[int], limit: int, json_mode: bool):
    """List leads in database."""
    async def _run():
        conn = await get_db_connection()
        if campaign_id:
            cur = await conn.execute("SELECT id, email, contact_name, company_name, status FROM leads WHERE campaign_id = ? LIMIT ?", (campaign_id, limit))
        else:
            cur = await conn.execute("SELECT id, email, contact_name, company_name, status FROM leads LIMIT ?", (limit,))
        rows = [dict(r) for r in await cur.fetchall()]
        await conn.close()
        return rows

    res = asyncio.run(_run())
    output_result(res, json_mode)


@leads.command("verify")
@click.argument("email")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
def leads_verify(email: str, json_mode: bool):
    """Verify an email with syntax, MX, and SMTP checks."""
    async def _run():
        from app.engine.verifier import verify_email_smtp
        return await verify_email_smtp(email)

    res = asyncio.run(_run())
    output_result(res, json_mode)


@leads.command("enrich")
@click.argument("first_name")
@click.argument("last_name")
@click.argument("company_domain")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
def leads_enrich(first_name: str, last_name: str, company_domain: str, json_mode: bool):
    """Find and verify work email from name and domain."""
    async def _run():
        from app.engine.verifier import enrich_person_email
        return await enrich_person_email(first_name, last_name, company_domain)

    res = asyncio.run(_run())
    output_result(res, json_mode)


# ----------------- SCRAPE (CRAWL4AI) -----------------
@cli.command("scrape")
@click.argument("url")
@click.option("--depth", default=1, help="Subpage crawl depth (default: 1)")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
def scrape_cmd(url: str, depth: int, json_mode: bool):
    """Scrape and extract structured markdown and emails from any website using Crawl4AI."""
    async def _run():
        from app.engine.scraper import scrape_company_website
        return await scrape_company_website(url, max_depth=depth)

    res = asyncio.run(_run())
    output_result(res, json_mode)


# ----------------- EMAIL -----------------
@cli.group()
def email():
    """Send and fetch emails."""
    pass


@email.command("send")
@click.argument("to_email")
@click.option("--subject", required=True, help="Email subject line")
@click.option("--body", required=True, help="Email body text")
@click.option("--reply-to", default=None, help="Reply-To address")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
def email_send(to_email: str, subject: str, body: str, reply_to: Optional[str], json_mode: bool):
    """Send a cold outreach email via active inboxes."""
    async def _run():
        from app.engine.cli_connectors import EmailCLIConnector
        return await EmailCLIConnector.send_email(to_email, subject, body, reply_to=reply_to)

    res = asyncio.run(_run())
    output_result(res, json_mode)


@email.command("poll-replies")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
def email_poll(json_mode: bool):
    """Poll for prospect email replies."""
    async def _run():
        from app.engine.cli_connectors import EmailCLIConnector
        return await EmailCLIConnector.poll_inbound_replies()

    res = asyncio.run(_run())
    output_result(res, json_mode)


# ----------------- INBOXES -----------------
@cli.group()
def inboxes():
    """Manage connected sender inboxes."""
    pass


@inboxes.command("list")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
def inboxes_list(json_mode: bool):
    """List sender inboxes and daily quotas."""
    async def _run():
        conn = await get_db_connection()
        cur = await conn.execute("SELECT id, email, display_name, provider, daily_quota, sent_today, status, health_score FROM inboxes")
        rows = [dict(r) for r in await cur.fetchall()]
        await conn.close()
        return rows

    res = asyncio.run(_run())
    output_result(res, json_mode)


@inboxes.command("reset-today")
@click.argument("inbox_id", type=int)
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
def inboxes_reset(inbox_id: int, json_mode: bool):
    """Reset an inbox's sent_today counter to 0."""
    async def _run():
        conn = await get_db_connection()
        await conn.execute("UPDATE inboxes SET sent_today = 0 WHERE id = ?", (inbox_id,))
        await conn.commit()
        await conn.close()
        return {"ok": True, "inbox_id": inbox_id, "sent_today": 0}

    res = asyncio.run(_run())
    output_result(res, json_mode)


# ----------------- CLI-HUB (CLI-ANYTHING) -----------------
@cli.group()
def clihub():
    """Discover and execute agent-native app CLIs from CLI-Anything's Hub."""
    pass


@clihub.command("search")
@click.argument("query", default="")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
def clihub_search(query: str, json_mode: bool):
    """Search CLI-Anything Hub for apps, web services, and automation tools."""
    async def _run():
        from app.engine.cli_connectors import search_clihub
        return await search_clihub(query)

    res = asyncio.run(_run())
    output_result(res, json_mode)


@clihub.command("install")
@click.argument("tool_name")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
def clihub_install(tool_name: str, json_mode: bool):
    """Install a CLI from CLI-Anything's Hub."""
    async def _run():
        from app.engine.cli_connectors import install_clihub_tool
        return await install_clihub_tool(tool_name)

    res = asyncio.run(_run())
    output_result(res, json_mode)


@clihub.command("launch", context_settings=dict(ignore_unknown_options=True))
@click.argument("tool_name")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def clihub_launch(tool_name: str, args):
    """Launch an installed CLI tool with arguments."""
    async def _run():
        from app.engine.cli_connectors import execute_clihub_tool
        return await execute_clihub_tool(tool_name, list(args))

    res = asyncio.run(_run())
    output_result(res, True)


# ----------------- OBSCURA (STEALTH HEADLESS BROWSER) -----------------
@cli.group()
def obscura():
    """Lightweight Rust headless browser for anti-bot stealth scraping & CDP."""
    pass


@obscura.command("fetch")
@click.argument("url")
@click.option("--stealth/--no-stealth", default=True, help="Enable anti-detection stealth mode")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
def obscura_fetch_cmd(url: str, stealth: bool, json_mode: bool):
    """Fetch and scrape a website using Obscura's stealth engine."""
    async def _run():
        from app.engine.obscura import fetch_with_obscura
        return await fetch_with_obscura(url, stealth=stealth)

    res = asyncio.run(_run())
    output_result(res, json_mode)


@obscura.command("serve")
@click.option("--port", default=9222, help="CDP listening port (default: 9222)")
@click.option("--stealth/--no-stealth", default=True, help="Enable anti-detection stealth mode")
def obscura_serve_cmd(port: int, stealth: bool):
    """Start Obscura CDP server for Playwright/Crawl4AI automation."""
    from app.engine.obscura import get_obscura_bin, get_obscura_env
    import subprocess

    bin_path = get_obscura_bin()
    if not bin_path:
        click.echo("Obscura binary not found. Run ./scripts/install_obscura.sh")
        return

    cmd = [bin_path, "serve", "--port", str(port)]
    if stealth:
        cmd.append("--stealth")

    click.echo(f"Starting Obscura CDP server on port {port} (stealth={stealth})...")
    subprocess.run(cmd, env=get_obscura_env())


# ----------------- REPL -----------------
@cli.command("repl")
def repl_mode():
    """Interactive CLI-Anything agent REPL."""
    click.echo("OpenExplee CLI-Anything Agent REPL (Type 'exit' to quit)")
    while True:
        try:
            line = input("openexplee> ").strip()
            if not line or line.lower() in ("exit", "quit"):
                break
            parts = line.split()
            try:
                cli.main(args=parts, standalone_mode=False)
            except Exception as e:
                click.echo(f"Error: {e}")
        except (KeyboardInterrupt, EOFError):
            break


if __name__ == "__main__":
    cli()

