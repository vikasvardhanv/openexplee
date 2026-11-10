# Secondary Domain Firewall & Deliverability Guide

Never send cold outbound emails from your primary company domain (`yourcompany.com`). If an outreach campaign gets flagged, it will poison your transactional emails, customer support tickets, and billing notifications.

This guide details how to build a clean **Domain Firewall** using secondary lookalike domains.

---

### Step 1: Buy 2–3 Lookalike Domains

Purchase domains related to your brand for ~$9/year each on [Cloudflare Registrar](https://www.cloudflare.com/products/registrar/) or [Porkbun](https://porkbun.com):

* Example primary domain: `acmepayments.com`
* Secondary outreach domains to buy:
  * `tryacmepayments.com`
  * `getacmepayments.com`
  * `acmepaymentshq.com`

---

### Step 2: 301 Redirect Web Traffic to Your Primary Site

When prospects receive your email, many will type your email domain into a browser. Set up a redirect so they land on your main website:

1. In Cloudflare, navigate to **Rules** &rarr; **Redirect Rules** (or Page Rules).
2. Create a rule:
   * **When:** `Incoming Request` matches `Hostname == "tryacmepayments.com"`
   * **Then:** `Dynamic Redirect` &rarr; `301 Moved Permanently` to `https://acmepayments.com`
3. Now all visitors to `tryacmepayments.com` are forwarded to your official website.

---

### Step 3: Configure DNS Deliverability Records

Add these 3 DNS TXT records to each secondary domain:

#### 1. SPF (Sender Policy Framework)
Authorizes your mail provider to send on your behalf.
* **Type:** `TXT`
* **Name:** `@` (root)
* **Value (for Google Workspace):**
  ```text
  v=spf1 include:_spf.google.com ~all
  ```
* **Value (for Microsoft 365):**
  ```text
  v=spf1 include:spf.protection.outlook.com ~all
  ```

#### 2. DKIM (DomainKeys Identified Mail)
Cryptographically signs every email.
* Generate your 2048-bit DKIM key inside your Google Workspace / M365 Admin Console.
* **Type:** `TXT`
* **Name:** `google._domainkey` (or your provider selector)
* **Value:** `v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ...`

#### 3. DMARC (Domain-based Message Authentication)
Tells recipient servers how to handle emails failing SPF/DKIM.
* **Type:** `TXT`
* **Name:** `_dmarc`
* **Value:**
  ```text
  v=DMARC1; p=none; sp=none; rua=mailto:dmarc-reports@tryacmepayments.com
  ```

---

### Step 4: Audit with OpenExplee's Built-in DNS Checker

1. Open your OpenExplee dashboard &rarr; **Inboxes & Domains**.
2. Enter your secondary domain in the **Domain Deliverability Firewall** box.
3. Click **Audit Records**.
4. OpenExplee queries the live DNS servers to ensure MX, SPF, and DMARC are all marked **Active** before you send your first cold email.

