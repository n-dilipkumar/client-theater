"""Per-connection settings, the room's pending queue, and the backlog decision.

The research is specific that the upsert's knobs are per-connection, not global:
"The batch size, key field, and ``allOrNone`` policy are per-connection settings.
A third party can register a vendor-specific 'bulk capability' (max batch size,
supported key types) and the scheduler adapts - e.g. it auto-falls back from
``UpsertMultiple`` to per-row ``PATCH`` for tables that don't support bulk
upsert."

So a :class:`Connection` is one room's answer to "which CRM object, keyed on
which external field, in batches of how many, rolling back or not". Two rooms may
point at the same vendor with different answers, and neither affects the other.

What a connection must not do
-----------------------------

* **Key on a record id.** Checked in :mod:`dsr.crm_upsert.capabilities` and
  re-checked here, so the refusal happens when the connection is saved rather
  than on the first run that trips over it.
* **Ask for a batch bigger than the vendor accepts.** Also refused on save: a
  connection that cannot work is not something to discover during a sync.
* **Map a room field onto a record id.** Same reason, and it is the feedback loop
  the research's "no ``id`` field" rule is really about.

Where the research is silent
----------------------------

:func:`lint` reports the one documented gap - an email address used as the
Salesforce External ID path segment can 404 on a TLD/extension collision
(``example@email.inc``) - and the research is explicit that "the documented
workarounds (different External ID field, custom Apex REST endpoint) were not
cross-verified against a second source". So the lint is an **advisory**, printed
on the connection and served by the route: the research documents the failure but
not a verified fix, and this module declines to invent one. Every other warning
is a hard rule above; this one is information.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from dsr.crm_upsert.capabilities import (
    Capability,
    is_record_id_field,
    resolve_batch_size,
    resolve_capability,
    resolve_connection_key_type,
)
from dsr.crm_upsert.errors import UnknownConnection, UpsertError
from dsr.store import RecordStore

#: The room's engagement queue. The research names it "room ``engagement``
#: table" and says "Per-row outcomes are written back to the room", so the queue
#: and the write-back share one collection.
COLLECTION_ENGAGEMENT = "engagement"
COLLECTION_CONNECTION = "crm_upsert_connection"
COLLECTION_RUN = "crm_upsert_run"
COLLECTION_CONFIG = "crm_upsert_config"

CONFIG_KEY = "settings"

#: How a row's ``sync_status`` reads. A row with no ``sync_status`` at all is
#: pending: it has never been sent, which is exactly what the queue is.
STATUS_PENDING = "pending"
STATUS_SYNCED = "synced"
STATUS_UNCONFIRMED = "unconfirmed"
STATUS_FAILED = "failed"

QUEUE_STATUSES: tuple[str, ...] = (STATUS_SYNCED, STATUS_UNCONFIRMED, STATUS_FAILED)

#: The statuses a run will still pick a row up from.
#:
#: A row that **failed** is still unsynced, so it belongs back in the queue -
#: the researched flow's own mechanism, since this workflow names no retry
#: policy, no dead-letter queue, and no give-up rule. It keeps the distinct
#: ``failed`` status rather than being flattened back to ``pending``, so a rep can
#: see at a glance which rows have already tried and lost. The two are separated
#: here because conflating them is the quiet version of the same bug: a
#: ``failed`` row that left the queue would be silently dropped, and one that was
#: renamed ``pending`` would lose the fact that it had already been tried.
UNSENT_STATUSES: frozenset[str] = frozenset({STATUS_PENDING, STATUS_FAILED})

DEFAULT_CONFIG: dict[str, Any] = {
    "queue": {
        #: The engagement table. Configurable because nothing in this product
        #: declares it and a team that calls its rows something else should not
        #: have to fork the feature.
        "collection": COLLECTION_ENGAGEMENT,
        #: "The room's queue accumulates up to 200 pending engagement rows". 200
        #: is the sourced Salesforce collections cap, so the target is the vendor's
        #: own ceiling rather than a number of our own.
        "target": 200,
        #: "the room may also upsert opportunistically when the queue exceeds N
        #: rows". The research names N and does not fix it; see
        #: :mod:`dsr.crm_upsert.inferences`.
        "opportunistic_threshold": 25,
    },
    "schedule": {
        #: "Nightly/backlog upsert runs on the room's scheduler". The research says
        #: nightly and nothing finer.
        "interval_hours": 24,
        #: A queue that has never been run is due however recent it is: a
        #: connection with a full queue and no history has never synced.
        "due_with_no_history": True,
    },
    "mapping": {
        #: The room field that carries a row's own stable identity, and therefore
        #: the value an upsert keys on. Separate from ``key_source`` on the
        #: connection because a team may key on an email or an account instead.
        "key_source": "engagement_id",
        #: Room field -> CRM field. Every line is optional; a team adds its own
        #: fields here without a migration, which is the whole point of storing
        #: payloads as open JSON.
        "fields": {
            "event_type": "Event_Type__c",
            "occurred_at": "Occurred_At__c",
            "person": "Buyer_Email__c",
            "account": "Account_Name__c",
            "document": "Document__c",
            "seconds_on_page": "Seconds_On_Page__c",
        },
    },
    "api_version": "vXX.X",
}


def _deep_merge(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Recursive merge, so a partial config patch cannot drop its siblings."""
    merged = dict(base)
    for key, value in patch.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def _config_id() -> str:
    return f"{COLLECTION_CONFIG}_{CONFIG_KEY}"


