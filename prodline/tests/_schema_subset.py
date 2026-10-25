"""Minimal JSON Schema 2020-12 subset validator (test support only).

Why this exists
---------------
The P1 contract forbids new Python dependencies, and the project venv does not
ship ``jsonschema``.  The six receipt schemas therefore deliberately stay inside
a small, explicit keyword subset, and this module validates both the schemas'
shape and sample receipts against that subset.

Supported keywords
------------------
``$schema``, ``$id``, ``$ref`` (local ``#/$defs/...`` only), ``$defs``,
``title``, ``description``, ``type``, ``required``, ``properties``,
``additionalProperties``, ``enum``, ``const``, ``pattern``, ``minLength``,
``maxLength``, ``minimum``, ``maximum``, ``items``, ``minItems``, ``maxItems``,
``uniqueItems``, ``oneOf``, ``anyOf``, ``allOf``, ``format`` (``date-time``
only), plus the annotations ``default`` and ``examples``.

Anything outside this set is rejected by :func:`assert_supported_schema` so a
future edit cannot quietly add a constraint this validator would ignore.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

SUPPORTED_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$ref",
        "$defs",
        "title",
        "description",
        "type",
        "required",
        "properties",
        "additionalProperties",
        "enum",
        "const",
        "pattern",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "items",
        "minItems",
        "maxItems",
        "uniqueItems",
        "oneOf",
        "anyOf",
        "allOf",
        "format",
        "default",
        "examples",
    }
)

_SCHEMA_MAP_KEYWORDS = frozenset({"properties", "$defs"})
_SCHEMA_SINGLE_KEYWORDS = frozenset({"items", "additionalProperties"})
_SCHEMA_LIST_KEYWORDS = frozenset({"allOf", "anyOf", "oneOf"})


class UnsupportedKeyword(ValueError):
    """The schema uses a keyword this subset validator does not implement."""


class UnresolvableRef(ValueError):
    """The schema uses an external or malformed ``$ref``."""


def load_schema(path: str | Path) -> dict[str, Any]:
    """Load a JSON Schema file, failing loudly on invalid JSON."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def resolve_ref(ref: str, root: dict[str, Any]) -> dict[str, Any]:
    """Resolve a local JSON pointer such as ``#/$defs/artifact``."""
    if not ref.startswith("#/"):
        raise UnresolvableRef(f"only local '#/...' refs are supported, got {ref!r}")
    node: Any = root
    for raw in ref[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or token not in node:
            raise UnresolvableRef(f"cannot resolve {ref!r} at {token!r}")
        node = node[token]
    if not isinstance(node, dict):
        raise UnresolvableRef(f"ref {ref!r} does not point at a schema object")
    return node


def _is_type(instance: Any, name: str) -> bool:
    if name == "object":
        return isinstance(instance, dict)
    if name == "array":
        return isinstance(instance, list)
    if name == "string":
        return isinstance(instance, str)
    if name == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if name == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if name == "boolean":
        return isinstance(instance, bool)
    if name == "null":
        return instance is None
    raise UnsupportedKeyword(f"unsupported JSON type {name!r}")


def _check_datetime(value: str) -> str | None:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return "is not an ISO 8601 date-time"
    if parsed.tzinfo is None:
        return "date-time must include a timezone (e.g. trailing 'Z')"
    return None


def validate(
    instance: Any,
    schema: dict[str, Any],
    *,
    path: str = "$",
    root: dict[str, Any] | None = None,
) -> list[str]:
    """Return a list of human-readable validation errors (empty == valid)."""
    if not isinstance(schema, dict):
        return [f"{path}: schema node is not an object"]
    root = schema if root is None else root
    errors: list[str] = []

    if "$ref" in schema:
        errors.extend(validate(instance, resolve_ref(schema["$ref"], root), path=path, root=root))

    if "type" in schema:
        declared = schema["type"]
        names = declared if isinstance(declared, list) else [declared]
        if not any(_is_type(instance, name) for name in names):
            errors.append(f"{path}: expected type {names}, got {type(instance).__name__}")

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {instance!r}")

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: expected one of {schema['enum']!r}, got {instance!r}")

    if isinstance(instance, dict):
        properties = schema.get("properties") or {}
        for key, subschema in properties.items():
            if key in instance:
                errors.extend(validate(instance[key], subschema, path=f"{path}.{key}", root=root))
        for key in schema.get("required") or []:
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            if key in properties:
                continue
            if additional is False:
                errors.append(f"{path}: unexpected property {key!r}")
            elif isinstance(additional, dict):
                errors.extend(validate(value, additional, path=f"{path}.{key}", root=root))

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(f"{path}: {instance!r} does not match pattern {schema['pattern']!r}")
        if schema.get("format") == "date-time":
            problem = _check_datetime(instance)
            if problem:
                errors.append(f"{path}: {instance!r} {problem}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} is below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} is above maximum {schema['maximum']}")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems {schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: more than maxItems {schema['maxItems']}")
        if schema.get("uniqueItems"):
            seen: set[str] = set()
            for index, item in enumerate(instance):
                marker = json.dumps(item, sort_keys=True, ensure_ascii=False)
                if marker in seen:
                    errors.append(f"{path}[{index}]: duplicate item")
                seen.add(marker)
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(instance):
                errors.extend(
                    validate(item, schema["items"], path=f"{path}[{index}]", root=root)
                )

    for keyword in ("allOf", "anyOf", "oneOf"):
        subschemas = schema.get(keyword)
        if not subschemas:
            continue
        results = [validate(instance, sub, path=path, root=root) for sub in subschemas]
        valid = sum(1 for result in results if not result)
        if keyword == "allOf":
            for result in results:
                errors.extend(result)
        elif keyword == "anyOf" and valid == 0:
            errors.append(f"{path}: does not match any of the anyOf branches")
        elif keyword == "oneOf" and valid != 1:
            errors.append(f"{path}: matches {valid} oneOf branches (exactly one required)")

    return errors


def assert_supported_schema(schema: dict[str, Any], path: str = "$") -> None:
    """Walk schema positions and reject keywords outside the supported subset."""
    if not isinstance(schema, dict):
        raise UnsupportedKeyword(f"{path}: schema node is not an object")
    for keyword in schema:
        if keyword not in SUPPORTED_KEYWORDS:
            raise UnsupportedKeyword(f"{path}: unsupported keyword {keyword!r}")
    for keyword, value in schema.items():
        if keyword in _SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
            for name, subschema in value.items():
                assert_supported_schema(subschema, f"{path}.{keyword}.{name}")
        elif keyword in _SCHEMA_SINGLE_KEYWORDS and isinstance(value, dict):
            assert_supported_schema(value, f"{path}.{keyword}")
        elif keyword in _SCHEMA_LIST_KEYWORDS and isinstance(value, list):
            for index, subschema in enumerate(value):
                assert_supported_schema(subschema, f"{path}.{keyword}[{index}]")
