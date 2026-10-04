"""The read seam: how a reconciliation gets the record it has to repair.

Reconciliation is a full re-read. "Make a Salesforce API call, such as a REST API
call, to retrieve the full data for record C, and save it in your system." So this
workflow cannot be a ledger of intentions: it has to be able to ask the vendor what
a record holds now.

The research names the vendor's tables and the room's replica as two separate data
sources - "CRM entity tables (incl. Recycle Bin / soft-deleted); room replica;
dirty-set and replay-id store" - so this module keeps them apart. The vendor's tables
are the thing that is read. The replica is the thing that is written. Reading the
replica to repair the replica would make the repair a no-op that always agrees with
itself.

Two readers ship here. :class:`StoredCrm` reads the room's own copy of the vendor's
tables, which is what a room without an org connection has. :class:`SimulatedCrm`
holds its tables in memory, which is what a domain test sets up in four lines.
Neither opens a socket: a network call in a repair path cannot be tested, and no
workflow in this research provisions an org connection.

The wire shapes are the researched ones, so a room pointed at a live vendor changes
this module and nothing above it: the per-record read is a REST-style lookup by id,
the non-deleted read is the overflow procedure's option (a), the Recycle Bin read is
``isDeleted=true`` against the entity, and the Dataverse read is a delta response
whose deleted rows carry ``$deletedEntity`` in their context and ``deleted`` as
their reason.

Every answer is plain JSON. A record payload is arbitrary JSON, and a team adding a
CRM field must need no coordination with anyone.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from dsr.store import RecordStore

#: The room's copy of the vendor's entity tables, including the Recycle Bin.
#:
#: Namespaced to this workflow, and separate from the replica collection on purpose.
#: This is the thing a reconciliation reads; the replica is the thing it writes.
SOURCE_ROWS = "crm_gap_source_row"

#: Dataverse's own marker for a row that is a deletion rather than a value. The
#: research quotes the context verbatim, so it is kept verbatim.
DELETED_ENTITY_SUFFIX = "$deletedEntity"

#: The reason Dataverse gives a deleted row. The research quotes it as
#: ``"reason": "deleted"``.
DELETED_REASON = "deleted"


@runtime_checkable
class CrmReader(Protocol):
    """What a reconciliation needs in order to repair a replica.

    The five questions below are the whole seam. Everything the engine decides is
    decided above this line; everything the vendor decides is decided under it.

    Every method takes the room first, because the vendor's tables are reached
    through the room's connection and a room that holds two buyers reads two
    different orgs.
    """

    #: The vendor this reader speaks for. One of the researched names.
    vendor: str

    def entities(self, room_id: str) -> list[str]:
        """The entity types this room can repair."""

    def record(self, room_id: str, entity: str, record_id: str) -> dict[str, Any] | None:
        """One record as the vendor holds it now, or ``None`` when it is gone.

        ``None`` is a real answer and not a failure. A ``GAP_DELETE`` is repaired by
        exactly this: the read returns nothing and the room writes a tombstone.
        """

    def live_records(self, room_id: str, entity: str) -> list[dict[str, Any]]:
        """Every record of the entity that is not deleted.

        The overflow procedure's option (a): "Get the non-deleted records from
        Salesforce, and synchronize."
        """

    def recycle_bin(self, room_id: str, entity: str) -> list[dict[str, Any]]:
        """Every soft-deleted record of the entity.

        "Query all records for the entity with ``isDeleted=true``. You get all the
        soft-deleted records for that entity that are in the Recycle Bin."
        """

    def delta(self, room_id: str, entity: str, delta_link: str | None = None) -> dict[str, Any]:
        """One Dataverse change page: the live rows, the deleted ids, the next link.

        Deletes arrive through the same cursor, so this is the only answer a
        Dataverse reconciliation needs and no delete diff is derived from it.
        """


class StoredCrm:
    """The room's own copy of the vendor's tables, read through the store.

    The reader a feature uses by default. A row with a falsy ``isDeleted`` is live;
    a row with a truthy one is in the Recycle Bin, which is the difference the
    research draws between "soft-deleted" and "no record". A row that is absent is
    gone entirely, and that is what makes a ``GAP_DELETE`` repairable.
    """

    def __init__(self, store: RecordStore, vendor: str = "salesforce") -> None:
        self.store = store
        self.vendor = vendor

    def _rows(self, room_id: str) -> list[dict[str, Any]]:
        return [
            (row.get("data") or {})
            for row in self.store.list(SOURCE_ROWS, room_id=room_id, limit=1000)
        ]

    def entities(self, room_id: str) -> list[str]:
        return sorted({str(row.get("entity")) for row in self._rows(room_id) if row.get("entity")})

    def record(self, room_id: str, entity: str, record_id: str) -> dict[str, Any] | None:
        for row in self._rows(room_id):
            if str(row.get("entity")) != entity or str(row.get("record_id")) != record_id:
                continue
            if row.get("is_deleted"):
                return None
            return _named(str(row.get("record_id")), row.get("payload"))
        return None

    def live_records(self, room_id: str, entity: str) -> list[dict[str, Any]]:
        return [
            _named(str(row.get("record_id")), row.get("payload"))
            for row in sorted(self._rows(room_id), key=_sort_key)
            if str(row.get("entity")) == entity and not row.get("is_deleted")
        ]

    def recycle_bin(self, room_id: str, entity: str) -> list[dict[str, Any]]:
        return [
            _named(str(row.get("record_id")), row.get("payload"))
            for row in sorted(self._rows(room_id), key=_sort_key)
            if str(row.get("entity")) == entity and row.get("is_deleted")
        ]

    def delta(self, room_id: str, entity: str, delta_link: str | None = None) -> dict[str, Any]:
        """One change page for the entity, with the deletions carried inline.

        The shape is the research's own example::

            {"@odata.deltaLink": ..., "value": [{...},
             {"@odata.context": ".../$deletedEntity", "id": "2e451703-...",
              "reason": "deleted"}]}

        and the room reads it back through
        :func:`dsr.crm_integration.reconcile.delta_page`, so a live vendor's response
        and this one go through the same code.

        The link advances on every call, so a caller that resumes from the position
        it was handed sees the room's next page rather than the one it already read.
        """
        rows = sorted(self._rows(room_id), key=_sort_key)
        value: list[dict[str, Any]] = []
        for row in rows:
            if str(row.get("entity")) != entity:
                continue
            record_id = str(row.get("record_id"))
            if row.get("is_deleted"):
                value.append(
                    {
                        "@odata.context": (
                            f"https://example.test/{entity}/{DELETED_ENTITY_SUFFIX}"
                        ),
                        "id": record_id,
                        "reason": DELETED_REASON,
                    }
                )
            else:
                value.append(_named(record_id, row.get("payload")))
        position = 1
        if delta_link and delta_link.startswith(f"dl:{entity}:"):
            tail = delta_link.rsplit(":", 1)[-1]
            position = (int(tail) if tail.isdigit() else 0) + 1
        return {"@odata.deltaLink": f"dl:{entity}:{position}", "value": value}


class SimulatedCrm:
    """The vendor's tables held in memory, for a test that sets the world up in four lines.

    Deliberately small and deliberately honest about what it is. Every answer is
    plain JSON, and every id it mints is derived from the entity and the record id
    so a test can predict it. ``room_id`` is accepted and ignored, because one
    instance stands for one org.
    """

    vendor = "salesforce"

    def __init__(
        self,
        records: dict[str, dict[str, dict[str, Any]]] | None = None,
        *,
        vendor: str = "salesforce",
    ) -> None:
        self.vendor = vendor
        #: ``{entity: {record_id: payload}}``. A payload with a truthy
        #: ``isDeleted`` is in the Recycle Bin rather than gone, which is the
        #: difference the research draws between "soft-deleted" and "no record".
        self.records: dict[str, dict[str, dict[str, Any]]] = {
            entity: dict(rows) for entity, rows in (records or {}).items()
        }
        #: Every call this reader answered, so a test can assert which question the
        #: repair asked. A repair that silently read the wrong thing is the failure
        #: this recovery path cannot afford.
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    # -- writing the vendor's copy, so a test can set the world up -------------- #

    def put(self, entity: str, record_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Store a live record."""
        rows = self.records.setdefault(entity, {})
        row = dict(payload)
        row["Id"] = record_id
        row["isDeleted"] = False
        rows[record_id] = row
        return dict(row)

    def delete(self, entity: str, record_id: str) -> bool:
        """Move a record to the Recycle Bin. The vendor keeps it."""
        rows = self.records.get(entity, {})
        row = rows.get(record_id)
        if row is None:
            return False
        row["isDeleted"] = True
        return True

    def purge(self, entity: str, record_id: str) -> bool:
        """Remove a record entirely, so a read of it returns nothing.

        This is the state a ``GAP_DELETE`` is repaired from, and it is why a reader
        can answer ``None``: the vendor no longer holds the record at all.
        """
        return self.records.get(entity, {}).pop(record_id, None) is not None

    # -- reading -------------------------------------------------------------- #

    def entities(self, room_id: str) -> list[str]:
        self.calls.append(("entities", (room_id,)))
        return sorted(self.records)

    def record(self, room_id: str, entity: str, record_id: str) -> dict[str, Any] | None:
        self.calls.append(("record", (entity, record_id)))
        row = self.records.get(entity, {}).get(record_id)
        if row is None or row.get("isDeleted"):
            return None
        return dict(row)

    def live_records(self, room_id: str, entity: str) -> list[dict[str, Any]]:
        self.calls.append(("live_records", (entity,)))
        rows = self.records.get(entity, {})
        return [dict(row) for _, row in sorted(rows.items()) if not row.get("isDeleted")]

    def recycle_bin(self, room_id: str, entity: str) -> list[dict[str, Any]]:
        self.calls.append(("recycle_bin", (entity,)))
        rows = self.records.get(entity, {})
        return [dict(row) for _, row in sorted(rows.items()) if row.get("isDeleted")]

    def delta(self, room_id: str, entity: str, delta_link: str | None = None) -> dict[str, Any]:
        """One change page, with the deletions carried inline.

        The shape is the research's own, and the room reads it back through
        :func:`dsr.crm_integration.reconcile.delta_page`, so a live vendor's response
        and this one go through the same code.
        """
        self.calls.append(("delta", (entity, delta_link)))
        rows = self.records.get(entity, {})
        value: list[dict[str, Any]] = []
        for record_id, row in sorted(rows.items()):
            if row.get("isDeleted"):
                value.append(
                    {
                        "@odata.context": f"https://example.test/{entity}/{DELETED_ENTITY_SUFFIX}",
                        "id": record_id,
                        "reason": DELETED_REASON,
                    }
                )
            else:
                value.append(dict(row))
        position = 1
        if delta_link and delta_link.startswith(f"dl:{entity}:"):
            tail = delta_link.rsplit(":", 1)[-1]
            position = (int(tail) if tail.isdigit() else 0) + 1
        return {"@odata.deltaLink": f"dl:{entity}:{position}", "value": value}


def _named(record_id: str, payload: Any) -> dict[str, Any]:
    """A payload with its own id in it, because a vendor's payload names its record.

    Without this the room could not tell which row a live record belongs to, and the
    overflow procedure's delete diff would delete everything: the difference is
    ``held minus returned``, and a returned row with no id returns nothing to
    subtract.
    """
    row = dict(payload or {})
    row["Id"] = record_id
    return row


def _sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("entity")), str(row.get("record_id")))


def default_reader(store: RecordStore, vendor: str = "salesforce") -> StoredCrm:
    """The reader a feature uses when it has no org connection of its own.

    A room pointed at a live vendor passes its own reader to
    :class:`~dsr.crm_integration.reconcile_engine.ReconcileEngine` instead, and
    nothing above this line changes.
    """
    return StoredCrm(store, vendor=vendor)


__all__ = [
    "DELETED_ENTITY_SUFFIX",
    "DELETED_REASON",
    "SOURCE_ROWS",
    "CrmReader",
    "SimulatedCrm",
    "StoredCrm",
    "default_reader",
]
