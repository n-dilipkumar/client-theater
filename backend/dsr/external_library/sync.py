"""The external content library: add a cloud file, and keep it current.

This is WF-008. The researched flow is a single operation that "creates a new
Library content item **linked to the external source file** and places it in
the target parent folder", with ``autoSync`` deciding whether the link is
living or a one-time snapshot. Everything here serves those two modes.

Modelling decisions, and why
----------------------------
**The linkage lives inside the library item's own ``data``**, under a nested
``source`` object, rather than in a side table or a provider-specific record.
The researched flow is "a *linked* Library content item" whose behaviour is
a property of that item, and the project's rule is that a field must never need
a migration: nested inside ``data`` the whole set is indexed by the dynamic
index and filterable with ``?where={"source.autoSync":true}`` the moment it is
written, with no schema work. A separate collection would also put a join in
the middle of "show me this item's sync state", which is a query the UI asks on
every render. (Recorded as decision ``nested_document_source`` via
``tools/jev.py``; see ``orchestration/decisions/jev-audit.jsonl``.)

**No file bytes are ever copied.** The item holds a pointer plus the metadata
the source reported, and the version that was applied. That is what "linked,
not a copy" means, and it is why a re-sync is a version comparison rather than
a download.

**Names come from the research, not from taste.** ``externalSource``,
``externalContentId``, ``parentFolderId``, ``autoSync`` and ``contentId`` are
the documented field names, kept verbatim so someone reading the research can
match them to the API. The item's provider-independent fields sit beside them
under ``source`` and carry no provider names at all.

Where the research is thin, this module says so at the point of the decision:
:class:`ExternalLibrarySync.resync` and :meth:`sync_status` implement the
referenced-but-unpublished ``GetContentSyncStatus`` companion, whose shape is a
design inference, and re-linking an already-linked file is made idempotent,
which the research does not specify.

**The audit ``source`` is an argument, never a constant.** Every write method
takes the path (or job name) that actually reached it, and the HTTP layer builds
that from ``router.prefix`` rather than writing a URL out in either place. See
the class docstring for why the branch's version of this was a defect.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from dsr.db.audited import new_id, utcnow
from dsr.external_library.errors import ExternalSyncError
from dsr.external_library.ratelimit import RateLimiter
from dsr.external_library.sources import SourceAdapter, SourceFile, get_source, source_registry

#: The library item collection. Named to match the ``document`` collection the
#: rest of the project already uses, so a linked file is just a document with a
#: ``source`` block and shows up wherever documents do.
ITEM_COLLECTION = "document"

#: Connection records. Separate from items because a connection outlives every
#: file linked through it, and its own fields are free-form.
CONNECTION_COLLECTION = "external_connection"

#: Target folders inside a room's library, addressed by ``parentFolderId``.
FOLDER_COLLECTION = "library_folder"

#: The keyword the research uses for a teamsite's root library folder.
ROOT = "root"

#: ``source.linkage`` values. A linked item follows its source; a snapshot does
#: not. Both are created by the same operation, per the research.
LINKED = "linked"
SNAPSHOT = "snapshot"

#: ``source.status`` values. Deliberately short: these are the only states the
#: store can actually know. Reporting ``in_sync`` means "matches the version it
#: last applied", not "verified against the source just now" — confirming the
#: latter means asking the source, which is what the re-sync pass is for.
IN_SYNC = "in_sync"
ORPHANED = "orphaned"


class ExternalLibrarySync:
    """Add, inspect and re-sync externally sourced library items.

    There is no ``source_of_record`` attribute and no default audit source
    anywhere in this class. The branch this was ported from carried ``"POST
    /api/library/external"`` as a constructor default and re-hard-coded
    ``"POST /api/library/external/resync"`` inside :meth:`resync`. An audit row
    that names a path the app might have stopped serving is worse than no audit
    row, because it looks authoritative. Every write method now takes the
    ``source`` as a required argument, and the HTTP layer builds it from
    ``router.prefix``, so the two cannot drift.
    """

    def __init__(
        self,
        store: Any,
        *,
        adapters: Mapping[str, SourceAdapter] | None = None,
        limiter: RateLimiter | None = None,
        connection_collection: str = CONNECTION_COLLECTION,
        item_collection: str = ITEM_COLLECTION,
        folder_collection: str = FOLDER_COLLECTION,
        room_collection: str = "room",
    ) -> None:
        self.store = store
        self._adapters = dict(adapters) if adapters is not None else source_registry()
        self.limiter = limiter or RateLimiter()
        self.connection_collection = connection_collection
        self.item_collection = item_collection
        self.folder_collection = folder_collection
        self.room_collection = room_collection

    # -- discovery ---------------------------------------------------------- #

    def sources(self) -> list[dict[str, Any]]:
        """Every registered source and whether a usable connection exists.

        The research is explicit that only GoogleDrive is supported "at this
        time" and that more may be added later. Reporting the registry instead
        of hard-coding a list is what makes that statement true of the code
        rather than only of the documentation.
        """
        connections = self._connections()
        by_source: dict[str, list[dict[str, Any]]] = {}
        for connection in connections:
            by_source.setdefault(str(connection["data"].get("source", "")).strip(), []).append(connection)

        result = []
        for name in sorted(self._adapters):
            usable = [c for c in by_source.get(name, []) if _is_connected(c["data"])]
            result.append(
                {
                    "name": name,
                    "supported": True,
                    "connection_required": True,
                    "connections": len(by_source.get(name, [])),
                    "connected": bool(usable),
                }
            )
        return result

    def connections(self) -> list[dict[str, Any]]:
        """Connection records with the lineage naming the research exposes.

        ``externalSystemConnectionName`` and the mapping back to the connection
        are what the reporting API surfaces for a linked item; the same lineage
        is available here so an operator can answer "which account did this come
        from" without leaving the app.
        """
        result = []
        for connection in self._connections():
            data = connection["data"]
            name = str(data.get("name") or data.get("account") or connection["id"])
            result.append(
                {
                    "record": connection,
                    "external_connection_id": connection["id"],
                    "external_system_connection_name": name,
                    "external_system_connection_mapping": {
                        "collection": self.connection_collection,
                        "id": connection["id"],
                        "source": data.get("source"),
                        "account": data.get("account"),
                    },
                    "connected": _is_connected(data),
                }
            )
        return result

    def folders(self, room_id: str | None = None) -> list[dict[str, Any]]:
        """Folders a caller may target with ``parentFolderId``, root first.

        The research reserves the keyword ``root`` for a teamsite's top-level
        library folder, and a client picking a target needs it offered rather
        than typed. Folders themselves are ordinary records, so this only
        prepends the keyword.
        """
        root = {"id": ROOT, "name": ROOT, "room_id": room_id, "keyword": True}
        records = self.store.list(
            self.folder_collection, room_id=room_id, limit=1000, order_by="created_at"
        )
        return [root] + [
            {
                "id": record["id"],
                "name": record["data"].get("name") or record["id"],
                "room_id": record.get("room_id"),
            }
            for record in records
        ]

    # -- the researched operation ------------------------------------------- #

    def add(
        self,
        *,
        external_source: str,
        external_content_id: str,
        room_id: str,
        parent_folder_id: str = ROOT,
        auto_sync: bool | None = None,
        title: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        actor: str | None = None,
        token: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Add an external file to a room's library.

        Creates a library item **linked to** the external source file, never a
        copy of it, and places it under ``parent_folder_id``.

        ``auto_sync`` defaults to ``False`` when omitted, matching the
        documented backend default: an omitted field means a one-time snapshot.

        ``source`` is the audit ``source`` for the insert, and is required: the
        caller that reached this method knows which route served the request,
        and this method does not.
        """
        # Order matters here, and it follows what the documented limit is
        # actually for. The rate limit belongs to the call to the external
        # source, so it is charged immediately before that call and not one
        # moment earlier: the checks below are local reads that cost the
        # provider nothing, and throttling them would turn a double-click into
        # a scary 429 instead of the harmless re-link it really is.
        room = self.store.get(room_id)
        if room is None or room["collection"] != self.room_collection:
            raise ExternalSyncError("RoomNotFound", f"room {room_id!r} does not exist")

        parent = self._resolve_folder(parent_folder_id, room_id)
        connection = self._resolve_connection(external_source)

        # Idempotent by (source, file, folder). The research does not specify
        # duplicate behaviour; making a repeat add a no-op is the reading that
        # cannot put two entries for one file in front of a buyer, and it makes
        # a double-click or a retried request harmless. The filter paths are
        # the ones the dynamic index actually stores, which is what lets this
        # be a query rather than a scan.
        existing = self.store.find(
            self.item_collection,
            {
                "source.kind": "external",
                "source.external_source": (external_source or "").strip(),
                "source.external_content_id": (external_content_id or "").strip(),
                "source.parent_folder_id": parent["id"],
            },
        )
        if existing:
            return {
                "content_id": existing[0]["id"],
                "created": False,
                "item": existing[0],
                "status": self.status_of(existing[0]),
            }

        self.limiter.check(token or actor)
        adapter = self._adapter(external_source)
        described = adapter.describe(connection["data"], (external_content_id or "").strip())

        sync_enabled = bool(auto_sync)  # omitted means False, per the research
        content_id = new_id(self.item_collection)
        now = utcnow()
        source_block = self._source_block(
            described,
            connection=connection,
            parent=parent,
            auto_sync=sync_enabled,
            status=IN_SYNC,
            now=now,
        )
        data = {
            **described.as_library_metadata(),
            "title": title or described.name,
            "status": "published",
            "source": source_block,
            # Arbitrary extras are stored as given. A team that needs a field
            # nobody has heard of adds it here, with no migration.
            **(dict(metadata) if metadata else {}),
        }
        # contentId is assigned by us rather than read back after the fact, so
        # the item can carry the correlation id the research tells the caller to
        # persist, and can be found by it through the dynamic index.
        data["source"]["content_id"] = content_id

        record = self.store.create(
            self.item_collection,
            data,
            record_id=content_id,
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return {
            "content_id": content_id,
            "created": True,
            "item": record,
            "status": self.status_of(record),
        }

    # -- read models -------------------------------------------------------- #

    def list_items(
        self,
        *,
        room_id: str | None = None,
        where: Mapping[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Every externally sourced library item, optionally scoped or filtered.

        ``where`` is merged over the ``source.kind == "external`` selector, so a
        caller can ask for ``{"source.autoSync": true}`` or anything else a team
        has added, without this method knowing the field exists.
        """
        filters: dict[str, Any] = {"source.kind": "external"}
        filters.update(dict(where or {}))
        records = self.store.find(self.item_collection, filters, limit=limit)
        if room_id is not None:
            records = [r for r in records if r.get("room_id") == room_id]
        return records

    def status_of(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Sync state of one item, derived from what the store holds.

        This is the ``GetContentSyncStatus`` companion the research references
        without publishing, so its shape is a design inference: a status word,
        the version the item applied, and why it reads that way.

        It answers from stored state and makes no provider call, so listing
        many items stays cheap. The trade-off is deliberate: a source that has
        moved since the last pass still reads ``in_sync`` here, because the
        store cannot know otherwise without asking the source. The re-sync
        report is where drift becomes visible, and a caller that wants a live
        answer asks the source.
        """
        source = dict(record.get("data", {}).get("source") or {})
        if not source:
            return {
                "linkage": None,
                "auto_sync": None,
                "state": "not_external",
                "reason": "item has no source block",
            }

        applied = source.get("source_version")
        if str(source.get("status") or IN_SYNC) == ORPHANED:
            state, reason = ORPHANED, "the source file is no longer visible to the connection"
        elif not source.get("auto_sync"):
            state = IN_SYNC
            reason = "one-time snapshot: auto sync was not requested, so the item does not follow its source"
        else:
            state = IN_SYNC
            reason = f"item holds version {applied}, the version it last applied from the source"

        return {
            "linkage": source.get("linkage"),
            "auto_sync": bool(source.get("auto_sync")),
            "state": state,
            "reason": reason,
            "applied_version": applied,
            "last_synced_at": source.get("last_synced_at"),
            "connection": source.get("external_connection_id"),
        }

    def sync_status(self, content_id: str) -> dict[str, Any]:
        """Status of one item, looked up by the ``contentId`` the add returned."""
        record = self.get_item(content_id)
        source = dict(record.get("data", {}).get("source") or {})
        return {
            "content_id": content_id,
            "record_id": record["id"],
            "room_id": record.get("room_id"),
            "external_source": source.get("external_source"),
            "external_content_id": source.get("external_content_id"),
            "parent_folder_id": source.get("parent_folder_id"),
            "linkage": source.get("linkage"),
            **self.status_of(record),
        }

    def get_item(self, content_id: str) -> dict[str, Any]:
        """One linked item, by record id or by the ``contentId`` the add returned.

        Public because the HTTP layer needs it: the branch's read route reached
        into ``_item`` with a ``noqa`` rather than being given a way to ask, and
        a route is not entitled to a private method of the domain it serves.
        """
        return self._item(content_id)

    # -- the automation ----------------------------------------------------- #

    def resync(
        self,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        limit: int = 500,
        source: str,
    ) -> dict[str, Any]:
        """Re-fetch every auto-synced item and apply what changed.

        The research states the behaviour this automates: with ``autoSync``
        true the item "will automatically re-sync whenever the linked source
        file is updated". This is that pass, made callable so a scheduler can
        drive it and an operator can run it now.

        Two rules keep the audit trail honest: an item whose source has not
        changed is not written at all (no audit row for work that did not
        happen), and a snapshot is never re-synced, because asking for a
        one-time snapshot and then following the file are opposite requests.

        ``source`` is the audit ``source`` for every write this pass makes, and
        is required. A scheduler driving this does not have an HTTP route, so
        it names what it is (``"scheduler"``, say) rather than inheriting a URL
        it never went through.
        """
        items = self.list_items(room_id=room_id, limit=limit)
        report: dict[str, Any] = {
            "checked": len(items),
            "updated": [],
            "in_sync": [],
            "skipped": [],
            "failed": [],
        }
        now = utcnow()

        for record in items:
            # Named ``stored`` rather than ``source`` so it cannot shadow the
            # audit ``source`` this pass was handed, which is a real hazard:
            # shadowing it would write the item's own JSON as the audit source.
            stored = dict(record["data"].get("source") or {})
            if not stored.get("auto_sync"):
                report["skipped"].append(
                    {
                        "content_id": record["id"],
                        "reason": "snapshot: auto sync was not requested for this item",
                    }
                )
                continue

            connection = None
            try:
                connection = self.store.get(str(stored.get("external_connection_id") or ""))
                if connection is None or not _is_connected(connection["data"]):
                    raise ExternalSyncError(
                        "ExternalConnectionNotFound",
                        f"connection {stored.get('external_connection_id')!r} is missing or not connected",
                    )
                adapter = self._adapter(str(stored.get("external_source") or ""))
                described = adapter.describe(connection["data"], str(stored.get("external_content_id") or ""))
            except ExternalSyncError as exc:
                self._mark_orphaned(record, stored, exc, now, actor, source_of_pass=source)
                report["failed"].append({"content_id": record["id"], **exc.to_payload()})
                continue

            applied = stored.get("source_version")
            if applied == described.version and stored.get("status") != ORPHANED:
                # Nothing moved. Writing here would be a change with no change
                # behind it, and the audit log is not the place for that.
                report["in_sync"].append({"content_id": record["id"], "version": described.version})
                continue

            # The target folder is not something a re-sync can change, so the
            # existing one is carried through rather than re-resolved. An
            # orphaned marker is cleared, because the source is demonstrably
            # reachable again and a stale "orphaned_at" would outlive the fact.
            refreshed = self._source_block(
                described,
                connection=connection,
                parent=None,
                auto_sync=True,
                status=IN_SYNC,
                now=now,
                parent_folder_id=stored.get("parent_folder_id"),
            )
            refreshed["orphaned_at"] = None
            refreshed["orphaned_reason"] = None
            self.store.update(
                record["id"],
                {
                    "source": {**stored, **refreshed},
                    # The title is a human label rather than something the
                    # source owns, so a re-sync does not rename an item
                    # somebody deliberately retitled.
                    **{k: v for k, v in described.as_library_metadata().items() if k != "title"},
                },
                actor=actor,
                source=source,
            )
            report["updated"].append(
                {
                    "content_id": record["id"],
                    "from_version": applied,
                    "to_version": described.version,
                }
            )

        report["counts"] = {
            key: len(report[key]) for key in ("updated", "in_sync", "skipped", "failed")
        }
        return report

    # -- internals ---------------------------------------------------------- #

    def _adapter(self, name: str) -> SourceAdapter:
        adapter = self._adapters.get((name or "").strip())
        if adapter is None:
            # Same wording as the registry lookup, so the message is identical
            # whether the caller goes through the API or the service directly.
            get_source(name)
            adapter = self._adapters[(name or "").strip()]  # pragma: no cover - defensive
        return adapter

    def _connections(self) -> list[dict[str, Any]]:
        return self.store.list(self.connection_collection, limit=1000, order_by="updated_at", descending=True)

    def _resolve_connection(self, external_source: str) -> dict[str, Any]:
        """Find a usable connection for the source, newest first.

        This is the documented prerequisite: without one, the operation fails
        with a not-found and a remediation pointing at profile settings.
        """
        name = (external_source or "").strip()
        self._adapter(name)  # reject an unsupported source before anything else
        for connection in self._connections():
            data = connection["data"]
            if str(data.get("source", "")).strip() == name and _is_connected(data):
                return connection
        raise ExternalSyncError(
            "ExternalConnectionNotFound",
            f"no connected {name} account is configured for this caller",
            remediation=f"Connect a {name} account in profile settings before retrying.",
            extra={"external_source": name},
        )

    def _resolve_folder(self, parent_folder_id: str | None, room_id: str) -> dict[str, Any]:
        """Resolve ``parentFolderId``: a folder id, or the keyword ``root``."""
        raw = (parent_folder_id or ROOT).strip() or ROOT
        if raw == ROOT:
            return {"id": ROOT, "name": ROOT}
        record = self.store.get(raw)
        if (
            record is None
            or record.get("deleted_at") is not None
            or record["collection"] != self.folder_collection
            or record.get("room_id") != room_id
        ):
            raise ExternalSyncError(
                "FolderNotFound",
                f"library folder {raw!r} is not a live folder in room {room_id}",
                extra={"room_id": room_id, "parent_folder_id": raw},
            )
        return {"id": record["id"], "name": record["data"].get("name") or record["id"]}

    def _item(self, content_id: str) -> dict[str, Any]:
        """Look up an item by record id, tolerating a lookup by ``contentId``."""
        record = self.store.get(content_id)
        if record is not None and record["collection"] == self.item_collection:
            return record
        matches = self.store.find(self.item_collection, {"source.content_id": content_id}, limit=2)
        if matches:
            return matches[0]
        raise ExternalSyncError(
            "ExternalContentNotFound",
            f"no library item with content id {content_id!r}",
            remediation="Use the contentId returned when the file was added.",
        )

    def _mark_orphaned(
        self,
        record: Mapping[str, Any],
        source: Mapping[str, Any],
        exc: ExternalSyncError,
        now: str,
        actor: str | None,
        *,
        source_of_pass: str,
    ) -> None:
        """Record that an item's source is gone, so the UI can surface it.

        This is a real change and is audited as one. If the item is already
        marked orphaned there is nothing to write, and a re-sync that discovers
        nothing must not manufacture audit rows.
        """
        if source.get("status") == ORPHANED:
            return
        self.store.update(
            record["id"],
            {
                "source": {
                    **source,
                    "status": ORPHANED,
                    "orphaned_at": now,
                    "orphaned_reason": f"{exc.code}: {exc.detail}",
                }
            },
            actor=actor,
            source=source_of_pass,
        )

    @staticmethod
    def _source_block(
        described: SourceFile,
        *,
        connection: Mapping[str, Any] | None,
        parent: Mapping[str, Any] | None,
        auto_sync: bool,
        status: str,
        now: str,
        parent_folder_id: str | None = None,
    ) -> dict[str, Any]:
        """The nested ``source`` block stored on the library item.

        The first block of keys is the researched vocabulary, kept verbatim.
        The rest is this project's own, and carries no provider names, so a
        future source is a new value in a field rather than a new schema.
        """
        folder_id = parent_folder_id if parent_folder_id is not None else (parent or {}).get("id", ROOT)
        block: dict[str, Any] = {
            "kind": "external",
            "external_source": described.source,
            "external_content_id": described.file_id,
            "parent_folder_id": folder_id,
            "auto_sync": bool(auto_sync),
            "linkage": LINKED if auto_sync else SNAPSHOT,
            "status": status,
            "source_version": described.version,
            "source_modified_at": described.modified_at,
            "last_synced_at": now,
        }
        if connection is not None:
            data = connection.get("data", connection)
            block["external_connection_id"] = connection["id"] if "id" in connection else None
            block["external_system_connection_name"] = str(
                data.get("name") or data.get("account") or connection.get("id") or ""
            )
        if parent is not None and parent.get("name"):
            block["parent_folder_name"] = parent["name"]
        if described.web_url:
            block["web_url"] = described.web_url
        if described.extra:
            # Provider fields nobody here models are still stored, not dropped.
            block["provider_metadata"] = dict(described.extra)
        return block


def _is_connected(data: Mapping[str, Any]) -> bool:
    """A connection is usable when it is not explicitly disconnected.

    Absent status means connected: a team adding a field must not have to
    remember to also add a status, and a record that exists is a connection
    somebody configured.
    """
    status = data.get("status")
    if status is None:
        return True
    return str(status).strip().lower() in ("connected", "active", "ok", "healthy", "linked")


#: Convenience for the API layer, which builds one per store.
def build_sync(store: Any, **kwargs: Any) -> ExternalLibrarySync:
    """Construct the service. Kept so callers need not name the class."""
    factory: Callable[..., ExternalLibrarySync] = ExternalLibrarySync
    return factory(store, **kwargs)
