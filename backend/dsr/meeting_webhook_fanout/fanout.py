"""The store-facing half: subscribe, sign, fan out, record.

What the room does with a meeting
---------------------------------
An admin registers a subscriber URL against one of the three researched event
types. When a meeting is created, updated or cancelled, this class serialises the
payload once, signs those exact bytes, and POSTs them to every enabled
subscription naming that event type. Every attempt becomes a delivery row, so a
subscriber that was skipped is visible rather than inferred.

Four rules that shape the code
------------------------------
**``source`` is a required keyword on every writing method.** A URL string
hardcoded inside a domain method is a defect, and the same class of bug has
shipped in this codebase before: a feature's audit log kept naming a path the app
had stopped serving. Making it required turns an omission into a ``TypeError`` at
the call site rather than an untraceable row.

**The bytes are built once.** :func:`~dsr.meeting_webhook_fanout.payloads.canonical_bytes`
runs once per event, and the result is both what gets signed and what gets sent. A
sender that signed one serialisation and posted another would produce a delivery
every subscriber refuses, and the refusal would look like the subscriber's bug.

**One event row, N delivery rows.** The research's fan-out is unbounded, so the
two counts are genuinely different and a reader has to be able to tell them apart.

**Replay protection is not the sender's job.** The research says so in those
words, and a sender that enforced the window would refuse its own correctly signed
delivery whenever a clock ran fast. The timestamp header goes out; the window is
published.
"""

from __future__ import annotations

import ipaddress
import secrets as _secrets
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlparse

from dsr.meeting_webhook_fanout.errors import (
    AlreadyExists,
    InvalidEventType,
    InvalidRequest,
    InvalidSubscriberUrl,
    MissingSecret,
    NoRoom,
    UnknownDelivery,
    UnknownEvent,
    UnknownSubscription,
)
from dsr.meeting_webhook_fanout.payloads import build_payload, canonical_bytes, missing_fields
from dsr.meeting_webhook_fanout.signing import headers_for, unix_seconds
from dsr.meeting_webhook_fanout.transport import (
    DEFAULT_TIMEOUT_SECONDS,
    DeliveryResult,
    Transport,
    UrllibTransport,
    is_retryable,
)
from dsr.meeting_webhook_fanout.vocabulary import (
    COLLECTIONS,
    DEFAULT_DEPLOYMENT_MODE,
    OUTCOME_DELIVERED,
    OUTCOME_FAILED,
    OUTCOME_SKIPPED,
    SKIP_NO_SUBSCRIBERS,
    STATUS_DISABLED,
    STATUS_ENABLED,
    describe as describe_vocabulary,
    is_known_deployment_mode,
    is_known_event_type,
    is_known_status,
    payload_type_for_event_type,
)
from dsr.store import RecordStore

__all__ = ["MeetingWebhookFanout", "validate_url"]


def _room_deployment_mode(store: RecordStore, room_id: str) -> str:
    """The room's deployment mode, or this build's default.

    Read from the room record rather than from configuration, because the research
    makes it a property of the deployment and a room is where a self-hosted
    install and a SaaS install differ. A room that has never set one gets
    ``self_hosted``, which is this build's deployment.
    """
    room = store.get(room_id)
    if room is None:
        raise NoRoom(f"room {room_id} does not exist")
    mode = str((room.get("data") or {}).get("deployment_mode") or "").strip().lower()
    if not mode:
        return DEFAULT_DEPLOYMENT_MODE
    if not is_known_deployment_mode(mode):
        raise InvalidRequest(
            f"deployment_mode {mode!r} is not one of "
            f"{sorted(row['id'] for row in describe_vocabulary()['deployment_modes'])}"
        )
    return mode


def _room_rows(
    store: RecordStore, collection: str, room_id: str, *, include_deleted: bool = False
) -> list[dict[str, Any]]:
    """This room's rows in one collection.

    Room scoping is the envelope's ``room_id`` column, so it is a ``list`` filter
    and not a ``find`` on a JSON path. Reaching for ``find`` with
    ``{"room_id": ...}`` would look up a field this package never writes into
    ``data``, and it would return nothing while appearing to be a working query -
    which is the quietest failure mode this store has.
    """
    return store.list(collection, room_id=room_id, limit=1000, include_deleted=include_deleted)


