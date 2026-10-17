"""Production wiring of the transport-closed liveness watchdog.

``tools/mcp_liveness.TransportLivenessWatchdog`` itself is behaviour-tested in
``tests/tools/test_mcp_transport_liveness.py``. This file covers the PRODUCTION
assembly that turns it on for every served MCP session:

(a) ``watch_session`` arms the process-level singleton and routes each event to
    the callback registered for THAT ``client_id`` only; ``unwatch_session``
    reclaims the slot and the callback (and is idempotent);
(b) the poller thread exits once its slot is disarmed — inline and with the real
    ``_default_thread_spawn`` thread — so no orphan poller survives a session;
(c) ``MCPServerTransportMixin._serve_session`` pairs exactly one watch with one
    unwatch on both the normal return and the raising path, and the armed slot is
    gone again once the session is over;
(d) ``session_transport_is_closed`` probes stdio children by pid and every other
    transport through ``transport_is_closed``.

Stdlib only: the ``mcp`` SDK is deliberately NOT imported here (the feature under
test is imported lazily / per test, so a missing symbol is a failure, never a
collection error that would hide the red signal).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
import types

import pytest

from tools.mcp_tool_transport import MCPServerTransportMixin

POLLER_THREAD_NAME = "mcp-transport-liveness"


def _liveness():
    from tools import mcp_liveness as ml

    return ml


def _require(module, name):
    value = getattr(module, name, None)
    assert value is not None, f"tools.mcp_liveness.{name} is missing"
    return value


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _live_poller_thread() -> bool:
    return any(thread.name == POLLER_THREAD_NAME for thread in threading.enumerate())


def _dead_pid() -> int:
    """A pid that has definitely exited AND been reaped."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


@pytest.fixture(autouse=True)
def _fresh_production_layer():
    """Each test gets a pristine lazy singleton + callback table."""
    ml = _liveness()
    old_wd = getattr(ml, "_PRODUCTION_WATCHDOG", None)
    old_callbacks = dict(getattr(ml, "_PRODUCTION_CALLBACKS", {}))
    ml._PRODUCTION_WATCHDOG = None
    ml._PRODUCTION_CALLBACKS = {}
    try:
        yield ml
    finally:
        ml._PRODUCTION_CALLBACKS = dict(old_callbacks)
        ml._PRODUCTION_WATCHDOG = old_wd


class TestProductionWatchSession:
    def test_production_watchdog_is_a_lazy_singleton(self):
        ml = _liveness()
        watchdog_of = _require(ml, "production_watchdog")
        watchdog_cls = _require(ml, "TransportLivenessWatchdog")

        assert ml._PRODUCTION_WATCHDOG is None  # fixture reset: nothing built yet
        first = watchdog_of()
        assert isinstance(first, watchdog_cls)
        assert ml._PRODUCTION_WATCHDOG is first
        assert watchdog_of() is first  # second call reuses the process-level instance

    def test_watch_arms_a_slot_and_registers_the_callback(self):
        ml = _liveness()
        watch = _require(ml, "watch_session")
        wd = ml.production_watchdog()
        wd._spawn = None  # drive poll() by hand instead of spawning the real poller
        wd._sleep = lambda _seconds: None
        seen = []
        callback = seen.append

        watch("client-A", server_name="srv", is_closed=lambda: False, on_event=callback)

        assert wd.has_slot("client-A")
        assert ml._PRODUCTION_CALLBACKS["client-A"] is callback

    def test_events_are_dispatched_only_to_the_owning_client(self):
        ml = _liveness()
        watch = _require(ml, "watch_session")
        wd = ml.production_watchdog()
        wd._spawn = None
        wd._sleep = lambda _seconds: None
        seen: dict[str, list] = {"A": [], "B": []}

        watch("A", server_name="srv", is_closed=lambda: True, on_event=seen["A"].append)
        watch("B", server_name="srv", is_closed=lambda: False, on_event=seen["B"].append)

        events = wd.poll()

        assert [event.client_id for event in events] == ["A"]
        assert [event.client_id for event in seen["A"]] == ["A"]
        assert seen["B"] == []  # a healthy replacement must never see A's close

    def test_callback_that_raises_is_swallowed(self):
        ml = _liveness()
        watch = _require(ml, "watch_session")
        wd = ml.production_watchdog()
        wd._spawn = None
        wd._sleep = lambda _seconds: None

        def boom(_event):
            raise RuntimeError("sink exploded")

        watch("A", server_name="srv", is_closed=lambda: True, on_event=boom)

        assert [event.client_id for event in wd.poll()] == ["A"]

    def test_unwatch_reclaims_slot_and_callback_and_is_idempotent(self):
        ml = _liveness()
        watch = _require(ml, "watch_session")
        unwatch = _require(ml, "unwatch_session")
        wd = ml.production_watchdog()
        wd._spawn = None
        wd._sleep = lambda _seconds: None

        watch("A", server_name="srv", is_closed=lambda: False, on_event=lambda event: None)

        assert unwatch("A") is True
        assert wd.slot_count() == 0
        assert "A" not in ml._PRODUCTION_CALLBACKS
        assert unwatch("A") is False  # already reclaimed