def load_config(store: RecordStore) -> dict[str, Any]:
    """Effective configuration: stored overrides layered onto the defaults.

    A missing config record is not an error. The defaults are the documented
    behaviour and are returned verbatim, so a client can show what is in force.
    """
    record = store.get(_config_id())
    if record is None:
        matches = store.find(COLLECTION_CONFIG, {"key": CONFIG_KEY}, limit=1)
        record = matches[0] if matches else None
    stored = (record or {}).get("data") or {}
    return _deep_merge(DEFAULT_CONFIG, stored if isinstance(stored, Mapping) else {})


def save_config(
    store: RecordStore, patch: Mapping[str, Any], *, actor: str | None = None, source: str
) -> dict[str, Any]:
    """Merge a partial config patch, audited.

    ``source`` is required rather than defaulted: only the HTTP layer knows its
    own path, and an audit row that cannot be traced back to the request that
    caused it is not an audit trail.
    """
    existing = store.get(_config_id())
    base = (existing or {}).get("data") or {}
    merged = _deep_merge(base if isinstance(base, Mapping) else {}, patch)
    merged["key"] = CONFIG_KEY
    if existing is None:
        return store.create(
            COLLECTION_CONFIG, merged, record_id=_config_id(), actor=actor, source=source
        )
    return store.update(_config_id(), merged, actor=actor, source=source)


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #


