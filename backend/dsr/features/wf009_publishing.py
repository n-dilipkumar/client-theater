"""WF-009: approve and publish library content, immediately or on schedule.

Ported from ``feature/WF-009-approve-and-publish-library-content-immediately-or-on``
onto the plugin host. The rules live in
:mod:`dsr.features.wf009_publishing_domain`, which the port took over
essentially unchanged; this module is the three things that used to be edits to
shared files:

* the route table, which the branch appended to the single ``app`` in
  ``dsr/api.py`` via ``dsr/publishing_api.py``, and which is now an
  ``APIRouter`` this feature owns;
* the error mapping, which was two ``@app.exception_handler`` blocks in
  ``dsr/api.py`` and is now an ``EXCEPTION_HANDLERS`` export the host attaches;
* the demo rows, which were an edit to ``backend/seed.py`` and are now a
  ``seed(db, context)`` hook the seeder calls.

What that buys is the property the host exists for: on the original branch this
workflow could not be merged without resolving a conflict against the other
eleven, because all twelve appended to the same three files.

The prefix is ``/api/publishing``, kept from the branch. It is shared with
WF-011 by design and the host allows that: WF-011 owns ``/rooms`` and its
status, share-link, access, ``/events`` and ``/webhooks`` paths, and this feature
owns ``/processes``, ``/submissions``, ``/workflows``, ``/publish``,
``/publications``, ``/publications/due``, ``/folders`` and ``/subscriptions``.
No concrete ``(method, path)`` pair is shared, so neither feature shadows the
other. The host would report a collision as a failed feature rather than let load
order decide.

Five deliberate departures from the branch, each required by the contract:

* **``source`` is passed in, never hard-coded.** The branch wrote a fixed label
  - ``source="WF-009 publish"`` - into the middle of every write. That is the
  defect the port brief calls out: the audit row did not name the route that
  served the write, so it could not drift away from what the app actually serves
  either. Every write method now takes a required ``source``, built here from
  ``router.prefix``, so the audit trail and the route table cannot disagree.
  The same reasoning applies to the queue's ``next_page`` link, which is now
  passed in rather than written into the domain module;
* **the service comes from ``StoreDep``.** The branch created it in the
  ``lifespan`` hook of ``dsr/api.py`` and reached for ``request.app.state``. That
  is a shared-file edit and a hidden dependency, so the service is now composed
  from the documented dependency seam, exactly as the other ports do;
* **the demo data is a ``seed`` hook**, not an edit to ``backend/seed.py``;
* **the domain module moved into this folder.** WF-011's branch added its own
  ``backend/dsr/publishing.py`` with its own ``PublishingService``. Leaving this
  one at that path would put two different services on one filename, across two
  branches that cannot see each other, on a file the CI guard does not watch -
  the same collision the host removes, one level down. It is now
  ``wf009_publishing_domain.py`` beside this file;
* **the exception handlers are exported, not registered.** FastAPI only accepts
  handlers on the app object, so the host attaches them.

The branch's two edits to ``dsr/store.py`` are **not** carried over. See the
module docstring of :mod:`dsr.features.wf009_publishing_domain` for what the port
uses instead.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.features.wf009_publishing_domain import (
    ApprovalConflict,
    PublishError,
    PublishingService,
    parse_publish_at,
    view_workflow,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-009-publishing",
    "ticket": "WF-009",
    "name": "Approve and publish library content",
    "description": (
        "Submit library content into a named approval process, clear its ordered "
        "steps, then publish immediately or on a UTC schedule. Published content "
        "lands in Dynamic Folders by its own metadata."
    ),
    "nav": [{"id": "publishing", "label": "Approval & publishing"}],
}

router = APIRouter(prefix="/api/publishing", tags=["wf009"])


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# Two handlers, and the reason they exist rather than falling through to the core
# ``AuditError`` handler is that the core one decides 409-vs-400 by searching the
# message for the words "conflict" or "exists". That makes a status code a
# function of prose: rewrite a message and the HTTP contract changes under a
# client that branched on it. These two types are this feature's own, so mapping
# them here makes "a decision conflicts with current state" a 409 and "this
# request cannot be attempted" a 400, whatever the message says. They subclass
# ``AuditError``, so the host still refuses a second feature from claiming the
# same type.


def _publish_error(request: Request, exc: PublishError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": "publish_error", "detail": str(exc)})


def _approval_conflict(request: Request, exc: ApprovalConflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"error": "approval_conflict", "detail": str(exc)})


EXCEPTION_HANDLERS = {
    PublishError: _publish_error,
    ApprovalConflict: _approval_conflict,
}


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
#
# Every route is a thin translation of an HTTP request into a
# ``PublishingService`` call. The rules live in one place so the HTTP surface
# cannot drift from them, and the generic ``/api/records`` endpoints stay
# available for anything this workflow does not model.


def get_service(store: RecordStore = StoreDep) -> PublishingService:
    """The publishing service, built on the shared audited store.

    Composed from the dependency seam rather than reaching for
    ``request.app.state``, which is what the branch's ``lifespan`` hook did. A
    feature that reads app state has a dependency the contract does not document
    and a test cannot construct; this one takes the same store every other reader
    takes, so the audit row is still written in the same transaction as the
    change.
    """
    return PublishingService(store)


ServiceDep = Depends(get_service)


def _source(verb: str, suffix: str = "") -> str:
    """The audit ``source`` for a write: the path this router actually serves.

    Built from ``router.prefix`` rather than written out, because the port
    brief's hard rule 4 is that a domain function must never name a path the app
    might have stopped serving. The branch wrote a fixed ``"WF-009 publish"``
    label into every write instead, which is the drift this exists to prevent.
    """
    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# Approval processes
# --------------------------------------------------------------------------- #


@router.post("/processes", status_code=201)
def create_process(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    service: PublishingService = ServiceDep,
) -> dict[str, Any]:
    """Define a named approval process: ordered steps, approvers, routing.

    ``steps`` is the only structural requirement. Any other key is stored
    verbatim, so a team can add an SLA or a routing rule without a migration.
    """
    return service.create_process(
        payload,
        actor=actor,
        room_id=room_id,
        source=_source("POST", "/processes"),
    )


@router.get("/processes")
def list_processes(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    service: PublishingService = ServiceDep,
) -> dict[str, Any]:
    processes = service.list_processes(room_id=room_id, limit=limit)
    return {"count": len(processes), "entries": processes}


# --------------------------------------------------------------------------- #
# Submissions and the queue
# --------------------------------------------------------------------------- #


@router.post("/submissions", status_code=201)
def submit_for_approval(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
    service: PublishingService = ServiceDep,
) -> dict[str, Any]:
    """Submit library content into an approval process.

    The workflow snapshots the process's steps, so editing the template later
    cannot change a review that is already in flight.
    """
    workflow = service.submit(
        content=payload.get("content") or [],
        process_id=str(payload.get("process_id") or ""),
        actor=actor,
        room_id=room_id,
        comment=payload.get("comment"),
        source=_source("POST", "/submissions"),
    )
    return view_workflow(workflow)


@router.get("/workflows")
def list_workflows(
    room_id: str | None = Query(default=None),
    status: str | None = Query(default=None, description="Pending | Approved | Rejected"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: PublishingService = ServiceDep,
) -> dict[str, Any]:
    """The approval queue.

    Paginated the way the researched ``GET /approvalWorkflows`` contract
    documents it: ``limit`` 1-1000 defaulting to 100, ``offset`` defaulting to
    0, and a ``total_count`` so a poller knows when to stop walking.
    """
    return service.list_workflows(
        room_id=room_id,
        status=status,
        limit=limit,
        offset=offset,
        # The pagination link is a promise about a URL this router serves, so it
        # is built here rather than written into the domain module.
        queue_path=f"{router.prefix}/workflows",
    )


@router.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: str, service: PublishingService = ServiceDep) -> dict[str, Any]:
    workflow = service.get_workflow(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail=f"approval workflow {workflow_id} not found")
    return workflow


@router.post("/workflows/{workflow_id}/steps/{step_key}")
def decide_step(
    workflow_id: str,
    step_key: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    service: PublishingService = ServiceDep,
) -> dict[str, Any]:
    """Approve or reject one step.

    The response is the whole workflow, with the resulting queue state under
    ``derived``. Steps advance in order: only the earliest still-``Pending`` step
    accepts a decision.
    """
    return service.decide(
        workflow_id,
        step_key,
        decision=str(payload.get("decision") or ""),
        actor=actor,
        comment=payload.get("comment"),
        source=_source("POST", f"/workflows/{workflow_id}/steps/{step_key}"),
    )


# --------------------------------------------------------------------------- #
# Publication
# --------------------------------------------------------------------------- #


@router.post("/publish")
def publish(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
    service: PublishingService = ServiceDep,
) -> dict[str, Any]:
    """Publish library content now, or schedule it for a future UTC instant.

    Omit ``publish_at`` to publish immediately. Supplying it defers the whole
    transition until :func:`run_due_publications` sweeps the instant. The result
    is a partial-success envelope: a batch can partly succeed, and every item
    that did not is named in ``errors``.
    """
    return service.publish(
        content=payload.get("content") or [],
        publish_at=payload.get("publish_at"),
        is_send_notification=payload.get("is_send_notification", True),
        comment=payload.get("comment"),
        actor=actor,
        subscribers=payload.get("subscribers") or [],
        room_id=room_id,
        source=_source("POST", "/publish"),
    )


@router.get("/publications")
def list_publications(
    room_id: str | None = Query(default=None),
    status: str | None = Query(default=None, description="scheduled | published"),
    limit: int = Query(default=100, ge=1, le=1000),
    service: PublishingService = ServiceDep,
) -> dict[str, Any]:
    return service.list_publications(room_id=room_id, limit=limit, status=status)


@router.post("/publications/due")
def run_due_publications(
    payload: dict[str, Any] = Body(default_factory=dict),
    service: PublishingService = ServiceDep,
) -> dict[str, Any]:
    """Apply every scheduled publication whose UTC instant has passed.

    There is no background scheduler in this project, so this is the mechanism
    that makes ``publish_at`` real. It follows the documented polling pattern
    ("Poll this endpoint periodically") and is an explicit call rather than a
    side effect of a read, which keeps "reads never mutate" true. ``now`` is
    accepted so a caller - or a test - can sweep against a chosen instant.

    The documents it releases are audited against *this* route, not against the
    ``/publish`` request that scheduled them, because this is the request that
    served the write.
    """
    raw_now = payload.get("now")
    now = parse_publish_at(raw_now) if raw_now else None
    return service.run_due(now=now, source=_source("POST", "/publications/due"))


# --------------------------------------------------------------------------- #
# Destinations and subscribers
# --------------------------------------------------------------------------- #


@router.post("/folders", status_code=201)
def create_folder(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    service: PublishingService = ServiceDep,
) -> dict[str, Any]:
    """Define a Dynamic Folder: a profile destination and a metadata match rule.

    Published content is routed by its own metadata. There is deliberately no
    "publish to this profile" parameter - the researched endpoint refuses to
    offer one for the same reason.
    """
    return service.create_folder(
        payload,
        actor=actor,
        room_id=room_id,
        source=_source("POST", "/folders"),
    )


@router.get("/folders")
def list_folders(
    room_id: str | None = Query(default=None),
    service: PublishingService = ServiceDep,
) -> dict[str, Any]:
    return service.list_folders(room_id=room_id)


@router.post("/subscriptions", status_code=201)
def create_subscription(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    service: PublishingService = ServiceDep,
) -> dict[str, Any]:
    """Register a subscriber who hears about publishes in a room."""
    return service.create_subscription(
        payload,
        actor=actor,
        room_id=room_id,
        source=_source("POST", "/subscriptions"),
    )


@router.get("/subscriptions")
def list_subscriptions(
    room_id: str | None = Query(default=None),
    service: PublishingService = ServiceDep,
) -> dict[str, Any]:
    return service.list_subscriptions(room_id=room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The approval process the demo dataset gets. Two ordered steps so the queue has
#: a step that is not actionable until the one before it is decided, and
#: per-step button labels so that part of the researched schema is visible in the
#: UI rather than only in a payload.
APPROVAL_PROCESS = {
    "name": "Standard content review",
    "sla_hours": 24,
    "steps": [
        {
            "key": "legal",
            "label": "Legal review",
            "approvers": ["sam", "priya"],
            "watchers": ["dana"],
            "approve_button_label": "Sign off",
            "reject_button_label": "Send back",
        },
        {"key": "brand", "label": "Brand review", "approvers": ["dana"]},
    ],
}

#: Drafts, added alongside the published core library rather than by changing it,
#: so the approval queue has something in it without disturbing what the other
#: pages already show. The metadata is what the Dynamic Folders match on.
DRAFTS = [
    ("Renewal Terms - Fabrikam", "pdf", {"region": "emea", "audience": "external"}),
    ("AI Governance Brief", "pdf", {"region": "amer", "audience": "external"}),
    ("Internal Pricing Memo", "pdf", {"region": "amer", "audience": "internal"}),
    ("Q4 Roadmap Deck", "deck", {"region": "emea", "audience": "external"}),
]

DYNAMIC_FOLDERS = [
    {"name": "EMEA buyer-facing", "profile": "emea-buyers", "matches": {"metadata.region": "emea"}},
    {"name": "External collateral", "profile": "public", "matches": {"metadata.audience": "external"}},
]

SUBSCRIBERS = ["a.buyer@northwind.example", "procurement@contoso.example"]

SEED_SOURCE = "seed"


def seed(db, context: dict[str, Any]) -> str:
    """Make the core demo dataset readable as an approval and publication queue.

    The branch did this by editing ``backend/seed.py``, which is a shared file:
    ten of the first twelve workflows rewrote it purely to add their own rows.
    The seeder calls this hook instead, so the demo data travels with the feature
    that needs it.

    Everything is produced by :class:`PublishingService` rather than by writing
    records directly, so the demo rows are created by the same code path the API
    uses and the audit trail they leave is the real one. The only exception is
    the ``document`` rows themselves: those are content the core seeder does not
    own, and it has no publish vocabulary for them.

    Two workflows are left in the queue on purpose: one mid-review, so the queue
    is not empty on first load, and one fully approved, so the publish action is
    reachable without clicking through anything.
    """
    room_ids: list[tuple[str, str]] = context["room_ids"]
    now = context["now"]
    if not room_ids:
        return "no rooms in the demo dataset"

    service = PublishingService(RecordStore(db))
    room_id = room_ids[0][0]

    process = service.create_process(
        APPROVAL_PROCESS, actor="dana", room_id=room_id, source=SEED_SOURCE
    )

    for folder in DYNAMIC_FOLDERS:
        service.create_folder(folder, actor="dana", room_id=room_id, source=SEED_SOURCE)

    for subscriber in SUBSCRIBERS:
        service.create_subscription(
            {"subscriber": subscriber}, actor="dana", room_id=room_id, source=SEED_SOURCE
        )

    drafts: list[str] = []
    for index, (title, kind, metadata) in enumerate(DRAFTS):
        room, _account = room_ids[index % len(room_ids)]
        record = db.create(
            "document",
            {
                "title": title,
                "kind": kind,
                "status": "Draft",
                "metadata": metadata,
                "versions": [{"version_id": "v1", "uploaded_at": now.isoformat(timespec="seconds")}],
            },
            room_id=room,
            actor="dana",
            source=SEED_SOURCE,
        )
        drafts.append(record["id"])

    # One workflow left mid-review, one left approved.
    pending = service.submit(
        content=drafts[:2],
        process_id=process["id"],
        actor="dana",
        comment="Ready for review",
        source=SEED_SOURCE,
    )
    service.decide(pending["id"], "legal", decision="approve", actor="sam", source=SEED_SOURCE)

    cleared = service.submit(
        content=drafts[2:3], process_id=process["id"], actor="dana", source=SEED_SOURCE
    )
    service.decide(cleared["id"], "legal", decision="approve", actor="sam", source=SEED_SOURCE)
    service.decide(cleared["id"], "brand", decision="approve", actor="dana", source=SEED_SOURCE)

    return (
        f"1 approval process, {len(DRAFTS)} draft documents, {len(DYNAMIC_FOLDERS)} dynamic "
        f"folders, {len(SUBSCRIBERS)} subscribers, 2 approval workflows (1 pending, 1 approved)"
    )
