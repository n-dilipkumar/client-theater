"""WF-109: the escalation workflow reads and writes, over the audited store.

The engine owns the records the router exposes: escalation workflows, the runs a sweep
produces, the Data Connector alert executions and the conversation's escalation activity.
It reads the conversations and message parts WF-107 owns and the SLA timer WF-108 owns,
rather than keeping a second copy of either.

Nothing here imports ``dsr.api`` and nothing opens SQLite. Every read and write goes
through the :class:`~dsr.store.RecordStore` the HTTP layer hands in, so the audit row is
written in the same transaction as the change.

The sweep is the trigger
------------------------

There is no worker. A timer is a projection: WF-108 computes a conversation's live SLA
state from the conversation, its parts, the policy snapshot and the office-hours schedule
at the instant asked for. This engine asks WF-108 for that projection at ``now``, applies
:func:`dsr.sla_escalation.rules.decide`, and writes a run when a workflow fires. A test
freezes the clock by passing ``now``; nothing else here ticks.

``source`` is a required keyword on every writing method, and the router builds each one
from ``router.prefix``, so no audit row can name a path the app stopped serving.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from dsr.conversation_chase import rules as chase_rules, vocabulary as chase_vocab
from dsr.sla_escalation import rules, vocabulary as vocab
from dsr.sla_timers import vocabulary as sla_vocab
from dsr.sla_timers.engine import SlaEngine
from dsr.store import RecordStore


def _moment_now() -> datetime:
    return chase_rules.utcnow()


class SlaEscalationEngine:
    """Workflows, the sweep, the runs, the alerts and the inbox, over one store."""

    def __init__(self, store: RecordStore, now: Any = None) -> None:
        self.store = store
        self._now = now or _moment_now

    def _moment(self) -> datetime:
        moment = self._now()
        if getattr(moment, "tzinfo", None):
            return moment
        return moment.replace(tzinfo=chase_rules.timezone.utc)

    # -- reads the engine needs ---------------------------------------------- #

    def _conversation_record(self, conversation_id: str) -> dict[str, Any]:
        record = self.store.get(conversation_id)
        if record is None or record.get("collection") != vocab.CONVERSATIONS:
            raise rules.ConversationNotFound(conversation_id)
        return record

    def conversation_data(self, conversation_id: str) -> dict[str, Any]:
        return dict(self._conversation_record(conversation_id).get("data") or {})

    def parts_of(self, conversation_id: str) -> list[dict[str, Any]]:
        record = self._conversation_record(conversation_id)
        room = record.get("room_id") or (record.get("data") or {}).get(vocab.ROOM_REF)
        rows = self.store.find(
            vocab.PARTS,
            {vocab.ROOM_REF: room, "conversation_id": conversation_id},
            limit=1000,
        )
        return [dict(row.get("data") or {}) for row in rows]

    def _workflow_record(
        self, workflow_id: str, *, include_archived: bool = False
    ) -> dict[str, Any]:
        record = self.store.db.get(workflow_id, include_deleted=True)
        if record is None or record.get("collection") != vocab.WORKFLOWS:
            raise rules.WorkflowNotFound(workflow_id)
        if record.get("deleted_at") and not include_archived:
            raise rules.EscalationRefusal(
                "workflow_archived", "workflow_id", "This escalation workflow is archived."
            )
        return record

    def _run_record(self, run_id: str) -> dict[str, Any]:
        record = self.store.get(run_id)
        if record is None or record.get("collection") != vocab.RUNS:
            raise rules.RunNotFound(run_id)
        return record

    def _active_timers(self, room_id: str) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.TIMERS, {vocab.ROOM_REF: room_id}, limit=1000)
        return [row for row in rows if (row.get("data") or {}).get("active", True)]

    def timer_view(self, conversation_id: str, now: datetime) -> dict[str, Any] | None:
        """WF-108's live timer projection for a conversation at ``now``.

        Reused rather than recomputed: the escalation's countdown must be the same
        number the inbox shows, so it is the same projection.
        """

        return SlaEngine(self.store, now=lambda: now).timer_for(conversation_id)

    def _policy_target(self, policy_id: str, metric: str) -> int | None:
        record = self.store.db.get(policy_id, include_deleted=True)
        if record is None or record.get("collection") != sla_vocab.POLICIES:
            raise rules.EscalationRefusal(
                "policy_not_found",
                "policy_id",
                f"No SLA policy {policy_id!r} to check the lead time.",
            )
        targets = dict((record.get("data") or {}).get("targets") or {})
        value = targets.get(metric)
        return int(value) if value is not None else None

    def _has_fired(self, workflow_id: str, conversation_id: str, metric: str) -> bool:
        rows = self.store.find(
            vocab.RUNS,
            {"workflow_id": workflow_id, "conversation_id": conversation_id, "metric": metric},
            limit=5,
        )
        return any((row.get("data") or {}).get("fired", True) for row in rows)

    def _check_lead(self, fields: Mapping[str, Any]) -> None:
        """Refuse a before-breach workflow whose lead is not shorter than a known target.

        Where the workflow names its policy, the target is knowable here and the rule the
        spec states is applied at save. Where it names none, the check is made per
        conversation at fire time.
        """

        if fields.get("trigger") not in vocab.LEAD_TRIGGERS or not fields.get("policy_id"):
            return
        target = self._policy_target(fields["policy_id"], fields["metric"])
        if target is None:
            return
        rules.require_lead_shorter(int(fields["lead_seconds"]), target, fields["metric"])

    # -- workflows ------------------------------------------------------------ #

    def create_workflow(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        fields = rules.normalise_workflow(payload)
        self._check_lead(fields)
        now = rules.stamp(self._moment())
        record = self.store.create(
            vocab.WORKFLOWS,
            {
                vocab.ROOM_REF: room_id,
                **fields,
                "created_at": now,
                "updated_at": now,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self.workflow_view(record["id"])

    def workflow_view(self, workflow_id: str) -> dict[str, Any]:
        record = self._workflow_record(workflow_id, include_archived=True)
        data = dict(record.get("data") or {})
        trigger = data.get("trigger") or vocab.DEFAULT_TRIGGER
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id") or data.get(vocab.ROOM_REF),
            "name": data.get("name"),
            "description": data.get("description"),
            "trigger": trigger,
            "trigger_label": vocab.TRIGGER_LABELS.get(trigger),
            "trigger_category": vocab.TRIGGER_CATEGORY,
            "trigger_category_label": vocab.TRIGGER_CATEGORY_LABEL,
            "metric": data.get("metric"),
            "metric_label": vocab.METRIC_LABELS.get(data.get("metric")),
            "metric_spec_label": vocab.METRIC_SPEC_LABELS.get(data.get("metric")),
            "lead_seconds": data.get("lead_seconds"),
            "policy_id": data.get("policy_id"),
            "level": vocab.LEVEL_FOR_TRIGGER.get(trigger),
            "actions": list(data.get("actions") or []),
            "action_labels": {
                str(action.get("kind")): vocab.ACTION_LABELS.get(str(action.get("kind")))
                for action in (data.get("actions") or [])
            },
            "live": bool(data.get("live")),
            "archived": record.get("deleted_at") is not None,
            "runs": len(self.store.find(vocab.RUNS, {"workflow_id": workflow_id}, limit=1000)),
            "created_at": data.get("created_at"),
            "updated_at": record.get("updated_at"),
        }

    def workflows(self, room_id: str, *, live_only: bool = False) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.WORKFLOWS, {vocab.ROOM_REF: room_id}, limit=500)
        views = [self.workflow_view(row["id"]) for row in rows]
        if live_only:
            return [view for view in views if view["live"]]
        return views

    def update_workflow(
        self,
        workflow_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Edit a workflow. Every field is revalidated, so a legal value cannot become illegal."""

        current = self._workflow_record(workflow_id, include_archived=True)
        data = dict(current.get("data") or {})
        body = dict(payload or {})
        merged = {
            "name": body.get("name", data.get("name")),
            "description": body.get("description", data.get("description")),
            "trigger": body.get("trigger", data.get("trigger")),
            "metric": body.get("metric", data.get("metric")),
            "lead_seconds": body.get("lead_seconds", data.get("lead_seconds")),
            "policy_id": body.get("policy_id", data.get("policy_id")),
            "actions": body.get("actions", data.get("actions")),
            "live": body.get("live", data.get("live")),
        }
        fields = rules.normalise_workflow(merged)
        self._check_lead(fields)
        fields["updated_at"] = rules.stamp(self._moment())
        self.store.update(workflow_id, fields, actor=actor, source=source)
        return self.workflow_view(workflow_id)

    def delete_workflow(
        self, workflow_id: str, *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        """Archive a workflow. Its runs keep the history they recorded."""

        self._workflow_record(workflow_id, include_archived=True)
        self.store.delete(workflow_id, actor=actor, source=source)
        return {"id": workflow_id, "archived": True}

    def go_live(self, workflow_id: str, *, source: str, actor: str | None = None) -> dict[str, Any]:
        """Set the workflow live. The flow's step 6: "Set the workflow live"."""

        current = self._workflow_record(workflow_id)
        if (current.get("data") or {}).get("live"):
            raise rules.EscalationRefusal(
                "workflow_already_live", "workflow_id", "This escalation workflow is already live."
            )
        self.store.update(
            workflow_id,
            {"live": True, "updated_at": rules.stamp(self._moment())},
            actor=actor,
            source=source,
        )
        return self.workflow_view(workflow_id)

    # -- the sweep ------------------------------------------------------------ #

    def evaluate(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """One pass over the room's live workflows and active SLA timers.

        Nothing is due until somebody calls this, which is the cost of the explicit-route
        decision, and it is reported rather than hidden: the response names every decision,
        the reason a run was suppressed, and the runs it wrote.

        A conversation with no active SLA is still evaluated, with the published
        ``no_active_sla`` reason, so the sweep proves it considered it.
        """

        body = dict(payload or {})
        now = rules.coerce_instant(body.get("now")) or self._moment()
        workflows = self.workflows(room_id, live_only=True)
        decisions: list[dict[str, Any]] = []
        fired: list[dict[str, Any]] = []

        conversations = self.store.find(vocab.CONVERSATIONS, {vocab.ROOM_REF: room_id}, limit=500)
        active_by_conversation = {
            (row.get("data") or {}).get("conversation_id"): row
            for row in self._active_timers(room_id)
        }

        for conversation_record in conversations:
            conversation_id = conversation_record["id"]
            conversation = dict(conversation_record.get("data") or {})
            parts = self.parts_of(conversation_id)
            timer_view = (
                self.timer_view(conversation_id, now)
                if conversation_id in active_by_conversation
                else None
            )
            for workflow in workflows:
                metric = workflow["metric"]
                target_override = None
                if workflow.get("policy_id") and timer_view:
                    if timer_view.get("policy_id") == workflow["policy_id"]:
                        target_override = self._policy_target(workflow["policy_id"], metric)
                    else:
                        # The workflow names a policy this conversation's timer is not on,
                        # so there is no target to check against and the workflow does not
                        # apply. That is what the spec's SLA target selector implies.
                        decisions.append(
                            {
                                "workflow_id": workflow["id"],
                                "workflow_name": workflow["name"],
                                "conversation_id": conversation_id,
                                **rules.skip_decision(
                                    workflow, vocab.SKIP_POLICY_MISMATCH, timer_view
                                ),
                            }
                        )
                        continue
                decision = rules.decide(
                    workflow,
                    timer_view,
                    conversation,
                    parts,
                    now,
                    already_fired=self._has_fired(workflow["id"], conversation_id, metric),
                    target_seconds=target_override,
                )
                decisions.append(
                    {
                        "workflow_id": workflow["id"],
                        "workflow_name": workflow["name"],
                        "conversation_id": conversation_id,
                        **decision,
                    }
                )
                if decision["fire"]:
                    fired.append(
                        self._fire(
                            workflow,
                            conversation_record,
                            decision,
                            now,
                            source=source,
                            actor=actor,
                        )
                    )

        counts = self._decision_counts(decisions)
        return {
            "room_id": room_id,
            "checked_at": rules.stamp(now),
            "workflows": len(workflows),
            "conversations_checked": len(conversations),
            "evaluated": len(decisions),
            "fired": len(fired),
            "runs": fired,
            "decisions": decisions,
            "counts": counts,
        }

    def _decision_counts(self, decisions: Sequence[Mapping[str, Any]]) -> dict[str, int]:
        counts = {reason: 0 for reason in vocab.SKIP_REASONS}
        counts["fired"] = 0
        for decision in decisions:
            if decision.get("fire"):
                counts["fired"] += 1
            else:
                reason = str(decision.get("reason"))
                counts[reason] = counts.get(reason, 0) + 1
        return counts

    # -- applying a run ------------------------------------------------------- #

    def _fire(
        self,
        workflow: Mapping[str, Any],
        conversation_record: Mapping[str, Any],
        decision: Mapping[str, Any],
        now: datetime,
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Apply one firing's actions and record the run, atomically.

        The conversation mutation, the internal note, the alert executions, the run and the
        activity row are written in one transaction, so a reader never sees a run whose
        actions are half applied.
        """

        conversation_id = conversation_record["id"]
        room_id = conversation_record.get("room_id") or (conversation_record.get("data") or {}).get(
            vocab.ROOM_REF
        )
        conversation = dict(conversation_record.get("data") or {})
        level = decision.get("level") or vocab.LEVEL_FOR_TRIGGER[workflow["trigger"]]
        applied: list[dict[str, Any]] = []
        executions: list[dict[str, Any]] = []
        updates: dict[str, Any] = {}
        note_parts: list[dict[str, Any]] = []
        at = rules.stamp(now)

        for action in workflow.get("actions") or []:
            kind = action.get("kind")
            summary = rules.action_summary(action)
            entry = {"kind": kind, "summary": summary}
            if kind == vocab.ACTION_ASSIGN:
                if action.get("assignee"):
                    updates["assignee"] = action["assignee"]
                    entry["assignee"] = action["assignee"]
                if action.get("team"):
                    updates["assigned_team"] = action["team"]
                    entry["team"] = action["team"]
            elif kind == vocab.ACTION_TAG:
                updates["tags"] = rules.merge_tag(conversation.get("tags"), action["tag"])
                entry["tag"] = action["tag"]
            elif kind == vocab.ACTION_NOTE:
                note_parts.append(
                    {
                        vocab.ROOM_REF: room_id,
                        "conversation_id": conversation_id,
                        "author_kind": chase_vocab.AUTHOR_SYSTEM,
                        "at": at,
                        "body": action["body"],
                        "origin": "workflow",
                        "sent_by_this_product": vocab.SENT_BY_THIS_PRODUCT,
                    }
                )
                entry["body"] = action["body"]
            elif kind == vocab.ACTION_NOTIFY:
                entry["via"] = action.get("via")
                entry["channel"] = action.get("channel")
            elif kind == vocab.ACTION_ALERT:
                outcome = rules.alert_outcome(action)
                executions.append(outcome)
                entry["alert"] = {
                    "success": outcome["success"],
                    "http_status": outcome["http_status"],
                    "error_type": outcome["error_type"],
                }
            applied.append(entry)

        updates.update(
            {
                "priority": True,
                "escalation_level": level,
                "escalation_rank": rules.escalation_rank(level),
                "escalation_workflow": workflow.get("name"),
                "escalated_at": at,
            }
        )

        run_fields = {
            vocab.ROOM_REF: room_id,
            "workflow_id": workflow["id"],
            "workflow_name": workflow["name"],
            "conversation_id": conversation_id,
            "trigger": workflow["trigger"],
            "metric": workflow["metric"],
            "metric_label": vocab.METRIC_SPEC_LABELS.get(workflow["metric"], workflow["metric"]),
            "level": level,
            "lead_seconds": workflow.get("lead_seconds"),
            "fire_at": decision.get("fire_at") or at,
            "remaining_seconds": decision.get("remaining_seconds"),
            "target_seconds": decision.get("target_seconds"),
            "actions": applied,
            "alert_count": len(executions),
            "alert_failures": sum(1 for outcome in executions if not outcome["success"]),
            "fired": True,
            "at": at,
        }

        with self.store.db.transaction(actor=actor, source=source) as tx:
            tx.update(conversation_id, updates)
            for part in note_parts:
                tx.create(vocab.PARTS, part, room_id=room_id, actor=actor, source=source)
            for outcome in executions:
                tx.create(
                    vocab.EXECUTIONS,
                    {
                        vocab.ROOM_REF: room_id,
                        "workflow_id": workflow["id"],
                        "conversation_id": conversation_id,
                        "at": at,
                        **outcome,
                    },
                    room_id=room_id,
                    actor=actor,
                    source=source,
                )
            run = tx.create(vocab.RUNS, run_fields, room_id=room_id, actor=actor, source=source)
            tx.create(
                vocab.ACTIVITY,
                {
                    vocab.ROOM_REF: room_id,
                    "conversation_id": conversation_id,
                    "workflow_id": workflow["id"],
                    "run_id": run["id"],
                    "trigger": workflow["trigger"],
                    "metric": workflow["metric"],
                    "level": level,
                    "type": vocab.LEVEL_LABELS[level],
                    "at": at,
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
        return self.run_view(run["id"])

    # -- reads ---------------------------------------------------------------- #

    def run_view(self, run_id: str) -> dict[str, Any]:
        record = self._run_record(run_id)
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id") or data.get(vocab.ROOM_REF),
            "workflow_id": data.get("workflow_id"),
            "workflow_name": data.get("workflow_name"),
            "conversation_id": data.get("conversation_id"),
            "trigger": data.get("trigger"),
            "trigger_label": vocab.TRIGGER_LABELS.get(str(data.get("trigger"))),
            "metric": data.get("metric"),
            "metric_label": data.get("metric_label"),
            "level": data.get("level"),
            "level_label": vocab.LEVEL_LABELS.get(str(data.get("level"))),
            "lead_seconds": data.get("lead_seconds"),
            "fire_at": data.get("fire_at"),
            "remaining_seconds": data.get("remaining_seconds"),
            "target_seconds": data.get("target_seconds"),
            "actions": list(data.get("actions") or []),
            "alert_count": data.get("alert_count") or 0,
            "alert_failures": data.get("alert_failures") or 0,
            "at": data.get("at"),
        }

    def runs(
        self,
        room_id: str,
        *,
        workflow_id: str | None = None,
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.RUNS, {vocab.ROOM_REF: room_id}, limit=1000)
        entries = [self.run_view(row["id"]) for row in rows]
        if workflow_id:
            entries = [entry for entry in entries if entry["workflow_id"] == workflow_id]
        if conversation_id:
            entries = [entry for entry in entries if entry["conversation_id"] == conversation_id]
        return sorted(entries, key=lambda entry: str(entry.get("at") or ""))

    def executions(self, room_id: str, *, run_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.EXECUTIONS, {vocab.ROOM_REF: room_id}, limit=1000)
        entries = [dict(row.get("data") or {}) | {"id": row.get("id")} for row in rows]
        if run_id:
            entries = [entry for entry in entries if entry.get("run_id") == run_id]
        return sorted(entries, key=lambda entry: str(entry.get("at") or ""))

    # -- the escalated inbox -------------------------------------------------- #

    def escalated_inbox(self, room_id: str, *, sort: str | None = None) -> dict[str, Any]:
        """Every conversation, with its escalation rank and its next SLA target.

        The default sort puts a breached escalation above a pre-warning, and both above an
        un-escalated conversation. ``sort=next_target`` orders by the nearest SLA deadline
        instead, which is WF-108's inbox order.
        """

        now = self._moment()
        conversations = self.store.find(vocab.CONVERSATIONS, {vocab.ROOM_REF: room_id}, limit=500)
        rows: list[dict[str, Any]] = []
        for record in conversations:
            data = dict(record.get("data") or {})
            view = self.timer_view(record["id"], now)
            nearest = view.get("nearest") if view else None
            level = data.get("escalation_level")
            rows.append(
                {
                    "conversation_id": record["id"],
                    "subject": data.get("subject"),
                    "inbox": data.get("inbox"),
                    "state": data.get("state"),
                    "priority": bool(data.get("priority")),
                    "escalation_level": level,
                    "escalation_level_label": vocab.LEVEL_LABELS.get(str(level)),
                    "escalation_rank": rules.escalation_rank(level),
                    "escalated_at": data.get("escalated_at"),
                    "escalation_workflow": data.get("escalation_workflow"),
                    "sla": view.get("policy_name") if view else None,
                    "badge": nearest.get("badge") if nearest else None,
                    "nearest": nearest,
                    "next_target_seconds": (
                        (0 - nearest["seconds"])
                        if nearest and nearest["breached"]
                        else (nearest["seconds"] if nearest else None)
                    ),
                }
            )

        if sort == "next_target":
            rows.sort(
                key=lambda row: (
                    row["next_target_seconds"] is None,
                    row["next_target_seconds"] if row["next_target_seconds"] is not None else 0,
                )
            )
        else:
            rows.sort(
                key=lambda row: (
                    -row["escalation_rank"],
                    row["next_target_seconds"] is None,
                    row["next_target_seconds"] if row["next_target_seconds"] is not None else 0,
                )
            )
        return {
            "room_id": room_id,
            "conversations": rows,
            "count": len(rows),
            "sort": sort or "escalation",
            "levels": list(vocab.LEVELS),
            "level_labels": dict(vocab.LEVEL_LABELS),
        }

    def summary(self, room_id: str) -> dict[str, Any]:
        workflows = self.workflows(room_id)
        runs = self.store.find(vocab.RUNS, {vocab.ROOM_REF: room_id}, limit=1000)
        executions = self.store.find(vocab.EXECUTIONS, {vocab.ROOM_REF: room_id}, limit=1000)
        conversations = self.store.find(vocab.CONVERSATIONS, {vocab.ROOM_REF: room_id}, limit=500)
        failed_alerts = [
            row for row in executions if (row.get("data") or {}).get("success") is False
        ]
        levels: dict[str, int] = {level: 0 for level in vocab.LEVELS}
        for record in conversations:
            level = (record.get("data") or {}).get("escalation_level")
            if level in levels:
                levels[level] += 1
        return {
            "room_id": room_id,
            "workflows": len(workflows),
            "live_workflows": sum(1 for view in workflows if view["live"]),
            "runs": len(runs),
            "alert_executions": len(executions),
            "failed_alerts": len(failed_alerts),
            "escalated_conversations": sum(levels.values()),
            "by_level": levels,
            "level_labels": dict(vocab.LEVEL_LABELS),
            "trigger_category": vocab.TRIGGER_CATEGORY,
            "trigger_category_label": vocab.TRIGGER_CATEGORY_LABEL,
        }

    def activity(self, room_id: str, conversation_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.ACTIVITY, {vocab.ROOM_REF: room_id}, limit=1000)
        entries = [dict(row.get("data") or {}) | {"id": row.get("id")} for row in rows]
        if conversation_id:
            entries = [
                entry for entry in entries if entry.get("conversation_id") == conversation_id
            ]
        return sorted(entries, key=lambda entry: str(entry.get("at") or ""))
