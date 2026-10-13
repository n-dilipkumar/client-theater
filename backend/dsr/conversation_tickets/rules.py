"""WF-110's pure rules: validation, trigger matching, the gate, and the payloads.

Nothing here reads or writes. Every function takes plain data and returns plain
data, so a test drives a rule with a literal rather than through a store. That is
what lets a rule's failure name the rule rather than the transport.

The three researched limits are here
------------------------------------

``trigger_matches``
    Fan-out order matters. The tracker exemption is checked before the conditions
    for the ticket-created trigger, because "the workflow will not trigger from
    tracker ticket creation" is a rule about the event, not about the ticket's
    fields. A tracker ticket that happens to match the type condition must still
    not fire, and the record must say why.
``action_gate`` and ``action_skip``
    "Tag conversation, Apply SLA, and Set CDA actions will only work if the
    ticket acted upon was converted from a conversation." The gate is a
    precondition: an unconverted ticket runs the ungated actions and records the
    gated ones as skipped, rather than failing the whole evaluation.
``state_topics`` and ``webhook_payload``
    "ticket.resolved ... is a state-based event" and "ticket.closed ... is
    separate from state transitions". They are two topics with two payload
    shapes, and :func:`read_webhook_ticket` reads both rather than one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.conversation_tickets import vocabulary as vocab
from dsr.conversation_tickets.errors import TagNotInWorkspace, TeamUnknown, TicketRefused

# --------------------------------------------------------------------------- #
# Instants
# --------------------------------------------------------------------------- #


def to_iso(moment: datetime) -> str:
    """One instant, in the sortable form the store uses."""

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def parse_instant(value: str | None) -> datetime | None:
    """Read an instant, or return ``None`` for a value that is not one."""

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# The small validators
# --------------------------------------------------------------------------- #


def _require_one_of(value: Any, allowed: Sequence[str], field: str, code: str) -> str:
    text = str(value or "").strip()
    if text not in allowed:
        raise TicketRefused(
            f"{field} must be one of {', '.join(allowed)}; got {value!r}",
            {field: "unknown"},
            code=code,
        )
    return text


def require_team(value: Any) -> str:
    """A team from the known set, which the Slack notification needs."""

    text = str(value or "").strip()
    if text not in vocab.TEAMS:
        raise TeamUnknown(f"team must be one of {', '.join(vocab.TEAMS)}; got {value!r}")
    return text


def require_ticket_type(value: Any) -> str:
    return _require_one_of(value, vocab.TICKET_TYPES, "ticket_type", "ticket_type_unknown")


def require_ticket_state(value: Any) -> str:
    return _require_one_of(value, vocab.TICKET_STATES, "state", "ticket_state_unknown")


def require_created_via(value: Any) -> str:
    return _require_one_of(value, vocab.CREATED_VIA, "created_via", "invalid_ticket_request")


def normalise_tags(values: Any) -> list[str]:
    """A tag list, de-duplicated, order preserved, blank entries dropped."""

    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, Iterable):
        raise TicketRefused("tags must be a list of strings", {"tags": "invalid"})
    seen: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


# --------------------------------------------------------------------------- #
# Conversion and the tracker exclusion
# --------------------------------------------------------------------------- #


def converted(data: Mapping[str, Any]) -> bool:
    """True when the ticket carries a linked conversation.

    "the ticket acted upon was converted from a conversation" is exactly this
    predicate. It is a property of the row, not of the route that created it, so
    a later ``link_conversation`` turns an unconverted ticket into a converted
    one and the gate answers differently afterwards.
    """

    return bool(str(data.get("conversation_id") or "").strip())


def is_tracker_created(created_via: Any) -> bool:
    """True when the tracker created the ticket.

    "the workflow will not trigger from tracker ticket creation", so this is the
    exclusion rather than a condition.
    """

    return str(created_via or "").strip() == vocab.VIA_TRACKER


def owning_team(ticket: Mapping[str, Any]) -> str | None:
    """The team that holds the ticket, or ``None`` when none is assigned."""

    team = str(ticket.get("team") or "").strip()
    return team or None


# --------------------------------------------------------------------------- #
# Ticket validation
# --------------------------------------------------------------------------- #


def validate_ticket(
    payload: Mapping[str, Any] | None,
    *,
    known_teams: Sequence[str] = vocab.TEAMS,
) -> dict[str, Any]:
    """Normalise a ticket payload, and refuse the shapes this workflow rejects.

    The conversion is derived from ``conversation_id``, not from a flag a caller
    could set independently. A ticket created via ``conversation`` must carry a
    conversation. A tracker ticket must not, because the tracker does not convert
    conversations.
    """

    data = dict(payload or {})
    errors: dict[str, str] = {}

    ticket_type = str(data.get("ticket_type") or vocab.DEFAULT_TICKET_TYPE).strip()
    if ticket_type not in vocab.TICKET_TYPES:
        errors["ticket_type"] = "unknown"
    state = str(data.get("state") or vocab.DEFAULT_TICKET_STATE).strip()
    if state not in vocab.TICKET_STATES:
        errors["state"] = "unknown"

    conversation_id = str(data.get("conversation_id") or "").strip() or None
    created_via = str(data.get("created_via") or "").strip()
    if not created_via:
        created_via = vocab.VIA_CONVERSATION if conversation_id else vocab.VIA_API
    if created_via not in vocab.CREATED_VIA:
        errors["created_via"] = "unknown"
    if created_via == vocab.VIA_CONVERSATION and not conversation_id:
        errors["conversation_id"] = "required when created_via is conversation"
    if created_via == vocab.VIA_TRACKER and conversation_id:
        errors["conversation_id"] = "a tracker ticket is not converted from a conversation"

    team = str(data.get("team") or "").strip() or None
    if team is not None and team not in known_teams:
        errors["team"] = "unknown"

    contacts = [str(item) for item in (data.get("contacts") or []) if str(item).strip()]
    if not contacts:
        email = str(data.get("contact_email") or "").strip()
        if email:
            contacts = [email]

    if errors:
        raise TicketRefused(
            "the ticket payload has " + ", ".join(sorted(errors)),
            errors,
        )

    return {
        "subject": str(data.get("subject") or "Untitled ticket").strip(),
        "ticket_type": ticket_type,
        "state": state,
        "created_via": created_via,
        "conversation_id": conversation_id,
        "team": team,
        "admin": str(data.get("admin") or "").strip() or None,
        "contacts": contacts,
        "tags": normalise_tags(data.get("tags")),
        "priority": bool(data.get("priority")),
        "sla": None,
        "conversation_closed": False,
        "room_ref": str(data.get("room_id") or data.get("room_ref") or "").strip(),
    }


# --------------------------------------------------------------------------- #
# Tags, teams and SLA policies
# --------------------------------------------------------------------------- #


def validate_tag(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    data = dict(payload or {})
    name = str(data.get("name") or "").strip()
    if not name:
        raise TicketRefused("a workspace tag needs a name", {"name": "required"})
    return {"name": name, "description": str(data.get("description") or "").strip()}


def resolve_tag(name: Any, workspace: Sequence[Mapping[str, Any]]) -> str:
    """The workspace's canonical spelling of a tag, or a refusal by name.

    A tag an action names must exist in the workspace. The action cannot create
    one, because a tag that exists only on one ticket is not a shared
    vocabulary. The match is case-insensitive and returns the stored spelling.
    """

    wanted = str(name or "").strip().lower()
    for record in workspace:
        stored = str(record.get("data", {}).get("name", ""))
        if stored.lower() == wanted:
            return stored
    raise TagNotInWorkspace(f"tag {name!r} is not in the workspace")


def validate_sla_policy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    data = dict(payload or {})
    name = str(data.get("name") or "").strip()
    if not name:
        raise TicketRefused("an SLA policy needs a name", {"name": "required"})
    metric = str(data.get("metric") or vocab.DEFAULT_SLA_METRIC).strip()
    if metric not in vocab.SLA_METRICS:
        raise TicketRefused(
            f"metric must be one of {', '.join(vocab.SLA_METRICS)}",
            {"metric": "unknown"},
        )
    try:
        hours = int(data.get("target_hours", vocab.DEFAULT_SLA_HOURS))
    except (TypeError, ValueError) as exc:
        raise TicketRefused(
            "target_hours must be a whole number", {"target_hours": "invalid"}
        ) from exc
    if hours <= 0:
        raise TicketRefused("target_hours must be greater than zero", {"target_hours": "invalid"})
    return {"name": name, "metric": metric, "target_hours": hours}


def sla_due(now: datetime, policy: Mapping[str, Any]) -> str:
    """The instant an SLA policy falls due, measured from now."""

    hours = int(policy.get("target_hours") or vocab.DEFAULT_SLA_HOURS)
    return to_iso(now + timedelta(hours=hours))


# --------------------------------------------------------------------------- #
# Triggers
# --------------------------------------------------------------------------- #


def validate_trigger(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalise a trigger: its kind, its conditions and its actions."""

    data = dict(payload or {})
    errors: dict[str, str] = {}
    name = str(data.get("name") or "").strip()
    if not name:
        errors["name"] = "required"
    kind = str(data.get("kind") or vocab.DEFAULT_TRIGGER_KIND).strip()
    if kind not in vocab.TRIGGER_KINDS:
        errors["kind"] = "unknown"

    raw_conditions = data.get("conditions")
    conditions = dict(raw_conditions) if isinstance(raw_conditions, Mapping) else {}
    for key in ("ticket_types",):
        values = conditions.get(key) or []
        if isinstance(values, str):
            values = [values]
        bad = [v for v in values if str(v) not in vocab.TICKET_TYPES]
        if bad:
            errors[key] = f"unknown: {', '.join(str(v) for v in bad)}"
        conditions[key] = [str(v) for v in values]
    for key in ("teams",):
        values = conditions.get(key) or []
        if isinstance(values, str):
            values = [values]
        bad = [v for v in values if str(v) not in vocab.TEAMS]
        if bad:
            errors[key] = f"unknown: {', '.join(str(v) for v in bad)}"
        conditions[key] = [str(v) for v in values]
    for key in ("to_states",):
        values = conditions.get(key) or []
        if isinstance(values, str):
            values = [values]
        bad = [v for v in values if str(v) not in vocab.TICKET_STATES]
        if bad:
            errors[key] = f"unknown: {', '.join(str(v) for v in bad)}"
        conditions[key] = [str(v) for v in values]
    conditions.setdefault("ticket_types", [])
    conditions.setdefault("teams", [])
    conditions.setdefault("to_states", [])

    raw_actions = data.get("actions") or []
    actions: list[dict[str, Any]] = []
    if not isinstance(raw_actions, list) or not raw_actions:
        errors["actions"] = "at least one action is required"
    else:
        for index, item in enumerate(raw_actions):
            action = dict(item) if isinstance(item, Mapping) else {}
            action_kind = str(action.get("kind") or "").strip()
            if action_kind not in vocab.ACTION_KINDS:
                errors[f"actions[{index}].kind"] = "unknown"
                continue
            actions.append(
                {
                    "kind": action_kind,
                    "team": str(action.get("team") or "").strip() or None,
                    "policy": str(action.get("policy") or "").strip() or None,
                    "tag": str(action.get("tag") or "").strip() or None,
                }
            )

    channel = str(data.get("channel") or "").strip() or None
    if errors:
        raise TicketRefused("the trigger has " + ", ".join(sorted(errors)), errors)
    return {
        "name": name,
        "kind": kind,
        "state": "draft",
        "conditions": conditions,
        "actions": actions,
        "channel": channel,
        "goal": str(data.get("goal") or "").strip(),
    }


