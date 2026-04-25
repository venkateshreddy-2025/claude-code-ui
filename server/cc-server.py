#!/usr/bin/env python3
"""
cc-server.py — WebSocket + HTTP server that wraps a persistent `claude`
CLI subprocess so a browser UI can talk to Claude Code directly.

Architecture
------------
    Browser  ──WS──►  cc-server.py  ──pipes──►  claude (Max subscription)
                          │
                          └── also serves static UI + uploads over HTTP
                              when CC_SERVE_STATIC=1 (the default).

* Multi-session: many conversations side-by-side, ChatGPT-style. Each
  session has its own message log on disk; ONE claude subprocess runs at
  a time (the active session's). Switching sessions kills the current
  subprocess and respawns claude with `--resume <session-id>` so the new
  session's prior turns are loaded.
* Each new session gets its own working directory under ~/claude-ui/<ts>/
  so claude can drop scratch files there without colliding with other
  sessions.
* File uploads ride the WS as base64 chunks. Images are passed to claude
  as `image` content blocks (vision); other files are saved to
  CC_DATA_DIR/cc-uploads/<session-id>/ and the path is mentioned to
  claude in text so its built-in Read tool can pull them in.
* Streaming: `--include-partial-messages` gives token-level deltas, which
  the server forwards to all connected browsers.

Two deployment modes
--------------------
1. **Standalone** (no reverse proxy):
   `python3 cc-server.py` → opens http://localhost:8765/.
   The same port serves the UI (HTTP), uploads (HTTP), and the websocket.
2. **Behind a reverse proxy** (e.g. Caddy / nginx for HTTPS + auth):
   set `CC_SERVE_STATIC=0` and `CC_PATH_PREFIX=/cc` (or whatever path the
   proxy mounts). The proxy serves index.html + uploads from disk and
   reverse-proxies `/cc/ws` to this WS server.

WS protocol
-----------
client → server
    {"type":"sessions"}                       request list of sessions
    {"type":"new"}                            create + switch to fresh session
    {"type":"switch","id"}                    switch active session
    {"type":"delete","id"}                    delete a session
    {"type":"rename","id","title"}            rename a session
    {"type":"star","id","value"}              toggle favorite
    {"type":"fork","id","lastN"}              fork a session (last N msgs)
    {"type":"send","text","attachments":[…]}  send a turn (optionally with files)
    {"type":"stop"}                           kill active subprocess
    {"type":"upload",…}                       binary upload (base64)
    {"type":"state"}                          request a state snapshot

server → client
    {"type":"state","activeId","sessions","messages","pid","busy","current"}
    {"type":"sessions","sessions":[…brief…]}
    {"type":"user","msg":{role,text,ts,id,attachments?}}
    {"type":"assistant_start","id"}
    {"type":"assistant_delta","id","text"}
    {"type":"assistant_end","id"}
    {"type":"turn_done"}
    {"type":"upload_ok","fileId","path","url","name","mimeType","size"}
    {"type":"error","message"}
    {"type":"session_ended"}
"""
from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import signal
import shutil
import sys
import time
import uuid
from http import HTTPStatus
from pathlib import Path

import websockets
from websockets.datastructures import Headers
from websockets.http11 import Response

# ───────────────────  paths + config  ───────────────────
HOST = os.environ.get('CC_SERVER_HOST', '127.0.0.1')
PORT = int(os.environ.get('CC_SERVER_PORT', 8765))

HOME = Path.home()
# CC_DATA_DIR holds session metadata, uploads, and logs. Override to e.g.
# ~/.openclaw to share data with the OpenClaw bridge install.
DATA_DIR = Path(os.environ.get('CC_DATA_DIR', str(HOME / '.claude-code-ui')))
SESS_DIR = DATA_DIR / 'cc-sessions'
SESS_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR = DATA_DIR / 'cc-uploads'
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR = DATA_DIR / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
INDEX_FILE = SESS_DIR / 'index.json'

# Per-session working directory root. Each new session gets its own folder
# under here so claude can write scratch files without colliding.
CWD_ROOT = Path(os.environ.get('CC_CWD_ROOT', str(HOME / 'claude-ui')))
CWD_ROOT.mkdir(parents=True, exist_ok=True)
DEFAULT_CWD = str(HOME.resolve())

# Static UI files (only used when CC_SERVE_STATIC=1).
DEFAULT_UI_DIR = Path(__file__).resolve().parent.parent / 'ui'
UI_DIR = Path(os.environ.get('CC_UI_DIR', str(DEFAULT_UI_DIR)))

CLAUDE_BIN = os.environ.get('CC_CLAUDE_BIN', 'claude')
# Default model for newly spawned claude subprocesses. Override per-session
# inside the chat with `/model <name>`. Set CC_MODEL_DEFAULT env to change
# the global default.
MODEL_DEFAULT = os.environ.get('CC_MODEL_DEFAULT', 'claude-sonnet-4-5')

# When mounted behind a reverse proxy that strips a path prefix (e.g.
# Caddy mapping /cc/* → /*), set CC_PATH_PREFIX=/cc so upload URLs the
# server emits include the prefix. Default empty (= served at root).
PATH_PREFIX = os.environ.get('CC_PATH_PREFIX', '').rstrip('/')

