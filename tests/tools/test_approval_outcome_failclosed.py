"""T-02: one closed approval-outcome set and a fail-closed ``request`` facade.

RED-first contract tests for ``tools.approval_outcome``. They pin behaviour,
not a snapshot: the four consent states are the whole vocabulary, every
abnormal path (no asker, asker raising, ``None``/illegal answer) resolves to
``unavailable`` (deny) and NEVER ``allowed-once``, and the audit pair
``approval/asked`` -> ``approval/decided`` is emitted to a log-only sink with
nothing riding back into a model-visible message.

The module is imported lazily inside each test so the collection phase never
depends on it existing (a RED run must fail on assertions, not on collection).
"""

from __future__ import annotations

import pytest


def _outcome():
    from tools import approval_outcome

    return approval_outcome


class TestClosedOutcomeSet:
    def test_outcome_set_is_exactly_the_four_consent_states(self):
        mod = _outcome()
        assert mod.OUTCOMES == frozenset(
            {mod.ALLOWED_ONCE, mod.REJECTED, mod.CANCELLED, mod.UNAVAILABLE}
        )
        # The closed vocabulary itself is the contract (D-002/T-02), not a snapshot.
        assert {
            mod.ALLOWED_ONCE,
            mod.REJECTED,
            mod.CANCELLED,
            mod.UNAVAILABLE,
        } == {"allowed-once", "rejected", "cancelled", "unavailable"}

    @pytest.mark.parametrize("answer", [None, "", "maybe", "ALLOWED-ONCE", 0, 1, object()])
    def test_illegal_or_missing_answer_is_unavailable(self, answer):
        mod = _outcome()
        assert mod.classify_outcome(answer) == mod.UNAVAILABLE

    @pytest.mark.parametrize(
        ("answer", "expected"),
        [
            ("allow", "ALLOWED_ONCE"),
            ("allowed-once", "ALLOWED_ONCE"),
            ("once", "ALLOWED_ONCE"),
            ("deny", "REJECTED"),
            ("rejected", "REJECTED"),
            ("cancel", "CANCELLED"),
            ("cancelled", "CANCELLED"),
            ("unavailable", "UNAVAILABLE"),
        ],
    )
    def test_explicit_answers_map_to_the_closed_set(self, answer, expected):
        mod = _outcome()
        assert mod.classify_outcome(answer) == getattr(mod, expected)


class TestFailClosedFacade:
    def test_no_answerer_is_unavailable(self):
        mod = _outcome()
        assert mod.request({"kind": "command", "description": "rm -rf /"}) == mod.UNAVAILABLE

    def test_asker_raising_is_unavailable(self):
        mod = _outcome()

        def boom(_req):
            raise RuntimeError("approval bridge died")

        assert mod.request({"kind": "command"}, asker=boom) == mod.UNAVAILABLE

    @pytest.mark.parametrize("answer", [None, "", "maybe", 0, 1, object()])
    def test_asker_returning_none_or_illegal_never_allows(self, answer):
        mod = _outcome()
        outcome = mod.request({"kind": "command"}, asker=lambda _req: answer)
        assert outcome == mod.UNAVAILABLE
        assert outcome != mod.ALLOWED_ONCE

    def test_explicit_asker_answers_pass_through(self):
        mod = _outcome()
        assert mod.request({"kind": "command"}, asker=lambda _req: "allow") == mod.ALLOWED_ONCE
        assert mod.request({"kind": "command"}, asker=lambda _req: "deny") == mod.REJECTED
        assert mod.request({"kind": "command"}, asker=lambda _req: "cancelled") == mod.CANCELLED


class TestAuditPair:
    def test_request_emits_asked_then_decided_to_the_sink(self):
        mod = _outcome()
        events: list[dict] = []
        outcome = mod.request(
            {"kind": "command", "pattern_key": "recursive delete", "description": "rm -rf /"},
            asker=lambda _req: "deny",
            audit_sink=events.append,
        )
        assert outcome == mod.REJECTED
        assert [event["event"] for event in events] == ["approval/asked", "approval/decided"]
        assert events[1]["outcome"] == mod.REJECTED

    def test_audit_pair_order_even_when_the_asker_dies(self):
        mod = _outcome()
        events: list[dict] = []

        def boom(_req):
            raise RuntimeError("bridge died")

        outcome = mod.request({"kind": "action"}, asker=boom, audit_sink=events.append)
        assert outcome == mod.UNAVAILABLE
        assert [event["event"] for event in events] == ["approval/asked", "approval/decided"]
        assert events[1]["outcome"] == mod.UNAVAILABLE

    def test_emit_audit_pair_logs_asked_then_decided(self):
        mod = _outcome()
        events: list[dict] = []
        mod.emit_audit_pair(
            events.append,
            request={"kind": "action", "description": "plugin rule"},
            outcome=mod.CANCELLED,
        )
        assert [event["event"] for event in events] == ["approval/asked", "approval/decided"]
        assert events[1]["outcome"] == mod.CANCELLED

    def test_audit_events_are_log_only_and_never_model_messages(self):
        mod = _outcome()
        events: list[dict] = []
        returned = mod.request(
            {"kind": "command", "description": "dangerous"},
            asker=lambda _req: "allow",
            audit_sink=events.append,
        )
        # The facade returns a bare outcome string: no audit payload can ride
        # along into a tool result the model reads.
        assert returned == mod.ALLOWED_ONCE
        assert isinstance(returned, str)
        assert "approval/" not in returned
        assert [event["event"] for event in events] == ["approval/asked", "approval/decided"]


class TestApprovalGateFailClosedWired:
    def test_plugin_gate_fail_closed_reports_unavailable(self, monkeypatch):
        """The fail-closed branch of ``_run_approval_gate`` must surface the
        unified ``unavailable`` outcome, not an untagged block."""
        import tools.approval as approval
        import tools.approval_context as approval_context

        monkeypatch.setattr(approval, "get_current_session_key", lambda default="": "tdo-o1")
        monkeypatch.setattr(approval, "is_approved", lambda sk, pk: False)
        monkeypatch.setattr(approval, "is_current_session_yolo_enabled", lambda: False)
        monkeypatch.setattr(approval, "_YOLO_MODE_FROZEN", False, raising=False)
        monkeypatch.setattr(approval, "_is_interactive_cli", lambda: False)
        monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_context, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval, "_is_cron_approval_context", lambda: False)
        monkeypatch.setattr(approval_context, "_is_cron_approval_context", lambda: False)
        monkeypatch.setattr(approval, "_is_single_query_approval_context", lambda: False)
        monkeypatch.setattr(approval_context, "_is_single_query_approval_context", lambda: False)
        monkeypatch.setattr(approval_context, "_get_approval_mode", lambda: "manual")
        monkeypatch.delenv("HERMES_EXEC_ASK", raising=False)
        monkeypatch.delenv("HERMES_SESSION_PLATFORM", raising=False)
        approval._reset_denials("tdo-o1")

        res = approval.request_tool_approval("write_file", "sensitive path", rule_key="tdo")

        assert res["approved"] is False
        assert res.get("outcome") == "unavailable"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
