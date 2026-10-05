"""WF-074: scope visibility to an audience with per-item permissions.

A build from a researched specification, not a port. The specification is
``docs/research/digital-sales-room-workflows/wf/WF-074.md``, quoted in full in issue 149, and
the rules live in :mod:`dsr.audience_permissions`. This module is the three things a feature
contributes and the three things it must never contribute.

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
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``. A test enforces it.
* No hand-written ``source=`` string. :func:`_source` builds every one from :data:`router`, so
  the audit row names the route that actually served the write.

The two scopes, and the one refusal between them
-------------------------------------------------

The specification's extensibility note calls the two ACL scopes "deliberately distinct" and
says their conflict "is resolved by an explicit, documented rule, which is the pattern worth
copying". So both live here, on one prefix, and the rule is a refusal rather than a
precedence order: a link that belongs to a group refuses a link override with ``422``, because
"their group determines visibility; switch the link to ``audience_type: \\"general\\"`` first."

The refusal is deliberate where the vendor's CLI is loose. The CLI page says overrides on a
group link "are ignored; the group's permissions take precedence", and the OpenAPI description
of the same endpoint says they are rejected. The CLI sentence describes a client that does not
report the rejection. An ignored override would leave a rep editing a grid that is not the one
in force, so this build refuses and names the way out.

Two empty states on a general link
----------------------------------

A link that was never scoped and a link whose scope was cleared both store zero rows, and the
vendor's CLI gives them opposite outcomes: "With no overrides, viewers see the full dataroom",
and of ``--clear``, "Remove all overrides and hide every item". The engine therefore carries a
marker on the link row, derived on the first link-permission write and never accepted from a
caller. Jev chose the marker over letting absence always mean the full room, and over writing
a full-room scope during a read, at confidence 1.00, audit ``jev-20261005T051919-25896-59873``.

The status codes here are this product's own
--------------------------------------------

The specification documents no HTTP status codes, so each one below follows the product's
conventions rather than inventing a codebook. 400 for a payload this workflow will not accept,
because the sender can fix it, and that includes an ``item_type`` that is not one of the two the
source names. 404 for a group, member, link or room that does not exist. 422 for a scope conflict
alone. The 400 and the 422 are different on purpose: a malformed domain or an unknown item type
is a sender error with an obvious fix, and a link override on a group link is a well-formed
request whose answer is no.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.audience_permissions import inferences, rules, vocabulary as vocab
from dsr.audience_permissions.engine import (
    ALLOW_DOWNLOAD_FIELD,
    GROUP_ID_FIELD,
    NAME_FIELD,
    NO_ITEMS_NOTE,
    ROOM_COLLECTION,
    SCOPE_STATE_LABELS,
    AudiencePermissionsEngine,
    RoomNotFound,
)
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-074-scope-visibility-to-an-audience-with-per-item",
    "ticket": "WF-074",
    "name": "Scope visibility to an audience with per-item permissions",
    "description": (
        "Create a named audience, list its members and its email domains, and grant it access "
        "item by item with separate view and download flags on every document and folder. The "
        "default is deny: an audience sees nothing until it is granted something, and folders "
        "above a granted item are opened so the tree stays navigable. Changes to members or "
        "permissions apply to the existing link immediately, with no re-sharing. A one-off "
        "audience is the same permissions set written straight onto a single link."
    ),
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several workflows
#: already share (``/api/library``, ``/api/publishing``, ``/api/access``).
router = APIRouter(prefix="/api/wf-074", tags=["WF-074"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is a domain
    function hardcoding a URL string, which leaves the audit log naming a route the app stopped
    serving. ``tests/test_wf074_http.py`` asserts every source this router can record matches a
    concrete ``(method, path)`` the host mounted.
    """

    return f"{method} {router.prefix}{path}"


def get_engine(store: RecordStore = StoreDep) -> AudiencePermissionsEngine:
    """An :class:`AudiencePermissionsEngine` over the process-wide audited store.

    Per request, for the same reason the rest of this product builds its engine per request: the
    engine holds nothing beyond the store and a clock, so building it here leaves both
    overridable in a test instead of hanging a long-lived object off ``app.state``, which is a
    shared file this feature may not edit.
    """

    return AudiencePermissionsEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# All four types are declared in dsr.audience_permissions and raised by nothing else in the