def trigger_matches(
    trigger: Mapping[str, Any],
    ticket: Mapping[str, Any],
    *,
    event_kind: str,
    to_state: str | None = None,
) -> dict[str, Any]:
    """Whether one trigger fires for one ticket event, and why not.

    Returns ``{"matched": bool, "reason": code|None}``. The order is the point:
    the tracker exemption is checked before the type condition, so a tracker
    ticket that matches the type is still skipped by the exclusion.
    """

    if str(trigger.get("state")) != "live":
        return {"matched": False, "reason": vocab.SKIP_TRIGGER_NOT_LIVE}
    if str(trigger.get("kind")) != event_kind:
        return {"matched": False, "reason": vocab.SKIP_KIND_NOT_MATCHED}
    if event_kind == vocab.TRIGGER_TICKET_CREATED and is_tracker_created(ticket.get("created_via")):
        return {"matched": False, "reason": vocab.SKIP_TRACKER_CREATED}

    conditions = trigger.get("conditions") or {}
    wanted_types = list(conditions.get("ticket_types") or [])
    if wanted_types and str(ticket.get("ticket_type")) not in wanted_types:
        return {"matched": False, "reason": vocab.SKIP_TYPE_NOT_MATCHED}
    wanted_teams = list(conditions.get("teams") or [])
    if wanted_teams and str(ticket.get("team") or "") not in wanted_teams:
        return {"matched": False, "reason": vocab.SKIP_TEAM_NOT_MATCHED}
    wanted_states = list(conditions.get("to_states") or [])
    if wanted_states and str(to_state or "") not in wanted_states:
        return {"matched": False, "reason": vocab.SKIP_STATE_NOT_MATCHED}
    return {"matched": True, "reason": None}


