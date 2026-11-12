import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from app.database import init_db, get_db_connection
from app.api.settings import update_settings, load_persisted_settings, SettingsUpdate
from app.api.cloudflare import handle_inbound_reply, InboundWebhookPayload
from app.api.accounts import create_inbox, InboxCreate
from app.api.mcp import MCP_TOOLS
from app.config import settings
from app.engine.sequencer import send_single_email
from app.engine.verifier import enrich_person_email
from app.engine.scheduler import reset_daily_inbox_quotas, process_due_followups
from app.engine.llm import _extract_json_blob
from app.engine.copywriter import generate_cold_email


async def run_checks():
    await init_db()

    # 1. Settings persistence across restarts
    await update_settings(SettingsUpdate(default_llm_provider="anthropic", anthropic_api_key="sk-ant-test123456"))
    settings.default_llm_provider = "gemini"
    settings.anthropic_api_key = None
    await load_persisted_settings()
    assert settings.default_llm_provider == "anthropic"
    assert settings.anthropic_api_key == "sk-ant-test123456"

    conn = await get_db_connection()
    await conn.execute("DELETE FROM app_settings WHERE key IN ('default_llm_provider', 'anthropic_api_key')")
    await conn.commit()
    await conn.close()
    settings.default_llm_provider = "gemini"
    settings.anthropic_api_key = None

    # 2. Primary Domain Shield blocks sending from viktron.ai directly
    shield_blocked = False
    try:
        await create_inbox(
            InboxCreate(
                email="vikash@viktron.ai", display_name="Vikash",
                provider="google", smtp_user="vikash@viktron.ai", smtp_password="x",
            )
        )
    except HTTPException as exc:
        shield_blocked = exc.status_code == 400 and "protected" in exc.detail.lower()
    assert shield_blocked, "Expected primary domain viktron.ai to be protected!"

    # 3. Suppression list blocks sending even if queued
    conn = await get_db_connection()
    await conn.execute(
        "INSERT OR IGNORE INTO suppression_list (entry_type, value, reason) VALUES ('email', 'blocked@test.invalid', 'test')"
    )
    await conn.commit()
    await conn.close()

    sup_res = await send_single_email(
        {"id": 1, "email": "ck@aspendash.com", "provider": "cf_worker", "smtp_host": "https://x.workers.dev", "smtp_password": "t"},
        "blocked@test.invalid", "Hi", "Body",
    )
    assert sup_res["success"] is False and "Suppressed" in sup_res["error"]

    conn = await get_db_connection()
    await conn.execute("DELETE FROM suppression_list WHERE value = 'blocked@test.invalid'")
    await conn.commit()
    await conn.close()

    # 4. cf_worker & mailflare dispatch payloads
    mock_resp = MagicMock(status_code=200, text='{"ok":true}')
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)

    with patch("app.engine.sequencer.httpx.AsyncClient", return_value=mock_client), \
         patch("app.engine.sequencer._record_send_success", new=AsyncMock()) as rec_mock:
        cf_inbox = {
            "id": 99, "provider": "cf_worker", "email": "ck@aspendash.com",
            "display_name": "Cristy Krall", "smtp_host": "https://mailer.example.workers.dev",
            "smtp_user": "cf", "smtp_password": "secret-token",
            "cc_email": "vikash@viktron.ai", "reply_to": "vikash@viktron.ai",
        }
        res1 = await send_single_email(cf_inbox, "galen@demoya.com", "Re: Intro", "Hello!", lead_id=None)
        assert res1["success"] is True

        mf_inbox = {
            "id": 100, "provider": "mailflare", "email": "ck@aspendash.com",
            "display_name": "Cristy Krall", "smtp_host": "https://mailflare.example.workers.dev",
            "smtp_user": "mbx_123", "smtp_password": "mf-api-key",
            "cc_email": "vikash@viktron.ai", "reply_to": "vikash@viktron.ai",
        }
        res2 = await send_single_email(mf_inbox, "galen@demoya.com", "Re: Intro", "Hello!", lead_id=None)
        assert res2["success"] is True and rec_mock.await_count == 2

    # 5. enrich_person_email stops fast on nonexistent domain + MCP tools registered
    enrich_res = await enrich_person_email("John", "Doe", "nonexistent-domain-xyz-99999.invalid")
    assert enrich_res["status"] == "invalid"
    assert len(MCP_TOOLS) == 9

    # 6. Inbound webhook handler
    wb_res = await handle_inbound_reply(
        InboundWebhookPayload(from_email="Nobody <nobody_test_check@invalid.local>", to_email="ck@aspendash.com", subject="Re: Hi")
    )
    assert wb_res["ok"] is True and wb_res["sender"] == "nobody_test_check@invalid.local"

    # 7. Scheduler quota reset & due follow-ups check
    reset_count = await reset_daily_inbox_quotas()
    assert isinstance(reset_count, int)
    due_count = await process_due_followups(limit=0)
    assert due_count == 0

    # 8. LLM JSON parser & Cal.com booking CTA injection
    parsed_blob = _extract_json_blob("Here is JSON:\n```json\n{\"ok\": true}\n```")
    assert parsed_blob == {"ok": True}
    with patch("app.engine.copywriter.call_llm_json", new=AsyncMock(return_value=None)):
        draft = await generate_cold_email("Acme", "acme.com", "Jane Doe", "CTO", "AI workflows", "Agent governance")
        assert "https://cal.viktron.ai/vikash/15min" in draft["body"]

    print("ALL CHECKS PASSED!")


if __name__ == "__main__":
    asyncio.run(run_checks())
