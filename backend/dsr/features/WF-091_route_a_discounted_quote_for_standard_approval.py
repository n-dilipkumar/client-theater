"""WF-091: route a discounted quote for standard approval.

The researched workflow, in full. An admin turns **Approvals** on for the quote
object, edits the rule, adds one or more **+ Add filter** conditions against a
chosen object's property, names up to ten approvers, picks **All approvers
required** or **At least one approver required**, and enters the approval note.
A seller clicks **Request approval**, adds **Notes to approver**, and the quote
moves to *Pending approval*. Each approver then **Approves** it or **Requests
changes**, and only an approved quote can be shared with a buyer.

The domain is in :mod:`dsr.quoting_proposals.quote_approval_engine` and its rules in
:mod:`dsr.quoting_proposals.quote_approval_rules`. What lives in this module is the
three things a workflow has to take out of shared files: the route table, the
mapping from domain errors to responses, and the demo data.

Hard rule 4: every write below is handed ``f"{router.prefix}..."``, built from
``router.prefix`` so the audit row and the route table cannot drift. A hardcoded URL
inside a domain function is a defect, and the same class of bug has shipped in this
codebase before: a feature's audit log kept naming a path the app had stopped
serving. ``source`` is a *required* keyword on every writing function here, so
omitting it is a ``TypeError`` at the call site rather than an untraceable row.

One handler for the whole error hierarchy.
:class:`~dsr.quoting_proposals.quote_approval_rules.ApprovalRefusal` is this
workflow's own type and the base of every refusal in it, and each refusal carries
its own code and status, so one handler answers 422 for a rule with no approver and
409 for a share of a quote nobody approved without a table saying which is which.
:class:`~dsr.quoting_proposals.quote_approval_rules.ApprovalNotFound` is a second
type rather than a subclass, because it is a 404 and it is the base of nothing else.
``RecordNotFound`` is deliberately not claimed: the core app already maps it to 404,
and two handlers for one type is a collision the host refuses.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.quoting_proposals import (
    quote_approval_inferences as inferences,
    quote_approval_vocabulary as vocab,
)
from dsr.quoting_proposals.quote_approval_engine import LINE_ITEMS, QuoteApprovalEngine
from dsr.quoting_proposals.quote_approval_rules import ApprovalNotFound, ApprovalRefusal
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-091-route-a-discounted-quote-for-standard-approval",
    "ticket": "WF-091",
    "name": "Route a discounted quote for standard approval",
    "description": (
        "Evaluate a quote's properties against configured filters when a seller "
        "requests approval or tries to publish, enrol the quote with its "
        "approvers, and end in their decision: approve, and the quote may be "
        "shared; request changes, and the seller edits it and submits again."
    ),
    "nav": [{"id": "quote-approval", "label": "Quote approval"}],
}

router = APIRouter(prefix="/api/WF-091", tags=["WF-091"])


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _refusal(request: Request, exc: ApprovalRefusal) -> JSONResponse:
    """A domain refusal, answered with the code and status it carries.

    ``errors`` rides along because a rule can be wrong in more than one way at once.
    A page puts each message beside the input that caused it rather than in one
    combined sentence.
    """
    return JSONResponse(
        status_code=exc.status,
        content={
            "error": exc.code,
            "detail": exc.detail,
            "status": exc.status,
            "errors": exc.errors,
        },
    )


def _not_found(request: Request, exc: ApprovalNotFound) -> JSONResponse:
    """No such rule or no such enrolment."""
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status, "id": exc.record_id},
    )


EXCEPTION_HANDLERS = {ApprovalRefusal: _refusal, ApprovalNotFound: _not_found}


# --------------------------------------------------------------------------- #
# The service over the store
# --------------------------------------------------------------------------- #
#
# Built per request from ``StoreDep`` rather than held on ``app.state``, because an
# ``app.state`` entry is exactly the edit to the shared ``dsr/api.py`` that the
# feature host exists to make unnecessary. It holds nothing but the store handle and
# a clock, both constructor arguments, so a test constructs one over rows of its
# own with a clock it controls.


def get_service(store: RecordStore = StoreDep) -> QuoteApprovalEngine:
    return QuoteApprovalEngine(store)


ServiceDep = Depends(get_service)


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="Every published vocabulary")
def vocabulary() -> dict[str, Any]:
    """Filters, approvers, requirements, states, decisions and channels.

    Served as data so a client renders its pickers from the server's vocabulary
    rather than from a list compiled into a page, and a value added here reaches
    every client at once.
    """
    return vocab.catalogue()


@router.get("/inferences", summary="Every judgement call this workflow rests on")
def inference_report() -> dict[str, Any]:
    """What the research fixes, what it leaves open, and which reading was taken.

    The self-approval reading is served on its own under ``self_approval`` so a page
    can show it beside a rule rather than making a reader find it in a list of seven.
    """
    return {
        "count": inferences.count(),
        "decisions": inferences.describe(),
        "self_approval": inferences.self_approval(),
    }


# --------------------------------------------------------------------------- #
# Approval rules
# --------------------------------------------------------------------------- #


@router.get("/rules", summary="The configured approval rules")
def list_rules(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    service: QuoteApprovalEngine = ServiceDep,
) -> dict[str, Any]:
    """Every rule, oldest first, so the page shows them in the order they were added."""
    listed = service.list_rules(room_id=room_id, limit=limit)
    return {"room_id": room_id, "count": len(listed), "rules": listed}


@router.post("/rules", status_code=201, summary="Configure an approval rule")
def create_rule(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    service: QuoteApprovalEngine = ServiceDep,
) -> dict[str, Any]:
    """Steps one to three of the researched flow, in one call.

    The filter builder, the approver list, the requirement and the approval note are
    saved together because the research's UI refuses to save a rule that is missing
    any of them, so a half-configured rule is a state this product does not create.

    The approver cap is enforced here rather than at enrolment: "Assign up to 10
    approvers" is a statement about the rule, and a rule with eleven approvers is
    wrong at save time rather than when a quote arrives.
    """
    return service.create_rule(
        payload, actor=actor, source=f"POST {router.prefix}/rules", room_id=room_id
    )


@router.get("/rules/{rule_id}", summary="One approval rule")
def read_rule(rule_id: str, service: QuoteApprovalEngine = ServiceDep) -> dict[str, Any]:
    return {"rule": service.rule(rule_id)}


@router.patch("/rules/{rule_id}", summary="Turn a rule on or off")
def patch_rule(
    rule_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    service: QuoteApprovalEngine = ServiceDep,
) -> dict[str, Any]:
    """The Quotes switch on the rule, and any other field, revalidated.

    The researched step three ends with "toggle the **Quotes** switch on to
    activate". This is that toggle, as a merge patch so turning the switch does not
    silently reset the filters. Turning it off enrols nobody: an inactive rule reads
    as a rule that did not match, and the answer says so.
    """
    return service.patch_rule(
        rule_id, payload, actor=actor, source=f"PATCH {router.prefix}/rules/{rule_id}"
    )


@router.delete("/rules/{rule_id}", summary="Remove an approval rule")
def delete_rule(
    rule_id: str,
    actor: str | None = Query(default=None),
    service: QuoteApprovalEngine = ServiceDep,
) -> dict[str, Any]:
    """Remove a rule. The enrolments it created are left alone and still explain themselves."""
    return service.delete_rule(
        rule_id, actor=actor, source=f"DELETE {router.prefix}/rules/{rule_id}"
    )


# --------------------------------------------------------------------------- #
# Conditions
# --------------------------------------------------------------------------- #


@router.get("/quotes/{quote_id}/conditions", summary="Why approval is required")
def quote_conditions(quote_id: str, service: QuoteApprovalEngine = ServiceDep) -> dict[str, Any]:
    """The researched "**View approval conditions**".

    A GET because it checks and writes nothing. It runs the same evaluation the
    enrolment runs, so what a seller is shown before submitting is what will be
    enforced after they submit.
    """
    return service.conditions(quote_id)


@router.get("/quotes/{quote_id}/state", summary="The approval state of a quote")
def quote_state(quote_id: str, service: QuoteApprovalEngine = ServiceDep) -> dict[str, Any]:
    """Which state the quote is in, whether it may be shared, and what releases a lock.

    Both the quote's own ``hs_status`` and this workflow's latest enrolment are
    returned, and ``authority`` says which one answered. The research exposes the
    state on the quote object through GET and PATCH, and says the approval states
    are "primarily managed via the UI and approval workflows", so the enrolment is
    the newer fact and the quote's stated value is still reported beside it.
    """
    return service.quote_state(quote_id)


# --------------------------------------------------------------------------- #
# Submitting and deciding
# --------------------------------------------------------------------------- #


@router.post("/quotes/{quote_id}/submit", status_code=201, summary="Request approval")
def submit_for_approval(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    trigger: str | None = Query(default=None, description="submit_approval, publish or share"),
    service: QuoteApprovalEngine = ServiceDep,
) -> dict[str, Any]:
    """The researched step four, and the publish and share attempts with it.

    "click **Request approval** -> add **Notes to approver** -> the quote moves to
    *Pending approval*". A ``trigger`` of ``publish`` or ``share`` is the same call
    because "approval enrolment and notification fire on the publish/share attempt".

    Four answers, and all four are 201 with a body rather than three answers and a
    status code:

    * ``required: true`` with an enrolment, because a rule matched and an approver
      other than the creator must decide;
    * an exempt enrolment, because every matched rule's only approver is the creator;
    * ``reason: no_rule_matched``, because no filter matched;
    * ``reason: matched_rule_is_inactive``, because a rule matched and its switch is
      off.

    The last two write nothing on purpose. A seller who asked for approval deserves
    to be told there was no rule to apply rather than to be given an empty pending
    request, and the two reasons have different fixes: write a filter, or turn a
    switch on.
    """
    body = dict(payload or {})
    return service.submit(
        quote_id,
        actor=actor,
        source=f"POST {router.prefix}/quotes/{quote_id}/submit",
        trigger=trigger or body.get("trigger") or vocab.TRIGGER_SUBMIT,
        notes_to_approver=body.get(vocab.NOTES_TO_APPROVER) or body.get("notes_to_approver"),
        room_id=room_id,
    )


@router.get("/requests", summary="Approval requests, filterable")
def list_requests(
    status: str | None = Query(default=None, description="PENDING_APPROVAL, APPROVED or REJECTED"),
    quote_id: str | None = Query(default=None),
    approver: str | None = Query(
        default=None, description="Only requests this approver must decide"
    ),
    room_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    service: QuoteApprovalEngine = ServiceDep,
) -> dict[str, Any]:
    """The researched "quotes index page filtered to **Pending approval**".

    Each filter is a JSON path in the record's own payload, resolved through the
    dynamic index, so a team that adds its own field can filter on it here too
    without a migration. ``approver`` finds the enrolments that carry a decision row
    for that person, which is what "waiting on me" means.
    """
    listed = service.list_enrolments(
        status=status, quote_id=quote_id, approver=approver, room_id=room_id, limit=limit
    )
    return {"room_id": room_id, "count": len(listed), "requests": listed}


@router.get("/requests/{enrolment_id}", summary="One approval request")
def read_request(enrolment_id: str, service: QuoteApprovalEngine = ServiceDep) -> dict[str, Any]:
    """One enrolment with its decisions, its tally and its conditions panel."""
    return {"request": service.enrolment_view(service.enrolment(enrolment_id))}


@router.post("/requests/{enrolment_id}/decide", summary="Approve or request changes")
def decide(
    enrolment_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    approver: str | None = Query(default=None, description="The approver making this decision"),
    actor: str | None = Query(default=None),
    service: QuoteApprovalEngine = ServiceDep,
) -> dict[str, Any]:
    """The researched step five, both branches on one route.

    "**Approve** (with an optional message) or **Request changes** (enter changes ->
    **Reject**)". The two branches differ only in the word and in what they leave
    behind, so keeping them on one path is what makes it impossible for them to
    disagree about who may decide.

    A change request needs its reason. "enter changes" is what makes a request for
    changes an instruction rather than a veto the seller cannot act on.

    The outcome is the requirement, not the vote. One approval satisfies an "At
    least one approver required" rule and every approval satisfies an "All approvers
    required" one. A change request ends the round whatever the requirement says,
    because the research says the seller then edits and re-submits.
    """
    body = dict(payload or {})
    return service.decide(
        enrolment_id,
        body.get("decision") or vocab.DECISION_APPROVE,
        actor=actor,
        source=f"POST {router.prefix}/requests/{enrolment_id}/decide",
        approver=approver or body.get("approver"),
        message=body.get("message") or body.get("reason"),
    )


@router.post("/quotes/{quote_id}/share", summary="Share an approved quote")
def share(
    quote_id: str,
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    service: QuoteApprovalEngine = ServiceDep,
) -> dict[str, Any]:
    """The researched gate on sharing.

    "only on approval can the quote be **Share**d (state ``Shared**) and sent to the
    buyer." A share of any other state is refused with that sentence, and the refusal
    is written to the quote's activity log, so "the product stopped a share" is a row
    a reviewer can find rather than a gap.

    This route does not write to the quote. The quote is a record another workflow
    owns, and its ``hs_status`` is left alone. The answer reports the state the share
    moved from and the state a quote in that condition would carry.
    """
    return service.share(
        quote_id,
        actor=actor,
        source=f"POST {router.prefix}/quotes/{quote_id}/share",
        room_id=room_id,
    )


# --------------------------------------------------------------------------- #
# The log and the board
# --------------------------------------------------------------------------- #


@router.get("/quotes/{quote_id}/activity", summary="A quote's approval activity")
def quote_activity(
    quote_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    service: QuoteApprovalEngine = ServiceDep,
) -> dict[str, Any]:
    """The researched activity log for one quote.

    "approval activity is written to the quote activity log automatically". Three of
    the five names are the research's own strings and each row carries ``sourced``, so
    a reader can tell a quoted activity from one this build added.
    """
    listed = service.activities(quote_id, limit=limit)
    return {"quote_id": quote_id, "count": len(listed), "activity": listed}


@router.get("/notifications", summary="The recorded approval notifications")
def list_notifications(
    quote_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    service: QuoteApprovalEngine = ServiceDep,
) -> dict[str, Any]:
    """Every recorded notification, with the recipient, the channel and the moment.

    Every row carries ``dispatched_by: "recorded"`` and ``delivered: false``. This
    product has no mail transport and no chat integration, so a row that read like a
    delivery receipt would be something a reviewer has to find in a diff rather than
    being told.
    """
    listed = service.notifications(quote_id=quote_id, limit=limit)
    return {
        "count": len(listed),
        "notifications": listed,
        "recorded_only_note": vocab.RECORDED_NOT_DELIVERED_NOTE,
    }


@router.get("/summary", summary="What is waiting on an approver")
def room_summary(
    room_id: str | None = Query(default=None),
    service: QuoteApprovalEngine = ServiceDep,
) -> dict[str, Any]:
    """Counts by state, and the number of approvers with something outstanding.

    ``awaiting_an_approver`` is the number a reviewer acts on, which is why it is
    named separately from the ``PENDING_APPROVAL`` count: the two are equal today,
    and a client that reads the second one keeps working if that ever stops being
    true.
    """
    return service.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #
#
# The branch added this to ``backend/seed.py``, which is a shared file that ten of
# the first twelve workflows rewrote purely to add their own rows. The seeder calls
# the hook below instead, so the demo travels with the feature that needs it.


def _demo_rooms(rooms: list[Any]) -> list[tuple[str, str]]:
    """The seeder's room list, whatever shape it arrives in.

    Normalised here because the seeder passes ``(room_id, name)`` pairs on some paths
    and bare ids on others, and a seed that raises on one of them loses its demo.
    """
    normalised: list[tuple[str, str]] = []
    for entry in rooms:
        if isinstance(entry, (tuple, list)):
            normalised.append((str(entry[0]), str(entry[1]) if len(entry) > 1 else ""))
        else:
            normalised.append((str(entry), ""))
    return normalised


def seed(db, context: dict[str, Any]) -> str:
    """Seed the states the research says matter, not just the happy path.

    Everything is produced by driving the real
    :class:`~dsr.quoting_proposals.quote_approval_engine.QuoteApprovalEngine` over
    the demo rooms, so the demo's statuses, decisions and notifications are what this
    workflow actually writes rather than rows composed by hand. A seeded enrolment
    and an API enrolment cannot disagree.

    What is here, and why each is here:

    * a rule that fires on a **deeply discounted line item**, because that is the
      filter the evidence names: "Require approval on quotes where a specific line
      item is above a certain discount amount";
    * a **pending** enrolment with two approvers and one approval recorded, so the
      "All approvers required" requirement is visible as a tally with somebody still
      outstanding rather than as a boolean;
    * an **approved** quote, shared, so the only state a share is allowed from is
      visible as a success rather than as an absence;
    * a **rejected** quote carrying the researched change request, so the seller's
      next step is on screen;
    * a **sole-approver** rule whose approver is the creator, so the researched
      exemption is a row rather than a paragraph in a docstring;
    * a **notification** set, recorded rather than delivered, with the sender's
      ``dispatched_by`` visible.

    Every character of the returned string is encodable by cp1252. A single
    RIGHTWARDS ARROW in one recovered feature's return string broke the entire
    seeder on a Windows console.
    """
    rooms = _demo_rooms(context.get("room_ids") or [])
    if not rooms:
        return "0 rules, 0 quotes (no demo rooms to scope an approval rule to)"

    from dsr.store import RecordStore

    base: Any = context.get("now")
    service = QuoteApprovalEngine(RecordStore(db), now=(lambda: base) if base else None)
    source = "seed"
    actor = "dana"
    outcomes: list[str] = []

    # Four plans, one per state the workflow distinguishes, spread over the demo
    # rooms so every room a plan touches has its own rule in it.
    plans: tuple[dict[str, Any], ...] = (
        {
            "state": "pending",
            "segment": "renewal-seat",
            "summary": "1 pending request on a discounted line item, 1 of 2 approvers decided",
            "rule": {
                "key": "deep-line-discount",
                "label": "Deep discount on any line item",
                "filters": [
                    {"object": "quote", "property": "segment", "operator": "is", "value": ""},
                    {"object": "line_item", "property": "discount", "operator": "gt", "value": 25},
                ],
                "approvers": ["dana", "sam", "priya"],
                "requirement": "all",
                "approval_note": "Check the margin before this discount goes to the buyer.",
                "channels": ["in_app", "email"],
            },
            "quote": {
                "name": "Northwind renewal, 40 percent off the seat line",
                "amount": 48000,
                "discount": 40,
            },
            "decide": "partial",
        },
        {
            "state": "approved",
            "segment": "expansion-large",
            "summary": "1 approved quote, then shared",
            "rule": {
                "key": "large-quote",
                "label": "Quote above 100,000 needs a signature",
                "filters": [
                    {"object": "quote", "property": "segment", "operator": "is", "value": ""},
                    {"object": "quote", "property": "amount", "operator": "gt", "value": 100000},
                ],
                "approvers": ["sam", "priya"],
                "requirement": "all",
                "approval_note": "Two signatures are required on a quote this size.",
            },
            "quote": {"name": "Halcyon expansion, 150,000", "amount": 150000, "discount": 10},
            "decide": "all",
        },
        {
            "state": "rejected",
            "segment": "pilot-small",
            "summary": "1 rejected request with the requested changes",
            "rule": {
                "key": "any-discount",
                "label": "Any discount at all needs approval",
                "filters": [
                    {"object": "quote", "property": "segment", "operator": "is", "value": ""},
                    {"object": "quote", "property": "discount", "operator": "gt", "value": 0},
                ],
                "approvers": ["sam", "priya"],
                "requirement": "all",
                "approval_note": "Every discount needs a second pair of eyes.",
            },
            "quote": {"name": "Northwind pilot, 5 percent off", "amount": 9000, "discount": 5},
            "decide": "reject",
        },
        {
            "state": "exempt",
            "segment": "pilot-sole",
            "summary": "1 quote that needed no approval, because its only approver wrote it",
            "rule": {
                "key": "sole-approver",
                "label": "One approver for everything",
                "filters": [
                    {"object": "quote", "property": "segment", "operator": "is", "value": ""},
                    {"object": "quote", "property": "amount", "operator": "gt", "value": 0},
                ],
                "approvers": ["dana"],
                "requirement": "all",
                "approval_note": "The one approver on this account reviews every quote.",
            },
            "quote": {
                "name": "Halcyon pilot written by the sole approver",
                "amount": 4200,
                "discount": 2,
            },
        },
    )

    rules_made = 0
    quotes_made = 0
    for index, plan in enumerate(plans):
        room_id = str(rooms[index % len(rooms)][0])
        # The first filter of every demo rule is a segment equality. Its value is
        # filled in here rather than written four times, so the plan's segment and
        # its rule's scope cannot drift apart. The filter list is copied first, so
        # the plan tuple stays immutable across its one iteration.
        rule_payload = {
            **plan["rule"],
            "filters": [
                {**one, "value": plan["segment"]} if one.get("property") == "segment" else dict(one)
                for one in plan["rule"]["filters"]
            ],
        }
        service.create_rule(rule_payload, actor=actor, source=source, room_id=room_id)
        rules_made += 1

        # A scope only this plan's rule can match, so two rules in the same room do
        # not enrol each other's quotes. Every demo rule filters on `segment` first.
        # A rule that cannot be scoped to its own demo quote is a demo where every
        # plan enrols every quote and the states on screen are wrong.
        quote_payload: dict[str, Any] = {
            **plan["quote"],
            vocab.HS_STATUS: vocab.STATE_DRAFT,
            "segment": plan["segment"],
            "creator": "dana" if plan["state"] != "rejected" else "marcus",
        }
        quote = service.store.create(
            vocab.SOURCE_QUOTES,
            quote_payload,
            room_id=room_id,
            actor=actor,
            source=source,
        )
        quotes_made += 1
        service.store.create(
            LINE_ITEMS,
            {
                "quote_id": quote["id"],
                "sku": "SEAT-STD",
                "quantity": 100,
                "unit_price": float(plan["quote"]["amount"]) / 100,
                "discount": plan["quote"]["discount"],
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )

        answer = service.submit(
            quote["id"],
            actor=actor,
            source=source,
            room_id=room_id,
            notes_to_approver="Please review.",
        )
        if answer.get("enrolled") and answer.get("enrolment"):
            record = service.enrolment(answer["enrolment"]["id"])
            enrolment_id = record["id"]
            if plan.get("decide") == "reject":
                service.decide(
                    enrolment_id,
                    vocab.DECISION_REQUEST_CHANGES,
                    actor="sam",
                    source=source,
                    approver="sam",
                    message="Cut the discount to 15 percent and resubmit.",
                )
            elif plan.get("decide") == "partial":
                service.decide(
                    enrolment_id, vocab.DECISION_APPROVE, actor="sam", source=source, approver="sam"
                )
            elif plan.get("decide") == "all":
                for identity in ("sam", "priya"):
                    service.decide(
                        enrolment_id,
                        vocab.DECISION_APPROVE,
                        actor=identity,
                        source=source,
                        approver=identity,
                    )
                service.share(quote["id"], actor=actor, source=source, room_id=room_id)
        outcomes.append(str(plan["summary"]))

    return (
        f"{rules_made} approval rules across {len(rooms)} room(s), {quotes_made} quotes "
        f"({'; '.join(outcomes)}), notifications recorded not delivered"
    )
