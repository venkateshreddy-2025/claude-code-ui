"""Routines = scheduled wake-ups the user requested ("text me at 4:30",
"every hour check my emails"). The CHAT'S CLAUDE owns the implementation
end-to-end: it writes the python, picks the scheduling mechanism, starts
the process. The bridge just keeps a REGISTRY so the user can list every
active routine and kill any of them without going back into the chat.

Scope of this module — deliberately small:
    • Routine model + JSON persistence at <DATA_DIR>/routines.json
    • Add / cancel / kill / list operations
    • PID-based kill (SIGTERM → SIGKILL fallback)
    • Optional liveness sweep so dead PIDs disappear from the UI

Scope of this module — deliberately NOT here:
    • No in-process scheduler. Claude picks its own mechanism
      (background python with `time.sleep`, crontab, launchd,
      `at` command — whatever fits).
    • No script template. Claude writes the file from scratch
      using its Write tool, in the chat's cwd.
    • No injection plumbing. Claude knows the bridge's WS URL
      from the system prompt; it can use cc-talk.py as a reference
      or write its own one-liner.

Marker contract claude uses (taught via GLOBAL_SYSTEM_PROMPT):

    |ROUTINE| {"title": "4:30 text reminder",
               "prompt": "[wake-up] text the user about X",
               "schedule": "at 4:30 PM today",
               "script_path": "/abs/path/script.py",
               "pid": 12345,
               "mechanism": "background python (time.sleep)"} |

    |ROUTINE_CANCEL| r-abc123def0 |

The bridge strips both markers from the persisted reply (same as
|SEND| / |ARTIFACT|), parses the JSON, registers / cancels, and
broadcasts an updated routine list.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable


# ───────────────────────  Routine model  ───────────────────────

@dataclass
class Routine:
    id: str
    sid: str                          # session this routine belongs to
    title: str                        # one-line human description
    prompt: str                       # what claude says will get injected when fired
    schedule: str                     # human description: "every hour", "at 4:30 PM today"
    user_request: str = ''            # original user text verbatim
    script_path: str = ''             # absolute path of the .py claude wrote
    pid: int | None = None            # the process claude started (kill target)
    mechanism: str = ''               # "background python", "crontab", "launchd", etc.
    cron_marker: str = ''             # optional: a unique string in claude's crontab line
                                      # for cleanup-on-cancel via `crontab -l | grep -v`
    created_at: float = 0.0
    last_fire_at: float | None = None
    fire_count: int = 0
    enabled: bool = True              # false after cancel; routine kept for audit
    cancelled_at: float | None = None
    cancel_reason: str = ''           # 'user', 'process_died', 'session_deleted'

    def to_dict(self) -> dict:
        return {
            'id':            self.id,
            'sid':           self.sid,
            'title':         self.title,
            'prompt':        self.prompt,
            'schedule':      self.schedule,
            'user_request':  self.user_request,
            'script_path':   self.script_path,
            'pid':           self.pid,
            'mechanism':     self.mechanism,
            'cron_marker':   self.cron_marker,
            'created_at':    self.created_at,
            'last_fire_at':  self.last_fire_at,
            'fire_count':    self.fire_count,
            'enabled':       self.enabled,
            'cancelled_at':  self.cancelled_at,
            'cancel_reason': self.cancel_reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'Routine':
        return cls(
            id            = d['id'],
            sid           = d['sid'],
            title         = d.get('title') or '',
            prompt        = d.get('prompt') or '',
            schedule      = d.get('schedule') or '',
            user_request  = d.get('user_request') or '',
            script_path   = d.get('script_path') or '',
            pid           = d.get('pid'),
            mechanism     = d.get('mechanism') or '',
            cron_marker   = d.get('cron_marker') or '',
            created_at    = d.get('created_at') or time.time(),
            last_fire_at  = d.get('last_fire_at'),
            fire_count    = d.get('fire_count') or 0,
            enabled       = d.get('enabled', True),
            cancelled_at  = d.get('cancelled_at'),
            cancel_reason = d.get('cancel_reason') or '',
        )

    def is_alive(self) -> bool:
        """True if this routine's PID is still running. False for
        routines without a PID (e.g. cron-based — we can't easily
        check) and for ones whose process has exited."""
        if not self.enabled or self.pid is None:
            return False
        try:
            os.kill(self.pid, 0)   # signal 0 = existence check
            return True
        except (ProcessLookupError, PermissionError):
            return False
        except OSError:
            return False

    def brief(self) -> dict:
        """JSON-serializable summary for the UI."""
        return {
            'id':           self.id,
            'sid':          self.sid,
            'title':        self.title,
            'prompt':       self.prompt[:300] + ('…' if len(self.prompt) > 300 else ''),
            'schedule':     self.schedule,
            'mechanism':    self.mechanism,
            'request':      self.user_request[:200],
            'scriptPath':   self.script_path,
            'pid':          self.pid,
            'alive':        self.is_alive(),
            'enabled':      self.enabled,
            'createdAt':    self.created_at,
            'lastFireAt':   self.last_fire_at,
            'fires':        self.fire_count,
            'cancelledAt':  self.cancelled_at,
            'cancelReason': self.cancel_reason,
        }


# ───────────────────────  Manager  ───────────────────────

class RoutineManager:
    """Owns the in-memory list of routines, persists to JSON, runs a
    liveness sweep so dead PIDs auto-disappear from the UI list."""

    def __init__(self, data_dir: Path,
                 broadcast: Callable[[dict], Awaitable[None]],
                 log: Callable[..., None]):
        self.data_dir      = data_dir
        self.routines_file = data_dir / 'routines.json'
        self._broadcast    = broadcast
        self._log          = log
        self.routines: list[Routine] = []
        self._sweep_task: asyncio.Task | None = None
        self.load()

    # ─── persistence ───
    def load(self) -> None:
        if not self.routines_file.exists():
            self.routines = []
            return
        try:
            raw = json.loads(self.routines_file.read_text())
            self.routines = [Routine.from_dict(d) for d in raw.get('routines', [])]
            self._log(f'routines: loaded {len(self.routines)}')
        except Exception as e:
            self._log(f'routines: load failed: {e}')
            self.routines = []

    def save(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.routines_file.with_suffix('.json.tmp')
            tmp.write_text(json.dumps({
                'routines': [r.to_dict() for r in self.routines],
            }, indent=2))
            tmp.replace(self.routines_file)
        except Exception as e:
            self._log(f'routines: save failed: {e}')

    # ─── public ops ───
    def register(self, *, sid: str, spec: dict) -> Routine | None:
        """Register a new routine from a |ROUTINE| marker spec dict.
        Returns the created Routine, or None if the spec was invalid."""
        title = (spec.get('title') or '').strip()[:140]
        prompt = (spec.get('prompt') or '').strip()
        if not title and not prompt:
            return None
        r = Routine(
            id           = 'r-' + uuid.uuid4().hex[:10],
            sid          = sid,
            title        = title or 'Routine',
            prompt       = prompt,
            schedule     = (spec.get('schedule') or '').strip()[:200],
            user_request = (spec.get('user_request') or '').strip()[:500],
            script_path  = (spec.get('script_path') or spec.get('script') or '').strip(),
            pid          = self._coerce_pid(spec.get('pid')),
            mechanism    = (spec.get('mechanism') or '').strip()[:80],
            cron_marker  = (spec.get('cron_marker') or '').strip()[:200],
            created_at   = time.time(),
        )
        self.routines.append(r)
        self.save()
        self._log(f'routines: + {r.id} sid={sid[:8]} pid={r.pid} '
                  f'sched="{r.schedule[:60]}" mech="{r.mechanism}"')
        return r

    def cancel(self, routine_id: str, *, reason: str = 'user') -> tuple[bool, str]:
        """Mark cancelled + kill the PID (best-effort) + try crontab
        cleanup if claude registered a cron_marker. Returns (ok, info)."""
        r = self._by_id(routine_id)
        if r is None:
            return False, 'unknown id'
        if not r.enabled:
            return True, 'already cancelled'

        info_parts: list[str] = []

        # 1. Kill the PID if any
        if r.pid:
            killed = self._kill_pid(r.pid)
            info_parts.append(f'pid={r.pid} {"killed" if killed else "not running"}')

        # 2. Optional crontab cleanup
        if r.cron_marker:
            removed = self._remove_cron_line(r.cron_marker)
            if removed:
                info_parts.append(f'crontab: removed {removed} line(s)')

        r.enabled = False
        r.cancelled_at = time.time()
        r.cancel_reason = reason
        self.save()
        self._log(f'routines: cancel {r.id} ({reason}) — {", ".join(info_parts) or "no kill action"}')
        return True, ', '.join(info_parts) or 'cancelled'

    def remove(self, routine_id: str) -> bool:
        """Hard delete an entry from the registry. Cancel first if
        still enabled — caller should usually cancel() then remove()."""
        before = len(self.routines)
        self.routines = [r for r in self.routines if r.id != routine_id]
        if len(self.routines) != before:
            self.save()
            self._log(f'routines: remove {routine_id}')
            return True
        return False

    def cancel_all_for_session(self, sid: str, *, reason: str = 'session_deleted') -> int:
        n = 0
        for r in self.routines:
            if r.sid == sid and r.enabled:
                self.cancel(r.id, reason=reason)
                n += 1
        return n

    def list_for_session(self, sid: str, *, only_enabled: bool = True) -> list[Routine]:
        return [r for r in self.routines
                if r.sid == sid and (r.enabled or not only_enabled)]

    def all_brief(self) -> list[dict]:
        # Newest first.
        return [r.brief() for r in sorted(self.routines,
                                          key=lambda x: -x.created_at)]

    # ─── liveness sweep (optional background task) ───
    def start_sweep(self, interval_s: int = 30) -> None:
        if self._sweep_task and not self._sweep_task.done():
            return
        self._sweep_task = asyncio.create_task(self._sweep_loop(interval_s))

    async def _sweep_loop(self, interval: int) -> None:
        self._log('routines: liveness sweep started')
        while True:
            try:
                changed = False
                for r in self.routines:
                    if not r.enabled or not r.pid:
                        continue
                    if not r.is_alive():
                        r.enabled = False
                        r.cancelled_at = time.time()
                        r.cancel_reason = 'process_died'
                        changed = True
                        self._log(f'routines: sweep — {r.id} pid={r.pid} died')
                if changed:
                    self.save()
                    try:
                        await self._broadcast({'type': 'routines',
                                               'routines': self.all_brief()})
                    except Exception:
                        pass
            except asyncio.CancelledError:
                return
            except Exception as e:
                self._log(f'routines: sweep error: {e}')
            await asyncio.sleep(interval)

    # ─── helpers ───
    def _by_id(self, routine_id: str) -> Routine | None:
        for r in self.routines:
            if r.id == routine_id:
                return r
        return None

    @staticmethod
    def _coerce_pid(v: Any) -> int | None:
        if v is None: return None
        try:
            n = int(v)
            return n if n > 0 else None
        except (ValueError, TypeError):
            return None

    def _kill_pid(self, pid: int) -> bool:
        """Send SIGTERM, then SIGKILL if it's still alive 1.5s later.
        Returns True if a signal was delivered to a live process."""
        try:
            os.kill(pid, 0)            # exists?
        except (ProcessLookupError, PermissionError, OSError):
            return False
        try:
            os.kill(pid, signal.SIGTERM)
            self._log(f'routines: SIGTERM pid={pid}')
        except OSError:
            return False
        # Brief wait, then SIGKILL fallback (don't await — we need
        # this method synchronous for the WS handler).
        deadline = time.time() + 1.5
        while time.time() < deadline:
            time.sleep(0.1)
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, PermissionError, OSError):
                return True            # already dead
        try:
            os.kill(pid, signal.SIGKILL)
            self._log(f'routines: SIGKILL pid={pid}')
        except OSError:
            pass
        return True

    def _remove_cron_line(self, marker: str) -> int:
        """Best-effort: re-write the user's crontab without any line
        containing `marker`. Returns number of lines removed (0 if
        crontab unavailable or no matches). Non-fatal on failure."""
        try:
            import subprocess
            res = subprocess.run(['crontab', '-l'],
                                 capture_output=True, text=True, timeout=4)
            if res.returncode != 0:
                return 0
            lines = res.stdout.splitlines()
            kept = [ln for ln in lines if marker not in ln]
            removed = len(lines) - len(kept)
            if removed == 0:
                return 0
            new_crontab = '\n'.join(kept) + ('\n' if kept else '')
            subprocess.run(['crontab', '-'],
                           input=new_crontab, text=True, timeout=4, check=True)
            return removed
        except Exception as e:
            self._log(f'routines: crontab cleanup skipped: {e}')
            return 0


# ───────────────────────  marker parsing  ───────────────────────

_ROUTINE_RE        = re.compile(
    r'\|\s*ROUTINE\s*\|\s*(\{.*?\})\s*\|',
    re.DOTALL,
)
_ROUTINE_CANCEL_RE = re.compile(r'\|\s*ROUTINE_CANCEL\s*\|\s*([^|\n]+?)\s*\|')


def extract_routine_markers(text: str) -> tuple[str, list[dict], list[str]]:
    """Pull `|ROUTINE| {...} |` and `|ROUTINE_CANCEL| <id> |` markers
    from `text`. Returns `(cleaned, [routine_specs], [cancel_ids])`.
    Spec parsing failures are skipped silently — the marker is still
    stripped from the cleaned text so the user doesn't see broken JSON."""
    if not text or '|' not in text:
        return text, [], []

    specs: list[dict] = []
    cancels: list[str] = []

    def _grab_spec(m: re.Match) -> str:
        try:
            spec = json.loads(m.group(1))
            if isinstance(spec, dict):
                specs.append(spec)
        except Exception:
            pass
        return ''

    def _grab_cancel(m: re.Match) -> str:
        cid = (m.group(1) or '').strip().strip('"\'`')
        if cid:
            cancels.append(cid)
        return ''

    cleaned = _ROUTINE_RE.sub(_grab_spec, text)
    cleaned = _ROUTINE_CANCEL_RE.sub(_grab_cancel, cleaned)
    cleaned = re.sub(r'[ \t]+\n', '\n', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).rstrip()
    return cleaned, specs, cancels
