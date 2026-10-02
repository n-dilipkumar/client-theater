"""The run: chunk, send, match results back to rows by position, write back.

This module is the researched user flow, step by step.

    1. The room's queue accumulates up to 200 pending engagement rows (or the
       nightly backlog).
    2. Admin-triggered **Sync now** (or the scheduled job) opens **Sync -> Run
       upsert**.
    3. The connector chunks rows into batches (200 for Salesforce collections,
       100 for HubSpot) and sends one upsert per chunk, keyed on the sync key
       chosen in W2.
    4. Each row either **updates** the existing CRM record (key found) or
       **creates** a new one (key not found).
    5. Per-row outcomes are written back to the room; failures appear in the sync
       log with the row's error text.

Four decisions in here are the ones a reviewer should look at first, because
each is a place where the obvious implementation is wrong.

Positional matching, not key matching
-------------------------------------

"Objects are created or updated in the order they're listed in the request body.
The ``UpsertResult`` objects are returned in the same order." So a result is tied
to a row by **position**, and a response of the wrong length is a chunk-level
failure rather than a zip that silently truncates. Matching by key would look
correct and would attach every outcome to the wrong row the first time the same
external key appears twice in one batch - which is exactly what re-running a
queue does.

allOrNone is about the chunk, not the row
-----------------------------------------

"You can choose whether to roll back the entire request when an error occurs."
When it is on and any item in a chunk failed, **nothing** in that chunk was
written, so every row in it is recorded ``rolled_back`` and none of them get a
``synced_at`` or a ``crm_record_id``. Writing back the rows that "succeeded"
would be inventing CRM state: under allOrNone there is none.

A row with no confirmation is not a successful row
--------------------------------------------------

Dataverse's ``UpsertMultiple`` "returns ``204 NoContent``", so for that vendor
there is no per-item result to read. Those rows are recorded ``submitted``:
``sent_at`` is set so the row leaves the queue and is not sent again forever, and
``synced_at`` and ``crm_record_id`` are **not** set, because the connector has no
evidence either way. Calling that success would be a lie the sync log could not
later correct; calling it a failure would re-send it on every run. The third
state is the only honest one, and the queue view counts it separately so a rep
can see it needs checking in the CRM.

Nothing runs inside the CRM
---------------------------

"Nightly/backlog upsert runs on the room's scheduler; the room may also upsert
opportunistically when the queue exceeds N rows. **Nothing runs inside the CRM in
this workflow.**" So a run writes to exactly two collections - the room's
engagement rows and the run's own log - and registers no trigger, flow, or
automation on the CRM side. :data:`WRITTEN_COLLECTIONS` names them and the suite
asserts it, because a run that started registering CRM-side work would be a
change in what this workflow is.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from dsr.crm_upsert.capabilities import Capability, resolve_mode, with_batch_size
from dsr.crm_upsert.connections import (
    COLLECTION_RUN,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SYNCED,
    STATUS_UNCONFIRMED,
    Connection,
    pending_rows,
)
from dsr.crm_upsert.errors import UnknownRun, UpsertError
from dsr.crm_upsert.payloads import (
    OUTCOMES,
    RowRejected,
    build_bulk_request,
    build_single_request,
    mapped_fields,
    preflight_row,
)
from dsr.crm_upsert.transport import OutboundRequest, OutboundResponse, Transport
from dsr.db.audited import RecordNotFound, new_id
from dsr.store import RecordStore

#: The only collections a run writes. Asserted by the suite, for the reason in
#: this module's docstring: "Nothing runs inside the CRM in this workflow."
WRITTEN_COLLECTIONS: frozenset[str] = frozenset({"engagement", COLLECTION_RUN})

#: Who asked for the run. "Admin-triggered **Sync now** (or the scheduled job)".
DRIVERS: tuple[str, ...] = ("manual", "scheduled", "opportunistic")

#: Per-item result count mismatch, which is a chunk failure rather than a
#: truncation. The wording matters in a log: it tells a rep the connector and the
#: CRM disagree about the batch, which no per-row error text can express.
RESULT_COUNT_MISMATCH = (
    "the CRM returned {got} results for {expected} rows, so no outcome can be attributed "
    "to a row; the request and the response disagree about the batch"
)

ROLLBACK_REASON = (
    "allOrNone is on, so one failed row rolled back the whole request and no row in this "
    "batch was written"
)


# --------------------------------------------------------------------------- #
# Outcomes
# --------------------------------------------------------------------------- #


@dataclass
class RowOutcome:
    """What happened to one engagement row.

    ``errors`` holds the row's own error text, because the research says failures
    "appear in the sync log with the row's error text" - the CRM's words, not a
    paraphrase of them.

    ``row_index`` is the position **within its chunk**, which is the same
    coordinate the vendor returned the result at. ``chunk_index`` says which
    request it belonged to.
    """

    record_id: str
    key: str = ""
    outcome: str = STATUS_PENDING
    crm_record_id: str = ""
    errors: list[str] = field(default_factory=list)
    chunk_index: int = 0
    row_index: int = 0
    #: The HTTP status of the request that carried this row, so a reader can tell
    #: a 4xx from a 5xx without opening the CRM.
    status: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "key": self.key,
            "outcome": self.outcome,
            "crm_record_id": self.crm_record_id or None,
            "errors": list(self.errors),
            "chunk_index": self.chunk_index,
            "row_index": self.row_index,
            "status": self.status,
        }


def chunk(items: Sequence[Any], size: int) -> list[list[Any]]:
    """Split ``items`` into consecutive chunks of at most ``size``.

    The researched caps are per request ("The list can contain up to 200
    objects", "Batch operations are limited to 100 records at a time"), so the
    chunking is over the *connection's* batch size after the vendor's cap has
    been applied, and it is consecutive rather than strided: the positional
    result guarantee only holds for a request whose items are in a known order.
    """
    if size < 1:
        raise UpsertError(f"batch size must be at least 1, got {size}")
    return [list(items[start : start + size]) for start in range(0, len(items), size)]


def chunk_indexed(items: Sequence[Any], size: int) -> list[list[tuple[int, Any]]]:
    """Like :func:`chunk`, but each item keeps its position in the original list.

    The global position is carried rather than recovered later with ``list.index``:
    two planned rows can be equal (the same key and the same fields queued twice),
    and ``index()`` would return the first of them, silently attributing both
    chunks' results to the same room row.
    """
    if size < 1:
        raise UpsertError(f"batch size must be at least 1, got {size}")
    indexed = list(enumerate(items))
    return [indexed[start : start + size] for start in range(0, len(indexed), size)]


# --------------------------------------------------------------------------- #
# Preflight
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PlannedRow:
    """A row that passed preflight: its key value and its mapped CRM fields."""

    record: Mapping[str, Any]
    key_value: str
    fields: Mapping[str, Any]

    @property
    def record_id(self) -> str:
        return str(self.record.get("id") or "")


def preflight(
    rows: Sequence[Mapping[str, Any]],
    *,
    connection: Connection,
    capability: Capability,
    key_source: str,
    field_map: Mapping[str, str],
) -> tuple[list[PlannedRow], list[RowOutcome]]:
    """Split rows into those that can be sent and those that are refused.

    One bad row must not stop the batch: the researched flow reports per-row
    outcomes, so a refusal is an outcome with a reason rather than an exception
    that abandons the other 199. :class:`RowRejected` carries a reason code from
    :data:`~dsr.crm_upsert.payloads.REJECTION_REASONS`, so a client can label the
    reason without parsing English.
    """
    partial_ok = connection.partial_upserts_supported(capability)
    planned: list[PlannedRow] = []
    rejected: list[RowOutcome] = []

    for index, record in enumerate(rows):
        data = record.get("data") or {}
        try:
            value = preflight_row(
                data,
                key_source=key_source,
                field_map=field_map,
                key_field=connection.key_field,
                vendor=connection.vendor,
                partial_upserts_supported=partial_ok,
                required_properties=connection.required_properties,
            )
        except RowRejected as exc:
            rejected.append(
                RowOutcome(
                    record_id=str(record.get("id") or ""),
                    key=str(data.get(key_source) or ""),
                    outcome="rejected",
                    errors=[exc.detail],
                    row_index=index,
                )
            )
            continue
        planned.append(
            PlannedRow(record=record, key_value=value, fields=mapped_fields(data, field_map))
        )

    return planned, rejected


# --------------------------------------------------------------------------- #
# Interpreting a response
# --------------------------------------------------------------------------- #


def _error_text(item: Mapping[str, Any]) -> str:
    """The row's own error text, from whichever shape the vendor used.

    Salesforce's ``UpsertResult.errors`` is a list of
    ``{statusCode, message, fields}``; a HubSpot-shaped result may carry a single
    ``message`` or an ``errors`` list of strings. Both are reduced to the
    vendor's own words, because "failures appear in the sync log with the row's
    error text" means the text a rep would see in the CRM.

    The ``statusCode`` is kept alongside the message where there is one. The
    message is what the rep reads; the code is what they paste into the vendor's
    own search, and dropping it would make a sync log less useful than the CRM it
    came from.
    """
    errors = item.get("errors")
    parts: list[str] = []
    if isinstance(errors, (list, tuple)):
        for entry in errors:
            if isinstance(entry, Mapping):
                message = str(entry.get("message") or entry.get("errorCode") or "").strip()
                code = str(entry.get("statusCode") or entry.get("errorCode") or "").strip()
                fields = entry.get("fields")
                suffix = (
                    f" [{', '.join(str(f) for f in fields)}]"
                    if isinstance(fields, (list, tuple)) and fields
                    else ""
                )
                if message and code and code != message:
                    parts.append(f"{message} [{code}]{suffix}")
                elif message:
                    parts.append(f"{message}{suffix}")
                elif code:
                    parts.append(code)
            elif entry not in (None, ""):
                parts.append(str(entry))
    elif errors not in (None, ""):
        parts.append(str(errors))
    if not parts and item.get("message"):
        parts.append(str(item["message"]))
    if not parts and item.get("error"):
        parts.append(str(item["error"]))
    return "; ".join(parts) or "the CRM returned no error text for this row"


def _is_success(item: Mapping[str, Any]) -> bool:
    """Whether a per-item result says the row was written.

    A result with no explicit ``success`` is judged on the absence of errors: a
    vendor that returns ``{"id": ...}`` with no flag has not reported a failure,
    and refusing to count it would lose every row from a vendor whose response
    shape this research does not quote in full.
    """
    if "success" in item:
        return bool(item.get("success"))
    has_errors = bool(item.get("errors")) or bool(item.get("message")) or bool(item.get("error"))
    return not has_errors


def _was_created(item: Mapping[str, Any], status: int) -> bool:
    """Whether a successful result created a record rather than updating one.

    Salesforce says so explicitly in ``created``, and its researched status codes
    agree: "``201`` - 'Created' success code, for POST requests and some PATCH
    requests" versus "``204`` - 'No Content' success code, for DELETE requests and
    some PATCH requests". So a single-row PATCH that answers 201 created
    something and one that answers 204 did not.

    A flag beats a status when both are present, because the flag is the field
    the vendor documents for exactly this question.
    """
    if "created" in item:
        return bool(item.get("created"))
    if "new" in item:
        return bool(item.get("new"))
    return int(status) == 201


def interpret_items(
    results: Sequence[Mapping[str, Any]],
    *,
    expected: int,
    status: int,
    planned: Sequence[PlannedRow],
    chunk_index: int,
) -> tuple[list[RowOutcome], str | None]:
    """Match per-item results to rows **by position**, or fail the whole chunk.

    Returns ``(outcomes, chunk_error)``. A ``chunk_error`` is set when the
    response cannot be trusted to describe the batch - a wrong number of results -
    and every row is then reported as failed with that reason.

    Truncating instead (``zip`` without a length check) would drop the tail of
    every oversized response and quietly leave those rows pending, which is the
    worst possible outcome: the run looks successful and the queue never drains.
    """
    if len(results) != expected:
        text = RESULT_COUNT_MISMATCH.format(got=len(results), expected=expected)
        return (
            [
                RowOutcome(
                    record_id=row.record_id,
                    key=row.key_value,
                    outcome="failed",
                    errors=[text],
                    chunk_index=chunk_index,
                    row_index=index,
                    status=status,
                )
                for index, row in enumerate(planned)
            ],
            text,
        )

    outcomes: list[RowOutcome] = []
    for index, (row, raw) in enumerate(zip(planned, results, strict=True)):
        item = raw if isinstance(raw, Mapping) else {"message": str(raw)}
        record_id = "" if item.get("id") in (None, "") else str(item.get("id"))
        if not _is_success(item):
            outcomes.append(
                RowOutcome(
                    record_id=row.record_id,
                    key=row.key_value,
                    outcome="failed",
                    errors=[_error_text(item)],
                    chunk_index=chunk_index,
                    row_index=index,
                    status=status,
                )
            )
            continue
        outcomes.append(
            RowOutcome(
                record_id=row.record_id,
                key=row.key_value,
                outcome="created" if _was_created(item, status) else "updated",
                crm_record_id=record_id,
                chunk_index=chunk_index,
                row_index=index,
                status=status,
            )
        )
    return outcomes, None


def interpret_single(
    response: OutboundResponse, *, planned_row: PlannedRow, chunk_index: int
) -> RowOutcome:
    """Read one row's outcome from a single-row upsert response.

    The researched status distinction carries the created/updated call here: a
    single-row PATCH that answers 201 created a record because the key was not
    found, and one that answers 204 updated one because it was. Any other 2xx did
    not create.
    """
    body = response.body if isinstance(response.body, Mapping) else {}
    if not response.ok:
        return RowOutcome(
            record_id=planned_row.record_id,
            key=planned_row.key_value,
            outcome="failed",
            errors=[_error_text(body) if body else f"HTTP {response.status}"],
            chunk_index=chunk_index,
            status=response.status,
        )
    return RowOutcome(
        record_id=planned_row.record_id,
        key=planned_row.key_value,
        outcome="created" if _was_created(body, response.status) else "updated",
        crm_record_id=str(body.get("id") or ""),
        chunk_index=chunk_index,
        status=response.status,
    )


def _extract_results(
    response: OutboundResponse,
) -> list[Mapping[str, Any]] | None:
    """Pull the per-item result list out of a response body, or ``None``.

    ``None`` means "this response does not describe the batch", which the caller
    turns into a chunk-level failure. That is a different thing from an empty
    list, which would be zero results against a batch of N rows and is caught by
    the length check instead.
    """
    body = response.body
    if isinstance(body, (list, tuple)):
        return [item if isinstance(item, Mapping) else {"message": str(item)} for item in body]
    if isinstance(body, Mapping):
        for field_name in ("results", "records", "UpsertResult", "Targets"):
            value = body.get(field_name)
            if isinstance(value, (list, tuple)):
                return [
                    item if isinstance(item, Mapping) else {"message": str(item)} for item in value
                ]
        # A single-row result on a bulk request: a body describing one record
        # cannot describe a chunk.
        if "id" in body or "success" in body:
            return None
    return None


def _whole_request_error(response: OutboundResponse) -> str:
    """The error text for a response that carries no per-item detail.

    Salesforce's whole-request errors are a list of ``{errorCode, message}``, so
    both that and a plain ``{"detail": ...}`` are reduced to the vendor's words.
    """
    body = response.body
    if isinstance(body, (list, tuple)):
        return _error_text({"errors": list(body)}) or f"HTTP {response.status}"
    if isinstance(body, Mapping):
        for key in ("error", "message", "detail", "status"):
            if body.get(key):
                return str(body[key])
        return (
            f"HTTP {response.status}: the CRM returned no per-item result for this batch, so no "
            "row in it can be confirmed"
        )
    return f"HTTP {response.status}"


def _interpret_chunk(
    response: OutboundResponse,
    *,
    capability: Capability,
    planned: Sequence[PlannedRow],
    chunk_index: int,
    all_or_none: bool,
) -> tuple[list[RowOutcome], str | None]:
    """Read a whole chunk's response, applying the researched vendor rules."""
    if not capability.returns_per_item_results:
        # "The `UpsertMultiple` action returns `204 NoContent`." There is nothing
        # to read, so every row is `submitted` and no row claims a record id.
        return (
            [
                RowOutcome(
                    record_id=row.record_id,
                    key=row.key_value,
                    outcome="submitted",
                    chunk_index=chunk_index,
                    row_index=index,
                    status=response.status,
                )
                for index, row in enumerate(planned)
            ],
            None,
        )

    results = _extract_results(response)
    if results is None:
        text = _whole_request_error(response)
        return (
            [
                RowOutcome(
                    record_id=row.record_id,
                    key=row.key_value,
                    outcome="failed",
                    errors=[text],
                    chunk_index=chunk_index,
                    row_index=index,
                    status=response.status,
                )
                for index, row in enumerate(planned)
            ],
            text,
        )

    produced, chunk_error = interpret_items(
        results,
        expected=len(planned),
        status=response.status,
        planned=planned,
        chunk_index=chunk_index,
    )
    if chunk_error is not None:
        return produced, chunk_error

    if all_or_none and (any(entry.outcome == "failed" for entry in produced) or not response.ok):
        # "You can choose whether to roll back the entire request when an error
        # occurs." Under allOrNone a single failure means nothing in this chunk
        # was written, so the rows that "succeeded" are rolled back too and none
        # of them may claim a record id. Recording them as successes would invent
        # CRM state that does not exist.
        reason = (
            ROLLBACK_REASON
            if response.ok
            else f"{ROLLBACK_REASON} (the request answered HTTP {response.status})"
        )
        rolled: list[RowOutcome] = []
        for entry in produced:
            if entry.outcome == "failed":
                rolled.append(entry)
                continue
            rolled.append(
                RowOutcome(
                    record_id=entry.record_id,
                    key=entry.key,
                    outcome="rolled_back",
                    errors=[reason],
                    chunk_index=entry.chunk_index,
                    row_index=entry.row_index,
                    status=entry.status,
                )
            )
        return rolled, reason

    return produced, None


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #


@dataclass
class RunResult:
    """The whole run: the plan, every request, every row, and the totals.

    Returned in full rather than summarised. The researched UI is a progress bar
    plus a per-row error list, and a progress bar needs the chunk count while a
    per-row list needs the rows; the sync log is this object, stored.
    """

    run_id: str
    record: dict[str, Any]
    outcomes: list[RowOutcome]
    requests: list[OutboundRequest]
    progress: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.record,
            "progress": self.progress,
            "outcomes": [entry.to_dict() for entry in self.outcomes],
        }


