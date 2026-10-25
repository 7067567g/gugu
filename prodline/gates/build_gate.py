"""Windows build-lane receipt recorder + event/tier gate (A03).

Why this exists
---------------
``build-win`` must make two states distinguishable in CI:

* **never ran** -- there is no receipt at all (the lane was skipped, removed,
  or its evidence was lost);
* **ran and failed** -- there is a receipt and it says ``status=fail`` (or it
  contradicts the event it claims to describe).

``prodline.stages.build`` is the Linux artifact stage: it always performs two
internal ``git archive`` builds and therefore always writes
``reproducibility.mode = byte-identical``.  That receipt cannot describe a
Windows lane whose *job-level* decision is "build once on PR, twice on main",
so this module records the job-level evidence from what actually ran, then
checks it.  The receipt stays ``build-receipt/v1`` (optional fields only; the
six v1 schemas are frozen), so ``receipt_check`` / ``accept`` keep working.

Tier semantics
--------------
* ``pr``: exactly one build -> ``single-build`` / ``not-attempted``.
* ``push-main``: exactly two builds ->
  - ``byte-identical`` / ``equal`` when every file matches;
  - ``manifest-normalized`` / ``equal`` when every file matches after the
    explicit ``--exclude`` paths (the known ``install-stamp.json`` ``builtAt``
    carrier is the reason this tier exists);
  - ``differ`` / ``status=fail`` otherwise.  A03 *records* the tier; whether
    ``builtAt`` is the only difference is A04's 7z file-level ruling.

CLI::

    python -m prodline.gates.build_gate record \
      --event pr --commit <40hex> --build-dir artifacts/build/win-1 \
      --exclude resources/install-stamp.json --out artifacts/build/build-receipt-win.json

    python -m prodline.gates.build_gate check \
      artifacts/build/build-receipt-win.json --event pr --expect-commit <40hex>

Exit codes (``check``): ``0`` pass, ``1`` evidence present but rejected
(**ran and failed**), ``2`` unusable input / configuration, ``3`` no receipt
(**never ran**).  ``record`` exits ``0`` when it wrote a receipt (even a
``status=fail`` one: the gate is the verdict), ``2`` when it could not.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from prodline.schema_subset import validate

PRODLINE_ROOT = Path(__file__).resolve().parents[1]
BUILD_RECEIPT_SCHEMA_PATH = PRODLINE_ROOT / "schemas" / "build-receipt.schema.json"

RECEIPT_SCHEMA = "build-receipt/v1"
GATE_SCHEMA = "gugu.prodline-build-gate/v1"
EVENT_PR = "pr"
EVENT_PUSH_MAIN = "push-main"
EVENTS = (EVENT_PR, EVENT_PUSH_MAIN)
PUSH_MAIN_REF = "refs/heads/main"
WINDOWS_IMAGE = "windows-latest"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_INPUT = 2
EXIT_MISSING = 3

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^(sha256:)?[0-9a-f]{64}$")
_INSTALLER_SUFFIXES = (".exe", ".msi")


class BuildGateError(RuntimeError):
    """The recorder cannot produce a contract-valid receipt."""


# --------------------------------------------------------------------------- #
# File manifests
# --------------------------------------------------------------------------- #


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def scan_tree(root: Path) -> tuple[dict[str, str], int]:
    """Return ``({relative posix path: sha256}, total bytes)`` for a build dir."""
    manifest: dict[str, str] = {}
    total = 0
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = _relative(path, root)
        manifest[relative] = sha256_file(path)
        total += path.stat().st_size
    return manifest, total


def excluded_paths(manifest: Iterable[str], patterns: Sequence[str]) -> list[str]:
    """Paths that match one of ``patterns`` (fnmatch, posix separators)."""
    matched: list[str] = []
    for relative in manifest:
        if any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns):
            matched.append(relative)
    return sorted(matched)


def manifest_sha256(manifest: dict[str, str]) -> str:
    """Canonical manifest hash: ``sha256  path\\n`` sorted by path, LF."""
    lines = [f"{manifest[path]}  {path}\n" for path in sorted(manifest)]
    return sha256_bytes("".join(lines).encode("utf-8"))


def _tool_version(command: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    output = (result.stdout or result.stderr).strip()
    if result.returncode != 0 or not output:
        return "unavailable"
    return output.splitlines()[0]


def probe_toolchain() -> dict[str, str]:
    return {
        "python": ".".join(str(part) for part in sys.version_info[:3]),
        "git": _tool_version(["git", "--version"]),
        "node": _tool_version(["node", "--version"]),
        "npm": _tool_version(["npm", "--version"]),
    }


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #


@dataclass
class BuildTree:
    index: int
    root: str
    files: dict[str, str] = field(default_factory=dict)
    compared: dict[str, str] = field(default_factory=dict)
    excluded: list[str] = field(default_factory=list)
    bytes: int = 0
    missing: bool = False

    @property
    def files_count(self) -> int:
        return len(self.files)

    def to_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "index": self.index,
            "root": self.root,
            "files_count": self.files_count,
            "files_sha256": "sha256:" + manifest_sha256(self.compared),
            "bytes": self.bytes,
        }
        if self.excluded:
            record["excluded_files"] = list(self.excluded)
        if self.missing:
            record["missing"] = True
        return record


def _scan(index: int, root: Path, patterns: Sequence[str]) -> BuildTree:
    tree = BuildTree(index=index, root=str(root))
    if not root.is_dir():
        tree.missing = True
        return tree
    files, total = scan_tree(root)
    tree.files = files
    tree.bytes = total
    tree.excluded = excluded_paths(files, patterns)
    excluded = set(tree.excluded)
    tree.compared = {path: digest for path, digest in files.items() if path not in excluded}
    return tree


def _installers(tree: BuildTree) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for relative, digest in sorted(tree.files.items()):
        if not relative.lower().endswith(_INSTALLER_SUFFIXES):
            continue
        artifacts.append(
            {
                "path": f"{tree.root}/{relative}".replace("\\", "/"),
                "sha256": "sha256:" + digest,
                "kind": "windows-installer",
            }
        )
    return artifacts


def build_receipt(
    *,
    event: str,
    commit: str,
    build_dirs: Sequence[Path | str],
    excludes: Sequence[str] = (),
    ref: str | None = None,
    run_id: str | None = None,
    run_attempt: str | None = None,
    image: str = WINDOWS_IMAGE,
    os_name: str = "windows",
    runner: dict[str, Any] | None = None,
    toolchain: dict[str, str] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Record one job-level ``build-receipt/v1`` for the Windows lane."""
    if event not in EVENTS:
        raise BuildGateError(f"unknown event {event!r}; expected one of {list(EVENTS)}")
    if not _HEX40.match(str(commit)):
        raise BuildGateError(f"commit is not 40 lowercase hex: {commit!r}")
    directories = [Path(item) for item in build_dirs]
    expected = 1 if event == EVENT_PR else 2
    if len(directories) != expected:
        raise BuildGateError(
            f"event {event!r} needs {expected} build dir(s), got {len(directories)}"
        )

    trees = [_scan(index, directory, excludes) for index, directory in enumerate(directories, 1)]
    problems: list[str] = []
    if event == EVENT_PR:
        build = trees[0]
        mode, verdict = "single-build", "not-attempted"
        if build.missing:
            problems.append(f"build 1 output directory is missing: {build.root}")
        elif build.files_count == 0:
            problems.append(f"build 1 output directory is empty: {build.root}")
        elif not _installers(build):
            problems.append(f"build 1 produced no Windows installer under: {build.root}")
    else:
        first, second = trees
        for tree in trees:
            if tree.missing:
                problems.append(f"build {tree.index} output directory is missing: {tree.root}")
            elif tree.files_count == 0:
                problems.append(f"build {tree.index} output directory is empty: {tree.root}")
        missing = first.missing or second.missing
        if missing:
            mode = "manifest-normalized" if excludes else "byte-identical"
            verdict = "differ"
        elif first.files == second.files:
            mode, verdict = "byte-identical", "equal"
        elif excludes and first.compared == second.compared:
            mode, verdict = "manifest-normalized", "equal"
        else:
            mode = "manifest-normalized" if excludes else "byte-identical"
            verdict = "differ"
        if not missing and not _installers(first):
            problems.append(f"build 1 produced no Windows installer under: {first.root}")
        if verdict != "equal":
            problems.append(
                "build 1 and build 2 are not reproducible under the requested exclusions"
            )

    status = "pass" if not problems else "fail"
    recorded_exclusions = (
        [] if event == EVENT_PR or mode == "byte-identical" else list(excludes)
    )
    artifacts = _installers(trees[0])
    if len(trees) > 1 and not trees[1].missing:
        artifacts.extend(_installers(trees[1]))

    resolved_toolchain = toolchain if toolchain is not None else probe_toolchain()
    resolved_runner = runner or {
        "os": os_name,
        "image": image,
        "arch": "x64",
        **resolved_toolchain,
    }

    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "commit": commit,
        "commit_short": commit[:10],
        "generated_at": generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stage": "build",
        "provenance": "real",
        "status": status,
        "os": os_name,
        "runner": resolved_runner,
        "toolchain": resolved_toolchain,
        "artifacts": artifacts,
        "clean_build": status == "pass",
        "clean_method": "fresh windows-latest runner + npm ci from lockfile",
        "reproducibility": {
            "mode": mode,
            "verdict": verdict,
            "builds": [tree.to_dict() for tree in trees],
            "compared_fields": ["file-manifest", "files_count", "bytes"],
            "exclusions": recorded_exclusions,
        },
        "event": {
            "name": event,
            "ref": ref,
            "sha": commit,
            "run_id": run_id,
            "run_attempt": run_attempt,
        },
        "producer": {
            "stage": "build",
            "tool": "prodline.gates.build_gate record",
            "contract_version": 1,
        },
    }
    if problems:
        receipt["problems"] = problems
    return receipt


