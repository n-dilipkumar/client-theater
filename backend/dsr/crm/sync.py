"""The CRM sync engine: one object, one job.

:class:`CRMSync` is the whole public surface of this package. It owns three
things and hides how they fit together:

* **Subscriptions** — who wants which event pushed where.
* **Automations** — the no-code path, When / Do this, with a field map.
* **Activity** — one row per attempt, success or errored, which is the research's
  Activity Log and the only thing a rep actually reads.

``record_event`` is the entry point. Everything else exists to configure it.

The envelope stays generic
--------------------------
Nothing here adds a column, a migration, or a typed field. The CRM field map in
an automation action is arbitrary JSON keyed by whatever the CRM calls the
field, and a team's fields are registered as ``crm_field`` records rather than
declared in code. A team that adds a field to their CRM ships a record, not a
pull request.

``source`` on every write
-------------------------
Every method that writes takes a required keyword-only ``source``, and the
feature's routes pass the route that served the request. The branch let five
hardcoded strings - ``"crm.event.<name>"``, ``"crm.activity"``,
``"crm.field.register"`` - reach the audit log, so an audit row could not be
traced back to the request that caused it. This class of bug has shipped in this
codebase before, so the parameter is required rather than defaulted: a caller
that forgets is a type error, not a wrong audit row.

A fan-out write is caused by the event, not by its own route, so the rows the
fan-out produces - the Activity Log entry, the counters it advances - carry the
event's ``source`` with a note naming which channel did the write. The route is
still the first thing in the string, so ``"POST /api/wf-016/events -> webhook
delivery"`` reads as what it is: a row written while serving that request.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping

from dsr.crm import automations as automation_rules
from dsr.crm import subscriptions as subscription_rules
from dsr.crm.errors import CrmError
from dsr.crm.delivery import (
    DEFAULT_BACKOFF,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_TIMEOUT,
    Transport,
    UrllibTransport,
    deliver,
)
from dsr.crm.vocabulary import (
    describe,
    is_field_type,
    merge_presets,
    require_event,
    require_page_status,
    room_facts,
    status_for,
)
from dsr.store import RecordStore

EVENTS_COLLECTION = "crm_event"
ACTIVITY_COLLECTION = "crm_activity"
FIELDS_COLLECTION = "crm_field"

#: Separates the route from the note on a fan-out write's ``source``. Parsed by
#: nothing in production; the test that checks every audit row names a served
#: route splits on it.
FANOUT_NOTE = " -> "


def fanout_source(source: str, channel: str) -> str:
    """The ``source`` for a write the fan-out made while serving ``source``."""
    return f"{source}{FANOUT_NOTE}{channel}"


class FieldRegistryError(CrmError):
    """Raised when a CRM field declaration cannot be stored as written."""


class CRMSync:
    """Push room events out to a CRM over webhooks and automations."""

    def __init__(
        self,
        store: RecordStore,
        *,
        transport: Transport | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff: float = DEFAULT_BACKOFF,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.store = store
        self.subscriptions = subscription_rules.SubscriptionBook(store)
        self.automations = automation_rules.AutomationBook(store)
        self.transport = transport or UrllibTransport()
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.backoff = backoff
        self._sleep = sleep

    # -- vocabulary --------------------------------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        """The published event, status, and field-type vocabularies.

        Served as data so a client renders the pickers from the same source the
        validator uses, and a new event reaches every client at once.
        """
        payload = describe()
        payload["crms"] = list(automation_rules.KNOWN_CRMS)
        payload["environments"] = list(automation_rules.SUPPORTED_ENVIRONMENTS)
        payload["action_kinds"] = list(automation_rules.RESOLVABLE_ACTIONS)
        return payload

    def presets(self) -> list[dict[str, Any]]:
        """The Recommended Automations a rep can start from."""
        return merge_presets()

    # -- CRM field registry ------------------------------------------------- #

    def register_field(
        self, spec: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Declare a CRM field so mappings onto it can be type-checked.

        Optional: a mapping onto an undeclared field still works, it just
        cannot be linted. ``visible_to_integration`` records whether the
        connected CRM user can actually see the field, which is the documented
        cause of custom fields silently not appearing.
        """
        name = str(spec.get("name") or "").strip()
        if not name:
            raise FieldRegistryError("name is required")
        field_type = str(spec.get("type") or "text").strip().lower()
        if not is_field_type(field_type):
            raise FieldRegistryError(
                f"unknown field type {field_type!r}; expected one of "
                f"{', '.join(describe()['field_types'])}"
            )
        return self.store.create(
            FIELDS_COLLECTION,
            {
                "name": name,
                "type": field_type,
                "object": str(spec.get("object") or "Opportunity"),
                "label": str(spec.get("label") or ""),
                "visible_to_integration": bool(spec.get("visible_to_integration", True)),
            },
            actor=actor,
            source=source,
        )

    def fields(self) -> list[dict[str, Any]]:
        return self.store.list(FIELDS_COLLECTION, limit=500)

    def _field_index(self) -> tuple[dict[str, str], frozenset[str]]:
        """``({field: type}, {fields the integration user cannot see})``."""
        types: dict[str, str] = {}
        hidden: set[str] = set()
        for record in self.fields():
            data = record["data"]
            name = data.get("name")
            if not name:
                continue
            types[name] = str(data.get("type") or "text")
            if not data.get("visible_to_integration", True):
                hidden.add(name)
        return types, frozenset(hidden)

    # -- subscriptions ------------------------------------------------------ #

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
        return self.subscriptions.subscribe(
            event,
            target_url,
            room_id=room_id,
            secret=secret,
            description=description,
            actor=actor,
            source=source,
        )

    def list_subscriptions(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        return [subscription_rules.summarise(r) for r in self.subscriptions.list(room_id=room_id)]

    def unsubscribe(
        self, subscription_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        return self.subscriptions.unsubscribe(subscription_id, actor=actor, source=source)

    # -- automations -------------------------------------------------------- #

    def create_automation(
        self, spec: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Create an automation and return it in the same shape as a read."""
        record = self.automations.create(spec, actor=actor, source=source)
        return self.get_automation(record["id"]) or {}

    def list_automations(self) -> list[dict[str, Any]]:
        field_types, hidden = self._field_index()
        return [automation_rules.summarise(r, field_types, hidden) for r in self.automations.list()]

    def get_automation(self, automation_id: str) -> dict[str, Any] | None:
        record = self.automations.get(automation_id)
        if record is None:
            return None
        field_types, hidden = self._field_index()
        return automation_rules.summarise(record, field_types, hidden)

    def update_automation(
        self,
        automation_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        self.automations.update(automation_id, patch, actor=actor, source=source)
        return self.get_automation(automation_id) or {}

    def delete_automation(
        self, automation_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        return self.automations.delete(automation_id, actor=actor, source=source)

    # -- events ------------------------------------------------------------- #

    def record_event(
        self,
        event: str,
        *,
        room_id: str | None = None,
        status: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record a room event and fan it out to every subscriber and rule.

        The event is stored first, so there is a durable record of what happened
        even when every delivery downstream fails. The returned ``activity``
        list is the per-target outcome, in the order the targets were tried.
        """
        name = require_event(event)
        resolved_status = require_page_status(status) if status else status_for(name)

        room = self.store.get(room_id) if room_id else None
        facts = room_facts(room["data"] if room else {}, room_id=room_id)

        # The research is explicit that the page's `metadata` rides along with
        # the event, so it round-trips untouched. An explicit override wins,
        # but never drops what the room already carried.
        payload_metadata = dict(facts.get("metadata") or {})
        payload_metadata.update(dict(metadata or {}))

        record = self.store.create(
            EVENTS_COLLECTION,
            {
                "event": name,
                "status": resolved_status,
                "metadata": payload_metadata,
                "facts": facts,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )

        envelope = {
            "id": record["id"],
            "event": name,
            "status": resolved_status,
            "occurred_at": record["created_at"],
            "room_id": room_id,
            "room": facts,
            "metadata": payload_metadata,
        }

        activity: list[dict[str, Any]] = []
        activity.extend(self._fan_out_webhooks(record, envelope, source))
        activity.extend(self._fan_out_automations(record, envelope, facts, source))

        return {
            "event": record,
            "activity": activity,
            "counts": self._counts(activity),
        }

    def list_events(
        self, *, room_id: str | None = None, event: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        where = {"event": event} if event else {}
        if where:
            records = self.store.find(EVENTS_COLLECTION, where, limit=limit)
            if room_id is not None:
                records = [r for r in records if r["room_id"] == room_id]
        elif room_id is not None:
            records = self.store.list(EVENTS_COLLECTION, room_id=room_id, limit=limit)
        else:
            records = self.store.list(EVENTS_COLLECTION, limit=limit)
        return records

    # -- activity ----------------------------------------------------------- #

    def activity(
        self,
        *,
        room_id: str | None = None,
        channel: str | None = None,
        status: str | None = None,
        event: str | None = None,
        subscription_id: str | None = None,
        automation_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """The Activity Log, newest first.

        Filterable on the envelope's ``room_id`` and on arbitrary ``data`` paths
        through the dynamic index, so a new channel or status does not need a
        query change here.
        """
        where: dict[str, Any] = {}
        for key, value in (
            ("channel", channel),
            ("status", status),
            ("event", event),
            ("subscription_id", subscription_id),
            ("automation_id", automation_id),
        ):
            if value:
                where[key] = value

        if where:
            records = self.store.find(ACTIVITY_COLLECTION, where, limit=limit)
            if room_id is not None:
                records = [r for r in records if r["room_id"] == room_id]
        else:
            records = self.store.list(ACTIVITY_COLLECTION, room_id=room_id, limit=limit)
        return records

    # -- internals ---------------------------------------------------------- #

    def _fan_out_webhooks(
        self, event: Mapping[str, Any], envelope: Mapping[str, Any], source: str
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for subscription in self.subscriptions.matching(
            event["data"]["event"], room_id=event["room_id"]
        ):
            data = subscription["data"]
            report = deliver(
                self.transport,
                data["target_url"],
                envelope,
                event=event["data"]["event"],
                delivery_id=f"{event['id']}:{subscription['id']}",
                secret=data.get("secret"),
                timeout=self.timeout,
                max_attempts=self.max_attempts,
                backoff=self.backoff,
                sleep=self._sleep,
            )
            outcome = report.to_dict()
            results.append(
                self._log(
                    {
                        "channel": "webhook",
                        "event": event["data"]["event"],
                        "event_id": event["id"],
                        "subscription_id": subscription["id"],
                        "target_url": data["target_url"],
                        "status": "success" if report.ok else "error",
                        "request": envelope,
                        **outcome,
                    },
                    room_id=event["room_id"],
                    actor="webhook-delivery",
                    source=fanout_source(source, "webhook delivery"),
                )
            )
            self.subscriptions.record_outcome(
                subscription["id"],
                ok=report.ok,
                source=fanout_source(source, "webhook delivery"),
            )
        return results

    def _fan_out_automations(
        self,
        event: Mapping[str, Any],
        envelope: Mapping[str, Any],
        facts: Mapping[str, Any],
        source: str,
    ) -> list[dict[str, Any]]:
        field_types, hidden = self._field_index()
        results: list[dict[str, Any]] = []
        template_id = facts.get("template_id")

        for automation in self.automations.matching(
            event["data"]["event"], template_id=template_id
        ):
            data = automation["data"]
            outcome = automation_rules.run(data, facts, field_types, hidden)
            errors = [
                warning
                for warning in outcome["warnings"]
                if warning["severity"] == "warning" and warning["code"] == "unresolved_fact"
            ]
            results.append(
                self._log(
                    {
                        "channel": "automation",
                        "event": event["data"]["event"],
                        "event_id": event["id"],
                        "automation_id": automation["id"],
                        "automation_name": data.get("name"),
                        # A run that resolved nothing it was asked to resolve is
                        # an error, not a success with empty fields: that is the
                        # case the Activity Log has to send a rep back to the
                        # room to fix by hand.
                        "status": "error" if errors else "success",
                        "request": envelope,
                        "resolved": outcome["resolved"],
                        "skipped": outcome["skipped"],
                        "warnings": outcome["warnings"],
                        "error": (
                            "; ".join(w["message"] for w in errors) if errors else None
                        ),
                        "needs_manual_update": bool(errors),
                    },
                    room_id=event["room_id"],
                    actor="automation",
                    source=fanout_source(source, "automation run"),
                )
            )
            self.automations.record_outcome(
                automation["id"],
                ok=not errors,
                source=fanout_source(source, "automation run"),
            )
        return results

    def _log(
        self,
        data: Mapping[str, Any],
        *,
        room_id: str | None,
        actor: str,
        source: str,
    ) -> dict[str, Any]:
        return self.store.create(
            ACTIVITY_COLLECTION, data, room_id=room_id, actor=actor, source=source
        )

    @staticmethod
    def _counts(activity: list[Mapping[str, Any]]) -> dict[str, int]:
        counts = {"total": len(activity), "success": 0, "error": 0}
        for entry in activity:
            counts[str(entry["data"].get("status"))] = counts.get(str(entry["data"].get("status")), 0) + 1
        return counts
