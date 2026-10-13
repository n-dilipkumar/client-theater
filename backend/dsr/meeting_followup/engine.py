"""The facade the HTTP layer calls, and the only module here that stores anything.

Every read and write goes through the :class:`~dsr.store.RecordStore` the caller
hands in, so the audit row is written in the same transaction as the change. This
module opens no connection, imports no framework, and never writes a ``source=``
string of its own: the HTTP layer builds every source from the router, so the
audit log cannot name a route the app stopped serving.

The clock is injected rather than read at call time. Every rule in this package is
a statement about an instant - a 30-minute push window, a morning evaluation, a
24-hour reminder - so the clock has to be the test's. A test that depended on the
machine's date would pass today and fail in a month, which is the defect the brief
warns about by name.

Three engines, one per half of the workflow, because the two halves share no rule
and no collection:

:meth:`FollowupEngine.prepare`
    The prep page, the dry run and Ask anything. Reads and writes the prep
    collections and nothing else.

:meth:`FollowupEngine.enrol`
    The When / If / Then automations, the CRM records they read, the assignment
    batch, the flow instances and the to-do queue. This is where both researched
    permission gates are applied, on every path that could enrol a prospect.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping

from dsr.meeting_followup import (
    automations as rules,
    cal,
    crm,
    flows,
    instances,
    prep,
    reminders,
    vocabulary as vocab,
)
from dsr.meeting_followup.errors import (
    AutomationNotFound,
    FlowInstanceNotFound,
    FlowNotFound,
    PrepPageNotFound,
    TodoNotFound,
)
from dsr.store import RecordStore


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")


def _starting_status(touches: list[Mapping[str, Any]], today: date) -> str:
    """The status a new instance starts in, given when its first touch is due.

    Derived, and derived from the touch schedule rather than chosen freely. A
    sequence whose first touch is due today or earlier has started, so it is
    ``Running``; one whose first touch is still in the future is ``Pending``,
    because that is the only one of the three researched statuses that describes a
    sequence which has not begun.

    ``today`` is the engine's clock, never the machine's date. A rule that read the
    wall clock would pass today and fail whenever the test's frozen date and the
    machine's date disagreed, which is the time-bomb the brief warns about.

    The vendor's response carries ``Running`` on the assignment itself, so that
    value stays on the entry the caller sees. This is the record's own status, and
    the two are reported side by side rather than conflated: the response says what
    the vendor would have returned, and the record says what this sequence is
    actually doing.
    """
    first = touches[0] if touches else None
    if first is None:
        return vocab.STATUS_PENDING
    due = str(first.get("due_on") or "")
    if due and due <= today.isoformat():
        return vocab.STATUS_RUNNING
    return vocab.STATUS_PENDING


class FollowupEngine:
    """The flow over the audited store, with no outbound socket."""

    def __init__(self, store: RecordStore, clock=None) -> None:
        self.store = store
        self._clock = clock or _utcnow

    def clock(self) -> datetime:
        return self._clock()

    # ----------------------------------------------------------------- #
    # CRM records
    # ----------------------------------------------------------------- #

    def upsert_crm_record(
        self,
        object_type: str,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Create or update one CRM entity record.

        One route for both operations, because the vendor's own path is one: the
        only difference is the operation and the mandatory modification date, and
        both are fields on the payload.
        """
        data = crm.normalise_record(object_type, payload)
        collection = crm.collection_for(data["object_type"])
        existing = self.store.find(collection, {"crm_id": data["crm_id"]})
        if existing:
            record = self.store.update(
                existing[0]["id"],
                {**(existing[0].get("data") or {}), **data},
                actor=actor,
                source=source,
            )
            return {"record": record, "operation": "update"}
        record = self.store.create(collection, data, room_id=room_id, actor=actor, source=source)
        return {"record": record, "operation": "create"}

    def crm_records(
        self, object_type: str | None = None, *, room_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Every CRM record, or every record of one object type."""
        if object_type:
            return self.store.list(crm.collection_for(object_type), limit=1000)
        out: list[dict[str, Any]] = []
        for kind in vocab.OBJECT_TYPES:
            out.extend(self.store.list(crm.collection_for(kind), limit=1000))
        return out

    def find_crm_record(self, crm_id: str) -> dict[str, Any] | None:
        """One CRM record by its Gong object id, across the four types."""
        for kind in vocab.OBJECT_TYPES:
            found = self.store.find(crm.collection_for(kind), {"crm_id": crm_id})
            if found:
                return found[0]
        return None

    def directory_users(self, *, limit: int = 500) -> list[dict[str, Any]]:
        """The seller directory the assignee gate resolves against.

        The Engage licence is a property of a Gong user, and no shipped collection
        holds one, so this reads the generic ``directory_user`` collection that
        :meth:`add_user` writes. Resolved through ``find`` rather than ``list`` so
        a room cannot see a directory entry that belongs to none.
        """
        return self.store.find("directory_user", {}, limit=limit)

    def add_user(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Register one assignee and their Engage licence.

        A seller is a record, not a credential: this stores a name, an email and a
        boolean, and nothing here is a secret.
        """
        email = str(payload.get("email") or "").strip().lower()
        if not email or "@" not in email:
            from dsr.meeting_followup.errors import AutomationInvalid

            raise AutomationInvalid("email is required", {"email": "required"})
        data = {
            "email": email,
            "name": str(payload.get("name") or email.split("@")[0]),
            vocab.ENGAGE_LICENSE_FIELD: bool(payload.get(vocab.ENGAGE_LICENSE_FIELD, False)),
        }
        existing = self.store.find("directory_user", {"email": email})
        if existing:
            return self.store.update(existing[0]["id"], data, actor=actor, source=source)
        return self.store.create(
            "directory_user", data, room_id=room_id, actor=actor, source=source
        )

    def find_user(self, email: str) -> dict[str, Any] | None:
        """One directory user by email, case-folded."""
        found = self.store.find("directory_user", {"email": str(email or "").lower()})
        return found[0] if found else None

    # ----------------------------------------------------------------- #
    # The CRM sync run
    # ----------------------------------------------------------------- #

    def sync_crm(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record one CRM sync run and the outbound request it would carry.

        The cadence rule and the modification-date rule are enforced by
        :func:`~dsr.meeting_followup.crm.validate_sync_run` before anything is
        written, so a refused run leaves no row and no audit entry.
        """
        run = crm.validate_sync_run(payload)
        run["request"] = crm.entities_request(run)
        run["executed_at"] = _stamp(self.clock())
        return self.store.create(
            vocab.CRM_SYNC_COLLECTION,
            run,
            room_id=room_id,
            actor=actor,
            source=source,
        )

    def sync_runs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """The recorded sync runs, newest first."""
        return self.store.list(vocab.CRM_SYNC_COLLECTION, limit=limit)

    # ----------------------------------------------------------------- #
    # Flows
    # ----------------------------------------------------------------- #

    def create_flow(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Declare a multi-touch follow-up flow.

        Steps are ordered by their offset, so the queue a seller sees is the
        sequence the seller wrote rather than the order the form posted them.
        """
        from dsr.meeting_followup.errors import FlowInvalid

        name = str(payload.get("name") or "").strip()
        if not name:
            raise FlowInvalid("name is required", {"name": "required"})
        steps = payload.get("steps") or []
        if not isinstance(steps, list) or not steps:
            raise FlowInvalid("a flow needs at least one step", {"steps": "at least one step"})
        for step in steps:
            kind = str(step.get("kind") or "").strip()
            if kind and kind not in vocab.TODO_KINDS:
                raise FlowInvalid(
                    f"step kind {kind!r} is not one of {', '.join(vocab.TODO_KINDS)}",
                    {"steps.kind": f"one of: {', '.join(vocab.TODO_KINDS)}"},
                )
        flow_id = str(payload.get("flow_id") or name.lower().replace(" ", "-"))
        data = {
            "flow_id": flow_id,
            "name": name,
            "description": str(payload.get("description") or ""),
            vocab.PERSONAL_FLOW_FIELD: bool(payload.get(vocab.PERSONAL_FLOW_FIELD, False)),
            "exclusive": bool(payload.get("exclusive", True)),
            "steps": sorted(
                [dict(step) for step in steps],
                key=lambda step: int(step.get("offset_days") or 0),
            ),
        }
        return self.store.create(
            vocab.FLOW_COLLECTION, data, room_id=room_id, actor=actor, source=source
        )

    def get_flow(self, flow_id: str) -> dict[str, Any]:
        """One flow by its business id.

        Resolved through ``find`` rather than a bare ``get``, because the flow id
        is a payload field a seller chose and not the record id.
        """
        found = self.store.find(vocab.FLOW_COLLECTION, {"flow_id": str(flow_id)})
        if not found:
            raise FlowNotFound(f"no flow with flow_id {flow_id!r}")
        return found[0]

    def flows(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Every declared flow."""
        return self.store.list(vocab.FLOW_COLLECTION, limit=limit)

    # ----------------------------------------------------------------- #
    # Automations
    # ----------------------------------------------------------------- #

    def create_automation(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Declare a When / If / Then automation.

        Three researched refusals are applied here, before the record is written:
        the flow must exist and must not be personal, and the assignee must hold an
        Engage licence. The fourth, the parent account, is per prospect and is
        applied in :meth:`enrol`.
        """
        data = rules.validate_automation(payload)
        flow = self.get_flow(data["flow_id"])
        crm.assert_not_personal(flow.get("data") or flow)

        assignee = None
        if data["assignee_email"]:
            assignee = self.find_user(data["assignee_email"])
            # The gate reads the *payload*, so the record envelope is unwrapped
            # before it is checked. Passing the envelope would read the licence off
            # a record that has none and refuse a licensed assignee.
            crm.assert_engaged((assignee or {}).get("data") or assignee)
        elif data["trigger"]["kind"] != vocab.TRACKER_DETECTED:
            from dsr.meeting_followup.errors import AutomationInvalid

            raise AutomationInvalid(
                "assignee_email is required: the selected user owns the flow to-dos "
                "and is the sender for any emails in the flow",
                {"assignee_email": "required"},
            )

        data["flow_name"] = str((flow.get("data") or {}).get("name") or "")
        data["assignee_licensed"] = True
        data["created_at"] = _stamp(self.clock())
        return self.store.create(
            vocab.AUTOMATION_COLLECTION,
            data,
            room_id=room_id,
            actor=actor,
            source=source,
        )

    def get_automation(self, automation_id: str) -> dict[str, Any]:
        """One automation by record id."""
        record = self.store.get(str(automation_id))
        if record is None or record.get("collection") != vocab.AUTOMATION_COLLECTION:
            raise AutomationNotFound(f"no automation with id {automation_id!r}")
        return record

    def automations(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Every automation."""
        return self.store.list(vocab.AUTOMATION_COLLECTION, limit=limit)

    def evaluate_event(
        self,
        event: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Evaluate one event against every automation.

        Returns every decision rather than raising for an automation that did not
        fire. A buyer's activity failing to match a filter is a fact about the
        buyer, and a caller who got a 400 for it would retry forever.
        """
        decisions: list[dict[str, Any]] = []
        enrolled: list[dict[str, Any]] = []
        for record in self.automations(limit=500):
            decision = rules.evaluate(record.get("data") or record, event)
            decisions.append(decision)
            if not decision["fires"]:
                continue
            prospect = self.find_crm_record(
                str(event.get("crm_id") or event.get("crmProspectId") or "")
            )
            assigned = self.enrol(
                {
                    "crmProspectsIds": [
                        str(event.get("crm_id") or event.get("crmProspectId") or "")
                    ],
                    "flowId": decision["flow_id"],
                    "flowInstanceOwnerEmail": decision["assignee_email"],
                },
                prospect=prospect,
                actor=actor,
                source=source,
            )
            enrolled.append(assigned)
            current = record.get("data") or {}
            self.store.update(
                record["id"],
                {
                    **current,
                    "run_count": int(current.get("run_count") or 0) + 1,
                    "last_run_at": _stamp(self.clock()),
                },
                actor=actor,
                source=source,
            )
        return {
            "event": dict(event),
            "decisions": decisions,
            "evaluated": len(decisions),
            "fired": sum(1 for item in decisions if item["fires"]),
            "enrolled": enrolled,
        }

    def morning_run(
        self,
        *,
        on: date | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """The daily schedule: every date-triggered automation, once.

        This is the only path that can enrol a deal on a date trigger, because the
        research says a date-based trigger "does not run immediately when an
        automation is created or edited". The date is a parameter, so a test
        freezes it rather than reading the machine's calendar.
        """
        today = on or self.clock().date()
        deals = self.store.list(crm.collection_for(vocab.DEAL_OBJECT_TYPE), limit=1000)
        fired: list[dict[str, Any]] = []
        for record in self.automations(limit=500):
            data = record.get("data") or {}
            due = rules.due_on_morning_run(data, today, [d.get("data") or d for d in deals])
            if not due:
                continue
            for deal in due:
                assigned = self.enrol(
                    {
                        "crmProspectsIds": [str(deal.get("crm_id"))],
                        "flowId": str(data.get("flow_id") or ""),
                        "flowInstanceOwnerEmail": str(data.get("assignee_email") or ""),
                    },
                    prospect=self.find_crm_record(str(deal.get("crm_id"))),
                    actor=actor,
                    source=source,
                )
                fired.append(assigned)
            self.store.update(
                record["id"],
                {
                    **data,
                    "last_evaluated_at": _stamp(self.clock()),
                    "run_count": int(data.get("run_count") or 0) + len(due),
                },
                actor=actor,
                source=source,
            )
        return {
            "evaluated_on": today.isoformat(),
            "evaluation_time": rules.morning_run_time(today).isoformat(),
            "schedule_quote": vocab.DATE_TRIGGER_SCHEDULE_QUOTE,
            "fired": len(fired),
            "assignments": fired,
        }

    # ----------------------------------------------------------------- #
    # Assignment
    # ----------------------------------------------------------------- #

    def enrol(
        self,
        payload: Mapping[str, Any],
        *,
        prospect: Mapping[str, Any] | None = None,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Assign a batch of prospects to a flow.

        Writes the instances and the to-do rows the vendor would create, and
        records the split answer verbatim. A prospect that fails its gate appears
        in ``prospectsNotAssigned`` and produces no instance, which is the
        researched behaviour.
        """
        request = flows.normalise_request(payload)
        flow = self.get_flow(request["flowId"])
        flow_data = flow.get("data") or {}
        owner = self.find_user(request["flowInstanceOwnerEmail"])
        owner_data = (owner or {}).get("data") or owner
        crm.assert_engaged(owner_data)

        candidates = [prospect] if prospect is not None else self.crm_records()
        # Unwrapped to payloads before the gates read them. Every gate reads a
        # payload field - crm_id, object_type, parent_account_id - and passing an
        # envelope would make all three read as absent, which would turn every
        # prospect into a per-prospect failure and pass a gate that never ran.
        answer = flows.assign(
            request,
            flow=flow_data,
            owner=owner_data,
            prospects=[dict(c.get("data") or c) for c in candidates if c is not None],
            room_id=room_id or "",
        )

        for entry in answer["prospectsAssigned"]:
            today = self.clock().date()
            touches = flows.build_touches(flow_data, starts_on=today.isoformat())
            record = self.store.create(
                vocab.INSTANCE_COLLECTION,
                {
                    "flow_instance_id": entry["flowInstanceId"],
                    "crm_prospect_id": entry["crmProspectId"],
                    "flow_id": request["flowId"],
                    "status": _starting_status(touches, today),
                    "vendor_status": entry["flowInstanceStatus"],
                    "owner_email": request["flowInstanceOwnerEmail"],
                    "exclusive": entry["exclusive"],
                    "workspace_id": entry["workspaceId"],
                    "steps": touches,
                    "created_at": _stamp(self.clock()),
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
            entry["record_id"] = record["id"]
            for touch in record["data"]["steps"]:
                self.store.create(
                    vocab.TODO_COLLECTION,
                    {
                        "todo_id": f"{record['data']['flow_instance_id']}-{touch['position']}",
                        "queue_id": record["data"]["flow_instance_id"],
                        "flow_instance_id": record["data"]["flow_instance_id"],
                        "owner_email": request["flowInstanceOwnerEmail"],
                        **touch,
                        "completed_at": None,
                    },
                    room_id=room_id,
                    actor=actor,
                    source=source,
                )

        self.store.create(
            vocab.ASSIGN_COLLECTION,
            {
                "request": request,
                "response": {
                    "prospectsAssigned": answer["prospectsAssigned"],
                    "prospectsNotAssigned": answer["prospectsNotAssigned"],
                },
                "endpoint": vocab.ASSIGN_ENDPOINT,
                "scope": vocab.ASSIGN_SCOPE,
                "at": _stamp(self.clock()),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return answer

    # ----------------------------------------------------------------- #
    # Flow instances and the to-do queue
    # ----------------------------------------------------------------- #

    def instances(self, *, owner_email: str | None = None, limit: int = 200) -> list[dict]:
        """Every flow instance, optionally filtered by owner."""
        rows = self.store.list(vocab.INSTANCE_COLLECTION, limit=limit)
        if owner_email:
            rows = [r for r in rows if (r.get("data") or {}).get("owner_email") == owner_email]
        return rows

    def get_instance(self, flow_instance_id: str) -> dict[str, Any]:
        """One instance by its flow instance id."""
        found = self.store.find(
            vocab.INSTANCE_COLLECTION, {"flow_instance_id": str(flow_instance_id)}
        )
        if not found:
            raise FlowInstanceNotFound(f"no flow instance with id {flow_instance_id!r}")
        return found[0]

    def set_instance_status(
        self,
        flow_instance_id: str,
        status: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Move an instance between the three researched statuses.

        The transition is refused with the alternatives attached, and the refusal
        writes nothing: an audit row for a change that did not happen would be a
        lie about the record.
        """
        record = self.get_instance(flow_instance_id)
        data = record.get("data") or {}
        target = instances.advance(data.get("status"), status, steps=data.get("steps") or [])
        return self.store.update(
            record["id"],
            {**data, "status": target},
            actor=actor,
            source=source,
        )

    def todos(
        self, *, owner_email: str | None = None, queue_id: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        """The to-do rows, filtered by owner and queue."""
        rows = self.store.list(vocab.TODO_COLLECTION, limit=limit)
        if owner_email:
            rows = [r for r in rows if (r.get("data") or {}).get("owner_email") == owner_email]
        if queue_id:
            rows = [r for r in rows if (r.get("data") or {}).get("queue_id") == queue_id]
        return rows

    def complete_todo(
        self,
        todo_id: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Complete one to-do in its queue.

        Quoted: "To-dos are grouped into a queue that shows your position as you
        move through them." Completing one is what moves the position, so the
        completed stamp is written here and the position recomputed on the next
        read.
        """
        found = self.store.find(vocab.TODO_COLLECTION, {"todo_id": str(todo_id)})
        if not found:
            raise TodoNotFound(f"no to-do with id {todo_id!r}")
        return self.store.update(
            found[0]["id"],
            {**(found[0].get("data") or {}), "completed_at": _stamp(self.clock())},
            actor=actor,
            source=source,
        )

    def queue(self, owner_email: str, *, flow_reference: str = "") -> dict[str, Any]:
        """One owner's grouped to-do queue, with each row's researched position."""
        rows = self.todos(owner_email=owner_email)
        return flows.queue_view(
            owner_email, [r.get("data") or r for r in rows], flow_reference=flow_reference
        )

    # ----------------------------------------------------------------- #
    # The prep page
    # ----------------------------------------------------------------- #

    def create_prep_page(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Open a prep page for a meeting."""
        data = prep.normalise_page(payload)
        data["created_at"] = _stamp(self.clock())
        record = self.store.create(
            vocab.PREP_COLLECTION, data, room_id=room_id, actor=actor, source=source
        )
        for participant in data["participants"]:
            for entry in participant.get("activity") or []:
                self.store.create(
                    vocab.ACTIVITY_COLLECTION,
                    {
                        "meeting_id": data["meeting_id"],
                        "participant": participant.get("email", ""),
                        **dict(entry),
                    },
                    room_id=room_id,
                    actor=actor,
                    source=source,
                )
        return record

    def get_prep_page(self, meeting_id: str) -> dict[str, Any]:
        """One prep page by meeting id."""
        found = self.store.find(vocab.PREP_COLLECTION, {"meeting_id": str(meeting_id)})
        if not found:
            raise PrepPageNotFound(f"no prep page for meeting {meeting_id!r}")
        return found[0]

    def prep_pages(self, *, room_id: str | None = None, limit: int = 100) -> list[dict]:
        """Every prep page."""
        return self.store.list(vocab.PREP_COLLECTION, limit=limit)

    def prep_view(
        self,
        meeting_id: str,
        *,
        mobile: bool = False,
    ) -> dict[str, Any]:
        """The prep page as a rep reads it, with its recent-activity feed.

        The mobile view omits the feed, which the research says the mobile app
        does. A server that guessed would silently withhold a section a desktop
        rep needed, so the flag travels with the request.
        """
        record = self.get_prep_page(meeting_id)
        activities = [
            r.get("data") or r for r in self.store.list(vocab.ACTIVITY_COLLECTION, limit=1000)
        ]
        scoped = [a for a in activities if str(a.get("meeting_id")) == str(meeting_id)]
        return prep.page_view(record.get("data") or record, activities=scoped, mobile=mobile)

    def dry_run(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Rehearse the meeting, and return the derived participant and feedback.

        Nothing is synthesised: the participant is built from the stored meeting
        context and the feedback is a checklist over the prep sections. Both say
        so on the record.
        """
        data = prep.normalise_dry_run(payload)
        page = self.get_prep_page(data["meeting_id"])
        page_data = page.get("data") or page
        data["participant"] = prep.dry_run_participant(page_data, data["voice"])
        data["feedback"] = prep.dry_run_feedback(page_data, payload.get("covered") or [])
        data["at"] = _stamp(self.clock())
        return self.store.create(vocab.DRY_RUN_COLLECTION, data, actor=actor, source=source)

    def ask(
        self,
        meeting_id: str,
        question: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Answer a question from the prep context, and record it."""
        page = self.get_prep_page(meeting_id)
        answer = prep.answer(page.get("data") or page, question)
        answer["meeting_id"] = meeting_id
        answer["asked_at"] = _stamp(self.clock())
        return self.store.create(vocab.ASK_COLLECTION, dict(answer), actor=actor, source=source)

    # ----------------------------------------------------------------- #
    # Cal webhooks
    # ----------------------------------------------------------------- #

    def receive_webhook(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record one Cal webhook.

        A recording or transcription link is recorded and not fetched: the token in
        the link expires, so a stored link is evidence that a recording existed and
        nothing more.
        """
        event = cal.validate_event(payload)
        event["received_at"] = _stamp(self.clock())
        event["fetched"] = False
        return self.store.create(
            vocab.WEBHOOK_COLLECTION,
            event,
            room_id=room_id,
            actor=actor,
            source=source,
        )

    def webhooks(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """The recorded webhooks, newest first."""
        return self.store.list(vocab.WEBHOOK_COLLECTION, limit=limit)

    # ----------------------------------------------------------------- #
    # AI tracker detections
    # ----------------------------------------------------------------- #

    def record_tracker(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record an AI tracker detection, and evaluate it against the automations.

        A tracker detection is one of the five researched triggers, and a call that
        mentions a concept is the thing the automation reacts to. Both halves
        happen in one call so a caller cannot record a detection and forget the
        rule that depends on it.
        """
        name = str(payload.get("tracker_name") or "").strip()
        if not name:
            from dsr.meeting_followup.errors import AutomationInvalid

            raise AutomationInvalid("tracker_name is required", {"tracker_name": "required"})
        detection = {
            "tracker_name": name,
            "meeting_id": str(payload.get("meeting_id") or ""),
            "concept": str(payload.get("concept") or ""),
            "crm_id": str(payload.get("crm_id") or ""),
            "detected_at": _stamp(self.clock()),
        }
        record = self.store.create(
            vocab.TRACKER_COLLECTION,
            detection,
            room_id=room_id,
            actor=actor,
            source=source,
        )
        evaluated = self.evaluate_event(
            {"kind": vocab.TRACKER_DETECTED, **detection}, actor=actor, source=source
        )
        return {"detection": record, "evaluated": evaluated}

    def detections(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """The recorded tracker detections."""
        return self.store.list(vocab.TRACKER_COLLECTION, limit=limit)

    # ----------------------------------------------------------------- #
    # Forecast reminders
    # ----------------------------------------------------------------- #

    def add_reminder(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Create one Forecast reminder. Slack channels are refused."""
        data = reminders.validate_reminder(payload)
        data["at"] = _stamp(self.clock())
        return self.store.create(
            vocab.REMINDER_COLLECTION,
            data,
            room_id=room_id,
            actor=actor,
            source=source,
        )

    def reminder_rows(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """The configured Forecast reminders, the in-app ones only."""
        return self.store.list(vocab.REMINDER_COLLECTION, limit=limit)

    # ----------------------------------------------------------------- #
    # The summary
    # ----------------------------------------------------------------- #

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Counts for the page header, and the states that need attention."""
        automations = self.automations(limit=500)
        instances = self.instances(limit=500)
        todos = self.todos(limit=1000)
        pages = self.prep_pages(limit=500)

        by_status: dict[str, int] = {status: 0 for status in vocab.FLOW_INSTANCE_STATUSES}
        for row in instances:
            status = str((row.get("data") or {}).get("status") or "")
            if status in by_status:
                by_status[status] += 1

        pending_evaluation = [
            row["id"]
            for row in automations
            if rules.created_or_edited_at_was_not_evaluated(row.get("data") or {})
        ]
        return {
            "room_id": room_id,
            "prep_pages": len(pages),
            "dry_runs": len(self.store.list(vocab.DRY_RUN_COLLECTION, limit=1000)),
            "automations": len(automations),
            "automations_enabled": sum(
                1 for row in automations if (row.get("data") or {}).get("enabled", True)
            ),
            "automations_awaiting_morning_run": len(pending_evaluation),
            "flows": len(self.flows(limit=500)),
            "instances": len(instances),
            "instances_by_status": by_status,
            "todos": len(todos),
            "todos_open": sum(
                1 for row in todos if not (row.get("data") or {}).get("completed_at")
            ),
            "crm_records": {
                kind: len(self.store.list(crm.collection_for(kind), limit=1000))
                for kind in vocab.OBJECT_TYPES
            },
            "webhooks": len(self.store.list(vocab.WEBHOOK_COLLECTION, limit=1000)),
            "assignments": len(self.store.list(vocab.ASSIGN_COLLECTION, limit=1000)),
            "assignment_failures": sum(
                len((row.get("data") or {}).get("response", {}).get("prospectsNotAssigned") or [])
                for row in self.store.list(vocab.ASSIGN_COLLECTION, limit=1000)
            ),
            "schedule_quote": vocab.DATE_TRIGGER_SCHEDULE_QUOTE,
            "statuses": list(vocab.FLOW_INSTANCE_STATUSES),
        }
