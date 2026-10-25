"""M3b navigate: four screens, renderer ack, timeout and read_state landing.

The agent and the desktop are separate processes. These tests exercise the real
cross-process boundary with real files under a temp ``HERMES_HOME``: the executor
writes ``designs/navigation.json``, a simulated renderer reads it and publishes
``designs/agent-state.json`` with the same ``nav_request_id``. Nothing here mocks
the executor's file I/O — that is the point of the test.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict

import pytest

from plugins.gugu_capabilities import executor, registry, store

SCREENS = {
    "work": ("/", "work"),
    "design": ("/design", "design"),
    "settings": ("/settings", "settings"),
    "artifacts": ("/artifacts", "artifacts"),
}


@pytest.fixture()
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("GUGU_NAV_TIMEOUT_MS", "1200")
    return home


def _write_runtime_state(payload: Dict[str, Any]) -> None:
    path = store.runtime_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _start_renderer(route: str, surface: str, *, request_id: str | None = None, delay: float = 0.0) -> threading.Thread:
    """Emulate the desktop runtime: read the mailbox, publish the landed route."""

    def run() -> None:
        deadline = time.time() + 5.0
        navigation = None
        while time.time() < deadline:
            navigation = store.read_navigation()
            if navigation is not None:
                break
            time.sleep(0.01)
        if navigation is None:
            return
        if delay:
            time.sleep(delay)
        _write_runtime_state(
            {
                "surface": surface,
                "route": route,
                "screen": navigation.get("screen"),
                # ``request_id`` lets a test publish a decoy ack first.
                "nav_request_id": request_id or navigation["request_id"],
                "project_id": None,
                "selection": [],
                "updated_at": time.time(),
            }
        )
        time.sleep(0.3)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_navigate_is_a_ready_m3_contract_capability() -> None:
    assert registry.validate_registry() == []
    entry = registry.get_capability("navigate")
    assert entry is not None
    assert entry["status"] == "ready"
    assert entry["milestone"] == "M3"
    assert entry["requires_approval"] is False
    assert "navigate" in executor._HANDLERS
    # No capability is deliberately deferred any more: ready means callable.
    assert [item["id"] for item in registry.list_capabilities() if item["status"] == "planned"] == []
    for capability in registry.list_capabilities():
        if capability["status"] == "ready":
            assert capability["id"] in executor._HANDLERS, capability["id"]


@pytest.mark.parametrize("screen", sorted(SCREENS))
def test_navigate_lands_on_each_screen_and_read_state_confirms_it(home: Path, screen: str) -> None:
    route, surface = SCREENS[screen]
    renderer = _start_renderer(route, surface)
    result = executor.execute("navigate", {"screen": screen})
    renderer.join(timeout=2)

    assert result["ok"] is True, result
    data = result["data"]
    assert data["navigated"] is True
    assert data["screen"] == screen
    assert data["route"] == route
    assert data["surface"] == surface
    assert data["request_id"].startswith("nav-")
    # One-shot mailbox: the request is consumed, not replayable on restart.
    assert store.read_navigation() is None

    state = executor.execute("read_state", {"scope": ["surface"]})
    assert state["ok"] is True
    assert state["data"]["surface"]["route"] == route
    assert state["data"]["surface"]["surface"] == surface


def test_navigate_reports_failure_without_a_renderer(home: Path, monkeypatch) -> None:
    monkeypatch.setenv("GUGU_NAV_TIMEOUT_MS", "150")
    result = executor.execute("navigate", {"screen": "settings"})

    assert result["ok"] is True
    data = result["data"]
    assert data["navigated"] is False
    assert data["reason"] == "no_renderer"
    assert data["route"] == ""
    assert store.read_navigation() is None


def test_navigate_ignores_a_decoy_ack_for_another_request(home: Path, monkeypatch) -> None:
    monkeypatch.setenv("GUGU_NAV_TIMEOUT_MS", "1500")
    # Decoy first: same route, wrong request id. Then the real ack.
    decoy = threading.Thread(
        target=_write_decoy_then_real,
        args=("/artifacts", "artifacts"),
        daemon=True,
    )
    decoy.start()
    result = executor.execute("navigate", {"screen": "artifacts"})
    decoy.join(timeout=3)

    assert result["ok"] is True
    assert result["data"]["navigated"] is True
    assert result["data"]["route"] == "/artifacts"


def _write_decoy_then_real(route: str, surface: str) -> None:
    deadline = time.time() + 5.0
    navigation = None
    while time.time() < deadline:
        navigation = store.read_navigation()
        if navigation is not None:
            break
        time.sleep(0.01)
    if navigation is None:
        return
    _write_runtime_state(
        {
            "surface": surface,
            "route": route,
            "screen": navigation.get("screen"),
            "nav_request_id": "nav-someone-else",
            "selection": [],
            "updated_at": time.time(),
        }
    )
    time.sleep(0.15)
    _write_runtime_state(
        {
            "surface": surface,
            "route": route,
            "screen": navigation.get("screen"),
            "nav_request_id": navigation["request_id"],
            "selection": [],
            "updated_at": time.time(),
        }
    )
    time.sleep(0.3)


def test_navigate_reports_timeout_when_the_renderer_never_acks(home: Path, monkeypatch) -> None:
    monkeypatch.setenv("GUGU_NAV_TIMEOUT_MS", "150")
    _write_runtime_state(
        {
            "surface": "work",
            "route": "/",
            "screen": "work",
            "nav_request_id": None,
            "selection": [],
            "updated_at": time.time(),
        }
    )

    result = executor.execute("navigate", {"screen": "design"})

    assert result["ok"] is True
    data = result["data"]
    assert data["navigated"] is False
    assert data["reason"] == "timeout"
    assert data["route"] == "/"
    assert store.read_navigation() is None


def test_navigate_rejects_a_screen_outside_the_contract(home: Path) -> None:
    result = executor.execute("navigate", {"screen": "code"})
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_arguments"
    assert store.read_navigation() is None
