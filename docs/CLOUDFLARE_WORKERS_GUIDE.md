# Cloudflare Workers & Mailflare Setup Guide

OpenExplee supports two Cloudflare Worker integrations for email sending and reply handling:

1. **OpenExplee Cloudflare Mailer (`cloudflare-worker/`):** A lightweight Worker with a `send_email` binding and inbound reply forwarding.
2. **Mailflare Workspace (`hieunc229/mailflare`):** A full webmail, calendar, and booking workspace on Cloudflare Workers + D1 + R2.

> **Prerequisite:** Outbound sending to external recipients through Cloudflare Email Service requires a **Cloudflare Workers Paid** plan (\$5/month). Inbound Email Routing is free.

---

## Option 1: Deploy the Lightweight OpenExplee Mailer Worker

1. Open your Cloudflare dashboard and onboard your sending domain under **Compute &rarr; Email Service &rarr; Email Sending**.
2. Run the deployment script:
   ```bash
   ./scripts/deploy_cf_mailer.sh
   ```
3. Log in to Cloudflare when Wrangler opens your browser.
4. Set a strong secret token for API calls:
   ```bash
   cd cloudflare-worker
   npx wrangler secret put WORKER_AUTH_TOKEN
   ```
5. Optional: To track replies automatically as Hot Leads in OpenExplee, set your public OpenExplee webhook URL:
   ```bash
   npx wrangler secret put OPENEXPLEE_WEBHOOK_URL
   # Example: https://your-openexplee-host/api/cloudflare/inbound-webhook
   ```
6. In the Cloudflare dashboard under **Email Routing**, bind incoming addresses on your domain to the `openexplee-cf-mailer` Worker so replies forward to `vikash@viktron.ai`.
7. Open OpenExplee &rarr; **Inboxes & Domains**, choose **Cloudflare Email Worker (send_email)**, enter the deployed Worker URL and `WORKER_AUTH_TOKEN`, then click **Test SMTP**.

---

## Option 2: Deploy Full Mailflare Workspace (`hieunc229/mailflare`)

1. Create a scoped `CF_TOKEN` in **Cloudflare Dashboard &rarr; Manage Account &rarr; Account API Tokens** with:
   * Zone: `DNS:Edit`, `Zone:Read`, `Zone Settings:Edit`, `Email Routing Rules:Edit`
   * Account: `Email Sending:Edit`
2. Run the Mailflare deployment script:
   ```bash
   ./scripts/deploy_mailflare.sh
   ```
3. Open your deployed Mailflare URL at `/setup`, create your admin account, and connect your domain.
4. Create a mailbox and generate an API key with the `send` scope in **Mailflare Settings &rarr; API keys**.
5. Open OpenExplee &rarr; **Inboxes & Domains**, choose **Mailflare Workspace API (Cloudflare)**, enter your Mailflare URL, Mailbox ID, and API key, then click **Test SMTP**.
