"""WF-089: quote in a transaction currency with FX conversion, and the refusals.

The rules live in :mod:`dsr.quote_currency` and are not restated here. This module is the
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

The status codes here are the product's own
-------------------------------------------

The specification documents no status codes for this workflow, so every one follows the
conventions the rest of this product uses and the distinction is recorded rather than
invented:

``200``
    Everything, including a pricing run that refused. The refusal is a recorded outcome and
    ``outcome: refused`` with the named code is the answer, because the run happened and
    produced a definite result. A caller can tell a misconfigured quote from a broken
    endpoint by reading the body rather than by guessing at a status code.
``201``
    A resource was created: a currency record, a price list, a price row, a quote, a line.
``400``
    A value this workflow will not accept: an unknown ISO code, a precision outside the
    bound, a non-positive rate, a unit price on a line item, an unknown recalculation trigger.
``404``
    A quote, price list or currency record that does not exist. Never a 500.
``409``
    A well-formed request that conflicts with the state of the record: changing a quote's
    currency while it holds line items, or pricing a quote whose rate cannot be resolved.

Two of the four error types carry a body a caller can act on rather than merely read: the
currency-change refusal names the line count, the sourced sentence and the remedy, and the
missing-rate refusal names the currency and what to do about it.

What this workflow reads and does not own
-----------------------------------------

**Quotes and line items.** WF-086 provisions them. This workflow reads those two collections
as data, and exposes routes that create them so it is demonstrable and testable before WF-086
lands. When WF-086 lands it writes into the same two collections and this workflow prices what
it finds. It does not import WF-086's module, because a feature must not import a feature.

**Price lists and price rows.** WF-087 provisions them, and the same arrangement applies.

**The two product models.** The research quotes HubSpot and Dynamics and they disagree. This
build implements the Dynamics model, and :data:`~dsr.quote_currency.vocabulary.PRODUCT_MODEL`
records why: code 34 is only expressible when a price list's currency can differ from the
header's. The full argument, with the alternatives, is at ``GET /decisions``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.quote_currency import QuoteCurrencyEngine, inferences, vocabulary as vocab
from dsr.quote_currency.errors import (
    CurrencyChangeRefused,
    CurrencyRefusal,
    QuoteNotFound,
    RateUnavailable,
)
from dsr.store import RecordStore

#: The price list field, under the researched spelling and the plain one.
#:
#: The research spells the header field ``pricelevelid`` because that is the Dataverse column
#: name, and a caller reading the vendor documentation will send that. A caller who has only
#: read this workflow's own prose will send ``price_list_id``. Both are accepted, and the
#: researched spelling wins when both are present, because it is the one a vendor-shaped
#: payload carries.
PRICE_LIST_KEYS = (vocab.PRICE_LIST_FIELD, "price_list_id")


def _price_list_id_of(body: dict[str, Any]) -> Any:
    """The price list a request names, under either spelling."""

    for key in PRICE_LIST_KEYS:
        if body.get(key) not in (None, ""):
            return body[key]
    return None


FEATURE = {
    "id": "wf-089-quote-in-a-transaction-currency-with-fx-conversion",
    "ticket": "WF-089",
    "name": "Quote in a transaction currency with FX conversion",
    "description": (
        "Price a quote in a transaction currency, derive every total into the organisation's "
        "base currency from the rate stamped on the quote, and refuse to price a wrong-currency "
        "combination with the researched PricingErrorCode rather than returning a partial total."
    ),
    "nav": [{"id": "wf-089-quote-currency", "label": "Quote currency"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several workflows
#: already share.
router = APIRouter(prefix="/api/wf-089", tags=["WF-089"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is a domain
    function hardcoding a URL string, which leaves the audit log naming a route the app
    stopped serving. ``tests/test_wf089_http.py`` asserts every source this router can record
    matches a concrete ``(method, path)`` the host mounted.
    """

    return f"{method} {router.prefix}{path}"


