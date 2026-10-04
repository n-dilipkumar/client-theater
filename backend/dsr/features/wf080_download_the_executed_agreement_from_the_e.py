"""WF-080: download the executed agreement from the e-vault, webhook-driven.

A build from a researched specification, not a port. There was no source branch. The
specification is ``docs/research/digital-sales-room-workflows/wf/WF-080.md``, quoted in
full in issue 128. The rules live in :mod:`dsr.security_governance.evault_rules` and are
not restated here. This module is the three things a feature contributes and the three
things it must never contribute.

What this module contributes
----------------------------

* The route table, under a prefix this feature owns.
* The mapping from this workflow's own error types to responses, exported as
  :data:`EXCEPTION_HANDLERS` because FastAPI accepts handlers on the app object only
  and this feature may not edit the app.
* The demo rows, as ``seed(db, context)`` rather than as an edit to the shared
  ``backend/seed.py``.

What it must never contribute
-----------------------------

* No shared file. ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``,
  ``dsr/db/audited.py``, ``backend/seed.py``, ``App.jsx``, ``main.jsx``,
  ``lib/api.js``, ``lib/features.js``, ``components/ui.jsx``, ``vite.config.js``.
  Twelve workflow branches each editing those files is why none of the original twelve
  merged.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``. A test enforces it.
* No hand-written ``source=`` string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served the write.
* No edit to ``dsr/security_governance/__init__.py``. That package is shared with WF-073
  and WF-075, and two workflow branches appending to one package initializer collide on
  the same lines for no benefit: Python imports a submodule without the package listing
  it, so the four ``evault_*`` modules here are reachable and the file stays untouched.

The shape the specification pins, and where each half lives
-----------------------------------------------------------

**202 with a ``Retry-After`` header and no body.** :func:`download_protected` returns a
``Response`` whose body is empty when the engine reports back-pressure. The seconds come
from the engine, which reads them from the subscription, so a room can raise the wait
without a code change here.

**401 on a sandbox key for the sealed endpoint.** :func:`retrieve` maps
``SandboxKeyRejected`` to 401 and its body names the plain endpoint, because the
specification says "sandbox-based integration tests must use the plain download
endpoint" and an error a caller cannot act on is an error this module should not send.

**429 mapped to ``throttled``.** :func:`retrieve` maps ``Throttled`` to 429 with the
retry wait as a ``Retry-After`` header. The specification says "429 -> ``throttled``" and
"Do not surface it as a generic failure", so the code is in the body rather than in a
sentence.

**Dedupe on the delivery id.** :func:`receive_event` answers 201 on the first delivery
and 200 on a repeat, with ``outcome: "duplicate"``. A repeat is not a fault and must not
be reported as one: the vendor would read 201 as a state change it did not get.

**Two variants that are not interchangeable.** The two retrieval routes are separate paths
rather than one route with a flag, because "the ``/download-protected`` endpoint always
returns the same digitally sealed PDF file, while ``/download`` allows for watermark
customization" is a statement about two endpoints. One path per endpoint only holds if the
sealed path cannot be made to serve the plain one, so ``/retrieve`` refuses a body naming
``variant: "plain"`` and refuses a ``watermark`` outright, rather than quietly honouring
either.

The statuses here are the researched ones
-----------------------------------------

429, 401 and 202 come from the specification's own evidence. 409 for a document whose
signers have not all finished does not, and is derived: see
``DERIVED_PRE_COMPLETION_REFUSAL`` in :mod:`dsr.security_governance.evault_inferences`.
400 and 404 follow this product's conventions. Nothing here guesses a vendor code the
research did not quote.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.security_governance import (
    evault_inferences,
    evault_rules as rules,
    evault_vocabulary as vocab,
)
from dsr.security_governance.evault_engine import HONESTY, EvaultEngine
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-080-download-the-executed-agreement-from-the-e-vault",
    "ticket": "WF-080",
    "name": "Download the executed agreement from the e-vault",
    "description": (
        "Subscribe to the PDF-ready event rather than polling a status, take one webhook "
        "delivery once even when the vendor retries it, and fetch the digitally sealed "
        "PDF from the e-vault. A 202 with Retry-After and no body is a normal outcome, the "
        "sealed endpoint answers production keys only, and the sealed bytes are "
        "byte-stable while the plain endpoint is where a watermark goes."
    ),
    "nav": [{"id": "wf-080-evault-download", "label": "E-vault download"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several workflows
#: already share (``/api/library``, ``/api/publishing``, ``/api/access``). The issue quotes
#: ``/api/wf080``; every other feature in this product uses the hyphenated form derived
#: from its ticket, and consistency with seventy neighbours beats matching one issue line.
router = APIRouter(prefix="/api/wf-080", tags=["WF-080"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is a domain
    function hardcoding a URL string, which leaves the audit log naming a route the app
    stopped serving. ``tests/test_wf080_http.py`` asserts every source this router can
    record matches a concrete ``(method, path)`` the host mounted.
    """

    return f"{method} {router.prefix}{path}"


