"""Image generation for D-P0-02.

Order is explicit: primary ``qwen-image-2.0`` -> fallback ``gpt-image-2-medium``
-> mock, and the model names are configuration (environment) rather than a
hard-coded vendor lock. A mock is never disguised: the artifact carries
``mock: true``, ``model: "mock"`` and the file itself is watermarked.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

DEFAULT_PRIMARY_MODEL = "qwen-image-2.0"
DEFAULT_FALLBACK_MODEL = "gpt-image-2-medium"
DEFAULT_SIZE = "1024x1024"
MAX_BYTES = 32 * 1024 * 1024

_SIZE_RE = re.compile(r"^(\d{2,4})x(\d{2,4})$")
_PRODUCTION = {"prod", "production", "live"}


class ImageError(Exception):
    """Generation failed after every allowed rung of the ladder."""

    def __init__(self, message: str, attempts: Optional[Sequence[Mapping[str, Any]]] = None) -> None:
        super().__init__(message)
        self.attempts = [dict(item) for item in (attempts or [])]


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def is_production() -> bool:
    for name in ("GUGU_ENV", "HERMES_ENV", "NODE_ENV"):
        if os.environ.get(name, "").strip().lower() in _PRODUCTION:
            return True
    return False


def mock_allowed() -> bool:
    """Mock is a non-production rung; production must fail honestly."""
    if is_production():
        return False
    if _flag("GUGU_ALLOW_MOCK_IMAGES"):
        return True
    return os.environ.get("GUGU_IMAGE_MODE", "").strip().lower() == "mock"


def model_candidates(requested: str = "") -> List[str]:
    """Requested model, then primary, then fallback; env overrides defaults."""
    primary = os.environ.get("GUGU_IMAGE_MODEL_PRIMARY", "").strip() or DEFAULT_PRIMARY_MODEL
    fallback = os.environ.get("GUGU_IMAGE_MODEL_FALLBACK", "").strip() or DEFAULT_FALLBACK_MODEL
    extra = [item.strip() for item in os.environ.get("GUGU_IMAGE_MODELS", "").split(",") if item.strip()]
    candidates: List[str] = []
    for item in [requested.strip(), primary, *extra, fallback]:
        if item and item not in candidates:
            candidates.append(item)
    return candidates


def normalise_size(size: str = "") -> str:
    value = (size or DEFAULT_SIZE).strip()
    match = _SIZE_RE.match(value)
    if not match:
        return DEFAULT_SIZE
    width, height = int(match.group(1)), int(match.group(2))
    if width > 2048 or height > 2048:
        return DEFAULT_SIZE
    return f"{width}x{height}"


def _gateway_credentials() -> Tuple[str, str]:
    """Env first; then the desktop's logged-in gateway helper.

    The lazy import is deliberate: headless/CI runs never touch config, and a
    remote backend without a login simply falls to the mock rung. Credentials
    are resolved at call time and never written to logs or the document.
    """
    base = (os.environ.get("GUGU_IMAGE_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("GUGU_IMAGE_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()
    if base and key:
        return base, key
    try:
        from hermes_cli.gugu_service import gateway_credentials

        return gateway_credentials()
    except Exception:  # noqa: BLE001 — not logged in / headless probe must not crash generation
        return base, key


def _real_generate(prompt: str, model: str, size: str, timeout: float = 180.0) -> Tuple[bytes, str]:
    import httpx

    base, key = _gateway_credentials()
    if not base or not key:
        raise ImageError("网关未配置（GUGU_IMAGE_BASE_URL/API_KEY 或桌面登录）")
    body: Dict[str, Any] = {"model": model, "prompt": prompt, "n": 1, "size": size}
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            f"{base}/images/generations",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body,
        )
        if response.status_code != 200:
            raise ImageError(f"HTTP {response.status_code}: {response.text[:160]}")
        payload = response.json()
        items = payload.get("data") or []
        if not items:
            raise ImageError("网关未返回图像数据")
        item = items[0]
        if item.get("b64_json"):
            data = base64.b64decode(item["b64_json"])
        elif item.get("url"):
            download = client.get(str(item["url"]), headers={"Authorization": f"Bearer {key}"})
            if download.status_code != 200:
                raise ImageError(f"下载生成图失败: HTTP {download.status_code}")
            data = download.content
        else:
            raise ImageError("网关返回缺少 b64_json/url")
    if len(data) > MAX_BYTES:
        raise ImageError("生成图超过 32MB 上限")
    media_type = _sniff(data)
    if media_type is None:
        raise ImageError("生成结果不是受支持的图像格式")
    return data, media_type


def _sniff(data: bytes) -> Optional[str]:
    from . import assets

    return assets.sniff_media(data)


# ---------------------------------------------------------------------------
# Mock rung (explicitly watermarked, non-production only)
# ---------------------------------------------------------------------------


def _palette(prompt: str, warm: Optional[bool] = None) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
    lower = prompt.lower()
    if warm is None:
        warm = any(token in lower for token in ("warm", "暖", "cozy", "orange", "amber"))
    if warm:
        return (247, 205, 158), (196, 108, 74)
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    a = tuple(int(digest[index : index + 2], 16) for index in (0, 2, 4))
    b = tuple(int(digest[index : index + 2], 16) for index in (6, 8, 10))
    return (max(a[0], 120), max(a[1], 120), max(a[2], 120)), b  # type: ignore[return-value]


def mock_png(prompt: str, size: str = DEFAULT_SIZE, label: str = "", warm: Optional[bool] = None) -> bytes:
    width, height = (int(part) for part in normalise_size(size).split("x"))
    bg, accent = _palette(prompt, warm)
    message = (label or prompt or "mock image").strip()[:120]
    try:
        from PIL import Image, ImageDraw, ImageFont

        image = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(image)
        for index in range(10):
            x = int(width * (index / 10))
            draw.rectangle([x, 0, x + max(2, width // 22), height], fill=accent if index % 2 == 0 else bg)
        draw.rectangle([24, 24, width - 24, height - 24], outline=(35, 38, 43), width=4)
        draw.rectangle([24, height - 96, width - 24, height - 24], fill=(255, 255, 255))
        font_big = ImageFont.load_default(size=max(22, width // 22))
        font_small = ImageFont.load_default(size=max(14, width // 48))
        draw.text((48, 44), "MOCK IMAGE", fill=(220, 70, 50), font=font_big)
        draw.text((48, 44 + max(34, width // 20)), "not a real generation", fill=(35, 38, 43), font=font_small)
        y = 44 + max(70, width // 14)
        for line in _wrap(message, 46):
            draw.text((48, y), line, fill=(35, 38, 43), font=font_small)
            y += max(20, width // 48)
        payload = io.BytesIO()
        image.save(payload, format="PNG", optimize=True)
        return payload.getvalue()
    except Exception:  # noqa: BLE001 — keep the fallback rung alive without Pillow
        return _solid_png(width, height, bg)


def _solid_png(width: int, height: int, color: Tuple[int, int, int]) -> bytes:
    """Minimal valid PNG (RGB, no interlace) for environments without Pillow."""
    import struct
    import zlib

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    row = bytes(color) * width
    raw = b"\x00" + row
    data = zlib.compress(raw * height, 9)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", data) + chunk(b"IEND", b"")


def _wrap(text: str, width: int) -> List[str]:
    lines: List[str] = []
    current = ""
    for char in text:
        current += char
        if len(current) >= width:
            lines.append(current)
            current = ""
    if current:
        lines.append(current)
    return lines[:4] or [text]


def generate_with_fallback(
    prompt: str,
    *,
    model: str = "",
    size: str = "",
    label: str = "",
    warm: Optional[bool] = None,
) -> Dict[str, Any]:
    """Try requested/primary/fallback, then the explicit mock rung."""
    size = normalise_size(size)
    candidates = model_candidates(model)
    attempts: List[Dict[str, Any]] = []
    for candidate in candidates:
        try:
            data, media_type = _real_generate(prompt, candidate, size)
            attempts.append({"model": candidate, "ok": True})
            return {
                "data": data,
                "media_type": media_type,
                "model": candidate,
                "mock": False,
                "attempts": attempts,
                "size": size,
            }
        except Exception as exc:  # noqa: BLE001 — every rung is reported, never hidden
            attempts.append({"model": candidate, "ok": False, "error": str(exc)[:200]})
    if mock_allowed():
        data = mock_png(prompt, size, label=label, warm=warm)
        attempts.append({"model": "mock", "ok": True, "explicit": True})
        return {
            "data": data,
            "media_type": "image/png",
            "model": "mock",
            "mock": True,
            "attempts": attempts,
            "size": size,
        }
    raise ImageError("主/备模型均失败，且 mock 未启用（生产环境不允许 mock）", attempts)


def image_metadata(result: Mapping[str, Any], prompt: str, *, source: str, correction: str = "") -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "prompt": prompt,
        "model": result.get("model"),
        "mock": bool(result.get("mock")),
        "size": result.get("size"),
        "source": source,
        "attempts": list(result.get("attempts") or []),
    }
    if correction:
        meta["correction"] = correction
    return meta


def mock_filename_hint(prompt: str) -> str:
    return f"MOCK-{hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:8]}"