def get_engine(store: RecordStore = StoreDep) -> QuoteCurrencyEngine:
    """A :class:`QuoteCurrencyEngine` over the process-wide audited store.

    Per request, for the same reason the rest of this product builds its engine per request:
    the engine holds nothing beyond the store and a clock, so building it here leaves every
    seam overridable in a test instead of hanging a long-lived object off ``app.state``, which
    is a shared file this feature may not edit.
    """

    return QuoteCurrencyEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# All four types are declared in dsr.quote_currency.errors and raised by nothing else in the
# product. That is what makes it safe to map them here: the host refuses a second feature
# registering a handler for the same type, and a handler for ValueError would intercept that
# exception across the whole product.


def _currency_refused(request: Request, exc: CurrencyRefusal) -> JSONResponse:
    """400 with a field-keyed map, so each message lands next to its input."""

    return JSONResponse(status_code=400, content=exc.to_dict())


def _not_found(request: Request, exc: QuoteNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content=exc.to_dict())


def _currency_change_refused(request: Request, exc: CurrencyChangeRefused) -> JSONResponse:
    """409 naming the line count, the sourced sentence, and the way out.

    A 409 with no remediation is a support ticket, so all three are in the body.
    """

    return JSONResponse(status_code=409, content=exc.to_dict())


def _rate_unavailable(request: Request, exc: RateUnavailable) -> JSONResponse:
    return JSONResponse(status_code=409, content=exc.to_dict())


EXCEPTION_HANDLERS = {
    CurrencyRefusal: _currency_refused,
    QuoteNotFound: _not_found,
    CurrencyChangeRefused: _currency_change_refused,
    RateUnavailable: _rate_unavailable,
}


# --------------------------------------------------------------------------- #
# The board and the research
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(
    room_id: str | None = Query(None), engine: QuoteCurrencyEngine = EngineDep
) -> dict[str, Any]:
    """The headline numbers, read back from the store. Reads only.

    ``refused_quotes`` sits beside ``priced_quotes`` rather than inside a total, because a
    refusal is the state the specification asks a reviewer to be able to see and a count that
    added them together would hide which is which.
    """

    return engine.summary(room_id=room_id)


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every researched term this workflow validates against.

    Served rather than duplicated in the frontend so the page cannot drift from the rules that
    compute it: the two money field lists, the two refusal codes, the six recalculation
    triggers and the rate policy all come from the same tables the engine reads.
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
# Currencies
# --------------------------------------------------------------------------- #


@router.get("/currencies")
def list_currencies(
    room_id: str | None = Query(None), engine: QuoteCurrencyEngine = EngineDep
) -> dict[str, Any]:
    """Every transaction currency record, with the rate and where the rate came from.

    A read, so it records no audit row. Resolving where a rate is held changes nothing.
    """

    rows = engine.currencies(room_id=room_id)
    base = engine.base_currency(room_id=room_id)
    return {
        "count": len(rows),
        "currencies": rows,
        "base_currency": (base or {}).get("data", {}).get("iso_code"),
        "currency_types": list(vocab.CURRENCY_TYPES),
        "rate_policy": vocab.RATE_POLICY,
        "rate_sources": list(vocab.RATE_SOURCES),
        "rate_event": vocab.RATE_EVENT,
        "rate_event_note": vocab.RATE_EVENT_NOTE,
    }


@router.post("/currencies", status_code=201)
def register_currency(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: QuoteCurrencyEngine = EngineDep,
) -> dict[str, Any]:
    """Register one transaction currency record.

    A rate may only be supplied for a ``Custom`` record. That is the reconciliation of the
    research's two statements about ``exchangerate``: read-only on a quote, writable on a
    currency record that exists to carry a custom rate. A ``Standard`` record with a supplied
    rate is refused rather than quietly accepted, because accepting it would leave two sources
    for one figure with no record of which won.
    """

    body = dict(payload or {})
    return engine.register_currency(
        body.get("iso_code"),
        exchange_rate=body.get("exchange_rate"),
        currency_symbol=body.get("currency_symbol"),
        currency_precision=body.get("currency_precision"),
        currency_type=body.get("currency_type"),
        is_base_currency=bool(body.get(vocab.BASE_CURRENCY_FIELD)),
        name=body.get("name"),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/currencies"),
    )


