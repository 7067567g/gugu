#!/usr/bin/env python3
"""A0 manifest: base/HEAD blob sha256 for gugu's kernel patch stack + gugu tests.

The patch stack is measured from the fork root (``FORK_BASE``) to ``HEAD`` over the
five kernel directories.  The ratchet numbers in LONG-LINE-PLAN §2.2 are asserted,
not assumed: 26 modified / 9 added / +774 / +1,441, and 18 gugu test files / +3,114.

Usage:
    python scripts/upstream/build_rebase_manifest.py            # write reports/rebase-manifest.json
    python scripts/upstream/build_rebase_manifest.py --check    # verify counts only, exit 0/1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

FORK_BASE = "171fdfd76978370d32e8a7e7c2b9865b6b4f47e4"
KERNEL_DIRS = ("agent", "tools", "hermes_cli", "gateway", "tui_gateway")
TEST_DIR = "tests"

# LONG-LINE-PLAN §2.2 ratchet + §1.2 measured test surface.
EXPECTED = {
    "kernel_total_files": 35,
    "kernel_modified_files": 26,
    "kernel_added_files": 9,
    "kernel_modified_added_lines": 774,
    "kernel_modified_deleted_lines": 65,
    "kernel_added_lines": 1441,
    "kernel_total_added_lines": 2215,
    "gugu_test_files": 18,
    "gugu_test_added_lines": 3114,
}

SCHEMA = "gugu.rebase-manifest/v1"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"},
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def rev_parse(repo: Path, rev: str) -> str:
    return _git(repo, "rev-parse", "--verify", rev).strip()


def parse_numstat(text: str) -> dict[str, tuple[int, int]]:
    """Parse ``git diff --numstat`` output into ``{path: (added, deleted)}``."""
    rows: dict[str, tuple[int, int]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        added, deleted, path = line.split("\t", 2)
        rows[path] = (int(added) if added != "-" else 0, int(deleted) if deleted != "-" else 0)
    return rows


def parse_name_status(text: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        status, path = line.split("\t", 1)
        rows[path] = status
    return rows


def blob_sha256(repo: Path, blob_sha: str) -> str:
    proc = subprocess.Popen(
        ["git", "-C", str(repo), "cat-file", "blob", blob_sha],
        stdout=subprocess.PIPE,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"},
    )
    digest = hashlib.sha256()
    assert proc.stdout is not None
    for chunk in iter(lambda: proc.stdout.read(1 << 20), b""):
        digest.update(chunk)
    if proc.wait() != 0:
        raise RuntimeError(f"git cat-file blob {blob_sha} failed")
    return digest.hexdigest()


def check_counts(counts: dict[str, int]) -> dict[str, bool]:
    return {key: counts.get(key) == want for key, want in EXPECTED.items()}


def build_manifest(repo: Path, base: str = FORK_BASE) -> dict:
    head = rev_parse(repo, "HEAD")
    base_full = rev_parse(repo, base)

    kernel_numstat = parse_numstat(
        _git(repo, "diff", "--numstat", "--no-renames", f"{base_full}..HEAD", "--", *KERNEL_DIRS)
    )
    kernel_status = parse_name_status(
        _git(repo, "diff", "--name-status", "--no-renames", f"{base_full}..HEAD", "--", *KERNEL_DIRS)
    )
    test_numstat = parse_numstat(
        _git(repo, "diff", "--numstat", "--no-renames", f"{base_full}..HEAD", "--", TEST_DIR)
    )
    test_status = parse_name_status(
        _git(repo, "diff", "--name-status", "--no-renames", f"{base_full}..HEAD", "--", TEST_DIR)
    )
    if set(kernel_numstat) != set(kernel_status):
        raise RuntimeError("kernel numstat/name-status disagree")
    if set(test_numstat) != set(test_status):
        raise RuntimeError("test numstat/name-status disagree")

    def entries(numstat: dict[str, tuple[int, int]], status: dict[str, str]) -> list[dict]:
        out: list[dict] = []
        for path in sorted(numstat):
            st = status[path]
            added, deleted = numstat[path]
            head_blob = rev_parse(repo, f"HEAD:{path}")
            if st == "A":
                base_blob = None
                base_sha256 = None
            elif st == "M":
                base_blob = rev_parse(repo, f"{base_full}:{path}")
                base_sha256 = blob_sha256(repo, base_blob)
            else:
                raise RuntimeError(f"unexpected status {st!r} for {path}")
            out.append(
                {
                    "path": path,
                    "status": st,
                    "base_blob": base_blob,
                    "base_sha256": base_sha256,
                    "head_blob": head_blob,
                    "head_sha256": blob_sha256(repo, head_blob),
                    "added_lines": added,
                    "deleted_lines": deleted,
                }
            )
        return out

    kernel = entries(kernel_numstat, kernel_status)
    tests = entries(test_numstat, test_status)

    modified = [e for e in kernel if e["status"] == "M"]
    added = [e for e in kernel if e["status"] == "A"]
    counts = {
        "kernel_total_files": len(kernel),
        "kernel_modified_files": len(modified),
        "kernel_added_files": len(added),
        "kernel_modified_added_lines": sum(e["added_lines"] for e in modified),
        "kernel_modified_deleted_lines": sum(e["deleted_lines"] for e in modified),
        "kernel_added_lines": sum(e["added_lines"] for e in added),
        "kernel_total_added_lines": sum(e["added_lines"] for e in kernel),
        "gugu_test_files": len(tests),
        "gugu_test_added_lines": sum(e["added_lines"] for e in tests),
    }
    checks = check_counts(counts)
    checks["kernel_statuses_only_A_or_M"] = all(e["status"] in ("A", "M") for e in kernel)
    checks["test_statuses_only_A_or_M"] = all(e["status"] in ("A", "M") for e in tests)
    return {
        "schema": SCHEMA,
        "repo": str(repo),
        "base_commit": base_full,
        "head_commit": head,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": {
            "kernel_dirs": list(KERNEL_DIRS),
            "test_dir": TEST_DIR,
            "diff": "git diff --numstat/--name-status --no-renames <base>..HEAD",
            "blob_sha256": "sha256 over `git cat-file blob <blob-sha>`",
        },
        "expected": EXPECTED,
        "counts": counts,
        "checks": checks,
        "all_checks_pass": all(checks.values()),
        "kernel_files": kernel,
        "gugu_test_files": tests,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--base", default=FORK_BASE)
    parser.add_argument("--out", default="reports/rebase-manifest.json")
    parser.add_argument("--check", action="store_true", help="verify counts without writing")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    manifest = build_manifest(repo, args.base)
    counts = manifest["counts"]
    print(f"kernel files={counts['kernel_total_files']} "
          f"(M={counts['kernel_modified_files']} A={counts['kernel_added_files']}) "
          f"+{counts['kernel_total_added_lines']}/-{counts['kernel_modified_deleted_lines']}")
    print(f"modified +{counts['kernel_modified_added_lines']} "
          f"new +{counts['kernel_added_lines']}")
    print(f"gugu tests={counts['gugu_test_files']} +{counts['gugu_test_added_lines']}")
    failed = [k for k, ok in manifest["checks"].items() if not ok]
    if failed:
        print(f"FAIL checks: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("OK all manifest checks pass")
    if not args.check:
        out = Path(args.out)
        if not out.is_absolute():
            out = repo / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
