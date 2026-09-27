"""The vendor side of the seam, held in this product's own audited store.

There is no CRM account behind this build, and pretending otherwise would be the
easiest way to ship a workflow nobody can review. So the "CRM" is a *local
simulation* whose state lives in three record collections and is read and written
through the same audited store as everything else:

``crm_remote_object``     the objects this connection has in the CRM
``crm_remote_property``   the properties on those objects, whoever created them
``crm_remote_key``        the alternate keys, and their background index state

The point of simulating the *effect* rather than only recording the request is
idempotency. The research's central rule is "Creation is idempotent: re-running
the installer is a no-op for already-present properties", and a build that only
recorded what it *would* send could assert that sentence in a comment and
nothing more. Here the second run really does find the properties already there,
really does create nothing, and really does write no create row to the audit log.
That is a property a test can hold the build to.

The pairs are deliberately distinct. ``crm_remote_property`` holds a property a
tenant's administrator added by hand in HubSpot, and there is no ``crm_property``
record for it, because this product did not create it and must not imply that it
did. The ``crm_`` collections are ours; the ``crm_remote_`` ones stand in for the
vendor's.

Every call records the request it corresponds to, spelled the way the research
spells it - ``POST /crm/properties/2026-09/2145902217`` for a HubSpot property,
``POST /api/data/v9.2/CreateEntityKey`` for a Dataverse key. The requests are
returned to the caller rather than logged to disk, so a run record can show
exactly what went over the wire for a deployment nobody was watching.

The key's background index is the researched ``EntityKeyMetadata.AsyncJob`` and
``EntityKeyIndexStatus`` pair. The four status names are sourced; how far along
a build is between one look and the next is this build's simulation, and it is
recorded as the ``key-index-advances-on-poll`` inference.
"""

from __future__ import annotations

import random
from typing import Any, Mapping

from dsr.db.audited import RecordNotFound
from dsr.store import RecordStore

OBJECTS = "crm_remote_object"
PROPERTIES = "crm_remote_property"
KEYS = "crm_remote_key"

#: How many looks at a background index build before it reports ``Active``.
#: Sourced names, unsourced cadence: see the module docstring and the inference
#: register.
DEFAULT_INDEX_POLLS = 2

#: What a connection's ``simulate`` block may ask the simulated vendor to do with
#: a key build. ``active`` is the honest path, ``failed`` exists so the
#: ``ReactivateEntityKey`` repair path has a half-provisioned key to repair -
#: which is a state the research names as worth having, not one to invent for the
#: demo's sake.
SIMULATED_INDEX_OUTCOMES = ("active", "failed")


def _new_numeric_id(rng: random.Random) -> int:
    """A HubSpot-shaped object type id: a positive integer, stable per run.

    HubSpot's property path carries the object type as a number, so a
    string-valued id would put a value on the wire the vendor does not use. Drawn
    from a seeded generator so two runs of the seeder produce the same demo.
    """
    return rng.randrange(100_000_000, 999_999_999)