@router.post("/currencies/{currency_id}/rate")
def stamp_rate(
    currency_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: QuoteCurrencyEngine = EngineDep,
) -> dict[str, Any]:
    """Stamp a custom rate on a ``Custom`` currency record.

    The only write path to ``exchangerate`` in this workflow, and it exists because the
    research says a deployment may do it: "``exchangerate`` is writable so a custom rate can be
    stamped".

    Existing quotes are not re-priced, and the response says how many hold a base figure
    computed from the superseded rate. A quote is a record of what was priced; a re-stamped
    currency rate does not rewrite it. The response also lists the triggers that would apply
    the new rate, so the operator is not left guessing how to bring a quote forward.
    """

    body = dict(payload or {})
    return engine.stamp_rate(
        currency_id,
        body.get("exchange_rate"),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/currencies/{currency_id}/rate"),
    )


# --------------------------------------------------------------------------- #
# Price lists and price rows
# --------------------------------------------------------------------------- #


@router.get("/price-lists")
def list_price_lists(
    room_id: str | None = Query(None), engine: QuoteCurrencyEngine = EngineDep
) -> dict[str, Any]:
    """Every price list, with its currency and the products it prices.

    A price list carries one currency, which is the HubSpot sentence that survives the choice
    of the Dynamics model and the rule that makes code 34 reachable.
    """

    rows = engine.price_lists(room_id=room_id)
    return {
        "count": len(rows),
        "price_lists": rows,
        "single_currency": vocab.PRICE_LIST_SINGLE_CURRENCY,
        "single_currency_reason": vocab.PRICE_LIST_SINGLE_CURRENCY_REASON,
    }


@router.post("/price-lists", status_code=201)
def create_price_list(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: QuoteCurrencyEngine = EngineDep,
) -> dict[str, Any]:
    """Create a price list in one currency.

    The currency must already be a registered currency record: a price list in a currency the
    deployment has no record of has no precision and no rate, so it is refused here rather
    than producing a quote that cannot be priced.
    """

    body = dict(payload or {})
    return engine.create_price_list(
        body.get("name"),
        body.get("iso_code"),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/price-lists"),
    )


@router.get("/price-lists/{price_list_id}/items")
def list_price_items(
    price_list_id: str,
    room_id: str | None = Query(None),
    engine: QuoteCurrencyEngine = EngineDep,
) -> dict[str, Any]:
    """The price rows on one list: the ``pricelevelproduct`` rows a unit price resolves from."""

    engine._price_list_record(price_list_id)  # noqa: SLF001 - a missing list is a 404
    rows = engine.price_items(price_list_id, room_id=room_id)
    return {
        "price_list_id": price_list_id,
        "count": len(rows),
        "items": rows,
        "source": "unit prices resolve from these rows in the list's currency",
    }


@router.post("/price-lists/{price_list_id}/items", status_code=201)
def create_price_item(
    price_list_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: QuoteCurrencyEngine = EngineDep,
) -> dict[str, Any]:
    """Create one price row: a product priced on one price list.

    The price list fixes the currency, so the row carries no currency of its own and cannot
    disagree with its list.
    """

    body = dict(payload or {})
    return engine.create_price_item(
        price_list_id,
        body.get("product_code"),
        body.get("unit_price"),
        tier=body.get("tier"),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/price-lists/{price_list_id}/items"),
    )


# --------------------------------------------------------------------------- #
# Quotes and line items
# --------------------------------------------------------------------------- #


@router.get("/quotes")
def list_quotes(
    room_id: str | None = Query(None), engine: QuoteCurrencyEngine = EngineDep
) -> dict[str, Any]:
    """Every quote with its header currency, its price list, its rate and both sets of totals.

    A read, so it records no audit row. Looking at a quote changes nothing.
    """

    rows = engine.quotes(room_id=room_id)
    return {
        "count": len(rows),
        "quotes": rows,
        "outcomes": list(vocab.PRICING_OUTCOMES),
        "authoritative": vocab.AUTHORITATIVE,
        "authoritative_reason": vocab.AUTHORITATIVE_REASON,
        "transaction_totals": list(vocab.TRANSACTION_TOTALS),
        "base_totals": list(vocab.BASE_TOTALS),
        "currency_change_requires_no_lines": vocab.CURRENCY_CHANGE_REQUIRES_NO_LINES,
    }