def get_engine(store: RecordStore = StoreDep) -> EvaultEngine:
    """An :class:`EvaultEngine` over the process-wide audited store.

    Per request, for the same reason the rest of this product builds its engine per
    request: the engine holds nothing beyond the store and a clock, so building it here
    leaves both overridable in a test instead of hanging a long-lived object off
    ``app.state``, which is a shared file this feature may not edit.
    """

    return EvaultEngine(store)


EngineDep = Depends(get_engine)


def _honesty() -> dict[str, Any]:
    """The four fields every response carries.

    A copy of :data:`dsr.security_governance.evault_engine.HONESTY`, not a second
    definition. The engine already puts the four fields into every projection it returns,
    so the routes below spread the same dict rather than restating the sentences: a caveat
    written twice can drift, and a caveat that appears in four responses out of five is a
    caveat a reader learns to skip.
    """

    return dict(HONESTY)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# All seven types are declared in dsr.security_governance.evault_rules and raised by nothing
# else in the product. That is what makes it safe to map them here: the host refuses a
# second feature registering a handler for the same type, and a handler for ValueError or
# LookupError would intercept those exceptions across the whole application.


def _subscription_invalid(request: Request, exc: rules.SubscriptionInvalid) -> JSONResponse:
    """400 with a field-keyed map, so each message lands next to its input."""
    return JSONResponse(
        status_code=400,
        content={
            "error": "subscription_invalid",
            "detail": str(exc),
            "errors": exc.errors,
            **_honesty(),
        },
    )


def _document_invalid(request: Request, exc: rules.DocumentInvalid) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "error": "document_invalid",
            "detail": str(exc),
            "errors": exc.errors,
            **_honesty(),
        },
    )


def _subscription_not_found(request: Request, exc: rules.SubscriptionNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such webhook subscription.", **_honesty()},
    )


def _document_not_found(request: Request, exc: rules.DocumentNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such executed document.", **_honesty()},
    )


def _artifact_not_found(request: Request, exc: rules.ArtifactNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such artifact.", **_honesty()},
    )


def _not_completed(request: Request, exc: rules.NotCompleted) -> JSONResponse:
    """409, and never a 202.

    Back-pressure means work in progress. A document whose signers have not all finished
    has no PDF being produced, so a ``Retry-After`` here would tell a client to retry for
    as long as the buyer takes to sign. The code names the state so the caller does not
    have to infer it, and the retry wait is not sent because there is nothing to wait for.
    """

    return JSONResponse(
        status_code=exc.status,
        content={
            "error": exc.code,
            "detail": str(exc),
            "state": exc.state,
            "retryable": False,
            **_honesty(),
        },
    )


def _sandbox_rejected(request: Request, exc: rules.SandboxKeyRejected) -> JSONResponse:
    """401, because that is the status the vendor documents for a sandbox key.

    The body names the endpoint that does work in a sandbox, which is the actionable half
    of the answer: "sandbox-based integration tests must use the plain download endpoint".
    """

    return JSONResponse(
        status_code=exc.status,
        content={
            "error": exc.code,
            "detail": str(exc),
            "remedy": vocab.SANDBOX_REMEDY,
            "use_instead": vocab.PLAIN_PATH,
            **_honesty(),
        },
    )


def _throttled(request: Request, exc: rules.Throttled) -> JSONResponse:
    """429 with the code ``throttled``, which is the specification's own wording.

    "Do not surface it as a generic failure" is satisfied by having a named code and a
    wait rather than by having a sentence that says it was too busy.
    """

    return JSONResponse(
        status_code=exc.status,
        content={
            "error": exc.code,
            "detail": str(exc),
            "retry_after": exc.retry_after_seconds,
            "window_seconds": vocab.THROTTLE_WINDOW_SECONDS,
            "limit": vocab.THROTTLE_LIMIT,
            **_honesty(),
        },
        headers={vocab.RETRY_AFTER_HEADER: str(exc.retry_after_seconds)},
    )


