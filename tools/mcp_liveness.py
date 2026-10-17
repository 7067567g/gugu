"""Application-backed MCP liveness parsing and user-facing status descriptions."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Literal, Optional

from hermes_platform import declaration
from hermes_platform.host import facts
from hermes_platform.resolver.app import AppDef, AppResolver
from hermes_platform.resolver.availability import Availability, availability
from hermes_platform.resolver.base import Effort
from hermes_platform.resolver.core import CheckState

logger = logging.getLogger(__name__)

LivenessKind = Literal["static", "server_json", "interactive_session"]
LivenessState = Literal[
    "app_not_running",
    "endpoint_unavailable",
    "no_interactive_session",
    "version_too_old",
    "missing_app",
]
Retry = Literal["after_user_action", "never_here"]


@dataclass(frozen=True)
class Liveness:
    kind: LivenessKind
    path: str = ""
    url_field: str = "http"
    token_field: str = "token"
    pid_field: str = "pid"

    def app_definition(self, definition: AppDef) -> AppDef:
        if self.kind != "server_json":
            return definition
        return replace(
            definition,
            liveness_kind="server_json",
            liveness_path=self.path,
            liveness_pid_key=self.pid_field,
            liveness_url_key=self.url_field,
            liveness_token_key=self.token_field,
        )


@dataclass(frozen=True)
class Status:
    state: LivenessState
    availability: Availability
    liveness: Liveness
    user_action: str
    retry: Retry


def parse_liveness(raw: Any) -> Liveness:
    """Parse one portable-plugin liveness declaration."""
    if not isinstance(raw, dict):
        raise ValueError("liveness must be an object")
    kind = raw.get("kind")
    if kind == "static":
        if set(raw) != {"kind"}:
            raise ValueError("static liveness only accepts 'kind'")
        return Liveness("static")
    if kind == "interactive_session":
        if set(raw) != {"kind"}:
            raise ValueError("interactive_session liveness only accepts 'kind'")
        return Liveness("interactive_session")
    if kind != "server_json":
        raise ValueError(f"unknown liveness kind: {kind!r}")
    if set(raw) - {"kind", "path", "fields"}:
        raise ValueError("server_json liveness has unknown fields")
    path = raw.get("path")
    fields = {"url": "http", "token": "token", "pid": "pid", **(raw.get("fields") or {})}
    if not isinstance(path, str) or not path.strip():
        raise ValueError("server_json liveness requires a non-empty path")
    if set(fields) != {"url", "token", "pid"}:
        raise ValueError("server_json liveness fields may only override url, token, and pid")
    if any(not isinstance(value, str) or not value.strip() for value in fields.values()):
        raise ValueError("server_json liveness field names must be non-empty strings")
    return Liveness(
        "server_json",
        path=path.strip(),
        url_field=fields["url"].strip(),
        token_field=fields["token"].strip(),
        pid_field=fields["pid"].strip(),
    )


def liveness_for(server_name: str) -> Liveness:
    """Return a server's registered liveness declaration, defaulting to static."""
    try:
        from hermes_cli.agent_plugins import liveness_for as registered_liveness
    except ImportError:
        return Liveness("static")
    raw = registered_liveness(server_name)
    if raw is None:
        return Liveness("static")
    try:
        return parse_liveness(raw)
    except ValueError as exc:
        logger.warning("MCP server '%s' has an invalid liveness declaration (%s); treating it as static", server_name, exc)
        return Liveness("static")


def _action(state: LivenessState, app_name: str) -> tuple[str, Retry]:
    actions: dict[LivenessState, tuple[str, Retry]] = {
        "app_not_running": (f"Start {app_name}, then try again.", "after_user_action"),
        "endpoint_unavailable": (f"Open {app_name} and enable its local connection, then try again.", "after_user_action"),
        "no_interactive_session": (f"Open an interactive desktop session and start {app_name}, then try again.", "never_here"),
        "version_too_old": (f"Update {app_name}, then try again.", "after_user_action"),
        "missing_app": (f"Install {app_name}, then try again.", "after_user_action"),
    }
    return actions[state]


