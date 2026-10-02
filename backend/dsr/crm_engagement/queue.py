"""The records this workflow writes, and the states they can be in.

The researched flow's step 2 is "the sales room records a row in its own ``engagement``
table and enqueues a CRM write", and its step 5 is "on success the worker writes the
returned CRM record id into its local row (``crm_record_id``) and marks the event as
synced". So there are two local rows and both are written here:

* the **engagement row** - the room's own durable record of what a buyer did. It exists
  the moment the buyer acts, before anything is sent, so a CRM that never answers still
  leaves the room knowing what happened.
* the **queue row** - the pending CRM write for that event, and where the worker's
  progress lives.

A rule that cannot fall through
-------------------------------
Every queue row ends in exactly one of :data:`~dsr.crm_engagement.vocabulary.QUEUE_STATES`
and none of them is a silent drop. A row this build cannot send is ``blocked`` with a
named :data:`~dsr.crm_engagement.vocabulary.BLOCK_REASONS` reason, and a blocked row is
**re-evaluated** on the next drain rather than remembered as blocked - so adding the
mapping row the research promises ("adding 'download', 'pricing-view', 'cta-click' needs
a mapping row, not a code path") and running the queue again sends exactly what was
waiting. A queue that drops what it cannot handle loses events nobody knows about, which
is the failure mode the research's own Sync log exists to prevent.

``source`` on every write
-------------------------
Every method that writes takes a **required keyword-only** ``source``, and the routes
pass the route that served the request, built from ``router.prefix``. Hardcoding a source
string in a domain function puts a path in the audit log that the app might have stopped
serving, and that class of bug has shipped in this codebase before.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.crm_engagement.errors import (
    InvalidConnector,
    InvalidEventType,
    InvalidFieldMap,
    UnknownRoom,
)
from dsr.crm_engagement.mapping import as_text, normalise_field_map
from dsr.crm_engagement.vocabulary import (
    BLOCK_REASONS,
    CANONICAL_FIELDS,
    QUEUE_STATES,
    VENDORS,
)
from dsr.db.audited import utcnow
from dsr.store import RecordStore


class InvalidFieldMapConnector(InvalidFieldMap):
    """A field map that does not name the CRM it writes to.

    With more than one connector registered, a map that named none would be ambiguous
    about which object it was writing to - and the create target comes from the
    connection, so an ambiguous map is a map that cannot be sent.
    """


class InvalidFieldMapMissing(InvalidFieldMap):
    """A field map, connector or queue row id that resolves to nothing.

    Reuses ``InvalidFieldMap`` so the HTTP surface keeps one 422 for the whole family: to
    a client, "you asked me to patch a row that is not there" and "you sent me a field map
    I cannot use" are the same class of answer.
    """


#: Every collection this workflow owns. Prefixed ``crm_`` so two features cannot claim
#: one path, and so ``/api/collections`` reads as one feature's rows.
CONNECTOR_COLLECTION = "crm_connector"
EVENT_TYPE_COLLECTION = "crm_event_catalogue"
FIELD_MAP_COLLECTION = "crm_field_map"
ENGAGEMENT_COLLECTION = "crm_engagement"
QUEUE_COLLECTION = "crm_engagement_queue"
SYNC_LOG_COLLECTION = "crm_sync_log"

#: The room collection is the store's own, not this feature's.
ROOM_COLLECTION = "room"

#: The two fields the researched step 5 writes onto the engagement row. Named exactly as
#: the research names them so a row is readable against the source.
CRM_RECORD_ID_FIELD = "crm_record_id"
SYNC_STATE_FIELD = "sync_state"

#: The connector fields this build reads, and what a caller may set.
CONNECTOR_FIELDS: tuple[str, ...] = (
    "vendor",
    "label",
    "base_url",
    "object",
    "entity_set",
    "sync_key_property",
    "token",
    "preferences",
    "enabled",
    "notes",
)


def normalise_connector(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a connector and return it in canonical shape.

    The target is whatever the vendor's create endpoint needs: HubSpot wants an object
    type, Dataverse wants an entity *set* name - which the research is specific about,
    since it is the thing 'Copy set name' copies - and Salesforce wants an sObject name.
    So the one required target field is the vendor's own, named in
    :data:`~dsr.crm_engagement.vocabulary.CREATE_ENDPOINTS`, and a payload that sets the
    wrong one for its vendor is refused rather than stored and ignored.
    """
    from dsr.crm_engagement.vocabulary import CREATE_ENDPOINTS

    vendor = as_text(payload.get("vendor")).lower()
    if not vendor:
        raise InvalidConnector("vendor is required")
    if vendor not in VENDORS:
        raise InvalidConnector(
            f"vendor {vendor!r} is not one this build writes to; known vendors are {list(VENDORS)}"
        )
    spec = CREATE_ENDPOINTS[vendor]
    target_field = str(spec["object_field"])
    target = as_text(payload.get(target_field))
    if not target:
        raise InvalidConnector(
            f"a {vendor} connector needs {target_field!r} - the {spec['object_label']} to create "
            f"the row on, for example {spec['object_example']!r}"
        )
    base_url = as_text(payload.get("base_url"))
    if not base_url:
        raise InvalidConnector("base_url is required: it is the vendor's API root")
    if not base_url.startswith(("http://", "https://")):
        raise InvalidConnector("base_url must be an absolute http(s) URL")

    preferences = payload.get("preferences")
    if isinstance(preferences, str):
        preferences = [preferences]
    return {
        "vendor": vendor,
        "label": as_text(payload.get("label")) or spec["label"],
        "base_url": base_url.rstrip("/"),
        "object": as_text(payload.get("object")) if target_field == "object" else "",
        "entity_set": as_text(payload.get("entity_set")) if target_field == "entity_set" else "",
        "sync_key_property": as_text(payload.get("sync_key_property")),
        "token": as_text(payload.get("token")),
        "preferences": [str(token).strip() for token in (preferences or []) if str(token).strip()],
        "enabled": bool(payload.get("enabled", True)),
        "notes": as_text(payload.get("notes")),
    }


