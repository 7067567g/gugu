# Remote semantics correction — `upstream` is a gugu mirror, `hermes` is the upstream

- run: `gugu-reb-r01-prep` (A1)
- repo: `/home/a7067567g/下载/deepseek/60-外部仓库/gugu-work`
- date: 2026-10-25
- machine-readable record: `arch/upstream.lock`

## 1. Observed remotes (before → after)

Before this round:

| remote | URL | push | meaning |
| --- | --- | --- | --- |
| `origin` | `https://github.com/7067567g/gugu.git` | allowed | gugu product repo |
| `upstream` | `https://github.com/joydove/gugu.git` | `DISABLED` | **gugu mirror** |

After A1:

| remote | URL | push | meaning |
| --- | --- | --- | --- |
| `origin` | `https://github.com/7067567g/gugu.git` | allowed | gugu product repo |
| `upstream` | `https://github.com/joydove/gugu.git` | `DISABLED` | **gugu mirror — NOT Hermes upstream** |
| `hermes` | `/home/a7067567g/下载/deepseek/60-外部仓库/hermes-upstream` | `DISABLED` | read-only local mirror of `NousResearch/hermes-agent` |

`hermes` was added with a local path on purpose: A0/A1 must not use the network. Its
fetch is additionally pinned with `remote.hermes.tagOpt=--no-tags` so a plain
`git fetch hermes` cannot drag in tags by accident.

## 2. The correction

`upstream` has never pointed at `NousResearch/hermes-agent`. It points at
`joydove/gugu`, a gugu mirror. Any script that runs `git fetch upstream` and expects
Hermes is fetching a second copy of gugu. The Hermes upstream is the `hermes` remote,
and `arch/upstream.lock` records the tag/commit that was verified from it.

The remote was **not renamed** to `mirror` in this round: the rename is an operational
change that touches the product updater's assumptions (see §4) and belongs to the A6 /
docs round, not to a zero-product-code A0/A1 round. The name stays; the semantics are
corrected here and in the lock.

## 3. Tag verification

```text
$ git -C /home/a7067567g/下载/deepseek/60-外部仓库/hermes-upstream rev-parse 'v0.22.5^{commit}'
bf0203a794a07aac3ff764972ab9087baa4e4f4e
$ git rev-parse 'refs/remotes/hermes/v0.22.5^{commit}'
bf0203a794a07aac3ff764972ab9087baa4e4f4e
```

- annotated tag object: `56b6a31fededdf7364e1a0a79548d144e7fd81a3` (`tag v0.22.5`,
  tagger `Hermes Release Automation`, target `bf0203a7`, version `0.22.5`)
- commit date/subject: `2026-10-22` / `fix(launchers): a Python repin republishes hermes.exe, not a .cmd`

### Fetch caveat (recorded, not hidden)

A **direct** `git fetch` from the mirror fails offline:

```text
remote: warning: lazy fetching disabled; some objects may not be available
remote: fatal: could not fetch 614c440766318b3281622238ba647993d6d7783e from promisor remote
fatal: git upload-pack: aborting due to possible repository corruption on the remote side
```

The mirror is a `blob:none` partial clone and the missing object is in its history; with
`GIT_NO_LAZY_FETCH=1` (required: no network) it cannot pack a complete history. The tag
was therefore fetched through a **temporary local relay**: a throwaway bare repo whose
`objects/info/alternates` is the mirror's object store, with `uploadpack.allowFilter=true`
set on the relay only. That serves a filtered pack from the mirror's local objects;
`GIT_NO_LAZY_FETCH=1` stayed on end-to-end. The relay was deleted; the target repo keeps
the fetched commit/trees (57,745 reachable commits, no blobs) and `hermes` as the
promisor for any later lazy fetch. This is recorded in `arch/upstream.lock.fetch`.

A later round with a full mirror (or an explicitly approved networked fetch) can replace
the relay with a plain `git fetch hermes refs/tags/v0.22.5:refs/remotes/hermes/v0.22.5`.

## 4. Coupling found (product code — untouched this round)

`hermes_cli/update_cmd_git.py` assumes the remote named `upstream` is
`NousResearch/hermes-agent`:

- line 271 prints `Added upstream: https://github.com/NousResearch/hermes-agent.git`;
- line 291 runs `git fetch upstream main` and compares `upstream/main` with `origin/main`.

On this repo `upstream` already exists (joydove/gugu), so `_has_upstream_remote` sees it
and the updater would compare gugu against gugu — never against Hermes. This is a real
semantic collision, not just documentation drift. It is **out of scope** for A0/A1
(product code zero-diff), and is carried into the next round:

> A6/docs: rename the `upstream` remote to `mirror` (or teach the updater to resolve
> `hermes` explicitly), then re-run the updater's upstream-sync tests.

## 5. Stale-text sweep

Read-only checks over tracked text:

```text
$ git grep -n 'joydove'
README.md:54:git clone https://github.com/joydove/gugu.git && cd gugu
docs/03-getting-started.md:17:git clone https://github.com/joydove/gugu.git
# plus product/security addresses and the desktop appId — all intentional
$ git grep -n 'fetch upstream\|upstream/v'
# only LONG-LINE-PLAN (which already calls the remote misleading) and generic
# GitHub/update-skill docs; no tracked doc claims 'upstream' is Hermes.
```

Conclusion: the correction is operational (lock + this report + README of the tooling),
not a bulk docs rewrite. The one product collision is §4.
