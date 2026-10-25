"""Export a canvas as a self-contained bundle.

No new dependency: ``zipfile`` + ``base64`` + a hand-written ``preview.svg``.
The bundle carries the Host-authoritative document, every referenced asset,
a manifest with checksums, and a preview board with labels — the same title /
label / position data the canvas renders.
"""

from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from . import assets, store

EXPORT_FORMAT = "gugu.canvas-export/v1"
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)


class ExportError(Exception):
    pass


def _asset_bytes(project_dir: Path, asset: Mapping[str, Any]) -> bytes:
    filename = assets.asset_filename(str(asset.get("mediaType") or ""), str(asset.get("id") or ""))
    path = project_dir / "assets" / filename
    if not path.is_file():
        return b""
    return path.read_bytes()


def _preview_svg(doc: Mapping[str, Any], project_dir: Path) -> str:
    elements = [item for item in (doc.get("elements") or {}).values() if isinstance(item, dict) and not item.get("hidden")]
    if elements:
        min_x = min(float(item.get("x", 0)) for item in elements)
        min_y = min(float(item.get("y", 0)) for item in elements)
        max_x = max(float(item.get("x", 0)) + float(item.get("width", 0)) for item in elements)
        max_y = max(float(item.get("y", 0)) + float(item.get("height", 0)) for item in elements)
    else:
        min_x = min_y = 0.0
        max_x, max_y = 960.0, 540.0
    margin = 56.0
    title_h = 84.0
    width = max(max_x - min_x + margin * 2, 720.0)
    height = max(max_y - min_y + margin * 2 + title_h, 480.0)
    scale = min(1.0, 1600.0 / width)
    width_s, height_s = width * scale, height * scale

    name = html.escape(str(doc.get("name") or doc.get("id") or "canvas"))
    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_s:.0f}" height="{height_s:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="{name}">',
        f'<rect width="{width:.0f}" height="{height:.0f}" fill="#f4f0e8"/>',
        f'<text x="{margin:.0f}" y="{margin - 2:.0f}" font-family="system-ui, sans-serif" font-size="30" '
        f'font-weight="600" fill="#23262b">{name}</text>',
        f'<text x="{margin:.0f}" y="{margin + 24:.0f}" font-family="system-ui, sans-serif" font-size="13" '
        f'fill="#7a828e">revision {int(doc.get("revision", 0))} · exported {time.strftime("%Y-%m-%d %H:%M:%S")}</text>',
    ]
    asset_map = doc.get("assets") if isinstance(doc.get("assets"), dict) else {}
    for element in sorted(elements, key=lambda item: float(item.get("z", 0))):
        asset = asset_map.get(element.get("assetId"))
        x = float(element.get("x", 0)) - min_x + margin
        y = float(element.get("y", 0)) - min_y + margin + title_h
        w = float(element.get("width", 0))
        h = float(element.get("height", 0))
        label = str(element.get("label") or (asset or {}).get("name") or "")
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="#ffffff" stroke="#d6cec1" rx="8"/>')
        if isinstance(asset, dict):
            data = _asset_bytes(project_dir, asset)
            if data:
                media = str(asset.get("mediaType") or "image/png")
                payload = base64.b64encode(data).decode("ascii")
                parts.append(
                    f'<image x="{x + 2:.1f}" y="{y + 2:.1f}" width="{max(w - 4, 1):.1f}" height="{max(h - 4, 1):.1f}" '
                    f'preserveAspectRatio="xMidYMid meet" href="data:{html.escape(media)};base64,{payload}"/>'
                )
            if asset.get("mock"):
                parts.append(
                    f'<text x="{x + 10:.1f}" y="{y + 22:.1f}" font-family="system-ui, sans-serif" font-size="12" '
                    f'font-weight="700" fill="#c43c28">MOCK · {html.escape(str(asset.get("model") or "mock"))}</text>'
                )
        if label:
            parts.append(
                f'<text x="{x + w / 2:.1f}" y="{y + h + 20:.1f}" text-anchor="middle" font-family="system-ui, sans-serif" '
                f'font-size="14" fill="#3b4048">{html.escape(label[:80])}</text>'
            )
    parts.append(
        f'<text x="{margin:.0f}" y="{height - 18:.0f}" font-family="system-ui, sans-serif" font-size="11" '
        f'fill="#9aa0a8">gugu canvas export · preview.svg · source document in project.json</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def _manifest(doc: Mapping[str, Any], project_dir: Path) -> Dict[str, Any]:
    asset_map = doc.get("assets") if isinstance(doc.get("assets"), dict) else {}
    entries: List[Dict[str, Any]] = []
    for element in sorted((doc.get("elements") or {}).values(), key=lambda item: float(item.get("z", 0))):
        if not isinstance(element, dict):
            continue
        asset = asset_map.get(element.get("assetId")) or {}
        data = _asset_bytes(project_dir, asset) if isinstance(asset, dict) else b""
        entries.append(
            {
                "element_id": element.get("id"),
                "label": element.get("label") or "",
                "asset_id": element.get("assetId"),
                "media_type": asset.get("mediaType"),
                "mock": bool(asset.get("mock")),
                "model": asset.get("model"),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest() if data else None,
                "x": element.get("x"),
                "y": element.get("y"),
                "width": element.get("width"),
                "height": element.get("height"),
            }
        )
    return {
        "format": EXPORT_FORMAT,
        "schema_version": doc.get("schemaVersion", 1),
        "project_id": doc.get("id"),
        "name": doc.get("name"),
        "revision": doc.get("revision"),
        "exported_at": time.time(),
        "viewport": doc.get("viewport"),
        "element_count": len(entries),
        "elements": entries,
        "asset_count": len(asset_map),
        "mock_assets": sum(1 for asset in asset_map.values() if isinstance(asset, dict) and asset.get("mock")),
    }


def _zip_bytes(files: Sequence[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files:
            info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, payload)
    return buffer.getvalue()


def export_project(project_id: str, fmt: str = "zip") -> Dict[str, Any]:
    """Build ``exports/<pid>-r<rev>-<hash>.zip``; returns a machine summary."""
    if (fmt or "zip").strip().lower() != "zip":
        raise ExportError(f"暂不支持导出格式: {fmt!r}（只有 zip）")
    doc = store.read_document(project_id)
    directory = store.project_dir(project_id)
    elements = doc.get("elements") if isinstance(doc.get("elements"), dict) else {}
    if not elements:
        raise ExportError("画布为空，没有可导出的内容")
    preview = _preview_svg(doc, directory)
    manifest = _manifest(doc, directory)
    files: List[tuple[str, bytes]] = [
        ("project.json", json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8")),
        ("preview.svg", preview.encode("utf-8")),
        ("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")),
    ]
    asset_map = doc.get("assets") if isinstance(doc.get("assets"), dict) else {}
    for asset in asset_map.values():
        if not isinstance(asset, dict):
            continue
        data = _asset_bytes(directory, asset)
        if data:
            files.append((f"assets/{assets.asset_filename(str(asset.get('mediaType') or ''), str(asset.get('id') or ''))}", data))
    payload = _zip_bytes(files)
    digest = hashlib.sha256(payload).hexdigest()
    exports = directory / "exports"
    exports.mkdir(exist_ok=True)
    filename = f"{project_id}-r{int(doc.get('revision', 0))}-{digest[:8]}.zip"
    path = exports / filename
    assets.atomic_write_bytes(path, payload)
    return {
        "format": "zip",
        "path": str(path),
        "filename": filename,
        "bytes": len(payload),
        "sha256": digest,
        "revision": int(doc.get("revision", 0)),
        "element_count": len(elements),
        "asset_count": int(manifest["asset_count"]),
        "mock_assets": int(manifest["mock_assets"]),
        "preview": "preview.svg",
    }