@router.post("/quotes", status_code=201)
def create_quote(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: QuoteCurrencyEngine = EngineDep,
) -> dict[str, Any]:
    """Create a quote header stamped with a transaction currency.

    A price list in a different currency is allowed here, and that is deliberate: the research
    describes the platform refusing and setting ``pricingerrorcode`` when the combination is
    wrong, which is only observable if the wrong combination can exist. The refusal is at
    pricing time, as code 34.

    ``deal_iso_code`` records the HubSpot inheritance rule without implementing a deal:
    "Quotes created from deals match the associated deal's *Currency* property". It is stored
    beside the header currency and reported, so a reader can see a quote whose currency came
    from a deal rather than from an operator. It never overrides the explicit currency.
    """

    body = dict(payload or {})
    return engine.create_quote(
        body.get("name"),
        body.get("iso_code"),
        price_list_id=_price_list_id_of(body),
        deal_iso_code=body.get("deal_iso_code"),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/quotes"),
    )


@router.get("/quotes/{quote_id}")
def read_quote(
    quote_id: str, room_id: str | None = Query(None), engine: QuoteCurrencyEngine = EngineDep
) -> dict[str, Any]:
    """One quote with its lines and both sets of totals.

    A quote that exists but holds no priced figures answers 200 with ``null`` totals and
    ``pricing_outcome: null``, because an unpriced quote is a state and not an error.
    """

    return engine.quote(quote_id, room_id=room_id)


@router.post("/quotes/{quote_id}/lines", status_code=201)
def add_line(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: QuoteCurrencyEngine = EngineDep,
) -> dict[str, Any]:
    """Add one line item: a product and a quantity, and no price.

    The research says unit prices resolve from the price row in the transaction currency, so a
    payload ``unit_price`` is refused with the field named rather than accepted. That is what
    makes "product add" a recalculation trigger: adding a line changes what the quote costs
    without any caller typing a price.
    """

    body = dict(payload or {})
    if "unit_price" in body:
        raise CurrencyRefusal(
            "A line item may not carry its own unit price.",
            {
                "unit_price": (
                    "unit_price is resolved from the price list row in the transaction "
                    "currency, so it is not accepted from the caller."
                )
            },
        )
    line = engine.add_line(
        quote_id,
        body.get("product_code"),
        body.get("quantity", 1),
        discount_amount=body.get("discount_amount"),
        tax_amount=body.get("tax_amount"),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/quotes/{quote_id}/lines"),
    )
    return {
        "line": line,
        "recalculate": {
            "trigger": vocab.TRIGGER_PRODUCT_ADD,
            "trigger_label": vocab.RECALCULATION_TRIGGER_LABELS[vocab.TRIGGER_PRODUCT_ADD],
            "note": (
                "Adding a line changes the quote's totals. POST /quotes/{quote_id}/price with "
                "this trigger to apply it."
            ),
        },
    }


@router.patch("/quotes/{quote_id}/currency")
def change_currency(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: QuoteCurrencyEngine = EngineDep,
) -> dict[str, Any]:
    """Change a quote's transaction currency, or refuse because it holds line items.

    The refusal is the sourced constraint: "You can't change the currency of the base record
    (in this case, an quote), unless you remove all the line items associated with the
    record."

    A PATCH rather than a POST, because the currency is a field of the quote rather than a
    resource of its own, and a PATCH on one field is the shape a caller expects.
    """

    body = dict(payload or {})
    return engine.change_currency(
        quote_id,
        body.get("iso_code"),
        price_list_id=_price_list_id_of(body),
        room_id=room_id,
        actor=actor,
        source=_source("PATCH", "/quotes/{quote_id}/currency"),
    )


# --------------------------------------------------------------------------- #
# Pricing
# --------------------------------------------------------------------------- #


