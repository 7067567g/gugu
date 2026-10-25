"""Source stage: read-only version-line and lockfile evidence (P1.2).

The source stage never builds, installs, or mutates the checkout.  It reads the
four release version lines plus the registered auxiliary sources, hashes the two
lockfiles, and returns one JSON-serialisable evidence document for the build
stage (A02) to consume.

This document is **not** a seventh receipt schema: ``pipeline.yaml`` keeps the
fixed six receipts, ``source`` stays receipt-free, and nothing here is added to
``prodline/schemas/``.  The evidence borrows the receipt identity fields
(``schema`` / ``commit`` / ``generated_at`` / ``stage`` / ``provenance`` /
``status``) so the acceptance layer can treat it uniformly without pretending it
is a receipt.

Public interface (consumed by A02, documented in
``docs/architecture/prodline/SOURCE-EVIDENCE.md``)::

    collect_source_evidence(root, commit=None) -> dict
    hash_lockfile(path, root=None) -> dict
    verify_source_evidence(evidence, root=None) -> list[str]

CLI::

    python -m prodline.stages.source --root . --out artifacts/source-evidence.json

Exit codes: ``0`` pass, ``1`` assertion problems, ``2`` unreadable input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Evidence contract id.  Additive changes keep ``/v1``; semantic changes need a new id.
EVIDENCE_SCHEMA = "gugu.prodline-source-evidence/v1"
STAGE_ID = "source"
PROVENANCE = "real"

VERSION_SOURCES_FILE = "version-sources.json"

#: The four release lines this stage asserts.  ``bootstrap_installer_tauri`` is
#: registered as an auxiliary source (B14) but is not one of the release lines.
RELEASE_LINE_GROUPS = (
    "product",
    "upstream_kernel",
    "upstream",
    "upstream_desktop",
)
#: Groups that must exist in ``version-sources.json`` for this stage to pass.
REGISTERED_GROUPS = RELEASE_LINE_GROUPS + ("bootstrap_installer_tauri",)

LOCKFILES = ("uv.lock", "package-lock.json")
LOCKFILE_CANONICALIZATION = "utf-8, LF, no BOM"

_SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_UTF8_BOM = b"\xef\xbb\xbf"


class SourceError(RuntimeError):
    """An input needed by the source stage is missing or unreadable."""


class SourceAssertionError(SourceError):
    """The checkout disagrees with ``version-sources.json`` (or an input is bad).

    ``problems`` always carries every reason, so a caller can print one compact
    failure report instead of stopping at the first drift.
    """

    def __init__(self, problems: list[str], evidence: dict[str, Any] | None = None) -> None:
        self.problems = list(problems)
        self.evidence = evidence
        super().__init__("; ".join(self.problems))


def _repo_root(root: Path | str | None) -> Path:
    return Path(root) if root is not None else Path(__file__).resolve().parents[2]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SourceError(f"cannot read {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise SourceError(f"{path} is not UTF-8: {exc}") from exc


def _read_json(path: Path) -> Any:
    text = _read_text(path)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SourceError(f"{path} is not valid JSON: {exc}") from exc


def _value_at_pointer(root: Path, entry: dict[str, Any]) -> str:
    """Read ``entry['path']`` + dotted ``entry['pointer']`` (``text`` = whole file)."""
    path = root / str(entry["path"])
    pointer = str(entry["pointer"])
    if pointer == "text":
        return _read_text(path).strip()
    suffix = path.suffix
    if suffix in {".json", ".lock"}:  # arch/upstream.lock is JSON with a .lock suffix
        node = _read_json(path)
    elif suffix == ".toml":
        try:
            with open(path, "rb") as handle:  # windows-footgun: ok - binary for tomllib
                node = tomllib.load(handle)
        except OSError as exc:
            raise SourceError(f"cannot read {path}: {exc}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise SourceError(f"{path} is not valid TOML: {exc}") from exc
    else:  # pragma: no cover - loud instead of silently passing a new source type
        raise SourceError(f"unsupported version-source suffix: {entry['path']}")
    for key in pointer.split("."):
        try:
            node = node[key]
        except (KeyError, TypeError) as exc:
            raise SourceError(f"{entry['path']}#{pointer}: missing key {key!r}") from exc
    return str(node)


def read_version_sources(root: Path | str | None = None) -> list[dict[str, Any]]:
    """Load and basic-shape-check the ``sources`` list of ``version-sources.json``."""
    root_path = _repo_root(root)
    path = root_path / VERSION_SOURCES_FILE
    if not path.is_file():
        raise SourceError(f"missing {VERSION_SOURCES_FILE}")
    document = _read_json(path)
    if not isinstance(document, dict):
        raise SourceError(f"{VERSION_SOURCES_FILE} is not a JSON object")
    entries = document.get("sources")
    if not isinstance(entries, list) or not entries:
        raise SourceError(f"{VERSION_SOURCES_FILE} has no non-empty 'sources' list")
    return [entry for entry in entries if isinstance(entry, dict)]


def version_line_problems(
    root: Path | str | None = None,
    entries: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Every way the declared version lines disagree with the checkout on disk."""
    root_path = _repo_root(root)
    problems: list[str] = []
    groups: list[str] = []
    if entries is None:
        try:
            entries = read_version_sources(root_path)
        except SourceError as exc:
            return [str(exc)]

    for entry in entries:
        if not isinstance(entry, dict):
            problems.append(f"manifest entry is not an object: {entry!r}")
            continue
        group = entry.get("group")
        if isinstance(group, str) and group:
            groups.append(group)
        for field in ("path", "pointer", "group", "assert"):
            if not entry.get(field):
                problems.append(f"manifest entry missing '{field}': {entry}")
                break
        else:
            try:
                value = _value_at_pointer(root_path, entry)
            except SourceError as exc:
                problems.append(str(exc))
                continue
            expectation = entry["assert"]
            if expectation == "unverified":
                if entry.get("verified") is not False:
                    problems.append(
                        f"{entry['path']}#{entry['pointer']} is registered 'unverified' "
                        "but is not flagged verified=false"
                    )
            elif expectation == "semver":
                if not _SEMVER.fullmatch(value or ""):
                    problems.append(f"{entry['path']}#{entry['pointer']} is not semver: {value!r}")
            elif value != expectation:
                problems.append(
                    f"{entry['path']}#{entry['pointer']} is {value!r}, manifest asserts {expectation!r}"
                )

    duplicates = sorted({group for group in groups if groups.count(group) > 1})
    if duplicates:
        problems.append(f"manifest has duplicate groups: {duplicates}")
    missing = [group for group in REGISTERED_GROUPS if group not in groups]
    if missing:
        problems.append(f"manifest is missing required groups: {missing}")

    upstream = next((entry for entry in entries if entry.get("group") == "upstream"), None)
    if upstream and str(upstream.get("path", "")).endswith("upstream.lock"):
        try:
            lock = _read_json(root_path / str(upstream["path"]))
        except SourceError:
            pass  # already reported by the value read above
        else:
            commit = str(lock.get("commit", ""))
            expected_commit = str(lock.get("expected_commit", ""))
            if not _HEX40.fullmatch(commit):
                problems.append(f"{upstream['path']}#commit is not 40 lowercase hex: {commit!r}")
            if expected_commit and commit != expected_commit:
                problems.append(
                    f"{upstream['path']}#commit is {commit!r}, expected_commit is {expected_commit!r}"
                )
            if lock.get("verified") is not True:
                problems.append(f"{upstream['path']} is not flagged verified=true")
    return problems


