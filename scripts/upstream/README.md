# scripts/upstream — A0/A1 tooling

Offline baseline tools for the gugu → `v0.22.5` re-baseline. No network, no product-code
changes; everything is reproducible from the local repo + the local Hermes mirror.

## Tools

| Tool | Purpose |
| --- | --- |
| `build_rebase_manifest.py` | base/HEAD blob sha256 for the 35 kernel files (26 M + 9 A) and 18 gugu tests; asserts 26/9/774/1441. |
| `check_symbol_drift.py` | AST import/symbol scan of the 35 kernel files against `v0.22.5`; 0 unresolved, or every allowlist entry has reason + owner + latest review round. |
| `lock_upstream.py` | writes `arch/upstream.lock` (tag object, commit, remotes, partial-clone fetch facts). |

```bash
cd /home/a7067567g/下载/deepseek/60-外部仓库/gugu-work
.venv/bin/python scripts/upstream/build_rebase_manifest.py --check
.venv/bin/python scripts/upstream/check_symbol_drift.py --check
.venv/bin/python scripts/upstream/lock_upstream.py --check
.venv/bin/python -m unittest discover -s scripts/upstream/tests -v
```

## The partial-clone caveat

`hermes-upstream` is a `blob:none` partial clone. Tag/tree/commit objects are local; a
subset of module blobs is not. The scanner therefore:

1. parses the tag blob when it is present locally;
2. otherwise accepts a **byte-identical proxy**: if the `v0.22.5` blob sha equals the
   fork-base blob sha or the mirror `main` blob sha, the content is provably the same;
3. otherwise records the import in `symbol-drift-allowlist.json` with a reason naming the
   missing blob, owner `gugu-reb-r02-rehearsal`, and `latest_review_round: r02`.

The allowlist is a deferred *verification*, never a claim that the symbol exists. A full
clone (or a networked fetch window) in r02 must resolve every entry. Direct `git fetch`
from the mirror fails offline because the mirror cannot pack its own missing history blob
(`614c440766318b3281622238ba647993d6d7783e`); A1 fetched the tag through a temporary local
relay whose object store was the mirror's (see `arch/upstream.lock.fetch.evidence`).

## Remote semantics

`upstream` is **`joydove/gugu`, a gugu mirror — not the Hermes upstream**. `hermes` is
the read-only remote for the local Hermes mirror. See `reports/upstream-remote.md`.
