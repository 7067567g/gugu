"""Read-only token measurement facade (T-02).

Turns a declared context/compression budget into a *hard binding* and gives the
message tail a replayable token snapshot, without changing compression
behaviour. Everything here is read-only:

* no compression is triggered,
* no turn is started,
* the caller's durable tail is never mutated.

The token estimate reuses the existing rough estimator
(``agent.model_metadata.estimate_tokens_rough``); no new tokenizer or third
party dependency is introduced. ``measure`` stays pure: the same durable tail
at the same ``log_revision`` always yields an equal snapshot.

Field semantics
---------------

``baseline``
    The preferred ground truth for the surface: the caller-supplied ``usage``
    anchor when present (``kind="usage"``), otherwise the rough estimate of the
    surface (``kind="estimated"``).
``total_tokens``
    The rough estimate of the measured surface, always equal to the sum of
    ``nodes[*].tokens``.
``surface_delta_tokens``
    Signed correction ``total_tokens - baseline.tokens``. Positive means the
    rough estimate sits above the anchor (growth), negative means below it
    (shrink). It is ``0`` when the baseline is itself the rough estimate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, Tuple

from agent.model_metadata import estimate_tokens_rough

__all__ = [
    "TokenBaseline",
    "TokenNode",
    "TokenMeasurement",
    "BudgetExceededError",
    "measure",
    "assert_within_budget",
]

BASELINE_KIND_USAGE = "usage"
BASELINE_KIND_ESTIMATED = "estimated"
BASELINE_KINDS = (BASELINE_KIND_USAGE, BASELINE_KIND_ESTIMATED)


class BudgetExceededError(RuntimeError):
    """Raised by :func:`assert_within_budget` when a measurement is over budget.

    An explicit ``raise`` (never a bare ``assert``) keeps the binding active
    under ``python -O``.
    """

    def __init__(self, measurement: "TokenMeasurement", declared_budget: int, *, label: str = "") -> None:
        self.measurement = measurement
        self.declared_budget = declared_budget
        self.label = label
        where = f" [{label}]" if label else ""
        super().__init__(
            f"token budget exceeded{where}: used {measurement.baseline.tokens} "
            f"of {declared_budget} declared tokens "
            f"(over by {measurement.baseline.tokens - declared_budget})"
        )


@dataclass(frozen=True)
class TokenBaseline:
    """Anchor the snapshot is measured against: ``"usage"`` or ``"estimated"``."""

    kind: str
    tokens: int


@dataclass(frozen=True)
class TokenNode:
    """Per-message breakdown of the measured surface."""

    index: int
    role: str
    tokens: int


@dataclass(frozen=True)
class TokenMeasurement:
    """Immutable, replayable snapshot of a durable message tail."""

    log_revision: int
    baseline: TokenBaseline
    surface_delta_tokens: int
    total_tokens: int
    nodes: Tuple[TokenNode, ...]


def _default_counter(text: str) -> int:
    return int(estimate_tokens_rough(text))


def _serialize(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def _node_text(message: Any) -> str:
    """Stable text for one message: content plus the tool-call envelope."""
    if not isinstance(message, Mapping):
        return _serialize(message)
    parts = []
    content = message.get("content")
    if content:
        parts.append(_serialize(content))
    tool_calls = message.get("tool_calls")
    if tool_calls:
        parts.append(_serialize(tool_calls))
    return "\n".join(parts)


def _usage_tokens(usage: Any) -> int:
    """Extract the anchor token count from an int, mapping or usage object."""
    if isinstance(usage, bool):
        raise TypeError("usage anchor must be an int, mapping or usage object")
    if isinstance(usage, int):
        return int(usage)
    keys = ("total_tokens", "tokens", "prompt_tokens", "input_tokens")
    if isinstance(usage, Mapping):
        for key in keys:
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return int(value)
    else:
        for key in keys:
            value = getattr(usage, key, None)
            if isinstance(value, int) and not isinstance(value, bool):
                return int(value)
    raise TypeError(f"unsupported usage anchor: {usage!r}")


def measure(
    messages: Iterable[Any],
    *,
    log_revision: int,
    usage: Any = None,
    counter: Optional[Callable[[str], int]] = None,
) -> TokenMeasurement:
    """Measure a durable message tail without mutating it.

    ``usage`` (an int, a mapping with ``total_tokens``/``prompt_tokens``/
    ``input_tokens``, or an object exposing those attributes) is the preferred
    anchor; when absent the rough estimate of the surface is the baseline.
    """
    estimate = counter or _default_counter
    nodes = tuple(
        TokenNode(
            index=index,
            role=str(message.get("role", "")) if isinstance(message, Mapping) else "",
            tokens=int(estimate(_node_text(message))),
        )
        for index, message in enumerate(tuple(messages or ()))
    )
    total_tokens = sum(node.tokens for node in nodes)

    if usage is None:
        baseline = TokenBaseline(kind=BASELINE_KIND_ESTIMATED, tokens=total_tokens)
    else:
        baseline = TokenBaseline(kind=BASELINE_KIND_USAGE, tokens=_usage_tokens(usage))

    return TokenMeasurement(
        log_revision=int(log_revision),
        baseline=baseline,
        surface_delta_tokens=total_tokens - baseline.tokens,
        total_tokens=total_tokens,
        nodes=nodes,
    )


def assert_within_budget(measurement: TokenMeasurement, declared_budget: int, *, label: str = "") -> int:
    """Assert ``measurement`` fits ``declared_budget``; return remaining tokens.

    The authoritative number is ``measurement.baseline.tokens`` (a caller-
    supplied usage anchor when present, else the rough estimate). Over-budget
    raises :class:`BudgetExceededError` rather than returning a negative
    remainder, so the declared budget cannot be silently ignored.
    """
    budget = int(declared_budget)
    if budget < 0:
        raise ValueError("declared_budget must be non-negative")
    remaining = budget - measurement.baseline.tokens
    if remaining < 0:
        raise BudgetExceededError(measurement, budget, label=label)
    return remaining
