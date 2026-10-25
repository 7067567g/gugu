"""Capability executor — one envelope, one validation path, no fake success.

``execute`` is the only entry point the agent tool and tests use. It validates
arguments against the registry schema, enforces approval, and refuses to run a
``planned`` capability instead of returning a plausible-looking fake result.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Mapping, Optional

from . import registry, schema, store

logger = logging.getLogger(__name__)


def _ok(capability: str, data: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "ok": True,
        "capability": capability,
        "data": dict(data),
        "meta": {"schema_version": registry.SCHEMA_VERSION},
    }


def _error(capability: str, code: str, message: str, details: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = dict(details)
    return {"ok": False, "capability": capability, "error": error}


def execute(capability_id: str, arguments: Optional[Mapping[str, Any]] = None, *, approved: bool = False) -> Dict[str, Any]:
    """Run one capability and return the shared envelope."""
    capability = registry.get_capability(str(capability_id))
    if capability is None:
        return _error(
            str(capability_id),
            "unknown_capability",
            f"未知能力: {capability_id!r}",
            {"available": list(registry.CAPABILITY_IDS)},
        )

    args: Mapping[str, Any] = arguments or {}
    if not isinstance(args, Mapping):
        return _error(capability_id, "invalid_arguments", "arguments 必须是对象")

    argument_errors = schema.validate(args, capability["input_schema"])
    if argument_errors:
        return _error(
            capability_id,
            "invalid_arguments",
            "参数不符合能力 schema",
            {"errors": argument_errors},
        )

    if capability["requires_approval"] and not approved:
        return _error(
            capability_id,
            "approval_required",
            "该能力需要用户审批后才能执行",
            {"milestone": capability["milestone"]},
        )

    if capability["status"] != "ready":
        return _error(
            capability_id,
            "not_implemented",
            f"{capability_id} 在 {capability['milestone']} 实现；M0 不返回假结果",
            {"status": capability["status"], "milestone": capability["milestone"]},
        )

    handler = _HANDLERS.get(capability_id)
    if handler is None:  # registry says ready but no handler -> contract bug, fail loudly.
        logger.error("capability %s is ready but has no handler", capability_id)
        return _error(capability_id, "internal_error", f"能力 {capability_id} 未绑定处理器")

    try:
        data = handler(args)
    except store.StoreError as exc:
        return _error(capability_id, exc.code, exc.message, exc.details)
    except Exception as exc:  # noqa: BLE001 — tool surface must return an envelope, never crash the turn
        logger.exception("capability %s failed", capability_id)
        return _error(capability_id, "internal_error", f"{capability_id} 执行失败: {exc}")

    return _ok(capability_id, data)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _list_capabilities(_args: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": registry.SCHEMA_VERSION,
        "capabilities": registry.list_capabilities(),
    }


def _read_state(args: Mapping[str, Any]) -> Dict[str, Any]:
    requested = set(args.get("scope") or ["surface", "project", "canvas", "selection"])
    runtime = store.read_runtime_state() or {}
    explicit_project = str(args.get("project_id") or "").strip()
    project_id = explicit_project or str(runtime.get("project_id") or "").strip()

    surface: Optional[Dict[str, Any]] = None
    if runtime and "surface" in requested:
        surface = {
            "surface": runtime.get("surface"),
            "route": runtime.get("route"),
            "updated_at": runtime.get("updated_at"),
        }

    project: Optional[Dict[str, Any]] = None
    canvas: Optional[Dict[str, Any]] = None
    doc: Optional[Dict[str, Any]] = None
    if project_id:
        try:
            doc = store.read_document(project_id)
        except store.StoreError:
            if explicit_project:
                raise
            project_id = ""
        if doc is not None:
            if "project" in requested:
                project = store.project_summary(doc)
            if "canvas" in requested:
                canvas = store.canvas_summary(doc)

    selection = {"ids": [], "elements": []}
    if "selection" in requested:
        ids = runtime.get("selection")
        if isinstance(ids, list):
            selection["ids"] = [item for item in ids if isinstance(item, str)]
        if doc is not None:
            elements = doc.get("elements") if isinstance(doc.get("elements"), dict) else {}
            selection["elements"] = [
                store.element_summary(elements[item]) for item in selection["ids"] if isinstance(elements.get(item), dict)
            ]

    return {
        "surface": surface,
        "project": project,
        "canvas": canvas,
        "selection": selection,
    }


def _place_on_canvas(args: Mapping[str, Any]) -> Dict[str, Any]:
    return store.place_on_canvas(
        project_id=str(args["project_id"]),
        artifact=dict(args["artifact"]),
        position=args.get("position"),
        element_id=args.get("element_id"),
        operation_id=args.get("operation_id"),
    )


def _update_artifact(args: Mapping[str, Any]) -> Dict[str, Any]:
    return store.update_artifact(
        project_id=str(args["project_id"]),
        element_id=str(args["element_id"]),
        asset_id=args.get("asset_id"),
        patch=args.get("patch"),
        instruction=args.get("instruction"),
        operation_id=args.get("operation_id"),
    )


def _undo(args: Mapping[str, Any]) -> Dict[str, Any]:
    return store.undo(str(args["project_id"]))


_HANDLERS: Dict[str, Callable[[Mapping[str, Any]], Dict[str, Any]]] = {
    "list_capabilities": _list_capabilities,
    "read_state": _read_state,
    "place_on_canvas": _place_on_canvas,
    "update_artifact": _update_artifact,
    "undo": _undo,
}