class SimulatedCrm:
    """One vendor, reached through a connection, with its state in the store."""

    def __init__(self, store: RecordStore, *, rng: random.Random | None = None) -> None:
        self.store = store
        self.rng = rng or random.Random("crm-provisioning")

    # -- reads: the researched step two ------------------------------------ #

    def service_document(self, connection: Mapping[str, Any]) -> dict[str, Any]:
        """``GET /`` - the service document, the first thing step two reads.

        Answered from the connection's vendor and environment rather than
        returned as an empty stub, because the two are the only things this
        build knows about the tenant, and a client rendering the diff wants to
        see which account it is diffing against.
        """
        return {
            "vendor": connection.get("vendor"),
            "environment": connection.get("environment"),
            "connection_id": connection.get("id"),
            "reachable": True,
        }

    def object_definitions(self, connection: Mapping[str, Any]) -> list[dict[str, Any]]:
        """The custom objects this connection already has.

        Ordered by name so a diff is a stable document rather than one that
        reshuffles between two runs against unchanged state.
        """
        rows = self.store.find(
            OBJECTS,
            {"connection_id": connection["id"]},
            limit=1000,
        )
        return sorted(
            (dict(row["data"]) | {"id": row["id"]} for row in rows), key=lambda d: str(d.get("name"))
        )

    def property_definitions(
        self, connection: Mapping[str, Any], crm_object_name: str
    ) -> list[dict[str, Any]]:
        """The properties on one object, whoever created them."""
        rows = self.store.find(
            PROPERTIES,
            {"connection_id": connection["id"], "object_name": crm_object_name},
            limit=1000,
        )
        return sorted(
            (dict(row["data"]) | {"id": row["id"]} for row in rows), key=lambda d: str(d.get("name"))
        )

    def find_object(self, connection: Mapping[str, Any], name: str) -> dict[str, Any] | None:
        for entry in self.object_definitions(connection):
            if entry.get("name") == name:
                return entry
        return None

    def find_property(
        self, connection: Mapping[str, Any], crm_object_name: str, name: str
    ) -> dict[str, Any] | None:
        for entry in self.property_definitions(connection, crm_object_name):
            if entry.get("name") == name:
                return entry
        return None

    def key_definitions(self, connection: Mapping[str, Any]) -> list[dict[str, Any]]:
        rows = self.store.find(KEYS, {"connection_id": connection["id"]}, limit=1000)
        return sorted(
            (dict(row["data"]) | {"id": row["id"]} for row in rows), key=lambda d: str(d.get("key_id"))
        )

    def find_key(self, connection: Mapping[str, Any], key_id: str) -> dict[str, Any] | None:
        for entry in self.key_definitions(connection):
            if entry.get("key_id") == key_id:
                return entry
        return None

    # -- writes: the researched create requests ---------------------------- #

    def create_object(
        self,
        connection: Mapping[str, Any],
        request: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Create the custom object. The record id is the vendor's own id."""
        name = str(request.get("name"))
        existing = self.find_object(connection, name)
        if existing is not None:
            # Not reachable through the diff, which reads first. Kept so a direct
            # call cannot produce two objects of one name, which is the one state
            # the vendor would refuse.
            return {**existing, "created": False}

        style = str(request.get("_object_id_style") or "numeric")
        crm_object_id = _new_numeric_id(self.rng) if style == "numeric" else name
        row = self.store.create(
            OBJECTS,
            {
                "connection_id": connection["id"],
                "vendor": connection.get("vendor"),
                "name": name,
                "label": request.get("label") or name,
                "description": request.get("description") or "",
                "crm_object_id": crm_object_id,
                "object_id_style": style,
            },
            room_id=connection.get("room_id"),
            actor=actor,
            source=source,
        )
        return {**dict(row["data"]), "id": row["id"], "created": True}

    def create_property(
        self,
        connection: Mapping[str, Any],
        crm_object_name: str,
        request: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Create one property on an object. The record id is the vendor's own."""
        name = str(request.get("name") or request.get("schemaName"))
        existing = self.find_property(connection, crm_object_name, name)
        if existing is not None:
            return {**existing, "created": False}

        row = self.store.create(
            PROPERTIES,
            {
                "connection_id": connection["id"],
                "vendor": connection.get("vendor"),
                "object_name": crm_object_name,
                "name": name,
                "label": request.get("label") or request.get("AttributeDisplayName") or name,
                "type": request.get("type") or request.get("AttributeType") or "string",
                "field_type": request.get("fieldType") or request.get("AttributeFormat") or "",
                "group_name": request.get("groupName") or "",
                "length": request.get("MaxLength") or request.get("length"),
                "options": [
                    {"label": str(item.get("label")), "value": str(item.get("value"))}
                    for item in (request.get("options") or [])
                    if isinstance(item, Mapping)
                ],
            },
            room_id=connection.get("room_id"),
            actor=actor,
            source=source,
        )
        return {**dict(row["data"]), "id": row["id"], "created": True}

    # -- the alternate key, and its background build ----------------------- #

    def create_key(
        self,
        connection: Mapping[str, Any],
        crm_object_name: str,
        metadata: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """``CreateEntityKey`` with an ``EntityKeyMetadata`` body.

        The key lands ``Pending`` with an ``AsyncJob`` id, because the research is
        explicit that a table with many records builds its index in the background
        and that the API reports ``EntityKeyMetadata.EntityKeyIndexStatus`` while
        it does. A synchronous build here would have been the easy choice and would
        have hidden the state the workflow is built to surface.
        """
        key_id = f"key-{self.rng.randrange(10**8):08d}"
        outcome = str((connection.get("simulate") or {}).get("key_index") or "active")
        if outcome not in SIMULATED_INDEX_OUTCOMES:
            outcome = "active"
        row = self.store.create(
            KEYS,
            {
                "connection_id": connection["id"],
                "vendor": connection.get("vendor"),
                "object_name": crm_object_name,
                "key_id": key_id,
                "columns": list(metadata.get("KeyAttributes") or []),
                "status": "Pending",
                "async_job_id": f"job-{self.rng.randrange(10**8):08d}",
                "polls": 0,
                "simulated_outcome": outcome,
                "reactivated_count": 0,
                "background": True,
            },
            room_id=connection.get("room_id"),
            actor=actor,
            source=source,
        )
        return {**dict(row["data"]), "id": row["id"], "created": True}

    def _key_row(self, connection: Mapping[str, Any], key_id: str) -> dict[str, Any]:
        entry = self.find_key(connection, key_id)
        if entry is None:
            raise RecordNotFound(key_id)
        return entry

    def advance_key(
        self, connection: Mapping[str, Any], key_id: str, *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        """One look at the background job, and the build moves one step on.

        ``Pending -> In Progress -> Active`` is the researched progression. A build
        whose simulated outcome is ``failed`` reports ``Failed`` at its first step
        and stays there, because a failed index that quietly retried itself would
        make the repair path untestable and the log a lie.
        """
        entry = self._key_row(connection, key_id)
        status = str(entry.get("status") or "Pending")
        polls = int(entry.get("polls") or 0) + 1
        if status == "Failed":
            next_status = "Failed"
        elif status == "Active":
            next_status = "Active"
        elif str(entry.get("simulated_outcome")) == "failed":
            next_status = "Failed"
        else:
            next_status = "In Progress" if polls < DEFAULT_INDEX_POLLS else "Active"

        return self._patch_key(
            connection,
            key_id,
            {"status": next_status, "polls": polls, "async_job_id": f"job-{self.rng.randrange(10**8):08d}"},
            source=source,
            actor=actor,
        )

    def reactivate_key(
        self, connection: Mapping[str, Any], key_id: str, *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        """``ReactivateEntityKey``: repair a half-provisioned key.

        A key that is ``Active`` is left exactly as it is - the research calls
        this "the extension point for repairing a half-provisioned key", and
        reactivating a working key is not that. A ``Failed`` key is re-armed to
        ``Pending`` with a fresh ``AsyncJob``; a key still building is left
        building, because it is not broken.
        """

        entry = self._key_row(connection, key_id)
        status = str(entry.get("status") or "Pending")
        if status != "Failed":
            return {**entry, "reactivated": False}

        return self._patch_key(
            connection,
            key_id,
            {
                "status": "Pending",
                "polls": 0,
                "async_job_id": f"job-{self.rng.randrange(10**8):08d}",
                "reactivated_count": int(entry.get("reactivated_count") or 0) + 1,
                # The repair is what makes the key usable again, so the re-armed
                # build is one that completes. Leaving the simulated failure in
                # place would model a repair that cannot repair, which is not what
                # the sourced extension point is for.
                "simulated_outcome": "active",
            },
            source=source,
            actor=actor,
            reactivated=True,
        )

    def _patch_key(
        self,
        connection: Mapping[str, Any],
        key_id: str,
        patch: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
        reactivated: bool = False,
    ) -> dict[str, Any]:
        entry = self._key_row(connection, key_id)
        row = self.store.update(
            entry["id"],
            dict(patch),
            actor=actor,
            source=source,
        )
        return {**dict(row["data"]), "id": row["id"], "reactivated": reactivated}
