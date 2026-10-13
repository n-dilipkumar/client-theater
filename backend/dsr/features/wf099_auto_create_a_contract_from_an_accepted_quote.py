"""WF-099: auto-create a contract from an accepted quote.

The rules live in :mod:`dsr.contract_generation`, which knows nothing about HTTP.
This module is the four things a feature adds and nothing else:

* the route table, an ``APIRouter`` this feature owns;
* the error mapping, an ``EXCEPTION_HANDLERS`` export the host attaches, because
  FastAPI accepts exception handlers on the app object only;
* the demo rows, a ``seed(db, context)`` hook the seeder calls;
* the settings the page reads, served from one place so a panel cannot drift from
  the rules that enforce it.

Dependencies come from :mod:`dsr.deps`. Nothing here imports ``dsr.api`` and
nothing opens SQLite, so the enforced test that greps feature modules for those
two things passes by construction.

Every write passes a ``source`` built from ``router.prefix`` and the route's own
path, so each audit row names a URL the app actually serves.

Source: ``docs/research/digital-sales-room-workflows/wf/WF-099.md``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.contract_generation import vocabulary as vocab
from dsr.contract_generation.engine import ContractEngine
from dsr.contract_generation.errors import ContractConflict, ContractError, ContractNotFound
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-099-auto-create-a-contract-from-an-accepted-quote",
    "ticket": "WF-099",
    "name": "Auto-create a contract from an accepted quote",
    "description": (
        "When a quote is accepted, create a contract that inherits its line items and "
        "terms, and an order when the seller is in the Connected CPQ beta. The setting, "
        "the effective start date and the legacy exclusion are all recorded."
    ),
    "nav": [{"id": "wf-099-auto-create-a-contract-from-an-accepted-quote", "label": "Contracts"}],
}

router = APIRouter(prefix="/api/wf-099", tags=["WF-099"])


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _contract_error(request: Request, exc: ContractError) -> JSONResponse:
    """A malformed request. 422, with each message beside its field."""

    return JSONResponse(
        status_code=422,
        content={"error": "contract_invalid", **exc.as_dict()},
    )


def _contract_not_found(request: Request, exc: ContractNotFound) -> JSONResponse:
    """A quote or contract that does not exist. 404."""

    return JSONResponse(
        status_code=404,
        content={"error": "contract_not_found", "detail": str(exc)},
    )


def _contract_conflict(request: Request, exc: ContractConflict) -> JSONResponse:
    """The record's state refuses the change. 409, with a stable reason token."""

    return JSONResponse(
        status_code=409,
        content={"error": "contract_conflict", **exc.as_dict()},
    )


EXCEPTION_HANDLERS = {
    ContractError: _contract_error,
    ContractNotFound: _contract_not_found,
    ContractConflict: _contract_conflict,
}


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


def get_engine(store: RecordStore = StoreDep) -> ContractEngine:
    """The engine, built on the shared audited store."""

    return ContractEngine(store)


EngineDep = Depends(get_engine)


