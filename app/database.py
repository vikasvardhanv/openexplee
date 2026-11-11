import aiosqlite
import os
from pathlib import Path
from typing import Dict, Any, List, Optional
from app.config import settings

DB_FILE = Path(settings.database_path)


async def init_db():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA foreign_keys=ON;")

        # Campaigns Table
        await db.execute("""
        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            icp_prompt TEXT NOT NULL,
            target_industry TEXT,
            status TEXT DEFAULT 'draft',
            value_proposition TEXT,
            reply_to_email TEXT,
            cc_email TEXT,
            total_leads INTEGER DEFAULT 0,
            sent_count INTEGER DEFAULT 0,
            hot_leads_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # Leads Table
        await db.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER REFERENCES campaigns(id) ON DELETE CASCADE,
            company_name TEXT,
            company_domain TEXT,
            website_url TEXT,
            contact_name TEXT,
            first_name TEXT,
            last_name TEXT,
            job_title TEXT,
            email TEXT,
            verification_status TEXT DEFAULT 'unverified',
            verification_reason TEXT,
            personalized_subject TEXT,
            personalized_body TEXT,
            status TEXT DEFAULT 'discovered',
            is_hot_lead BOOLEAN DEFAULT 0,
            sent_at TIMESTAMP,
            sent_from_inbox TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # Inboxes / Sending Accounts Table
        await db.execute("""
        CREATE TABLE IF NOT EXISTS inboxes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            display_name TEXT,
            provider TEXT NOT NULL,
            smtp_host TEXT NOT NULL,
            smtp_port INTEGER NOT NULL DEFAULT 587,
            smtp_user TEXT NOT NULL,
            smtp_password TEXT NOT NULL,
            reply_to TEXT,
            cc_email TEXT,
            use_tls BOOLEAN DEFAULT 1,
            daily_quota INTEGER DEFAULT 35,
            sent_today INTEGER DEFAULT 0,
            last_sent_at TIMESTAMP,
            status TEXT DEFAULT 'active',
            health_score INTEGER DEFAULT 100,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # Warmup Log Table
        await db.execute("""
        CREATE TABLE IF NOT EXISTS warmup_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inbox_id INTEGER REFERENCES inboxes(id) ON DELETE CASCADE,
            action TEXT,
            peer_address TEXT,
            status TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # App Settings Key-Value Table
        await db.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)

        # Safe Schema Migrations for existing DBs
        for table, col, col_type in [
            ("campaigns", "reply_to_email", "TEXT"),
            ("campaigns", "cc_email", "TEXT"),
            ("campaigns", "hot_leads_count", "INTEGER DEFAULT 0"),
            ("leads", "is_hot_lead", "BOOLEAN DEFAULT 0"),
            ("inboxes", "reply_to", "TEXT"),
            ("inboxes", "cc_email", "TEXT")
        ]:
            try:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type};")
            except Exception:
                pass

        await db.commit()


async def get_db_connection() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(DB_FILE)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON;")
    return conn

