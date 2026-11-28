"""Checks the Sellbird link, the relay answer grading, and the two send guards.
Uses in-memory and throwaway databases, never data/openexplee.db.

Run: venv/bin/python tests/test_sync_sellbird.py
"""
import asyncio
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import get_db_connection, init_db  # noqa: E402
from app.engine.sellbird_link import sync  # noqa: E402
from app.engine.sequencer import bounce_rate_too_high, dispatch_campaign_batch  # noqa: E402
from app.engine.verifier import grade_relay_answer  # noqa: E402


def check_link():
    oe, sb = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
    oe.executescript("""
        CREATE TABLE campaigns (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE suppression_list (id INTEGER PRIMARY KEY, entry_type TEXT, value TEXT UNIQUE, reason TEXT);
        CREATE TABLE leads (id INTEGER PRIMARY KEY, campaign_id INTEGER, company_name TEXT, company_domain TEXT,
            contact_name TEXT, first_name TEXT, last_name TEXT, job_title TEXT, email TEXT,
            verification_status TEXT, verification_reason TEXT, status TEXT);
        INSERT INTO campaigns VALUES (4, 'T');
        INSERT INTO suppression_list (entry_type, value, reason) VALUES
            ('email', 'bounced@a.test', 'hard_bounce: relay log'), ('domain', 'blocked.test', 'manual');
        INSERT INTO leads (campaign_id, email, status) VALUES (4, 'already@lead.test', 'sent');
    """)
    sb.executescript("""
        CREATE TABLE suppressions (id TEXT PRIMARY KEY, kind TEXT, value TEXT, reason TEXT, source TEXT DEFAULT '');
        CREATE UNIQUE INDEX uq ON suppressions(kind, value);
        CREATE TABLE prospects (first_name TEXT, last_name TEXT, title TEXT, email TEXT, company TEXT,
            email_status TEXT, status TEXT, source TEXT);
        INSERT INTO suppressions VALUES ('1', 'email', 'old@sync.test', 'openexplee_bounce_sync', '');
        INSERT INTO suppressions VALUES ('2', 'address', 'optout@sb.test', 'opted_out', 'reply');
        INSERT INTO prospects VALUES
            ('Ana', 'Good', 'CTO', 'Ana@Good.test', 'Good Co', 'verified', 'new', 'company_website'),
            ('Bo', 'Guess', 'CTO', 'bo@guess.test', 'Guess Co', 'guess', 'new', 'company_website'),
            ('Cy', 'Bad', 'CTO', 'cy@bad.test', 'Bad Co', 'invalid', 'new', 'company_website'),
            ('', '', 'General inbox', 'info@inbox.test', 'Inbox Co', 'guess', 'new', 'company_inbox'),
            ('Info', 'Team', 'CTO', 'x@placeholder.test', 'Fake Co', 'guess', 'new', 'company_website'),
            ('Di', 'Blocked', 'CTO', 'di@blocked.test', 'Blocked Co', 'verified', 'new', 'company_website'),
            ('Ed', 'Out', 'CTO', 'optout@sb.test', 'Out Co', 'verified', 'new', 'company_website'),
            ('Fi', 'Dup', 'CTO', 'already@lead.test', 'Dup Co', 'verified', 'new', 'company_website'),
            ('Gil', 'Done', 'CTO', 'gil@done.test', 'Done Co', 'verified', 'contacted', 'company_website');
    """)

    done = sync(oe, sb, campaign_id=4)

    # The old 'email' rows now use the kind Sellbird looks up
    assert done["repaired"] == 1
    assert not sb.execute("SELECT 1 FROM suppressions WHERE kind = 'email'").fetchone()
    # OpenExplee's list reaches Sellbird with a reason and kind Sellbird accepts
    assert sb.execute("SELECT reason FROM suppressions WHERE kind='address' AND value='bounced@a.test'").fetchone() == ("bounced",)
    assert sb.execute("SELECT 1 FROM suppressions WHERE kind='domain' AND value='blocked.test'").fetchone()
    # Sellbird's opt-out reaches OpenExplee, and nothing is echoed back
    assert oe.execute("SELECT reason FROM suppression_list WHERE value='optout@sb.test'").fetchone() == ("sellbird: opted_out",)
    assert not oe.execute("SELECT 1 FROM suppression_list WHERE value='old@sync.test'").fetchone()
    # Named, new, unblocked people come over, guessed or not, and none can be sent yet
    imported = oe.execute(
        "SELECT email, verification_status, status FROM leads WHERE verification_reason IS NOT NULL ORDER BY email").fetchall()
    assert imported == [("ana@good.test", "unverified", "discovered"),
                        ("bo@guess.test", "unverified", "discovered")], imported
    # A second run changes nothing
    again = sync(oe, sb, campaign_id=4)
    assert again == {"repaired": 0, "to_sellbird": 0, "to_openexplee": 0, "leads_imported": 0}, again


def check_grading():
    assert grade_relay_answer({"mail": 250, "rcpt": 250, "catchall": 550})["status"] == "verified"
    assert grade_relay_answer({"mail": 250, "rcpt": 250, "catchall": 250})["status"] == "catch_all"
    assert grade_relay_answer({"mail": 250, "rcpt": 550, "catchall": 0})["status"] == "invalid"
    for unsure in ({"mail": 250, "rcpt": 451, "catchall": 0}, {"mail": 550, "rcpt": 0, "catchall": 0},
                   {"error": "TimeoutError"}):
        graded = grade_relay_answer(unsure)
        # 'risky' without the retry words, so the scheduler does not ask again on every tick
        assert graded["status"] == "risky" and "not checked" not in graded["details"], graded


async def check_send_guards():
    await init_db()
    conn = await get_db_connection()
    await conn.execute("INSERT INTO campaigns (id, name, icp_prompt) VALUES (1, 'T', 'x')")
    await conn.execute(
        "INSERT INTO leads (campaign_id, email, status, verification_status, personalized_subject, personalized_body) "
        "VALUES (1, 'nocopy@lead.test', 'discovered', 'verified', '', NULL)")
    await conn.commit()
    # A confirmed lead with no written email is not picked up, so no placeholder goes out
    res = await dispatch_campaign_batch(1, limit=5)
    assert res["dispatched"] == 0 and "No queued or verified leads" in res["errors"][0], res

    assert await bounce_rate_too_high() is None  # too few sends to judge
    for i in range(20):
        await conn.execute(
            "INSERT INTO leads (campaign_id, email, status, sent_at) VALUES (1, ?, ?, datetime('now', '-1 day'))",
            (f"s{i}@lead.test", "bounced" if i < 5 else "sent"))
    await conn.commit()
    await conn.close()
    held = await bounce_rate_too_high()
    assert held and "5 of the last 20" in held, held
    res = await dispatch_campaign_batch(1, limit=5)
    assert res == {"dispatched": 0, "errors": [held]}, res


if __name__ == "__main__":
    check_link()
    check_grading()
    asyncio.run(check_send_guards())
    print("ALL SYNC CHECKS PASSED!")
