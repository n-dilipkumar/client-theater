"""WF-119: gate an automation on external human approval and resume.

An automation reaches a pause node and the contact is **held** there. A person
decides in an external approval system, which calls one of the two resume webhooks
with the documented ``extras`` body, and the workflow continues on the branch that
decision selected.

The domain lives in :mod:`dsr.approval_gate`: the vocabulary and the recorded
decisions, the refusal hierarchy, the pure envelope and resume rules, and
:class:`~dsr.approval_gate.engine.ApprovalGate` over the record store. This module
is the HTTP surface and nothing more. It declares its own prefix, takes its
dependency from ``dsr.deps`` (never ``dsr.api``), and lets the plugin host mount it
by discovery, so no shared file is edited to add it.

Two details are load-bearing:

* Every write's audit ``source`` is built from the live request method and path,
  so every audit row names a route the app actually serves.
* The resume webhook resolves its gate from a token in the URL and its hold from
  the ``extras`` object in the body, so the correlation key is verified and never
  trusted.

The resume mode is async, which was chosen by Jev at audit
``jev-20261014T105812-18180-92928`` and is recorded in
:mod:`dsr.approval_gate.vocabulary`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from dsr.approval_gate import vocabulary as vocab
from dsr.approval_gate.engine import ApprovalGate, seed  # seed re-exported for the seeder
from dsr.approval_gate.errors import REFUSAL_STATUS, ApprovalGateRefusal
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-119-approval-gate",
    "ticket": "WF-119",
    "prefix": "/api/wf-119",
    "name": "Gate an automation on external human approval and resume",
    "description": (
        "Hold a contact at a pause node until a person approves or refuses it in an "
        "external system, then resume the automation on the branch that decision "
        "selected. The hold is a record, so it survives a process restart, and the "
        "documented extras object resumes it only after its correlation key is verified."
    ),
}

#: Owned by this ticket and therefore unique. Every concrete path below is under
#: it, so nothing here can shadow another feature's route or a core one.
router = APIRouter(prefix="/api/wf-119", tags=["WF-119"])


def _refusal(request: Request, exc: ApprovalGateRefusal) -> JSONResponse:
    """One handler for the family, dispatched on the concrete subclass."""
    status, code = REFUSAL_STATUS.get(type(exc), (422, "approval_gate_refused"))
    return JSONResponse(
        status_code=status,
        content={
            "error": code,
            "detail": str(exc),
            "problems": list(getattr(exc, "problems", []) or []),
        },
    )


EXCEPTION_HANDLERS = {ApprovalGateRefusal: _refusal}

__all__ = ["EXCEPTION_HANDLERS", "FEATURE", "router", "seed"]


def engine(store: RecordStore) -> ApprovalGate:
    return ApprovalGate(store)


def _actor(request: Request) -> str:
    return str(request.headers.get("X-Actor") or "dana")


def _source(request: Request) -> str:
    """The audit ``source`` for a write: the route that actually served it.

    Using the live request path rather than a hand-written string means the audit
    row always names a route the app serves, including when a path parameter
    carried a real value.
    """
    return f"{request.method} {request.url.path}"


# --------------------------------------------------------------------------- #
# Vocabulary and decisions
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary_route() -> dict[str, Any]:
    """The rules, the envelope, the window and the decisions, without a store."""
    return vocab.vocabulary()


@router.get("/decisions")
def decisions_route() -> dict[str, Any]:
    """Every judgement this build recorded, with its audit id and the alternative."""
    return {
        "resume_mode": vocab.resume_mode_record(),
        "count": len(vocab.decisions()),
        "decisions": vocab.decisions(),
        "not_built": vocab.not_built(),
    }


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(store: RecordStore = StoreDep) -> dict[str, Any]:
    """Counts the page leads with."""
    return engine(store).summary()


@router.get("/gates")
def list_gates(store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every configured pause node, with the URLs this app serves for it."""
    gates = engine(store).list_gates()
    return {"count": len(gates), "gates": gates}


