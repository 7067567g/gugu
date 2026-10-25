"""Canvas store for M0 capabilities.

The agent process and the desktop dashboard are separate processes, so the
dashboard's in-process ``threading.Lock`` cannot serialize agent writes. This
module uses an OS file lock (``fcntl.flock``) on ``<project>/agent.lock`` for
every read-modify-write, plus atomic temp+rename writes — the same document
discipline as ``plugins/gugu-design/dashboard/gugu_storage.py``.

M0 scope: agent actions touch only the canvas fields (``elements`` /
``assets`` / ``versions`` / ``viewport``). Undo snapshots those fields and
merges them back, so a concurrent job-status update is never rolled back.
The renderer does not receive the dashboard's ``/events`` broadcast for agent
writes; the M1 host-RPC path closes that gap (see docs/05-agent-capabilities.md).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional

from . import assets as _assets

try:  # POSIX; M0 targets Linux/macOS desktop dev boxes.
    import fcntl
except ImportError:  # pragma: no cover - Windows is explicitly out of M0 scope
    fcntl = None  # type: ignore[assignment]

_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ELEMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")

UNDO_LIMIT = 10
OPS_LIMIT = 100
CANVAS_FIELDS = ("elements", "assets", "versions", "viewport")

_thread_locks: Dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


class StoreError(Exception):
    """A capability-level failure with a stable machine code."""

    def __init__(self, code: str, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def as_error(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


def designs_root() -> Path:
    from hermes_constants import get_default_hermes_root

    return Path(get_default_hermes_root()) / "designs"


def runtime_state_path() -> Path:
    return designs_root() / "agent-state.json"


def read_runtime_state() -> Optional[Dict[str, Any]]:
    """Latest surface/project/selection snapshot published by the renderer."""
    path = runtime_state_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Guidance mailbox (M2)
#
# The backend has no renderer-directed host channel yet, so the agent writes
# the ONE active guidance step to ``designs/guidance.json`` and the /design
# surface polls it. Last writer wins; the renderer deletes it on any local
# interrupt so the agent's next read never sees a stale overlay. Keep the file
# name in sync with plugins/gugu-design/dashboard/plugin_api.py.
# ---------------------------------------------------------------------------


def guidance_path() -> Path:
    return designs_root() / "guidance.json"


def read_guidance() -> Optional[Dict[str, Any]]:
    path = guidance_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_guidance(payload: Mapping[str, Any]) -> None:
    _atomic_write_json(guidance_path(), dict(payload))


def clear_guidance(guidance_id: Optional[str] = None) -> bool:
    """Delete the mailbox; with ``guidance_id`` only if it still points there.

    Returns whether a file was actually removed, so ``clear_guidance`` can be
    safely retried (second call reports ``cleared=false``).
    """
    path = guidance_path()
    current = read_guidance()
    if current is None:
        return False
    if guidance_id and str(current.get("guidance_id") or "") != str(guidance_id):
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def project_dir(project_id: str) -> Path:
    if not isinstance(project_id, str) or _PROJECT_ID_RE.match(project_id) is None:
        raise StoreError("invalid_project_id", f"非法工程 id: {project_id!r}")
    directory = designs_root() / project_id
    if not directory.is_dir():
        raise StoreError(
            "project_not_found",
            f"工程不存在: {project_id}",
            {"project_id": project_id, "designs_root": str(designs_root())},
        )
    return directory


@contextmanager
def project_lock(project_id: str) -> Iterator[None]:
    directory = project_dir(project_id)
    lock_path = directory / "agent.lock"
    with open(lock_path, "a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        else:  # pragma: no cover - Windows out of M0 scope
            with _thread_lock(project_id):
                yield
            return
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _thread_lock(project_id: str) -> threading.Lock:
    with _thread_locks_guard:
        return _thread_locks.setdefault(project_id, threading.Lock())


# ---------------------------------------------------------------------------
# Document I/O
# ---------------------------------------------------------------------------


def load_document(project_id: str) -> Dict[str, Any]:
    path = project_dir(project_id) / "project.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StoreError("project_document_missing", f"工程文档缺失: {project_id}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise StoreError("project_document_unreadable", f"工程文档不可读: {exc}") from exc
    if not isinstance(doc, dict):
        raise StoreError("project_document_invalid", "工程文档不是对象")
    return doc


def read_document(project_id: str) -> Dict[str, Any]:
    with project_lock(project_id):
        return load_document(project_id)


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)


def _save_document(project_id: str, doc: Dict[str, Any]) -> None:
    _atomic_write_json(project_dir(project_id) / "project.json", doc)


def create_project(project_id: str, name: str) -> Dict[str, Any]:
    """Create an empty canvas document (used by the hero E2E and tests)."""
    if _PROJECT_ID_RE.match(str(project_id)) is None:
        raise StoreError("invalid_project_id", f"非法工程 id: {project_id!r}")
    directory = designs_root() / project_id
    if directory.exists():
        raise StoreError("project_exists", f"工程已存在: {project_id}")
    (directory / "assets").mkdir(parents=True)
    now = time.time()
    doc: Dict[str, Any] = {
        "schemaVersion": 1,
        "revision": 1,
        "id": project_id,
        "name": name or project_id,
        "createdAt": now,
        "updatedAt": now,
        "viewport": {"x": 0, "y": 0, "scale": 1},
        "assets": {},
        "comments": {},
        "elements": {},
        "versions": {},
        "jobs": {},
        "recent_operation_ids": [],
        "operation_results": {},
    }
    _atomic_write_json(directory / "project.json", doc)
    return copy.deepcopy(doc)


_ASSET_META_KEYS = ("prompt", "model", "mock", "size", "source", "fallback", "attempts", "correction", "diagram")


def register_asset(
    project_id: str,
    data: bytes,
    media_type: str,
    name: str,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate + persist one generated artifact into the project document."""
    media = _assets.validate_asset_bytes(data, media_type)
    with project_lock(project_id):
        doc = load_document(project_id)
        asset_id = uuid.uuid4().hex[:16]
        filename = _assets.asset_filename(media, asset_id)
        _assets.atomic_write_bytes(project_dir(project_id) / "assets" / filename, data)
        width, height = _assets.image_dimensions(data, media)
        asset: Dict[str, Any] = {
            "id": asset_id,
            "name": name,
            "mediaType": media,
            "bytes": len(data),
            "width": width,
            "height": height,
            "createdAt": time.time(),
            "kind": "video" if media.startswith("video/") else "image",
        }
        for key in _ASSET_META_KEYS:
            if metadata and key in metadata and metadata[key] not in (None, ""):
                asset[key] = copy.deepcopy(metadata[key])
        version_id = uuid.uuid4().hex[:16]
        version = {"id": version_id, "assetId": asset_id, "createdAt": time.time(), "label": name}
        assets_map = doc.setdefault("assets", {})
        if not isinstance(assets_map, dict):
            raise StoreError("project_document_invalid", "assets 不是对象")
        versions_map = doc.setdefault("versions", {})
        if not isinstance(versions_map, dict):
            raise StoreError("project_document_invalid", "versions 不是对象")
        assets_map[asset_id] = asset
        versions_map[version_id] = version
        asset["versionId"] = version_id
        doc["revision"] = int(doc.get("revision", 0)) + 1
        doc["updatedAt"] = time.time()
        _save_document(project_id, doc)
        _append_activity(
            project_id,
            "register_asset",
            doc["revision"],
            {"asset_id": asset_id, "media_type": media, "mock": bool(asset.get("mock")), "name": name},
        )
        return _assets.asset_metadata(asset)


