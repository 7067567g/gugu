"""Tests for the F2 arch parse-error gate (``prodline/gates/arch_boundaries.py``).

The gate consumes the existing checker's JSON receipt.  A first-party parse
error must be red (exit 2) unless an explicit allowlist proves every offending
file is outside the first-party surface; a truncated sample list and a boundary
regression must also be red.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from prodline.gates import arch_boundaries  # noqa: E402  (path bootstrap above)

HEX40 = "a" * 40


def _receipt(*, parse_errors: int = 0, files: list[str] | None = None, verdict: str = "baseline-ok") -> dict:
    samples = [{"file": name, "line": 1, "message": "SyntaxError"} for name in (files or [])]
    return {
        "schema": arch_boundaries.ARCH_RECEIPT_SCHEMA,
        "commit": HEX40,
        "verdict": verdict,
        "summary": {
            "files_scanned": 100,
            "imports_resolved": 200,
            "violations": 0,
            "parse_errors": parse_errors,
            "unclassified_edges": 0,
        },
        "parse_error_samples": samples,
    }


def test_zero_parse_errors_passes() -> None:
    result = arch_boundaries.evaluate(_receipt())
    assert result.ok
    assert result.exit_code == 0
    assert result.parse_errors == 0


def test_parse_errors_without_allowlist_fail_closed() -> None:
    result = arch_boundaries.evaluate(
        _receipt(parse_errors=2, files=["agent/a.py", "tools/b.py"])
    )
    assert not result.ok
    assert result.exit_code == 2
    assert "no --allow" in result.problems[0]
    assert "agent/a.py" in result.problems[0]


def test_parse_errors_fully_allowlisted_pass() -> None:
    result = arch_boundaries.evaluate(
        _receipt(parse_errors=2, files=["vendor/a.py", "vendor/nested/b.py"]),
        allow=["vendor/*"],
    )
    assert result.ok
    assert result.allowlisted == 2


def test_one_unallowlisted_parse_error_fails() -> None:
    result = arch_boundaries.evaluate(
        _receipt(parse_errors=2, files=["vendor/a.py", "agent/b.py"]),
        allow=["vendor/*"],
    )
    assert not result.ok
    assert result.exit_code == 2
    assert "agent/b.py" in result.problems[0]


def test_truncated_sample_list_fails_closed() -> None:
    receipt = _receipt(parse_errors=30, files=["vendor/a.py"])
    result = arch_boundaries.evaluate(receipt, allow=["vendor/*"])
    assert not result.ok
    assert result.exit_code == 2
    assert "only 1 sample" in result.problems[0]


def test_boundary_regression_is_exit_one() -> None:
    result = arch_boundaries.evaluate(_receipt(verdict="regression"))
    assert not result.ok
    assert result.exit_code == 1
    assert "baseline-ok" in result.problems[0]


def test_wrong_receipt_schema_is_rejected() -> None:
    result = arch_boundaries.evaluate({"schema": "something/v1"})
    assert not result.ok
    assert result.exit_code == 2


def test_missing_summary_is_rejected() -> None:
    result = arch_boundaries.evaluate({"schema": arch_boundaries.ARCH_RECEIPT_SCHEMA})
    assert not result.ok
    assert result.exit_code == 2


def test_unreadable_receipt_is_exit_two(tmp_path: Path) -> None:
    assert arch_boundaries.main(["--receipt", str(tmp_path / "missing.json")]) == 2


def test_malformed_receipt_is_exit_two(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    path.write_text("{not json", encoding="utf-8")
    assert arch_boundaries.main(["--receipt", str(path)]) == 2


def test_cli_passes_and_writes_json(tmp_path: Path, capsys) -> None:
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(_receipt()), encoding="utf-8")
    assert arch_boundaries.main(["--receipt", str(path), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == arch_boundaries.GATE_SCHEMA
    assert payload["ok"] is True


def test_cli_fails_on_parse_errors(tmp_path: Path, capsys) -> None:
    path = tmp_path / "receipt.json"
    path.write_text(
        json.dumps(_receipt(parse_errors=1, files=["agent/a.py"])), encoding="utf-8"
    )
    assert arch_boundaries.main(["--receipt", str(path)]) == 2
    assert "ERROR" in capsys.readouterr().out
