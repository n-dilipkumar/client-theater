"""WF-078: require recipient identity verification before open or sign.

A build from a researched specification, not a port. The specification is
``docs/research/digital-sales-room-workflows/wf/WF-078.md``, quoted in full in issue 172.
The rules live in :mod:`dsr.identity_verification` and are not restated here. This module
is the three things a feature contributes and the three things it must never contribute.

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
* No hand-written ``source=`` string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served the write.

The one place this module reads another workflow
------------------------------------------------

:func:`get_engine` passes ``dsr.audit_export.vocabulary.verification_code`` into the
engine as its ``code_for`` seam. That is the whole of the coupling and it is deliberate.

The specification names the integer codes - "``47`` recipient verification with kba
passed", "``51`` recipient verification with kba failed", "``69`` recipient verification
with email otp passed", "``70`` recipient verification with email otp failed" - and this
repository already owns the whole enum in ``dsr.audit_export``. Two tables describing the
same integers would drift the first time a team reordered a method, so this feature
carries the *facts* of an attempt and lets that table turn them into a code. The engine
takes the callable rather than importing it, so the domain package has no dependency on
another workflow's package and stays testable on its own.

Ownership of the setting
------------------------

The specification names the collision and instructs: "Decide which workflow owns the
setting and which reads it, and record the decision, so one gate is not implemented
twice." Jev was asked, and chose this workflow owning the gate over three alternatives at
confidence 0.93, audit ``jev-20261004T140557-22596-57651``. The reasoning is recorded in
:mod:`dsr.identity_verification.inferences` under ``OWNERSHIP_WF078_OWNS_THE_GATE`` and
served at ``GET /api/wf-078/decisions``.

The status codes here are this product's own
--------------------------------------------

The specification documents no HTTP status codes, so every status below follows the
product's conventions rather than inventing a codebook: 400 for a setting this workflow
will not accept, 404 for a document or recipient that does not exist, 403 for a body that
a gate is withholding, and 422 for an attempt that names a gate this workflow does not
recognise. The 403 and the 422 are different on purpose: a withheld body is a correct
answer to a well-formed request, and an unknown gate is a request this build cannot
interpret.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.audit_export.vocabulary import verification_code
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.identity_verification import inferences, rules, vocabulary as vocab
from dsr.identity_verification.engine import IdentityVerificationEngine
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-078-require-recipient-identity-verification",
    "ticket": "WF-078",
    "name": "Require recipient identity verification before open or sign",
    "description": (
        "Put a verification on a recipient and choose the moment it is demanded: before "
        "the recipient can view the document, or before they can sign it. Four methods are "
        "supported, as a discriminated union: a typed passcode, an SMS one-time password, "
        "knowledge-based questions and a government-issued ID check. The document body is "
        "withheld until the check clears, and every attempt - passed or failed - is "
        "written to the audit trail."
    ),
    "nav": [{"id": "wf-078-recipient-verification", "label": "Recipient verification"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several workflows
#: already share (``/api/library``, ``/api/publishing``, ``/api/access``).
router = APIRouter(prefix="/api/wf-078", tags=["WF-078"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is a
    domain function hardcoding a URL string, which leaves the audit log naming a route
    the app stopped serving. ``tests/test_wf078_http.py`` asserts every source this
    router can record matches a concrete ``(method, path)`` the host mounted.
    """

    return f"{method} {router.prefix}{path}"


def get_engine(store: RecordStore = StoreDep) -> IdentityVerificationEngine:
    """An :class:`IdentityVerificationEngine` over the process-wide audited store.

    Per request, for the same reason the rest of this product builds its engine per
    request: the engine holds nothing beyond the store and a clock, so building it here
    leaves both overridable in a test instead of hanging a long-lived object off
    ``app.state``, which is a shared file this feature may not edit.
    """

    return IdentityVerificationEngine(store, code_for=verification_code)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# All four types are declared in dsr.identity_verification.rules and raised by nothing
# else in the product. That is what makes it safe to map them here: the host refuses a
# second feature registering a handler for the same type, and a handler for ValueError or
# PermissionError would intercept those exceptions across the whole product.


