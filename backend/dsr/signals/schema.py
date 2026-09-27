"""A small JSON-Schema subset, for checking a signal against its registration.

"Signals must follow the structure defined on the Signal Registration", and the
registration defines that structure as ``data_shape`` and ``metadata_shape``, both
JSON Schema. Nothing in the research says *which* draft of JSON Schema, or how
much of it the vendor honours, so this module implements the keywords a signal
contract actually needs and is explicit about the boundary.

Supported
    ``type`` (name or list), ``properties``, ``required``,
    ``additionalProperties`` (boolean), ``enum``, ``const``, ``minimum``,
    ``maximum``, ``exclusiveMinimum``, ``exclusiveMaximum``, ``multipleOf``,
    ``minLength``, ``maxLength``, ``pattern``, ``items``, ``minItems``,
    ``maxItems``, ``uniqueItems``, and ``format`` as a light check for
    ``date``, ``date-time`` and ``email``.

Deliberately not supported
    ``$ref``, ``allOf``/``anyOf``/``oneOf``/``not``, ``if``/``then``/``else``,
    ``patternProperties``, ``dependentSchemas``, ``$defs``, numeric and
    cross-field keywords, and a non-boolean ``additionalProperties`` schema.

Unknown keywords are ignored, which is what JSON Schema itself requires of an
annotation and is also the safe default for a partner-authored shape: a keyword
this build has never heard of must not be able to reject a good signal.

There is no dependency on a JSON-Schema library because the product's declared
dependencies are FastAPI and uvicorn, and a shape contract small enough to read
in one sitting is one a reviewer can check. :data:`SUPPORTED_KEYWORDS` is served
at ``GET /api/wf-027/vocabulary`` so the boundary is visible rather than implied.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Mapping, Sequence

from dsr.signals.errors import SignalError

#: Every keyword this validator looks at. Anything else in a shape is treated as
#: an annotation and ignored.
SUPPORTED_KEYWORDS: frozenset[str] = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "enum",
        "const",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "items",
        "minItems",
        "maxItems",
        "uniqueItems",
        "format",
        # annotations a partner is entitled to write and this build carries
        # through without acting on
        "title",
        "description",
        "examples",
        "$schema",
        "$id",
        "default",
    }
)

#: The JSON type names, and how a Python value maps onto one.
JSON_TYPES: tuple[str, ...] = ("object", "array", "string", "number", "integer", "boolean", "null")

#: Formats checked here. Any other value is an annotation, per JSON Schema's own
#: default, and is ignored.
CHECKED_FORMATS: frozenset[str] = frozenset({"date", "date-time", "email"})


def json_type(value: Any) -> str:
    """The JSON type name of a Python value.

    ``bool`` is checked before ``int`` on purpose: in Python ``True`` is an
    ``int``, and a shape asking for an integer must not accept ``true``.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return "unknown"


def _matches_type(value: Any, wanted: str) -> bool:
    actual = json_type(value)
    if wanted == "number":
        # Every integer is a valid JSON number, and an integer shape accepts a
        # number that happens to be whole - the reverse is not true.
        return actual in ("number", "integer")
    if wanted == "integer":
        return actual == "integer" or (actual == "number" and float(value).is_integer())
    return actual == wanted


def require_object_shape(value: Any, what: str) -> dict[str, Any]:
    """Check that a declared shape is a JSON Schema object describing an object.

    ``data_shape`` and ``metadata_shape`` both describe a JSON object, so a shape
    that declares no ``type`` is fine - absence of a type means "any type" - but
    one that declares something else is a registration mistake worth naming now
    rather than at the first signal that fails to match.
    """
    if not isinstance(value, Mapping) or not value:
        raise SignalError(f"{what} must be a non-empty JSON Schema object")
    declared = value.get("type")
    if declared is None:
        return dict(value)
    names = [declared] if isinstance(declared, str) else list(declared) if isinstance(declared, Sequence) else []
    if not names or any(not isinstance(name, str) for name in names):
        raise SignalError(f"{what}.type must be a type name or a list of type names")
    unknown = [name for name in names if name not in JSON_TYPES]
    if unknown:
        raise SignalError(
            f"{what}.type names {', '.join(unknown)}; a JSON Schema type is one of "
            f"{', '.join(JSON_TYPES)}"
        )
    if "object" not in names:
        raise SignalError(
            f"{what}.type must be \"object\" because a {what.replace('_shape', '')} "
            f"carries JSON object fields; got {declared!r}"
        )
    properties = value.get("properties")
    if properties is not None and not isinstance(properties, Mapping):
        raise SignalError(f"{what}.properties must be an object of field name to schema")
    return dict(value)


def _check_format(value: str, fmt: str) -> str | None:
    if fmt == "date":
        try:
            date.fromisoformat(value)
        except ValueError:
            return "is not an ISO 8601 date (YYYY-MM-DD)"
        return None
    if fmt == "date-time":
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return "is not an ISO 8601 date-time"
        return None
    if fmt == "email":
        local, _, domain = value.partition("@")
        return None if local and domain and "." in domain else "is not an email address"
    return None


