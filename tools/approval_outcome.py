"""One closed approval-outcome vocabulary plus a fail-closed ``request`` facade.

Approval decisions used to be scattered stringly-typed ``outcome`` values across
the shell guards, the plugin/action gate and the write gates. T-02 collapses the
consent vocabulary to four words (``allowed-once``, ``rejected``, ``cancelled``,
``unavailable``) and routes the abnormal paths through one rule: no asker, an
asker that raises, and a ``None``/illegal answer all deny as ``unavailable``.

Every request also emits the audit pair ``approval/asked`` -> ``approval/decided``
to a log-only sink (default: this module's logger). The facade returns only the
outcome string, so the audit record cannot travel into a model-visible message.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

ALLOWED_ONCE = "allowed-once"
REJECTED = "rejected"
CANCELLED = "cancelled"
UNAVAILABLE = "unavailable"

OUTCOMES = frozenset({ALLOWED_ONCE, REJECTED, CANCELLED, UNAVAILABLE})

# Raw answers that may cross the boundary, normalized onto the closed set.
# Anything absent here (``None``, an illegal string, a non-string) denies.
_ANSWER_OUTCOMES = {
    "allow": ALLOWED_ONCE, "allowed": ALLOWED_ONCE, "allowed-once": ALLOWED_ONCE,
    "allowed_once": ALLOWED_ONCE, "once": ALLOWED_ONCE,
    "approve": ALLOWED_ONCE, "approved": ALLOWED_ONCE,
    "deny": REJECTED, "denied": REJECTED, "reject": REJECTED, "rejected": REJECTED,
    "cancel": CANCELLED, "cancelled": CANCELLED, "canceled": CANCELLED,
    "unavailable": UNAVAILABLE, "error": UNAVAILABLE, "blocked": UNAVAILABLE,
    "timeout": UNAVAILABLE, "notify_failed": UNAVAILABLE,
}

_AUDIT_FIELDS = ("kind", "pattern_key", "description", "target", "session_key")


def classify_outcome(answer: Any = None, *, error: Any = None) -> str:
    """Normalize a raw answer to the closed set. An error marker or an
    unknown/absent answer denies (``unavailable``); there is no implicit allow."""
    if error is not None:
        return UNAVAILABLE
    return _ANSWER_OUTCOMES.get(answer, UNAVAILABLE)


def _request_payload(request: Any) -> dict:
    """Audit fields of a request (mapping or object); ``target`` is truncated
    like the approval log lines so a long command cannot flood the log."""
    if isinstance(request, Mapping):
        raw = dict(request)
    else:
        raw = {field: getattr(request, field) for field in _AUDIT_FIELDS if hasattr(request, field)}
    payload = {field: raw[field] for field in _AUDIT_FIELDS
               if raw.get(field) is not None}
    target = payload.get("target")
    if isinstance(target, str) and len(target) > 200:
        payload["target"] = target[:200]
    return payload


def _emit(audit_sink, event: str, payload: dict) -> None:
    record = {"event": event, **payload}
    if audit_sink is not None:
        audit_sink(record)
    else:
        logger.info("%s %s", event, payload)


def emit_audit_pair(audit_sink, *, request: Any, outcome: str) -> None:
    """Write the ``approval/asked`` + ``approval/decided`` pair, in order, to a
    log-only sink. Returns nothing, so the audit record cannot reach a model
    message."""
    payload = _request_payload(request)
    _emit(audit_sink, "approval/asked", payload)
    _emit(audit_sink, "approval/decided", {**payload, "outcome": outcome})


def request(req: Any, *, asker: Optional[Callable[[Any], Any]] = None,
            audit_sink: Optional[Callable[[dict], None]] = None) -> str:
    """Resolve one approval request to the closed outcome set.

    ``asker`` is the callback that would obtain consent. Absent or raising, or
    answering anything outside the vocabulary, fails closed to ``unavailable``.
    Emits the audit pair to ``audit_sink`` (default logger) and returns only the
    outcome string."""
    error = None
    answer = None
    if asker is None:
        error = "no_answerer"
    else:
        try:
            answer = asker(req)
        except Exception:
            logger.debug("approval asker raised; failing closed", exc_info=True)
            error = "asker_error"
    outcome = classify_outcome(answer, error=error)
    emit_audit_pair(audit_sink, request=req, outcome=outcome)
    return outcome
