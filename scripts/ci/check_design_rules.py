#!/usr/bin/env python3
"""DESIGN.md rule checker (T-03) — stdlib only.

Validates that:
1. ``DESIGN.md`` exists at the repo root and declares the required sections
   (canvas boundary / element types / asset limits / job state machine /
   failure visibility / orchestration entry).
2. The three limit values are written exactly once and match the constants in
   ``plugins/gugu-design/dashboard/gugu_storage.py`` (parsed with ``ast``, the
   module is never imported/executed).
3. The wiring the docs promise actually exists: ``board.tsx`` imports and
   renders ``OrchestrationPanel``, ``api.ts`` has GET + PUT ``/orchestration``,
   and the gugu-design backend exposes ``submit_job`` / ``_run_image_job`` /
   ``_fail_job``.

Failure is closed: every problem is collected, printed as a reason, and the
CLI exits 1. ``check(root)`` returns the list of reasons (empty == pass).
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]

DESIGN_REL = "DESIGN.md"
STORAGE_REL = "plugins/gugu-design/dashboard/gugu_storage.py"
PLUGIN_API_REL = "plugins/gugu-design/dashboard/plugin_api.py"
BOARD_REL = "apps/desktop/src/plugins/kanban/board.tsx"
KANBAN_API_REL = "apps/desktop/src/plugins/kanban/api.ts"

REQUIRED_SECTIONS = (
    "画布边界",
    "元素类型",
    "资产上限",
    "job 状态机",
    "失败可见",
    "编排入口",
)

# (constant name, unit shown in DESIGN.md). MB values are derived as bytes // 1 MiB.
LIMITS = (
    ("MAX_IMAGE_BYTES", "MB"),
    ("MAX_DOCUMENT_BYTES", "MB"),
    ("OPERATION_HISTORY", None),
)

BACKEND_SYMBOLS = ("submit_job", "_run_image_job", "_fail_job")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _read(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _numeric_value(node: ast.AST) -> int:
    """Evaluate a constant numeric expression (``32 * 1024 * 1024``) safely."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return int(node.value)
    if isinstance(node, ast.BinOp):
        left = _numeric_value(node.left)
        right = _numeric_value(node.right)
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
    raise ValueError(f"不支持的数字表达式：{ast.dump(node)}")


def _storage_constants(root: Path) -> tuple[dict[str, int], list[str]]:
    """Parse the three limit constants out of gugu_storage.py without importing it."""
    errors: list[str] = []
    values: dict[str, int] = {}
    path = root / STORAGE_REL
    text = _read(path)
    if text is None:
        return values, [f"缺少常量来源：{STORAGE_REL}"]
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return values, [f"{STORAGE_REL} 语法错误：{exc}"]

    wanted = {name for name, _unit in LIMITS}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in wanted:
            continue
        try:
            values[target.id] = _numeric_value(node.value)
        except ValueError as exc:
            errors.append(f"{STORAGE_REL} 中 {target.id} 无法求值：{exc}")

    for name in sorted(wanted - set(values)):
        if not any(name in err for err in errors):
            errors.append(f"{STORAGE_REL} 缺少常量 {name}")
    return values, errors


