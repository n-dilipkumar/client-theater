"""WF-070: require NDA acceptance before a viewer sees the room.

The rules live in :mod:`dsr.link_gating.agreement` and are not restated here.
This module is the three things a feature contributes, and the three things it
must never contribute.

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
  A hundred workflow branches each editing those files is why none of the first
  hundred merged.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``.
* No hand-written ``source=`` string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served the write.

A note on error types
---------------------

WF-069 already registers handlers for ``rules.GateError``, ``rules.GateDenied``
and ``rules.LinkNotFound``, and the host refuses a second feature registering a
handler for the same type. This module therefore maps
:mod:`dsr.link_gating.agreement`'s own three types, which are distinct classes
and not subclasses of WF-069's. None of them is a builtin, deliberately: a handler
registered for ``ValueError`` or ``PermissionError`` would intercept those
exceptions across the whole product.

A note on the link rows this workflow writes
--------------------------------------------

The gate settings live on the ``Link`` row, because the research says so: "The
gate flag + agreement reference live on the Link row." So this feature writes
``enable_agreement`` and ``agreement_id`` into a payload owned by WF-069's gate,
and it does so through the store rather than through WF-069's engine. The two
fields are named in ``rules.PRESET_COVERED_FIELDS`` and are carried onto a link
seeded from a governed baseline without being enforced by anything, which is
exactly the gap this workflow fills. See the module docstring of
:mod:`dsr.link_gating.agreement` for why the alternative - a parallel gate row -
was rejected.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.link_gating import agreement as nda, gate as gate_engine
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-070-require-nda-acceptance-before-viewing",
    "ticket": "WF-070",
    "name": "Require NDA acceptance before viewing",
    "description": (
        "Put an NDA in front of a buyer link. The agreement text is served instead "
        "of room content, a viewer's acceptance is recorded against their own "
        "viewer session and the text it was given under, and content is released "
        "only to an acceptance that still covers the agreement as it stands."
    ),
    "nav": [{"id": "wf-070-nda-gate", "label": "NDA gate"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several
#: workflows already share. Every path below sits under it.
router = APIRouter(prefix="/api/wf-070", tags=["WF-070"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is
    a domain function hardcoding a URL string, which leaves the audit log naming a
    route the app stopped serving.
    """
    return f"{method} {router.prefix}{path}"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _agreement_error(request: Request, exc: nda.AgreementError) -> JSONResponse:
    """400 with a field-keyed map, so each message lands next to its input."""
    return JSONResponse(
        status_code=400,
        content={"error": "agreement_invalid", "detail": str(exc), "errors": exc.errors},
    )


def _agreement_denied(request: Request, exc: nda.AgreementDenied) -> JSONResponse:
    """403 for a viewer the gate will not let through.

    ``reason`` is a stable token the UI branches on; ``detail`` is the sentence the
    viewer reads, and for a closed link the two are worded the way WF-069 words an
    expired one so a viewer cannot tell a withdrawal from an expiry.
    """
    return JSONResponse(
        status_code=403,
        content={"error": "agreement_denied", "reason": exc.reason, "detail": str(exc)},
    )


def _agreement_not_found(request: Request, exc: nda.AgreementNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such agreement or link."},
    )


EXCEPTION_HANDLERS = {
    nda.AgreementError: _agreement_error,
    nda.AgreementDenied: _agreement_denied,
    nda.AgreementNotFound: _agreement_not_found,
}


def _engine(store: RecordStore) -> nda.AgreementEngine:
    """The engine, with the clock left at its production default.

    The real clock here rather than a request-supplied value: a caller that could
    pass its own ``now`` could pass a past one and open a gate that has already
    closed. Tests inject the clock into the engine directly.
    """
    return nda.AgreementEngine(store)


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

    Includes what is deliberately absent, under ``not_implemented``. A buyer or a
    reviewer who cannot tell the difference between "we decided not to" and "we
    forgot to" has to go and read the source, and that is a cost paid every time
    the question comes up.
    """
    return nda.vocabulary()


@router.post("/rooms/{room_id}/agreements")
def create_agreement(
    room_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Write an NDA a viewer can be shown and accept."""
    return _clean(
        _engine(store).create_agreement(
            room_id, payload, source=_source("POST", "/rooms/{room_id}/agreements"), actor="rep"
        )
    )


@router.get("/agreements")
def list_agreements(
    room_id: str | None = Query(None), store: RecordStore = StoreDep
) -> dict[str, Any]:
    """Every agreement, without the bodies. A list view needs titles and versions."""
    return {"room_id": room_id, "agreements": _clean(_engine(store).list_agreements(room_id))}


