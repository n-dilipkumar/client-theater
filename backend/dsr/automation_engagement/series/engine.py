"""The series flow end to end, over the audited store.

Collections
-----------

Five, all ordinary JSON in ``records.data``. No migration, no typed column, and a
team adding a field needs no coordination with anyone.

``wf105_series``
    The definition: name, state, the starting rule block's filters, the sticky
    exit filters, the goal (outcome filters and a match window), and the path of
    blocks ending in a tag block.
``wf105_tag``
    The workspace tag vocabulary. A tag block selects from here and can never
    create an entry.
``wf105_enrollment``
    One contact's membership of one series. One row per (series, contact), so the
    sticky exit is a property of the row: a completed row can be re-entered by
    updating it, and an exited row can never be.
``wf105_tag_application``
    A tag written onto a contact when the contact reaches the tag block. This is
    the "writes a Tag onto the Contact object" step of the data flow.
``wf105_series_evaluation``
    One row per poll, recording what the poll decided. A skip is recorded rather
    than raised, because a data-driven automation skips far more often than it
    enrols.

The source argument
-------------------

Every writing method takes ``source`` as a required keyword. The route owns the
string, built from ``router.prefix``, so the audit row and the route table cannot
drift: a hardcoded source inside a domain method is a defect, and the same class
of bug has shipped here before, with a feature's audit log naming a path the app
had stopped serving. Making it required means omitting it is a ``TypeError`` at
the call site rather than an untraceable row in production.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from dsr.automation_engagement.series import rules, vocabulary as vocab
from dsr.automation_engagement.series.errors import (
    SeriesConflict,
    SeriesNotFound,
    SeriesRefused,
)
from dsr.store import RecordStore

SeriesRecord = dict[str, Any]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SeriesEngine:
    """The workflow over one :class:`~dsr.store.RecordStore`.

    The store and the clock are the only constructor arguments. Building the
    engine per request rather than holding it on ``app.state`` keeps the clock a
    plain argument, which is what lets a test drive the whole workflow with rows
    of its own and a clock it controls, and leaves ``app.state`` a thing no
    feature has to touch.
    """

    def __init__(self, store: RecordStore, clock: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self.clock = clock or _utcnow

    def _now(self) -> datetime:
        return self.clock()

    # ------------------------------------------------------------------ #
    # Tags: the shared workspace vocabulary
    # ------------------------------------------------------------------ #

    def create_tag(
        self, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> SeriesRecord:
        """Register a workspace tag. Tags are shared, not per-series."""

        data = rules.validate_tag(payload)
        for existing in self.tags(limit=1000):
            if str(existing["data"].get("name", "")).lower() == data["name"].lower():
                raise SeriesConflict(
                    f"a workspace tag named {data['name']!r} already exists",
                    code="tag_name_already_used",
                )
        return self.store.create(vocab.TAG_COLLECTION, data, actor=actor, source=source)

    def tags(self, *, limit: int = 100) -> list[SeriesRecord]:
        return self.store.list(
            vocab.TAG_COLLECTION, limit=limit, order_by="created_at", descending=False
        )

    def require_tag(self, name: str) -> SeriesRecord:
        for record in self.tags(limit=1000):
            if str(record["data"].get("name", "")).lower() == str(name).lower():
                return record
        raise SeriesNotFound(f"tag {name!r} is not in the workspace", kind="tag")

    # ------------------------------------------------------------------ #
    # Series
    # ------------------------------------------------------------------ #

    def create_series(
        self, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> SeriesRecord:
        """Declare a series. Validated in full before the row exists."""

        data = rules.validate_series(payload, self.tags(limit=1000))
        return self.store.create(vocab.SERIES_COLLECTION, data, actor=actor, source=source)

    def series(
        self,
        *,
        room_id: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> list[SeriesRecord]:
        records = self.store.list(
            vocab.SERIES_COLLECTION,
            room_id=room_id,
            limit=limit,
            order_by="created_at",
            descending=False,
        )
        if state:
            records = [r for r in records if str(r["data"].get("state")) == state]
        return records

    def require_series(self, series_id: str) -> SeriesRecord:
        record = self.store.get(series_id)
        if record is None or record["collection"] != vocab.SERIES_COLLECTION:
            raise SeriesNotFound(f"series {series_id!r} does not exist", kind="series")
        return record

    def publish_series(self, series_id: str, *, actor: str | None, source: str) -> SeriesRecord:
        """Move a series to live, re-checking its tag blocks against the workspace.

        The tag block's tag was validated when the series was declared, but the
        workspace can change between declare and publish, so the tag is resolved
        again here. A tag that has since left the workspace is refused by name
        rather than published into a block that cannot apply it.
        """

        record = self.require_series(series_id)
        data = record["data"]
        if str(data.get("state")) == vocab.STATE_LIVE:
            raise SeriesConflict("the series is already live", code="series_already_live")
        workspace = self.tags(limit=1000)
        for block in data.get("path", []):
            if block.get("kind") == vocab.BLOCK_TAG:
                rules.select_tag(block, workspace)
        updated = self.store.update(
            series_id,
            {"state": vocab.STATE_LIVE, "published_at": rules.to_iso(self._now())},
            actor=actor,
            source=source,
        )
        return updated

    # ------------------------------------------------------------------ #
    # Enrollment
    # ------------------------------------------------------------------ #

    def _enrollment(self, series_id: str, contact_id: str) -> SeriesRecord | None:
        rows = self.store.find(
            vocab.ENROLLMENT_COLLECTION,
            {"series_id": series_id, "contact_id": contact_id},
            limit=1,
        )
        return rows[0] if rows else None

    def _apply_entry(
        self,
        series: SeriesRecord,
        contact: Mapping[str, Any],
        now: datetime,
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Run the starting rule for one contact and write what it decides."""

        contact_id = self._require_contact_id(contact)
        prior = self._enrollment(str(series["id"]), contact_id)
        prior_data = prior["data"] if prior else None
        decision = rules.entry_decision(series["data"], contact, prior_data)
        enrollment = prior

        if decision["outcome"] in vocab.ENROLLING_OUTCOMES:
            if prior is None:
                enrollment = self.store.create(
                    vocab.ENROLLMENT_COLLECTION,
                    {
                        "series_id": series["id"],
                        "contact_id": contact_id,
                        "state": vocab.ENROLLED,
                        "position": 0,
                        "reentry_count": 0,
                        "entered_at": rules.to_iso(now),
                        "contact_snapshot": dict(contact),
                    },
                    actor=actor,
                    source=source,
                )
            else:
                reentries = int(prior["data"].get("reentry_count") or 0) + 1
                enrollment = self.store.update(
                    str(prior["id"]),
                    {
                        "state": vocab.ENROLLED,
                        "position": 0,
                        "reentry_count": reentries,
                        "entered_at": rules.to_iso(now),
                        "completed_at": None,
                        "contact_snapshot": dict(contact),
                    },
                    actor=actor,
                    source=source,
                )
        return {
            "contact_id": contact_id,
            "outcome": decision["outcome"],
            "reason": decision["reason"],
            "enrollment": enrollment,
        }

    @staticmethod
    def _require_contact_id(contact: Mapping[str, Any]) -> str:
        contact_id = str((contact or {}).get("contact_id") or "").strip()
        if not contact_id:
            raise SeriesRefused(
                "a contact needs a contact_id: every enrollment lands on one contact",
                {"contact_id": "required"},
            )
        return contact_id

    def evaluate_entry(
        self,
        series_id: str,
        contact: Mapping[str, Any],
        *,
        event_type: str | None = None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Evaluate one contact against one series and record the decision.

        This is the automation's unit of work. It writes an evaluation row even
        when the outcome is a skip, so a polled series leaves a trail of what it
        considered rather than only what it changed.
        """

        series = self.require_series(series_id)
        now = self._now()
        result = self._apply_entry(series, contact, now, actor=actor, source=source)
        evaluation = self.store.create(
            vocab.EVALUATION_COLLECTION,
            {
                "series_id": series_id,
                "at": rules.to_iso(now),
                "event_type": event_type,
                "event_count": 1,
                "outcomes": {result["outcome"]: 1},
                "enrolled": 1 if result["outcome"] == vocab.OUTCOME_ENROLLED else 0,
                "reentered": 1 if result["outcome"] == vocab.OUTCOME_REENTERED else 0,
            },
            actor=actor,
            source=source,
        )
        return {
            "series_id": series_id,
            "contact_id": result["contact_id"],
            "outcome": result["outcome"],
            "reason": result["reason"],
            "enrollment": result["enrollment"],
            "evaluation_id": evaluation["id"],
        }

    def advance(
        self,
        series_id: str,
        contact_id: str,
        *,
        contact: Mapping[str, Any] | None = None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Move an active enrollment one block along the path.

        When the block is a tag block the tag is written onto the contact, which
        is the data flow's "tag block writes a Tag onto the Contact object". When
        the move passes the last block the enrollment completes and the Goal tab
        is evaluated against the contact.
        """

        series = self.require_series(series_id)
        record = self._enrollment(series_id, contact_id)
        if record is None:
            raise SeriesNotFound(
                f"contact {contact_id!r} is not enrolled in series {series_id!r}",
                kind="enrollment",
            )
        data = record["data"]
        if str(data.get("state")) != vocab.ENROLLED:
            raise SeriesConflict(
                f"the enrollment is {data.get('state')}, so it cannot advance",
                code="enrollment_not_active",
            )

        path = series["data"].get("path", [])
        position = int(data.get("position") or 0)
        if position >= len(path):
            raise SeriesConflict(
                "the enrollment has already walked the path", code="path_exhausted"
            )

        now = self._now()
        block = path[position]
        tag_application = None
        if block.get("kind") == vocab.BLOCK_TAG:
            tag_application = self._write_tag_application(
                series, contact_id, block, now, actor=actor, source=source
            )

        new_position = position + 1
        completed = new_position >= len(path)
        patch: dict[str, Any] = {"position": new_position}
        goal: dict[str, Any] = {"met": False, "reason": "the path is not complete"}
        if completed:
            current = dict(data.get("contact_snapshot") or {})
            if contact is not None:
                current = dict(contact)
            goal = rules.goal_met(series["data"], current, data, now)
            patch.update(
                {
                    "state": vocab.COMPLETED,
                    "completed_at": rules.to_iso(now),
                    "goal_met": bool(goal["met"]),
                    "goal_reason": goal["reason"],
                }
            )
        updated = self.store.update(str(record["id"]), patch, actor=actor, source=source)
        return {
            "outcome": "completed" if completed else "advanced",
            "series_id": series_id,
            "contact_id": contact_id,
            "block": block,
            "tag_application": tag_application,
            "goal": goal,
            "enrollment": updated,
        }

    def _write_tag_application(
        self,
        series: SeriesRecord,
        contact_id: str,
        block: Mapping[str, Any],
        now: datetime,
        *,
        actor: str | None,
        source: str,
    ) -> SeriesRecord:
        """Write the tag onto the contact. This does not create a tag."""

        return self.store.create(
            vocab.TAG_APPLICATION_COLLECTION,
            {
                "series_id": series["id"],
                "contact_id": contact_id,
                "tag": str(block.get("tag")),
                "block_title": block.get("title"),
                "applied_at": rules.to_iso(now),
                "topic": vocab.TAG_CREATED_TOPIC,
            },
            actor=actor,
            source=source,
        )

    def record_exit(
        self,
        series_id: str,
        contact_id: str,
        contact: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Record a sticky exit. The contact can never re-enter afterwards."""

        series = self.require_series(series_id)
        record = self._enrollment(series_id, contact_id)
        if record is None:
            raise SeriesNotFound(
                f"contact {contact_id!r} is not enrolled in series {series_id!r}",
                kind="enrollment",
            )
        if str(record["data"].get("state")) == vocab.EXITED:
            raise SeriesConflict("the contact has already exited", code="already_exited")
        reason = rules.suggest_exit_reason(series["data"], contact)
        now = self._now()
        updated = self.store.update(
            str(record["id"]),
            {
                "state": vocab.EXITED,
                "exit_reason": reason,
                "exited_at": rules.to_iso(now),
            },
            actor=actor,
            source=source,
        )
        return {
            "series_id": series_id,
            "contact_id": contact_id,
            "outcome": "exited",
            "exit_reason": reason,
            "enrollment": updated,
        }

    # ------------------------------------------------------------------ #
    # The poll: entry and progression, on demand
    # ------------------------------------------------------------------ #

    def poll_series(
        self,
        series_id: str,
        events: Sequence[Mapping[str, Any]],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Evaluate entry, and progression when an event asks for it, in one poll.

        The research polls a series every 30 minutes "to evaluate entry and
        progression". This build exposes that poll as a call rather than a
        background thread - see
        :data:`~dsr.automation_engagement.series.inferences.DERIVED_POLL_IS_ON_DEMAND`
        - and writes one evaluation row for the whole poll. ``events`` are the
        recurring domain events the data flow names, each carrying a contact.
        """

        series = self.require_series(series_id)
        if str(series["data"].get("state")) != vocab.STATE_LIVE:
            raise SeriesConflict(
                "only a live series is polled",
                code="series_not_live",
            )
        now = self._now()
        enrolled = 0
        reentered = 0
        outcomes: dict[str, int] = {}
        advanced = 0
        for event in events:
            contact = event.get("contact") if isinstance(event, Mapping) else None
            if not isinstance(contact, Mapping):
                raise SeriesRefused(
                    "every poll event needs a contact",
                    {"events": "an event is missing its contact"},
                )
            result = self._apply_entry(series, contact, now, actor=actor, source=source)
            outcomes[result["outcome"]] = outcomes.get(result["outcome"], 0) + 1
            if result["outcome"] == vocab.OUTCOME_ENROLLED:
                enrolled += 1
            if result["outcome"] == vocab.OUTCOME_REENTERED:
                reentered += 1
            if event.get("advance") and result["outcome"] in vocab.ENROLLING_OUTCOMES:
                self.advance(
                    series_id,
                    result["contact_id"],
                    contact=contact,
                    actor=actor,
                    source=source,
                )
                advanced += 1

        evaluation = self.store.create(
            vocab.EVALUATION_COLLECTION,
            {
                "series_id": series_id,
                "at": rules.to_iso(now),
                "event_count": len(events),
                "outcomes": outcomes,
                "enrolled": enrolled,
                "reentered": reentered,
                "advanced": advanced,
            },
            actor=actor,
            source=source,
        )
        self.store.update(
            series_id,
            {"last_polled_at": rules.to_iso(now)},
            actor=actor,
            source=source,
        )
        return {
            "series_id": series_id,
            "at": rules.to_iso(now),
            "event_count": len(events),
            "outcomes": outcomes,
            "enrolled": enrolled,
            "reentered": reentered,
            "advanced": advanced,
            "evaluation_id": evaluation["id"],
        }

    def series_due(self, series_id: str) -> dict[str, Any]:
        """Whether a series is due for its next poll, and when that is.

        The researched cadence is a property of the domain rather than of a timer.
        A scheduler reads this route, calls the poll when ``due`` is true, and the
        poll records ``last_polled_at``, so the 30-minute interval is enforced
        without a thread inside the process. This is the half of the researched
        "polled every 30 minutes" that makes an on-demand poll honest: the cadence
        is data a caller can act on, not a comment beside a route.
        """

        record = self.require_series(series_id)
        now = self._now()
        data = record["data"]
        last = data.get("last_polled_at")
        interval = int(data.get("poll_interval_minutes") or vocab.POLL_INTERVAL_MINUTES)
        last_dt = rules.parse_instant(last)
        next_due = rules.to_iso(last_dt + timedelta(minutes=interval)) if last_dt else None
        return {
            "series_id": series_id,
            "state": data.get("state"),
            "due": rules.due_for_poll(data, last, now),
            "last_polled_at": last,
            "next_due_at": next_due,
            "poll_interval_minutes": interval,
            "at": rules.to_iso(now),
        }

    def due_series(self, *, room_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Every live series whose next poll is due, oldest first.

        This is what a platform scheduler calls: one read that names the work to
        do, so no timer has to live inside a feature.
        """

        due: list[dict[str, Any]] = []
        for record in self.series(room_id=room_id, state=vocab.STATE_LIVE, limit=limit):
            view = self.series_due(str(record["id"]))
            if view["due"]:
                due.append(view)
        return due

    # ------------------------------------------------------------------ #
    # Reads for the page
    # ------------------------------------------------------------------ #

    def enrollments(
        self,
        *,
        series_id: str | None = None,
        contact_id: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> list[SeriesRecord]:
        where: dict[str, Any] = {}
        if series_id:
            where["series_id"] = series_id
        if contact_id:
            where["contact_id"] = contact_id
        if state:
            where["state"] = state
        return self.store.find(vocab.ENROLLMENT_COLLECTION, where, limit=limit)

    def tag_applications(
        self,
        *,
        series_id: str | None = None,
        contact_id: str | None = None,
        tag: str | None = None,
        limit: int = 100,
    ) -> list[SeriesRecord]:
        where: dict[str, Any] = {}
        if series_id:
            where["series_id"] = series_id
        if contact_id:
            where["contact_id"] = contact_id
        if tag:
            where["tag"] = tag
        return self.store.find(vocab.TAG_APPLICATION_COLLECTION, where, limit=limit)

    def evaluations(self, *, series_id: str | None = None, limit: int = 100) -> list[SeriesRecord]:
        where: dict[str, Any] = {"series_id": series_id} if series_id else {}
        return self.store.find(vocab.EVALUATION_COLLECTION, where, limit=limit)

    def series_view(self, record: SeriesRecord) -> dict[str, Any]:
        """One series with the tags its tag blocks name, resolved for the page."""

        data = record["data"]
        path = data.get("path", [])
        return {
            **record,
            "tag_blocks": [block for block in path if block.get("kind") == vocab.BLOCK_TAG],
            "block_count": len(path),
            "start_filter_count": len(data.get("start_filters", [])),
            "exit_filter_count": len(data.get("exit_filters", [])),
        }

    def catalog(self, *, room_id: str | None = None, limit: int = 100) -> dict[str, Any]:
        """Everything a reviewer needs to understand one poll's decisions."""

        return {
            "series": [
                self.series_view(record) for record in self.series(room_id=room_id, limit=limit)
            ],
            "tags": self.tags(limit=limit),
            "enrollments": self.enrollments(limit=limit),
            "tag_applications": self.tag_applications(limit=limit),
        }

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Counts for the page header, over the same rows the lists return."""

        series = self.series(room_id=room_id, limit=1000)
        enrollments = self.enrollments(limit=1000)
        applications = self.tag_applications(limit=1000)
        by_state: dict[str, int] = {}
        for record in enrollments:
            key = str(record["data"].get("state"))
            by_state[key] = by_state.get(key, 0) + 1
        return {
            "series": len(series),
            "live": len([r for r in series if r["data"].get("state") == vocab.STATE_LIVE]),
            "tags": len(self.tags(limit=1000)),
            "enrollments": len(enrollments),
            "active": by_state.get(vocab.ENROLLED, 0),
            "completed": by_state.get(vocab.COMPLETED, 0),
            "exited": by_state.get(vocab.EXITED, 0),
            "tag_applications": len(applications),
            "evaluations": len(self.evaluations(limit=1000)),
            "poll_interval_minutes": vocab.POLL_INTERVAL_MINUTES,
        }
