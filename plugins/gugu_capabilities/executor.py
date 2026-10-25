"""Capability executor — one envelope, one validation path, no fake success.

``execute`` is the only entry point the agent tool and tests use. It validates
arguments against the registry schema, enforces approval, and refuses to run a
``planned`` capability instead of returning a plausible-looking fake result.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, Mapping, Optional

from . import assets as _assets, diagram, export, images, registry, schema, store

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
    except (diagram.DiagramError, images.ImageError) as exc:
        details: Dict[str, Any] = {}
        attempts = getattr(exc, "attempts", None)
        if attempts:
            details["attempts"] = attempts
        return _error(capability_id, "generation_failed", str(exc), details)
    except export.ExportError as exc:
        return _error(capability_id, "export_failed", str(exc))
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


def _resolve_project(args: Mapping[str, Any]) -> str:
    explicit = str(args.get("project_id") or "").strip()
    if explicit:
        return explicit
    runtime = store.read_runtime_state() or {}
    project_id = str(runtime.get("project_id") or "").strip()
    if not project_id:
        raise store.StoreError(
            "project_required",
            "该能力需要一个工程：传 project_id，或先让桌面渲染端发布 agent-state",
        )
    return project_id


def _slug(value: str, fallback: str = "artifact") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return (text[:48] or fallback).lower()


def _generate_diagram(args: Mapping[str, Any]) -> Dict[str, Any]:
    project_id = _resolve_project(args)
    source = str(args["source"])
    source_kind = str(args.get("source_kind") or "repo")
    style = str(args.get("style") or "")
    try:
        built = diagram.build_diagram(source, source_kind, style)
    except diagram.DiagramError:
        try:
            outline = diagram.source_to_outline(source, source_kind)
        except diagram.DiagramError:
            # Last rung: keep whatever the caller gave us rather than fail hard.
            outline = {"title": "Outline", "outline": [source.strip()[:400] or "empty source"]}
        text = diagram.render_text_outline(outline["title"], outline["outline"])
        stored = store.write_text_artifact(project_id, text, name="diagram-outline")
        artifact = {
            "kind": "diagram-text",
            "format": "text/markdown",
            "name": f"{outline['title']}.md",
            "path": stored["path"],
            "sha256": stored["sha256"],
            "bytes": stored["bytes"],
            "fallback": "text",
        }
        return {"artifact": artifact, "mermaid": "", "svg": "", "format": "text/markdown", "fallback": "text"}

    title = built["title"]
    asset = store.register_asset(
        project_id,
        built["svg"].encode("utf-8"),
        "image/svg+xml",
        f"{_slug(title, 'architecture')}.svg",
        {
            "source": "diagram",
            "fallback": built["fallback"],
            "diagram": {"mermaid": built["mermaid"] or built["mermaid_attempt"], "digest": built["digest"]},
        },
    )
    artifact = {
        "asset_id": asset["id"],
        "kind": "diagram",
        "format": "svg",
        "name": f"{title}.svg",
        "media_type": "image/svg+xml",
        "width": asset.get("width"),
        "height": asset.get("height"),
        "fallback": built["fallback"],
        "digest": built["digest"],
    }
    return {
        "artifact": artifact,
        "mermaid": built["mermaid"] or built["mermaid_attempt"],
        "svg": built["svg"],
        "format": "svg",
        "fallback": built["fallback"],
    }


def _generate_image(args: Mapping[str, Any]) -> Dict[str, Any]:
    project_id = _resolve_project(args)
    prompt = str(args["prompt"])
    label = str(args.get("label") or "").strip()
    result = images.generate_with_fallback(
        prompt,
        model=str(args.get("model") or ""),
        size=str(args.get("size") or ""),
        label=label,
    )
    mock = bool(result.get("mock"))
    suffix = _assets.media_extension(str(result["media_type"]))
    name = f"{'MOCK-' if mock else ''}{_slug(label or prompt, 'image')}.{suffix}"
    asset = store.register_asset(
        project_id,
        result["data"],
        str(result["media_type"]),
        name,
        images.image_metadata(result, prompt, source="image"),
    )
    artifact = {
        "asset_id": asset["id"],
        "kind": "image",
        "name": name,
        "media_type": asset.get("mediaType"),
        "width": asset.get("width"),
        "height": asset.get("height"),
        "model": result.get("model"),
        "mock": mock,
    }
    return {
        "artifact": artifact,
        "model": result.get("model"),
        "mock": mock,
        "attempts": result.get("attempts") or [],
    }


def _update_artifact(args: Mapping[str, Any]) -> Dict[str, Any]:
    project_id = str(args["project_id"])
    element_id = str(args["element_id"])
    asset_id = args.get("asset_id")
    instruction = str(args.get("instruction") or "").strip()
    regenerated = False
    model = ""
    mock = False
    if instruction and not asset_id:
        detail = store.element_with_asset(project_id, element_id)
        current_asset = detail.get("asset") or {}
        element = detail.get("element") or {}
        base_prompt = str(current_asset.get("prompt") or element.get("label") or current_asset.get("name") or "canvas illustration")
        warm = any(token in instruction.lower() for token in ("warm", "暖", "cozy", "orange"))
        combined_prompt = f"{base_prompt}\nRevision instruction: {instruction}"
        result = images.generate_with_fallback(
            combined_prompt,
            size=str(current_asset.get("size") or "1024x1024"),
            label=str(element.get("label") or ""),
            warm=warm if warm else None,
        )
        model = str(result.get("model") or "")
        mock = bool(result.get("mock"))
        suffix = _assets.media_extension(str(result["media_type"]))
        name = f"{'MOCK-' if mock else ''}{_slug(element.get('label') or 'corrected', 'corrected')}.{suffix}"
        asset = store.register_asset(
            project_id,
            result["data"],
            str(result["media_type"]),
            name,
            images.image_metadata(result, combined_prompt, source="image-correction", correction=instruction),
        )
        asset_id = asset["id"]
        regenerated = True

    data = store.update_artifact(
        project_id=project_id,
        element_id=element_id,
        asset_id=asset_id,
        patch=args.get("patch"),
        instruction=instruction if not regenerated else None,
        operation_id=args.get("operation_id"),
    )
    if regenerated:
        data = dict(data)
        data.update({"regenerated": True, "model": model, "mock": mock})
    return data


def _export_canvas(args: Mapping[str, Any]) -> Dict[str, Any]:
    return export.export_project(str(args["project_id"]), str(args.get("format") or "zip"))


def _undo(args: Mapping[str, Any]) -> Dict[str, Any]:
    return store.undo(str(args["project_id"]))


_HANDLERS: Dict[str, Callable[[Mapping[str, Any]], Dict[str, Any]]] = {
    "list_capabilities": _list_capabilities,
    "read_state": _read_state,
    "generate_diagram": _generate_diagram,
    "generate_image": _generate_image,
    "place_on_canvas": _place_on_canvas,
    "update_artifact": _update_artifact,
    "export_canvas": _export_canvas,
    "undo": _undo,
}
