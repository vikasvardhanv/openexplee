import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from app.database import init_db, get_db_connection
from app.api.settings import update_settings, load_persisted_settings, SettingsUpdate
from app.api.cloudflare import handle_inbound_reply, InboundWebhookPayload
from app.config import settings
from app.engine.sequencer import send_single_email


async def run_checks():
    await init_db()

    # 1. Verify settings persist to SQLite and reload cleanly
    await update_settings(SettingsUpdate(default_llm_provider="anthropic", anthropic_api_key="sk-ant-test123456"))
    settings.default_llm_provider = "gemini"
    settings.anthropic_api_key = None
    await load_persisted_settings()
    assert settings.default_llm_provider == "anthropic", f"Expected anthropic, got {settings.default_llm_provider}"
    assert settings.anthropic_api_key == "sk-ant-test123456"

    # Clean test setting
    conn = await get_db_connection()
    await conn.execute("DELETE FROM app_settings WHERE key IN ('default_llm_provider', 'anthropic_api_key')")
    await conn.commit()
    await conn.close()
    settings.default_llm_provider = "gemini"
    settings.anthropic_api_key = None

    # 2. Verify cf_worker & mailflare dispatch paths with mocked HTTP transport
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
        assert res1["success"] is True, f"CF Worker dispatch failed: {res1}"
        call_kwargs = mock_client.__aenter__.return_value.post.call_args.kwargs
        assert call_kwargs["json"]["cc"] == "vikash@viktron.ai"
        assert call_kwargs["json"]["replyTo"] == "vikash@viktron.ai"

        mf_inbox = {
            "id": 100, "provider": "mailflare", "email": "ck@aspendash.com",
            "display_name": "Cristy Krall", "smtp_host": "https://mailflare.example.workers.dev",
            "smtp_user": "mbx_123", "smtp_password": "mf-api-key",
            "cc_email": "vikash@viktron.ai", "reply_to": "vikash@viktron.ai",
        }
        res2 = await send_single_email(mf_inbox, "galen@demoya.com", "Re: Intro", "Hello!", lead_id=None)
        assert res2["success"] is True, f"Mailflare dispatch failed: {res2}"
        mf_kwargs = mock_client.__aenter__.return_value.post.call_args.kwargs
        assert mf_kwargs["json"]["mailboxId"] == "mbx_123"
        assert rec_mock.await_count == 2

    # 3. Verify inbound webhook handler parses sender cleanly
    wb_res = await handle_inbound_reply(
        InboundWebhookPayload(from_email="Nobody <nobody_test_check@invalid.local>", to_email="ck@aspendash.com", subject="Re: Hi")
    )
    assert wb_res["ok"] is True and wb_res["sender"] == "nobody_test_check@invalid.local"

    print("ALL CHECKS PASSED!")


if __name__ == "__main__":
    asyncio.run(run_checks())
