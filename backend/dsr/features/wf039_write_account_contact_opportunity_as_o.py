"""WF-039: write account + contact + opportunity as one atomic transaction.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-039.md``, which is
the specification. The researched decisions are the product: the six-step user
flow, the four vendor endpoints, the two reference syntaxes, ``allOrNone`` as the
rollback policy, ``collateSubrequests`` as the ordering knob, the six sets of
documented limits, and the per-step outcomes - including the two the research
states most plainly and the least often implement: *dependent subrequests aren't
executed*, and *an implicit dependency is not ordered against its dependency
unless you set ``collateSubrequests`` to false*.

This module is the three things the contract requires of a feature and nothing
else: the HTTP surface, the mapping from domain errors to responses, and the demo
data. The domain lives in :mod:`dsr.atomic_bundle`.

Why the prefix is ``/api/wf-039``
---------------------------------
A spec does not declare its routes, so a ticket-derived prefix cannot collide
with a feature-shaped one by construction. Every room-scoped path is room-scoped
- ``/rooms/{room_id}/bundles``, ``/rooms/{room_id}/runs``, ``/rooms/{room_id}/targets``
- and the host's loader would report a ``(method, path)`` clash as a failed
feature rather than shadowing it.

``source=`` comes from the route
--------------------------------
Every write route below passes the route that actually served it, built from
``router.prefix`` so it cannot drift when the prefix changes, and ``source`` is a
*required* keyword on every domain method that writes, so it cannot silently
regress. A test asserts that every source recorded in the audit log names a
route the host actually mounted. The rows the CRM creates carry the commit
route's own source too - they are writes, and they are audited, and an audit row
that cannot be traced back to the request that caused it is not an audit trail.

Error mapping
-------------
Three handlers, one per distinct HTTP answer, and all three types are this
feature's own. ``RecordNotFound`` and ``AuditError`` are deliberately not
claimed: the core app already maps them correctly, and two handlers for one type
is a collision the host refuses. The split between ``400`` and ``428`` is
deliberate - "this bundle is wrong" and "this installation is not set up yet"
are different things for a client, and ``apiRequest`` in the frontend carries the
status so a page can tell them apart.
"""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.atomic_bundle import (
    BUNDLE_COLLECTION,
    CONNECTOR_COLLECTION,
    OUTCOMES,
    RUN_COLLECTION,
    TARGET_COLLECTION,
    BundleCommitter,
    BundleError,
    BundleNotConfigured,
    NotFound,
    describe_inferences,
    describe_vocabulary,
    warning_codes,
)
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-039-write-account-contact-opportunity-as-o",
    "ticket": "WF-039",
    "name": "Write account + contact + opportunity as one atomic transaction",
    "description": (
        "Declare a related record set as data - ordered subrequests with id references - "
        "and commit it as one request through Salesforce Composite, Salesforce sObject "
        "Tree, a Dataverse changeset, or HubSpot's documented sequence. The rollback "
        "policy is allOrNone, the ordering knob is collateSubrequests, and the bundle "
        "preview shows the subrequest order before anything is sent."
    ),
    "nav": [{"id": "opportunity-bundle", "label": "Opportunity bundle"}],
}

router = APIRouter(prefix="/api/wf-039", tags=["wf039"])


def get_committer(store: RecordStore = StoreDep) -> BundleCommitter:
    """A :class:`BundleCommitter` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle and a transport factory, and ``app.state`` is where it would
    otherwise have to be built in the shared app's lifespan. Building it here also
    leaves the transport a seam a test can override, so the suite can commit
    without a CRM.
    """
    return BundleCommitter(store)


CommitterDep = Depends(get_committer)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _bundle_error(request: Request, exc: BundleError) -> JSONResponse:
    """Well-formed JSON asking for something this layer will not do. 400.

    ``BundleError`` is the base of every refusal in :mod:`dsr.atomic_bundle`: a
    record with no ``referenceId``, a dependency that points forwards, a bundle
    larger than a documented limit, a rollback policy this dialect cannot
    honour. All of them are the caller's to fix, and the message always names the
    record and the rule it broke.
    """
    return JSONResponse(status_code=400, content={"error": "bundle_error", "detail": str(exc)})


