"""WF-001: create a Digital Sales Room from an account and a template.

Ported from ``feature/WF-001-create-room-from-template`` onto the plugin host.
The researched three-step wizard — account, template, name and optional friendly
URL — lives in :mod:`dsr.room_templates`, which is pure over
:class:`~dsr.store.RecordStore` and has no framework in it. This module is the
HTTP surface and the demo data: the four routes the research's flow needs, the
error mapping, and a ``seed`` hook.

Four deliberate departures from the branch, each forced by the contract:

* **The prefix is ``/api/wf-001``.** The branch appended four routes to
  ``dsr/api.py``, which every workflow branch also wrote. Under the host they
  become ``/api/wf-001/accounts``, ``/api/wf-001/room-templates`` and
  ``/api/wf-001/rooms``, and ``dsr/api.py`` is not touched. The branch was never
  merged, so nothing external depended on the old paths.
* **The module is ``room_templates``, not ``rooms``.** WF-005 owns
  ``backend/dsr/rooms.py`` and the room state machine. See the note in
  :mod:`dsr.room_templates`.
* **Every write is handed the path this router actually serves.** The branch
  hard-coded ``source="POST /api/rooms"`` inside the domain module, which is the
  defect the port brief calls out: the audit row names a route that does not
  exist in this deployment. ``source`` is now a required keyword on
  :func:`dsr.room_templates.create_room` and is built from ``router.prefix`` by
  :func:`_source`, so the two cannot drift.
* **The error mapping is exported, not registered.** FastAPI accepts exception
  handlers on the app object only, so ``api.py`` is not edited; ``EXCEPTION_HANDLERS``
  below is attached by the host.

The read side (``GET /accounts`` and ``GET /rooms``) is here rather than in
``dsr.room_templates``, because that module's remit is the template-to-room
wizard — catalogue, slug rules, and the create. Those two are the routes the
wizard reads from and the Rooms list the wizard returns to, which is HTTP
vocabulary, and keeping them with the routes they serve means the domain module
does not need to know a URL exists. WF-005 will have its own read side over the
same records; two readers of one schema-flexible collection is the arrangement
the storage layer is designed for.

One thing the port could **not** carry over, and it is a finding rather than an
omission: the researched *Rooms* page in the Launchpad console is a core page,
and the branch rewrote ``frontend/src/pages/Rooms.jsx`` to put the wizard and the
list on it. That file is core, so a feature may not own it. The wizard and the
list are therefore this feature's own page, reachable from the nav because the
host globs ``src/features/*/index.jsx``. Putting a *New room* affordance on the
core rooms page instead is a one-line change in a file this feature may not
touch, and it belongs to whoever owns that page.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.room_templates import (
    ACCOUNT,
    DEFAULT_STATUS,
    ROOM,
    SCAN_LIMIT,
    STATUSES,
    RoomCreationError,
    create_room,
    list_templates,
    slugify,
)
from dsr.store import RecordStore

__all__ = ["FEATURE", "router", "EXCEPTION_HANDLERS", "seed", "list_accounts", "list_rooms"]

FEATURE = {
    "id": "wf-001-room-templates",
    "ticket": "WF-001",
    "name": "Create a room from a template",
    "description": (
        "A three-step wizard: bind the room to an account, pick a template, name it. "
        "The room and the site it is bound to are written in one transaction, and "
        "every other field a team sends is stored as-is."
    ),
    "nav": [{"id": "create-room", "label": "Create a room"}],
}

router = APIRouter(prefix="/api/wf-001", tags=["WF-001"])


def _source(path: str) -> str:
    """The route a write came from, built from this router's own prefix.

    An audit row that names a route nobody can call is a lie in the one table
    this product promises is complete, so the string is derived rather than
    written down twice.
    """
    return f"{router.prefix}{path}"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _room_creation_error(request: Request, exc: RoomCreationError) -> JSONResponse:
    """Map a refused request to its status and a machine-readable code.

    The wizard needs the code to say which step to send the operator back to;
    the message is written for the operator, not for a log reader.
    """
    return JSONResponse(status_code=exc.status, content={"error": exc.code, "detail": exc.message})


EXCEPTION_HANDLERS = {RoomCreationError: _room_creation_error}


# --------------------------------------------------------------------------- #
# Read side: the wizard's two lookups and the Rooms list
# --------------------------------------------------------------------------- #


def list_accounts(
    store: RecordStore, *, query: str | None = None, limit: int = 100, offset: int = 0
) -> dict[str, Any]:
    """Accounts a room can be bound to, newest first.

    Accounts are ordinary schema-flexible records, so the record is returned
    as-is: a team that stores domains, tiers, or member graphs gets them back
    without this function knowing they exist.
    """
    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))
    records = store.list(ACCOUNT, limit=SCAN_LIMIT, order_by="created_at", descending=False)

    needle = (query or "").strip().lower()
    if needle:
        records = [
            record
            for record in records
            if needle in str(record["data"].get("name", "")).lower()
            or needle in str(record["data"].get("domain", "")).lower()
        ]

    records.sort(key=lambda record: record["created_at"], reverse=True)
    return {
        "accounts": records[offset : offset + limit],
        "count": len(records[offset : offset + limit]),
        "total": len(records),
    }


def _searchable_text(record: Any) -> str:
    data = record.get("data") or {}
    return " ".join(
        str(data.get(key, "")) for key in ("name", "friendly_url", "account_name", "template_name")
    ).lower()


def list_rooms(
    store: RecordStore,
    *,
    status: str = DEFAULT_STATUS,
    query: str | None = None,
    account_id: str | None = None,
    template_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """The Rooms list: one status at a time, plus an optional free-text search.

    ``status`` defaults to ``active`` because that is what the researched list
    shows by default; pass ``all`` to include archived rooms. Exact filters go
    through the dynamic index; the free-text ``query`` is matched in Python
    because the index answers exact matches only, so it is applied to at most
    ``SCAN_LIMIT`` records. That bounded limitation is deliberate: the generic
    ``/api/records/room?where=...`` route remains the way to query arbitrary
    JSON paths, and this route speaks only the list's own filters.
    """
    normalised_status = (status or DEFAULT_STATUS).strip().lower()
    if normalised_status not in (*STATUSES, "all"):
        raise RoomCreationError(
            "invalid_status", f"status must be one of {list(STATUSES)} or 'all', got {status!r}"
        )

    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))

    exact: dict[str, Any] = {}
    if normalised_status != "all":
        exact["status"] = normalised_status
    if account_id:
        exact["account_id"] = account_id
    if template_id:
        exact["template_id"] = template_id

    if exact:
        records = store.find(ROOM, exact, limit=SCAN_LIMIT)
    else:
        records = store.list(ROOM, limit=SCAN_LIMIT, order_by="created_at", descending=False)

    needle = (query or "").strip().lower()
    if needle:
        records = [record for record in records if needle in _searchable_text(record)]

    ordered = sorted(records, key=lambda record: record["created_at"], reverse=True)
    page = list(ordered[offset : offset + limit])
    return {
        "rooms": page,
        "count": len(page),
        "total": len(ordered),
        "status": normalised_status,
        "limit": limit,
        "offset": offset,
    }


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@router.get("/room-templates")
def room_templates(store: RecordStore = StoreDep) -> dict[str, Any]:
    """Wizard step 2: the templates an operator can pick.

    The shipped catalogue overlaid with any ``template`` records in the store,
    so a fresh deployment has something to select and a team can still add or
    retune a template with a plain ``POST /api/records/template`` and no
    redeploy.
    """
    templates = list_templates(store)
    return {"templates": templates, "count": len(templates)}


@router.get("/accounts")
def accounts(
    q: str | None = Query(default=None, description="case-insensitive substring of name or domain"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Wizard step 1: the accounts a room can be bound to.

    Pass-through, not a projection: an account is an ordinary schema-flexible
    record, so a team's own fields survive the round trip untouched.
    """
    return list_accounts(store, query=q, limit=limit, offset=offset)


