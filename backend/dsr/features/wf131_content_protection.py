"""WF-131: per-recipient content protection and auditability.

The rules live in :mod:`dsr.content_protection`, which knows nothing about HTTP.
This module is the five things a feature adds and nothing else:

* the route table, an ``APIRouter`` this feature owns on ``/api/wf-131``;
* the error mapping, an ``EXCEPTION_HANDLERS`` export the host attaches, because
  FastAPI accepts exception handlers on the app object only;
* the demo rows, a ``seed(db, context)`` hook the seeder calls;
* the vocabulary and the honesty sentences, served from one place so the page
  cannot drift from the rules that validate it;
* the plan gate, delegated to WF-071's own rule rather than re-implemented, so a
  watermark enabled through this bundle is judged by the workflow that owns it.

Dependencies come from :mod:`dsr.deps`. Nothing here imports ``dsr.api`` and
nothing opens SQLite, so the enforced test that greps feature modules for those
two things passes by construction.

The researched mapping of the spec's surfaces onto this feature:

=============================================  =========================================
Researched surface                              Route
=============================================  =========================================
the policy matrix                               ``GET/POST /links/{id}/policies``
one recipient's policy                          ``GET/PATCH /policies/{id}``
revocation ("revoke a link ... it dies            ``POST /policies/{id}/revoke``,
immediately")                                   ``/policies/{id}/restore``
the gate, evaluated for one recipient          ``GET /policies/{id}/evaluate``
a viewer entering the room                      ``POST /policies/{id}/admit``
the action log (view/download/share/revoke)     ``GET/POST /links/{id}/actions``
the bulk-download alert                         ``GET /links/{id}/bulk-alert``
the whole bundle on one link                    ``GET /links/{id}``
=============================================  =========================================

Source: ``docs/research/digital-sales-room-workflows/wf/WF-131.md``, quoted in
full on issue #167.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.content_protection import (
    ACTION_CODES,
    ACTION_COLLECTION,
    POLICY_COLLECTION,
    ContentProtectionError,
    PolicyNotFound,
    ProtectionBook,
    ProtectionDenied,
    describe,
    honesty,
)
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore
from dsr.viewer_watermark import rules as watermark_rules

FEATURE = {
    "id": "wf-131-content-protection",
    "ticket": "WF-131",
    "name": "Per-recipient content protection",
    "description": (
        "One matrix per recipient: email verification, a passcode, an NDA, a "
        "watermark, a download switch and an IP allow-list, plus an action log "
        "(view/download/share/revoke) that names the four protections each row "
        "satisfies. The gate is WF-069's, the NDA is WF-070's, the watermark is "
        "WF-071's, the download AND is WF-072's and the action codes are WF-079's."
    ),
    "nav": [{"id": "content-protection", "label": "Content protection"}],
}

router = APIRouter(prefix="/api/wf-131", tags=["WF-131"])


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


def get_book(store: RecordStore = StoreDep) -> ProtectionBook:
    """The book, built on the shared audited store.

    Per request rather than hung on ``app.state``, which is the shared-file edit
    this host exists to remove. It also leaves the clock a seam a test can replace.
    """
    return ProtectionBook(store)


BookDep = Depends(get_book)


def _source(verb: str, suffix: str) -> str:
    """The audit ``source`` for a write: the path this router actually serves."""
    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _protection_error(request: Request, exc: ContentProtectionError) -> JSONResponse:
    """A malformed policy or action, answered 422 with a field-keyed map."""
    return JSONResponse(status_code=422, content=exc.to_dict())


def _denied(request: Request, exc: ProtectionDenied) -> JSONResponse:
    """A recipient the matrix refused, answered 403 with the decision."""
    return JSONResponse(status_code=403, content=exc.to_dict())


def _not_found(request: Request, exc: PolicyNotFound) -> JSONResponse:
    """No such policy or link, answered 404.

    Its own handler because a feature may only map error types it raises itself:
    registering one for the store's ``RecordNotFound`` would intercept that
    exception across the whole product.
    """
    return JSONResponse(status_code=404, content={"error": "policy_not_found", "detail": str(exc)})


EXCEPTION_HANDLERS = {
    ContentProtectionError: _protection_error,
    ProtectionDenied: _denied,
    PolicyNotFound: _not_found,
}


def _require_plan_on_flip(plan: str | None, *, was: Any, now: Any) -> None:
    """WF-071's plan gate, fired only when a watermark flips on.

    Delegated rather than re-implemented: ``watermark_rules.require_plan`` raises
    the error type WF-071 already maps, so the response is the researched
    ``403 forbidden_plan_feature`` and this feature registers no duplicate
    handler. The gate fires on a flip only, which is WF-071's own reading of
    "enabling a gated feature returns 403 ... while existing links keep working
    after a downgrade".
    """
    if plan is None:
        return
    if bool(now) and not bool(was):
        watermark_rules.require_plan(plan)


# --------------------------------------------------------------------------- #
# The published contract
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every term, with the workflow each one came from."""
    return describe()


