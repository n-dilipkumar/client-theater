"""The store-facing surface of WF-040: record a batch, log the failures, retry.

:class:`SyncLog` is the only class in this package that touches a
:class:`~dsr.store.RecordStore`. It holds nothing but the store handle, so it is
built per request from a dependency and the whole workflow is unit-testable
against a temporary database without the app.

Four rules it exists to enforce:

**Every write names the route that served it.** ``source`` is a required keyword on
every write method, with no default. An audit row that says ``"record run"`` cannot
be traced back to the request that caused it, and an audit row naming a path the
app no longer serves is worse than no audit row - the same defect the feature
contract calls out by name. The routes build it from ``router.prefix``.

**A refusal before the commit writes nothing.** :meth:`preflight` is the
"reject invalid writes before commit" half of this ticket and it is a pure
function of the payload and the rules. A row it refuses is not in the log, because
a row in the log means a vendor answered, and no request was sent. That is why
``POST /validate`` leaves no audit rows, and the suite asserts it.

**Retries re-send the failed rows and nothing else.** The user flow's last step is
"Retry failed rows only - successes are not re-sent", so the retry endpoints build
the normaliser's input from the failed rows alone. A succeeded row is not in that
list, which makes the guarantee structural rather than a filter somebody has to
remember to add.

**The room transports nothing.** Every ``apis_hit`` in the research is a call a
connector makes, and this product holds no vendor credentials. The retry and drain
endpoints take the responses back rather than inventing an HTTP client, and called
with no body they report the batch to send. See the
``the-drain-is-the-connector-not-the-server`` inference.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from dsr.partial_failures import retry as retry_module, rules as rules_module, validation
from dsr.partial_failures.errors import InvalidPayload, UnknownRoom, UnknownRow, UnknownRun
from dsr.partial_failures.normalise import BatchOutcome, RowOutcome, normalise
from dsr.partial_failures.timestamps import TIMESTAMP_KEYS, check_not_ahead, iso, parse_instant
from dsr.partial_failures.vocabulary import (
    DISPOSITIONS,
    ROOM_COLLECTION,
    ROW_COLLECTION,
    ROW_STATUSES,
    RULES_COLLECTION,
    RULES_RECORD_ID,
    RUN_COLLECTION,
    require_connector,
)
from dsr.store import RecordStore

#: Keys an input row may carry that this workflow owns. Everything else in a row
#: is the caller's and is stored verbatim, which is what keeps a team from needing
#: a migration to add a field to a synced row.
_ROW_RESERVED = frozenset({"row_key", "entity", "values", "trace_id"})

#: Keys of a run payload this workflow owns. Anything else is the caller's and is
#: stored verbatim, for the same reason.
_RUN_RESERVED = frozenset(
    {"connector", "rows", "vendor", "room_id", "started_at", "finished_at", "as_of"}
)

#: How much of a row's retry history is kept.
#:
#: A row an admin can retry without bound would otherwise grow an unbounded array
#: inside a record, and the audit log would carry a copy of the growth on every
#: attempt. The cap and the flag make the loss visible. Not sourced: the research
#: describes the retry action and says nothing about how long its history lives.
HISTORY_LIMIT = 25

#: Page size for a collection scan. The store caps a single read at 1000.
_PAGE = 1000

#: A ceiling on how many records one scan will read, so a pathological deployment
#: cannot turn a Sync log page into an unbounded query. A response that hits it
#: says so rather than quietly reporting a partial count.
_MAX_RECORDS = 20_000


class SyncLog:
    """The Sync log, the field-level detail behind it, and the retry queue."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    # -- rooms -------------------------------------------------------------- #

    def require_room(self, room_id: Any) -> dict[str, Any]:
        """The room, or a refusal the HTTP layer turns into a 404."""
        text = str(room_id or "").strip()
        record = self.store.get(text) if text else None
        if record is None or record.get("collection") != ROOM_COLLECTION:
            raise UnknownRoom(text or "(none)")
        return record

    # -- rules -------------------------------------------------------------- #

    def rules(self) -> tuple[dict[str, Any], str]:
        """The rules in force, and whether they came from the defaults."""
        record = self.store.get(RULES_RECORD_ID)
        data = (
            record.get("data") if record and record.get("collection") == RULES_COLLECTION else None
        )
        return rules_module.effective(data if isinstance(data, Mapping) else None)

    def rules_view(self) -> dict[str, Any]:
        """The rules as served, plus the stored copy when the two disagree.

        ``stored`` is served even when it is in force, so a client can show the
        configuration as it was written rather than only as it was interpreted.
        """
        rules, origin = self.rules()
        record = self.store.get(RULES_RECORD_ID)
        stored = record.get("data") if record else None
        view = rules_module.describe(rules, origin)
        view["stored"] = dict(stored) if isinstance(stored, Mapping) else None
        view["effective"] = {
            "max_attempts": rules["max_attempts"],
            "routing": len(rules["routing"]),
            "preflight": len(rules["preflight"]),
        }
        return view

    def save_rules(
        self, patch: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Store a rules override. Audited, and the row is the whole of it.

        A patch, not a replacement: adding a pre-flight rule should not silently
        remove the three this build ships with. ``source`` has no default because
        the route that served this is the only thing that knows it.
        """
        current, _origin = self.rules()
        merged = rules_module.merge(current, patch)
        existing = self.store.get(RULES_RECORD_ID)
        if existing is None:
            record = self.store.create(
                RULES_COLLECTION, merged, record_id=RULES_RECORD_ID, actor=actor, source=source
            )
        else:
            record = self.store.update(RULES_RECORD_ID, merged, actor=actor, source=source)
        # The full view rather than just the record: a caller that has just changed
        # the rules wants to see what is now in force, not what it sent.
        return {"updated": True, "record": record, **self.rules_view()}

    # -- before the commit -------------------------------------------------- #

    def preflight(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Which rows of a proposed batch may be sent, and which must not be.

        A pure read of the rules. Nothing is written, and that is the whole of the
        "before commit" half: a row refused here costs no CRM call, does not appear
        in the Sync log, and is not something "Retry failed rows only" will ever try
        to re-send, because it was never sent.
        """
        if not isinstance(payload, Mapping):
            raise InvalidPayload(f"the payload must be a JSON object; got {type(payload).__name__}")
        connector = require_connector(_require(payload, "connector"))
        rows = _input_rows(_require_rows(payload))
        rules, origin = self.rules()
        verdict = validation.check_batch(rows, rules["preflight"], connector=connector)
        verdict["rules_source"] = origin
        verdict["at"] = iso(datetime.now(timezone.utc))
        return verdict

    # -- recording a batch -------------------------------------------------- #

    def record_run(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Record one sync run and one log row per input row.

        The room is required. The research lists "room error table" among the data
        sources, so a failure belongs to a deal, and a row scoped to nothing is a
        row no room's Sync log can show.

        The pre-flight verdict is computed and stored on the run even though
        nothing was refused. A batch that went out carrying a row the room's own
        rules would have refused is the cheapest possible moment to learn that a
        rule is needed, and the run is where someone is already looking.
        """
        if not isinstance(payload, Mapping):
            raise InvalidPayload(
                f"the run payload must be a JSON object; got {type(payload).__name__}"
            )

        moment = now or datetime.now(timezone.utc)
        room = self.require_room(room_id or payload.get("room_id"))
        connector = require_connector(_require(payload, "connector"))
        inputs = _input_rows(_require_rows(payload))
        vendor = _require_vendor(payload)
        rules, origin = self.rules()

        started = parse_instant(
            _first_present(payload, TIMESTAMP_KEYS) or payload.get("started_at") or moment
        )
        finished = parse_instant(payload["finished_at"]) if payload.get("finished_at") else moment
        check_not_ahead(started, now=moment)
        check_not_ahead(finished, now=moment)
        if finished < started:
            raise InvalidPayload(
                f"the run finished at {iso(finished)}, before it started at {iso(started)}"
            )

        before = validation.check_batch(inputs, rules["preflight"], connector=connector)
        outcome = normalise(
            connector, vendor["status"], vendor["body"], inputs, overrides=rules["routing"]
        )

        run_data = {
            **{k: v for k, v in payload.items() if k not in _RUN_RESERVED},
            "connector": connector,
            "started_at": iso(started),
            "finished_at": iso(finished),
            "http_status": outcome.http_status,
            "per_record": outcome.per_record,
            "rows": len(inputs),
            "succeeded": outcome.succeeded,
            "failed": outcome.failed,
            "reported_errors": outcome.reported_errors,
            "described_errors": outcome.described_errors,
            "consistent": outcome.consistent,
            "request_error": outcome.request.as_dict() if outcome.request else None,
            "unattributed": [error.as_dict() for error in outcome.unattributed],
            "notes": list(outcome.notes) + _refusable_notes(before),
            "preflight": {
                "rules_source": origin,
                "would_refuse": before["refused"],
                "would_accept": before["accepted"],
                "refused_rows": before["hold"],
                "rules_applied": before["rules_applied"],
            },
        }
        run = self.store.create(
            RUN_COLLECTION, run_data, room_id=room["id"], actor=actor, source=source
        )

        records = self._create_rows(
            run_id=run["id"],
            connector=connector,
            inputs=inputs,
            outcome=outcome,
            before=before,
            rules=rules,
            room_id=room["id"],
            actor=actor,
            source=source,
            moment=moment,
            attempts=1,
        )
        return {
            "recorded": True,
            "run": self._run_view(run_data | {"id": run["id"], "room_id": room["id"]}, records),
            "rows": [self._row_view(record) for record in records],
        }

    def get_run(self, run_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        """One run with its per-row outcomes and the log rows they produced."""
        run = self._require_run(run_id, room_id=room_id)
        records, _truncated = self._rows_for_run(run["id"], room_id=run["room_id"])
        return self._run_view(run["data"] | {"id": run["id"], "room_id": run["room_id"]}, records)

    def list_runs(
        self, *, room_id: str | None = None, connector: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """Sync runs, newest first, with the counts a rep reads them for."""
        if room_id:
            self.require_room(room_id)
        wanted = require_connector(connector) if connector else None

        records, truncated = self._scan(RUN_COLLECTION, room_id=room_id)
        selected = [
            record
            for record in records
            if not wanted or (record.get("data") or {}).get("connector") == wanted
        ]
        selected.sort(key=self._run_sort_key, reverse=True)
        capped = selected[: max(1, min(int(limit), 1000))]

        by_connector: dict[str, dict[str, int]] = {}
        for record in selected:
            name = str((record.get("data") or {}).get("connector") or "")
            bucket = by_connector.setdefault(
                name, {"runs": 0, "rows": 0, "succeeded": 0, "failed": 0}
            )
            bucket["runs"] += 1
            bucket["rows"] += int((record.get("data") or {}).get("rows") or 0)
            bucket["succeeded"] += int((record.get("data") or {}).get("succeeded") or 0)
            bucket["failed"] += int((record.get("data") or {}).get("failed") or 0)

        return {
            "count": len(capped),
            "total": len(selected),
            "truncated": truncated,
            "runs": [
                {
                    "id": record["id"],
                    "room_id": record["room_id"],
                    "connector": (record.get("data") or {}).get("connector"),
                    "label": (record.get("data") or {}).get("label"),
                    "started_at": (record.get("data") or {}).get("started_at"),
                    "http_status": (record.get("data") or {}).get("http_status"),
                    "per_record": (record.get("data") or {}).get("per_record"),
                    "rows": (record.get("data") or {}).get("rows"),
                    "succeeded": (record.get("data") or {}).get("succeeded"),
                    "failed": (record.get("data") or {}).get("failed"),
                }
                for record in capped
            ],
            "by_connector": by_connector,
        }

    # -- the Sync log ------------------------------------------------------- #

    def sync_log(
        self,
        room_id: str,
        *,
        run_id: str | None = None,
        status: str | None = None,
        disposition: str | None = None,
        connector: str | None = None,
        field: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """The room's Sync log: one row per outcome, newest attempt first.

        Every filter is a value the room already stores on the row, so a new filter
        is a query parameter and not a schema change. The summary is computed over
        exactly the rows returned, so a filtered view does not report totals for the
        whole log.
        """
        room = self.require_room(room_id)
        wanted_status = _require_choice("status", status, ROW_STATUSES)
        wanted_disposition = _require_choice("disposition", disposition, DISPOSITIONS)
        wanted_connector = require_connector(connector) if connector else None
        wanted_field = str(field).strip().lower() if field else None

        records, truncated = self._scan(ROW_COLLECTION, room_id=room["id"])
        selected: list[dict[str, Any]] = []
        for record in records:
            data = record.get("data") or {}
            if run_id and str(data.get("run_id") or "") != str(run_id):
                continue
            if wanted_status and str(data.get("status") or "") != wanted_status:
                continue
            if wanted_disposition and str(data.get("disposition") or "") != wanted_disposition:
                continue
            if wanted_connector and str(data.get("connector") or "") != wanted_connector:
                continue
            if wanted_field and str(data.get("field") or "").lower() != wanted_field:
                continue
            selected.append(record)

        selected.sort(key=self._log_sort_key, reverse=True)
        capped = selected[: max(1, min(int(limit), 1000))]

        summary: dict[str, Any] = {
            "rows": len(selected),
            "succeeded": 0,
            "failed": 0,
            "queued": 0,
            "needs_action": 0,
            "resolved": 0,
            "retryable": 0,
            "with_doc_link": 0,
            "with_property": 0,
        }
        for record in selected:
            data = record.get("data") or {}
            if str(data.get("status") or "") in ("succeeded", "failed"):
                summary[str(data["status"])] += 1
            disposition = str(data.get("disposition") or "")
            if disposition in summary:
                summary[disposition] += 1
            if data.get("retryable"):
                summary["retryable"] += 1
            if (data.get("error") or {}).get("doc_link"):
                summary["with_doc_link"] += 1
            if data.get("field"):
                summary["with_property"] += 1

        return {
            "room_id": room["id"],
            "count": len(capped),
            "total": len(selected),
            "truncated": truncated,
            "summary": summary,
            "rows": [self._row_view(record) for record in capped],
            "statuses": list(ROW_STATUSES),
            "dispositions": list(DISPOSITIONS),
        }

    def row_detail(self, room_id: str, row_id: str) -> dict[str, Any]:
        """One row in full: which property, what was sent, what was expected.

        The researched detail view, and the answer to the question the research asks
        an admin to be able to answer: "Field-level error detail (which property,
        what was sent, what was expected)".
        """
        room = self.require_room(room_id)
        record = self._require_row(row_id, room_id=room["id"])
        data = record.get("data") or {}
        error = data.get("error") if isinstance(data.get("error"), Mapping) else {}
        field = data.get("field")
        sent = data.get("sent") if isinstance(data.get("sent"), Mapping) else {}
        rules, origin = self.rules()
        run = self.store.get(str(data.get("run_id") or ""))
        run_data = (run or {}).get("data") or {}

        return {
            "row": self._row_view(record),
            "detail": {
                "property": field,
                "property_source": data.get("field_basis"),
                "sent": sent.get(field) if field else None,
                "sent_length": len(sent[field])
                if field and isinstance(sent.get(field), str)
                else None,
                "expected": data.get("expected") or [],
                "reason": error.get("message"),
                "code": error.get("code"),
                "doc_link": error.get("doc_link"),
                "retryable": error.get("retryable"),
                "basis": error.get("basis"),
                "matched_rule": error.get("matched_rule"),
                "scope": error.get("scope"),
                "http_status": error.get("http_status"),
                "correlation": data.get("correlation"),
                "correlation_basis": data.get("correlation_basis"),
                "related": error.get("related") or [],
                "raw": error.get("raw") or {},
                "passed_preflight": data.get("passed_preflight"),
                "preflight_violations": data.get("preflight_violations") or [],
                "hold_message": _hold_message(data),
            },
            "retry": {
                "attempts": data.get("attempts"),
                "max_attempts": rules["max_attempts"],
                "can_retry_now": retry_module.can_retry(data, max_attempts=rules["max_attempts"]),
                "disposition": data.get("disposition"),
                "next_retry_at": data.get("next_retry_at"),
                "bound_is": (
                    "the automatic drain's bound; a manual retry is not capped, because an admin who "
                    "has just fixed the mapping is answering a question the queue cannot"
                ),
            },
            "rules_source": origin,
            "run": {
                "id": (run or {}).get("id"),
                "connector": run_data.get("connector"),
                "started_at": run_data.get("started_at"),
                "http_status": run_data.get("http_status"),
                "per_record": run_data.get("per_record"),
                "notes": run_data.get("notes") or [],
                "preflight": run_data.get("preflight"),
            },
            "history": data.get("history") or [],
            "history_truncated": bool(data.get("history_truncated")),
            "queue_note": data.get("queue_note"),
        }

    # -- the retry queue ---------------------------------------------------- #

    def queue(
        self,
        *,
        room_id: str | None = None,
        connector: str | None = None,
        limit: int = 1000,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        """The retry queue: what drains automatically, and what waits for a person.

        A read, and the reason the researched automation is inspectable. A queue
        only a timer can see is a queue nobody can debug, and the research
        distinguishes the two waiting states precisely enough that showing them side
        by side is the point.

        ``as_of`` answers the question for a past instant, so "what was the queue
        like at the end of last week's batch" is a question this can answer rather
        than one a reader has to reconstruct.
        """
        if room_id:
            self.require_room(room_id)
        wanted = require_connector(connector) if connector else None
        rules, origin = self.rules()
        moment = parse_instant(as_of) if as_of else None
        records, truncated = self._failed_rows(room_id=room_id, connector=wanted, limit=limit)
        planned = retry_module.plan(
            [self._plan_row(record) for record in records],
            max_attempts=rules["max_attempts"],
            now=moment,
        )
        return {
            "as_of": planned["at"],
            "rules_source": origin,
            "max_attempts": rules["max_attempts"],
            "backoff": retry_module.BACKOFF_LABEL,
            "counts": planned["counts"],
            "drain": planned["drain"],
            "scheduled": planned["scheduled"],
            "expired": planned["expired"],
            "waiting": planned["waiting"],
            "truncated": truncated,
        }

    def drain(
        self,
        payload: Mapping[str, Any] | None = None,
        *,
        room_id: str | None = None,
        connector: str | None = None,
        actor: str | None = None,
        source: str,
        now: datetime | None = None,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        """The researched automation: drain what clears on its own, and stop.

        Two jobs, separated because they are different jobs.

        *Rows that have exhausted the bound move to ``needs_action``.* A row sent
        five times is not a throttle, and leaving it in the automatic queue means
        the researched drain never terminates and the row never reaches the only
        state a person can act on. This half writes; it is the only write here.

        *Rows that are retryable, under the bound and past their backoff are
        reported for sending.* The room does not send them: every ``apis_hit`` in
        the research is a call a connector makes, and this product holds no vendor
        credentials. Called with no body the endpoint returns the batch - row key,
        trace id, entity and the mapped values. Called with ``vendor``, the
        responses are applied through the same normaliser a fresh batch goes
        through, so a retried row and a first-time row are classified by one piece
        of code.
        """
        moment = now or (parse_instant(as_of) if as_of else None) or datetime.now(timezone.utc)
        body = payload or {}
        if room_id:
            self.require_room(room_id)
        wanted = require_connector(connector) if connector else None
        rules, origin = self.rules()

        records, truncated = self._failed_rows(
            room_id=room_id, connector=wanted, limit=_MAX_RECORDS
        )
        by_id = {str(record["id"]): record for record in records}
        planned = retry_module.plan(
            [self._plan_row(record) for record in records],
            max_attempts=rules["max_attempts"],
            now=moment,
        )
        # Counted over the whole collection rather than from the plan, because the
        # plan only sees the failed rows: the point of the number is that the drain
        # had successes available and left them alone.
        succeeded = self._succeeded_count(room_id=room_id, connector=wanted)

        expired_now = self._expire(planned["expired"], by_id, actor=actor, source=source)

        sent = [
            self._send_row(by_id[str(entry["id"])], entry)
            for entry in planned["drain"]
            if str(entry["id"]) in by_id
        ]

        applied: list[dict[str, Any]] = []
        outcome_summary: dict[str, Any] | None = None
        vendor = _vendor_of(body)
        if vendor is not None and sent:
            connectors = sorted({str(item["connector"]) for item in sent if item["connector"]})
            if len(connectors) > 1:
                raise InvalidPayload(
                    "the rows due for a retry span more than one connector ("
                    f"{', '.join(connectors)}); a response from one CRM cannot be applied to "
                    "another's rows. Drain them separately, or filter with ?connector=."
                )
            used = require_connector(body.get("connector") or (connectors[0] if connectors else ""))
            applied, outcome_summary = self._apply(
                records,
                sent,
                used,
                vendor,
                rules=rules,
                actor=actor,
                source=source,
                moment=moment,
            )

        return {
            "drained_at": iso(moment),
            "rules_source": origin,
            "max_attempts": rules["max_attempts"],
            "backoff": retry_module.BACKOFF_LABEL,
            "counts": planned["counts"],
            "sent": sent,
            "scheduled": planned["scheduled"],
            "expired_now": expired_now,
            "applied": applied,
            "outcome": outcome_summary,
            "waiting": planned["counts"]["waiting"],
            "succeeded_not_resent": succeeded,
            "truncated": truncated,
            "transported_by": (
                "the caller, through the connector: this room opens no socket and holds no vendor "
                "credential. Send `sent`, then post the vendor's response back as `vendor`."
            ),
        }

    def retry_failed(
        self,
        run_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """The researched admin action: "Retry failed rows only".

        The failed rows of one run are the normaliser's input and nothing else, so
        a row that succeeded cannot be re-sent even by accident. Succeeded rows are
        returned as ``untouched`` so the caller can see they were considered and
        excluded rather than forgotten - and so a test can see it too.

        Not capped by ``max_attempts``: that bound governs the *automatic* drain.
        An admin who has just fixed the mapping is answering a question the queue
        cannot, and refusing their explicit action would make the researched "waits
        for admin action" state a dead end.
        """
        moment = now or datetime.now(timezone.utc)
        run = self._require_run(run_id)
        run_data = run.get("data") or {}
        connector = str(run_data.get("connector") or "")
        rules, origin = self.rules()

        records, _truncated = self._rows_for_run(run["id"], room_id=run["room_id"])
        failed = [
            record
            for record in records
            if str((record.get("data") or {}).get("status")) == "failed"
        ]
        succeeded = [
            record
            for record in records
            if str((record.get("data") or {}).get("status")) == "succeeded"
        ]
        untouched = [(record.get("data") or {}).get("row_key") for record in succeeded]

        base = {
            "run_id": run["id"],
            "room_id": run["room_id"],
            "connector": connector,
            "untouched": untouched,
            "rules_source": origin,
        }

        if not failed:
            return {
                **base,
                "retried": 0,
                "nothing_to_retry": True,
                "detail": (
                    f"every row in this run succeeded, so there is nothing to re-send; the "
                    f"{len(succeeded)} succeeded row(s) were not touched"
                ),
                "sent": [],
            }

        sent = [
            self._send_row(record, {"attempts": (record.get("data") or {}).get("attempts")})
            for record in failed
        ]

        vendor = _vendor_of(payload)
        if vendor is None:
            return {
                **base,
                "retried": 0,
                "awaiting_vendor_response": True,
                "sent": sent,
                "detail": (
                    f"these are the {len(failed)} failed row(s) to re-send; the "
                    f"{len(succeeded)} succeeded row(s) in this run are not in the list and will "
                    "not be re-sent"
                ),
            }

        applied, outcome_summary = self._apply(
            failed, sent, connector, vendor, rules=rules, actor=actor, source=source, moment=moment
        )
        return {
            **base,
            "retried": len(applied),
            "succeeded_now": sum(1 for item in applied if item["status"] == "succeeded"),
            "still_failed": sum(1 for item in applied if item["status"] == "failed"),
            "sent": sent,
            "applied": applied,
            "outcome": outcome_summary,
        }

    # -- internals: reads --------------------------------------------------- #

    def _failed_rows(
        self, *, room_id: str | None, connector: str | None, limit: int
    ) -> tuple[list[dict[str, Any]], bool]:
        records, truncated = self._scan(ROW_COLLECTION, room_id=room_id)
        failed = [
            record
            for record in records
            if str((record.get("data") or {}).get("status")) == "failed"
            and (not connector or (record.get("data") or {}).get("connector") == connector)
        ]
        failed.sort(key=self._log_sort_key, reverse=True)
        return failed[: max(1, min(int(limit), _MAX_RECORDS))], truncated

    def _succeeded_count(self, *, room_id: str | None, connector: str | None) -> int:
        """How many rows in scope already succeeded, for the drain's report.

        Read from the store rather than from the plan, because the plan is only
        given the failed rows. The number exists to make the researched guarantee
        visible: the drain had successful rows available and did not send them.
        """
        records, _truncated = self._scan(ROW_COLLECTION, room_id=room_id)
        return sum(
            1
            for record in records
            if str((record.get("data") or {}).get("status")) == "succeeded"
            and (not connector or (record.get("data") or {}).get("connector") == connector)
        )

    def _rows_for_run(
        self, run_id: str, *, room_id: str | None = None
    ) -> tuple[list[dict[str, Any]], bool]:
        records, truncated = self._scan(ROW_COLLECTION, room_id=room_id)
        selected = [
            record
            for record in records
            if str((record.get("data") or {}).get("run_id")) == str(run_id)
        ]
        # Input order, which is the order the connector sent them in and the only
        # order a positional correlation can be read against.
        selected.sort(key=lambda record: int((record.get("data") or {}).get("position") or 0))
        return selected, truncated

    def _scan(
        self, collection: str, *, room_id: str | None = None
    ) -> tuple[list[dict[str, Any]], bool]:
        """Every live record in a collection, paged on the id so nothing is skipped.

        Paged on the record id rather than on ``created_at``: ids are unique, so an
        offset page cannot skip or repeat a row when two records share a
        millisecond, which is exactly what a batch writer produces.
        """
        records: list[dict[str, Any]] = []
        offset = 0
        while len(records) < _MAX_RECORDS:
            page = self.store.list(
                collection,
                room_id=room_id,
                limit=_PAGE,
                offset=offset,
                order_by="id",
                descending=False,
            )
            if not page:
                break
            records.extend(page)
            if len(page) < _PAGE:
                break
            offset += len(page)
        return records, len(records) >= _MAX_RECORDS

    def _require_run(self, run_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        text = str(run_id or "").strip()
        record = self.store.get(text) if text else None
        if record is None or record.get("collection") != RUN_COLLECTION:
            raise UnknownRun(text or "(none)")
        if room_id and str(record.get("room_id")) != str(room_id):
            raise UnknownRun(f"{text} is not on room {room_id}")
        return record

    def _require_row(self, row_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        text = str(row_id or "").strip()
        record = self.store.get(text) if text else None
        if record is None or record.get("collection") != ROW_COLLECTION:
            raise UnknownRow(text or "(none)")
        if room_id and str(record.get("room_id")) != str(room_id):
            raise UnknownRow(f"{text} is not on room {room_id}")
        return record

    # -- internals: writes -------------------------------------------------- #

    def _create_rows(
        self,
        *,
        run_id: str,
        connector: str,
        inputs: Sequence[Mapping[str, Any]],
        outcome: BatchOutcome,
        before: Mapping[str, Any],
        rules: Mapping[str, Any],
        room_id: str,
        actor: str | None,
        source: str,
        moment: datetime,
        attempts: int,
    ) -> list[dict[str, Any]]:
        """One audited record per row, carrying the field-level detail with it."""
        verdicts = {str(entry["row_key"]): entry for entry in before.get("verdicts") or []}
        written: list[dict[str, Any]] = []
        for item in outcome.rows:
            data = self._row_data(
                run_id=run_id,
                connector=connector,
                source_row=inputs[item.index],
                item=item,
                rules=rules,
                attempts=attempts,
                moment=moment,
                preflight=verdicts.get(item.row_key) or {},
            )
            written.append(
                self.store.create(ROW_COLLECTION, data, room_id=room_id, actor=actor, source=source)
            )
        return written

    def _apply(
        self,
        records: Sequence[Mapping[str, Any]],
        sent: Sequence[Mapping[str, Any]],
        connector: str,
        vendor: Mapping[str, Any],
        *,
        rules: Mapping[str, Any],
        actor: str | None,
        source: str,
        moment: datetime,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Fold a retry's outcomes back onto the rows they belong to.

        The attempt counter goes up whatever the outcome, because the row *was*
        sent. A counter that only moved on success would make a row that is being
        re-sent forever look like it had never been tried, and would let the attempt
        bound never trigger.
        """
        inputs = [
            {
                "row_key": item["row_key"],
                "entity": item["entity"],
                "trace_id": item["trace_id"],
                "values": item["values"],
            }
            for item in sent
        ]
        before = validation.check_batch(inputs, rules["preflight"], connector=connector)
        verdicts = {str(entry["row_key"]): entry for entry in before.get("verdicts") or []}
        outcome = normalise(
            connector, vendor["status"], vendor["body"], inputs, overrides=rules["routing"]
        )
        by_row = {str((record.get("data") or {}).get("row_key")): record for record in records}

        applied: list[dict[str, Any]] = []
        for item in outcome.rows:
            record = by_row.get(item.row_key)
            if record is None:
                continue
            previous = record.get("data") or {}
            attempts = int(previous.get("attempts") or 1) + 1
            merged = self._row_data(
                run_id=str(previous.get("run_id") or ""),
                connector=connector,
                source_row={
                    "row_key": item.row_key,
                    "entity": item.entity,
                    "trace_id": item.trace_id,
                    "values": previous.get("sent") or {},
                },
                item=item,
                rules=rules,
                attempts=attempts,
                moment=moment,
                preflight=verdicts.get(item.row_key) or {},
                previous=previous,
            )
            updated = self.store.update(record["id"], merged, actor=actor, source=source)
            applied.append(
                {
                    "row_id": record["id"],
                    "row_key": item.row_key,
                    "status": item.status,
                    "attempts": attempts,
                    "row": self._row_view(updated),
                }
            )
        return applied, outcome.as_dict()

    def _expire(
        self,
        expired: Sequence[Mapping[str, Any]],
        by_id: Mapping[str, Mapping[str, Any]],
        *,
        actor: str | None,
        source: str,
    ) -> list[dict[str, Any]]:
        """Move rows past the attempt bound out of the automatic queue.

        Only rows whose disposition actually changes are written, so a drain with
        nothing to do leaves the audit log alone and a test can say so.
        """
        moved: list[dict[str, Any]] = []
        for entry in expired:
            record = by_id.get(str(entry["id"]))
            if record is None:
                continue
            data = record.get("data") or {}
            if data.get("disposition") == "needs_action":
                continue
            updated = self.store.update(
                record["id"],
                {
                    "disposition": "needs_action",
                    "next_retry_at": None,
                    "queue_note": str(entry.get("expires_because") or ""),
                },
                actor=actor,
                source=source,
            )
            moved.append(
                {"row_id": record["id"], "row_key": (updated.get("data") or {}).get("row_key")}
            )
        return moved

    def _row_data(
        self,
        *,
        run_id: str,
        connector: str,
        source_row: Mapping[str, Any],
        item: RowOutcome,
        rules: Mapping[str, Any],
        attempts: int,
        moment: datetime,
        preflight: Mapping[str, Any],
        previous: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The stored shape of one log row.

        Anything the caller put on the input row that this workflow does not own is
        stored verbatim, so a team adding ``data.billing.seat`` to a synced row
        ships a record rather than a migration. On a retry ``source_row`` carries
        only the four keys the room owns, and the store's shallow merge leaves the
        caller's own fields from the first attempt in place.
        """
        error = item.error.as_dict() if item.error else None
        retryable = bool(error and error.get("retryable"))
        entity = str(source_row.get("entity") or item.entity or "")
        property_name, property_source = self._property(error, preflight)

        history = list((previous or {}).get("history") or [])
        history.append(
            {
                "at": iso(moment),
                "status": item.status,
                "attempt": attempts,
                "code": (error or {}).get("code"),
                "message": (error or {}).get("message"),
                "retryable": retryable,
                "scope": (error or {}).get("scope"),
                "http_status": (error or {}).get("http_status"),
            }
        )
        truncated = bool((previous or {}).get("history_truncated"))
        while len(history) > HISTORY_LIMIT:
            history = history[1:]
            truncated = True

        data: dict[str, Any] = {
            **{k: v for k, v in source_row.items() if k not in _ROW_RESERVED},
            "run_id": run_id,
            "connector": connector,
            "position": item.index,
            "entity": entity,
            "row_key": item.row_key,
            "trace_id": item.trace_id or source_row.get("trace_id"),
            "status": item.status,
            "attempts": attempts,
            "first_attempt_at": (previous or {}).get("first_attempt_at") or iso(moment),
            "last_attempt_at": iso(moment),
            "succeeded_at": iso(moment) if item.status == "succeeded" else None,
            "retryable": retryable,
            # A disposition is a *waiting* state, and a row that succeeded on its
            # first attempt is not waiting for anything. `resolved` is reserved for
            # a row that was failed and then accepted on a retry, which is the one
            # success a rep is meant to notice.
            "disposition": _disposition_for(item, previous, retryable),
            "next_retry_at": (
                None
                if item.status == "succeeded" or not retryable
                else iso(retry_module.next_attempt_at(attempts - 1, moment))
            ),
            "error": error,
            "field": property_name,
            "field_basis": property_source,
            "expected": validation.expectations_for(
                rules["preflight"], connector=connector, entity=entity, field=property_name
            ),
            "passed_preflight": bool(preflight.get("accepted")) if preflight else None,
            "preflight_violations": preflight.get("violations") or [],
            "sent": dict(source_row.get("values") or {}),
            "correlation": item.correlation,
            "correlation_basis": item.correlation_basis,
            "vendor_record_id": item.vendor_record_id,
            "history": history,
            "history_truncated": truncated,
        }
        if previous and previous.get("queue_note"):
            data["queue_note"] = previous["queue_note"]
        return data

    def _property(
        self, error: Mapping[str, Any] | None, preflight: Mapping[str, Any]
    ) -> tuple[str | None, str | None]:
        """Which property the log names, and which of the two sources supplied it.

        The vendor's own answer wins. Failing that, a pre-flight violation for the
        row names a real property with a real rule behind it, and the row records
        that the room supplied it rather than the vendor - which keeps "the log
        names a property" and "the log names the vendor's property" two different
        claims.
        """
        if error is not None and error.get("field"):
            return str(error["field"]), "vendor"
        for violation in preflight.get("violations") or []:
            if violation.get("field"):
                return str(violation["field"]), "preflight"
        return None, None

    # -- internals: shaping ------------------------------------------------- #

    def _row_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = record.get("data") or {}
        error = data.get("error") if isinstance(data.get("error"), Mapping) else {}
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "run_id": data.get("run_id"),
            "connector": data.get("connector"),
            "entity": data.get("entity"),
            "row_key": data.get("row_key"),
            "trace_id": data.get("trace_id"),
            "status": data.get("status"),
            "attempts": data.get("attempts"),
            "last_attempt_at": data.get("last_attempt_at"),
            "succeeded_at": data.get("succeeded_at"),
            "retryable": data.get("retryable"),
            "disposition": data.get("disposition"),
            "next_retry_at": data.get("next_retry_at"),
            "field": data.get("field"),
            "field_basis": data.get("field_basis"),
            "reason": error.get("message"),
            "code": error.get("code"),
            "doc_link": error.get("doc_link"),
            "expected": data.get("expected") or [],
            "passed_preflight": data.get("passed_preflight"),
        }

    def _run_view(
        self, data: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        rows = [self._row_view(record) for record in records]
        return {
            "id": data.get("id"),
            "room_id": data.get("room_id"),
            "connector": data.get("connector"),
            "label": data.get("label"),
            "started_at": data.get("started_at"),
            "finished_at": data.get("finished_at"),
            "http_status": data.get("http_status"),
            "per_record": data.get("per_record"),
            "rows": data.get("rows"),
            "succeeded": data.get("succeeded"),
            "failed": data.get("failed"),
            "queued": sum(1 for row in rows if row.get("disposition") == "queued"),
            "needs_action": sum(1 for row in rows if row.get("disposition") == "needs_action"),
            "resolved": sum(1 for row in rows if row.get("disposition") == "resolved"),
            "reported_errors": data.get("reported_errors"),
            "described_errors": data.get("described_errors"),
            "consistent": data.get("consistent"),
            "request_error": data.get("request_error"),
            "unattributed": data.get("unattributed") or [],
            "notes": data.get("notes") or [],
            "preflight": data.get("preflight"),
            "outcomes": rows,
        }

    @staticmethod
    def _plan_row(record: Mapping[str, Any]) -> dict[str, Any]:
        data = record.get("data") or {}
        return data | {"id": record.get("id"), "room_id": record.get("room_id")}

    @staticmethod
    def _send_row(record: Mapping[str, Any], entry: Mapping[str, Any]) -> dict[str, Any]:
        data = record.get("data") or {}
        return {
            "row_id": record.get("id"),
            "room_id": record.get("room_id"),
            "connector": data.get("connector"),
            "row_key": data.get("row_key"),
            "trace_id": data.get("trace_id"),
            "entity": data.get("entity"),
            "values": data.get("sent") or {},
            "attempts": entry.get("attempts"),
            "wait_seconds": entry.get("wait_seconds"),
            "next_attempt_at": entry.get("next_attempt_at"),
        }

    @staticmethod
    def _log_sort_key(record: Mapping[str, Any]) -> tuple[str, int, str]:
        data = record.get("data") or {}
        return (
            str(data.get("last_attempt_at") or ""),
            int(data.get("attempts") or 1),
            str(data.get("row_key") or ""),
        )

    @staticmethod
    def _run_sort_key(record: Mapping[str, Any]) -> tuple[str, str]:
        return (
            str((record.get("data") or {}).get("started_at") or ""),
            str(record.get("id") or ""),
        )


# --------------------------------------------------------------------------- #
# Payload helpers
# --------------------------------------------------------------------------- #


def _require(payload: Mapping[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value in (None, "", [], {}):
        raise InvalidPayload(
            f"{key} is required; a sync-run result payload without it cannot be turned into "
            "per-row outcomes"
        )
    return value


def _optional(payload: Mapping[str, Any], key: str) -> Any:
    value = payload.get(key)
    return None if value in (None, "") else value


def _first_present(payload: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _require_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = payload.get("rows")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise InvalidPayload(
            f"rows must be a list of the rows that were sent; got {type(rows).__name__}"
        )
    if not rows:
        raise InvalidPayload(
            "a batch with no rows records nothing; a sync run exists to report per-row outcomes"
        )
    return list(rows)


def _require_vendor(payload: Mapping[str, Any]) -> dict[str, Any]:
    vendor = payload.get("vendor")
    if not isinstance(vendor, Mapping):
        raise InvalidPayload(
            "vendor is required: a sync-run result payload is the rows that were sent plus the "
            "status and body the connector got back"
        )
    if "status" not in vendor:
        raise InvalidPayload(
            "vendor.status is required; the status is how a 207 Multi-Status is told from a 400"
        )
    return {"status": vendor.get("status"), "body": vendor.get("body")}


def _vendor_of(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """The vendor response a retry or drain was handed, or ``None`` for "not yet"."""
    if not payload or payload.get("vendor") is None:
        return None
    return _require_vendor(payload)


def _input_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The rows as the pre-flight and the normaliser both want them.

    A row's ``values`` is what the connector sent for that row: the room validates
    the mapped properties, not the vendor's envelope, because the envelope is the
    connector's business and the mapped properties are the ones the CRM validates.
    """
    prepared: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise InvalidPayload(f"row {index} must be a JSON object; got {type(row).__name__}")
        values = row.get("values")
        if values is None:
            values = {}
        if not isinstance(values, Mapping):
            raise InvalidPayload(
                f"row {index}: values must be a JSON object of the mapped properties; got "
                f"{type(values).__name__}"
            )
        prepared.append(
            {
                **{k: v for k, v in row.items() if k not in _ROW_RESERVED},
                "row_key": str(row.get("row_key") or "").strip(),
                "entity": str(row.get("entity") or ""),
                "trace_id": row.get("trace_id"),
                "values": dict(values),
            }
        )
    return prepared


def _refusable_notes(before: Mapping[str, Any]) -> list[str]:
    """The pre-flight's verdict, restated on a run that was sent anyway.

    This is the "before commit" half made visible after the fact. A batch that went
    out carrying a row the room's own rules would have refused is the cheapest
    possible moment to learn that a rule is needed, and the run is where someone is
    already looking.
    """
    if not before.get("refused"):
        return []
    return [
        f"the room's own pre-flight rules would have refused {before['refused']} of these "
        f"{before['rows']} row(s) before the batch was sent - {', '.join(before['hold'])} - so this "
        "run spent CRM calls on writes that could have been caught locally"
    ]


def _disposition_for(
    item: RowOutcome, previous: Mapping[str, Any] | None, retryable: bool
) -> str | None:
    """Which waiting state this row is in, or ``None`` because it is not waiting.

    A row that succeeded on its first attempt has no waiting state and gets none:
    ``resolved`` is a claim that something was outstanding and has been dealt with,
    and claiming it for a row that never failed would make the Sync log's one
    genuinely interesting state - a failure an admin fixed - indistinguishable from
    a write that worked the first time.
    """
    if item.status == "succeeded":
        return "resolved" if (previous or {}).get("status") == "failed" else None
    return retry_module.disposition_for(retryable)


def _hold_message(data: Mapping[str, Any]) -> str | None:
    """What to tell an admin about the room's own half of a failure.

    A row the pre-flight accepted and the CRM then refused is worth saying out
    loud, and so is a row the pre-flight had no opinion on: the second is the
    symptom of a rule this configuration is missing.
    """
    if data.get("passed_preflight"):
        return (
            "the room's own pre-flight rules accepted this row and the CRM refused it, so the "
            "refusal comes from a rule the room does not hold - an admin-configured validation "
            "rule, a plug-in, or a permission"
        )
    if data.get("expected"):
        return None
    if str(data.get("status")) == "failed":
        return (
            "the room holds no rule for this row's property, so a retry will fail the same way; "
            "add a pre-flight rule for it to stop the next attempt from costing a CRM call"
        )
    return None


def _require_choice(what: str, value: Any, allowed: Sequence[str]) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text not in allowed:
        raise InvalidPayload(f"unknown {what} {value!r}; this workflow serves {', '.join(allowed)}")
    return text


__all__ = ["SyncLog", "HISTORY_LIMIT"]
