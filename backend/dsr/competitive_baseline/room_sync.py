"""WF-125: the pure rules of a CRM-driven room lifecycle.

The researched specification is
``docs/research/digital-sales-room-workflows/wf/WF-125.md``, quoted in full in
issue 126. A seller picks a Salesforce Opportunity or a HubSpot Deal, the room
is generated from the CRM fields, a data-driven rule creates rooms when a
condition is met, and every room engagement event writes back to the CRM's
standard and custom objects.

This is a **competitive baseline**, not a vendor integration guide. It builds
the sync surface and the rule, not a real Salesforce client. The shape was
chosen by Jev (audit ``jev-20261014T094842-5076-22331``, confidence 0.98,
selected ``mirror_records``):

* The CRM side is **mirrored records** in the audited store. One record per
  opportunity, account, contact or custom object, with an open payload. There
  is no fixed column list, so a team that adds a custom object adds a row.
* The **read is a store read**. The rule evaluates dotted JSON paths against a
  mirrored payload, so the rule is data-driven rather than a UI trigger.
* The **room is generated from the mirrored record** through a template.
* Each **write-back lands on the mirrored record** and is logged as one
  write-back row per target object, so custom objects are the acceptance bar
  rather than an afterthought.

The module imports the store and nothing else. It opens no database connection,
it names no route, and it imports no framework, so every rule below is
exercised without a server. The HTTP surface in
``dsr/features/wf125_crm_room_sync.py`` owns the routes and the error mapping.

The alternative this build rejected is the fixed-field shape: deal name, stage
and amount as typed columns plus one named custom object. That shape cannot
honestly claim to cover custom objects, and the research is explicit that a
sync which touches only standard fields "lands in your CRM too shallow to act
on".
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Collections. One shared `records` table carries every feature, so each name is
# namespaced per ticket and `find()` matches on the collection before anything
# else.
# --------------------------------------------------------------------------- #

#: One room-creation rule: a CRM, an object, a condition set and a template.
RULE_COLLECTION = "wf125_rule"

#: One mirrored CRM record: an opportunity, account, contact or custom object,
#: with an open payload. This is the local model of the CRM's own tables.
RECORD_COLLECTION = "wf125_crm_record"

#: One deal to one generated room, plus the template and the path that made it.
LINK_COLLECTION = "wf125_room_link"

#: One write-back target: which CRM object receives which engagement facts.
TARGET_COLLECTION = "wf125_writeback_target"

#: One engagement event: a view, a download or a share.
EVENT_COLLECTION = "wf125_engagement_event"

#: One write-back of one event to one target object. The acceptance bar: a row
#: per target object, standard or custom.
WRITEBACK_COLLECTION = "wf125_writeback"

#: The product's own room collection. A generated room is a real room.
ROOM_COLLECTION = "room"

#: The payload-side twin of the envelope's `room_id`. `room_id` is stripped from
#: `data` before the dynamic index is built, so a payload that stored its room
#: there would be unfilterable by `find()`.
ROOM_REF = "room_ref"

#: Keys the store owns. They are stripped from a payload before it is written.
RESERVED_KEYS = frozenset(
    {"id", "collection", "room_id", "revision", "created_at", "updated_at", "deleted_at"}
)

# --------------------------------------------------------------------------- #
# The CRM vocabulary
# --------------------------------------------------------------------------- #

#: The three CRM systems the research names. These strings match
#: ``dsr.crm_integration.vocabulary.CRM_SYSTEMS``, which is the merged WF-042
#: connector's vocabulary. The feature module asserts the two agree rather than
#: letting them drift apart quietly.
CRM_SYSTEMS: tuple[str, ...] = ("salesforce", "dataverse", "hubspot")

#: The objects whose names the product already knows. Any other object name is a
#: custom object and is accepted by the same code path.
STANDARD_OBJECTS: tuple[str, ...] = ("deal", "opportunity", "account", "contact", "product")

#: Every object this workflow may write back to, standard and custom alike.
OBJECT_KINDS = ("standard", "custom")

#: The comparisons a rule condition may use. `exists` reads the field for
#: presence and ignores `value`.
CONDITION_OPS: tuple[str, ...] = (
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "contains",
    "exists",
)

#: The engagement event kinds the research names: "write-back of every
#: view/download/share".
ENGAGEMENT_KINDS: tuple[str, ...] = ("view", "download", "share")

#: The payload field each event kind increments on the mirrored deal.
COUNTER_FIELDS: dict[str, str] = {
    "view": "room_view_count",
    "download": "room_download_count",
    "share": "room_share_count",
}

#: The write-back states. `synced` is the success; `pending` and `failed` are
#: the two states the demo must also show.
PENDING_STATE = "pending"
SYNCED_STATE = "synced"
FAILED_STATE = "failed"
WRITEBACK_STATES = (PENDING_STATE, SYNCED_STATE, FAILED_STATE)

#: The path a rule and a seller pick name to say how a room was made.
CREATED_FROM_RULE = "rule"
CREATED_FROM_SELLER = "seller"

# --------------------------------------------------------------------------- #
# Roles: per-user room provisioning based on role (Walnut Admin/Editor/Presenter)
# --------------------------------------------------------------------------- #

ROLES: tuple[str, ...] = ("admin", "editor", "presenter")

#: What each role may do to a room. Narrower than the CRM's own rights on
#: purpose: this is the room's view of a role, not the CRM's.
ROLE_CAPABILITIES: dict[str, dict[str, bool]] = {
    "admin": {"provision": True, "edit": True, "present": True, "delete": True},
    "editor": {"provision": False, "edit": True, "present": True, "delete": False},
    "presenter": {"provision": False, "edit": False, "present": True, "delete": False},
}

#: The capabilities a role the product has not heard of receives: present only.
DEFAULT_ROLE_CAPABILITY: dict[str, bool] = {
    "provision": False,
    "edit": False,
    "present": True,
    "delete": False,
}

# --------------------------------------------------------------------------- #
# The decisions the issue left open, each with the alternative rejected.
# --------------------------------------------------------------------------- #

DECISIONS: dict[str, str] = {
    "shape": (
        "The CRM side is mirrored records in the audited store, one per object "
        "with an open payload. The alternative a fixed-field model was rejected: "
        "a fixed column list cannot cover the custom objects the research calls "
        "the acceptance bar."
    ),
    "custom_objects": (
        "Standard and custom objects share one code path. An object name outside "
        "the known set is a custom object. A team registers a write-back target "
        "for it as a row, so no migration and no typed column is needed."
    ),
    "writeback_granularity": (
        "The write-back is per engagement event, not per session: one write-back "
        "row per target object per view, download or share, matching the "
        "research's 'automatic CRM write-back of every view/download/share'."
    ),
    "room_creation_paths": (
        "Two paths ship. A seller picks a deal and provisions one room, and a "
        "data-driven rule provisions a room for every mirrored record that "
        "matches its condition set. The rule reads stored JSON, so it is not a "
        "UI trigger."
    ),
    "rule_engine": (
        "The rule engine is this module's own condition evaluator over dotted "
        "JSON paths. The research names n8n, Node-RED, Windmill and Kestra as "
        "open-source substitutes, but it also notes none of them speaks "
        "Salesforce natively, so the connector stays this repository's."
    ),
    "connector": (
        "No real CRM client. The mirror is the local model of the CRM, so a "
        "write-back that lands on the mirror is what would be sent. This build "
        "makes no network call and needs no credentials."
    ),
    "crm_read": (
        "The CRM read is WF-042's connector, not this module. This workflow "
        "consumes mirrored records, so it does not restate the vendor query "
        "path that WF-042 owns."
    ),
    "permissions": (
        "Roles are Admin, Editor and Presenter, and each maps to a capability "
        "set. Per-user provisioning is a capability lookup rather than a new "
        "auth model, because the product already owns authentication."
    ),
    "mirror_writeback": (
        "A write-back lands on the mirrored record and is logged as one row per "
        "target object. When a custom object has no mirror yet, the target "
        "decides: create the mirror, or fail the write-back and wait for the "
        "CRM row to be ingested. Both paths ship, so neither is a surprise."
    ),
}

#: What this build deliberately does not do, named so a reviewer does not have
#: to guess whether a gap is a decision or an oversight.
NOT_IMPLEMENTED: tuple[str, ...] = (
    "A real CRM client: no Salesforce, HubSpot or Dynamics call is made.",
    "The CRM read path: WF-042's connector owns it, and this build reads mirrors.",
    "Object storage for a room's files: the room is a record, not a bucket.",
    "Email or Slack delivery of a room invite; the API returns the room.",
    "A hosted rule engine; the condition evaluator runs in-process.",
)


class RoomSyncError(ValueError):
    """A rule, a record, a target or an event this workflow will not accept.

    Carries a field-keyed map, because a seller filling in a form needs the
    message beside the input that caused it. The HTTP layer renders the map as
    ``errors``.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})


