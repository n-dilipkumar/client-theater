"""WF-069: gate a buyer link with a password, an expiry and email verification.

A build, not a port. There was no source branch for this workflow - only the
researched document at ``docs/research/digital-sales-room-workflows/wf/WF-069.md``,
which is the specification. The rules live in :mod:`dsr.link_gating` and are not
restated here; this module is the three things a feature contributes, and the
three things it must never contribute.

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
  Twelve workflow branches each editing those files is why none of the original
  twelve merged.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``. A test enforces
  it, because importing the app from a feature reintroduces exactly the coupling
  the host removes.
* No hand-written ``source=`` string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served the write.
  The branch history is full of features whose audit log named a path the app had
  stopped serving, and the contract names that defect by name.

A note on secrets in this file
------------------------------

This workflow stores credentials-adjacent state: password hashes, one-time code
hashes, expiries. Every response below goes through
:func:`~dsr.link_gating.secrets.redact`, and no route accepts or returns a
cleartext secret. The password is write-only - it arrives in a request body and is
hashed on the way to the store - and a one-time code never appears in a response
at all, because the buyer receives it by email and the research does not name the
delivery vendor. ``tests/test_wf069.py`` asserts the negative directly: it walks
every response this router can produce and fails if a secret does.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.link_gating import gate as gate_engine, rules
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-069-gate-each-buyer-link-with-a-password-a",
    "ticket": "WF-069",
    "name": "Gate each buyer link with a password, an expiry and email verification",
    "description": (
        "Put a password, an expiry and an email step in front of every buyer link. "
        "Expiry is evaluated on each viewer request, email authentication proves "
        "the inbox with a one-time code, and the verified buyer is persisted so "
        "every later view is attributed."
    ),
    "nav": [{"id": "wf-069-gating", "label": "Link gating"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several
#: workflows already share (``/api/library``, ``/api/publishing``, ``/api/access``
#: on WF-015). Every path below sits under it.
router = APIRouter(prefix="/api/wf-069", tags=["WF-069"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is
    a domain function hardcoding a URL string, which leaves the audit log naming a
    route the app stopped serving. ``tests/test_wf069.py`` asserts every source
    this router can record matches a concrete ``(method, path)`` the host mounted.
    """
    return f"{method} {router.prefix}{path}"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# All three types are declared in dsr.link_gating.rules and raised by nothing else
# in the product. That is what makes it safe to map them here: the host refuses a
# second feature registering a handler for the same type, and a handler for
# ValueError or PermissionError would intercept those exceptions everywhere.


def _gate_error(request: Request, exc: rules.GateError) -> JSONResponse:
    """400 with a field-keyed map, so each message lands next to its input."""
    return JSONResponse(
        status_code=400,
        content={"error": "gate_settings_invalid", "detail": str(exc), "errors": exc.errors},
    )


def _gate_denied(request: Request, exc: rules.GateDenied) -> JSONResponse:
    """403 for a buyer the gate will not let through.

    ``reason`` is a stable token the UI branches on; ``detail`` is the sentence the
    buyer reads, and for an expired or a revoked link the two are worded
    identically so the message does not tell a buyer they were cut off.
    """
    return JSONResponse(
        status_code=403,
        content={"error": "gate_denied", "reason": exc.reason, "detail": str(exc)},
    )


def _link_not_found(request: Request, exc: rules.LinkNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such gated link."},
    )


EXCEPTION_HANDLERS = {
    rules.GateError: _gate_error,
    rules.GateDenied: _gate_denied,
    rules.LinkNotFound: _link_not_found,
}


def _engine(store: RecordStore) -> gate_engine.GateEngine:
    """The engine, with both seams left at their production defaults.

    The clock is the real one here rather than a request-supplied value: a caller
    that could pass its own ``now`` could pass a past one and walk a gate that has
    already closed. Tests inject the clock into the engine directly.
    """
    return gate_engine.GateEngine(store)


def _clean(value: Any) -> Any:
    return gate_engine.redacted(value)


