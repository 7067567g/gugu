"""Shape tests for the six ``v1`` receipt schemas.

The fixed contract (ROUND-SPEC / D-P1-02 / D-P1-03) is checked here in the
executable form:

* every receipt is a draft-2020-12 object with the six common required fields;
* ``provenance`` is required and limited to real|fixture|mock|accelerated;
* ``install-receipt`` requires ``artifact{path,sha256,manifest_sha256,
  provenance,verified}``;
* v1 stays additive: an unknown future field must not invalidate a receipt.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
import yaml

TESTS_DIR = Path(__file__).resolve().parent
PRODLINE = TESTS_DIR.parent
REPO_ROOT = PRODLINE.parent
sys.path.insert(0, str(REPO_ROOT))

from prodline.schema_subset import (  # noqa: E402  (path bootstrap above)
    UnresolvableRef,
    UnsupportedKeyword,
    assert_supported_schema,
    load_schema,
    resolve_ref,
    validate,
)

PIPELINE = yaml.safe_load((PRODLINE / "pipeline.yaml").read_text(encoding="utf-8"))
RECEIPT_IDS = ["build", "install", "runtime", "task", "recovery", "release"]
SCHEMAS = {
    receipt_id: load_schema(PRODLINE / PIPELINE["receipts"][receipt_id]["schema"])
    for receipt_id in RECEIPT_IDS
}
STAGE_BY_RECEIPT = {
    receipt_id: stage["id"]
    for stage in PIPELINE["stages"]
    for receipt_id in stage["receipts"]
}
COMMON_FIELDS = PIPELINE["receipt_contract"]["required_common_fields"]
HEX40 = "a" * 40
HEX64 = "b" * 64


def _base(receipt_id: str) -> dict:
    return {
        "schema": f"{receipt_id}-receipt/v1",
        "commit": HEX40,
        "generated_at": "2026-10-25T12:00:00Z",
        "stage": STAGE_BY_RECEIPT[receipt_id],
        "provenance": "real",
        "status": "pass",
    }


SAMPLES: dict[str, dict] = {
    "build": {
        **_base("build"),
        "os": "ubuntu-24.04",
        "product_version": "0.9.0",
        "desktop_version": "0.17.6",
        "upstream_tag": "v0.22.5",
        "toolchain": {"python": "3.12.15", "node": "24.22.1", "git": "2.43.0"},
        "lockfiles": {"uv.lock": "sha256:" + HEX64, "package-lock.json": "sha256:" + HEX64},
        "artifacts": [
            {
                "path": "artifact.zip",
                "sha256": "sha256:" + HEX64,
                "bytes": 82486414,
                "kind": "source-zip",
                "manifest_path": "artifact-manifest.json",
                "manifest_sha256": "sha256:" + HEX64,
            }
        ],
        "clean_build": True,
        "clean_method": "fresh runner + npm ci from lockfile",
        "reproducibility": {
            "mode": "byte-identical",
            "verdict": "equal",
            "builds": [{"id": "build-1", "sha256": "sha256:" + HEX64}],
        },
        "producer": {
            "stage": "build",
            "tool": "prodline/stages/build.py",
            "contract_version": 1,
        },
    },
    "install": {
        **_base("install"),
        "install_dir": "C:/gugu",
        "duration_s": 321.5,
        "product_version": "0.9.0",
        "kernel_version": "0.21.5",
        "health_version": "0.21.5",
        "artifact": {
            "path": "artifact.zip",
            "sha256": HEX64,
            "manifest_sha256": HEX64,
            "provenance": "real",
            "verified": True,
            "bytes": 82486414,
            "manifest_path": "artifact-manifest.json",
            "installer_sha256": HEX64,
            "verification": {
                "sha256": True,
                "layout": True,
                "version": True,
                "files_sha256": True,
            },
        },
        "uninstall_ok": True,
        "clean_precondition": {"node": False, "python": False, "uv": False},
    },
    "runtime": {
        **_base("runtime"),
        "pid": 4242,
        "port": 8080,
        "endpoint": "http://127.0.0.1:8080/api/health",
        "ready_seconds": 42.5,
        "product_version": "0.9.0",
        "kernel_version": "0.21.5",
        "health_version": "0.21.5",
        "log_path": "logs/gateway.log",
        "shutdown": "graceful",
        "exit_code": 0,
    },
    "task": {
        **_base("task"),
        "task_id": "task-001",
        "input_hash": HEX64,
        "output_hash": HEX64,
        "verify": True,
        "verdict": "pass",
        "duration_ms": 1234,
        "cost_usd": 0.0042,
        "retries": 0,
    },
    "recovery": {
        **_base("recovery"),
        "fault": "kill backend process",
        "fault_type": "kill",
        "injected": True,
        "recovered": True,
        "recovery_seconds": 8.5,
        "attempts": 1,
        "duplicate_side_effects": 0,
        "backend_health": {"status": 200},
    },
    "release": {
        **_base("release"),
        "from_artifact": {"path": "artifact-old.zip", "sha256": HEX64, "verified": True},
        "to_artifact": {"path": "artifact-new.zip", "sha256": HEX64, "verified": True},
        "upgrade_ok": True,
        "rollback_ok": True,
        "data_check": {"rows_before": 10, "rows_after": 10},
        "release_version": "0.9.0",
    },
}


def _iter_schema_objects(node: object, path: str = "$"):
    if not isinstance(node, dict):
        return
    yield path, node
    for keyword in ("properties", "$defs"):
        value = node.get(keyword)
        if isinstance(value, dict):
            for name, subschema in value.items():
                yield from _iter_schema_objects(subschema, f"{path}.{keyword}.{name}")
    for keyword in ("items", "additionalProperties"):
        value = node.get(keyword)
        if isinstance(value, dict):
            yield from _iter_schema_objects(value, f"{path}.{keyword}")
    for keyword in ("allOf", "anyOf", "oneOf"):
        value = node.get(keyword)
        if isinstance(value, list):
            for index, subschema in enumerate(value):
                yield from _iter_schema_objects(subschema, f"{path}.{keyword}[{index}]")


@pytest.mark.parametrize("receipt_id", RECEIPT_IDS)
def test_schema_is_draft_2020_12_object(receipt_id: str):
    schema = SCHEMAS[receipt_id]
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"].endswith(f"/{receipt_id}-receipt/v1")
    assert schema["title"] == f"{receipt_id}-receipt/v1"
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is True


@pytest.mark.parametrize("receipt_id", RECEIPT_IDS)
def test_schema_uses_only_the_supported_keyword_subset(receipt_id: str):
    # A future edit must not add a constraint the stdlib validator would ignore.
    assert_supported_schema(SCHEMAS[receipt_id])


@pytest.mark.parametrize("receipt_id", RECEIPT_IDS)
def test_every_required_key_is_declared_in_properties(receipt_id: str):
    for path, node in _iter_schema_objects(SCHEMAS[receipt_id]):
        required = node.get("required")
        if not required:
            continue
        properties = node.get("properties") or {}
        for key in required:
            assert key in properties, f"{path}: required {key!r} is not declared in properties"


def _property(schema: dict, name: str) -> dict:
    """Return a property schema, following a local ``$ref`` when present."""
    prop = schema["properties"][name]
    return resolve_ref(prop["$ref"], schema) if "$ref" in prop else prop


@pytest.mark.parametrize("receipt_id", RECEIPT_IDS)
def test_common_required_fields_are_enforced(receipt_id: str):
    schema = SCHEMAS[receipt_id]
    required = schema["required"]
    for field in COMMON_FIELDS:
        assert field in required
    assert _property(schema, "provenance")["enum"] == [
        "real",
        "fixture",
        "mock",
        "accelerated",
    ]
    assert _property(schema, "status")["enum"] == ["pass", "fail", "skip"]


@pytest.mark.parametrize("receipt_id", RECEIPT_IDS)
def test_positive_sample_validates(receipt_id: str):
    errors = validate(SAMPLES[receipt_id], SCHEMAS[receipt_id])
    assert errors == [], f"{receipt_id} sample should be valid:\n" + "\n".join(errors)


@pytest.mark.parametrize("receipt_id", RECEIPT_IDS)
@pytest.mark.parametrize("field", COMMON_FIELDS)
def test_missing_common_field_is_rejected(receipt_id: str, field: str):
    sample = copy.deepcopy(SAMPLES[receipt_id])
    sample.pop(field)
    errors = validate(sample, SCHEMAS[receipt_id])
    assert any(field in error for error in errors), (
        f"removing required {field!r} from {receipt_id}-receipt was not rejected"
    )


@pytest.mark.parametrize("receipt_id", RECEIPT_IDS)
def test_bad_provenance_is_rejected(receipt_id: str):
    sample = copy.deepcopy(SAMPLES[receipt_id])
    sample["provenance"] = "synthetic"
    errors = validate(sample, SCHEMAS[receipt_id])
    assert any("provenance" in error for error in errors)


@pytest.mark.parametrize("receipt_id", RECEIPT_IDS)
def test_bad_status_is_rejected(receipt_id: str):
    sample = copy.deepcopy(SAMPLES[receipt_id])
    sample["status"] = "SKIP"
    errors = validate(sample, SCHEMAS[receipt_id])
    assert any("status" in error for error in errors)


@pytest.mark.parametrize("receipt_id", RECEIPT_IDS)
def test_commit_must_be_a_full_lowercase_sha(receipt_id: str):
    sample = copy.deepcopy(SAMPLES[receipt_id])
    sample["commit"] = "ABC"
    errors = validate(sample, SCHEMAS[receipt_id])
    assert any("commit" in error for error in errors)


@pytest.mark.parametrize("receipt_id", RECEIPT_IDS)
def test_generated_at_requires_a_timezone(receipt_id: str):
    sample = copy.deepcopy(SAMPLES[receipt_id])
    sample["generated_at"] = "2026-10-25T12:00:00"
    errors = validate(sample, SCHEMAS[receipt_id])
    assert any("timezone" in error for error in errors)


@pytest.mark.parametrize("receipt_id", RECEIPT_IDS)
def test_additive_field_is_allowed(receipt_id: str):
    sample = copy.deepcopy(SAMPLES[receipt_id])
    sample["future_optional_field"] = {"anything": [1, 2, 3]}
    errors = validate(sample, SCHEMAS[receipt_id])
    assert errors == [], "v1 must stay additive; a future field broke validation"


INSTALL_ARTIFACT_FIELDS = ["path", "sha256", "manifest_sha256", "provenance", "verified"]


@pytest.mark.parametrize("field", INSTALL_ARTIFACT_FIELDS)
def test_install_artifact_requires_each_contract_field(field: str):
    sample = copy.deepcopy(SAMPLES["install"])
    sample["artifact"].pop(field)
    errors = validate(sample, SCHEMAS["install"])
    assert any(field in error for error in errors), (
        f"install-receipt artifact without {field!r} was not rejected"
    )


def test_install_receipt_requires_the_artifact_block():
    sample = copy.deepcopy(SAMPLES["install"])
    sample.pop("artifact")
    errors = validate(sample, SCHEMAS["install"])
    assert any("artifact" in error for error in errors)


def test_install_artifact_rejects_the_sha256_prefix():
    # P must strip A's "sha256:" prefix before writing its own observation.
    sample = copy.deepcopy(SAMPLES["install"])
    sample["artifact"]["sha256"] = "sha256:" + HEX64
    errors = validate(sample, SCHEMAS["install"])
    assert any("sha256" in error for error in errors)


def test_build_artifact_accepts_the_sha256_prefix():
    # A's build-receipt may carry "sha256:<hex>" verbatim; v1 must not force a rewrite.
    sample = copy.deepcopy(SAMPLES["build"])
    sample["artifacts"][0]["sha256"] = "sha256:" + HEX64
    errors = validate(sample, SCHEMAS["build"])
    assert errors == []


def test_fixture_provenance_is_shape_valid_but_policy_blocks_a_pass():
    sample = copy.deepcopy(SAMPLES["install"])
    sample["provenance"] = "fixture"
    sample["artifact"]["provenance"] = "fixture"
    assert validate(sample, SCHEMAS["install"]) == []
    rules = PIPELINE["acceptance"]["rules"]
    assert rules["pass_requires_provenance"] == "real"
    assert rules["skip_is_not_pass"] is True


def test_skip_receipt_is_shape_valid_but_not_a_pass():
    sample = copy.deepcopy(SAMPLES["build"])
    sample["status"] = "skip"
    assert validate(sample, SCHEMAS["build"]) == []
    assert PIPELINE["acceptance"]["rules"]["skip_is_not_pass"] is True


def test_schema_ids_are_unique():
    ids = [SCHEMAS[receipt_id]["$id"] for receipt_id in RECEIPT_IDS]
    assert len(ids) == len(set(ids))


def test_all_schema_refs_resolve_locally():
    for receipt_id, schema in SCHEMAS.items():
        for path, node in _iter_schema_objects(schema):
            ref = node.get("$ref")
            if ref is not None:
                assert resolve_ref(ref, schema) is not None, f"{receipt_id}{path}: {ref}"


def test_external_refs_and_unknown_keywords_are_rejected():
    with pytest.raises(UnresolvableRef):
        resolve_ref("https://example.com/schema", {})
    with pytest.raises(UnsupportedKeyword):
        assert_supported_schema({"type": "object", "if": {}})
