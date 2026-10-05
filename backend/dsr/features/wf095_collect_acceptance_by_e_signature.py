"""WF-095: collect acceptance by e-signature, countersignature and identity verification.

The rules live in :mod:`dsr.quote_acceptance` and are not restated here. This module is the
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
* No import of the application module. Dependencies come from ``dsr.deps``. A test enforces it.
* No hand-written ``source=`` route string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served the write.

The status codes here are the product's own
-------------------------------------------

The specification documents no status codes for this workflow, so every one follows the
conventions the rest of this product uses and the distinction is recorded rather than
invented:

``200``
    Everything, including a signing attempt that failed. A refused signature is a recorded
    outcome and ``outcome: failed`` with the named reason is the answer, because the attempt
    happened and produced a definite result, and because the specification says "Signing
    attempt failures are logged automatically". A caller can tell an unverified buyer from a
    broken endpoint by reading the body.
``201``
    A resource was created: an envelope, a quote, a document, a signer.
``400``
    A value this workflow will not accept: an unknown acceptance method, an e-signature quote
    with no buyer signer, an *In signing* attachment on a quote that is not an e-signature, a
    document over the 40 MB cap, a signature mode that is not draw, type or upload, and a
    signer count the envelope cannot carry.
``404``
    An envelope, signer or event that does not exist. Never a 500.
``409``
    A well-formed request that conflicts with the state of the record: a status transition the
    machine does not authorise, a countersignature recorded before the buyer's, a reassignment
    the quote forbids or a party who has already signed, and a room at a stated quota ceiling.

What this workflow reads and does not own
-----------------------------------------

**Quotes and proposal documents.** WF-086 and WF-093 provision those rows. This workflow reads
``wf086_quote`` and ``wf093_document`` as data and never rewrites either, because a signature
must not be able to change the document it is bound to. It exposes routes that create a demo
quote and document so the flow is demonstrable and testable before those tickets land; when
they land they write into the same two collections and this workflow signs what it finds.

**Identity verification.** WF-078 provisions the out-of-band identity proof. This workflow
models the envelope's own one-hour verification step and does not import WF-078's engine,
because a feature must not import a feature.

**The contract.** The data flow's last clause creates a contract on acceptance. The issue
names WF-099 as that consumer, so it is downstream of this ticket.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.quote_acceptance import vocabulary as vocab
from dsr.quote_acceptance.engine import AcceptanceEngine
from dsr.quote_acceptance.errors import (
    AcceptanceRefused,
    EnvelopeNotFound,
    QuotaRefused,
    SignerNotFound,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-095-collect-acceptance-by-e-signature-countersignature",
    "ticket": "WF-095",
    "name": "Collect acceptance by e-signature, countersignature and identity verification",
    "description": (
        "Collect a quote's acceptance as a signature envelope: tick the buyer contacts and "
        "pick the countersigners, verify the buyer's identity against the researched one-hour "
        "window, bind a drawn, typed or uploaded signature to the quote document, advance the "
        "signing status as each party signs, and count one quota usage per envelope rather "
        "than per signature."
    ),
    "nav": [{"id": "wf-095-quote-acceptance", "label": "Quote acceptance"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several workflows
#: already share.
router = APIRouter(prefix="/api/wf-095", tags=["WF-095"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is a domain
    function hardcoding a URL string, which leaves the audit log naming a route the app
    stopped serving. ``tests/test_wf095_http.py`` asserts every source this router can record
    matches a concrete ``(method, path)`` the host mounted.
    """

    return f"{method} {router.prefix}{path}"


def get_engine(store: RecordStore = StoreDep) -> AcceptanceEngine:
    """An :class:`AcceptanceEngine` over the process-wide audited store.

    Per request, for the same reason the rest of this product builds its engine per request:
    the engine holds nothing beyond the store and a clock, so building it here leaves every
    seam overridable in a test instead of hanging a long-lived object off ``app.state``,
    which is a shared file this feature may not edit.
    """

    return AcceptanceEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# All four types are declared in dsr.quote_acceptance.errors and raised by nothing else in