def write_receipt(receipt: dict[str, Any], out: Path) -> Path:
    """Validate against the frozen schema, then write atomically."""
    schema = _load_schema()
    errors = validate(receipt, schema)
    if errors:
        raise BuildGateError("recorded receipt violates build-receipt/v1: " + "; ".join(errors))
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    temporary = out.with_name(out.name + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, out)
    return out


def _load_schema() -> dict[str, Any]:
    if not BUILD_RECEIPT_SCHEMA_PATH.is_file():
        raise BuildGateError(f"missing build receipt schema: {BUILD_RECEIPT_SCHEMA_PATH}")
    return json.loads(BUILD_RECEIPT_SCHEMA_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Checking
# --------------------------------------------------------------------------- #


@dataclass
class GateResult:
    decision: str = "fail"
    exit_code: int = EXIT_FAIL
    event: str | None = None
    mode: str | None = None
    status: str | None = None
    receipt_sha256: str | None = None
    never_ran: bool = False
    ran_and_failed: bool = False
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == EXIT_PASS and not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": GATE_SCHEMA,
            "decision": self.decision,
            "exit_code": self.exit_code,
            "event": self.event,
            "mode": self.mode,
            "status": self.status,
            "receipt_sha256": self.receipt_sha256,
            "never_ran": self.never_ran,
            "ran_and_failed": self.ran_and_failed,
            "problems": list(self.problems),
        }


