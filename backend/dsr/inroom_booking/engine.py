"""WF-136: the in-room next-step booking workflow, over the audited store.

This module is the workflow's rules and nothing else. It holds no request, no
response and no framework type - the HTTP surface is
``backend/dsr/features/wf136_inroom_booking.py`` - and every write takes a
``source`` the route names, so an audit row always says which route served it.

What this workflow adds, and what it borrows
--------------------------------------------

The research is unambiguous that the custom work is the room integration only:
"No OSS project does booking *inside* a deal room". Everything on either side of
that sentence already exists on this branch, so this module **reuses it rather
than re-declaring it**, which is the ticket's own instruction:

* **Slot and booking rules** come from :class:`dsr.inroom_scheduling.SchedulingEngine`.
  The room's grid and the booking itself are that workflow's answers, including
  the live-slot check and the hold. This module never computes a slot.
* **The conferencing link** comes from
  :class:`dsr.conference_links.engine.ProvisioningEngine`. Following WF-059's own
  rule - "A booking whose Location cannot produce a conference is still a
  booking" - a failure there leaves the meeting standing and marks it unlinked.
* **The MAP task** is flipped through
  :class:`dsr.mutual_action.plan.PlanEngine`, the route WF-124 already serves,
  rather than by editing a task row directly.

What this module owns is the layer the research says is missing: a *next step*
the seller proposes, a *buyer* who answers it inside the room, a *meeting* row
that ties the booking to the proposal and the link, a *timeline* row the buyer
sees, and a *next-steps panel* that merges this workflow's proposals with
WF-124's open tasks without caching either.

Why the state of a proposal is derived
--------------------------------------

A proposal carries an expiry and nobody runs a scheduler in this product. So the
state a reader sees is computed on every read: ``state`` is what was stored, and
``effective_state`` is that plus "and it has lapsed if the clock passed
``expires_at``". Reading therefore has no side effect. The one route that does
persist the transition is the auto-expiry route, which is the researched "no user
action needed for the hold to expire" applied to a proposal.

Why this module translates the borrowed workflows' errors
---------------------------------------------------------

:mod:`dsr.inroom_scheduling`, :mod:`dsr.conference_links` and
:mod:`dsr.mutual_action.plan` each raise their own error types, and the feature
host mounts one exception handler per type across the whole product. Two
workflows may not map the same type, and WF-058, WF-059 and WF-124 already map
theirs. So this module catches those foreign errors at the boundary and re-raises
its own, which is also what lets the page branch on this workflow's reasons
without knowing the three other workflows' vocabularies.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from dsr.conference_links.engine import ProvisioningEngine
from dsr.conference_links.errors import ConferenceLinkError
from dsr.inroom_booking.errors import (
    AttendeeRequired,
    BookingRefused,
    InRoomBookingError,
    MeetingNotFound,
    NextStepTaskNotFound,
    ProposalAnswered,
    ProposalExpired,
    ProposalInvalid,
    ProposalNotFound,
    RecordingNotAttached,
    RoomNotBookable,
    SlotNotOffered,
)
from dsr.inroom_booking.vocabulary import (
    ACCEPT_BOOKS_IMMEDIATELY,
    IDLE_MINUTES,
    MEETING_COLLECTION,
    MEETING_LINKED,
    MEETING_UNLINKED,
    PROPOSAL_ACCEPTED,
    PROPOSAL_BOOKED,
    PROPOSAL_COLLECTION,
    PROPOSAL_DECLINED,
    PROPOSAL_EXPIRED,
    PROPOSAL_PROPOSED,
    PROPOSAL_TTL_MINUTES,
    ROOM_FIELD,
    ROOM_MEETING_LIMIT,
    TIMELINE_COLLECTION,
)
from dsr.inroom_scheduling import (
    EmbedConfigError,
    HoldExpired,
    HoldRequired,
    SchedulingEngine,
    SchedulingError,
    SlotUnavailable,
    TokenExpired,
)
from dsr.mutual_action import plan as mutual
from dsr.store import RecordStore

#: The room's own activity rows. Written by WF-006 and by ``backend/seed.py``,
#: and read here only to answer "has this room gone quiet".
ACTIVITY_COLLECTION = "activity"

#: The room collection's own name, so a create cannot be pointed at a proposal
#: id by mistake.
ROOM_COLLECTION = "room"

#: The longest and shortest meeting this workflow will propose. Bounds rather
#: than a default: the research fixes no length, and a proposal for a
#: ninety-hour meeting is a typo, not a next step.
MIN_DURATION_MINUTES = 5
MAX_DURATION_MINUTES = 480

#: Every key a caller may send as the start of the meeting, in the order the
#: accepted spellings are tried. ``start`` is Cal's own name and wins.
_START_KEYS = ("start", "slot", "starts_at")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse_instant(value: Any) -> datetime | None:
    """A timezone-aware instant, or ``None`` if the value is not one.

    Naive datetimes are read as UTC rather than refused: the store contains both
    ``seed`` rows and route rows, and a missing offset is a formatting choice,
    not a different moment. A value that cannot be parsed at all returns
    ``None`` so the caller decides, instead of the reader inventing "now".
    """
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _first(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


class InRoomBookingEngine:
    """The workflow, over the audited store.

    Built per request from ``StoreDep``, like every other feature's engine. The
    three borrowed engines are constructed lazily around the same store and the
    same clock, and each is injectable so a test can replace one without a
    database or assert on another without a network call.
    """

    def __init__(
        self,
        store: RecordStore,
        *,
        now: Callable[[], datetime] | None = None,
        scheduling: SchedulingEngine | None = None,
        conferencing: ProvisioningEngine | None = None,
        plans: mutual.PlanEngine | None = None,
    ) -> None:
        self.store = store
        self._now = now if now is not None else _utcnow
        self.scheduling = (
            scheduling if scheduling is not None else SchedulingEngine(store, clock=self._now)
        )
        self.conferencing = (
            conferencing if conferencing is not None else ProvisioningEngine(store, now=self._now)
        )
        self.plans = plans if plans is not None else mutual.PlanEngine(store, now=self._now)

    # -- clock and records --------------------------------------------------- #

    def now(self) -> datetime:
        return self._now()

    def _record_of(self, collection: str, record_id: str, error: type[InRoomBookingError]):
        record = self.store.get(record_id)
        if record is None or record.get("collection") != collection:
            raise error(f"{collection} record {record_id} does not exist")
        return record

    def _proposal_record(self, proposal_id: str) -> dict[str, Any]:
        return self._record_of(PROPOSAL_COLLECTION, proposal_id, ProposalNotFound)

    def _meeting_record(self, meeting_id: str) -> dict[str, Any]:
        return self._record_of(MEETING_COLLECTION, meeting_id, MeetingNotFound)

    # -- views --------------------------------------------------------------- #

    def effective_state(self, data: Mapping[str, Any], *, now: datetime | None = None) -> str:
        """The stored state, or ``expired`` once the clock passes ``expires_at``.

        ``proposed`` is the only state that can lapse: an answered or booked
        proposal is already decided, and a declined one is not going to lapse
        into anything.
        """
        state = str(data.get("state") or PROPOSAL_PROPOSED)
        if state != PROPOSAL_PROPOSED:
            return state
        expiry = _parse_instant(data.get("expires_at"))
        if expiry is not None and expiry <= (now or self.now()):
            return PROPOSAL_EXPIRED
        return state

    def proposal_view(
        self, record: Mapping[str, Any], *, now: datetime | None = None
    ) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        data["id"] = record.get("id")
        data["room_id"] = record.get("room_id") or data.get("room_id")
        data["effective_state"] = self.effective_state(data, now=now)
        data["answerable"] = data["effective_state"] == PROPOSAL_PROPOSED
        return data

    def meeting_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        data["id"] = record.get("id")
        data["room_id"] = record.get("room_id") or data.get("room_id")
        return data

    # -- room bookability ---------------------------------------------------- #

    def require_bookable_room(self, room_id: str) -> dict[str, Any]:
        """The room's embed response, or this workflow's own refusal.

        The two things that make a room unbookable are WF-058's answers, so they
        are read from WF-058's engine and translated rather than re-derived. The
        reason token says which of the two it is, because a room with no embed
        and a room whose token lapsed are fixed by different people.
        """
        room = self.store.get(room_id)
        if room is None or room.get("collection") != ROOM_COLLECTION:
            raise RoomNotBookable(
                f"room {room_id} does not exist, so there is no availability to book",
                reason="room_not_found",
                http_status=404,
            )
        try:
            response = self.scheduling.embed_response(room_id)
        except EmbedConfigError as exc:
            raise RoomNotBookable(
                f"room {room_id} has no bookable calendar installed: {exc}",
                reason="room_has_no_embed",
            ) from exc
        summary = response.get("summary") or {}
        if not summary.get("can_book"):
            token_reason = str((summary.get("token") or {}).get("reason") or "token_not_live")
            raise RoomNotBookable(
                f"room {room_id} cannot book: its calendar token is not live ({token_reason})",
                reason=token_reason,
            )
        return response

    # -- proposals ----------------------------------------------------------- #

    def create_proposal(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """The seller's next step, written down and given an expiry.

        Validated before anything is written, including the MAP task it points
        at, so a proposal can never name a task the plan does not hold. A
        refusal writes nothing at all - a 400 that left a proposal behind would
        be a next step nobody proposed.
        """
        self.require_bookable_room(room_id)
        body = dict(payload or {})
        errors: dict[str, str] = {}

        summary = str(_first(body, ("summary", "title", "next_step")) or "").strip()
        if not summary:
            errors["summary"] = "Say what the next step is."

        task_id = _first(body, ("task_id", "taskId"))
        task_id = str(task_id) if task_id is not None else None
        if task_id is not None:
            try:
                self.plans.read_task(task_id)
            except mutual.PlanNotFound as exc:
                raise NextStepTaskNotFound(
                    f"the plan holds no task {task_id}; a next step can only complete a task "
                    "that exists on this room's plan"
                ) from exc

        duration = _first(body, ("duration_minutes", "durationMinutes", "duration"))
        duration_minutes: int | None = None
        if duration is not None:
            try:
                duration_minutes = int(duration)
            except (TypeError, ValueError):
                errors["duration_minutes"] = "A meeting length is a whole number of minutes."
            else:
                if not MIN_DURATION_MINUTES <= duration_minutes <= MAX_DURATION_MINUTES:
                    errors["duration_minutes"] = (
                        f"Choose between {MIN_DURATION_MINUTES} and {MAX_DURATION_MINUTES} minutes."
                    )

        expires = _parse_instant(_first(body, ("expires_at", "expiresAt")))
        if errors:
            raise ProposalInvalid("the proposal could not be read", errors)

        now = self.now()
        if expires is None:
            expires = now + timedelta(minutes=PROPOSAL_TTL_MINUTES)

        data: dict[str, Any] = {
            "room_id": room_id,
            "summary": summary,
            "details": str(body.get("details") or "").strip() or None,
            "task_id": task_id,
            "duration_minutes": duration_minutes,
            "proposed_by": actor,
            "state": PROPOSAL_PROPOSED,
            "proposed_at": _iso(now),
            "expires_at": _iso(expires),
            "answered_at": None,
            "meeting_id": None,
            "booking_uid": None,
            "decline_reason": None,
        }
        record = self.store.create(
            PROPOSAL_COLLECTION, data, room_id=room_id, actor=actor, source=source
        )
        return self.proposal_view(record)

    def list_proposals(self, room_id: str, *, state: str | None = None) -> list[dict[str, Any]]:
        # Scoped by the record's ``room_id`` column, not by a JSON path: the store
        # lifts ``room_id`` out of ``data`` into its own column, so a ``find``
        # for it would match nothing.
        records = self.store.list(PROPOSAL_COLLECTION, room_id=room_id, limit=200)
        views = [self.proposal_view(record) for record in records]
        if state:
            views = [view for view in views if view["effective_state"] == state]
        views.sort(key=lambda view: str(view.get("proposed_at") or ""), reverse=True)
        return views

    def read_proposal(self, proposal_id: str) -> dict[str, Any]:
        return self.proposal_view(self._proposal_record(proposal_id))

    def slots(self, proposal_id: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """The seller's live availability, for the room the proposal is in.

        A read, and it saves nothing: WF-058's own ``/routed-slots`` is the
        precedent, and the research is explicit that rendering the grid "will
        not actually save the response". The selector is filled from the room's
        embed when the caller names none, so the page can ask a proposal for its
        grid without knowing which event type the room was configured with.
        """
        record = self._proposal_record(proposal_id)
        room_id = str(record.get("room_id") or (record.get("data") or {}).get("room_id"))
        query = dict(params or {})
        selector_keys = ("eventTypeId", "event_type_id", "eventTypeSlug", "usernames", "teamSlug")
        if not any(query.get(key) for key in selector_keys):
            embed = self.scheduling.embed_record(room_id) or {}
            if embed.get("eventTypeId"):
                query["eventTypeId"] = embed["eventTypeId"]
        try:
            grid = self.scheduling.slots(room_id, query)
        except SchedulingError as exc:
            raise self._translate_scheduling(exc) from exc
        return {**grid, "proposal_id": proposal_id}

    def accept(
        self,
        proposal_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """The buyer's one-click accept: book the slot, then integrate the room.

        In order: the proposal must still be live; the room must still be
        bookable; WF-058 books the slot and enforces every slot and hold rule;
        WF-059 provisions the link and may fail without failing the meeting; the
        MAP task is flipped through WF-124; a meeting row, a timeline row and the
        room's convenience copy are written.

        Nothing is written before the booking succeeds. A refusal at any step
        leaves the proposal ``proposed``, which is what lets the buyer pick
        again.
        """
        record = self._proposal_record(proposal_id)
        data = dict(record.get("data") or {})
        now = self.now()
        state = self.effective_state(data, now=now)
        if state == PROPOSAL_EXPIRED:
            raise ProposalExpired(
                "this next step lapsed before it was answered; ask the seller to propose it again"
            )
        if state != PROPOSAL_PROPOSED:
            raise ProposalAnswered(f"this next step was already {state}")

        room_id = str(record.get("room_id") or data.get("room_id"))
        self.require_bookable_room(room_id)

        body = dict(payload or {})
        start = _first(body, _START_KEYS)
        if start is None:
            raise SlotNotOffered(
                "choose the slot you want; the request named none",
                reason="start_required",
                http_status=400,
            )
        if self._attendee_of(body) is None:
            raise AttendeeRequired(
                "an invitation needs a name and an email for the person being booked"
            )

        booking_payload = self._booking_payload(data, body, start=start)
        try:
            booking = self.scheduling.book(room_id, booking_payload, actor=actor, source=source)
        except SchedulingError as exc:
            raise self._translate_scheduling(exc) from exc

        meeting_state, conference = self._provision_conference(
            room_id, booking, actor=actor, source=source
        )
        map_state = self._complete_next_step_task(data, actor=actor, source=source)

        meeting_data: dict[str, Any] = {
            "room_id": room_id,
            "proposal_id": proposal_id,
            "summary": data.get("summary"),
            "booking_uid": booking.get("uid"),
            "start": booking.get("start"),
            "end": booking.get("end"),
            "state": meeting_state,
            "conference_url": conference.get("url"),
            "conference_provider": conference.get("provider"),
            "conference_note": conference.get("note"),
            "map_task_id": data.get("task_id"),
            "map_task_state": map_state,
            "attendee": (booking.get("booking") or {}).get("attendee"),
            "recording_url": None,
            "booked_at": _iso(now),
            "booked_by": actor,
        }
        meeting = self.store.create(
            MEETING_COLLECTION, meeting_data, room_id=room_id, actor=actor, source=source
        )
        self.store.update(
            record["id"],
            {
                "state": PROPOSAL_BOOKED,
                "answered_at": _iso(now),
                "meeting_id": meeting.get("id"),
                "booking_uid": booking.get("uid"),
            },
            actor=actor,
            source=source,
        )
        self._write_timeline(
            room_id,
            {
                "summary": f"Next step booked: {data.get('summary')}",
                "kind": "meeting_booked",
                "meeting_id": meeting.get("id"),
                "start": meeting_data["start"],
            },
            actor=actor,
            source=source,
        )
        self._annotate_room(
            room_id,
            meeting,
            proposal_id=proposal_id,
            summary=data.get("summary"),
            actor=actor,
            source=source,
        )

        return {
            "proposal": self.proposal_view(self._proposal_record(proposal_id), now=now),
            "meeting": self.meeting_view(meeting),
            "booking": booking.get("booking"),
            "accept_books_immediately": ACCEPT_BOOKS_IMMEDIATELY,
        }

    def decline(
        self,
        proposal_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        record = self._proposal_record(proposal_id)
        data = dict(record.get("data") or {})
        state = self.effective_state(data)
        if state == PROPOSAL_EXPIRED:
            raise ProposalExpired("this next step lapsed; there is nothing left to decline")
        if state != PROPOSAL_PROPOSED:
            raise ProposalAnswered(f"this next step was already {state}")
        reason = str((payload or {}).get("reason") or "").strip() or None
        updated = self.store.update(
            record["id"],
            {
                "state": PROPOSAL_DECLINED,
                "answered_at": _iso(self.now()),
                "decline_reason": reason,
            },
            actor=actor,
            source=source,
        )
        return self.proposal_view(updated)

    def expire_lapsed(
        self, room_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Persist the expiry of every proposal whose clock has run out.

        The only write on this path, and it is the researched automation
        ("no user action needed for the hold to expire") applied to a proposal.
        Reads derive the same state without writing; this route is what makes the
        transition durable for anything reading the raw rows.
        """
        now = self.now()
        expired: list[str] = []
        records = self.store.list(PROPOSAL_COLLECTION, room_id=room_id, limit=500)
        for record in records:
            data = record.get("data") or {}
            if data.get("state") != PROPOSAL_PROPOSED:
                continue
            expiry = _parse_instant(data.get("expires_at"))
            if expiry is not None and expiry <= now:
                self.store.update(
                    record["id"],
                    {"state": PROPOSAL_EXPIRED, "expired_at": _iso(now)},
                    actor=actor,
                    source=source,
                )
                expired.append(str(record.get("id")))
        return {"room_id": room_id, "expired": expired, "count": len(expired)}

    def auto_propose(
        self, room_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Propose a next step when the room has gone quiet, once.

        The idle threshold is :data:`dsr.inroom_booking.vocabulary.IDLE_MINUTES`,
        published as an unresolved inference because Jev did not clear it. The
        automation also refuses to propose twice: a room with a live proposal
        already has a next step waiting for an answer.
        """
        self.require_bookable_room(room_id)
        live = [
            view
            for view in self.list_proposals(room_id)
            if view["effective_state"] == PROPOSAL_PROPOSED
        ]
        if live:
            return {
                "room_id": room_id,
                "proposed": False,
                "reason": "already_proposed",
                "proposal": live[0],
            }
        idle, idle_since = self._idle_state(room_id)
        if not idle:
            return {
                "room_id": room_id,
                "proposed": False,
                "reason": "room_active",
                "idle_since": idle_since,
                "idle_minutes": IDLE_MINUTES,
            }
        task_id, summary = self._auto_summary(room_id)
        proposal = self.create_proposal(
            room_id,
            {"summary": summary, "task_id": task_id},
            actor=actor,
            source=source,
        )
        return {
            "room_id": room_id,
            "proposed": True,
            "reason": "room_idle",
            "idle_since": idle_since,
            "idle_minutes": IDLE_MINUTES,
            "proposal": proposal,
        }

    # -- meetings ------------------------------------------------------------ #

    def list_meetings(self, room_id: str) -> list[dict[str, Any]]:
        records = self.store.list(MEETING_COLLECTION, room_id=room_id, limit=200)
        views = [self.meeting_view(record) for record in records]
        views.sort(key=lambda view: str(view.get("booked_at") or ""), reverse=True)
        return views

    def read_meeting(self, meeting_id: str) -> dict[str, Any]:
        return self.meeting_view(self._meeting_record(meeting_id))

    def attach_recording(
        self,
        meeting_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Attach a recording by URL, and put it on the room's timeline.

        The research's automation is "auto-record and attach" and its surface is
        "recorded demo auto-attached (Watch recording)". Recording is WF-060's
        work, so this attaches the artefact and claims nothing about producing
        it. The URL is the only thing that can be wrong, so it is the only
        refusal.
        """
        record = self._meeting_record(meeting_id)
        body = dict(payload or {})
        url = str(_first(body, ("url", "recording_url", "recordingUrl")) or "").strip()
        if not url.startswith(("http://", "https://")):
            raise RecordingNotAttached(
                "a recording is attached by URL; supply an http or https address"
            )
        now = self.now()
        updated = self.store.update(
            record["id"],
            {
                "recording_url": url,
                "recording_label": str(body.get("label") or "Watch recording"),
                "recording_attached_at": _iso(now),
            },
            actor=actor,
            source=source,
        )
        room_id = str(record.get("room_id") or "")
        self._write_timeline(
            room_id,
            {
                "summary": "Watch recording",
                "kind": "recording_attached",
                "meeting_id": meeting_id,
                "url": url,
                "at": _iso(now),
            },
            actor=actor,
            source=source,
        )
        return self.meeting_view(updated)

    # -- the next-steps panel ------------------------------------------------ #

    def next_steps(self, room_id: str) -> dict[str, Any]:
        """The room's "next steps" panel, built fresh from its two inputs.

        The research names "the MAP's open tasks" and "the room's 'next steps'
        panel" as separate data sources, so the panel merges this workflow's live
        proposals with WF-124's open tasks. Nothing is stored: a MAP task
        completed through WF-124's own route would leave a stored panel stale,
        and the specification's whole point is that the panel updates.
        """
        proposals = [
            view
            for view in self.list_proposals(room_id)
            if view["effective_state"] in (PROPOSAL_PROPOSED, PROPOSAL_ACCEPTED)
        ]
        map_tasks: list[dict[str, Any]] = []
        for plan in self.plans.list_plans(room_id):
            plan_id = str(plan.get("id"))
            for task in self.plans.tasks_of(plan_id):
                if mutual.is_done(task):
                    continue
                map_tasks.append(
                    {
                        "id": task.get("id"),
                        "plan_id": plan_id,
                        "title": task.get("title"),
                        "owner": task.get("owner"),
                        "owner_side": task.get("owner_side"),
                        "status": task.get("status"),
                        "due_date": task.get("due_date"),
                        "visibility": task.get("visibility"),
                    }
                )
        return {
            "room_id": room_id,
            "proposals": proposals,
            "map_tasks": map_tasks,
            "count": len(proposals) + len(map_tasks),
            "generated_at": _iso(self.now()),
        }

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        proposals = (
            self.list_proposals(room_id)
            if room_id
            else [
                self.proposal_view(record)
                for record in self.store.list(PROPOSAL_COLLECTION, limit=500)
            ]
        )
        meetings = (
            self.list_meetings(room_id)
            if room_id
            else [
                self.meeting_view(record)
                for record in self.store.list(MEETING_COLLECTION, limit=500)
            ]
        )
        by_state: dict[str, int] = {}
        for view in proposals:
            key = str(view["effective_state"])
            by_state[key] = by_state.get(key, 0) + 1
        linked = sum(1 for meeting in meetings if meeting.get("state") == MEETING_LINKED)
        return {
            "room_id": room_id,
            "proposals": len(proposals),
            "by_state": by_state,
            "answerable": by_state.get(PROPOSAL_PROPOSED, 0),
            "booked": by_state.get(PROPOSAL_BOOKED, 0),
            "meetings": len(meetings),
            "meetings_linked": linked,
            "meetings_unlinked": len(meetings) - linked,
            "idle_minutes": IDLE_MINUTES,
            "generated_at": _iso(self.now()),
        }

    # -- internals ----------------------------------------------------------- #

    def _attendee_of(self, body: Mapping[str, Any]) -> dict[str, Any] | None:
        """The attendee the buyer's click names, or ``None`` if it names none.

        Accepts both the Cal-shaped ``attendee`` object and flat ``name``/``email``
        keys, because a page may send either, and requires both a name and an
        email before returning: WF-058 refuses a booking without them, and a
        refusal here says which field is missing rather than translating a
        provider's complaint.
        """
        attendee = body.get("attendee")
        if isinstance(attendee, Mapping):
            name = str(attendee.get("name") or "").strip()
            email = str(attendee.get("email") or "").strip()
        else:
            name = str(body.get("name") or body.get("attendee_name") or "").strip()
            email = str(body.get("email") or body.get("attendee_email") or "").strip()
        if not name or not email:
            return None
        return {"name": name, "email": email}

    def _booking_payload(
        self, data: Mapping[str, Any], body: Mapping[str, Any], *, start: Any
    ) -> dict[str, Any]:
        """The payload WF-058's ``book`` takes, assembled from the buyer's click.

        The attendee is the one field WF-058 requires and the proposal cannot
        supply, so it is read from the click and never invented: a meeting with a
        placeholder attendee is a meeting nobody is invited to.
        """
        attendee = self._attendee_of(body) or {}
        payload: dict[str, Any] = {"start": start, "attendee": dict(attendee)}
        for key in (
            "reservationUid",
            "reservation_uid",
            "holdUid",
            "timeZone",
            "time_zone",
            "metadata",
            "bookingFieldsResponses",
            "booking_fields_responses",
        ):
            if body.get(key) not in (None, ""):
                payload[key] = body[key]
        duration = _first(body, ("duration_minutes", "durationMinutes")) or data.get(
            "duration_minutes"
        )
        if duration is not None:
            payload["duration_minutes"] = duration
        return payload

    def _translate_scheduling(self, exc: SchedulingError) -> InRoomBookingError:
        """A WF-058 refusal, in this workflow's vocabulary.

        The mapping is by cause, not by wording: a room that cannot book at all
        and a slot that has gone are different things to do next, and the reason
        token is what the page branches on.
        """
        if isinstance(exc, (EmbedConfigError, TokenExpired)):
            return RoomNotBookable(str(exc))
        if isinstance(exc, (SlotUnavailable, HoldExpired, HoldRequired)):
            return SlotNotOffered(str(exc))
        return BookingRefused(str(exc))

    def _provision_conference(
        self, room_id: str, booking: Mapping[str, Any], *, actor: str | None, source: str
    ) -> tuple[str, dict[str, Any]]:
        """Provision the join link, and never fail the meeting over it.

        WF-059's own rule, adopted rather than re-decided: "A booking whose
        Location cannot produce a conference is still a booking." So a failure
        here returns ``unlinked`` with the provider's reason, and every other
        write still happens.
        """
        booking_body = booking.get("booking") or {}
        attendee = booking_body.get("attendee") or {}
        payload = {
            "booking_uid": booking.get("uid"),
            "starts_at": booking.get("start"),
            "attendee_email": attendee.get("email"),
            "attendee_name": attendee.get("name"),
        }
        try:
            record = self.conferencing.book(room_id, payload, actor=actor, source=source)
        except ConferenceLinkError as exc:
            return MEETING_UNLINKED, {"url": None, "provider": None, "note": f"{exc.code}: {exc}"}
        url = record.get("meetingLocation")
        provider = record.get("location_provider") or record.get("provider")
        if not url:
            return MEETING_UNLINKED, {
                "url": None,
                "provider": provider,
                "note": f"the location {record.get('location_kind')!r} produced no link",
            }
        return MEETING_LINKED, {"url": url, "provider": provider, "note": None}

    def _complete_next_step_task(
        self, data: Mapping[str, Any], *, actor: str | None, source: str
    ) -> str | None:
        """Flip the proposal's MAP task to done through WF-124's own engine.

        A failure here is recorded rather than raised: the meeting is booked and
        the buyer's decision stands, and losing it because a plan row moved would
        be the worst of the two outcomes.
        """
        task_id = data.get("task_id")
        if not task_id:
            return None
        try:
            self.plans.update_task(str(task_id), {"status": "done"}, actor=actor, source=source)
        except (mutual.PlanNotFound, mutual.PlanError) as exc:
            return f"not_completed:{type(exc).__name__}"
        return "done"

    def _write_timeline(
        self, room_id: str, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        data = dict(payload)
        data.setdefault("at", _iso(self.now()))
        record = self.store.create(
            TIMELINE_COLLECTION, data, room_id=room_id, actor=actor, source=source
        )
        return {**data, "id": record.get("id")}

    def _annotate_room(
        self,
        room_id: str,
        meeting: Mapping[str, Any],
        *,
        proposal_id: str,
        summary: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any] | None:
        """Merge this workflow's convenience copy into the room row.

        A shallow merge at the field, then a shallow merge inside it, the shape
        WF-058 and WF-124 already use, so one workflow's annotation cannot
        overwrite another's. The meetings list is capped; the meeting records are
        the durable copy.
        """
        room = self.store.get(room_id)
        if room is None:
            return None
        existing = (room.get("data") or {}).get(ROOM_FIELD)
        body = dict(existing) if isinstance(existing, Mapping) else {}
        meetings = list(body.get("meetings") or [])
        meetings.append(
            {
                "id": meeting.get("id"),
                "start": (meeting.get("data") or {}).get("start"),
                "state": (meeting.get("data") or {}).get("state"),
                "summary": summary,
            }
        )
        body["meetings"] = meetings[-ROOM_MEETING_LIMIT:]
        body["next_step"] = {
            "proposal_id": proposal_id,
            "summary": summary,
            "state": PROPOSAL_BOOKED,
        }
        body["updated_at"] = _iso(self.now())
        return self.store.update(room_id, {ROOM_FIELD: body}, actor=actor, source=source)

    def _idle_state(self, room_id: str) -> tuple[bool, str]:
        """Whether the room has been quiet long enough, and since when.

        Read from the room's own activity rows, which WF-006 writes and the
        seeder populates. A room with no activity at all is idle: it was opened
        and nobody came, which is exactly the moment to offer a next step.
        """
        now = self.now()
        latest: datetime | None = None
        for record in self.store.list(ACTIVITY_COLLECTION, room_id=room_id, limit=500):
            at = _parse_instant((record.get("data") or {}).get("occurred_at"))
            if at is not None and (latest is None or at > latest):
                latest = at
        if latest is None:
            room = self.store.get(room_id) or {}
            latest = _parse_instant(room.get("created_at")) or now
        idle_seconds = (now - latest).total_seconds()
        return idle_seconds >= IDLE_MINUTES * 60, _iso(latest)

    def _auto_summary(self, room_id: str) -> tuple[str | None, str]:
        """What the automation proposes: the plan's own next open task, or a default."""
        for plan in self.plans.list_plans(room_id):
            plan_id = str(plan.get("id"))
            for task in self.plans.tasks_of(plan_id):
                if not mutual.is_done(task):
                    return task.get("id"), f"Book time for: {task.get('title')}"
        return None, "Book the next step"


__all__ = ["InRoomBookingEngine", "ACTIVITY_COLLECTION", "ROOM_COLLECTION"]
