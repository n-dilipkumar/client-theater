"""WF-107: the reads and the writes behind chasing and reroute.

This module turns the pure rules beside it into records. It holds a store handle and a
clock and nothing else, which is what lets a test build one directly and move the clock
by hand. Nothing here imports ``dsr.api`` and nothing here opens SQLite: every read and
every write goes through the :class:`~dsr.store.RecordStore` the HTTP layer hands in, so
the audit row is written in the same transaction as the change.

What the engine owns
--------------------

**A conversation and its parts.** The research's data flow starts from "Last-message
timestamp on the Conversation object" and writes "message part[s]". One row per part is
what makes the anchor rules checkable: :func:`~dsr.conversation_chase.rules.anchor_instant`
reads a list of parts, and "the first customer message" is only a fact if the first part
is identifiable as one.

**A trigger and its step list.** Step 7 is "Save both and set live", so a trigger is a
draft until :meth:`ConversationChaseEngine.go_live` and a draft fires nothing.

**A run per firing.** A run holds the step cursor and the wait state. That is the whole
of "the Wait/Snooze timer runs and can be interrupted": an interrupted run is a run in
the ``interrupted`` state with the part that cancelled it recorded, not a row that
disappeared.

**Activity rows.** "Close action transitions the conversation to a closed state -> Tag
written to the conversation part -> assignment moves the conversation between teams" is
three events a reviewer reads in order, so they are rows.

What the engine does not claim
------------------------------

**That a message left this product.** The research's extensibility names
``POST /messages`` replay, Data Connectors and ``X-Hub-Signature`` webhooks, all of which
need an Intercom credential this product does not hold. Every message is written as a
part and every response carries ``sent_by_this_product: False``. A confirmation that
reads like a fact about a real inbox is the exact failure the repository already records
in ``booking_approval.py``, so it is stated rather than implied.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from dsr.conversation_chase import rules, vocabulary as vocab
from dsr.store import RecordStore

#: Reported on every message and every reroute. See the module docstring.
SENT_BY_THIS_PRODUCT = vocab.SENT_BY_THIS_PRODUCT

#: The researched maximum number of runs a sweep writes per conversation. One, because
#: each live trigger may fire once per customer message and a conversation with three
#: customer messages has three tokens, so a sweep that wanted three per conversation
#: would have to be told how many triggers exist. One run per trigger per sweep is the
#: honest unit: the sweep decides who is due, and ``advance`` does the rest.
DEFAULT_RUN_LIMIT = 50


class ConversationChaseEngine:
    """Conversations, triggers, runs and the events between them.

    Every writing method takes ``source`` as a required keyword. A hardcoded URL inside
    a domain method is a defect the feature contract names by name: the audit row has to
    name the route that served the write, and the only code that knows that is the route.
    """

    def __init__(self, store: RecordStore, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._now = now or rules.utcnow

    # -- clocks and lookups --------------------------------------------------- #

    def _moment(self) -> datetime:
        return self._now()

    def _conversation_record(self, conversation_id: str) -> dict[str, Any]:
        record = self.store.get(conversation_id)
        if record is None or record.get("collection") != vocab.CONVERSATIONS:
            raise rules.ConversationNotFound(conversation_id)
        return record

    def _trigger_record(self, trigger_id: str) -> dict[str, Any]:
        record = self.store.get(trigger_id)
        if record is None or record.get("collection") != vocab.TRIGGERS:
            raise rules.TriggerNotFound(trigger_id)
        return record

    def _run_record(self, run_id: str) -> dict[str, Any]:
        record = self.store.get(run_id)
        if record is None or record.get("collection") != vocab.RUNS:
            raise rules.RunNotFound(run_id)
        return record

    def parts_of(self, conversation_id: str) -> list[dict[str, Any]]:
        """Every part of a conversation, oldest first **by the instant it carries**.

        Sorted by the payload's ``at`` rather than by the envelope's ``created_at``,
        because a message can be written with an instant in its past: a row imported from
        another system, or a test that places three messages at known instants. Ordering by
        ``created_at`` would then report them in the order they arrived rather than the
        order they were sent, and both anchor rules read "first" and "last" from this list.
        A part with no instant sorts first, so it is never mistaken for a later one.
        """

        rows = self.store.find(
            vocab.PARTS,
            {vocab.ROOM_REF: self._room_of(conversation_id), "conversation_id": conversation_id},
            limit=1000,
        )
        parts = [dict(row.get("data") or {}) for row in rows]
        # One ordering rule, shared with the anchor functions that read this list.
        return rules.parts_of(parts)

    def _room_of(self, conversation_id: str) -> Any:
        record = self.store.get(conversation_id)
        if record is None:
            return None
        return record.get("room_id") or (record.get("data") or {}).get(vocab.ROOM_REF)

    # -- projections ---------------------------------------------------------- #

    def conversation_view(self, conversation_id: str) -> dict[str, Any]:
        """One conversation, its parts and its live trigger status."""

        record = self._conversation_record(conversation_id)
        data = dict(record.get("data") or {})
        parts = self.parts_of(conversation_id)
        return {
            "id": record.get("id"),
            "revision": record.get("revision"),
            "room_id": record.get("room_id"),
            "updated_at": record.get("updated_at"),
            "state": rules.require_state(data.get("state")),
            "origin": vocab.require_origin(data.get("origin")),
            "inbox": data.get("inbox"),
            "previous_inbox": data.get("previous_inbox"),
            "tags": list(data.get("tags") or []),
            "priority": bool(data.get("priority")),
            "customer_first_message_at": _first_at(parts, vocab.AUTHOR_CUSTOMER),
            "customer_last_message_at": _last_at(parts, vocab.AUTHOR_CUSTOMER),
            "last_activity_at": _last_at(parts),
            "parts": [
                {
                    "id": part.get("id"),
                    "author_kind": part.get("author_kind"),
                    "at": part.get("at"),
                    "body": part.get("body"),
                    "source": part.get("source"),
                }
                for part in parts
            ],
        }

    def conversations(self, room_id: str) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.CONVERSATIONS, {vocab.ROOM_REF: room_id}, limit=500)
        return [dict(row.get("data") or {}) | {"id": row.get("id")} for row in rows]

    # -- conversations -------------------------------------------------------- #

    def open_conversation(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Open a conversation.

        The origin is explicit on every call, because "This workflow won't trigger for
        conversations created via our REST API" is only a rule if the origin is recorded
        rather than inferred from which route was used.

        ``create_conversation_without_contact_reply`` is passed explicitly, defaulted from
        :data:`vocabulary.CREATE_WITHOUT_CONTACT_REPLY_DEFAULT`, which is ``False`` as the
        messages API reference states. The value is recorded on the conversation so the
        decision is auditable rather than implicit.
        """

        data = dict(payload or {})
        origin = rules.require_origin(data.get("origin"))
        create_flag = data.get(
            vocab.CREATE_WITHOUT_CONTACT_REPLY,
            vocab.CREATE_WITHOUT_CONTACT_REPLY_DEFAULT,
        )
        # Coerced to a real bool so a caller sending the string "false" does not get a
        # conversation that is truthy forever.
        create_flag = bool(create_flag) and str(create_flag).strip().lower() not in (
            "false",
            "0",
            "",
        )

        inbox = data.get("inbox")
        tags = [rules.require_tag(tag) for tag in (data.get("tags") or [])]

        record = self.store.create(
            vocab.CONVERSATIONS,
            {
                vocab.ROOM_REF: room_id,
                "state": rules.require_state(data.get("state")),
                "origin": origin,
                "inbox": inbox,
                "tags": tags,
                "priority": bool(data.get("priority")),
                "customer_email": data.get("customer_email"),
                "subject": data.get("subject"),
                vocab.CREATE_WITHOUT_CONTACT_REPLY: create_flag,
                "created_at": rules.stamp(self._moment()),
                "sent_by_this_product": SENT_BY_THIS_PRODUCT,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self.conversation_view(record["id"])

    def add_message(
        self,
        conversation_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Write a message part and, when it is from the buyer, re-arm the trigger.

        The re-arm is the whole of "can only trigger once per customer message": each
        customer message mints a new token, so the next sweep may fire once against it
        and once only. A teammate or system message mints nothing, because the limit is
        per *customer* message and nothing else.
        """

        record = self._conversation_record(conversation_id)
        data = dict(record.get("data") or {})
        state = rules.require_state(data.get("state"))
        if state == vocab.STATE_CLOSED:
            raise rules.ChaseRefusal(
                "conversation_closed",
                "conversation_id",
                "A closed conversation cannot receive a message part.",
            )

        body = dict(payload or {})
        author = rules.require_author_kind(body.get("author_kind"))
        at = rules.coerce_instant(body.get("at")) or self._moment()
        text = str(body.get("body") or "").strip()

        part = self.store.create(
            vocab.PARTS,
            {
                vocab.ROOM_REF: record.get("room_id"),
                "conversation_id": conversation_id,
                "author_kind": author,
                "at": rules.stamp(at),
                "body": text or None,
                "origin": body.get("origin")
                or ("workflow" if author == vocab.AUTHOR_SYSTEM else author),
                "sent_by_this_product": SENT_BY_THIS_PRODUCT
                if author == vocab.AUTHOR_SYSTEM
                else None,
            },
            room_id=record.get("room_id"),
            actor=actor,
            source=source,
        )

        # A customer message moves the conversation to open if it was snoozed: the buyer
        # is talking, so a paused timer has nothing left to pause.
        if author == vocab.AUTHOR_CUSTOMER and state == vocab.STATE_SNOOZED:
            self.store.update(
                conversation_id,
                {"state": vocab.STATE_OPEN, "unsnoozed_at": rules.stamp(at)},
                actor=actor,
                source=source,
            )

        self._activity(
            conversation_id,
            vocab.ACTIVITY_TYPES[1],
            "message_sent",
            {"author_kind": author, "at": rules.stamp(at)},
            source=source,
            actor=actor,
        )
        return dict(part.get("data") or {}) | {"id": part.get("id")}

    def snooze(
        self,
        conversation_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Snooze a conversation, which pauses both triggers.

        A snoozed conversation is skipped by the sweep with its own published code,
        rather than being treated as closed or as due. The Snooze action is one of the
        two steps that takes precedence over the global auto-close setting, and that is
        read off the trigger's step list rather than stored here.
        """

        record = self._conversation_record(conversation_id)
        state = rules.require_state((record.get("data") or {}).get("state"))
        if state == vocab.STATE_CLOSED:
            raise rules.ChaseRefusal(
                "conversation_closed", "conversation_id", "A closed conversation cannot be snoozed."
            )
        updated = self.store.update(
            conversation_id,
            {
                "state": vocab.STATE_SNOOZED,
                "snoozed_at": rules.stamp(self._moment()),
                "snooze_reason": (payload or {}).get("reason"),
            },
            actor=actor,
            source=source,
        )
        self._activity(
            conversation_id,
            vocab.ACTIVITY_SNOOZED,
            "snoozed",
            {"reason": (payload or {}).get("reason")},
            source=source,
            actor=actor,
        )
        return self.conversation_view(updated["id"])

    def close(
        self,
        conversation_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Close a conversation.

        A close is idempotent: a conversation already closed stays closed and no second
        activity row is written, because "Close conversation" on something already
        closed is a no-op rather than a conflict. The caller that needs to know is told
        by reading the state back, and this keeps a sweep that races two triggers from
        producing two closing events for one conversation.
        """

        record = self._conversation_record(conversation_id)
        state = rules.require_state((record.get("data") or {}).get("state"))
        if state == vocab.STATE_CLOSED:
            return self.conversation_view(conversation_id)

        updated = self.store.update(
            conversation_id,
            {
                "state": vocab.STATE_CLOSED,
                "closed_at": rules.stamp(self._moment()),
                "closed_reason": reason,
            },
            actor=actor,
            source=source,
        )
        self._activity(
            conversation_id,
            vocab.ACTIVITY_CONVERSATION_CLOSED,
            "conversation_closed",
            {"reason": reason},
            source=source,
            actor=actor,
        )
        return self.conversation_view(updated["id"])

    def tag(
        self,
        conversation_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Write a tag onto the conversation.

        Step 5 puts the Tag *after* the Close action and step 6's tag is
        'delayed response'. A tag on a closed conversation is legal, which is why the
        order the research gives is preserved rather than enforced as a precondition:
        "then a Close conversation action, then Tag conversation" would be unreachable
        if tagging a closed conversation were refused.
        """

        record = self._conversation_record(conversation_id)
        name = rules.require_tag((payload or {}).get("tag"))
        existing = list((record.get("data") or {}).get("tags") or [])
        if name in existing:
            return self.conversation_view(conversation_id)

        self.store.update(
            conversation_id,
            {"tags": [*existing, name]},
            actor=actor,
            source=source,
        )
        self._activity(
            conversation_id,
            vocab.ACTIVITY_TAGGED,
            "tagged",
            {"tag": name},
            source=source,
            actor=actor,
        )
        return self.conversation_view(conversation_id)

    def mark_priority(
        self,
        conversation_id: str,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Mark a conversation as priority. Step 6's "Mark as priority"."""

        record = self._conversation_record(conversation_id)
        self.store.update(
            conversation_id,
            {"priority": True, "marked_priority_at": rules.stamp(self._moment())},
            actor=actor,
            source=source,
        )
        self._activity(
            conversation_id,
            vocab.ACTIVITY_MARKED_PRIORITY,
            "marked_priority",
            {},
            source=source,
            actor=actor,
        )
        return self.conversation_view(record["id"])

    def reroute(
        self,
        conversation_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
        known_inboxes: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Assign a conversation to another inbox.

        Step 6: "**Assign conversation** to reroute the conversation to the desired
        Inbox". The previous inbox is recorded on the conversation as well as the event,
        because "assignment moves the conversation between teams" is only checkable if the
        place it came from is still visible afterwards.

        Rerouting to the inbox the conversation is already in is refused rather than
        silently accepted: a workflow whose reroute step names the conversation's own
        inbox has a misconfiguration, and reporting it as success would hide that.
        """

        record = self._conversation_record(conversation_id)
        data = dict(record.get("data") or {})
        state = rules.require_state(data.get("state"))
        if state == vocab.STATE_CLOSED:
            raise rules.ChaseRefusal(
                "conversation_closed",
                "conversation_id",
                "A closed conversation cannot be rerouted to another inbox.",
            )

        inbox = " ".join(str((payload or {}).get("inbox") or "").split())
        if not inbox:
            raise rules.ChaseRefusal("inbox_unknown", "inbox", "An assign step needs the inbox.")

        if known_inboxes is not None and inbox not in set(known_inboxes):
            raise rules.ChaseRefusal(
                "inbox_unknown",
                "inbox",
                f"No such inbox {inbox!r}; this account has "
                f"{', '.join(sorted(known_inboxes)) or 'none recorded'}.",
            )

        if data.get("inbox") == inbox:
            raise rules.ChaseRefusal(
                "reroute_to_same_inbox",
                "inbox",
                f"The conversation is already assigned to {inbox!r}.",
            )

        self.store.update(
            conversation_id,
            {
                "inbox": inbox,
                "previous_inbox": data.get("inbox"),
                "rerouted_at": rules.stamp(self._moment()),
            },
            actor=actor,
            source=source,
        )
        self._activity(
            conversation_id,
            vocab.ACTIVITY_REROUTED,
            "rerouted",
            {"from": data.get("inbox"), "to": inbox, "sent_by_this_product": SENT_BY_THIS_PRODUCT},
            source=source,
            actor=actor,
        )
        return self.conversation_view(conversation_id)

    # -- office hours --------------------------------------------------------- #

    def office_hours(self, room_id: str) -> dict[str, Any]:
        """The room's schedule, with the derived default filled in.

        A room with no stored schedule still answers, and ``stored`` is ``False`` so a
        reader can tell the derived default from one somebody set.
        """

        rows = self.store.find(vocab.OFFICE_HOURS, {vocab.ROOM_REF: room_id}, limit=1)
        record = rows[0] if rows else None
        stored = dict(record.get("data") or {}) if record else {}
        # A room with nothing stored gets the derived default rather than a schedule with
        # every day closed. Normalising ``None`` would produce exactly that, and an account
        # with no stored hours would read as permanently shut.
        raw_schedule = (
            stored.get("schedule") if stored.get("schedule") else rules.default_office_hours()
        )
        schedule = rules.normalise_office_hours(raw_schedule)
        return {
            "room_id": room_id,
            "id": record.get("id") if record else None,
            "stored": record is not None,
            "schedule": schedule,
            "derived": not record,
            "derivation": vocab.OFFICE_HOURS_QUOTE,
            "timezone": stored.get("timezone") or "UTC",
        }

    def save_office_hours(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Write the room's schedule.

        Validated before the write, so a schedule whose close is before its open is
        refused and no unusable row is stored.
        """

        body = dict(payload or {})
        schedule = rules.normalise_office_hours(
            body.get("schedule")
            if body.get("schedule") is not None
            else rules.default_office_hours()
        )
        existing = self.store.find(vocab.OFFICE_HOURS, {vocab.ROOM_REF: room_id}, limit=1)
        fields = {
            vocab.ROOM_REF: room_id,
            "schedule": schedule,
            "timezone": str(body.get("timezone") or "UTC"),
            "updated_at": rules.stamp(self._moment()),
        }
        if existing:
            record = self.store.update(existing[0]["id"], fields, actor=actor, source=source)
        else:
            record = self.store.create(
                vocab.OFFICE_HOURS, fields, room_id=room_id, actor=actor, source=source
            )
        return {
            "room_id": room_id,
            "id": record.get("id"),
            "stored": True,
            "derived": False,
            "schedule": schedule,
            "timezone": fields["timezone"],
            "derivation": vocab.OFFICE_HOURS_QUOTE,
        }

    def expected_reply_time(
        self, room_id: str, anchor: datetime, duration_seconds: int
    ) -> dict[str, Any]:
        """When a reply is expected on a conversation, per this room's office hours."""

        schedule = self.office_hours(room_id)["schedule"]
        due = rules.expected_reply_time(anchor, duration_seconds, schedule)
        return {
            "anchor": rules.stamp(anchor),
            "duration_seconds": duration_seconds,
            "expected_reply_time": rules.stamp(due),
            "office_minutes": rules.office_minutes_between(anchor, duration_seconds, schedule),
            "schedule": schedule,
            "derivation": vocab.OFFICE_HOURS_QUOTE,
        }

    # -- triggers ------------------------------------------------------------- #

    def create_trigger(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Create a trigger, a draft until it is set live.

        Step 7 is "Save both and set live", so ``live`` defaults to ``False`` and a draft
        fires nothing. A caller who sends ``live: True`` at creation is believed, because
        the research's step order describes the builder's flow rather than forbidding it.
        """

        body = dict(payload or {})
        steps = rules.normalise_steps(body.get("steps"))
        duration = rules.require_duration(
            body.get("duration_seconds")
            if body.get("duration_seconds") is not None
            else vocab.DEFAULT_TRIGGER_SECONDS
        )
        record = self.store.create(
            vocab.TRIGGERS,
            {
                vocab.ROOM_REF: room_id,
                "kind": rules.require_trigger_kind(body.get("kind")),
                "duration_seconds": duration,
                "channels": rules.require_channels(body.get("channels")),
                "audience": body.get("audience"),
                "scheduling": body.get("scheduling") or {},
                "goal": body.get("goal"),
                "steps": steps,
                "live": bool(body.get("live")),
                "created_at": rules.stamp(self._moment()),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self.trigger_view(record["id"])

    def trigger_view(self, trigger_id: str) -> dict[str, Any]:
        record = self._trigger_record(trigger_id)
        data = dict(record.get("data") or {})
        steps = list(data.get("steps") or [])
        return {
            "id": record.get("id"),
            "revision": record.get("revision"),
            "room_id": record.get("room_id"),
            "updated_at": record.get("updated_at"),
            "kind": data.get("kind"),
            "kind_label": vocab.TRIGGER_KIND_LABELS.get(
                str(data.get("kind")), str(data.get("kind"))
            ),
            "duration_seconds": data.get("duration_seconds"),
            "channels": list(data.get("channels") or []),
            "audience": data.get("audience"),
            "scheduling": data.get("scheduling") or {},
            "goal": data.get("goal"),
            "steps": steps,
            "live": bool(data.get("live")),
            "anchor": vocab.anchor_for(str(data.get("kind"))),
            "anchor_label": vocab.ANCHOR_LABELS.get(
                vocab.anchor_for(str(data.get("kind"))), vocab.anchor_for(str(data.get("kind")))
            ),
            "close_authority": rules.close_authority(steps),
        }

    def triggers(self, room_id: str) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.TRIGGERS, {vocab.ROOM_REF: room_id}, limit=200)
        return [
            self.trigger_view(row["id"])
            for row in sorted(rows, key=lambda row: str(row.get("created_at") or ""))
        ]

    def update_trigger(
        self,
        trigger_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Edit a trigger's duration, steps, fields or live switch.

        Every field is revalidated on the way in, so a duration that was legal at creation
        and illegal after an edit cannot be stored: :func:`rules.require_duration` is
        called on the value being written, not only on the value that created the row.
        """

        record = self._trigger_record(trigger_id)
        data = dict(record.get("data") or {})
        body = dict(payload or {})

        patch: dict[str, Any] = {}
        if "duration_seconds" in body:
            patch["duration_seconds"] = rules.require_duration(body.get("duration_seconds"))
        if "steps" in body:
            patch["steps"] = rules.normalise_steps(body.get("steps"))
        if "channels" in body:
            patch["channels"] = rules.require_channels(body.get("channels"))
        if "kind" in body:
            patch["kind"] = rules.require_trigger_kind(body.get("kind"))
        for field in ("audience", "scheduling", "goal", "live"):
            if field in body:
                patch[field] = body.get(field)

        # A step that inherits the trigger's own duration has to be re-checked against
        # the *new* trigger duration, or an edit could leave a step holding a duration
        # outside the researched bounds.
        if patch.get("steps") is not None and patch.get("duration_seconds") is None:
            patch["steps"] = _recheck_step_durations(patch["steps"], data.get("duration_seconds"))

        merged = {**data, **patch}
        for step in merged.get("steps") or []:
            if str(step.get("kind")) in vocab.DURATION_STEPS:
                rules.require_step_duration(step.get("duration_seconds"))

        self.store.update(trigger_id, patch, actor=actor, source=source)
        return self.trigger_view(trigger_id)

    def go_live(self, trigger_id: str, *, source: str, actor: str | None = None) -> dict[str, Any]:
        """Set a trigger live. Step 7: "Save both and set live".

        Going live on an already-live trigger is refused: the research describes one
        transition, and a second one that reported success would make a page's toggle
        indistinguishable from a no-op.
        """

        record = self._trigger_record(trigger_id)
        if bool((record.get("data") or {}).get("live")):
            raise rules.ChaseRefusal(
                "trigger_already_live", "trigger_id", "This trigger is already live."
            )
        self.store.update(
            trigger_id,
            {"live": True, "went_live_at": rules.stamp(self._moment())},
            actor=actor,
            source=source,
        )
        return self.trigger_view(trigger_id)

    def delete_trigger(
        self, trigger_id: str, *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        """Soft-delete a trigger.

        A soft delete rather than a hard one because the runs a trigger produced are
        evidence: "the workflow can only trigger once per customer message" is checked
        against the consumed tokens on those runs, and destroying them would let a
        deleted and recreated trigger fire twice against the same message.
        """

        self._trigger_record(trigger_id)
        # The store's delete result reports ``hard``, never ``deleted_at``, so the soft
        # flag is read from the field that exists rather than from one that does not.
        deleted = self.store.delete(trigger_id, actor=actor, source=source)
        return {
            "id": deleted.get("id"),
            "trigger_id": trigger_id,
            "deleted": True,
            "hard": bool(deleted.get("hard")),
            "runs_kept": True,
        }

    # -- the sweep ------------------------------------------------------------ #

    def evaluate(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """The inactivity sweep: who is due, who is skipped, and why.

        One pass over the room's live triggers and its conversations. Every outcome is
        reported rather than raised, because "nothing is due yet" is the common case and
        is not a fault in the caller's request.

        Two live triggers of the same kind both fire against one conversation, and each fires
        once, because the once-per-message limit is per trigger. ``trigger_id`` scopes
        the sweep to one of them.

        The order of the checks is load-bearing and is the same as
        :func:`rules.trigger_eligible` then the anchor then the arm token:

        1. eligibility -- the API-created exemption, then snoozed, then closed;
        2. the anchor for this trigger kind, and no message of that kind means a skip;
        3. the inactivity window, and not elapsed means a skip with the seconds left;
        4. the arm token, already spent means a skip.

        A live trigger with no matching conversation reports ``no_trigger_of_this_kind``
        only when the room has no trigger of the requested kind at all, so a caller
        filtering by kind learns that rather than learning nothing happened.
        """

        body = dict(payload or {})
        now = rules.coerce_instant(body.get("now")) or self._moment()
        kind_filter = body.get("kind")
        if kind_filter is not None:
            kind_filter = rules.require_trigger_kind(kind_filter)
        only = body.get("conversation_id")
        only_trigger = body.get("trigger_id")

        trigger_rows = self.store.find(vocab.TRIGGERS, {vocab.ROOM_REF: room_id}, limit=200)
        triggers = [self.trigger_view(row["id"]) for row in trigger_rows]
        if kind_filter is not None:
            triggers = [entry for entry in triggers if entry["kind"] == kind_filter]
        if only_trigger is not None:
            # Scoping to one trigger is how a caller evaluates a single workflow, and how
            # a caller that knows two live triggers share a kind separates them. Without
            # it, two triggers of the same kind both fire against the same conversation,
            # which is correct behaviour and not something a caller can act on.
            triggers = [entry for entry in triggers if entry["id"] == only_trigger]

        conversation_rows = self.store.find(
            vocab.CONVERSATIONS, {vocab.ROOM_REF: room_id}, limit=500
        )
        conversation_ids = [
            row["id"] for row in conversation_rows if only is None or row["id"] == only
        ]

        fired: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        if not triggers:
            skipped.append(
                {
                    "conversation_id": None,
                    "trigger_id": None,
                    "reason": vocab.SKIP_NO_TRIGGER,
                    "detail": vocab.SKIP_REASON_TEXT[vocab.SKIP_NO_TRIGGER],
                }
            )

        for trigger in triggers:
            for conversation_id in conversation_ids:
                record = self.store.get(conversation_id)
                if record is None:
                    continue
                data = dict(record.get("data") or {})

                eligibility = rules.trigger_eligible(data)
                if not eligibility["eligible"]:
                    skipped.append(
                        _skip(
                            conversation_id,
                            trigger["id"],
                            eligibility["reason"],
                            extra={"state": eligibility["state"]},
                        )
                    )
                    continue

                if not trigger["live"]:
                    skipped.append(
                        _skip(conversation_id, trigger["id"], vocab.SKIP_TRIGGER_NOT_LIVE)
                    )
                    continue

                parts = self.parts_of(conversation_id)
                anchor, anchor_kind = rules.anchor_instant(parts, trigger["kind"])
                if anchor is None:
                    skipped.append(
                        _skip(
                            conversation_id,
                            trigger["id"],
                            vocab.SKIP_NO_ANCHOR_MESSAGE,
                            extra={"anchor": anchor_kind},
                        )
                    )
                    continue

                window = rules.inactivity(anchor, now, int(trigger["duration_seconds"]))
                if not window["due"]:
                    skipped.append(
                        _skip(
                            conversation_id,
                            trigger["id"],
                            vocab.SKIP_NOT_ELIGIBLE,
                            extra={
                                "seconds_remaining": window["seconds_remaining"],
                                "anchor": window["anchor"],
                                "anchor_kind": anchor_kind,
                            },
                        )
                    )
                    continue

                token = rules.arm_token(parts)
                consumed = self._consumed_tokens(conversation_id, trigger["id"])
                if not rules.should_fire(token, consumed):
                    skipped.append(
                        _skip(
                            conversation_id,
                            trigger["id"],
                            vocab.SKIP_ALREADY_FIRED,
                            extra={"anchor": window["anchor"], "anchor_kind": anchor_kind},
                        )
                    )
                    continue

                run = self._start_run(
                    room_id,
                    conversation_id,
                    trigger,
                    token=token,
                    anchor=anchor,
                    anchor_kind=anchor_kind,
                    overdue=window["seconds_overdue"],
                    source=source,
                    actor=actor,
                )
                fired.append(run)

        return {
            "room_id": room_id,
            "evaluated_at": rules.stamp(now),
            "triggers_considered": len(triggers),
            "conversations_considered": len(conversation_ids),
            "fired": fired,
            "skipped": skipped,
            "fired_count": len(fired),
            "skipped_count": len(skipped),
            "due_next": self.due_count(room_id, now=now),
        }

    def due_count(self, room_id: str, *, now: datetime | None = None) -> int:
        """How many conversations are past their window on at least one live trigger.

        Reported on every sweep response and on the summary, so the gap left by the
        explicit-route decision is visible on the page rather than silent. Read-only, and
        therefore a GET the page may call freely.
        """

        moment = now or self._moment()
        trigger_rows = self.store.find(vocab.TRIGGERS, {vocab.ROOM_REF: room_id}, limit=200)
        triggers = [
            self.trigger_view(row["id"])
            for row in trigger_rows
            if row and self.trigger_view(row["id"])["live"]
        ]
        if not triggers:
            return 0

        due = 0
        for row in self.store.find(vocab.CONVERSATIONS, {vocab.ROOM_REF: room_id}, limit=500):
            data = dict(row.get("data") or {})
            if not rules.trigger_eligible(data)["eligible"]:
                continue
            parts = self.parts_of(row["id"])
            for trigger in triggers:
                anchor, _ = rules.anchor_instant(parts, trigger["kind"])
                if anchor is None:
                    continue
                if not rules.inactivity(anchor, moment, int(trigger["duration_seconds"]))["due"]:
                    continue
                # A conversation whose token this trigger has already spent is not due.
                # Counting it would make ``due_next`` non-zero after the sweep that just
                # fired it, and the field claims to be what the next call would act on.
                token = rules.arm_token(parts)
                if not rules.should_fire(token, self._consumed_tokens(row["id"], trigger["id"])):
                    continue
                due += 1
                break
        return due

    # -- runs ----------------------------------------------------------------- #

    def _start_run(
        self,
        room_id: str,
        conversation_id: str,
        trigger: Mapping[str, Any],
        *,
        token: str | None,
        anchor: datetime,
        anchor_kind: str,
        overdue: float,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Write the run that a firing creates.

        The consumed token is recorded on the run rather than in a separate ledger,
        because the once-per-message rule is a property of the firing and a separate
        ledger would need its own reconciliation story.
        """

        now = self._moment()
        record = self.store.create(
            vocab.RUNS,
            {
                vocab.ROOM_REF: room_id,
                "conversation_id": conversation_id,
                "trigger_id": trigger.get("id"),
                "kind": trigger.get("kind"),
                "state": vocab.RUN_RUNNING,
                "steps": list(trigger.get("steps") or []),
                "cursor": 0,
                "arm_token": token,
                "anchor": rules.stamp(anchor),
                "anchor_kind": anchor_kind,
                "seconds_overdue": overdue,
                "started_at": rules.stamp(now),
                "history": [],
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        self._activity(
            conversation_id,
            vocab.ACTIVITY_TRIGGER_FIRED,
            "trigger_fired",
            {
                "trigger_id": trigger.get("id"),
                "kind": trigger.get("kind"),
                "anchor_kind": anchor_kind,
                "anchor": rules.stamp(anchor),
                "seconds_overdue": overdue,
                "once_per_message": vocab.ONCE_PER_MESSAGE,
            },
            source=source,
            actor=actor,
        )
        return self.run_view(record["id"])

    def _consumed_tokens(self, conversation_id: str, trigger_id: str | None) -> list[str]:
        """The arm tokens already spent for one conversation.

        Read from the runs, so the once-per-message rule survives a restart and does not
        depend on a sweep having run in one uninterrupted process.
        """

        room = self._room_of(conversation_id)
        rows = self.store.find(
            vocab.RUNS,
            {vocab.ROOM_REF: room, "conversation_id": conversation_id},
            limit=1000,
        )
        tokens: list[str] = []
        for row in rows:
            data = dict(row.get("data") or {})
            if trigger_id is not None and data.get("trigger_id") != trigger_id:
                continue
            token = data.get("arm_token")
            if token:
                tokens.append(str(token))
        return tokens

    def run_view(self, run_id: str) -> dict[str, Any]:
        record = self._run_record(run_id)
        data = dict(record.get("data") or {})
        steps = list(data.get("steps") or [])
        cursor = int(data.get("cursor") or 0)
        next_step = steps[cursor] if cursor < len(steps) else None
        return {
            "id": record.get("id"),
            "revision": record.get("revision"),
            "room_id": record.get("room_id"),
            "conversation_id": data.get("conversation_id"),
            "trigger_id": data.get("trigger_id"),
            "kind": data.get("kind"),
            "state": data.get("state"),
            "state_label": vocab.RUN_STATE_LABELS.get(
                str(data.get("state")), str(data.get("state"))
            ),
            "cursor": cursor,
            "steps": steps,
            "next_step": next_step,
            "remaining_steps": steps[cursor:],
            "arm_token": data.get("arm_token"),
            "anchor": data.get("anchor"),
            "anchor_kind": data.get("anchor_kind"),
            "anchor_label": vocab.ANCHOR_LABELS.get(
                str(data.get("anchor_kind")), str(data.get("anchor_kind"))
            ),
            "started_at": data.get("started_at"),
            # Reported for a run that was interrupted, and ``None`` for every other state
            # rather than absent: a reader asking "who stopped this?" should get an answer
            # from every run, not a missing key on the ones that were not stopped.
            "interrupted_by": data.get("interrupted_by"),
            "interrupted_at": data.get("interrupted_at"),
            "wait_started_at": data.get("wait_started_at"),
            "wait_resolved_at": data.get("wait_resolved_at"),
            "finished_at": data.get("finished_at"),
            "seconds_overdue": data.get("seconds_overdue"),
            "history": list(data.get("history") or []),
            "sent_by_this_product": SENT_BY_THIS_PRODUCT,
        }

    def runs(self, room_id: str) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.RUNS, {vocab.ROOM_REF: room_id}, limit=500)
        return [
            self.run_view(row["id"])
            for row in sorted(rows, key=lambda row: str(row.get("created_at") or ""))
        ]

    def advance(
        self,
        run_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
        known_inboxes: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Write the next step, or start the wait.

        The step list is walked by index, which is why order is preserved when a trigger
        is normalised: the research describes "a closing message block, then a Close
        conversation action, then Tag conversation", and a reordering would change what
        the workflow does.

        A run that has reached the end of its steps finishes. A run whose next step is a
        Wait or a Snooze starts the timer and moves to ``waiting`` rather than stepping
        over it, because "the Wait/Snooze timer runs" is a state the run holds.
        """

        record = self._run_record(run_id)
        data = dict(record.get("data") or {})
        state = str(data.get("state"))
        if state in vocab.CLOSED_RUN_STATES:
            raise rules.ChaseRefusal(
                "run_not_advancing",
                "run_id",
                f"This run is {vocab.RUN_STATE_LABELS.get(state, state)} and has no next step.",
            )
        if state == vocab.RUN_WAITING:
            raise rules.ChaseRefusal(
                "run_not_advancing",
                "run_id",
                "This run is in a wait. Resolve it before advancing again.",
            )

        steps = list(data.get("steps") or [])
        cursor = int(data.get("cursor") or 0)
        if cursor >= len(steps):
            self._finish(run_id, source=source, actor=actor)
            return self.run_view(run_id)

        step = steps[cursor]
        kind = str(step.get("kind"))
        conversation_id = str(data.get("conversation_id"))
        now = self._moment()

        if kind in vocab.HOLDING_STEPS:
            held = dict(step)
            held["started_at"] = rules.stamp(now)
            self.store.update(
                run_id,
                {
                    "state": vocab.RUN_WAITING,
                    "current_step": held,
                    "wait_started_at": rules.stamp(now),
                    "history": [
                        *list(data.get("history") or []),
                        {"kind": kind, "at": rules.stamp(now)},
                    ],
                },
                actor=actor,
                source=source,
            )
            self._activity(
                conversation_id,
                vocab.ACTIVITY_WAIT_STARTED,
                "wait_started",
                {
                    "kind": kind,
                    "duration_seconds": held.get("duration_seconds"),
                    "at": rules.stamp(now),
                },
                source=source,
                actor=actor,
            )
            return self.run_view(run_id)

        self._apply_step(
            conversation_id,
            kind,
            step,
            source=source,
            actor=actor,
            known_inboxes=known_inboxes,
        )

        next_cursor = cursor + 1
        remaining = steps[next_cursor:]
        self.store.update(
            run_id,
            {
                "cursor": next_cursor,
                "state": vocab.RUN_FINISHED if not remaining else vocab.RUN_RUNNING,
                "current_step": None,
                "wait_started_at": None,
                "finished_at": rules.stamp(now) if not remaining else None,
                "history": [
                    *list(data.get("history") or []),
                    {"kind": kind, "at": rules.stamp(now), "step": step},
                ],
            },
            actor=actor,
            source=source,
        )
        return self.run_view(run_id)

    def _apply_step(
        self,
        conversation_id: str,
        kind: str,
        step: Mapping[str, Any],
        *,
        source: str,
        actor: str | None,
        known_inboxes: Sequence[str] | None,
    ) -> None:
        """One non-holding step's effect on the conversation."""

        body = step.get("body")

        if kind == vocab.STEP_MESSAGE:
            self.add_message(
                conversation_id,
                {"author_kind": vocab.AUTHOR_SYSTEM, "body": body},
                source=source,
                actor=actor,
            )
        elif kind == vocab.STEP_CLOSE_MESSAGE:
            self.add_message(
                conversation_id,
                {"author_kind": vocab.AUTHOR_SYSTEM, "body": body},
                source=source,
                actor=actor,
            )
        elif kind == vocab.STEP_CLOSE:
            self.close(conversation_id, source=source, actor=actor, reason="workflow_close")
        elif kind == vocab.STEP_TAG:
            self.tag(conversation_id, {"tag": step.get("tag")}, source=source, actor=actor)
        elif kind == vocab.STEP_MARK_PRIORITY:
            self.mark_priority(conversation_id, source=source, actor=actor)
        elif kind == vocab.STEP_ASSIGN:
            self.reroute(
                conversation_id,
                {"inbox": step.get("inbox")},
                source=source,
                actor=actor,
                known_inboxes=known_inboxes,
            )
        elif kind == vocab.STEP_SHOW_EXPECTED_REPLY_TIME:
            room = self._room_of(conversation_id)
            anchor = rules.coerce_instant(step.get("anchor")) or self._moment()
            expected = self.expected_reply_time(
                str(room),
                anchor,
                int(step.get("duration_seconds") or vocab.DEFAULT_TRIGGER_SECONDS),
            )
            self._activity(
                conversation_id,
                vocab.ACTIVITY_EXPECTED_REPLY_TIME,
                "expected_reply_time",
                expected,
                source=source,
                actor=actor,
            )

    def resolve(
        self,
        run_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None = None,
        known_inboxes: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Resolve a wait: finish it, or end the run because it was interrupted.

        The interruption check comes first, because it is the researched behaviour and it
        is cheaper to ask: "configure ... which interruption events cancel the wait" means
        a wait that has been cancelled is not a wait that has run out.
        """

        record = self._run_record(run_id)
        data = dict(record.get("data") or {})
        state = str(data.get("state"))
        if state != vocab.RUN_WAITING:
            raise rules.ChaseRefusal(
                "run_not_waiting",
                "run_id",
                f"This run is {vocab.RUN_STATE_LABELS.get(state, state)}, not in a wait to resolve.",
            )

        conversation_id = str(data.get("conversation_id"))
        parts = self.parts_of(conversation_id)
        held = dict(data.get("current_step") or {})
        now = rules.coerce_instant((payload or {}).get("now")) or self._moment()

        verdict = rules.wait_state(held, parts, now)
        if verdict["state"] == "waiting":
            raise rules.ChaseRefusal(
                "run_not_waiting",
                "run_id",
                f"The wait has {verdict['remaining_seconds']:.0f} second(s) left and has "
                "not been interrupted.",
            )

        if verdict["state"] == "interrupted":
            self.store.update(
                run_id,
                {
                    "state": vocab.RUN_INTERRUPTED,
                    "interrupted_by": verdict.get("interrupted_by"),
                    "interrupted_at": verdict.get("at"),
                    "finished_at": rules.stamp(now),
                    "current_step": None,
                    "history": [
                        *list(data.get("history") or []),
                        {
                            "kind": "interrupted",
                            "at": rules.stamp(now),
                            "interrupted_by": verdict.get("interrupted_by"),
                        },
                    ],
                },
                actor=actor,
                source=source,
            )
            self._activity(
                conversation_id,
                vocab.ACTIVITY_WAIT_INTERRUPTED,
                "wait_interrupted",
                {
                    "interrupted_by": verdict.get("interrupted_by"),
                    "at": verdict.get("at"),
                    "terminal": vocab.INTERRUPTED_IS_TERMINAL,
                },
                source=source,
                actor=actor,
            )
            return self.run_view(run_id)

        steps = list(data.get("steps") or [])
        next_cursor = int(data.get("cursor") or 0) + 1
        remaining = steps[next_cursor:]
        self.store.update(
            run_id,
            {
                "cursor": next_cursor,
                "state": vocab.RUN_FINISHED if not remaining else vocab.RUN_RUNNING,
                "current_step": None,
                "wait_started_at": None,
                "wait_resolved_at": rules.stamp(now),
                "finished_at": rules.stamp(now) if not remaining else None,
                "history": [
                    *list(data.get("history") or []),
                    {
                        "kind": "wait_elapsed",
                        "at": rules.stamp(now),
                        "duration_seconds": verdict.get("duration_seconds"),
                    },
                ],
            },
            actor=actor,
            source=source,
        )
        return self.run_view(run_id)

    def _finish(self, run_id: str, *, source: str, actor: str | None) -> None:
        self.store.update(
            run_id,
            {"state": vocab.RUN_FINISHED, "finished_at": rules.stamp(self._moment())},
            actor=actor,
            source=source,
        )

    # -- activity ------------------------------------------------------------- #

    def _activity(
        self,
        conversation_id: str,
        label: str,
        code: str,
        detail: Mapping[str, Any],
        *,
        source: str,
        actor: str | None,
    ) -> None:
        """One event row.

        The label is a human sentence and the code is the stable key a client filters
        on, because "Conversation closed" reads well on a page and is useless as a
        ``where`` clause.
        """

        room = self._room_of(conversation_id)
        self.store.create(
            vocab.ACTIVITY,
            {
                vocab.ROOM_REF: room,
                "conversation_id": conversation_id,
                "label": label,
                "code": code,
                "detail": dict(detail),
                "at": rules.stamp(self._moment()),
            },
            room_id=room,
            actor=actor,
            source=source,
        )

    def activity(self, room_id: str, conversation_id: str | None = None) -> list[dict[str, Any]]:
        where: dict[str, Any] = {vocab.ROOM_REF: room_id}
        if conversation_id:
            where["conversation_id"] = conversation_id
        rows = self.store.find(vocab.ACTIVITY, where, limit=1000)
        return [
            {
                "id": row.get("id"),
                "conversation_id": (row.get("data") or {}).get("conversation_id"),
                "label": (row.get("data") or {}).get("label"),
                "code": (row.get("data") or {}).get("code"),
                "detail": (row.get("data") or {}).get("detail") or {},
                "at": (row.get("data") or {}).get("at"),
            }
            for row in sorted(rows, key=lambda row: str(row.get("created_at") or ""))
        ]

    # -- summary -------------------------------------------------------------- #

    def summary(self, room_id: str) -> dict[str, Any]:
        """What the sweep would do right now, per state.

        Read-only and cheap enough for the page to call on load. ``due`` is the honest
        consequence of the explicit-route decision: it says how many conversations are
        past their window on a live trigger, so a seller can see that a sweep is due
        without the product claiming something fired.
        """

        now = self._moment()
        conversation_rows = self.store.find(
            vocab.CONVERSATIONS, {vocab.ROOM_REF: room_id}, limit=500
        )
        states = {state: 0 for state in vocab.CONVERSATION_STATES}
        priority = 0
        api_created = 0
        for row in conversation_rows:
            data = dict(row.get("data") or {})
            states[rules.require_state(data.get("state"))] += 1
            if data.get("priority"):
                priority += 1
            if data.get("origin") == vocab.API_CREATED_ORIGIN:
                api_created += 1

        trigger_rows = self.store.find(vocab.TRIGGERS, {vocab.ROOM_REF: room_id}, limit=200)
        triggers = [self.trigger_view(row["id"]) for row in trigger_rows]
        run_rows = self.store.find(vocab.RUNS, {vocab.ROOM_REF: room_id}, limit=500)
        run_states = {state: 0 for state in vocab.RUN_STATES}
        for row in run_rows:
            state = str((row.get("data") or {}).get("state"))
            if state in run_states:
                run_states[state] += 1

        return {
            "room_id": room_id,
            "conversations": len(conversation_rows),
            "by_state": states,
            "priority": priority,
            "api_created": api_created,
            "triggers": len(triggers),
            "live_triggers": sum(1 for entry in triggers if entry["live"]),
            "runs": len(run_rows),
            "runs_by_state": run_states,
            "due": self.due_count(room_id, now=now),
            "office_hours": self.office_hours(room_id),
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _skip(
    conversation_id: str | None,
    trigger_id: str | None,
    reason: str,
    *,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One skip row: who was skipped, which trigger, and the published reason."""

    return {
        "conversation_id": conversation_id,
        "trigger_id": trigger_id,
        "reason": reason,
        "detail": vocab.SKIP_REASON_TEXT.get(reason, reason),
        "is_specification_rule": reason in vocab.SPECIFICATION_SKIPS,
        **dict(extra or {}),
    }


def _first_at(parts: Sequence[Mapping[str, Any]], author_kind: str) -> str | None:
    for part in parts:
        if part.get("author_kind") == author_kind and part.get("at"):
            return str(part.get("at"))
    return None


def _last_at(parts: Sequence[Mapping[str, Any]], author_kind: str | None = None) -> str | None:
    for part in reversed(list(parts)):
        if author_kind is None or part.get("author_kind") == author_kind:
            if part.get("at"):
                return str(part.get("at"))
    return None


def _recheck_step_durations(
    steps: Sequence[Mapping[str, Any]], fallback_seconds: Any
) -> list[dict[str, Any]]:
    """Re-bound a step list against a trigger whose duration just changed.

    A Wait step that inherited the trigger's length has to be re-checked when the
    trigger's own length moves, or an edit could store a step outside the researched
    bounds. Only steps that inherit are touched; a step with an explicit duration keeps
    it, because that number was validated when it was written.
    """

    fallback = fallback_seconds if fallback_seconds is not None else vocab.DEFAULT_WAIT_SECONDS
    out: list[dict[str, Any]] = []
    for step in steps:
        kind = str(step.get("kind"))
        if kind in vocab.DURATION_STEPS and step.get("duration_seconds") is None:
            out.append({**step, "duration_seconds": rules.require_step_duration(fallback)})
        else:
            out.append(dict(step))
    return out
