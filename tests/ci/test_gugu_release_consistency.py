"""Mechanical release-line consistency checks for gugu (stdlib only, no network)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

_SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


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


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
    problems = release_consistency_problems(root)
    print("\n".join(problems) if problems else "RELEASE_CONSISTENT")
    raise SystemExit(1 if problems else 0)
