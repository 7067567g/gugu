"""Persisted, session-scoped plan mode (log-only).

``/plan`` turns plan mode on for the current session and injects the planning
prompt; ``/plan off`` clears it. The flag and any pending plan live in a small
``plan_mode_state`` table in the profile's ``state.db`` (an **independent** table
— the ``sessions`` schema is untouched, so no migration risk). Plan mode is
**never** added to the model transcript or the system prompt, so it is
prompt-cache safe and toggling it does not change the request's tool directory.

Public surface:

* :func:`set_plan_mode` / :func:`is_plan_mode` / :func:`clear_plan_mode`
* :func:`set_pending_plan` / :func:`get_pending_plan`
* :func:`exit_plan_mode` — validate a markdown plan and hand it to the user for
  review (``approved`` / ``keep-planning`` / ``dismiss``)

State survives process restarts and re-imports (resume / reload); only
:func:`reset_cache` drops the in-process memo.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS plan_mode_state (
    session_id TEXT PRIMARY KEY,
    active INTEGER NOT NULL DEFAULT 0,
    pending_plan TEXT,
    updated_at REAL NOT NULL
)
"""

_ANONYMOUS_SESSION = "__anonymous__"

# Process-local memo of the active flag. A fresh import / reset_cache() drops it,
# so reads fall back to the persisted row (the resume/reload contract).
_CACHE_LOCK = threading.RLock()
_ACTIVE_CACHE: dict[str, bool] = {}


def reset_cache() -> None:
    """Forget the in-process memo (tests, profile switches, manual reload)."""
    with _CACHE_LOCK:
        _ACTIVE_CACHE.clear()


def _normalize_session_id(session_id: str | None) -> str:
    return str(session_id or _ANONYMOUS_SESSION)


def _cache_key(session_id: str) -> str:
    return f"{get_hermes_home()}\x00{session_id}"


def _db_path() -> Path:
    return get_hermes_home() / "state.db"


def _secure_db_files(db: Path) -> None:
    """Tighten ``state.db`` + sidecars to 0600 (never hardcode a path)."""
    if os.name == "nt":
        return
    try:
        from hermes_state import _secure_state_db_files

        _secure_state_db_files(db, create_main=False)
        return
    except Exception:  # pragma: no cover - canonical helper is the normal path
        logger.debug("plan_mode: canonical state.db hardening unavailable", exc_info=True)
    for path in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    db = _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), timeout=5.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(_TABLE_DDL)
        conn.commit()
        _secure_db_files(db)
        yield conn
    finally:
        conn.close()


def set_plan_mode(session_id: str, active: bool) -> None:
    """Enable/disable plan mode for *session_id*.

    Disabling (``/plan off``) also clears any pending plan.
    """
    sid = _normalize_session_id(session_id)
    now = time.time()
    with _connection() as conn:
        if active:
            conn.execute(
                "INSERT INTO plan_mode_state (session_id, active, pending_plan, updated_at) "
                "VALUES (?, 1, NULL, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET active=1, updated_at=excluded.updated_at",
                (sid, now),
            )
        else:
            conn.execute(
                "INSERT INTO plan_mode_state (session_id, active, pending_plan, updated_at) "
                "VALUES (?, 0, NULL, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "active=0, pending_plan=NULL, updated_at=excluded.updated_at",
                (sid, now),
            )
        conn.commit()
    with _CACHE_LOCK:
        _ACTIVE_CACHE[_cache_key(sid)] = bool(active)


def clear_plan_mode(session_id: str) -> None:
    """Exit plan mode for *session_id* and drop any pending plan (``/plan off``)."""
    set_plan_mode(session_id, False)


def is_plan_mode(session_id: str) -> bool:
    """True when *session_id* is in plan mode; survives reload/resume."""
    sid = _normalize_session_id(session_id)
    key = _cache_key(sid)
    with _CACHE_LOCK:
        cached = _ACTIVE_CACHE.get(key)
    if cached is not None:
        return cached
    with _connection() as conn:
        row = conn.execute(
            "SELECT active FROM plan_mode_state WHERE session_id=?", (sid,)
        ).fetchone()
    active = bool(row["active"]) if row is not None else False
    with _CACHE_LOCK:
        _ACTIVE_CACHE[key] = active
    return active


def set_pending_plan(session_id: str, plan: str) -> None:
    """Store the last submitted plan for review; implies plan mode is active."""
    sid = _normalize_session_id(session_id)
    now = time.time()
    with _connection() as conn:
        conn.execute(
            "INSERT INTO plan_mode_state (session_id, active, pending_plan, updated_at) "
            "VALUES (?, 1, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "pending_plan=excluded.pending_plan, updated_at=excluded.updated_at",
            (sid, plan, now),
        )
        conn.commit()
    with _CACHE_LOCK:
        _ACTIVE_CACHE[_cache_key(sid)] = True


def get_pending_plan(session_id: str) -> str | None:
    """The pending plan markdown for *session_id*, or ``None``."""
    sid = _normalize_session_id(session_id)
    with _connection() as conn:
        row = conn.execute(
            "SELECT pending_plan FROM plan_mode_state WHERE session_id=?", (sid,)
        ).fetchone()
    return None if row is None else row["pending_plan"]


def apply_plan_command(session_id: str | None, args: str = "") -> bool:
    """Apply a ``/plan`` argument to the persisted flag (shared by every surface).

    ``off`` exits plan mode and clears any pending plan; anything else (including
    the bare command) enters plan mode. Returns ``True`` when plan mode is now
    active, so the caller injects the planning prompt; ``False`` when the
    argument exited plan mode.
    """
    if (args or "").strip().lower() == "off":
        if session_id:
            clear_plan_mode(session_id)
        return False
    if session_id:
        set_plan_mode(session_id, True)
    return True


def exit_plan_mode(
    plan_markdown: str = "",
    session_id: str | None = None,
    *,
    dismiss: bool = False,
) -> str:
    """Hand a markdown plan out of plan mode, for the user's review.

    Returns a JSON string. ``outcome`` is one of ``approved`` (valid plan stored
    for review), ``keep-planning`` (invalid plan — no ``#`` heading), or
    ``dismiss`` (the user discarded the plan and plan mode was cleared).
    """
    sid = _normalize_session_id(session_id)
    if dismiss:
        clear_plan_mode(sid)
        return json.dumps({"success": True, "outcome": "dismiss"})
    plan = (plan_markdown or "").strip()
    if not plan.lstrip().startswith("#"):
        return json.dumps(
            {
                "success": False,
                "outcome": "keep-planning",
                "error": "Plan must be markdown that starts with a '#' heading.",
            }
        )
    set_pending_plan(sid, plan)
    return json.dumps({"success": True, "outcome": "approved", "plan": plan})
