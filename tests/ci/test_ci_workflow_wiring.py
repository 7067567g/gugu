"""Static wiring assertions for the CI workflow and coverage gate (T-01).

These tests parse ``.github/workflows/ci.yml`` and ``pyproject.toml`` as data
(YAML / TOML) instead of grepping strings, so a change that merely renames a
job or moves a flag is still detectable. They are intentionally stdlib-only
plus PyYAML: the CI lanes they guard install the exact pytest/pytest-cov pins
from ``[dev]`` plus the ``pyyaml``/``httpx`` pins from
``[project].dependencies`` (the package itself is not installed on the fast
lane, and this module imports ``yaml`` at module scope while the root
``conftest.py`` autouse fixture imports the ``agent`` -> ``hermes_cli`` chain,
which reaches ``httpx``).

Scope: job graph (``pytest`` matrix on 3.11 + 3.12, the wider
``pytest-suites`` regression lane, the ``desktop-vitest`` UI lane, a
``coverage`` job with ``--cov-fail-under=40``, the ``arch-gate`` hardening
(F2 parse-error gate + F4 checker tests), the P1.3 ``prodline-gate`` lane, the
A03 ``build-win`` lane (windows-latest, no job-level skip, step-level
single/dual build, event/tier gate), the A05 ``build-linux`` lane (A02's
source->build->artifact_check->accept chain, no skip and no soft-fail), and an
``all-checks-passed`` aggregator whose ``needs`` covers every other job) plus
the matching ``[tool.coverage.*]`` configuration and its measured-baseline
comments in ``pyproject.toml``.
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
    "arch-gate",
    "prodline-gate",
    "build-linux",
    "build-win",
    "desktop-typecheck",
    "pytest",
    "pytest-suites",
    "desktop-vitest",
    "coverage",
    "all-checks-passed",
}
PREREQUISITE_JOBS = REQUIRED_JOBS - {"all-checks-passed"}
EXPECTED_PYTHON_MATRIX = {"3.11", "3.12"}
# Surfaces the fast `tests/ci` signal deliberately does not cover and that this
# repo churns most: the provider-profile suite and the MCP liveness contract.
EXPECTED_SUITE_TARGETS = (
    "tests/plugins/model_providers",
    "tests/tools/test_mcp_transport_liveness.py",
)
# Coverage numbers the two stale comments (ci.yml + pyproject.toml) must carry,
# measured on 6e41d7f2 with Python 3.12.15. The old 41% (1882/1110) wording is
# a stale claim and must be gone.
COVERAGE_BASELINE_PARTS = ("43.03%", "2022 statements, 1152 missing", "2026-10-18")
STALE_COVERAGE_PARTS = ("41%", "1882 statements", "1110 missing")


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


def _pyproject_text() -> str:
    """Raw text: comments are not part of the parsed TOML, so read them here."""
    assert PYPROJECT_PATH.is_file(), f"missing pyproject.toml: {PYPROJECT_PATH}"
    return PYPROJECT_PATH.read_text(encoding="utf-8")


def _toml_section(text: str, header: str) -> str:
    """Verbatim lines of ``[header]`` up to (excluding) the next table header."""
    lines = text.splitlines()
    try:
        start = lines.index(header)
    except ValueError:  # pragma: no cover - guard clause
        raise AssertionError(f"pyproject.toml is missing the {header} table") from None
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("["):
            break
        body.append(line)
    return "\n".join(body)


def _step_values(job: dict[str, Any], key: str) -> list[str]:
    """Raw values of a single step-level key (e.g. working-directory) in a job."""
    values: list[str] = []
    for step in job.get("steps") or []:
        assert isinstance(step, dict), f"malformed step in job: {step!r}"
        value = step.get(key)
        if value is not None:
            values.append(str(value))
    return values


def _job_ids_from_raw_text() -> list[str]:
    """Job ids as the workflow file actually spells them (two-space indent keys)."""
    return re.findall(r"^  ([a-z][a-z0-9-]*):\s*$", _workflow_text(), flags=re.MULTILINE)


def test_workflow_declares_expected_job_graph() -> None:
    jobs = _jobs()

    assert REQUIRED_JOBS <= set(jobs), (
        "ci.yml is missing required jobs: "
        f"{sorted(REQUIRED_JOBS - set(jobs))}; found {sorted(jobs)}"
    )
    assert len(jobs) >= 7, f"expected >= 7 jobs, found {len(jobs)}: {sorted(jobs)}"
    assert len(_job_ids_from_raw_text()) >= 7, (
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
    # This module imports yaml at module scope and the fast lane does not
    # install the package, so the PyYAML pin is part of the lane contract.
    assert "pyyaml==6.0.3" in text, (
        "pytest job must install the PyYAML pin (wiring test parses ci.yml as YAML)"
    )
    # tests/conftest.py imports agent.moa_loop in an autouse fixture, whose
    # import chain reaches hermes_cli.auth -> httpx; without it every test
    # errors at setup with `ModuleNotFoundError: httpx`.
    assert '"httpx[socks]==0.28.1"' in text, (
        "pytest job must install the httpx pin needed by the root conftest fixture"
    )
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
    assert "pyyaml==6.0.3" in text, (
        "coverage job must install the PyYAML pin so tests/ci can collect the wiring test"
    )
    assert '"httpx[socks]==0.28.1"' in text, (
        "coverage job must install the httpx pin needed by the root conftest fixture"
    )
    assert re.search(r"python -m pytest tests/ci\b", text), (
        "coverage job must run the tests/ci suite"
    )
    assert "--cov=scripts/ci" in text, "coverage job must measure scripts/ci"
    assert "--cov-report=term-missing" in text, "coverage job must print missing lines"
    assert "--cov-fail-under=40" in text, (
        "coverage job must enforce the 40% floor (D-005: baseline 43.03% - epsilon)"
    )


def test_pytest_suites_job_covers_wider_regression_surfaces() -> None:
    """The wider lane must actually run the surfaces the fast signal skips."""
    jobs = _jobs()
    assert "pytest-suites" in jobs, "ci.yml must define a `pytest-suites` job"
    job = jobs["pytest-suites"]

    assert job.get("runs-on"), "pytest-suites must declare a runner"
    text = _steps_text(job)
    matrix_versions = ((job.get("strategy") or {}).get("matrix") or {}).get("python-version")
    if matrix_versions is None:
        assert re.search(r"python-version:\s*['\"]?3\.12['\"]?", text), (
            "pytest-suites must run on a single Python 3.12 lane"
        )
    else:
        assert {str(v) for v in matrix_versions} == {"3.12"}, (
            "pytest-suites must target Python 3.12 only"
        )

    assert "pytest==9.1.1" in text, "pytest-suites must pin pytest==9.1.1"
    assert "pytest-cov==7.1.0" in text, "pytest-suites must install pytest-cov==7.1.0"
    assert re.search(r"pip install -e \.", text), (
        "pytest-suites must install the package editable so plugins.*/tools.* imports resolve"
    )

    for target in EXPECTED_SUITE_TARGETS:
        assert target in text, f"pytest-suites must run {target}"

    # Keep one canonical coverage gate: this lane stays threshold-free.
    assert "--cov-fail-under" not in text, (
        "the suites lane should not repeat the coverage gate; keep it in the coverage job"
    )


def test_desktop_vitest_job_runs_ui_project() -> None:
    jobs = _jobs()
    assert "desktop-vitest" in jobs, "ci.yml must define a `desktop-vitest` job"
    job = jobs["desktop-vitest"]

    assert job.get("runs-on"), "desktop-vitest must declare a runner"
    text = _steps_text(job)
    assert re.search(r"node-version:\s*['\"]?24['\"]?", text), (
        "desktop-vitest must run on node 24 (root engines: ^22.22 || ^24.11 || >=26)"
    )
    assert re.search(r"npm ci\b", text), "desktop-vitest must install from the lockfile"
    assert "--legacy-peer-deps" in text, (
        "desktop-vitest must mirror the typecheck lane's legacy-peer-deps install"
    )
    assert re.search(r"npx vitest run\b", text), "desktop-vitest must run `npx vitest run`"
    assert re.search(r"--project\s+ui\b", text), (
        "desktop-vitest must select the `ui` (jsdom renderer) vitest project"
    )
    assert "apps/desktop" in _step_values(job, "working-directory"), (
        "desktop-vitest must run from apps/desktop so it picks up vitest.config.ts"
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
    for job_id in ("pytest-suites", "desktop-vitest"):
        assert job_id in needs, f"all-checks-passed must depend on the new `{job_id}` job"
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


def test_coverage_comments_carry_measured_baseline() -> None:
    """Both stale comments must quote the freshly measured 43.03% (2022/1152)."""
    workflow_text = _workflow_text()
    report_comment = _toml_section(_pyproject_text(), "[tool.coverage.report]")

    for where, text in (("ci.yml", workflow_text), ("pyproject.toml", report_comment)):
        for part in COVERAGE_BASELINE_PARTS:
            assert part in text, f"{where} coverage comment must state {part!r}"
        for stale in STALE_COVERAGE_PARTS:
            assert stale not in text, f"{where} coverage comment still claims the stale {stale!r}"


def test_coverage_run_comment_documents_interpreter_scope() -> None:
    """The run-side comment must pin which interpreters may act as the gate."""
    run_comment = _toml_section(_pyproject_text(), "[tool.coverage.run]")

    for part in ("3.11/3.12", "3.12.15", "3.14.4", "<3.14"):
        assert part in run_comment, (
            f"[tool.coverage.run] comment must document the interpreter scope ({part!r})"
        )
    # The gate itself stays untouched by the comment refresh.
    assert "source = [\"scripts/ci\"]" in run_comment, (
        "[tool.coverage.run] source must stay ['scripts/ci']"
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


def test_arch_gate_is_a_required_job_and_has_no_soft_fail() -> None:
    """F1: the boundary gate is pinned; F2/F4 hardening must not be softened."""
    jobs = _jobs()
    assert "arch-gate" in REQUIRED_JOBS, "F1: REQUIRED_JOBS must pin arch-gate"
    assert "arch-gate" in jobs

    job = jobs["arch-gate"]
    assert job.get("runs-on"), "arch-gate must declare a runner"
    text = _steps_text(job)
    assert "check_boundaries.py" in text, "arch-gate must run the boundary checker"
    assert "--baseline arch/baseline.json" in text, (
        "arch-gate must ratchet against arch/baseline.json"
    )
    assert "--json-out" in text, (
        "F2 needs the checker's JSON receipt; run it with --json-out"
    )
    assert "prodline.gates.arch_boundaries" in text, (
        "F2: a parse-error gate must consume the JSON receipt"
    )
    assert re.search(r"pytest\s+scripts/arch\b", text), (
        "F4: arch-gate must run the checker's unit tests"
    )
    assert "pytest==9.1.1" in text, "F4 needs the pinned pytest in arch-gate"


def test_prodline_gate_runs_contract_tests_and_real_accept() -> None:
    jobs = _jobs()
    assert "prodline-gate" in jobs, "ci.yml must define a prodline-gate job"
    job = jobs["prodline-gate"]

    assert job.get("runs-on"), "prodline-gate must declare a runner"
    text = _steps_text(job)
    assert re.search(r"python-version:\s*['\"]?3\.12['\"]?", text), (
        "prodline-gate must run on Python 3.12"
    )
    assert "pyyaml==6.0.3" in text, (
        "prodline-gate must install the existing PyYAML pin (pipeline.yaml parsing)"
    )
    assert "pytest==9.1.1" in text, "prodline-gate must pin pytest like the other lanes"
    assert re.search(r"python -m pytest prodline/tests\b", text), (
        "prodline-gate must run the prodline contract tests"
    )
    assert "prodline.stages.source" in text, (
        "prodline-gate must collect real source evidence before accept"
    )
    assert re.search(r"prodline\.runners\.accept\s+--only source\b", text), (
        "prodline-gate must run accept --only source"
    )
    assert "--expect-pass" in text, (
        "prodline-gate must fail when the source stage is not PASS"
    )


def test_build_win_job_is_windows_event_tiered_and_not_skippable() -> None:
    """A03: the Windows lane must not be able to look like it never had to run."""
    jobs = _jobs()
    assert "build-win" in REQUIRED_JOBS, "A03: REQUIRED_JOBS must pin build-win"
    assert "build-win" in jobs, "ci.yml must define a `build-win` job"
    job = jobs["build-win"]

    assert job.get("runs-on") == "windows-latest", (
        "build-win must run on windows-latest (no wine substitute on this host)"
    )
    assert "if" not in job, (
        "build-win must not be job-level skipped; a skipped job must never read as optional"
    )
    assert "continue-on-error" not in job, "build-win must not soft-fail"
    assert "continue-on-error" not in _steps_text(job), (
        "no build-win step may soft-fail"
    )

    build_steps = [
        step
        for step in job.get("steps") or []
        if isinstance(step, dict) and "dist:win:nsis" in str(step.get("run") or "")
    ]
    assert len(build_steps) == 2, (
        "build-win must invoke the NSIS build twice so push:main can compare two builds"
    )
    unconditional = [step for step in build_steps if not step.get("if")]
    conditional = [step for step in build_steps if step.get("if")]
    assert len(unconditional) == 1 and len(conditional) == 1, (
        "exactly one build runs on every event; exactly one is gated to push:main"
    )
    gate_if = str(conditional[0].get("if"))
    assert "github.ref == 'refs/heads/main'" in gate_if, (
        "the second build must be step-level gated to the main ref"
    )
    assert "refs/heads/main" in gate_if, (
        "the second build must only run on main (PRs build once)"
    )

    text = _steps_text(job)
    assert "prodline.gates.build_gate record" in text, (
        "build-win must record event-tiered evidence"
    )
    assert "prodline.gates.build_gate check" in text, (
        "build-win must gate the receipt (exit 3 = never ran, exit 1 = failed)"
    )
    assert "--expect-commit" in text, "the gate must bind the receipt to the run's commit"

    record_steps = [
        step
        for step in job.get("steps") or []
        if isinstance(step, dict) and "build_gate record" in str(step.get("run") or "")
    ]
    check_steps = [
        step
        for step in job.get("steps") or []
        if isinstance(step, dict) and "build_gate check" in str(step.get("run") or "")
    ]
    assert len(record_steps) == 1 and record_steps[0].get("if") == "always()", (
        "the record step must run even after a failed build so the failure leaves a receipt"
    )
    assert len(check_steps) == 1 and check_steps[0].get("if") == "always()", (
        "the gate step must run even after a failed build so it reports fail, not missing"
    )

    uploads = [
        step
        for step in job.get("steps") or []
        if isinstance(step, dict) and str(step.get("uses") or "").startswith("actions/upload-artifact")
    ]
    assert uploads, "build-win must upload its receipt for later aggregation"
    assert uploads[0].get("if") == "always()", "the receipt upload must run on failure too"

    aggregator = jobs["all-checks-passed"]
    needs = aggregator.get("needs")
    needs = {needs} if isinstance(needs, str) else set(needs or [])
    assert "build-win" in needs, "all-checks-passed must depend on build-win"
    assert "test \"${{ needs.build-win.result }}\" = \"success\"" in _steps_text(aggregator), (
        "the aggregator must turn a skipped/failed build-win into a red required check"
    )


def test_build_linux_lane_builds_the_artifact_and_is_not_skippable() -> None:
    """A05: the Linux lane must run A02's real chain, not just be listed in needs."""
    jobs = _jobs()
    assert "build-linux" in REQUIRED_JOBS, "A05: REQUIRED_JOBS must pin build-linux"
    assert "build-linux" in jobs, "ci.yml must define a `build-linux` job"
    job = jobs["build-linux"]

    assert job.get("runs-on") == "ubuntu-latest", "build-linux must run on ubuntu-latest"
    assert "if" not in job, (
        "build-linux must not be job-level skipped; a skipped build must never read as pass"
    )
    assert "continue-on-error" not in job, "build-linux must not soft-fail"
    assert "continue-on-error" not in _steps_text(job), (
        "no build-linux step may soft-fail"
    )

    text = _steps_text(job)
    # Listed in `needs` is not enough: the lane has to produce the artifact and
    # let `accept --expect-pass` refuse a SKIP, otherwise it is a green no-op.
    for marker in (
        "prodline.stages.source",
        "prodline.stages.build",
        "--require-source-evidence",
        "prodline.gates.artifact_check",
        "--build-receipt artifacts/receipts/build-receipt.json",
        "prodline.runners.accept --only source,build",
        "--expect-pass",
        "pyyaml==6.0.3",
    ):
        assert marker in text, f"build-linux lane is missing {marker!r}"
    assert text.count("prodline.stages.build") == 1, (
        "the build step must be the one dual-build invocation; no single-build shortcut"
    )
    assert "--no-verify" not in text, (
        "build-linux must not disable artifact verification"
    )

    uploads = [
        step
        for step in job.get("steps") or []
        if isinstance(step, dict) and str(step.get("uses") or "").startswith("actions/upload-artifact")
    ]
    assert uploads, "build-linux must upload its artifact bundle for later consumption"
    assert "artifact.zip" in str(uploads[0].get("with")), (
        "the Linux upload must include artifact.zip so B-side can consume it"
    )

    aggregator = jobs["all-checks-passed"]
    needs = aggregator.get("needs")
    needs = {needs} if isinstance(needs, str) else set(needs or [])
    aggregator_text = _steps_text(aggregator)
    for lane in ("build-linux", "build-win"):
        assert lane in needs, f"all-checks-passed must depend on {lane}"
        expected = 'test "${{ needs.' + lane + '.result }}" = "success"'
        assert expected in aggregator_text, (
            f"the aggregator must turn a skipped/failed {lane} into a red required check"
        )


def test_ci_has_no_continue_on_error_soft_fail() -> None:
    """A gate that can fail must not be marked `continue-on-error`."""
    offenders: list[str] = []
    for job_id, job in _jobs().items():
        if "continue-on-error" in job:
            offenders.append(f"job {job_id}")
        for index, step in enumerate(job.get("steps") or []):
            if isinstance(step, dict) and "continue-on-error" in step:
                offenders.append(f"job {job_id} step {index}")
    assert not offenders, f"ci.yml must not soft-fail gate steps: {offenders}"


if __name__ == "__main__":  # pragma: no cover - manual convenience
    raise SystemExit(pytest.main([__file__, "-q"]))