# product. That is what makes it safe to map them here: the host refuses a second feature
# registering a handler for the same type, and a handler for ValueError or LookupError would
# intercept those exceptions across the whole product.


def _payload_refused(request: Request, exc: rules.AudienceRuleError) -> JSONResponse:
    """400 with a field-keyed map, so each message lands next to the input that caused it."""

    return JSONResponse(
        status_code=400,
        content={
            "error": "audience_payload_refused",
            "detail": str(exc),
            "errors": exc.errors,
        },
    )


def _scope_conflict(request: Request, exc: rules.ScopeConflict) -> JSONResponse:
    """422, and the way out is named.

    :class:`~dsr.audience_permissions.rules.ScopeConflict` subclasses the 400 handler's type, so
    it needs its own registration to reach 422 rather than being caught by the broader one. That
    is not a second opinion about the payload: the payload is fine, and the link's audience is
    what this build refuses to write over.
    """

    return JSONResponse(
        status_code=422,
        content={
            "error": vocab.SCOPE_CONFLICT_REJECTED,
            "detail": str(exc),
            "errors": exc.errors,
            "rule": vocab.SCOPE_CONFLICT_REJECTED,
            "way_out": vocab.SCOPE_CONFLICT_MESSAGE,
        },
    )


def _group_not_found(request: Request, exc: rules.GroupNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such audience in this room."},
    )


def _member_not_found(request: Request, exc: rules.MemberNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such member of that audience."},
    )


def _link_not_found(request: Request, exc: rules.LinkNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such link in this room."},
    )


def _room_not_found(request: Request, exc: RoomNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "error": "not_found",
            "detail": "No such room. An audience hangs on a room's own library rows.",
        },
    )


EXCEPTION_HANDLERS = {
    rules.AudienceRuleError: _payload_refused,
    rules.ScopeConflict: _scope_conflict,
    rules.GroupNotFound: _group_not_found,
    rules.MemberNotFound: _member_not_found,
    rules.LinkNotFound: _link_not_found,
    RoomNotFound: _room_not_found,
}


