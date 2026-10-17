"""Behaviour contracts for the transport-closed liveness watchdog and the
404 ``Session not found`` recovery path (``tools/mcp_liveness.py``).

The feature under test is imported lazily inside every test: before the
implementation lands the missing symbols must surface as assertion failures,
never as a collection error that would hide the red signal.

Contracts covered:
(a) a Ready client whose transport is closed emits exactly one event bound to
    that client's ``client_id``;
(b) a same-name replacement connection is not evicted by the stale event;
(c) drifting out of ``Ready`` (re-handshake / reset_transport) is silent;
(d) the slot is cleared on event so a replacement can re-arm immediately;
(e) a 404 ``Session not found`` invalidates the schema cache and re-initializes.
"""

from __future__ import annotations


def _liveness():
    from tools import mcp_liveness as ml

    return ml


def _require(module, name):
    value = getattr(module, name, None)
    assert value is not None, f"tools.mcp_liveness.{name} is missing"
    return value


def _watchdog(*, on_event=None, spawn=None, sleep=None):
    ml = _liveness()
    cls = _require(ml, "TransportLivenessWatchdog")
    return ml, cls(on_event=on_event, spawn=spawn, sleep=sleep)


class TestTransportClosedWatchdog:
    def test_ready_state_literal_is_ready(self):
        ml = _liveness()
        assert getattr(ml, "READY_STATE", None) == "Ready"

    def test_default_poll_interval_is_500ms(self):
        ml, wd = _watchdog(spawn=None)
        assert wd.poll_interval == 0.5

    def test_closed_ready_client_emits_one_event_bound_to_client_id(self):
        events = []
        ml, wd = _watchdog(on_event=events.append, spawn=None)
        wd.arm(
            "client-A",
            server_name="srv",
            state_getter=lambda: "Ready",
            is_closed=lambda: True,
        )

        first = wd.poll()
        second = wd.poll()

        assert [e.client_id for e in first] == ["client-A"]
        assert first[0].server_name == "srv"
        assert second == []
        assert [e.client_id for e in events] == ["client-A"]

    def test_event_clears_slot_so_a_replacement_can_rearm(self):
        events = []
        ml, wd = _watchdog(on_event=events.append, spawn=None)
        wd.arm("client-A", state_getter=lambda: "Ready", is_closed=lambda: True)
        assert [e.client_id for e in wd.poll()] == ["client-A"]
        assert not wd.has_slot("client-A")

        # A brand-new connection re-arms under its own client id immediately.
        wd.arm("client-B", state_getter=lambda: "Ready", is_closed=lambda: False)
        assert wd.poll() == []
        assert wd.has_slot("client-B")
        assert [e.client_id for e in events] == ["client-A"]

    def test_same_name_replacement_is_not_evicted_by_stale_event(self):
        ml, wd = _watchdog(spawn=None)
        wd.arm(
            "client-A",
            server_name="srv",
            state_getter=lambda: "Ready",
            is_closed=lambda: True,
        )
        # The replacement connection to the SAME server name is already live.
        wd.arm(
            "client-B",
            server_name="srv",
            state_getter=lambda: "Ready",
            is_closed=lambda: False,
        )

        events = wd.poll()

        assert [e.client_id for e in events] == ["client-A"]
        matches = _require(ml, "event_matches_client")
        assert matches(events[0], "B") is False
        assert matches(events[0], "client-A") is True
        assert wd.has_slot("client-B")

    def test_state_drift_out_of_ready_is_silent_and_disarms(self):
        events = []
        ml, wd = _watchdog(on_event=events.append, spawn=None)
        wd.arm("client-A", state_getter=lambda: "Reconnecting", is_closed=lambda: True)

        assert wd.poll() == []
        assert events == []
        assert not wd.has_slot("client-A")

    def test_unknown_transport_state_never_emits_a_false_close(self):
        events = []
        ml, wd = _watchdog(on_event=events.append, spawn=None)
        wd.arm("client-A", state_getter=lambda: "Ready", is_closed=lambda: None)

        assert wd.poll() == []
        assert events == []
        assert wd.has_slot("client-A")

    def test_arm_spawns_exactly_one_poller_per_client(self):
        spawned = []
        ml, wd = _watchdog(spawn=lambda target: spawned.append(target))
        wd.arm("A", state_getter=lambda: "Ready", is_closed=lambda: False)
        wd.arm("B", state_getter=lambda: "Ready", is_closed=lambda: False)
        assert len(spawned) == 2

    def test_one_shot_poller_stops_after_the_first_event(self):
        events = []
        state = {"closed": False, "sleeps": 0}

        def sleeper(_seconds):
            state["sleeps"] += 1
            state["closed"] = True

        ml, wd = _watchdog(
            on_event=events.append,
            spawn=lambda target: target(),  # run the poller inline, deterministically
            sleep=sleeper,
        )
        wd.arm("A", state_getter=lambda: "Ready", is_closed=lambda: state["closed"])

        assert [e.client_id for e in events] == ["A"]
        assert state["sleeps"] == 1
        assert not wd.has_slot("A")


