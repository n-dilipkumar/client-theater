"""The two webhooks a reassignment fires, and the payload each carries.

The research names both, from two different vendors, and they are not
interchangeable:

``Meeting Update``
    Chili Piper's ``For Meeting Update`` custom webhook, "Triggered whenever a
    meeting is updated, for example if it's reassigned or rescheduled". It
    carries ``type: "Updated"`` and fires for *any* update, so it fires for
    every reassignment this workflow performs.

``BOOKING_REASSIGNED``
    Cal's, and the scope is narrower and load-bearing: it "Fires when a
    round-robin booking's host is reassigned (automatic or manual)". A
    non-round-robin reassignment therefore fires ``Meeting Update`` only.
    Getting this backwards would tell a CRM to re-point ownership for a booking
    whose host rotation was never in play, and a CRM that trusts
    ``BOOKING_REASSIGNED`` acts on it.

The payload keys are the researched ones: ``addedHostUserIds`` lists "the user
IDs of the host(s) newly assigned to the booking", ``removedHostUserIds`` lists
"the user IDs of the host(s) removed", and ``organizer`` "reflects the new
host" - which is why ``organizer`` is built from the *new* host and the removed
host appears only under ``removedHostUserIds``.

These payloads are built and stored on the reassignment record. They are not
delivered anywhere: the research documents no endpoint this product can reach,
and the delivery machinery is WF-046's and WF-047's, not this workflow's. What
is this workflow's is knowing exactly what it *would* send, and a reader being
able to check that against the vendor docs.
"""

from __future__ import annotations

from typing import Any

from dsr.reassign.vocabulary import (
    BOOKING_REASSIGNED_SCOPE,
    MEETING_UPDATE_WEBHOOK,
    REASSIGNED_PAYLOAD_KEYS,
    invite_for,
)

#: Chili Piper's custom webhook name, and the ``type`` its payload carries.
MEETING_UPDATE = "Meeting Update"
MEETING_UPDATE_TYPE = "Updated"

#: Cal's webhook name.
BOOKING_REASSIGNED = "BOOKING_REASSIGNED"

#: The payload keys Cal adds to a reassigned booking, published so a reviewer
#: can check them against the webhook guide without reading the code.
REASSIGNED_ONLY_KEYS: tuple[str, ...] = ("addedHostUserIds", "removedHostUserIds", "organizer")

#: Cal's webhook payload version. The v2 API reference and the webhook guide are
#: the v2 shape, and ``organizer`` is an object there rather than the string the
#: older guide shows, so the version is stated in the payload rather than left
#: to be inferred.
PAYLOAD_VERSION = 2


def events_for(meeting: dict[str, Any]) -> list[str]:
    """Which webhooks this meeting's reassignment fires.

    ``Meeting Update`` always, because a reassignment is a meeting update by
    the webhook's own description. ``BOOKING_REASSIGNED`` only for a round-robin
    booking, because that is the scope Cal documents for it.

    Ordered, so the stored record and the page list them the same way every
    time rather than in whatever order two ``if`` statements happen to run.
    """
    events = [MEETING_UPDATE]
    if bool(meeting.get("round_robin")):
        events.append(BOOKING_REASSIGNED)
    return events