# Toggle: also serve the UI + upload static files over HTTP from this
# process (default ON). Set CC_SERVE_STATIC=0 if you have Caddy/nginx
# serving them already.
SERVE_STATIC = os.environ.get('CC_SERVE_STATIC', '1') not in ('0', 'false', 'no')

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB after base64 decode

IMAGE_MIMES = {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}

# ───────────────────  state  ───────────────────
class State:
    def __init__(self):
        self.active_id: str | None = None
        self.pid: int | None = None
        self.proc: asyncio.subprocess.Process | None = None
        self.busy: bool = False
        self.current = None        # in-progress assistant message
        self.clients: set = set()
        self.reader_task: asyncio.Task | None = None

state = State()


def log(*a):
    print(time.strftime('%H:%M:%S'), ' '.join(str(x) for x in a),
          file=sys.stderr, flush=True)


def scrubbed_env():
    """Strip env vars that would make claude bypass the user's `claude
    login` (Max subscription) credentials."""
    env = dict(os.environ)
    for k in ('ANTHROPIC_API_KEY', 'CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT'):
        env.pop(k, None)
    return env


# ───────────────────  index + per-session persistence  ───────────────────
def load_index() -> dict:
    if INDEX_FILE.exists():
        try:
            return json.loads(INDEX_FILE.read_text())
        except Exception as e:
            log('load_index failed:', e)
    return {'active': None, 'sessions': []}


def save_index(idx: dict):
    tmp = INDEX_FILE.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(idx, indent=2))
    tmp.replace(INDEX_FILE)


def session_file(sid: str) -> Path:
    return SESS_DIR / f'{sid}.json'


def load_session(sid: str) -> dict | None:
    f = session_file(sid)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception as e:
        log(f'load_session({sid}) failed:', e)
        return None


def save_session(sess: dict):
    f = session_file(sess['id'])
    tmp = f.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(sess, indent=2))
    tmp.replace(f)


def list_sessions_brief():
    """Return [{id, title, createdAt, lastActiveAt, favorite, cwd}].
    Favorites sort to the top, then most-recently-active first."""
    idx = load_index()
    items = list(idx.get('sessions', []))
    items.sort(key=lambda s: (
        not bool(s.get('favorite')),
        -(s.get('lastActiveAt') or s.get('createdAt') or 0),
    ))
    return items


def upsert_index_entry(sess: dict):
    idx = load_index()
    items = idx.get('sessions', [])
    items = [s for s in items if s['id'] != sess['id']]
    items.append({
        'id': sess['id'],
        'title': sess.get('title') or 'New chat',
        'createdAt': sess.get('createdAt'),
        'lastActiveAt': sess.get('lastActiveAt') or sess.get('createdAt'),
        'favorite': bool(sess.get('favorite', False)),
        'cwd': sess.get('cwd'),
    })
    idx['sessions'] = items
    save_index(idx)


def remove_index_entry(sid: str):
    idx = load_index()
    idx['sessions'] = [s for s in idx.get('sessions', []) if s['id'] != sid]
    if idx.get('active') == sid:
        idx['active'] = None
    save_index(idx)


def set_active(sid: str | None):
    idx = load_index()
    idx['active'] = sid
    save_index(idx)


# ───────────────────  current session  ───────────────────
def current_session() -> dict | None:
    return load_session(state.active_id) if state.active_id else None


def derive_title_from_message(text: str) -> str:
    """First ~8 words of the user's first message, claude.ai-style.
    Strips slash commands and image markers."""
    t = (text or '').strip()
    if not t:
        return 'New chat'
    if t.startswith('/'):
        return time.strftime('%Y-%m-%d %H:%M')
    words = t.replace('\n', ' ').split()
    title = ' '.join(words[:8]).strip()
    if len(title) > 60:
        title = title[:57].rstrip() + '…'
    return title or 'New chat'


def append_message(msg: dict):
    sess = current_session()
    if sess is None:
        return
    sess.setdefault('messages', []).append(msg)
    sess['lastActiveAt'] = time.time()
    if msg.get('role') == 'user':
        if not sess.get('title') or sess.get('title') == 'New chat':
            sess['title'] = derive_title_from_message(msg.get('text') or '')
    save_session(sess)
    upsert_index_entry(sess)
    # Mirror the chat into its own working dir so the conversation lives
    # alongside any files claude is writing — easy backup / portability.
    try:
        cwd = sess.get('cwd')
        if cwd:
            chat_file = Path(cwd) / 'chat.json'
            chat_file.write_text(json.dumps({
                'id': sess['id'],
                'title': sess.get('title'),
                'createdAt': sess.get('createdAt'),
                'lastActiveAt': sess.get('lastActiveAt'),
                'messages': sess.get('messages', []),
            }, indent=2))
    except Exception as e:
        log(f'mirror to cwd failed: {e}')


# ───────────────────  broadcast  ───────────────────
async def broadcast(msg: dict):
    if not state.clients:
        return
    data = json.dumps(msg)
    dead = []
    for ws in list(state.clients):
        try: await ws.send(data)
        except Exception: dead.append(ws)
    for ws in dead:
        state.clients.discard(ws)


def state_snapshot() -> dict:
    sess = current_session()
    msgs = (sess or {}).get('messages', [])
    return {
        'type': 'state',
        'activeId': state.active_id,
        'sessions': list_sessions_brief(),
        'messages': msgs,
        'pid': state.pid,
        'busy': state.busy,
        'current': state.current,
        'cwd': (sess or {}).get('cwd') or DEFAULT_CWD,
    }


