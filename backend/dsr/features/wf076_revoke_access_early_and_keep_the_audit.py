"""WF-076: revoke access early, and keep the row that proves it.

Six researched operations on a room's access graph, and one guarantee that
holds across all of them: **the URL stops resolving now, and the record that
says why survives the cut.** That is what makes an early revocation defensible
afterwards rather than merely fast.

The domain rules live in :mod:`dsr.revocation`, beside the feature rather than
inside it, so the rules are testable without a request and the folder stays
small. This module is the HTTP layer: a prefixed router, the refusals mapped to
statuses, the demo rows, and nothing else.

Two conventions the house already uses, kept here for consistency:

* ``source`` is built by :func:`_source` from ``router.prefix``, never written
  out. A domain method that hardcoded a URL string would be the defect the
  contract names by hand - a feature whose audit log kept recording a path the
  app had stopped serving - so ``source`` is a required argument on every write
  and the route passes it in.
* Every refusal is one type, mapped to one status, in a single handler. 404 for
  something that does not exist, 409 for something already in that state, 409
  again for a frozen dataroom (the API is refusing the *state*, not the
  request), 400 for a malformed body or a missing confirmation, 422 for a
  cross-team attach.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.revocation import (
    ALL_COLLECTIONS,
    CACHED_COPY_RECALL,
    CASCADE_COLLECTIONS,
    DEFAULT_ACTOR,
    FROZEN_REFUSALS,
    GONE_CASCADE_GROUP,
    GONE_CASCADE_ROOM,
    GONE_MEMBER_REMOVED,
    GRACE_PERIOD_MINUTES,
    GRANTS,
    GROUPS,
    HARD_DELETE,
    IRREVERSIBLE,
    LINKS,
    MEMBERS,
    NOT_FOUND,
    PERMISSIONS,
    RESOLVES,
    REVOCATION_CAUSES,
    TARGET_GROUP,
    TARGET_ROOM,
    TARGETS,
    VIEWERS,
    RevocationBadRequest,
    RevocationConflict,
    RevocationCrossTeam,
    RevocationFrozen,
    RevocationNotFound,
    RevocationRefusal,
    inferences as _inferences,
    payload as fields,
)
from dsr.revocation.engine import Revocation
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-076-revoke-access-early-and-keep-the-audit",
    "ticket": "WF-076",
    "name": "Revoke access early and keep the audit row",
    "description": (
        "Cut a share link, an audience, one buyer, or an entire dataroom's access graph, and "
        "keep every revoked row so the cut is defensible afterwards. Revocation is request-time: "
        "the URL stops resolving on the next request, with no grace period and no way to undo it."
    ),
}

router = APIRouter(prefix="/api/wf-076", tags=["WF-076"])


def engine(store: RecordStore) -> Revocation:
    """The engine, told which prefix it is mounted under so it can find its own audit rows."""
    return Revocation(store, source_prefix=router.prefix)


def _source(verb: str, path: str) -> str:
    """The audit ``source`` for a write served by this router.

    Built from ``router.prefix`` rather than written out, because hard rule 4 of
    the brief is exactly this: the audit row must name the route that served the
    write, and the route table is the only thing that can say what that is. The
    concrete ids are included so an operator can grep for one revoke, and the
    test that guards this matches each recorded source back against the routes
    the host actually mounted.
    """
    return f"{verb} {router.prefix}{path}"


def _actor(request: Request) -> str:
    return str(request.headers.get("X-Actor") or DEFAULT_ACTOR)


_STATUS = {
    RevocationNotFound: (404, "not_found"),
    RevocationConflict: (409, "conflict"),
    RevocationFrozen: (409, "dataroom_frozen"),
    RevocationBadRequest: (400, "bad_request"),
    RevocationCrossTeam: (422, "cross_team_refused"),
}


def _refusal(request: Request, exc: RevocationRefusal) -> JSONResponse:
    """One handler for every refusal, mapped to the status each one means."""
    status, code = _STATUS.get(type(exc), (422, "refused"))
    return JSONResponse(status_code=status, content={"error": code, "detail": str(exc)})


EXCEPTION_HANDLERS = {RevocationRefusal: _refusal}


# --------------------------------------------------------------------------- #
# What this workflow claims, readable without a store.
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """The researched operations, the quote behind each, and the guarantee.

    Exposed so a reviewer can check what the feature claims against the code
    that enforces it, and so the page renders the rules rather than restating
    them.
    """
    return {
        "collections": {
            LINKS: "Papermark Link: a share link, and the row a revoke marks rather than removes",
            GROUPS: "DataroomGroup: an audience inside a room",
            MEMBERS: "DataroomGroupMember: one buyer in one group",
            PERMISSIONS: "DataroomGroupPermission: view / download for one item in one group",
            VIEWERS: "the underlying viewer a membership points at, kept when the membership goes",
            GRANTS: "DataroomDocument join row: detach deletes this and nothing else",
        },
        "operations": {
            "revoke_link": {
                "route": f"POST {router.prefix}/rooms/{{room_id}}/links/{{link_id}}/revoke",
                "effect": "the public URL stops resolving on the next request",
                "row": "soft-deleted and kept",
                "quote": (
                    "Soft-deletes the link. The public URL stops resolving immediately; the row "
                    "is kept in the database for audit. If the link used a custom-domain slug, "
                    "the slug is renamed so the original can be reused."
                ),
            },
            "delete_group": {
                "route": f"POST {router.prefix}/rooms/{{room_id}}/groups/{{group_id}}/delete",
                "effect": "the group, its memberships, its permissions and every link pointing at it",
                "row": "soft-deleted, one transaction, one audit row naming all of them",
                "quote": (
                    "Deletes the group, its memberships, its permissions, AND every share link "
                    "pointing at it - active group links stop resolving immediately."
                ),
                "confirmation": "confirm must equal the group id",
            },
            "remove_member": {
                "route": (
                    f"POST {router.prefix}/rooms/{{room_id}}/groups/{{group_id}}/members"
                    "/{member_id}/remove"
                ),
                "effect": "only that buyer's membership; the link and everyone else are untouched",
                "row": "the membership is soft-deleted; the viewer is kept",
                "quote": (
                    "The underlying viewer is kept - only their membership in this group is "
                    "removed."
                ),
            },
            "set_permissions": {
                "route": f"POST {router.prefix}/rooms/{{room_id}}/groups/{{group_id}}/permissions",
                "effect": "hide an item by turning view and download off",
                "row": "an update to the permission row",
                "quote": "hide an item (view off, download off)",
            },
            "attach": {
                "route": f"POST {router.prefix}/rooms/{{room_id}}/documents",
                "effect": "attach a team-library document to this dataroom",
                "quote": (
                    "The document must belong to the same team as the dataroom; cross-team "
                    "attaches are refused. Frozen datarooms refuse new attachments."
                ),
            },
            "detach": {
                "route": f"POST {router.prefix}/rooms/{{room_id}}/documents/{{document_id}}/detach",
                "effect": "detach from this dataroom; the library document and its other "
                "attachments are left intact",
                "row": "the join row is soft-deleted and kept",
                "quote": (
                    "Deleting the document from a dataroom is a join-row delete; the team-library "
                    "document and its attachments to other datarooms are left intact."
                ),
            },
            "purge": {
                "route": f"POST {router.prefix}/rooms/{{room_id}}/purge",
                "effect": "cascade to every link, group, membership, permission and join row",
                "documents": "the team library survives",
                "quote": (
                    "Deletion cascades to every link and folder and is unrecoverable; documents "
                    "stay in the team library."
                ),
                "confirmation": "confirm must equal the room id",
            },
        },
        "guarantee": {
            "when": "request-time, not scheduled",
            "grace_period_minutes": GRACE_PERIOD_MINUTES,
            "cached_copy_recall": CACHED_COPY_RECALL,
            "row_kept": True,
            "hard_delete": HARD_DELETE,
            "reversible": IRREVERSIBLE,
            "quote": (
                "Revocation is request-time, not scheduled - in-flight viewers are cut on their "
                "next request. There is no grace period and no cached-copy recall: the guarantee "
                "is 'the public URL stops resolving immediately.'"
            ),
        },
        "resolution_reasons": {
            RESOLVES: "the link is live and the requester may have it",
            NOT_FOUND: "no live link holds that slug",
            "revoked": "the link was revoked directly",
            GONE_CASCADE_GROUP: "the group was deleted",
            GONE_CASCADE_ROOM: "the dataroom was purged",
            GONE_MEMBER_REMOVED: "the requester is not a live member of the group",
        },
        "targets": list(TARGETS),
        "revocation_causes": list(REVOCATION_CAUSES),
        "cascade_collections": list(CASCADE_COLLECTIONS),
        "all_collections": list(ALL_COLLECTIONS),
        "frozen_refusals": sorted(FROZEN_REFUSALS),
        "frozen_rule": (
            "Frozen datarooms refuse new attachments, detaches and moves. Revocation is not in "
            "that list, so a frozen room can still have its access cut."
        ),
        "irreversible": {
            "delete_group": True,
            "purge": True,
            "revoke_link": True,
            "note": (
                "None of the three can be undone through this API and there is no restore route. "
                "The rows survive, because keeping the evidence is the point; reissuing access is "
                "a different act, and it means issuing a new link."
            ),
        },
        "slug_rule": (
            "A slug is held by a live link. Revoking a link on a custom domain renames its slug to "
            "a tombstone so the original becomes reusable; a cascaded link simply stops being live."
        ),
    }


@router.get("/inferences")
def inferences() -> dict[str, Any]:
    """Every point where the research is silent, and the reading this build took."""
    return _inferences.describe()


@router.get("/summary")
def summary(store: RecordStore = StoreDep) -> dict[str, Any]:
    """Counts per collection and per room, from the records rather than a counter."""
    return engine(store).summary()


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/state")
def room_state(room_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Everything a UI needs to render this room's access, derived.

    ``available_actions`` comes from the same freeze rule the write path
    enforces, so the page cannot offer a button the route would refuse.
    """
    return engine(store).state(room_id)


