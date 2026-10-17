"""Unified, side-effect-free secret redaction for log/error/tool-output boundaries.

This is the single place a byte of text is scrubbed of credential material before
it is persisted or handed to another surface. It deliberately knows nothing about
Hermes config, profiles or the ``security.redact_secrets`` preference: callers
that need a hard egress boundary pass ``force=True`` to their own gate and then
run this pure function. Redaction here is **over**-inclusive by design — a false
positive costs readability, a false negative leaks a credential.

Coverage:

* vendor/structural prefixes — ``sk-`` / ``xai-``, AWS ``AKIA``/``ASIA``,
  GitHub PAT (classic + fine-grained), GitLab, Slack, Google API keys,
  PEM private-key blocks, ``Bearer <token>`` and bare JWTs;
* sensitive URL query parameters (``access_token`` / ``api_key`` / ``token`` /
  ``password`` / ...);
* assignment style (``key = value`` / ``key: value`` with a secret-ish key and a
  value of at least eight characters).

``MATCH_ANY`` is the cheap pre-screen: when no pattern fires the input string is
returned unchanged (same object), so the common case costs one pass of regex
searches and never rebuilds a string.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["REDACTED", "MATCH_ANY", "redact_secrets", "redact_json_string_values"]

REDACTED = "[REDACTED_SECRET]"

# --- structural patterns ---------------------------------------------------

# A full PEM block. ``[A-Z0-9 ]*`` covers ``RSA``/``EC``/``OPENSSH``/``PRIVATE``.
_PEM_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)

# ``Bearer <token>``: keep the scheme for readability, scrub the credential.
_BEARER_RE = re.compile(
    r"(?P<scheme>\bBearer)(?P<ws>\s+)(?P<token>[A-Za-z0-9._~+/=\-]+)",
    re.IGNORECASE,
)

# Bare JWT: the header of every JWT base64url-encodes ``{"...`` as ``eyJ...``.
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")

_VENDOR_RES = (
    re.compile(r"sk-[A-Za-z0-9_-]{10,}"),             # OpenAI-style (also sk- project keys)
    re.compile(r"xai-[A-Za-z0-9]{10,}"),              # xAI / Grok
    re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}"),         # AWS access key id
    re.compile(r"github_pat_[A-Za-z0-9_]{10,}"),      # GitHub PAT (fine-grained)
    re.compile(r"gh[pousr]_[A-Za-z0-9]{10,}"),        # GitHub PAT/OAuth/user/server/refresh
    re.compile(r"glpat-[A-Za-z0-9_-]{10,}"),          # GitLab personal access token
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),      # Slack bot/app/user tokens
    re.compile(r"AIza[A-Za-z0-9_-]{30,}"),            # Google API key
)

# --- URL query parameters --------------------------------------------------

_QUERY_NAMES = (
    "access_token", "refresh_token", "id_token", "api_key", "apikey",
    "client_secret", "password", "passwd", "secret", "jwt", "session",
    "token", "signature", "x-amz-signature", "code", "auth",
)
_QUERY_RE = re.compile(
    r"(?P<lead>[?&])(?P<name>" + "|".join(_QUERY_NAMES) + r")(?P<eq>=)(?P<value>[^&\s#]+)",
    re.IGNORECASE,
)

# --- assignment style ------------------------------------------------------

# Secret-ish key names, longest first so ``access_token`` wins over ``token``.
_ASSIGN_NAMES = (
    "access_token", "refresh_token", "auth_token", "client_secret",
    "private_key", "session_token", "api_key", "apikey", "token",
    "password", "passwd", "secret", "credential",
)
_ASSIGN_RE = re.compile(
    r"(?P<key>(?<![A-Za-z0-9_])[A-Za-z0-9_.\-]*(?:"
    + "|".join(_ASSIGN_NAMES)
    + r")(?![A-Za-z0-9_]))"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<quote>['\"]?)"
    r"(?P<value>[^\s'\"&,;]{8,})"
    r"(?P=quote)",
    re.IGNORECASE,
)

# Ordered rules. Order matters only for overlap: a whole PEM block is consumed
# before the vendor patterns can nibble its body, and ``Bearer`` runs before the
# bare-JWT rule.
_RULES = (
    (_PEM_RE, REDACTED),
    (_BEARER_RE, r"\g<scheme>\g<ws>" + REDACTED),
    *((pattern, REDACTED) for pattern in _VENDOR_RES),
    (_JWT_RE, REDACTED),
    (_QUERY_RE, r"\g<lead>\g<name>\g<eq>" + REDACTED),
    (_ASSIGN_RE, r"\g<key>\g<sep>\g<quote>" + REDACTED + r"\g<quote>"),
)

# Cheap, drift-free pre-screen: the exact patterns the replacement pass uses.
# ``redact_secrets`` stays a pure function with no I/O and no config reads.
MATCH_ANY = tuple(pattern for pattern, _ in _RULES)


def redact_secrets(text: str) -> str:
    """Return ``text`` with every recognized secret replaced by ``REDACTED``.

    Pure: no globals are mutated, no config/environment is read, and a text with
    no match is returned unchanged as the *same* object (the ``MATCH_ANY``
    short circuit). Callers on a hard egress boundary are responsible for
    choosing to always call this regardless of user preference.
    """
    if not isinstance(text, str) or not text:
        return text
    if not any(pattern.search(text) for pattern in MATCH_ANY):
        return text
    redacted = text
    for pattern, replacement in _RULES:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_json_string_values(obj: Any) -> Any:
    """Recursively redact the **string values** of a JSON-like object.

    ``dict`` and ``list`` containers are rebuilt (input is never mutated), keys
    and every non-string leaf are passed through untouched. The counterpart of
    :func:`redact_secrets` for structured tool/error payloads.
    """
    if isinstance(obj, str):
        return redact_secrets(obj)
    if isinstance(obj, dict):
        return {key: redact_json_string_values(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [redact_json_string_values(item) for item in obj]
    return obj
