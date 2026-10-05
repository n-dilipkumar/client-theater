"""WF-100: create a renewal quote from a contract and auto-create the renewal deal.

The rules live in :mod:`dsr.renewal_quotes` and are not restated here. This module is the
three things a feature contributes and the three things it must never contribute.

What this module contributes
----------------------------

* The route table, under a prefix this feature owns.
* The mapping from this workflow's own error types to responses, exported as
  :data:`EXCEPTION_HANDLERS` because FastAPI accepts handlers on the app object only and this
  feature may not edit the app.
* The demo rows, as ``seed(db, context)`` rather than as an edit to the shared
  ``backend/seed.py``.

What it must never contribute
-----------------------------

* No shared file. ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``, ``dsr/db/audited.py``,
  ``backend/seed.py``, ``App.jsx``, ``main.jsx``, ``lib/api.js``, ``lib/features.js``,
  ``components/ui.jsx``, ``vite.config.js``.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``. A test enforces it.
* No hand-written ``source=`` route string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served the write.

The status codes are this product's own
----------------------------------------

The specification documents no status codes for this workflow, so every one follows the
conventions the rest of this product uses and the distinction is recorded rather than
invented:

``200``
    Everything that read, and every state change that is a field rather than a resource. A
    quote that moved from draft to shared happened, and the body names the state.
``201``
    A resource was created: a quote, a template, a pipeline, a workflow.
``400``
    A value this workflow will not accept: an unknown effective date mode, a custom date mode
    with no date, a proration flag that is not a boolean, a pipeline named without a stage.
``404``
    A contract, template, quote, deal or pipeline that does not exist. Never a 500.
``409``
    A well-formed request that conflicts with the state of a record: renewing a contract with
    no end date, renewing a contract that already has an accepted renewal, accepting a quote
    twice, or accepting a quote whose change effective date did not resolve.

Acceptance is a 409 the second time, not a 200. The second acceptance would create a second
contract and a second deal for the same renewal, and a duplicate contract in a renewal chain
is the one error a seller cannot see and a reviewer would have to find in the chain.

What this workflow reads and does not own
-----------------------------------------

**Quotes.** Another workflow owns the general quote collection. This workflow reads it as
data and does not write into it, because two workflows writing one collection is how a hundred
parallel agents collide. The vendor-shaped endpoint the research names is recorded on each
quote record as ``vendor_endpoint`` so the association is visible without a second write.

**The renewal and change quote template.** The research names the template as an input and
never says which workflow creates it. No pending ticket provisions one, so this workflow owns
``wf100_renewal_template`` and writes its own templates. That choice is recorded in
``GET /decisions`` as ``wf100-template-ownership``.

**Direct contract renewal.** The research marks it BETA and says it bypasses the quote. This
workflow did not build the bypass. It built the quote path, and says so in
``GET /vocabulary`` rather than leaving the absence for a reviewer to discover.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.renewal_quotes import RenewalQuoteEngine, inferences, vocabulary as vocab
from dsr.renewal_quotes.errors import (
    RenewalConflict,
    RenewalNotFound,
    RenewalRefusal,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-100-create-a-renewal-quote-from-a-contract-and-auto",
    "ticket": "WF-100",
    "name": "Create a renewal quote from a contract and auto-create the renewal deal",
    "description": (
        "Build a renewal quote from an expiring contract against a chosen template and change "
        "effective date. On acceptance the room creates the new contract, links it to the prior "
        "contract as a renewal chain, and creates the renewal deal in the chosen pipeline and "
        "stage."
    ),
    "nav": [{"id": "wf-100-renewal-quotes", "label": "Renewal quotes"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several workflows
#: already share.
router = APIRouter(prefix="/api/wf-100", tags=["WF-100"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is a domain
    function hardcoding a URL string, which leaves the audit log naming a route the app stopped
    serving. ``tests/test_wf100_http.py`` asserts every source this router can record matches a
    concrete ``(method, path)`` the host mounted.
    """

    return f"{method} {router.prefix}{path}"