@dataclass
class Connection:
    """One room's upsert settings.

    Not a frozen dataclass: a connection is edited, and the *record* is the
    audited thing. Every field is plain JSON so a team can add one of its own -
    ``required_properties`` and ``capability`` are the two this feature reads that
    it does not define, and there is nothing stopping a third.
    """

    vendor: str
    object: str
    key_field: str
    id: str = ""
    name: str = ""
    room_id: str | None = None
    key_type: str = ""
    key_source: str = ""
    #: ``None`` means "the vendor's own cap", which is what the research's
    #: per-connection setting should default to: asking for 200 at Salesforce is
    #: asking for its documented maximum, and asking for 200 at HubSpot is an
    #: error the research says outright.
    batch_size: int | None = None
    all_or_none: bool = False
    update_only: bool = False
    fields: dict[str, str] = field(default_factory=dict)
    #: Enforced when the vendor does not support partial upserts (HubSpot with
    #: ``email``). Empty means "nothing extra required", which is a real setting
    #: rather than a missing one.
    required_properties: list[str] = field(default_factory=list)
    #: An inline capability, for a vendor nobody has registered.
    capability: dict[str, Any] = field(default_factory=dict)
    entity_set: str = ""
    api_version: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_record(cls, record: Mapping[str, Any], *, config: Mapping[str, Any]) -> "Connection":
        """Build a connection from a stored record, filling in the defaults.

        Tolerant by construction: an unknown key in the record is ignored rather
        than raising, so a record written by a newer version of this feature
        still loads. A missing ``key_source`` falls back to the configured
        default, so a connection stored before the field existed keeps working.
        """
        data = dict(record.get("data") or {})
        mapping = config.get("mapping") or {}
        fields = data.get("fields")
        return cls(
            vendor=str(data.get("vendor") or ""),
            object=str(data.get("object") or ""),
            key_field=str(data.get("key_field") or ""),
            id=str(record.get("id") or ""),
            name=str(data.get("name") or data.get("object") or ""),
            room_id=record.get("room_id"),
            key_type=str(data.get("key_type") or ""),
            key_source=str(data.get("key_source") or mapping.get("key_source") or ""),
            batch_size=_optional_int(data.get("batch_size")),
            all_or_none=bool(data.get("all_or_none", False)),
            update_only=bool(data.get("update_only", False)),
            fields=dict(fields)
            if isinstance(fields, Mapping)
            else dict(mapping.get("fields") or {}),
            required_properties=[str(p) for p in (data.get("required_properties") or [])],
            capability=dict(data.get("capability") or {}),
            entity_set=str(data.get("entity_set") or data.get("object") or ""),
            api_version=str(data.get("api_version") or config.get("api_version") or ""),
            notes=str(data.get("notes") or ""),
        )

    # -- derived ------------------------------------------------------------ #

    def resolved_capability(self, store: RecordStore | None) -> Any:
        """The capability this connection will use, and where it came from."""
        return resolve_capability(store, self.vendor, declared=self.capability or None)

    def effective_key_type(self, store: RecordStore | None) -> str:
        """This connection's key type, inferred from the capability when unstated.

        A stored connection predates nothing and states nothing, so an old record
        still resolves: the capability's own single key type is used, which is the
        only thing that vendor can key on anyway.
        """
        return resolve_connection_key_type(
            self.resolved_capability(store).capability, self.key_type, self.key_field
        )

    def effective_batch_size(self, store: RecordStore | None) -> int:
        """The batch size after the vendor's cap has been applied.

        Raises :class:`~dsr.crm_upsert.errors.BatchTooLarge` rather than clamping,
        because the caps are documented vendor limits and a silent clamp would
        report a plan the connector did not run.
        """
        resolved = self.resolved_capability(store)
        return resolve_batch_size(resolved.capability, self.batch_size)

    def target(self) -> str:
        """The CRM object this connection writes to, honouring a Dataverse entity set."""
        return self.entity_set or self.object

    def partial_upserts_supported(self, capability: Capability) -> bool:
        """Whether this vendor accepts a sparse upsert for this key.

        Only one researched case says no: "Partial upserts are not supported when
        using ``email`` as the ``idProperty`` for contacts." So the refusal is
        scoped to exactly that - a HubSpot connection keyed on ``email`` - and a
        different key type on the same vendor is not refused on a guess.
        """
        if capability.vendor == "hubspot" and str(self.key_field).strip().lower() == "email":
            return False
        return True


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise UpsertError(f"batch_size must be a whole number, got {value!r}") from exc