def _totals(outcomes: Sequence[RowOutcome]) -> dict[str, int]:
    """The counts a rep reads, plus two derived ones the researched flow implies.

    ``confirmed`` is what the CRM actually confirmed - created plus updated.
    ``needs_attention`` is everything else that is not merely unconfirmed:
    a failed, rejected, or rolled-back row is work a person owns, whereas an
    unconfirmed row is work the CRM owns.
    """
    counts = {outcome: 0 for outcome in OUTCOMES}
    for entry in outcomes:
        counts[entry.outcome] = counts.get(entry.outcome, 0) + 1
    counts["rows"] = len(outcomes)
    counts["confirmed"] = counts["created"] + counts["updated"]
    counts["needs_attention"] = counts["failed"] + counts["rejected"] + counts["rolled_back"]
    return counts


@dataclass(frozen=True)
class _Plan:
    """Everything a run resolves once, before it sends anything."""

    capability: Capability
    capability_source: str
    mode: str
    size: int
    key_source: str
    api_version: str
    object_name: str


def _plan(store: RecordStore, connection: Connection, config: Mapping[str, Any]) -> _Plan:
    """Resolve the capability, the batch size, and the mode, or refuse.

    Done before the first request so an unsatisfiable connection - a batch size
    the vendor does not accept, a key the vendor cannot key on, a table with
    neither a bulk nor a single upsert - is an error at the door rather than a
    run that sends 200 rows and fails.

    An unsaved connection is refused here too, and not for tidiness: the queue is
    keyed on the connection id, so an unsaved one would fall back to the
    row-level state and quietly share a queue with every other connection on the
    room - re-sending rows another connection had already handled. That is the
    bug :func:`dsr.crm_upsert.connections.connection_state` exists to prevent, and
    it is cheaper to refuse than to reproduce.
    """
    if not connection.id:
        raise UpsertError(
            "this connection has not been saved, so the queue cannot be scoped to it. "
            "Save the connection before running or previewing an upsert."
        )
    resolved = connection.resolved_capability(store)
    size = connection.effective_batch_size(store)
    capability = with_batch_size(resolved.capability, size)
    return _Plan(
        capability=capability,
        capability_source=resolved.source,
        mode=resolve_mode(capability),
        size=size,
        key_source=connection.key_source
        or str((config.get("mapping") or {}).get("key_source") or ""),
        api_version=connection.api_version or str(config.get("api_version") or ""),
        object_name=connection.target(),
    )