# ───────────────────  claude subprocess  ───────────────────
async def stop_subprocess(broadcast_end: bool = True):
    if state.proc is None:
        return
    log(f'stopping claude pid={state.pid}')
    try:
        state.proc.terminate()
        try:
            await asyncio.wait_for(state.proc.wait(), 5)
        except asyncio.TimeoutError:
            state.proc.kill()
    except ProcessLookupError:
        pass
    state.proc = None
    state.pid = None
    state.busy = False
    state.current = None
    if state.reader_task:
        state.reader_task.cancel()
        state.reader_task = None
    if broadcast_end:
        await broadcast({'type': 'session_ended'})


async def start_subprocess(resume_id: str | None = None, cwd: str | None = None,
                            system_prompt: str | None = None):
    """Start claude for the current active session. If resume_id is set,
    --resume that session id so prior turns are reloaded. cwd defaults to
    the session's stored working directory. system_prompt, if provided,
    is appended via --append-system-prompt (used by forked sessions)."""
    if state.active_id is None:
        log('start_subprocess: no active session')
        return
    await stop_subprocess(broadcast_end=False)

    if cwd is None:
        sess = current_session()
        cwd = (sess or {}).get('cwd') or DEFAULT_CWD
    if system_prompt is None:
        sess = current_session()
        if sess and sess.get('systemPrompt'):
            system_prompt = sess['systemPrompt']

    sess_model = (current_session() or {}).get('model') or MODEL_DEFAULT

    args = [
        CLAUDE_BIN, '-p',
        '--input-format', 'stream-json',
        '--output-format', 'stream-json',
        '--include-partial-messages',
        '--verbose',
        '--dangerously-skip-permissions',
        '--model', sess_model,
    ]
    if system_prompt:
        args += ['--append-system-prompt', system_prompt]
    if resume_id:
        args += ['--resume', resume_id]
    else:
        args += ['--session-id', state.active_id]

    log(f'spawning claude (active={state.active_id} resume={resume_id} cwd={cwd})')
    state.proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=scrubbed_env(),
        cwd=cwd,
    )
    state.pid = state.proc.pid
    state.busy = False
    state.current = None
    log(f'  → pid {state.pid}')
    state.reader_task = asyncio.create_task(claude_reader())


async def new_session(cwd_override: str | None = None,
                       model_override: str | None = None):
    """Create a new session. Optional overrides come from the New-session
    popup in the UI:

    * cwd_override: absolute path the user wants claude to run in. We
      expand `~` and create the directory if needed. Empty / None → use
      the default ~/claude-ui/<timestamp>/ folder.
    * model_override: model id to pin to this session (also persisted on
      the session JSON so future resumes use the same model).
    """
    sid = str(uuid.uuid4())
    if cwd_override:
        cwd = Path(cwd_override).expanduser().resolve()
        cwd.mkdir(parents=True, exist_ok=True)
    else:
        stamp_fs = time.strftime('%Y-%m-%d_%H-%M-%S')
        cwd = CWD_ROOT / stamp_fs
        cwd.mkdir(parents=True, exist_ok=True)
    sess = {
        'id': sid,
        'title': 'New chat',
        'createdAt': time.time(),
        'lastActiveAt': time.time(),
        'favorite': False,
        'cwd': str(cwd),
        'messages': [],
    }
    if model_override:
        sess['model'] = model_override
    save_session(sess)
    upsert_index_entry(sess)
    state.active_id = sid
    set_active(sid)
    await broadcast({'type': 'spawning',
                     'sessionId': sid, 'title': 'New chat'})
    await start_subprocess(resume_id=None, cwd=str(cwd))
    await broadcast(state_snapshot())


def build_resume_system_prompt(sess: dict, last_n: int = 200) -> str | None:
    """Build a fork-style JSON dump of the session's recent messages so
    claude has the conversation context regardless of whether its own
    session memory survived (e.g., after a system reboot, a different
    claude version, or a moved $HOME). The JSON file on disk is the
    source of truth — claude's memory is the convenient cache."""
    msgs = (sess.get('messages') or [])[-last_n:]
    if not msgs:
        return None
    convo = [{'role': m.get('role'), 'text': m.get('text') or ''} for m in msgs]
    return (
        'You are continuing an existing conversation. Recent dialogue '
        '(last ' + str(len(msgs)) + ' turns, role + text only):\n\n'
        '```json\n' + json.dumps(convo, ensure_ascii=False) + '\n```\n\n'
        'The user already sees these messages in their UI. Continue from '
        'where the dialogue ends without summarizing or repeating.'
    )


async def switch_session(sid: str):
    sess = load_session(sid)
    if not sess:
        log(f'switch: unknown session {sid}')
        return
    state.active_id = sid
    set_active(sid)
    # Tell every connected client we're spinning up so the UI can show a
    # loader. The state snapshot that lands a beat later carries the new
    # PID and clears the loader.
    await broadcast({'type': 'spawning',
                     'sessionId': sid,
                     'title': sess.get('title') or 'New chat'})
    # Always feed the JSON history along with --resume. --resume uses
    # claude's own session memory (fast path); the JSON dump is a
    # safety net for when that memory is gone (e.g. after reboot).
    sys_prompt = sess.get('systemPrompt') or build_resume_system_prompt(sess)
    await start_subprocess(resume_id=sid, cwd=sess.get('cwd'),
                           system_prompt=sys_prompt)
    await broadcast(state_snapshot())


