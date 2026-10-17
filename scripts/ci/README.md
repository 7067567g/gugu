# scripts/ci — CI helper scripts (orphaned from `.github/**` since `56f6357`)

**Status:** upstream commit `56f6357` removed the `.github/**` workflows/action that used to
consume these scripts, so nothing under `.github/` references `scripts/ci/**` any more
(`grep -rn 'scripts/ci' .github/` → rc=1). The scripts are **保留** (kept), because tests under
`tests/**` still import or execute them directly: `git grep -l 'scripts/ci' -- tests/` lists
13 test files, and every file in the A/B classes below has a live consumer.

Do **not** delete an A/B-class file on the grounds that "CI does not use it" — its consumer is a
test, not a job. To re-wire these scripts into CI, first record the new consumer in this file,
then update the tracking item `B-X2-01` (see `docs/CHANGELOG-gugu.md` and the round's
`reports/REFACTOR-PLAN.md`).

## A 类 · consumed directly by `tests/ci/*` (10)

| Script | Consumer |
| --- | --- |
| `assemble_review_comment.py` | `tests/ci/test_assemble_review_comment.py` |
| `check_os_marker_fakes.py` | `tests/ci/test_check_os_marker_fakes.py` |
| `classify_changes.py` | `tests/ci/test_classify_changes.py` (4 tests are currently `skip`ed — see `B-X2-01`) |
| `e2e_screenshot_status.py` | `tests/ci/test_e2e_screenshot_status.py` |
| `emit_review_status.py` | `tests/ci/test_emit_review_status.py` |
| `list_os_marked_tests.py` | `tests/ci/test_list_os_marked_tests.py` |
| `live_comment.py` | `tests/ci/test_live_comment.py` (2 tests are currently `skip`ed — see `B-X2-01`) |
| `lockfile_diff.py` | `tests/ci/test_lockfile_diff.py` |
| `publish_e2e_evidence.py` | `tests/ci/test_publish_e2e_evidence.py` |
| `timings_report.py` | `tests/ci/test_timings_report.py` |

**Deletion condition:** delete (or rewrite) the corresponding `tests/ci/test_*.py` first.

## B 类 · consumed by `tests/scripts/*` or tooling (3)

| Script | Consumer |
| --- | --- |
| `check_profile_archive_boundary.py` | `tests/scripts/test_check_profile_archive_boundary.py` |
| `check_public_surface.py` | `tests/scripts/test_check_public_surface.py`; `evals/postmortem/forensics/rework.py` |
| `profile_scope_patterns.json` | `scripts/check_profile_scope_patterns.py`; `tests/scripts/test_check_profile_scope_patterns.py` |

## C 类 · manual Windows smoke scripts (2)

| Script | Consumer |
| --- | --- |
| `test_install_ps1_cli_launchers.ps1` | manual only (header `Run:` comment); no automated consumer |
| `test_install_ps1_path_migration.ps1` | manual only (header `Run:` comment); no automated consumer |

Deleting a C-class script requires a Windows install-surface review; it is not automatic just
because no CI job or test references it.

## Rules

- Do not delete A/B-class files merely because there is no CI consumer. Tests are consumers.
- Do not add `.github/**` stubs that would make the skipped wiring tests pass without any job
  actually running — that fakes CI wiring (see AUDIT A2-03/A1-06 and `B-X2-01`).
- When CI is re-wired, update this file and `B-X2-01` in the same change.
