#!/usr/bin/env bash
# Convenience launcher for the Telegram bridge.
#
# Reads the bot token from one of (in priority order):
#   1. CC_TELEGRAM_BOT_TOKEN already exported in your shell
#   2. <repo>/runtime/configs/telegram/config.json — a JSON file owned
#      by you (chmod 600) with shape:
#          {"bot_token": "...", "allowed_users": [123],
#           "allowed_chats": []}
#      cc-server reads this directly at startup if no env var is set.
#   3. interactive prompt (input is hidden) — saves to (2) if you say yes
#
# The token never appears on a command line, never gets written to a
# log, and never leaves the local machine except as required to talk
# to api.telegram.org.

set -euo pipefail

# Path to the cloned repo. Auto-detects from this script's location;
# override via CC_REPO if you've moved the script.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CC_REPO="${CC_REPO:-$(cd "$SCRIPT_DIR/.." && pwd)}"
CC_SERVER="${CC_REPO}/server/cc-server.py"

# Defaults — override by exporting before running this script.
# Bridge defaults to <repo>/runtime/ for everything; only set port here.
: "${CC_SERVER_PORT:=8765}"
: "${CC_TG_CONFIG_FILE:=${CC_REPO}/runtime/configs/telegram/config.json}"

if [ ! -f "$CC_SERVER" ]; then
  echo "✗ cc-server.py not found at $CC_SERVER"
  echo "  Set CC_REPO to the path of your claude-code-ui clone."
  exit 1
fi

# ── interactive token / allowlist setup if config is missing ───────
if [ -z "${CC_TELEGRAM_BOT_TOKEN:-}" ] && [ ! -f "$CC_TG_CONFIG_FILE" ]; then
  echo "No telegram config at $CC_TG_CONFIG_FILE."
  echo "Paste BotFather token (input hidden, leave blank to skip telegram):"
  read -r -s _TOKEN
  echo
  if [ -n "$_TOKEN" ]; then
    echo "Enter your Telegram numeric user id (find via @userinfobot):"
    read -r _UID
    mkdir -p "$(dirname "$CC_TG_CONFIG_FILE")"
    chmod 700 "$(dirname "$CC_TG_CONFIG_FILE")"
    umask 077
    python3 - "$_TOKEN" "$_UID" "$CC_TG_CONFIG_FILE" <<'PY'
import json, os, sys
token, uid, path = sys.argv[1], sys.argv[2], sys.argv[3]
out = {"bot_token": token, "allowed_users": [], "allowed_chats": []}
try: out["allowed_users"] = [int(uid)]
except ValueError: pass
with open(path, "w") as f:
    json.dump(out, f, indent=2)
os.chmod(path, 0o600)
print(f"✓ saved {path} (chmod 600)")
PY
    unset _TOKEN _UID
  fi
fi

export CC_SERVER_PORT
[ -n "${CC_TELEGRAM_BOT_TOKEN:-}" ] && export CC_TELEGRAM_BOT_TOKEN

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  starting cc-server with telegram bridge"
echo "    port:        $CC_SERVER_PORT"
echo "    repo:        $CC_REPO"
echo "    runtime:     $CC_REPO/runtime"
echo "    tg config:   $CC_TG_CONFIG_FILE"
echo "════════════════════════════════════════════════════════════"
echo ""

exec python3 "$CC_SERVER"
