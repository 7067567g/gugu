"""Self-tests for the A0/A1 upstream tooling.

Run without pytest (the repo venv does not ship it):
    .venv/bin/python -m unittest discover -s scripts/upstream/tests -v
"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_rebase_manifest as manifest  # noqa: E402
import check_symbol_drift as drift  # noqa: E402
import lock_upstream as lock  # noqa: E402


class ManifestTests(unittest.TestCase):
    def test_parse_numstat_handles_binary_dash(self) -> None:
        rows = manifest.parse_numstat("10\t2\ta.py\n-\t-\tb.bin\n")
        self.assertEqual(rows, {"a.py": (10, 2), "b.bin": (0, 0)})

    def test_parse_name_status(self) -> None:
        rows = manifest.parse_name_status("M\ta.py\nA\tb.py\n")
        self.assertEqual(rows, {"a.py": "M", "b.py": "A"})

    def test_check_counts_accepts_ratchet(self) -> None:
        counts = {
            "kernel_total_files": 35,
            "kernel_modified_files": 26,
            "kernel_added_files": 9,
            "kernel_modified_added_lines": 774,
            "kernel_modified_deleted_lines": 65,
            "kernel_added_lines": 1441,
            "kernel_total_added_lines": 2215,
            "gugu_test_files": 18,
            "gugu_test_added_lines": 3114,
        }
        self.assertTrue(all(manifest.check_counts(counts).values()))

    def test_check_counts_rejects_drift(self) -> None:
        counts = {"kernel_total_files": 34, "kernel_modified_files": 26,
                  "kernel_added_files": 9, "kernel_modified_added_lines": 774,
                  "kernel_modified_deleted_lines": 65, "kernel_added_lines": 1441,
                  "kernel_total_added_lines": 2215, "gugu_test_files": 18,
                  "gugu_test_added_lines": 3114}
        self.assertFalse(manifest.check_counts(counts)["kernel_total_files"])


class ImportTests(unittest.TestCase):
    def test_collects_absolute_relative_and_type_checking(self) -> None:
        source = (
            "import os\n"
            "from a.b import c as d\n"
            "from .rel import e\n"
            "if TYPE_CHECKING:\n"
            "    from typing_only import t\n"
        )
        items = drift.collect_imports("agent/foo.py", source)
        got = {(item["module"], item["name"]): item for item in items}
        self.assertIn(("os", None), got)
        self.assertIn(("a.b", "c"), got)
        self.assertIn(("agent.rel", "e"), got)
        self.assertTrue(got[("typing_only", "t")]["type_checking"])

    def test_resolve_relative_parent(self) -> None:
        node = ast.parse("from ..bar import x").body[0]
        self.assertEqual(drift._resolve_relative("agent/sub/mod.py", node), "agent.bar")

    def test_module_symbols_ignore_function_locals(self) -> None:
        source = (
            "import os\n"
            "from x import y\n"
            "CONST = 1\n"
            "class C: pass\n"
            "def f():\n"
            "    local = 2\n"
            "    class Inner: pass\n"
            "__all__ = ['exported']\n"
            "def __getattr__(name): return None\n"
        )
        syms = drift.module_symbols(source)
        self.assertIsNotNone(syms)
        for name in ("CONST", "C", "f", "y", "exported", "__getattr__"):
            self.assertIn(name, syms)
        self.assertNotIn("local", syms)
        self.assertNotIn("Inner", syms)

    def test_module_symbols_returns_none_on_parse_error(self) -> None:
        self.assertIsNone(drift.module_symbols("def broken("))


class AllowlistTests(unittest.TestCase):
    def test_validate_allowlist_requires_three_fields(self) -> None:
        valid = {
            "module": "m", "name": "n", "kind": "SYMBOL-UNVERIFIED",
            "reason": "tag blob absent in the local partial clone", "owner": "r02",
            "latest_review_round": "r02",
        }
        invalid = {"module": "m", "name": "n2", "reason": "short"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "allowlist.json"
            path.write_text(json.dumps({"entries": [valid, invalid]}), encoding="utf-8")
            index, bad = drift.load_allowlist(path)
        self.assertEqual(len(index), 1)
        self.assertEqual(len(bad), 1)
        self.assertIn("owner", bad[0]["missing"])

    def test_aggregate_unresolved_groups_paths(self) -> None:
        entries = [
            {"module": "m", "name": "n", "kind": "SYMBOL-UNVERIFIED",
             "path": "a.py", "line": 1, "type_checking": False, "tag_blob": "x"},
            {"module": "m", "name": "n", "kind": "SYMBOL-UNVERIFIED",
             "path": "b.py", "line": 2, "type_checking": True, "tag_blob": "x"},
        ]
        groups = drift.aggregate_unresolved(entries)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["count"], 2)
        self.assertEqual(groups[0]["paths"], ["a.py:1", "b.py:2"])
        self.assertEqual(groups[0]["owner"], "gugu-reb-r02-rehearsal")
        self.assertEqual(groups[0]["latest_review_round"], "r02")
        self.assertIn("m", groups[0]["reason"])
        self.assertFalse(groups[0]["type_checking_only"])


class LockTests(unittest.TestCase):
    def test_parse_remote_v_strips_partial_clone_annotation(self) -> None:
        text = (
            "hermes\t/path/to/mirror (fetch) [blob:none]\n"
            "hermes\tDISABLED (push)\n"
            "origin\thttps://example/gugu.git (fetch)\n"
        )
        remotes = lock.parse_remote_v(text)
        self.assertEqual(remotes["hermes"]["fetch"], "/path/to/mirror")
        self.assertEqual(remotes["hermes"]["push"], "DISABLED")
        self.assertEqual(remotes["origin"]["fetch"], "https://example/gugu.git")


if __name__ == "__main__":
    unittest.main()
