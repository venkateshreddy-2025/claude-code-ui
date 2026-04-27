#!/usr/bin/env python3
"""cc-talk — peek + send + spawn into any chat the bridge owns, from the CLI.

The bridge holds a Worker per session; each Worker is a long-lived
`claude` subprocess. This script proves you can talk to the SAME
worker the browser/Telegram talks to, just over the bridge's
WebSocket. No new claude process for `send`; same context; full
continuity. `spawn` does start a new worker.

Usage:
    cc-talk.py list                          # list all sessions (sid, chatId, persona, pid)
    cc-talk.py history <sid|title-fuzzy>     # dump full message log
    cc-talk.py send <sid> "your message"     # send + stream the reply
    cc-talk.py tail <sid>                    # follow live, no send
    cc-talk.py spawn <persona> "<brief>"     # NEW: create a session with persona,
                                             #      send brief as first turn,
                                             #      print sid+chatId+pid+stream
    cc-talk.py delete <sid>                  # NEW: tear down session + worker

Examples:
    cc-talk.py list
    cc-talk.py spawn claudy "Build a single-file HTML calculator at /tmp/calc.html. Done = ARTIFACT marker pointing at the file."
    cc-talk.py send c2 "switch the theme to dark"
    cc-talk.py history c2
    cc-talk.py delete c2                     # ALWAYS ask the user first

Persona ids: aurora · claudy · curator · flash · sage · pallavi · lumen · verdict
(See runtime/_data/personas.json — `cc-talk.py list` resolves the current set.)

Connects to ws://127.0.0.1:18793/ws by default; override with
$CC_WS_URL.
"""
from __future__ import annotations
import asyncio, json, os, sys, time
from pathlib import Path

WS_URL = os.environ.get('CC_WS_URL', 'ws://127.0.0.1:18793/ws')

# Default to <repo>/runtime/ — same convention as cc-server.py. tools/
# is at <repo>/tools/, so the parent of this script's parent is the repo.
_REPO_ROOT = Path(__file__).resolve().parent.parent
CWD_ROOT = Path(os.environ.get('CC_CWD_ROOT', str(_REPO_ROOT / 'runtime')))
DATA_DIR = Path(os.environ.get('CC_DATA_DIR', str(CWD_ROOT / '_data')))
SESS_DIR = DATA_DIR / 'cc-sessions'
INDEX_FILE = SESS_DIR / 'index.json'


def load_index() -> dict:
    if not INDEX_FILE.exists():
        return {'active': None, 'sessions': []}
    return json.loads(INDEX_FILE.read_text())


def resolve_sid(needle: str) -> str | None:
    """Match by exact sid prefix, then chatId, then fuzzy title."""
    idx = load_index()
    sessions = idx.get('sessions', [])
    needle_l = needle.lower()
    for s in sessions:
        if s['id'] == needle or s['id'].startswith(needle):
            return s['id']
    for s in sessions:
        if (s.get('chatId') or '').lower() == needle_l:
            return s['id']
    matches = [s for s in sessions
               if needle_l in (s.get('title') or '').lower()]
    if len(matches) == 1:
        return matches[0]['id']
    if len(matches) > 1:
        print(f'ambiguous: {len(matches)} titles match', file=sys.stderr)
        for s in matches[:5]:
            print(f'  {s["id"][:8]}  {s.get("title")}', file=sys.stderr)
        return None
    return None


def cmd_list() -> None:
    idx = load_index()
    sessions = sorted(idx.get('sessions', []),
                      key=lambda s: -(s.get('lastActiveAt') or 0))
    print(f'{len(sessions)} session(s):')
    for s in sessions:
        flag = '*' if idx.get('active') == s['id'] else ' '
        saved = s.get('lastSavedAt')
        ago_active = _ago(s.get('lastActiveAt'))
        ago_saved  = _ago(saved) if saved else '—'
        print(f' {flag} {s["id"][:8]}  active:{ago_active:>10}  saved:{ago_saved:>10}  {s.get("title","")[:60]}')


def _ago(ts: float | None) -> str:
    if not ts:
        return '—'
    d = max(0, time.time() - float(ts))
    if d < 60:    return f'{int(d)}s'
    if d < 3600:  return f'{int(d/60)}m'
    if d < 86400: return f'{int(d/3600)}h'
    return f'{int(d/86400)}d'


