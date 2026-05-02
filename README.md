<div align="center">

<img src="ui/assets/armyclaw.png" alt="ArmyClaw" width="160">

# ArmyClaw

### Make your Claude Code subscription **100× more powerful.**

A web + mobile + Telegram command center that turns your **single** `claude login`
into a parallel battalion: dozens of long-running sessions, group brainstorms,
scheduled wake-ups, persistent personas, artifacts canvas, an inbuilt terminal,
and full-text search — all reusing your **Pro / Max** plan.
**Zero API keys. Zero credits burned. Zero surprise bills.**

[Quick start](#quick-start) ·
[Why ArmyClaw](#why-armyclaw) ·
[Feature tour](#feature-tour) ·
[Telegram setup](#telegram-bridge-setup) ·
[Architecture](#architecture) ·
[Roadmap](#roadmap)

</div>

---

## TL;DR

ArmyClaw is a **layer that opens many WebSocket-attached `claude` subprocesses
in the background**, all sharing your existing subscription. You drive them
from a clean web UI, your phone via Telegram, or both at once. The CLI keeps
streaming whether you're tabbed in, tabbed out, on the couch, or on a plane.

```
        ┌────────────────────  YOU  ────────────────────┐
        │                                               │
   ┌────▼────┐    ┌────────────┐    ┌────────────┐  ┌──▼──┐
   │ Browser │    │  Telegram  │    │  Reverse   │  │ Mic │
   │ (PWA)   │    │  bot       │    │  proxy     │  │     │
   └────┬────┘    └─────┬──────┘    └─────┬──────┘  └──┬──┘
        │ WS            │ HTTPS           │ WS         │ WebSpeech
        └────────┬──────┴──────┬──────────┴────────────┘
                 ▼             ▼
        ┌────────────────────────────────────┐
        │         cc-server.py (Python)      │
        │  ┌────────┐  ┌────────┐  ┌──────┐  │
        │  │worker A│  │worker B│  │worker│  │  ← parallel claude -p
        │  │claude -p  │claude -p  │  C   │  │     processes, one per
        │  └────────┘  └────────┘  └──────┘  │     chat / group member
        │      ▲           ▲          ▲      │
        │      └───────────┴──────────┘      │
        │      shared subscription auth      │
        │      (your `claude login` token)   │
        └────────────────────────────────────┘
                          │
                  ~/Documents/claude-code-ui/runtime/
                    ├─ <chatId>/chat.json  (per-chat working dir)
                    ├─ long-term-memory.md (cross-chat brain)
                    ├─ knowledge/          (saved skills)
                    └─ snapshots/          (time-machine)
```

**Built for one person who pays Anthropic once and refuses to do that twice.**
Localhost-by-default; bring your own reverse proxy for LAN/remote.

---

## Quick start

```bash
# 1. One-time: install the claude CLI and sign in.
npm install -g @anthropic-ai/claude-code
claude login                                # browser → your Pro/Max account

# 2. Clone + install (one Python dep).
git clone https://github.com/venkateshreddy-2025/claude-code-ui.git armyclaw
cd armyclaw
pip3 install --user "websockets>=12,<17"

# 3. Run.
python3 server/cc-server.py                 # → http://localhost:8765/
```

Open the URL, type something, claude streams back. Want it on your phone?
Skip ahead to [Telegram bridge setup](#telegram-bridge-setup).

> **First time?** Read [Architecture](#architecture) for what's actually
> happening under the hood.

---

## Why ArmyClaw?

Claude Code is the CLI. ArmyClaw is the **operations center on top of it**.
Three things matter when you compare wrappers:

1. **Cost model.** Are you re-implementing on the API (every spawn burns
   credits) or reusing the subscription (flat monthly fee)?
2. **Surface area.** Web, mobile, messaging, scheduled — or just one?
3. **Multi-agent.** Can you run a parallel team, or is it one chat at a time?

Here's how ArmyClaw stacks up:

|                          | **ArmyClaw**                  | Anthropic CLI      | OpenClaw            | Claudia             |
|--------------------------|-------------------------------|--------------------|---------------------|---------------------|
| **Cost model**           | Subscription (flat)           | Subscription (flat)| API key (was OAuth) | Subscription (flat) |
| **Web UI**               | ✓ single-file SPA, ~285 KB    | —                  | —                   | Tauri desktop only  |
| **Mobile (Telegram)**    | ✓ shipped, two-way bridge     | —                  | ✓ (and others)      | —                   |
| **Parallel sessions**    | ✓ unlimited, true subprocess  | One                | ✓                   | ✓                   |
| **Multi-agent groups**   | ✓ Slack-style peer broadcast  | —                  | —                   | —                   |
| **Routines (wake-ups)**  | ✓ cron + heartbeat            | —                  | ✓ heartbeat         | —                   |
| **Cross-chat memory**    | ✓ shared `long-term-memory.md`| —                  | per-skill           | —                   |
| **Personas**             | ✓ unlimited, persistent       | One CLAUDE.md      | ✓ via skills        | ✓ via agents        |
| **Inbuilt terminal**     | ✓ xterm.js + PTY              | (you're in one)    | —                   | —                   |
| **File explorer**        | ✓ browse + edit               | —                  | —                   | ✓                   |
| **Artifacts / canvas**   | ✓ rendered side-panel         | —                  | —                   | —                   |
| **Time-machine**         | ✓ snapshots, restore          | —                  | —                   | ✓ checkpoints       |
| **Voice in**             | ✓ Web Speech API              | —                  | —                   | —                   |
| **Group chat etiquette** | ✓ short-reply enforcer        | —                  | —                   | —                   |
| **License**              | MIT                           | proprietary        | MIT-style           | AGPL                |

### The economics

> Claude API costs run **$1,500–$5,600/mo** for the kind of usage that fits
> in Anthropic's $200 Max plan.[^1] That's the gap ArmyClaw captures:
> **15–36× cheaper than re-billing through the API** while shipping more
> features than any of the alternatives above.

[^1]: [Why I stopped paying API bills and saved 36×](https://levelup.gitconnected.com/why-i-stopped-paying-api-bills-and-saved-36x-on-claude-the-math-will-shock-you-46454323346c) · [Claude Code pricing 2026](https://www.ssdnodes.com/blog/claude-code-pricing-in-2026-every-plan-explained-pro-max-api-teams/)

If your wrapper uses your `ANTHROPIC_API_KEY`, you are paying twice. ArmyClaw
spawns the local `claude` binary directly — same auth path as if you typed
`claude` in your terminal. **Anthropic explicitly allows this**; CLI-style
spawning is documented use.[^2]

[^2]: Anthropic's CLI-spawning policy clarification — [HN discussion](https://news.ycombinator.com/item?id=47844269)

---

## Feature tour

### 1. Parallel sessions, real subprocesses

Every chat in the sidebar gets its own long-running `claude -p` subprocess.
Switching chats **never** kills a stream. You can:

- Send a 5-minute prompt in chat A.
- Switch to chat B and start a separate task.
- Watch chat C's reply arrive in the sidebar with a live indicator + unread dot.
- Scroll through chat D's history while A, B, C all stream in parallel.

Workers are lazy — they spawn on first send, not on session creation, so the
sidebar can show 100 chats without 100 `claude` processes running.

### 2. Group chats with multi-agent peer broadcast

Hit **+ New group chat**, pick 2–5 personas, send a message. Every member
gets it in parallel. **And here's the trick: every persona sees every other
persona's reply, in real time, like a Slack channel.**

```
You:  Tucker, ask Brielle one specific question about her day.
      Brielle, give him a real answer. Then keep going — I am
      quietly watching.

Tucker:  Alright Brielle — since you clearly know so much about
         what men should be doing, what's the most productive
         thing you actually did today?

Brielle: Okay, Tucker — I deep-cleaned my entire apartment,
         rearranged my furniture, and dropped off two bags of my
         ex's stuff at Goodwill. Pretty therapeutic.

Tucker:  Respect on the Goodwill drop-off — closure with a tax
         deduction. But rearranging furniture alone? That's
         either a glow-up or a breakdown.

Brielle: Why does it have to be one or the other? Fine — both.
```

This is **bidirectional bot-to-bot delivery**. The bridge enforces:

- **Short replies** — 1–2 sentences cap, no filler ("OK", "Got it" are banned).
- **`[silent]` marker** — if a member has nothing useful to add, they emit
  `[silent]` and the bridge drops the bubble entirely (the room stays clean).
- **Chain cap** — peer-to-peer ping-pong is metered (default 12 turns) so
  nobody loops forever after a single human prompt.
- **One-time bootstrap** — the first user message in each group ships with
  an invisible `[bridge: this is a GROUP CHAT, roster: …]` block so every
  member knows the room rules cold.
- **Up to 5 members per group**, add/remove mid-conversation via the people
  icon in the header.

Group etiquette rules apply **only in groups**. Solo 1-on-1 chats keep their
persona's full verbosity.

### 3. Telegram bridge — drive any chat from your phone

Bind a chat in your Telegram bot, and your phone becomes a thin client to
the same `claude` worker your browser is talking to. Two-way mirrored:

- Reply in Telegram → assistant streams back via `editMessageText`.
- Reply in the web UI → mirrored to bound Telegram chats with a `🌐 Web:` prefix.
- Multiple bound users → everyone sees the same stream live.
- File delivery: when claude appends `|SEND| <path> |` markers, the bridge
  pushes the file to Telegram via the right method (`sendPhoto`, `sendVideo`,
  `sendAudio`, `sendDocument`) for native preview.

Slash commands inside Telegram: `/list`, `/new`, `/here`, `/fork`, `/start`.

Full setup walk-through with `BotFather` is below — [Telegram bridge setup](#telegram-bridge-setup).

### 4. Routines — scheduled wake-ups, no cron edits

Like OpenClaw's heartbeat, but you can have **as many as you want**, each
bound to a chat. Each routine is a (cadence, prompt) tuple:

- **Cadence** — `every 15 minutes`, `every day at 09:00`, or a raw cron
  expression.
- **Prompt** — what claude wakes up and does (read your inbox, post a
  status digest, run a watchdog test, summarise the day).

Routines run inside the chat they're bound to, so they share its
working directory, persona, and knowledge. State survives restart —
the bridge re-arms the cron line on next boot. Cancel any routine
without opening its chat: header → ⚙ → **Routines**.

### 5. Cross-chat memory — chats brainstorm without corrupting context

Every chat reads `runtime/long-term-memory.md` at the start of every turn.
Entries get added when you tell a chat to **save** (the `SAVE` flow drops
a knowledge file under `runtime/knowledge/<topic>.md` and indexes it
into the long-term memory).

That means:

- Chat A discovers a tricky GoDaddy MySQL workaround → saves it.
- Chat B asking about MySQL on shared hosting → its preamble points it
  at the saved entry → it Reads the file → applies the workaround.
- Neither chat had to fork. Neither's context window got polluted with
  the other's noise. They share knowledge, not transcripts.

The long-term memory is **the cross-chat substrate**. A "@-mention" UI
to summon a specific chat's wisdom on demand is on the roadmap.

### 6. Personas — unlimited, per-chat character pinning

Personas are folders under `runtime/_data/personas/<id>/` with a `PERSONA.md`
(personality + voice) and an `INSTRUCTIONS.md` (output rules + style).
Pin one to any chat, and:

- Every spawn for that chat materialises the persona files into the
  cwd so claude's `Read` tool can look at them.
- Sidebar items get the persona's accent color.
- The composer's typing bubble shows the persona's avatar.
- Filter the chat list by persona via the pill row at the top of the sidebar.

Ship with `Claudy` (default), `Aurora` (chief-of-staff orchestrator),
`Pallavi`, `Flash`, `Sage`, `Tucker`, `Brielle`. Add as many as you want
through the **+ New persona** modal. No re-deploy.

### 7. Artifacts + canvas — interactive side panel

When claude produces something worth seeing rendered (HTML, SVG, JSON,
code blocks for download, images), it appends `|ARTIFACT| <path> |` and
the bridge mounts it in the right-hand artifacts panel:

- **HTML / SVG** — rendered live in a sandboxed iframe.
- **Images** — full-screen lightbox with a tap.
- **Code** — syntax-highlighted with a copy button.
- **Generic files** — download button.

Artifacts persist on disk (next to the chat's `chat.json`) so reload
brings them back. Fork the chat → fork its artifacts.

### 8. Fork chats — branch context, keep momentum

Click **Fork** below the last assistant message. ArmyClaw:

1. Creates a new session with a new UUID + fresh working directory.
2. Copies the last 200 messages into the new chat.json.
3. Spawns claude with `--append-system-prompt` containing those 200 turns
   as a JSON dump, so the new session **already remembers what the
   parent knew** — no re-explaining, no re-uploading files.

Forks show with a `- fork` suffix in the title. Rename via ⋮.

### 9. Chat filters — by persona, tag, favorite, time

The sidebar header has two filter selects:

- **Persona** — dropdown auto-populated from any personas with at
  least one chat. "All chats (12) · Tucker (3) · Aurora (5) · …"
- **Tag** — chats can be tagged (project, theme); filter narrows by tag.

Plus:

- **Favorite** (☆) pins to the top.
- **Recent** scrolls below.
- **Date subtitles** under each title — `5m · May 2, 12:05 AM` — for
  at-a-glance recency.

### 10. Search — full-text across every chat, ⌘K style

Magnifier in the header (or `⌘K` / `Ctrl-K`) opens a SERP-style overlay:

- Live snippet highlighting as you type.
- Filter by **role** (User / Assistant / All).
- Sort by recency or relevance.
- Filter by **cwd substring** to limit to a project.
- Filter by **persona** or **tag**.
- Click a result → preview popup → "Open in chat →" jumps to the exact
  message and pulses the bubble.

### 11. Notes — per-chat scratchpad

Header → 📝 opens the notes panel. Each chat has its own `NOTES.md` in
its working directory. Markdown-rendered, autosaved, lives on disk
forever. Use it for:

- Open questions you don't want claude to answer yet.
- Output you don't want re-summarised.
- A standalone scratchpad that doesn't pollute the chat transcript.

### 12. Voice control — speech-to-text, on-device

Mic icon in the composer toggles the browser's Web Speech API. **No audio
ever leaves the page.** Interim results stream into the textarea live;
hit `Esc` or click the pulsing mic to stop. Edit before sending.

Coming up (roadmap): streaming TTS so claude reads its answers back to
you while you stay hands-free.

### 13. Inbuilt terminal — xterm.js + PTY, in the right panel

Header → ⌨ opens a real terminal **inside the same page**, attached to
a server-side `pty.fork()` running your shell. Run `git status`, run a
test, tail a log — without leaving ArmyClaw. The terminal:

- Streams via the same WebSocket.
- Auto-reconnects on refresh (PTY survives short disconnects).
- Resizes when the panel resizes.
- Inherits the active chat's working directory by default.

### 14. File explorer — browse + edit, rooted at the chat's cwd

Header → 📁 opens a file tree rooted at `runtime/<chatId>/`. Click a
file → opens an inline editor with autosave. Click a folder → expands.
All paths are sanity-checked against the root so `..` can't escape.

Drag-and-drop uploads land in the chat's uploads dir; the path is
mentioned to claude so its `Read` tool can pull it in on the next turn.

### 15. Snapshots / time-machine

Header → ⚙ → **Snapshots** opens a timeline of saved checkpoints. Each
snapshot is a full disk-state copy of:

- All chats (`chat.json` files).
- Long-term memory.
- Personas + skills + knowledge + configs.
- Settings.

Restore any snapshot to roll back. Snapshots are gzipped folders under
`runtime/snapshots/<ts>/`. Useful before a risky experiment, before
upgrading the bridge, or just because.

### 16. Wake-on-restart — interrupted sessions self-resume

If the server is killed mid-stream, ArmyClaw flags those sessions on
disk (`streaming: true`). On next boot, the bridge:

1. Scans for streaming sessions.
2. Resumes the corresponding chat.
3. Posts a synthetic user message: `[wake: server restarted at <ts>,
   please continue…]` so claude picks up where it left off without
   the typewriter UI sitting frozen.

Idle chats are left alone. No lost work, no manual restart drill.

### 17. Skills + knowledge

Two parallel registries that show up in the UI under **⚙ → Skills**
and **⚙ → Knowledge**:

- **Skills** (`runtime/skills/<name>/SKILL.md`) — reusable procedures
  claude can recognise and apply. Triggers are listed; when a user
  message matches, the relevant skill markdown is loaded.
- **Knowledge** (`runtime/knowledge/<topic>.md`) — saved learnings,
  references, gotchas. Indexed into long-term memory for cross-chat
  retrieval.

Both auto-rebuild when files change.

### 18. Themes — 10 accents, persisted

Amber, Ember, Sunset, Rose, Magenta, Violet, Ocean, Mint, Forest, Slate.
Pick from header → 🎨. Persisted in `localStorage`. Tables, code blocks,
links, sidebar accents, typing bubbles all retint together.

### 19. Reload-safe state

Refresh mid-stream. Close your laptop. Switch Wi-Fi networks. The
WebSocket reconnects, the server replays the full state snapshot
(sessions, in-progress streams, busy flags), and the UI resumes
exactly where you were — including the streaming bubble that's
still mid-token.

### 20. Crash recovery + auto-fallback

If `claude --resume <id>` fails (corrupt CLI session memory, fresh
machine, etc.), the bridge:

1. Detects the "No conversation found" error in the result event.
2. Arms the fallback: next spawn skips `--resume` and rebuilds context
   from `chat.json` via `--append-system-prompt`.
3. **Auto-retries the last user message** so you don't have to retype.

You see no hiccup. The chat just keeps going.

---

## Architecture

### One server, many workers

`server/cc-server.py` is ~6,500 lines of asyncio Python. It owns:

- **A WebSocket server** (`/ws`) the UI connects to.
- **An HTTP server** (same port) for static assets, uploads, healthz.
- **A `Worker` per session** — group chat members get
  `<sessionId>:<personaId>` keys; solo chats get bare `<sessionId>`.
- **A reader task per worker** — pumps stdout from `claude -p`, parses
  the stream-JSON event protocol, broadcasts deltas to every connected
  client tagged with `sessionId`.
- **A Telegram poller** (optional) — long-polls `getUpdates`, routes
  messages to the same workers.
- **A routines manager** — schedules and fires wake-ups via an internal
  cron-style loop.
- **A pty.fork() per terminal panel** — the inbuilt terminal.

### How a turn flows

```
1. User sends "fix the bug" in chat A from the web UI.
2. cc-server appends the message to runtime/<chatA>/chat.json.
3. cc-server marks chat A as `streaming: true` (wake-on-restart safety).
4. Lazy-spawn check: if no claude worker for chat A, spawn one with
     claude -p --input-format stream-json --output-format stream-json
            --include-partial-messages --verbose
            --dangerously-skip-permissions --model <m>
            --resume <chatA-uuid>
   plus a system-prompt blob (global rules + persona + skills + memory).
5. cc-server writes {role: user, content: "fix the bug"} to claude's stdin.
6. Claude streams a `message_start` event. cc-server broadcasts:
     {type: "assistant_start", sessionId: "<A>", personaId: "...", id: "..."}
7. Each `content_block_delta` becomes:
     {type: "assistant_delta", sessionId: "<A>", text: "<chunk>"}
8. The browser appends to bubble A's text (~12 fps throttled render).
   Other browsers, Telegram chats bound to A, all see the same stream.
9. `message_stop` ends the assistant bubble. cc-server appends the final
   text to chat.json, runs the file-marker scanner (`|SEND|`, `|ARTIFACT|`),
   delivers files to Telegram + snapshots them locally, broadcasts
   `assistant_end` and `turn_done`.
10. cc-server clears `streaming: true`. Worker stays alive for the next turn.
```

For **group chats**, step 4 runs once per persona member in parallel,
and step 9's non-silent reply also fans out to every other member's
worker as `[from <name>]: <text>` so the room becomes Slack-like.

### Subscription preservation

The `claude` binary uses your `~/.claude/credentials.json` (set by
`claude login`). ArmyClaw spawns the binary with that auth intact:

- **Never** sets `ANTHROPIC_API_KEY`.
- **Scrubs** any inherited `ANTHROPIC_*` env vars before exec (so a
  shell with an API key set won't accidentally bypass the subscription).
- **Never** calls `api.anthropic.com` directly.

So your $20 Pro plan or $200 Max plan covers every chat, every group
member, every routine, every Telegram message, every wake-on-restart.

---

## Requirements

| Tool          | Version       | Why                                                       |
|---------------|---------------|-----------------------------------------------------------|
| **Python**    | 3.10+         | `match`, `\|` types, asyncio                              |
| **claude**    | latest        | The actual model. `npm i -g @anthropic-ai/claude-code`    |
| **Node.js**   | any LTS       | Only because `claude` is an npm package                   |
| **websockets**| 12 – 16       | Sole runtime Python dep                                   |

Tested on **macOS** (primary) and **Linux**. Runs on **Windows under WSL2**;
pure Windows is untested.

---

## Install

### Option A — minimal (recommended)

```bash
git clone https://github.com/venkateshreddy-2025/claude-code-ui.git armyclaw
cd armyclaw
pip3 install --user "websockets>=12,<17"
python3 server/cc-server.py
# → http://localhost:8765/
```

### Option B — guided installer

```bash
git clone https://github.com/venkateshreddy-2025/claude-code-ui.git armyclaw
cd armyclaw
./scripts/install.sh             # checks deps, creates data dir, smoke-tests
./scripts/install.sh --start     # …and starts the server in the foreground
./scripts/install.sh --launchd   # macOS: also installs a launchd plist
```

The script never modifies anything outside the repo + your data dir at
`~/.claude-code-ui/` + (with `--launchd`) the plist file.

### Option C — keep it running in the background

**macOS (launchd):**

```bash
cp examples/ai.claude-code-ui.plist ~/Library/LaunchAgents/
# Edit paths + env vars inside the plist.
launchctl load ~/Library/LaunchAgents/ai.claude-code-ui.plist
```

**Linux (systemd, user unit):**

```bash
cp examples/cc-ui.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now cc-ui
```

### Start / stop / restart cheatsheet

```bash
# Start (default port 8765, override with CC_SERVER_PORT)
python3 server/cc-server.py

# Find the PID
ps aux | grep cc-server.py | grep -v grep | awk '{print $2}'

# Stop (graceful)
kill <pid>

# Stop (force)
kill -9 <pid>

# Restart, one-liner
kill $(ps aux | grep cc-server.py | grep -v grep | awk '{print $2}') 2>/dev/null
sleep 1
nohup python3 server/cc-server.py > runtime/bridge.log 2>&1 &

# Health check
curl -s http://localhost:8765/healthz && echo " (server up)"
```

---

## Telegram bridge setup

Drive any chat from a Telegram bot you control. Useful when you're away
from the laptop — keep a long task running, check on it from your phone,
type follow-ups, receive any files claude generates.

### What you get

| Telegram action      | Effect                                                                 |
|----------------------|------------------------------------------------------------------------|
| (any plain text)     | Routes to your bound (or most-recent) chat. Reply streams back via `editMessageText`. |
| `/list`              | Numbered list + tappable inline keyboard of recent chats. Tap → binds + replays last 10 messages. |
| `/new <title?>`      | Creates a fresh chat, binds you to it.                                 |
| `/here`              | Shows the title and id of the currently bound chat.                    |
| `/fork`              | Forks the bound chat (last 200 turns) and binds you to the new one.    |
| `/start`             | Onboarding text.                                                        |

When the same chat session has multiple Telegram users bound, every
recipient sees the streaming reply in real time. Replies typed in the
web UI are mirrored to bound Telegram chats with a `🌐 Web:` prefix;
replies typed by Telegram user A are mirrored to other bound users with
a `📱 from Telegram:` prefix (A's own chat is never echoed back).

### Setup walk-through

#### 1. Create a bot

Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`,
and follow the prompts. BotFather replies with a token like
`123456789:AAH-…`. **Keep it secret** — anyone with the token can act
as your bot.

#### 2. Find your Telegram user ID

Message [@userinfobot](https://t.me/userinfobot). It replies with your
numeric user ID — you'll need this for the allowlist.

#### 3. Configure ArmyClaw

Two ways: env vars (good for daemons) or a JSON config file (good for
ad-hoc runs).

**Env vars** (e.g. inside a launchd plist or shell profile):

```bash
export CC_TELEGRAM_BOT_TOKEN='123456789:AAH-…'
export CC_TELEGRAM_ALLOWED_USERS='YOUR_USER_ID'   # comma-separated for many
# Optional second gate — chat IDs that must also pass:
# export CC_TELEGRAM_ALLOWED_CHATS='-100123456,-100789012'
```

Don't pass the token on a command line where `ps` can see it — use
your shell's restricted env or a file (next option).

**Config file** (auto-discovered, no env vars needed):

```bash
mkdir -p runtime/configs/telegram
chmod 700 runtime/configs/telegram
umask 077
cat > runtime/configs/telegram/config.json <<'JSON'
{
  "bot_token": "123456789:AAH-…",
  "allowed_users": [YOUR_USER_ID],
  "allowed_chats": []
}
JSON
chmod 600 runtime/configs/telegram/config.json

# then just run the server normally
python3 server/cc-server.py
```

#### 4. Restart and watch the log

Within ~2 seconds of boot you should see:

```
telegram: enabled, allowlist=[YOUR_USER_ID], chats=any
telegram: signed in as @your_bot_username (id=…)
```

If you instead see `telegram: 401 unauthorized — bad token`, the token
is wrong or stale.

#### 5. Test

Send `/start` to your bot in Telegram. You should get the onboarding
reply. Type `/` and Telegram should auto-complete the bridge's
commands.

#### Helper script

`scripts/run-telegram-bridge.sh` is an interactive launcher — prompts
for the token + your user id on first run, writes them to
`runtime/configs/telegram/config.json` (chmod 600), then exec's the
server. Useful for development; for production, prefer the
launchd/systemd path above.

### File delivery from Telegram

When claude appends `|SEND| <absolute-path> |` to a reply, the bridge:

1. Snapshots the file into `runtime/_data/cc-uploads/<sid>/bot-<id>-<file>`.
2. Records `{name, mimeType, size, path, url}` in chat.json.
3. Strips the marker from the persisted text (so the reply reads
   cleanly).
4. Delivers the file to every Telegram chat bound to the session
   using the right Bot API method:

| Extension                              | Method         | Telegram preview                |
|----------------------------------------|----------------|---------------------------------|
| `.png .jpg .jpeg .gif .webp .bmp`      | `sendPhoto`    | Native large preview            |
| `.mp4 .mov .webm .mkv .m4v`            | `sendVideo`    | Inline player                   |
| `.mp3 .m4a .wav .ogg .flac .aac`       | `sendAudio`    | Inline audio player             |
| `.pdf`                                 | `sendDocument` | First-page preview on mobile    |
| (everything else)                      | `sendDocument` | Filename + size                 |

**Limits** — 45 MB per file (5 MB margin under Telegram's 50 MB hard
cap), 10 files per turn, hidden files + noisy dirs skipped.

### Safety

The bridge is **fail-closed by default**: an empty
`CC_TELEGRAM_ALLOWED_USERS` rejects every incoming message. The bot
can't be used by random people who guess your bot username.

- Two-gate allowlist (sender uid AND, optionally, chat id).
- Token never logged; never persisted by the server.
- 401 from `getMe` at boot stops the poller cleanly.
- A non-allowlisted user gets a polite refusal that includes their
  numeric uid so you can decide whether to add them.

> **Only enable the bridge on a server you control.** The CLI runs
> with `--dangerously-skip-permissions`, so anyone reaching a running
> claude through ANY surface (web, Telegram, future WhatsApp/Slack)
> can read and write files as the user the server runs as.

---

## Configuration

Every knob is an environment variable. Defaults shown.

| Variable                          | Default                          | Controls                                                                 |
|-----------------------------------|----------------------------------|--------------------------------------------------------------------------|
| `CC_SERVER_HOST`                  | `127.0.0.1`                      | Bind address. `0.0.0.0` exposes on LAN — put a reverse proxy with auth in front first. |
| `CC_SERVER_PORT`                  | `8765`                           | HTTP + WebSocket port.                                                   |
| `CC_CLAUDE_BIN`                   | `claude`                         | Path to the claude CLI. Use a full path inside a launchd plist.          |
| `CC_MODEL_DEFAULT`                | `claude-sonnet-4-5`              | Default model. Override per-chat with `/model <name>`.                   |
| `CC_DATA_DIR`                     | `<repo>/runtime/_data`           | Where session metadata, uploads, logs live.                              |
| `CC_CWD_ROOT`                     | `<repo>/runtime`                 | Per-chat working dirs + global stores (knowledge, skills, snapshots, soul, long-term memory). |
| `CC_UI_DIR`                       | `<repo>/ui`                      | Where `index.html` lives.                                                |
| `CC_SERVE_STATIC`                 | `1`                              | Serve the UI from this server. `0` if Caddy/nginx is fronting it.        |
| `CC_PATH_PREFIX`                  | (empty)                          | URL prefix the proxy mounts you at, e.g. `/cc`.                          |
| `CC_MEMORY_FILE`                  | `<repo>/runtime/long-term-memory.md` | Cross-chat brain index.                                              |
| `CC_TELEGRAM_BOT_TOKEN`           | (empty)                          | Enable the Telegram bridge with this BotFather token.                    |
| `CC_TELEGRAM_ALLOWED_USERS`       | (empty)                          | Comma-separated Telegram user IDs allowed to use the bot. **Required** when token is set. |
| `CC_TELEGRAM_ALLOWED_CHATS`       | (empty)                          | Optional second gate: comma-separated chat IDs that must also pass.      |
| `CC_TELEGRAM_EDIT_INTERVAL_MS`    | `1200`                           | Min ms between `editMessageText` calls per chat (Telegram rate limit ~1/sec/chat). |
| `CC_ATTACH_SNAPSHOT_LIMIT`        | `10485760` (10 MB)               | Files claude writes smaller than this get copied into the chat's uploads dir for persistence. Larger files are delivered to Telegram but not snapshotted. |

---

## Reverse proxy (HTTPS + auth)

For LAN or remote access, run `cc-server` with `CC_SERVE_STATIC=0` and
put Caddy in front. See [`examples/Caddyfile`](examples/Caddyfile).

```bash
# Foreground for development:
CC_SERVE_STATIC=0 CC_PATH_PREFIX="" python3 server/cc-server.py

# Caddy serves the UI + uploads from disk and reverse-proxies /ws.
caddy run --config examples/Caddyfile
```

> **Don't expose this to the internet without basic auth.** The
> `--dangerously-skip-permissions` flag means whoever can reach the
> WebSocket endpoint can run shell commands and write files as the
> user the server runs as.

---

## Project layout

```
armyclaw/
├── server/
│   └── cc-server.py             # WS + HTTP server (Python 3, asyncio + websockets)
├── ui/
│   ├── index.html               # single-file SPA, ~285 KB, no build step
│   └── assets/
│       └── armyclaw.png         # logo / favicon / mobile home-screen icon
├── examples/
│   ├── Caddyfile                # optional reverse-proxy template
│   ├── ai.claude-code-ui.plist  # macOS launchd template
│   └── cc-ui.service            # Linux systemd template
├── scripts/
│   ├── install.sh               # idempotent local installer
│   └── run-telegram-bridge.sh   # interactive launcher for the TG bridge
├── docs/
│   ├── REQUIREMENTS.md          # detailed feature list + backlog
│   └── STATUS.md                # paths, env vars, health checks
├── runtime/                     # gitignored — your data
│   ├── <chatId>/                # per-chat working dir + chat.json + NOTES.md
│   ├── _data/
│   │   ├── cc-sessions/         # chat metadata + index
│   │   ├── personas/            # persona bundles
│   │   └── cc-uploads/          # per-chat uploads + bot snapshots
│   ├── knowledge/               # saved knowledge files (cross-chat)
│   ├── skills/                  # claude skills registry
│   ├── snapshots/               # time-machine archives
│   ├── configs/telegram/        # telegram config (chmod 700)
│   └── long-term-memory.md      # cross-chat brain index
├── README.md                    # ← you are here
├── LICENSE                      # MIT
└── .gitignore
```

---

## Roadmap

| Status   | Feature                                                              |
|----------|----------------------------------------------------------------------|
| **Shipped** | Telegram bridge (full, two-way, file delivery)                    |
| Planned  | **WhatsApp bridge** — same `Worker` reuse, Baileys for the channel adapter |
| Planned  | **Slack bridge** — same pattern via Slack Events API + slash commands |
| Planned  | **Discord bridge** — for Discord-native teams                        |
| Planned  | **Cross-chat @-mention** — explicit "summon chat X's expertise here" beyond the long-term-memory shared substrate |
| Planned  | **Streaming TTS** — claude reads its replies back to you             |
| Planned  | **Voice messages in** — record audio in the browser, deliver as audio attachment |
| Planned  | **PWA + push notifications** — install to home screen + APNs/FCM    |
| Planned  | **iOS / Android native shells** — WKWebView around the local UI      |
| Researching | **Native MCP server config UI** — toggle external tools per-chat  |

Out of scope (intentionally):
- Multi-user / multi-tenant SaaS — this is a **single-user local tool**.
- Editing past messages — claude's session model doesn't support edits;
  fork instead.
- Real-time WebRTC voice — different protocol stack, days of work.

---

## Troubleshooting

**The page loads but messages don't send → "not connected".**
WebSocket failed to upgrade. Check:
- `python3 server/cc-server.py` actually printed `cc-server starting`.
- The URL bar matches `CC_SERVER_PORT` (default 8765).
- `curl http://localhost:8765/healthz` returns `ok`.

**Empty replies on `/model`, `/mcp`, etc.**
Expected. Slash commands change claude's internal state but don't print
output through `claude --print` mode. The UI shows a one-line note
("Slash command handled silently — no text response") for empty turns
starting with `/`.

**`claude: command not found`.**
Either install via `npm install -g @anthropic-ai/claude-code`, or set
`CC_CLAUDE_BIN=/full/path/to/claude` so the server can find it.

**Chat sits there forever after sending — no reply.**
A `claude --resume` failure. ArmyClaw normally detects this, arms the
fallback, and auto-retries the last user message. If it doesn't, the
session memory is corrupt for that ID — fork the chat from the last
healthy turn and continue in the fork.

**Image uploads vanish after reload.**
Make sure `CC_SERVE_STATIC=1` (the default) so the server serves
`/uploads/...` URLs. Behind a proxy with `CC_SERVE_STATIC=0`, the
proxy needs to expose `/uploads/*` from `<CC_DATA_DIR>/cc-uploads`.

**Telegram bot is silent.**
- Server log must print `telegram: signed in as @yourbot (id=…)`. If
  not, the token is missing/wrong.
- Your Telegram user ID must be in `CC_TELEGRAM_ALLOWED_USERS`. The
  bot replies to non-allowlisted users with their ID — if you didn't
  see a refusal, the poller isn't running.
- No other process can be consuming this bot's `getUpdates` queue.
  The bridge calls `deleteWebhook` at startup; if a different process
  is also long-polling, you'll see `Conflict: terminated by other
  getUpdates request` in the log.

**Telegram `/` autocomplete is empty.**
The bot needs to register its commands once. The bridge does this via
`setMyCommands` at startup; if it failed, the log shows
`telegram: setMyCommands failed`.

**Group chat: members fall silent / never respond to each other.**
Check the bridge log for `peer broadcast` lines — those should fire
every time a non-silent reply lands. If they don't, the session is
missing `groupChat: true` in `runtime/_data/cc-sessions/<sid>.json`.
The migration helper `reindex_groupchat_briefs()` runs on every boot
and should re-flag any orphaned groups; restart the server.

**Server reboots and the daemon is gone.**
Use `scripts/install.sh --launchd` (macOS) or copy
`examples/cc-ui.service` (Linux).

---

## Contributing

Issues and PRs welcome. The whole UI is one HTML file with no build
step — open `ui/index.html` in your editor, refresh the browser, done.
The server is one Python file. The Markdown library is `marked` loaded
from a CDN script tag in the HTML; no bundler.

When adding a feature:

- Keep the single-file constraint where possible (one `cc-server.py`,
  one `index.html`).
- Add an entry to `docs/REQUIREMENTS.md`.
- If a new env var is introduced, add it to the [Configuration](#configuration)
  table and to the boot log.
- If a new Telegram command is added, register it via `setMyCommands`
  so it appears in the bot's `/` autocomplete.

---

## Security disclosure

ArmyClaw spawns claude with `--dangerously-skip-permissions`. Whoever
reaches a running claude through any surface (web UI, Telegram, future
bridges) can run shell commands and write files as the user the
server runs as. **Localhost-by-default for a reason.** Don't expose
without auth + a reverse proxy.

If you discover a security issue, please open a GitHub issue marked
`[security]` rather than a public PR.

---

## License

[MIT](LICENSE) — do whatever you want, no warranty.

---

<div align="center">
<sub>
  Built by one person who pays Anthropic once and refuses to do that twice.<br>
  Logo · the ArmyClaw mascot, by way of ChatGPT image gen.
</sub>
</div>
