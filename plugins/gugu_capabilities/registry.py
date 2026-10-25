"""M0 capability registry — the single, machine-checkable contract.

Every capability declares the seven things the product plan requires:
``id / semantics / input_schema / output_schema / side_effects /
requires_approval / ui_anchors`` plus examples. ``status`` separates what M0
actually executes (``ready``) from what is deliberately deferred (``planned``);
a planned capability must never pretend to work — the executor returns
``not_implemented`` with its milestone.

The registry is data, not code paths: handlers live in ``executor.py`` and the
UI anchor registry lives here so the desktop and the agent share one vocabulary.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, Iterable, Mapping, Tuple

from . import schema as _schema

SCHEMA_VERSION = "gugu.capability-registry/v1"

# ---------------------------------------------------------------------------
# UI anchors: `data-agent-id="{surface}.{element}"` in apps/desktop.
# "present" anchors exist today (M0 adds the missing ones in the gugu-design
# surface); "planned" anchors are reserved for M1/M2 and may not be targeted by
# a ready capability yet.
# ---------------------------------------------------------------------------

ANCHORS: Dict[str, Dict[str, str]] = {
    "design.projects": {"surface": "design", "status": "present", "description": "工程列表/新建区"},
    "design.projects.name": {"surface": "design", "status": "present", "description": "新工程名输入框"},
    "design.projects.create": {"surface": "design", "status": "present", "description": "新建工程按钮"},
    "design.workspace": {"surface": "design", "status": "present", "description": "设计工作区根"},
    "design.canvas": {"surface": "design", "status": "present", "description": "Konva 画布容器"},
    "design.canvas.undo": {"surface": "design", "status": "present", "description": "画布撤销按钮"},
    "design.canvas.redo": {"surface": "design", "status": "present", "description": "画布重做按钮"},
    "design.canvas.fit": {"surface": "design", "status": "present", "description": "适应视图按钮"},
    "design.canvas.upload": {"surface": "design", "status": "present", "description": "导入素材按钮"},
    "design.canvas.hide": {"surface": "design", "status": "present", "description": "隐藏所选按钮"},
    "design.panel": {"surface": "design", "status": "present", "description": "右侧生成面板"},
    "design.panel.jobs": {"surface": "design", "status": "present", "description": "生成任务列表"},
    "design.composer": {"surface": "design", "status": "present", "description": "现有生成输入框（全局 AI 入口复用）"},
    "design.composer.kind": {"surface": "design", "status": "present", "description": "图像/视频切换"},
    "design.composer.send": {"surface": "design", "status": "present", "description": "提交生成按钮"},
    "guidance.overlay": {"surface": "guidance", "status": "present", "description": "引导层 overlay 根（active 时挂到 body；不可作为高亮目标）"},
    "canvas.export": {"surface": "canvas", "status": "present", "description": "画布导出按钮（M1）"},
}

CAPABILITY_IDS: Tuple[str, ...] = (
    "navigate",
    "read_state",
    "list_capabilities",
    "generate_diagram",
    "generate_image",
    "place_on_canvas",
    "update_artifact",
    "highlight",
    "clear_guidance",
    "undo",
    "export_canvas",
)

_OBJECT = "object"
_REQUIRED_ENTRY_FIELDS = (
    "id",
    "semantics",
    "input_schema",
    "output_schema",
    "side_effects",
    "requires_approval",
    "ui_anchors",
    "examples",
    "status",
    "milestone",
)

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


def _cap(
    *,
    cid: str,
    semantics: str,
    input_schema: Mapping[str, Any],
    output_schema: Mapping[str, Any],
    side_effects: Iterable[str],
    requires_approval: bool,
    ui_anchors: Iterable[str],
    examples: Iterable[Mapping[str, Any]],
    status: str,
    milestone: str,
    notes: str = "",
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "id": cid,
        "semantics": semantics,
        "input_schema": dict(input_schema),
        "output_schema": dict(output_schema),
        "side_effects": list(side_effects),
        "requires_approval": requires_approval,
        "ui_anchors": list(ui_anchors),
        "examples": [dict(item) for item in examples],
        "status": status,
        "milestone": milestone,
    }
    if notes:
        entry["notes"] = notes
    return entry


_EMPTY_INPUT = {"type": _OBJECT, "properties": {}, "additionalProperties": False}

CAPABILITIES: Tuple[Dict[str, Any], ...] = (
    _cap(
        cid="navigate",
        semantics="把用户带到指定界面；返回实际落点，供 Agent 继续 read_state。",
        input_schema={
            "type": _OBJECT,
            "properties": {
                "screen": {"type": "string", "enum": ["work", "design", "settings", "artifacts"]},
                "query": {"type": "string", "minLength": 1, "description": "可选的界面内定位参数"},
            },
            "required": ["screen"],
            "additionalProperties": False,
        },
        output_schema={
            "type": _OBJECT,
            "properties": {
                "screen": {"type": "string"},
                "navigated": {"type": "boolean"},
            },
            "required": ["screen", "navigated"],
        },
        side_effects=["切换当前界面路由", "关闭当前临时浮层"],
        requires_approval=False,
        ui_anchors=["design.workspace"],
        examples=[{"description": "从任意界面打开设计画布", "arguments": {"screen": "design"}}],
        status="planned",
        milestone="M0",
        notes="M0 只登记契约；真实导航与模式切换在 M1 做（当前桌面渲染层没有跨进程导航通道）。",
    ),
    _cap(
        cid="read_state",
        semantics="读取当前界面 / 项目 / 画布 / 选择状态，是 Agent 探索软件的只读入口。",
        input_schema={
            "type": _OBJECT,
            "properties": {
                "scope": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["surface", "project", "canvas", "selection"]},
                    "minItems": 1,
                },
                "project_id": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": _OBJECT,
            "properties": {
                "surface": {"type": ["object", "null"]},
                "project": {"type": ["object", "null"]},
                "canvas": {"type": ["object", "null"]},
                "selection": {"type": _OBJECT},
            },
            "required": ["surface", "project", "canvas", "selection"],
        },
        side_effects=[],
        requires_approval=False,
        ui_anchors=["design.workspace"],
        examples=[
            {"description": "读全部状态", "arguments": {"scope": ["surface", "project", "canvas", "selection"]}},
            {"description": "只读画布摘要", "arguments": {"scope": ["canvas"], "project_id": "proj-1"}},
        ],
        status="ready",
        milestone="M0",
    ),
    _cap(
        cid="list_capabilities",
        semantics="枚举这个 App 当前能做什么：能力 id、schema、副作用、审批要求与可指锚点。",
        input_schema=_EMPTY_INPUT,
        output_schema={
            "type": _OBJECT,
            "properties": {
                "schema_version": {"type": "string"},
                "capabilities": {"type": "array", "items": {"type": _OBJECT}},
                "anchors": {"type": "array", "items": {"type": _OBJECT}},
            },
            "required": ["schema_version", "capabilities", "anchors"],
        },
        side_effects=[],
        requires_approval=False,
        ui_anchors=[],
        examples=[{"description": "获取能力清单", "arguments": {}}],
        status="ready",
        milestone="M0",
    ),
    _cap(
        cid="generate_diagram",
        semantics="把 repo/README/大纲转换成架构图（D-P0-01：Mermaid 源 canonical，渲染 SVG 落画布）。",
        input_schema={
            "type": _OBJECT,
            "properties": {
                "source": {"type": "string", "minLength": 1, "description": "repo 路径、README 正文或大纲"},
                "source_kind": {"type": "string", "enum": ["repo", "readme", "outline"]},
                "style": {"type": "string", "minLength": 1},
                "project_id": {"type": "string", "minLength": 1, "description": "落资产的目标工程；缺省读运行时工程"},
            },
            "required": ["source"],
            "additionalProperties": False,
        },
        output_schema={
            "type": _OBJECT,
            "properties": {
                "artifact": {"type": _OBJECT},
                "mermaid": {"type": "string"},
                "svg": {"type": "string"},
                "format": {"type": "string", "enum": ["svg", "text/markdown"]},
                "fallback": {"type": ["string", "null"]},
            },
            "required": ["artifact"],
        },
        side_effects=["生成新 artifact", "不自动落到画布（需再调 place_on_canvas）"],
        requires_approval=True,
        ui_anchors=["design.panel", "design.canvas"],
        examples=[{"description": "README 生成架构图", "arguments": {"source": "README.md", "source_kind": "repo"}}],
        status="ready",
        milestone="M1",
        notes="M1：Mermaid 文本为 canonical，渲染 SVG 注册为画布 asset；失败按 outline SVG → 文本大纲降级，不假成功。",
    ),
    _cap(
        cid="generate_image",
        semantics="按提示词生成配图（D-P0-02：qwen-image-2.0 主 / gpt-image-2-medium 备 / mock 兜底并显式标注）。",
        input_schema={
            "type": _OBJECT,
            "properties": {
                "prompt": {"type": "string", "minLength": 1},
                "refs": {"type": "array", "items": {"type": "string"}},
                "model": {"type": "string", "minLength": 1},
                "size": {"type": "string", "enum": ["1024x1024", "1536x1024", "1024x1536"]},
                "project_id": {"type": "string", "minLength": 1, "description": "落资产的目标工程；缺省读运行时工程"},
                "label": {"type": "string", "minLength": 1, "maxLength": 80},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
        output_schema={
            "type": _OBJECT,
            "properties": {
                "artifact": {"type": _OBJECT},
                "model": {"type": "string"},
                "mock": {"type": "boolean"},
                "attempts": {"type": "array", "items": {"type": _OBJECT}},
            },
            "required": ["artifact", "model", "mock"],
        },
        side_effects=["调用图像网关或显式 mock", "产生新 asset，不自动落到画布"],
        requires_approval=True,
        ui_anchors=["design.composer", "design.composer.send"],
        examples=[{"description": "生成 1024 方图", "arguments": {"prompt": "warm architectural diagram, paper texture"}}],
        status="ready",
        milestone="M1",
        notes="M1：主/备模型名走 env（GUGU_IMAGE_MODEL_PRIMARY/FALLBACK）；离线非生产可显式 mock，mock=true 且文件带水印。",
    ),
    _cap(
        cid="place_on_canvas",
        semantics="把一个已有 artifact 放到画布指定位置；同一 artifact key 重复调用不产生副本。",
        input_schema={
            "type": _OBJECT,
            "properties": {
                "project_id": {"type": "string", "minLength": 1},
                "artifact": {
                    "type": _OBJECT,
                    "properties": {
                        "asset_id": {"type": "string", "minLength": 1},
                        "key": {"type": "string", "minLength": 1, "description": "稳定身份；缺省用 asset_id"},
                        "width": {"type": "integer", "minimum": 1},
                        "height": {"type": "integer", "minimum": 1},
                        "label": {"type": "string", "maxLength": 80},
                    },
                    "required": ["asset_id"],
                    "additionalProperties": False,
                },
                "position": {
                    "type": _OBJECT,
                    "properties": {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "anchor": {"type": "string", "enum": ["canvas-origin"]},
                    },
                    "additionalProperties": False,
                },
                "element_id": {"type": "string", "minLength": 1},
                "operation_id": {"type": "string", "minLength": 1},
            },
            "required": ["project_id", "artifact"],
            "additionalProperties": False,
        },
        output_schema={
            "type": _OBJECT,
            "properties": {
                "element": {"type": _OBJECT},
                "created": {"type": "boolean"},
                "idempotent": {"type": "boolean"},
                "revision": {"type": "integer"},
            },
            "required": ["element", "created", "idempotent", "revision"],
        },
        side_effects=["画布 elements 新增一个节点", "文档 revision +1", "写 agent 活动日志与 undo 快照"],
        requires_approval=False,
        ui_anchors=["design.canvas"],
        examples=[
            {
                "description": "把 asset 放到 (100, 80)",
                "arguments": {
                    "project_id": "proj-1",
                    "artifact": {"asset_id": "0123456789abcdef", "key": "diagram-main"},
                    "position": {"x": 100, "y": 80},
                },
            }
        ],
        status="ready",
        milestone="M0",
    ),
    _cap(
        cid="update_artifact",
        semantics="原地替换画布元素指向的 artifact / 几何 / 标签，不新增副本；instruction 走重生成后原地换图。",
        input_schema={
            "type": _OBJECT,
            "properties": {
                "project_id": {"type": "string", "minLength": 1},
                "element_id": {"type": "string", "minLength": 1},
                "asset_id": {"type": "string", "minLength": 1},
                "patch": {
                    "type": _OBJECT,
                    "properties": {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "width": {"type": "number", "minimum": 1},
                        "height": {"type": "number", "minimum": 1},
                        "rotation": {"type": "number"},
                        "label": {"type": "string", "maxLength": 80},
                    },
                    "additionalProperties": False,
                },
                "instruction": {"type": "string", "minLength": 1},
                "operation_id": {"type": "string", "minLength": 1},
            },
            "required": ["project_id", "element_id"],
            "additionalProperties": False,
        },
        output_schema={
            "type": _OBJECT,
            "properties": {
                "element": {"type": _OBJECT},
                "changed": {"type": "boolean"},
                "idempotent": {"type": "boolean"},
                "revision": {"type": "integer"},
                "regenerated": {"type": "boolean"},
                "model": {"type": "string"},
                "mock": {"type": "boolean"},
            },
            "required": ["element", "changed", "idempotent", "revision"],
        },
        side_effects=["原地改画布元素，不新增节点", "文档 revision +1", "写 undo 快照"],
        requires_approval=False,
        ui_anchors=["design.canvas"],
        examples=[
            {
                "description": "把元素换成另一张图（原地）",
                "arguments": {
                    "project_id": "proj-1",
                    "element_id": "agent-abc123",
                    "asset_id": "fedcba9876543210",
                },
            }
        ],
        status="ready",
        milestone="M1",
        notes="M0 做 asset/几何替换；M1 的 instruction 在 executor 层先重生成新 asset，再原地替换同一 element_id（不新增副本）。",
    ),
    _cap(
        cid="export_canvas",
        semantics="把当前画布导出为自包含 zip（project.json + assets + preview.svg + manifest 校验和）。",
        input_schema={
            "type": _OBJECT,
            "properties": {
                "project_id": {"type": "string", "minLength": 1},
                "format": {"type": "string", "enum": ["zip"]},
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
        output_schema={
            "type": _OBJECT,
            "properties": {
                "format": {"type": "string"},
                "path": {"type": "string"},
                "filename": {"type": "string"},
                "bytes": {"type": "integer"},
                "sha256": {"type": "string"},
                "revision": {"type": "integer"},
                "element_count": {"type": "integer"},
                "mock_assets": {"type": "integer"},
            },
            "required": ["format", "path", "filename", "bytes", "sha256", "revision"],
        },
        side_effects=["在工程 exports/ 写一个 zip", "不改文档 revision"],
        requires_approval=False,
        ui_anchors=["canvas.export"],
        examples=[{"description": "导出当前画布", "arguments": {"project_id": "proj-1", "format": "zip"}}],
        status="ready",
        milestone="M1",
    ),
    _cap(
        cid="highlight",
        semantics="引导：高亮/图框/说明一个已登记锚点，可被用户随时中断（M2）。",
        input_schema={
            "type": _OBJECT,
            "properties": {
                "target_anchor": {"type": "string", "minLength": 1},
                "text": {"type": "string", "maxLength": 280},
                "style": {"type": "string", "enum": ["highlight", "box", "label"]},
                "timeout_ms": {"type": "integer", "minimum": 0, "maximum": 60000},
            },
            "required": ["target_anchor"],
            "additionalProperties": False,
        },
        output_schema={
            "type": _OBJECT,
            "properties": {
                "guidance_id": {"type": "string"},
                "target_anchor": {"type": "string"},
                "style": {"type": "string"},
                "timeout_ms": {"type": "integer"},
            },
            "required": ["guidance_id"],
        },
        side_effects=["渲染引导层", "注册中断监听", "写入 guidance 邮箱（designs/guidance.json）"],
        requires_approval=False,
        ui_anchors=["guidance.overlay"],
        examples=[{"description": "高亮画布区", "arguments": {"target_anchor": "design.canvas", "text": "产出会落在这里"}}],
        status="ready",
        milestone="M2",
        notes="M2：渲染端消费 guidance 邮箱并保证零残留；只接受 present 且非 guidance 自身的锚点。",
    ),
    _cap(
        cid="clear_guidance",
        semantics="立即清除所有引导；必须可重复调用且零残留（M2）。",
        input_schema=_EMPTY_INPUT,
        output_schema={
            "type": _OBJECT,
            "properties": {"cleared": {"type": "boolean"}},
            "required": ["cleared"],
        },
        side_effects=["移除引导 DOM/监听/timer", "恢复焦点与滚动"],
        requires_approval=False,
        ui_anchors=["guidance.overlay"],
        examples=[{"description": "清空引导", "arguments": {}}],
        status="ready",
        milestone="M2",
        notes="M2：删除 guidance 邮箱；渲染端下一次轮询即按 Agent 取消收尾，重复调用返回 cleared=false。",
    ),
    _cap(
        cid="undo",
        semantics="回滚 Agent 的上一动作（M0：画布放置/替换；快照栈，最多 10 步）。",
        input_schema={
            "type": _OBJECT,
            "properties": {"project_id": {"type": "string", "minLength": 1}},
            "required": ["project_id"],
            "additionalProperties": False,
        },
        output_schema={
            "type": _OBJECT,
            "properties": {
                "undone": {"type": "boolean"},
                "revision": {"type": "integer"},
                "restored_revision": {"type": ["integer", "null"]},
            },
            "required": ["undone", "revision", "restored_revision"],
        },
        side_effects=["恢复画布文档快照", "文档 revision +1", "写活动日志"],
        requires_approval=False,
        ui_anchors=["design.canvas.undo"],
        examples=[{"description": "撤销上一次放置", "arguments": {"project_id": "proj-1"}}],
        status="ready",
        milestone="M0",
    ),
)


def get_capability(capability_id: str) -> Dict[str, Any] | None:
    for entry in CAPABILITIES:
        if entry["id"] == capability_id:
            return entry
    return None


def list_capabilities() -> list[Dict[str, Any]]:
    """JSON-safe deep copy, ordered by the canonical id tuple."""
    by_id = {entry["id"]: entry for entry in CAPABILITIES}
    return [copy.deepcopy(by_id[cid]) for cid in CAPABILITY_IDS]


def anchor_registry() -> Dict[str, Dict[str, str]]:
    return copy.deepcopy(ANCHORS)


def list_anchors() -> list[Dict[str, Any]]:
    """Agent-facing anchor inventory: what it can point at, and what it can't.

    ``targetable`` is the guidance contract, not a UI detail: an anchor is
    targetable when its DOM exists today and it is not the guidance overlay
    itself (a guidance that frames its own overlay would never terminate).
    """
    anchors: list[Dict[str, Any]] = []
    for anchor_id in sorted(ANCHORS):
        entry = ANCHORS[anchor_id]
        anchors.append(
            {
                "id": anchor_id,
                "surface": entry["surface"],
                "status": entry["status"],
                "description": entry["description"],
                "targetable": entry["status"] == "present" and entry["surface"] != "guidance",
            }
        )
    return anchors


# ---------------------------------------------------------------------------
# Meta-schema validation (runs in tests and at tool registration time)
# ---------------------------------------------------------------------------


def validate_registry() -> list[str]:
    errors: list[str] = []
    ids = [entry.get("id") for entry in CAPABILITIES]

    if len(CAPABILITIES) != len(CAPABILITY_IDS):
        errors.append(f"registry has {len(CAPABILITIES)} entries, expected {len(CAPABILITY_IDS)}")
    if set(ids) != set(CAPABILITY_IDS):
        errors.append(f"capability ids mismatch: {sorted(ids)!r}")
    if len(ids) != len(set(ids)):
        errors.append(f"duplicate capability ids: {sorted(ids)!r}")

    for entry in CAPABILITIES:
        where = f"capability {entry.get('id')!r}"
        missing = [field for field in _REQUIRED_ENTRY_FIELDS if field not in entry]
        if missing:
            errors.append(f"{where}: missing fields {missing!r}")
            continue
        if not isinstance(entry["id"], str) or _ID_RE.match(entry["id"]) is None:
            errors.append(f"{where}: id must match {_ID_RE.pattern!r}")
        if not isinstance(entry["semantics"], str) or len(entry["semantics"]) < 10:
            errors.append(f"{where}: semantics must be a meaningful string")
        if entry["status"] not in ("ready", "planned"):
            errors.append(f"{where}: status must be ready|planned")
        if entry["milestone"] not in ("M0", "M1", "M2"):
            errors.append(f"{where}: milestone must be M0|M1|M2")
        if not isinstance(entry["requires_approval"], bool):
            errors.append(f"{where}: requires_approval must be bool")
        if not isinstance(entry["side_effects"], list) or not all(isinstance(x, str) for x in entry["side_effects"]):
            errors.append(f"{where}: side_effects must be a list of strings")
        if not isinstance(entry["ui_anchors"], list) or not all(isinstance(x, str) for x in entry["ui_anchors"]):
            errors.append(f"{where}: ui_anchors must be a list of strings")
        for anchor in entry["ui_anchors"]:
            if anchor not in ANCHORS:
                errors.append(f"{where}: unknown ui anchor {anchor!r}")
            elif entry["status"] == "ready" and ANCHORS[anchor]["status"] != "present":
                errors.append(f"{where}: ready capability references planned anchor {anchor!r}")
        for field in ("input_schema", "output_schema"):
            shape_error = _check_schema_definition(entry[field], f"{where}.{field}")
            if shape_error:
                errors.append(shape_error)
        if not isinstance(entry["examples"], list) or not entry["examples"]:
            errors.append(f"{where}: examples must be a non-empty list")
        else:
            for index, example in enumerate(entry["examples"]):
                if not isinstance(example, Mapping) or "arguments" not in example:
                    errors.append(f"{where}.examples[{index}]: needs arguments")
                    continue
                arg_errors = _schema.validate(example["arguments"], entry["input_schema"], path=f"example[{index}]")
                errors.extend(f"{where}: {err}" for err in arg_errors)

    try:
        json.dumps(CAPABILITIES, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        errors.append(f"registry is not JSON-serializable: {exc}")

    return errors


def _check_schema_definition(definition: Any, where: str) -> str:
    if not isinstance(definition, Mapping):
        return f"{where}: schema must be an object"
    if definition.get("type") != _OBJECT:
        return f"{where}: top-level type must be 'object'"
    properties = definition.get("properties")
    if not isinstance(properties, Mapping):
        return f"{where}: properties must be an object"
    required = definition.get("required", [])
    if not isinstance(required, list) or not all(isinstance(name, str) for name in required):
        return f"{where}: required must be a list of strings"
    unknown = [name for name in required if name not in properties]
    if unknown:
        return f"{where}: required names not declared in properties: {unknown!r}"
    try:
        json.dumps(definition, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        return f"{where}: schema is not JSON-serializable: {exc}"
    return ""
