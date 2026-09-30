"""The room's local replica, and what each of the four change types does to it.

The data flow ends "commit -> room replica / UI invalidation", and the four
change types come from "Changes include creation of a new record, updates to an
existing record, deletion of a record, and undeletion of a record." Read
together they are four behaviours, and this module is where each one is decided:

``CREATE``    the row is written from the payload. A create event "contains all
              the populated fields", so nothing is missing and nothing is merged.
``UPDATE``    only the fields the event says changed are written, and everything
              else the replica already holds is left alone. That is the
              "unchanged but needed" gap: an update event does not carry the
              fields that did not change, so a room that cleared them would lose
              data the CRM still has.
``DELETE``    the row becomes a tombstone. Its sync key is kept, which is the
              only reason an UNDELETE can find it again.
``UNDELETE``  the tombstone comes back, written from the payload, because an
              undelete event also "contains all the populated fields".

The tombstone is this build's decision and the research does not state it. See
``delete-is-a-tombstone`` in :mod:`dsr.change_stream.inferences`.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.change_stream import vocabulary
from dsr.change_stream.errors import RecordUnresolvable
from dsr.change_stream.events import enriched_fields_in_effect
from dsr.change_stream.fieldmap import (
    apply_field_map,
    resolve_external_id,
    sync_key_field,
    unmapped_crm_fields,
)


def plan_event(
    event: Mapping[str, Any],
    channel: Mapping[str, Any],
    *,
    existing: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """What this event would do to the replica, decided before anything is written.

    Returned rather than applied, so the refusal path and the write path share
    one decision and cannot disagree - and so the HTTP layer can report the plan
    for an event it is about to buffer without having written anything.
    """
    field_map: Mapping[str, Any] = channel.get("field_map") or {}
    change_type = str(event["change_type"])
    enriched = enriched_fields_in_effect(event, channel)
    payload: Mapping[str, Any] = event.get("payload") or {}
    changed = event.get("changed_fields")
    changed_list = list(changed) if isinstance(changed, list) else None

    external_id = resolve_external_id(payload, enriched, field_map)
    key_field = sync_key_field(field_map)

    if change_type in ("CREATE", "UNDELETE") and external_id is None:
        # A create event carries all the populated fields, so a missing sync key
        # is not an enrichment problem - it is an event that cannot say what it
        # created. Said separately so the message is not "add this to your
        # enriched fields", which would be advice that cannot help.
        raise RecordUnresolvable(
            f"a {change_type} event carries every populated field, so its sync key "
            f"{field_map.get('sync_key')!r} was expected in the payload and was not. "
            "Enrichment does not apply to create or undelete events."
        )

    if change_type in ("UPDATE", "DELETE") and external_id is None:
        raise RecordUnresolvable(
            f"this {change_type} event does not carry {field_map.get('sync_key')!r} in its "
            f"payload or its enriched fields, so the room cannot tell which record changed. "
            f"Add {field_map.get('sync_key')!r} to the enriched fields on channel "
            f"{channel.get('name')!r} - a custom channel, since enrichment is not available on "
            "the standard one. This is the case event enrichment exists for: the field the room "
            "needs to resolve a record is a field the change did not touch."
        )

    applied = apply_field_map(payload, enriched, field_map, changed_fields=changed_list)
    unmapped = unmapped_crm_fields(payload, field_map, changed_list)

    if change_type == "CREATE":
        action = "insert"
        state = "live"
    elif change_type == "UPDATE":
        action = "update" if existing else "insert"
        state = "live"
    elif change_type == "DELETE":
        action = "delete"
        state = "deleted"
    else:  # UNDELETE
        action = "restore" if existing else "insert"
        state = "live"

    return {
        "action": action,
        "state": state,
        "change_type": change_type,
        "external_id": external_id,
        "sync_key_field": key_field,
        "fields": applied["fields"],
        "read_from": applied["read_from"],
        "unmapped_crm_fields": unmapped,
        "enriched_fields_used": sorted(enriched),
        "resolution": "payload" if resolve_external_id(payload, {}, field_map) else "enriched",
        "account": str(applied["fields"].get("account") or "") or None,
        "existed": existing is not None,
        # Carried onto the replica row so the lookup that finds it again can be
        # answered by the dynamic index rather than by scanning every room's rows.
        "channel_id": str(channel.get("id") or ""),
        "channel_name": str(channel.get("name") or ""),
    }


def merge_into(
    existing: Mapping[str, Any] | None,
    plan: Mapping[str, Any],
    event: Mapping[str, Any],
) -> dict[str, Any]:
    """The replica row after this event, with no store and no side effect.

    A pure function so the four change-type behaviours are one readable function
    rather than four scattered write paths, and so a test can assert on the row a
    commit would produce without committing anything.
    """
    key_field = str(plan["sync_key_field"])
    previous: dict[str, Any] = dict((existing or {}).get("data") or {})
    written: dict[str, Any] = dict(previous)

    if plan["state"] == "deleted":
        # A tombstone keeps the sync key and the account, and drops nothing. The
        # room may still want to know which record the CRM took away, and
        # dropping the key would make the row unresolvable if it came back.
        written[key_field] = plan["external_id"]
    else:
        written.update(plan["fields"])
        written[key_field] = plan["external_id"]

    changed_now = sorted(
        name for name in set(previous) | set(written) if previous.get(name) != written.get(name)
    )

    # The whole replica row is one JSON payload, metadata included, so that a
    # team can filter on any of it through the dynamic index - `replica_state` is
    # the one that matters most, because "which records has the CRM deleted" is
    # the question a room asks before it trusts a panel.
    written["replica_state"] = str(plan["state"])
    written["channel_id"] = str(plan.get("channel_id") or "")
    written["channel_name"] = str(plan.get("channel_name") or "")
    written["last_change_type"] = str(plan["change_type"])
    written["last_sequence_number"] = int(event["sequence_number"])
    written["last_transaction_key"] = str(event["transaction_key"])
    written["last_commit_timestamp"] = str(event["commit_timestamp"])
    written["last_entity"] = str(event.get("entity") or "")
    written["enriched_fields_used"] = list(plan.get("enriched_fields_used") or [])
    written["unmapped_crm_fields"] = list(plan.get("unmapped_crm_fields") or [])

    return {
        "data": written,
        "previous": previous,
        "changed_replica_fields": changed_now,
        "state": str(plan["state"]),
    }


def replica_findings(
    plan: Mapping[str, Any],
    channel: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """What a reader of a commit result should be warned about.

    Three of these are the research's own points about how little a change event
    tells you, surfaced as warnings rather than left in a comment: the values that
    arrived from an enriched field, the CRM fields the map does not name, and a
    replica whose row was not there before the event.
    """
    findings: list[dict[str, Any]] = []
    enriched_used = list(plan.get("enriched_fields_used") or [])
    if enriched_used:
        findings.append({
            "code": "values_from_enriched_fields",
            "severity": "info",
            "detail": (
                f"{', '.join(enriched_used)} came from the channel's enriched fields, not from "
                "this change. An update event does not carry the fields that did not change."
            ),
        })
    unmapped = list(plan.get("unmapped_crm_fields") or [])
    if unmapped:
        findings.append({
            "code": "unmapped_crm_fields",
            "severity": "info",
            "detail": (
                f"the event carried {', '.join(unmapped)}, which field_map on channel "
                f"{channel.get('name')!r} does not name. They are recorded, not dropped."
            ),
        })
    if plan["change_type"] == "UPDATE" and not plan.get("existed"):
        findings.append({
            "code": "update_for_unknown_record",
            "severity": "warning",
            "detail": (
                f"this UPDATE was applied as an insert: no replica row held "
                f"{plan['external_id']!r} before it. The room had not seen the create, which "
                "usually means the channel was subscribed after the record was made."
            ),
        })
    if plan["change_type"] == "DELETE" and not plan.get("existed"):
        findings.append({
            "code": "delete_for_unknown_record",
            "severity": "warning",
            "detail": (
                f"this DELETE was applied to a tombstone: no replica row held "
                f"{plan['external_id']!r} before it. The record is remembered as gone so an "
                "UNDELETE can find it."
            ),
        })
    if plan["change_type"] == "UNDELETE" and not plan.get("existed"):
        findings.append({
            "code": "undelete_for_unknown_record",
            "severity": "warning",
            "detail": (
                f"this UNDELETE found no tombstone for {plan['external_id']!r}, so there was no "
                "earlier state to restore and the row was written from the event. An undelete "
                "event contains all the populated fields, so the row is complete either way."
            ),
        })
    return findings


def deal_panel_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """One replica row, shaped for the buyer's deal panel.

    "The room refreshes the affected buyer's deal panel" is the whole of what the
    research says a panel is, so this is a projection of the replica rather than a
    new model: the sync key, the account it belongs to, the mapped room fields,
    and enough about the last change to explain why the panel moved.
    """
    data = dict(row.get("data") or {})
    tracked = {
        "replica_state",
        "channel_id",
        "channel_name",
        "last_change_type",
        "last_sequence_number",
        "last_transaction_key",
        "last_commit_timestamp",
        "last_entity",
        "enriched_fields_used",
        "unmapped_crm_fields",
    }
    return {
        "replica_id": str(row.get("id") or ""),
        "room_id": str(row.get("room_id") or ""),
        "state": str(data.get("replica_state") or vocabulary.REPLICA_STATES[0]),
        "account": str(data.get("account") or ""),
        "last_change_type": str(data.get("last_change_type") or ""),
        "last_commit_timestamp": str(data.get("last_commit_timestamp") or ""),
        "last_sequence_number": data.get("last_sequence_number"),
        "fields": {name: value for name, value in data.items() if name not in tracked},
    }