async def delete_session(sid: str):
    if state.active_id == sid:
        await stop_subprocess(broadcast_end=False)
        state.active_id = None
        set_active(None)
    f = session_file(sid)
    try: f.unlink()
    except FileNotFoundError: pass
    try: shutil.rmtree(UPLOAD_DIR / sid)
    except FileNotFoundError: pass
    remove_index_entry(sid)
    items = list_sessions_brief()
    if items:
        await switch_session(items[0]['id'])
    else:
        await broadcast(state_snapshot())


async def rename_session(sid: str, title: str):
    sess = load_session(sid)
    if sess is None:
        return
    sess['title'] = (title or '').strip()[:80] or 'Untitled'
    save_session(sess)
    upsert_index_entry(sess)
    await broadcast({'type': 'sessions', 'sessions': list_sessions_brief()})


async def star_session(sid: str, value: bool):
    sess = load_session(sid)
    if sess is None:
        return
    sess['favorite'] = bool(value)
    save_session(sess)
    upsert_index_entry(sess)
    await broadcast({'type': 'sessions', 'sessions': list_sessions_brief()})


async def fork_session(source_id: str, last_n: int = 200):
    """Create a brand-new claude session that inherits the last N messages
    from `source_id`. The full message log is preloaded into the new
    session AND into a `--append-system-prompt` for claude so it has the
    context when the user sends their next message."""
    src = load_session(source_id)
    if src is None:
        await broadcast({'type': 'error', 'message': f'fork: unknown source {source_id}'})
        return

    sid = str(uuid.uuid4())
    stamp_fs = time.strftime('%Y-%m-%d_%H-%M-%S')
    cwd = CWD_ROOT / stamp_fs
    cwd.mkdir(parents=True, exist_ok=True)

    src_msgs = (src.get('messages') or [])[-last_n:]
    base_title = (src.get('title') or 'New chat').replace(' - fork', '').strip()
    fork_title = f'{base_title} - fork'

    convo = [{'role': m.get('role'), 'text': m.get('text') or ''} for m in src_msgs]
    sys_blob = (
        'This is a FORKED conversation. Here is the prior dialogue as JSON '
        '(role + text only):\n\n'
        '```json\n' + json.dumps(convo, ensure_ascii=False) + '\n```\n\n'
        'When the user sends their next message, continue the conversation '
        'from this point. The user can already see all prior messages in '
        'their UI; do not repeat or summarize them.'
    )

    sess = {
        'id': sid,
        'title': fork_title,
        'createdAt': time.time(),
        'lastActiveAt': time.time(),
        'favorite': False,
        'cwd': str(cwd),
        'forkedFrom': source_id,
        'forkedAt': time.time(),
        'messages': src_msgs,
        'systemPrompt': sys_blob,
    }
    save_session(sess)
    upsert_index_entry(sess)

    state.active_id = sid
    set_active(sid)
    await start_subprocess(resume_id=None, cwd=str(cwd), system_prompt=sys_blob)
    await broadcast(state_snapshot())


# ───────────────────  search  ───────────────────
def _build_snippet(text: str, match_pos: int, match_len: int,
                   context_chars: int = 80) -> dict:
    """Extract a snippet around a match position. Returns {pre, hit, post,
    truncated_left, truncated_right} so the UI can render the highlight
    with prepended/appended ellipses where appropriate.

    Boundaries are snapped to nearby whitespace (the FIRST space after
    `start` and the LAST space before `end`) so words aren't cut in half.
    """
    n = len(text)
    start = max(0, match_pos - context_chars)
    end = min(n, match_pos + match_len + context_chars)
    if start > 0:
        # Move forward to the first whitespace so we don't start mid-word.
        space = text.find(' ', start, match_pos)
        if space != -1 and (match_pos - space) < context_chars:
            start = space + 1
    if end < n:
        # Move backward to the last whitespace within range so we keep
        # as much trailing context as possible without cutting mid-word.
        space = text.rfind(' ', match_pos + match_len, end)
        if space != -1 and (space - (match_pos + match_len)) > 8:
            end = space
    # Collapse newlines + carriage returns to single spaces but leave
    # natural word spacing intact, so pre+hit+post concatenate cleanly.
    def flat(s: str) -> str:
        return s.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
    return {
        'pre':  flat(text[start:match_pos]),
        'hit':  text[match_pos:match_pos + match_len],
        'post': flat(text[match_pos + match_len:end]),
        'truncated_left':  start > 0,
        'truncated_right': end < n,
    }


