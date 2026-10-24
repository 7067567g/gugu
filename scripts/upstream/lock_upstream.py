#!/usr/bin/env python3
"""Write ``arch/upstream.lock``: the verified Hermes tag/commit plus remote semantics.

This is the machine-readable correction of the repo's remote story: ``upstream`` is a
gugu mirror (``joydove/gugu``), *not* Hermes; Hermes is reached through the read-only
``hermes`` remote that points at the local mirror.  Fetching the tag from that local
path is what keeps A0/A1 offline.

Usage:
    python scripts/upstream/lock_upstream.py            # write arch/upstream.lock
    python scripts/upstream/lock_upstream.py --check    # verify the file matches reality
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TAG = "v0.22.5"
EXPECTED_COMMIT = "bf0203a794a07aac3ff764972ab9087baa4e4f4e"
DEFAULT_MIRROR = "/home/a7067567g/下载/deepseek/60-外部仓库/hermes-upstream"
LOCAL_REF = "refs/remotes/hermes/v0.22.5"
SCHEMA = "gugu.upstream-lock/v1"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"},
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}: {proc.stderr.strip()}")
    return proc


def _rev_parse(repo: Path, rev: str) -> str | None:
    proc = _git(repo, "rev-parse", "--verify", rev, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def parse_remote_v(text: str) -> dict[str, dict[str, str]]:
    """Parse ``git remote -v`` into ``{name: {"fetch": url, "push": url}}``."""
    out: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        match = re.match(r"^(\S+)\t(.+) \((fetch|push)\)(?: \[[^\]]+\])?$", line.rstrip())
        if not match:
            continue
        name, url, kind = match.groups()
        out.setdefault(name, {})[kind] = url
    return out


def build_lock(repo: Path, mirror: Path) -> dict:
    commit = _rev_parse(mirror, f"{TAG}^{{commit}}")
    if not commit:
        raise RuntimeError(f"{TAG} is not present in {mirror}")
    tag_object = _rev_parse(mirror, TAG)
    tag_type = _git(mirror, "cat-file", "-t", TAG).stdout.strip()
    meta = _git(mirror, "show", "-s", "--format=%cs%x00%s", commit).stdout.rstrip("\n").split("\x00", 1)
    remotes = parse_remote_v(_git(repo, "remote", "-v").stdout)
    local_commit = _rev_parse(repo, f"{LOCAL_REF}^{{commit}}")
    hermes = remotes.get("hermes", {})
    lock = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tag": TAG,
        "tag_object": tag_object,
        "tag_object_type": tag_type,
        "commit": commit,
        "commit_short": commit[:8],
        "commit_date": meta[0] if len(meta) > 0 else None,
        "commit_subject": meta[1] if len(meta) > 1 else None,
        "expected_commit": EXPECTED_COMMIT,
        "verified": commit == EXPECTED_COMMIT,
        "local_ref": LOCAL_REF,
        "local_commit": local_commit,
        "local_ref_matches": local_commit == EXPECTED_COMMIT,
        "fetch": {
            "filter": "blob:none",
            "partial": True,
            "tag_object": tag_object,
            "tag_object_type": tag_type,
            "local_commit": local_commit,
            "direct_refetch_idempotent": local_commit == EXPECTED_COMMIT,
            "evidence": (
                "the local mirror is a blob:none partial clone, so a direct fetch cannot pack "
                "its own missing history blob (614c440766318b3281622238ba647993d6d7783e). "
                "The tag was fetched with GIT_NO_LAZY_FETCH=1 through a temporary local relay "
                "whose object store is the mirror's (alternates) and whose uploadpack.allowFilter "
                "is enabled; the relay was deleted after the fetch. No network was used."
            ),
        },
        "remote": {
            "name": "hermes",
            "fetch_url": hermes.get("fetch"),
            "push_url": hermes.get("push"),
            "readonly": hermes.get("push") == "DISABLED",
            "promisor": True,
            "partial_clone_filter": "blob:none",
            "local_mirror": str(mirror),
            "refspec": f"refs/tags/{TAG}:{LOCAL_REF}",
            "fetch_command": (
                f"GIT_NO_LAZY_FETCH=1 git fetch --no-tags hermes "
                f"refs/tags/{TAG}:{LOCAL_REF}"
            ),
        },
        "remotes": {
            "origin": {
                **remotes.get("origin", {}),
                "semantics": "gugu product remote (7067567g/gugu); human-writable, never pushed by automation",
            },
            "upstream": {
                **remotes.get("upstream", {}),
                "semantics": (
                    "GUGU MIRROR — github.com/joydove/gugu. This is NOT the Hermes upstream; "
                    "push is DISABLED. Never use it as the rebase source."
                ),
            },
            "hermes": {
                **hermes,
                "semantics": (
                    "read-only mirror of NousResearch/hermes-agent at a local path; "
                    "fetch only from the local path, never from the network in A0/A1"
                ),
            },
        },
        "upstream_semantics": {
            "hermes_upstream": "hermes remote (local mirror of NousResearch/hermes-agent)",
            "gugu_mirror": "upstream remote (joydove/gugu) — not an upstream",
            "rule": "scripts/docs must never fetch the rebase source from the 'upstream' remote",
        },
        "verify_commands": [
            f"git -C {mirror} rev-parse '{TAG}^{{commit}}'",
            f"git -C {repo} rev-parse '{LOCAL_REF}^{{commit}}'",
        ],
        "repo_partial_clone": True,
        "manifest": "reports/rebase-manifest.json",
        "notes": [
            "The local mirror is a blob:none partial clone; tag/tree/commit are local, "
            "some module blobs are not. No network fetch is performed in A0/A1.",
            "The 'upstream' remote keeps its name to avoid breaking existing scripts; "
            "its semantics are corrected here and in reports/upstream-remote.md.",
        ],
    }
    return lock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--mirror", default=DEFAULT_MIRROR)
    parser.add_argument("--out", default="arch/upstream.lock")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    mirror = Path(args.mirror).resolve()
    lock = build_lock(repo, mirror)

    ok = bool(lock["verified"] and lock["local_ref_matches"] and lock["remote"]["readonly"])
    print(f"tag={lock['tag']} object={lock['tag_object']} ({lock['tag_object_type']})")
    print(f"commit={lock['commit']} expected={lock['expected_commit']} verified={lock['verified']}")
    print(f"local_ref={lock['local_ref']} matches={lock['local_ref_matches']}")
    print(f"hermes remote fetch={lock['remote']['fetch_url']} push={lock['remote']['push_url']}")
    if not ok:
        print("FAIL lock preconditions (commit/local-ref/read-only remote)", file=sys.stderr)

    out = Path(args.out)
    if not out.is_absolute():
        out = repo / out
    if args.check:
        if not out.exists():
            print(f"FAIL {out} is missing", file=sys.stderr)
            return 1
        current = json.loads(out.read_text(encoding="utf-8"))
        comparable = ("tag", "tag_object", "commit", "expected_commit", "verified", "local_commit",
                      "local_ref_matches", "remote", "remotes", "upstream_semantics")
        mismatch = [key for key in comparable if current.get(key) != lock.get(key)]
        if mismatch:
            print(f"FAIL lock mismatch in: {', '.join(mismatch)}", file=sys.stderr)
            return 1
        print(f"OK {out} matches the verified tag/commit and remote semantics")
        return 0 if ok else 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
