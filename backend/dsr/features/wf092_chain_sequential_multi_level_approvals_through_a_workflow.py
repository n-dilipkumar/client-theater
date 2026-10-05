"""WF-092: chain sequential multi-level approvals through a workflow.

The rules live in :mod:`dsr.quoting_proposals.approval_chain_rules` and the reads and
writes in :mod:`dsr.quoting_proposals.approval_chain_engine`. Neither knows anything
about HTTP. This module is the four things a feature adds and nothing else:

* the route table, an ``APIRouter`` this feature owns;
* the error mapping, an ``EXCEPTION_HANDLERS`` export the host attaches, because
  FastAPI accepts exception handlers on the app object only;
* the demo rows, a ``seed(db, context)`` hook the seeder calls;
* the vocabulary and the inferences, served from one place so the panel cannot drift
  from the rules that validate it.

Dependencies come from :mod:`dsr.deps`. Nothing here imports ``dsr.api`` and nothing
opens SQLite, so the enforced test that greps feature modules for those two things
passes by construction.

Every write passes a ``source`` built from ``router.prefix`` and the route's own path,
rather than a label written once. That is what keeps every audit row naming a URL the
app actually serves.

Source: ``docs/research/digital-sales-room-workflows/wf/WF-092.md``, issue 157.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.quoting_proposals import (
    approval_chain_inferences as inferences,
    approval_chain_vocabulary as vocab,
)
from dsr.quoting_proposals.approval_chain_engine import (
    ApprovalChainEngine,
    DuplicateWorkflow,
    WorkflowNotFound,
)
from dsr.quoting_proposals.approval_chain_rules import ApprovalRuleError
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-092-chain-sequential-multi-level-approvals-through-a-workflow",
    "ticket": "WF-092",
    "name": "Chain sequential multi-level approvals through a workflow",
    "description": (
        "Branch a quote on its amount, push a ranked approval step onto each "
        "qualifying quote, and run the chain so the last decision writes APPROVED or "
        "REJECTED. A lower-priority approver is never notified before every approver "
        "at the current priority has decided."
    ),
    "nav": [
        {
            "id": "wf-092-chain-sequential-multi-level-approvals-through-a-workflow",
            "label": "Quote approval chain",
        }
    ],
}

router = APIRouter(prefix="/api/wf-092", tags=["WF-092"])


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _approval_rule_error(request: Request, exc: ApprovalRuleError) -> JSONResponse:
    """A researched rule refused the request.

    422 rather than 400: the request was well-formed and the rules are what refused it.
    The core ``AuditError`` handler decides its status by searching the message for the
    words "conflict" or "exists", which would make a status code a function of prose.
    This type is the workflow's own, so the mapping is declared here.
    """
    return JSONResponse(
        status_code=422, content={"error": "approval_rule_refused", "detail": str(exc)}
    )


def _workflow_not_found(request: Request, exc: WorkflowNotFound) -> JSONResponse:
    """No approval workflow exists yet."""
    return JSONResponse(
        status_code=404, content={"error": "workflow_not_found", "detail": str(exc)}
    )


def _duplicate_workflow(request: Request, exc: DuplicateWorkflow) -> JSONResponse:
    """A second approval workflow was requested.

    409 rather than 422: the request was valid, and the conflict is with an existing
    workflow. "You can't duplicate the workflow or create a new workflow to use for
    quote approvals."
    """
    return JSONResponse(
        status_code=409, content={"error": "workflow_is_single", "detail": str(exc)}
    )


EXCEPTION_HANDLERS = {
    ApprovalRuleError: _approval_rule_error,
    WorkflowNotFound: _workflow_not_found,
    DuplicateWorkflow: _duplicate_workflow,
}


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


def get_engine(store: RecordStore = StoreDep) -> ApprovalChainEngine:
    """The engine, built on the shared audited store.

    Composed from the documented dependency seam rather than reaching for
    ``request.app.state``, so a test can construct it and the audit row is still written
    in the same transaction as the change.
    """
    return ApprovalChainEngine(store)


EngineDep = Depends(get_engine)


def _source(verb: str, suffix: str) -> str:
    """The audit ``source`` for a write: the path this router actually serves."""
    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# The researched surface
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every name, ceiling and cap the rules enforce, served from one place.

    The page renders these rather than repeating them, so a panel cannot tell the user
    one cap while the validator enforces another.
    """
    return vocab.describe()


