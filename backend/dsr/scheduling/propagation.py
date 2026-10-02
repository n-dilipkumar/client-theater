"""Step 4: what happens downstream of a reschedule or a cancel.

The research is specific about this half and it is the half that touches other
systems, so it is one module with one function per researched side effect:

* the **calendar event** is moved on a reschedule and cancelled on a cancel;
* the **CRM ``Event``** is updated on a reschedule, and on a cancel it is deleted
  when the Meeting Type's ``Delete Event`` is on and updated when it is off;
* the **webhooks** are pushed - ``BOOKING_RESCHEDULED`` carrying ``rescheduleId``,
  ``rescheduleUid``, ``rescheduleStartTime``, ``rescheduleEndTime``;
  ``BOOKING_CANCELLED`` carrying ``cancellationReason`` and ``cancelledByEmail``;
  ``BOOKING_LOCATION_UPDATED`` when a reschedule also moves the location; and
  Chili Piper's ``Meeting Update``, plus ``type: "Deleted"`` on a cancel;
* the **workflow triggers** ``rescheduleEvent`` and ``eventCancelled`` fire, which
  is what sends the notices and re-bases the reminders - "reminder state is
  recomputed (reminders were relative to the old time)".

The calendar provider and the CRM are the audited store, queried and written the
way those systems would be. That is the same seam WF-041 documents and for the
same reason: the research documents request shapes and no endpoint this product
can reach, and calling a fake HTTP client at a real vendor would be a claim the
code cannot back.

**Every function here only writes, and every write takes a transaction handle.**
That is not a style choice. A reschedule that moved the calendar event and then
failed to write the history row would leave two systems disagreeing about when
the meeting is, and a product whose promise is a complete audit log cannot afford
a second failure mode on top of it. The consequence for callers is that reads
happen before the transaction opens - :class:`~dsr.db.audited.AuditedWriter` is a
write handle, and it is deliberately not a read handle.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping, Sequence

from dsr.scheduling.errors import MeetingChangeError
from dsr.scheduling.timeutil import iso, parse
from dsr.scheduling.vocabulary import (
    BOOKING_CANCELLED,
    BOOKING_LOCATION_UPDATED,
    BOOKING_RESCHEDULED,
    CHANGE_CANCELLED,
    CHANGE_LOCATION_UPDATED,
    CHANGE_RESCHEDULED,
    CHANNEL_EMAIL,
    CHANNEL_SLACK,
    CRM_SOBJECT,
    NOTIFICATION_CHANNEL_NAMES,
    TEMPLATE_CANCELLED,
    TEMPLATE_RESCHEDULED,
)

CALENDAR_EVENT_COLLECTION = "meeting_calendar_event"
CRM_EVENT_COLLECTION = "meeting_crm_event"
WEBHOOK_COLLECTION = "meeting_webhook"
NOTIFICATION_COLLECTION = "meeting_notification"


def calendar_event_id_for(
    booking: Mapping[str, Any], existing: Mapping[str, Any] | None = None
) -> str:
    """The ``event_id`` a meeting's calendar event has, or will have.

    Shared by the two places that need it - the seam that writes the row, and the
    engine that puts the id on a reschedule's new booking - because two
    derivations of the same value would drift, and a booking pointing at a
    different id from the one the event actually carries is a downstream
    subscription that silently stops receiving updates.

    An existing event's id always wins, so a real id from a real provider is never
    overwritten by a derived one.
    """
    if existing is not None and existing["data"].get("event_id"):
        return str(existing["data"]["event_id"])
    booked = str(booking.get("calendar_event_id") or "")
    return booked or f"cal-{booking.get('uid') or ''}"


#: A pushed webhook is recorded as delivered because this product has no
#: downstream transport that could fail. Recorded as a field rather than assumed,
#: so the day a real transport lands the demo shows the difference instead of the
#: history having claimed success all along.
DELIVERED = "delivered"
FAILED = "failed"

#: The Cal workflow trigger each change fires. Sourced: "Cal workflow triggers
#: rescheduleEvent, eventCancelled."
TRIGGER_FOR_CHANGE: dict[str, str] = {
    CHANGE_RESCHEDULED: "rescheduleEvent",
    CHANGE_CANCELLED: "eventCancelled",
    CHANGE_LOCATION_UPDATED: "rescheduleEvent",
}

TEMPLATE_FOR_CHANGE: dict[str, str] = {
    CHANGE_RESCHEDULED: TEMPLATE_RESCHEDULED,
    CHANGE_CANCELLED: TEMPLATE_CANCELLED,
    CHANGE_LOCATION_UPDATED: TEMPLATE_RESCHEDULED,
}

#: Both researched channels, in the order the research names them: "notification
#: channels (email/Slack)".
DEFAULT_CHANNELS: tuple[str, ...] = (CHANNEL_EMAIL, CHANNEL_SLACK)


def default_channels() -> list[str]:
    return list(DEFAULT_CHANNELS)


def channels_for(meeting_type: Mapping[str, Any] | None) -> list[str]:
    """The channels a meeting type notifies on, filtered to the researched set.

    An unknown channel is refused rather than dropped: a team that misspells
    ``slak`` should find out at the point of configuration, not later by
    discovering that nobody was told anything.
    """
    configured = dict(meeting_type or {}).get("notify_channels")
    if configured in (None, "", []):
        return default_channels()
    if not isinstance(configured, (list, tuple)):
        raise MeetingChangeError("notify_channels must be a list of channel names")
    chosen: list[str] = []
    for raw in configured:
        name = str(raw).strip().lower()
        if name not in NOTIFICATION_CHANNEL_NAMES:
            raise MeetingChangeError(
                f"unknown notification channel {raw!r}; the researched channels are "
                + ", ".join(NOTIFICATION_CHANNEL_NAMES)
            )
        if name not in chosen:
            chosen.append(name)
    return chosen or default_channels()


def crm_event_id_for(booking: Mapping[str, Any], existing: Mapping[str, Any] | None = None) -> str:
    """The ``event_id`` a meeting's CRM ``Event`` has, or will have.

    The twin of :func:`calendar_event_id_for`, and for the same reason: the engine
    puts the id on a reschedule's new booking so a downstream reader can follow the
    Event, and two derivations of one value would drift.
    """
    if existing is not None and existing["data"].get("event_id"):
        return str(existing["data"]["event_id"])
    booked = str(booking.get("crm_event_id") or "")
    return booked or f"sf-{booking.get('uid') or ''}"


def crm_status_for(change_type: str) -> str:
    return "active" if change_type != CHANGE_CANCELLED else "cancelled"


def calendar_status_for(change_type: str) -> str:
    return "confirmed" if change_type != CHANGE_CANCELLED else "cancelled"


# --------------------------------------------------------------------------- #
# The calendar provider
# --------------------------------------------------------------------------- #


def move_calendar_event(
    tx: Any,
    *,
    booking: Mapping[str, Any],
    current_uid: str | None,
    change_type: str,
    start_at: str,
    end_at: str,
    change_id: str,
    existing: Mapping[str, Any] | None,
    room_id: str | None,
    actor: str | None,
    source: str,
) -> dict[str, Any]:
    """Move (or cancel) the meeting's calendar event.

    Keyed to the meeting's **chain**, not to whichever booking is current, and that
    is the researched behaviour rather than an implementation detail: "calendar
    event moved/cancelled" describes one event changing time, not a second event
    appearing. Keying by booking would give a meeting moved twice two events, and
    the attendee's calendar would then show the meeting twice.

    So the row is looked up by ``chain_root`` and its ``booking_uid`` is rewritten
    to the booking now current. A downstream system holding the ``event_id`` keeps
    its subscription across any number of moves, which is the practical difference
    between "the event moved" and "a new event appeared".

    A booking with no calendar event gets one, because "moved" presupposes an
    event. A booking that never produced one is a configuration gap, and writing
    the row anyway is what makes the gap visible instead of leaving the calendar
    silently out of step with the room.
    """
    # `current_uid` is the booking this change leaves in force, which on a reschedule
    # is the *new* one. It is passed separately from `booking`, which carries the
    # meeting's chain: the row is identified by the chain and points at whoever is
    # current, so a rep looking the event up from the meeting in front of them
    # finds it. Collapsing the two would leave the row naming a superseded booking.
    uid = str(current_uid or booking.get("uid") or "")
    chain = str(booking.get("chain_root") or uid)
    payload = {
        "booking_uid": uid,
        "chain_root": chain,
        "provider": booking.get("calendar_provider") or "google",
        "event_id": calendar_event_id_for(booking, existing),
        "start_at": iso(start_at),
        "end_at": iso(end_at),
        "status": calendar_status_for(change_type),
        "last_change_id": change_id,
    }
    if existing is not None:
        tx.update(existing["id"], payload, actor=actor, source=source)
        return {"action": "updated", "event_id": payload["event_id"], "record_id": existing["id"]}
    created = tx.create(
        CALENDAR_EVENT_COLLECTION, payload, room_id=room_id, actor=actor, source=source
    )
    return {"action": "created", "event_id": payload["event_id"], "record_id": created["id"]}


# --------------------------------------------------------------------------- #
# The CRM Event
# --------------------------------------------------------------------------- #


def propagate_crm_event(
    tx: Any,
    *,
    booking: Mapping[str, Any],
    current_uid: str | None,
    change_type: str,
    delete_event: bool,
    start_at: str,
    end_at: str,
    change_id: str,
    existing: Mapping[str, Any] | None,
    room_id: str | None,
    actor: str | None,
    source: str,
) -> dict[str, Any]:
    """Update the CRM ``Event`` on a reschedule; delete or update it on a cancel.

    The researched toggle decides which: "You can define if the Salesforce Event
    will be deleted if the meeting is canceled from the Dashboard or deleted from
    your calendar provider." With the toggle on, a cancelled meeting's Event is
    soft-deleted, so the audit log keeps the row and the CRM stops showing a
    meeting nobody is attending. With it off, the Event is updated to
    ``cancelled`` and kept - the configuration a team picks when the CRM calendar
    is the record of what was agreed, which is exactly why it is a setting.
    """
    # The booking this change leaves in force, and the chain that identifies the
    # meeting. Separate for the same reason as the calendar event: on a reschedule
    # the chain is the old booking's and the current uid is the new one's.
    uid = str(current_uid or booking.get("uid") or "")
    chain = str(booking.get("chain_root") or uid)
    base = {
        "booking_uid": uid,
        # The chain, for the same reason as the calendar event: one CRM Event per
        # meeting, updated as the meeting moves. A row keyed on a superseded uid
        # would be a second Event on the next move.
        "chain_root": chain,
        "sobject": CRM_SOBJECT,
        "event_id": crm_event_id_for(booking, existing),
        "subject": booking.get("title") or "Meeting",
        "start_at": iso(start_at),
        "end_at": iso(end_at),
        "last_change_id": change_id,
    }

    if change_type == CHANGE_CANCELLED and delete_event:
        if existing is None:
            # Says so rather than creating an Event to delete. Minting one and
            # immediately removing it would leave an audit row for a write that
            # undid itself, which is exactly the kind of noise that makes an
            # audit log hard to read.
            return {
                "action": "skipped",
                "sobject": CRM_SOBJECT,
                "event_id": None,
                "reason": "no CRM Event existed to delete",
            }
        # Recorded as a lifecycle state rather than a store delete, and the reason
        # is the transaction handle: `AuditedWriter` offers create and update and
        # deliberately no delete, so a "delete" here would have to be a second,
        # separate transaction. That is exactly the half-propagation this module
        # exists to prevent - a CRM event left on somebody's calendar because the
        # deletion could not join the same commit as the cancellation that
        # justified it. `status: deleted` is atomic with everything else, is the
        # same fact, and the row stays readable, which is what the audit promise
        # needs anyway.
        payload = {**base, "status": "deleted", "deleted_at": None}
        tx.update(existing["id"], payload, actor=actor, source=source)
        return {
            "action": "deleted",
            "sobject": CRM_SOBJECT,
            "record_id": existing["id"],
            "event_id": base["event_id"],
            "because": "Delete Event is on for this meeting type",
            "recorded_as": "status=deleted on the CRM Event row, inside this transaction",
        }

    payload = {**base, "status": crm_status_for(change_type)}
    if existing is not None:
        tx.update(existing["id"], payload, actor=actor, source=source)
        return {
            "action": "updated",
            "sobject": CRM_SOBJECT,
            "record_id": existing["id"],
            "event_id": payload["event_id"],
        }
    created = tx.create(CRM_EVENT_COLLECTION, payload, room_id=room_id, actor=actor, source=source)
    return {
        "action": "created",
        "sobject": CRM_SOBJECT,
        "record_id": created["id"],
        "event_id": payload["event_id"],
    }


# --------------------------------------------------------------------------- #
# Webhooks
# --------------------------------------------------------------------------- #


def webhook_envelopes(
    *,
    change_type: str,
    new_booking: Mapping[str, Any] | None,
    old_booking: Mapping[str, Any],
    reschedule_id: int | None,
    cancellation_reason: str | None,
    cancelled_by_email: str | None,
    location: str | None,
) -> list[dict[str, Any]]:
    """The researched payloads, built from the researched field names.

    Both payload shapes are quoted in the research, so they are reproduced with
    the documented field names - ``rescheduleId`` / ``rescheduleUid`` /
    ``rescheduleStartTime`` / ``rescheduleEndTime`` and ``cancellationReason`` /
    ``cancelledByEmail`` - and Chili Piper's side gets ``Meeting Update`` with
    ``type: "Deleted"`` on a cancel, which is the string the research gives it.

    ``BOOKING_LOCATION_UPDATED`` is emitted only when the reschedule actually moved
    the location, which is the condition its name states. Emitting it on every
    reschedule would be a webhook whose payload says nothing changed.
    """
    old_uid = str(old_booking.get("uid") or "")
    envelopes: list[dict[str, Any]] = []

    if change_type == CHANGE_RESCHEDULED:
        new_uid = str((new_booking or {}).get("uid") or "")
        envelopes.append(
            {
                "webhook": BOOKING_RESCHEDULED,
                "change_type": change_type,
                "payload": {
                    "uid": new_uid,
                    "rescheduleId": reschedule_id,
                    "rescheduleUid": old_uid,
                    "rescheduleStartTime": (new_booking or {}).get("start_at"),
                    "rescheduleEndTime": (new_booking or {}).get("end_at"),
                },
            }
        )
        if location and location != old_booking.get("location"):
            envelopes.append(
                {
                    "webhook": BOOKING_LOCATION_UPDATED,
                    "change_type": change_type,
                    "payload": {"uid": new_uid, "location": location, "rescheduleUid": old_uid},
                }
            )
        envelopes.append(
            {
                "webhook": "Meeting Update",
                "change_type": change_type,
                "payload": {
                    "uid": new_uid,
                    "booking_uid": old_uid,
                    "start_at": (new_booking or {}).get("start_at"),
                    "end_at": (new_booking or {}).get("end_at"),
                },
            }
        )
        return envelopes

    if change_type == CHANGE_CANCELLED:
        envelopes.append(
            {
                "webhook": BOOKING_CANCELLED,
                "change_type": change_type,
                "payload": {
                    "uid": old_uid,
                    "cancellationReason": cancellation_reason,
                    "cancelledByEmail": cancelled_by_email,
                },
            }
        )
        envelopes.append(
            {
                "webhook": "Meeting Update",
                "change_type": change_type,
                "payload": {"uid": old_uid, "type": "Deleted"},
            }
        )
        return envelopes

    return envelopes


def record_webhooks(
    tx: Any,
    envelopes: Sequence[Mapping[str, Any]],
    *,
    booking_uid: str,
    change_id: str,
    room_id: str | None,
    actor: str | None,
    source: str,
    delivered: bool = True,
) -> list[dict[str, Any]]:
    """Write the fan-out as rows, so the pushed payloads are readable back."""
    written: list[dict[str, Any]] = []
    for envelope in envelopes:
        record = tx.create(
            WEBHOOK_COLLECTION,
            {
                "webhook": envelope.get("webhook"),
                "booking_uid": booking_uid,
                "change_id": change_id,
                "change_type": envelope.get("change_type"),
                "payload": envelope.get("payload"),
                "status": DELIVERED if delivered else FAILED,
                "attempt": 1,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        written.append({"id": record["id"], "webhook": record["data"]["webhook"]})
    return written


# --------------------------------------------------------------------------- #
# Reminders
# --------------------------------------------------------------------------- #


def rebase_reminders(reminders: Any, new_start: Any) -> list[dict[str, Any]]:
    """Move every reminder onto the new time, keeping its offset.

    "Reminder state is recomputed (reminders were relative to the old time)." The
    reminders are stored as ``{offset_minutes, scheduled_for}`` pairs, so
    re-basing is arithmetic on the offset and **the offset is preserved**: a "one
    hour before" reminder stays a one-hour-before reminder. Recomputing the
    absolute time and leaving the offset behind would silently turn every reminder
    into a reminder for the new start, which is not what anyone who set "15
    minutes before" meant.
    """
    start = parse(new_start)
    rebased: list[dict[str, Any]] = []
    for entry in reminders or []:
        if not isinstance(entry, Mapping):
            continue
        item = dict(entry)
        try:
            offset_minutes = int(item.get("offset_minutes"))
        except (TypeError, ValueError):
            offset_minutes = 0
        item["offset_minutes"] = offset_minutes
        item["scheduled_for"] = iso(start - timedelta(minutes=offset_minutes))
        item["status"] = "scheduled"
        item["rebased_from"] = entry.get("scheduled_for")
        rebased.append(item)
    return rebased


def drop_reminders(reminders: Any) -> list[dict[str, Any]]:
    """Mark every reminder dropped, keeping it on the record.

    A cancelled meeting's reminders are not deleted: the history says a reminder
    was set and then dropped with the booking, which is what makes the Events
    History row reconstructable afterwards.
    """
    dropped: list[dict[str, Any]] = []
    for entry in reminders or []:
        if not isinstance(entry, Mapping):
            continue
        item = dict(entry)
        item["status"] = "dropped"
        item["dropped_from"] = item.get("scheduled_for")
        dropped.append(item)
    return dropped


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #


def record_notifications(
    tx: Any,
    *,
    change_type: str,
    booking: Mapping[str, Any],
    change_id: str,
    channels: Sequence[str],
    room_id: str | None,
    actor: str | None,
    source: str,
    at: str,
) -> list[dict[str, Any]]:
    """One notice per channel, to the attendee and the host.

    The researched trigger is what sends them - "``rescheduleEvent`` /
    ``eventCancelled`` workflow triggers fire re-sends and notifications" - so the
    trigger is recorded on the change row and the notices hang off it. The
    ``reschedule_requested`` change has no researched template, so it sends
    nothing here; the attendee's link to complete it is the researched artefact
    and it is written as a reschedule request instead.
    """
    template = TEMPLATE_FOR_CHANGE.get(change_type)
    if not template:
        return []
    recipients = (("attendee", booking.get("attendee_email")), ("host", booking.get("host_email")))
    written: list[dict[str, Any]] = []
    for channel in channels:
        for role, address in recipients:
            if not address:
                continue
            record = tx.create(
                NOTIFICATION_COLLECTION,
                {
                    "channel": channel,
                    "template": template,
                    "trigger": TRIGGER_FOR_CHANGE.get(change_type),
                    "recipient": str(address).strip().lower(),
                    "recipient_role": role,
                    "booking_uid": booking.get("uid"),
                    "change_id": change_id,
                    "status": DELIVERED,
                    "at": at,
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
            written.append({"id": record["id"], "channel": channel, "recipient_role": role})
    return written


__all__ = [
    "CALENDAR_EVENT_COLLECTION",
    "CRM_EVENT_COLLECTION",
    "DEFAULT_CHANNELS",
    "DELIVERED",
    "FAILED",
    "NOTIFICATION_COLLECTION",
    "TEMPLATE_FOR_CHANGE",
    "TRIGGER_FOR_CHANGE",
    "WEBHOOK_COLLECTION",
    "calendar_status_for",
    "channels_for",
    "crm_status_for",
    "default_channels",
    "drop_reminders",
    "move_calendar_event",
    "propagate_crm_event",
    "rebase_reminders",
    "record_notifications",
    "record_webhooks",
    "webhook_envelopes",
]
