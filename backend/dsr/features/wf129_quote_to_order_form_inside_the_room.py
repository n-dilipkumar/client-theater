"""WF-129: quote-to-order-form inside the room, with a recorded signature.

The rules live in :mod:`dsr.inroom_ordering` and are not restated here. This
module is the three things a feature contributes and the three things it must
never contribute.

What this module contributes
----------------------------

* The route table, under a prefix this feature owns.
* The mapping from this workflow's own error types to responses, exported as
  :data:`EXCEPTION_HANDLERS` because FastAPI accepts handlers on the app object
  only and this feature may not edit the app.
* The demo rows, as ``seed(db, context)`` rather than as an edit to the shared
  ``backend/seed.py``.

What it must never contribute
-----------------------------

* No shared file. ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``,
  ``dsr/db/audited.py``, ``backend/seed.py``, ``App.jsx``, ``main.jsx``,
  ``lib/api.js``, ``lib/features.js``, ``components/ui.jsx``, ``vite.config.js``.
* No import of the application module. Dependencies come from ``dsr.deps``. A
  test enforces it.
* No hand-written ``source=`` route string. :func:`_source` builds every one
  from :data:`router`, so the audit row names the route that actually served the
  write.

No e-sign provider exists here
------------------------------

The research names DocuSign, Documenso and OpenSign. None is reached. A
signature is recorded as this workflow's own event, beside an audit certificate
that states in plain words that no third-party provider verified it. The
DocuSign Rooms product pages are 404, so that evidence is treated as unsourced
and is not cited as a live capability.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.inroom_ordering import vocabulary as vocab
from dsr.inroom_ordering.engine import OrderFormEngine
from dsr.inroom_ordering.errors import (
    OrderFormConflict,
    OrderFormError,
    OrderFormNotFound,
)
from dsr.inroom_ordering.rules import as_text
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-129-inroom-order-form",
    "ticket": "WF-129",
    "name": "Quote-to-order-form inside the room, with e-signature",
    "description": (
        "Render a quote as an itemised, buyer-editable order form inside the room, "
        "let the buyer redline and accept it, capture a typed, drawn or "
        "typed-photo signature there without a redirect, issue an audit "
        "certificate beside the signed artefact, and file the executed form back "
        "on the deal."
    ),
    "nav": [{"id": "wf-129-order-form", "label": "In-room order form"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several
#: workflows already share. Every path below sits under it.
router = APIRouter(prefix="/api/wf-129", tags=["WF-129"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents
    is a domain function hardcoding a URL string, which leaves the audit log
    naming a route the app stopped serving.
    """
    return f"{method} {router.prefix}{path}"


def get_engine(store: RecordStore = StoreDep) -> OrderFormEngine:
    """An :class:`OrderFormEngine` over the process-wide audited store.

    Built per request so every seam stays overridable in a test rather than
    hanging a long-lived object off ``app.state``, which is a shared file this
    feature may not edit.
    """
    return OrderFormEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _order_form_error(request: Request, exc: OrderFormError) -> JSONResponse:
    """422 with a field-keyed map, so each message lands beside its input."""
    return JSONResponse(
        status_code=422,
        content={"error": "order_form_invalid", **exc.as_dict()},
    )


def _order_form_not_found(request: Request, exc: OrderFormNotFound) -> JSONResponse:
    """404, worded so it does not confirm what a caller may not see."""
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": str(exc)},
    )


def _order_form_conflict(request: Request, exc: OrderFormConflict) -> JSONResponse:
    """409 with a stable ``reason`` token the page branches on.

    409 rather than 400 because nothing about the request is malformed. Retrying
    the same call returns the same answer, which is what makes it a conflict.
    """
    return JSONResponse(
        status_code=409,
        content={"error": "order_form_conflict", **exc.as_dict()},
    )


EXCEPTION_HANDLERS = {
    OrderFormError: _order_form_error,
    OrderFormNotFound: _order_form_not_found,
    OrderFormConflict: _order_form_conflict,
}


# --------------------------------------------------------------------------- #
# The board and the research
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(
    room_id: str | None = Query(None), engine: OrderFormEngine = EngineDep
) -> dict[str, Any]:
    """The headline numbers. Reads only."""
    return engine.summary(room_id)


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """The words this workflow enforces, so the page cannot drift from them."""
    return vocab.vocabulary()


