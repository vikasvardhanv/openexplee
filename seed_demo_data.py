import asyncio
import aiosqlite
from pathlib import Path

DB_FILE = Path("data/openexplee.db")

async def seed():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("PRAGMA foreign_keys=ON;")

        # Clean existing
        await db.execute("DELETE FROM warmup_logs;")
        await db.execute("DELETE FROM leads;")
        await db.execute("DELETE FROM campaigns;")
        await db.execute("DELETE FROM inboxes;")

        # Insert Campaigns
        await db.execute("""
            INSERT INTO campaigns (id, name, icp_prompt, target_industry, status, value_proposition, total_leads, sent_count)
            VALUES 
            (1, 'US B2B SaaS Founders Q2', 'B2B SaaS companies in California using Stripe with 10-50 employees', 'Developer Tools & SaaS', 'ready', 'Automated test acceleration cutting CI/CD build times by 60%', 48, 32),
            (2, 'Fintech Infrastructure', 'Fintech startups building modern cross-border payments & billing APIs', 'Fintech', 'ready', 'Direct clearing and payout rails with sub-second finality', 36, 24),
            (3, 'AI Developer Tooling', 'Founders of open-source developer productivity tools and AI agent frameworks', 'AI & Infrastructure', 'discovering', 'Real-time agentic memory and telemetry layer', 24, 12);
        """)

        # Insert Inboxes
        await db.execute("""
            INSERT INTO inboxes (id, email, display_name, provider, smtp_host, smtp_port, smtp_user, smtp_password, daily_quota, sent_today, status, health_score)
            VALUES
            (1, 'alex@trycompany.com', 'Alex Morgan', 'google', 'smtp.gmail.com', 587, 'alex@trycompany.com', 'demo_pass_1', 35, 28, 'active', 99),
            (2, 'alex@getcompany.com', 'Alex Morgan', 'google', 'smtp.gmail.com', 587, 'alex@getcompany.com', 'demo_pass_2', 35, 24, 'active', 98),
            (3, 'outreach@companyhq.com', 'Growth Team', 'outlook', 'smtp.office365.com', 587, 'outreach@companyhq.com', 'demo_pass_3', 35, 19, 'active', 97),
            (4, 'dispatch@companyapp.io', 'Alex M.', 'hyvor', 'mail.hyvor.com', 587, 'dispatch@companyapp.io', 'demo_pass_4', 35, 13, 'active', 100);
        """)

        # Insert Leads
        leads = [
            (
                1, 'Linear', 'linear.app', 'https://linear.app',
                'Karri Saarinen', 'Karri', 'Saarinen', 'CEO & Co-founder',
                'karri@linear.app', 'verified', 'MX + SMTP Handshake confirmed (250 OK)',
                'speeding up cycle planning at Linear',
                'Hi Karri,\n\nSaw how Linear revolutionized issue tracking and workflow speed. We built a fast CI/CD runner that drops test suite execution from 14 mins to 4 mins.\n\nOpen to a brief 5-min look next Tuesday?\n\nBest,\nAlex',
                'sent', 'alex@trycompany.com'
            ),
            (
                1, 'Supabase', 'supabase.com', 'https://supabase.com',
                'Paul Copplestone', 'Paul', 'Copplestone', 'CEO & Co-founder',
                'paul@supabase.com', 'verified', 'MX records active (aspmx.l.google.com), deliverable',
                'database CI/CD test runs for Supabase',
                'Hi Paul,\n\nLove the momentum behind Supabase self-hosting and vector storage. We built automated test container acceleration that trims PR validation times in half.\n\nWorth a quick 5-min chat next week?\n\nBest,\nAlex',
                'sent', 'alex@getcompany.com'
            ),
            (
                1, 'Resend', 'resend.com', 'https://resend.com',
                'Zeno Rocha', 'Zeno', 'Rocha', 'Founder & CEO',
                'zeno@resend.com', 'verified', 'MX verified (Google Workspace), 250 deliverable',
                'email infra test suites at Resend',
                'Hi Zeno,\n\nBig fan of Resend\'s developer experience and React Email. We created a lightweight test parallelizer designed specifically for fast developer tooling teams.\n\nWould love to show you a 3-minute demo if you are open to it.\n\nBest,\nAlex',
                'sent', 'outreach@companyhq.com'
            ),
            (
                1, 'PostHog', 'posthog.com', 'https://posthog.com',
                'James Hawkins', 'James', 'Hawkins', 'CEO & Co-founder',
                'james@posthog.com', 'verified', 'Live mailbox handshake verified',
                'test acceleration for PostHog',
                'Hi James,\n\nFollow your open-source roadmap closely. We built an automated test runner that cuts CI queue bottlenecks by 65% for high-velocity repos.\n\nOpen to taking a look next week?\n\nBest,\nAlex',
                'enriched', None
            ),
            (
                1, 'Vercel', 'vercel.com', 'https://vercel.com',
                'Guillermo Rauch', 'Guillermo', 'Rauch', 'CEO',
                'rauchg@vercel.com', 'verified', 'MX confirmed deliverable',
                'frontend build & test times at Vercel',
                'Hi Guillermo,\n\nInspiring to see the Next.js ecosystem scale. We engineered a zero-overhead test runner reducing PR check times by over 60%.\n\nWould love to share the benchmark if you have 5 minutes.\n\nBest,\nAlex',
                'enriched', None
            )
        ]

        for l in leads:
            await db.execute("""
                INSERT INTO leads (
                    campaign_id, company_name, company_domain, website_url,
                    contact_name, first_name, last_name, job_title,
                    email, verification_status, verification_reason,
                    personalized_subject, personalized_body, status, sent_from_inbox
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, l)

        await db.commit()
    print("Demo data seeded successfully.")

if __name__ == "__main__":
    asyncio.run(seed())
