"""P0 gate: the capability docs table must match the live registry.

Regression this guards: after M2 shipped ``highlight`` / ``clear_guidance`` the
capability doc still listed them as ``planned``. All 103 P0 pytest cases were
green because no gate compared the product doc with the registry, so the stale
table survived review. This asserts the *relationship* (documented status ==
registry status), not a frozen capability list: advancing a capability to
``ready`` requires updating the doc in the same change, which is exactly the
P0 documentation gate we were missing.
"""
from __future__ import annotations

import re
from pathlib import Path

from plugins.gugu_capabilities import registry

_ROOT = Path(__file__).resolve().parents[2]
_DOC = _ROOT / "docs" / "05-agent-capabilities.md"

# ``| `capability(args)` | ready (M1) | ... |`` -- status is the second cell.
_ROW = re.compile(r"^\|\s*`([a-z_]+)\([^`]*`\s*\|\s*(ready|planned)\b", re.MULTILINE)


def _documented_statuses() -> dict[str, str]:
    return dict(_ROW.findall(_DOC.read_text(encoding="utf-8")))


def _live_statuses() -> dict[str, str]:
    return {entry["id"]: entry["status"] for entry in registry.list_capabilities()}


def test_capability_table_covers_every_registry_entry() -> None:
    documented = _documented_statuses()
    live = _live_statuses()
    assert documented, f"no capability rows found in {_DOC}"
    assert set(documented) == set(live), (
        "docs/05 capability table and registry disagree on the capability set: "
        f"only-doc={sorted(set(documented) - set(live))} "
        f"only-registry={sorted(set(live) - set(documented))}"
    )


def test_documented_status_matches_registry_status() -> None:
    documented = _documented_statuses()
    live = _live_statuses()
    mismatches = {
        capability: (documented.get(capability), live[capability])
        for capability in live
        if documented.get(capability) != live[capability]
    }
    assert not mismatches, (
        "docs/05 status drifted from registry (documented, live): "
        f"{mismatches}. Update the doc table with the capability change."
    )
