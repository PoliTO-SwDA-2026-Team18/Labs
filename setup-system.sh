#!/usr/bin/env bash
# What this script does:
#   1. Stops any running Docker Compose services (volumes/data are preserved)
#   2. Creates /tmp data directories if they do not already exist
#   3. Starts the infrastructure services (database, messagebus, cache, jaeger) via Docker Compose
#   4. Starts MailHog (SMTP trap on :1025, web UI on :8025)
#   5. Starts MZinga dev server via npm run dev (foreground — Ctrl+C to stop)
#
# Usage:
#   chmod +x setup-system.sh
#   ./setup-system.sh
#
# Platforms: macOS, Linux, WSL (Windows Subsystem for Linux)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MZINGA_DIR="$SCRIPT_DIR/mzinga/mzinga-apps"

# Guard: mzinga-apps must have been cloned and npm install must have been run
if [[ ! -d "$MZINGA_DIR" ]]; then
  echo ""
  echo "ERROR: $MZINGA_DIR not found."
  echo "       Clone mzinga-apps first:"
  echo "         cd mzinga && git clone https://github.com/mzinga-io/mzinga-apps.git"
  exit 1
fi

if [[ ! -d "$MZINGA_DIR/node_modules" ]]; then
  echo ""
  echo "ERROR: $MZINGA_DIR/node_modules not found."
  echo "       Run 'npm install' inside mzinga/mzinga-apps first."
  exit 1
fi

# ── 1. Stop existing containers (preserve volumes / data) ─────────────────────
echo ""
echo "==> [1/5] Stopping existing Docker Compose services (volumes preserved)..."
(cd "$MZINGA_DIR" && docker compose down --remove-orphans) || true
echo "    Done."

# ── 2. Ensure /tmp data directories exist (never wiped) ──────────────────────
echo ""
echo "==> [2/5] Ensuring /tmp data directories exist..."
mkdir -p /tmp/database /tmp/mzinga /tmp/messagebus
echo "    /tmp/database, /tmp/mzinga, /tmp/messagebus ready."

# ── 3. Start infrastructure services in the background ───────────────────────
echo ""
echo "==> [3/5] Starting Docker infrastructure services (database, messagebus, cache, jaeger)..."
(cd "$MZINGA_DIR" && docker compose up database messagebus cache jaeger --detach)
echo "    Infrastructure started in the background."
echo "    Run 'docker compose logs -f' inside mzinga/mzinga-apps/ to follow logs."

# ── 4. Start MailHog ──────────────────────────────────────────────────────────
echo ""
echo "==> [4/5] Starting MailHog (SMTP on :1025, web UI on :8025)..."
docker rm -f mailhog 2>/dev/null || true
docker run -d --name mailhog -p 1025:1025 -p 8025:8025 mailhog/mailhog
echo "    MailHog started. Open http://localhost:8025 to view caught emails."

# ── 5. Start MZinga dev server ────────────────────────────────────────────────
echo ""
echo "==> [5/5] Starting MZinga dev server (npm run dev)..."
echo "    Press Ctrl+C to stop."
echo ""
cd "$MZINGA_DIR" && npm run dev
