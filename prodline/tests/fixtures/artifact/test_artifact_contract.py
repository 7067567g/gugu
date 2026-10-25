"""Contract + negative-fixture tests for ``artifact-manifest/v1`` (A01).

The matrix lives in ``cases.json``; ``fixture_builder.py`` materialises each
case (text tree -> deterministic zip -> manifest whose ``git_tree``/``commit``
come from a real temporary git repository).  The checker must be green on
``valid`` and red on every negative case for the stated reason.

The test module lives here (not ``prodline/tests/test_artifact_*.py``) because
A01's write scope only allows ``prodline/tests/fixtures/artifact/``; pytest
still collects it from ``prodline/tests``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(HERE))

from prodline.gates import artifact_check  # noqa: E402  (path bootstrap above)
from prodline.schema_subset import assert_supported_schema  # noqa: E402

import fixture_builder  # noqa: E402  (same directory)

CASES = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))["cases"]
CASE_IDS = [str(case["id"]) for case in CASES]
REQUIRED_FIELDS = [
    "schema",
    "commit",
    "git_tree",
    "provenance",
    "product_version",
    "desktop_version",
    "upstream_kernel_version",
    "upstream_tag",
    "artifact_sha256",
    "artifact_bytes",
    "files_sha256",
    "files_count",
    "files_manifest",
    "built_at",
    "runner",
    "dirty",
    "lockfiles",
    "includes",
    "excludes",
    "build_receipt_sha256",
    "producer",
]
REQUIRED_NEGATIVE_CLASSES = {
    # A01's four mandated negative fixtures (see CONTRACT-FREEZE.md §4 / plan-A §5.3).
    "missing-git-tree",
    "dirty-true",
    "version-mismatch",
    "files-count-mismatch",
}


@pytest.fixture(scope="module")
def contract() -> dict:
    return artifact_check.load_contract()


def test_contract_freezes_exactly_21_fields(contract: dict) -> None:
    assert contract["$id"] == "https://gugu.dev/prodline/contracts/artifact-manifest/v1"
    assert contract["required"] == REQUIRED_FIELDS
    assert len(contract["required"]) == 21
    assert contract["additionalProperties"] is True
    assert contract["properties"]["schema"]["const"] == "artifact-manifest/v1"
    assert contract["properties"]["files_manifest"]["const"] == "FILES.sha256"
    for field in REQUIRED_FIELDS:
        assert field in contract["properties"], field
    for optional in (
        "commit_short",
        "branch",
        "git_archive",
        "extra_artifacts",
        "payloads",
        "fixture_reason",
    ):
        assert optional in contract["properties"], optional
    assert_supported_schema(contract)


def test_negative_matrix_covers_the_four_mandated_classes() -> None:
    present = set(CASE_IDS)
    missing = REQUIRED_NEGATIVE_CLASSES - present
    assert not missing, f"cases.json is missing mandated negative fixtures: {sorted(missing)}"
    assert CASE_IDS.count("valid") == 1


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_case_matrix(case: dict, tmp_path: Path) -> None:
    built = fixture_builder.build_case(case, tmp_path)
    result = artifact_check.check_artifact(built.manifest, zip_path=built.zip_path)
    if case["expect"] == "pass":
        assert result.problems == []
        assert result.ok
        return
    assert not result.ok, f"{case['id']} unexpectedly passed"
    for needle in case.get("problems_contain") or []:
        assert any(needle in problem for problem in result.problems), (
            f"{case['id']}: {needle!r} not in {result.problems}"
        )


def test_valid_case_with_repo_and_build_receipt(tmp_path: Path) -> None:
    built = fixture_builder.build_case({"id": "valid-full", "expect": "pass"}, tmp_path)
    result = artifact_check.check_artifact(
        built.manifest,
        zip_path=built.zip_path,
        repo=built.git_meta.repo,
        build_receipt=built.build_receipt_path,
    )
    assert result.problems == []
    assert "repo-commit-tree" in result.checks
    assert "build-receipt-sha256" in result.checks


def test_build_receipt_tamper_is_red(tmp_path: Path) -> None:
    built = fixture_builder.build_case({"id": "valid-receipt", "expect": "pass"}, tmp_path)
    tampered = tmp_path / "tampered-build-receipt.json"
    tampered.write_text("not the receipt that produced the artifact\n", encoding="utf-8")
    result = artifact_check.check_artifact(
        built.manifest, zip_path=built.zip_path, build_receipt=tampered
    )
    assert any("build_receipt: sha256 mismatch" in problem for problem in result.problems)


def test_manifest_only_mode(tmp_path: Path) -> None:
    built = fixture_builder.build_case({"id": "manifest-only", "expect": "pass"}, tmp_path)
    result = artifact_check.check_artifact(built.manifest)
    assert result.ok
    assert "manifest-only" in result.checks


def test_expect_commit_mismatch(tmp_path: Path) -> None:
    built = fixture_builder.build_case({"id": "expect-commit", "expect": "pass"}, tmp_path)
    result = artifact_check.check_artifact(
        built.manifest, zip_path=built.zip_path, expect_commit="0" * 40
    )
    assert any("commit mismatch" in problem for problem in result.problems)


def test_expect_commit_rejects_non_hex(tmp_path: Path) -> None:
    built = fixture_builder.build_case({"id": "bad-expect", "expect": "pass"}, tmp_path)
    with pytest.raises(artifact_check.ContractError):
        artifact_check.check_artifact(
            built.manifest, zip_path=built.zip_path, expect_commit="not-a-commit"
        )


def test_git_tree_escape_hatch(tmp_path: Path) -> None:
    case = next(case for case in CASES if case["id"] == "git-tree-mismatch")
    built = fixture_builder.build_case(case, tmp_path)
    assert not artifact_check.check_artifact(
        built.manifest, zip_path=built.zip_path
    ).ok
    assert artifact_check.check_artifact(
        built.manifest, zip_path=built.zip_path, verify_git_tree=False
    ).ok


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    valid = fixture_builder.build_case({"id": "cli-valid", "expect": "pass"}, tmp_path)
    assert artifact_check.main(
        ["--zip", str(valid.zip_path), "--manifest", str(valid.manifest_path), "--quiet"]
    ) == 0

    dirty = fixture_builder.build_case(
        {"id": "cli-dirty", "expect": "fail", "manifest_patch": {"dirty": True}}, tmp_path
    )
    assert artifact_check.main(
        ["--zip", str(dirty.zip_path), "--manifest", str(dirty.manifest_path)]
    ) == 1

    assert artifact_check.main(["--manifest", str(tmp_path / "missing.json")]) == 2
    capsys.readouterr()


def test_cli_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    built = fixture_builder.build_case({"id": "cli-json", "expect": "pass"}, tmp_path)
    assert artifact_check.main(
        [
            "--zip",
            str(built.zip_path),
            "--manifest",
            str(built.manifest_path),
            "--format",
            "json",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "gugu.prodline-artifact-check/v1"
    assert payload["ok"] is True
    assert payload["problems"] == []


def test_git_tree_digest_matches_git_mktree(tmp_path: Path) -> None:
    """All three git modes, with git itself as the ground truth (no chmod)."""
    repo = tmp_path / "mktree"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)

    def blob(data: bytes) -> str:
        completed = subprocess.run(
            ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
            input=data,
            capture_output=True,
            check=True,
        )
        return completed.stdout.decode().strip()

    def mktree(rows: list[str]) -> str:
        completed = subprocess.run(
            ["git", "-C", str(repo), "mktree"],
            input="\n".join(rows) + "\n",
            capture_output=True,
            text=True,
            check=True,
        )
        return completed.stdout.strip()

    sub_tree = mktree([f"100644 blob {blob(b'x\n')}\tfile.txt"])
    root_tree = mktree(
        [
            f"100755 blob {blob(b'#!/bin/sh\n')}\trun.sh",
            f"120000 blob {blob(b'run.sh')}\tlink",
            f"040000 tree {sub_tree}\tsub",
        ]
    )
    assert artifact_check.git_tree_digest(
        {
            "run.sh": (0o100755, b"#!/bin/sh\n"),
            "link": (0o120000, b"run.sh"),
            "sub/file.txt": (0o100644, b"x\n"),
        }
    ) == root_tree


def test_git_tree_rebuild_applies_gitattributes_eol(tmp_path: Path) -> None:
    """``git archive`` writes CRLF for ``*.ps1``; the commit tree has LF.

    Hashing raw zip bytes would therefore misjudge a real artifact.  The
    checker extracts and lets git normalize (exactly the B-side command).
    """
    source = tmp_path / "source"
    source.mkdir()
    (source / ".gitattributes").write_text("*.ps1 text eol=crlf\n", encoding="utf-8")
    (source / "GUGU_VERSION").write_text("0.9.0\n", encoding="utf-8")
    (source / "a.ps1").write_text("line1\nline2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(source), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "crlf",
        ],
        check=True,
    )
    tree = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD^{tree}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    archive_path = tmp_path / "crlf.zip"
    fixture_builder._write_zip(
        archive_path,
        {
            ".gitattributes": b"*.ps1 text eol=crlf\n",
            "GUGU_VERSION": b"0.9.0\n",
            "a.ps1": b"line1\r\nline2\r\n",  # what git archive emits
        },
    )
    with zipfile.ZipFile(archive_path) as archive:
        members = {info.filename: info for info in archive.infolist()}
        assert artifact_check.rebuild_tree_with_git(archive, members) == tree
    raw_tree = artifact_check.git_tree_digest(
        {
            ".gitattributes": (0o100644, b"*.ps1 text eol=crlf\n"),
            "GUGU_VERSION": (0o100644, b"0.9.0\n"),
            "a.ps1": (0o100644, b"line1\r\nline2\r\n"),
        }
    )
    assert raw_tree != tree
