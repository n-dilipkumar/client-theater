"""Webhooks and their subscriptions, over the audited store.

Four collections, four fixed things
-----------------------------------
* ``stream_webhook`` - the researched **Create Webhook**: a name, an HTTPS target
  URL, a generated secret, and the ``verified_at`` stamp from the POST that
  proves the URL answers. Account level, so ``room_id`` is ``None``: the
  research puts webhooks under **Settings -> Data Management**, not in a room.
* ``stream_subscription`` - the researched **Subscriptions** page: a set of
  subscription types on one webhook, an optional JSONPath filter, and the
  active/paused flag the research names ("viewed in detail, paused or
  unsubscribed").
* ``stream_event`` - the recorded ``webhook-event`` inputs, stored before
  anything is sent so a total delivery outage still leaves a record of what
  happened.
* ``stream_delivery`` - one row per (event, subscription), carrying every
  attempt.

``source`` on every write
------------------------
Every method that writes takes a required keyword-only ``source`` and the route
supplies it. The defect this prevents is named in the build brief: an audit row
recording a path the app has stopped serving. A domain function that hard-codes
its own path cannot be caught by reading the route table, so the parameter is
required and a test checks every recorded ``source`` against the routes the host
actually mounted.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.db.audited import RecordNotFound
from dsr.event_stream import signing
from dsr.event_stream.errors import SubscriptionError, TargetError
from dsr.event_stream.filters import CompiledFilter, compile_filter
from dsr.event_stream.targets import iso, parse_now, validate_target_url
from dsr.event_stream.vocabulary import require_types, unknown_types

WEBHOOK_COLLECTION = "stream_webhook"
SUBSCRIPTION_COLLECTION = "stream_subscription"
EVENT_COLLECTION = "stream_event"
DELIVERY_COLLECTION = "stream_delivery"

#: How many rows a list route will look at before filtering in Python. The
#: store caps a single query at 1000 and the filtering below is not something a
#: SQL query can express (``room_id`` being null means "every room", not "no
#: room"), so the window is explicit rather than implied by the cap.
LIST_WINDOW = 500


# --------------------------------------------------------------------------- #
# Webhooks
# --------------------------------------------------------------------------- #


def require_name(value: Any) -> str:
    """The researched "name it" step. A webhook without a name cannot be
    recognised in a list by the person who has to turn it off."""
    if not isinstance(value, str) or not value.strip():
        raise TargetError(
            "name is required",
            code="name_required",
            remediation='Name the webhook, for example "Northwind → warehouse".',
        )
    return value.strip()


class EndpointBook:
    """Create, read, pause, retire webhooks. Verification happens in
    :class:`dsr.event_stream.stream.EventStream`, before a record is written."""

    collection = WEBHOOK_COLLECTION

    def __init__(self, store: Any) -> None:
        self.store = store

    # -- reads -------------------------------------------------------------- #

    def get(self, webhook_id: str) -> dict[str, Any] | None:
        record = self.store.get(webhook_id)
        if record is None or record["collection"] != self.collection:
            return None
        return record

    def require(self, webhook_id: str) -> dict[str, Any]:
        record = self.get(webhook_id)
        if record is None:
            raise RecordNotFound(webhook_id)
        return record

    def list(self, *, include_paused: bool = True) -> list[dict[str, Any]]:
        records = self.store.list(self.collection, limit=LIST_WINDOW)
        if include_paused:
            return records
        return [record for record in records if record["data"].get("active", True)]

    def live(self) -> list[dict[str, Any]]:
        """Only the webhooks that would receive a delivery right now.

        A retired (soft-deleted) row is already invisible to ``store.list``, and
        a paused one is filtered here, so a paused webhook is not merely
        unreadable but genuinely not on the delivery path.
        """
        return [record for record in self.list() if record["data"].get("active", True)]

    # -- writes ------------------------------------------------------------- #

    def build(
        self,
        name: Any,
        target_url: Any,
        *,
        verified_at: str | None = None,
        description: str = "",
        metadata: Mapping[str, Any] | None = None,
        allow_private_target: bool = False,
    ) -> dict[str, Any]:
        """The payload a verified webhook is created from.

        Split from :meth:`create` so the verification POST genuinely happens
        *before* anything is written: a webhook that cannot be verified must
        leave no record at all, or the list fills up with endpoints that have
        never worked and nobody cleaned them up.
        """
        return {
            "name": require_name(name),
            "target_url": validate_target_url(target_url, allow_private=allow_private_target),
            "description": description or "",
            "active": True,
            "secret": signing.generate_secret(),
            "previous_secret": None,
            "previous_secret_confirmed": False,
            "key_rotated_at": None,
            "verified_at": verified_at,
            "verified_status": None,
            "subscriptions": 0,
            "deliveries": 0,
            "failures": 0,
            "last_delivered_at": None,
            "last_state": None,
            "metadata": dict(metadata or {}),
        }

    def create(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
        verified_status: int | None = None,
    ) -> dict[str, Any]:
        data = dict(payload)
        data["verified_status"] = verified_status
        return self.store.create(self.collection, data, actor=actor, source=source)

    def update(
        self,
        webhook_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Patch a webhook.

        ``name``, ``description``, ``active`` and ``metadata`` are the editable
        fields. The target URL, the secret, and the previous secret are
        deliberately **not** patchable: changing a URL re-opens the verification
        question, and changing a secret is the rotate route so the overlap
        rules apply. A caller that sends one of them is refused rather than
        silently ignored, because a PATCH that quietly drops half its body is
        how a rep ends up believing they changed the target.
        """
        refused = sorted(set(patch) & {"secret", "previous_secret", "target_url", "verified_at"})
        if refused:
            raise TargetError(
                f"{', '.join(refused)} cannot be patched",
                code="field_not_patchable",
                remediation=(
                    "Rotate the secret with POST .../key/rotate so the previous-secret "
                    "overlap applies; a new target URL means a new webhook, because the "
                    "new URL has not been verified with a POST."
                ),
            )
        unknown = sorted(set(patch) - {"name", "description", "active", "metadata"})
        if unknown:
            raise TargetError(
                f"unknown field(s): {', '.join(unknown)}",
                code="field_unknown",
                remediation="Patchable fields are name, description, active and metadata.",
            )
        if "name" in patch:
            patch = {**patch, "name": require_name(patch["name"])}
        if "active" in patch and not isinstance(patch["active"], bool):
            raise TargetError("active must be true or false", code="active_invalid")
        self.require(webhook_id)
        return self.store.update(webhook_id, patch, actor=actor, source=source)

    def retire(self, webhook_id: str, *, actor: str | None = None, source: str) -> dict[str, Any]:
        """Soft-delete a webhook. The secret and the delivery history stay auditable."""
        self.require(webhook_id)
        return self.store.delete(webhook_id, actor=actor, source=source)

    def rotate(
        self, webhook_id: str, *, actor: str | None = None, source: str, now: Any = None
    ) -> dict[str, Any]:
        """Rotate the signing secret, keeping the old one for the overlap.

        The current secret becomes ``previous_secret`` and a new one becomes
        ``secret``. If a rotation was already in flight, its previous secret is
        dropped rather than chained: only one generation of overlap is ever in
        flight, so a subscriber that has been away for two rotations does not
        keep validating against a key that is two generations old.
        """
        record = self.require(webhook_id)
        data = record["data"]
        current = data.get("secret")
        if not current:
            raise TargetError(
                f"webhook {webhook_id} has no secret to rotate",
                code="no_secret",
                remediation="A webhook created without a secret has nothing to rotate.",
            )
        fresh = signing.generate_secret()
        return self.store.update(
            webhook_id,
            {
                "secret": fresh,
                "previous_secret": current,
                "previous_secret_confirmed": False,
                "key_rotated_at": iso(parse_now(now)),
            },
            actor=actor,
            source=source,
        )

    def record_delivery(
        self,
        webhook_id: str,
        *,
        state: str,
        ok: bool,
        now: Any = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Fold one delivery outcome into the webhook's counters.

        Counters live on the record rather than being computed on read, so the
        list answers "which of my integrations is quietly broken" in one call.
        """
        record = self.require(webhook_id)
        data = record["data"]
        patch: dict[str, Any] = {
            "deliveries": int(data.get("deliveries") or 0) + 1,
            "failures": int(data.get("failures") or 0) + (0 if ok else 1),
            "last_state": state,
        }
        if ok:
            patch["last_delivered_at"] = iso(parse_now(now))
        return self.store.update(webhook_id, patch, actor=actor, source=source)


def summarise_webhook(record: Mapping[str, Any]) -> dict[str, Any]:
    """The list shape: never the secret, only whether there is one.

    Every read path in the product goes through this, so the one thing a rep must
    not be able to do by accident - read a signing key out of a list response -
    is not possible rather than merely discouraged.
    """
    data = record.get("data") or {}
    return {
        "id": record.get("id"),
        "name": data.get("name"),
        "description": data.get("description", ""),
        "target_url": data.get("target_url"),
        "active": bool(data.get("active", True)),
        "signed": bool(data.get("secret")),
        "secret_hint": signing.mask(data.get("secret")),
        "key_rotated_at": data.get("key_rotated_at"),
        "rotation_in_progress": bool(
            data.get("previous_secret") and not data.get("previous_secret_confirmed")
        ),
        "verified_at": data.get("verified_at"),
        "verified_status": data.get("verified_status"),
        "subscriptions": int(data.get("subscriptions") or 0),
        "deliveries": int(data.get("deliveries") or 0),
        "failures": int(data.get("failures") or 0),
        "last_state": data.get("last_state"),
        "last_delivered_at": data.get("last_delivered_at"),
        "created_at": record.get("created_at"),
    }


# --------------------------------------------------------------------------- #
# Subscriptions
# --------------------------------------------------------------------------- #


class SubscriptionBook:
    """The researched Subscriptions page: create, view in detail, pause, unsubscribe."""

    collection = SUBSCRIPTION_COLLECTION

    def __init__(self, store: Any, endpoints: EndpointBook | None = None) -> None:
        self.store = store
        self.endpoints = endpoints or EndpointBook(store)

    # -- reads -------------------------------------------------------------- #

    def get(self, subscription_id: str) -> dict[str, Any] | None:
        record = self.store.get(subscription_id)
        if record is None or record["collection"] != self.collection:
            return None
        return record

    def require(self, subscription_id: str) -> dict[str, Any]:
        record = self.get(subscription_id)
        if record is None:
            raise RecordNotFound(subscription_id)
        return record

    def list(
        self,
        *,
        webhook_id: str | None = None,
        room_id: str | None = None,
        include_paused: bool = True,
    ) -> list[dict[str, Any]]:
        """Subscriptions, newest first.

        ``room_id`` is a scope filter, not an "only that room" filter: a
        subscription with no ``room_id`` receives every room's events, so it is
        included for any scope. ``store.list(room_id=None)`` means *unfiltered*,
        so the unscoped set has to be picked out here rather than asked for.
        """
        records = self.store.list(self.collection, limit=LIST_WINDOW)
        if webhook_id is not None:
            records = [r for r in records if r["data"].get("webhook_id") == webhook_id]
        if room_id is not None:
            records = [r for r in records if r["room_id"] in (None, room_id)]
        if not include_paused:
            records = [r for r in records if r["data"].get("active", True)]
        return records

    def matching(self, event: str, *, room_id: str | None = None) -> list[dict[str, Any]]:
        """Active subscriptions that should receive ``event``.

        A subscription is on the delivery path only when all four hold:

        * it is active - a paused subscription is not paused, it is skipped;
        * its webhook exists and is active - the research's pause is on the
          **Webhook** page, so a paused webhook stops everything under it;
        * the event type is one it asked for;
        * the room scope covers the event's room.

        Returns the webhook record alongside each subscription so the caller
        does not have to re-read it once per candidate.
        """
        found: list[tuple[dict[str, Any], dict[str, Any]]] = []
        seen: set[str] = set()
        for subscription in self.list(room_id=room_id, include_paused=False):
            data = subscription["data"]
            if event not in (data.get("types") or ()):
                continue
            webhook = self.endpoints.get(str(data.get("webhook_id") or ""))
            if webhook is None or not webhook["data"].get("active", True):
                continue
            key = str(webhook["id"])
            if key in seen:
                # Two subscriptions on one webhook both matching is not a
                # conflict, but the caller must not deliver the same payload
                # twice under the same delivery id.
                continue
            seen.add(key)
            found.append((subscription, webhook))
        return found

    # -- writes ------------------------------------------------------------- #

    def create(
        self,
        webhook_id: str,
        types: Any,
        *,
        room_id: str | None = None,
        filter_expression: Any = None,
        description: str = "",
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Subscribe a webhook to one or more event types.

        The filter is compiled here, at subscribe time, so a bad expression is
        refused while the operator is looking at it rather than discovered
        silently next time somebody edits the filter.
        """
        webhook = self.endpoints.require(webhook_id)
        wanted = require_types(types)
        compiled = compile_filter(filter_expression)
        # `room_id` is deliberately NOT in this payload. The store treats it as
        # part of the fixed envelope and strips it out of `data` on the way in, so
        # putting it here would look like it worked while the field silently
        # vanished. The scope lives in the envelope, and
        # :meth:`summarise_subscription` reads it from there.
        data = {
            "webhook_id": str(webhook_id),
            "webhook_name": webhook["data"].get("name"),
            "types": list(wanted),
            # Reported, never a refusal: the research's list is introduced with
            # "include", so a type this build has not read about is stored and
            # flagged rather than rejected.
            "unknown_types": unknown_types(wanted),
            "filter": compiled.expression if compiled else None,
            "active": True,
            "description": description or "",
            "deliveries": 0,
            "deliveries_skipped": 0,
            "deliveries_filtered_out": 0,
            "failures": 0,
            "last_state": None,
            "last_delivered_at": None,
            "paused_at": None,
            "resumed_at": None,
        }
        record = self.store.create(
            self.collection, data, room_id=room_id, actor=actor, source=source
        )
        self._count_subscriptions(webhook_id, actor=actor, source=source)
        return record

    def update(
        self,
        subscription_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
        now: Any = None,
    ) -> dict[str, Any]:
        """Pause, resume, retype, or refilter a subscription.

        The research's own words: a subscription can be "viewed in detail,
        paused or unsubscribed". Pausing is a flag rather than a delete because
        a rep who misconfigures a target should not have to retype the
        subscription to stop the traffic, and the counters survive the pause.
        """
        record = self.require(subscription_id)
        data = record["data"]
        unknown = sorted(set(patch) - {"types", "filter", "active", "description"})
        if unknown:
            # Checked *before* the enrichment below adds derived fields, so a
            # caller cannot smuggle a computed key in through a valid one.
            raise SubscriptionError(
                f"unknown field(s): {', '.join(unknown)}",
                code="field_unknown",
                remediation="Patchable fields are types, filter, active and description.",
            )
        if "types" in patch:
            wanted = require_types(patch["types"])
            patch = {**patch, "types": list(wanted), "unknown_types": unknown_types(wanted)}
        if "filter" in patch:
            compiled = compile_filter(patch["filter"])
            patch = {**patch, "filter": compiled.expression if compiled else None}
        if "active" in patch and not isinstance(patch["active"], bool):
            raise SubscriptionError("active must be true or false", code="active_invalid")
        if not patch:
            return record
        if "active" in patch and patch["active"] and not data.get("active", True):
            patch = {**patch, "paused_at": None, "resumed_at": iso(parse_now(now))}
        if "active" in patch and not patch["active"] and data.get("active", True):
            patch = {**patch, "paused_at": iso(parse_now(now)), "resumed_at": None}
        updated = self.store.update(subscription_id, patch, actor=actor, source=source)
        if "active" in patch:
            # The webhook's live count has to follow a pause, or it would keep
            # reporting a subscription that is off the delivery path.
            self._count_subscriptions(str(data.get("webhook_id") or ""), actor=actor, source=source)
        return updated

    def unsubscribe(
        self, subscription_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Soft-delete a subscription.

        A soft delete, so the cancellation is audited and the record of what was
        sent where outlives the unsubscribe.
        """
        record = self.require(subscription_id)
        result = self.store.delete(subscription_id, actor=actor, source=source)
        self._count_subscriptions(
            str(record["data"].get("webhook_id") or ""), actor=actor, source=source
        )
        return result

    def record_outcome(
        self,
        subscription_id: str,
        *,
        state: str,
        ok: bool,
        attempted: bool = True,
        filter_matched: bool = True,
        now: Any = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Fold one delivery outcome into a subscription's counters.

        ``attempted=False`` and ``filter_matched=False`` are tracked separately
        from ``ok=False``, because "your filter excluded everything" and "your
        endpoint refused" are different problems with the same red number on a
        dashboard, and an operator who cannot tell them apart will debug the
        wrong system.
        """
        record = self.require(subscription_id)
        data = record["data"]
        patch: dict[str, Any] = {"last_state": state}
        if attempted:
            patch["deliveries"] = int(data.get("deliveries") or 0) + 1
            if not filter_matched:
                patch["deliveries_filtered_out"] = int(data.get("deliveries_filtered_out") or 0) + 1
        else:
            patch["deliveries_skipped"] = int(data.get("deliveries_skipped") or 0) + 1
            if not filter_matched:
                # A skip is counted as a skip, and separately as a filter miss,
                # because "your filter excluded everything" and "you were paused"
                # are different answers behind the same red number.
                patch["deliveries_filtered_out"] = int(data.get("deliveries_filtered_out") or 0) + 1
        if not ok:
            patch["failures"] = int(data.get("failures") or 0) + 1
        if ok:
            patch["last_delivered_at"] = iso(parse_now(now))
        return self.store.update(subscription_id, patch, actor=actor, source=source)

    # -- internals ---------------------------------------------------------- #

    def _count_subscriptions(self, webhook_id: str, *, actor: str | None, source: str) -> None:
        if not webhook_id:
            return
        webhook = self.endpoints.get(webhook_id)
        if webhook is None:
            return
        live = [
            record
            for record in self.list(webhook_id=webhook_id)
            if record["data"].get("active", True)
        ]
        self.store.update(
            webhook_id,
            {"subscriptions": len(live)},
            actor=actor,
            source=source,
        )


def summarise_subscription(record: Mapping[str, Any]) -> dict[str, Any]:
    """The list shape for a subscription. Never carries the secret; the
    ``webhook_id`` and the webhook's own hint answer "is this signed?".

    ``room_id`` is read from the envelope, not from ``data``: the store treats it
    as reserved and strips it out of the payload on write, so a payload that
    carried it would look correct in this function and be empty in the database.
    """
    data = record.get("data") or {}
    return {
        "id": record.get("id"),
        "webhook_id": data.get("webhook_id"),
        "webhook_name": data.get("webhook_name"),
        "types": list(data.get("types") or ()),
        "unknown_types": list(data.get("unknown_types") or ()),
        "filter": data.get("filter"),
        "active": bool(data.get("active", True)),
        "description": data.get("description", ""),
        "room_id": record.get("room_id"),
        "deliveries": int(data.get("deliveries") or 0),
        "deliveries_skipped": int(data.get("deliveries_skipped") or 0),
        "deliveries_filtered_out": int(data.get("deliveries_filtered_out") or 0),
        "failures": int(data.get("failures") or 0),
        "last_state": data.get("last_state"),
        "last_delivered_at": data.get("last_delivered_at"),
        "paused_at": data.get("paused_at"),
        "resumed_at": data.get("resumed_at"),
        "created_at": record.get("created_at"),
    }


def compile_for(record: Mapping[str, Any]) -> CompiledFilter | None:
    """The compiled filter of a stored subscription, or ``None``."""
    return compile_filter((record.get("data") or {}).get("filter"))


def types_of(record: Mapping[str, Any]) -> Sequence[str]:
    """The event types a stored subscription asked for."""
    return tuple((record.get("data") or {}).get("types") or ())
