"""Display labels: the vendor's annotation when it has one, the room's map when it does not.

User-flow step five: "Room requests the *display labels* for option columns
(stage, status) so the panel shows 'Proposal sent', not the integer ``2``."

And the extensibility rule that makes it a rule rather than a special case:
"Vendors expose a capability flag for 'display-label annotations' that the room
uses when available and falls back to its own option-set map when not."

So there are two label sources and a check in front of them, never a dependency.
A room on Salesforce or HubSpot has no vendor annotation to request, and its
panel still has to say "Proposal sent" - so the option-set map below is a working
part of the read path, not a degradation notice.

An option-set record is ``{system, object, field, values}``. ``field`` is the
*room* field name rather than the vendor column, because the option set is the
room's own vocabulary: it has to mean the same thing whichever vendor spelled the
column.
"""

from __future__ import annotations

from typing import Any

from dsr.crm_integration import vocabulary
from dsr.crm_integration.errors import DuplicateOptionSet, UnknownOptionSet
from dsr.store import RecordStore

#: The collection the room's own option sets live in. Owned by this feature.
OPTION_SETS = "crm_read_option_set"

#: The room fields this module offers to label. A field outside this set is
#: refused, because an option set on a free-text field is a value that can never
#: be right.
LABELLABLE_FIELDS: tuple[str, ...] = ("stage", "status", "industry", "account_industry")


def normalise(payload: dict[str, Any]) -> dict[str, Any]:
    """Clean a submitted option set.

    Keys are stringified because the vendor's option values arrive as integers
    on one system and as strings on another, and a lookup that missed because the
    map stored ``2`` and the record carried ``"2"`` would show the raw value on
    exactly the rows the feature exists to improve.
    """
    system = vocabulary.require_system(payload.get("system") or "")
    object_name = vocabulary.require_object(payload.get("object") or "")
    field = str(payload.get("field") or "").strip()
    if field not in LABELLABLE_FIELDS:
        raise UnknownOptionSet(
            f"{field!r} is not a field this workflow labels. Choose one of "
            f"{', '.join(LABELLABLE_FIELDS)}."
        )
    values = payload.get("values") or {}
    if not isinstance(values, dict) or not values:
        raise UnknownOptionSet(
            f"the option set for {object_name}.{field} needs a non-empty values object"
        )
    data: dict[str, Any] = {
        "system": system,
        "object": object_name,
        "field": field,
        "values": {str(key): str(item) for key, item in values.items()},
    }
    for key, value in payload.items():
        if key not in data:
            data[key] = value
    return data


def option_sets(
    store: RecordStore,
    system: str | None = None,
    room_id: str | None = None,
) -> list[dict[str, Any]]:
    """Every registered option set, optionally narrowed to one vendor and one room.

    A room-scoped read sees its own option sets *and* the global ones. An option
    set registered with no room is the product's shared vocabulary - "Proposal
    sent" means the same thing to every seller - and a room that had to register
    its own copy of the same map before its panel could read well would make the
    fallback optional in practice, which is the opposite of what the research
    describes.
    """
    vendor = vocabulary.require_system(system) if system else None
    found = store.list(OPTION_SETS, room_id=None, limit=1000, order_by="created_at")
    return [
        row
        for row in found
        if (vendor is None or row["data"].get("system") == vendor)
        and (room_id is None or row.get("room_id") in (None, room_id))
    ]


def option_set(
    store: RecordStore,
    system: str,
    object_name: str,
    field: str,
    room_id: str | None = None,
) -> dict[str, Any]:
    """The option set for one field, or refuse.

    Refusing rather than returning nothing, because a caller that asked for a
    specific option set and got a silent ``None`` would treat the field as
    unlabelable, which is a different claim from "you have not registered one".
    """
    vendor = vocabulary.require_system(system)
    target = vocabulary.require_object(object_name)
    name = str(field or "").strip()
    for record in option_sets(store, vendor, room_id=room_id):
        data = record["data"]
        if data.get("object") == target and data.get("field") == name:
            return record
    raise UnknownOptionSet(
        f"no option set is registered for {vendor}.{target}.{name}. Register one so the "
        "panel can show a label instead of the stored value."
    )