def _not_configured(request: Request, exc: BundleNotConfigured) -> JSONResponse:
    """Well formed, but this installation is not set up to answer it yet. 428.

    Distinct from 400 so a client can say "finish the setup" rather than "you got
    the request wrong" - the frontend's ``apiRequest`` carries the status for
    exactly this, and a preview with no connector attached is a real state a rep
    will meet.
    """
    return JSONResponse(status_code=428, content={"error": "not_configured", "detail": str(exc)})


def _not_found(request: Request, exc: NotFound) -> JSONResponse:
    """A room, bundle, connector or run id that does not resolve. 404.

    The resource name travels in the body rather than only in the prose, so a
    client can branch on it instead of pattern-matching a message.
    """
    return JSONResponse(
        status_code=404,
        content={
            "error": "not_found",
            "detail": str(exc),
            "resource": exc.resource,
            "id": exc.record_id,
            "room_id": exc.room_id,
        },
    )


EXCEPTION_HANDLERS = {
    BundleError: _bundle_error,
    BundleNotConfigured: _not_configured,
    NotFound: _not_found,
}


# --------------------------------------------------------------------------- #
# The researched contract, served as data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """The sourced vocabulary: the four endpoints, the two reference syntaxes, the
    limits, the per-step outcomes, and the user flow the preview walks through.

    Served as data so a client renders its pickers from the same source the
    validator enforces against. Includes the adjacent surfaces this build
    deliberately does not implement, so an omission reads as a decision rather
    than an oversight, and the research's own four stated gaps, so a reader knows
    which numbers are quoted and which shapes are not.
    """
    payload = describe_vocabulary()
    payload["collections"] = [
        CONNECTOR_COLLECTION,
        BUNDLE_COLLECTION,
        RUN_COLLECTION,
        TARGET_COLLECTION,
    ]
    return payload


@router.get("/inferences")
def inferences() -> dict[str, Any]:
    """Every design inference this workflow rests on, and how to change each one.

    The research is specific about the wire and silent about almost everything
    around it. The parts that are therefore judgement calls - the record shape,
    the default rollback policy, what "a single actionable error" means, the
    changeset framing, the HubSpot sequence - are collected in
    :mod:`dsr.atomic_bundle.inferences` and served here, next to the sourced
    facts they are measured against.

    A read with no side effect, so it needs no store. Also carries the warning
    vocabulary, because a warning and an inference are the same kind of statement
    about the same gap.
    """
    payload = describe_inferences()
    payload["warnings"] = [dict(entry) for entry in warning_codes()]
    return payload


# --------------------------------------------------------------------------- #
# Connectors: which CRM, and how it is spoken to
# --------------------------------------------------------------------------- #


@router.get("/connectors")
def list_connectors(committer: BundleCommitter = CommitterDep) -> dict[str, Any]:
    """Registered CRM connectors.

    A read never returns the token: it answers ``has_token`` and a masked hint of
    the last four characters, because the token *is* the ``Authorization`` header
    on every request and a connector row is readable by anyone who can call the
    API.
    """
    connectors = committer.list_connectors()
    return {"count": len(connectors), "connectors": connectors}


@router.post("/connectors", status_code=201)
def create_connector(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    committer: BundleCommitter = CommitterDep,
) -> dict[str, Any]:
    """Register a CRM connector: its dialect, its rollback defaults, its base URL.

    ``base_url`` is optional and nothing is defaulted in its place. A connector
    with no base URL can still declare bundles and preview the real request; it
    just cannot commit over HTTP until one is set, and the refusal says so.
    """
    return committer.create_connector(
        payload, actor=actor, source=f"POST {router.prefix}/connectors"
    )


@router.get("/connectors/{connector_id}")
def read_connector(connector_id: str, committer: BundleCommitter = CommitterDep) -> dict[str, Any]:
    return committer.get_connector(connector_id)


@router.patch("/connectors/{connector_id}")
def update_connector(
    connector_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    committer: BundleCommitter = CommitterDep,
) -> dict[str, Any]:
    """Patch a connector: swap the dialect, move to HTTP and set a base URL, flip
    the on/off toggle, rotate the token.

    The toggle lives on the connector rather than beside it, because a connector
    that is switched off is a property of the integration: the researched
    behaviour is one explicit request, and whether this installation is allowed to
    make it is one fact, not two.
    """
    return committer.update_connector(
        connector_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/connectors/{connector_id}",
    )


@router.delete("/connectors/{connector_id}", status_code=204)
def delete_connector(
    connector_id: str,
    actor: str | None = Query(default=None),
    committer: BundleCommitter = CommitterDep,
) -> Response:
    """Soft-delete a connector. The run log outlives it, and stays readable.

    A soft delete rather than a hard one so a run's ``connector_id`` still
    resolves to something and a reader can see what the connector said at the
    time - which is the only version of it that matters.
    """
    committer.delete_connector(
        connector_id, actor=actor, source=f"DELETE {router.prefix}/connectors/{connector_id}"
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Bundles: the dependency graph, declared as data
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/bundles")
def list_bundles(
    room_id: str,
    dialect: str | None = Query(default=None),
    policy: str | None = Query(default=None),
    committer: BundleCommitter = CommitterDep,
) -> dict[str, Any]:
    """The bundles declared for this room, newest first.

    Filterable by dialect and by rollback policy - both are JSON paths in each
    row's own payload, resolved through the dynamic index, so a new dialect needs
    no change here.
    """
    bundles = committer.list_bundles(room_id, dialect=dialect, policy=policy)
    return {
        "room_id": room_id,
        "count": len(bundles),
        "dialect": dialect,
        "policy": policy,
        "bundles": bundles,
    }


@router.post("/rooms/{room_id}/bundles", status_code=201)
def create_bundle(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    committer: BundleCommitter = CommitterDep,
) -> dict[str, Any]:
    """Declare a bundle: an ordered list of records, each naming the one before it
    it depends on.

    This is the researched extensibility claim made concrete. A 4th record type is
    one more entry in ``records``; nothing in the transport learns its name. The
    list is *in dependency order*, and a record may only depend on one that came
    before it - a bundle that has to be reordered to be valid is a bundle whose
    preview lied.
    """
    return committer.create_bundle(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/bundles"
    )


@router.get("/rooms/{room_id}/bundles/{bundle_id}")
def read_bundle(
    room_id: str, bundle_id: str, committer: BundleCommitter = CommitterDep
) -> dict[str, Any]:
    return committer.get_bundle(room_id, bundle_id)


@router.patch("/rooms/{room_id}/bundles/{bundle_id}")
def update_bundle(
    room_id: str,
    bundle_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    committer: BundleCommitter = CommitterDep,
) -> dict[str, Any]:
    """Patch a bundle: change the fields, add a record, switch the policy.

    Every key is ordinary JSON and no key is required, so a team adding a field to
    a record does it by shipping a payload rather than by a migration.
    """
    return committer.update_bundle(
        room_id,
        bundle_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/rooms/{room_id}/bundles/{bundle_id}",
    )


@router.delete("/rooms/{room_id}/bundles/{bundle_id}", status_code=204)
def delete_bundle(
    room_id: str,
    bundle_id: str,
    actor: str | None = Query(default=None),
    committer: BundleCommitter = CommitterDep,
) -> Response:
    """Soft-delete a bundle. Its runs stay, and keep the request they sent."""
    committer.delete_bundle(
        room_id,
        bundle_id,
        actor=actor,
        source=f"DELETE {router.prefix}/rooms/{room_id}/bundles/{bundle_id}",
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# The researched bundle preview
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/bundles/{bundle_id}/preview")
def preview_bundle(
    room_id: str,
    bundle_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    committer: BundleCommitter = CommitterDep,
) -> dict[str, Any]:
    """The researched **Sync → bundle preview**: the subrequest order, and why.

    [sourced] "Room **Sync → bundle preview** showing the subrequest order;
    ``collateSubrequests`` toggle to force execution order." So the preview is
    the feature, and this route is it: the plan, the exact request that would go
    on the wire, the warnings the current settings produce, the blockers that
    would stop a commit, and the researched user flow the order maps onto.

    Writes nothing, and accepts ``dialect``, ``policy`` and
    ``collate_subrequests`` so a rep can try the other settings and see the
    request change before committing to one. Every field here is computed by the
    same code the commit runs, so the preview cannot drift from the commit.
    """
    return committer.preview(
        room_id,
        bundle_id,
        dialect=_choice(payload, "dialect"),
        policy=_choice(payload, "policy"),
        collate_subrequests=_flag(payload, "collate_subrequests"),
    )


# --------------------------------------------------------------------------- #
# The researched commit
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/bundles/{bundle_id}/commit")
def commit_bundle(
    room_id: str,
    bundle_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    committer: BundleCommitter = CommitterDep,
) -> dict[str, Any]:
    """Commit the bundle: one request, one transaction, one run record.

    [sourced] "On any failure the whole bundle rolls back (strict mode) and the
    room shows a single actionable error." So this route returns one run with one
    ``actionable_error``, and the per-subrequest outcomes are on the same record
    for anyone reading the detail.

    Overridable per commit with ``dialect``, ``policy`` and
    ``collate_subrequests`` - the researched rollback policy and ordering toggle,
    set for *this* request rather than for every future one.

    ``faults`` is deliberately not accepted here. A deployment's failures come
    from the CRM; a client that could inject them would be a client that could
    forge an audit record.
    """
    return committer.commit(
        room_id,
        bundle_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/bundles/{bundle_id}/commit",
        dialect=_choice(payload, "dialect"),
        policy=_choice(payload, "policy"),
        collate_subrequests=_flag(payload, "collate_subrequests"),
    )


# --------------------------------------------------------------------------- #
# The run log
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/runs")
def list_runs(
    room_id: str,
    bundle_id: str | None = Query(default=None),
    ok: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    committer: BundleCommitter = CommitterDep,
) -> dict[str, Any]:
    """Every commit attempted for this room, newest first.

    A failed commit is a row, not a gap: the failure is the thing a rep has to
    read. The summary is computed over exactly the rows returned, so a filtered
    view does not report totals for the whole log.
    """
    runs = committer.runs(room_id, bundle_id=bundle_id, ok=ok, limit=limit)
    summary = {outcome: 0 for outcome in OUTCOMES}
    for run in runs:
        for outcome, count in (run.get("counts") or {}).items():
            summary[outcome] = summary.get(outcome, 0) + count
    summary["runs"] = len(runs)
    summary["ok"] = sum(1 for run in runs if run.get("ok"))
    return {
        "room_id": room_id,
        "count": len(runs),
        "summary": summary,
        "filter": {"bundle_id": bundle_id, "ok": ok, "limit": limit},
        "runs": runs,
    }


@router.get("/rooms/{room_id}/runs/{run_id}")
def read_run(
    room_id: str, run_id: str, committer: BundleCommitter = CommitterDep
) -> dict[str, Any]:
    """One commit in full: the request that was sent, every subrequest outcome,
    the single actionable error, and the CRM's raw response."""
    return committer.run(room_id, run_id)


