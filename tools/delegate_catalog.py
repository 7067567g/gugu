"""Read-only, session-facing catalog of delegated subagent members (target T-01).

The hot path keeps its own ownership state in the in-memory registry
(``tools/delegate_tool_registry.py``); the authoritative audit trail is the durable
``async_delegations`` table in ``state.db`` written by ``tools/async_delegation.py``.
This module overlays the two so a session can still ask "which members did I dispatch"
after the in-memory registry is gone (process restart, or a rebuilt registry).

Every function here is a pure read: it never runs crash recovery, restores undelivered
completions, claims, or writes a row. A missing or unreadable ``state.db`` degrades to
the in-memory answer instead of raising.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from hermes_constants import get_hermes_home
from tools import delegate_tool_registry as registry

# Public selector -> durable-ledger column (``session_key`` is the routing key persisted as
# ``origin_session``). Same claim semantics as async_delegation._session_records: any
# non-empty selector claims the record.
_SELECTOR_COLUMNS = (("origin_ui_session_id", "origin_ui_session_id"),
                     ("session_key", "origin_session"),
                     ("parent_session_id", "parent_session_id"))

_DURABLE_SELECT = (
    "SELECT delegation_id, origin_session, origin_ui_session_id, parent_session_id, state, "
    "dispatched_at, completed_at, delivery_state, task_json FROM async_delegations WHERE ({where})"
)


def _resolve_db_path(db_path: Optional[Any]) -> Path:
    """Resolve the ledger path at CALL time (never a module constant) so profiles and
    per-test ``HERMES_HOME`` redirects are honoured."""
    return Path(db_path) if db_path is not None else get_hermes_home() / "state.db"


def _select(path: Path, query: str, params: Sequence[Any]) -> List[sqlite3.Row]:
    """Run one SELECT over a read-only connection; fall back to a plain connection only when
    the OS refuses read-only access (e.g. WAL without a shm). Neither connection writes."""
    try:
        conn = sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        return list(conn.execute(query, list(params)).fetchall())
    finally:
        conn.close()


def _durable_rows(path: Path, selectors: Sequence[Tuple[str, str]], statuses: Optional[Sequence[str]]) -> List[sqlite3.Row]:
    if not path.exists():
        return []
    where = " OR ".join(f"{column}=?" for column, _ in selectors)
    query, params = _DURABLE_SELECT.format(where=where), [wanted for _, wanted in selectors]
    if statuses:
        query += f" AND state IN ({','.join('?' * len(statuses))})"
        params.extend(statuses)
    try:
        return _select(path, query, params)
    except (sqlite3.Error, OSError):
        # A missing/corrupt/locked ledger is a benign empty-durable case for a read API.
        return []


def _member_from_memory(record: Dict[str, Any]) -> Dict[str, Any]:
    owner_agent = str(record.get("owner_agent_session_id") or "")
    owner_ui = str(record.get("owner_ui_session_id") or "")
    return {
        "subagent_id": record.get("subagent_id"),
        "delegation_id": record.get("delegation_id"),
        "goal": record.get("goal"),
        "state": record.get("status"),
        "owner_agent_session_id": owner_agent or None,
        "origin_ui_session_id": owner_ui or None,
        # The registry does not stamp the routing session_key, so a session_key selector
        # cannot claim a purely in-memory record.
        "session_key": None,
        "parent_session_id": owner_agent or None,
        "dispatched_at": None,
        "completed_at": None,
        "delivery_state": None,
        "source": "memory",
    }


def _member_from_durable(row: sqlite3.Row) -> Dict[str, Any]:
    task: Dict[str, Any] = {}
    if row["task_json"]:
        try:
            task = json.loads(row["task_json"]) or {}
        except (TypeError, ValueError):
            task = {}
    goal = task.get("goal")
    if not goal and isinstance(task.get("goals"), list):
        goal = "; ".join(str(g) for g in task["goals"] if g) or None
    return {
        "subagent_id": None,
        "delegation_id": row["delegation_id"],
        "goal": goal,
        "state": row["state"],
        # The spawner's durable session id is this ledger's owner attribution.
        "owner_agent_session_id": row["parent_session_id"],
        "origin_ui_session_id": row["origin_ui_session_id"],
        "session_key": row["origin_session"],
        "parent_session_id": row["parent_session_id"],
        "dispatched_at": row["dispatched_at"],
        "completed_at": row["completed_at"],
        "delivery_state": row["delivery_state"],
        "source": "durable",
    }


def _member_key(member: Dict[str, Any]) -> str:
    return member.get("delegation_id") or f"subagent:{member.get('subagent_id')}"


def _fill_gaps(base: Dict[str, Any], durable: Dict[str, Any]) -> None:
    """Overlay the durable row onto a memory member without clobbering fresher live fields."""
    for field in ("goal", "state", "owner_agent_session_id", "origin_ui_session_id", "session_key",
                  "parent_session_id", "dispatched_at", "completed_at", "delivery_state"):
        if base.get(field) in (None, "") and durable.get(field) not in (None, ""):
            base[field] = durable[field]
    base["source"] = "memory+durable"


def list_session_members(
    session_key: str = "",
    origin_ui_session_id: str = "",
    parent_session_id: str = "",
    *,
    statuses: Optional[Iterable[str]] = None,
    include_memory: bool = True,
    db_path: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Members a session dispatched: in-memory live/recent records overlaid on the durable
    ``async_delegations`` ledger, deduped by ``delegation_id`` (memory wins for live fields).

    Any non-empty selector claims a member: ``session_key`` (``origin_session`` routing key at
    dispatch), ``origin_ui_session_id`` (TUI tab), or ``parent_session_id`` (spawner's durable
    id). With no selector the result is empty (never a full-ledger dump). ``statuses`` narrows
    both sources by their ``state``. ``db_path`` overrides the ledger location (defaults to
    ``$HERMES_HOME/state.db`` resolved at call time).
    """
    wanted = {"session_key": session_key, "origin_ui_session_id": origin_ui_session_id,
              "parent_session_id": parent_session_id}
    selectors = [(column, str(wanted[field])) for field, column in _SELECTOR_COLUMNS if wanted.get(field)]
    if not selectors:
        return []
    status_filter = None if statuses is None else {str(s) for s in statuses}

    members: Dict[str, Dict[str, Any]] = {}
    if include_memory:
        for record in registry.list_known_subagents():
            owner_agent = str(record.get("owner_agent_session_id") or "")
            owner_ui = str(record.get("owner_ui_session_id") or "")
            if not ((origin_ui_session_id and owner_ui == str(origin_ui_session_id))
                    or (parent_session_id and owner_agent == str(parent_session_id))):
                continue
            member = _member_from_memory(record)
            if status_filter is not None and member.get("state") not in status_filter:
                continue
            members[_member_key(member)] = member

    for row in _durable_rows(_resolve_db_path(db_path), selectors,
                             sorted(status_filter) if status_filter else None):
        durable = _member_from_durable(row)
        existing = members.get(_member_key(durable))
        if existing is None:
            members[_member_key(durable)] = durable
        else:
            _fill_gaps(existing, durable)
    return list(members.values())