def normalise_event_type(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an event-catalogue row.

    The research's extensibility sentence is the whole reason this collection exists:
    "New event types are rows in the room's event catalogue mapped by the field map, so
    adding 'download', 'pricing-view', 'cta-click' needs a mapping row, not a code path."
    So a row is just a name and its metadata, and the engine never checks it against a
    list of known types.
    """
    name = as_text(payload.get("event_type") or payload.get("name"))
    if not name:
        raise InvalidEventType(
            "event_type is required: this is the row that lets a new type ship without a code path"
        )
    return {
        "event_type": name,
        "label": as_text(payload.get("label")) or name.replace("_", " ").title(),
        "description": as_text(payload.get("description")),
        "canonical_fields": list(payload.get("canonical_fields") or sorted(CANONICAL_FIELDS)),
        "tracks_dwell": bool(payload.get("tracks_dwell", False)),
        "enabled": bool(payload.get("enabled", True)),
    }


def _with_room(record: Mapping[str, Any]) -> dict[str, Any]:
    """One record's payload plus its id, its room, and its timestamps.

    ``room_id`` is a column on the record, not a field in its payload, so folding the
    envelope in here is what stops every room-scoped row from reading as unscoped.
    """
    return {
        **(record.get("data") or {}),
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


class SyncBook:
    """Every read and write this workflow makes, over the audited store.

    Nothing here opens a connection. The store is the only write path, so the audit row
    lands in the same transaction as the change - which is the guarantee the product is
    built on.
    """

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    # -- rooms -------------------------------------------------------------- #

    def require_room(self, room_id: str, *, source: str | None = None) -> dict[str, Any]:
        """Resolve a room, or raise :class:`UnknownRoom`."""
        del source
        record = self.store.get(str(room_id))
        if record is None or record.get("collection") != ROOM_COLLECTION:
            raise UnknownRoom(str(room_id))
        return record

    # -- connectors --------------------------------------------------------- #

    def connectors(self, *, enabled: bool | None = None) -> list[dict[str, Any]]:
        """Every connector, with the envelope's ``room_id`` folded into the projection.

        ``room_id`` is a column on the record, not a field in its payload, so it has to be
        carried across explicitly. Left out, every connector reads as unscoped and the
        worker's room-scoped-first resolution quietly becomes "whichever sorted first" -
        which is the one behaviour that block resolution exists to prevent.
        """
        rows = self.store.list(CONNECTOR_COLLECTION, limit=1000)
        result = [_with_room(row) for row in rows]
        if enabled is not None:
            result = [row for row in result if bool(row.get("enabled")) is enabled]
        return sorted(
            result, key=lambda row: (str(row.get("vendor")), str(row.get("label")), str(row["id"]))
        )

    def connector(self, connector_id: str) -> dict[str, Any] | None:
        record = self.store.get(str(connector_id))
        if record is None or record.get("collection") != CONNECTOR_COLLECTION:
            return None
        return _with_room(record)

    def save_connector(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        spec = normalise_connector(payload)
        record = self.store.create(
            CONNECTOR_COLLECTION, spec, room_id=room_id, actor=actor, source=source
        )
        return _with_room(record)

    def update_connector(
        self, connector_id: str, patch: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        existing = self.store.get(str(connector_id))
        if existing is None or existing.get("collection") != CONNECTOR_COLLECTION:
            raise InvalidConnector(f"connector {connector_id} not found")
        merged = {**(existing.get("data") or {}), **dict(patch)}
        spec = normalise_connector(merged)
        record = self.store.update(str(connector_id), spec, actor=actor, source=source)
        return _with_room(record)

    def delete_connector(
        self, connector_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        existing = self.store.get(str(connector_id))
        if existing is None or existing.get("collection") != CONNECTOR_COLLECTION:
            raise InvalidConnector(f"connector {connector_id} not found")
        return self.store.delete(str(connector_id), actor=actor, source=source)

    def connector_view(self, connector: Mapping[str, Any]) -> dict[str, Any]:
        """A connector as a read returns it: no token, only whether there is one.

        The token *is* the ``Authorization`` header, and the Sync log's recorded request
        headers are readable, so it is never in a read.
        """
        from dsr.crm_engagement.payloads import explain_preferences, preference_list

        view = {key: value for key, value in connector.items() if key != "token"}
        view["has_token"] = bool(as_text(connector.get("token")))
        tokens = preference_list(connector)
        view["preferences"] = tokens
        view["preference_detail"] = explain_preferences(tokens)
        return view

    # -- event catalogue ---------------------------------------------------- #

    def event_types(self, *, enabled: bool | None = None) -> list[dict[str, Any]]:
        rows = self.store.list(EVENT_TYPE_COLLECTION, limit=1000)
        result = [_with_room(row) for row in rows]
        if enabled is not None:
            result = [row for row in result if bool(row.get("enabled")) is enabled]
        return sorted(result, key=lambda row: (str(row.get("event_type")), str(row["id"])))

    def event_type(self, event_type_id: str) -> dict[str, Any] | None:
        record = self.store.get(str(event_type_id))
        if record is None or record.get("collection") != EVENT_TYPE_COLLECTION:
            return None
        return _with_room(record)

    def event_type_by_name(self, name: str) -> dict[str, Any] | None:
        for row in self.event_types():
            if str(row.get("event_type")) == str(name):
                return row
        return None

    def save_event_type(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        spec = normalise_event_type(payload)
        if self.event_type_by_name(spec["event_type"]) is not None:
            raise InvalidEventType(
                f"event type {spec['event_type']!r} is already in the catalogue; patch that row "
                "instead of adding a second one for the same type"
            )
        record = self.store.create(
            EVENT_TYPE_COLLECTION, spec, room_id=room_id, actor=actor, source=source
        )
        return _with_room(record)

    def update_event_type(
        self, event_type_id: str, patch: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        existing = self.store.get(str(event_type_id))
        if existing is None or existing.get("collection") != EVENT_TYPE_COLLECTION:
            raise InvalidEventType(f"event type {event_type_id} not found")
        merged = {**(existing.get("data") or {}), **dict(patch)}
        merged.pop("id", None)
        spec = normalise_event_type(merged)
        record = self.store.update(str(event_type_id), spec, actor=actor, source=source)
        return _with_room(record)

    def delete_event_type(
        self, event_type_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        existing = self.store.get(str(event_type_id))
        if existing is None or existing.get("collection") != EVENT_TYPE_COLLECTION:
            raise InvalidEventType(f"event type {event_type_id} not found")
        return self.store.delete(str(event_type_id), actor=actor, source=source)

    # -- field maps --------------------------------------------------------- #

    def field_maps(
        self, *, event_type: str | None = None, connector_id: str | None = None
    ) -> list[dict[str, Any]]:
        rows = self.store.list(FIELD_MAP_COLLECTION, limit=1000)
        result = [_with_room(row) for row in rows]
        if event_type is not None:
            result = [row for row in result if str(row.get("event_type")) == str(event_type)]
        if connector_id is not None:
            result = [
                row for row in result if str(row.get("connector_id") or "") == str(connector_id)
            ]
        return sorted(result, key=lambda row: (str(row.get("event_type")), str(row["id"])))

    def field_map(self, field_map_id: str) -> dict[str, Any] | None:
        record = self.store.get(str(field_map_id))
        if record is None or record.get("collection") != FIELD_MAP_COLLECTION:
            return None
        return _with_room(record)

    def save_field_map(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        spec = normalise_field_map(payload)
        if not spec["connector_id"]:
            # A map with no connector would be ambiguous with more than one CRM attached,
            # and the researched create target comes from the connection.
            raise InvalidFieldMapConnector(
                "connector_id is required: a field map says which CRM it writes to"
            )
        record = self.store.create(
            FIELD_MAP_COLLECTION, spec, room_id=room_id, actor=actor, source=source
        )
        return _with_room(record)

    def update_field_map(
        self, field_map_id: str, patch: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        existing = self.store.get(str(field_map_id))
        if existing is None or existing.get("collection") != FIELD_MAP_COLLECTION:
            raise InvalidFieldMapMissing(f"field map {field_map_id} not found")
        merged = {**(existing.get("data") or {}), **dict(patch)}
        merged.pop("id", None)
        spec = normalise_field_map(merged)
        record = self.store.update(str(field_map_id), spec, actor=actor, source=source)
        return _with_room(record)

    def delete_field_map(
        self, field_map_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        existing = self.store.get(str(field_map_id))
        if existing is None or existing.get("collection") != FIELD_MAP_COLLECTION:
            raise InvalidFieldMapMissing(f"field map {field_map_id} not found")
        return self.store.delete(str(field_map_id), actor=actor, source=source)

    def field_map_for(self, event_type: str, connector_id: str) -> dict[str, Any] | None:
        """The map covering one event type on one connector, or ``None``.

        An exact (event type, connector) match wins; a map with no connector set applies
        to whichever connector is in use. The second rule is what lets a team add a
        mapping row before the connector exists, which is the order the researched flow
        reads in - catalogue and map first, then the connection that carries them.
        """
        candidates = [
            row
            for row in self.field_maps(event_type=event_type)
            if row.get("enabled", True)
            and str(row.get("connector_id") or "") in (str(connector_id), "")
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda row: (str(row.get("connector_id") or "") == "", str(row["id"])))
        return candidates[0]

    # -- engagement rows: researched step 2, first half --------------------- #

    def record_engagement(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Store the room's own record of what a buyer did.

        The payload is kept verbatim - the store is schema-flexible, so a team shipping a
        new field on an engagement event must not need anyone to change this. The two
        workflow-state fields the researched step 5 writes are the only ones added here,
        and they start empty.
        """
        event_type = as_text(payload.get("type") or payload.get("event_type"))
        if not event_type:
            raise InvalidEventType(
                "type is required: the event catalogue and the field map are both keyed on it"
            )
        data = {key: value for key, value in dict(payload).items()}
        data["type"] = event_type
        data.setdefault("sync_state", "pending")
        data[CRM_RECORD_ID_FIELD] = None
        record = self.store.create(
            ENGAGEMENT_COLLECTION, data, room_id=str(room_id), actor=actor, source=source
        )
        return record

    def engagement(self, engagement_id: str) -> dict[str, Any] | None:
        record = self.store.get(str(engagement_id))
        if record is None or record.get("collection") != ENGAGEMENT_COLLECTION:
            return None
        return record

    def engagements(
        self,
        room_id: str | None = None,
        *,
        type: str | None = None,
        sync_state: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """The room's engagement feed, newest first, with each event's sync state.

        Filters are JSON paths in each row's own payload, resolved through the dynamic
        index, so a new state or a new event type needs no change here.
        """
        where: dict[str, Any] = {}
        if type is not None:
            where["type"] = type
        if sync_state is not None:
            where[SYNC_STATE_FIELD] = sync_state
        if where:
            rows = self.store.find(ENGAGEMENT_COLLECTION, where, limit=limit)
        else:
            rows = self.store.list(ENGAGEMENT_COLLECTION, room_id=room_id, limit=limit)
        if room_id is not None:
            rows = [row for row in rows if str(row.get("room_id")) == str(room_id)]
        return rows

    # -- queue rows: researched step 2, second half ------------------------- #

    def enqueue(
        self,
        engagement: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Create the pending CRM write for one engagement row.

        A separate row from the engagement itself, because the researched flow has two
        different lifetimes: the room knows what the buyer did immediately, and the write
        may be pending, retried, blocked or failed long afterwards. Collapsing them would
        mean rewriting the event every time the worker touched it, and the audit trail
        would be a series of edits to one fact rather than a series of attempts.
        """
        payload = {
            "engagement_id": engagement["id"],
            "room_id": engagement.get("room_id"),
            "type": (engagement.get("data") or {}).get("type"),
            "state": "pending",
            "connector_id": None,
            "attempts": 0,
            "attempt_statuses": [],
            "attempt_log": [],
            "findings": [],
            "request": {},
            "vendor_error": {},
            "resolution": {},
            "crm_record_id": None,
            "crm_record_id_from": None,
            "http_status": None,
            "error": None,
            "failure_reason": None,
            "block_reason": None,
            "needs_manual_update": False,
            "synced_at": None,
            "last_attempt_at": None,
            "last_source": source,
        }
        record = self.store.create(
            QUEUE_COLLECTION, payload, room_id=engagement.get("room_id"), actor=actor, source=source
        )
        return record

    def queue_row(self, queue_id: str) -> dict[str, Any] | None:
        record = self.store.get(str(queue_id))
        if record is None or record.get("collection") != QUEUE_COLLECTION:
            return None
        return record

    def queue_rows(
        self,
        room_id: str | None = None,
        *,
        state: str | None = None,
        engagement_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Queue rows, oldest first.

        Oldest first is the order the queue is worked in, and it is the reverse of the
        store's default listing. A worker that took the newest rows first would starve the
        backlog behind a busy room, and the backlog is exactly what a buyer who engaged
        during an outage is waiting on.
        """
        where: dict[str, Any] = {}
        if state is not None:
            where["state"] = state
        if engagement_id is not None:
            where["engagement_id"] = str(engagement_id)
        rows = (
            self.store.find(QUEUE_COLLECTION, where, limit=max(limit, 1))
            if where
            else self.store.list(QUEUE_COLLECTION, room_id=room_id, limit=limit)
        )
        if room_id is not None:
            rows = [row for row in rows if str(row.get("room_id")) == str(room_id)]
        rows.sort(key=lambda row: (str(row.get("created_at") or ""), str(row.get("id"))))
        return rows[: max(1, int(limit))]

    def pending_rows(self, room_id: str | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
        """The rows a drain would work, in the order it would work them."""
        return self.queue_rows(room_id, state="pending", limit=limit)

    def _settle(
        self,
        queue_id: str,
        state: str,
        patch: Mapping[str, Any],
        *,
        engagement_patch: Mapping[str, Any] | None = None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Move a queue row to a terminal state, and mirror it onto the engagement row.

        Both writes carry the same ``source``: the researched step 5 changes two rows in
        one outcome, and an audit trail that named the route for one and not the other
        would make a reader doubt the pair.
        """
        if state not in QUEUE_STATES:
            raise ValueError(f"{state!r} is not a queue state")
        existing = self.store.get(str(queue_id))
        if existing is None or existing.get("collection") != QUEUE_COLLECTION:
            raise InvalidFieldMapMissing(f"queue row {queue_id} not found")
        merged = {
            **(existing.get("data") or {}),
            "state": state,
            "last_source": source,
            **dict(patch),
        }
        record = self.store.update(str(queue_id), merged, actor=actor, source=source)
        if engagement_patch is not None:
            engagement_id = str((existing.get("data") or {}).get("engagement_id") or "")
            if engagement_id and self.store.get(engagement_id) is not None:
                self.store.update(engagement_id, dict(engagement_patch), actor=actor, source=source)
        return record

    def _append_attempts(
        self,
        queue_id: str,
        attempt_log: Sequence[Mapping[str, Any]],
        attempt_statuses: Sequence[Any],
    ) -> tuple[list[dict[str, Any]], list[Any], int]:
        """The whole attempt history for a row: what came before, then this run.

        Appended, not replaced. A row retried by hand produces a second run, and the reason
        the second one happened is the first one - so overwriting would leave a row whose
        ``attempt_log`` shows only the successful retry and no trace of what it took to get
        there. The attempt numbers are renumbered from one so the history reads as one
        sequence rather than two.
        """
        existing = self.store.get(str(queue_id))
        previous = list((existing.get("data") or {}).get("attempt_log") or []) if existing else []
        statuses = (
            list((existing.get("data") or {}).get("attempt_statuses") or []) if existing else []
        )
        combined = previous + [dict(entry) for entry in attempt_log]
        renumbered = [
            {**entry, "attempt": number} for number, entry in enumerate(combined, start=1)
        ]
        return renumbered, statuses + list(attempt_statuses), len(previous) + len(attempt_log)

    def mark_synced(
        self,
        queue_id: str,
        *,
        connector_id: str,
        crm_record_id: str,
        crm_record_id_from: str | None,
        attempt_log: Sequence[Mapping[str, Any]],
        attempt_statuses: Sequence[Any],
        request: Mapping[str, Any],
        findings: Sequence[Mapping[str, Any]],
        resolution: Mapping[str, Any],
        vendor_error: Mapping[str, Any],
        http_status: int | None,
        duration_ms: float = 0.0,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """The researched step 5, success half: write the id, mark the event synced."""
        now = utcnow()
        attempts, statuses, total = self._append_attempts(queue_id, attempt_log, attempt_statuses)
        return self._settle(
            queue_id,
            "synced",
            {
                "connector_id": as_text(connector_id) or None,
                "crm_record_id": crm_record_id,
                "crm_record_id_from": crm_record_id_from,
                "attempts": total,
                "attempt_log": attempts,
                "attempt_statuses": statuses,
                "request": dict(request),
                "findings": [dict(entry) for entry in findings],
                "resolution": dict(resolution),
                "vendor_error": dict(vendor_error),
                "http_status": http_status,
                "error": None,
                "failure_reason": None,
                "block_reason": None,
                "needs_manual_update": False,
                "synced_at": now,
                "last_attempt_at": now,
            },
            engagement_patch={
                CRM_RECORD_ID_FIELD: crm_record_id,
                SYNC_STATE_FIELD: "synced",
                "crm_record_id_from": crm_record_id_from,
                "synced_at": now,
            },
            actor=actor,
            source=source,
        )

    def mark_failed(
        self,
        queue_id: str,
        *,
        connector_id: str,
        failure_reason: str,
        attempt_log: Sequence[Mapping[str, Any]],
        attempt_statuses: Sequence[Any],
        request: Mapping[str, Any],
        findings: Sequence[Mapping[str, Any]],
        resolution: Mapping[str, Any],
        vendor_error: Mapping[str, Any],
        http_status: int | None,
        error: str | None,
        needs_manual_update: bool,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """The researched step 5, failure half: recorded, with the whole attempt log."""
        now = utcnow()
        attempts, statuses, total = self._append_attempts(queue_id, attempt_log, attempt_statuses)
        return self._settle(
            queue_id,
            "failed",
            {
                "connector_id": as_text(connector_id) or None,
                "crm_record_id": None,
                "attempts": total,
                "attempt_log": attempts,
                "attempt_statuses": statuses,
                "request": dict(request),
                "findings": [dict(entry) for entry in findings],
                "resolution": dict(resolution),
                "vendor_error": dict(vendor_error),
                "http_status": http_status,
                "error": error,
                "failure_reason": failure_reason,
                "block_reason": None,
                "needs_manual_update": bool(needs_manual_update),
                "synced_at": None,
                "last_attempt_at": now,
            },
            engagement_patch={
                SYNC_STATE_FIELD: "failed",
                "failure_reason": failure_reason,
                "needs_manual_update": bool(needs_manual_update),
                "last_attempt_at": now,
            },
            actor=actor,
            source=source,
        )

    def mark_blocked(
        self,
        queue_id: str,
        *,
        block_reason: str,
        resolution: Mapping[str, Any] | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """A row this build will not send, with the named reason.

        The reason must be one this build documents. Coercing an unknown one into a
        documented one would put a wrong explanation on a row: a rep reading
        ``field_map_empty`` on a row that failed for another reason has been actively
        misled, and the reason is the only thing on the row a human acts on.

        Not remembered as blocked: the next drain re-plans a blocked row exactly as it
        would a pending one, so fixing the configuration and running the queue again is
        enough.
        """
        if block_reason not in BLOCK_REASONS:
            raise ValueError(
                f"{block_reason!r} is not a documented block reason; "
                f"choose one of {sorted(BLOCK_REASONS)}"
            )
        reason = block_reason
        return self._settle(
            queue_id,
            "blocked",
            {
                "block_reason": reason,
                "block_detail": BLOCK_REASONS[reason],
                "resolution": dict(resolution or {}),
                "needs_manual_update": False,
            },
            engagement_patch={SYNC_STATE_FIELD: "blocked", "block_reason": reason},
            actor=actor,
            source=source,
        )

    # -- the Sync log ------------------------------------------------------- #

    def log_attempt(
        self,
        *,
        queue_row: Mapping[str, Any],
        report: Mapping[str, Any],
        outcome: str,
        trigger: str,
        findings: Sequence[Mapping[str, Any]] = (),
        resolution: Mapping[str, Any] | None = None,
        request: Mapping[str, Any] | None = None,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """One chronological row in the Sync log, per write the worker made.

        Appended rather than updated: a row retried by hand produces a *second* row, so
        the panel reads as a history of what the worker did and when, which is what "the
        admin **Sync log** panel" is for.
        """
        payload = {
            "queue_id": queue_row.get("id"),
            "engagement_id": (queue_row.get("data") or {}).get("engagement_id"),
            "outcome": outcome,
            "trigger": trigger,
            "vendor": (request or {}).get("vendor"),
            "object": (request or {}).get("object"),
            "url": (request or {}).get("url"),
            "http_status": report.get("http_status"),
            "error": report.get("error"),
            "attempts": report.get("attempts"),
            "attempt_statuses": list(report.get("attempt_statuses") or []),
            "attempt_log": [dict(entry) for entry in (report.get("attempt_log") or [])],
            "request": dict(request or {}),
            "crm_record_id": report.get("crm_record_id"),
            "crm_record_id_from": report.get("crm_record_id_from"),
            "crm_record_id_sourced": report.get("crm_record_id_sourced"),
            "vendor_error": dict(report.get("vendor_error") or {}),
            "needs_manual_update": report.get("needs_manual_update"),
            "failure_reason": (queue_row.get("data") or {}).get("failure_reason"),
            "findings": [dict(entry) for entry in findings],
            "resolution": dict(resolution or {}),
        }
        record = self.store.create(
            SYNC_LOG_COLLECTION,
            payload,
            room_id=room_id if room_id is not None else queue_row.get("room_id"),
            actor=actor,
            source=source,
        )
        return record

    def sync_log(
        self,
        room_id: str | None = None,
        *,
        outcome: str | None = None,
        vendor: str | None = None,
        engagement_id: str | None = None,
        needs_manual_update: bool | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if outcome is not None:
            where["outcome"] = outcome
        if vendor is not None:
            where["vendor"] = vendor
        if engagement_id is not None:
            where["engagement_id"] = str(engagement_id)
        if needs_manual_update is not None:
            where["needs_manual_update"] = needs_manual_update
        rows = (
            self.store.find(SYNC_LOG_COLLECTION, where, limit=limit)
            if where
            else self.store.list(SYNC_LOG_COLLECTION, room_id=room_id, limit=limit)
        )
        if room_id is not None:
            rows = [row for row in rows if str(row.get("room_id")) == str(room_id)]
        return rows

    def sync_log_entry(self, log_id: str) -> dict[str, Any] | None:
        record = self.store.get(str(log_id))
        if record is None or record.get("collection") != SYNC_LOG_COLLECTION:
            return None
        return record


__all__ = [
    "CRM_RECORD_ID_FIELD",
    "CONNECTOR_COLLECTION",
    "ENGAGEMENT_COLLECTION",
    "EVENT_TYPE_COLLECTION",
    "FIELD_MAP_COLLECTION",
    "InvalidFieldMapConnector",
    "InvalidFieldMapMissing",
    "QUEUE_COLLECTION",
    "ROOM_COLLECTION",
    "SYNC_STATE_FIELD",
    "SYNC_LOG_COLLECTION",
    "SyncBook",
    "normalise_connector",
    "normalise_event_type",
]
