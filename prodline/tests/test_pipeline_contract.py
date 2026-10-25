"""Contract tests for ``prodline/pipeline.yaml`` and its receipt registry.

These tests are the P1.1 executable form of the fixed contract: five stable
stage ids, six ``v1`` receipts, the acceptance policy, and the mapping between
the two.  They do not execute any stage.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

PRODLINE = Path(__file__).resolve().parents[1]
REPO_ROOT = PRODLINE.parent
sys.path.insert(0, str(REPO_ROOT))

from prodline.schema_subset import resolve_ref  # noqa: E402  (path bootstrap above)

PIPELINE_PATH = PRODLINE / "pipeline.yaml"
SCHEMAS_DIR = PRODLINE / "schemas"

EXPECTED_STAGE_IDS = ["source", "build", "install", "runtime", "quality"]
EXPECTED_RECEIPT_IDS = ["build", "install", "runtime", "task", "recovery", "release"]
COMMON_REQUIRED = ["schema", "commit", "generated_at", "stage", "provenance", "status"]
PROVENANCE_VALUES = ["real", "fixture", "mock", "accelerated"]


def load_pipeline() -> dict:
    return yaml.safe_load(PIPELINE_PATH.read_text(encoding="utf-8"))


def load_schema(relative_path: str) -> dict:
    return json.loads((PRODLINE / relative_path).read_text(encoding="utf-8"))


def _property(schema: dict, name: str) -> dict:
    """Return a property schema, following a local ``$ref`` when present."""
    prop = schema["properties"][name]
    return resolve_ref(prop["$ref"], schema) if "$ref" in prop else prop


def test_pipeline_declares_the_five_stable_stage_ids_in_order():
    pipeline = load_pipeline()
    assert pipeline["schema"] == "gugu.prodline-pipeline/v1"
    stages = pipeline["stages"]
    assert [stage["id"] for stage in stages] == EXPECTED_STAGE_IDS
    orders = [stage["order"] for stage in stages]
    assert orders == sorted(orders)
    assert len(set(orders)) == len(orders)


def test_receipt_registry_is_exactly_the_six_v1_schemas():
    pipeline = load_pipeline()
    assert sorted(pipeline["receipts"]) == sorted(EXPECTED_RECEIPT_IDS)
    for receipt_id, entry in pipeline["receipts"].items():
        assert entry["schema_id"] == f"{receipt_id}-receipt/v1"
        schema_path = PRODLINE / entry["schema"]
        assert schema_path.is_file(), f"missing schema file for {receipt_id}: {schema_path}"
        schema = load_schema(entry["schema"])
        assert schema["$schema"] == pipeline["schema_draft"]
        assert schema["$id"] == f"{pipeline['schema_id_base']}/{receipt_id}-receipt/v1"
        assert schema["properties"]["schema"]["const"] == entry["schema_id"]


def test_no_receipt_schema_exists_outside_the_registry():
    on_disk = sorted(path.name for path in SCHEMAS_DIR.glob("*.schema.json"))
    expected = sorted(
        Path(entry["schema"]).name for entry in load_pipeline()["receipts"].values()
    )
    assert on_disk == expected


def test_each_receipt_is_produced_by_exactly_one_stage():
    pipeline = load_pipeline()
    produced_by: dict[str, str] = {}
    for stage in pipeline["stages"]:
        for receipt_id in stage["receipts"]:
            assert receipt_id in pipeline["receipts"], f"unknown receipt {receipt_id!r}"
            assert receipt_id not in produced_by, f"{receipt_id!r} is listed by two stages"
            produced_by[receipt_id] = stage["id"]
    assert sorted(produced_by) == sorted(EXPECTED_RECEIPT_IDS)


def test_schema_stage_const_matches_the_producing_stage():
    pipeline = load_pipeline()
    for stage in pipeline["stages"]:
        for receipt_id in stage["receipts"]:
            schema = load_schema(pipeline["receipts"][receipt_id]["schema"])
            assert schema["properties"]["stage"]["const"] == stage["id"], (
                f"{receipt_id}-receipt is produced by stage {stage['id']!r} but its "
                f"schema declares stage {schema['properties']['stage']['const']!r}"
            )


def test_source_stage_has_no_receipt_in_the_fixed_contract():
    pipeline = load_pipeline()
    source = next(stage for stage in pipeline["stages"] if stage["id"] == "source")
    assert source["receipts"] == []


def test_common_required_fields_and_enums_match_every_schema():
    pipeline = load_pipeline()
    contract = pipeline["receipt_contract"]
    assert contract["required_common_fields"] == COMMON_REQUIRED
    assert contract["provenance_values"] == PROVENANCE_VALUES
    assert contract["status_values"] == ["pass", "fail", "skip"]
    assert contract["additive_only"] is True
    for receipt_id, entry in pipeline["receipts"].items():
        schema = load_schema(entry["schema"])
        required = schema["required"]
        for field in COMMON_REQUIRED:
            assert field in required, f"{receipt_id}-receipt does not require {field!r}"
        assert _property(schema, "provenance")["enum"] == PROVENANCE_VALUES
        assert _property(schema, "status")["enum"] == ["pass", "fail", "skip"]
        assert schema["additionalProperties"] is True, (
            f"{receipt_id}-receipt must stay additive: additionalProperties must be true"
        )


def test_install_receipt_requires_the_artifact_block():
    pipeline = load_pipeline()
    schema = load_schema(pipeline["receipts"]["install"]["schema"])
    assert "artifact" in schema["required"]
    artifact = schema["$defs"]["artifact"]
    required = set(artifact["required"])
    assert {"path", "sha256", "manifest_sha256", "provenance", "verified"} <= required
    artifact_provenance = artifact["properties"]["provenance"]
    artifact_provenance = resolve_ref(artifact_provenance["$ref"], schema)
    assert artifact_provenance["enum"] == PROVENANCE_VALUES
    assert artifact["properties"]["verified"]["type"] == "boolean"


def test_acceptance_policy_is_declared_before_the_runner_exists():
    acceptance = load_pipeline()["acceptance"]
    assert acceptance["report_schema"] == "acceptance-report/v1"
    assert acceptance["report_fields"] == ["schema", "commit", "stages", "receipts", "verdict"]
    rules = acceptance["rules"]
    assert rules["aggregate_only"] is True
    assert rules["skip_is_not_pass"] is True
    assert rules["pass_requires_provenance"] == "real"