def search_messages(query: str, role: str = 'all', path_filter: str = '',
                    date_from: float | None = None, date_to: float | None = None,
                    sort: str = 'desc', limit: int = 200) -> list[dict]:
    """Scan every session's messages for substring (case-insensitive)
    matches of `query`. Returns matching messages with snippet metadata so
    the UI can render a SERP-style result list with highlighted hits.

    Filters:
        role: 'all' | 'user' | 'assistant'
        path_filter: substring of the session's cwd to require
        date_from / date_to: epoch seconds, inclusive
        sort: 'desc' (newest first) | 'asc' (oldest first)
        limit: max results to return
    """
    q = (query or '').strip().lower()
    results: list[dict] = []

    sessions = load_index().get('sessions', [])
    # Sort sessions so the result truncation is deterministic
    sessions.sort(key=lambda s: -(s.get('lastActiveAt') or s.get('createdAt') or 0))

    for sess_brief in sessions:
        sid = sess_brief.get('id')
        if not sid:
            continue
        sess = load_session(sid)
        if not sess:
            continue
        cwd = sess.get('cwd') or ''
        if path_filter and path_filter.lower() not in cwd.lower():
            continue
        for m in sess.get('messages', []) or []:
            r = m.get('role') or ''
            if role and role != 'all' and r != role:
                continue
            ts = m.get('ts') or 0
            if date_from is not None and ts < date_from:
                continue
            if date_to is not None and ts > date_to:
                continue
            text = m.get('text') or ''
            if not text:
                continue
            if q:
                pos = text.lower().find(q)
                if pos < 0:
                    continue
                snippet = _build_snippet(text, pos, len(q))
            else:
                # Empty query → return everything matching the filters,
                # with the leading chunk as the snippet.
                snippet = _build_snippet(text, 0, 0)

            results.append({
                'sessionId':    sid,
                'sessionTitle': sess.get('title') or 'Untitled',
                'sessionCwd':   cwd,
                'msgId':        m.get('id'),
                'role':         r,
                'ts':           ts,
                'snippet':      snippet,
                'fullLength':   len(text),
            })

            if len(results) >= limit * 4:
                # Keep scanning a little past `limit` so the sort below
                # has options, but bail out before reading thousands of
                # large messages we'll discard.
                break
        if len(results) >= limit * 4:
            break

    rev = (sort != 'asc')
    results.sort(key=lambda r: r.get('ts') or 0, reverse=rev)
    return results[:limit]


SUMMARY_MODEL = os.environ.get('CC_SUMMARY_MODEL', 'claude-opus-4-6[1m]')


async def summarize_session(sid: str):
    """Spawn a SEPARATE claude subprocess (separate from the active
    session's claude) to generate a Markdown summary of the conversation,
    then save it to <session_cwd>/PROGRESS-<timestamp>.md.

    Why separate: keeps the active session's claude undisturbed, avoids
    polluting its context with the summary prompt, and lets us pin the
    summarizer to Opus 4.6 (1M) regardless of the user's chat model.
    """
    sess = load_session(sid)
    if sess is None:
        await broadcast({'type': 'summary_error',
                         'sessionId': sid,
                         'message': f'unknown session {sid}'})
        return

    msgs = sess.get('messages') or []
    if not msgs:
        await broadcast({'type': 'summary_error',
                         'sessionId': sid,
                         'message': 'session has no messages to summarize'})
        return

    # Build the prompt — JSON dump of the conversation + clear instructions.
    # We strip attachments to keep the input compact (paths-only).
    convo = []
    for m in msgs:
        if not m.get('text'):
            continue
        convo.append({
            'role': m.get('role'),
            'text': m.get('text'),
            'ts':   m.get('ts'),
        })
    prompt = (
        "You are summarizing a developer's chat session with claude code "
        "into a clean Markdown progress report. Read the conversation "
        "below and produce ONE Markdown document with these sections:\n\n"
        "  # <Short title (≤8 words) — derived from the conversation>\n"
        "  ## Overview\n"
        "  ## Decisions made\n"
        "  ## Code / artifacts produced\n"
        "  ## Open questions\n"
        "  ## Next steps\n\n"
        "Rules:\n"
        "  • Output ONLY the Markdown — no preamble, no commentary.\n"
        "  • Preserve key code snippets in fenced blocks.\n"
        "  • Use bullet points for lists; keep prose tight.\n"
        "  • Never invent context not present in the conversation.\n"
        "  • If a section has no content, write a single line: \"_None._\"\n\n"
        "Conversation:\n\n"
        "```json\n" + json.dumps(convo, ensure_ascii=False) + "\n```\n"
    )

    cwd = Path(sess.get('cwd') or DEFAULT_CWD)
    cwd.mkdir(parents=True, exist_ok=True)

    args = [
        CLAUDE_BIN, '-p',
        '--output-format', 'text',
        '--model', SUMMARY_MODEL,
        '--dangerously-skip-permissions',
    ]
    log(f'summarize: spawning {SUMMARY_MODEL} for session {sid} in {cwd}')
    await broadcast({'type': 'summarizing', 'sessionId': sid,
                     'model': SUMMARY_MODEL})

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=scrubbed_env(),
            cwd=str(cwd),
        )
    except Exception as e:
        log(f'summarize spawn failed: {e}')
        await broadcast({'type': 'summary_error',
                         'sessionId': sid,
                         'message': f'spawn failed: {e}'})
        return

    try:
        # Generous timeout — opus 4.6 1M can take a couple minutes for a
        # long conversation. The UI is gated meanwhile.
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(prompt.encode('utf-8')),
            timeout=300,
        )
    except asyncio.TimeoutError:
        try: proc.kill()
        except Exception: pass
        log('summarize: timeout')
        await broadcast({'type': 'summary_error',
                         'sessionId': sid,
                         'message': 'timed out after 5 minutes'})
        return
    except Exception as e:
        log(f'summarize communicate failed: {e}')
        await broadcast({'type': 'summary_error',
                         'sessionId': sid,
                         'message': str(e)})
        return

    if proc.returncode != 0:
        err = (stderr or b'').decode('utf-8', errors='replace')[:500]
        log(f'summarize: claude exit {proc.returncode}: {err}')
        await broadcast({'type': 'summary_error',
                         'sessionId': sid,
                         'message': f'claude exited {proc.returncode}: {err}'})
        return

    md = (stdout or b'').decode('utf-8', errors='replace').strip()
    if not md:
        await broadcast({'type': 'summary_error',
                         'sessionId': sid,
                         'message': 'empty summary returned'})
        return

    out = cwd / f'PROGRESS-{time.strftime("%Y-%m-%d_%H-%M-%S")}.md'
    try:
        out.write_text(md, encoding='utf-8')
    except Exception as e:
        log(f'summarize: write failed: {e}')
        await broadcast({'type': 'summary_error',
                         'sessionId': sid,
                         'message': f'write failed: {e}'})
        return

    log(f'summarize: saved {out} ({len(md)} bytes)')
    await broadcast({'type': 'summary_done',
                     'sessionId': sid,
                     'path': str(out),
                     'bytes': len(md)})


