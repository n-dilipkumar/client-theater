"""The façade the HTTP layer calls, and the only place that writes.

:class:`ChangeStreamEngine` owns six collections and the in-memory buffer set
that goes with them. Nothing else in this package touches a store, which is what
makes the domain rules testable on their own and makes the audit trail the
product promises possible: every write below carries a ``source`` that the route
supplied, and none of them carries a URL string of its own.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.change_stream import (
    channels as channel_rules,
    dataverse as dataverse_rules,
    events as event_rules,
    hubspot as hubspot_rules,
    inferences as inference_rules,
    replica as replica_rules,
    usage as usage_rules,
    vocabulary,
)
from dsr.change_stream.buffering import BufferSet, TransactionBuffer
from dsr.change_stream.errors import (
    CdcNotEnabled,
    ChangeStreamError,
    ChannelInUse,
    DataverseError,
    DuplicateChannelName,
    DuplicateOrg,
    EditionDoesNotSupportCdc,
    EntityNotOnChannel,
    MalformedEvent,
    NoOutstandingFetchRequest,
    OrgError,
    SubscriptionClosed,
    UnknownChannel,
    UnknownOrg,
    UnknownSubscription,
    UnknownTable,
)
from dsr.change_stream.fieldmap import field_map_findings, normalise_field_map, sync_key_field
from dsr.store import RecordStore

#: The collections this feature owns. Named, so a test can assert that nothing
#: here writes into a collection another workflow owns.
#:
#: ``crm_change_subscription`` rather than the shorter ``crm_subscription``,
#: because WF-016 already owns a collection called that one for its webhook
#: subscriptions. Two features writing into one collection is not caught by the
#: route-collision check - they are different routes - and the symptom is a
#: feature's listing showing rows it did not write, which is worse than a
#: failure because it looks like data.
ORGS = "crm_org"
CHANNELS = "crm_channel"
SUBSCRIPTIONS = "crm_change_subscription"
CHANGE_EVENTS = "crm_change_event"
#: Qualified rather than the neutral `crm_replica` that WF-045's backfill engine
#: already owns on main. Both features replicate CRM records into a room, by
#: different mechanisms, so the neutral name belongs to the one that landed
#: first. Two features writing one collection is invisible to the host's route
#: check and shows up as one feature's listing containing the other's rows.
REPLICA = "crm_change_stream_replica"
INVALIDATIONS = "crm_panel_invalidation"
TABLES = "dataverse_table"
HUBSPOT_SUBSCRIPTIONS = "hubspot_webhook_subscription"

#: Every collection this engine writes to, for the summary and for a test.
OWNED_COLLECTIONS: tuple[str, ...] = (
    ORGS,
    CHANNELS,
    SUBSCRIPTIONS,
    CHANGE_EVENTS,
    REPLICA,
    INVALIDATIONS,
    TABLES,
    HUBSPOT_SUBSCRIPTIONS,
)


class ChangeStreamEngine:
    """Every read and write for WF-043, over one audited store.

    Constructed per request by the feature module rather than stored on
    ``app.state``: the engine holds nothing but the store handle and its buffers,
    and an ``app.state`` entry is exactly the edit to the shared ``dsr/api.py``
    that the plugin host exists to make unnecessary.
    """

    def __init__(self, store: RecordStore) -> None:
        self.store = store
        # Parked transactions are process-wide, not per engine: see
        # ``BufferSet._registry`` in dsr/change_stream/buffering.py. The engine is
        # built per request, so a buffer held here would be discarded between the
        # event that parked a change and the event that closes its transaction.
        self._api_calls: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ #
    # Vocabulary and inferences
    # ------------------------------------------------------------------ #

    def vocabulary(self) -> dict[str, Any]:
        """Every published vocabulary, served as data.

        A client renders its pickers from this rather than from a list compiled
        into a page, so a value added here reaches every client at once, and a
        caller asking "what does this accept" gets an answer rather than a 400.
        """
        payload = vocabulary.describe()
        payload["hubspot"] = dict(
            payload["hubspot"],
            **{
                "direction": hubspot_rules.describe()["direction"],
                "fired_by": hubspot_rules.describe()["fired_by"],
            },
        )
        payload["usage"] = usage_rules.describe_metric_source()
        payload["fields"] = {
            "change_event": [
                "changeType",
                "transactionKey",
                "sequenceNumber",
                "commitTimestamp",
                "changedFields",
                "payload",
                "enrichedFields",
                "entity",
            ],
            "field_map": ["sync_key", "sync_key_field", "account_field", "fields"],
        }
        return payload

    def inferences(self) -> dict[str, Any]:
        """Every judgement call this workflow rests on, and how to change each one."""
        return inference_rules.describe()

    # ------------------------------------------------------------------ #
    # Orgs, and the edition gate
    # ------------------------------------------------------------------ #

    def orgs(self) -> list[dict[str, Any]]:
        return self.store.list(ORGS, limit=500)

    def org(self, org_id: str) -> dict[str, Any] | None:
        record = self.store.get(org_id)
        if record is None or record["collection"] != ORGS:
            return None
        return record

    def require_org(self, org_id: str) -> dict[str, Any]:
        record = self.org(org_id)
        if record is None:
            raise UnknownOrg(f"org {org_id} not found")
        return record

    def register_org(
        self, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Register a connected CRM org, with the edition the gate reads.

        One record per system, because the edition gate is a property of the org
        and two records for one system would give it two answers. The edition is
        declared, not detected - see ``edition-is-declared-not-detected``.
        """
        system = str(payload.get("system") or "").strip().lower()
        if system not in vocabulary.CRM_SYSTEMS:
            raise OrgError(
                f"system must be one of {', '.join(vocabulary.CRM_SYSTEMS)}; got {payload.get('system')!r}"
            )
        existing = self.store.find(ORGS, {"system": system})
        if existing:
            raise DuplicateOrg(
                f"a {system} org is already registered as {existing[0]['id']}. The edition gate "
                "is a property of the org, so there is one row per system."
            )
        edition = str(payload.get("edition") or "").strip()
        if not edition:
            raise OrgError(
                "edition is required. Change Data Capture is 'Available in: Enterprise, "
                "Performance, Unlimited, and Developer editions', and a room has to say which "
                "one it is on for that gate to mean anything."
            )
        record = self.store.create(
            ORGS,
            {
                "system": system,
                "edition": edition,
                "edition_covers_cdc": vocabulary.edition_supports_cdc(edition),
                "cdc_enabled": False,
                "cdc_entities": [],
                "org_name": str(payload.get("org_name") or ""),
                "call_budget": {
                    "limit": int(payload.get("call_budget") or vocabulary.DEFAULT_API_CALL_BUDGET),
                    "spent": 0,
                },
            },
            actor=actor,
            source=source,
        )
        return record

    def patch_org(
        self,
        org_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Enable Change Data Capture, or say why this org cannot.

        This is user-flow step one: "An operator enables Change Data Capture for
        the objects the room cares about". The edition gate is the researched
        part, and it is a refusal rather than a flag, because an org that sets
        the flag and then subscribes to nothing is a room that looks configured
        and is not.
        """
        record = self.require_org(org_id)
        data = dict(record["data"])
        patch: dict[str, Any] = {}

        if "cdc_enabled" in payload:
            requested = bool(payload.get("cdc_enabled"))
            if requested and not bool(data.get("cdc_enabled")):
                if not vocabulary.edition_supports_cdc(data.get("edition")):
                    raise _edition_error(data.get("edition"))
                entities = [
                    str(name)
                    for name in (payload.get("entities") or data.get("cdc_entities") or [])
                ]
                if not entities:
                    raise OrgError(
                        "entities is required to enable Change Data Capture: the room has to say "
                        "which objects it cares about, because a channel is 'a stream of change "
                        "events that correspond to one or more entities'"
                    )
                patch["cdc_enabled"] = True
                patch["cdc_entities"] = entities
            elif requested:
                patch["cdc_enabled"] = True
                if payload.get("entities"):
                    patch["cdc_entities"] = [str(name) for name in payload["entities"]]
            else:
                # Not researched: nothing in the workflow turns CDC off, and refusing would
                # invent a rule. Allowed, and the reasoning is served at /inferences.
                patch["cdc_enabled"] = False

        for key in ("edition", "org_name"):
            if key in payload:
                patch[key] = str(payload.get(key) or "")
        if "edition" in patch:
            patch["edition_covers_cdc"] = vocabulary.edition_supports_cdc(patch["edition"])

        if not patch:
            raise OrgError("nothing to change: send cdc_enabled, entities, edition or org_name")
        return self.store.update(record["id"], patch, actor=actor, source=source)

    def require_cdc(self, org: Mapping[str, Any]) -> None:
        """Refuse anything that streams from an org that never enabled CDC."""
        if not bool((org.get("data") or {}).get("cdc_enabled")):
            raise CdcNotEnabled(
                f"Change Data Capture is not enabled on the {org.get('data', {}).get('system')!r} "
                "org. That is the first step of the researched flow: an operator enables it for "
                "the objects the room cares about, and then a channel is a stream of change "
                "events that correspond to one or more entities."
            )

    # ------------------------------------------------------------------ #
    # Channels
    # ------------------------------------------------------------------ #

    def channels(
        self, *, name: str | None = None, org_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Every channel, optionally narrowed to one org or one exact name.

        The name filter is a byte-for-byte comparison, because "The channel name
        is case-sensitive." A client that wants a case-insensitive search can
        ask for the list and fold the names itself; the API does not fold them on
        its behalf, because doing so would make a channel that reads as standard
        behave as custom.
        """
        records = self.store.list(CHANNELS, limit=500)
        if org_id:
            records = [
                r
                for r in records
                if str(r.get("room_id") or "") is not None
                and str((r["data"] or {}).get("org_id")) == str(org_id)
            ]
        if name:
            records = [r for r in records if str((r["data"] or {}).get("name")) == name]
        return [self.present_channel(record) for record in records]

    def channel(self, channel_id: str) -> dict[str, Any] | None:
        record = self.store.get(channel_id)
        if record is None or record["collection"] != CHANNELS:
            return None
        return self.present_channel(record)

    def require_channel(self, channel_id: str) -> dict[str, Any]:
        record = self.store.get(channel_id)
        if record is None or record["collection"] != CHANNELS:
            raise UnknownChannel(f"channel {channel_id} not found")
        return record

    def present_channel(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A channel row plus what a reader needs to judge it."""
        data = dict(record["data"])
        return {
            "id": record["id"],
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            **data,
            "field_map_findings": field_map_findings(data.get("field_map")),
            "enrichment_note": self._enrichment_note(data),
        }

    def _enrichment_note(self, data: Mapping[str, Any]) -> str:
        """Why this channel can or cannot carry enriched fields, in one sentence."""
        if list(data.get("enriched_fields") or []):
            return (
                f"{len(data['enriched_fields'])} enriched field(s), applied to update and delete "
                "events only."
            )
        if str(data.get("kind")) == "standard":
            return (
                f"{vocabulary.STANDARD_CHANNEL} is the standard channel, and enrichment on it is "
                "refused so other subscribers do not receive unchanged fields they did not expect."
            )
        transport = str(data.get("transport") or "")
        if not vocabulary.supports_enrichment(transport):
            return (
                f"Event enrichment is not supported on the {transport!r} transport. It is supported "
                "for Pub/Sub API, CometD (Streaming API), and event relays."
            )
        return (
            "No enriched fields. Add the field the room needs to resolve a record - the research's "
            "example is the external ID - to this custom channel."
        )

    def create_channel(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Create a channel, standard or custom, on a CDC-enabled org.

        The standard channel is a fixed fact rather than a choice: naming
        ``/data/ChangeEvents`` is what makes a channel standard, and a caller
        cannot relabel a custom channel as the standard one to get at the
        enrichment rule.
        """
        org_id = str(payload.get("org_id") or "")
        org = self.require_org(org_id)
        self.require_cdc(org)

        transport = str(payload.get("transport") or "pubsub").strip()
        normalised = channel_rules.normalise_channel(payload, org=org, transport=transport)

        siblings = [dict(r["data"]) for r in self.store.list(CHANNELS, limit=500)]
        if channel_rules.channel_name_taken(siblings, normalised["name"], org_id=org["id"]):
            raise DuplicateChannelName(
                f"channel name {normalised['name']!r} is already in use on this org "
                f"({', '.join(channel_rules.names_in_use(siblings, org_id=org['id']))}). The channel "
                "name is case-sensitive, so a name differing only in case is a different channel "
                "and would not be refused here."
            )

        record = self.store.create(CHANNELS, normalised, actor=actor, source=source)
        return self.present_channel(record)

    def patch_channel(
        self,
        channel_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Change a channel's entities, field map, or buffer size.

        Not its name and not its kind. The name is the identity the collision
        check and the enrichment rule both read, so renaming a channel in place
        would let a custom channel become "the standard channel" without ever
        passing the check that created it. Create a new channel instead.
        """
        record = self.require_channel(channel_id)
        patch: dict[str, Any] = {}

        if "entities" in payload:
            entities = [str(name) for name in (payload.get("entities") or []) if str(name).strip()]
            if not entities:
                raise UnknownChannel("entities must name at least one object")
            patch["entities"] = entities
        if "field_map" in payload:
            patch["field_map"] = normalise_field_map(payload["field_map"])
        if "buffer_bytes" in payload:
            try:
                size = int(payload["buffer_bytes"])
            except (TypeError, ValueError) as exc:
                raise UnknownChannel("buffer_bytes must be a whole number of bytes") from exc
            if size <= 0:
                raise UnknownChannel("buffer_bytes must be greater than zero")
            patch["buffer_bytes"] = size
            patch["buffer_matches_recommendation"] = size == vocabulary.RECOMMENDED_BUFFER_BYTES
        if not patch:
            raise UnknownChannel("nothing to change: send entities, field_map or buffer_bytes")
        return self.present_channel(
            self.store.update(record["id"], patch, actor=actor, source=source)
        )

    def delete_channel(
        self,
        channel_id: str,
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Soft-delete a channel, refusing one a live subscription is reading.

        A soft delete, so the cancellation is audited and the record of what was
        streamed outlives it. The refusal when a live subscription exists is
        because that subscription would keep buffering events against a channel
        nobody can see, and its commits would then be unattributable.
        """
        record = self.require_channel(channel_id)
        live = self.store.find(SUBSCRIPTIONS, {"channel_id": channel_id, "state": "open"})
        if live:
            raise ChannelInUse(
                f"channel {channel_id} has {len(live)} open subscription(s). Close them first: "
                "they would keep buffering events against a channel this API no longer serves, "
                "and the commits would name something nobody can read."
            )
        return self.store.delete(record["id"], actor=actor, source=source)

    def enrich_channel(
        self,
        channel_id: str,
        fields: Any,
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Add enriched fields to a custom channel.

        This is user-flow step five, and the researched remedy for the one error
        this workflow can most usefully refuse: an update event that does not
        carry the field the room resolves records on.
        """
        record = self.require_channel(channel_id)
        data = dict(record["data"])
        result = channel_rules.add_enrichment(
            data, fields, transport=str(data.get("transport") or "")
        )
        updated = self.store.update(
            record["id"],
            {"enriched_fields": result["enriched_fields"]},
            actor=actor,
            source=source,
        )
        presented = self.present_channel(updated)
        presented["added"] = result["added"]
        return presented

    def unenrich_channel(
        self,
        channel_id: str,
        field: str,
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Drop one enriched field from a channel."""
        record = self.require_channel(channel_id)
        result = channel_rules.remove_enrichment(dict(record["data"]), field)
        updated = self.store.update(
            record["id"],
            {"enriched_fields": result["enriched_fields"]},
            actor=actor,
            source=source,
        )
        presented = self.present_channel(updated)
        presented["removed"] = result["removed"]
        return presented

    # ------------------------------------------------------------------ #
    # Subscriptions
    # ------------------------------------------------------------------ #

    def subscriptions(
        self,
        *,
        room_id: str | None = None,
        channel_id: str | None = None,
        state: str | None = None,
    ) -> list[dict[str, Any]]:
        records = self.store.list(SUBSCRIPTIONS, room_id=room_id, limit=500)
        rows = []
        for record in records:
            data = dict(record["data"])
            if channel_id and str(data.get("channel_id")) != str(channel_id):
                continue
            if state and str(data.get("state")) != state:
                continue
            rows.append(self.present_subscription(record))
        return rows

    def subscription(self, subscription_id: str) -> dict[str, Any] | None:
        record = self.store.get(subscription_id)
        if record is None or record["collection"] != SUBSCRIPTIONS:
            return None
        return self.present_subscription(record)

    def require_subscription(self, subscription_id: str) -> dict[str, Any]:
        record = self.store.get(subscription_id)
        if record is None or record["collection"] != SUBSCRIPTIONS:
            raise UnknownSubscription(f"subscription {subscription_id} not found")
        return record

    def present_subscription(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record["data"])
        subscription_id = str(record["id"])
        buffers = self._buffer_set(subscription_id)
        return {
            "id": subscription_id,
            "room_id": record.get("room_id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            **data,
            "wire_format": data.get("wire_format")
            or vocabulary.wire_format_for(str(data.get("transport") or "")),
            "buffered": buffers.describe() if buffers else [],
            "usage": usage_rules.describe({**data, "id": subscription_id}),
        }

    def open_subscription(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Open a long-lived subscription on a channel.

        "The room's subscriber client opens a long-lived subscription (Pub/Sub
        API, or a delta-link poll loop)." A subscription starts with no
        outstanding FetchRequest, because the research publishes no default fetch
        size and the flow control is the client's to set.
        """
        channel_record = self.require_channel(str(payload.get("channel_id") or ""))
        channel = {**channel_record["data"], "id": channel_record["id"]}
        org = self.require_org(str(channel.get("org_id") or ""))
        self.require_cdc(org)

        transport = str(payload.get("transport") or channel.get("transport") or "pubsub").strip()
        if transport not in vocabulary.TRANSPORTS:
            raise ChangeStreamError(
                f"transport must be one of {', '.join(vocabulary.TRANSPORTS)}; got {transport!r}"
            )
        if transport != str(channel.get("transport") or transport):
            # A subscription cannot be a different transport from the channel it reads, and
            # allowing it would mean the enrichment rule was checked against one transport
            # and the events arrived over another.
            raise ChangeStreamError(
                f"channel {channel.get('name')!r} streams over {channel.get('transport')!r}; a "
                f"subscription on it cannot use {transport!r}. The transport decides whether "
                "enrichment is available, so it has to be the one the channel declared."
            )

        entity = str(payload.get("entity") or "").strip()
        if entity and entity not in {str(name) for name in channel.get("entities") or []}:
            raise EntityNotOnChannel(
                f"channel {channel.get('name')!r} streams "
                f"{sorted(str(name) for name in channel.get('entities') or [])}; a subscription on "
                f"it cannot read {entity!r}. Subscribe on a channel that streams it, or add it to "
                "this one's entities."
            )

        data: dict[str, Any] = {
            "channel_id": str(channel_record["id"]),
            "channel_name": str(channel.get("name")),
            "transport": transport,
            "wire_format": vocabulary.wire_format_for(transport),
            "state": "open",
            "entity": entity,
            "buffer_limit_bytes": int(
                channel.get("buffer_bytes") or vocabulary.RECOMMENDED_BUFFER_BYTES
            ),
            "usage": usage_rules.blank(),
            "fetch_outstanding": 0,
        }
        record = self.store.create(SUBSCRIPTIONS, data, room_id=room_id, actor=actor, source=source)
        self._buffer_set(str(record["id"]))
        return self.present_subscription(record)

    def fetch(
        self,
        subscription_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Record a FetchRequest: "the number of requested events" to come.

        "The client can control the flow of events received by setting the number
        of requested events in the FetchRequest parameter." The vendor's own
        field name is accepted as well, so a client written against the RPC shape
        works unchanged.
        """
        record = self.require_subscription(subscription_id)
        data = dict(record["data"])
        if str(data.get("state")) != "open":
            raise SubscriptionClosed(
                f"subscription {subscription_id} is closed. A subscription is long-lived and the "
                "research describes two states for it, so a closed one receives nothing; open a "
                "new one."
            )
        raw = payload.get("num_requested")
        if raw is None:
            raw = payload.get("numEvents")
        if raw is None:
            raise MalformedEvent(
                "num_requested is required: the client controls the flow of events received by "
                "setting the number of requested events in the FetchRequest parameter"
            )
        try:
            requested = int(raw)
        except (TypeError, ValueError) as exc:
            raise MalformedEvent(
                f"num_requested must be a whole number of events; got {raw!r}"
            ) from exc
        if requested <= 0:
            raise MalformedEvent("num_requested must be greater than zero")

        updated_usage = usage_rules.request_fetch(data.get("usage"), requested)
        updated = self.store.update(
            record["id"],
            {"usage": updated_usage, "fetch_outstanding": updated_usage["fetch_outstanding"]},
            actor=actor,
            source=source,
        )
        return self.present_subscription(updated)

    def close_subscription(
        self,
        subscription_id: str,
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Close a subscription, flushing whatever transaction is still parked.

        The drain the commit rule leaves open. "Only commits to the room's local
        replica when the key changes", so the last transaction in a stream has no
        successor - and dropping it would silently lose a change.

        A flush that cannot be applied refuses the close and leaves the
        subscription **open**, with the transaction still parked. The alternative
        - closing anyway - would strand a change the room had already accepted,
        and the researched remedy (enrich the field the room needs to resolve the
        record) is something the room can only do while it still has a
        subscription to commit on.
        """
        record = self.require_subscription(subscription_id)
        data = dict(record["data"])
        if str(data.get("state")) != "open":
            return {
                "subscription": self.present_subscription(record),
                "flushed": [],
                "already_closed": True,
            }
        held = self._buffer_set(subscription_id)
        ready = held.flush()
        flushed: list[dict[str, Any]] = []
        if ready:
            # Raises if the transaction cannot be applied, and `restore_front`
            # has already put it back by then, so the subscription stays open
            # and the change stays committable.
            flushed = self._commit_many(
                ready,
                room_id=str(record.get("room_id") or ""),
                channel_id=str(data.get("channel_id") or ""),
                subscription_id=subscription_id,
                actor=actor,
                source=source,
            )
        updated = self.store.update(
            record["id"],
            {"state": "closed", "fetch_outstanding": 0},
            actor=actor,
            source=source,
        )
        BufferSet.release(subscription_id)
        return {
            "subscription": self.present_subscription(updated),
            "flushed": flushed,
            "already_closed": False,
            "reason": (
                "a subscription is long-lived, and the last transaction in a stream has no key "
                "change to commit it, so closing flushes it"
            ),
        }

    # ------------------------------------------------------------------ #
    # The event path: buffer, then commit
    # ------------------------------------------------------------------ #

    def _channel_data(self, channel_id: str) -> dict[str, Any]:
        """A channel's stored payload, with its record id folded in.

        The id is not in ``data`` - it is the record envelope - and the replica
        write needs it, because a replica row has to name the channel it came
        from or the lookup that finds it again would have to scan every room's
        rows to find out.
        """
        record = self.require_channel(channel_id)
        return {**record["data"], "id": record["id"]}

    def _buffer_set(self, subscription_id: str) -> BufferSet:
        """The parked transactions for one subscription, shared process-wide."""
        return BufferSet.for_subscription(subscription_id)

    def deliver_event(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """One change event from the room's subscriber client.

        Three decisions, in the order the research's data flow states them:
        deserialise, buffer under the transaction key, and commit whatever that
        key closed. The commit is applied before the response is written, so a
        caller that reads the replica straight after sees the transaction the
        arrival triggered.
        """
        subscription_id = str(payload.get("subscription_id") or "").strip()
        if not subscription_id:
            raise MalformedEvent(
                "subscription_id is required: a change event arrives on a subscription, and the "
                "FetchRequest that authorised it belongs to that subscription"
            )
        record = self.require_subscription(subscription_id)
        data = dict(record["data"])
        if str(data.get("state")) != "open":
            raise SubscriptionClosed(
                f"subscription {subscription_id} is closed and receives no events"
            )

        channel = self._channel_data(str(data.get("channel_id") or ""))

        event = event_rules.normalise_event(payload, room_id=room_id)
        event_rules.applies_to_entity(event, channel)

        # The flow control, before anything is written: an event the client did
        # not ask for is refused rather than counted, because counting it would
        # make the requested-versus-delivered numbers a lie.
        usage = usage_rules.merge(data.get("usage"))
        if int(usage.get("fetch_outstanding") or 0) < 1:
            raise NoOutstandingFetchRequest(
                f"subscription {subscription_id} has no FetchRequest outstanding. The Subscribe "
                "method uses bidirectional streaming, enabling the client to request more events "
                "as it consumes events, so an event delivered with nothing requested is one the "
                "client was not authorised to receive. Send a FetchRequest first."
            )

        stored = self.store.create(
            CHANGE_EVENTS,
            {
                **event,
                "channel_id": str(channel.get("id") or ""),
                "channel_name": str(channel.get("name")),
                "subscription_id": subscription_id,
                "state": vocabulary.EVENT_STATES[0],
                "enrichment_note": event_rules.enrichment_dropped_reason(event),
                "enriched_fields_effective": event_rules.enriched_fields_in_effect(event, channel),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        # Presented, not raw: the caller reads this row, and the envelope's
        # `data` nesting is a storage detail rather than part of the workflow.
        presented = self.present_event(stored)

        buffers = self._buffer_set(subscription_id)
        buffer, completed, outcome = buffers.accept(event)
        if not outcome.get("added"):
            self._patch_usage(
                subscription_id, usage_rules.record_duplicate(usage), actor=actor, source=source
            )
            return {
                "outcome": "duplicate",
                "reason": outcome.get("reason"),
                "sequence_number": outcome.get("sequence_number"),
                "event": presented,
                "buffer": buffer.describe(),
                "committed": [],
                "note": (
                    "this sequence number is already parked under this transactionKey, so the "
                    "event is not applied twice. The research says nothing about at-least-once "
                    "delivery, so this is reported rather than treated as a redelivery."
                ),
            }

        # Chained, not applied as two patches against the same snapshot: the
        # second write would otherwise undo the first, and the parked-transaction
        # counter would read zero on a stream that is plainly buffering.
        opened = len(buffer) == 1
        if opened:
            usage = usage_rules.record_buffered(usage)
        usage = usage_rules.record_delivery(usage, buffer_bytes=buffers.buffer_bytes())
        self._patch_usage(subscription_id, usage, actor=actor, source=source)

        committed = self._commit_many(
            completed,
            room_id=room_id,
            channel_id=str(channel.get("id") or ""),
            subscription_id=subscription_id,
            actor=actor,
            source=source,
        )

        return {
            "outcome": "buffered",
            "event": presented,
            "buffer": buffer.describe(),
            "buffered_transactions": buffers.keys(),
            "committed": committed,
            "commit_rule": (
                "a change is parked under its transactionKey and only commits to the room's "
                "replica when the key changes"
            ),
        }

    def _commit_many(
        self,
        buffers: Sequence[TransactionBuffer],
        *,
        room_id: str,
        channel_id: str,
        subscription_id: str,
        actor: str | None,
        source: str,
    ) -> list[dict[str, Any]]:
        if not buffers:
            return []
        held = self._buffer_set(subscription_id)
        # Checked once for the whole set, before the first write: a stream that
        # has been re-delivered would otherwise write an older change over a
        # newer one, and the replica would look healthy while doing it.
        try:
            held.assert_orderable(buffers)
            return [
                self._commit_one(
                    buffer,
                    room_id=room_id,
                    channel_id=channel_id,
                    subscription_id=subscription_id,
                    actor=actor,
                    source=source,
                )
                for buffer in buffers
            ]
        except ChangeStreamError:
            # A transaction that cannot be applied goes back where it was. The
            # research's rule is that a commit happens as a unit when the key
            # changes, so a transaction that fails must not be half-written and
            # must not be dropped either - it stays parked, its events stay in
            # the change log, and the room can fix the channel and try again.
            held.restore_front(buffers)
            raise

    def _commit_one(
        self,
        buffer: TransactionBuffer,
        *,
        room_id: str,
        channel_id: str,
        subscription_id: str,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Apply one parked transaction to the room's replica, as a unit.

        The unit matters. Every event's plan is decided *before* any of them is
        written, so a transaction whose fourth event cannot resolve its record
        leaves the replica exactly as it was rather than three-quarters applied -
        which is what the research's transaction key is for.
        """
        channel = self._channel_data(channel_id)
        events = buffer.ordered()

        plans: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for event in events:
            existing = self._find_replica(
                room_id,
                channel,
                replica_rules.resolve_external_id(
                    event.get("payload") or {},
                    event_rules.enriched_fields_in_effect(event, channel),
                    channel.get("field_map") or {},
                ),
            )
            plan = replica_rules.plan_event(event, channel, existing=existing)
            plans.append((plan, existing or {}))

        written: list[dict[str, Any]] = []
        for event, (plan, _planned_against) in zip(events, plans, strict=True):
            # Re-read rather than reuse the plan-phase row: two events in one
            # transaction can touch the same record, and the second has to see
            # what the first wrote.
            existing = self._find_replica(room_id, channel, plan["external_id"])
            merged = replica_rules.merge_into(existing, plan, event)
            findings = replica_rules.replica_findings(plan, channel)
            if existing is None:
                record = self.store.create(
                    REPLICA,
                    merged["data"],
                    room_id=room_id,
                    actor=actor,
                    source=source,
                )
            else:
                record = self.store.update(
                    existing["id"], merged["data"], actor=actor, source=source
                )
            written.append(
                {
                    "external_id": plan["external_id"],
                    "replica_id": str(record["id"]),
                    "action": plan["action"],
                    "state": merged["state"],
                    "change_type": plan["change_type"],
                    "changed_replica_fields": merged["changed_replica_fields"],
                    "enriched_fields_used": plan["enriched_fields_used"],
                    "resolution": plan["resolution"],
                    "unmapped_crm_fields": plan["unmapped_crm_fields"],
                    "findings": findings,
                }
            )

        invalidations = [
            self._invalidate(room_id, channel, row, buffer, source=source, actor=actor)
            for row in written
        ]

        self._mark_events_committed(events, buffer=buffer, source=source, actor=actor)

        usage = usage_rules.merge(self.require_subscription(subscription_id)["data"].get("usage"))
        self._patch_usage(
            subscription_id,
            usage_rules.record_commit(usage, events=len(events), writes=len(written)),
            actor=actor,
            source=source,
        )

        return {
            "transaction_key": buffer.key,
            "event_count": len(events),
            "sequence_numbers": [int(e["sequence_number"]) for e in events],
            "sequence_gaps": buffer.describe()["sequence_gaps"],
            "replica_writes": written,
            "invalidations": invalidations,
        }

    def _invalidate(
        self,
        room_id: str,
        channel: Mapping[str, Any],
        row: Mapping[str, Any],
        buffer: Any,
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """The buyer's deal panel is now stale, and by how much.

        "The room refreshes the affected buyer's deal panel" is the whole of what
        the research says a panel is, so the invalidation names the panel, the
        record, and - when the field map says which buyer it is - the account.
        Where the field map declares no account field the invalidation records
        ``resolved: false`` rather than picking one.
        """
        account = str(
            (self.store.get(str(row["replica_id"])) or {}).get("data", {}).get("account") or ""
        )
        resolved = bool(account)
        unresolved_note = (
            None
            if resolved
            else (
                "the channel's field_map declares no account_field, so this refresh is not "
                "addressed to a buyer. The research says the room refreshes the affected "
                "buyer's deal panel and not where the buyer comes from."
            )
        )
        record = self.store.create(
            INVALIDATIONS,
            {
                "panel": vocabulary.DEAL_PANEL,
                "external_id": row["external_id"],
                "account": account,
                "resolved": resolved,
                "unresolved_note": unresolved_note,
                "reason": "crm_change_committed",
                "channel_name": str(channel.get("name") or ""),
                "transaction_key": buffer.key,
                "change_type": row["change_type"],
                "replica_state": row["state"],
                "changed_replica_fields": row["changed_replica_fields"],
                "committed_at": str(buffer.describe()["last_commit_timestamp"] or ""),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return {
            "id": str(record["id"]),
            "panel": vocabulary.DEAL_PANEL,
            "external_id": row["external_id"],
            "account": account,
            "resolved": resolved,
            "unresolved_note": unresolved_note,
        }

    def _mark_events_committed(
        self,
        events: Sequence[Mapping[str, Any]],
        *,
        buffer: TransactionBuffer,
        source: str,
        actor: str | None,
    ) -> None:
        """Mark a parked transaction committed, and record what its order looked like.

        The sequence gap rides on the event rows rather than only on the commit
        response, because the response is one call long and the question "was this
        transaction's order complete?" is asked later, by whoever is reading the
        change log. Recorded, not repaired: reconciling a dropped stream is a
        different researched workflow.
        """
        described = buffer.describe()
        for event in events:
            matches = self.store.find(
                CHANGE_EVENTS,
                {
                    "transaction_key": str(event["transaction_key"]),
                    "sequence_number": int(event["sequence_number"]),
                },
            )
            for match in matches:
                if str((match["data"] or {}).get("state")) == vocabulary.EVENT_STATES[0]:
                    self.store.update(
                        match["id"],
                        {
                            "state": vocabulary.EVENT_STATES[1],
                            "sequence_gaps": described["sequence_gaps"],
                            "transaction_sequence_numbers": described["sequence_numbers"],
                        },
                        actor=actor,
                        source=source,
                    )

    def _patch_usage(
        self,
        subscription_id: str,
        usage: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> None:
        record = self.store.get(subscription_id)
        if record is None or record["collection"] != SUBSCRIPTIONS:
            return
        self.store.update(
            subscription_id,
            {"usage": dict(usage), "fetch_outstanding": int(usage.get("fetch_outstanding") or 0)},
            actor=actor,
            source=source,
        )

    def _find_replica(
        self,
        room_id: str,
        channel: Mapping[str, Any],
        external_id: str | None,
    ) -> dict[str, Any] | None:
        if not external_id:
            return None
        matches = self.store.find(
            REPLICA,
            {
                sync_key_field(channel.get("field_map") or {}): str(external_id),
                "channel_name": str(channel.get("name") or ""),
            },
        )
        for match in matches:
            if str(match.get("room_id") or "") == str(room_id or ""):
                return match
        return None

    # ------------------------------------------------------------------ #
    # Reads: the Live activity / Realtime panel, the replica, the panel
    # ------------------------------------------------------------------ #

    def events(
        self,
        *,
        room_id: str,
        change_type: str | None = None,
        transaction_key: str | None = None,
        state: str | None = None,
        entity: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """The room's Live activity / Realtime panel, newest first.

        Every filter is a JSON path in the event's own payload, resolved through
        the dynamic index, so a field a team added later is queryable without a
        change to this route.
        """
        where: dict[str, Any] = {}
        if change_type:
            where["change_type"] = vocabulary.normalise_change_type(change_type) or change_type
        if transaction_key:
            where["transaction_key"] = transaction_key
        if state:
            where["state"] = state
        if entity:
            where["entity"] = entity
        records = self.store.find(CHANGE_EVENTS, where, limit=min(int(limit), 1000))
        rows = [record for record in records if str(record.get("room_id") or "") == str(room_id)]
        rows.sort(
            key=lambda record: (
                str(record["data"].get("commit_timestamp") or ""),
                int(record["data"].get("sequence_number") or 0),
            ),
            reverse=True,
        )
        return [self.present_event(record) for record in rows]

    def present_event(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record["data"])
        return {
            "id": str(record["id"]),
            "room_id": record.get("room_id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            **data,
        }

    def event(self, event_id: str, *, room_id: str) -> dict[str, Any] | None:
        record = self.store.get(event_id)
        if record is None or record["collection"] != CHANGE_EVENTS:
            return None
        if str(record.get("room_id") or "") != str(room_id):
            return None
        return self.present_event(record)

    def buffer_view(self, *, room_id: str, subscription_id: str | None = None) -> dict[str, Any]:
        """What is parked right now, and why it has not committed.

        A subscription with nothing parked is a healthy idle stream, and the
        reason is on the response: a change is only committed by the arrival of a
        change under a different key, so an idle buffer is the expected state
        rather than a symptom.
        """
        rows = []
        target = subscription_id
        for record in self.store.list(SUBSCRIPTIONS, room_id=room_id, limit=500):
            if target and str(record["id"]) != str(target):
                continue
            buffers = self._buffer_set(str(record["id"]))
            presented = self.present_subscription(record)
            presented["parked"] = buffers.describe()
            presented["buffered_events"] = buffers.event_count()
            presented["buffer_bytes"] = buffers.buffer_bytes()
            rows.append(presented)
        return {
            "room_id": room_id,
            "count": len(rows),
            "commit_rule": (
                "a change is parked under its transactionKey and only commits to the room's "
                "replica when the key changes; the last transaction flushes when the "
                "subscription closes"
            ),
            "subscriptions": rows,
        }

    def replica(
        self,
        *,
        room_id: str,
        state: str | None = None,
        account: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """The room's local replica, as the buyer's deal panel would read it."""
        where: dict[str, Any] = {}
        if state:
            where["replica_state"] = state
        if account:
            where["account"] = account
        records = self.store.find(REPLICA, where, limit=min(int(limit), 1000))
        rows = [r for r in records if str(r.get("room_id") or "") == str(room_id)]
        rows.sort(key=lambda r: str(r["data"].get("last_commit_timestamp") or ""), reverse=True)
        return [replica_rules.deal_panel_row(r) for r in rows]

    def replica_row(self, *, room_id: str, external_id: str) -> dict[str, Any] | None:
        for row in self.replica(room_id=room_id, limit=1000):
            if str(row["fields"].get("external_id") or "") == str(external_id):
                return row
        return None

    def deal_panel(self, *, room_id: str) -> dict[str, Any]:
        """The buyer's deal panel, grouped by the account that owns each record.

        Grouped because a panel belongs to a buyer and a transaction can touch
        more than one. An ungrouped record is listed under its own external id
        rather than being dropped, so a channel with no account_field still
        produces a panel with something in it.
        """
        rows = self.replica(room_id=room_id, limit=1000)
        accounts: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            key = row["account"] or f"(unattributed) {row['fields'].get('external_id')}"
            accounts.setdefault(str(key), []).append(row)
        return {
            "room_id": room_id,
            "panel": vocabulary.DEAL_PANEL,
            "count": len(rows),
            "live": sum(1 for row in rows if row["state"] == "live"),
            "deleted": sum(1 for row in rows if row["state"] == "deleted"),
            "accounts": [
                {
                    "account": name,
                    "records": len(entries),
                    "last_commit_timestamp": max(
                        (str(entry["last_commit_timestamp"]) for entry in entries), default=""
                    ),
                    "rows": entries,
                }
                for name, entries in sorted(accounts.items())
            ],
        }

    def invalidations(self, *, room_id: str, limit: int = 200) -> list[dict[str, Any]]:
        records = self.store.list(INVALIDATIONS, room_id=room_id, limit=min(int(limit), 1000))
        return [
            {"id": str(r["id"]), "created_at": r.get("created_at"), **(r["data"] or {})}
            for r in records
        ]

    # ------------------------------------------------------------------ #
    # Dataverse
    # ------------------------------------------------------------------ #

    def tables(self) -> list[dict[str, Any]]:
        return [
            {"id": r["id"], "created_at": r.get("created_at"), **(r["data"] or {})}
            for r in self.store.list(TABLES, limit=500)
        ]

    def table(self, table_id: str) -> dict[str, Any] | None:
        record = self.store.get(table_id)
        if record is None or record["collection"] != TABLES:
            return None
        return {"id": record["id"], **(record["data"] or {})}

    def require_table(self, table_id: str) -> dict[str, Any]:
        record = self.store.get(table_id)
        if record is None or record["collection"] != TABLES:
            raise UnknownTable(f"table {table_id} not found")
        return record

    def declare_table(
        self, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Declare a Dataverse table. Change tracking starts off.

        Off, because turning it on is a step with an irreversible consequence and
        a room that never took that step should not be assumed to have.
        """
        data = dataverse_rules.normalise_table(payload)
        existing = [
            r
            for r in self.store.list(TABLES, limit=500)
            if str((r["data"] or {}).get("logical_name")) == data["logical_name"]
        ]
        if existing:
            raise _duplicate_table_error(data["logical_name"], existing[0]["id"])
        record = self.store.create(TABLES, data, actor=actor, source=source)
        return {
            "id": record["id"],
            **data,
            "annotation": None,
            "how_to_enable": (
                "In Power Apps, select Data > Tables and the specific table. Under Advanced "
                "options, you find the Track changes property."
            ),
        }

    def enable_track_changes(
        self, table_id: str, *, actor: str | None, source: str
    ) -> dict[str, Any]:
        record = self.require_table(table_id)
        result = dataverse_rules.enable_track_changes(dict(record["data"]))
        updated = self.store.update(
            record["id"],
            {
                "track_changes": True,
                "change_tracking_supported": True,
            },
            actor=actor,
            source=source,
        )
        return {
            "id": updated["id"],
            **(updated["data"] or {}),
            "already_enabled": result["already_enabled"],
            "annotation": dataverse_rules.track_changes_annotation(),
            "irreversible": True,
            "irreversible_note": (
                '"After you enable change tracking for a table, you can\'t disable it."'
            ),
        }

    def disable_track_changes(
        self, table_id: str, *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Always refuses. See :func:`dsr.change_stream.dataverse.refuse_disable`."""
        record = self.require_table(table_id)
        dataverse_rules.refuse_disable(dict(record["data"]))

    def poll_table(
        self,
        table_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """One delta-link poll, in the vendor's response shape.

        The delta link is advanced by the room, not chosen by the caller: a poll
        that carried no ``$deltatoken`` is a full read, and one that carried the
        table's current token is the incremental read the header asks for. What
        the room cannot do is invent a token, so an unknown token is used as sent
        and the response says whether it was the table's own.
        """
        record = self.require_table(table_id)
        data = dict(record["data"])
        options = {str(k): v for k, v in (payload.get("options") or {}).items()}
        observed = int(payload.get("changes_observed") or data.get("change_count") or 0)
        result = dataverse_rules.poll(
            data, prefer=payload.get("prefer"), options=options, observed_changes=observed
        )
        token = result["returned_deltatoken"]
        updated = self.store.update(
            record["id"],
            {
                "deltatoken": token,
                "delta_link": result["@odata.deltaLink"],
                "poll_count": int(data.get("poll_count") or 0) + 1,
                "change_count": int(data.get("change_count") or 0) + observed,
            },
            actor=actor,
            source=source,
        )
        return {
            "table_id": record["id"],
            "logical_name": updated["data"].get("logical_name"),
            **result,
        }

    def count_table(self, table_id: str, *, deltatoken: str | None) -> dict[str, Any]:
        record = self.require_table(table_id)
        return {
            "table_id": record["id"],
            **dataverse_rules.change_count(dict(record["data"]), deltatoken=deltatoken),
        }

    # ------------------------------------------------------------------ #
    # HubSpot
    # ------------------------------------------------------------------ #

    def hubspot_subscriptions(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        records = self.store.list(HUBSPOT_SUBSCRIPTIONS, room_id=room_id, limit=1000)
        return [
            {
                "id": r["id"],
                "room_id": r.get("room_id"),
                "created_at": r.get("created_at"),
                **(r["data"] or {}),
            }
            for r in records
        ]

    def hubspot_capacity(self) -> dict[str, Any]:
        live = self.store.list(HUBSPOT_SUBSCRIPTIONS, limit=1000)
        return {
            "live": len(live),
            "limit": hubspot_rules.WEBHOOK_SUBSCRIPTION_LIMIT,
            "remaining": max(0, hubspot_rules.WEBHOOK_SUBSCRIPTION_LIMIT - len(live)),
            "at_limit": len(live) >= hubspot_rules.WEBHOOK_SUBSCRIPTION_LIMIT,
            "source": '"You can create up to 1,000 webhook subscriptions per app."',
        }

    def register_hubspot_subscription(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Register a room's HubSpot workflow webhook target.

        The researched seam and nothing wider: "Webhooks can be triggered as an
        action in any workflow, so you can use any workflow starting conditions as
        the criteria." The subscription REST API is not built, because the
        research records that its documentation could not be read.
        """
        self.hubspot_require_capacity()
        data = hubspot_rules.normalise_subscription(payload)
        record = self.store.create(
            HUBSPOT_SUBSCRIPTIONS, data, room_id=room_id, actor=actor, source=source
        )
        return {
            "id": record["id"],
            "room_id": record.get("room_id"),
            **data,
            "capacity": self.hubspot_capacity(),
        }

    def hubspot_require_capacity(self) -> None:
        hubspot_rules.require_capacity(len(self.store.list(HUBSPOT_SUBSCRIPTIONS, limit=1000)))

    def delete_hubspot_subscription(
        self, subscription_id: str, *, actor: str | None, source: str
    ) -> dict[str, Any]:
        record = self.store.get(subscription_id)
        if record is None or record["collection"] != HUBSPOT_SUBSCRIPTIONS:
            raise ChangeStreamError(f"hubspot webhook subscription {subscription_id} not found")
        return self.store.delete(record["id"], actor=actor, source=source)

    def hubspot_usage(self, *, org_id: str | None = None) -> dict[str, Any]:
        """The rate-limit fact, with the budget it is an exemption from.

        The research states the exemption and no limit, so the budget is declared
        here and the exempt calls are counted beside the charged ones - a report
        showing only the remaining budget would make the exemption invisible, and
        an invisible exemption cannot be audited.
        """
        budget = {"limit": vocabulary.DEFAULT_API_CALL_BUDGET, "spent": 0}
        if org_id:
            org = self.org(org_id)
            if org is not None:
                budget = dict(org["data"].get("call_budget") or budget)
        subscriptions = self.store.list(HUBSPOT_SUBSCRIPTIONS, limit=1000)
        received = sum(int((r["data"] or {}).get("calls_received") or 0) for r in subscriptions)
        exempt = sum(
            int((r["data"] or {}).get("calls_exempt_from_rate_limit") or 0) for r in subscriptions
        )
        return {
            **hubspot_rules.describe(),
            "budget": budget,
            "calls_received": received,
            "calls_exempt_from_rate_limit": exempt,
            "capacity": self.hubspot_capacity(),
        }

    def charge_hubspot_call(
        self, subscription_id: str, *, via_workflow: bool, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Count one call, and whether it cost the room budget.

        Always ``via_workflow=True`` for a webhook action, because that is what
        the research describes. The flag is on the call rather than fixed so the
        counter the research is about - the exempt one - is the one being kept.

        ``source`` is required rather than defaulted: this write is reached from
        the seeder as well as from a caller, and a hardcoded default is exactly
        the defect the audit-source rule exists to prevent.
        """
        record = self.store.get(subscription_id)
        if record is None or record["collection"] != HUBSPOT_SUBSCRIPTIONS:
            raise ChangeStreamError(f"hubspot webhook subscription {subscription_id} not found")
        data = dict(record["data"])
        budget = dict(
            data.get("call_budget") or {"limit": vocabulary.DEFAULT_API_CALL_BUDGET, "spent": 0}
        )
        result = hubspot_rules.charge_or_exempt(budget, via_workflow=via_workflow)
        updated = self.store.update(
            record["id"],
            {
                "calls_received": int(data.get("calls_received") or 0) + 1,
                "calls_exempt_from_rate_limit": int(data.get("calls_exempt_from_rate_limit") or 0)
                + (1 if via_workflow else 0),
                "calls_charged_to_budget": int(data.get("calls_charged_to_budget") or 0)
                + (0 if via_workflow else 1),
                "call_budget": {"limit": result["limit"], "spent": result["spent"]},
            },
            actor=actor,
            source=source,
        )
        return {"id": updated["id"], **(updated["data"] or {})}

    # ------------------------------------------------------------------ #
    # Usage and summary
    # ------------------------------------------------------------------ #

    def usage(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Every subscription's delivery usage, against the researched defaults."""
        records = self.store.list(SUBSCRIPTIONS, room_id=room_id, limit=500)
        rows = [
            usage_rules.describe(dict(r["data"], id=r["id"], room_id=r.get("room_id")))
            for r in records
        ]
        totals = usage_rules.blank()
        for row in rows:
            for key in totals:
                totals[key] = totals[key] + int(row.get(key) or 0)
        return {
            "room_id": room_id,
            "count": len(rows),
            "totals": totals,
            "recommended_buffer_bytes": vocabulary.RECOMMENDED_BUFFER_BYTES,
            "subscriptions": rows,
            "metric": usage_rules.describe_metric_source(),
        }

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Counts for the room, over this room's rows only."""
        events = self.store.list(CHANGE_EVENTS, room_id=room_id, limit=1000)
        replica_rows = self.store.list(REPLICA, room_id=room_id, limit=1000)
        buffers = self.store.list(SUBSCRIPTIONS, room_id=room_id, limit=500)
        change_types: dict[str, int] = {}
        for record in events:
            key = str((record["data"] or {}).get("change_type") or "")
            change_types[key] = change_types.get(key, 0) + 1
        states: dict[str, int] = {}
        for record in events:
            key = str((record["data"] or {}).get("state") or "")
            states[key] = states.get(key, 0) + 1
        return {
            "room_id": room_id,
            "events": len(events),
            "events_by_change_type": change_types,
            "events_by_state": states,
            "buffered_now": sum(self._buffer_set(str(r["id"])).event_count() for r in buffers),
            "replica_rows": len(replica_rows),
            "replica_live": sum(
                1 for r in replica_rows if str((r["data"] or {}).get("replica_state")) == "live"
            ),
            "replica_deleted": sum(
                1 for r in replica_rows if str((r["data"] or {}).get("replica_state")) == "deleted"
            ),
            "subscriptions_open": sum(
                1 for r in buffers if str((r["data"] or {}).get("state")) == "open"
            ),
            "channels": len(self.channels()),
            "commit_rule": (
                "a change is parked under its transactionKey and only commits to the room's "
                "replica when the key changes"
            ),
        }


def _edition_error(edition: Any) -> EditionDoesNotSupportCdc:
    """The edition refusal, built here so its wording exists exactly once."""
    return EditionDoesNotSupportCdc(
        f"Change Data Capture is not available in the {edition!r} edition. It is available in: "
        f"{', '.join(vocabulary.CDC_EDITIONS)}. Change tracking the room could not receive would "
        "leave the room looking configured and streaming nothing."
    )


def _duplicate_table_error(logical_name: str, existing_id: str) -> DataverseError:
    """The duplicate-table refusal, kept beside its only caller."""
    return DataverseError(
        f"table {logical_name!r} is already declared as {existing_id}. A delta link is scoped to "
        "one table, so two rows for one logical name would give the same table two delta links."
    )


__all__ = ["ChangeStreamEngine", "OWNED_COLLECTIONS"]
