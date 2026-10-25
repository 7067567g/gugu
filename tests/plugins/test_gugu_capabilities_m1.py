"""M1 capability tests — real files, real zip, no fake generation.

Coverage:
- repo/readme/outline -> Mermaid -> SVG asset, and both fallback rungs;
- SVG is accepted by the capability validator AND the dashboard storage twin;
- model ladder from env (no vendor lock), explicit mock metadata;
- one-sentence ``update_artifact`` regenerates in place (same element id, no copy);
- export bundle contents + checksums;
- the whole Hero flow B with measured acceptance metrics.
"""

from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path
from typing import Any, Dict

import pytest

from plugins.gugu_capabilities import assets, diagram, executor, export as cap_export, hero_flow, images, registry, store

GATEWAY_ENV = (
    "GUGU_IMAGE_BASE_URL",
    "GUGU_IMAGE_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "GUGU_ALLOW_MOCK_IMAGES",
    "GUGU_IMAGE_MODELS",
    "GUGU_IMAGE_MODEL_PRIMARY",
    "GUGU_IMAGE_MODEL_FALLBACK",
    "GUGU_IMAGE_MODE",
    "GUGU_ENV",
    "HERMES_ENV",
)


@pytest.fixture()
def project(tmp_path, monkeypatch) -> Dict[str, Any]:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    for name in GATEWAY_ENV:
        monkeypatch.delenv(name, raising=False)
    project_id = "proj-m1"
    store.create_project(project_id, "M1 Demo")
    return {"home": home, "pid": project_id, "dir": store.project_dir(project_id)}