def _select(
    store: RecordStore,
    connection: Connection,
    config: Mapping[str, Any],
    plan: _Plan,
    *,
    room_id: str,
    limit: int | None,
) -> tuple[list[PlannedRow], list[RowOutcome]]:
    """The queue, then the preflight split."""
    rows = pending_rows(store, connection, config, room_id=room_id, limit=limit)
    return preflight(
        rows,
        connection=connection,
        capability=plan.capability,
        key_source=plan.key_source,
        field_map=connection.fields,
    )


def _chunk_record(
    index: int,
    request: OutboundRequest,
    response: OutboundResponse,
    produced: Sequence[RowOutcome],
    chunk_error: str | None,
    rows_done: int,
    rows_total: int,
) -> dict[str, Any]:
    """One chunk's entry in the sync log, as the reviewer reads it.

    Carries the full request, not a summary of it: the researched rules are about
    the request's *shape*, and a log that said "sent 200 rows" could not show
    that a chunk had carried a record id.
    """
    return {
        "index": index,
        "method": request.method,
        "path": request.path,
        "query": dict(request.query),
        "body": request.to_dict()["body"],
        "row_indexes": list(request.row_indexes),
        "size": len(produced),
        "status": response.status,
        "error": chunk_error,
        "outcomes": {
            name: sum(1 for entry in produced if entry.outcome == name)
            for name in ("created", "updated", "submitted", "failed", "rolled_back")
        },
        "progress": {"rows_done": rows_done, "rows_total": rows_total},
    }


