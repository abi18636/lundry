#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export BOT_URL="${BOT_URL:-https://zenith-trader-bot.onrender.com}"
# TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID must come from environment (never hardcode)
while true; do
  python3 scripts/hourly_watchdog.py || true
  sleep 3600
done
