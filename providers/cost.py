"""Single read seam for the provider-owned cost / account-usage hooks.

``ProviderProfile.get_usage_cost`` and ``ProviderProfile.fetch_account_usage`` are
optional hooks a profile may override. Before this module, every consumer decided on
its own whether to call them (and how to treat the base no-op), so "who reads a
profile's cost" and "who spawns a plugin usage hook" were spread across ``agent/``.

These two functions are the only call sites:

* :func:`read_call_cost` — per-call cost reported by the profile, ``None`` when there
  is no profile or no override (callers then fall through to route-based pricing).
* :func:`read_account_usage` — account-usage snapshot for the ``/usage`` surface,
  ``None`` for the base no-op (no worker thread is spawned in that case).

Both are deliberately inert: no caching, no exceptions swallowed, no policy. The
caller keeps its existing failure/open semantics.

:func:`uses_base_account_usage` exposes the base-hook test used by both
:func:`read_account_usage` and the agent-side deadline wrapper.
"""

from __future__ import annotations

from typing import Any

from providers.base import ProviderProfile


def read_call_cost(profile: ProviderProfile | None, model: str, usage: Any) -> Any | None:
    """Return the profile's reported cost for one call, or ``None``.

    This is the only call site of ``ProviderProfile.get_usage_cost``: a missing
    profile and a profile that does not override the hook both read as ``None``, so
    callers have a single "no provider-reported cost" signal.
    """
    if profile is None:
        return None
    return profile.get_usage_cost(model, usage)


def uses_base_account_usage(profile: ProviderProfile | None) -> bool:
    """Whether *profile* keeps the base (no-op) ``fetch_account_usage`` hook.

    ``None`` counts as base — there is nothing to call. Shared with the agent-side
    deadline wrapper so it can skip spawning a bounded worker for profiles that never
    override the hook, without ``agent/`` importing ``fetch_account_usage`` itself.
    """
    if profile is None:
        return True
    return type(profile).fetch_account_usage is ProviderProfile.fetch_account_usage


def read_account_usage(
    profile: ProviderProfile | None,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
) -> Any | None:
    """Return the profile's account-usage snapshot, or ``None``.

    The only call site of ``ProviderProfile.fetch_account_usage``. The base hook is a
    documented no-op, so it is short-circuited here: a profile that does not implement
    it must not pay for a bounded worker thread on every ``/usage`` render. An
    overridden hook receives the caller's credentials unchanged.
    """
    if uses_base_account_usage(profile):
        return None
    assert profile is not None  # narrowing for type checkers; base/None returned above
    return profile.fetch_account_usage(base_url=base_url, api_key=api_key)