# --------------------------------------------------------------------------- #
# The converted-conversation gate
# --------------------------------------------------------------------------- #


def action_gate(action_kind: str, ticket: Mapping[str, Any]) -> bool:
    """True when the converted-conversation precondition lets the action run."""

    if action_kind not in vocab.CONVERSION_REQUIRED_ACTIONS:
        return True
    return converted(ticket)


def action_skip(
    action: Mapping[str, Any],
    ticket: Mapping[str, Any],
    *,
    known_tags: Sequence[Mapping[str, Any]] = (),
    known_policies: Sequence[Mapping[str, Any]] = (),
) -> str | None:
    """The outcome code for a gated action, or ``None`` when it may run.

    The gate is checked first, because "the ticket acted upon was converted from
    a conversation" is the precondition the research names. An unknown policy or
    tag is a second, narrower reason.
    """

    kind = str(action.get("kind"))
    if not action_gate(kind, ticket):
        return vocab.ACTION_SKIPPED_NOT_CONVERTED
    if kind == vocab.ACTION_NOTIFY_TEAM:
        if not owning_team(ticket):
            return vocab.ACTION_SKIPPED_UNKNOWN_TEAM
    if kind == vocab.ACTION_ASSIGN_TEAM:
        team = str(action.get("team") or "").strip()
        if not team or team not in vocab.TEAMS:
            return vocab.ACTION_SKIPPED_UNKNOWN_TEAM
    if kind == vocab.ACTION_APPLY_SLA:
        policy = str(action.get("policy") or "").strip()
        names = {str(record.get("data", {}).get("name", "")) for record in known_policies}
        if not policy or policy not in names:
            return vocab.ACTION_SKIPPED_UNKNOWN_POLICY
    if kind == vocab.ACTION_TAG_CONVERSATION:
        tag = str(action.get("tag") or "").strip()
        names = {str(record.get("data", {}).get("name", "")).lower() for record in known_tags}
        if not tag or tag.lower() not in names:
            return vocab.ACTION_SKIPPED_UNKNOWN_TAG
    return None


