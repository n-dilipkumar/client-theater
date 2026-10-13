"""The WF-110 flow end to end, over the audited store.

Collections
-----------

Eight, all ordinary JSON in ``records.data``. No migration binds a team's field,
and a team adding a field needs no coordination with anyone.

``wf110_ticket``
    The object the workflow exists for: a ticket type, a state, an owning team,
    optional linked conversation, contacts and tags. Its ``created_via`` records
    how it came to exist, because the tracker exclusion depends on that.
``wf110_tag``
    The workspace tag vocabulary. The "tag the linked conversation" action
    selects from here and can never create an entry.
``wf110_sla_policy``
    A named policy with a metric and a target in hours. WF-108 owns SLA timers, so
    this build stores the policy and the due instant and depends on nothing in
    WF-108.
``wf110_trigger``
    One workflow: a kind, conditions, actions, and a draft-or-live state.
``wf110_evaluation``
    One trigger's decision for one ticket event. A skip is recorded, not raised.
``wf110_notification``
    The Slack message to the owning team. It is recorded as queued, not
    delivered: WF-112 owns Slack delivery and may not be present.
``wf110_webhook``
    Every ``ticket.*`` event this workflow emits, in the shape its topic uses.
``wf110_ticket_activity``
    The ticket's event log: created, linked, assigned, replied, tagged, changed.

The source argument
-------------------

Every writing method takes ``source`` as a required keyword. The route owns the
string, built from ``router.prefix``, so the audit row and the route table cannot
drift. Making it required means omitting it is a ``TypeError`` at the call site
rather than an untraceable row in production.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from dsr.conversation_tickets import rules, vocabulary as vocab
from dsr.conversation_tickets.errors import (
    ConversationNotFound,
    TagNotInWorkspace,
    TicketConflict,
    TicketNotFound,
    TicketRefused,
)
from dsr.store import RecordStore

TicketRecord = dict[str, Any]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TicketEngine:
    """The workflow over one :class:`~dsr.store.RecordStore`.

    The store and the clock are the only constructor arguments. Building the
    engine per request rather than holding it on ``app.state`` keeps the clock a
    plain argument, which lets a test drive the whole workflow with rows of its
    own and a clock it controls, and leaves ``app.state`` a thing no feature has
    to touch.
    """

    def __init__(self, store: RecordStore, clock: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self.clock = clock or _utcnow

    def _now(self) -> datetime:
        return self.clock()

    # ------------------------------------------------------------------ #
    # The workspace tag vocabulary
    # ------------------------------------------------------------------ #

    def create_tag(
        self, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> TicketRecord:
        """Register a workspace tag. A tag is shared, not per-ticket."""

        data = rules.validate_tag(payload)
        for existing in self.tags(limit=1000):
            if str(existing["data"].get("name", "")).lower() == data["name"].lower():
                raise TicketConflict(
                    f"a workspace tag named {data['name']!r} already exists",
                    code="tag_name_already_used",
                )
        return self.store.create(vocab.TAG_COLLECTION, data, actor=actor, source=source)

    def tags(self, *, limit: int = 1000) -> list[TicketRecord]:
        return self.store.list(
            vocab.TAG_COLLECTION, limit=limit, order_by="created_at", descending=False
        )

    def require_tag(self, name: str) -> TicketRecord:
        for record in self.tags(limit=1000):
            if str(record["data"].get("name", "")).lower() == str(name).lower():
                return record
        raise TagNotInWorkspace(f"tag {name!r} is not in the workspace")

    # ------------------------------------------------------------------ #
    # SLA policies
    # ------------------------------------------------------------------ #

    def create_sla_policy(
        self, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> TicketRecord:
        data = rules.validate_sla_policy(payload)
        for existing in self.sla_policies(limit=1000):
            if str(existing["data"].get("name", "")).lower() == data["name"].lower():
                raise TicketConflict(
                    f"an SLA policy named {data['name']!r} already exists",
                    code="sla_policy_already_used",
                )
        return self.store.create(vocab.SLA_COLLECTION, data, actor=actor, source=source)

    def sla_policies(self, *, limit: int = 1000) -> list[TicketRecord]:
        return self.store.list(
            vocab.SLA_COLLECTION, limit=limit, order_by="created_at", descending=False
        )

    def require_sla_policy(self, name: str) -> TicketRecord:
        for record in self.sla_policies(limit=1000):
            if str(record["data"].get("name", "")).lower() == str(name).lower():
                return record
        raise TicketNotFound(f"SLA policy {name!r} does not exist", kind="sla_policy")

    # ------------------------------------------------------------------ #
    # Triggers
    # ------------------------------------------------------------------ #

    def create_trigger(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
        room_id: str | None = None,
    ) -> TicketRecord:
        """Build a trigger. It is a draft until it is set live (step 5)."""

        data = rules.validate_trigger(payload)
        data["room_ref"] = str(room_id or payload.get("room_id") or "").strip()
        return self.store.create(
            vocab.TRIGGER_COLLECTION, data, room_id=room_id, actor=actor, source=source
        )

    def triggers(
        self, *, kind: str | None = None, room_id: str | None = None, limit: int = 200
    ) -> list[TicketRecord]:
        records = self.store.list(
            vocab.TRIGGER_COLLECTION,
            room_id=room_id,
            limit=limit,
            order_by="created_at",
            descending=False,
        )
        if kind:
            records = [r for r in records if str(r["data"].get("kind")) == kind]
        return records

    def require_trigger(self, trigger_id: str) -> TicketRecord:
        record = self.store.get(trigger_id)
        if record is None or record["collection"] != vocab.TRIGGER_COLLECTION:
            raise TicketNotFound(f"trigger {trigger_id!r} does not exist", kind="trigger")
        return record

    def update_trigger(
        self,
        trigger_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> TicketRecord:
        """Edit a trigger. Every field is revalidated, so an illegal edit cannot land."""

        record = self.require_trigger(trigger_id)
        merged = {**record["data"], **dict(payload or {})}
        data = rules.validate_trigger(merged)
        data["state"] = str(record["data"].get("state") or "draft")
        data["room_ref"] = str(record["data"].get("room_ref") or "")
        return self.store.update(trigger_id, data, actor=actor, source=source)

    def go_live(self, trigger_id: str, *, actor: str | None, source: str) -> TicketRecord:
        """Step 5: "Save and set live". A draft fires nothing."""

        record = self.require_trigger(trigger_id)
        if str(record["data"].get("state")) == "live":
            raise TicketConflict("the trigger is already live", code="trigger_already_live")
        return self.store.update(trigger_id, {"state": "live"}, actor=actor, source=source)

    def delete_trigger(self, trigger_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        record = self.require_trigger(trigger_id)
        self.store.delete(str(record["id"]), actor=actor, source=source)
        return {"id": record["id"], "deleted": True}

    # ------------------------------------------------------------------ #
    # Tickets
    # ------------------------------------------------------------------ #

    def create_ticket(
        self, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Create a ticket, then fire the ticket-created trigger and emit webhooks.

        The order matches the researched data flow: the ticket object is created,
        the ``ticket is created`` trigger fires, the actions run, and the state
        transitions emit webhooks. A tracker-created ticket is still created; only
        the trigger is excluded.
        """

        data = rules.validate_ticket(payload)
        room_id = str(payload.get("room_id") or "").strip() or None
        conversation_id = data.get("conversation_id")
        if conversation_id:
            self._require_conversation(str(conversation_id))

        record = self.store.create(
            vocab.TICKET_COLLECTION, data, room_id=room_id, actor=actor, source=source
        )
        self._activity(
            record, vocab.ACTIVITY_TICKET_CREATED, data["subject"], source=source, actor=actor
        )

        webhooks = [self._emit(vocab.TOPIC_TICKET_CREATED, record, source=source, actor=actor)]
        if record["data"].get("contacts"):
            webhooks.append(
                self._emit(
                    vocab.TOPIC_CONTACT_ATTACHED,
                    record,
                    source=source,
                    actor=actor,
                    extra={"contacts": list(record["data"]["contacts"])},
                )
            )

        fired = self._fire(vocab.TRIGGER_TICKET_CREATED, record, source=source, actor=actor)
        record = fired["ticket"]
        # The assignment and SLA actions changed the row, so the creation webhook
        # above described the row as it was created. The team-assigned webhook is
        # emitted by the action itself and carries the new team.
        return {
            "ticket": self.ticket_view(record),
            "evaluations": fired["evaluations"],
            "notifications": fired["notifications"],
            "webhooks": webhooks + fired["webhooks"],
        }

    def tickets(
        self,
        *,
        room_id: str | None = None,
        state: str | None = None,
        team: str | None = None,
        ticket_type: str | None = None,
        limit: int = 200,
    ) -> list[TicketRecord]:
        records = self.store.list(
            vocab.TICKET_COLLECTION,
            room_id=room_id,
            limit=limit,
            order_by="created_at",
            descending=False,
        )
        if state:
            records = [r for r in records if str(r["data"].get("state")) == state]
        if team:
            records = [r for r in records if str(r["data"].get("team")) == team]
        if ticket_type:
            records = [r for r in records if str(r["data"].get("ticket_type")) == ticket_type]
        return records

    def require_ticket(self, ticket_id: str) -> TicketRecord:
        record = self.store.get(ticket_id)
        if record is None or record["collection"] != vocab.TICKET_COLLECTION:
            raise TicketNotFound(f"ticket {ticket_id!r} does not exist", kind="ticket")
        return record

    def ticket_view(self, record: TicketRecord) -> dict[str, Any]:
        data = record["data"]
        return {
            "id": record["id"],
            "room_id": record.get("room_id"),
            "revision": record.get("revision"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            **data,
            "converted": rules.converted(data),
            "owning_team": rules.owning_team(data),
            "ticket_type_label": vocab.TICKET_TYPE_LABELS.get(str(data.get("ticket_type"))),
            "state_label": vocab.TICKET_STATE_LABELS.get(str(data.get("state"))),
            "created_via_label": vocab.CREATED_VIA_LABELS.get(str(data.get("created_via"))),
        }

    def search(self, payload: Mapping[str, Any] | None) -> list[TicketRecord]:
        """Find tickets by the fields the search endpoint names."""

        body = dict(payload or {})
        records = self.tickets(
            room_id=str(body.get("room_id") or "").strip() or None,
            state=str(body.get("state") or "").strip() or None,
            team=str(body.get("team") or "").strip() or None,
            ticket_type=str(body.get("ticket_type") or "").strip() or None,
        )
        query = str(body.get("query") or "").strip().lower()
        if query:
            records = [
                record
                for record in records
                if query in str(record["data"].get("subject", "")).lower()
                or query in str(record["id"]).lower()
            ]
        return records

    # ------------------------------------------------------------------ #
    # Ticket edits
    # ------------------------------------------------------------------ #

    def add_tag(
        self, ticket_id: str, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> TicketRecord:
        record = self.require_ticket(ticket_id)
        canonical = rules.resolve_tag(payload.get("tag"), self.tags())
        tags = rules.normalise_tags(list(record["data"].get("tags") or []) + [canonical])
        updated = self.store.update(ticket_id, {"tags": tags}, actor=actor, source=source)
        self._activity(updated, vocab.ACTIVITY_TAG_ADDED, canonical, source=source, actor=actor)
        return updated

    def remove_tag(
        self, ticket_id: str, tag_name: str, *, actor: str | None, source: str
    ) -> TicketRecord:
        record = self.require_ticket(ticket_id)
        tags = list(record["data"].get("tags") or [])
        remaining = [tag for tag in tags if tag.lower() != str(tag_name).lower()]
        if len(remaining) == len(tags):
            raise TicketConflict(
                f"tag {tag_name!r} is not on this ticket", code="tag_not_on_ticket"
            )
        updated = self.store.update(ticket_id, {"tags": remaining}, actor=actor, source=source)
        self._activity(updated, vocab.ACTIVITY_TAG_REMOVED, tag_name, source=source, actor=actor)
        return updated

    def reply(
        self, ticket_id: str, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """An admin reply or internal note. "POST /tickets/{id}/reply"."""

        record = self.require_ticket(ticket_id)
        body = dict(payload or {})
        text = str(body.get("body") or "").strip()
        if not text:
            raise TicketRefused("a reply needs a body", {"body": "required"})
        admin = str(body.get("admin") or "").strip() or record["data"].get("admin")
        patch: dict[str, Any] = {}
        webhooks: list[dict[str, Any]] = []
        if admin and admin != record["data"].get("admin"):
            patch["admin"] = admin
        updated = (
            self.store.update(ticket_id, patch, actor=actor, source=source) if patch else record
        )
        self._activity(
            updated,
            vocab.ACTIVITY_REPLIED,
            ("internal note" if body.get("internal_note") else "reply") + f": {text[:120]}",
            source=source,
            actor=actor,
        )
        if patch.get("admin"):
            webhooks.append(
                self._emit(
                    vocab.TOPIC_ADMIN_ASSIGNED,
                    updated,
                    source=source,
                    actor=actor,
                    extra={"admin": admin},
                )
            )
        return {"ticket": self.ticket_view(updated), "webhooks": webhooks}

    def change_type(
        self, ticket_id: str, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> TicketRecord:
        """Change a ticket's type. "POST /tickets/change_type"."""

        self.require_ticket(ticket_id)
        ticket_type = rules.require_ticket_type(payload.get("ticket_type"))
        updated = self.store.update(
            ticket_id, {"ticket_type": ticket_type}, actor=actor, source=source
        )
        self._activity(
            updated,
            vocab.ACTIVITY_STATE_CHANGED,
            f"type -> {ticket_type}",
            source=source,
            actor=actor,
        )
        return updated

    def change_state(
        self, ticket_id: str, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """A teammate changes the ticket's state.

        The state transition emits ``ticket.state.updated`` and, into a state that
        has its own event, that event. It then fires the state-changed trigger, so
        the owning team is notified a second time on a state change. ``closed`` is
        not produced here: it is not a state transition.
        """

        record = self.require_ticket(ticket_id)
        new_state = rules.require_ticket_state(payload.get("state"))
        previous = str(record["data"].get("state"))
        updated = self.store.update(ticket_id, {"state": new_state}, actor=actor, source=source)
        self._activity(
            updated,
            vocab.ACTIVITY_STATE_CHANGED,
            f"{previous} -> {new_state}",
            source=source,
            actor=actor,
        )
        webhooks = [
            self._emit(
                topic, updated, source=source, actor=actor, extra={"previous_state": previous}
            )
            for topic in rules.state_topics(previous, new_state)
        ]
        fired = self._fire(
            vocab.TRIGGER_STATE_CHANGED,
            updated,
            source=source,
            actor=actor,
            to_state=new_state,
        )
        return {
            "ticket": self.ticket_view(fired["ticket"]),
            "previous_state": previous,
            "state": new_state,
            "evaluations": fired["evaluations"],
            "notifications": fired["notifications"],
            "webhooks": webhooks + fired["webhooks"],
        }

    # ------------------------------------------------------------------ #
    # The linked conversation
    # ------------------------------------------------------------------ #

    def _require_conversation(self, conversation_id: str) -> TicketRecord:
        record = self.store.get(conversation_id)
        if record is None or record["collection"] != vocab.CONVERSATION_COLLECTION:
            raise ConversationNotFound(f"conversation {conversation_id!r} does not exist")
        return record

    def link_conversation(
        self, ticket_id: str, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Link a conversation, which is what makes the ticket converted.

        The converted-conversation gate reads this one field, so linking a
        conversation is what turns the gated actions on.
        """

        record = self.require_ticket(ticket_id)
        conversation_id = str(payload.get("conversation_id") or "").strip()
        if not conversation_id:
            raise TicketRefused(
                "link_conversation needs a conversation_id", {"conversation_id": "required"}
            )
        if str(record["data"].get("conversation_id") or "") == conversation_id:
            raise TicketConflict(
                "the conversation is already linked to this ticket",
                code="conversation_already_linked",
            )
        conversation = self._require_conversation(conversation_id)
        updated = self.store.update(
            ticket_id,
            {"conversation_id": conversation_id, "created_via": vocab.VIA_CONVERSATION},
            actor=actor,
            source=source,
        )
        self._activity(
            updated, vocab.ACTIVITY_CONVERSATION_LINKED, conversation_id, source=source, actor=actor
        )
        return {
            "ticket": self.ticket_view(updated),
            "conversation": self._conversation_view(conversation),
        }

    def unlink_conversation(
        self, ticket_id: str, *, actor: str | None, source: str
    ) -> dict[str, Any]:
        record = self.require_ticket(ticket_id)
        if not rules.converted(record["data"]):
            raise TicketConflict(
                "the ticket has no linked conversation", code="no_conversation_linked"
            )
        previous = str(record["data"].get("conversation_id"))
        updated = self.store.update(
            ticket_id,
            {"conversation_id": None, "created_via": vocab.VIA_API, "conversation_closed": False},
            actor=actor,
            source=source,
        )
        self._activity(
            updated, vocab.ACTIVITY_CONVERSATION_UNLINKED, previous, source=source, actor=actor
        )
        return {"ticket": self.ticket_view(updated)}

    def close_conversation(
        self, ticket_id: str, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Close the ticket's conversation and emit ``ticket.closed``.

        "ticket.closed ... Fires when a ticket conversation is closed. This is
        separate from state transitions." So this route closes the conversation
        and emits the nested payload. It does not change the ticket's state.
        """

        record = self.require_ticket(ticket_id)
        if not rules.converted(record["data"]):
            raise TicketRefused(
                "only a ticket converted from a conversation has a conversation to close",
                {"conversation_id": "required"},
                code="conversation_not_found",
            )
        conversation_id = str(record["data"]["conversation_id"])
        self._require_conversation(conversation_id)
        closed_conversation = self.store.update(
            conversation_id, {"state": vocab.CONVERSATION_CLOSED}, actor=actor, source=source
        )
        updated = self.store.update(
            ticket_id, {"conversation_closed": True}, actor=actor, source=source
        )
        self._activity(
            updated, vocab.ACTIVITY_CONVERSATION_CLOSED, conversation_id, source=source, actor=actor
        )
        webhook = self._emit(vocab.TOPIC_CLOSED, updated, source=source, actor=actor)
        return {
            "ticket": self.ticket_view(updated),
            "conversation": self._conversation_view(closed_conversation),
            "webhooks": [webhook],
        }

    def _conversation_view(self, record: TicketRecord) -> dict[str, Any]:
        return {
            "id": record["id"],
            "state": record["data"].get("state"),
            "tags": list(record["data"].get("tags") or []),
        }

    # ------------------------------------------------------------------ #
    # Firing the triggers
    # ------------------------------------------------------------------ #

    def _fire(
        self,
        event_kind: str,
        ticket: TicketRecord,
        *,
        source: str,
        actor: str | None,
        to_state: str | None = None,
    ) -> dict[str, Any]:
        """Fire every trigger of ``event_kind`` for one ticket event.

        A trigger that does not match records why. A matched trigger runs its
        actions in the order it declares them, and each action that the
        converted-conversation gate blocks records its own skip outcome. Nothing
        here raises for a skip: a skip is the point of the workflow.
        """

        evaluations: list[dict[str, Any]] = []
        notifications: list[dict[str, Any]] = []
        webhooks: list[dict[str, Any]] = []
        workspace_tags = self.tags()
        policies = self.sla_policies()
        room = str(ticket.get("room_id") or "")
        for trigger in self.triggers(kind=event_kind):
            # A trigger with no room is workspace-wide. A trigger with a room
            # fires only for a ticket in that room, so one room's escalation
            # configuration cannot act on another room's ticket.
            trigger_room = str(trigger["data"].get("room_ref") or "")
            if trigger_room and trigger_room != room:
                continue
            match = rules.trigger_matches(
                trigger["data"], ticket["data"], event_kind=event_kind, to_state=to_state
            )
            if not match["matched"]:
                evaluations.append(
                    self._record_evaluation(
                        trigger,
                        ticket,
                        event_kind,
                        matched=False,
                        reason=str(match["reason"]),
                        actions=[],
                        source=source,
                        actor=actor,
                    )
                )
                continue
            results: list[dict[str, Any]] = []
            for action in trigger["data"].get("actions") or []:
                skip = rules.action_skip(
                    action, ticket["data"], known_tags=workspace_tags, known_policies=policies
                )
                if skip:
                    results.append(
                        {
                            "kind": action.get("kind"),
                            "outcome": skip,
                            "detail": vocab.SKIP_REASON_TEXT.get(skip) or "",
                        }
                    )
                    continue
                done = self._run_action(trigger, action, ticket, source=source, actor=actor)
                if done.get("ticket") is not None:
                    ticket = done["ticket"]
                results.append(done["result"])
                if done.get("notification") is not None:
                    notifications.append(done["notification"])
                if done.get("webhook") is not None:
                    webhooks.append(done["webhook"])
            evaluations.append(
                self._record_evaluation(
                    trigger,
                    ticket,
                    event_kind,
                    matched=True,
                    reason=None,
                    actions=results,
                    source=source,
                    actor=actor,
                )
            )
        return {
            "ticket": ticket,
            "evaluations": evaluations,
            "notifications": notifications,
            "webhooks": webhooks,
        }

    def _run_action(
        self,
        trigger: TicketRecord,
        action: Mapping[str, Any],
        ticket: TicketRecord,
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        kind = str(action.get("kind"))
        now = self._now()
        if kind == vocab.ACTION_ASSIGN_TEAM:
            team = str(action.get("team") or "").strip()
            updated = self.store.update(
                str(ticket["id"]), {"team": team}, actor=actor, source=source
            )
            self._activity(updated, vocab.ACTIVITY_TEAM_ASSIGNED, team, source=source, actor=actor)
            webhook = self._emit(
                vocab.TOPIC_TEAM_ASSIGNED, updated, source=source, actor=actor, extra={"team": team}
            )
            return {
                "ticket": updated,
                "result": {"kind": kind, "outcome": vocab.ACTION_RAN, "team": team},
                "webhook": webhook,
            }
        if kind == vocab.ACTION_APPLY_SLA:
            policy = self.require_sla_policy(str(action.get("policy")))
            sla = {
                "policy": policy["data"]["name"],
                "metric": policy["data"]["metric"],
                "applied_at": rules.to_iso(now),
                "due_at": rules.sla_due(now, policy["data"]),
                "applied_by_trigger": trigger["id"],
            }
            updated = self.store.update(str(ticket["id"]), {"sla": sla}, actor=actor, source=source)
            self._activity(
                updated,
                vocab.ACTIVITY_STATE_CHANGED,
                f"SLA {sla['policy']}",
                source=source,
                actor=actor,
            )
            return {
                "ticket": updated,
                "result": {"kind": kind, "outcome": vocab.ACTION_RAN, "sla": sla},
            }
        if kind == vocab.ACTION_TAG_CONVERSATION:
            conversation_id = str(ticket["data"].get("conversation_id") or "")
            conversation = self.store.get(conversation_id)
            if conversation is None or conversation["collection"] != vocab.CONVERSATION_COLLECTION:
                return {
                    "ticket": None,
                    "result": {
                        "kind": kind,
                        "outcome": vocab.ACTION_SKIPPED_NOT_CONVERTED,
                        "detail": "the linked conversation no longer exists",
                    },
                }
            canonical = rules.resolve_tag(action.get("tag"), self.tags())
            merged = rules.normalise_tags(
                list(conversation["data"].get("tags") or []) + [canonical]
            )
            self.store.update(conversation_id, {"tags": merged}, actor=actor, source=source)
            self._activity(
                ticket,
                vocab.ACTIVITY_TAG_ADDED,
                f"conversation tagged {canonical}",
                source=source,
                actor=actor,
            )
            return {
                "ticket": None,
                "result": {"kind": kind, "outcome": vocab.ACTION_RAN, "tag": canonical},
            }
        if kind == vocab.ACTION_NOTIFY_TEAM:
            team = rules.owning_team(ticket["data"]) or ""
            channel = str(trigger["data"].get("channel") or f"#{team}")
            notification = self._notify(
                ticket,
                team=team,
                channel=channel,
                source=source,
                actor=actor,
                trigger_id=str(trigger["id"]),
                reasons=self._trigger_goal(trigger),
            )
            return {
                "ticket": None,
                "result": {"kind": kind, "outcome": vocab.ACTION_RAN, "channel": channel},
                "notification": notification,
            }
        # Unreachable while ``action_skip`` and ``validate_trigger`` agree on the
        # action set, and a refusal rather than a silent success if they ever drift.
        raise TicketRefused(f"action {kind!r} is not implemented", {"action": "unknown"})

    def _trigger_goal(self, trigger: TicketRecord) -> list[str]:
        goal = str(trigger["data"].get("goal") or "").strip()
        return [goal] if goal else []

    # ------------------------------------------------------------------ #
    # Writes for the record
    # ------------------------------------------------------------------ #

    def _activity(
        self,
        ticket: TicketRecord,
        kind: str,
        detail: str,
        *,
        source: str,
        actor: str | None,
    ) -> TicketRecord:
        return self.store.create(
            vocab.ACTIVITY_COLLECTION,
            {
                "ticket_id": ticket["id"],
                "kind": kind,
                "detail": detail,
                "at": rules.to_iso(self._now()),
                "room_ref": str(ticket.get("room_id") or ""),
            },
            room_id=ticket.get("room_id"),
            actor=actor,
            source=source,
        )

    def _record_evaluation(
        self,
        trigger: TicketRecord,
        ticket: TicketRecord,
        event_kind: str,
        *,
        matched: bool,
        reason: str | None,
        actions: Sequence[Mapping[str, Any]],
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        return self.store.create(
            vocab.EVALUATION_COLLECTION,
            {
                "trigger_id": trigger["id"],
                "trigger_name": trigger["data"].get("name"),
                "ticket_id": ticket["id"],
                "event_kind": event_kind,
                "matched": matched,
                "reason": reason,
                "reason_text": vocab.SKIP_REASON_TEXT.get(reason or ""),
                "is_specification_rule": (reason in vocab.SPECIFICATION_SKIPS),
                "actions": [dict(action) for action in actions],
                "at": rules.to_iso(self._now()),
                "room_ref": str(ticket.get("room_id") or ""),
            },
            room_id=ticket.get("room_id"),
            actor=actor,
            source=source,
        )

    def _notify(
        self,
        ticket: TicketRecord,
        *,
        team: str,
        channel: str,
        trigger_id: str,
        reasons: Sequence[str],
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        return self.store.create(
            vocab.NOTIFICATION_COLLECTION,
            {
                "ticket_id": ticket["id"],
                "team": team,
                "channel": channel,
                "subject": ticket["data"].get("subject"),
                "trigger_id": trigger_id,
                "reasons": list(reasons),
                "state": vocab.NOTIFY_QUEUED,
                "delivered_by_this_product": vocab.DELIVERED_BY_THIS_PRODUCT,
                "at": rules.to_iso(self._now()),
                "room_ref": str(ticket.get("room_id") or ""),
            },
            room_id=ticket.get("room_id"),
            actor=actor,
            source=source,
        )

    def _emit(
        self,
        topic: str,
        ticket: TicketRecord,
        *,
        source: str,
        actor: str | None,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = rules.webhook_payload(topic, ticket["data"] | {"id": ticket["id"]}, extra=extra)
        record = self.store.create(
            vocab.WEBHOOK_COLLECTION,
            {
                "topic": topic,
                "ticket_id": ticket["id"],
                "shape": rules.payload_shape(topic),
                "api_version": vocab.API_VERSION,
                "payload": payload,
                "delivered_by_this_product": False,
                "at": rules.to_iso(self._now()),
                "room_ref": str(ticket.get("room_id") or ""),
            },
            room_id=ticket.get("room_id"),
            actor=actor,
            source=source,
        )
        self._activity(ticket, vocab.ACTIVITY_WEBHOOK, topic, source=source, actor=actor)
        return record

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    def _notifications(
        self, *, ticket_id: str | None = None, limit: int = 200
    ) -> list[TicketRecord]:
        records = self.store.list(
            vocab.NOTIFICATION_COLLECTION, limit=limit, order_by="created_at", descending=False
        )
        if ticket_id:
            records = [r for r in records if str(r["data"].get("ticket_id")) == ticket_id]
        return records

    def notifications(self, *, ticket_id: str | None = None, limit: int = 200) -> dict[str, Any]:
        records = self._notifications(ticket_id=ticket_id, limit=limit)
        by_state: dict[str, int] = {state: 0 for state in vocab.NOTIFICATION_STATES}
        for record in records:
            state = str(record["data"].get("state"))
            by_state[state] = by_state.get(state, 0) + 1
        return {
            "count": len(records),
            "by_state": by_state,
            "delivered_by_this_product": vocab.DELIVERED_BY_THIS_PRODUCT,
            "notifications": records,
        }

    def webhooks(
        self,
        *,
        ticket_id: str | None = None,
        topic: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        records = self.store.list(
            vocab.WEBHOOK_COLLECTION, limit=limit, order_by="created_at", descending=False
        )
        if ticket_id:
            records = [r for r in records if str(r["data"].get("ticket_id")) == ticket_id]
        if topic:
            records = [r for r in records if str(r["data"].get("topic")) == topic]
        shaped = [
            {
                **record,
                "parsed_ticket": rules.read_webhook_ticket(record["data"].get("payload") or {}),
            }
            for record in records
        ]
        by_topic: dict[str, int] = {}
        for record in records:
            key = str(record["data"].get("topic"))
            by_topic[key] = by_topic.get(key, 0) + 1
        return {
            "count": len(shaped),
            "by_topic": by_topic,
            "by_shape": dict(vocab.WEBHOOK_PAYLOAD_SHAPES),
            "webhooks": shaped,
        }

    def evaluations(
        self,
        *,
        ticket_id: str | None = None,
        trigger_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        records = self.store.list(
            vocab.EVALUATION_COLLECTION, limit=limit, order_by="created_at", descending=False
        )
        if ticket_id:
            records = [r for r in records if str(r["data"].get("ticket_id")) == ticket_id]
        if trigger_id:
            records = [r for r in records if str(r["data"].get("trigger_id")) == trigger_id]
        skip_reasons: dict[str, int] = {}
        for record in records:
            reason = record["data"].get("reason")
            if reason:
                skip_reasons[str(reason)] = skip_reasons.get(str(reason), 0) + 1
        return {
            "count": len(records),
            "matched": sum(1 for r in records if r["data"].get("matched")),
            "skipped": sum(1 for r in records if not r["data"].get("matched")),
            "by_skip_reason": skip_reasons,
            "evaluations": records,
        }

    def activity(self, ticket_id: str) -> dict[str, Any]:
        self.require_ticket(ticket_id)
        records = [
            record
            for record in self.store.list(
                vocab.ACTIVITY_COLLECTION, limit=500, order_by="created_at", descending=False
            )
            if str(record["data"].get("ticket_id")) == ticket_id
        ]
        return {"ticket_id": ticket_id, "count": len(records), "activity": records}

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Counts over exactly the rows the same filters would return."""

        tickets = self.tickets(room_id=room_id, limit=1000)
        notifications = self._notifications(limit=1000)
        if room_id:
            notified = {
                str(record["data"].get("ticket_id"))
                for record in notifications
                if str(record["data"].get("room_ref") or "") == room_id
            }
        else:
            notified = {str(record["data"].get("ticket_id")) for record in notifications}
        by_state: dict[str, int] = {state: 0 for state in vocab.TICKET_STATES}
        by_team: dict[str, int] = {team: 0 for team in vocab.TEAMS}
        for record in tickets:
            state = str(record["data"].get("state"))
            by_state[state] = by_state.get(state, 0) + 1
            team = str(record["data"].get("team") or "")
            if team:
                by_team[team] = by_team.get(team, 0) + 1
        triggers = self.triggers(room_id=room_id, limit=1000)
        return {
            "tickets": len(tickets),
            "by_state": by_state,
            "by_team": by_team,
            "converted": sum(1 for r in tickets if rules.converted(r["data"])),
            "tracker_created": sum(
                1 for r in tickets if rules.is_tracker_created(r["data"].get("created_via"))
            ),
            "triggers": len(triggers),
            "live_triggers": sum(1 for t in triggers if str(t["data"].get("state")) == "live"),
            "notifications": len(notifications),
            "notified_tickets": len(notified),
            "tickets_without_a_notification": sum(
                1 for record in tickets if str(record["id"]) not in notified
            ),
            "webhooks": self.store.count_where(vocab.WEBHOOK_COLLECTION, {}),
            "tickets_per_state_label": {
                vocab.TICKET_STATE_LABELS.get(state, state): count
                for state, count in by_state.items()
            },
        }

    def catalog(self, *, room_id: str | None = None, limit: int = 200) -> dict[str, Any]:
        return {
            "room_id": room_id,
            "tickets": self.tickets(room_id=room_id, limit=limit),
            "triggers": self.triggers(room_id=room_id, limit=limit),
            "tags": self.tags(limit=limit),
            "sla_policies": self.sla_policies(limit=limit),
            "notifications": self._notifications(limit=limit),
        }
