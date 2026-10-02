"""A change event, normalised from whatever the transport delivered.

The research's data flow names the fields once: "change event (``changeType``,
``transactionKey``, ``sequenceNumber``, ``commitTimestamp``, ``changedFields``,
record payload)". Those six, plus the record identity the room resolves on, are
the whole envelope. Everything else a room wants is a room field and travels in
the payload, which is why the payload is stored verbatim rather than squeezed
into a fixed shape.

Normalisation is deliberately *lenient about the payload and strict about the
envelope*. The payload is arbitrary JSON - a team adding a CRM field must not need
coordination - while the six named fields are the contract between the transport
and this room, and a caller who gets one of them wrong has made a mistake worth
hearing about rather than a record worth storing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.change_stream import vocabulary
from dsr.change_stream.errors import (
    EntityNotOnChannel,
    MalformedEvent,
    MissingTransactionKey,
    UnknownChangeType,
)

#: The six fields the research names, and what this package calls each of them.
#:
#: The left column is the vendor spelling a client actually sends; the right is
#: the name it is stored under. The vendor spelling is accepted on input in both
#: cases, because the research writes the names in the vendor's camelCase and
#: every other payload in this product is snake_case, and a client that has to
#: remember which is which is a client that gets one wrong.
ENVELOPE_FIELDS: dict[str, tuple[str, ...]] = {
    "change_type": ("change_type", "changeType"),
    "transaction_key": ("transaction_key", "transactionKey"),
    "sequence_number": ("sequence_number", "sequenceNumber"),
    "commit_timestamp": ("commit_timestamp", "commitTimestamp"),
    "changed_fields": ("changed_fields", "changedFields"),
    "enriched_fields": ("enriched_fields", "enrichedFields", "enriched"),
    "entity": ("entity", "sobject", "changeOrigin", "changeOriginEntity"),
    "wire_format": ("wire_format", "wireFormat"),
}

#: The record payload's own aliases, kept out of the table above only so that
#: table reads as the research wrote it. A client that nests the record under
#: ``record`` rather than ``payload`` is common enough to be worth accepting, and
#: the payload is arbitrary JSON either way.
_PAYLOAD_ALIASES: tuple[str, ...] = ("payload", "record", "recordPayload", "record_payload")


def _pick(body: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in body and body[name] is not None:
            return body[name]
    return None


def parse_timestamp(value: Any) -> str:
    """An ISO-8601 timestamp, normalised to UTC and always with an offset.

    The research names ``commitTimestamp`` and nothing about its format, so the
    parsing is deliberately generous - a bare ``Z``, an offset, a naive value
    assumed to be UTC - and the *result* is strict: whatever arrived, what is
    stored is an unambiguous UTC instant, because the buffer orders a transaction
    by these and a naive local time is not orderable across two operators.
    """
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()
    raise MalformedEvent(
        "commit_timestamp is not an ISO-8601 instant; got "
        f"{value!r}. Name it explicitly rather than letting the room guess a zone."
    )


def parse_sequence_number(value: Any) -> int:
    """``sequenceNumber`` as an integer.

    A monotonic counter inside one transaction. Not optional in practice, so
    refused when absent - but the value is not required to *start* at anything in
    particular, only to order, and :func:`dsr.change_stream.buffering` sorts on it.
    """
    if isinstance(value, bool) or value is None:
        raise MalformedEvent(f"sequence_number is required and must be an integer; got {value!r}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise MalformedEvent(
            f"sequence_number is required and must be an integer; got {value!r}"
        ) from exc


def normalise_event(body: Mapping[str, Any], *, room_id: str | None = None) -> dict[str, Any]:
    """One change event, in the shape this package stores.

    Raises a :class:`~dsr.change_stream.errors.EventError` subclass rather than
    returning a partially-filled record, because a half-read change event is the
    one thing that must never reach the replica: the research's commit rule means
    a transaction is applied as a unit, and one malformed event inside a
    transaction would be committed or dropped with the rest of it.
    """
    if not isinstance(body, Mapping):
        raise MalformedEvent(f"a change event must be a JSON object; got {type(body).__name__}")

    change_type = vocabulary.normalise_change_type(_pick(body, ENVELOPE_FIELDS["change_type"]))
    if change_type is None:
        raise UnknownChangeType(
            "changeType must be one of "
            f"{', '.join(vocabulary.CHANGE_TYPES)}; got {_pick(body, ENVELOPE_FIELDS['change_type'])!r}. "
            "Gap and overflow markers are a different workflow: this one streams record changes."
        )

    transaction_key = _pick(body, ENVELOPE_FIELDS["transaction_key"])
    if not isinstance(transaction_key, str) or not transaction_key.strip():
        raise MissingTransactionKey(
            "transactionKey is required: the room buffers a change under it and only "
            "commits to the replica when the key changes, so an event with no key "
            "cannot be placed."
        )

    payload = _pick(body, _PAYLOAD_ALIASES)
    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise MalformedEvent(
            f"the record payload must be a JSON object; got {type(payload).__name__}"
        )

    enriched = _pick(body, ENVELOPE_FIELDS["enriched_fields"])
    if enriched is None:
        enriched = {}
    if not isinstance(enriched, Mapping):
        raise MalformedEvent(
            f"enriched fields must be a JSON object; got {type(enriched).__name__}"
        )

    changed_fields = _pick(body, ENVELOPE_FIELDS["changed_fields"])
    if changed_fields is None:
        changed = None
    elif isinstance(changed_fields, (list, tuple)):
        changed = [str(name) for name in changed_fields if str(name).strip()]
    else:
        raise MalformedEvent(
            f"changedFields must be a list of CRM field names; got {type(changed_fields).__name__}"
        )

    event: dict[str, Any] = {
        "change_type": change_type,
        "transaction_key": transaction_key.strip(),
        "sequence_number": parse_sequence_number(_pick(body, ENVELOPE_FIELDS["sequence_number"])),
        "commit_timestamp": parse_timestamp(_pick(body, ENVELOPE_FIELDS["commit_timestamp"])),
        # ``None`` and ``[]`` mean different things and are kept apart: no list at
        # all means the transport did not say which fields changed, so the room
        # applies everything the payload carries. An empty list means it said,
        # and the answer was nothing.
        "changed_fields": changed,
        "payload": dict(payload),
        "enriched_fields": dict(enriched),
    }

    entity = _pick(body, ENVELOPE_FIELDS["entity"])
    if entity is not None:
        event["entity"] = str(entity)
    if room_id is not None:
        event["room_id"] = room_id

    wire_format = _pick(body, ENVELOPE_FIELDS["wire_format"])
    if wire_format is not None:
        event["wire_format"] = str(wire_format)
    return event


def applies_to_entity(event: Mapping[str, Any], channel: Mapping[str, Any]) -> None:
    """Refuse an event whose entity the channel does not stream.

    A channel "is a stream of change events that correspond to one or more
    entities", so a subscriber that receives an entity it did not ask for has
    either been sent the wrong channel's events or has mis-declared its channel.
    Either way it is a refusal rather than a silent drop: a dropped change is a
    change the room's replica never learns about, and the research's whole promise
    is that the replica is kept current.
    """
    entities = list(channel.get("entities") or [])
    if not entities:
        return
    entity = event.get("entity")
    if entity is None:
        return
    if str(entity) not in {str(name) for name in entities}:
        raise EntityNotOnChannel(
            f"channel {channel.get('name')!r} streams {sorted(str(e) for e in entities)}; "
            f"this event is for {entity!r}. An event for an entity the channel does not "
            "carry would never reach the room's replica, so it is refused rather than dropped."
        )


def enriched_fields_in_effect(
    event: Mapping[str, Any], channel: Mapping[str, Any]
) -> dict[str, Any]:
    """The enriched fields this event may actually use.

    The research draws the line by change type, not by transport: "Fields that
    you select for enrichment are included in change events for update and delete
    operations. Enriched fields aren't included in change events for create and
    undelete operations because these events contain all the populated fields."

    So a create event that arrives carrying enriched fields has them *dropped
    here*, and the reason is reported alongside. Treating them as authoritative
    would let a stale enrichment overwrite the full field set the create event
    does carry, which is the one outcome the vendor's sentence exists to prevent.
    """
    supplied = dict(event.get("enriched_fields") or {})
    change_type = str(event.get("change_type"))
    if not supplied:
        return {}
    if not vocabulary.is_enriched_change_type(change_type):
        return {}
    selected = {str(name) for name in (channel.get("enriched_fields") or [])}
    return {name: value for name, value in supplied.items() if name in selected}


def enrichment_dropped_reason(event: Mapping[str, Any]) -> str:
    """Why this event's enriched fields were not used, or ``""`` if they were."""
    supplied = dict(event.get("enriched_fields") or {})
    if not supplied:
        return ""
    if not vocabulary.is_enriched_change_type(str(event.get("change_type"))):
        return (
            f"a {event.get('change_type')} event contains all the populated fields, so the "
            f"enriched fields it carried ({', '.join(sorted(supplied))}) were not used"
        )
    return ""