@router.get("/honesty")
def honesty_route() -> dict[str, Any]:
    """What this workflow is and is not worth, and the three items it declines."""
    return honesty()


@router.get("/actions")
def action_codes() -> dict[str, Any]:
    """The four logged verbs and the WF-079 integer code each one carries."""
    return {
        "actions": [{"action": action, "code": code} for action, code in ACTION_CODES.items()],
        "event_code_field": "event_code",
    }


@router.get("/summary")
def summary(
    room_id: str | None = Query(default=None),
    book: ProtectionBook = BookDep,
) -> dict[str, Any]:
    """Counts for the scope: policies, recipients, actions, the bulk alert."""
    return book.summary(room_id)


# --------------------------------------------------------------------------- #
# The whole bundle on one link
# --------------------------------------------------------------------------- #


@router.get("/links/{link_id}")
def compose_link(
    link_id: str,
    collection: str = Query(
        default="", description="Which workflow's link collection holds this link."
    ),
    book: ProtectionBook = BookDep,
) -> dict[str, Any]:
    """The four protections and this workflow's matrix, joined onto one link."""
    return book.compose_link(link_id, collection or None)


# --------------------------------------------------------------------------- #
# The policy matrix
# --------------------------------------------------------------------------- #


@router.get("/policies")
def list_policies_in_scope(
    room_id: str | None = Query(default=None),
    link_id: str | None = Query(default=None),
    include_revoked: bool = Query(default=True),
    book: ProtectionBook = BookDep,
) -> dict[str, Any]:
    """Every policy in scope, by room or by link. The board's list."""
    policies = book.list_policies(link_id, room_id, include_revoked=include_revoked)
    return {"room_id": room_id, "link_id": link_id, "count": len(policies), "policies": policies}


@router.get("/links/{link_id}/policies")
def list_policies(
    link_id: str,
    include_revoked: bool = Query(default=True),
    book: ProtectionBook = BookDep,
) -> dict[str, Any]:
    """Every recipient's policy for this link, revoked rows included by default."""
    policies = book.list_policies(link_id, include_revoked=include_revoked)
    return {"link_id": link_id, "count": len(policies), "policies": policies}


@router.post("/links/{link_id}/policies", status_code=201)
def create_policy(
    link_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    plan: str | None = Query(
        default=None, description="The team's plan, checked only when a watermark flips on"
    ),
    book: ProtectionBook = BookDep,
) -> dict[str, Any]:
    """Bind a policy to a recipient of this link.

    Enabling a watermark on the new policy meets WF-071's plan gate when a plan is
    named; the gate is the same rule, so no second plan system exists.
    """
    body = {"link_id": link_id, **dict(payload or {})}
    _require_plan_on_flip(plan, was=False, now=body.get("enable_watermark"))
    return book.create_policy(
        room_id or "",
        body,
        actor=actor,
        source=_source("POST", "/links/{link_id}/policies"),
    )


@router.get("/policies/{policy_id}")
def read_policy(policy_id: str, book: ProtectionBook = BookDep) -> dict[str, Any]:
    """One recipient's policy, with every field it interprets."""
    return book.read_policy(policy_id)


@router.patch("/policies/{policy_id}")
def update_policy(
    policy_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    plan: str | None = Query(default=None),
    book: ProtectionBook = BookDep,
) -> dict[str, Any]:
    """Move one or more matrix switches, or clear the agreement with null."""
    current = book.read_policy(policy_id)
    _require_plan_on_flip(
        plan,
        was=current["policy"].get("enable_watermark"),
        now=(payload or {}).get("enable_watermark", current["policy"].get("enable_watermark")),
    )
    return book.update_policy(
        policy_id,
        payload,
        actor=actor,
        source=_source("PATCH", "/policies/{policy_id}"),
    )


@router.post("/policies/{policy_id}/revoke")
def revoke_policy(
    policy_id: str,
    actor: str | None = Query(default=None),
    book: ProtectionBook = BookDep,
) -> dict[str, Any]:
    """Revoke a recipient. The next request is refused, and the revoke is logged."""
    return book.revoke_policy(
        policy_id,
        actor=actor,
        source=_source("POST", "/policies/{policy_id}/revoke"),
    )


@router.post("/policies/{policy_id}/restore")
def restore_policy(
    policy_id: str,
    actor: str | None = Query(default=None),
    book: ProtectionBook = BookDep,
) -> dict[str, Any]:
    """Undo a revocation."""
    return book.restore_policy(
        policy_id,
        actor=actor,
        source=_source("POST", "/policies/{policy_id}/restore"),
    )


