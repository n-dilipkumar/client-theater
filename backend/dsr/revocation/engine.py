"""The revocation engine: six researched operations, one transaction each.

:class:`Revocation` is the whole domain. It never opens a database connection,
never imports the FastAPI app, and takes ``source`` as a required argument on
every write so the audit row names the route that actually served it - the
defect the contract names by hand, "a feature's audit log kept recording a path
the app had stopped serving", cannot happen when forgetting the argument is a
``TypeError``.

Why ``bulk_delete`` and not a loop
----------------------------------
Two of the six operations are cascades: a group delete and a dataroom purge.
Deleting their records one call at a time would let the audit log describe work
that only partly happened, which is the exact failure mode
:class:`~dsr.db.audited.AuditedDatabase.bulk_delete` was added for. It is
all-or-nothing - a missing id rolls the whole set back - and it writes the full
``before_state`` of every record it removes, which is what makes "which slug did
that cascade free" answerable years later.

Why a revoke writes twice
-------------------------
:meth:`Revocation.revoke_link` records the revocation facts with an ``update``
and *then* soft-deletes. The order is the point. If the delete fails, the link
is still live and the row carries a revocation that did not happen - access stays
on and the state is visible, which is the right direction to fail in. Cutting
access first and writing the evidence second would fail the other way: the URL
dead, no record of who did it, and nothing to appeal.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from dsr.db.audited import utcnow
from dsr.revocation import (
    ALL_COLLECTIONS,
    CACHED_COPY_RECALL,
    CASCADE_COLLECTIONS,
    DEFAULT_ACTOR,
    FROZEN_REFUSALS,
    GONE_CASCADE_GROUP,
    GONE_CASCADE_ROOM,
    GONE_MEMBER_REMOVED,
    GONE_REVOKED,
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
    REVOKED_BY_GROUP,
    REVOKED_BY_ROOM,
    REVOKED_DIRECTLY,
    TARGET_GROUP,
    TARGET_ROOM,
    TARGETS,
    VIEWERS,
    RevocationBadRequest,
    RevocationConflict,
    RevocationCrossTeam,
    RevocationFrozen,
    RevocationNotFound,
    is_custom_domain,
    payload,
    tombstone_slug,
)
from dsr.store import RecordStore

#: A generous ceiling for a room-scoped read. A room with more than this many
#: access records needs paging, which no researched operation needs today, and a
#: silent truncation would be worse than an honest bound.
READ_LIMIT = 1000


class Revocation:
    """Early revocation over one room's access graph.

    ``source_prefix`` is this feature's router prefix, used to recognise this
    feature's own rows in the shared audit log. It is passed in rather than
    imported so the engine never has to know where it is mounted - and so a test
    can mount the router under a different prefix and still get a truthful
    answer.
    """

    def __init__(self, store: RecordStore, source_prefix: str = "/api/wf-076") -> None:
        self.store = store
        self.source_prefix = source_prefix

    # ---------------------------------------------------------------- reads #

    def _rows(
        self,
        collection: str,
        room_id: str | None,
        where: Mapping[str, Any] | None = None,
        *,
        include_deleted: bool = False,
        limit: int = READ_LIMIT,
    ) -> list[dict[str, Any]]:
        """Room-scoped rows, optionally filtered by dotted JSON paths.

        ``find`` filters on ``data`` only and cannot see the ``room_id``
        envelope, so a room-scoped filter is applied here. Passing ``where=None``
        takes the cheap path: ``list`` filters on the envelope in SQL.
        """
        if not where:
            return self.store.list(
                collection, room_id=room_id, limit=limit, include_deleted=include_deleted
            )
        found = self.store.find(collection, where, limit=limit, include_deleted=include_deleted)
        if room_id is None:
            return found
        return [row for row in found if row.get("room_id") == room_id]

    def room(self, room_id: str) -> dict[str, Any]:
        """The room record, or 404.

        The room is a core collection, so a missing one is a caller error rather
        than an empty state - and every route here is room-scoped, so it is
        checked once here rather than six times in six handlers.
        """
        record = self.store.get(room_id)
        if record is None:
            raise RevocationNotFound(f"room {room_id} does not exist")
        return record

    @staticmethod
    def is_frozen(record: Mapping[str, Any]) -> bool:
        """Is this room frozen?

        Read defensively: the field is ordinary JSON a team sets, and every room
        that predates this workflow has no such field at all. Absent means not
        frozen, which is the documented default and the safe direction for the
        *access* rules even though freezing is the conservative act.
        """
        return bool(payload(record).get("frozen"))

    def frozen(self, room_id: str) -> bool:
        return self.is_frozen(self.room(room_id))

    @staticmethod
    def team_of(record: Mapping[str, Any]) -> str:
        """The room's team. Its ``account`` is what this product calls it."""
        return str(payload(record).get("account") or "").strip()

    def _require(
        self, collection: str, record_id: str, room_id: str | None, *, label: str
    ) -> dict[str, Any]:
        """Fetch one live record, scoped to its room, or 404.

        Room scoping is applied by hand rather than by ``room_id=`` because the
        envelope filter is not available on ``get``; a caller that guessed
        another room's record id gets a 404 instead of someone else's data.
        """
        record = self.store.get(record_id)
        if record is None or record.get("collection") != collection:
            raise RevocationNotFound(f"{label} {record_id} does not exist")
        if room_id is not None and record.get("room_id") != room_id:
            raise RevocationNotFound(f"{label} {record_id} is not in room {room_id}")
        return record

    def link(self, room_id: str, link_id: str, *, include_revoked: bool = False) -> dict[str, Any]:
        """One link. Revoked links are readable, which is the whole point."""
        if include_revoked:
            record = self.store.db.get(link_id, include_deleted=True)
            if (
                record is None
                or record.get("collection") != LINKS
                or record.get("room_id") != room_id
            ):
                raise RevocationNotFound(f"link {link_id} does not exist in room {room_id}")
            return record
        return self._require(LINKS, link_id, room_id, label="link")

    def links(self, room_id: str, *, include_revoked: bool = False) -> list[dict[str, Any]]:
        """Links in a room, live only unless ``include_revoked`` says otherwise."""
        return self._rows(LINKS, room_id, include_deleted=include_revoked)

    def group(self, room_id: str, group_id: str) -> dict[str, Any]:
        return self._require(GROUPS, group_id, room_id, label="group")

    def groups(self, room_id: str) -> list[dict[str, Any]]:
        return self._rows(GROUPS, room_id)

    def members(self, room_id: str, group_id: str | None = None) -> list[dict[str, Any]]:
        where = {"group_id": group_id} if group_id else None
        return self._rows(MEMBERS, room_id, where)

    def viewers(self, room_id: str) -> list[dict[str, Any]]:
        """The underlying viewers. A removed membership leaves these alone."""
        return self._rows(VIEWERS, room_id)

    def permissions(self, room_id: str, group_id: str | None = None) -> list[dict[str, Any]]:
        where = {"group_id": group_id} if group_id else None
        return self._rows(PERMISSIONS, room_id, where)

    def grants(self, room_id: str, *, include_detached: bool = False) -> list[dict[str, Any]]:
        """The dataroom-document join rows. Detach deletes one of these and nothing else."""
        return self._rows(GRANTS, room_id, include_deleted=include_detached)

    def group_view(self, room_id: str, group_id: str) -> dict[str, Any]:
        """One group with its memberships, permissions and links attached.

        Assembled rather than stored, so a member removed a second ago cannot be
        served from a stale copy of the group.
        """
        record = self.group(room_id, group_id)
        links = [link for link in self.links(room_id) if payload(link).get("group_id") == group_id]
        return {
            **record,
            "members": self.members(room_id, group_id),
            "permissions": self.permissions(room_id, group_id),
            "links": links,
            "member_count": len(self.members(room_id, group_id)),
            "permission_count": len(self.permissions(room_id, group_id)),
            "link_count": len(links),
        }

    def links_stopped_by_group(self, room_id: str, group_id: str) -> list[dict[str, Any]]:
        """Every live link pointing at a group - the set a group delete takes down."""
        return [
            link
            for link in self.links(room_id)
            if payload(link).get("group_id") == group_id
            and payload(link).get("target") == TARGET_GROUP
        ]

    def live_members(self, room_id: str, group_id: str) -> list[dict[str, Any]]:
        return self.members(room_id, group_id)

    def links_with_slug(self, room_id: str, slug: str) -> list[dict[str, Any]]:
        """The live links holding a slug. Empty means the slug is free to issue."""
        return self._rows(LINKS, room_id, {"slug": str(slug or "").strip()})

    def _slug_holders(self, room_id: str, slug: str) -> list[dict[str, Any]]:
        """Every row that has ever held a slug, live or deleted.

        Two paths, because a revoke can move a slug. A revoked custom-domain link
        keeps its old name in ``original_slug`` and wears a tombstone in ``slug``,
        so a lookup on ``slug`` alone would report the URL as never having
        existed - and "revoked" versus "never issued" is the whole difference to
        someone holding a dead link.
        """
        needle = str(slug or "").strip()
        if not needle:
            return []
        seen: dict[str, dict[str, Any]] = {}
        for row in self._rows(LINKS, room_id, {"slug": needle}, include_deleted=True):
            seen[row["id"]] = row
        for row in self._rows(LINKS, room_id, {"original_slug": needle}, include_deleted=True):
            seen.setdefault(row["id"], row)
        return list(seen.values())

    def slug_available(self, room_id: str, slug: str) -> bool:
        """Is this slug free to issue?

        A slug is held by a *live* link. A revoked custom-domain link has been
        renamed to a tombstone and a cascaded one is no longer live, so in both
        cases the original becomes reusable - which is the researched outcome:
        "the slug is renamed so the original can be reused."
        """
        needle = str(slug or "").strip()
        if not needle:
            return False
        return not self.links_with_slug(room_id, needle)

    def library_document(self, document_id: str) -> dict[str, Any] | None:
        """A team-library document record, if one exists.

        Read so a detach can report whether the library document survived. A
        ``None`` here does not mean the document was destroyed by a detach - it
        means the id was never a document record, which is a different problem
        and worth not conflating with the researched outcome.
        """
        return self.store.get(str(document_id or ""))

    def retained(self, room_id: str, record_id: str) -> dict[str, Any]:
        """A revoked record and the audit trail that describes it.

        The surface this workflow is named for. After a revoke, the ordinary read
        is gone and *this* is what remains: the row, marked, and the audit entry
        written in the same transaction as the change.
        """
        record = self.store.db.get(record_id, include_deleted=True)
        if record is None or record.get("room_id") != room_id:
            raise RevocationNotFound(f"record {record_id} does not exist in room {room_id}")
        if record.get("collection") not in ALL_COLLECTIONS:
            raise RevocationNotFound(
                f"record {record_id} is a {record.get('collection')}, not an access record"
            )
        return {
            "id": record_id,
            "collection": record.get("collection"),
            "room_id": room_id,
            "revision": record.get("revision"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "deleted_at": record.get("deleted_at"),
            "live": record.get("deleted_at") is None,
            "data": record.get("data") or {},
            "audit": self._audit_for(record_id),
        }

    def trail(self, room_id: str | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
        """Audit rows written by this feature's own routes, newest first.

        Filtered on the router prefix rather than on a collection, because one
        researched cascade writes a single audit row naming every record it
        removed. That row is the only place the full before_state lives.
        """
        entries = self.store.audit(limit=min(int(limit), 1000))
        out = []
        for entry in entries:
            if not _names_this_feature(entry.get("source"), self.source_prefix):
                continue
            if room_id is not None and entry.get("room_id") != room_id:
                continue
            out.append(
                {
                    "seq": entry.get("seq"),
                    "ts": entry.get("ts"),
                    "action": entry.get("action"),
                    "source": entry.get("source"),
                    "actor": entry.get("actor"),
                    "room_id": entry.get("room_id"),
                    "record_id": entry.get("record_id"),
                    "collection": entry.get("collection"),
                    "summary": entry.get("summary"),
                    "diff": entry.get("diff"),
                    "counts": entry.get("after_state", {}).get("count")
                    if isinstance(entry.get("after_state"), Mapping)
                    else None,
                }
            )
        return out

    def _audit_for(self, record_id: str) -> list[dict[str, Any]]:
        return [
            {
                "ts": entry.get("ts"),
                "action": entry.get("action"),
                "source": entry.get("source"),
                "actor": entry.get("actor"),
                "summary": entry.get("summary"),
                "diff": entry.get("diff"),
            }
            for entry in self.store.audit(record_id=record_id, limit=100)
        ]

    def resolve(self, room_id: str, slug: str, viewer: str | None = None) -> dict[str, Any]:
        """Does this public URL still work, for this requester, right now?

        The researched guarantee, as a pure function of live state: "The public
        URL stops resolving immediately." There is no grace period and no cache,
        so the answer cannot disagree with the database by more than one
        request.

        A group link resolves only for a live member of its group. That is what
        makes removing one membership a way to stop one buyer.
        """
        needle = str(slug or "").strip()
        answer: dict[str, Any] = {
            "room_id": room_id,
            "slug": needle,
            "viewer": (viewer or "").strip() or None,
            "resolves": False,
            "reason": NOT_FOUND,
            "link_id": None,
            "checked_at": utcnow(),
            "grace_period_minutes": GRACE_PERIOD_MINUTES,
            "cached_copy_recall": CACHED_COPY_RECALL,
        }
        if not needle:
            return answer

        holders = self._slug_holders(room_id, needle)
        live = [row for row in holders if row.get("deleted_at") is None]
        if not live:
            answer["withheld_by"] = _withheld_by(holders)
            answer["reason"] = _gone_reason(answer["withheld_by"])
            return answer

        link = live[0]
        data = payload(link)
        answer.update(
            {
                "resolves": True,
                "reason": RESOLVES,
                "link_id": link["id"],
                "target": data.get("target"),
                "group_id": data.get("group_id"),
                "custom_domain": is_custom_domain(data.get("domain")),
            }
        )
        if data.get("target") != TARGET_GROUP:
            return answer

        group_id = str(data.get("group_id") or "")
        group = self.store.db.get(group_id, include_deleted=True) if group_id else None
        if group_id and group is not None and group.get("deleted_at") is not None:
            answer.update({"resolves": False, "reason": GONE_CASCADE_GROUP})
            return answer
        who = (viewer or "").strip().lower()
        allowed = {
            str(payload(m).get("viewer_email") or "").strip().lower()
            for m in self.live_members(room_id, group_id)
        }
        if who not in allowed:
            answer.update({"resolves": False, "reason": GONE_MEMBER_REMOVED})
        return answer

    def state(self, room_id: str) -> dict[str, Any]:
        """Everything a UI needs to render one room's access, derived.

        Derived rather than stored so a page cannot offer a button the route
        would refuse, and so a refusal has a stated reason attached to it.
        """
        room = self.room(room_id)
        frozen = self.is_frozen(room)
        live_links = self.links(room_id)
        revoked = [
            row for row in self.links(room_id, include_revoked=True) if row.get("deleted_at")
        ]
        attached = self.grants(room_id)
        detached = [r for r in self.grants(room_id, include_detached=True) if r.get("deleted_at")]

        always = ["delete_group", "purge", "remove_member", "revoke_link", "set_permissions"]
        refused: dict[str, str] = {}
        if frozen:
            for action in sorted(FROZEN_REFUSALS):
                refused[action] = "the dataroom is frozen"
        return {
            "room_id": room_id,
            "frozen": frozen,
            "team": self.team_of(room),
            "counts": {
                "links": len(live_links),
                "links_revoked": len(revoked),
                "groups": len(self.groups(room_id)),
                "members": len(self.members(room_id)),
                "viewers": len(self.viewers(room_id)),
                "permissions": len(self.permissions(room_id)),
                "attached": len(attached),
                "detached": len(detached),
            },
            "available_actions": sorted(always + [a for a in FROZEN_REFUSALS if a not in refused]),
            "refused_actions": refused,
            "frozen_refusals": sorted(FROZEN_REFUSALS),
            "guarantee": {
                "request_time": True,
                "grace_period_minutes": GRACE_PERIOD_MINUTES,
                "cached_copy_recall": CACHED_COPY_RECALL,
                "row_kept_for_audit": True,
                "reversible": IRREVERSIBLE,
                "hard_delete": HARD_DELETE,
            },
        }

    def summary(self) -> dict[str, Any]:
        """Product-wide counts, from the collections rather than from a counter."""
        by_room: dict[str, dict[str, int]] = {}
        for collection in ALL_COLLECTIONS:
            for row in self._rows(collection, None):
                room_id = str(row.get("room_id") or "unscoped")
                bucket = by_room.setdefault(room_id, {name: 0 for name in ALL_COLLECTIONS})
                bucket[collection] += 1
                if row.get("deleted_at") is not None:
                    bucket[f"{collection}.revoked"] = bucket.get(f"{collection}.revoked", 0) + 1
        return {
            "collections": list(ALL_COLLECTIONS),
            "cascade_collections": list(CASCADE_COLLECTIONS),
            "rooms": len(by_room),
            "by_room": by_room,
            "guarantee": {
                "request_time": True,
                "grace_period_minutes": GRACE_PERIOD_MINUTES,
                "cached_copy_recall": CACHED_COPY_RECALL,
                "reversible": IRREVERSIBLE,
            },
            "revocation_causes": list(REVOCATION_CAUSES),
        }

    # --------------------------------------------------------------- writes #

    def create_link(
        self,
        room_id: str,
        *,
        slug: str,
        target: str = TARGET_ROOM,
        group_id: str | None = None,
        domain: str | None = None,
        label: str = "",
        actor: str = DEFAULT_ACTOR,
        source: str,
    ) -> dict[str, Any]:
        """Issue a share link. The thing revocation takes away later.

        Refuses a slug a live link already holds, because the researched slug
        registry is only verifiable if two links cannot quietly share an entry.
        """
        room = self.room(room_id)
        needle = str(slug or "").strip()
        if not needle:
            raise RevocationBadRequest("a link needs a slug")
        if target not in TARGETS:
            raise RevocationBadRequest(f"target must be one of {list(TARGETS)}; got {target!r}")
        if target == TARGET_GROUP:
            if not group_id:
                raise RevocationBadRequest("a group link needs the group it points at")
            self.group(room_id, group_id)
        elif group_id:
            raise RevocationBadRequest("a room link points at no group")
        if not self.slug_available(room_id, needle):
            raise RevocationConflict(f"slug {needle!r} is already held by a live link")

        record = self.store.create(
            LINKS,
            {
                "slug": needle,
                "original_slug": None,
                "target": target,
                "group_id": group_id,
                "domain": str(domain).strip() if domain else None,
                "custom_domain": is_custom_domain(domain),
                "label": label or needle,
                "team": self.team_of(room),
                "issued_at": utcnow(),
                "issued_by": actor,
                "revoked": False,
                "revoked_at": None,
                "revoked_by": None,
                "revoked_via": None,
                "revocation_reason": None,
                "slug_released": False,
                "reversible": IRREVERSIBLE,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return {"link": record, "slug_available_after": self.slug_available(room_id, needle)}

    def revoke_link(
        self,
        room_id: str,
        link_id: str,
        *,
        reason: str = "",
        actor: str = DEFAULT_ACTOR,
        source: str,
    ) -> dict[str, Any]:
        """Cut a link's public URL and keep the row.

        Two writes, in this order. The first records who cut it, when, why and
        which slug it held; the second marks the row deleted. If the second
        fails, the link is still live and the row says a revocation was attempted
        - access stays on and the state is visible, which is the direction that
        fails safely. The researched guarantee still holds on the success path:
        the soft delete is committed before this returns, so the next request
        does not resolve.

        The slug is renamed only when the link used a custom domain, which is the
        one place the research draws that line.
        """
        record = self.store.db.get(link_id, include_deleted=True)
        if record is None or record.get("collection") != LINKS:
            raise RevocationNotFound(f"link {link_id} does not exist")
        if record.get("room_id") != room_id:
            raise RevocationNotFound(f"link {link_id} is not in room {room_id}")
        data = payload(record)
        # Read through the deleted row rather than the live one, so a second revoke
        # of an already-revoked link is a 409 and not a 404. "Does not exist" would
        # tell a caller the URL was never issued, which is the opposite of what
        # they need to hear about a URL they already cut.
        if record.get("deleted_at") is not None or data.get("revoked"):
            raise RevocationConflict(
                f"link {link_id} was already revoked at {data.get('revoked_at')}"
            )

        at = utcnow()
        original = str(data.get("slug") or "")
        custom = bool(data.get("custom_domain")) or is_custom_domain(data.get("domain"))
        patch: dict[str, Any] = {
            "revoked": True,
            "revoked_at": at,
            "revoked_by": actor,
            "revoked_via": REVOKED_DIRECTLY,
            "revocation_reason": reason or None,
            "effective_at": at,
            "grace_period_minutes": GRACE_PERIOD_MINUTES,
            "cached_copy_recall": CACHED_COPY_RECALL,
        }
        released_slug: str | None = None
        if custom:
            released_slug = tombstone_slug(link_id, original)
            patch["slug"] = released_slug
            patch["original_slug"] = original
            patch["slug_released"] = True
            patch["slug_released_at"] = at

        self.store.update(link_id, patch, actor=actor, source=source)
        self.store.delete(link_id, actor=actor, source=source, hard=HARD_DELETE)

        return {
            "link_id": link_id,
            "room_id": room_id,
            "revoked": True,
            "revoked_at": at,
            "revoked_by": actor,
            "reason": reason or None,
            "via": REVOKED_DIRECTLY,
            "row_kept": True,
            "original_slug": original,
            "slug": released_slug or original,
            "slug_released": bool(released_slug),
            "slug_available_now": self.slug_available(room_id, original),
            "grace_period_minutes": GRACE_PERIOD_MINUTES,
            "cached_copy_recall": CACHED_COPY_RECALL,
            "reversible": IRREVERSIBLE,
            "retained": self.retained(room_id, link_id),
        }

    def create_group(
        self,
        room_id: str,
        *,
        name: str,
        actor: str = DEFAULT_ACTOR,
        source: str,
    ) -> dict[str, Any]:
        """Create an audience to cut access to, one member or one group at a time."""
        self.room(room_id)
        label = str(name or "").strip()
        if not label:
            raise RevocationBadRequest("a group needs a name")
        return self.store.create(
            GROUPS,
            {"name": label, "created_at": utcnow(), "created_by": actor},
            room_id=room_id,
            actor=actor,
            source=source,
        )

    def add_member(
        self,
        room_id: str,
        group_id: str,
        *,
        email: str,
        viewer_id: str | None = None,
        name: str = "",
        actor: str = DEFAULT_ACTOR,
        source: str,
    ) -> dict[str, Any]:
        """Put one buyer in one group.

        Creates the underlying viewer on first sight, because "the underlying
        viewer is kept" only means something if there is one to keep, and a
        membership that outlives its group delete must still point at something.
        """
        self.group(room_id, group_id)
        address = str(email or "").strip().lower()
        if not address:
            raise RevocationBadRequest("a membership needs an email address")
        viewer = None
        if viewer_id:
            viewer = self._require(VIEWERS, viewer_id, room_id, label="viewer")
        else:
            existing = self._rows(VIEWERS, room_id, {"email": address})
            viewer = (
                existing[0]
                if existing
                else self.store.create(
                    VIEWERS,
                    {"email": address, "name": name or address, "first_seen": utcnow()},
                    room_id=room_id,
                    actor=actor,
                    source=source,
                )
            )
        existing = self._rows(MEMBERS, room_id, {"group_id": group_id, "viewer_email": address})
        if existing:
            raise RevocationConflict(f"{address} is already in group {group_id}")
        member = self.store.create(
            MEMBERS,
            {
                "group_id": group_id,
                "viewer_id": viewer["id"],
                "viewer_email": address,
                "joined_at": utcnow(),
                "status": "member",
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return {"member": member, "viewer": viewer}

    def remove_member(
        self,
        room_id: str,
        group_id: str,
        member_id: str,
        *,
        actor: str = DEFAULT_ACTOR,
        source: str,
    ) -> dict[str, Any]:
        """Remove one buyer's membership. The viewer record stays.

        Sourced: "The underlying viewer is kept - only their membership in this
        group is removed." The viewer's survival is checked and returned rather
        than assumed, because it is the difference between removing a buyer and
        deleting a person.
        """
        self.group(room_id, group_id)
        member = self._require(MEMBERS, member_id, room_id, label="member")
        data = payload(member)
        if str(data.get("group_id") or "") != group_id:
            raise RevocationNotFound(f"member {member_id} is not in group {group_id}")

        self.store.delete(member_id, actor=actor, source=source, hard=HARD_DELETE)
        viewer_id = str(data.get("viewer_id") or "")
        viewer = self.store.get(viewer_id) if viewer_id else None
        slug = self._group_slug(room_id, group_id)
        return {
            "member_id": member_id,
            "room_id": room_id,
            "group_id": group_id,
            "viewer_id": viewer_id,
            "viewer_email": data.get("viewer_email"),
            "viewer_kept": viewer is not None,
            "resolves_now": self.resolve(room_id, slug, viewer=str(data.get("viewer_email") or "")),
        }

    def _group_slug(self, room_id: str, group_id: str) -> str:
        """One of a group's link slugs, for a resolution check. Empty if it has none."""
        links = self.links_stopped_by_group(room_id, group_id)
        return str(payload(links[0]).get("slug") or "") if links else ""

    def delete_group(
        self,
        room_id: str,
        group_id: str,
        *,
        confirm: str,
        actor: str = DEFAULT_ACTOR,
        source: str,
    ) -> dict[str, Any]:
        """Delete a group, its memberships, its permissions and its links.

        Sourced: "Deletes the group, its memberships, its permissions, AND every
        share link pointing at it - active group links stop resolving
        immediately." and "This matches the dashboard and cannot be undone."

        One transaction for the whole set, so a cascade cannot half-apply, and
        one audit row carrying every record's before_state. ``confirm`` must echo
        the group id: the research asks the UI to gate an irreversible act, and a
        boolean that a client can send blind gates nothing.
        """
        self.group(room_id, group_id)
        if str(confirm) != group_id:
            raise RevocationBadRequest(
                f"deleting group {group_id} is irreversible; confirm with the group id"
            )

        members = self.members(room_id, group_id)
        permissions = self.permissions(room_id, group_id)
        links = self.links_stopped_by_group(room_id, group_id)
        doomed = (
            [group_id]
            + [m["id"] for m in members]
            + [p["id"] for p in permissions]
            + [link["id"] for link in links]
        )
        slugs = [str(payload(link).get("slug") or "") for link in links]
        self._flag_links(
            links, cause=REVOKED_BY_GROUP, actor=actor, source=source, reason="group deleted"
        )
        result = self.store.bulk_delete(
            doomed,
            room_id=room_id,
            actor=actor,
            source=source,
            hard=HARD_DELETE,
        )
        return {
            "group_id": group_id,
            "room_id": room_id,
            "removed": result["count"],
            "by_collection": {
                GROUPS: 1,
                MEMBERS: len(members),
                PERMISSIONS: len(permissions),
                LINKS: len(links),
            },
            "viewers_kept": [str(payload(m).get("viewer_id") or "") for m in members],
            "slugs_freed": [slug for slug in slugs if slug],
            "slugs_available_now": {
                slug: self.slug_available(room_id, slug) for slug in slugs if slug
            },
            "links_stopped": [link["id"] for link in links],
            "reversible": IRREVERSIBLE,
            "row_kept_for_audit": not result["hard"],
            "audit_records": result["records"],
            "ids": result["ids"],
        }

    def set_permissions(
        self,
        room_id: str,
        group_id: str,
        permissions: Sequence[Mapping[str, Any]] | Mapping[str, Any],
        *,
        actor: str = DEFAULT_ACTOR,
        source: str,
    ) -> dict[str, Any]:
        """Hide or re-expose items for a group by flipping ``view`` / ``download``.

        The researched call is ``PUT .../groups/{gid}/permissions`` - "hide an
        item (view off, download off)". A list is accepted as well as a single
        item, because the vendor's group view edits several at once; each flag
        pair is still its own audited write, so one bad pair does not take the
        others with it.

        ``download`` without ``view`` is refused: see the ``download-requires-
        view`` inference.
        """
        self.group(room_id, group_id)
        wanted = _as_permission_list(permissions)
        applied = []
        for item in wanted:
            document_id = str(item.get("document_id") or "").strip()
            if not document_id:
                raise RevocationBadRequest("each permission needs a document_id")
            view = _flag(item, "view")
            download = _flag(item, "download")
            if download and not view:
                raise RevocationBadRequest(
                    f"{document_id}: download cannot be on while view is off; "
                    "hide an item by turning both off"
                )
            applied.append(
                self._apply_permission(
                    room_id, group_id, document_id, view, download, actor=actor, source=source
                )
            )
        return {"room_id": room_id, "group_id": group_id, "applied": applied}

    def _flag_links(
        self,
        links: Sequence[Mapping[str, Any]],
        *,
        cause: str,
        actor: str,
        source: str,
        reason: str,
    ) -> list[str]:
        """Record *why* a cascade is about to take these links.

        ``bulk_delete`` soft-deletes rows but cannot also write a field onto them -
        ``AuditedWriter`` has no delete, so there is no way to do both in one
        transaction - and a link row that cannot say whether it was revoked by hand
        or swept up with its group cannot answer the question this workflow exists
        for.

        So this runs first, as separate updates, and the cut runs second.

        **That ordering is what makes it safe.** A failure anywhere in this pass
        leaves every link still live with a flag on it; a failure in the
        ``bulk_delete`` rolls the whole set back and also leaves every link live.
        The direction that would be unsafe - access cut with no record of why -
        cannot happen, because the cut is the last step and it is all-or-nothing.
        What a partial failure costs is a flag on a live link, which the next
        cascade or a direct revoke overwrites.

        Only the links are flagged. Every other row in the set is described in
        full by the cascade's own audit row, whose ``before_state`` carries each
        record's payload; writing one update per row as well would multiply the
        audit log for no extra evidence.
        """
        at = utcnow()
        flagged: list[str] = []
        for row in links:
            record_id = str(row.get("id") or "")
            if not record_id:
                continue
            self.store.update(
                record_id,
                {
                    "revoked": True,
                    "revoked_at": at,
                    "revoked_by": actor,
                    "revoked_via": cause,
                    "revocation_reason": reason,
                    "effective_at": at,
                    "grace_period_minutes": GRACE_PERIOD_MINUTES,
                    "cached_copy_recall": CACHED_COPY_RECALL,
                },
                actor=actor,
                source=source,
            )
            flagged.append(record_id)
        return flagged

    def _apply_permission(
        self,
        room_id: str,
        group_id: str,
        document_id: str,
        view: bool,
        download: bool,
        *,
        actor: str = DEFAULT_ACTOR,
        source: str,
    ) -> dict[str, Any]:
        existing = self._rows(
            PERMISSIONS, room_id, {"group_id": group_id, "document_id": document_id}
        )
        at = utcnow()
        if existing:
            record = self.store.update(
                existing[0]["id"],
                {"view": view, "download": download, "changed_at": at},
                actor=actor,
                source=source,
            )
            action = "updated"
        else:
            record = self.store.create(
                PERMISSIONS,
                {
                    "group_id": group_id,
                    "document_id": document_id,
                    "view": view,
                    "download": download,
                    "changed_at": at,
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
            action = "created"
        return {
            "action": action,
            "permission_id": record["id"],
            "document_id": document_id,
            "view": view,
            "download": download,
            "hidden": not view,
        }

    def attach(
        self,
        room_id: str,
        *,
        document_id: str,
        document_team: str | None = None,
        title: str = "",
        actor: str = DEFAULT_ACTOR,
        source: str,
    ) -> dict[str, Any]:
        """Attach a library document to this dataroom.

        Two refusals, both sourced: a frozen dataroom refuses new attachments,
        and "cross-team attaches are refused". A document with no team of its own
        is treated as this dataroom's - see the
        ``document-team-defaults-to-the-dataroom`` inference.
        """
        room = self.room(room_id)
        if self.is_frozen(room):
            raise RevocationFrozen(f"dataroom {room_id} is frozen and refuses new attachments")
        target = str(document_id or "").strip()
        if not target:
            raise RevocationBadRequest("an attachment needs a document_id")
        attached = self.grants(room_id)
        if any(str(payload(g).get("document_id") or "") == target for g in attached):
            raise RevocationConflict(f"document {target} is already attached to {room_id}")

        room_team = self.team_of(room)
        team = self._document_team(document_id, document_team) or room_team
        if team and room_team and team != room_team:
            raise RevocationCrossTeam(
                f"document {target} belongs to team {team!r}, dataroom {room_id} to {room_team!r}"
            )
        return self.store.create(
            GRANTS,
            {
                "document_id": target,
                "document_team": team or None,
                "title": title or target,
                "team": room_team or None,
                "status": "attached",
                "attached_at": utcnow(),
                "attached_by": actor,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )

    def _document_team(self, document_id: str, override: str | None) -> str | None:
        """Whose team a document belongs to.

        An explicit ``override`` from the caller wins, because a caller that knows
        better should not have to write the field first. Otherwise the document
        record's own ``team`` is read, which is what makes the researched
        cross-team refusal reachable from the API alone: without it, a document
        carrying ``team: "Contoso Health"`` would be attachable to Northwind's
        dataroom simply because the attach request did not repeat the team.

        Returns ``None`` when nothing names a team, and the caller treats that as
        "same team" - see the ``document-team-defaults-to-the-dataroom`` inference.
        """
        declared = str(override).strip() if override else ""
        if declared:
            return declared
        record = self.library_document(document_id)
        if record is None:
            return None
        return str(payload(record).get("team") or "").strip() or None

    def detach(
        self,
        room_id: str,
        document_id: str,
        *,
        actor: str = DEFAULT_ACTOR,
        source: str,
    ) -> dict[str, Any]:
        """Detach one document from this dataroom, and nothing else.

        Sourced: detach is a join-row delete - "the team-library document and its
        attachments to other datarooms are left intact." So the only record that
        changes is this room's grant; the ``document`` record and every other
        room's grant for the same document are untouched, and the response says
        so rather than leaving it to be inferred.
        """
        room = self.room(room_id)
        if self.is_frozen(room):
            raise RevocationFrozen(f"dataroom {room_id} is frozen and refuses detaches")
        target = str(document_id or "").strip()
        grants = self.grants(room_id)
        match = next(
            (g for g in grants if str(payload(g).get("document_id") or "") == target), None
        )
        if match is None:
            raise RevocationNotFound(f"document {target} is not attached to room {room_id}")

        self.store.delete(match["id"], actor=actor, source=source, hard=HARD_DELETE)
        elsewhere = [
            other
            for other in self._rows(GRANTS, None, {"document_id": target})
            if other.get("room_id") != room_id and other.get("deleted_at") is None
        ]
        return {
            "room_id": room_id,
            "document_id": target,
            "grant_id": match["id"],
            "detached": True,
            "document_kept": True,
            "other_datarooms_intact": [other["room_id"] for other in elsewhere],
            "library_document_id": target,
            "row_kept_for_audit": True,
        }

    def purge(
        self,
        room_id: str,
        *,
        confirm: str,
        actor: str = DEFAULT_ACTOR,
        source: str,
    ) -> dict[str, Any]:
        """Delete a dataroom's access graph: every link, group and join row.

        Sourced: "Deletion cascades to every link and folder and is
        unrecoverable; documents stay in the team library."

        Viewers are excluded, because "The underlying viewer is kept" is stated
        for the membership delete and the people in a room are not the room's
        content. The core ``room`` and ``document`` records are not this
        feature's to remove - see the ``purge-stops-at-the-access-graph``
        inference - and their survival is returned so it can be asserted.
        """
        self.room(room_id)
        if str(confirm) != room_id:
            raise RevocationBadRequest(
                f"purging dataroom {room_id} is unrecoverable; confirm with the room id"
            )

        doomed: list[str] = []
        by_collection: dict[str, int] = {}
        for collection in CASCADE_COLLECTIONS:
            rows = self._rows(collection, room_id, include_deleted=True)
            live = [row for row in rows if row.get("deleted_at") is None]
            if live:
                doomed.extend(row["id"] for row in live)
            by_collection[collection] = len(live)
        if not doomed:
            return {
                "room_id": room_id,
                "removed": 0,
                "by_collection": by_collection,
                "documents_kept": True,
                "viewers_kept": len(self.viewers(room_id)),
                "reversible": IRREVERSIBLE,
            }
        self._flag_links(
            self._rows(LINKS, room_id),
            cause=REVOKED_BY_ROOM,
            actor=actor,
            source=source,
            reason="dataroom purged",
        )
        result = self.store.bulk_delete(
            doomed,
            room_id=room_id,
            actor=actor,
            source=source,
            hard=HARD_DELETE,
        )
        return {
            "room_id": room_id,
            "removed": result["count"],
            "by_collection": by_collection,
            "documents_kept": True,
            "viewers_kept": len(self.viewers(room_id)),
            "reversible": IRREVERSIBLE,
            "row_kept_for_audit": not result["hard"],
            "audit_records": result["records"],
            "ids": result["ids"],
        }


# --------------------------------------------------------------------------- #
# Small helpers. Pure, so the rules they encode are testable without a store.
# --------------------------------------------------------------------------- #


def _flag(item: Mapping[str, Any], key: str) -> bool:
    """Read a permission flag, defaulting to off.

    Defaulting closed is the point: a permission row that omits a flag has not
    granted it, and a caller that means to grant one has to say so.
    """
    return bool(item.get(key))


def _as_permission_list(permissions: Any) -> list[Mapping[str, Any]]:
    """Accept one permission or a list of them, and refuse anything else."""
    if permissions is None:
        raise RevocationBadRequest("no permissions supplied")
    if isinstance(permissions, Mapping):
        nested = permissions.get("permissions")
        if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
            return [item for item in nested if isinstance(item, Mapping)]
        return [permissions]
    if isinstance(permissions, Sequence) and not isinstance(permissions, (str, bytes)):
        return [item for item in permissions if isinstance(item, Mapping)]
    raise RevocationBadRequest("permissions must be an object or a list of objects")


def _names_this_feature(source: Any, prefix: str) -> bool:
    """Was this audit row written by one of ``prefix``'s routes?

    The recorded source is ``"<METHOD> <prefix><path>"``, so the prefix sits
    *after* the verb. Matching on the front of the string instead would silently
    match nothing - and a trail that silently returns zero rows reads exactly
    like a feature that has done no work.
    """
    verb, separator, path = str(source or "").partition(" ")
    return bool(separator) and path.startswith(prefix)


def _gone_reason(cause: str | None) -> str:
    """Translate a withheld-by cause into the reason a client sees.

    ``None`` means nobody ever held the slug, which is a different answer from a
    revoked one and must not be reported as one: a buyer holding a dead URL needs
    to know whether someone cut them off or whether nobody ever sent it.
    """
    if cause == REVOKED_BY_GROUP:
        return GONE_CASCADE_GROUP
    if cause == REVOKED_BY_ROOM:
        return GONE_CASCADE_ROOM
    if cause == REVOKED_DIRECTLY:
        return GONE_REVOKED
    return NOT_FOUND


def _withheld_by(rows: Iterable[Mapping[str, Any]]) -> str | None:
    """Why a slug no longer resolves, read off the deleted rows that held it.

    The rows survive the revoke, which is what makes this answerable at all - a
    hard delete would leave a slug that simply never existed, and nobody could
    tell a revoked link from a typo afterwards.
    """
    for row in rows:
        if row.get("deleted_at") is None:
            continue
        cause = str(payload(row).get("revoked_via") or REVOKED_DIRECTLY)
        if cause in (REVOKED_BY_GROUP, REVOKED_BY_ROOM, REVOKED_DIRECTLY):
            return cause
    return None


__all__ = ["READ_LIMIT", "Revocation"]