def _settings_invalid(request: Request, exc: rules.VerificationSettingsInvalid) -> JSONResponse:
    """400 with a field-keyed map, so each message lands next to its input."""

    return JSONResponse(
        status_code=400,
        content={
            "error": "verification_settings_invalid",
            "detail": str(exc),
            "errors": exc.errors,
        },
    )


def _recipient_not_found(request: Request, exc: rules.RecipientNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such verified recipient."},
    )


def _document_not_found(request: Request, exc: rules.DocumentNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such verified document."},
    )


def _gate_not_cleared(request: Request, exc: rules.GateNotCleared) -> JSONResponse:
    """403, naming the gate that is outstanding.

    403 and not 404, because the document exists and the recipient may have it: what is
    missing is a cleared check, not a row. The body names the place and the method so a
    recipient screen can say what it wants rather than showing a refusal with no reason,
    and it repeats the limitation so a refusal cannot be read as a broken document.
    """

    return JSONResponse(
        status_code=403,
        content={
            "error": "document_body_withheld",
            "detail": str(exc),
            "place": exc.place,
            "method": exc.method,
            "withheld_reason": exc.reason,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
            vocab.NOT_PROOF_FIELD: vocab.NOT_PROOF,
        },
    )


EXCEPTION_HANDLERS = {
    rules.VerificationSettingsInvalid: _settings_invalid,
    rules.RecipientNotFound: _recipient_not_found,
    rules.DocumentNotFound: _document_not_found,
    rules.GateNotCleared: _gate_not_cleared,
}