# the product. That is what makes it safe to map them here: the host refuses a second feature
# registering a handler for the same type, and a handler for ValueError would intercept that
# exception across the whole product.


def _acceptance_refused(request: Request, exc: AcceptanceRefused) -> JSONResponse:
    """400 with a field-keyed map, so each message lands next to its input."""

    return JSONResponse(status_code=400, content=exc.to_dict())


def _envelope_not_found(request: Request, exc: EnvelopeNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content=exc.to_dict())


def _signer_not_found(request: Request, exc: SignerNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content=exc.to_dict())


def _quota_refused(request: Request, exc: QuotaRefused) -> JSONResponse:
    """409 naming the month, the ceiling and the way out."""

    return JSONResponse(status_code=409, content=exc.to_dict())


EXCEPTION_HANDLERS = {
    AcceptanceRefused: _acceptance_refused,
    EnvelopeNotFound: _envelope_not_found,
    SignerNotFound: _signer_not_found,
    QuotaRefused: _quota_refused,
}


# --------------------------------------------------------------------------- #
# The board and the research
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(
    room_id: str | None = Query(None), engine: AcceptanceEngine = EngineDep
) -> dict[str, Any]:
    """The headline numbers, read back from the store. Reads only."""

    return engine.summary(room_id)


@router.get("/vocabulary")
def vocabulary(engine: AcceptanceEngine = EngineDep) -> dict[str, Any]:
    """The researched vocabulary, so the page cannot drift from the rules behind it."""

    return engine.vocabulary()


@router.get("/decisions")
def decisions(engine: AcceptanceEngine = EngineDep) -> dict[str, Any]:
    """Every judgement call this workflow made, with the alternative it rejected."""

    return {"count": len(engine.decisions()), "decisions": engine.decisions()}


# --------------------------------------------------------------------------- #
# The quote and the document this workflow signs
# --------------------------------------------------------------------------- #
#
# WF-086 and WF-093 provision both. These two routes exist so the flow is demonstrable and
# testable before those tickets land, and so a signature has something to be bound to. They
# write into the same two collections the real workflows write into, and once those land they
# are the only writers.


@router.post("/rooms/{room_id}/quotes", status_code=201)
def create_quote(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: AcceptanceEngine = EngineDep,
) -> dict[str, Any]:
    """A quote to collect acceptance on. Provisioned by WF-086 in production."""

    record = engine.store.create(
        vocab.QUOTES_COLLECTION,
        dict(payload),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/rooms/{room_id}/quotes"),
    )
    return {
        "id": record["id"],
        "room_id": room_id,
        "revision": record["revision"],
        **record["data"],
    }


@router.post("/rooms/{room_id}/documents", status_code=201)
def create_document(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: AcceptanceEngine = EngineDep,
) -> dict[str, Any]:
    """A proposal document for the signature to bind to. Provisioned by WF-093 in production."""

    record = engine.store.create(
        vocab.DOCUMENTS_COLLECTION,
        dict(payload),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/rooms/{room_id}/documents"),
    )
    return {
        "id": record["id"],
        "room_id": room_id,
        "revision": record["revision"],
        **record["data"],
    }


# --------------------------------------------------------------------------- #
# The envelope
# --------------------------------------------------------------------------- #


@router.post("/envelopes", status_code=201)
def open_envelope(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    is_published: bool = Query(False),
    actor: str | None = Query(None),
    engine: AcceptanceEngine = EngineDep,
) -> dict[str, Any]:
    """Open a signing envelope from a quote's acceptance configuration.

    The research's seller step: "in the sidebar select **E-signature** -> tick the buyer
    contacts under **Buyer contacts required to sign** ... and select **Countersigners** from
    your organisation." The configuration is validated, the document is measured against the
    40 MB cap, the quota is checked, and only then is the envelope written. Quota is consumed
    here for a published quote, because the evidence says usage counts as soon as the option
    is turned on.

    ``document_size_bytes`` is read from the payload so a caller measuring the PDF can have
    the cap enforced at open time rather than discovering it when the buyer tries to sign.
    """

    return engine.open_envelope(
        payload,
        room_id=room_id,
        is_published=is_published,
        document_id=payload.get("document_id"),
        document_size_bytes=payload.get("document_size_bytes"),
        actor=actor,
        source=_source("POST", "/envelopes"),
    )


