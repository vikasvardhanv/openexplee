"""No placeholder name in a greeting, no placeholder address gets mail. Run: python tests/test_email_copy.py"""
import asyncio
from unittest.mock import AsyncMock, patch

from app.engine import copywriter, sequencer
from app.engine.copywriter import greeting_name


async def run_checks():
    for placeholder in (None, "", "Founder / Hiring Lead", "None Unknown", "none none", "Kevin Unknown"):
        assert greeting_name(placeholder) == "there", placeholder
    assert greeting_name("Jane Doe") == "Jane"

    # When the model gives no answer, the stand-in email has no spintax, no "None" and no invented claim.
    with patch.object(copywriter, "call_llm_json", new=AsyncMock(return_value=None)):
        draft = await copywriter.generate_cold_email("Acme", "acme.com", "None Unknown", "", "", "Paid internships. More.")
    assert draft["body"].startswith("Hi there,") and "{" not in draft["body"] and "helping teams" not in draft["body"]

    # An address built from a placeholder name is blocked before any mail server is contacted.
    with patch.multiple(sequencer, _is_suppressed=AsyncMock(return_value=False), _auto_suppress_bounce=AsyncMock()):
        for bad in ("none.unknown@acme.com", "kevin.unknown@acme.com", "none.none@acme.com"):
            result = await sequencer.send_single_email({"email": "me@x.test", "id": 1}, bad, "s", "b")
            assert not result["success"] and "placeholder" in result["error"], bad


if __name__ == "__main__":
    asyncio.run(run_checks())
    print("ok")