def expand_match(session_id: str, msg_id: str, context_chars: int = 600) -> dict | None:
    """Return a wider snippet for the popup-preview view of a search
    result. Looks up the message by id within the session."""
    sess = load_session(session_id)
    if not sess:
        return None
    for m in sess.get('messages', []) or []:
        if m.get('id') == msg_id:
            text = m.get('text') or ''
            return {
                'sessionId':    session_id,
                'sessionTitle': sess.get('title') or 'Untitled',
                'sessionCwd':   sess.get('cwd') or '',
                'msgId':        msg_id,
                'role':         m.get('role') or '',
                'ts':           m.get('ts') or 0,
                # Whole text — UI can render with markdown / collapse if huge.
                'text':         text,
            }
    return None


# ───────────────────  claude stdout reader  ───────────────────
async def claude_reader():
    proc = state.proc
    in_text_block = False
    try:
        while proc and proc.returncode is None:
            line = await proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            t = obj.get('type')

            if t == 'stream_event':
                ev = obj.get('event', {}) or {}
                ev_type = ev.get('type')
                if ev_type == 'content_block_start':
                    block = ev.get('content_block', {}) or {}
                    if block.get('type') == 'text':
                        in_text_block = True
                        if state.current is None:
                            state.current = {'id': str(uuid.uuid4()),
                                             'text': '', 'started_at': time.time()}
                            await broadcast({'type': 'assistant_start',
                                             'id': state.current['id']})
                    else:
                        in_text_block = False
                elif ev_type == 'content_block_delta':
                    if in_text_block:
                        delta = ev.get('delta', {}) or {}
                        if delta.get('type') == 'text_delta':
                            chunk = delta.get('text', '') or ''
                            if chunk and state.current:
                                state.current['text'] += chunk
                                await broadcast({'type': 'assistant_delta',
                                                 'id': state.current['id'],
                                                 'text': chunk})
                elif ev_type == 'content_block_stop':
                    in_text_block = False
                elif ev_type == 'message_stop':
                    if state.current and state.current.get('text'):
                        msg = {'role': 'assistant',
                               'text': state.current['text'],
                               'ts': time.time(),
                               'id': state.current['id']}
                        append_message(msg)
                        await broadcast({'type': 'assistant_end',
                                         'id': state.current['id']})
                    state.current = None
                continue

            if t == 'result':
                state.busy = False
                if state.current and state.current.get('text'):
                    msg = {'role': 'assistant',
                           'text': state.current['text'],
                           'ts': time.time(),
                           'id': state.current['id']}
                    append_message(msg)
                    await broadcast({'type': 'assistant_end',
                                     'id': state.current['id']})
                state.current = None
                await broadcast({'type': 'turn_done',
                                 'sessions': list_sessions_brief()})
                continue

            if t == 'assistant':
                if state.current is not None:
                    continue
                blocks = obj.get('message', {}).get('content', []) or []
                full_text = ''.join(b.get('text', '') for b in blocks if b.get('type') == 'text')
                if full_text:
                    mid = str(uuid.uuid4())
                    msg = {'role': 'assistant', 'text': full_text,
                           'ts': time.time(), 'id': mid}
                    append_message(msg)
                    await broadcast({'type': 'assistant_start', 'id': mid})
                    await broadcast({'type': 'assistant_delta', 'id': mid, 'text': full_text})
                    await broadcast({'type': 'assistant_end', 'id': mid})

    except asyncio.CancelledError:
        pass
    except Exception as e:
        log('reader error:', e)
        await broadcast({'type': 'error', 'message': f'reader: {e}'})
    finally:
        if state.proc and state.proc.returncode is not None:
            log(f'claude exited (code {state.proc.returncode})')
            state.proc = None
            state.pid = None
            state.busy = False
            state.current = None
            await broadcast({'type': 'session_ended'})


# ───────────────────  send (with attachments)  ───────────────────
def b64_decode(data: str) -> bytes | None:
    try:
        return base64.b64decode(data, validate=True)
    except Exception:
        return None


