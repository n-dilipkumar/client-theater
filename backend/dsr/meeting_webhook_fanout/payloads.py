"""The flat Chili Piper envelope, and the exact bytes that get signed.

Two things live here and the second is the one that matters.

**The envelope is flat.** Every documented Chili Piper field is at the top level
and there is no ``payload`` wrapper. The research quotes the field list as
``meetingIdChili``, title and description and location, start and end,
``primaryGuestTimeZone``, the host and assignee and booker identities and their
``*IdChili``, ``primaryGuestDataFields``, ``additionalGuests[]``,
``workspaceId/Name``, ``productFeatureType/Name/Id``, ``distributionName/Id``,
``meetingTypeName/Id``, and ``type: Created|Updated|Deleted``. All top level.

The research also quotes a *second*, wrapped shape from the Cal side, and warns
that ``MEETING_STARTED`` and ``MEETING_ENDED`` are exceptions to it. This build
fires none of Cal's events, so it has no occasion to mix the two. Jev chose the
flat envelope for exactly that reason, at confidence 0.96, audit
``jev-20261004T070110-25984-70596``. Reproducing Cal's wrapper as well would put
three shapes in one product, and the research's own warning about mixing them
silently would become a real risk.

**The bytes are built once and signed once.** :func:`canonical_bytes` serialises
the payload and returns those exact bytes, and the same bytes are what the
signature covers and what the transport sends. That is not tidiness, it is the
whole contract: the research records a signature mismatch as *"Ensure you're
verifying against the raw request body, not a re-serialised/parsed JSON
object."* A sender that signed one serialisation and posted another would
produce a delivery every subscriber refuses, and the refusal would look like the
subscriber's bug.

``sort_keys=True`` so two identical payloads serialise identically. That is what
lets a test compare a recorded delivery against a rebuilt one, and lets a
subscriber cache and replay a body it has already seen.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.meeting_webhook_fanout.vocabulary import (
    PAYLOAD_FIELDS,
    event_type_for_payload,
    payload_type_for_event_type,
)

__all__ = [
    "build_payload",
    "canonical_bytes",
    "event_type_for_payload",
    "missing_fields",
    "payload_type_for_event_type",
    "signable_payload",
]


def _isoformat(value: Any) -> str | None:
    """An ISO 8601 string, from a datetime or from a string already in one."""
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return moment.isoformat()
    return str(value)


def build_payload(meeting: Mapping[str, Any], event_type: str) -> dict[str, Any]:
    """The flat meeting payload for one subscription type.

    ``meeting`` is ordinary JSON, read from ``records.data``. Every documented
    field is taken when present and omitted when not, rather than sent as
    ``null``: a field this room does not hold is a field the subscriber cannot
    act on, and a payload full of nulls reads as a room that holds nothing.

    The two keys ``type`` and ``meetingIdChili`` are the only ones the room
    synthesises. ``type`` is the join to the subscription type
    (:data:`~dsr.meeting_webhook_fanout.vocabulary.EVENT_TYPES`), and
    ``meetingIdChili`` falls back to the record's own id when the meeting row has
    no vendor id, because a payload with no meeting identity is not a payload.
    """
    payload_type = payload_type_for_event_type(event_type)
    payload: dict[str, Any] = {"type": payload_type}

    values: dict[str, Any] = {
        "meetingIdChili": meeting.get("meetingIdChili")
        or meeting.get("meeting_id_chili")
        or meeting.get("id"),
        "title": meeting.get("title") or meeting.get("name"),
        "description": meeting.get("description"),
        "location": meeting.get("location"),
        "start": _isoformat(meeting.get("start")),
        "end": _isoformat(meeting.get("end")),
        "primaryGuestTimeZone": meeting.get("primaryGuestTimeZone")
        or meeting.get("timezone")
        or meeting.get("time_zone"),
        "primaryGuestName": meeting.get("primaryGuestName") or meeting.get("guest_name"),
        "primaryGuestEmail": meeting.get("primaryGuestEmail") or meeting.get("guest_email"),
        "primaryGuestIdChili": meeting.get("primaryGuestIdChili") or meeting.get("guest_id_chili"),
        "primaryGuestDataFields": meeting.get("primaryGuestDataFields")
        or meeting.get("guest_data_fields"),
        "hostIdChili": meeting.get("hostIdChili") or meeting.get("host_id_chili"),
        "hostName": meeting.get("hostName") or meeting.get("host_name"),
        "assigneeIdChili": meeting.get("assigneeIdChili") or meeting.get("assignee_id_chili"),
        "assigneeName": meeting.get("assigneeName") or meeting.get("assignee_name"),
        "bookerIdChili": meeting.get("bookerIdChili") or meeting.get("booker_id_chili"),
        "bookerName": meeting.get("bookerName") or meeting.get("booker_name"),
        "additionalGuests": meeting.get("additionalGuests") or meeting.get("additional_guests"),
        "workspaceId": meeting.get("workspaceId") or meeting.get("workspace_id"),
        "workspaceName": meeting.get("workspaceName") or meeting.get("workspace_name"),
        "productFeatureType": meeting.get("productFeatureType"),
        "productFeatureName": meeting.get("productFeatureName"),
        "productFeatureId": meeting.get("productFeatureId"),
        "distributionName": meeting.get("distributionName") or meeting.get("distribution_name"),
        "distributionId": meeting.get("distributionId") or meeting.get("distribution_id"),
        "meetingTypeName": meeting.get("meetingTypeName") or meeting.get("meeting_type_name"),
        "meetingTypeId": meeting.get("meetingTypeId") or meeting.get("meeting_type_id"),
    }

    for key, value in values.items():
        if value is not None:
            payload[key] = value

    if not payload.get("meetingIdChili"):
        raise ValueError("a meeting payload needs a meeting identity")
    return payload


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    """The exact bytes that get signed and the exact bytes that get sent.

    UTF-8, keys sorted, no added whitespace. One serialisation, computed once by
    the caller, so the signature and the request cannot drift apart.
    """
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")


def missing_fields(payload: Mapping[str, Any]) -> list[str]:
    """Which documented fields this payload left out.

    Served on the event row and in the sample response, so a reader comparing a
    delivery against the contract can see what the room did not hold rather than
    inferring it from a null.
    """
    return [name for name in PAYLOAD_FIELDS if name not in payload]


def signable_payload(payload: Mapping[str, Any]) -> str:
    """The body as text, which is what the signature is computed over.

    Decoded from :func:`canonical_bytes` rather than re-serialised, so the string
    a signature covers is the same string by construction.
    """
    return canonical_bytes(payload).decode("utf-8")