def write_text_artifact(project_id: str, text: str, name: str = "outline") -> Dict[str, Any]:
    """Persist the last-resort text outline (not a canvas asset)."""
    with project_lock(project_id):
        load_document(project_id)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        directory = project_dir(project_id) / "artifacts"
        directory.mkdir(exist_ok=True)
        path = directory / f"{name}-{digest}.md"
        payload = text.encode("utf-8")
        _assets.atomic_write_bytes(path, payload)
        return {
            "path": str(path),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "format": "text/markdown",
        }


def element_with_asset(project_id: str, element_id: str) -> Dict[str, Any]:
    with project_lock(project_id):
        doc = load_document(project_id)
        elements = doc.get("elements") if isinstance(doc.get("elements"), dict) else {}
        element = elements.get(element_id)
        if not isinstance(element, dict):
            raise StoreError("element_not_found", f"元素不存在: {element_id}", {"element_id": element_id})
        assets_map = doc.get("assets") if isinstance(doc.get("assets"), dict) else {}
        asset = assets_map.get(element.get("assetId"))
        return {"element": copy.deepcopy(element), "asset": copy.deepcopy(asset) if isinstance(asset, dict) else {}}


def _undo_path(project_id: str) -> Path:
    return project_dir(project_id) / "agent-undo.json"


