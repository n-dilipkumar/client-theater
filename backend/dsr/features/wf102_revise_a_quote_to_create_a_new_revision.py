"""WF-102: revise a quote to create a new revision instead of losing the deal.

The rules live in :mod:`dsr.quote_revision`, which knows nothing about HTTP. This
module is the four things a feature contributes, and the three things it must
never contribute.

What this module contributes
----------------------------

* The route table, under a prefix this feature owns (``/api/wf-102``).
* The mapping from this workflow's error types to responses, exported as
  :data:`EXCEPTION_HANDLERS` because FastAPI accepts handlers on the app object
  only.
* The demo rows, as ``seed(db, context)`` rather than as an edit to the shared
  ``backend/seed.py``.
* The vocabulary the page renders, served from one place.

What it must never contribute
-----------------------------

* No shared file: ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``,
  ``dsr/db/audited.py``, ``seed.py``, ``App.jsx``, ``main.jsx``, ``lib/api.js``,
  ``lib/features.js``, ``components/ui.jsx``, ``vite.config.js``.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``.
* No hand-written ``source=`` string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served the write.

Source: ``docs/research/digital-sales-room-workflows/wf/WF-102.md``; issue 196.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.quote_revision import QuoteRevisionEngine
from dsr.quote_revision.errors import RevisionConflict, RevisionError, RevisionNotFound
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-102-quote-revision",
    "ticket": "WF-102",
    "name": "Revise a quote into a new revision",
    "description": (
        "Revise a quote by creating a new draft with the next revision id while the prior "
        "record closes as Revised, so the deal history stays readable. Close alternatives, "
        "clone, recall and re-publish are part of the same lineage."
    ),
    "nav": [{"id": "wf-102-quote-revision", "label": "Quote revisions"}],
}

router = APIRouter(prefix="/api/wf-102", tags=["WF-102"])


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _revision_error(request: Request, exc: RevisionError) -> JSONResponse:
    """422, with each message beside its field."""

    return JSONResponse(
        status_code=422,
        content={"error": "revision_invalid", **exc.as_dict()},
    )


def _revision_not_found(request: Request, exc: RevisionNotFound) -> JSONResponse:
    """404, worded so it does not confirm what a caller may not see."""

    return JSONResponse(
        status_code=404,
        content={"error": "revision_not_found", "detail": str(exc)},
    )


def _revision_conflict(request: Request, exc: RevisionConflict) -> JSONResponse:
    """409, with a stable reason token the page branches on."""

    return JSONResponse(
        status_code=409,
        content={"error": "revision_conflict", **exc.as_dict()},
    )


EXCEPTION_HANDLERS = {
    RevisionError: _revision_error,
    RevisionNotFound: _revision_not_found,
    RevisionConflict: _revision_conflict,
}


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


def get_engine(store: RecordStore = StoreDep) -> QuoteRevisionEngine:
    """The engine, built on the shared audited store.

    The clock is left at its production default so a caller cannot pass a past
    date and make a closed quote look live. Tests inject the clock directly.
    """

    return QuoteRevisionEngine(store)


EngineDep = Depends(get_engine)


def _source(verb: str, suffix: str) -> str:
    """The audit ``source`` for a write: the path this router actually serves."""

    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every word, reason and evidenced rule this workflow enforces."""

    return QuoteRevisionEngine.vocabulary()


@router.get("/summary")
def summary(
    room_id: str | None = Query(default=None),
    engine: QuoteRevisionEngine = EngineDep,
) -> dict[str, Any]:
    """The headline counts a seller needs before opening a quote."""

    return engine.summary(room_id=room_id)


@router.get("/quotes")
def list_quotes(
    room_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    engine: QuoteRevisionEngine = EngineDep,
) -> dict[str, Any]:
    """Every quote, or one room's, oldest first, with its derived facts."""

    quotes = engine.list_quotes(room_id=room_id, status=status)
    return {"count": len(quotes), "entries": quotes}


@router.get("/quotes/{quote_id}")
def read_quote(quote_id: str, engine: QuoteRevisionEngine = EngineDep) -> dict[str, Any]:
    """One quote with its line items, pricing mode and revision number."""

    return engine.read_quote(quote_id)


@router.get("/quotes/{quote_id}/lineage")
def lineage(quote_id: str, engine: QuoteRevisionEngine = EngineDep) -> dict[str, Any]:
    """The revision chain for a quote, oldest first."""

    return engine.lineage(quote_id)


@router.get("/quotes/{quote_id}/activity")
def quote_activity(
    quote_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    engine: QuoteRevisionEngine = EngineDep,
) -> dict[str, Any]:
    """The close, recall and re-publish trail for a quote, newest first."""

    entries = engine.activity(quote_id, limit=limit)
    return {"count": len(entries), "entries": entries}


@router.post("/quotes/{quote_id}/revise")
def revise(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: QuoteRevisionEngine = EngineDep,
) -> dict[str, Any]:
    """Revise a quote: a new draft with the next revision id, the prior closed as Revised.

    This is the *Revise* command. It writes no event row of its own, because the
    research records ``ReviseQuote`` as ``Event: False``; the ``quoteclose``
    activity it triggers is the row an event consumer reads.
    """

    return engine.revise(
        quote_id,
        payload,
        source=_source("POST", f"/quotes/{quote_id}/revise"),
        actor=payload.get("actor"),
    )


