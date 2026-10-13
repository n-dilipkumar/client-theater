"""The reads and the writes: a room policy, a link policy, a grant, a decision, a download.

This module is the only one in :mod:`dsr.view_only_access` that touches a store. It holds the
store and a clock and nothing else, and the HTTP layer builds it per request for exactly
that reason: both seams stay overridable in a test without hanging a long-lived object off
``app.state``, which is a shared file this feature may not edit.

Every write carries an ``actor`` and a ``source``, and both reach the audit log in the same
transaction as the change. No string in this module is a literal route: the feature module
builds each one from its own router, and ``tests/test_wf072_http.py`` asserts every source
this workflow can record names a concrete ``(method, path)`` the host mounted.

The boundary this module respects
---------------------------------

The room policy and the link policy are this workflow's own rows in ``wf072_room_policy`` and
``wf072_link``. The per-item ``can_download`` grant is read from WF-074's permission rows,
by collection name, through the store. This module never imports ``dsr.audience_permissions``
and never writes those rows on the request path: the ownership split is the decision recorded
as ``jev-20261013T150724-2296-44663``.

Why the download path writes one row in one transaction
-------------------------------------------------------

The specification says a successful fetch "writes a ``View`` row with ``downloaded_at`` and
``download_type``, which is what the audit trail reads", and the issue asks to enforce that
the row lands "in the same transaction as the bytes being released". The bytes are released
by the caller, so what this workflow can guarantee is that the decision and the row are one
unit: the row is created through an audited write, and a refused fetch creates nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from dsr.store import RecordStore
from dsr.view_only_access import rules, vocabulary as vocab

#: How many rows a single read returns. The store clamps to 1000; this workflow's board and
#: its download log are small, so one page is the whole set.
MAX_PAGE = 200


class ViewOnlyAccessEngine:
    """The writes and the reads for one request, over the process-wide audited store."""

    def __init__(self, store: RecordStore, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._now = now or (lambda: datetime.now(timezone.utc))

    # ----------------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------------- #

    def _timestamp(self) -> str:
        return self._now().isoformat()

    def _room_or_404(self, room_id: str) -> dict[str, Any]:
        record = self.store.get(room_id)
        if record is None or record.get("collection") != vocab.ROOM_COLLECTION:
            raise rules.RoomNotFound(f"No such room: {room_id}")
        return record

    def _link_or_404(self, link_id: str) -> dict[str, Any]:
        record = self.store.get(link_id)
        if record is None or record.get("collection") != vocab.LINK_COLLECTION:
            raise rules.LinkNotFound(f"No such governed link: {link_id}")
        return record

    @staticmethod
    def _shape_link(record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "room_id": data.get(vocab.ROOM_REF) or record.get("room_id"),
            vocab.NAME_FIELD: data.get(vocab.NAME_FIELD),
            vocab.ALLOW_DOWNLOAD: rules.coerce_flag(
                data.get(vocab.ALLOW_DOWNLOAD), vocab.ALLOW_DOWNLOAD
            ),
            "revision": record.get("revision"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
        }

    @staticmethod
    def _shape_room_policy(record: Mapping[str, Any] | None, room_id: str) -> dict[str, Any]:
        data = dict((record or {}).get("data") or {})
        return {
            "room_id": room_id,
            "policy_id": (record or {}).get("id"),
            "configured": record is not None,
            vocab.BULK_DOWNLOAD: rules.coerce_flag(
                data.get(vocab.BULK_DOWNLOAD), vocab.BULK_DOWNLOAD
            ),
            "revision": (record or {}).get("revision"),
            "updated_at": (record or {}).get("updated_at"),
        }

    def _room_policy_record(self, room_id: str) -> dict[str, Any] | None:
        found = self.store.find(
            vocab.ROOM_POLICY_COLLECTION, {vocab.ROOM_REF: room_id}, limit=MAX_PAGE
        )
        return found[0] if found else None

    # ----------------------------------------------------------------------- #
    # The room policy: the room-wide escalation
    # ----------------------------------------------------------------------- #

    def room_policy(self, room_id: str) -> dict[str, Any]:
        """One room's policy, or the documented default when nothing is set.

        Read-only, and it never writes a row: a read that created a default row would make
        "never configured" and "configured off" the same state, which is the distinction the
        page most needs to keep.
        """

        return self._shape_room_policy(self._room_policy_record(room_id), room_id)

    def set_room_policy(
        self, room_id: str, payload: Mapping[str, Any], *, actor: str | None, source: str | None
    ) -> dict[str, Any]:
        """Set the room's bulk download control. Mirrors ``PATCH /v1/datarooms/{id}``.

        Only ``bulk_download`` is interpreted; any other field the payload carries is stored
        and carried, because the specification's settings triple also holds ``conversations``
        and ``agents`` and those belong to other workflows.
        """

        self._room_or_404(room_id)
        changes = rules.validate_room_policy(payload)
        changes[vocab.ROOM_REF] = room_id
        existing = self._room_policy_record(room_id)
        if existing is None:
            record = self.store.create(
                vocab.ROOM_POLICY_COLLECTION,
                changes,
                room_id=room_id,
                actor=actor,
                source=source,
            )
        else:
            record = self.store.update(existing["id"], changes, actor=actor, source=source)
        return self._shape_room_policy(record, room_id)

    # ----------------------------------------------------------------------- #
    # The link policy: the per-link switch
    # ----------------------------------------------------------------------- #

    def links(self, room_id: str | None = None) -> list[dict[str, Any]]:
        if room_id:
            records = self.store.find(
                vocab.LINK_COLLECTION, {vocab.ROOM_REF: room_id}, limit=MAX_PAGE
            )
        else:
            records = self.store.list(vocab.LINK_COLLECTION, limit=MAX_PAGE)
        return [self._shape_link(record) for record in records]

    def create_link(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str | None,
    ) -> dict[str, Any]:
        """Create a governed link. Mirrors ``POST /v1/links``.

        ``allow_download`` defaults to the documented ``false``, so a link created without
        the field is view-only and must be explicitly escalated.
        """

        self._room_or_404(room_id)
        data = rules.validate_link_policy(payload)
        data.setdefault(vocab.ALLOW_DOWNLOAD, vocab.DEFAULT_ALLOW_DOWNLOAD)
        data[vocab.ALLOW_DOWNLOAD] = rules.coerce_flag(
            data.get(vocab.ALLOW_DOWNLOAD), vocab.ALLOW_DOWNLOAD
        )
        data[vocab.ROOM_REF] = room_id
        record = self.store.create(
            vocab.LINK_COLLECTION, data, room_id=room_id, actor=actor, source=source
        )
        return self._shape_link(record)

    def read_link(self, link_id: str) -> dict[str, Any]:
        return self._shape_link(self._link_or_404(link_id))

    def update_link(
        self,
        link_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str | None,
    ) -> dict[str, Any]:
        """Toggle the link's ``allow_download`` in place. Mirrors ``PATCH /v1/links/{id}``.

        The id and the room are untouched, which is the specification's "the URL and existing
        viewers are unaffected". A viewer already looking at the link sees the new setting on
        their next request, because the control is read per request and nothing caches it.
        """

        self._link_or_404(link_id)
        changes = rules.validate_link_policy(payload)
        record = self.store.update(link_id, changes, actor=actor, source=source)
        return self._shape_link(record)

    # ----------------------------------------------------------------------- #
    # The per-item grant, read from WF-074's rows
    # ----------------------------------------------------------------------- #

    def grant(self, item_id: str) -> dict[str, Any]:
        """This item's ``can_download`` grant, read from WF-074's permission rows.

        Read-only over two collections this workflow does not own. The rows are combined and
        handed to :func:`~dsr.view_only_access.rules.grant_from_rows`, which tells a live
        grant, a revoked row and a missing row apart.
        """

        records: list[dict[str, Any]] = []
        for collection in vocab.PERMISSION_SOURCE_COLLECTIONS:
            records.extend(self.store.find(collection, {vocab.ITEM_ID: item_id}, limit=MAX_PAGE))
        # Newest first, so a later grant wins over an earlier one across both collections.
        records.sort(key=lambda record: str(record.get("updated_at") or ""), reverse=True)
        grant = rules.grant_from_rows(records, item_id)
        grant[vocab.ITEM_ID] = item_id
        return grant

    # ----------------------------------------------------------------------- #
    # The decision
    # ----------------------------------------------------------------------- #

    def evaluate(self, link_id: str, item_id: str) -> dict[str, Any]:
        """The three-way AND for one item on one link, read-only.

        The room control comes from this workflow's room policy, the link control from its
        link policy, and the item control from WF-074's grant. The decision is returned
        complete: every gate, the verdict, and which gate withheld the file.
        """

        link = self.read_link(link_id)
        room = self.room_policy(link["room_id"])
        grant = self.grant(item_id)
        decision = rules.decide_download(
            room[vocab.BULK_DOWNLOAD], link[vocab.ALLOW_DOWNLOAD], grant[vocab.CAN_DOWNLOAD]
        )
        return self._decision_payload(link, item_id, room, grant, decision)

    def _decision_payload(
        self,
        link: Mapping[str, Any],
        item_id: str,
        room: Mapping[str, Any],
        grant: Mapping[str, Any],
        decision: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "link_id": link["id"],
            "room_id": link["room_id"],
            vocab.ITEM_ID: item_id,
            **decision,
            "controls": {
                vocab.BULK_DOWNLOAD: bool(room[vocab.BULK_DOWNLOAD]),
                vocab.ALLOW_DOWNLOAD: bool(link[vocab.ALLOW_DOWNLOAD]),
                vocab.CAN_DOWNLOAD: bool(grant[vocab.CAN_DOWNLOAD]),
            },
            "room_policy_configured": bool(room["configured"]),
            "grant": dict(grant),
            vocab.OWNER_FIELD: vocab.SCOPE_OWNER,
            vocab.READ_SOURCE_FIELD: vocab.READ_SOURCE,
            vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    def bulk(self, link_id: str, item_ids: Iterable[str]) -> dict[str, Any]:
        """The room-wide escalation: download every named document as a zip.

        The zip is gated by the room and the link controls and by every item it would carry.
        The item ids are named by the caller because the library's rows belong to WF-003 and
        this workflow reads the grants, not the tree.
        """

        link = self.read_link(link_id)
        room = self.room_policy(link["room_id"])
        items = [{"item_id": item_id, **self.grant(item_id)} for item_id in list(item_ids)]
        decision = rules.decide_bulk(room[vocab.BULK_DOWNLOAD], link[vocab.ALLOW_DOWNLOAD], items)
        return {
            "link_id": link["id"],
            "room_id": link["room_id"],
            **decision,
            "items": items,
            "controls": {
                vocab.BULK_DOWNLOAD: bool(room[vocab.BULK_DOWNLOAD]),
                vocab.ALLOW_DOWNLOAD: bool(link[vocab.ALLOW_DOWNLOAD]),
            },
            vocab.OWNER_FIELD: vocab.SCOPE_OWNER,
            vocab.READ_SOURCE_FIELD: vocab.READ_SOURCE,
            vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    # ----------------------------------------------------------------------- #
    # The download path: enforcement is synchronous
    # ----------------------------------------------------------------------- #

    def fetch(
        self,
        link_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str | None,
    ) -> dict[str, Any]:
        """Serve one document, and record the download when the three controls agree.

        This is the synchronous enforcement the specification describes: "None scheduled;
        enforcement is synchronous on the download path." The decision is re-read here rather
        than trusted from the caller, so a link whose switch was turned off a moment ago is
        refused now.

        A permitted fetch writes the ``View`` row and releases the bytes as one unit: the
        row is created through an audited write, so its audit entry lands in the same
        transaction as the row. A refused fetch writes nothing and raises
        :class:`~dsr.view_only_access.rules.DownloadRefused` carrying the decision.
        """

        item_id = str(payload.get(vocab.ITEM_ID) or "").strip()
        if not item_id:
            raise rules.PolicyRefused(
                "A download must name an item.",
                {vocab.ITEM_ID: "Send the item_id whose bytes you want."},
            )
        kind = rules.coerce_download_type(payload.get(vocab.DOWNLOAD_TYPE))
        decision = self.evaluate(link_id, item_id)
        if not decision[vocab.DOWNLOAD_FIELD]:
            raise rules.DownloadRefused(
                "This download is view-only: the three controls did not all permit it.",
                decision,
            )

        view_payload = {
            vocab.ITEM_ID: item_id,
            vocab.VIEW_TYPE: vocab.VIEW_TYPE_DOCUMENT,
            vocab.VIEWER_EMAIL: payload.get(vocab.VIEWER_EMAIL),
            vocab.VIEWED_AT: self._timestamp(),
            vocab.DOWNLOADED_AT: self._timestamp(),
            vocab.DOWNLOAD_TYPE: kind,
        }
        # One transaction holds the download event. The bytes are released by the caller, so
        # what this workflow guarantees is that the row and its audit entry are one write,
        # and that a refusal writes neither.
        with self.store.db.transaction(actor=actor, source=source) as writer:
            view = writer.create(
                vocab.VIEW_COLLECTION,
                view_payload,
                room_id=decision["room_id"],
            )
        return {
            "view": self._shape_view(view),
            "download": True,
            "download_type": kind,
            "decision": decision,
            vocab.OWNER_FIELD: vocab.SCOPE_OWNER,
            vocab.READ_SOURCE_FIELD: vocab.READ_SOURCE,
            vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    # ----------------------------------------------------------------------- #
    # Reads
    # ----------------------------------------------------------------------- #

    @staticmethod
    def _shape_view(record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        kind = data.get(vocab.DOWNLOAD_TYPE)
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            vocab.ITEM_ID: data.get(vocab.ITEM_ID),
            vocab.VIEWER_EMAIL: data.get(vocab.VIEWER_EMAIL),
            vocab.VIEW_TYPE: data.get(vocab.VIEW_TYPE),
            vocab.VIEWED_AT: data.get(vocab.VIEWED_AT),
            vocab.DOWNLOADED_AT: data.get(vocab.DOWNLOADED_AT),
            vocab.DOWNLOAD_TYPE: kind,
            "downloaded": bool(kind),
        }

    def views(self, link_id: str | None = None, room_id: str | None = None) -> list[dict[str, Any]]:
        if link_id:
            link = self.read_link(link_id)
            room_id = link["room_id"]
        if room_id:
            records = self.store.list(vocab.VIEW_COLLECTION, room_id=room_id, limit=MAX_PAGE)
        else:
            records = self.store.list(vocab.VIEW_COLLECTION, limit=MAX_PAGE)
        return [self._shape_view(record) for record in records]

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        """The board's headline numbers. Reads only, and every count is measured."""

        links = self.links(room_id)
        if room_id:
            policies = [self.room_policy(room_id)]
            views = self.views(room_id=room_id)
        else:
            policy_records = self.store.list(vocab.ROOM_POLICY_COLLECTION, limit=MAX_PAGE)
            policies = [
                self._shape_room_policy(record, record.get("room_id")) for record in policy_records
            ]
            views = self.views()
        return {
            "rooms_configured": sum(1 for policy in policies if policy["configured"]),
            "bulk_download": sum(1 for policy in policies if policy[vocab.BULK_DOWNLOAD]),
            "links": len(links),
            "allow_download": sum(1 for link in links if link[vocab.ALLOW_DOWNLOAD]),
            "view_only_links": sum(1 for link in links if not link[vocab.ALLOW_DOWNLOAD]),
            "downloads": sum(1 for view in views if view["downloaded"]),
            "views": len(views),
            vocab.OWNER_FIELD: vocab.SCOPE_OWNER,
            vocab.READ_SOURCE_FIELD: vocab.READ_SOURCE,
            vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }
