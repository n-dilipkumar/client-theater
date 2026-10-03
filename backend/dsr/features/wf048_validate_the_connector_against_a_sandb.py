"""WF-048: validate the connector against a sandbox or test account.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-048.md``, which
is the specification. The researched decisions are the product: the Power
Platform environment vocabulary (Sandbox / Default / Trial / Developer /
Production, with copy + reset, the 30-day trial and the one-per-user limit),
HubSpot's configurable test accounts created from a config file with the
platform ``2025.2`` / CLI ``8.3.0`` floors, the four assertions the room
scores from the sandbox's own answers (object created, dedupe key honoured,
rollback fired on an intentionally bad row, quota headers behaved), the
promotion gate ("Only after green does the admin switch the connection to
production"), and the two automations the research names - the self-test
command that makes sandbox validation a first-class feature, and the CI job
that creates the test account from a config file on every push.

This module is the three things the contract requires of a feature and
nothing else: the HTTP surface, the mapping from domain errors to responses,
and the demo data. The domain lives in :mod:`dsr.connector_sandbox`.

Why the prefix is ``/api/wf-048``
---------------------------------
A spec does not declare its routes, so a ticket-derived prefix cannot collide
with a feature-shaped one by construction. Every room-scoped path is
room-scoped, and the host's loader would report a ``(method, path)`` clash as
a failed feature rather than shadowing it.

``source=`` comes from the route
--------------------------------
Every write route below passes the route that actually served it, built from
``router.prefix`` so it cannot drift when the prefix changes, and ``source``
is a required keyword on every domain method that writes, so it cannot
silently regress. A test asserts that every source recorded in the audit log
matches a route the host actually mounted - including the CI run, which
writes a test-environment row *and* a run record through one route.

Error mapping
-------------
Five handlers, one per distinct answer, and all of the types are this
feature's own. ``RecordNotFound`` and ``AuditError`` are deliberately not
claimed: the core app already maps them, and two handlers for one type is a
collision the host refuses. The split between ``400`` (the request asks for
something this layer will not do: a production test sync, an unsupported
environment type, a HubSpot account below the sourced version floors), ``404``
(no such connection, room or run), ``422`` (promotion without a green run -
the request is fine, the evidence is missing) and ``502`` (the vendor's own
endpoint failed) is deliberate, and ``apiRequest`` in the frontend carries
the status so a page can tell them apart.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Mapping

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.connector_sandbox import (
    COLLECTION_CONNECTION,
    DEFAULT_BACKOFF_SECONDS,
    SandboxError,
    ScriptedTransport,
    SimulatedTransport,
    TestSyncRefused,
    NotValidatedError,
    UnknownConnectionError,
    UnknownRoomError,
    UnknownRunError,
    VendorRequestError,
    ci_run as domain_ci_run,
    connection_summary,
    convert_production_to_sandbox,
    create_connection,
    create_test_environment,
    execute_run,
    latest_run,
    list_connections,
    load_connection,
    overview,
    promote,
    require_room,
    revert,
    run_summary,
    self_test as domain_self_test,
    describe_inferences,
    describe_vocabulary,
)
from dsr.connector_sandbox.errors import UnknownEnvironmentKind
from dsr.connector_sandbox.transport import UrllibTransport
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-048-validate-the-connector-against-a-sandb",
    "ticket": "WF-048",
    "name": "Validate the connector against a sandbox or test account",
    "description": (
        "Point a connection at a non-production org - a Power Platform sandbox "
        "or a HubSpot configurable test account - run the full test sync with "
        "synthetic buyers, score the four researched assertions, and open the "
        "promotion gate only on green."
    ),
    "nav": [{"id": "sandbox-validation", "label": "Sandbox validation"}],
}

router = APIRouter(prefix="/api/wf-048", tags=["WF-048"])


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _sandbox_error(request: Request, exc: SandboxError) -> JSONResponse:
    """Well-formed JSON asking for something this layer will not do. 400."""
    return JSONResponse(status_code=400, content={"error": "sandbox_error", "detail": str(exc)})


def _not_validated(request: Request, exc: NotValidatedError) -> JSONResponse:
    """Promotion without a green run. 422, with the run status named.

    Starlette picks the most specific handler by MRO, so this wins over the
    base for the same exception and a page can say "run the test sync" rather
    than "the request was refused".
    """
    return JSONResponse(
        status_code=422, content={"error": "not_validated", "detail": str(exc)}
    )


def _unknown_connection(request: Request, exc: UnknownConnectionError) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": str(exc), "id": exc.connection_id},
    )


def _unknown_room(request: Request, exc: UnknownRoomError) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": str(exc), "id": exc.room_id},
    )


def _unknown_run(request: Request, exc: UnknownRunError) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": str(exc), "id": exc.run_id},
    )


def _vendor_request_error(request: Request, exc: VendorRequestError) -> JSONResponse:
    """The vendor's own endpoint failed. 502: the caller may retry it."""
    return JSONResponse(status_code=502, content={"error": "vendor_unreachable", "detail": str(exc)})