@router.post("/rooms", status_code=201)
def create_room_endpoint(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    request_id: str | None = Query(
        default=None, description="read this submission's writes back as a unit"
    ),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Wizard steps 1-3: create a room from an account and a template.

    Required: ``name``, ``account_id``, ``template_id``. Optional:
    ``friendly_url`` (derived from the name when omitted), ``created_by``,
    ``created_by_username``. **Any other field is stored and indexed as-is**,
    which is how a team ships a new room attribute without a migration.
    """
    body = dict(payload)
    for consumed in (
        "name",
        "account_id",
        "template_id",
        "friendly_url",
        "created_by",
        "created_by_username",
    ):
        body.pop(consumed, None)

    return create_room(
        store,
        name=payload.get("name"),
        account_id=payload.get("account_id"),
        template_id=payload.get("template_id"),
        friendly_url=payload.get("friendly_url"),
        source=_source("/rooms"),
        actor=actor,
        created_by=payload.get("created_by"),
        created_by_username=payload.get("created_by_username"),
        extra=body,
        request_id=request_id,
    )


@router.get("/rooms")
def list_rooms_endpoint(
    status: str = Query(default=DEFAULT_STATUS, description="active | archived | all"),
    q: str | None = Query(
        default=None, description="substring of name, friendly URL, account, or template"
    ),
    account_id: str | None = Query(default=None),
    template_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The Rooms list, filtered to one status at a time (defaults to Active)."""
    return list_rooms(
        store,
        status=status,
        query=q,
        account_id=account_id,
        template_id=template_id,
        limit=limit,
        offset=offset,
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #
#
# The branch rewrote ``backend/seed.py`` to build the demo rooms through
# ``create_room``. That file is shared, and ten of the first twelve features
# rewrote it purely to add their own demo rows. The seeder calls the hook below
# instead, so the demo travels with the feature that needs it.
#
# The core seeder's own ``room`` records carry no ``status`` field, so the Rooms
# list - which filters on one status at a time and defaults to Active - would show
# an empty page. A feature whose page is empty in the demo is a feature nobody can
# review, so the hook creates accounts and rooms through exactly the code path the
# API uses. There is no second implementation to drift.

#: Wizard step 1 selects one of these. The membership graph a deployment would
#: sync from a directory lives here as ordinary schema-flexible fields.
ACCOUNTS: tuple[dict[str, Any], ...] = (
    {
        "name": "Northwind Traders",
        "domain": "northwind.example",
        "tier": "enterprise",
        "member_count": 6,
        "contact_count": 4,
    },
    {
        "name": "Contoso Health",
        "domain": "contoso.example",
        "tier": "enterprise",
        "member_count": 4,
        "contact_count": 3,
    },
    {
        "name": "Fabrikam Logistics",
        "domain": "fabrikam.example",
        "tier": "mid-market",
        "member_count": 3,
        "contact_count": 2,
    },
)

#: ``(account, template, name, friendly_url, status)``. The last row is archived,
#: so the researched *Status* filter has something to switch to; the third is
#: given an explicit operator-typed URL and the others are derived from the name,
#: so both branches of the friendly-URL rule are visible without interaction.
DEMO_ROOMS: tuple[tuple[str, str, str, str | None, str], ...] = (
    (
        "Northwind Traders",
        "tpl_guided_evaluation",
        "Northwind Traders - Enterprise Evaluation",
        None,
        "active",
    ),
    (
        "Contoso Health",
        "tpl_technical_review",
        "Contoso Health - Security Review",
        "contoso-security",
        "active",
    ),
    ("Fabrikam Logistics", "tpl_standard", "Fabrikam Logistics - Renewal", None, "active"),
    ("Northwind Traders", "tpl_standard", "Northwind Traders - Pilot (2025)", None, "archived"),
)


def seed(db, context: dict[str, Any]) -> str:
    """Create demo accounts and rooms the wizard itself would have created.

    Every write goes through the audited database, so seeding twice produces a
    second complete set of audit rows rather than overwriting the first. The
    archived row is archived *after* it is created rather than at create time,
    because ``extra`` is merged first precisely so that a caller cannot spoof the
    status a room is created with.
    """
    store = RecordStore(db)
    accounts = {
        spec["name"]: store.create(ACCOUNT, spec, actor="sam", source="seed")["id"]
        for spec in ACCOUNTS
    }

    for account, template_id, name, friendly_url, status in DEMO_ROOMS:
        room = create_room(
            store,
            name=name,
            account_id=accounts[account],
            template_id=template_id,
            source="seed",
            actor="dana",
            created_by="dana",
            friendly_url=friendly_url,
            request_id=f"seed-wf001-{slugify(name)}",
        )
        if status != DEFAULT_STATUS:
            db.update(room["id"], {"status": status}, actor="dana", source="seed")

    return f"{len(ACCOUNTS)} accounts, {len(DEMO_ROOMS)} rooms (1 archived)"
