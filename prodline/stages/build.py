"""Build stage: reproducible ``artifact.zip`` + manifest + ``build-receipt/v1`` (A02).

The build stage is the producer half of the frozen A-to-B contract
(``prodline/contracts/artifact-manifest.v1.json`` /
``docs/architecture/prodline/ARTIFACT-CONTRACT.md``).  It turns a **committed**
tree into the delivery set::

    artifact.zip            # git-archive content + synthetic FILES.sha256
    artifact-manifest.json  # 21 required fields, provenance=real, dirty=false
    build-receipt.json      # build-receipt/v1, provenance=real, status=pass|fail
    artifact-check.json     # gate output (independent evidence, not a receipt)

Reproducibility
---------------
The whole pipeline (``git archive`` -> FILES manifest -> deterministic zip) is
run **twice** on the same commit.  The receipt records ``byte-identical`` only
when both runs produce the same zip sha256/bytes and the same
``FILES.sha256``; otherwise the receipt is ``status: fail`` and the CLI exits 1.
There is no flag that turns a single build into PASS.

Chain direction (avoiding an impossible cycle)
----------------------------------------------
``manifest.build_receipt_sha256`` binds the final receipt bytes, while the
receipt only carries the artifact zip sha256 (the optional
``artifacts[].manifest_sha256`` is deliberately not used: a receipt that hashed
the manifest would make ``H(manifest)`` depend on ``H(receipt)`` and vice
versa).  The manifest is still fully verified against the zip by
``prodline.gates.artifact_check`` (schema, bytes, FILES chain, three versions,
lockfiles, rebuilt ``git_tree``, repo commit->tree).

Clean tree
----------
Tracked modifications abort the build: a publishable manifest must declare
``dirty: false`` (schema ``const``).  Untracked/ignored files are fine -- the
delivery itself lives in the gitignored ``artifacts/`` -- so the check is
``git status --porcelain --untracked-files=no``.

CLI::

    python -m prodline.stages.build --root . --outdir artifacts/receipts
    python -m prodline.stages.build --commit <sha> --image gugu-linux-x86_64

Exit codes: ``0`` pass, ``1`` assertion/reproducibility/check problem,
``2`` unreadable input or configuration error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from prodline.gates.artifact_check import (
    FILES_MANIFEST,
    LOCKFILES,
    REQUIRED_MEMBERS,
    CheckResult,
    ContractError,
    check_artifact,
    sha256_bytes,
    sha256_file,
)

STAGE_ID = "build"
RECEIPT_SCHEMA = "build-receipt/v1"
PROVENANCE = "real"
TOOL = "prodline/stages/build.py"
CONTRACT_VERSION = 1

DEFAULT_OUTDIR = Path("artifacts/receipts")
DEFAULT_SOURCE_EVIDENCE = Path("artifacts/source-evidence.json")

#: Fixed metadata keeps the rebuilt zip byte-identical across runs.  The value
#: is the zip epoch (1980-01-01), not a build time.
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
ZIP_COMPRESSION_LEVEL = 9

EXCLUDES = [
    ".git/**",
    "node_modules/**",
    ".venv/**",
    "apps/desktop/dist/**",
    "apps/desktop/build/**",
    "apps/desktop/release/**",
    "artifacts/**",
    "__pycache__/**",
]
INCLUDES = {
    "pyproject.toml": True,
    "GUGU_VERSION": True,
    "scripts/install.ps1": True,
}

ARTIFACT_NAME = "artifact.zip"
MANIFEST_NAME = "artifact-manifest.json"
RECEIPT_NAME = "build-receipt.json"
CHECK_NAME = "artifact-check.json"

_UTF8_BOM = b"\xef\xbb\xbf"
_HEX40 = re.compile(r"^[0-9a-f]{40}$")


class BuildError(RuntimeError):
    """An input needed by the build stage is missing or unreadable."""


class BuildAssertionError(BuildError):
    """The tree or the produced artifact set disagrees with the contract."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


# --------------------------------------------------------------------------- #
# Paths / hashing / time
# --------------------------------------------------------------------------- #


