# Self-Hosted Hyvor Relay (`vikasvardhanv/relay`) VPS Guide

Use Hyvor Relay on a \$5–\$6/month Linux VPS when you want unlimited outbound sending across multiple secondary domains without Google, Resend, SES, or Cloudflare rate limits or bans.

---

## 1. VPS Requirements

1. Rent an Ubuntu 24.04 VPS (\$5–\$6/month) from a provider that allows outbound TCP port 25 (or opens it on request, such as Hetzner, Vultr, or Contabo).
2. Make sure the VPS provider sets a **PTR (Reverse DNS)** record on your VPS IP pointing to your Relay hostname (for example, `relay.tryviktron.ai`).

---

## 2. Start Relay with Docker Compose

1. Copy `infra/relay/` to your VPS.
2. Create the `.env` file and set secrets:
   ```bash
   cd infra/relay
   cp .env.example .env
   # Generate APP_SECRET with: openssl rand -hex 32
   ```
3. Start Postgres and Relay:
   ```bash
   docker compose up -d
   ```
4. Open `WEB_URL` in your browser, log in via OIDC, create a **Project** (`Distributional` or `Transactional`), add your secondary domain (e.g., `tryviktron.ai`), and publish the DKIM/SPF/DMARC records shown by Relay.
5. Generate an **API Key** inside your Relay Project.

---

## 3. Connect Relay to OpenExplee

1. Open OpenExplee (`http://localhost:8000`) &rarr; **Inboxes & Domains**.
2. Select **Provider:** `Hyvor Relay (Self-Hosted SMTP)`.
3. Fill in:
   * **Email Address:** `ck@tryviktron.ai` (your secondary domain — never `viktron.ai`)
   * **Sender Name:** `Cristy Krall`
   * **SMTP Host:** `relay.tryviktron.ai`
   * **SMTP Port:** `587`
   * **SMTP Username:** `relay` (any non-empty string; Relay authenticates using the password token)
   * **Password:** Paste your **Hyvor Relay API Key**
   * **Reply-To Address:** `vikash@viktron.ai`
   * **Master Notification CC Email:** `vikash@viktron.ai`
4. Click **Add & Authenticate Inbox**, then click **Test SMTP**.
