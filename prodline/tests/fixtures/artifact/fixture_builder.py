"""Deterministic artifact fixtures for the ``artifact-manifest/v1`` contract.

The fixtures are **text only**: no zip is committed.  For each case in
``cases.json`` this module builds, in a temporary directory:

* a small source tree from ``tree/base`` (plus ``zip_add`` / ``zip_remove``);
* ``FILES.sha256`` from the base tree (with optional hash overrides);
* a deterministic zip (fixed timestamps and 100644 modes);
* a manifest whose ``git_tree``/``commit`` come from a **real** temporary git
  repository, so the checker's tree rebuild is verified against git rather than
  against itself;
* a dummy build receipt so ``--build-receipt`` can be exercised.

Case keys (all optional except ``id``/``expect``):

``zip_add``            ``{path: text}`` added to the zip but not to FILES
``zip_remove``         ``[path]`` removed from both zip and FILES generation
``files_omit``         ``[path]`` still in the zip, deliberately not in FILES
``files_hash_override`` ``{path: 64-hex}`` wrong per-path hash in FILES
``files_append``       ``[raw line]`` appended to FILES after hashing
``files_patch``        ``[[old, new]]`` raw text substitutions in FILES
``manifest_patch``     RFC 7386 merge patch applied last (``null`` deletes)
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
BASE_TREE = HERE / "tree" / "base"
FILES_MANIFEST = "FILES.sha256"
ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
RUNNER = {
    "os": "linux",
    "image": "fixture",
    "arch": "x86_64",
    "python": "3.12.15",
    "uv": "0.12.22",
    "node": "24.22.1",
    "npm": "11.6.0",
    "git": "2.43.0",
}
EXCLUDES = [
    ".git/**",
    "node_modules/**",
    ".venv/**",
    "apps/desktop/dist/**",
    "apps/desktop/build/**",
    "apps/desktop/release/**",
    "artifacts/**",
    "__pycache__/**",
]


@dataclass(frozen=True)
class GitMeta:
    repo: Path
    commit: str
    tree: str


@dataclass(frozen=True)
class BuiltCase:
    case: dict[str, Any]
    zip_path: Path
    manifest_path: Path
    manifest: dict[str, Any]
    build_receipt_path: Path
    git_meta: GitMeta


def read_base_files() -> dict[str, bytes]:
    return {
        path.relative_to(BASE_TREE).as_posix(): path.read_bytes()
        for path in sorted(BASE_TREE.rglob("*"))
        if path.is_file()
    }


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


@lru_cache(maxsize=1)
def default_git_meta() -> GitMeta:
    """A real commit of ``tree/base``; its tree id is the fixture ground truth."""
    root = Path(tempfile.mkdtemp(prefix="gugu-artifact-fixture-git-"))
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    for rel, data in read_base_files().items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="gugu-fixture",
        GIT_AUTHOR_EMAIL="fixture@example.invalid",
        GIT_COMMITTER_NAME="gugu-fixture",
        GIT_COMMITTER_EMAIL="fixture@example.invalid",
    )
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "fixture", env=env)
    return GitMeta(
        repo=root,
        commit=_git(root, "rev-parse", "HEAD"),
        tree=_git(root, "rev-parse", "HEAD^{tree}"),
    )


def merge_patch(target: Any, patch: Any) -> Any:
    """RFC 7386 JSON Merge Patch (``null`` deletes an object member)."""
    if not isinstance(patch, dict):
        return patch
    result = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # Unix, so the mode is meaningful
            info.external_attr = 0o100644 << 16
            archive.writestr(info, members[name])


def _files_text(case: dict[str, Any], base: dict[str, bytes]) -> str:
    omitted = set(case.get("files_omit") or [])
    overrides = case.get("files_hash_override") or {}
    lines = []
    for path in sorted(base):
        if path in omitted:
            continue
        digest = str(overrides.get(path) or _sha256(base[path]))
        lines.append(f"{digest}  {path}")
    for raw in case.get("files_append") or []:
        lines.append(str(raw))
    text = "\n".join(lines) + "\n"
    for old, new in case.get("files_patch") or []:
        text = text.replace(str(old), str(new))
    return text


def _manifest(
    base: dict[str, bytes],
    files_text: str,
    zip_bytes: bytes,
    build_receipt_bytes: bytes,
    git_meta: GitMeta,
) -> dict[str, Any]:
    product = base["GUGU_VERSION"].decode("utf-8").strip()
    pyproject = tomllib.loads(base["pyproject.toml"].decode("utf-8"))
    desktop = json.loads(base["apps/desktop/package.json"])["version"]
    upstream_tag = json.loads(base["arch/upstream.lock"])["tag"]
    kernel_version = pyproject["project"]["version"]
    return {
        "schema": "artifact-manifest/v1",
        "commit": git_meta.commit,
        "git_tree": git_meta.tree,
        "provenance": "real",
        "product_version": product,
        "desktop_version": desktop,
        "upstream_kernel_version": kernel_version,
        "upstream_tag": upstream_tag,
        "artifact_sha256": "sha256:" + _sha256(zip_bytes),
        "artifact_bytes": len(zip_bytes),
        "files_sha256": "sha256:" + _sha256(files_text.encode("utf-8")),
        "files_count": len([line for line in files_text.splitlines() if line]),
        "files_manifest": FILES_MANIFEST,
        "built_at": "2026-10-25T12:00:00Z",
        "runner": dict(RUNNER),
        "dirty": False,
        "lockfiles": {
            name: "sha256:" + _sha256(base[name])
            for name in ("uv.lock", "package-lock.json")
        },
        "includes": {
            "pyproject.toml": True,
            "GUGU_VERSION": True,
            "scripts/install.ps1": True,
        },
        "excludes": list(EXCLUDES),
        "build_receipt_sha256": "sha256:" + _sha256(build_receipt_bytes),
        "producer": {
            "stage": "build",
            "tool": "prodline/stages/build.py",
            "contract_version": 1,
        },
    }


def build_case(
    case: dict[str, Any],
    work_dir: Path | str,
    *,
    git_meta: GitMeta | None = None,
) -> BuiltCase:
    """Materialise one case under ``work_dir/<case id>`` and return its paths."""
    case = dict(case)
    case_id = str(case.get("id") or "case")
    out = Path(work_dir) / case_id
    out.mkdir(parents=True, exist_ok=True)
    meta = git_meta or default_git_meta()

    base = read_base_files()
    files_text = _files_text(case, base)

    members = dict(base)
    for path in case.get("zip_remove") or []:
        members.pop(path, None)
    for path, text in (case.get("zip_add") or {}).items():
        members[str(path)] = str(text).encode("utf-8")
    if FILES_MANIFEST not in (case.get("zip_remove") or []):
        members[FILES_MANIFEST] = files_text.encode("utf-8")

    zip_path = out / "artifact.zip"
    _write_zip(zip_path, members)
    zip_bytes = zip_path.read_bytes()

    build_receipt = out / "build-receipt.json"
    build_receipt.write_text(
        json.dumps({"schema": "build-receipt/v1", "commit": meta.commit}) + "\n",
        encoding="utf-8",
    )

    manifest = _manifest(base, files_text, zip_bytes, build_receipt.read_bytes(), meta)
    manifest = merge_patch(manifest, case.get("manifest_patch") or {})
    manifest_path = out / "artifact-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return BuiltCase(
        case=case,
        zip_path=zip_path,
        manifest_path=manifest_path,
        manifest=manifest,
        build_receipt_path=build_receipt,
        git_meta=meta,
    )