class RoomSyncRefused(PermissionError):
    """A change the current state will not accept, or a write that did not land.

    ``reason`` is a stable machine token, so the page branches on it instead of
    parsing an English sentence.
    """

    #: A write was made but did not read back.
    REASON_WRITE_NOT_VISIBLE = "write_not_visible"

    #: The rule is disabled, so it provisions nothing.
    REASON_RULE_DISABLED = "rule_disabled"

    #: The CRM object this target writes to has no mirror yet.
    REASON_MIRROR_ABSENT = "mirror_absent"

    #: The write-back is already synced, so a retry writes nothing.
    REASON_ALREADY_SYNCED = "already_synced"

    _MESSAGES = {
        REASON_WRITE_NOT_VISIBLE: "The change was written but did not read back.",
        REASON_RULE_DISABLED: "That rule is disabled. Enable it before you apply it.",
        REASON_MIRROR_ABSENT: "The CRM object has no mirror yet, so the write-back failed.",
        REASON_ALREADY_SYNCED: "That write-back is already synced.",
    }

    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(detail or self._MESSAGES.get(reason, "That change was refused."))
        self.reason = reason


class RoomSyncNotFound(LookupError):
    """No such rule, mirrored record, target, event, write-back or room.

    Its own type rather than the store's ``RecordNotFound``, because a feature
    may map only the error types it raises itself.
    """


# --------------------------------------------------------------------------- #
# Coercion and the low-level readers
# --------------------------------------------------------------------------- #