# --------------------------------------------------------------------------- #
# The board and the research
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(
    room_id: str | None = Query(None), engine: IdentityVerificationEngine = EngineDep
) -> dict[str, Any]:
    """The board's headline numbers. Reads only."""

    return engine.summary(room_id)


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every researched term this workflow validates against.

    Served rather than duplicated in the frontend so the recipient-settings panel cannot
    drift from the rules that validate it: the two gates with their sourced audiences, the
    four methods with the vendor's own field names, the passcode bound, the E.164 bound,
    the SMS role axis and the three honesty sentences all come from the same tables the
    validator reads.
    """

    return {
        **vocab.vocabulary_payload(),
        "decisions": {"count": inferences.count(), "ids": list(inferences.DECISIONS)},
        "assumptions_recorded": [item["id"] for item in inferences.assumptions()],
    }


@router.get("/decisions")
def list_decisions() -> dict[str, Any]:
    """Every judgement call this workflow made, with what it rejected.

    The specification says an implementer "must derive it and record the derivation, not
    assume it". This route is that record, and it is served rather than buried in a
    docstring so a reviewer reads the decision instead of the code.
    """

    return {"count": inferences.count(), "decisions": inferences.describe()}


@router.get("/assumptions")
def list_assumptions() -> dict[str, Any]:
    """Only the three the specification itself marked inferred.

    Split out because a reader asking "what in this is assumed rather than sourced?"
    should not have to read every entry to find out, and because the answer is the one a
    compliance reader needs first.
    """

    rows = inferences.assumptions()
    return {"count": len(rows), "assumptions": rows, vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION}


@router.get("/decisions/{decision_id}")
def read_decision(decision_id: str) -> dict[str, Any]:
    """One judgement call by id, or a 404."""

    decision = inferences.describe_one(decision_id)
    if not decision:
        raise HTTPException(status_code=404, detail="No such recorded decision.")
    return decision


# --------------------------------------------------------------------------- #
# Documents and their recipients
# --------------------------------------------------------------------------- #


@router.get("/documents")
def list_documents(
    room_id: str | None = Query(None), engine: IdentityVerificationEngine = EngineDep
) -> dict[str, Any]:
    """Every document this workflow governs."""

    rows = engine.documents(room_id)
    return {"count": len(rows), "documents": rows, vocab.LIMITATION_FIELD: vocab.LIMITATION}


@router.post("/rooms/{room_id}/documents", status_code=201)
def create_document(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: IdentityVerificationEngine = EngineDep,
) -> dict[str, Any]:
    """Create a document with its recipients. Mirrors ``POST /public/v1/documents``.

    The specification's first user-flow step: "Sender sets ``verification_settings`` on a
    recipient - either at document creation (Create Document) or on a live document
    (Update Recipient)." This is the first half, and the recipients named in the same body
    are created here, so the whole of that step is one call.
    """

    return engine.create_document(
        room_id, payload, actor=actor, source=_source("POST", "/rooms/{room_id}/documents")
    )


@router.get("/documents/{document_id}")
def read_document(
    document_id: str, engine: IdentityVerificationEngine = EngineDep
) -> dict[str, Any]:
    """One document, its recipients, and where each one's gates stand.

    Never returns the body. The body comes from ``GET /documents/{id}/body``, which is
    the one route that runs the gate, so a caller cannot read a document's contents by
    reading its metadata.
    """

    return engine.read_document(document_id)


@router.get("/documents/{document_id}/recipients")
def list_document_recipients(
    document_id: str, engine: IdentityVerificationEngine = EngineDep
) -> dict[str, Any]:
    """Every recipient on one document."""

    engine.read_document(document_id)
    rows = engine.recipients(document_id)
    return {
        "document_id": document_id,
        "count": len(rows),
        "recipients": rows,
        vocab.LIMITATION_FIELD: vocab.LIMITATION,
        vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
    }


@router.post("/documents/{document_id}/recipients", status_code=201)
def create_recipient(
    document_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: IdentityVerificationEngine = EngineDep,
) -> dict[str, Any]:
    """Add a recipient to a document, with the gates it carries."""

    room_id = engine.read_document(document_id)["room_id"]
    return engine.create_recipient(
        room_id,
        document_id,
        payload,
        actor=actor,
        source=_source("POST", "/documents/{document_id}/recipients"),
    )


# --------------------------------------------------------------------------- #
# The recipient's own surface
# --------------------------------------------------------------------------- #


@router.get("/recipients")
def list_recipients(
    room_id: str | None = Query(None),
    document_id: str | None = Query(None),
    engine: IdentityVerificationEngine = EngineDep,
) -> dict[str, Any]:
    """Every recipient, optionally narrowed to a room or a document."""

    rows = engine.recipients(document_id)
    if room_id:
        rows = [row for row in rows if row.get("room_id") == room_id]
    return {
        "count": len(rows),
        "recipients": rows,
        vocab.LIMITATION_FIELD: vocab.LIMITATION,
        vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
    }


@router.get("/recipients/{recipient_id}")
def read_recipient(
    recipient_id: str, engine: IdentityVerificationEngine = EngineDep
) -> dict[str, Any]:
    """One recipient, its gates, and its most recent attempt per gate.

    The stored passcode and phone number are not in this response. A settings page is the
    one place a secret is most likely to leak from - the most readers and the least reason
    to have them - so the response reports which gates exist and which method each one
    uses, and the value stays behind the prompt route.
    """

    return engine.read_recipient(recipient_id)


@router.patch("/recipients/{recipient_id}")
def update_recipient(
    recipient_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: IdentityVerificationEngine = EngineDep,
) -> dict[str, Any]:
    """Add, change or remove a recipient's verification. Mirrors ``PATCH`` Update Recipient.

    Tri-state per gate, so one endpoint can both install a gate and take one off: a gate
    the body omits is left alone, a gate the body sets replaces what was stored for that
    gate, and a gate the body sets to ``null`` removes it. A gate the body omits is why one
    recipient can gain a second gate at a different moment without the first one moving.

    A role change is honoured and re-checked against the stored gates, so a recipient
    moved from signer to recipient while carrying a ``before_sign`` gate is refused rather
    than locked out of a document they may still view.
    """

    return engine.update_recipient(
        recipient_id, payload, actor=actor, source=_source("PATCH", "/recipients/{recipient_id}")
    )


@router.get("/recipients/{recipient_id}/prompt")
def read_prompt(
    recipient_id: str,
    place: str = Query(vocab.BEFORE_OPEN),
    engine: IdentityVerificationEngine = EngineDep,
) -> dict[str, Any]:
    """What the recipient is asked for at one gate, and never the answer.

    The questions come back for a knowledge-based gate, because a recipient cannot answer
    a question they cannot see. The expected answers do not, for any method, and neither
    does a passcode or a one-time code.
    """

    resolved = rules.normalise_place(place)
    if resolved is None:
        raise HTTPException(status_code=422, detail=f"Unknown verification place: {place}")
    return engine.prompt_for(recipient_id, resolved)


@router.post("/recipients/{recipient_id}/codes", status_code=201)
def send_code(
    recipient_id: str,
    actor: str | None = Query(None),
    engine: IdentityVerificationEngine = EngineDep,
) -> dict[str, Any]:
    """Issue a one-time code for an SMS gate.

    The evidence describes this from the sender's side: "They can then select the 'Send
    code' button to receive a 6-digit code via text message", and they "can request the
    code to be sent again if needed." So a resend is allowed and issuing a code supersedes
    the previous one, which is the part that matters: at most one code is ever valid.

    The code is not in the response. A send endpoint that handed the code back would have
    turned the gate into a suggestion.
    """

    return engine.send_code(
        recipient_id, actor=actor, source=_source("POST", "/recipients/{recipient_id}/codes")
    )


# --------------------------------------------------------------------------- #
# Attempts
# --------------------------------------------------------------------------- #


@router.post("/recipients/{recipient_id}/attempts", status_code=201)
def make_attempt(
    recipient_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    place: str | None = Query(None),
    actor: str | None = Query(None),
    engine: IdentityVerificationEngine = EngineDep,
) -> dict[str, Any]:
    """Run one verification attempt and write it, whichever way it went.

    The specification's fifth user-flow step: "Every attempt - pass or fail - is written
    to the document's audit trail." A failure is a normal 201 with ``outcome: "fail"`` and
    a named reason, not a 4xx: the attempt happened, and an attempt that was rejected is
    exactly the row the specification requires to be visible.

    The check re-runs from the recipient's stored settings on every call. Nothing is read
    from a previous attempt, so the second attempt on a document is answered by the
    evidence in front of it and not by the first attempt's result.
    """

    body = dict(payload or {})
    resolved = rules.normalise_place(place or body.get("verification_place") or vocab.BEFORE_OPEN)
    if resolved is None:
        raise HTTPException(status_code=422, detail="Unknown verification place.")
    evidence = body.get("evidence") if isinstance(body.get("evidence"), dict) else body
    return engine.attempt(
        recipient_id,
        resolved,
        evidence,
        actor=actor,
        source=_source("POST", "/recipients/{recipient_id}/attempts"),
    )


@router.get("/recipients/{recipient_id}/attempts")
def list_recipient_attempts(
    recipient_id: str, engine: IdentityVerificationEngine = EngineDep
) -> dict[str, Any]:
    """Every attempt on one recipient, newest first."""

    engine.read_recipient(recipient_id)
    rows = engine.attempts(recipient_id=recipient_id)
    return {
        "recipient_id": recipient_id,
        "count": len(rows),
        "attempts": rows,
        vocab.LIMITATION_FIELD: vocab.LIMITATION,
        vocab.NOT_PROOF_FIELD: vocab.NOT_PROOF,
    }


@router.get("/attempts")
def list_attempts(
    room_id: str | None = Query(None),
    recipient_id: str | None = Query(None),
    document_id: str | None = Query(None),
    engine: IdentityVerificationEngine = EngineDep,
) -> dict[str, Any]:
    """Every attempt, optionally narrowed. The read side of the audit trail.

    Mirrors ``GET /public/v2/documents/{document_id}/audit-trail``. Each row carries the
    integer action code its outcome resolved to, so a compliance review filters on a
    number rather than on a word somebody can reword.
    """

    rows = engine.attempts(room_id, recipient_id, document_id)
    return {
        "count": len(rows),
        "attempts": rows,
        "code_table_owner": vocab.CODE_TABLE_OWNER,
        vocab.LIMITATION_FIELD: vocab.LIMITATION,
        vocab.NOT_PROOF_FIELD: vocab.NOT_PROOF,
    }


# --------------------------------------------------------------------------- #
# The withheld body
# --------------------------------------------------------------------------- #


@router.get("/documents/{document_id}/body")
def read_body(
    document_id: str,
    recipient_id: str = Query(...),
    place: str = Query(vocab.BEFORE_OPEN),
    engine: IdentityVerificationEngine = EngineDep,
) -> dict[str, Any]:
    """The document's body, once the gate at ``place`` has cleared.

    The specification's data flow, in one clause: "the document body is withheld until it
    clears". This is the only route in the workflow that returns document content, and it
    resolves the gate first.

    The gate is resolved from the **latest** attempt, never from "has this recipient ever
    passed". The specification's automation section is explicit that verification "is
    re-asserted by the gate on every attempt; there is no 'verify once, remember forever'
    behaviour", so a pass stamps what happened rather than granting a standing permission,
    and a later failure withholds the body again.
    """

    resolved = rules.normalise_place(place)
    if resolved is None:
        raise HTTPException(status_code=422, detail=f"Unknown verification place: {place}")
    return engine.read_body(document_id, recipient_id, place=resolved)


@router.get("/documents/{document_id}/body-state")
def read_body_state(
    document_id: str,
    recipient_id: str = Query(...),
    place: str = Query(vocab.BEFORE_OPEN),
    engine: IdentityVerificationEngine = EngineDep,
) -> dict[str, Any]:
    """Whether the body would be released right now, without delivering it.

    A page needs to say "this document is withheld until you verify" without handing over
    the document to say it, and a route that answered this question by returning the body
    would have defeated the gate to render a label.
    """

    resolved = rules.normalise_place(place)
    if resolved is None:
        raise HTTPException(status_code=422, detail=f"Unknown verification place: {place}")
    return engine.body_state(document_id, recipient_id, place=resolved)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the states the specification says matter, not just the happy path.

    Every row is produced by calling the real :class:`IdentityVerificationEngine`, so the
    demo cannot show a shape, an audit row or an attempt the HTTP routes would not
    produce. ``source="seed"`` rather than a route string: no route served this, and
    claiming one would be the lie hard rule 4 of the brief exists to prevent.

    The states seeded, and why each is here:

    * a document with a **passcode gate before open**, and an attempt that **passed**, so
      the released body is a state a reviewer can see rather than a claim;
    * a document with a **knowledge-based gate before open** and an attempt that **failed**
      on one of its answers, because the specification requires a rejected attempt to be
      "as visible as a successful one" and a demo holding only passes would misrepresent
      the control;
    * a **signer carrying two gates at two moments** - a passcode before open and a
      knowledge-based check before sign - because the specification's extensibility note
      says "the same recipient object can be verified differently for viewing and
      signing", and that sentence is only visible as one recipient holding two settings;
    * a **delivery-only SMS** recipient, because the specification separates
      authentication from delivery and a demo with only authenticating numbers would hide
      the difference;
    * a **code issued then re-issued** for that SMS gate, so the supersede is a state
      rather than a claim;
    * an **ungated recipient**, because most recipients in a room carry no verification
      and a demo holding only gated ones would make the control look universal.

    The return string is ASCII and is asserted encodable by cp1252 in
    ``tests/test_wf078.py``: the seeder prints it to a Windows console, and one
    RIGHTWARDS ARROW in a recovered feature's return string broke the whole seeder.
    """

    store = RecordStore(db)
    now = context["now"]
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return ""

    room_id = room_ids[0][0]
    room_two = room_ids[1][0] if len(room_ids) > 1 else room_id

    engine = IdentityVerificationEngine(store, now=lambda: now)

    # 1. Passcode before open, and an attempt that passed.
    opened = engine.create_document(
        room_id,
        {
            "title": "Northwind - master services agreement",
            "status": "sent",
            "body": "This master services agreement runs for twenty four months from the "
            "effective date. The supplier delivers the platform in three milestones.",
            "recipients": [
                {
                    "email": "ops@northwind.example",
                    "name": "Dana Reed",
                    "role": vocab.ROLE_RECIPIENT,
                    "verification_settings": {
                        vocab.BEFORE_OPEN: {
                            "method": vocab.METHOD_PASSCODE,
                            "passcode": "Deal2026",
                        }
                    },
                }
            ],
        },
        actor="dana",
        source="seed",
    )
    first_recipient = opened["recipients"][0]
    engine.attempt(
        first_recipient["id"],
        vocab.BEFORE_OPEN,
        {"passcode": "Deal2026"},
        actor="dana",
        source="seed",
    )

    # 2. Knowledge-based before open, and an attempt that failed on one answer.
    answered = engine.create_document(
        room_two,
        {
            "title": "Halcyon - data processing addendum",
            "status": "sent",
            "body": "This addendum sets out how personal data is processed on behalf of "
            "the controller named in the agreement.",
            "recipients": [
                {
                    "email": "legal@halcyon.example",
                    "name": "Priya Raman",
                    "role": vocab.ROLE_RECIPIENT,
                    "verification_settings": {
                        vocab.BEFORE_OPEN: {
                            "method": vocab.METHOD_KBA,
                            "questions": [
                                {"prompt": "Registered company number", "answer": "04198233"},
                                {"prompt": "County of registration", "answer": "Greater London"},
                            ],
                        }
                    },
                }
            ],
        },
        actor="dana",
        source="seed",
    )
    second_recipient = answered["recipients"][0]
    engine.attempt(
        second_recipient["id"],
        vocab.BEFORE_OPEN,
        {
            "answers": {
                "Registered company number": "04198233",
                "County of registration": "Kent",
            }
        },
        actor="dana",
        source="seed",
    )

    # 3. One signer, two gates, two moments. This is the extensibility note made visible.
    two_axis = engine.create_document(
        room_two,
        {
            "title": "Vantage - order form with a sign gate",
            "status": "sent",
            "body": "This order form is governed by the master services agreement and "
            "expires at the end of the quarter.",
            "recipients": [
                {
                    "email": "procurement@vantage.example",
                    "name": "Alex Doyle",
                    "role": vocab.ROLE_SIGNER,
                    "verification_settings": {
                        vocab.BEFORE_OPEN: {
                            "method": vocab.METHOD_PASSCODE,
                            "passcode": "Order4471",
                        },
                        vocab.BEFORE_SIGN: {
                            "method": vocab.METHOD_KBA,
                            "questions": [
                                {"prompt": "Purchase order number", "answer": "PO-88214"},
                            ],
                        },
                    },
                }
            ],
        },
        actor="dana",
        source="seed",
    )
    third_recipient = two_axis["recipients"][0]
    engine.attempt(
        third_recipient["id"],
        vocab.BEFORE_OPEN,
        {"passcode": "Order4471"},
        actor="dana",
        source="seed",
    )

    # 4. An SMS recipient whose number is delivery only, with a code issued then reissued.
    #    The reissue supersedes the first code, which is what makes a resend narrow access
    #    rather than widen it.
    texted = engine.create_document(
        room_two,
        {
            "title": "Kestrel - renewal schedule",
            "status": "sent",
            "body": "This schedule renews the subscription for a further twelve months on "
            "the terms set out in the current agreement.",
            "recipients": [
                {
                    "email": "ap@kestrel.example",
                    "name": "Robin Hale",
                    "role": vocab.ROLE_SIGNER,
                    "verification_settings": {
                        vocab.BEFORE_SIGN: {
                            "method": vocab.METHOD_SMS,
                            "phone_number": vocab.PHONE_PLACEHOLDER,
                            vocab.SMS_TYPE_FIELD: vocab.SMS_TYPE_DELIVERY,
                        }
                    },
                }
            ],
        },
        actor="dana",
        source="seed",
    )
    texted_recipient = texted["recipients"][0]
    engine.send_code(texted_recipient["id"], actor="dana", source="seed", code="418204")
    engine.send_code(texted_recipient["id"], actor="dana", source="seed", code="662951")

    # 5. An ungated recipient, so the ordinary case is a state on the board.
    engine.create_recipient(
        room_id,
        opened["id"],
        {"email": "observer@vantage.example", "name": "Sam Ito"},
        actor="dana",
        source="seed",
    )

    # Counts are read back from the engine rather than written out here, so the line the
    # seeder prints cannot describe a state the seed did not produce. The summary is taken
    # with no room filter, which is what makes it a count of everything this seed created
    # across both rooms rather than of one room.
    board = engine.summary()
    attempts = engine.attempts()
    failed = board["attempts_failed"]

    return (
        f"{board['documents']} verified documents, {board['recipients']} recipients "
        f"({board['gated_recipients']} gated, {board['ungated_recipients']} ungated); "
        f"{board['before_open_recipients']} verified before open, "
        f"{board['before_sign_recipients']} before sign, "
        f"{board['two_axis_recipients']} carrying both moments at once; "
        f"{len(attempts)} attempt(s), {board['attempts_passed']} passed and "
        f"{failed} failed - a rejection is written like a pass"
    )
