"""The façade the HTTP layer calls. The only module in this package that writes.

It holds nothing but a :class:`~dsr.store.RecordStore` handle, which is why the
feature module builds one per request from ``StoreDep`` rather than hanging it on
``app.state``: an ``app.state`` entry would mean editing ``dsr/api.py``, and the
whole point of the feature host is that adding a workflow is adding a file.

Every writing method takes a required ``source=``. That is not decoration. The
audit row is the product's guarantee, and an audit row that names a string rather
than a route cannot be traced back to the request that caused it. A required
keyword makes the omission a ``TypeError`` at the call site rather than a silent
untraceable row in production.

The signal this workflow notifies on is read from WF-027's own collection by its
published name (:data:`~dsr.signal_slack.vocabulary.SIGNAL_COLLECTION`). The
import is deliberately absent: see the package docstring for why.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.signal_slack import rules
from dsr.signal_slack.errors import (
    DuplicateAutomation,
    DuplicateConnection,
    InvalidDispatch,
    UnknownAutomation,
    UnknownConnection,
    UnknownDispatch,
    UnknownSignal,
)
from dsr.signal_slack.inferences import describe as describe_inferences
from dsr.signal_slack.vocabulary import (
    AUTOMATION_COLLECTION,
    CONNECTION_COLLECTION,
    DISPATCH_COLLECTION,
    DISPATCH_STATES,
    SIGNAL_COLLECTION,
    STATE_CONNECTED,
    STATE_REVOKED,
    describe,
)
from dsr.store import RecordStore


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def present(record: Mapping[str, Any]) -> dict[str, Any]:
    """A record flattened to ``data`` plus the id and the timestamps.

    Every read goes through this so there is one shape for a record in this
    package. Handing the raw envelope to a branch would put every field lookup one
    level too deep, which is the kind of mistake that returns a silent ``None``.
    """
    data = dict(record.get("data") or {})
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "revision": record.get("revision"),
        **data,
    }


class SlackNotifier:
    """Connect a workspace, configure a Slack block, and record the decision."""

    def __init__(self, store: RecordStore, *, now: Any = None) -> None:
        self.store = store
        # Injectable so a test can assert on the rate-limit window without freezing
        # the process clock.
        self._now = now or _utcnow

    # -- vocabulary --------------------------------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        """Every published name, number, tag and refusal, served as data."""
        return describe()

    def inferences(self) -> dict[str, Any]:
        """Every judgement call, beside the sourced statements it sits next to."""
        return describe_inferences()

    # -- connections -------------------------------------------------------- #

    def connections(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.list(CONNECTION_COLLECTION, room_id=room_id, limit=1000)
        return [present(row) for row in rows]

    def connection(self, *, room_id: str | None = None) -> dict[str, Any] | None:
        """The room's connected workspace, or ``None``.

        A revoked connection is skipped: the room may connect a new workspace after
        revoking one, and the live one is what a dispatch resolves through.
        """
        for row in self.connections(room_id=room_id):
            if str(row.get("state")) == STATE_CONNECTED:
                return row
        return None

    def read_connection(self, connection_id: str) -> dict[str, Any]:
        record = self.store.get(connection_id)
        if record is None or record.get("collection") != CONNECTION_COLLECTION:
            raise UnknownConnection(f"no Slack connection exists under {connection_id!r}")
        return present(record)

    def connect(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Store a connection, refusing a credential with no bot scope or no channel.

        One connected workspace per room: a second would make "which bot posted
        this" unanswerable.
        """
        data = rules.normalise_connection(payload)
        existing = self.connection(room_id=room_id)
        if existing is not None:
            raise DuplicateConnection(
                f"this room already has a connected Slack workspace ({existing['workspace']}). "
                "Revoke it before connecting another."
            )
        record = self.store.create(
            CONNECTION_COLLECTION, data, room_id=room_id, actor=actor, source=source
        )
        return present(record)

    def revoke(self, connection_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        self.read_connection(connection_id)
        record = self.store.update(
            connection_id, {"state": STATE_REVOKED}, actor=actor, source=source
        )
        return present(record)

    # -- automations -------------------------------------------------------- #

    def automations(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.list(AUTOMATION_COLLECTION, room_id=room_id, limit=1000)
        return [present(row) for row in rows]

    def read_automation(self, automation_id: str) -> dict[str, Any]:
        record = self.store.get(automation_id)
        if record is None or record.get("collection") != AUTOMATION_COLLECTION:
            raise UnknownAutomation(f"no Slack automation exists under {automation_id!r}")
        return present(record)

    def _clash(self, name: str, *, room_id: str | None, ignoring: str = "") -> bool:
        for row in self.automations(room_id=room_id):
            if row["id"] != ignoring and str(row.get("name")) == name:
                return True
        return False

    def create_automation(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        data = rules.normalise_automation(payload)
        if self._clash(data["name"], room_id=room_id):
            raise DuplicateAutomation(
                f"a Slack block named {data['name']!r} already exists in this room"
            )
        record = self.store.create(
            AUTOMATION_COLLECTION, data, room_id=room_id, actor=actor, source=source
        )
        return present(record)

    def update_automation(
        self,
        automation_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Patch a block, re-validating the fields a patch touches.

        The merged record is normalised as a whole rather than field by field,
        because a patch that changed only the message still has to satisfy the
        message rules, and one that changed only the state still has to satisfy the
        name rule.
        """
        current = self.read_automation(automation_id)
        merged = {
            "name": current.get("name"),
            "block_kind": current.get("block_kind"),
            "trigger": current.get("trigger"),
            "message": current.get("message"),
            "channel": current.get("channel"),
            "state": current.get("state"),
            "note": current.get("note"),
        }
        merged.update({key: value for key, value in patch.items() if key in merged})
        data = rules.normalise_automation(merged)
        if self._clash(data["name"], room_id=current.get("room_id"), ignoring=automation_id):
            raise DuplicateAutomation(
                f"a Slack block named {data['name']!r} already exists in this room"
            )
        record = self.store.update(automation_id, data, actor=actor, source=source)
        return present(record)

    def delete_automation(
        self, automation_id: str, *, actor: str | None, source: str
    ) -> dict[str, Any]:
        self.read_automation(automation_id)
        self.store.delete(automation_id, actor=actor, source=source)
        return {"id": automation_id, "deleted": True}

    # -- signals ------------------------------------------------------------ #

    def read_signal(self, signal_id: str) -> dict[str, Any]:
        """A buyer signal, read from WF-027's collection by its published name."""
        record = self.store.get(signal_id)
        if record is None or record.get("collection") != SIGNAL_COLLECTION:
            raise UnknownSignal(
                f"no buyer signal exists under {signal_id!r}. A live notification names a signal "
                f"the intent-signal workflow emitted into {SIGNAL_COLLECTION!r}."
            )
        return present(record)

    # -- dispatches --------------------------------------------------------- #

    def dispatches(
        self,
        *,
        room_id: str | None = None,
        automation_id: str = "",
        kind: str = "",
        state: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = self.store.list(DISPATCH_COLLECTION, room_id=room_id, limit=1000)
        kept = []
        for row in rows:
            item = present(row)
            if automation_id and item.get("automation_id") != automation_id:
                continue
            if kind and item.get("kind") != kind:
                continue
            if state and item.get("state") != state:
                continue
            kept.append(item)
        return kept[: max(1, min(int(limit), 1000))]

    def read_dispatch(self, dispatch_id: str) -> dict[str, Any]:
        record = self.store.get(dispatch_id)
        if record is None or record.get("collection") != DISPATCH_COLLECTION:
            raise UnknownDispatch(f"no notification decision exists under {dispatch_id!r}")
        return present(record)

    def _priors(
        self, *, room_id: str | None, automation_id: str, channel: str, kind: str
    ) -> list[str]:
        """The moments a dispatch of this kind to this destination was recorded.

        The window is per automation and per channel: a burst of signals for two
        different automations is two streams of work, not one rate-limited one.
        """
        moments = []
        for row in self.dispatches(room_id=room_id, kind=kind):
            if row.get("automation_id") != automation_id:
                continue
            if row.get("channel") != channel:
                continue
            stamp = row.get("decided_at") or row.get("created_at")
            if stamp:
                moments.append(str(stamp))
        return moments

    def decide(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Compute the decision without writing it."""
        if not isinstance(payload, Mapping):
            raise InvalidDispatch("a notification request must be a JSON object")
        kind = rules.normalise_dispatch_kind(payload.get("kind"))
        automation_id = str(payload.get("automation_id") or "")
        automation = self.read_automation(automation_id) if automation_id else None
        signal = None
        signal_id = str(payload.get("signal_id") or "")
        if kind == "live":
            if not signal_id:
                raise InvalidDispatch(
                    "a live notification is fired by a buyer signal. Name the signal_id it "
                    "notifies on."
                )
            signal = self.read_signal(signal_id)
        elif signal_id:
            signal = self.read_signal(signal_id)
        connection = self.connection(room_id=room_id)
        moment = now or self._now()
        priors = self._priors(
            room_id=room_id,
            automation_id=automation_id,
            channel=str(payload.get("channel") or (automation or {}).get("channel") or ""),
            kind=kind,
        )
        decision = rules.decide(
            connection=connection,
            automation=automation,
            signal=signal,
            customer=payload.get("customer") or {},
            channel=payload.get("channel"),
            kind=kind,
            now=moment,
            priors=priors,
        )
        decision["decided_at"] = moment.isoformat()
        decision["automation_id"] = automation_id
        decision["connection_id"] = (connection or {}).get("id", "")
        return decision

    def record_dispatch(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Record the notification decision, whether it would post or was refused.

        A refusal is a record with ``state='refused'`` and a code, not a 4xx: the
        page has to show a seller why a notification did not become deliverable, and
        the rate-limit refusal has to carry the retry window.
        """
        decision = self.decide(payload, room_id=room_id, now=now)
        if decision["state"] not in DISPATCH_STATES:
            raise InvalidDispatch(f"unknown dispatch state {decision['state']!r}")
        record = self.store.create(
            DISPATCH_COLLECTION, decision, room_id=room_id, actor=actor, source=source
        )
        return present(record)

    # -- summary ------------------------------------------------------------ #

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """The room at a glance, from this feature's own collections only."""
        connection = self.connection(room_id=room_id)
        automations = self.automations(room_id=room_id)
        dispatches = self.dispatches(room_id=room_id)
        states: dict[str, int] = {}
        refusals: dict[str, int] = {}
        for row in dispatches:
            states[str(row.get("state"))] = states.get(str(row.get("state")), 0) + 1
            if row.get("refusal"):
                refusals[str(row["refusal"])] = refusals.get(str(row["refusal"]), 0) + 1
        return {
            "room_id": room_id,
            "connected": connection is not None,
            "workspace": (connection or {}).get("workspace", ""),
            "channel": (connection or {}).get("channel", ""),
            "automations": len(automations),
            "live_automations": sum(1 for row in automations if row.get("state") == "live"),
            "dispatches": len(dispatches),
            "dispatch_states": states,
            "refusals": refusals,
            "sends_slack": False,
        }


__all__ = ["SlackNotifier", "present"]