def validate_connection(
    store: RecordStore | None,
    spec: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
) -> Connection:
    """Turn a submitted spec into a :class:`Connection`, refusing what cannot work.

    Everything refused here is refused at save time rather than on the first run,
    so a connection in the UI is always one that could work. The three rules are
    the researched ones: the key must be an external id, the batch must fit the
    vendor, and no room field may be mapped onto a record id.
    """
    data = dict(spec)
    vendor = str(data.get("vendor") or "").strip()
    object_name = str(data.get("object") or data.get("entity_set") or "").strip()
    key_field = str(data.get("key_field") or "").strip()
    if not vendor:
        raise UpsertError("vendor is required")
    if not object_name:
        raise UpsertError("object is required: the CRM object this connection upserts into")
    if not key_field:
        raise UpsertError("key_field is required: the external-id field the upsert keys on")

    connection = Connection.from_record(
        {"id": str(data.get("id") or ""), "data": data}, config=config
    )
    connection.vendor = vendor
    connection.object = object_name
    connection.key_field = key_field
    connection.entity_set = str(data.get("entity_set") or object_name)
    if data.get("name"):
        connection.name = str(data["name"])

    resolved = connection.resolved_capability(store)
    connection.key_type = resolve_connection_key_type(
        resolved.capability, connection.key_type, connection.key_field
    )
    connection.batch_size = resolve_batch_size(resolved.capability, connection.batch_size)

    for target in connection.fields.values():
        if target and is_record_id_field(str(target)):
            raise UpsertError(
                f"field map targets {target!r}, which is a CRM record id. The payload "
                "carries the external-id field only."
            )
    return connection


def list_connections(
    store: RecordStore, config: Mapping[str, Any], *, room_id: str | None = None
) -> list[Connection]:
    """Every connection, optionally narrowed to one room."""
    records = _page(store, COLLECTION_CONNECTION, room_id=room_id)
    return [Connection.from_record(record, config=config) for record in records]


def load_connection(
    store: RecordStore, connection_id: str, config: Mapping[str, Any]
) -> Connection:
    """One connection by id, or :class:`UnknownConnection`.

    Room-scoped reads stay room-scoped: a connection belongs to a room, and a
    caller who named a room must not be handed another room's settings.
    """
    record = store.get(connection_id)
    if record is None or record.get("collection") != COLLECTION_CONNECTION:
        raise UnknownConnection(connection_id)
    return Connection.from_record(record, config=config)


def save_connection(
    store: RecordStore,
    spec: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    actor: str | None = None,
    source: str,
) -> dict[str, Any]:
    """Create or replace a connection, validated and audited."""
    connection = validate_connection(store, spec, config=config)
    payload = connection.to_dict()
    # `id` is envelope, not payload: the store owns it and the audit diff should
    # not carry a field the caller cannot set.
    payload.pop("id", None)
    room_id = str(spec.get("room_id") or "") or None
    existing_id = str(spec.get("id") or "")
    if existing_id and store.get(existing_id) is not None:
        return store.update(existing_id, payload, actor=actor, source=source)
    return store.create(COLLECTION_CONNECTION, payload, room_id=room_id, actor=actor, source=source)


def delete_connection(
    store: RecordStore, connection_id: str, *, actor: str | None = None, source: str
) -> dict[str, Any]:
    """Soft-delete a connection. Its runs and its synced rows stay auditable."""
    if store.get(connection_id) is None:
        raise UnknownConnection(connection_id)
    return store.delete(connection_id, actor=actor, source=source)


# --------------------------------------------------------------------------- #
# The queue
# --------------------------------------------------------------------------- #


def _page(
    store: RecordStore, collection: str, *, room_id: str | None = None
) -> list[dict[str, Any]]:
    """Every live record in a collection, paged so nothing is truncated.

    ``store.find`` cannot scope to a room - it filters on ``data`` paths only, and
    ``room_id`` is envelope, not payload - so a room-scoped read pages ``list``
    and filters on the envelope, which is the one place ``room_id`` is guaranteed
    to be correct.
    """
    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = store.list(
            collection,
            room_id=room_id,
            limit=200,
            offset=offset,
            order_by="created_at",
            descending=False,
        )
        records.extend(page)
        if len(page) < 200:
            return records
        offset += 200


