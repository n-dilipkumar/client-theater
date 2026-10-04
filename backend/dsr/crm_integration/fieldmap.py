"""Deriving the panel's read set from the same field map the writes use.

The research states this as the workflow's extensibility property: "The read set
is derived from the *same* field map as writes, so a deployment that adds a
field automatically gets it in the room's deal panel with no extra API code."

That sentence is the reason this module exists in the shape it does. A read set
compiled into :mod:`dsr.crm_integration.query` would satisfy every example in the
research and fail the claim, because a deployment that added one field to its
write map would get a panel missing it. So nothing here holds a list of columns.
The only list is :data:`DEFAULT_FIELD_MAP`, which is the starting point a room
overrides, and the read set is computed from whatever map it is handed.

The shape is ``{object: {crm_field: room_field}}``. The outer key is one of
:data:`dsr.crm_integration.vocabulary.CRM_OBJECTS`, so a field map for an object
the panel does not read is a finding rather than a silent no-op.
"""

from __future__ import annotations

from typing import Any

from dsr.crm_integration import vocabulary
from dsr.crm_integration.errors import EmptyReadSet, FieldMapError, FieldNotMapped

#: The fields the user flow names, as a starting point per vendor:
#: "the room needs deal name, stage, amount, primary contact, account industry",
#: which is one field from each of the three objects plus two more on the deal.
#:
#: Salesforce spells its columns ``StageName`` and ``Amount``; Dataverse spells
#: them ``stepname`` and ``estimatedvalue``; HubSpot spells them ``dealstage`` and
#: ``amount``. The room field on the right is the same in all three, which is the
#: point of a field map: the panel renders one shape across three vendors.
DEFAULT_FIELD_MAP: dict[str, dict[str, dict[str, str]]] = {
    "salesforce": {
        "deal": {
            "Name": "deal_name",
            "StageName": "stage",
            "Amount": "amount",
            "CloseDate": "close_date",
            "Probability": "probability",
        },
        "contact": {
            "Name": "contact_name",
            "Title": "contact_title",
            "Email": "contact_email",
        },
        "account": {
            "Name": "account_name",
            "Industry": "account_industry",
            "BillingCountry": "account_country",
        },
    },
    "dataverse": {
        "deal": {
            "name": "deal_name",
            "stepname": "stage",
            "estimatedvalue": "amount",
            "estimatedclosedate": "close_date",
        },
        "contact": {
            "fullname": "contact_name",
            "jobtitle": "contact_title",
            "emailaddress1": "contact_email",
        },
        "account": {
            "name": "account_name",
            "industrycode": "account_industry",
            "address1_country": "account_country",
        },
    },
    "hubspot": {
        "deal": {
            "dealname": "deal_name",
            "dealstage": "stage",
            "amount": "amount",
            "closedate": "close_date",
        },
        "contact": {
            "email": "contact_email",
            "jobtitle": "contact_title",
            "phone": "contact_phone",
        },
        "account": {
            "name": "account_name",
            "industry": "account_industry",
            "country": "account_country",
        },
    },
}


def default_field_map(system: str) -> dict[str, dict[str, str]]:
    """The starting point for one vendor, deep-copied so a caller may edit it."""
    return {
        object_name: dict(mapping)
        for object_name, mapping in DEFAULT_FIELD_MAP[vocabulary.require_system(system)].items()
    }


