# Inbox Setup & SMTP Authentication Guide

OpenExplee rotates across multiple sending accounts to keep daily volume per inbox low (30–40/day) and maximize inbox deliverability.

---

### Option 1: Google Workspace (Recommended for Deliverability)

Google Workspace accounts provide high inbox placement with corporate spam filters.

#### Steps to Generate an App Password:
1. Log into your Google Workspace account (e.g. `alex@tryacmepayments.com`).
2. Go to **Google Account Settings** &rarr; **Security**.
3. Under **How you sign in to Google**, ensure **2-Step Verification** is turned **ON**.
4. Search for or click **App passwords** (or visit [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)).
5. Name the app: `OpenExplee`.
6. Click **Create** &rarr; copy the 16-character generated code (e.g., `abcd efgh ijkl mnop`).
7. In OpenExplee, go to **Inboxes & Domains** &rarr; **Connect Sending Inbox**:
   * **Provider:** Google Workspace
   * **Email:** `alex@tryacmepayments.com`
   * **SMTP Username:** `alex@tryacmepayments.com`
   * **Password:** Enter the 16-character App Password (without spaces)
   * **Daily Quota:** `35`
8. Click **Add & Authenticate**, then click **Test SMTP** to verify connection.

---

### Option 2: Microsoft 365 (Exchange Online)

1. Open **Microsoft 365 Admin Center**.
2. Select your user &rarr; **Mail** &rarr; **Manage email apps**.
3. Make sure **Authenticated SMTP** is checked.
4. In OpenExplee:
   * **Provider:** Microsoft 365
   * **Host:** `smtp.office365.com`
   * **Port:** `587`
   * **Username:** `alex@tryacmepayments.com`
   * **Password:** Your M365 account password (or App Password if MFA is enabled)

---

### Option 3: Hyvor Relay (Self-Hosted / Open-Source Engine)

If you self-host [Hyvor Relay](https://github.com/hyvor/relay) for full data sovereignty:
1. In your Hyvor Relay admin dashboard, verify your sending domain (`tryacmepayments.com`).
2. Generate an API / SMTP credential key.
3. In OpenExplee:
   * **Provider:** Hyvor Relay (or Custom SMTP)
   * **SMTP Host:** `your-relay-host.com` (or `mail.hyvor.com`)
   * **SMTP Port:** `587`
   * **Username:** Your Hyvor Relay SMTP User
   * **Password:** Your Hyvor Relay SMTP Key

---

### The Low-Volume Rotation Rule

To send 300 emails/day safely:
* **DO NOT:** Send 300 emails from 1 inbox.
* **DO:** Connect 8–10 inboxes across 2–3 secondary domains, capped at 30–35 emails/day each.
OpenExplee automatically rotates across your inboxes using a round-robin scheduler with randomized time delays.