@router.get("/inferences")
def read_inferences() -> dict[str, Any]:
    """Every judgement call this workflow makes, and what it deliberately leaves out.

    Served so a reviewer reads the list instead of reconstructing it from a diff.
    """
    return inferences.describe()


# --------------------------------------------------------------------------- #
# The single workflow
# --------------------------------------------------------------------------- #


@router.get("/workflow")
def read_workflow(engine: ApprovalChainEngine = EngineDep) -> dict[str, Any]:
    """The one approval workflow."""
    return engine.get_workflow()


@router.post("/workflow")
def create_workflow(
    engine: ApprovalChainEngine = EngineDep,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Create the approval workflow, or return the one that already exists.

    The research is explicit that this workflow is single, so a second one is refused
    rather than stored.
    """
    workflow = engine.ensure_workflow(
        source=_source("POST", "/workflow"),
        name=str(body.get("name") or "Sequential quote approval"),
        re_enroll=bool(body.get(vocab.RE_ENROL_KEY)),
    )
    return {"workflow": workflow, "single": True}


@router.post("/workflow/re-enrol-toggle")
def toggle_re_enrol(
    engine: ApprovalChainEngine = EngineDep,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Turn the re-enrolment switch on or off.

    "Re-enrollment can be toggled for quotes that need re-approval after edits."
    """
    engine.get_workflow()
    updated = engine.store.update(
        engine.get_workflow()["id"],
        {vocab.RE_ENROL_KEY: bool(body.get(vocab.RE_ENROL_KEY))},
        actor="wf-092",
        source=_source("POST", "/workflow/re-enrol-toggle"),
    )
    return {"workflow": _payload(updated)}


# --------------------------------------------------------------------------- #
# Branches
# --------------------------------------------------------------------------- #


@router.get("/branches")
def list_branches(engine: ApprovalChainEngine = EngineDep) -> dict[str, Any]:
    """Every configured branch, each with the priority levels it pushes."""
    return {"branches": engine.list_branches_with_steps()}


@router.post("/branches")
def create_branch(
    engine: ApprovalChainEngine = EngineDep,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Add one branch on a quote property, with the ranked approvers it pushes.

    Both researched caps are enforced before any row is written: at most five
    sequences, and at most ten approvers in a sequence.
    """
    branch = engine.add_branch(
        source=_source("POST", "/branches"),
        name=str(body.get("name") or "Quote amount branch"),
        prop=str(body.get("property") or vocab.PROPERTY_QUOTE_AMOUNT),
        operator=str(body.get("operator") or "greater_than"),
        threshold=float(body.get("threshold") or vocab.DEFAULT_BRANCH_THRESHOLD),
        sequences=list(body.get("sequences") or []),
        room_id=body.get("room_id"),
    )
    return {"branch": branch}


# --------------------------------------------------------------------------- #
# Quotes and enrolment
# --------------------------------------------------------------------------- #


@router.get("/quotes")
def list_quotes(engine: ApprovalChainEngine = EngineDep) -> dict[str, Any]:
    """The quotes a branch can be evaluated against."""
    return {"quotes": engine.list_quotes()}


@router.get("/quotes/{quote_id}/evaluate")
def evaluate_quote(quote_id: str, engine: ApprovalChainEngine = EngineDep) -> dict[str, Any]:
    """Which branches qualify for one quote, and why.

    Nothing is written. This is what the workflow editor shows before anybody enrols.
    """
    quote = engine.find_quote(quote_id)
    if quote is None:
        raise HTTPException(status_code=404, detail=f"quote {quote_id} does not exist")
    return engine.evaluate(quote)


@router.post("/quotes/{quote_id}/enrol")
def enrol_quote(quote_id: str, engine: ApprovalChainEngine = EngineDep) -> dict[str, Any]:
    """Start the approval flow for one quote.

    Each qualifying branch pushes an approval step, and the chain waits on the approvers
    at the first priority. A quote that matched no step is auto-approved, which is the
    researched valve.
    """
    result = engine.enrol(quote_id, source=_source("POST", f"/quotes/{quote_id}/enrol"))
    return result


# --------------------------------------------------------------------------- #
# The chain
# --------------------------------------------------------------------------- #


@router.get("/enrolments")
def list_enrolments(engine: ApprovalChainEngine = EngineDep) -> dict[str, Any]:
    """Every enrolment, each with its priority levels."""
    return {"enrolments": engine.list_enrolments()}


@router.get("/enrolments/{enrolment_id}")
def read_enrolment(enrolment_id: str, engine: ApprovalChainEngine = EngineDep) -> dict[str, Any]:
    """One enrolment with its levels, decisions and notifications."""
    return engine.get_enrolment(enrolment_id)


@router.post("/enrolments/{enrolment_id}/decide")
def decide(
    enrolment_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
    engine: ApprovalChainEngine = EngineDep,
) -> dict[str, Any]:
    """Record one approver's decision.

    The decision is refused when the approver sits at a priority the chain has not
    reached, because "Sequential approvals require approval by every approver at each
    priority step".
    """
    approver = str(body.get(vocab.APPROVER_KEY) or "")
    decision = str(body.get("decision") or "")
    if not approver:
        raise HTTPException(status_code=400, detail="an approver is required")
    if not decision:
        raise HTTPException(status_code=400, detail="a decision is required")
    return engine.decide(
        enrolment_id,
        approver,
        decision,
        source=_source("POST", f"/enrolments/{enrolment_id}/decide"),
        actor=str(body.get("actor") or approver),
    )


@router.post("/enrolments/{enrolment_id}/re-enrol")
def re_enrol(enrolment_id: str, engine: ApprovalChainEngine = EngineDep) -> dict[str, Any]:
    """Send a decided chain back to the first priority after an edit.

    Only available when the workflow's re-enrolment switch is on, which is what the
    research says the toggle is for.
    """
    return engine.re_enrol(
        enrolment_id, source=_source("POST", f"/enrolments/{enrolment_id}/re-enrol")
    )


# --------------------------------------------------------------------------- #
# Seed
# --------------------------------------------------------------------------- #

#: The demo scenario the specification gives: "you could set a sales manager as first
#: priority, a sales director as second priority, and a legal representative as third
#: priority."
SEED_SEQUENCES = (
    {
        "priority": 1,
        "approvers": ["sales_manager"],
        "message": "Please approve this quote of {{quote.quote_amount}}.",
        "requirement": vocab.REQUIREMENT_SEQUENTIAL,
    },
    {
        "priority": 2,
        "approvers": ["sales_director"],
        "message": "Second review for {{quote.name}}.",
        "requirement": vocab.REQUIREMENT_SEQUENTIAL,
    },
    {
        "priority": 3,
        "approvers": ["legal_representative"],
        "message": "Legal sign off for {{quote.name}}.",
        "requirement": vocab.REQUIREMENT_SEQUENTIAL,
    },
)


def _payload(record: Any) -> dict[str, Any]:
    """Flatten a stored record into its payload, keeping the record id."""
    if not record:
        return {}
    return {**dict(record.get("data") or {}), "id": record.get("id")}


def seed(db: Any, context: dict[str, Any]) -> str:
    """Create the demo branch and two quotes, one waiting and one approved.

    A feature whose page is empty in the demo is a feature nobody can review, so this
    seeds the researched three-priority chain, a quote above the branch threshold that
    is waiting on its sales manager, and a small quote that auto-approved.

    Every character of the returned string is ASCII, because a single arrow glyph in
    one recovered feature broke the entire seeder on a Windows console.
    """
    from dsr.quoting_proposals.approval_chain_engine import ApprovalChainEngine
    from dsr.store import RecordStore

    store = RecordStore(db)
    engine = ApprovalChainEngine(store)
    now = context.get("now")

    engine.ensure_workflow(source="seed", re_enroll=True)
    engine.add_branch(
        source="seed",
        name="Quotes above 5000",
        sequences=[dict(sequence) for sequence in SEED_SEQUENCES],
    )

    big_id = db.create(
        vocab.SOURCE_QUOTES,
        {
            "name": "Acme expansion",
            "quote_amount": 12000.0,
            "currency": "USD",
            "created_at": now.isoformat() if hasattr(now, "isoformat") else now,
        },
        actor="wf-092",
        source="seed",
    )["id"]

    small_id = db.create(
        vocab.SOURCE_QUOTES,
        {
            "name": "Pilot",
            "quote_amount": 900.0,
            "currency": "USD",
            "created_at": now.isoformat() if hasattr(now, "isoformat") else now,
        },
        actor="wf-092",
        source="seed",
    )["id"]

    waiting = engine.enrol(big_id, source="seed")
    auto = engine.enrol(small_id, source="seed")

    waiting_levels = len(waiting.get("levels") or [])
    auto_text = "auto-approved" if auto.get("auto_approved") else "waiting"
    return (
        f"1 approval workflow, 1 branch over 5000, {waiting_levels} priority levels, "
        f"1 chain waiting on a sales manager, 1 {auto_text} quote"
    )
