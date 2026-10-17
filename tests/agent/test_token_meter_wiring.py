"""Wiring contracts for the token-meter facade in the request preflight (T-01).

``agent.token_meter`` shipped in v0.2.0 but production never called it (R-002).
These tests pin the two wiring boundaries:

  (a) ``_preflight_request_tokens`` routes through ``token_meter.measure`` and
      stores the snapshot on the agent; with a live usage anchor the returned
      figure is exactly ``measure(...).baseline.tokens`` (anchor semantics land
      through the facade), while a missing anchor still prefers the native
      wire estimate over ``measure``'s rough ``estimated`` baseline;
  (b) a request whose compression is blocked (summary cooldown) but which no
      longer fits the model window is refused before the provider call, with
      the same ``/compress`` / ``/new`` guidance; a blocked request that still
      fits the window keeps the warn-and-continue behaviour.

Every ``agent`` import lives inside the test body on purpose: before the wiring
exists these tests fail as assertions (never as collection errors).
"""

from __future__ import annotations

import types
from unittest.mock import patch

import pytest


def _messages():
    return [
        {"role": "system", "content": "you are a helpful agent"},
        {"role": "user", "content": "hello " * 20},
        {"role": "assistant", "content": "hi " * 20},
        {"role": "user", "content": "continue " * 20},
    ]


def _run_build(agent):
    """Drive ``build_turn_context`` through the cooldown-blocked preflight branch.

    The estimate is forced through the generic fallback (``estimate_request_tokens_rough``)
    so the scenario is deterministic: the native Responses estimator is stubbed out and the
    rough estimate is the caller's chosen number.
    """
    from unittest.mock import patch

    from agent.turn_context import build_turn_context

    with patch("agent.auxiliary_client.set_runtime_main", lambda *a, **k: None), \
         patch("agent.turn_context._should_run_preflight_estimate", return_value=True), \
         patch("agent.codex_responses_adapter.estimate_native_responses_preflight_tokens", return_value=None), \
         patch("agent.turn_context.estimate_request_tokens_rough", return_value=80_000):
        return build_turn_context(
            agent=agent,
            user_message="hello",
            system_message=None,
            conversation_history=None,
            task_id=None,
            stream_callback=None,
            persist_user_message=None,
            restore_or_build_system_prompt=lambda *a, **k: None,
            install_safe_stdio=lambda: None,
            sanitize_surrogates=lambda s: s,
            summarize_user_message_for_log=lambda s: s,
            set_session_context=lambda _sid: None,
            set_current_write_origin=lambda _o: None,
            ra=lambda: types.SimpleNamespace(_set_interrupt=lambda *a, **k: None),
        )


def _blocked_agent(context_length):
    """``_FakeAgent`` with compression on and a live summary cooldown."""
    from tests.agent.test_turn_context import _FakeAgent

    agent = _FakeAgent()
    agent.compression_enabled = True
    warnings = []
    agent._emit_warning = warnings.append
    agent.context_compressor = types.SimpleNamespace(
        protect_first_n=2,
        protect_last_n=2,
        threshold_tokens=30_000,
        context_length=context_length,
        summary_target_ratio=0.3,
        last_prompt_tokens=0,
        last_real_prompt_tokens=0,
        should_compress=lambda tokens=None: False,
        should_compress_info=lambda tokens=None: (False, "cooldown:30"),
        get_active_compression_failure_cooldown=lambda: {"remaining_seconds": 30.0},
    )
    return agent, warnings


# ---------------------------------------------------------------------------
# (a) preflight -> token_meter.measure
# ---------------------------------------------------------------------------


def test_preflight_calls_token_meter_measure(monkeypatch):
    from agent import token_meter
    from agent.turn_context import _preflight_request_tokens
    from agent.usage_anchor import capture_usage_anchor

    messages = _messages()
    anchor = capture_usage_anchor(1_000, 50, messages[:3])
    assert anchor is not None, "fixture anchor must be valid"

    calls = []
    real_measure = token_meter.measure

    def counting_measure(*args, **kwargs):
        calls.append((args, kwargs))
        return real_measure(*args, **kwargs)

    # Lazy import inside ``_preflight_request_tokens`` means the module attribute
    # is the seam the production call site reads.
    monkeypatch.setattr(token_meter, "measure", counting_measure)

    class _Agent:
        tools = []

    agent = _Agent()
    agent._usage_anchor = anchor

    result = _preflight_request_tokens(agent, messages, "")

    assert len(calls) >= 1, "preflight must measure through agent.token_meter.measure"
    snapshot = agent._last_token_measurement
    assert snapshot.baseline.kind == "usage", "a live usage anchor must reach measure()"
    assert result == snapshot.baseline.tokens

    # The anchor figure is the returned preflight value, not a second estimator.
    _, kwargs = calls[0]
    assert kwargs.get("usage") is not None


def test_preflight_without_anchor_keeps_native_estimate(monkeypatch):
    """``measure``'s estimated baseline must not replace the native wire estimate."""
    from agent import codex_responses_adapter, token_meter, turn_context

    messages = _messages()
    calls = []
    real_measure = token_meter.measure

    def counting_measure(*args, **kwargs):
        calls.append((args, kwargs))
        return real_measure(*args, **kwargs)

    monkeypatch.setattr(token_meter, "measure", counting_measure)
    monkeypatch.setattr(
        codex_responses_adapter,
        "estimate_native_responses_preflight_tokens",
        lambda *a, **k: 4_321,
    )

    class _Agent:
        tools = []
        _usage_anchor = None

    agent = _Agent()
    result = turn_context._preflight_request_tokens(agent, messages, "")

    assert len(calls) >= 1, "preflight must still exercise the facade without an anchor"
    assert agent._last_token_measurement.baseline.kind == "estimated"
    assert result == 4_321, "native wire estimate must stay authoritative over rough estimate"


# ---------------------------------------------------------------------------
# (b) blocked preflight: hard refusal only above the model window
# ---------------------------------------------------------------------------


def test_blocked_over_window_refuses_send():
    from agent.turn_context import PreflightCompressionTimedOut

    agent, warnings = _blocked_agent(context_length=50_000)

    with pytest.raises(PreflightCompressionTimedOut) as excinfo:
        _run_build(agent)

    message = str(excinfo.value)
    assert "/compress" in message or "/new" in message, message
    assert warnings, "the existing blocked-overflow warning must still fire"
    assert "cooldown" in warnings[0]


def test_blocked_below_window_still_warns_and_continues():
    agent, warnings = _blocked_agent(context_length=200_000)

    ctx = _run_build(agent)

    assert ctx.messages[-1]["role"] == "user", "a fitting blocked request keeps sending"
    assert len(warnings) == 1
    assert "cooldown" in warnings[0]
