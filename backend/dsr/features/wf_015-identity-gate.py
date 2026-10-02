"""WF-015: verify buyer identity and restrict access by email domain.

Ported from ``feature/WF-015-verify-buyer-identity-and-restrict-by-email-domain``
onto the plugin host.

The policy model itself is untouched: it lives in :mod:`dsr.access`, which the
port took over as it stood. That module is the researched specification made
executable - the three assurance tiers, template policies that inheriting rooms
defer to without a write to the room, an allowlist that normalises and dedupes,
bot and link-scanner detection from request headers, a verification outbox, and
sessions with a TTL. Nothing in it knows about HTTP, and nothing in this file
reaches past ``RecordStore``. The two tests that encode its deliberate stances -
``test_a_stored_policy_that_no_longer_validates_is_reported_not_hidden`` and
``test_template_change_reaches_an_inheriting_room_with_no_write_to_the_room`` -
are kept because they are decisions, not coverage.

This module is the three things on the branch that were edits to shared files:

* the route table, twelve endpoints the branch registered by hand on the shared
  ``app`` in ``dsr/api.py`` and which are now a router this feature owns and the
  host mounts;
* the error mapping, which was two ``@app.exception_handler`` blocks in
  ``dsr/api.py`` and is now an ``EXCEPTION_HANDLERS`` export the host attaches;
* the demo rows, which were an edit to ``backend/seed.py`` and are now a
  ``seed(db, context)`` hook the seeder calls.

Without that shape this workflow could not merge without resolving a conflict
against the other eleven, because all twelve appended to the same three files.

Five deliberate departures from the branch, each required by the contract:

* **The prefix is ``/api/wf-015-identity-gate``.** The branch served
  ``/api/rooms/{id}/access`` and ``/api/templates/{id}/access`` from the shared
  app. Those are core vocabulary several other workflows want - a feature that
  claims them is either a collision or dead code - so every path moved under a
  prefix this feature owns. The branch was never merged, so nothing external
  depended on the old paths. The *shape* under the prefix is unchanged, and
  ``backend/tests/test_access_api.py`` asserts the old paths are not served
  anywhere, in both directions.
* **Every write is handed the path this router actually serves.** The branch
  hard-coded ``source="POST /access/sessions"``, ``"GET /access/verify"``,
  ``"GET /access/session"`` and ``f"PUT /api/rooms/..."`` inside
  :mod:`dsr.access`. That is the exact defect the port brief calls out: the audit
  log ends up naming a route nobody can call. Those are now required keyword
  arguments, so the domain cannot guess and the caller cannot forget.
* **The verification link is built from the prefix too.** The branch wrote
  ``f"{root}/api/rooms/{room_id}/access/verify?token=..."`` into the outbox. Left
  alone that link 404s, and it is the one moment a buyer is actually watching, so
  ``api_prefix`` is threaded in from :data:`router` the same way ``source`` is.
* **The error mapping is a domain error, not an ``HTTPException``.** The branch
  handled these by hand at each call site. ``PolicyError`` and ``AccessDenied``
  now propagate and the host-installed handlers turn them into responses, which is
  what ``EXCEPTION_HANDLERS`` exists for. Both types are raised only by
  :mod:`dsr.access`, so the host cannot have a second feature mapping them.
* **The buyer gate is not in the nav.** The branch added a ``BUYER_ROUTE`` branch
  to ``App.jsx`` so ``#/view/{room_id}`` rendered without the seller's chrome.
  ``App.jsx`` is shared, so instead the descriptor in
  ``frontend/src/features/wf-015-identity-gate/index.jsx`` renders the gate when
  the hash is a buyer route. The security property survives intact - room content
  is not requested until the API grants it - but a buyer following an emailed link
  also sees the seller's sidebar. That is a one-line change in ``App.jsx`` and it
  is a human's decision, not this port's; see the module docstring of
  ``AccessSettings.jsx`` in the frontend folder.

One thing the port could **not** carry over, and it is a finding rather than an
omission: the branch's ``ui.jsx`` added ``Checkbox``, ``RadioGroup``, ``Notice``,
``CopyField`` and eight icon glyphs, and its ``lib/api.js`` added
``error.code`` / ``error.errors`` / ``error.body`` to every failed request. All of
those are shared files. The four primitives and the glyphs are rebuilt inside this
feature's frontend folder; the structured error fields are worked around in this
feature's own API wrapper, because ``apiRequest`` reads the error body to build a
message and then discards it. Both are listed in the port report as promotion work
rather than smuggled in here.
"""