# --------------------------------------------------------------------------- #
# Seller surface
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(room_id: str | None = Query(None), store: RecordStore = StoreDep) -> dict[str, Any]:
    """The board's headline numbers. Reads only."""
    return _clean(_engine(store).summary(room_id))


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """The vocabulary this workflow enforces, so the UI need not hard-code it.

    The preset-covered field list is research, not implementation: it is the
    documented list of what a governed baseline may seed. Serving it from one
    place is what stops the create form and the preset editor disagreeing about
    which fields exist.
    """
    return {
        "steps": list(rules.GATE_SEQUENCE),
        "defaults": {
            "email_protected": rules.DEFAULT_EMAIL_PROTECTED,
            "email_authenticated": rules.DEFAULT_EMAIL_AUTHENTICATED,
            "enable_notification": rules.DEFAULT_ENABLE_NOTIFICATION,
            "expires_at": rules.NEVER_EXPIRES,
        },
        "preset_covered_fields": list(rules.PRESET_COVERED_FIELDS),
        "gated_fields": list(rules.GATED_FIELDS),
        "preset_extras": [
            field for field in rules.PRESET_COVERED_FIELDS if field not in rules.GATED_FIELDS
        ],
        "delivery": {
            "configured": False,
            "detail": (
                "The one-time code is handed to a delivery transport this "
                "deployment supplies. The vendor is not named in the research, so "
                "none is assumed; the dispatch itself is recorded either way."
            ),
        },
        "not_implemented": [
            "No attempt cap on the one-time code and no lifetime on it: the "
            "research specifies neither, and a guessed value locks out real "
            "buyers. The code is single-use, which is what 'one-time' means.",
            "No record of refused attempts. The research records views, not "
            "refusals, and what a refusal counter should count is a privacy "
            "decision nobody has made.",
        ],
    }


@router.post("/presets")
def create_preset(
    payload: dict[str, Any] = Body(...), store: RecordStore = StoreDep
) -> dict[str, Any]:
    """Define a governed baseline every future link can be seeded from."""
    engine = _engine(store)
    return _clean(engine.create_preset(payload, source=_source("POST", "/presets"), actor="rep"))


@router.get("/presets")
def list_presets(store: RecordStore = StoreDep) -> dict[str, Any]:
    return {"presets": _clean(_engine(store).list_presets())}


