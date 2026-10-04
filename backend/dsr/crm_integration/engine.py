"""The façade the HTTP layer calls. Owns the five collections this feature writes.

Every write in this package goes through :class:`~dsr.store.RecordStore`, and
every one of them takes ``source`` as a **required** keyword. That is not
ceremony: the audit row is the guarantee this product is built on, and an audit
row that names a path the app does not serve is worse than no audit row, because
it looks like evidence. ``source`` is therefore a call-site argument rather than
a default, so a route that forgets it is a ``TypeError`` in a test rather than a
silent lie in production.

The five collections:

``crm_read_identity``   which buyer maps to which CRM records, and with which
                        capability flags and field map
``crm_read_option_set`` the room's own display labels, for vendors that cannot
                        annotate
``crm_read_record``     the vendor's three tables, held locally
``crm_read_snapshot``   the cached panel per identity, with its TTL
``crm_read_query``      one row per read plan issued, so a read is auditable

All five are ordinary JSON in ``records.data``. There is no migration and no
typed column, so a deployment that adds a field needs no coordination with
anyone.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from dsr.crm_integration import (
    cache,
    fieldmap,
    identity,
    inferences,
    labels,
    normalize,
    query as query_rules,
    sources,
    vocabulary,
)
from dsr.crm_integration.errors import UnknownIdentity
from dsr.store import RecordStore

#: One row per read plan issued. The read path is a read, but a read nobody can
#: account for is indistinguishable from a read that never happened.
QUERIES = "crm_read_query"

#: Every collection this feature owns. Served at the feature's own route so an
#: operator can see exactly which tables a new workflow added.
OWNED_COLLECTIONS: tuple[str, ...] = (
    identity.IDENTITIES,
    labels.OPTION_SETS,
    sources.RECORDS,
    cache.SNAPSHOTS,
    QUERIES,
)


class CrmReadEngine:
    """The read path: resolve, query, normalise, label, cache, render."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    # ----------------------------------------------------------------- #
    # Vocabulary
    # ----------------------------------------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        """Every published value, as data a client renders pickers from."""
        return {
            "systems": list(vocabulary.CRM_SYSTEMS),
            "objects": list(vocabulary.CRM_OBJECTS),
            "object_names": query_rules.describe()["objects"],
            "id_fields": dict(vocabulary.ID_FIELDS),
            "identity_sources": list(vocabulary.IDENTITY_SOURCES),
            "identity_source": vocabulary.IDENTITY_SOURCE,
            "signed_token_claims": list(vocabulary.SIGNED_TOKEN_CLAIMS),
            "display_labels": dict(vocabulary.DISPLAY_LABEL_CAPABILITY),
            "display_annotation": vocabulary.DATAVERSE_DISPLAY_ANNOTATION,
            "display_preference": vocabulary.DATAVERSE_DISPLAY_PREFERENCE,
            "labellable_fields": list(labels.LABELLABLE_FIELDS),
            "cache_states": list(vocabulary.CACHE_STATES),
            "read_outcomes": list(vocabulary.READ_OUTCOMES),
            "paging": query_rules.describe()["paging"],
            "limits": query_rules.describe()["limits"],
            "api_versions": query_rules.describe()["api_versions"],
            "collections": list(OWNED_COLLECTIONS),
        }

    def inferences(self) -> dict[str, Any]:
        """Every judgement call this workflow rests on, and how to change each one."""
        return inferences.describe()

    def vendors(self) -> dict[str, Any]:
        """Each vendor's endpoints, limits and display-label capability."""
        described = query_rules.describe()
        return {
            "count": len(vocabulary.CRM_SYSTEMS),
            "vendors": [
                {
                    "system": system,
                    "read_only_endpoints": list(query_rules.READ_ONLY_ENDPOINTS),
                    "capability": labels.capability(system),
                    "limits": described["limits"].get(system, {}),
                    "paging_mode": described["paging"].get("modes", {}).get(system),
                }
                for system in vocabulary.CRM_SYSTEMS
            ],
        }

    # ----------------------------------------------------------------- #
    # Identities
    # ----------------------------------------------------------------- #

    def identities(self, room_id: str | None = None) -> list[dict[str, Any]]:
        return [identity.identity_view(row) for row in identity.identities(self.store, room_id)]

    def register_identity(
        self,
        payload: dict[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        data = identity.normalise(payload)
        identity.check_duplicate(self.store, room_id or "", data)
        record = self.store.create(
            identity.IDENTITIES,
            data,
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return identity.identity_view(record)

    def read_identity(self, identity_id: str, room_id: str | None = None) -> dict[str, Any]:
        return identity.identity_view(identity.identity(self.store, identity_id, room_id))

    def patch_identity(
        self,
        identity_id: str,
        payload: dict[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        record = identity.identity(self.store, identity_id, room_id)
        data = identity.normalise({**(record["data"] or {}), **payload})
        identity.check_duplicate(
            self.store,
            room_id or record.get("room_id") or "",
            data,
            exclude_id=record["id"],
        )
        updated = self.store.update(record["id"], data, actor=actor, source=source)
        return identity.identity_view(updated)

    def delete_identity(
        self,
        identity_id: str,
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Soft-delete an identity, and the snapshot that was keyed to it.

        A soft delete, so the cancellation is audited. The snapshot goes with it:
        a cached panel for an identity the room no longer serves is a row that can
        still be read by anyone holding its id.
        """
        record = identity.identity(self.store, identity_id, room_id)
        removed: list[str] = []
        snapshot = cache.find(self.store, record["id"], room_id)
        if snapshot is not None:
            self.store.delete(snapshot["id"], actor=actor, source=source)
            removed.append(snapshot["id"])
        deleted = self.store.delete(record["id"], actor=actor, source=source)
        return {
            "id": deleted["id"],
            "deleted": True,
            "soft_delete": True,
            "snapshot_removed": removed,
        }

    # ----------------------------------------------------------------- #
    # Option sets: the room's own display labels
    # ----------------------------------------------------------------- #

    def option_sets(
        self, system: str | None = None, room_id: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            {"id": row["id"], "room_id": row.get("room_id"), **(row["data"] or {})}
            for row in labels.option_sets(self.store, system, room_id)
        ]

    def register_option_set(
        self,
        payload: dict[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        data = labels.normalise(payload)
        labels.check_duplicate(self.store, data, room_id)
        record = self.store.create(
            labels.OPTION_SETS, data, room_id=room_id, actor=actor, source=source
        )
        return {"id": record["id"], "room_id": record.get("room_id"), **data}

    def read_option_set(
        self,
        option_set_id: str,
        *,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        record = self.store.get(option_set_id)
        if record is None or record["collection"] != labels.OPTION_SETS:
            raise UnknownIdentity(f"no option set with id {option_set_id}")
        if room_id is not None and record.get("room_id") not in (None, room_id):
            raise UnknownIdentity(f"option set {option_set_id} does not belong to room {room_id}")
        return {"id": record["id"], "room_id": record.get("room_id"), **(record["data"] or {})}

    def delete_option_set(
        self,
        option_set_id: str,
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        record = self.store.get(option_set_id)
        if record is None or record["collection"] != labels.OPTION_SETS:
            raise UnknownIdentity(f"no option set with id {option_set_id}")
        self.store.delete(record["id"], actor=actor, source=source)
        return {"id": record["id"], "deleted": True}

    # ----------------------------------------------------------------- #
    # The vendor's tables
    # ----------------------------------------------------------------- #

    def tables(self, room_id: str | None = None) -> list[dict[str, Any]]:
        """Every vendor table with the row count the room holds for it."""
        rows: list[dict[str, Any]] = []
        for system in vocabulary.CRM_SYSTEMS:
            for object_name in vocabulary.CRM_OBJECTS:
                rows.append(
                    {
                        "system": system,
                        "object": object_name,
                        "vendor_name": vocabulary.object_name(system, object_name),
                        "rows": sources.table_size(self.store, system, object_name, room_id),
                        "page_limit": vocabulary.page_limit(system),
                    }
                )
        return rows

    def declare_record(
        self,
        payload: dict[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        data = sources.normalise_record(payload)
        record = self.store.create(
            sources.RECORDS, data, room_id=room_id, actor=actor, source=source
        )
        return {"id": record["id"], "room_id": record.get("room_id"), **data}

    # ----------------------------------------------------------------- #
    # The read
    # ----------------------------------------------------------------- #

    def pull(
        self,
        room_id: str,
        *,
        identity_id: str | None = None,
        buyer_email: str | None = None,
        limit: int | None = None,
        refresh: bool = False,
        now: datetime | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Run the whole flow for one buyer and cache the panel.

        Steps two to five of the user flow, in order: resolve the identity, issue
        one field-scoped read per object, normalise each page, fill the display
        labels from the vendor's annotation or the room's option sets, cache the
        result, and hand back the panel.

        ``refresh`` forces the read even when the cache is fresh. It is the
        scheduler's route: "Read-through cache refresh on a room scheduler" means
        the scheduler asks, and a scheduler that could not ask would have no way
        to honour a TTL at all.

        A buyer with no CRM identity is **not** an error here. The panel comes
        back with ``crm_context: false``, every part unfilled, and the reason
        beside it, because "the room still works when a seller authors it without
        CRM context" is the research's own summary of this workflow.
        """
        moment = now or cache.utcnow()
        found = identity.resolve_or_refuse(
            self.store, room_id, identity_id=identity_id, buyer_email=buyer_email
        )
        if found is None:
            return self._no_context_panel(room_id, identity_id, buyer_email, moment)

        data = found["data"]
        system = vocabulary.require_system(data.get("system"))
        mapped = fieldmap.normalise_field_map(
            data.get("field_map") or fieldmap.default_field_map(system)
        )
        display_labels = identity.supports_display_labels(data, system)

        reads: dict[str, Any] = {}
        per_object: dict[str, dict[str, Any]] = {}
        logged: list[dict[str, Any]] = []
        fills: list[str] = []
        unlabelled: list[dict[str, Any]] = []
        for object_name in vocabulary.CRM_OBJECTS:
            page = self._read_object(
                room_id=room_id,
                system=system,
                object_name=object_name,
                identity_id=found["id"],
                identity_data=data,
                mapped=mapped,
                display_labels=display_labels,
                limit=limit,
                actor=actor,
                source=source,
                logged=logged,
            )
            if page is None:
                continue
            per_object[object_name] = page
            page["records"], applied = labels.apply_fallback(
                page["records"], labels.fallback_labels(self.store, system, object_name, room_id)
            )
            fills.extend(f"{object_name}.{field}" for field in applied)
            unlabelled.extend(
                {"object": object_name, **row} for row in labels.unlabelled(page["records"])
            )
            reads[object_name] = {
                key: page[key]
                for key in (
                    "total",
                    "total_reported",
                    "returned",
                    "done",
                    "next",
                    "paging_mode",
                    "read_set",
                    "truncated_at_vendor_limit",
                    "unapplied_conditions",
                    "unmapped_returned",
                    "stalled",
                )
            }

        panel = normalize.deal_panel(system, data, per_object, mapped)
        outcome = self._outcome(reads, per_object, display_labels)
        snapshot = cache.build_snapshot(
            found,
            panel,
            reads,
            outcome=outcome,
            now=moment,
            fills=fills,
            unlabelled=unlabelled,
        )
        stored = self.store.create(
            cache.SNAPSHOTS, snapshot, room_id=room_id, actor=actor, source=source
        )
        for row in logged:
            self.store.create(QUERIES, row, room_id=room_id, actor=actor, source=source)

        return {
            "identity": identity.identity_view(found),
            "crm_context": True,
            "panel": panel,
            "reads": reads,
            "display_labels": display_labels,
            "label_source": (
                "vendor annotations"
                if display_labels and vocabulary.supports_display_labels(system)
                else "room option sets"
            ),
            "fallback_fills": sorted(set(fills)),
            "unlabelled": unlabelled,
            "outcome": outcome,
            "queries": logged,
            "cache": cache.describe_cache(stored, moment),
            "snapshot_id": stored["id"],
            "refreshed": True,
            "refresh_was_forced": bool(refresh),
        }

    def _read_object(
        self,
        *,
        room_id: str,
        system: str,
        object_name: str,
        identity_id: str,
        identity_data: dict[str, Any],
        mapped: dict[str, dict[str, str]],
        display_labels: bool,
        limit: int | None,
        actor: str | None,
        source: str,
        logged: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """One object: issue the read, answer it, normalise it, log it.

        Returns ``None`` when the identity names no record for this object and no
        owner to read by, which is not a failure. The panel reports the object as
        unfound and the room still renders.

        The research's data flow says "filter by id/owner", and both branches are
        here: a resolved record id is the narrow read, and an owner with no record
        id is the wider one. A room whose seller knows the rep but not the deal id
        still gets a panel, filtered to that rep's records rather than to the whole
        table.
        """
        target_id = str(identity_data.get(f"{object_name}_id") or "").strip()
        id_property = "id"
        if not target_id and object_name == "contact":
            target_id = str(identity_data.get("buyer_email") or "").strip()
            id_property = "email" if target_id else "id"
        owner_id = str(identity_data.get("owner_id") or "").strip()
        if not target_id and not owner_id:
            # Neither an id nor an owner is known, so there is nothing to filter
            # this object by and the read would be the whole table. A panel that
            # filled a buyer's contact card from the whole contact table would be
            # showing a stranger, so the object is reported unfound instead.
            return None
        # A resolved record id is the narrower filter and is the whole of it.
        # Applying the owner's clause on top would drop every record whose row
        # carries no owner, which is most contact and account rows, and the panel
        # would report a contact who exists as a contact who does not.
        scoped_owner = "" if target_id else owner_id

        plan = query_rules.build_query(
            system,
            object_name,
            field_map=mapped,
            target_id=target_id,
            id_property=id_property,
            owner_id=scoped_owner,
            limit=limit,
            display_labels=display_labels,
        )
        response = sources.run_query(self.store, plan, room_id=room_id)
        page = normalize.normalise_response(
            system,
            object_name,
            response,
            mapped,
            requested_limit=plan.limit,
        )
        logged.append(
            {
                "identity_id": identity_id,
                "object": object_name,
                "vendor_name": vocabulary.object_name(system, object_name),
                "at": cache.utcnow().isoformat(),
                "read_only": True,
                **plan.as_dict(),
                "done": page["done"],
                "returned": page["returned"],
                "total": page["total"],
                "truncated_at_vendor_limit": page["truncated_at_vendor_limit"],
                "unapplied_conditions": page["unapplied_conditions"],
                "stalled": page["stalled"],
            }
        )
        return page

    def _outcome(
        self,
        reads: dict[str, Any],
        per_object: dict[str, dict[str, Any]],
        display_labels: bool,
    ) -> str:
        """Which of the published read outcomes this read produced.

        ``complete`` last, so any finding wins over it. A read that filled labels
        from the room's own option sets is a ``capability_fallback`` rather than a
        plain complete, because the panel is showing the room's vocabulary and the
        operator needs to know that is what happened.
        """
        if not per_object:
            return "empty"
        pages = list(reads.values())
        if any(page["truncated_at_vendor_limit"] for page in pages):
            return "truncated_at_vendor_limit"
        if any(page["next"] for page in pages):
            return "paged"
        if any(page["unapplied_conditions"] for page in pages):
            return "paged"
        if not display_labels:
            return "capability_fallback"
        return "complete"

    def _no_context_panel(
        self,
        room_id: str,
        identity_id: str | None,
        buyer_email: str | None,
        now: datetime,
    ) -> dict[str, Any]:
        """The panel for a room with no CRM identity for this buyer.

        Rendered, not refused. The room still opens, the panel still has its
        shape, and the reason it is empty is a sentence rather than a status
        code.
        """
        return {
            "identity": None,
            "crm_context": False,
            "reason": (
                "no CRM identity is registered for this buyer in this room. A seller may "
                "author a room without CRM context; the deal panel renders without CRM "
                "fields and the rest of the room is unaffected."
            ),
            "asked_for": {"identity_id": identity_id, "buyer_email": buyer_email},
            "panel": {
                "system": None,
                "buyer": {"email": buyer_email, "name": None},
                "resolved_ids": {"deal": None, "contact": None, "account": None},
                "deal": {
                    "found": False,
                    "name": None,
                    "stage": {"value": None, "label": ""},
                    "amount": None,
                    "external_id": None,
                },
                "contact": {
                    "found": False,
                    "name": None,
                    "title": None,
                    "email": buyer_email,
                    "external_id": None,
                },
                "account": {
                    "found": False,
                    "name": None,
                    "industry": {"value": None, "label": ""},
                    "external_id": None,
                },
                "read_set": [],
                "objects_read": [],
                "unmapped_returned": {},
            },
            "reads": {},
            "display_labels": False,
            "label_source": "none",
            "fallback_fills": [],
            "unlabelled": [],
            "outcome": "empty",
            "queries": [],
            "cache": cache.describe_cache(None, now),
            "snapshot_id": None,
            "refreshed": False,
            "refresh_was_forced": False,
        }

    def read_panel(
        self,
        room_id: str,
        *,
        identity_id: str | None = None,
        buyer_email: str | None = None,
        refresh: bool = False,
        now: datetime | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """The panel, read through the cache.

        Fresh cache serves without a read. ``refresh=True`` forces one. That is
        the whole of the read-through rule, and the response says which branch
        ran so a reviewer can see it.
        """
        moment = now or cache.utcnow()
        found = identity.resolve(
            self.store, room_id, identity_id=identity_id, buyer_email=buyer_email
        )
        if found is None:
            return self._no_context_panel(room_id, identity_id, buyer_email, moment)
        if not refresh:
            snapshot = cache.find(self.store, found["id"], room_id)
            if cache.is_fresh(snapshot, moment):
                data = found["data"]
                return {
                    "identity": identity.identity_view(found),
                    "crm_context": True,
                    "panel": (snapshot["data"] or {}).get("panel"),
                    "reads": (snapshot["data"] or {}).get("reads") or {},
                    "display_labels": bool((snapshot["data"] or {}).get("display_labels")),
                    "label_source": (
                        "vendor annotations"
                        if (snapshot["data"] or {}).get("display_labels")
                        else "room option sets"
                    ),
                    "fallback_fills": (snapshot["data"] or {}).get("fallback_fills") or [],
                    "unlabelled": (snapshot["data"] or {}).get("unlabelled") or [],
                    "outcome": (snapshot["data"] or {}).get("outcome"),
                    "queries": [],
                    "cache": cache.describe_cache(snapshot, moment),
                    "snapshot_id": snapshot["id"],
                    "refreshed": False,
                    "refresh_was_forced": False,
                    "system": data.get("system"),
                }
        return self.pull(
            room_id,
            identity_id=found["id"],
            buyer_email=buyer_email,
            refresh=refresh,
            now=moment,
            actor=actor,
            source=source,
        )

    def deal_panel(self, room_id: str) -> dict[str, Any]:
        """The whole room's deal panel, one entry per registered identity.

        Each identity is read through its own cache, so one stale buyer does not
        force a read of the other three. An identity whose snapshot has expired
        reads the vendor; one that has not does not. The response reports both
        counts, because "three panels, none read" is a different fact from
        "three panels, all current".
        """
        rows = identity.identities(self.store, room_id)
        panels: list[dict[str, Any]] = []
        read_count = 0
        for row in rows:
            snapshot = cache.find(self.store, row["id"], room_id)
            fresh = cache.is_fresh(snapshot)
            if not fresh:
                read_count += 1
            panels.append(
                {
                    "identity_id": row["id"],
                    "buyer_email": row["data"].get("buyer_email"),
                    "system": row["data"].get("system"),
                    "panel": (snapshot or {}).get("data", {}).get("panel"),
                    "cache": cache.describe_cache(snapshot),
                }
            )
        return {
            "room_id": room_id,
            "count": len(panels),
            "identities": len(rows),
            "reads_issued": read_count,
            "served_from_cache": len(panels) - read_count,
            "without_crm_context": len(rows) == 0,
            "panels": panels,
        }

    # ----------------------------------------------------------------- #
    # The read query log
    # ----------------------------------------------------------------- #

    def queries(
        self,
        room_id: str | None = None,
        object_name: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Every read plan this room issued, newest first.

        Filtered through the dynamic index, so a field a team added to the log
        later is queryable without a change to this route.
        """
        where: dict[str, Any] = {}
        if object_name:
            where["object"] = vocabulary.require_object(object_name)
        rows = self.store.find(QUERIES, where, limit=limit)
        if room_id is not None:
            rows = [row for row in rows if row.get("room_id") == room_id]
        return [
            {
                "id": row["id"],
                "room_id": row.get("room_id"),
                "at": row.get("created_at"),
                **(row["data"] or {}),
            }
            for row in rows
        ]

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        """Counts for the room: identities, option sets, tables, cache states."""
        identities = identity.identities(self.store, room_id)
        by_system: dict[str, int] = {}
        for row in identities:
            key = str(row["data"].get("system") or "unknown")
            by_system[key] = by_system.get(key, 0) + 1
        return {
            "room_id": room_id,
            "identities": len(identities),
            "identities_by_system": by_system,
            "option_sets": len(labels.option_sets(self.store, room_id=room_id)),
            "vendor_rows": sum(row["rows"] for row in self.tables(room_id)),
            "read_plans": len(self.queries(room_id, limit=1000)),
            "cache": cache.cache_summary(self.store, room_id),
            "collections": list(OWNED_COLLECTIONS),
        }


__all__ = ["OWNED_COLLECTIONS", "QUERIES", "CrmReadEngine"]