def status(server_name: str) -> Status | None:
    """Return the current unavailable state for a registered declaration."""
    decl = declaration.lookup(server_name)
    if decl is None:
        return None
    available = availability(decl)
    live = liveness_for(server_name)
    if available.state in {"missing_app", "unsupported_os"}:
        state: LivenessState = "missing_app"
    elif available.state == "version_too_old":
        state = "version_too_old"
    elif live.kind == "interactive_session" and not facts.interactive_session():
        state = "no_interactive_session"
    elif live.kind == "server_json":
        definition = decl.app_for(facts.os_family())
        if definition is None:
            state = "missing_app"
        else:
            probe = AppResolver(live.app_definition(definition)).probe(
                AppResolver(live.app_definition(definition)).locate(), effort=Effort.LOCAL
            )
            if probe.running.value is not True:
                state = "app_not_running"
            elif probe.endpoint.state is not CheckState.PRESENT:
                state = "endpoint_unavailable"
            else:
                state = "app_not_running"
    else:
        state = "app_not_running"
    action, retry = _action(state, decl.name)
    return Status(state, available, live, action, retry)


def describe(decl: declaration.Declaration, available: Availability, liveness_state: LivenessState) -> str:
    """Compose one unavailable-state sentence with one user action."""
    app_name = decl.name
    action, _retry = _action(liveness_state, app_name)
    if liveness_state == "missing_app":
        reason = f"{app_name} is not installed."
    elif liveness_state == "version_too_old":
        found = f" version {available.version}" if available.version else ""
        minimum = f"; version {available.min_version} or newer is required" if available.min_version else ""
        reason = f"{app_name}{found} is too old{minimum}."
    elif liveness_state == "no_interactive_session":
        reason = f"{app_name} needs an interactive desktop session."
    elif liveness_state == "endpoint_unavailable":
        reason = f"{app_name}'s local endpoint is unavailable."
    else:
        reason = f"{app_name} is not running."
    return f"{reason} {action}"


def unavailable_details(server_name: str) -> tuple[declaration.Declaration, Status, str] | None:
    """Return declaration, structured state, and its composed sentence."""
    decl = declaration.lookup(server_name)
    current = status(server_name)
    if decl is None or current is None:
        return None
    return decl, current, describe(decl, current.availability, current.state)


# --------------------------------------------------------------------------- #
# Transport-closed liveness watchdog (WS-O1 / T-01)
#
# Application-backed liveness above only describes unavailable *declarations*.
# Once a client is connected and ``Ready``, the transport can still close
# underneath it (server restart, idle TCP reap, cancelled stream pump) with no
# lifecycle event firing until the next call burns a full tool timeout. This
# watchdog is the first-class signal: one one-shot poller per Ready client.
# --------------------------------------------------------------------------- #

READY_STATE = "Ready"


def _default_thread_spawn(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name="mcp-transport-liveness", daemon=True).start()


@dataclass(frozen=True)
class TransportClosed:
    """One transport-closed observation, bound to the client instance that armed it."""

    client_id: str
    server_name: str = ""
    reason: str = "transport_closed"


def event_matches_client(event: TransportClosed, current_client_id: Optional[str]) -> bool:
    """True only when *event* belongs to the client instance still live.

    A same-name replacement connection is a DIFFERENT client instance; comparing
    ids (never names) is what keeps a stale predecessor's event from evicting it.
    """
    return current_client_id is not None and event.client_id == current_client_id