@router.get("/rooms/{room_id}/links")
def list_links(
    room_id: str,
    include_revoked: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=1000),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Share links in a room. Revoked ones are readable, which is the point."""
    eng = engine(store)
    # Checked explicitly rather than left to the collection read: a room that does
    # not exist and a room with no links are different answers, and only one of
    # them is worth a 404.
    eng.room(room_id)
    rows = eng.links(room_id, include_revoked=include_revoked)[:limit]
    return {
        "room_id": room_id,
        "include_revoked": include_revoked,
        "count": len(rows),
        "links": [_link_view(eng, room_id, row) for row in rows],
    }


def _link_view(eng: Revocation, room_id: str, row: dict[str, Any]) -> dict[str, Any]:
    """One link, with the two things a rep needs beside it: is it live, and is it gated.

    ``resolves`` is deliberately absent here. A group link only resolves for a
    live member, so asking "does this resolve" without saying who is asking
    would report every group link as dead. ``GET .../resolve`` is where that
    question is answered honestly.
    """
    data = fields(row)
    live = row.get("deleted_at") is None
    return {
        "id": row.get("id"),
        "room_id": room_id,
        "slug": data.get("slug"),
        "original_slug": data.get("original_slug"),
        "label": data.get("label"),
        "target": data.get("target"),
        "group_id": data.get("group_id"),
        "domain": data.get("domain"),
        "custom_domain": bool(data.get("custom_domain")),
        "issued_at": data.get("issued_at"),
        "live": live,
        "gated_on_membership": data.get("target") == TARGET_GROUP,
        "revoked": bool(data.get("revoked")) or not live,
        "revoked_at": data.get("revoked_at"),
        "revoked_by": data.get("revoked_by"),
        "revoked_via": data.get("revoked_via"),
        "revocation_reason": data.get("revocation_reason"),
        "slug_released": bool(data.get("slug_released")),
        "reversible": IRREVERSIBLE,
    }


@router.get("/rooms/{room_id}/links/{link_id}")
def get_link(
    room_id: str,
    link_id: str,
    include_revoked: bool = Query(default=True),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """One link, plus the audit trail written for it."""
    eng = engine(store)
    row = eng.link(room_id, link_id, include_revoked=include_revoked)
    return {**_link_view(eng, room_id, row), "audit": eng.retained(room_id, link_id)["audit"]}


@router.get("/rooms/{room_id}/groups")
def list_groups(room_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Audiences in a room, with the counts a delete would take with them."""
    eng = engine(store)
    eng.room(room_id)
    rows = eng.groups(room_id)
    return {
        "room_id": room_id,
        "count": len(rows),
        "groups": [eng.group_view(room_id, row["id"]) for row in rows],
    }