def _source(verb: str, suffix: str) -> str:
    """The audit ``source`` for a write: the path this router actually serves."""

    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every word, mode, flow and refusal reason the rules use, from one place."""

    return vocab.vocabulary()


@router.get("/summary")
def summary(
    room_id: str | None = Query(default=None),
    engine: ContractEngine = EngineDep,
) -> dict[str, Any]:
    """Counts a seller needs before opening a contract."""

    return engine.summary(room_id=room_id)


@router.get("/settings")
def get_settings(
    room_id: str | None = Query(default=None),
    engine: ContractEngine = EngineDep,
) -> dict[str, Any]:
    """The contract-generation setting, with the default marked."""

    return engine.settings(room_id=room_id)


@router.post("/settings")
def update_settings(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    engine: ContractEngine = EngineDep,
) -> dict[str, Any]:
    """Turn contract creation on or off and record the settings page values.

    Only the fields this workflow knows are read. An unknown key is ignored
    rather than refused, so a team adding a field needs no change here.
    """

    return engine.update_settings(
        payload,
        room_id=room_id,
        actor=payload.get("actor"),
        source=_source("POST", "/settings"),
    )


@router.get("/contracts")
def list_contracts(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: ContractEngine = EngineDep,
) -> dict[str, Any]:
    """Contracts, each with its derived term label and order link."""

    records = engine.contracts(room_id=room_id, limit=limit)
    return {"count": len(records), "entries": records}


@router.post("/contracts")
def create_contract(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    engine: ContractEngine = EngineDep,
) -> dict[str, Any]:
    """Create a standalone contract with no quote.

    This is the Direct Create, Edit, and Renew beta path, gated by its own
    setting because the research calls it a separate beta.
    """

    return engine.create_standalone(
        payload,
        room_id=room_id,
        actor=payload.get("actor"),
        source=_source("POST", "/contracts"),
    )


@router.get("/contracts/{contract_id}")
def get_contract(
    contract_id: str,
    engine: ContractEngine = EngineDep,
) -> dict[str, Any]:
    """One contract with its inherited line items and terms."""

    return engine.contract(contract_id)


@router.get("/quotes/{quote_id}")
def get_quote(
    quote_id: str,
    engine: ContractEngine = EngineDep,
) -> dict[str, Any]:
    """What would happen if this quote were accepted now, and why."""

    return engine.quote_readiness(quote_id)


@router.post("/quotes/{quote_id}/acceptance")
def record_acceptance(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ContractEngine = EngineDep,
) -> dict[str, Any]:
    """Record the platform's acceptance of a quote, then create its contract.

    This is the acceptance path the issue names. It is the only route that
    authorises a contract, so a generic write of the quote's ``hs_status`` cannot
    mint one. The body may name the acceptance method, the accepting party and
    the four effective-start properties the platform sets on the quote.
    """

    result = engine.accept(
        quote_id,
        payload,
        actor=payload.get("actor"),
        source=_source("POST", f"/quotes/{quote_id}/acceptance"),
    )
    return result


@router.post("/quotes/{quote_id}/contract")
def generate_contract(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ContractEngine = EngineDep,
) -> dict[str, Any]:
    """Create the contract for a quote that already carries an acceptance.

    Idempotent: a quote that already produced a contract returns it unchanged.
    """

    return engine.generate(
        quote_id,
        actor=payload.get("actor"),
        source=_source("POST", f"/quotes/{quote_id}/contract"),
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db, context):
    """Seed the states the scenario matrix and the exclusions name.

    Five quotes cover the interesting cases, and not all of them succeed: a quote
    that produces a contract and an order, one that produces a contract only, one
    where the setting is off so nothing is created, one legacy quote that is
    excluded, and one billing-enabled quote that creates no contract but still
    expects a subscription. A standalone contract covers the Direct Create beta.
    Demo data containing only the happy path teaches a reviewer nothing.

    Every character of the returned string is cp1252-encodable. The seeder prints
    it on a Windows console, and one arrow character in one recovered feature
    broke the whole seed.
    """

    rooms = context.get("room_ids") or []
    if not rooms:
        return None
    room_id = rooms[0][0]
    now = context["now"]
    store = RecordStore(db)
    engine = ContractEngine(store)

    def quote(title: str, **extra: Any) -> str:
        record = db.create(
            "wf086_quote",
            {
                "title": title,
                "status": "published",
                "template_type": "CPQ_QUOTE",
                "currency": "EUR",
                "currency_label": "EUR",
                "account": "Northwind Traders",
                "company_name": "Northwind Traders",
                "owner": "dana",
                "deal_id": "deal_northwind",
                "expires_on": "2035-12-31",
                "published_at": now.isoformat(),
                "totals": {"total": 0.0, "total_contract_value": 0.0},
                **extra,
            },
            room_id=room_id,
            actor="dana",
            source="seed",
        )
        return record["id"]

    def line(quote_id: str, position: int, name: str, qty: float, price: float) -> None:
        total = round(qty * price, 2)
        db.create(
            "wf086_line_item",
            {
                "quote_id": quote_id,
                "position": position,
                "name": name,
                "sku": f"SKU-{position:02d}",
                "quantity": qty,
                "unit_price": price,
                "discount_type": None,
                "discount_value": 0.0,
                "tax_rate": 0.21,
                "amounts": {
                    "quantity": qty,
                    "unit_price": price,
                    "subtotal": total,
                    "discount": 0.0,
                    "net": total,
                    "tax": round(total * 0.21, 2),
                    "total": total,
                    "total_with_tax": round(total * 1.21, 2),
                },
            },
            room_id=room_id,
            actor="dana",
            source="seed",
        )

    # The setting the page opens on: creation on, Connected CPQ on, direct
    # contracts on so the standalone path is demonstrable.
    engine.update_settings(
        {
            "create_contracts_from_accepted_quotes": True,
            "connected_cpq": True,
            "direct_contracts_enabled": True,
            "renewal_alert_mode": "days",
            "renewal_alert_days": 30,
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )

    # 1. Setting on and in the beta: a contract and an order.
    first = quote("Northwind annual licence", term_length=12)
    line(first, 1, "Platform licence", 1, 12000.0)
    line(first, 2, "Onboarding", 1, 2500.0)
    engine.accept(
        first,
        {"method": "esignature", "accepted_by": "buyer@northwind.example"},
        actor="dana",
        source="seed",
    )

    # 2. Setting on, not in the beta: a contract only.
    engine.update_settings({"connected_cpq": False}, room_id=room_id, actor="dana", source="seed")
    second = quote("Contoso onboarding", term_length=6)
    line(second, 1, "Onboarding sprint", 2, 4000.0)
    engine.accept(
        second,
        {"method": "clickwrap", "accepted_by": "buyer@contoso.example"},
        actor="dana",
        source="seed",
    )
    engine.update_settings({"connected_cpq": True}, room_id=room_id, actor="dana", source="seed")

    # 3. Setting off: no contract, even though the quote is accepted.
    engine.update_settings(
        {"create_contracts_from_accepted_quotes": False},
        room_id=room_id,
        actor="dana",
        source="seed",
    )
    third = quote("Fabrikam pilot", term_length=3, hs_billing_enabled=True)
    line(third, 1, "Pilot seat", 5, 300.0)
    engine.accept(
        third,
        {"method": "clickwrap", "accepted_by": "buyer@fabrikam.example"},
        actor="dana",
        source="seed",
    )
    engine.update_settings(
        {"create_contracts_from_accepted_quotes": True},
        room_id=room_id,
        actor="dana",
        source="seed",
    )

    # 4. A legacy accepted quote: recorded as accepted, excluded from contracts.
    legacy = quote("Adventure Works historic", legacy_quote=True)
    line(legacy, 1, "Legacy support", 1, 900.0)
    engine.accept(
        legacy,
        {"method": "print_and_sign", "accepted_by": "buyer@adventure.example"},
        actor="dana",
        source="seed",
    )

    # 5. A standalone contract, from the Direct Create beta.
    engine.create_standalone(
        {
            "name": "Wingtip standalone agreement",
            "currency": "EUR",
            "start_date": "2026-04-01",
            "term_length": 12,
            "line_items": [{"name": "Support retainer", "quantity": 1, "unit_price": 6000.0}],
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )

    return (
        "4 accepted quotes (1 contract and order, 1 contract only, 1 no contract with "
        "billing, 1 legacy excluded) and 1 standalone contract"
    )
