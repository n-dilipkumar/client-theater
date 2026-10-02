"""The field map: how a CRM record becomes a room replica row.

The research's data flow names the transformation once - "Transformation: event
payload is mapped through the same field map; enriched fields fill the
'unchanged but needed' gaps" - and the user flow names the field that matters
once: "If the room needs an unchanged field (e.g. the external ID) to resolve the
record, that field is added as an enriched field on the channel."

Those two sentences together are the whole of this module. A field map is a
sync key plus a set of named mappings, and the sync key is the one mapping that
is allowed to be missing from a payload because enrichment can supply it. Every
other mapping is allowed to be missing because a field that did not change is not
in an update event - the room keeps the value it already has rather than clearing
it, which is the behaviour the word "unchanged" in the research's own sentence
describes.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.change_stream.errors import FieldMapError

#: The replica field a sync key lands in when the map does not say. Chosen to
#: match the research's own example ("the external ID") rather than to be clever.
DEFAULT_SYNC_KEY_FIELD = "external_id"


def normalise_field_map(raw: Any) -> dict[str, Any]:
    """A field map in the shape this package applies it with.

    A map is::

        {
          "sync_key": "External_Id__c",          # the CRM field that resolves a record
          "sync_key_field": "external_id",      # optional; where it lands on the replica
          "account_field": "Account_Name__c",   # optional; which buyer's panel to refresh
          "fields": {"StageName": "stage", ...} # CRM field -> replica field
        }

    ``fields`` is a flat object of CRM field names to room field names. Flat, not
    nested, because the research's transformation is a mapping between two sets
    of scalar field names and inventing a nesting rule would add a concept the
    research does not have.
    """
    if raw is None:
        raise FieldMapError("a channel needs a field_map before it can stream anything")
    if not isinstance(raw, Mapping):
        raise FieldMapError(f"field_map must be a JSON object; got {type(raw).__name__}")

    fields_raw = raw.get("fields")
    if not isinstance(fields_raw, Mapping) or not fields_raw:
        raise FieldMapError(
            "field_map.fields must be a non-empty object of CRM field name -> room field name"
        )

    fields: dict[str, str] = {}
    for crm_field, room_field in fields_raw.items():
        name = str(crm_field).strip()
        target = str(room_field or "").strip()
        if not name:
            raise FieldMapError("field_map.fields has an empty CRM field name")
        if not target:
            raise FieldMapError(
                f"field_map.fields[{name!r}] has no room field name; a mapping with no target "
                "cannot be applied"
            )
        fields[name] = target

    sync_key = str(raw.get("sync_key") or "").strip()
    if not sync_key:
        raise FieldMapError(
            "field_map.sync_key is the CRM field that resolves a record - the research's "
            '"e.g. the external ID" - and a map without it cannot identify what changed'
        )

    account_field = str(raw.get("account_field") or "").strip() or None
    sync_key_field = str(raw.get("sync_key_field") or "").strip() or DEFAULT_SYNC_KEY_FIELD

    return {
        "sync_key": sync_key,
        "sync_key_field": sync_key_field,
        "account_field": account_field,
        "fields": fields,
    }


def field_map_findings(raw: Any) -> list[dict[str, Any]]:
    """Everything questionable about a proposed field map, as a list.

    Reported rather than raised, because the page shows them next to the map
    instead of refusing it. Two of them are worth surfacing loudly:

    * a sync key that is not in ``fields`` - it still works, because the sync key
      lands in its own field, but a reader looking at ``fields`` will not see the
      one mapping that resolves records;
    * a field map that names no CRM field at all for some entity's important
      values, which is not detectable here and is therefore not claimed.

    The one thing this does refuse is a map that cannot work at all, and
    :func:`normalise_field_map` does that.
    """
    findings: list[dict[str, Any]] = []
    if not isinstance(raw, Mapping):
        return [
            {
                "code": "field_map_not_an_object",
                "severity": "error",
                "detail": (f"field_map must be a JSON object; got {type(raw).__name__}"),
            }
        ]

    fields = raw.get("fields")
    if not isinstance(fields, Mapping) or not fields:
        return [
            {
                "code": "field_map_fields_missing",
                "severity": "error",
                "detail": (
                    "field_map.fields must be a non-empty object of CRM field name -> room field name"
                ),
            }
        ]

    sync_key = str(raw.get("sync_key") or "").strip()
    if not sync_key:
        findings.append(
            {
                "code": "sync_key_missing",
                "severity": "error",
                "detail": (
                    "field_map.sync_key is the CRM field that resolves a record; without it an "
                    "update event cannot be matched to a replica row."
                ),
            }
        )
    elif sync_key not in fields:
        findings.append(
            {
                "code": "sync_key_not_in_fields",
                "severity": "info",
                "detail": (
                    f"sync_key {sync_key!r} is not in field_map.fields. It still resolves records - "
                    f"it lands in {raw.get('sync_key_field') or DEFAULT_SYNC_KEY_FIELD!r} - but a "
                    "reader scanning fields will not see the mapping that matters most."
                ),
            }
        )

    if not str(raw.get("account_field") or "").strip():
        findings.append(
            {
                "code": "account_field_missing",
                "severity": "info",
                "detail": (
                    "No account_field, so a committed change refreshes the deal panel without "
                    "naming which buyer it belongs to. The invalidation records resolved: false "
                    "rather than guessing."
                ),
            }
        )
    return findings


def resolve_external_id(
    payload: Mapping[str, Any],
    enriched: Mapping[str, Any],
    field_map: Mapping[str, Any],
) -> str | None:
    """The CRM record's identity, from the payload first and enrichment second.

    Payload before enrichment, deliberately. An update event carries what
    changed; if the sync key itself changed then the *new* value in the payload is
    the one the room should resolve on, and preferring the enriched copy would
    point at the row that used to exist.
    """
    sync_key = str(field_map.get("sync_key") or "")
    if not sync_key:
        return None
    for source in (payload, enriched):
        value = source.get(sync_key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def sync_key_field(field_map: Mapping[str, Any]) -> str:
    """The replica field the sync key is written to."""
    return str(field_map.get("sync_key_field") or DEFAULT_SYNC_KEY_FIELD)


def apply_field_map(
    payload: Mapping[str, Any],
    enriched: Mapping[str, Any],
    field_map: Mapping[str, Any],
    *,
    changed_fields: list[str] | None,
) -> dict[str, Any]:
    """The room fields this event writes, and the CRM fields it read them from.

    ``changed_fields`` is the research's ``changedFields``, and ``None`` means the
    transport did not send one - in which case every mapped field the payload
    carries is applied. An empty list means the transport said nothing changed,
    and is honoured as such.

    This is where the "unchanged but needed" gap closes: a field the map names
    but the event did not change contributes nothing here, so the replica keeps
    the value it already had. Nothing is cleared.
    """
    fields: Mapping[str, str] = field_map.get("fields") or {}
    selected: set[str] | None = None if changed_fields is None else set(changed_fields)
    written: dict[str, Any] = {}
    read_from: dict[str, str] = {}

    for crm_field, room_field in fields.items():
        if selected is not None and crm_field not in selected:
            continue
        if crm_field in payload:
            written[room_field] = payload[crm_field]
            read_from[room_field] = "payload"
        elif crm_field in enriched:
            # The gap the enrichment rule exists to fill. Recorded, because a
            # reader of a replica row should be able to see which values arrived
            # from an enriched field rather than from the change itself.
            written[room_field] = enriched[crm_field]
            read_from[room_field] = "enriched"

    key_field = sync_key_field(field_map)
    for source, origin in ((payload, "payload"), (enriched, "enriched")):
        value = source.get(str(field_map.get("sync_key")))
        if value is not None and str(value).strip():
            written[key_field] = value
            read_from[key_field] = origin
            break

    account_field = field_map.get("account_field")
    if account_field:
        for source, origin in ((payload, "payload"), (enriched, "enriched")):
            value = source.get(str(account_field))
            if value is not None and str(value).strip():
                written["account"] = value
                read_from["account"] = origin
                break

    return {"fields": written, "read_from": read_from}


def unmapped_crm_fields(
    payload: Mapping[str, Any],
    field_map: Mapping[str, Any],
    changed_fields: list[str] | None,
) -> list[str]:
    """CRM fields the event carried that the map does not name.

    Not an error and not dropped silently: reported on the replica row and on
    the invalidation, so a team that added a CRM field can see it arriving before
    it has decided where it belongs. This is the product's standing rule - "a team
    adding a field must not need coordination with anyone" - given somewhere to
    land.
    """
    known = set((field_map.get("fields") or {}).keys())
    known.add(str(field_map.get("sync_key") or ""))
    account = field_map.get("account_field")
    if account:
        known.add(str(account))
    candidates = set(changed_fields) if changed_fields is not None else set(payload)
    return sorted(name for name in candidates if str(name) not in known and str(name))
