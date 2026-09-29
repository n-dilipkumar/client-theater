"""The engine: WF-064's researched user flow, in order, over the audited store.

Steps 1 to 5 of ``WF-064.md``, and the whole of them:

    1. An attendee opens the reschedule/cancel URL embedded in the invite body
       (``CP.Meeting.RescheduleUrl`` / ``CP.Meeting.CancelUrl``), or the host uses
       the Meetings Activity panel.
    2. For a reschedule, the same Distribution/Meeting Type context re-opens and
       availability is recomputed - with the booking's own slot released, because
       ``bookingUidToReschedule`` "will ensure that the original booking time
       appears within the returned available slots".
    3. For a cancel, an optional cancellation reason is captured; the meeting is
       released and, per the Meeting Type's ``Delete Event``, the CRM ``Event`` is
       deleted too.
    4. Downstream: calendar event moved or cancelled, ``Delete Event`` applied,
       the webhooks pushed, an Events History row written.
    5. The host-side equivalents: an immediate reschedule, a request to reschedule
       (which cancels the booking and mails the attendee a link), and a cancel
       that takes one recurrence or all of them.

Four constraints are worth stating outright, because they are constraints rather
than preferences:

**``source`` is a required keyword on every write.** The audit row must name the
route that served it, so each route builds its source from ``router.prefix`` and
passes it down. A domain function that hardcoded a URL string would let the audit
log name a path the app had stopped serving - that defect has shipped in this
codebase before, so the parameter is required rather than optional.

**The whole propagation is one transaction.** A reschedule touches the old
booking, a new booking, a calendar event, a CRM event, the history row, the
webhooks and the reminders. If the sixth of those failed after the first five
landed, the calendar and the CRM would disagree about when the meeting is, and
the audit log would describe a change that did not happen. Reads happen first,
because :class:`~dsr.db.audited.AuditedWriter` is a write handle and is
deliberately not a read handle.

**The calendar and the CRM are the audited store.** See
:mod:`dsr.scheduling.propagation` for why. Everything above that seam is the
researched part, so a real transport is a change to one place.

**A refused change writes nothing at all.** No half-cancelled series, no change
row for an operation that did not happen, no CRM event for a meeting that is still
on the calendar. The one thing a refusal does produce is an error saying why.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from dsr.db.audited import RecordNotFound
from dsr.scheduling import links as link_tools
from dsr.scheduling import propagation
from dsr.scheduling.availability import (
    DEFAULT_RANGE_DAYS,
    available_slots,
    explain_missing,
    find_slot,
    slot_end_for,
)
from dsr.scheduling.errors import (
    BookingConflict,
    MeetingChangeError,
    MeetingNotFound,
)
from dsr.scheduling.links import LinkState
from dsr.scheduling.meeting_types import (
    MAX_RESCHEDULE_HORIZON_DAYS,
    delete_event,
    normalise_meeting_type,
    window_for,
)
from dsr.scheduling.timeutil import iso, parse, utcnow
from dsr.scheduling.vocabulary import (
    ATTENDEE,
    BOOKED,
    CANCEL,
    CANCELLED,
    CHANGE_CANCELLED,
    CHANGE_RESCHEDULE_REQUESTED,
    CHANGE_RESCHEDULED,
    CHILICAL_HOME,
    CRM_SOBJECT,
    HOST,
    LIVE_STATUSES,
    RESCHEDULE,
    RESCHEDULE_LINK,
    REQUEST_RESCHEDULE,
    RESCHEDULED,
    SCOPE_ALL,
    SCOPE_THIS,
    require_actor_kind,
    require_cancel_scope,
    require_change_type,
    require_intent,
    require_reschedule_source,
)
from dsr.store import RecordStore

MEETING_TYPE_COLLECTION = "meeting_type"
BOOKING_COLLECTION = "booking"
CHANGE_COLLECTION = "meeting_change"
REQUEST_COLLECTION = "meeting_reschedule_request"

#: A reschedule is one transaction, and the change row is written first inside it
#: so the downstream rows can point at it. The reason for the order: the history
#: row is the record of the change, and a webhook that cannot name the change it
#: belongs to is a webhook nobody can trace back.
MAX_SERIES_SWEEP = 200

#: How far either side of a requested time the availability is computed, so a
#: refusal can name a genuinely open slot rather than saying the search range ran
#: out. A week, because the shortest gap a weekday-only meeting type can produce
#: is a weekend.
RESCHEDULE_SEARCH_DAYS = 4


def default_uid() -> str:
    """A booking uid, in Cal's ``uid`` shape without the Cal prefix."""
    return f"bk_{secrets.token_hex(6)}"


def default_token() -> str:
    return link_tools.mint_token()


def _utcnow() -> datetime:
    return utcnow()


def _type_data(meeting_type: Mapping[str, Any] | None) -> dict[str, Any]:
    """A Meeting Type as its stored payload.

    Accepts either the full record the store returns or the bare payload, because
    the two reach this function from different callers - :meth:`resolve_meeting_type`
    hands back a record, while a caller holding only ``{"host_email": ...}`` from a
    query string has no envelope at all. Guessing wrong would produce availability
    for a host named ``"data"``, which is the kind of bug that reads as a data
    problem rather than a shape problem.
    """
    body = dict(meeting_type or {})
    inner = body.get("data")
    return dict(inner) if isinstance(inner, Mapping) else body


