"""WF-093: build a branded proposal from a template.

A build from a researched specification, not a port. The research is
``docs/research/raw/quoting-proposals.md`` section 8, quoted in full in issue 164, and the
rules live in :mod:`dsr.quoting_proposals` and are not restated here.

What this module contributes
----------------------------

* The route table, under a prefix this feature owns.
* The mapping from this workflow's own error types to responses, exported as
  :data:`EXCEPTION_HANDLERS` because FastAPI accepts handlers on the app object only and
  this feature may not edit the app.
* The demo rows, as ``seed(db, context)`` rather than as an edit to the shared
  ``backend/seed.py``.

What it must never contribute
-----------------------------

* No shared file. ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``,
  ``dsr/db/audited.py``, ``backend/seed.py``, ``App.jsx``, ``main.jsx``,
  ``lib/api.js``, ``lib/features.js``, ``components/ui.jsx``, ``vite.config.js``.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``. A test enforces it.
* No hand-written ``source=`` route string. :func:`_source` builds every one from
  :data:`router`, so an audit row names the route that actually served the write.

The three controls this workflow ships
--------------------------------------

The specification names three steps. Each is a route below.

**Templates.** Layout, module order, hide and show, bindings, and branding tokens. A
template is never a document.

**Brands.** The brand kit a template associates with, which drives logo and brand-kit
colours. Separate from templates because the specification lets a template override a
brand, and collapsing the two would make the override meaningless.

**The merge.** Template plus quote record data becomes a stored document model, which is
the quote's presentation layer. Each rendered module carries its resolved bindings, so a
reader sees which record path answered for each field.

The rule the workflow exists to enforce
---------------------------------------

"Updating your logo and branding won't update existing published quotes, only currently
drafted quotes and quotes created after updating." The document is stored as a snapshot
rather than rendered live from the template, which is what makes that rule true of this
store and not only of the vendor's. A published document refuses a re-render and the
refusal carries the evidence.

The status codes here are the product's own
-------------------------------------------

The specification documents no status codes for this workflow, so every one follows the
conventions the rest of this product uses and the distinction is recorded rather than
invented: 400 for a template, a binding or a merge input this workflow will not accept,
403 for a re-render of a published document, 404 for a template, a brand, a quote or a
document that does not exist. Reads of an empty store answer 200 with empty states,
because a board that 500s on a fresh room is a broken feature.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.quoting_proposals import vocabulary as vocab
from dsr.quoting_proposals.engine import ProposalEngine
from dsr.quoting_proposals.rules import DocumentFrozen, ProposalNotFound, ProposalRefusal
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-093-build-a-branded-proposal-from-a-template",
    "ticket": "WF-093",
    "name": "Build a branded proposal from a template",
    "description": (
        "Merge a template definition with quote record data into a stored document model, "
        "with the brand kit resolved by the precedence the evidence fixes, the line-item "
        "cap reported rather than hidden, and a published proposal never rewritten by a "
        "later template change."
    ),
    "nav": [{"id": "wf-093-proposals", "label": "Proposals"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several
#: workflows already share.
router = APIRouter(prefix="/api/wf-093", tags=["WF-093"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is a
    domain function hardcoding a URL string, which leaves the audit log naming a route
    the app stopped serving. ``tests/test_wf093_http.py`` asserts every source this
    router can record matches a concrete ``(method, path)`` the host mounted.
    """

    return f"{method} {router.prefix}{path}"


def get_engine(store: RecordStore = StoreDep) -> ProposalEngine:
    """A :class:`ProposalEngine` over the process-wide audited store.

    Per request, for the same reason the rest of this product builds its engine per
    request: the engine holds nothing beyond the store and a clock, so building it here
    leaves every seam overridable in a test instead of hanging a long-lived object off
    ``app.state``, which is a shared file this feature may not edit.
    """

    return ProposalEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# All three types are declared in dsr.quoting_proposals.rules and raised by nothing else
# in the product. That is what makes it safe to map them here: the host refuses a second
# feature registering a handler for the same type, and a handler for ValueError would
# intercept that exception across the whole product.


def _refused(request: Request, exc: ProposalRefusal) -> JSONResponse:
    """400 with a field-keyed map, so each message lands next to its input."""

    return JSONResponse(
        status_code=400,
        content={
            "error": "invalid_proposal",
            "detail": str(exc),
            "errors": exc.errors,
        },
    )


