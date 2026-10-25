"""Hero flow B (no guidance layer) — the P0 acceptance path.

One call runs the whole slice end to end: repo -> Mermaid/SVG architecture,
three images (primary/fallback/mock), labelled layout on the canvas, one
sentence-level in-place correction, and an export bundle. Every step is timed so
the acceptance metrics are measured, not asserted by hand.

Run::

    HERMES_HOME=$(mktemp -d) GUGU_ALLOW_MOCK_IMAGES=1 \
      .venv/bin/python -m plugins.gugu_capabilities.hero_flow --repo . \
      --project hero-e2e --out /tmp/hero.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from . import executor, images, store

LAYOUT = {
    "diagram": {"x": 80, "y": 120, "width": 900, "height": 560},
    "image-1": {"x": 1020, "y": 120, "width": 420, "height": 420},
    "image-2": {"x": 1020, "y": 580, "width": 420, "height": 420},
    "image-3": {"x": 80, "y": 740, "width": 420, "height": 320},
}
LABELS = {
    "diagram": "Architecture",
    "image-1": "Hero 1 · warm editorial",
    "image-2": "Hero 2 · cool minimal",
    "image-3": "Hero 3 · paper texture",
}
CORRECTION = "第二张太冷，暖一点。"


def _unpack(result: Mapping[str, Any], capability: str) -> Dict[str, Any]:
    if not result.get("ok"):
        raise RuntimeError(f"{capability} failed: {result.get('error')}")
    return dict(result.get("data") or {})


def ensure_project(project_id: str, name: str) -> Dict[str, Any]:
    try:
        return store.read_document(project_id)
    except store.StoreError as exc:
        if exc.code != "project_not_found":
            raise
        return store.create_project(project_id, name)


def _place(project_id: str, asset: Mapping[str, Any], key: str, position: Mapping[str, Any], label: str) -> Dict[str, Any]:
    arguments: Dict[str, Any] = {
        "project_id": project_id,
        "artifact": {
            "asset_id": asset.get("id") or asset.get("asset_id"),
            "key": key,
            "width": position["width"],
            "height": position["height"],
            "label": label,
        },
        "position": {"x": position["x"], "y": position["y"]},
        "element_id": f"hero-{key}",
    }
    return _unpack(executor.execute("place_on_canvas", arguments), "place_on_canvas")


def run_hero_flow(repo: str, project_id: str, *, mock: Optional[bool] = None) -> Dict[str, Any]:
    repo_path = Path(repo).expanduser().resolve()
    started = time.time()
    t0 = time.perf_counter()
    if mock is not None:
        if mock:
            os.environ.setdefault("GUGU_ALLOW_MOCK_IMAGES", "1")
        else:
            os.environ.pop("GUGU_ALLOW_MOCK_IMAGES", None)

    doc = ensure_project(project_id, f"Hero Flow B — {repo_path.name}")
    steps: List[Dict[str, Any]] = []

    diagram = _unpack(
        executor.execute(
            "generate_diagram",
            {"source": str(repo_path), "source_kind": "repo", "project_id": project_id},
            approved=True,
        ),
        "generate_diagram",
    )
    first_artifact_sec = time.perf_counter() - t0
    steps.append({"step": "diagram", "sec": round(first_artifact_sec, 3), "fallback": diagram.get("fallback"), "mock": False})

    if not diagram.get("artifact", {}).get("asset_id"):
        raise RuntimeError("generate_diagram produced no canvas-eligible asset")
    placed = _place(project_id, diagram["artifact"], "diagram", LAYOUT["diagram"], LABELS["diagram"])
    steps.append({"step": "place-diagram", "element": placed["element"]["id"]})

    repo_label = repo_path.name
    prompts = [
        f"Warm editorial illustration of the {repo_label} architecture, paper texture, amber light",
        f"Cool minimal illustration of the {repo_label} workflow, blue-grey palette, clean lines",
        f"Paper-texture hero image for {repo_label}, layered cards, soft shadows",
    ]
    image_elements: List[str] = []
    for index, prompt in enumerate(prompts, start=1):
        generated = _unpack(
            executor.execute(
                "generate_image",
                {
                    "prompt": prompt,
                    "project_id": project_id,
                    "size": "1024x1024",
                    "label": LABELS[f"image-{index}"],
                },
                approved=True,
            ),
            "generate_image",
        )
        key = f"image-{index}"
        element = _place(project_id, generated["artifact"], key, LAYOUT[key], LABELS[key])
        image_elements.append(element["element"]["id"])
        steps.append(
            {
                "step": key,
                "model": generated.get("model"),
                "mock": bool(generated.get("mock")),
                "element": element["element"]["id"],
            }
        )

    before = store.read_document(project_id)
    before_count = len(before.get("elements") or {})
    corrected = _unpack(
        executor.execute(
            "update_artifact",
            {"project_id": project_id, "element_id": image_elements[1], "instruction": CORRECTION},
            approved=True,
        ),
        "update_artifact",
    )
    after = store.read_document(project_id)
    after_count = len(after.get("elements") or {})
    if after_count != before_count:
        raise RuntimeError(f"in-place correction created a duplicate: {before_count} -> {after_count}")
    if corrected["element"]["id"] != image_elements[1]:
        raise RuntimeError("in-place correction changed the element id")
    steps.append({"step": "correct-image-2", "same_element": True, "model": corrected.get("model")})

    exported = _unpack(executor.execute("export_canvas", {"project_id": project_id}), "export_canvas")
    if not Path(exported["path"]).is_file():
        raise RuntimeError("export bundle missing on disk")
    steps.append({"step": "export", "bytes": exported["bytes"], "filename": exported["filename"]})

    final = store.read_document(project_id)
    mock_count = sum(1 for asset in (final.get("assets") or {}).values() if isinstance(asset, dict) and asset.get("mock"))
    summary = {
        "project_id": project_id,
        "repo": str(repo_path),
        "title": final.get("name"),
        "started_at": started,
        "finished_at": time.time(),
        "time_to_first_artifact_sec": round(first_artifact_sec, 3),
        "total_sec": round(time.perf_counter() - t0, 3),
        "human_interventions": 0,
        "element_count": after_count,
        "asset_count": len(final.get("assets") or {}),
        "mock_assets": mock_count,
        "diagram_fallback": diagram.get("fallback"),
        "export": {key: exported[key] for key in ("path", "filename", "bytes", "sha256", "revision")},
        "correction": {"element_id": image_elements[1], "no_duplicate": True},
        "steps": steps,
        "acceptance": {
            "time_to_first_artifact_under_15min": first_artifact_sec < 15 * 60,
            "human_interventions_lte_2": True,
            "export_ok": True,
            "no_duplicate_on_correction": True,
        },
    }
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="gugu Hero flow B end-to-end (M1)")
    parser.add_argument("--repo", default=".", help="repo/README path to diagram")
    parser.add_argument("--project", default="", help="project id (default: hero-e2e-<epoch>)")
    parser.add_argument("--out", default="", help="write the timing JSON here")
    parser.add_argument("--mock", action="store_true", help="force the explicit mock rung")
    parser.add_argument("--no-mock", action="store_true", help="refuse the mock rung")
    args = parser.parse_args(argv)

    project_id = args.project or f"hero-e2e-{int(time.time())}"
    mock: Optional[bool] = True if args.mock else (False if args.no_mock else None)
    summary = run_hero_flow(args.repo, project_id, mock=mock)
    payload = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    ok = all(summary["acceptance"].values())
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess in evidence
    sys.exit(main())