class MeetingChangeEngine:
    """Reschedule, request a reschedule, and cancel - over the audited store.

    Constructed per request from ``StoreDep``, as WF-016 and WF-041 both do. The
    engine holds the store, a clock and three id factories and nothing else, so
    per-request construction is equivalent and every one of those is overridable
    in a test without a shared file changing.
    """

    def __init__(
        self,
        store: RecordStore,
        *,
        clock: Callable[[], datetime] | None = None,
        uid_factory: Callable[[], str] | None = None,
        token_factory: Callable[[], str] | None = None,
        link_base: str = "",
        link_path: str = link_tools.LINK_PATH,
    ) -> None:
        self.store = store
        self.clock = clock if clock is not None else _utcnow
        self.uid_factory = uid_factory if uid_factory is not None else default_uid
        self.token_factory = token_factory if token_factory is not None else default_token
        self.link_base = link_base
        self.link_path = link_path

    # ----------------------------------------------------------------- #
    # Meeting Types
    # ----------------------------------------------------------------- #

    def create_meeting_type(
        self, spec: Mapping[str, Any], *, room_id: str | None = None, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Declare a Meeting Type: the context a reschedule re-opens.

        Validated before the row exists, so a Meeting Type with an unreadable
        hour or an impossible meeting length cannot leave a half-configured
        context behind for a reschedule to re-open later.
        """
        payload = normalise_meeting_type(spec)
        return self.store.create(
            MEETING_TYPE_COLLECTION, payload, room_id=room_id, actor=actor, source=source
        )

    def get_meeting_type(self, meeting_type_id: str) -> dict[str, Any] | None:
        return self.store.get(meeting_type_id)

    def list_meeting_types(
        self, *, room_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        records = self.store.list(MEETING_TYPE_COLLECTION, limit=limit)
        if room_id is not None:
            records = [record for record in records if record.get("room_id") == room_id]
        return records

    def update_meeting_type(
        self, meeting_type_id: str, patch: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Patch a Meeting Type, re-validating the merged result.

        A partial patch merged onto the stored type and re-run through the same
        validation a create goes through, so a patch cannot leave a Meeting Type
        whose ``hours`` a slot computation will later fail to read.
        """
        current = self._require_meeting_type(meeting_type_id)
        merged = {**current["data"], **dict(patch or {})}
        # `normalise_meeting_type` re-validates the pass-through configuration
        # fields as well as the structural ones, so a patch cannot leave a Meeting
        # Type naming a channel or a reminder offset the propagation code would
        # then have to defend against at the moment somebody cancels a meeting.
        payload = normalise_meeting_type(merged)
        return self.store.update(meeting_type_id, payload, actor=actor, source=source)

    def delete_meeting_type(
        self, meeting_type_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Soft-delete a Meeting Type. Its bookings and history stay readable."""
        self._require_meeting_type(meeting_type_id)
        return self.store.delete(meeting_type_id, actor=actor, source=source)

    def _require_meeting_type(self, meeting_type_id: str) -> dict[str, Any]:
        record = self.store.get(meeting_type_id)
        if record is None or record.get("collection") != MEETING_TYPE_COLLECTION:
            raise MeetingNotFound(f"meeting type {meeting_type_id} not found")
        return record

    def default_meeting_type(self) -> dict[str, Any]:
        """The context a booking with no Meeting Type resolves to.

        A resolved shape with no ``id``, not a stored row: a booking registered
        with a bare host and a length is a legitimate thing to do, and refusing it
        would make the Meeting Type a prerequisite for the simplest possible
        booking.
        """
        return {
            "id": None,
            "data": normalise_meeting_type({"name": "Ad hoc", "host_email": "host@example.invalid"}),
        }

    def resolve_meeting_type(self, meeting_type_id: str | None) -> dict[str, Any]:
        if not meeting_type_id:
            return self.default_meeting_type()
        return self._require_meeting_type(meeting_type_id)

    # ----------------------------------------------------------------- #
    # Bookings
    # ----------------------------------------------------------------- #

    def create_booking(
        self,
        spec: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Register a booked meeting: the "old booking record" the flow starts from.

        A booking is the input to this workflow rather than its output, so it is
        created here over HTTP. That is what makes the feature demonstrable end to
        end against a real database rather than only against seeded rows, and it
        is the same shape a booking webhook from Cal would hand this product.
        """
        body = dict(spec or {})
        meeting_type = self.resolve_meeting_type(body.get("meeting_type_id"))
        type_data = dict(meeting_type["data"])

        uid = str(body.get("uid") or "").strip() or self.uid_factory()
        if self.store.find(BOOKING_COLLECTION, {"uid": uid}, limit=1):
            raise MeetingChangeError(f"a booking with uid {uid} already exists")

        start_at = parse(body.get("start_at"), label="start_at")
        duration = int(body.get("duration_minutes") or type_data["duration_minutes"])
        end_at = parse(body["end_at"]) if body.get("end_at") else start_at + timedelta(minutes=duration)

        host_email = str(body.get("host_email") or type_data["host_email"]).strip().lower()
        tokens = link_tools.mint_pair(uid, factory=self.token_factory)

        payload: dict[str, Any] = {
            "uid": uid,
            "meeting_type_id": meeting_type.get("id"),
            "title": str(body.get("title") or type_data.get("name") or "Meeting"),
            "host_email": host_email,
            "attendee_name": str(body.get("attendee_name") or "").strip(),
            "attendee_email": str(body.get("attendee_email") or "").strip().lower(),
            "start_at": iso(start_at),
            "end_at": iso(end_at),
            "duration_minutes": duration,
            "timezone": str(body.get("timezone") or type_data.get("timezone") or "UTC"),
            "location": str(body.get("location") or "").strip() or None,
            "calendar_provider": str(
                body.get("calendar_provider") or type_data.get("calendar_provider") or "google"
            ),
            "status": BOOKED,
            "chain_root": uid,
            "reschedule_token": tokens[RESCHEDULE],
            "cancel_token": tokens[CANCEL],
            "reminders": self._reminders_for(body, type_data, start_at),
        }
        for optional in ("recurring_group", "recurrence_index", "crm_event_id", "calendar_event_id"):
            value = body.get(optional)
            if value not in (None, ""):
                payload[optional] = value
        if body.get("room_id") and not room_id:
            room_id = str(body["room_id"])
        return self.store.create(BOOKING_COLLECTION, payload, room_id=room_id, actor=actor, source=source)

    def _reminders_for(
        self, body: Mapping[str, Any], type_data: Mapping[str, Any], start_at: datetime
    ) -> list[dict[str, Any]]:
        """Materialise reminders from whatever the caller or the type supplies.

        This package does not decide what a reminder set should be - that is WF-011
        - so there is no default here. A booking with neither gets an empty list
        and the recompute on a reschedule has nothing to move, which is the honest
        outcome rather than an invented 24-hour-and-1-hour pair.
        """
        raw = body.get("reminders")
        if raw in (None, ""):
            offsets = type_data.get("reminder_offsets")
            if not offsets:
                return []
            raw = [{"offset_minutes": offset} for offset in offsets]
        reminders: list[dict[str, Any]] = []
        for entry in raw or []:
            if not isinstance(entry, Mapping):
                raise MeetingChangeError("each reminder must be an object with an offset_minutes")
            try:
                offset = int(entry.get("offset_minutes"))
            except (TypeError, ValueError) as exc:
                raise MeetingChangeError("each reminder needs an integer offset_minutes") from exc
            item = {"offset_minutes": offset, "status": "scheduled"}
            if entry.get("channel"):
                item["channel"] = str(entry["channel"])
            if entry.get("label"):
                item["label"] = str(entry["label"])
            item["scheduled_for"] = iso(start_at - timedelta(minutes=offset))
            reminders.append(item)
        return reminders

    def get_booking(self, uid: str, *, include_superseded: bool = True) -> dict[str, Any] | None:
        """The booking with this ``uid``.

        Resolved through the dynamic index on ``data.uid`` rather than by record
        id, because the researched identifier is Cal's ``bookingUid`` and a
        caller holding an invite has that, not this product's record id.
        """
        found = self.store.find(BOOKING_COLLECTION, {"uid": str(uid)}, limit=1)
        if not found:
            return None
        if not include_superseded and str(found[0]["data"].get("status")) not in LIVE_STATUSES:
            return None
        return found[0]

    def require_booking(self, uid: str) -> dict[str, Any]:
        record = self.get_booking(uid)
        if record is None:
            raise MeetingNotFound(f"booking {uid} not found")
        return record

    def require_live_booking(self, uid: str) -> dict[str, Any]:
        record = self.require_booking(uid)
        data = record["data"]
        if str(data.get("status")) not in LIVE_STATUSES:
            raise BookingConflict(
                f"booking {uid} is {data.get('status')}, and only a booked meeting can be "
                "rescheduled or cancelled"
            )
        return record

    def list_bookings(
        self,
        *,
        room_id: str | None = None,
        status: str | None = None,
        host_email: str | None = None,
        attendee_email: str | None = None,
        recurring_group: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if status is not None:
            where["status"] = str(status)
        if host_email is not None:
            where["host_email"] = str(host_email).strip().lower()
        if attendee_email is not None:
            where["attendee_email"] = str(attendee_email).strip().lower()
        if recurring_group is not None:
            where["recurring_group"] = str(recurring_group)
        records = self.store.find(BOOKING_COLLECTION, where, limit=1000)
        if room_id is not None:
            records = [record for record in records if record.get("room_id") == room_id]
        return records[:limit]

    def live_bookings_for_host(self, host_email: str) -> list[dict[str, Any]]:
        """Every live booking for a host, which is what holds slots against them."""
        return [
            record
            for record in self.store.find(
                BOOKING_COLLECTION, {"host_email": str(host_email).strip().lower()}, limit=1000
            )
            if str(record["data"].get("status")) in LIVE_STATUSES
        ]

    # ----------------------------------------------------------------- #
    # Availability
    # ----------------------------------------------------------------- #

    def host_slots(
        self,
        meeting_type: Mapping[str, Any] | None,
        *,
        host_email: str | None = None,
        from_at: Any = None,
        to_at: Any = None,
        booking_uid_to_reschedule: str | None = None,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """The recomputed availability, with the researched exception applied.

        The one argument that is not a date range is
        ``booking_uid_to_reschedule``: naming a booking releases *its* slot, and
        only its slot, so "the original booking time appears within the returned
        available slots when rescheduling".
        """
        # Accepts a record, a bare payload, or a Meeting Type id. The id case is
        # not a convenience: the endpoint and the seed both hold an id and neither
        # has a record to hand, and resolving it here means every caller gets the
        # same window and duration rather than each re-deriving them.
        resolved = (
            self._require_meeting_type(str(meeting_type))
            if isinstance(meeting_type, str)
            else meeting_type
        )
        data = _type_data(resolved) or _type_data(self.default_meeting_type())
        host = str(host_email or data.get("host_email") or "").strip().lower()
        if not host:
            raise MeetingChangeError("host_email is required to compute availability")
        reference = now or self.clock()
        start = parse(from_at, label="from") if from_at else reference
        end = parse(to_at, label="to") if to_at else start + timedelta(days=DEFAULT_RANGE_DAYS)
        return available_slots(
            host_email=host,
            window=window_for(data),
            duration_minutes=int(data["duration_minutes"]),
            existing=[record["data"] for record in self.live_bookings_for_host(host)],
            from_at=start,
            to_at=end,
            booking_uid_to_reschedule=booking_uid_to_reschedule,
            now=reference,
        )

    # ----------------------------------------------------------------- #
    # Links
    # ----------------------------------------------------------------- #

    def link_state(self, token: str, *, now: datetime | None = None) -> LinkState:
        """What a link token resolves to, and whether it still works.

        Also resolves a reschedule-request token, so the two URLs an attendee can
        be holding - the one in the original invite and the one in the
        "please pick a new time" mail - are read through one endpoint.
        """
        moment = now or self.clock()
        needle = str(token or "").strip()
        if not needle:
            raise MeetingNotFound("no link token was given")

        requests = self.store.find(REQUEST_COLLECTION, {"token": needle}, limit=1)
        if requests:
            record = requests[0]
            data = record["data"]
            status = str(data.get("status") or "")
            return LinkState(
                token=needle,
                kind=CHANGE_RESCHEDULE_REQUESTED,
                tag="CP.Meeting.RescheduleUrl",
                booking_uid=str(data.get("original_uid") or ""),
                expired=status != "pending",
                reason=(
                    "this reschedule request is open"
                    if status == "pending"
                    else f"this reschedule request is {status}"
                ),
                checked_at=iso(moment),
                booking=data,
            )

        booking, kind = link_tools.find_booking_by_token(self.store, needle)
        meeting_type = self.resolve_meeting_type(booking["data"].get("meeting_type_id"))
        return link_tools.state_for(booking, kind, meeting_type.get("data"), moment)

    def invite(
        self,
        uid: str,
        *,
        base: str | None = None,
        now: datetime | None = None,
        path: str | None = None,
    ) -> dict[str, Any]:
        """The invite body, with ``CP.Meeting.RescheduleUrl`` / ``CancelUrl`` resolved."""
        booking = self.require_booking(uid)
        meeting_type = self.resolve_meeting_type(booking["data"].get("meeting_type_id"))
        return link_tools.invite_body(
            booking,
            base=base if base is not None else self.link_base,
            meeting_type=meeting_type.get("data"),
            now=now or self.clock(),
            path=path if path is not None else self.link_path,
        )

    # ----------------------------------------------------------------- #
    # Planning: what would happen, writing nothing
    # ----------------------------------------------------------------- #

    def plan(
        self,
        room_id: str,
        uid: str,
        request: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Resolve a reschedule or cancel request and report it, writing nothing.

        The read-only half of :meth:`reschedule` / :meth:`cancel`, and it shares
        every rule with them, so the answer it gives is the answer the write will
        produce. A rep about to tell an attendee "yes, we can move it to Thursday"
        can see the whole propagation first - which rows move, which webhooks go
        out, what the CRM event will do - and a caller can see a refusal before
        committing to it.
        """
        moment = now or self.clock()
        body = dict(request or {})
        # `require_intent` rather than an `in` test here, so a misspelled intent is
        # refused with the published set named - the same treatment every other
        # vocabulary in this package gets, and the reason the validation helpers
        # raise the domain error rather than a bare ValueError.
        intent = require_intent(body.get("intent") or RESCHEDULE)

        booking = self.require_live_booking(uid)
        data = booking["data"]
        meeting_type = self.resolve_meeting_type(data.get("meeting_type_id"))
        type_data = dict(meeting_type["data"])

        if intent == CANCEL:
            scope = require_cancel_scope(body.get("scope") or SCOPE_THIS)
            targets = self._cancel_targets(booking, scope)
            # The via source first, then the actor, for the same reason `cancel`
            # does it in that order: a link cancellation is the attendee's, and
            # Events History's promise is that it shows who.
            via = self._via_source(body, booking)
            who, kind = self._actor(body, booking, via)
            return {
                "room_id": room_id,
                "intent": CANCEL,
                "booking_uid": uid,
                "scope": scope,
                "targets": [str(record["data"]["uid"]) for record in targets],
                "change_type": CHANGE_CANCELLED,
                "actor_email": who,
                "actor_kind": kind,
                "reschedule_source": via,
                "delete_event": delete_event(type_data),
                "crm_sobject": CRM_SOBJECT,
                "webhooks": [entry["webhook"] for entry in self._envelopes_for_cancel(data, body)],
                "triggers": [propagation.TRIGGER_FOR_CHANGE[CHANGE_CANCELLED]],
                "reminders_dropped": len(data.get("reminders") or []),
                "writes": "cancels each target booking, moves its calendar event, applies "
                "Delete Event, pushes BOOKING_CANCELLED and Meeting Update, and writes one "
                "Events History row per target",
                "at": iso(moment),
            }

        source_kind = self._reschedule_source(body, booking)
        guard = self._link_guard(body, booking, source_kind, moment)
        target = self._resolve_target(booking, type_data, body, moment)
        # A plan for a request-to-reschedule reports the same two effects the write
        # produces - the cancellation and the pending request - so a caller is not
        # told one thing and then gets another. The change type is named by
        # `cause` for the same reason the write names it that way.
        via = self._via_source(body, booking)
        who, kind = self._actor(body, booking, via)
        envelopes = propagation.webhook_envelopes(
            change_type=CHANGE_RESCHEDULED,
            new_booking={
                "uid": str(body.get("new_uid") or ""),
                "start_at": target["start_at"],
                "end_at": target["end_at"],
            },
            old_booking=data,
            reschedule_id=self._next_reschedule_id(booking),
            cancellation_reason=None,
            cancelled_by_email=None,
            location=target["location"],
        )
        plan: dict[str, Any] = {
            "room_id": room_id,
            "intent": intent,
            "booking_uid": uid,
            "change_type": CHANGE_RESCHEDULED,
            "reschedule_source": source_kind,
            "reschedule_id": self._next_reschedule_id(booking),
            "actor_email": who,
            "actor_kind": kind,
            "from": {"start_at": data.get("start_at"), "end_at": data.get("end_at")},
            "to": {
                "start_at": target["start_at"],
                "end_at": target["end_at"],
                "location": target["location"],
            },
            "link": guard.to_dict() if guard else None,
            "reminders_rebased": len(data.get("reminders") or []),
            "webhooks": [entry["webhook"] for entry in envelopes],
            "triggers": [propagation.TRIGGER_FOR_CHANGE[CHANGE_RESCHEDULED]],
            "crm_sobject": CRM_SOBJECT,
            "writes": "supersedes the old booking, creates a new booking, moves the calendar "
            "event, updates the CRM Event, pushes the webhooks, re-bases the reminders, and "
            "writes one Events History row",
            "at": iso(moment),
        }

        if intent == REQUEST_RESCHEDULE:
            # The researched sentence has two clauses and both land, so a plan for
            # this intent says both rather than describing the reschedule half and
            # letting the caller discover the cancellation by causing one.
            plan["writes"] = (
                "cancels the booking, moves its calendar event, applies Delete Event, pushes "
                "BOOKING_CANCELLED and Meeting Update, writes the Events History row, and "
                "raises a pending reschedule request carrying a token that outlives the booking "
                "it cancelled"
            )
            plan["also"] = {
                "cancels_the_booking": True,
                "change_type": CHANGE_CANCELLED,
                "cause": CHANGE_RESCHEDULE_REQUESTED,
                "delete_event": delete_event(type_data),
                "webhooks": [entry["webhook"] for entry in self._envelopes_for_cancel(data, body)],
                "triggers": [propagation.TRIGGER_FOR_CHANGE[CHANGE_CANCELLED]],
            }
        return plan

    # ----------------------------------------------------------------- #
    # The workflow
    # ----------------------------------------------------------------- #

    def reschedule(
        self,
        room_id: str,
        uid: str,
        request: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Steps 1 to 4: move a booked meeting and propagate the move.

        Returns the Events History row, because that is the artefact the flow is
        required to produce and the one a rep reads afterwards.
        """
        moment = self.clock()
        self._require_room(room_id)
        body = dict(request or {})
        booking = self.require_live_booking(uid)
        data = booking["data"]
        meeting_type = self.resolve_meeting_type(data.get("meeting_type_id"))
        type_data = dict(meeting_type["data"])

        source_kind = self._reschedule_source(body, booking)
        self._link_guard(body, booking, source_kind, moment, enforce=True)
        target = self._resolve_target(booking, type_data, body, moment)

        reason = self._clean_reason(body.get("reason") or body.get("reschedule_reason"))
        who, kind = self._actor(body, booking, source_kind)
        reschedule_id = self._next_reschedule_id(booking)
        new_uid = str(body.get("new_uid") or "").strip() or self.uid_factory()
        if self.store.find(BOOKING_COLLECTION, {"uid": new_uid}, limit=1):
            raise MeetingChangeError(f"a booking with uid {new_uid} already exists")

        new_payload = self._new_booking_payload(booking, type_data, new_uid, target, reason, source_kind, reschedule_id)
        new_room_id = booking.get("room_id")
        envelopes = propagation.webhook_envelopes(
            change_type=CHANGE_RESCHEDULED,
            new_booking=new_payload,
            old_booking=data,
            reschedule_id=reschedule_id,
            cancellation_reason=None,
            cancelled_by_email=None,
            location=target["location"],
        )

        with self.store.db.transaction(actor=actor, source=source) as tx:
            old_patch = {
                "status": RESCHEDULED,
                "rescheduled_to_uid": new_uid,
                "reschedule_id": reschedule_id,
                "reschedule_reason": reason,
                "reschedule_source": source_kind,
                "rescheduled_by": who,
                "reminders": propagation.rebase_reminders(data.get("reminders"), target["start_at"]),
            }
            tx.update(booking["id"], old_patch, actor=actor, source=source)
            created = tx.create(
                BOOKING_COLLECTION, new_payload, room_id=new_room_id, actor=actor, source=source
            )

            change_payload = self._change_payload(
                change_type=CHANGE_RESCHEDULED,
                booking=data,
                new_booking=new_payload,
                room_id=new_room_id,
                actor_email=who,
                actor_kind=kind,
                at=iso(moment),
                reschedule_source=source_kind,
                reschedule_id=reschedule_id,
                reason=reason,
            )
            change = tx.create(
                CHANGE_COLLECTION, change_payload, room_id=new_room_id, actor=actor, source=source
            )

            calendar = propagation.move_calendar_event(
                tx,
                # The superseded booking, not the new one: the researched
                # behaviour is that one calendar event moves, so the row keyed to
                # the old uid is the one updated. The new booking inherits
                # `calendar_event_id` in its own payload.
                booking=data,
                # The new booking is what this change leaves in force, so the row
                # is pointed at it; the chain still identifies the meeting.
                current_uid=new_payload["uid"],
                change_type=CHANGE_RESCHEDULED,
                start_at=target["start_at"],
                end_at=target["end_at"],
                change_id=change["id"],
                existing=self._calendar_event(str(data.get("uid") or ""), self._chain_root(booking)),
                room_id=new_room_id,
                actor=actor,
                source=source,
            )
            crm = propagation.propagate_crm_event(
                tx,
                # The superseded booking, for the same reason as the calendar event:
                # one CRM Event per meeting, updated as the meeting moves. The
                # chain is what finds it on a second move.
                booking=data,
                current_uid=new_payload["uid"],
                change_type=CHANGE_RESCHEDULED,
                delete_event=delete_event(type_data),
                start_at=target["start_at"],
                end_at=target["end_at"],
                change_id=change["id"],
                existing=self._crm_event(str(data.get("uid") or ""), self._chain_root(booking)),
                room_id=new_room_id,
                actor=actor,
                source=source,
            )
            webhooks = propagation.record_webhooks(
                tx,
                envelopes,
                booking_uid=new_payload["uid"],
                change_id=change["id"],
                room_id=new_room_id,
                actor=actor,
                source=source,
            )
            notices = propagation.record_notifications(
                tx,
                change_type=CHANGE_RESCHEDULED,
                booking=new_payload,
                change_id=change["id"],
                channels=propagation.channels_for(type_data),
                room_id=new_room_id,
                actor=actor,
                source=source,
                at=iso(moment),
            )
            tx.update(
                change["id"],
                {
                    "calendar": calendar,
                    "crm_event": crm,
                    "webhooks_sent": [item["webhook"] for item in webhooks],
                    "notifications_sent": len(notices),
                    "new_booking_id": created["id"],
                    "new_booking_uid": new_payload["uid"],
                },
                actor=actor,
                source=source,
            )
        return self.store.get(change["id"]) or change

    def request_reschedule(
        self,
        room_id: str,
        uid: str,
        request: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """``POST /v2/bookings/{uid}/request-reschedule``, with both halves of it.

        "The booking will be cancelled and the attendee will receive an email with
        a link to reschedule." Both sentences are load-bearing and they are two
        different things, so both happen: the cancel path runs in full - release
        the slot, cancel the calendar event, apply ``Delete Event``, push
        ``BOOKING_CANCELLED``, write the history row - and then a *pending* reschedule
        request is written carrying its own token, which is the link the attendee
        completes.

        The token is a new artefact rather than the booking's own ``RescheduleUrl``
        for two researched reasons, both of which a reader should be able to check
        against the flow: the original link is on a booking this call has just
        cancelled, and the whole point of the request is that it must outlive the
        meeting - which is what "Expire Reschedule Link lets a vendor force a
        fresh booking after the fact" is for.
        """
        moment = self.clock()
        self._require_room(room_id)
        body = dict(request or {})
        booking = self.require_live_booking(uid)
        data = booking["data"]
        meeting_type = self.resolve_meeting_type(data.get("meeting_type_id"))
        type_data = dict(meeting_type["data"])

        source_kind = self._reschedule_source(body, booking)
        self._link_guard(body, booking, source_kind, moment, enforce=True)
        reason = self._clean_reason(body.get("reason") or body.get("reschedule_reason"))
        who, kind = self._actor(body, booking, source_kind)
        scope = require_cancel_scope(body.get("scope") or SCOPE_THIS)
        targets = self._cancel_targets(booking, scope)
        token = str(body.get("token") or "").strip() or self.token_factory()

        notifications: list[dict[str, Any]] = []
        with self.store.db.transaction(actor=actor, source=source) as tx:
            for target in targets:
                change, sent = self._cancel_one(
                    tx,
                    target=target,
                    room_id=target.get("room_id") or room_id,
                    actor_email=who,
                    actor_kind=kind,
                    reason=reason,
                    scope=SCOPE_THIS,
                    at=iso(moment),
                    type_data=type_data,
                    actor=actor,
                    source=source,
                    cause=CHANGE_RESCHEDULE_REQUESTED,
                )
                notifications.append({"change_id": change["id"], "webhooks": sent})

            original = targets[0]
            record = tx.create(
                REQUEST_COLLECTION,
                {
                    "token": token,
                    "status": "pending",
                    "original_uid": original["data"]["uid"],
                    "chain_root": str(original["data"].get("chain_root") or original["data"]["uid"]),
                    "meeting_type_id": original["data"].get("meeting_type_id"),
                    # Also on the envelope, which is what `room_id` here writes.
                    # Not repeated in `data` on purpose: the store reserves
                    # `room_id` and strips it from the payload on insert, so a
                    # copy there would be silently dropped and the request would
                    # come back scoped to nothing.
                    "booked_for_room": original.get("room_id") or room_id,
                    "host_email": original["data"].get("host_email"),
                    "attendee_email": str(
                        body.get("attendee_email") or original["data"].get("attendee_email") or ""
                    ).strip().lower(),
                    "reason": reason,
                    "requested_by": who,
                    "requested_by_kind": kind,
                    "reschedule_source": source_kind,
                    "at": iso(moment),
                    "original_start_at": original["data"].get("start_at"),
                },
                room_id=original.get("room_id") or room_id,
                actor=actor,
                source=source,
            )
        return self.store.get(record["id"]) or record

    def complete_request(
        self,
        request_id: str,
        body: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """The other half of a reschedule request: the attendee picks a time.

        Creates the new booking, writes the history row, and propagates - the same
        work an immediate reschedule does. The new booking is linked back through
        ``rescheduled_from_uid`` and the request's ``original_uid`` so the two-step
        path is as traceable as the one-step one, which is what makes "reschedule
        churn" alerting possible across it.
        """
        moment = self.clock()
        request = self.store.get(request_id)
        if request is None or request.get("collection") != REQUEST_COLLECTION:
            raise MeetingNotFound(f"reschedule request {request_id} not found")
        data = request["data"]
        if str(data.get("status")) != "pending":
            raise BookingConflict(f"reschedule request {request_id} is {data.get('status')}")
        # Raised here rather than at the end so an unusable request never reads a
        # slot list it cannot act on.
        if not data.get("meeting_type_id"):
            raise MeetingChangeError(
                f"reschedule request {request_id} names no Meeting Type, so there is no "
                "availability to recompute against; the request outlived the type it was raised against"
            )

        meeting_type = self.resolve_meeting_type(data.get("meeting_type_id"))
        type_data = dict(meeting_type["data"])
        # The room comes from the envelope, never from the payload: `room_id` is a
        # reserved key the store strips from `data`, so a request read back from
        # `data.room_id` would always be absent and every completed reschedule
        # would land unscoped, invisible to the room's own history.
        room_id = str(request.get("room_id") or "") or None
        original = self.get_booking(str(data.get("original_uid") or ""))
        if original is None:
            raise MeetingNotFound(f"booking {data.get('original_uid')} not found")

        payload = dict(body or {})
        # A request has no live booking holding its old slot, so nothing is
        # released and every slot on offer is a genuinely new time.
        slots = self.host_slots(
            type_data, now=moment, booking_uid_to_reschedule=str(data.get("original_uid") or "")
        )
        start_at = self._require_start(payload)
        slot = find_slot(slots, start_at)
        if slot is None:
            raise MeetingChangeError(explain_missing(slots, start_at))
        if slot["in_past"]:
            raise MeetingChangeError(
                explain_missing(slots, start_at, booking_uid_to_reschedule=None)
            )
        self._check_horizon(start_at, moment)

        who = self._clean_email(
            payload.get("actor_email") or data.get("attendee_email"), "actor_email"
        )
        kind = ATTENDEE
        reschedule_id = self._next_chain_reschedule_id(str(data.get("chain_root") or ""))
        new_uid = str(payload.get("new_uid") or "").strip() or self.uid_factory()
        if self.store.find(BOOKING_COLLECTION, {"uid": new_uid}, limit=1):
            raise MeetingChangeError(f"a booking with uid {new_uid} already exists")

        start = parse(start_at)
        end = parse(slot_end_for(start_at, int(type_data["duration_minutes"])))
        reason = self._clean_reason(payload.get("reason")) or data.get("reason")
        tokens = link_tools.mint_pair(new_uid, factory=self.token_factory)
        new_payload = {
            "uid": new_uid,
            "meeting_type_id": meeting_type.get("id"),
            "title": original["data"].get("title") or type_data.get("name") or "Meeting",
            "host_email": original["data"].get("host_email"),
            "attendee_name": original["data"].get("attendee_name"),
            "attendee_email": str(data.get("attendee_email") or original["data"].get("attendee_email") or ""),
            "start_at": iso(start),
            "end_at": iso(end),
            "duration_minutes": int(type_data["duration_minutes"]),
            "timezone": type_data.get("timezone") or "UTC",
            "location": str(payload.get("location") or original["data"].get("location") or "") or None,
            "calendar_provider": original["data"].get("calendar_provider"),
            "status": BOOKED,
            "chain_root": str(data.get("chain_root") or new_uid),
            "rescheduled_from_uid": str(data.get("original_uid") or ""),
            "reschedule_id": reschedule_id,
            "reschedule_reason": reason,
            "reschedule_source": RESCHEDULE_LINK,
            "reschedule_request_id": request_id,
            "reschedule_token": tokens[RESCHEDULE],
            "cancel_token": tokens[CANCEL],
            "reminders": self._reminders_for(payload, type_data, start),
        }

        envelopes = propagation.webhook_envelopes(
            change_type=CHANGE_RESCHEDULED,
            new_booking=new_payload,
            old_booking=original["data"],
            reschedule_id=reschedule_id,
            cancellation_reason=None,
            cancelled_by_email=None,
            location=new_payload.get("location"),
        )

        with self.store.db.transaction(actor=actor, source=source) as tx:
            created = tx.create(BOOKING_COLLECTION, new_payload, room_id=room_id, actor=actor, source=source)
            change_payload = self._change_payload(
                change_type=CHANGE_RESCHEDULED,
                booking=original["data"],
                new_booking=new_payload,
                room_id=room_id,
                actor_email=who,
                actor_kind=kind,
                at=iso(moment),
                reschedule_source=RESCHEDULE_LINK,
                reschedule_id=reschedule_id,
                reason=reason,
                request_id=request_id,
            )
            change = tx.create(CHANGE_COLLECTION, change_payload, room_id=room_id, actor=actor, source=source)
            calendar = propagation.move_calendar_event(
                tx,
                # The cancelled original, for the same reason as an immediate
                # reschedule: the two-step path moves the same calendar event.
                booking=original["data"],
                current_uid=new_payload["uid"],
                change_type=CHANGE_RESCHEDULED,
                start_at=new_payload["start_at"],
                end_at=new_payload["end_at"],
                change_id=change["id"],
                existing=self._calendar_event(
                    str(data.get("original_uid") or ""), str(data.get("chain_root") or "")
                ),
                room_id=room_id,
                actor=actor,
                source=source,
            )
            crm = propagation.propagate_crm_event(
                tx,
                # The cancelled original, for the same reason as the calendar event.
                booking=original["data"],
                current_uid=new_payload["uid"],
                change_type=CHANGE_RESCHEDULED,
                delete_event=delete_event(type_data),
                start_at=new_payload["start_at"],
                end_at=new_payload["end_at"],
                change_id=change["id"],
                existing=self._crm_event(
                    str(data.get("original_uid") or ""), str(data.get("chain_root") or "")
                ),
                room_id=room_id,
                actor=actor,
                source=source,
            )
            webhooks = propagation.record_webhooks(
                tx,
                envelopes,
                booking_uid=new_payload["uid"],
                change_id=change["id"],
                room_id=room_id,
                actor=actor,
                source=source,
            )
            notices = propagation.record_notifications(
                tx,
                change_type=CHANGE_RESCHEDULED,
                booking=new_payload,
                change_id=change["id"],
                channels=propagation.channels_for(type_data),
                room_id=room_id,
                actor=actor,
                source=source,
                at=iso(moment),
            )
            tx.update(
                request_id,
                {
                    "status": "completed",
                    "completed_at": iso(moment),
                    "completed_booking_id": created["id"],
                    "completed_booking_uid": new_payload["uid"],
                },
                actor=actor,
                source=source,
            )
            tx.update(
                change["id"],
                {
                    "calendar": calendar,
                    "crm_event": crm,
                    "webhooks_sent": [item["webhook"] for item in webhooks],
                    "notifications_sent": len(notices),
                    "new_booking_id": created["id"],
                    "new_booking_uid": new_payload["uid"],
                },
                actor=actor,
                source=source,
            )
        return self.store.get(change["id"]) or change

    def cancel(
        self,
        room_id: str,
        uid: str,
        request: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """``POST /v2/bookings/{uid}/cancel``: one recurrence, or all of them.

        "``:bookingUid`` can be ... of an usual booking, individual recurrence or
        recurring booking to cancel all recurrences." The whole set is cancelled in
        one transaction, each target with its own history row, its own webhooks and
        its own CRM event, because a half-cancelled series is the one outcome that
        leaves a customer with a meeting nobody is sure exists.
        """
        moment = self.clock()
        self._require_room(room_id)
        body = dict(request or {})
        booking = self.require_live_booking(uid)
        data = booking["data"]
        meeting_type = self.resolve_meeting_type(data.get("meeting_type_id"))
        type_data = dict(meeting_type["data"])

        scope = require_cancel_scope(body.get("scope") or SCOPE_THIS)
        targets = self._cancel_targets(booking, scope)
        reason = self._clean_reason(body.get("reason") or body.get("cancellation_reason"))
        # The via source is resolved first because it decides who the change is
        # attributed to. A cancel that arrived through the link in the invite is
        # the attendee's, and `CP.Meeting.CancelUrl` is exactly that link - so
        # reading the actor before the source would file an attendee's
        # cancellation under the host, which is the one attribution the history
        # row exists to get right.
        via = self._via_source(body, booking)
        who, kind = self._actor(body, booking, via)

        written: list[dict[str, Any]] = []
        with self.store.db.transaction(actor=actor, source=source) as tx:
            for target in targets:
                change, sent = self._cancel_one(
                    tx,
                    target=target,
                    room_id=target.get("room_id") or room_id,
                    actor_email=who,
                    actor_kind=kind,
                    reason=reason,
                    scope=scope,
                    at=iso(moment),
                    type_data=type_data,
                    actor=actor,
                    source=source,
                    via_source=via,
                )
                written.append({"change_id": change["id"], "webhooks": sent})

        if len(written) == 1:
            return self.store.get(written[0]["change_id"]) or written[0]
        # A series sweep writes one row per occurrence, and returning only the
        # first would hide the rest from the caller. The shape is explicit.
        return {
            "sweep": {
                "scope": scope,
                "count": len(written),
                "recurring_group": (targets[0]["data"].get("recurring_group") if targets else None),
                "changes": [item["change_id"] for item in written],
                "at": iso(moment),
            }
        }

    # ----------------------------------------------------------------- #
    # Reads of the propagated state
    # ----------------------------------------------------------------- #

    def changes(
        self,
        *,
        room_id: str | None = None,
        change_type: str | None = None,
        booking_uid: str | None = None,
        chain_root: str | None = None,
        actor_email: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Events History, newest first. Every filter is a JSON path in the payload.

        ``room_id`` is filtered here rather than in the query: the dynamic index
        resolves JSON paths inside ``data`` but not the ``room_id`` column, so
        :meth:`~dsr.db.audited.AuditedDatabase.find` cannot scope by room.
        Filtering in Python keeps that a detail of this method rather than a
        schema change.
        """
        where: dict[str, Any] = {}
        if change_type is not None:
            where["type"] = require_change_type(change_type)
        if booking_uid is not None:
            where["booking_uid"] = str(booking_uid)
        if chain_root is not None:
            where["chain_root"] = str(chain_root)
        if actor_email is not None:
            where["actor_email"] = str(actor_email).strip().lower()
        records = self.store.find(CHANGE_COLLECTION, where, limit=1000)
        if room_id is not None:
            records = [record for record in records if record.get("room_id") == room_id]
        return records[:limit]

    def webhooks(
        self, *, booking_uid: str | None = None, change_id: str | None = None, webhook: str | None = None,
        status: str | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if booking_uid is not None:
            where["booking_uid"] = str(booking_uid)
        if change_id is not None:
            where["change_id"] = str(change_id)
        if webhook is not None:
            where["webhook"] = str(webhook)
        if status is not None:
            where["status"] = str(status)
        return self.store.find(propagation.WEBHOOK_COLLECTION, where, limit=limit)

    def notifications(
        self, *, booking_uid: str | None = None, change_id: str | None = None,
        channel: str | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if booking_uid is not None:
            where["booking_uid"] = str(booking_uid)
        if change_id is not None:
            where["change_id"] = str(change_id)
        if channel is not None:
            where["channel"] = str(channel)
        return self.store.find(propagation.NOTIFICATION_COLLECTION, where, limit=limit)

    def crm_events(self, *, booking_uid: str | None = None, include_deleted: bool = False) -> list[dict[str, Any]]:
        where = {"booking_uid": str(booking_uid)} if booking_uid else {}
        return self.store.find(propagation.CRM_EVENT_COLLECTION, where, limit=200, include_deleted=include_deleted)

    def calendar_events(self, *, booking_uid: str | None = None) -> list[dict[str, Any]]:
        where = {"booking_uid": str(booking_uid)} if booking_uid else {}
        return self.store.find(propagation.CALENDAR_EVENT_COLLECTION, where, limit=200)

    def requests(
        self, *, status: str | None = None, booking_uid: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if status is not None:
            where["status"] = str(status)
        if booking_uid is not None:
            where["original_uid"] = str(booking_uid)
        return self.store.find(REQUEST_COLLECTION, where, limit=limit)

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        record = self.store.get(request_id)
        return record if record and record.get("collection") == REQUEST_COLLECTION else None

    def get_change(self, change_id: str) -> dict[str, Any] | None:
        record = self.store.get(change_id)
        return record if record and record.get("collection") == CHANGE_COLLECTION else None

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Counts for the top of the page, over exactly the rows the filters return."""
        rows = self.changes(room_id=room_id, limit=1000)
        by_type: dict[str, int] = {}
        by_source: dict[str, int] = {}
        reasons = 0
        for record in rows:
            data = record["data"]
            by_type[str(data.get("type"))] = by_type.get(str(data.get("type")), 0) + 1
            if data.get("reschedule_source"):
                key = str(data["reschedule_source"])
                by_source[key] = by_source.get(key, 0) + 1
            if data.get("cancellation_reason"):
                reasons += 1
        bookings = self.list_bookings(room_id=room_id, limit=1000)
        live = [record for record in bookings if str(record["data"].get("status")) in LIVE_STATUSES]
        pushed = self.webhooks(limit=1000)
        return {
            "room_id": room_id,
            "changes": len(rows),
            "by_type": dict(sorted(by_type.items())),
            "by_reschedule_source": dict(sorted(by_source.items())),
            "with_cancellation_reason": reasons,
            "bookings": len(bookings),
            "live_bookings": len(live),
            "pending_requests": len(self.requests(status="pending")),
            "webhooks_pushed": len(pushed),
            # Counted on the row's own status rather than on `deleted_at`, because
            # `Delete Event` is recorded as a lifecycle state inside the change's
            # transaction - see propagation.propagate_crm_event for why.
            "crm_events_deleted": sum(
                1
                for record in self.crm_events(include_deleted=True)
                if str(record["data"].get("status")) == "deleted"
            ),
        }

    # ----------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------- #

    def _require_room(self, room_id: str) -> dict[str, Any]:
        room = self.store.get(room_id)
        if room is None:
            # RecordNotFound, not MeetingNotFound: a room that does not exist is a
            # 404 in every route in the product, and the core app already maps
            # this type. Raising our own error here would answer 400 for what
            # everything else answers 404 for.
            raise RecordNotFound(room_id)
        return room

    def _calendar_event(self, uid: str, chain_root: str | None = None) -> dict[str, Any] | None:
        """A meeting's calendar event, found by chain.

        By chain rather than by booking uid, because one event moves with the
        meeting: a reschedule leaves the old booking superseded and a new one
        current, and a lookup keyed on the current uid alone would find nothing and
        mint a second event. The chain is what identifies the meeting across every
        move it has had.
        """
        if chain_root:
            found = self.store.find(propagation.CALENDAR_EVENT_COLLECTION, {"chain_root": chain_root}, limit=1)
            if found:
                return found[0]
        found = self.store.find(propagation.CALENDAR_EVENT_COLLECTION, {"booking_uid": uid}, limit=1)
        return found[0] if found else None

    def _crm_event(self, uid: str, chain_root: str | None = None) -> dict[str, Any] | None:
        """A meeting's CRM ``Event``, found by chain.

        By chain for the same reason as the calendar event: the researched flow
        says the CRM Event is *updated* on a move, which is a statement about one
        object changing rather than a new one appearing. A lookup on the current
        booking's uid alone would find nothing on a second move and create a second
        Event, leaving two Salesforce events for one meeting.
        """
        if chain_root:
            found = self.store.find(propagation.CRM_EVENT_COLLECTION, {"chain_root": chain_root}, limit=1)
            if found:
                return found[0]
        found = self.store.find(propagation.CRM_EVENT_COLLECTION, {"booking_uid": uid}, limit=1)
        return found[0] if found else None

    def _chain_root(self, booking: Mapping[str, Any]) -> str:
        data = dict(booking.get("data") or booking)
        return str(data.get("chain_root") or data.get("uid") or "")

    def _next_reschedule_id(self, booking: Mapping[str, Any]) -> int:
        return self._next_chain_reschedule_id(self._chain_root(booking))

    def _next_chain_reschedule_id(self, chain_root: str) -> int:
        """The next ``rescheduleId`` on a chain.

        The research's example is ``200`` and its field name is an id, so this is
        a sequence per reschedule chain and the first move of a booking is 1. The
        number is counted from the history rows rather than held in a counter
        field, so a chain's ids stay correct after a restore and cannot drift from
        the history they are supposed to number.
        """
        if not chain_root:
            return 1
        return len(self.store.find(CHANGE_COLLECTION, {"chain_root": chain_root}, limit=1000)) + 1

    def _reschedule_source(self, body: Mapping[str, Any], booking: Mapping[str, Any]) -> str:
        """Which of the three researched sources this reschedule came from.

        Explicit beats derived. A caller that says ``calendar_event`` gets
        ``calendar_event`` even if it also passes a link token, because the link
        token may have travelled in a forwarded mail and the host's own action is
        the fact that matters. With neither, the default is ``chilical_home``: the
        Meetings Activity panel is the researched surface for a host-driven change,
        and a request that names no source is a request from the panel.
        """
        explicit = body.get("reschedule_source")
        if explicit not in (None, ""):
            return require_reschedule_source(explicit)
        if body.get("link_token"):
            return RESCHEDULE_LINK
        return CHILICAL_HOME

    def _via_source(self, body: Mapping[str, Any], booking: Mapping[str, Any] | None = None) -> str:
        """Where a cancellation came from.

        "Triggers when a user or prospect cancels the meeting from any via source"
        is the research's own reason to record this: the Chili Piper webhook fires
        whatever the via, so the history has to say which one it was. Same three
        values as a reschedule, for the same sourced reason - the ``Delete Event``
        sentence names both the Dashboard and the calendar provider.

        A supplied token means the change arrived through a link, and the link
        named in the source is whichever kind of token it is, resolved rather than
        assumed. Attributing a cancellation to the host because the caller passed
        a link token without saying so would put the wrong person in the one field
        Events History promises to show.
        """
        explicit = body.get("reschedule_source") or body.get("via_source")
        if explicit not in (None, ""):
            return require_reschedule_source(explicit)
        # Any link token means the change arrived through a link, whichever of the
        # two it is. Resolving the kind first and returning the panel for a
        # reschedule token would attribute an attendee's cancellation to the host
        # purely because they used the wrong door, and the research names the two
        # tags as a pair.
        if str(body.get("link_token") or "").strip():
            return RESCHEDULE_LINK
        return CHILICAL_HOME

    def _link_guard(
        self,
        body: Mapping[str, Any],
        booking: Mapping[str, Any],
        source_kind: str,
        moment: datetime,
        *,
        enforce: bool = False,
    ) -> LinkState | None:
        """Check the reschedule link, when the change claims to be arriving through it.

        Only the ``reschedule_link`` source is guarded. A host in the Meetings
        Activity panel and a change made in the calendar provider are not arriving
        through a link, so ``Expire Reschedule Link`` - a setting *about the link* -
        does not apply to them. Enforcing expiry on a host action would make the
        setting something it was never described as.
        """
        if source_kind != RESCHEDULE_LINK:
            return None
        token = str(body.get("link_token") or link_tools.token_for(booking, RESCHEDULE))
        resolved, kind = link_tools.find_booking_by_token(self.store, token)
        if str(resolved["data"].get("uid")) != str(booking["data"].get("uid")):
            raise MeetingNotFound(f"link {token} does not belong to booking {booking['data'].get('uid')}")
        if kind != RESCHEDULE:
            raise MeetingChangeError(
                f"link {token} is a cancel link; a reschedule needs the reschedule link"
            )
        meeting_type = self.resolve_meeting_type(booking["data"].get("meeting_type_id"))
        state = link_tools.state_for(resolved, RESCHEDULE, meeting_type.get("data"), moment)
        if enforce:
            link_tools.require_open(state)
        return state

    def _require_start(self, body: Mapping[str, Any]) -> str:
        start = body.get("start_at") or body.get("start") or body.get("new_start_at")
        if start in (None, ""):
            raise MeetingChangeError("start_at is required: a reschedule names the new time")
        return iso(parse(start, label="start_at"))

    def _check_horizon(self, start_at: str, moment: datetime) -> None:
        horizon = moment + timedelta(days=MAX_RESCHEDULE_HORIZON_DAYS)
        if parse(start_at) > horizon:
            raise MeetingChangeError(
                f"start_at is more than {MAX_RESCHEDULE_HORIZON_DAYS} days ahead; a reschedule "
                "moves a meeting, it does not book one for next year"
            )

    def _resolve_target(
        self,
        booking: Mapping[str, Any],
        type_data: Mapping[str, Any],
        body: Mapping[str, Any],
        moment: datetime,
    ) -> dict[str, Any]:
        """The researched new slot, or a refusal saying why there isn't one.

        This is where ``bookingUidToReschedule`` earns its keep: the availability
        read is run with this booking's own uid, so its original time is on offer
        along with every genuinely free slot, and nothing else is released.
        """
        uid = str(booking["data"].get("uid"))
        start_at = self._require_start(body)
        # The horizon is checked before the slot list is computed, not after. It is
        # a property of the request rather than of the calendar, so a request that
        # is already refused should not pay for an availability read - and, more
        # usefully, the refusal a caller sees for a wildly distant date should be
        # about the horizon rather than about a weekend it happened to land on.
        self._check_horizon(start_at, moment)
        duration = int(body.get("duration_minutes") or type_data["duration_minutes"])
        host = str(body.get("host_email") or booking["data"].get("host_email") or type_data["host_email"])
        # The window is a week either side rather than a day. Two reasons, both
        # about what the caller is told: a target on a Saturday has no slot of its
        # own but has open slots on Friday and Monday, and a one-day window would
        # report "past the end of the range that was searched" for a date that was
        # only ever out of hours. A week makes the refusal name the real reason and
        # puts a genuinely open time in the message.
        slots = available_slots(
            host_email=host,
            window=window_for(type_data),
            duration_minutes=duration,
            existing=[record["data"] for record in self.live_bookings_for_host(host)],
            from_at=parse(start_at) - timedelta(days=RESCHEDULE_SEARCH_DAYS),
            to_at=parse(start_at) + timedelta(days=RESCHEDULE_SEARCH_DAYS),
            booking_uid_to_reschedule=uid,
            now=moment,
        )
        slot = find_slot(slots, start_at)
        if slot is None:
            raise MeetingChangeError(
                explain_missing(slots, start_at, booking_uid_to_reschedule=uid)
            )
        if slot["in_past"] and not slot.get("original_slot"):
            raise MeetingChangeError(
                f"{start_at} is in the past, so the meeting cannot be moved there; the original "
                "booking time is the one past slot that remains available"
            )
        return {
            "start_at": start_at,
            "end_at": slot["end_at"],
            "location": str(body.get("location") or booking["data"].get("location") or "") or None,
            "original_slot": bool(slot.get("original_slot")),
            "host_email": host,
        }

    def _actor(
        self, body: Mapping[str, Any], booking: Mapping[str, Any], source_kind: str
    ) -> tuple[str, str]:
        """``(email, kind)`` for the Events History "who".

        The researched row has to show who, and the researched place a name appears
        on a cancellation is ``cancelledByEmail`` - so the email is required and the
        kind is *derived* rather than trusted. A change arriving through the
        reschedule link is the attendee's, whatever ``actor_kind`` the payload
        claims: a link token can travel in a forwarded mail, and if the payload
        could overrule the provenance then the provenance would be worth nothing in
        the history it exists to record. Everything else takes the caller's word,
        because the host panel is a signed-in surface and the caller's claim there
        is the only information there is.
        """
        data = dict(booking.get("data") or {})
        if source_kind == RESCHEDULE_LINK:
            kind = ATTENDEE
        else:
            kind = str(body.get("actor_kind") or HOST)
        require_actor_kind(kind)
        address = (
            body.get("actor_email")
            or data.get("attendee_email")
            or data.get("host_email")
        )
        return self._clean_email(address, "actor_email"), kind

    def _clean_email(self, value: Any, label: str) -> str:
        text = str(value or "").strip().lower()
        if not text:
            raise MeetingChangeError(f"{label} is required: the history row records who changed it")
        if "@" not in text or text.startswith("@") or text.endswith("@") or " " in text:
            raise MeetingChangeError(f"{label} must be an email address; got {value!r}")
        return text

    def _clean_reason(self, value: Any) -> str | None:
        """An optional cancellation or reschedule reason.

        Optional in both directions: "an optional cancellation reason is captured",
        and a reschedule to the same time needs no explanation. Capped because the
        reason ships in a webhook payload and a third party building "reschedule
        churn" alerting should not have to handle an unbounded field.
        """
        if value in (None, ""):
            return None
        text = str(value).strip()
        if not text:
            return None
        if len(text) > 2000:
            raise MeetingChangeError("reason must be 2000 characters or fewer")
        return text

    def _cancel_targets(self, booking: Mapping[str, Any], scope: str) -> list[dict[str, Any]]:
        """The bookings a cancel at this scope acts on.

        ``this`` is one row. ``all`` is every live booking in the recurring series,
        including this one, and a booking with no series is simply itself - which is
        what the researched sentence says about "an usual booking".
        """
        data = dict(booking.get("data") or {})
        if scope == SCOPE_THIS:
            return [booking]
        group = data.get("recurring_group")
        if not group:
            return [booking]
        series = [
            record
            for record in self.list_bookings(recurring_group=str(group), limit=MAX_SERIES_SWEEP)
            if str(record["data"].get("status")) in LIVE_STATUSES
        ]
        # The named booking first, so the returned list and the first history row
        # agree about which occurrence the caller asked about.
        series.sort(key=lambda record: (record["id"] != booking["id"], str(record["id"])))
        return series or [booking]

    def _envelopes_for_cancel(
        self, data: Mapping[str, Any], body: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        return propagation.webhook_envelopes(
            change_type=CHANGE_CANCELLED,
            new_booking=None,
            old_booking=data,
            reschedule_id=None,
            cancellation_reason=self._clean_reason(body.get("reason") or body.get("cancellation_reason")),
            cancelled_by_email=self._clean_email(
                body.get("actor_email") or data.get("attendee_email") or data.get("host_email"),
                "actor_email",
            ),
            location=None,
        )

    def _new_booking_payload(
        self,
        old: Mapping[str, Any],
        type_data: Mapping[str, Any],
        new_uid: str,
        target: Mapping[str, Any],
        reason: str | None,
        source_kind: str,
        reschedule_id: int,
    ) -> dict[str, Any]:
        """The new booking, carrying the researched chain fields.

        ``rescheduledFromUid`` and ``rescheduledToUid`` are the two ends of the
        move, so one lands here and the other is patched onto the old booking;
        ``rescheduleId`` numbers the move in the chain; ``rescheduleReason`` is
        captured because the research says a third party builds churn alerting from
        it. The old meeting's own link tokens do **not** travel: a link is minted
        for the new booking, so the invite the attendee receives after a reschedule
        points at the meeting that is actually happening.
        """
        data = dict(old.get("data") or {})
        tokens = link_tools.mint_pair(new_uid, factory=self.token_factory)
        start = parse(target["start_at"])
        # The meeting's calendar event moves rather than being duplicated, so the
        # new booking carries the same `event_id` forward. A downstream system
        # subscribed to that event keeps receiving updates after a move instead of
        # having to re-subscribe, which is the practical difference between "the
        # event moved" and "a new event appeared". Derived by the same helper the
        # seam uses, and read before the transaction opens because the write handle
        # cannot read.
        inherited_event = propagation.calendar_event_id_for(
            data, self._calendar_event(str(data.get("uid") or ""), self._chain_root(old))
        )
        payload: dict[str, Any] = {
            "uid": new_uid,
            "meeting_type_id": data.get("meeting_type_id"),
            "title": data.get("title"),
            "host_email": target["host_email"],
            "attendee_name": data.get("attendee_name"),
            "attendee_email": data.get("attendee_email"),
            "start_at": target["start_at"],
            "end_at": target["end_at"],
            "duration_minutes": int((parse(target["end_at"]) - start).total_seconds() // 60),
            "timezone": data.get("timezone") or type_data.get("timezone") or "UTC",
            "location": target["location"],
            "calendar_provider": data.get("calendar_provider"),
            "status": BOOKED,
            "chain_root": str(data.get("chain_root") or data.get("uid") or new_uid),
            "rescheduled_from_uid": str(data.get("uid") or ""),
            "reschedule_id": reschedule_id,
            "reschedule_reason": reason,
            "reschedule_source": source_kind,
            "rescheduled_by": data.get("rescheduled_by"),
            "reschedule_token": tokens[RESCHEDULE],
            "cancel_token": tokens[CANCEL],
            "reminders": propagation.rebase_reminders(data.get("reminders"), target["start_at"]),
        }
        if inherited_event:
            # Only when there is one to inherit. On the very first move the old
            # booking has no calendar event yet, and `move_calendar_event` is about
            # to mint one - so a null here would be a claim that no event exists
            # recorded on the booking that is about to have one. The `event_id` the
            # propagation mints is deterministic from the old uid, so the booking
            # that ends up pointing at it is the one the seam already produced.
            payload["calendar_event_id"] = inherited_event
        # The CRM Event travels the same way, from the same reasoning: one meeting,
        # one Event, updated as the meeting moves.
        inherited_crm = propagation.crm_event_id_for(
            data, self._crm_event(str(data.get("uid") or ""), self._chain_root(old))
        )
        if inherited_crm:
            payload["crm_event_id"] = inherited_crm
        # The series membership travels with the occurrence: it is the same meeting,
        # moved, so a later cancel-all still reaches it.
        for optional in ("recurring_group", "recurrence_index"):
            if data.get(optional) not in (None, ""):
                payload[optional] = data[optional]
        return payload

    def _change_payload(
        self,
        *,
        change_type: str,
        booking: Mapping[str, Any],
        new_booking: Mapping[str, Any] | None,
        room_id: str | None,
        actor_email: str,
        actor_kind: str,
        at: str,
        reschedule_source: str | None = None,
        reschedule_id: int | None = None,
        reason: str | None = None,
        cancellation_reason: str | None = None,
        cancelled_by_email: str | None = None,
        scope: str | None = None,
        via_source: str | None = None,
        cause: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """One Events History row, with the four things the research says it shows.

        "Whenever a meeting is reassigned, we will display who rescheduled it, to
        whom, when, and the rescheduling source (Calendar event, ChiliCal Home, or
        Reschedule Link)." So ``actor_email``/``actor_kind`` is the who,
        ``to_host_email``/``to_attendee_email`` plus the ``to`` block is the to whom,
        ``at`` is the when, and ``reschedule_source`` is the source. They are top
        level rather than nested because a history that has to be unpacked to be
        read is a history nobody reads.
        """
        # Both arguments are stored payloads, not envelopes. Every caller has
        # already unwrapped them, and accepting an envelope here would be silently
        # wrong: `{"data": {...}}.get("data")` yields None, the row lands with
        # every field blank, and nothing raises. The one caller that has a record
        # passes `record["data"]`.
        data = dict(booking or {})
        target = dict(new_booking or {})
        payload: dict[str, Any] = {
            "type": change_type,
            "cause": cause,
            "intent": RESCHEDULE if change_type == CHANGE_RESCHEDULED else CANCEL,
            "booking_uid": str(data.get("uid") or ""),
            "chain_root": str(data.get("chain_root") or data.get("uid") or ""),
            "reschedule_source": reschedule_source,
            "reschedule_id": reschedule_id,
            "reschedule_reason": reason,
            "actor_email": actor_email,
            "actor_kind": actor_kind,
            "at": at,
            "from": {
                "uid": data.get("uid"),
                "start_at": data.get("start_at"),
                "end_at": data.get("end_at"),
                "host_email": data.get("host_email"),
                "attendee_email": data.get("attendee_email"),
                "location": data.get("location"),
                "status": data.get("status"),
            },
            "to": {
                "uid": target.get("uid"),
                "start_at": target.get("start_at"),
                "end_at": target.get("end_at"),
                "host_email": target.get("host_email"),
                "attendee_email": target.get("attendee_email"),
                "location": target.get("location"),
                "status": target.get("status"),
            },
            "to_host_email": target.get("host_email"),
            "to_attendee_email": target.get("attendee_email"),
            "triggers": [propagation.TRIGGER_FOR_CHANGE[change_type]]
            if change_type in propagation.TRIGGER_FOR_CHANGE
            else [],
            "reminders": {
                "before": len(data.get("reminders") or []),
                "after": len(target.get("reminders") or []) if target else 0,
                "recomputed": bool(target),
            },
        }
        if change_type == CHANGE_CANCELLED:
            payload["cancellation_reason"] = cancellation_reason
            payload["cancelled_by_email"] = cancelled_by_email
            payload["scope"] = scope
            payload["via_source"] = via_source
        if request_id is not None:
            payload["reschedule_request_id"] = request_id
        return payload

    def _cancel_one(
        self,
        tx: Any,
        *,
        target: Mapping[str, Any],
        room_id: str | None,
        actor_email: str,
        actor_kind: str,
        reason: str | None,
        scope: str,
        at: str,
        type_data: Mapping[str, Any],
        actor: str | None,
        source: str,
        via_source: str | None = None,
        cause: str | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        """Cancel one booking and propagate it, inside a caller's transaction.

        The one place a cancellation is actually performed, so a series sweep and a
        single cancel cannot drift apart: both go through here, and both get the
        same calendar event, the same ``Delete Event`` decision, the same webhooks
        and the same history row.
        """
        data = dict(target.get("data") or {})
        uid = str(data.get("uid") or "")
        change_type = CHANGE_CANCELLED

        tx.update(
            target["id"],
            {
                "status": CANCELLED,
                "cancelled_at": at,
                "cancelled_by_email": actor_email,
                "cancellation_reason": reason,
                "cancelled_by_kind": actor_kind,
                "cancelled_via": via_source,
                "reminders": propagation.drop_reminders(data.get("reminders")),
            },
            actor=actor,
            source=source,
        )

        change_payload = self._change_payload(
            change_type=change_type,
            booking=data,
            new_booking=None,
            room_id=room_id,
            actor_email=actor_email,
            actor_kind=actor_kind,
            at=at,
            reschedule_source=via_source,
            reschedule_id=data.get("reschedule_id"),
            reason=None,
            cancellation_reason=reason,
            cancelled_by_email=actor_email,
            scope=scope,
            via_source=via_source,
            cause=cause,
        )
        change = tx.create(CHANGE_COLLECTION, change_payload, room_id=room_id, actor=actor, source=source)

        # A cancellation leaves this booking in force - there is no replacement -
        # so the current uid is the booking's own. Passed explicitly rather than
        # defaulted, so the two events cannot drift on which one they point at.
        current_uid = uid
        calendar = propagation.move_calendar_event(
            tx,
            booking=data,
            current_uid=current_uid,
            change_type=change_type,
            start_at=data.get("start_at"),
            end_at=data.get("end_at"),
            change_id=change["id"],
            existing=self._calendar_event(uid, str(data.get("chain_root") or "")),
            room_id=room_id,
            actor=actor,
            source=source,
        )
        crm = propagation.propagate_crm_event(
            tx,
            booking=data,
            current_uid=current_uid,
            change_type=change_type,
            delete_event=delete_event(type_data),
            start_at=data.get("start_at"),
            end_at=data.get("end_at"),
            change_id=change["id"],
            # By chain, because a meeting moved once and then cancelled carries its
            # CRM Event on the *first* booking's uid - the row is keyed to the
            # meeting, and a lookup on the current uid alone would find nothing and
            # report "no CRM Event existed to delete" on a meeting that has one.
            existing=self._crm_event(uid, str(data.get("chain_root") or "")),
            room_id=room_id,
            actor=actor,
            source=source,
        )
        envelopes = propagation.webhook_envelopes(
            change_type=change_type,
            new_booking=None,
            old_booking=data,
            reschedule_id=None,
            cancellation_reason=reason,
            cancelled_by_email=actor_email,
            location=None,
        )
        webhooks = propagation.record_webhooks(
            tx,
            envelopes,
            booking_uid=uid,
            change_id=change["id"],
            room_id=room_id,
            actor=actor,
            source=source,
        )
        notices = propagation.record_notifications(
            tx,
            change_type=change_type,
            booking=data,
            change_id=change["id"],
            channels=propagation.channels_for(type_data),
            room_id=room_id,
            actor=actor,
            source=source,
            at=at,
        )
        tx.update(
            change["id"],
            {
                "calendar": calendar,
                "crm_event": crm,
                "webhooks_sent": [item["webhook"] for item in webhooks],
                "notifications_sent": len(notices),
            },
            actor=actor,
            source=source,
        )
        return change, [item["webhook"] for item in webhooks]


__all__ = [
    "BOOKING_COLLECTION",
    "CHANGE_COLLECTION",
    "MEETING_TYPE_COLLECTION",
    "REQUEST_COLLECTION",
    "MeetingChangeEngine",
    "default_token",
    "default_uid",
]