@router.post("/rooms/{room_id}/links")
def create_link(
    room_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Create a gated link. Mirrors ``POST /v1/links``.

    Room-scoped because that is where the thing being shared lives; the link id
    the response carries is what a buyer follows.
    """
    engine = _engine(store)
    return _clean(
        engine.create_link(
            room_id, payload, source=_source("POST", "/rooms/{room_id}/links"), actor="rep"
        )
    )


@router.get("/rooms/{room_id}/links")
def list_room_links(room_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    engine = _engine(store)
    return {
        "room_id": room_id,
        "links": _clean(engine.list_links(room_id)),
        "revoked": _clean(engine.list_revoked_links(room_id)),
    }


@router.get("/links/{link_id}")
def read_link(link_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    return _clean(_engine(store).read_link(link_id))


@router.patch("/links/{link_id}")
def update_link(
    link_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Rotate the password or move the expiry in place.

    Tri-state: a field the body omits is left alone, ``true``/``false`` (or the
    CLI's ``on``/``off``) sets it, and an explicit ``null`` clears it. Sending
    ``{"expires_at": null}`` is therefore not the same request as sending ``{}``.
    """
    engine = _engine(store)
    return _clean(
        engine.update_link(
            link_id, payload, source=_source("PATCH", "/links/{link_id}"), actor="rep"
        )
    )


@router.delete("/links/{link_id}")
def revoke_link(link_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Revoke. Takes effect on the buyer's next request."""
    engine = _engine(store)
    return _clean(
        engine.revoke_link(link_id, source=_source("DELETE", "/links/{link_id}"), actor="rep")
    )


@router.get("/rooms/{room_id}/views")
def list_views(
    room_id: str, link_id: str | None = Query(None), store: RecordStore = StoreDep
) -> dict[str, Any]:
    return {"room_id": room_id, "views": _clean(_engine(store).list_views(room_id, link_id))}


@router.get("/rooms/{room_id}/notifications")
def list_notifications(room_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    return {
        "room_id": room_id,
        "notifications": _clean(_engine(store).list_notifications(room_id)),
    }


@router.get("/visitors/{visitor_id}")
def read_visitor(visitor_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Read back the persisted, verified buyer identity.

    Mirrors ``GET /v1/visitors/{id}``. Only ever a row this workflow wrote after a
    one-time code was accepted, so ``verified`` is not a caller-supplied claim.
    """
    return _clean(_engine(store).read_visitor(visitor_id))


# --------------------------------------------------------------------------- #
# Buyer surface
# --------------------------------------------------------------------------- #
#
# No seller authentication on these routes, because the research describes them as
# the path a buyer takes from an emailed URL: the link id is the public handle and
# the gate is the only thing standing between the URL and the document. Anything
# that identifies *who is asking* is what the gate collects, not the transport.


@router.get("/links/{link_id}/gate")
def gate_state(link_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Which step the buyer is on. Expiry is evaluated on this request."""
    return _clean(_engine(store).gate_state(link_id))


@router.post("/links/{link_id}/gate/email")
def submit_email(
    link_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Step one. Collects the address, and dispatches a code when authenticated.

    The response says a code went out; it never says what the code is.
    """
    engine = _engine(store)
    return _clean(
        engine.submit_email(
            link_id, payload.get("email"), source=_source("POST", "/links/{link_id}/gate/email")
        )
    )


@router.post("/links/{link_id}/gate/code")
def submit_code(
    link_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Step two, only on an authenticated link. Verified server-side."""
    engine = _engine(store)
    return _clean(
        engine.submit_code(
            link_id,
            payload.get("challenge_id"),
            payload.get("code"),
            source=_source("POST", "/links/{link_id}/gate/code"),
        )
    )


@router.post("/links/{link_id}/gate/password")
def submit_password(
    link_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Step three, and the grant. The password is hashed on the way in."""
    engine = _engine(store)
    return _clean(
        engine.submit_password(
            link_id,
            payload.get("password"),
            challenge_id=payload.get("challenge_id"),
            source=_source("POST", "/links/{link_id}/gate/password"),
        )
    )


@router.get("/links/{link_id}/document")
def read_document(
    link_id: str,
    view_token: str = Query(..., alias="view_token"),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The document, and only to a buyer holding a granted view token.

    Expiry is re-evaluated here too, so a token granted before the expiry does not
    outlive it.
    """
    engine = _engine(store)
    return _clean(
        engine.read_document(
            link_id, view_token, source=_source("GET", "/links/{link_id}/document")
        )
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the gate states the research says matter, not just the happy path.

    Produced by calling the real :class:`~dsr.link_gating.gate.GateEngine`, so the
    demo cannot show a shape, an event, an audit row or a view that the HTTP
    routes would not produce. ``source="seed"`` rather than a route string: no
    route served this, and claiming one would be the lie hard rule 4 exists to
    prevent.

    The states seeded, and why each is here:

    * a link that has **already expired** - the buyer gets the friendly page, and
      the board shows a real expiry rather than only future ones;
    * a link that is about to **expire**, so the countdown is visible;
    * a **revoked** link - "anyone with the URL gets the expired page", and a
      seller needs to see the revocation they performed;
    * an **authenticated** link with a verified visitor, a view event and the
      notification that defaults on, so the attribution chain is inspectable;
    * a link with a **password rejected** against it, because a security board
      that only shows successes teaches a reviewer nothing;
    * a **pending code** challenge and a **consumed** one;
    * an **open** link with no gate at all, so the "nothing is asked" state exists;
    * a **preset** and two links seeded from it, one overriding the preset's
      expiry with an explicit ``null``.

    The seed cannot complete an authenticated round trip end to end, because the
    one-time code only exists inside the delivery transport and this function does
    not stand one up. It injects one - a closure that keeps the code in a local
    list for the length of the seed and nothing after - because a demo row that
    claims a verified visitor with no verified visitor behind it is exactly the
    kind of thing this workflow exists to prevent.
    """
    store = RecordStore(db)
    now: datetime = context["now"]
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return ""

    # Injected so the authenticated rows are produced by the real gate. The code
    # never leaves this closure.
    sent: list[dict[str, Any]] = []

    def capture(message: dict[str, Any]) -> None:
        sent.append(dict(message))

    engine = gate_engine.GateEngine(store, now=lambda: now, deliver=capture)

    room_id = room_ids[0][0]
    room_two = room_ids[1][0] if len(room_ids) > 1 else room_id

    # A document link needs a document to point at. If the core dataset seeded one
    # the link resolves; if not, it stays a dataroom link rather than inventing a
    # record in a collection another workflow owns.
    document = next(iter(store.list("document", limit=1)), None)
    target: dict[str, Any] = (
        {"document_id": document["id"]} if document else {"dataroom_id": room_two}
    )

    preset = engine.create_preset(
        {
            "name": "Enterprise deal baseline",
            "fields": {
                "password": "northwind-2026",
                # Carries an expiry on purpose: the point of the second seeded link
                # is to show "Pass null to override a preset's expiry with none",
                # and there is nothing to override if the baseline has no expiry.
                "expires_at": (now + timedelta(days=30)).isoformat(timespec="milliseconds"),
                "email_protected": True,
                "email_authenticated": False,
                "enable_notification": True,
                "enable_watermark": True,
                "allow_download": False,
            },
        },
        source="seed",
        actor="dana",
    )

    # 1. Fully gated, unexpired: password, an email address, and a code.
    authenticated = engine.create_link(
        room_id,
        {
            "title": "Northwind - mutual NDA",
            "dataroom_id": room_id,
            "password": "northwind-2026",
            "email_authenticated": True,
            "expires_at": (now + timedelta(days=21)).isoformat(timespec="milliseconds"),
        },
        source="seed",
        actor="dana",
    )

    # 2. Already expired: the friendly page, with the document still on disk.
    engine.create_link(
        room_id,
        {
            "title": "Halcyon - pricing (expired 3 days ago)",
            "dataroom_id": room_id,
            "password": "halcyon-deal",
            "expires_at": (now - timedelta(days=3)).isoformat(timespec="milliseconds"),
        },
        source="seed",
        actor="dana",
    )

    # 3. About to expire, so the countdown is on the board.
    engine.create_link(
        room_two,
        {
            "title": "Vantage - security review (closes today)",
            **target,
            "password": "vantage",
            "expires_at": (now + timedelta(hours=6)).isoformat(timespec="milliseconds"),
        },
        source="seed",
        actor="dana",
    )

    # 4. Preset-seeded, and a second one that overrides the preset's expiry with an
    #    explicit null - the distinction the OpenAPI note calls out.
    engine.create_link(
        room_two,
        {
            "title": "Kestrel - seeded from the baseline",
            "dataroom_id": room_two,
            "preset_id": preset["id"],
        },
        source="seed",
        actor="dana",
    )
    engine.create_link(
        room_two,
        {
            "title": "Kestrel - baseline, expiry explicitly cleared",
            "dataroom_id": room_two,
            "preset_id": preset["id"],
            "expires_at": None,
        },
        source="seed",
        actor="dana",
    )

    # 5. Open, with nothing asked at all.
    engine.create_link(
        room_two,
        {"title": "Public page - ungated", "dataroom_id": room_two, "email_protected": False},
        source="seed",
        actor="dana",
    )

    # 6. A real pass through the authenticated gate, so a verified visitor, a view
    #    event and the notification exist for the board to show - and a second pass
    #    that a wrong password stops, because a board that only shows successes
    #    teaches a reviewer nothing.
    granted = _walk_through(
        engine, authenticated["id"], "buyer@northwind.example", "northwind-2026", sent
    )
    refused = _walk_through(
        engine, authenticated["id"], "ops@northwind.example", "not-the-password", sent
    )
    if granted is None or refused is not None:
        # A demo row claiming a view that was never recorded is the one thing this
        # workflow exists to prevent, so the seed reports failure rather than
        # asserting success.
        return (
            f"WF-069 demo data incomplete: expected one granted view and one refused "
            f"password, got granted={granted is not None} refused={refused is not None}"
        )

    # 7. Revoked: the friendly page on the next request, and the row survives.
    revoked = engine.create_link(
        room_two,
        {"title": "Meridian - withdrawn", "dataroom_id": room_two, "password": "meridian"},
        source="seed",
        actor="dana",
    )
    engine.revoke_link(revoked["id"], source="seed", actor="dana")

    # 8. A pending code challenge, so the in-flight state is reviewable. Minted by
    #    asking for the email again rather than by writing a row by hand.
    engine.submit_email(
        authenticated["id"], "pending@northwind.example", source="seed", actor="dana"
    )

    # Counts are read back from the engine rather than written out here, so the
    # line the seeder prints cannot drift from what was actually created.
    counted = engine.summary()
    return (
        f"1 governed baseline, {counted['links']} links across the gate states "
        f"(authenticated, expired, expiring, preset-seeded, expiry-cleared, open) "
        f"plus {counted['revoked']} revoked, {counted['views']} verified view(s), "
        f"{counted['verified_visitors']} verified visitor(s), "
        f"{counted['notifications']} notification(s), "
        f"{counted['pending_codes']} one-time code(s) pending"
    )


def _walk_through(
    engine: gate_engine.GateEngine,
    link_id: str,
    email: str,
    password: str,
    sent: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Take one buyer all the way through the gate, returning the grant.

    Returns ``None`` if the gate refused, so a seed that could not complete says
    so rather than claiming a view it did not record.
    """
    try:
        opened = engine.submit_email(link_id, email, source="seed")
        code_message = next(
            (m for m in reversed(sent) if m.get("challenge_id") == opened.get("challenge_id")),
            None,
        )
        if opened.get("step") == rules.STEP_CODE and code_message:
            verified = engine.submit_code(
                link_id, opened["challenge_id"], code_message["code"], source="seed"
            )
        else:
            verified = opened
        if verified.get("granted"):
            return verified
        return engine.submit_password(
            link_id, password, challenge_id=verified.get("challenge_id"), source="seed"
        )
    except rules.GateDenied:
        return None
