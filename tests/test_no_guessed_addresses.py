"""A lead is sendable only when its address was confirmed or published. Run: python tests/test_no_guessed_addresses.py"""
import asyncio
from unittest.mock import AsyncMock, patch

from app.engine import discovery
from app.engine.verifier import enrich_person_email


def _smtp(status):
    return AsyncMock(return_value={"email": "x@acme.com", "status": status, "mx_host": "mx.acme.com", "details": ""})


async def _discover(people, found_emails, enrich_status, page="x", fit=True):
    site = {"domain": "acme.com", "url": "https://acme.com", "title": "Acme"}
    intel = {"domain": "acme.com", "url": "https://acme.com", "found_emails": found_emails, "content_summary": page}
    with patch.multiple(
        discovery,
        _get_suppressed_domains=AsyncMock(return_value=set()),
        _suggest_domains_via_llm=AsyncMock(return_value=[site]),
        _search_queries_for_icp=AsyncMock(return_value=[]),
        scrape_company_website=AsyncMock(return_value=intel),
        _extract_people_with_llm=AsyncMock(return_value=people),
        enrich_person_email=AsyncMock(return_value={"email": "jane.doe@acme.com", "status": enrich_status}),
    ), patch("app.engine.llm.call_llm_json", new=AsyncMock(return_value={"fit": fit})):
        leads = await discovery.discover_leads_for_icp("any", max_companies=1)
        return leads[0] if leads else None


async def run_checks():
    jane = [{"first_name": "Jane", "last_name": "Doe", "job_title": "CTO"}]

    # 1. A name pattern the mail server did not confirm is not sendable.
    for unconfirmed in ("risky", "catch_all"):
        with patch("app.engine.verifier.verify_email_smtp", new=_smtp(unconfirmed)):
            assert (await enrich_person_email("Maya", "Patel", "acme.com"))["status"] == "invalid"
    with patch("app.engine.verifier.verify_email_smtp", new=_smtp("verified")):
        assert (await enrich_person_email("Maya", "Patel", "acme.com"))["status"] == "verified"

    # 2. No person and no published address: the lead is saved as not sendable.
    lead = await _discover([], [], "invalid")
    assert lead["email"] == "contact@acme.com" and lead["verification_status"] == "invalid"

    # 3. A person with an unconfirmed address is not sendable.
    assert (await _discover(jane, [], "invalid"))["verification_status"] == "invalid"

    # 4. A published address is used, and the campaign step checks the mail server.
    lead = await _discover(jane, ["hello@acme.com"], "invalid")
    assert lead["email"] == "hello@acme.com" and "verification_status" not in lead

    # 5. A confirmed address is used as it is.
    lead = await _discover(jane, [], "verified")
    assert lead["email"] == "jane.doe@acme.com" and lead["verification_status"] == "verified"

    # 6. A name the model invented is dropped.
    invented = [{"first_name": "Not", "last_name": "Present", "job_title": "CEO"}, jane[0]]
    with patch("app.engine.llm.call_llm_json", new=AsyncMock(return_value=invented)):
        people = await discovery._extract_people_with_llm("acme.com", "Our CTO Jane Doe leads engineering.")
    assert [p["first_name"] for p in people] == ["Jane"]

    # 7. A website that does not load (an invented domain) gives no lead.
    assert await _discover(jane, ["hello@acme.com"], "verified", page="") is None

    # 8. A site the model reads as not a target company (directory, parked, other company) gives no lead.
    assert await _discover(jane, ["hello@acme.com"], "verified", fit=False) is None

    # 9. No model answer keeps the company.
    with patch("app.engine.llm.call_llm_json", new=AsyncMock(return_value=None)):
        assert await discovery._is_target_company("any", "Acme", "acme.com", "Acme builds rockets.") is True

    print("ALL CHECKS PASSED!")


if __name__ == "__main__":
    asyncio.run(run_checks())