def _repo_root(root: Path | str | None) -> Path:
    return Path(root) if root is not None else Path(__file__).resolve().parents[2]


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def sha256_prefixed(path: Path | str) -> str:
    return "sha256:" + sha256_file(path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Git (read-only)
# --------------------------------------------------------------------------- #


def _git(root: Path, *args: str, binary: bool = False) -> str | bytes:
    command = ["git", "-C", str(root), "-c", "core.autocrlf=false", *args]
    try:
        completed = subprocess.run(command, capture_output=True, text=not binary, check=False)
    except OSError as exc:
        raise BuildError(f"cannot run git: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr or completed.stdout
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        raise BuildError(f"git {' '.join(args)} failed: {str(detail).strip()}")
    return completed.stdout


def git_commit(root: Path | str | None = None, commit: str | None = None) -> str:
    """Resolve ``commit`` (default ``HEAD``) to 40 lowercase hex."""
    root_path = _repo_root(root)
    if commit is None:
        resolved = str(_git(root_path, "rev-parse", "--verify", "HEAD")).strip().lower()
    else:
        resolved = str(_git(root_path, "rev-parse", "--verify", f"{commit}^{{commit}}")).strip().lower()
    if _HEX40.fullmatch(resolved) is None:
        raise BuildError(f"commit is not 40 lowercase hex: {resolved!r}")
    return resolved


def git_tree(root: Path | str | None, commit: str) -> str:
    """``git rev-parse <commit>^{tree}`` -- the contract's tree identity."""
    tree = str(_git(_repo_root(root), "rev-parse", f"{commit}^{{tree}}")).strip().lower()
    if _HEX40.fullmatch(tree) is None:
        raise BuildError(f"git tree is not 40 lowercase hex: {tree!r}")
    return tree


def git_branch(root: Path | str | None = None) -> str:
    try:
        branch = str(_git(_repo_root(root), "rev-parse", "--abbrev-ref", "HEAD")).strip()
    except BuildError:
        return "HEAD"
    return branch or "HEAD"


def git_tag(root: Path | str | None, commit: str) -> str | None:
    try:
        tag = str(_git(_repo_root(root), "describe", "--tags", "--exact-match", commit)).strip()
    except BuildError:
        return None
    return tag or None


def tracked_dirty(root: Path | str | None = None) -> list[str]:
    """Tracked-only dirt; ignored/untracked delivery files do not count."""
    output = str(
        _git(_repo_root(root), "status", "--porcelain", "--untracked-files=no")
    ).strip()
    return [line for line in output.splitlines() if line.strip()]


def _tool_version(command: str, *args: str) -> str:
    executable = shutil.which(command)
    if executable is None:
        return "absent"
    try:
        completed = subprocess.run(
            [executable, *args], capture_output=True, text=True, check=False, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    line = (completed.stdout or completed.stderr or "").strip().splitlines()
    return line[0].strip() if line else "unknown"


def _os_image(image: str | None) -> str:
    if image:
        return image
    release = platform.release() or "unknown"
    return f"host:{release}"


def runner_versions(image: str | None = None) -> dict[str, str]:
    """Manifest ``runner`` block: host facts recorded with the artifact."""
    return {
        "os": platform.system().lower() or "unknown",
        "image": _os_image(image),
        "arch": platform.machine() or "unknown",
        "python": platform.python_version(),
        "uv": _tool_version("uv", "--version"),
        "node": _tool_version("node", "--version"),
        "npm": _tool_version("npm", "--version"),
        "git": _tool_version("git", "--version"),
    }


# --------------------------------------------------------------------------- #
# Artifact construction
# --------------------------------------------------------------------------- #


@dataclass
class BuildRun:
    """One full build of one commit (input for the reproducibility record)."""

    archive_sha256: str
    archive_bytes: int
    zip_sha256: str
    zip_bytes: int
    files_sha256: str
    files_count: int
    zip_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "git_archive_sha256": "sha256:" + self.archive_sha256,
            "git_archive_bytes": self.archive_bytes,
            "artifact_sha256": "sha256:" + self.zip_sha256,
            "artifact_bytes": self.zip_bytes,
            "files_sha256": "sha256:" + self.files_sha256,
            "files_count": self.files_count,
        }


@dataclass
class BuildResult:
    status: str
    manifest: dict[str, Any]
    receipt: dict[str, Any]
    runs: list[BuildRun]
    artifact_path: Path
    manifest_path: Path
    receipt_path: Path
    check: CheckResult | None = None
    problems: list[str] = field(default_factory=list)
    source_evidence_note: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "pass" and not self.problems


def _sha256_file_stream(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:  # windows-footgun: ok - binary hash
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_archive(root: Path, commit: str, destination: Path) -> tuple[str, int]:
    """Run ``git archive --format=zip -9 <commit>`` into ``destination``."""
    command = [
        "git",
        "-C",
        str(root),
        "-c",
        "core.autocrlf=false",
        "archive",
        "--format=zip",
        f"-{ZIP_COMPRESSION_LEVEL}",
        commit,
    ]
    try:
        with open(destination, "wb") as handle:  # windows-footgun: ok - binary archive
            completed = subprocess.run(command, stdout=handle, stderr=subprocess.PIPE, check=False)
    except OSError as exc:
        raise BuildError(f"cannot run git archive: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or b"").decode("utf-8", "replace").strip()
        raise BuildError(f"git archive failed: {detail}")
    return _sha256_file_stream(destination), destination.stat().st_size


def build_files_manifest(archive: zipfile.ZipFile) -> tuple[str, int]:
    """``FILES.sha256`` over the **zip member bytes** (post ``.gitattributes``)."""
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = info.filename
        if name in seen:
            raise BuildError(f"duplicate zip entry in git archive: {name!r}")
        seen.add(name)
        digest = hashlib.sha256()
        with archive.open(info) as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        entries.append((name, digest.hexdigest()))
    entries.sort(key=lambda item: item[0])
    text = "".join(f"{digest}  {path}\n" for path, digest in entries)
    return text, len(entries)


def _zip_external_attr(info: zipfile.ZipInfo) -> int:
    """Preserve the git mode in the zip; ``git archive`` writes 0 on this repo."""
    raw = (info.external_attr >> 16) & 0xFFFF
    if raw == 0:
        raw = 0o100644
    return raw << 16


def write_deterministic_zip(
    archive_path: Path,
    files_text: str,
    destination: Path,
) -> tuple[str, int, str, int]:
    """Rebuild the archive with sorted members and fixed metadata.

    Returns ``(zip_sha256, zip_bytes, files_sha256, files_count)``.  The content
    bytes of every member are copied unchanged, so the per-path hashes in
    ``FILES.sha256`` (computed from the git archive) still hold.
    """
    files_bytes = files_text.encode("utf-8")
    files_sha = hashlib.sha256(files_bytes).hexdigest()
    files_count = len([line for line in files_text.splitlines() if line])
    if files_count == 0:
        raise BuildError("git archive produced no files; refusing to write an empty artifact")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        with zipfile.ZipFile(archive_path) as source, zipfile.ZipFile(
            temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=ZIP_COMPRESSION_LEVEL
        ) as target:
            members = {info.filename: info for info in source.infolist() if not info.is_dir()}
            if FILES_MANIFEST in members:
                raise BuildError(
                    f"git archive unexpectedly contains {FILES_MANIFEST!r}; it must be synthetic"
                )
            # FILES.sha256 is sorted into its natural position so the rebuilt zip
            # has one deterministic member order (no "synthetic entry last" rule).
            for name in sorted([*members, FILES_MANIFEST]):
                if name == FILES_MANIFEST:
                    payload = files_bytes
                    external_attr = 0o100644 << 16
                else:
                    info = members[name]
                    payload = source.read(name)
                    external_attr = _zip_external_attr(info)
                entry = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.create_system = 3  # Unix, so the mode below is meaningful
                entry.external_attr = external_attr
                target.writestr(entry, payload)
    except OSError as exc:
        raise BuildError(f"cannot write artifact zip {destination}: {exc}") from exc
    os.replace(temporary, destination)
    return (
        _sha256_file_stream(destination),
        destination.stat().st_size,
        files_sha,
        files_count,
    )


def _read_member_text(archive: zipfile.ZipFile, name: str) -> str:
    try:
        payload = archive.read(name)
    except KeyError as exc:
        raise BuildError(f"required zip member missing: {name}") from exc
    if payload.startswith(_UTF8_BOM):
        payload = payload[len(_UTF8_BOM) :]
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BuildError(f"zip member {name} is not UTF-8: {exc}") from exc


def read_zip_versions(zip_path: Path) -> dict[str, str]:
    """Three release versions + upstream tag, all read from files inside the zip."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = {info.filename for info in archive.infolist() if not info.is_dir()}
            missing = [name for name in REQUIRED_MEMBERS if name not in names]
            if missing:
                raise BuildError(f"artifact zip is missing required members: {missing}")
            product = _read_member_text(archive, "GUGU_VERSION").strip()
            pyproject_text = _read_member_text(archive, "pyproject.toml")
            desktop_text = _read_member_text(archive, "apps/desktop/package.json")
            upstream_text = _read_member_text(archive, "arch/upstream.lock")
    except zipfile.BadZipFile as exc:
        raise BuildError(f"{zip_path} is not a readable zip: {exc}") from exc
    try:
        kernel = str(tomllib.loads(pyproject_text)["project"]["version"])
    except (tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise BuildError(f"cannot read pyproject.toml [project].version from zip: {exc}") from exc
    try:
        desktop = str(json.loads(desktop_text)["version"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise BuildError(f"cannot read apps/desktop/package.json#version from zip: {exc}") from exc
    try:
        upstream_tag = str(json.loads(upstream_text)["tag"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise BuildError(f"cannot read arch/upstream.lock#tag from zip: {exc}") from exc
    return {
        "product_version": product,
        "upstream_kernel_version": kernel,
        "desktop_version": desktop,
        "upstream_tag": upstream_tag,
    }


def lockfile_hashes(zip_path: Path) -> dict[str, str]:
    """Lockfile digests as the checker recomputes them: from zip member bytes."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = {info.filename for info in archive.infolist() if not info.is_dir()}
            hashes: dict[str, str] = {}
            for name in LOCKFILES:
                if name not in names:
                    raise BuildError(f"artifact zip is missing lockfile: {name}")
                hashes[name] = "sha256:" + sha256_bytes(archive.read(name))
            return hashes
    except zipfile.BadZipFile as exc:
        raise BuildError(f"{zip_path} is not a readable zip: {exc}") from exc


def _build_fingerprint(commit: str, tree: str, files_sha256: str) -> str:
    canonical = json.dumps(
        {"commit": commit, "git_tree": tree, "files_sha256": files_sha256},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + sha256_bytes(canonical.encode("utf-8"))


def build_once(
    root: Path,
    commit: str,
    work_dir: Path,
    *,
    image: str | None = None,
) -> BuildRun:
    """One complete build: git archive -> FILES.sha256 -> deterministic zip."""
    work_dir.mkdir(parents=True, exist_ok=True)
    archive_path = work_dir / "git-archive.zip"
    archive_sha, archive_bytes = _git_archive(root, commit, archive_path)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            files_text, files_count = build_files_manifest(archive)
    except zipfile.BadZipFile as exc:
        raise BuildError(f"git archive output is not a readable zip: {exc}") from exc
    zip_path = work_dir / ARTIFACT_NAME
    zip_sha, zip_bytes, files_sha, written_count = write_deterministic_zip(
        archive_path, files_text, zip_path
    )
    if written_count != files_count:
        raise BuildError(
            f"files_count drifted while writing the zip ({files_count} -> {written_count})"
        )
    return BuildRun(
        archive_sha256=archive_sha,
        archive_bytes=archive_bytes,
        zip_sha256=zip_sha,
        zip_bytes=zip_bytes,
        files_sha256=files_sha,
        files_count=files_count,
        zip_path=zip_path,
    )


def reproducibility_record(first: BuildRun, second: BuildRun) -> tuple[dict[str, Any], list[str]]:
    """Compare two runs; ``byte-identical`` is the only PASS-eligible mode."""
    compared = [
        ("zip_sha256", "artifact_sha256"),
        ("zip_bytes", "artifact_bytes"),
        ("archive_sha256", "git_archive_sha256"),
        ("archive_bytes", "git_archive_bytes"),
        ("files_sha256", "files_sha256"),
        ("files_count", "files_count"),
    ]
    problems: list[str] = []
    for attribute, label in compared:
        left = getattr(first, attribute)
        right = getattr(second, attribute)
        if left != right:
            problems.append(
                f"reproducibility: {label} differs between build 1 and build 2 "
                f"({left!r} != {right!r})"
            )
    record = {
        "mode": "byte-identical",
        "verdict": "equal" if not problems else "differ",
        "builds": [first.to_dict(), second.to_dict()],
        "compared_fields": [label for _attribute, label in compared],
        "exclusions": ["built_at"],
    }
    return record, problems


# --------------------------------------------------------------------------- #
# Manifest / receipt assembly
# --------------------------------------------------------------------------- #


def _load_source_evidence(
    root: Path,
    evidence_path: Path,
    commit: str,
    *,
    required: bool,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return ``(evidence_file_ref, note)`` for a same-commit source evidence.

    When ``required`` is true an unusable document is a build error; otherwise it
    is omitted from the receipt and the reason is recorded (never silently PASSed).
    """
    from prodline.stages.source import SourceError, verify_source_evidence

    if not evidence_path.is_file():
        note = f"source evidence absent: {_display_path(evidence_path, root)}"
        if required:
            raise BuildError(note)
        return None, note
    try:
        data = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        note = f"source evidence unreadable: {exc}"
        if required:
            raise BuildError(note)
        return None, note
    problems = verify_source_evidence(data, root)
    if data.get("commit") != commit:
        problems.append(
            f"source evidence commit {data.get('commit')!r} != build commit {commit!r}"
        )
    if problems:
        note = "source evidence not used: " + "; ".join(problems)
        if required:
            raise BuildAssertionError(problems)
        return None, note
    return (
        {
            "path": _display_path(evidence_path, root),
            "sha256": "sha256:" + sha256_file(evidence_path),
            "note": "P1 source stage evidence, verified against the same commit",
        },
        None,
    )


def _json_bytes(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def assemble_manifest(
    *,
    root: Path,
    commit: str,
    tree: str,
    zip_path: Path,
    run: BuildRun,
    receipt_sha256: str,
    source_evidence: dict[str, Any] | None,
    image: str | None = None,
) -> dict[str, Any]:
    versions = read_zip_versions(zip_path)
    manifest: dict[str, Any] = {
        "schema": "artifact-manifest/v1",
        "commit": commit,
        "commit_short": commit[:10],
        "git_tree": tree,
        "branch": git_branch(root),
        "provenance": PROVENANCE,
        "product_version": versions["product_version"],
        "desktop_version": versions["desktop_version"],
        "upstream_kernel_version": versions["upstream_kernel_version"],
        "upstream_tag": versions["upstream_tag"],
        "artifact_sha256": "sha256:" + run.zip_sha256,
        "artifact_bytes": run.zip_bytes,
        "files_sha256": "sha256:" + run.files_sha256,
        "files_count": run.files_count,
        "files_manifest": FILES_MANIFEST,
        "built_at": _utc_now(),
        "runner": runner_versions(image),
        "dirty": False,
        "lockfiles": lockfile_hashes(zip_path),
        "includes": dict(INCLUDES),
        "excludes": list(EXCLUDES),
        "build_receipt_sha256": "sha256:" + receipt_sha256,
        "producer": {"stage": STAGE_ID, "tool": TOOL, "contract_version": CONTRACT_VERSION},
        "git_archive": {
            "format": "zip",
            "compression_level": ZIP_COMPRESSION_LEVEL,
            "generator": "git archive --format=zip",
            "git_version": _tool_version("git", "--version"),
        },
    }
    if source_evidence is not None:
        manifest["source_evidence_sha256"] = source_evidence["sha256"]
    return manifest


def assemble_receipt(
    *,
    root: Path,
    commit: str,
    tree: str,
    zip_path: Path,
    zip_sha256: str,
    zip_bytes: int,
    files_sha256: str,
    files_count: int,
    reproducibility: dict[str, Any],
    status: str,
    source_evidence: dict[str, Any] | None,
    image: str | None = None,
) -> dict[str, Any]:
    versions = read_zip_versions(zip_path)
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "commit": commit,
        "commit_short": commit[:10],
        "generated_at": _utc_now(),
        "stage": STAGE_ID,
        "provenance": PROVENANCE,
        "status": status,
        "tag": git_tag(root, commit),
        "branch": git_branch(root),
        "dirty": False,
        "os": platform.system().lower() or "unknown",
        "runner": runner_versions(image),
        "product_version": versions["product_version"],
        "desktop_version": versions["desktop_version"],
        "upstream_tag": versions["upstream_tag"],
        "upstream_kernel_version": versions["upstream_kernel_version"],
        "toolchain": {
            "python": platform.python_version(),
            "git": _tool_version("git", "--version"),
            "uv": _tool_version("uv", "--version"),
            "node": _tool_version("node", "--version"),
            "npm": _tool_version("npm", "--version"),
        },
        "lockfiles": lockfile_hashes(zip_path),
        "artifacts": [
            {
                "path": _display_path(zip_path, root),
                "sha256": "sha256:" + zip_sha256,
                "bytes": zip_bytes,
                "kind": "artifact-zip",
            }
        ],
        "clean_build": status == "pass",
        "clean_method": "git status --porcelain --untracked-files=no (tracked tree clean)",
        "reproducibility": reproducibility,
        "build_fingerprint": _build_fingerprint(commit, tree, "sha256:" + files_sha256),
        "producer": {"stage": STAGE_ID, "tool": TOOL, "contract_version": CONTRACT_VERSION},
    }
    if source_evidence is not None:
        receipt["evidence"] = [source_evidence]
    return receipt


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def _run_artifact_check(
    manifest: dict[str, Any],
    *,
    zip_path: Path,
    commit: str,
    receipt_path: Path,
    root: Path,
) -> CheckResult:
    try:
        return check_artifact(
            manifest,
            zip_path=zip_path,
            expect_commit=commit,
            build_receipt=receipt_path,
            repo=root,
            verify_files=True,
            verify_git_tree=True,
        )
    except ContractError as exc:
        raise BuildError(f"artifact check could not run: {exc}") from exc


def build_artifact(
    root: Path | str | None = None,
    *,
    commit: str | None = None,
    outdir: Path | str | None = None,
    image: str | None = None,
    source_evidence: Path | str | None = None,
    require_source_evidence: bool = False,
) -> BuildResult:
    """Build the delivery set for ``commit`` and self-check it.

    ``require_source_evidence`` is for CI callers that want a missing/stale P1
    evidence document to be a hard error; the default records the omission in the
    build receipt instead.
    """
    root_path = _repo_root(root)
    if not root_path.is_dir():
        raise BuildError(f"root is not a directory: {root_path}")
    out_path = Path(outdir) if outdir is not None else root_path / DEFAULT_OUTDIR
    if not out_path.is_absolute():
        out_path = root_path / out_path

    dirty = tracked_dirty(root_path)
    if dirty:
        raise BuildAssertionError(
            ["tracked working tree is dirty; commit or stash before building: " + "; ".join(dirty[:10])]
        )

    resolved_commit = git_commit(root_path, commit)
    tree = git_tree(root_path, resolved_commit)

    evidence_path = (
        Path(source_evidence)
        if source_evidence is not None
        else root_path / DEFAULT_SOURCE_EVIDENCE
    )
    if not evidence_path.is_absolute():
        evidence_path = root_path / evidence_path
    evidence_ref, evidence_note = _load_source_evidence(
        root_path,
        evidence_path,
        resolved_commit,
        required=require_source_evidence,
    )

    work_root = Path(tempfile.mkdtemp(prefix="gugu-build-"))
    try:
        first = build_once(root_path, resolved_commit, work_root / "run1", image=image)
        second = build_once(root_path, resolved_commit, work_root / "run2", image=image)
        record, problems = reproducibility_record(first, second)
        status = "pass" if not problems else "fail"

        artifact_path = out_path / ARTIFACT_NAME
        manifest_path = out_path / MANIFEST_NAME
        receipt_path = out_path / RECEIPT_NAME
        check_path = out_path / CHECK_NAME
        out_path.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(first.zip_path, artifact_path)
        if _sha256_file_stream(artifact_path) != first.zip_sha256:
            raise BuildError("artifact copy changed bytes; refusing to write a receipt")

        receipt = assemble_receipt(
            root=root_path,
            commit=resolved_commit,
            tree=tree,
            zip_path=artifact_path,
            zip_sha256=first.zip_sha256,
            zip_bytes=first.zip_bytes,
            files_sha256=first.files_sha256,
            files_count=first.files_count,
            reproducibility=record,
            status=status,
            source_evidence=evidence_ref,
            image=image,
        )
        receipt_bytes = _json_bytes(receipt)
        manifest = assemble_manifest(
            root=root_path,
            commit=resolved_commit,
            tree=tree,
            zip_path=artifact_path,
            run=first,
            receipt_sha256=sha256_bytes(receipt_bytes),
            source_evidence=evidence_ref,
            image=image,
        )
        _write_atomic(receipt_path, receipt_bytes)
        _write_atomic(manifest_path, _json_bytes(manifest))

        check = _run_artifact_check(
            manifest,
            zip_path=artifact_path,
            commit=resolved_commit,
            receipt_path=receipt_path,
            root=root_path,
        )
        if not check.ok:
            # Never leave a PASS receipt next to an artifact that failed its gate.
            problems.extend(check.problems)
            receipt["status"] = "fail"
            receipt["clean_build"] = False
            receipt["problems"] = list(check.problems)
            receipt_bytes = _json_bytes(receipt)
            manifest["build_receipt_sha256"] = "sha256:" + sha256_bytes(receipt_bytes)
            _write_atomic(receipt_path, receipt_bytes)
            _write_atomic(manifest_path, _json_bytes(manifest))
            check = _run_artifact_check(
                manifest,
                zip_path=artifact_path,
                commit=resolved_commit,
                receipt_path=receipt_path,
                root=root_path,
            )

        # Record repo-relative paths + the final manifest hash in the gate output.
        # ``check_artifact`` sets the zip hash but not the manifest identity (the
        # CLI fills that in); the delivery-side JSON is more useful with both.
        check.manifest_path = _display_path(manifest_path, root_path)
        check.manifest_sha256 = sha256_file(manifest_path)
        check.artifact_path = _display_path(artifact_path, root_path)
        check.artifact_sha256 = sha256_file(artifact_path)
        _write_atomic(
            check_path,
            (json.dumps(check.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(
                "utf-8"
            ),
        )
        result = BuildResult(
            status=receipt["status"],
            manifest=manifest,
            receipt=receipt,
            runs=[first, second],
            artifact_path=artifact_path,
            manifest_path=manifest_path,
            receipt_path=receipt_path,
            check=check,
            problems=list(problems),
            source_evidence_note=evidence_note,
        )
        return result
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m prodline.stages.build",
        description="Build artifact.zip + artifact-manifest.json + build-receipt/v1 (A02).",
    )
    parser.add_argument("--root", type=Path, default=None, help="checkout root (default: this repo)")
    parser.add_argument("--commit", default=None, help="commit to archive (default: git HEAD)")
    parser.add_argument(
        "--outdir", type=Path, default=None, help=f"delivery directory (default: {DEFAULT_OUTDIR})"
    )
    parser.add_argument("--image", default=None, help="manifest runner.image (default: host:<kernel>)")
    parser.add_argument(
        "--source-evidence",
        type=Path,
        default=None,
        help=f"P1 source evidence to verify and cite (default: {DEFAULT_SOURCE_EVIDENCE})",
    )
    parser.add_argument(
        "--require-source-evidence",
        action="store_true",
        help="fail instead of omitting a missing/stale source evidence document",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = build_artifact(
            args.root,
            commit=args.commit,
            outdir=args.outdir,
            image=args.image,
            source_evidence=args.source_evidence,
            require_source_evidence=args.require_source_evidence,
        )
    except BuildAssertionError as exc:
        for problem in exc.problems:
            print(f"build: {problem}", file=sys.stderr)
        return 1
    except BuildError as exc:
        print(f"build: {exc}", file=sys.stderr)
        return 2

    if not args.quiet:
        print(f"build: commit {result.receipt['commit']} status {result.status}")
        print(f"  artifact {result.artifact_path} ({result.manifest['artifact_bytes']} bytes)")
        print(f"  manifest {result.manifest_path}")
        print(f"  receipt  {result.receipt_path}")
        first, second = result.runs
        print(
            "  reproducibility "
            f"{result.receipt['reproducibility']['verdict']} "
            f"(sha256:{first.zip_sha256} == sha256:{second.zip_sha256})"
        )
        if result.source_evidence_note:
            print(f"  source-evidence: {result.source_evidence_note}")
        if result.check is not None:
            print(f"  artifact-check: {'ok' if result.check.ok else 'invalid'}")
        for problem in result.problems:
            print(f"  ERROR {problem}", file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover - exercised through main() in tests
    raise SystemExit(main())
