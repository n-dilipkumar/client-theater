"""Transform via field map, then upsert into the room replica.

The researched data flow is explicit about the order:

    "job result file / paged rows -> transform via field map -> upsert into room
    replica (or push into CRM for a reverse backfill) -> advance cursor"

Three things live here, and the third is what makes the whole thing idempotent.

**The key.** A replica row is upserted on the value the vendor considers stable
for the record. Without one there is no upsert, only an insert, and a restart
would double every row - which would make the research's "no duplicates, no
gaps" untrue. A row that carries no key is therefore refused rather than stored
(see :class:`~dsr.crm_backfill.errors.UnkeyedRow`).

**The map.** ``{crm_field: replica_field}``, applied one field at a time. A
field the map does not mention is carried through under its own name, because
this product's standing rule is that a team adding a field must not need
coordination with anyone: a map that silently dropped everything unmapped would
be a map that loses data on every vendor that adds a field.

**What counts as unchanged.** Two replica rows are the same row when their
*mapped payload* is identical. Their bookkeeping is not part of that: a run that
re-reads a page and rewrote identical rows would produce an audit row per row
per restart and a revision number nobody can reason about. So an unchanged row
is not written at all, and its ``last_seen_at`` means "when its content last
changed", not "when we last looked". That is the visible half of idempotence,
and it is what a reviewer can count in the run's own totals.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.crm_backfill.errors import PlanError, UnkeyedRow

#: The fields this product adds to a replica row, which are bookkeeping rather
#: than vendor data. A field map may not target one of them: mapping
#: ``external_id`` onto ``last_seen_at`` would be a request this package cannot
#: keep true.
RESERVED_FIELDS: frozenset[str] = frozenset(
    {
        "external_id",
        "vendor",
        "connection_id",
        "object",
        "run_id",
        "first_seen_at",
        "last_seen_at",
        "source_updated_at",
    }
)

#: The envelope's own reserved names, mirrored from
#: :data:`dsr.db.audited._RESERVED` rather than imported, because it is private
#: there and a mirror is better than a reach into another module's internals.
#:
#: This set is the reason a vendor field called ``id`` is not written as ``id``.
#: A CRM's record id is the most important field it has - it is what the upsert
#: keys on - and the store strips ``id`` from every payload because the envelope
#: owns that name. Carrying the value as ``external_id`` keeps it, and
#: duplicating it under the envelope's name would produce a row that silently
#: loses its own identity.
ENVELOPE_RESERVED: frozenset[str] = frozenset(
    {"id", "collection", "room_id", "revision", "created_at", "updated_at", "deleted_at"}
)

#: The default key field, when a connection does not name its own. A vendor
#: external id is the thing a CRM considers the record's identity, and this
#: build's simulated history uses ``id`` for exactly that reason.
DEFAULT_KEY_FIELD = "id"

#: The fields a key is looked for under, in order. The first that carries a
#: value wins, so a history that uses one spelling and a caller that names
#: another both key correctly.
KEY_FIELDS: tuple[str, ...] = ("id", "external_id", "externalId")


def external_id(row: Mapping[str, Any], key_field: str = DEFAULT_KEY_FIELD) -> str:
    """The value a row is upserted on, or a refusal naming the field to fix."""
    for field in (key_field, *KEY_FIELDS):
        if not field:
            continue
        value = row.get(field)
        if value is None or str(value).strip() == "":
            continue
        return str(value).strip()
    raise UnkeyedRow(
        f"the row has no value for {key_field!r}, nor for id, external_id or externalId, so it "
        "cannot be upserted. A row that cannot be keyed is a row a restart would store twice, and "
        "the promise this workflow makes is that a restart produces no duplicates and no gaps."
    )


def apply_map(
    row: Mapping[str, Any],
    field_map: Mapping[str, str] | None,
    *,
    reverse: bool = False,
) -> dict[str, Any]:
    """Transform one vendor row through the field map.

    ``reverse`` is for the push direction: the same map read the other way, so a
    connection configures its transform once and both directions agree about
    what a field means. A row read backwards carries the bookkeeping back in
    reverse too, which is what makes a push the same operation rather than a
    second one.
    """
    lookup = {source: target for source, target in (field_map or {}).items()}
    if reverse:
        lookup = {target: source for source, target in lookup.items()}

    mapped: dict[str, Any] = {}
    for key, value in row.items():
        name = str(key)
        target = lookup.get(name, name)
        if target in ENVELOPE_RESERVED:
            # The envelope owns these names, and the value that matters among
            # them is carried as `external_id`. Skipping rather than raising is
            # what lets a vendor that calls its key `id` - which is most of them
            # - be backfilled at all.
            continue
        if target in RESERVED_FIELDS:
            if reverse:
                # Bookkeeping going back out to the CRM would be a field the
                # vendor does not have, and the map is not asking for it.
                continue
            raise PlanError(
                f"field_map maps {name!r} onto {target!r}, which is bookkeeping this product keeps "
                f"({', '.join(sorted(RESERVED_FIELDS))}). A map onto one of those would put a CRM "
                "field where the row's own identity or timestamps live."
            )
        mapped[target] = value
    return mapped


def replica_row(
    row: Mapping[str, Any],
    *,
    field_map: Mapping[str, str] | None,
    key_field: str,
    vendor: str,
    connection_id: str,
    room_id: str,
    object_name: str | None,
    run_id: str | None,
    at: str,
) -> dict[str, Any]:
    """One row as it will be stored: mapped payload plus this product's bookkeeping."""
    key = external_id(row, key_field)
    payload = apply_map(row, field_map)
    payload.update(
        {
            "external_id": key,
            "vendor": vendor,
            "connection_id": connection_id,
            "object": object_name or (row.get("object") or None),
            "first_seen_at": at,
            "last_seen_at": at,
            "source_updated_at": _stamp(row),
        }
    )
    if run_id:
        payload["run_id"] = run_id
    return payload


