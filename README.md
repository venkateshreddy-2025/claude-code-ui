# claude-code-ui

A clean, ChatGPT-style web UI for the [`claude` CLI](https://docs.claude.com/en/docs/claude-code/overview)
that runs on your own machine. Multi-session, streaming, themed, with
Markdown and image support — all backed by long-running `claude`
subprocesses. No middleware, no proxy SaaS, no API keys: it uses your
existing `claude login` (Pro / Max subscription).

```
                     ┌─────────────────────────────────────────────────┐
                     │                cc-server (Python)               │
 ┌───────────┐       │  ┌────────────┐ ┌────────────┐ ┌─────────────┐  │
 │  Browser  │──WS──►│  │  worker A  │ │  worker B  │ │  worker C   │  │
 └───────────┘       │  │  claude -p │ │  claude -p │ │  claude -p  │  │
                     │  └────────────┘ └────────────┘ └─────────────┘  │
 ┌───────────┐  HTTPS│         ▲              ▲               ▲        │
 │ Telegram  │──────►│         │              │               │        │
 │   bot     │       └─────────┴──────────────┴───────────────┴────────┘
 └───────────┘                ~/.claude-code-ui/cc-sessions/<uuid>.json
```

Designed for **local single-user use**. You run it, it binds to
`127.0.0.1`, your chats live on disk under `~/.claude-code-ui/`. No
hosting, no cloud.

---

## Table of contents

- [Features](#features)
- [Quick start](#quick-start)
- [Requirements](#requirements)
- [Install](#install)
- [Configuration](#configuration)
- [How it works](#how-it-works)
- [Telegram bridge (optional)](#telegram-bridge-optional)
- [Reverse proxy (HTTPS + auth)](#reverse-proxy-https--auth)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## Features

### Web UI

- **Parallel sessions** — every chat runs its own `claude` subprocess.
  Switching chats in the sidebar never kills a stream; replies in
  background sessions keep arriving and surface as live / unread
  indicators in the sidebar.
- **Token-level streaming** with throttled rendering and smart
  auto-scroll (only follows you if you're already at the bottom).
- **Full Markdown** — headings, lists, tables, code blocks, blockquotes
  — themed against your accent color.
- **10 themes** — Amber, Ember, Sunset, Rose, Magenta, Violet, Ocean,
  Mint, Forest, Slate. Persisted in `localStorage`.
- **File / image upload** — paperclip, drag-drop, or clipboard paste;
  10 MB cap. Images go through as `image` content blocks (vision);
  other files are saved to disk and the path is mentioned to claude
  so its `Read` tool can pull them in.
- **Fork any chat** — clones the last 200 turns into a new session
  and feeds them to claude as a system prompt so context is preserved.
- **Slash commands pass through** — `/model`, `/mcp`, `/help`, `/clear`,
  etc. — sent verbatim to the CLI.
- **Personas** — pre-defined role/instruction bundles you can pin to
  new sessions (a default "Claudy" persona is seeded on first run).
- **Mic / speech-to-text** via the browser's Web Speech API — no audio
  ever leaves the page.
- **Mobile-friendly** — single responsive layout from 320 px phones to
  27" monitors.
- **Reload-safe** — refresh the page mid-stream and it resumes from
  the same point.
- **Search across every chat** — magnifier in the header (or ⌘K)
  opens a SERP-style overlay. Filter by role, sort by recency, click
  a result to jump to that exact message.

### Resilience

- **Crash recovery** — if a session's `claude --resume` keeps failing
  (corrupted CLI session memory), the server falls back to a fresh
  spawn that rebuilds context from the on-disk JSON via
  `--append-system-prompt`. The UI never sees the hiccup.
- **Reload-safe state** — every WebSocket reconnect gets the full state
  back: session list, messages, in-progress streams.

### Telegram bridge (optional)

- Drive any session from a Telegram bot you control.
- Slash commands inside Telegram: `/list`, `/new`, `/here`, `/fork`,
  `/start`.
- Bidirectional sync: a reply triggered from the web UI streams to
  every Telegram chat bound to that session in real time, and
  vice-versa. See [Telegram bridge (optional)](#telegram-bridge-optional).

---

## Quick start

```bash
# 1. Install the claude CLI and sign in (one-time).
npm install -g @anthropic-ai/claude-code
claude login                        # opens a browser → Pro/Max account

# 2. Clone and install the single Python dep.
git clone https://github.com/venkateshreddy-2025/claude-code-ui.git
cd claude-code-ui
pip3 install --user "websockets>=12,<17"

# 3. Run.
python3 server/cc-server.py
# → http://localhost:8765/
```

Open the URL, type something, claude streams back.

> First time? See [How it works](#how-it-works) for what's happening
> behind the scenes.

---

## Requirements

| Tool | Version | Why |
|---|---|---|
| **Python** | 3.10+ | `match`, `\|` types, asyncio |
| **claude CLI** | latest | The actual model — installed via `npm install -g @anthropic-ai/claude-code` |
| **Node.js** | any LTS | Only because `claude` is an npm package |
| **websockets** (Python) | 12 – 16 | Sole runtime dependency |

Tested on **macOS** and **Linux**. Should run on **Windows under
WSL2**; pure Windows is untested (per-session cwd logic uses POSIX
paths).

---

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
./scripts/install.sh             # checks deps, creates data dir, smoke-tests
./scripts/install.sh --start     # …and starts the server in the foreground
./scripts/install.sh --launchd   # macOS: also installs a launchd plist
```

The script never modifies anything outside the repo, your data dir at
`~/.claude-code-ui/`, and (with `--launchd`) the plist file.

### Option C — keep it running in the background

**macOS (launchd):**
```bash
cp examples/ai.claude-code-ui.plist ~/Library/LaunchAgents/
# Edit the placeholders (paths, env vars) inside the plist.
launchctl load ~/Library/LaunchAgents/ai.claude-code-ui.plist
```

**Linux (systemd, user unit):**
```bash
cp examples/cc-ui.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now cc-ui
```

---

## Configuration

Every knob is an environment variable. Defaults shown.

| Variable | Default | What it controls |
|---|---|---|
| `CC_SERVER_HOST` | `127.0.0.1` | Bind address. `0.0.0.0` exposes on LAN — put a reverse proxy with auth in front first. |
| `CC_SERVER_PORT` | `8765` | HTTP + WebSocket port. |
| `CC_CLAUDE_BIN` | `claude` | Path to the claude CLI. Use a full path if not on `PATH` (e.g. inside a launchd plist). |
| `CC_MODEL_DEFAULT` | `claude-sonnet-4-5` | Model for new sessions. Override per-session with `/model <name>` in chat. |
| `CC_DATA_DIR` | `~/.claude-code-ui` | Where session metadata, uploads, and logs live. |
| `CC_CWD_ROOT` | `~/claude-ui` | Where per-session working directories are created. |
| `CC_UI_DIR` | `<repo>/ui` | Where `index.html` lives. |
| `CC_SERVE_STATIC` | `1` | Serve the UI from this server. Set `0` if you have Caddy/nginx in front. |
| `CC_PATH_PREFIX` | (empty) | URL prefix the proxy mounts you at, e.g. `/cc`. |
| `CC_TELEGRAM_BOT_TOKEN` | (empty) | Enable the Telegram bridge with this BotFather token. |
| `CC_TELEGRAM_ALLOWED_USERS` | (empty) | Comma-separated Telegram user IDs allowed to use the bot. **Required** when `CC_TELEGRAM_BOT_TOKEN` is set. |
| `CC_TELEGRAM_ALLOWED_CHATS` | (empty) | Optional second gate: comma-separated chat IDs that must also pass. |
| `CC_TELEGRAM_EDIT_INTERVAL_MS` | `1200` | Min ms between `editMessageText` calls per chat (Telegram's rate limit is ~1/sec/chat). |
| `CC_ATTACH_SNAPSHOT_LIMIT` | `10485760` (10 MB) | Files claude writes that are smaller than this get copied into the session's uploads dir so they persist in chat.json. Larger files are delivered to Telegram but not snapshotted (avoids cloning repos / huge binaries). |

---

## How it works

### Sessions, working directories, `chat.json`

Every "+ New chat" in the UI:

1. Generates a UUID.
2. Creates `~/claude-ui/<YYYY-MM-DD_HH-MM-SS>/` as the session's
   working directory.
3. Spawns `claude -p --session-id <uuid>` with that directory as cwd.
4. After every message, mirrors the conversation into
   `<cwd>/chat.json` so it lives next to any files claude is producing.

The JSON files in `~/.claude-code-ui/cc-sessions/` are the source of
truth for the UI (titles, message logs, metadata). Claude's own
session memory under `~/.claude/` is the convenient cache that lets
`--resume <uuid>` warm up faster on subsequent turns.

### Parallel workers

The server keeps a `Worker` per session — its own claude subprocess,
its own reader task, its own busy/streaming state. Switching sessions
in the UI just changes which worker the focused tab is bound to;
**no worker is ever killed by a focus change**. This lets you:

- Send a long-running prompt in chat A
- Switch to chat B while A keeps streaming
- See A's status pulse in the sidebar (live indicator) and any new
  content land as an unread dot
- Send a separate message in B that runs in parallel
- Switch back to A and find its in-progress text exactly where it
  was, still streaming if not done

Workers are created lazily on the first message. Switching to a chat
that has no worker yet is free; only sending wakes up a subprocess.

### Forking a chat

Click **Fork** below the last assistant message. The server:

1. Creates a brand-new session UUID + working directory.
2. Copies the last 200 messages from the source session into the new
   one (so they show up in the UI immediately).
3. Spawns claude with `--append-system-prompt` containing a JSON dump
   of those 200 messages as context.

The forked title is `<original> - fork`. Rename via the ⋮ menu.

### Uploading files

Paperclip, drag-drop, or paste from the clipboard. Files go to
`~/.claude-code-ui/cc-uploads/<session-id>/`.

- **Images** (PNG, JPEG, GIF, WebP) are inlined as `image` content
  blocks — claude can read them via vision.
- **Other files** (PDF, code, text) are saved to disk and the path is
  appended to your message text → claude's `Read` tool pulls them in
  on demand.

Image previews stick around after a page reload because they're
served from `/uploads/...` rather than browser blob URLs.

---

## Telegram bridge (optional)

Drive any chat from a Telegram bot. Useful for stepping away from
your desk: keep replying to claude on your phone, then sit back down
at the web UI and pick up exactly where you left off — both surfaces
share the same workers and the same on-disk session.

### What you get

| Telegram action | Effect |
|---|---|
| (any plain text) | Routes to your bound (or most-recent) session. Reply streams back via `editMessageText`. |
| `/list` | Numbered list + tappable inline keyboard of recent chats. Tap → binds, then replays the **last 10 messages** as a transcript so you have context. |
| `/new <title?>` | Creates a fresh session, binds you to it. |
| `/here` | Shows the title and id of the currently bound session. (Use `/list` and tap the same chat to re-load its history.) |
| `/fork` | Forks the bound chat (last 200 turns) and binds you to the new one. |
| `/start` | Onboarding text. |

When a turn happens in a session multiple Telegram users are bound
to, every recipient sees the streaming reply in real time. Messages
sent from the web UI are mirrored to bound Telegram chats with a
"🌐 Web: …" prefix; messages sent from one Telegram user are mirrored
to *other* bound users with a "📱 from Telegram: …" prefix (the
original sender's chat is never echoed back to itself).

### Per-message source + format adaptation

The same chat session can receive interleaved messages from the web
UI and Telegram. Each user message is tagged with its source on
disk (`source: "telegram"` for bridge messages, omitted for web).

When the bridge sends a message to claude, it prefixes the user's
text with `[TG] `. A global system-prompt prefix tells claude:

- **`[TG]`-marked messages** → reply in **plain text only**. No
  Markdown bold (`**…**`), italics, inline code, code fences,
  headers, blockquotes, or Markdown-style bullets. Telegram clients
  render those characters literally.
- **No `[TG]` prefix** → full GitHub-flavoured Markdown (the web UI
  renders that natively).

So when you switch from Telegram to the web UI mid-conversation,
claude immediately resumes using Markdown — no manual switch needed.
The web UI also shows a small `✈︎ via telegram` badge under user
messages that came from the bridge.

### File delivery

The bridge can deliver files claude writes during a turn (via its
`Write`, `Edit`, or shell tools) directly into your Telegram chat.

**Opt-in by default.** File delivery only fires when your message
looks like it's asking for one — phrases like *"send me…"*, *"make a
PDF"*, *"give me a screenshot"*, or any explicit file extension
(*"create hello.txt"*, *"export to .csv"*) trigger the post-turn
scan. Plain coding messages (*"refactor this"*, *"fix the bug"*)
leave the bot text-only so claude can use scratch files freely
without spamming your Telegram.

**Three signal sources** (most authoritative first):

1. **`tool_use` events from claude's stream** — `Write`, `Edit`, and
   `NotebookEdit` tools emit the exact `file_path` claude is writing
   to. We capture this directly from the JSON stream, so it's
   100% reliable for files claude creates or modifies via these
   tools.
2. **cwd diff** — files claude writes via shell tools (Bash, etc.)
   inside the session's working directory.
3. **Path mentions in the reply text** — backstop for cross-cwd
   writes claude announces (*"Saved at ~/Downloads/joke.md"*).
   Strict filters: regular files only, mtime > turn start, nothing
   under system prefixes (`/usr/`, `/System/`, `/etc/`, etc.), no
   hidden files.

**Persistence in `chat.json`.** Each delivered file is *snapshotted*
into the session's uploads dir (`<CC_DATA_DIR>/cc-uploads/<sid>/`)
and the resulting record `{name, mimeType, size, path, url}` is
appended to the assistant message in `chat.json`. This means:

- Reloading the chat in the web UI shows the attachments under the
  reply (just like user-uploaded files).
- The `chat.json` file in the session's working directory is a
  complete portable snapshot of the conversation including files.
- Files larger than `CC_ATTACH_SNAPSHOT_LIMIT` (default 10 MB) are
  *delivered to Telegram* but not snapshotted locally — the chat
  history shows them as `{name, size, note: "too large to keep a
  copy"}` rather than maintaining a copy. This avoids cloning entire
  repos or large binaries.

When delivery does fire, each file is sent through the API method
that gives the best native preview on Telegram clients:

| File extension | API method | What you see in Telegram |
|---|---|---|
| `.png .jpg .jpeg .gif .webp .bmp` | `sendPhoto` | Native large inline preview, tap for full size |
| `.mp4 .mov .webm .mkv .m4v` | `sendVideo` | Thumbnail + inline player |
| `.mp3 .m4a .wav .ogg .flac .aac` | `sendAudio` | Inline audio player |
| `.pdf` | `sendDocument` | Mobile clients show first-page preview; desktop shows icon + open |
| `.pptx .docx .xlsx .key .pages …` | `sendDocument` | Filename + size; tap opens in associated app |
| `.txt .md .json .py .js …` | `sendDocument` | Inline text preview if small (<5 KB) |
| `.zip .tar .gz` | `sendDocument` | Filename + size only |

Limits and behaviour:

- **File size cap: 45 MB per file.** The Telegram Bot API hard-limits
  single uploads at 50 MB; we leave a 5 MB margin for the multipart
  envelope. Files bigger than 45 MB are skipped with a message
  listing what wasn't delivered. (Running your own [local Bot API
  server](https://core.telegram.org/bots/api#using-a-local-bot-api-server)
  raises the cap to 2 GB; not configured by default.)
- A maximum of **10 files per turn** are delivered. If a turn
  produces more, the rest are listed but not uploaded.
- Hidden files (starting with `.`), noisy directories (`node_modules`,
  `.git`, `dist`, `__pycache__`, `.venv`, `target`, `build`, `.cache`,
  …), and cc-server's own bookkeeping files (`chat.json`, `PERSONA.md`,
  `INSTRUCTIONS.md`) are skipped automatically.
- If `sendPhoto`/`sendVideo` rejects a file (oversized image
  dimensions, malformed media), the bridge transparently falls back
  to `sendDocument` so the file still gets through.
- If a turn produces only files (no text reply), the placeholder
  reads "📎 N file(s) attached below" instead of "(no text response)".

### Setup

1. **Create a bot.** Message [@BotFather](https://t.me/BotFather) on
   Telegram, send `/newbot`, follow the prompts. BotFather replies
   with a token like `123456789:AAH-…`. Keep it secret — anyone with
   the token can act as your bot.

2. **Find your Telegram user ID.** Message
   [@userinfobot](https://t.me/userinfobot) — it replies with your
   numeric user ID.

3. **Set the env vars** (do **not** put the token on a command line
   where `ps` can see it; use a file or your shell's restricted
   environment instead). Example for launchd:

   ```xml
   <key>EnvironmentVariables</key>
   <dict>
     <key>CC_TELEGRAM_BOT_TOKEN</key><string>YOUR_BOTFATHER_TOKEN</string>
     <key>CC_TELEGRAM_ALLOWED_USERS</key><string>YOUR_TELEGRAM_USER_ID</string>
   </dict>
   ```

   Or for a quick shell run, store the token in a file and load it:

   ```bash
   # one-time
   mkdir -p ~/.config/claude-code-ui
   umask 077
   echo "YOUR_BOTFATHER_TOKEN" > ~/.config/claude-code-ui/telegram.token
   chmod 600 ~/.config/claude-code-ui/telegram.token

   # every run
   CC_TELEGRAM_BOT_TOKEN=$(cat ~/.config/claude-code-ui/telegram.token) \
   CC_TELEGRAM_ALLOWED_USERS=YOUR_TELEGRAM_USER_ID \
   python3 server/cc-server.py
   ```

4. **Restart the server.** Watch the log for these two lines within
   ~2 seconds of boot:

   ```
   telegram: enabled, allowlist=[YOUR_USER_ID], chats=any
   telegram: signed in as @your_bot_username (id=...)
   ```

   If you see `telegram: 401 unauthorized — bad token`, the token in
   the env var is wrong or stale.

5. **Test.** Send `/start` to your bot in Telegram. You should get
   the onboarding reply. Type `/` and Telegram should auto-complete
   the available commands.

### Helper script

`scripts/run-telegram-bridge.sh` is an interactive launcher that
loads the token from `~/.config/claude-code-ui/telegram.token` (or
prompts for it with hidden input), then execs the server with the
right env. Useful for ad-hoc development; for daemonised setups,
prefer the launchd / systemd path above.

### Safety

The bridge is **fail-closed by default**: an empty
`CC_TELEGRAM_ALLOWED_USERS` rejects every incoming message. The bot
can't be silently used by random people who guess your bot username.

- Two-gate allowlist: sender uid must be in
  `CC_TELEGRAM_ALLOWED_USERS`, **and** (if non-empty) chat id must
  also be in `CC_TELEGRAM_ALLOWED_CHATS`.
- Token never logged. Token never persisted by the server (only the
  process env reads it).
- 401 from `getMe` at boot stops the poller cleanly so you notice
  immediately rather than retrying forever.
- A non-allowlisted user gets a polite refusal that includes their
  numeric uid, so you can decide whether to add them.

> ⚠️ Only enable the bridge on a server you control. The CLI runs
> with `--dangerously-skip-permissions`, so anyone who reaches a
> running claude through any surface (web or Telegram) can read and
> write files as the user the server runs as.

---

## Reverse proxy (HTTPS + auth)

For LAN or remote access, run cc-server with `CC_SERVE_STATIC=0` and
put Caddy in front. See [`examples/Caddyfile`](examples/Caddyfile)
for a working template.

```bash
# Foreground for development:
CC_SERVE_STATIC=0 CC_PATH_PREFIX="" python3 server/cc-server.py

# Caddy serves the UI + uploads from disk and reverse-proxies /ws.
caddy run --config examples/Caddyfile
```

> ⚠️ **Don't expose this to the internet without basic auth.** The
> `--dangerously-skip-permissions` flag the server passes to claude
> means whoever can reach the WebSocket endpoint can run shell
> commands and write files as the user. Localhost-only by default
> for a reason.

---

## Project layout

```
claude-code-ui/
├── server/
│   └── cc-server.py          # WS + HTTP server (Python 3, websockets)
├── ui/
│   └── index.html            # single-file SPA, ~100 KB, no build step
├── examples/
│   ├── Caddyfile             # optional reverse-proxy template
│   ├── ai.claude-code-ui.plist   # macOS launchd template
│   └── cc-ui.service         # Linux systemd template
├── scripts/
│   ├── install.sh            # idempotent local installer
│   └── run-telegram-bridge.sh    # interactive launcher for the TG bridge
├── docs/
│   ├── REQUIREMENTS.md       # detailed feature list + backlog
│   └── STATUS.md             # paths, env vars, health checks
├── README.md
├── LICENSE                   # MIT
└── .gitignore
```

---

## Troubleshooting

**The page loads but messages don't send → "not connected"**
WebSocket failed to upgrade. Check:
- `python3 server/cc-server.py` actually printed `cc-server starting`
  with no errors.
- The URL bar matches `CC_SERVER_PORT` (default 8765).
- `curl http://localhost:8765/healthz` returns `ok`.

**Empty replies on `/model`, `/mcp`, etc.**
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
the proxy needs to expose `/uploads/*` from
`<CC_DATA_DIR>/cc-uploads` — see the bundled `examples/Caddyfile`.

**Telegram bot is silent / shows two ticks but never replies**
The bridge isn't running. Check:
- The server log printed `telegram: signed in as @yourbot (id=…)` at
  boot. If not, `CC_TELEGRAM_BOT_TOKEN` is missing or wrong.
- Your Telegram user ID is in `CC_TELEGRAM_ALLOWED_USERS`. The bot
  refuses messages from anyone else and replies with their ID — if
  you didn't see a refusal either, the server isn't polling.
- No other process or webhook is consuming this bot's `getUpdates`
  queue. The bridge calls `deleteWebhook` at startup; if a different
  process is also long-polling, you'll see `Conflict: terminated by
  other getUpdates request` in the log.

**Telegram `/` autocomplete is empty**
The bot needs to register its commands once. The bridge does this
via `setMyCommands` at startup; if it failed, the log shows
`telegram: setMyCommands failed`. Restart the server after fixing the
underlying error.

**Server reboots and the daemon is gone**
Use `scripts/install.sh --launchd` (macOS) or copy
`examples/cc-ui.service` (Linux) to set up a service that starts on
login.

**Session stuck in a spawn-and-die loop**
The server tracks short-lived crashes in a 30 s rolling window. After
two failures on `--resume <sid>`, future spawns for that session
skip `--resume` and rebuild context from JSON. If a session is
permanently broken, delete it from the sidebar and fork from a
healthy snapshot.

---

## Contributing

Issues and PRs welcome. The whole UI is one HTML file with no build
step — open `ui/index.html` in your editor, refresh the browser,
done. The server is one Python file. The Markdown library is `marked`
loaded from a CDN script tag in the HTML; no bundler.

When adding a feature, please:

- Keep the single-file constraint where possible (one `cc-server.py`,
  one `index.html`).
- Add an entry to `docs/REQUIREMENTS.md`.
- If a new env var is introduced, add it to the [Configuration](#configuration)
  table and to the boot log.

## License

[MIT](LICENSE)