async def handle_upload(payload: dict) -> dict:
    """Save an uploaded file to disk under the active session's upload dir,
    and return a path *plus* a URL so the UI can render it inline AND
    survive page reloads (browser blob: URLs disappear after refresh)."""
    if state.active_id is None:
        return {'type': 'error', 'message': 'no active session'}
    name = (payload.get('name') or 'file').replace('/', '_')[:120]
    mime = payload.get('mimeType') or 'application/octet-stream'
    raw = payload.get('data') or ''
    bin_data = b64_decode(raw)
    if bin_data is None:
        return {'type': 'error', 'message': 'invalid base64'}
    if len(bin_data) > MAX_UPLOAD_BYTES:
        return {'type': 'error', 'message': f'too large ({len(bin_data)} > {MAX_UPLOAD_BYTES})'}
    fid = str(uuid.uuid4())[:8]
    rel_dir = state.active_id
    out_dir = UPLOAD_DIR / rel_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f'{fid}-{name}'
    path = out_dir / safe_name
    path.write_bytes(bin_data)
    log(f'upload saved: {path} ({len(bin_data)} bytes, {mime})')
    # The URL the UI will use — prefixed when behind a reverse proxy.
    url = f'{PATH_PREFIX}/uploads/{rel_dir}/{safe_name}'
    return {
        'type': 'upload_ok',
        'fileId': fid,
        'path': str(path),
        'url': url,
        'name': name,
        'mimeType': mime,
        'size': len(bin_data),
    }


async def send_to_claude(text: str, attachments: list[dict]):
    if state.active_id is None:
        await new_session()

    if state.proc is None or state.proc.returncode is not None:
        await start_subprocess(resume_id=state.active_id)

    msg_id = str(uuid.uuid4())
    user_msg = {
        'role': 'user', 'text': text, 'ts': time.time(), 'id': msg_id,
    }
    if attachments:
        user_msg['attachments'] = [
            {'name': a.get('name'), 'mimeType': a.get('mimeType'),
             'size': a.get('size'), 'path': a.get('path'), 'url': a.get('url')}
            for a in attachments
        ]

    append_message(user_msg)
    state.busy = True
    await broadcast({'type': 'user', 'msg': user_msg})

    content = []
    if text:
        content.append({'type': 'text', 'text': text})

    file_lines = []
    for a in attachments or []:
        path = a.get('path')
        mime = a.get('mimeType') or 'application/octet-stream'
        if not path or not Path(path).exists():
            continue
        if mime in IMAGE_MIMES:
            try:
                data = Path(path).read_bytes()
                content.append({
                    'type': 'image',
                    'source': {
                        'type': 'base64',
                        'media_type': mime,
                        'data': base64.b64encode(data).decode('ascii'),
                    },
                })
            except Exception as e:
                log(f'failed to inline image {path}: {e}')
                file_lines.append(f'(attached: {path})')
        else:
            file_lines.append(f'(attached: {path}  [{a.get("name")}, {mime}])')
    if file_lines:
        joined = '\n'.join(file_lines)
        if content and content[0].get('type') == 'text':
            content[0]['text'] = (content[0]['text'] + '\n\n' + joined).strip()
        else:
            content.insert(0, {'type': 'text', 'text': joined})

    if not content:
        log('send: empty content, skipping')
        return

    payload = {
        'type': 'user',
        'message': {'role': 'user', 'content': content},
    }
    try:
        state.proc.stdin.write((json.dumps(payload) + '\n').encode('utf-8'))
        await state.proc.stdin.drain()
    except Exception as e:
        log('write error:', e)
        await broadcast({'type': 'error', 'message': f'send failed: {e}'})


# ───────────────────  HTTP static + uploads  ───────────────────
def _safe_join(root: Path, rel: str) -> Path | None:
    """Resolve `rel` under `root`, refusing any path that escapes `root`."""
    try:
        rel = rel.lstrip('/')
        target = (root / rel).resolve()
        root_resolved = root.resolve()
        if root_resolved not in target.parents and target != root_resolved:
            return None
        return target
    except Exception:
        return None


