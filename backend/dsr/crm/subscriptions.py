"""Webhook subscriptions: the general-purpose seam.

Sourced behaviour, from the research:

* ``POST /v1/webhooks`` with ``{event, targetUrl}`` creates a subscription and
  answers ``201 Subscribed``.
* "Store the returned ``id`` in case you want to cancel the subscription later
  on" — so the record id *is* the subscription id, and there is nothing else to
  persist to cancel it later.
* ``GET /v1/webhooks`` lists them.
* ``DELETE /v1/webhooks/{subscriptionId}`` cancels, answering ``204``.

The record id doubles as the subscription id here for the same reason: a second
identifier would be a second thing to keep in sync, and the audit trail already
keeps cancelled subscriptions recoverable.

``source`` on every write
-------------------------
Each method that writes takes a required keyword-only ``source``, and the caller
passes the route that served the request. The branch hardcoded the strings
``"subscribe"``, ``"unsubscribe"`` and ``"delivery"``, which name no route at all
- so an audit row could not be traced back to the request that caused it. Making
it a required argument is what stops that regressing: a new caller cannot forget
to pass one.
"""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlsplit

from dsr.crm.errors import CrmError
from dsr.crm.vocabulary import require_event
from dsr.db.audited import RecordNotFound, utcnow
from dsr.store import RecordStore

COLLECTION = "crm_subscription"


class SubscriptionError(CrmError):
    """Raised when a subscription request cannot be honoured as written."""


def validate_target_url(target_url: Any) -> str:
    """Accept only absolute http(s) URLs.

    A webhook target is fetched by the server, so an unvalidated value here is
    a request-forgery and internal-network primitive handed to anyone who can
    call the API.
    """
    if not isinstance(target_url, str) or not target_url.strip():
        raise SubscriptionError("target_url is required")
    candidate = target_url.strip()
    parts = urlsplit(candidate)
    if parts.scheme not in ("http", "https"):
        raise SubscriptionError("target_url must be an absolute http or https URL")
    if not parts.netloc:
        raise SubscriptionError("target_url must include a host")
    return candidate


class SubscriptionBook:
    """Create, list, and cancel webhook subscriptions over the audited store."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def subscribe(
        self,
        event: str,
        target_url: str,
        *,
        room_id: str | None = None,
        secret: str | None = None,
        description: str = "",
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Create a subscription. The returned ``id`` is the subscription id."""
        payload = {
            "event": require_event(event),
            "target_url": validate_target_url(target_url),
            # `active` is this product's addition: the source has no pause, but
            # a rep who misconfigures a target should not have to delete and
            # retype the subscription to stop the traffic.
            "active": True,
            "secret": secret or None,
            "description": description or "",
            "deliveries": 0,
            "failures": 0,
            "last_status": None,
            "last_delivered_at": None,
        }
        return self.store.create(
            COLLECTION, payload, room_id=room_id, actor=actor, source=source
        )

    def list(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        """List live subscriptions, newest first.

        ``room_id`` is a scope filter, not a filter for "only that room": a
        subscription with no ``room_id`` receives every room's events, so it is
        included for any scope. Note that ``list(..., room_id=None)`` on the
        store means *unfiltered*, so the unscoped set has to be picked out here
        rather than asked for.
        """
        everything = self.store.list(COLLECTION, limit=200)
        if room_id is None:
            return everything
        return [
            record
            for record in everything
            if record["room_id"] in (None, room_id)
        ]

    def get(self, subscription_id: str) -> dict[str, Any] | None:
        record = self.store.get(subscription_id)
        if record is None or record["collection"] != COLLECTION:
            return None
        return record

    def unsubscribe(
        self, subscription_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Cancel a subscription.

        A soft delete, so the cancellation is audited and the history of what
        was sent where survives the unsubscribe. A missing id raises
        :class:`RecordNotFound` rather than a bad-request error, matching how
        every other route in the API answers for an absent record.
        """
        if self.get(subscription_id) is None:
            raise RecordNotFound(subscription_id)
        return self.store.delete(subscription_id, actor=actor, source=source)

    def matching(self, event: str, *, room_id: str | None = None) -> list[dict[str, Any]]:
        """Active subscriptions that should receive this event."""
        return [
            record
            for record in self.list(room_id=room_id)
            if record["data"].get("event") == event and record["data"].get("active", True)
        ]

    def record_outcome(
        self, subscription_id: str, *, ok: bool, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Fold a delivery outcome back into the subscription's counters.

        Counters live on the record rather than being computed on read so a
        rep can see at a glance which of their integrations is quietly failing.
        """
        current = self.get(subscription_id)
        if current is None:
            raise RecordNotFound(subscription_id)
        data = current["data"]
        patch: dict[str, Any] = {
            "deliveries": int(data.get("deliveries") or 0) + 1,
            "failures": int(data.get("failures") or 0) + (0 if ok else 1),
            "last_status": "success" if ok else "error",
        }
        if ok:
            patch["last_delivered_at"] = utcnow()
        return self.store.update(subscription_id, patch, actor=actor, source=source)


def summarise(record: Mapping[str, Any]) -> dict[str, Any]:
    """The list shape: the fields a rep needs, without the shared secret."""
    data = record.get("data") or {}
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "event": data.get("event"),
        "target_url": data.get("target_url"),
        "active": bool(data.get("active", True)),
        "description": data.get("description", ""),
        "signed": bool(data.get("secret")),
        "deliveries": int(data.get("deliveries") or 0),
        "failures": int(data.get("failures") or 0),
        "last_status": data.get("last_status"),
        "last_delivered_at": data.get("last_delivered_at"),
        "created_at": record.get("created_at"),
    }