def _progress(
    driver: str, chunks: Sequence[Mapping[str, Any]], totals: Mapping[str, int], rows_sent: int
) -> dict[str, Any]:
    """The block a progress bar renders.

    ``percent`` is over rows actually put on the wire, not over rows selected: a
    run that rejected 40 of 200 rows before sending anything has finished, and a
    bar stuck at 80% because of rows it deliberately never sent would be lying.
    """
    sendable = totals["rows"] - totals["rejected"]
    percent = 100.0 if sendable <= 0 else round(min(100.0, rows_sent / sendable * 100.0), 1)
    return {
        "chunks_total": len(chunks),
        "chunks_done": len(chunks),
        "rows_total": totals["rows"],
        "rows_sent": rows_sent,
        "rows_rejected": totals["rejected"],
        "rows_confirmed": totals["confirmed"],
        "rows_unconfirmed": totals["submitted"],
        "rows_needing_attention": totals["needs_attention"],
        "percent": percent,
        "driver": driver,
    }


def preview(
    store: RecordStore,
    connection: Connection,
    config: Mapping[str, Any],
    *,
    room_id: str,
    limit: int | None = None,
) -> dict[str, Any]:
    """The requests a run *would* send, without sending any of them.

    This is the honest way to review the researched request shapes: the same
    builder the run uses, so what a reviewer reads here is what goes over the
    wire. It is also what makes the payload rules testable without a credential
    and a network.
    """
    plan = _plan(store, connection, config)
    planned, rejected = _select(store, connection, config, plan, room_id=room_id, limit=limit)

    requests: list[dict[str, Any]] = []
    if plan.mode == "bulk":
        for index, group in enumerate(chunk_indexed(planned, plan.size)):
            requests.append(
                build_bulk_request(
                    plan.capability,
                    object_name=plan.object_name,
                    key_field=connection.key_field,
                    entries=[(row.key_value, row.fields) for _position, row in group],
                    all_or_none=connection.all_or_none,
                    api_version=plan.api_version,
                    chunk_index=index,
                    row_indexes=[position for position, _row in group],
                ).to_dict()
            )
    else:
        for index, row in enumerate(planned):
            requests.append(
                build_single_request(
                    plan.capability,
                    object_name=plan.object_name,
                    key_field=connection.key_field,
                    key_value_=row.key_value,
                    fields=row.fields,
                    update_only=connection.update_only,
                    api_version=plan.api_version,
                    chunk_index=index,
                    row_indexes=[index],
                ).to_dict()
            )

    return {
        "connection_id": connection.id,
        "room_id": room_id,
        "mode": plan.mode,
        "vendor": plan.capability.vendor,
        "capability_source": plan.capability_source,
        "object": plan.object_name,
        "key_field": connection.key_field,
        "key_source": plan.key_source,
        "batch_size": plan.size,
        "all_or_none": connection.all_or_none,
        "update_only": connection.update_only,
        "pending": len(planned) + len(rejected),
        "planned": len(planned),
        "rejected": [entry.to_dict() for entry in rejected],
        "chunks": len(requests),
        "requests": requests,
        "sends_nothing": True,
    }