def check_duplicate(
    store: RecordStore,
    data: dict[str, Any],
    room_id: str | None = None,
) -> None:
    """Refuse a second option set for one field on one object, at one scope.

    The fallback is consulted when the vendor cannot annotate, so two maps at the
    same scope would make two different labels both correct for one stored value,
    and which one the panel showed would depend on row order.

    Scoped exactly, not inherited: a room may register its own wording over a
    global map, because "Proposal sent" is a phrase the seller chose and a seller
    who calls it "Proposal issued" has not made a mistake. The room's own map wins,
    because :func:`fallback_labels` merges the global rows before the room's.
    """
    for record in option_sets(store, data.get("system")):
        data_other = record["data"]
        if record.get("room_id") != room_id:
            continue
        if data_other.get("object") == data.get("object") and data_other.get("field") == data.get(
            "field"
        ):
            raise DuplicateOptionSet(
                f"an option set for {data.get('object')}.{data.get('field')} is already "
                f"registered (id {record['id']}). Patch it instead of registering a second."
            )


def label_for(values: dict[str, str], value: Any) -> str:
    """The label for one stored option value, or an empty string.

    Matched on the string form on both sides, because one vendor's option values
    are integers and another's are strings, and a panel that labelled half its
    rows would be the exact failure the annotation exists to prevent.
    """
    if value is None:
        return ""
    return str(values.get(str(value), ""))


def fallback_labels(
    store: RecordStore,
    system: str,
    object_name: str,
    room_id: str | None = None,
) -> dict[str, str]:
    """The room's own labels for one object, as ``{room_field: {value: label}}``.

    Empty when nothing is registered. An empty map is not an error: it is what a
    room with no option sets has, and the panel then shows the stored values and
    says so through :func:`unlabelled`.

    Sorted so the global rows apply **first** and the room's own rows last, which
    is what makes a room's wording win over the shared default. Relying on the
    store's own order would put the answer at the mercy of which row was written
    last, and a room whose seller chose different wording would silently get the
    shared one back.
    """
    vendor = vocabulary.require_system(system)
    target = vocabulary.require_object(object_name)
    rows = sorted(
        option_sets(store, vendor, room_id=room_id),
        key=lambda row: (row.get("room_id") is not None, row.get("created_at") or ""),
    )
    collected: dict[str, dict[str, str]] = {}
    for record in rows:
        data = record["data"]
        if data.get("object") != target:
            continue
        collected.setdefault(str(data.get("field")), {}).update(data.get("values") or {})
    return collected


def apply_fallback(
    records: list[dict[str, Any]],
    labels: dict[str, dict[str, str]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fill in the labels the vendor did not supply. Returns the records and the fills.

    Only a field with **no** label from the vendor is filled. A vendor that
    annotates is authoritative: its label comes from the org the seller actually
    uses, and the room's map is a fallback the research describes as a fallback.
    """
    fills: list[str] = []
    for record in records:
        for field, values in labels.items():
            if record["labels"].get(field):
                continue
            if field not in record["fields"]:
                continue
            found = label_for(values, record["fields"].get(field))
            if found:
                record["labels"][field] = found
                fills.append(field)
    return records, fills


def unlabelled(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Which option fields are still showing a raw value, and on how many rows.

    This is the room's own answer to "does the panel read well here", and it is
    served rather than asserted. A panel showing ``2`` because nothing could label
    it is legible to the seller who wrote the CRM and not to the buyer, and the
    difference is one row of a report away from being fixable.
    """
    counted: dict[str, int] = {}
    for record in records:
        for field in record["fields"]:
            if field in LABELLABLE_FIELDS and not record["labels"].get(field):
                counted[field] = counted.get(field, 0) + 1
    return [{"field": field, "rows": rows} for field, rows in sorted(counted.items())]


def capability(system: str) -> dict[str, Any]:
    """The capability flag for one vendor, and what happens when it is false."""
    vendor = vocabulary.require_system(system)
    supported = vocabulary.supports_display_labels(vendor)
    return {
        "system": vendor,
        "display_label_annotations": supported,
        "preference_header": (
            {"Prefer": vocabulary.DATAVERSE_DISPLAY_PREFERENCE} if supported else {}
        ),
        "annotation": vocabulary.DATAVERSE_DISPLAY_ANNOTATION if supported else None,
        "fallback": "room option sets" if not supported else "room option sets, unused",
        "labellable_fields": list(LABELLABLE_FIELDS),
        "why": (
            "the vendor annotates a stored option value with its label when the room "
            "asks, so the panel reads the label"
            if supported
            else "the vendor publishes no display-label annotation, so the panel uses the "
            "room's own option-set map"
        ),
    }


__all__ = [
    "LABELLABLE_FIELDS",
    "OPTION_SETS",
    "apply_fallback",
    "capability",
    "check_duplicate",
    "fallback_labels",
    "label_for",
    "normalise",
    "option_set",
    "option_sets",
    "unlabelled",
]