EXCEPTION_HANDLERS = {
    rules.SubscriptionInvalid: _subscription_invalid,
    rules.DocumentInvalid: _document_invalid,
    rules.SubscriptionNotFound: _subscription_not_found,
    rules.DocumentNotFound: _document_not_found,
    rules.ArtifactNotFound: _artifact_not_found,
    rules.NotCompleted: _not_completed,
    rules.SandboxKeyRejected: _sandbox_rejected,
    rules.Throttled: _throttled,
}


# --------------------------------------------------------------------------- #
# The board and the research
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(room_id: str | None = Query(None), engine: EvaultEngine = EngineDep) -> dict[str, Any]:
    """The board's headline numbers. Reads only, so it writes no audit row."""
    return {**engine.summary(room_id), **_honesty()}


@router.get("/vocabulary")
def vocabulary(engine: EvaultEngine = EngineDep) -> dict[str, Any]:
    """Every published vocabulary, served as data.

    The one trigger, the dedupe header, the two variants with their endpoints and their
    two booleans, the environments, the four document states, the five fetch outcomes and
    the throttle window. A client renders its variant list from this rather than from a
    list compiled into the page, so the editor can never disagree with the validator about
    what is legal.
    """
    return {**engine.vocabulary(), **_honesty()}


@router.get("/decisions")
def decisions(engine: EvaultEngine = EngineDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The specification names what it left open - the retry seconds, the throttle, the
    payload shape, the shared key - and this workflow derived each one and recorded the
    alternative it rejected.
    """
    return {**engine.inferences(), **_honesty()}


@router.get("/decisions/{decision_id}")
def read_decision(decision_id: str, engine: EvaultEngine = EngineDep) -> Any:
    """One recorded decision, or a 404 naming the ids that do exist."""
    found = evault_inferences.describe_one(decision_id)
    if not found:
        return JSONResponse(
            status_code=404,
            content={
                "error": "not_found",
                "detail": f"No recorded decision called {decision_id}.",
                "known": sorted(evault_inferences.DECISIONS),
                **_honesty(),
            },
        )
    return {**found, **_honesty()}


@router.get("/whoami")
def webhook_contract() -> dict[str, Any]:
    """What the vendor needs to configure, in one place.

    The dedupe header, the path to aim at, the one trigger worth subscribing to, and the
    two download endpoints with the environment each one answers in. Someone configuring a
    webhook needs these and should not have to read them out of this module's source.
    """
    return {
        "dedupe_header": vocab.DEDUPE_HEADER,
        "path_template": _source("POST", "/rooms/{room_id}/events"),
        "triggers": list(vocab.REQUIRED_TRIGGERS),
        "join_key": "vendor_document_id",
        "download_endpoints": {name: path for name, path in vocab.VARIANT_ENDPOINTS.items()},
        "sealed_environment": vocab.SEALED_ENVIRONMENT,
        "back_pressure": {
            "status": vocab.STATUS_ACCEPTED,
            "header": vocab.RETRY_AFTER_HEADER,
            "body": None,
        },
        "throttle": {"status": vocab.STATUS_THROTTLED, "code": vocab.OUTCOME_THROTTLED},
        **_honesty(),
    }


# --------------------------------------------------------------------------- #
# Subscriptions
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/subscriptions")
def list_subscriptions(room_id: str, engine: EvaultEngine = EngineDep) -> dict[str, Any]:
    """Every subscription this room holds, cancelled ones included."""
    rows = engine.subscriptions(room_id)
    return {
        "room_id": room_id,
        "count": len(rows),
        "active": sum(1 for row in rows if row["active"]),
        "subscriptions": rows,
        **_honesty(),
    }


@router.post("/rooms/{room_id}/subscriptions", status_code=201)
def create_subscription(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: EvaultEngine = EngineDep,
) -> dict[str, Any]:
    """Step 1: subscribe to the ready event. Mirrors ``POST /public/v1/webhook-subscriptions``.

    A subscription that does not carry ``document_completed_pdf_ready`` is refused. This
    workflow does not poll, so a room that cannot hear the ready event has no second
    mechanism and the PDF would never be announced.
    """
    return engine.create_subscription(
        room_id,
        payload,
        actor=actor,
        source=_source("POST", "/rooms/{room_id}/subscriptions"),
    )


@router.get("/subscriptions/{subscription_id}")
def read_subscription(subscription_id: str, engine: EvaultEngine = EngineDep) -> dict[str, Any]:
    """One subscription, by this room's record id."""
    return engine.read_subscription(subscription_id)


@router.patch("/subscriptions/{subscription_id}")
def update_subscription(
    subscription_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: EvaultEngine = EngineDep,
) -> dict[str, Any]:
    """Rotate the triggers, the wait, or the active flag. Mirrors ``PATCH``.

    Tri-state on the triggers: the body omits them and they stay, the body supplies them
    and the whole list is replaced, and the body supplies ``null`` and the field is left
    out. The replacement is validated as a whole first, so a patch that would leave the
    subscription unable to hear the ready event is refused with the stored list intact.
    """
    return engine.update_subscription(
        subscription_id,
        payload,
        actor=actor,
        source=_source("PATCH", "/subscriptions/{subscription_id}"),
    )


@router.delete("/subscriptions/{subscription_id}")
def cancel_subscription(
    subscription_id: str,
    actor: str | None = Query(default=None),
    engine: EvaultEngine = EngineDep,
) -> dict[str, Any]:
    """Stop listening, keeping the row readable. Mirrors ``DELETE``.

    Recorded as a cancellation rather than a deletion, so the evidence of what arrived
    under this subscription outlives it. See ``DERIVED_CANCEL_KEEPS_THE_ROW``.
    """
    return engine.cancel_subscription(
        subscription_id,
        actor=actor,
        source=_source("DELETE", "/subscriptions/{subscription_id}"),
    )


# --------------------------------------------------------------------------- #
# Executed documents
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/documents")
def list_documents(
    room_id: str,
    state: str | None = Query(default=None, description="filter by state"),
    engine: EvaultEngine = EngineDep,
) -> dict[str, Any]:
    """Every executed agreement this room is waiting on or holds."""
    rows = engine.documents(room_id)
    if state:
        rows = [row for row in rows if row["state"] == state]
    return {
        "room_id": room_id,
        "count": len(rows),
        "sealed": sum(1 for row in rows if row["state"] == vocab.STATE_SEALED),
        "generating": sum(1 for row in rows if row["state"] == vocab.STATE_GENERATING),
        "documents": rows,
        **_honesty(),
    }


@router.post("/rooms/{room_id}/documents", status_code=201)
def create_document(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: EvaultEngine = EngineDep,
) -> dict[str, Any]:
    """Record the agreement whose executed PDF this room will fetch.

    The vendor document id is required and unique within the room, because it is the join
    key the ready event carries. Two rows claiming one id would make that join ambiguous,
    and the refusal names the row that already holds it.
    """
    return engine.register_document(
        room_id,
        payload,
        actor=actor,
        source=_source("POST", "/rooms/{room_id}/documents"),
    )


@router.get("/rooms/{room_id}/documents/{document_id}")
def read_document(
    room_id: str, document_id: str, engine: EvaultEngine = EngineDep
) -> dict[str, Any]:
    """One agreement with its artifacts, its deliveries and its retrieval history."""
    return engine.read_document(document_id, room_id=room_id)


# --------------------------------------------------------------------------- #
# The webhook
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/events", status_code=201)
async def receive_event(
    room_id: str,
    request: Request,
    response: Response,  # type: ignore[assignment] - FastAPI injects the real object
    actor: str | None = Query(default=None),
    engine: EvaultEngine = EngineDep,
) -> Any:
    """Steps 2 and 3: the vendor fires the ready event, and the room applies it once.

    The body is read from the request rather than declared as a FastAPI model, so a
    notification this build does not recognise is a domain refusal with a code and a
    sentence rather than a framework validation error. Re-serialising the payload would
    also lose which key the document id arrived under, and that key is one of the four
    shapes the join tries.

    The status is the other half of the dedupe. A first delivery is 201. A repeat is
    **200**, not 201, because nothing new happened and 201 would tell the vendor a state
    change it did not get.
    """
    try:
        payload = await _json_object(request)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"error": "bad_json", "detail": str(exc), **_honesty()},
        )

    report = engine.receive_event(
        room_id,
        headers=dict(request.headers),
        payload=payload,
        actor=actor or "vendor",
        source=_source("POST", "/rooms/{room_id}/events"),
    )
    if report.get("outcome") == vocab.OUTCOME_DUPLICATE:
        response.status_code = 200
    return {**report, **_honesty()}


