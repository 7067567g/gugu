"""Single source of truth for the version lines of this checkout (stdlib only).

Two version lines coexist in the gugu workspace and must not be conflated:

* **product** — the gugu product version, declared once in the root ``GUGU_VERSION`` file.
  This is what the CLI shows first (``gugu v<product>``) and what releases/tags follow.
* **upstream kernel** — the Hermes Agent version this tree is built on, declared in
  ``pyproject.toml`` under ``[project].version``. It is *not* rewritten by gugu releases.

This module is the one reader for both, so the startup banner, ``hermes --version`` and
``build_info`` never re-derive the strings (or the file locations) themselves.

Deliberately stdlib-only and import-cheap: it sits on the ``hermes --version`` fast path,
where importing anything heavy (rich, prompt_toolkit, requests) would defeat the point.
Neither reader raises — an unreadable/broken source degrades to ``None``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

#: Root file holding the gugu product version (the release-line single source).
PRODUCT_VERSION_FILE = "GUGU_VERSION"
#: Root file holding the upstream Hermes Agent kernel version.
UPSTREAM_VERSION_FILE = "pyproject.toml"
#: The four version-source groups recorded in ``version-sources.json`` (D-004).
PRODUCT_VERSION_GROUPS = ("product", "upstream_kernel", "upstream_desktop", "workspace_unclassified")

_SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _root(root: Optional[Path]) -> Path:
    return Path(root) if root is not None else _REPO_ROOT


def read_product_version(root: Optional[Path] = None) -> Optional[str]:
    """The gugu product version from ``root/GUGU_VERSION``, or ``None``.

    ``None`` when the file is missing/unreadable or does not hold a bare semver — callers
    treat that as "product identity unknown" and degrade, never crash.
    """
    try:
        text = (_root(root) / PRODUCT_VERSION_FILE).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text if _SEMVER.fullmatch(text) else None


def read_upstream_version(root: Optional[Path] = None) -> Optional[str]:
    """The upstream kernel version from ``root/pyproject.toml`` ``[project].version``, or ``None``."""
    try:
        import tomllib

        with open(_root(root) / UPSTREAM_VERSION_FILE, "rb") as handle:  # windows-footgun: ok — binary mode, tomllib requires bytes
            raw = tomllib.load(handle).get("project", {}).get("version")
    except Exception:
        return None
    return str(raw) if raw else None


def version_summary(root: Optional[Path] = None) -> dict:
    """``{product, upstream_kernel, product_file, upstream_file}`` for the given tree.

    Each version field degrades to ``None`` independently; the file names always resolve.
    """
    return {
        "product": read_product_version(root),
        "upstream_kernel": read_upstream_version(root),
        "product_file": PRODUCT_VERSION_FILE,
        "upstream_file": UPSTREAM_VERSION_FILE,
    }