@router.get("/decisions")
def decisions() -> dict[str, Any]:
    """Every judgement call the research left open, with the alternative rejected."""
    picks = OrderFormEngine.decisions()
    return {"count": len(picks), "decisions": picks}


# --------------------------------------------------------------------------- #
# The order form
# --------------------------------------------------------------------------- #


@router.post("/order-forms", status_code=201)
def create_order_form(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    engine: OrderFormEngine = EngineDep,
) -> dict[str, Any]:
    """Render a quote as an itemised, buyer-editable order form inside the room."""
    return engine.create_order_form(
        room_id, payload, source=_source("POST", "/order-forms"), actor="rep"
    )


@router.get("/order-forms")
def list_order_forms(
    room_id: str | None = Query(None),
    status: str | None = Query(None),
    engine: OrderFormEngine = EngineDep,
) -> dict[str, Any]:
    """Every order form, or one room's, oldest first."""
    forms = engine.list_order_forms(room_id, status)
    return {"count": len(forms), "order_forms": forms}


@router.get("/order-forms/{form_id}")
def read_order_form(form_id: str, engine: OrderFormEngine = EngineDep) -> dict[str, Any]:
    """One order form, with its lines, its redlines and its certificate."""
    return engine.read_order_form(form_id)


@router.get("/order-forms/{form_id}/certificate")
def read_certificate(form_id: str, engine: OrderFormEngine = EngineDep) -> dict[str, Any]:
    """The audit certificate for a signed order form, or a 404 when there is none."""
    engine.read_order_form(form_id)  # 404 rather than a null for a bad form id
    certificate = engine.certificate_for(form_id)
    if certificate is None:
        raise OrderFormNotFound("This order form has no audit certificate yet.")
    return certificate


# --------------------------------------------------------------------------- #
# The buyer's edits and redlines
# --------------------------------------------------------------------------- #


@router.patch("/order-forms/{form_id}/lines/{line_id}")
def update_line(
    form_id: str,
    line_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: OrderFormEngine = EngineDep,
) -> dict[str, Any]:
    """Change one line of the draft document, recomputing its figures."""
    return engine.update_line(
        form_id,
        line_id,
        payload,
        source=_source("PATCH", "/order-forms/{form_id}/lines/{line_id}"),
        actor="buyer",
    )


@router.get("/order-forms/{form_id}/revisions")
def list_revisions(form_id: str, engine: OrderFormEngine = EngineDep) -> dict[str, Any]:
    """Every redline proposed on an order form, oldest first."""
    engine.read_order_form(form_id)  # 404 for a bad form id
    rows = engine.revisions_of(form_id)
    return {"order_form_id": form_id, "count": len(rows), "revisions": rows}


@router.post("/order-forms/{form_id}/revisions", status_code=201)
def propose_revision(
    form_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: OrderFormEngine = EngineDep,
) -> dict[str, Any]:
    """Propose a redline on one line. It applies only when it is accepted."""
    return engine.propose_revision(
        form_id,
        payload,
        source=_source("POST", "/order-forms/{form_id}/revisions"),
        actor=as_text(payload.get("proposed_by"), "buyer"),
    )


@router.post("/revisions/{revision_id}/resolve")
def resolve_revision(
    revision_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: OrderFormEngine = EngineDep,
) -> dict[str, Any]:
    """Accept or reject a redline. An accepted redline bumps the document version."""
    return engine.resolve_revision(
        revision_id,
        payload,
        source=_source("POST", "/revisions/{revision_id}/resolve"),
        actor=as_text(payload.get("resolved_by")) or "rep",
    )


# --------------------------------------------------------------------------- #
# The buyer's steps in the room
# --------------------------------------------------------------------------- #


@router.post("/order-forms/{form_id}/accept")
def accept(
    form_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    engine: OrderFormEngine = EngineDep,
) -> dict[str, Any]:
    """The buyer accepts the document in the room. Freezes it for signing."""
    return engine.accept(
        form_id,
        payload,
        room_id=room_id,
        source=_source("POST", "/order-forms/{form_id}/accept"),
        actor=as_text(payload.get("accepted_by")) or "buyer",
    )