@router.post("/quotes/{quote_id}/price")
def price_quote(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: QuoteCurrencyEngine = EngineDep,
) -> dict[str, Any]:
    """Run the pricing, and return either both sets of totals or a named refusal.

    ``trigger`` names which of the research's four recalculation events this run answers, and
    it defaults to ``record_open`` because the research's first trigger is the record being
    opened. Each run raises the ``RetrieveExchangeRate`` event and stamps the answer onto the
    quote, so the base figures are computed against the rate in force when the quote was
    priced.

    A wrong-currency combination answers 200 with ``outcome: refused`` and the researched
    ``pricing_error_code``. That is deliberate: the run happened, it produced a definite answer,
    and a refusal is a state a reviewer must be able to see. It writes an audit row exactly as
    a successful run does.
    """

    body = dict(payload or {})
    return engine.price_quote(
        quote_id,
        trigger=body.get("trigger") or vocab.TRIGGER_OPEN,
        discount=body.get("discount"),
        tax=body.get("tax"),
        freight=body.get("freight"),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/quotes/{quote_id}/price"),
    )


@router.get("/quotes/{quote_id}/pricing-runs")
def list_pricing_runs(
    quote_id: str, room_id: str | None = Query(None), engine: QuoteCurrencyEngine = EngineDep
) -> dict[str, Any]:
    """Every pricing run on one quote, newest first.

    A read, so it records no audit row. Each run carries its trigger, its rate and its outcome,
    which is what makes "when was this quote priced, at what rate, and did it refuse" a
    question with an answer rather than a question.
    """

    engine.quote(quote_id, room_id=room_id)  # a missing quote is a 404
    runs = engine.pricing_runs(quote_id, room_id=room_id)
    return {
        "quote_id": quote_id,
        "count": len(runs),
        "runs": runs,
        "outcomes": list(vocab.PRICING_OUTCOMES),
        "recalculation_triggers": list(vocab.RECALCULATION_TRIGGERS),
    }


