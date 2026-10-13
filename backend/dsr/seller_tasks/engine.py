"""The seller-task engine: the only module in this package that reads or writes.

Every write goes through :class:`~dsr.store.RecordStore` and its
:class:`~dsr.db.audited.AuditedDatabase`. A write that must produce more than one
row - a task and the ``Task Added`` event it fires - runs inside one
``transaction()`` block, so the pair cannot half-succeed.

The view a caller reads back carries the task id. The research makes the task id
the join key for every later step: "Task ID appears in task-related workflows,
such as task triggers or after an Add Task step".

The clock is injected. Production passes the wall clock; a test passes a frozen
function, so no test depends on today's date.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from dsr.seller_tasks import rules, vocabulary as v
from dsr.seller_tasks.errors import TaskConflict, TaskNotFound, TaskValidationError
from dsr.store import RecordStore

Clock = Callable[[], datetime]


def _wall_clock() -> datetime:
    return datetime.now(timezone.utc)


class SellerTaskEngine:
    """Tasks, their reminder, their lifecycle events, and the recycling bin."""

    def __init__(self, store: RecordStore, *, now: Clock | None = None) -> None:
        self.store = store
        self._now = now or _wall_clock

    # -- clock -------------------------------------------------------------- #

    def now_ms(self) -> int:
        return rules.parse_timestamp(self._now(), "now") or 0

    # -- vocabulary --------------------------------------------------------- #

    @staticmethod
    def vocabulary() -> dict[str, Any]:
        payload = v.published_vocabulary()
        payload["signal_rules"] = {
            signal: dict(rule) for signal, rule in sorted(rules.SIGNAL_TASK_RULES.items())
        }
        payload["default_subject"] = rules.DEFAULT_SUBJECT
        payload["contact_collection"] = rules.CONTACT_COLLECTION
        return payload

    # -- reads -------------------------------------------------------------- #

    def require(self, task_id: str) -> dict[str, Any]:
        record = self.store.get(task_id)
        if record is None or record["collection"] != v.TASK_COLLECTION:
            raise TaskNotFound(task_id)
        return record

    def require_contact(self, contact_id: str) -> dict[str, Any]:
        record = self.store.get(contact_id)
        if record is None or record["collection"] != rules.CONTACT_COLLECTION:
            raise TaskNotFound(contact_id, collection="contact")
        if str(record["data"].get("object", "")).lower() != rules.CONTACT_OBJECT:
            raise TaskNotFound(contact_id, collection="contact")
        return record

    def get(self, task_id: str) -> dict[str, Any]:
        return self._view(self.require(task_id), now_ms=self.now_ms())

    def list_tasks(
        self,
        *,
        room_id: str | None = None,
        status: str | None = None,
        assignee: str | None = None,
        contact_id: str | None = None,
        attachment: str | None = None,
        include_deleted: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if status:
            where["status"] = status
        if assignee:
            where["assignee"] = assignee
        if contact_id:
            where["contact_id"] = contact_id
        if attachment:
            where["attachment"] = attachment
        if where:
            records = self.store.find(
                v.TASK_COLLECTION, where, limit=limit, include_deleted=include_deleted
            )
        else:
            records = self.store.list(
                v.TASK_COLLECTION, limit=limit, include_deleted=include_deleted, room_id=room_id
            )
        if room_id:
            records = [r for r in records if r.get("room_id") == room_id]
        now_ms = self.now_ms()
        return [self._view(record, now_ms=now_ms) for record in records]

    def events(self, task_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        records = self.store.find(v.EVENT_COLLECTION, {"task_id": task_id}, limit=limit)
        records.sort(key=lambda r: r.get("created_at") or "")
        return [self._event_view(record) for record in records]

    def notifications(self, task_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        records = self.store.find(v.NOTIFICATION_COLLECTION, {"task_id": task_id}, limit=limit)
        return [dict(record["data"], id=record["id"]) for record in records]

    def list_contacts(
        self, *, room_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Contacts a task may attach to. Read from WF-039's collection."""

        records = self.store.find(
            rules.CONTACT_COLLECTION, {"object": rules.CONTACT_OBJECT}, limit=limit
        )
        if room_id:
            records = [r for r in records if r.get("room_id") == room_id]
        return [
            {
                "id": record["id"],
                "room_id": record.get("room_id"),
                "reference_id": record["data"].get("reference_id"),
                "label": _contact_label(record["data"]),
                "body": dict(record["data"].get("body") or {}),
            }
            for record in records
        ]

    def bin(self, *, room_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        records = self.store.list(
            v.TASK_COLLECTION, limit=1000, include_deleted=True, room_id=room_id
        )
        now_ms = self.now_ms()
        binned = [r for r in records if r.get("deleted_at")]
        binned.sort(key=lambda r: r.get("deleted_at") or "", reverse=True)
        return [self._view(record, now_ms=now_ms, include_deleted=True) for record in binned][
            :limit
        ]

    def due_reminders(self, at_ms: int | None = None) -> list[dict[str, Any]]:
        """Open tasks whose reminder time has been reached and not yet fired."""

        moment = at_ms if at_ms is not None else self.now_ms()
        records = self.store.find(v.TASK_COLLECTION, {"status": v.STATUS_NOT_STARTED}, limit=1000)
        due = []
        for record in records:
            data = record["data"]
            reminder = data.get("reminder_at_ms")
            if reminder is None or data.get("reminder_fired_at_ms") is not None:
                continue
            if int(reminder) <= moment:
                due.append(record)
        due.sort(key=lambda r: r["data"].get("reminder_at_ms") or 0)
        return [self._view(record, now_ms=moment) for record in due]

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        live = self.list_tasks(room_id=room_id, limit=1000)
        now_ms = self.now_ms()
        counts: dict[str, int] = {}
        for task in live:
            counts[task["status"]] = counts.get(task["status"], 0) + 1
        overdue = [t for t in live if t["overdue"]]
        reminders_due = [
            t
            for t in live
            if t["status"] == v.STATUS_NOT_STARTED
            and t["reminder_at_ms"] is not None
            and t["reminder_fired_at_ms"] is None
            and t["reminder_at_ms"] <= now_ms
        ]
        return {
            "tasks": len(live),
            "by_status": counts,
            "completed": counts.get(v.STATUS_COMPLETED, 0),
            "open": counts.get(v.STATUS_NOT_STARTED, 0),
            "standalone": len([t for t in live if t["standalone"]]),
            "attached": len([t for t in live if t["attached"]]),
            "overdue": len(overdue),
            "reminders_due": len(reminders_due),
            "binned": len(self.bin(room_id=room_id, limit=1000)),
            "events": self.store.db.count_where(v.EVENT_COLLECTION, {}),
        }

    # -- writes ------------------------------------------------------------- #

    def create(
        self,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Create one task, its ``Task Added`` event, and its notification if asked."""

        now_ms = self.now_ms()
        payload = dict(payload or {})
        signal = payload.get("signal")
        defaults = rules.signal_defaults(signal, now_ms) if signal else None
        fields = rules.normalise_task_payload(payload, signal_defaults_map=defaults)

        if fields["attachment"] == v.ATTACHMENT_CONTACT:
            self.require_contact(fields["contact_id"])

        room_id = payload.get("room_id")
        data = self._task_data(fields, now_ms)
        notification = None
        with self.store.db.transaction(actor=actor, source=source) as tx:
            record = tx.create(
                v.TASK_COLLECTION,
                data,
                room_id=room_id or None,
                actor=actor,
                source=source,
            )
            self._write_event(tx, record["id"], v.TRIGGER_TASK_ADDED, now_ms, actor, source=source)
            if fields["notify"]:
                notification = tx.create(
                    v.NOTIFICATION_COLLECTION,
                    {
                        "task_id": record["id"],
                        "assignee": fields["assignee"],
                        "trigger": v.TRIGGER_TASK_ADDED,
                        "at_ms": now_ms,
                        "message": f"Task assigned: {fields['subject']}",
                    },
                    room_id=room_id or None,
                    actor=actor,
                    source=source,
                )
        view = self._view(record, now_ms=now_ms)
        view["events"] = self.events(record["id"])
        view["notifications"] = [notification["data"]] if notification else []
        return view

    def create_from_signal(
        self,
        signal: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        if not rules.is_known_signal(signal):
            raise TaskValidationError({"signal": f"must be one of {', '.join(v.SIGNALS)}"})
        merged = dict(payload or {})
        merged["signal"] = signal
        return self.create(merged, source=source, actor=actor)

    def update(
        self,
        task_id: str,
        patch: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        record = self.require(task_id)
        current = record["data"]
        merged_raw: dict[str, Any] = {
            "subject": patch.get("subject", current.get("subject")),
            "body": patch.get("body", current.get("body")),
            "due_at": patch.get("due_at", current.get("due_at_ms")),
            "reminder_at": (
                patch["reminder_at"] if "reminder_at" in patch else current.get("reminder_at_ms")
            ),
            "hs_task_status": current.get("status"),
            "hs_task_priority": patch.get("hs_task_priority", current.get("priority")),
            "hs_task_type": patch.get("hs_task_type", current.get("task_type")),
            "assignee": patch.get("assignee", current.get("assignee")),
            "assignee_role": patch.get("assignee_role", current.get("assignee_role")),
            "contact_id": current.get("contact_id"),
        }
        fields = rules.normalise_task_payload(merged_raw)
        now_ms = self.now_ms()
        data = {
            "subject": fields["subject"],
            "body": fields["body"],
            "due_at_ms": fields["due_at_ms"],
            "due_at": fields["due_at"],
            "reminder_at_ms": fields["reminder_at_ms"],
            "reminder_at": fields["reminder_at"],
            "priority": fields["priority"],
            "task_type": fields["task_type"],
            "assignee": fields["assignee"],
            "assignee_role": fields["assignee_role"],
            "updated_at_ms": now_ms,
        }
        record = self.store.update(task_id, data, actor=actor, source=source)
        return self._view(record, now_ms=now_ms)

    def complete(
        self,
        task_id: str,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        record = self.require(task_id)
        if record["data"].get("status") == v.STATUS_COMPLETED:
            raise TaskConflict("task_already_completed", "this task is already completed")
        now_ms = self.now_ms()
        with self.store.db.transaction(actor=actor, source=source) as tx:
            record = tx.update(
                task_id,
                {"status": v.STATUS_COMPLETED, "completed_at_ms": now_ms, "updated_at_ms": now_ms},
                actor=actor,
                source=source,
            )
            self._write_event(tx, task_id, v.TRIGGER_TASK_COMPLETED, now_ms, actor, source=source)
        view = self._view(record, now_ms=now_ms)
        view["events"] = self.events(task_id)
        return view

    def fire_reminder(
        self,
        task_id: str,
        *,
        source: str,
        actor: str | None = None,
        at_ms: int | None = None,
    ) -> dict[str, Any]:
        """Write the ``Task Reminder`` event. The reminder re-fires the workflow."""

        record = self.require(task_id)
        data = record["data"]
        if data.get("status") == v.STATUS_COMPLETED:
            raise TaskConflict("task_completed", "a completed task has no reminder to fire")
        if data.get("reminder_at_ms") is None:
            raise TaskConflict("reminder_not_set", "this task has no reminder time")
        if data.get("reminder_fired_at_ms") is not None:
            raise TaskConflict(
                "reminder_already_fired",
                "the reminder already fired",
                extra={"fired_at_ms": data["reminder_fired_at_ms"]},
            )
        moment = at_ms if at_ms is not None else self.now_ms()
        with self.store.db.transaction(actor=actor, source=source) as tx:
            record = tx.update(
                task_id, {"reminder_fired_at_ms": moment}, actor=actor, source=source
            )
            self._write_event(tx, task_id, v.TRIGGER_TASK_REMINDER, moment, actor, source=source)
        view = self._view(record, now_ms=moment)
        view["events"] = self.events(task_id)
        return view

    def attach(
        self,
        task_id: str,
        contact_id: str,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        self.require(task_id)
        self.require_contact(contact_id)
        now_ms = self.now_ms()
        record = self.store.update(
            task_id,
            {
                "contact_id": contact_id,
                "attachment": v.ATTACHMENT_CONTACT,
                "updated_at_ms": now_ms,
            },
            actor=actor,
            source=source,
        )
        return self._view(record, now_ms=now_ms)

    def detach(
        self,
        task_id: str,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        self.require(task_id)
        now_ms = self.now_ms()
        record = self.store.update(
            task_id,
            {
                "contact_id": None,
                "attachment": v.ATTACHMENT_STANDALONE,
                "updated_at_ms": now_ms,
            },
            actor=actor,
            source=source,
        )
        return self._view(record, now_ms=now_ms)

    def pin(
        self,
        task_id: str,
        engagement_id: str,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Record the pin intent on the task row.

        The research pins a task by writing the task id into
        ``hs_pinned_engagement_id`` on another object. This build writes the pin
        to the task row and does not write to a foreign object.
        """

        self.require(task_id)
        record = self.store.update(
            task_id,
            {"pin_engagement_id": engagement_id, "updated_at_ms": self.now_ms()},
            actor=actor,
            source=source,
        )
        return self._view(record, now_ms=self.now_ms())

    def soft_delete(self, task_id: str, *, source: str, actor: str | None = None) -> dict[str, Any]:
        """Move a task to the recycling bin.

        The delete is soft, so the row and its history stay readable. The view
        reads the record the caller already holds, because a plain ``get``
        excludes a deleted row by design.
        """

        record = self.require(task_id)
        self.store.delete(task_id, actor=actor, source=source)
        view = self._view(record, now_ms=self.now_ms(), include_deleted=True)
        view["binned"] = True
        return {
            "task_id": task_id,
            "deleted": True,
            "bin": True,
            "prior_status": record["data"].get("status"),
            "view": view,
        }

    def restore(self, task_id: str, *, source: str, actor: str | None = None) -> dict[str, Any]:
        record = self.store.restore(task_id, actor=actor, source=source)
        return self._view(record, now_ms=self.now_ms())

    def purge_bin(
        self,
        *,
        source: str,
        actor: str | None = None,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Hard-delete binned rows past the retention window."""

        moment = now_ms if now_ms is not None else self.now_ms()
        cutoff = rules.retention_cutoff_ms(moment)
        records = self.store.list(v.TASK_COLLECTION, limit=1000, include_deleted=True)
        purged = []
        for record in records:
            deleted_at = record.get("deleted_at")
            if not deleted_at:
                continue
            deleted_ms = rules.parse_timestamp(deleted_at, "deleted_at")
            if deleted_ms is not None and deleted_ms < cutoff:
                self.store.delete(record["id"], hard=True, actor=actor, source=source)
                purged.append(record["id"])
        return {
            "purged": len(purged),
            "ids": purged,
            "retention_days": v.RECYCLING_BIN_RETENTION_DAYS,
        }

    # -- internals ---------------------------------------------------------- #

    def _task_data(self, fields: Mapping[str, Any], now_ms: int) -> dict[str, Any]:
        return {
            "subject": fields["subject"],
            "body": fields["body"],
            "due_at_ms": fields["due_at_ms"],
            "due_at": fields["due_at"],
            "reminder_at_ms": fields["reminder_at_ms"],
            "reminder_at": fields["reminder_at"],
            "status": fields["status"],
            "priority": fields["priority"],
            "task_type": fields["task_type"],
            "attachment": fields["attachment"],
            "contact_id": fields["contact_id"],
            "assignee": fields["assignee"],
            "assignee_role": fields["assignee_role"],
            "signal": fields["signal"],
            "source_signal": fields["source_signal"],
            "created_at_ms": now_ms,
            "updated_at_ms": now_ms,
            "completed_at_ms": None,
            "reminder_fired_at_ms": None,
            "pin_engagement_id": None,
        }

    def _write_event(
        self,
        writer: Any,
        task_id: str,
        trigger: str,
        at_ms: int,
        actor: str | None,
        *,
        source: str,
    ) -> dict[str, Any]:
        return writer.create(
            v.EVENT_COLLECTION,
            {
                "task_id": task_id,
                "trigger": trigger,
                "at_ms": at_ms,
                "at": rules.to_iso(at_ms),
            },
            actor=actor,
            source=source,
        )

    def _event_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": record["id"],
            "task_id": record["data"].get("task_id"),
            "trigger": record["data"].get("trigger"),
            "at_ms": record["data"].get("at_ms"),
            "at": record["data"].get("at"),
            "created_at": record.get("created_at"),
        }

    def _view(
        self,
        record: Mapping[str, Any],
        *,
        now_ms: int,
        include_deleted: bool = False,
    ) -> dict[str, Any]:
        data = dict(record["data"])
        due = data.get("due_at_ms")
        reminder = data.get("reminder_at_ms")
        open_task = data.get("status") != v.STATUS_COMPLETED
        reminder_due = (
            open_task
            and reminder is not None
            and data.get("reminder_fired_at_ms") is None
            and int(reminder) <= now_ms
        )
        view = {
            "id": record["id"],
            "collection": record["collection"],
            "room_id": record.get("room_id"),
            "revision": record.get("revision"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "deleted_at": record.get("deleted_at"),
            **data,
            "status_label": _STATUS_LABELS.get(data.get("status"), data.get("status")),
            "type_label": _TYPE_LABELS.get(data.get("task_type"), data.get("task_type")),
            "priority_label": _PRIORITY_LABELS.get(data.get("priority"), data.get("priority")),
            "is_open": open_task,
            "attached": data.get("attachment") == v.ATTACHMENT_CONTACT,
            "standalone": data.get("attachment") == v.ATTACHMENT_STANDALONE,
            "overdue": bool(open_task and due is not None and int(due) < now_ms),
            "reminder_due": bool(reminder_due),
            "due_in_ms": (int(due) - now_ms) if due is not None else None,
            "now_ms": now_ms,
            "binned": bool(record.get("deleted_at")),
        }
        if include_deleted:
            view["binned"] = bool(record.get("deleted_at"))
        return view


#: The words a page shows. A colour is never the only signal, so every status
#: carries a label.
_STATUS_LABELS = {
    v.STATUS_NOT_STARTED: "Not started",
    v.STATUS_COMPLETED: "Completed",
}

_TYPE_LABELS = {
    v.TYPE_EMAIL: "Email",
    v.TYPE_CALL: "Call",
    v.TYPE_TODO: "To-do",
}

_PRIORITY_LABELS = {
    v.PRIORITY_LOW: "Low",
    v.PRIORITY_MEDIUM: "Medium",
    v.PRIORITY_HIGH: "High",
}


def _contact_label(data: Mapping[str, Any]) -> str:
    """A readable contact name from a WF-039 contact row, without a fixed shape."""

    body = data.get("body") or {}
    if isinstance(body, Mapping):
        for key in ("email", "Email", "name", "Name", "full_name"):
            value = body.get(key)
            if value:
                return str(value)
    reference = data.get("reference_id")
    return str(reference or "contact")


__all__ = ["SellerTaskEngine"]
