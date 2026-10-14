"""The live-session engine: the only module in this package that reads or writes.

Every write goes through :class:`~dsr.store.RecordStore` and its
:class:`~dsr.db.audited.AuditedDatabase`, so the audit row is written in the
same transaction as the change. The engine never opens SQLite itself.

The clock is injected. Production passes the wall clock; a test passes a frozen
function, so no test depends on today's date.

What is real and what is a stand-in
-----------------------------------

This build records the *session*: who was invited, who joined, which step the
presenter revealed, what was asked, and what the session left behind. It does
not carry a live stream. No SFU runs, no WebSocket opens and no media is hosted.
A follower reads the presenter's cursor by polling the state route, so "sync" is
recorded state and the per-participant link is a stand-in URL. The recording is
a reference to an artefact that no object store holds. See
:data:`dsr.live_sessions.vocabulary.SOURCED_GAPS`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from dsr.db.audited import new_id
from dsr.live_sessions import rules, vocabulary as v
from dsr.live_sessions.errors import (
    REASON_ALREADY_ENDED,
    REASON_ALREADY_STARTED,
    REASON_NOT_PRESENTER,
    REASON_OUT_OF_RANGE,
    REASON_QUESTION_ALREADY_ANSWERED,
    REASON_SECOND_PRESENTER,
    REASON_SESSION_NOT_LIVE,
    REASON_SKIPPED_REVEAL,
    SessionConflict,
    SessionNotFound,
)
from dsr.store import RecordStore

Clock = Callable[[], datetime]


def _wall_clock() -> datetime:
    return datetime.now(timezone.utc)


class LiveSessionBook:
    """Sessions, their participants, their reveal cursor and their output."""

    def __init__(self, store: RecordStore, *, now: Clock | None = None) -> None:
        self.store = store
        self._now = now or _wall_clock

    # -- clock -------------------------------------------------------------- #

    def now_ms(self) -> int:
        return rules.parse_timestamp(self._now(), "now") or 0

    # -- vocabulary --------------------------------------------------------- #

    @staticmethod
    def vocabulary() -> dict[str, Any]:
        return v.published_vocabulary()

    # -- rooms, pages and recipients (reusing WF-002 and WF-004) ------------ #

    def require_room(self, room_id: str) -> dict[str, Any]:
        room = self.store.get(room_id)
        if room is None or room.get("collection") != "room":
            raise SessionNotFound(room_id, collection="room")
        return room

    def pages(self, room_id: str) -> list[dict[str, Any]]:
        """The room's pages in creation order: the deck the presenter reveals."""

        return self.store.list(
            v.PAGE_COLLECTION, room_id=room_id, limit=500, order_by="created_at", descending=False
        )

    def recipients(self, room_id: str) -> list[dict[str, Any]]:
        """The room's buyers, reusing WF-004's grant and invitation rows.

        A recipient is one principal email with the role WF-004 gave it. Grants
        win over invitations, and invitations over the room's ``viewers`` list,
        because the grant is the live record and the others are the offering.
        A room that declares nobody returns an empty list, which the caller reads
        as "unconfigured" rather than "everybody is refused".
        """

        self.require_room(room_id)
        room = self.store.get(room_id) or {}
        room_data = room.get("data") or {}

        found: dict[str, dict[str, Any]] = {}
        priority = {"grant": 3, "invitation": 2, "viewer": 1}

        def add(email: Any, role: Any, source: str) -> None:
            text = str(email or "").strip().lower()
            if not text:
                return
            existing = found.get(text)
            if existing is not None and priority.get(existing["source"], 0) >= priority[source]:
                return
            found[text] = {"email": text, "role": str(role or v.DEFAULT_ROLE), "source": source}

        for grant in self.store.list(v.ACCESS_GRANTS, room_id=room_id, limit=500):
            data = grant.get("data") or {}
            add(data.get("principal"), data.get("role"), "grant")
        for invitation in self.store.list(v.INVITATIONS, room_id=room_id, limit=500):
            data = invitation.get("data") or {}
            add(data.get("email"), data.get("role"), "invitation")
        for email in rules.normalise_emails(room_data.get(v.ROOM_VIEWERS_FIELD)):
            add(email, v.DEFAULT_ROLE, "viewer")
        return list(found.values())

    # -- sessions: reads ---------------------------------------------------- #

    def require_session(self, session_id: str) -> dict[str, Any]:
        record = self.store.get(session_id)
        if record is None or record.get("collection") != v.SESSION_COLLECTION:
            raise SessionNotFound(session_id, collection="session")
        return record

    def get_session(self, session_id: str) -> dict[str, Any]:
        return rules.session_view(self.require_session(session_id))

    def sessions(
        self,
        *,
        room_id: str | None = None,
        state: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        records = self.store.list(
            v.SESSION_COLLECTION,
            room_id=room_id,
            limit=limit,
            order_by="created_at",
            descending=True,
        )
        views = [rules.session_view(record) for record in records]
        if state:
            views = [view for view in views if view["state"] == state]
        return views

    # -- sessions: writes --------------------------------------------------- #

    def create_session(
        self,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Create a session from the room, plan the reveal order, invite people.

        The agenda is the room's own pages (WF-002). With no explicit invitees
        the invitees are the room's recipients (WF-004), so a session started
        from a configured room already knows who was asked. A per-participant
        link is issued for each participant, one token each.
        """

        fields = rules.validate_session(payload)
        room_id = str(fields["room_id"])
        room = self.require_room(room_id)
        room_data = room.get("data") or {}

        presenter = fields["presenter"] or _owner_email(room_data)
        invitees = fields["invitees"] or [row["email"] for row in self.recipients(room_id)]

        now_ms = self.now_ms()
        agenda = rules.plan_agenda(self.pages(room_id))
        session_id = new_id("wf128_sess")
        record = self.store.create(
            v.SESSION_COLLECTION,
            {
                "title": fields["title"]
                or f"Live session in {room_data.get('name') or 'the room'}",
                "state": v.STATE_SCHEDULED,
                "presenter": presenter,
                "agenda": agenda,
                "position": 0,
                "revealed": 0,
                "transport": v.TRANSPORT_STAND_IN,
                "scheduled_for_ms": fields["scheduled_for_ms"],
                "created_at_ms": now_ms,
                "started_at_ms": None,
                "started_at": None,
                "ended_at_ms": None,
                "ended_at": None,
                "recording_id": None,
            },
            record_id=session_id,
            room_id=room_id,
            actor=actor,
            source=source,
        )

        invited: list[dict[str, Any]] = []
        if presenter:
            invited.append(
                self._add_participant(
                    session_id,
                    room_id,
                    presenter,
                    v.ROLE_PRESENTER,
                    None,
                    now_ms=now_ms,
                    source=source,
                    actor=actor,
                )
            )
        for email in invitees:
            if presenter and email == presenter:
                continue
            invited.append(
                self._add_participant(
                    session_id,
                    room_id,
                    email,
                    v.ROLE_FOLLOWER,
                    None,
                    now_ms=now_ms,
                    source=source,
                    actor=actor,
                )
            )

        view = rules.session_view(record)
        return {
            **view,
            "participants": [rules.participant_view(row) for row in invited],
            "room_name": room_data.get("name"),
        }

    def invite(
        self,
        session_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Add one participant, one per-participant link, and notify them.

        A session has exactly one presenter. Inviting a second presenter is
        refused rather than silently demoting one, because "the presenter" would
        stop being unambiguous.
        """

        session = self.require_session(session_id)
        fields = rules.validate_invite(payload)
        room_id = session.get("room_id")
        now_ms = self.now_ms()

        if fields["role"] == v.ROLE_PRESENTER and self._presenter_row(session_id) is not None:
            raise SessionConflict(
                REASON_SECOND_PRESENTER,
                f"session {session_id} already has a presenter",
            )

        record = self._add_participant(
            session_id,
            str(room_id) if room_id else None,
            str(fields["email"]),
            str(fields["role"]),
            fields["name"],
            now_ms=now_ms,
            source=source,
            actor=actor,
        )
        return rules.participant_view(record)

    def start_session(
        self,
        session_id: str,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Start the live session, begin recording and notify every invitee.

        "Auto-record and auto-publish" begins here: a recording row is created in
        the recording state. No recorder runs; the state is what is real.
        """

        session = self.require_session(session_id)
        data = session.get("data") or {}
        state = str(data.get("state"))
        if state == v.STATE_LIVE:
            raise SessionConflict(REASON_ALREADY_STARTED, f"session {session_id} is already live")
        if state == v.STATE_ENDED:
            raise SessionConflict(REASON_ALREADY_ENDED, f"session {session_id} has ended")

        room_id = session.get("room_id")
        now_ms = self.now_ms()
        recording = self.store.create(
            v.RECORDING_COLLECTION,
            {
                "session_id": session_id,
                "status": v.RECORDING_RECORDING,
                "replay_ref": None,
                "playback_url": None,
                "duration_seconds": None,
                "started_at_ms": now_ms,
                "published_at_ms": None,
                "stand_in": True,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )

        updated = self.store.update(
            session_id,
            {
                "state": v.STATE_LIVE,
                "started_at_ms": now_ms,
                "started_at": rules.to_iso(now_ms),
                "recording_id": recording["id"],
            },
            actor=actor,
            source=source,
        )

        self._event(
            session_id,
            room_id,
            v.EVENT_SESSION_STARTED,
            actor=actor,
            detail="The presenter started the session.",
            now_ms=now_ms,
            source=source,
        )
        notified = self._notify(
            session_id=session_id,
            room_id=room_id,
            kind=v.EVENT_PARTICIPANT_INVITED,
            message="is live now.",
            now_ms=now_ms,
            source=source,
            actor=actor,
        )

        view = rules.session_view(updated)
        return {
            **view,
            "recording": rules.recording_view(recording),
            "notified": [rules.notification_view(row) for row in notified],
        }

    def end_session(
        self,
        session_id: str,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """End the session, publish the replay and close every attendance row.

        "the session is recorded and attached to the room as a replay the buyer
        can revisit asynchronously". Publishing is deterministic on end: the
        recording flips to published with a stand-in reference and URL.
        """

        session = self.require_session(session_id)
        data = session.get("data") or {}
        if str(data.get("state")) != v.STATE_LIVE:
            raise SessionConflict(REASON_SESSION_NOT_LIVE, f"session {session_id} is not live")

        room_id = session.get("room_id")
        now_ms = self.now_ms()
        started_at_ms = data.get("started_at_ms")
        duration = (
            max(0, int(round((now_ms - int(started_at_ms)) / 1000)))
            if started_at_ms is not None
            else 0
        )

        recording_view = None
        recording_id = data.get("recording_id")
        if recording_id:
            published = self.store.update(
                str(recording_id),
                {
                    "status": v.RECORDING_PUBLISHED,
                    "replay_ref": f"artefact://wf128/{session_id}",
                    "playback_url": rules.replay_url(session_id),
                    "duration_seconds": duration,
                    "published_at_ms": now_ms,
                    "published_at": rules.to_iso(now_ms),
                },
                actor=actor,
                source=source,
            )
            recording_view = rules.recording_view(published)
            self._event(
                session_id,
                room_id,
                v.EVENT_RECORDING_PUBLISHED,
                actor=actor,
                detail="The replay was published to the room.",
                now_ms=now_ms,
                source=source,
            )

        updated = self.store.update(
            session_id,
            {
                "state": v.STATE_ENDED,
                "ended_at_ms": now_ms,
                "ended_at": rules.to_iso(now_ms),
            },
            actor=actor,
            source=source,
        )
        self._event(
            session_id,
            room_id,
            v.EVENT_SESSION_ENDED,
            actor=actor,
            detail="The presenter ended the session.",
            now_ms=now_ms,
            source=source,
        )

        # Every participant who is still present leaves at the end.
        for participant in self.participants(session_id=session_id, status=v.PARTICIPANT_JOINED):
            self.store.update(
                participant["id"],
                {
                    "status": v.PARTICIPANT_LEFT,
                    "left_at_ms": now_ms,
                    "left_at": rules.to_iso(now_ms),
                },
                actor=actor,
                source=source,
            )
        self._close_attendance(session_id, now_ms=now_ms, source=source, actor=actor)

        view = rules.session_view(updated)
        return {
            **view,
            "recording": recording_view,
            "attendance": self.attendance(session_id=session_id),
        }

    # -- the reveal cursor -------------------------------------------------- #

    def reveal(
        self,
        session_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Move the presenter's cursor, respecting the reveal order.

        Only the presenter controls the cursor. A forward move is one step; a
        backward move is any earlier step. A forward jump would reveal a step
        before an earlier one, so it is refused with a stable token.
        """

        session = self.require_session(session_id)
        data = session.get("data") or {}
        if str(data.get("state")) != v.STATE_LIVE:
            raise SessionConflict(REASON_SESSION_NOT_LIVE, f"session {session_id} is not live")

        fields = rules.validate_move(payload)
        agenda = list(data.get("agenda") or [])
        role = self._role_for(session_id, data, payload.get("as") or actor)
        current = int(data.get("position") or 0)

        ok, reason = rules.reveal_decision(
            role=role, current=current, target=int(fields["position"]), last=len(agenda) - 1
        )
        if not ok:
            detail = {
                REASON_NOT_PRESENTER: "only the presenter drives the session",
                REASON_SKIPPED_REVEAL: "a step cannot be revealed before the step before it",
                REASON_OUT_OF_RANGE: f"position must be between 0 and {len(agenda) - 1}",
            }.get(str(reason), "that move is refused")
            raise SessionConflict(str(reason), detail)

        target = int(fields["position"])
        now_ms = self.now_ms()
        updated = self.store.update(
            session_id,
            {
                "position": target,
                "revealed": max(int(data.get("revealed") or 0), target),
            },
            actor=actor,
            source=source,
        )
        kind = v.EVENT_STEP_REVEALED if target >= current else v.EVENT_STEP_BACK
        self._event(
            session_id,
            session.get("room_id"),
            kind,
            actor=payload.get("as") or actor,
            detail=f"Step {target + 1} of {len(agenda)}.",
            now_ms=now_ms,
            source=source,
            position=target,
        )
        return {
            **rules.session_view(updated),
            "moved_from": current,
            "direction": "forward" if target > current else "back" if target < current else "hold",
        }

    def state(self, session_id: str, *, viewer: str | None = None) -> dict[str, Any]:
        """The read a follower polls to see the presenter's current step.

        Read-only, so a poll never writes. A follower acknowledges with
        :meth:`sync`, which is a write and therefore a route the audit can name.
        """

        session = self.require_session(session_id)
        view = rules.session_view(session)
        participants = self.participants(session_id=session_id)
        current = view["position"]
        needle = (viewer or "").strip().lower()
        return {
            **view,
            "viewer": needle or None,
            "participants": [rules.participant_view(row) for row in participants],
            "presenter_participant": _presenter_email(participants),
            "follower_count": len(
                [row for row in participants if _status(row) == v.PARTICIPANT_JOINED]
            ),
            "following": current,
        }

    def sync(
        self,
        session_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """A follower acknowledges the presenter's cursor and gets the step back.

        This is the stand-in for a live transport: the follower reports which
        step they now show, and the write is what proves they were in sync.
        """

        session = self.require_session(session_id)
        view = rules.session_view(session)
        participant = self._participant_by_email(session_id, payload.get("as") or actor)
        if participant is None:
            raise SessionNotFound(str(payload.get("as") or actor or ""), collection="participant")

        now_ms = self.now_ms()
        updated = self.store.update(
            participant["id"],
            {"last_seen_position": int(view["position"]), "last_seen_at_ms": now_ms},
            actor=actor,
            source=source,
        )
        if _status(participant) == v.PARTICIPANT_INVITED:
            updated = self.store.update(
                participant["id"],
                {
                    "status": v.PARTICIPANT_JOINED,
                    "last_seen_position": int(view["position"]),
                    "last_seen_at_ms": now_ms,
                    "joined_at_ms": now_ms,
                    "joined_at": rules.to_iso(now_ms),
                },
                actor=actor,
                source=source,
            )
            self._event(
                session_id,
                session.get("room_id"),
                v.EVENT_PARTICIPANT_JOINED,
                actor=str(updated.get("data", {}).get("email")),
                detail="A participant joined and is following.",
                now_ms=now_ms,
                source=source,
            )
            self._attend(
                session_id,
                session.get("room_id"),
                updated,
                now_ms=now_ms,
                source=source,
                actor=actor,
            )
        return {
            **view,
            "participant": rules.participant_view(updated),
            "following": int(view["position"]),
        }

    # -- questions ---------------------------------------------------------- #

    def ask(
        self,
        session_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Log a question against the session and therefore against the deal."""

        session = self.require_session(session_id)
        fields = rules.validate_question(payload)
        now_ms = self.now_ms()
        record = self.store.create(
            v.QUESTION_COLLECTION,
            {
                "session_id": session_id,
                "asker": fields["asker"],
                "text": fields["text"],
                "status": v.QUESTION_OPEN,
                "answer": None,
                "asked_at_ms": now_ms,
                "asked_at": rules.to_iso(now_ms),
                "answered_at_ms": None,
                "answered_at": None,
            },
            room_id=session.get("room_id"),
            actor=actor,
            source=source,
        )
        self._event(
            session_id,
            session.get("room_id"),
            v.EVENT_QUESTION_ASKED,
            actor=str(fields["asker"]),
            detail=str(fields["text"]),
            now_ms=now_ms,
            source=source,
        )
        return {
            **rules.question_view(record),
            "deal_ref": session.get("room_id"),
        }

    def answer(
        self,
        question_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Answer a logged question, in the session or after it."""

        record = self.store.get(question_id)
        if record is None or record.get("collection") != v.QUESTION_COLLECTION:
            raise SessionNotFound(question_id, collection="question")
        data = record.get("data") or {}
        if str(data.get("status")) == v.QUESTION_ANSWERED:
            raise SessionConflict(
                REASON_QUESTION_ALREADY_ANSWERED,
                f"question {question_id} is already answered",
            )
        fields = rules.validate_answer(payload)
        now_ms = self.now_ms()
        updated = self.store.update(
            question_id,
            {
                "status": v.QUESTION_ANSWERED,
                "answer": fields["answer"],
                "answered_at_ms": now_ms,
                "answered_at": rules.to_iso(now_ms),
            },
            actor=actor,
            source=source,
        )
        session_id = data.get("session_id")
        if session_id:
            self._event(
                str(session_id),
                record.get("room_id"),
                v.EVENT_QUESTION_ANSWERED,
                actor=actor,
                detail=str(fields["answer"]),
                now_ms=now_ms,
                source=source,
            )
        return rules.question_view(updated)

    # -- reads -------------------------------------------------------------- #

    def participants(
        self, *, session_id: str | None = None, status: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        records = self.store.list(
            v.PARTICIPANT_COLLECTION,
            limit=limit,
            order_by="created_at",
            descending=False,
        )
        views = [rules.participant_view(record) for record in records]
        if session_id:
            views = [view for view in views if view["session_id"] == session_id]
        if status:
            views = [view for view in views if view["status"] == status]
        return views

    def events(self, *, session_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        records = self.store.list(
            v.EVENT_COLLECTION, limit=limit, order_by="created_at", descending=True
        )
        views = [rules.event_view(record) for record in records]
        if session_id:
            views = [view for view in views if view["session_id"] == session_id]
        return views

    def questions(
        self, *, session_id: str | None = None, status: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        records = self.store.list(
            v.QUESTION_COLLECTION, limit=limit, order_by="created_at", descending=False
        )
        views = [rules.question_view(record) for record in records]
        if session_id:
            views = [view for view in views if view["session_id"] == session_id]
        if status:
            views = [view for view in views if view["status"] == status]
        return views

    def attendance(
        self, *, session_id: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        records = self.store.list(
            v.ATTENDANCE_COLLECTION, limit=limit, order_by="created_at", descending=False
        )
        views = [rules.attendance_view(record) for record in records]
        if session_id:
            views = [view for view in views if view["session_id"] == session_id]
        return views

    def recordings(
        self, *, session_id: str | None = None, room_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        records = self.store.list(
            v.RECORDING_COLLECTION,
            room_id=room_id,
            limit=limit,
            order_by="created_at",
            descending=True,
        )
        views = [rules.recording_view(record) for record in records]
        if session_id:
            views = [view for view in views if view["session_id"] == session_id]
        return views

    def notifications(
        self, *, session_id: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        records = self.store.list(
            v.NOTIFICATION_COLLECTION, limit=limit, order_by="created_at", descending=True
        )
        views = [rules.notification_view(record) for record in records]
        if session_id:
            views = [view for view in views if view["session_id"] == session_id]
        return views

    # -- the seller's summary ---------------------------------------------- #

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        sessions = self.sessions(room_id=room_id, limit=500)
        live = [row for row in sessions if row["state"] == v.STATE_LIVE]
        ended = [row for row in sessions if row["state"] == v.STATE_ENDED]
        live_ids = {row["id"] for row in sessions}
        participants = [
            row for row in self.participants(limit=2000) if row["session_id"] in live_ids
        ]
        attendance = [row for row in self.attendance(limit=2000) if row["session_id"] in live_ids]
        questions = [row for row in self.questions(limit=2000) if row["session_id"] in live_ids]
        recordings = [
            row
            for row in self.recordings(room_id=room_id, limit=500)
            if row["session_id"] in live_ids
        ]
        return {
            "sessions": len(sessions),
            "live": len(live),
            "ended": len(ended),
            "participants": len(participants),
            "joined": len([row for row in participants if row["status"] == v.PARTICIPANT_JOINED]),
            "attended": len([row for row in attendance if row["attended"]]),
            "questions": len(questions),
            "open_questions": len([row for row in questions if row["status"] == v.QUESTION_OPEN]),
            "replays": len([row for row in recordings if row["status"] == v.RECORDING_PUBLISHED]),
            "transport": v.TRANSPORT_STAND_IN,
            "generated_at": rules.to_iso(self.now_ms()),
        }

    # -- internals ---------------------------------------------------------- #

    def _presenter_row(self, session_id: str) -> dict[str, Any] | None:
        for row in self.participants(session_id=session_id):
            if row["role"] == v.ROLE_PRESENTER:
                return row
        return None

    def _participant_by_email(self, session_id: str, email: Any) -> dict[str, Any] | None:
        needle = str(email or "").strip().lower()
        if not needle:
            return None
        for record in self.store.list(v.PARTICIPANT_COLLECTION, limit=500):
            data = record.get("data") or {}
            if (
                data.get("session_id") == session_id
                and str(data.get("email") or "").lower() == needle
            ):
                return record
        return None

    def _role_for(self, session_id: str, data: Mapping[str, Any], who: Any) -> str:
        """Resolve who is acting. Absent ``as`` means the presenter, who is the rep."""

        needle = str(who or "").strip().lower()
        if not needle:
            return v.ROLE_PRESENTER
        presenter = str(data.get("presenter") or "").strip().lower()
        if presenter and needle == presenter:
            return v.ROLE_PRESENTER
        participant = self._participant_by_email(session_id, needle)
        if participant is None:
            raise SessionNotFound(needle, collection="participant")
        return str((participant.get("data") or {}).get("role") or v.ROLE_FOLLOWER)

    def _add_participant(
        self,
        session_id: str,
        room_id: str | None,
        email: str,
        role: str,
        name: str | None,
        *,
        now_ms: int,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        existing = self._participant_by_email(session_id, email)
        if existing is not None:
            return existing
        if role == v.ROLE_PRESENTER and self._presenter_row(session_id) is not None:
            raise SessionConflict(
                REASON_SECOND_PRESENTER, f"session {session_id} already has a presenter"
            )
        token = new_id("wf128_join")
        record = self.store.create(
            v.PARTICIPANT_COLLECTION,
            {
                "session_id": session_id,
                "email": email,
                "name": name,
                "role": role,
                "status": v.PARTICIPANT_INVITED,
                "join_token": token,
                "join_url": rules.join_url(token),
                "joined_at_ms": None,
                "joined_at": None,
                "left_at_ms": None,
                "left_at": None,
                "last_seen_position": None,
                "last_seen_at_ms": None,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        self._event(
            session_id,
            room_id,
            v.EVENT_PARTICIPANT_INVITED,
            actor=actor,
            subject=email,
            detail=f"Invited as {role}.",
            now_ms=now_ms,
            source=source,
        )
        self._event(
            session_id,
            room_id,
            v.EVENT_ACCESS_LINK_ISSUED,
            actor=actor,
            subject=email,
            detail="A per-participant link was issued.",
            now_ms=now_ms,
            source=source,
        )
        self._attend(session_id, room_id, record, now_ms=now_ms, source=source, actor=actor)
        return record

    def _attend(
        self,
        session_id: str,
        room_id: str | None,
        participant: Mapping[str, Any],
        *,
        now_ms: int,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Create or refresh one attendance row for a participant."""

        data = participant.get("data") or {}
        email = str(data.get("email") or "")
        existing = self.store.find(
            v.ATTENDANCE_COLLECTION, {"session_id": session_id, "email": email}, limit=1
        )
        joined_at_ms = data.get("joined_at_ms")
        payload = {
            "session_id": session_id,
            "participant_id": participant.get("id"),
            "email": email,
            "role": data.get("role"),
            "state": rules.attendance_state(joined_at_ms),
            "joined_at_ms": joined_at_ms,
            "joined_at": data.get("joined_at"),
            "left_at_ms": data.get("left_at_ms"),
            "left_at": data.get("left_at"),
            "seconds": rules.attended_seconds(joined_at_ms, data.get("left_at_ms"), now_ms),
            # The research disagrees on the destination; this build writes to the
            # room record. See vocabulary.SOURCED_GAPS.
            "written_to": v.DESTINATION_ROOM,
        }
        if existing:
            return self.store.update(existing[0]["id"], payload, actor=actor, source=source)
        return self.store.create(
            v.ATTENDANCE_COLLECTION, payload, room_id=room_id, actor=actor, source=source
        )

    def _close_attendance(
        self, session_id: str, *, now_ms: int, source: str, actor: str | None
    ) -> None:
        for record in self.store.find(
            v.ATTENDANCE_COLLECTION, {"session_id": session_id}, limit=500
        ):
            data = record.get("data") or {}
            joined_at_ms = data.get("joined_at_ms")
            self.store.update(
                record["id"],
                {
                    "left_at_ms": now_ms,
                    "left_at": rules.to_iso(now_ms),
                    "seconds": rules.attended_seconds(joined_at_ms, now_ms, now_ms),
                },
                actor=actor,
                source=source,
            )

    def _notify(
        self,
        *,
        session_id: str,
        room_id: Any,
        kind: str,
        message: str,
        now_ms: int,
        source: str,
        actor: str | None,
    ) -> list[dict[str, Any]]:
        """Write one notification per participant. "auto-notify invitees"."""

        rows: list[dict[str, Any]] = []
        session = self.require_session(session_id)
        title = (session.get("data") or {}).get("title") or "The live session"
        for participant in self.participants(session_id=session_id):
            rows.append(
                self.store.create(
                    v.NOTIFICATION_COLLECTION,
                    {
                        "session_id": session_id,
                        "participant_id": participant["id"],
                        "recipient": participant["email"],
                        "kind": kind,
                        "channel": "email",
                        "message": f"{title} {message}",
                        "at_ms": now_ms,
                        "at": rules.to_iso(now_ms),
                    },
                    room_id=room_id,
                    actor=actor,
                    source=source,
                )
            )
        return rows

    def _event(
        self,
        session_id: str,
        room_id: Any,
        kind: str,
        *,
        actor: str | None,
        now_ms: int,
        source: str,
        subject: str | None = None,
        detail: str | None = None,
        position: int | None = None,
    ) -> dict[str, Any]:
        return self.store.create(
            v.EVENT_COLLECTION,
            {
                "session_id": session_id,
                "kind": kind,
                "actor": actor,
                "subject": subject,
                "detail": detail,
                "position": position,
                "at_ms": now_ms,
                "at": rules.to_iso(now_ms),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )


def _owner_email(room_data: Mapping[str, Any]) -> str | None:
    owner = room_data.get("owner")
    if not owner:
        return None
    text = str(owner).strip().lower()
    return text if "@" in text else None


def _status(record: Mapping[str, Any]) -> str:
    return str((record.get("data") or {}).get("status") or "")


def _presenter_email(participants: list[dict[str, Any]]) -> str | None:
    for row in participants:
        if row.get("role") == v.ROLE_PRESENTER:
            return row.get("email")
    return None


__all__ = ["LiveSessionBook"]