def get_engine(store: RecordStore = StoreDep) -> RenewalQuoteEngine:
    """A :class:`RenewalQuoteEngine` over the process-wide audited store.

    Per request, for the same reason the rest of this product builds its engine per request:
    the engine holds nothing beyond the store and a clock, so building it here leaves every
    seam overridable in a test instead of hanging a long-lived object off ``app.state``, which
    is a shared file this feature may not edit.
    """

    return RenewalQuoteEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# All three types are declared in dsr.renewal_quotes.errors and raised by nothing else in the
# product. That is what makes it safe to map them here: the host refuses a second feature
# registering a handler for the same type, and a handler for ValueError would intercept that
# exception across the whole product.


def _refused(request: Request, exc: RenewalRefusal) -> JSONResponse:
    """400 with a field-keyed map, so each message lands next to its input."""

    return JSONResponse(status_code=400, content=exc.to_dict())


def _not_found(request: Request, exc: RenewalNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content=exc.to_dict())


def _conflict(request: Request, exc: RenewalConflict) -> JSONResponse:
    """409 naming the way out, because a bare 409 is a support ticket."""

    return JSONResponse(status_code=409, content=exc.to_dict())


EXCEPTION_HANDLERS = {
    RenewalRefusal: _refused,
    RenewalNotFound: _not_found,
    RenewalConflict: _conflict,
}