def _room_match(
    store: RecordStore, room_id: str, record_id: str, *, live_only: bool = True
) -> dict[str, Any] | None:
    """One row by id, if it belongs to this room.

    The room check is on the envelope, so a row from another room is not found even
    when its id is known. A feature that returned another tenant's row because the
    caller guessed an id would be a data leak, and an id is guessable.

    ``RecordStore.get`` is a passthrough with no ``include_deleted``, so a retired
    subscription would not resolve and a reader following a delivery-log reference
    would hit a 404 on a row that still exists. The audited database's own ``get``
    takes the flag, and it is the same audited wrapper this package already reads
    and writes through, so this is not a bypass of the audit log - it is one more
    call on the object that owns it.
    """
    row = store.db.get(record_id, include_deleted=True)
    if row is None or row.get("room_id") != room_id:
        return None
    if live_only and row.get("deleted_at"):
        return None
    return row


def validate_url(url: str, mode: str) -> str:
    """The subscriber URL, or the reason this deployment will not take it.

    The research gives two rules and they disagree, so the mode decides which one
    applies:

    * ``saas`` - HTTPS only, and private or internal addresses and ``localhost``
      are blocked.
    * ``self_hosted`` - HTTP and HTTPS both accepted, and private addresses are
      allowed for internal webhooks.

    A URL this build cannot even parse as a URL is refused here rather than at the
    transport, because the error a subscriber gets then is a DNS failure that says
    nothing about the mistake.
    """
    text = str(url or "").strip()
    if not text:
        raise InvalidSubscriberUrl("a subscription needs a subscriber URL")
    parsed = urlparse(text)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise InvalidSubscriberUrl(
            f"subscriber URL must be http or https in {mode} mode; got {scheme or 'no scheme'!r}"
        )
    if not parsed.hostname:
        raise InvalidSubscriberUrl(f"subscriber URL {text!r} has no host")

    if mode != "saas":
        return text

    if scheme != "https":
        raise InvalidSubscriberUrl(
            "Cal.com SaaS accepts only HTTPS subscriber URLs; HTTP is refused in saas mode"
        )
    host = (parsed.hostname or "").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise InvalidSubscriberUrl("Cal.com SaaS blocks localhost subscriber URLs")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return text
    if address.is_private or address.is_loopback or address.is_link_local:
        raise InvalidSubscriberUrl(
            f"Cal.com SaaS blocks private and internal subscriber addresses; {host} is one"
        )
    return text