@router.post("/quotes/{quote_id}/close")
def close(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: QuoteRevisionEngine = EngineDep,
) -> dict[str, Any]:
    """Close a quote as Lost, Canceled or Revised, with the same quoteclose activity."""

    return engine.close(
        quote_id,
        payload,
        source=_source("POST", f"/quotes/{quote_id}/close"),
        actor=payload.get("actor"),
    )


@router.post("/quotes/{quote_id}/clone")
def clone(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: QuoteRevisionEngine = EngineDep,
) -> dict[str, Any]:
    """Clone a quote: copy the line prices, terms and discount, leaving the original open."""

    return engine.clone(
        quote_id,
        payload,
        source=_source("POST", f"/quotes/{quote_id}/clone"),
        actor=payload.get("actor"),
    )


@router.post("/quotes/{quote_id}/reprice")
def reprice(
    quote_id: str,
    engine: QuoteRevisionEngine = EngineDep,
) -> dict[str, Any]:
    """Adjust a draft's prices to the current price list. A frozen quote is left alone."""

    return engine.reprice(
        quote_id,
        source=_source("POST", f"/quotes/{quote_id}/reprice"),
        actor=None,
    )


@router.post("/quotes/{quote_id}/recall")
def recall(
    quote_id: str,
    engine: QuoteRevisionEngine = EngineDep,
) -> dict[str, Any]:
    """Recall a published quote back to draft, refused once it is approved, accepted or paid."""

    return engine.recall(
        quote_id,
        source=_source("POST", f"/quotes/{quote_id}/recall"),
        actor=None,
    )


@router.post("/quotes/{quote_id}/republish")
def republish(
    quote_id: str,
    engine: QuoteRevisionEngine = EngineDep,
) -> dict[str, Any]:
    """Re-issue a recalled quote, generating a fresh link and PDF."""

    return engine.republish(
        quote_id,
        source=_source("POST", f"/quotes/{quote_id}/republish"),
        actor=None,
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the states the research says a revision must keep readable.

    Four quotes, and not all of them succeed, because a demo containing only the
    happy path teaches a reviewer nothing:

    * a quote revised twice, so the chain reads revision 0, 1, 2 with the first
      two closed as Revised and the third open;
    * a published quote closed as Lost;
    * a published quote closed as Canceled;
    * a published quote recalled and re-published, so the activity trail and the
      regenerated artefacts are visible;
    * a draft cloned so the clone's copied prices are visible beside the
      revision's repriced ones.

    Every character of the returned string is cp1252-encodable. The seeder prints
    it on a Windows console, and one arrow character in one recovered feature
    broke the whole seed.
    """

    rooms = context.get("room_ids") or []
    if not rooms:
        return ""
    first = rooms[0]
    room_id = first[0] if isinstance(first, (list, tuple)) else first
    now = context["now"]
    store = RecordStore(db)
    engine = QuoteRevisionEngine(store, now=lambda: now)

    def make_quote(title: str, status: str, qty: float, price: float) -> str:
        record = db.create(
            "wf086_quote",
            {
                "title": title,
                "status": status,
                "template_type": "CPQ_QUOTE",
                "currency": "EUR",
                "currency_label": "EUR",
                "account": "Northwind Traders",
                "company_name": "Northwind Traders",
                "owner": "dana",
                "deal_id": "deal_northwind",
                "expires_on": "2099-12-31",
                "revision_number": 0,
                "payment_schedule": [],
                "totals": {},
                "created_at": now.isoformat(),
            },
            room_id=room_id,
            actor="dana",
            source="seed",
        )
        total = round(qty * price, 2)
        db.create(
            "wf086_line_item",
            {
                "quote_id": record["id"],
                "position": 0,
                "name": title,
                "sku": "NWD-PLAT-ANN",
                "quantity": qty,
                "unit_price": price,
                "discount_type": "percentage",
                "discount_value": 0.0,
                "tax_rate": 10.0,
                "price_source": "manual",
                "amounts": {"total": total},
            },
            room_id=room_id,
            actor="dana",
            source="seed",
        )
        return record["id"]

    # 1. Revised twice: revision 0, 1, 2.
    chain = make_quote("Northwind annual quote", "draft", 30, 4800.0)
    first = engine.revise(chain, {"actor": "dana"}, source="seed", actor="dana")["quote"]["id"]
    engine.revise(first, {"actor": "dana"}, source="seed", actor="dana")

    # 2 and 3. The two close alternatives that are not a revision.
    engine.close(
        make_quote("Contoso pilot", "published", 1, 9000.0),
        {"reason": "lost", "actor": "dana"},
        source="seed",
        actor="dana",
    )
    engine.close(
        make_quote("Fabrikam expansion", "published", 2, 4200.0),
        {"reason": "canceled", "actor": "dana"},
        source="seed",
        actor="dana",
    )

    # 4. Recall and re-publish, so the trail and the regenerated artefacts exist.
    recalled = make_quote("Adventure Works renewal", "published", 1, 15000.0)
    engine.recall(recalled, source="seed", actor="dana")
    engine.republish(recalled, source="seed", actor="dana")

    # 5. A clone, which copies the price and leaves the original open.
    clone_source = make_quote("Wingtip first edition", "draft", 4, 2500.0)
    engine.clone(clone_source, {"actor": "dana"}, source="seed", actor="dana")

    counted = engine.summary(room_id)
    return (
        "WF-102 seeded: 1 quote revised twice (revision 0, 1, 2), 1 closed as lost, "
        "1 closed as canceled, 1 recalled and re-published, 1 clone; "
        f"{counted['activities']} activities and {counted['artefacts']} artefacts in the room"
    )
