"""M2 guidance: anchor inventory, highlight/clear_guidance and the mailbox.

The renderer owns the overlay; the agent process owns the command. These tests
exercise the real boundary between them: registry -> executor -> JSON file, and
the gugu-design GET/DELETE endpoints the desktop reads. Zero-residue DOM
assertions live in ``apps/desktop/src/plugins/gugu-design/guidance.test.ts``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from plugins.gugu_capabilities import executor, registry, store

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_API = REPO_ROOT / "plugins" / "gugu-design" / "dashboard" / "plugin_api.py"


def _load_module(path: Path, name: str) -> Any:
    assert path.is_file(), f"missing module under test: {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


@pytest.fixture()
def plugin_api(home: Path) -> Any:
    return _load_module(PLUGIN_API, "gugu_guidance_plugin_api")


def _highlight(**arguments: Any) -> dict[str, Any]:
    return executor.execute("highlight", arguments)


# ---------------------------------------------------------------------------
# Registry contract
# ---------------------------------------------------------------------------


def test_highlight_and_clear_guidance_are_ready_contract_capabilities():
    assert registry.validate_registry() == []
    for capability_id in ("highlight", "clear_guidance"):
        entry = registry.get_capability(capability_id)
        assert entry is not None
        assert entry["status"] == "ready", capability_id
        assert entry["milestone"] == "M2", capability_id
        assert entry["requires_approval"] is False
        assert entry["ui_anchors"] == ["guidance.overlay"]


def test_list_capabilities_enumerates_targetable_anchors():
    result = executor.execute("list_capabilities", {})

    assert result["ok"] is True
    anchors = {anchor["id"]: anchor for anchor in result["data"]["anchors"]}
    assert anchors["design.canvas"]["targetable"] is True
    assert anchors["canvas.export"]["targetable"] is True
    assert anchors["guidance.overlay"]["status"] == "present"
    assert anchors["guidance.overlay"]["targetable"] is False
    # Every non-overlay anchor exists today, so every one of them is targetable.
    assert {anchor["id"] for anchor in anchors.values() if anchor["targetable"]} == {
        anchor_id for anchor_id in registry.ANCHORS if registry.ANCHORS[anchor_id]["surface"] != "guidance"
    }


def test_anchors_cover_work_settings_and_artifacts_surfaces():
    result = executor.execute("list_capabilities", {})
    anchors = {anchor["id"]: anchor for anchor in result["data"]["anchors"]}

    cross_surface = {"work.composer": "work", "settings.header": "settings", "artifacts.root": "artifacts"}
    for anchor_id, surface in cross_surface.items():
        assert anchors[anchor_id]["surface"] == surface
        assert anchors[anchor_id]["status"] == "present"
        assert anchors[anchor_id]["targetable"] is True


@pytest.mark.parametrize("anchor", ["work.composer", "settings.header", "artifacts.root"])
def test_highlight_works_on_every_registered_surface(home: Path, anchor: str):
    result = _highlight(target_anchor=anchor, text="跨界面引导")

    assert result["ok"] is True, result
    payload = store.read_guidance()
    assert payload is not None
    assert payload["target_anchor"] == anchor
    assert store.clear_guidance() is True


def test_registry_examples_for_highlight_still_match_schema():
    entry = registry.get_capability("highlight")
    assert entry is not None
    from plugins.gugu_capabilities import schema

    for example in entry["examples"]:
        assert schema.validate(example["arguments"], entry["input_schema"]) == []


# ---------------------------------------------------------------------------
# Executor -> mailbox
# ---------------------------------------------------------------------------


def test_highlight_writes_one_guidance_step(home: Path):
    result = _highlight(
        target_anchor="design.canvas",
        text="产出会落在这里",
        style="box",
        timeout_ms=4000,
    )

    assert result["ok"] is True, result
    data = result["data"]
    assert data["guidance_id"].startswith("gd-")
    assert data["target_anchor"] == "design.canvas"
    assert data["style"] == "box"
    assert data["timeout_ms"] == 4000

    payload = store.read_guidance()
    assert payload is not None
    assert payload["guidance_id"] == data["guidance_id"]
    assert payload["target_anchor"] == "design.canvas"
    assert payload["text"] == "产出会落在这里"
    assert payload["style"] == "box"
    assert payload["timeout_ms"] == 4000
    assert isinstance(payload["created_at"], float)
    # The mailbox lives beside agent-state.json, not inside a project.
    assert store.guidance_path() == home / "designs" / "guidance.json"


def test_highlight_defaults_to_highlight_style(home: Path):
    result = _highlight(target_anchor="canvas.export")
    assert result["ok"] is True, result
    payload = store.read_guidance()
    assert payload is not None
    assert payload["style"] == "highlight"
    assert payload["text"] == ""
    assert payload["timeout_ms"] == 0


def test_highlight_rejects_unknown_anchor_without_writing(home: Path):
    result = _highlight(target_anchor="design.nope")

    assert result["ok"] is False
    assert result["error"]["code"] == "unknown_anchor"
    assert "design.canvas" in result["error"]["details"]["available"]
    assert store.read_guidance() is None


def test_highlight_refuses_the_guidance_overlay_itself(home: Path):
    result = _highlight(target_anchor="guidance.overlay")

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_anchor"
    assert store.read_guidance() is None


def test_highlight_rejects_schema_violations(home: Path):
    empty = _highlight(target_anchor="")
    long_text = _highlight(target_anchor="design.canvas", text="x" * 281)

    assert empty["error"]["code"] == "invalid_arguments"
    assert long_text["error"]["code"] == "invalid_arguments"
    assert store.read_guidance() is None


def test_clear_guidance_removes_the_mailbox_and_is_idempotent(home: Path):
    assert _highlight(target_anchor="design.canvas")["ok"] is True
    assert store.read_guidance() is not None

    first = executor.execute("clear_guidance", {})
    second = executor.execute("clear_guidance", {})

    assert first["ok"] is True and first["data"] == {"cleared": True}
    assert second["ok"] is True and second["data"] == {"cleared": False}
    assert store.read_guidance() is None
    assert store.guidance_path().exists() is False


def test_store_clear_guidance_is_scoped_to_one_step(home: Path):
    store.write_guidance({"guidance_id": "gd-old", "target_anchor": "design.canvas"})

    assert store.clear_guidance("gd-new") is False
    assert store.read_guidance() is not None
    assert store.clear_guidance("gd-old") is True
    assert store.read_guidance() is None


# ---------------------------------------------------------------------------
# Desktop-facing endpoints
# ---------------------------------------------------------------------------


def test_guidance_endpoint_serves_the_agent_step(plugin_api: Any, home: Path):
    written = _highlight(target_anchor="canvas.export", text="导出在这里", style="highlight")

    response = asyncio.run(plugin_api.get_guidance())

    assert response["guidance"]["guidance_id"] == written["data"]["guidance_id"]
    assert response["guidance"]["target_anchor"] == "canvas.export"
    assert response["guidance"]["text"] == "导出在这里"
    # The TS GuidanceCommand reads these exact keys.
    assert set(response["guidance"]) >= {"guidance_id", "target_anchor", "text", "style", "timeout_ms"}


def test_guidance_endpoint_returns_null_when_empty(plugin_api: Any, home: Path):
    assert asyncio.run(plugin_api.get_guidance()) == {"guidance": None}


def test_dismiss_is_scoped_and_idempotent(plugin_api: Any, home: Path):
    written = _highlight(target_anchor="design.canvas")
    guidance_id = written["data"]["guidance_id"]

    stale = asyncio.run(plugin_api.dismiss_guidance("gd-someone-else"))
    assert stale == {"cleared": False}
    assert store.read_guidance() is not None

    cleared = asyncio.run(plugin_api.dismiss_guidance(guidance_id))
    assert cleared == {"cleared": True}
    assert store.read_guidance() is None

    again = asyncio.run(plugin_api.dismiss_guidance(guidance_id))
    assert again == {"cleared": False}
