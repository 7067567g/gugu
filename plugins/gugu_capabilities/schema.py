"""Tiny JSON-Schema subset used by the M0 capability registry.

Why hand-rolled: the capability contract must be validated at runtime (agent
arguments + registry metadata) without adding a Python dependency. The subset
covers exactly what registry entries use today; unsupported keywords are
ignored on purpose so a future richer schema does not silently fail open.

Supported keywords: type, enum, properties, required, additionalProperties
(false only), items, minItems, minLength, maxLength, pattern, minimum, maximum.
"""

from __future__ import annotations

import re
from typing import Any, Mapping


def validate(instance: Any, schema: Mapping[str, Any], *, path: str = "$") -> list[str]:
    """Return human-readable validation errors (empty list == valid)."""
    errors: list[str] = []
    if not isinstance(schema, Mapping):
        return [f"{path}: schema is not an object"]

    expected = schema.get("type")
    if expected is not None and not _type_ok(instance, expected):
        return [f"{path}: expected {expected}, got {_json_type(instance)}"]

    enum = schema.get("enum")
    if enum is not None and instance not in enum:
        errors.append(f"{path}: {instance!r} is not one of {list(enum)!r}")

    if isinstance(instance, str):
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(instance) < min_length:
            errors.append(f"{path}: string shorter than {min_length}")
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and len(instance) > max_length:
            errors.append(f"{path}: string longer than {max_length}")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, instance) is None:
            errors.append(f"{path}: string does not match {pattern!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and instance < minimum:
            errors.append(f"{path}: {instance} < minimum {minimum}")
        if isinstance(maximum, (int, float)) and instance > maximum:
            errors.append(f"{path}: {instance} > maximum {maximum}")

    if isinstance(instance, Mapping):
        properties = schema.get("properties") or {}
        if not isinstance(properties, Mapping):
            return errors + [f"{path}: properties must be an object"]
        for name in schema.get("required") or ():
            if name not in instance:
                errors.append(f"{path}: missing required property {name!r}")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(instance) - set(properties))
            if unknown:
                errors.append(f"{path}: unexpected properties {unknown!r}")
        for name, child_schema in properties.items():
            if name in instance:
                errors.extend(validate(instance[name], child_schema, path=f"{path}.{name}"))

    if isinstance(instance, list):
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(instance) < min_items:
            errors.append(f"{path}: array shorter than {min_items}")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(instance):
                errors.extend(validate(item, item_schema, path=f"{path}[{index}]"))

    return errors


def _type_ok(instance: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return any(_type_ok(instance, item) for item in expected)
    if expected == "object":
        return isinstance(instance, Mapping)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    return True


def _json_type(instance: Any) -> str:
    if instance is None:
        return "null"
    if isinstance(instance, bool):
        return "boolean"
    if isinstance(instance, str):
        return "string"
    if isinstance(instance, int):
        return "integer"
    if isinstance(instance, float):
        return "number"
    if isinstance(instance, list):
        return "array"
    if isinstance(instance, Mapping):
        return "object"
    return type(instance).__name__