def _fail(result: GateResult, problem: str) -> None:
    result.problems.append(problem)
    result.exit_code = EXIT_FAIL
    result.decision = "fail"
    result.ran_and_failed = True


def evaluate(receipt: Any | None, *, event: str, expect_commit: str | None = None) -> GateResult:
    """Decide whether a Windows build receipt proves what the event requires."""
    result = GateResult(event=event)
    if event not in EVENTS:
        result.decision = "error"
        result.exit_code = EXIT_INPUT
        result.problems.append(f"unknown event {event!r}; expected one of {list(EVENTS)}")
        return result
    if receipt is None:
        result.decision = "missing"
        result.exit_code = EXIT_MISSING
        result.never_ran = True
        result.problems.append(
            "no build receipt: the build-win lane never ran or its evidence was lost "
            "(never ran is not a pass)"
        )
        return result
    if not isinstance(receipt, dict):
        result.decision = "error"
        result.exit_code = EXIT_INPUT
        result.problems.append("build receipt is not a JSON object")
        return result

    result.ran_and_failed = True
    result.receipt_sha256 = sha256_bytes(
        json.dumps(receipt, sort_keys=True, ensure_ascii=False).encode("utf-8")
    )
    schema_errors = validate(receipt, _load_schema())
    if schema_errors:
        result.decision = "error"
        result.exit_code = EXIT_INPUT
        result.problems.extend(f"schema: {problem}" for problem in schema_errors)
        return result

    result.status = receipt.get("status")
    reproducibility = receipt.get("reproducibility")
    if isinstance(reproducibility, dict):
        result.mode = reproducibility.get("mode")

    if receipt.get("schema") != RECEIPT_SCHEMA:
        _fail(result, f"schema is {receipt.get('schema')!r}, expected {RECEIPT_SCHEMA!r}")
    if receipt.get("stage") != "build":
        _fail(result, f"stage is {receipt.get('stage')!r}, expected 'build'")
    if receipt.get("provenance") != "real":
        _fail(result, f"provenance is {receipt.get('provenance')!r}; only 'real' can pass")
    if result.status != "pass":
        _fail(
            result,
            f"build receipt status is {result.status!r}; "
            "an executed build that did not pass is not a pass (skip is not pass either)",
        )
    if receipt.get("clean_build") is not True:
        _fail(result, "clean_build must be true for a passing build")
    if receipt.get("os") != "windows":
        _fail(result, f"os is {receipt.get('os')!r}; the build-win lane must report 'windows'")
    runner = receipt.get("runner")
    if not isinstance(runner, dict) or runner.get("image") != WINDOWS_IMAGE:
        _fail(result, f"runner.image must be {WINDOWS_IMAGE!r}")

    commit = receipt.get("commit")
    if not isinstance(commit, str) or not _HEX40.match(commit):
        _fail(result, f"commit is not 40 lowercase hex: {commit!r}")
    elif expect_commit and commit != expect_commit:
        _fail(result, f"receipt commit {commit} != expected {expect_commit}")

    recorded_event = receipt.get("event")
    if not isinstance(recorded_event, dict):
        _fail(result, "receipt has no 'event' object")
    else:
        if recorded_event.get("name") != event:
            _fail(
                result,
                f"receipt event.name is {recorded_event.get('name')!r}, expected {event!r}",
            )
        recorded_ref = recorded_event.get("ref")
        if not isinstance(recorded_ref, str) or not recorded_ref:
            _fail(result, "receipt event.ref is missing")
        elif event == EVENT_PUSH_MAIN and recorded_ref != PUSH_MAIN_REF:
            _fail(result, f"push-main ref must be {PUSH_MAIN_REF!r}, got {recorded_ref!r}")
        elif event == EVENT_PR and recorded_ref == PUSH_MAIN_REF:
            _fail(result, "a PR receipt must not claim refs/heads/main")
        recorded_sha = recorded_event.get("sha")
        if recorded_sha is not None and recorded_sha != commit:
            _fail(result, "receipt event.sha does not match receipt commit")

    if not isinstance(reproducibility, dict):
        _fail(result, "receipt has no 'reproducibility' object")
    else:
        mode = reproducibility.get("mode")
        verdict = reproducibility.get("verdict")
        builds = reproducibility.get("builds")
        exclusions = reproducibility.get("exclusions")
        if mode not in ("byte-identical", "manifest-normalized", "single-build", "not-attempted"):
            _fail(result, f"unknown reproducibility.mode: {mode!r}")
        if not isinstance(builds, list):
            _fail(result, "reproducibility.builds must be a list")
            builds = []
        if event == EVENT_PR:
            if mode != "single-build":
                _fail(
                    result,
                    f"PR builds once, but receipt claims reproducibility.mode={mode!r} "
                    "(a second build was not run)",
                )
            if verdict != "not-attempted":
                _fail(result, f"PR receipt verdict must be 'not-attempted', got {verdict!r}")
            if len(builds) != 1:
                _fail(result, f"PR receipt must record exactly 1 build, got {len(builds)}")
        else:
            if mode not in ("byte-identical", "manifest-normalized"):
                _fail(
                    result,
                    f"push-main must build twice, but receipt claims mode={mode!r}",
                )
            if verdict != "equal":
                _fail(result, f"push-main verdict must be 'equal', got {verdict!r}")
            if len(builds) != 2:
                _fail(result, f"push-main receipt must record 2 builds, got {len(builds)}")
            elif not _same_manifest_hash(builds[0], builds[1]):
                _fail(result, "the two recorded builds do not share files_sha256")
            if not isinstance(exclusions, list):
                _fail(result, "reproducibility.exclusions must be a list")
                exclusions = []
            if mode == "byte-identical" and exclusions:
                _fail(result, "byte-identical must not exclude files")
            if mode == "byte-identical" and any(
                _excluded(build) for build in builds if isinstance(build, dict)
            ):
                _fail(result, "byte-identical builds must not record excluded_files")
            if mode == "manifest-normalized":
                if not exclusions:
                    _fail(result, "manifest-normalized requires non-empty exclusions")
                elif not any(_excluded(build) for build in builds):
                    _fail(result, "manifest-normalized exclusions matched no differing file")
        for index, build in enumerate(builds, 1):
            if not isinstance(build, dict):
                _fail(result, f"reproducibility.builds[{index - 1}] is not an object")
                continue
            if not isinstance(build.get("files_count"), int) or build["files_count"] <= 0:
                _fail(result, f"build {index} files_count must be a positive integer")
            if not _hex64(build.get("files_sha256")):
                _fail(result, f"build {index} files_sha256 is not a sha256")
            if build.get("missing") is True:
                _fail(result, f"build {index} is missing its output directory")

    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        _fail(result, "a passing windows build must record at least one installer artifact")
    else:
        for index, artifact in enumerate(artifacts, 1):
            if not isinstance(artifact, dict):
                _fail(result, f"artifacts[{index - 1}] is not an object")
                continue
            if not artifact.get("path"):
                _fail(result, f"artifacts[{index - 1}] has no path")
            if not _hex64(artifact.get("sha256")):
                _fail(result, f"artifacts[{index - 1}] sha256 is not a sha256")

    if result.problems:
        result.exit_code = EXIT_FAIL
        result.decision = "fail"
    else:
        result.exit_code = EXIT_PASS
        result.decision = "pass"
        result.ran_and_failed = False
    return result


