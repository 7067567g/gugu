"""Version single-source reader + CLI product-identity display (stdlib only, no network).

Covers WS-2 / T-02 (G-02): ``hermes_cli.version_source`` reads the gugu product version
from the root ``GUGU_VERSION`` file and the upstream kernel version from ``pyproject.toml``;
the CLI banner / fast ``--version`` path / ``build_info`` surface both, and degrade
gracefully when the product version is unreadable.

The product version is read from the repo **dynamically** (never hard-coded) so this file
stays green across release bumps.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import hermes_cli
from hermes_cli import build_info, version_source

_ROOT = Path(__file__).resolve().parents[2]
_SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")

_GROUPS = {"product", "upstream_kernel", "upstream_desktop", "workspace_unclassified"}


def _declared_product_version() -> str:
    """The version this checkout actually declares (dynamic — never hard-coded)."""
    return (_ROOT / "GUGU_VERSION").read_text(encoding="utf-8").strip()


def _pyproject_version() -> str:
    import tomllib

    with open(_ROOT / "pyproject.toml", "rb") as fh:
        return str(tomllib.load(fh)["project"]["version"])


# --------------------------------------------------------------------------- reader layer


def test_read_product_version_matches_repo_file() -> None:
    assert version_source.read_product_version(_ROOT) == _declared_product_version()


def test_read_product_version_from_custom_root(tmp_path: Path) -> None:
    (tmp_path / "GUGU_VERSION").write_text("7.8.9\n", encoding="utf-8")
    assert version_source.read_product_version(tmp_path) == "7.8.9"


def test_read_product_version_rejects_non_semver(tmp_path: Path) -> None:
    (tmp_path / "GUGU_VERSION").write_text("0.4\n", encoding="utf-8")
    assert version_source.read_product_version(tmp_path) is None


def test_read_product_version_missing_file_is_none(tmp_path: Path) -> None:
    assert version_source.read_product_version(tmp_path) is None


def test_read_upstream_version_matches_pyproject() -> None:
    assert version_source.read_upstream_version(_ROOT) == _pyproject_version()


def test_read_upstream_version_from_custom_root(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "1.2.3"\n', encoding="utf-8")
    assert version_source.read_upstream_version(tmp_path) == "1.2.3"


def test_read_upstream_version_missing_or_broken_is_none(tmp_path: Path) -> None:
    assert version_source.read_upstream_version(tmp_path) is None
    (tmp_path / "pyproject.toml").write_text("[project\nversion =", encoding="utf-8")
    assert version_source.read_upstream_version(tmp_path) is None


def test_version_summary_carries_both_lines() -> None:
    summary = version_source.version_summary(_ROOT)
    assert summary["product"] == _declared_product_version()
    assert summary["upstream_kernel"] == _pyproject_version()
    assert summary["product_file"] == "GUGU_VERSION"
    assert summary["upstream_file"] == "pyproject.toml"


def test_version_summary_degrades_to_none_per_field(tmp_path: Path) -> None:
    summary = version_source.version_summary(tmp_path)
    assert summary["product"] is None and summary["upstream_kernel"] is None


def test_version_source_stays_stdlib_only() -> None:
    src = Path(version_source.__file__).read_text(encoding="utf-8")
    assert "from hermes_cli" not in src and "import hermes_cli" not in src
    for heavy in ("import rich", "import prompt_toolkit", "import requests", "import httpx"):
        assert heavy not in src


# --------------------------------------------------------------------------- banner label


def _banner_label() -> str:
    from hermes_cli.banner import format_banner_version_label

    return format_banner_version_label()


def test_banner_label_leads_with_product_and_keeps_upstream() -> None:
    label = _banner_label()
    product = _declared_product_version()
    assert label.startswith(f"gugu v{product} · Hermes Agent v{hermes_cli.__version__}")
    assert f"Hermes Agent v{hermes_cli.__version__}" in label


def test_banner_label_degrades_when_product_version_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(version_source, "read_product_version", lambda *a, **k: None)
    label = _banner_label()
    assert "gugu v" not in label
    assert f"Hermes Agent v{hermes_cli.__version__}" in label


def test_banner_label_never_raises_when_reader_explodes(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a, **k):
        raise RuntimeError("reader down")

    monkeypatch.setattr(version_source, "read_product_version", _boom)
    label = _banner_label()
    assert f"Hermes Agent v{hermes_cli.__version__}" in label


# --------------------------------------------------------------------------- fast --version


def test_fast_version_fallback_prefers_product(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    import hermes_cli.banner as banner
    from hermes_cli import _startup_fast

    def _boom():
        raise RuntimeError("banner unavailable")

    monkeypatch.setattr(banner, "format_banner_version_label", _boom)
    _startup_fast.print_fast_version_info(check_updates=False)
    out = capsys.readouterr().out
    product = _declared_product_version()
    assert f"gugu v{product} · Hermes Agent v{hermes_cli.__version__}" in out


def test_fast_version_fallback_without_product(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    import hermes_cli.banner as banner
    from hermes_cli import _startup_fast

    def _boom():
        raise RuntimeError("banner unavailable")

    monkeypatch.setattr(banner, "format_banner_version_label", _boom)
    monkeypatch.setattr(version_source, "read_product_version", lambda *a, **k: None)
    _startup_fast.print_fast_version_info(check_updates=False)
    out = capsys.readouterr().out
    assert "gugu v" not in out
    assert f"Hermes Agent v{hermes_cli.__version__}" in out


# --------------------------------------------------------------------------- build_info


def test_code_identity_exposes_product_version_alongside_upstream() -> None:
    identity = build_info.get_code_identity(refresh=True)
    assert identity["product_version"] == _declared_product_version()
    # ``version`` keeps its existing upstream-kernel meaning (0.21.5), unchanged by T-02.
    assert identity["version"] == hermes_cli.__version__


def test_code_identity_product_version_degrades_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a, **k):
        raise RuntimeError("reader down")

    monkeypatch.setattr(version_source, "read_product_version", _boom)
    identity = build_info.get_code_identity(refresh=True)
    assert identity["product_version"] is None
    assert identity["version"] == hermes_cli.__version__


def test_build_info_single_sources_upstream_version() -> None:
    """X1: ``build_info`` reuses ``version_source`` — it must not re-parse ``pyproject.toml``.

    Mechanical guard for the refactor batch: (a) the module source must contain no ``tomllib``
    (the reader layer is the single parser), and (b) the surfaced ``version`` must equal what the
    single-source reader returns for this checkout (upstream kernel line).
    """
    src = Path(build_info.__file__).read_text(encoding="utf-8")
    assert "tomllib" not in src
    assert build_info.get_code_identity(refresh=True)["version"] == version_source.read_upstream_version(_ROOT)


def test_manifest_groups_are_declared_once(tmp_path: Path) -> None:
    """The reader is the only place the product version is defined for Python callers."""
    assert _GROUPS == set(version_source.PRODUCT_VERSION_GROUPS)
    assert _SEMVER.fullmatch(_declared_product_version())


# --------------------------------------------------------------------------- [project] single parser (X2)

#: Modules that used to re-parse ``pyproject.toml`` ``[project]`` themselves (X2 target).
_PROJECT_READER_MODULES = {
    "update_cmd_maint": _ROOT / "hermes_cli" / "update_cmd_maint.py",
    "main_install_repair": _ROOT / "hermes_cli" / "main_install_repair.py",
    "_early_recovery": _ROOT / "hermes_cli" / "_early_recovery.py",
}


def test_read_project_table_matches_tomllib() -> None:
    import tomllib

    with open(_ROOT / "pyproject.toml", "rb") as fh:
        expected = tomllib.load(fh)["project"]
    assert version_source.read_project_table(_ROOT) == expected


def test_read_project_table_from_custom_root(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    assert version_source.read_project_table(tmp_path) == {"name": "x", "version": "1.2.3"}


def test_read_project_table_missing_or_broken_is_none(tmp_path: Path) -> None:
    assert version_source.read_project_table(tmp_path) is None
    (tmp_path / "pyproject.toml").write_text("[project\nversion =", encoding="utf-8")
    assert version_source.read_project_table(tmp_path) is None


def test_read_project_table_non_table_project_is_none(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('project = "nope"\n', encoding="utf-8")
    assert version_source.read_project_table(tmp_path) is None


def test_read_upstream_version_reuses_read_project_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(version_source, "read_project_table", lambda *a, **k: {"version": "9.9.9"})
    assert version_source.read_upstream_version(_ROOT) == "9.9.9"
    monkeypatch.setattr(version_source, "read_project_table", lambda *a, **k: None)
    assert version_source.read_upstream_version(_ROOT) is None


def test_project_table_has_a_single_parser() -> None:
    """X2: the three former re-parsers route through ``version_source`` (no ``tomllib``)."""
    assert hasattr(version_source, "read_project_table")
    for name, path in _PROJECT_READER_MODULES.items():
        src = path.read_text(encoding="utf-8")
        assert "tomllib" not in src, f"{name} still parses pyproject.toml itself"