# --------------------------------------------------------------------------- #
# The board and the research
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(
    room_id: str | None = Query(None), engine: AudiencePermissionsEngine = EngineDep
) -> dict[str, Any]:
    """The board's headline numbers. Reads only.

    The count that matters is ``groups_with_no_permissions``: those are the audiences in the
    shipped state, and "A new group sees **nothing** until you grant permissions" is a state a
    rep has to be able to see rather than infer from a total.
    """

    return engine.summary(room_id)


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every researched term this workflow validates against.

    Served rather than duplicated in the frontend so the grid cannot drift from the rules that
    validate it: the two item types, the two scopes and their write semantics, the three
    membership steps in order, the three size caps, the email gate and the honesty sentences all
    come from the same tables the validator reads.
    """

    return {
        **vocab.vocabulary_payload(),
        "decisions": {"count": inferences.count(), "ids": list(inferences.DECISIONS)},
        "visibility_states_served": inferences.VISIBILITY_STATES,
        "link_scope_states": [
            {"id": key, "meaning": value} for key, value in SCOPE_STATE_LABELS.items()
        ],
        "no_items_note": NO_ITEMS_NOTE,
    }


@router.get("/decisions")
def list_decisions() -> dict[str, Any]:
    """Every judgement call this workflow made, with what it rejected.

    The specification instructs an implementer directly: one "must derive it and record the
    derivation, not assume it". This route is that record, and it is served rather than buried in
    a docstring so a reviewer reads the decision instead of the code.
    """

    return {"count": inferences.count(), "decisions": inferences.describe()}


@router.get("/decisions/{decision_id}")
def read_decision(decision_id: str) -> dict[str, Any]:
    """One judgement call by id, or a 404."""

    decision = inferences.describe_one(decision_id)
    if not decision:
        raise HTTPException(status_code=404, detail="No such recorded decision.")
    return decision


# --------------------------------------------------------------------------- #
# Audiences
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/groups")
def list_room_groups(room_id: str, engine: AudiencePermissionsEngine = EngineDep) -> dict[str, Any]:
    """Every named audience in one room, oldest first."""

    rows = engine.groups(room_id)
    return {
        "room_id": room_id,
        "count": len(rows),
        "groups": rows,
        "default_deny": vocab.SCOPE_DESCRIPTIONS[vocab.SCOPE_GROUP],
        vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
        vocab.LIMITATION_FIELD: vocab.LIMITATION,
    }


@router.post("/rooms/{room_id}/groups", status_code=201)
def create_group(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: AudiencePermissionsEngine = EngineDep,
) -> dict[str, Any]:
    """Create an audience. Mirrors ``POST /v1/datarooms/{id}/groups``.

    The specification's first user-flow step: "Rep opens the dataroom's Groups surface and
    creates a named audience (e.g. 'Co-investors'), optionally listing email domains that count
    as members implicitly."

    Nothing is granted here. A group created with no permissions is the researched starting
    state and the response says so, because "A new group sees **nothing** until you grant
    permissions" is a fact a rep should read rather than discover.
    """

    return engine.create_group(
        room_id, payload, actor=actor, source=_source("POST", "/rooms/{room_id}/groups")
    )


@router.get("/groups/{group_id}")
def read_group(group_id: str, engine: AudiencePermissionsEngine = EngineDep) -> dict[str, Any]:
    """One audience with its counts, its domains and its open-group switch."""

    return engine.read_group(group_id)


@router.patch("/groups/{group_id}")
def update_group(
    group_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: AudiencePermissionsEngine = EngineDep,
) -> dict[str, Any]:
    """Rename an audience, change its domains, or flip its ``allow_all`` switch.

    Membership is not patched here. Adding and removing members are the two calls the source
    names, and a patch that could add a member would make the idempotent "already-present
    members are skipped" behaviour unreachable from one of the two paths into it.
    """

    return engine.update_group(
        group_id, payload, actor=actor, source=_source("PATCH", "/groups/{group_id}")
    )


# --------------------------------------------------------------------------- #
# Members
# --------------------------------------------------------------------------- #


@router.get("/groups/{group_id}/members")
def list_members(group_id: str, engine: AudiencePermissionsEngine = EngineDep) -> dict[str, Any]:
    """Every member of one audience, by address."""

    rows = engine.members(group_id)
    return {
        "group_id": group_id,
        "count": len(rows),
        "members": rows,
        "no_invitations": vocab.NO_INVITATIONS,
        vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
        vocab.LIMITATION_FIELD: vocab.LIMITATION,
    }


@router.post("/groups/{group_id}/members", status_code=201)
def add_members(
    group_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: AudiencePermissionsEngine = EngineDep,
) -> dict[str, Any]:
    """Add member addresses. Mirrors ``POST /v1/datarooms/{id}/groups/{gid}/members``.

    The specification's second user-flow step, and the source is emphatic about both halves:
    "Viewers are created for unknown addresses; already-present members are skipped, so the call
    is idempotent. **No invitation emails are sent.**"

    So the response separates what was added from what was already there, and it reports
    ``invitations_sent: 0`` on every call. A workflow that invites people elsewhere must not be
    able to read this as having invited anybody.

    A 201 rather than a 200, because the call does create members. A rep who added none because
    they were all present still gets a 201 with the counts, which is the idempotent answer rather
    than a refusal.
    """

    body = dict(payload or {})
    addresses = body.get("emails") if "emails" in body else body
    return engine.add_members(
        group_id, addresses, actor=actor, source=_source("POST", "/groups/{group_id}/members")
    )


@router.delete("/groups/{group_id}/members/{member_id}")
def remove_member(
    group_id: str,
    member_id: str,
    actor: str | None = Query(None),
    engine: AudiencePermissionsEngine = EngineDep,
) -> dict[str, Any]:
    """Remove one member. Mirrors ``DELETE .../groups/{gid}/members/{mid}``.

    The guide: "Later changes to the group's permissions or members apply to the existing link
    immediately, no re-sharing." So the response carries ``applies_immediately: true`` and
    ``link_reissued: false``, and the engine returns the address that lost access rather than a
    new link. Membership is re-read on every view, so nothing has to be pushed anywhere.
    """

    return engine.remove_member(
        group_id,
        member_id,
        actor=actor,
        source=_source("DELETE", "/groups/{group_id}/members/{member_id}"),
    )


# --------------------------------------------------------------------------- #
# Group permissions: delta
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/items")
def list_room_items(room_id: str, engine: AudiencePermissionsEngine = EngineDep) -> dict[str, Any]:
    """Every document and folder a permission can point at, folders first.

    Mirrors ``GET /v1/datarooms/{id}/documents`` and ``.../folders``, whose stated job is to
    "resolve ``item_id``s". Those two calls are one response here because a permission entry names
    an item type beside its id, and a grid that could only show documents would hide half of what
    it edits.

    These rows belong to the room's library and this workflow only reads them. A room with
    nothing ingested returns an empty list and :data:`NO_ITEMS_NOTE`, which is a different answer
    from an empty permissions grid and the one a rep most needs told apart.
    """

    rows = engine.items(room_id)
    return {
        "room_id": room_id,
        "count": len(rows),
        "items": rows,
        "item_types": [
            {
                "id": item_type,
                "label": vocab.ITEM_TYPE_LABELS[item_type],
                "collection": vocab.ITEM_TYPE_COLLECTIONS[item_type],
            }
            for item_type in vocab.ITEM_TYPES
        ],
        "no_items_note": None if rows else NO_ITEMS_NOTE,
        vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
        vocab.LIMITATION_FIELD: vocab.LIMITATION,
    }


@router.get("/groups/{group_id}/permissions")
def read_group_permissions(
    group_id: str, engine: AudiencePermissionsEngine = EngineDep
) -> dict[str, Any]:
    """The whole grid: every item in the room and what this audience may do with it.

    The hidden items are returned here, each with the reason it is hidden, because a rep looking
    at a grid needs to know whether nobody granted an item or somebody revoked it. That is what
    :data:`~dsr.audience_permissions.vocabulary.HIDDEN_CAN_VIEW_FALSE` separates from the
    shipped default of no row at all.

    Contrast :func:`read_link_view`, which returns only what the viewer would be served. A grid
    that withheld the hidden items would be useless, and a viewer endpoint that returned them
    would defeat the filter.
    """

    return engine.group_permissions(group_id)


@router.put("/groups/{group_id}/permissions")
def set_group_permissions(
    group_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: AudiencePermissionsEngine = EngineDep,
) -> dict[str, Any]:
    """Upsert the entries sent. Mirrors ``PUT .../groups/{gid}/permissions``.

    The specification's third user-flow step: "Rep grants access item by item - documents and
    folders - with separate **view** and **download** flags. Anything without an entry is
    invisible to that audience."

    Delta semantics, quoted: "Items not listed keep their current state (delta semantics -
    matching the dashboard)." So the response names what this call touched and what it left alone,
    because a rep who granted one document and turned off another in the same call needs to know
    both happened.

    ``PUT`` rather than ``POST`` because the source names the method. The verb is misleading for
    a delta and it is the vendor's own name for it, so it is kept and the semantics are carried
    in the body.
    """

    return engine.set_group_permissions(
        group_id, payload, actor=actor, source=_source("PUT", "/groups/{group_id}/permissions")
    )


# --------------------------------------------------------------------------- #
# Links
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/links")
def list_room_links(
    room_id: str,
    group_id: str | None = Query(None),
    engine: AudiencePermissionsEngine = EngineDep,
) -> dict[str, Any]:
    """Every link in one room, optionally narrowed to one group's links."""

    rows = engine.links(room_id, group_id)
    return {
        "room_id": room_id,
        "group_id": group_id,
        "count": len(rows),
        "links": rows,
        "email_gate_note": vocab.EMAIL_GATE_NOTE,
        vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
        vocab.LIMITATION_FIELD: vocab.LIMITATION,
    }