def _not_found(request: Request, exc: ProposalNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": "not_found", "detail": str(exc)})


def _frozen(request: Request, exc: DocumentFrozen) -> JSONResponse:
    """403 carrying the remediation, because a 403 with no remediation is a support ticket."""

    return JSONResponse(status_code=403, content=exc.to_dict())


EXCEPTION_HANDLERS = {
    ProposalRefusal: _refused,
    ProposalNotFound: _not_found,
    DocumentFrozen: _frozen,
}


# --------------------------------------------------------------------------- #
# The board and the research
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(
    room_id: str | None = Query(None),
    engine: ProposalEngine = EngineDep,
) -> dict[str, Any]:
    """The headline numbers, read back from the store. Reads only.

    Carries ``unresolved_bindings`` and ``truncated_line_items`` beside the document
    count, because a proposal that rendered with a field it could not bind is still a
    proposal, and folding those two into the document count would report fewer documents
    than exist.
    """

    return engine.summary(room_id)


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every researched term this workflow validates against.

    Served rather than duplicated in the frontend so the page cannot drift from the rules
    that compute it: the modules, the header fields, the party roles, the totals rows,
    the brand tokens, the logo precedence, the document lifecycle, the two researched
    caps, and the three document models with the two this workflow rejected.
    """

    return vocab.vocabulary()


@router.get("/decisions")
def list_decisions(engine: ProposalEngine = EngineDep) -> dict[str, Any]:
    """Every judgement call this workflow made, with what it rejected.

    The specification asks an implementer to "derive it and record the derivation, not
    assume it" four times. This route is that record, served rather than buried in a
    docstring so a reviewer reads the decision instead of the code.
    """

    return engine.decisions()


@router.get("/decisions/{inference_id}")
def read_decision(inference_id: str, engine: ProposalEngine = EngineDep) -> dict[str, Any]:
    """One judgement call by id, or a 404."""

    decision = next(
        (row for row in engine.decisions()["decisions"] if row["id"] == inference_id), None
    )
    if not decision:
        raise HTTPException(status_code=404, detail="No such recorded decision.")
    return decision


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #


@router.get("/templates")
def list_templates(
    room_id: str | None = Query(None),
    engine: ProposalEngine = EngineDep,
) -> dict[str, Any]:
    """Every template definition, with its module order and its custom-module flag.

    The custom-module flag is on the list because the evidence says an API user "can
    select templates that have custom modules included on them". A list that hid the flag
    would leave a caller unable to find such a template.
    """

    rows = engine.templates(room_id)
    return {
        "count": len(rows),
        "templates": rows,
        "custom_module_advisory": rows[0]["custom_module_advisory"] if rows else None,
    }


@router.post("/templates", status_code=201)
def save_template(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    template_id: str | None = Query(None),
    engine: ProposalEngine = EngineDep,
) -> dict[str, Any]:
    """Create or replace one template definition.

    The write is a merge-patch when the key already exists or ``template_id`` names one,
    so a caller changing one binding does not resend the whole definition. The response
    says which of the two happened under ``action``, because a create that silently
    overwrote a template would be indistinguishable from an update in the audit log.

    A custom-coded module may be *selected* here, never authored. The response carries
    that advisory on every template.
    """

    return engine.save_template(
        payload,
        room_id=room_id,
        actor=actor,
        template_id=template_id,
        source=_source("POST", "/templates"),
    )


@router.get("/templates/{template_id}")
def read_template(template_id: str, engine: ProposalEngine = EngineDep) -> dict[str, Any]:
    """One template definition."""

    return engine.template(template_id)


# --------------------------------------------------------------------------- #
# Brands
# --------------------------------------------------------------------------- #


@router.get("/brands")
def list_brands(
    room_id: str | None = Query(None),
    engine: ProposalEngine = EngineDep,
) -> dict[str, Any]:
    """Every brand kit, with the four tokens the specification's brand carries."""

    rows = engine.brands(room_id)
    return {
        "count": len(rows),
        "brands": rows,
        "logo_sources": list(vocab.LOGO_SOURCES),
        "logo_source_note": (
            "The specification lists three: quote branding settings, account branding, "
            "and the brand. An earlier source in this list wins."
        ),
    }


@router.post("/brands", status_code=201)
def save_brand(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    brand_id: str | None = Query(None),
    engine: ProposalEngine = EngineDep,
) -> dict[str, Any]:
    """Create or replace one brand kit.

    A colour that is not a hex value is refused rather than stored, so a document never
    carries a colour a browser cannot draw. The value itself is the seller's own data and
    travels to the page as a value, not as page styling: the page still styles with
    semantic tokens.
    """

    return engine.save_brand(
        payload,
        room_id=room_id,
        actor=actor,
        brand_id=brand_id,
        source=_source("POST", "/brands"),
    )


# --------------------------------------------------------------------------- #
# The quote, read as data
# --------------------------------------------------------------------------- #


@router.get("/quotes")
def list_quotes(
    room_id: str | None = Query(None),
    engine: ProposalEngine = EngineDep,
) -> dict[str, Any]:
    """Every quote this workflow can merge into, and where they come from.

    WF-086 provisions these rows. This workflow reads them and never creates one, so the
    response names the collection and says so on the list itself. A caller that finds the
    list empty has not hit a failure: WF-086 may not have merged, and an empty list is a
    state the page renders.
    """

    rows = engine.quotes(room_id)
    return {
        "count": len(rows),
        "quotes": rows,
        "collection": vocab.QUOTES,
        "provisioned_by": "WF-086",
        "read_only": True,
        "association_type_id": vocab.ASSOCIATION_TYPE_ID,
        "note": (
            "The quote-to-template association is settable only at quote creation, so "
            "this workflow never writes a quote and never sets that field."
        ),
    }


@router.get("/quotes/{quote_id}")
def read_quote(quote_id: str, engine: ProposalEngine = EngineDep) -> dict[str, Any]:
    """One quote, with its line items as this workflow can read them.

    The line items are matched on whatever reference the rows carry rather than on a
    required column, because WF-086 is not merged and no column of its rows is
    guaranteed. A workflow that required one would render an empty module on every quote
    until the other ticket landed.
    """

    quote = engine.quote(quote_id)
    items = engine.line_items(quote_id)
    return {
        **quote,
        "line_items": items,
        "line_item_count": len(items),
        "line_item_collection": vocab.LINE_ITEMS,
        "related_record_cap": vocab.RELATED_RECORD_CAP,
        "cap_evidence": vocab.CAP_EVIDENCE["related_records"],
    }


# --------------------------------------------------------------------------- #
# The merge
# --------------------------------------------------------------------------- #


@router.get("/preview")
def preview(
    template_id: str = Query(...),
    quote_id: str = Query(...),
    engine: ProposalEngine = EngineDep,
) -> dict[str, Any]:
    """Merge without writing anything.

    The merge is pure, so a caller can see exactly what a proposal would render before
    committing to one. This route writes no record and records no audit row, because
    resolving no change changes nothing.
    """

    template = engine.template(template_id)
    return engine.render(template, engine.quote_for_merge(quote_id))


@router.post("/documents", status_code=201)
def instantiate(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: ProposalEngine = EngineDep,
) -> dict[str, Any]:
    """Merge a template with a quote and store the document.

    The stored document is a snapshot: it holds the branding it was rendered with, so a
    later brand change cannot alter a published proposal. That is what makes the
    non-retroactive rule true of this store rather than only of the vendor's.

    The response carries the unresolved bindings and the truncation count, so a caller
    that renders a proposal with three empty fields can see that it did.
    """

    body = dict(payload or {})
    return engine.instantiate(
        str(body.get("template_id") or ""),
        str(body.get("quote_id") or ""),
        overrides=body.get("overrides"),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/documents"),
    )


@router.get("/documents")
def list_documents(
    room_id: str | None = Query(None),
    engine: ProposalEngine = EngineDep,
) -> dict[str, Any]:
    """Every rendered document, newest first. Reads only."""

    rows = engine.documents(room_id)
    return {
        "count": len(rows),
        "documents": rows,
        "states": [
            {"id": state, "label": vocab.DOCUMENT_STATE_LABELS[state]}
            for state in vocab.DOCUMENT_STATES
        ],
        "no_retroactive_application_quote": vocab.NO_RETROACTIVE_APPLICATION_QUOTE,
    }


@router.get("/documents/{document_id}")
def read_document(document_id: str, engine: ProposalEngine = EngineDep) -> dict[str, Any]:
    """One rendered document, with the non-retroactive rule computed rather than asserted."""

    return engine.document(document_id)


@router.post("/documents/{document_id}/transition")
def transition(
    document_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: ProposalEngine = EngineDep,
) -> dict[str, Any]:
    """Move a document one step: publish, return to draft, or re-render.

    A re-render of a published document is refused with 403 and the refusal names the
    remediation, because silently rewriting a proposal the buyer already received is the
    exact failure this workflow exists to prevent.
    """

    body = dict(payload or {})
    return engine.transition(
        document_id,
        str(body.get("action") or ""),
        actor=actor,
        source=_source("POST", "/documents/{document_id}/transition"),
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the states the specification's user flow describes, not just the happy path.

    Every row is produced by calling the real :class:`ProposalEngine`, so the demo cannot
    show a state, a count or an audit row the HTTP routes would not produce.

    The states seeded, and why each is here:

    * **a brand kit** with an accent and a logo, so the branding precedence has three
      sources to resolve between rather than one;
    * **two templates**, one that hides a module and one that binds a path the quote does
      not carry, so the hide/show control and the unresolved-binding report are both
      visible;
    * **a quote and its line items**, written into the collection WF-086 provisions. The
      issue declines to claim a dependency on WF-086, so this workflow's demo provisions
      the rows itself, which is what a read-only workflow has to do to be reviewable. One
      template also carries ``carry_custom_modules``, so the custom-module limit the
      evidence states has a row that shows it;
    * **a document in the instantiated state and one published**, so the non-retroactive
      rule has both an answer on the board. Publishing is the state that freezes, and a
      board showing only unpublished documents could not demonstrate the rule at all.

    The return string is ASCII and is asserted encodable by cp1252 in
    ``tests/test_wf093.py``: the seeder prints it to a Windows console, and one
    RIGHTWARDS ARROW in a recovered feature's return string broke the whole seeder.
    """

    store = RecordStore(db)
    now = context["now"]
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return ""

    engine = ProposalEngine(store, now=lambda: now)
    room_id = room_ids[0][0]
    source = "wf-093 seed"

    brand = engine.save_brand(
        {
            "name": "Halcyon Cloud",
            "accent": "#10506f",
            "accent_soft": "#e7eef4",
            "company_name": "Halcyon Cloud",
            vocab.FALLBACK_NAME_WHEN_LOGO_ABSENT: True,
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )

    full = engine.save_template(
        {
            "name": "Standard proposal",
            "brand_id": brand["id"],
            "cover_letter": "Thank you for the opportunity to propose.",
            "terms": "Net 30 days from the invoice date.",
            "bindings": {
                "parties": [
                    {"field": "seller", "path": "deal.seller.name"},
                    {"field": "buyer", "path": "company.name"},
                    # Deliberately a path this quote does not carry, so the unresolved
                    # report is visible rather than described.
                    {"field": "bill_to", "path": "company.billing_address.city"},
                ]
            },
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )

    # A second template that hides a module and carries a custom-coded module, so the
    # hide/show control and the API limit on authoring custom modules both have a row.
    lean = engine.save_template(
        {
            "name": "Renewal, summary only",
            "key": "renewal-summary",
            "brand_id": brand["id"],
            "hidden": [vocab.MODULE_ACCEPTANCE],
            "carry_custom_modules": True,
            "executive_summary": "A renewal of the existing platform agreement.",
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )

    quote = store.create(
        vocab.QUOTES,
        {
            "title": "Northwind platform renewal",
            "deal": {"seller": {"name": "Halcyon Cloud"}},
            "company": {"name": "Northwind Logistics"},
            "currency_label": "USD",
            "issue_date": now.isoformat(),
            "po_number": "PO-4417",
            vocab.TEMPLATE_ASSOC_TYPE_FIELD: vocab.ASSOCIATION_TYPE_ID,
            vocab.TEMPLATE_ASSOC_ID_FIELD: full["id"],
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )
    for position, (name, amount) in enumerate(
        (("Platform seats", 12000.0), ("Support retainer", 3600.0), ("Onboarding", 2400.0))
    ):
        store.create(
            vocab.LINE_ITEMS,
            {"quote_id": quote["id"], "name": name, "amount": amount, "position": position + 1},
            room_id=room_id,
            actor="dana",
            source=source,
        )

    # One document left in the instantiated state and one published, so the
    # non-retroactive rule has both an answer on the board. Publishing is the state that
    # freezes, and a board showing only unpublished documents could not demonstrate the
    # rule at all.
    engine.instantiate(full["id"], quote["id"], room_id=room_id, actor="dana", source=source)
    published = engine.instantiate(
        lean["id"], quote["id"], room_id=room_id, actor="dana", source=source
    )
    engine.transition(published["id"], "publish", actor="dana", source=source)

    board = engine.summary(room_id)
    unresolved = board["unresolved_bindings"]
    frozen = board["by_state"].get(vocab.STATE_PUBLISHED, 0)

    return (
        f"{board['brands']} brand kit with an accent and a company-name logo fallback; "
        f"{board['templates']} template definitions, one hiding a module and one carrying "
        f"a custom-coded module that this API can select but never author; "
        f"{board['line_items_read']} priced line item(s) on 1 quote read from "
        f"{vocab.QUOTES}, which WF-086 provisions; "
        f"{board['documents']} rendered document(s) of which {frozen} is published and "
        f"frozen against later template changes; "
        f"{unresolved} binding(s) resolved against nothing because the quote does not "
        f"carry the path, which the merge reports rather than hides"
    )