@router.get("/gates/{key}")
def read_gate(key: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """One gate."""
    record = engine(store).require_gate(key)
    return engine(store).gate_view(record)


@router.get("/holds")
def list_holds(
    gate_key: str | None = Query(default=None, description="Filter by gate key"),
    state: str | None = Query(default=None, description="held, resumed or expired"),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Every held contact, newest first, each with its derived expiry."""
    holds = engine(store).list_holds(gate_key=gate_key, state=state)
    return {"count": len(holds), "holds": holds}


@router.get("/holds/{hold_id}")
def read_hold(hold_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """One hold, with whether it is still resumable."""
    hold = engine(store).hold_view(engine(store).require_hold(hold_id))
    return hold


@router.get("/holds/{hold_id}/events")
def hold_events(hold_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """The pause, resume, expire and replay events for one hold."""
    gate = engine(store)
    gate.require_hold(hold_id)
    events = gate.events_for(hold_id)
    return {"count": len(events), "events": events}


# --------------------------------------------------------------------------- #
# Gates
# --------------------------------------------------------------------------- #


@router.post("/gates", status_code=201)
def create_gate(
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Configure a pause node.

    The pause toggle defaults off, which is the shape the vendor's own builder
    has: a workflow holds a contact only once Pause Execution is turned on.
    """
    body = dict(payload)
    extra = {
        key: value
        for key, value in body.items()
        if key
        not in {
            "key",
            "workflow_id",
            "action_key",
            "name",
            vocab.PAUSE_EXECUTION_KEY,
            vocab.BRANCHES_KEY,
            "hold_seconds",
            "action_url",
        }
    }
    return engine(store).create_gate(
        key=body.get("key"),
        workflow_id=body.get("workflow_id"),
        action_key=body.get("action_key"),
        name=body.get("name"),
        pause_execution=body.get(vocab.PAUSE_EXECUTION_KEY),
        branches=body.get(vocab.BRANCHES_KEY),
        hold_seconds=body.get("hold_seconds"),
        action_url=body.get("action_url"),
        extra=extra,
        actor=_actor(request),
        source=_source(request),
    )


@router.patch("/gates/{key}")
def revise_gate(
    key: str,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Edit a gate's pause toggle, branches, window or metadata. The key is immutable."""
    return engine(store).revise_gate(
        key, dict(payload), actor=_actor(request), source=_source(request)
    )


@router.post("/gates/{key}/pause")
def pause_contact(
    key: str,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Hold a contact at the gate.

    This is the endpoint the action's configured URL points at. The body is the
    documented envelope: ``data`` is the mapped field payload, ``extras`` is the
    correlation key, and ``meta`` carries the action key and version.
    """
    return engine(store).pause(key, dict(payload), actor=_actor(request), source=_source(request))


# --------------------------------------------------------------------------- #
# The resume
# --------------------------------------------------------------------------- #


@router.post("/hooks/{token}/{outcome}")
def resume_hook(
    token: str,
    outcome: str,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """One of the two resume webhooks: Success Execution or Failed Execution.

    The URL names the outcome. The body carries ``extras`` and ``branchId``, as
    the specification says; the branch is verified against the gate rather than
    trusted. A repeated resume carrying the same decision is an idempotent
    replay, and one carrying a different decision is refused.
    """
    return engine(store).resume_by_token(
        token,
        outcome,
        dict(payload),
        actor=_actor(request),
        source=_source(request),
    )


@router.post("/holds/{hold_id}/resume")
def resume_hold(
    hold_id: str,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Decide one hold from inside the product.

    This is the same decision the webhook carries, addressed by hold id rather
    than by the correlation key, so a reviewer can act without replaying an
    external system's body.
    """
    body = dict(payload)
    return engine(store).resume_hold(
        hold_id,
        url_outcome=body.get("outcome"),
        body_outcome=None,
        body_branch=body.get(vocab.BRANCH_KEY),
        approver=body.get("approver"),
        source_url=f"{vocab.PREFIX}/holds/{hold_id}/resume",
        actor=_actor(request),
        source=_source(request),
    )


# --------------------------------------------------------------------------- #
# Expiry
# --------------------------------------------------------------------------- #


@router.post("/sweep")
def sweep(request: Request, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Expire every hold that has lapsed.

    Expiry is also evaluated lazily on every read and every resume, so this route
    is a convenience for the page rather than the only way a hold becomes
    expired.
    """
    return engine(store).sweep(actor=_actor(request), source=_source(request))