from __future__ import annotations

import os
import urllib.parse
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from dsr.access import (
    MODE_IDENTIFY,
    MODE_VERIFY_EMAIL,
    OUTBOX_COLLECTION,
    POLICY_COLLECTION,
    SESSION_COLLECTION,
    AccessDenied,
    AccessGate,
    PolicyError,
    validate_policy,
)
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-015-identity-gate",
    "ticket": "WF-015",
    "name": "Verify buyer identity and restrict by email domain",
    "description": (
        "Three assurance tiers in front of a room - open, identification only, or "
        "verification by email - with an approved-domain allowlist on top, a "
        "template default that inheriting rooms follow, and a delivery outbox so "
        "the verification round trip works on an install with no mail server."
    ),
    "nav": [{"id": "access-and-identity", "label": "Access and identity"}],
}

router = APIRouter(prefix="/api/wf-015-identity-gate", tags=["WF-015"])


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# ``PolicyError`` and ``AccessDenied`` are this workflow's own types, raised by
# :mod:`dsr.access` and by nothing else in the product. That is what makes it safe
# to map them here: the host refuses two features mapping the same type, and a
# feature that registered a handler for ``ValueError`` or ``PermissionError``
# would intercept exceptions from the whole product.


def _policy_error(request: Request, exc: PolicyError) -> JSONResponse:
    """400 with a field-keyed map, so the UI can put each message next to the
    input that caused it rather than showing one combined string."""
    return JSONResponse(
        status_code=400,
        content={"error": "policy_invalid", "detail": str(exc), "errors": exc.errors},
    )


def _access_denied(request: Request, exc: AccessDenied) -> JSONResponse:
    """403 for a buyer the gate will not let through.

    The attempt is always recorded on a session; only the attribution is
    withheld, so the response carries the reason and the session id and nothing
    about who the buyer is.
    """
    return JSONResponse(
        status_code=403,
        content={"error": exc.reason, "detail": str(exc), "session_id": exc.session_id},
    )


EXCEPTION_HANDLERS = {PolicyError: _policy_error, AccessDenied: _access_denied}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _source(verb: str, suffix: str) -> str:
    """The audit ``source`` for a write: the path this router actually serves.

    Built from ``router.prefix`` rather than written out, because hard rule 4 of
    the port brief is that a write's audit row must name the route that served
    it. The branch guessed these strings inside the domain module and the guesses
    went stale the moment the paths moved; deriving them here means the route table
    and the audit log cannot disagree.
    """
    return f"{verb} {router.prefix}{suffix}"


def _require_room(store: RecordStore, room_id: str) -> None:
    """404 unless the id names a live record in the `room` collection."""
    record = store.get(room_id)
    if record is None or record["collection"] != "room":
        raise HTTPException(status_code=404, detail=f"room {room_id} not found")


def _base_url(request: Request) -> str:
    """Absolute base for links we hand a buyer.

    An operator behind a proxy sets ``DSR_PUBLIC_URL``; otherwise the request's own
    host is used, which is right for a local install and for a single-host
    deployment. Read per request, not at import, so a test can pin it.
    """
    return os.environ.get("DSR_PUBLIC_URL", "").rstrip("/") or str(request.base_url).rstrip("/")


