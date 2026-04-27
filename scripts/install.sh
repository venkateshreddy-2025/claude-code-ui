#!/usr/bin/env bash
# install.sh — minimal local install for claude-code-ui.
#
# What it does
# ────────────
#   1. Verifies python3, claude, and the websockets package are available
#      (offers to install websockets via pip if missing).
#   2. Creates the data dir at ~/.claude-code-ui/.
#   3. Prints the command to start the server in the foreground.
#
# Usage
# ─────
#   ./scripts/install.sh                    # interactive
#   ./scripts/install.sh --start            # also start the server
#   ./scripts/install.sh --launchd          # install macOS launchd plist
#
# This script never edits anything outside the repo dir + the plist + the
# data dir. To uninstall, delete ~/.claude-code-ui/ and the plist.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${CC_DATA_DIR:-$HOME/.claude-code-ui}"
PORT="${CC_SERVER_PORT:-8765}"

red()    { printf '\033[31m%s\033[0m\n' "$*" >&2; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
bold()   { printf '\033[1m%s\033[0m\n' "$*"; }

step() { echo; bold "▸ $*"; }

start_flag=0
launchd_flag=0
for arg in "$@"; do
    case "$arg" in
        --start)   start_flag=1   ;;
        --launchd) launchd_flag=1 ;;
        --help|-h)
            sed -n '2,18p' "$0"; exit 0 ;;
        *) red "unknown arg: $arg"; exit 2 ;;
    esac
done

step "Checking prerequisites"

if ! command -v python3 >/dev/null 2>&1; then
    red "python3 not found. Install Python 3.10+ and re-run."
    exit 1
fi
green "✓ python3: $(python3 --version)"

if ! command -v claude >/dev/null 2>&1; then
    red "claude CLI not found in PATH."
    cat <<EOF
Install it with:
    npm install -g @anthropic-ai/claude-code

Then sign in (the UI uses the existing claude login — your Pro/Max
subscription, not an API key):
    claude login
EOF
    exit 1
fi
green "✓ claude:  $(claude --version 2>/dev/null | head -1)"

# websockets module
if ! python3 -c "import websockets" 2>/dev/null; then
    yellow "Python 'websockets' package missing."
    read -r -p "Install it now with pip3? [Y/n] " ans
    ans=${ans:-Y}
    if [[ "$ans" =~ ^[Yy]$ ]]; then
        pip3 install --user "websockets>=12,<17"
    else
        red "Cannot start the server without websockets. Install it manually:"
        echo "    pip3 install --user 'websockets>=12,<17'"
        exit 1
    fi
fi
ws_ver=$(python3 -c "import websockets; print(websockets.__version__)")
green "✓ websockets: $ws_ver"

step "Creating data directory"
mkdir -p "$DATA_DIR/cc-sessions" "$DATA_DIR/cc-uploads" "$DATA_DIR/logs"
green "✓ $DATA_DIR/"
green "  ├── cc-sessions/  (one JSON per chat)"
green "  ├── cc-uploads/   (image + file attachments)"
green "  └── logs/"

step "Quick smoke test"
echo "Verifying the server starts and serves the UI..."
(
    cd "$REPO_DIR"
    CC_SERVER_PORT="$((PORT + 100))" CC_DATA_DIR="$DATA_DIR" \
        python3 server/cc-server.py >/dev/null 2>&1 &
    pid=$!
    sleep 1.2
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$((PORT + 100))/" || echo 000)
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    if [[ "$code" == "200" ]]; then
        green "✓ HTTP 200 on /"
    else
        red "✗ smoke test failed (got HTTP $code)"
        red "  See $DATA_DIR/logs/cc-server.stderr.log for details."
        exit 1
    fi
)

if (( launchd_flag )); then
    if [[ "$(uname)" != "Darwin" ]]; then
        red "--launchd is macOS-only. Skipping."
    else
        step "Installing launchd plist"
        plist_dst="$HOME/Library/LaunchAgents/ai.claude-code-ui.plist"
        cp "$REPO_DIR/examples/ai.claude-code-ui.plist" "$plist_dst"
        # Substitute /Users/YOU and python3 path
        py_bin="$(command -v python3)"
        claude_bin="$(command -v claude)"
        sed -i '' \
            -e "s|/Users/YOU|$HOME|g" \
            -e "s|/usr/bin/python3|$py_bin|g" \
            -e "s|/opt/homebrew/bin/claude|$claude_bin|g" \
            "$plist_dst"
        launchctl unload "$plist_dst" 2>/dev/null || true
        launchctl load   "$plist_dst"
        green "✓ Loaded $plist_dst"
        green "  Server now keeps running on port $PORT after reboot."
    fi
fi

step "Done"
cat <<EOF

Open the UI:
    http://127.0.0.1:$PORT/

Or start it in the foreground:
    cd "$REPO_DIR"
    python3 server/cc-server.py

Environment overrides:
    CC_SERVER_PORT=$PORT       (port to listen on)
    CC_MODEL_DEFAULT=...       (default model — see "claude" docs)
    CC_DATA_DIR=$DATA_DIR
    CC_CWD_ROOT=<repo>/runtime  (default — keeps state next to source)

See README.md for full configuration options.
EOF

if (( start_flag )); then
    step "Starting cc-server in the foreground (Ctrl-C to stop)"
    cd "$REPO_DIR"
    exec python3 server/cc-server.py
fi
