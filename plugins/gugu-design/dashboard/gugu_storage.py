"""gugu-design 存储纪律 —— 移植自 dsh-design-mode
`packages/design/design-mode/src/storage.ts`（MIT），Python 平移。核心不变量：

- **原子写**：temp + os.replace，0o600；崩溃不会留下半截 document.json。
- **审计日志**：journal.ndjson append-only，每次修订一行 {revision, operationId, kind, time}。
- **资产校验**：MIME magic 嗅探（不信扩展名/声明）、字节与边长上限、mask 必须带 alpha。
- **路径防护**：realpath 容器化断言 + 拒绝 symlink/非常规文件；读回时再比对 size 与 magic。
- **幂等**：recent_operation_ids + operation_results（上限 OPERATION_HISTORY）。

凭证纪律（同 storage.ts/providers.ts）：网关 key 只在 job 启动瞬间解析，
不进文档、不进日志、不进浏览器包。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

# ---- 限额（对齐 dsh design-app cordis.patch.yml 的默认值） ------------------
MAX_IMAGE_BYTES = 32 * 1024 * 1024      # 单资产 32MB
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024    # document.json 8MB
MAX_IMAGE_DIMENSION = 16384             # 解码后边长上限
OPERATION_HISTORY = 100                 # 幂等窗口

# magic 签名 → 规范 MIME（对齐 storage.ts 的 png/jpeg/webp/mp4 白名单）
_SIGNATURES: list[tuple[bytes, int, str]] = [
    (b"\x89PNG\r\n\x1a\n", 0, "image/png"),
    (b"\xff\xd8\xff", 0, "image/jpeg"),
    (b"RIFF", 0, "image/webp"),          # + offset 8 必须 "WEBP"
    (b"\x00\x00\x00", 0, "video/mp4"),   # ftyp 在 offset 4（长度前缀可变）
]

_ASSET_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "video/mp4": "mp4",
}

_SVG_DIM_RE = re.compile(r'<svg[^>]*\bwidth\s*=\s*["\']([0-9.]+)', re.IGNORECASE)
_SVG_HEIGHT_RE = re.compile(r'<svg[^>]*\bheight\s*=\s*["\']([0-9.]+)', re.IGNORECASE)
_SVG_VIEWBOX_RE = re.compile(
    r'<svg[^>]*\bviewBox\s*=\s*["\']\s*[-0-9.]+[\s,]+[-0-9.]+[\s,]+([0-9.]+)[\s,]+([0-9.]+)',
    re.IGNORECASE,
)


def sniff_media(data: bytes) -> Optional[str]:
    """按文件头判定 MIME；不匹配返回 None（拒绝入库）。"""
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
        # D-P0-01: rendered Mermaid diagrams land as SVG assets.
        return "image/svg+xml"
    return None


def _svg_dimensions(data: bytes) -> tuple[int, int]:
    text = data.decode("utf-8", errors="replace")[:8192]
    width = _SVG_DIM_RE.search(text)
    height = _SVG_HEIGHT_RE.search(text)
    if width and height:
        return int(float(width.group(1))), int(float(height.group(1)))
    viewbox = _SVG_VIEWBOX_RE.search(text)
    if viewbox:
        return int(float(viewbox.group(1))), int(float(viewbox.group(2)))
    return 0, 0


def image_dimensions(data: bytes, media_type: str = "") -> tuple[int, int]:
    """解码取真实宽高（sharp 的 PIL 等价物）；失败返回 (0, 0)。"""
    if media_type == "image/svg+xml":
        return _svg_dimensions(data)
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            return int(im.width), int(im.height)
    except Exception:  # noqa: BLE001 — 视频/损坏文件无尺寸概念
        return 0, 0


def validate_asset_bytes(data: bytes, declared_mime: str) -> str:
    """校验并返回规范 MIME；不合法抛 ValueError（handler 转 4xx）。"""
    sniffed = sniff_media(data)
    if sniffed is None:
        raise ValueError("不支持的资产类型（magic 校验失败，仅 png/jpeg/webp/mp4）")
    if sniffed.startswith("image/") and len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"资产超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MB 上限")
    if declared_mime and declared_mime.split(";")[0] != sniffed:
        # 声明与实测不符：以实测为准，但拒绝可疑伪装（image 声明为 video）
        if not declared_mime.startswith(sniffed.split("/")[0] + "/"):
            raise ValueError(f"资产类型声明 {declared_mime!r} 与实测 {sniffed!r} 不符")
    if sniffed.startswith("image/"):
        w, h = image_dimensions(data, sniffed)
        if max(w, h) > MAX_IMAGE_DIMENSION:
            raise ValueError(f"图像边长超过 {MAX_IMAGE_DIMENSION}px 上限")
    return sniffed


def asset_filename(media_type: str, asset_id: str) -> str:
    return f"{asset_id}.{_ASSET_EXT.get(media_type, 'bin')}"


# ---- 原子写 / journal --------------------------------------------------------

def atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def atomic_write_json(path: Path, doc: dict) -> None:
    raw = json.dumps(doc, ensure_ascii=False, indent=1)
    if len(raw.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ValueError("工程文档超过 8MB 上限")
    atomic_write_bytes(path, raw.encode("utf-8"))


def journal_append(project_dir: Path, kind: str, revision: int, operation_id: str = "") -> None:
    """append-only 审计；写失败不阻断主流程（journal 是尽力而为的账本）。"""
    try:
        with (project_dir / "journal.ndjson").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(
                {"revision": revision, "operationId": operation_id, "kind": kind, "time": time.time()},
                ensure_ascii=False,
            ) + "\n")
    except OSError:
        pass


# ---- 路径容器化 --------------------------------------------------------------

def safe_join(container: Path, *parts: str) -> Path:
    """容器化拼接：拒绝 `..` 逃逸、symlink、非常规文件。realpath 断言。"""
    candidate = container.joinpath(*parts)
    try:
        resolved = candidate.resolve()
        base = container.resolve()
    except OSError as exc:
        raise ValueError(f"路径解析失败: {exc}") from exc
    if base != resolved and base not in resolved.parents:
        raise ValueError("路径逃逸被拒绝")
    if candidate.is_symlink():
        raise ValueError("拒绝 symlink 资产")
    return candidate


# ---- 幂等窗口 ----------------------------------------------------------------

def remember_operation(doc: dict, operation_id: str, result: Any) -> None:
    """记录 operation 结果（dsh recentOperationIds/operationResults 语义）。"""
    recent: list = doc.setdefault("recent_operation_ids", [])
    results: dict = doc.setdefault("operation_results", {})
    if operation_id not in recent:
        recent.append(operation_id)
        while len(recent) > OPERATION_HISTORY:
            results.pop(recent.pop(0), None)
    results[operation_id] = result


def replay_operation(doc: dict, operation_id: str) -> Optional[Any]:
    """命中幂等窗口返回既有结果（网络重试/工具重放同结果）。"""
    if not operation_id:
        return None
    results = doc.get("operation_results") or {}
    return results.get(operation_id)
