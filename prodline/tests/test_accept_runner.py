"""Tests for ``prodline/runners/accept.py`` (P1.3).

The acceptance policy is the point of these tests:

* a real, passing source-evidence document (or receipt) yields ``PASS``;
* ``fixture`` / ``mock`` / ``accelerated`` never yields ``PASS`` -- it is ``SKIP``;
* a missing receipt is ``SKIP`` and ``--only source,build`` still exits 0;
* ``status: fail``, a tampered receipt, or a mixed-commit chain yields ``FAIL``;
* a receipt whose commit differs from the report commit yields ``FAIL`` and
  ``report.receipts[]`` still records the offending commit;
* a tampered or stale sha256 in ``evidence[]`` / ``artifact{}`` yields ``FAIL``
  (the chain is verified by default, not only by ``receipt_check``);
* ``--expect-pass`` turns any non-pass report red (CI uses it).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from prodline.runners import accept  # noqa: E402  (path bootstrap above)

HEX40 = "a" * 40
HEX40_OTHER = "b" * 40
HEX64 = "c" * 64


def _source_evidence(
    *,
    commit: str = HEX40,
    status: str = "pass",
    provenance: str = "real",
) -> dict:
    return {
        "schema": accept.SOURCE_EVIDENCE_SCHEMA,
        "stage": "source",
        "commit": commit,
        "generated_at": "2026-10-25T12:00:00Z",
        "provenance": provenance,
        "status": status,
        "root": "/checkout",
        "release_lines": ["product", "upstream_kernel", "upstream", "upstream_desktop"],
        "versions": {"product": {"actual": "0.9.0"}},
        "lockfiles": {
            "uv.lock": {
                "sha256": HEX64,
                "raw_sha256": HEX64,
                "bytes": 1,
            },
            "package-lock.json": {
                "sha256": HEX64,
                "raw_sha256": HEX64,
                "bytes": 1,
            },
        },
        "problems": [],
    }


def _write_source(root: Path, **kwargs) -> Path:
    path = root / "artifacts" / "source-evidence.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_source_evidence(**kwargs)), encoding="utf-8")
    return path


def _receipt(
    kind: str,
    *,
    commit: str = HEX40,
    status: str = "pass",
    provenance: str = "real",
    evidence: list[dict] | None = None,
) -> dict:
    payload = {
        "schema": f"{kind}-receipt/v1",
        "commit": commit,
        "generated_at": "2026-10-25T12:00:00Z",
        "stage": "runtime" if kind in {"runtime", "task", "recovery"} else ("quality" if kind == "release" else kind),
        "provenance": provenance,
        "status": status,
    }
    if evidence is not None:
        payload["evidence"] = evidence
    return payload


def _write_receipt(
    root: Path,
    kind: str = "build",
    *,
    name: str | None = None,
    payload: dict | None = None,
    **kwargs,
) -> Path:
    path = root / "artifacts" / "receipts" / (name or f"{kind}-receipt.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    if payload is None:
        payload = _receipt(kind, **kwargs)
        if kind == "install":
            payload["artifact"] = {
                "path": "artifact.zip",
                "sha256": HEX64,
                "manifest_sha256": HEX64,
                "provenance": payload["provenance"],
                "verified": True,
            }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_evidence(root: Path, relative: str = "evidence/build.log", payload: bytes = b"build-log") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _report(root: Path, only=("source",), **kwargs) -> dict:
    return accept.build_report(root=root, only=only, commit=kwargs.pop("commit", HEX40), **kwargs)


def test_source_real_pass_yields_pass(tmp_path: Path) -> None:
    evidence = _write_source(tmp_path)
    report = _report(tmp_path)
    assert report["verdict"] == "pass", report
    assert report["schema"] == "acceptance-report/v1"
    assert report["stages"][0]["id"] == "source"
    assert report["stages"][0]["status"] == "pass"
    assert report["stages"][0]["provenance"] == "real"
    assert report["stages"][0]["evidence"][0]["sha256"] == accept.receipt_check.sha256_file(evidence)


def test_report_has_exactly_the_five_contract_fields(tmp_path: Path) -> None:
    _write_source(tmp_path)
    report = _report(tmp_path)
    assert tuple(report) == accept.REPORT_FIELDS or set(report) == set(accept.REPORT_FIELDS)
    assert report["receipts"] == []


def test_absent_source_evidence_is_skip_not_pass(tmp_path: Path) -> None:
    report = _report(tmp_path)
    assert report["stages"][0]["status"] == "skip"
    assert report["verdict"] == "incomplete"


@pytest.mark.parametrize("provenance", ["fixture", "mock", "accelerated"])
def test_non_real_source_evidence_never_passes(tmp_path: Path, provenance: str) -> None:
    _write_source(tmp_path, provenance=provenance)
    report = _report(tmp_path)
    assert report["stages"][0]["status"] == "skip"
    assert "does not yield PASS" in report["stages"][0]["reason"]
    assert report["verdict"] == "incomplete"


def test_failing_source_evidence_is_fail(tmp_path: Path) -> None:
    _write_source(tmp_path, status="fail")
    report = _report(tmp_path)
    assert report["stages"][0]["status"] == "fail"
    assert report["verdict"] == "fail"


def test_source_evidence_commit_mismatch_is_fail(tmp_path: Path) -> None:
    _write_source(tmp_path, commit=HEX40_OTHER)
    report = _report(tmp_path)
    assert report["stages"][0]["status"] == "fail"
    assert any("commit" in part for part in report["stages"][0]["reason"].split("; "))


def test_build_without_receipts_is_skip_and_exits_zero(tmp_path: Path, capsys) -> None:
    _write_source(tmp_path)
    out = tmp_path / "report.json"
    code = accept.main(
        ["--root", str(tmp_path), "--only", "source,build", "--commit", HEX40, "--out", str(out)]
    )
    assert code == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert [stage["id"] for stage in report["stages"]] == ["source", "build"]
    assert report["stages"][1]["status"] == "skip"
    assert report["verdict"] == "incomplete"
    assert "SKIP" in capsys.readouterr().out


def test_real_build_receipt_passes(tmp_path: Path) -> None:
    _write_receipt(tmp_path, "build")
    report = _report(tmp_path, only=("build",))
    assert report["stages"][0]["status"] == "pass"
    assert report["verdict"] == "pass"
    assert report["receipts"][0]["sha256"] == accept.receipt_check.sha256_file(
        tmp_path / "artifacts" / "receipts" / "build-receipt.json"
    )


def test_fixture_receipt_with_status_pass_is_skip(tmp_path: Path) -> None:
    _write_receipt(tmp_path, "build", provenance="fixture")
    report = _report(tmp_path, only=("build",))
    assert report["stages"][0]["status"] == "skip"
    assert "does not yield PASS" in report["stages"][0]["reason"]
    assert report["verdict"] == "incomplete"


def test_skipped_receipt_is_skip_not_pass(tmp_path: Path) -> None:
    _write_receipt(tmp_path, "build", status="skip")
    report = _report(tmp_path, only=("build",))
    assert report["stages"][0]["status"] == "skip"
    assert report["verdict"] == "incomplete"


def test_failing_receipt_fails_the_stage_and_exits_one(tmp_path: Path) -> None:
    _write_receipt(tmp_path, "build", status="fail")
    report = _report(tmp_path, only=("build",))
    assert report["stages"][0]["status"] == "fail"
    assert report["verdict"] == "fail"
    assert accept.main(["--root", str(tmp_path), "--only", "build", "--commit", HEX40]) == 1


def test_tampered_receipt_is_fail(tmp_path: Path) -> None:
    path = _write_receipt(tmp_path, "build")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("commit")
    path.write_text(json.dumps(payload), encoding="utf-8")
    report = _report(tmp_path, only=("build",))
    assert report["stages"][0]["status"] == "fail"
    assert "commit" in report["stages"][0]["reason"]


def test_wrong_commit_receipt_fails_the_stage_and_exits_one(tmp_path: Path) -> None:
    """G-1: a single receipt from another commit must never aggregate to PASS."""
    _write_receipt(tmp_path, "build", commit=HEX40_OTHER)
    report = _report(tmp_path, only=("build",))
    assert report["stages"][0]["status"] == "fail", report
    assert "commit mismatch" in report["stages"][0]["reason"]
    assert report["verdict"] == "fail"
    assert accept.main(["--root", str(tmp_path), "--only", "build", "--commit", HEX40]) == 1


def test_wrong_commit_receipt_records_its_commit_in_the_report(tmp_path: Path) -> None:
    """The report must keep the offending commit, not just the expected one."""
    _write_receipt(tmp_path, "build", commit=HEX40_OTHER)
    report = _report(tmp_path, only=("build",))
    assert report["commit"] == HEX40
    assert report["receipts"][0]["commit"] == HEX40_OTHER
    assert report["stages"][0]["receipts"][0]["commit"] == HEX40_OTHER


def test_matching_commit_is_recorded_and_passes(tmp_path: Path) -> None:
    _write_receipt(tmp_path, "build", commit=HEX40)
    report = _report(tmp_path, only=("build",))
    assert report["stages"][0]["status"] == "pass", report
    assert report["receipts"][0]["commit"] == HEX40
    assert report["stages"][0]["receipts"][0]["commit"] == HEX40


def test_mixed_commit_receipts_fail(tmp_path: Path) -> None:
    _write_receipt(tmp_path, "build", commit=HEX40)
    _write_receipt(tmp_path, "runtime", commit=HEX40_OTHER)
    report = _report(tmp_path, only=("build", "runtime"))
    assert report["verdict"] == "fail", report
    assert {entry["commit"] for entry in report["receipts"]} == {HEX40, HEX40_OTHER}
    # The loader-level "receipts disagree on commit" problem is attached to the
    # first non-source stage; the offending receipt fails its own stage too.
    assert any(
        "disagree on commit" in problem for problem in report["stages"][0].get("problems", [])
    )
    assert report["stages"][1]["status"] == "fail"
    assert "commit mismatch" in report["stages"][1]["reason"]


def test_valid_sha_chain_passes(tmp_path: Path) -> None:
    """G-2 positive: enabling the chain by default must not reject good evidence."""
    log = _write_evidence(tmp_path)
    evidence = [{"path": "evidence/build.log", "sha256": accept.receipt_check.sha256_file(log)}]
    _write_receipt(tmp_path, "build", evidence=evidence)
    report = _report(tmp_path, only=("build",))
    assert report["stages"][0]["status"] == "pass", report
    assert report["verdict"] == "pass"


def test_tampered_sha_chain_fails(tmp_path: Path) -> None:
    """G-2: one byte changed after the receipt was written must turn the stage red."""
    log = _write_evidence(tmp_path, payload=b"build-log")
    evidence = [{"path": "evidence/build.log", "sha256": accept.receipt_check.sha256_file(log)}]
    _write_receipt(tmp_path, "build", evidence=evidence)
    log.write_bytes(b"build-lag")  # same length, different bytes
    report = _report(tmp_path, only=("build",))
    assert report["stages"][0]["status"] == "fail", report
    assert "sha256 mismatch" in report["stages"][0]["reason"]
    assert report["verdict"] == "fail"
    assert accept.main(["--root", str(tmp_path), "--only", "build", "--commit", HEX40]) == 1


def test_stale_sha_chain_missing_file_fails(tmp_path: Path) -> None:
    _write_evidence(tmp_path)
    evidence = [{"path": "evidence/gone.log", "sha256": HEX64}]
    _write_receipt(tmp_path, "build", evidence=evidence)
    report = _report(tmp_path, only=("build",))
    assert report["stages"][0]["status"] == "fail", report
    assert "file not found" in report["stages"][0]["reason"]


def test_install_artifact_sha_is_verified(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.zip"
    artifact.write_bytes(b"artifact-bytes")
    manifest = tmp_path / "artifact-manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    payload = _receipt("install")
    payload["artifact"] = {
        "path": "artifact.zip",
        "sha256": accept.receipt_check.sha256_file(artifact),
        "manifest_path": "artifact-manifest.json",
        "manifest_sha256": accept.receipt_check.sha256_file(manifest),
        "provenance": "real",
        "verified": True,
    }
    _write_receipt(tmp_path, "install", payload=payload)
    assert _report(tmp_path, only=("install",))["stages"][0]["status"] == "pass"

    artifact.write_bytes(b"artifact-bytez")
    report = _report(tmp_path, only=("install",))
    assert report["stages"][0]["status"] == "fail", report
    assert "sha256 mismatch" in report["stages"][0]["reason"]


def test_acceptance_report_in_receipts_dir_is_ignored(tmp_path: Path) -> None:
    _write_receipt(tmp_path, "build")
    receipts_dir = tmp_path / "artifacts" / "receipts"
    (receipts_dir / "acceptance-report.json").write_text(
        json.dumps({"schema": "acceptance-report/v1", "commit": HEX40}), encoding="utf-8"
    )
    first = _report(tmp_path, only=("build",))
    second = _report(tmp_path, only=("build",))
    assert first == second
    assert len(second["receipts"]) == 1


def test_expect_pass_rejects_incomplete(tmp_path: Path) -> None:
    _write_source(tmp_path)
    code = accept.main(
        ["--root", str(tmp_path), "--only", "source,build", "--commit", HEX40, "--expect-pass"]
    )
    assert code == 1


def test_expect_pass_accepts_a_real_pass(tmp_path: Path) -> None:
    _write_source(tmp_path)
    code = accept.main(
        ["--root", str(tmp_path), "--only", "source", "--commit", HEX40, "--expect-pass"]
    )
    assert code == 0


def test_unknown_only_stage_is_a_usage_error(tmp_path: Path) -> None:
    assert accept.main(["--root", str(tmp_path), "--only", "source,deploy", "--commit", HEX40]) == 2


def test_bad_commit_override_is_a_usage_error(tmp_path: Path) -> None:
    assert accept.main(["--root", str(tmp_path), "--only", "source", "--commit", "abc"]) == 2


def test_default_only_reports_all_five_stages_in_order(tmp_path: Path) -> None:
    _write_source(tmp_path)
    report = accept.build_report(root=tmp_path, commit=HEX40)
    assert [stage["id"] for stage in report["stages"]] == list(accept.STAGE_IDS)
    assert report["verdict"] == "incomplete"


def test_source_evidence_is_not_counted_as_a_receipt(tmp_path: Path) -> None:
    _write_source(tmp_path)
    report = _report(tmp_path)
    assert report["receipts"] == []
    assert report["stages"][0]["evidence"][0]["schema"] == accept.SOURCE_EVIDENCE_SCHEMA
