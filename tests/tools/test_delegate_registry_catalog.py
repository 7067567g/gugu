"""Contract tests for the read-only, session-facing subagent member catalog.

Target T-01: overlay the in-memory live/recent subagent registry
(``tools/delegate_tool_registry.py``) on the durable async-delegation ledger
(``state.db`` / ``async_delegations``, owned by ``tools/async_delegation.py``) so
that after a process restart -- i.e. with the in-memory tables cleared -- the
same durable backend can still answer "which members did this session dispatch".
The read API must be strictly read-only: it never runs crash recovery, never
restores completions, and never mutates ledger rows.

The module under test may not exist yet during the RED phase; imports are lazy
so a missing module surfaces as an empty result (a semantic failure) rather than
a collection-time ImportError.
"""

from __future__ import annotations

import importlib
import sqlite3

import pytest

from tools import async_delegation as ad
from tools import delegate_tool_registry as registry


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Point HERMES_HOME at a per-test tempdir (the suite is run with --noconftest)."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    yield tmp_path
    _clear_memory()


def _clear_memory() -> None:
    with registry._active_subagents_lock:
        registry._active_subagents.clear()
        registry._recent_subagents.clear()


def _list_members(**selectors):
    """Call the catalog read API; an absent module yields [] so the RED phase is a
    real assertion failure about a missing member, not an ImportError."""
    try:
        catalog = importlib.import_module("tools.delegate_catalog")
    except ModuleNotFoundError:
        return []
    return catalog.list_session_members(**selectors)


def _persist(goal, delegation_id, *, state_after=None, **selectors):
    ad._persist_dispatch({
        "delegation_id": delegation_id,
        "goal": goal,
        "session_key": selectors.get("session_key", ""),
        "origin_ui_session_id": selectors.get("origin_ui_session_id", ""),
        "parent_session_id": selectors.get("parent_session_id"),
        "dispatched_at": 1_700_000_000.0,
    })
    if state_after is not None:
        ad._persist_completion(
            {"delegation_id": delegation_id, "status": state_after, "completed_at": 1_700_000_100.0},
            {"status": state_after},
        )


def _row_count() -> int:
    path = ad._db_path()
    if not path.exists():
        return 0
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM async_delegations").fetchone()[0]
    finally:
        conn.close()


def test_durable_member_survives_memory_clear():
    """A delegation persisted before the restart is still listed afterwards."""
    _persist("map the repository", "deleg_abc123", session_key="sess-1", parent_session_id="parent-1")
    _clear_memory()  # simulate process restart: no in-memory registry survives

    members = _list_members(session_key="sess-1")
    by_id = {m.get("delegation_id"): m for m in members}

    assert "deleg_abc123" in by_id, f"durable member missing after restart: {members!r}"
    member = by_id["deleg_abc123"]
    assert member["goal"] == "map the repository"
    assert member["delegation_id"] == "deleg_abc123"
    assert member["state"] == "running"
    assert member["owner_agent_session_id"] == "parent-1"


def test_durable_member_matches_each_selector():
    """Any non-empty selector (session_key / origin_ui_session_id / parent_session_id) claims the row."""
    _persist("ui-task", "deleg_ui", origin_ui_session_id="ui-tab-7")
    _persist("parent-task", "deleg_parent", parent_session_id="spawner-9")
    _persist("key-task", "deleg_key", session_key="route-key-3")
    _clear_memory()

    for selector, expected in (
        ({"origin_ui_session_id": "ui-tab-7"}, "deleg_ui"),
        ({"parent_session_id": "spawner-9"}, "deleg_parent"),
        ({"session_key": "route-key-3"}, "deleg_key"),
    ):
        ids = {m.get("delegation_id") for m in _list_members(**selector)}
        assert expected in ids, f"selector {selector} did not claim {expected}: {ids!r}"

    assert _list_members(session_key="no-such-session") == []


def test_terminal_state_is_reported_from_ledger():
    """The restart answer carries the persisted terminal state, not just 'running'."""
    _persist("finished work", "deleg_done", state_after="completed", session_key="sess-2")
    _clear_memory()

    member = next(m for m in _list_members(session_key="sess-2") if m["delegation_id"] == "deleg_done")
    assert member["state"] == "completed"


def test_statuses_filter_narrows_the_ledger():
    """The optional statuses filter narrows durable rows by their persisted state."""
    _persist("still running", "deleg_live", session_key="sess-filter")
    _persist("already done", "deleg_finished", state_after="completed", session_key="sess-filter")
    _clear_memory()

    listed = {m["delegation_id"] for m in _list_members(session_key="sess-filter", statuses={"completed"})}
    assert listed == {"deleg_finished"}


def test_read_api_never_runs_recovery_or_mutates_rows(monkeypatch):
    """Recovery/restore are hard-failed; a read must still answer and leave rows untouched."""
    _persist("audit me", "deleg_ro1", session_key="sess-ro")
    _clear_memory()
    before = _row_count()
    assert before == 1

    def _boom(*_args, **_kwargs):
        raise AssertionError("the read API must not invoke crash recovery / completion restore")

    monkeypatch.setattr(ad, "recover_abandoned_delegations", _boom)
    monkeypatch.setattr(ad, "restore_undelivered_completions", _boom)

    members = _list_members(session_key="sess-ro")
    assert any(m.get("delegation_id") == "deleg_ro1" for m in members), members
    assert _row_count() == before


def test_memory_registry_is_merged_without_a_ledger():
    """Live/recent in-memory members are part of the answer on the hot path."""
    registry._register_subagent({
        "subagent_id": "sub-live-1",
        "goal": "in-memory task",
        "delegation_id": "deleg_mem",
        "owner_agent_session_id": "parent-mem",
        "owner_session_id": "ui-mem",
        "status": "running",
        "agent": object(),
    })
    try:
        members = _list_members(parent_session_id="parent-mem")
        match = next((m for m in members if m.get("delegation_id") == "deleg_mem"), None)
        assert match is not None, members
        assert match["goal"] == "in-memory task"
        assert match["owner_agent_session_id"] == "parent-mem"

        ui_members = _list_members(origin_ui_session_id="ui-mem")
        ui_match = next((m for m in ui_members if m.get("delegation_id") == "deleg_mem"), None)
        assert ui_match is not None, ui_members
        assert ui_match["origin_ui_session_id"] == "ui-mem"
    finally:
        _clear_memory()


def test_missing_or_unreadable_db_returns_memory_without_raising(tmp_path):
    """No state.db / a corrupt file is a benign empty-durable case, never an exception."""
    assert _list_members(session_key="sess") == []

    (tmp_path / "state.db").write_bytes(b"not a sqlite database")
    assert _list_members(session_key="sess") == []


def test_no_selector_returns_empty():
    """Without a selector the API must not dump the whole ledger."""
    _persist("x", "deleg_nosel", session_key="sess-x")
    _clear_memory()
    assert _list_members() == []