def _pid_alive(pid: Any) -> Optional[bool]:
    """POSIX ``kill(pid, 0)`` liveness, falling back to psutil; None when unknown."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        try:
            import psutil

            return bool(psutil.pid_exists(pid))
        except Exception:
            return None


def transport_is_closed(transport: Any) -> Optional[bool]:
    """Best-effort duck-typed read of a transport's closed state.

    Returns True/False when the transport exposes a trustworthy handle
    (``is_transport_closed`` / ``is_closed`` / ``is_closing`` / ``closed`` — a
    bool attribute or a zero-arg callable) and None when the answer is unknown.
    ``None`` is a first-class result: the watchdog keeps polling rather than
    emitting a false close. stdio transports that expose their child pid/pids
    are inferred from process liveness; transports exposing neither are a
    documented gap (see the WS-O1 report's known limitations).
    """
    if transport is None:
        return None
    for name in ("is_transport_closed", "is_closed", "is_closing", "closed"):
        try:
            value = getattr(transport, name, None)
        except Exception:
            logger.debug("transport_is_closed: %r accessor raised; trying next handle", name, exc_info=True)
            continue
        if value is None:
            continue
        try:
            result = value() if callable(value) else value
        except Exception:
            logger.debug("transport_is_closed: %r raised; trying next handle", name, exc_info=True)
            continue
        if isinstance(result, bool):
            return result
    for name in ("child_pids", "pids", "pid"):
        try:
            pids = getattr(transport, name, None)
        except Exception:
            continue
        if pids is None:
            continue
        if isinstance(pids, (int, str)):
            pids = [pids]
        try:
            probes = [_pid_alive(pid) for pid in pids]
        except TypeError:
            continue
        known = [alive for alive in probes if alive is not None]
        if known:
            return not any(known)
    return None


@dataclass
class _LivenessSlot:
    client_id: str
    server_name: str
    state_getter: Callable[[], str]
    is_closed: Callable[[], Optional[bool]]


class TransportLivenessWatchdog:
    """One-shot poller per ``Ready`` MCP client that reports a closed transport.

    ``arm`` binds a slot to one client instance. The optional spawned thread runs
    one pass every ``poll_interval`` seconds; ``poll`` exposes the same pass
    synchronously for deterministic use. The first pass that observes the client
    still ``Ready`` with a CLOSED transport emits exactly one
    :class:`TransportClosed` carrying that client's id and removes the slot, so a
    replacement connection can re-arm immediately. A state drift out of ``Ready``
    (re-handshake / ``reset_transport``) removes the slot silently — a stale
    generation never emits a false close.
    """

    _UNSET = object()

    def __init__(self, *, poll_interval: float = 0.5, on_event: Optional[Callable[[TransportClosed], None]] = None,
                 spawn: Any = _UNSET, sleep: Optional[Callable[[float], None]] = None):
        self.poll_interval = float(poll_interval)
        self._on_event = on_event
        self._spawn = _default_thread_spawn if spawn is self._UNSET else spawn
        self._sleep = sleep or time.sleep
        self._lock = threading.Lock()
        self._slots: dict[str, _LivenessSlot] = {}

    def arm(self, client_id: str, *, server_name: str = "",
            state_getter: Callable[[], str] | str,
            is_closed: Optional[Callable[[], Optional[bool]]] = None,
            transport: Any = None) -> _LivenessSlot:
        """Register the one-shot poller for *client_id* (bound to that instance)."""
        if is_closed is None:
            if transport is None:
                raise ValueError("arm() requires is_closed or transport")
            def is_closed() -> Optional[bool]:
                return transport_is_closed(transport)
        getter = state_getter if callable(state_getter) else (lambda: state_getter)
        slot = _LivenessSlot(str(client_id), server_name, getter, is_closed)
        with self._lock:
            self._slots[slot.client_id] = slot
        if self._spawn is not None:
            self._spawn(lambda: self._run_slot(slot))
        return slot

    def has_slot(self, client_id: str) -> bool:
        with self._lock:
            return str(client_id) in self._slots

    def slot_count(self) -> int:
        with self._lock:
            return len(self._slots)

    def disarm(self, client_id: str) -> bool:
        with self._lock:
            return self._slots.pop(str(client_id), None) is not None

    def poll(self) -> list[TransportClosed]:
        """Evaluate every armed slot once; return the events emitted this pass."""
        with self._lock:
            slots = list(self._slots.values())
        events: list[TransportClosed] = []
        for slot in slots:
            event = self._poll_slot(slot)
            if event is not None:
                events.append(event)
        return events

    def _poll_slot(self, slot: _LivenessSlot) -> Optional[TransportClosed]:
        try:
            state = slot.state_getter()
        except Exception:
            logger.debug("transport liveness: state getter for client %s raised", slot.client_id, exc_info=True)
            self._drop(slot)
            return None
        if state != READY_STATE:
            self._drop(slot)  # drifted out of Ready: silent exit, never a false close
            return None
        try:
            closed = slot.is_closed()
        except Exception:
            logger.debug("transport liveness: closed probe for client %s raised", slot.client_id, exc_info=True)
            return None
        if closed is None or not closed:
            return None
        if not self._drop(slot):
            return None  # another pass already emitted for this generation
        event = TransportClosed(slot.client_id, slot.server_name)
        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:
                logger.debug("transport liveness: event sink raised", exc_info=True)
        return event

    def _drop(self, slot: _LivenessSlot) -> bool:
        with self._lock:
            if self._slots.get(slot.client_id) is slot:
                del self._slots[slot.client_id]
                return True
        return False

    def _run_slot(self, slot: _LivenessSlot) -> None:
        while self.has_slot(slot.client_id):
            try:
                self._sleep(self.poll_interval)
            except Exception:
                return
            if not self.has_slot(slot.client_id):
                return
            if self._poll_slot(slot) is not None:
                return  # one-shot: the slot is gone, the poller exits


# --------------------------------------------------------------------------- #
# 404 "Session not found" -> re-initialize + schema cache invalidation (T-01)
# --------------------------------------------------------------------------- #

_SESSION_NOT_FOUND_MARKERS: tuple[str, ...] = (
    "session not found",
    "session expired",
    "session does not exist",
    "session is not valid",
    "unknown session",
    "invalid session",
    "no such session",
    "session not initialized",
)


def _extract_http_status(error: Any) -> Optional[int]:
    for attr in ("status_code", "status"):
        value = getattr(error, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _extract_response_text(error: Any) -> str:
    response = getattr(error, "response", None)
    for attr in ("text", "content"):
        value = getattr(response, attr, None)
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8", "replace")
            except Exception:
                value = ""
        if isinstance(value, str) and value:
            return value
    return ""


def is_session_not_found(error: Any = None, *, status_code: Optional[int] = None, body: Any = None) -> bool:
    """True when *error* / *status_code* / *body* describe a 404 ``Session not found``.

    A bare 404 (endpoint missing) or the session marker on a non-404 status is a
    different failure and must NOT trigger the re-initialize path.
    """
    effective_status = status_code if status_code is not None else _extract_http_status(error)
    if effective_status is not None and effective_status != 404:
        return False
    parts = []
    if body is not None:
        parts.append(str(body))
    if error is not None:
        parts.append(str(error))
        parts.append(type(error).__name__)
        for arg in getattr(error, "args", ()) or ():
            parts.append(str(arg))
    parts.append(_extract_response_text(error))
    haystack = " ".join(part for part in parts if part).lower()
    if not haystack:
        return False
    return any(marker in haystack for marker in _SESSION_NOT_FOUND_MARKERS)


def invalidate_schema_cache(server_name: str) -> bool:
    """Drop one server's persisted tool schema so the next discovery re-lists it.

    Best-effort and non-blocking: a cache that cannot be rewritten must not
    derail the caller's recovery path.
    """
    if not server_name:
        return False
    try:
        from tools import mcp_schema_cache as _schema_cache

        with _schema_cache._cache_lock:
            data = _schema_cache._load_all()
            if server_name not in data:
                return False
            del data[server_name]
            _schema_cache._save_all(data)
        return True
    except Exception:
        logger.debug("MCP session recovery: could not invalidate schema cache for %s", server_name, exc_info=True)
        return False


def recover_session_not_found(server_name: str = "", error: Any = None, *,
                              status_code: Optional[int] = None, body: Any = None,
                              reinitialize: Optional[Callable[[str], None]] = None,
                              invalidate_cache: Optional[Callable[[str], None]] = None) -> bool:
    """Handle a 404 ``Session not found``: invalidate the schema cache, then re-initialize.

    Both hooks are injectable. ``invalidate_cache`` defaults to
    :func:`invalidate_schema_cache`; ``reinitialize`` has no default because the
    actual re-handshake belongs to the caller's reconnect loop. Returns True only
    when the error matched. Hook failures are logged, never raised, so a
    read-only recovery attempt can never block or leak the caller's task.
    """
    if not is_session_not_found(error, status_code=status_code, body=body):
        return False
    invalidator = invalidate_cache or invalidate_schema_cache
    try:
        invalidator(server_name)
    except Exception:
        logger.debug("MCP session recovery: invalidate_cache hook failed for %s", server_name, exc_info=True)
    if reinitialize is not None:
        try:
            reinitialize(server_name)
        except Exception:
            logger.debug("MCP session recovery: reinitialize hook failed for %s", server_name, exc_info=True)
    return True
