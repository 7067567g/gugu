"""G03 — the provider registry is a single write seam, and cost/account reads go through one door.

Two contracts are pinned here, both by *runtime behaviour* rather than source-text grep:

1. ``providers._REGISTRY`` refuses *single-key identity writes*. Every process-wide
   registration must go through ``register_provider()`` → ``_store_profile()``; a bare
   ``providers._REGISTRY[name] = profile`` (a second, unmirrored registration path)
   raises ``RuntimeError``. Whole-snapshot maintenance (``clear``/``update``/``pop``/
   ``popitem``) stays open so snapshot restore/isolation keeps working. The registry is
   still a plain ``dict`` for ``get``/``values``/``items``.
2. ``providers.cost.read_call_cost`` / ``read_account_usage`` are the only call sites
   of the ``ProviderProfile.get_usage_cost`` / ``fetch_account_usage`` hooks, and both
   forward faithfully (base no-op → ``None`` without spawning work);
   ``uses_base_account_usage`` is the shared base-hook probe for that short-circuit.
"""

from __future__ import annotations

import pytest

import providers
from providers.base import ProviderProfile


@pytest.fixture
def fresh_registry(monkeypatch):
    """A sealed-but-empty process layer, minus discovery, for registration assertions.

    ``_discover_providers`` is short-circuited (``_discovered = True``) so the profile
    registered by the test is the only row, and the alias/source tables start clean.
    """
    monkeypatch.setattr(providers, "_REGISTRY", providers._SealedRegistry())
    monkeypatch.setattr(providers, "_ALIASES", {})
    monkeypatch.setattr(providers, "_SOURCES", {})
    monkeypatch.setattr(providers, "_PROVIDER_LIST_CACHE", None)
    monkeypatch.setattr(providers, "_discovered", True)
    monkeypatch.setattr(providers, "_discovering", False)
    return providers._REGISTRY


class TestRegistryWriteSeam:
    """Direct single-key writes to ``_REGISTRY`` are rejected at runtime."""

    def test_direct_setitem_is_rejected(self):
        with pytest.raises(RuntimeError):
            providers._REGISTRY["seam-direct"] = ProviderProfile(name="seam-direct")

    def test_direct_delitem_is_rejected(self):
        with pytest.raises(RuntimeError):
            del providers._REGISTRY["seam-direct"]

    def test_direct_setdefault_is_rejected(self):
        with pytest.raises(RuntimeError):
            providers._REGISTRY.setdefault("seam-direct", ProviderProfile(name="seam-direct"))

    def test_reads_still_work(self, fresh_registry):
        """Sealing the identity write must not turn the registry into an unreadable object."""
        assert providers._REGISTRY == {}
        assert providers._REGISTRY.get("missing") is None
        assert list(providers._REGISTRY.items()) == []

    def test_register_provider_lands_through_the_seam(self, fresh_registry):
        profile = ProviderProfile(name="seam-demo", aliases=("seam-alias",))
        providers.register_provider(profile)

        assert providers._REGISTRY["seam-demo"] is profile
        assert providers.get_provider_profile("seam-demo") is profile
        assert providers.get_provider_profile("seam-alias") is profile
        assert providers.provider_source("seam-demo") == "runtime"

    def test_snapshot_maintenance_stays_open(self, fresh_registry):
        """``update``/``pop``/``clear`` maintain a whole snapshot, not a new identity.

        ``hermes_cli/plugin_dev.py`` restores a registry snapshot via ``pop`` + ``update``
        and the discovery tests isolate state via ``clear``/``update``; those must keep
        working without the write token.
        """
        profile = ProviderProfile(name="seam-snapshot")

        providers._REGISTRY.update({"seam-snapshot": profile})
        assert providers._REGISTRY["seam-snapshot"] is profile

        assert providers._REGISTRY.pop("seam-snapshot") is profile
        assert providers._REGISTRY.pop("missing", None) is None

        providers._REGISTRY.update({"seam-snapshot": profile})
        providers._REGISTRY.clear()
        assert providers._REGISTRY == {}


class TestCostReadSeam:
    """``read_call_cost`` is a faithful pass-through with exactly one call."""

    def test_none_profile_reads_as_none(self):
        from providers.cost import read_call_cost

        assert read_call_cost(None, "any-model", object()) is None

    def test_forwards_profile_result_and_calls_once(self):
        from providers.cost import read_call_cost

        sentinel = object()

        class _FakeProfile:
            def __init__(self):
                self.calls: list = []

            def get_usage_cost(self, model, usage):
                self.calls.append((model, usage))
                return sentinel

        fake, usage = _FakeProfile(), object()
        assert read_call_cost(fake, "demo-model", usage) is sentinel
        assert fake.calls == [("demo-model", usage)]


class TestAccountUsageReadSeam:
    """``read_account_usage`` no-ops for the base profile, forwards overrides."""

    def test_none_profile_reads_as_none(self):
        from providers.cost import read_account_usage

        assert read_account_usage(None) is None

    def test_base_profile_no_ops_without_spawning(self):
        from providers.cost import read_account_usage

        profile = ProviderProfile(name="base-noop")
        assert read_account_usage(profile, base_url="http://x", api_key="k") is None

    def test_override_is_forwarded_with_credentials(self):
        from providers.cost import read_account_usage

        snapshot = object()

        class _UsageProfile(ProviderProfile):
            def __init__(self):
                super().__init__(name="usage-override")
                self.calls: list = []

            def fetch_account_usage(self, *, base_url=None, api_key=None):
                self.calls.append((base_url, api_key))
                return snapshot

        profile = _UsageProfile()
        assert read_account_usage(profile, base_url="http://x", api_key="k") is snapshot
        assert profile.calls == [("http://x", "k")]

    def test_uses_base_account_usage_probe(self):
        from providers.cost import uses_base_account_usage

        class _UsageProfile(ProviderProfile):
            def fetch_account_usage(self, *, base_url=None, api_key=None):
                return None

        assert uses_base_account_usage(None) is True
        assert uses_base_account_usage(ProviderProfile(name="base-noop")) is True
        assert uses_base_account_usage(_UsageProfile(name="override")) is False

    def test_base_hook_skips_worker_before_spawn(self, monkeypatch):
        """The agent-side deadline wrapper must not spawn a worker for the base hook."""
        import agent.account_usage as account_usage
        import agent.deadline as deadline

        def _fail(*args, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("run_bounded_sync must not be reached for the base hook")

        monkeypatch.setattr(deadline, "run_bounded_sync", _fail)

        assert account_usage._call_plugin_usage_hook(
            ProviderProfile(name="base-noop"), "http://x", "k") is None
