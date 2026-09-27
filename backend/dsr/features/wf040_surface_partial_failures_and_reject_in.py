"""WF-040: surface partial failures, and reject invalid writes before commit.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-040.md``. The domain
logic is in :mod:`dsr.partial_failures`; this module is the three things a workflow
needs in order to exist in this product: the HTTP surface under a prefix of its
own, the mapping from its domain errors to responses, and the demo data.

What the research specifies, and where each piece of it landed
---------------------------------------------------------------

*Step two, "the connector asks for per-record outcomes".* ``GET /connectors``
serves, per vendor, the request that produces them, the status it answers with,
the key that correlates a result back to a row, and the researched quotation:
HubSpot's ``207 Multi-Status`` with a unique ``objectWriteTraceId`` per input,
Dataverse's ``Prefer: odata.continue-on-error`` (which answers ``200 OK`` and puts
the failures in the body), and Salesforce's ``allOrNone: false``.

*Step three, "the Sync log lists each failed row with a human-readable reason and
the offending property".* ``GET /rooms/{room_id}/sync-log``, filterable by status,
disposition, connector, run and property, with the summary computed over exactly
the rows returned so a filtered view does not report totals for the whole log.

*Step four, "Field-level error detail (which property, what was sent, what was
expected)".* ``GET /rooms/{room_id}/rows/{row_id}``. When the vendor names no
property the room supplies one from its own validation metadata and says which of
the two it used, because "the log names a property" and "the log names the
vendor's property" are two different claims.

*Step five, "Retry failed rows only - successes are not re-sent".*
``POST /runs/{run_id}/retry``. The failed rows of the run are the normaliser's
input and nothing else, so a succeeded row cannot be re-sent even by accident.

*The automation, "the retry queue drains automatically for retryable classes and
waits for admin action for validation failures".* ``GET /queue`` shows both
waiting states and what is due; ``POST /queue/drain`` moves rows that have
exhausted the bound out of the automatic queue, and reports the batch that is due.
The room opens no socket: every ``apis_hit`` in the research is a call a connector
makes, and this product holds no vendor credentials.

*The other half of the title, "reject invalid writes before commit".*
``POST /validate`` checks a proposed batch against the room's own rules and
returns which rows must not be sent. It writes nothing - a refused row costs no
CRM call, does not appear in the Sync log, and is not something the retry action
will ever try to re-send, because it was never sent. The rules are a record a
deployment extends, which is the researched extension point: "a deployment can add
a rule ... without changing the transport".

Choices the research left open
-------------------------------

They are collected in :mod:`dsr.partial_failures.inferences` and served at
``GET /inferences`` rather than buried in comments: which codes count as the
researched "rate limit" and "locked" classes, what happens to a code this build
has never heard of, how a Dataverse or Salesforce result is correlated when the
source set names no key, and how long a row's retry history lives.

The two things this module is careful about, because both have gone wrong in this
codebase before:

**``source`` is built from ``router.prefix`` and required by the domain layer.**
Every write below names the route that served it, so an audit row can be traced to
a request. A domain method with a hardcoded string as its ``source`` is a defect,
and ``source`` having no default is what stops it regressing.

**Dependencies come from ``dsr.deps``.** This module never imports ``dsr.api``; a
feature that reaches for the app reintroduces exactly the coupling the plugin host
exists to remove.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.partial_failures import (
    CONNECTORS,
    DISPOSITIONS,
    ROW_STATUSES,
    PartialFailureError,
    SyncLog,
    UnknownRoom,
    UnknownRow,
    UnknownRun,
    describe_inferences,
    vocabulary,
)
from dsr.partial_failures.log import HISTORY_LIMIT
from dsr.partial_failures.vocabulary import CONNECTOR_LABELS, ROW_COLLECTION
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-040-surface-partial-failures-and-reject-in",
    "ticket": "WF-040",
    "name": "Surface partial failures and reject invalid writes before commit",
    "description": (
        "Per-record outcomes from HubSpot, Dataverse and Salesforce, normalised into one room "
        "error model. A Sync log names each failed row with the offending property, an admin can "
        "see what was sent against what was expected, and a retry re-sends the failed rows only."
    ),
    "nav": [{"id": "sync-log", "label": "Sync log"}],
}

router = APIRouter(prefix="/api/wf-040", tags=["wf-040"])


def get_sync_log(store: RecordStore = StoreDep) -> SyncLog:
    """A :class:`SyncLog` over the process-wide audited store.

    Built per request rather than held on ``app.state``: the class holds nothing
    but the store handle, and ``app.state`` is a shared file this feature must not
    edit. It also leaves the whole workflow unit-testable against a temporary
    database without the app running.
    """
    return SyncLog(store)


LogDep = Depends(get_sync_log)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _partial_failure_error(request: Request, exc: PartialFailureError) -> JSONResponse:
    """A refusal from this workflow, as a response.

    One registered handler for the whole hierarchy, branching on the type rather
    than registering six: :class:`PartialFailureError` is a type this workflow
    owns, so a global registration for it cannot intercept an exception raised
    anywhere else in the product, and a hierarchy that grows a seventh refusal
    next month does not need a seventh registration.

    A room, run or row that does not exist is 404 because that is what it is.
    Everything else is 400: a well-formed request asking for something this layer
    will not do - a connector outside the researched three, a payload with no rows
    in it, a rules patch that would produce a configuration nothing can enforce.
    An unknown connector is a 400 rather than a 404 for that reason: it is a bad
    value in a request, not a resource that is missing, and the client that sent it
    can fix it without looking anything up.

    ``RecordNotFound`` is deliberately not claimed: the core app already maps it to
    404, and two handlers for one type is a collision the host refuses.
    """
    status = 404 if isinstance(exc, (UnknownRoom, UnknownRun, UnknownRow)) else 400
    return JSONResponse(
        status_code=status,
        content={"error": type(exc).__name__, "detail": str(exc)},
    )


EXCEPTION_HANDLERS = {PartialFailureError: _partial_failure_error}


# --------------------------------------------------------------------------- #
# Vocabulary and inferences
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="Every published name, as data")
def sync_vocabulary() -> dict[str, Any]:
    """Connectors, row statuses, dispositions, the error model, the collections.

    Served so a client renders its status chips, its pickers and its error detail
    from the same source the normaliser validates against. A name added here
    reaches every client at once, and no client compiles a list that can drift.
    """
    published = vocabulary()
    published["history_limit"] = HISTORY_LIMIT
    return published


@router.get("/connectors", summary="What each vendor must be asked for")
def connectors() -> dict[str, Any]:
    """Per-connector: the request that yields per-record outcomes, and its gap.

    The researched step two, made concrete per vendor and kept honest. Each
    descriptor carries the request headers, the status the vendor answers with, the
    correlation key, where a doc link comes from, and - importantly - the ``gap``
    the research states about that vendor. HubSpot's descriptor says ``batch_create``
    and not ``batch/upsert`` because the research explicitly did not confirm the
    latter, and a connector that relies on it should be able to see that here rather
    than discover it in production.

    A read with no side effect, so it needs no store.
    """
    published = vocabulary()
    return {
        "count": len(published["connectors"]),
        "connectors": published["connectors"],
        "ids": list(CONNECTORS),
        "labels": dict(CONNECTOR_LABELS),
        "hubspot_validation_enforcement": published["hubspot_validation_enforcement"],
        "error_model_keys": published["error_model_keys"],
    }


@router.get("/inferences", summary="What the research left to this build")
def sync_inferences() -> dict[str, Any]:
    """Every judgement call in the workflow, named, bounded and changeable.

    The research quotes three vendors' statuses, two error bodies, two request
    headers and one annotation, and it states its own gap. Everything that follows
    from the silences - which codes are the researched retryable classes, what a
    row the response ignores becomes, how long a retry history lives - is here, so
    a reviewer can disagree with a named entry instead of hunting through a diff.
    """
    return describe_inferences()


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #


@router.get("/rules", summary="The rules in force")
def read_rules(log: SyncLog = LogDep) -> dict[str, Any]:
    """The classification overrides, the pre-flight rules, and the attempt bound.

    ``source`` distinguishes the shipped defaults from a stored override, so a
    client can say which model it is reading rather than presenting a tuned
    threshold as though the source published it. ``stored`` is served alongside, so
    a configuration that has become unreadable is visible rather than papered over.
    """
    return log.rules_view()


@router.patch("/rules", summary="Retune the rules without a redeploy")
def update_rules(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    log: SyncLog = LogDep,
) -> dict[str, Any]:
    """Store a rules override. A partial patch, and the write is audited.

    This is the researched extension point: "a deployment can add a rule ... without
    changing the transport". Adding a pre-flight rule merges by id rather than
    replacing the list, so retuning the shipped Dataverse length limit does not
    silently drop the two shipped email rules, and an unknown rule kind is refused
    rather than stored - a rule that silently does nothing looks exactly like a rule
    that passes.

    Nothing here is a migration or a column. The rules are a record, which is how a
    team ships a different validation model without coordinating with anyone.
    """
    return log.save_rules(payload, actor=actor, source=f"PATCH {router.prefix}/rules")


# --------------------------------------------------------------------------- #
# Before the commit
# --------------------------------------------------------------------------- #


@router.post("/validate", summary="Reject invalid writes before the batch is sent")
def validate(
    payload: dict[str, Any] = Body(default_factory=dict),
    log: SyncLog = LogDep,
) -> dict[str, Any]:
    """Which rows of a proposed batch may be sent, and which must not be.

    The "reject invalid writes before commit" half of this workflow, and it is
    deliberately a write-free call. A row refused here never reaches a CRM, never
    appears in the Sync log - because a row in the log means a vendor answered, and
    no request was sent - and is not something ``/runs/{run_id}/retry`` will try to
    re-send. Each refusal says which property, what was sent, and what was expected,
    which is the same detail the Sync log shows for a row the CRM refused.

    Called with the rows a connector is about to send, this is the last point at
    which a bad mapping costs nothing.
    """
    return log.preflight(payload)


# --------------------------------------------------------------------------- #
# Recording a batch
# --------------------------------------------------------------------------- #


@router.get("/runs", summary="Sync runs, newest first")
def list_runs(
    room_id: str | None = Query(default=None, description="One room; omitted means every room"),
    connector: str | None = Query(default=None, description=f"One of {', '.join(CONNECTORS)}"),
    limit: int = Query(default=100, ge=1, le=1000),
    log: SyncLog = LogDep,
) -> dict[str, Any]:
    """Every recorded batch, with the counts a rep reads them for.

    The ``by_connector`` breakdown is over the *whole* filtered set rather than the
    page, so a paginated list does not report a different picture from a short one.
    """
    return log.list_runs(room_id=room_id, connector=connector, limit=limit)


@router.post("/runs", status_code=201, summary="Record one sync run and its per-row outcomes")
def record_run(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None, description="Overrides the room named in the body"),
    actor: str | None = Query(default=None),
    log: SyncLog = LogDep,
) -> dict[str, Any]:
    """Hand the room a batch result: the rows that were sent, and what came back.

    ``vendor.status`` and ``vendor.body`` are the connector's own response, read
    through the same normaliser for all three vendors. The run records the
    per-record outcomes, the vendor's own error count against how many errors were
    actually described, any error that could not be keyed to a row, and the
    pre-flight verdict the room reached - so a batch that went out carrying a row the
    room would have refused is visible on the run that proved it.

    ``room_id`` on the query string wins over the body, as it does everywhere else
    in this product.
    """
    result = log.record_run(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/runs"
    )
    return result


@router.get("/runs/{run_id}", summary="One run, with its per-row outcomes")
def read_run(
    run_id: str,
    room_id: str | None = Query(default=None, description="Require the run to be on this room"),
    log: SyncLog = LogDep,
) -> dict[str, Any]:
    """The batch, the notes explaining anything surprising about it, and every row.

    ``notes`` is where the three facts a reader most needs live: that a Dataverse
    200 was not a success, that an error could not be keyed to a row, and that the
    vendor's own count disagreed with what it described.
    """
    return log.get_run(run_id, room_id=room_id)


@router.post("/runs/{run_id}/retry", summary="Retry failed rows only")
def retry_run(
    run_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    log: SyncLog = LogDep,
) -> dict[str, Any]:
    """The researched admin action: "Retry failed rows only - successes are not re-sent".

    The failed rows of this run are the normaliser's input and nothing else, so a
    row that succeeded cannot be re-sent even by accident; the succeeded rows come
    back in ``untouched`` so the caller can see they were considered and excluded.

    Called with no body it returns the rows to re-send. Called with ``vendor`` it
    applies the connector's response through the same normaliser a first-time batch
    goes through, so a retried row and a new row are classified by one piece of
    code. A row that succeeds on a retry becomes ``resolved``; one that fails again
    keeps its attempt count, because the row *was* sent and a counter that only
    moved on success would never reach the bound that ends the automatic drain.

    Not capped by ``max_attempts``: that bounds the automatic queue, and an admin
    who has just fixed the mapping is answering a question the queue cannot.
    """
    return log.retry_failed(
        run_id, payload, actor=actor, source=f"POST {router.prefix}/runs/{run_id}/retry"
    )


# --------------------------------------------------------------------------- #
# The retry queue
# --------------------------------------------------------------------------- #


@router.get("/queue", summary="What drains automatically, and what waits for a person")
def read_queue(
    room_id: str | None = Query(default=None, description="One room; omitted means every room"),
    connector: str | None = Query(default=None, description=f"One of {', '.join(CONNECTORS)}"),
    as_of: str | None = Query(default=None, description="ISO instant to evaluate the queue against"),
    limit: int = Query(default=1000, ge=1, le=1000),
    log: SyncLog = LogDep,
) -> dict[str, Any]:
    """The researched automation, made inspectable.

    Four lists, because each asks a different question of the next person to look:
    ``drain`` is due now, ``scheduled`` is retryable but still backing off, ``expired``
    has run out of attempts and is about to need a person, and ``waiting`` is every
    terminal row that no amount of draining touches.

    ``as_of`` answers the question for a past instant, so "what did the queue look
    like at the end of last week's batch" is answerable rather than reconstructed.
    """
    return log.queue(room_id=room_id, connector=connector, as_of=as_of, limit=limit)


@router.post("/queue/drain", summary="The researched automation: drain what clears on its own")
def drain_queue(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None, description="One room; omitted means every room"),
    connector: str | None = Query(default=None, description=f"One of {', '.join(CONNECTORS)}"),
    as_of: str | None = Query(default=None, description="ISO instant to evaluate the queue against"),
    actor: str | None = Query(default=None),
    log: SyncLog = LogDep,
) -> dict[str, Any]:
    """Drain the retry queue, and stop when a row stops being retryable.

    Two things happen, and they are separate because they are separate jobs. Rows
    that have exhausted ``max_attempts`` move to ``needs_action`` - that is the only
    write here, and it is the one that makes the researched drain terminate. Rows
    that are retryable, under the bound and past their backoff are returned in
    ``sent`` for the connector to transmit.

    The room does not transmit them. Every ``apis_hit`` in the research is a call a
    connector makes, and this product holds no vendor credentials, so the endpoint
    is the seam: send ``sent``, then post the response back as ``vendor`` and it is
    applied through the same normaliser. Passing no body is the scheduler-friendly
    half, and it is what makes the automation inspectable without a job.

    A response covering rows from more than one connector is refused rather than
    guessed at: a HubSpot response cannot be applied to a Dataverse row.
    """
    return log.drain(
        payload, room_id=room_id, connector=connector, actor=actor, as_of=as_of,
        source=f"POST {router.prefix}/queue/drain",
    )


# --------------------------------------------------------------------------- #
# The Sync log
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/sync-log", summary="The room's Sync log, with per-row status")
def sync_log(
    room_id: str,
    status: str | None = Query(default=None, description=f"One of {', '.join(ROW_STATUSES)}"),
    disposition: str | None = Query(default=None, description=f"One of {', '.join(DISPOSITIONS)}"),
    connector: str | None = Query(default=None, description=f"One of {', '.join(CONNECTORS)}"),
    run_id: str | None = Query(default=None),
    field: str | None = Query(default=None, description="The offending property"),
    limit: int = Query(default=100, ge=1, le=1000),
    log: SyncLog = LogDep,
) -> dict[str, Any]:
    """The researched Sync log: one row per outcome, newest attempt first.

    Every filter is a value the room already stores on the row, resolved through the
    dynamic index where the store can and from the row's own payload where it
    cannot, so a new filter is a query parameter and not a schema change. The
    summary is computed over exactly the rows returned, so a filtered view does not
    report totals for the whole log.
    """
    return log.sync_log(
        room_id,
        status=status,
        disposition=disposition,
        connector=connector,
        run_id=run_id,
        field=field,
        limit=limit,
    )


@router.get("/rooms/{room_id}/rows/{row_id}", summary="Field-level error detail for one row")
def read_row(
    room_id: str,
    row_id: str,
    log: SyncLog = LogDep,
) -> dict[str, Any]:
    """Which property, what was sent, what was expected - and why it is classified that way.

    The researched detail view. ``property_source`` says whether the vendor named
    the property or the room supplied it from its own validation metadata, and
    ``basis`` says which entry decided whether the failure is retried - both so a
    rep can act and both so a reviewer can check the classification without reading
    the code.
    """
    return log.row_detail(room_id, row_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The five runs the demo seeds, in the order they are written.
#:
#: A demo of only clean syncs would teach a reviewer nothing about the thing this
#: workflow exists for, so the plan deliberately covers the states that are hard:
#:
#:   room 0  HubSpot 207 Multi-Status, 3 rows. One succeeded, one refused by a
#:           validation rule with the property named in the per-item error, and
#:           one error carrying a trace id that names no row in the batch. That
#:           last one is the interesting row: it is kept, unattributed, on the
#:           run, because a dropped error is a row nobody will ever fix.
#:   room 1  Dataverse $batch answering 200 OK with two failures in the body. One
#:           is the researched 0x80044331 naming 'subject' over 200, with the
#:           HelpLink annotation beside it; one is a 412 ConcurrencyVersionMismatch.
#:           The run also carries the room's own pre-flight note, because the
#:           over-long subject is a write the room's shipped rules would have
#:           refused before the batch was sent.
#:   room 2  Salesforce answering 403 REQUEST_LIMIT_EXCEEDED for the whole batch.
#:           Both rows are queued, and the seed then runs one real drain: one row
#:           is accepted and becomes resolved, one is still throttled and is still
#:           queued. The automatic automation, doing both halves of its job.
#:   room 3  Two HubSpot runs: one where every row succeeded, so a reviewer can see
#:           what clean looks like and can confirm the retry action leaves the
#:           successes alone; and one where a permission failure names no property
#:           at all, which is where the detail view says the room holds no rule for
#:           the row and the refusal came from something the room cannot see.
DEMO_RUNS: tuple[dict[str, Any], ...] = (
    {
        "room": 0,
        "connector": "hubspot",
        "label": "Contact sync — Northwind",
        "minutes_ago": 55,
        "rows": [
            {"row_key": "northwind-1", "entity": "contact", "trace_id": "hs-nw-1",
             "values": {"email": "a.buyer@northwind.example", "lastname": "Ashworth"}},
            {"row_key": "northwind-2", "entity": "contact", "trace_id": "hs-nw-2",
             "values": {"email": "b.buyer@northwind.example", "lastname": "Byrne"}},
            {"row_key": "northwind-3", "entity": "contact", "trace_id": "hs-nw-3",
             "values": {"email": "c.buyer@northwind.example", "lastname": "Cruz"}},
        ],
        "vendor": {
            "status": 207,
            "body": {
                "status": "error",
                "numErrors": 2,
                "results": [
                    {"id": "901", "status": "success",
                     "context": {"objectWriteTraceId": ["hs-nw-1"]}},
                    {
                        "status": "error",
                        "category": "VALIDATION_ERROR",
                        "message": "The value supplied for property 'lastname' is not valid.",
                        "context": {"objectWriteTraceId": ["hs-nw-2"]},
                        "errors": [
                            {"message": "lastname is required by the admin rule",
                             "code": "INVALID_VALUE", "in": "lastname"},
                        ],
                    },
                    {
                        "status": "error",
                        "category": "VALIDATION_ERROR",
                        "message": "This record could not be validated.",
                        "context": {"objectWriteTraceId": ["hs-nw-unattributed"]},
                        "errors": [
                            {"message": "the owning portal is not reachable for this write",
                             "code": "UNKNOWN"},
                        ],
                    },
                ],
            },
        },
        "state": "HubSpot partial: 1 ok, 1 named property, 1 error kept unattributed",
    },
    {
        "room": 1,
        "connector": "dataverse",
        "label": "Task sync — Contoso",
        "minutes_ago": 40,
        "rows": [
            {"row_key": "contoso-1", "entity": "task", "trace_id": "dv-ct-1",
             "values": {"subject": "Security review kickoff"}},
            {"row_key": "contoso-2", "entity": "task", "trace_id": "dv-ct-2",
             "values": {"subject": "Follow-up: " + ("A" * 260)}},
            {"row_key": "contoso-3", "entity": "task", "trace_id": "dv-ct-3",
             "values": {"subject": "Pricing walkthrough"}},
        ],
        "vendor": {
            "status": 200,
            "body": [
                {"status": 204, "body": {"id": "task-8801"}},
                {
                    "status": 400,
                    "body": {
                        "error": {
                            "code": "0x80044331",
                            "message": (
                                "A validation error occurred. The length of the 'subject' "
                                "attribute of the 'task' entity exceeded the maximum allowed "
                                "length of '200'."
                            ),
                        },
                        "@Microsoft.PowerApps.CDS.HelpLink": {
                            "HelpLink": "https://learn.microsoft.com/power-apps/developer/data-platform/webapi/compose-http-requests-handle-errors",
                            "Description": "How Dataverse reports a validation error",
                        },
                    },
                },
                {
                    "status": 412,
                    "body": {"error": {"code": "ConcurrencyVersionMismatch",
                                       "message": "The record changed after it was read."}},
                },
            ],
        },
        "state": "Dataverse 200 that is not a success: 1 written, 1 0x80044331 with a doc link, 1 x412",
    },
    {
        "room": 2,
        "connector": "salesforce",
        "label": "Contact sync — Fabrikam",
        "minutes_ago": 25,
        "rows": [
            {"row_key": "fabrikam-1", "entity": "contact", "trace_id": "sf-fb-1",
             "values": {"email": "ops@fabrikam.example"}},
            {"row_key": "fabrikam-2", "entity": "contact", "trace_id": "sf-fb-2",
             "values": {"email": "renewals@fabrikam.example"}},
        ],
        "vendor": {
            "status": 403,
            "body": {
                "errorCode": "REQUEST_LIMIT_EXCEEDED",
                "message": "TotalRequests Limit exceeded.",
            },
        },
        "state": "Salesforce 403 request limit: whole batch refused, 2 rows queued",
    },
    {
        "room": 3,
        "connector": "hubspot",
        "label": "Contact sync — Adventure Works",
        "minutes_ago": 12,
        "rows": [
            {"row_key": "adventure-1", "entity": "contact", "trace_id": "hs-aw-1",
             "values": {"email": "lead@adventure.example"}},
            {"row_key": "adventure-2", "entity": "contact", "trace_id": "hs-aw-2",
             "values": {"email": "coordinator@adventure.example"}},
        ],
        "vendor": {
            "status": 200,
            "body": {
                "status": "success",
                "results": [
                    {"id": "7701", "status": "success",
                     "context": {"objectWriteTraceId": ["hs-aw-1"]}},
                    {"id": "7702", "status": "success",
                     "context": {"objectWriteTraceId": ["hs-aw-2"]}},
                ],
            },
        },
        "state": "HubSpot clean: every row succeeded, the rows a retry must leave alone",
    },
    {
        "room": 3,
        "connector": "hubspot",
        "label": "Contact sync — Adventure Works (after the integration role changed)",
        "minutes_ago": 6,
        "rows": [
            {"row_key": "adventure-3", "entity": "contact", "trace_id": "hs-aw-3",
             "values": {"email": "newcontact@adventure.example"}},
        ],
        "vendor": {
            "status": 207,
            "body": {
                "status": "error",
                "numErrors": 1,
                "results": [
                    {
                        "status": "error",
                        "category": "FORBIDDEN",
                        "message": "The integration user does not have permission to create contacts.",
                        "context": {"objectWriteTraceId": ["hs-aw-3"]},
                    },
                ],
            },
        },
        "state": "HubSpot permission failure that names no property at all",
    },
)

#: The one drain the seed actually runs, so the demo shows the researched
#: automation doing both of its jobs rather than a queue sitting still.
#:
#: One of the two rate-limited rows is accepted and becomes ``resolved``; the other
#: is throttled again and stays ``queued`` with a higher attempt count, which is the
#: state the demo most needs someone to look at.
#:
#: Each result carries the connector's own per-row trace value as the returned
#: record id, which is the correlation the room prefers over a position. That makes
#: the demo independent of the order the queue happens to hand the rows back in -
#: otherwise which row cleared would depend on a sort, and a demo that changes what
#: it is showing when a sort changes is a demo nobody can review.
DEMO_DRAIN_VENDOR = {
    "status": 200,
    "body": {
        "results": [
            {"id": "sf-fb-1", "success": True},
            {
                "id": "sf-fb-2",
                "success": False,
                "errors": [
                    {
                        "statusCode": "403",
                        "errorCode": "REQUEST_LIMIT_EXCEEDED",
                        "message": "TotalRequests Limit exceeded.",
                        "fields": [],
                    }
                ],
            },
        ]
    },
}


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the researched states, including the ones that only show up on failure.

    The runs are written through the real :class:`SyncLog` rather than by hand, so
    the demo cannot show a shape the workflow would not produce, every seeded row is
    audited like any other, and the one drain the seed runs is the real researched
    automation rather than a hand-written "attempts: 2".

    Keyed by position rather than by account name, the way the seeder hands out ids
    rather than room records. A database with no demo rooms gets nothing and says so
    rather than inventing rooms to hang failures on.

    Returns a short description of what was added, which the seeder prints.
    """
    rooms: list[tuple[str, str]] = list(context.get("room_ids") or [])
    now = context["now"]
    log = SyncLog(RecordStore(db))
    source = "seed"

    if not rooms:
        return "0 sync runs (no rooms to scope them to)"

    written: list[str] = []
    for plan in DEMO_RUNS:
        index = int(plan["room"])
        if index >= len(rooms):
            continue
        room_id, account = rooms[index]
        result = log.record_run(
            {
                "connector": plan["connector"],
                "label": plan["label"],
                "rows": plan["rows"],
                "vendor": plan["vendor"],
                "started_at": (now - timedelta(minutes=int(plan["minutes_ago"]))).isoformat(
                    timespec="seconds"
                ),
                "account": account,
            },
            room_id=room_id,
            actor="dana",
            source=source,
            now=now - timedelta(minutes=int(plan["minutes_ago"]) - 1),
        )
        written.append(result["run"]["id"])

    # The researched automation, run once for real. Long enough after the run that
    # the backoff on each queued row has elapsed, so both rows are genuinely due -
    # a drain that fired early would leave them scheduled and the demo would show a
    # queue nobody can act on.
    drain = log.drain(
        {"vendor": DEMO_DRAIN_VENDOR},
        actor="dana",
        source=source,
        now=now,
    )

    return (
        f"{len(written)} sync runs across {len({plan['room'] for plan in DEMO_RUNS})} rooms: "
        + "; ".join(str(plan["state"]) for plan in DEMO_RUNS[: len(written)])
        + f". Then one real drain: {drain['counts']['drain']} due, "
        f"{len(drain['expired_now'])} expired into 'needs a person', "
        f"{len(drain['applied'])} outcomes applied. Collections {ROW_COLLECTION}, crm_sync_run."
    )