def _http_response(status: int, body: bytes, content_type: str = 'text/plain; charset=utf-8',
                   extra_headers: list[tuple[str, str]] | None = None) -> Response:
    headers = [
        ('Content-Type', content_type),
        ('Content-Length', str(len(body))),
        ('Cache-Control', 'no-cache'),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    return Response(HTTPStatus(status), HTTPStatus(status).phrase,
                    Headers(headers), body)


def _serve_file(path: Path) -> Response:
    if not path.exists() or not path.is_file():
        return _http_response(404, b'Not Found')
    ctype, _ = mimetypes.guess_type(str(path))
    if not ctype:
        ctype = 'application/octet-stream'
    try:
        body = path.read_bytes()
    except Exception as e:
        log(f'serve_file({path}) failed: {e}')
        return _http_response(500, f'read error: {e}'.encode())
    return _http_response(200, body, ctype)


def http_handler(connection, request):
    """websockets `process_request` hook. Return None to upgrade to WS;
    return a Response to serve HTTP instead. Runs for every incoming
    request, including /ws upgrades."""
    path = request.path
    # Strip query string
    if '?' in path:
        path = path.split('?', 1)[0]

    # WS endpoints — return None so websockets handles the upgrade.
    if path in ('/ws', '/'):
        # `/` is an HTTP page request unless it has the Upgrade header,
        # so check for that.
        upgrade = (request.headers.get('Upgrade') or '').lower()
        if upgrade == 'websocket':
            return None
        # else: fall through to HTTP routing

    if not SERVE_STATIC:
        return _http_response(404, b'static serving disabled (CC_SERVE_STATIC=0)')

    # /healthz — used by tunnel watchdogs
    if path == '/healthz':
        return _http_response(200, b'ok')

    # /uploads/<sid>/<file>
    if path.startswith('/uploads/'):
        rel = path[len('/uploads/'):]
        target = _safe_join(UPLOAD_DIR, rel)
        if target is None:
            return _http_response(403, b'Forbidden')
        return _serve_file(target)

    # /assets/* served from UI_DIR/assets/* (future use)
    if path.startswith('/assets/'):
        rel = path[len('/'):]
        target = _safe_join(UI_DIR, rel)
        if target is None:
            return _http_response(403, b'Forbidden')
        return _serve_file(target)

    # Otherwise: index.html (SPA-style routing — every other path falls
    # through to the static UI).
    index = UI_DIR / 'index.html'
    if not index.exists():
        return _http_response(
            500,
            f'index.html not found at {index}\n'
            f'Set CC_UI_DIR to the directory containing index.html.\n'.encode())
    return _serve_file(index)


# ───────────────────  WS handler  ───────────────────
async def handle_client(websocket):
    state.clients.add(websocket)
    log(f'client connected (total: {len(state.clients)})')
    try:
        await websocket.send(json.dumps(state_snapshot()))

        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            cmd = msg.get('type')

            if cmd == 'send':
                text = (msg.get('text') or '').strip()
                attachments = msg.get('attachments') or []
                if text or attachments:
                    await send_to_claude(text, attachments)
            elif cmd == 'new':
                await new_session(
                    cwd_override=msg.get('cwd'),
                    model_override=msg.get('model'),
                )
            elif cmd == 'summarize':
                sid = msg.get('id') or state.active_id
                if sid:
                    asyncio.create_task(summarize_session(sid))
            elif cmd == 'switch':
                sid = msg.get('id')
                if sid: await switch_session(sid)
            elif cmd == 'delete':
                sid = msg.get('id')
                if sid: await delete_session(sid)
            elif cmd == 'rename':
                sid = msg.get('id'); title = msg.get('title')
                if sid: await rename_session(sid, title)
            elif cmd == 'star':
                sid = msg.get('id'); val = bool(msg.get('value'))
                if sid: await star_session(sid, val)
            elif cmd == 'fork':
                sid = msg.get('id') or state.active_id
                last_n = int(msg.get('lastN') or 200)
                if sid: await fork_session(sid, last_n)
            elif cmd == 'sessions':
                await websocket.send(json.dumps({
                    'type': 'sessions', 'sessions': list_sessions_brief()}))
            elif cmd == 'upload':
                resp = await handle_upload(msg)
                await websocket.send(json.dumps(resp))
            elif cmd == 'search':
                results = search_messages(
                    query=msg.get('q') or '',
                    role=(msg.get('role') or 'all'),
                    path_filter=msg.get('path') or '',
                    date_from=msg.get('dateFrom'),
                    date_to=msg.get('dateTo'),
                    sort=(msg.get('sort') or 'desc'),
                    limit=int(msg.get('limit') or 200),
                )
                await websocket.send(json.dumps({
                    'type': 'search_results',
                    'reqId': msg.get('reqId'),
                    'results': results,
                }))
            elif cmd == 'search_expand':
                detail = expand_match(
                    msg.get('sessionId') or '',
                    msg.get('msgId') or '',
                )
                await websocket.send(json.dumps({
                    'type': 'search_detail',
                    'reqId': msg.get('reqId'),
                    'result': detail,
                }))
            elif cmd == 'state':
                await websocket.send(json.dumps(state_snapshot()))
            elif cmd == 'stop':
                await stop_subprocess()
            else:
                log('unknown cmd:', cmd)

    except websockets.ConnectionClosed:
        pass
    except Exception as e:
        log('client error:', e)
    finally:
        state.clients.discard(websocket)
        log(f'client disconnected (remaining: {len(state.clients)})')


# ───────────────────  bootstrap  ───────────────────
async def main():
    idx = load_index()
    state.active_id = idx.get('active')
    if state.active_id and not load_session(state.active_id):
        state.active_id = None
        set_active(None)

    log(f'cc-server starting')
    log(f'  host       : {HOST}:{PORT}')
    log(f'  data dir   : {DATA_DIR}')
    log(f'  cwd root   : {CWD_ROOT}')
    log(f'  ui dir     : {UI_DIR}')
    log(f'  serve UI   : {SERVE_STATIC}')
    log(f'  path pfx   : {PATH_PREFIX or "(none)"}')
    log(f'  model      : {MODEL_DEFAULT}')
    log(f'  active     : {state.active_id}')
    if SERVE_STATIC:
        log(f'  open       : http://{HOST}:{PORT}/')
    else:
        log(f'  ws only    : ws://{HOST}:{PORT}{PATH_PREFIX}/ws')

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try: loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError: pass

    async with websockets.serve(
            handle_client, HOST, PORT,
            max_size=16 * 1024 * 1024,
            process_request=http_handler):
        await stop_event.wait()

    log('shutting down')
    await stop_subprocess(broadcast_end=False)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