@router.get("/envelopes")
def list_envelopes(
    room_id: str | None = Query(None), engine: AcceptanceEngine = EngineDep
) -> dict[str, Any]:
    """Every signing envelope, each with its signers and where the signing status stands."""

    rows = engine.envelopes(room_id)
    return {"count": len(rows), "envelopes": rows}


@router.get("/envelopes/{envelope_id}")
def read_envelope(envelope_id: str, engine: AcceptanceEngine = EngineDep) -> dict[str, Any]:
    """One envelope, its signers, and the signing status with the evidence beside it."""

    return engine.envelope(envelope_id)


@router.get("/envelopes/{envelope_id}/events")
def list_events(
    envelope_id: str,
    engine: AcceptanceEngine = EngineDep,
) -> dict[str, Any]:
    """Every signature event on the envelope, and the activity each one wrote."""

    engine.envelope(envelope_id)
    rows = engine.events(envelope_id)
    return {"envelope_id": envelope_id, "count": len(rows), "events": rows}


# --------------------------------------------------------------------------- #
# The buyer's acceptance steps
# --------------------------------------------------------------------------- #


@router.post("/envelopes/{envelope_id}/view")
def mark_viewed(
    envelope_id: str,
    actor: str | None = Query(None),
    engine: AcceptanceEngine = EngineDep,
) -> dict[str, Any]:
    """A buyer opened the quote. Advances pending signature to viewed - pending signature.

    The research's buyer step one: "Buyer: opens the shared link". Viewing writes no activity
    row, because the research names four activities and this is not one of them, but it does
    advance the status, so a viewer is visible on the board.
    """

    return engine.mark_viewed(
        envelope_id, actor=actor, source=_source("POST", "/envelopes/{envelope_id}/view")
    )


@router.post("/envelopes/{envelope_id}/verify")
def request_verification(
    envelope_id: str,
    actor: str | None = Query(None),
    engine: AcceptanceEngine = EngineDep,
) -> dict[str, Any]:
    """Mint the one-hour verification link, on the buyer's *Verify email* click.

    The evidence fixes the window: "Buyers have one hour to complete the signature process
    after clicking Verify email." The window opens now, not when the envelope was sent.
    """

    return engine.request_verification(
        envelope_id, actor=actor, source=_source("POST", "/envelopes/{envelope_id}/verify")
    )


@router.post("/envelopes/{envelope_id}/verify/confirm")
def confirm_verification(
    envelope_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: AcceptanceEngine = EngineDep,
) -> dict[str, Any]:
    """Check a verification token against the envelope's window.

    Returns a pass or a fail, never an exception: the window being closed is an ordinary
    answer. The buyer has to click the link again to reopen it.
    """

    return engine.verify(
        envelope_id,
        payload.get("token"),
        actor=actor,
        source=_source("POST", "/envelopes/{envelope_id}/verify/confirm"),
    )


@router.post("/signers/{signer_id}/sign")
def sign(
    signer_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: AcceptanceEngine = EngineDep,
) -> dict[str, Any]:
    """Record one party's signature and advance the signing status.

    The buyer signs first and moves the status to pending countersignature, and the
    countersigner signs second and moves it to accepted, sealing the envelope. A signature is
    drawn, typed or uploaded, so ``signature_mode`` is one of the three.

    A refused signature is a 200 carrying ``outcome: failed`` with the named reason, because
    the attempt happened and the specification logs failures automatically.
    """

    return engine.sign(
        signer_id,
        signature_mode=payload.get("signature_mode"),
        signature_payload=payload.get("signature_payload"),
        verification_token=payload.get("verification_token"),
        actor=actor,
        source=_source("POST", "/signers/{signer_id}/sign"),
    )


