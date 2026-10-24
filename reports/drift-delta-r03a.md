# Drift delta — v0.22.5 scan, 15 → 18 after the mirror was completed

- run: `gugu-reb-r03a-driftclose` (A1 closeout)
- scanner: `scripts/upstream/check_symbol_drift.py` (unchanged this round)
- new report: `reports/drift-v0.22.5.json` (generated 2026-10-24T23:25:50Z)
- old report: `runs/gugu-reb-r01-prep/reports/drift-v0.22.5.json` (generated 2026-10-24T22:54:45Z)
- environment: `GIT_NO_LAZY_FETCH=1`, proxies pointed at `127.0.0.1:9`; no network

## 1. Counts

| metric | old (r01) | new (r03a) | delta |
| --- | ---: | ---: | ---: |
| `resolved` | 655 | 918 | +263 |
| `drift` | 15 | 18 | +3 |
| `allowlisted` | 266 | 0 | -266 |
| `unresolved` | 0 | 0 | +0 |
| `skipped` | 491 | 491 | +0 |
| imports total | 1427 | 1427 | +0 |
| allowlist file entries | 175 | 0 | -175 |

Old kinds: `{"MODULE-GONE": 2, "SYMBOL-GONE": 13}`; new kinds: `{"MODULE-GONE": 2, "SYMBOL-GONE": 15, "SYMBOL-NEVER-AT-BASE": 1}`.
The 266 `allowlisted` occurrences (175 unique `module,name` entries) all became provable: 263 resolved, 3 drift.
No import disappeared (`skipped` and `imports_total` are unchanged).

## 2. The 3 new drift entries (exactly what the allowlist masked)

### 2.1 `hermes_cli.anon_auth.guest_carries_inference` — SYMBOL-GONE

- import site: `hermes_cli/banner.py:881`
- base symbol: `True`; tag symbol: `False`
- old allowlist: kind `SYMBOL-UNVERIFIED`, tag_blob `7f1568d308fbb10e68263e6d862709ff34d30bfc`, owner `gugu-reb-r02-rehearsal`, latest_review_round `r02`, count 1
- why it was masked: the tag blob was absent from the `blob:none` mirror, so the scanner could not read the module and deferred it as `SYMBOL-UNVERIFIED`. It was **never** proven present — the allowlist entry only recorded the missing blob.
- why it is real now: the blob is present and tree-bound to `v0.22.5` (`reports/mirror-integrity-v0.22.5.json`), so the scanner read the actual module and proved the symbol absent.
- assessment: gugu HEAD's `hermes_cli/anon_auth.py` still defines `guest_carries_inference` (base and HEAD both do); v0.22.5's `hermes_cli/anon_auth.py` does not. `hermes_cli/banner.py` uses it in `_route_model_for_banner()` to decide whether the Nous free tier serves a model. Real drift: A6 must follow/re-express the upstream free-tier check.

### 2.2 `tools.skills_tool._find_all_skills` — SYMBOL-GONE

- import site: `hermes_cli/banner.py:120`
- base symbol: `True`; tag symbol: `False`
- old allowlist: kind `SYMBOL-UNVERIFIED`, tag_blob `a6f8d186048f91c93b9285c6147fd51cca76a4eb`, owner `gugu-reb-r02-rehearsal`, latest_review_round `r02`, count 1
- why it was masked: the tag blob was absent from the `blob:none` mirror, so the scanner could not read the module and deferred it as `SYMBOL-UNVERIFIED`. It was **never** proven present — the allowlist entry only recorded the missing blob.
- why it is real now: the blob is present and tree-bound to `v0.22.5` (`reports/mirror-integrity-v0.22.5.json`), so the scanner read the actual module and proved the symbol absent.
- assessment: gugu HEAD's `tools/skills_tool.py` still defines `_find_all_skills` (base and HEAD both do); v0.22.5's `tools/skills_tool.py` does not. `hermes_cli/banner.py` calls it inside `get_available_skills()._scan()` to feed the startup banner. Real drift: A6 must re-express against the v0.22.5 skill-discovery surface or keep an explicit gugu copy. It is not new code from A3.

### 2.3 `tui_gateway.server.install_exit_flush_signal_handlers` — SYMBOL-NEVER-AT-BASE