class TestTransportClosedProbe:
    def test_duck_typed_flags_and_callables(self):
        ml = _liveness()
        probe = _require(ml, "transport_is_closed")

        class Flagged:
            is_closed = True

        class Method:
            def is_transport_closed(self):
                return False

        class Unknown:
            pass

        class Broken:
            @property
            def is_closed(self):
                raise RuntimeError("nope")

        assert probe(Flagged()) is True
        assert probe(Method()) is False
        assert probe(Unknown()) is None
        assert probe(None) is None
        assert probe(Broken()) is None


class TestSessionNotFoundRecovery:
    class _SessionNotFound(ConnectionError):
        status_code = 404

        def __str__(self):
            return "Session not found"

    def test_detects_marker_and_404(self):
        ml = _liveness()
        decide = _require(ml, "is_session_not_found")

        assert decide(self._SessionNotFound()) is True
        assert decide(body='{"error": "Session not found"}') is True
        assert decide("Session not found", status_code=404) is True
        # A bare 404 without the session marker is a different failure.
        assert decide("Not Found", status_code=404) is False
        # The same marker on a non-404 status is not the unknown-session case.
        assert decide("Session not found", status_code=500) is False

    def test_recovery_invalidates_cache_then_reinitializes(self):
        ml = _liveness()
        recover = _require(ml, "recover_session_not_found")
        calls = []

        handled = recover(
            "srv",
            self._SessionNotFound(),
            reinitialize=lambda name: calls.append(("reinit", name)),
            invalidate_cache=lambda name: calls.append(("invalidate", name)),
        )

        assert handled is True
        assert calls == [("invalidate", "srv"), ("reinit", "srv")]

    def test_unrelated_error_is_not_recovered(self):
        ml = _liveness()
        recover = _require(ml, "recover_session_not_found")
        calls = []

        handled = recover(
            "srv",
            "connection reset by peer",
            reinitialize=lambda name: calls.append("reinit"),
            invalidate_cache=lambda name: calls.append("invalidate"),
        )

        assert handled is False
        assert calls == []

    def test_invalidate_schema_cache_drops_only_that_server(self, monkeypatch, tmp_path):
        from tools import mcp_schema_cache as msc

        ml = _liveness()
        invalidate = _require(ml, "invalidate_schema_cache")
        monkeypatch.setattr(msc, "_cache_path", lambda: tmp_path / "cache.json")
        msc.write_cache_entry("srv", "fp", tools=[{"name": "t"}], utility_tools=[])
        msc.write_cache_entry("other", "fp", tools=[], utility_tools=[])

        invalidate("srv")

        assert msc.get_cached_entry("srv", "fp") is None
        assert msc.get_cached_entry("other", "fp") is not None

    def test_http_transport_seam_uses_the_recovery_hook(self, monkeypatch):
        from tools import mcp_tool_transport as mtt

        ml = _liveness()
        seam = getattr(mtt, "_recover_session_not_found", None)
        assert seam is not None, "tools.mcp_tool_transport has no session-not-found seam"
        calls = []
        monkeypatch.setattr(
            ml, "invalidate_schema_cache", lambda name: calls.append(("invalidate", name))
        )

        handled = seam(
            "srv",
            self._SessionNotFound(),
            reinitialize=lambda name: calls.append(("reinit", name)),
        )

        assert handled is True
        assert calls == [("invalidate", "srv"), ("reinit", "srv")]
