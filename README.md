# claude-code-ui

A clean, ChatGPT-style web UI for the [`claude` CLI](https://docs.claude.com/en/docs/claude-code/overview)
that runs on your own machine. Multi-session, streaming, themed, with
Markdown + image support — all backed by a long-running `claude`
subprocess. No middleware, no proxy SaaS, no API keys: it uses your
existing `claude login` (Pro / Max subscription).

```
Browser  ──WebSocket──►  cc-server.py  ──pipes──►  claude CLI (Max subscription)
```

Designed for **local use** — you run it, it runs on `localhost`, your
chats stay on disk under `~/.claude-code-ui/`. Everything in this repo
is single-user. No hosting, no cloud.

---

## Features

- **Multi-session** — many chats side-by-side, ChatGPT-style. Each gets
  its own working directory at `~/claude-ui/<timestamp>/` so claude can
  drop scratch files there.
- **Token-level streaming** with throttled rendering and smart
  auto-scroll (only follows you if you're near the bottom).
- **Full Markdown** — headings, lists, tables, code blocks — themed
  against your accent color.
- **10 themes** — Amber, Ember, Sunset, Rose, Magenta, Violet, Ocean,
  Mint, Forest, Slate. Stored in `localStorage`.
- **File / image upload** — paperclip + drag-drop + clipboard paste, 10
  MB cap. Images go through as `image` content blocks (vision); other
  files mention the path so claude's `Read` tool can pull them in.
- **Fork** any chat — copies the last 200 turns into a new session and
  feeds them to claude as system prompt.
- **Slash commands pass through** — `/model`, `/mcp`, `/help`, `/clear`,
  etc.
- **Mic / speech-to-text** via the browser's Web Speech API — no audio
  ever leaves the page.
- **Mobile-friendly** — single layout from 320 px phones to 27" monitors.
- **Reload-safe** — refresh the page and the active stream resumes.

## Quick start (5 commands)

```bash
# 1. Install the claude CLI and sign in (one-time).
npm install -g @anthropic-ai/claude-code
claude login                        # opens browser → Pro/Max account

# 2. Clone this repo and install the one Python dep.
git clone https://github.com/venkateshreddy-2025/claude-code-ui.git
cd claude-code-ui
pip3 install --user "websockets>=12,<17"

# 3. Run.
python3 server/cc-server.py
# → http://localhost:8765/
```

That's it. Open the URL, you'll see a fresh chat. Type something,
claude streams back.

> 💡 First time? See [Setting up Claude Code sessions](#setting-up-claude-code-sessions)
> below for what to expect from the `claude login`, model picker, etc.

---

## Requirements

| | Version | Why |
|---|---|---|
| **Python** | 3.10+ | `match` statements, `\|` types, asyncio |
| **claude CLI** | latest | the actual brain — installed via `npm install -g @anthropic-ai/claude-code` |
| **Node.js** | any LTS | only because claude is an npm package |
| **websockets** (Python) | 12 – 16 | the only Python dep |

Tested on **macOS** and **Linux**. Should work on **Windows** under
WSL2; pure-Windows is untested (the per-session cwd logic uses POSIX
paths).

## Install

### Option A — minimal (recommended)

```bash
git clone https://github.com/venkateshreddy-2025/claude-code-ui.git
cd claude-code-ui
pip3 install --user "websockets>=12,<17"
python3 server/cc-server.py
```

Open <http://localhost:8765/>.

### Option B — using `scripts/install.sh`

```bash
git clone https://github.com/venkateshreddy-2025/claude-code-ui.git
cd claude-code-ui
./scripts/install.sh             # checks deps, creates data dir, smoke tests
./scripts/install.sh --start     # …and starts the server in the foreground
./scripts/install.sh --launchd   # macOS: also installs a launchd plist
```

The script never touches anything outside the repo, your
`~/.claude-code-ui/` data dir, and (with `--launchd`) the plist file.

### Option C — keep it running in the background

**macOS (launchd):**
```bash
cp examples/ai.claude-code-ui.plist ~/Library/LaunchAgents/
# Edit the /Users/YOU placeholders + python3 path in the plist.
launchctl load ~/Library/LaunchAgents/ai.claude-code-ui.plist
```

**Linux (systemd, user unit):**
```bash
cp examples/cc-ui.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now cc-ui
```

---

## Setting up Claude Code sessions

The UI is a thin shell around the `claude` CLI. Most of what looks like
"sessions" in this UI maps to claude's own session model.

### 1. Sign in once

```bash
claude login
```

This opens a browser, you log in to your Anthropic Pro or Max account,
and a credential is written to your machine. The UI **never** sees this
credential — it just spawns `claude` and lets the CLI use whatever
auth is already there.

> The server **scrubs** `ANTHROPIC_API_KEY`, `CLAUDECODE`, and
> `CLAUDE_CODE_ENTRYPOINT` from the env before spawning claude, so even
> if you have those set globally, claude will still use your subscription
> login (the one you got from `claude login`).

### 2. Pick a default model (optional)

```bash
# Set once via env var…
export CC_MODEL_DEFAULT=claude-opus-4-1

# …or pass it inline.
CC_MODEL_DEFAULT=claude-opus-4-1 python3 server/cc-server.py
```

Inside any chat, you can switch models for that session with the
slash command:

```
/model claude-opus-4-1
```

The supported model strings are whatever your `claude` CLI version
accepts — run `claude --help` to see the list.

### 3. Sessions, working directories, and chat.json

Every "+ New chat" in the UI:

1. Generates a UUID.
2. Creates `~/claude-ui/<YYYY-MM-DD_HH-MM-SS>/` as the session's working
   directory.
3. Spawns `claude -p --session-id <uuid>` with that dir as cwd.
4. After every message, writes `<cwd>/chat.json` so the conversation
   lives alongside any files claude is producing.

Switching to a different chat in the sidebar:

1. Kills the current `claude` subprocess.
2. Re-spawns `claude -p --resume <uuid>` with that session's stored
   cwd, so claude reloads its prior context from its own session store.

This means **claude itself owns the conversation memory** — the JSON
files in `~/.claude-code-ui/cc-sessions/` are just for the UI to know
which sessions exist, their titles, and to render the message bubbles.

If you blow away `~/.claude-code-ui/`, claude's own session memory is
preserved (under `~/.claude/`, where claude stores it). You'd just lose
the sidebar's metadata.

### 4. Forking a chat

Click the **Fork** button below the last assistant message. The server:

1. Creates a brand-new session UUID + cwd.
2. Copies the last 200 messages from the source session into the new
   one (so they show up in the UI immediately).
3. Spawns claude with `--append-system-prompt` containing a JSON dump
   of those 200 messages, so claude has the prior dialogue as context
   when you send the next message.

The forked title is `<original> - fork`. You can rename it via the ⋮
menu in the sidebar.

### 5. Uploading files

Paperclip, drag-drop, or paste from the clipboard. Files go into
`~/.claude-code-ui/cc-uploads/<session-id>/`.

- **Images** (PNG, JPEG, GIF, WebP) are inlined as `image` content
  blocks → claude can see them via vision.
- **Other files** (PDF, code, text) are saved to disk and the path is
  appended to your message text → claude's built-in `Read` tool pulls
  them in on demand.

Image previews stick around after a page reload because they're served
from `/uploads/...` rather than blob: URLs.

---

## Configuration

All knobs are environment variables, defaults shown:

| Var | Default | What |
|---|---|---|
| `CC_SERVER_HOST` | `127.0.0.1` | Bind address. Use `0.0.0.0` to expose on LAN (then put a reverse proxy with auth in front!). |
| `CC_SERVER_PORT` | `8765` | HTTP + WS port. |
| `CC_CLAUDE_BIN` | `claude` | Path to the claude CLI. Use a full path if it's not on `PATH` (e.g. inside a launchd plist). |
| `CC_MODEL_DEFAULT` | `claude-sonnet-4-5` | Model for new sessions. Override per-session with `/model <name>` in chat. |
| `CC_DATA_DIR` | `~/.claude-code-ui` | Where session metadata, uploads, and logs live. |
| `CC_CWD_ROOT` | `~/claude-ui` | Where per-session working dirs are created. |
| `CC_UI_DIR` | `<repo>/ui` | Where `index.html` is. Defaults to the `ui/` next to `cc-server.py`. |
| `CC_SERVE_STATIC` | `1` | Serve the UI from this server. Set `0` if you have Caddy/nginx fronting it. |
| `CC_PATH_PREFIX` | (empty) | URL prefix the proxy mounts you at, e.g. `/cc`. |

### Theming

Open the gear icon in the sidebar footer to pick from the 10 built-in
themes. Selection is stored in `localStorage`, so it persists per
browser.

To add your own theme, edit the `[data-theme="..."]` blocks at the top
of `ui/index.html` — copy any existing theme and tweak `--accent`,
`--accent-2`, `--bg-1`, etc.

### Reverse proxy (HTTPS + basic auth)

For LAN / remote access, run cc-server with `CC_SERVE_STATIC=0` and
put Caddy in front. See [`examples/Caddyfile`](examples/Caddyfile)
for a working template.

```bash
# In your shell, for foreground use:
CC_SERVE_STATIC=0 CC_PATH_PREFIX="" python3 server/cc-server.py

# Caddy serves the UI + uploads from disk and reverse-proxies /ws.
caddy run --config examples/Caddyfile
```

> ⚠️ **Don't expose this to the internet without basic auth.** The
> `--dangerously-skip-permissions` flag the server passes to claude
> means whoever can reach your `/ws` endpoint can run shell commands
> and write files as you. Localhost-only by default for a reason.

---

## Project layout

```
claude-code-ui/
├── server/
│   └── cc-server.py          # WS + HTTP server (Python 3, websockets)
├── ui/
│   └── index.html            # single-file SPA, ~85 KB, no build step
├── examples/
│   ├── Caddyfile             # optional reverse-proxy template
│   ├── ai.claude-code-ui.plist   # macOS launchd template
│   └── cc-ui.service         # Linux systemd template
├── scripts/
│   └── install.sh            # idempotent local installer
├── docs/
│   ├── REQUIREMENTS.md       # detailed feature list + backlog
│   └── STATUS.md             # paths, env vars, health checks
├── README.md
├── LICENSE                   # MIT
└── .gitignore
```

## Troubleshooting

**The page loads, but messages don't send → "not connected"**
The WebSocket failed to upgrade. Check:
- `python3 server/cc-server.py` actually printed `cc-server starting`
  with no errors.
- The URL bar matches `CC_SERVER_PORT` (default 8765).
- `curl http://localhost:8765/healthz` returns `ok`.

**Empty replies on `/model`, `/mcp` etc.**
Expected. Slash commands change claude's internal state but don't
print output through `claude --print` mode. The UI shows a one-line
note ("Slash command handled silently — no text response") for
empty turns starting with `/`.

**`claude: command not found`**
Either install via `npm install -g @anthropic-ai/claude-code`, or set
`CC_CLAUDE_BIN=/full/path/to/claude` so the server can find it.

**Image uploads vanish after reload**
Make sure `CC_SERVE_STATIC=1` (the default) so the server serves
`/uploads/...` URLs. If you're behind a proxy with `CC_SERVE_STATIC=0`,
the proxy needs to expose `/uploads/*` from `~/.claude-code-ui/cc-uploads`
— see the bundled `examples/Caddyfile`.

**My machine reboots and the server is gone**
Use `scripts/install.sh --launchd` (macOS) or copy
`examples/cc-ui.service` (Linux) to set up a service that starts on
login.

## Contributing

Issues and PRs welcome. The whole UI is one HTML file with no build
step — open `ui/index.html` in your editor, refresh the browser, done.

The server is one Python file. The Markdown library is `marked` loaded
from a CDN script tag in the HTML, no bundler.

## License

[MIT](LICENSE)