# --------------------------------------------------------------------------- #
# What the CRM now holds
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/targets")
def list_targets(room_id: str, committer: BundleCommitter = CommitterDep) -> dict[str, Any]:
    """The records the CRM created for this room.

    [sourced] "The CRM executes subrequests in order, capturing each created record
    id." This route is that sentence, made a query. It is also how a rollback is
    seen: the rows a strict bundle created are gone from here, while the run
    record still says they existed.
    """
    rows = committer.targets(room_id)
    by_object: dict[str, int] = {}
    for row in rows:
        obj = str(row["data"].get("object") or "unknown")
        by_object[obj] = by_object.get(obj, 0) + 1
    return {"room_id": room_id, "count": len(rows), "by_object": by_object, "targets": rows}


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _choice(payload: Mapping[str, Any], key: str) -> str | None:
    """One of the three researched settings, or ``None`` to take the default."""
    value = payload.get(key)
    if value is None:
        return None
    return str(value)


def _flag(payload: Mapping[str, Any], key: str) -> bool | None:
    """The ordering toggle, or ``None`` to take the default.

    Read as ``True``/``False``/``None`` rather than as a truthiness test, because
    ``collate_subrequests: false`` is the sourced fix for an implicit dependency
    and a payload that omits the key must not turn it on.
    """
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The connectors the demo registers. Deliberately mixed, because a demo showing
#: only healthy integrations teaches a reviewer nothing:
#:
#: * **Salesforce** - the composite dialect, the researched default, a real API
#:   version, and a base URL, so a deployment that points it at an org has
#:   nothing left to configure.
#: * **Dataverse** - the changeset dialect, with the two entity sets it renamed,
#:   so the preview shows an overridden name rather than the default guess.
#: * **HubSpot** - the sequence dialect, and **disabled**, so the run log has an
#:   integration that exists and is not in use.
DEMO_CONNECTORS: tuple[Mapping[str, Any], ...] = (
    {
        "name": "Salesforce production",
        "dialect": "salesforce_composite",
        "policy": "strict",
        "transport": "local",
        "base_url": "https://acme.my.salesforce.example",
        "api_version": "v61.0",
        "collate_subrequests": False,
        "enabled": True,
    },
    {
        "name": "Dataverse (contoso ops)",
        "dialect": "dataverse_batch",
        "policy": "strict",
        "transport": "local",
        "base_url": "https://contoso.crm.dynamics.example",
        "collate_subrequests": True,
        "entity_sets": {"Account": "contoso_accounts"},
        "enabled": True,
    },
    {
        "name": "HubSpot (paused)",
        "dialect": "hubspot_associations",
        "policy": "strict",
        "transport": "local",
        "collate_subrequests": True,
        "enabled": False,
    },
)

