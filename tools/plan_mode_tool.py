"""``exit_plan_mode`` — submit a markdown plan for the user's review.

Always registered (never gated on the plan-mode flag) so the model's tool
catalog is byte-stable across mode toggles: switching plan mode changes only the
injected prompt segment, never the request's tool directory.
"""

from __future__ import annotations

from tools.registry import registry

_SCHEMA = {
    "name": "exit_plan_mode",
    "description": (
        "Leave plan mode by handing the saved markdown implementation plan to the user "
        "for review. 'plan_markdown' must be the full plan and must begin with a "
        "level-1 '#' heading; a plan without one is rejected and plan mode keeps "
        "running. Set 'dismiss' to true to discard the plan and leave plan mode "
        "without submitting one."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "plan_markdown": {
                "type": "string",
                "description": "The complete markdown plan, beginning with a '#' heading.",
            },
            "dismiss": {
                "type": "boolean",
                "description": "Discard the plan and leave plan mode without submitting.",
                "default": False,
            },
        },
        "required": [],
    },
}


def exit_plan_mode_tool(
    plan_markdown: str = "",
    session_id: str = "",
    dismiss: bool = False,
) -> str:
    """Registry-facing wrapper: delegate validation/persistence to the state module."""
    from agent.plan_mode import exit_plan_mode

    return exit_plan_mode(plan_markdown, session_id=session_id, dismiss=bool(dismiss))


registry.register(
    name="exit_plan_mode",
    toolset="plan",
    schema=_SCHEMA,
    handler=lambda args, **kw: exit_plan_mode_tool(
        plan_markdown=args.get("plan_markdown", ""),
        session_id=kw.get("session_id") or "",
        dismiss=bool(args.get("dismiss", False)),
    ),
    emoji="📋",
)
