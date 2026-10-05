"""WF-088: auto-assign the correct price book or price list to a deal by rule.

The researched workflow, in full. An admin opens **CRM > Products** ->
**Manage price books**, opens a price book, hovers **Assignment rules** and clicks the
**edit icon**. In **Filters** they click **+ Add filter** and configure deal-property
filters in ``and`` / ``or`` groups, review the matching deals in the right panel,
toggle **Auto-assigned** on, **Save**, and toggle the price book's **Inactive** switch
off to activate. From then on a deal created against those properties is written the
price book, and the product lookup and the line-item prices are scoped to it.

The domain is in :mod:`dsr.quoting_proposals.price_book_engine` and its rules in
:mod:`dsr.quoting_proposals.price_book_rules`. What lives in this module is the three
things a workflow has to take out of shared files: the route table, the mapping from
domain errors to responses, and the demo data.

Hard rule 4: every write below is handed ``f"{router.prefix}..."``, built from
``router.prefix`` so the audit row and the route table cannot drift. A hardcoded URL
inside a domain function is a defect, and the same class of bug has shipped in this
codebase before: a feature's audit log kept naming a path the app had stopped serving.
``source`` is a *required* keyword on every writing function here, so omitting it is a
``TypeError`` at the call site rather than an untraceable row.

One handler for the whole error hierarchy.
:class:`~dsr.quoting_proposals.price_book_rules.PriceBookRefusal` is this workflow's
own type and the base of every refusal in it, and each refusal carries its own code and
status, so one handler answers 422 for a rule with no price book and 409 for an
override to the book the deal already has without a table saying which is which.
:class:`~dsr.quoting_proposals.price_book_rules.PriceBookNotFound` is a second type
rather than a subclass, because it is a 404 and it is the base of nothing else.
``RecordNotFound`` is deliberately not claimed: the core app already maps it to 404,
and two handlers for one type is a collision the host refuses.

A word on what the message is
-----------------------------

``POST /deals/{deal_id}/assign`` is the researched moment: a deal row is created and
the rules are evaluated. The same message fires on create/update of an opportunity,
quote, order or invoice row in Dynamics, and this product does not implement the
other three because the workflows that own those rows are not this one. It refuses an
unknown trigger rather than guessing which one a caller meant.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.quoting_proposals import (
    price_book_inferences as inferences,
    price_book_vocabulary as vocab,
)
from dsr.quoting_proposals.price_book_engine import PriceBookEngine
from dsr.quoting_proposals.price_book_rules import PriceBookNotFound, PriceBookRefusal
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-088-auto-assign-the-correct-price-book-or-price",
    "ticket": "WF-088",
    "name": "Auto-assign the correct price book or price list to a deal by rule",
    "description": (
        "Evaluate a deal's and its company's properties against configured filters "
        "when the deal row is created, and write the one matching price book onto the "
        "deal so the product lookup and the line-item prices are scoped to it. A deal "
        "that matches more than one rule waits for its owner to choose, and a quote "
        "inherits the book from its deal."
    ),
    "nav": [{"id": "price-book-rules", "label": "Price book rules"}],
}

router = APIRouter(prefix="/api/WF-088", tags=["WF-088"])


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _refusal(request: Request, exc: PriceBookRefusal) -> JSONResponse:
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


def _not_found(request: Request, exc: PriceBookNotFound) -> JSONResponse:
    """No such rule, no such assignment, no such deal."""
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status, "id": exc.record_id},
    )


EXCEPTION_HANDLERS = {PriceBookRefusal: _refusal, PriceBookNotFound: _not_found}


# --------------------------------------------------------------------------- #
# The service over the store
# --------------------------------------------------------------------------- #
#
# Built per request from ``StoreDep`` rather than held on ``app.state``, because an
# ``app.state`` entry is exactly the edit to the shared ``dsr/api.py`` that the feature
# host exists to make unnecessary. It holds nothing but the store handle and a clock,
# both constructor arguments, so a test constructs one over rows of its own with a
# clock it controls.


def get_service(store: RecordStore = StoreDep) -> PriceBookEngine:
    return PriceBookEngine(store)


ServiceDep = Depends(get_service)


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="Every published vocabulary")
def vocabulary() -> dict[str, Any]:
    """Filters, operators, switches, modes, reasons and states.

    Served as data so a client renders its pickers from the server's vocabulary
    rather than from a list compiled into a page, and a value added here reaches every
    client at once.
    """
    return vocab.catalogue()


@router.get("/inferences", summary="Every judgement call this workflow rests on")
def inference_report() -> dict[str, Any]:
    """What the research fixes, what it leaves open, and which reading was taken.

    The two readings a seller meets on their first deal are served on their own: the
    multiple-match case, because its answer is "nothing was written", and the
    destructive override, because a page has to warn before it offers it.
    """
    return {
        "count": inferences.count(),
        "decisions": inferences.describe(),
        "multiple_matches": inferences.multiple_matches(),
        "override_removes_lines": inferences.override_removes_lines(),
    }


# --------------------------------------------------------------------------- #
# Assignment rules
# --------------------------------------------------------------------------- #


@router.get("/rules", summary="The configured assignment rules")
def list_rules(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    service: PriceBookEngine = ServiceDep,
) -> dict[str, Any]:
    """Every rule, oldest first, so the page shows them in the order they were added."""
    listed = service.list_rules(room_id=room_id, limit=limit)
    return {"room_id": room_id, "count": len(listed), "rules": listed}


@router.post("/rules", status_code=201, summary="Configure an assignment rule")
def create_rule(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    service: PriceBookEngine = ServiceDep,
) -> dict[str, Any]:
    """Steps one and two of the researched flow, in one call.

    The price book, the filters and the two switches are saved together because a rule
    that names no price book has nothing to assign, and the research's own UI refuses
    to save one that is missing any of its parts, so a half-configured rule is a state
    this product does not create either.
    """
    return service.create_rule(
        payload, actor=actor, source=f"POST {router.prefix}/rules", room_id=room_id
    )


@router.get("/rules/{rule_id}", summary="One assignment rule")
def read_rule(rule_id: str, service: PriceBookEngine = ServiceDep) -> dict[str, Any]:
    return {"rule": service.rule(rule_id)}


@router.patch("/rules/{rule_id}", summary="Turn a rule on or off, or switch auto-assignment")
def patch_rule(
    rule_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    service: PriceBookEngine = ServiceDep,
) -> dict[str, Any]:
    """The **Inactive** switch and the **Auto-assigned** toggle, as a merge patch.

    The researched flow ends with "toggle **Auto-assigned** on -> **Save** -> toggle
    the price book's **Inactive** switch off to activate", and those are two different
    fields on one rule. This is both of them, revalidated on save.

    Turning ``enabled`` off writes nothing on a later assignment and says why.
    Turning ``auto_assign`` off is the researched test-first mode: the rule still
    matches and is still reported, and nothing is written.
    """
    return service.patch_rule(
        rule_id, payload, actor=actor, source=f"PATCH {router.prefix}/rules/{rule_id}"
    )


@router.delete("/rules/{rule_id}", summary="Remove an assignment rule")
def delete_rule(
    rule_id: str,
    actor: str | None = Query(default=None),
    service: PriceBookEngine = ServiceDep,
) -> dict[str, Any]:
    """Remove a rule. The assignments it created are left alone and still explain themselves."""
    return service.delete_rule(
        rule_id, actor=actor, source=f"DELETE {router.prefix}/rules/{rule_id}"
    )


# --------------------------------------------------------------------------- #
# The rule, on a deal
# --------------------------------------------------------------------------- #


@router.get("/deals/{deal_id}/conditions", summary="Which price books a deal matches")
def deal_conditions(deal_id: str, service: PriceBookEngine = ServiceDep) -> dict[str, Any]:
    """The researched right panel: "review the matching deals in the right panel".

    A GET because it checks and writes nothing. It runs the same evaluation the
    assignment runs, so what an admin reviews while building a rule is what will be
    enforced when a deal arrives. The per-filter report is carried, because a panel
    that says "two rules matched" with no filter report is nothing to review.
    """
    return service.conditions(deal_id)


@router.post("/deals/{deal_id}/assign", status_code=201, summary="Run the rule on a deal")
def assign(
    deal_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    trigger: str | None = Query(default=None, description="create or update"),
    service: PriceBookEngine = ServiceDep,
) -> dict[str, Any]:
    """The researched moment: a deal row is created and the rules are evaluated.

    Nine answers and all nine are 201 with a body, because "nothing was assigned" and
    "a price book was assigned" are both real results and a status code cannot say
    which happened:

    * ``assigned``, because "When a deal matches exactly one assignment rule, that
      price book is automatically assigned";
    * ``needs_choice``, with every matching book as a candidate, because both sourced
      sentences about more than one match say a person chooses;
    * ``no_rule_matched``, which is the researched manual-only mode;
    * ``matched_rule_is_inactive``, because the price book's **Inactive** switch is on;
    * ``matched_rule_without_auto_assign``, the researched test-first mode;
    * ``already_assigned``, because auto-assignment does not run again;
    * ``auto_assign_runs_on_create_only``, because the trigger was not create.

    Only the first writes the deal. Every one of them is recorded as an assignment, so
    "why does this deal have no price book" has an answer a reviewer can find.

    A ``trigger`` of ``update`` is answered rather than refused, and assigns nothing.
    The sourced sentence is unambiguous that auto-assignment does not re-run, and the
    same call is what an integration sends on every property change, so refusing it
    would be refusing the integration rather than the rule.
    """
    body = dict(payload or {})
    return service.assign(
        deal_id,
        actor=actor,
        source=f"POST {router.prefix}/deals/{deal_id}/assign",
        trigger=trigger or body.get("trigger") or vocab.TRIGGER_CREATE,
        room_id=room_id,
    )


@router.get("/deals/{deal_id}/price-book", summary="The price book on a deal")
def deal_price_book(deal_id: str, service: PriceBookEngine = ServiceDep) -> dict[str, Any]:
    """The researched *Line items* card: **Price book: None**, or the book's name.

    ``authority`` says where the value came from, which is the difference between "this
    workflow assigned one" and "a CRM mirror already priced this deal". The candidate
    list is included so the **Price book** dropdown offers exactly the books the rules
    matched rather than every book in the workspace.
    """
    return service.deal_price_book(deal_id)


@router.post("/deals/{deal_id}/price-book", summary="Change the price book on a deal")
def override_price_book(
    deal_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    service: PriceBookEngine = ServiceDep,
) -> dict[str, Any]:
    """The researched override: "click **Price book: None** -> **Change price book**".

    Two things are honest about it in the answer rather than in a footnote:

    * it is how a deal owner resolves a deal that matched more than one rule, which is
      the sourced reading of that case;
    * it is destructive. "If the price book is changed, any line items associated with
      the previous price book will be removed", so the lines of the previous book are
      removed and ``line_items_removed`` names every id that went.

    Setting the book the deal already has is refused with its own code rather than
    treated as a no-op, because a no-op that removes nothing still reads as a change
    of book on the audit trail.
    """
    body = dict(payload or {})
    return service.override(
        deal_id,
        body.get(vocab.PRICE_BOOK) or body.get("price_book") or body.get("price_book_id"),
        actor=actor,
        source=f"POST {router.prefix}/deals/{deal_id}/price-book",
        room_id=room_id,
    )


# --------------------------------------------------------------------------- #
# The quote
# --------------------------------------------------------------------------- #


@router.get("/quotes/{quote_id}/price-book", summary="The price book a quote inherits")
def quote_price_book(quote_id: str, service: PriceBookEngine = ServiceDep) -> dict[str, Any]:
    """ "Quotes inherit the price book from the associated deal."

    A read and not a write, deliberately: "Users can't select a price book when
    creating a quote; they must select it on the deal", so there is no route here that
    sets one. A quote whose deal has no book reports ``authority: none`` and the
    sentence, rather than inventing a default book nobody chose.
    """
    return service.quote_price_book(quote_id)


# --------------------------------------------------------------------------- #
# The log and the board
# --------------------------------------------------------------------------- #


@router.get("/assignments", summary="The recorded assignments, filterable")
def list_assignments(
    deal_id: str | None = Query(default=None),
    outcome: str | None = Query(default=None, description="A published reason code"),
    room_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    service: PriceBookEngine = ServiceDep,
) -> dict[str, Any]:
    """Every decision, including the ones that assigned nothing.

    Each filter is a JSON path in the record's own payload, resolved through the
    dynamic index, so a team that adds its own field can filter on it here too without
    a migration. ``outcome`` takes one of the published reason codes, so the log can
    be read as "every deal still waiting for a choice".
    """
    listed = service.list_assignments(
        deal_id=deal_id, outcome=outcome, room_id=room_id, limit=limit
    )
    return {
        "room_id": room_id,
        "count": len(listed),
        "assignments": listed,
        "reasons": list(vocab.ASSIGNMENT_REASONS),
    }


@router.get("/assignments/{assignment_id}", summary="One recorded assignment")
def read_assignment(assignment_id: str, service: PriceBookEngine = ServiceDep) -> dict[str, Any]:
    """One assignment, with the filter report and the candidate list as they were."""
    return {"assignment": service._assignment_view(service.assignment(assignment_id))}


@router.get("/summary", summary="What is priced and what is waiting on a person")
def room_summary(
    room_id: str | None = Query(default=None),
    service: PriceBookEngine = ServiceDep,
) -> dict[str, Any]:
    """Counts by reason, the mode the rules put the workspace in, and the two readings.

    ``awaiting_a_choice`` is the number a reviewer acts on, which is why it is named
    separately from the total: the two are equal today, and a client that reads the
    second keeps working if that ever stops being true.
    """
    return service.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #
#
# The branch that added this could have edited ``backend/seed.py``, which is a shared
# file that ten of the first twelve workflows rewrote purely to add their own rows.
# The seeder calls the hook below instead, so the demo travels with the feature that
# needs it.


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
    :class:`~dsr.quoting_proposals.price_book_engine.PriceBookEngine` over the demo
    rooms, so the demo's rules, assignments and removals are what this workflow
    actually writes rather than rows composed by hand. A seeded assignment and an API
    assignment cannot disagree.

    What is here, and why each is here:

    * a rule that assigns on a deal's own ``segment``, which is the filter the flow's
      "**+ Add filter**" most obviously builds;
    * a rule that assigns on a **company** property, because "deal-property filters
      over HubSpot deal properties, company properties" names two objects and a demo
      with only one of them hides the other;
    * a **test-first** rule whose **Auto-assigned** switch is off, so the researched
      middle mode is a row rather than a paragraph in a docstring;
    * an **inactive** rule, so "toggle the price book's **Inactive** switch off to
      activate" is visible as a switch that changes the answer;
    * a deal that matches **two** rules, so the multiple-match reading and the choice
      the owner then makes are on screen;
    * a deal whose price book was **changed by hand**, with the sourced line-item
      removal recorded against it, so the destructive override is visible as a success
      with a count rather than as a warning nobody can test;
    * a **quote** that inherits its deal's price book, so the sourced inheritance is a
      read rather than an assertion.

    Every character of the returned string is encodable by cp1252. A single
    RIGHTWARDS ARROW in one recovered feature's return string broke the entire seeder
    on a Windows console.
    """
    rooms = _demo_rooms(context.get("room_ids") or [])
    if not rooms:
        return "0 rules, 0 deals (no demo rooms to scope an assignment rule to)"

    from dsr.store import RecordStore

    base: Any = context.get("now")
    service = PriceBookEngine(RecordStore(db), now=(lambda: base) if base else None)
    source = "seed"
    actor = "dana"
    outcomes: list[str] = []

    # One plan per state this workflow distinguishes, spread over the demo rooms so
    # every room a plan touches has its own rule in it. Every plan filters on a
    # `segment` only it uses, so two rules in one room cannot price each other's deals.
    plans: tuple[dict[str, Any], ...] = (
        {
            "state": "assigned",
            "segment": "enterprise-inbound",
            "summary": "1 deal priced by exactly one matching rule",
            "rule": {
                "key": "enterprise-inbound",
                "label": "Enterprise inbound deals are priced from the enterprise book",
                "filters": [
                    {"object": "deal", "property": "segment", "operator": "is", "value": ""}
                ],
                "auto_assign": True,
                "enabled": True,
            },
            "book": {"id": "pb-enterprise", "name": "Enterprise list 2026"},
        },
        {
            "state": "company_matched",
            "segment": "partner-led",
            "summary": "1 deal priced by a rule that filters on the company's industry",
            "rule": {
                "key": "software-industry",
                "label": "Software companies are priced from the software book",
                "filters": [
                    {"object": "company", "property": "industry", "operator": "is", "value": ""}
                ],
                "auto_assign": True,
                "enabled": True,
            },
            "book": {"id": "pb-software", "name": "Software partner list"},
            # The company this plan's rule reads, and the value its filter compares
            # against. Filled into the filter's first slot above.
            "scope_value": "software",
            "company": {"id": "co_northwind", "name": "Northwind", "industry": "software"},
        },
        {
            "state": "test_first",
            "segment": "pilot",
            "summary": "1 rule in test first mode, matching but assigning nothing",
            "rule": {
                "key": "pilot-under-review",
                "label": "Pilot deals, while the book is being reviewed",
                "filters": [
                    {"object": "deal", "property": "segment", "operator": "is", "value": ""}
                ],
                "auto_assign": False,
                "enabled": True,
            },
            "book": {"id": "pb-pilot", "name": "Pilot list"},
        },
        {
            "state": "inactive",
            "segment": "renewal",
            "summary": "1 inactive rule, matching but assigning nothing",
            "rule": {
                "key": "renewal-inactive",
                "label": "Renewal book, not activated yet",
                "filters": [
                    {"object": "deal", "property": "segment", "operator": "is", "value": ""}
                ],
                "auto_assign": True,
                "enabled": False,
            },
            "book": {"id": "pb-renewal", "name": "Renewal list"},
        },
        {
            "state": "needs_choice",
            "segment": "mid-market",
            "summary": "1 deal matching 2 rules, so a person chooses",
            "rule": {
                "key": "mid-market-standard",
                "label": "Mid market, standard terms",
                "filters": [
                    {"object": "deal", "property": "segment", "operator": "is", "value": ""}
                ],
                "auto_assign": True,
                "enabled": True,
            },
            "book": {"id": "pb-standard", "name": "Standard list"},
            "second_rule": {
                "key": "mid-market-preferred",
                "label": "Mid market, preferred partner terms",
                "filters": [
                    {"object": "deal", "property": "amount", "operator": "gte", "value": 20000}
                ],
                "auto_assign": True,
                "enabled": True,
            },
            "second_book": {"id": "pb-partner", "name": "Preferred partner list"},
        },
        {
            "state": "changed_by_hand",
            "segment": "expansion",
            "summary": "1 deal whose price book was changed by hand, with the sourced removal",
            "rule": {
                "key": "expansion-standard",
                "label": "Expansion deals start from the standard list",
                "filters": [
                    {"object": "deal", "property": "segment", "operator": "is", "value": ""}
                ],
                "auto_assign": True,
                "enabled": True,
            },
            "book": {"id": "pb-standard", "name": "Standard list"},
        },
    )

    rules_made = 0
    deals_made = 0
    quotes_made = 0
    for index, plan in enumerate(plans):
        room_id = str(rooms[index % len(rooms)][0])
        # The filter that carries the plan's own segment has its value filled in here
        # rather than written six times, so the plan's segment and its rule's scope
        # cannot drift apart. The filter list is copied first, so the plan tuple stays
        # immutable across its one iteration.
        # A rule's first filter is left with an empty value in the plan, and the value
        # that scopes it to this plan's own deal is filled in here. Done by position
        # rather than by property name, because one plan filters on a company property
        # and the rest on a deal property, and a name-based rule would leave that one
        # unscoped -- which is how a demo ends up with every rule pricing every deal.
        filters = [{**one} for one in plan["rule"]["filters"]]
        if filters:
            filters[0]["value"] = plan.get("scope_value", plan["segment"])
        rule_payload = {**plan["rule"], "price_book": plan["book"], "filters": filters}
        service.create_rule(rule_payload, actor=actor, source=source, room_id=room_id)
        rules_made += 1

        if plan.get("second_rule"):
            # A second rule in the same room, scoped to the amount the plan's deal
            # carries, so the deal matches both and neither is a coincidence.
            service.create_rule(
                {**plan["second_rule"], "price_book": plan["second_book"]},
                actor=actor,
                source=source,
                room_id=room_id,
            )
            rules_made += 1

        if plan.get("company"):
            service.store.create(
                vocab.SOURCE_COMPANIES,
                plan["company"],
                record_id=plan["company"]["id"],
                room_id=room_id,
                actor=actor,
                source=source,
            )

        deal_payload: dict[str, Any] = {
            "name": f"{plan['segment'].replace('-', ' ').title()} deal",
            "segment": plan["segment"],
            "amount": 45000 if plan["state"] == "needs_choice" else 12000,
            "stage": "decision_maker_engaged",
        }
        if plan.get("company"):
            deal_payload["company"] = plan["company"]["id"]
        deal = service.store.create(
            vocab.SOURCE_DEALS,
            deal_payload,
            room_id=room_id,
            actor=actor,
            source=source,
        )
        deals_made += 1

        # The answer is deliberately not captured. The demo's states are asserted in the
        # seed test, against the rows the seed really produced, rather than against a local
        # variable here that could agree with nothing.
        service.assign(deal["id"], actor=actor, source=source, room_id=room_id)

        if plan["state"] == "changed_by_hand":
            # A line item under the assigned book, then a hand change. The sourced
            # sentence says the previous book's lines are removed, so the demo shows
            # the removal rather than describing it.
            service.store.create(
                vocab.LINE_ITEMS,
                {"deal_id": deal["id"], "sku": "SEAT-STD", "quantity": 20, "unit_price": 900},
                room_id=room_id,
                actor=actor,
                source=source,
            )
            service.override(
                deal["id"],
                {"id": "pb-partner", "name": "Preferred partner list"},
                actor="sam",
                source=source,
                room_id=room_id,
            )
        elif plan["state"] == "needs_choice":
            # The sourced resolution of more than one match: the deal owner chooses.
            service.override(
                deal["id"],
                plan["second_book"],
                actor="sam",
                source=source,
                room_id=room_id,
            )

        if plan["state"] == "assigned":
            quote = service.store.create(
                vocab.SOURCE_QUOTES,
                {
                    "name": f"{plan['segment'].title()} quote",
                    # The quote's own state, in the vendor's spelling. It belongs to
                    # WF-086 and this workflow never writes it; it is here so the
                    # inherited price book is read off a realistic quote.
                    "hs_status": "DRAFT",
                    "deal": deal["id"],
                    "creator": actor,
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
            quotes_made += 1
            service.quote_price_book(quote["id"])

        outcomes.append(str(plan["summary"]))

    return (
        f"{rules_made} assignment rules across {len(rooms)} room(s), {deals_made} deals, "
        f"{quotes_made} quotes inheriting a price book ({'; '.join(outcomes)})"
    )
