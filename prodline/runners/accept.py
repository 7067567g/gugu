"""Acceptance runner: aggregate receipts into ``acceptance-report/v1`` (P1.3).

Contract (``pipeline.yaml#acceptance.rules``)
---------------------------------------------
``aggregate_only``
    The runner reads receipt documents and records ``path + sha256``.  It never
    rebuilds, reinstalls or re-runs a probe.  For the ``source`` stage it also
    records *evidence* rather than a seventh receipt (``pipeline.yaml`` keeps the
    fixed six).  Source evidence is re-hashed but not recomputed: that is the
    explicit ``aggregate_only`` exception documented in ``ACCEPTANCE-GATE.md``.
``commit_binding``
    The report commit (``--commit`` or ``git HEAD``) is passed to
    ``receipt_check.check_paths(..., commit=...)``, so every receipt must carry
    that exact commit.  A single wrong-commit receipt fails its stage; mixed
    commits fail the run.  ``report.receipts[]`` records each receipt's commit
    next to its path/sha256 so the mismatch is auditable.
``verify_files``
    The sha256 chain is always enabled: every file a receipt points at
    (``evidence[]`` / ``artifacts[]`` / ``artifact{}`` / release ``from/to``)
    is re-hashed.  This is not a recomputation of the underlying build -- it is
    the same "is this still the file the receipt recorded" check as
    ``receipt_check --verify-files``, now on the acceptance path by default.
``skip_is_not_pass``
    A stage with no receipt is ``SKIP``, and the report verdict is
    ``incomplete`` -- never ``pass``.
``pass_requires_provenance: real``
    A receipt whose ``provenance`` is ``fixture`` / ``mock`` / ``accelerated``
    can be shape-valid and even say ``status: pass``; it still cannot make the
    stage ``PASS``.

Report shape is exactly ``{schema, commit, stages, receipts, verdict}``.  Stage
status is ``pass|fail|skip``; verdict is ``pass|fail|incomplete``.

CLI::

    python -m prodline.runners.accept --only source --out artifacts/receipts/acceptance-report.json
    python -m prodline.runners.accept --only source,build --expect-pass

Exit codes: ``0`` pass or incomplete (the P1 contract requires exit 0 when an
unimplemented stage is ``SKIP``), ``1`` fail (or non-pass with
``--expect-pass``), ``2`` usage / configuration error.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from prodline.gates import receipt_check

REPORT_SCHEMA = "acceptance-report/v1"
SOURCE_EVIDENCE_SCHEMA = "gugu.prodline-source-evidence/v1"
STAGE_IDS = ("source", "build", "install", "runtime", "quality")
REPORT_FIELDS = ("schema", "commit", "stages", "receipts", "verdict")
DEFAULT_SOURCE_EVIDENCE = Path("artifacts/source-evidence.json")
DEFAULT_RECEIPTS_DIR = Path("artifacts/receipts")
PROVENANCE_VALUES = ("real", "fixture", "mock", "accelerated")
STATUS_VALUES = ("pass", "fail", "skip")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^(sha256:)?[0-9a-f]{64}$")


class AcceptError(RuntimeError):
    """The runner cannot build a report from the given inputs."""


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _resolve_under(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def resolve_commit(root: Path, explicit: str | None) -> str:
    """Return the commit the report is about (explicit wins over ``git HEAD``)."""
    if explicit is not None:
        if not _HEX40.fullmatch(explicit):
            raise AcceptError(f"--commit must be 40 lowercase hex, got {explicit!r}")
        return explicit
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AcceptError(
            "cannot resolve git HEAD; pass --commit in a checkout-less environment"
        ) from exc
    commit = completed.stdout.strip().lower()
    if not _HEX40.fullmatch(commit):
        raise AcceptError(f"git HEAD is not 40 lowercase hex: {commit!r}")
    return commit


# --------------------------------------------------------------------------- #
# Source stage (evidence, not a receipt)
# --------------------------------------------------------------------------- #


def validate_source_evidence(data: Any, *, expected_commit: str) -> list[str]:
    """Shape check for the P1.2 source-evidence document.

    Deliberately does **not** recompute the version lines or lockfile hashes;
    ``prodline.stages.source.verify_source_evidence`` owns that and is a
    separate, opt-in check (``aggregate_only``).
    """
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["source evidence is not a JSON object"]
    if data.get("schema") != SOURCE_EVIDENCE_SCHEMA:
        problems.append(f"schema is {data.get('schema')!r}, expected {SOURCE_EVIDENCE_SCHEMA!r}")
    if data.get("stage") != "source":
        problems.append(f"stage is {data.get('stage')!r}, expected 'source'")
    if data.get("commit") != expected_commit:
        problems.append(f"commit is {data.get('commit')!r}, expected {expected_commit!r}")
    if data.get("provenance") not in PROVENANCE_VALUES:
        problems.append(f"provenance is {data.get('provenance')!r}")
    if data.get("status") not in STATUS_VALUES:
        problems.append(f"status is {data.get('status')!r}")
    lockfiles = data.get("lockfiles")
    if not isinstance(lockfiles, dict):
        problems.append("lockfiles is not an object")
    else:
        for name in ("uv.lock", "package-lock.json"):
            entry = lockfiles.get(name)
            if not isinstance(entry, dict):
                problems.append(f"lockfiles is missing {name!r}")
                continue
            for field in ("sha256", "raw_sha256"):
                if not isinstance(entry.get(field), str) or not _HEX64.fullmatch(entry[field]):
                    problems.append(f"lockfiles.{name}.{field} is not a sha256 string")
    if not isinstance(data.get("problems"), list):
        problems.append("problems is not a list")
    return problems


def collect_source_stage(
    root: Path,
    evidence_path: Path,
    *,
    expected_commit: str,
) -> dict[str, Any]:
    stage: dict[str, Any] = {
        "id": "source",
        "status": "skip",
        "provenance": None,
        "evidence": [],
        "reason": "source evidence absent",
    }
    if not evidence_path.is_file():
        return stage

    payload = evidence_path.read_bytes()
    digest = receipt_check.sha256_bytes(payload)
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        stage.update(status="fail", reason=f"source evidence is not valid JSON: {exc}")
        return stage

    record = {
        "path": _display_path(evidence_path, root),
        "sha256": digest,
        "schema": data.get("schema") if isinstance(data, dict) else None,
        "status": data.get("status") if isinstance(data, dict) else None,
        "provenance": data.get("provenance") if isinstance(data, dict) else None,
    }
    stage["evidence"] = [record]
    stage["provenance"] = record["provenance"]

    problems = validate_source_evidence(data, expected_commit=expected_commit)
    if problems:
        stage.update(status="fail", reason="; ".join(problems))
    elif data.get("status") == "fail":
        stage.update(status="fail", reason="source evidence status is 'fail'")
    elif data.get("status") == "skip":
        stage.update(status="skip", reason="source evidence status is 'skip'")
    elif data.get("provenance") != "real":
        stage.update(
            status="skip",
            reason=f"provenance {data.get('provenance')!r} does not yield PASS",
        )
    else:
        stage.update(status="pass", reason="source evidence status=pass provenance=real")
    return stage


# --------------------------------------------------------------------------- #
# Receipt stages
# --------------------------------------------------------------------------- #


def aggregate_receipt_stage(stage_id: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold every receipt of one stage into a ``pass|fail|skip`` stage entry."""
    stage: dict[str, Any] = {
        "id": stage_id,
        "status": "skip",
        "provenance": None,
        "receipts": [
            {
                "path": entry["path"],
                "sha256": entry["sha256"],
                "schema": entry.get("schema"),
                "status": entry.get("status"),
                "provenance": entry.get("provenance"),
                "commit": entry.get("commit"),
            }
            for entry in entries
        ],
    }
    if not entries:
        stage["reason"] = "no receipts for this stage"
        return stage

    failed = [entry for entry in entries if entry.get("problems") or entry.get("status") == "fail"]
    if failed:
        stage["status"] = "fail"
        reasons: list[str] = []
        for entry in failed:
            reasons.append(
                f"{Path(entry['path']).name}: "
                + ("; ".join(entry["problems"]) if entry.get("problems") else "status=fail")
            )
        stage["reason"] = " | ".join(reasons)
        return stage

    real_passes = [
        entry
        for entry in entries
        if entry.get("status") == "pass" and entry.get("provenance") == "real"
    ]
    if real_passes:
        stage["status"] = "pass"
        stage["provenance"] = "real"
        stage["reason"] = f"{len(real_passes)} real receipt(s) with status=pass"
        return stage

    if any(entry.get("status") == "pass" for entry in entries):
        non_real = sorted(
            {str(entry.get("provenance")) for entry in entries if entry.get("status") == "pass"}
        )
        stage["reason"] = (
            "provenance " + ", ".join(non_real) + " does not yield PASS"
        )
        return stage

    stage["reason"] = "all receipts are status=skip"
    return stage


