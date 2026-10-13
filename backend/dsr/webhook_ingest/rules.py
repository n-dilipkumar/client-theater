"""WF-113: the pure rules - parse, resolve, render, plan. No storage, no HTTP.

Every function here is total over the JSON the research calls "JSON of any
shape": a body may be an object, an array, a string, a number or null, and the
parse step wraps whatever arrives into a trigger data object instead of
rejecting it. That is the one rule the workflow turns on - the sender is an
external commerce platform or a Zapier replacement, and a build that accepted
only one shape would refuse the exact payloads the research describes.

Four rules with a reason each
-----------------------------
* **Any shape parses.** :func:`trigger_data` records the body as it arrived and
  derives an ``identifiers`` map and a flat ``attributes`` map beside it. A body
  that is not an object is wrapped, never dropped.
* **Customer data resolves to the empty string.** :func:`resolve_expression`
  returns :data:`~dsr.webhook_ingest.vocabulary.CUSTOMER_DATA_PLACEHOLDER` for any
  path that does not start at the trigger object. Quoted: *"Any attempt to
  reference customer data will return ``""``"*.
* **The batch ceiling is a wall.** :func:`plan_fanout` raises
  :class:`~dsr.webhook_ingest.errors.BatchLimitExceeded` for a group over 1,000
  rather than truncating it, because the research states the number as a fact.
* **Deletes are events.** A Delete or Suppress is a name like any other event
  name, so nothing here special-cases it.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from dsr.webhook_ingest import vocabulary as vocab
from dsr.webhook_ingest.errors import BatchLimitExceeded, InvalidValueType, UnknownAction

__all__ = [
    "canonical_json",
    "check_rate",
    "fingerprint",
    "flatten",
    "get_path",
    "plan_fanout",
    "primary_identifier",
    "render_javascript",
    "render_liquid",
    "render_mapping",
    "render_value",
    "resolve_expression",
    "resolve_identifiers",
    "shape_of",
    "trigger_data",
]

_MISSING = object()

_LIQUID_RE = re.compile(r"\{\{\s*(?P<expr>[^}]+?)\s*\}\}")
_JS_RETURN_RE = re.compile(r"^return\s+(?P<expr>.+?)\s*;?\s*$", re.DOTALL)

#: The two roots a mapping may start at. The Liquid example uses the singular
#: ``trigger`` and the JavaScript example uses the plural ``triggers``; the
#: research quotes both, so both resolve.
_TRIGGER_ROOTS = ("trigger", "triggers")


# --------------------------------------------------------------------------- #
# Any shape parses
# --------------------------------------------------------------------------- #


def shape_of(value: Any) -> str:
    """The JSON shape a body arrived in, named rather than inferred."""
    if value is None:
        return "empty"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return "scalar"


def canonical_json(value: Any) -> str:
    """A stable serialisation, so a fingerprint of equal bodies is equal."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def fingerprint(value: Any) -> str:
    """The dedupe key for one body: sha256 of its canonical JSON.

    Equal bytes are not required and equal objects are. The research describes a
    service that retries a request it did not see acknowledged, and a retry
    re-serialises, so keying on the object rather than the bytes is what makes a
    retry recognisable.
    """
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def get_path(value: Any, path: str) -> Any:
    """Read a dotted path out of arbitrary JSON. ``_MISSING`` when absent."""
    if not path:
        return _MISSING
    node: Any = value
    for segment in str(path).split("."):
        if isinstance(node, Mapping):
            if segment not in node:
                return _MISSING
            node = node[segment]
        elif isinstance(node, (list, tuple)):
            if not segment.lstrip("-").isdigit():
                return _MISSING
            try:
                node = node[int(segment)]
            except IndexError:
                return _MISSING
        else:
            return _MISSING
    return node


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Every scalar in a body, keyed by dotted path.

    Objects and arrays are walked rather than stored whole, because the
    downstream event's ``data`` is a flat map a person maps fields into. A
    nested object is still reachable by its dotted path.
    """
    out: dict[str, Any] = {}
    if isinstance(value, Mapping):
        for key, child in value.items():
            out.update(flatten(child, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            out.update(flatten(child, f"{prefix}.{index}" if prefix else str(index)))
    elif prefix:
        out[prefix] = value
    return out


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool))


def resolve_identifiers(payload: Any) -> dict[str, str]:
    """Every identifier value this body carries, keyed by identifier type.

    Read from the *incoming* payload, never from an existing person: the research
    forbids the latter in the same sentence that gives the placeholder. The keys
    are the identifier types the research names, so ``trigger.identifiers.id``
    is the Liquid the research quotes.
    """
    found: dict[str, str] = {}
    for row in vocab.IDENTIFIER_TYPES:
        for path in row["paths"]:
            value = get_path(payload, path)
            if value is not _MISSING and _is_scalar(value) and str(value).strip() != "":
                found[row["id"]] = _stringify(value)
                break
    # ``custom`` has no fixed path; the body may name one directly.
    custom = get_path(payload, "identifiers.custom")
    if custom is not _MISSING and _is_scalar(custom) and str(custom).strip() != "":
        found["custom"] = _stringify(custom)
    return found


def primary_identifier(identifiers: Mapping[str, Any], identifier_type: str) -> str | None:
    """The one identifier a person is matched on, or the first one present."""
    value = identifiers.get(identifier_type)
    if value:
        return str(value)
    return next((str(value) for value in identifiers.values() if value), None)


def trigger_data(
    payload: Any,
    *,
    endpoint_ref: str,
    automation_ref: str,
    received_at: str,
    identifier_type: str = "id",
) -> dict[str, Any]:
    """The researched Trigger data object, from a body of any shape.

    The returned object *is* the resolution context for a mapping: a body that is
    an object has its fields spread at the top, so the research's
    ``{{trigger.identifiers.id}}`` and any ``{{trigger.customer.email}}`` both
    resolve. ``body`` keeps the payload exactly as it arrived, so nothing is lost
    and a reader sees what the external service actually sent.
    """
    if isinstance(payload, Mapping):
        view: dict[str, Any] = dict(payload)
    elif isinstance(payload, (list, tuple)):
        view = {"items": list(payload)}
    else:
        view = {"value": payload}
    identifiers = resolve_identifiers(payload)
    # Derived keys are written after the spread, so a body that carries its own
    # key of the same name cannot shadow the resolved value a mapping reads.
    view["identifiers"] = identifiers
    view["attributes"] = flatten(payload)
    view["primary_identifier"] = primary_identifier(identifiers, identifier_type)
    view["identifier_type"] = identifier_type
    view["shape"] = shape_of(payload)
    view["fingerprint"] = fingerprint(payload)
    view["body"] = payload
    view["endpoint_ref"] = endpoint_ref
    view["automation_ref"] = automation_ref
    view["received_at"] = received_at
    return view


# --------------------------------------------------------------------------- #
# Liquid and JavaScript
# --------------------------------------------------------------------------- #


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return canonical_json(value)
    if value is None:
        return ""
    return str(value)


def resolve_expression(context: Mapping[str, Any], expression: Any) -> str:
    """Resolve one Liquid path or JavaScript path against the trigger.

    A path that does not start at the trigger object returns the empty string.
    That is the research's hard constraint, not this build's caution: *"Any
    attempt to reference customer data will return ``""``"*.
    """
    text = str(expression or "").strip()
    if not text:
        return ""
    head, _, _rest = text.partition(".")
    if head not in _TRIGGER_ROOTS:
        return vocab.CUSTOMER_DATA_PLACEHOLDER
    normalised = "trigger" + text[len(head) :]
    value = get_path(context, normalised)
    if value is _MISSING or value is None:
        return ""
    return _stringify(value)


def _apply_filter(spec: str, value: str) -> str:
    """The one filter the research names, and a pass-through for the rest.

    The example is ``purchased_at | date: %s``, and ``%s`` there means unix
    seconds. A filter this build does not implement returns the value unchanged
    rather than an empty string, so an unmapped field renders as itself.
    """
    if not value:
        return value
    name, _, _argument = spec.partition(":")
    if name.strip().lower() != "date":
        return value
    from datetime import datetime, timezone

    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return str(int(moment.timestamp()))


def render_liquid(template: Any, context: Mapping[str, Any]) -> str:
    """Replace every ``{{path}}`` token with the value the trigger holds."""

    def replace(match: re.Match[str]) -> str:
        expression = match.group("expr")
        path, _, filter_spec = expression.partition("|")
        value = resolve_expression(context, path)
        if filter_spec.strip():
            return _apply_filter(filter_spec.strip(), value)
        return value

    return _LIQUID_RE.sub(replace, str(template))


def render_javascript(expression: Any, context: Mapping[str, Any]) -> str:
    """Resolve the ``return ...;`` form the research quotes."""
    text = str(expression or "").strip()
    match = _JS_RETURN_RE.match(text)
    if match:
        return resolve_expression(context, match.group("expr"))
    return resolve_expression(context, text)


def render_value(value: Any, value_type: str, context: Mapping[str, Any]) -> Any:
    """Render one mapping value in the declared value type."""
    if value_type not in vocab.VALUE_TYPE_IDS:
        raise InvalidValueType(
            f"value_type {value_type!r} is not one of {list(vocab.VALUE_TYPE_IDS)}"
        )
    if not isinstance(value, str):
        return value
    if value_type == vocab.VALUE_TYPE_JAVASCRIPT:
        return render_javascript(value, context) if value.strip().startswith("return") else value
    if "{{" in value:
        return render_liquid(value, context)
    return value


def render_mapping(
    mapping: Mapping[str, Any] | None,
    value_type: str,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Render every value of one mapping. A non-mapping renders as empty."""
    if not isinstance(mapping, Mapping):
        return {}
    return {str(key): render_value(value, value_type, context) for key, value in mapping.items()}


