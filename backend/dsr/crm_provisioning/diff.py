"""The schema diff: what is missing, what is already right, and what is left alone.

This is step two and step three of the researched flow, and the two steps are one
function because separating them would create the exact bug the research is
warning about. The flow reads:

  2. "The sales room compares its own object definition against the CRM's live
      schema ... to list what already exists."
  3. "The sales room **creates only what is missing** - the engagement object plus
      each property - never destructively renaming or dropping existing fields."

A plan is the only thing this package ever acts on. :func:`compute` reads and
returns; it never writes. There is exactly one writer -
:func:`dsr.crm_provisioning.engine.ProvisioningEngine.install` - and it applies a
plan that was computed from state read at that moment, so the set of create
requests is a decision someone can read before it happens. That is the
"dry-run diff view" the research lists among the surfaces in play, and it is a
read rather than a mode flag, because a flag is one boolean away from a bug.

Three decisions are the workflow's whole character:

**A property that exists and matches is ``unchanged`` and no request is built for
it.** That is idempotency, spelled out rather than asserted.

**A property that exists and differs is ``conflict``, and it is never changed.**
Not "changed with a warning", not "changed if the change is additive" - the
research says "never destructively renaming or dropping existing fields", and the
only way a build honours that sentence is for no code path to reach an update. A
tenant who widened a field by hand in the CRM must not have this workflow narrow
it back on the next deploy, and a tenant who hand-made a field the manifest also
declares must get a row in the diff saying so rather than a silent overwrite.

**A property the manifest no longer declares is ``left_in_place``, and is never
dropped.** A deploy that removes a field from the manifest is the most likely way
to destroy a tenant's data by accident, so the diff names the field and stops.
The symmetric case - a manifest that *renames* a field - is the same thing seen
from the other side: the new name is missing and gets created, the old name is
left in place, and the diff says both, so a human can see that a rename happened
before deciding whether the old column should ever go.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.crm_provisioning.manifest import SYNC_KEY_FIELD, property_level
from dsr.crm_provisioning.vendors import VendorAdapter
from dsr.crm_provisioning.vocabulary import (
    KEY_ACTIONS,
    OBJECT_ACTIONS,
    PROPERTY_ACTIONS,
)

__all__ = [
    "KEY_ACTIONS",
    "OBJECT_ACTIONS",
    "compute",
    "key_metadata",
    "object_request",
    "property_request",
]


def _camel(name: str) -> str:
    return "".join(part.capitalize() for part in str(name).split("_") if part)


def property_request(prop: Mapping[str, Any], adapter: VendorAdapter) -> dict[str, Any]:
    """The vendor's create-property body for one manifest property.

    The keys are the vendor's own, from the research:

    * HubSpot - "``groupName`` ... ``name``: the internal name of the property
      ... ``label`` ... ``type``: the type of property. ``fieldType``: the field
      type."
    * Dataverse - a column is an attribute of a table definition, so the internal
      name rides on ``schemaName``, the display name on ``AttributeDisplayName``,
      and the body's ``AttributeType`` is the researched attribute metadata name.

    Everything beyond the required fields is additive and vendor-appropriate, so
    a manifest can carry a description, an option set and a length without any of
    them being unknown to the API.
    """
    typed = adapter.typed(str(prop.get("type") or "string"))
    body: dict[str, Any] = {
        adapter.name_field: prop["name"],
        adapter.label_field: prop.get("label") or prop["name"],
    }
    body.update(typed)
    if prop.get("group_name"):
        body["groupName" if adapter.vendor == "hubspot" else "AttributeGroupName"] = prop[
            "group_name"
        ]
    if prop.get("description"):
        body["description" if adapter.vendor == "hubspot" else "AttributeDescription"] = prop[
            "description"
        ]
    if prop.get("length") and prop.get("type") == "string":
        body["MaxLength"] = int(prop["length"])
    if prop.get("options"):
        body["options" if adapter.vendor == "hubspot" else "OptionSet"] = [
            {"label": option["label"], "value": option["value"]} for option in prop["options"]
        ]
    if prop.get("required"):
        body["isRequired" if adapter.vendor == "hubspot" else "IsRequired"] = True
    return body


def object_request(manifest: Mapping[str, Any], adapter: VendorAdapter) -> dict[str, Any]:
    """The vendor's create-object body.

    ``_object_id_style`` is stripped by the engine before the request is recorded
    on the run; it tells the simulated vendor what shape of id to mint, and it is
    not something a CRM accepts in a body.
    """
    obj = manifest.get("object") or {}
    body: dict[str, Any] = {
        "name": obj.get("name"),
        "label": obj.get("label"),
        "description": obj.get("description") or "",
        "_object_id_style": adapter.object_id_style,
    }
    if adapter.vendor == "dataverse":
        # Dataverse names a table by its schema name and carries no display label
        # at the table level, so the label is not invented into the body.
        body.pop("label")
    return body


def key_metadata(manifest: Mapping[str, Any], adapter: VendorAdapter) -> dict[str, Any]:
    """The ``EntityKeyMetadata`` body for the declared sync key.

    "To define alternate keys programmatically, first create an object of type
    ``EntityKeyMetadata`` ... After you set the key columns, use
    ``CreateEntityKey`` to create the keys for a table." So the body is the
    metadata object with its ``KeyAttributes`` set to the declared columns, and
    the two together are one request.
    """
    sync_key = manifest.get(SYNC_KEY_FIELD) or {}
    columns = [column["name"] for column in (sync_key.get("columns") or [])]
    return {
        "EntityTypeName": (manifest.get("object") or {}).get("name"),
        "KeyAttributes": columns,
        "AlternateKey": _camel("_".join(columns)) if columns else "",
    }


def _matches(existing: Mapping[str, Any], prop: Mapping[str, Any], adapter: VendorAdapter) -> bool:
    """Whether an existing CRM property is exactly what the manifest declares.

    Compared on the pair the vendor compares on - the neutral type it was created
    as, and the field type it renders as - plus the label, because a label is the
    one thing a person typed. A length difference is deliberately *not* a
    mismatch: widening a string column is something a tenant does to stop losing
    data, and this workflow has no way to widen one either, so calling it a
    conflict on every deploy would be noise about something nobody can act on.
    It is reported as an ``advisory`` finding instead, which is where a fact
    belongs when no one can act on it.
    """
    if str(existing.get("label") or "") != str(prop.get("label") or prop["name"]):
        return False
    existing_type = str(existing.get("type") or "")
    wanted = adapter.typed(str(prop.get("type") or "string"))
    wanted_type = str(wanted.get("type") or wanted.get("AttributeType") or "")
    return existing_type == wanted_type


def _width_finding(existing: Mapping[str, Any], prop: Mapping[str, Any]) -> dict[str, Any] | None:
    declared = prop.get("length")
    actual = existing.get("length")
    if prop.get("type") != "string" or not declared or not actual:
        return None
    if int(actual) >= int(declared):
        return None
    return {
        "severity": "advisory",
        "code": "narrower_in_crm",
        "message": (
            f"property {prop['name']!r} is declared at {declared} characters and the "
            f"CRM column is {actual}; this workflow cannot widen a column, so the "
            "narrower value is left as it is"
        ),
        "path": f"properties.{prop['name']}.length",
        "property": prop["name"],
    }


def compute(
    *,
    connection: Mapping[str, Any],
    manifest: Mapping[str, Any],
    adapter: VendorAdapter,
    findings: Sequence[Mapping[str, Any]] = (),
    objects: Sequence[Mapping[str, Any]] = (),
    properties: Sequence[Mapping[str, Any]] = (),
    keys: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Read the CRM's live schema and say exactly what an install would do.

    Pure: it takes the live schema as arguments and returns a plan. Nothing here
    writes, which is what makes the dry-run view a read and not a mode.
    """
    skipped = {f["property"] for f in property_level(list(findings)) if f.get("property")}

    object_name = str((manifest.get("object") or {}).get("name") or "")
    existing_object = next((o for o in objects if o.get("name") == object_name), None)
    object_action = "unchanged" if existing_object else "create"
    object_type = str((existing_object or {}).get("crm_object_id") or "")

    by_name = {str(p.get("name")): p for p in properties}
    plans: list[dict[str, Any]] = []
    advisories: list[dict[str, Any]] = []

    for prop in manifest.get("properties") or []:
        name = prop["name"]
        existing = by_name.get(name)
        entry: dict[str, Any] = {
            "name": name,
            "label": prop.get("label") or name,
            "type": prop.get("type"),
            "group_name": prop.get("group_name") or "",
            "existing": existing,
        }
        if name in skipped:
            entry.update(
                action="unmappable",
                reason="this build cannot create it for this vendor; see the findings",
                request=None,
            )
        elif not adapter.supports(str(prop.get("type") or "")):
            entry.update(
                action="unmappable",
                reason=f"no {adapter.vendor} mapping for type {prop.get('type')!r}",
                request=None,
            )
        elif existing is not None:
            if _matches(existing, prop, adapter):
                entry.update(
                    action="unchanged", reason="already present and identical", request=None
                )
                width = _width_finding(existing, prop)
                if width is not None:
                    advisories.append(width)
            else:
                entry.update(
                    action="conflict",
                    reason=(
                        f"the CRM has it as {existing.get('type')!r}"
                        f"/{existing.get('field_type') or '-'} labelled "
                        f"{existing.get('label')!r}; this workflow never changes an "
                        "existing property"
                    ),
                    request=None,
                )
        else:
            entry.update(
                action="create",
                reason="missing from the CRM",
                request=property_request(prop, adapter),
            )
        plans.append(entry)

    declared = {str(p.get("name")) for p in manifest.get("properties") or []}
    orphans = [
        {
            "name": str(p.get("name")),
            "label": p.get("label"),
            "action": "left_in_place",
            "reason": "the manifest no longer declares it; this workflow never drops a field",
        }
        for p in properties
        if str(p.get("name")) not in declared
    ]

    sync_key = manifest.get(SYNC_KEY_FIELD)
    key_plan: dict[str, Any]
    if not sync_key:
        key_plan = {
            "action": "absent",
            "reason": "the manifest declares no sync key",
            "columns": [],
        }
    elif not adapter.key_create:
        key_plan = {
            "action": "unsupported",
            "reason": (
                f"{adapter.vendor} has no sourced alternate-key call in this build; the "
                "key request is reported, not sent"
            ),
            "columns": [c["name"] for c in sync_key.get("columns") or []],
        }
    else:
        wanted = [c["name"] for c in sync_key.get("columns") or []]
        found = next((k for k in keys if list(k.get("columns") or []) == wanted), None)
        if found is None:
            key_plan = {
                "action": "create",
                "reason": "the declared key is not on the CRM object yet",
                "columns": wanted,
                "key_id": None,
                "request": key_metadata(manifest, adapter),
            }
        else:
            key_plan = {
                "action": "unchanged" if str(found.get("status")) == "Active" else "failed",
                "reason": (
                    f"the key exists and its index is {found.get('status')}"
                    if str(found.get("status")) == "Active"
                    else f"the key exists but its index is {found.get('status')}"
                ),
                "columns": wanted,
                "key_id": found.get("key_id"),
                "status": found.get("status"),
                "request": None,
            }

    counts = {
        action: sum(1 for row in plans if row["action"] == action) for action in PROPERTY_ACTIONS
    }
    counts["left_in_place"] = len(orphans)
    return {
        "vendor": adapter.vendor,
        "connection_id": connection.get("id"),
        "manifest_id": manifest.get("manifest_id"),
        "manifest_version": manifest.get("version"),
        "room_object_id": manifest.get("room_object_id"),
        "object": {
            "name": object_name,
            "label": (manifest.get("object") or {}).get("label"),
            "action": object_action,
            "crm_object_id": object_type or None,
            "request": object_request(manifest, adapter) if object_action == "create" else None,
        },
        "properties": plans,
        "left_in_place": orphans,
        "key": key_plan,
        "counts": counts,
        "findings": [dict(f) for f in findings],
        "advisories": advisories,
        "will_create": 1 if object_action == "create" else 0,
        "complete": counts["unmappable"] == 0
        and not [f for f in findings if f.get("severity") == "blocking"],
    }
