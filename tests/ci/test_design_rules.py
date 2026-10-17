"""Tests for the DESIGN.md rule checker (T-03).

The checker (``scripts/ci/check_design_rules.py``) is stdlib-only and must
fail closed: duplicate numeric copies, missing required sections and drift
between DESIGN.md and ``gugu_storage.py`` constants all return rc=1 with a
human-readable reason. Positive case: the repository itself passes.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = REPO_ROOT / "scripts" / "ci" / "check_design_rules.py"
DESIGN_PATH = REPO_ROOT / "DESIGN.md"
STORAGE_PATH = REPO_ROOT / "plugins" / "gugu-design" / "dashboard" / "gugu_storage.py"
PLUGIN_API_PATH = REPO_ROOT / "plugins" / "gugu-design" / "dashboard" / "plugin_api.py"
BOARD_PATH = REPO_ROOT / "apps" / "desktop" / "src" / "plugins" / "kanban" / "board.tsx"
KANBAN_API_PATH = REPO_ROOT / "apps" / "desktop" / "src" / "plugins" / "kanban" / "api.ts"

# Files the checker inspects besides DESIGN.md. Copied verbatim so the fixture
# tree is a realistic positive before we inject a single defect.
WIRING_FILES = (
    "plugins/gugu-design/dashboard/gugu_storage.py",
    "plugins/gugu-design/dashboard/plugin_api.py",
    "apps/desktop/src/plugins/kanban/board.tsx",
    "apps/desktop/src/plugins/kanban/api.ts",
)


def _load_checker():
    assert CHECKER_PATH.is_file(), f"missing checker: {CHECKER_PATH}"
    spec = importlib.util.spec_from_file_location("check_design_rules_under_test", CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_tree(tmp_path: Path) -> Path:
    """Minimal repo tree the checker can validate; positive until mutated."""
    root = tmp_path / "repo"
    root.mkdir()
    assert DESIGN_PATH.is_file(), f"missing DESIGN.md: {DESIGN_PATH}"
    shutil.copyfile(DESIGN_PATH, root / "DESIGN.md")
    for rel in WIRING_FILES:
        src = REPO_ROOT / rel
        assert src.is_file(), f"missing wiring file: {src}"
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    return root


def _design_text(root: Path) -> str:
    return (root / "DESIGN.md").read_text(encoding="utf-8")


def _write_design(root: Path, text: str) -> None:
    (root / "DESIGN.md").write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------


def test_checker_accepts_repository():
    checker = _load_checker()
    assert checker.check(REPO_ROOT) == []
    result = subprocess.run(
        [sys.executable, str(CHECKER_PATH)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "通过" in result.stdout


def test_checker_accepts_unmodified_fixture_tree(tmp_path):
    checker = _load_checker()
    root = _make_tree(tmp_path)
    assert checker.check(root) == []


# ---------------------------------------------------------------------------
# Negative cases (discriminative power)
# ---------------------------------------------------------------------------


def test_duplicate_numeric_copy_fails(tmp_path):
    checker = _load_checker()
    root = _make_tree(tmp_path)
    _write_design(root, _design_text(root) + "\n- 再抄一遍图片上限：32MB。\n")

    errors = checker.check(root)
    assert errors, "duplicate numeric copy must be rejected"
    joined = "\n".join(errors)
    assert "32" in joined and "1 次" in joined

    result = subprocess.run(
        [sys.executable, str(CHECKER_PATH), str(root)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "失败" in result.stdout
    assert "32" in result.stdout


def test_missing_required_section_fails(tmp_path):
    checker = _load_checker()
    root = _make_tree(tmp_path)
    text = _design_text(root)
    assert "## 4. job 状态机" in text
    _write_design(root, text.replace("## 4. job 状态机", "## 4. 已删除的小节"))

    errors = checker.check(root)
    assert any("job 状态机" in err for err in errors), errors

    result = subprocess.run(
        [sys.executable, str(CHECKER_PATH), str(root)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "job 状态机" in result.stdout


def test_constant_drift_fails(tmp_path):
    checker = _load_checker()
    root = _make_tree(tmp_path)
    storage = root / "plugins/gugu-design/dashboard/gugu_storage.py"
    storage.write_text(
        storage.read_text(encoding="utf-8").replace(
            "MAX_IMAGE_BYTES = 32 * 1024 * 1024", "MAX_IMAGE_BYTES = 16 * 1024 * 1024"
        ),
        encoding="utf-8",
    )

    errors = checker.check(root)
    assert any("MAX_IMAGE_BYTES" in err for err in errors), errors