def validate(shape: Mapping[str, Any], value: Any, path: str = "$") -> list[dict[str, str]]:
    """Validate ``value`` against ``shape``.

    Returns a list of findings, empty when the value conforms. A list rather than
    the first failure, so a sender fixing a signal sees every problem in one
    response instead of one per attempt.
    """
    findings: list[dict[str, str]] = []
    if not isinstance(shape, Mapping):
        return [{"path": path, "keyword": "schema", "message": "the shape is not a JSON Schema object"}]

    def fail(at: str, keyword: str, message: str) -> None:
        findings.append({"path": at, "keyword": keyword, "message": message})

    declared = shape.get("type")
    if declared is not None:
        names = [declared] if isinstance(declared, str) else list(declared)
        if not any(_matches_type(value, name) for name in names if isinstance(name, str)):
            wanted = ", ".join(str(name) for name in names)
            fail(
                path,
                "type",
                f"expected {wanted}, got {json_type(value)}",
            )
            # Once the type is wrong, every other keyword would describe a value
            # that is not the one here. Stopping here keeps the report honest.
            return findings

    if "const" in shape and value != shape["const"]:
        fail(path, "const", f"must be {shape['const']!r}")

    if "enum" in shape:
        allowed = shape["enum"]
        if isinstance(allowed, Sequence) and not isinstance(allowed, str) and value not in allowed:
            options = ", ".join(repr(option) for option in allowed)
            fail(path, "enum", f"must be one of {options}")

    if isinstance(value, str):
        _validate_string(shape, value, path, fail)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        _validate_number(shape, value, path, fail)
    if isinstance(value, Mapping):
        _validate_object(shape, value, path, fail)
    if isinstance(value, (list, tuple)):
        _validate_array(shape, value, path, fail)

    return findings


def _validate_string(shape: Mapping[str, Any], value: str, path: str, fail: Any) -> None:
    if isinstance(shape.get("minLength"), int) and len(value) < shape["minLength"]:
        fail(path, "minLength", f"must be at least {shape['minLength']} characters")
    if isinstance(shape.get("maxLength"), int) and len(value) > shape["maxLength"]:
        fail(path, "maxLength", f"must be at most {shape['maxLength']} characters")
    pattern = shape.get("pattern")
    if isinstance(pattern, str):
        try:
            matched = re.search(pattern, value) is not None
        except re.error as exc:  # a broken pattern is the registration's fault
            fail(path, "pattern", f"the declared pattern is not a valid regular expression: {exc}")
            return
        if not matched:
            fail(path, "pattern", f"does not match {pattern!r}")
    fmt = shape.get("format")
    if isinstance(fmt, str) and fmt in CHECKED_FORMATS:
        problem = _check_format(value, fmt)
        if problem:
            fail(path, "format", f"declared as {fmt} but {problem}")


def _validate_number(shape: Mapping[str, Any], value: float, path: str, fail: Any) -> None:
    minimum = shape.get("minimum")
    if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and value < minimum:
        fail(path, "minimum", f"must be at least {minimum}")
    maximum = shape.get("maximum")
    if isinstance(maximum, (int, float)) and not isinstance(maximum, bool) and value > maximum:
        fail(path, "maximum", f"must be at most {maximum}")
    exclusive_min = shape.get("exclusiveMinimum")
    if (
        isinstance(exclusive_min, (int, float))
        and not isinstance(exclusive_min, bool)
        and value <= exclusive_min
    ):
        fail(path, "exclusiveMinimum", f"must be greater than {exclusive_min}")
    exclusive_max = shape.get("exclusiveMaximum")
    if (
        isinstance(exclusive_max, (int, float))
        and not isinstance(exclusive_max, bool)
        and value >= exclusive_max
    ):
        fail(path, "exclusiveMaximum", f"must be less than {exclusive_max}")
    multiple = shape.get("multipleOf")
    if isinstance(multiple, (int, float)) and not isinstance(multiple, bool) and multiple > 0:
        quotient = value / multiple
        if abs(quotient - round(quotient)) > 1e-9:
            fail(path, "multipleOf", f"must be a multiple of {multiple}")


def _validate_object(
    shape: Mapping[str, Any], value: Mapping[str, Any], path: str, fail: Any
) -> None:
    required = shape.get("required")
    if isinstance(required, Sequence) and not isinstance(required, str):
        for name in required:
            if isinstance(name, str) and name not in value:
                fail(f"{path}.{name}", "required", "is required and was not supplied")

    properties = shape.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    additional = shape.get("additionalProperties")

    for name, child in value.items():
        child_path = f"{path}.{name}"
        if name in properties:
            for finding in validate(properties[name], child, child_path):
                _append(fail, finding)
            continue
        if additional is False:
            fail(
                child_path,
                "additionalProperties",
                "is not declared by the shape and the shape does not allow extra fields",
            )


def _validate_array(shape: Mapping[str, Any], value: Sequence[Any], path: str, fail: Any) -> None:
    if isinstance(shape.get("minItems"), int) and len(value) < shape["minItems"]:
        fail(path, "minItems", f"must have at least {shape['minItems']} item(s)")
    if isinstance(shape.get("maxItems"), int) and len(value) > shape["maxItems"]:
        fail(path, "maxItems", f"must have at most {shape['maxItems']} item(s)")
    if shape.get("uniqueItems") is True:
        seen: list[Any] = []
        for item in value:
            if item in seen:
                fail(path, "uniqueItems", "must not repeat an item")
                break
            seen.append(item)
    items = shape.get("items")
    if isinstance(items, Mapping):
        for index, item in enumerate(value):
            for finding in validate(items, item, f"{path}[{index}]"):
                _append(fail, finding)


def _append(fail: Any, finding: Mapping[str, str]) -> None:
    fail(finding["path"], finding["keyword"], finding["message"])


def conforms(shape: Mapping[str, Any], value: Any) -> bool:
    """``True`` when the value satisfies the shape."""
    return not validate(shape, value)
