"""Resolving a buyer's CRM identity before the room reads anything.

User-flow step two: "Room resolves the buyer's CRM identity (from W1/W2 mapping
or a signed token)."

The research names the source and does not choose it. This build takes the room
mapping, and :data:`dsr.crm_integration.inferences.IDENTITY_SOURCE` records the
derivation. What matters for the rest of the package is the shape of the answer:
an identity is a row that carries the vendor, the three record ids the deal panel
needs, the vendor capability flags, and the field map the read set is derived
from.
"""

from __future__ import annotations

from typing import Any

from dsr.crm_integration import vocabulary
from dsr.crm_integration.errors import (
    AmbiguousIdentity,
    DuplicateIdentity,
    MissingBuyerEmail,
    NoCrmIdentity,
    UnknownIdentity,
)
from dsr.store import RecordStore

#: The collection the room's CRM identities live in. Owned by this feature and
#: created by discovery, with no migration: the payload is ordinary JSON in
#: ``records.data``, so a deployment can add a field without coordinating with
#: anyone.
IDENTITIES = "crm_read_identity"

#: The room-scoped key. Two rooms may hold the same buyer for different orgs,
#: which is the whole point of a sales room, so the buyer email alone is not a
#: key.
IDENTITY_KEY = ("buyer_email",)


def normalise(payload: dict[str, Any]) -> dict[str, Any]:
    """Clean a submitted identity without rejecting a field we do not know.

    Only the fields the read path reads are validated. Everything else is passed
    through untouched, because the payload is arbitrary JSON by contract and a
    team that adds ``crm_region__c`` tomorrow must not need a change here.
    """
    system = vocabulary.require_system(payload.get("system") or "")
    buyer = str(payload.get("buyer_email") or payload.get("email") or "").strip()
    if not buyer:
        raise MissingBuyerEmail(
            "buyer_email is required. It is the room-scoped key the read is filtered by."
        )
    data: dict[str, Any] = {
        "system": system,
        "buyer_email": buyer,
        "buyer_name": str(payload.get("buyer_name") or "").strip(),
        "account_id": str(payload.get("account_id") or "").strip(),
        "contact_id": str(payload.get("contact_id") or "").strip(),
        "deal_id": str(payload.get("deal_id") or "").strip(),
        "source": vocabulary.IDENTITY_SOURCE,
        "display_labels": supports_display_labels(payload, system),
        "ttl_seconds": vocabulary.clamp_ttl(payload.get("ttl_seconds")),
    }
    # Copied rather than whitelisted, so an unknown field survives a round trip.
    for key, value in payload.items():
        if key not in data and key not in {"email", "ttl_seconds"}:
            data[key] = value
    return data


def supports_display_labels(payload: dict[str, Any], system: str) -> bool:
    """Read the capability flag, and let the row override the vendor default.

    The research describes the flag as the vendor's, and this API keeps the
    vendor default as the default. An operator may still pin it, because a
    Dataverse org with the annotation disabled by policy reports it capable and
    then returns no labels - and a panel that falls back to its own option-set
    map silently is better than a panel showing ``0``.
    """
    declared = payload.get("display_labels")
    if isinstance(declared, bool):
        return declared
    return vocabulary.supports_display_labels(system)


def identities(store: RecordStore, room_id: str | None = None) -> list[dict[str, Any]]:
    """Every registered identity, optionally for one room."""
    rows = store.list(IDENTITIES, room_id=room_id, limit=1000, order_by="created_at")
    return [row for row in rows if row["data"].get("source") != "seed_deleted"]


def identity(store: RecordStore, identity_id: str, room_id: str | None = None) -> dict[str, Any]:
    """One identity, or refuse.

    ``room_id`` is checked when given. A room-scoped read that accepted another
    room's identity id would put one buyer's deal panel on another buyer's room,
    which is the one mistake this whole product exists to prevent.
    """
    record = store.get(identity_id)
    if record is None or record.get("collection") != IDENTITIES:
        raise UnknownIdentity(f"no CRM identity with id {identity_id}")
    if room_id is not None and record.get("room_id") not in (None, room_id):
        raise UnknownIdentity(f"CRM identity {identity_id} does not belong to room {room_id}")
    return record


