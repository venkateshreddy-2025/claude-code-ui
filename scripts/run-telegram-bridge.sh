#!/usr/bin/env bash
# Convenience launcher for the experimental telegram-bridge branch.
#
# Reads the bot token from one of (in priority order):
#   1. CC_TELEGRAM_BOT_TOKEN already exported in your shell
#   2. ~/.openclaw/telegram.token (chmod 600 — file you control)
#   3. interactive prompt (input is hidden)
#
# Token never appears on a command line, never gets written to a log,
# never leaves the local machine except to api.telegram.org.

set -euo pipefail

# Where the experimental code lives. Override CC_REPO if you cloned
# the feature branch somewhere else.
CC_REPO="${CC_REPO:-/tmp/claude-code-ui-stage}"
CC_SERVER="${CC_REPO}/server/cc-server.py"
CC_UI_DIR_DEFAULT="${CC_REPO}/ui"

# Defaults you can override by exporting them before running this script.
: "${CC_SERVER_PORT:=18793}"
: "${CC_DATA_DIR:=$HOME/.openclaw}"
: "${CC_UI_DIR:=$CC_UI_DIR_DEFAULT}"
: "${CC_TELEGRAM_ALLOWED_USERS:=}"

if [ ! -f "$CC_SERVER" ]; then
  echo "✗ cc-server.py not found at $CC_SERVER"
  echo "  Set CC_REPO to the path of your claude-code-ui clone."
  exit 1
fi

# ── token resolution ───────────────────────────────────────────────
TOKEN_FILE="$HOME/.openclaw/telegram.token"
if [ -z "${CC_TELEGRAM_BOT_TOKEN:-}" ] && [ -f "$TOKEN_FILE" ]; then
  CC_TELEGRAM_BOT_TOKEN=$(<"$TOKEN_FILE")
  echo "✓ token loaded from $TOKEN_FILE"
fi
if [ -z "${CC_TELEGRAM_BOT_TOKEN:-}" ]; then
  echo "Paste BotFather token (input hidden):"
  read -r -s CC_TELEGRAM_BOT_TOKEN
  echo
  if [ -z "$CC_TELEGRAM_BOT_TOKEN" ]; then
    echo "✗ no token entered, aborting."
    exit 1
  fi
  echo "💾 Save token to $TOKEN_FILE for next time? (y/N)"
  read -r save_choice
  if [[ "$save_choice" =~ ^[Yy]$ ]]; then
    mkdir -p "$(dirname "$TOKEN_FILE")"
    umask 077
    printf "%s" "$CC_TELEGRAM_BOT_TOKEN" > "$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE"
    echo "✓ saved (chmod 600)"
  fi
fi

# ── allowlist resolution ───────────────────────────────────────────
if [ -z "$CC_TELEGRAM_ALLOWED_USERS" ]; then
  echo "Enter your Telegram numeric user id (e.g. 6370291936):"
  read -r CC_TELEGRAM_ALLOWED_USERS
  if [ -z "$CC_TELEGRAM_ALLOWED_USERS" ]; then
    echo "⚠ no allowlist set — every message will be refused. Continuing anyway."
  fi
fi

export CC_SERVER_PORT CC_DATA_DIR CC_UI_DIR
export CC_TELEGRAM_BOT_TOKEN CC_TELEGRAM_ALLOWED_USERS

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  starting cc-server with telegram bridge"
echo "    port:        $CC_SERVER_PORT"
echo "    data dir:    $CC_DATA_DIR"
echo "    ui dir:      $CC_UI_DIR"
echo "    allow uids:  ${CC_TELEGRAM_ALLOWED_USERS:-(empty — bot off)}"
echo "    token:       (loaded, hidden)"
echo "════════════════════════════════════════════════════════════"
echo ""

exec python3 "$CC_SERVER"
