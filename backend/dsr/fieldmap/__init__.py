"""WF-035: map sales-room fields onto CRM fields and define the sync key.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-035.md``. This is a
**build**, not a port: the workflow had a finished research document and no code, so
the research document is the specification and the rules it fixes are landed rather
than re-litigated.

:class:`FieldMapping` is the one public surface, and it holds nothing beyond the
store handle, so the feature module builds one per request from a dependency rather
than hanging it on ``app.state`` - which is what keeps ``dsr/api.py`` untouched and
leaves the suite a seam to override.

The researched flow, and where each step landed
----------------------------------------------
1. "Admin opens **Integrations -> <connection> -> Field mapping**" - a connection
   record, optionally room-scoped, and the mapping lives under it.
2. "Admin picks the CRM object the room writes to (e.g. Contact, custom Engagement
   object, lead)" - ``crm_object`` on the mapping, required, and immutable after
   creation because every row's validation is expressed against that object's
   properties.
3. "For each sales-room field, admin picks the CRM property/column, its direction
   (in / out / both), and a transform" - one row per sales-room field in
   :mod:`dsr.fieldmap.mappings`, with the direction checked against the researched
   three-value vocabulary and the transform resolved by name from the registry in
   :mod:`dsr.fieldmap.transforms`.
4. "Admin picks the **sync key** ... and marks it unique so the CRM itself rejects
   collisions" - :mod:`dsr.fieldmap.sync_key`, with HubSpot's ten-unique-property
   ceiling, Dataverse's ten-alternate-key ceiling, the five eligible Dataverse
   attribute types, and a plan for the create request that is marked sourced only
   where the research cites it.
5. "Admin clicks **Validate mapping** ... flags unknown properties, wrong types, and
   unsupported option values before any data is written" -
   :mod:`dsr.fieldmap.validate`, and :meth:`FieldMapping.activate` refuses a mapping
   whose latest validation carries an error.

The six modules
---------------
``errors``
    The eight refusals this workflow makes, under one domain base type.
``vocabulary``
    Directions, provider types, the researched ceilings, the quoted rules, the four
    APIs to read, and the shipped per-vendor default mappings.
``transforms``
    The named, versioned transform registry, and the six transforms.
``metadata``
    The three vendor document shapes, normalised into one :class:`Property`.
``mappings``
    The six collections, and every read and write this workflow performs.
``validate``
    The per-row badges, the sync key's section, and the report.
``preview``
    The per-record evaluation, in both directions, which writes nothing.

Schema flexibility
------------------
No migration, no typed column, no new required field. A row is a JSON payload, the
transform's configuration is an opaque object the transform itself reads, and the
property metadata is stored verbatim in the shape a connector read it in. A team
mapping a field this build has never heard of needs nothing from anyone.

``source`` on every write
-------------------------
Every method that writes takes a **required keyword-only** ``source``, and the
routes pass the route that served the request, built from ``router.prefix``.
Hardcoding a source string in a domain function puts a path in the audit log that
the app might have stopped serving, and that class of bug has shipped in this
codebase before.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.db.audited import utcnow
from dsr.fieldmap import (
    inferences as _inferences,
    preview as _preview,
    sync_key as _sync_key,
    transforms as _transforms,
    validate as _validate,
    vocabulary as _vocabulary,
)
from dsr.fieldmap.errors import (
    FieldMapError,
    InvalidMapping,
    InvalidSyncKey,
    MappingNotValid,
    MetadataUnavailable,
    SyncKeyCapacity,
    UnknownConnection,
    UnknownFieldRow,
    UnknownMapping,
    UnsupportedSyncKeyRequest,
)
from dsr.fieldmap.mappings import (
    CONNECTION_COLLECTION,
    MAPPING_COLLECTION,
    METADATA_COLLECTION,
    ROW_COLLECTION,
    TRANSFORM_COLLECTION,
    VALIDATION_COLLECTION,
    MappingBook,
    connection_view,
    mapping_view,
    metadata_view,
    row_view,
    validation_view,
)
from dsr.fieldmap.metadata import Metadata, normalise
from dsr.store import RecordStore

__all__ = [
    "CONNECTION_COLLECTION",
    "MAPPING_COLLECTION",
    "METADATA_COLLECTION",
    "ROW_COLLECTION",
    "TRANSFORM_COLLECTION",
    "VALIDATION_COLLECTION",
    "FieldMapError",
    "FieldMapping",
    "InvalidMapping",
    "InvalidSyncKey",
    "MAPPING_FLAGS",
    "METADATA_FLAGS",
    "MappingBook",
    "MappingNotValid",
    "Metadata",
    "MetadataUnavailable",
    "SEVERITY",
    "SyncKeyCapacity",
    "UnknownConnection",
    "UnknownFieldRow",
    "UnknownMapping",
    "UnsupportedSyncKeyRequest",
    "as_row",
    "build_report",
    "connection_view",
    "describe",
    "inferences",
    "mapping_view",
    "metadata_view",
    "normalise",
    "preview",
    "row_view",
    "sync_key",
    "transforms",
    "validate_mapping",
    "validation_view",
    "vocabulary",
]

#: Re-exported so a test can pin the researched flag sets from one import.
METADATA_FLAGS = _validate.METADATA_FLAGS
MAPPING_FLAGS = _validate.MAPPING_FLAGS
SEVERITY = _validate.SEVERITY

build_report = _validate.build_report
as_row = _validate.as_row
validate_mapping = _validate.validate_row
preview = _preview.preview
sync_key = _sync_key
transforms = _transforms
vocabulary = _vocabulary
inferences = _inferences


class FieldMapping:
    """The workflow, over a schema-flexible audited store.

    Every read and write goes through :class:`~dsr.fieldmap.mappings.MappingBook`,
    which goes through :class:`~dsr.store.RecordStore`, which goes through
    :class:`~dsr.db.audited.AuditedDatabase`. No SQLite connection is opened
    anywhere in this package, and the audit row is written in the same transaction
    as the change - which is the guarantee the product is built on.
    """

    def __init__(self, store: RecordStore) -> None:
        self.store = store
        self.book = MappingBook(store)

    # -- vocabulary --------------------------------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        """The published vocabulary, so a client renders pickers from one source."""
        payload = _vocabulary.describe()
        payload["transforms"] = _transforms.describe()
        return payload

    def inferences(self) -> dict[str, Any]:
        """Every decision the research does not make, named and bounded."""
        return _inferences.describe()

    def transform_catalogue(self) -> dict[str, Any]:
        """The registry, plus every transform declared as data.

        A declaration is reported with the registry's own answer, so the page can
        show "declared, not executable" next to the built-ins rather than in a
        separate list a reader has to reconcile.
        """
        executable = {item["key"]: item for item in _transforms.describe()}
        declared: list[dict[str, Any]] = []
        for record in self.book.declared_transforms():
            entry = dict(record["data"])
            entry["id"] = record["id"]
            entry["executable"] = entry["key"] in executable
            declared.append(entry)
        return {
            "registered": _transforms.describe(),
            "names": list(_transforms.REGISTRY.names()),
            "versions": {
                name: list(_transforms.REGISTRY.versions_of(name))
                for name in _transforms.REGISTRY.names()
            },
            "declared": declared,
        }

    # -- connections -------------------------------------------------------- #

    def list_connections(
        self, *, room_id: str | None = None, include_global: bool = True
    ) -> dict[str, Any]:
        """Connections, each with how many mappings it carries."""
        records = self.book.connections(room_id=room_id, include_global=include_global)
        counts: dict[str, int] = {}
        for record in self.book.mappings():
            key = str(record["data"].get("connection_id") or "")
            counts[key] = counts.get(key, 0) + 1
        return {
            "count": len(records),
            "connections": [
                connection_view(record, mapping_count=counts.get(str(record["id"]), 0))
                for record in records
            ],
        }

    def read_connection(self, connection_id: str) -> dict[str, Any]:
        """One connection, with its mappings and its recorded metadata.

        Raises :class:`~dsr.fieldmap.errors.UnknownConnection` for an id that is not
        a live connection, so a client has one 404 shape across a read, a patch and
        a property read.
        """
        record = self.book.require_connection(connection_id)
        mappings = self.book.mappings(connection_id)
        return {
            "connection": connection_view(record, mapping_count=len(mappings)),
            "mappings": [mapping_view(item, rows=self.book.rows(item["id"])) for item in mappings],
            "metadata": [
                {
                    "crm_object": item["data"].get("crm_object"),
                    "fetched_at": item["data"].get("fetched_at"),
                    "property_count": item["data"].get("property_count"),
                    "document": item["data"].get("document"),
                }
                for item in self.book.all_metadata(connection_id)
            ],
        }

    def register_connection(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record a connection. ``source`` is required, not defaulted."""
        return self.book.create_connection(payload, room_id=room_id, actor=actor, source=source)

    def amend_connection(
        self,
        connection_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Merge a patch into a connection and return the whole connection view."""
        self.book.patch_connection(connection_id, patch, actor=actor, source=source)
        return self.read_connection(connection_id)

    # -- property metadata -------------------------------------------------- #

    def read_properties(self, connection_id: str, crm_object: str) -> dict[str, Any]:
        """The recorded read for one object, with the endpoints a connector needs.

        Refuses with :class:`~dsr.fieldmap.errors.MetadataUnavailable` when nothing
        has been recorded, and the refusal carries the endpoints from
        :data:`~dsr.fieldmap.vocabulary.METADATA_ENDPOINTS` so the remedy is a call
        rather than a hunt through the documentation.
        """
        connection = self.book.require_connection(connection_id)
        provider = str(connection["data"].get("provider") or "")
        metadata = self.book.metadata(connection_id, crm_object)
        if metadata is None:
            raise self._no_metadata(connection_id, crm_object, provider)
        payload = metadata_view(
            metadata,
            max_age_seconds=int(connection["data"].get("metadata_max_age_seconds") or 0),
            now=utcnow(),
        )
        payload["endpoints"] = [
            dict(entry) for entry in _vocabulary.METADATA_ENDPOINTS.get(provider, ())
        ]
        return payload

    def record_properties(
        self,
        connection_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record a connector's property read for one object.

        The document is normalised first, so an unreadable shape is refused here
        rather than stored and quietly validating against nothing. A malformed
        metadata document is a connector bug, and storing it would make every later
        validation pass against a fixture nobody can see.
        """
        connection = self.book.require_connection(connection_id)
        provider = str(connection["data"].get("provider") or "")
        crm_object = str(payload.get("crm_object") or "").strip()
        if not crm_object:
            raise InvalidMapping(
                "crm_object is required: property metadata is read for one CRM object at a time"
            )
        document = payload.get("document")
        if not isinstance(document, Mapping):
            raise InvalidMapping(
                "document is required: the vendor's property metadata, exactly as the connector "
                "read it. The endpoint to read it from is served by GET "
                "/connections/{id}/properties."
            )
        metadata = normalise(provider, document, crm_object)
        fetched_at = str(payload.get("fetched_at") or "") or utcnow()
        metadata = Metadata(
            provider=metadata.provider,
            crm_object=metadata.crm_object,
            properties=metadata.properties,
            keys=metadata.keys,
            document=metadata.document,
            fetched_at=fetched_at,
            notes=metadata.notes,
        )
        record = self.book.record_metadata(
            connection_id, crm_object, metadata, actor=actor, source=source
        )
        return {
            "id": record["id"],
            "connection_id": connection_id,
            "crm_object": crm_object,
            "fetched_at": fetched_at,
            "document": metadata.document,
            "property_count": len(metadata.properties),
            "keys": [list(key) for key in metadata.keys],
            "unique_key_usage": metadata.unique_key_usage,
            "unique_key_limit": _vocabulary.UNIQUE_KEY_LIMIT,
            "notes": list(metadata.notes),
        }

    def _no_metadata(
        self, connection_id: str, crm_object: str, provider: str
    ) -> MetadataUnavailable:
        endpoints = _vocabulary.METADATA_ENDPOINTS.get(provider, ())
        return MetadataUnavailable(
            f"no property metadata has been recorded for {crm_object!r} on this connection. "
            "Validate needs the CRM's own property and type metadata: read it and post the "
            "document to the properties route.",
            connection_id=str(connection_id),
            crm_object=str(crm_object),
            source_document=str(endpoints[0]["url"]) if endpoints else "",
            endpoints=tuple(dict(entry) for entry in endpoints),
        )

    # -- mappings ----------------------------------------------------------- #

    def list_mappings(
        self,
        connection_id: str | None = None,
        *,
        room_id: str | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        """Mappings with their grids, their pinned key and their last validation."""
        records = self.book.mappings(connection_id, room_id=room_id, state=state)
        views = []
        for record in records:
            data = record["data"]
            views.append(
                mapping_view(
                    record,
                    rows=self.book.rows(record["id"]),
                    metadata=self.book.metadata(
                        str(data.get("connection_id") or ""), str(data.get("crm_object") or "")
                    ),
                    last_validation=self.book.last_validation(record["id"]),
                )
            )
        return {"count": len(views), "mappings": views}

    def read_mapping(self, connection_id: str, mapping_id: str) -> dict[str, Any]:
        """One mapping, with everything the grid renders.

        Refuses when the mapping does not exist *and* when it belongs to a different
        connection: the two are a 404 and a 409 in the product's own reading, but
        both are "you asked for something under this connection that is not there",
        and a client that navigated here will render the same thing for both. The
        message names the connection the mapping actually belongs to.
        """
        record = self.book.require_mapping(mapping_id)
        data = record["data"]
        if str(data.get("connection_id") or "") != str(connection_id):
            raise UnknownMapping(
                f"mapping {mapping_id!r} belongs to connection "
                f"{data.get('connection_id')!r}, not {connection_id!r}"
            )
        connection = self.book.connection(connection_id)
        return mapping_view(
            record,
            rows=self.book.rows(mapping_id),
            connection=connection,
            metadata=self.book.metadata(connection_id, str(data.get("crm_object") or "")),
            last_validation=self.book.last_validation(mapping_id),
        )

    def create_mapping(
        self,
        connection_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record the mapping for one CRM object on one connection."""
        record = self.book.create_mapping(connection_id, payload, actor=actor, source=source)
        view = self.read_mapping(connection_id, record["id"])
        if payload.get("apply_defaults") and not record["data"].get("defaults_applied"):
            view["defaults_skipped"] = self.book.defaults_skipped(record["id"])
        return view

    def amend_mapping(
        self,
        connection_id: str,
        mapping_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Amend a mapping's labels and notes. Object, connection and state are ruled."""
        self.read_mapping(connection_id, mapping_id)
        self.book.patch_mapping(mapping_id, patch, actor=actor, source=source)
        self.book.clear_validation(mapping_id, actor=actor, source=source)
        return self.read_mapping(connection_id, mapping_id)

    def delete_mapping(
        self, connection_id: str, mapping_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Soft-delete a mapping and its rows, so the removal stays auditable."""
        self.read_mapping(connection_id, mapping_id)
        return self.book.delete_mapping(mapping_id, actor=actor, source=source)

    # -- the grid ----------------------------------------------------------- #

    def list_rows(self, connection_id: str, mapping_id: str) -> dict[str, Any]:
        """The grid, in the order the fields were first mapped."""
        self.read_mapping(connection_id, mapping_id)
        rows = self.book.rows(mapping_id)
        return {
            "mapping_id": mapping_id,
            "count": len(rows),
            "rows": [row_view(row) for row in rows],
        }

    def put_row(
        self,
        connection_id: str,
        mapping_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Add a row, or amend the existing row for the same sales-room field.

        Any change to the grid clears the last validation, so an activation can
        never ride a report about a grid that no longer looks like this one.
        """
        self.read_mapping(connection_id, mapping_id)
        record, created = self.book.upsert_row(mapping_id, payload, actor=actor, source=source)
        self.book.clear_validation(mapping_id, actor=actor, source=source)
        return {"row": row_view(record), "created": created}

    def patch_row(
        self,
        connection_id: str,
        mapping_id: str,
        row_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Amend one row."""
        self.read_mapping(connection_id, mapping_id)
        record = self.book.patch_row(row_id, patch, actor=actor, source=source)
        self.book.clear_validation(mapping_id, actor=actor, source=source)
        return {"row": row_view(record)}

    def delete_row(
        self,
        connection_id: str,
        mapping_id: str,
        row_id: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Remove one row from the grid."""
        self.read_mapping(connection_id, mapping_id)
        result = self.book.delete_row(row_id, actor=actor, source=source)
        self.book.clear_validation(mapping_id, actor=actor, source=source)
        return result

    # -- validate ----------------------------------------------------------- #

    def validate(
        self,
        connection_id: str,
        mapping_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """The research's step 5, and the report it produces.

        ``{"record": true}`` stores the run, which is what activation reads. The
        default is to compute and return without storing, so a client can render a
        live badge as an admin edits a row without filling the audit log with a
        report per keystroke.
        """
        mapping = self.read_mapping(connection_id, mapping_id)
        data = dict(mapping)
        data["id"] = mapping_id
        metadata = self._metadata_for(connection_id, str(mapping.get("crm_object") or ""))
        report = _validate.build_report(data, self.book.rows(mapping_id), metadata, now=utcnow())
        record: Mapping[str, Any] | None = None
        if bool((payload or {}).get("record")):
            record = self.book.record_validation(mapping_id, report, actor=actor, source=source)
        return {
            "report": report,
            "recorded": record is not None,
            "validation_id": record["id"] if record else "",
        }

    def last_validation(self, connection_id: str, mapping_id: str) -> dict[str, Any]:
        """The stored report activation would read, or a 404-shaped empty answer.

        Not an error: a mapping that has never been validated has no report, and a
        client asking "is this activatable?" needs that answered rather than
        refused.
        """
        self.read_mapping(connection_id, mapping_id)
        record = self.book.last_validation(mapping_id)
        if record is None:
            mapping = self.read_mapping(connection_id, mapping_id)
            return {
                "validation": None,
                "validated": False,
                "can_activate": False,
                "stale": bool(mapping.get("validation_stale")),
                "reason": "this mapping has never been validated; click Validate mapping first",
            }
        return {
            "validation": validation_view(record),
            "validated": True,
            "can_activate": bool(record["data"].get("can_activate")),
            "stale": False,
            "reason": "",
        }

    def validations(self, connection_id: str, mapping_id: str) -> dict[str, Any]:
        """Every stored run for a mapping, newest first."""
        self.read_mapping(connection_id, mapping_id)
        records = self.book.validations(mapping_id)
        return {
            "count": len(records),
            "validations": [validation_view(record) for record in records],
        }

    def activate(
        self, connection_id: str, mapping_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Make a mapping live, refusing anything that is not clean.

        This is the research's "before any data is written" turned into a gate. Two
        refusals, and they are different situations: a mapping that was never
        validated, and one whose latest run carries an error. The first has no
        report to send back; the second sends the offending report so a client can
        point at the row.
        """
        self.read_mapping(connection_id, mapping_id)
        record = self.book.last_validation(mapping_id)
        if record is None:
            raise MappingNotValid(
                "this mapping has never been validated. Run Validate mapping first: the research "
                "has the mapping's properties and option values checked before any data is written.",
                report=None,
            )
        report = dict(record["data"])
        if not report.get("can_activate"):
            blocking = list(report.get("blocking") or [])
            raise MappingNotValid(
                "this mapping's latest validation carries "
                f"{len(blocking)} error finding(s) ({', '.join(blocking)}), so it cannot go live. "
                "Fix the rows they name and validate again.",
                report=report,
            )
        self.book.set_state(
            mapping_id, "active", extra={"activated_at": utcnow()}, actor=actor, source=source
        )
        return self.read_mapping(connection_id, mapping_id)

    def deactivate(
        self, connection_id: str, mapping_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Return a mapping to draft, keeping its grid and its validation."""
        self.read_mapping(connection_id, mapping_id)
        self.book.set_state(
            mapping_id, "draft", extra={"deactivated_at": utcnow()}, actor=actor, source=source
        )
        return self.read_mapping(connection_id, mapping_id)

    # -- the sync key ------------------------------------------------------- #

    def read_sync_key(self, connection_id: str, mapping_id: str) -> dict[str, Any]:
        """The pinned key, its usage against the researched ceiling, and its plan.

        Every part is optional and reported rather than refused: a mapping with no
        key, no metadata, or an unsourced provider is a state an admin needs to see
        to fix, not a 404.
        """
        mapping = self.read_mapping(connection_id, mapping_id)
        sync_key = dict(mapping.get("sync_key") or {})
        payload: dict[str, Any] = {
            "mapping_id": mapping_id,
            "pinned": bool(sync_key.get("properties")),
            "sync_key": sync_key,
            "limit": _vocabulary.UNIQUE_KEY_LIMIT,
            "limit_quotes": dict(_vocabulary.UNIQUE_KEY_LIMIT_QUOTES),
            "plan": None,
            "plan_error": None,
            "usage": sync_key.get("usage") or {},
        }
        if not payload["pinned"]:
            return payload
        try:
            metadata = self._metadata_for(connection_id, str(mapping.get("crm_object") or ""))
        except MetadataUnavailable as exc:
            payload["plan_error"] = str(exc)
            return payload
        payload["already_enforced"] = _sync_key.already_keyed(metadata, sync_key)
        payload["key_section"] = _validate.validate_sync_key(sync_key, metadata)
        try:
            payload["plan"] = _sync_key.build_create_request(
                metadata,
                sync_key,
                self._connection_data(connection_id),
            )
        except (UnsupportedSyncKeyRequest, InvalidSyncKey) as exc:
            payload["plan_error"] = str(exc)
            payload["plan_gap"] = getattr(exc, "gap", "")
        return payload

    def pin_sync_key(
        self,
        connection_id: str,
        mapping_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Pin the sync key: the property that carries the sales room's own row id.

        Validation happens here rather than at activate time so a refusal names the
        one thing that is wrong. The ten-key ceiling is a refusal rather than a
        finding, because it cannot be fixed by editing a row.
        """
        self.read_mapping(connection_id, mapping_id)
        metadata = self._metadata_for(
            connection_id, payload.get("crm_object") or self._crm_object(mapping_id)
        )
        pinned = _sync_key.pin(payload, metadata, now=utcnow())
        self.book.set_sync_key(mapping_id, pinned, actor=actor, source=source)
        self.book.clear_validation(mapping_id, actor=actor, source=source)
        return self.read_sync_key(connection_id, mapping_id)

    def unpin_sync_key(
        self, connection_id: str, mapping_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Remove the pinned key, so the mapping stops carrying the room's row id."""
        self.read_mapping(connection_id, mapping_id)
        self.book.set_sync_key(mapping_id, {}, actor=actor, source=source)
        self.book.clear_validation(mapping_id, actor=actor, source=source)
        return self.read_sync_key(connection_id, mapping_id)

    def build_sync_key_request(
        self,
        connection_id: str,
        mapping_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The vendor plan for the create request. Pure: it writes nothing.

        A plan rather than a call, because the authenticated request executor is
        WF-034's. Each step carries ``sourced``, and the Salesforce case raises
        instead - the research could not source the external-ID half, and a
        fabricated URL would be worse than a refusal.
        """
        mapping = self.read_mapping(connection_id, mapping_id)
        sync_key = dict(mapping.get("sync_key") or {})
        if not sync_key.get("properties"):
            raise InvalidSyncKey("no sync key is pinned, so there is no property to create")
        options = payload or {}
        metadata = self._metadata_for(connection_id, str(mapping.get("crm_object") or ""))
        return _sync_key.build_create_request(
            metadata,
            sync_key,
            self._connection_data(connection_id),
            group_name=str(options.get("group_name") or ""),
            label=str(options.get("label") or ""),
            value_type=str(options.get("type") or ""),
            field_type=str(options.get("field_type") or ""),
        )

    # -- preview ------------------------------------------------------------ #

    def preview(
        self,
        connection_id: str,
        mapping_id: str,
        record: Mapping[str, Any],
        *,
        directions: Sequence[str] = ("out", "in"),
    ) -> dict[str, Any]:
        """What one sync cycle would send, and what it would read back.

        Reads only. A preview is the thing an admin runs on every edit, and a
        read that audited a row per keystroke would make the audit log useless.
        """
        mapping = self.read_mapping(connection_id, mapping_id)
        metadata = self._metadata_for(connection_id, str(mapping.get("crm_object") or ""))
        data = dict(mapping)
        data["mapping_id"] = mapping_id
        return _preview.preview(
            data, self.book.rows(mapping_id), metadata, record, directions=directions
        )

    # -- summary ------------------------------------------------------------ #

    def summary(self) -> dict[str, Any]:
        """The page's header, and the demo dataset's first question answered.

        Computed over live records rather than stored counters, so a row written a
        second ago is in the number the reader sees.
        """
        connections = self.book.connections(include_global=False)
        mappings = self.book.mappings()
        rows = [row for record in mappings for row in self.book.rows(record["id"])]
        validations = self.store.list(VALIDATION_COLLECTION, limit=1000)
        metadata = self.book.all_metadata()
        by_state: dict[str, int] = {}
        by_provider: dict[str, int] = {}
        pinned = 0
        for record in mappings:
            data = record["data"]
            by_state[str(data.get("state") or "draft")] = (
                by_state.get(str(data.get("state") or "draft"), 0) + 1
            )
            provider = str(data.get("provider") or "")
            by_provider[provider] = by_provider.get(provider, 0) + 1
            if (data.get("sync_key") or {}).get("properties"):
                pinned += 1
        activatable = [
            record["id"]
            for record in mappings
            if record["data"].get("can_activate") and str(record["data"].get("state")) != "active"
        ]
        return {
            "connections": len(connections),
            "mappings": len(mappings),
            "rows": len(rows),
            "by_state": by_state,
            "by_provider": by_provider,
            "active": by_state.get("active", 0),
            "draft": by_state.get("draft", 0),
            "sync_keys_pinned": pinned,
            "validations": len(validations),
            "metadata_reads": len(metadata),
            "objects_without_metadata": self._objects_without_metadata(),
            "activatable": activatable,
            "transforms": len(list(_transforms.REGISTRY.names())),
            "declared_transforms": len(self.book.declared_transforms()),
            "unique_key_limit": _vocabulary.UNIQUE_KEY_LIMIT,
            "inferences": len(_inferences.INFERENCES),
        }

    def _objects_without_metadata(self) -> list[dict[str, Any]]:
        """Every mapping whose object has no recorded read - the first thing to fix."""
        gaps: list[dict[str, Any]] = []
        for record in self.book.mappings():
            data = record["data"]
            connection_id = str(data.get("connection_id") or "")
            crm_object = str(data.get("crm_object") or "")
            if self.book.metadata(connection_id, crm_object) is None:
                gaps.append(
                    {
                        "mapping_id": record["id"],
                        "connection_id": connection_id,
                        "crm_object": crm_object,
                        "provider": str(data.get("provider") or ""),
                    }
                )
        return gaps

    # -- transforms --------------------------------------------------------- #

    def declare_transform(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record a transform as data. Execution still comes from the registry."""
        record = self.book.declare_transform(payload, actor=actor, source=source)
        return {"transform": dict(record["data"]), "id": record["id"]}

    # -- internals ---------------------------------------------------------- #

    def _crm_object(self, mapping_id: str) -> str:
        return str(self.book.require_mapping(mapping_id)["data"].get("crm_object") or "")

    def _connection_data(self, connection_id: str) -> dict[str, Any]:
        """A connection's own payload, not its envelope.

        The request builder reads ``property_group`` straight off what it is given,
        so handing it the record would find no group on a connection that has one -
        which is exactly the bug this accessor exists to stop repeating.
        """
        return dict(self.book.require_connection(connection_id)["data"])

    def _metadata_for(self, connection_id: str, crm_object: str) -> Metadata:
        """The recorded metadata, or the 409 that names what to read.

        The single place this workflow decides a metadata read is required, so
        validate, preview, the sync key and the create plan cannot disagree about
        when it is.
        """
        connection = self.book.require_connection(connection_id)
        metadata = self.book.metadata(connection_id, crm_object)
        if metadata is None:
            raise self._no_metadata(
                connection_id, crm_object, str(connection["data"].get("provider") or "")
            )
        return metadata