class MeetingWebhookFanout:
    """Every rule in this workflow, over a :class:`~dsr.store.RecordStore`.

    The transport is held rather than constructed per call, so a test supplies a
    fake and the seeder never opens a socket. The engine holds nothing else, and
    it is a plain object, which is what a test constructs.
    """

    def __init__(self, store: RecordStore, transport: Transport | None = None) -> None:
        self.store = store
        self.transport: Transport = transport or UrllibTransport()

    # -- reference data ----------------------------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        """Every published vocabulary, served as data."""
        return describe_vocabulary()

    def inferences(self) -> dict[str, Any]:
        """Every judgement call this workflow rests on, and how to change each one."""
        from dsr.meeting_webhook_fanout.inferences import describe as describe_inferences

        return describe_inferences()

    # -- the signing secret ------------------------------------------------- #

    def secret(self, room_id: str) -> dict[str, Any]:
        """The room's HMAC signing secret, and where it came from.

        The research says the secret comes from support and is not shown in the
        vendor UI, so this route is the only place a reader can see it. It is
        served rather than masked, for the same reason WF-044 serves its endpoint
        secret: a team configuring a subscriber needs the value, and a masked
        secret means one support ticket per tenant.
        """
        _room_deployment_mode(self.store, room_id)
        row = self._key(room_id)
        return {
            "room_id": room_id,
            "secret": row["data"].get("secret") if row else None,
            "secret_id": row["id"] if row else None,
            "origin": row["data"].get("origin") if row else None,
            "source_of_truth": (
                "Chili Piper issues the tenant secret by email. It is not shown in the UI."
            ),
        }

    def set_secret(
        self,
        room_id: str,
        secret: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Set or rotate the room's signing secret.

        A write, and an audited one: rotating a signing secret invalidates every
        subscriber holding the old one, and a reader deciding whether a delivery
        failure is a clock problem or a rotation needs the audit row.
        """
        _room_deployment_mode(self.store, room_id)
        text = str(secret or "").strip()
        if not text:
            raise MissingSecret("a signing secret cannot be empty")
        existing = self._key(room_id)
        data = {
            "secret": text,
            "origin": "supplied by the tenant",
            "rotated_at": datetime.now(timezone.utc).isoformat(),
            "rotations": int((existing["data"].get("rotations") or 0) + 1) if existing else 1,
        }
        if existing:
            row = self.store.update(existing["id"], data, actor=actor, source=source)
        else:
            row = self.store.create(
                COLLECTIONS["key"], data, room_id=room_id, actor=actor, source=source
            )
        return dict(row["data"], id=row["id"])

    def _ensure_secret(self, room_id: str, *, actor: str | None, source: str) -> str:
        """The room's secret, minting one when the tenant never supplied any.

        The research says the tenant obtains its secret by emailing support, so a
        room that has not set one is the normal case rather than a broken one. A
        subscriber needs a signature to check against either way, so the room
        supplies one and records that it did.

        The mint is attributed to the caller rather than to ``system``. A person
        reading the audit log and asking "who changed my signing secret" wants the
        name that caused it, and the name that caused it is whoever emitted the
        event.
        """
        row = self._key(room_id)
        if row is not None and row["data"].get("secret"):
            return str(row["data"]["secret"])
        who = actor or "system"
        minted = _secrets.token_hex(32)
        self.set_secret(
            room_id,
            minted,
            actor=who,
            source=source,
        )
        # ``set_secret`` records origin as "supplied by the tenant", which is a lie
        # for one this room minted, so the origin is corrected in the same breath.
        minted_row = self._key(room_id)
        if minted_row is not None:
            self.store.update(
                minted_row["id"],
                {"origin": "minted by the room; ask support for the tenant secret"},
                actor=who,
                source=source,
            )
        return minted

    def _key(self, room_id: str) -> dict[str, Any] | None:
        rows = _room_rows(self.store, COLLECTIONS["key"], room_id)
        return rows[0] if rows else None

    # -- subscriptions ------------------------------------------------------ #

    def subscribe(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Steps 1 and 2: create a subscription, then set its row's status.

        The researched flow is *"pastes the subscriber URL and clicks Create, then
        sets the row's status to **Enabled**"*. So a new row lands **disabled** and
        becomes enabled by a deliberate second act, which is the ``PATCH`` route.
        A body that asks for ``enabled`` at creation is honoured, because an admin
        who ticked the box has already made the second decision.

        The pair is the identity: *'multiple webhook types may have the same webhook
        URL, and multiple webhook URLs for the same type'*. So a URL can carry
        several types and a type can have several URLs, and the same pair twice is
        refused rather than delivered to twice.
        """
        mode = _room_deployment_mode(self.store, room_id)
        event_type = str(payload.get("event_type") or "").strip()
        if not is_known_event_type(event_type):
            raise InvalidEventType(
                f"event_type must be one of the three the research names: "
                f"{[row['id'] for row in describe_vocabulary()['event_types']]}"
            )
        url = validate_url(payload.get("url"), mode)

        status = str(payload.get("status") or STATUS_DISABLED).strip().lower()
        if not is_known_status(status):
            raise InvalidRequest(f"status must be 'enabled' or 'disabled', got {status!r}")

        if self.find_subscription(room_id, url, event_type) is not None:
            raise AlreadyExists(
                f"room {room_id} already has a {event_type} subscription for {url}. "
                "Edit that row rather than adding a second one; two rows for one pair "
                "would deliver the same event twice to the same address."
            )

        # Every other key the caller sent is kept. Payloads are ordinary JSON, so a
        # team adding its own field to a subscription must need no coordination with
        # this package: whatever it sent is what is stored, and the six keys below
        # are the only ones this build validates.
        reserved = {"url", "event_type", "status", "description"}
        data = {
            key: value
            for key, value in payload.items()
            if key not in reserved and value is not None
        }
        data.update(
            {
                "url": url,
                "event_type": event_type,
                "payload_type": payload_type_for_event_type(event_type),
                "status": status,
                "deployment_mode": mode,
                "description": payload.get("description") or "",
                "created_by": actor or "unknown",
            }
        )
        row = self.store.create(
            COLLECTIONS["subscription"],
            data,
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return dict(row["data"], id=row["id"], revision=row["revision"])

    def amend(
        self,
        room_id: str,
        subscription_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """The row's status toggle, or a corrected URL.

        Step 2's control is *'sets the row's status to **Enabled**'*, so the status
        toggle is a ``PATCH`` and not a separate enable route: a reader looking at
        the table wants one row and one edit, not a row and a button.

        The whole merged row is re-validated rather than field by field, so an
        amendment cannot smuggle in a rule the original create would have refused.
        """
        row = _room_match(self.store, room_id, subscription_id)
        if row is None:
            raise UnknownSubscription(f"subscription {subscription_id} is not in room {room_id}")
        mode = _room_deployment_mode(self.store, room_id)
        current = dict(row["data"])

        merged = dict(current)
        merged.update({key: value for key, value in payload.items() if value is not None})

        event_type = str(merged.get("event_type") or "").strip()
        if not is_known_event_type(event_type):
            raise InvalidEventType(f"event_type {event_type!r} is not one of the three")
        url = validate_url(merged.get("url"), mode)
        status = str(merged.get("status") or STATUS_DISABLED).strip().lower()
        if not is_known_status(status):
            raise InvalidRequest(f"status must be 'enabled' or 'disabled', got {status!r}")

        clash = self.find_subscription(room_id, url, event_type)
        if clash is not None and clash["id"] != subscription_id:
            raise AlreadyExists(f"room {room_id} already has a {event_type} subscription for {url}")

        merged.update(
            {
                "url": url,
                "event_type": event_type,
                "payload_type": payload_type_for_event_type(event_type),
                "status": status,
                "deployment_mode": mode,
            }
        )
        updated = self.store.update(subscription_id, merged, actor=actor, source=source)
        return dict(updated["data"], id=updated["id"], revision=updated["revision"])

    def retire(
        self,
        room_id: str,
        subscription_id: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """The per-row delete the research names.

        *"the status toggle, per-row delete, ordering"* - so delete is one row, not
        the table. It is a soft delete, because the delivery log names the
        subscription that ran and destroying the row would leave that history
        pointing at nothing, which is the exact defect the audit-source rule
        exists to prevent one layer up.
        """
        row = _room_match(self.store, room_id, subscription_id)
        if row is None:
            raise UnknownSubscription(f"subscription {subscription_id} is not in room {room_id}")
        # Read the data before the delete, because ``store.delete`` answers with the
        # envelope and the id and no data. The caller gets the row it retired rather
        # than an id it would have to resolve again.
        data = dict(row["data"])
        self.store.delete(subscription_id, actor=actor, source=source)
        return dict(data, id=subscription_id, retired=True)

    def subscriptions(self, room_id: str, *, include_retired: bool = False) -> dict[str, Any]:
        """The subscriber table, grouped by event type.

        The research's table has a status toggle, a per-row delete and an order,
        so the response carries all three: the count per type, the enabled count,
        and the rows in the order they were created.
        """
        _room_deployment_mode(self.store, room_id)
        ordered = _room_rows(
            self.store,
            COLLECTIONS["subscription"],
            room_id,
            include_deleted=include_retired,
        )
        ordered.sort(key=lambda row: (row.get("created_at") or "", row["id"]))
        live = [row for row in ordered if not row.get("deleted_at")]
        by_type: dict[str, int] = {}
        enabled_by_type: dict[str, int] = {}
        for row in live:
            key = str(row["data"].get("event_type"))
            by_type[key] = by_type.get(key, 0) + 1
            if row["data"].get("status") == STATUS_ENABLED:
                enabled_by_type[key] = enabled_by_type.get(key, 0) + 1
        return {
            "room_id": room_id,
            "count": len(live),
            "enabled": sum(1 for row in live if row["data"].get("status") == STATUS_ENABLED),
            "retired": sum(1 for row in ordered if row.get("deleted_at")),
            "by_event_type": by_type,
            "enabled_by_event_type": enabled_by_type,
            "subscription_limit": None,
            "subscription_limit_note": (
                "The research states the fan-out is unbounded: "
                "'You are not limited by the number of webhooks you have'."
            ),
            "subscriptions": [
                dict(row["data"], id=row["id"], retired=bool(row.get("deleted_at")))
                for row in ordered
            ],
        }

    def subscription(self, room_id: str, subscription_id: str) -> dict[str, Any]:
        """One row, or 404.

        A retired row still answers, and says so, because the delivery log names
        this subscription and a reader following one of those names needs the row
        it pointed at.
        """
        row = _room_match(self.store, room_id, subscription_id, live_only=False)
        if row is None:
            raise UnknownSubscription(f"subscription {subscription_id} is not in room {room_id}")
        return dict(row["data"], id=row["id"], retired=bool(row.get("deleted_at")))

    def find_subscription(self, room_id: str, url: str, event_type: str) -> dict[str, Any] | None:
        """The live subscription for one URL and one event type, or None.

        Both halves of the identity, because the research says the fan-out runs in
        both directions: one URL may serve several types and one type may have
        several URLs. The room comes from the envelope and the other two from the
        record's own data.
        """
        for row in _room_rows(self.store, COLLECTIONS["subscription"], room_id):
            if row.get("deleted_at"):
                continue
            if row["data"].get("url") == url and row["data"].get("event_type") == event_type:
                return row
        return None

    def _matching(self, room_id: str, event_type: str, *, enabled_only: bool) -> list[dict]:
        rows = [
            row
            for row in _room_rows(self.store, COLLECTIONS["subscription"], room_id)
            if row["data"].get("event_type") == event_type
        ]
        if not enabled_only:
            return rows
        return [row for row in rows if row["data"].get("status") == STATUS_ENABLED]

    # -- events ------------------------------------------------------------- #

    def emit(
        self,
        room_id: str,
        meeting: Mapping[str, Any],
        event_type: str,
        *,
        actor: str | None = None,
        source: str,
        now: datetime | None = None,
        transport: Transport | None = None,
    ) -> dict[str, Any]:
        """Serialise, sign and fan out one meeting event.

        The whole researched data flow, in order: *"meeting created/updated/canceled
        -> Chili Piper serialises a meeting payload -> HMAC-SHA256 signature +
        timestamp headers -> POST to subscriber"*.

        Three steps, in this order, and the order is the contract:

        1. Build the payload once and serialise it once.
        2. Sign those exact bytes with ``{timestamp}.{raw_body}``.
        3. POST those same bytes with the signature and timestamp headers.

        The event row is written before the first delivery is attempted, so a
        fan-out that raises halfway still leaves the reader with the signed event
        and the deliveries that did land. The alternative - writing the event row
        last - loses the event entirely if the third subscriber times out, and the
        reader is left with a partial delivery list and no way to redeliver.
        """
        mode = _room_deployment_mode(self.store, room_id)
        if not is_known_event_type(event_type):
            raise InvalidEventType(f"event_type {event_type!r} is not one of the three")

        moment = now or datetime.now(timezone.utc)
        timestamp = unix_seconds(moment)
        secret = self._ensure_secret(room_id, actor=actor, source=source)

        try:
            payload = build_payload(meeting, event_type)
        except ValueError as exc:
            raise InvalidRequest(str(exc)) from exc
        raw = canonical_bytes(payload)
        body = raw.decode("utf-8")
        signature_headers = headers_for(secret, timestamp, body)

        event_row = self.store.create(
            COLLECTIONS["event"],
            {
                "event_type": event_type,
                "payload_type": payload["type"],
                "meeting_id": payload.get("meetingIdChili"),
                "timestamp": timestamp,
                "signed_at": moment.isoformat(),
                "raw_body": body,
                "payload": payload,
                "missing_fields": missing_fields(payload),
                "signature": signature_headers["X-Chili-Signature"],
                "signature_header": "X-Chili-Signature",
                "timestamp_header": "X-Chili-Timestamp",
                "signing_input": f"{timestamp}.{body}",
                "deployment_mode": mode,
                "envelope": "flat",
                "emitted_by": actor or "system",
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        event_id = event_row["id"]

        targets = self._matching(room_id, event_type, enabled_only=True)
        disabled = [
            row
            for row in self._matching(room_id, event_type, enabled_only=False)
            if row["data"].get("status") != STATUS_ENABLED
        ]

        if not targets:
            self.store.create(
                COLLECTIONS["delivery"],
                {
                    "event_id": event_id,
                    "event_type": event_type,
                    "outcome": OUTCOME_SKIPPED,
                    "reason": SKIP_NO_SUBSCRIBERS,
                    "at": moment.isoformat(),
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
            return {
                "event": dict(event_row["data"], id=event_id),
                "deliveries": [],
                "disabled_subscriptions": len(disabled),
                "delivered": 0,
                "failed": 0,
                "skipped": 1,
                "reason": SKIP_NO_SUBSCRIBERS,
            }

        sender = transport or self.transport
        deliveries: list[dict[str, Any]] = []
        for row in targets:
            result = self._attempt(
                sender,
                room_id=room_id,
                event_id=event_id,
                event_type=event_type,
                url=str(row["data"]["url"]),
                subscription_id=row["id"],
                raw=raw,
                headers=signature_headers,
                moment=moment,
                actor=actor,
                source=source,
            )
            deliveries.append(result)

        return {
            "event": dict(event_row["data"], id=event_id),
            "deliveries": deliveries,
            "disabled_subscriptions": len(disabled),
            "delivered": sum(1 for row in deliveries if row["outcome"] == OUTCOME_DELIVERED),
            "failed": sum(1 for row in deliveries if row["outcome"] == OUTCOME_FAILED),
            "skipped": 0,
            "reason": None,
        }

    def _attempt(
        self,
        sender: Transport,
        *,
        room_id: str,
        event_id: str,
        event_type: str,
        url: str,
        subscription_id: str,
        raw: bytes,
        headers: Mapping[str, str],
        moment: datetime,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """One attempt at one subscriber, recorded either way.

        A refused or unreachable subscriber writes a row rather than raising, so a
        fan-out over ten subscribers is nine deliveries and one failure rather than
        a stack trace that loses the nine. The caller decides what a partial
        fan-out means; this row is what a reader looks at later.
        """
        result: DeliveryResult = sender.post(url, raw, headers, DEFAULT_TIMEOUT_SECONDS)
        outcome = OUTCOME_DELIVERED if result.ok else OUTCOME_FAILED
        data = {
            "event_id": event_id,
            "event_type": event_type,
            "subscription_id": subscription_id,
            "url": url,
            "outcome": outcome,
            "status": result.status,
            "error": result.error,
            "response_excerpt": result.body,
            "duration_ms": result.duration_ms,
            "retryable": is_retryable(result),
            "retryable_note": "advice only; this build has no retry ladder",
            "final_url": result.final_url or url,
            "attempted_at": moment.isoformat(),
            "attempt_number": 1,
        }
        row = self.store.create(
            COLLECTIONS["delivery"], data, room_id=room_id, actor=actor, source=source
        )
        return dict(row["data"], id=row["id"])

    def redeliver(
        self,
        room_id: str,
        event_id: str,
        *,
        actor: str | None = None,
        source: str,
        transport: Transport | None = None,
    ) -> dict[str, Any]:
        """Re-attempt one event, and record the attempt beside the first.

        The research names no retry ladder, so this is a route a person calls
        rather than a scheduler this build invented. The *same bytes* are re-signed
        and re-sent: the raw body is read back off the event row rather than
        rebuilt from the payload, because a rebuilt body is a body whose field
        order this build now chooses rather than one the subscriber already saw.

        The timestamp is the wall clock, not the original, because a delivery whose
        signature covers a timestamp from an hour ago is outside every consumer's
        freshness window and the redelivery would be refused for the wrong reason.
        """
        row = _room_match(self.store, room_id, event_id)
        if row is None:
            raise UnknownEvent(f"event {event_id} is not in room {room_id}")
        data = row["data"]
        event_type = str(data.get("event_type") or "")
        if not is_known_event_type(event_type):
            raise InvalidEventType(f"event {event_id} names event_type {event_type!r}")

        secret = self._ensure_secret(room_id, actor=actor, source=source)
        raw = str(data.get("raw_body") or "").encode("utf-8")
        moment = datetime.now(timezone.utc)
        timestamp = unix_seconds(moment)
        headers = headers_for(secret, timestamp, raw.decode("utf-8"))

        targets = self._matching(room_id, event_type, enabled_only=True)
        sender = transport or self.transport
        deliveries = [
            self._attempt(
                sender,
                room_id=room_id,
                event_id=event_id,
                event_type=event_type,
                url=str(target["data"]["url"]),
                subscription_id=target["id"],
                raw=raw,
                headers=headers,
                moment=moment,
                actor=actor,
                source=source,
            )
            for target in targets
        ]
        if not deliveries:
            raise UnknownEvent(
                f"event {event_id} has no enabled {event_type} subscription left to redeliver to"
            )
        return {
            "event": dict(data, id=event_id),
            "deliveries": deliveries,
            "delivered": sum(1 for row_ in deliveries if row_["outcome"] == OUTCOME_DELIVERED),
            "failed": sum(1 for row_ in deliveries if row_["outcome"] == OUTCOME_FAILED),
        }

    def events(
        self, room_id: str, *, event_type: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        """The event log, newest first."""
        _room_deployment_mode(self.store, room_id)
        rows = self._matching_events(room_id, event_type)
        by_type: dict[str, int] = {}
        for row in rows:
            key = str(row["data"].get("event_type"))
            by_type[key] = by_type.get(key, 0) + 1
        return {
            "room_id": room_id,
            "count": len(rows),
            "by_event_type": by_type,
            "events": [self._event_view(row) for row in rows[:limit]],
        }

    def event(self, room_id: str, event_id: str) -> dict[str, Any]:
        """One event, with the exact bytes that were signed."""
        row = _room_match(self.store, room_id, event_id, live_only=False)
        if row is None:
            raise UnknownEvent(f"event {event_id} is not in room {room_id}")
        return self._event_view(row, with_body=True)

    def _matching_events(self, room_id: str, event_type: str | None) -> list[dict]:
        rows = [
            row
            for row in _room_rows(self.store, COLLECTIONS["event"], room_id)
            if event_type is None or row["data"].get("event_type") == event_type
        ]
        rows.sort(key=lambda row: (row.get("created_at") or "", row["id"]), reverse=True)
        return rows

    def _event_view(self, row: Mapping[str, Any], *, with_body: bool = False) -> dict[str, Any]:
        """One event row.

        ``raw_body``, ``payload`` and ``signing_input`` are left off a list row: a
        list of fifty events would otherwise carry fifty copies of a body no list
        view renders. All three come back from :meth:`event`, which is the route
        that exists to serve them.
        """
        data = dict(row["data"])
        if not with_body:
            data.pop("raw_body", None)
            data.pop("payload", None)
            data.pop("signing_input", None)
        return dict(data, id=row["id"])

    def deliveries(
        self,
        room_id: str,
        *,
        event_type: str | None = None,
        outcome: str | None = None,
        include_skipped: bool = True,
        limit: int = 100,
    ) -> dict[str, Any]:
        """One row per attempt, with the outcome that decided it."""
        _room_deployment_mode(self.store, room_id)
        rows = [
            row
            for row in _room_rows(self.store, COLLECTIONS["delivery"], room_id)
            if event_type is None or row["data"].get("event_type") == event_type
        ]
        if outcome:
            rows = [row for row in rows if row["data"].get("outcome") == outcome]
        if not include_skipped:
            rows = [row for row in rows if row["data"].get("outcome") != OUTCOME_SKIPPED]
        ordered = sorted(
            rows, key=lambda row: (row.get("created_at") or "", row["id"]), reverse=True
        )[:limit]
        by_outcome: dict[str, int] = {}
        for row in rows:
            key = str(row["data"].get("outcome") or "unknown")
            by_outcome[key] = by_outcome.get(key, 0) + 1
        return {
            "room_id": room_id,
            "count": len(ordered),
            "by_outcome": by_outcome,
            "deliveries": [dict(row["data"], id=row["id"]) for row in ordered],
        }

    def delivery(self, room_id: str, delivery_id: str) -> dict[str, Any]:
        """One attempt, with the response that came back."""
        row = _room_match(self.store, room_id, delivery_id, live_only=False)
        if row is None:
            raise UnknownDelivery(f"delivery {delivery_id} is not in room {room_id}")
        return dict(row["data"], id=row["id"])

    # -- the sample, and the summary ---------------------------------------- #

    def sample(self, room_id: str, event_type: str = "new_meeting") -> dict[str, Any]:
        """The exact bytes and the signature, so a reader can check both.

        The research's step 4 is the *subscriber's* side: recompute the HMAC,
        compare in constant time, and reject a stale timestamp. A subscriber cannot
        check any of that without the room's secret and one real body, so this
        serves both beside the string that was signed.

        The body is a shape the research documents rather than a shape the room
        happens to hold, so a team can verify their implementation before they have
        a real meeting to point it at.
        """
        _room_deployment_mode(self.store, room_id)
        if not is_known_event_type(event_type):
            raise InvalidEventType(f"event_type {event_type!r} is not one of the three")
        meeting = _SAMPLE_MEETING
        payload = build_payload(meeting, event_type)
        raw = canonical_bytes(payload)
        body = raw.decode("utf-8")
        timestamp = unix_seconds()
        key = self._key(room_id)
        secret = str(key["data"]["secret"]) if key and key["data"].get("secret") else "unset"
        signature = headers_for(secret, timestamp, body)["X-Chili-Signature"]
        return {
            "room_id": room_id,
            "event_type": event_type,
            "payload_type": payload["type"],
            "timestamp": timestamp,
            "deployment_mode": _room_deployment_mode(self.store, room_id),
            "raw_body": body,
            "signing_input": f"{timestamp}.{body}",
            "signature": signature,
            "signature_header": "X-Chili-Signature",
            "timestamp_header": "X-Chili-Timestamp",
            "secret_is_set": secret != "unset",
            "secret": secret,
            "verify_snippet": (
                "expected = hmac.new(secret, f'{timestamp}.{raw_body}'.encode(), "
                "hashlib.sha256).hexdigest(); "
                "assert hmac.compare_digest(expected, signature)"
            ),
            "replay_window_seconds": 300,
            "replay_window_owner": "the consumer, not the sender",
            "missing_fields": missing_fields(payload),
        }

    def summary(self, room_id: str) -> dict[str, Any]:
        """Counts for the page header, and the researched constraints beside them.

        Counted over this room's own rows rather than the whole collections, so a
        room's header says what happened in that room. The three notes are here
        because all three are things a reader looks for and does not find: the
        fan-out is unbounded, the replay window is the consumer's, and there is no
        retry ladder.
        """
        _room_deployment_mode(self.store, room_id)
        subs = self.subscriptions(room_id, include_retired=True)
        event_rows = self._matching_events(room_id, None)
        delivery_rows = _room_rows(self.store, COLLECTIONS["delivery"], room_id)
        by_outcome: dict[str, int] = {}
        for row in delivery_rows:
            key = str(row["data"].get("outcome") or "unknown")
            by_outcome[key] = by_outcome.get(key, 0) + 1
        by_type: dict[str, int] = {}
        for row in event_rows:
            key = str(row["data"].get("event_type"))
            by_type[key] = by_type.get(key, 0) + 1
        key_row = self._key(room_id)
        return {
            "room_id": room_id,
            "subscriptions": sum(1 for row in subs["subscriptions"] if not row.get("retired")),
            "enabled": subs["enabled"],
            "retired": subs["retired"],
            "events": len(event_rows),
            "deliveries": len(delivery_rows),
            "by_outcome": by_outcome,
            "by_event_type": by_type,
            "has_secret": bool(key_row and key_row["data"].get("secret")),
            "notes": [
                "The fan-out is unbounded: 'You are not limited by the number of "
                "webhooks you have', and one URL may serve several types while one "
                "type may have several URLs.",
                "Replay protection belongs to the consumer. The room ships the "
                "timestamp header and does not refuse a delivery on age.",
                "One attempt per event. There is no retry ladder, because the research "
                "names none. Redelivery is a route a person calls.",
            ],
        }


#: The body the sample route signs. A shape the research documents rather than a
#: shape the room happens to hold, so a team can verify their subscriber before
#: they have a real meeting to point it at. Every documented field the room does
#: not synthesise is filled from the research's own names.
_SAMPLE_MEETING: dict[str, Any] = {
    "meetingIdChili": "m-0001",
    "title": "Northwind Traders - enterprise evaluation",
    "description": "Walk through the enterprise evaluation with the buying committee.",
    "location": "Microsoft Teams",
    "start": "2026-10-08T14:00:00+00:00",
    "end": "2026-10-08T14:45:00+00:00",
    "primaryGuestTimeZone": "Europe/London",
    "primaryGuestName": "Priya Raman",
    "primaryGuestEmail": "priya.raman@northwind.example",
    "primaryGuestIdChili": "g-0001",
    "primaryGuestDataFields": {"seats": 480},
    "hostIdChili": "h-0001",
    "hostName": "Dana Okafor",
    "assigneeIdChili": "h-0001",
    "assigneeName": "Dana Okafor",
    "bookerIdChili": "g-0001",
    "bookerName": "Priya Raman",
    "additionalGuests": [{"name": "Tom Alvarez", "email": "tom.alvarez@northwind.example"}],
    "workspaceId": "ws-0001",
    "workspaceName": "Northwind Traders",
    "productFeatureType": "ConciergeRouter",
    "productFeatureName": "Concierge router",
    "productFeatureId": "pf-0001",
    "distributionName": "Enterprise inbound",
    "distributionId": "d-0001",
    "meetingTypeName": "Demo",
    "meetingTypeId": "mt-0001",
}