@router.post("/order-forms/{form_id}/sign")
def sign(
    form_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    engine: OrderFormEngine = EngineDep,
) -> dict[str, Any]:
    """Capture the buyer's signature in the room and issue the audit certificate."""
    return engine.sign(
        form_id,
        payload,
        room_id=room_id,
        source=_source("POST", "/order-forms/{form_id}/sign"),
        actor=as_text(payload.get("signed_by")) or "buyer",
    )


@router.post("/order-forms/{form_id}/file")
def file_back(
    form_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: OrderFormEngine = EngineDep,
) -> dict[str, Any]:
    """File the executed order form on the deal and advance its close date."""
    return engine.file_back(
        form_id,
        payload,
        source=_source("POST", "/order-forms/{form_id}/file"),
        actor=as_text(payload.get("filed_by")) or "rep",
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the states this workflow exists to show, on the first room.

    The states seeded, and why each is here:

    * **a draft order form**, buyer-editable, with one redline proposed and
      accepted, so the version history and the accepted revision are visible;
    * **an accepted order form**, frozen and waiting on a signature, so the
      "accept then sign" order is visible;
    * **a signed order form**, so the signature, the signed artefact and the
      audit certificate all exist;
    * **a filed order form**, so the write-back onto the deal and the advanced
      close date are visible.

    The seed calls the real engine rather than writing rows, so it proves the
    rules produce these states instead of asserting them. A quote to render is
    reused when the mirror holds one and provisioned only when it holds none,
    and the deal is provisioned only when ``crm_deal`` is empty, so another
    workflow's counts are not moved to make this page fuller.

    Every character of the returned string is cp1252-encodable. The seeder
    prints it on a Windows console, and one RIGHTWARDS ARROW in a recovered
    feature's return string broke the whole seeder. The string names three
    non-success states on purpose: no e-sign provider, no MAP surface and no
    PDF/DOCX rendering.
    """
    store = RecordStore(db)
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return ""
    room_id = room_ids[0][0]
    engine = OrderFormEngine(store, now=lambda: context["now"])
    source, actor = "seed", "dana"

    quote_id, quote_provisioned = _quote_to_render(store, room_id, source, actor)
    deal_id = _deal_for_filing(store, room_id, source, actor)

    # 1. A draft, buyer-editable, with a redline accepted into version 2.
    draft = engine.create_order_form(
        room_id,
        {"quote_id": quote_id, "title": "Northwind order form (in review)"},
        source=source,
        actor=actor,
    )
    draft_id = draft["order_form"]["id"]
    lines = draft["order_form"]["lines"]
    redline_accepted = False
    if lines:
        proposal = engine.propose_revision(
            draft_id,
            {
                "line_id": lines[0]["id"],
                "field": "quantity",
                "proposed_value": 40,
                "proposed_by": "buyer",
            },
            source=source,
            actor="ada",
        )
        engine.resolve_revision(
            proposal["revision"]["id"],
            {"decision": "accept", "resolved_by": "dana"},
            source=source,
            actor="dana",
        )
        redline_accepted = True

    # 2. Accepted, frozen and waiting on a signature.
    accepted = engine.create_order_form(
        room_id,
        {"quote_id": quote_id, "title": "Contoso order form (accepted)"},
        source=source,
        actor=actor,
    )
    engine.accept(
        accepted["order_form"]["id"],
        {"accepted_by": "Ada Byron"},
        source=source,
        actor="ada",
    )

    # 3. Signed, with the artefact and the audit certificate.
    to_sign = engine.create_order_form(
        room_id,
        {"quote_id": quote_id, "title": "Fabrikam order form (signed)"},
        source=source,
        actor=actor,
    )
    signed_id = to_sign["order_form"]["id"]
    engine.accept(signed_id, {"accepted_by": "Rui Almeida"}, source=source, actor="rui")
    engine.sign(
        signed_id,
        {
            "mode": "typed_photo",
            "text": "Rui Almeida",
            "image": "signature-scan.png",
            "signer": {"name": "Rui Almeida", "email": "rui@fabrikam.example"},
        },
        source=source,
        actor="rui",
    )

    # 4. Signed and filed back on the deal.
    to_file = engine.create_order_form(
        room_id,
        {"quote_id": quote_id, "deal_id": deal_id, "title": "Adventure Works order form (filed)"},
        source=source,
        actor=actor,
    )
    filed_id = to_file["order_form"]["id"]
    engine.accept(filed_id, {"accepted_by": "Grace Okonkwo"}, source=source, actor="grace")
    engine.sign(
        filed_id,
        {
            "mode": "drawn",
            "strokes": [[0, 0], [10, 12], [22, 8]],
            "signer": {"name": "Grace Okonkwo", "email": "grace@adventure.example"},
        },
        source=source,
        actor="grace",
    )
    filed = engine.file_back(filed_id, source=source, actor="dana")

    counted = engine.summary(room_id)
    deal_state = "deal updated" if filed["deal_filed"] else "no crm_deal to file onto"
    redline_state = (
        f"{counted['revisions_accepted']} redline accepted" if redline_accepted else "no redline"
    )
    return (
        f"{counted['order_forms']} order forms: "
        f"{counted['by_status'][vocab.STATUS_DRAFT]} draft editable in the room "
        f"({redline_state}), "
        f"{counted['by_status'][vocab.STATUS_ACCEPTED]} accepted awaiting signature, "
        f"{counted['by_status'][vocab.STATUS_SIGNED]} signed with an audit certificate, "
        f"{counted['by_status'][vocab.STATUS_FILED]} filed to the deal ({deal_state}); "
        f"quote {'provisioned' if quote_provisioned else 'reused'}; "
        "MAP not advanced (no MAP surface); "
        "payment links out of scope; "
        "PDF/DOCX rendering unavailable (json only)"
    )


def _quote_to_render(store: RecordStore, room_id: str, source: str, actor: str) -> tuple[str, bool]:
    """A quote with at least one line, reused when the mirror holds one.

    WF-086 owns ``wf086_quote`` and provisions quotes in its own seed. This
    workflow renders whatever it finds and provisions a quote only when the
    mirror holds none, so it does not add to another workflow's counts.
    """
    for quote in store.list(vocab.QUOTE_COLLECTION, limit=50):
        if store.find(vocab.QUOTE_LINE_ITEM_COLLECTION, {"quote_id": str(quote["id"])}, limit=1):
            return str(quote["id"]), False

    quote = store.create(
        vocab.QUOTE_COLLECTION,
        {
            "title": "Northwind Traders annual order",
            "status": "draft",
            "account": "Northwind Traders",
            "company_name": "Northwind Traders",
            "owner": "dana",
            "currency": "USD",
            "expires_on": "2099-06-30",
            "totals": {"total": 0.0, "total_contract_value": 0.0},
        },
        room_id=room_id,
        actor=actor,
        source=source,
    )
    for position, (name, sku, qty, price, billing, tax) in enumerate(
        [
            ("Northwind Platform, annual", "NWD-PLAT-ANN", 40, 4800.0, "recurring", 0.0),
            ("Onboarding workshop", "NWD-IMP-ONB", 2, 1500.0, "one_time", 0.0),
        ]
    ):
        store.create(
            vocab.QUOTE_LINE_ITEM_COLLECTION,
            {
                "quote_id": str(quote["id"]),
                "position": position,
                "name": name,
                "sku": sku,
                "quantity": qty,
                "unit_price": price,
                "billing_frequency": billing,
                "tax_rate": tax,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
    return str(quote["id"]), True


def _deal_for_filing(store: RecordStore, room_id: str, source: str, actor: str) -> str | None:
    """The deal the executed order form is filed on.

    Reused when the CRM mirror holds one. Provisioned only when the mirror is
    empty, because another workflow owns ``crm_deal`` and a rollup of its own
    counts those rows: writing a deal into a populated mirror would move
    somebody else's numbers to make this page look fuller.
    """
    existing = store.list(vocab.DEAL_COLLECTION, limit=1)
    if existing:
        return str(existing[0]["id"])
    deal = store.create(
        vocab.DEAL_COLLECTION,
        {
            "name": "Adventure Works pilot and expansion",
            "account": "Adventure Works",
            "owner": "dana",
            "currency": "USD",
            "amount": 0.0,
            "stage": "negotiation",
            "close_date": "2099-12-31",
        },
        room_id=room_id,
        actor=actor,
        source=source,
    )
    return str(deal["id"])