@router.get("/rooms/{room_id}/groups/{group_id}")
def get_group(room_id: str, group_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """One group with its memberships, permissions and links."""
    return engine(store).group_view(room_id, group_id)


@router.get("/rooms/{room_id}/viewers")
def list_viewers(room_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """The underlying viewers.

    Read this after removing a membership: the membership goes, these rows stay.
    That difference is the researched outcome, so it gets its own route rather
    than being something a reviewer has to infer from a cascade's return value.
    """
    eng = engine(store)
    eng.room(room_id)
    memberships = eng.members(room_id)
    viewer_ids = {str(fields(m).get("viewer_id") or "") for m in memberships}
    rows = eng.viewers(room_id)
    return {
        "room_id": room_id,
        "count": len(rows),
        "viewers": [
            {
                **row,
                "memberships": sum(
                    1 for m in memberships if fields(m).get("viewer_id") == row["id"]
                ),
                "in_any_group": row["id"] in viewer_ids,
            }
            for row in rows
        ],
    }


@router.get("/rooms/{room_id}/documents")
def list_documents(
    room_id: str,
    include_detached: bool = Query(default=True),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The dataroom-to-library join rows, attached and detached.

    A detached row is shown rather than hidden: the detach is a join-row delete
    that keeps the row for audit, so the history of what this room has shown is
    readable here.
    """
    eng = engine(store)
    eng.room(room_id)
    rows = eng.grants(room_id, include_detached=include_detached)
    return {
        "room_id": room_id,
        "include_detached": include_detached,
        "count": len(rows),
        "documents": [
            {
                **row,
                "attached": row.get("deleted_at") is None,
                "document_kept": store.get(str(fields(row).get("document_id") or "")) is not None,
            }
            for row in rows
        ],
    }


@router.get("/rooms/{room_id}/resolve")
def resolve(
    room_id: str,
    slug: str = Query(description="the public slug the buyer is holding"),
    viewer: str | None = Query(default=None, description="who is asking"),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Does this public URL still work, for this requester, right now?

    The researched guarantee as an endpoint: "the public URL stops resolving
    immediately". Call it before and after a revoke and the answer changes on the
    revoke, not on a timer.
    """
    eng = engine(store)
    eng.room(room_id)
    return eng.resolve(room_id, slug, viewer=viewer)


@router.get("/rooms/{room_id}/slugs/{slug}")
def slug_status(room_id: str, slug: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Is this slug free to issue?

    The researched consequence of a revoke: "the slug is renamed so the original
    can be reused."
    """
    eng = engine(store)
    eng.room(room_id)
    return {
        "room_id": room_id,
        "slug": slug,
        "available": eng.slug_available(room_id, slug),
        "held_by": [row["id"] for row in eng.links_with_slug(room_id, slug)],
    }


@router.get("/rooms/{room_id}/revocations/{record_id}")
def retained_row(room_id: str, record_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """A revoked record and the audit trail that describes it.

    After a revoke the ordinary read is gone and this is what remains: the row,
    marked, and the audit entry written in the same transaction as the change.
    """
    return engine(store).retained(room_id, record_id)


@router.get("/rooms/{room_id}/trail")
def trail(
    room_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Every audit row this feature's own routes wrote in this room, newest first.

    Filtered on the router prefix rather than on a collection, because a cascade
    writes one audit row naming every record it removed - and that row is the
    only place the full before_state lives.
    """
    eng = engine(store)
    eng.room(room_id)
    rows = eng.trail(room_id, limit=limit)
    return {"room_id": room_id, "count": len(rows), "trail": rows}


# --------------------------------------------------------------------------- #
# Writes. Every one of these passes a source built from router.prefix.
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/links")
def create_link(
    room_id: str,
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Issue a share link: the thing revocation takes away later."""
    body = payload or {}
    return engine(store).create_link(
        room_id,
        slug=str(body.get("slug") or ""),
        target=str(body.get("target") or TARGET_ROOM),
        group_id=body.get("group_id"),
        domain=body.get("domain"),
        label=str(body.get("label") or ""),
        actor=_actor(request),
        source=_source("POST", f"/rooms/{room_id}/links"),
    )


@router.post("/rooms/{room_id}/links/{link_id}/revoke")
def revoke_link(
    room_id: str,
    link_id: str,
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Cut the public URL and keep the row.

    Two writes: the revocation facts, then the soft delete. The order matters -
    if the delete fails, the link is still live and the row says so, which is the
    direction that fails safely.
    """
    body = payload or {}
    return engine(store).revoke_link(
        room_id,
        link_id,
        reason=str(body.get("reason") or ""),
        actor=_actor(request),
        source=_source("POST", f"/rooms/{room_id}/links/{link_id}/revoke"),
    )


@router.post("/rooms/{room_id}/groups")
def create_group(
    room_id: str,
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Create an audience to cut access to, one member or one group at a time."""
    body = payload or {}
    return engine(store).create_group(
        room_id,
        name=str(body.get("name") or ""),
        actor=_actor(request),
        source=_source("POST", f"/rooms/{room_id}/groups"),
    )


@router.post("/rooms/{room_id}/groups/{group_id}/delete")
def delete_group(
    room_id: str,
    group_id: str,
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Delete a group, its memberships, its permissions and its links.

    Irreversible, so it refuses unless ``confirm`` echoes the group id. One
    transaction for the whole set: a cascade that half-applied would leave live
    links behind and describe work that did not happen.
    """
    body = payload or {}
    return engine(store).delete_group(
        room_id,
        group_id,
        confirm=str(body.get("confirm") or ""),
        actor=_actor(request),
        source=_source("POST", f"/rooms/{room_id}/groups/{group_id}/delete"),
    )


@router.post("/rooms/{room_id}/groups/{group_id}/members")
def add_member(
    room_id: str,
    group_id: str,
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Put one buyer in one group, creating the underlying viewer on first sight."""
    body = payload or {}
    return engine(store).add_member(
        room_id,
        group_id,
        email=str(body.get("email") or ""),
        viewer_id=body.get("viewer_id"),
        name=str(body.get("name") or ""),
        actor=_actor(request),
        source=_source("POST", f"/rooms/{room_id}/groups/{group_id}/members"),
    )


@router.post("/rooms/{room_id}/groups/{group_id}/members/{member_id}/remove")
def remove_member(
    room_id: str,
    group_id: str,
    member_id: str,
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Remove one buyer's membership. The viewer record stays.

    The response reports the resolution for that buyer immediately afterwards, so
    the effect of the removal is visible in the same call that caused it.
    """
    return engine(store).remove_member(
        room_id,
        group_id,
        member_id,
        actor=_actor(request),
        source=_source("POST", f"/rooms/{room_id}/groups/{group_id}/members/{member_id}/remove"),
    )


@router.post("/rooms/{room_id}/groups/{group_id}/permissions")
def set_permissions(
    room_id: str,
    group_id: str,
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Hide or re-expose items for a group by flipping ``view`` / ``download``.

    Takes one permission or a list, because the researched endpoint is plural:
    the vendor's group view edits several at once, and each flag pair is still
    its own audited write.
    """
    body = payload or {}
    supplied: Any = body.get("permissions") if "permissions" in body else body
    return engine(store).set_permissions(
        room_id,
        group_id,
        supplied,
        actor=_actor(request),
        source=_source("POST", f"/rooms/{room_id}/groups/{group_id}/permissions"),
    )


@router.post("/rooms/{room_id}/documents")
def attach_document(
    room_id: str,
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Attach a team-library document to this dataroom.

    Refused on a frozen dataroom, and refused across teams: "The document must
    belong to the same team as the dataroom; cross-team attaches are refused.
    Frozen datarooms refuse new attachments."
    """
    body = payload or {}
    return engine(store).attach(
        room_id,
        document_id=str(body.get("document_id") or ""),
        document_team=body.get("document_team"),
        title=str(body.get("title") or ""),
        actor=_actor(request),
        source=_source("POST", f"/rooms/{room_id}/documents"),
    )


@router.post("/rooms/{room_id}/documents/{document_id}/detach")
def detach_document(
    room_id: str,
    document_id: str,
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Detach one document from this dataroom, and nothing else.

    A join-row delete: the library document and its attachments to other
    datarooms are left intact, and the response lists the other datarooms so that
    claim is checkable rather than asserted.
    """
    return engine(store).detach(
        room_id,
        document_id,
        actor=_actor(request),
        source=_source("POST", f"/rooms/{room_id}/documents/{document_id}/detach"),
    )


@router.post("/rooms/{room_id}/purge")
def purge(
    room_id: str,
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Delete a dataroom's access graph: every link, group and join row.

    Irrecoverable, so it refuses unless ``confirm`` echoes the room id. The team
    library survives; so do the underlying viewers.
    """
    body = payload or {}
    return engine(store).purge(
        room_id,
        confirm=str(body.get("confirm") or ""),
        actor=_actor(request),
        source=_source("POST", f"/rooms/{room_id}/purge"),
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db, context):
    """Seed the states the research says matter, not just the happy path.

    Four rooms' worth of access, chosen so every researched rule is reachable
    from the demo page without writing anything first:

    * **An active room** with two audiences, a custom-domain room link, a group
      link per audience, three buyers, per-item permissions, and one viewer who
      is in no group at all - so "remove a membership, the viewer is kept" is a
      visible difference rather than a claim.
    * **A frozen room** with a live link and an attached document, so the freeze
      refusal is reachable and its *scope* is visible: the link can still be
      revoked even though an attach is refused.
    * **A room with a link revoked before you arrived**, so the audit trail has
      something in it on first load. That row is written through
      :meth:`Revocation.revoke_link`, so it carries the same evidence a live
      revoke does, and its ``source`` names the route a rep would have called -
      which is the convention ``wf005`` already uses in its own seed.
    * **A cross-team document** in the library, so the attach refusal is
      reachable. It is created here rather than tagged onto a core demo document,
      because a field written onto someone else's row is a field they did not
      agree to.

    ``db`` is an ``AuditedDatabase``; ``context`` carries ``room_ids``, ``now``
    and a per-feature ``rng``.
    """
    from dsr.store import RecordStore

    store = RecordStore(db)
    eng = Revocation(store, source_prefix=router.prefix)
    room_ids = context.get("room_ids") or []
    now = context.get("now")
    if len(room_ids) < 3:
        return None

    northwind_id, _ = room_ids[0]
    frozen_id, _ = room_ids[1]
    fabrikam_id, _ = room_ids[2]

    # -- the active room ---------------------------------------------------- #
    northwind = store.get(northwind_id)
    if northwind is None:
        return None

    procurement = eng.create_group(
        northwind_id,
        name="Procurement",
        actor="dana",
        source=_source("POST", f"/rooms/{northwind_id}/groups"),
    )
    legal = eng.create_group(
        northwind_id,
        name="Legal review",
        actor="dana",
        source=_source("POST", f"/rooms/{northwind_id}/groups"),
    )

    buyers = [
        (procurement, "a.buyer@northwind.example", "A. Buyer"),
        (procurement, "procurement@northwind.example", "Procurement Desk"),
        (legal, "counsel@northwind.example", "External Counsel"),
    ]
    members: list[dict[str, Any]] = []
    for group, email, name in buyers:
        added = eng.add_member(
            northwind_id,
            group["id"],
            email=email,
            name=name,
            actor="dana",
            source=_source("POST", f"/rooms/{northwind_id}/groups/{group['id']}/members"),
        )
        members.append(added["member"])

    # A viewer in the room but in no group: removing a membership elsewhere must
    # leave this record alone, and there has to be a second one to see it by.
    eng.add_member(
        northwind_id,
        procurement["id"],
        email="ops@northwind.example",
        name="Ops Desk",
        actor="dana",
        source=_source("POST", f"/rooms/{northwind_id}/groups/{procurement['id']}/members"),
    )
    store.create(
        VIEWERS,
        {
            "email": "analyst@partner.example",
            "name": "Partner Analyst",
            "first_seen": now.isoformat(timespec="seconds")
            if hasattr(now, "isoformat")
            else str(now),
            "note": "signed up from a public link; never in a group",
        },
        room_id=northwind_id,
        actor="system",
        source="seed",
    )

    # Attach real library documents by id, not by title: the join row has to name
    # the record it points at, or a detach would report on a row that never
    # existed.
    library = store.list("document", room_id=northwind_id, limit=10)
    if len(library) < 2:
        return None
    granted = library[:2]
    for row in granted:
        eng.attach(
            northwind_id,
            document_id=str(row["id"]),
            title=str(fields(row).get("title") or row["id"]),
            actor="dana",
            source=_source("POST", f"/rooms/{northwind_id}/documents"),
        )
    eng.set_permissions(
        northwind_id,
        legal["id"],
        {"document_id": str(granted[0]["id"]), "view": False, "download": False},
        actor="dana",
        source=_source("POST", f"/rooms/{northwind_id}/groups/{legal['id']}/permissions"),
    )

    eng.create_link(
        northwind_id,
        slug="northwind-overview",
        target=TARGET_ROOM,
        # A custom domain, so the slug rename on revoke has something to do.
        domain="share.northwind.example",
        label="Northwind overview (custom domain)",
        actor="dana",
        source=_source("POST", f"/rooms/{northwind_id}/links"),
    )
    eng.create_link(
        northwind_id,
        slug="northwind-procurement",
        target=TARGET_GROUP,
        group_id=procurement["id"],
        label="Procurement group link",
        actor="dana",
        source=_source("POST", f"/rooms/{northwind_id}/links"),
    )
    eng.create_link(
        northwind_id,
        slug="northwind-legal",
        target=TARGET_GROUP,
        group_id=legal["id"],
        label="Legal group link",
        actor="dana",
        source=_source("POST", f"/rooms/{northwind_id}/links"),
    )

    # A library document belonging to another team. Attaching it to Northwind is
    # refused, and the demo has to be able to show that refusal being reached.
    cross_team = store.create(
        "document",
        {
            "title": "Contoso renewal terms (different team)",
            "kind": "pdf",
            "pages": 4,
            "public": False,
            "status": "published",
            "team": "Contoso Health",
        },
        room_id=frozen_id,
        actor="sam",
        source="seed",
    )

    # -- the frozen room ---------------------------------------------------- #
    #
    # Attach before freezing. The researched rule is that a frozen dataroom
    # refuses new attachments, so seeding in the other order would seed a room
    # whose only join row could never have existed - a demo that lies about the
    # rule it is meant to show.
    eng.attach(
        frozen_id,
        document_id=str(cross_team["id"]),
        title="Contoso renewal terms (different team)",
        document_team="Contoso Health",
        actor="sam",
        source=_source("POST", f"/rooms/{frozen_id}/documents"),
    )
    eng.create_link(
        frozen_id,
        slug="contoso-security-review",
        target=TARGET_ROOM,
        label="Contoso review link",
        actor="sam",
        source=_source("POST", f"/rooms/{frozen_id}/links"),
    )
    current = store.get(frozen_id)
    if current is not None and not eng.is_frozen(current):
        store.update(
            frozen_id,
            {"frozen": True, "frozen_reason": "Embargoed pending the security review."},
            actor="sam",
            source="seed",
        )

    # -- a room whose link was revoked before you arrived -------------------- #
    embargolink = eng.create_link(
        fabrikam_id,
        slug="fabrikam-diligence",
        target=TARGET_ROOM,
        domain="share.fabrikam.example",
        label="Fabrikam diligence (embargoed)",
        actor="dana",
        source=_source("POST", f"/rooms/{fabrikam_id}/links"),
    )
    eng.revoke_link(
        fabrikam_id,
        embargolink["link"]["id"],
        reason="Diligence embargoed pending the renewal.",
        actor="dana",
        source=_source("POST", f"/rooms/{fabrikam_id}/links/{embargolink['link']['id']}/revoke"),
    )

    return _describe(store, northwind_id, frozen_id, fabrikam_id)


def _describe(store: RecordStore, *room_ids: str) -> str:
    """Count what was actually seeded.

    Measured rather than written out, because a summary that drifts from the
    rows is worse than no summary: the seeder prints it, so a reviewer reading
    the seed log is reading a claim about the database.
    """
    counts = {
        collection: sum(len(store.list(collection, room_id=room, limit=500)) for room in room_ids)
        for collection in ALL_COLLECTIONS
    }
    # Counted per room inside the sum, not by subtracting the all-room live total
    # once per room: that subtracts the whole total N times and printed a
    # negative count, which is exactly the sort of wrong number a seeder writes
    # into a demo log that a reviewer then trusts.
    revoked = sum(
        len(store.list(LINKS, room_id=room, limit=500, include_deleted=True))
        - len(store.list(LINKS, room_id=room, limit=500))
        for room in room_ids
    )
    frozen = sum(1 for room in room_ids if bool(fields(store.get(room) or {}).get("frozen")))
    return (
        f"{counts[GROUPS]} groups, {counts[MEMBERS]} memberships, {counts[VIEWERS]} viewers, "
        f"{counts[PERMISSIONS]} permissions, {counts[GRANTS]} attach rows, "
        f"{counts[LINKS]} live links ({revoked} already revoked), "
        f"{frozen} room frozen, 1 cross-team library document"
    )


__all__ = ["EXCEPTION_HANDLERS", "FEATURE", "engine", "router", "seed"]