def read_int(value: Any, default: int | None = None) -> int | None:
    """Read a whole number, or ``default`` when the value is not one.

    A bool is refused: ``True`` is not a count or an amount.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def read_number(value: Any) -> float | None:
    """Read a number for a comparison, or ``None`` when the value is not one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def as_datetime(value: Any) -> datetime | None:
    """Read an ISO date or timestamp as an aware datetime, or ``None``.

    Returns ``None`` rather than raising for a malformed value, because one
    unreadable timestamp must not stop a sync.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def strip_reserved(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """A payload without the keys the store owns."""
    return {key: value for key, value in dict(payload or {}).items() if key not in RESERVED_KEYS}


def dig(payload: Any, path: str, default: Any = None) -> Any:
    """Read a dotted path out of an arbitrary JSON payload.

    Mirrored records are whatever a team put in ``data``, so every read has to
    tolerate absence. A numeric segment indexes into a list, so
    ``products.0.name`` reads back what was written.
    """
    current = payload
    for part in str(path or "").split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, (list, tuple)) and part.lstrip("-").isdigit():
            index = int(part)
            if -len(current) <= index < len(current):
                current = current[index]
            else:
                return default
        else:
            return default
    return current


def object_kind(object_name: Any) -> str:
    """Whether an object name is standard or custom. Any unknown name is custom."""
    folded = str(object_name or "").strip().lower()
    if not folded:
        raise RoomSyncError("A write-back needs a target object.", {"object": "Name the object."})
    return "standard" if folded in STANDARD_OBJECTS else "custom"


def require_crm(value: Any) -> str:
    """The canonical CRM system name, case-folded, or a field-keyed error."""
    folded = str(value or "").strip().lower()
    if folded not in CRM_SYSTEMS:
        raise RoomSyncError(
            "The CRM could not be read.",
            {"crm": "crm must be one of " + ", ".join(CRM_SYSTEMS) + "."},
        )
    return folded


def require_engagement_kind(value: Any) -> str:
    """The canonical engagement kind, or a field-keyed error."""
    folded = str(value or "").strip().lower()
    if folded not in ENGAGEMENT_KINDS:
        raise RoomSyncError(
            "The event could not be read.",
            {"kind": "kind must be one of " + ", ".join(ENGAGEMENT_KINDS) + "."},
        )
    return folded


# --------------------------------------------------------------------------- #
# The condition rule. This is the one rule that matters.
# --------------------------------------------------------------------------- #


def evaluate_condition(condition: Mapping[str, Any], payload: Any) -> bool:
    """One condition against one payload. A missing field matches nothing but ``ne``.

    The operator set is the rule's whole vocabulary, so a rule is data: a stored
    row, evaluated against a stored record, with no call back into the UI.
    """
    field = str(condition.get("field") or "").strip()
    op = str(condition.get("op") or "eq").strip().lower()
    expected = condition.get("value")
    actual = dig(payload, field)

    if op == "exists":
        return actual is not None
    if actual is None:
        # A field the record does not carry is "not equal to" any value and is
        # not greater, less, in or containing anything. `ne` is the one operator
        # a missing field satisfies, and it does so deliberately.
        return op == "ne"

    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op in ("gt", "gte", "lt", "lte"):
        left, right = read_number(actual), read_number(expected)
        if left is not None and right is not None:
            return {
                "gt": left > right,
                "gte": left >= right,
                "lt": left < right,
                "lte": left <= right,
            }[op]
        # Not both numeric: compare the text, which is what a stage ladder wants.
        return {
            "gt": str(actual) > str(expected),
            "gte": str(actual) >= str(expected),
            "lt": str(actual) < str(expected),
            "lte": str(actual) <= str(expected),
        }[op]
    if op == "in":
        options = expected if isinstance(expected, (list, tuple, set)) else [expected]
        return actual in options
    if op == "contains":
        if isinstance(actual, (list, tuple, set)):
            return expected in actual
        return str(expected) in str(actual)
    return False


def rule_matches(rule: Mapping[str, Any], payload: Any) -> bool:
    """Whether a record's payload satisfies every condition of a rule.

    All conditions must hold. An empty condition set matches nothing, because a
    rule that matches everything is a rule nobody wrote on purpose.
    """
    conditions = rule.get("conditions") or []
    if not conditions:
        return False
    return all(evaluate_condition(condition, payload) for condition in conditions)


def matching_records(
    rule: Mapping[str, Any], records: Iterable[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    """The records a rule's payload conditions select."""
    return [record for record in records if rule_matches(rule, data_of(record))]


# --------------------------------------------------------------------------- #
# Validation. A field this workflow has never heard of is kept.
# --------------------------------------------------------------------------- #


