"""Tests for ``prodline/stages/build.py`` (A02, Linux build).

The trees here are **synthetic** git repositories built in ``tmp_path``: they
exercise the real code path (real ``git archive``, real zip bytes, real
``git write-tree`` rebuiling) but they are not the project artifact and can never
become a PASS receipt in the project's ``artifacts/`` directory.

Positive coverage: the 21-field manifest, ``FILES.sha256`` built from the archive
member bytes (so ``*.ps1`` CRLF survives), the receipt -> zip -> manifest chain,
and ``accept --only source,build`` going green on a same-commit source evidence.

Negative coverage: a dirty tracked tree, a zip missing a required member, missing
or stale source evidence (must not be silently faked), and a tampered artifact
(the acceptance build stage must fail).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from prodline.gates import artifact_check  # noqa: E402  (path bootstrap above)
from prodline.runners import accept  # noqa: E402
from prodline.stages import build, source  # noqa: E402

CONTRACT_PATH = REPO_ROOT / "prodline" / "contracts" / "artifact-manifest.v1.json"
FORBIDDEN = (".git/", "node_modules/", "artifacts/", "__pycache__/")


def _version_manifest() -> dict:
    return {
        "schema": 1,
        "sources": [
            {"group": "product", "path": "GUGU_VERSION", "pointer": "text",
             "assert": "semver", "verified": True},
            {"group": "upstream_kernel", "path": "pyproject.toml",
             "pointer": "project.version", "assert": "0.21.5", "verified": True},
            {"group": "upstream", "path": "arch/upstream.lock", "pointer": "tag",
             "assert": "v0.22.5", "verified": True},
            {"group": "upstream_desktop", "path": "apps/desktop/package.json",
             "pointer": "version", "assert": "0.17.6", "verified": True},
            {"group": "bootstrap_installer_tauri",
             "path": "apps/bootstrap-installer/src-tauri/tauri.conf.json",
             "pointer": "version", "assert": "0.21.1", "verified": True},
        ],
    }


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _commit(root: Path, message: str = "fixture") -> str:
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="gugu-build-test",
        GIT_AUTHOR_EMAIL="build-test@example.invalid",
        GIT_COMMITTER_NAME="gugu-build-test",
        GIT_COMMITTER_EMAIL="build-test@example.invalid",
    )
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", message],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return _git(root, "rev-parse", "HEAD")


def _write_tree(root: Path, *, drop: str | None = None) -> None:
    files = {
        "GUGU_VERSION": "0.9.0\n",
        "pyproject.toml": '[project]\nname = "gugu"\nversion = "0.21.5"\n',
        "apps/desktop/package.json": json.dumps({"version": "0.17.6"}) + "\n",
        "apps/bootstrap-installer/src-tauri/tauri.conf.json": json.dumps(
            {"productName": "Hermes", "version": "0.21.1"}
        ) + "\n",
        "arch/upstream.lock": json.dumps(
            {"tag": "v0.22.5", "commit": "a" * 40, "expected_commit": "a" * 40, "verified": True}
        ) + "\n",
        "scripts/install.ps1": 'Write-Host "install"\n',
        "uv.lock": "version = 1\n",
        "package-lock.json": '{"lockfileVersion": 3}\n',
        "src/app.py": "print('hello')\n",
        "docs/notes.md": "# notes\n",
        "version-sources.json": json.dumps(_version_manifest()) + "\n",
        ".gitattributes": "*.ps1 text eol=crlf\n*.json text eol=lf\n*.py text eol=lf\n",
    }
    if drop is not None:
        files.pop(drop, None)
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _write_tree(root)
    return root, _commit(root)


def _build(repo: tuple[Path, str], outdir: str = "artifacts/receipts"):
    root, _commit_sha = repo
    return build.build_artifact(root, outdir=outdir)


def _files_entries(zip_path: Path) -> list[tuple[str, str]]:
    with zipfile.ZipFile(zip_path) as archive:
        text = archive.read(build.FILES_MANIFEST).decode("utf-8")
    entries: list[tuple[str, str]] = []
    for line in text.splitlines():
        digest, path = line.split("  ", 1)
        entries.append((path, digest))
    return entries


# --------------------------------------------------------------------------- #
# Positive
# --------------------------------------------------------------------------- #


def test_build_emits_the_full_manifest_and_a_real_pass_receipt(repo) -> None:
    root, commit = repo
    result = _build(repo)

    assert result.status == "pass", result.problems
    assert result.problems == []
    assert result.check is not None and result.check.ok, result.check.problems if result.check else None

    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    expected_keys = set(contract["required"])
    assert expected_keys <= set(result.manifest), sorted(expected_keys - set(result.manifest))

    manifest = result.manifest
    assert manifest["schema"] == "artifact-manifest/v1"
    assert manifest["commit"] == commit
    assert manifest["git_tree"] == _git(root, "rev-parse", f"{commit}^{{tree}}")
    assert manifest["provenance"] == "real"
    assert manifest["dirty"] is False
    assert manifest["product_version"] == "0.9.0"
    assert manifest["upstream_kernel_version"] == "0.21.5"
    assert manifest["desktop_version"] == "0.17.6"
    assert manifest["upstream_tag"] == "v0.22.5"
    assert manifest["producer"] == {
        "stage": "build", "tool": "prodline/stages/build.py", "contract_version": 1
    }
    assert manifest["files_manifest"] == build.FILES_MANIFEST
    assert manifest["artifact_bytes"] == result.artifact_path.stat().st_size
    assert manifest["artifact_sha256"] == "sha256:" + hashlib.sha256(
        result.artifact_path.read_bytes()
    ).hexdigest()

    receipt = result.receipt
    assert receipt["schema"] == "build-receipt/v1"
    assert receipt["stage"] == "build"
    assert receipt["commit"] == commit
    assert receipt["provenance"] == "real"
    assert receipt["status"] == "pass"
    assert receipt["dirty"] is False
    assert receipt["reproducibility"]["mode"] == "byte-identical"
    assert receipt["reproducibility"]["verdict"] == "equal"
    assert len(receipt["reproducibility"]["builds"]) == 2

    assert result.artifact_path.is_file()
    assert result.manifest_path.is_file()
    assert result.receipt_path.is_file()
    check_path = result.artifact_path.parent / build.CHECK_NAME
    assert check_path.is_file()
    check_data = json.loads(check_path.read_text(encoding="utf-8"))
    assert check_data["ok"] is True
    assert check_data["manifest"] == "artifacts/receipts/artifact-manifest.json"
    assert check_data["manifest_sha256"] == hashlib.sha256(
        result.manifest_path.read_bytes()
    ).hexdigest()
    assert check_data["artifact_sha256"] == hashlib.sha256(
        result.artifact_path.read_bytes()
    ).hexdigest()


def test_manifest_chains_to_the_receipt_but_not_the_other_way_around(repo) -> None:
    """Manifest -> receipt -> zip is the only acyclic direction."""
    result = _build(repo)
    receipt_bytes = result.receipt_path.read_bytes()
    assert result.manifest["build_receipt_sha256"] == "sha256:" + hashlib.sha256(
        receipt_bytes
    ).hexdigest()

    artifacts = result.receipt["artifacts"]
    assert [entry["path"] for entry in artifacts] == ["artifacts/receipts/artifact.zip"]
    assert "manifest_sha256" not in artifacts[0]
    # The receipt never hashes the manifest, so H(manifest) is free to hash the receipt.
    assert result.receipt.get("artifacts") and len(artifacts) == 1


def test_files_manifest_covers_every_zip_member_and_hashes_member_bytes(repo) -> None:
    result = _build(repo)
    entries = _files_entries(result.artifact_path)
    paths = [path for path, _digest in entries]

    assert paths == sorted(paths), "FILES.sha256 must be sorted by path"
    assert build.FILES_MANIFEST not in paths, "FILES.sha256 must not list itself"
    assert len(paths) == result.manifest["files_count"]
    assert len(paths) == len(set(paths))

    with zipfile.ZipFile(result.artifact_path) as archive:
        members = sorted(
            info.filename for info in archive.infolist() if not info.is_dir()
        )
        assert sorted(paths + [build.FILES_MANIFEST]) == members
        for path, digest in entries:
            assert digest == hashlib.sha256(archive.read(path)).hexdigest(), path


def test_archive_bytes_are_preserved_and_ps1_keeps_its_crlf(repo, tmp_path: Path) -> None:
    """FILES.sha256 hashes archive bytes, not checkout bytes (``*.ps1`` is CRLF)."""
    root, commit = repo
    result = _build(repo)

    archive_path = tmp_path / "probe-git-archive.zip"
    subprocess.run(
        ["git", "-C", str(root), "archive", "--format=zip", "-9", commit, "-o", str(archive_path)],
        check=True,
        capture_output=True,
    )
    with zipfile.ZipFile(archive_path) as base, zipfile.ZipFile(result.artifact_path) as built:
        base_names = [info.filename for info in base.infolist() if not info.is_dir()]
        built_names = [info.filename for info in built.infolist() if not info.is_dir()]
        assert set(base_names) | {build.FILES_MANIFEST} == set(built_names)
        for name in base_names:
            assert built.read(name) == base.read(name), name
        assert built.read("scripts/install.ps1").endswith(b"\r\n")
        assert b"\r\n" not in built.read("src/app.py")


def test_artifact_has_no_forbidden_trees_and_required_root_members(repo) -> None:
    result = _build(repo)
    with zipfile.ZipFile(result.artifact_path) as archive:
        names = [info.filename for info in archive.infolist() if not info.is_dir()]
    for required in build.REQUIRED_MEMBERS:
        assert required in names
    assert not [
        name for name in names if any(name.startswith(prefix) for prefix in FORBIDDEN)
    ]


def test_valid_source_evidence_is_cited_and_accept_source_build_passes(repo) -> None:
    """Synthetic end-to-end shape of the round goal: source PASS + build PASS."""
    root, commit = repo
    evidence = source.collect_source_evidence(root, commit=commit)
    assert evidence["status"] == "pass"
    evidence_path = root / "artifacts" / "source-evidence.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    result = build.build_artifact(root, outdir="artifacts/receipts")
    assert result.status == "pass", result.problems
    assert result.receipt["evidence"][0]["path"] == "artifacts/source-evidence.json"
    assert result.receipt["evidence"][0]["sha256"] == "sha256:" + hashlib.sha256(
        evidence_path.read_bytes()
    ).hexdigest()

    report = accept.build_report(root=root, only=("source", "build"), commit=commit)
    stages = {stage["id"]: stage for stage in report["stages"]}
    assert stages["source"]["status"] == "pass"
    assert stages["source"]["provenance"] == "real"
    assert stages["build"]["status"] == "pass"
    assert stages["build"]["provenance"] == "real"
    assert report["verdict"] == "pass"


def test_untracked_delivery_directory_does_not_block_a_rebuild(repo) -> None:
    first = _build(repo)
    second = _build(repo)
    assert first.status == "pass"
    assert second.status == "pass"
    assert first.runs[0].zip_sha256 == second.runs[0].zip_sha256


def test_cli_runs_and_returns_zero(repo, capsys) -> None:
    root, _commit_sha = repo
    code = build.main(["--root", str(root), "--outdir", "artifacts/receipts"])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "status pass" in captured.out
    assert "reproducibility equal" in captured.out


# --------------------------------------------------------------------------- #
# Negative
# --------------------------------------------------------------------------- #


def test_dirty_tracked_tree_is_refused_before_any_receipt(repo) -> None:
    root, _commit_sha = repo
    (root / "src" / "app.py").write_text("print('dirty')\n", encoding="utf-8")
    outdir = root / "artifacts" / "receipts"

    with pytest.raises(build.BuildAssertionError) as excinfo:
        build.build_artifact(root, outdir="artifacts/receipts")
    assert "dirty" in str(excinfo.value)
    assert not (outdir / build.RECEIPT_NAME).exists()


def test_archive_missing_a_required_member_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_tree(root, drop="scripts/install.ps1")
    _commit(root)
    with pytest.raises(build.BuildError) as excinfo:
        build.build_artifact(root, outdir="artifacts/receipts")
    assert "scripts/install.ps1" in str(excinfo.value)


def test_missing_source_evidence_is_not_fabricated(repo) -> None:
    result = _build(repo)
    assert result.status == "pass"
    assert "evidence" not in result.receipt
    assert result.source_evidence_note is not None
    assert "absent" in result.source_evidence_note


def test_required_source_evidence_that_is_missing_is_a_hard_error(repo) -> None:
    root, _commit_sha = repo
    with pytest.raises(build.BuildError) as excinfo:
        build.build_artifact(root, outdir="artifacts/receipts", require_source_evidence=True)
    assert "source evidence absent" in str(excinfo.value)


def test_stale_source_evidence_is_rejected_when_required(repo) -> None:
    root, commit = repo
    evidence = source.collect_source_evidence(root, commit=commit)
    evidence["commit"] = "b" * 40
    path = root / "artifacts" / "source-evidence.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(build.BuildAssertionError) as excinfo:
        build.build_artifact(root, outdir="artifacts/receipts", require_source_evidence=True)
    assert any("commit" in problem for problem in excinfo.value.problems)


def test_tampered_artifact_fails_the_checker_and_the_acceptance_stage(repo) -> None:
    root, commit = repo
    result = _build(repo)
    assert result.status == "pass"

    with open(result.artifact_path, "ab") as handle:
        handle.write(b"tamper")

    check = artifact_check.check_artifact(
        result.manifest,
        zip_path=result.artifact_path,
        build_receipt=result.receipt_path,
        repo=root,
    )
    assert not check.ok
    assert any("artifact: sha256 mismatch" in problem for problem in check.problems)

    report = accept.build_report(root=root, only=("build",), commit=commit)
    assert report["verdict"] == "fail"
    assert report["stages"][0]["status"] == "fail"
