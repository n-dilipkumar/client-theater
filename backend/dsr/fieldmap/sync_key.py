"""The sync key: which CRM property carries the sales room's own row id.

The research's step 4: "Admin picks the **sync key** - the CRM property that
carries the sales-room's own row id, and marks it unique so the CRM itself rejects
collisions."

Two vendors, two mechanisms, one decision:

**HubSpot** - "To create a property requiring unique values via API: 1. Make a
``POST`` request to ``/crm/properties/2026-09/{objectType}``. 2. In your request
body, for the ``hasUniqueValue`` field, set the value to ``true``." A HubSpot unique
ID property is one property, so a sync key here is exactly one, and the create body
must carry both ``type`` and ``fieldType`` because "when creating or updating
properties, both ``type`` and ``fieldType`` values are required".

**Dataverse** - an alternate key instead: "Alternate keys in Microsoft Dataverse let
you uniquely identify table rows by using business columns instead of only a GUID
primary key", over a "unique combination of columns", built from five permitted
attribute types and capped at "up to ten alternate key table definitions". So a
Dataverse sync key may name several columns, and every one of them must be an
eligible type.

**Salesforce** - the research records a gap: "Salesforce's *Object Reference* and
*Metadata API* field pages are client-rendered and unreadable via the read path, so
the Salesforce-specific half of 'create the external-ID field' is not cited." So a
Salesforce sync key can be *pinned* - an admin marks an existing field unique in
Salesforce and records it here - but this module refuses to emit a create request,
and the refusal says why rather than inventing an endpoint.

What is emitted, and what is not
--------------------------------

:func:`build_create_request` returns a **request plan**, not a call: it never opens
a socket, because the authenticated request executor is WF-034's. Each plan step
carries ``sourced``. HubSpot's is ``True`` - the endpoint, the method and the body
shape are all in this workflow's evidence. Dataverse's is ``False``: the two
``EntityDefinitions`` reads and the eligible-type list are cited here, but the
key-*creation* surface is WF-036's, and a plan that quietly presented an unsourced
URL as if it were documented would be worse than one that says it is a gap.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.db.audited import utcnow
from dsr.fieldmap.errors import InvalidSyncKey, SyncKeyCapacity, UnsupportedSyncKeyRequest
from dsr.fieldmap.metadata import Metadata, Property
from dsr.fieldmap.vocabulary import (
    HUBSPOT_CREATE_HINT,
    HUBSPOT_FIELD_TYPES_BY_TYPE,
    UNIQUE_KEY_LIMIT,
    UNIQUE_KEY_LIMIT_QUOTES,
)

#: The HubSpot create body defaults: a sync key carries an opaque id, so a string
#: presented as a plain text field is the coherent pair.
DEFAULT_KEY_TYPE = "string"
DEFAULT_KEY_FIELD_TYPE = "text"

SALESFORCE_GAP = (
    "Salesforce's external-ID field cannot be created from what this workflow cites: the "
    "research records that Salesforce's Object Reference and Metadata API field pages are "
    "client-rendered and unreadable via the read path. Pin the field you have already marked "
    "unique in Salesforce, or add the request from a primary source."
)

DATAVERSE_GAP = (
    "WF-035 cites the two EntityDefinitions reads and the alternate-key type rule, not the "
    "key-creation surface: creating a key is WF-036's territory. The step names below are this "
    "build's reading, marked sourced: false."
)


def pin(
    sync_key: Mapping[str, Any],
    metadata: Metadata,
    *,
    now: str = "",
) -> dict[str, Any]:
    """Validate a proposed sync key and return the shape that gets stored.

    Raises rather than returning findings, because pinning is a single decision an
    admin makes once: a refused pin with a list of what is wrong is slower than a
    pin that either works or says the one thing that is wrong. The full breakdown
    of what *would* be wrong is available from ``Validate mapping``, which reports
    every row at once.
    """
    raw = sync_key.get("properties")
    if isinstance(raw, str):
        properties = [part.strip() for part in raw.split(",") if part.strip()]
    elif isinstance(raw, Sequence):
        properties = [str(name).strip() for name in raw if str(name).strip()]
    else:
        properties = []
    if not properties:
        raise InvalidSyncKey(
            "the sync key needs at least one CRM property: it is the property that carries the "
            "sales room's own row id",
            findings=({"flag": "no_sync_key", "message": "no properties named"},),
        )

    if metadata.provider == "hubspot" and len(properties) > 1:
        raise InvalidSyncKey(
            "a HubSpot unique ID property is a single property, so a HubSpot sync key names one. "
            f"This one names {len(properties)}: {', '.join(properties)}.",
            findings=(
                {
                    "flag": "sync_key_too_many_properties",
                    "provider": "hubspot",
                    "properties": properties,
                    "message": "HubSpot's unique-value property is one property, not a combination",
                },
            ),
        )

    unknown: list[dict[str, Any]] = []
    ineligible: list[dict[str, Any]] = []
    for name in properties:
        prop = metadata.property(name)
        if prop is None:
            unknown.append(
                {
                    "property": name,
                    "suggestions": list(metadata.suggest(name)),
                    "message": f"the {metadata.crm_object!r} object has no property {name!r}",
                }
            )
            continue
        if metadata.provider == "dataverse" and not metadata.type_is_key_eligible(prop.value_type):
            ineligible.append(
                {
                    "property": name,
                    "attribute_type": prop.value_type,
                    "message": (
                        f"{name!r} is a {prop.value_type}; an alternate key may only be built from "
                        "Decimal, String, DateTime, Lookup or Picklist attributes"
                    ),
                }
            )
    if unknown:
        raise InvalidSyncKey(
            f"the sync key names {len(unknown)} propert(y/ies) this object does not carry",
            findings=tuple(unknown),
        )
    if ineligible:
        raise InvalidSyncKey(
            f"the sync key names {len(ineligible)} attribute(s) that cannot be in a key",
            findings=tuple(ineligible),
        )

    used = metadata.unique_key_usage
    already = sum(1 for name in properties if _already_enforced(metadata, name))
    needed = len(properties) - already
    if used + needed > UNIQUE_KEY_LIMIT:
        raise SyncKeyCapacity(
            f"pinning this key needs {needed} more unique keys and the object already holds {used} "
            f"of the {UNIQUE_KEY_LIMIT} its CRM allows",
            used=used,
            limit=UNIQUE_KEY_LIMIT,
            providers=tuple(sorted(UNIQUE_KEY_LIMIT_QUOTES)),
        )

    return {
        "properties": properties,
        "unique": bool(sync_key.get("unique", True)),
        "pinned_at": now or utcnow(),
        "provider": metadata.provider,
        "crm_object": metadata.crm_object,
        "already_enforced": already,
        "newly_enforced": needed,
        "usage": {
            "used": used,
            "needed": needed,
            "limit": UNIQUE_KEY_LIMIT,
            # What is left *after* this key is pinned, which is the number an admin
            # wants: "9 of 10 used" describes the past, "0 left" describes what
            # happens next.
            "remaining": max(0, UNIQUE_KEY_LIMIT - used - needed),
        },
    }


def _already_enforced(metadata: Metadata, name: str) -> bool:
    prop = metadata.property(name)
    return bool(prop and (prop.unique or prop.keyed))


def already_keyed(metadata: Metadata, sync_key: Mapping[str, Any]) -> bool:
    """Whether every named property is already unique or already in a key.

    The idempotence question the request builder needs: creating a property that
    exists, or an alternate key that exists, is refused by the CRM, so the plan
    says what state the object is actually in.
    """
    names = [str(name) for name in (sync_key.get("properties") or [])]
    if not names:
        return False
    return all(_already_enforced(metadata, name) for name in names)


def build_create_request(
    metadata: Metadata,
    sync_key: Mapping[str, Any],
    connection: Mapping[str, Any] | None = None,
    *,
    group_name: str = "",
    label: str = "",
    value_type: str = "",
    field_type: str = "",
) -> dict[str, Any]:
    """The vendor plan that would make the CRM enforce the key.

    Pure: it reads the recorded metadata and the pinned key, and returns a document
    describing the request. Nothing is sent, nothing is written, and the same input
    always produces the same plan - which is what makes it testable at all.

    ``group_name`` falls back to the connection's configured ``property_group``.
    When neither is set the builder **refuses**, because the cited requirement is
    that the field is present: inventing a plausible group name would produce a
    request the CRM rejects and a reader who cannot tell which half was wrong.
    """
    names = [str(name) for name in (sync_key.get("properties") or [])]
    if not names:
        raise InvalidSyncKey("no sync key is pinned, so there is no property to create")
    provider = metadata.provider
    if provider == "salesforce":
        raise UnsupportedSyncKeyRequest(
            "this workflow will not emit a Salesforce external-ID field request",
            gap=SALESFORCE_GAP,
            provider=provider,
        )

    resolved = [metadata.property(name) for name in names]
    if any(prop is None for prop in resolved):
        missing = [name for name, prop in zip(names, resolved, strict=True) if prop is None]
        raise InvalidSyncKey(
            f"the pinned sync key names {', '.join(missing)}, which the object does not carry; "
            "re-read the property metadata and re-pin"
        )

    if provider == "hubspot":
        return _hubspot_plan(
            metadata,
            names,
            first=resolved[0],
            connection=connection or {},
            group_name=group_name,
            label=label,
            value_type=value_type,
            field_type=field_type,
        )
    if provider == "dataverse":
        return _dataverse_plan(metadata, names)
    raise UnsupportedSyncKeyRequest(
        f"no request builder is defined for provider {provider!r}",
        gap="The workflow documents HubSpot and Dataverse; Salesforce's half is a recorded gap.",
        provider=str(provider),
    )


def _hubspot_plan(
    metadata: Metadata,
    names: Sequence[str],
    *,
    first: Property,
    connection: Mapping[str, Any],
    group_name: str,
    label: str,
    value_type: str,
    field_type: str,
) -> dict[str, Any]:
    """The researched HubSpot create request, with the required fields enforced.

    Both ``type`` and ``fieldType`` are required, and they mean different things,
    so the two checks are separate: an empty one is a missing field, and a pair
    that disagrees is a pair the CRM will refuse. The group is required too, which
    the research's sibling workflow states for the same create endpoint; when the
    metadata knows the object's groups, a group the object does not have is refused
    rather than sent.
    """
    resolved_type = str(value_type or DEFAULT_KEY_TYPE).strip()
    resolved_field_type = str(field_type or DEFAULT_KEY_FIELD_TYPE).strip()
    if not resolved_type or not resolved_field_type:
        raise UnsupportedSyncKeyRequest(
            "a HubSpot property create needs both `type` and `fieldType`; neither may be empty",
            gap=HUBSPOT_CREATE_HINT,
            provider="hubspot",
        )
    allowed_field_types = HUBSPOT_FIELD_TYPES_BY_TYPE.get(resolved_type)
    if allowed_field_types is None:
        raise UnsupportedSyncKeyRequest(
            f"{resolved_type!r} is not a HubSpot property type this workflow knows, so it cannot "
            f"say which fieldType goes with it. Known types: {', '.join(sorted(HUBSPOT_FIELD_TYPES_BY_TYPE))}.",
            gap=HUBSPOT_CREATE_HINT,
            provider="hubspot",
        )
    if resolved_field_type not in allowed_field_types:
        raise UnsupportedSyncKeyRequest(
            f"a {resolved_type} property is presented as a {', '.join(allowed_field_types)}, not as "
            f"a {resolved_field_type}. `type` is the property's type and `fieldType` is how it is "
            "presented; a pair that disagrees is what the CRM refuses.",
            gap=HUBSPOT_CREATE_HINT,
            provider="hubspot",
        )

    group = str(group_name or connection.get("property_group") or "").strip()
    if not group:
        raise UnsupportedSyncKeyRequest(
            "a HubSpot property create needs a `groupName`: the file to create the property in is "
            "required, and this build will not guess one",
            gap=(
                "HubSpot property creation requires groupName alongside name, label, type and "
                "fieldType. Set it on the connection or pass it with the request."
            ),
            provider="hubspot",
        )
    known_groups = metadata.groups
    if known_groups and group not in known_groups:
        raise UnsupportedSyncKeyRequest(
            f"the {metadata.crm_object!r} object has no property group {group!r}",
            gap=("Known groups on this object: " + ", ".join(known_groups) if known_groups else ""),
            provider="hubspot",
        )

    return {
        "provider": "hubspot",
        "sourced": True,
        "gap": "",
        "crm_object": metadata.crm_object,
        "already_enforced": first.unique,
        "note": (
            '"To create a property requiring unique values via API: 1. Make a POST request to '
            "/crm/properties/2026-09/{objectType}. 2. In your request body, for the hasUniqueValue "
            'field, set the value to true."'
        ),
        "steps": [
            {
                "step": 1,
                "method": "POST",
                "url": f"/crm/properties/2026-09/{metadata.crm_object}",
                "body": {
                    "groupName": group,
                    "name": str(names[0]),
                    "label": str(label or names[0]),
                    "type": resolved_type,
                    "fieldType": resolved_field_type,
                    # Not caller-controlled: the research is a single sentence - "for the
                    # hasUniqueValue field, set the value to true" - and a body that set
                    # it false would be a valid request that does not do the thing it
                    # was asked for.
                    "hasUniqueValue": True,
                },
                "sourced": True,
            }
        ],
    }


def _dataverse_plan(metadata: Metadata, names: Sequence[str]) -> dict[str, Any]:
    """The Dataverse alternate-key plan, marked as not cited in this workflow.

    Two steps, in the order WF-036's evidence gives: create an object of type
    ``EntityKeyMetadata`` with the key columns set, then call ``CreateEntityKey``
    against the table. The first URL is the standard Web API entity set; the second
    is left to the deployer because this workflow does not cite it, and a plan that
    carried a URL it could not source would be a guess dressed as a reference.
    """
    return {
        "provider": "dataverse",
        "sourced": False,
        "gap": DATAVERSE_GAP,
        "crm_object": metadata.crm_object,
        "already_enforced": all(
            bool(prop and prop.keyed) for prop in (metadata.property(name) for name in names)
        ),
        "note": (
            "Alternate keys identify table rows by business columns instead of only a GUID "
            "primary key, and are built from Decimal, String, DateTime, Lookup or Picklist "
            "attributes. A table can have up to ten alternate key table definitions."
        ),
        "steps": [
            {
                "step": 1,
                "name": "EntityKeyMetadata",
                "method": "POST",
                "url": "/api/data/v9.2/EntityKeyMetadata",
                "body": {
                    "ObjectTypeCode": metadata.crm_object,
                    "Keys": [
                        {
                            "EntityKeyIndexStatus": "Active",
                            "KeyAttributes": list(names),
                        }
                    ],
                },
                "sourced": False,
            },
            {
                "step": 2,
                "name": "CreateEntityKey",
                "method": "POST",
                "url": None,
                "url_note": (
                    "Not cited in WF-035. The key-creation call belongs to WF-036, which cites "
                    "EntityKeyMetadata and CreateEntityKey; supply the URL from your own source."
                ),
                "body": {"KeyAttributes": list(names)},
                "sourced": False,
            },
        ],
    }