# --------------------------------------------------------------------------- #
# The evaluation and the viewer's admission
# --------------------------------------------------------------------------- #


def _context_from(body: Any) -> dict[str, Any]:
    """The viewer facts an evaluation reads, from a JSON body or query params.

    Kept in one place so ``evaluate`` and ``admit`` cannot read different keys.
    """
    body = dict(body or {})
    return {
        "email": body.get("email"),
        "email_verified": bool(body.get("email_verified")),
        "passcode_ok": bool(body.get("passcode_ok")),
        "nda_accepted": bool(body.get("nda_accepted")),
        "ip": body.get("ip"),
        "download": dict(body.get("download") or {}),
    }


@router.get("/policies/{policy_id}/evaluate")
def evaluate_policy(
    policy_id: str,
    ip: str | None = Query(default=None),
    email_verified: bool = Query(default=False),
    passcode_ok: bool = Query(default=False),
    nda_accepted: bool = Query(default=False),
    room_bulk_download: bool = Query(default=False),
    item_can_download: bool = Query(default=False),
    book: ProtectionBook = BookDep,
) -> dict[str, Any]:
    """Answer the matrix for one recipient, without writing anything."""
    return book.evaluate(
        policy_id,
        {
            "email_verified": email_verified,
            "passcode_ok": passcode_ok,
            "nda_accepted": nda_accepted,
            "ip": ip,
            "download": {
                "bulk_download": room_bulk_download,
                "can_download": item_can_download,
            },
        },
    )


@router.post("/policies/{policy_id}/admit")
def admit(
    policy_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    book: ProtectionBook = BookDep,
) -> dict[str, Any]:
    """A viewer entering the room: evaluate, log the attempt, refuse if denied.

    A denial is answered 403 with the decision that named the protection, and the
    attempt is still logged - a rejected attempt is as visible as a successful one.
    """
    return book.admit(
        policy_id,
        _context_from(payload),
        actor=actor,
        source=_source("POST", "/policies/{policy_id}/admit"),
    )


# --------------------------------------------------------------------------- #
# The action log
# --------------------------------------------------------------------------- #


@router.get("/links/{link_id}/actions")
def list_actions(
    link_id: str,
    recipient: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    book: ProtectionBook = BookDep,
) -> dict[str, Any]:
    """Every action logged for this link, newest first, filterable by recipient."""
    actions = book.list_actions(link_id, recipient, limit=limit)
    return {"link_id": link_id, "count": len(actions), "actions": actions}


@router.post("/links/{link_id}/actions", status_code=201)
def record_action(
    link_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    book: ProtectionBook = BookDep,
) -> dict[str, Any]:
    """Log one action: view, download, share or revoke.

    The caller names the action and the recipient; the row carries the WF-079
    integer code the export queries by. A share is logged as its own action rather
    than derived from a view, so "who forwarded this on" stays answerable.
    """
    body = dict(payload or {})
    return book.record_action(
        link_id,
        body.get("recipient"),
        action=body.get("action"),
        allowed=body.get("allowed", True),
        room_id=room_id,
        detail=body.get("detail"),
        source=_source("POST", "/links/{link_id}/actions"),
        actor=actor,
    )


@router.get("/links/{link_id}/bulk-alert")
def bulk_alert(
    link_id: str,
    window_minutes: int | None = Query(default=None, ge=1, le=10080),
    threshold: int | None = Query(default=None, ge=1, le=1000),
    book: ProtectionBook = BookDep,
) -> dict[str, Any]:
    """The bulk-download pattern on this link. An alert, never a block."""
    return book.bulk_alert(link_id, window_minutes=window_minutes, threshold=threshold)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

DEMO_RECIPIENT = "a.buyer@northwind.example"
DEMO_SECOND = "b.buyer@northwind.example"
DEMO_IP = "203.0.113.24"
DEMO_NDA_TITLE = "Northwind mutual NDA"
DEMO_NDA_BODY = (
    "Recipient will keep the disclosed materials confidential, use them only to "
    "evaluate the proposed engagement, and return or destroy them on request."
)


