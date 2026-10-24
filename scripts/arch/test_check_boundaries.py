#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-tests for ``scripts/arch/check_boundaries.py``.

Run either way (both are documented in the round report):

    python3 scripts/arch/check_boundaries.py --self-test
    python3 -m pytest scripts/arch -q

The first is the zero-dependency entry point (fixtures only).  This file adds
ledger-coverage and real-baseline assertions on top, so a missing classification
rule or a stale baseline fails the suite.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CHECKER = HERE / "check_boundaries.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("arch_check_boundaries", CHECKER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolves __module__ during exec
    spec.loader.exec_module(module)
    return module


cb = _load_checker()


def test_builtin_self_test_all_cases(capsys):
    assert cb.run_self_test() == 0
    out = capsys.readouterr().out
    assert "all cases passed" in out


def test_ledger_covers_every_python_file():
    """No first-party .py may escape the ledger (that is how 'don't guess' stays true)."""
    arch = cb.load_architecture(REPO / "arch" / "architecture.yaml")
    unmatched = []
    matched = 0
    for path in cb.iter_python_files(REPO, arch):
        rel = path.relative_to(REPO).as_posix()
        if arch.match(rel) is None:
            unmatched.append(rel)
        else:
            matched += 1
    assert not unmatched, f"unclassified files: {unmatched[:10]}"
    assert matched > 6000, f"expected the v0.8.0 tree (~7k .py), matched {matched}"


def test_baseline_matches_current_ledger_and_totals():
    arch = cb.load_architecture(REPO / "arch" / "architecture.yaml")
    baseline = cb.load_baseline(REPO / "arch" / "baseline.json")
    assert baseline["architecture_sha256"] == arch.sha256, (
        "architecture.yaml changed without re-seeding the baseline; run "
        "check_boundaries.py --update-baseline --reason ..."
    )
    counts = baseline["counts"]
    assert "K->G" in counts and "X->G" in counts
    assert baseline["total"] == sum(int(v) for v in counts.values())
    assert baseline["total"] > 0


def test_real_repo_is_within_baseline():
    """The gate result itself: current measured graph <= committed baseline."""
    arch = cb.load_architecture(REPO / "arch" / "architecture.yaml")
    baseline = cb.load_baseline(REPO / "arch" / "baseline.json")
    scan = cb.scan_repo(REPO, arch)
    cmp = cb.compare(scan, baseline)
    assert cmp.ok, f"regressions: {cmp.regressions}"
    assert cmp.improvements == [], (
        "violations dropped below baseline; ratchet the baseline down explicitly "
        f"(--update-baseline --reason ...): {cmp.improvements}"
    )


def test_update_baseline_requires_reason(tmp_path, capsys):
    ledger = cb._write_fixture(tmp_path)
    assert ledger.exists()
    code = cb.main(
        ["--repo", str(tmp_path), "--baseline", str(tmp_path / "arch" / "baseline.json"), "--update-baseline"]
    )
    assert code == 2
    assert "requires a non-empty --reason" in capsys.readouterr().err


def test_new_violation_fails_gate_inside_fixture(tmp_path):
    cb._write_fixture(tmp_path)
    baseline = tmp_path / "arch" / "baseline.json"
    assert cb.main(["--repo", str(tmp_path), "--baseline", str(baseline), "--update-baseline", "--reason", "fixture"]) == 0
    agent_a = tmp_path / "agent" / "a.py"
    agent_a.write_text(agent_a.read_text(encoding="utf-8") + "from hermes_cli import auth\n", encoding="utf-8")
    assert cb.main(["--repo", str(tmp_path), "--baseline", str(baseline)]) == 1
