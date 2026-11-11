#!/usr/bin/env bash
set -euo pipefail

MAILFLARE_DIR="${1:-/tmp/mailflare-deploy}"

echo "[1/6] Cloning hieunc229/mailflare into $MAILFLARE_DIR..."
if [ ! -d "$MAILFLARE_DIR/.git" ]; then
  git clone --depth 1 https://github.com/hieunc229/mailflare.git "$MAILFLARE_DIR"
fi

cd "$MAILFLARE_DIR"

echo "[2/6] Installing Mailflare dependencies..."
npm install

echo "[3/6] Checking Wrangler authentication..."
if ! npx wrangler whoami >/dev/null 2>&1; then
  echo "Not logged in to Cloudflare. Launching interactive login..."
  npx wrangler login
fi

echo "[4/6] Provisioning Cloudflare D1, R2, and Queues (idempotent)..."
npx wrangler d1 create mailflare || true
npx wrangler r2 bucket create mailflare-raw || true
npx wrangler queues create mailflare-inbound || true
npx wrangler queues create mailflare-outbound || true
npx wrangler queues create mailflare-agent || true

echo ""
echo "IMPORTANT: Ensure the D1 database_id from 'npx wrangler d1 list' is set in $MAILFLARE_DIR/wrangler.jsonc if Wrangler asks for it."
echo ""

echo "[5/6] Deploying Mailflare Worker..."
npm run deploy

echo "[6/6] Setting runtime CF_TOKEN secret..."
echo "Paste your scoped CF_TOKEN (DNS Edit + Email Routing Rules Edit + Email Sending Edit):"
npx wrangler secret put CF_TOKEN

echo ""
echo "Mailflare deployed! Open your deployed Worker URL at /setup to create your admin account and connect your domain."