def run_upsert(
    store: RecordStore,
    connection: Connection,
    config: Mapping[str, Any],
    *,
    room_id: str,
    transport: Transport,
    actor: str | None = None,
    source: str,
    limit: int | None = None,
    driver: str = "manual",
    now: datetime | None = None,
) -> RunResult:
    """Run the researched upsert over a room's pending rows.

    The order is the flow's: select the queue, plan it, chunk it, send one
    request per chunk, interpret per position, then write back per row and record
    the log. The run record is written **last**, so a log entry never describes a
    run that did not finish, and every row write-back carries the same ``source``
    the route supplied - the audit row names the route that actually served it.

    The transport's own ``note`` is recorded on the run. A transport that
    simulates a CRM says so there, so a reviewer reading the sync log can never
    mistake a simulated confirmation for one a real CRM gave.
    """
    if driver not in DRIVERS:
        raise UpsertError(f"driver must be one of {list(DRIVERS)}, got {driver!r}")

    moment = now or datetime.now(timezone.utc)
    started = time.perf_counter()
    plan = _plan(store, connection, config)
    planned, outcomes = _select(store, connection, config, plan, room_id=room_id, limit=limit)
    rows_total = len(planned) + len(outcomes)

    # Minted before the write-back so a row's nested state can name the run that
    # produced it. The record is still *written* last, so a log entry never
    # describes a run that did not finish.
    run_id = new_id(COLLECTION_RUN)

    sent: list[OutboundRequest] = []
    chunks: list[dict[str, Any]] = []
    rows_sent = 0

    if plan.mode == "bulk":
        for index, group in enumerate(chunk_indexed(planned, plan.size)):
            positions = [position for position, _row in group]
            request = build_bulk_request(
                plan.capability,
                object_name=plan.object_name,
                key_field=connection.key_field,
                entries=[(row.key_value, row.fields) for _position, row in group],
                all_or_none=connection.all_or_none,
                api_version=plan.api_version,
                chunk_index=index,
                row_indexes=positions,
            )
            sent.append(request)
            response = transport.send(request)
            produced, chunk_error = _interpret_chunk(
                response,
                capability=plan.capability,
                planned=[row for _position, row in group],
                chunk_index=index,
                all_or_none=connection.all_or_none,
            )
            outcomes.extend(produced)
            rows_sent += len(group)
            chunks.append(
                _chunk_record(
                    index, request, response, produced, chunk_error, rows_sent, rows_total
                )
            )
    else:
        for index, row in enumerate(planned):
            request = build_single_request(
                plan.capability,
                object_name=plan.object_name,
                key_field=connection.key_field,
                key_value_=row.key_value,
                fields=row.fields,
                update_only=connection.update_only,
                api_version=plan.api_version,
                chunk_index=index,
                row_indexes=[index],
            )
            sent.append(request)
            response = transport.send(request)
            produced = [interpret_single(response, planned_row=row, chunk_index=index)]
            outcomes.extend(produced)
            rows_sent += 1
            chunks.append(
                _chunk_record(index, request, response, produced, None, rows_sent, rows_total)
            )

    written = _write_back(
        store,
        outcomes,
        connection=connection,
        room_id=room_id,
        actor=actor,
        source=source,
        moment=moment,
        run_id_hint=run_id,
    )
    totals = _totals(outcomes)
    finished = datetime.now(timezone.utc)

    record = store.create(
        COLLECTION_RUN,
        {
            "connection_id": connection.id,
            "connection_name": connection.name or connection.object,
            "driver": driver,
            "mode": plan.mode,
            "vendor": plan.capability.vendor,
            "capability_source": plan.capability_source,
            "object": plan.object_name,
            "key_field": connection.key_field,
            "key_type": connection.key_type or connection.effective_key_type(store),
            "key_source": plan.key_source,
            "batch_size": plan.size,
            "all_or_none": connection.all_or_none,
            "update_only": connection.update_only,
            "chunks": chunks,
            "totals": totals,
            "rows_written_back": written,
            "transport": str(getattr(transport, "note", "") or "external"),
            "started_at": moment.isoformat(timespec="seconds"),
            "finished_at": finished.isoformat(timespec="milliseconds"),
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        },
        record_id=run_id,
        room_id=room_id,
        actor=actor,
        source=source,
    )

    return RunResult(
        run_id=str(record["id"]),
        record=record,
        outcomes=outcomes,
        requests=sent,
        progress=_progress(driver, chunks, totals, rows_sent),
    )


