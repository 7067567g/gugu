"""Static wiring assertions for the CI workflow and coverage gate (T-01).

These tests parse ``.github/workflows/ci.yml`` and ``pyproject.toml`` as data
(YAML / TOML) instead of grepping strings, so a change that merely renames a
job or moves a flag is still detectable. They are intentionally stdlib-only
plus PyYAML: the CI job they guard installs only ``pytest`` and
``pytest-cov``.

Scope: job graph (``pytest`` matrix on 3.11 + 3.12, a ``coverage`` job with
``--cov-fail-under=40``, and an ``all-checks-passed`` aggregator whose
``needs`` covers every other job) and the matching ``[tool.coverage.*]``
configuration in ``pyproject.toml``.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

REQUIRED_JOBS = {
    "python-compile",
    "desktop-typecheck",
    "pytest",
    "coverage",
    "all-checks-passed",
}
PREREQUISITE_JOBS = REQUIRED_JOBS - {"all-checks-passed"}
EXPECTED_PYTHON_MATRIX = {"3.11", "3.12"}


def _workflow_text() -> str:
    assert WORKFLOW_PATH.is_file(), f"missing CI workflow: {WORKFLOW_PATH}"
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _workflow() -> dict[str, Any]:
    data = yaml.safe_load(_workflow_text())
    assert isinstance(data, dict), "ci.yml must parse to a mapping"
    jobs = data.get("jobs")
    assert isinstance(jobs, dict) and jobs, "ci.yml must define a non-empty `jobs` mapping"
    return data


def _jobs() -> dict[str, Any]:
    return _workflow()["jobs"]


def _steps_text(job: dict[str, Any]) -> str:
    """Flatten every step name/run/with value of a job into one searchable string."""
    chunks: list[str] = []
    for step in job.get("steps") or []:
        assert isinstance(step, dict), f"malformed step in job: {step!r}"
        for key in ("name", "run", "uses"):
            value = step.get(key)
            if value is not None:
                chunks.append(str(value))
        for key, value in (step.get("with") or {}).items():
            chunks.append(f"{key}: {value}")
    return "\n".join(chunks)


def _pyproject() -> dict[str, Any]:
    assert PYPROJECT_PATH.is_file(), f"missing pyproject.toml: {PYPROJECT_PATH}"
    with PYPROJECT_PATH.open("rb") as handle:
        return tomllib.load(handle)


def _job_ids_from_raw_text() -> list[str]:
    """Job ids as the workflow file actually spells them (two-space indent keys)."""
    return re.findall(r"^  ([a-z][a-z0-9-]*):\s*$", _workflow_text(), flags=re.MULTILINE)


def test_workflow_declares_expected_job_graph() -> None:
    jobs = _jobs()

    assert REQUIRED_JOBS <= set(jobs), (
        "ci.yml is missing required jobs: "
        f"{sorted(REQUIRED_JOBS - set(jobs))}; found {sorted(jobs)}"
    )
    assert len(jobs) >= 5, f"expected >= 5 jobs, found {len(jobs)}: {sorted(jobs)}"
    assert len(_job_ids_from_raw_text()) >= 5, (
        "top-level job ids must stay grep-able as two-space `key:` lines; "
        f"found {_job_ids_from_raw_text()}"
    )


def test_existing_jobs_are_kept() -> None:
    jobs = _jobs()

    for job_id in ("python-compile", "desktop-typecheck"):
        assert job_id in jobs, f"{job_id} must not be removed by the T-01 wiring"
        job = jobs[job_id]
        assert isinstance(job.get("steps"), list) and job["steps"], f"{job_id} lost its steps"
        assert job.get("runs-on"), f"{job_id} lost its runner"


def test_pytest_job_runs_matrix_on_supported_pythons() -> None:
    jobs = _jobs()
    assert "pytest" in jobs, "ci.yml must define a `pytest` job"
    job = jobs["pytest"]

    strategy = job.get("strategy") or {}
    matrix = strategy.get("matrix") or {}
    versions = matrix.get("python-version")
    assert versions, "pytest job must declare strategy.matrix.python-version"
    versions = {str(version) for version in versions}
    assert versions == EXPECTED_PYTHON_MATRIX, (
        f"pytest matrix must be exactly {sorted(EXPECTED_PYTHON_MATRIX)}, got {sorted(versions)}"
    )

    text = _steps_text(job)
    assert "pytest==9.1.1" in text, "pytest job must pin pytest==9.1.1 (lockstep with [dev])"
    assert "pytest-cov==7.1.0" in text, "pytest job must install pytest-cov==7.1.0"
    assert re.search(r"python -m pytest tests/ci\b", text), (
        "pytest job must run `python -m pytest tests/ci`"
    )
    # On the matrix lane the threshold is enforced by the dedicated coverage
    # job; the matrix lane is the fast cross-interpreter signal.
    assert "--cov-fail-under" not in text, (
        "the matrix lane should not repeat the coverage gate; keep it in the coverage job"
    )


def test_coverage_job_enforces_threshold() -> None:
    jobs = _jobs()
    assert "coverage" in jobs, "ci.yml must define a `coverage` job"
    job = jobs["coverage"]

    text = _steps_text(job)
    matrix_versions = ((job.get("strategy") or {}).get("matrix") or {}).get("python-version")
    if matrix_versions is None:
        # Single-version job: the pinned interpreter lives in setup-python's with.
        assert re.search(r"python-version:\s*['\"]?3\.12['\"]?", text), (
            "coverage job must run on Python 3.12"
        )
    else:
        assert {str(v) for v in matrix_versions} == {"3.12"}, (
            "coverage job must target Python 3.12 only"
        )

    assert "pytest==9.1.1" in text, "coverage job must pin pytest==9.1.1"
    assert "pytest-cov==7.1.0" in text, "coverage job must install pytest-cov==7.1.0"
    assert re.search(r"python -m pytest tests/ci\b", text), (
        "coverage job must run the tests/ci suite"
    )
    assert "--cov=scripts/ci" in text, "coverage job must measure scripts/ci"
    assert "--cov-report=term-missing" in text, "coverage job must print missing lines"
    assert "--cov-fail-under=40" in text, (
        "coverage job must enforce the 40% floor (D-005: baseline 41% - epsilon)"
    )


def test_all_checks_passed_aggregates_every_prerequisite() -> None:
    jobs = _jobs()
    assert "all-checks-passed" in jobs, "ci.yml must define an `all-checks-passed` job"
    job = jobs["all-checks-passed"]

    needs = job.get("needs")
    assert needs, "all-checks-passed must declare `needs`"
    needs = {needs} if isinstance(needs, str) else set(needs)
    missing = PREREQUISITE_JOBS - needs
    assert not missing, (
        f"all-checks-passed must depend on every prerequisite job; missing {sorted(missing)}"
    )
    assert set(jobs) - {"all-checks-passed"} <= needs, (
        "all-checks-passed must aggregate every job in the workflow graph"
    )
    # Aggregators must always report, otherwise a failed dependency silently
    # leaves the required check pending instead of red.
    assert job.get("if") == "always()", "all-checks-passed must run with `if: always()`"


def test_pyproject_coverage_gate_matches_workflow() -> None:
    config = _pyproject()
    coverage = config.get("tool", {}).get("coverage") or {}

    run_section = coverage.get("run") or {}
    assert run_section.get("source") == ["scripts/ci"], (
        "pyproject.toml must set [tool.coverage.run] source = ['scripts/ci']"
    )

    report_section = coverage.get("report") or {}
    assert report_section.get("fail_under") == 40, (
        "pyproject.toml must set [tool.coverage.report] fail_under = 40"
    )


def test_dev_extra_pins_pytest_cov() -> None:
    config = _pyproject()
    dev_extra = config.get("project", {}).get("optional-dependencies", {}).get("dev")
    assert isinstance(dev_extra, list) and dev_extra, "pyproject.toml must keep a [dev] extra"
    assert "pytest-cov==7.1.0" in dev_extra, (
        "the dev extra must pin pytest-cov==7.1.0 so local runs match CI"
    )
    assert not [pin for pin in dev_extra if pin.startswith("pytest-cov") and pin != "pytest-cov==7.1.0"]


def test_product_version_is_not_touched_by_ci_wiring() -> None:
    # Guardrail: T-01 owns CI + coverage config only. The upstream kernel
    # version is a different workstream's business (D-004).
    config = _pyproject()
    assert config["project"]["version"] == "0.21.5", (
        "T-01 must not change [project].version (upstream kernel stays 0.21.5)"
    )


if __name__ == "__main__":  # pragma: no cover - manual convenience
    raise SystemExit(pytest.main([__file__, "-q"]))