def cmd_history(needle: str) -> None:
    sid = resolve_sid(needle)
    if not sid:
        print(f'no session matches "{needle}"', file=sys.stderr); sys.exit(1)
    p = SESS_DIR / f'{sid}.json'
    sess = json.loads(p.read_text())
    msgs = sess.get('messages') or []
    print(f'═══ {sess.get("title")} ═══')
    print(f'sid:    {sid}')
    print(f'cwd:    {sess.get("cwd")}')
    print(f'msgs:   {len(msgs)}')
    print(f'saved:  {_ago(sess.get("lastSavedAt"))}')
    print()
    for i, m in enumerate(msgs):
        role = m.get('role', '?')
        text = (m.get('text') or '').rstrip()
        ts   = time.strftime('%H:%M:%S', time.localtime(m.get('ts', 0)))
        atts = m.get('attachments') or []
        arts = m.get('artifacts')   or []
        print(f'─── [{i}] {role:9s} {ts} ───')
        print(text or '(empty)')
        if atts: print(f'  📎 {len(atts)} attachment(s)')
        if arts: print(f'  🎨 {len(arts)} artifact(s)')
        print()


async def cmd_send(sid_arg: str, text: str, *, follow_only: bool = False) -> None:
    """Connect to the bridge's WS, send a turn (unless follow_only),
    stream until turn_done."""
    try:
        import websockets
    except ImportError:
        print('pip install websockets', file=sys.stderr); sys.exit(1)
    sid = resolve_sid(sid_arg)
    if not sid:
        print(f'no session matches "{sid_arg}"', file=sys.stderr); sys.exit(1)
    print(f'→ ws connect to {WS_URL}', file=sys.stderr)
    async with websockets.connect(WS_URL, max_size=None) as ws:
        # The bridge sends a `state` snapshot on connect — ignore it
        # except as confirmation we're up.
        await asyncio.wait_for(ws.recv(), timeout=5)
        if not follow_only:
            payload = {'type': 'send', 'id': sid, 'text': text}
            await ws.send(json.dumps(payload))
            print(f'→ sent to sid={sid[:8]}: {text[:80]}', file=sys.stderr)
        else:
            print(f'→ following sid={sid[:8]} (no send)', file=sys.stderr)

        cur_id = None
        async for raw in ws:
            try: m = json.loads(raw)
            except Exception: continue
            t   = m.get('type')
            msg_sid = m.get('sessionId') or m.get('id') or None
            # Filter: only print events for our target session.
            # `assistant_*` events have `id` (msg id) but no session id;
            # treat them as relevant during an active stream.
            if t in ('user', 'turn_done', 'save_started', 'save_done', 'save_error'):
                if msg_sid and msg_sid != sid: continue

            if t == 'assistant_start':
                cur_id = m.get('id')
                if cur_id and (msg_sid is None or msg_sid == sid):
                    print('\n┌─── assistant ───')
            elif t == 'assistant_delta':
                if m.get('id') == cur_id:
                    sys.stdout.write(m.get('text') or '')
                    sys.stdout.flush()
            elif t == 'assistant_end':
                if m.get('id') == cur_id:
                    print('\n└─── end ───')
                    cur_id = None
            elif t == 'turn_done':
                print('\n[turn_done]', file=sys.stderr)
                if not follow_only:
                    return
            elif t == 'save_started':
                print(f'[save_started for {(msg_sid or "?")[:8]}]', file=sys.stderr)
            elif t == 'save_done':
                print(f'[save_done   for {(msg_sid or "?")[:8]}]', file=sys.stderr)
            elif t == 'error':
                print(f'[error] {m.get("message")}', file=sys.stderr)


