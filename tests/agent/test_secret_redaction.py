"""Contract tests for ``agent.secret_redaction`` (T-03).

The module under test is imported *inside* each test body on purpose: before the
implementation exists the collection step must still succeed, so the red run
fails on missing-module assertions at test runtime rather than a collection
error. Covers vendor prefixes, PEM/Bearer/bare-JWT structure, sensitive URL query
parameters, assignment style, the no-match short circuit and the recursive JSON
walk.
"""

from __future__ import annotations

import pytest

REDACTED = "[REDACTED_SECRET]"

SK = "sk-" + "a" * 32
XAI = "xai-" + "b" * 32
AWS = "AKIA" + "A1B2C3D4E5F6G7H8"
GH_CLASSIC = "ghp_" + "C" * 36
GH_FINE = "github_pat_" + "D" * 40
GITLAB = "glpat-" + "E" * 20
SLACK = "xoxb-" + "123456789012-123456789012-abcdefghijklmnop"
GOOGLE = "AIza" + "F" * 35
JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)
PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEA0123456789abcdefghijklmnopqrstuvwxyzABCD\n"
    "-----END RSA PRIVATE KEY-----"
)

VENDOR_SAMPLES = [
    ("openai-sk", SK),
    ("xai", XAI),
    ("aws", AWS),
    ("github-classic", GH_CLASSIC),
    ("github-fine-grained", GH_FINE),
    ("gitlab", GITLAB),
    ("slack", SLACK),
    ("google", GOOGLE),
]


def redact(text: str) -> str:
    from agent.secret_redaction import redact_secrets

    return redact_secrets(text)


@pytest.mark.parametrize("label,secret", VENDOR_SAMPLES)
def test_vendor_prefixed_secret_is_replaced(label: str, secret: str) -> None:
    out = redact(f"prefix context before {secret} trailing context after")
    assert secret not in out, f"{label} leaked: {out!r}"
    assert REDACTED in out


def test_private_key_block_is_replaced() -> None:
    out = redact(f"dump follows:\n{PEM}\nend of dump")
    assert PEM not in out
    assert "MIIEowIBAAKCAQEA" not in out
    assert REDACTED in out


def test_bearer_token_is_replaced() -> None:
    out = redact(f"Authorization: Bearer {JWT}")
    assert JWT not in out
    assert "Bearer" in out
    assert REDACTED in out


def test_bare_jwt_is_replaced() -> None:
    out = redact(f"the session credential is {JWT} for this run")
    assert JWT not in out
    assert REDACTED in out


def test_sensitive_query_parameters_are_replaced() -> None:
    url = (
        "https://api.example.com/v1/items?"
        "access_token=abc123DEF456&page=2&api_key=zzz999&password=hunter2x"
    )
    out = redact(url)
    assert "abc123DEF456" not in out
    assert "zzz999" not in out
    assert "hunter2x" not in out
    assert "access_token=[REDACTED_SECRET]" in out
    assert "api_key=[REDACTED_SECRET]" in out
    assert "password=[REDACTED_SECRET]" in out
    assert "page=2" in out
    assert out.startswith("https://api.example.com/v1/items?")


def test_assignment_style_is_replaced() -> None:
    out = redact('config password="hunter2hunter2" and token: abcdefghijkl done')
    assert "hunter2hunter2" not in out
    assert "abcdefghijkl" not in out
    assert out.count(REDACTED) == 2


def test_no_secret_text_is_returned_unchanged_short_circuit() -> None:
    text = "ordinary prose with no credentials at all #1234"
    assert redact(text) is text


def test_empty_string_is_returned_unchanged() -> None:
    assert redact("") == ""


def test_json_nested_string_values_are_redacted() -> None:
    from agent.secret_redaction import redact_json_string_values

    payload = {
        "outer": {"inner": SK, "nested": [{"deep": GH_CLASSIC}]},
        "list": [GH_FINE, 42, None, True, "plain text"],
        "count": 7,
    }
    out = redact_json_string_values(payload)

    assert out["outer"]["inner"] == REDACTED
    assert out["outer"]["nested"][0]["deep"] == REDACTED
    assert out["list"][0] == REDACTED
    assert out["list"][1] == 42
    assert out["list"][2] is None
    assert out["list"][3] is True
    assert out["list"][4] == "plain text"
    assert out["count"] == 7
    # The input must not be mutated in place.
    assert payload["outer"]["inner"] == SK


def test_json_non_string_leaves_pass_through_identity() -> None:
    from agent.secret_redaction import redact_json_string_values

    assert redact_json_string_values(3) == 3
    assert redact_json_string_values(None) is None
    assert redact_json_string_values(True) is True
    assert redact_json_string_values(["clean"]) == ["clean"]
