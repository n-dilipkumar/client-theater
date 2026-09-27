"""The façade the HTTP layer calls. Owns the six collections this workflow writes.

``crm_connection``       a CRM account a deployment reaches through, optionally
                         bound to a room
``crm_manifest``         a versioned sales-room package manifest, exactly as
                         shipped: the research's extensibility claim is that
                         "the room's object descriptor is data, not code", so
                         this is a record and not a constant
``crm_object``           the mapping the data flow asks for: "records the mapping
                         of room-object-id -> CRM-object-id for subsequent syncs"
``crm_property``         a property *this* workflow created, with the request that
                         created it
``crm_sync_key``         an alternate key *this* workflow requested, and what
                         became of its background build
``crm_installation``     one run of the installer, with its plan and its requests

Paired with :mod:`dsr.crm_provisioning.gateway`, which owns the three
``crm_remote_*`` collections standing in for the vendor's own state.

Why the object and property records are separate from the remote ones is worth
one paragraph, because it is the difference between an honest audit trail and a
comforting one. A tenant administrator can add a property in HubSpot's own UI.
That property exists at the vendor and is found by the diff - and there is no
``crm_property`` record for it, because this product did not create it. Writing
one would put an ``insert`` row in the audit log for a change nobody in this
system made.

**Every writing method takes a required ``source=`` keyword.** A hardcoded URL
inside a domain method is a defect, and this codebase has shipped the exact bug
the rule exists to prevent: a feature's audit log kept naming a path the app had
stopped serving. Required rather than defaulted, so the failure is a
``TypeError`` at the call site instead of an untraceable row in production.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.crm_provisioning import diff as schema_diff
from dsr.crm_provisioning import manifest as manifests
from dsr.crm_provisioning.errors import (
    DuplicateManifestVersion,
    ManifestError,
    PropertyConflict,
    ProvisioningError,
)
from dsr.crm_provisioning.gateway import SimulatedCrm
from dsr.crm_provisioning.vendors import NEUTRAL_TYPES, adapter_for, describe_adapter
from dsr.crm_provisioning.vocabulary import (
    CONNECTION_ENVIRONMENTS,
    UNSUPPORTED_VENDORS,
    VENDORS,
    vocabulary,
)
from dsr.db.audited import RecordNotFound
from dsr.store import RecordStore

CONNECTIONS = "crm_connection"
MANIFESTS = "crm_manifest"
OBJECTS = "crm_object"
PROPERTIES = "crm_property"
KEYS = "crm_sync_key"
RUNS = "crm_installation"


def _data(record: Mapping[str, Any]) -> dict[str, Any]:
    return dict(record.get("data") or {}) | {"id": record["id"]}


def _version_key(version: str) -> tuple[tuple[int, int, str], ...]:
    """Order two version strings the way a person reading them would.

    Digits compare numerically and everything else compares as text, so ``1.10.0``
    beats ``1.9.0`` and a manifest versioned by release date still installs its
    newest. Plain string ordering gets the first of those wrong, and a deploy
    pipeline that silently rolls a room back to an older descriptor is exactly the
    failure this whole package exists to prevent.

    Semver is not *required* and not imposed: a version is an opaque token this
    key merely orders sensibly, so ``2026-09-27``, ``v3`` and ``2.1.0`` all work.
    """
    parts = re.findall(r"\d+|\D+", str(version))
    return tuple((0, int(part), "") if part.isdigit() else (1, 0, part) for part in parts)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ProvisioningEngine:
    """Every decision and every write of the CRM provisioning workflow."""

    def __init__(self, store: RecordStore, *, crm: SimulatedCrm | None = None) -> None:
        self.store = store
        self.crm = crm or SimulatedCrm(store)

    # -- published vocabulary and judgement --------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        """Every value this workflow enforces against, plus both vendor surfaces.

        The type maps live here rather than in a hand-kept page, so a team reading
        the endpoint sees exactly the bodies a property create will carry, and a
        reviewer reading the diff can check the two against each other.
        """
        payload = vocabulary()
        payload["vendors_detail"] = [describe_adapter(adapter_for(name)) for name in VENDORS]
        payload["neutral_types"] = list(NEUTRAL_TYPES)
        return payload

    # -- connections -------------------------------------------------------- #

    def connections(
        self, *, room_id: str | None = None, vendor: str | None = None
    ) -> list[dict[str, Any]]:
        # ``room_id`` is an envelope column rather than a JSON path, so it cannot
        # go through ``find()``'s dynamic index; the store's own room filter is
        # the one that reaches it.
        rows = self.store.list(CONNECTIONS, room_id=room_id, limit=1000)
        listed = [_data(row) | {"room_id": row.get("room_id")} for row in rows]
        if vendor:
            wanted = str(vendor).strip().lower()
            listed = [row for row in listed if str(row.get("vendor")) == wanted]
        return sorted(listed, key=lambda row: (str(row.get("vendor")), str(row.get("name"))))

    def connection(self, connection_id: str) -> dict[str, Any]:
        record = self.store.get(connection_id)
        if record is None or record["collection"] != CONNECTIONS:
            raise RecordNotFound(connection_id)
        # ``room_id`` is an envelope column: the store strips it out of ``data``
        # on the way in, so the binding is read back from the envelope rather than
        # from the payload. Restated here because every caller that installs into
        # a connection needs it and would otherwise reach for a key that is not
        # there.
        return _data(record) | {"room_id": record.get("room_id")}

    def register_connection(
        self, payload: Mapping[str, Any] | None, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Register a CRM account the installer can provision into.

        The research opens at "Admin runs Integrations -> <connection> -> Install
        integration package", so a connection is a given this workflow assumes
        rather than one it specifies. It is registered here only because this
        product has nothing else to hang an installation on, and because a
        connection is a record like every other: a team adding a vendor's account
        ships a row, not a code path. A vendor outside the researched pair is
        accepted and *reported* as unsupported rather than refused here, so the
        account exists and the refusal comes from the install a human actually
        runs, naming the research's gap.
        """
        body = dict(payload or {})
        name = str(body.get("name") or "").strip()
        if not name:
            raise ProvisioningError("name is required")
        vendor = str(body.get("vendor") or "").strip().lower()
        if not vendor:
            raise ProvisioningError("vendor is required")
        environment = str(body.get("environment") or "production")
        if environment not in CONNECTION_ENVIRONMENTS:
            raise ProvisioningError(
                f"environment must be one of {', '.join(CONNECTION_ENVIRONMENTS)}"
            )
        simulate = body.get("simulate") or {}
        if not isinstance(simulate, Mapping):
            raise ProvisioningError("simulate must be a JSON object")
        unsupported = simulate.get("key_index")
        if unsupported is not None and unsupported not in ("active", "failed"):
            raise ProvisioningError("simulate.key_index must be 'active' or 'failed'")

        record = self.store.create(
            CONNECTIONS,
            {
                "name": name,
                "vendor": vendor,
                "environment": environment,
                "enabled": bool(body.get("enabled", True)),
                "supported": vendor in VENDORS,
                "unsupported_reason": UNSUPPORTED_VENDORS.get(vendor),
                "simulate": {k: v for k, v in simulate.items() if k == "key_index"},
            },
            room_id=body.get("room_id"),
            actor=actor,
            source=source,
        )
        return _data(record) | {"room_id": record.get("room_id")}

    # -- manifests ---------------------------------------------------------- #

    def manifests(self, *, manifest_id: str | None = None) -> list[dict[str, Any]]:
        rows = (
            self.store.find(MANIFESTS, {"manifest_id": manifest_id}, limit=1000)
            if manifest_id
            else self.store.list(MANIFESTS, limit=1000)
        )
        listed = [_data(row) for row in rows]
        return sorted(listed, key=lambda row: (str(row.get("manifest_id")), str(row.get("version"))))

    def manifest(self, manifest_id: str, version: str | None = None) -> dict[str, Any]:
        rows = self.store.find(MANIFESTS, {"manifest_id": manifest_id}, limit=1000)
        if not rows:
            raise RecordNotFound(f"manifest {manifest_id}")
        if version is None:
            # The highest by a numeric-aware order, not by string sort: "1.9.0"
            # sorts above "1.10.0" lexicographically, and a pipeline that silently
            # rolls a room back to an older descriptor is the failure this package
            # exists to prevent.
            chosen = max(rows, key=lambda row: _version_key(str(_data(row).get("version") or "")))
        else:
            chosen = next((row for row in rows if str(_data(row).get("version")) == str(version)), None)
            if chosen is None:
                raise RecordNotFound(f"manifest {manifest_id} version {version}")
        return _data(chosen)

    def register_manifest(
        self, payload: Mapping[str, Any] | None, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Register a versioned manifest. Re-registering a version is refused.

        The same body for a version already on file is accepted and reports
        ``unchanged`` rather than 409 - re-running a deploy pipeline must not fail
        on a manifest that has not changed. A *different* body for a version
        already on file is 409, because an install that ran against version 1.0.0
        must still be able to say later that the manifest it installed was this
        body, and overwriting it would make that sentence false.
        """
        body = manifests.normalise_manifest(payload)
        manifest_id = body["manifest_id"]
        version = body["version"]
        existing = [
            row
            for row in self.store.find(MANIFESTS, {"manifest_id": manifest_id}, limit=1000)
            if str(_data(row).get("version")) == version
        ]
        if existing:
            current = dict(existing[0]["data"])
            if current == body:
                return {**_data(existing[0]), "outcome": "unchanged"}
            raise DuplicateManifestVersion(
                f"manifest {manifest_id!r} version {version!r} is already registered with a "
                "different body; register the new body under a new version, because an "
                "installed run names the version it installed"
            )

        record = self.store.create(
            MANIFESTS,
            body,
            actor=actor,
            source=source,
        )
        return {**_data(record), "outcome": "created"}

    def manifest_report(self, manifest_id: str, version: str | None = None) -> dict[str, Any]:
        """A manifest with its findings for every researched vendor, and nothing else.

        Two vendors, so a team can see that a manifest installs cleanly on Dataverse
        and skips a property on HubSpot before they run anything. Unknown vendors
        are reported in the same shape, carrying the research's own gap statement,
        so a Salesforce deployment sees why rather than an empty list.
        """
        record = self.manifest(manifest_id, version)
        try:
            manifests.check_key(record)
            key_report: dict[str, Any] = {"ok": True, "error": None}
        except ProvisioningError as exc:
            key_report = {"ok": False, "error": str(exc)}
        per_vendor: dict[str, Any] = {}
        for vendor in VENDORS:
            adapter = adapter_for(vendor)
            findings = manifests.findings_for(record, adapter)
            per_vendor[vendor] = {
                "findings": findings,
                "blocking": manifests.blocking(findings),
                "skipped_properties": sorted(
                    {f["property"] for f in manifests.property_level(findings) if f.get("property")}
                ),
                "installable": not manifests.blocking(findings) and key_report["ok"],
            }
        per_vendor.update(
            {
                vendor: {
                    "findings": [],
                    "blocking": [],
                    "skipped_properties": [],
                    "installable": False,
                    "unsupported_reason": reason,
                }
                for vendor, reason in UNSUPPORTED_VENDORS.items()
            }
        )
        return {
            "manifest": record,
            "sync_key": key_report,
            "per_vendor": per_vendor,
            "installable": any(entry["installable"] for entry in per_vendor.values()),
        }

    # -- the diff ------------------------------------------------------------ #

    def plan(
        self,
        connection_id: str,
        manifest_id: str,
        version: str | None = None,
        *,
        vendor: str | None = None,
    ) -> dict[str, Any]:
        """Read the live schema and return the plan, writing nothing.

        The researched "dry-run diff view", and a read rather than a flag: this
        method cannot write, so there is no path by which a caller asking for a
        preview can create something.
        """
        connection = self.connection(connection_id)
        record = self.manifest(manifest_id, version)
        chosen = adapter_for(vendor or connection.get("vendor"))
        findings = manifests.findings_for(record, chosen)
        plan = schema_diff.compute(
            connection=connection,
            manifest=record,
            adapter=chosen,
            findings=findings,
            objects=self.crm.object_definitions(connection),
            properties=self.crm.property_definitions(connection, str((record.get("object") or {}).get("name"))),
            keys=self.crm.key_definitions(connection),
        )
        plan["service_document"] = self.crm.service_document(connection)
        plan["blocking_findings"] = manifests.blocking(findings)
        return plan

    def install(
        self,
        connection_id: str,
        manifest_id: str,
        version: str | None = None,
        *,
        dry_run: bool = False,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Create only what is missing, then record what the run did.

        The researched flow, in order: read the live schema, compute the diff,
        ``POST`` the creates for the object and each missing property, and "record
        the mapping of room-object-id -> CRM-object-id for subsequent syncs". The
        key request is separate and last, because the research is explicit that the
        index builds in the background: a caller gets a key in ``Pending`` and an
        object that is already usable, rather than a request that blocks on an
        index.

        ``dry_run`` returns the plan and stores a run record saying it was a
        preview. It stores *that* and nothing else - no object, no property, no
        key - so a CI job can call the installer on every deploy to see what
        *would* change without a preview being able to change it.
        """
        plan = self.plan(connection_id, manifest_id, version)
        connection = self.connection(connection_id)
        record = self.manifest(manifest_id, version)
        adapter = adapter_for(connection.get("vendor"))

        blocking = plan.get("blocking_findings") or []
        if blocking:
            raise ManifestError(
                "this manifest cannot be installed as described: "
                + "; ".join(str(f["message"]) for f in blocking)
            )
        manifests.check_key(record)

        requests: list[dict[str, Any]] = [
            {"method": "GET", "path": path, "target": "schema"} for path in adapter.schema_read
        ]
        # Two counters, because a preview must be able to say both how much it
        # would have done and how much it did. Collapsing them into one number is
        # how a dry run ends up reporting "0 created" for a deploy that would have
        # added four fields, which reads as "nothing to do" rather than as "a
        # preview".
        created = 0
        applied = 0
        object_record: dict[str, Any] | None = None

        if plan["object"]["action"] == "create":
            body = dict(plan["object"]["request"] or {})
            style = body.pop("_object_id_style", adapter.object_id_style)
            requests.append(
                {
                    "method": "POST",
                    "path": adapter.object_create,
                    "target": f"object:{body.get('name')}",
                    "body": body,
                }
            )
            if dry_run:
                created += 1
            else:
                remote = self.crm.create_object(
                    connection, body | {"_object_id_style": style}, source=source, actor=actor
                )
                object_record = _data(
                    self.store.create(
                        OBJECTS,
                        {
                            "connection_id": connection["id"],
                            "vendor": adapter.vendor,
                            "room_object_id": record.get("room_object_id"),
                            # A *vendor* id, and the two researched vendors spell it
                            # differently: HubSpot's object type is a number and
                            # Dataverse's is a schema name. The remote record keeps
                            # the vendor's own type, because that is what came back;
                            # every record this workflow owns holds the string, so
                            # one comparison works everywhere and a client is never
                            # handed a JSON value whose type depends on which
                            # vendor provisioned it.
                            "crm_object_id": str(remote.get("crm_object_id")),
                            "crm_object_name": remote.get("name"),
                            "object_label": remote.get("label"),
                            "manifest_id": record.get("manifest_id"),
                            "manifest_version": record.get("version"),
                            "declared_properties": len(record.get("properties") or []),
                        },
                        room_id=connection.get("room_id"),
                        actor=actor,
                        source=source,
                    )
                )
                created += 1
                applied += 1
        elif not dry_run:
            object_record = self.object_for_connection(connection, str(plan["object"]["name"]))

        object_type = str(
            (object_record or {}).get("crm_object_id") or plan["object"].get("crm_object_id") or ""
        )

        property_records: list[dict[str, Any]] = []
        for row in plan["properties"]:
            if row["action"] != "create":
                continue
            path = adapter.property_path(object_type)
            # Recorded whether or not this is a preview. The requests are the
            # researched paths and bodies, and a preview that reported only the
            # object it would create would be telling a reviewer a third of the
            # truth about what their deploy sends.
            requests.append(
                {
                    "method": "POST",
                    "path": path,
                    "target": f"property:{row['name']}",
                    "body": row["request"],
                }
            )
            if dry_run:
                created += 1
                continue
            self.crm.create_property(
                connection, str(plan["object"]["name"]), row["request"], source=source, actor=actor
            )
            property_records.append(
                _data(
                    self.store.create(
                        PROPERTIES,
                        {
                            "connection_id": connection["id"],
                            "crm_object_id": object_type,
                            "crm_object_name": str(plan["object"]["name"]),
                            "name": row["name"],
                            "label": row["label"],
                            "type": row["type"],
                            "origin": "installed",
                            "manifest_id": record.get("manifest_id"),
                            "manifest_version": record.get("version"),
                            "request": row["request"],
                        },
                        room_id=connection.get("room_id"),
                        actor=actor,
                        source=source,
                    )
                )
            )
            created += 1
            applied += 1

        key_record: dict[str, Any] | None = None
        key_plan = plan["key"]
        if key_plan["action"] == "create":
            metadata = dict(key_plan.get("request") or {})
            requests.append(
                {
                    "method": "POST",
                    "path": str(adapter.key_create),
                    "target": f"key:{','.join(key_plan.get('columns') or [])}",
                    "body": metadata,
                }
            )
            created += 1
            if not dry_run:
                remote_key = self.crm.create_key(
                    connection, str(plan["object"]["name"]), metadata, source=source, actor=actor
                )
                key_record = _data(
                    self.store.create(
                        KEYS,
                        {
                            "connection_id": connection["id"],
                            "vendor": adapter.vendor,
                            "crm_object_id": object_type,
                            "crm_object_name": str(plan["object"]["name"]),
                            "key_id": remote_key.get("key_id"),
                            "columns": list(remote_key.get("columns") or []),
                            "status": remote_key.get("status"),
                            "async_job_id": remote_key.get("async_job_id"),
                            "polls": 0,
                            "reactivated_count": 0,
                            "remote_record_id": remote_key.get("id"),
                        },
                        room_id=connection.get("room_id"),
                        actor=actor,
                        source=source,
                    )
                )
                applied += 1
        elif not dry_run and key_plan["action"] == "unchanged":
            key_record = self.key_for_columns(connection, list(key_plan.get("columns") or []))

        skipped = [
            {"property": row["name"], "reason": row["reason"]}
            for row in plan["properties"]
            if row["action"] == "unmappable"
        ]
        conflicts = [
            {"property": row["name"], "reason": row["reason"]}
            for row in plan["properties"]
            if row["action"] == "conflict"
        ]

        if object_record is not None and skipped:
            # The object is installed with a hole in it, and that has to be visible
            # on the object itself rather than only on the run that made the hole.
            self.store.update(
                object_record["id"],
                {"complete": False, "skipped_properties": [row["property"] for row in skipped]},
                actor=actor,
                source=source,
            )
            object_record = _data(self.store.require(object_record["id"]))

        outcome = "dry_run" if dry_run else ("created" if created else "unchanged")
        run = self.store.create(
            RUNS,
            {
                "connection_id": connection["id"],
                "vendor": adapter.vendor,
                "manifest_id": record.get("manifest_id"),
                "manifest_version": record.get("version"),
                "room_object_id": record.get("room_object_id"),
                "crm_object_id": object_type or None,
                "crm_object_name": str(plan["object"]["name"]),
                "dry_run": bool(dry_run),
                "outcome": outcome,
                "counts": {
                    "created": created,
                    "applied": applied,
                    "properties": len(property_records),
                    "unchanged": plan["counts"]["unchanged"],
                    "conflicts": len(conflicts),
                    "skipped": len(skipped),
                    "left_in_place": plan["counts"]["left_in_place"],
                },
                "skipped": skipped,
                "conflicts": conflicts,
                "left_in_place": plan["left_in_place"],
                "key": {
                    "action": key_plan["action"],
                    "reason": key_plan["reason"],
                    "columns": key_plan.get("columns") or [],
                    "key_id": (key_record or {}).get("key_id"),
                    "status": (key_record or {}).get("status"),
                },
                "findings": plan["findings"],
                "advisories": plan["advisories"],
                "requests": requests,
                "plan": plan,
            },
            room_id=connection.get("room_id"),
            actor=actor,
            source=source,
        )
        return {
            "outcome": outcome,
            "dry_run": bool(dry_run),
            "created": created,
            "applied": applied,
            "complete": not skipped,
            "run": _data(run),
            "plan": plan,
            "object": object_record,
            "properties": list(property_records),
            "key": key_record,
            "mapping": {
                "room_object_id": record.get("room_object_id"),
                "crm_object_id": object_type or None,
                "crm_object_name": str(plan["object"]["name"]),
                "connection_id": connection["id"],
                "vendor": adapter.vendor,
            },
        }

    # -- objects and properties --------------------------------------------- #

    def objects(
        self, *, connection_id: str | None = None, room_id: str | None = None
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if connection_id:
            where["connection_id"] = connection_id
        # ``room_id`` is an envelope column, not a JSON path, so it cannot go
        # through ``find()``'s dynamic index; the store's own room filter is the
        # one that reaches it.
        rows = self.store.list(OBJECTS, room_id=room_id, limit=1000)
        if where:
            rows = [row for row in rows if all(row["data"].get(k) == v for k, v in where.items())]
        return sorted(
            (_data(row) for row in rows), key=lambda row: str(row.get("crm_object_name"))
        )

    def object(self, object_id: str) -> dict[str, Any]:
        record = self.store.get(object_id)
        if record is None or record["collection"] != OBJECTS:
            raise RecordNotFound(object_id)
        return _data(record)

    def object_for_connection(self, connection: Mapping[str, Any], name: str) -> dict[str, Any] | None:
        rows = self.store.find(
            OBJECTS,
            {"connection_id": connection["id"], "crm_object_name": name},
            limit=1000,
        )
        return _data(rows[0]) if rows else None

    def properties(self, object_id: str) -> list[dict[str, Any]]:
        record = self.store.require(object_id)
        if record["collection"] != OBJECTS:
            raise RecordNotFound(object_id)
        rows = self.store.find(
            PROPERTIES, {"crm_object_id": str(record["data"].get("crm_object_id"))}, limit=1000
        )
        found = [_data(row) for row in rows]
        return sorted(found, key=lambda row: str(row.get("name")))

    def add_property(
        self,
        object_id: str,
        payload: Mapping[str, Any] | None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Create one more property on an installed object, outside the installer.

        The research's extensibility claim is that adding a CRM field is a config
        change, and a field a team needs this afternoon should not have to wait for
        a new manifest version. The rules the installer holds to are the rules
        here: a property that already exists at the vendor is refused rather than
        changed, because "never destructively renaming or dropping existing
        fields" is a property of the *workflow*, not of the entry point.
        """
        record = self.store.require(object_id)
        if record["collection"] != OBJECTS:
            raise RecordNotFound(object_id)
        connection = self.connection(str(record["data"].get("connection_id")))
        adapter = adapter_for(connection.get("vendor"))
        body = dict(payload or {})

        name = str(body.get("name") or "").strip()
        if not name:
            raise ManifestError("name is required")
        prop = {
            "name": name,
            "label": str(body.get("label") or name),
            "type": str(body.get("type") or "string"),
            "group_name": str(body.get("group_name") or body.get("groupName") or ""),
            "length": body.get("length"),
            "description": str(body.get("description") or ""),
            "options": manifests.normalise_options(body.get("options")),
            "required": bool(body.get("required", False)),
        }

        # Reused rather than re-implemented: a field added by hand goes through
        # exactly the checks a manifest property goes through, so the two entry
        # points cannot disagree about what a vendor will accept.
        findings = manifests.findings_for(
            {"properties": [prop], "object": record["data"], manifests.SYNC_KEY_FIELD: None}, adapter
        )
        blocking_here = manifests.property_level(findings)
        if blocking_here:
            # A hand-added field that a vendor would refuse is the caller's to fix,
            # and the finding says which field is missing, so the message is a
            # list of those sentences rather than one generic refusal.
            raise ManifestError("; ".join(str(f["message"]) for f in blocking_here))
        if not adapter.supports(prop["type"]):
            raise ProvisioningError(
                f"{adapter.vendor} has no mapping for type {prop['type']!r} in this build; "
                "add a mapping rather than a guessed field type"
            )

        remote = self.crm.find_property(
            connection, str(record["data"].get("crm_object_name")), prop["name"]
        )
        if remote is not None:
            raise PropertyConflict(
                f"property {prop['name']!r} already exists on "
                f"{record['data'].get('crm_object_name')!r} as {remote.get('type')!r}; this "
                "workflow creates properties and never changes them"
            )

        request = schema_diff.property_request(prop, adapter)
        self.crm.create_property(
            connection, str(record["data"].get("crm_object_name")), request, source=source, actor=actor
        )
        created = self.store.create(
            PROPERTIES,
            {
                "connection_id": connection["id"],
                "crm_object_id": record["data"].get("crm_object_id"),
                "crm_object_name": record["data"].get("crm_object_name"),
                "name": prop["name"],
                "label": prop["label"],
                "type": prop["type"],
                "origin": "manual",
                "request": request,
            },
            room_id=connection.get("room_id"),
            actor=actor,
            source=source,
        )
        return _data(created)

    # -- keys ---------------------------------------------------------------- #

    def keys(
        self, *, connection_id: str | None = None, object_id: str | None = None
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if connection_id:
            where["connection_id"] = connection_id
        if object_id:
            where["crm_object_id"] = str(self.object(object_id).get("crm_object_id"))
        rows = (
            self.store.find(KEYS, where, limit=1000) if where else self.store.list(KEYS, limit=1000)
        )
        return sorted((_data(row) for row in rows), key=lambda row: str(row.get("key_id")))

    def key(self, key_id: str) -> dict[str, Any]:
        record = self.store.get(key_id)
        if record is None or record["collection"] != KEYS:
            raise RecordNotFound(key_id)
        return _data(record)

    def key_for_columns(self, connection: Mapping[str, Any], columns: list[str]) -> dict[str, Any] | None:
        rows = self.store.find(KEYS, {"connection_id": connection["id"]}, limit=1000)
        for row in rows:
            if list(_data(row).get("columns") or []) == list(columns):
                return _data(row)
        return None

    def _drive_key(self, key_id: str, action: str, *, actor: str | None, source: str) -> dict[str, Any]:
        record = self.key(key_id)
        connection = self.connection(str(record.get("connection_id")))
        if action == "poll":
            remote = self.crm.advance_key(
                connection, str(record.get("key_id")), source=source, actor=actor
            )
        else:
            remote = self.crm.reactivate_key(
                connection, str(record.get("key_id")), source=source, actor=actor
            )
        patch = {
            "status": remote.get("status"),
            "polls": remote.get("polls"),
            "async_job_id": remote.get("async_job_id"),
            "reactivated_count": remote.get("reactivated_count", record.get("reactivated_count") or 0),
            "reactivated": bool(remote.get("reactivated")),
        }
        if remote.get("status") == "Active":
            patch["activated_at"] = _now()
        updated = self.store.update(key_id, patch, actor=actor, source=source)
        return _data(updated)

    def poll_key(self, key_id: str, *, actor: str | None = None, source: str) -> dict[str, Any]:
        """One look at the background index build. The researched ``AsyncJob``."""
        return self._drive_key(key_id, "poll", actor=actor, source=source)

    def reactivate_key(self, key_id: str, *, actor: str | None = None, source: str) -> dict[str, Any]:
        """``ReactivateEntityKey``: repair a half-provisioned key.

        Idempotent in the same direction the installer is. A key whose index is
        already ``Active`` is left alone and reports ``reactivated: false``; a key
        that is still building is left building. Only a ``Failed`` key is re-armed.
        """
        return self._drive_key(key_id, "reactivate", actor=actor, source=source)

    # -- runs ---------------------------------------------------------------- #

    def runs(
        self, *, connection_id: str | None = None, outcome: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if connection_id:
            where["connection_id"] = connection_id
        if outcome:
            where["outcome"] = outcome
        rows = self.store.find(RUNS, where, limit=limit) if where else self.store.list(RUNS, limit=limit)
        return [_data(row) for row in rows]

    def run(self, run_id: str) -> dict[str, Any]:
        record = self.store.get(run_id)
        if record is None or record["collection"] != RUNS:
            raise RecordNotFound(run_id)
        return _data(record)

    # -- room-scoped reads --------------------------------------------------- #

    def room_objects(self, room_id: str) -> dict[str, Any]:
        """What this room's connections have in their CRMs.

        Provisioning is scoped to a *connection*, not to a room: the engagement
        object is one tenant-level artefact, and a room in the key would let two
        rooms in the same account install it twice under two ids. So the room
        scoping lives on the read, which is where a room genuinely appears - the
        research's own result is "the room now has a first-class, CRM-native
        object to write engagement rows into", which is a question about the room.
        """
        connections = self.connections(room_id=room_id)
        rows = self.store.list(OBJECTS, room_id=room_id, limit=1000)
        objects = sorted(
            (_data(row) for row in rows), key=lambda row: str(row.get("crm_object_name"))
        )
        by_object: dict[str, list[dict[str, Any]]] = {}
        for obj in objects:
            by_object.setdefault(str(obj.get("crm_object_id")), []).extend(self.properties(obj["id"]))
        keys = self.store.list(KEYS, room_id=room_id, limit=1000)
        return {
            "room_id": room_id,
            "connections": connections,
            "count": len(objects),
            "objects": objects,
            "properties": {key: value for key, value in by_object.items()},
            "keys": [_data(row) for row in keys],
        }

    def room_summary(self, room_id: str) -> dict[str, Any]:
        """Counts for one room, and what still needs a person.

        Every count is scoped to this room rather than to the whole collection, so
        a room's header says what happened to that room. ``needs_repair`` counts
        keys whose index is ``Failed`` and keys still building, because those are
        the two states a human is the only one who can move.
        """
        payload = self.room_objects(room_id)
        keys = payload["keys"]
        connection_ids = {c["id"] for c in payload["connections"]}
        runs = [
            _data(row)
            for row in self.store.list(RUNS, room_id=room_id, limit=1000)
            if str(row["data"].get("connection_id")) in connection_ids or not connection_ids
        ]
        connections = payload["connections"]
        return {
            "room_id": room_id,
            "connections": len(connections),
            "unsupported_connections": [c["name"] for c in connections if not c.get("supported", True)],
            "objects": payload["count"],
            "properties": sum(len(rows) for rows in payload["properties"].values()),
            "keys": len(keys),
            "keys_active": sum(1 for key in keys if key.get("status") == "Active"),
            "needs_repair": sum(1 for key in keys if key.get("status") in {"Failed", "Pending", "In Progress"}),
            "incomplete_objects": sum(1 for obj in payload["objects"] if obj.get("complete") is False),
            "installations": len(runs),
            "last_installation": runs[0] if runs else None,
        }
