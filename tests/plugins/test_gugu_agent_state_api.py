"""Agent-state endpoint + capability read_state integration (M0).

The renderer publishes surface/project/selection through the gugu-design plugin
API; the agent process reads the same JSON file in ``read_state``. This test
exercises both ends against one temp ``HERMES_HOME`` instead of mocking either.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

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
def plugin_api(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    module = _load_module(PLUGIN_API, "gugu_agent_state_plugin_api")
    return {"module": module, "home": home}


def _write_project(home: Path, project_id: str = "proj-1") -> None:
    directory = home / "designs" / project_id
    (directory / "assets").mkdir(parents=True, exist_ok=True)
    (directory / "project.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "revision": 7,
                "id": project_id,
                "name": "State Demo",
                "createdAt": 1.0,
                "updatedAt": 1.0,
                "viewport": {"x": 0, "y": 0, "scale": 1},
                "assets": {},
                "comments": {},
                "elements": {},
                "versions": {},
                "jobs": {},
                "recent_operation_ids": [],
                "operation_results": {},
            }
        ),
        encoding="utf-8",
    )


def test_agent_state_put_get_roundtrip(plugin_api):
    module = plugin_api["module"]
    written = asyncio.run(
        module.put_agent_state(
            module.AgentStateBody(
                surface="design",
                route="/design",
                project_id="proj-1",
                selection=["agent-one", "agent-two"],
            )
        )
    )
    assert written["ok"] is True
    assert written["state"]["surface"] == "design"

    read_back = asyncio.run(module.get_agent_state())
    assert read_back["state"]["surface"] == "design"
    assert read_back["state"]["route"] == "/design"
    assert read_back["state"]["project_id"] == "proj-1"
    assert read_back["state"]["selection"] == ["agent-one", "agent-two"]
    assert read_back["state"]["updated_at"] > 0


def test_agent_state_missing_file_is_null(plugin_api):
    assert asyncio.run(plugin_api["module"].get_agent_state()) == {"state": None}


def test_agent_state_file_is_not_listed_as_a_project(plugin_api):
    module = plugin_api["module"]
    _write_project(plugin_api["home"])
    asyncio.run(module.put_agent_state(module.AgentStateBody(surface="design", project_id="proj-1")))
    listing = asyncio.run(module.list_projects())
    assert [item["id"] for item in listing["projects"]] == ["proj-1"]


def test_capability_read_state_reads_what_the_api_published(plugin_api):
    from plugins.gugu_capabilities import executor

    module = plugin_api["module"]
    _write_project(plugin_api["home"])
    asyncio.run(
        module.put_agent_state(
            module.AgentStateBody(surface="design", route="/design", project_id="proj-1", selection=[])
        )
    )

    result = executor.execute("read_state", {})
    assert result["ok"] is True
    assert result["data"]["surface"]["surface"] == "design"
    assert result["data"]["project"]["id"] == "proj-1"
    assert result["data"]["canvas"]["revision"] == 7
