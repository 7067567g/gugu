"""Reproducibility tests for the A02 Linux build (``prodline/stages/build.py``).

The contract's measured claim is ``git archive --format=zip -9 HEAD`` twice gives
the same sha256.  The build stage adds a synthetic ``FILES.sha256`` and rebuilds
the zip with fixed metadata, so this file pins the stronger claim:

* two full builds (same process and separate processes) are **byte-identical**;
* the rebuilt zip keeps the archive member bytes and fixed zip metadata;
* ``built_at`` never influences the artifact, and
* a non-reproducible build is ``status: fail`` -- never PASS, never accepted.

All repositories are synthetic ``tmp_path`` git checkouts.
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

from prodline.runners import accept  # noqa: E402  (path bootstrap above)
from prodline.stages import build  # noqa: E402

FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _commit(root: Path) -> str:
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="gugu-repro-test",
        GIT_AUTHOR_EMAIL="repro-test@example.invalid",
        GIT_COMMITTER_NAME="gugu-repro-test",
        GIT_COMMITTER_EMAIL="repro-test@example.invalid",
    )
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", "fixture"],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return _git(root, "rev-parse", "HEAD")


def _write_tree(root: Path) -> None:
    files = {
        "GUGU_VERSION": "0.9.0\n",
        "pyproject.toml": '[project]\nname = "gugu"\nversion = "0.21.5"\n',
        "apps/desktop/package.json": json.dumps({"version": "0.17.6"}) + "\n",
        "arch/upstream.lock": json.dumps({"tag": "v0.22.5", "commit": "a" * 40}) + "\n",
        "scripts/install.ps1": 'Write-Host "install"\n',
        "uv.lock": "version = 1\n",
        "package-lock.json": '{"lockfileVersion": 3}\n',
        "src/app.py": "print('hello')\n",
        "docs/notes.md": "# notes\n",
        "assets/data.txt": "deterministic payload\n",
        ".gitattributes": "*.ps1 text eol=crlf\n*.json text eol=lf\n",
    }
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_git_archive_is_deterministic_for_one_commit(repo, tmp_path: Path) -> None:
    root, commit = repo
    first = tmp_path / "archive-1.zip"
    second = tmp_path / "archive-2.zip"
    for destination in (first, second):
        subprocess.run(
            [
                "git", "-C", str(root), "archive", "--format=zip", "-9", commit,
                "-o", str(destination),
            ],
            check=True,
            capture_output=True,
        )
    assert _sha256(first) == _sha256(second)


def test_two_full_builds_are_byte_identical(repo) -> None:
    root, commit = repo
    first = build.build_artifact(root, outdir="artifacts/receipts")
    first_zip = first.artifact_path.read_bytes()
    first_sha = _sha256(first.artifact_path)
    second = build.build_artifact(root, outdir="artifacts/receipts")

    assert first.status == "pass" and second.status == "pass"
    assert second.artifact_path.read_bytes() == first_zip
    assert _sha256(second.artifact_path) == first_sha
    assert first.manifest["artifact_sha256"] == second.manifest["artifact_sha256"]
    assert first.manifest["artifact_bytes"] == second.manifest["artifact_bytes"]
    assert first.manifest["files_sha256"] == second.manifest["files_sha256"]
    assert first.manifest["files_count"] == second.manifest["files_count"]
    assert first.receipt["build_fingerprint"] == second.receipt["build_fingerprint"]

    # built_at moves, and build_receipt_sha256 follows generated_at; everything
    # else in the manifest is a pure function of the commit.
    manifest_a = dict(first.manifest)
    manifest_b = dict(second.manifest)
    for moving in ("built_at", "build_receipt_sha256"):
        manifest_a.pop(moving)
        manifest_b.pop(moving)
    assert manifest_a == manifest_b
    assert first.receipt["reproducibility"]["exclusions"] == ["built_at"]
    assert first.receipt["reproducibility"]["verdict"] == "equal"
    assert len(first.receipt["reproducibility"]["builds"]) == 2

    receipt_a = dict(first.receipt)
    receipt_b = dict(second.receipt)
    receipt_a.pop("generated_at")
    receipt_b.pop("generated_at")
    assert receipt_a == receipt_b
    assert first.receipt["commit"] == commit and second.receipt["commit"] == commit


def test_rebuilt_zip_has_fixed_metadata_and_sorted_members(repo) -> None:
    result = build.build_artifact(repo[0], outdir="artifacts/receipts")
    with zipfile.ZipFile(result.artifact_path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        names = [info.filename for info in infos]
        for info in infos:
            assert info.date_time == FIXED_TIMESTAMP, info.filename
            assert info.create_system == 3, info.filename
            mode = (info.external_attr >> 16) & 0xFFFF
            assert mode == 0o100644, (info.filename, oct(mode))
        assert names == sorted(names)
        assert build.FILES_MANIFEST in names


def test_two_separate_processes_produce_the_same_artifact(repo) -> None:
    root, _commit_sha = repo
    outdirs = ["artifacts/proc-a", "artifacts/proc-b"]
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    for outdir in outdirs:
        completed = subprocess.run(
            [
                sys.executable, "-m", "prodline.stages.build",
                "--root", str(root), "--outdir", outdir, "--quiet",
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
    first = root / outdirs[0] / build.ARTIFACT_NAME
    second = root / outdirs[1] / build.ARTIFACT_NAME
    assert _sha256(first) == _sha256(second)


def test_receipt_records_the_real_git_archive_hashes(repo, tmp_path: Path) -> None:
    root, commit = repo
    result = build.build_artifact(root, outdir="artifacts/receipts")
    probe = tmp_path / "probe.zip"
    subprocess.run(
        ["git", "-C", str(root), "archive", "--format=zip", "-9", commit, "-o", str(probe)],
        check=True,
        capture_output=True,
    )
    expected = "sha256:" + _sha256(probe)
    builds = result.receipt["reproducibility"]["builds"]
    assert [entry["git_archive_sha256"] for entry in builds] == [expected, expected]


def test_non_reproducible_build_is_never_pass_or_accepted(repo, monkeypatch) -> None:
    root, commit = repo
    original = build.build_once
    calls = {"count": 0}

    def unstable(root_path, commit_sha, work_dir, *, image=None):
        run = original(root_path, commit_sha, work_dir, image=image)
        calls["count"] += 1
        if calls["count"] % 2 == 0:  # the second run of every build attempt
            run.zip_sha256 = "0" * 64
            run.zip_bytes = run.zip_bytes + 1
        return run

    monkeypatch.setattr(build, "build_once", unstable)
    result = build.build_artifact(root, outdir="artifacts/receipts")

    assert result.status == "fail"
    assert not result.ok
    assert result.receipt["status"] == "fail"
    assert result.receipt["reproducibility"]["verdict"] == "differ"
    assert any("artifact_sha256 differs" in problem for problem in result.problems)

    # The fail receipt is still bound to the manifest, so the set stays auditable.
    receipt_bytes = result.receipt_path.read_bytes()
    assert result.manifest["build_receipt_sha256"] == "sha256:" + hashlib.sha256(
        receipt_bytes
    ).hexdigest()

    report = accept.build_report(root=root, only=("build",), commit=commit)
    assert report["verdict"] == "fail"
    assert report["stages"][0]["status"] == "fail"

    code = build.main(["--root", str(root), "--outdir", "artifacts/receipts-cli", "--quiet"])
    assert code == 1