async def _json_object(request: Request) -> dict[str, Any]:
    """The notification body, exactly as it arrived.

    Reads ``request.json()`` and refuses anything that is not an object. A notification
    that is a JSON array or a bare string cannot name a document, and answering with a
    framework validation error would hide that behind a 422 that says nothing about which
    part of the envelope was wrong.
    """

    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001 - any parse failure is one refusal
        raise ValueError(f"the event body is not JSON: {exc}") from None
    if not isinstance(payload, dict):
        raise ValueError("the event body is not a JSON object")
    return payload


@router.get("/rooms/{room_id}/events")
def list_events(
    room_id: str,
    document_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    engine: EvaultEngine = EngineDep,
) -> dict[str, Any]:
    """The webhook history, with each row's delivery count intact.

    Retries are here rather than collapsed away, because "process each webhook
    notification once... even when PandaDoc retries delivery" is only demonstrably true if
    the repeat is visible.
    """
    rows = engine.deliveries(room_id, document_ref=document_id, limit=limit)
    return {
        "room_id": room_id,
        "count": len(rows),
        "retries_deduped": sum(row["retries"] for row in rows),
        "events": rows,
        **_honesty(),
    }


# --------------------------------------------------------------------------- #
# The two download endpoints
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/documents/{document_id}/download-protected")
def download_protected(
    room_id: str,
    document_id: str,
    engine: EvaultEngine = EngineDep,
) -> Response:
    """The vendor's sealed download, mirrored: bytes, or 202 with a ``Retry-After``.

    A read. It writes no attempt row and no audit row, because serving a stored artifact
    changes nothing and a route that wrote on every read would fill the guarantee's own
    log with rows describing nothing having happened. The retrieval is recorded by
    :func:`retrieve`, which is the write.

    The 202 shape is exact and deliberate: the status, a ``Retry-After`` header carrying
    the number of seconds, and **no response body at all**, because the vendor documents
    exactly that and a client written against the vendor has to keep working. See
    ``DERIVED_EMPTY_BODY_ON_202``.
    """

    shaped = engine.serve_protected(document_id, room_id=room_id)
    if shaped["status"] == vocab.STATUS_ACCEPTED:
        return Response(
            status_code=vocab.STATUS_ACCEPTED,
            headers=dict(shaped["headers"]),
        )
    return Response(
        status_code=vocab.STATUS_READY,
        content=shaped["body"],
        media_type=vocab.PDF_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{document_id}.pdf"',
            "X-DSR-Digest": str(shaped.get("digest") or ""),
            vocab.EFFECT_FIELD: vocab.EFFECT,
        },
    )