def _write_back(
    store: RecordStore,
    outcomes: Sequence[RowOutcome],
    *,
    connection: Connection,
    room_id: str,
    actor: str | None,
    source: str,
    moment: datetime,
    run_id_hint: str = "",
) -> int:
    """Write each row's outcome back onto the room's engagement row.

    "Per-row outcomes are written back to the room" and the data flow says "room
    updates ``synced_at`` / ``crm_record_id`` per row". Which fields move depends
    on the outcome, and the differences are the point:

    * ``created`` / ``updated`` - the CRM confirmed a record, so ``synced_at`` and
      ``crm_record_id`` are set. The record id only ever comes from a response;
      it is never invented here.
    * ``submitted`` - sent, no confirmation available. ``sent_at`` is set so the
      row leaves the queue, and ``synced_at`` and ``crm_record_id`` are not,
      because the connector has no evidence either way.
    * ``failed`` / ``rejected`` / ``rolled_back`` - the row is still unsent, so
      it goes back in the queue. It keeps a distinct status (``failed`` for one
      the CRM refused, ``pending`` for one this connector refused) rather than
      being flattened, so a rep can see which rows have already been tried, and
      ``synced_at`` is explicitly cleared so a row that once synced does not read
      as synced now. The error text is stored on the row as well as in the log.

    Two places are written, deliberately:

    * ``sync.<connection_id>`` - this connection's state, which is what the queue
      filters on. A room wired to two CRMs keeps two independent states on the
      same row.
    * the row-level ``sync_status`` / ``synced_at`` / ``crm_record_id`` - the
      researched field names, recorded as the row's most recent outcome whatever
      connection produced it. They are the convenient read; the nested state is
      the authoritative one for the queue.

    ``store.update`` merges shallowly, so the whole ``sync`` map is read and
    written back: writing ``sync.<id>`` alone would silently drop a sibling
    connection's state.

    A row that keeps failing is retried on the next run. This workflow does not
    own a retry policy - delivery retries with backoff are WF-016's subject - so
    the queue itself is the mechanism, and the reason is left on the row each
    time.
    """
    stamp = moment.isoformat(timespec="milliseconds")
    connection_id = str(connection.id or "")
    written = 0

    # One read per distinct row, not per outcome, and the existing map is carried
    # forward so a sibling connection's state survives this write.
    existing: dict[str, Any] = {}
    for entry in outcomes:
        if not entry.record_id or entry.record_id in existing:
            continue
        record = store.get(entry.record_id)
        data = (record or {}).get("data") or {}
        current = data.get("sync")
        existing[entry.record_id] = dict(current) if isinstance(current, Mapping) else {}

    for entry in outcomes:
        if not entry.record_id:
            continue
        status = _status_for(entry.outcome)
        nested: dict[str, Any] = {
            "status": status,
            "outcome": entry.outcome,
            "errors": list(entry.errors),
            "last_attempt_at": stamp,
        }
        if entry.crm_record_id:
            nested["crm_record_id"] = entry.crm_record_id
        if entry.outcome in ("created", "updated"):
            nested["synced_at"] = stamp
        elif entry.outcome == "submitted":
            nested["sent_at"] = stamp
        if run_id_hint:
            nested["run_id"] = run_id_hint

        patch: dict[str, Any] = {
            "sync_status": status,
            "sync_outcome": entry.outcome,
            "sync_errors": list(entry.errors),
            "last_attempt_at": stamp,
        }
        if entry.outcome in ("created", "updated"):
            patch["synced_at"] = stamp
            if entry.crm_record_id:
                patch["crm_record_id"] = entry.crm_record_id
        elif entry.outcome == "submitted":
            patch["sent_at"] = stamp
        else:
            # Explicitly cleared: a row that once synced and now failed must not
            # keep reading as synced to a list view that only looks at this field.
            patch["synced_at"] = None
        if connection_id:
            merged = dict(existing.get(entry.record_id) or {})
            merged[connection_id] = nested
            patch["sync"] = merged

        try:
            store.update(entry.record_id, patch, actor=actor, source=source)
            written += 1
        except RecordNotFound:
            # A row deleted between selection and write-back. The other rows'
            # outcomes are already decided and the log is still the record of
            # them, so this is reported on the outcome rather than aborting the run.
            entry.outcome = "failed"
            entry.errors.append("the room row was deleted before its outcome could be written back")
    return written


