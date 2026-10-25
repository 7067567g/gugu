"""M0 capability contract tests — real file I/O, no mocked executor.

Coverage:
- registry meta-schema (11 ids, all seven required fields, examples conform);
- ``list_capabilities`` and ``read_state`` executed end-to-end against a temp
  ``HERMES_HOME`` + a real ``designs/<pid>/project.json``;
- ``place_on_canvas`` idempotency (natural element id + operation_id replay),
  ``update_artifact`` in-place replacement (no duplicate node), ``undo``;
- approval / planned capabilities never fake success;
- the plugin registers exactly one agent tool and its handler runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from plugins.gugu_capabilities import executor, registry, schema, store

ASSET_A = "0123456789abcdef"
ASSET_B = "fedcba9876543210"


def _document(project_id: str, *, revision: int = 3) -> Dict[str, Any]:
    return {
        "schemaVersion": 1,
        "revision": revision,
        "id": project_id,
        "name": "M0 Demo",
        "createdAt": 1.0,
        "updatedAt": 1.0,
        "viewport": {"x": 0, "y": 0, "scale": 1},
        "assets": {
            ASSET_A: {
                "id": ASSET_A,
                "name": "one.png",
                "mediaType": "image/png",
                "bytes": 10,
                "width": 400,
                "height": 300,
                "createdAt": 1.0,
                "kind": "image",
                "versionId": "version-a",
            },
            ASSET_B: {
                "id": ASSET_B,
                "name": "two.png",
                "mediaType": "image/png",
                "bytes": 10,
                "width": 500,
                "height": 250,
                "createdAt": 1.0,
                "kind": "image",
                "versionId": "version-b",
            },
        },
        "comments": {},
        "elements": {},
        "versions": {
            "version-a": {"id": "version-a", "assetId": ASSET_A, "createdAt": 1.0, "label": "one"},
            "version-b": {"id": "version-b", "assetId": ASSET_B, "createdAt": 1.0, "label": "two"},
        },
        "jobs": {},
        "recent_operation_ids": [],
        "operation_results": {},
    }


@pytest.fixture()
def project(tmp_path, monkeypatch) -> Dict[str, Any]:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    project_id = "proj-1"
    directory = home / "designs" / project_id
    (directory / "assets").mkdir(parents=True)
    document = _document(project_id)
    (directory / "project.json").write_text(json.dumps(document), encoding="utf-8")
    return {"home": home, "pid": project_id, "dir": directory, "doc": document}


def _write_doc(project: Dict[str, Any], document: Dict[str, Any]) -> None:
    (project["dir"] / "project.json").write_text(json.dumps(document), encoding="utf-8")


def _read_doc(project: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads((project["dir"] / "project.json").read_text(encoding="utf-8"))


def _place(project: Dict[str, Any], **overrides: Any) -> Dict[str, Any]:
    arguments: Dict[str, Any] = {
        "project_id": project["pid"],
        "artifact": {"asset_id": ASSET_A, "key": "diagram-main"},
        "position": {"x": 100, "y": 80},
    }
    arguments.update(overrides)
    return executor.execute("place_on_canvas", arguments)


# ---------------------------------------------------------------------------
# Registry contract
# ---------------------------------------------------------------------------


def test_registry_contract_is_complete_and_valid():
    assert len(registry.CAPABILITIES) == 11
    assert registry.validate_registry() == []

    for capability in registry.list_capabilities():
        for field in (
            "id",
            "semantics",
            "input_schema",
            "output_schema",
            "side_effects",
            "requires_approval",
            "ui_anchors",
            "examples",
        ):
            assert field in capability, (capability["id"], field)
        assert capability["input_schema"]["type"] == "object"
        assert capability["output_schema"]["type"] == "object"
        assert isinstance(capability["requires_approval"], bool)

    assert {item["id"] for item in registry.list_capabilities()} == set(registry.CAPABILITY_IDS)


def test_registry_examples_match_their_input_schema():
    for capability in registry.list_capabilities():
        for example in capability["examples"]:
            errors = schema.validate(example["arguments"], capability["input_schema"])
            assert errors == [], f"{capability['id']}: {errors}"


def test_ready_capabilities_only_target_present_anchors():
    anchors = registry.anchor_registry()
    for capability in registry.list_capabilities():
        for anchor in capability["ui_anchors"]:
            assert anchor in anchors, (capability["id"], anchor)
            if capability["status"] == "ready":
                assert anchors[anchor]["status"] == "present", (capability["id"], anchor)


# ---------------------------------------------------------------------------
# list_capabilities / read_state
# ---------------------------------------------------------------------------


def test_list_capabilities_executes(project):
    result = executor.execute("list_capabilities", {})
    assert result["ok"] is True
    assert result["data"]["schema_version"] == registry.SCHEMA_VERSION
    assert len(result["data"]["capabilities"]) == 11


def test_read_state_reads_surface_project_canvas_and_selection(project):
    placed = _place(project)
    assert placed["ok"] is True
    element_id = placed["data"]["element"]["id"]

    (project["home"] / "designs" / "agent-state.json").write_text(
        json.dumps(
            {
                "surface": "design",
                "route": "/design",
                "project_id": project["pid"],
                "selection": [element_id],
                "updated_at": 123.0,
            }
        ),
        encoding="utf-8",
    )

    result = executor.execute("read_state", {"scope": ["surface", "project", "canvas", "selection"]})
    assert result["ok"] is True
    data = result["data"]
    assert data["surface"]["surface"] == "design"
    assert data["project"]["id"] == project["pid"]
    assert data["canvas"]["element_count"] == 1
    assert data["canvas"]["elements"][0]["id"] == element_id
    assert data["selection"]["ids"] == [element_id]
    assert data["selection"]["elements"][0]["id"] == element_id


def test_read_state_without_runtime_state_is_graceful(project):
    result = executor.execute("read_state", {})
    assert result["ok"] is True
    assert result["data"]["surface"] is None
    assert result["data"]["project"] is None
    assert result["data"]["canvas"] is None
    assert result["data"]["selection"] == {"ids": [], "elements": []}


def test_read_state_stale_runtime_project_is_graceful(project):
    (project["home"] / "designs" / "agent-state.json").write_text(
        json.dumps({"surface": "design", "route": "/design", "project_id": "missing-project", "selection": []}),
        encoding="utf-8",
    )
    result = executor.execute("read_state", {})
    assert result["ok"] is True
    assert result["data"]["project"] is None
    assert result["data"]["canvas"] is None


# ---------------------------------------------------------------------------
# place_on_canvas
# ---------------------------------------------------------------------------


def test_place_on_canvas_creates_one_element_and_is_idempotent(project):
    first = _place(project)
    assert first["ok"] is True
    assert first["data"]["created"] is True
    assert first["data"]["idempotent"] is False
    element_id = first["data"]["element"]["id"]
    assert element_id.startswith("agent-")
    revision_after_first = first["data"]["revision"]

    second = _place(project)
    assert second["ok"] is True
    assert second["data"]["created"] is False
    assert second["data"]["idempotent"] is True
    assert second["data"]["revision"] == revision_after_first
    assert second["data"]["element"]["id"] == element_id

    document = _read_doc(project)
    assert len(document["elements"]) == 1
    assert document["elements"][element_id]["assetId"] == ASSET_A
    assert document["elements"][element_id]["x"] == 100
    assert document["elements"][element_id]["y"] == 80


def test_place_operation_id_replay_does_not_bump_revision(project):
    first = _place(project, operation_id="op-place-1")
    revision = first["data"]["revision"]
    second = _place(project, operation_id="op-place-1")
    assert second["ok"] is True
    assert second["data"]["idempotent"] is True
    assert second["data"]["revision"] == revision
    assert len(_read_doc(project)["elements"]) == 1


def test_place_unknown_asset_is_rejected(project):
    result = executor.execute(
        "place_on_canvas",
        {
            "project_id": project["pid"],
            "artifact": {"asset_id": "ffffffffffffffff"},
        },
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "unknown_asset"
    assert _read_doc(project)["elements"] == {}


def test_place_same_element_id_with_other_asset_conflicts(project):
    first = _place(project, element_id="agent-fixed")
    assert first["ok"] is True
    conflict = executor.execute(
        "place_on_canvas",
        {
            "project_id": project["pid"],
            "artifact": {"asset_id": ASSET_B},
            "element_id": "agent-fixed",
        },
    )
    assert conflict["ok"] is False
    assert conflict["error"]["code"] == "element_conflict"


# ---------------------------------------------------------------------------
# update_artifact
# ---------------------------------------------------------------------------


def test_update_artifact_replaces_in_place_without_duplicating(project):
    placed = _place(project)
    element_id = placed["data"]["element"]["id"]
    before_revision = placed["data"]["revision"]

    updated = executor.execute(
        "update_artifact",
        {"project_id": project["pid"], "element_id": element_id, "asset_id": ASSET_B},
    )
    assert updated["ok"] is True
    assert updated["data"]["changed"] is True
    assert updated["data"]["element"]["id"] == element_id
    assert updated["data"]["element"]["assetId"] == ASSET_B
    assert updated["data"]["element"]["x"] == 100
    assert updated["data"]["revision"] == before_revision + 1

    document = _read_doc(project)
    assert len(document["elements"]) == 1
    assert document["elements"][element_id]["assetId"] == ASSET_B
    assert document["elements"][element_id]["versionId"] == "version-b"

    replay = executor.execute(
        "update_artifact",
        {"project_id": project["pid"], "element_id": element_id, "asset_id": ASSET_B},
    )
    assert replay["ok"] is True
    assert replay["data"]["changed"] is False
    assert replay["data"]["idempotent"] is True
    assert replay["data"]["revision"] == updated["data"]["revision"]


def test_update_artifact_patches_geometry_in_place(project):
    placed = _place(project)
    element_id = placed["data"]["element"]["id"]
    updated = executor.execute(
        "update_artifact",
        {
            "project_id": project["pid"],
            "element_id": element_id,
            "patch": {"x": 10, "y": 20, "width": 640},
        },
    )
    assert updated["ok"] is True
    assert updated["data"]["element"]["x"] == 10
    assert updated["data"]["element"]["width"] == 640
    assert len(_read_doc(project)["elements"]) == 1


def test_update_artifact_instruction_needs_a_working_generation_rung(project, monkeypatch):
    placed = _place(project)
    element_id = placed["data"]["element"]["id"]
    for name in ("GUGU_IMAGE_BASE_URL", "GUGU_IMAGE_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_KEY", "GUGU_ALLOW_MOCK_IMAGES"):
        monkeypatch.delenv(name, raising=False)
    result = executor.execute(
        "update_artifact",
        {"project_id": project["pid"], "element_id": element_id, "instruction": "warmer"},
    )
    # M1 wires instruction to the generation ladder; with no gateway and no mock
    # consent it must fail honestly instead of returning the old asset.
    assert result["ok"] is False
    assert result["error"]["code"] == "generation_failed"
    assert len(result["error"]["details"]["attempts"]) == 2


# ---------------------------------------------------------------------------
# undo
# ---------------------------------------------------------------------------


def test_undo_restores_canvas_across_multiple_actions(project):
    _place(project, artifact={"asset_id": ASSET_A, "key": "one"}, element_id="agent-one")
    _place(project, artifact={"asset_id": ASSET_B, "key": "two"}, element_id="agent-two")
    assert len(_read_doc(project)["elements"]) == 2

    first_undo = executor.execute("undo", {"project_id": project["pid"]})
    assert first_undo["ok"] is True
    assert first_undo["data"]["undone"] is True
    assert len(_read_doc(project)["elements"]) == 1

    second_undo = executor.execute("undo", {"project_id": project["pid"]})
    assert second_undo["ok"] is True
    assert second_undo["data"]["undone"] is True
    assert len(_read_doc(project)["elements"]) == 0

    empty_undo = executor.execute("undo", {"project_id": project["pid"]})
    assert empty_undo["ok"] is True
    assert empty_undo["data"]["undone"] is False
    assert empty_undo["data"]["restored_revision"] is None


def test_undo_preserves_jobs_owned_by_the_host(project):
    _place(project)
    document = _read_doc(project)
    document["jobs"] = {"job-1": {"id": "job-1", "status": "running"}}
    _write_doc(project, document)

    executor.execute("undo", {"project_id": project["pid"]})
    restored = _read_doc(project)
    assert restored["elements"] == {}
    assert restored["jobs"]["job-1"]["status"] == "running"


# ---------------------------------------------------------------------------
# Approval / planned / error envelope
# ---------------------------------------------------------------------------


def test_generate_capability_requires_approval_before_any_work(project, monkeypatch):
    result = executor.execute("generate_image", {"prompt": "a warm diagram"})
    assert result["ok"] is False
    assert result["error"]["code"] == "approval_required"

    # Approved but with no gateway and no mock consent: it must fail honestly,
    # never return a plausible-looking fake image.
    for name in ("GUGU_IMAGE_BASE_URL", "GUGU_IMAGE_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_KEY", "GUGU_ALLOW_MOCK_IMAGES"):
        monkeypatch.delenv(name, raising=False)
    failed = executor.execute(
        "generate_image",
        {"prompt": "a warm diagram", "project_id": project["pid"]},
        approved=True,
    )
    assert failed["ok"] is False
    assert failed["error"]["code"] == "generation_failed"


def test_planned_capabilities_never_pretend_to_work(project):
    # M2 moved highlight/clear_guidance to ready; navigate is the only surface
    # still deliberately deferred. Assert the exact planned set so a regression
    # (or a capability silently flipping to ready without a handler) is visible.
    planned = [capability["id"] for capability in registry.list_capabilities() if capability["status"] == "planned"]
    assert planned == ["navigate"]

    result = executor.execute("navigate", {"screen": "design"}, approved=True)
    assert result["ok"] is False
    assert result["error"]["code"] == "not_implemented"
    assert result["error"]["details"]["milestone"] == "M0"


def test_invalid_arguments_and_unknown_capability_are_structured(project):
    invalid = executor.execute("place_on_canvas", {"project_id": project["pid"]})
    assert invalid["ok"] is False
    assert invalid["error"]["code"] == "invalid_arguments"
    assert any("artifact" in item for item in invalid["error"]["details"]["errors"])

    unknown = executor.execute("does_not_exist", {})
    assert unknown["ok"] is False
    assert unknown["error"]["code"] == "unknown_capability"
    assert "read_state" in unknown["error"]["details"]["available"]


def test_missing_project_is_a_structured_error(project):
    result = executor.execute(
        "place_on_canvas",
        {"project_id": "nope", "artifact": {"asset_id": ASSET_A}},
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "project_not_found"


# ---------------------------------------------------------------------------
# Plugin tool registration (existing tool mechanism = agent callable)
# ---------------------------------------------------------------------------


def test_plugin_registers_one_tool_and_handler_runs(project):
    import plugins.gugu_capabilities as plugin

    class _Ctx:
        def __init__(self) -> None:
            self.tools: dict[str, dict[str, Any]] = {}

        def register_tool(self, **kwargs: Any) -> None:
            self.tools[kwargs["name"]] = kwargs

    ctx = _Ctx()
    plugin.register(ctx)
    assert list(ctx.tools) == ["gugu_capability"]
    tool = ctx.tools["gugu_capability"]
    assert tool["toolset"] == "gugu-capabilities"
    assert tool["schema"]["parameters"]["properties"]["capability"]["enum"] == list(registry.CAPABILITY_IDS)

    payload = json.loads(tool["handler"]({"capability": "list_capabilities", "arguments": {}}))
    assert payload["ok"] is True
    assert len(payload["data"]["capabilities"]) == 11


def test_real_plugin_loader_loads_the_plugin(project):
    """Exercise the same loader the bundled discovery sweep uses (not a plain import)."""
    import logging
    import sys

    from plugins.plugin_loader import load_plugin_module

    module_name = "plugins.gugu_capabilities_probe"
    plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / "gugu_capabilities"
    try:
        module = load_plugin_module(
            module_name,
            plugin_dir,
            parents=(),
            logger=logging.getLogger("gugu-capabilities-probe"),
        )
        assert module is not None

        class _Ctx:
            def __init__(self) -> None:
                self.tools: dict[str, dict[str, Any]] = {}

            def register_tool(self, **kwargs: Any) -> None:
                self.tools[kwargs["name"]] = kwargs

        ctx = _Ctx()
        module.register(ctx)
        assert list(ctx.tools) == ["gugu_capability"]
    finally:
        for name in [item for item in sys.modules if item == module_name or item.startswith(f"{module_name}.")]:
            sys.modules.pop(name, None)
        parent = sys.modules.get("plugins")
        if parent is not None and hasattr(parent, "gugu_capabilities_probe"):
            delattr(parent, "gugu_capabilities_probe")


def test_store_activity_is_appended(project):
    _place(project)
    activity = (Path(project["dir"]) / "agent-activity.ndjson").read_text(encoding="utf-8").strip().splitlines()
    assert len(activity) == 1
    entry = json.loads(activity[0])
    assert entry["capability"] == "place_on_canvas"
    assert "agent-" in entry["detail"]["element_id"]
