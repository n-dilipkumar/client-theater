"""The records this workflow writes, and the only way it writes them.

Five collections, all schema-flexible. Nothing here adds a column, a migration or
a required field: a team that maps a field this build has never heard of stores
``source_field``, ``target_property``, ``transform`` and a ``transform_config`` of
its own shape, and reads back exactly what it wrote.

===========================  ==================================================
``crm_connection``           One CRM connection, room-scoped or global. The
                             research's "Integrations -> <connection> -> Field
                             mapping" is keyed on this id.
``crm_object_mapping``       The mapping for one CRM object on one connection:
                             the chosen object, the pinned sync key, and whether
                             it is draft, validated or active.
``crm_field_mapping_row``    One row of the grid: source column, target property,
                             direction, transform.
``crm_property_metadata``    One connector read of one object's property metadata.
``crm_mapping_validation``   One run of "Validate mapping", with its per-row
                             badges.
``crm_transform``            A transform declared as data. Execution still comes
                             from :mod:`dsr.fieldmap.transforms`.
===========================  ==================================================

``source`` on every write
-------------------------

Every method that writes takes a **required keyword-only** ``source``, and the
routes pass the route that served the request, built from ``router.prefix``. The
branch history of this codebase is full of features whose audit log named a route
the app had stopped serving; a defaulted ``source`` is how that happens again.

Deleting a mapping deletes its rows
-----------------------------------

:meth:`MappingBook.delete_mapping` soft-deletes the mapping **and** every row that
belongs to it. A row whose mapping is gone is unreachable by any route and yet
still matches ``find("crm_field_mapping_row", {"mapping_id": ...})``, so a tenant
that restored the mapping would find a grid it had never validated. Both deletions
are audited, and the route names the mapping rather than the rows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from dsr.db.audited import utcnow
from dsr.fieldmap.errors import (
    InvalidMapping,
    UnknownConnection,
    UnknownFieldRow,
    UnknownMapping,
)
from dsr.fieldmap.metadata import Metadata
from dsr.fieldmap.transforms import REGISTRY
from dsr.fieldmap.vocabulary import (
    default_mapping,
    default_mapping_object,
    direction_of,
    normalise_provider,
)
from dsr.store import RecordStore

CONNECTION_COLLECTION = "crm_connection"
MAPPING_COLLECTION = "crm_object_mapping"
ROW_COLLECTION = "crm_field_mapping_row"
METADATA_COLLECTION = "crm_property_metadata"
VALIDATION_COLLECTION = "crm_mapping_validation"
TRANSFORM_COLLECTION = "crm_transform"

#: The mapping's lifecycle. A mapping is never active without a clean validation:
#: see :meth:`~dsr.fieldmap.mappings.MappingBook.activate`.
MAPPING_STATES = ("draft", "active")

#: The envelope. A payload key in this set is never stored inside ``data``, so a
#: caller cannot smuggle an ``id`` in through a body and shadow the record's own.
_ENVELOPE_KEYS = frozenset(
    {"id", "collection", "room_id", "revision", "created_at", "updated_at", "deleted_at"}
)

#: Row defaults applied on write. A direction is required; everything else has a
#: default so a half-specified row is still recordable and the grid can show what
#: is missing rather than the API refusing the whole grid.
ROW_DEFAULTS: dict[str, Any] = {
    "direction": "out",
    "transform": "identity",
    "transform_version": None,
    "transform_config": {},
    "source_type": "text",
    "required": False,
}


class MappingBook:
    """Every read and write this workflow performs, over the audited store.

    Holds nothing beyond the store handle, so the feature builds one per request
    from a dependency rather than hanging it on ``app.state`` - which is what keeps
    ``dsr/api.py`` untouched and leaves the suite an override seam.
    """

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    # -- connections -------------------------------------------------------- #

    def connection(self, connection_id: str) -> dict[str, Any] | None:
        record = self.store.get(str(connection_id or ""))
        if record is None or record.get("collection") != CONNECTION_COLLECTION:
            return None
        return record

    def require_connection(self, connection_id: str) -> dict[str, Any]:
        record = self.connection(connection_id)
        if record is None:
            raise UnknownConnection(f"connection {connection_id!r} not found")
        return record

    def connections(
        self, *, room_id: str | None = None, include_global: bool = True
    ) -> list[dict[str, Any]]:
        """Connections, newest first, optionally filtered to a room.

        ``include_global`` is what makes a room-scoped page useful: a connection
        with no ``room_id`` is the research's "per connection, not per deployment"
        shape - it applies to every room - so a room view shows its own
        connections and the shared ones.
        """
        records = self.store.list(CONNECTION_COLLECTION, limit=1000)
        if room_id is None:
            return records
        scoped = [
            record
            for record in records
            if str(record.get("room_id") or "") == str(room_id)
            or (include_global and not record.get("room_id"))
        ]
        return scoped

    def create_connection(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record a connection. The provider is required; nothing else is.

        The connection itself is not this workflow's work - the OAuth exchange and
        the token vault are WF-034's - so a connection here is a *reference* for
        mapping purposes. A deployment that already stores connections from WF-034
        can pass that id straight to a mapping and never call this route.
        """
        provider = normalise_provider(payload.get("provider"))
        if not provider:
            raise InvalidMapping(
                "provider is required and must be one of hubspot, dataverse, salesforce"
            )
        name = str(payload.get("name") or "").strip()
        if not name:
            raise InvalidMapping(
                "name is required: a connection a reader cannot name is not reviewable"
            )
        # Everything the caller sent is stored, and the fields this workflow reads
        # are then normalised over it. The allowlist was tried first and dropped a
        # deployment's own keys on the floor, which is schema flexibility in name
        # only: a team that keeps its portal id beside the vendor org would have
        # had to ask for a migration to add it.
        data: dict[str, Any] = {
            key: value for key, value in payload.items() if key not in _ENVELOPE_KEYS
        }
        data.update(
            {
                "name": name,
                "provider": provider,
                "account": str(payload.get("account") or ""),
                "vendor_org": str(payload.get("vendor_org") or ""),
                "connected": bool(payload.get("connected", True)),
                # The HubSpot property group a created sync-key property is filed
                # under. Required in the create body, so it is configured here rather
                # than invented per request.
                "property_group": str(payload.get("property_group") or ""),
            }
        )
        return self.store.create(
            CONNECTION_COLLECTION, data, room_id=room_id, actor=actor, source=source
        )

    def patch_connection(
        self,
        connection_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Merge a patch into a connection. A provider change is refused."""
        self.require_connection(connection_id)
        merged: dict[str, Any] = {}
        for key, value in patch.items():
            if key == "provider":
                if normalise_provider(value) != normalise_provider(
                    self.connection(connection_id)["data"].get("provider")
                ):
                    raise InvalidMapping(
                        "a connection's provider cannot be changed: it decides which CRM the "
                        "metadata, the validation and the sync key are read against. Record a new "
                        "connection instead."
                    )
                continue
            if key in (
                "id",
                "collection",
                "room_id",
                "revision",
                "created_at",
                "updated_at",
                "deleted_at",
            ):
                continue
            merged[key] = value
        return self.store.update(str(connection_id), merged, actor=actor, source=source)

    # -- property metadata -------------------------------------------------- #

    def metadata(self, connection_id: str, crm_object: str) -> Metadata | None:
        """The recorded read for this connection and object, if there is one.

        Located with ``find`` on the two dotted paths that matter, so a connection
        carrying metadata for several objects resolves without a scan on read.
        """
        records = self.store.find(
            METADATA_COLLECTION,
            {"connection_id": str(connection_id), "crm_object": str(crm_object)},
            limit=1000,
        )
        if not records:
            return None
        # Newest first, and the newest read is the one a validation should use.
        return Metadata.from_dict(records[0]["data"])

    def metadata_record(self, connection_id: str, crm_object: str) -> dict[str, Any] | None:
        records = self.store.find(
            METADATA_COLLECTION,
            {"connection_id": str(connection_id), "crm_object": str(crm_object)},
            limit=1000,
        )
        return records[0] if records else None

    def all_metadata(self, connection_id: str | None = None) -> list[dict[str, Any]]:
        where = {"connection_id": str(connection_id)} if connection_id else {}
        return self.store.find(METADATA_COLLECTION, where, limit=1000)

    def record_metadata(
        self,
        connection_id: str,
        crm_object: str,
        metadata: Metadata,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Store a connector's property read.

        The newest read wins on lookup and every read is kept, so "what did the
        validator see when this mapping was activated" is answerable later. That is
        the value of keeping the history rather than updating in place: a mapping
        validated against last month's schema is a different claim from one
        validated today, and only the timestamps tell them apart.
        """
        payload = metadata.to_dict()
        payload["connection_id"] = str(connection_id)
        payload["crm_object"] = str(crm_object)
        payload["fetched_at"] = metadata.fetched_at or utcnow()
        return self.store.create(METADATA_COLLECTION, payload, actor=actor, source=source)

    # -- mappings ----------------------------------------------------------- #

    def mapping(self, mapping_id: str) -> dict[str, Any] | None:
        record = self.store.get(str(mapping_id or ""))
        if record is None or record.get("collection") != MAPPING_COLLECTION:
            return None
        return record

    def require_mapping(self, mapping_id: str) -> dict[str, Any]:
        record = self.mapping(mapping_id)
        if record is None:
            raise UnknownMapping(f"mapping {mapping_id!r} not found")
        return record

    def mappings(
        self,
        connection_id: str | None = None,
        *,
        room_id: str | None = None,
        state: str | None = None,
    ) -> list[dict[str, Any]]:
        """Mappings, newest first, filtered by connection, room and state."""
        records = self.store.list(MAPPING_COLLECTION, limit=1000)
        if connection_id is not None:
            records = [
                record
                for record in records
                if str(record["data"].get("connection_id") or "") == str(connection_id)
            ]
        if room_id is not None:
            records = [
                record for record in records if str(record.get("room_id") or "") == str(room_id)
            ]
        if state:
            records = [
                record for record in records if str(record["data"].get("state") or "") == str(state)
            ]
        return records

    def create_mapping(
        self,
        connection_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record the mapping for one CRM object, and optionally the vendor default.

        ``apply_defaults`` copies the shipped per-vendor rows, and only when the
        object is the one those rows were written for. That guard is what makes
        "let tenants override individual fields" safe: applying a Contacts default
        to a custom engagement object would land every row on a property that object
        does not have, and validation would report five unknown properties for a
        connection the admin configured correctly.
        """
        connection = self.require_connection(connection_id)
        provider = str(connection["data"].get("provider") or "")
        crm_object = str(payload.get("crm_object") or "").strip()
        if not crm_object:
            raise InvalidMapping("crm_object is required: the mapping is for one CRM object")
        data: dict[str, Any] = {
            "connection_id": str(connection_id),
            "provider": provider,
            "crm_object": crm_object,
            "crm_object_label": str(payload.get("crm_object_label") or crm_object),
            "state": "draft",
            "sync_key": {},
            "defaults_applied": False,
        }
        if payload.get("notes"):
            data["notes"] = str(payload["notes"])
        record = self.store.create(
            MAPPING_COLLECTION,
            data,
            room_id=connection.get("room_id"),
            actor=actor,
            source=source,
        )
        if payload.get("apply_defaults"):
            self.apply_defaults(
                record["id"],
                actor=actor,
                source=source,
            )
            record = self.mapping(record["id"]) or record
        return record

    def apply_defaults(
        self,
        mapping_id: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> list[dict[str, Any]]:
        """Copy the provider's shipped default rows onto a mapping.

        A no-op, returning nothing, when the mapping's object is not the one the
        defaults were written for. :meth:`defaults_skipped` says why.
        """
        record = self.require_mapping(mapping_id)
        provider = str(record["data"].get("provider") or "")
        if str(record["data"].get("crm_object") or "") != default_mapping_object(provider):
            return []
        created: list[dict[str, Any]] = []
        for row in default_mapping(provider):
            created.append(
                self.upsert_row(
                    mapping_id,
                    row,
                    actor=actor,
                    source=source,
                )
            )
        self.store.update(
            mapping_id,
            {"defaults_applied": True, "defaults_object": default_mapping_object(provider)},
            actor=actor,
            source=source,
        )
        return created

    def defaults_skipped(self, mapping_id: str) -> str:
        """Why the shipped defaults did not apply, or the empty string when they did."""
        record = self.require_mapping(mapping_id)
        data = record["data"]
        if data.get("defaults_applied"):
            return ""
        provider = str(data.get("provider") or "")
        return (
            f"the {provider} defaults are written for the "
            f"{default_mapping_object(provider)!r} object and this mapping is for "
            f"{str(data.get('crm_object') or '')!r}"
        )

    def patch_mapping(
        self,
        mapping_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Merge a patch into a mapping. The connection, object and state are not
        patchable here - the object is what the grid validates against and the
        state moves through activation, so both have a rule rather than a shortcut."""
        record = self.require_mapping(mapping_id)
        data = record["data"]
        if "crm_object" in patch and str(patch["crm_object"]) != str(data.get("crm_object") or ""):
            raise InvalidMapping(
                "a mapping's CRM object cannot be changed: every row's validation is expressed "
                "against that object's properties. Record a new mapping instead."
            )
        if "state" in patch:
            raise InvalidMapping(
                "a mapping's state moves through activation, not through a patch: an active "
                "mapping must have a clean validation behind it."
            )
        if "connection_id" in patch and str(patch["connection_id"]) != str(
            data.get("connection_id") or ""
        ):
            raise InvalidMapping("a mapping cannot be moved to another connection")
        merged = {
            key: value
            for key, value in patch.items()
            if key
            not in (
                "id",
                "collection",
                "room_id",
                "revision",
                "created_at",
                "updated_at",
                "deleted_at",
            )
        }
        return self.store.update(mapping_id, merged, actor=actor, source=source)

    def set_sync_key(
        self,
        mapping_id: str,
        sync_key: Mapping[str, Any] | None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Replace or clear the pinned sync key, and drop the validation stamp.

        Pinning is step 4 of the flow and it is evaluated per record like
        everything else, so a change to the key changes what a sync cycle sends and
        the last validation no longer describes this mapping.
        """
        self.require_mapping(mapping_id)
        return self.store.update(
            mapping_id, {"sync_key": dict(sync_key or {})}, actor=actor, source=source
        )

    def set_state(
        self,
        mapping_id: str,
        state: str,
        *,
        extra: Mapping[str, Any] | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Move a mapping between draft and active.

        The only path to ``state``. :meth:`patch_mapping` refuses a patch that
        carries one, so every state change passes through here and is stamped with
        the time it happened.
        """
        if state not in MAPPING_STATES:
            raise InvalidMapping(f"state must be one of {', '.join(MAPPING_STATES)}")
        self.require_mapping(mapping_id)
        return self.store.update(
            mapping_id,
            {"state": state, **(dict(extra) if extra else {})},
            actor=actor,
            source=source,
        )

    def delete_mapping(
        self,
        mapping_id: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Soft-delete a mapping and every row on it.

        A soft delete, so the mapping and its rows stay in the audit trail and a
        restore brings back a grid that still has the rows it was validated with.
        """
        self.require_mapping(mapping_id)
        removed = []
        for row in self.rows(mapping_id):
            self.store.delete(row["id"], actor=actor, source=source)
            removed.append(row["id"])
        return {
            **self.store.delete(mapping_id, actor=actor, source=source),
            "rows_removed": len(removed),
        }

    # -- grid rows ---------------------------------------------------------- #

    def rows(self, mapping_id: str) -> list[dict[str, Any]]:
        """The grid, in the order the fields were first mapped.

        ``created_at`` ascending, and this ordering is load-bearing rather than
        cosmetic. ``find`` answers newest-first, which would hand the *last* row to
        the validator first - and the duplicate-target check works by recording
        which row claimed a property first. Reversed, it flags the row an admin
        added first and exonerates the one they added to fix it.

        The second key must be the *insertion* sequence, not the record id. Record
        ids are ``uuid4().hex``, so a tie on ``created_at`` - which two rows
        written in the same millisecond always tie on, and a loop adding a row per
        mapped field does exactly that - fell to a random comparison. The order was
        then stable within one machine and arbitrary across two, on identical data,
        and the duplicate-target check flagged whichever row the random order put
        first. That surfaced on CI rather than locally, as a mapping report listing
        its rows as ``b, a, c`` instead of ``a, b, c``: over a fast local run the
        clock always advances between two writes and the tie never forms.

        So the grid is read through :meth:`RecordStore.list`, whose own tie-break
        is the insertion ``rowid`` - monotonic, independent of the query plan, and
        identical on every machine. See the note on :meth:`AuditedDatabase.list`,
        which changed its tie-break to ``rowid`` for exactly this reason. The
        mapping's own rows are selected out of that collection-wide read by id, so
        the mapping scope is unchanged.
        """
        by_id = {
            str(record["id"])
            for record in self.store.find(
                ROW_COLLECTION, {"mapping_id": str(mapping_id)}, limit=1000
            )
        }
        ordered = self.store.list(
            ROW_COLLECTION,
            limit=1000,
            order_by="created_at",
            descending=False,
        )
        return [record for record in ordered if str(record["id"]) in by_id]

    def row(self, row_id: str) -> dict[str, Any] | None:
        record = self.store.get(str(row_id or ""))
        if record is None or record.get("collection") != ROW_COLLECTION:
            return None
        return record

    def require_row(self, row_id: str) -> dict[str, Any]:
        record = self.row(row_id)
        if record is None:
            raise UnknownFieldRow(f"mapping row {row_id!r} not found")
        return record

    def row_for_field(self, mapping_id: str, source_field: str) -> dict[str, Any] | None:
        """The row for a sales-room field, if the grid maps it.

        The uniqueness a mapping grid needs is on ``(mapping, source field)``, not
        on the row id: two rows for ``email`` is a mapping that would send the same
        column twice, and the grid has to refuse the second rather than hold both.
        """
        for record in self.rows(mapping_id):
            if str(record["data"].get("source_field") or "") == str(source_field or ""):
                return record
        return None

    def upsert_row(
        self,
        mapping_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> tuple[dict[str, Any], bool]:
        """Add a row, or amend the existing row for the same sales-room field.

        Returns ``(record, created)``. Upsert rather than insert because the grid is
        edited in place: a client re-posting a row it fetched is amending, not
        asking for a second row that would double every field in the payload.
        """
        self.require_mapping(mapping_id)
        source_field = str(payload.get("source_field") or "").strip()
        if not source_field:
            raise InvalidMapping("source_field is required: a row maps one sales-room field")
        data = self._row_data(payload)
        existing = self.row_for_field(mapping_id, source_field)
        if existing is None:
            record = self.store.create(
                ROW_COLLECTION,
                {**data, "mapping_id": str(mapping_id)},
                actor=actor,
                source=source,
            )
            return record, True
        return self.store.update(existing["id"], data, actor=actor, source=source), False

    def patch_row(
        self,
        row_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Amend one row. The mapping a row belongs to is not patchable.

        The patch is merged over the *stored* row and the merged result is what
        gets validated, because :meth:`AuditedDatabase.update` merges at the top
        level: validating the patch on its own would let a caller who sent only
        ``{"direction": "in"}`` be checked against empty defaults and have a row
        written with a blank target property.
        """
        record = self.require_row(row_id)
        if "mapping_id" in patch:
            raise InvalidMapping("a mapping row cannot be moved to another mapping")
        merged = {**dict(record["data"]), **{k: v for k, v in patch.items() if k != "mapping_id"}}
        return self.store.update(row_id, self._row_data(merged), actor=actor, source=source)

    def delete_row(self, row_id: str, *, actor: str | None = None, source: str) -> dict[str, Any]:
        """Soft-delete one row, so the mapping it was removed from stays auditable."""
        self.require_row(row_id)
        return self.store.delete(row_id, actor=actor, source=source)

    def _row_data(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and normalise a row's own fields, leaving the envelope alone.

        Called with the whole row - a create, or a patch already merged over the
        stored one - so there is exactly one validator for a row rather than two
        that could drift apart.
        """
        data: dict[str, Any] = dict(ROW_DEFAULTS)
        if "source_field" in payload:
            text = str(payload["source_field"] or "").strip()
            if not text:
                raise InvalidMapping("source_field is required: a row maps one sales-room field")
            data["source_field"] = text
        if "direction" in payload:
            direction = direction_of(payload["direction"])
            if not direction:
                raise InvalidMapping(
                    f"direction {payload['direction']!r} is not one of "
                    "'in', 'out' or 'both'; the researched vocabulary is exactly those three"
                )
            data["direction"] = direction
        if "transform" in payload:
            name = str(payload["transform"] or "").strip()
            if not name:
                raise InvalidMapping(
                    "transform is required: a row names the function applied to its value"
                )
            data["transform"] = name
        if "transform_version" in payload and payload["transform_version"] is not None:
            try:
                data["transform_version"] = int(payload["transform_version"])
            except (TypeError, ValueError) as exc:
                raise InvalidMapping(
                    f"transform_version {payload['transform_version']!r} is not a whole number"
                ) from exc
        if "transform_config" in payload:
            config = payload["transform_config"]
            if config in (None, ""):
                config = {}
            if not isinstance(config, Mapping):
                raise InvalidMapping("transform_config must be a JSON object")
            data["transform_config"] = dict(config)
        if "source_type" in payload:
            data["source_type"] = str(payload["source_type"] or "text").strip().lower() or "text"
        if "target_property" in payload:
            data["target_property"] = str(payload["target_property"] or "").strip()
        if "label" in payload:
            data["label"] = str(payload["label"] or "").strip()
        if "required" in payload:
            data["required"] = bool(payload["required"])
        if "notes" in payload:
            data["notes"] = str(payload["notes"] or "").strip()
        return data

    # -- validation reports ------------------------------------------------- #

    def record_validation(
        self,
        mapping_id: str,
        report: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Store one run of "Validate mapping".

        Every run is kept, and the mapping is stamped with the id of the latest, so
        "the validation this mapping was activated on" is a stored fact rather than
        a recollection. The report is not rewritten in place for the same reason the
        metadata is not: a mapping that fails today and passed last week is a fact
        a reviewer needs.
        """
        payload = dict(report)
        payload["mapping_id"] = str(mapping_id)
        record = self.store.create(VALIDATION_COLLECTION, payload, actor=actor, source=source)
        self.require_mapping(mapping_id)
        self.store.update(
            mapping_id,
            {
                "last_validation_id": record["id"],
                "last_validated_at": payload.get("validated_at"),
                "last_validation_errors": int(payload.get("counts", {}).get("error", 0) or 0),
                "can_activate": bool(payload.get("can_activate")),
            },
            actor=actor,
            source=source,
        )
        return record

    def validations(self, mapping_id: str) -> list[dict[str, Any]]:
        """Every validation run for a mapping, newest first."""
        return self.store.find(VALIDATION_COLLECTION, {"mapping_id": str(mapping_id)}, limit=1000)

    def last_validation(self, mapping_id: str) -> dict[str, Any] | None:
        mapping = self.require_mapping(mapping_id)
        report_id = str(mapping["data"].get("last_validation_id") or "")
        if not report_id:
            return None
        return self.store.get(report_id)

    def clear_validation(self, mapping_id: str, *, actor: str | None = None, source: str) -> None:
        """Forget the last validation after a change that invalidates it.

        Called by every write that changes the grid, the sync key or the object. A
        mapping whose rows changed after it was validated has not been validated in
        its current shape, and leaving the old report stamped on it would let an
        activation ride a validation of something else.
        """
        mapping = self.mapping(mapping_id)
        if mapping is None:
            return
        self.store.update(
            mapping_id,
            {
                "last_validation_id": "",
                "last_validated_at": None,
                "last_validation_errors": 0,
                "can_activate": False,
                "validation_stale": True,
            },
            actor=actor,
            source=source,
        )

    # -- declared transforms ------------------------------------------------ #

    def declare_transform(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record a transform as data.

        The research's extension point is a *code* registration in the connector,
        so a declaration is not executable by itself: :data:`declared_only` is
        computed rather than stored, and the grid reports a row that names this
        transform as ``transform_unavailable`` until the code is registered in
        :mod:`dsr.fieldmap.transforms`. Recording the declaration anyway is
        deliberate - it is where an admin writes down the transform a deployment is
        adding, and the grid then says precisely what is missing.
        """
        name = str(payload.get("name") or "").strip()
        if not name:
            raise InvalidMapping("name is required: a transform is identified by its name")
        version = payload.get("version", 1)
        try:
            version = int(version)
        except (TypeError, ValueError) as exc:
            raise InvalidMapping(
                f"version {payload.get('version')!r} is not a whole number"
            ) from exc
        if version < 1:
            raise InvalidMapping("version must be 1 or more")
        resolved = REGISTRY.resolve(name, version)
        applies_to = payload.get("applies_to")
        data: dict[str, Any] = {
            "name": name,
            "version": version,
            "key": f"{name}@{version}",
            "description": str(payload.get("description") or ""),
            "applies_to": [str(entry) for entry in applies_to]
            if isinstance(applies_to, Sequence) and not isinstance(applies_to, str)
            else [],
            "connection_id": str(payload.get("connection_id") or ""),
            "declared_only": resolved is None or not resolved.executable,
        }
        return self.store.create(TRANSFORM_COLLECTION, data, actor=actor, source=source)

    def declared_transforms(self, connection_id: str | None = None) -> list[dict[str, Any]]:
        records = self.store.list(TRANSFORM_COLLECTION, limit=1000)
        if connection_id is None:
            return records
        return [
            record
            for record in records
            if str(record["data"].get("connection_id") or "") in ("", str(connection_id))
        ]


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #


def connection_view(record: Mapping[str, Any], *, mapping_count: int = 0) -> dict[str, Any]:
    """A connection, flattened for a list, with what it is mapped to."""
    data = dict(record.get("data") or {})
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "name": data.get("name") or "",
        "provider": data.get("provider") or "",
        "account": data.get("account") or "",
        "vendor_org": data.get("vendor_org") or "",
        "connected": bool(data.get("connected")),
        "property_group": data.get("property_group") or "",
        "notes": data.get("notes") or "",
        "mapping_count": mapping_count,
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def mapping_view(
    record: Mapping[str, Any],
    *,
    rows: Sequence[Mapping[str, Any]] = (),
    connection: Mapping[str, Any] | None = None,
    metadata: Metadata | None = None,
    last_validation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A mapping, with its grid, its pinned key and its last validation.

    ``rows`` are the stored records; the caller passes the ones it already loaded
    so a list view does not re-query per mapping.
    """
    data = dict(record.get("data") or {})
    validation = dict(last_validation.get("data") or {}) if last_validation else None
    connection_data = dict((connection or {}).get("data") or {}) if connection else {}
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "revision": record.get("revision"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "connection_id": data.get("connection_id") or "",
        "connection_name": connection_data.get("name") or None,
        "provider": data.get("provider") or "",
        "crm_object": data.get("crm_object") or "",
        "crm_object_label": data.get("crm_object_label") or "",
        "state": data.get("state") or "draft",
        "sync_key": data.get("sync_key") or {},
        "defaults_applied": bool(data.get("defaults_applied")),
        "last_validation_id": data.get("last_validation_id") or "",
        "last_validated_at": data.get("last_validated_at"),
        "can_activate": bool(data.get("can_activate")),
        "validation_stale": bool(data.get("validation_stale")),
        "row_count": len(rows),
        "rows": [row_view(row) for row in rows],
        "validation": validation,
        "metadata": {
            "recorded": metadata is not None,
            "document": metadata.document if metadata else "",
            "fetched_at": metadata.fetched_at if metadata else "",
            "property_count": len(metadata.properties) if metadata else 0,
        },
    }


def row_view(record: Mapping[str, Any]) -> dict[str, Any]:
    """One grid row, flattened."""
    data = dict(record.get("data") or {})
    return {
        "id": record.get("id"),
        "mapping_id": data.get("mapping_id") or "",
        "source_field": data.get("source_field") or "",
        "source_type": data.get("source_type") or "text",
        "target_property": data.get("target_property") or "",
        "label": data.get("label") or "",
        "direction": data.get("direction") or "out",
        "transform": data.get("transform") or "identity",
        "transform_version": data.get("transform_version"),
        "transform_config": data.get("transform_config") or {},
        "required": bool(data.get("required")),
        "notes": data.get("notes") or "",
    }


def validation_view(record: Mapping[str, Any]) -> dict[str, Any]:
    """One stored validation run."""
    data = dict(record.get("data") or {})
    return {
        "id": record.get("id"),
        "mapping_id": data.get("mapping_id") or "",
        "created_at": record.get("created_at"),
        **{key: value for key, value in data.items() if key != "mapping_id"},
    }


def metadata_view(metadata: Metadata, *, max_age_seconds: int = 0, now: str = "") -> dict[str, Any]:
    """Recorded metadata, with the age a reader needs to judge it.

    ``stale`` is measured against ``max_age_seconds`` rather than decided here: the
    research defers auto-invalidation on a schema change to W17, so this reports
    age and lets the client decide, and says so in the payload.
    """
    payload = metadata.to_dict()
    age: int | None = None
    if metadata.fetched_at and now:
        try:
            read = datetime.fromisoformat(metadata.fetched_at)
            moment = datetime.fromisoformat(now)
            age = max(0, int((moment - read).total_seconds()))
        except ValueError:
            age = None
    payload["age_seconds"] = age
    payload["stale"] = bool(age is not None and max_age_seconds > 0 and age > max_age_seconds)
    payload["staleness_note"] = (
        "Auto-invalidation on a CRM schema change is W17's work, not this workflow's: this "
        "reports the age of the read and leaves the decision to the reader."
    )
    return payload
