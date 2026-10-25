"""Asset sniffing/dimension/write helpers for the capability plugin.

The dashboard has its own validator (``plugins/gugu-design/dashboard/gugu_storage.py``)
because the plugin directory name is hyphenated and cannot be imported as a
normal package. This module is the capability-side twin: same magic-first
discipline, plus ``image/svg+xml`` which D-P0-01 needs for rendered diagrams.
A test asserts the two implementations agree on the shared raster types.

Nothing here trusts a declared MIME: the bytes decide. SVG is only accepted when
the document actually contains an ``<svg`` root element.
"""

from __future__ import annotations

import io
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

MAX_ASSET_BYTES = 32 * 1024 * 1024
MAX_DIMENSION = 16384

_ASSET_EXT: Dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "video/mp4": "mp4",
}

_SVG_WIDTH_RE = re.compile(r'<svg[^>]*\bwidth\s*=\s*["\']([0-9.]+)(?:px)?["\']', re.IGNORECASE)
_SVG_HEIGHT_RE = re.compile(r'<svg[^>]*\bheight\s*=\s*["\']([0-9.]+)(?:px)?["\']', re.IGNORECASE)
_SVG_VIEWBOX_RE = re.compile(r'<svg[^>]*\bviewBox\s*=\s*["\']\s*[-0-9.]+[\s,]+[-0-9.]+[\s,]+([0-9.]+)[\s,]+([0-9.]+)', re.IGNORECASE)


def sniff_media(data: bytes) -> Optional[str]:
    """Return the canonical MIME for known magic bytes, else ``None``."""
    if len(data) < 12:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp":
        return "video/mp4"
    head = data[:4096].lstrip()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head):
        return "image/svg+xml"
    return None


def svg_dimensions(data: bytes) -> Tuple[int, int]:
    text = data.decode("utf-8", errors="replace")[:8192]
    width = _SVG_WIDTH_RE.search(text)
    height = _SVG_HEIGHT_RE.search(text)
    if width and height:
        return int(float(width.group(1))), int(float(height.group(1)))
    viewbox = _SVG_VIEWBOX_RE.search(text)
    if viewbox:
        return int(float(viewbox.group(1))), int(float(viewbox.group(2)))
    return 0, 0


def image_dimensions(data: bytes, media_type: str = "") -> Tuple[int, int]:
    if media_type == "image/svg+xml":
        return svg_dimensions(data)
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            return int(image.width), int(image.height)
    except Exception:  # noqa: BLE001 — video/broken bytes have no raster size
        return 0, 0


def validate_asset_bytes(data: bytes, declared_mime: str = "") -> str:
    """Validate bytes and return the canonical MIME; raises ``ValueError``."""
    sniffed = sniff_media(data)
    if sniffed is None:
        raise ValueError("不支持的资产类型（magic 校验失败，仅 png/jpeg/webp/svg/mp4）")
    if len(data) > MAX_ASSET_BYTES:
        raise ValueError(f"资产超过 {MAX_ASSET_BYTES // (1024 * 1024)}MB 上限")
    if declared_mime:
        declared = declared_mime.split(";")[0].strip()
        # Declared/actual mismatch is allowed only within one media family; an
        # image disguised as video is rejected.
        if declared and declared != sniffed and not declared.startswith(sniffed.split("/")[0] + "/"):
            raise ValueError(f"资产类型声明 {declared!r} 与实测 {sniffed!r} 不符")
    if sniffed.startswith("image/"):
        width, height = image_dimensions(data, sniffed)
        if max(width, height) > MAX_DIMENSION:
            raise ValueError(f"图像边长超过 {MAX_DIMENSION}px 上限")
    return sniffed


def asset_filename(media_type: str, asset_id: str) -> str:
    return f"{asset_id}.{_ASSET_EXT.get(media_type, 'bin')}"


def media_extension(media_type: str) -> str:
    return _ASSET_EXT.get(media_type, "bin")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)


def asset_metadata(asset: Mapping[str, Any]) -> Dict[str, Any]:
    """Public summary that keeps generation provenance (model/mock/prompt)."""
    keys = (
        "id",
        "name",
        "mediaType",
        "bytes",
        "width",
        "height",
        "createdAt",
        "kind",
        "versionId",
        "prompt",
        "model",
        "mock",
        "source",
        "fallback",
    )
    return {key: asset.get(key) for key in keys if key in asset}
