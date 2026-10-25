"""Design slice tests: real backend job chain + kanban orchestration wiring (T-03).

Coverage:
- ``DESIGN.md`` is the single rule source and ``scripts/ci/check_design_rules.py``
  accepts it (the red test before the docs/checker landed).
- Real gugu-design backend chain with an isolated ``HERMES_HOME`` and
  ``_designs_root`` monkeypatched to ``tmp_path``:
  ``create_project`` → ``submit_job(kind=image)`` → ``queued``; then a gateway
  failure must surface as ``failed`` + non-empty ``error.message``.
- Kanban orchestration backend: ``get_orchestration_settings()`` reads the
  ``kanban.*`` knobs from config.
- Static wiring: ``board.tsx`` imports/renders ``OrchestrationPanel``,
  ``orchestration.tsx`` uses ``fetchOrchestration``, ``api.ts`` has GET + PUT
  ``/orchestration``.

Runs under the scratch venv (fastapi/httpx/pydantic are required); the repo
conftest is intentionally bypassed in the acceptance command.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN_PATH = REPO_ROOT / "DESIGN.md"
CHECKER_PATH = REPO_ROOT / "scripts" / "ci" / "check_design_rules.py"
DESIGN_PLUGIN_PATH = REPO_ROOT / "plugins" / "gugu-design" / "dashboard" / "plugin_api.py"
KANBAN_PLUGIN_PATH = REPO_ROOT / "plugins" / "kanban" / "dashboard" / "plugin_api.py"
BOARD_PATH = REPO_ROOT / "apps" / "desktop" / "src" / "plugins" / "kanban" / "board.tsx"
ORCHESTRATION_TSX_PATH = REPO_ROOT / "apps" / "desktop" / "src" / "plugins" / "kanban" / "orchestration.tsx"
KANBAN_API_PATH = REPO_ROOT / "apps" / "desktop" / "src" / "plugins" / "kanban" / "api.ts"


def _load_module(path: Path, name: str) -> Any:
    assert path.is_file(), f"missing module under test: {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def design_plugin(tmp_path, monkeypatch):
    """Isolated gugu-design backend: temp home, temp designs root, no worker threads."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    module = _load_module(DESIGN_PLUGIN_PATH, "gugu_design_slice_plugin_api")
    designs = tmp_path / "designs"
    designs.mkdir()
    monkeypatch.setattr(module, "_designs_root", lambda: designs)
    # Never spawn the real job thread (submit_job must stay offline in tests).
    monkeypatch.setattr(module, "_start_job", lambda pid, job: None)
    return module


@pytest.fixture()
def kanban_plugin(tmp_path, monkeypatch):
    home = tmp_path / ".hermes_kanban"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return _load_module(KANBAN_PLUGIN_PATH, "kanban_design_slice_plugin_api")


def _create_project(module: Any, name: str = "Slice") -> str:
    created = asyncio.run(module.create_project(module.CreateProjectBody(name=name)))
    project_id = created["id"]
    assert isinstance(project_id, str) and project_id
    return project_id


def _submit_image_job(module: Any, project_id: str, prompt: str = "a cat on a canvas") -> str:
    body = module.JobBody(kind="image", request={"prompt": prompt, "model": "test-image-model"})
    submitted = asyncio.run(module.submit_job(project_id, body))
    job_id = submitted["jobId"]
    assert isinstance(job_id, str) and job_id
    return job_id


def _jobs(module: Any, project_id: str) -> list[dict]:
    listed = asyncio.run(module.list_jobs(project_id))
    jobs = listed["jobs"]
    assert isinstance(jobs, list)
    return jobs


# ---------------------------------------------------------------------------
# 1. Rule single source + checker
# ---------------------------------------------------------------------------


def test_design_single_source_and_checker_pass():
    """DESIGN.md exists and the repo passes the machine checker with rc=0."""
    assert DESIGN_PATH.is_file(), "DESIGN.md must be the single rule source at the repo root"
    checker = _load_module(CHECKER_PATH, "check_design_rules_design_slice")
    errors = checker.check(REPO_ROOT)
    assert errors == [], f"design rule violations: {errors}"


# ---------------------------------------------------------------------------
# 2. Real backend chain: create -> submit -> queued -> failed visible
# ---------------------------------------------------------------------------