def resolve(
    store: RecordStore,
    room_id: str,
    identity_id: str | None = None,
    buyer_email: str | None = None,
) -> dict[str, Any] | None:
    """Find the identity for a room view, or return ``None``.

    ``None`` is a real answer and not a failure. "the room still works when a
    seller authors it without CRM context" is the research's own summary of the
    workflow, so the panel route has to be able to say "this buyer has no CRM
    identity" and render the deal panel without any CRM fields in it.

    The lookup order is the identity id first and the buyer email second,
    because an explicit id is what the caller already resolved and a re-read of
    the same row under a different key would be a second answer to one question.
    """
    if identity_id:
        record = store.get(identity_id)
        if record is None or record.get("collection") != IDENTITIES:
            return None
        if record.get("room_id") not in (None, room_id):
            return None
        return record
    buyer = str(buyer_email or "").strip().lower()
    if not buyer:
        return None
    for record in identities(store, room_id=room_id):
        if str(record["data"].get("buyer_email") or "").strip().lower() == buyer:
            return record
    return None


def resolve_or_refuse(
    store: RecordStore,
    room_id: str,
    identity_id: str | None = None,
    buyer_email: str | None = None,
) -> dict[str, Any] | None:
    """:func:`resolve`, plus the one-ambiguous-caller rule.

    A pull that names neither an identity nor a buyer has said "the room", and a
    room with exactly one registered buyer has one answer. A room with several
    has no answer at all, and guessing would put one buyer's deal panel on
    another's room. That is refused with the count and the query parameter that
    resolves it, rather than resolved to the newest row.
    """
    found = resolve(store, room_id, identity_id=identity_id, buyer_email=buyer_email)
    if found is not None or identity_id or buyer_email:
        return found
    registered = identities(store, room_id=room_id)
    if len(registered) == 1:
        return registered[0]
    if len(registered) > 1:
        raise AmbiguousIdentity(
            f"room {room_id} has {len(registered)} CRM identities and this read named "
            "none of them. Pass ?identity_id= or ?buyer_email= so the panel is the "
            "right buyer's."
        )
    return None


def require(
    store: RecordStore,
    room_id: str,
    identity_id: str | None = None,
    buyer_email: str | None = None,
) -> dict[str, Any]:
    """:func:`resolve`, but a caller that cannot render a panel gets a refusal."""
    record = resolve(store, room_id, identity_id=identity_id, buyer_email=buyer_email)
    if record is None:
        if identity_id:
            raise UnknownIdentity(f"no CRM identity with id {identity_id}")
        raise NoCrmIdentity(
            f"room {room_id} has no CRM identity for "
            f"{buyer_email or 'this view'}. A seller may author a room without CRM "
            "context; the deal panel then renders without CRM fields."
        )
    return record


def check_duplicate(
    store: RecordStore,
    room_id: str,
    data: dict[str, Any],
    exclude_id: str | None = None,
) -> None:
    """Refuse a second identity for one buyer in one room.

    The read is scoped by ``(room_id, buyer_email)`` and the cache is keyed the
    same way, so two rows would give one panel two answers and the cache two
    entries that disagree with each other.

    ``exclude_id`` is the row a patch is rewriting. Skipping it by **record id**
    rather than by email is the whole reason patching works: skipping by email
    would also skip the row being created, because a new registration carries the
    same buyer as the row it is colliding with, and the collision check would
    pass every time.
    """
    buyer = str(data.get("buyer_email") or "").strip().lower()
    vendor = data.get("system")
    for record in identities(store, room_id=room_id):
        if exclude_id is not None and record["id"] == exclude_id:
            continue
        other = record["data"]
        if str(other.get("buyer_email") or "").strip().lower() != buyer:
            continue
        if (other.get("system") or "") != vendor:
            continue
        raise DuplicateIdentity(
            f"room {room_id} already has a {vendor} identity for {buyer} "
            f"(id {record['id']}). Patch it instead of registering a second one."
        )


def identity_view(record: dict[str, Any]) -> dict[str, Any]:
    """The flattened shape a client reads, with the capability flags beside it."""
    data = record.get("data") or {}
    system = data.get("system") or ""
    return {
        "id": record["id"],
        "room_id": record.get("room_id"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        **data,
        "identity_source": data.get("source") or vocabulary.IDENTITY_SOURCE,
        "identity_source_derivation": "see /wf-042/inferences",
        "display_label_capability": vocabulary.DISPLAY_LABEL_CAPABILITY,
        "display_labels": supports_display_labels(data, system),
        "objects": list(vocabulary.CRM_OBJECTS),
    }


__all__ = [
    "IDENTITIES",
    "IDENTITY_KEY",
    "check_duplicate",
    "identities",
    "identity",
    "identity_view",
    "normalise",
    "require",
    "resolve",
    "resolve_or_refuse",
    "supports_display_labels",
]