def _status_for(outcome: str) -> str:
    """The row's ``sync_status`` for an outcome.

    The only two states a *landed* row can end in are ``synced`` (the CRM
    confirmed it) and ``unconfirmed`` (sent, nothing to confirm it with).

    A row that failed keeps ``failed`` and a row this connector refused keeps
    ``pending``, because the two mean different things to whoever has to act:
    one was turned down by the CRM and may now succeed, the other cannot be sent
    until somebody fixes the row. Both are in
    :data:`~dsr.crm_upsert.connections.UNSENT_STATUSES`, so the queue picks up
    either - the researched flow's own mechanism, since this workflow names no
    retry and no give-up rule.
    """
    if outcome in ("created", "updated"):
        return STATUS_SYNCED
    if outcome == "submitted":
        return STATUS_UNCONFIRMED
    if outcome == "failed":
        return STATUS_FAILED
    return STATUS_PENDING


# --------------------------------------------------------------------------- #
# Reading runs back
# --------------------------------------------------------------------------- #


def list_runs(
    store: RecordStore,
    *,
    room_id: str | None = None,
    connection_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """The sync log, newest first.

    ``store.find`` filters on ``data`` paths only and cannot scope to a room,
    because ``room_id`` is envelope rather than payload. So whichever lookup runs
    first, the room is enforced against the envelope afterwards - the one place
    ``room_id`` is guaranteed to be right.
    """
    if connection_id:
        records = store.find(COLLECTION_RUN, {"connection_id": connection_id}, limit=limit)
    else:
        records = store.list(COLLECTION_RUN, room_id=room_id, limit=limit)
    if room_id:
        records = [record for record in records if record.get("room_id") == room_id]
    return records


def load_run(store: RecordStore, run_id: str) -> dict[str, Any]:
    """One run by id, or :class:`~dsr.crm_upsert.errors.UnknownRun`."""
    record = store.get(run_id)
    if record is None or record.get("collection") != COLLECTION_RUN:
        raise UnknownRun(run_id)
    return record
