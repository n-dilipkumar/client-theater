"""The event stream: record an activity, fan it out, and remember what happened.

This is the public surface of :mod:`dsr.event_stream`. Everything the HTTP layer
does goes through it, and so does the seeder, which is what makes the demo rows
exactly what the workflow produces rather than rows written by hand.

The order of operations, and why it is that order
-------------------------------------------------
:meth:`EventStream.record_event` writes the event **first**, then delivers, then
records each delivery. Three consequences, all of them wanted:

* a total delivery outage still leaves a durable record of what happened, so a
  warehouse backfill has something to reconcile against;
* every write is on the audited path, and each carries the ``source`` the route
  supplied, so an audit row names the route that served the request;
* the fan-out rows are written while serving that request, so they carry the
  same ``source`` with a note naming the channel. A reader of the audit log
  still starts from the route, which is the point of the rule.

Pausing is skip, not fail
-------------------------
A paused subscription and a paused webhook are *skipped*, and the delivery row
says so with a reason. They are not failures and they are not silent: an
operator who paused a webhook for a fortnight and then looked at the log should
be able to see that the fortnight produced no deliveries *on purpose*.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.db.audited import RecordNotFound
from dsr.event_stream import backfill as backfill_module
from dsr.event_stream import delivery as delivery_module
from dsr.event_stream import payloads as payload_module
from dsr.event_stream import signing
from dsr.event_stream.backfill import RateLimiter
from dsr.event_stream.delivery import DeliveryResult, Transport, UrllibTransport
from dsr.event_stream.errors import (
    DeliveryError,
    EventPayloadError,
    NotPermitted,
    TargetError,
)
from dsr.event_stream.filters import compile_filter
from dsr.event_stream.registry import (
    DELIVERY_COLLECTION,
    EVENT_COLLECTION,
    EndpointBook,
    SubscriptionBook,
    compile_for,
    summarise_subscription,
    summarise_webhook,
    types_of,
)
from dsr.event_stream.targets import (
    iso,
    parse_now,
    verification_failure,
)
from dsr.event_stream.vocabulary import (
    ALWAYS_ASSOCIATED,
    PULL_RESOURCES,
    is_known_event,
    require_event,
    unknown_types,
)
from dsr.store import RecordStore

#: The role the research requires to create a webhook: "You must be an account
#: ``admin`` to create a webhook."
ROLE_ADMIN = "admin"

#: The payload object name a test event carries, for a subscriber that wants to
#: route test traffic away from its warehouse without parsing the body.
TEST_EVENT_LABEL = "test"


class EventStream:
    """One event stream over one audited store.

    Holds nothing but the store handle, a transport, a rate limiter, and a clock
    seam. That is why it is built per request from
    :data:`dsr.deps.StoreDep` rather than parked on ``app.state``: putting it
    there would be an edit to the shared app, and the transport would stop being
    overridable by a test.
    """

    def __init__(
        self,
        store: RecordStore,
        *,
        transport: Transport | None = None,
        rate_limiter: RateLimiter | None = None,
        now: Any = None,
        timeout: float | None = None,
    ) -> None:
        self.store = store
        self.transport = transport or UrllibTransport()
        self.rate_limiter = rate_limiter or RateLimiter()
        #: A fixed moment, for a deterministic seed. ``None`` means "read the
        #: clock", which is what every request does.
        self.now = now
        self.timeout = delivery_module.DEFAULT_TIMEOUT if timeout is None else timeout
        self.endpoints = EndpointBook(store)
        self.subscriptions = SubscriptionBook(store, self.endpoints)

    # -- clock -------------------------------------------------------------- #

    def moment(self, override: Any = None) -> str:
        """Now, as a wire timestamp, honouring the fixed-clock seam."""
        return iso(parse_now(override if override is not None else self.now))

    # ----------------------------------------------------------------------- #
    # Webhooks
    # ----------------------------------------------------------------------- #

    def create_webhook(
        self,
        name: Any,
        target_url: Any,
        *,
        role: str | None = None,
        description: str = "",
        metadata: Mapping[str, Any] | None = None,
        actor: str | None = None,
        source: str,
        allow_private_target: bool = False,
    ) -> dict[str, Any]:
        """Create a webhook, after verifying the target with a POST.

        The researched user flow, in order: "Click **Create Webhook**, name it,
        and enter the HTTPS target URL (Dock verifies the URL with a POST). Copy
        the generated **secret** (View Key)".

        So the order is enforced: role checked, name and URL validated, the
        verification POST sent, and only then the record written. A URL that
        would not accept the POST means **no webhook is created** - the refusal
        writes nothing at all, so the list never fills with endpoints that have
        never worked.

        The response carries the secret once, because the operator's next step
        in the researched flow is to copy it. Every later read masks it.
        """
        self.require_admin(role, action="create a webhook")
        payload = self.endpoints.build(
            name,
            target_url,
            verified_at=self.moment(),
            description=description,
            metadata=metadata,
            allow_private_target=allow_private_target,
        )
        verification = self.verify_target(payload["target_url"], payload, secret=payload["secret"])
        record = self.endpoints.create(
            payload, actor=actor, source=source, verified_status=verification["http_status"]
        )
        return {
            "webhook": summarise_webhook(record),
            # Shown exactly once. The researched flow's "Copy the generated
            # secret" is a step that has to happen somewhere, and this is it.
            "secret": payload["secret"],
            "secret_note": (
                "Copy this now. Read paths return a masked hint, and the only other way "
                "to see it again is POST the webhook's /key route."
            ),
            "verification": verification,
        }

    def require_admin(self, role: str | None, *, action: str) -> str:
        """Enforce "You must be an account ``admin`` to create a webhook."

        Absent is refused as firmly as wrong. Defaulting the role would make the
        rule unobservable, and a permission rule that cannot be seen failing is
        not a rule.
        """
        if role is None or not str(role).strip():
            raise NotPermitted(
                f"an account {ROLE_ADMIN} role is required to {action}",
                remediation=(
                    "Send role=admin. The research states the rule exactly: you must be an "
                    "account admin to create a webhook."
                ),
            )
        if str(role).strip().lower() != ROLE_ADMIN:
            raise NotPermitted(
                f"role {role!r} may not {action}",
                remediation=f"Send role={ROLE_ADMIN}.",
            )
        return ROLE_ADMIN

    def verify_target(
        self,
        url: str,
        webhook: Mapping[str, Any],
        *,
        secret: str | None = None,
    ) -> dict[str, Any]:
        """The POST that verifies a target URL answers.

        The payload is the researched "Send test events" shape, marked
        ``test: true``: so the endpoint sees a well-formed ``webhook-event``
        during setup, and one mechanism covers both researched steps rather than
        inventing a second, undocumented handshake.

        Returns the outcome rather than the report, because the caller only needs
        three facts - did it answer, what status, how long - and the report's own
        serialisation is a delivery row's business.
        """
        payload = self.test_payload(webhook)
        report = delivery_module.attempt_delivery(
            self.transport,
            url,
            payload,
            event=TEST_EVENT_LABEL,
            delivery_id=f"verify-{payload['id']}",
            attempt=1,
            secret=secret,
            timeout=self.timeout,
            now=self.now,
        )
        if not report.ok:
            raise verification_failure(url, report.result.status, report.result.error, report.result.body)
        return {
            "state": report.state,
            "http_status": report.result.status,
            "duration_ms": report.result.duration_ms,
            "attempt": report.attempt,
            "at": report.at,
        }

    def test_payload(self, webhook: Mapping[str, Any], *, index: int = 0) -> dict[str, Any]:
        """The ``webhook-event`` a verification POST or "Send test events" sends.

        Marked ``test: true`` so a subscriber can route it away from its
        warehouse. Inference ``test-events-are-marked``: the research names the
        affordance ("**Send test events**") but not the payload, and a payload
        indistinguishable from a real event would poison a real pipeline.
        """
        data = webhook.get("data") if "data" in webhook else webhook
        return payload_module.build_event_payload(
            {
                "event": "workspace.viewed",
                "property_name": "workspace.activity",
                "property_value": "test",
                "associated_objects": {
                    "workspace": {"id": str(data.get("id") or "workspace-under-test")},
                    "account": {"id": str(data.get("account_id") or "account-under-test")},
                    # No user. The researched rule is that anonymous activity
                    # omits `user`, and a verification POST has no user, so a
                    # test event is also the honest demonstration of that rule.
                },
                "metadata": {"kind": "verification" if index == 0 else "test-event", "index": index},
            },
            event_id=f"test-{index}",
            now=self.now,
            test=True,
        )

    def list_webhooks(self, *, include_paused: bool = True) -> list[dict[str, Any]]:
        return [summarise_webhook(record) for record in self.endpoints.list(include_paused=include_paused)]

    def read_webhook(self, webhook_id: str) -> dict[str, Any]:
        """One webhook, with its subscriptions.

        The live count from the record and the list itself are both returned, under
        different keys: the count is what the row maintains, and the list is what
        an operator actually reads. Overwriting one with the other would make the
        counter untestable.
        """
        record = self.endpoints.require(webhook_id)
        summary = summarise_webhook(record)
        summary["subscription_count"] = summary.pop("subscriptions")
        summary["subscriptions"] = [
            summarise_subscription(sub) for sub in self.subscriptions.list(webhook_id=webhook_id)
        ]
        return summary

    def update_webhook(
        self,
        webhook_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        return summarise_webhook(
            self.endpoints.update(webhook_id, patch, actor=actor, source=source)
        )

    def retire_webhook(
        self, webhook_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Retire a webhook and unsubscribe everything under it.

        One action, because "I no longer want this integration" is one decision,
        and leaving live subscriptions pointing at a retired webhook would keep
        the row count climbing for an endpoint nobody looks at again.
        """
        self.endpoints.require(webhook_id)
        for subscription in self.subscriptions.list(webhook_id=webhook_id):
            self.subscriptions.unsubscribe(subscription["id"], actor=actor, source=source)
        self.endpoints.retire(webhook_id, actor=actor, source=source)
        return {"id": webhook_id, "retired": True, "deleted": True, "hard": False}

    def reveal_key(self, webhook_id: str) -> dict[str, Any]:
        """The researched **View Key**: read the secret again.

        Writes nothing, so there is no audit row for it. That is the right
        trade: this product audits mutations, and auditing a read of a signing
        secret would put the secret into the audit log every time somebody opened
        the Subscriptions page. The honest consequence is that a key read is not
        traceable, which is recorded in the ``secret-is-stored-in-the-record``
        entry in :mod:`dsr.event_stream.inferences` alongside the louder
        problem - the secret is in ``records.data`` and therefore in the audit
        row for the write that created it.
        """
        record = self.endpoints.require(webhook_id)
        secret = record["data"].get("secret")
        if not secret:
            raise TargetError(
                f"webhook {webhook_id} has no secret",
                code="no_secret",
                remediation="This webhook was created unsigned, so there is no key to view.",
            )
        return {"id": webhook_id, "secret": secret, "secret_hint": signing.mask(secret)}

    def rotate_key(
        self, webhook_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Rotate the signing secret, keeping the old one for the overlap.

        Returns the new secret once, exactly like creation: the researched flow's
        step after a rotation is to deploy it.
        """
        record = self.endpoints.rotate(webhook_id, actor=actor, source=source)
        return {
            "id": webhook_id,
            "secret": record["data"]["secret"],
            "secret_hint": signing.mask(record["data"]["secret"]),
            "key_rotated_at": record["data"]["key_rotated_at"],
            "previous_secret_offered_until": (
                "the first delivery that succeeds under the new secret, or the next rotation"
            ),
        }

    # ----------------------------------------------------------------------- #
    # Subscriptions
    # ----------------------------------------------------------------------- #

    def create_subscription(
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
        return summarise_subscription(
            self.subscriptions.create(
                webhook_id,
                types,
                room_id=room_id,
                filter_expression=filter_expression,
                description=description,
                actor=actor,
                source=source,
            )
        )

    def list_subscriptions(
        self,
        *,
        webhook_id: str | None = None,
        room_id: str | None = None,
        include_paused: bool = True,
    ) -> list[dict[str, Any]]:
        return [
            summarise_subscription(record)
            for record in self.subscriptions.list(
                webhook_id=webhook_id, room_id=room_id, include_paused=include_paused
            )
        ]

    def read_subscription(self, subscription_id: str) -> dict[str, Any]:
        """A subscription "viewed in detail", as the research describes.

        Detail means the compiled filter and the recent deliveries for it, not
        just the row: a subscription is only understandable next to what it has
        actually received.
        """
        record = self.subscriptions.require(subscription_id)
        summary = summarise_subscription(record)
        compiled = compile_for(record)
        summary["filter"] = compiled.expression if compiled else None
        summary["filter_compiled"] = compiled.to_dict() if compiled else None
        summary["deliveries_recent"] = self.list_deliveries(
            subscription_id=subscription_id, limit=10
        )
        return summary

    def update_subscription(
        self,
        subscription_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
        now: Any = None,
    ) -> dict[str, Any]:
        return summarise_subscription(
            self.subscriptions.update(
                subscription_id, patch, actor=actor, source=source, now=now if now is not None else self.now
            )
        )

    def unsubscribe(
        self, subscription_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        return self.subscriptions.unsubscribe(subscription_id, actor=actor, source=source)

    # ----------------------------------------------------------------------- #
    # Test events
    # ----------------------------------------------------------------------- #

    def send_test_events(
        self,
        webhook_id: str,
        *,
        types: Sequence[str] | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """The researched "**Send test events**" during setup.

        One test event per subscribed type by default, so an operator who
        subscribed to nine types sees nine shapes arrive and can verify each one.
        The result is a real delivery row per event, so a test that fails is
        visible in the same place a real failure is - not in a toast.
        """
        record = self.endpoints.require(webhook_id)
        subscriptions = [
            sub
            for sub in self.subscriptions.list(webhook_id=webhook_id, include_paused=False)
        ]
        wanted = list(types) if types else sorted({t for sub in subscriptions for t in types_of(sub)})
        if not wanted:
            raise DeliveryError(
                f"webhook {webhook_id} has no active subscription to test",
                code="nothing_to_test",
                remediation=(
                    "Create a subscription first - there is no type to send a test event for. "
                    "POST /webhooks/{id}/subscriptions with at least one type."
                ),
            )

        data = record["data"]
        sent: list[dict[str, Any]] = []
        for index, event in enumerate(wanted, start=1):
            normalised = require_event(event)
            payload = self.test_payload(data, index=index)
            payload["event"] = normalised
            report = delivery_module.attempt_delivery(
                self.transport,
                str(data["target_url"]),
                payload,
                event=normalised,
                delivery_id=f"{record['id']}:test:{index}",
                attempt=1,
                secret=data.get("secret"),
                previous_secret=signing.rotation_overlap(data),
                timeout=self.timeout,
                now=self.now,
            )
            stored = self._store_delivery(
                event_id=f"test-{index}",
                event=normalised,
                subscription_id=None,
                webhook=record,
                room_id=None,
                report=report,
                state=delivery_module.TESTED if report.ok else report.state,
                test=True,
                actor=actor,
                source=source,
            )
            sent.append(
                {
                    "event": normalised,
                    "known": is_known_event(normalised),
                    "state": stored["data"]["state"],
                    "http_status": stored["data"].get("http_status"),
                    "delivery_id": stored["id"],
                }
            )
        return {
            "webhook_id": webhook_id,
            "count": len(sent),
            "events": sent,
            "known_types": unknown_types(wanted),
        }

    # ----------------------------------------------------------------------- #
    # Events
    # ----------------------------------------------------------------------- #

    def record_event(
        self,
        event: Any,
        *,
        room_id: str | None = None,
        property_name: str | None = None,
        property_previous_value: Any = None,
        property_value: Any = None,
        associated_objects: Mapping[str, Any] | None = None,
        asset: Mapping[str, Any] | None = None,
        form_questions: Any = None,
        form_question_responses: Any = None,
        share_link: str | None = None,
        occurred_at: Any = None,
        account: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        actor: str | None = None,
        source: str,
        test: bool = False,
    ) -> dict[str, Any]:
        """Record a workspace activity and fan it out to the subscriptions.

        Validated before anything is written: an event type that is not a string,
        a ``workspace.page.viewed`` with no ``workspacePage``, a
        ``presentation.viewed`` with no share link, a ``file_upload`` response
        with no ``expiresAt``. Every one of those is refused here, so a
        subscriber can rely on the payload's shape rather than defensively
        re-checking it.
        """
        normalised = require_event(event)
        objects = payload_module.normalise_associated_objects(associated_objects, event=normalised)

        if payload_module.is_share_link_event(normalised) and not share_link:
            raise EventPayloadError(
                f"{normalised} is only emitted for share-link activity",
                code="share_link_required",
                remediation=(
                    "The research is explicit: presentation events are share-link activity only. "
                    "Ordinary asset link activity is asset.viewed, asset.shared and "
                    "asset.downloaded, which need no share link."
                ),
            )

        data: dict[str, Any] = {
            "event": normalised,
            "known": is_known_event(normalised),
            "property_name": property_name,
            "property_previous_value": property_previous_value,
            "property_value": property_value,
            "associated_objects": objects,
            "anonymous": payload_module.is_anonymous(objects),
            "occurred_at": self.moment(occurred_at),
            "account": account,
            "metadata": dict(metadata or {}),
            "test": bool(test),
            "deliveries": 0,
            "skipped": 0,
        }
        if share_link:
            data[payload_module.SHARE_LINK_FIELD] = share_link
        if asset is not None:
            data["asset"] = payload_module.normalise_asset_snapshot(asset)
        if payload_module.is_form_event(normalised):
            questions = payload_module.normalise_form_questions(form_questions)
            data["form_questions"] = questions
            data["form_question_responses"] = payload_module.normalise_form_responses(
                form_question_responses, questions, now=self.now
            )

        record = self.store.create(EVENT_COLLECTION, data, room_id=room_id, actor=actor, source=source)
        payload = payload_module.build_event_payload(data, event_id=record["id"], now=self.now)
        deliveries = self._fan_out(record, data, payload, actor=actor, source=source)
        record = self.store.update(
            record["id"],
            {"deliveries": len([d for d in deliveries if d["attempted"]]),
             "skipped": len([d for d in deliveries if not d["attempted"]])},
            actor=actor,
            source=source,
        )
        return {
            "event": record,
            "payload": payload,
            "anonymous": bool(data["anonymous"]),
            "known_type": bool(data["known"]),
            "deliveries": deliveries,
        }

    def list_events(
        self,
        *,
        room_id: str | None = None,
        event: str | None = None,
        known: bool | None = None,
        anonymous: bool | None = None,
        where: Mapping[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Recorded events, newest first.

        Filters on payload paths through the dynamic index, so a team that added
        ``metadata.campaign`` can ask for it on this same call without a change
        to the route. ``where`` is merged after the named filters and wins on a
        collision, so a caller can always narrow further than the parameters
        allow.
        """
        filters: dict[str, Any] = dict(where or {})
        if event:
            filters["event"] = require_event(event)
        if known is not None:
            filters["known"] = known
        if anonymous is not None:
            filters["anonymous"] = anonymous
        # `store.list(room_id=...)` would exclude the unscoped rows, and an
        # unscoped subscription is delivered every room's activity - so the room
        # scope is applied here, where "this room or every room" is expressible.
        records = (
            self.store.find(EVENT_COLLECTION, filters, limit=limit)
            if filters
            else self.store.list(EVENT_COLLECTION, limit=limit)
        )
        if room_id is not None:
            records = [r for r in records if r["room_id"] in (None, room_id)]
        return records

    def read_event(self, event_id: str) -> dict[str, Any]:
        record = self.store.get(event_id)
        if record is None or record["collection"] != EVENT_COLLECTION:
            raise RecordNotFound(event_id)
        payload = payload_module.build_event_payload(record["data"], event_id=record["id"], now=self.now)
        return {"event": record, "payload": payload}

    # ----------------------------------------------------------------------- #
    # Deliveries
    # ----------------------------------------------------------------------- #

    def list_deliveries(
        self,
        *,
        room_id: str | None = None,
        subscription_id: str | None = None,
        webhook_id: str | None = None,
        event: str | None = None,
        state: str | None = None,
        where: Mapping[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Deliveries, newest first, filtered through the dynamic index."""
        filters: dict[str, Any] = dict(where or {})
        if subscription_id:
            filters["subscription_id"] = subscription_id
        if webhook_id:
            filters["webhook_id"] = webhook_id
        if event:
            filters["event"] = require_event(event)
        if state:
            filters["state"] = require_state(state)
        records = (
            self.store.find(DELIVERY_COLLECTION, filters, limit=limit)
            if filters
            else self.store.list(DELIVERY_COLLECTION, limit=limit)
        )
        if room_id is not None:
            records = [r for r in records if r["room_id"] in (None, room_id)]
        return records

    def read_delivery(self, delivery_id: str) -> dict[str, Any]:
        record = self.store.get(delivery_id)
        if record is None or record["collection"] != DELIVERY_COLLECTION:
            raise RecordNotFound(delivery_id)
        return record

    def retry_delivery(
        self, delivery_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Perform the next attempt on a delivery.

        The researched ladder is a *schedule* rather than a sleep, and this is
        the route that spends it. A deployment drives it from a queue on
        ``next_attempt_at``; an operator drives it now when an endpoint has come
        back and they do not want to wait an hour.

        Refuses a delivery that is not in a retryable state, rather than sending
        it anyway: retrying a delivered payload is how a warehouse gets the same
        row twice, and a subscriber that deduplicates on the delivery id would
        be the only thing standing between an operator and a duplicate.
        """
        record = self.read_delivery(delivery_id)
        data = record["data"]
        state = str(data.get("state"))
        if state != delivery_module.RETRYING:
            raise DeliveryError(
                f"delivery {delivery_id} is {state}, not retrying",
                code="not_retryable",
                remediation=(
                    f"Only a delivery in state {delivery_module.RETRYING!r} can be retried. "
                    "A delivered, failed, skipped or tested delivery is final."
                ),
            )

        webhook = self.endpoints.require(str(data.get("webhook_id") or ""))
        payload = data.get("payload") or {}
        report = delivery_module.next_attempt(
            self.transport,
            str(webhook["data"]["target_url"]),
            payload,
            data,
            event=str(data.get("event") or ""),
            delivery_id=str(record["id"]),
            secret=webhook["data"].get("secret"),
            previous_secret=signing.rotation_overlap(webhook["data"]),
            timeout=self.timeout,
            now=self.now,
        )
        stored = self.store.update(
            delivery_id,
            report.to_dict(),
            actor=actor,
            source=source,
        )
        self._fold_outcome(
            stored, state=report.state, ok=report.ok, attempt_number=report.attempt, actor=actor, source=source
        )
        if report.ok:
            # A successful delivery closes a key rotation overlap: the new
            # secret has demonstrably reached a subscriber that accepted it.
            confirm = signing.confirm_rotation(webhook["data"])
            if confirm:
                self.store.update(
                    webhook["id"], confirm, actor=actor, source=source
                )
        return stored

    def _fan_out(
        self,
        event_record: Mapping[str, Any],
        data: Mapping[str, Any],
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> list[dict[str, Any]]:
        """Deliver one event to every subscription that should receive it.

        Also records the ones that should *not*, as ``skipped``, because a paused
        subscription that produces no row at all is indistinguishable from a
        subscription that was never asked.
        """
        event = str(data["event"])
        room_id = event_record["room_id"]
        delivered: list[dict[str, Any]] = []
        seen_webhooks: set[str] = set()

        # Every candidate, *including* the paused ones. A paused subscription is
        # not on the delivery path, but it still has to produce a row, or "paused"
        # is indistinguishable from "never asked" and the fortnight a rep paused
        # an integration for leaves no trace at all.
        candidates = self.subscriptions.list(room_id=room_id, include_paused=True)
        for subscription in candidates:
            sub_data = subscription["data"]
            if event not in (sub_data.get("types") or ()):
                continue
            webhook = self.endpoints.require(str(sub_data["webhook_id"]))
            subscription_id = str(subscription["id"])
            webhook_id = str(webhook["id"])

            # One predicate for "should not be sent", evaluated in the order an
            # operator would want to read the resulting reason: the webhook first,
            # then the subscription, then the filter. The three reasons are the
            # researched pause plus the one filter rule, and they are the only
            # three ways a delivery is deliberately withheld.
            compiled = compile_for(subscription)
            if not webhook["data"].get("active", True):
                reason = "webhook_paused"
            elif not sub_data.get("active", True):
                reason = "subscription_paused"
            elif compiled is not None and not compiled.matches(payload):
                reason = "filter_excluded"
            else:
                reason = None
            if reason is not None:
                delivered.append(
                    self._skip(
                        event_id=str(event_record["id"]),
                        event=event,
                        subscription=subscription,
                        webhook=webhook,
                        room_id=room_id,
                        reason=reason,
                        actor=actor,
                        source=source,
                    )
                )
                continue

            if webhook_id in seen_webhooks:
                # Two subscriptions on one webhook both matching is not a
                # conflict, but the payload, the target, and the delivery id would
                # all be identical - a subscriber would see the same event twice.
                continue
            seen_webhooks.add(webhook_id)

            report = delivery_module.attempt_delivery(
                self.transport,
                str(webhook["data"]["target_url"]),
                payload,
                event=event,
                delivery_id=f"{event_record['id']}:{subscription_id}",
                attempt=1,
                secret=webhook["data"].get("secret"),
                previous_secret=signing.rotation_overlap(webhook["data"]),
                timeout=self.timeout,
                now=self.now,
            )
            stored = self._store_delivery(
                event_id=str(event_record["id"]),
                event=event,
                subscription_id=subscription_id,
                webhook=webhook,
                room_id=room_id,
                report=report,
                state=report.state,
                actor=actor,
                source=source,
            )
            self._fold_outcome(
                stored, state=report.state, ok=report.ok, attempt_number=1, actor=actor, source=source
            )
            delivered.append(
                self._delivery_view(
                    stored,
                    subscription_id=subscription_id,
                    webhook_id=webhook_id,
                    attempted=True,
                    filter_matched=True,
                )
            )
        return delivered

    def _skip(
        self,
        *,
        event_id: str,
        event: str,
        subscription: Mapping[str, Any],
        webhook: Mapping[str, Any],
        room_id: str | None,
        reason: str,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Record one deliberate non-delivery, and count it against the subscription.

        A skip is a row *and* a counter, for the same reason: an operator looking
        at a subscription with zero deliveries has to be able to see whether it
        was paused, was filtered out, or simply never matched.
        """
        subscription_id = str(subscription["id"])
        stored = self._store_delivery(
            event_id=event_id,
            event=event,
            subscription_id=subscription_id,
            webhook=webhook,
            room_id=room_id,
            report=None,
            state=delivery_module.SKIPPED,
            reason=reason,
            actor=actor,
            source=source,
        )
        self.subscriptions.record_outcome(
            subscription_id,
            state=delivery_module.SKIPPED,
            ok=True,
            attempted=False,
            filter_matched=reason != "filter_excluded",
            now=self.now,
            actor=actor,
            source=source,
        )
        return self._delivery_view(
            stored,
            subscription_id=subscription_id,
            webhook_id=str(webhook["id"]),
            attempted=False,
            filter_matched=reason != "filter_excluded",
        )

    def _delivery_view(
        self,
        record: Mapping[str, Any],
        *,
        subscription_id: str,
        webhook_id: str,
        attempted: bool,
        filter_matched: bool,
    ) -> dict[str, Any]:
        """One line of the fan-out response, whether or not anything was sent.

        The same shape for a delivery and for a skip, so a caller reading the
        response does not have to know which of the two happened before it can
        find out.
        """
        data = record["data"]
        return {
            "delivery_id": str(record["id"]),
            "subscription_id": subscription_id,
            "webhook_id": webhook_id,
            "state": str(data.get("state")),
            "http_status": data.get("http_status"),
            "reason": data.get("reason"),
            "attempted": attempted,
            "filter_matched": filter_matched,
            "next_attempt_at": data.get("next_attempt_at"),
            "record": dict(record),
        }

    def _store_delivery(
        self,
        *,
        event_id: str,
        event: str,
        subscription_id: str | None,
        webhook: Mapping[str, Any],
        room_id: str | None,
        report: delivery_module.DeliveryReport | None,
        state: str,
        reason: str | None = None,
        test: bool = False,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """One delivery row, whether or not anything was sent.

        ``state`` is applied *after* the report's own state, so a caller can
        override it - which is how a successful test event is recorded as
        ``tested`` rather than ``delivered``, without losing the report's attempt
        record underneath.
        """
        body: dict[str, Any] = {
            "event_id": event_id,
            "event": event,
            "subscription_id": subscription_id,
            "webhook_id": str(webhook["id"]),
            "webhook_name": (webhook.get("data") or {}).get("name"),
            "test": test,
            "attempted": report is not None,
            "reason": reason,
            "at": self.moment(),
        }
        if report is not None:
            body.update(report.to_dict())
            body["payload"] = json_safe(report.body)
        else:
            body.update(
                {
                    "attempt": 0,
                    "attempts_total": 0,
                    "attempts_remaining": 0,
                    "http_status": None,
                    "error": None,
                    "response": {"status": None, "body": ""},
                    "retryable": False,
                    "retry_in_seconds": None,
                    "next_attempt_at": None,
                    "attempt_log": [],
                    "filter_matched": reason != "filter_excluded",
                    "duration_ms": 0.0,
                    "request_headers": {},
                }
            )
        body["state"] = state
        return self.store.create(
            DELIVERY_COLLECTION, body, room_id=room_id, actor=actor, source=source
        )

    def _fold_outcome(
        self,
        record: Mapping[str, Any],
        *,
        state: str,
        ok: bool,
        attempt_number: int,
        actor: str | None,
        source: str,
    ) -> None:
        """Advance the subscription's and the webhook's counters.

        Written while serving the request that caused them, so they carry that
        request's ``source`` - the same rule as every other write here.
        """
        data = record.get("data") or {}
        subscription_id = data.get("subscription_id")
        webhook_id = data.get("webhook_id")
        if subscription_id:
            self.subscriptions.record_outcome(
                str(subscription_id),
                state=state,
                ok=ok,
                attempted=bool(data.get("attempted", True)),
                filter_matched=bool(data.get("filter_matched", True)),
                now=self.now,
                actor=actor,
                source=source,
            )
        if webhook_id and attempt_number == 1:
            # Only the first attempt counts towards the webhook's totals, or a
            # retry would inflate them and a broken integration would look
            # busier than a working one.
            self.endpoints.record_delivery(
                str(webhook_id), state=state, ok=ok, now=self.now, actor=actor, source=source
            )

    # ----------------------------------------------------------------------- #
    # Backfill
    # ----------------------------------------------------------------------- #

    def backfill(
        self,
        resource: str,
        *,
        properties: Any = None,
        limit: int = 100,
        room_id: str | None = None,
        form_id: str | None = None,
        record_id: str | None = None,
        caller: str = "anonymous",
    ) -> dict[str, Any]:
        """One researched pull resource, rate limited and projected.

        The rate limit is charged *before* the query, so a caller that is over
        its allowance gets a 429 without the database work having happened.
        """
        if resource not in PULL_RESOURCES:
            raise DeliveryError(
                f"unknown backfill resource {resource!r}",
                code="unknown_resource",
                status=404,
                remediation=f"Known resources: {', '.join(sorted(PULL_RESOURCES))}.",
            )
        limit_state = self.rate_limiter.check(caller)
        spec = PULL_RESOURCES[resource]
        available = [field["path"] for field in self.store.fields(str(spec["collection"]))]
        wanted = backfill_module.require_properties(properties, available)
        result = backfill_module.pull(
            self.store,
            resource,
            properties=wanted,
            limit=limit,
            room_id=room_id,
            form_id=form_id,
            record_id=record_id,
        )
        result["rate_limit"] = limit_state
        return result

    def backfill_rate_limit(self) -> dict[str, Any]:
        return backfill_module.describe_rate_limit(self.rate_limiter)

    # ----------------------------------------------------------------------- #
    # Summary
    # ----------------------------------------------------------------------- #

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Counts for the page header, scoped exactly to the rows shown.

        Counting what a request returned rather than the whole table keeps a
        filtered view from reporting totals the operator is not looking at.
        """
        events = self.list_events(room_id=room_id, limit=1000)
        deliveries = self.list_deliveries(room_id=room_id, limit=1000)
        states: dict[str, int] = {}
        reasons: dict[str, int] = {}
        for record in deliveries:
            data = record["data"]
            states[str(data.get("state"))] = states.get(str(data.get("state")), 0) + 1
            if data.get("reason"):
                reasons[str(data["reason"])] = reasons.get(str(data["reason"]), 0) + 1
        anonymous = sum(1 for record in events if record["data"].get("anonymous"))
        all_subscriptions = self.subscriptions.list(room_id=room_id)
        live_subscriptions = [s for s in all_subscriptions if s["data"].get("active", True)]
        every_webhook = self.endpoints.list()
        return {
            "room_id": room_id,
            "webhooks": len(self.endpoints.live()),
            "webhooks_paused": len(every_webhook) - len(self.endpoints.live()),
            "subscriptions": len(live_subscriptions),
            "subscriptions_paused": len(all_subscriptions) - len(live_subscriptions),
            "events": len(events),
            "events_anonymous": anonymous,
            "deliveries": len(deliveries),
            "delivery_states": states,
            "skip_reasons": reasons,
            "rate_limit": self.backfill_rate_limit(),
        }


def require_state(value: Any) -> str:
    """Validate a delivery state filter against the published set."""
    text = str(value or "").strip()
    if text not in delivery_module.DELIVERY_STATES:
        raise DeliveryError(
            f"unknown delivery state {text!r}",
            code="unknown_state",
            status=400,
            remediation=f"States are: {', '.join(delivery_module.DELIVERY_STATES)}.",
        )
    return text


def json_safe(raw: bytes) -> Any:
    """The bytes a delivery went out with, decoded.

    The stored payload is what the signature was computed over, so a subscriber
    dispute can be settled from the row itself. Decoded rather than base64 so it
    is readable in the JSON view; it was serialised with ``default=str`` so it is
    always decodable.
    """
    import json

    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):  # pragma: no cover - defensive
        return raw.decode("utf-8", "replace")


__all__ = [
    "EventStream",
    "ROLE_ADMIN",
    "ALWAYS_ASSOCIATED",
    "require_state",
    "json_safe",
]