async def cmd_spawn(persona_id: str, brief: str) -> None:
    """Create a new session with `persona_id`, send `brief` as the first
    user turn, stream until turn_done. Prints the new sid + chatId + pid
    so the caller (Aurora) can track and follow up later.

    Implementation: WS sends `{type:'new', persona:<id>}` → bridge replies
    with a `spawning` event carrying the new sid → we fire the brief as
    a `send` turn → stream assistant deltas to stdout → done."""
    try:
        import websockets
    except ImportError:
        print('pip install websockets', file=sys.stderr); sys.exit(1)
    if not persona_id:
        print('spawn: persona_id required', file=sys.stderr); sys.exit(1)
    print(f'→ ws connect to {WS_URL}', file=sys.stderr)
    async with websockets.connect(WS_URL, max_size=None) as ws:
        await asyncio.wait_for(ws.recv(), timeout=5)  # initial state
        await ws.send(json.dumps({'type': 'new', 'persona': persona_id}))
        new_sid = None
        chat_id = None
        # Wait for the bridge to broadcast the spawn.
        for _ in range(50):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
            except asyncio.TimeoutError:
                break
            try: m = json.loads(raw)
            except Exception: continue
            if m.get('type') == 'spawning' and m.get('sessionId'):
                new_sid = m['sessionId']
            if m.get('type') in ('sessions', 'state') and not new_sid:
                # Some servers broadcast `sessions` on creation
                items = m.get('sessions') or []
                if items:
                    # Newest first by createdAt
                    items.sort(key=lambda s: -(s.get('createdAt') or 0))
                    new_sid = items[0].get('id')
            if new_sid:
                # Look up chatId from the freshly-saved index file
                try:
                    p = SESS_DIR / f'{new_sid}.json'
                    if p.exists():
                        s = json.loads(p.read_text())
                        chat_id = s.get('chatId')
                except Exception: pass
                break
        if not new_sid:
            print('spawn: bridge never reported a new sid', file=sys.stderr)
            sys.exit(1)
        print(f'✓ spawned sid={new_sid[:8]}  chatId={chat_id or "?"}  persona={persona_id}',
              file=sys.stderr)
        # Now send the brief as the first turn.
        await ws.send(json.dumps({'type': 'send', 'id': new_sid, 'text': brief}))
        print(f'→ sent brief ({len(brief)} chars)', file=sys.stderr)
        cur_id = None
        async for raw in ws:
            try: m = json.loads(raw)
            except Exception: continue
            t = m.get('type')
            msg_sid = m.get('sessionId') or m.get('id') or None
            if t in ('user','turn_done','save_started','save_done','save_error'):
                if msg_sid and msg_sid != new_sid: continue
            if t == 'assistant_start':
                cur_id = m.get('id')
                if cur_id and (msg_sid is None or msg_sid == new_sid):
                    print('\n┌─── assistant ───')
            elif t == 'assistant_delta':
                if m.get('id') == cur_id:
                    sys.stdout.write(m.get('text') or ''); sys.stdout.flush()
            elif t == 'assistant_end':
                if m.get('id') == cur_id:
                    print('\n└─── end ───'); cur_id = None
            elif t == 'turn_done':
                print(f'\n[turn_done sid={new_sid[:8]} chatId={chat_id or "?"}]',
                      file=sys.stderr)
                return
            elif t == 'error':
                print(f'[error] {m.get("message")}', file=sys.stderr)


async def cmd_delete(sid_arg: str) -> None:
    """Send `{type:'delete', id:<sid>}` to the bridge. The bridge stops
    the worker, cancels routines for this session, and removes its files.
    Use ONLY after asking the user."""
    try:
        import websockets
    except ImportError:
        print('pip install websockets', file=sys.stderr); sys.exit(1)
    sid = resolve_sid(sid_arg)
    if not sid:
        print(f'no session matches "{sid_arg}"', file=sys.stderr); sys.exit(1)
    async with websockets.connect(WS_URL, max_size=None) as ws:
        await asyncio.wait_for(ws.recv(), timeout=5)
        await ws.send(json.dumps({'type':'delete','id':sid}))
        # Drain a few responses so the bridge can process before we close.
        for _ in range(5):
            try: await asyncio.wait_for(ws.recv(), timeout=1)
            except asyncio.TimeoutError: break
        print(f'✓ delete sent for sid={sid[:8]}', file=sys.stderr)


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(0)
    cmd = sys.argv[1]
    if cmd == 'list':
        cmd_list()
    elif cmd == 'history' and len(sys.argv) >= 3:
        cmd_history(sys.argv[2])
    elif cmd == 'send' and len(sys.argv) >= 4:
        asyncio.run(cmd_send(sys.argv[2], ' '.join(sys.argv[3:])))
    elif cmd == 'tail' and len(sys.argv) >= 3:
        asyncio.run(cmd_send(sys.argv[2], '', follow_only=True))
    elif cmd == 'spawn' and len(sys.argv) >= 4:
        asyncio.run(cmd_spawn(sys.argv[2], ' '.join(sys.argv[3:])))
    elif cmd == 'delete' and len(sys.argv) >= 3:
        asyncio.run(cmd_delete(sys.argv[2]))
    else:
        print(__doc__); sys.exit(1)


if __name__ == '__main__':
    main()
