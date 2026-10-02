"""Bulk-capability registry: what each vendor's upsert endpoint will accept.

The research names this seam directly. Its extensibility note: "The batch size,
key field, and ``allOrNone`` policy are per-connection settings. A third party can
register a vendor-specific 'bulk capability' (max batch size, supported key
types) and the scheduler adapts - e.g. it auto-falls back from ``UpsertMultiple``
to per-row ``PATCH`` for tables that don't support bulk upsert."

So a *capability* is the vendor-level answer to "what does one upsert request
here accept", and a *connection* is the per-connection answer to "and for this
table, on this room". Keeping the two apart is what lets the scheduler adapt
without knowing any vendor by name: it asks for a capability, gets a batch size
and a mode, and sends.

Two places a capability can come from, in precedence order:

1. **Stored.** A record in :data:`COLLECTION_CAPABILITY`. This is how a third
   party adds a vendor at runtime - the researched seam - and also how an
   operator corrects a built-in's numbers without a code change.
2. **Built in.** The three the research documents, below.

Where a stored capability overrides a built-in one, the run records which source
it used (``capability_source``). An override is never silent: a wrong batch size
for a vendor is exactly the kind of thing that should be visible in the log
rather than discovered as a mysterious 400.

Nothing here is a migration or a typed column. Capabilities are ordinary JSON
records, and a vendor a team has never heard of is added by adding a record.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping

from dsr.crm_upsert.errors import BatchTooLarge, NoUpsertPath, UnsupportedKey
from dsr.store import RecordStore

COLLECTION_CAPABILITY = "crm_upsert_capability"

#: The key types this connector understands, and what each one means on the
#: wire. Every one of the three the research documents appears here under the
#: name the vendor's own documentation uses for it.
KEY_TYPES: tuple[str, ...] = (
    "external_id",  # Salesforce: an External ID field
    "alternate_key",  # Dataverse: a key defined with sample_keyattribute
    "unique_property",  # HubSpot: the idProperty selector
    "record_id",  # the record id itself - refused, see UnsupportedKey
)

#: Field names that are a CRM record id rather than an external key.
#:
#: "Only external ids are supported. Don't use record ids." A record id is what
#: an upsert *returns*; keying on it cannot both create and update, because a key
#: that does not exist yet is precisely the case that must create. Matched
#: case-insensitively, because Salesforce spells it ``Id``, Dataverse
#: ``recordid``/``@odata.id``-adjacent, and a team's own field could be any of
#: them. ``crm_record_id`` is the room's own write-back column and must never be
#: mapped forward either: echoing it into the next request is the same bug one
#: layer up.
RECORD_ID_FIELDS: frozenset[str] = frozenset(
    {"id", "ids", "recordid", "record_id", "crm_record_id", "crmrecordid", "systemid", "guid"}
)

#: How the row is keyed, when the caller does not say. The research's flow says
#: the rows are "keyed on the sync key chosen in W2"; this workflow does not own
#: that choice, so the key type is required on a connection and this is only the
#: value used when a stored connection predates the field.
DEFAULT_KEY_TYPE = "external_id"


def is_record_id_field(name: str) -> bool:
    """Whether ``name`` names a record id rather than an external key."""
    return str(name).strip().lower() in RECORD_ID_FIELDS


@dataclass(frozen=True)
class Capability:
    """What one vendor's upsert endpoint accepts.

    A frozen dataclass on purpose: a capability is a fact about someone else's
    API, and the two ways to change it (override the stored record, or edit this
    file) should both be visible in the diff.
    """

    vendor: str
    #: The researched cap. "The list can contain up to 200 objects" for
    #: Salesforce; "Batch operations are limited to 100 records at a time" for
    #: HubSpot.
    max_batch_size: int
    #: The request shape. One of ``attributes`` (an ``attributes.type`` map per
    #: item), ``id_property`` (HubSpot's ``idProperty`` selector), or ``odata``
    #: (a ``Targets`` collection with ``@odata.type`` and ``@odata.id``).
    payload_style: str
    #: Whether the vendor's response carries a per-item success flag and errors
    #: array. This is the field that decides whether the connector can *confirm*
    #: a row: Dataverse's ``UpsertMultiple`` "returns ``204 NoContent``", so it
    #: cannot, and pretending otherwise would write a confirmation the connector
    #: never received.
    returns_per_item_results: bool
    #: Whether one request may carry a whole chunk. False is the researched
    #: fallback case - "it auto-falls back from ``UpsertMultiple`` to per-row
    #: ``PATCH`` for tables that don't support bulk upsert" - and the only thing
    #: :func:`resolve_mode` reads to make that decision.
    supports_bulk_upsert: bool = True
    #: Whether the vendor accepts a single-row upsert on an external key, which
    #: is what the researched fallback to per-row ``PATCH`` needs.
    supports_single_upsert: bool = True
    #: The researched per-connection switches this vendor actually honours.
    supports_update_only: bool = False
    supports_all_or_none: bool = False
    #: The key types this vendor's upsert can key on.
    supported_key_types: tuple[str, ...] = ("external_id", "alternate_key", "unique_property")
    #: Request templates, with ``{...}`` placeholders filled by the payload
    #: builders. ``api_version`` is substituted for the ``vXX.X`` the research
    #: writes, so a team can move a connection to a new API version without a
    #: code change.
    bulk_path: str = ""
    single_path: str = ""
    #: Whether this is one of the research's own documents, or a local
    #: judgement call. Carried into the run record so a log says which.
    sourced: bool = True
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["supported_key_types"] = list(self.supported_key_types)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Capability":
        """Build a capability from a stored record, ignoring unknown keys.

        Tolerating unknown keys is the schema-flexibility rule applied to a
        capability: a stored record written by a newer version of this feature
        must still load here rather than take the feature off the registry.
        """
        known = {f for f in cls.__dataclass_fields__}
        payload = {k: v for k, v in dict(data).items() if k in known}
        key_types = payload.get("supported_key_types")
        if isinstance(key_types, (list, tuple)):
            payload["supported_key_types"] = tuple(str(item) for item in key_types)
        payload.setdefault("vendor", "unknown")
        payload.setdefault("max_batch_size", 1)
        payload.setdefault("payload_style", "attributes")
        payload.setdefault("returns_per_item_results", True)
        return cls(**payload)


# --------------------------------------------------------------------------- #
# The three the research documents
# --------------------------------------------------------------------------- #

#: "Use a `PATCH` request with sObject Collections to either create or update
#: (upsert) up to 200 records based on an external ID field. This method returns
#: a list of `UpsertResult` objects. You can choose whether to roll back the
#: entire request when an error occurs."
#:
#: The single-row form is the second researched Salesforce endpoint:
#: "PATCH /services/data/vXX.X/sobjects/{sObject}/{fieldName}/{fieldValue}" with
#: an optional "updateOnly" parameter.
SALESFORCE = Capability(
    vendor="salesforce",
    max_batch_size=200,
    payload_style="attributes",
    returns_per_item_results=True,
    supports_single_upsert=True,
    supports_update_only=True,
    supports_all_or_none=True,
    supported_key_types=("external_id",),
    bulk_path="/services/data/vXX.X/composite/sobjects/{object}/{key_field}",
    single_path="/services/data/vXX.X/sobjects/{object}/{key_field}/{key_value}",
    sourced=True,
    notes=(
        "sObject Collections upsert. Every item needs an attributes map carrying "
        "type, and only external ids are supported - never record ids."
    ),
)

#: "To upsert records, make a `POST` request to /crm/objects/{version}/
#: {objectTypeId}/batch/upsert. In your request body, include the `idProperty`
#: parameter to identify the unique identifier property you're using."
#:
#: "Partial upserts are not supported when using `email` as the `idProperty` for
#: contacts" - enforced in :mod:`dsr.crm_upsert.payloads`, because that sentence
#: is the only thing standing between a sparse write and a blanked contact.
#:
#: The single-row path is **not** quoted by the research, which documents only the
#: batch endpoint. It is a local inference, recorded in
#: :mod:`dsr.crm_upsert.inferences`; ``sourced`` is True only because the batch
#: half of this capability is sourced, so the run log points at
#: ``inferences`` for the other half rather than pretending.
HUBSPOT = Capability(
    vendor="hubspot",
    max_batch_size=100,
    payload_style="id_property",
    returns_per_item_results=True,
    supports_single_upsert=True,
    supports_update_only=False,
    supports_all_or_none=False,
    supported_key_types=("unique_property",),
    bulk_path="/crm/v3/objects/{object}/batch/upsert",
    single_path="/crm/v3/objects/{object}/{key_field}/{key_value}",
    sourced=True,
    notes=(
        "Batch upsert with an idProperty selector. Capped at 100 records. "
        "Partial upserts are not supported when idProperty is email."
    ),
)

#: "`Upsert` (Create or Update) multiple records of same type in a single
#: request." with "a `Targets` collection where each item carries `@odata.type`
#: and `@odata.id` using the alternate key", and "The `UpsertMultiple` action
#: returns `204 NoContent`".
#:
#: ``returns_per_item_results=False`` is the load-bearing field here and is
#: straight from the source: with a 204 and no body there is nothing to confirm a
#: row with. ``max_batch_size`` is a local judgement call - the research quotes
#: the cap for Salesforce and HubSpot and is silent about Dataverse's - and is
#: listed as an inference.
DATAVERSE = Capability(
    vendor="dataverse",
    max_batch_size=1000,
    payload_style="odata",
    returns_per_item_results=False,
    supports_single_upsert=True,
    supports_update_only=False,
    supports_all_or_none=False,
    supported_key_types=("alternate_key",),
    bulk_path="/api/data/v9.2/{entityset}/Microsoft.Dynamics.CRM.UpsertMultiple",
    single_path="/api/data/v9.2/{entityset}({key_field}={key_value})",
    sourced=True,
    notes=(
        "UpsertMultiple returns 204 NoContent, so a row can be sent but never "
        "confirmed from the response. @odata.type is required on every item."
    ),
)

#: A table with no bulk upsert at all - the researched fallback case, shipped as
#: a default so the fallback is exercised by the product's own vocabulary rather
#: than only by a test. This is a local capability, not a researched vendor, and
#: says so.
LEGACY_TABLE = Capability(
    vendor="legacy_table",
    max_batch_size=50,
    payload_style="id_property",
    returns_per_item_results=True,
    supports_bulk_upsert=False,
    supports_single_upsert=True,
    supports_update_only=True,
    supports_all_or_none=False,
    supported_key_types=("external_id", "unique_property"),
    bulk_path="",
    single_path="/api/legacy/{object}/{key_field}/{key_value}",
    sourced=False,
    notes=(
        "A table that cannot take a bulk upsert. Every run against it falls back "
        "to one request per row, which is the researched auto-fallback rule."
    ),
)

BUILT_IN: dict[str, Capability] = {
    capability.vendor: capability for capability in (SALESFORCE, HUBSPOT, DATAVERSE, LEGACY_TABLE)
}


# --------------------------------------------------------------------------- #
# The in-process registry: the code-level half of the researched seam
# --------------------------------------------------------------------------- #

#: Vendors registered by a third party at import time. Kept separate from
#: :data:`BUILT_IN` so ``unregister_bulk_capability`` cannot delete a researched
#: capability out from under the product, and so a test can register and clean up.
_REGISTERED: dict[str, Capability] = {}


def register_bulk_capability(capability: Capability) -> Capability:
    """Register a vendor capability in this process.

    The researched extensibility seam at code level: a third party with a
    vendor-specific bulk capability calls this, and the scheduler adapts without
    learning the vendor's name anywhere. Returns the capability for chaining.

    A vendor that is already registered is replaced rather than refused: an
    operator who knows a built-in's batch size is wrong needs to be able to say
    so, and the run record carries ``capability_source`` so the replacement is
    visible in the log.
    """
    if not capability.vendor:
        raise ValueError("a capability needs a vendor name")
    if capability.max_batch_size < 1:
        raise ValueError(f"{capability.vendor}: max_batch_size must be at least 1")
    _REGISTERED[capability.vendor] = capability
    return capability


def unregister_bulk_capability(vendor: str) -> bool:
    """Remove a process-registered capability. Built-ins are not removable."""
    return _REGISTERED.pop(vendor, None) is not None


def registered_capabilities() -> dict[str, Capability]:
    """Process-registered capabilities, including ones shadowing a built-in."""
    return dict(_REGISTERED)


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ResolvedCapability:
    """A capability plus where it came from and the batch size to actually use.

    ``batch_size`` is ``None`` until :func:`resolve_batch_size` has checked it
    against the vendor's cap, because an over-cap request is refused rather than
    clamped and the two must not be confused.
    """

    capability: Capability
    source: str  # built_in | stored | registered | connection
    batch_size: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability.to_dict(),
            "source": self.source,
            "batch_size": self.batch_size,
        }


def _stored_capability(store: RecordStore | None, vendor: str) -> Capability | None:
    if store is None:
        return None
    record = store.get(f"{COLLECTION_CAPABILITY}_{vendor}")
    if record is None:
        matches = store.find(COLLECTION_CAPABILITY, {"vendor": vendor}, limit=1)
        record = matches[0] if matches else None
    if record is None:
        return None
    data = record.get("data") or {}
    if not isinstance(data, Mapping):
        return None
    return Capability.from_dict({**data, "vendor": data.get("vendor") or vendor})


def resolve_capability(
    store: RecordStore | None,
    vendor: str,
    *,
    declared: Mapping[str, Any] | None = None,
) -> ResolvedCapability:
    """Find the capability for ``vendor``, saying which one answered.

    Precedence, highest first: a capability declared inline on the connection, a
    stored record, a process-registered one, then the research's built-ins. A
    connection may declare its own because a vendor is a *connection*'s choice
    and a test should not have to mutate a process-wide registry to exercise a
    table with no bulk upsert.

    An unknown vendor with nothing declared is :class:`NoUpsertPath`: the
    research promises a fallback from bulk to per-row, and a vendor nobody has
    described has neither.
    """
    if declared:
        return ResolvedCapability(Capability.from_dict(declared), "connection")
    stored = _stored_capability(store, vendor)
    if stored is not None:
        return ResolvedCapability(stored, "stored")
    registered = _REGISTERED.get(vendor)
    if registered is not None:
        return ResolvedCapability(registered, "registered")
    built_in = BUILT_IN.get(vendor)
    if built_in is not None:
        return ResolvedCapability(built_in, "built_in")
    raise NoUpsertPath(
        f"no bulk capability is registered for vendor {vendor!r}: add a capability record "
        f"or call register_bulk_capability() so the connector knows its batch size"
    )


def catalogue(store: RecordStore | None = None) -> list[dict[str, Any]]:
    """Every capability a client can pick from, in one list.

    Stored records are folded in, so an operator sees their own override next to
    the researched one it replaces rather than having to know it exists.
    """
    merged: dict[str, Capability] = dict(BUILT_IN)
    for capability in _REGISTERED.values():
        merged[capability.vendor] = capability
    if store is not None:
        for record in store.list(COLLECTION_CAPABILITY, limit=500, order_by="id", descending=False):
            data = record.get("data") or {}
            vendor = str(data.get("vendor") or "")
            if not vendor:
                continue
            try:
                merged[vendor] = Capability.from_dict({**data, "vendor": vendor})
            except TypeError:
                # A record nobody can read is skipped rather than taken as
                # licence to fail the whole catalogue.
                continue

    listed: list[dict[str, Any]] = []
    for name in sorted(merged):
        capability = merged[name]
        entry = capability.to_dict()
        entry["origin"] = (
            "stored"
            if (store is not None and _stored_capability(store, name) is not None)
            else ("registered" if name in _REGISTERED else "built_in")
        )
        entry["is_default"] = name in BUILT_IN
        listed.append(entry)
    return listed


def resolve_mode(capability: Capability) -> str:
    """``bulk`` when one request may carry a chunk, else ``single``.

    The researched fallback in one line: "it auto-falls back from
    ``UpsertMultiple`` to per-row ``PATCH`` for tables that don't support bulk
    upsert". A capability with neither is :class:`NoUpsertPath` rather than a
    silent single, because a single request that also does not exist is not a
    fallback.
    """
    if capability.supports_bulk_upsert and capability.bulk_path:
        return "bulk"
    if capability.supports_single_upsert and capability.single_path:
        return "single"
    raise NoUpsertPath(
        f"{capability.vendor} declares neither a bulk upsert nor a single-row upsert, "
        "so there is nothing to fall back to"
    )


def resolve_key_type(capability: Capability, key_type: str, key_field: str) -> str:
    """Check a connection's key against the researched rules, returning it.

    Refuses a record id on the strength of "Only external ids are supported.
    Don't use record ids.", and a key type the vendor's capability does not
    declare. Both are configuration errors that would otherwise surface as an
    opaque vendor error on the first real run.
    """
    normalised = str(key_type or DEFAULT_KEY_TYPE).strip().lower()
    if normalised not in KEY_TYPES:
        raise UnsupportedKey(f"key_type {key_type!r} is not one of {list(KEY_TYPES)}")
    if normalised == "record_id" or is_record_id_field(key_field):
        raise UnsupportedKey(
            f"key field {key_field!r} is a record id. Only external ids are supported; "
            "a record id is what an upsert returns, so keying on it cannot create"
        )
    if normalised not in capability.supported_key_types:
        raise UnsupportedKey(
            f"{capability.vendor} cannot key an upsert on {normalised!r}; it supports "
            f"{list(capability.supported_key_types)}"
        )
    return normalised


def resolve_connection_key_type(capability: Capability, stated: str, key_field: str) -> str:
    """The key type for a connection, inferring it when the connection is silent.

    A vendor whose capability declares exactly one key type - Salesforce
    ``external_id``, Dataverse ``alternate_key``, HubSpot ``unique_property`` -
    has no choice to make, so requiring every connection to restate it would be
    pure friction and one more place to get it wrong. A vendor that declares
    several keeps :data:`DEFAULT_KEY_TYPE`, and an explicit ``key_type`` on the
    connection always wins.

    The record-id refusal is unaffected: it is checked on the field *name* in
    :func:`resolve_key_type`, so inferring a key type can never slip a record id
    through.
    """
    if stated:
        return resolve_key_type(capability, stated, key_field)
    supported = list(capability.supported_key_types)
    if len(supported) == 1:
        return resolve_key_type(capability, supported[0], key_field)
    return resolve_key_type(capability, DEFAULT_KEY_TYPE, key_field)


def resolve_batch_size(capability: Capability, requested: int | None) -> int:
    """The batch size to use, refusing rather than clamping an over-cap request.

    The caps are hard vendor limits, so asking for 500 rows at HubSpot cannot be
    honoured by sending 100 and calling it 500. A run that silently clamped would
    report a plan the connector did not execute, and a connection set to 500
    would fail only once somebody had a real run to look at.
    """
    cap = int(capability.max_batch_size)
    if requested is None:
        return cap
    asked = int(requested)
    if asked < 1:
        raise BatchTooLarge(f"batch size must be at least 1, got {asked}")
    if asked > cap:
        raise BatchTooLarge(
            f"{capability.vendor} accepts at most {cap} records per upsert request, "
            f"batch size {asked} was requested. Lower the connection's batch size."
        )
    return asked


def with_batch_size(capability: Capability, size: int) -> Capability:
    """A copy of ``capability`` whose cap is ``size``.

    Used after :func:`resolve_batch_size` has accepted a smaller per-connection
    size, so the payload builder and the chunker read one number rather than
    two that could disagree.
    """
    return replace(capability, max_batch_size=int(size))


def merge_declared(base: Capability, declared: Mapping[str, Any]) -> Capability:
    """Layer a connection's inline declaration over a resolved capability.

    Only keys the caller actually supplied are taken, so a connection that
    declares nothing but a vendor inherits the researched numbers. Round-tripping
    through :meth:`Capability.from_dict` is what makes the field-name lists
    tuples again, which ``dataclasses.replace`` requires.
    """
    supplied = {k: v for k, v in dict(declared).items() if k != "vendor"}
    if not supplied:
        return base
    merged = {**base.to_dict(), **supplied, "vendor": base.vendor}
    return Capability.from_dict(merged)


def supported_vendors() -> list[str]:
    """Vendor names a new connection can name without declaring a capability."""
    return sorted(set(BUILT_IN) | set(_REGISTERED))