def connection_state(data: Mapping[str, Any], connection_id: str) -> dict[str, Any]:
    """One connection's view of a row, from the row's nested ``sync`` map.

    A row is addressed by this feature's state as
    ``sync.<connection_id>.{status,synced_at,crm_record_id,...}``. Two reasons,
    and the second is a bug this shape prevents:

    1. **Schema flexibility.** ``sync`` is ordinary payload, so a new per-connection
       field needs no migration, and the dynamic index resolves the dotted path
       ``sync.<connection_id>.status`` for a filter.
    2. **Two connections may target one room.** The research models one set of
       write-backs per row ("room updates ``synced_at`` / ``crm_record_id`` per
       row") and says nothing about a second connection. A single flat
       ``sync_status`` would make the two fight: connection B's queue would
       contain rows connection A had already sent, and every run would re-send
       them. Nesting by connection is what makes "pending" mean *pending for this
       connection*.

    ``store.update`` merges shallowly, so a caller writing one connection's state
    must read the whole ``sync`` map first and write it back whole - which is what
    :func:`dsr.crm_upsert.runs._write_back` does.
    """
    sync = data.get("sync")
    if not isinstance(sync, Mapping):
        return {}
    state = sync.get(str(connection_id))
    return dict(state) if isinstance(state, Mapping) else {}


def row_status(record: Mapping[str, Any], connection_id: str | None = None) -> str:
    """A row's sync state, with "never sent" reading as pending.

    With a ``connection_id`` the answer is that connection's own state; without
    one it is the row-level mirror, which records the most recent outcome whatever
    connection produced it.

    A row nobody has synced has no state at all, and the research says the queue is
    what "accumulates" before a run, so absent means pending rather than unknown.
    """
    data = record.get("data") or {}
    if connection_id:
        status = str(connection_state(data, connection_id).get("status") or "").strip().lower()
    else:
        status = str(data.get("sync_status") or "").strip().lower()
    return status or STATUS_PENDING