def _demo_link(db: AuditedDatabase, room_id: str, now: Any) -> str:
    """A real WF-069 link, a real WF-070 agreement, through their own engines.

    The demo uses the workflows' own writes rather than hand-written rows, so the
    matrix below is evaluated against a link the gate itself would produce. A demo
    that invented the link's shape could show a state the product cannot make.
    """
    from dsr.link_gating.agreement import AgreementEngine
    from dsr.link_gating.gate import GateEngine

    store = RecordStore(db)
    document = db.create(
        "document",
        {
            "title": "Enterprise Security & Compliance Overview",
            "kind": "pdf",
            "pages": 12,
            "public": False,
            "status": "published",
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )
    links = GateEngine(store, now=lambda: now)
    link = links.create_link(
        room_id,
        {
            "title": "Northwind evaluation",
            "document_id": document["id"],
            "email_protected": True,
            "password": "northwind-2026",
        },
        actor="dana",
        source="seed",
    )
    agreement = AgreementEngine(store, now=lambda: now).create_agreement(
        room_id,
        {"title": DEMO_NDA_TITLE, "body": DEMO_NDA_BODY, "kind": "nda"},
        actor="dana",
        source="seed",
    )
    AgreementEngine(store, now=lambda: now).set_gate(
        link["id"],
        {"enable_agreement": True, "agreement_id": agreement["id"]},
        actor="dana",
        source="seed",
    )
    return link["id"]


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the matrix and the states a reviewer needs to read.

    The demo deliberately contains the states the happy path alone would not
    teach, because a demo of only success teaches nothing:

    * one recipient gated harder than the link: passcode plus the NDA, allow-listed
      to one address, with a watermark and downloads off;
    * a second recipient with the same link and no extra requirement, so the
      matrix's per-recipient shape is visible;
    * a revocation, so the column reads "revoked" rather than only "on";
    * a bulk-download pattern for one recipient, so the alert has something to
      report without inventing a block.
    """
    rooms = context.get("room_ids") or []
    if not rooms:
        return "0 recipient policies (no rooms to scope them to)"
    room_id = rooms[0][0]
    now = context["now"]
    link_id = _demo_link(db, room_id, now)

    book = ProtectionBook(RecordStore(db), now=lambda: now)
    strict = book.create_policy(
        room_id,
        {
            "link_id": link_id,
            "recipient": DEMO_RECIPIENT,
            "require_passcode": True,
            "enable_agreement": True,
            "agreement_id": _demo_agreement_id(db, room_id),
            "enable_watermark": True,
            "allow_download": False,
            "ip_allowlist": [DEMO_IP],
        },
        actor="dana",
        source="seed",
    )
    open_policy = book.create_policy(
        room_id,
        {"link_id": link_id, "recipient": DEMO_SECOND, "allow_download": True},
        actor="dana",
        source="seed",
    )

    # The happy path: the strict recipient proves the inbox, the passcode and the
    # acceptance, and comes from the allowed address.
    book.admit(
        strict["id"],
        {
            "email_verified": True,
            "passcode_ok": True,
            "nda_accepted": True,
            "ip": DEMO_IP,
            "download": {"bulk_download": False, "can_download": True},
        },
        actor="viewer",
        source="seed",
    )
    downloads = 0
    for _ in range(6):
        book.record_action(
            link_id,
            open_policy["recipient"],
            action="download",
            allowed=True,
            room_id=room_id,
            detail="Downloaded the overview deck.",
            source="seed",
        )
        downloads += 1
    book.record_action(
        link_id,
        open_policy["recipient"],
        action="share",
        allowed=True,
        room_id=room_id,
        detail="Forwarded the link to a colleague.",
        source="seed",
    )

    # The refusal: a request from an address the allow-list does not cover.
    try:
        book.admit(
            strict["id"],
            {
                "email_verified": True,
                "passcode_ok": True,
                "nda_accepted": True,
                "ip": "198.51.100.7",
            },
            actor="viewer",
            source="seed",
        )
        refused = "no refusal recorded"
    except ProtectionDenied:
        refused = "an off-list address was refused"

    revoked = book.revoke_policy(strict["id"], actor="dana", source="seed")
    revoked_state = "one recipient revoked" if revoked["revoked"] else "revoke did not apply"

    alert = book.bulk_alert(link_id)
    return (
        f"2 recipient policies on 1 gated link (1 passcode+NDA+watermark, "
        f"1 download-open; {downloads} downloads and 1 share logged); {refused}; "
        f"{revoked_state}; bulk alert {alert['alert']} at {alert['threshold']} "
        f"downloads in {alert['window_minutes']} minutes"
    )


def _demo_agreement_id(db: AuditedDatabase, room_id: str) -> str:
    """The agreement the demo gate points at, for the policy to bind to."""
    store = RecordStore(db)
    rows = store.find("wf070_agreement", {"room_ref": room_id}, limit=1)
    if not rows:
        raise ContentProtectionError("the demo agreement was not created")
    return rows[0]["id"]


__all__ = [
    "EXCEPTION_HANDLERS",
    "FEATURE",
    "router",
    "seed",
    # Referenced so a rename in a dependency's collection name fails loudly here.
    "POLICY_COLLECTION",
    "ACTION_COLLECTION",
]