- import site: `hermes_cli/web_server.py:1512`
- base symbol: `False`; tag symbol: `False`
- old allowlist: kind `SYMBOL-UNVERIFIED`, tag_blob `b9ab08d4b480137f1194aa37831f51e4dfc915f8`, owner `gugu-reb-r02-rehearsal`, latest_review_round `r02`, count 1
- why it was masked: the tag blob was absent from the `blob:none` mirror, so the scanner could not read the module and deferred it as `SYMBOL-UNVERIFIED`. It was **never** proven present — the allowlist entry only recorded the missing blob.
- why it is real now: the blob is present and tree-bound to `v0.22.5` (`reports/mirror-integrity-v0.22.5.json`), so the scanner read the actual module and proved the symbol absent.
- assessment: `hermes_cli/web_server.py` imports it inside `try/except Exception` to install exit-flush signal handlers. The symbol exists in neither the fork base nor HEAD (the import is a pre-existing dead guard, present already in 171fdfd7), and v0.22.5's `tui_gateway/server.py` does not define it either. Because nothing resolves, the guarded import always falls through to `_log.debug`. It is drift of the `SYMBOL-NEVER-AT-BASE` kind, not a rebase blocker; A6 should delete the dead import (or implement the guard against the v0.22.5 surface).

## 3. All 18 drift entries (★ = new in this scan)

| | kind | module | symbol | import site | base_symbol |
| ---: | --- | --- | --- | --- | --- |
| 1 | SYMBOL-GONE | `hermes_cli.plugin_compat` | `warn_once` | `agent/context_compressor.py:5578` | True |
| 2 | SYMBOL-GONE | `hermes_cli.plugin_compat` | `warn_once` | `agent/turn_context.py:1326` | True |
| 3 | SYMBOL-GONE | `gateway.run_inbound_unauthorized` | `PAIRING_RATE_LIMITED_REPLY` | `gateway/run_inbound.py:24` | True |
| 4 | SYMBOL-GONE | `hermes_cli.banner` | `UPDATE_AVAILABLE_NO_COUNT` | `hermes_cli/_startup_fast.py:227` | True |
| 5 | SYMBOL-GONE | `hermes_cli.banner` | `check_for_updates` | `hermes_cli/_startup_fast.py:227` | True |
| 6 | SYMBOL-GONE | `tools.skills_tool` | `_find_all_skills` | `hermes_cli/banner.py:120` | True |
| 7 | SYMBOL-GONE | `hermes_cli.build_info` | `get_build_sha` | `hermes_cli/banner.py:454` | True |
| 8 | SYMBOL-GONE | `hermes_cli.anon_auth` | `guest_carries_inference` | `hermes_cli/banner.py:881` | True |
| 9 | SYMBOL-GONE | `hermes_cli.plugin_compat` | `warn_once` | `hermes_cli/commands.py:755` | True |
| 10 | SYMBOL-GONE | `hermes_cli.update_handoff` | `detached_shim_child_env` | `hermes_cli/main_install_repair.py:440` | True |
| 11 | SYMBOL-GONE | `hermes_cli.plugin_compat` | `warn_once` | `hermes_cli/models.py:2869` | True |
| 12 | SYMBOL-GONE | `tools.checkpoint_manager` | `checkpoint_footprint_notice` | `hermes_cli/update_cmd_maint.py:893` | True |
| 13 | SYMBOL-NEVER-AT-BASE | `tui_gateway.server` | `install_exit_flush_signal_handlers` | `hermes_cli/web_server.py:1512` | False |
| 14 | SYMBOL-GONE | `hermes_cli.plugin_compat` | `warn_once` | `hermes_cli/web_server.py:1970` | True |
| 15 | SYMBOL-GONE | `tools.approval_context` | `_tirith_fail_open` | `tools/approval.py:24` | True |
| 16 | MODULE-GONE | `tools.tirith_security` | `check_command_security` | `tools/approval.py:660` | None |
| 17 | MODULE-GONE | `tools.tirith_security` | `check_command_security` | `tools/approval.py:1151` | None |
| 18 | SYMBOL-GONE | `hermes_cli.plugin_compat` | `warn_once` | `tools/approval.py:1358` | True |

## 4. Why the allowlist is now `entries: []` (not a cosmetic reset)

1. The 32 previously missing module blobs are all present and tree-bound to the tag (32/32); see `reports/mirror-integrity-v0.22.5.{json,txt}`.
2. With the blobs present, the scanner resolves all 1,427 imports offline: 918 resolved + 18 drift + 491 skipped, `unresolved=0`, `allowlisted=0`.
3. The 3 new entries are **real drift**, so they must stay visible in `reports/drift-v0.22.5.json`; re-adding them to an allowlist would repeat the exact masking this round removes.
4. `SYMBOL-NEVER-AT-BASE` is not "upstream dropped a symbol gugu uses" (the scanner default disposition text for that kind is imprecise); it is a pre-existing dead guarded import. `check_symbol_drift.py` is out of scope this round, so the precise A6 action is recorded here and in `reports/gates-r03a.txt`; A6 should either remove the import or implement the guard.

## 5. Reproduce

```bash
cd /home/a7067567g/下载/deepseek/60-外部仓库/gugu-work
GIT_NO_LAZY_FETCH=1 .venv/bin/python scripts/upstream/check_symbol_drift.py --check
GIT_NO_LAZY_FETCH=1 .venv/bin/python scripts/upstream/check_symbol_drift.py
```