def payload_of(stored: Mapping[str, Any] | None) -> dict[str, Any]:
    """The part of a stored row a change is measured against.

    Everything except this product's bookkeeping. A page re-read after a crash
    produces the same payload, so the row is recognised as unchanged and not
    written again - which is what makes the retry a retry rather than a
    duplicate.
    """
    if not stored:
        return {}
    return {str(key): value for key, value in stored.items() if str(key) not in RESERVED_FIELDS}


def merge(
    stored: Mapping[str, Any] | None, incoming: Mapping[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Merge a fresh payload over a stored row.

    Returns ``(merged, changed)``. ``changed`` is the answer to "should this be
    written", and it compares payloads only, so a page re-read after a crash
    comes back as a run of ``changed: false`` rather than a run of writes.

    ``first_seen_at`` keeps the earlier of the two: it is when the replica first
    learned about this row, which the newest read cannot un-know. ``last_seen_at``
    always moves, because the content may genuinely have changed.
    """
    if not stored:
        return dict(incoming), True
    merged = dict(incoming)
    merged["first_seen_at"] = stored.get("first_seen_at") or incoming.get("first_seen_at")
    if stored.get("external_id") != incoming.get("external_id"):
        return dict(incoming), True
    return merged, payload_of(stored) != payload_of(incoming)


def outbox(
    stored_rows: Sequence[Mapping[str, Any]],
    *,
    field_map: Mapping[str, str] | None,
    key_field: str = DEFAULT_KEY_FIELD,
) -> list[dict[str, Any]]:
    """Turn stored replica rows into what a push will send.

    The reverse transform, in the same shape the pull reads, so a push and a
    pull page through the same machinery. Soft-deleted rows are left out: a push
    is a statement of what the replica currently holds, and a deleted row is not
    part of that.

    The key goes back under the name the vendor uses for it. The pull stores it
    as ``external_id`` because the envelope owns ``id`` (see
    :data:`ENVELOPE_RESERVED`), and a push that sent ``external_id`` to a CRM
    expecting ``Id`` would be rejected by the vendor for the same reason the
    pull could not store it.
    """
    sent = []
    for row in stored_rows:
        if row.get("deleted_at"):
            continue
        payload = apply_map(row, field_map, reverse=True)
        if key_field and key_field not in ENVELOPE_RESERVED:
            payload[key_field] = row.get("external_id")
        sent.append(payload)
    return sent


def _stamp(row: Mapping[str, Any]) -> str | None:
    for field in ("updated_at", "updatedAt", "occurred_at", "occurredAt", "created_at"):
        value = row.get(field)
        if value:
            return str(value)
    return None
