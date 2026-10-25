"""Receipt checker: schema + semantics + sha256 chain (P1.3).

``pipeline.yaml`` declares the six ``v1`` receipts and the acceptance policy;
this module is the executable check that a receipt file is *evidence* before
``prodline.runners.accept`` aggregates it.  It never recomputes the underlying
build/install/runtime facts: it validates the receipt document, the fields the
JSON Schema cannot express (``task_id``, ``fault``, ``from_artifact`` ...), and
-- with ``--verify-files`` -- the sha256 chain of the files a receipt points at.

Why the semantics live here and not in the schemas
--------------------------------------------------
The schemas stay additive-only ``v1``.  Requiring e.g. ``task_id`` at the schema
level would change the meaning of an existing ``v1`` receipt; the checker can
add the operational requirement without inventing a ``v2``.  See
``docs/architecture/prodline/ACCEPTANCE-GATE.md``.

CLI::

    python -m prodline.gates.receipt_check --receipts-dir artifacts/receipts
    python -m prodline.gates.receipt_check path/to/build-receipt.json --verify-files

Exit codes: ``0`` all receipts valid, ``1`` at least one problem, ``2`` bad
input / configuration (including "nothing to check").
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

from prodline.schema_subset import assert_supported_schema, validate

PRODLINE_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_PATH = PRODLINE_ROOT / "pipeline.yaml"

#: Tool-output schema (not a receipt, not registered in ``pipeline.yaml``).
CHECK_SCHEMA = "gugu.prodline-receipt-check/v1"

_HEX64 = re.compile(r"^(sha256:)?[0-9a-f]{64}$")
_RECEIPT_FILENAME = re.compile(
    r"^(build|install|runtime|task|recovery|release)-receipt.*\.json$"
)
_ANY_RECEIPT_SCHEMA = re.compile(r"^[a-z]+-receipt/v[0-9]+$")

class ContractError(RuntimeError):
    """``pipeline.yaml`` or a schema file is missing or malformed."""


@dataclass
class RegisteredSchema:
    receipt_id: str
    schema_id: str
    stage: str
    path: Path
    schema: dict[str, Any]


@dataclass
class ReceiptRecord:
    path: Path
    sha256: str
    data: dict[str, Any] | None = None
    schema_id: str | None = None
    stage: str | None = None
    status: str | None = None
    provenance: str | None = None
    commit: str | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "schema": self.schema_id,
            "stage": self.stage,
            "status": self.status,
            "provenance": self.provenance,
            "commit": self.commit,
            "problems": list(self.problems),
        }


@dataclass
class CheckResult:
    records: list[ReceiptRecord] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    commit: str | None = None

    @property
    def ok(self) -> bool:
        return not self.problems and all(record.ok for record in self.records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CHECK_SCHEMA,
            "ok": self.ok,
            "commit": self.commit,
            "receipts": [record.to_dict() for record in self.records],
            "problems": list(self.problems),
        }


# --------------------------------------------------------------------------- #
# Contract loading
# --------------------------------------------------------------------------- #


def load_pipeline(path: Path | str | None = None) -> dict[str, Any]:
    """Load and sanity-check ``pipeline.yaml`` (the single source of truth)."""
    pipeline_path = Path(path) if path is not None else PIPELINE_PATH
    try:
        pipeline = yaml.safe_load(pipeline_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContractError(f"cannot read {pipeline_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ContractError(f"{pipeline_path} is not valid YAML: {exc}") from exc
    if not isinstance(pipeline, dict) or pipeline.get("schema") != "gugu.prodline-pipeline/v1":
        raise ContractError(f"{pipeline_path} is not a gugu.prodline-pipeline/v1 document")
    return pipeline


def registered_schemas(
    pipeline: dict[str, Any] | None = None,
    *,
    schema_dir: Path | str | None = None,
) -> dict[str, RegisteredSchema]:
    """Return ``{schema_id: RegisteredSchema}`` for the six registered receipts."""
    pipeline = load_pipeline() if pipeline is None else pipeline
    root = Path(schema_dir) if schema_dir is not None else PRODLINE_ROOT
    stage_by_receipt = {
        receipt_id: stage["id"]
        for stage in pipeline.get("stages", [])
        for receipt_id in stage.get("receipts", [])
    }
    registry: dict[str, RegisteredSchema] = {}
    for receipt_id, entry in (pipeline.get("receipts") or {}).items():
        schema_id = str(entry.get("schema_id", ""))
        path = root / str(entry.get("schema", ""))
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError(f"cannot load schema for {receipt_id!r}: {exc}") from exc
        try:
            assert_supported_schema(schema)
        except Exception as exc:  # UnsupportedKeyword / UnresolvableRef
            raise ContractError(f"schema {schema_id!r} is outside the supported subset: {exc}") from exc
        stage = stage_by_receipt.get(receipt_id)
        if stage is None:
            raise ContractError(f"receipt {receipt_id!r} is not produced by any stage")
        registry[schema_id] = RegisteredSchema(
            receipt_id=receipt_id,
            schema_id=schema_id,
            stage=stage,
            path=path,
            schema=schema,
        )
    expected = set((pipeline.get("receipts") or {}).keys())
    if set(registry) != {f"{name}-receipt/v1" for name in expected}:
        raise ContractError("pipeline receipt registry and schema ids disagree")
    return registry


# --------------------------------------------------------------------------- #
# Per-receipt checks
# --------------------------------------------------------------------------- #


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path | str) -> str:
    return sha256_bytes(Path(path).read_bytes())


def _is_hex64(value: Any) -> bool:
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _check_artifact_ref(name: str, value: Any, problems: list[str]) -> None:
    if not isinstance(value, dict):
        problems.append(f"semantics: {name} must be an object")
        return
    if not _is_nonempty_string(value.get("path")):
        problems.append(f"semantics: {name}.path must be a non-empty string")
    if not _is_hex64(value.get("sha256")):
        problems.append(f"semantics: {name}.sha256 must be 64 lowercase hex (optional 'sha256:' prefix)")


#: Fields the JSON Schema leaves optional but the gate requires (P1.3).
SEMANTIC_RULES: dict[str, tuple[str, ...]] = {
    "task-receipt/v1": ("task_id", "input_hash", "output_hash", "verify", "verdict"),
    "recovery-receipt/v1": ("fault", "injected", "recovered", "duplicate_side_effects"),
    "release-receipt/v1": (
        "from_artifact",
        "to_artifact",
        "upgrade_ok",
        "rollback_ok",
    ),
}


def check_semantics(receipt: dict[str, Any]) -> list[str]:
    """Require the payload fields a receipt is useless without."""
    problems: list[str] = []
    schema_id = str(receipt.get("schema", ""))
    for field_name in SEMANTIC_RULES.get(schema_id, ()):
        if field_name not in receipt:
            problems.append(f"semantics: missing required field {field_name!r}")

    if schema_id == "task-receipt/v1":
        if "task_id" in receipt and not _is_nonempty_string(receipt["task_id"]):
            problems.append("semantics: task_id must be a non-empty string")
        for name in ("input_hash", "output_hash"):
            if name in receipt and not _is_hex64(receipt[name]):
                problems.append(f"semantics: {name} must be 64 lowercase hex")
        if "verify" in receipt and not isinstance(receipt["verify"], bool):
            problems.append("semantics: verify must be a boolean")
        if "verdict" in receipt and receipt["verdict"] not in {"pass", "fail", "skip"}:
            problems.append("semantics: verdict must be one of pass|fail|skip")

    if schema_id == "recovery-receipt/v1":
        if "fault" in receipt and not _is_nonempty_string(receipt["fault"]):
            problems.append("semantics: fault must be a non-empty string")
        for name in ("injected", "recovered"):
            if name in receipt and not isinstance(receipt[name], bool):
                problems.append(f"semantics: {name} must be a boolean")
        if "duplicate_side_effects" in receipt and (
            not isinstance(receipt["duplicate_side_effects"], int)
            or isinstance(receipt["duplicate_side_effects"], bool)
            or receipt["duplicate_side_effects"] < 0
        ):
            problems.append("semantics: duplicate_side_effects must be a non-negative integer")

    if schema_id == "release-receipt/v1":
        for name in ("from_artifact", "to_artifact"):
            if name in receipt:
                _check_artifact_ref(name, receipt[name], problems)
        for name in ("upgrade_ok", "rollback_ok"):
            if name in receipt and not isinstance(receipt[name], bool):
                problems.append(f"semantics: {name} must be a boolean")

    return problems


def _check_file_ref(
    problems: list[str],
    *,
    label: str,
    path_value: Any,
    hash_value: Any,
    root: Path,
) -> None:
    if not _is_nonempty_string(path_value):
        problems.append(f"chain: {label}.path must be a non-empty string")
        return
    if not _is_hex64(hash_value):
        problems.append(f"chain: {label}.sha256 must be 64 lowercase hex")
        return
    raw = str(path_value)
    if raw.startswith(("http://", "https://")):
        problems.append(f"chain: {label} points at a URL, expected a local evidence file: {raw}")
        return
    candidate = Path(raw)
    resolved = candidate if candidate.is_absolute() else root / candidate
    if not resolved.is_file():
        problems.append(f"chain: {label} file not found: {resolved}")
        return
    expected = str(hash_value).removeprefix("sha256:")
    actual = sha256_file(resolved)
    if actual != expected:
        problems.append(f"chain: {label} sha256 mismatch for {resolved} (recorded {expected}, actual {actual})")


def check_hash_chain(receipt: dict[str, Any], *, root: Path | str) -> list[str]:
    """Recompute the sha256 of every local file a receipt references."""
    problems: list[str] = []
    root_path = Path(root)
    schema_id = str(receipt.get("schema", ""))

    for index, entry in enumerate(receipt.get("evidence") or []):
        if not isinstance(entry, dict):
            problems.append(f"chain: evidence[{index}] is not an object")
            continue
        _check_file_ref(
            problems,
            label=f"evidence[{index}]",
            path_value=entry.get("path"),
            hash_value=entry.get("sha256"),
            root=root_path,
        )

    if schema_id == "build-receipt/v1":
        for index, artifact in enumerate(receipt.get("artifacts") or []):
            if not isinstance(artifact, dict):
                problems.append(f"chain: artifacts[{index}] is not an object")
                continue
            _check_file_ref(
                problems,
                label=f"artifacts[{index}]",
                path_value=artifact.get("path"),
                hash_value=artifact.get("sha256"),
                root=root_path,
            )
            if artifact.get("manifest_path") is not None:
                _check_file_ref(
                    problems,
                    label=f"artifacts[{index}].manifest",
                    path_value=artifact.get("manifest_path"),
                    hash_value=artifact.get("manifest_sha256"),
                    root=root_path,
                )

    if schema_id == "install-receipt/v1" and isinstance(receipt.get("artifact"), dict):
        artifact = receipt["artifact"]
        _check_file_ref(
            problems,
            label="artifact",
            path_value=artifact.get("path"),
            hash_value=artifact.get("sha256"),
            root=root_path,
        )
        if artifact.get("manifest_path") is not None:
            _check_file_ref(
                problems,
                label="artifact.manifest",
                path_value=artifact.get("manifest_path"),
                hash_value=artifact.get("manifest_sha256"),
                root=root_path,
            )

    if schema_id == "release-receipt/v1":
        for name in ("from_artifact", "to_artifact"):
            value = receipt.get(name)
            if isinstance(value, dict):
                _check_file_ref(
                    problems,
                    label=name,
                    path_value=value.get("path"),
                    hash_value=value.get("sha256"),
                    root=root_path,
                )

    return problems


def check_receipt(
    receipt: Any,
    *,
    registry: dict[str, RegisteredSchema],
    commit: str | None = None,
    verify_files: bool = False,
    root: Path | str | None = None,
) -> list[str]:
    """Validate one receipt document; return every problem (empty == valid)."""
    if not isinstance(receipt, dict):
        return ["receipt is not a JSON object"]
    problems: list[str] = []

    schema_id = receipt.get("schema")
    entry = registry.get(str(schema_id))
    if entry is None:
        problems.append(f"schema {schema_id!r} is not registered in pipeline.yaml")
        return problems

    problems.extend(validate(receipt, entry.schema))

    if commit is not None and receipt.get("commit") != commit:
        problems.append(
            f"commit mismatch: receipt has {receipt.get('commit')!r}, expected {commit!r}"
        )

    if receipt.get("stage") != entry.stage:
        problems.append(
            f"stage mismatch: {entry.schema_id} must be produced by {entry.stage!r}, "
            f"got {receipt.get('stage')!r}"
        )

    problems.extend(check_semantics(receipt))

    if verify_files:
        problems.extend(check_hash_chain(receipt, root=Path.cwd() if root is None else root))

    return problems


# --------------------------------------------------------------------------- #
# Loading receipt files
# --------------------------------------------------------------------------- #


def _load_one(
    path: Path,
    *,
    registry: dict[str, RegisteredSchema],
    commit: str | None,
    verify_files: bool,
    root: Path | None,
    require_receipt: bool,
) -> ReceiptRecord | None:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        if not require_receipt:
            return None
        return ReceiptRecord(path=path, sha256="", problems=[f"cannot read receipt: {exc}"])

    digest = sha256_bytes(payload)
    record = ReceiptRecord(path=path, sha256=digest)
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        if not require_receipt and not _RECEIPT_FILENAME.match(path.name):
            return None
        record.problems.append(f"invalid JSON: {exc}")
        return record

    schema_id = data.get("schema") if isinstance(data, dict) else None
    registered = registry.get(str(schema_id))
    if registered is None:
        # A receipt-shaped document with an unregistered schema is a real
        # problem; arbitrary JSON (manifest, evidence, acceptance report) is not.
        if isinstance(schema_id, str) and _ANY_RECEIPT_SCHEMA.match(schema_id):
            record.schema_id = schema_id
            record.problems.append(f"schema {schema_id!r} is not registered in pipeline.yaml")
            return record
        return None

    record.data = data
    record.schema_id = registered.schema_id
    record.stage = data.get("stage")
    record.status = data.get("status")
    record.provenance = data.get("provenance")
    record.commit = data.get("commit") if isinstance(data.get("commit"), str) else None
    record.problems.extend(
        check_receipt(
            data,
            registry=registry,
            commit=commit,
            verify_files=verify_files,
            root=root,
        )
    )
    return record


def _iter_json_files(directory: Path) -> Iterable[Path]:
    return sorted(
        (path for path in directory.rglob("*.json") if path.is_file()),
        key=lambda path: str(path),
    )


def check_paths(
    paths: Sequence[Path | str] = (),
    *,
    receipts_dir: Path | str | None = None,
    root: Path | str | None = None,
    commit: str | None = None,
    verify_files: bool = False,
    allow_mixed_commits: bool = False,
    allow_empty: bool = False,
    pipeline: dict[str, Any] | None = None,
) -> CheckResult:
    """Check explicit receipt files and/or every receipt under ``receipts_dir``."""
    result = CheckResult(commit=commit)
    try:
        registry = registered_schemas(pipeline)
    except ContractError as exc:
        result.problems.append(str(exc))
        return result

    root_path = Path(root) if root is not None else Path.cwd()
    explicit = [Path(path) for path in paths]
    for path in explicit:
        if not path.is_file():
            result.problems.append(f"receipt not found: {path}")
            continue
        record = _load_one(
            path,
            registry=registry,
            commit=commit,
            verify_files=verify_files,
            root=root_path,
            require_receipt=True,
        )
        if record is not None:
            result.records.append(record)

    if receipts_dir is not None:
        directory = Path(receipts_dir)
        if not directory.is_dir():
            result.problems.append(f"receipts directory not found: {directory}")
        else:
            for path in _iter_json_files(directory):
                record = _load_one(
                    path,
                    registry=registry,
                    commit=commit,
                    verify_files=verify_files,
                    root=root_path,
                    require_receipt=False,
                )
                if record is not None:
                    result.records.append(record)

    if not explicit and receipts_dir is None:
        result.problems.append("no receipts given (pass paths or --receipts-dir)")

    commits = {str(record.data.get("commit")) for record in result.records if record.data}
    if len(commits) > 1 and not allow_mixed_commits:
        result.problems.append(
            "receipts disagree on commit: " + ", ".join(sorted(commits))
        )
    if result.commit is None and len(commits) == 1:
        result.commit = next(iter(commits))

    if not result.records and not result.problems and not allow_empty:
        result.problems.append("no receipt documents found")
    return result


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m prodline.gates.receipt_check",
        description="Validate prodline v1 receipts (schema + semantics + sha256 chain).",
    )
    parser.add_argument("paths", nargs="*", type=Path, help="explicit receipt JSON files")
    parser.add_argument("--receipts-dir", type=Path, default=None, help="scan this directory recursively")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="base for relative evidence paths")
    parser.add_argument("--commit", default=None, help="require every receipt to carry this 40-hex commit")
    parser.add_argument("--verify-files", action="store_true", help="recompute referenced file sha256")
    parser.add_argument("--allow-mixed-commits", action="store_true", help="do not fail when receipts disagree on commit")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    result = check_paths(
        args.paths,
        receipts_dir=args.receipts_dir,
        root=args.root,
        commit=args.commit,
        verify_files=args.verify_files,
        allow_mixed_commits=args.allow_mixed_commits,
    )

    if args.format == "json":
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True, ensure_ascii=False))
    elif not args.quiet:
        print(f"receipt-check: {len(result.records)} receipt(s), commit={result.commit or '?'}")
        for record in result.records:
            state = "OK  " if record.ok else "FAIL"
            print(
                f"  {state} {record.path} "
                f"({record.schema_id or '?'}, {record.status or '?'}, {record.provenance or '?'})"
            )
            for problem in record.problems:
                print(f"        - {problem}")
        for problem in result.problems:
            print(f"  ERROR {problem}")
        print(f"  verdict: {'ok' if result.ok else 'invalid'}")

    if result.problems and not result.records:
        return 2
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover - exercised through main() in tests
    raise SystemExit(main())