@pytest.fixture()
def repo(tmp_path) -> Path:
    root = tmp_path / "demo-repo"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "README.md").write_text(
        "# Demo Architecture\n\n## Runtime\n\n## Storage\n\n## Renderer\n",
        encoding="utf-8",
    )
    (root / "src" / "main.py").write_text("print('demo')\n", encoding="utf-8")
    (root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    return root


def _doc(project: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads((project["dir"] / "project.json").read_text(encoding="utf-8"))


def _place(
    project: Dict[str, Any],
    artifact: Dict[str, Any],
    *,
    label: str = "Architecture",
    key: str = "k",
    element_id: str = "",
    x: float = 40,
    y: float = 60,
) -> Dict[str, Any]:
    args: Dict[str, Any] = {
        "project_id": project["pid"],
        "artifact": {"asset_id": artifact.get("asset_id") or artifact.get("id"), "key": key, "label": label},
        "position": {"x": x, "y": y},
    }
    if element_id:
        args["element_id"] = element_id
    return executor.execute("place_on_canvas", args)


def _fail_real(_prompt: str, _model: str, _size: str, timeout: float = 180.0):
    raise images.ImageError("network down")


# ---------------------------------------------------------------------------
# Diagram + SVG asset type
# ---------------------------------------------------------------------------


def test_repo_diagram_builds_mermaid_and_registers_svg_asset(project, repo):
    result = executor.execute(
        "generate_diagram",
        {"source": str(repo), "source_kind": "repo", "project_id": project["pid"]},
        approved=True,
    )
    assert result["ok"] is True, result
    data = result["data"]
    assert data["format"] == "svg"
    assert data["fallback"] is None
    assert data["mermaid"].startswith("flowchart LR")
    assert "src/" in data["mermaid"]
    assert data["svg"].startswith("<svg")
    assert "Mermaid source" in data["svg"]
    asset_id = data["artifact"]["asset_id"]
    assert asset_id

    doc = _doc(project)
    asset = doc["assets"][asset_id]
    assert asset["mediaType"] == "image/svg+xml"
    assert asset["kind"] == "image"
    assert asset["width"] > 0 and asset["height"] > 0
    assert asset["diagram"]["mermaid"].startswith("flowchart")
    path = project["dir"] / "assets" / assets.asset_filename("image/svg+xml", asset_id)
    assert path.is_file()
    assert path.read_bytes().startswith(b"<svg")


def test_placed_svg_element_keeps_label_and_is_readable(project, repo):
    data = executor.execute(
        "generate_diagram",
        {"source": str(repo), "source_kind": "repo", "project_id": project["pid"]},
        approved=True,
    )["data"]
    placed = _place(project, data["artifact"])
    assert placed["ok"] is True, placed
    element_id = placed["data"]["element"]["id"]
    assert placed["data"]["element"]["label"] == "Architecture"
    assert placed["data"]["element"]["type"] == "image"

    state = executor.execute("read_state", {"scope": ["canvas"], "project_id": project["pid"]})
    assert state["ok"] is True
    element = state["data"]["canvas"]["elements"][0]
    assert element["id"] == element_id
    assert element["label"] == "Architecture"


def test_readme_and_outline_sources_render(project):
    readme = executor.execute(
        "generate_diagram",
        {"source": "# My System\n\n## API\n\n## Storage", "source_kind": "readme", "project_id": project["pid"]},
        approved=True,
    )
    assert readme["ok"] is True, readme
    assert "My System" in readme["data"]["mermaid"]
    assert "API" in readme["data"]["svg"]

    outline = executor.execute(
        "generate_diagram",
        {"source": "Input -> Parse\nParse -> Render", "source_kind": "outline", "project_id": project["pid"]},
        approved=True,
    )
    assert outline["ok"] is True, outline
    assert outline["data"]["mermaid"].count("-->") == 2


def test_diagram_falls_back_to_outline_svg(project, repo, monkeypatch):
    def boom(*_args: Any, **_kwargs: Any) -> str:
        raise diagram.DiagramError("renderer unavailable")

    monkeypatch.setattr(diagram, "render_mermaid_svg", boom)
    built = diagram.build_diagram(str(repo), "repo")
    assert built["fallback"] == "outline-svg"
    assert "Structured outline fallback" in built["svg"]
    assert built["mermaid"] == ""


def test_diagram_falls_back_to_text_artifact_not_a_hard_failure(project, repo, monkeypatch):
    def boom(*_args: Any, **_kwargs: Any) -> str:
        raise diagram.DiagramError("no renderer")

    monkeypatch.setattr(diagram, "build_diagram", boom)
    monkeypatch.setattr(diagram, "source_to_outline", boom)
    result = executor.execute(
        "generate_diagram",
        {"source": str(repo), "source_kind": "repo", "project_id": project["pid"]},
        approved=True,
    )
    assert result["ok"] is True, result
    assert result["data"]["format"] == "text/markdown"
    assert result["data"]["fallback"] == "text"
    path = Path(result["data"]["artifact"]["path"])
    assert path.is_file()
    assert "结构化文本大纲" in path.read_text(encoding="utf-8")


def test_generate_diagram_requires_approval(project, repo):
    result = executor.execute("generate_diagram", {"source": str(repo), "source_kind": "repo"})
    assert result["ok"] is False
    assert result["error"]["code"] == "approval_required"


def test_svg_is_accepted_by_both_asset_validators():
    spec = importlib.util.spec_from_file_location(
        "gugu_storage_probe", Path(__file__).resolve().parents[2] / "plugins" / "gugu-design" / "dashboard" / "gugu_storage.py"
    )
    assert spec and spec.loader
    gs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gs)

    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="1" height="1"/></svg>'
    assert assets.sniff_media(svg) == "image/svg+xml"
    assert gs.sniff_media(svg) == "image/svg+xml"
    assert assets.validate_asset_bytes(svg, "image/svg+xml") == "image/svg+xml"
    assert gs.validate_asset_bytes(svg, "image/svg+xml") == "image/svg+xml"
    assert assets.image_dimensions(svg, "image/svg+xml") == (640, 480)
    assert gs.image_dimensions(svg, "image/svg+xml") == (640, 480)
    assert assets.asset_filename("image/svg+xml", "abc") == gs.asset_filename("image/svg+xml", "abc") == "abc.svg"
    for media in ("image/png", "image/jpeg", "image/webp", "video/mp4"):
        assert assets.asset_filename(media, "abc") == gs.asset_filename(media, "abc")


# ---------------------------------------------------------------------------
# Image ladder + explicit mock
# ---------------------------------------------------------------------------


def test_model_candidates_come_from_env_and_dedupe(monkeypatch):
    monkeypatch.setenv("GUGU_IMAGE_MODEL_PRIMARY", "primary-x")
    monkeypatch.setenv("GUGU_IMAGE_MODEL_FALLBACK", "fallback-y")
    monkeypatch.setenv("GUGU_IMAGE_MODELS", "extra-z,primary-x")
    assert images.model_candidates("requested-w") == ["requested-w", "primary-x", "extra-z", "fallback-y"]
    assert len(images.model_candidates("")) == 3


def test_image_primary_and_fallback_fail_then_explicit_mock(project, monkeypatch):
    tried: list[str] = []

    def fail(prompt: str, model: str, size: str, timeout: float = 180.0):
        tried.append(model)
        raise images.ImageError("no gateway")

    monkeypatch.setattr(images, "_real_generate", fail)
    monkeypatch.setenv("GUGU_ALLOW_MOCK_IMAGES", "1")
    result = executor.execute(
        "generate_image",
        {"prompt": "warm hero", "project_id": project["pid"], "label": "Hero 1", "size": "1024x1024"},
        approved=True,
    )
    assert result["ok"] is True, result
    data = result["data"]
    assert data["mock"] is True
    assert data["model"] == "mock"
    assert tried == ["qwen-image-2.0", "gpt-image-2-medium"]
    assert [item["ok"] for item in data["attempts"]] == [False, False, True]
    assert data["artifact"]["name"].startswith("MOCK-")

    doc = _doc(project)
    asset = doc["assets"][data["artifact"]["asset_id"]]
    assert asset["mock"] is True
    assert asset["model"] == "mock"
    assert asset["prompt"] == "warm hero"
    path = project["dir"] / "assets" / assets.asset_filename("image/png", asset["id"])
    assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_image_without_mock_consent_fails_honestly(project, monkeypatch):
    monkeypatch.setattr(images, "_real_generate", _fail_real)
    result = executor.execute(
        "generate_image",
        {"prompt": "warm hero", "project_id": project["pid"]},
        approved=True,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "generation_failed"
    assert len(result["error"]["details"]["attempts"]) == 2
    assert _doc(project)["assets"] == {}


def test_production_refuses_mock_even_with_flag(monkeypatch):
    monkeypatch.setenv("GUGU_ALLOW_MOCK_IMAGES", "1")
    monkeypatch.setenv("GUGU_ENV", "production")
    assert images.mock_allowed() is False


def test_real_generation_parses_b64_and_uses_bearer_header(monkeypatch):
    """The live path is exercised with a fake HTTP client (no network, no real key)."""
    import base64

    import httpx

    png = images.mock_png("probe", "256x256")
    seen: Dict[str, Any] = {}

    class _Response:
        status_code = 200

        def json(self) -> Dict[str, Any]:
            return {"data": [{"b64_json": base64.b64encode(png).decode("ascii")}]}

    class _Client:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> "_Client":
            return self

        def __exit__(self, *_exc: Any) -> None:
            return None

        def post(self, url: str, headers: Dict[str, str], json: Dict[str, Any]) -> _Response:
            seen.update({"url": url, "headers": headers, "body": json})
            return _Response()

    monkeypatch.setenv("GUGU_IMAGE_BASE_URL", "https://gateway.invalid")
    monkeypatch.setenv("GUGU_IMAGE_API_KEY", "test-key-not-real")
    monkeypatch.setattr(httpx, "Client", _Client)

    data, media = images._real_generate("warm hero", "qwen-image-2.0", "1024x1024")
    assert data == png
    assert media == "image/png"
    assert seen["url"] == "https://gateway.invalid/images/generations"
    assert seen["headers"]["Authorization"] == "Bearer test-key-not-real"
    assert seen["body"]["model"] == "qwen-image-2.0"
    assert "test-key-not-real" not in json.dumps(seen["body"])


# ---------------------------------------------------------------------------
# In-place correction
# ---------------------------------------------------------------------------


def test_instruction_regenerates_same_element_without_duplicate(project, monkeypatch):
    monkeypatch.setattr(images, "_real_generate", _fail_real)
    monkeypatch.setenv("GUGU_ALLOW_MOCK_IMAGES", "1")
    generated = executor.execute(
        "generate_image",
        {"prompt": "cool minimal hero", "project_id": project["pid"], "label": "Hero 2"},
        approved=True,
    )["data"]
    placed = _place(
        project,
        generated["artifact"],
        element_id="hero-2",
        label="Hero 2",
        key="hero-2",
    )
    element_id = placed["data"]["element"]["id"]
    before = _doc(project)
    old_asset = before["elements"][element_id]["assetId"]

    corrected = executor.execute(
        "update_artifact",
        {"project_id": project["pid"], "element_id": element_id, "instruction": "第二张太冷，暖一点。"},
        approved=True,
    )
    assert corrected["ok"] is True, corrected
    data = corrected["data"]
    assert data["regenerated"] is True
    assert data["model"] == "mock"
    assert data["element"]["id"] == element_id
    assert data["element"]["assetId"] != old_asset
    assert data["element"]["label"] == "Hero 2"
    assert data["element"]["x"] == 40 and data["element"]["y"] == 60

    after = _doc(project)
    assert len(after["elements"]) == len(before["elements"]) == 1
    new_asset = after["assets"][data["element"]["assetId"]]
    assert new_asset["source"] == "image-correction"
    assert new_asset["correction"] == "第二张太冷，暖一点。"
    assert "Revision instruction" in new_asset["prompt"]
    # The old asset stays in history (version trail), but no duplicate element.
    assert old_asset in after["assets"]


# ---------------------------------------------------------------------------
# Export + Hero flow
# ---------------------------------------------------------------------------


def test_export_bundle_is_self_contained(project, repo, monkeypatch):
    monkeypatch.setattr(images, "_real_generate", _fail_real)
    monkeypatch.setenv("GUGU_ALLOW_MOCK_IMAGES", "1")
    diagram_data = executor.execute(
        "generate_diagram",
        {"source": str(repo), "source_kind": "repo", "project_id": project["pid"]},
        approved=True,
    )["data"]
    _place(project, diagram_data["artifact"], element_id="arch")
    image_data = executor.execute(
        "generate_image",
        {"prompt": "warm hero", "project_id": project["pid"], "label": "Hero"},
        approved=True,
    )["data"]
    _place(
        project,
        image_data["artifact"],
        element_id="hero",
        label="Hero",
        key="hero",
    )
    revision_before = _doc(project)["revision"]

    result = executor.execute("export_canvas", {"project_id": project["pid"]})
    assert result["ok"] is True, result
    data = result["data"]
    path = Path(data["path"])
    assert path.is_file()
    assert data["revision"] == revision_before
    assert _doc(project)["revision"] == revision_before

    payload = path.read_bytes()
    assert data["sha256"] == __import__("hashlib").sha256(payload).hexdigest()
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        assert {"project.json", "preview.svg", "manifest.json"} <= names
        assert any(name.startswith("assets/") and name.endswith(".svg") for name in names)
        assert any(name.startswith("assets/") and name.endswith(".png") for name in names)
        preview = archive.read("preview.svg").decode("utf-8")
        assert "M1 Demo" in preview
        assert "Architecture" in preview and "Hero" in preview
        assert "MOCK" in preview
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == cap_export.EXPORT_FORMAT
        assert manifest["element_count"] == 2
        assert len(manifest["elements"]) == 2
        assert manifest["mock_assets"] == 1
        assert all(entry["sha256"] for entry in manifest["elements"])


def test_hero_flow_b_end_to_end_metrics(project, repo, monkeypatch):
    monkeypatch.setattr(images, "_real_generate", _fail_real)
    summary = hero_flow.run_hero_flow(str(repo), "hero-e2e", mock=True)
    assert summary["project_id"] == "hero-e2e"
    assert summary["element_count"] == 4
    assert summary["diagram_fallback"] is None
    assert summary["mock_assets"] >= 3
    assert summary["time_to_first_artifact_sec"] < 900
    assert summary["human_interventions"] == 0
    assert summary["correction"]["no_duplicate"] is True
    assert all(summary["acceptance"].values()), summary["acceptance"]
    assert Path(summary["export"]["path"]).is_file()
    assert summary["export"]["bytes"] > 0
    doc = store.read_document("hero-e2e")
    labels = {element.get("label") for element in doc["elements"].values()}
    assert {"Architecture", "Hero 1 · warm editorial", "Hero 3 · paper texture"} <= labels


def test_export_empty_canvas_is_a_structured_error(project):
    result = executor.execute("export_canvas", {"project_id": project["pid"]})
    assert result["ok"] is False
    assert result["error"]["code"] == "export_failed"


def test_registry_status_matches_delivered_milestones():
    assert len(registry.CAPABILITIES) == 11
    assert registry.validate_registry() == []
    status = {entry["id"]: entry["status"] for entry in registry.list_capabilities()}
    assert status["generate_diagram"] == "ready"
    assert status["generate_image"] == "ready"
    assert status["export_canvas"] == "ready"
    assert status["navigate"] == "ready"
    # M2 delivered the guidance pair; M3b made navigate real. Overlay +
    # lifecycle tests live in test_gugu_guidance.py / test_gugu_navigate.py and
    # the desktop vitest suite.
    assert registry.get_capability("navigate")["milestone"] == "M3"
    assert status["highlight"] == "ready"
    assert status["clear_guidance"] == "ready"
