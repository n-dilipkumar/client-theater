"""Pure rules for WF-115: validation, the state machine, and filter evaluation.

Every function here takes and returns plain data. There is no store, no request,
and no framework, so the rules can be exercised without opening a database and
the feature module stays a set of route declarations. This module imports nothing
from ``dsr``.

The blocking rules are in :func:`module_errors`; the advisory ones are in
:func:`module_warnings`. The split matters: a blocking problem refuses the write,
and a warning is returned next to a module that was accepted, so an incomplete
sample payload is visible without being fatal.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from . import vocabulary as vocab

_MISSING = object()

_HTTP_PREFIXES = ("http://", "https://")


def _is_http_url(value: object) -> bool:
    return (
        isinstance(value, str) and value.startswith(_HTTP_PREFIXES) and len(value) > len("http://")
    )


def key_problem(key: object) -> str | None:
    """The reason ``key`` is not a valid immutable key, or None.

    A key is the identifier a workflow references, so its shape is fixed: it
    starts with a letter, holds only lowercase letters, digits and underscores,
    and is between three and sixty-four characters.
    """
    if not isinstance(key, str) or not key:
        return "key is required and must be a string"
    if not re.match(vocab.KEY_PATTERN, key):
        return (
            f"key must match {vocab.KEY_PATTERN} (lowercase, start with a letter, 3-64 characters)"
        )
    return None


def dynamic_filter_count(filters: object) -> int:
    """How many Dynamic filters a filter list declares."""
    if not isinstance(filters, Sequence) or isinstance(filters, (str, bytes)):
        return 0
    count = 0
    for entry in filters:
        if isinstance(entry, Mapping) and entry.get("field_type") == vocab.FILTER_DYNAMIC:
            count += 1
    return count


def _filter_errors(filters: object) -> list[str]:
    if not isinstance(filters, Sequence) or isinstance(filters, (str, bytes)):
        return ["filters must be a list"]
    problems: list[str] = []
    for index, entry in enumerate(filters):
        where = f"filters[{index}]"
        if not isinstance(entry, Mapping):
            problems.append(f"{where} must be an object")
            continue
        field = entry.get("field")
        if not isinstance(field, str) or not field:
            problems.append(f"{where}.field is required")
        field_type = entry.get("field_type")
        if field_type not in vocab.FILTER_TYPES:
            problems.append(f"{where}.field_type must be one of {', '.join(vocab.FILTER_TYPES)}")
            continue
        if field_type in (vocab.FILTER_SELECT, vocab.FILTER_MULTI_SELECT):
            options = entry.get("options")
            has_options = (
                isinstance(options, Sequence)
                and not isinstance(options, (str, bytes))
                and len(options) > 0
            )
            if not has_options and entry.get("option_source") not in vocab.OPTION_SOURCES:
                problems.append(
                    f"{where} needs non-empty options or an option_source in "
                    f"{', '.join(vocab.OPTION_SOURCES)}"
                )
        if field_type == vocab.FILTER_DYNAMIC:
            if entry.get("option_source") != "external_api":
                problems.append(f"{where}.option_source must be external_api for a Dynamic filter")
            if not _is_http_url(entry.get("dynamic_url")):
                problems.append(f"{where}.dynamic_url must be an http(s) URL")
    dynamic = dynamic_filter_count(filters)
    if dynamic > vocab.DYNAMIC_FILTER_LIMIT:
        problems.append(
            f"at most {vocab.DYNAMIC_FILTER_LIMIT} Dynamic filter is allowed per module; "
            f"{dynamic} were declared"
        )
    return problems


def _custom_variable_errors(custom_variables: object) -> list[str]:
    if custom_variables is None:
        return []
    if not isinstance(custom_variables, Sequence) or isinstance(custom_variables, (str, bytes)):
        return ["custom_variables must be a list"]
    problems: list[str] = []
    for index, entry in enumerate(custom_variables):
        where = f"custom_variables[{index}]"
        if not isinstance(entry, Mapping):
            problems.append(f"{where} must be an object")
            continue
        if not isinstance(entry.get("name"), str) or not entry.get("name"):
            problems.append(f"{where}.name is required")
        if not isinstance(entry.get("key_path"), str) or not entry.get("key_path"):
            problems.append(f"{where}.key_path is required")
    return problems


def _execution_errors(kind: str, execution: object) -> list[str]:
    if kind != vocab.KIND_ACTION:
        return []
    if not isinstance(execution, Mapping):
        return ["an action needs an execution object with a method of url or custom_code"]
    method = execution.get("method")
    if method not in vocab.EXECUTION_METHODS:
        return ["execution.method must be one of " + ", ".join(vocab.EXECUTION_METHODS)]
    if method == vocab.EXECUTION_URL and not _is_http_url(execution.get("url")):
        return ["execution.url must be an http(s) URL when execution.method is url"]
    if method == vocab.EXECUTION_CUSTOM_CODE:
        code = execution.get("code")
        if not isinstance(code, str) or not code.strip():
            return ["execution.code is required when execution.method is custom_code"]
    return []


def _trigger_errors(kind: str, data: Mapping[str, Any]) -> list[str]:
    if kind != vocab.KIND_TRIGGER:
        return []
    problems: list[str] = []
    if not _is_http_url(data.get("subscription_url")):
        problems.append("a trigger needs a subscription_url that is an http(s) URL")
    if dynamic_filter_count(data.get("filters")) > 0 and not _is_http_url(
        data.get("dynamic_filter_url")
    ):
        problems.append("a trigger with a Dynamic filter needs a dynamic_filter_url http(s) URL")
    events = data.get("events")
    if events is not None:
        if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
            problems.append("events must be a list")
        else:
            unknown = [event for event in events if event not in vocab.EVENT_TYPES]
            if unknown:
                problems.append(
                    f"events may only be {', '.join(vocab.EVENT_TYPES)}; got {unknown[0]!r}"
                )
    return problems


def module_errors(kind: object, data: Mapping[str, Any]) -> list[str]:
    """Every blocking problem with a module payload. Empty means acceptable."""
    problems: list[str] = []
    if kind not in vocab.KINDS:
        problems.append(f"kind must be one of {', '.join(vocab.KINDS)}")
        return problems
    key_issue = key_problem(data.get("key"))
    if key_issue:
        problems.append(key_issue)
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        problems.append("name is required")
    sample = data.get("sample_payload")
    if sample is not None and not isinstance(sample, Mapping):
        problems.append("sample_payload must be a JSON object")
    if data.get("filters") is not None:
        problems.extend(_filter_errors(data.get("filters")))
    problems.extend(_custom_variable_errors(data.get("custom_variables")))
    problems.extend(_trigger_errors(str(kind), data))
    problems.extend(_execution_errors(str(kind), data.get("execution")))
    pause = data.get("pause_resume_webhook")
    if pause is not None and not _is_http_url(pause):
        problems.append("pause_resume_webhook must be an http(s) URL")
    if data.get("distribution") is not None and data.get("distribution") not in vocab.DISTRIBUTIONS:
        problems.append(f"distribution must be one of {', '.join(vocab.DISTRIBUTIONS)}")
    return problems


def module_warnings(kind: object, data: Mapping[str, Any]) -> list[str]:
    """Advisory findings that do not refuse the write."""
    warnings: list[str] = []
    sample = data.get("sample_payload")
    filters = data.get("filters")
    if kind == vocab.KIND_TRIGGER and not isinstance(sample, Mapping):
        warnings.append(
            "no sample_payload: filters and custom variables cannot be resolved for this trigger"
        )
    if (
        isinstance(sample, Mapping)
        and isinstance(filters, Sequence)
        and not isinstance(filters, (str, bytes))
    ):
        for entry in filters:
            if not isinstance(entry, Mapping) or not entry.get("required"):
                continue
            field = entry.get("field")
            if isinstance(field, str) and field and resolve_path(sample, field) is _MISSING:
                warnings.append(f"required filter {field!r} does not resolve in the sample payload")
    if kind == vocab.KIND_ACTION:
        execution = data.get("execution")
        if (
            isinstance(execution, Mapping)
            and execution.get("method") == vocab.EXECUTION_CUSTOM_CODE
        ):
            warnings.append("custom code is recorded as configuration and is never executed here")
    return warnings


def can_transition(state: object, target: object) -> bool:
    """Whether the review state machine allows ``state`` to move to ``target``."""
    if not isinstance(state, str) or not isinstance(target, str):
        return False
    return target in vocab.TRANSITIONS.get(state, ())


def targets_from(state: object) -> tuple[str, ...]:
    """Every state ``state`` may move to, including ``deleted``."""
    if not isinstance(state, str):
        return ()
    return vocab.TRANSITIONS.get(state, ())


def resolve_path(payload: object, path: object, default: Any = _MISSING) -> Any:
    """Resolve a dotted path against arbitrary JSON.

    ``{"contact": {"name": "Dana"}}`` resolves ``contact.name``. A list is
    indexed by its position, so ``items.0.sku`` works. ``default`` (the module
    sentinel when unset) is returned when any segment is missing.
    """
    if not isinstance(path, str) or not path:
        return default
    current = payload
    for segment in path.split("."):
        if isinstance(current, Mapping):
            if segment not in current:
                return default
            current = current[segment]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            if not segment.isdigit() or int(segment) >= len(current):
                return default
            current = current[int(segment)]
        else:
            return default
    return current


def evaluate_filters(filters: object, sample_payload: object) -> dict[str, Any]:
    """Resolve each filter's field against a sample payload.

    The platform resolves filters against the payload; this is the room's model
    of that resolution, used by the delivery route and the page. A filter is
    ``resolved`` when its field resolves. ``matched`` is true when every required
    filter resolves, which is the condition the platform uses to run the trigger.
    """
    rows: list[dict[str, Any]] = []
    resolved: dict[str, Any] = {}
    missing_required: list[str] = []
    if isinstance(filters, Sequence) and not isinstance(filters, (str, bytes)):
        for entry in filters:
            if not isinstance(entry, Mapping):
                continue
            field = entry.get("field")
            if not isinstance(field, str) or not field:
                continue
            value = resolve_path(sample_payload, field)
            found = value is not _MISSING
            if found:
                resolved[field] = value
            required = bool(entry.get("required"))
            if required and not found:
                missing_required.append(field)
            rows.append(
                {
                    "field": field,
                    "field_type": entry.get("field_type"),
                    "required": required,
                    "resolved": found,
                    "value": value if found else None,
                }
            )
    return {
        "matched": not missing_required,
        "filters": rows,
        "resolved": resolved,
        "missing_required": missing_required,
    }


def delivery_errors(event_type: object, target_url: object, filters: object) -> list[str]:
    """Every blocking problem with a subscription delivery payload."""
    problems: list[str] = []
    if event_type not in vocab.EVENT_TYPES:
        problems.append(f"event_type must be one of {', '.join(vocab.EVENT_TYPES)}")
    if not _is_http_url(target_url):
        problems.append("target_url must be an http(s) URL")
    if filters is not None and (
        not isinstance(filters, Sequence) or isinstance(filters, (str, bytes))
    ):
        problems.append("filters must be a list")
    return problems


__all__ = [
    "can_transition",
    "delivery_errors",
    "dynamic_filter_count",
    "evaluate_filters",
    "key_problem",
    "module_errors",
    "module_warnings",
    "resolve_path",
    "targets_from",
]