@router.get("/quotes/{quote_id}/rate-reads")
def list_rate_reads(
    quote_id: str, room_id: str | None = Query(None), engine: QuoteCurrencyEngine = EngineDep
) -> dict[str, Any]:
    """Every rate this quote's pricing raised an event for, newest first.

    The rate source in the research is an event, not a poll, so "when is a rate fetched" is
    answered by rows rather than by a comment. A refused run appears here too, because the
    event fires before the refusal is decided.
    """

    engine.quote(quote_id, room_id=room_id)  # a missing quote is a 404
    reads = engine.rate_reads(quote_id, room_id=room_id)
    return {
        "quote_id": quote_id,
        "count": len(reads),
        "reads": reads,
        "event": vocab.RATE_EVENT,
        "event_note": vocab.RATE_EVENT_NOTE,
        "recalculation_triggers": list(vocab.RECALCULATION_TRIGGERS),
    }


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the states the specification's user flow describes, not just the happy path.

    Every row is produced by calling the real :class:`QuoteCurrencyEngine`, so the demo cannot
    show a state, a count or an audit row the HTTP routes would not produce.

    The states seeded, and why each is here:

    * **USD as the base currency** and **EUR as a ``Custom`` currency record with a stamped
      rate**, because the rate policy is the decision the issue asks to be recorded and a
      board with no rate on it cannot show a rate at all.
    * **A GBP ``Standard`` record with no rate**, so the board shows a currency the deployment
      has registered but not yet priced in, which is the honest state of a real roll-out and
      the one a reviewer would otherwise find missing.
    * **Two price lists, one per currency**, each with the same three products, so the same
      product is visibly priced twice and the "multi-currency is modelled as multiple price
      lists" rule is visible rather than asserted.
    * **A priced EUR quote** with two lines, a line discount, a line tax and freight, so both
      sets of totals are on the board and the authoritative figure is named beside them.
    * **A quote whose price list is in the wrong currency**, refused with **code 34**. This is
      the refusal the specification requires, and it can only be observed if the wrong
      combination can exist, so the seed creates one deliberately.
    * **A quote with a product that has no price row**, refused with **code 38**, so the
      board carries both codes rather than only the one a mis-selected price list produces.
    * **A second USD quote priced from the USD price list**, so the board shows two currencies
      priced and one conversion, rather than a single example.
    * **A quote whose currency was changed** because it held no line items, so the board shows
      the constraint has an allowed path as well as a refused one.

    The pricing runs are executed rather than left pending, because a board where every quote
    reads ``null`` is a board a reviewer cannot check. The currency-change refusal is *not*
    executed, for the same reason WF-085 left its purge pending: the refused state is the one
    worth opening.

    The return string is ASCII and is asserted encodable by cp1252 in
    ``tests/test_wf089.py``: the seeder prints it to a Windows console, and one RIGHTWARDS
    ARROW in a recovered feature's return string broke the whole seeder.
    """

    store = RecordStore(db)
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return ""
    now = context["now"]
    eu_room = room_ids[0][0]
    us_room = room_ids[min(1, len(room_ids) - 1)][0]
    engine = QuoteCurrencyEngine(store, now=lambda: now)

    # 1. Three currency records. The base currency, a Custom record with a stamped rate, and
    #    a Standard record with no rate yet, which is a real state during a roll-out.
    #
    #    Registered at organisation scope with no room, because ``transactioncurrency`` is an
    #    organisation table. A deployment does not register its base currency once per data
    #    room, and a second room without one would have no base currency to report in.
    engine.register_currency(
        "USD",
        is_base_currency=True,
        currency_symbol="$",
        name="US Dollar (organisation base currency)",
        actor="dana",
        source="wf-089 seed",
    )
    engine.register_currency(
        "EUR",
        currency_type="Custom",
        exchange_rate="1.08",
        currency_symbol="EUR",
        name="Euro (negotiated rate)",
        actor="dana",
        source="wf-089 seed",
    )
    engine.register_currency(
        "GBP",
        currency_symbol="GBP",
        name="Pound Sterling (rate pending)",
        actor="sam",
        source="wf-089 seed",
    )

    # 2. One price list per currency, each pricing the same three products. A price list
    #    carries one currency, which is how multi-currency is modelled here.
    catalogue = {
        "SEAT-STD": {"USD": "1320.00", "EUR": "1200.00"},
        "SEAT-PRE": {"USD": "1980.00", "EUR": "1800.50"},
        "SEAT-ENT": {"USD": "2460.00", "EUR": "2236.00"},
    }
    lists: dict[str, dict[str, Any]] = {}
    for code, room_id in (("USD", us_room), ("EUR", eu_room)):
        price_list = engine.create_price_list(
            f"{code} enterprise price list",
            code,
            room_id=room_id,
            actor="dana",
            source="wf-089 seed",
        )
        for product, prices in catalogue.items():
            engine.create_price_item(
                price_list["id"],
                product,
                prices[code],
                tier="standard",
                room_id=room_id,
                actor="dana",
                source="wf-089 seed",
            )
        lists[code] = price_list

    # 3. The priced EUR quote. Both sets of totals land on the board, and the authoritative
    #    figure is named beside them.
    renewal = engine.create_quote(
        "Northwind platform renewal",
        "EUR",
        price_list_id=lists["EUR"]["id"],
        room_id=eu_room,
        actor="dana",
        source="wf-089 seed",
    )
    engine.add_line(
        renewal["id"], "SEAT-STD", 10, room_id=eu_room, actor="dana", source="wf-089 seed"
    )
    engine.add_line(
        renewal["id"],
        "SEAT-PRE",
        2,
        discount_amount="500.00",
        tax_amount="300.00",
        room_id=eu_room,
        actor="dana",
        source="wf-089 seed",
    )
    priced = engine.price_quote(
        renewal["id"],
        trigger=vocab.TRIGGER_CREATE,
        freight="250.00",
        room_id=eu_room,
        actor="dana",
        source="wf-089 seed",
    )["run"]

    # 4. A priced USD quote, so the board shows two currencies priced rather than one
    #    conversion repeated.
    expansion = engine.create_quote(
        "Halcyon seat expansion",
        "USD",
        price_list_id=lists["USD"]["id"],
        room_id=us_room,
        actor="sam",
        source="wf-089 seed",
    )
    engine.add_line(
        expansion["id"], "SEAT-ENT", 6, room_id=us_room, actor="sam", source="wf-089 seed"
    )
    engine.price_quote(
        expansion["id"],
        trigger=vocab.TRIGGER_PRODUCT_ADD,
        room_id=us_room,
        actor="sam",
        source="wf-089 seed",
    )

    # 5. The wrong-currency price list. The mismatch is allowed at stamping and refused at
    #    pricing, as code 34, and that refusal is the state the specification asks a reviewer
    #    to be able to see.
    wrong_list = engine.create_quote(
        "Vantage renewal priced off the USD list",
        "EUR",
        price_list_id=lists["USD"]["id"],
        room_id=eu_room,
        actor="dana",
        source="wf-089 seed",
    )
    engine.add_line(
        wrong_list["id"], "SEAT-STD", 4, room_id=eu_room, actor="dana", source="wf-089 seed"
    )
    refused_list = engine.price_quote(
        wrong_list["id"],
        trigger=vocab.TRIGGER_PRODUCT_ADD,
        room_id=eu_room,
        actor="dana",
        source="wf-089 seed",
    )["run"]
    # Asserted rather than assumed. A seed that stopped producing a refusal would otherwise
    # still print the line describing it, and the demo would lie about the state the
    # specification asks a reviewer to be able to see.
    if refused_list["pricing_error_code"] != vocab.CODE_PRICE_LIST_CURRENCY_MISMATCH:
        raise AssertionError(
            f"the seed expected code {vocab.CODE_PRICE_LIST_CURRENCY_MISMATCH} and got "
            f"{refused_list['pricing_error_code']!r}"
        )

    # 6. The product with no price row in the transaction currency, refused as code 38.
    unpriced_product = engine.create_quote(
        "Meridian pilot with an unpriced module",
        "EUR",
        price_list_id=lists["EUR"]["id"],
        room_id=eu_room,
        actor="dana",
        source="wf-089 seed",
    )
    engine.add_line(
        unpriced_product["id"],
        "MODULE-ANALYTICS",
        1,
        room_id=eu_room,
        actor="dana",
        source="wf-089 seed",
    )
    refused_product = engine.price_quote(
        unpriced_product["id"],
        trigger=vocab.TRIGGER_OPEN,
        room_id=eu_room,
        actor="dana",
        source="wf-089 seed",
    )["run"]
    # The same assertion for the second code. Both refusal codes have to be on the demo
    # board, or the demo shows that one kind of refusal is possible and never the other.
    if refused_product["pricing_error_code"] != vocab.CODE_LINE_ITEM_HAS_NO_PRICE:
        raise AssertionError(
            f"the seed expected code {vocab.CODE_LINE_ITEM_HAS_NO_PRICE} and got "
            f"{refused_product['pricing_error_code']!r}"
        )

    # 7. A quote whose currency changed, because it held no line items. The constraint the
    #    research states has an allowed path as well as a refused one, and the board shows it.
    reconvert = engine.create_quote(
        "Cypress quote currency converted before any line was added",
        "EUR",
        price_list_id=lists["EUR"]["id"],
        room_id=eu_room,
        actor="dana",
        source="wf-089 seed",
    )
    engine.change_currency(
        reconvert["id"],
        "USD",
        price_list_id=lists["USD"]["id"],
        room_id=eu_room,
        actor="dana",
        source="wf-089 seed",
    )

    # Counts are read back rather than written out, so the line the seeder prints cannot
    # describe a state the seed did not produce.
    board = engine.summary()

    return (
        f"3 transaction currencies registered with USD as the base currency, EUR carrying a "
        f"stamped custom rate of {priced['rate']} and GBP still without one; "
        f"2 single-currency price lists pricing the same 3 products each; "
        f"{board['priced_quotes']} quote(s) priced, the largest at "
        f"{priced['totals']['totalamount']} EUR which is {priced['totals_base']['totalamount_base']} "
        f"in the base currency; "
        f"{board['refused_quotes']} quote(s) refused to price, {board['refusals_by_code']['34']} "
        f"with code 34 Invalid Price Level Currency and {board['refusals_by_code']['38']} with "
        f"code 38 Transaction currency is not set for the product price list item; "
        f"1 quote re-stamped in another currency because it held no line item"
    )