def normalise_field_map(raw: Any, system: str | None = None) -> dict[str, dict[str, str]]:
    """Validate a submitted field map and return it in canonical shape.

    Rejects a non-object, an object key outside
    :data:`~dsr.crm_integration.vocabulary.CRM_OBJECTS`, and a column mapping
    whose value is not a string. Accepts a partial map: a room that reads only
    the deal and the account is a legitimate deployment, and forcing three
    objects on it would be this build inventing a requirement.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise FieldMapError("field_map must be an object keyed by object name")
    result: dict[str, dict[str, str]] = {}
    for key, mapping in raw.items():
        object_name = vocabulary.require_object(str(key))
        if not isinstance(mapping, dict):
            raise FieldMapError(f"field_map.{key} must be an object of column to room field")
        columns: dict[str, str] = {}
        for column, room_field in mapping.items():
            column_name = str(column or "").strip()
            field_name = str(room_field or "").strip()
            if not column_name or not field_name:
                raise FieldMapError(
                    f"field_map.{key} has an entry with a blank column or room field: "
                    f"{column!r} to {room_field!r}"
                )
            columns[column_name] = field_name
        if columns:
            result[object_name] = columns
    if system is not None:
        # Naming the vendor here only validates the vendor. The map itself is
        # vendor-independent, which is what lets one map answer for three.
        vocabulary.require_system(system)
    return result


def read_set(field_map: dict[str, dict[str, str]], system: str, object_name: str) -> list[str]:
    """The columns one read selects, id first, in map order.

    The id column is prepended because the room has to be able to name the row it
    read and to ask the vendor for the next page keyed by it. It is not a field
    the map carries, so prepending it is the one place this function adds a
    column, and it is a column every vendor returns whether or not it is asked.
    """
    target = vocabulary.require_object(object_name)
    vendor = vocabulary.require_system(system)
    columns = (field_map or {}).get(target) or {}
    if not columns:
        raise EmptyReadSet(
            f"the field map selects no column for the {target} on {vendor}. A read with "
            "no columns returns nothing, and returning nothing with a 200 is "
            "indistinguishable from a room whose CRM has no deal."
        )
    selected = [vocabulary.ID_FIELDS[vendor]]
    for column in columns:
        if column not in selected:
            selected.append(column)
    return selected


def room_fields(field_map: dict[str, dict[str, str]], object_name: str) -> list[str]:
    """The room-side field names one object contributes, in map order."""
    target = vocabulary.require_object(object_name)
    seen: list[str] = []
    for name in (field_map or {}).get(target, {}).values():
        if name not in seen:
            seen.append(name)
    return seen


def require_mapped(field_map: dict[str, dict[str, str]], object_name: str, field: str) -> str:
    """The CRM column behind one room field, or refuse with the field named.

    Used by the panel's own five fields. A room whose map does not carry
    ``deal_name`` has a gap the operator has to close, and the refusal names the
    object and the field so it is actionable without reading a diff.
    """
    target = vocabulary.require_object(object_name)
    for column, name in (field_map or {}).get(target, {}).items():
        if name == field:
            return column
    raise FieldNotMapped(
        f"the field map does not carry {field!r} on the {target}. Add it to the "
        f"identity's field_map.{target}; the read set then carries it with no "
        "change to this API."
    )


def unmapped_columns(
    field_map: dict[str, dict[str, str]],
    object_name: str,
    returned: list[str],
) -> list[str]:
    """Columns a vendor returned that this room's map does not carry.

    The honest complement of a field-scoped read: the vendor returned them, this
    read did not ask for them, and a panel that hid the fact would look like a
    deployment with fewer fields than it has.
    """
    target = vocabulary.require_object(object_name)
    known = set((field_map or {}).get(target, {}))
    return sorted(name for name in returned if name not in known)


def panel_fields(field_map: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """The whole read set as data, so a client can render the panel's columns."""
    rows: list[dict[str, Any]] = []
    for object_name in vocabulary.CRM_OBJECTS:
        for column, field in (field_map or {}).get(object_name, {}).items():
            rows.append({"object": object_name, "crm_field": column, "room_field": field})
    return rows


def findings(field_map: dict[str, dict[str, str]], system: str) -> list[str]:
    """What a map does not cover, as sentences.

    Served next to the map rather than only logged. A deployment whose map leaves
    one of the three objects empty has a panel with a hole in it, and the operator
    can only close that hole by being told which object it is.

    An object whose columns are all free text is *not* a finding. Every seller
    spells a company name in free text, and a report that called that a gap would
    train people to ignore it.
    """
    vendor = vocabulary.require_system(system)
    notes: list[str] = []
    for object_name in vocabulary.CRM_OBJECTS:
        if not (field_map or {}).get(object_name):
            notes.append(
                f"the field map selects no column for the {object_name} on {vendor}; "
                "the panel will leave that object empty"
            )
    return notes


#: The room fields whose values are option codes rather than free text. Only these
#: are worth a display label, so this is what :mod:`dsr.crm_integration.labels`
#: offers to label.
LABELLED_ROOM_FIELDS: frozenset[str] = frozenset(
    {"stage", "status", "industry", "account_industry", "reason", "type", "currency"}
)


__all__ = [
    "DEFAULT_FIELD_MAP",
    "LABELLED_ROOM_FIELDS",
    "default_field_map",
    "findings",
    "normalise_field_map",
    "panel_fields",
    "read_set",
    "require_mapped",
    "room_fields",
    "unmapped_columns",
]