def _check_doc(root: Path, constants: dict[str, int]) -> list[str]:
    errors: list[str] = []
    design = root / DESIGN_REL
    text = _read(design)
    if text is None:
        return [f"缺少规则单源：{DESIGN_REL}"]

    for section in REQUIRED_SECTIONS:
        if not re.search(rf"^##\s+.*{re.escape(section)}", text, flags=re.MULTILINE):
            errors.append(f"{DESIGN_REL} 缺少必需小节：{section}")

    for name, unit in LIMITS:
        if name not in constants:
            continue  # already reported by the storage pass
        documented = constants[name] // (1024 * 1024) if unit == "MB" else constants[name]
        label = f"{documented}{unit or ''}"

        name_hits = len(re.findall(rf"\b{re.escape(name)}\b", text))
        if name_hits != 1:
            errors.append(
                f"{DESIGN_REL} 中 {name} 出现 {name_hits} 次（应恰好 1 次：数值只写一处）"
            )

        value_pattern = rf"{documented}\s*{unit}" if unit else rf"\b{documented}\b"
        value_hits = len(re.findall(value_pattern, text))
        if value_hits != 1:
            errors.append(
                f"{DESIGN_REL} 中数值 {label} 出现 {value_hits} 次（应恰好 1 次，且与 {STORAGE_REL} 常量一致）"
            )

        line_match = re.search(rf"^.*\b{re.escape(name)}\b.*$", text, flags=re.MULTILINE)
        if line_match is None:
            errors.append(f"{DESIGN_REL} 未在常量表中登记 {name}")
            continue
        if unit:
            numbers = re.findall(rf"(\d+)\s*{unit}", line_match.group(0))
        else:
            numbers = re.findall(r"\b(\d+)\b", line_match.group(0))
        if not numbers:
            errors.append(f"{DESIGN_REL} 中 {name} 未标注文档值（期望 {label}）")
        elif int(numbers[0]) != documented:
            errors.append(
                f"{DESIGN_REL} 中 {name} 文档值 {numbers[0]}{unit or ''} "
                f"与 {STORAGE_REL} 常量 {label} 不一致"
            )

    return errors


def _check_wiring(root: Path) -> list[str]:
    errors: list[str] = []

    board = _read(root / BOARD_REL)
    if board is None:
        errors.append(f"缺少前端看板：{BOARD_REL}")
    else:
        imported = re.search(
            r"import\s*\{[^}]*\bOrchestrationPanel\b[^}]*\}\s*from\s*['\"]\./orchestration['\"]",
            board,
        )
        if imported is None:
            errors.append(f"{BOARD_REL} 未从 ./orchestration 引入 OrchestrationPanel")
        if "<OrchestrationPanel" not in board:
            errors.append(f"{BOARD_REL} 未渲染 <OrchestrationPanel />")

    api = _read(root / KANBAN_API_REL)
    if api is None:
        errors.append(f"缺少前端 API：{KANBAN_API_REL}")
    else:
        if "fetchOrchestration" not in api or "'/orchestration'" not in api:
            errors.append(f"{KANBAN_API_REL} 缺少 GET /orchestration（fetchOrchestration）")
        put_match = re.search(
            r"saveOrchestration[\s\S]{0,240}?'/orchestration'[\s\S]{0,240}?method:\s*['\"]PUT['\"]",
            api,
        )
        if put_match is None:
            errors.append(f"{KANBAN_API_REL} 缺少 PUT /orchestration（saveOrchestration）")

    backend = _read(root / PLUGIN_API_REL)
    if backend is None:
        errors.append(f"缺少 Design 后端：{PLUGIN_API_REL}")
    else:
        for symbol in BACKEND_SYMBOLS:
            if not re.search(
                rf"^\s*(?:async\s+)?def\s+{re.escape(symbol)}\s*\(", backend, flags=re.MULTILINE
            ):
                errors.append(f"{PLUGIN_API_REL} 缺少 {symbol}")

    return errors


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def check(root: Optional[Path | str] = None) -> list[str]:
    """Return every DESIGN.md rule violation under ``root`` (empty list == pass)."""
    base = Path(root) if root is not None else REPO_ROOT
    base = base.resolve()
    constants, errors = _storage_constants(base)
    errors.extend(_check_doc(base, constants))
    errors.extend(_check_wiring(base))
    return errors


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="校验 DESIGN.md 规则单源与编排接线")
    parser.add_argument("root", nargs="?", default=str(REPO_ROOT), help="仓库根（默认本脚本向上两级）")
    parser.add_argument("--root", dest="root_opt", default=None, help="同上，显式指定")
    args = parser.parse_args(argv)
    root = Path(args.root_opt or args.root)

    errors = check(root)
    if errors:
        print(f"设计规则校验失败（root={root.resolve()}）：")
        for reason in errors:
            print(f" - {reason}")
        return 1

    print(f"设计规则校验通过（root={root.resolve()}）")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
