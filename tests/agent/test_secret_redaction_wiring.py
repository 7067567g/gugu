"""Wiring contract for ``agent.secret_redaction`` at two persistence boundaries (T-02).

v0.2.0 shipped the pure redactor but only wired it into ``cron/incidents.py``;
compaction summaries still used the older ``agent.redact`` pass and owned
credential-pool rows passed through unchanged. These tests pin the two missing
call sites:

* ``agent.context_compressor._redact_compaction_text`` must run the unified
  ``redact_secrets`` pass (on top of the legacy strict pass), and
* ``agent.credential_persistence.sanitize_borrowed_credential_payload`` must
  redact non-whitelisted string values of *owned* payloads, while leaving the
  refresh-critical fields untouched, and must fail closed when the redactor
  raises instead of persisting cleartext.

Project modules are imported inside each test body on purpose: before the
wiring exists no module-level import fails at collection time, so the red run
fails on behaviour assertions rather than on a collection error.
"""

from __future__ import annotations

import json

import pytest

SECRET = "sk-abcdefghijklmnop"


def test_compaction_summary_calls_unified_redactor(monkeypatch) -> None:
    from agent import secret_redaction
    from agent.context_compressor import _redact_compaction_text

    calls: list[str] = []
    real_redact_secrets = secret_redaction.redact_secrets

    def _counting_redact_secrets(text):
        calls.append(text)
        return real_redact_secrets(text)

    monkeypatch.setattr(secret_redaction, "redact_secrets", _counting_redact_secrets)

    out = _redact_compaction_text(f"key {SECRET}")

    assert calls, "compaction summary boundary must call agent.secret_redaction.redact_secrets"
    assert SECRET not in out


def test_owned_payload_non_whitelisted_secret_is_redacted() -> None:
    from agent.credential_persistence import sanitize_borrowed_credential_payload

    payload = {
        "source": "manual",
        "headers": {"Authorization": f"Bearer {SECRET}"},
        "label": "primary",
    }

    out = sanitize_borrowed_credential_payload(payload, "openai")

    assert isinstance(out, dict)
    assert "headers" in out, "the metadata/headers structure must survive redaction"
    assert SECRET not in json.dumps(out)
    assert out["label"] == "primary"


def test_redactor_failure_denies_write(monkeypatch) -> None:
    from agent import credential_persistence

    # Before the wiring lands the class does not exist yet; the fallback keeps
    # the test focused on "no exception means the cleartext was returned".
    redaction_error = getattr(
        credential_persistence, "CredentialRedactionError", RuntimeError
    )

    def _exploding_redactor(_value):
        raise RuntimeError("redactor unavailable (test double)")

    monkeypatch.setattr(
        credential_persistence, "redact_json_string_values", _exploding_redactor,
        raising=False,
    )

    payload = {
        "source": "manual",
        "headers": {"Authorization": f"Bearer {SECRET}"},
    }

    with pytest.raises(redaction_error) as excinfo:
        credential_persistence.sanitize_borrowed_credential_payload(payload, "openai")

    # Fail-closed errors must not smuggle the cleartext into the exception text.
    assert SECRET not in str(excinfo.value)


def test_owned_payload_whitelist_keeps_refresh_fields() -> None:
    from agent.credential_persistence import sanitize_borrowed_credential_payload

    payload = {
        "source": "manual",
        "agent_key": f"agent-key-{SECRET}",
        "access_token": f"access-{SECRET}",
        "refresh_token": f"refresh-{SECRET}",
        "api_key": f"api-{SECRET}",
        "token": f"token-{SECRET}",
        "secret": f"secret-{SECRET}",
        "secret_fingerprint": "sha256:0123456789abcdef",
        "label": "primary",
    }

    out = sanitize_borrowed_credential_payload(payload, "openai")

    # Refresh-critical fields must be byte-identical: redacting them would
    # silently break credential refresh, the exact regression guarded here.
    assert out == payload
