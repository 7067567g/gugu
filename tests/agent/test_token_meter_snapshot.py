"""Contract tests for the read-only token measurement facade (T-02).

``agent.token_meter`` is imported inside each test body on purpose: before the
facade exists the run must fail as failed assertions (ModuleNotFoundError
raised inside the test function), never as a collection error.

Covered contracts:
  (a) replaying the same durable tail at the same ``log_revision`` is stable;
  (b) a caller-provided ``usage`` anchor outranks the rough ``estimated`` one;
  (c) ``surface_delta_tokens`` keeps its sign (growth vs. shrink);
  (d) a declared budget is a hard, raising binding (not a comment constant);
  (e) the facade is read-only: no compression, no new turn, no input mutation.
"""

import copy
from unittest.mock import MagicMock

import pytest


def _tail():
    return [
        {"role": "system", "content": "you are a helpful agent"},
        {"role": "user", "content": "hello " * 50},
        {"role": "assistant", "content": "hi " * 40},
    ]


# ---------------------------------------------------------------------------
# (a) replay stability
# ---------------------------------------------------------------------------


def test_replay_same_durable_tail_and_log_revision_is_stable():
    from agent.token_meter import measure

    tail = _tail()
    first = measure(tail, log_revision=42)
    second = measure(copy.deepcopy(tail), log_revision=42)

    assert first.log_revision == 42
    assert second.log_revision == first.log_revision == 42
    assert first == second
    assert first.total_tokens > 0

    # A different revision is echoed verbatim; the measured surface is unchanged.
    third = measure(tail, log_revision=43)
    assert third.log_revision == 43
    assert third.total_tokens == first.total_tokens
    assert third.nodes == first.nodes


# ---------------------------------------------------------------------------
# (b) usage anchor outranks estimated
# ---------------------------------------------------------------------------


def test_usage_anchor_wins_over_estimated_baseline():
    from agent.token_meter import measure

    tail = _tail()
    estimated = measure(tail, log_revision=1)
    assert estimated.baseline.kind == "estimated"
    assert estimated.baseline.tokens == estimated.total_tokens

    anchored = measure(tail, log_revision=1, usage={"total_tokens": 1234})
    assert anchored.baseline.kind == "usage"
    assert anchored.baseline.tokens == 1234

    class _Usage:
        total_tokens = 99

    object_anchor = measure(tail, log_revision=1, usage=_Usage())
    assert object_anchor.baseline.kind == "usage"
    assert object_anchor.baseline.tokens == 99

    integer_anchor = measure(tail, log_revision=1, usage=7)
    assert integer_anchor.baseline.kind == "usage"
    assert integer_anchor.baseline.tokens == 7


# ---------------------------------------------------------------------------
# (c) signed surface delta
# ---------------------------------------------------------------------------


def test_surface_delta_tokens_preserves_sign():
    from agent.token_meter import measure

    tail = _tail()
    base = measure(tail, log_revision=1).total_tokens
    assert base > 25

    grew = measure(tail, log_revision=1, usage=base - 25)
    shrank = measure(tail, log_revision=1, usage=base + 25)

    assert grew.surface_delta_tokens == 25
    assert shrank.surface_delta_tokens == -25
    assert grew.surface_delta_tokens > 0 > shrank.surface_delta_tokens
    # The measured surface total does not move with the anchor.
    assert grew.total_tokens == shrank.total_tokens == base
    assert grew.baseline.tokens + grew.surface_delta_tokens == grew.total_tokens


# ---------------------------------------------------------------------------
# (d) declared budget is a hard binding
# ---------------------------------------------------------------------------


def test_declared_budget_is_a_hard_raising_binding():
    from agent.token_meter import BudgetExceededError, assert_within_budget, measure

    tail = _tail()
    measurement = measure(tail, log_revision=1)

    assert assert_within_budget(measurement, measurement.baseline.tokens + 100, label="ctx") == 100
    with pytest.raises(BudgetExceededError):
        assert_within_budget(measurement, measurement.baseline.tokens - 1, label="ctx")

    # The usage anchor, when present, is the number the budget binds to.
    anchored = measure(tail, log_revision=1, usage=measurement.baseline.tokens + 1000)
    with pytest.raises(BudgetExceededError):
        assert_within_budget(anchored, measurement.baseline.tokens + 500, label="ctx")
    assert assert_within_budget(anchored, measurement.baseline.tokens + 1000) == 0


# ---------------------------------------------------------------------------
# (e) read-only facade
# ---------------------------------------------------------------------------


def test_facade_is_read_only_and_never_compresses_or_starts_a_turn(monkeypatch):
    from agent.token_meter import assert_within_budget, measure

    import agent.context_compressor as context_compressor
    import agent.conversation_loop as conversation_loop

    compress = MagicMock(side_effect=AssertionError("compression must not be triggered"))
    run_turn = MagicMock(side_effect=AssertionError("a new turn must not be started"))
    monkeypatch.setattr(context_compressor.ContextCompressor, "compress", compress)
    monkeypatch.setattr(conversation_loop, "run_conversation", run_turn)

    tail = _tail()
    before = copy.deepcopy(tail)

    measurement = measure(tail, log_revision=5)
    assert_within_budget(measurement, measurement.baseline.tokens + 1)

    assert compress.call_count == 0
    assert run_turn.call_count == 0
    assert tail == before


# ---------------------------------------------------------------------------
# node breakdown
# ---------------------------------------------------------------------------


def test_nodes_break_down_every_message():
    from agent.token_meter import TokenMeasurement, measure

    tail = _tail()
    measurement = measure(tail, log_revision=1)

    assert isinstance(measurement, TokenMeasurement)
    assert measurement.baseline.kind in {"usage", "estimated"}
    assert len(measurement.nodes) == len(tail)
    assert [node.role for node in measurement.nodes] == ["system", "user", "assistant"]
    assert all(node.tokens > 0 for node in measurement.nodes)
    assert sum(node.tokens for node in measurement.nodes) == measurement.total_tokens