def collect_receipts(
    root: Path,
    receipts_dir: Path,
    *,
    commit: str | None = None,
    verify_files: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Load and validate every receipt document under ``receipts_dir``.

    ``commit`` is the report commit: every receipt must match it.  ``verify_files``
    defaults to ``True`` because acceptance must not aggregate a receipt whose
    recorded sha256 no longer matches the file it points at.

    Returns ``(entries, problems)``.  Entries carry their stage (from the
    registered schema, not from the document's possibly-wrong ``stage`` field).
    """
    if not receipts_dir.is_dir():
        return [], []

    # An empty directory is a SKIP for the stage, not an accept-level failure.
    result = receipt_check.check_paths(
        receipts_dir=receipts_dir,
        root=root,
        commit=commit,
        verify_files=verify_files,
        allow_empty=True,
    )
    registry = receipt_check.registered_schemas()
    entries: list[dict[str, Any]] = []
    problems: list[str] = []
    for record in result.records:
        schema_entry = registry.get(str(record.schema_id)) if record.schema_id else None
        stage = schema_entry.stage if schema_entry else record.stage
        if record.problems and schema_entry is None:
            problems.append(f"{record.path}: " + "; ".join(record.problems))
            continue
        entries.append(
            {
                "path": _display_path(record.path, root),
                "sha256": record.sha256,
                "schema": record.schema_id,
                "stage": stage,
                "status": record.status,
                "provenance": record.provenance,
                "commit": record.commit,
                "problems": list(record.problems),
            }
        )
    problems.extend(result.problems)
    return entries, problems


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


def parse_only(value: str | None) -> list[str]:
    if value is None:
        return list(STAGE_IDS)
    requested = [part.strip() for part in value.split(",") if part.strip()]
    unknown = [part for part in requested if part not in STAGE_IDS]
    if unknown:
        raise AcceptError(f"unknown stage(s) in --only: {', '.join(unknown)}")
    if not requested:
        raise AcceptError("--only selected no stage")
    return [stage for stage in STAGE_IDS if stage in requested]


def build_report(
    *,
    root: Path = Path("."),
    only: Sequence[str] = STAGE_IDS,
    commit: str | None = None,
    receipts_dir: Path | str | None = None,
    source_evidence: Path | str | None = None,
) -> dict[str, Any]:
    root_path = Path(root)
    commit_sha = resolve_commit(root_path, commit)

    evidence_path = (
        _resolve_under(root_path, Path(source_evidence))
        if source_evidence is not None
        else root_path / DEFAULT_SOURCE_EVIDENCE
    )
    receipts_path = (
        _resolve_under(root_path, Path(receipts_dir))
        if receipts_dir is not None
        else root_path / DEFAULT_RECEIPTS_DIR
    )

    stages: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    global_problems: list[str] = []

    if "source" in only:
        stages.append(collect_source_stage(root_path, evidence_path, expected_commit=commit_sha))

    receipt_entries: list[dict[str, Any]] = []
    if any(stage != "source" for stage in only):
        receipt_entries, global_problems = collect_receipts(
            root_path,
            receipts_path,
            commit=commit_sha,
            verify_files=True,
        )

    for stage_id in only:
        if stage_id == "source":
            continue
        stage_entries = [entry for entry in receipt_entries if entry.get("stage") == stage_id]
        stages.append(aggregate_receipt_stage(stage_id, stage_entries))
        receipts.extend(stage_entries)

    if global_problems:
        verdict = "fail"
    elif any(stage["status"] == "fail" for stage in stages):
        verdict = "fail"
    elif all(stage["status"] == "pass" for stage in stages):
        verdict = "pass"
    else:
        verdict = "incomplete"

    report = {
        "schema": REPORT_SCHEMA,
        "commit": commit_sha,
        "stages": stages,
        "receipts": receipts,
        "verdict": verdict,
    }
    if global_problems:
        # Keep the five fixed top-level fields; attach loader problems to the
        # first non-source stage so the report stays machine-readable.
        for stage in stages:
            stage.setdefault("problems", []).extend(global_problems)
            if stage["status"] != "fail":
                stage["status"] = "fail"
                stage["reason"] = "receipt loader problems: " + "; ".join(global_problems)
            break
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m prodline.runners.accept",
        description="Aggregate prodline receipts into an acceptance-report/v1 document.",
    )
    parser.add_argument("--only", default=None, help="comma-separated stage ids (default: all five)")
    parser.add_argument("--out", type=Path, default=None, help="write the report here")
    parser.add_argument("--root", type=Path, default=Path("."), help="checkout root")
    parser.add_argument("--commit", default=None, help="report commit (default: git HEAD)")
    parser.add_argument("--receipts-dir", type=Path, default=None, help="receipt directory")
    parser.add_argument("--source-evidence", type=Path, default=None, help="source evidence path")
    parser.add_argument(
        "--expect-pass",
        action="store_true",
        help="exit 1 unless every requested stage is PASS (CI uses this)",
    )
    args = parser.parse_args(argv)

    try:
        only = parse_only(args.only)
        report = build_report(
            root=args.root,
            only=only,
            commit=args.commit,
            receipts_dir=args.receipts_dir,
            source_evidence=args.source_evidence,
        )
    except AcceptError as exc:
        print(f"accept: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.out.with_name(args.out.name + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(args.out)
        print(f"accept: wrote {args.out}")
    else:
        print(payload, end="")

    print(f"accept: commit {report['commit']} verdict {report['verdict']}")
    for stage in report["stages"]:
        provenance = stage.get("provenance") or "-"
        print(f"  {stage['id']:<8} {stage['status'].upper():<5} provenance={provenance}")

    if report["verdict"] == "fail":
        return 1
    if args.expect_pass and report["verdict"] != "pass":
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main() in tests
    raise SystemExit(main())