def meeting_update_payload(
    meeting: dict[str, Any],
    host: dict[str, Any],
    *,
    reassignment: dict[str, Any],
    at: str,
) -> dict[str, Any]:
    """Chili Piper's ``For Meeting Update`` body, with ``type: "Updated"``.

    The meeting block is the *new* meeting: the assignee, the slot, and the
    invite as it now stands. A consumer that only reads this payload sees the
    meeting after the change, which is what "Triggered whenever a meeting is
    updated" describes.
    """
    return {
        "event": MEETING_UPDATE,
        "webhook": "for_meeting_update",
        "trigger": MEETING_UPDATE_WEBHOOK,
        "type": MEETING_UPDATE_TYPE,
        "occurred_at": at,
        "reassignment": {
            "id": reassignment.get("id"),
            "from_host_id": reassignment.get("from_host_id"),
            "to_host_id": reassignment.get("to_host_id"),
            "surface": reassignment.get("surface"),
            "requested_by": reassignment.get("requested_by"),
        },
        "meeting": {
            "id": meeting.get("id"),
            "booking_uid": meeting.get("booking_uid"),
            "title": meeting.get("title"),
            "meeting_type": meeting.get("meeting_type"),
            "workspace": meeting.get("workspace"),
            "distribution": meeting.get("distribution"),
            "team": meeting.get("team"),
            "booker": meeting.get("booker"),
            "product_source": meeting.get("product_source"),
            "status": meeting.get("status"),
            "round_robin": bool(meeting.get("round_robin")),
            "starts_at": meeting.get("starts_at"),
            "ends_at": meeting.get("ends_at"),
            "host": {"id": host.get("id"), "name": host.get("name"), "email": host.get("email")},
            "invite": invite_for(host),
        },
    }


def booking_reassigned_payload(
    meeting: dict[str, Any],
    host: dict[str, Any],
    previous_host: dict[str, Any],
    *,
    reassignment: dict[str, Any],
    booking_uid: str,
    at: str,
) -> dict[str, Any]:
    """Cal's ``BOOKING_REASSIGNED`` body.

    ``organizer`` is the new host - "``organizer`` reflects the new host" - and
    the host that lost the booking appears under ``removedHostUserIds`` and
    nowhere else, so a consumer replacing ownership has exactly one id to
    remove and it is named rather than inferred.
    """
    return {
        "event": BOOKING_REASSIGNED,
        "webhook": "booking_reassigned",
        "trigger": BOOKING_REASSIGNED_SCOPE,
        "payloadVersion": PAYLOAD_VERSION,
        "type": MEETING_UPDATE_TYPE,
        "occurred_at": at,
        "bookingUid": booking_uid,
        "previousBookingUid": meeting.get("booking_uid"),
        "organizer": {
            "id": host.get("id"),
            "name": host.get("name"),
            "email": host.get("email"),
            "username": host.get("username") or host.get("email"),
        },
        "addedHostUserIds": [host["id"]] if host.get("id") else [],
        "removedHostUserIds": [previous_host["id"]] if previous_host.get("id") else [],
        "meetingType": {"id": meeting.get("meeting_type"), "slug": meeting.get("meeting_type")},
        "startTime": meeting.get("starts_at"),
        "endTime": meeting.get("ends_at"),
        "reassignedBy": reassignment.get("requested_by"),
        "reassignmentSource": reassignment.get("surface"),
    }


def webhook_catalogue() -> dict[str, Any]:
    """Both webhooks, what fires them, and the keys each adds.

    Served at ``/api/wf-063/webhooks`` so the page can show a reviewer what this
    workflow emits without a reviewer reading the source.
    """
    return {
        "count": 2,
        "payload_version": PAYLOAD_VERSION,
        "reassigned_only_keys": list(REASSIGNED_ONLY_KEYS),
        "events": [
            {
                "event": MEETING_UPDATE,
                "vendor": "chili_piper",
                "webhook": "for_meeting_update",
                "fires_when": "any meeting update, including a reassignment or a reschedule",
                "scope": "every reassignment",
                "type": MEETING_UPDATE_TYPE,
                "quoted": MEETING_UPDATE_WEBHOOK,
            },
            {
                "event": BOOKING_REASSIGNED,
                "vendor": "cal",
                "webhook": "booking_reassigned",
                "fires_when": "a round-robin booking's host is reassigned, automatic or manual",
                "scope": "round-robin bookings only",
                "type": MEETING_UPDATE_TYPE,
                "adds": list(REASSIGNED_ONLY_KEYS),
                "quoted": BOOKING_REASSIGNED_SCOPE,
                "adds_quoted": REASSIGNED_PAYLOAD_KEYS,
            },
        ],
    }
