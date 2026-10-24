# Drift reconciliation — why 136 became 266 (and 175 allowlist entries)

- run: `gugu-reb-r01-prep` (A1)
- scan: `scripts/upstream/check_symbol_drift.py`
- output: `reports/drift-v0.22.5.json`
- allowlist: `scripts/upstream/symbol-drift-allowlist.json`

## 1. The two numbers

| number | scope | status |
| --- | --- | --- |
| **136** | `LONG-LINE-PLAN` §1.5: imports that could not be symbol-checked because the tag-side module blob was absent. The rehearsal script was not archived. | lower bound, not reproducible exactly |
| **266** | this scan: all absolute imports in all **35** kernel files, relative imports resolved, every import classified | full scope, reproducible |

The 136 is a **subset** of the 266, not a contradiction. The larger number comes from
three scope differences:

1. the plan's count was taken over the 26 modified files (the 9 added gugu files were
   handled separately); this scan covers all 35;
2. relative imports (`from .x import y`) are resolved to their package path here;
3. every import is classified (resolved / drift / skipped / allowlisted), so nothing is
   left in an unlabeled "unread" bucket.

## 2. Variants actually measured

| variant | unresolved occurrences | unique `(module, name)` |
| --- | ---: | ---: |
| all 35 files (gate scope) | 266 | 175 |
| 26 modified files only (`--only-modified`) | 256 | 171 |
| 26 modified, excluding root-level modules (`cli`, `hermes_constants`, `run_agent`, `model_tools`) | 182 | 130 |
| all 35, excluding those root modules | 190 | 134 |
| module top-level imports only | 27 | 24 |

No variant lands exactly on 136; the closest is 134 (all 35 files, root modules excluded).
That is consistent with the plan's 136 being a narrower, unarchived口径 — it cannot be
reconstructed from the current repo, so this round does not pretend it can.

## 3. What the full scan found

| class | count | meaning |
| --- | ---: | --- |
| resolved | 655 | symbol proven present at `v0.22.5` (tag blob, or byte-identical base/main proxy) |
| drift | **15** | 13 `SYMBOL-GONE` + 2 `MODULE-GONE`; each has a disposition for A6 |
| skipped | 491 | external/third-party imports, or symbols introduced by the gugu patch stack and re-applied in A3–A5 |
| unresolved | 266 occurrences | tag blob absent and no byte-identical proxy |

The 15 drift entries are exactly the 15 candidates in `LONG-LINE-PLAN` §1.5 (six
`warn_once` sites, `PAIRING_RATE_LIMITED_REPLY`, the two banner/update symbols,
`get_build_sha`, `detached_shim_child_env`, `checkpoint_footprint_notice`,
`_tirith_fail_open`, and two `tirith_security` module imports). No additional real drift
was found.

## 4. Why the 266 are allowlisted, not silently passed

The local mirror is a `blob:none` partial clone. For each unresolved module the `v0.22.5`
blob is missing **and** its sha is not byte-identical to a locally available base/main
blob, so no exact symbol check is possible. The alternatives are:

- network fetch (forbidden by ROUND-SPEC §4 in A0/A1);
- claim "resolved" without evidence (forbidden by the evidence standard);
- allowlist with a named owner and a hard re-check round (the mechanism `§2.3.1` defines).

This round takes the third path. The allowlist has **175 entries** (one per unique
`module, name`), each with:

- `reason`: names the missing blob and why no fetch was possible;
- `owner`: `gugu-reb-r02-rehearsal`;
- `latest_review_round`: `r02`.

The 266 occurrences are fully covered; `counts.unresolved == 0` means "no unclassified
import", not "every symbol proven present". `reports/drift-v0.22.5.json` keeps the
distinction visible (`counts.allowlisted = 266`, `allowlist_entries = 175`), and r02 must
resolve every entry on a full clone before A3 starts.
