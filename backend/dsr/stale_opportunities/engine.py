"""The sweep engine: the only module in this package that reads or writes.

Every write goes through :class:`~dsr.store.RecordStore` and its
:class:`~dsr.db.audited.AuditedDatabase`. An escalation is a sequence of audited
writes - an opportunity update, then a task written through WF-116's engine,
then a notification and an escalation row - because the audited wrapper's
transaction cannot nest the task engine's own. A step that fails is recorded on
the run as failed rather than reported as committed.

The clock is injected. Production passes the wall clock; a test passes a frozen
function, so no test depends on today's date.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from dsr.seller_tasks.engine import SellerTaskEngine
from dsr.stale_opportunities import rules, vocabulary as v
from dsr.stale_opportunities.errors import SweepConflict, SweepNotFound
from dsr.store import RecordStore

Clock = Callable[[], datetime]


def _wall_clock() -> datetime:
    return datetime.now(timezone.utc)


#: The words a page shows for each skip reason. A colour is never the only
#: signal, so every reason carries a label.
REASON_LABELS: dict[str, str] = {
    v.REASON_MATCHED: "Matched",
    v.REASON_NO_EVIDENCE: "No activity evidence",
    v.REASON_BELOW_THRESHOLD: "Below the threshold",
    v.REASON_STAGE_FILTERED: "Filtered by stage",
    v.REASON_VALUE_FILTERED: "Filtered by value band",
    v.REASON_ALREADY_ESCALATED: "Already escalated",
}


class StaleOpportunitySweepEngine:
    """Sweeps, the opportunities they read, and the runs they leave behind."""

    def __init__(
        self,
        store: RecordStore,
        *,
        now: Clock | None = None,
        task_engine: SellerTaskEngine | None = None,
    ) -> None:
        self.store = store
        self._now = now or _wall_clock
        # The owner task is WF-116's, written through that workflow's engine so
        # this package does not re-declare a task rule.
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

    # -- sweeps: reads ------------------------------------------------------ #

    def require_sweep(self, sweep_id: str) -> dict[str, Any]:
        record = self.store.get(sweep_id)
        if record is None or record["collection"] != v.SWEEP_COLLECTION:
            raise SweepNotFound(sweep_id, collection="sweep")
        return record

    def get_sweep(self, sweep_id: str) -> dict[str, Any]:
        return self._sweep_view(self.require_sweep(sweep_id))

    def list_sweeps(self, *, room_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        records = self.store.list(v.SWEEP_COLLECTION, room_id=room_id, limit=limit)
        return [self._sweep_view(record) for record in records]

    # -- sweeps: writes ----------------------------------------------------- #

    def create_sweep(
        self,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        fields = rules.normalise_sweep_payload(payload)
        room_id = fields.pop("room_id")
        now_ms = self.now_ms()
        data = {**fields, "created_at_ms": now_ms, "updated_at_ms": now_ms}
        record = self.store.create(
            v.SWEEP_COLLECTION, data, room_id=room_id, actor=actor, source=source
        )
        return self._sweep_view(record)

    def update_sweep(
        self,
        sweep_id: str,
        patch: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        record = self.require_sweep(sweep_id)
        merged: dict[str, Any] = {**record["data"], **dict(patch or {})}
        merged["room_id"] = patch.get("room_id", record.get("room_id"))
        fields = rules.normalise_sweep_payload(merged)
        fields.pop("room_id")
        fields["created_at_ms"] = record["data"].get("created_at_ms")
        fields["updated_at_ms"] = self.now_ms()
        updated = self.store.update(sweep_id, fields, actor=actor, source=source)
        return self._sweep_view(updated)

    def set_published(
        self,
        sweep_id: str,
        published: bool,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        return self.update_sweep(
            sweep_id, {"published": bool(published)}, source=source, actor=actor
        )

    def delete_sweep(
        self, sweep_id: str, *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        self.require_sweep(sweep_id)
        self.store.delete(sweep_id, actor=actor, source=source)
        return {"sweep_id": sweep_id, "deleted": True}

    # -- opportunities: the object the data flow evaluates ------------------ #

    def opportunities(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        """Every CRM opportunity row this build can see, normalised.

        Read from WF-039's collection by name, filtered to the object the
        researched bundle writes. Any other object in that collection is left
        alone.
        """

        records = self.store.find(
            v.OPPORTUNITY_COLLECTION, {"object": v.OPPORTUNITY_OBJECT}, limit=1000
        )
        if room_id:
            records = [record for record in records if record.get("room_id") == room_id]
        return [rules.opportunity_view(record) for record in records]

    def evaluate(self, sweep_id: str, *, at_ms: int | None = None) -> dict[str, Any]:
        """What the sweep would do right now, writing nothing."""

        sweep = self.get_sweep(sweep_id)
        moment = at_ms if at_ms is not None else self.now_ms()
        views = self.opportunities(room_id=sweep.get("room_id"))
        decisions = [rules.evaluate_opportunity(view, sweep, moment) for view in views]
        matched = [decision for decision in decisions if decision["matched"]]
        return {
            "sweep_id": sweep_id,
            "at_ms": moment,
            "at": rules.to_iso(moment),
            "scanned": len(views),
            "matched": len(matched),
            "decisions": decisions,
        }

    # -- the run ------------------------------------------------------------ #

    def sweep(
        self,
        sweep_id: str,
        *,
        source: str,
        actor: str | None = None,
        at_ms: int | None = None,
    ) -> dict[str, Any]:
        """Run one sweep: evaluate, then escalate every matched opportunity."""

        sweep = self.get_sweep(sweep_id)
        if not sweep.get("published"):
            raise SweepConflict(
                "sweep_not_published",
                "publish the sweep before running it",
                extra={"sweep_id": sweep_id},
            )
        moment = at_ms if at_ms is not None else self.now_ms()
        views = self.opportunities(room_id=sweep.get("room_id"))

        counts = {"scanned": len(views), "matched": 0, "escalated": 0, "skipped": 0, "failed": 0}
        results: list[dict[str, Any]] = []

        for view in views:
            decision = rules.evaluate_opportunity(view, sweep, moment)
            if not decision["matched"]:
                counts["skipped"] += 1
                results.append({**_compact(decision), "outcome": "skipped"})
                continue
            counts["matched"] += 1
            try:
                escalation = self._escalate(
                    view, sweep, decision, moment, source=source, actor=actor
                )
            except Exception as exc:  # one bad row must not abort the whole sweep
                counts["failed"] += 1
                results.append(
                    {**_compact(decision), "outcome": "failed", "error": _error_detail(exc)}
                )
                continue
            counts["escalated"] += 1
            results.append({**_compact(decision), "outcome": "escalated", **escalation})

        run_data = {
            "sweep_id": sweep_id,
            "room_id": sweep.get("room_id"),
            "at_ms": moment,
            "at": rules.to_iso(moment),
            "inactivity_basis": sweep.get("inactivity_basis"),
            "threshold_days": sweep.get("threshold_days"),
            "refire_control": sweep.get("refire_control"),
            "by_reason": _reason_counts(results),
            **counts,
            "results": results,
        }
        record = self.store.create(
            v.RUN_COLLECTION, run_data, room_id=sweep.get("room_id"), actor=actor, source=source
        )
        return self._run_view(record)

    def _escalate(
        self,
        view: Mapping[str, Any],
        sweep: Mapping[str, Any],
        decision: Mapping[str, Any],
        now_ms: int,
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """The three researched actions: update, task, notify - plus the marker.

        The marker is written last, on purpose. It is the researched
        Remove-from-Workflow control, so the opportunity must only be taken out
        of the sweep once the escalation's work is done. A task that fails leaves
        the opportunity eligible and the next run retries it; the run records the
        failure rather than a false success.
        """

        step = decision.get("step") or {"action": v.STEP_ESCALATE, "after_days": 0}
        action = step.get("action", v.STEP_ESCALATE)

        task = self.tasks.create(
            rules.task_payload(view, sweep, decision, now_ms), source=source, actor=actor
        )

        notification = self.store.create(
            v.NOTIFICATION_COLLECTION,
            {
                "sweep_id": sweep["id"],
                "opportunity_id": view["id"],
                "team": sweep.get("notify_team"),
                "recipients": [
                    recipient
                    for recipient in (view.get("owner"), sweep.get("assignee"))
                    if recipient
                ],
                "action": action,
                "message": rules.notification_message(view, sweep, decision),
                "task_id": task["id"],
                "assignee": task.get("assignee"),
                "at_ms": now_ms,
                "at": rules.to_iso(now_ms),
            },
            room_id=sweep.get("room_id"),
            actor=actor,
            source=source,
        )

        patch = rules.escalation_patch(view, sweep, step, now_ms)
        self.store.update(view["id"], patch, actor=actor, source=source)

        escalation = self.store.create(
            v.ESCALATION_COLLECTION,
            {
                "sweep_id": sweep["id"],
                "opportunity_id": view["id"],
                "step": action,
                "after_days": step.get("after_days"),
                "idle_days": decision.get("idle_days"),
                "reference_ms": decision.get("reference_ms"),
                "refire_control": sweep.get("refire_control"),
                "task_id": task["id"],
                "notification_id": notification["id"],
                "at_ms": now_ms,
                "at": rules.to_iso(now_ms),
            },
            room_id=sweep.get("room_id"),
            actor=actor,
            source=source,
        )
        return {
            "step": action,
            "task_id": task["id"],
            "assignee": task.get("assignee"),
            "notification_id": notification["id"],
            "escalation_id": escalation["id"],
        }

    # -- runs and their children: reads ------------------------------------- #

    def list_runs(
        self,
        *,
        sweep_id: str | None = None,
        room_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if sweep_id:
            records = self.store.find(v.RUN_COLLECTION, {"sweep_id": sweep_id}, limit=limit)
        else:
            records = self.store.list(v.RUN_COLLECTION, room_id=room_id, limit=limit)
        if room_id and sweep_id:
            records = [record for record in records if record.get("room_id") == room_id]
        return [self._run_view(record) for record in records]

    def get_run(self, run_id: str) -> dict[str, Any]:
        record = self.store.get(run_id)
        if record is None or record["collection"] != v.RUN_COLLECTION:
            raise SweepNotFound(run_id, collection="run")
        return self._run_view(record)

    def list_escalations(
        self, *, sweep_id: str | None = None, room_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        if sweep_id:
            records = self.store.find(v.ESCALATION_COLLECTION, {"sweep_id": sweep_id}, limit=limit)
        else:
            records = self.store.list(v.ESCALATION_COLLECTION, room_id=room_id, limit=limit)
        if room_id and sweep_id:
            records = [record for record in records if record.get("room_id") == room_id]
        return [self._simple_view(record) for record in records]

    def list_notifications(
        self, *, sweep_id: str | None = None, room_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        if sweep_id:
            records = self.store.find(
                v.NOTIFICATION_COLLECTION, {"sweep_id": sweep_id}, limit=limit
            )
        else:
            records = self.store.list(v.NOTIFICATION_COLLECTION, room_id=room_id, limit=limit)
        if room_id and sweep_id:
            records = [record for record in records if record.get("room_id") == room_id]
        return [self._simple_view(record) for record in records]

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        sweeps = self.list_sweeps(room_id=room_id, limit=1000)
        opportunities = self.opportunities(room_id=room_id)
        runs = self.list_runs(room_id=room_id, limit=1000)
        escalations = self.list_escalations(room_id=room_id, limit=1000)
        by_reason: dict[str, int] = {}
        for decision in self._all_reasons(opportunities, sweeps):
            by_reason[decision] = by_reason.get(decision, 0) + 1
        return {
            "sweeps": len(sweeps),
            "published": len([sweep for sweep in sweeps if sweep.get("published")]),
            "opportunities": len(opportunities),
            "runs": len(runs),
            "escalated_total": sum(run.get("escalated", 0) for run in runs),
            "escalations": len(escalations),
            "notifications": self.store.count_where(v.NOTIFICATION_COLLECTION, {}),
            "by_reason": by_reason,
        }

    # -- internals ---------------------------------------------------------- #

    def _all_reasons(
        self, opportunities: list[dict[str, Any]], sweeps: list[dict[str, Any]]
    ) -> list[str]:
        """Evaluate every opportunity against every published sweep, for counts.

        A read-only helper so the headline counts cannot disagree with what a
        run would decide.
        """

        moment = self.now_ms()
        reasons: list[str] = []
        for sweep in sweeps:
            if not sweep.get("published"):
                continue
            for view in opportunities:
                reasons.append(rules.evaluate_opportunity(view, sweep, moment)["reason"])
        return reasons

    def _sweep_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record["data"])
        return {
            "id": record["id"],
            "collection": record["collection"],
            "room_id": record.get("room_id"),
            "revision": record.get("revision"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "deleted_at": record.get("deleted_at"),
            **data,
            "cadence_steps": len(data.get("cadence") or []),
        }

    def _run_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": record["id"],
            "collection": record["collection"],
            "room_id": record.get("room_id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            **dict(record["data"]),
        }

    def _simple_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": record["id"],
            "collection": record["collection"],
            "room_id": record.get("room_id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            **dict(record["data"]),
        }


def _compact(decision: Mapping[str, Any]) -> dict[str, Any]:
    """One decision, without the raw opportunity payload it read."""

    return {
        "opportunity_id": decision.get("opportunity_id"),
        "name": decision.get("name"),
        "stage": decision.get("stage"),
        "owner": decision.get("owner"),
        "value": decision.get("value"),
        "reference_ms": decision.get("reference_ms"),
        "reference_field": decision.get("reference_field"),
        "idle_days": decision.get("idle_days"),
        "reason": decision.get("reason"),
        "matched": decision.get("matched"),
    }


def _reason_counts(results: list[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        reason = str(result.get("reason"))
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _error_detail(exc: Exception) -> dict[str, Any]:
    as_dict = getattr(exc, "as_dict", None)
    if callable(as_dict):
        return as_dict()
    return {"code": type(exc).__name__, "detail": str(exc)}


__all__ = ["REASON_LABELS", "StaleOpportunitySweepEngine"]
