"""gugu M0 capability surface — the agent's one door into the app.

The tool is intentionally ONE entry (``gugu_capability``) rather than ten core
tools: the model discovers the surface with ``list_capabilities`` and invokes a
capability by id, so adding a capability never grows the core tool schema paid
for on every API call. Handlers live in ``executor.py``; the contract and UI
anchors live in ``registry.py``.

Agent-callable today (M2): ``list_capabilities`` (includes the anchor inventory)
/ ``read_state`` / ``generate_diagram`` (Mermaid canonical -> SVG, with
outline/text fallback) / ``generate_image`` (primary -> fallback -> explicit
mock) / ``place_on_canvas`` / ``update_artifact`` (including one-sentence
in-place regeneration) / ``export_canvas`` / ``undo`` / ``highlight`` /
``clear_guidance``.
Registered but deliberately deferred: ``navigate`` (routing slice). It returns
``not_implemented`` — this plugin never fakes a generation or a guidance
overlay.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Mapping, Optional

from . import executor, registry

logger = logging.getLogger(__name__)

TOOLSET = "gugu-capabilities"
TOOL_NAME = "gugu_capability"

SCHEMA: Dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "咕咕 agent-native 能力入口（单工具、按 capability 分派）。"
        "先 capability='list_capabilities' 枚举能力与参数 schema，再 capability='read_state' "
        "读取当前界面/项目/画布/选择。画布写入用 place_on_canvas / update_artifact（原地替换）/ undo。"
        "生成用 generate_diagram（Mermaid→SVG，失败降级 outline/文本）与 generate_image"
        "（主/备/显式 mock，mock=true 时不得当作真实生成）。导出用 export_canvas。"
        "引导用 highlight（只指 list_capabilities.anchors 里 targetable=true 的锚点）"
        "与 clear_guidance（幂等清空）；navigate 仍为 planned。"
        "需要审批的能力返回 approval_required；不要用本工具伪造生成结果。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "capability": {
                "type": "string",
                "enum": list(registry.CAPABILITY_IDS),
                "description": "能力 id；见 list_capabilities",
            },
            "arguments": {
                "type": "object",
                "description": "该能力的输入参数，形状见 list_capabilities 返回的 input_schema",
            },
        },
        "required": ["capability"],
        "additionalProperties": False,
    },
}


def handle_capability(arguments: Mapping[str, Any], session_id: Optional[str] = None, **_kwargs: Any) -> str:
    """Tool handler: always returns a JSON envelope string."""
    payload = dict(arguments) if isinstance(arguments, Mapping) else {}
    capability_id = str(payload.get("capability") or "")
    capability_args = payload.get("arguments") or {}
    result = executor.execute(capability_id, capability_args)
    return json.dumps(result, ensure_ascii=False)


def register(ctx: Any) -> None:
    """Plugin entry point — register the single capability tool."""
    problems = registry.validate_registry()
    if problems:
        # Fail visible in logs without taking the whole agent startup down; the
        # test suite treats any problem as a hard failure.
        logger.error("gugu capability registry is invalid: %s", "; ".join(problems))

    ctx.register_tool(
        name=TOOL_NAME,
        toolset=TOOLSET,
        schema=SCHEMA,
        handler=handle_capability,
        check_fn=lambda: True,
        description="咕咕 agent-native 能力入口（能力/锚点清单、状态、生成、画布读写、原地修正、导出、引导、undo）",
        emoji="🧩",
    )