@router.post("/rooms/{room_id}/documents/{document_id}/retrieve")
def retrieve(
    room_id: str,
    document_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: EvaultEngine = EngineDep,
) -> dict[str, Any]:
    """Call the sealed endpoint and record the attempt. The write half of the pair.

    A write, so it answers JSON and it records a row. The three outcomes a caller has to
    be able to tell apart each get their own answer:

    * **200** with the artifact's length, digest and media type.
    * **202** with the wait, when the PDF is still being produced. The vendor returns no
      body for this, so the wait is in the body here because this route is the room's own
      record and not a mirror; the mirror is
      :func:`download_protected`.
    * **401** or **429**, mapped by the handlers above, with the code the specification
      names.

    A body may carry ``environment`` to stand in for the vendor key's environment. It may not
    carry ``variant``: this route *is* the sealed variant, and reading the variant from the
    body would let a POST here return watermarked bytes from the path documented as the
    sealed write half. A body that names the plain variant is refused rather than ignored,
    because a caller that asked for one endpoint and was served the other has been misled
    quietly, and a quiet misdirection on a sealed artifact is the failure this workflow most
    needs to avoid. A watermark sent here is likewise refused, since the sealed bytes must
    not vary with the caller: see ``DERIVED_ARTIFACT_BYTES``.
    """

    requested = payload.get("variant")
    if requested is not None and rules.coerce_variant(requested) != vocab.VARIANT_SEALED:
        raise rules.DocumentInvalid(
            "This route retrieves the sealed variant. Use retrieve-plain for the plain one.",
            {"variant": "variant is not a field on this route."},
        )
    if payload.get("watermark"):
        raise rules.DocumentInvalid(
            "The sealed variant does not take a watermark. Use retrieve-plain for that.",
            {"watermark": "watermark is not a field on this route."},
        )

    result = engine.retrieve(
        document_id,
        room_id=room_id,
        variant=vocab.VARIANT_SEALED,
        environment=payload.get("environment"),
        actor=actor,
        source=_source("POST", "/rooms/{room_id}/documents/{document_id}/retrieve"),
    )
    if result["outcome"] == vocab.OUTCOME_BACK_PRESSURE:
        return JSONResponse(
            status_code=vocab.STATUS_ACCEPTED,
            content={**result, **_honesty()},
            headers={vocab.RETRY_AFTER_HEADER: str(result["retry_after"])},
        )
    return {**result, **_honesty()}


