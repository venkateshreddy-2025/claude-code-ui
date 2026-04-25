# claude-code-ui — Status

Snapshot of services + paths after a default install.

---

## Services

```
cc-server.py — WebSocket + HTTP, port 8765 (configurable via CC_SERVER_PORT)
```

Foreground:
```bash
python3 server/cc-server.py
```

Background (macOS launchd):
```bash
cp examples/ai.claude-code-ui.plist ~/Library/LaunchAgents/
# edit /Users/YOU paths in the plist
launchctl load ~/Library/LaunchAgents/ai.claude-code-ui.plist
```

Background (Linux systemd, user unit):
```bash
cp examples/cc-ui.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now cc-ui
```

## Where things live

```
<repo>/                                  # this git repo
├── server/cc-server.py                  # WS + HTTP server
├── ui/                                  # static UI assets
│   └── index.html                       # single-file SPA, ~85 KB
├── examples/                            # config templates
│   ├── Caddyfile                        # optional reverse proxy
│   ├── ai.claude-code-ui.plist          # macOS launchd
│   └── cc-ui.service                    # Linux systemd
├── scripts/install.sh                   # idempotent local installer
├── docs/REQUIREMENTS.md                 # what's done + backlog
└── docs/STATUS.md                       # ↳ you're here

~/.claude-code-ui/                       # runtime data (override with CC_DATA_DIR)
├── cc-sessions/
│   ├── index.json                       # {active, sessions: [{id,title,…}]}
│   └── <uuid>.json                      # one per session
├── cc-uploads/<session-id>/             # files uploaded inside that session
└── logs/                                # cc-server.{stdout,stderr}.log

~/claude-ui/<YYYY-MM-DD_HH-MM-SS>/       # per-session working directory
└── chat.json                            # message log mirror (claude works here)
```

## URLs

| Path | What |
|---|---|
| `http://localhost:8765/` | The UI (default standalone install) |
| `http://localhost:8765/ws` | WebSocket endpoint |
| `http://localhost:8765/uploads/<sid>/<file>` | User-uploaded files |
| `http://localhost:8765/healthz` | Health check (returns "ok") |

## Configuration knobs

| Env var | Default | What |
|---|---|---|
| `CC_SERVER_HOST` | `127.0.0.1` | Bind address |
| `CC_SERVER_PORT` | `8765` | WS + HTTP port |
| `CC_CLAUDE_BIN` | `claude` | Path to claude CLI |
| `CC_MODEL_DEFAULT` | `claude-sonnet-4-5` | Model for new spawns; override per-session with `/model` |
| `CC_DATA_DIR` | `~/.claude-code-ui` | Sessions + uploads + logs |
| `CC_CWD_ROOT` | `~/claude-ui` | Where per-session working dirs are created |
| `CC_UI_DIR` | `<repo>/ui` | Static UI directory |
| `CC_SERVE_STATIC` | `1` | Serve the UI from this server. Set `0` if Caddy/nginx is fronting it. |
| `CC_PATH_PREFIX` | (empty) | URL prefix when behind a proxy that mounts you at e.g. `/cc/` |

## Health check

```bash
curl -sS -o /dev/null -w "%{http_code}\n" http://localhost:8765/healthz
# Expect: 200

curl -sS -o /dev/null -w "%{http_code}\n" \
  -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" -H "Sec-WebSocket-Version: 13" \
  http://localhost:8765/ws
# Expect: 101
```

## End-to-end test

```bash
# Spawn a fresh claude with the configured model, send "ok", expect "ok" back.
python3 - <<'PY'
import asyncio, json, websockets

async def go():
    async with websockets.connect('ws://localhost:8765/ws') as ws:
        await ws.send(json.dumps({'type': 'new'}))
        await ws.send(json.dumps({'type': 'send', 'text': 'Reply with: ok'}))
        async for raw in ws:
            m = json.loads(raw)
            if m.get('type') == 'assistant_delta':
                print(m.get('text'), end='', flush=True)
            elif m.get('type') == 'turn_done':
                print(); break
asyncio.run(go())
PY
```

## Logs

```bash
# Foreground: stderr is visible.
python3 server/cc-server.py

# Background (macOS launchd):
tail -f ~/.claude-code-ui/logs/cc-server.stderr.log

# Background (Linux systemd):
journalctl --user -u cc-ui -f
```
