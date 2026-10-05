"""The reads and the writes: a group, its members, its ACL, a link, and what a viewer sees.

This module is the only one in :mod:`dsr.audience_permissions` that writes. It holds the
store and a clock and nothing else, and it is built per request by the HTTP layer for
exactly that reason: both seams stay overridable in a test without hanging a long-lived
object off ``app.state``, which is a shared file this feature may not edit.

Every write carries an ``actor`` and a ``source``, and both reach the audit log in the same
transaction as the change. No string in this module is a literal route: the feature module
builds each one from its own router, and ``tests/test_wf074_http.py`` asserts every source
this workflow can record names a concrete ``(method, path)`` the host mounted.

Five decisions live here rather than in :mod:`~dsr.audience_permissions.rules`
--------------------------------------------------------------------

The rules module is committed and reviewed, so these are recorded here with the evidence
that settled them rather than appended to a file whose shape a reviewer has already read.

**The link's own permissions keep a marker, because the vendor separates two empty states.**
The CLI page for links, section ``permissions``, says two things that cannot both be read
off a row set: "With no overrides, viewers see the full dataroom", and of ``--clear``,
"Remove all overrides and hide every item". A link that was never scoped and a link whose
scope was cleared therefore both store zero rows and mean opposite things. So a general link
carries :data:`LINK_SCOPE_SET`, set by the first link-permission write of any size including
an empty one, and it is derived by the engine and never accepted from a caller: a caller that
could set the marker could declare a link scoped without granting anything. Jev chose this
over letting absence always mean the full room and over writing a full-room scope during a
read, at confidence 1.00, audit ``jev-20261005T051919-25896-59873``.

**A row the payload drops is revoked, not deleted, and the whole replace is one
transaction.** :func:`~dsr.audience_permissions.rules.apply_full_replace` returns the keys to
drop, and "drop" has to mean something the audit log can describe. :class:`AuditedWriter`
offers ``create`` and ``update`` and no delete, and
:meth:`AuditedDatabase.delete` refuses to run inside an open transaction, so the choices were
a soft delete per row outside the block or a revoked stamp inside it. The block wins: a
half-applied full replace is a permissions set that grants something the payload removed, or
hides something it kept, and the grid a rep is reading would show a state nobody asked for.

**Ancestor auto-open does not overrule a row the payload itself wrote.** The source says
"Ancestor folders of any item made visible are automatically set to ``can_view: true`` so the
folder tree stays navigable", and it does not say what happens when the same payload grants
the ancestor ``can_view: false``. The rep's explicit row wins, and the folder stays closed,
because the grid renders the stored rows and a row that is silently rewritten on the way in
is a row the rep cannot reason about. The response names the folders whose own row kept them
closed under :data:`ANCESTORS_WITHHELD`, so the outcome is visible rather than silent.

**A link's own download switch gates the per-item flag, and both facts are reported.** The
same CLI section documents ``--download`` as "Allow downloading (also needs
``--allow-download`` on the link)". So an item whose row says ``can_download: true`` is still
view-only on a link whose own switch is off. :meth:`AudiencePermissionsEngine.view` reports
the row's flag and the effective one separately, because collapsing them is how a grid comes
to say "downloadable" over a link that serves view-only.

**A room with no library rows has no items, and that is not an empty permissions set.** The
item rows belong to WF-003's library and this workflow only reads them, so a room nothing has
been ingested into yields no items. The response says so in
:data:`NO_ITEMS_NOTE` rather than returning an empty grid, because an empty grid and a room
with nothing in it are the two states a rep most needs told apart.

The room reference
------------------

Two different filters are used for two different collections, and mixing them up is silent.
This workflow's own rows carry :data:`~dsr.audience_permissions.vocabulary.ROOM_REF` in the
payload so ``find()`` can filter them by room and by group in one indexed lookup. The
library's rows do not: ``dsr.library`` puts the room on the record envelope and nothing in
``data``, so they are read with ``list(room_id=...)``. A ``find()`` against a room reference
on the library's rows returns nothing at all, and a room filter that matches no rows is the
defect the payload-side twin exists to prevent.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from dsr.audience_permissions import rules, vocabulary as vocab
from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Keys this engine adds to a stored row
# --------------------------------------------------------------------------- #
#
# Every one of these lives in `data`, so a team can read and reshape it without a migration
# and the envelope stays exactly the seven keys the store reserves.

#: The key a link row records its membership with. Only a general link's marker is ever
#: consulted; a group link refuses link overrides, so it has no link scope to mark.
LINK_SCOPE_SET = "link_scope_set"

#: The key a link row names its group with, and the key a member or permission row names its
#: group with. Same string on all three so one ``find()`` reaches a group's whole fan-out.
GROUP_ID_FIELD = vocab.GROUP_ID_FIELD

#: The key a link permission row names its link with.
LINK_ID_FIELD = "link_id"

#: Set on a permission row a full replace no longer wants. The row stays, because the audit
#: log has to be able to say the row was there and is not in force, and a hard delete would
#: leave a link's permission history unreadable.
REVOKED_AT = "revoked_at"

#: When the row was last written. Read-only provenance on the payload; nothing filters on it.
GRANTED_AT = "granted_at"

#: The link's own download switch, quoted: "Allow downloading (also needs ``--allow-download``
#: on the link)". Off by default, which is the CLI's own default.
ALLOW_DOWNLOAD_FIELD = "allow_download"

#: The name on a link row. Optional on the wire because the CLI generates one.
NAME_FIELD = "name"

#: A group's own download switch is not in the evidence and is not implemented. A rep who
#: wants an audience that may browse but not download gets that per item, which is the shape
#: the source documents.
GROUP_DOWNLOAD_NOT_SUPPORTED = (
    "A group has no download switch of its own. Grant download per item, which is where the "
    "source puts it."
)

# --------------------------------------------------------------------------- #
# Scope states, for a general link
# --------------------------------------------------------------------------- #
#
# The three readings of a general link's own ACL, so a page can say which one is in force
# instead of showing an empty grid that looks like a permissions fault.

SCOPE_UNSCOPED = "unscoped"
SCOPE_CLEARED = "cleared"
SCOPE_SET = "scoped"

#: A group link has no link scope of its own, so it reports the group's state instead. Kept
#: beside the other three rather than folded into one, because "this link carries its own
#: permissions" would be a false thing to say about a link whose permissions a group owns.
SCOPE_FROM_GROUP = vocab.SCOPE_GROUP

SCOPE_STATE_LABELS = {
    SCOPE_UNSCOPED: "This link was never scoped. Every item in the room is visible on it.",
    SCOPE_CLEARED: (
        "This link's scope was cleared. Every item is hidden, which is what clearing the set "
        "is for."
    ),
    SCOPE_SET: "This link carries its own per-item permissions.",
    SCOPE_FROM_GROUP: (
        "This link belongs to a group, so the group's permissions decide what it shows. The "
        "link's own overrides are refused."
    ),
}

#: What a room with nothing ingested into looks like, said rather than rendered as an empty
#: grid. The item rows are WF-003's and this workflow only reads them.
NO_ITEMS_NOTE = (
    "This room holds no library documents or folders, so there is nothing to grant. The items "
    "a permission points at are the room's own library rows, and this workflow reads them and "
    "never writes them."
)

#: The folders a payload kept closed itself. Named in the response so the outcome of
#: "auto-open does not overrule your own row" is visible rather than inferred from a grid.
ANCESTORS_WITHHELD = "ancestors_withheld"

# --------------------------------------------------------------------------- #
# Collections this engine reads but does not own
# --------------------------------------------------------------------------- #

ROOM_COLLECTION = "room"

#: ``find`` and ``list`` both cap a page at 1000 rows, and neither reports truncation. A
#: permissions grid that silently showed 1000 of 1400 grants would be an over-grant on
#: screen, so the count is read separately and the response says when a page was short.
MAX_PAGE = 1000


class RoomNotFound(LookupError):
    """The room a row would hang on does not exist.

    Declared here rather than in the rules module because this is a store-shaped question
    only the engine asks. Mapping it is safe for the same reason every other mapping in this
    feature is safe: the host refuses a second feature registering a handler for the same
    type, and nothing else in the product raises it.
    """


class AudiencePermissionsEngine:
    """Every read and write this workflow performs, over one audited store.

    ``now`` is a callable rather than a value so a test can move the clock by hand, which is
    what makes the ordering of the ancestor writes and the grants assertable.
    """

    def __init__(self, store: RecordStore, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._now = now or rules.utcnow

    # ----------------------------------------------------------------- #
    # Groups
    # ----------------------------------------------------------------- #

    def create_group(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """A named audience, and the states the guide says a new one ships in.

        The guide's first sentence about a new group is "A new group sees **nothing** until you
        grant permissions", so nothing is granted here. A group created with no permissions is
        the researched starting state and the response says so, rather than leaving the rep to
        find out from an empty grid.

        ``allow_all`` and ``domains`` are both normalised and capped by the rules, so a caller
        that goes through the engine cannot bypass the ``max 100`` bound by building the list
        itself. That bound is the vendor's own request bound: it protects the caller's account,
        so it is checked where the list is built rather than in the HTTP layer.
        """

        self._require_room(room_id)
        body = dict(payload or {})
        name = str(body.get(NAME_FIELD) or "").strip()
        if not name:
            raise rules.AudienceRuleError(
                "A group needs a name.", {NAME_FIELD: "Give the audience a name."}
            )
        allow_all = _as_bool(body.get(vocab.ALLOW_ALL), field=vocab.ALLOW_ALL)
        domains = rules.normalise_domains(body.get(vocab.DOMAINS_FIELD))

        record = self.store.create(
            vocab.GROUP_COLLECTION,
            {
                vocab.ROOM_REF: room_id,
                NAME_FIELD: name,
                vocab.ALLOW_ALL: allow_all,
                vocab.DOMAINS_FIELD: domains,
                "created_at": rules.stamp(self._now()),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self._group_payload(record)

    def groups(self, room_id: str | None = None) -> list[dict[str, Any]]:
        """Every audience, oldest first, optionally narrowed to one room."""

        records = self.store.list(vocab.GROUP_COLLECTION, limit=MAX_PAGE, order_by="created_at")
        rows = []
        for record in records:
            data = dict(record.get("data") or {})
            if room_id and data.get(vocab.ROOM_REF) != room_id:
                continue
            rows.append(self._group_payload(record))
        return rows

    def read_group(self, group_id: str) -> dict[str, Any]:
        """One audience with its members, its links and its per-item permissions."""

        record = self._group_record(group_id)
        return self._group_payload(record)

    def update_group(
        self,
        group_id: str,
        changes: Mapping[str, Any],
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Change a group's name, its domains or its ``allow_all`` switch.

        Membership is not patched here. Adding and removing members are the two calls the
        source names, and a patch that could add a member would make the idempotent
        "already-present members are skipped" behaviour unreachable from one of the two paths
        into it.

        Revocation needs no reissue: the guide says "Later changes to the group's permissions
        or members apply to the existing link immediately, no re-sharing", which is true here
        because nothing about the link is cached on it. Every view re-reads the group's rows.
        """

        self._group_record(group_id)
        body = dict(changes or {})
        unknown = sorted(set(body) - {NAME_FIELD, vocab.ALLOW_ALL, vocab.DOMAINS_FIELD})
        if unknown:
            raise rules.AudienceRuleError(
                f"A group carries no key named {unknown[0]}.",
                {
                    unknown[0]: (
                        f"A group is {NAME_FIELD}, {vocab.ALLOW_ALL} and {vocab.DOMAINS_FIELD}. "
                        "Members are added and removed through the member calls."
                    )
                },
            )

        patch: dict[str, Any] = {}
        if NAME_FIELD in body:
            name = str(body[NAME_FIELD] or "").strip()
            if not name:
                raise rules.AudienceRuleError(
                    "A group needs a name.", {NAME_FIELD: "Give the audience a name."}
                )
            patch[NAME_FIELD] = name
        if vocab.ALLOW_ALL in body:
            patch[vocab.ALLOW_ALL] = _as_bool(body[vocab.ALLOW_ALL], field=vocab.ALLOW_ALL)
        if vocab.DOMAINS_FIELD in body:
            patch[vocab.DOMAINS_FIELD] = rules.normalise_domains(body[vocab.DOMAINS_FIELD])

        if patch:
            self.store.update(group_id, patch, actor=actor, source=source)
        return self._group_payload(self._group_record(group_id))

    def _group_record(self, group_id: str) -> dict[str, Any]:
        record = self.store.get(group_id)
        if record is None or record.get("collection") != vocab.GROUP_COLLECTION:
            raise rules.GroupNotFound(group_id)
        return dict(record)

    def _group_payload(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A stored group as the API returns it, with its three counts read back.

        ``member_count`` and ``link_count`` are the vendor's own field names on the group, and
        both are counted from the rows rather than kept in a counter, so a count cannot drift
        from the rows it describes and a member removed through another route cannot leave a
        group claiming somebody who is gone.
        """

        group_id = record.get("id")
        data = dict(record.get("data") or {})
        members = self._member_records(group_id)
        links = self._link_records(group_id=group_id)
        permissions = self._permission_index(group_id)
        live = {key: row["entry"] for key, row in permissions.items()}

        return {
            "id": group_id,
            "room_id": data.get(vocab.ROOM_REF) or record.get("room_id"),
            NAME_FIELD: data.get(NAME_FIELD),
            vocab.ALLOW_ALL: bool(data.get(vocab.ALLOW_ALL)),
            vocab.DOMAINS_FIELD: list(data.get(vocab.DOMAINS_FIELD) or []),
            "created_at": data.get("created_at"),
            "updated_at": record.get("updated_at"),
            "revision": record.get("revision"),
            **rules.group_counts(members, links, list(live.values())),
            "domains_summary": vocab.DOMAIN_RULE_TEXT,
            "download_switch": GROUP_DOWNLOAD_NOT_SUPPORTED,
            "sees_nothing_until_granted": vocab.SCOPE_DESCRIPTIONS[vocab.SCOPE_GROUP],
            vocab.OWNER_FIELD: vocab.SCOPE_OWNER,
            vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    # ----------------------------------------------------------------- #
    # Members
    # ----------------------------------------------------------------- #

    def add_members(
        self,
        group_id: str,
        emails: Any,
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Add member addresses. Idempotent, silent, and capped.

        Quoted: "Viewers are created for unknown addresses; already-present members are
        skipped, so the call is idempotent. **No invitation emails are sent.**" So the return
        value separates what was added from what was already there, and it carries
        :data:`~dsr.audience_permissions.vocabulary.NO_INVITATIONS` on every call. A workflow
        that invites people elsewhere must not read this as having invited anybody.
        """

        record = self._group_record(group_id)
        room_id = (record.get("data") or {}).get(vocab.ROOM_REF) or record.get("room_id")
        wanted = rules.normalise_member_emails(emails)
        present = {
            str((row.get("data") or {}).get(vocab.EMAIL_FIELD) or "").strip().lower()
            for row in self._member_records(group_id)
        }

        added: list[str] = []
        skipped: list[str] = []
        moment = rules.stamp(self._now())
        for address in wanted:
            if address in present:
                skipped.append(address)
                continue
            self.store.create(
                vocab.MEMBER_COLLECTION,
                {
                    vocab.ROOM_REF: room_id,
                    GROUP_ID_FIELD: group_id,
                    vocab.EMAIL_FIELD: address,
                    "added_at": moment,
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
            present.add(address)
            added.append(address)

        return {
            "group_id": group_id,
            "requested": len(wanted),
            "added": added,
            "added_count": len(added),
            "skipped": skipped,
            "skipped_count": len(skipped),
            "idempotent": True,
            "invitations_sent": 0,
            "invitation_note": vocab.NO_INVITATIONS,
            "member_count": len(self._member_records(group_id)),
            vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    def remove_member(
        self,
        group_id: str,
        member_id: str,
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Remove one member. The next view of the link reflects it.

        The guide: "Later changes to the group's permissions or members apply to the existing
        link immediately, no re-sharing." Nothing on the link is reissued and nothing is
        cached, so this returns the address that lost access rather than a new link.

        The member's permission rows are left alone. They are the group's, not the member's,
        and removing a row that a second member would also be covered by would silently shrink
        the grant rather than the audience.
        """

        self._group_record(group_id)
        record = self.store.get(member_id)
        data = dict((record or {}).get("data") or {})
        if (
            record is None
            or record.get("collection") != vocab.MEMBER_COLLECTION
            or data.get(GROUP_ID_FIELD) != group_id
        ):
            raise rules.MemberNotFound(member_id)

        self.store.delete(member_id, actor=actor, source=source)
        return {
            "group_id": group_id,
            "member_id": member_id,
            "email": data.get(vocab.EMAIL_FIELD),
            "revoked_at": rules.stamp(self._now()),
            "link_reissued": False,
            "applies_immediately": True,
            "member_count": len(self._member_records(group_id)),
            "note": (
                "The link this group's viewers hold is unchanged. The next request from this "
                "address is refused, because membership is re-evaluated on every view."
            ),
            vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    def members(self, group_id: str) -> list[dict[str, Any]]:
        """Every member of one audience, by address so the order is stable."""

        self._group_record(group_id)
        rows = []
        for record in sorted(
            self._member_records(group_id),
            key=lambda row: str((row.get("data") or {}).get(vocab.EMAIL_FIELD) or ""),
        ):
            data = dict(record.get("data") or {})
            rows.append(
                {
                    "id": record.get("id"),
                    "group_id": data.get(GROUP_ID_FIELD),
                    "room_id": data.get(vocab.ROOM_REF),
                    vocab.EMAIL_FIELD: data.get(vocab.EMAIL_FIELD),
                    "added_at": data.get("added_at"),
                    "invitation_sent": False,
                }
            )
        return rows

    def _member_records(self, group_id: str) -> list[dict[str, Any]]:
        return self.store.find(
            vocab.MEMBER_COLLECTION,
            {GROUP_ID_FIELD: group_id},
            limit=MAX_PAGE,
        )

    # ----------------------------------------------------------------- #
    # Items: the library rows a permission points at
    # ----------------------------------------------------------------- #

    def items(self, room_id: str) -> list[dict[str, Any]]:
        """Every document and folder in one room, folders first.

        Read from the library's own collections and never written. The filter is the record
        envelope's ``room_id`` rather than a payload key, because ``dsr.library`` puts the room
        there and nowhere else: a ``find()`` on a payload room reference would match nothing,
        and a room filter that matches nothing is a room filter that looks like an empty room.
        """

        rows: list[dict[str, Any]] = []
        for item_type, collection in vocab.ITEM_TYPE_COLLECTIONS.items():
            for record in self.store.list(collection, room_id=room_id, limit=MAX_PAGE):
                data = dict(record.get("data") or {})
                rows.append(
                    {
                        "item_id": record.get("id"),
                        "item_type": item_type,
                        "name": data.get("name") or data.get("title") or record.get("id"),
                        "collection": collection,
                        "parent_folder_id": data.get(vocab.PARENT_FOLDER_FIELD)
                        or vocab.ROOT_FOLDER,
                        "format": data.get("format"),
                    }
                )
        rows.sort(key=lambda row: (row["item_type"], str(row["name"])))
        return rows

    def _parent_map(self, room_id: str | None) -> dict[str, dict[str, Any]]:
        """Record id to the payload carrying its ``parentFolderId``, for both collections.

        Documents are in this map as well as folders, and that is not a widening:
        :func:`~dsr.audience_permissions.rules.ancestors_of` reads the *item's* own parent from
        the same map before it walks the folders above it. A map holding only folders resolves
        every document to the room root, so no document would ever open a folder and the
        ancestor rule would silently do nothing.

        An empty room reference yields an empty map, which is the behaviour the walk already has
        for a missing parent: no ancestors found, and the grant still lands.
        """

        if not room_id:
            return {}
        found: dict[str, dict[str, Any]] = {}
        for collection in vocab.ITEM_COLLECTIONS:
            for record in self.store.list(collection, room_id=room_id, limit=MAX_PAGE):
                found[record["id"]] = dict(record.get("data") or {})
        return found

    # ----------------------------------------------------------------- #
    # Group permissions: delta
    # ----------------------------------------------------------------- #

    def group_permissions(self, group_id: str) -> dict[str, Any]:
        """The whole grid: every item in the room and what this audience may do with it.

        Unlike the view, this returns the hidden items too, each with the reason it is hidden,
        because "nobody granted it" and "somebody revoked it" are different facts for a rep
        looking at a grid. That distinction is what
        :data:`~dsr.audience_permissions.vocabulary.HIDDEN_CAN_VIEW_FALSE` is for.
        """

        record = self._group_record(group_id)
        room_id = (record.get("data") or {}).get(vocab.ROOM_REF) or record.get("room_id")
        index = self._permission_index(group_id)
        grid, dangling = self._grid(room_id, index)

        return {
            "group_id": group_id,
            "room_id": room_id,
            "scope": vocab.SCOPE_GROUP,
            "semantics": vocab.SCOPE_SEMANTICS[vocab.SCOPE_GROUP],
            "semantics_text": vocab.SCOPE_DESCRIPTIONS[vocab.SCOPE_GROUP],
            "count": len(grid),
            "items": grid,
            "rows": sorted(index.values(), key=lambda row: row["entry_key"]),
            "granted": len([row for row in grid if row[vocab.ROW_PRESENT_FIELD]]),
            "hidden": len([row for row in grid if not row[vocab.CAN_VIEW]]),
            "view_only": len([row for row in grid if row["state"] == vocab.VIEW_ONLY]),
            "dangling": dangling,
            "item_count": len(self.items(room_id)),
            "no_items_note": None if grid else NO_ITEMS_NOTE,
            "items_page_truncated": self._truncated(
                vocab.PERMISSION_COLLECTION, GROUP_ID_FIELD, group_id
            ),
            vocab.OWNER_FIELD: vocab.SCOPE_OWNER,
            vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
            vocab.NOT_PROOF_FIELD: vocab.NOT_PROOF,
        }

    def set_group_permissions(
        self,
        group_id: str,
        payload: Any,
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Upsert the entries sent. Items the payload omits keep what they had.

        Group semantics, quoted: "Items not listed keep their current state (delta semantics -
        matching the dashboard)".

        Two things the rules module cannot do because they need the store: the upsert itself is
        a read-then-insert-or-update, and that has to be one transaction. Outside a block the
        two halves are two transactions and a concurrent grant for the same item between them
        is lost. Inside one they commit together or not at all.

        Validation runs before the transaction opens, so a rejected payload writes nothing: an
        audit row describing a change that did not happen is a row a reader has to learn to
        discount.
        """

        record = self._group_record(group_id)
        room_id = (record.get("data") or {}).get(vocab.ROOM_REF) or record.get("room_id")
        entries = _entries_from(payload)
        index = self._permission_index(group_id)
        existing = {key: row["entry"] for key, row in index.items()}

        outcome = rules.apply_delta(existing, entries)
        merged = {rules.entry_key(entry): entry for entry in outcome["entries"]}
        named = {rules.entry_key(entry) for entry in entries}

        planned, withheld = self._plan_ancestors(merged, entries, named, room_id)
        written = self._write_permissions(
            room_id=room_id,
            collection=vocab.PERMISSION_COLLECTION,
            owner_field=GROUP_ID_FIELD,
            owner_id=group_id,
            index=index,
            entries=entries,
            planned=planned,
            source=source,
            actor=actor,
        )

        return {
            "group_id": group_id,
            "room_id": room_id,
            "scope": vocab.SCOPE_GROUP,
            "semantics": outcome["semantics"],
            "semantics_text": vocab.SCOPE_DESCRIPTIONS[vocab.SCOPE_GROUP],
            "touched": outcome[vocab.TOUCHED_KEY],
            "touched_count": len(outcome[vocab.TOUCHED_KEY]),
            "untouched": outcome[vocab.UNTOUCHED_KEY],
            "untouched_count": len(outcome[vocab.UNTOUCHED_KEY]),
            "auto_opened": written["auto_opened"],
            "auto_opened_count": len(written["auto_opened"]),
            ANCESTORS_WITHHELD: withheld,
            "entries": outcome["entries"],
            "permission_count": written["live_rows"],
            "dangling_items": self._dangling(room_id, written["live_keys"]),
            "no_reissue_needed": True,
            vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    # ----------------------------------------------------------------- #
    # Links
    # ----------------------------------------------------------------- #

    def create_link(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Mint one link, either scoped to a group or scoped to nothing.

        ``audience_type: "group"`` with a ``group_id`` takes the group's visibility. ``"general"``
        starts unscoped, which the CLI states means the full dataroom, and
        :data:`LINK_SCOPE_SET` is written false so a later read can tell unscoped from cleared.

        Email gating is not a field and there is no route that sets it. The guide makes it
        unconditional for a group link: "Group links are always email-gated; a viewer must be a
        member (by email or domain) to get in, unless the group has ``allow_all``." A stored flag
        could be set to false on a group link, and that is the one thing the evidence says cannot
        happen, so the response derives it instead of storing it.
        """

        self._require_room(room_id)
        body = dict(payload or {})
        kind = _audience_type(body.get(vocab.AUDIENCE_TYPE_FIELD) or vocab.AUDIENCE_GENERAL)
        group_id = str(body.get(GROUP_ID_FIELD) or "").strip()

        if kind == vocab.AUDIENCE_GROUP:
            if not group_id:
                raise rules.AudienceRuleError(
                    "A group link needs a group.",
                    {GROUP_ID_FIELD: "Name the group this link is scoped to."},
                )
            group_record = self._group_record(group_id)
            group_room = (group_record.get("data") or {}).get(vocab.ROOM_REF)
            if group_room != room_id:
                raise rules.GroupNotFound(
                    f"group {group_id} belongs to a different room than {room_id}"
                )
        else:
            group_id = ""

        record = self.store.create(
            vocab.LINK_COLLECTION,
            {
                vocab.ROOM_REF: room_id,
                NAME_FIELD: str(body.get(NAME_FIELD) or f"Link for {room_id}"),
                vocab.AUDIENCE_TYPE_FIELD: kind,
                GROUP_ID_FIELD: group_id or None,
                ALLOW_DOWNLOAD_FIELD: _as_bool(
                    body.get(ALLOW_DOWNLOAD_FIELD), field=ALLOW_DOWNLOAD_FIELD
                ),
                LINK_SCOPE_SET: False,
                "created_at": rules.stamp(self._now()),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self._link_payload(record)

    def links(
        self, room_id: str | None = None, group_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Every link this workflow minted, optionally narrowed by room or group."""

        records = self.store.list(vocab.LINK_COLLECTION, limit=MAX_PAGE, order_by="created_at")
        rows = []
        for record in records:
            data = dict(record.get("data") or {})
            if room_id and data.get(vocab.ROOM_REF) != room_id:
                continue
            if group_id and data.get(GROUP_ID_FIELD) != group_id:
                continue
            rows.append(self._link_payload(record))
        return rows

    def read_link(self, link_id: str) -> dict[str, Any]:
        """One link, its audience and its scope state."""

        return self._link_payload(self._link_record(link_id))

    def _link_record(self, link_id: str) -> dict[str, Any]:
        record = self.store.get(link_id)
        if record is None or record.get("collection") != vocab.LINK_COLLECTION:
            raise rules.LinkNotFound(link_id)
        return dict(record)

    def _link_records(self, group_id: str | None = None) -> list[dict[str, Any]]:
        if group_id is None:
            return self.store.list(vocab.LINK_COLLECTION, limit=MAX_PAGE, order_by="created_at")
        return self.store.find(vocab.LINK_COLLECTION, {GROUP_ID_FIELD: group_id}, limit=MAX_PAGE)

    def _link_payload(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        kind = str(data.get(vocab.AUDIENCE_TYPE_FIELD) or vocab.AUDIENCE_GENERAL)
        index = self._link_permission_index(record.get("id"))
        live = {key: row["entry"] for key, row in index.items()}
        allow_download = bool(data.get(ALLOW_DOWNLOAD_FIELD))
        scoped = kind == vocab.AUDIENCE_GROUP or bool(data.get(LINK_SCOPE_SET))

        return {
            "id": record.get("id"),
            "room_id": data.get(vocab.ROOM_REF) or record.get("room_id"),
            NAME_FIELD: data.get(NAME_FIELD),
            vocab.AUDIENCE_TYPE_FIELD: kind,
            "audience_label": vocab.AUDIENCE_LABELS.get(kind, kind),
            GROUP_ID_FIELD: data.get(GROUP_ID_FIELD),
            ALLOW_DOWNLOAD_FIELD: allow_download,
            "allow_download_meaning": (
                "An item still needs its own can_download true. The link's switch is a second "
                "gate on top of the per-item flag."
            ),
            "link_scope_set": bool(data.get(LINK_SCOPE_SET)),
            "scope_state": self._scope_state(kind, bool(data.get(LINK_SCOPE_SET)), len(live)),
            "scope_state_meaning": SCOPE_STATE_LABELS[
                self._scope_state(kind, bool(data.get(LINK_SCOPE_SET)), len(live))
            ],
            "link_overrides_allowed": kind != vocab.AUDIENCE_GROUP,
            "email_gated": kind == vocab.AUDIENCE_GROUP,
            "email_gate_note": vocab.EMAIL_GATE_NOTE if kind == vocab.AUDIENCE_GROUP else None,
            "permission_count": len(live),
            "created_at": data.get("created_at"),
            "updated_at": record.get("updated_at"),
            "revision": record.get("revision"),
            "scoped": scoped,
            "no_reissue_needed": True,
            vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    @staticmethod
    def _scope_state(kind: str, marker: bool, live_rows: int) -> str:
        """Which of the three readings of a link's own ACL is in force.

        A group link has no link scope of its own, so it reports the group's default-deny
        state. A general link that was never scoped reports the full room. A general link whose
        scope was set and then emptied reports nothing.
        """

        if kind == vocab.AUDIENCE_GROUP:
            return SCOPE_FROM_GROUP
        if not marker:
            return SCOPE_UNSCOPED
        return SCOPE_CLEARED if live_rows == 0 else SCOPE_SET

    # ----------------------------------------------------------------- #
    # Link permissions: full replace
    # ----------------------------------------------------------------- #

    def link_permissions(self, link_id: str) -> dict[str, Any]:
        """The link's own overrides, and the whole grid beside them."""

        record = self._link_record(link_id)
        data = dict(record.get("data") or {})
        room_id = data.get(vocab.ROOM_REF) or record.get("room_id")
        index = self._link_permission_index(link_id)
        grid, dangling = self._grid(room_id, index)
        kind = str(data.get(vocab.AUDIENCE_TYPE_FIELD) or vocab.AUDIENCE_GENERAL)

        return {
            "link_id": link_id,
            "room_id": room_id,
            vocab.AUDIENCE_TYPE_FIELD: kind,
            "scope": vocab.SCOPE_LINK,
            "semantics": vocab.SCOPE_SEMANTICS[vocab.SCOPE_LINK],
            "semantics_text": vocab.SCOPE_DESCRIPTIONS[vocab.SCOPE_LINK],
            "scope_state": self._scope_state(kind, bool(data.get(LINK_SCOPE_SET)), len(index)),
            "scope_state_meaning": SCOPE_STATE_LABELS[
                self._scope_state(kind, bool(data.get(LINK_SCOPE_SET)), len(index))
            ],
            "count": len(grid),
            "items": grid,
            "rows": sorted(index.values(), key=lambda row: row["entry_key"]),
            "granted": len([row for row in grid if row[vocab.ROW_PRESENT_FIELD]]),
            "hidden": len([row for row in grid if not row[vocab.CAN_VIEW]]),
            "view_only": len([row for row in grid if row["state"] == vocab.VIEW_ONLY]),
            "dangling": dangling,
            "revoked": self._revoked_rows(link_id),
            "item_count": len(self.items(room_id)),
            "no_items_note": None if grid else NO_ITEMS_NOTE,
            "items_page_truncated": self._truncated(
                vocab.LINK_PERMISSION_COLLECTION, LINK_ID_FIELD, link_id
            ),
            vocab.OWNER_FIELD: vocab.SCOPE_OWNER,
            vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
            vocab.NOT_PROOF_FIELD: vocab.NOT_PROOF,
        }

    def set_link_permissions(
        self,
        link_id: str,
        payload: Any,
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Replace the link's overrides with the payload. Omitted items lose their override.

        Link semantics, quoted: "The complete desired permission state for this link
        (full-replace semantics...) An empty array clears all overrides, which hides every
        item on the link."

        A group link is refused before the payload is even read. The OpenAPI description of this
        endpoint says a link override on ``audience_type: "group"`` is "Rejected with 422 on
        links with ``audience_type: \\"group\\"`` - their group determines visibility; switch the
        link to ``audience_type: \\"general\\"`` first." The CLI page is looser and says the
        overrides are ignored. The refusal is what this build implements, because the CLI
        sentence describes a client that does not report the rejection, and an ignored override
        would leave a rep editing a grid that is not the one in force.
        """

        record = self._link_record(link_id)
        data = dict(record.get("data") or {})
        # First, because it is a property of the link rather than of the payload. A group link
        # refuses this call whatever the body says, and a 422 about the payload would send the
        # rep looking in the wrong place.
        kind = rules.require_link_scope(data.get(vocab.AUDIENCE_TYPE_FIELD))
        room_id = data.get(vocab.ROOM_REF) or record.get("room_id")

        entries = _entries_from(payload)
        index = self._link_permission_index(link_id)
        existing = {key: row["entry"] for key, row in index.items()}

        outcome = rules.apply_full_replace(existing, entries)
        wanted = {rules.entry_key(entry): entry for entry in outcome["entries"]}
        named = {rules.entry_key(entry) for entry in entries}

        planned, withheld = self._plan_ancestors(wanted, entries, named, room_id)

        live_after = {key: row["entry"] for key, row in index.items() if key in wanted}
        live_after.update({key: row["entry"] for key, row in self._planned_index(planned).items()})
        written = self._write_permissions(
            room_id=room_id,
            collection=vocab.LINK_PERMISSION_COLLECTION,
            owner_field=LINK_ID_FIELD,
            owner_id=link_id,
            index=index,
            entries=entries,
            planned=planned,
            revoke=[key for key in index if key not in wanted],
            mark_scope=True,
            scope_owner_field=LINK_ID_FIELD,
            scope_owner_id=link_id,
            source=source,
            actor=actor,
        )

        return {
            "link_id": link_id,
            "room_id": room_id,
            "scope": vocab.SCOPE_LINK,
            "audience_type": kind,
            "semantics": outcome["semantics"],
            "semantics_text": vocab.SCOPE_DESCRIPTIONS[vocab.SCOPE_LINK],
            "touched": outcome[vocab.TOUCHED_KEY],
            "touched_count": len(outcome[vocab.TOUCHED_KEY]),
            "dropped": outcome[vocab.DROPPED_KEY],
            "dropped_count": len(outcome[vocab.DROPPED_KEY]),
            "auto_opened": written["auto_opened"],
            "auto_opened_count": len(written["auto_opened"]),
            ANCESTORS_WITHHELD: withheld,
            "entries": outcome["entries"],
            "permission_count": written["live_rows"],
            "scope_state": self._scope_state(kind, True, len(live_after)),
            "scope_state_meaning": SCOPE_STATE_LABELS[
                self._scope_state(kind, True, len(live_after))
            ],
            LINK_SCOPE_SET: True,
            "scope_set_note": (
                "This link now carries its own scope, so an empty set hides everything rather "
                "than opening the room. That is the documented --clear behaviour."
            ),
            "dangling_items": self._dangling(room_id, written["live_keys"]),
            vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    def _revoked_rows(self, link_id: str) -> list[dict[str, Any]]:
        """The rows a full replace took out of force, newest first."""

        rows = []
        for record in self.store.find(
            vocab.LINK_PERMISSION_COLLECTION, {LINK_ID_FIELD: link_id}, limit=MAX_PAGE
        ):
            data = dict(record.get("data") or {})
            if not data.get(REVOKED_AT):
                continue
            rows.append(
                {
                    "item_id": data.get("item_id"),
                    "item_type": data.get("item_type"),
                    "entry_key": f"{data.get('item_type')}:{data.get('item_id')}",
                    "revoked_at": data.get(REVOKED_AT),
                    "last_can_view": data.get(vocab.CAN_VIEW),
                    "last_can_download": data.get(vocab.CAN_DOWNLOAD),
                }
            )
        rows.sort(key=lambda row: (row["entry_key"], str(row["revoked_at"])))
        return rows

    # ----------------------------------------------------------------- #
    # The view: membership, then the filtered tree
    # ----------------------------------------------------------------- #

    def view(self, link_id: str, email: Any = None) -> dict[str, Any]:
        """What this address sees on this link, resolved now and filtered before any bytes.

        The specification's data flow, in one clause: the resolved permission set "filters the
        dataroom tree server-side before any bytes are sent". So the hidden items are counted
        and named by reason but never returned, and the returned list is the tree the viewer
        would be served.

        Membership is re-read on every call. The guide says "Later changes to the group's
        permissions or members apply to the existing link immediately, no re-sharing", so
        nothing here is cached on the link and a member removed a minute ago is refused now.

        A general link performs no membership check, and says so. Email gating for a general
        link belongs to whichever workflow owns the link's other gates; this workflow refuses to
        report a verdict it did not reach.
        """

        record = self._link_record(link_id)
        data = dict(record.get("data") or {})
        room_id = data.get(vocab.ROOM_REF) or record.get("room_id")
        kind = str(data.get(vocab.AUDIENCE_TYPE_FIELD) or vocab.AUDIENCE_GENERAL)
        allow_download = bool(data.get(ALLOW_DOWNLOAD_FIELD))

        group_record: dict[str, Any] | None = None
        membership: dict[str, Any] | None = None
        admitted = True
        denied: str | None = None

        if kind == vocab.AUDIENCE_GROUP:
            group_record = self._group_record(str(data.get(GROUP_ID_FIELD) or ""))
            membership = rules.resolve_membership(
                {**(group_record.get("data") or {}), GROUP_ID_FIELD: group_record.get("id")},
                self._member_records(str(group_record.get("id"))),
                email,
            )
            admitted = rules.is_member(membership)
            if not admitted:
                denied = vocab.NOT_A_MEMBER

        # The scope state is computed from the link's own live row count rather than a literal,
        # because the cleared/scoped distinction is exactly that count. Reporting `cleared` for a
        # link holding one grant would tell a rep their scope is empty while the room is open.
        scope_state = self._scope_state(
            kind,
            bool(data.get(LINK_SCOPE_SET)),
            len(self._link_permission_index(link_id)),
        )
        if kind == vocab.AUDIENCE_GROUP:
            index = self._permission_index(str(group_record.get("id"))) if group_record else {}
            effective = {key: row["entry"] for key, row in index.items()}
        elif scope_state == SCOPE_UNSCOPED:
            # Sourced: "With no overrides, viewers see the full dataroom."
            effective = None
        else:
            index = self._link_permission_index(link_id)
            effective = {key: row["entry"] for key, row in index.items()}

        if not admitted:
            visible: list[dict[str, Any]] = []
            hidden_count = 0
            by_reason: dict[str, int] = {vocab.NOT_A_MEMBER: 0}
        elif effective is None:
            items = self.items(room_id)
            visible = [self._view_row(item, None, allow_download) for item in items]
            hidden_count = 0
            by_reason = {}
        else:
            visible, hidden_count, by_reason = self._filtered(room_id, effective, allow_download)

        return {
            "link_id": link_id,
            "room_id": room_id,
            vocab.AUDIENCE_TYPE_FIELD: kind,
            "audience_label": vocab.AUDIENCE_LABELS.get(kind, kind),
            GROUP_ID_FIELD: data.get(GROUP_ID_FIELD),
            "email": (membership or {}).get(vocab.EMAIL_FIELD) or (str(email) if email else None),
            "membership": membership,
            "membership_step": (membership or {}).get(vocab.MEMBERSHIP_STEP_FIELD),
            "membership_meaning": vocab.MEMBERSHIP_LABELS.get(
                (membership or {}).get(vocab.MEMBERSHIP_STEP_FIELD) or vocab.MEMBERSHIP_NONE,
                vocab.MEMBERSHIP_LABELS[vocab.MEMBERSHIP_NONE],
            ),
            "membership_checked": membership is not None,
            "admitted": admitted,
            "denied_reason": denied,
            "scope": vocab.SCOPE_GROUP if kind == vocab.AUDIENCE_GROUP else vocab.SCOPE_LINK,
            "scope_state": scope_state,
            "scope_state_meaning": SCOPE_STATE_LABELS[scope_state],
            ALLOW_DOWNLOAD_FIELD: allow_download,
            "filtered_server_side": True,
            "item_count": len(visible),
            "items": visible,
            "hidden_count": hidden_count,
            "hidden_by_reason": by_reason,
            "no_items_note": None if self.items(room_id) else NO_ITEMS_NOTE,
            "re_evaluated_every_view": True,
            vocab.OWNER_FIELD: vocab.SCOPE_OWNER,
            vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
            vocab.NOT_PROOF_FIELD: vocab.NOT_PROOF,
        }

    def _filtered(
        self, room_id: str | None, effective: Mapping[str, Mapping[str, Any]], allow_download: bool
    ) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
        """Every item in the room, decided against the ACL, with the hidden ones withheld.

        The hidden rows are counted and tallied by reason and then dropped. Returning them
        would defeat the filter, and dropping them without a count would make an empty room and
        a fully hidden room look the same to the caller.
        """

        visible: list[dict[str, Any]] = []
        by_reason: dict[str, int] = {}
        for item in self.items(room_id):
            entry = rules.entry_for(effective, str(item["item_id"]), item["item_type"])
            decision = rules.decide_item(entry)
            if not decision[vocab.CAN_VIEW]:
                reason = str(decision[vocab.DENY_REASON_FIELD])
                by_reason[reason] = by_reason.get(reason, 0) + 1
                continue
            visible.append(self._view_row(item, entry, allow_download))
        return visible, sum(by_reason.values()), dict(sorted(by_reason.items()))

    @staticmethod
    def _view_row(
        item: Mapping[str, Any],
        entry: Mapping[str, Any] | None,
        allow_download: bool,
    ) -> dict[str, Any]:
        """One item as the viewer receives it.

        ``can_download_row`` and ``can_download`` are both reported. The row's flag is the
        grant; the effective flag also has to clear the link's own switch, which the source
        calls out: "Allow downloading (also needs ``--allow-download`` on the link)". Reporting
        one of them would make the grid say downloadable over a link that serves view-only.
        """

        decision = rules.decide_item(entry)
        row_download = bool(decision[vocab.CAN_DOWNLOAD])
        return {
            "item_id": item["item_id"],
            "item_type": item["item_type"],
            "name": item.get("name"),
            "collection": item.get("collection"),
            "parent_folder_id": item.get("parent_folder_id"),
            "state": decision["state"],
            "can_view": decision[vocab.CAN_VIEW],
            "can_download_row": row_download,
            "can_download": row_download and allow_download,
            "download_blocked_by_link": row_download and not allow_download,
            "auto_opened": bool((entry or {}).get(vocab.AUTO_OPENED_FIELD)),
            "auto_opened_meaning": (
                "This folder was opened for a document inside it rather than chosen by a rep."
                if (entry or {}).get(vocab.AUTO_OPENED_FIELD)
                else None
            ),
            "row_present": decision[vocab.ROW_PRESENT_FIELD],
        }

    # ----------------------------------------------------------------- #
    # The board
    # ----------------------------------------------------------------- #

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        """The page's headline numbers, read back rather than accumulated.

        The number that matters most is the first one after the counts: how many groups have
        no permissions at all. Those are the groups in the guide's shipped state, "A new group
        sees **nothing** until you grant permissions", and a board that only showed totals
        would let that read as a healthy empty room.
        """

        groups = self.groups(room_id)
        links = self.links(room_id)
        items = self.items(room_id) if room_id else []
        blank = [row for row in groups if row[vocab.PERMISSION_COUNT_FIELD] == 0]
        dangling: list[dict[str, Any]] = []
        hidden_without_rows = 0
        if room_id:
            for row in groups:
                index = self._permission_index(row["id"])
                grid, rows_dangling = self._grid(room_id, index)
                dangling.extend(rows_dangling)
                hidden_without_rows += len(
                    [
                        cell
                        for cell in grid
                        if cell[vocab.DENY_REASON_FIELD] == vocab.DENY_NO_PERMISSION_ROW
                    ]
                )

        return {
            "groups": len(groups),
            "members": sum(row[vocab.MEMBER_COUNT_FIELD] for row in groups),
            "links": len(links),
            "group_links": len(
                [row for row in links if row[vocab.AUDIENCE_TYPE_FIELD] == vocab.AUDIENCE_GROUP]
            ),
            "general_links": len(
                [row for row in links if row[vocab.AUDIENCE_TYPE_FIELD] == vocab.AUDIENCE_GENERAL]
            ),
            "permissions": sum(row[vocab.PERMISSION_COUNT_FIELD] for row in groups),
            "groups_with_no_permissions": len(blank),
            "groups_with_no_permissions_names": [row[NAME_FIELD] for row in blank],
            "open_groups": len([row for row in groups if row[vocab.ALLOW_ALL]]),
            "domain_groups": len([row for row in groups if row[vocab.DOMAINS_FIELD]]),
            "items": len(items),
            "items_hidden_for_default": hidden_without_rows,
            "dangling_permissions": dangling,
            "by_audience_type": _tally(links, vocab.AUDIENCE_TYPE_FIELD),
            "by_item_type": _tally(items, "item_type"),
            vocab.OWNER_FIELD: vocab.SCOPE_OWNER,
            vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
            vocab.NOT_PROOF_FIELD: vocab.NOT_PROOF,
        }

    # ----------------------------------------------------------------- #
    # The grid, shared by both scopes
    # ----------------------------------------------------------------- #

    def _grid(
        self, room_id: str | None, index: Mapping[str, Mapping[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Every item in the room beside the ACL's answer for it, plus the dangling rows.

        A row naming an item that is not there is reported rather than dropped. The recorded
        decision for a dangling ``item_id`` is that it is stored as given and reported, because
        it is a fact about the room rather than an error in the grant, and because the library's
        rows are this workflow's to read and not to police.
        """

        items = self.items(room_id or "")
        known = {(row["item_type"], str(row["item_id"])) for row in items}
        grid: list[dict[str, Any]] = []
        dangling: list[dict[str, Any]] = []
        for item in items:
            key = rules.entry_key(item)
            entry = (index.get(key) or {}).get("entry")
            grid.append(
                {
                    "item_id": item["item_id"],
                    "item_type": item["item_type"],
                    "item_type_label": vocab.ITEM_TYPE_LABELS.get(
                        item["item_type"], item["item_type"]
                    ),
                    "name": item["name"],
                    "parent_folder_id": item["parent_folder_id"],
                    "can_view": bool(entry.get(vocab.CAN_VIEW)) if entry else False,
                    "can_download": bool(entry.get(vocab.CAN_DOWNLOAD)) if entry else False,
                    "auto_opened": bool((entry or {}).get(vocab.AUTO_OPENED_FIELD)),
                    **rules.decide_item(entry),
                }
            )
        for key, row in sorted(index.items()):
            item_type, item_id = key.split(":", 1)
            if (item_type, item_id) in known:
                continue
            dangling.append(
                {
                    "item_id": item_id,
                    "item_type": item_type,
                    "entry_key": key,
                    "can_view": row["entry"].get(vocab.CAN_VIEW),
                    "can_download": row["entry"].get(vocab.CAN_DOWNLOAD),
                    "row_id": row.get("id"),
                    "why": (
                        "No document or folder in this room carries this id. The grant is kept "
                        "and reported; the item rows belong to the room's library."
                    ),
                }
            )
        return grid, dangling

    def _dangling(self, room_id: str | None, live_keys: Sequence[str]) -> list[dict[str, Any]]:
        known = {f"{row['item_type']}:{row['item_id']}" for row in self.items(room_id)}
        return [
            {"item_key": key, "why": "No document or folder in this room carries this id."}
            for key in sorted(live_keys)
            if key not in known
        ]

    # ----------------------------------------------------------------- #
    # Permission rows
    # ----------------------------------------------------------------- #

    def _permission_index(self, group_id: str) -> dict[str, dict[str, Any]]:
        """A group's live rows, keyed by entry key, each with its record id.

        The record id is what makes the upsert possible: this repository gives every record its
        own uuid, so an ``item_id`` is not a primary key and a row for ``(group, type, id)`` has
        to be found before it can be updated rather than inserted a second time.

        Revoked rows are filtered out in Python rather than by ``find()``, because the field that
        marks them is null on a live row and the dynamic index cannot express "is null".
        """

        return self._index(vocab.PERMISSION_COLLECTION, GROUP_ID_FIELD, group_id)

    def _link_permission_index(self, link_id: str) -> dict[str, dict[str, Any]]:
        return self._index(vocab.LINK_PERMISSION_COLLECTION, LINK_ID_FIELD, link_id)

    def _index(self, collection: str, owner_field: str, owner_id: str) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        for record in self.store.find(collection, {owner_field: owner_id}, limit=MAX_PAGE):
            data = dict(record.get("data") or {})
            if data.get(REVOKED_AT):
                continue
            entry = _entry_from_row(data)
            key = rules.entry_key(entry)
            index[key] = {"id": record.get("id"), "entry": entry, "entry_key": key, "data": data}
        return index

    @staticmethod
    def _planned_index(planned: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        return {
            rules.entry_key(entry): {"entry": dict(entry), "entry_key": rules.entry_key(entry)}
            for entry in planned
        }

    def _plan_ancestors(
        self,
        merged: Mapping[str, Mapping[str, Any]],
        entries: Sequence[Mapping[str, Any]],
        named: set[str],
        room_id: str | None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """The ancestor rows to write, and the folders the payload kept closed itself.

        The rules module decides which ancestors a grant needs. Two adjustments belong here
        because they are about the payload rather than about the tree: an ancestor the payload
        names is left exactly as the payload wrote it, whatever it wrote, and the ones it named
        closed are returned so the response can say so.
        """

        parents = self._parent_map(room_id)
        planned = rules.plan_ancestor_grants(list(merged.values()), merged, parents)
        kept: list[dict[str, Any]] = []
        wanted: list[dict[str, Any]] = []
        for row in planned:
            if rules.entry_key(row) in named:
                entry = merged.get(rules.entry_key(row)) or {}
                if not entry.get(vocab.CAN_VIEW):
                    kept.append(
                        {
                            "item_id": row["item_id"],
                            "item_type": row["item_type"],
                            "can_view": False,
                            "why": (
                                "This call granted a document inside it and set can_view false "
                                "on the folder itself. Your own row was kept, so the folder "
                                "stays closed and the document is not reachable through the "
                                "folder tree."
                            ),
                        }
                    )
                continue
            wanted.append(row)
        return wanted, kept

    def _write_permissions(
        self,
        *,
        room_id: str | None,
        collection: str,
        owner_field: str,
        owner_id: str,
        index: Mapping[str, Mapping[str, Any]],
        entries: Sequence[Mapping[str, Any]],
        planned: Sequence[Mapping[str, Any]],
        revoke: Sequence[str] = (),
        mark_scope: bool = False,
        scope_owner_field: str | None = None,
        scope_owner_id: str | None = None,
        source: str | None,
        actor: str | None,
    ) -> dict[str, Any]:
        """Insert, update and revoke rows inside one transaction, and nothing outside it.

        :class:`AuditedWriter` offers ``create`` and ``update`` and no delete, and the
        single-record methods on the database refuse to run while a transaction is open. So a
        revoke is a stamp on the row and a full replace is atomic, which is the half of the
        choice that matters: a replace that dropped four of its six rows and then failed would
        leave a permissions set nobody asked for, and the grid a rep reads is that set.

        The ancestors are written after the grants in the same block, so a grant and the folders
        it opens commit together or neither does.
        """

        moment = rules.stamp(self._now())
        writes = [dict(entry) for entry in entries] + [dict(entry) for entry in planned]
        auto_opened = [rules.entry_key(entry) for entry in planned]
        revoked: list[str] = []

        with self.store.db.transaction(actor=actor, source=source) as tx:
            for entry in writes:
                key = rules.entry_key(entry)
                patch = {
                    "item_id": entry["item_id"],
                    "item_type": entry["item_type"],
                    vocab.CAN_VIEW: bool(entry[vocab.CAN_VIEW]),
                    vocab.CAN_DOWNLOAD: bool(entry[vocab.CAN_DOWNLOAD]),
                    vocab.AUTO_OPENED_FIELD: key in auto_opened,
                    GRANTED_AT: moment,
                    REVOKED_AT: None,
                }
                existing = index.get(key)
                if existing is None:
                    tx.create(
                        collection,
                        {vocab.ROOM_REF: room_id, owner_field: owner_id, **patch},
                        room_id=room_id,
                        actor=actor,
                        source=source,
                    )
                else:
                    tx.update(existing["id"], patch, actor=actor, source=source)

            for key in revoke:
                existing = index.get(key)
                if existing is None or existing["id"] is None:
                    continue
                tx.update(
                    existing["id"],
                    {REVOKED_AT: moment},
                    actor=actor,
                    source=source,
                )
                revoked.append(key)

            if mark_scope and scope_owner_field and scope_owner_id:
                tx.update(
                    scope_owner_id,
                    {LINK_SCOPE_SET: True},
                    actor=actor,
                    source=source,
                )

        # What is live after this call: every row that was written, plus every row the index
        # held that this call neither wrote nor revoked. A delta never drops, so its answer is
        # the whole index; a full replace answers only the rows it wanted.
        written_keys = {rules.entry_key(entry) for entry in writes}
        dropped = set(revoke)
        surviving = (written_keys | (set(index) - written_keys)) - dropped
        return {
            "auto_opened": sorted(auto_opened),
            "live_keys": sorted(surviving),
            "revoked": sorted(revoked),
            "live_rows": len(surviving),
        }

    def _truncated(self, collection: str, owner_field: str, owner_id: str) -> bool:
        """Whether a page came back short, so a grid never silently omits grants."""

        try:
            total = self.store.count_where(collection, {owner_field: owner_id})
        except Exception:  # pragma: no cover - count_where mirrors find and cannot differ here
            return False
        return total > MAX_PAGE

    # ----------------------------------------------------------------- #
    # Rooms
    # ----------------------------------------------------------------- #

    def _require_room(self, room_id: str) -> None:
        record = self.store.get(room_id)
        if record is None or record.get("collection") != ROOM_COLLECTION:
            raise RoomNotFound(room_id)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _entries_from(payload: Any) -> list[dict[str, Any]]:
    """The permission list out of a body that may wrap it.

    Both shapes are accepted because the source's own request objects differ: the group call
    sends ``{"permissions": [...]}`` and the link call sends ``{"permissions": [...]}`` too, but
    a caller holding just the array is the shape the CLI takes. Anything else is a validation
    failure rather than an empty set, because silently treating an unreadable body as "clear
    everything" is the one interpretation a full-replace call must never make.
    """

    body = payload
    if isinstance(body, Mapping):
        if "permissions" not in body:
            raise rules.AudienceRuleError(
                "A permissions call needs a permissions list.",
                {"permissions": 'Send {"permissions": [...]}.'},
            )
        body = body["permissions"]
    elif payload is None:
        raise rules.AudienceRuleError(
            "A permissions call needs a permissions list.",
            {"permissions": 'Send {"permissions": [...]}, or [] to clear every override.'},
        )
    return rules.build_permissions(body)


def _entry_from_row(data: Mapping[str, Any]) -> dict[str, Any]:
    """A stored row reduced to the four researched keys.

    The reduction matters twice. :func:`~dsr.audience_permissions.rules.apply_delta` compares
    the stored state against the payload to decide what a call actually touched, so a row read
    whole would differ from a freshly built entry on its own bookkeeping keys and every
    re-send would look like a change. And it is what makes a row this workflow did not write
    still readable through the closed entry shape.
    """

    entry = {
        "item_id": str(data.get("item_id") or ""),
        "item_type": str(data.get("item_type") or ""),
        vocab.CAN_VIEW: bool(data.get(vocab.CAN_VIEW)),
        vocab.CAN_DOWNLOAD: bool(data.get(vocab.CAN_DOWNLOAD)),
    }
    if data.get(vocab.AUTO_OPENED_FIELD):
        entry[vocab.AUTO_OPENED_FIELD] = True
    return entry


def _as_bool(value: Any, *, field: str) -> bool:
    """A boolean the caller has to mean.

    ``"false"`` and ``0`` are accepted because a form and a query string both hand one over, and
    anything else that is not a boolean is refused. A switch that treated the string "false" as
    true would be the worst kind of permissions defect: the row would say the opposite of what
    the rep saw.
    """

    if isinstance(value, bool) or value is None:
        return bool(value)
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off", ""):
        return False
    raise rules.AudienceRuleError(
        f"{field} must be true or false.",
        {field: f"Use true or false, not {value!r}."},
    )


def _audience_type(value: Any) -> str:
    kind = str(value or "").strip().lower()
    if kind not in vocab.AUDIENCE_TYPES:
        raise rules.AudienceRuleError(
            f"{kind or 'an empty value'!r} is not an audience type.",
            {
                vocab.AUDIENCE_TYPE_FIELD: (
                    f"audience_type must be {vocab.AUDIENCE_GENERAL} or {vocab.AUDIENCE_GROUP}."
                )
            },
        )
    return kind


def _tally(rows: Iterable[Mapping[str, Any]], key: str) -> dict[str, int]:
    """How many rows carry each value for ``key``, largest first then alphabetical.

    Sorted here so the page and the API agree on the order without either re-sorting, which is
    the kind of small disagreement that becomes a flaky test.
    """

    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
