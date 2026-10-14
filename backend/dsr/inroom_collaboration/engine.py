"""WF-132: every read and write in-room conversation makes, in one place.

The store is the only thing this module touches. There is no SQLite handle and no
HTTP object here, so the whole workflow is exercised in tests without a server,
and the feature module on top is a route table and nothing else.

What it builds
--------------

* **Threads and comments**, anchored to one of the four room objects the research
  names. A thread is the unit a buyer and a seller talk inside.
* **Mentions and watchers**, the "mentions/notifications" arrow: tagging a
  colleague resolves them against WF-004's room grants, records the notification,
  and subscribes them to the thread.
* **Resolution state**, the second arrow's target: open until someone resolves it,
  with resolving and reopening recorded rather than destructive.
* **Champion edits**, the third arrow: a buyer-side participant with a
  content-managing role edits a section through the same route a seller uses, and
  the change is recorded as a before/after delta in an append-only log.
* **Question escalation**: a buyer question with no seller or champion reply past
  the interval escalates to the room owner.

Every write takes a ``source`` keyword and passes it to the store. The feature
module builds it from ``router.prefix``, so the audit row names a route the app
actually serves. A domain function that hardcoded a URL string would leave the
audit log naming a route that stopped existing, which is the defect WF-004's port
records by name.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from dsr.inroom_collaboration import rules as r, vocabulary as v
from dsr.inroom_collaboration.errors import (
    InRoomConflict,
    InRoomDenied,
    InRoomNotFound,
    InRoomValidation,
)
from dsr.store import RecordStore


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _end_of_day_utc(day: Any) -> datetime | None:
    """The instant a dated grant lapses: the last moment of that day in UTC.

    Restated here rather than imported: this package may import nothing but the
    store, and WF-004's rule is one line. The semantics are its semantics - a
    grant valid until a date is valid for the whole of that UTC day.
    """
    text = str(day or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return parsed.replace(hour=23, minute=59, second=59, microsecond=999_000)


class InRoomEngine:
    """Threads, comments, mentions, edits and escalations over the record store."""

    def __init__(self, store: RecordStore, *, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._clock = now or _utcnow

    # -- clock and lookups -------------------------------------------------- #

    def now(self) -> datetime:
        return self._clock()

    def _stamp(self) -> str:
        return self.now().isoformat(timespec="milliseconds")

    def _record(self, collection: str, record_id: Any) -> dict[str, Any]:
        """Fetch a record of one collection, or raise :class:`InRoomNotFound`."""
        row = None
        if isinstance(record_id, str) and record_id:
            try:
                row = self.store.get(record_id)
            except Exception:  # noqa: BLE001 - a malformed id is a miss, not a crash
                row = None
        if row is None or row.get("collection") != collection:
            raise InRoomNotFound(f"no {collection} {record_id!r} in this room")
        return row

    def room(self, room_id: str) -> dict[str, Any]:
        return self._record(v.ROOM_COLLECTION, room_id)

    @staticmethod
    def _data(record: Mapping[str, Any]) -> dict[str, Any]:
        return dict(record.get("data") or {})

    def _room_of(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = self._data(record)
        return self.room(record.get("room_id") or data.get("room_ref"))

    # -- identity, from WF-004's roles vocabulary --------------------------- #

    def role_of(self, room: Mapping[str, Any], actor: Any) -> str | None:
        """The role ``actor`` holds in this room, or ``None`` for no access.

        The owner is the room's ``owner`` field. Everyone else needs a grant in
        WF-004's ``room_access`` collection that has not lapsed, which is what
        makes removing someone really remove their ability to post.
        """
        who = r.normalise_principal(actor)
        data = self._data(room)
        if who and who == r.normalise_principal(data.get("owner")):
            return v.ROLE_OWNER
        if not who:
            return None
        room_id = room.get("id")
        now = self.now()
        for grant in self.store.list(v.ACCESS_COLLECTION, room_id=room_id, limit=500):
            grant_data = self._data(grant)
            if r.normalise_principal(grant_data.get("principal")) != who:
                continue
            deadline = _end_of_day_utc(grant_data.get("access_valid_until"))
            if deadline is not None and now > deadline:
                continue
            return str(grant_data.get("role") or v.ROLE_VIEWER)
        return None

    def identity(self, room_id: str, actor: Any) -> dict[str, Any]:
        """The actor's role, side and champion status in one room."""
        room = self.room(room_id)
        role = self.role_of(room, actor)
        side = r.side_for(actor, self._data(room).get("owner"))
        return {
            "principal": r.normalise_principal(actor),
            "role": role,
            "role_label": r.role_label(role),
            "side": side,
            "is_champion": r.is_champion(role, side),
        }

    def _require_commenter(self, room: Mapping[str, Any], actor: Any) -> dict[str, Any]:
        """Resolve an actor who may post, or raise :class:`InRoomDenied`."""
        who = r.normalise_principal(actor)
        if not who:
            raise InRoomDenied("an actor is required to post in this room")
        role = self.role_of(room, who)
        if not r.can_comment(role):
            raise InRoomDenied(f"{who} has {r.role_label(role)} and cannot post in this room")
        side = r.side_for(who, self._data(room).get("owner"))
        return {
            "principal": who,
            "role": role,
            "side": side,
            "is_champion": r.is_champion(role, side),
        }

    def _require_editor(self, room: Mapping[str, Any], actor: Any) -> dict[str, Any]:
        """Resolve an actor who may edit content, or raise :class:`InRoomDenied`.

        This is the champion rule. A buyer-side holder of a content-managing role
        passes, and so does the seller, through the same check: there is no
        buyer-only route, which is what "act on their own room without a seller"
        means in this build.
        """
        who = r.normalise_principal(actor)
        if not who:
            raise InRoomDenied("an actor is required to edit content")
        role = self.role_of(room, who)
        if not r.can_edit_content(role):
            raise InRoomDenied(
                f"{who} has {r.role_label(role)} and cannot edit content in this room"
            )
        side = r.side_for(who, self._data(room).get("owner"))
        return {
            "principal": who,
            "role": role,
            "side": side,
            "is_champion": r.is_champion(role, side),
        }

    def recipients(self, room: Mapping[str, Any], handle: Any) -> str | None:
        """Resolve a tagged handle to a principal in the room, or ``None``.

        The addressee list is the room's owner plus every live grant in WF-004's
        collection. A handle matches on the full principal or on its local part,
        so ``@dana`` and ``@dana@example.com`` both reach the same person. A
        handle nobody matches is unresolved, which is how the WF-004 dependency
        stays visible: without a grant there is no recipient to notify.
        """
        needle = r.normalise_principal(handle)
        if not needle:
            return None
        owner = r.normalise_principal(self._data(room).get("owner"))
        candidates: list[str] = [owner] if owner else []
        now = self.now()
        for grant in self.store.list(v.ACCESS_COLLECTION, room_id=room.get("id"), limit=500):
            grant_data = self._data(grant)
            deadline = _end_of_day_utc(grant_data.get("access_valid_until"))
            if deadline is not None and now > deadline:
                continue
            principal = r.normalise_principal(grant_data.get("principal"))
            if principal:
                candidates.append(principal)
        for principal in candidates:
            local = principal.split("@", 1)[0]
            if needle == principal or needle == local:
                return principal
        return None

    # -- threads ------------------------------------------------------------ #

    def open_thread(
        self, room_id: str, payload: Mapping[str, Any], *, source: str
    ) -> dict[str, Any]:
        """Open a thread and write its first comment in one all-or-nothing step.

        A thread with no comment is not a conversation; writing both through one
        ``transaction()`` block is what stops a half-created thread existing in
        the audit log.
        """
        room = self.room(room_id)
        actor = self._require_commenter(room, payload.get("actor"))
        anchor_type, anchor_id = r.validate_anchor(
            payload.get("anchor_type"), payload.get("anchor_id")
        )
        body = r.normalise_body(payload.get("body"))
        kind = self._comment_kind(payload.get("kind"))
        label = r.anchor_label(anchor_type, payload.get("anchor_label"), anchor_id)
        stamp = self._stamp()

        with self.store.db.transaction(actor=actor["principal"]) as tx:
            thread = tx.create(
                v.THREAD_COLLECTION,
                {
                    "room_ref": room_id,
                    "anchor_type": anchor_type,
                    "anchor_id": anchor_id,
                    "anchor_label": label,
                    "state": v.STATE_OPEN,
                    "created_by": actor["principal"],
                    "created_side": actor["side"],
                    "watchers": [actor["principal"]],
                    "message_count": 0,
                    "last_activity_at": stamp,
                    "map_task_ref": None,
                    "contested": v.CONTESTED,
                },
                room_id=room_id,
                actor=actor["principal"],
                source=source,
            )
            self._write_comment(tx, thread, actor, body, kind, source=source)
            thread_id = thread["id"]
        # Read the committed row back: the dict above predates the comment's
        # watcher and message-count update, and a view built from it would show a
        # thread with no messages.
        fresh = self.store.require(thread_id)
        return self._thread_view(fresh)

    def list_threads(
        self,
        *,
        room_id: str | None = None,
        anchor_type: str | None = None,
        anchor_id: str | None = None,
        state: str | None = None,
        principal: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Threads, newest activity first, optionally filtered.

        Filtering on the anchor, the state or a watcher in Python rather than SQL
        keeps the query simple and the semantics exact: ``principal`` matches the
        watcher list, which is a JSON array the dynamic index flattens to
        positions rather than to a set. Readers are cheap here because a room has
        tens of threads, not tens of thousands.
        """
        rows = self.store.list(v.THREAD_COLLECTION, room_id=room_id, limit=min(limit, 1000))
        needle = r.normalise_principal(principal) if principal else None
        result: list[dict[str, Any]] = []
        for record in rows:
            data = self._data(record)
            if anchor_type and data.get("anchor_type") != anchor_type:
                continue
            if anchor_id and data.get("anchor_id") != anchor_id:
                continue
            if state and data.get("state") != state:
                continue
            watchers = [r.normalise_principal(w) for w in data.get("watchers") or []]
            if needle and needle not in watchers:
                continue
            result.append(record)
        result.sort(
            key=lambda rec: str(self._data(rec).get("last_activity_at") or ""), reverse=True
        )
        return [self._thread_view(record, brief=True) for record in result[:limit]]

    def read_thread(self, thread_id: str) -> dict[str, Any]:
        record = self._record(v.THREAD_COLLECTION, thread_id)
        return self._thread_view(record)

    def add_comment(
        self, thread_id: str, payload: Mapping[str, Any], *, source: str
    ) -> dict[str, Any]:
        """Reply in a thread, or post a question into it.

        A resolved thread refuses a plain comment with a 409 and a stable reason;
        the caller reopens it first. That is a deliberate choice over silently
        reopening: a reopen is a decision someone made, and hiding it would erase
        the resolution from the flow.
        """
        record = self._record(v.THREAD_COLLECTION, thread_id)
        room = self._room_of(record)
        actor = self._require_commenter(room, payload.get("actor"))
        if self._data(record).get("state") != v.STATE_OPEN:
            raise InRoomConflict(InRoomConflict.REASON_THREAD_RESOLVED)
        body = r.normalise_body(payload.get("body"))
        kind = self._comment_kind(payload.get("kind"))
        reply_to = payload.get("reply_to")
        if kind == v.KIND_REPLY and not reply_to:
            raise InRoomValidation("a reply must name the comment it answers")
        comment = self._write_comment(
            self.store, record, actor, body, kind, source=source, reply_to=reply_to
        )
        return {"thread_id": thread_id, "comment": comment}

    def resolve_thread(
        self, thread_id: str, payload: Mapping[str, Any], *, source: str
    ) -> dict[str, Any]:
        record = self._record(v.THREAD_COLLECTION, thread_id)
        room = self._room_of(record)
        actor = self._require_commenter(room, payload.get("actor"))
        if self._data(record).get("state") == v.STATE_RESOLVED:
            raise InRoomConflict(InRoomConflict.REASON_THREAD_ALREADY_RESOLVED)
        updated = self.store.update(
            thread_id,
            {
                "state": v.STATE_RESOLVED,
                "resolved_by": actor["principal"],
                "resolved_at": self._stamp(),
            },
            actor=actor["principal"],
            source=source,
        )
        return self._thread_view(updated)

    def reopen_thread(
        self, thread_id: str, payload: Mapping[str, Any], *, source: str
    ) -> dict[str, Any]:
        record = self._record(v.THREAD_COLLECTION, thread_id)
        room = self._room_of(record)
        actor = self._require_commenter(room, payload.get("actor"))
        if self._data(record).get("state") != v.STATE_RESOLVED:
            raise InRoomConflict(InRoomConflict.REASON_THREAD_NOT_RESOLVED)
        updated = self.store.update(
            thread_id,
            {
                "state": v.STATE_OPEN,
                "reopened_by": actor["principal"],
                "reopened_at": self._stamp(),
            },
            actor=actor["principal"],
            source=source,
        )
        return self._thread_view(updated)

    def tag(self, thread_id: str, payload: Mapping[str, Any], *, source: str) -> dict[str, Any]:
        """Tag colleagues into an existing thread.

        The body is the source of a tag on a new comment; this route exists so a
        thread can pull someone in without writing a comment whose only content is
        the tag. Each handle resolves to a live participant, is recorded, and is
        subscribed to the thread.
        """
        record = self._record(v.THREAD_COLLECTION, thread_id)
        room = self._room_of(record)
        actor = self._require_commenter(room, payload.get("actor"))
        handles = self._handles(payload)
        mentions = self._write_mentions(self.store, room, record, None, handles, source=source)
        watchers = self._watchers_with(
            self._data(record), [m["principal"] for m in mentions if m["principal"]]
        )
        self.store.update(
            thread_id,
            {"watchers": watchers, "last_activity_at": self._stamp()},
            actor=actor["principal"],
            source=source,
        )
        return {"thread_id": thread_id, "mentions": mentions, "watchers": watchers}

    def watch(self, thread_id: str, payload: Mapping[str, Any], *, source: str) -> dict[str, Any]:
        """Subscribe a principal to a thread.

        Auto-subscription covers the author and every recipient; this route lets a
        seller add themselves to a thread they were not tagged into, which is the
        "auto-subscribe watchers" behaviour made explicit rather than inferred.
        """
        record = self._record(v.THREAD_COLLECTION, thread_id)
        room = self._room_of(record)
        actor = self._require_commenter(room, payload.get("actor"))
        principal = r.normalise_principal(payload.get("principal") or actor["principal"])
        watchers = self._watchers_with(self._data(record), [principal])
        updated = self.store.update(
            thread_id, {"watchers": watchers}, actor=actor["principal"], source=source
        )
        return {"thread_id": thread_id, "watchers": self._data(updated).get("watchers") or watchers}

    def convert_to_task(
        self, thread_id: str, payload: Mapping[str, Any], *, source: str
    ) -> dict[str, Any]:
        """Record the auto-thread-to-MAP-task conversion the research names.

        The conversion row names the thread the task should come from and the
        details a task needs. The task row itself belongs to WF-124 and is not
        written here; if the caller already created one it may pass
        ``map_task_ref`` and the thread is linked to it. Refusing to write another
        workflow's collection is the boundary the feature contract draws.
        """
        record = self._record(v.THREAD_COLLECTION, thread_id)
        room = self._room_of(record)
        actor = self._require_commenter(room, payload.get("actor"))
        data = self._data(record)
        if data.get("map_task_ref"):
            raise InRoomConflict(InRoomConflict.REASON_ALREADY_CONVERTED)
        comments = self._comments_of(thread_id)
        excerpt = (self._data(comments[-1]).get("body") if comments else "") or ""
        title = str(payload.get("title") or data.get("anchor_label") or "").strip()
        if not title:
            title = (excerpt[:80] or "Thread follow-up").strip()
        owner_side = str(payload.get("owner_side") or v.SIDE_SELLER).strip().lower()
        if owner_side not in v.SIDES:
            owner_side = v.SIDE_SELLER
        conversion = self.store.create(
            v.CONVERSION_COLLECTION,
            {
                "room_ref": room.get("id"),
                "thread_ref": thread_id,
                "title": title,
                "owner": str(payload.get("owner") or "").strip() or None,
                "owner_side": owner_side,
                "due_date": str(payload.get("due_date") or "").strip() or None,
                "map_task_ref": str(payload.get("map_task_ref") or "").strip() or None,
                "converted_by": actor["principal"],
                "converted_side": actor["side"],
                "at": self._stamp(),
            },
            room_id=room.get("id"),
            actor=actor["principal"],
            source=source,
        )
        if conversion["data"].get("map_task_ref"):
            self.store.update(
                thread_id,
                {"map_task_ref": conversion["data"]["map_task_ref"]},
                actor=actor["principal"],
                source=source,
            )
        return self._conversion_view(conversion)

    # -- champion edits ----------------------------------------------------- #

    def record_edit(
        self, room_id: str, payload: Mapping[str, Any], *, source: str
    ) -> dict[str, Any]:
        """Record a champion editing a section, as a content delta in a change log.

        The research's flow is "champion edits -> content delta -> change log", and
        this route is that in one write: the before and after are kept, the delta
        between them is derived, and whether the editor was the buyer-side champion
        is recorded, which is the self-service evidence the workflow exists to
        produce.
        """
        room = self.room(room_id)
        actor = self._require_editor(room, payload.get("actor"))
        section = str(payload.get("section_ref") or payload.get("anchor_id") or "").strip()
        if not section:
            raise InRoomValidation("section_ref is required", {"section_ref": "required"})
        before = self._mapping(payload.get("before"))
        after = self._mapping(payload.get("after"))
        if not before and not after:
            raise InRoomValidation("a before or an after value is required")
        record = self.store.create(
            v.EDIT_COLLECTION,
            {
                "room_ref": room_id,
                "section_ref": section,
                "anchor_type": str(payload.get("anchor_type") or "").strip() or None,
                "summary": str(payload.get("summary") or "").strip() or None,
                "before": before,
                "after": after,
                "delta": self._delta(before, after),
                "actor": actor["principal"],
                "actor_role": actor["role"],
                "side": actor["side"],
                "champion": actor["is_champion"],
                "at": self._stamp(),
            },
            room_id=room_id,
            actor=actor["principal"],
            source=source,
        )
        return self._edit_view(record)

    def list_edits(self, room_id: str | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.store.list(v.EDIT_COLLECTION, room_id=room_id, limit=min(limit, 1000))
        rows.sort(key=lambda rec: str(self._data(rec).get("at") or ""), reverse=True)
        return [self._edit_view(record) for record in rows]

    def champion_activity(self, room_id: str | None = None) -> dict[str, Any]:
        """The buyer-side champion's self-service edits, the flow's win condition."""
        edits = [edit for edit in self.list_edits(room_id) if edit["champion"]]
        by_principal: dict[str, int] = {}
        for edit in edits:
            key = edit["actor"] or "unknown"
            by_principal[key] = by_principal.get(key, 0) + 1
        return {
            "room_id": room_id,
            "champion_edits": len(edits),
            "editors": [
                {"principal": who, "edits": count}
                for who, count in sorted(by_principal.items(), key=lambda pair: (-pair[1], pair[0]))
            ],
            "edits": edits,
        }

    # -- escalations -------------------------------------------------------- #

    def refresh_escalations(
        self, room_id: str | None = None, *, source: str
    ) -> list[dict[str, Any]]:
        """Write one escalation row per unanswered buyer question, and return them.

        Idempotent: a question that already has an escalation row is skipped, so a
        sweep that runs every minute still writes one row per question. The sweep
        is explicit rather than automatic because escalation depends on time
        passing, and nothing advances a clock inside a request.
        """
        rooms = [self.room(room_id)] if room_id else self.store.list(v.ROOM_COLLECTION, limit=1000)
        existing = {
            r.normalise_principal(self._data(row).get("comment_ref"))
            for row in self.store.list(v.ESCALATION_COLLECTION, room_id=room_id, limit=1000)
        }
        now = self.now()
        created: list[dict[str, Any]] = []
        for room in rooms:
            for thread in self.store.list(v.THREAD_COLLECTION, room_id=room.get("id"), limit=500):
                thread_data = self._data(thread)
                if thread_data.get("state") == v.STATE_RESOLVED:
                    continue
                comments = [
                    self._data(row) | {"id": row["id"]} for row in self._comments_of(thread["id"])
                ]
                for question in r.unanswered_buyer_questions(
                    comments, now=now, thread_state=thread_data.get("state", v.STATE_OPEN)
                ):
                    key = r.normalise_principal(question["comment_ref"])
                    if key in existing:
                        continue
                    record = self.store.create(
                        v.ESCALATION_COLLECTION,
                        {
                            "room_ref": room.get("id"),
                            "thread_ref": thread["id"],
                            "comment_ref": question["comment_ref"],
                            "reason": "unanswered_buyer_question",
                            "escalated_to": self._data(room).get("owner"),
                            "hours_open": question["hours_open"],
                            "asked_at": question["at"],
                            "at": self._stamp(),
                        },
                        room_id=room.get("id"),
                        actor="system",
                        source=source,
                    )
                    existing.add(key)
                    created.append(self._escalation_view(record))
        return created

    def escalations(
        self, *, room_id: str | None = None, thread_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        rows = self.store.list(v.ESCALATION_COLLECTION, room_id=room_id, limit=min(limit, 1000))
        if thread_id:
            rows = [row for row in rows if self._data(row).get("thread_ref") == thread_id]
        rows.sort(key=lambda rec: str(self._data(rec).get("at") or ""), reverse=True)
        return [self._escalation_view(record) for record in rows]

    # -- summary ------------------------------------------------------------ #

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        """The board's headline numbers. Reads only."""
        threads = self.store.list(v.THREAD_COLLECTION, room_id=room_id, limit=1000)
        comments = self.store.list(v.COMMENT_COLLECTION, room_id=room_id, limit=1000)
        mentions = self.store.list(v.MENTION_COLLECTION, room_id=room_id, limit=1000)
        edits = self.store.list(v.EDIT_COLLECTION, room_id=room_id, limit=1000)
        conversions = self.store.list(v.CONVERSION_COLLECTION, room_id=room_id, limit=1000)
        escalations = self.store.list(v.ESCALATION_COLLECTION, room_id=room_id, limit=1000)
        now = self.now()
        questions = 0
        unanswered = 0
        for thread in threads:
            if self._data(thread).get("state") == v.STATE_RESOLVED:
                continue
            rows = [self._data(row) | {"id": row["id"]} for row in self._comments_of(thread["id"])]
            questions += sum(
                1
                for comment in rows
                if comment.get("kind") == v.KIND_QUESTION
                and comment.get("author_side") == v.SIDE_BUYER
            )
            unanswered += len(r.unanswered_buyer_questions(rows, now=now))
        return {
            "room_id": room_id,
            "threads": len(threads),
            "open_threads": sum(1 for t in threads if self._data(t).get("state") == v.STATE_OPEN),
            "resolved_threads": sum(
                1 for t in threads if self._data(t).get("state") == v.STATE_RESOLVED
            ),
            "comments": len(comments),
            "questions": questions,
            "unanswered_questions": unanswered,
            "mentions": len(mentions),
            "unresolved_mentions": sum(
                1 for row in mentions if self._data(row).get("state") == v.MENTION_UNRESOLVED
            ),
            "champion_edits": sum(1 for row in edits if self._data(row).get("champion")),
            "edits": len(edits),
            "conversions": len(conversions),
            "escalations": len(escalations),
            "contested": v.CONTESTED,
        }

    # -- internals ---------------------------------------------------------- #

    @staticmethod
    def _comment_kind(value: Any) -> str:
        kind = str(value or v.KIND_COMMENT).strip().lower()
        if kind not in v.COMMENT_KINDS:
            raise InRoomValidation(f"kind must be one of {', '.join(v.COMMENT_KINDS)}")
        return kind

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
        delta: dict[str, Any] = {}
        for key in set(before) | set(after):
            old, new = before.get(key), after.get(key)
            if old != new:
                delta[key] = {"from": old, "to": new}
        return delta

    @staticmethod
    def _watchers_with(data: Mapping[str, Any], additions: Iterable[Any]) -> list[str]:
        watchers = [r.normalise_principal(w) for w in data.get("watchers") or []]
        for addition in additions:
            who = r.normalise_principal(addition)
            if who and who not in watchers:
                watchers.append(who)
        return watchers

    @staticmethod
    def _handles(payload: Mapping[str, Any]) -> list[str]:
        raw = payload.get("principals")
        if raw is None and payload.get("handle"):
            raw = [payload.get("handle")]
        if isinstance(raw, str):
            raw = [raw]
        handles: list[str] = []
        for value in raw or []:
            handle = r.normalise_principal(str(value).lstrip("@"))
            if handle and handle not in handles:
                handles.append(handle)
        if not handles:
            raise InRoomValidation("at least one handle is required to tag someone")
        return handles

    def _comments_of(self, thread_id: str) -> list[dict[str, Any]]:
        rows = self.store.find(v.COMMENT_COLLECTION, {"thread_ref": thread_id}, limit=1000)
        rows.sort(key=lambda rec: (str(self._data(rec).get("at") or ""), rec["id"]))
        return rows

    def _mentions_of(self, thread_id: str) -> list[dict[str, Any]]:
        rows = self.store.find(v.MENTION_COLLECTION, {"thread_ref": thread_id}, limit=1000)
        rows.sort(key=lambda rec: (str(self._data(rec).get("at") or ""), rec["id"]))
        return rows

    def _write_comment(
        self,
        writer: Any,
        thread: Mapping[str, Any],
        actor: Mapping[str, Any],
        body: str,
        kind: str,
        *,
        source: str,
        reply_to: Any = None,
    ) -> dict[str, Any]:
        """Write one comment, resolve its mentions, and update its thread.

        ``writer`` is either the store or an open transaction handle; both expose
        ``create`` and ``update``, so a comment inside ``open_thread`` commits with
        the thread it belongs to rather than being refused for opening a second
        transaction.
        """
        data = self._data(thread)
        room_id = thread.get("room_id") or data.get("room_ref")
        room = self.store.get(room_id) or {"id": room_id, "data": {}}
        handles = r.extract_mentions(body)
        stamp = self._stamp()
        comment = writer.create(
            v.COMMENT_COLLECTION,
            {
                "thread_ref": thread["id"],
                "room_ref": room_id,
                "body": body,
                "author": actor["principal"],
                "author_side": actor["side"],
                "author_role": actor["role"],
                "kind": kind,
                "reply_to": reply_to,
                "mentions": handles,
                "at": stamp,
            },
            room_id=room_id,
            actor=actor["principal"],
            source=source,
        )
        mentions = self._write_mentions(writer, room, thread, comment["id"], handles, source=source)
        writer.update(
            thread["id"],
            {
                "watchers": self._watchers_with(
                    data,
                    [actor["principal"], *[m["principal"] for m in mentions if m["principal"]]],
                ),
                "message_count": int(data.get("message_count") or 0) + 1,
                "last_activity_at": stamp,
            },
            actor=actor["principal"],
            source=source,
        )
        return self._comment_view(comment, mentions)

    def _write_mentions(
        self,
        writer: Any,
        room: Mapping[str, Any],
        thread: Mapping[str, Any],
        comment_ref: Any,
        handles: Iterable[str],
        *,
        source: str,
    ) -> list[dict[str, Any]]:
        """Record one mention row per handle and say whether it resolved."""
        views: list[dict[str, Any]] = []
        stamp = self._stamp()
        for handle in handles:
            principal = self.recipients(room, handle)
            record = writer.create(
                v.MENTION_COLLECTION,
                {
                    "thread_ref": thread["id"],
                    "comment_ref": comment_ref,
                    "handle": handle,
                    "principal": principal,
                    "state": v.MENTION_NOTIFIED if principal else v.MENTION_UNRESOLVED,
                    "at": stamp,
                },
                room_id=room.get("id"),
                actor=principal or "system",
                source=source,
            )
            views.append(self._mention_view(record))
        return views

    # -- views -------------------------------------------------------------- #

    def _thread_view(
        self,
        record: Mapping[str, Any],
        *,
        comments: list[dict[str, Any]] | None = None,
        brief: bool = False,
    ) -> dict[str, Any]:
        data = self._data(record)
        thread_id = record["id"]
        comment_views = (
            comments
            if comments is not None
            else [self._comment_view(row) for row in self._comments_of(thread_id)]
        )
        mention_rows = self._mentions_of(thread_id)
        unanswered = r.unanswered_buyer_questions(
            comment_views, now=self.now(), thread_state=data.get("state", v.STATE_OPEN)
        )
        view = {
            "id": thread_id,
            "room_id": record.get("room_id") or data.get("room_ref"),
            "anchor": {
                "type": data.get("anchor_type"),
                "id": data.get("anchor_id"),
                "label": data.get("anchor_label"),
            },
            "state": data.get("state"),
            "created_by": data.get("created_by"),
            "created_side": data.get("created_side"),
            "watchers": list(data.get("watchers") or []),
            "message_count": data.get("message_count"),
            "last_activity_at": data.get("last_activity_at"),
            "map_task_ref": data.get("map_task_ref"),
            "contested": bool(data.get("contested")),
            "unanswered_questions": len(unanswered),
            "mention_count": len(mention_rows),
            "unresolved_mentions": sum(
                1 for row in mention_rows if self._data(row).get("state") == v.MENTION_UNRESOLVED
            ),
            "comments": comment_views,
        }
        if brief:
            if view["comments"]:
                latest = view["comments"][-1]
                view["last_comment"] = {
                    "author": latest.get("author"),
                    "side": latest.get("author_side"),
                    "body": latest.get("body"),
                }
            for comment in view["comments"]:
                comment.pop("mentions", None)
        else:
            view["mentions"] = [self._mention_view(row) for row in mention_rows]
            view["escalations"] = self.escalations(thread_id=thread_id)
        return view

    def _comment_view(
        self, record: Mapping[str, Any], mentions: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        data = self._data(record)
        return {
            "id": record.get("id"),
            "thread_ref": data.get("thread_ref"),
            "body": data.get("body"),
            "author": data.get("author"),
            "author_side": data.get("author_side"),
            "author_role": data.get("author_role"),
            "kind": data.get("kind"),
            "reply_to": data.get("reply_to"),
            "at": data.get("at"),
            "mentions": (
                mentions
                if mentions is not None
                else [
                    self._mention_view(row)
                    for row in self.store.find(
                        v.MENTION_COLLECTION, {"comment_ref": record.get("id")}, limit=1000
                    )
                ]
            ),
        }

    def _mention_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = self._data(record)
        return {
            "id": record.get("id"),
            "thread_ref": data.get("thread_ref"),
            "comment_ref": data.get("comment_ref"),
            "handle": data.get("handle"),
            "principal": data.get("principal"),
            "state": data.get("state"),
            "notified": data.get("state") == v.MENTION_NOTIFIED,
            "at": data.get("at"),
        }

    def _escalation_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = self._data(record)
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id") or data.get("room_ref"),
            "thread_ref": data.get("thread_ref"),
            "comment_ref": data.get("comment_ref"),
            "reason": data.get("reason"),
            "escalated_to": data.get("escalated_to"),
            "hours_open": data.get("hours_open"),
            "asked_at": data.get("asked_at"),
            "at": data.get("at"),
        }

    def _edit_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = self._data(record)
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id") or data.get("room_ref"),
            "section_ref": data.get("section_ref"),
            "anchor_type": data.get("anchor_type"),
            "summary": data.get("summary"),
            "before": data.get("before") or {},
            "after": data.get("after") or {},
            "delta": data.get("delta") or {},
            "actor": data.get("actor"),
            "actor_role": data.get("actor_role"),
            "side": data.get("side"),
            "champion": bool(data.get("champion")),
            "at": data.get("at"),
        }

    def _conversion_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = self._data(record)
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id") or data.get("room_ref"),
            "thread_ref": data.get("thread_ref"),
            "title": data.get("title"),
            "owner": data.get("owner"),
            "owner_side": data.get("owner_side"),
            "due_date": data.get("due_date"),
            "map_task_ref": data.get("map_task_ref"),
            "converted_by": data.get("converted_by"),
            "converted_side": data.get("converted_side"),
            "at": data.get("at"),
        }