def action_requires_conversion(action_kind: str) -> bool:
    return action_kind in vocab.CONVERSION_REQUIRED_ACTIONS


# --------------------------------------------------------------------------- #
# Webhooks: the two topics and the two payload shapes
# --------------------------------------------------------------------------- #


def state_topics(previous: str, new: str) -> list[str]:
    """Every topic a state transition emits, in order.

    ``ticket.state.updated`` always fires. A transition into a state that has its
    own event fires that too. ``resolved`` and ``closed`` stay apart: ``closed``
    is not produced here, because it is not a state transition.
    """

    topics = [vocab.TOPIC_STATE_UPDATED]
    dedicated = vocab.STATE_EVENT_TOPICS.get(str(new))
    if dedicated and dedicated not in topics:
        topics.append(dedicated)
    return topics


def parse_api_version(version: str) -> tuple[int, ...]:
    """Read a dotted version, tolerating a trailing marker such as ``+``."""

    parts: list[int] = []
    for chunk in str(version).split("."):
        digits = ""
        for character in chunk:
            if character.isdigit():
                digits += character
            else:
                break
        if digits:
            parts.append(int(digits))
        else:
            break
    return tuple(parts)


def uses_nested_shape(topic: str, api_version: str = vocab.API_VERSION) -> bool:
    """Whether the topic nests the ticket under ``ticket`` on ``data.item``.

    "the Ticket is nested under a ticket field on data.item" is the
    ``ticket.closed`` shape. The boundary is ``v2.15+``; below it the difference
    the research names does not exist yet.
    """

    if topic not in vocab.NESTED_TICKET_TOPICS:
        return False
    return parse_api_version(api_version) >= parse_api_version(vocab.API_VERSION)


def payload_shape(topic: str, api_version: str = vocab.API_VERSION) -> str:
    return vocab.PAYLOAD_NESTED if uses_nested_shape(topic, api_version) else vocab.PAYLOAD_FLAT


def webhook_payload(
    topic: str,
    ticket: Mapping[str, Any],
    *,
    api_version: str = vocab.API_VERSION,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one webhook payload in the shape its topic uses.

    ``ticket.closed`` nests the ticket under a ``ticket`` field on ``data.item``.
    Every other topic carries the ticket fields directly on ``data.item``. One
    builder decides this from the topic, so no caller can pair the wrong topic
    with the wrong shape.
    """

    item: dict[str, Any] = {
        "id": ticket.get("id"),
        "ticket_type": ticket.get("ticket_type"),
        "state": ticket.get("state"),
        "team": ticket.get("team"),
        "conversation_id": ticket.get("conversation_id"),
    }
    if extra:
        item.update(dict(extra))
    if uses_nested_shape(topic, api_version):
        item = {"ticket": item}
    return {"type": topic, "api_version": api_version, "data": {"item": item}}


def read_webhook_ticket(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Read the ticket from either payload shape.

    A reader that assumed one shape would return ``{}`` for the other and look
    like an empty webhook rather than a parsing bug. This one handles both, and
    the test names both.
    """

    item = (payload.get("data") or {}).get("item") or {}
    nested = item.get("ticket")
    if isinstance(nested, Mapping):
        return dict(nested)
    return dict(item)
