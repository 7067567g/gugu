"""G03 — fallback-catalog regression pins (no control-flow changes, only a net).

``ProviderProfile.fallback_models`` / ``model_aliases`` default to empty containers,
and ``hermes_cli.models.merge_profile_catalog`` keeps its documented precedence: the
curated fallback is served alone when the live catalog is unavailable, and leads the
merged rows otherwise. These assertions exist so the G03 registry/cost seam work can
never quietly change model selection.
"""

from __future__ import annotations

from providers.base import ProviderProfile


def test_profile_fallback_defaults_are_empty():
    profile = ProviderProfile(name="demo")
    assert profile.fallback_models == ()
    assert profile.model_aliases == {}


def test_merge_profile_catalog_serves_fallback_when_live_is_unavailable():
    from hermes_cli.models import merge_profile_catalog

    profile = ProviderProfile(name="demo", fallback_models=("a", "b"))
    assert merge_profile_catalog("demo", profile, None) == ["a", "b"]


def test_merge_profile_catalog_without_fallback_and_live_is_none():
    from hermes_cli.models import merge_profile_catalog

    assert merge_profile_catalog("demo", ProviderProfile(name="demo"), None) is None


def test_merge_profile_catalog_keeps_curated_rows_first():
    from hermes_cli.models import merge_profile_catalog

    profile = ProviderProfile(name="demo", fallback_models=("a",))
    assert merge_profile_catalog("demo", profile, ["x"]) == ["a", "x"]
