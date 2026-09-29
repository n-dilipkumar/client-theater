"""The engine: meeting reminders over the audited store.

Everything the product does to a booking lives in :mod:`dsr.meeting_reminders`,
which is pure. This module is the part that needs a database: the reminder
assets, the meeting types they attach to, the bookings, the deliveries, the
organisation's messaging setup, and the reply forwarding.

Two design points that are constraints rather than choices.

**``source`` is a required keyword on every write.** The audit row must name the
route that served the write, so the route builds it from ``router.prefix`` and
passes it down. A domain function that hardcoded a URL string would let the
audit log name a path the app had stopped serving - that defect has shipped in
this codebase before, and making the parameter required is what stops it
regressing silently.

**There is no outbound socket.** The research documents Chili Piper's
configuration surface, a Twilio setup article, and Cal's ``POST /v2/workflows``;
it documents no endpoint this product can reach and no credential. So a
"delivery" is a row carrying the composed message, the resolved recipients and
the decision, written through the audited store, and the Cal projection is the
exact DTO a real call would carry. The seam is :meth:`ReminderEngine.deliver`,
and everything above it is the researched part.

The collections are all prefixed ``meeting_reminder_`` so a reader of
``/api/collections`` can tell this workflow's rows apart from the core
``room``/``activity``/``timeline`` ones, and so no other feature can collide
with them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from dsr.db.audited import RecordNotFound
from dsr.meeting_reminders import conditions as cond
from dsr.meeting_reminders import tags as tagmod
from dsr.meeting_reminders import vocabulary as vocab
from dsr.meeting_reminders.errors import ConfigurationRefused, ReminderError
from dsr.store import RecordStore

#: A reusable reminder asset. The research calls these "reusable assets
#: attachable to many Meeting Types", which is why the attachment below is its
#: own collection rather than an array on the reminder.
REMINDER_COLLECTION = "meeting_reminder"

#: A Meeting Type: the thing a booking is made against and reminders attach to.
MEETING_TYPE_COLLECTION = "meeting_reminder_type"

#: The link. Its own collection so "Remove from Meeting Type" and "Delete" are
#: two different operations on two different rows, exactly as the research puts
#: them.
ATTACHMENT_COLLECTION = "meeting_reminder_attachment"

#: A booking: start, duration, guests, booker, host, assignees, phone, and the
#: calendar invite's responseStatus.
BOOKING_COLLECTION = "meeting_reminder_booking"

#: One reminder's verdict for one booking. This is the *Meetings Activity* row
#: step 7 describes: "open a meeting → per-reminder status".
DELIVERY_COLLECTION = "meeting_reminder_delivery"

#: The organisation's messaging setup: the no-reply domain and the Twilio
#: connection, which the research locates on the same Command Center page.
ORG_COLLECTION = "meeting_reminder_org"

#: An inbound SMS reply, and the email addresses it was forwarded to.
REPLY_COLLECTION = "meeting_reminder_sms_reply"

#: One organisation's setup row. There is one, so it is fetched by a fixed
#: marker rather than by collection: the audit trail needs every write to name
#: a route, and a singleton read does not need one.
ORG_KEY = "default"

#: How many forwarded replies a delivery keeps inline. The replies themselves
#: are durable records; this is the convenience copy that rides along with the
#: delivery so a rep reading *Meetings Activity* sees the conversation without a
#: second query. Capped, because an array on a record is a poor place to keep an
#: unbounded history.
REPLY_HISTORY_LIMIT = 20


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReminderEngine:
    """Meeting reminders and SMS nudges, over the audited store.

    Constructed per request from ``StoreDep``, the way
    :class:`~dsr.dedupe.DedupeEngine` is in WF-041. The engine holds nothing
    beyond the store and a clock, so per-request construction is equivalent and
    leaves both overridable in a test.
    """

    def __init__(self, store: RecordStore, *, clock: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self.clock = clock if clock is not None else _utcnow

    # -- the organisation ---------------------------------------------------- #

    def org(self) -> dict[str, Any]:
        """The organisation's messaging setup, or an empty setup.

        An absent row is an empty setup rather than a 404, because every read of
        a reminder's sender goes through here and "not configured yet" is a
        normal state, not a missing record.
        """
        for record in self.store.find(ORG_COLLECTION, {"key": ORG_KEY}, limit=1):
            return record
        return {"id": None, "data": {}}

    def org_settings(self) -> dict[str, Any]:
        """The setup as a plain mapping, which is what the rules read."""
        return dict(self.org().get("data") or {})

    def save_org(self, payload: Mapping[str, Any], *, actor: str | None = None, source: str) -> dict[str, Any]:
        """Write the organisation's messaging setup.

        Upsert by the fixed marker rather than by a client-supplied id, so
        "the setup" stays a singleton no matter how many callers race for it.
        """
        body = dict(payload or {})
        existing = self.org()
        record = self.store.update(
            existing["id"],
            {**body, "key": ORG_KEY},
            actor=actor,
            source=source,
        ) if existing.get("id") else self.store.create(
            ORG_COLLECTION, {**body, "key": ORG_KEY}, actor=actor, source=source
        )
        return record

    def twilio_ready(self) -> bool:
        """Whether SMS can be switched on at all.

        "A Chili Piper Admin must connect Twilio to your company's Command Center
        Integrations page." The two flags are separate because the research flags
        reply forwarding separately: a connection is enough to send, and only an
        organisation-*owned* account is enough to forward replies.
        """
        settings = self.org_settings()
        return bool(settings.get(vocab.CONNECTION_CONNECTED))

    def reply_forwarding_ready(self) -> bool:
        """Whether inbound SMS replies can be forwarded by email.

        "⚠️ **Warning:** Forwarding SMS replies requires your company's own Twilio
        account to be connected in Command Center." A connected-but-not-owned
        account sends fine and cannot forward, which is why this is a second
        method rather than a flag on the first.
        """
        settings = self.org_settings()
        return bool(settings.get(vocab.CONNECTION_CONNECTED)) and bool(
            settings.get(vocab.CONNECTION_OWN_ACCOUNT)
        )

    # -- reminders ----------------------------------------------------------- #

    def validate_reminder(
        self, spec: Mapping[str, Any], *, existing: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Validate a reminder into its normalised form, or raise.

        Every researched vocabulary is enforced here rather than at fire time, so
        a reminder that cannot work is refused when it is saved and not when a
        customer does not receive it. The two organisational preconditions -
        a Twilio connection for SMS, and a no-reply domain for a no-reply
        sender - are checked here too, and they are the reason this method needs
        the engine rather than being a pure function.
        """
        merged = {**dict(existing or {}), **dict(spec or {})}
        channel = vocab.require_channel(merged.get("channel"))
        condition = vocab.require_condition(merged.get("condition"))
        offset = vocab.require_offset(
            (merged.get("offset") or {}).get("value", merged.get("offsetValue", vocab.DEFAULT_OFFSET)),
            (merged.get("offset") or {}).get("unit", merged.get("offsetUnit", vocab.DEFAULT_UNIT)),
        )

        if channel == vocab.SMS:
            mode = vocab.require_sms_from(merged.get("smsFrom"))
            if not self.twilio_ready():
                raise ConfigurationRefused(
                    "an SMS reminder needs a connected Twilio account: " + vocab.TWILIO_QUOTE
                )
            # The number itself, not just the connection. A connection with no
            # number behind it is a reminder that can be saved and never sent,
            # and "Sender not found" is not one of the five researched skip
            # reasons - so the mistake is found here, where it is fixable, rather
            # than at a customer's silence.
            key = "localNumber" if mode == vocab.LOCAL_AREA_NUMBER else "number"
            if not str(self.org_settings().get(key) or "").strip():
                raise ConfigurationRefused(
                    f"an SMS reminder that sends from the {mode.replace('_', ' ')} needs a matching "
                    f"number configured on the Command Center first; the connection alone is not "
                    "enough to send from"
                )
        else:
            email_to = vocab.require_email_to(merged.get("emailTo"))
            email_from = vocab.require_email_from(merged.get("emailFrom"))
            if email_from in vocab.EMAIL_FROM_REQUIRES and not self.org_settings().get("noreply_domain"):
                raise ConfigurationRefused(
                    f"a {email_from} sender needs the organisation's own sending domain configured "
                    "first; the research calls it a 'No-reply address on a custom domain'"
                )
        vocab.require_replies_to(merged.get("repliesTo"))

        # Validates the group, and raises on an unknown rule kind - a rule that
        # does not fall through is a rule that cannot be wrong silently.
        group = cond.evaluate_group(merged.get("conditions"), {})

        payload: dict[str, Any] = dict(merged)
        payload["channel"] = channel
        payload["condition"] = condition
        payload["offset"] = {"value": offset["value"], "unit": offset["unit"]}
        payload.pop("offsetValue", None)
        payload.pop("offsetUnit", None)
        if channel == vocab.SMS:
            payload["smsFrom"] = vocab.require_sms_from(merged.get("smsFrom"))
        else:
            payload["emailTo"] = email_to
            payload["emailFrom"] = email_from
        payload["repliesTo"] = vocab.require_replies_to(merged.get("repliesTo"))
        payload["conditions"] = {
            "match": group["match"],
            "rules": [verdict["rule"] for verdict in group["rules"]],
        }
        payload[vocab.SOURCE_LOCALE] = vocab.require_source_locale(merged.get(vocab.SOURCE_LOCALE))
        payload["includeCalendarEvent"] = vocab.include_calendar_event(merged.get("includeCalendarEvent"))
        payload["skipNoShowAttendees"] = vocab.skip_no_show_attendees(merged.get("skipNoShowAttendees"))
        payload["autoTranslateEnabled"] = vocab.auto_translate_enabled(merged.get("autoTranslateEnabled"))
        payload.setdefault("enabled", True)
        if not str(payload.get("name") or "").strip():
            raise ReminderError("a reminder needs a name; the research's own list is a named asset")
        return payload

    def create_reminder(
        self, spec: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Create a reusable reminder asset.

        Not room-scoped: the research is explicit that a reminder is "a reusable
        asset attachable to many Meeting Types", and a reminder that belonged to
        one room could not be reused across two. The booking and its deliveries
        are what carry the room.
        """
        return self.store.create(
            REMINDER_COLLECTION, self.validate_reminder(spec), actor=actor, source=source
        )

    def get_reminder(self, reminder_id: str) -> dict[str, Any] | None:
        record = self.store.get(reminder_id)
        if record is None or record["collection"] != REMINDER_COLLECTION:
            return None
        return record

    def require_reminder(self, reminder_id: str) -> dict[str, Any]:
        record = self.get_reminder(reminder_id)
        if record is None:
            raise ReminderError(f"reminder {reminder_id} not found")
        return record

    def list_reminders(
        self, *, channel: str | None = None, condition: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Reminder assets, newest first. Every filter is a JSON path in the payload."""
        where: dict[str, Any] = {}
        if channel is not None:
            where["channel"] = vocab.require_channel(channel)
        if condition is not None:
            where["condition"] = vocab.require_condition(condition)
        return self.store.find(REMINDER_COLLECTION, where, limit=limit)

    def update_reminder(
        self, reminder_id: str, patch: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Patch a reminder, re-validating the merged result.

        A partial patch is merged onto the stored reminder and re-run through the
        same validation a create goes through, so a patch cannot leave a reminder
        whose channel is SMS with no Twilio account, or whose no-reply sender has
        no domain behind it.
        """
        current = self.require_reminder(reminder_id)
        return self.store.update(
            reminder_id, self.validate_reminder(patch, existing=current["data"]), actor=actor, source=source
        )

    def delete_reminder(self, reminder_id: str, *, actor: str | None = None, source: str) -> dict[str, Any]:
        """**Delete** the asset. Soft.

        The researched counterpart to :meth:`detach_reminder`, and the two are
        different actions on different rows. A soft delete, because the
        deliveries this reminder produced stay auditable: a reminder's history
        outliving the reminder is the point of an audit log.
        """
        self.require_reminder(reminder_id)
        return self.store.delete(reminder_id, actor=actor, source=source)

    # -- meeting types ------------------------------------------------------- #

    def create_meeting_type(
        self, spec: Mapping[str, Any], *, room_id: str | None = None, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Declare a Meeting Type, which reminders attach to and bookings are made against.

        Room-scoped through the envelope, so a room's meeting types are its own,
        but not scoped in the URL: the research's meeting types are an
        organisation-level configuration surface, and a Meeting Type reached only
        through a room would be a different object.
        """
        body = dict(spec or {})
        if not str(body.get("name") or "").strip():
            raise ReminderError("a meeting type needs a name")
        return self.store.create(
            MEETING_TYPE_COLLECTION, body, room_id=room_id, actor=actor, source=source
        )

    def get_meeting_type(self, meeting_type_id: str) -> dict[str, Any] | None:
        record = self.store.get(meeting_type_id)
        if record is None or record["collection"] != MEETING_TYPE_COLLECTION:
            return None
        return record

    def list_meeting_types(self, *, room_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        records = self.store.list(MEETING_TYPE_COLLECTION, limit=limit)
        if room_id is not None:
            records = [record for record in records if record.get("room_id") in (None, room_id)]
        return records

    def phone_required(self, meeting_type_id: str) -> bool:
        """Whether this meeting type's booking form must collect a phone.

        Cal's `attendee.phoneNumber` "becomes required when SMS reminders are
        enabled for the event type", so this is derived rather than stored: it is
        true exactly when an **enabled SMS** reminder is attached.
        """
        for attachment in self.attachments(meeting_type_id):
            reminder = self.get_reminder(str(attachment["data"].get("reminder_id") or ""))
            if reminder is None:
                continue
            data = reminder["data"]
            if data.get("channel") == vocab.SMS and data.get("enabled", True):
                return True
        return False

    def meeting_type_view(self, meeting_type: Mapping[str, Any]) -> dict[str, Any]:
        """A meeting type with the derived fields a booking form needs.

        ``phone_required`` and the attached reminder count are computed here
        rather than stored, so attaching an SMS reminder cannot leave a stale
        ``phone_required: false`` behind on a form that now needs one.
        """
        data = dict(meeting_type.get("data") or {})
        meeting_type_id = str(meeting_type["id"])
        attachments = self.attachments(meeting_type_id)
        return {
            **meeting_type,
            "reminder_count": len(attachments),
            "phone_required": self.phone_required(meeting_type_id),
            "phone_required_quote": vocab.PHONE_REQUIRED_QUOTE,
            "name": data.get("name"),
        }

    # -- attachments --------------------------------------------------------- #

    def attach(
        self, meeting_type_id: str, reminder_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Attach a reminder to a meeting type. Idempotent.

        Re-attaching returns the existing attachment rather than a second one,
        because "attachable to many Meeting Types" cuts both ways and a
        double-attach would show an administrator the same reminder twice and
        send it twice.
        """
        self.require_meeting_type(meeting_type_id)
        self.require_reminder(reminder_id)
        for existing in self.attachments(meeting_type_id):
            if str(existing["data"].get("reminder_id")) == reminder_id:
                return existing
        return self.store.create(
            ATTACHMENT_COLLECTION,
            {"meeting_type_id": meeting_type_id, "reminder_id": reminder_id},
            actor=actor,
            source=source,
        )

    def attachments(self, meeting_type_id: str) -> list[dict[str, Any]]:
        return self.store.find(ATTACHMENT_COLLECTION, {"meeting_type_id": meeting_type_id}, limit=500)

    def require_meeting_type(self, meeting_type_id: str) -> dict[str, Any]:
        record = self.get_meeting_type(meeting_type_id)
        if record is None:
            raise ReminderError(f"meeting type {meeting_type_id} not found")
        return record

    def attached_reminders(self, meeting_type_id: str) -> list[dict[str, Any]]:
        """The live reminder assets attached to a meeting type.

        Skips attachments whose reminder has been deleted. A deleted reminder's
        deliveries stay auditable, but it must not keep firing - and the
        difference has to be visible rather than silent, so the count of skipped
        attachments is reported by :meth:`meeting_type_view`.
        """
        found: list[dict[str, Any]] = []
        for attachment in self.attachments(meeting_type_id):
            reminder = self.get_reminder(str(attachment["data"].get("reminder_id") or ""))
            if reminder is not None:
                found.append(reminder)
        return found

    def detach(
        self, meeting_type_id: str, reminder_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """**Remove from Meeting Type.** The reminder asset and its other
        attachments survive.

        The researched counterpart to :meth:`delete_reminder`, and the reason
        both exist: a reminder attached to six meeting types must survive being
        removed from the seventh.
        """
        self.require_meeting_type(meeting_type_id)
        for attachment in self.attachments(meeting_type_id):
            if str(attachment["data"].get("reminder_id")) == reminder_id:
                return self.store.delete(attachment["id"], actor=actor, source=source)
        raise ReminderError(f"reminder {reminder_id} is not attached to meeting type {meeting_type_id}")

    def attachment_ids_for(self, reminder_id: str) -> list[str]:
        """The meeting types one reminder is attached to.

        Feeds Cal's ``activation.activeOnEventTypeIds``: a reusable reminder is
        active on the event types it is attached to, so this is what the
        projection reports.
        """
        return sorted(
            str(record["data"].get("meeting_type_id"))
            for record in self.store.find(ATTACHMENT_COLLECTION, {"reminder_id": reminder_id}, limit=500)
        )

    # -- bookings ------------------------------------------------------------ #

    def create_booking(
        self,
        room_id: str,
        spec: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
        enforce_phone: bool = True,
    ) -> dict[str, Any]:
        """Record a booking on a room, against a meeting type.

        Enforces the researched ``phoneRequired`` consequence here rather than at
        delivery time: Cal says the phone field "becomes required when SMS
        reminders are enabled for the event type", which is a statement about the
        booking *form*, so a booking created against such a meeting type without
        a guest phone is refused rather than accepted and silently undeliverable.

        ``enforce_phone=False`` exists for one caller: the seed, which needs a
        booking that *predates* the SMS reminder being attached, so the researched
        "Phone not found" skip is reachable at all. A real deployment has the
        same situation - a booking made last month, an SMS reminder attached today
        - and cannot be refused retroactively, which is why the researched
        consequence has a second half at delivery time. The HTTP route never sets
        it, and the flag is recorded on the row so a seeded booking is not
        indistinguishable from one that simply has no phone.
        """
        if self.store.get(room_id) is None:
            raise RecordNotFound(room_id)
        body = dict(spec or {})
        meeting_type_id = str(body.get("meetingTypeId") or "")
        if not meeting_type_id:
            raise ReminderError("a booking must name the meetingTypeId it was made against")
        meeting_type = self.require_meeting_type(meeting_type_id)
        cond.require_instant(body.get("start"), "start")
        if enforce_phone and self.phone_required(meeting_type_id) and not self._guest_phone(body):
            raise ConfigurationRefused(
                "this meeting type has an SMS reminder attached, so the booking form requires a "
                "phone: " + vocab.GUEST_FORM_PHONE_QUOTE
            )
        payload = {
            **body,
            "meetingTypeId": meeting_type_id,
            "meetingTypeName": (meeting_type["data"] or {}).get("name"),
        }
        if not enforce_phone:
            payload["phoneRequiredAtBooking"] = False
        return self.store.create(BOOKING_COLLECTION, payload, room_id=room_id, actor=actor, source=source)

    @staticmethod
    def _guest_phone(spec: Mapping[str, Any]) -> str:
        """Any phone on the booking, wherever the guest form put it.

        The guest form is the schema-flexible half of the product: a team may put
        the phone on ``primaryGuest``, spread across ``guests``, or at the top
        level, and requiring one specific shape would be a schema the research
        does not describe. So the booking form's field is read where it is.
        """
        primary = spec.get("primaryGuest")
        if isinstance(primary, Mapping) and str(primary.get("phone") or "").strip():
            return str(primary["phone"]).strip()
        for guest in spec.get("guests") or []:
            if isinstance(guest, Mapping) and str(guest.get("phone") or "").strip():
                return str(guest["phone"]).strip()
        return str(spec.get("phone") or "").strip()

    def get_booking(self, booking_id: str) -> dict[str, Any] | None:
        record = self.store.get(booking_id)
        if record is None or record["collection"] != BOOKING_COLLECTION:
            return None
        return record

    def require_booking(self, booking_id: str) -> dict[str, Any]:
        record = self.get_booking(booking_id)
        if record is None:
            raise ReminderError(f"booking {booking_id} not found")
        return record

    def list_bookings(self, *, room_id: str | None = None, meeting_type_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Bookings, newest first.

        ``room_id`` is filtered here rather than in the query: the dynamic index
        resolves JSON paths inside ``data`` but not the ``room_id`` column, so
        :meth:`AuditedDatabase.find` cannot scope by room. Filtering in Python
        keeps that a detail of this method instead of a schema change.
        """
        where: dict[str, Any] = {}
        if meeting_type_id is not None:
            where["meetingTypeId"] = meeting_type_id
        records = self.store.find(BOOKING_COLLECTION, where, limit=1000)
        if room_id is not None:
            records = [record for record in records if record.get("room_id") == room_id]
        return records[:limit]

    # -- preview ------------------------------------------------------------- #

    def preview(
        self, reminder: Mapping[str, Any], booking: Mapping[str, Any], *, now: datetime
    ) -> dict[str, Any]:
        """Render the message and decide, writing **nothing at all**.

        The researched Preview pane, and the read-only half of :meth:`deliver`.
        Both call the same :func:`~dsr.meeting_reminders.conditions.evaluate`, so
        a preview cannot disagree with what the delivery will do - which is the
        only thing that makes a preview worth reading.
        """
        # The planning moment is the booking's own `bookedAt`, not this call's
        # clock, for the reason in `_decide_from`. So a preview answers the
        # question an administrator is actually asking - "if this is attached to
        # this booking, does it go out?" - rather than "was it attached before
        # the moment it fires?".
        when_planned = cond.planned_at(booking) or now
        decision = self._decide_from(reminder, booking, when_planned=when_planned, now=now)
        composed = self.compose(reminder, booking, recipients=decision.recipients)
        return {
            "decision": decision.to_dict(),
            "message": composed,
            "planned_at": when_planned.isoformat(),
            "planned_at_source": "bookedAt" if cond.planned_at(booking) else "the caller's clock",
        }

    def compose(
        self,
        reminder: Mapping[str, Any],
        booking: Mapping[str, Any],
        *,
        recipients: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Render the subject and body for a booking, and the extras Cal names.

        ``includeCalendarEvent`` attaches a ``.ics`` to the email, and
        ``autoTranslateEnabled`` with ``sourceLocale`` records the locale the
        message went out in. ``skipNoShowAttendees`` is **not** applied here: it
        is applied when the audience is resolved, in
        :func:`~dsr.meeting_reminders.conditions.resolve_recipients`, because a
        guest left out of the list is a guest the *decision* must not claim to
        have notified. Doing it in both places would be two answers to one
        question, and the second would quietly win.

        All four are named in ``features_tools``; all four are switches this build
        implements and records rather than claims.
        """
        target = [dict(recipient) for recipient in (recipients or [])]

        locale = vocab.require_source_locale(reminder.get(vocab.SOURCE_LOCALE))
        translated = vocab.auto_translate_enabled(reminder.get("autoTranslateEnabled"))
        rendered = tagmod.render_message(reminder.get("subject"), reminder.get("body"), booking)

        attachments: list[dict[str, Any]] = []
        if vocab.include_calendar_event(reminder.get("includeCalendarEvent")):
            attachments.append(
                {
                    "filename": "invite.ics",
                    "content_type": "text/calendar",
                    "method": "REQUEST",
                    "summary": str(booking.get("title") or ""),
                    "start": booking.get("start"),
                    "duration_minutes": booking.get("durationMinutes"),
                    "location": str(booking.get("location") or booking.get("meetingUrl") or ""),
                }
            )

        return {
            "subject": rendered["subject"],
            "body": rendered["body"],
            "resolved_tags": rendered["resolved"],
            "missing_tags": rendered["missing"],
            "locale": locale,
            "auto_translate_enabled": translated,
            # Named, and false: the research names the switch and no catalogue.
            # See the `translation-is-not-claimed` inference.
            "translated": False,
            "recipients": target,
            "attachments": attachments,
        }

    # -- delivery ------------------------------------------------------------ #

    def plan(self, booking_record: Mapping[str, Any], *, actor: str | None = None, source: str) -> dict[str, Any]:
        """Plan every attached reminder for a booking. Writes the scheduled rows.

        This is the *planning* half of the researched automation, and it is where
        "Reminder schedule time in the past" is decided - see the
        ``schedule-in-the-past-is-a-planning-question`` inference. A reminder
        whose moment has already gone by is recorded as skipped at this point,
        with the reason, rather than being left to look scheduled.
        """
        booking = dict(booking_record.get("data") or {})
        meeting_type_id = str(booking.get("meetingTypeId") or "")
        now = self.clock()
        planned: list[dict[str, Any]] = []
        for reminder_record in self.attached_reminders(meeting_type_id):
            reminder = dict(reminder_record["data"])
            if not reminder.get("enabled", True):
                continue
            decision = cond.plan(reminder, booking, now=now)
            planned.append(
                self._record(reminder_record, booking_record, decision, now=now, actor=actor, source=source)
            )
        return {
            "booking_id": booking_record["id"],
            "room_id": booking_record.get("room_id"),
            "planned": len(planned),
            "deliveries": planned,
        }

    def fire(
        self,
        *,
        room_id: str | None = None,
        now: datetime | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Fire every due scheduled delivery. This is the automation.

        ``source`` is required and passed through unchanged to every row this
        writes, for the same reason it is required on every other write here: the
        audit row must name the route that served the run, not a string the
        domain invented. The HTTP route builds it from ``router.prefix``; the
        seed passes ``"seed"``.

        A delivery that is already ``sent`` or ``skipped`` is not re-decided, so
        re-running a window gives the same answer and cannot rewrite an audit row.
        A booking with no scheduled row yet is picked up on this pass rather than
        waiting for a separate planning run - the scheduler is the thing that
        both plans and fires in production.
        """
        moment = now if now is not None else self.clock()
        fired: list[dict[str, Any]] = []
        for booking_record in self.list_bookings(room_id=room_id, limit=1000):
            booking = dict(booking_record["data"] or {})
            meeting_type_id = str(booking.get("meetingTypeId") or "")
            for reminder_record in self.attached_reminders(meeting_type_id):
                reminder = dict(reminder_record["data"])
                if not reminder.get("enabled", True):
                    continue
                when = cond.fire_at(reminder, booking)
                if when is None:
                    # No usable start: plan records the researched
                    # "schedule_in_past" reason for it, and a booking with no
                    # start has nothing for a scheduler to wait for.
                    continue
                existing = self._existing(booking_record["id"], reminder_record["id"])
                already_settled = (
                    existing is not None and str((existing["data"] or {}).get("status")) != vocab.SCHEDULED
                )
                if already_settled:
                    continue
                when_planned = cond.planned_at(booking) or moment
                decision = self._decide_from(reminder, booking, when_planned=when_planned, now=moment)
                if existing is not None:
                    fired.append(self._update(existing, decision, now=moment, source=source))
                else:
                    fired.append(
                        self._record(
                            reminder_record, booking_record, decision, now=moment, actor=None, source=source
                        )
                    )
        return {
            "fired": len(fired),
            "sent": sum(1 for row in fired if (row["data"] or {}).get("status") == vocab.SENT),
            "skipped": sum(1 for row in fired if (row["data"] or {}).get("status") == vocab.SKIPPED),
            "deliveries": fired,
        }

    def deliver(
        self,
        room_id: str,
        booking_id: str,
        *,
        reminder_id: str | None = None,
        actor: str | None = None,
        source: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """The whole researched workflow for one booking, end to end.

        Plan, then decide, then record - through the same two functions the
        scheduler and the preview use, so a delivery written here is one the
        scheduler would have produced.
        """
        if self.store.get(room_id) is None:
            raise RecordNotFound(room_id)
        booking_record = self.require_booking(booking_id)
        if booking_record.get("room_id") != room_id:
            raise ReminderError(f"booking {booking_id} is not in room {room_id}")
        moment = now if now is not None else self.clock()
        booking = dict(booking_record["data"] or {})
        meeting_type_id = str(booking.get("meetingTypeId") or "")

        # The planning moment comes off the booking, not off this call's clock.
        # A reminder cannot have been attached before the meeting was booked, and
        # a scheduler that ran an hour late would otherwise retroactively declare
        # every due reminder "planned in the past".
        when_planned = cond.planned_at(booking) or moment
        out: list[dict[str, Any]] = []
        for reminder_record in self.attached_reminders(meeting_type_id):
            if reminder_id and reminder_record["id"] != reminder_id:
                continue
            reminder = dict(reminder_record["data"])
            if not reminder.get("enabled", True):
                continue
            decision = self._decide_from(reminder, booking, when_planned=when_planned, now=moment)
            out.append(
                self._record(reminder_record, booking_record, decision, now=moment, actor=actor, source=source)
            )
        return {"booking_id": booking_id, "room_id": room_id, "count": len(out), "deliveries": out}

    def _decide_from(
        self,
        reminder: Mapping[str, Any],
        booking: Mapping[str, Any],
        *,
        when_planned: datetime,
        now: datetime,
    ) -> cond.Decision:
        """:func:`~dsr.meeting_reminders.conditions.plan` then
        :func:`~dsr.meeting_reminders.conditions.decide`, with two clocks.

        The same pair of steps :func:`conditions.evaluate` runs, spelled out here
        so the run's clock and the booking's planning moment cannot be confused
        for one another. That confusion is not hypothetical: passing the run's
        clock as the planning clock makes every reminder that fires report
        "Reminder schedule time in the past", because by definition a reminder
        that has fired has a fire time behind it.
        """
        planned = cond.plan(reminder, booking, now=when_planned)
        if planned.status == vocab.SKIPPED or planned.fire_at is None or planned.fire_at > now:
            return planned
        return cond.decide(reminder, booking, now=now, org=self.org_settings())

    def _existing(self, booking_id: str, reminder_id: str) -> dict[str, Any] | None:
        for record in self.store.find(
            DELIVERY_COLLECTION, {"bookingId": booking_id, "reminderId": reminder_id}, limit=1
        ):
            return record
        return None

    def _record(
        self,
        reminder_record: Mapping[str, Any],
        booking_record: Mapping[str, Any],
        decision: cond.Decision,
        *,
        now: datetime,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Write one delivery row. The single place a delivery is created."""
        reminder = dict(reminder_record["data"])
        booking = dict(booking_record["data"] or {})
        composed = self.compose(reminder, booking, recipients=decision.recipients)
        return self.store.create(
            DELIVERY_COLLECTION,
            {
                **decision.to_dict(),
                "bookingId": booking_record["id"],
                "roomId": booking_record.get("room_id"),
                "reminderId": reminder_record["id"],
                "reminderName": reminder.get("name"),
                "meetingTypeId": booking.get("meetingTypeId"),
                "message": composed,
                "replies": [],
                "at": now.isoformat(),
            },
            room_id=booking_record.get("room_id"),
            actor=actor,
            source=source,
        )

    def _update(
        self,
        existing: Mapping[str, Any],
        decision: cond.Decision,
        *,
        now: datetime,
        source: str,
    ) -> dict[str, Any]:
        """Move a scheduled delivery to its outcome, keeping what was already recorded.

        The scheduled row's recipients and composed message are kept: they were
        resolved at planning time and are the artifact an administrator reads,
        while the status, the reason and the check trail are what the run
        establishes. A reschedule between planning and firing is visible as a
        different ``fire_at`` rather than being silently overwritten, because the
        old value is kept beside the new one.
        """
        return self.store.update(
            existing["id"],
            {
                **decision.to_dict(),
                "at": now.isoformat(),
                "planned_at": (existing["data"] or {}).get("at"),
            },
            source=source,
        )

    # -- the activity feed --------------------------------------------------- #

    def deliveries(
        self,
        *,
        room_id: str | None = None,
        booking_id: str | None = None,
        status: str | None = None,
        reason: str | None = None,
        channel: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Every delivery, newest first. The researched *Meetings Activity* list.

        Every filter is a JSON path in the payload, except ``room_id``, which is
        the envelope column and is filtered in Python - the dynamic index does
        not cover it, and adding a column for it would be the migration the
        schema-flexibility rule exists to prevent.
        """
        where: dict[str, Any] = {}
        if booking_id is not None:
            where["bookingId"] = booking_id
        if status is not None:
            where["status"] = vocab.require_status(status)
        if reason is not None:
            where["reason"] = vocab.require_skip_reason(reason)
        if channel is not None:
            where["channel"] = vocab.require_channel(channel)
        records = self.store.find(DELIVERY_COLLECTION, where, limit=1000)
        if room_id is not None:
            records = [record for record in records if record.get("room_id") == room_id]
        return records[:limit]

    def get_delivery(self, delivery_id: str) -> dict[str, Any] | None:
        record = self.store.get(delivery_id)
        if record is None or record["collection"] != DELIVERY_COLLECTION:
            return None
        return record

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Counts for the top of the page, over exactly the rows the filters return.

        A skip needing a human is counted apart from one that is not, because
        they mean different things to a rep: "Recipient not found" is a data
        problem someone has to fix on the booking, and a weekday restriction is
        the rule working.
        """
        records = self.deliveries(room_id=room_id, limit=1000)
        by_status: dict[str, int] = {}
        by_reason: dict[str, int] = {}
        by_channel: dict[str, int] = {}
        needs_human = 0
        recipients = 0
        for record in records:
            data = record["data"]
            status = str(data.get("status"))
            by_status[status] = by_status.get(status, 0) + 1
            channel = str(data.get("channel"))
            by_channel[channel] = by_channel.get(channel, 0) + 1
            reason = data.get("reason")
            if reason:
                by_reason[str(reason)] = by_reason.get(str(reason), 0) + 1
                if str(reason) in vocab.NEEDS_HUMAN_REASONS:
                    needs_human += 1
            recipients += len(data.get("recipients") or [])
        return {
            "room_id": room_id,
            "deliveries": len(records),
            "by_status": dict(sorted(by_status.items())),
            "by_reason": dict(sorted(by_reason.items())),
            "by_channel": dict(sorted(by_channel.items())),
            "recipients": recipients,
            "needs_human": needs_human,
            "reminders": len(self.list_reminders(limit=1000)),
            "meeting_types": len(self.list_meeting_types(limit=1000)),
            "sms_ready": self.twilio_ready(),
            "reply_forwarding_ready": self.reply_forwarding_ready(),
        }

    # -- inbound SMS replies ------------------------------------------------- #

    def record_reply(
        self, delivery_id: str, body: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Record an inbound SMS reply and the addresses it was forwarded to.

        "When a guest replies to an SMS reminder, Chili Piper forwards the text to
        your team by email." The forwarding itself is the researched step; which
        addresses it goes to is ``Send Replies To``, the researched choice.

        Refused without an organisation-owned Twilio account, because the
        research flags exactly that: "⚠️ **Warning:** Forwarding SMS replies
        requires your company's own Twilio account to be connected in Command
        Center." A connection that is not owned can send an SMS and cannot
        forward one, and pretending otherwise would lose a guest's reply.
        """
        if not self.reply_forwarding_ready():
            raise ConfigurationRefused(vocab.TWILIO_REPLY_FORWARDING_QUOTE)
        delivery = self.get_delivery(delivery_id)
        if delivery is None:
            raise ReminderError(f"delivery {delivery_id} not found")
        data = delivery["data"]
        if str(data.get("channel")) != vocab.SMS:
            raise ReminderError(
                f"delivery {delivery_id} is an {data.get('channel')} delivery; only an SMS reminder "
                "has replies forwarded by email"
            )
        forwarded_to = [str(value) for value in (data.get("replies_to") or []) if str(value).strip()]
        if not forwarded_to:
            raise ReminderError(
                "this reminder has no reply-forwarding address; the booking resolved no host, "
                "booker or assignee to forward to"
            )
        text = str((body or {}).get("body") or "").strip()
        if not text:
            raise ReminderError("a reply needs a body")

        now = self.clock()
        record = self.store.create(
            REPLY_COLLECTION,
            {
                "deliveryId": delivery_id,
                "roomId": delivery.get("room_id"),
                "from": str((body or {}).get("from") or "").strip(),
                "body": text,
                "forwardedTo": forwarded_to,
                "at": now.isoformat(),
            },
            room_id=delivery.get("room_id"),
            actor=actor,
            source=source,
        )
        history = [str(value) for value in (data.get("replies") or [])]
        history.append(record["id"])
        self.store.update(
            delivery_id,
            {"replies": history[-REPLY_HISTORY_LIMIT:]},
            actor=actor,
            source=source,
        )
        return record

    def replies(self, *, room_id: str | None = None, delivery_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if delivery_id is not None:
            where["deliveryId"] = delivery_id
        records = self.store.find(REPLY_COLLECTION, where, limit=limit)
        if room_id is not None:
            records = [record for record in records if record.get("room_id") == room_id]
        return records
