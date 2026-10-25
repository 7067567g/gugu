"""Mechanical release-line consistency checks for gugu (stdlib only, no network).

Original three release-line assertions (GUGU_VERSION ↔ CHANGELOG ↔ README) are kept, plus
WS-2 / T-02 additions: the machine-readable version-source manifest (``version-sources.json``),
the CLI product-identity label, and ``build_info.product_version`` sharing the single source.

The product version is always read from ``GUGU_VERSION`` dynamically (never hard-coded) so
these checks survive release bumps; the upstream constants are asserted from the manifest.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")

_EXPECTED_GROUPS = {"product", "upstream_kernel", "upstream", "upstream_desktop", "workspace_unclassified"}


def _declared_product_version(root: Path) -> str:
    return (root / "GUGU_VERSION").read_text(encoding="utf-8").strip()


def release_consistency_problems(root: Path) -> list[str]:
    problems: list[str] = []
    version_file = root / "GUGU_VERSION"
    changelog = root / "docs" / "CHANGELOG-gugu.md"
    readme = root / "README.md"

    if not version_file.is_file():
        return [f"missing {version_file}"]
    version = version_file.read_text(encoding="utf-8").strip()
    if not _SEMVER.fullmatch(version):
        problems.append(f"GUGU_VERSION is not valid semver: {version!r}")

    if not changelog.is_file():
        problems.append(f"missing {changelog}")
    else:
        text = changelog.read_text(encoding="utf-8")
        if not re.search(rf"(?m)^##\s+{re.escape(version)}(?=\s|$)", text):
            problems.append(f"docs/CHANGELOG-gugu.md has no '## {version}' section")

    if not readme.is_file():
        problems.append(f"missing {readme}")
    else:
        rows = [ln for ln in readme.read_text(encoding="utf-8").splitlines()
                if ln.lstrip().startswith("|")]
        if not any(f"gugu-v{version}" in ln for ln in rows):
            problems.append(f"README.md has no 'gugu-v{version}' version-table row")
    return problems


def test_repo_release_line_is_consistent() -> None:
    assert release_consistency_problems(Path(__file__).resolve().parents[2]) == []


def test_invalid_semver_is_reported(tmp_path: Path) -> None:
    (tmp_path / "GUGU_VERSION").write_text("0.3\n", encoding="utf-8")
    assert any("semver" in p for p in release_consistency_problems(tmp_path))


def test_missing_changelog_section_is_reported(tmp_path: Path) -> None:
    (tmp_path / "GUGU_VERSION").write_text("9.9.9\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "CHANGELOG-gugu.md").write_text("# c\n\n## 0.1.0 — x\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("| gugu-v0.1.0 |\n", encoding="utf-8")
    assert any("## 9.9.9" in p for p in release_consistency_problems(tmp_path))


def test_missing_readme_row_is_reported(tmp_path: Path) -> None:
    (tmp_path / "GUGU_VERSION").write_text("9.9.9\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "CHANGELOG-gugu.md").write_text("## 9.9.9 — x\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("| gugu-v0.1.0 |\n", encoding="utf-8")
    assert any("gugu-v9.9.9" in p for p in release_consistency_problems(tmp_path))


# --------------------------------------------------------------------------- WS-2 additions

_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = _ROOT / "version-sources.json"


def _load_manifest(root: Path = _ROOT) -> dict:
    return json.loads((root / "version-sources.json").read_text(encoding="utf-8"))


def _read_declared_value(root: Path, entry: dict) -> str | None:
    """Value at ``entry['path']`` + dotted ``entry['pointer']`` (``text`` = whole file)."""
    path = root / entry["path"]
    pointer = entry["pointer"]
    if pointer == "text":
        return path.read_text(encoding="utf-8").strip()
    if path.suffix in {".json", ".lock"}:  # arch/upstream.lock is JSON with a .lock suffix
        node = json.loads(path.read_text(encoding="utf-8"))
    elif path.suffix == ".toml":
        import tomllib

        with open(path, "rb") as fh:
            node = tomllib.load(fh)
    else:  # pragma: no cover - keeps failures loud instead of silently passing
        raise AssertionError(f"unsupported manifest source: {entry['path']}")
    for key in pointer.split("."):
        node = node[key]
    return str(node)


def _write_minimal_tree(root: Path) -> None:
    """A self-contained manifest tree so manifest tests never lean on the real checkout."""
    (root / "GUGU_VERSION").write_text("9.9.9\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "1.2.3"\n', encoding="utf-8")
    (root / "arch").mkdir()
    (root / "arch" / "upstream.lock").write_text('{"tag": "v9.9.9", "commit": "abc"}', encoding="utf-8")
    (root / "apps" / "desktop").mkdir(parents=True)
    (root / "apps" / "desktop" / "package.json").write_text('{"version": "4.5.6"}', encoding="utf-8")
    (root / "package.json").write_text('{"version": "1.0.0"}', encoding="utf-8")
    manifest = {
        "schema": 1,
        "sources": [
            {"group": "product", "path": "GUGU_VERSION", "pointer": "text", "assert": "semver", "verified": True},
            {"group": "upstream_kernel", "path": "pyproject.toml", "pointer": "project.version", "assert": "1.2.3", "verified": True},
            {"group": "upstream", "path": "arch/upstream.lock", "pointer": "tag", "assert": "v9.9.9", "verified": True},
            {"group": "upstream_desktop", "path": "apps/desktop/package.json", "pointer": "version", "assert": "4.5.6", "verified": True},
            {"group": "workspace_unclassified", "path": "package.json", "pointer": "version", "assert": "unverified", "verified": False},
        ],
    }
    (root / "version-sources.json").write_text(json.dumps(manifest), encoding="utf-8")


def manifest_problems(root: Path = _ROOT) -> list[str]:
    """Every way the four-source manifest can disagree with the checkout on disk."""
    problems: list[str] = []
    if not (root / "version-sources.json").is_file():
        return ["missing version-sources.json"]
    manifest = _load_manifest(root)
    entries = manifest.get("sources")
    if not isinstance(entries, list) or not entries:
        return ["version-sources.json has no 'sources' list"]
    groups = [entry.get("group") for entry in entries]
    missing = _EXPECTED_GROUPS - set(groups)
    if missing:
        problems.append(f"manifest is missing groups: {sorted(missing)}")
    for entry in entries:
        for field in ("path", "pointer", "group", "assert"):
            if not entry.get(field):
                problems.append(f"manifest entry missing '{field}': {entry}")
                break
        else:
            try:
                value = _read_declared_value(root, entry)
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                problems.append(f"manifest source unreadable ({entry['path']}): {exc}")
                continue
            expectation = entry["assert"]
            if expectation == "unverified":
                if entry.get("verified") is not False:
                    problems.append(
                        f"{entry['path']}#{entry['pointer']} asserts 'unverified' but is not flagged verified=false"
                    )
            elif expectation == "semver":
                if not _SEMVER.fullmatch(value or ""):
                    problems.append(f"{entry['path']}#{entry['pointer']} is not semver: {value!r}")
            elif value != expectation:
                problems.append(
                    f"{entry['path']}#{entry['pointer']} is {value!r}, manifest asserts {expectation!r}"
                )
    return problems


def test_version_sources_manifest_declares_expected_groups() -> None:
    manifest = _load_manifest()
    groups = [entry["group"] for entry in manifest["sources"]]
    assert set(groups) == _EXPECTED_GROUPS
    assert len(groups) == len(set(groups)), f"duplicate group entries: {groups}"


def test_manifest_asserts_the_upstream_lock_tag() -> None:
    """D-103: pyproject's kernel version is the dynamic 0.0.0 placeholder, so the
    real baseline (v0.22.5 / bf0203a7) is asserted from arch/upstream.lock."""
    entry = next(e for e in _load_manifest()["sources"] if e["group"] == "upstream")
    assert entry["path"] == "arch/upstream.lock"
    assert entry["pointer"] == "tag"
    assert entry["assert"] == "v0.22.5"
    assert entry["verified"] is True


def test_version_sources_manifest_is_consistent_with_repo() -> None:
    assert manifest_problems(_ROOT) == []


def test_manifest_flags_root_package_json_as_unverified() -> None:
    entry = next(e for e in _load_manifest()["sources"] if e["path"] == "package.json")
    assert entry["assert"] == "unverified" and entry["verified"] is False


def test_manifest_reports_wrong_declared_value(tmp_path: Path) -> None:
    """Discriminating counterexample: a wrong upstream expectation must be reported."""
    _write_minimal_tree(tmp_path)
    manifest = json.loads((tmp_path / "version-sources.json").read_text(encoding="utf-8"))
    for entry in manifest["sources"]:
        if entry["path"] == "pyproject.toml":
            entry["assert"] = "9.9.9"
    (tmp_path / "version-sources.json").write_text(json.dumps(manifest), encoding="utf-8")
    problems = manifest_problems(tmp_path)
    assert any("pyproject.toml" in p and "9.9.9" in p for p in problems), problems


def test_manifest_reports_wrong_upstream_lock_tag(tmp_path: Path) -> None:
    """The lock tag is asserted literally: a different baseline must be reported."""
    _write_minimal_tree(tmp_path)
    manifest = json.loads((tmp_path / "version-sources.json").read_text(encoding="utf-8"))
    for entry in manifest["sources"]:
        if entry["group"] == "upstream":
            entry["assert"] = "v0.0.1"
    (tmp_path / "version-sources.json").write_text(json.dumps(manifest), encoding="utf-8")
    problems = manifest_problems(tmp_path)
    assert any("arch/upstream.lock" in p and "v0.0.1" in p for p in problems), problems


def test_manifest_accepts_self_consistent_tree(tmp_path: Path) -> None:
    _write_minimal_tree(tmp_path)
    assert manifest_problems(tmp_path) == []


def test_manifest_reports_unverified_flag_mismatch(tmp_path: Path) -> None:
    """Discriminating counterexample: 'unverified' must be flagged verified=false."""
    _write_minimal_tree(tmp_path)
    manifest = json.loads((tmp_path / "version-sources.json").read_text(encoding="utf-8"))
    for entry in manifest["sources"]:
        if entry["group"] == "workspace_unclassified":
            entry["verified"] = True
    (tmp_path / "version-sources.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert any("unverified" in p for p in manifest_problems(tmp_path))


def test_manifest_reports_missing_group(tmp_path: Path) -> None:
    _write_minimal_tree(tmp_path)
    broken = json.loads((tmp_path / "version-sources.json").read_text(encoding="utf-8"))
    broken["sources"] = [e for e in broken["sources"] if e["group"] != "workspace_unclassified"]
    (tmp_path / "version-sources.json").write_text(json.dumps(broken), encoding="utf-8")
    assert any("workspace_unclassified" in p for p in manifest_problems(tmp_path))


def test_cli_label_shows_product_version_and_upstream() -> None:
    import hermes_cli
    from hermes_cli.banner import format_banner_version_label

    version = _declared_product_version(_ROOT)
    label = format_banner_version_label()
    assert f"gugu v{version}" in label
    assert f"Hermes Agent v{hermes_cli.__version__}" in label


def test_build_info_product_version_is_same_source() -> None:
    import hermes_cli
    from hermes_cli.build_info import get_code_identity

    identity = get_code_identity(refresh=True)
    assert identity["product_version"] == _declared_product_version(_ROOT)
    assert identity["version"] == hermes_cli.__version__


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
    problems = release_consistency_problems(root) + manifest_problems(root)
    print("\n".join(problems) if problems else "RELEASE_CONSISTENT")
    raise SystemExit(1 if problems else 0)