# --------------------------------------------------------------------------- #
# The fan-out plan
# --------------------------------------------------------------------------- #


def _delivery(
    action_key: str, outcome: str, *, reason: str | None = None, count: int | None = None
) -> dict[str, Any]:
    return {"action": action_key, "outcome": outcome, "reason": reason, "count": count}


def plan_fanout(
    actions: Any,
    trigger: Mapping[str, Any],
    *,
    identifier_type: str = "id",
) -> dict[str, list[dict[str, Any]]]:
    """Turn an automation's actions and one trigger into events and deliveries.

    Pure: it writes nothing. The engine stores what this returns. An unknown
    action raises, an over-limit batch raises, and every other refusal is a
    *recorded outcome* rather than an exception, because "the Slack block had no
    channel" is a row a person reads, not a failed ingest.
    """
    context = {"trigger": trigger}
    events: list[dict[str, Any]] = []
    deliveries: list[dict[str, Any]] = []

    rows = actions if isinstance(actions, (list, tuple)) else []
    for action in rows:
        if not isinstance(action, Mapping):
            raise UnknownAction(f"an action must be an object, got {type(action).__name__}")
        kind = str(action.get("key") or "")
        if not vocab.is_known_action(kind):
            raise UnknownAction(f"action {kind!r} is not one of {list(vocab.ACTION_KEYS)}")
        value_type = str(action.get("value_type") or vocab.VALUE_TYPE_LIQUID)
        mapping = action.get("mapping") if isinstance(action.get("mapping"), Mapping) else {}
        name = render_value(action.get("name") or "", value_type, context)

        if kind == vocab.ACTION_SEND_EVENT:
            if not str(name).strip():
                deliveries.append(
                    _delivery(kind, vocab.OUTCOME_FAILED, reason=vocab.FAIL_EVENT_NAME_MISSING)
                )
                continue
            events.append(
                {
                    "name": str(name),
                    "data": render_mapping(mapping, value_type, context),
                    "action": kind,
                }
            )
            deliveries.append(_delivery(kind, vocab.OUTCOME_DELIVERED, count=1))

        elif kind == vocab.ACTION_CREATE_OR_UPDATE_PERSON:
            identifier = trigger.get("primary_identifier")
            if not identifier:
                deliveries.append(
                    _delivery(kind, vocab.OUTCOME_SKIPPED, reason=vocab.SKIP_NO_IDENTIFIER)
                )
                continue
            data = render_mapping(mapping, value_type, context)
            data["identifier"] = identifier
            events.append({"name": "person:upsert", "data": data, "action": kind})
            deliveries.append(_delivery(kind, vocab.OUTCOME_DELIVERED, count=1))

        elif kind == vocab.ACTION_BATCH_UPDATE:
            members = action.get("members")
            if members is None:
                members = get_path(trigger.get("body"), "group")
            if members is _MISSING or members is None:
                members = []
            if not isinstance(members, (list, tuple)):
                members = [members]
            if len(members) > vocab.BATCH_LIMIT:
                raise BatchLimitExceeded(
                    f"a Batch Update covers up to {vocab.BATCH_LIMIT} people; this one names {len(members)}.",
                    count=len(members),
                    limit=vocab.BATCH_LIMIT,
                )
            if not members:
                deliveries.append(
                    _delivery(kind, vocab.OUTCOME_SKIPPED, reason=vocab.SKIP_NO_BATCH)
                )
                continue
            event_name = str(name).strip() or "group:updated"
            for index, member in enumerate(members):
                data = render_mapping(mapping, value_type, context)
                data["member"] = member
                data["index"] = index
                events.append({"name": event_name, "data": data, "action": kind})
            deliveries.append(_delivery(kind, vocab.OUTCOME_DELIVERED, count=len(members)))

        elif kind == vocab.ACTION_SEND_DATA:
            target = str(action.get("target_url") or "").strip()
            if not target:
                deliveries.append(
                    _delivery(kind, vocab.OUTCOME_SKIPPED, reason=vocab.SKIP_NO_TARGET)
                )
                continue
            events.append(
                {
                    "name": "data:forwarded",
                    "data": {
                        "to": target,
                        "trigger_fingerprint": trigger.get("fingerprint"),
                        "payload": trigger.get("body"),
                    },
                    "action": kind,
                }
            )
            deliveries.append(_delivery(kind, vocab.OUTCOME_DELIVERED, count=1))

        elif kind == vocab.ACTION_SLACK:
            channel = str(action.get("channel") or "").strip()
            if not channel:
                deliveries.append(
                    _delivery(kind, vocab.OUTCOME_SKIPPED, reason=vocab.SKIP_NO_CHANNEL)
                )
                continue
            events.append(
                {
                    "name": "slack:message",
                    "data": {"channel": channel, **render_mapping(mapping, value_type, context)},
                    "action": kind,
                }
            )
            deliveries.append(_delivery(kind, vocab.OUTCOME_DELIVERED, count=1))

    return {"events": events, "deliveries": deliveries}


# --------------------------------------------------------------------------- #
# Rate limits: published, with the arithmetic a caller would otherwise re-do
# --------------------------------------------------------------------------- #


def check_rate(surface: str, count: int) -> dict[str, Any]:
    """Whether a count is inside the published bound for one surface.

    Nothing enforces this: the research calls the Track/Pipelines bound soft and
    describes no behaviour for crossing it. The function exists so a caller that
    wants the arithmetic gets it from one place rather than re-deriving it.
    """
    for row in vocab.RATE_LIMITS:
        if row["surface"] == surface:
            return {
                "surface": surface,
                "limit": row["limit"],
                "window_seconds": row["window_seconds"],
                "soft": row["soft"],
                "count": int(count),
                "allowed": int(count) <= int(row["limit"]),
            }
    raise KeyError(surface)