def read_version_lines(
    root: Path | str | None = None,
    entries: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """``group -> {path, pointer, expected, actual, verified}`` for every source entry."""
    root_path = _repo_root(root)
    if entries is None:
        entries = read_version_sources(root_path)
    lines: dict[str, dict[str, Any]] = {}
    for entry in entries:
        group = entry.get("group")
        if not isinstance(group, str) or not group:
            continue
        record: dict[str, Any] = {
            "path": entry.get("path"),
            "pointer": entry.get("pointer"),
            "expected": entry.get("assert"),
            "actual": None,
            "verified": bool(entry.get("verified", False)),
        }
        if entry.get("path") and entry.get("pointer"):
            try:
                record["actual"] = _value_at_pointer(root_path, entry)
            except SourceError:
                record["actual"] = None
        lines[group] = record
    return lines


def canonical_lockfile_bytes(path: Path | str) -> tuple[bytes, dict[str, Any]]:
    """Return ``(canonical_bytes, metadata)`` for a text lockfile.

    Canonical form is UTF-8 with a stripped BOM and LF newlines, matching the A02
    contract (``canonical（LF、无 BOM）``).  The metadata records whether the file
    on disk was already canonical, plus its BOM/EOL state, so a CRLF checkout is
    visible without changing the recomputable hash.
    """
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SourceError(f"cannot read {path}: {exc}") from exc
    bom = raw.startswith(_UTF8_BOM)
    body = raw[len(_UTF8_BOM) :] if bom else raw
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceError(f"{path} is not UTF-8: {exc}") from exc
    if b"\r\n" in body:
        eol = "crlf"
    elif b"\r" in body:
        eol = "cr"
    else:
        eol = "lf"
    canonical = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return canonical, {"bom": bom, "eol": eol, "canonical": (not bom and eol == "lf")}


def hash_lockfile(path: Path | str, root: Path | str | None = None) -> dict[str, Any]:
    """Hash a lockfile by canonical bytes, while also recording the raw bytes hash."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SourceError(f"cannot read {path}: {exc}") from exc
    canonical, meta = canonical_lockfile_bytes(path)
    root_path = _repo_root(root)
    try:
        display = path.resolve().relative_to(root_path.resolve()).as_posix()
    except ValueError:
        display = path.as_posix()
    return {
        "path": display,
        "bytes": len(raw),
        "sha256": "sha256:" + hashlib.sha256(canonical).hexdigest(),
        "raw_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "canonicalization": LOCKFILE_CANONICALIZATION,
        "canonical": meta["canonical"],
        "eol": meta["eol"],
        "bom": meta["bom"],
    }


def git_commit(root: Path | str | None = None) -> str:
    """``git rev-parse HEAD`` as 40 lowercase hex (read-only, no network)."""
    root_path = _repo_root(root)
    try:
        completed = subprocess.run(
            ["git", "-C", str(root_path), "rev-parse", "--verify", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise SourceError(f"cannot run git: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise SourceError(f"cannot resolve git HEAD in {root_path}: {detail}")
    commit = completed.stdout.strip().lower()
    if not _HEX40.fullmatch(commit):
        raise SourceError(f"git HEAD is not 40 lowercase hex: {commit!r}")
    return commit


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def collect_source_evidence(
    root: Path | str | None = None,
    *,
    commit: str | None = None,
    strict: bool = True,
) -> dict[str, Any]:
    """Collect the source evidence document for ``root``.

    ``commit`` overrides ``git rev-parse HEAD`` (tests / archives without ``.git``).
    With ``strict=True`` any drift raises :class:`SourceAssertionError` carrying
    ``.evidence``; with ``strict=False`` the document is returned with
    ``status="fail"`` and a ``problems`` list.
    """
    root_path = _repo_root(root)
    if not root_path.is_dir():
        raise SourceError(f"root is not a directory: {root_path}")
    resolved_commit = (commit or git_commit(root_path)).strip().lower()
    if not _HEX40.fullmatch(resolved_commit):
        raise SourceError(f"commit must be 40 lowercase hex: {resolved_commit!r}")

    problems: list[str] = []
    lines: dict[str, dict[str, Any]] = {}
    try:
        entries = read_version_sources(root_path)
    except SourceError as exc:
        problems.append(str(exc))
    else:
        problems.extend(version_line_problems(root_path, entries=entries))
        lines = read_version_lines(root_path, entries=entries)

    lockfiles: dict[str, dict[str, Any]] = {}
    for name in LOCKFILES:
        path = root_path / name
        if not path.is_file():
            problems.append(f"missing lockfile: {name}")
            continue
        try:
            lockfiles[name] = hash_lockfile(path, root_path)
        except SourceError as exc:
            problems.append(str(exc))

    evidence: dict[str, Any] = {
        "schema": EVIDENCE_SCHEMA,
        "stage": STAGE_ID,
        "commit": resolved_commit,
        "generated_at": _utc_now(),
        "provenance": PROVENANCE,
        "status": "pass" if not problems else "fail",
        "root": str(root_path.resolve()),
        "release_lines": list(RELEASE_LINE_GROUPS),
        "versions": lines,
        "lockfiles": lockfiles,
        "problems": problems,
    }
    if strict and problems:
        raise SourceAssertionError(problems, evidence)
    return evidence


def verify_source_evidence(
    evidence: dict[str, Any],
    root: Path | str | None = None,
) -> list[str]:
    """Recompute the evidence against ``root``; return every disagreement.

    This is the tamper check A02 uses before trusting a source-evidence JSON:
    version files, the manifest, and both lockfiles are read again and compared
    with the recorded bytes/hashes.
    """
    root_path = _repo_root(root)
    if not isinstance(evidence, dict):
        return ["evidence is not a JSON object"]
    problems: list[str] = []
    if evidence.get("schema") != EVIDENCE_SCHEMA:
        problems.append(f"evidence schema is {evidence.get('schema')!r}, expected {EVIDENCE_SCHEMA!r}")
    if evidence.get("stage") != STAGE_ID:
        problems.append(f"evidence stage is {evidence.get('stage')!r}, expected {STAGE_ID!r}")
    if evidence.get("provenance") != PROVENANCE:
        problems.append(f"evidence provenance is {evidence.get('provenance')!r}, expected {PROVENANCE!r}")
    if not _HEX40.fullmatch(str(evidence.get("commit", ""))):
        problems.append(f"evidence commit is not 40 lowercase hex: {evidence.get('commit')!r}")
    if evidence.get("status") != "pass":
        problems.append(f"evidence status is {evidence.get('status')!r}, not 'pass'")

    recorded_lockfiles = evidence.get("lockfiles")
    if not isinstance(recorded_lockfiles, dict):
        problems.append("evidence has no 'lockfiles' object")
    else:
        for name in LOCKFILES:
            recorded = recorded_lockfiles.get(name)
            if not isinstance(recorded, dict):
                problems.append(f"evidence is missing lockfile {name}")
                continue
            path = root_path / name
            if not path.is_file():
                problems.append(f"lockfile disappeared: {name}")
                continue
            try:
                current = hash_lockfile(path, root_path)
            except SourceError as exc:
                problems.append(str(exc))
                continue
            for field in ("sha256", "raw_sha256", "bytes"):
                if recorded.get(field) != current.get(field):
                    problems.append(
                        f"{name}: {field} changed "
                        f"(recorded {recorded.get(field)!r}, current {current.get(field)!r})"
                    )

    try:
        current_lines = read_version_lines(root_path)
    except SourceError as exc:
        problems.append(str(exc))
    else:
        recorded_versions = evidence.get("versions")
        if isinstance(recorded_versions, dict):
            for group, recorded in recorded_versions.items():
                if not isinstance(recorded, dict):
                    continue
                current = current_lines.get(group)
                if current is None:
                    problems.append(f"version line disappeared: {group}")
                elif recorded.get("actual") != current.get("actual"):
                    problems.append(
                        f"version line {group} changed "
                        f"(recorded {recorded.get('actual')!r}, current {current.get('actual')!r})"
                    )
    problems.extend(version_line_problems(root_path))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m prodline.stages.source",
        description="Collect read-only source evidence (version lines + lockfile hashes).",
    )
    parser.add_argument("--root", type=Path, default=None, help="checkout root (default: this repo)")
    parser.add_argument("--commit", default=None, help="40-hex commit override (default: git HEAD)")
    parser.add_argument("--out", type=Path, default=None, help="write evidence JSON here")
    parser.add_argument("--quiet", action="store_true", help="do not echo the document")
    args = parser.parse_args(argv)

    try:
        evidence = collect_source_evidence(args.root, commit=args.commit, strict=False)
    except SourceError as exc:
        print(f"source: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.out.with_name(args.out.name + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(args.out)
        if not args.quiet:
            print(f"source: wrote {args.out}")
    elif not args.quiet:
        print(payload, end="")

    if evidence["status"] != "pass":
        for problem in evidence["problems"]:
            print(f"source: {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main() in tests
    raise SystemExit(main())
