"""WF-062: route a requested slot for host approval before confirming.

The researched workflow, in full. An admin turns **requires confirmation** on for
an event type. A prospect or an agent asks for a slot. The booking is created
``PENDING``, carrying a ``oneTimePassword`` and ``requiresConfirmation: true``, a
``BOOKING_REQUESTED`` webhook fires, and the sales room or the rep is notified so
the request can be surfaced. The host then either **confirms** it, and the status
becomes ``ACCEPTED`` and a calendar event is created, or **declines** it with a
reason, and a ``BOOKING_REJECTED`` webhook fires carrying that ``rejectionReason``
back to the attendee and on to the CRM and the room.

The domain is in :mod:`dsr.booking_approval`, which is pure: every researched
rule here - who may decide, which bypass flags are honoured and for whom, what a
pending request does to the host's availability - is a function of its arguments
and is tested without a database. What lives in this module is the three things a
workflow has to take out of shared files: the route table, the mapping from
domain errors to responses, and the demo data.

Hard rule 4: every write below is handed ``f"{router.prefix}..."``, built from
``router.prefix`` so the audit row and the route table cannot drift. A hardcoded
URL inside a domain function is a defect, and the same class of bug has shipped
in this codebase before: a feature's audit log kept naming a path the app had
stopped serving. ``source`` is a *required* keyword on every writing function
here, so omitting it is a ``TypeError`` at the call site rather than an
untraceable row in production.

One handler for the whole error hierarchy. :class:`dsr.booking_approval.ApprovalError`
is this workflow's own type and the base of every refusal in it, and each refusal
carries its own ``status`` and ``code``, so one handler answers 403 for a decision
by somebody who does not own the booking and 409 for a slot somebody already
holds without being told which in a table. ``RecordNotFound`` is deliberately not
claimed: the core app already maps it to 404, and two handlers for one type is a
collision the host refuses.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any, Mapping

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr import booking_approval as approval
from dsr.booking_approval import ApprovalError
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-062-route-a-requested-slot-for-host-approv",
    "ticket": "WF-062",
    "name": "Route a requested slot for host approval before confirming",
    "description": (
        "Hold a requested slot as a PENDING booking that carries a one-time password, surface it "
        "to the rep, and end in a host's decision: confirm, and a calendar event is created; "
        "decline with a reason, and a BOOKING_REJECTED webhook carries that reason to the "
        "attendee while the held slot goes back to the host."
    ),
    "nav": [{"id": "booking-approval", "label": "Slot approval"}],
}

router = APIRouter(prefix="/api/wf-062", tags=["wf062"])


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _approval_error(request: Request, exc: ApprovalError) -> JSONResponse:
    """A domain refusal, answered with the status and code it carries.

    ``refusals`` rides along because a request can fail more than one check - an
    attendee over the limit asking for a slot that is also taken - and a caller
    fixing the first one and being told about the second on the retry is worse
    than being told both now.
    """
    body: dict[str, Any] = {"error": exc.code, "detail": str(exc), "status": exc.status}
    if exc.refusals:
        body["refusals"] = exc.refusals
    return JSONResponse(status_code=exc.status, content=body)


EXCEPTION_HANDLERS = {ApprovalError: _approval_error}


# --------------------------------------------------------------------------- #
# The service over the store
# --------------------------------------------------------------------------- #
#
# Built per request from ``StoreDep`` rather than held on ``app.state``,
# because an ``app.state`` entry is exactly the edit to the shared ``dsr/api.py``
# that the feature host exists to make unnecessary. It holds nothing but the
# store handle and a clock, both constructor arguments, so a test constructs one
# over rows of its own with a clock it controls.


class ApprovalService:
    """The workflow, over one audited store."""

    def __init__(self, store: RecordStore, *, clock=None) -> None:
        self.store = store
        self._clock = clock or approval.now_utc

    def now(self) -> datetime:
        return approval.as_utc(self._clock())

    # -- lookups ----------------------------------------------------------- #

    def _require(self, collection: str, record_id: str, code: str, label: str) -> dict[str, Any]:
        record = self.store.get(record_id)
        if record is None or record["collection"] != collection:
            raise ApprovalError(f"{label} {record_id} not found", code=code, status=404)
        return record

    def event_type(self, room_id: str, event_type_id: str) -> dict[str, Any]:
        """One event type, and it must belong to this room.

        Scoping an event type to its room is the difference between a room-scoped
        route and a room-decorated one: a room id in the path that does not
        constrain the record is a way to read and write another room's
        configuration.
        """
        record = self._require(
            approval.EVENT_TYPES, event_type_id, "unknown_event_type", "event type"
        )
        if record.get("room_id") != room_id:
            raise ApprovalError(
                f"event type {event_type_id} is not in room {room_id}",
                code="unknown_event_type",
                status=404,
            )
        return record

    def booking(self, room_id: str, uid: str) -> dict[str, Any]:
        """One booking by its ``uid``, scoped to this room.

        Looked up through the dynamic index on ``uid`` rather than by a record id,
        because ``uid`` is the identifier the researched API is written in - a
        client following the flow with a ``bookingUid`` should not have to learn
        this product's record ids to use it.
        """
        matches = self.store.find(approval.BOOKING_REQUESTS, {"uid": uid}, limit=10)
        for record in matches:
            if record.get("room_id") == room_id:
                return record
        raise ApprovalError(
            f"booking {uid} not found in room {room_id}", code="unknown_booking", status=404
        )

    def overlapping(
        self,
        event_type: Mapping[str, Any],
        start: datetime,
        end: datetime,
        *,
        exclude: str | None = None,
    ) -> list[dict[str, Any]]:
        """Live bookings for this event type that overlap a span, as payloads.

        Narrowed by ``eventTypeId`` through the dynamic index and then by room,
        status and overlap in Python, because the overlap test is interval
        arithmetic no index can answer. The whole point is the last filter: the
        list of *candidates* is a query, the list of *clashes* is this module's
        own rule.

        The returned dicts are the records' ``data``, not the envelope. The
        domain in :mod:`dsr.booking_approval` is pure and knows nothing about
        the store's envelope, so translating happens here rather than in a
        function that would then be reading ``record["code"]`` for a field that
        lives at ``record["data"]["code"]``.
        """
        candidates = self.store.find(
            approval.BOOKING_REQUESTS, {"eventTypeId": str(event_type.get("id") or "")}, limit=500
        )
        clashes: list[dict[str, Any]] = []
        for record in candidates:
            data = record.get("data") or {}
            if event_type.get("room_id") and record.get("room_id") != event_type.get("room_id"):
                continue
            if str(data.get("uid") or "") == (exclude or ""):
                continue
            if str(data.get("status") or "") not in approval.OCCUPYING_STATUSES:
                continue
            try:
                other_start = approval.parse_moment(data.get("start"), "start")
                other_end = approval.parse_moment(data.get("end"), "end")
            except ApprovalError:
                # A stored booking whose times no longer parse cannot be proved
                # to clash, and one unreadable row must not refuse every new
                # request for the host. It is dropped from the candidate set and
                # the booking itself is left alone.
                continue
            if approval.overlaps(start, end, other_start, other_end):
                clashes.append(data)
        return clashes

    def attendee_bookings(self, email: str, event_type_id: str) -> list[dict[str, Any]]:
        """The attendee's live bookings on one event type, as payloads.

        ``attendee.email`` is a nested JSON path, which is exactly what the
        dynamic index is for: no migration, no typed column, and a team that
        renames or adds an attendee field needs no coordination.
        """
        records = self.store.find(approval.BOOKING_REQUESTS, {"attendee.email": email}, limit=500)
        return [
            dict(record.get("data") or {})
            for record in records
            if str((record.get("data") or {}).get("eventTypeId") or "") == str(event_type_id)
        ]

    def verification_for(
        self, email: str, event_type_id: str, code: str | None = None
    ) -> dict[str, Any] | None:
        """The email-verification record a request's code points at.

        Returns the record's ``data`` or ``None``, for the same reason
        :meth:`overlapping` returns payloads: the domain in
        :mod:`dsr.booking_approval` is pure and knows nothing about the store's
        envelope.

        With no ``code``, the newest *verified* record is returned, which is what
        "is this address already verified for this event type" means and what the
        ``required`` check reports. With a ``code``, the newest record carrying
        *that* code is returned whether or not it has been verified - so the
        domain can tell "no code matches" from "that code was sent but never
        verified", and those two need different things from the caller.

        The sort is on ``created_at`` because that is the insertion sequence the
        store defines (see the note in ``AuditedDatabase.list``), so the answer
        does not depend on which row the query plan reached first.
        """
        where: dict[str, Any] = {"email": email, "eventTypeId": str(event_type_id)}
        if code:
            where["code"] = str(code)
        records = self.store.find(approval.EMAIL_VERIFICATIONS, where, limit=200)
        if code:
            candidates = list(records)
        else:
            candidates = [
                record for record in records if (record.get("data") or {}).get("verifiedAt")
            ]
        if not candidates:
            return None
        candidates.sort(key=lambda record: (record.get("created_at") or "", record["id"]))
        return dict(candidates[-1].get("data") or {})

    # -- event types ------------------------------------------------------- #

    def create_event_type(
        self, room_id: str, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        data = approval.validate_event_type(payload)
        record = self.store.create(
            approval.EVENT_TYPES, data, room_id=room_id, actor=actor, source=source
        )
        # The view, not the raw record: a caller that has just created a type
        # wants to know which gates it actually has open, and returning the bare
        # record would make every client recompute what the server already knows.
        return self.event_type_view(record)

    def list_event_types(self, room_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        records = self.store.list(
            approval.EVENT_TYPES, room_id=room_id, limit=limit, order_by="created_at"
        )
        return [self.event_type_view(record) for record in records]

    def event_type_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """An event type plus the two gates it actually has open."""
        data = record.get("data") or {}
        return {
            **record,
            "requires_confirmation": approval.requires_confirmation(data),
            "email_verification_required": bool(data.get("emailVerification")),
            "bypass_flags": list(approval.BYPASS_FLAGS),
        }

    def patch_event_type(
        self,
        room_id: str,
        event_type_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """The requires-confirmation toggle, and any other field, revalidated.

        The whole payload is revalidated rather than only the keys that were
        sent, because ``validate_event_type`` rejects a ``requiresConfirmation``
        that is not a boolean and a half-applied toggle is a config that reads as
        enabled and is not.
        """
        record = self.event_type(room_id, event_type_id)
        merged = approval.validate_event_type({**(record.get("data") or {}), **dict(payload or {})})
        updated = self.store.update(record["id"], merged, actor=actor, source=source)
        return self.event_type_view(updated)

    # -- email verification ------------------------------------------------ #

    def verification_required(self, room_id: str, event_type_id: str, email: str) -> dict[str, Any]:
        """The researched "Check if email verification is required"."""
        record = self.event_type(room_id, event_type_id)
        data = record.get("data") or {}
        required = bool(data.get("emailVerification"))
        return {
            "eventTypeId": record["id"],
            "email": email,
            "required": required,
            "already_verified": self.verification_for(email, record["id"]) is not None,
            "reason": (
                "Email verification code required when event type has email verification enabled"
                if required
                else "this event type does not have email verification enabled"
            ),
        }

    def send_verification_code(
        self,
        room_id: str,
        event_type_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
        rng: random.Random | None = None,
    ) -> dict[str, Any]:
        """The researched "Send email verification code".

        The code is *recorded*, and the response says so with
        ``dispatched_by: "simulated"``. This product has no mail transport, and a
        response that read like a delivery receipt would be the kind of thing a
        reviewer has to find in a diff rather than being told.

        Sending again replaces the outstanding code for the address rather than
        adding a second live one, so there is never a question about which of two
        codes a request is holding.
        """
        record = self.event_type(room_id, event_type_id)
        email = str((payload or {}).get("email") or "").strip()
        if not email:
            raise ApprovalError("email is required", code="invalid_request", status=422)
        code = approval.mint_verification_code(rng)
        moment = self.now()
        created = self.store.create(
            approval.EMAIL_VERIFICATIONS,
            {
                "email": email,
                "eventTypeId": record["id"],
                "code": code,
                "sentAt": approval.iso(moment),
                "verifiedAt": None,
                "dispatched_by": approval.DISPATCHED_BY,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return {
            "verification": created,
            "code": code,
            "dispatched_by": approval.DISPATCHED_BY,
        }

    def verify_email(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """The researched "Verify email with code".

        Verification is recorded on the record that carries the code rather than
        on a separate fact, so "was this address verified" is answerable from one
        row and a code that was never sent cannot be verified by guessing it.
        """
        body = dict(payload or {})
        code = str(body.get("code") or "").strip()
        if not code:
            raise ApprovalError("code is required", code="invalid_request", status=422)
        event_type_id = str(body.get("eventTypeId") or "").strip()
        where: dict[str, Any] = {"code": code}
        if event_type_id:
            where["eventTypeId"] = event_type_id
        candidates = [
            record
            for record in self.store.find(approval.EMAIL_VERIFICATIONS, where, limit=200)
            if record.get("room_id") == room_id
        ]
        if not candidates:
            raise ApprovalError(
                "no email verification code matches that code in this room",
                code="invalid_verification_code",
                status=422,
            )
        # The newest unverified match, so a re-sent code verifies the code that
        # was actually sent rather than an older one for the same address.
        candidates.sort(key=lambda record: (record.get("created_at") or "", record["id"]))
        target = candidates[-1]
        if (target.get("data") or {}).get("verifiedAt"):
            return {
                "verification": target,
                "already_verified": True,
                "email": (target.get("data") or {}).get("email"),
            }
        moment = self.now()
        updated = self.store.update(
            target["id"],
            {
                "verifiedAt": approval.iso(moment),
                "eventTypeId": event_type_id or (target.get("data") or {}).get("eventTypeId"),
            },
            actor=actor,
            source=source,
        )
        return {
            "verification": updated,
            "already_verified": False,
            "email": (updated.get("data") or {}).get("email"),
        }

    # -- the request path -------------------------------------------------- #

    def request_slot(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
        caller: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """``POST /v2/bookings``: create the booking, with confirmation semantics.

        A booking is either a request or a plain acceptance, and which one is
        decided by the event type's ``requiresConfirmation`` - or overridden for
        this one request, which is the researched flow's second entry ("or the
        booking is created as a request"). Either way the response carries
        ``requiresConfirmation``, ``oneTimePassword``, ``status`` and
        ``rejectionReason``, because that is the ``BookingOutput`` shape the
        research quotes and a client written against it should not have to learn
        a different one.
        """
        body = dict(payload or {})
        requested_type = str(body.get("eventTypeId") or "").strip()
        if not requested_type:
            raise ApprovalError(
                "eventTypeId is required: a slot is requested against an event type",
                code="invalid_request",
                status=422,
            )
        event_type = self.event_type(room_id, requested_type)
        data = dict(event_type.get("data") or {})
        if "requiresConfirmationOverride" in body:
            # The flow's second entry, carried on the request rather than the
            # type. Read in the domain, which only lets it add a request and
            # never remove one, so a caller cannot step over the toggle the admin
            # set; see the inference named `a-per-request-override-can-only-add-approval`.
            data["requiresConfirmationOverride"] = body["requiresConfirmationOverride"]

        start = approval.parse_moment(body.get("start"), "start")
        duration = int(data.get("durationMinutes") or 30)
        end = start + timedelta(minutes=duration)
        attendee = dict(body.get("attendee") or {})
        address = str(attendee.get("email") or "").strip()
        if not address:
            raise ApprovalError("attendee.email is required", code="invalid_request", status=422)

        decision = approval.evaluate_request(
            # The domain scopes its limit check on `id`, which lives on the
            # envelope and not in the payload - so it is folded in here rather
            # than the domain reaching for a record shape it is not given.
            {**data, "id": event_type["id"]},
            now=self.now(),
            start=start,
            attendee=attendee,
            caller=caller,
            api_version=body.get("apiVersion"),
            bypass=body.get("bypass") or {},
            email_verification_code=body.get("emailVerificationCode"),
            verification=self.verification_for(
                address, event_type["id"], body.get("emailVerificationCode")
            ),
            overlapping=self.overlapping(
                {**data, "id": event_type["id"], "room_id": room_id}, start, end
            ),
            attendee_bookings=self.attendee_bookings(address, event_type["id"]),
        )
        approval.raise_refusals(decision["refusals"])

        routing = approval.resolve_routing(
            {**data, "id": event_type["id"]},
            skip_contact_owner=bool(body.get("skipContactOwner")),
            contact_owner=body.get("contactOwnerId") or data.get("contactOwnerId"),
        )
        moment = self.now()
        uid = str(body.get("uid") or _new_uid())
        record_payload: dict[str, Any] = {
            "uid": uid,
            "eventTypeId": event_type["id"],
            "title": data.get("title"),
            "status": decision["status"],
            "requiresConfirmation": decision["requires_confirmation"],
            "requiresConfirmationOverride": body.get("requiresConfirmationOverride"),
            "oneTimePassword": decision["one_time_password"],
            "rejectionReason": None,
            "attendee": attendee,
            "organizer": {
                "name": data.get("hostName"),
                "email": data.get("hostEmail"),
                "timeZone": data.get("timeZone"),
            },
            "booker": body.get("booker") or {"name": attendee.get("name"), "email": address},
            "hostId": data.get("hostId"),
            # The owner's and assigned users are copied onto the booking so a
            # decision can be authorised from the booking alone. The researched
            # rule is "the authorization header refers to the owner of the
            # booking", and reading the event type back to answer it would make
            # a decision depend on a configuration that may have changed since
            # the request was made.
            "ownerId": data.get("ownerId"),
            "assignedUserIds": list(data.get("assignedUserIds") or []),
            "start": approval.iso(start),
            "end": approval.iso(end),
            "timeZone": data.get("timeZone") or body.get("timeZone"),
            "location": data.get("location"),
            "requestedAt": approval.iso(moment),
            "decidedAt": None,
            "decidedBy": None,
            "driver": None,
            "calendarEventId": None,
            "holdsSlot": True,
            "bypasses": decision["bypasses"],
            "checks": decision["checks"],
            "emailVerification": decision["email_verification"],
            "routing": routing,
            "apiVersion": body.get("apiVersion"),
        }
        record = self.store.create(
            approval.BOOKING_REQUESTS, record_payload, room_id=room_id, actor=actor, source=source
        )

        result: dict[str, Any] = {
            "created": True,
            "request": self.booking_view(record),
            "bypasses": decision["bypasses"],
            "checks": decision["checks"],
            "routing": routing,
            "webhooks": [],
            "dispatches": [],
            "calendar_event": None,
        }

        if decision["requires_confirmation"]:
            # A request holds the slot and waits. The webhook fires, and the
            # researched triggers can act on it without anybody opening the room.
            result["webhooks"] = [self._emit_webhook(record, "BOOKING_REQUESTED", source=source)]
            result["dispatches"] = self._dispatch(record, "bookingRequested", source=source)
        else:
            # No confirmation is required, so the booking is already accepted and
            # the calendar event the research says is "created only on confirm"
            # is created here: this booking was accepted on creation.
            result["calendar_event"] = self._create_calendar_event(record, source=source)
        return result

    def _emit_webhook(
        self, record: Mapping[str, Any], event: str, *, source: str
    ) -> dict[str, Any]:
        """Record one researched webhook payload in full.

        ``sequence`` is stored rather than left to the reader's sort: two
        webhooks for one request are written in the same millisecond often enough
        that ordering them by ``created_at`` alone is a coin flip, and a webhook
        log whose order is a coin flip is a log nobody can read back.
        """
        data = dict(record.get("data") or {})
        previous = self.webhooks_for(str(record.get("room_id") or ""), str(data.get("uid") or ""))
        sequence = (
            max((int(entry["data"].get("sequence") or 0) for entry in previous), default=0) + 1
        )
        if event == "BOOKING_REQUESTED":
            payload = approval.booking_requested_payload(data, created_at=approval.iso(self.now()))
        elif event == "BOOKING_REJECTED":
            payload = approval.booking_rejected_payload(data, created_at=approval.iso(self.now()))
        else:  # pragma: no cover - the vocabulary is closed
            raise ApprovalError(
                f"unknown webhook event {event}", code="invalid_request", status=422
            )
        return self.store.create(
            approval.BOOKING_WEBHOOKS,
            {
                "event": event,
                "sequence": sequence,
                "bookingUid": data.get("uid"),
                "status": data.get("status"),
                "rejectionReason": data.get("rejectionReason"),
                "payload": payload,
                "delivered": False,
                "delivery": "recorded",
            },
            room_id=record.get("room_id"),
            actor="system",
            source=source,
        )

    def _dispatch(
        self, record: Mapping[str, Any], trigger: str, *, source: str
    ) -> list[dict[str, Any]]:
        """Fire every enabled rule on this trigger, and record what it would do.

        ``bookingRequested`` "can immediately kick off a rep-facing to-do/SMS/email"
        and ``bookingRejected`` "can trigger re-routing", so a rule is a trigger
        plus the channels to raise. Each firing is its own record, attributed to
        the request that caused it, so a room can see what a request set off.
        """
        room_id = record.get("room_id")
        # Declaration order, not newest first: the order the room's admin wrote
        # the rules in is the order the rep expects their notifications in, and
        # `list` orders by `updated_at` descending by default.
        rules = self.store.list(
            approval.BOOKING_AUTOMATIONS,
            room_id=room_id,
            limit=200,
            order_by="created_at",
            descending=False,
        )
        data = dict(record.get("data") or {})
        routing = data.get("routing") or {}
        sent: list[dict[str, Any]] = []
        for rule in approval.matching_rules(rules, trigger):
            for channel in rule["channels"]:
                recipient = (
                    routing.get("hostId") if channel != "to_do" else routing.get("assignedTo")
                )
                sent.append(
                    self.store.create(
                        approval.BOOKING_DISPATCHES,
                        {
                            "bookingUid": data.get("uid"),
                            "automationId": rule["id"],
                            "trigger": trigger,
                            "channel": channel,
                            "recipient": recipient,
                            "label": rule.get("label"),
                            "dispatched_at": approval.iso(self.now()),
                            "dispatched_by": approval.DISPATCHED_BY,
                        },
                        room_id=room_id,
                        actor="system",
                        source=source,
                    )
                )
        return sent

    def _create_calendar_event(self, record: Mapping[str, Any], *, source: str) -> dict[str, Any]:
        """The calendar event, created only when the booking is accepted."""
        data = dict(record.get("data") or {})
        event = self.store.create(
            approval.BOOKING_EVENTS,
            approval.calendar_event_for(data, uid=_new_uid(), now=self.now()),
            room_id=record.get("room_id"),
            actor="system",
            source=source,
        )
        self.store.update(
            record["id"],
            {"calendarEventId": event["id"]},
            actor="system",
            source=source,
        )
        return event

    # -- the decision path ------------------------------------------------- #

    def decide(
        self,
        room_id: str,
        uid: str,
        decision: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
        caller: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Confirm or decline a request, and everything that follows from it.

        The same call serves both researched routes, because they differ only in
        the word and in what they leave behind: a confirm creates the calendar
        event, a decline fires ``BOOKING_REJECTED`` with the reason and releases
        the held slot. Keeping them on one path is what makes it impossible for
        the two to disagree about who may decide.
        """
        record = self.booking(room_id, uid)
        data = dict(record.get("data") or {})
        outcome = approval.evaluate_decision(
            {**data, "id": record["id"]},
            decision=decision,
            actor=actor,
            driver=str((payload or {}).get("driver") or "host"),
            caller=caller,
            one_time_password=(payload or {}).get("oneTimePassword"),
            reason=(payload or {}).get("reason"),
            api_version=(payload or {}).get("apiVersion"),
            bypass=(payload or {}).get("bypass") or {},
            overlapping=self.overlapping(
                {**data, "id": data.get("eventTypeId"), "room_id": room_id},
                approval.parse_moment(data.get("start"), "start"),
                approval.parse_moment(data.get("end"), "end"),
                exclude=str(data.get("uid") or ""),
            ),
        )

        moment = self.now()
        patch: dict[str, Any] = {
            "status": outcome["status"],
            "decidedAt": approval.iso(moment),
            "decidedBy": outcome["decided_by"],
            "driver": outcome["driver"],
            "holdsSlot": outcome["holds_slot"],
            # A one-time password authorises exactly one decision. It is cleared
            # here so a second decision cannot be made with a credential that has
            # already been spent, and so a stored record stops carrying a secret
            # that no longer grants anything.
            "oneTimePassword": None,
            "authorisation": outcome["authorisation"],
            "bypasses": outcome["bypasses"],
        }
        if decision == "decline":
            patch["rejectionReason"] = outcome["rejection_reason"]
            patch["reasonSupplied"] = outcome["reason_supplied"]
        updated = self.store.update(record["id"], patch, actor=actor, source=source)

        result: dict[str, Any] = {
            "request": self.booking_view(updated),
            "decision": decision,
            "driver": outcome["driver"],
            "decided_by": outcome["decided_by"],
            "authorisation": outcome["authorisation"],
            "bypasses": outcome["bypasses"],
            "slot_released": not outcome["holds_slot"],
            "webhooks": [],
            "dispatches": [],
            "calendar_event": None,
        }
        if decision == "confirm":
            result["calendar_event"] = self._create_calendar_event(updated, source=source)
        else:
            result["webhooks"] = [self._emit_webhook(updated, "BOOKING_REJECTED", source=source)]
            result["rejection_reason"] = outcome["rejection_reason"]
            result["dispatches"] = self._dispatch(updated, "bookingRejected", source=source)
        return result

    # -- views ------------------------------------------------------------- #

    def booking_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A booking as the researched ``BookingOutput`` shape, plus its decisions.

        The four researched output fields are lifted to the top level *and* left
        in ``data``, so a client written against ``BookingOutput`` and one reading
        the record are reading the same thing.
        """
        data = dict(record.get("data") or {})
        return {
            **record,
            "uid": data.get("uid"),
            "status": data.get("status"),
            "api_status": approval.API_STATUS.get(str(data.get("status") or "")),
            "requiresConfirmation": bool(data.get("requiresConfirmation")),
            "oneTimePassword": data.get("oneTimePassword"),
            "rejectionReason": data.get("rejectionReason"),
            "pending": data.get("status") == approval.DECIDABLE_STATUS,
            "holdsSlot": bool(data.get("holdsSlot")),
            "decidable": data.get("status") == approval.DECIDABLE_STATUS,
        }

    def list_requests(
        self,
        room_id: str,
        *,
        status: str | None = None,
        event_type_id: str | None = None,
        host_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Requests in a room, newest first, filtered through the dynamic index.

        Each filter is a JSON path in the record's own payload, so filtering by
        status, event type or host needs no column and no migration.
        """
        where: dict[str, Any] = {}
        if status:
            where["status"] = status
        if event_type_id:
            where["eventTypeId"] = event_type_id
        if host_id:
            where["hostId"] = host_id
        if where:
            records = self.store.find(approval.BOOKING_REQUESTS, where, limit=min(limit, 1000))
            scoped = [record for record in records if record.get("room_id") == room_id]
        else:
            scoped = self.store.list(
                approval.BOOKING_REQUESTS, room_id=room_id, limit=min(limit, 1000)
            )
        return [self.booking_view(record) for record in scoped[:limit]]

    def webhooks_for(self, room_id: str, uid: str) -> list[dict[str, Any]]:
        """The webhook payloads a request emitted, oldest first.

        Ordered by the stored ``sequence`` rather than by ``created_at``: two
        webhooks for one request are often written in the same millisecond, and a
        tie broken by a random uuid is a coin flip, not an order.
        """
        records = self.store.find(approval.BOOKING_WEBHOOKS, {"bookingUid": uid}, limit=200)
        scoped = [record for record in records if record.get("room_id") == room_id]
        scoped.sort(
            key=lambda record: (
                int((record.get("data") or {}).get("sequence") or 0),
                record.get("created_at") or "",
                record["id"],
            )
        )
        return scoped

    def calendar_event_for(self, room_id: str, uid: str) -> dict[str, Any] | None:
        """The calendar event for a request, or ``None`` - which is the point.

        The research says the event is "created only on confirm", so a pending or
        declined request answering ``None`` here is the researched behaviour
        rather than a missing feature.
        """
        record = self.booking(room_id, uid)
        event_id = (record.get("data") or {}).get("calendarEventId")
        if not event_id:
            return None
        return self.store.get(event_id)

    def summary(self, room_id: str) -> dict[str, Any]:
        """Counts by status, and what is waiting on a human."""
        records = self.store.list(approval.BOOKING_REQUESTS, room_id=room_id, limit=1000)
        by_status: dict[str, int] = {status: 0 for status in approval.STATUSES}
        holds = 0
        for record in records:
            status = str((record.get("data") or {}).get("status") or "")
            if status in by_status:
                by_status[status] += 1
            if (record.get("data") or {}).get("holdsSlot"):
                holds += 1
        return {
            "room_id": room_id,
            "counts": by_status,
            "requests": len(records),
            "pending": by_status.get("PENDING", 0),
            "awaiting_a_host": by_status.get("PENDING", 0),
            "held_slots": holds,
            "event_types": _room_count(self.store, approval.EVENT_TYPES, room_id),
            "automations": _room_count(self.store, approval.BOOKING_AUTOMATIONS, room_id),
            "dispatches": _room_count(self.store, approval.BOOKING_DISPATCHES, room_id),
            "webhooks": _room_count(self.store, approval.BOOKING_WEBHOOKS, room_id),
            "calendar_events": _room_count(self.store, approval.BOOKING_EVENTS, room_id),
        }

    # -- automations ------------------------------------------------------- #

    def create_automation(
        self, room_id: str, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        return self.store.create(
            approval.BOOKING_AUTOMATIONS,
            approval.validate_automation(payload),
            room_id=room_id,
            actor=actor,
            source=source,
        )

    def list_automations(self, room_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        return self.store.list(
            approval.BOOKING_AUTOMATIONS, room_id=room_id, limit=limit, order_by="created_at"
        )


def get_service(store: RecordStore = StoreDep) -> ApprovalService:
    return ApprovalService(store)


ServiceDep = Depends(get_service)


def _caller(
    actor: str | None, user_id: str | None, authenticated: bool, roles: str | None
) -> dict[str, Any]:
    """Assemble the caller the domain asks about.

    This product has no session layer, so who is calling is what the route is
    told. Every field is optional and its absence is meaningful: no ``userId``
    means nobody could be matched to a role even if roles were claimed, and
    ``authenticated=False`` is honoured exactly where the research puts it - on
    a bypass, not on a decision. See ``inferences`` for why that asymmetry is
    deliberate.
    """
    return {
        "userId": user_id or actor,
        "authenticated": authenticated,
        "roles": [name.strip() for name in (roles or "").split(",") if name.strip()],
    }


def _new_uid() -> str:
    return approval.mint_one_time_password()


def _room_count(store: RecordStore, collection: str, room_id: str) -> int:
    """How many live records of a collection are in a room.

    ``RecordStore`` exposes ``count_where`` but not ``count``, and ``count_where``
    takes no ``room_id`` - room scoping lives in the envelope's own column, not
    in the payload. So this reads the ids, which is exact for a collection the
    size these are and needs no new method on a shared file to be exact.
    """
    return len(store.list(collection, room_id=room_id, limit=1000))


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="Every published vocabulary")
def vocabulary() -> dict[str, Any]:
    """Statuses, webhook events, triggers, channels, bypass flags and roles.

    Served as data so a client renders its pickers from the server's vocabulary
    rather than from a list compiled into a page, and a value added here reaches
    every client at once.
    """
    return approval.vocabulary()


@router.get("/capabilities", summary="The researched API surface, and what of it is here")
def capabilities() -> dict[str, Any]:
    """Which researched ``apis_hit`` this feature implements, and where."""
    return approval.capabilities()


@router.get("/inferences", summary="Every judgement call this workflow rests on")
def inferences() -> dict[str, Any]:
    """What the research fixes, what it leaves open, and which reading was taken."""
    return approval.inferences()


# --------------------------------------------------------------------------- #
# Event types
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/event-types", summary="Event types in a room")
def list_event_types(room_id: str, service: ApprovalService = ServiceDep) -> dict[str, Any]:
    """The event types a room can be asked to book against."""
    listed = service.list_event_types(room_id)
    return {"room_id": room_id, "count": len(listed), "event_types": listed}


@router.post("/rooms/{room_id}/event-types", status_code=201, summary="Create an event type")
def create_event_type(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    service: ApprovalService = ServiceDep,
) -> dict[str, Any]:
    """Declare an event type: its host, its length, and its two gates.

    ``requiresConfirmation`` is the toggle the admin enables in step one of the
    researched flow and ``emailVerification`` is the separate optional gate.
    Both default to off, so a room that has not configured anything books
    directly rather than waiting for a host nobody was told about.
    """
    return service.create_event_type(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/event-types"
    )


@router.get("/rooms/{room_id}/event-types/{event_type_id}", summary="One event type")
def read_event_type(
    room_id: str, event_type_id: str, service: ApprovalService = ServiceDep
) -> dict[str, Any]:
    return {"event_type": service.event_type_view(service.event_type(room_id, event_type_id))}


@router.patch("/rooms/{room_id}/event-types/{event_type_id}", summary="Turn a gate on or off")
def patch_event_type(
    room_id: str,
    event_type_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    service: ApprovalService = ServiceDep,
) -> dict[str, Any]:
    """The requires-confirmation toggle, and any other field, revalidated.

    The researched flow's step one is this call, and it is a merge patch so
    turning one gate on does not silently reset the fields the caller did not
    mention.
    """
    return service.patch_event_type(
        room_id,
        event_type_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/rooms/{room_id}/event-types/{event_type_id}",
    )


# --------------------------------------------------------------------------- #
# The email-verification triad
# --------------------------------------------------------------------------- #


@router.get(
    "/rooms/{room_id}/email-verification/required",
    summary="Check if email verification is required",
)
def verification_required(
    room_id: str,
    event_type_id: str = Query(default="", description="The event type to check against"),
    email: str = Query(default="", description="The address that would book"),
    service: ApprovalService = ServiceDep,
) -> dict[str, Any]:
    """The researched "Check if email verification is required".

    A ``GET`` because it checks and writes nothing, which is also what the
    research's own ``GET/POST /v2/bookings/email-verification/...`` triad says:
    the GET asks, the POSTs send and verify. Making it a POST would have added a
    ninth write-shaped route that is not one, and a client that counted the
    write routes would be wrong.

    Room-scoped rather than global because it resolves against an event type, and
    the event type is a room's: whether verification is required is a property of
    this room's configuration and not of the address.
    """
    return service.verification_required(room_id, event_type_id, email)


@router.post(
    "/rooms/{room_id}/email-verification/send",
    status_code=201,
    summary="Send an email verification code",
)
def send_verification_code(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    service: ApprovalService = ServiceDep,
) -> dict[str, Any]:
    """The researched "Send email verification code".

    The code comes back in the response and is marked ``dispatched_by:
    "simulated"``: this product has no mail transport, and a caller that had to
    guess the code out of a real inbox could not build the flow at all.
    """
    return service.send_verification_code(
        room_id,
        str(payload.get("eventTypeId") or ""),
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/email-verification/send",
    )


@router.post("/rooms/{room_id}/email-verification/verify", summary="Verify an email with a code")
def verify_email(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    service: ApprovalService = ServiceDep,
) -> dict[str, Any]:
    """The researched "Verify email with code"."""
    return service.verify_email(
        room_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/email-verification/verify",
    )


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/requests", status_code=201, summary="Request a slot")
def request_slot(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    caller: str | None = Query(default=None, description="The caller's userId"),
    authenticated: bool = Query(default=True),
    roles: str | None = Query(default=None, description="Comma-separated declared roles"),
    api_version: str | None = Query(default=None, alias="apiVersion"),
    service: ApprovalService = ServiceDep,
) -> dict[str, Any]:
    """``POST /v2/bookings``: request a slot on an event type.

    The researched step two. Depending on the event type's
    ``requiresConfirmation`` this either creates a ``PENDING`` request that
    carries a ``oneTimePassword``, fires ``BOOKING_REQUESTED`` and waits for a
    host, or accepts the booking immediately and creates its calendar event -
    the research's "or the booking is created as a request" entry.

    ``bypass`` may carry ``allowConflicts``, ``allowBookingOutOfBounds`` and
    ``skipBookingLimits``. They are honoured only for an authenticated caller
    holding one of the five researched roles on one of the two named API
    versions, and an unentitled one is ignored with the reason returned in
    ``bypasses.ignored`` rather than refused.
    """
    body = dict(payload or {})
    if api_version and not body.get("apiVersion"):
        body["apiVersion"] = api_version
    return service.request_slot(
        room_id,
        body,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/requests",
        caller=_caller(actor, caller, authenticated, roles),
    )


@router.get("/rooms/{room_id}/requests", summary="Requests in a room")
def list_requests(
    room_id: str,
    status: str | None = Query(default=None),
    event_type_id: str | None = Query(default=None),
    host_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    service: ApprovalService = ServiceDep,
) -> dict[str, Any]:
    """Every request, filterable by status, event type or host.

    The filters are JSON paths in the record's own payload, resolved through the
    dynamic index, so a team that adds its own field can filter on it here too
    without a migration.
    """
    listed = service.list_requests(
        room_id, status=status, event_type_id=event_type_id, host_id=host_id, limit=limit
    )
    return {"room_id": room_id, "count": len(listed), "requests": listed}


@router.get("/rooms/{room_id}/requests/{uid}", summary="One request")
def read_request(room_id: str, uid: str, service: ApprovalService = ServiceDep) -> dict[str, Any]:
    """One request, by the ``uid`` the researched API calls a ``bookingUid``."""
    return {"request": service.booking_view(service.booking(room_id, uid))}


@router.post("/rooms/{room_id}/requests/{uid}/confirm", summary="Confirm a request")
def confirm_request(
    room_id: str,
    uid: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    caller: str | None = Query(default=None, description="The caller's userId"),
    authenticated: bool = Query(default=True),
    roles: str | None = Query(default=None, description="Comma-separated declared roles"),
    api_version: str | None = Query(default=None, alias="apiVersion"),
    service: ApprovalService = ServiceDep,
) -> dict[str, Any]:
    """``POST /v2/bookings/{bookingUid}/confirm``.

    The researched step four. The status becomes ``ACCEPTED`` and, because a
    calendar event is "created only on confirm", one is created here.

    "The provided authorization header refers to the owner of the booking", so
    the caller must be the host, the event owner, an assigned user, a team admin
    or an organisation admin - or present the request's one-time password, which
    the research issues without saying what it authorises (see ``inferences``).

    ``driver=unattended`` is the researched unattended approval and is refused
    unless the caller is entitled to a bypass it actually asked for.
    """
    body = dict(payload or {})
    if api_version and not body.get("apiVersion"):
        body["apiVersion"] = api_version
    return service.decide(
        room_id,
        uid,
        "confirm",
        body,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/requests/{uid}/confirm",
        caller=_caller(actor, caller, authenticated, roles),
    )


@router.post("/rooms/{room_id}/requests/{uid}/decline", summary="Decline a request")
def decline_request(
    room_id: str,
    uid: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    caller: str | None = Query(default=None, description="The caller's userId"),
    authenticated: bool = Query(default=True),
    roles: str | None = Query(default=None, description="Comma-separated declared roles"),
    api_version: str | None = Query(default=None, alias="apiVersion"),
    service: ApprovalService = ServiceDep,
) -> dict[str, Any]:
    """``POST /v2/bookings/{bookingUid}/decline``.

    The researched step four, the other branch. The status becomes ``REJECTED``,
    a ``BOOKING_REJECTED`` webhook fires carrying the ``rejectionReason``, the
    attendee is notified, and the slot the request was holding is released.

    The reason is optional in the research's own words, so a decline with no
    reason records the researched default - "The organizer is no longer
    available at this time." - rather than sending the attendee an empty field.
    """
    body = dict(payload or {})
    if api_version and not body.get("apiVersion"):
        body["apiVersion"] = api_version
    return service.decide(
        room_id,
        uid,
        "decline",
        body,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/requests/{uid}/decline",
        caller=_caller(actor, caller, authenticated, roles),
    )


@router.get("/rooms/{room_id}/requests/{uid}/webhooks", summary="The webhooks a request emitted")
def request_webhooks(
    room_id: str, uid: str, service: ApprovalService = ServiceDep
) -> dict[str, Any]:
    """``BOOKING_REQUESTED`` and ``BOOKING_REJECTED`` payloads, in full.

    Both are stored rather than delivered, so this route is how a reviewer sees
    the exact payload the researched evidence quotes.
    """
    listed = service.webhooks_for(room_id, uid)
    return {"room_id": room_id, "uid": uid, "count": len(listed), "webhooks": listed}


@router.get(
    "/rooms/{room_id}/requests/{uid}/calendar-event", summary="The calendar event for a request"
)
def request_calendar_event(
    room_id: str, uid: str, service: ApprovalService = ServiceDep
) -> dict[str, Any]:
    """The calendar event, or ``null`` when the booking has not been confirmed.

    ``null`` is the researched answer for a pending or declined request, not a
    missing feature: a calendar event is created only on confirm.
    """
    return {
        "room_id": room_id,
        "uid": uid,
        "calendar_event": service.calendar_event_for(room_id, uid),
    }


# --------------------------------------------------------------------------- #
# Automations and the summary
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/automations", summary="Workflow rules in a room")
def list_automations(room_id: str, service: ApprovalService = ServiceDep) -> dict[str, Any]:
    """The rules a request or a decline can fire without anybody opening the room."""
    listed = service.list_automations(room_id)
    return {"room_id": room_id, "count": len(listed), "automations": listed}


@router.post("/rooms/{room_id}/automations", status_code=201, summary="Add a workflow rule")
def create_automation(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    service: ApprovalService = ServiceDep,
) -> dict[str, Any]:
    """Key a rep-facing action on ``bookingRequested`` or ``bookingRejected``.

    The researched automations note: a request "can immediately kick off a
    rep-facing to-do/SMS/email" and a rejection "can trigger re-routing". A rule
    is a trigger plus the channels to raise on it; each firing is recorded.
    """
    return service.create_automation(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/automations"
    )


@router.get("/rooms/{room_id}/summary", summary="What is waiting on a host")
def room_summary(room_id: str, service: ApprovalService = ServiceDep) -> dict[str, Any]:
    """Counts by status, and the number of slots a pending request is holding.

    ``awaiting_a_host`` is the number a rep actually acts on, which is why it is
    named separately from ``pending``: the two are equal today, and a client that
    reads the second one keeps working if that ever stops being true.
    """
    return service.summary(room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #
#
# The branch added this to ``backend/seed.py``, which is a shared file that ten of
# the first twelve workflows rewrote purely to add their own rows. The seeder
# calls the hook below instead, so the demo travels with the feature that needs
# it.


def _demo_rooms(rooms: list[Any]) -> list[tuple[str, str]]:
    return [entry if isinstance(entry, (tuple, list)) else (entry, "") for entry in rooms]


def seed(db, context: dict[str, Any]) -> str:
    """Seed the states the research says matter - not just the happy path.

    Everything is produced by driving the real :class:`ApprovalService` over the
    demo rooms, so the demo's statuses, webhook payloads, holds, dispatches and
    calendar events are what this workflow actually writes rather than rows
    composed by hand. A seeded booking and an API booking cannot disagree.

    What is here, and why each is here:

    * a **pending request** holding its slot - the state the whole ticket is
      about, and the only one a reviewer can act on without clicking;
    * a **confirmed** request with its calendar event, so "created only on
      confirm" is visible as a created row rather than as an absence;
    * a **declined** request carrying the researched ``rejectionReason`` with its
      ``BOOKING_REJECTED`` payload - and the same slot requested again
      immediately afterwards, which is the visible proof that a decline released
      the hold;
    * a **booking created without confirmation**, already accepted with a
      calendar event, so the research's second entry ("or the booking is created
      as a request") is on screen next to the first;
    * an event type with **email verification on** and a code genuinely sent and
      verified, so the separate optional gate is a row in the demo;
    * two event types with the **bounds and limit** configured (notice, range, a
      per-attendee limit), so ``allowBookingOutOfBounds`` and
      ``skipBookingLimits`` have something to act on and a reviewer can see why
      a request was refused;
    * two **workflow rules** per room, one per trigger, each with its dispatch
      recorded against the request that caused it.

    Returns a string the seeder prints, and the counts in it are what the
    workflow actually wrote rather than what was intended.
    """
    rooms = _demo_rooms(context.get("room_ids") or [])
    rng: random.Random = context.get("rng") or random.Random("wf062")
    base: datetime = context.get("now") or approval.now_utc()
    source = "seed"
    actor = "dana"

    from dsr.store import RecordStore

    service = ApprovalService(RecordStore(db), clock=lambda: approval.as_utc(base))

    if not rooms:
        return "0 event types, 0 requests (no demo rooms to scope a request to)"

    # One plan per state the research distinguishes. Each plan carries the event
    # type it needs, and the event type is created *in* the room that will hold
    # the request: room scoping is a real constraint on this feature's routes, so
    # a demo row outside its room would be a row the demo cannot show.
    plans: tuple[dict[str, Any], ...] = (
        {
            "state": "pending",
            "summary": "1 pending request holding its slot",
            "event_type": {
                "title": "Discovery call",
                "hostId": "dana",
                "hostName": "Dana Okafor",
                "hostEmail": "dana@example.com",
                "ownerId": "dana",
                "durationMinutes": 30,
                "requiresConfirmation": True,
                "timeZone": "Australia/Sydney",
            },
        },
        {
            "state": "confirmed",
            "summary": "1 confirmed request with its calendar event",
            "verify_email": True,
            "event_type": {
                "title": "Technical deep dive",
                "hostId": "sam",
                "hostName": "Sam Rivera",
                "hostEmail": "sam@example.com",
                "ownerId": "sam",
                "assignedUserIds": ["priya"],
                "durationMinutes": 60,
                "requiresConfirmation": True,
                "emailVerification": True,
                "timeZone": "Australia/Sydney",
            },
            "decide": "confirm",
            "decider": "sam",
        },
        {
            "state": "declined",
            "summary": "1 declined request with BOOKING_REJECTED, and the released slot re-requested",
            "event_type": {
                "title": "Renewal walkthrough",
                "hostId": "priya",
                "hostName": "Priya Nair",
                "hostEmail": "priya@example.com",
                "ownerId": "priya",
                "durationMinutes": 45,
                "requiresConfirmation": True,
                "minimumNoticeMinutes": 60,
                "maximumRangeDays": 90,
            },
            "decide": "decline",
            "decider": "priya",
            "re_request": True,
        },
        {
            "state": "accepted_directly",
            "summary": "1 booking accepted on creation, with a per-attendee limit configured",
            "event_type": {
                "title": "Intro call (no approval needed)",
                "hostId": "dana",
                "hostName": "Dana Okafor",
                "hostEmail": "dana@example.com",
                "ownerId": "dana",
                "durationMinutes": 15,
                "requiresConfirmation": False,
                "bookingLimitPerAttendee": 1,
            },
        },
    )

    # A workflow rule per trigger, in every room a plan touches, so a request and
    # a decline both have somewhere to go without a person opening the room.
    automations: tuple[tuple[str, list[str], str], ...] = (
        ("bookingRequested", ["to_do", "email"], "Surface the request to the rep"),
        ("bookingRejected", ["to_do"], "Re-route a declined slot"),
    )

    names = ("Priya Shah", "Marcus Bell", "Elena Duarte", "Tomas Novak")
    touched: list[str] = []
    outcomes: list[str] = []
    requests_made = 0
    verified = 0

    for index, plan in enumerate(plans):
        room_id = str(rooms[index % len(rooms)][0])
        if room_id not in touched:
            touched.append(room_id)
            for trigger, channels, label in automations:
                service.create_automation(
                    room_id,
                    {"trigger": trigger, "channels": channels, "label": label},
                    actor=actor,
                    source=source,
                )

        event_type = service.create_event_type(
            room_id, plan["event_type"], actor=actor, source=source
        )
        address = f"buyer{index + 1}@example.com"
        when = base + timedelta(days=2 + index, hours=index + 1)

        body: dict[str, Any] = {
            "eventTypeId": event_type["id"],
            "start": approval.iso(when),
            "attendee": {
                "name": names[index % len(names)],
                "email": address,
                "phoneNumber": f"+61400{index:06d}",
            },
            "contactOwnerId": "dana" if index % 2 == 0 else None,
        }

        # The event type with the gate on gets a real code, sent and verified
        # through the same two calls a prospect would make, so the separate
        # optional gate is a row in the demo rather than a paragraph in a
        # docstring.
        if plan.get("verify_email"):
            sent = service.send_verification_code(
                room_id, event_type["id"], {"email": address}, actor=actor, source=source, rng=rng
            )
            service.verify_email(
                room_id,
                {"email": address, "code": sent["code"], "eventTypeId": event_type["id"]},
                actor=actor,
                source=source,
            )
            body["emailVerificationCode"] = sent["code"]
            verified += 1

        request = service.request_slot(
            room_id,
            body,
            actor=actor,
            source=source,
            caller={"userId": "buyer", "authenticated": False, "roles": []},
        )
        requests_made += 1
        uid = str(request["request"]["uid"])

        if plan.get("decide"):
            decider = str(plan["decider"])
            payload: dict[str, Any] = {}
            if plan["decide"] == "decline":
                payload["reason"] = approval.DEFAULT_REJECTION_REASON
            service.decide(
                room_id,
                uid,
                str(plan["decide"]),
                payload,
                actor=decider,
                source=source,
                caller={"userId": decider, "authenticated": True, "roles": []},
            )
            if plan.get("re_request"):
                # The decline released the hold, so the same slot can be asked
                # for again. Seeded so the release is visible as a fact rather
                # than as a claim in a docstring.
                service.request_slot(
                    room_id,
                    {
                        "eventTypeId": event_type["id"],
                        "start": approval.iso(when),
                        "attendee": {"name": names[index % len(names)], "email": address},
                    },
                    actor=actor,
                    source=source,
                )
                requests_made += 1
        outcomes.append(str(plan["summary"]))

    return (
        f"{len(plans)} event types across {len(touched)} room(s), "
        f"{len(automations)} automations per room, {requests_made} bookings "
        f"({'; '.join(outcomes)}), {verified} verified email"
    )