@router.get("/agreements/{agreement_id}")
def read_agreement(agreement_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """One agreement, with its body. This is what a viewer is shown."""
    return _clean(_engine(store).read_agreement(agreement_id))


@router.patch("/agreements/{agreement_id}")
def update_agreement(
    agreement_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Edit an agreement. Changing the text moves its version and re-opens the gate.

    Prior acceptances survive, naming the version and digest they were given
    under. So the record of who accepted what stays intact, and anyone who has not
    accepted the current text has to accept it again.
    """
    return _clean(
        _engine(store).update_agreement(
            agreement_id,
            payload,
            source=_source("PATCH", "/agreements/{agreement_id}"),
            actor="rep",
        )
    )


@router.delete("/agreements/{agreement_id}")
def delete_agreement(agreement_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Retire an NDA. The text and every acceptance of it survive.

    Links that named this agreement keep naming it and now fail closed, and the
    response lists them. Repairing them silently would be the one change nobody
    could audit, so the seller decides whether each should point somewhere else or
    stop asking.
    """
    return _clean(
        _engine(store).delete_agreement(
            agreement_id, source=_source("DELETE", "/agreements/{agreement_id}"), actor="rep"
        )
    )


@router.get("/rooms/{room_id}/gates")
def list_gates(room_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every link in a room and the agreement gate on it."""
    return {"room_id": room_id, "gates": _clean(_engine(store).list_gates(room_id))}


@router.get("/links/{link_id}/agreement")
def read_link_gate(link_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Confirm the gate state. Mirrors ``GET /v1/links/{id}`` for these fields.

    A revoked link answers rather than 404s, so a seller can still see which NDA a
    withdrawn link was pointing at.
    """
    return _clean(_engine(store).link_gate(link_id))


@router.patch("/links/{link_id}/agreement")
def update_link_gate(
    link_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Turn the gate on or off, or point it at another NDA.

    Tri-state: a field the body omits is left alone, ``true``/``false`` (or the
    CLI's ``on``/``off``) sets it, and an explicit ``null`` clears the agreement id.
    Sending ``{"enable_agreement": false}`` therefore keeps the NDA selected and
    only stops asking for it, which is how a rep pauses a gate mid-deal.
    """
    return _clean(
        _engine(store).set_gate(
            link_id, payload, source=_source("PATCH", "/links/{link_id}/agreement"), actor="rep"
        )
    )


@router.get("/rooms/{room_id}/acceptances")
def list_acceptances(
    room_id: str,
    link_id: str | None = Query(None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Who accepted what, and which version of the text they were given."""
    return {
        "room_id": room_id,
        "acceptances": _clean(_engine(store).list_acceptances(room_id, link_id)),
    }


@router.get("/rooms/{room_id}/sessions")
def list_sessions(
    room_id: str,
    link_id: str | None = Query(None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Viewer sessions. Token hashes are stripped on the way out."""
    return {
        "room_id": room_id,
        "sessions": _clean(_engine(store).list_sessions(room_id, link_id)),
    }


# --------------------------------------------------------------------------- #
# Viewer surface
# --------------------------------------------------------------------------- #
#
# No seller authentication on these routes, because the research describes them as
# the path a viewer takes from an emailed URL: the link id is the public handle and
# the gate is the only thing standing between the URL and the room. What
# identifies *who is asking* is what the gate collects, not the transport.


@router.get("/links/{link_id}/gate")
def open_link(link_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """The viewer's first request: mint a session and serve the NDA.

    Returns 403 with ``reason: link_closed`` for an expired or revoked link, and
    the same wording for both.
    """
    return _clean(_engine(store).open_link(link_id, source=_source("GET", "/links/{link_id}/gate")))


@router.post("/links/{link_id}/gate/agreement")
def accept_agreement(
    link_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Record the viewer's acceptance.

    ``accepted`` must be explicitly true. A request that merely arrives here has
    not accepted anything.
    """
    return _clean(
        _engine(store).accept(
            link_id,
            payload.get("session_id"),
            payload,
            source=_source("POST", "/links/{link_id}/gate/agreement"),
        )
    )


@router.get("/links/{link_id}/content")
def read_content(
    link_id: str,
    session_id: str | None = Query(None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Release the room content, and only to a viewer who has accepted.

    ``remaining_gates`` names the gates this workflow does not own. A link that also
    asks for a password has not been opened by accepting the NDA, and the response
    says so rather than implying the room is fully available.
    """
    return _clean(
        _engine(store).content(
            link_id, session_id, source=_source("GET", "/links/{link_id}/content")
        )
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the gate states the research says matter, not just the happy path.

    Produced by calling the real engines, so the demo cannot show a shape, an event
    or an audit row the HTTP routes would not produce. ``source="seed"`` rather
    than a route string: no route served this, and claiming one would be the lie
    hard rule 4 exists to prevent.

    The states seeded, and why each is here:

    * an **ungated** link, so "nothing is asked" exists and the gate can be seen to
      be doing something rather than always on;
    * a link **gated and unaccepted**, which is the state a buyer meets first and
      the only state in which the gate is actually doing work;
    * a link **gated and accepted**, with a real acceptance row against a real text
      digest, so the board shows the chain the research describes;
    * a link whose **agreement text was changed after acceptance**, which is the
      state that proves the gate re-opens rather than grandfathering the buyer;
    * a link whose gate is **on but points at an agreement that is not there**, the
      fail-closed state, because a board that cannot show that hides the worst thing
      this workflow can be in;
    * a second **agreement** on the other room, so the board shows that the gate is
      link-scoped and two links can point at two different NDAs.
    """
    store = RecordStore(db)
    now: datetime = context["now"]
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return ""

    room_id = room_ids[0][0]
    room_two = room_ids[1][0] if len(room_ids) > 1 else room_id
    engine = nda.AgreementEngine(store, now=lambda: now)

    # Links are made by WF-069's own engine, so they are real gated links rather
    # than rows written by hand into a collection this workflow does not own.
    links = gate_engine.GateEngine(store, now=lambda: now)
    source, actor = "seed", "dana"

    mutual = engine.create_agreement(
        room_id,
        {
            "title": "Northwind mutual NDA",
            "kind": nda.KIND_NDA,
            "body": (
                "MUTUAL NON-DISCLOSURE AGREEMENT. Each party agrees to keep the "
                "other party's confidential information confidential, and to use it "
                "only to evaluate the proposed transaction."
            ),
            "governing_law": "England and Wales",
            "effective_date": now.date().isoformat(),
        },
        source=source,
        actor=actor,
    )
    one_way = engine.create_agreement(
        room_two,
        {
            "title": "Vantage one-way NDA",
            "kind": nda.KIND_NDA,
            "body": (
                "ONE-WAY NON-DISCLOSURE AGREEMENT. The recipient agrees to keep the "
                "disclosing party's confidential information confidential for two "
                "years from the date of acceptance."
            ),
            "governing_law": "Delaware",
        },
        source=source,
        actor=actor,
    )

    def link(title: str, room: str, **extra: Any) -> dict[str, Any]:
        return links.create_link(
            room, {"title": title, "dataroom_id": room, **extra}, source=source, actor=actor
        )

    # 1. Ungated. The control case.
    link("Halcyon - ungated", room_id, email_protected=False)

    # 2. Gated, and nobody has accepted it. The state a buyer meets first.
    pending = link("Kestrel - NDA pending", room_two, password="kestrel-deal")
    engine.set_gate(
        pending["id"],
        {nda.CONVENIENCE_FLAG: mutual["id"]},
        source=source,
        actor=actor,
    )

    # 3. Gated and accepted, with a real acceptance against a real digest.
    accepted = link("Meridian - NDA accepted", room_two, password="meridian")
    engine.set_gate(
        accepted["id"],
        {nda.ENABLE_FIELD: True, nda.AGREEMENT_REF_FIELD: one_way["id"]},
        source=source,
        actor=actor,
    )
    opened = engine.open_link(accepted["id"], source=source)
    engine.accept(
        accepted["id"],
        opened["session_id"],
        {"accepted": True, "email": "buyer@meridian.example"},
        source=source,
    )

    # 4. Accepted, then the text was changed. The gate must re-open.
    revised = link("Vantage - NDA revised after acceptance", room_two, password="vantage")
    engine.set_gate(
        revised["id"], {nda.CONVENIENCE_FLAG: one_way["id"]}, source=source, actor=actor
    )
    first_pass = engine.open_link(revised["id"], source=source)
    engine.accept(
        revised["id"],
        first_pass["session_id"],
        {"accepted": True, "email": "ops@vantage.example"},
        source=source,
    )
    engine.update_agreement(
        one_way["id"],
        {"body": "ONE-WAY NDA. Amended to add a three-year retention period."},
        source=source,
        actor=actor,
    )

    # 5. The gate is on and points at an agreement that has been retired. This is
    #    the fail-closed state, and it is reachable only because retiring an
    #    agreement keeps its id: the link still names it, the text is no longer
    #    served, and content stops. A board that cannot show this hides the worst
    #    thing this workflow can be in.
    retired = link("Orphan - gate points at a retired agreement", room_two, password="orphan")
    engine.set_gate(
        retired["id"], {nda.CONVENIENCE_FLAG: one_way["id"]}, source=source, actor=actor
    )
    engine.delete_agreement(one_way["id"], source=source, actor=actor)

    # Counts are read back from the engine rather than written out here, so the
    # line the seeder prints cannot drift from what was actually created.
    counted = engine.summary()
    return (
        f"{counted['agreements']} agreements, {counted['gated']} of {counted['links']} links "
        f"gated across the states (ungated, pending, accepted, revised, gate-pointing-at-nothing), "
        f"{counted['acceptances']} acceptance(s) on {counted['accepted_sessions']} of "
        f"{counted['sessions']} viewer session(s), {counted['broken_gates']} gate(s) failing closed"
    )