def _hex64(value: Any) -> bool:
    return isinstance(value, str) and bool(_HEX64.match(value))


def _same_manifest_hash(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return first.get("files_sha256") == second.get("files_sha256")


def _excluded(build: dict[str, Any]) -> bool:
    return bool(build.get("excluded_files"))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _event_from_args(args: argparse.Namespace) -> str:
    event = args.event
    if event not in EVENTS:
        raise BuildGateError(f"unknown --event {event!r}; expected one of {list(EVENTS)}")
    return event


def _record_main(args: argparse.Namespace) -> int:
    try:
        event = _event_from_args(args)
        ref = args.ref if args.ref is not None else os.environ.get("GITHUB_REF")
        receipt = build_receipt(
            event=event,
            commit=args.commit,
            build_dirs=args.build_dir,
            excludes=args.exclude,
            ref=ref,
            run_id=args.run_id if args.run_id is not None else os.environ.get("GITHUB_RUN_ID"),
            run_attempt=(
                args.run_attempt
                if args.run_attempt is not None
                else os.environ.get("GITHUB_RUN_ATTEMPT")
            ),
            image=args.image,
        )
        out = write_receipt(receipt, args.out)
    except (BuildGateError, OSError) as exc:
        print(f"build_gate: {exc}", file=sys.stderr)
        return EXIT_INPUT
    print(
        f"build_gate: recorded {receipt['status']} "
        f"event={event} mode={receipt['reproducibility']['mode']} -> {out}"
    )
    for problem in receipt.get("problems", []):
        print(f"  problem: {problem}")
    return EXIT_PASS


def _check_main(args: argparse.Namespace) -> int:
    path = args.receipt
    receipt: Any | None
    if not path.is_file():
        receipt = None
    else:
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"build_gate: cannot read receipt {path}: {exc}", file=sys.stderr)
            return EXIT_INPUT
    try:
        event = _event_from_args(args)
        result = evaluate(receipt, event=event, expect_commit=args.expect_commit)
    except (BuildGateError, OSError, json.JSONDecodeError) as exc:
        print(f"build_gate: {exc}", file=sys.stderr)
        return EXIT_INPUT

    payload = json.dumps(result.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.format == "json":
        print(payload, end="")
    else:
        print(
            f"build_gate: decision {result.decision} (exit {result.exit_code}) "
            f"event={result.event} mode={result.mode} status={result.status}"
        )
        print(f"  never_ran={str(result.never_ran).lower()} "
              f"ran_and_failed={str(result.ran_and_failed).lower()}")
        for problem in result.problems:
            print(f"  problem: {problem}")
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload, encoding="utf-8")
    return result.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m prodline.gates.build_gate",
        description="Record and gate the Windows build-lane receipt (A03).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="measure build output(s) into a build-receipt/v1")
    record.add_argument("--event", required=True, choices=EVENTS)
    record.add_argument("--commit", required=True, help="40-hex commit the build came from")
    record.add_argument(
        "--build-dir", dest="build_dir", action="append", required=True, type=Path,
        help="build output directory (1 for pr, 2 for push-main)",
    )
    record.add_argument(
        "--exclude", action="append", default=[],
        help="relative path/glob ignored when comparing the two builds",
    )
    record.add_argument("--ref", default=None, help="default: $GITHUB_REF")
    record.add_argument("--run-id", default=None, help="default: $GITHUB_RUN_ID")
    record.add_argument("--run-attempt", default=None, help="default: $GITHUB_RUN_ATTEMPT")
    record.add_argument("--image", default=WINDOWS_IMAGE)
    record.add_argument("--out", required=True, type=Path)
    record.set_defaults(handler=_record_main)

    check = subparsers.add_parser("check", help="gate a build-receipt/v1 for an event")
    check.add_argument("receipt", type=Path)
    check.add_argument("--event", required=True, choices=EVENTS)
    check.add_argument("--expect-commit", default=None)
    check.add_argument("--format", choices=("text", "json"), default="text")
    check.add_argument("--json-out", type=Path, default=None)
    check.set_defaults(handler=_check_main)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":  # pragma: no cover - exercised through main() in tests
    raise SystemExit(main())