def _ops_path(project_id: str) -> Path:
    return project_dir(project_id) / "agent-ops.json"


def _activity_path(project_id: str) -> Path:
    return project_dir(project_id) / "agent-activity.ndjson"


def _load_stack(project_id: str) -> List[Dict[str, Any]]:
    path = _undo_path(project_id)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    stack = payload.get("stack") if isinstance(payload, dict) else None
    return [item for item in stack if isinstance(item, dict)] if isinstance(stack, list) else []


def _save_stack(project_id: str, stack: List[Dict[str, Any]]) -> None:
    _atomic_write_json(_undo_path(project_id), {"schema": "gugu.capability-undo/v1", "stack": stack[-UNDO_LIMIT:]})


def _load_ops(project_id: str) -> Dict[str, Any]:
    path = _ops_path(project_id)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    ops = payload.get("ops") if isinstance(payload, dict) else None
    return ops if isinstance(ops, dict) else {}


def _save_ops(project_id: str, ops: Mapping[str, Any]) -> None:
    ordered = dict(list(ops.items())[-OPS_LIMIT:])
    _atomic_write_json(_ops_path(project_id), {"schema": "gugu.capability-ops/v1", "ops": ordered})


def _append_activity(project_id: str, capability: str, revision: int, detail: Mapping[str, Any]) -> None:
    entry = {
        "ts": time.time(),
        "capability": capability,
        "revision": revision,
        "detail": dict(detail),
    }
    with open(_activity_path(project_id), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _snapshot_canvas(doc: Mapping[str, Any]) -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {"revision": int(doc.get("revision", 0))}
    for field in CANVAS_FIELDS:
        value = doc.get(field)
        snapshot[field] = copy.deepcopy(value) if isinstance(value, (dict, list)) else value
    return snapshot


def _remember_operation(
    project_id: str,
    operation_id: str,
    capability: str,
    element_id: str,
    result: Mapping[str, Any],
) -> None:
    ops = _load_ops(project_id)
    ops[operation_id] = {
        "capability": capability,
        "element_id": element_id,
        "result": dict(result),
        "at": time.time(),
    }
    _save_ops(project_id, ops)


def _replay_operation(
    project_id: str,
    operation_id: Optional[str],
    capability: str,
    element_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    if not operation_id:
        return None
    entry = _load_ops(project_id).get(operation_id)
    if not isinstance(entry, dict):
        return None
    if entry.get("capability") != capability:
        raise StoreError(
            "operation_id_conflict",
            f"operation_id {operation_id!r} 已用于 {entry.get('capability')!r}",
        )
    if element_id and entry.get("element_id") != element_id:
        raise StoreError("operation_id_conflict", f"operation_id {operation_id!r} 指向另一个元素")
    return entry


# ---------------------------------------------------------------------------
# Summaries (read_state)
# ---------------------------------------------------------------------------


def element_summary(element: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": element.get("id"),
        "assetId": element.get("assetId"),
        "versionId": element.get("versionId"),
        "type": element.get("type"),
        "x": element.get("x"),
        "y": element.get("y"),
        "width": element.get("width"),
        "height": element.get("height"),
        "rotation": element.get("rotation"),
        "z": element.get("z"),
        "hidden": bool(element.get("hidden")),
        "locked": bool(element.get("locked")),
        "label": element.get("label") or "",
    }


def canvas_summary(doc: Mapping[str, Any], limit: int = 200) -> Dict[str, Any]:
    elements = doc.get("elements") if isinstance(doc.get("elements"), dict) else {}
    items = sorted(elements.values(), key=lambda item: float(item.get("z", 0)))
    return {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "revision": int(doc.get("revision", 0)),
        "viewport": copy.deepcopy(doc.get("viewport") or {}),
        "element_count": len(elements),
        "visible_count": sum(1 for item in elements.values() if not item.get("hidden")),
        "asset_count": len(doc.get("assets") or {}),
        "elements": [element_summary(item) for item in items[:limit]],
        "truncated": len(items) > limit,
    }


def project_summary(doc: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "revision": int(doc.get("revision", 0)),
        "updatedAt": doc.get("updatedAt"),
        "element_count": len(doc.get("elements") or {}),
    }


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


def _require_asset(doc: Mapping[str, Any], asset_id: str) -> Dict[str, Any]:
    assets = doc.get("assets") if isinstance(doc.get("assets"), dict) else {}
    asset = assets.get(asset_id)
    if not isinstance(asset, dict):
        raise StoreError(
            "unknown_asset",
            f"asset 不存在: {asset_id}",
            {"asset_id": asset_id, "known_assets": sorted(assets)[:20]},
        )
    return asset


def _normalise_element_id(value: Optional[str], key: str) -> str:
    candidate = (value or "").strip()
    if candidate:
        if _ELEMENT_ID_RE.match(candidate) is None:
            raise StoreError("invalid_element_id", f"非法元素 id: {candidate!r}")
        return candidate
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return f"agent-{digest}"


def place_on_canvas(
    project_id: str,
    artifact: Mapping[str, Any],
    position: Optional[Mapping[str, Any]] = None,
    element_id: Optional[str] = None,
    operation_id: Optional[str] = None,
) -> Dict[str, Any]:
    with project_lock(project_id):
        doc = load_document(project_id)
        asset_id = str(artifact.get("asset_id") or "").strip()
        if not asset_id:
            raise StoreError("invalid_arguments", "artifact.asset_id 不能为空")
        asset = _require_asset(doc, asset_id)
        key = str(artifact.get("key") or asset_id)
        eid = _normalise_element_id(element_id, key)

        cached = _replay_operation(project_id, operation_id, "place_on_canvas", eid)
        if cached is not None:
            element = (doc.get("elements") or {}).get(eid)
            if element is not None:
                result = copy.deepcopy(cached.get("result") or {})
                result["element"] = element_summary(element)
                result["idempotent"] = True
                return result

        elements = doc.setdefault("elements", {})
        if not isinstance(elements, dict):
            raise StoreError("project_document_invalid", "elements 不是对象")
        existing = elements.get(eid)
        if existing is not None:
            if existing.get("assetId") == asset_id:
                return {
                    "element": element_summary(existing),
                    "created": False,
                    "idempotent": True,
                    "revision": int(doc.get("revision", 0)),
                }
            raise StoreError(
                "element_conflict",
                f"元素 {eid} 已存在且指向另一个 asset",
                {"element_id": eid, "existing_asset_id": existing.get("assetId")},
            )

        width = _dimension(artifact.get("width"), asset.get("width"), 512)
        height = _dimension(artifact.get("height"), asset.get("height"), _default_height(asset))
        x, y = _resolve_position(position, width, height)
        next_z = max((float(item.get("z", 0)) for item in elements.values()), default=0) + 1

        element = {
            "assetId": asset_id,
            "height": height,
            "hidden": False,
            "id": eid,
            "locked": False,
            "rotation": 0,
            "type": asset.get("kind") or "image",
            "versionId": asset.get("versionId") or "",
            "width": width,
            "x": x,
            "y": y,
            "z": next_z,
        }
        label = str(artifact.get("label") or "").strip()
        if label:
            element["label"] = label[:80]

        snapshot = _snapshot_canvas(doc)
        elements[eid] = element
        doc["revision"] = int(doc.get("revision", 0)) + 1
        doc["updatedAt"] = time.time()
        _save_document(project_id, doc)

        stack = _load_stack(project_id)
        stack.append({"capability": "place_on_canvas", "document": snapshot, "revision": snapshot["revision"]})
        _save_stack(project_id, stack)
        _append_activity(project_id, "place_on_canvas", doc["revision"], {"element_id": eid, "asset_id": asset_id})

        result = {
            "element": element_summary(element),
            "created": True,
            "idempotent": False,
            "revision": doc["revision"],
        }
        if operation_id:
            _remember_operation(project_id, operation_id, "place_on_canvas", eid, result)
        return result


def update_artifact(
    project_id: str,
    element_id: str,
    asset_id: Optional[str] = None,
    patch: Optional[Mapping[str, Any]] = None,
    instruction: Optional[str] = None,
    operation_id: Optional[str] = None,
) -> Dict[str, Any]:
    with project_lock(project_id):
        doc = load_document(project_id)
        elements = doc.get("elements") if isinstance(doc.get("elements"), dict) else {}
        element = elements.get(element_id)
        if not isinstance(element, dict):
            raise StoreError("element_not_found", f"元素不存在: {element_id}", {"element_id": element_id})
        if element.get("locked"):
            raise StoreError("element_locked", f"元素已锁定: {element_id}", {"element_id": element_id})

        patch = dict(patch or {})
        if instruction and not asset_id:
            raise StoreError(
                "generation_required",
                "M0 不做真实重生成：给 asset_id 做原地替换；instruction 触发的生成在 M1 接入",
                {"element_id": element_id, "instruction": instruction},
            )
        if not asset_id and not patch:
            raise StoreError("invalid_arguments", "update_artifact 需要 asset_id 或 patch 至少一项")

        cached = _replay_operation(project_id, operation_id, "update_artifact", element_id)
        if cached is not None:
            result = copy.deepcopy(cached.get("result") or {})
            result["element"] = element_summary(element)
            result["idempotent"] = True
            return result

        target = copy.deepcopy(element)
        if asset_id:
            asset = _require_asset(doc, asset_id)
            target["assetId"] = asset_id
            target["versionId"] = asset.get("versionId") or ""
            target["type"] = asset.get("kind") or target.get("type") or "image"
        for field, value in patch.items():
            target[field] = value

        if target == element:
            return {
                "element": element_summary(element),
                "changed": False,
                "idempotent": True,
                "revision": int(doc.get("revision", 0)),
            }

        snapshot = _snapshot_canvas(doc)
        elements[element_id] = target
        doc["revision"] = int(doc.get("revision", 0)) + 1
        doc["updatedAt"] = time.time()
        _save_document(project_id, doc)

        stack = _load_stack(project_id)
        stack.append({"capability": "update_artifact", "document": snapshot, "revision": snapshot["revision"]})
        _save_stack(project_id, stack)
        _append_activity(
            project_id,
            "update_artifact",
            doc["revision"],
            {"element_id": element_id, "asset_id": asset_id, "patch": patch},
        )

        result = {
            "element": element_summary(target),
            "changed": True,
            "idempotent": False,
            "revision": doc["revision"],
        }
        if operation_id:
            _remember_operation(project_id, operation_id, "update_artifact", element_id, result)
        return result


def undo(project_id: str) -> Dict[str, Any]:
    with project_lock(project_id):
        doc = load_document(project_id)
        stack = _load_stack(project_id)
        if not stack:
            return {
                "undone": False,
                "revision": int(doc.get("revision", 0)),
                "restored_revision": None,
            }

        entry = stack.pop()
        snapshot = entry.get("document") if isinstance(entry, dict) else None
        if not isinstance(snapshot, dict):
            raise StoreError("undo_snapshot_invalid", "undo 快照损坏")

        for field in CANVAS_FIELDS:
            if field in snapshot:
                doc[field] = copy.deepcopy(snapshot[field])
        doc["revision"] = max(int(doc.get("revision", 0)), int(snapshot.get("revision", 0))) + 1
        doc["updatedAt"] = time.time()
        _save_document(project_id, doc)
        _save_stack(project_id, stack)
        _append_activity(project_id, "undo", doc["revision"], {"restored_revision": snapshot.get("revision")})

        return {
            "undone": True,
            "revision": doc["revision"],
            "restored_revision": int(snapshot.get("revision", 0)),
        }


def _dimension(explicit: Any, asset_value: Any, fallback: int) -> int:
    for value in (explicit, asset_value):
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return int(fallback)


def _default_height(asset: Mapping[str, Any]) -> int:
    width = asset.get("width")
    if isinstance(width, (int, float)) and width > 0:
        return int(width * (9 / 16 if asset.get("kind") == "video" else 1))
    return 512


def _resolve_position(position: Optional[Mapping[str, Any]], width: int, height: int) -> tuple[float, float]:
    position = dict(position or {})
    has_x = "x" in position
    has_y = "y" in position
    if has_x != has_y:
        raise StoreError("invalid_position", "position 必须同时给 x 和 y，或只给 anchor")
    if has_x:
        x, y = position["x"], position["y"]
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise StoreError("invalid_position", "position.x / position.y 必须是数字")
        return float(x), float(y)
    anchor = position.get("anchor") or "canvas-origin"
    if anchor != "canvas-origin":
        raise StoreError("invalid_position", f"未知 anchor: {anchor!r}")
    return -width / 2, -height / 2
