"""Links Sellbird (finds prospects) to OpenExplee (the only sender).

1. Do-not-email lists are copied both ways, so an address that bounced or opted out
   in one tool is never emailed by the other.
2. With a campaign id, Sellbird's named prospects become leads in that OpenExplee campaign.
   They arrive as 'unverified'. OpenExplee asks the mail server about each mailbox and
   writes the email before anything is sent, so Sellbird's guessed addresses are safe to import.

The scheduler runs this on every tick. By hand, it is a dry run unless --apply is given:

    venv/bin/python -m app.engine.sellbird_link                       # show what would change
    venv/bin/python -m app.engine.sellbird_link --campaign 4 --apply
"""
import argparse
import sqlite3
import uuid
from pathlib import Path
from typing import Dict, Optional

from app.config import settings
from app.engine.verifier import is_valid_person_name

SOURCE = "openexplee"


def _sellbird_reason(reason: str) -> str:
    """OpenExplee's free-text reason, as one of the reasons Sellbird accepts."""
    r = (reason or "").lower()
    if "bounce" in r:
        return "bounced"
    if "complaint" in r:
        return "complaint"
    if "opt" in r or "unsubscribe" in r:
        return "opted_out"
    return "imported"


def sync(oe: sqlite3.Connection, sb: sqlite3.Connection, campaign_id: Optional[int] = None) -> Dict[str, int]:
    """Runs the sync inside the two open connections. The caller commits or rolls back."""
    done = {"repaired": 0, "to_sellbird": 0, "to_openexplee": 0, "leads_imported": 0}

    # An earlier sync stored addresses as kind 'email'. Sellbird only looks up 'address'
    # and 'domain', so those rows blocked nothing.
    done["repaired"] = sb.execute("UPDATE OR IGNORE suppressions SET kind = 'address' WHERE kind = 'email'").rowcount
    sb.execute("DELETE FROM suppressions WHERE kind = 'email'")

    for entry_type, value, reason in oe.execute("SELECT entry_type, value, reason FROM suppression_list").fetchall():
        kind = "domain" if entry_type == "domain" else "address"
        done["to_sellbird"] += sb.execute(
            "INSERT OR IGNORE INTO suppressions (id, kind, value, reason, source) VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, kind, value.strip().lower(), _sellbird_reason(reason), SOURCE),
        ).rowcount

    for kind, value, reason in sb.execute(
        "SELECT kind, value, reason FROM suppressions WHERE source != ? AND reason != 'openexplee_bounce_sync'",
        (SOURCE,),
    ).fetchall():
        done["to_openexplee"] += oe.execute(
            "INSERT OR IGNORE INTO suppression_list (entry_type, value, reason) VALUES (?, ?, ?)",
            ("domain" if kind == "domain" else "email", value, f"sellbird: {reason}"),
        ).rowcount

    if not campaign_id:
        return done
    if not oe.execute("SELECT 1 FROM campaigns WHERE id = ?", (campaign_id,)).fetchone():
        raise ValueError(f"OpenExplee has no campaign {campaign_id}")

    # Named people only. A general inbox (info@, support@) is not a person to write to,
    # and Sellbird's 'invalid' addresses stay behind.
    prospects = sb.execute(
        "SELECT first_name, last_name, title, email, company FROM prospects "
        "WHERE email != '' AND email_status != 'invalid' AND status = 'new' AND source != 'company_inbox'"
    ).fetchall()
    for first, last, title, email, company in prospects:
        if not is_valid_person_name(first, last):
            continue
        email = email.strip().lower()
        domain = email.split("@")[-1]
        blocked = oe.execute(
            "SELECT 1 FROM suppression_list WHERE LOWER(value) IN (?, ?) "
            "UNION SELECT 1 FROM leads WHERE LOWER(email) = ?",
            (email, domain, email),
        ).fetchone()
        if blocked:
            continue
        oe.execute(
            "INSERT INTO leads (campaign_id, company_name, company_domain, contact_name, first_name, "
            "last_name, job_title, email, verification_status, verification_reason, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unverified', 'imported from Sellbird', 'discovered')",
            (campaign_id, company or domain, domain, f"{first} {last}".strip(), first, last, title, email),
        )
        done["leads_imported"] += 1
    return done


def run_sync(campaign_id: Optional[int] = None, apply: bool = False) -> Optional[Dict[str, int]]:
    """Opens both databases and syncs them. None when Sellbird is not installed."""
    sellbird_db = Path(settings.sellbird_db_path).expanduser()
    if not sellbird_db.exists():
        return None
    oe, sb = sqlite3.connect(settings.database_path, timeout=10), sqlite3.connect(sellbird_db, timeout=10)
    try:
        done = sync(oe, sb, campaign_id)
        if apply:
            sb.commit()
            oe.commit()
        return done
    finally:
        oe.close()  # closing without a commit drops a dry run
        sb.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--campaign", type=int, default=settings.sellbird_campaign_id or None,
                        help="OpenExplee campaign id that receives Sellbird prospects")
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args()
    done = run_sync(args.campaign, args.apply)
    if done is None:
        raise SystemExit(f"No Sellbird database at {settings.sellbird_db_path}")
    print(("APPLIED" if args.apply else "DRY RUN (add --apply to write)") + ":")
    print(f"  Sellbird do-not-email rows repaired:    {done['repaired']}")
    print(f"  do-not-email copied to Sellbird:        {done['to_sellbird']}")
    print(f"  do-not-email copied to OpenExplee:      {done['to_openexplee']}")
    print(f"  prospects imported as OpenExplee leads: {done['leads_imported']}")