def _unknown_environment_kind(request: Request, exc: UnknownEnvironmentKind) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": "unknown_environment_kind", "detail": str(exc)})


EXCEPTION_HANDLERS = {
    SandboxError: _sandbox_error,
    NotValidatedError: _not_validated,
    UnknownConnectionError: _unknown_connection,
    UnknownRoomError: _unknown_room,
    UnknownRunError: _unknown_run,
    VendorRequestError: _vendor_request_error,
    UnknownEnvironmentKind: _unknown_environment_kind,
}


def get_validator(store: RecordStore = StoreDep) -> RecordStore:
    """The store, as the domain already sees it.

    The domain's functions take the store directly - the engine holds
    nothing but the store handle, so there is nothing to cache on
    ``app.state`` (which is the one place a feature would have to edit a
    shared file) and nothing to rebuild per request.
    """
    return store


ValidatorDep = Depends(get_validator)


# --------------------------------------------------------------------------- #
# The researched contract, served as data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """The researched contract: the flow, the environment facts, the gap.

    Served as data so a client renders its pickers from the same source the
    validator enforces against, and so a reviewer can read the quoted facts
    without opening a Python file. Includes the research gap verbatim: the
    Salesforce sandbox types that could not be sourced are a fact a reader
    should be told about rather than left to assume.
    """
    return describe_vocabulary()


@router.get("/inferences")
def inferences() -> dict[str, Any]:
    """Every design inference this workflow rests on, and how to change each one.

    The research is specific about the environment vocabulary, the version
    floors and the four assertions, and silent about what a route refuses
    versus scores. The judgement calls are collected in
    :mod:`dsr.connector_sandbox.inferences` and served here.
    """
    return describe_inferences()


@router.get("/summary")
def summary(store: RecordStore = ValidatorDep) -> dict[str, Any]:
    """Counts across every connection and run, for the page header."""
    return overview(store)


# --------------------------------------------------------------------------- #
# Connections and test environments
# --------------------------------------------------------------------------- #


@router.get("/connections")
def list_all_connections(store: RecordStore = ValidatorDep) -> dict[str, Any]:
    """Every connection, production and test environment alike.

    The summaries never carry credentials: the sandbox credential is stored
    on the row as an opaque token and the page reports its presence, not its
    value.
    """
    rows = [connection_summary(record) for record in list_connections(store)]
    return {
        "count": len(rows),
        "production": sum(1 for row in rows if row["environment"] == "production"),
        "test_environments": sum(1 for row in rows if row["environment"] != "production"),
        "connections": rows,
    }


@router.get("/rooms/{room_id}/connections")
def room_connections(room_id: str, store: RecordStore = ValidatorDep) -> dict[str, Any]:
    """The connections that serve this room, production and test rows together."""
    require_room(store, room_id)
    rows = [connection_summary(record) for record in list_connections(store, room_id)]
    return {
        "room_id": room_id,
        "count": len(rows),
        "by_environment": {
            "production": sum(1 for row in rows if row["environment"] == "production"),
            "test": sum(1 for row in rows if row["environment"] != "production"),
        },
        "connections": rows,
    }


@router.post("/rooms/{room_id}/connections", status_code=201)
def register_connection(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    store: RecordStore = ValidatorDep,
) -> dict[str, Any]:
    """Register the production connection. The researched step 1, recorded.

    The ``base_url`` and credentials come from the vendor's own admin
    surface; the mapping (object, key field, field map) is what a test
    environment later clones unchanged, because the researched data flow
    says "same mapping + same code path as production".
    """
    return create_connection(
        store,
        room_id,
        payload,
        actor=actor or "admin",
        source=f"POST {router.prefix}/rooms/{room_id}/connections",
    )


@router.get("/connections/{connection_id}")
def read_connection(connection_id: str, store: RecordStore = ValidatorDep) -> dict[str, Any]:
    """One connection, with its latest run pointer when it has one."""
    record = load_connection(store, connection_id)
    summary = connection_summary(record)
    run = latest_run(store, connection_id)
    if run is not None:
        summary["latest_run"] = run_summary(run)
    return summary


@router.post("/connections/{connection_id}/test-environment", status_code=201)
def create_test_environment_route(
    connection_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    store: RecordStore = ValidatorDep,
) -> dict[str, Any]:
    """Create the test environment row: a sandbox, or a HubSpot test account.

    [sourced] "HubSpot: admin (or CI) creates a **configurable test account**
    that simulates a specific subscription/tier." / "Dataverse/Power
    Platform: admin converts or creates a **Sandbox** environment (copy +
    reset supported)."

    The mapping is cloned unchanged from the production connection; the
    environment coordinates are replaced. A HubSpot account below platform
    ``2025.2`` or CLI ``8.3.0`` is refused, a second live trial for one user
    is refused, and ``production`` is refused as a test environment - each
    with the sourced rule in the refusal text.
    """
    return create_test_environment(
        store,
        connection_id,
        payload,
        now=datetime.now(timezone.utc),
        actor=actor or "admin",
        source=f"POST {router.prefix}/connections/{connection_id}/test-environment",
    )


@router.post("/connections/{connection_id}/convert")
def convert_connection(
    connection_id: str,
    actor: str | None = Query(default=None),
    store: RecordStore = ValidatorDep,
) -> dict[str, Any]:
    """Convert a production environment to sandbox. Never refused.

    [sourced] "converting from a production to a sandbox environment can't
    be blocked." Provisioning a sandbox can be admin-restricted; the
    conversion is not.
    """
    return convert_production_to_sandbox(
        store,
        connection_id,
        actor=actor or "admin",
        source=f"POST {router.prefix}/connections/{connection_id}/convert",
    )


# --------------------------------------------------------------------------- #
# The run, and its assertions
# --------------------------------------------------------------------------- #


@router.post("/connections/{connection_id}/run-test-sync")
def run_test_sync_route(
    connection_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    store: RecordStore = ValidatorDep,
) -> dict[str, Any]:
    """Step 4: **Run test sync** — the pipeline against the sandbox, scored. 200.

    [sourced] "The room runs **Run test sync** — the full W3→W6 pipeline
    against the sandbox with synthetic buyers." The four assertions are
    scored from the sandbox's own answers; the run stops on the first quota
    signal and records the back-off. A run against a production connection
    is refused: that is the property the validation exists for.
    """
    transport = _transport_for(payload)
    result = execute_run(
        store,
        connection_id,
        transport=transport,
        fixture=payload.get("fixture"),
        actor=actor or "admin",
        source=f"POST {router.prefix}/connections/{connection_id}/run-test-sync",
    )
    return {"run": run_summary(_load_run(store, result.run_id))}


@router.get("/rooms/{room_id}/runs")
def room_runs(
    room_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    store: RecordStore = ValidatorDep,
) -> dict[str, Any]:
    """Every run for a room, newest first, assertions verbatim."""
    require_room(store, room_id)
    from dsr.connector_sandbox.runs import COLLECTION_RUN

    rows = store.list(COLLECTION_RUN, room_id=room_id, limit=limit)
    return {"room_id": room_id, "count": len(rows), "runs": [run_summary(row) for row in rows]}


@router.get("/runs/{run_id}")
def read_run(run_id: str, store: RecordStore = ValidatorDep) -> dict[str, Any]:
    """One run, with the four assertions and the request log."""
    return run_summary(_load_run(store, run_id))


# --------------------------------------------------------------------------- #
# The promotion gate, and the revert
# --------------------------------------------------------------------------- #


@router.post("/connections/{connection_id}/promote")
def promote_connection(
    connection_id: str,
    actor: str | None = Query(default=None),
    store: RecordStore = ValidatorDep,
) -> dict[str, Any]:
    """Step 6: switch the connection to production. Only after green.

    [sourced] "Only after green does the admin switch the connection to
    production." Refused - with the run status that failed the gate - when
    the newest run is not green, and the promotion audit row names the run
    that carried the evidence.
    """
    return promote(
        store,
        connection_id,
        actor=actor or "admin",
        source=f"POST {router.prefix}/connections/{connection_id}/promote",
    )


@router.post("/connections/{connection_id}/revert")
def revert_connection(
    connection_id: str,
    actor: str | None = Query(default=None),
    store: RecordStore = ValidatorDep,
) -> dict[str, Any]:
    """Abandon the test environment. The researched "promote or revert" endpoint.

    The production row the test environment was cloned from is untouched:
    the test ran against the sandbox, so there is nothing to roll back on
    the row that was never part of the test.
    """
    return revert(
        store,
        connection_id,
        actor=actor or "admin",
        source=f"POST {router.prefix}/connections/{connection_id}/revert",
    )


# --------------------------------------------------------------------------- #
# The two automations the research names
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/self-test", status_code=201)
def self_test_route(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    store: RecordStore = ValidatorDep,
) -> dict[str, Any]:
    """The researched extensibility, as an endpoint.

    [sourced] "the open-source room can ship a 'self-test' command that
    creates both connection rows and runs the fixture, making sandbox
    validation a first-class feature rather than an ops chore." Creates the
    production row when it is absent, a fresh test-environment row, and
    runs the fixture; answers with the report an admin and a CI job both
    read.
    """
    transport = _transport_for(payload)
    return domain_self_test(
        store,
        room_id,
        payload,
        transport=transport,
        actor=actor or "admin",
        source=f"POST {router.prefix}/rooms/{room_id}/self-test",
    )


@router.post("/rooms/{room_id}/ci-run")
def ci_run_route(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    store: RecordStore = ValidatorDep,
) -> dict[str, Any]:
    """The researched automation: create the test account from the config, then smoke-test.

    [sourced] "CI/CD — GitHub Actions creates the test account from a config
    file on every push, then the sync smoke test runs. This is the
    vendor-documented automation path, not an invention." The report is
    shaped for a job step: ``ci.pass``, the run id, and the assertions.
    """
    transport = _transport_for(payload)
    return domain_ci_run(
        store,
        room_id,
        payload,
        transport=transport,
        actor=actor or "ci",
        source=f"POST {router.prefix}/rooms/{room_id}/ci-run",
    )


# --------------------------------------------------------------------------- #
# Transport selection and demo data
# --------------------------------------------------------------------------- #


#: The base URL the demo's ``redirect_uri``-shaped sandbox rows point at. It
#: resolves to nothing, because the demo never opens a socket.
DEMO_SANDBOX_BASE = "https://sandbox.example/api/wf-048/demo"


def _transport_for(payload: Mapping[str, Any]):
    """The transport a run speaks through.

    A request may hand the validator a ``scripted`` list so a caller can
    drive the pipeline without a socket. The default is the
    :class:`SimulatedTransport`, which answers each probe the way a
    compliant sandbox would - for the same reason WF-038's route default is
    one: the research cites no credentials for a real org, and every run it
    serves records ``transport: "simulated"`` in the run record so a
    reviewer can never read one of its confirmations as a fact about a real
    CRM. A deployment with a real sandbox points ``DSR_WF048_TRANSPORT=real``
    at :class:`UrllibTransport` - the one dependency a team overrides.
    """
    script = payload.get("scripted")
    if script is None:
        if os.environ.get("DSR_WF048_TRANSPORT", "").strip().lower() in ("real", "urllib", "1"):
            return UrllibTransport()
        return SimulatedTransport()
    from dsr.connector_sandbox.transport import SandboxResponse

    responses = [
        SandboxResponse(
            status=int(entry.get("status", 200)),
            body=str(entry.get("body", "")),
            headers=dict(entry.get("headers") or {}),
        )
        for entry in script
    ]
    return ScriptedTransport(responses)


#: What the scripted demo transport answers, per probe, for each demo state.
#:
#: The three states are the ones the research makes this workflow responsible
#: for: a sandbox where every assertion passes (green, promotable), a sandbox
#: whose rollback probe comes back *accepted* (the vendor did not roll the
#: chunk back, so the good row beside the bad one was written), and a run the
#: vendor answers 429 for, which the room must stop on.
DEMO_SCRIPTS: Mapping[str, list[Mapping[str, Any]]] = {
    "green": [
        {"status": 201, "body": '{"id": "sbx-0001", "created": true, "success": true}',
         "headers": {"Sforce-Limit-Info": "api-usage=31/5000"}},
        {"status": 200, "body": '{"id": "sbx-0001", "created": false, "success": true}',
         "headers": {"Sforce-Limit-Info": "api-usage=32/5000"}},
        {"status": 400, "body": ('{"records": [{"success": false, "errors": [{"message": '
                                 '"FIELD_INTEGRITY_EXCEPTION: Invalid field value"}]}, '
                                '{"success": false, "errors": [{"message": "Rolled back"}]}]}')},
    ],
    "accepted_chunk": [
        {"status": 201, "body": '{"id": "sbx-0003", "created": true, "success": true}',
         "headers": {"Sforce-Limit-Info": "api-usage=17/5000"}},
        {"status": 200, "body": '{"id": "sbx-0003", "created": false, "success": true}',
         "headers": {"Sforce-Limit-Info": "api-usage=18/5000"}},
        {"status": 200, "body": ('{"records": [{"id": "sbx-0004", "success": true, "created": true}, '
                                 '{"success": false, "errors": [{"message": "poisoned"}]}]}')},
    ],
    "quota": [
        {"status": 201, "body": '{"id": "sbx-0005", "created": true, "success": true}',
         "headers": {"Sforce-Limit-Info": "api-usage=4998/5000"}},
        {"status": 429, "body": '{"error": "RATE_LIMIT_REACHED"}', "headers": {"Retry-After": "45"}},
        {"status": 200, "body": '{"records": []}'},
    ],
    "dedupe_broken": [
        {"status": 201, "body": '{"id": "sbx-0006", "created": true, "success": true}',
         "headers": {}},
        {"status": 201, "body": '{"id": "sbx-0007", "created": true, "success": true}',
         "headers": {}},
        {"status": 400, "body": '{"records": [{"success": false}, {"success": false}]}',
         "headers": {}},
    ],
}


def _scripted_transport(name: str):
    """A :class:`ScriptedTransport` from one of the demo scripts."""
    entries = DEMO_SCRIPTS[name]
    from dsr.connector_sandbox.transport import SandboxResponse

    return ScriptedTransport(
        [
            SandboxResponse(
                status=int(entry.get("status", 200)),
                body=str(entry.get("body", "")),
                headers=dict(entry.get("headers") or {}),
            )
            for entry in entries
        ]
    )


def _load_run(store: RecordStore, run_id: str) -> dict[str, Any]:
    from dsr.connector_sandbox.runs import COLLECTION_RUN
    from dsr.connector_sandbox.errors import UnknownRunError

    record = store.get(run_id)
    if record is None or record["collection"] != COLLECTION_RUN:
        raise UnknownRunError(run_id)
    return record


#: The demo connections: (script, kind, spec overrides). ``sandbox`` is the
#: green promotable run; the rest demonstrate the states a demo of only green
#: would never exercise.
DEMO_CONNECTIONS: tuple[Mapping[str, Any], ...] = (
    {
        "name": "green",
        "room": 0,
        "vendor": "hubspot",
        "label": "Northwind Traders — HubSpot (promoted after green)",
        "kind": "hubspot",
        "config": {"subscription": "Enterprise", "tier": "Professional and Enterprise"},
        "platform_version": "2025.2",
        "cli_version": "8.3.0",
        "promote": True,
        "note": "the researched happy path: green run, then promoted",
    },
    {
        "name": "green",
        "room": 1,
        "vendor": "salesforce",
        "label": "Contoso Health — Salesforce (green, awaiting promotion)",
        "kind": "power_platform",
        "env_type": "sandbox",
        "promote": False,
        "note": "green but not promoted: the gate is open, the admin decides",
    },
    {
        "name": "accepted_chunk",
        "room": 1,
        "vendor": "salesforce",
        "label": "Fabrikam Logistics — Salesforce (rollback did not fire)",
        "kind": "power_platform",
        "env_type": "sandbox",
        "promote": False,
        "note": (
            "the vendor accepted the allOrNone chunk that carried a poisoned row, "
            "so the good row beside it was written: the assertion failed and "
            "promotion is refused"
        ),
    },
    {
        "name": "quota",
        "room": 2,
        "vendor": "salesforce",
        "label": "Adventure Works — Salesforce (quota signal mid-run)",
        "kind": "power_platform",
        "env_type": "sandbox",
        "promote": False,
        "note": (
            "the vendor answered 429 with Retry-After: 45 on the second probe; "
            "the run stopped, recorded the back-off and skipped the rest"
        ),
    },
    {
        "name": "dedupe_broken",
        "room": 2,
        "vendor": "hubspot",
        "label": "Adventure Works — HubSpot (dedupe created a second record)",
        "kind": "hubspot",
        "config": {"subscription": "Starter", "tier": "Starter"},
        "platform_version": "2025.2",
        "cli_version": "8.4.1",
        "promote": False,
        "note": "the vendor created a second record for the same dedupe key",
    },
    {
        "name": None,
        "room": 3,
        "vendor": "dataverse",
        "label": "Contoso Health — Dataverse (production, no test environment yet)",
        "kind": None,
        "promote": False,
        "note": "a production connection with nothing to validate yet",
    },
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed six connections across the states this workflow is responsible for.

    Every row is produced by running the real
    :mod:`dsr.connector_sandbox` engine over :class:`ScriptedTransport` -
    the real environment cloning, the real run and the real assertions - so
    the demo cannot show a state the workflow would not actually reach, and
    seeding never opens a socket:

    * **green and promoted** - Northwind on HubSpot, whose configurable test
      account was created from a config file, whose run passed all four
      assertions, and whose promotion names the run it came from;
    * **green, not promoted** - Contoso on Salesforce: the gate is open, the
      admin decides;
    * **rollback did not fire** - Fabrikam, whose vendor accepted the
      allOrNone chunk that carried the poisoned row, so the good row beside
      it was written;
    * **quota signal mid-run** - Adventure Works, whose vendor answered 429
      on the second probe: the run stopped, recorded the back-off, skipped
      the rollback probe;
    * **dedupe broken** - a HubSpot test account whose vendor created a
      second record for the same key;
    * **no test environment yet** - a production connection with nothing to
      validate, because an admin who has not started is a state a reviewer
      should see.
    """
    from datetime import datetime, timedelta, timezone

    from dsr.connector_sandbox.environments import create_test_environment
    from dsr.connector_sandbox.automation import self_test as domain_self_test

    store = RecordStore(db)
    source = "seed"
    actor = "dana"

    rooms: list[tuple[str, str]] = [
        (str(room_id), str(account))
        for room_id, account in list(context.get("room_ids") or [])
        if store.get(str(room_id)) is not None
    ]
    if not rooms:
        return "0 sandbox connections (no demo rooms to scope them to)"

    def room_at(index: Any) -> str:
        return rooms[index % len(rooms)][0]

    now = context.get("now") or datetime.now(timezone.utc)
    counts = {"green": 0, "failed": 0, "promoted": 0}

    for spec in DEMO_CONNECTIONS:
        room_id = room_at(spec["room"])
        production = store.create(
            COLLECTION_CONNECTION,
            {
                "vendor": str(spec["vendor"]),
                "base_url": f"https://{spec['vendor']}.example/api/wf-048/demo",
                "object_name": "Engagement__c" if spec["vendor"] == "salesforce" else "engagements",
                "key_field": "External_Engagement_Id__c" if spec["vendor"] == "salesforce" else "engagement_key",
                "field_map": {"event_type": "Event_Type__c", "buyer_email": "Buyer_Email__c"},
                "tenant": "acme",
                "label": str(spec["label"]),
                "environment": "production",
                "notes": str(spec.get("note") or ""),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        if spec.get("name") is None:
            continue

        if spec["kind"] == "hubspot":
            sandbox = create_test_environment(
                store,
                production["id"],
                {
                    "kind": "hubspot",
                    "room_id": room_id,
                    "base_url": f"{DEMO_SANDBOX_BASE}/{spec['name']}",
                    "credential": f"demo-test-account-{spec['name']}",
                    "config": dict(spec.get("config") or {"subscription": "Enterprise", "tier": "demo"}),
                    "platform_version": str(spec.get("platform_version") or "2025.2"),
                    "cli_version": str(spec.get("cli_version") or "8.3.0"),
                    "label": f"{spec['label']} — HubSpot configurable test account",
                },
                now=now,
                actor=actor,
                source=source,
            )
        else:
            sandbox = create_test_environment(
                store,
                production["id"],
                {
                    "kind": "power_platform",
                    "room_id": room_id,
                    "env_type": str(spec.get("env_type") or "sandbox"),
                    "base_url": f"{DEMO_SANDBOX_BASE}/{spec['name']}",
                    "credential": f"demo-sandbox-{spec['name']}",
                    "label": f"{spec['label']} — sandbox",
                },
                now=now,
                actor=actor,
                source=source,
            )

        result = execute_run(
            store,
            sandbox["id"],
            transport=_scripted_transport(str(spec["name"])),
            now=now,
            actor=actor,
            source=source,
        )
        if result.passed:
            counts["green"] += 1
            if spec.get("promote"):
                promote(store, sandbox["id"], now=now, actor=actor, source=source)
                counts["promoted"] += 1
        else:
            counts["failed"] += 1

    return (
        f"{len(DEMO_CONNECTIONS)} connections, {counts['green']} green runs, "
        f"{counts['failed']} failed runs, {counts['promoted']} promoted"
    )
