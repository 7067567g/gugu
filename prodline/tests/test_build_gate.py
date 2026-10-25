"""Tests for the Windows build-lane receipt recorder + event/tier gate (A03).

The gate exists so CI can tell **never ran** (no receipt -> exit 3) from
**ran and failed** (receipt rejected -> exit 1).  These tests build synthetic
build-output directories and receipts as *data*; no Windows runner or product
binary is needed or claimed.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from prodline.gates import build_gate  # noqa: E402  (path bootstrap above)
from prodline.schema_subset import validate  # noqa: E402

HEX40 = "a" * 40
OTHER40 = "b" * 40
RUNNER = {"os": "windows", "image": "windows-latest", "arch": "x64"}
TOOLCHAIN = {"python": "3.12.15", "git": "git version 2.50.0", "node": "v24.0.0", "npm": "11.0.0"}
EXCLUDE_STAMP = "resources/install-stamp.json"


def _tree(root: Path, files: dict[str, bytes]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for relative, payload in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return root


def _installer_tree(root: Path, *, stamp: bytes = b'{"builtAt":"2026-10-25T00:00:00Z"}') -> Path:
    return _tree(
        root,
        {
            "gugu-0.17.6-win-x64.exe": b"nsis-installer-bytes",
            "latest.yml": b"version: 0.17.6\n",
            "resources/install-stamp.json": stamp,
            "resources/app.asar": b"asar-bytes",
        },
    )


def _record(
    event: str,
    dirs: list[Path],
    *,
    excludes: list[str] | None = None,
    ref: str | None = None,
    commit: str = HEX40,
) -> dict:
    resolved_ref = ref
    if resolved_ref is None:
        resolved_ref = build_gate.PUSH_MAIN_REF if event == build_gate.EVENT_PUSH_MAIN else "refs/pull/7/merge"
    return build_gate.build_receipt(
        event=event,
        commit=commit,
        build_dirs=dirs,
        excludes=excludes or [],
        ref=resolved_ref,
        run_id="123456",
        run_attempt="1",
        runner=RUNNER,
        toolchain=TOOLCHAIN,
        generated_at="2026-10-25T00:00:00Z",
    )


# ---------------------------------------------------------------------------
# Recording: event tiers
# ---------------------------------------------------------------------------


def test_pr_single_build_records_single_build_and_passes(tmp_path: Path) -> None:
    receipt = _record(build_gate.EVENT_PR, [_installer_tree(tmp_path / "win-1")])

    assert receipt["schema"] == "build-receipt/v1"
    assert receipt["status"] == "pass"
    assert receipt["provenance"] == "real"
    assert receipt["reproducibility"]["mode"] == "single-build"
    assert receipt["reproducibility"]["verdict"] == "not-attempted"
    assert len(receipt["reproducibility"]["builds"]) == 1
    assert receipt["reproducibility"]["exclusions"] == []
    assert receipt["artifacts"], "the installer must be recorded as an artifact"

    result = build_gate.evaluate(receipt, event=build_gate.EVENT_PR, expect_commit=HEX40)
    assert result.ok, result.problems
    assert result.exit_code == build_gate.EXIT_PASS


def test_push_identical_trees_are_byte_identical(tmp_path: Path) -> None:
    first = _installer_tree(tmp_path / "win-1")
    second = _installer_tree(tmp_path / "win-2")
    receipt = _record(build_gate.EVENT_PUSH_MAIN, [first, second])

    assert receipt["status"] == "pass"
    assert receipt["reproducibility"]["mode"] == "byte-identical"
    assert receipt["reproducibility"]["verdict"] == "equal"
    assert receipt["reproducibility"]["exclusions"] == []
    assert build_gate.evaluate(receipt, event=build_gate.EVENT_PUSH_MAIN).ok


def test_push_built_at_only_difference_is_manifest_normalized(tmp_path: Path) -> None:
    first = _installer_tree(tmp_path / "win-1", stamp=b'{"builtAt":"2026-10-25T00:00:01Z"}')
    second = _installer_tree(tmp_path / "win-2", stamp=b'{"builtAt":"2026-10-25T00:00:02Z"}')
    receipt = _record(
        build_gate.EVENT_PUSH_MAIN, [first, second], excludes=[EXCLUDE_STAMP]
    )

    reproducibility = receipt["reproducibility"]
    assert receipt["status"] == "pass"
    assert reproducibility["mode"] == "manifest-normalized"
    assert reproducibility["verdict"] == "equal"
    assert reproducibility["exclusions"] == [EXCLUDE_STAMP]
    assert reproducibility["builds"][0]["excluded_files"] == [EXCLUDE_STAMP]
    assert build_gate.evaluate(receipt, event=build_gate.EVENT_PUSH_MAIN).ok


def test_push_unexplained_difference_fails_the_record(tmp_path: Path) -> None:
    first = _installer_tree(tmp_path / "win-1")
    second = _tree(
        tmp_path / "win-2",
        {
            "gugu-0.17.6-win-x64.exe": b"different-installer-bytes",
            "latest.yml": b"version: 0.17.6\n",
            "resources/install-stamp.json": b'{"builtAt":"2026-10-25T00:00:00Z"}',
            "resources/app.asar": b"asar-bytes",
        },
    )
    receipt = _record(build_gate.EVENT_PUSH_MAIN, [first, second])

    assert receipt["status"] == "fail"
    assert receipt["reproducibility"]["verdict"] == "differ"
    assert receipt["clean_build"] is False
    assert receipt["problems"]

    result = build_gate.evaluate(receipt, event=build_gate.EVENT_PUSH_MAIN)
    assert not result.ok
    assert result.exit_code == build_gate.EXIT_FAIL
    assert result.decision == "fail"


def test_push_requires_two_build_directories(tmp_path: Path) -> None:
    with pytest.raises(build_gate.BuildGateError):
        _record(build_gate.EVENT_PUSH_MAIN, [_installer_tree(tmp_path / "win-1")])
    with pytest.raises(build_gate.BuildGateError):
        _record(
            build_gate.EVENT_PR,
            [_installer_tree(tmp_path / "a"), _installer_tree(tmp_path / "b")],
        )


def test_record_validates_against_the_frozen_schema(tmp_path: Path) -> None:
    receipt = _record(build_gate.EVENT_PR, [_installer_tree(tmp_path / "win-1")])
    schema = json.loads(build_gate.BUILD_RECEIPT_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert validate(receipt, schema) == []


def test_missing_build_dir_records_ran_and_failed(tmp_path: Path) -> None:
    receipt = _record(build_gate.EVENT_PR, [tmp_path / "not-built"])

    assert receipt["status"] == "fail"
    assert receipt["reproducibility"]["builds"][0].get("missing") is True
    result = build_gate.evaluate(receipt, event=build_gate.EVENT_PR)
    assert result.exit_code == build_gate.EXIT_FAIL
    assert result.ran_and_failed is True


# ---------------------------------------------------------------------------
# Gate: event/tier coherence (the mandated negatives)
# ---------------------------------------------------------------------------


def test_pr_receipt_claiming_byte_identical_is_red(tmp_path: Path) -> None:
    receipt = _record(build_gate.EVENT_PR, [_installer_tree(tmp_path / "win-1")])
    receipt["reproducibility"]["mode"] = "byte-identical"
    receipt["reproducibility"]["verdict"] = "equal"

    result = build_gate.evaluate(receipt, event=build_gate.EVENT_PR)
    assert result.exit_code == build_gate.EXIT_FAIL
    assert any("PR builds once" in problem for problem in result.problems), result.problems


def test_push_receipt_claiming_single_build_is_red(tmp_path: Path) -> None:
    first = _installer_tree(tmp_path / "win-1")
    second = _installer_tree(tmp_path / "win-2")
    receipt = _record(build_gate.EVENT_PUSH_MAIN, [first, second])
    receipt["reproducibility"]["mode"] = "single-build"
    receipt["reproducibility"]["verdict"] = "not-attempted"
    receipt["reproducibility"]["builds"] = receipt["reproducibility"]["builds"][:1]

    result = build_gate.evaluate(receipt, event=build_gate.EVENT_PUSH_MAIN)
    assert result.exit_code == build_gate.EXIT_FAIL
    assert any("push-main must build twice" in problem for problem in result.problems), (
        result.problems
    )


def test_manifest_normalized_requires_exclusions(tmp_path: Path) -> None:
    first = _installer_tree(tmp_path / "win-1", stamp=b"one")
    second = _installer_tree(tmp_path / "win-2", stamp=b"two")
    receipt = _record(
        build_gate.EVENT_PUSH_MAIN, [first, second], excludes=[EXCLUDE_STAMP]
    )
    receipt["reproducibility"]["mode"] = "manifest-normalized"
    receipt["reproducibility"]["exclusions"] = []

    result = build_gate.evaluate(receipt, event=build_gate.EVENT_PUSH_MAIN)
    assert result.exit_code == build_gate.EXIT_FAIL
    assert any("requires non-empty exclusions" in problem for problem in result.problems)


def test_byte_identical_must_not_exclude_files(tmp_path: Path) -> None:
    first = _installer_tree(tmp_path / "win-1")
    second = _installer_tree(tmp_path / "win-2")
    receipt = _record(build_gate.EVENT_PUSH_MAIN, [first, second])
    receipt["reproducibility"]["exclusions"] = [EXCLUDE_STAMP]

    result = build_gate.evaluate(receipt, event=build_gate.EVENT_PUSH_MAIN)
    assert result.exit_code == build_gate.EXIT_FAIL
    assert any("byte-identical must not exclude" in problem for problem in result.problems)


def test_missing_build_entry_is_red_even_when_status_claims_pass(tmp_path: Path) -> None:
    receipt = _record(build_gate.EVENT_PR, [_installer_tree(tmp_path / "win-1")])
    receipt["reproducibility"]["builds"][0]["missing"] = True

    result = build_gate.evaluate(receipt, event=build_gate.EVENT_PR)
    assert result.exit_code == build_gate.EXIT_FAIL
    assert any("missing its output directory" in problem for problem in result.problems)


def test_byte_identical_with_excluded_files_is_red(tmp_path: Path) -> None:
    first = _installer_tree(tmp_path / "win-1")
    second = _installer_tree(tmp_path / "win-2")
    receipt = _record(build_gate.EVENT_PUSH_MAIN, [first, second])
    receipt["reproducibility"]["builds"][0]["excluded_files"] = [EXCLUDE_STAMP]

    result = build_gate.evaluate(receipt, event=build_gate.EVENT_PUSH_MAIN)
    assert result.exit_code == build_gate.EXIT_FAIL
    assert any("must not record excluded_files" in problem for problem in result.problems)


def test_pr_receipt_with_main_ref_is_red(tmp_path: Path) -> None:
    receipt = _record(
        build_gate.EVENT_PR,
        [_installer_tree(tmp_path / "win-1")],
        ref=build_gate.PUSH_MAIN_REF,
    )
    result = build_gate.evaluate(receipt, event=build_gate.EVENT_PR)
    assert result.exit_code == build_gate.EXIT_FAIL
    assert any("must not claim refs/heads/main" in problem for problem in result.problems)


def test_event_mismatch_is_red(tmp_path: Path) -> None:
    receipt = _record(build_gate.EVENT_PR, [_installer_tree(tmp_path / "win-1")])
    result = build_gate.evaluate(receipt, event=build_gate.EVENT_PUSH_MAIN)
    assert result.exit_code == build_gate.EXIT_FAIL


def test_non_windows_runner_is_red(tmp_path: Path) -> None:
    receipt = _record(build_gate.EVENT_PR, [_installer_tree(tmp_path / "win-1")])
    receipt["runner"]["image"] = "ubuntu-latest"

    result = build_gate.evaluate(receipt, event=build_gate.EVENT_PR)
    assert result.exit_code == build_gate.EXIT_FAIL
    assert any("runner.image" in problem for problem in result.problems)


# ---------------------------------------------------------------------------
# Gate: never ran vs ran and failed
# ---------------------------------------------------------------------------


def test_missing_receipt_is_exit_3_never_ran() -> None:
    result = build_gate.evaluate(None, event=build_gate.EVENT_PR)

    assert result.exit_code == build_gate.EXIT_MISSING
    assert result.decision == "missing"
    assert result.never_ran is True
    assert result.ran_and_failed is False
    assert result.to_dict()["never_ran"] is True


def test_failed_receipt_is_exit_1_ran_and_failed(tmp_path: Path) -> None:
    receipt = _record(build_gate.EVENT_PR, [tmp_path / "not-built"])
    result = build_gate.evaluate(receipt, event=build_gate.EVENT_PR)

    assert result.exit_code == build_gate.EXIT_FAIL
    assert result.decision == "fail"
    assert result.never_ran is False
    assert result.ran_and_failed is True


def test_skip_status_is_not_a_pass(tmp_path: Path) -> None:
    receipt = _record(build_gate.EVENT_PR, [_installer_tree(tmp_path / "win-1")])
    receipt["status"] = "skip"
    receipt["clean_build"] = False

    result = build_gate.evaluate(receipt, event=build_gate.EVENT_PR)
    assert result.exit_code == build_gate.EXIT_FAIL
    assert any("skip is not pass" in problem for problem in result.problems)


@pytest.mark.parametrize("provenance", ["fixture", "mock", "accelerated"])
def test_non_real_provenance_never_passes(tmp_path: Path, provenance: str) -> None:
    receipt = _record(build_gate.EVENT_PR, [_installer_tree(tmp_path / "win-1")])
    receipt["provenance"] = provenance

    result = build_gate.evaluate(receipt, event=build_gate.EVENT_PR)
    assert result.exit_code == build_gate.EXIT_FAIL
    assert any("only 'real' can pass" in problem for problem in result.problems)


def test_commit_mismatch_is_red(tmp_path: Path) -> None:
    receipt = _record(build_gate.EVENT_PR, [_installer_tree(tmp_path / "win-1")])
    result = build_gate.evaluate(
        receipt, event=build_gate.EVENT_PR, expect_commit=OTHER40
    )
    assert result.exit_code == build_gate.EXIT_FAIL
    assert any("!= expected" in problem for problem in result.problems)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_record_then_check_roundtrip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tree = _installer_tree(tmp_path / "win-1")
    out = tmp_path / "artifacts" / "build-receipt-win.json"

    exit_code = build_gate.main(
        [
            "record",
            "--event", "pr",
            "--commit", HEX40,
            "--build-dir", str(tree),
            "--exclude", EXCLUDE_STAMP,
            "--ref", "refs/pull/7/merge",
            "--run-id", "123456",
            "--run-attempt", "1",
            "--out", str(out),
        ]
    )
    assert exit_code == 0, capsys.readouterr()
    assert out.is_file()

    exit_code = build_gate.main(
        [
            "check",
            str(out),
            "--event", "pr",
            "--expect-commit", HEX40,
            "--format", "json",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0, captured
    assert '"decision": "pass"' in captured.out


def test_cli_check_missing_receipt_exits_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = build_gate.main(
        ["check", str(tmp_path / "absent.json"), "--event", "pr"]
    )
    captured = capsys.readouterr()
    assert exit_code == build_gate.EXIT_MISSING
    assert "never_ran=true" in captured.out


def test_cli_record_rejects_wrong_build_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = _installer_tree(tmp_path / "win-1")
    exit_code = build_gate.main(
        [
            "record",
            "--event", "push-main",
            "--commit", HEX40,
            "--build-dir", str(tree),
            "--out", str(tmp_path / "receipt.json"),
        ]
    )
    assert exit_code == build_gate.EXIT_INPUT
    assert "needs 2 build dir" in capsys.readouterr().err


def test_deepcopy_mutation_does_not_leak_between_receipts(tmp_path: Path) -> None:
    first = _record(build_gate.EVENT_PR, [_installer_tree(tmp_path / "win-1")])
    mutated = copy.deepcopy(first)
    mutated["reproducibility"]["mode"] = "byte-identical"
    assert first["reproducibility"]["mode"] == "single-build"