@router.post("/rooms/{room_id}/documents/{document_id}/retrieve-plain")
def retrieve_plain(
    room_id: str,
    document_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: EvaultEngine = EngineDep,
) -> dict[str, Any]:
    """Call the plain endpoint, where a watermark belongs.

    A separate path rather than a flag on :func:`retrieve`, because the specification
    describes two endpoints and not one endpoint with a mode: "the
    ``/download-protected`` endpoint always returns the same digitally sealed PDF file,
    while ``/download`` allows for watermark customization". One path per endpoint means
    the watermark is a field on exactly the endpoint that accepts one.

    The bytes differ from the sealed ones whenever a watermark is given, and that is the
    trade-off made visible rather than hidden: a watermarked copy is a derived file and
    not the executed agreement.
    """

    body = dict(payload or {})
    result = engine.retrieve(
        document_id,
        room_id=room_id,
        variant=vocab.VARIANT_PLAIN,
        environment=body.get("environment"),
        watermark=body.get("watermark"),
        actor=actor,
        source=_source("POST", "/rooms/{room_id}/documents/{document_id}/retrieve-plain"),
    )
    if result["outcome"] == vocab.OUTCOME_BACK_PRESSURE:
        return JSONResponse(
            status_code=vocab.STATUS_ACCEPTED,
            content={**result, **_honesty()},
            headers={vocab.RETRY_AFTER_HEADER: str(result["retry_after"])},
        )
    return {**result, **_honesty()}


@router.get("/rooms/{room_id}/attempts")
def list_attempts(
    room_id: str,
    document_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    engine: EvaultEngine = EngineDep,
) -> dict[str, Any]:
    """Every recorded retrieval, newest first.

    This is where a page learns what a 202 meant. The vendor returns no body with a 202,
    so the record of the wait is the attempt row, and it outlives the tab that asked.
    """

    rows = engine.attempts(room_id, document_ref=document_id, limit=limit)
    return {
        "room_id": room_id,
        "count": len(rows),
        "by_outcome": _tally(rows),
        "attempts": rows,
        **_honesty(),
    }


@router.get("/rooms/{room_id}/artifacts")
def list_artifacts(
    room_id: str,
    document_id: str | None = Query(default=None),
    engine: EvaultEngine = EngineDep,
) -> dict[str, Any]:
    """Every artifact retrieved in this room, with its digest and its variant."""
    rows = engine.artifacts(room_id, document_ref=document_id)
    return {
        "room_id": room_id,
        "count": len(rows),
        "sealed": sum(1 for row in rows if row["variant"] == vocab.VARIANT_SEALED),
        "watermarked": sum(1 for row in rows if row.get("watermark")),
        "artifacts": rows,
        **_honesty(),
    }


@router.get("/rooms/{room_id}/artifacts/{artifact_id}")
def read_artifact(
    room_id: str, artifact_id: str, engine: EvaultEngine = EngineDep
) -> dict[str, Any]:
    """One stored artifact: its variant, its length, its digest and whether it is sealed.

    The record, not the bytes. ``GET .../download-protected`` is the route that streams
    bytes and answers the vendor's shape; this one answers what a stored copy is, which is
    what an audit reader wants and what a page needs in order to label a copy without
    fetching it. A read, so it writes nothing.
    """
    return engine.read_artifact(artifact_id, room_id=room_id)


