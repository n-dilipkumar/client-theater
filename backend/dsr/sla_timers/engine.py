"""WF-108: the SLA timer reads and writes, over the audited store.

The engine owns the records the router exposes: SLA policies, one active timer per
conversation, and the conversation's SLA activity log. It reads the conversations and
message parts WF-107 owns, and the office-hours calendar WF-107 owns, rather than
keeping a second copy of either.

Nothing here imports ``dsr.api`` and nothing opens SQLite. Every read and write goes
through the :class:`~dsr.store.RecordStore` the HTTP layer hands in, so the audit row is
written in the same transaction as the change.

The timer is a projection
-------------------------

A timer's live state is computed from the conversation, its parts, the policy snapshot
and the office-hours schedule at the instant the engine's clock reads. That is what makes
a "continuous" timer work with no worker: nothing has to tick for the state to be
correct, and ``evaluate`` only exists to write the state back and to log the transitions
a reader would otherwise have to diff.

``source`` is a required keyword on every writing method, and the router builds each one
from ``router.prefix``, so no audit row can name a path the app stopped serving.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from dsr.conversation_chase import rules as chase_rules
from dsr.sla_timers import rules, vocabulary as vocab
from dsr.store import RecordStore


def _moment_now() -> datetime:
    return chase_rules.utcnow()


class SlaEngine:
    """Policies, timers, the sweep, the inbox view and the report, over one store."""

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

    def _policy_record(self, policy_id: str, *, include_archived: bool = False) -> dict[str, Any]:
        record = self.store.db.get(policy_id, include_deleted=True)
        if record is None or record.get("collection") != vocab.POLICIES:
            raise rules.PolicyNotFound(policy_id)
        if record.get("deleted_at") and not include_archived:
            raise rules.SlaRefusal("policy_archived", "policy_id", "This SLA policy is archived.")
        return record

    def _timer_record(self, timer_id: str) -> dict[str, Any]:
        record = self.store.get(timer_id)
        if record is None or record.get("collection") != vocab.TIMERS:
            raise rules.TimerNotFound(timer_id)
        return record

    def _active_timer_record(self, conversation_id: str) -> dict[str, Any] | None:
        rows = self.store.find(vocab.TIMERS, {"conversation_id": conversation_id}, limit=5)
        active = [row for row in rows if (row.get("data") or {}).get("active", True)]
        return active[0] if active else None

    # -- office hours --------------------------------------------------------- #

    def office_hours(self, room_id: str) -> dict[str, Any]:
        """The room's schedule, with WF-107's derived default filled in.

        The calendar is WF-107's collection, so a room has one schedule. A room with
        nothing stored still answers, and ``derived`` is ``True`` so a reader can tell the
        default from one somebody set.
        """

        rows = self.store.find(vocab.OFFICE_HOURS, {vocab.ROOM_REF: room_id}, limit=1)
        record = rows[0] if rows else None
        stored = dict(record.get("data") or {}) if record else {}
        raw = stored.get("schedule") if stored.get("schedule") else rules.default_schedule()
        schedule = rules.normalise_schedule(raw)
        return {
            "room_id": room_id,
            "id": record.get("id") if record else None,
            "stored": record is not None,
            "derived": not record,
            "schedule": schedule,
            "timezone": stored.get("timezone") or "UTC",
            "derivation": vocab.OFFICE_HOURS_QUOTE,
        }

    def schedule_for(self, room_id: str) -> dict[str, Any]:
        return self.office_hours(room_id)["schedule"]

    def save_office_hours(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Write the room's schedule, validated before the write."""

        body = dict(payload or {})
        raw = body.get("schedule") if body.get("schedule") is not None else rules.default_schedule()
        schedule = rules.normalise_schedule(raw)
        existing = self.store.find(vocab.OFFICE_HOURS, {vocab.ROOM_REF: room_id}, limit=1)
        fields = {
            vocab.ROOM_REF: room_id,
            "schedule": schedule,
            "timezone": str(body.get("timezone") or "UTC"),
            "updated_at": rules.stamp(self._moment()),
        }
        if existing:
            self.store.update(existing[0]["id"], fields, actor=actor, source=source)
        else:
            self.store.create(
                vocab.OFFICE_HOURS, fields, room_id=room_id, actor=actor, source=source
            )
        return self.office_hours(room_id)

    # -- policies ------------------------------------------------------------- #

    def create_policy(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        fields = rules.normalise_policy(payload)
        now = rules.stamp(self._moment())
        record = self.store.create(
            vocab.POLICIES,
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
        return self.policy_view(record["id"])

    def policy_view(self, policy_id: str) -> dict[str, Any]:
        record = self._policy_record(policy_id, include_archived=True)
        data = dict(record.get("data") or {})
        used = self.used_by(policy_id)
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id") or data.get(vocab.ROOM_REF),
            "name": data.get("name"),
            "description": data.get("description"),
            "targets": data.get("targets") or vocab.default_targets(),
            "target_labels": {key: vocab.METRIC_LABELS[key] for key in (data.get("targets") or {})},
            "pause_reasons": list(data.get("pause_reasons") or []),
            "pause_labels": {
                key: vocab.PAUSE_LABELS[key] for key in (data.get("pause_reasons") or [])
            },
            "object_kind": data.get("object_kind") or vocab.DEFAULT_OBJECT_KIND,
            "metrics": list(
                vocab.metrics_for_kind(data.get("object_kind") or vocab.DEFAULT_OBJECT_KIND)
            ),
            "teams": list(data.get("teams") or []),
            "workflow_name": data.get("workflow_name"),
            "live": bool(data.get("live")),
            "archived": record.get("deleted_at") is not None,
            "created_at": data.get("created_at"),
            "updated_at": record.get("updated_at"),
            "used_by": used,
        }

    def policies(self, room_id: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
        rows = self.store.find(
            vocab.POLICIES,
            {vocab.ROOM_REF: room_id},
            limit=500,
            include_deleted=include_archived,
        )
        return [self.policy_view(row["id"]) for row in rows]

    def update_policy(
        self,
        policy_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Edit a policy. Every field is revalidated, so a legal value cannot become illegal."""

        current = self._policy_record(policy_id, include_archived=True)
        data = dict(current.get("data") or {})
        body = dict(payload or {})
        merged = {
            "name": body.get("name", data.get("name")),
            "description": body.get("description", data.get("description")),
            "targets": body.get("targets", data.get("targets")),
            "pause_reasons": body.get("pause_reasons", data.get("pause_reasons")),
            "object_kind": body.get("object_kind", data.get("object_kind")),
            "teams": body.get("teams", data.get("teams")),
            "workflow_name": body.get("workflow_name", data.get("workflow_name")),
            "live": body.get("live", data.get("live")),
        }
        fields = rules.normalise_policy(merged)
        fields["updated_at"] = rules.stamp(self._moment())
        self.store.update(policy_id, fields, actor=actor, source=source)
        return self.policy_view(policy_id)

    def delete_policy(
        self, policy_id: str, *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        """Archive a policy. Its timers keep their snapshot and stay readable."""

        self._policy_record(policy_id, include_archived=True)
        self.store.delete(policy_id, actor=actor, source=source)
        return {"id": policy_id, "archived": True}

    def used_by(self, policy_id: str) -> dict[str, Any]:
        """The workflows and conversations that depend on a policy, for the Used by column."""

        rows = self.store.find(vocab.TIMERS, {"policy_id": policy_id}, limit=1000)
        workflows = sorted(
            {
                str((row.get("data") or {}).get("workflow_name"))
                for row in rows
                if (row.get("data") or {}).get("workflow_name")
            }
        )
        conversations = {row.get("id") for row in rows}
        active = [row for row in rows if (row.get("data") or {}).get("active", True)]
        return {
            "workflows": workflows,
            "workflow_count": len(workflows),
            "timers": len(conversations),
            "active_timers": len(active),
        }

    # -- applying a policy ---------------------------------------------------- #

    def apply(
        self,
        conversation_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Apply an SLA policy to a conversation, removing any existing active SLA.

        "Intercom supports only one active SLA per conversation, so when a Workflow
        applies a new SLA later, the existing SLA is automatically removed." The removal
        and the insert share one transaction, so a reader never sees two active timers.
        """

        body = dict(payload or {})
        policy_id = str(body.get("policy_id") or "").strip()
        if not policy_id:
            raise rules.SlaRefusal("policy_not_found", "policy_id", "an apply needs a policy_id.")
        conversation = self._conversation_record(conversation_id)
        policy = rules.normalise_policy(self._policy_record(policy_id).get("data") or {})
        conversation_data = dict(conversation.get("data") or {})
        state = rules.require_state(conversation_data.get("state"))
        if state == vocab.STATE_CLOSED:
            raise rules.SlaRefusal(
                "conversation_closed",
                "conversation_id",
                "A closed conversation cannot take an SLA.",
            )

        now = self._moment()
        applied_at = rules.coerce_instant(body.get("applied_at")) or now
        applied_stamp = rules.stamp(applied_at)
        workflow_name = (
            " ".join(str(body.get("workflow_name") or "").split())
            or policy.get("workflow_name")
            or "Workflow"
        )
        room_id = conversation.get("room_id") or conversation_data.get(vocab.ROOM_REF)
        existing = self._active_timer_record(conversation_id)

        with self.store.db.transaction(actor=actor, source=source) as tx:
            if existing is not None:
                tx.update(existing["id"], {"active": False, "removed_at": applied_stamp})
            timer = tx.create(
                vocab.TIMERS,
                {
                    vocab.ROOM_REF: room_id,
                    "conversation_id": conversation_id,
                    "policy_id": policy_id,
                    "policy_name": policy["name"],
                    "workflow_name": workflow_name,
                    "object_kind": policy["object_kind"],
                    "targets": policy["targets"],
                    "pause_reasons": policy["pause_reasons"],
                    "teams": policy["teams"],
                    "applied_at": applied_stamp,
                    "active": True,
                    "reopened_after_breach": False,
                    "state": vocab.TIMER_RUNNING,
                    "metrics": [],
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
            tx.create(
                vocab.ACTIVITY,
                {
                    vocab.ROOM_REF: room_id,
                    "conversation_id": conversation_id,
                    "timer_id": timer["id"],
                    "type": vocab.ACTIVITY_SLA_APPLIED,
                    "sla": policy["name"],
                    "workflow_name": workflow_name,
                    "at": applied_stamp,
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
        return self.timer_view(timer["id"])

    def remove(
        self, conversation_id: str, *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        """Remove the conversation's active SLA."""

        existing = self._active_timer_record(conversation_id)
        if existing is None:
            raise rules.SlaRefusal(
                "no_active_sla", "conversation_id", "There is no active SLA to remove."
            )
        now = rules.stamp(self._moment())
        room_id = existing.get("room_id") or (existing.get("data") or {}).get(vocab.ROOM_REF)
        with self.store.db.transaction(actor=actor, source=source) as tx:
            tx.update(existing["id"], {"active": False, "removed_at": now})
            tx.create(
                vocab.ACTIVITY,
                {
                    vocab.ROOM_REF: room_id,
                    "conversation_id": conversation_id,
                    "timer_id": existing["id"],
                    "type": vocab.ACTIVITY_SLA_REMOVED,
                    "sla": (existing.get("data") or {}).get("policy_name"),
                    "at": now,
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
        return {"conversation_id": conversation_id, "removed": existing["id"], "at": now}

    # -- the timer projection ------------------------------------------------- #

    def _rule_policy(
        self, timer_data: Mapping[str, Any], schedule: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {
            "applied_at": timer_data.get("applied_at"),
            "schedule": schedule,
            "pause_reasons": timer_data.get("pause_reasons") or list(vocab.DEFAULT_PAUSE_REASONS),
            "object_kind": timer_data.get("object_kind") or vocab.DEFAULT_OBJECT_KIND,
            "targets": timer_data.get("targets") or vocab.default_targets(),
        }

    def _timer_view_from(self, record: Mapping[str, Any], now: datetime) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        conversation_id = data.get("conversation_id")
        conversation = dict(self._conversation_record(conversation_id).get("data") or {})
        parts = self.parts_of(conversation_id)
        schedule = self.schedule_for(record.get("room_id") or data.get(vocab.ROOM_REF))
        metrics = rules.progress_for_policy(
            self._rule_policy(data, schedule), conversation, parts, now
        )
        state = rules.timer_state(metrics)
        nearest = rules.nearest_target(metrics)
        reopened = bool(data.get("reopened_after_breach"))
        outcomes = {entry["metric"]: rules.classify_outcome(entry, reopened) for entry in metrics}
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id") or data.get(vocab.ROOM_REF),
            "conversation_id": conversation_id,
            "policy_id": data.get("policy_id"),
            "policy_name": data.get("policy_name"),
            "workflow_name": data.get("workflow_name"),
            "object_kind": data.get("object_kind"),
            "targets": dict(data.get("targets") or {}),
            "pause_reasons": list(data.get("pause_reasons") or []),
            "teams": list(data.get("teams") or []),
            "applied_at": data.get("applied_at"),
            "active": bool(data.get("active", True)),
            "reopened_after_breach": reopened,
            "state": state,
            "state_label": vocab.TIMER_STATE_LABELS[state],
            "badge": nearest["badge"] if nearest else None,
            "badge_label": vocab.BADGE_LABELS[nearest["badge"]] if nearest else None,
            "nearest": nearest,
            "paused": state == vocab.TIMER_PAUSED,
            "metrics": metrics,
            "outcomes": outcomes,
            "conversation_state": conversation.get("state"),
            "checked_at": rules.stamp(now),
        }

    def timer_view(self, timer_id: str) -> dict[str, Any]:
        return self._timer_view_from(self._timer_record(timer_id), self._moment())

    def timer_for(self, conversation_id: str) -> dict[str, Any] | None:
        record = self._active_timer_record(conversation_id)
        if record is None:
            return None
        return self._timer_view_from(record, self._moment())

    # -- the sweep ------------------------------------------------------------ #

    def evaluate(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Recompute every active timer in the room, persist it, and log transitions.

        Nothing is due until somebody calls this, which is the cost of the explicit-route
        decision. It is reported rather than hidden: the response counts the timers by
        state and names the transitions it wrote.

        A breach on a snoozed conversation reopens it, per the auto-unsnooze rule, and the
        reopen is recorded on the timer so the report can classify the breach as fixed
        rather than as a plain miss.
        """

        body = dict(payload or {})
        now = rules.coerce_instant(body.get("now")) or self._moment()
        rows = self.store.find(vocab.TIMERS, {vocab.ROOM_REF: room_id}, limit=1000)
        active = [row for row in rows if (row.get("data") or {}).get("active", True)]
        transitions: list[dict[str, Any]] = []
        reopened: list[str] = []

        for row in active:
            data = dict(row.get("data") or {})
            conversation_id = data.get("conversation_id")
            conversation_record = self._conversation_record(conversation_id)
            conversation = dict(conversation_record.get("data") or {})
            parts = self.parts_of(conversation_id)
            schedule = self.schedule_for(room_id)
            metrics = rules.progress_for_policy(
                self._rule_policy(data, schedule), conversation, parts, now
            )

            previous = {
                entry.get("metric"): entry.get("state") for entry in data.get("metrics") or []
            }
            reopened_flag = bool(data.get("reopened_after_breach"))
            if rules.is_reopening_breach(metrics, conversation):
                if not reopened_flag:
                    reopened_flag = True
                    reopened.append(conversation_id)
                    self.store.update(
                        conversation_id,
                        {"state": vocab.STATE_REOPENED, "unsnoozed_at": rules.stamp(now)},
                        actor=actor,
                        source=source,
                    )
                    transitions.append(
                        {
                            "conversation_id": conversation_id,
                            "type": vocab.ACTIVITY_SLA_REOPENED,
                            "at": rules.stamp(now),
                        }
                    )

            for entry in metrics:
                before = previous.get(entry["metric"])
                if entry["state"] == vocab.METRIC_BREACHED and before != vocab.METRIC_BREACHED:
                    transitions.append(
                        {
                            "conversation_id": conversation_id,
                            "type": vocab.ACTIVITY_SLA_BREACHED,
                            "metric": entry["metric"],
                            "at": rules.stamp(now),
                        }
                    )
                elif entry["state"] == vocab.METRIC_MET and before != vocab.METRIC_MET:
                    transitions.append(
                        {
                            "conversation_id": conversation_id,
                            "type": vocab.ACTIVITY_SLA_MET,
                            "metric": entry["metric"],
                            "at": rules.stamp(now),
                        }
                    )

            state = rules.timer_state(metrics)
            nearest = rules.nearest_target(metrics)
            self.store.update(
                row["id"],
                {
                    "metrics": metrics,
                    "state": state,
                    "badge": nearest["badge"] if nearest else None,
                    "nearest": nearest,
                    "reopened_after_breach": reopened_flag,
                    "checked_at": rules.stamp(now),
                },
                actor=actor,
                source=source,
            )

        if transitions:
            self._write_transitions(room_id, transitions, source=source, actor=actor)

        counts = self._state_counts(room_id)
        return {
            "room_id": room_id,
            "checked": len(active),
            "checked_at": rules.stamp(now),
            "counts": counts,
            "reopened": reopened,
            "transitions": transitions,
        }

    def _write_transitions(
        self,
        room_id: str,
        transitions: Sequence[Mapping[str, Any]],
        *,
        source: str,
        actor: str | None,
    ) -> None:
        for transition in transitions:
            self.store.create(
                vocab.ACTIVITY,
                {
                    vocab.ROOM_REF: room_id,
                    "conversation_id": transition.get("conversation_id"),
                    "type": transition.get("type"),
                    "metric": transition.get("metric"),
                    "at": transition.get("at"),
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )

    def _state_counts(self, room_id: str) -> dict[str, int]:
        counts = {state: 0 for state in vocab.TIMER_STATES}
        now = self._moment()
        for row in self.store.find(vocab.TIMERS, {vocab.ROOM_REF: room_id}, limit=1000):
            data = dict(row.get("data") or {})
            if not data.get("active", True):
                continue
            view = self._timer_view_from(row, now)
            counts[view["state"]] = counts.get(view["state"], 0) + 1
        return counts

    # -- the inbox and the report --------------------------------------------- #

    def inbox(self, room_id: str, *, sort: str | None = None) -> dict[str, Any]:
        """Every conversation with its SLA badge and nearest target.

        The "Next SLA target" sort puts a breached conversation first, then the
        conversation with the least time left. A conversation with no SLA sorts last.
        """

        now = self._moment()
        conversations = self.store.find(vocab.CONVERSATIONS, {vocab.ROOM_REF: room_id}, limit=500)
        rows: list[dict[str, Any]] = []
        for record in conversations:
            data = dict(record.get("data") or {})
            timer_record = self._active_timer_record(record["id"])
            view = self._timer_view_from(timer_record, now) if timer_record else None
            nearest = view["nearest"] if view else None
            rows.append(
                {
                    "conversation_id": record["id"],
                    "subject": data.get("subject"),
                    "inbox": data.get("inbox"),
                    "state": data.get("state"),
                    "sla": view["policy_name"] if view else None,
                    "timer_state": view["state"] if view else None,
                    "badge": nearest["badge"] if nearest else None,
                    "badge_label": vocab.BADGE_LABELS[nearest["badge"]] if nearest else None,
                    "nearest": nearest,
                    "next_target_seconds": (0 - nearest["seconds"])
                    if nearest and nearest["breached"]
                    else (nearest["seconds"] if nearest else None),
                }
            )

        if sort == "next_target":
            rows.sort(
                key=lambda row: (
                    row["next_target_seconds"] is None,
                    row["next_target_seconds"] if row["next_target_seconds"] is not None else 0,
                )
            )
        return {
            "room_id": room_id,
            "conversations": rows,
            "count": len(rows),
            "sort": sort or "default",
            "badges": list(vocab.BADGES),
            "badge_labels": dict(vocab.BADGE_LABELS),
        }

    def report(
        self, room_id: str, *, policy_id: str | None = None, team: str | None = None
    ) -> dict[str, Any]:
        """Hit, miss and fixed rates per SLA, over the room's timers.

        The rates are computed by rules.classify_outcome over each timer's persisted
        state, so the report and the badge cannot disagree about what a breach is. A
        metric still running is not counted, because it has no outcome yet.
        """

        now = self._moment()
        rows = self.store.find(vocab.TIMERS, {vocab.ROOM_REF: room_id}, limit=1000)
        by_sla: dict[str, dict[str, Any]] = {}
        totals = {outcome: 0 for outcome in vocab.OUTCOMES}
        per_metric: dict[str, dict[str, int]] = {
            metric: {o: 0 for o in vocab.OUTCOMES} for metric in vocab.METRIC_KINDS
        }

        for row in rows:
            data = dict(row.get("data") or {})
            if policy_id and data.get("policy_id") != policy_id:
                continue
            if team and team not in (data.get("teams") or []):
                continue
            view = self._timer_view_from(row, now)
            name = view["policy_name"] or "Unnamed SLA"
            bucket = by_sla.setdefault(
                name,
                {
                    "sla": name,
                    "policy_id": view["policy_id"],
                    "timers": 0,
                    "outcomes": {outcome: 0 for outcome in vocab.OUTCOMES},
                },
            )
            bucket["timers"] += 1
            for metric, outcome in view["outcomes"].items():
                if outcome is None:
                    continue
                bucket["outcomes"][outcome] += 1
                totals[outcome] += 1
                per_metric.setdefault(metric, {o: 0 for o in vocab.OUTCOMES})[outcome] += 1

        def _rates(outcomes: Mapping[str, int]) -> dict[str, float]:
            decided = sum(outcomes.values())
            return {
                outcome: (round(count / decided, 4) if decided else 0.0)
                for outcome, count in outcomes.items()
            }

        department = []
        for bucket in by_sla.values():
            bucket["rates"] = _rates(bucket["outcomes"])
            department.append(bucket)
        department.sort(key=lambda bucket: bucket["sla"])

        return {
            "room_id": room_id,
            "policy_id": policy_id,
            "team": team,
            "slas": department,
            "totals": totals,
            "rates": _rates(totals),
            "per_metric": {metric: _rates(counts) for metric, counts in per_metric.items()},
            "per_metric_counts": per_metric,
            "outcomes": list(vocab.OUTCOMES),
            "outcome_labels": dict(vocab.OUTCOME_LABELS),
            "count": len(department),
        }

    def summary(self, room_id: str) -> dict[str, Any]:
        counts = self._state_counts(room_id)
        policies = self.store.find(vocab.POLICIES, {vocab.ROOM_REF: room_id}, limit=500)
        timers = [
            row
            for row in self.store.find(vocab.TIMERS, {vocab.ROOM_REF: room_id}, limit=1000)
            if (row.get("data") or {}).get("active", True)
        ]
        activity = self.store.find(vocab.ACTIVITY, {vocab.ROOM_REF: room_id}, limit=1000)
        return {
            "room_id": room_id,
            "policies": len(policies),
            "live_policies": sum(1 for row in policies if (row.get("data") or {}).get("live")),
            "active_timers": len(timers),
            "timers_by_state": counts,
            "state_labels": dict(vocab.TIMER_STATE_LABELS),
            "activity": len(activity),
            "office_hours": self.office_hours(room_id),
        }

    def activity(self, room_id: str, conversation_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.ACTIVITY, {vocab.ROOM_REF: room_id}, limit=1000)
        entries = [dict(row.get("data") or {}) | {"id": row.get("id")} for row in rows]
        if conversation_id:
            entries = [
                entry for entry in entries if entry.get("conversation_id") == conversation_id
            ]
        return sorted(entries, key=lambda entry: str(entry.get("at") or ""))