#: The bundles the demo declares, chosen to exercise the researched rules rather
#: than to look tidy. Each is ``(room index, name, dialect, policy, collate,
#: records, faults)``, and the faults are injected through the real engine rather
#: than written by hand, so every run in the demo is one this workflow produced.
DEMO_BUNDLES: tuple[Mapping[str, Any], ...] = (
    {
        "room": 0,
        "name": "Northwind Q4 opportunity bundle",
        "note": "the happy path: three subrequests, one request, three rows",
        "dialect": "salesforce_composite",
        "policy": "strict",
        "collate_subrequests": False,
        "faults": {},
        "records": [
            {
                "reference_id": "refAccount",
                "type": "Account",
                "fields": {"Name": "Northwind Traders", "BillingCountry": "GB"},
            },
            {
                "reference_id": "refContact",
                "type": "Contact",
                "fields": {"LastName": "Okonkwo", "Email": "a.buyer@northwind.example"},
                "parent": {"reference": "refAccount", "field": "AccountId"},
            },
            {
                "reference_id": "refOpportunity",
                "type": "Opportunity",
                "fields": {"Name": "Northwind - Q4 enterprise", "StageName": "Qualification"},
                "parent": {"reference": "refAccount", "field": "AccountId"},
            },
        ],
    },
    {
        "room": 0,
        "name": "Northwind Q4 - partial, contact refused",
        "note": (
            "allOrNone: false. The Account and the Opportunity are independent of the "
            "failed Contact, so they are executed; the Contact is the one that failed."
        ),
        "dialect": "salesforce_composite",
        "policy": "partial",
        "collate_subrequests": False,
        "faults": {"refContact": "REQUIRED_FIELD_MISSING: Required fields are missing: [Email]"},
        "records": [
            {
                "reference_id": "refAccount",
                "type": "Account",
                "fields": {"Name": "Fabrikam Logistics"},
            },
            {
                "reference_id": "refContact",
                "type": "Contact",
                "fields": {"LastName": "Alvarez"},
                "parent": {"reference": "refAccount", "field": "AccountId"},
            },
            {
                "reference_id": "refOpportunity",
                "type": "Opportunity",
                "fields": {"Name": "Fabrikam - pilot"},
                "parent": {"reference": "refAccount", "field": "AccountId"},
            },
        ],
    },
    {
        "room": 1,
        "name": "Contoso renewal - strict rollback",
        "note": (
            "allOrNone: true. The Account and the Opportunity were created and then the "
            "whole composite was rolled back, which is the state a rep has to be shown "
            "rather than told."
        ),
        "dialect": "salesforce_composite",
        "policy": "strict",
        "collate_subrequests": False,
        "faults": {"refContact": "DUPLICATE_VALUE: an email address already exists"},
        "records": [
            {
                "reference_id": "refAccount",
                "type": "Account",
                "fields": {"Name": "Contoso Ltd"},
            },
            {
                "reference_id": "refContact",
                "type": "Contact",
                "fields": {"LastName": "Whitfield", "Email": "ops@contoso.example"},
                "parent": {"reference": "refAccount", "field": "AccountId"},
            },
            {
                "reference_id": "refOpportunity",
                "type": "Opportunity",
                "fields": {"Name": "Contoso - renewal"},
                "parent": {"reference": "refAccount", "field": "AccountId"},
            },
        ],
    },
    {
        "room": 1,
        "name": "Contoso renewal - dependents skipped",
        "note": (
            "partial, with the failure on the *root*. Both children depend on the "
            "Account, so neither is executed: [sourced] 'Dependent subrequests aren't "
            "executed.'"
        ),
        "dialect": "salesforce_composite",
        "policy": "partial",
        "collate_subrequests": False,
        "faults": {"refAccount": "INSUFFICIENT_ACCESS: user cannot create Account"},
        "records": [
            {
                "reference_id": "refAccount",
                "type": "Account",
                "fields": {"Name": "Tailspin Toys"},
            },
            {
                "reference_id": "refContact",
                "type": "Contact",
                "fields": {"LastName": "Sandoval"},
                "parent": {"reference": "refAccount", "field": "AccountId"},
            },
            {
                "reference_id": "refOpportunity",
                "type": "Opportunity",
                "fields": {"Name": "Tailspin - regional"},
                "parent": {"reference": "refAccount", "field": "AccountId"},
            },
        ],
    },
    {
        "room": 2,
        "name": "Adventure Works - sObject tree",
        "note": (
            "the tree endpoint: one nested tree, five levels deep and five types are "
            "the documented limits, and 'If an error occurs while creating a record, "
            "the entire request fails' - so this dialect has no partial mode at all."
        ),
        "dialect": "salesforce_sobject_tree",
        "policy": "strict",
        "collate_subrequests": False,
        "faults": {"refContact": "STRING_TOO_LONG: BillingStreet exceeds 255 characters"},
        "records": [
            {
                "reference_id": "refAccount",
                "type": "Account",
                "fields": {"Name": "Adventure Works"},
            },
            {
                "reference_id": "refContact",
                "type": "Contact",
                "fields": {"LastName": "Bachmann"},
                "parent": {"reference": "refAccount", "field": "AccountId"},
            },
            {
                "reference_id": "refOpportunity",
                "type": "Opportunity",
                "fields": {"Name": "Adventure Works - EMEA"},
                "parent": {"reference": "refAccount", "field": "AccountId"},
            },
        ],
    },
    {
        "room": 2,
        "name": "Litware - Dataverse changeset",
        "note": (
            "one changeset, Content-ID 1/2/3, and 'originatingleadid@odata.bind': '$1' - "
            "the researched example, verbatim. A changeset is atomic as a whole, so "
            "this dialect refuses a partial policy rather than pretending."
        ),
        "dialect": "dataverse_batch",
        "policy": "strict",
        "collate_subrequests": True,
        "faults": {},
        "records": [
            {
                "reference_id": "refAccount",
                "type": "Account",
                "fields": {"name": "Litware Inc"},
            },
            {
                "reference_id": "refContact",
                "type": "Contact",
                "fields": {"lastname": "Nguyen", "emailaddress1": "buyer@litware.example"},
                "parent": {"reference": "refAccount", "field": "parentaccountid"},
            },
            {
                "reference_id": "refOpportunity",
                "type": "Opportunity",
                "fields": {"name": "Litware - expansion"},
                "parent": {"reference": "refAccount", "field": "parentaccountid"},
            },
        ],
    },
    {
        "room": 3,
        "name": "Wide World - HubSpot sequence",
        "note": (
            "the only dialect with no documented cross-object transaction. It renders "
            "a sequence, says so, and honours a strict policy by deleting what it "
            "created - a compensation, not a rollback."
        ),
        "dialect": "hubspot_associations",
        "policy": "strict",
        "collate_subrequests": True,
        "faults": {
            "refOpportunity-association": "VALIDATION_ERROR: association type is not defined",
            "refAccount-delete": "LOCKED: the account row cannot be deleted",
        },
        "records": [
            {
                "reference_id": "refAccount",
                "type": "Account",
                "fields": {"name": "Wide World Importers", "object_type_id": "2"},
            },
            {
                "reference_id": "refContact",
                "type": "Contact",
                "fields": {"lastname": "Nakamura", "object_type_id": "0-1"},
                "parent": {"reference": "refAccount", "field": "3"},
            },
            {
                "reference_id": "refOpportunity",
                "type": "Opportunity",
                "fields": {"dealname": "Wide World - APAC", "object_type_id": "4"},
                "parent": {"reference": "refAccount", "field": "3"},
            },
        ],
    },
    {
        "room": 3,
        "name": "Proseware - implicit dependency on the account name",
        "note": (
            "the researched collation caveat, made reachable. Two audit records of the "
            "same custom type bracket the account, and the second one's trigger reads "
            "the account's name - an *implicit* dependency, which is exactly the case "
            "the research says collation can get wrong. So the preview warns while "
            "collateSubrequests is on, and the commit fails with a collation violation "
            "until it is turned off."
        ),
        "dialect": "salesforce_composite",
        "policy": "strict",
        "collate_subrequests": True,
        "faults": {},
        "records": [
            {
                "reference_id": "refPriorAudit",
                "type": "AccountAudit__c",
                "fields": {"Reason__c": "carried over from last year"},
            },
            {
                "reference_id": "refAccount",
                "type": "Account",
                "fields": {"Name": "Proseware Inc"},
            },
            {
                "reference_id": "refContact",
                "type": "Contact",
                "fields": {"LastName": "Duffy"},
                "parent": {"reference": "refAccount", "field": "AccountId"},
            },
            {
                "reference_id": "refAudit",
                "type": "AccountAudit__c",
                "fields": {"Reason__c": "triggered by the account name"},
                "implicit_depends_on": ["refAccount"],
            },
        ],
    },
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed three connectors, eight bundles, and real commits through all of them.

    Every run in the demo is produced by running the real
    :class:`~dsr.atomic_bundle.engine.BundleCommitter` over the real local CRM, so
    the demo cannot show a shape the workflow would not produce, and seeding never
    opens a socket.

    It is deliberately mixed, because a demo showing only green teaches a
    reviewer nothing. Between them the eight bundles produce:

    * a clean strict commit that creates three rows and lands the Opportunity on
      the Account it just created;
    * a **partial** run where two independent subrequests commit and one fails;
    * a **strict rollback** where the Account was created and then the whole
      composite was rolled back - the rows are gone and the run still says they
      existed;
    * a run where the **root** fails under partial and both children are
      *skipped* rather than failed, quoting the sourced rule;
    * an **sObject tree** run, whose dialect has no partial mode at all;
    * a **Dataverse changeset** run carrying the researched
      ``"originatingleadid@odata.bind": "$1"`` example verbatim;
    * a **HubSpot sequence** whose association ``PUT`` is refused, and whose strict
      policy then compensates - with the compensation itself refused, so the rows
      remain and the run says why;
    * and a bundle with an **implicit dependency**, which the preview warns about
      while collation is on and the commit refuses until it is turned off.

    Returns a short description of what was added, which the seeder prints.
    """
    store = RecordStore(db)
    committer = BundleCommitter(store)
    actor = "dana"
    source = "seed"

    connectors = {
        str(spec["name"]): committer.create_connector(spec, actor=actor, source=source)
        for spec in DEMO_CONNECTORS
    }
    by_dialect = {str(record["dialect"]): record for record in connectors.values()}

    rooms: list[tuple[str, str]] = [
        (str(room_id), str(account))
        for room_id, account in list(context.get("room_ids") or [])
        if store.get(str(room_id)) is not None
    ]
    if not rooms:
        # No demo rooms to attach to. The connectors are still worth having, and
        # the seeder prints what was skipped. Filtered by existence rather than
        # trusted, because a seeder that aborts a whole feature over one stale
        # room id leaves a page nobody can review.
        return f"{len(connectors)} connectors, 0 bundles (no rooms to scope them to)"

    created = 0
    committed = 0
    failed = 0
    skipped = 0
    rolled_back = 0

    for spec in DEMO_BUNDLES:
        room_id = rooms[int(spec["room"]) % len(rooms)][0]
        dialect = str(spec["dialect"])
        payload = {
            "name": spec["name"],
            "records": spec["records"],
            "dialect": dialect,
            "policy": spec["policy"],
            "collate_subrequests": spec["collate_subrequests"],
        }
        connector = by_dialect.get(dialect)
        if connector is not None:
            payload["connector_id"] = connector["id"]
        bundle = committer.create_bundle(room_id, payload, actor=actor, source=source)
        created += 1

        run = committer.commit(
            room_id,
            bundle["id"],
            actor=actor,
            source=source,
            faults=dict(spec.get("faults") or {}),
        )
        committed += len(run["committed"])
        failed += len(run["failed"])
        skipped += len(run["skipped"])
        rolled_back += len(run["rolled_back"])

    # The implicit-dependency bundle a second time, with the ordering knob at the
    # value the research says fixes it. This is the whole point of the toggle: the
    # same bundle, the same CRM, one setting different, and the run that failed
    # now commits. A toggle nobody can see working is a toggle nobody trusts.
    implicit = next((spec for spec in DEMO_BUNDLES if spec["name"].startswith("Proseware")), None)
    if implicit is not None:
        room_id = rooms[int(implicit["room"]) % len(rooms)][0]
        payload = {
            "name": f"{implicit['name']} (collation off)",
            "records": implicit["records"],
            "dialect": "salesforce_composite",
            "policy": "strict",
            "collate_subrequests": False,
        }
        connector = by_dialect.get("salesforce_composite")
        if connector is not None:
            payload["connector_id"] = connector["id"]
        bundle = committer.create_bundle(room_id, payload, actor=actor, source=source)
        created += 1
        run = committer.commit(
            room_id, bundle["id"], actor=actor, source=source, collate_subrequests=False
        )
        committed += len(run["committed"])

    return (
        f"{len(connectors)} connectors, {created} bundles, "
        f"{committed} subrequests committed, {failed} failed, {skipped} skipped with the sourced "
        f"reason, {rolled_back} rolled back or compensated "
        "(1 strict rollback, 1 partial commit, 1 changeset, 1 refused compensation, "
        "1 collation failure and the same bundle again with the toggle fixed)"
    )
