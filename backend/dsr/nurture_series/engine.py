"""The nurture-series state machine over the audited store.

The engine holds a store handle and a clock and nothing else, so a test can build one directly
and move the clock by hand. Every writing method takes ``source`` as a required keyword, and the
HTTP layer passes a string built from ``router.prefix``, so every audit row names a route the
host actually mounted. A hardcoded URL inside a domain method is a defect.

The walk
--------

A series is an ordered list of rule, content, wait and tag blocks. A run enters at
``start_cursor`` - past the entry rule if the first block is one - and :meth:`advance` walks it
until it hits a hold, a content block that cannot deliver, or the end.

* **rule** blocks gate progression. A satisfied branch advances; ``once`` exits the path
  immediately when it is not satisfied; ``for`` holds the run until its window elapses.
* **content** blocks dispatch to a channel. A block that can deliver advances; one that cannot
  and has a fallback holds while it retries, and its fallback fires when the hold resolves.
* **wait** blocks hold for their duration.
* **tag** blocks add a tag and advance.

**This engine sends nothing.** Every dispatch row carries ``sent_by_this_product: False`` and
``channel_send_is_recorded: True``. The channel decision and the fallback are real, recorded
state; the send is a stand-in, the same standard :mod:`dsr.conversation_chase` and
:mod:`dsr.automation_engagement` follow.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from dsr.nurture_series import rules, vocabulary as vocab
from dsr.nurture_series.errors import RunNotFound, SeriesNotFound, SeriesRefused
from dsr.store import RecordStore


class NurtureSeriesEngine:
    """Rule, content, wait and tag blocks with channel fallback, over the audited store."""

    def __init__(self, store: RecordStore, *, now: Any = None) -> None:
        self.store = store
        self._clock = now or (lambda: datetime.now(timezone.utc))

    # -- the clock ---------------------------------------------------------- #

    def _now(self, payload: Any = None) -> datetime:
        """The clock, movable by a caller that supplies ``now`` in its payload.

        Nothing in this workflow reads a wall clock but this method. A test pins an instant by
        passing it, and the seeder derives every instant from ``context["now"]``.
        """

        if isinstance(payload, Mapping):
            moment = rules.parse_instant(payload.get("now"))
            if moment is not None:
                return moment
        return self._clock()

    # -- lookups ------------------------------------------------------------ #

    def _require_series(self, series_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        record = self.store.get(series_id)
        if record is None or record.get("collection") != vocab.SERIES_COLLECTION:
            raise SeriesNotFound(f"no series {series_id!r}")
        if room_id is not None and record.get("room_id") != room_id:
            raise SeriesNotFound(f"no series {series_id!r} in room {room_id!r}")
        return record

    def _require_run(self, run_id: str) -> dict[str, Any]:
        record = self.store.get(run_id)
        if record is None or record.get("collection") != vocab.RUN_COLLECTION:
            raise RunNotFound(f"no run {run_id!r}")
        return record

    def _series_for_run(self, run: Mapping[str, Any]) -> dict[str, Any]:
        series = self.store.get(str(run["data"].get("series_id") or ""))
        if series is None or series.get("collection") != vocab.SERIES_COLLECTION:
            raise SeriesNotFound(f"no series for run {run['id']!r}")
        return series

    def _blocks_for_run(self, run: Mapping[str, Any]) -> list[dict[str, Any]]:
        return rules.blocks_for(self._series_for_run(run)["data"])

    # -- small writers ------------------------------------------------------ #

    def _activity(
        self,
        run: Mapping[str, Any],
        code: str,
        detail: Mapping[str, Any],
        now: datetime,
        *,
        source: str,
        actor: str,
    ) -> dict[str, Any]:
        """One event on a run, so a page can show the walk in order."""

        return self.store.create(
            vocab.ACTIVITY_COLLECTION,
            {
                "run_id": run["id"],
                "series_id": run["data"].get("series_id"),
                "contact_id": run["data"].get("contact_id"),
                "code": code,
                "detail": dict(detail),
                "at": rules.to_iso(now),
            },
            room_id=run.get("room_id"),
            actor=actor,
            source=source,
        )

    def _dispatch(
        self,
        run: Mapping[str, Any],
        block: Mapping[str, Any],
        channel: str,
        *,
        state: str,
        is_fallback: bool,
        reason: str | None,
        retried: float,
        now: datetime,
        source: str,
        actor: str,
    ) -> dict[str, Any]:
        """One channel attempt, recorded. No provider is called."""

        return self.store.create(
            vocab.DISPATCH_COLLECTION,
            {
                "run_id": run["id"],
                "series_id": run["data"].get("series_id"),
                "contact_id": run["data"].get("contact_id"),
                "block_id": block.get("id"),
                "channel": channel,
                "content_type": block.get("content_type"),
                "state": state,
                "is_fallback": is_fallback,
                "reason": reason,
                "send_window_seconds": block.get("send_window_seconds"),
                "retried_for_seconds": retried,
                "sent_by_this_product": False,
                "channel_send_is_recorded": True,
                "at": rules.to_iso(now),
            },
            room_id=run.get("room_id"),
            actor=actor,
            source=source,
        )

    def _save(
        self, run: dict[str, Any], patch: Mapping[str, Any], *, source: str, actor: str
    ) -> dict[str, Any]:
        """Merge a patch onto the run and keep the in-memory record in step."""

        run["data"].update(dict(patch))
        updated = self.store.update(run["id"], run["data"], actor=actor, source=source)
        run["data"] = updated["data"]
        return run

    def _save_series(
        self, series: dict[str, Any], patch: Mapping[str, Any], *, source: str, actor: str
    ) -> dict[str, Any]:
        series["data"].update(dict(patch))
        updated = self.store.update(series["id"], series["data"], actor=actor, source=source)
        series["data"] = updated["data"]
        return series

    def _child(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return {"id": record["id"], **dict(record.get("data") or {})}

    def _events(self, run_id: str) -> list[str]:
        return [
            str(row["data"].get("event"))
            for row in self.store.find(vocab.ENGAGEMENT_COLLECTION, {"run_id": run_id}, limit=500)
        ]

    # ------------------------------------------------------------------ #
    # Series
    # ------------------------------------------------------------------ #

    def create_series(
        self, room_id: str, payload: Mapping[str, Any], *, source: str, actor: str
    ) -> dict[str, Any]:
        """Register a series. It is a draft until it is set live (step 8)."""

        validated = rules.validate_series(payload)
        now = self._now(payload)
        record = self.store.create(
            vocab.SERIES_COLLECTION,
            {
                "name": validated["name"],
                "state": vocab.STATE_DRAFT,
                "blocks": validated["blocks"],
                "entry_rule": validated["entry_rule"],
                "start_cursor": validated["start_cursor"],
                "audience_mechanism": validated["audience_mechanism"],
                "template": str(payload.get("template") or ""),
                "created_at": rules.to_iso(now),
                "went_live_at": None,
                "sent_by_this_product": False,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self.series_view(record["id"])

    def list_series(self, room_id: str, *, state: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.list(vocab.SERIES_COLLECTION, room_id=room_id, limit=500)
        if state:
            wanted = str(state).strip().lower()
            if wanted not in vocab.SERIES_STATES:
                raise SeriesRefused(
                    f"series state {state!r} is not one of {', '.join(vocab.SERIES_STATES)}",
                    code="unknown_series_state",
                    errors={"state": "not a published series state"},
                )
            rows = [row for row in rows if row["data"].get("state") == wanted]
        return [self.series_view(row["id"]) for row in rows]

    def series_view(self, series_id: str) -> dict[str, Any]:
        series = self._require_series(series_id)
        data = series["data"]
        runs = self.store.find(vocab.RUN_COLLECTION, {"series_id": series_id}, limit=2000)
        by_state = {state: 0 for state in vocab.RUN_STATES}
        for run in runs:
            state = run["data"].get("state")
            if state in by_state:
                by_state[state] += 1
        return {
            "id": series["id"],
            "room_id": series.get("room_id"),
            "name": data.get("name"),
            "state": data.get("state"),
            "template": data.get("template"),
            "blocks": rules.blocks_for(data),
            "entry_rule": data.get("entry_rule"),
            "start_cursor": data.get("start_cursor", 0),
            "audience_mechanism": data.get("audience_mechanism"),
            "created_at": data.get("created_at"),
            "went_live_at": data.get("went_live_at"),
            "runs": len(runs),
            "runs_by_state": by_state,
            "sent_by_this_product": False,
        }

    def update_series(
        self, series_id: str, payload: Mapping[str, Any], *, source: str, actor: str
    ) -> dict[str, Any]:
        """Edit a series. Every field is revalidated on the way in."""

        series = self._require_series(series_id)
        patch: dict[str, Any] = {}
        if "name" in payload or "blocks" in payload:
            merged = {
                "name": payload.get("name", series["data"].get("name")),
                "blocks": payload.get("blocks", series["data"].get("blocks")),
            }
            validated = rules.validate_series(merged)
            patch.update(
                {
                    "name": validated["name"],
                    "blocks": validated["blocks"],
                    "entry_rule": validated["entry_rule"],
                    "start_cursor": validated["start_cursor"],
                }
            )
        if "template" in payload:
            patch["template"] = str(payload.get("template") or "")
        if payload.get("state") is not None:
            target = str(payload["state"]).strip().lower()
            if target not in vocab.SERIES_STATES:
                raise SeriesRefused(
                    f"series state {target!r} is not one of {', '.join(vocab.SERIES_STATES)}",
                    code="unknown_series_state",
                    errors={"state": "not a published series state"},
                )
            if target == vocab.STATE_LIVE and series["data"].get("state") != vocab.STATE_LIVE:
                return self.go_live(series_id, source=source, actor=actor)
            patch["state"] = target
        self._save_series(series, patch, source=source, actor=actor)
        return self.series_view(series_id)

    def go_live(self, series_id: str, *, source: str, actor: str) -> dict[str, Any]:
        """Step 8: "Click Set series live". A draft fires nothing."""

        series = self._require_series(series_id)
        if series["data"].get("state") == vocab.STATE_LIVE:
            raise SeriesRefused(
                "the series is already live",
                code="series_already_live",
                status=409,
                errors={"state": "already live"},
            )
        self._save_series(
            series,
            {"state": vocab.STATE_LIVE, "went_live_at": rules.to_iso(self._now())},
            source=source,
            actor=actor,
        )
        return self.series_view(series_id)

    def delete_series(self, series_id: str, *, source: str, actor: str) -> dict[str, Any]:
        """Soft-delete a series. Its runs survive, and keep the walk they recorded."""

        self._require_series(series_id)
        result = self.store.delete(series_id, actor=actor, source=source)
        return {"id": series_id, "deleted": True, "hard": bool(result.get("hard"))}

    def preview(self, room_id: str, series_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Step 2's audience preview: who will enter, and why the others will not.

        Read-only. The filters are the same evaluator the entry gate uses, so the preview
        cannot drift from what ``evaluate`` will do.
        """

        series = self._require_series(series_id, room_id=room_id)
        contacts = rules.normalise_contacts(payload.get("audience"))
        entry = series["data"].get("entry_rule") or {}
        results: list[dict[str, Any]] = []
        entered = 0
        for contact in contacts:
            passed, matched, total = rules.entry_matches(series["data"], contact)
            reasons: list[str] = []
            if not passed:
                for rule in entry.get("filters") or []:
                    ok, reason = rules.matches_filter(contact, rule)
                    if not ok:
                        reasons.append(reason)
            else:
                entered += 1
            results.append(
                {
                    "contact_id": contact["contact_id"],
                    "entered": passed,
                    "matched": matched,
                    "total": total,
                    "reasons": reasons,
                }
            )
        return {
            "room_id": room_id,
            "series_id": series_id,
            "entry_rule": entry,
            "audience_size": len(contacts),
            "entered": entered,
            "excluded": len(contacts) - entered,
            "contacts": results,
            "poll": {"interval_seconds": vocab.POLL_INTERVAL_SECONDS, "quote": vocab.POLL_QUOTE},
        }

    def export_series(self, series_id: str) -> dict[str, Any]:
        """The EU Data Act export, exposing the four fields the research names."""

        series = self._require_series(series_id)
        data = series["data"]
        entry = data.get("entry_rule") or {}
        return {
            "series_id": series["id"],
            "room_id": series.get("room_id"),
            "exported_at": rules.to_iso(self._now()),
            "targeting": {
                "entry_rule": entry,
                "filters": [dict(rule) for rule in entry.get("filters") or []],
                "match": entry.get("match"),
            },
            "snapshot": rules.describe_snapshot(data),
            "attributes": rules.attribute_paths_used(data),
            "embedded_rules": rules.embedded_rules(data),
            "fields": list(vocab.EXPORT_FIELDS),
            "quote": vocab.EXPORT_QUOTE,
            "obligation": "EU Data Act",
        }

    # ------------------------------------------------------------------ #
    # Entry: the poll
    # ------------------------------------------------------------------ #

    def evaluate(
        self, room_id: str, payload: Mapping[str, Any], *, source: str, actor: str
    ) -> dict[str, Any]:
        """One poll: gate entry for each live series, and report what is due next.

        The research's poll is "Series checks every 30 minutes whether a customer matches each
        rule, message, wait, or tag block." Nothing fires until somebody calls this, and the
        response reports ``due`` - the count the next call would resolve - so the gap is visible
        rather than silent.
        """

        now = self._now(payload)
        audience = rules.normalise_contacts(payload.get("audience"))
        series_rows = self.store.list(vocab.SERIES_COLLECTION, room_id=room_id, limit=500)
        live = [row for row in series_rows if row["data"].get("state") == vocab.STATE_LIVE]

        fired: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        if not live:
            skipped.append(
                {
                    "reason": "no_live_series",
                    "room_id": room_id,
                    "is_specification_rule": False,
                }
            )
        for series in live:
            for contact in audience:
                passed, matched, total = rules.entry_matches(series["data"], contact)
                if not passed:
                    skipped.append(
                        {
                            "reason": "entry_filters_unmatched",
                            "series_id": series["id"],
                            "contact_id": contact["contact_id"],
                            "matched": matched,
                            "total": total,
                            "is_specification_rule": False,
                        }
                    )
                    continue
                existing = self.store.find(
                    vocab.RUN_COLLECTION,
                    {"series_id": series["id"], "contact_id": contact["contact_id"]},
                    limit=1,
                )
                if existing:
                    skipped.append(
                        {
                            "reason": "already_entered",
                            "series_id": series["id"],
                            "contact_id": contact["contact_id"],
                            "is_specification_rule": True,
                        }
                    )
                    continue
                run = self._create_run(series, contact, now, source=source, actor=actor)
                fired.append(run)

        return {
            "room_id": room_id,
            "series_considered": len(live),
            "audience_size": len(audience),
            "fired": fired,
            "fired_count": len(fired),
            "skipped": skipped,
            "skipped_count": len(skipped),
            "due": self.due_count(room_id, now=now),
            "poll": {"interval_seconds": vocab.POLL_INTERVAL_SECONDS, "quote": vocab.POLL_QUOTE},
        }

    def _create_run(
        self,
        series: Mapping[str, Any],
        contact: Mapping[str, Any],
        now: datetime,
        *,
        source: str,
        actor: str,
    ) -> dict[str, Any]:
        start_cursor = int(series["data"].get("start_cursor", 0))
        record = self.store.create(
            vocab.RUN_COLLECTION,
            {
                "series_id": series["id"],
                "contact_id": contact["contact_id"],
                "contact": dict(contact),
                "cursor": start_cursor,
                "state": vocab.RUN_RUNNING,
                "tags": [],
                "entered_at": rules.to_iso(now),
                "resume_at": None,
                "waiting_kind": None,
                "waiting_block": None,
                "wait_started_at": None,
                "sent_by_this_product": False,
            },
            room_id=series.get("room_id"),
            actor=actor,
            source=source,
        )
        self._activity(
            record, "run_entered", {"start_cursor": start_cursor}, now, source=source, actor=actor
        )
        return self.run_view(record["id"])

    def due_count(self, room_id: str, *, now: datetime | None = None) -> int:
        """How many waiting runs the next poll or resolve could act on."""

        moment = now or self._now()
        count = 0
        for run in self.store.list(vocab.RUN_COLLECTION, room_id=room_id, limit=2000):
            if run["data"].get("state") != vocab.RUN_WAITING:
                continue
            resume_at = rules.parse_instant(run["data"].get("resume_at"))
            if resume_at is None or moment >= resume_at:
                count += 1
        return count

    # ------------------------------------------------------------------ #
    # Runs
    # ------------------------------------------------------------------ #

    def list_runs(
        self, room_id: str, *, series_id: str | None = None, state: str | None = None
    ) -> list[dict[str, Any]]:
        rows = self.store.list(vocab.RUN_COLLECTION, room_id=room_id, limit=2000)
        if series_id:
            rows = [row for row in rows if row["data"].get("series_id") == series_id]
        if state:
            wanted = str(state).strip().lower()
            if wanted not in vocab.RUN_STATES:
                raise SeriesRefused(
                    f"run state {state!r} is not one of {', '.join(vocab.RUN_STATES)}",
                    code="unknown_run_state",
                    errors={"state": "not a published run state"},
                )
            rows = [row for row in rows if row["data"].get("state") == wanted]
        return [self.run_view(row["id"]) for row in rows]

    def run_view(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        data = run["data"]
        blocks = self._blocks_for_run(run)
        cursor = int(data.get("cursor", 0))
        dispatches = [
            self._child(row)
            for row in self.store.find(vocab.DISPATCH_COLLECTION, {"run_id": run_id}, limit=200)
        ]
        dispatches.sort(key=lambda row: str(row.get("at") or ""))
        engagements = [
            self._child(row)
            for row in self.store.find(vocab.ENGAGEMENT_COLLECTION, {"run_id": run_id}, limit=200)
        ]
        engagements.sort(key=lambda row: str(row.get("at") or ""))
        activity = [
            self._child(row)
            for row in self.store.find(vocab.ACTIVITY_COLLECTION, {"run_id": run_id}, limit=500)
        ]
        activity.sort(key=lambda row: str(row.get("at") or ""))
        return {
            "id": run["id"],
            "room_id": run.get("room_id"),
            "series_id": data.get("series_id"),
            "contact_id": data.get("contact_id"),
            "contact": data.get("contact"),
            "state": data.get("state"),
            "cursor": cursor,
            "next_block": blocks[cursor] if 0 <= cursor < len(blocks) else None,
            "remaining_blocks": blocks[cursor:],
            "tags": list(data.get("tags") or []),
            "entered_at": data.get("entered_at"),
            "resume_at": data.get("resume_at"),
            "waiting_kind": data.get("waiting_kind"),
            "dispatches": dispatches,
            "dispatch_count": len(dispatches),
            "engagement": engagements,
            "activity": activity,
            "sent_by_this_product": False,
        }

    def advance(
        self, run_id: str, payload: Mapping[str, Any], *, source: str, actor: str
    ) -> dict[str, Any]:
        """Walk the run until it holds, exits, finishes, or holds on a retry window."""

        run = self._require_run(run_id)
        if run["data"].get("state") != vocab.RUN_RUNNING:
            raise SeriesRefused(
                f"run {run_id!r} is {run['data'].get('state')}, so it cannot be advanced",
                code="run_not_advancing",
                status=409,
                errors={"state": "not running"},
            )
        return self._walk(run, self._now(payload), source=source, actor=actor)

    def resolve(
        self, run_id: str, payload: Mapping[str, Any], *, source: str, actor: str
    ) -> dict[str, Any]:
        """Finish a hold: a wait, or a rule match window, or a send-window retry."""

        run = self._require_run(run_id)
        data = run["data"]
        if data.get("state") != vocab.RUN_WAITING:
            raise SeriesRefused(
                f"run {run_id!r} is not waiting, so there is nothing to resolve",
                code="run_not_waiting",
                status=409,
                errors={"state": "not waiting"},
            )
        now = self._now(payload)
        resume_at = rules.parse_instant(data.get("resume_at"))

        kind = data.get("waiting_kind")
        cursor = int(data.get("cursor", 0))
        blocks = self._blocks_for_run(run)

        if kind == "send_window":
            block = blocks[cursor]
            window = float(block.get("send_window_seconds") or vocab.DEFAULT_SEND_WINDOW_SECONDS)
            started = rules.parse_instant(data.get("wait_started_at"))
            elapsed = (now - started).total_seconds() if started else window
            decision = rules.decide_channel(block, data["contact"])
            if decision["can_deliver"]:
                # The buyer came online inside the window, so the block delivers rather than
                # falling back. That is the whole point of a retry window.
                self._dispatch(
                    run,
                    block,
                    decision["channel"],
                    state="delivered",
                    is_fallback=False,
                    reason=None,
                    retried=min(elapsed, window),
                    now=now,
                    source=source,
                    actor=actor,
                )
                self._activity(
                    run,
                    "content_dispatched",
                    {"block_id": block.get("id"), "channel": decision["channel"]},
                    now,
                    source=source,
                    actor=actor,
                )
                self._save(run, self._resume_patch(cursor + 1), source=source, actor=actor)
                return self._walk(self._require_run(run_id), now, source=source, actor=actor)
            if resume_at is not None and now < resume_at:
                raise SeriesRefused(
                    f"run {run_id!r} is still retrying until {rules.to_iso(resume_at)}",
                    code="wait_not_elapsed",
                    status=409,
                    errors={"resume_at": "not elapsed"},
                )
            reason = rules.fallback_reason(bool(data["contact"].get("online")), elapsed, window)
            self._dispatch(
                run,
                block,
                str(block.get("fallback_channel") or vocab.FALLBACK_CHANNEL_DEFAULT),
                state="fallback",
                is_fallback=True,
                reason=reason,
                retried=min(elapsed, window),
                now=now,
                source=source,
                actor=actor,
            )
            self._activity(
                run,
                "fallback_sent",
                {
                    "block_id": block.get("id"),
                    "channel": block.get("fallback_channel"),
                    "reason": reason,
                },
                now,
                source=source,
                actor=actor,
            )
            cursor += 1
            self._save(
                run,
                self._resume_patch(cursor),
                source=source,
                actor=actor,
            )
            return self._walk(self._require_run(run_id), now, source=source, actor=actor)

        if kind == "rule":
            block = blocks[cursor]
            ok, reason = rules.rule_satisfied(data["contact"], self._events(run_id), block)
            if not ok and resume_at is not None and now < resume_at:
                raise SeriesRefused(
                    f"run {run_id!r} is still trying to match until {rules.to_iso(resume_at)}",
                    code="wait_not_elapsed",
                    status=409,
                    errors={"resume_at": "not elapsed"},
                )
            if not ok:
                return self._finish(
                    run,
                    vocab.RUN_EXITED,
                    cursor,
                    now,
                    source=source,
                    actor=actor,
                    exit_reason="no_match",
                )
            self._activity(
                run,
                "rule_matched",
                {"block_id": block.get("id"), "reason": reason},
                now,
                source=source,
                actor=actor,
            )
            cursor += 1
            self._save(run, self._resume_patch(cursor), source=source, actor=actor)
            return self._walk(self._require_run(run_id), now, source=source, actor=actor)

        # kind == "wait"
        if resume_at is not None and now < resume_at:
            raise SeriesRefused(
                f"run {run_id!r} is waiting until {rules.to_iso(resume_at)}",
                code="wait_not_elapsed",
                status=409,
                errors={"resume_at": "not elapsed"},
            )
        self._activity(
            run,
            "wait_resolved",
            {"block_id": blocks[cursor].get("id")},
            now,
            source=source,
            actor=actor,
        )
        cursor += 1
        self._save(run, self._resume_patch(cursor), source=source, actor=actor)
        return self._walk(self._require_run(run_id), now, source=source, actor=actor)

    def record_event(
        self, run_id: str, payload: Mapping[str, Any], *, source: str, actor: str
    ) -> dict[str, Any]:
        """Record an engagement event against the buyer the run belongs to."""

        run = self._require_run(run_id)
        event = vocab.require_engagement_event(payload.get("event"))
        now = self._now(payload)
        row = self.store.create(
            vocab.ENGAGEMENT_COLLECTION,
            {
                "run_id": run_id,
                "series_id": run["data"].get("series_id"),
                "contact_id": run["data"].get("contact_id"),
                "event": event,
                "target": "contact",
                "at": rules.to_iso(now),
            },
            room_id=run.get("room_id"),
            actor=actor,
            source=source,
        )
        self._activity(
            run, "engagement_recorded", {"event": event}, now, source=source, actor=actor
        )
        if event in ("series_completion", "series_exit"):
            state = vocab.RUN_FINISHED if event == "series_completion" else vocab.RUN_EXITED
            if run["data"].get("state") in (vocab.RUN_RUNNING, vocab.RUN_WAITING):
                self._finish(
                    run,
                    state,
                    int(run["data"].get("cursor", 0)),
                    now,
                    source=source,
                    actor=actor,
                    exit_reason=event,
                )
        return self._child(row)

    # -- the walk ----------------------------------------------------------- #

    def _resume_patch(self, cursor: int) -> dict[str, Any]:
        return {
            "cursor": cursor,
            "state": vocab.RUN_RUNNING,
            "resume_at": None,
            "waiting_kind": None,
            "waiting_block": None,
            "wait_started_at": None,
        }

    def _walk(
        self, run: dict[str, Any], now: datetime, *, source: str, actor: str
    ) -> dict[str, Any]:
        blocks = self._blocks_for_run(run)
        cursor = int(run["data"].get("cursor", 0))
        while cursor < len(blocks):
            block = blocks[cursor]
            kind = block.get("kind")

            if kind == "rule":
                contact = run["data"]["contact"]
                ok, reason = rules.rule_satisfied(contact, self._events(run["id"]), block)
                if ok:
                    self._activity(
                        run,
                        "rule_matched",
                        {"block_id": block.get("id"), "reason": reason},
                        now,
                        source=source,
                        actor=actor,
                    )
                    cursor += 1
                    self._save(run, {"cursor": cursor}, source=source, actor=actor)
                    continue
                if block.get("wait_mode") == "once":
                    self._activity(
                        run,
                        "rule_unmatched",
                        {"block_id": block.get("id"), "reason": reason},
                        now,
                        source=source,
                        actor=actor,
                    )
                    return self._finish(
                        run,
                        vocab.RUN_EXITED,
                        cursor,
                        now,
                        source=source,
                        actor=actor,
                        exit_reason="no_match",
                    )
                window = float(
                    block.get("match_within_seconds") or vocab.DEFAULT_MATCH_WITHIN_SECONDS
                )
                return self._hold(
                    run,
                    cursor,
                    "rule",
                    now,
                    window,
                    source=source,
                    actor=actor,
                    detail={"block_id": block.get("id")},
                )

            if kind == "content":
                decision = rules.decide_channel(block, run["data"]["contact"])
                if decision["can_deliver"]:
                    self._dispatch(
                        run,
                        block,
                        decision["channel"],
                        state="delivered",
                        is_fallback=False,
                        reason=None,
                        retried=0,
                        now=now,
                        source=source,
                        actor=actor,
                    )
                    self._activity(
                        run,
                        "content_dispatched",
                        {"block_id": block.get("id"), "channel": decision["channel"]},
                        now,
                        source=source,
                        actor=actor,
                    )
                    cursor += 1
                    self._save(run, {"cursor": cursor}, source=source, actor=actor)
                    continue
                if not block.get("fallback_enabled"):
                    self._dispatch(
                        run,
                        block,
                        decision["channel"],
                        state="not_delivered",
                        is_fallback=False,
                        reason=decision["reason"],
                        retried=0,
                        now=now,
                        source=source,
                        actor=actor,
                    )
                    self._activity(
                        run,
                        "content_not_delivered",
                        {"block_id": block.get("id"), "reason": decision["reason"]},
                        now,
                        source=source,
                        actor=actor,
                    )
                    cursor += 1
                    self._save(run, {"cursor": cursor}, source=source, actor=actor)
                    continue
                window = float(
                    block.get("send_window_seconds") or vocab.DEFAULT_SEND_WINDOW_SECONDS
                )
                self._dispatch(
                    run,
                    block,
                    decision["channel"],
                    state="retrying",
                    is_fallback=False,
                    reason=decision["reason"],
                    retried=0,
                    now=now,
                    source=source,
                    actor=actor,
                )
                return self._hold(
                    run,
                    cursor,
                    "send_window",
                    now,
                    window,
                    source=source,
                    actor=actor,
                    detail={"block_id": block.get("id"), "channel": decision["channel"]},
                )

            if kind == "wait":
                window = float(block["duration_seconds"])
                return self._hold(
                    run,
                    cursor,
                    "wait",
                    now,
                    window,
                    source=source,
                    actor=actor,
                    detail={"block_id": block.get("id")},
                )

            # tag
            tags = list(run["data"].get("tags") or [])
            if block.get("tag") not in tags:
                tags.append(block.get("tag"))
            self._activity(
                run,
                "tagged",
                {"tag": block.get("tag")},
                now,
                source=source,
                actor=actor,
            )
            cursor += 1
            self._save(run, {"tags": tags, "cursor": cursor}, source=source, actor=actor)

        return self._finish(run, vocab.RUN_FINISHED, cursor, now, source=source, actor=actor)

    def _hold(
        self,
        run: dict[str, Any],
        cursor: int,
        kind: str,
        now: datetime,
        seconds: float,
        *,
        source: str,
        actor: str,
        detail: Mapping[str, Any],
    ) -> dict[str, Any]:
        resume = now + timedelta(seconds=seconds)
        self._activity(
            run,
            "wait_started",
            {"kind": kind, "resume_at": rules.to_iso(resume), **dict(detail)},
            now,
            source=source,
            actor=actor,
        )
        self._save(
            run,
            {
                "state": vocab.RUN_WAITING,
                "waiting_kind": kind,
                "waiting_block": cursor,
                "resume_at": rules.to_iso(resume),
                "wait_started_at": rules.to_iso(now),
            },
            source=source,
            actor=actor,
        )
        return self.run_view(run["id"])

    def _finish(
        self,
        run: dict[str, Any],
        state: str,
        cursor: int,
        now: datetime,
        *,
        source: str,
        actor: str,
        exit_reason: str | None = None,
    ) -> dict[str, Any]:
        detail: dict[str, Any] = {"state": state}
        if exit_reason:
            detail["reason"] = exit_reason
        code = "run_finished" if state == vocab.RUN_FINISHED else "run_exited"
        self._activity(run, code, detail, now, source=source, actor=actor)
        self._save(run, self._resume_patch(cursor) | {"state": state}, source=source, actor=actor)
        return self.run_view(run["id"])

    # ------------------------------------------------------------------ #
    # Summary
    # ------------------------------------------------------------------ #

    def summary(self, room_id: str) -> dict[str, Any]:
        series_rows = self.store.list(vocab.SERIES_COLLECTION, room_id=room_id, limit=500)
        by_state = {state: 0 for state in vocab.SERIES_STATES}
        for row in series_rows:
            state = row["data"].get("state")
            if state in by_state:
                by_state[state] += 1

        runs = self.store.list(vocab.RUN_COLLECTION, room_id=room_id, limit=2000)
        runs_by_state = {state: 0 for state in vocab.RUN_STATES}
        for run in runs:
            state = run["data"].get("state")
            if state in runs_by_state:
                runs_by_state[state] += 1

        dispatches = self.store.list(vocab.DISPATCH_COLLECTION, room_id=room_id, limit=5000)
        dispatch_by_state: dict[str, int] = {}
        fallbacks = 0
        for row in dispatches:
            state = str(row["data"].get("state"))
            dispatch_by_state[state] = dispatch_by_state.get(state, 0) + 1
            if row["data"].get("is_fallback"):
                fallbacks += 1

        engagements = self.store.list(vocab.ENGAGEMENT_COLLECTION, room_id=room_id, limit=5000)
        engagement_by_event: dict[str, int] = {}
        for row in engagements:
            event = str(row["data"].get("event"))
            engagement_by_event[event] = engagement_by_event.get(event, 0) + 1

        return {
            "room_id": room_id,
            "series": len(series_rows),
            "series_by_state": by_state,
            "runs": len(runs),
            "runs_by_state": runs_by_state,
            "dispatches": len(dispatches),
            "dispatches_by_state": dispatch_by_state,
            "fallbacks": fallbacks,
            "engagements": len(engagements),
            "engagement_by_event": engagement_by_event,
            "due": self.due_count(room_id),
            "poll": {"interval_seconds": vocab.POLL_INTERVAL_SECONDS, "quote": vocab.POLL_QUOTE},
            "sent_by_this_product": False,
        }
