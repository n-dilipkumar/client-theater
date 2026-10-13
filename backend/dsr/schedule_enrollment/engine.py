"""The enrollment engine: the only module in this package that reads or writes.

Every write goes through :class:`~dsr.store.RecordStore` and its
:class:`~dsr.db.audited.AuditedDatabase`. An enrollment is a sequence of audited
writes - the enrollment row, the marker on the CRM record, a workflow-log line,
then the task (written through WF-116's engine, which opens its own transaction)
and the internal notification - because the audited wrapper's transaction cannot
nest the task engine's own. A step that fails is recorded on the run as failed
rather than reported as committed, and the enrollment stays queued so the next
run retries it.

The clock is injected. Production passes the wall clock; a test passes a frozen
function, so no test depends on today's date.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from dsr.schedule_enrollment import rules, vocabulary as v
from dsr.schedule_enrollment.errors import EnrollConflict, EnrollNotFound, EnrollValidationError
from dsr.seller_tasks.engine import SellerTaskEngine
from dsr.store import RecordStore

Clock = Callable[[], datetime]


def _wall_clock() -> datetime:
    return datetime.now(timezone.utc)


#: The words a page shows for each reason. A colour is never the only signal, so
#: every reason carries a label.
REASON_LABELS: dict[str, str] = {
    v.REASON_MATCHED: "Enrolled",
    v.REASON_RE_ENROLLED: "Re-enrolled",
    v.REASON_NO_FILTER_MATCH: "Did not meet the filter",
    v.REASON_ALREADY_ENROLLED: "Already enrolled",
    v.REASON_SINGLE_ENROLLMENT: "Already enrolled once",
    v.REASON_DEFERRED: "Deferred at activation",
    v.REASON_UNENROLLED: "Unenrolled",
}

#: The zero counters every run starts from.
_EMPTY_COUNTS: dict[str, int] = {
    "scanned": 0,
    "matched": 0,
    "enrolled": 0,
    "re_enrolled": 0,
    "released": 0,
    "unenrolled": 0,
    "skipped": 0,
    "failed": 0,
    "deferred": 0,
}


class ScheduleEnrollmentEngine:
    """Schedules, the records they scan, and the enrollments they leave behind."""

    def __init__(
        self,
        store: RecordStore,
        *,
        now: Clock | None = None,
        task_engine: SellerTaskEngine | None = None,
    ) -> None:
        self.store = store
        self._now = now or _wall_clock
        # The task the first action creates is WF-116's, written through that
        # workflow's engine so this package does not re-declare a task rule.
        self.tasks = task_engine or SellerTaskEngine(store, now=self._now)

    # -- clock -------------------------------------------------------------- #

    def now_ms(self) -> int:
        return rules.parse_timestamp(self._now(), "now") or 0

    # -- vocabulary --------------------------------------------------------- #

    @staticmethod
    def vocabulary() -> dict[str, Any]:
        payload = v.published_vocabulary()
        payload["reason_labels"] = dict(REASON_LABELS)
        return payload

    # -- schedules: reads --------------------------------------------------- #

    def require_schedule(self, schedule_id: str) -> dict[str, Any]:
        record = self.store.get(schedule_id)
        if record is None or record["collection"] != v.SCHEDULE_COLLECTION:
            raise EnrollNotFound(schedule_id, collection="schedule")
        return record

    def get_schedule(self, schedule_id: str) -> dict[str, Any]:
        return self._schedule_view(self.require_schedule(schedule_id))

    def list_schedules(
        self, *, room_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        records = self.store.list(v.SCHEDULE_COLLECTION, room_id=room_id, limit=limit)
        return [self._schedule_view(record) for record in records]

    # -- schedules: writes -------------------------------------------------- #

    def _apply(
        self,
        record: Mapping[str, Any],
        patch: Mapping[str, Any],
        *,
        source: str,
        actor: str | None,
        now_ms: int,
    ) -> dict[str, Any]:
        """Normalise a merged payload and write it, carrying engine-managed keys."""

        merged: dict[str, Any] = {**record["data"], **dict(patch or {})}
        if patch and patch.get("room_id") is not None:
            merged["room_id"] = patch.get("room_id")
        elif "room_id" not in merged:
            merged["room_id"] = record.get("room_id")
        fields = rules.normalise_schedule_payload(merged)
        fields.pop("room_id")
        fields["created_at_ms"] = record["data"].get("created_at_ms")
        fields["updated_at_ms"] = now_ms
        for key in ("published_at_ms", "last_run_at_ms", "last_run_id"):
            fields[key] = merged.get(key)
        return self.store.update(record["id"], fields, actor=actor, source=source)

    def create_schedule(
        self,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        fields = rules.normalise_schedule_payload(payload)
        room_id = fields.pop("room_id")
        now_ms = self.now_ms()
        data = {
            **fields,
            "created_at_ms": now_ms,
            "updated_at_ms": now_ms,
            "published_at_ms": now_ms if fields["published"] else None,
            "last_run_at_ms": None,
            "last_run_id": None,
        }
        record = self.store.create(
            v.SCHEDULE_COLLECTION, data, room_id=room_id, actor=actor, source=source
        )
        return self._schedule_view(record)

    def update_schedule(
        self,
        schedule_id: str,
        patch: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        record = self.require_schedule(schedule_id)
        updated = self._apply(record, patch, source=source, actor=actor, now_ms=self.now_ms())
        return self._schedule_view(updated)

    def set_published(
        self,
        schedule_id: str,
        published: bool,
        *,
        backfill: str | None = None,
        source: str,
        actor: str | None = None,
        at_ms: int | None = None,
    ) -> dict[str, Any]:
        """Turn the schedule on or off, and take the publish panel's backfill choice.

        Turning it on is the researched Review-and-publish step. A publish with
        ``existing_and_future`` runs the backfill immediately and records that run
        as ``backfill``. A publish with ``future_only`` enrols nobody and records
        an ``activation`` run whose deferred count is the records that matched at
        publish time; those records enrol on the next scheduled run.
        """

        record = self.require_schedule(schedule_id)
        moment = at_ms if at_ms is not None else self.now_ms()

        if not published:
            updated = self._apply(
                record, {"published": False}, source=source, actor=actor, now_ms=moment
            )
            return {"schedule": self._schedule_view(updated), "run": None, "backfill": None}

        errors: dict[str, str] = {}
        choice = rules.coerce_choice(
            backfill if backfill not in (None, "") else record["data"].get("backfill"),
            v.BACKFILL_CHOICES,
            "backfill",
            errors,
            default=v.DEFAULT_BACKFILL,
        )
        if errors:
            raise EnrollValidationError(errors)

        updated = self._apply(
            record,
            {"published": True, "backfill": choice, "published_at_ms": moment},
            source=source,
            actor=actor,
            now_ms=moment,
        )
        schedule = self._schedule_view(updated)
        run = self._publish_run(
            schedule, choice or v.DEFAULT_BACKFILL, moment, source=source, actor=actor
        )
        return {"schedule": schedule, "run": run, "backfill": choice}

    def delete_schedule(
        self, schedule_id: str, *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        self.require_schedule(schedule_id)
        self.store.delete(schedule_id, actor=actor, source=source)
        return {"schedule_id": schedule_id, "deleted": True}

    # -- the records the schedule scans ------------------------------------- #

    def records(
        self,
        *,
        room_id: str | None = None,
        object: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Every CRM record row this build can see, normalised.

        Read from WF-039's collection by name. An ``object`` filter matches
        case-insensitively, so a schedule that names one object family leaves the
        rest of the collection alone.
        """

        found = self.store.list(v.CRM_RECORD_COLLECTION, room_id=room_id, limit=limit)
        if object:
            target = str(object).strip().lower()
            found = [
                record
                for record in found
                if str((record.get("data") or {}).get("object") or "").strip().lower() == target
            ]
        return [rules.record_view(record) for record in found]

    # -- evaluation and preview --------------------------------------------- #

    def _decision(
        self, schedule: Mapping[str, Any], view: Mapping[str, Any], moment: int
    ) -> dict[str, Any]:
        prior = self._current_enrollment(schedule["id"], view["id"])
        prior_data = prior["data"] if prior else None
        decision: dict[str, Any] = {
            "record_id": view["id"],
            "object": view.get("object"),
            "name": view.get("name"),
            "matched": False,
            "reason": v.REASON_NO_FILTER_MATCH,
            "re_enrollment": False,
            "prior_status": (prior_data or {}).get("status"),
        }
        if not rules.record_matches(view.get("data") or {}, schedule.get("filters") or []):
            return decision
        matched, reason, re_enrollment = rules.eligibility(
            prior_data, schedule.get("re_enroll", v.DEFAULT_REENROLL_MODE)
        )
        decision["matched"] = matched
        decision["reason"] = reason
        decision["re_enrollment"] = re_enrollment
        return decision

    def evaluate(self, schedule_id: str, *, at_ms: int | None = None) -> dict[str, Any]:
        """What the schedule would do right now, writing nothing."""

        schedule = self.get_schedule(schedule_id)
        moment = at_ms if at_ms is not None else self.now_ms()
        views = self.records(room_id=schedule.get("room_id"), object=schedule.get("object"))
        decisions = [self._decision(schedule, view, moment) for view in views]
        matched = [d for d in decisions if d["matched"]]
        queued = [
            enrollment
            for enrollment in self.enrollments(schedule_id=schedule_id, limit=1000)
            if enrollment["status"] == v.STATUS_QUEUED
        ]
        due = [enrollment for enrollment in queued if rules.is_released(enrollment, moment)]
        return {
            "schedule_id": schedule_id,
            "at_ms": moment,
            "at": rules.to_iso(moment),
            "object": schedule.get("object"),
            "scanned": len(views),
            "matched": len(matched),
            "re_enrollments": len([d for d in matched if d["re_enrollment"]]),
            "decisions": decisions,
            "queued": len(queued),
            "queued_due": len(due),
        }

    # -- the run ------------------------------------------------------------ #

    def run(
        self,
        schedule_id: str,
        *,
        source: str,
        actor: str | None = None,
        at_ms: int | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        """Run one scheduled scan: unenroll, enrol, then release the queue."""

        schedule = self.get_schedule(schedule_id)
        if not schedule.get("published"):
            raise EnrollConflict(
                "schedule_not_published",
                "publish the schedule before running it",
                extra={"schedule_id": schedule_id},
            )
        moment = at_ms if at_ms is not None else self.now_ms()
        run_kind = kind or v.RUN_SCHEDULE
        run_record = self.store.create(
            v.RUN_COLLECTION,
            {
                "schedule_id": schedule_id,
                "room_id": schedule.get("room_id"),
                "kind": run_kind,
                "object": schedule.get("object"),
                "re_enroll": schedule.get("re_enroll"),
                "backfill": schedule.get("backfill"),
                "at_ms": moment,
                "at": rules.to_iso(moment),
                **_EMPTY_COUNTS,
                "by_reason": {},
                "results": [],
            },
            room_id=schedule.get("room_id"),
            actor=actor,
            source=source,
        )
        run_id = run_record["id"]
        counts = dict(_EMPTY_COUNTS)
        results: list[dict[str, Any]] = []

        # 1. Unenrollment first, so a record the criteria removed is free to
        #    match again in this same run.
        for enrollment in self._active_enrollments(schedule_id):
            view = self._view_for_record(enrollment["record_id"], enrollment.get("object"))
            criteria = schedule.get("unenroll_filter") or []
            if not criteria:
                continue
            if rules.record_matches(view.get("data") or {}, criteria):
                try:
                    self._unenroll(schedule, enrollment, view, moment, run_id, source, actor)
                except Exception as exc:  # one bad row must not abort the whole run
                    counts["failed"] += 1
                    results.append(self._failure(enrollment["record_id"], view, exc))
                    continue
                counts["unenrolled"] += 1
                results.append(
                    {
                        "record_id": enrollment["record_id"],
                        "object": enrollment.get("object"),
                        "name": view.get("name"),
                        "reason": v.REASON_UNENROLLED,
                        "outcome": "unenrolled",
                        "enrollment_id": enrollment["id"],
                    }
                )

        # 2. Scan the object the schedule names.
        views = self.records(room_id=schedule.get("room_id"), object=schedule.get("object"))
        counts["scanned"] = len(views)
        for view in views:
            decision = self._decision(schedule, view, moment)
            if not decision["matched"]:
                counts["skipped"] += 1
                results.append({**decision, "outcome": "skipped"})
                continue
            counts["matched"] += 1
            try:
                enrollment = self._enroll(schedule, view, decision, moment, run_id, source, actor)
            except Exception as exc:
                counts["failed"] += 1
                results.append(self._failure(view["id"], view, exc, decision))
                continue
            outcome = "re_enrolled" if decision["re_enrollment"] else "enrolled"
            counts[outcome] += 1
            results.append({**decision, "outcome": outcome, "enrollment_id": enrollment["id"]})

        # 3. Release the queue. A delay of zero was released by step 2's own
        #    enrollment, so it fires here rather than waiting a whole interval.
        for enrollment in self.enrollments(schedule_id=schedule_id, limit=1000):
            if enrollment["status"] != v.STATUS_QUEUED:
                continue
            if not rules.is_released(enrollment, moment):
                continue
            view = self._view_for_record(enrollment["record_id"], enrollment.get("object"))
            try:
                released = self._release(schedule, enrollment, view, moment, run_id, source, actor)
            except Exception as exc:
                counts["failed"] += 1
                results.append(self._failure(enrollment["record_id"], view, exc))
                continue
            counts["released"] += 1
            results.append(
                {
                    "record_id": enrollment["record_id"],
                    "object": enrollment.get("object"),
                    "name": view.get("name"),
                    "reason": enrollment.get("reason"),
                    "outcome": "released",
                    "enrollment_id": released["id"],
                    "task_id": released.get("task_id"),
                }
            )

        return self._finish_run(schedule, run_id, run_kind, counts, results, moment, source, actor)

    def _publish_run(
        self,
        schedule: Mapping[str, Any],
        choice: str,
        moment: int,
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """The run a publish produces: a backfill, or a record of the deferral."""

        if choice == v.BACKFILL_EXISTING:
            return self.run(
                schedule["id"], source=source, actor=actor, at_ms=moment, kind=v.RUN_BACKFILL
            )

        # Deferral is not exclusion: enrol nobody, record how many matched, and
        # let the next scheduled run enrol them.
        views = self.records(room_id=schedule.get("room_id"), object=schedule.get("object"))
        deferred = len(
            [view for view in views if self._decision(schedule, view, moment)["matched"]]
        )
        counts = dict(_EMPTY_COUNTS)
        counts["scanned"] = len(views)
        counts["deferred"] = deferred
        run_record = self.store.create(
            v.RUN_COLLECTION,
            {
                "schedule_id": schedule["id"],
                "room_id": schedule.get("room_id"),
                "kind": v.RUN_ACTIVATION,
                "object": schedule.get("object"),
                "re_enroll": schedule.get("re_enroll"),
                "backfill": choice,
                "at_ms": moment,
                "at": rules.to_iso(moment),
                **counts,
                "by_reason": {v.REASON_DEFERRED: deferred} if deferred else {},
                "results": [],
            },
            room_id=schedule.get("room_id"),
            actor=actor,
            source=source,
        )
        self._event(
            schedule,
            {
                "kind": v.EVENT_DEFERRED,
                "reason": v.REASON_DEFERRED,
                "run_id": run_record["id"],
                "detail": f"{deferred} record(s) matched at activation and were deferred",
            },
            moment,
            source=source,
            actor=actor,
        )
        return self._run_view(run_record)

    # -- the writes of one enrollment --------------------------------------- #

    def _enroll(
        self,
        schedule: Mapping[str, Any],
        view: Mapping[str, Any],
        decision: Mapping[str, Any],
        moment: int,
        run_id: str,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        delay = int(schedule.get("delay_minutes") or 0)
        payload = rules.enrollment_payload(
            schedule,
            view,
            moment,
            run_id=run_id,
            reason=decision["reason"],
            re_enrollment=bool(decision["re_enrollment"]),
            delay_minutes=delay,
        )
        record = self.store.create(
            v.ENROLLMENT_COLLECTION,
            payload,
            room_id=schedule.get("room_id"),
            actor=actor,
            source=source,
        )
        self.store.update(
            view["id"],
            rules.marker_patch(schedule, v.STATUS_QUEUED, moment),
            actor=actor,
            source=source,
        )
        self._event(
            schedule,
            {
                "kind": v.EVENT_RE_ENROLLED if decision["re_enrollment"] else v.EVENT_ENROLLED,
                "reason": decision["reason"],
                "record_id": view["id"],
                "enrollment_id": record["id"],
                "run_id": run_id,
            },
            moment,
            source=source,
            actor=actor,
        )
        return self._enrollment_view(record)

    def _release(
        self,
        schedule: Mapping[str, Any],
        enrollment: Mapping[str, Any],
        view: Mapping[str, Any],
        moment: int,
        run_id: str,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Fire the actions in order, then complete the enrollment and mark it."""

        task = self.tasks.create(
            rules.task_payload(schedule, view, moment), source=source, actor=actor
        )
        notification = self.store.create(
            v.NOTIFICATION_COLLECTION,
            {
                "schedule_id": schedule["id"],
                "record_id": enrollment["record_id"],
                "enrollment_id": enrollment["id"],
                "message": rules.notification_message(schedule, view),
                "task_id": task["id"],
                "assignee": task.get("assignee"),
                "at_ms": moment,
                "at": rules.to_iso(moment),
            },
            room_id=schedule.get("room_id"),
            actor=actor,
            source=source,
        )
        updated = self.store.update(
            enrollment["id"],
            {
                "status": v.STATUS_COMPLETED,
                "completed_at_ms": moment,
                "completed_at": rules.to_iso(moment),
                "task_id": task["id"],
                "notification_id": notification["id"],
            },
            actor=actor,
            source=source,
        )
        self.store.update(
            enrollment["record_id"],
            rules.marker_patch(schedule, v.STATUS_COMPLETED, moment),
            actor=actor,
            source=source,
        )
        self._event(
            schedule,
            {
                "kind": v.EVENT_RELEASED,
                "reason": enrollment.get("reason"),
                "record_id": enrollment["record_id"],
                "enrollment_id": enrollment["id"],
                "run_id": run_id,
                "task_id": task["id"],
            },
            moment,
            source=source,
            actor=actor,
        )
        self._event(
            schedule,
            {
                "kind": v.EVENT_COMPLETED,
                "reason": enrollment.get("reason"),
                "record_id": enrollment["record_id"],
                "enrollment_id": enrollment["id"],
                "run_id": run_id,
            },
            moment,
            source=source,
            actor=actor,
        )
        return self._enrollment_view(updated)

    def _unenroll(
        self,
        schedule: Mapping[str, Any],
        enrollment: Mapping[str, Any],
        view: Mapping[str, Any],
        moment: int,
        run_id: str,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        updated = self.store.update(
            enrollment["id"],
            {
                "status": v.STATUS_UNENROLLED,
                "reason": v.REASON_UNENROLLED,
                "unenrolled_at_ms": moment,
                "unenrolled_at": rules.to_iso(moment),
            },
            actor=actor,
            source=source,
        )
        self.store.update(
            enrollment["record_id"],
            rules.marker_patch(schedule, v.STATUS_UNENROLLED, moment),
            actor=actor,
            source=source,
        )
        self._event(
            schedule,
            {
                "kind": v.EVENT_UNENROLLED,
                "reason": v.REASON_UNENROLLED,
                "record_id": enrollment["record_id"],
                "enrollment_id": enrollment["id"],
                "run_id": run_id,
            },
            moment,
            source=source,
            actor=actor,
        )
        return self._enrollment_view(updated)

    # -- run completion and the reads that follow --------------------------- #

    def _finish_run(
        self,
        schedule: Mapping[str, Any],
        run_id: str,
        kind: str,
        counts: Mapping[str, int],
        results: list[dict[str, Any]],
        moment: int,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        by_reason: dict[str, int] = {}
        for result in results:
            reason = str(result.get("reason"))
            by_reason[reason] = by_reason.get(reason, 0) + 1
        updated = self.store.update(
            run_id,
            {**dict(counts), "by_reason": by_reason, "results": results},
            actor=actor,
            source=source,
        )
        self._event(
            schedule,
            {"kind": v.EVENT_RUN, "run_id": run_id, "detail": kind},
            moment,
            source=source,
            actor=actor,
        )
        record = self.require_schedule(schedule["id"])
        self._apply(
            record,
            {"last_run_at_ms": moment, "last_run_id": run_id},
            source=source,
            actor=actor,
            now_ms=moment,
        )
        return self._run_view(updated)

    def _failure(
        self,
        record_id: str,
        view: Mapping[str, Any],
        exc: Exception,
        decision: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        detail = getattr(exc, "as_dict", None)
        return {
            "record_id": record_id,
            "object": view.get("object"),
            "name": view.get("name"),
            "reason": (decision or {}).get("reason"),
            "outcome": "failed",
            "error": detail()
            if callable(detail)
            else {"code": type(exc).__name__, "detail": str(exc)},
        }

    def _event(
        self,
        schedule: Mapping[str, Any],
        payload: Mapping[str, Any],
        moment: int,
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        record = self.store.create(
            v.EVENT_COLLECTION,
            {
                "schedule_id": schedule["id"],
                "room_id": schedule.get("room_id"),
                "at_ms": moment,
                "at": rules.to_iso(moment),
                **dict(payload),
            },
            room_id=schedule.get("room_id"),
            actor=actor,
            source=source,
        )
        return self._simple_view(record)

    # -- enrollments, runs, events and notifications: reads ----------------- #

    def _current_enrollment(self, schedule_id: str, record_id: str) -> dict[str, Any] | None:
        found = self.store.find(
            v.ENROLLMENT_COLLECTION, {"schedule_id": schedule_id, "record_id": record_id}, limit=200
        )
        if not found:
            return None
        return max(found, key=lambda row: int(row["data"].get("enrolled_at_ms") or 0))

    def _active_enrollments(self, schedule_id: str) -> list[dict[str, Any]]:
        return [
            enrollment
            for enrollment in self.enrollments(schedule_id=schedule_id, limit=1000)
            if enrollment["status"] in v.ACTIVE_STATUSES
        ]

    def _view_for_record(self, record_id: str, object_name: str | None) -> dict[str, Any]:
        record = self.store.get(record_id)
        if record is None:
            return {
                "id": record_id,
                "room_id": None,
                "object": object_name,
                "name": record_id,
                "data": {},
            }
        return rules.record_view(record)

    def enrollments(
        self,
        *,
        schedule_id: str | None = None,
        record_id: str | None = None,
        room_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if schedule_id:
            records = self.store.find(
                v.ENROLLMENT_COLLECTION, {"schedule_id": schedule_id}, limit=limit
            )
        elif record_id:
            records = self.store.find(
                v.ENROLLMENT_COLLECTION, {"record_id": record_id}, limit=limit
            )
        else:
            records = self.store.list(v.ENROLLMENT_COLLECTION, room_id=room_id, limit=limit)
        if room_id:
            records = [record for record in records if record.get("room_id") == room_id]
        views = [self._enrollment_view(record) for record in records]
        if status:
            views = [view for view in views if view["status"] == status]
        return views

    def get_enrollment(self, enrollment_id: str) -> dict[str, Any]:
        record = self.store.get(enrollment_id)
        if record is None or record["collection"] != v.ENROLLMENT_COLLECTION:
            raise EnrollNotFound(enrollment_id, collection="enrollment")
        return self._enrollment_view(record)

    def list_runs(
        self,
        *,
        schedule_id: str | None = None,
        room_id: str | None = None,
        kind: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if schedule_id:
            records = self.store.find(v.RUN_COLLECTION, {"schedule_id": schedule_id}, limit=limit)
        else:
            records = self.store.list(v.RUN_COLLECTION, room_id=room_id, limit=limit)
        if room_id:
            records = [record for record in records if record.get("room_id") == room_id]
        views = [self._run_view(record) for record in records]
        if kind:
            views = [view for view in views if view["kind"] == kind]
        return views

    def get_run(self, run_id: str) -> dict[str, Any]:
        record = self.store.get(run_id)
        if record is None or record["collection"] != v.RUN_COLLECTION:
            raise EnrollNotFound(run_id, collection="run")
        return self._run_view(record)

    def events(
        self,
        *,
        schedule_id: str | None = None,
        record_id: str | None = None,
        room_id: str | None = None,
        kind: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if schedule_id:
            records = self.store.find(v.EVENT_COLLECTION, {"schedule_id": schedule_id}, limit=limit)
        elif record_id:
            records = self.store.find(v.EVENT_COLLECTION, {"record_id": record_id}, limit=limit)
        else:
            records = self.store.list(v.EVENT_COLLECTION, room_id=room_id, limit=limit)
        if room_id:
            records = [record for record in records if record.get("room_id") == room_id]
        views = [self._simple_view(record) for record in records]
        if kind:
            views = [view for view in views if view["kind"] == kind]
        return views

    def notifications(
        self, *, schedule_id: str | None = None, room_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        if schedule_id:
            records = self.store.find(
                v.NOTIFICATION_COLLECTION, {"schedule_id": schedule_id}, limit=limit
            )
        else:
            records = self.store.list(v.NOTIFICATION_COLLECTION, room_id=room_id, limit=limit)
        if room_id:
            records = [record for record in records if record.get("room_id") == room_id]
        return [self._simple_view(record) for record in records]

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        schedules = self.list_schedules(room_id=room_id, limit=1000)
        enrollments = self.enrollments(room_id=room_id, limit=1000)
        runs = self.list_runs(room_id=room_id, limit=1000)
        by_status: dict[str, int] = {status: 0 for status in v.STATUSES}
        for enrollment in enrollments:
            status = str(enrollment.get("status"))
            by_status[status] = by_status.get(status, 0) + 1
        return {
            "schedules": len(schedules),
            "published": len([schedule for schedule in schedules if schedule.get("published")]),
            "records": len(self.records(room_id=room_id)),
            "enrollments": len(enrollments),
            "by_status": by_status,
            "queued": by_status.get(v.STATUS_QUEUED, 0),
            "completed": by_status.get(v.STATUS_COMPLETED, 0),
            "unenrolled": by_status.get(v.STATUS_UNENROLLED, 0),
            "re_enrollments": len(
                [enrollment for enrollment in enrollments if enrollment.get("re_enrollment")]
            ),
            "runs": len(runs),
            "enrolled_total": sum(
                int(run.get("enrolled") or 0) + int(run.get("re_enrolled") or 0) for run in runs
            ),
            "released_total": sum(int(run.get("released") or 0) for run in runs),
            "notifications": self.store.count_where(v.NOTIFICATION_COLLECTION, {}),
        }

    # -- views -------------------------------------------------------------- #

    def _schedule_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record["data"])
        last = data.get("last_run_at_ms")
        base = last if last is not None else data.get("published_at_ms")
        return {
            "id": record["id"],
            "collection": record["collection"],
            "room_id": record.get("room_id"),
            "revision": record.get("revision"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "deleted_at": record.get("deleted_at"),
            **data,
            "filter_count": len(data.get("filters") or []),
            "unenroll_count": len(data.get("unenroll_filter") or []),
            "action_count": len(data.get("actions") or []),
            "next_run_at_ms": rules.next_run_at_ms(base, data.get("interval_minutes")),
        }

    def _enrollment_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": record["id"],
            "collection": record["collection"],
            "room_id": record.get("room_id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            **dict(record["data"]),
        }

    def _run_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return self._enrollment_view(record)

    def _simple_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return self._enrollment_view(record)


__all__ = ["REASON_LABELS", "ScheduleEnrollmentEngine"]