@router.post("/rooms/{room_id}/links", status_code=201)
def create_link(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: AudiencePermissionsEngine = EngineDep,
) -> dict[str, Any]:
    """Mint one link, scoped to a group or scoped to nothing.

    Mirrors ``POST /v1/links`` with ``audience_type: "group"`` and a ``group_id``, and covers the
    sixth user-flow step: "For a one-off audience, the rep sets the same permissions directly on a
    single link instead of creating a group."

    Email gating is not a field and there is no route that sets it. The guide makes it
    unconditional for a group link, so a stored flag could be set to false on one, and that is
    the one thing the evidence says cannot happen. The response derives it.
    """

    return engine.create_link(
        room_id, payload, actor=actor, source=_source("POST", "/rooms/{room_id}/links")
    )


@router.get("/links/{link_id}")
def read_link(link_id: str, engine: AudiencePermissionsEngine = EngineDep) -> dict[str, Any]:
    """One link, its audience, and which of the three scope states it is in."""

    return engine.read_link(link_id)


@router.get("/links/{link_id}/permissions")
def read_link_permissions(
    link_id: str, engine: AudiencePermissionsEngine = EngineDep
) -> dict[str, Any]:
    """The link's own overrides, and the whole grid beside them.

    Mirrors ``GET /v1/links/{id}/permissions``, and carries the scope state so a caller can tell
    an unscoped link from a cleared one. Both store zero rows and mean opposite things.
    """

    return engine.link_permissions(link_id)


