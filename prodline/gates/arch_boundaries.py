"""First-party parse-error gate over the arch-gate JSON receipt (F2 / B2).

``scripts/arch/check_boundaries.py`` already computes ``summary.parse_errors``
but only prints it -- a first-party file it cannot parse is silently skipped.
F2 asks for a hard failure (exit 2) plus an explicit allowlist, without
rewriting the checker's rule engine.

This module is the CI-side gate: the arch-gate lane writes the checker's JSON
receipt with ``--json-out`` and then runs this consumer.  It deliberately does
not rescan the repo and does not duplicate any K/X/G/P logic; the checker stays
the single source of truth for the verdict and for the parse-error list.

Fails closed when the receipt only carries a sample of the errors: if
``summary.parse_errors > len(parse_error_samples)`` the remaining files cannot
be checked against the allowlist, so the gate refuses to pass.

CLI::

    python -m prodline.gates.arch_boundaries --receipt artifacts/arch/boundary-receipt.json
    python -m prodline.gates.arch_boundaries --receipt r.json --allow 'vendor/**'

Exit codes: ``0`` no unallowlisted parse errors, ``1`` boundary regression,
``2`` parse errors / unreadable or malformed receipt / configuration error.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

ARCH_RECEIPT_SCHEMA = "gugu.arch-receipt/v1"
GATE_SCHEMA = "gugu.prodline-arch-parse-gate/v1"


@dataclass
class GateResult:
    parse_errors: int = 0
    files_scanned: int = 0
    verdict: str | None = None
    allowlisted: int = 0
    problems: list[str] = field(default_factory=list)
    exit_code: int = 0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": GATE_SCHEMA,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "verdict": self.verdict,
            "parse_errors": self.parse_errors,
            "files_scanned": self.files_scanned,
            "allowlisted": self.allowlisted,
            "problems": list(self.problems),
        }


def _matches_allowlist(file_name: str, allow: Sequence[str]) -> bool:
    normalized = file_name.replace("\\", "/")
    return any(fnmatch.fnmatchcase(normalized, pattern) for pattern in allow)


def evaluate(receipt: Any, *, allow: Sequence[str] = ()) -> GateResult:
    """Decide whether a ``gugu.arch-receipt/v1`` document passes the F2 gate."""
    result = GateResult()
    if not isinstance(receipt, dict):
        result.problems.append("arch receipt is not a JSON object")
        result.exit_code = 2
        return result
    if receipt.get("schema") != ARCH_RECEIPT_SCHEMA:
        result.problems.append(
            f"arch receipt schema is {receipt.get('schema')!r}, expected {ARCH_RECEIPT_SCHEMA!r}"
        )
        result.exit_code = 2
        return result

    result.verdict = receipt.get("verdict")
    summary = receipt.get("summary")
    if not isinstance(summary, dict):
        result.problems.append("arch receipt has no 'summary' object")
        result.exit_code = 2
        return result

    parse_errors = summary.get("parse_errors")
    files_scanned = summary.get("files_scanned")
    if not isinstance(parse_errors, int) or isinstance(parse_errors, bool) or parse_errors < 0:
        result.problems.append(f"summary.parse_errors is not a non-negative integer: {parse_errors!r}")
        result.exit_code = 2
        return result
    if not isinstance(files_scanned, int) or isinstance(files_scanned, bool) or files_scanned <= 0:
        result.problems.append(f"summary.files_scanned is not a positive integer: {files_scanned!r}")
        result.exit_code = 2
        return result
    result.parse_errors = parse_errors
    result.files_scanned = files_scanned

    if result.verdict != "baseline-ok":
        result.problems.append(f"arch-gate verdict is {result.verdict!r}, expected 'baseline-ok'")
        result.exit_code = 1
        return result

    if parse_errors == 0:
        return result

    samples = receipt.get("parse_error_samples")
    if not isinstance(samples, list) or not samples:
        result.problems.append(
            f"{parse_errors} parse error(s) but the receipt carries no samples; cannot allowlist"
        )
        result.exit_code = 2
        return result
    if len(samples) < parse_errors:
        result.problems.append(
            f"{parse_errors} parse error(s) but only {len(samples)} sample(s) recorded; "
            "refusing to pass files that cannot be checked"
        )
        result.exit_code = 2
        return result

    if not allow:
        files = sorted({str(sample.get("file", "?")) for sample in samples if isinstance(sample, dict)})
        result.problems.append(
            f"{parse_errors} first-party parse error(s) and no --allow pattern; "
            "offending files: " + ", ".join(files)
        )
        result.exit_code = 2
        return result

    unmatched: list[str] = []
    matched = 0
    for sample in samples:
        file_name = str(sample.get("file", "")) if isinstance(sample, dict) else ""
        if file_name and _matches_allowlist(file_name, allow):
            matched += 1
        else:
            unmatched.append(file_name or "<missing file>")
    result.allowlisted = matched
    if unmatched:
        result.problems.append(
            "parse errors outside the allowlist: " + ", ".join(sorted(set(unmatched)))
        )
        result.exit_code = 2
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m prodline.gates.arch_boundaries",
        description="Fail when the arch-gate receipt reports parse errors (F2).",
    )
    parser.add_argument("--receipt", type=Path, required=True, help="JSON receipt from check_boundaries.py")
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="GLOB",
        help="explicit allowlist pattern for a parse-error file (repeatable)",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)

    try:
        receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"arch-parse-gate: cannot read {args.receipt}: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"arch-parse-gate: {args.receipt} is not valid JSON: {exc}", file=sys.stderr)
        return 2

    result = evaluate(receipt, allow=args.allow)
    if args.format == "json":
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(
            f"arch-parse-gate: receipt={args.receipt} verdict={result.verdict} "
            f"files={result.files_scanned} parse_errors={result.parse_errors} "
            f"allowlisted={result.allowlisted}"
        )
        for problem in result.problems:
            print(f"  ERROR {problem}")
        print(f"  gate: {'ok' if result.ok else 'invalid'}")
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover - exercised through main() in tests
    raise SystemExit(main())
