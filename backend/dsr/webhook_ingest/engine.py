"""WF-113: the store-facing half - register, ingest, dedupe, fan out, record.

This is the only module in the package that reads or writes. It builds on
:mod:`dsr.webhook_ingest.rules`, which is pure, so the interesting decisions are
testable without a database.

What one ingest does, in order
------------------------------
1. Resolve the generated URL's token to an endpoint, and the endpoint to its
   automation. A token this build did not issue is a 404.
2. Refuse a draft automation. The flow starts the automation deliberately, and a
   buffered ingest is indistinguishable to the sender from a lost one.
3. Verify the signature, but only when the automation carries a secret. The
   researched flow copies one generated URL and names no signing step.
4. Parse the body into the Trigger data object. Any JSON shape parses.
5. De-duplicate on the body's fingerprint. A repeat is recorded as a duplicate
   and fans out nothing.
6. Fan out. An over-limit batch is recorded as rejected and refused; every other
   refusal is a delivery row, not an exception.

``source`` is a required keyword on every writing method, so the audit row names
the route that actually served the write. A URL string hardcoded inside a domain
method is a defect: a feature's audit log has named a path the app stopped
serving in this codebase before.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.store import RecordStore
from dsr.webhook_ingest import inferences as infer, rules, signing, vocabulary as vocab
from dsr.webhook_ingest.errors import (
    AutomationNotStarted,
    BatchLimitExceeded,
    InvalidAutomation,
    SignatureMismatch,
    SignatureMissing,
    UnknownAutomation,
    UnknownDelivery,
    UnknownEndpoint,
    UnknownEvent,
    UnknownTrigger,
    WebhookIngestError,
)

__all__ = ["WebhookIngest"]

_PAGE = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WebhookIngest:
    """The reads and writes, over an audited store.

    Built per request by the HTTP layer rather than hung off ``app.state``, so a
    test can substitute a clock without editing a shared file. ``now`` is a
    callable, not a value, because a module-level constant is captured on first
    import and a test that sets the clock afterwards would keep the old one.
    """

    def __init__(self, store: RecordStore, *, now: Any = None) -> None:
        self.store = store
        self.now = now or _now

    # -- clock --------------------------------------------------------------- #

    def clock(self) -> datetime:
        value = self.now()
        return value if getattr(value, "tzinfo", None) else value.replace(tzinfo=timezone.utc)

    def _stamp(self) -> str:
        return self.clock().isoformat(timespec="milliseconds")

    # -- automations and their generated URLs -------------------------------- #

    def create_automation(
        self,
        room_ref: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Register an automation in a room and generate its webhook URL.

        A new automation lands a draft, because the researched flow is *Review
        items -> Start Automation* and starting is a deliberate second act. The
        generated URL exists from the moment the automation does, so a team can
        copy it before starting - which is exactly the order the flow describes.
        """
        name = str((payload or {}).get("name") or "").strip()
        if not name:
            raise InvalidAutomation("an automation needs a name.", name="a name is required.")

        identifier_type = str((payload or {}).get("identifier_type") or "id").strip().lower()
        if not vocab.is_known_identifier_type(identifier_type):
            raise InvalidAutomation(
                f"identifier_type {identifier_type!r} is not one of {list(vocab.IDENTIFIER_TYPE_IDS)}.",
                identifier_type=f"must be one of {list(vocab.IDENTIFIER_TYPE_IDS)}",
            )

        actions = self._validate_actions((payload or {}).get("actions") or [])
        secret = str((payload or {}).get("secret") or "").strip() or None

        automation = self.store.create(
            vocab.COLLECTION_AUTOMATION,
            {
                "name": name,
                "status": vocab.STATUS_DRAFT,
                "identifier_type": identifier_type,
                "actions": actions,
                "action_count": len(actions),
                "secret": secret,
                "has_secret": bool(secret),
            },
            room_id=str(room_ref),
            actor=actor,
            source=source,
        )
        endpoint = self._generate_endpoint(automation, str(room_ref), actor=actor, source=source)
        return self._automation_summary(automation, endpoint)

    def _validate_actions(self, actions: Any) -> list[dict[str, Any]]:
        rows = actions if isinstance(actions, (list, tuple)) else []
        out: list[dict[str, Any]] = []
        for action in rows:
            if not isinstance(action, Mapping):
                raise InvalidAutomation("every action must be an object.")
            key = str(action.get("key") or "")
            if not vocab.is_known_action(key):
                raise InvalidAutomation(
                    f"action {key!r} is not one of {list(vocab.ACTION_KEYS)}.",
                    action=f"must be one of {list(vocab.ACTION_KEYS)}",
                )
            value_type = str(action.get("value_type") or vocab.VALUE_TYPE_LIQUID).strip().lower()
            if not vocab.is_known_value_type(value_type):
                raise InvalidAutomation(
                    f"value_type {value_type!r} is not one of {list(vocab.VALUE_TYPE_IDS)}.",
                    value_type=f"must be one of {list(vocab.VALUE_TYPE_IDS)}",
                )
            out.append({**dict(action), "key": key, "value_type": value_type})
        return out

    def _generate_endpoint(
        self,
        automation: Mapping[str, Any],
        room_ref: str,
        *,
        actor: str | None,
        source: str | None,
    ) -> dict[str, Any]:
        token = secrets.token_urlsafe(24)
        return self.store.create(
            vocab.COLLECTION_ENDPOINT,
            {
                "automation_ref": automation["id"],
                "token": token,
                "label": (automation.get("data") or {}).get("name"),
            },
            room_id=room_ref,
            actor=actor,
            source=source,
        )

    def _endpoint_for(self, automation_id: str) -> dict[str, Any] | None:
        rows = self.store.find(
            vocab.COLLECTION_ENDPOINT, {"automation_ref": automation_id}, limit=1
        )
        return rows[0] if rows else None

    def _automation_summary(
        self, automation: Mapping[str, Any], endpoint: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        data = automation.get("data") or {}
        row = endpoint if endpoint is not None else self._endpoint_for(automation["id"])
        token = (row or {}).get("data", {}).get("token")
        return {
            "id": automation["id"],
            "room_ref": automation.get("room_id"),
            "name": data.get("name"),
            "status": data.get("status"),
            "identifier_type": data.get("identifier_type"),
            "actions": data.get("actions") or [],
            "action_count": data.get("action_count", len(data.get("actions") or [])),
            # The secret is never served, only whether one exists.
            "has_secret": bool(data.get("secret")),
            "endpoint_id": row["id"] if row else None,
            "token": token,
            "url": vocab.ENDPOINT_PATH_TEMPLATE.format(token=token) if token else None,
            "created_at": automation.get("created_at"),
            "updated_at": automation.get("updated_at"),
        }

    def automations(self, room_ref: str) -> list[dict[str, Any]]:
        rows = self.store.list(vocab.COLLECTION_AUTOMATION, room_id=str(room_ref), limit=_PAGE)
        return [self._automation_summary(row) for row in rows]

    def automation(self, automation_id: str) -> dict[str, Any]:
        row = self.store.get(automation_id)
        if row is None or row["collection"] != vocab.COLLECTION_AUTOMATION:
            raise UnknownAutomation(f"no automation with id {automation_id}.")
        return self._automation_summary(row)

    def patch_automation(
        self,
        automation_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Change an automation's name, actions, identifier type, status or secret.

        The whole merged automation is re-validated rather than the patch field by
        field, so an amendment cannot smuggle in an action the original create
        would have refused. The secret may be set here but is never returned.
        """
        current = self.automation(automation_id)
        data = dict(current)
        updates: dict[str, Any] = {}

        if "name" in patch and patch["name"] is not None:
            name = str(patch["name"]).strip()
            if not name:
                raise InvalidAutomation("an automation needs a name.", name="a name is required.")
            updates["name"] = name

        if "identifier_type" in patch and patch["identifier_type"] is not None:
            identifier_type = str(patch["identifier_type"]).strip().lower()
            if not vocab.is_known_identifier_type(identifier_type):
                raise InvalidAutomation(
                    f"identifier_type {identifier_type!r} is not one of {list(vocab.IDENTIFIER_TYPE_IDS)}."
                )
            updates["identifier_type"] = identifier_type

        if "actions" in patch:
            actions = self._validate_actions(patch["actions"])
            updates["actions"] = actions
            updates["action_count"] = len(actions)

        if "status" in patch and patch["status"] is not None:
            status = str(patch["status"]).strip().lower()
            if status not in vocab.STATUSES:
                raise InvalidAutomation(f"status {status!r} is not one of {list(vocab.STATUSES)}.")
            updates["status"] = status

        if "secret" in patch:
            secret = str(patch["secret"] or "").strip() or None
            updates["secret"] = secret
            updates["has_secret"] = bool(secret)

        data.update(updates)
        self.store.update(automation_id, updates, actor=actor, source=source)
        return self.automation(automation_id)

    def start_automation(
        self, automation_id: str, *, actor: str | None = None, source: str | None = None
    ) -> dict[str, Any]:
        """The researched step 6: **Review items -> Start Automation**."""
        return self.patch_automation(
            automation_id, {"status": vocab.STATUS_STARTED}, actor=actor, source=source
        )

    # -- ingest -------------------------------------------------------------- #

    def _endpoint_by_token(self, token: str) -> dict[str, Any]:
        rows = self.store.find(vocab.COLLECTION_ENDPOINT, {"token": str(token)}, limit=1)
        if not rows:
            raise UnknownEndpoint(
                "this URL is not a webhook URL this deployment issued. Copy the generated URL "
                "from the automation, or create one."
            )
        return rows[0]

    def _raw_text(self, raw_body: Any, payload: Any) -> str:
        if isinstance(raw_body, bytes):
            return raw_body.decode("utf-8", "replace")
        if isinstance(raw_body, str):
            return raw_body
        return rules.canonical_json(payload)

    def ingest(
        self,
        token: str,
        payload: Any,
        *,
        raw_body: Any = None,
        signature: str | None = None,
        timestamp: str | None = None,
        actor: str = "provider",
        source: str | None = None,
    ) -> dict[str, Any]:
        """One inbound delivery: resolve, verify, parse, dedupe, fan out, record.

        Returns a report rather than raising for a duplicate, because a repeat is
        an outcome of a successful call. It raises only for a delivery this build
        will not act on at all, and each of those leaves a recorded row first.
        """
        endpoint = self._endpoint_by_token(token)
        room_ref = str(endpoint.get("room_id") or "")
        automation = self.store.get((endpoint.get("data") or {}).get("automation_ref") or "")
        if automation is None or automation["collection"] != vocab.COLLECTION_AUTOMATION:
            raise UnknownAutomation(
                "the automation behind this webhook URL no longer exists, so the URL is dead."
            )
        if (automation.get("data") or {}).get("status") != vocab.STATUS_STARTED:
            raise AutomationNotStarted(
                "this automation is not started. Open it and choose Start Automation, then "
                "send the delivery again.",
                automation_ref=automation["id"],
                status=(automation.get("data") or {}).get("status"),
            )

        secret = (automation.get("data") or {}).get("secret")
        if secret:
            ok, reason = signing.verify(
                str(secret),
                self._raw_text(raw_body, payload),
                signature=signature,
                timestamp=timestamp,
                now=self.clock(),
            )
            if not ok:
                self._record_trigger(
                    room_ref,
                    rules.trigger_data(
                        payload,
                        endpoint_ref=endpoint["id"],
                        automation_ref=automation["id"],
                        received_at=self._stamp(),
                        identifier_type=str(
                            (automation.get("data") or {}).get("identifier_type") or "id"
                        ),
                    ),
                    state=vocab.TRIGGER_REJECTED,
                    reason=reason,
                    actor=actor,
                    source=source,
                )
                if reason in ("signature_missing", "timestamp_missing"):
                    raise SignatureMissing(
                        "this automation requires a signature. Send the "
                        f"{signing.SIGNATURE_HEADER} and {signing.TIMESTAMP_HEADER} headers.",
                        reason=reason,
                    )
                raise SignatureMismatch(
                    "the signature did not verify against the automation's secret, or the "
                    "timestamp is outside the window.",
                    reason=reason,
                    window_seconds=signing.MAX_AGE_SECONDS,
                )

        identifier_type = str((automation.get("data") or {}).get("identifier_type") or "id")
        trigger = rules.trigger_data(
            payload,
            endpoint_ref=endpoint["id"],
            automation_ref=automation["id"],
            received_at=self._stamp(),
            identifier_type=identifier_type,
        )

        seen = self.store.find(
            vocab.COLLECTION_DEDUPE,
            {"endpoint_ref": endpoint["id"], "fingerprint": trigger["fingerprint"]},
            limit=1,
        )
        if seen:
            row = self._record_trigger(
                room_ref,
                trigger,
                state=vocab.TRIGGER_DUPLICATE,
                duplicate_of=seen[0]["id"],
                actor=actor,
                source=source,
            )
            return {
                "state": vocab.TRIGGER_DUPLICATE,
                "trigger": self._trigger_summary(row),
                "events": [],
                "deliveries": [],
                "fanout": self._fanout_summary(room_ref),
            }

        try:
            plan = rules.plan_fanout(
                (automation.get("data") or {}).get("actions") or [],
                trigger,
                identifier_type=identifier_type,
            )
        except BatchLimitExceeded as exc:
            self._record_trigger(
                room_ref,
                trigger,
                state=vocab.TRIGGER_REJECTED,
                reason=exc.code,
                detail=exc.extra,
                actor=actor,
                source=source,
            )
            raise

        row = self._record_trigger(
            room_ref, trigger, state=vocab.TRIGGER_ACCEPTED, actor=actor, source=source
        )
        self.store.create(
            vocab.COLLECTION_DEDUPE,
            {
                "endpoint_ref": endpoint["id"],
                "fingerprint": trigger["fingerprint"],
                "trigger_ref": row["id"],
                "first_seen_at": self._stamp(),
            },
            room_id=room_ref,
            actor=actor,
            source=source,
        )

        events = [
            self.store.create(
                vocab.COLLECTION_EVENT,
                {
                    "trigger_ref": row["id"],
                    "automation_ref": automation["id"],
                    "action": event["action"],
                    "name": event["name"],
                    "data": event["data"],
                },
                room_id=room_ref,
                actor=actor,
                source=source,
            )
            for event in plan["events"]
        ]
        deliveries = [
            self.store.create(
                vocab.COLLECTION_DELIVERY,
                {
                    "trigger_ref": row["id"],
                    "automation_ref": automation["id"],
                    "action": delivery["action"],
                    "outcome": delivery["outcome"],
                    "reason": delivery["reason"],
                    "count": delivery["count"],
                },
                room_id=room_ref,
                actor=actor,
                source=source,
            )
            for delivery in plan["deliveries"]
        ]
        return {
            "state": vocab.TRIGGER_ACCEPTED,
            "trigger": self._trigger_summary(row),
            "events": [self._event_summary(event) for event in events],
            "deliveries": [self._delivery_summary(delivery) for delivery in deliveries],
            "fanout": self._fanout_summary(room_ref),
        }

    def _record_trigger(
        self,
        room_ref: str,
        trigger: Mapping[str, Any],
        *,
        state: str,
        actor: str,
        source: str | None,
        reason: str | None = None,
        detail: Mapping[str, Any] | None = None,
        duplicate_of: str | None = None,
    ) -> dict[str, Any]:
        return self.store.create(
            vocab.COLLECTION_TRIGGER,
            {
                "endpoint_ref": trigger["endpoint_ref"],
                "automation_ref": trigger["automation_ref"],
                "shape": trigger["shape"],
                "fingerprint": trigger["fingerprint"],
                "identifiers": trigger["identifiers"],
                "attributes": trigger["attributes"],
                "body": trigger["body"],
                "received_at": trigger["received_at"],
                "state": state,
                "reason": reason,
                "detail": dict(detail or {}),
                "duplicate_of": duplicate_of,
            },
            room_id=room_ref,
            actor=actor,
            source=source,
        )

    # -- reads --------------------------------------------------------------- #

    def triggers(
        self, room_ref: str, *, state: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        rows = self.store.list(
            vocab.COLLECTION_TRIGGER, room_id=str(room_ref), limit=max(1, min(int(limit), _PAGE))
        )
        out = [self._trigger_summary(row) for row in rows]
        if state:
            out = [row for row in out if row["state"] == state]
        return {
            "room_ref": str(room_ref),
            "count": len(out),
            "by_state": _tally(out, "state"),
            "triggers": out,
        }

    def trigger(self, trigger_id: str) -> dict[str, Any]:
        return self._trigger_summary(
            self._require(trigger_id, vocab.COLLECTION_TRIGGER, UnknownTrigger)
        )

    def events(self, room_ref: str, *, limit: int = 100) -> dict[str, Any]:
        rows = self.store.list(
            vocab.COLLECTION_EVENT, room_id=str(room_ref), limit=max(1, min(int(limit), _PAGE))
        )
        out = [self._event_summary(row) for row in rows]
        return {
            "room_ref": str(room_ref),
            "count": len(out),
            "by_name": _tally(out, "name"),
            "events": out,
        }

    def event(self, event_id: str) -> dict[str, Any]:
        return self._event_summary(self._require(event_id, vocab.COLLECTION_EVENT, UnknownEvent))

    def deliveries(
        self, room_ref: str, *, outcome: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        rows = self.store.list(
            vocab.COLLECTION_DELIVERY, room_id=str(room_ref), limit=max(1, min(int(limit), _PAGE))
        )
        out = [self._delivery_summary(row) for row in rows]
        if outcome:
            out = [row for row in out if row["outcome"] == outcome]
        return {
            "room_ref": str(room_ref),
            "count": len(out),
            "by_outcome": _tally(out, "outcome"),
            "deliveries": out,
        }

    def delivery(self, delivery_id: str) -> dict[str, Any]:
        return self._delivery_summary(
            self._require(delivery_id, vocab.COLLECTION_DELIVERY, UnknownDelivery)
        )

    def _require(
        self, record_id: str, collection: str, error: type[WebhookIngestError]
    ) -> dict[str, Any]:
        row = self.store.get(record_id)
        if row is None or row["collection"] != collection:
            raise error(f"no {collection} record with id {record_id}.")
        return row

    def _fanout_summary(self, room_ref: str) -> dict[str, Any]:
        return {
            "events": len(
                self.store.list(vocab.COLLECTION_EVENT, room_id=str(room_ref), limit=_PAGE)
            ),
            "deliveries": len(
                self.store.list(vocab.COLLECTION_DELIVERY, room_id=str(room_ref), limit=_PAGE)
            ),
        }

    def summary(self, room_ref: str | None = None) -> dict[str, Any]:
        """Headline numbers for one room, or for the whole workspace.

        Reads only, so it writes no audit row. A route that wrote on every read
        would fill the guarantee's own log with rows describing nothing happening.
        """
        where_room = str(room_ref) if room_ref else None
        automations = self.store.list(vocab.COLLECTION_AUTOMATION, room_id=where_room, limit=_PAGE)
        triggers = [
            self._trigger_summary(row)
            for row in self.store.list(vocab.COLLECTION_TRIGGER, room_id=where_room, limit=_PAGE)
        ]
        events = [
            self._event_summary(row)
            for row in self.store.list(vocab.COLLECTION_EVENT, room_id=where_room, limit=_PAGE)
        ]
        deliveries = [
            self._delivery_summary(row)
            for row in self.store.list(vocab.COLLECTION_DELIVERY, room_id=where_room, limit=_PAGE)
        ]
        return {
            "ticket": vocab.TICKET,
            "room_ref": room_ref,
            "automations": len(automations),
            "started": sum(
                1
                for row in automations
                if (row.get("data") or {}).get("status") == vocab.STATUS_STARTED
            ),
            "triggers": len(triggers),
            "by_state": _tally(triggers, "state"),
            "accepted": sum(1 for row in triggers if row["state"] == vocab.TRIGGER_ACCEPTED),
            "duplicates": sum(1 for row in triggers if row["state"] == vocab.TRIGGER_DUPLICATE),
            "rejected": sum(1 for row in triggers if row["state"] == vocab.TRIGGER_REJECTED),
            "events": len(events),
            "deliveries": len(deliveries),
            "by_outcome": _tally(deliveries, "outcome"),
            "by_skip_reason": _tally([row for row in deliveries if row["reason"]], "reason"),
            "notes": [
                "The webhook is the subject. A mapping that references the person renders an empty string.",
                f"A Batch Update covers up to {vocab.BATCH_LIMIT} people; an over-limit batch is refused, not truncated.",
                "A repeated delivery is recorded as a duplicate and fans out nothing.",
            ],
            "rate_limits_enforced": False,
        }

    def vocabulary(self) -> dict[str, Any]:
        """Every researched term, plus this workflow's signing contract."""
        return {
            "ticket": vocab.TICKET,
            "vocabulary": vocab.describe(),
            "signing": {
                "signature_header": signing.SIGNATURE_HEADER,
                "timestamp_header": signing.TIMESTAMP_HEADER,
                "rule": "HMAC-SHA256(secret, '{timestamp}.{raw_body}'), hex encoded",
                "encoding": "hex",
                "timestamp_unit": "unix seconds",
                "window_seconds": signing.MAX_AGE_SECONDS,
                "required_when": "the automation carries a secret",
                "reused_from": "WF-066 dsr.meeting_webhook_fanout.signing",
            },
            "inferences": infer.count(),
        }

    def inferences(self) -> dict[str, Any]:
        return {
            "ticket": vocab.TICKET,
            "count": infer.count(),
            "decisions": infer.describe(),
        }

    def inference(self, decision_id: str) -> dict[str, Any] | None:
        return infer.describe_one(decision_id)

    def known_inference_ids(self) -> list[str]:
        return infer.known_ids()

    # -- projections --------------------------------------------------------- #

    def _trigger_summary(self, row: Mapping[str, Any]) -> dict[str, Any]:
        data = row.get("data") or {}
        return {
            "id": row["id"],
            "room_ref": row.get("room_id"),
            "endpoint_ref": data.get("endpoint_ref"),
            "automation_ref": data.get("automation_ref"),
            "state": data.get("state"),
            "reason": data.get("reason"),
            "duplicate_of": data.get("duplicate_of"),
            "shape": data.get("shape"),
            "fingerprint": data.get("fingerprint"),
            "identifiers": data.get("identifiers") or {},
            "attribute_count": len(data.get("attributes") or {}),
            "body": data.get("body"),
            "received_at": data.get("received_at"),
            "recorded_at": row.get("created_at"),
        }

    def _event_summary(self, row: Mapping[str, Any]) -> dict[str, Any]:
        data = row.get("data") or {}
        return {
            "id": row["id"],
            "room_ref": row.get("room_id"),
            "trigger_ref": data.get("trigger_ref"),
            "automation_ref": data.get("automation_ref"),
            "action": data.get("action"),
            "name": data.get("name"),
            "data": data.get("data") or {},
            "recorded_at": row.get("created_at"),
        }

    def _delivery_summary(self, row: Mapping[str, Any]) -> dict[str, Any]:
        data = row.get("data") or {}
        return {
            "id": row["id"],
            "room_ref": row.get("room_id"),
            "trigger_ref": data.get("trigger_ref"),
            "automation_ref": data.get("automation_ref"),
            "action": data.get("action"),
            "outcome": data.get("outcome"),
            "reason": data.get("reason"),
            "count": data.get("count"),
            "recorded_at": row.get("created_at"),
        }


def _tally(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        out[str(value)] = out.get(str(value), 0) + 1
    return out