def _tally(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        outcome = str(row.get("outcome") or "")
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The feature's own prefix, duplicated here so the demo data's audit sources name the
#: same routes the router serves. A change to the prefix has to be made deliberately in
#: both places, which is the point of writing it twice.
PREFIX = "/api/wf-080"

DEMO_SUBSCRIPTION_SECRET = "northwind-evault-webhook-secret"

DEMO_VENDOR_IDS = {
    "sealed": "pd_doc_northwind_sealed_001",
    "generating": "pd_doc_contoso_generating_002",
    "waiting": "pd_doc_fabrikam_waiting_003",
    "sandbox": "pd_doc_adventure_sandbox_004",
    "throttled": "pd_doc_northwind_throttled_005",
}


def _room(store: RecordStore, name: str, account: str) -> str:
    return store.create("room", {"name": name, "account": account}, actor="dana")["id"]


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Three agreements in three rooms, and the states that are not all successes.

    The rows are produced by running the real :class:`EvaultEngine`, so the demo cannot
    show a shape this workflow would not produce, and seeding never opens a socket. It is
    deliberately mixed, because a demo of only green teaches a reviewer nothing:

    * **room one** - an agreement whose ready event fired **twice**, so the second
      delivery is counted as a retry and applied once. That is the vendor's own rule made
      visible, and it is the rule a reviewer most wants to see demonstrated;
    * **the same room** - a retrieval that got **202 with Retry-After** because the PDF was
      still generating, then one that **succeeded** once the vault had it, so the retry
      loop is shown closing rather than described;
    * **room two** - an agreement still **awaiting signatures**, so the download is refused
      as ``not_completed`` rather than answered with a wait, which is the distinction this
      workflow exists to keep;
    * **room three** - a **sandbox** key calling the sealed endpoint, refused as
      ``sandbox_key_rejected``, then served by the plain endpoint, which is exactly what
      the specification tells a sandbox caller to do;
    * **room one again** - a document driven to the **throttle limit** so the last retrieval
      is refused as ``throttled`` with a wait, and the attempt that caused it is not
      counted against the window, so a client that keeps retrying can recover.

    The seeder hands over ``[(room_id, account), ...]``. It is topped up with rooms this
    seed creates, because the states above are per-room. The return string says how many
    rooms came from the seeder, and every character in it is encodable by cp1252: ASCII
    only, no arrow, no curly quote.
    """
    store = RecordStore(db)
    engine = EvaultEngine(store)
    given: list[str] = [
        entry if isinstance(entry, (tuple, list)) else (entry, "")
        for entry in (context.get("room_ids") or [])
    ]

    names = [
        ("Northwind Traders - e-vault download", "Northwind Traders"),
        ("Contoso Health - awaiting signatures", "Contoso Health"),
        ("Adventure Works - sandbox key", "Adventure Works"),
    ]
    room_ids = [room_id for room_id, _ in given if store.get(room_id) is not None][:3]
    created = 0
    for index in range(3):
        if index < len(room_ids):
            continue
        name, account = names[index]
        room_ids.append(_room(store, name, account))
        created += 1
    sealed_room, waiting_room, sandbox_room = room_ids

    subscription_source = f"POST {PREFIX}/rooms/{{room_id}}/subscriptions"
    document_source = f"POST {PREFIX}/rooms/{{room_id}}/documents"
    event_source = f"POST {PREFIX}/rooms/{{room_id}}/events"
    retrieve_source = f"POST {PREFIX}/rooms/{{room_id}}/documents/{{document_id}}/retrieve"
    plain_source = f"POST {PREFIX}/rooms/{{room_id}}/documents/{{document_id}}/retrieve-plain"

    parts: list[str] = []
    for room_id in room_ids:
        engine.create_subscription(
            room_id,
            {
                "triggers": [vocab.PDF_READY_TRIGGER],
                "environment": vocab.ENVIRONMENT_PRODUCTION,
                "vendor_document_id": f"plan-{room_id}",
                "webhook_secret": DEMO_SUBSCRIPTION_SECRET,
            },
            actor="dana",
            source=subscription_source,
        )

    # -- room one: sealed, with a retry and a closed retry loop -------------- #
    sealed = engine.register_document(
        sealed_room,
        {
            "vendor_document_id": DEMO_VENDOR_IDS["sealed"],
            "subject": "Northwind master services agreement",
        },
        actor="dana",
        source=document_source,
    )
    sealed_id = sealed["id"]
    retry_outcome = "not counted"
    for _attempt in (1, 2):
        report = engine.receive_event(
            sealed_room,
            headers={vocab.DEDUPE_HEADER: "evt_northwind_ready_001"},
            payload={
                "event": vocab.PDF_READY_TRIGGER,
                "data": {"id": DEMO_VENDOR_IDS["sealed"]},
            },
            actor="vendor",
            source=event_source,
        )
        if report.get("outcome") == vocab.OUTCOME_DUPLICATE:
            retry_outcome = vocab.OUTCOME_DUPLICATE
    first_fetch = engine.retrieve(
        sealed_id,
        environment=vocab.ENVIRONMENT_PRODUCTION,
        actor="dana",
        source=retrieve_source,
    )
    loop_outcome = str(first_fetch.get("outcome") or "unknown")
    engine.set_state(
        sealed_id,
        vocab.STATE_SEALED,
        actor="vendor",
        source=event_source,
    )
    sealed_fetch = engine.retrieve(
        sealed_id,
        environment=vocab.ENVIRONMENT_PRODUCTION,
        actor="dana",
        source=retrieve_source,
    )
    if sealed_fetch.get("outcome") == vocab.OUTCOME_RETRIEVED:
        loop_outcome = f"{loop_outcome} then {vocab.OUTCOME_RETRIEVED}"
    plain_fetch = engine.retrieve(
        sealed_id,
        variant=vocab.VARIANT_PLAIN,
        environment=vocab.ENVIRONMENT_PRODUCTION,
        watermark="NORTHWIND CONFIDENTIAL",
        actor="dana",
        source=plain_source,
    )

    # -- room two: still awaiting signatures, so the download is refused ---- #
    waiting = engine.register_document(
        waiting_room,
        {
            "vendor_document_id": DEMO_VENDOR_IDS["waiting"],
            "subject": "Contoso order form",
        },
        actor="sam",
        source=document_source,
    )
    refused = "not refused"
    try:
        engine.retrieve(
            waiting["id"],
            environment=vocab.ENVIRONMENT_PRODUCTION,
            actor="sam",
            source=retrieve_source,
        )
    except rules.NotCompleted as exc:
        refused = exc.code

    # -- room three: a sandbox key, refused then redirected --------------- #
    sandbox = engine.register_document(
        sandbox_room,
        {
            "vendor_document_id": DEMO_VENDOR_IDS["sandbox"],
            "subject": "Adventure Works pilot order form",
            "environment": vocab.ENVIRONMENT_SANDBOX,
            "state": vocab.STATE_SEALED,
        },
        actor="sam",
        source=document_source,
    )
    sandbox_refusal = "not refused"
    try:
        engine.retrieve(
            sandbox["id"],
            environment=vocab.ENVIRONMENT_SANDBOX,
            actor="sam",
            source=retrieve_source,
        )
    except rules.SandboxKeyRejected as exc:
        sandbox_refusal = exc.code
    sandbox_plain = engine.retrieve(
        sandbox["id"],
        variant=vocab.VARIANT_PLAIN,
        environment=vocab.ENVIRONMENT_SANDBOX,
        actor="sam",
        source=plain_source,
    )

    # -- room one again: driven to the throttle limit --------------------- #
    throttled = engine.register_document(
        sealed_room,
        {
            "vendor_document_id": DEMO_VENDOR_IDS["throttled"],
            "subject": "Northwind data processing addendum",
            "state": vocab.STATE_SEALED,
        },
        actor="dana",
        source=document_source,
    )
    throttle_refusal = "not throttled"
    for _ in range(vocab.THROTTLE_LIMIT):
        engine.retrieve(
            throttled["id"],
            environment=vocab.ENVIRONMENT_PRODUCTION,
            actor="dana",
            source=retrieve_source,
        )
    try:
        engine.retrieve(
            throttled["id"],
            environment=vocab.ENVIRONMENT_PRODUCTION,
            actor="dana",
            source=retrieve_source,
        )
    except rules.Throttled as exc:
        throttle_refusal = (
            f"{exc.code} after {vocab.THROTTLE_LIMIT} in {vocab.THROTTLE_WINDOW_SECONDS}s"
        )

    summary = engine.summary(sealed_room)
    sealed_artifact = sealed_fetch.get("artifact") or {}
    plain_artifact = plain_fetch.get("artifact") or {}
    sandbox_artifact = sandbox_plain.get("artifact") or {}

    parts.append(
        f"room one: {summary['documents']} agreement(s), {summary['deliveries']} delivery "
        f"whose retry was recorded as {retry_outcome}, {summary['attempts']} retrieval(s) "
        f"across {len(summary['attempts_by_outcome'])} outcome(s) with the retry loop read "
        f"{loop_outcome}, sealed digest {str(sealed_artifact.get('sha256'))[:12]} over "
        f"{sealed_artifact.get('byte_length')} byte(s)"
    )
    parts.append(
        f"a plain copy with a watermark is {plain_artifact.get('byte_length')} byte(s) against "
        f"the sealed {sealed_artifact.get('byte_length')} and is not the sealed artifact"
    )
    parts.append(
        f"room two: an agreement awaiting signatures is refused as {refused} rather than "
        f"answered with a wait"
    )
    parts.append(
        f"room three: a sandbox key is refused as {sandbox_refusal} and the plain endpoint "
        f"returns {sandbox_artifact.get('byte_length')} byte(s)"
    )
    parts.append(
        f"room one again: retrieval past the limit is refused as {throttle_refusal}, and the "
        f"refused attempt is not counted against the window"
    )
    if created:
        parts.append(f"{created} room(s) created for the per-room states")
    parts.append(f"{len(given)} room(s) from the seeder")
    if not summary["sealed_documents"]:
        parts.append("NOT SEALED")
    if not summary["retries_deduped"]:
        parts.append("NO RETRY RECORDED")
    if retry_outcome != vocab.OUTCOME_DUPLICATE:
        parts.append("RETRY NOT DEDUPED")
    return "; ".join(parts)
