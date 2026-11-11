#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WORKER_DIR="$ROOT_DIR/cloudflare-worker"

cd "$WORKER_DIR"

if [ ! -d "node_modules" ]; then
  echo "[1/4] Installing Cloudflare Worker dependencies..."
  npm install
fi

echo "[2/4] Typechecking Worker..."
npm run typecheck

echo "[3/4] Checking Wrangler authentication..."
if ! npx wrangler whoami >/dev/null 2>&1; then
  echo "Not logged in to Cloudflare. Launching interactive login..."
  npx wrangler login
fi

echo "[4/4] Deploying openexplee-cf-mailer Worker..."
npx wrangler deploy

echo ""
echo "Next step: Set your Worker secrets (only needed once):"
echo "  cd $WORKER_DIR"
echo "  npx wrangler secret put WORKER_AUTH_TOKEN"
echo "  # Optional (to mark Hot Leads in OpenExplee on reply):"
echo "  npx wrangler secret put OPENEXPLEE_WEBHOOK_URL"