@router.post("/signers/{signer_id}/reassign")
def reassign(
    signer_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: AcceptanceEngine = EngineDep,
) -> dict[str, Any]:
    """Reassign a signer who has not yet signed.

    Refused when the quote has *Quote signer(s) can reassign* off, or when this party has
    already signed. The reassignment appends a *Quote reassigned* activity rather than
    editing the old one, because the activity log is append-only.
    """

    return engine.reassign(
        signer_id, payload, actor=actor, source=_source("POST", "/signers/{signer_id}/reassign")
    )


@router.get("/signers/{signer_id}")
def read_signer(signer_id: str, engine: AcceptanceEngine = EngineDep) -> dict[str, Any]:
    """One signer, with the role label and whether they have verified and signed."""

    return engine.signer(signer_id)


# --------------------------------------------------------------------------- #
# Quota
# --------------------------------------------------------------------------- #


@router.get("/quota")
def quota(month: str | None = Query(None), engine: AcceptanceEngine = EngineDep) -> dict[str, Any]:
    """The month's e-signature usage: how many envelopes were charged, and at what cost.

    The evidence fixes two things and a third it leaves open. Usage "will count toward the
    limit as soon as the e-signature option is turned on for a published quote", a quote with
    three signatures "only counts as one usage", and the research states no ceiling, so this
    reports ``limit: null`` rather than inventing one.
    """

    return engine.quota_usage(month)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Create the states this workflow exists to show, on the first room.

    The states seeded, and why each is here:

    * **an envelope awaiting the buyer**, with verification on and no request yet, so the board
      shows a quote that is genuinely waiting on a buyer rather than a completed one;
    * **an envelope part-signed**, at pending countersignature, so the countersigner's turn is
      visible and the activity log has a *Quote buyer signed* row;
    * **a reassigned signer**, so *Quote reassigned* appears and the flag's effect is shown;
    * **a failed signing attempt**, so *Signing attempt failed* appears, because the evidence
      says failures are logged automatically;
    * **an accepted envelope**, sealed, so the whole flow has a completed row.

    Every state except the first two needs the flow driven to it, so the seed calls the engine
    rather than writing rows directly. That is deliberate: it proves the rules produce these
    states, rather than the seed asserting them.

    The return string is ASCII and is asserted encodable by cp1252 in
    ``tests/test_wf095.py``: the seeder prints it to a Windows console, and one
    RIGHTWARDS ARROW in a recovered feature's return string broke the whole seeder.
    """

    store = RecordStore(db)
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return ""

    room_id = room_ids[0][0]
    now = context["now"]
    source = "wf-095 seed"

    counter_token = {"n": 0}

    def _token() -> str:
        counter_token["n"] += 1
        return f"seed-token-{counter_token['n']}"

    engine = AcceptanceEngine(store, now=lambda: now, token_factory=_token)

    quote = store.create(
        vocab.QUOTES_COLLECTION,
        {"title": "Northwind platform renewal", "company": {"name": "Northwind Logistics"}},
        room_id=room_id,
        actor="dana",
        source=source,
    )
    document = store.create(
        vocab.DOCUMENTS_COLLECTION,
        {"quote_id": quote["id"], "state": "published", "title": "Northwind renewal proposal"},
        room_id=room_id,
        actor="dana",
        source=source,
    )

    # 1. Awaiting the buyer. Verification on, no request yet: the widget is locked.
    waiting = engine.open_envelope(
        {
            "quote_id": quote["id"],
            "document_id": document["id"],
            "buyer_signers": [{"name": "Ada Byron", "email": "ada@northwind.example"}],
            "countersigners": [{"name": "Dana Reyes", "email": "dana@halcyon.example"}],
            "reassign_allowed": True,
            "identity_verification_required": True,
        },
        room_id=room_id,
        is_published=True,
        document_size_bytes=1_800_000,
        actor="dana",
        source=source,
    )

    # 2. Part-signed. A second quote, opened, verified and signed by the buyer only, so the
    #    status stands at pending countersignature and the countersigner still owes a
    #    signature.
    quote_two = store.create(
        vocab.QUOTES_COLLECTION,
        {"title": "Halcyon seat expansion", "company": {"name": "Orbis Freight"}},
        room_id=room_id,
        actor="dana",
        source=source,
    )
    document_two = store.create(
        vocab.DOCUMENTS_COLLECTION,
        {"quote_id": quote_two["id"], "state": "published", "title": "Orbis expansion proposal"},
        room_id=room_id,
        actor="dana",
        source=source,
    )
    part = engine.open_envelope(
        {
            "quote_id": quote_two["id"],
            "document_id": document_two["id"],
            "buyer_signers": [{"name": "Rui Almeida", "email": "rui@orbis.example"}],
            "countersigners": [{"name": "Dana Reyes", "email": "dana@halcyon.example"}],
            "identity_verification_required": True,
        },
        room_id=room_id,
        is_published=True,
        document_size_bytes=900_000,
        actor="dana",
        source=source,
    )
    part_buyer = part["signers"][0]
    engine.mark_viewed(part["id"], actor="rui", source=source)
    link = engine.request_verification(part["id"], actor="rui", source=source)
    engine.verify(part["id"], link["verification_link"], actor="rui", source=source)
    engine.sign(
        part_buyer["id"],
        signature_mode="type",
        signature_payload={"text": "Rui Almeida"},
        verification_token=link["verification_link"],
        actor="rui",
        source=source,
    )

    # 3. Reassigned, then 4. a failed attempt. The third envelope has reassignment on, so the
    #    reassignment is allowed, and the failed attempt is the buyer signing before they
    #    verify.
    reassigned = engine.open_envelope(
        {
            "quote_id": quote["id"],
            "document_id": document["id"],
            "buyer_signers": [{"name": "Ada Byron", "email": "ada@northwind.example"}],
            "reassign_allowed": True,
            "identity_verification_required": True,
        },
        room_id=room_id,
        is_published=False,
        actor="dana",
        source=source,
    )
    engine.reassign(
        reassigned["signers"][0]["id"],
        {"name": "Grace Okonkwo", "email": "grace@northwind.example"},
        actor="ada",
        source=source,
    )
    engine.sign(
        reassigned["signers"][0]["id"],
        signature_mode="draw",
        actor="grace",
        source=source,
    )

    # 5. Accepted. The first envelope is carried all the way to sealed.
    engine.mark_viewed(waiting["id"], actor="ada", source=source)
    first_link = engine.request_verification(waiting["id"], actor="ada", source=source)
    engine.verify(waiting["id"], first_link["verification_link"], actor="ada", source=source)
    engine.sign(
        waiting["signers"][0]["id"],
        signature_mode="upload",
        signature_payload={"filename": "ada-signature.png"},
        verification_token=first_link["verification_link"],
        actor="ada",
        source=source,
    )
    engine.sign(
        waiting["signers"][1]["id"],
        signature_mode="type",
        signature_payload={"text": "Dana Reyes"},
        actor="dana",
        source=source,
    )

    by_status = engine.summary(room_id)["by_status"]
    quota = engine.quota_usage()

    return (
        f"5 signing envelopes: {by_status[vocab.STATUS_ACCEPTED]} accepted, "
        f"{by_status[vocab.STATUS_PENDING_COUNTERSIGNATURE]} pending countersignature, "
        f"{by_status[vocab.STATUS_PENDING_SIGNATURE]} pending signature; "
        "1 reassigned signer, 1 failed signing attempt, 1 sealed copy; "
        f"{quota['used']} quota usage in {quota['month']}"
    )