def _deliver() -> bool:
    """Whether the gate queues a verification message.

    ``DSR_ACCESS_DELIVER=0`` lets a service issue its own link by another route,
    which is what a real SMTP transport would do. The session is still created, so
    a mail failure can never lock a buyer out.
    """
    return os.environ.get("DSR_ACCESS_DELIVER", "1").lower() not in ("0", "false", "no")


# --------------------------------------------------------------------------- #
# Policy: a room's own
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/access", summary="The policy in force for a room")
def get_room_access(room_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """The effective policy plus the level it resolved at.

    ``level`` matters to the seller: a control inherited from a template and shown
    without its origin is indistinguishable from one they set themselves, and the
    researched point is that policy rather than per-page discipline should enforce
    the control.

    ``errors`` appears when a stored policy no longer validates. It is reported
    rather than hidden, and the room falls open to ``open`` so a tightened rule
    cannot lock the seller out of their own room.
    """
    _require_room(store, room_id)
    return AccessGate(store).resolve(room_id).as_dict()


@router.put("/rooms/{room_id}/access", summary="Set the room's own policy")
def put_room_access(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Validate, normalise, and store the room's own policy.

    The whole policy is one payload, because a seller filling in a form is not
    making a sequence of PATCHes. Unknown keys ride along untouched, so a team's
    own field on a policy survives a save from this form with no migration.
    ``PolicyError`` becomes a 400 with a field-keyed error map.
    """
    _require_room(store, room_id)
    return AccessGate(store).set_policy(
        "room",
        room_id,
        payload,
        actor=actor,
        source=_source("PUT", f"/rooms/{room_id}/access"),
    )


@router.delete("/rooms/{room_id}/access", summary="Drop the room's own policy")
def delete_room_access(
    room_id: str,
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Drop the room's own policy so it falls back to whatever it inherits.

    Audited as a soft delete, so "who could see this room, and who changed that"
    stays answerable after the policy is gone.
    """
    _require_room(store, room_id)
    return AccessGate(store).clear_policy(
        "room",
        room_id,
        actor=actor,
        source=_source("DELETE", f"/rooms/{room_id}/access"),
    )


# --------------------------------------------------------------------------- #
# Policy: the template default
# --------------------------------------------------------------------------- #
#
# A template's policy is a *default*: rooms that inherit it pick it up on their next
# read, with no write to the room. That is the researched behaviour - verification
# is "automatically applied to each page your team creates from it" - and it is
# why a policy change is enforced by policy rather than by remembering to set it on
# every page.


@router.get("/templates/{template_id}/access", summary="The template default policy")
def get_template_access(template_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """The default a template applies to the rooms created from it.

    No 404 and no existence check: a template id is a free string a team chose, and
    "no policy set yet" is the honest answer rather than a missing record.
    """
    policy = AccessGate(store).template_policy(template_id)
    if policy is None:
        return {"policy": None, "level": "default", "source": None, "template_id": template_id}
    return {"policy": policy, "level": "template", "source": None, "template_id": template_id}


@router.put("/templates/{template_id}/access", summary="Set the template default")
def put_template_access(
    template_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Set the template default. Rooms that inherit pick it up on their next read."""
    return AccessGate(store).set_policy(
        "template",
        template_id,
        payload,
        actor=actor,
        source=_source("PUT", f"/templates/{template_id}/access"),
    )


@router.delete("/templates/{template_id}/access", summary="Drop the template default")
def delete_template_access(
    template_id: str,
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Drop the template default. Every inheriting room falls back to ``open``."""
    return AccessGate(store).clear_policy(
        "template",
        template_id,
        actor=actor,
        source=_source("DELETE", f"/templates/{template_id}/access"),
    )


# --------------------------------------------------------------------------- #
# What the buyer is told
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/access/requirements", summary="What the buyer must do")
def access_requirements(room_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """The buyer form's contract: which fields, whether verification is needed,
    whether an existing account may fill them in, and whether a domain rule applies.

    The allowlist is deliberately absent. A form that says "only foo.example is
    accepted" is a free allowlist oracle: it turns the gate from a check into a
    lookup table for anyone who probes it once.
    """
    _require_room(store, room_id)
    return AccessGate(store).requirements(room_id)


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/access/sessions", status_code=201, summary="Submit the gate form")
def create_access_session(
    room_id: str,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Run a buyer's form submission through the gate.

    The tier decides everything downstream: ``open`` grants and records nothing,
    ``identify`` records the identity and grants without sending mail, and
    ``verify_email`` leaves the room closed until the emailed link is followed. A
    domain that is not on the list is refused with 403 and the attempt is recorded
    without being attributed to anybody.

    The request's own headers are read here rather than in the domain module,
    because the bot heuristic is a statement about the request and the request is
    the HTTP layer's to describe.
    """
    _require_room(store, room_id)
    return AccessGate(store).open_session(
        room_id,
        payload,
        headers=dict(request.headers),
        method=str(payload.get("method") or "identified"),
        base_url=_base_url(request),
        deliver=_deliver(),
        source=_source("POST", f"/rooms/{room_id}/access/sessions"),
        api_prefix=router.prefix,
    )


@router.get("/rooms/{room_id}/access/verify", summary="The link from the verification email")
def verify_access_session(
    room_id: str,
    request: Request,
    token: str = Query(default=""),
    redirect: bool = Query(default=False),
    store: RecordStore = StoreDep,
) -> Any:
    """Verify a session from the emailed link.

    Idempotent, because a buyer who clicks twice should not be punished, but every
    presentation is counted and audited. The allowlist is re-checked here as well as
    at the form, so a link that has been sitting in an inbox cannot outlive a policy
    the seller has since tightened.

    With ``redirect=1`` - the form the emailed link carries - a successful
    verification sends the buyer on to the room in the app instead of returning JSON
    to a human. A refusal still returns 403 rather than redirecting, so a dead link
    tells the buyer why.
    """
    _require_room(store, room_id)
    result = AccessGate(store).verify(
        room_id, token, source=_source("GET", f"/rooms/{room_id}/access/verify")
    )
    if redirect:
        target = f"/#/view/{urllib.parse.quote(room_id)}?token={urllib.parse.quote(token)}"
        return RedirectResponse(url=target, status_code=303)
    return result


@router.get("/rooms/{room_id}/access/session", summary="What a token is worth right now")
def check_access_session(
    room_id: str,
    token: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Re-check a session, and say what is still owed.

    A status endpoint rather than a command: it answers 200 with a body for every
    state the buyer can legitimately be in, and reserves 4xx for an unknown token
    or a refusal. ``token`` is optional, so this also answers "what does this room
    need?" for a buyer who has no token yet - which is how the gate page decides
    whether to render a form at all.
    """
    _require_room(store, room_id)
    return AccessGate(store).check(
        room_id, token, source=_source("GET", f"/rooms/{room_id}/access/session")
    )


# --------------------------------------------------------------------------- #
# Seller views
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/access/sessions", summary="Who has been let in, and who was not")
def list_access_sessions(
    room_id: str,
    include_bots: bool = Query(default=False),
    include_refused: bool = Query(default=False),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The seller's view of the gate.

    Bot-flagged attempts are excluded by default: the research is explicit that
    scanners "pollute analytics", and a flag is only useful if it changes what the
    seller actually sees. ``excluded_bots`` reports the size of the exclusion rather
    than hiding it.

    Refused attempts are hidden by default and included on request. A refused probe
    is worth a seller's attention and is not worth being mistaken for a viewer.
    """
    _require_room(store, room_id)
    return AccessGate(store).sessions(
        room_id, include_bots=include_bots, include_refused=include_refused
    )


@router.get("/rooms/{room_id}/access/outbox", summary="Queued verification messages")
def list_access_outbox(
    room_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The delivery seam, so the verification link is reachable in a local install
    that has no mail server.

    This is a read. The message rows are written by the gate as a side effect of
    the buyer submitting the form, and the row is marked ``sent`` when the link is
    followed, so the seller can see which buyers never came back.
    """
    _require_room(store, room_id)
    messages = AccessGate(store).outbox(room_id, limit=limit)
    return {"room_id": room_id, "count": len(messages), "messages": messages}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #
#
# The branch added this to ``backend/seed.py``, which is a shared file that ten of
# the first twelve workflows rewrote purely to add their own rows. The seeder calls
# the hook below instead, so the demo travels with the feature that needs it.


def _iso_in(base: datetime, **delta: float) -> str:
    return (base + timedelta(**delta)).isoformat(timespec="milliseconds")


def seed(db, context: dict[str, Any]) -> str:
    """Make the core demo dataset show all three tiers, and the state that follows.

    A feature whose page is empty in the demo is a feature nobody can review, so
    what is seeded here is chosen to make each researched behaviour visible on
    screen without any interaction:

    * a **template default** that the inheriting room follows, with no policy
      written to the room - the whole point of policy over per-page discipline;
    * one room on each configured tier, plus one with no policy anywhere, so the
      radio group, the domain field's disabled state, and the "own policy /
      inherited / no policy set" badge each have something to render;
    * a **domain-restricted** room, which is the only combination of checkboxes the
      research calls out as meaningful;
    * sessions in every state the seller list renders - identified, verified, still
      pending, and a refused domain - including one bot-flagged probe, so the
      "excluded, and here is how many" line has a real number behind it;
    * a queued verification message with its link, because the outbox is the only
      way a local install can complete the round trip;
    * one analytics row with ``identity_verified`` set, which is the research's
      claim that a verified identity is "visible within your page analytics" and is
      otherwise invisible in a demo.

    Every write goes through the audited database, so running the seed twice
    produces a second complete set of audit rows rather than overwriting the first.
    """
    room_ids: list[tuple[str, str]] = context["room_ids"]
    now = context["now"]
    if len(room_ids) < 2:
        return "needs at least two rooms to show the tiers side by side"

    first, second = room_ids[0][0], room_ids[1][0]

    # The template default, deliberately not copied onto any room.
    template = db.create(
        "room_template",
        {"name": "Standard evaluation room", "description": "Used for every new evaluation."},
        actor="dana",
        source="seed",
    )
    db.create(
        POLICY_COLLECTION,
        {
            "subject_kind": "template",
            "subject_id": template["id"],
            **validate_policy({"mode": MODE_IDENTIFY, "collect_name": True, "collect_email": True}),
        },
        actor="dana",
        source="seed",
    )

    def room_policy(room_id: str, **policy: Any) -> None:
        db.create(
            POLICY_COLLECTION,
            {"subject_kind": "room", "subject_id": room_id, **validate_policy(policy)},
            room_id=room_id,
            actor="dana",
            source="seed",
        )

    # Tier 1: identification only, inherited from the template above. The room
    # carries only the template link, never a policy of its own.
    db.create(
        "room",
        {
            "name": "Inherits the template policy",
            "account": room_ids[0][1],
            "template_id": template["id"],
        },
        actor="dana",
        source="seed",
    )
    # Tier 2: the room's own identification policy, which overrides the template.
    room_policy(first, mode=MODE_IDENTIFY, collect_name=True, collect_email=True)
    # Tier 3: verification with the domain allowlist, the only sourced combination
    # that has both checkboxes on.
    room_policy(
        second,
        mode=MODE_VERIFY_EMAIL,
        collect_name=True,
        collect_email=True,
        domain_security=True,
        allowed_domains="northwind.example, @contoso.example",
    )
    # A room with no policy anywhere: the open tier, and the "no policy set" badge.
    db.create(
        "room",
        {
            "name": "Open to anyone with the link",
            "account": room_ids[2][1] if len(room_ids) > 2 else room_ids[0][1],
        },
        actor="dana",
        source="seed",
    )

    sessions: list[dict[str, Any]] = []

    def session(room_id: str, **data: Any) -> dict[str, Any]:
        record = db.create(
            SESSION_COLLECTION,
            {
                "policy_mode": data.get("policy_mode", MODE_IDENTIFY),
                "method": "identified",
                "issued_at": _iso_in(now, hours=-30),
                "attempts": 0,
                "likely_bot": False,
                **data,
            },
            room_id=room_id,
            actor="gate",
            source="seed",
        )
        sessions.append(record)
        return record

    # A verified buyer, and the one analytics row the research promises.
    verified = session(
        first,
        name="Priya Raman",
        email="priya@northwind.example",
        email_domain="northwind.example",
        status="verified",
        verified_at=_iso_in(now, hours=-26),
        viewed_at=_iso_in(now, hours=-25),
        views=2,
        expires_at=_iso_in(now, days=6),
    )
    db.create(
        "activity",
        {
            "person": verified["data"]["email"],
            "name": verified["data"]["name"],
            "account": room_ids[0][1],
            "action": "viewed",
            "target": "Northwind Traders",
            "identity_verified": True,
            "session_id": verified["id"],
            "occurred_at": verified["data"]["viewed_at"],
        },
        room_id=first,
        actor="gate",
        source="seed",
    )

    # Identified without verification: the middle tier, and an unverified analytics
    # row so the two are distinguishable in the list.
    session(
        first,
        name="Tom Okafor",
        email="tom@contoso.example",
        email_domain="contoso.example",
        status="identified",
        expires_at=_iso_in(now, days=1),
    )
    # Awaiting the email click, with the message the seller has to hand over. This
    # is the round trip a reviewer can actually finish in the demo.
    pending = session(
        second,
        name="Alex Buyer",
        email="alex@northwind.example",
        email_domain="northwind.example",
        status="pending_verification",
        policy_mode=MODE_VERIFY_EMAIL,
        expires_at=_iso_in(now, hours=18),
    )
    # A refused domain on the same restricted room, hidden by default and shown on
    # request: the seller should be able to see the room was probed.
    session(
        second,
        name="Someone Else",
        email="someone@elsewhere.example",
        email_domain="elsewhere.example",
        status="refused",
        refusal_reason="domain_not_allowed",
        policy_mode=MODE_VERIFY_EMAIL,
        expires_at=_iso_in(now, hours=18),
    )
    # A mail previewer, which the list excludes and counts.
    session(
        second,
        name="Link preview",
        email="preview@elsewhere.example",
        email_domain="elsewhere.example",
        status="refused",
        refusal_reason="likely_bot",
        likely_bot=True,
        policy_mode=MODE_VERIFY_EMAIL,
        expires_at=_iso_in(now, hours=18),
    )

    db.create(
        OUTBOX_COLLECTION,
        {
            "session_id": pending["id"],
            "to": pending["data"]["email"],
            "subject": "Verify your email to open the room",
            "body": "Confirm this address to view the room.",
            "link": f"/api/wf-015-identity-gate/rooms/{second}/access/verify?token={pending['id']}",
            "open_link": (
                f"/api/wf-015-identity-gate/rooms/{second}/access/verify"
                f"?token={pending['id']}&redirect=1"
            ),
            "delivered_via": "outbox",
            "status": "queued",
            "queued_at": _iso_in(now, hours=-20),
        },
        room_id=second,
        actor="gate",
        source="seed",
    )

    return (
        f"1 template policy, 2 room policies ({MODE_IDENTIFY} + {MODE_VERIFY_EMAIL} with domain), "
        f"3 rooms, {len(sessions)} sessions "
        f"(1 verified, 1 identified, 1 pending, 2 refused, 1 bot), 1 queued message"
    )
