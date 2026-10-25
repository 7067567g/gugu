"""Tests for ``prodline/gates/receipt_check.py`` (P1.3).

Positive: each of the six families validates when it carries the payload the
gate requires.  Negative: unregistered schemas, missing semantic fields, a bad
provenance, a commit mismatch, a receipt-shaped document with an unknown schema,
mixed commits, and a 1-byte evidence tamper must all be reported.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from prodline.gates import receipt_check  # noqa: E402  (path bootstrap above)

HEX40 = "a" * 40
HEX40_OTHER = "b" * 40
HEX64 = "c" * 64


def _base(kind: str) -> dict:
    return {
        "schema": f"{kind}-receipt/v1",
        "commit": HEX40,
        "generated_at": "2026-10-25T12:00:00Z",
        "stage": {  # stage is fixed per schema in the real contract
            "build": "build",
            "install": "install",
            "runtime": "runtime",
            "task": "runtime",
            "recovery": "runtime",
            "release": "quality",
        }[kind],
        "provenance": "real",
        "status": "pass",
    }


def _receipt(kind: str) -> dict:
    receipt = _base(kind)
    if kind == "install":
        receipt["artifact"] = {
            "path": "artifact.zip",
            "sha256": HEX64,
            "manifest_sha256": HEX64,
            "provenance": "real",
            "verified": True,
        }
    elif kind == "task":
        receipt.update(
            task_id="task-001",
            input_hash=HEX64,
            output_hash=HEX64,
            verify=True,
            verdict="pass",
        )
    elif kind == "recovery":
        receipt.update(
            fault="kill backend",
            fault_type="kill",
            injected=True,
            recovered=True,
            duplicate_side_effects=0,
        )
    elif kind == "release":
        receipt.update(
            from_artifact={"path": "old.zip", "sha256": HEX64},
            to_artifact={"path": "new.zip", "sha256": HEX64},
            upgrade_ok=True,
            rollback_ok=True,
        )
    return receipt


REGISTRY = receipt_check.registered_schemas()


def _problems(receipt: dict, **kwargs) -> list[str]:
    return receipt_check.check_receipt(receipt, registry=REGISTRY, **kwargs)


@pytest.mark.parametrize("kind", ["build", "install", "runtime", "task", "recovery", "release"])
def test_minimal_receipt_for_each_family_validates(kind: str) -> None:
    assert _problems(_receipt(kind)) == []


def test_unregistered_schema_is_rejected() -> None:
    receipt = _receipt("build")
    receipt["schema"] = "build-receipt/v9"
    assert any("not registered" in problem for problem in _problems(receipt))


def test_receipt_shaped_unknown_schema_is_flagged_when_scanning(tmp_path: Path) -> None:
    (tmp_path / "build-receipt.json").write_text(
        json.dumps({**_receipt("build"), "schema": "build-receipt/v9"}), encoding="utf-8"
    )
    result = receipt_check.check_paths(receipts_dir=tmp_path)
    assert not result.ok
    assert any("not registered" in problem for record in result.records for problem in record.problems)


@pytest.mark.parametrize("kind", ["task", "recovery", "release"])
def test_missing_semantic_field_is_rejected(kind: str) -> None:
    receipt = _receipt(kind)
    field_name = {
        "task": "task_id",
        "recovery": "fault",
        "release": "from_artifact",
    }[kind]
    receipt.pop(field_name)
    assert any(field_name in problem for problem in _problems(receipt))


def test_task_hashes_must_be_hex64() -> None:
    receipt = _receipt("task")
    receipt["input_hash"] = "sha256:not-a-hash"
    assert any("input_hash" in problem for problem in _problems(receipt))


def test_release_artifact_refs_require_sha256() -> None:
    receipt = _receipt("release")
    receipt["to_artifact"].pop("sha256")
    assert any("to_artifact.sha256" in problem for problem in _problems(receipt))


def test_bad_provenance_is_rejected_by_the_schema() -> None:
    receipt = _receipt("build")
    receipt["provenance"] = "synthetic"
    assert any("provenance" in problem for problem in _problems(receipt))


def test_commit_mismatch_is_rejected_when_expected_commit_is_given() -> None:
    receipt = _receipt("build")
    assert any(
        "commit mismatch" in problem for problem in _problems(receipt, commit=HEX40_OTHER)
    )


def test_install_receipt_requires_the_artifact_block() -> None:
    receipt = _receipt("install")
    receipt.pop("artifact")
    assert any("artifact" in problem for problem in _problems(receipt))


def test_hash_chain_accepts_a_matching_file(tmp_path: Path) -> None:
    (tmp_path / "artifact.zip").write_bytes(b"artifact-bytes")
    (tmp_path / "artifact-manifest.json").write_text("{}", encoding="utf-8")
    receipt = _receipt("install")
    receipt["artifact"].update(
        sha256=receipt_check.sha256_file(tmp_path / "artifact.zip"),
        manifest_sha256=receipt_check.sha256_file(tmp_path / "artifact-manifest.json"),
        manifest_path="artifact-manifest.json",
    )
    assert _problems(receipt, verify_files=True, root=tmp_path) == []


def test_hash_chain_detects_a_one_byte_tamper(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.zip"
    artifact.write_bytes(b"artifact-bytes")
    receipt = _receipt("install")
    receipt["artifact"].update(
        sha256=receipt_check.sha256_file(artifact),
        manifest_sha256=HEX64,
        manifest_path="artifact-manifest.json",
    )
    (tmp_path / "artifact-manifest.json").write_text("{}", encoding="utf-8")
    artifact.write_bytes(b"artifact-bytez")
    problems = _problems(receipt, verify_files=True, root=tmp_path)
    assert any("sha256 mismatch" in problem for problem in problems)


def test_hash_chain_requires_the_referenced_file(tmp_path: Path) -> None:
    receipt = _receipt("build")
    receipt["evidence"] = [{"path": "evidence/missing.log", "sha256": HEX64}]
    problems = _problems(receipt, verify_files=True, root=tmp_path)
    assert any("file not found" in problem for problem in problems)


def test_build_artifact_manifest_is_checked(tmp_path: Path) -> None:
    (tmp_path / "artifact.zip").write_bytes(b"zip")
    (tmp_path / "manifest.json").write_bytes(b'{"files": []}')
    receipt = _receipt("build")
    receipt["artifacts"] = [
        {
            "path": "artifact.zip",
            "sha256": receipt_check.sha256_file(tmp_path / "artifact.zip"),
            "manifest_path": "manifest.json",
            "manifest_sha256": receipt_check.sha256_file(tmp_path / "manifest.json"),
        }
    ]
    assert _problems(receipt, verify_files=True, root=tmp_path) == []


def test_receipt_files_record_their_own_sha256(tmp_path: Path) -> None:
    path = tmp_path / "build-receipt.json"
    path.write_text(json.dumps(_receipt("build")), encoding="utf-8")
    result = receipt_check.check_paths([path])
    assert result.ok
    assert result.records[0].sha256 == receipt_check.sha256_file(path)


def test_directory_scan_ignores_non_receipt_json(tmp_path: Path) -> None:
    receipts = tmp_path / "artifacts" / "receipts"
    receipts.mkdir(parents=True)
    (receipts / "build-receipt.json").write_text(json.dumps(_receipt("build")), encoding="utf-8")
    (receipts / "acceptance-report.json").write_text(
        json.dumps({"schema": "acceptance-report/v1", "commit": HEX40}), encoding="utf-8"
    )
    (receipts / "artifact-manifest.json").write_text(json.dumps({"files": []}), encoding="utf-8")
    result = receipt_check.check_paths(receipts_dir=receipts)
    assert result.ok
    assert [record.path.name for record in result.records] == ["build-receipt.json"]


def test_invalid_json_receipt_filename_is_flagged(tmp_path: Path) -> None:
    receipts = tmp_path / "artifacts" / "receipts"
    receipts.mkdir(parents=True)
    (receipts / "build-receipt.json").write_text("{not json", encoding="utf-8")
    result = receipt_check.check_paths(receipts_dir=receipts)
    assert not result.ok
    assert any("invalid JSON" in problem for problem in result.records[0].problems)


def test_mixed_commits_fail_unless_explicitly_allowed(tmp_path: Path) -> None:
    (tmp_path / "build-receipt.json").write_text(json.dumps(_receipt("build")), encoding="utf-8")
    other = _receipt("runtime")
    other["commit"] = HEX40_OTHER
    (tmp_path / "runtime-receipt.json").write_text(json.dumps(other), encoding="utf-8")
    assert not receipt_check.check_paths(receipts_dir=tmp_path).ok
    allowed = receipt_check.check_paths(receipts_dir=tmp_path, allow_mixed_commits=True)
    assert allowed.ok


def test_cli_returns_zero_for_a_valid_receipt(tmp_path: Path, capsys) -> None:
    path = tmp_path / "build-receipt.json"
    path.write_text(json.dumps(_receipt("build")), encoding="utf-8")
    assert receipt_check.main([str(path)]) == 0
    assert "verdict: ok" in capsys.readouterr().out


def test_cli_returns_one_for_an_invalid_receipt(tmp_path: Path) -> None:
    path = tmp_path / "build-receipt.json"
    receipt = _receipt("build")
    receipt.pop("provenance")
    path.write_text(json.dumps(receipt), encoding="utf-8")
    assert receipt_check.main([str(path)]) == 1


def test_cli_returns_two_when_there_is_nothing_to_check() -> None:
    assert receipt_check.main([]) == 2


def test_cli_json_output_has_the_check_schema(tmp_path: Path, capsys) -> None:
    path = tmp_path / "build-receipt.json"
    path.write_text(json.dumps(_receipt("build")), encoding="utf-8")
    assert receipt_check.main([str(path), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == receipt_check.CHECK_SCHEMA
    assert payload["ok"] is True
    assert payload["receipts"][0]["sha256"] == receipt_check.sha256_file(path)
