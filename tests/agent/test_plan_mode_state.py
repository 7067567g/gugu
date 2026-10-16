"""Session-scoped plan mode: persistence, ``/plan off``, and ``exit_plan_mode``.

Regression coverage for the T-03 upgrade of ``/plan`` from a one-shot prompt to a
persisted, togglable session state. Plan mode is **log-only**: its flag never
enters the model request, so ``build_plan_prompt`` output is byte-identical
whether or not the session is in plan mode.

Isolation: this file is run with ``--noconftest`` in the WS-O3 harness command,
so the autouse fixture below (not ``tests/conftest.py``) pins ``HERMES_HOME`` to
a temp dir. It must never write to ``~/.hermes``.
"""

from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _reload_plan_mode():
    """Re-import the module from scratch, dropping every module-level cache."""
    import agent.plan_mode as plan_mode

    return importlib.reload(plan_mode)


class TestPlanModePersistence:
    def test_activate_then_is_true(self):
        from agent.plan_mode import is_plan_mode, set_plan_mode

        set_plan_mode("sess-1", True)
        assert is_plan_mode("sess-1") is True

    def test_default_is_false(self):
        from agent.plan_mode import is_plan_mode

        assert is_plan_mode("never-seen") is False

    def test_persists_across_module_reload(self):
        """A fresh import (empty caches) still reads the stored flag from disk."""
        from agent import plan_mode

        plan_mode.set_plan_mode("sess-2", True)
        plan_mode.reset_cache()
        reloaded = _reload_plan_mode()
        assert reloaded.is_plan_mode("sess-2") is True

    def test_off_clears_active_and_pending(self):
        from agent.plan_mode import (
            get_pending_plan,
            is_plan_mode,
            set_pending_plan,
            set_plan_mode,
        )

        set_plan_mode("sess-3", True)
        set_pending_plan("sess-3", "# plan\nbody")
        set_plan_mode("sess-3", False)  # equivalent to /plan off
        assert is_plan_mode("sess-3") is False
        assert get_pending_plan("sess-3") is None

    def test_clear_plan_mode_helper(self):
        from agent.plan_mode import clear_plan_mode, is_plan_mode, set_plan_mode

        set_plan_mode("sess-4", True)
        clear_plan_mode("sess-4")
        assert is_plan_mode("sess-4") is False


class TestExitPlanMode:
    def test_rejects_plan_without_heading(self):
        from agent.plan_mode import exit_plan_mode

        payload = json.loads(exit_plan_mode("没有标题", session_id="sess-exit-1"))
        assert payload["success"] is False
        assert payload["outcome"] == "keep-planning"

    def test_accepts_markdown_heading(self):
        from agent.plan_mode import exit_plan_mode

        plan = "# 计划\n\n## 步骤\n- 做 A"
        payload = json.loads(exit_plan_mode(plan, session_id="sess-exit-2"))
        assert payload["success"] is True
        assert payload["outcome"] == "approved"

    def test_dismiss_clears_state(self):
        from agent.plan_mode import exit_plan_mode, is_plan_mode, set_plan_mode

        set_plan_mode("sess-exit-3", True)
        payload = json.loads(exit_plan_mode("", session_id="sess-exit-3", dismiss=True))
        assert payload["outcome"] == "dismiss"
        assert is_plan_mode("sess-exit-3") is False


class TestPlanPromptIsLogOnly:
    def test_prompt_bytes_independent_of_plan_state(self):
        from agent.plan_mode import set_plan_mode
        from agent.plan_prompt import build_plan_prompt

        before = build_plan_prompt("migrate auth to OIDC")
        set_plan_mode("sess-prompt", True)
        assert build_plan_prompt("migrate auth to OIDC") == before


class TestCliPlanHandlerWiring:
    """D-004: the CLI handler must toggle the persisted flag, not just queue a prompt."""

    @staticmethod
    def _stub(session_id: str):
        class _StubCLI:
            pass

        stub = _StubCLI()
        stub.session_id = session_id
        stub.queued = []

        def _queue_prompt_turn(msg, command):
            stub.queued.append((msg, command))

        stub._queue_prompt_turn = _queue_prompt_turn
        return stub

    def test_plan_off_clears_persisted_state(self):
        from agent.plan_mode import is_plan_mode, set_plan_mode
        from hermes_cli.cli_commands_mixin import CLICommandsMixin

        stub = self._stub("sess-cli-off")
        set_plan_mode(stub.session_id, True)

        CLICommandsMixin._handle_plan_command(stub, "/plan off")

        assert is_plan_mode(stub.session_id) is False

    def test_plan_on_sets_persisted_state(self):
        from agent.plan_mode import is_plan_mode
        from hermes_cli.cli_commands_mixin import CLICommandsMixin

        stub = self._stub("sess-cli-on")

        CLICommandsMixin._handle_plan_command(stub, "/plan migrate auth to OIDC")

        assert is_plan_mode(stub.session_id) is True
        assert stub.queued and stub.queued[-1][1] == "/plan"
