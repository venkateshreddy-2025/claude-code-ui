# claude-code-ui — Requirements & Status

A clean, ChatGPT-style web UI that talks **directly** to a long-running
`claude` CLI subprocess. No API keys, no SaaS layer, no proxy server in
the middle — your existing `claude login` (Pro / Max subscription) is
the only credential it uses.

---

## Architecture

```
Browser  ──WebSocket──►  cc-server.py (asyncio)  ──pipes──►  claude (Max subscription)
   ▲                                                                │
   │                                                                ▼
   ◄──── HTTP (same port, static UI + uploads) ──┘     ~/claude-ui/<timestamp>/
                                                        ↑ per-session cwd + chat.json
```

* One `claude` subprocess at a time (the active session's). Switching
  sessions kills the current process and `--resume <session-id>` reloads
  context from disk.
* Sessions live in `~/.claude-code-ui/cc-sessions/<uuid>.json` and are
  mirrored into the session's working dir at `~/claude-ui/<ts>/chat.json`.
* Forking copies the last 200 turns into a new session and feeds them
  to claude via `--append-system-prompt`.

Files:
* `server/cc-server.py` — async WebSocket + HTTP server (Python 3 + `websockets`)
* `ui/index.html` — single static page, ~85 KB, no build step
* `examples/Caddyfile` — optional reverse proxy (HTTPS + basic auth)
* `examples/ai.claude-code-ui.plist` — macOS launchd template
* `examples/cc-ui.service` — Linux systemd template

---

## Implemented

| # | Requirement | Notes |
|---|---|---|
| 1 | **Direct chat UI for the `claude` CLI** | No middleman — your `claude login` does the auth. |
| 2 | **Persistent subprocess** | `claude -p --input-format stream-json --output-format stream-json --include-partial-messages --dangerously-skip-permissions --model <model> --session-id <uuid>`. ANTHROPIC_API_KEY etc. scrubbed from env. |
| 3 | **Reload-safe state** | Each WS reconnect gets the full state snapshot back: session list, messages, in-progress streaming. |
| 4 | **New chat = new pid + new session** | `+ New chat` creates a UUID, mkdir of `~/claude-ui/<timestamp>/`, spawns claude with `--session-id`. |
| 5 | **Token-level streaming** | `--include-partial-messages` gives `content_block_delta`; the server forwards each `text_delta` to all connected browsers. |
| 6 | **Mobile + desktop** | One responsive layout that scales from 320 px phones to 27" monitors. Sidebar collapses on desktop, drawer-overlays on mobile. |
| 7 | **10 themes** | Amber, Ember, Sunset, Rose, Magenta, Violet, Ocean, Mint, Forest, Slate — persisted in localStorage. |
| 8 | **ChatGPT-style sidebar** | Left column lists chats with title + relative time, ⋮ menu (star/rename/fork/delete). Starred sessions pinned to top. |
| 9 | **File / image upload** | Paperclip + drag-drop + clipboard paste, 10 MB cap. Images go to claude as `image` content blocks (vision); other files saved to disk + path mentioned in text so claude's Read tool can pull them. |
| 10 | **Per-session cwd** | Each new session gets `~/claude-ui/<timestamp>/`. Switching sessions reuses their original folder. |
| 11 | **Interruption / queueing** | Send button stays enabled while claude is mid-reply. New messages write to claude's stdin; claude finishes the current turn then reads + answers the new one. |
| 12 | **Configurable default model** | `--model` flag on every spawn, overridable via `CC_MODEL_DEFAULT` env or `/model <name>` inside a chat. |
| 13 | **Collapsible + resizable sidebar** | Hamburger toggles. Drag the right edge to resize 200–480 px. State persists in localStorage. |
| 14 | **Auto-focus composer** | On page load, `+ New chat`, session switch, and `turn_done`. Skipped on phone-narrow screens to avoid the keyboard popping up. |
| 15 | **Typing indicator** | Bouncing-dots bubble appears the instant Send is hit; hides as soon as the first text delta streams in. |
| 16 | **Slash commands pass through** | `/model`, `/mcp`, `/help`, `/clear` etc. — sent verbatim to claude. When a turn ends with no assistant text and the user message starts with `/`, the UI shows a system note ("Slash command handled silently — no text response"). |
| 17 | **Microphone / speech-to-text** | Web Speech API (browser-native). Mic button between paperclip and textarea. Click to toggle; pulses red while listening; Esc to stop. Interim results stream live. |
| 18 | **Title from first user message words** | First user turn promotes "New chat" to first ~8 words (≤60 chars), claude.ai-style. Slash-command-only first turns fall back to a YYYY-MM-DD HH:MM stamp. |
| 19 | **Datetime as small subtext under title** | Tiny `when` label under each session title (10 px). Format: "5m · Apr 25, 3:17 PM". |
| 20 | **chat.json mirrored into session folder** | Every message append rewrites `<cwd>/chat.json`, so the conversation lives alongside any files claude is writing — easy backup & portability. |
| 21 | **Copy icon on every message** | Hover-revealed action bar below each bubble. Copy → clipboard, with a green "✓ Copied" flash. |
| 22 | **Fork icon on the LAST claude message** | Always-visible on the most recent bot message. Click → confirms → server spawns a new session with last 200 turns inherited. New title = original + " - fork". |
| 23 | **Long user message expand/collapse** | If text > 600 chars or > 9 lines, bubble starts collapsed at ~14 em with a fade gradient and a "Show more" toggle. No limit on claude responses. |
| 24 | **Full Markdown rendering** | Headings, lists, tables, code blocks, blockquotes — all themed against the active accent color. Tables get a tinted header row + alternating body rows. |
| 25 | **Throttled streaming + smart auto-scroll** | DOM is re-rendered ~12 fps (not per-token). Auto-scroll only follows if the user is within 80 px of the bottom; a "↓ Latest" button appears otherwise so they can keep reading old turns. |
| 26 | **Image lightbox** | Inline images cap at 220 px tall in chat, click for full-size with name + dimensions. |
| 27 | **Standalone HTTP serving** | cc-server.py serves the UI + uploads itself by default — no Caddy/nginx required. Set `CC_SERVE_STATIC=0` if you prefer to put a reverse proxy in front. |
| 28 | **Search across every chat (SERP)** | Magnifier in header (or ⌘K) opens a full-screen search overlay. Filter by role (All / User / Claude), sort newest/oldest, filter by cwd substring. Live snippet highlighting. Click a result → popup with the full message → "Open in chat →" switches to that session and pulses the matching bubble. |

## Backlog

| # | Requirement | Notes |
|---|---|---|
| B1 | **Per-session model selector in UI** | Dropdown next to the chat header for picking model without typing `/model`. |
| B2 | **Project directory picker** | "Use the user's chosen project" instead of always `~/claude-ui/<timestamp>/`. Picker that browses + lets user pick. |
| B3 | **Search by date range** | Add explicit From/To date pickers to the search filters (server already accepts dateFrom/dateTo). |
| B4 | **PWA + offline shell** | Install to home screen; cache the static HTML so reopening on a flaky network shows the shell while WS reconnects. |
| B5 | **Streaming TTS replies** | Pipe claude's reply through TTS (macOS `say`, OpenAI TTS, etc.) → audio chunks over WS → `<audio>`. |
| B6 | **Voice messages (audio attachment)** | Hold-to-record audio from the browser → server sends as audio attachment. |
| B7 | **Native iOS/Android wrappers** | WKWebView around the local UI + APNs / FCM push + Share Extension. |

## Out of scope (intentionally)

* Real-time WebRTC voice call — different protocol stack, days of work.
* Editing past messages — claude's session model doesn't support edits;
  fork instead.
* Multi-user / multi-tenant — this is a single-user local tool.

## Known limitations

* `claude --print` mode silently consumes some slash commands (`/model`,
  `/mcp`) — they change internal state but don't print to stdout. The UI
  shows a hint on empty turns starting with `/`.
* TUI pickers (arrow-key model picker, fuzzy session resume) don't render
  in the web composer because the protocol is JSON event stream, not a
  PTY.

## Glossary

| Name | What |
|---|---|
| **cc-server** | The Python websockets+http server that wraps `claude` |
| **cc-ui** | This single-file HTML chat UI |
| **session** | One claude conversation (UUID) with its own message log + cwd folder |
| **fork** | New session that inherits the last 200 turns from another session |
| **active session** | The one whose claude subprocess is currently running |