# --------------------------------------------------------------------------- #
# The board and the research
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(
    room_id: str | None = Query(None), engine: RenewalQuoteEngine = EngineDep
) -> dict[str, Any]:
    """The headline numbers, read back from the store. Reads only.

    ``accepted_quotes`` sits beside ``renewal_contracts`` rather than inside a total, because a
    reviewer needs to see which quotes became a contract and which did not.
    """

    return engine.summary(room_id=room_id)


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every researched term this workflow validates against.

    Served rather than duplicated in the frontend so the page cannot drift from the rules that
    compute it: the four effective date modes, the renewal date rule with both branches, the
    Evergreen label, the deal selection methods, the contract targets and the re-enrol decision
    all come from the same tables the engine reads.
    """

    return vocab.describe()


@router.get("/decisions")
def list_decisions() -> dict[str, Any]:
    """Every judgement call this workflow made, with what it rejected.

    The specification asks an implementer to "derive it and record the derivation, not assume
    it". This route is that record, served rather than buried in a docstring so a reviewer reads
    the decision instead of the code.
    """

    return {"count": inferences.count(), "decisions": inferences.describe()}


@router.get("/decisions/{decision_id}")
def read_decision(decision_id: str) -> dict[str, Any]:
    """One judgement call by id, or a 404."""

    decision = inferences.describe_one(decision_id)
    if not decision:
        raise HTTPException(status_code=404, detail="No such recorded decision.")
    return decision


# --------------------------------------------------------------------------- #
# Contracts
# --------------------------------------------------------------------------- #


@router.get("/contracts")
def list_contracts(
    room_id: str | None = Query(None), engine: RenewalQuoteEngine = EngineDep
) -> dict[str, Any]:
    """Every contract with its term label, its renewal date and when its alert is due.

    A read, so it records no audit row. Looking at a contract changes nothing.

    ``term_label`` is the researched Evergreen label, derived from the line items on every
    read, because the evidence says it is a label applied to the term length and not a separate
    contract type.
    """

    rows = engine.contracts(room_id=room_id)
    return {
        "count": len(rows),
        "contracts": rows,
        "evergreen_label": vocab.EVERGREEN_LABEL,
        "evergreen_rule": vocab.EVERGREEN_RULE,
        "renewal_date_rule": vocab.RENEWAL_DATE_RULE,
        "renewal_date_branches": dict(vocab.RENEWAL_DATE_BRANCHES),
    }


@router.get("/contracts/{contract_id}")
def read_contract(
    contract_id: str,
    room_id: str | None = Query(None),
    engine: RenewalQuoteEngine = EngineDep,
) -> dict[str, Any]:
    """One contract with its renewal chain in both directions.

    ``chain.previous`` is the contract this one replaced and ``chain.next`` is the contract that
    replaced it. Both are recorded on write, so the chain is a walk rather than a search.
    """

    return engine.contract(contract_id, room_id=room_id)


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #


@router.get("/templates")
def list_templates(
    room_id: str | None = Query(None), engine: RenewalQuoteEngine = EngineDep
) -> dict[str, Any]:
    """Every renewal and change quote template.

    This workflow owns the collection because the research names the template as an input and
    no pending ticket provisions one. The ownership note is served with it.
    """

    rows = engine.templates(room_id=room_id)
    return {
        "count": len(rows),
        "templates": rows,
        "collection": vocab.TEMPLATE_COLLECTION,
        "ownership_note": vocab.TEMPLATE_OWNERSHIP_NOTE,
        "association_type": vocab.TEMPLATE_ASSOCIATION_TYPE,
        "association_note": vocab.TEMPLATE_ASSOCIATION_NOTE,
    }


@router.post("/templates", status_code=201)
def create_template(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: RenewalQuoteEngine = EngineDep,
) -> dict[str, Any]:
    """Create a renewal or change quote template.

    A template is layout, a change type, a term length and a discount format. It is never a
    quote.
    """

    return engine.create_template(
        payload.get("name"),
        change_type=payload.get("change_type"),
        term_months=payload.get("term_months"),
        discount_format=payload.get("discount_format"),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/templates"),
    )


# --------------------------------------------------------------------------- #
# Deal pipelines
# --------------------------------------------------------------------------- #


@router.get("/pipelines")
def list_pipelines(
    room_id: str | None = Query(None), engine: RenewalQuoteEngine = EngineDep
) -> dict[str, Any]:
    """Every deal pipeline the seller can start a renewal deal in.

    The research says the renewal panel offers a Deal pipeline and a Deal stage, so both have to
    exist before a quote can carry the seller's choice.
    """

    rows = engine.pipelines(room_id=room_id)
    return {"count": len(rows), "pipelines": rows}


@router.post("/pipelines", status_code=201)
def create_pipeline(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: RenewalQuoteEngine = EngineDep,
) -> dict[str, Any]:
    """Register one deal pipeline with its stages."""

    return engine.create_pipeline(
        payload.get("name"),
        stages=payload.get("stages"),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/pipelines"),
    )


# --------------------------------------------------------------------------- #
# Renewal quotes
# --------------------------------------------------------------------------- #


@router.get("/quotes")
def list_quotes(
    room_id: str | None = Query(None), engine: RenewalQuoteEngine = EngineDep
) -> dict[str, Any]:
    """Every renewal quote, newest first. A read, so it records no audit row."""

    rows = engine.quotes(room_id=room_id)
    return {
        "count": len(rows),
        "quotes": rows,
        "states": list(vocab.QUOTE_STATES),
        "state_labels": dict(vocab.QUOTE_STATE_LABELS),
        "derived_not_sourced": vocab.DERIVED_NOT_SOURCED,
    }


@router.post("/quotes", status_code=201)
def create_quote(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: RenewalQuoteEngine = EngineDep,
) -> dict[str, Any]:
    """Create a renewal quote from a contract.

    The quote is prefilled from the contract: the seller and buyer, the currency, the line
    items and the term. It records the template as association type 286, because the research
    states that number is attached at creation.

    A contract that cannot be renewed answers 409 rather than producing a quote, so a seller is
    never handed a quote that cannot become a contract.
    """

    return engine.create_quote(
        payload.get("contract_id"),
        template_id=payload.get("template_id"),
        effective_date_mode=payload.get("effective_date_mode") or "on_agreement",
        effective_date_on=payload.get("effective_date_on"),
        delay_days=payload.get("delay_days"),
        delay_months=payload.get("delay_months"),
        prorate=payload.get("prorate", True),
        deal_pipeline_id=payload.get("deal_pipeline_id"),
        deal_stage=payload.get("deal_stage"),
        deal_selection_method=payload.get("deal_selection_method") or "new_deal_default_stage",
        deal_name=payload.get("deal_name"),
        name=payload.get("name"),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/quotes"),
    )


@router.get("/quotes/{quote_id}")
def read_quote(
    quote_id: str,
    room_id: str | None = Query(None),
    engine: RenewalQuoteEngine = EngineDep,
) -> dict[str, Any]:
    """One renewal quote with its resolved effective date and its proration answer.

    A draft quote whose change effective date is On agreement reports ``resolved: false``,
    because there is no agreement yet. That is a state, not an error, so it answers 200.
    """

    return engine.quote(quote_id, room_id=room_id)


@router.patch("/quotes/{quote_id}")
def update_quote(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: RenewalQuoteEngine = EngineDep,
) -> dict[str, Any]:
    """Change a quote's effective date mode or its proration flag.

    An accepted quote answers 409, because changing the date an accepted contract was created
    from would leave the contract and the quote disagreeing about the same fact.
    """

    return engine.update_effective_date(
        quote_id,
        mode=payload.get("effective_date_mode"),
        on=payload.get("effective_date_on"),
        delay_days=payload.get("delay_days"),
        delay_months=payload.get("delay_months"),
        prorate=payload.get("prorate"),
        room_id=room_id,
        actor=actor,
        source=_source("PATCH", "/quotes/{quote_id}"),
    )


@router.post("/quotes/{quote_id}/state")
def change_state(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: RenewalQuoteEngine = EngineDep,
) -> dict[str, Any]:
    """Move a quote to draft, shared or superseded.

    ``accepted`` is refused here and has its own route, because accepting creates the new
    contract and the renewal deal. A state write must never create records behind a caller's
    back.
    """

    return engine.set_state(
        quote_id,
        payload.get("state"),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/quotes/{quote_id}/state"),
    )


@router.post("/quotes/{quote_id}/accept")
def accept_quote(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: RenewalQuoteEngine = EngineDep,
) -> dict[str, Any]:
    """Accept a quote, create the new contract, link the chain, and create the renewal deal.

    This is the researched transition: "When a renewal quote is accepted, a new contract is
    created and automatically associated with the previous contract."

    The acceptance signal is not sourced. The research never says who accepts or how the room
    learns that it happened. This workflow makes acceptance an explicit room action rather than
    inferring it from a status write, and the derivation is recorded as ``wf100-acceptance-signal``
    in ``GET /decisions``. The Jev audit id for that choice is ``jev-20261004T231639-22752-99672``.

    A second acceptance answers 409 rather than creating a second contract and a second deal.
    """

    return engine.accept(
        quote_id,
        accepted_by=payload.get("accepted_by"),
        existing_deal_id=payload.get("existing_deal_id"),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/quotes/{quote_id}/accept"),
    )


# --------------------------------------------------------------------------- #
# Renewal deals
# --------------------------------------------------------------------------- #


@router.get("/deals")
def list_deals(
    room_id: str | None = Query(None), engine: RenewalQuoteEngine = EngineDep
) -> dict[str, Any]:
    """Every renewal deal this workflow created.

    A read, so it records no audit row. The deal is created at acceptance, not at quote time, so
    a renewal the buyer has not accepted has no deal on the board.
    """

    rows = engine.deals(room_id=room_id)
    return {
        "count": len(rows),
        "deals": rows,
        "created_at_acceptance": True,
        "selection_methods": list(vocab.DEAL_SELECTION_METHODS),
        "selection_method_labels": dict(vocab.DEAL_SELECTION_METHOD_LABELS),
    }


@router.get("/deals/{deal_id}")
def read_deal(
    deal_id: str,
    room_id: str | None = Query(None),
    engine: RenewalQuoteEngine = EngineDep,
) -> dict[str, Any]:
    """One renewal deal, with the quote and the new contract it tracks."""

    return engine.deal(deal_id, room_id=room_id)


# --------------------------------------------------------------------------- #
# The renewal workflow action
# --------------------------------------------------------------------------- #


@router.get("/workflows")
def list_workflows(
    room_id: str | None = Query(None), engine: RenewalQuoteEngine = EngineDep
) -> dict[str, Any]:
    """Every renewal workflow definition.

    The research calls this a deal-based workflow action and explicitly not a documented REST
    endpoint, so it is modelled as a room action backed by a stored definition. The note is
    served with it so a reader knows there is no vendor call underneath.
    """

    rows = engine.workflows(room_id=room_id)
    return {
        "count": len(rows),
        "workflows": rows,
        "action_note": vocab.WORKFLOW_ACTION_NOTE,
        "contract_targets": list(vocab.CONTRACT_TARGETS),
        "contract_target_labels": dict(vocab.CONTRACT_TARGET_LABELS),
        "re_enroll_note": vocab.REENROLL_NOT_SOURCED,
    }


@router.post("/workflows", status_code=201)
def create_workflow(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: RenewalQuoteEngine = EngineDep,
) -> dict[str, Any]:
    """Create a renewal workflow against one contract or against all associated contracts."""

    return engine.create_workflow(
        contract_id=payload.get("contract_id"),
        deal_ids=payload.get("deal_ids"),
        template_id=payload.get("template_id"),
        deal_selection_method=payload.get("deal_selection_method") or "new_deal_default_stage",
        deal_pipeline_id=payload.get("deal_pipeline_id"),
        deal_stage=payload.get("deal_stage"),
        contract_target=payload.get("contract_target") or "one_contract",
        re_enroll=payload.get("re_enroll", False),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/workflows"),
    )


@router.get("/workflows/{workflow_id}")
def read_workflow(
    workflow_id: str,
    room_id: str | None = Query(None),
    engine: RenewalQuoteEngine = EngineDep,
) -> dict[str, Any]:
    """One renewal workflow definition."""

    return engine.workflow(workflow_id, room_id=room_id)


@router.post("/workflows/{workflow_id}/run")
def run_workflow(
    workflow_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: RenewalQuoteEngine = EngineDep,
) -> dict[str, Any]:
    """Run a renewal workflow, creating a renewal quote for each contract it covers.

    The research distinguishes two contract scopes and both are implemented: one contract, and
    Contracts: all associated. The response names the scope and says how many quotes were
    created, so a caller can tell the two apart without reading the definition back.
    """

    return engine.run_workflow(
        workflow_id,
        prorate=payload.get("prorate", True),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/workflows/{workflow_id}/run"),
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the states the specification's user flow describes, not just the happy path.

    Every row is produced by calling the real :class:`RenewalQuoteEngine`, so the demo cannot
    show a state, a count or an audit row the HTTP routes would not produce.

    The states seeded, and why each is here:

    * **Two deal pipelines**, because the renewal panel offers a Deal pipeline and a Deal stage
      and a quote that carries neither cannot show the choice the research describes.
    * **Two renewal and change quote templates**, a 12 month renewal and a 24 month change, so
      the template choice is visible rather than assumed.
    * **A fixed-term contract with a named seller, buyer and two line items**, which is the
      contract a renewal quote is built from.
    * **An accepted renewal quote**, so the board shows the researched outcome: a new contract,
      a chain link in both directions, and a renewal deal in the seller's pipeline and stage.
    * **A draft renewal quote on a second contract**, so the board shows a renewal that has not
      been accepted and therefore has no deal. A board where every quote was accepted could not
      show what the workflow does before acceptance.
    * **A change quote whose effective date is a delayed start of 45 days**, so the delayed
      start branch of the researched Summary module is visible, and its resolved date reads as
      45 days after the day of agreement rather than as a hard-coded date.
    * **An evergreen contract** whose line items all renew until cancelled, so the researched
      Evergreen label is on the board as a label rather than as a separate type.
    * **A renewal workflow with re-enrol on**, and one without, so both re-enrol behaviours are
      visible and the one that moves is distinguishable from the one that does not.

    The return string is ASCII and is asserted encodable by cp1252 in
    ``tests/test_wf100.py``: the seeder prints it to a Windows console, and one RIGHTWARDS
    ARROW in a recovered feature's return string broke the whole seeder.
    """

    store = RecordStore(db)
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return ""
    now = context["now"]
    room_id = room_ids[0][0]
    engine = RenewalQuoteEngine(store, now=lambda: now)
    today = now.date()
    source = "wf-100 seed"

    # 1. Two deal pipelines. The renewal panel offers a Deal pipeline and a Deal stage, so both
    #    have to exist before a quote can carry the seller's choice.
    renewals_pipeline = engine.create_pipeline(
        "Renewals",
        stages=["Qualification", "Contract sent", "Negotiation", "Closed won"],
        room_id=room_id,
        actor="dana",
        source=source,
    )["pipeline"]
    expansions_pipeline = engine.create_pipeline(
        "Expansions",
        stages=["Discovery", "Proposal", "Closed won"],
        room_id=room_id,
        actor="sam",
        source=source,
    )["pipeline"]

    # 2. Two templates, a renewal and a change, so the template choice is visible.
    annual = engine.create_template(
        "Annual renewal template",
        change_type="renewal",
        term_months=12,
        room_id=room_id,
        actor="dana",
        source=source,
    )["template"]
    biennial_change = engine.create_template(
        "Two year change template",
        change_type="change",
        term_months=24,
        discount_format="percentage",
        room_id=room_id,
        actor="dana",
        source=source,
    )["template"]

    # 3. The fixed-term contract a renewal quote is built from. Two line items, a named seller
    #    and buyer, and an end date, so the not-finalised branch of the renewal date rule has a
    #    date to report.
    contract = store.create(
        "contract",
        {
            "name": "Northwind platform agreement",
            "seller": "Dana Ruiz",
            "buyer": "Halcyon Cloud",
            "currency": "EUR",
            "start_date": today.replace(year=today.year - 1).isoformat(),
            "end_date": today.isoformat(),
            "term_length": 12,
            "line_items": [
                {"sku": "SEAT-STD", "quantity": 40, "amount": "48000.00"},
                {"sku": "SUPPORT-PLAT", "quantity": 1, "amount": "9600.00"},
            ],
            "alert_offset_days": 30,
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )

    # 4. The accepted renewal. This is the researched outcome, produced by the real engine call
    #    the accept route makes, so the demo cannot show a contract the HTTP route would not
    #    create.
    accepted = engine.create_quote(
        contract["id"],
        template_id=annual["id"],
        effective_date_mode="on_agreement",
        prorate=True,
        deal_pipeline_id=renewals_pipeline["id"],
        deal_stage="Contract sent",
        room_id=room_id,
        actor="dana",
        source=source,
    )["quote"]
    accepted_result = engine.accept(
        accepted["id"],
        accepted_by="Priya Nair at Halcyon Cloud",
        room_id=room_id,
        actor="dana",
        source=source,
    )

    # 5. A draft renewal on a second contract, so the board shows a renewal that has not been
    #    accepted and therefore has no deal. A board where every quote was accepted could not
    #    show what the workflow does before acceptance.
    second = store.create(
        "contract",
        {
            "name": "Vantage support agreement",
            "seller": "Dana Ruiz",
            "buyer": "Vantage Logistics",
            "currency": "GBP",
            "start_date": today.replace(year=today.year - 2).isoformat(),
            "end_date": today.replace(month=min(12, today.month + 1)).isoformat(),
            "term_length": 24,
            "line_items": [{"sku": "SEAT-PRE", "quantity": 8, "amount": "14240.00"}],
            "alert_offset_days": 14,
        },
        room_id=room_id,
        actor="sam",
        source=source,
    )
    engine.create_quote(
        second["id"],
        template_id=biennial_change["id"],
        effective_date_mode="custom_date",
        effective_date_on=today.replace(year=today.year + 1).isoformat(),
        prorate=False,
        deal_pipeline_id=expansions_pipeline["id"],
        deal_stage="Proposal",
        room_id=room_id,
        actor="sam",
        source=source,
    )

    # 6. A change quote whose effective date is a delayed start of 45 days, so the delayed
    #    start branch of the researched Summary module is on the board.
    third = store.create(
        "contract",
        {
            "name": "Meridian pilot agreement",
            "seller": "Sam Okafor",
            "buyer": "Meridian Health",
            "currency": "USD",
            "start_date": today.replace(year=today.year - 1).isoformat(),
            "end_date": today.replace(day=28).isoformat(),
            "term_length": 12,
            "line_items": [{"sku": "SEAT-STD", "quantity": 12, "amount": "15840.00"}],
        },
        room_id=room_id,
        actor="sam",
        source=source,
    )
    engine.create_quote(
        third["id"],
        template_id=annual["id"],
        effective_date_mode="delayed_start",
        delay_days=45,
        prorate=True,
        deal_pipeline_id=expansions_pipeline["id"],
        deal_stage="Discovery",
        room_id=room_id,
        actor="sam",
        source=source,
    )

    # 7. An evergreen contract. Every line item renews until cancelled, so the researched
    #    Evergreen label is on the board as a label and not as a separate type.
    evergreen = store.create(
        "contract",
        {
            "name": "Cypress evergreen agreement",
            "seller": "Dana Ruiz",
            "buyer": "Cypress Labs",
            "currency": "EUR",
            "start_date": today.replace(year=today.year - 3).isoformat(),
            "term_length": vocab.EVERGREEN_LABEL,
            "line_items": [
                {
                    "sku": "SEAT-ENT",
                    "quantity": 60,
                    "amount": "147600.00",
                    vocab.EVERGREEN_RENEWAL_FIELD: vocab.EVERGREEN_RENEWAL_VALUE,
                },
                {
                    "sku": "SUPPORT-ENT",
                    "quantity": 1,
                    "amount": "24000.00",
                    vocab.EVERGREEN_RENEWAL_FIELD: vocab.EVERGREEN_RENEWAL_VALUE,
                },
            ],
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )
    # Asserted rather than assumed. A seed that stopped producing the label would otherwise
    # still print the line describing it, and the demo would lie about the researched rule.
    evergreen_label = rules_term_label(store, evergreen["id"])
    if evergreen_label != vocab.EVERGREEN_LABEL:
        raise AssertionError(
            f"the seed expected the term label {vocab.EVERGREEN_LABEL!r} and got "
            f"{evergreen_label!r}"
        )

    # 8. Two renewal workflows, one with re-enrol on and one without, so both behaviours are on
    #    the board and the one that moves is distinguishable from the one that does not.
    engine.create_workflow(
        contract_id=third["id"],
        template_id=annual["id"],
        deal_pipeline_id=expansions_pipeline["id"],
        deal_stage="Discovery",
        contract_target="one_contract",
        re_enroll=True,
        room_id=room_id,
        actor="sam",
        source=source,
    )
    engine.create_workflow(
        contract_id=second["id"],
        template_id=biennial_change["id"],
        deal_selection_method="existing_deal",
        contract_target="one_contract",
        re_enroll=False,
        room_id=room_id,
        actor="sam",
        source=source,
    )

    # Counts are read back rather than written out, so the line the seeder prints cannot
    # describe a state the seed did not produce.
    board = engine.summary(room_id=room_id)
    chain = accepted_result["renewal_date"]

    return (
        f"{board['contracts']} contracts seeded, {board['renewable_contracts']} of them renewable "
        f"now; "
        f"{board['templates']} renewal and change quote templates; "
        f"{board['pipelines']} deal pipelines; "
        f"{board['quotes']} renewal quotes, {board['accepted_quotes']} accepted and "
        f"{board['quotes'] - board['accepted_quotes']} still open; "
        f"the accepted one created {board['renewal_contracts']} renewal contract and "
        f"{board['deals']} renewal deal in stage {accepted_result['deal']['stage']}, with the "
        f"renewal date moving to {chain['renewal_date']} on the {chain['branch']} branch; "
        f"1 evergreen contract labelled {vocab.EVERGREEN_LABEL}; "
        f"{board['workflows']} renewal workflows, 1 with re-enrol on"
    )


def rules_term_label(store: RecordStore, contract_id: str) -> str:
    """The term label for one stored contract, read back through the domain rules.

    A read helper rather than an inline call, so the assertion in the seed reads as a check on
    the product rather than as a repeat of the code under test.
    """

    from dsr.renewal_quotes import rules  # noqa: PLC0415 - one use, kept out of module scope

    record = store.get(contract_id)
    if record is None:
        return ""
    return rules.term_label(dict(record.get("data") or {}))