def test_create_project_then_submit_image_job_is_queued(design_plugin):
    project_id = _create_project(design_plugin)
    job_id = _submit_image_job(design_plugin, project_id)

    jobs = _jobs(design_plugin, project_id)
    assert len(jobs) == 1, jobs
    job = jobs[0]
    assert job["id"] == job_id
    assert job["kind"] == "image"
    assert job["status"] == "queued"
    assert job["request"]["prompt"] == "a cat on a canvas"


def test_failed_job_exposes_error_message(design_plugin, monkeypatch):
    project_id = _create_project(design_plugin, "Failure visible")
    job_id = _submit_image_job(design_plugin, project_id)

    def _gateway_down() -> tuple[str, str]:
        raise HTTPException(status_code=400, detail="尚未登录咕咕聚合网关")

    monkeypatch.setattr(design_plugin, "_gateway", _gateway_down)
    design_plugin._run_image_job(project_id, job_id, {"prompt": "a cat", "model": "test-image-model"})

    job = next(j for j in _jobs(design_plugin, project_id) if j["id"] == job_id)
    assert job["status"] == "failed"
    assert job.get("error"), "failed job must carry an error object"
    message = str(job["error"].get("message") or "").strip()
    assert message, "failed job must expose a non-empty error.message"
    assert "登录" in message
    assert job["error"].get("retryable") is True


def test_submit_job_rejects_unknown_kind(design_plugin):
    project_id = _create_project(design_plugin)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            design_plugin.submit_job(
                project_id,
                design_plugin.JobBody(kind="audio", request={"prompt": "nope"}),
            )
        )
    assert excinfo.value.status_code == 400


def test_export_endpoint_returns_a_self_contained_zip(design_plugin, monkeypatch):
    """The desktop export button goes through this route; it must return real zip bytes."""
    from plugins.gugu_capabilities import images, store as cap_store

    monkeypatch.setattr(cap_store, "designs_root", lambda: design_plugin._designs_root())
    project_id = _create_project(design_plugin, "Export me")
    asset = cap_store.register_asset(
        project_id,
        images.mock_png("warm hero", "256x256"),
        "image/png",
        "MOCK-hero.png",
        {"mock": True, "model": "mock"},
    )
    placed = cap_store.place_on_canvas(
        project_id,
        {"asset_id": asset["id"], "key": "hero", "label": "Hero"},
        {"x": 10, "y": 20},
    )
    assert placed["created"] is True

    result = asyncio.run(design_plugin.export_canvas(project_id))
    payload = base64.b64decode(result["data"])
    assert payload.startswith(b"PK")
    assert result["filename"].endswith(".zip")
    assert result["bytes"] == len(payload)
    assert result["mock_assets"] == 1


# ---------------------------------------------------------------------------
# 3. Kanban orchestration backend
# ---------------------------------------------------------------------------


def test_kanban_orchestration_settings_reads_config(kanban_plugin, monkeypatch):
    monkeypatch.setattr(
        kanban_plugin,
        "_load_config_or_empty",
        lambda: {"kanban": {"auto_decompose": False, "auto_promote_children": True}},
    )
    settings = kanban_plugin.get_orchestration_settings()

    assert settings["auto_decompose"] is False
    assert settings["auto_promote_children"] is True
    for key in (
        "orchestrator_profile",
        "default_assignee",
        "resolved_orchestrator_profile",
        "resolved_default_assignee",
        "active_profile",
    ):
        assert key in settings, f"orchestration settings missing {key}: {settings}"


# ---------------------------------------------------------------------------
# 4. Frontend wiring (static)
# ---------------------------------------------------------------------------


def test_board_imports_and_renders_orchestration_panel():
    text = BOARD_PATH.read_text(encoding="utf-8")
    assert "import { OrchestrationPanel } from './orchestration'" in text
    assert "<OrchestrationPanel" in text


def test_orchestration_panel_and_api_use_get_put_orchestration():
    panel = ORCHESTRATION_TSX_PATH.read_text(encoding="utf-8")
    assert "fetchOrchestration" in panel
    assert "saveOrchestration" in panel

    api = KANBAN_API_PATH.read_text(encoding="utf-8")
    assert "fetchOrchestration" in api
    assert "'/orchestration'" in api
    assert "saveOrchestration" in api
    assert "method: 'PUT'" in api
    # GET wrapper and PUT wrapper both target /orchestration.
    assert api.index("fetchOrchestration") < api.index("'/orchestration'")
    put_block = api[api.index("saveOrchestration") :]
    assert "'/orchestration'" in put_block
    assert "method: 'PUT'" in put_block
