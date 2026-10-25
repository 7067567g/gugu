"""Artifact checker: verify ``artifact.zip`` + ``artifact-manifest.json`` (A01).

This is the executable half of the frozen A-to-B delivery contract in
``prodline/contracts/artifact-manifest.v1.json``.  It validates the manifest
against the schema, then -- when the zip is supplied -- verifies the things a
schema cannot express:

* the zip bytes match ``artifact_sha256`` / ``artifact_bytes``;
* every entry is a safe relative path (no zip-slip / absolute / drive / ``\\``);
* forbidden trees (``.git``, ``node_modules``, ``.venv``, desktop build dirs,
  ``artifacts``, ``__pycache__``) are absent;
* ``FILES.sha256`` is canonical (sorted, LF, two spaces, no BOM), its own
  sha256 and line count match the manifest, and every listed file matches the
  zip member byte-for-byte (the set must be equal, no extra members);
* the three version lines and ``upstream_tag`` equal the files inside the zip;
* ``lockfiles`` equal the zip members;
* ``git_tree`` is rebuilt from the zip members (excluding the synthetic
  ``FILES.sha256``) and compared; with ``--repo`` the commit->tree mapping is
  verified with git too;
* ``--build-receipt`` re-hashes the build receipt;
* ``dirty`` is ``false`` (schema const) and non-``real`` provenance carries a
  ``fixture_reason`` (gate semantics, because the schema subset has no if/then).

The manifest is *never* recomputed from the underlying build facts; like
``receipt_check`` this module checks evidence, it does not produce it.

CLI::

    python -m prodline.gates.artifact_check --zip artifact.zip --manifest artifact-manifest.json
    python -m prodline.gates.artifact_check --manifest artifact-manifest.json --format json

Exit codes: ``0`` pass, ``1`` at least one problem, ``2`` bad input/configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
import sys
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from prodline.schema_subset import assert_supported_schema, validate

PRODLINE_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = PRODLINE_ROOT / "contracts" / "artifact-manifest.v1.json"

#: Tool-output schema; not a receipt and not registered in ``pipeline.yaml``.
CHECK_SCHEMA = "gugu.prodline-artifact-check/v1"

FILES_MANIFEST = "FILES.sha256"
REQUIRED_MEMBERS = ("pyproject.toml", "GUGU_VERSION", FILES_MANIFEST, "scripts/install.ps1")
FORBIDDEN_PREFIXES = (
    ".git/",
    "node_modules/",
    ".venv/",
    "apps/desktop/dist/",
    "apps/desktop/build/",
    "apps/desktop/release/",
    "artifacts/",
    "__pycache__/",
)
LOCKFILES = ("uv.lock", "package-lock.json")

_LINE = re.compile(r"^([0-9a-f]{64})  (.+)$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")


class ContractError(RuntimeError):
    """The contract, a manifest, or the CLI input is unusable."""


@dataclass
class CheckResult:
    manifest_path: str | None = None
    manifest_sha256: str | None = None
    artifact_path: str | None = None
    artifact_sha256: str | None = None
    checks: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CHECK_SCHEMA,
            "ok": self.ok,
            "manifest": self.manifest_path,
            "manifest_sha256": self.manifest_sha256,
            "artifact": self.artifact_path,
            "artifact_sha256": self.artifact_sha256,
            "checks": list(self.checks),
            "problems": list(self.problems),
        }


# --------------------------------------------------------------------------- #
# Hashing / loading
# --------------------------------------------------------------------------- #


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path | str) -> str:
    return sha256_bytes(Path(path).read_bytes())


def load_contract(path: Path | str | None = None) -> dict[str, Any]:
    contract_path = Path(path) if path is not None else CONTRACT_PATH
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot load artifact contract {contract_path}: {exc}") from exc
    try:
        assert_supported_schema(contract)
    except Exception as exc:  # UnsupportedKeyword / UnresolvableRef
        raise ContractError(
            f"artifact contract {contract_path} is outside the supported subset: {exc}"
        ) from exc
    return contract


def load_manifest(path: Path | str) -> tuple[dict[str, Any], str]:
    manifest_path = Path(path)
    try:
        payload = manifest_path.read_bytes()
    except OSError as exc:
        raise ContractError(f"cannot read manifest {manifest_path}: {exc}") from exc
    try:
        manifest = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"manifest {manifest_path} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ContractError(f"manifest {manifest_path} is not a JSON object")
    return manifest, sha256_bytes(payload)


# --------------------------------------------------------------------------- #
# Manifest-only checks
# --------------------------------------------------------------------------- #


def check_manifest(
    manifest: dict[str, Any],
    *,
    contract: dict[str, Any] | None = None,
    expect_commit: str | None = None,
) -> list[str]:
    """Schema + manifest-local semantics; returns every problem."""
    schema = load_contract() if contract is None else contract
    problems = validate(manifest, schema)

    provenance = manifest.get("provenance")
    if provenance in {"fixture", "mock", "accelerated"}:
        reason = manifest.get("fixture_reason")
        if not isinstance(reason, str) or not reason.strip():
            problems.append(
                f"semantics: provenance {provenance!r} requires a non-empty fixture_reason"
            )
    if expect_commit is not None:
        if _HEX40.fullmatch(expect_commit) is None:
            raise ContractError(f"--expect-commit is not 40 lowercase hex: {expect_commit!r}")
        if manifest.get("commit") != expect_commit:
            problems.append(
                f"commit mismatch: manifest has {manifest.get('commit')!r}, expected {expect_commit!r}"
            )
    return problems


# --------------------------------------------------------------------------- #
# Zip inspection
# --------------------------------------------------------------------------- #


def _entry_problem(name: str) -> str | None:
    if not name:
        return "empty zip entry name"
    if "\x00" in name:
        return f"zip entry contains NUL: {name!r}"
    if name.startswith(("/", "\\")):
        return f"zip entry is absolute: {name!r}"
    if "\\" in name:
        return f"zip entry uses a backslash separator: {name!r}"
    if re.match(r"^[A-Za-z]:", name):
        return f"zip entry starts with a drive letter: {name!r}"
    parts = name.rstrip("/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return f"zip entry escapes or is not canonical: {name!r}"
    return None


def _forbidden(name: str) -> str | None:
    lowered = name.lower()
    for prefix in FORBIDDEN_PREFIXES:
        if lowered.startswith(prefix):
            return f"forbidden tree in zip: {name!r} (matches {prefix!r})"
    return None


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        return archive.read(name)
    except KeyError as exc:
        raise ContractError(f"zip member disappeared while reading: {name!r}") from exc


def _decode_member(archive: zipfile.ZipFile, name: str, label: str, problems: list[str]) -> str | None:
    try:
        payload = archive.read(name)
    except KeyError:
        problems.append(f"{label}: {name} is missing from the zip")
        return None
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        problems.append(f"{label}: {name} is not valid UTF-8: {exc}")
        return None


def _parse_files_manifest(text: str, problems: list[str]) -> list[tuple[str, str]]:
    if text.startswith("\ufeff"):
        problems.append(f"files: {FILES_MANIFEST} starts with a UTF-8 BOM")
        text = text.lstrip("\ufeff")
    if "\r" in text:
        problems.append(f"files: {FILES_MANIFEST} must use LF line endings only")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text and not text.endswith("\n"):
        problems.append(f"files: {FILES_MANIFEST} must end with a trailing LF")

    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(text.split("\n"), start=1):
        if not raw:
            continue
        match = _LINE.fullmatch(raw)
        if match is None:
            problems.append(
                f"files: line {index} is not '<64 hex><SP><SP><path>': {raw[:120]!r}"
            )
            continue
        digest, path = match.group(1), match.group(2)
        entry_problem = _entry_problem(path)
        if entry_problem:
            problems.append(f"files: line {index}: {entry_problem}")
            continue
        if path == FILES_MANIFEST:
            problems.append(f"files: {FILES_MANIFEST} must not list itself")
            continue
        if path in seen:
            problems.append(f"files: duplicate path in {FILES_MANIFEST}: {path!r}")
            continue
        seen.add(path)
        entries.append((path, digest))

    paths = [path for path, _ in entries]
    if paths != sorted(paths):
        problems.append(f"files: {FILES_MANIFEST} entries are not sorted by path")
    return entries


def _git_mode(info: zipfile.ZipInfo) -> int:
    """Map zip external attributes to the git tree mode."""
    raw = (info.external_attr >> 16) & 0xFFFF
    if raw == 0:
        return 0o100644
    if stat.S_ISLNK(raw):
        return 0o120000
    if raw & 0o111:
        return 0o100755
    return 0o100644


def git_tree_digest(files: dict[str, tuple[int, bytes]]) -> str:
    """Rebuild the git tree object id from ``{path: (mode, bytes)}``.

    ``mode`` is the git mode (``0o100644`` / ``0o100755`` / ``0o120000``).
    Raises ``ValueError`` when a path is both a file and a directory.
    """
    root: dict[str, Any] = {}
    for path, (mode, data) in files.items():
        parts = path.split("/")
        node = root
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f"path {path!r} is both a file and a directory")
            node = child
        leaf = parts[-1]
        if isinstance(node.get(leaf), dict):
            raise ValueError(f"path {path!r} is both a file and a directory")
        node[leaf] = (mode, data)

    def write(node: dict[str, Any]) -> bytes:
        entries: list[tuple[bytes, bytes, bytes, bool]] = []
        for name, value in node.items():
            encoded = name.encode("utf-8")
            if isinstance(value, dict):
                entries.append((encoded, b"40000", write(value), True))
            else:
                mode, data = value
                blob = hashlib.sha1(b"blob %d\x00" % len(data) + data).digest()
                entries.append((encoded, f"{mode:o}".encode("ascii"), blob, False))
        entries.sort(key=lambda item: item[0] + (b"/" if item[3] else b""))
        payload = b"".join(
            mode + b" " + name + b"\x00" + digest
            for name, mode, digest, _is_tree in entries
        )
        return hashlib.sha1(b"tree %d\x00" % len(payload) + payload).digest()

    return write(root).hex()


# --------------------------------------------------------------------------- #
# Full artifact check
# --------------------------------------------------------------------------- #


def _check_version_bindings(
    archive: zipfile.ZipFile,
    manifest: dict[str, Any],
    problems: list[str],
) -> None:
    product = _decode_member(archive, "GUGU_VERSION", "version", problems)
    if product is not None and product.strip() != manifest.get("product_version"):
        problems.append(
            "version: product_version does not match zip GUGU_VERSION "
            f"({manifest.get('product_version')!r} != {product.strip()!r})"
        )

    pyproject = _decode_member(archive, "pyproject.toml", "version", problems)
    if pyproject is not None:
        try:
            project_version = str(tomllib.loads(pyproject)["project"]["version"])
        except (tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
            problems.append(f"version: cannot read pyproject.toml [project].version: {exc}")
        else:
            if project_version != manifest.get("upstream_kernel_version"):
                problems.append(
                    "version: upstream_kernel_version does not match zip pyproject.toml "
                    f"({manifest.get('upstream_kernel_version')!r} != {project_version!r})"
                )

    desktop = _decode_member(archive, "apps/desktop/package.json", "version", problems)
    if desktop is not None:
        try:
            desktop_version = json.loads(desktop)["version"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            problems.append(f"version: cannot read apps/desktop/package.json#version: {exc}")
        else:
            if desktop_version != manifest.get("desktop_version"):
                problems.append(
                    "version: desktop_version does not match zip apps/desktop/package.json "
                    f"({manifest.get('desktop_version')!r} != {desktop_version!r})"
                )

    lock = _decode_member(archive, "arch/upstream.lock", "version", problems)
    if lock is not None:
        try:
            tag = json.loads(lock)["tag"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            problems.append(f"version: cannot read arch/upstream.lock#tag: {exc}")
        else:
            if tag != manifest.get("upstream_tag"):
                problems.append(
                    "version: upstream_tag does not match zip arch/upstream.lock "
                    f"({manifest.get('upstream_tag')!r} != {tag!r})"
                )


def _check_lockfiles(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    manifest: dict[str, Any],
    problems: list[str],
) -> None:
    declared = manifest.get("lockfiles")
    if not isinstance(declared, dict):
        return  # schema already reported it
    for name in LOCKFILES:
        expected = declared.get(name)
        if not isinstance(expected, str):
            continue  # schema already reported it
        if name not in members:
            problems.append(f"lockfiles: {name} is declared but missing from the zip")
            continue
        actual = "sha256:" + sha256_bytes(_read_member(archive, name))
        if actual != expected:
            problems.append(
                f"lockfiles: {name} sha256 mismatch (manifest {expected}, actual {actual})"
            )


def _check_tree(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    manifest: dict[str, Any],
    problems: list[str],
) -> None:
    declared = manifest.get("git_tree")
    if not isinstance(declared, str):
        return  # schema already reported it
    files: dict[str, tuple[int, bytes]] = {}
    for name, info in members.items():
        if name == FILES_MANIFEST:
            continue
        try:
            files[name] = (_git_mode(info), _read_member(archive, name))
        except ContractError as exc:
            problems.append(f"git_tree: {exc}")
            return
    try:
        actual = git_tree_digest(files)
    except ValueError as exc:
        problems.append(f"git_tree: cannot rebuild tree: {exc}")
        return
    if actual != declared:
        problems.append(
            "git_tree: rebuilt tree does not match manifest "
            f"(manifest {declared}, actual {actual}; {FILES_MANIFEST} excluded as synthetic)"
        )


def _check_repo(repo: Path, manifest: dict[str, Any], problems: list[str]) -> None:
    commit = manifest.get("commit")
    tree = manifest.get("git_tree")
    if not isinstance(commit, str) or not isinstance(tree, str):
        return
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", f"{commit}^{{tree}}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise ContractError(f"cannot run git for --repo {repo}: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        problems.append(f"repo: cannot resolve {commit}^{{tree}} in {repo}: {detail}")
        return
    actual = completed.stdout.strip()
    if actual != tree:
        problems.append(
            f"repo: commit {commit} has tree {actual}, manifest says {tree}"
        )


def check_artifact(
    manifest: dict[str, Any],
    *,
    zip_path: Path | str | None = None,
    contract: dict[str, Any] | None = None,
    expect_commit: str | None = None,
    build_receipt: Path | str | None = None,
    repo: Path | str | None = None,
    verify_files: bool = True,
    verify_git_tree: bool = True,
) -> CheckResult:
    """Check a manifest and (when given) its zip; never raises for bad evidence."""
    result = CheckResult()
    result.problems.extend(check_manifest(manifest, contract=contract, expect_commit=expect_commit))
    result.checks.append("manifest-schema")

    if zip_path is None:
        result.checks.append("manifest-only")
        if build_receipt is not None:
            _check_build_receipt(manifest, Path(build_receipt), result.problems)
        return result

    artifact = Path(zip_path)
    try:
        payload = artifact.read_bytes()
    except OSError as exc:
        raise ContractError(f"cannot read artifact zip {artifact}: {exc}") from exc
    result.artifact_path = str(artifact)
    result.artifact_sha256 = sha256_bytes(payload)

    declared_sha = manifest.get("artifact_sha256")
    if "sha256:" + result.artifact_sha256 != declared_sha:
        result.problems.append(
            "artifact: sha256 mismatch "
            f"(manifest {declared_sha}, actual sha256:{result.artifact_sha256})"
        )
    result.checks.append("artifact-sha256")

    if len(payload) != manifest.get("artifact_bytes"):
        result.problems.append(
            "artifact: byte size mismatch "
            f"(manifest {manifest.get('artifact_bytes')}, actual {len(payload)})"
        )
    result.checks.append("artifact-bytes")

    try:
        archive = zipfile.ZipFile(artifact)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ContractError(f"{artifact} is not a readable zip: {exc}") from exc

    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            duplicates = sorted({name for name in names if names.count(name) > 1})
            result.problems.append(f"zip: duplicate entries: {duplicates[:5]}")
        result.checks.append("zip-entry-uniqueness")

        for name in names:
            if name.endswith("/"):
                continue
            problem = _entry_problem(name)
            if problem:
                result.problems.append(problem)
        result.checks.append("zip-path-safety")

        for name in names:
            if name.endswith("/"):
                continue
            problem = _forbidden(name)
            if problem:
                result.problems.append(problem)
        result.checks.append("zip-forbidden-trees")

        members = {info.filename: info for info in infos if not info.is_dir()}
        for required in REQUIRED_MEMBERS:
            if required not in members:
                result.problems.append(f"zip: required root member missing: {required}")
        result.checks.append("zip-required-members")

        includes = manifest.get("includes")
        if isinstance(includes, dict):
            for name in ("pyproject.toml", "GUGU_VERSION", "scripts/install.ps1"):
                if includes.get(name) is True and name not in members:
                    result.problems.append(
                        f"includes: manifest claims {name}=true but the zip has no such member"
                    )

        files_text: str | None = None
        if FILES_MANIFEST in members:
            raw_files = _read_member(archive, FILES_MANIFEST)
            actual_files_sha = "sha256:" + sha256_bytes(raw_files)
            if actual_files_sha != manifest.get("files_sha256"):
                result.problems.append(
                    "files: files_sha256 mismatch "
                    f"(manifest {manifest.get('files_sha256')}, actual {actual_files_sha})"
                )
            try:
                files_text = raw_files.decode("utf-8")
            except UnicodeDecodeError as exc:
                result.problems.append(f"files: {FILES_MANIFEST} is not valid UTF-8: {exc}")
        entries: list[tuple[str, str]] = []
        if files_text is not None:
            entries = _parse_files_manifest(files_text, result.problems)
            if len(entries) != manifest.get("files_count"):
                result.problems.append(
                    "files: files_count mismatch "
                    f"(manifest {manifest.get('files_count')}, actual {len(entries)})"
                )
        result.checks.append("files-manifest")

        if FILES_MANIFEST in members:
            declared_paths = {path for path, _ in entries}
            zip_paths = set(members) - {FILES_MANIFEST}
            missing = sorted(declared_paths - zip_paths)
            extra = sorted(zip_paths - declared_paths)
            if missing:
                result.problems.append(
                    f"files: listed in {FILES_MANIFEST} but missing from zip: {missing[:5]}"
                )
            if extra:
                result.problems.append(
                    f"files: present in zip but not listed in {FILES_MANIFEST}: {extra[:5]}"
                )
            result.checks.append("files-coverage")

        if verify_files:
            for path, expected in entries:
                if path not in members:
                    continue
                actual = sha256_bytes(_read_member(archive, path))
                if actual != expected:
                    result.problems.append(
                        f"files: sha256 mismatch for {path} (listed {expected}, actual {actual})"
                    )
            result.checks.append("files-per-path-sha256")

        _check_version_bindings(archive, manifest, result.problems)
        result.checks.append("version-bindings")
        _check_lockfiles(archive, members, manifest, result.problems)
        result.checks.append("lockfiles")

        if verify_git_tree:
            _check_tree(archive, members, manifest, result.problems)
            result.checks.append("git-tree")

    if build_receipt is not None:
        _check_build_receipt(manifest, Path(build_receipt), result.problems)
        result.checks.append("build-receipt-sha256")

    if repo is not None:
        _check_repo(Path(repo), manifest, result.problems)
        result.checks.append("repo-commit-tree")

    return result


def _check_build_receipt(manifest: dict[str, Any], path: Path, problems: list[str]) -> None:
    try:
        actual = "sha256:" + sha256_file(path)
    except OSError as exc:
        raise ContractError(f"cannot read build receipt {path}: {exc}") from exc
    declared = manifest.get("build_receipt_sha256")
    if actual != declared:
        problems.append(
            "build_receipt: sha256 mismatch "
            f"(manifest {declared}, actual {actual})"
        )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m prodline.gates.artifact_check",
        description="Validate artifact.zip + artifact-manifest.json (artifact-manifest/v1).",
    )
    parser.add_argument("--manifest", type=Path, required=True, help="artifact-manifest.json")
    parser.add_argument("--zip", type=Path, default=None, help="artifact.zip (omit for manifest-only)")
    parser.add_argument("--expect-commit", default=None, help="require this 40-hex commit")
    parser.add_argument("--repo", type=Path, default=None, help="git checkout used to resolve commit->tree")
    parser.add_argument("--build-receipt", type=Path, default=None, help="build-receipt.json to re-hash")
    parser.add_argument("--no-verify-files", action="store_true", help="skip per-path sha256 recompute")
    parser.add_argument("--no-verify-git-tree", action="store_true", help="skip git_tree rebuild")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    try:
        manifest, manifest_sha = load_manifest(args.manifest)
        result = check_artifact(
            manifest,
            zip_path=args.zip,
            expect_commit=args.expect_commit,
            build_receipt=args.build_receipt,
            repo=args.repo,
            verify_files=not args.no_verify_files,
            verify_git_tree=not args.no_verify_git_tree,
        )
    except ContractError as exc:
        print(f"artifact-check: {exc}", file=sys.stderr)
        return 2

    result.manifest_path = str(args.manifest)
    result.manifest_sha256 = manifest_sha

    if args.format == "json":
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True, ensure_ascii=False))
    elif not args.quiet:
        target = str(args.zip) if args.zip is not None else "(manifest only)"
        print(f"artifact-check: {args.manifest} + {target}")
        for check in result.checks:
            print(f"  ran  {check}")
        for problem in result.problems:
            print(f"  ERROR {problem}")
        print(f"  verdict: {'ok' if result.ok else 'invalid'}")

    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover - exercised through main() in tests
    raise SystemExit(main())
