"""Tests for ``prodline/stages/source.py`` (P1.2).

Positive: a self-consistent tree yields one evidence document with the four
release lines, the registered tauri line (B14), and recomputable lockfile hashes.

Negative: version drift, a missing tauri registration, a missing version file,
a missing lockfile, upstream-lock self-inconsistency, and a one-byte lockfile
tamper must all be reported (``strict=True`` raises; ``strict=False`` returns
``status="fail"``).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from prodline.stages import source  # noqa: E402  (path bootstrap above)

HEX40 = "a" * 40
HEX40_OTHER = "b" * 40


def _manifest() -> dict:
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
            {"group": "workspace_unclassified", "path": "package.json",
             "pointer": "version", "assert": "unverified", "verified": False},
        ],
    }


def _write_tree(
    root: Path,
    *,
    product: str = "0.9.0",
    kernel: str = "0.21.5",
    upstream_tag: str = "v0.22.5",
    desktop: str = "0.17.6",
    tauri: str = "0.21.1",
    manifest: dict | None = None,
) -> None:
    (root / "GUGU_VERSION").write_text(product + "\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "{kernel}"\n', encoding="utf-8"
    )
    (root / "arch").mkdir(exist_ok=True)
    lock = {
        "tag": upstream_tag,
        "commit": HEX40,
        "expected_commit": HEX40,
        "verified": True,
    }
    (root / "arch" / "upstream.lock").write_text(json.dumps(lock), encoding="utf-8")
    (root / "apps" / "desktop").mkdir(parents=True, exist_ok=True)
    (root / "apps" / "desktop" / "package.json").write_text(
        json.dumps({"version": desktop}), encoding="utf-8"
    )
    (root / "apps" / "bootstrap-installer" / "src-tauri").mkdir(parents=True, exist_ok=True)
    (root / "apps" / "bootstrap-installer" / "src-tauri" / "tauri.conf.json").write_text(
        json.dumps({"productName": "Hermes", "version": tauri}), encoding="utf-8"
    )
    (root / "package.json").write_text(json.dumps({"version": "1.0.0"}), encoding="utf-8")
    (root / "uv.lock").write_bytes(b"version = 1\n")
    (root / "package-lock.json").write_bytes(b'{"lockfileVersion": 3}\n')
    (root / "version-sources.json").write_text(
        json.dumps(manifest or _manifest()), encoding="utf-8"
    )


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    _write_tree(tmp_path)
    return tmp_path


def test_collects_four_release_lines_and_the_registered_tauri_line(tree: Path) -> None:
    evidence = source.collect_source_evidence(tree, commit=HEX40)

    assert evidence["schema"] == source.EVIDENCE_SCHEMA
    assert evidence["stage"] == "source"
    assert evidence["provenance"] == "real"
    assert evidence["status"] == "pass" and evidence["problems"] == []
    assert evidence["commit"] == HEX40
    assert len(evidence["release_lines"]) == 4
    assert "bootstrap_installer_tauri" not in evidence["release_lines"]

    assert evidence["versions"]["product"]["actual"] == "0.9.0"
    assert evidence["versions"]["upstream_kernel"]["actual"] == "0.21.5"
    assert evidence["versions"]["upstream"]["actual"] == "v0.22.5"
    assert evidence["versions"]["upstream_desktop"]["actual"] == "0.17.6"
    # B14: registered, asserted literally, and independent of the release lines.
    assert evidence["versions"]["bootstrap_installer_tauri"]["actual"] == "0.21.1"
    assert evidence["versions"]["bootstrap_installer_tauri"]["verified"] is True


def test_evidence_is_json_serialisable_and_carries_receipt_identity(tree: Path) -> None:
    evidence = source.collect_source_evidence(tree, commit=HEX40)
    round_tripped = json.loads(json.dumps(evidence))
    for field in ("schema", "commit", "generated_at", "stage", "provenance", "status"):
        assert round_tripped[field], f"evidence is missing identity field {field}"
    assert round_tripped["generated_at"].endswith("Z")
    assert set(round_tripped["lockfiles"]) == set(source.LOCKFILES)


def test_version_drift_is_red(tree: Path) -> None:
    _write_tree(tree, desktop="9.9.9")
    with pytest.raises(source.SourceAssertionError) as excinfo:
        source.collect_source_evidence(tree, commit=HEX40)
    assert any("apps/desktop/package.json" in p and "9.9.9" in p for p in excinfo.value.problems)
    assert excinfo.value.evidence is not None
    assert excinfo.value.evidence["status"] == "fail"

    lenient = source.collect_source_evidence(tree, commit=HEX40, strict=False)
    assert lenient["status"] == "fail"
    assert lenient["problems"]


def test_tauri_missing_registration_is_red_b14(tree: Path) -> None:
    manifest = _manifest()
    manifest["sources"] = [
        entry for entry in manifest["sources"]
        if entry["group"] != "bootstrap_installer_tauri"
    ]
    _write_tree(tree, manifest=manifest)
    with pytest.raises(source.SourceAssertionError) as excinfo:
        source.collect_source_evidence(tree, commit=HEX40)
    assert any("bootstrap_installer_tauri" in p for p in excinfo.value.problems)


def test_tauri_version_drift_is_red(tree: Path) -> None:
    _write_tree(tree, tauri="9.9.9")
    with pytest.raises(source.SourceAssertionError) as excinfo:
        source.collect_source_evidence(tree, commit=HEX40)
    assert any("tauri.conf.json" in p and "9.9.9" in p for p in excinfo.value.problems)


def test_upstream_lock_self_inconsistency_is_red(tree: Path) -> None:
    lock_path = tree / "arch" / "upstream.lock"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["expected_commit"] = HEX40_OTHER
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(source.SourceAssertionError) as excinfo:
        source.collect_source_evidence(tree, commit=HEX40)
    assert any("expected_commit" in p for p in excinfo.value.problems)


def test_missing_manifest_is_reported(tree: Path) -> None:
    (tree / "version-sources.json").unlink()
    with pytest.raises(source.SourceAssertionError) as excinfo:
        source.collect_source_evidence(tree, commit=HEX40)
    assert any("version-sources.json" in p for p in excinfo.value.problems)


def test_missing_lockfile_is_reported(tree: Path) -> None:
    (tree / "package-lock.json").unlink()
    with pytest.raises(source.SourceAssertionError) as excinfo:
        source.collect_source_evidence(tree, commit=HEX40)
    assert any("package-lock.json" in p for p in excinfo.value.problems)


def test_lockfile_hashes_are_recomputable(tree: Path) -> None:
    evidence = source.collect_source_evidence(tree, commit=HEX40)
    for name in source.LOCKFILES:
        raw = (tree / name).read_bytes()
        canonical = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        recorded = evidence["lockfiles"][name]
        assert recorded["bytes"] == len(raw)
        assert recorded["sha256"] == "sha256:" + hashlib.sha256(canonical).hexdigest()
        assert recorded["raw_sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
        assert recorded["canonical"] is True and recorded["eol"] == "lf"


def test_verify_source_evidence_accepts_untampered_tree(tree: Path) -> None:
    evidence = source.collect_source_evidence(tree, commit=HEX40)
    assert source.verify_source_evidence(evidence, tree) == []


def test_verify_source_evidence_detects_one_byte_lockfile_tamper(tree: Path) -> None:
    evidence = source.collect_source_evidence(tree, commit=HEX40)
    (tree / "uv.lock").write_bytes(b"version = 2\n")  # one byte differs from "1"
    problems = source.verify_source_evidence(evidence, tree)
    assert any("uv.lock" in p and "sha256" in p for p in problems), problems


def test_verify_source_evidence_detects_version_drift(tree: Path) -> None:
    evidence = source.collect_source_evidence(tree, commit=HEX40)
    _write_tree(tree, desktop="0.17.7")
    problems = source.verify_source_evidence(evidence, tree)
    assert any("upstream_desktop" in p or "apps/desktop/package.json" in p for p in problems)


def test_canonical_hash_is_stable_for_crlf_and_bom(tmp_path: Path) -> None:
    path = tmp_path / "uv.lock"
    path.write_bytes(b"a\r\nb\r\n")
    crlf = source.hash_lockfile(path, tmp_path)
    assert crlf["sha256"] == "sha256:" + hashlib.sha256(b"a\nb\n").hexdigest()
    assert crlf["raw_sha256"] == "sha256:" + hashlib.sha256(b"a\r\nb\r\n").hexdigest()
    assert crlf["canonical"] is False and crlf["eol"] == "crlf"

    path.write_bytes(b"\xef\xbb\xbfa\nb\n")
    bom = source.hash_lockfile(path, tmp_path)
    assert bom["sha256"] == "sha256:" + hashlib.sha256(b"a\nb\n").hexdigest()
    assert bom["canonical"] is False and bom["bom"] is True


def test_cli_writes_evidence_and_exits_zero(tree: Path, tmp_path: Path, capsys) -> None:
    out = tmp_path / "nested" / "source-evidence.json"
    code = source.main(["--root", str(tree), "--commit", HEX40, "--out", str(out), "--quiet"])
    assert code == 0
    evidence = json.loads(out.read_text(encoding="utf-8"))
    assert evidence["status"] == "pass" and evidence["schema"] == source.EVIDENCE_SCHEMA
    assert capsys.readouterr().err == ""


def test_cli_prints_json_and_exits_zero(tree: Path, capsys) -> None:
    code = source.main(["--root", str(tree), "--commit", HEX40])
    assert code == 0
    evidence = json.loads(capsys.readouterr().out)
    assert evidence["status"] == "pass"


def test_cli_exits_one_on_version_drift(tree: Path, capsys) -> None:
    _write_tree(tree, desktop="9.9.9")
    code = source.main(["--root", str(tree), "--commit", HEX40])
    assert code == 1
    assert "apps/desktop/package.json" in capsys.readouterr().err


def test_cli_exits_two_when_commit_cannot_be_resolved(tree: Path, capsys) -> None:
    code = source.main(["--root", str(tree), "--quiet"])
    assert code == 2
    assert "git HEAD" in capsys.readouterr().err