def validate_condition(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalise one condition, or raise with a message per bad field."""
    data = strip_reserved(payload)
    errors: dict[str, str] = {}
    field = str(data.get("field") or "").strip()
    if not field:
        errors["field"] = "Name the record field the condition reads."
    op = str(data.get("op") or "eq").strip().lower()
    if op not in CONDITION_OPS:
        errors["op"] = "op must be one of " + ", ".join(CONDITION_OPS) + "."
    if op != "exists" and "value" not in data:
        errors["value"] = "Give the value the condition compares against."
    if errors:
        raise RoomSyncError("The rule condition could not be read.", errors)
    normalised = dict(data)
    normalised["field"] = field
    normalised["op"] = op
    return normalised


def validate_rule(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalise a room-creation rule, or raise with a message per bad field."""
    data = strip_reserved(payload)
    errors: dict[str, str] = {}

    name = str(data.get("name") or "").strip()
    if not name:
        errors["name"] = "Name the rule."

    crm = str(data.get("crm") or "").strip().lower()
    if crm not in CRM_SYSTEMS:
        errors["crm"] = "crm must be one of " + ", ".join(CRM_SYSTEMS) + "."

    object_name = str(data.get("object") or "").strip()
    if not object_name:
        errors["object"] = "Name the CRM object the rule reads."

    raw_conditions = data.get("conditions")
    conditions: list[dict[str, Any]] = []
    if not isinstance(raw_conditions, list) or not raw_conditions:
        errors["conditions"] = "A rule needs at least one condition."
    else:
        for position, condition in enumerate(raw_conditions):
            try:
                conditions.append(validate_condition(condition))
            except RoomSyncError as exc:
                errors[f"conditions.{position}"] = str(exc)

    if errors:
        raise RoomSyncError("The rule could not be read.", errors)

    normalised = dict(data)
    normalised["name"] = name
    normalised["crm"] = crm
    normalised["object"] = object_name
    normalised["conditions"] = conditions
    normalised["enabled"] = bool(data.get("enabled", True))
    if data.get("template_id") is not None:
        normalised["template_id"] = str(data["template_id"])
    return normalised


def validate_record(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalise a mirrored CRM record, or raise with a message per bad field.

    Everything other than the three addressing fields is kept verbatim. That is
    the whole of schema flexibility: a team's custom object fields need no
    migration and no typed column.
    """
    data = strip_reserved(payload)
    errors: dict[str, str] = {}

    crm = str(data.get("crm") or "").strip().lower()
    if crm not in CRM_SYSTEMS:
        errors["crm"] = "crm must be one of " + ", ".join(CRM_SYSTEMS) + "."

    object_name = str(data.get("object") or "").strip()
    if not object_name:
        errors["object"] = "Name the CRM object."

    external_id = str(data.get("external_id") or "").strip()
    if not external_id:
        errors["external_id"] = "Give the CRM's own id for this record."

    if errors:
        raise RoomSyncError("The CRM record could not be read.", errors)

    normalised = dict(data)
    normalised["crm"] = crm
    normalised["object"] = object_name
    normalised["external_id"] = external_id
    return normalised


def validate_target(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalise a write-back target, or raise with a message per bad field.

    ``fields`` maps a CRM field name to a fact path, so a custom object's field
    list is data. ``create_mirror`` decides what happens when the CRM row does
    not exist locally yet.
    """
    data = strip_reserved(payload)
    errors: dict[str, str] = {}

    object_name = str(data.get("object") or "").strip()
    if not object_name:
        errors["object"] = "Name the object the write-back targets."

    raw_fields = data.get("fields")
    fields: dict[str, str] = {}
    if not isinstance(raw_fields, Mapping) or not raw_fields:
        errors["fields"] = "Give at least one field mapping."
    else:
        for key, value in raw_fields.items():
            target_field = str(key or "").strip()
            if not target_field:
                errors["fields"] = "A field mapping needs a target field name."
                break
            fields[target_field] = str(value)

    crm = data.get("crm")
    if crm not in (None, ""):
        crm = str(crm).strip().lower()
        if crm not in CRM_SYSTEMS:
            errors["crm"] = "crm must be one of " + ", ".join(CRM_SYSTEMS) + "."

    if errors:
        raise RoomSyncError("The write-back target could not be read.", errors)

    normalised = dict(data)
    normalised["object"] = object_name
    normalised["fields"] = fields
    if crm:
        normalised["crm"] = crm
    normalised["enabled"] = bool(data.get("enabled", True))
    normalised["auto_sync"] = bool(data.get("auto_sync", True))
    normalised["create_mirror"] = bool(data.get("create_mirror", False))
    if data.get("external_id_path") is not None:
        normalised["external_id_path"] = str(data["external_id_path"])
    return normalised


def validate_event(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalise an engagement event, or raise with a message per bad field."""
    data = strip_reserved(payload)
    errors: dict[str, str] = {}

    kind = str(data.get("kind") or "").strip().lower()
    if kind not in ENGAGEMENT_KINDS:
        errors["kind"] = "kind must be one of " + ", ".join(ENGAGEMENT_KINDS) + "."

    at = data.get("at")
    if at not in (None, "") and as_datetime(at) is None:
        errors["at"] = "at must be an ISO 8601 date or timestamp."

    if errors:
        raise RoomSyncError("The event could not be read.", errors)

    normalised = dict(data)
    normalised["kind"] = kind
    if at not in (None, ""):
        normalised["at"] = str(at)
    if normalised.get("actor") is not None:
        normalised["actor"] = str(normalised["actor"])
    return normalised


# --------------------------------------------------------------------------- #
# Generation and projection
# --------------------------------------------------------------------------- #


def room_plan(
    rule: Mapping[str, Any] | None, deal: Mapping[str, Any], *, deal_id: str | None = None
) -> dict[str, Any]:
    """The fields a room is generated with from a mirrored CRM deal.

    The user flow names the fields: "name, contacts, value, stage, products".
    A field the deal does not carry is written as ``None`` rather than dropped,
    so the room's shape does not depend on how complete the CRM record was.
    """
    deal = deal or {}
    rule = rule or {}
    name = dig(deal, "name") or dig(deal, "deal_name") or dig(deal, "account") or "Deal room"
    plan: dict[str, Any] = {
        "name": str(name),
        "account": dig(deal, "account"),
        "stage": dig(deal, "stage"),
        "value": dig(deal, "value"),
        "currency": dig(deal, "currency"),
        "contacts": list(dig(deal, "contacts", []) or []),
        "products": list(dig(deal, "products", []) or []),
        "crm": deal.get("crm"),
        "external_id": deal.get("external_id"),
        "deal_id": deal_id,
    }
    if rule.get("template_id") is not None:
        plan["template_id"] = rule.get("template_id")
    return plan


def engagement_facts(
    event: Mapping[str, Any], deal: Mapping[str, Any], *, deal_id: str | None = None
) -> dict[str, Any]:
    """The facts one engagement event exposes to a write-back target.

    A target names a fact path here. The deal's running counters are part of the
    facts, which is how a target writes back a view count rather than a bare
    event marker.
    """
    return {
        "deal_id": deal_id,
        "external_id": deal.get("external_id"),
        "crm": deal.get("crm"),
        "kind": event.get("kind"),
        "at": event.get("at"),
        "view_count": read_int(deal.get("room_view_count"), 0),
        "download_count": read_int(deal.get("room_download_count"), 0),
        "share_count": read_int(deal.get("room_share_count"), 0),
        "last_activity_at": deal.get("last_room_activity_at"),
        "stage": deal.get("stage"),
        "value": deal.get("value"),
        "account": deal.get("account"),
        "name": deal.get("name"),
    }


def project_fields(fields: Mapping[str, Any], facts: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve ``{crm_field: fact_path}`` into ``{crm_field: value}``.

    A fact path absent from the facts resolves to ``None`` rather than raising: a
    deal with no share yet should not crash the run.
    """
    return {str(target): dig(facts, str(source)) for target, source in fields.items()}


def role_capability(role: Any) -> dict[str, bool]:
    """What a role may do to a room. An unknown role presents, and nothing more."""
    return dict(ROLE_CAPABILITIES.get(str(role or "").strip().lower(), DEFAULT_ROLE_CAPABILITY))


def vocabulary() -> dict[str, Any]:
    """The vocabulary this workflow enforces, and the decisions behind it."""
    return {
        "crm_systems": list(CRM_SYSTEMS),
        "standard_objects": list(STANDARD_OBJECTS),
        "object_kinds": list(OBJECT_KINDS),
        "condition_ops": list(CONDITION_OPS),
        "engagement_kinds": list(ENGAGEMENT_KINDS),
        "counter_fields": dict(COUNTER_FIELDS),
        "writeback_states": list(WRITEBACK_STATES),
        "roles": list(ROLES),
        "role_capabilities": {role: dict(caps) for role, caps in ROLE_CAPABILITIES.items()},
        "decisions": dict(DECISIONS),
        "not_implemented": list(NOT_IMPLEMENTED),
    }


# --------------------------------------------------------------------------- #
# Record helpers. The store returns whole records; the presenters flatten the
# payload and hide nothing, because every field here is the team's own.
# --------------------------------------------------------------------------- #


def data_of(record: Mapping[str, Any]) -> dict[str, Any]:
    """The payload of a record, as a fresh mutable dict."""
    return dict(record.get("data") or {})


def room_of(record: Mapping[str, Any]) -> Any:
    """The room a record belongs to, from the envelope or the payload."""
    return record.get("room_id") or data_of(record).get(ROOM_REF)


def present_rule(record: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": record["id"], **data_of(record)}


def present_record(record: Mapping[str, Any]) -> dict[str, Any]:
    data = data_of(record)
    return {"id": record["id"], "object_kind": object_kind(data.get("object")), **data}


def present_target(record: Mapping[str, Any]) -> dict[str, Any]:
    data = data_of(record)
    return {"id": record["id"], "object_kind": object_kind(data.get("object")), **data}


def present_link(record: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": record["id"], "room_id": room_of(record), **data_of(record)}


def present_event(record: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": record["id"], **data_of(record)}


def present_writeback(record: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": record["id"], **data_of(record)}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# The engine: where the pure rules meet the store.
# --------------------------------------------------------------------------- #


class RoomSyncEngine:
    """Reads and writes rules, mirrors, targets, rooms, events and write-backs.

    ``now`` is injectable so a test can pin the clock. The production route
    leaves it at the real clock. Every write takes a ``source`` argument from
    its caller, and the feature module builds that string from its own router: a
    literal URL here would leave the audit log naming a route the app stopped
    serving.
    """

    def __init__(self, store: RecordStore, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._now = now or _utcnow

    # -- reads of one record ------------------------------------------------ #

    def _require(self, collection: str, record_id: str, label: str) -> dict[str, Any]:
        record = self.store.get(record_id)
        if record is None or record.get("collection") != collection:
            raise RoomSyncNotFound(f"No such {label}: {record_id}")
        return record

    def _list(self, collection: str, *, limit: int = 500) -> list[dict[str, Any]]:
        return self.store.list(collection, limit=limit)

    # -- rules -------------------------------------------------------------- #

    def list_rules(self, enabled: bool | None = None) -> list[dict[str, Any]]:
        records = self._list(RULE_COLLECTION)
        records.sort(key=lambda record: str(record.get("created_at") or ""))
        rows = [present_rule(record) for record in records]
        if enabled is None:
            return rows
        return [row for row in rows if bool(row.get("enabled")) is enabled]

    def read_rule(self, rule_id: str) -> dict[str, Any]:
        return present_rule(self._require(RULE_COLLECTION, rule_id, "rule"))

    def create_rule(
        self, payload: Mapping[str, Any] | None, *, source: str, actor: str
    ) -> dict[str, Any]:
        """Write a data-driven room-creation rule."""
        normalised = validate_rule(payload)
        record = self.store.create(RULE_COLLECTION, normalised, source=source, actor=actor)
        return present_rule(record)

    def update_rule(
        self, rule_id: str, patch: Mapping[str, Any] | None, *, source: str, actor: str
    ) -> dict[str, Any]:
        """Change a rule's conditions, target object, template or enabled flag."""
        record = self._require(RULE_COLLECTION, rule_id, "rule")
        merged = {**data_of(record), **dict(patch or {})}
        normalised = validate_rule(merged)
        fresh = self._write_verified(
            record["id"],
            normalised,
            expect={"name": normalised["name"], "enabled": normalised["enabled"]},
            source=source,
            actor=actor,
        )
        return present_rule(fresh)

    def delete_rule(self, rule_id: str, *, source: str, actor: str) -> dict[str, Any]:
        """Remove a rule. The rooms it already made are left alone."""
        self._require(RULE_COLLECTION, rule_id, "rule")
        self.store.delete(rule_id, source=source, actor=actor)
        return {"removed": True, "id": rule_id}

    # -- mirrored CRM records ----------------------------------------------- #

    def list_records(
        self, object_name: str | None = None, crm: str | None = None
    ) -> list[dict[str, Any]]:
        records = self._list(RECORD_COLLECTION)
        rows = [present_record(record) for record in records]
        if object_name is not None:
            rows = [row for row in rows if str(row.get("object")) == str(object_name)]
        if crm is not None:
            rows = [row for row in rows if str(row.get("crm")) == str(crm)]
        rows.sort(key=lambda row: (str(row.get("object")), str(row.get("external_id"))))
        return rows

    def read_record(self, record_id: str) -> dict[str, Any]:
        return present_record(self._require(RECORD_COLLECTION, record_id, "CRM record"))

    def _find_mirror(
        self, crm: str | None, object_name: str, external_id: Any
    ) -> dict[str, Any] | None:
        if external_id in (None, ""):
            return None
        where: dict[str, Any] = {"object": object_name, "external_id": external_id}
        if crm:
            where["crm"] = crm
        found = self.store.find(RECORD_COLLECTION, where, limit=1)
        return found[0] if found else None

    def ingest_record(
        self, payload: Mapping[str, Any] | None, *, source: str, actor: str
    ) -> dict[str, Any]:
        """Upsert one mirrored CRM record, keyed by (crm, object, external_id).

        The upsert is why the mirror stays a mirror: a later read of the same
        CRM record updates the row rather than making a second one.
        """
        normalised = validate_record(payload)
        existing = self._find_mirror(
            normalised["crm"], normalised["object"], normalised["external_id"]
        )
        if existing is None:
            record = self.store.create(RECORD_COLLECTION, normalised, source=source, actor=actor)
        else:
            record = self._write_verified(
                existing["id"],
                normalised,
                expect={
                    "crm": normalised["crm"],
                    "object": normalised["object"],
                    "external_id": normalised["external_id"],
                },
                source=source,
                actor=actor,
            )
        return present_record(record)

    # -- write-back targets -------------------------------------------------- #

    def list_targets(self, enabled: bool | None = None) -> list[dict[str, Any]]:
        records = self._list(TARGET_COLLECTION)
        records.sort(key=lambda record: str(record.get("created_at") or ""))
        rows = [present_target(record) for record in records]
        if enabled is None:
            return rows
        return [row for row in rows if bool(row.get("enabled")) is enabled]

    def create_target(
        self, payload: Mapping[str, Any] | None, *, source: str, actor: str
    ) -> dict[str, Any]:
        """Register one write-back target, standard or custom."""
        normalised = validate_target(payload)
        record = self.store.create(TARGET_COLLECTION, normalised, source=source, actor=actor)
        return present_target(record)

    # -- rooms --------------------------------------------------------------- #

    def list_rooms(self, deal_id: str | None = None) -> list[dict[str, Any]]:
        records = self._list(LINK_COLLECTION)
        rows = [present_link(record) for record in records]
        if deal_id is not None:
            rows = [row for row in rows if str(row.get("deal_id")) == str(deal_id)]
        rows.sort(key=lambda row: str(row.get("created_at") or ""))
        return rows

    def evaluate(self, rule_id: str | None = None, *, source: str, actor: str) -> dict[str, Any]:
        """Apply the enabled rules and provision a room per matching record.

        Idempotent by (rule, deal): a second apply makes no second room. A rule
        named by id must be enabled; a disabled rule refuses rather than being
        silently skipped, so a caller learns why nothing happened.
        """
        if rule_id is not None:
            rule = self.read_rule(rule_id)
            if not rule.get("enabled"):
                raise RoomSyncRefused(RoomSyncRefused.REASON_RULE_DISABLED)
            rules = [rule]
        else:
            rules = self.list_rules(enabled=True)

        matched = 0
        provisioned: list[dict[str, Any]] = []
        for rule in rules:
            candidates = [
                record
                for record in self.list_records(object_name=rule["object"], crm=rule["crm"])
                if rule_matches(rule, record)
            ]
            matched += len(candidates)
            for record in candidates:
                link = self._link_for(rule_id=rule["id"], deal_id=record["id"])
                if link is not None:
                    continue
                provisioned.append(self._provision(rule, record, source=source, actor=actor))
        return {
            "rules": len(rules),
            "matched": matched,
            "provisioned": len(provisioned),
            "rooms": provisioned,
        }

    def provision_deal(
        self,
        deal_id: str,
        *,
        template_id: str | None = None,
        source: str,
        actor: str,
    ) -> dict[str, Any]:
        """The explicit seller pick: one deal, one room.

        Idempotent by deal: a deal that already has a room returns the existing
        link rather than making a second one.
        """
        record = self._require(RECORD_COLLECTION, deal_id, "CRM record")
        existing = self._link_for(deal_id=deal_id)
        if existing is not None:
            return existing
        rule = {"id": None, "template_id": template_id}
        return self._provision(
            rule, present_record(record), created_from="seller", source=source, actor=actor
        )

    def _link_for(
        self, *, rule_id: str | None = None, deal_id: str | None = None
    ) -> dict[str, Any] | None:
        where: dict[str, Any] = {}
        if deal_id is not None:
            where["deal_id"] = deal_id
        if rule_id is not None:
            where["rule_id"] = rule_id
        if not where:
            return None
        found = self.store.find(LINK_COLLECTION, where, limit=1)
        return present_link(found[0]) if found else None

    def _provision(
        self,
        rule: Mapping[str, Any],
        deal: Mapping[str, Any],
        *,
        created_from: str = CREATED_FROM_RULE,
        source: str,
        actor: str,
    ) -> dict[str, Any]:
        """Generate one room from one deal and link the two."""
        plan = room_plan(rule, deal, deal_id=deal.get("id"))
        room = self.store.create(ROOM_COLLECTION, plan, source=source, actor=actor)
        data: dict[str, Any] = {
            "rule_id": rule.get("id"),
            "deal_id": deal.get("id"),
            "crm": deal.get("crm"),
            "object": deal.get("object"),
            "external_id": deal.get("external_id"),
            "template_id": rule.get("template_id"),
            "created_from": created_from,
            "created_at": self._now().isoformat(),
        }
        link = self.store.create(
            LINK_COLLECTION, data, room_id=room["id"], source=source, actor=actor
        )
        presented = present_link(link)
        presented["room"] = {"id": room["id"], "name": plan["name"]}
        return presented

    # -- engagement events and write-back ------------------------------------ #

    def list_events(self, deal_id: str | None = None) -> list[dict[str, Any]]:
        records = self._list(EVENT_COLLECTION)
        rows = [present_event(record) for record in records]
        if deal_id is not None:
            rows = [row for row in rows if str(row.get("deal_id")) == str(deal_id)]
        rows.sort(key=lambda row: str(row.get("created_at") or ""))
        return rows

    def list_writebacks(
        self, event_id: str | None = None, state: str | None = None
    ) -> list[dict[str, Any]]:
        records = self._list(WRITEBACK_COLLECTION)
        rows = [present_writeback(record) for record in records]
        if event_id is not None:
            rows = [row for row in rows if str(row.get("event_id")) == str(event_id)]
        if state is not None:
            rows = [row for row in rows if str(row.get("state")) == str(state)]
        rows.sort(key=lambda row: str(row.get("created_at") or ""))
        return rows

    def record_event(
        self, deal_id: str, payload: Mapping[str, Any] | None, *, source: str, actor: str
    ) -> dict[str, Any]:
        """Record one engagement event and write it back to every target.

        The write-back is per event, not per session. The event is created
        first, then one write-back row per enabled target. A target with
        ``auto_sync`` false is left ``pending``; the retry route settles it.
        """
        record = self._require(RECORD_COLLECTION, deal_id, "CRM record")
        normalised = validate_event(payload)
        at = str(normalised.get("at") or self._now().isoformat())
        kind = normalised["kind"]
        counter = COUNTER_FIELDS[kind]
        current = data_of(record)
        new_count = (read_int(current.get(counter), 0) or 0) + 1
        fresh = self._write_verified(
            record["id"],
            {counter: new_count, "last_room_activity_at": at, "last_engagement_kind": kind},
            expect={counter: new_count},
            source=source,
            actor=actor,
        )
        deal = data_of(fresh)
        facts = engagement_facts({"kind": kind, "at": at}, deal, deal_id=deal_id)
        event_data: dict[str, Any] = {
            "deal_id": deal_id,
            "crm": deal.get("crm"),
            "object": deal.get("object"),
            "external_id": deal.get("external_id"),
            "kind": kind,
            "at": at,
            "facts": facts,
        }
        if normalised.get("actor") is not None:
            event_data["actor"] = normalised["actor"]
        event = self.store.create(EVENT_COLLECTION, event_data, source=source, actor=actor)

        writebacks: list[dict[str, Any]] = []
        for target in self.list_targets(enabled=True):
            writebacks.append(self._write_back(target, fresh, event, source=source, actor=actor))
        return {
            "event": present_event(event),
            "writebacks": [present_writeback(record) for record in writebacks],
        }

    def retry_writeback(self, writeback_id: str, *, source: str, actor: str) -> dict[str, Any]:
        """Re-attempt one write-back that is pending or failed.

        A synced write-back refuses, so a retry cannot double-write a recorded
        engagement.
        """
        record = self._require(WRITEBACK_COLLECTION, writeback_id, "write-back")
        row = data_of(record)
        if str(row.get("state")) == SYNCED_STATE:
            raise RoomSyncRefused(RoomSyncRefused.REASON_ALREADY_SYNCED)
        deal = self._require(RECORD_COLLECTION, str(row.get("deal_id")), "CRM record")
        state, reason = self._apply_writeback(row, deal, source=source, actor=actor)
        fresh = self._write_verified(
            record["id"],
            {
                "state": state,
                "reason": reason,
                "attempts": (read_int(row.get("attempts"), 0) or 0) + 1,
            },
            expect={"state": state},
            source=source,
            actor=actor,
        )
        return present_writeback(fresh)

    def _write_back(
        self,
        target: Mapping[str, Any],
        deal_record: Mapping[str, Any],
        event: Mapping[str, Any],
        *,
        source: str,
        actor: str,
    ) -> dict[str, Any]:
        """One write-back row for one event and one target object."""
        facts = data_of(event).get("facts") or {}
        deal = data_of(deal_record)
        fields = project_fields(target.get("fields") or {}, facts)
        crm = target.get("crm") or deal.get("crm")
        external_id = dig(deal, str(target.get("external_id_path") or "external_id"))
        auto = bool(target.get("auto_sync", True))
        if not auto:
            state, reason = PENDING_STATE, None
        else:
            state, reason = self._apply_writeback(
                {
                    "object": target["object"],
                    "crm": crm,
                    "external_id": external_id,
                    "fields": fields,
                    "object_kind": object_kind(target["object"]),
                    "create_mirror": bool(target.get("create_mirror", False)),
                },
                deal_record,
                source=source,
                actor=actor,
            )
        row: dict[str, Any] = {
            "event_id": event["id"],
            "deal_id": deal_record["id"],
            "crm": crm,
            "object": target["object"],
            "object_kind": object_kind(target["object"]),
            "external_id": external_id,
            "fields": fields,
            "auto_sync": auto,
            "create_mirror": bool(target.get("create_mirror", False)),
            "state": state,
            "reason": reason,
            "attempts": 0 if not auto else 1,
            "at": data_of(event).get("at"),
        }
        return self.store.create(WRITEBACK_COLLECTION, row, source=source, actor=actor)

    def _apply_writeback(
        self,
        row: Mapping[str, Any],
        deal_record: Mapping[str, Any],
        *,
        source: str,
        actor: str,
    ) -> tuple[str, str | None]:
        """Write the projected fields onto the mirrored target object.

        Returns ``(state, reason)``. The write is read back before it reports
        success, so a write that did not land becomes ``failed`` rather than a
        success the store did not keep.
        """
        fields = dict(row.get("fields") or {})
        crm = row.get("crm")
        object_name = str(row.get("object"))
        external_id = row.get("external_id")
        mirror = self._find_mirror(crm, object_name, external_id)
        try:
            if mirror is None:
                if not row.get("create_mirror"):
                    return FAILED_STATE, RoomSyncRefused.REASON_MIRROR_ABSENT
                self.store.create(
                    RECORD_COLLECTION,
                    {
                        "crm": crm,
                        "object": object_name,
                        "external_id": external_id,
                        **fields,
                    },
                    source=source,
                    actor=actor,
                )
            else:
                self._write_verified(
                    mirror["id"], fields, expect=fields, source=source, actor=actor
                )
        except RoomSyncRefused as exc:
            return FAILED_STATE, exc.reason
        return SYNCED_STATE, None

    # -- summary ------------------------------------------------------------- #

    def summary(self) -> dict[str, Any]:
        """The headline numbers, including the states that are not successes."""
        now = self._now()
        rules = self.list_rules()
        records = self.list_records()
        targets = self.list_targets()
        links = self.list_rooms()
        events = self.list_events()
        writebacks = self.list_writebacks()
        states = [str(row.get("state")) for row in writebacks]
        return {
            "rules": len(rules),
            "rules_enabled": sum(1 for rule in rules if rule.get("enabled")),
            "records": len(records),
            "standard_objects": sum(
                1 for row in records if object_kind(row.get("object")) == "standard"
            ),
            "custom_objects": sum(
                1 for row in records if object_kind(row.get("object")) == "custom"
            ),
            "targets": len(targets),
            "custom_targets": sum(
                1 for target in targets if object_kind(target.get("object")) == "custom"
            ),
            "rooms": len(links),
            "events": len(events),
            "writebacks": len(writebacks),
            "pending": states.count(PENDING_STATE),
            "synced": states.count(SYNCED_STATE),
            "failed": states.count(FAILED_STATE),
            "generated_at": now.isoformat(),
        }

    # -- internals ----------------------------------------------------------- #

    def _write_verified(
        self,
        record_id: str,
        patch: Mapping[str, Any],
        *,
        expect: Mapping[str, Any],
        source: str,
        actor: str,
    ) -> dict[str, Any]:
        """Write, read back, and refuse to report success if it did not land."""
        self.store.update(record_id, patch, source=source, actor=actor)
        fresh = self.store.get(record_id)
        if fresh is None:
            raise RoomSyncRefused(
                RoomSyncRefused.REASON_WRITE_NOT_VISIBLE,
                f"record {record_id} vanished after a write.",
            )
        data = data_of(fresh)
        for key, value in expect.items():
            if data.get(key) != value:
                raise RoomSyncRefused(
                    RoomSyncRefused.REASON_WRITE_NOT_VISIBLE,
                    f"{key} did not read back as written.",
                )
        return fresh