class TestPollerReclamation:
    def test_inline_poller_returns_once_its_slot_is_disarmed(self):
        ml = _liveness()
        watch = _require(ml, "watch_session")
        unwatch = _require(ml, "unwatch_session")
        wd = ml.production_watchdog()
        wd._spawn = lambda target: target()  # run the poller inline, deterministically
        sleeps = []

        def sleeper(_seconds):
            sleeps.append(_seconds)
            unwatch("A")  # the unwatch a finished _serve_session issues
            if len(sleeps) > 3:  # guard: a poller that ignores the disarm must fail, not hang
                raise RuntimeError("poller did not exit after disarm")

        wd._sleep = sleeper

        watch("A", server_name="srv", is_closed=lambda: False, on_event=lambda event: None)

        assert len(sleeps) == 1  # one pass, then the loop saw the slot gone and returned
        assert wd.has_slot("A") is False

    def test_real_thread_poller_exits_after_unwatch(self):
        ml = _liveness()
        watch = _require(ml, "watch_session")
        unwatch = _require(ml, "unwatch_session")
        wd = ml.production_watchdog()
        assert wd._spawn is not None  # the daemon-thread spawn is in place

        watch("A", server_name="srv", is_closed=lambda: False)
        assert _wait_until(_live_poller_thread), "watch_session spawned no poller thread"

        assert unwatch("A") is True

        assert _wait_until(lambda: not _live_poller_thread()), "poller thread outlived the disarm"


class _FakeSession:
    """Stand-in for an SDK ClientSession: `transport_is_closed` sees a trustworthy flag."""

    is_closed = False


class _StubServer(MCPServerTransportMixin):
    """Minimal ``MCPServerTransportMixin`` host: handshake/discovery/wait are injected.

    No ``__slots__`` on purpose — the subclass of the slots-only mixin needs a
    ``__dict__`` for the task attributes ``_serve_session`` reads.
    """

    def __init__(self, *, name: str = "srv", outcome: str = "reconnect"):
        self.name = name
        self._ready = asyncio.Event()
        self._ever_connected = False
        self._session_proven = False
        self._reconnect_event = asyncio.Event()
        self._stdio_child_pids: set = set()
        self.session = None
        self.initialize_result = None
        self._outcome = outcome

    async def _negotiate_session(self, session, connect_timeout):
        return object()

    async def _discover_tools(self):
        return None

    async def _wait_for_lifecycle_event(self):
        if self._outcome == "raise":
            raise ConnectionError("lifecycle wait exploded")
        return self._outcome


def _record_watch_calls(monkeypatch, ml):
    calls = []

    def watch(client_id, **kwargs):
        calls.append(("watch", client_id))
        return None

    def unwatch(client_id):
        calls.append(("unwatch", client_id))
        return True

    monkeypatch.setattr(ml, "watch_session", watch)
    monkeypatch.setattr(ml, "unwatch_session", unwatch)
    return calls


class TestServeSessionWiring:
    def test_serve_session_pairs_watch_and_unwatch_on_normal_return(self, monkeypatch):
        ml = _liveness()
        calls = _record_watch_calls(monkeypatch, ml)
        stub = _StubServer(outcome="reconnect")
        session = _FakeSession()
        client_id = f"srv:{id(session)}"

        reason = asyncio.run(stub._serve_session(session, 5.0))

        assert reason == "reconnect"
        assert calls == [("watch", client_id), ("unwatch", client_id)]

    def test_serve_session_pairs_watch_and_unwatch_when_the_wait_raises(self, monkeypatch):
        ml = _liveness()
        calls = _record_watch_calls(monkeypatch, ml)
        stub = _StubServer(outcome="raise")
        session = _FakeSession()
        client_id = f"srv:{id(session)}"

        with pytest.raises(ConnectionError):
            asyncio.run(stub._serve_session(session, 5.0))

        assert calls == [("watch", client_id), ("unwatch", client_id)]

    def test_serve_session_arms_the_production_singleton_for_its_own_session(self):
        ml = _liveness()
        wd = ml.production_watchdog()
        wd._spawn = None  # no poller thread: assert the registered slot directly
        wd._sleep = lambda _seconds: None
        stub = _StubServer(outcome="reconnect")
        session = _FakeSession()
        client_id = f"srv:{id(session)}"
        observed = {}

        async def _wait():
            observed["slots"] = wd.slot_count()
            observed["client_ids"] = list(ml._PRODUCTION_CALLBACKS)
            return "reconnect"

        stub._wait_for_lifecycle_event = _wait

        reason = asyncio.run(stub._serve_session(session, 5.0))

        assert reason == "reconnect"
        assert observed == {"slots": 1, "client_ids": [client_id]}
        assert wd.has_slot(client_id) is False  # reclaimed by the finally block
        assert ml._PRODUCTION_CALLBACKS == {}


class TestSessionTransportIsClosed:
    def test_stdio_child_pids_are_probed_by_pid_liveness(self):
        ml = _liveness()
        probe = _require(ml, "session_transport_is_closed")

        alive = types.SimpleNamespace(_stdio_child_pids={os.getpid()})
        assert probe(alive, object()) is False

        gone = types.SimpleNamespace(_stdio_child_pids={_dead_pid()})
        assert probe(gone, object()) is True

    def test_without_stdio_children_the_session_transport_is_probed(self):
        ml = _liveness()
        probe = _require(ml, "session_transport_is_closed")
        task = types.SimpleNamespace(_stdio_child_pids=set())

        assert probe(task, types.SimpleNamespace(is_closed=True)) is True
        assert probe(task, types.SimpleNamespace(is_closed=False)) is False
        assert probe(task, object()) is None  # unknown, never a false close