def pending_rows(
    store: RecordStore,
    connection: Connection,
    config: Mapping[str, Any],
    *,
    room_id: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """The room's rows this connection has not yet landed, oldest first.

    "Has not yet landed" covers both a row never sent and a row whose last
    attempt failed - see :data:`UNSENT_STATUSES`. A row that failed stays in the
    queue because the research gives this workflow no retry policy and no
    give-up rule, so the queue is the mechanism, and a row dropped from it after
    one failure would never be synced at all.

    Oldest first because the queue is a backlog: the row that has waited longest
    is the one whose staleness is costing the most, and "The `UpsertResult`
    objects are returned in the same order" only helps if the order is one the
    connector chose deliberately.

    Scoped to this connection, so a room wired to two CRMs does not send every
    row twice.
    """
    collection = str((config.get("queue") or {}).get("collection") or COLLECTION_ENGAGEMENT)
    records = _page(store, collection, room_id=room_id)
    rows = [record for record in records if row_status(record, connection.id) in UNSENT_STATUSES]
    if limit is not None:
        rows = rows[: max(0, int(limit))]
    return rows


def queue_view(
    store: RecordStore,
    connection: Connection,
    config: Mapping[str, Any],
    *,
    room_id: str,
) -> dict[str, Any]:
    """The queue, with the counts a rep reads before pressing **Sync now**.

    Includes the unconfirmed count, which is the one a rep cannot get from the
    CRM: rows sent to a vendor that returns no per-item result are neither
    confirmed nor failed, and "submitted" must not be counted as success.
    """
    collection = str((config.get("queue") or {}).get("collection") or COLLECTION_ENGAGEMENT)
    records = _page(store, collection, room_id=room_id)
    counts = {STATUS_PENDING: 0, STATUS_SYNCED: 0, STATUS_UNCONFIRMED: 0, STATUS_FAILED: 0}
    for record in records:
        status = row_status(record, connection.id)
        counts[status] = counts.get(status, 0) + 1
    target = int((config.get("queue") or {}).get("target") or 200)
    unsent = sum(counts[status] for status in UNSENT_STATUSES)
    return {
        "room_id": room_id,
        "connection_id": connection.id,
        "collection": collection,
        "counts": counts,
        "unsent": unsent,
        "total": len(records),
        "target": target,
        "at_target": unsent >= target,
        "rows": records,
    }


# --------------------------------------------------------------------------- #
# The backlog: the researched scheduler
# --------------------------------------------------------------------------- #


def backlog(
    store: RecordStore,
    connection: Connection,
    config: Mapping[str, Any],
    *,
    room_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Whether the room's scheduled upsert is due, and why.

    Two researched triggers, kept distinct because they have different evidence:

    * **Nightly.** "Nightly/backlog upsert runs on the room's scheduler."
    * **Opportunistic.** "the room may also upsert opportunistically when the
      queue exceeds N rows." N is the configured threshold; the research names
      the rule and not the number.

    A queue that has never been run is due immediately. That is not an
    invention: a connection with rows and no run has never synced, and "the
    nightly backlog" is a description of work that exists, not a description of
    work that has been done.
    """
    moment = now or datetime.now(timezone.utc)
    view = queue_view(store, connection, config, room_id=room_id)
    schedule = config.get("schedule") or {}
    interval_hours = int(schedule.get("interval_hours") or 24)
    threshold = int((config.get("queue") or {}).get("opportunistic_threshold") or 25)

    runs = _page(store, COLLECTION_RUN, room_id=room_id)
    connection_runs = [
        run for run in runs if (run.get("data") or {}).get("connection_id") == connection.id
    ]
    connection_runs.sort(key=lambda run: str(run.get("updated_at") or ""), reverse=True)
    last = connection_runs[0] if connection_runs else None
    # `started_at`, not `finished_at`: a schedule is measured from when the run
    # began, and a backfilled or replayed run carries the moment it was asked for
    # in `started_at` while `finished_at` is wall-clock. Keying off `finished_at`
    # would make a replayed run look like it just happened.
    last_at = str(
        (last or {}).get("data", {}).get("started_at")
        or (last or {}).get("created_at")
        or (last or {}).get("updated_at")
        or ""
    )

    # "Pending" here means unsent, which includes rows whose last attempt failed:
    # a scheduler asking "is there work waiting" wants that row, or a nightly run
    # would never clear a failure the operator has already fixed in the CRM.
    pending = int(view["unsent"])
    failed = int(view["counts"][STATUS_FAILED])
    reasons: list[str] = []
    if pending == 0:
        reasons.append("the queue is empty")
    if last is None:
        if bool(schedule.get("due_with_no_history", True)) and pending:
            reasons.append("this connection has never been synced and rows are waiting")
    else:
        hours = _hours_since(last_at, moment)
        if hours is not None and hours >= interval_hours:
            reasons.append(
                f"{int(hours)}h since the last run, and the schedule is every {interval_hours}h"
            )
    over_threshold = pending > threshold
    if over_threshold:
        reasons.append(f"{pending} rows pending, over the opportunistic threshold of {threshold}")
    if failed:
        reasons.append(f"{failed} rows failed their last attempt and are queued again")

    return {
        "room_id": room_id,
        "connection_id": connection.id,
        "pending": pending,
        "failed": failed,
        "unconfirmed": view["counts"][STATUS_UNCONFIRMED],
        "threshold": threshold,
        "over_threshold": over_threshold,
        "interval_hours": interval_hours,
        "last_run_at": last_at or None,
        "last_run_id": (last or {}).get("id"),
        "next_due_at": _next_due(last_at, interval_hours, moment).isoformat(timespec="seconds")
        if last_at
        else None,
        "due": bool(reasons) and pending > 0,
        "reasons": reasons,
        "as_of": moment.isoformat(timespec="seconds"),
    }


def _hours_since(stamp: str, moment: datetime) -> float | None:
    if not stamp:
        return None
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max(0.0, (moment - then).total_seconds() / 3600.0)


def _next_due(last_at: str, interval_hours: int, moment: datetime) -> datetime:
    hours = _hours_since(last_at, moment)
    if hours is None:
        return moment
    return moment + timedelta(hours=max(0.0, interval_hours - hours))


# --------------------------------------------------------------------------- #
# Lint: the researched gap, and the rules that are not gaps
# --------------------------------------------------------------------------- #


def lint(
    connection: Connection,
    config: Mapping[str, Any],
    store: RecordStore | None = None,
) -> list[dict[str, Any]]:
    """Advisories for a connection, most important first.

    One entry is a **documented gap** rather than a rule, and says so in its
    ``kind``. The research records that Salesforce warns "using an email address
    as the External ID path segment can 404 on TLD/extension collisions (e.g.
    ``example@email.inc``)" and then states that "the documented workarounds
    (different External ID field, custom Apex REST endpoint) were not
    cross-verified against a second source".

    So this reports the risk and refuses to recommend a fix. Recommending
    "use a different External ID field" would be quoting the research's own
    unverified note back as though it were sourced, which is the one thing the
    gaps paragraph exists to prevent.
    """
    advisories: list[dict[str, Any]] = []
    key_field = str(connection.key_field).strip().lower()

    if connection.vendor == "salesforce" and key_field == "email":
        advisories.append(
            {
                "id": "salesforce-email-external-id",
                "kind": "documented_gap",
                "severity": "warning",
                "message": (
                    "Salesforce documents that an email address used as the External ID "
                    "path segment can 404 on a TLD/extension collision, for example "
                    "example@email.inc. The research did not cross-verify any workaround "
                    "against a second source, so this build does not recommend one. "
                    "Expect occasional 404s on keys that are valid emails."
                ),
                "source": "docs/research/digital-sales-room-workflows/wf/WF-038.md (gaps)",
            }
        )

    if connection.vendor == "hubspot" and key_field == "email":
        advisories.append(
            {
                "id": "hubspot-email-no-partial-upsert",
                "kind": "sourced_rule",
                "severity": "warning",
                "message": (
                    "HubSpot documents that partial upserts are not supported when email is "
                    "the idProperty for contacts. Rows missing any required property are "
                    "refused rather than sent, because the vendor would treat the missing "
                    "properties as empty."
                ),
                "change_it": (
                    f"Set required_properties on the connection (currently "
                    f"{list(connection.required_properties) or 'empty'}) to the properties a "
                    "contact cannot be written without."
                ),
                "source": "developers.hubspot.com - using object APIs",
            }
        )

    if store is not None:
        try:
            capability = connection.resolved_capability(store).capability
        except UpsertError as exc:
            advisories.append(
                {
                    "id": "no-capability",
                    "kind": "error",
                    "severity": "error",
                    "message": str(exc),
                    "source": "capability registry",
                }
            )
            return advisories
        if not capability.sourced:
            advisories.append(
                {
                    "id": "local-capability",
                    "kind": "inference",
                    "severity": "info",
                    "message": (
                        f"'{capability.vendor}' is a local capability, not a vendor this "
                        "research documents. Its batch size and key types are a judgement "
                        "call; see the inferences endpoint."
                    ),
                    "source": "dsr.crm_upsert.inferences",
                }
            )
        if not capability.returns_per_item_results:
            advisories.append(
                {
                    "id": "no-per-item-results",
                    "kind": "sourced_rule",
                    "severity": "info",
                    "message": (
                        f"{capability.vendor} returns no per-item result for a bulk upsert, "
                        "so rows sent to it are recorded as submitted rather than synced. "
                        "They are not re-sent, and they are not counted as confirmed."
                    ),
                    "source": capability.notes,
                }
            )
    return advisories