@router.put("/links/{link_id}/permissions")
def set_link_permissions(
    link_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: AudiencePermissionsEngine = EngineDep,
) -> dict[str, Any]:
    """Replace the link's overrides with the payload. Mirrors ``PUT /v1/links/{id}/permissions``.

    Full-replace semantics, quoted: "The complete desired permission state for this link
    (full-replace semantics...) An empty array clears all overrides, which hides every item on the
    link."

    An empty array is the documented clear, so it is accepted rather than refused, and it sets the
    link's scope marker. Without that marker the next read could not tell a cleared link from one
    that was never scoped, and the first would show the full room after the rep hid it.

    A group link is refused with 422 before the body is read, and the way out is named: switch the
    link to ``audience_type: "general"`` first. See the module docstring for why the refusal is
    kept where the vendor's CLI only says overrides are ignored.
    """

    return engine.set_link_permissions(
        link_id, payload, actor=actor, source=_source("PUT", "/links/{link_id}/permissions")
    )


@router.get("/links/{link_id}/view")
def read_link_view(
    link_id: str,
    email: str | None = Query(None),
    engine: AudiencePermissionsEngine = EngineDep,
) -> dict[str, Any]:
    """What this address sees on this link right now, filtered before any bytes.

    The specification's data flow, in one clause: at viewer entry the requester's email is
    matched against memberships, and "the resolved permission set filters the data room tree
    server-side before any bytes are sent."

    So the hidden items are counted and named by reason and never returned. Returning them would
    defeat the filter; dropping them without a count would make an empty room and a fully hidden
    room look the same to the caller.

    A refusal here is a 200 with ``admitted: false``, not a 403. The viewer did nothing wrong:
    the link exists, the answer is that this address is not in the audience, and a viewer screen
    needs the membership step to say so. The spec's own email gate is a gate the requester passes
    before this call, which is why the address arrives as a parameter at all.
    """

    return engine.view(link_id, email)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the states the specification says matter, not just the happy path.

    Every row is produced by calling the real :class:`AudiencePermissionsEngine`, so the demo
    cannot show a shape, an audit row or a grid the HTTP routes would not produce.
    ``source="seed"`` rather than a route string: no route served this, and claiming one would be
    the lie hard rule 4 of the brief exists to prevent.

    The states seeded, and why each is here:

    * an audience with **explicit members and an implicit domain**, granted one folder it may
      browse but not download, so the second flag is a state a reviewer can see rather than a
      claim;
    * a **group link** on that audience, because "Group links are always email-gated" is only
      visible as a link whose audience is a group;
    * a second audience with **no permissions at all**, because "A new group sees **nothing**
      until you grant permissions" is the shipped default and a demo holding only granted
      audiences would misrepresent it;
    * an audience with **``allow_all`` set**, because that short-circuits the email and domain
      checks and the spec calls it out as a short-circuit;
    * a **general link left unscoped**, so the "with no overrides, viewers see the full dataroom"
      behaviour is a row a reviewer can read;
    * a **general link scoped to two items**, where a third item is dropped, so full-replace
      delta semantics are visible as a difference rather than as a claim;
    * a **dangling grant**, naming an item id the room does not have, so the reported-rather-than-
      dropped decision is visible;
    * a **member removed**, so immediate revocation with no re-share is a row rather than a
      promise.

    The return string is ASCII and is asserted encodable by cp1252 in ``tests/test_wf074.py``:
    the seeder prints it to a Windows console, and one RIGHTWARDS ARROW in a recovered feature's
    return string broke the whole seeder.
    """

    store = RecordStore(db)
    now = context["now"]
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return "no rooms in the core dataset; nothing to scope an audience to"

    engine = AudiencePermissionsEngine(store, now=lambda: now)
    room_one = room_ids[0][0]
    room_two = room_ids[1][0] if len(room_ids) > 1 else room_one

    # Rooms are the core dataset's rows and this feature does not create them. The engine only
    # needs one to exist for a group to hang on, so each room named in the context is read back
    # and anything the seeder could not create is skipped loudly rather than faked.
    for room_id in (room_one, room_two):
        if store.get(room_id) is None or store.get(room_id).get("collection") != ROOM_COLLECTION:
            return f"room {room_id} is not in the core dataset; nothing to scope an audience to"

    # The item rows are the room's own library rows, read and never written. A fresh demo
    # database has documents from the core seeder, and folders only if the library workflow
    # already ran, so the item list is read back rather than assumed.
    items = engine.items(room_one)
    documents = [row for row in items if row["item_type"] == vocab.ITEM_TYPE_DOCUMENT]
    folders = [row for row in items if row["item_type"] == vocab.ITEM_TYPE_FOLDER]

    # 1. An audience with explicit members and an implicit domain, granted a browse-only folder.
    investors = engine.create_group(
        room_one,
        {
            NAME_FIELD: "Co-investors",
            vocab.ALLOW_ALL: False,
            # Both spellings of the same domain, so the normalisation is a state a reviewer sees:
            # "Accepts bare (acme.com) or @-prefixed (@acme.com) domains".
            vocab.DOMAINS_FIELD: ["sequoia.example", "@HALCYON.example", "sequoia.example"],
        },
        actor="dana",
        source="seed",
    )
    engine.add_members(
        investors["id"],
        ["jane@sequoia.example", "ops@northwind.example", "ops@northwind.example"],
        actor="dana",
        source="seed",
    )

    if documents:
        engine.set_group_permissions(
            investors["id"],
            {
                "permissions": [
                    {
                        "item_id": documents[0]["item_id"],
                        "item_type": documents[0]["item_type"],
                        vocab.CAN_VIEW: True,
                        vocab.CAN_DOWNLOAD: True,
                    }
                ]
            },
            actor="dana",
            source="seed",
        )
    if folders:
        # The specification's own example: a folder an audience may browse but not download. The
        # second flag is the reason the entry has two booleans.
        engine.set_group_permissions(
            investors["id"],
            {
                "permissions": [
                    {
                        "item_id": folders[0]["item_id"],
                        "item_type": folders[0]["item_type"],
                        vocab.CAN_VIEW: True,
                        vocab.CAN_DOWNLOAD: False,
                    }
                ]
            },
            actor="dana",
            source="seed",
        )

    # 2. A group link on that audience. "Group links are always email-gated" is only visible as
    #    a link whose audience is a group.
    engine.create_link(
        room_one,
        {
            NAME_FIELD: "Co-investors review",
            vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP,
            GROUP_ID_FIELD: investors["id"],
            ALLOW_DOWNLOAD_FIELD: True,
        },
        actor="dana",
        source="seed",
    )

    # 3. An audience with no permissions at all: the shipped default, "A new group sees nothing
    #    until you grant permissions", and the state a demo of granted audiences would hide.
    reviewers = engine.create_group(
        room_two,
        {NAME_FIELD: "Data room reviewers", vocab.DOMAINS_FIELD: ["contoso.example"]},
        actor="dana",
        source="seed",
    )

    # 4. An audience that allows everyone, because that short-circuits the two narrow checks.
    engine.create_group(
        room_two,
        {NAME_FIELD: "Open house", vocab.ALLOW_ALL: True},
        actor="dana",
        source="seed",
    )
    opened = engine.create_group(
        room_two,
        {NAME_FIELD: "Open house", vocab.ALLOW_ALL: True},
        actor="dana",
        source="seed",
    )
    engine.add_members(
        opened["id"],
        ["observer@vantage.example"],
        actor="dana",
        source="seed",
    )

    # 5. A member removed, so immediate revocation with no re-share is a row rather than a
    #    promise. It is a member this seed created, held from the call that added it, so the
    #    removal cannot land on a row some other workflow's seed wrote.
    revoked = engine.add_members(
        reviewers["id"],
        ["former.analyst@vantage.example"],
        actor="dana",
        source="seed",
    )
    removed = 0
    if revoked["added"]:
        engine.remove_member(
            reviewers["id"],
            engine.members(reviewers["id"])[0]["id"],
            actor="dana",
            source="seed",
        )
        removed = 1

    # 6. A general link left unscoped: "With no overrides, viewers see the full dataroom."
    engine.create_link(
        room_one,
        {
            NAME_FIELD: "Room overview, unscoped",
            vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GENERAL,
            ALLOW_DOWNLOAD_FIELD: False,
        },
        actor="dana",
        source="seed",
    )

    # 7. A general link scoped to two items, where the first scope is replaced so the drop is
    #    visible as a difference rather than as a claim.
    scoped = engine.create_link(
        room_one,
        {
            NAME_FIELD: "Teaser only",
            vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GENERAL,
            ALLOW_DOWNLOAD_FIELD: True,
        },
        actor="dana",
        source="seed",
    )
    if len(documents) >= 2:
        engine.set_link_permissions(
            scoped["id"],
            {
                "permissions": [
                    {
                        "item_id": documents[0]["item_id"],
                        "item_type": documents[0]["item_type"],
                        vocab.CAN_VIEW: True,
                        vocab.CAN_DOWNLOAD: True,
                    },
                    {
                        "item_id": documents[1]["item_id"],
                        "item_type": documents[1]["item_type"],
                        vocab.CAN_VIEW: True,
                        vocab.CAN_DOWNLOAD: False,
                    },
                ]
            },
            actor="dana",
            source="seed",
        )
        engine.set_link_permissions(
            scoped["id"],
            {
                "permissions": [
                    {
                        "item_id": documents[0]["item_id"],
                        "item_type": documents[0]["item_type"],
                        vocab.CAN_VIEW: True,
                        vocab.CAN_DOWNLOAD: False,
                    }
                ]
            },
            actor="dana",
            source="seed",
        )

    # 8. A dangling grant, naming an item id the room does not have. The recorded decision is
    #    that it is kept and reported, because it is a fact about the room rather than an error
    #    in the grant, so the demo has to show the report.
    if documents:
        engine.set_group_permissions(
            investors["id"],
            {
                "permissions": [
                    {
                        "item_id": "ddoc_not_in_this_room",
                        "item_type": vocab.ITEM_TYPE_DOCUMENT,
                        vocab.CAN_VIEW: True,
                        vocab.CAN_DOWNLOAD: False,
                    }
                ]
            },
            actor="dana",
            source="seed",
        )

    # The counts are read back from the engine rather than written out here, so the line the
    # seeder prints cannot describe a state the seed did not produce. Every figure is taken with
    # no room filter, which makes them counts of everything this seed created.
    board = engine.summary()
    grid = engine.group_permissions(investors["id"])
    link_grid = engine.link_permissions(scoped["id"])
    scope_states = {row["scope_state"] for row in engine.links()}

    return (
        f"{board['groups']} audiences ({board['members']} member rows, "
        f"{board['open_groups']} open to anyone, {board['groups_with_no_permissions']} granted "
        f"nothing yet); {board['permissions']} group permission rows across "
        f"{len(grid['items'])} item(s) in the room, {len(grid['dangling'])} dangling; "
        f"{board['links']} links ({board['group_links']} group-scoped, "
        f"{board['general_links']} general) in states {', '.join(sorted(scope_states))}; "
        f"{link_grid['granted']} item(s) granted on the scoped general link; "
        f"{removed} member(s) revoked with no re-share"
    )
