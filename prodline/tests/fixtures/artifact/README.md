# artifact contract fixtures (A01)

Text-only fixtures for [`artifact-manifest/v1`](../../../contracts/artifact-manifest.v1.md)
and [`artifact_check.py`](../../../gates/artifact_check.py). **No zip is committed**:
each case is built in a temporary directory by `fixture_builder.py`, so the
fixtures cannot rot into a binary blob nobody can review.

## Layout

```text
cases.json               # case id -> patches -> expected verdict + problem substrings
tree/base/               # the clean source tree (9 files, nested dirs included)
fixture_builder.py       # builds: FILES.sha256 -> deterministic zip -> manifest
test_artifact_contract.py# pytest matrix + contract invariants + CLI exit codes
```

`fixture_builder.py` derives `commit` and `git_tree` from a **real temporary git
repository** created from `tree/base`; the checker's tree rebuild is therefore
compared to git, not to itself. `FILES.sha256` is the only synthetic zip member
(it is not in the commit tree and is excluded from the rebuilt tree, per the
contract).

## Case keys

| key | effect |
| --- | --- |
| `zip_add` | add `{path: text}` to the zip, not to `FILES.sha256` (extra-member / unsafe / forbidden cases) |
| `zip_remove` | remove a path from the zip; it stays in `FILES.sha256` (missing-member) |
| `files_omit` | keep the path in the zip but not in `FILES.sha256` (unlisted member) |
| `files_hash_override` | write a wrong 64-hex hash for a path (per-file sha mismatch) |
| `files_append` | append raw line(s) to `FILES.sha256` (self-listing / malformed) |
| `files_patch` | raw `[[old, new]]` substitutions in `FILES.sha256` |
| `manifest_patch` | RFC 7386 merge patch on the manifest (`null` deletes) |

## Mandated negative classes

`cases.json` must always contain these four (checked by the test):

- `missing-git-tree` — a required field is absent;
- `dirty-true` — `dirty` must be `false`;
- `version-mismatch` — `desktop_version` != the file inside the zip;
- `files-count-mismatch` — `files_count` != real `FILES.sha256` line count.

Plus: artifact sha/bytes, files sha, extra/missing/unlisted members, per-file
hash, zip-slip, absolute path, forbidden `.git/**`, rebuilt tree mismatch,
fixture without reason, missing runner key, bad producer/schema, self-listing.

## Run

```bash
cd /home/a7067567g/下载/deepseek/60-外部仓库/gugu-work
.venv/bin/python -m pytest prodline/tests/fixtures/artifact -q
.venv/bin/python -m pytest prodline/tests -q
```

The matrix is data-driven; add a case to `cases.json` and (if it needs a new
kind of mutation) extend `fixture_builder.py`. Do not weaken an existing
`problems_contain` to make a case pass.
