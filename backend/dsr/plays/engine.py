"""The one object this workflow is driven through.

:class:`PlayEngine` is the façade the HTTP layer calls. It holds nothing but a
:class:`~dsr.store.RecordStore` handle, which is why the feature module builds one
per request from ``StoreDep`` rather than hanging it on ``app.state`` - an
``app.state`` entry would mean editing ``dsr/api.py``, and the whole point of the
feature host is that adding a workflow is adding a file.

Every method that writes takes a required ``source=``. That is not decoration. The
audit row is the product's guarantee, and an audit row that names a string rather
than a route cannot be traced back to the request that caused it - a defect this
codebase has already shipped once. A required keyword means the omission is a
``TypeError`` at the call site rather than a silently untraceable row in
production.

Four collections, all schema-flexible
--------------------------------------
``play_framework``, ``play_task``, ``play_event``, ``play_webhook``. None has a
migration, a typed column, or a required field beyond the researched contract,
because a team adding a field must not need to coordinate with anyone. Every filter
goes through ``find()`` and therefore through the dynamic index, so a field added
later is queryable the moment it is written.

The chain a signal walks
------------------------
:meth:`dispatch` is the whole workflow in one call, and the order matters:

1. match the signal against every registered Play (:mod:`dsr.plays.matching`);
2. for each Play that fired and has not fired on this signal before, resolve the
   assignment (:mod:`dsr.plays.assignment`);
3. create the one-off task and its ``task_created`` event.

Steps 2 and 3 are inside the loop over the firing Plays but outside the loop over
one Play, so a signal that fires three Plays produces three tasks and three
assignments, and a Play that is disabled produces neither.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.plays import activation, assignment, events, framework, matching, tasks, vocabulary
from dsr.plays.errors import (
    DeliveryConflict,
    EventError,
    EventNotFound,
    FrameworkInUse,
    PlayError,
    PlayNotFound,
    SubscriptionNotFound,
    TaskNotFound,
    UnknownSignal,
    UnknownSignalRegistration,
)
from dsr.plays.vocabulary import AUTOMATION_NOTE

#: The four collections this workflow owns. Named here so a filter and a route
#: cannot disagree about which one they mean.
FRAMEWORKS = "play_framework"
TASK = "play_task"
EVENTS = "play_event"
WEBHOOKS = "play_webhook"

#: Where a signal registration lives. Read-only, and named rather than imported:
#: WF-027 owns that collection and this feature must not import another feature.
#: The Play workflow needs the registration's indicator list and nothing else, and
#: a missing registration is a refusal rather than a guess.
SIGNAL_REGISTRATIONS = "signal_registration"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class PlayEngine:
    """Register Play frameworks, fire them at signals, and track the outcomes."""

    def __init__(self, store: Any, *, now: Any = None) -> None:
        self.store = store
        # Injectable so a test can assert on ordering and timestamps without
        # freezing the whole process clock.
        self._now = now or _now

    def moment(self) -> datetime:
        """The current instant, parsed, so a test can inject a fixed clock."""
        text = self._now() if callable(self._now) else str(self._now)
        parsed = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def stamp(self) -> str:
        return self.moment().isoformat(timespec="milliseconds")

    # -- vocabulary --------------------------------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        """Every published rule, served as data.

        A client renders its pickers from this rather than from a list compiled into
        the page, so a value added server-side reaches every client at once.
        """
        return {
            **vocabulary.describe(),
            **matching.describe(),
            **tasks.describe(),
            **events.describe(),
            "assignment": assignment.describe(),
        }

    def inferences(self) -> dict[str, Any]:
        """Where this workflow stops being sourced. See :mod:`dsr.plays.inferences`."""
        from dsr.plays import inferences as inferences_module

        return inferences_module.describe()

    # -- signal registrations (read-only) ----------------------------------- #

    def declared_indicators(self, registration_id: str) -> list[str] | None:
        """The indicator keys a signal registration declares, or ``None`` if absent.

        ``None`` and ``[]`` mean different things and the difference decides whether
        a Play is refused. ``[]`` means the registration exists and declares no
        indicator, so any Play triggering on one is undeclared. ``None`` means there
        is no registration to check against, and the caller refuses outright - a
        Play registered against nothing is not a Play.
        """
        record = self.store.get(registration_id)
        if record is None or record.get("collection") != SIGNAL_REGISTRATIONS:
            return None
        indicators = (record.get("data") or {}).get("indicators") or []
        return [str(entry.get("key")) for entry in indicators if isinstance(entry, Mapping)]

    # -- play frameworks ---------------------------------------------------- #

    def present_framework(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A Play flattened to ``data`` plus the record id and its activation state.

        Every read of a Play goes through this, and so does every internal use of
        one, so there is exactly one shape for a Play in this package.
        """
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "revision": record.get("revision"),
            **activation.state(data),
            **data,
            "warnings": data.get("warnings") or [],
        }

    def register(
        self, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Register a Play framework against a signal registration.

        "An application can create more than one framework per signal
        registration", so there is no uniqueness check on the pair at all - two
        Plays on one registration is the researched shape, not a conflict.

        The signal registration must exist. "After registering a signal (see #12),
        register a Play" is an order, and a Play pointing at a registration this
        product does not hold could never fire, so it is refused with 409 rather
        than stored as a template waiting for a registration to appear.
        """
        body = dict(payload or {})
        registration_id = body.get("signal_registration_id") or body.get("signalRegistrationId")
        if isinstance(registration_id, str) and registration_id.strip():
            declared = self.declared_indicators(registration_id.strip())
            if declared is None:
                raise UnknownSignalRegistration(
                    f"no signal registration {registration_id.strip()!r}. A Play is "
                    'registered against a signal registration - "After registering a '
                    'signal (see #12), register a Play" - and a Play whose registration '
                    "does not exist can never fire. Register the signal type first."
                )
        else:
            # Let the validator produce the message for a missing or empty id, so
            # the rule and the wording live in one place.
            declared = None

        data, warnings = framework.normalise_framework(payload, declared_indicators=declared)
        data["warnings"] = warnings
        created = self.store.create(FRAMEWORKS, data, actor=actor, source=source)
        return {
            "outcome": "registered",
            "play": self.present_framework(created),
            "warnings": warnings,
        }

    def frameworks(
        self,
        *,
        signal_registration_id: str | None = None,
        task_type: str | None = None,
        enabled: bool | None = None,
        indicator: str | None = None,
        include_destroyed: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """The Play registry, newest first.

        Each filter is a JSON path in the Play's own payload and is resolved by
        ``find()`` through the dynamic index - including
        ``attributes.task_type``, a dotted path into a nested object, which is the
        schema-flexibility rule working: a field this code never declared is
        queryable the moment it is written, with no migration.
        """
        where: dict[str, Any] = {}
        if signal_registration_id:
            where["signal_registration_id"] = signal_registration_id
        if task_type:
            where["attributes.task_type"] = vocabulary.require_task_type(task_type)
        if enabled is not None:
            where["enabled"] = enabled

        if where:
            found = self.store.find(
                FRAMEWORKS, where, limit=1000, include_deleted=include_destroyed
            )
        else:
            found = self.store.list(
                FRAMEWORKS,
                limit=1000,
                order_by="created_at",
                descending=False,
                include_deleted=include_destroyed,
            )
        if not include_destroyed:
            found = [record for record in found if record.get("deleted_at") is None]
        if indicator:
            # A post-filter rather than a `where` clause, and the reason is worth
            # stating: the dynamic index flattens an array to positional paths
            # (`indicators.0`, `indicators.1`), so "any of these keys" is not
            # expressible as one indexed lookup. Every other filter here resolves
            # through the index, which is the schema-flexibility rule working.
            found = [
                record
                for record in found
                if indicator in ((record.get("data") or {}).get("indicators") or [])
            ]
        return [self.present_framework(record) for record in found[: max(1, min(int(limit), 1000))]]

    def framework(self, play_id: str) -> dict[str, Any] | None:
        record = self.store.get(play_id)
        if record is None or record.get("collection") != FRAMEWORKS:
            return None
        return record

    def amend(
        self, play_id: str, patch: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Apply a patch to a Play, or refuse it and say every reason why.

        One attempt should not have to be repeated once per field, so every
        offending path comes back at once. What may change is decided by
        :func:`dsr.plays.framework.amendment_findings`.
        """
        record = self.framework(play_id)
        if record is None:
            raise PlayNotFound(f"play {play_id} not found")
        current = dict(record.get("data") or {})
        generated = self.store.find(TASK, {"play_id": play_id}, limit=1000)
        findings = framework.amendment_findings(current, patch, task_count=len(generated))
        if findings:
            raise FrameworkInUse(
                "this Play's fields are not all patchable. Refused: "
                + "; ".join(
                    f"{finding['path']} ({finding['change']}): {finding['detail']}"
                    for finding in findings
                )
            )
        merged = framework.apply_amendment(current, patch)
        updated = self.store.update(play_id, merged, actor=actor, source=source)
        return self.present_framework(updated)

    def destroy(self, play_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        """Retire a Play, or refuse while it is live.

        "After registration, the registered Play must be enabled in the Salesloft
        UI" - an enabled Play is a running automation, and removing it from under a
        queue of tasks is not something to do by accident. Disable first. The
        refusal is a reading rather than a quotation and is recorded as the
        ``destroy-requires-disable`` inference.

        A destroyed Play is a soft-deleted row, so the tasks it created still name it
        and the audit trail does not point at nothing.
        """
        record = self.framework(play_id)
        if record is None:
            raise PlayNotFound(f"play {play_id} not found")
        data = record.get("data") or {}
        if data.get("enabled") is True:
            raise FrameworkInUse(
                f"play {play_id} is enabled, so it is a running automation and cannot be "
                "destroyed. Disable it first; disabling stops it creating anything and "
                "leaves the tasks it already created exactly as they are."
            )
        self.store.delete(play_id, actor=actor, source=source)
        return {
            "destroyed": True,
            "id": play_id,
            "tasks_retained": len(self.store.find(TASK, {"play_id": play_id}, limit=1000)),
        }

    # -- activation --------------------------------------------------------- #

    def _set_activation(
        self, play_id: str, action: str, *, actor: str | None, source: str
    ) -> dict[str, Any]:
        record = self.framework(play_id)
        if record is None:
            raise PlayNotFound(f"play {play_id} not found")
        handler = activation.enable if action == "enable" else activation.disable
        result = handler(record.get("data") or {}, actor=actor, now=self._now)
        if not result["patch"]:
            return {
                "outcome": result["outcome"],
                "detail": result["detail"],
                "play": self.present_framework(record),
            }
        updated = self.store.update(play_id, result["patch"], actor=actor, source=source)
        return {
            "outcome": result["outcome"],
            "detail": result["detail"],
            "play": self.present_framework(updated),
        }

    def enable(self, play_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        """Switch a Play on. See :mod:`dsr.plays.activation`."""
        return self._set_activation(play_id, "enable", actor=actor, source=source)

    def disable(self, play_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        """Switch a Play off, leaving the tasks it created alone."""
        return self._set_activation(play_id, "disable", actor=actor, source=source)

    # -- dispatch ----------------------------------------------------------- #

    def dispatch(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Run a signal against the registry. The workflow, in one call.

        ``payload`` is either ``{"signal_id": "..."}`` - a signal already stored -
        or ``{"signal": {...}}`` for a signal arriving inline. The inline form
        exists because the researched flow starts at "Registered signal fires" and a
        caller should not have to post a signal to this product before asking what
        this product would do with it.

        Nothing is raised for a signal that fires nothing. A signal is a fact about
        a buyer, and a fact that matches no Play is not a caller error - the
        response says which gate each Play stopped at instead.
        """
        if not isinstance(payload, Mapping):
            raise PlayError("a dispatch must be a JSON object naming a signal")
        signal = self._signal(payload)

        registration = signal.get("registration_id") or signal.get("type")
        plays = self.frameworks(
            signal_registration_id=registration if isinstance(registration, str) else None,
            limit=1000,
        )
        decisions = matching.match_plays(plays, signal)
        fired = [decision for decision in decisions if decision["fired"]]

        moment = self.moment()
        created_tasks: list[dict[str, Any]] = []
        already: list[dict[str, Any]] = []
        unassigned = 0

        for decision in fired:
            play_record = self.framework(str(decision["play_id"]))
            if play_record is None:  # a Play destroyed between the listing and here
                continue
            play = self.present_framework(play_record)
            signal_key = self._signal_key(signal)

            existing = self.store.find(
                TASK, {"play_id": play["id"], "signal_key": signal_key}, limit=1
            )
            if existing:
                # "A Play generates a one-off action": one signal, one task, per
                # Play. The repeat reports the task that exists rather than
                # creating a second one, and the duplicate count on the task makes
                # the retry visible.
                attempts = int((existing[0].get("data") or {}).get("duplicate_attempts") or 0) + 1
                self.store.update(
                    existing[0]["id"], {"duplicate_attempts": attempts}, actor=actor, source=source
                )
                self.store.get(existing[0]["id"]) or existing[0]
                already.append(
                    {
                        "play_id": play["id"],
                        "task_id": existing[0]["id"],
                        "duplicate_attempts": attempts,
                        "reason": "duplicate_signal_for_this_play",
                    }
                )
                continue

            resolved = assignment.resolve_assignment(
                signal.get("attribution"),
                subjects=signal.get("subjects"),
                candidates=self._candidates(signal, room_id),
                now=moment,
            )
            if not resolved["assigned"]:
                unassigned += 1

            task_data = tasks.build_task(
                play,
                signal,
                assignment=resolved,
                room_id=room_id,
                occurred_at=self.stamp(),
                signal_key=signal_key,
                triggered_by=decision.get("overlap") or [],
                fields=signal.get("fields"),
            )
            record = self.store.create(TASK, task_data, room_id=room_id, actor=actor, source=source)
            created_tasks.append(tasks.present(record))
            # The event is stored beside the task rather than on it, so the event
            # collection stays a flat log of what happened and can be filtered by
            # event type without reading every task. It is created `pending`:
            # whether the webhook carrying it arrived is a separate fact, and only
            # an observed delivery answers it.
            self.store.create(
                EVENTS,
                events.build_event(
                    "task_created",
                    task=task_data | {"id": record["id"]},
                    room_id=room_id,
                    occurred_at=self.stamp(),
                ),
                room_id=room_id,
                actor=actor,
                source=source,
            )

        if not plays:
            note = matching.note_no_plays()
        elif not fired:
            note = matching.note_no_plays([p for p in plays if p.get("enabled") is True])
        else:
            note = {
                "reason": "fired",
                "detail": (
                    f"{len(fired)} Play(s) fired and created {len(created_tasks)} task(s) with "
                    "no human in the loop. The only human in the loop is the seller who acts "
                    "on the task."
                ),
            }

        return {
            "room_id": room_id,
            "signal_id": payload.get("signal_id"),
            "signal_key": self._signal_key(signal),
            "signal_type": signal.get("type"),
            "decisions": decisions,
            "fired": len(fired),
            "created": created_tasks,
            "already_dispatched": already,
            "created_count": len(created_tasks),
            "unassigned": unassigned,
            "outcome": note["reason"],
            "detail": note["detail"],
            "automation_note": AUTOMATION_NOTE,
        }

    def _signal(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """The signal a dispatch is about, from an id or supplied inline."""
        signal_id = payload.get("signal_id")
        if isinstance(signal_id, str) and signal_id.strip():
            record = self.store.get(signal_id.strip())
            if record is None or record.get("collection") not in (
                "intent_signal",
                "play_signal",
            ):
                raise UnknownSignal(
                    f"no stored signal {signal_id.strip()!r}. The researched flow starts at "
                    '"Registered signal fires", so a dispatch names a signal this product '
                    "already holds, or supplies one inline as `signal`."
                )
            data = dict(record.get("data") or {})
            return {"id": record.get("id"), "room_id": record.get("room_id"), **data}
        inline = payload.get("signal")
        if isinstance(inline, Mapping):
            return dict(inline)
        raise PlayError(
            "a dispatch must carry signal_id, naming a stored signal, or signal, supplying "
            "one inline"
        )

    def _signal_key(self, signal: Mapping[str, Any]) -> str:
        """The identity a one-off task is deduplicated on.

        The record id when the signal was stored, so two rows are two signals, and
        the idempotency key when it was supplied inline, because that is the field
        the researched signal carries for exactly this purpose ("If we receive two
        signals with the same idempotency_key one of them will be dropped. The first
        one wins."). With neither, a caller can pass ``signal_key``.
        """
        for candidate in (signal.get("id"), signal.get("idempotency_key")):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        raise PlayError(
            "a signal must carry an id or an idempotency_key. Without one there is no way to "
            "tell a new signal from a repeat, and a Play that creates a task per arrival is "
            "not a one-off action."
        )

    def _candidates(
        self, signal: Mapping[str, Any], room_id: str | None
    ) -> list[Mapping[str, Any]]:
        """The Account roster, from the signal, or from the room it happened in.

        Read through the store rather than computed, because the Buyer Engagement
        Score's formula is a documented research gap. A room may carry an
        ``engagement_roster`` because this product's payloads are open JSON and a
        team that computes the score can add it without a migration.
        """
        for source in (signal.get("candidates"), signal.get("engagement_roster")):
            if isinstance(source, (list, tuple)):
                return list(source)
        if not room_id:
            return []
        record = self.store.get(room_id)
        roster = (record.get("data") or {}).get("engagement_roster") if record else None
        return list(roster) if isinstance(roster, (list, tuple)) else []

    # -- tasks -------------------------------------------------------------- #

    def task(self, task_id: str, *, room_id: str | None = None) -> dict[str, Any] | None:
        record = self.store.get(task_id)
        if record is None or record.get("collection") != TASK:
            return None
        if room_id is not None and record.get("room_id") != room_id:
            return None
        return tasks.present(record)

    def generated_tasks(
        self,
        *,
        room_id: str | None = None,
        play_id: str | None = None,
        state: str | None = None,
        assigned: bool | None = None,
        task_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """The tasks the automation generated, newest first.

        Room scope is a post-filter rather than a ``where`` clause, because
        ``find()`` takes no room and ``room_id`` is a column rather than a JSON
        path. It is a filter, not an access rule: this product's rooms carry no ACL
        and pretending otherwise in a query would be a claim the store cannot back.
        """
        where: dict[str, Any] = {}
        if play_id:
            where["play_id"] = play_id
        if state:
            where["state"] = state
        if assigned is not None:
            where["assigned"] = assigned
        if task_type:
            where["task_type"] = vocabulary.require_task_type(task_type)

        if where:
            found = self.store.find(TASK, where, limit=1000)
            records = [
                record for record in found if room_id is None or record.get("room_id") == room_id
            ]
        elif room_id:
            records = self.store.list(TASK, room_id=room_id, limit=1000)
        else:
            records = self.store.list(TASK, limit=1000)

        capped = max(1, min(int(limit), 1000))
        return [tasks.present(record) for record in records[:capped]]

    def complete_task(
        self,
        task_id: str,
        payload: Mapping[str, Any] | None,
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """The seller acted. Close the one-off action and record the outcome.

        "seller acts -> task/step/success events stream out via webhooks." This is
        that last step: the task leaves ``open``, and the event types the task type
        produces on completion are recorded. A cadence step also produces
        ``step_created`` and ``success_created``, because adding someone to a
        cadence is what created the step.
        """
        record = self.store.get(task_id)
        if record is None or record.get("collection") != TASK:
            raise TaskNotFound(f"task {task_id} not found")
        if room_id is not None and record.get("room_id") != room_id:
            raise TaskNotFound(f"task {task_id} is not in room {room_id}")
        data = dict(record.get("data") or {})
        if data.get("state") == "completed":
            return {
                "outcome": "already_completed",
                "detail": "This task was already completed, so nothing was written.",
                "task": tasks.present(record),
                "events": [],
            }

        note = (payload or {}).get("note")
        if note is not None and not isinstance(note, str):
            raise PlayError("note must be a string")
        stamp = self.stamp()
        updated = self.store.update(
            task_id,
            {
                "state": "completed",
                "completed_at": stamp,
                "outcome_note": (note or "").strip() or None,
            },
            actor=actor,
            source=source,
        )

        task_type = str(data.get("task_type") or "")
        produced: list[dict[str, Any]] = []
        for event_type in tasks.TASK_TYPE_EVENTS.get(task_type, ("task_completed",)):
            if event_type == "task_created":
                # Already recorded at dispatch. The researched lifecycle does not
                # create the task twice.
                continue
            record = self.store.create(
                EVENTS,
                events.build_event(
                    event_type,
                    task=data | {"id": task_id},
                    room_id=room_id,
                    occurred_at=stamp,
                ),
                room_id=room_id,
                actor=actor,
                source=source,
            )
            # The stored record, not the body that went into it: the caller needs
            # the event's id to record a delivery against it, and a body without one
            # is an event it can see but not act on.
            produced.append(tasks.present(record))

        return {
            "outcome": "completed",
            "detail": (
                f"Task closed. {len(produced)} outcome event(s) recorded. Nothing further is "
                "automated: a Play is a one-off action, not a sequence."
            ),
            "task": tasks.present(updated),
            "events": produced,
        }

    # -- outcome events ----------------------------------------------------- #

    def outcome_events(
        self,
        *,
        room_id: str | None = None,
        event_type: str | None = None,
        delivery_state: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """The recorded outcome events, newest first."""
        where: dict[str, Any] = {}
        if event_type:
            where["event_type"] = events.require_event_type(event_type)
        if delivery_state:
            if delivery_state not in events.DELIVERY_STATES:
                raise PlayError(
                    f"delivery state must be one of {', '.join(events.DELIVERY_STATES)}"
                )
            where["state"] = delivery_state
        if task_id:
            where["task_id"] = task_id

        if where:
            found = self.store.find(EVENTS, where, limit=1000)
            records = [
                record for record in found if room_id is None or record.get("room_id") == room_id
            ]
        elif room_id:
            records = self.store.list(EVENTS, room_id=room_id, limit=1000)
        else:
            records = self.store.list(EVENTS, limit=1000)

        capped = max(1, min(int(limit), 1000))
        return [tasks.present(record) for record in records[:capped]]

    def record_attempt(
        self,
        event_id: str,
        payload: Mapping[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Record one webhook delivery attempt, and apply the researched retry rule.

        "A failing webhook is retried three additional times, spaced 15 seconds
        apart, before being marked as failed." The rule is enforced, not just
        reported: an attempt recorded before its scheduled time is refused, and a
        delivery that is already ``delivered`` or ``failed`` takes no more attempts
        at all.
        """
        record = self.store.get(event_id)
        if record is None or record.get("collection") != EVENTS:
            raise EventNotFound(f"event {event_id} not found")
        if room_id is not None and record.get("room_id") != room_id:
            raise EventNotFound(f"event {event_id} is not in room {room_id}")
        data = dict(record.get("data") or {})
        if not isinstance(payload, Mapping):
            raise EventError("a delivery attempt must be a JSON object")

        current = events.delivery(data.get("attempts"))
        if current["state"] in ("delivered", "failed"):
            raise DeliveryConflict(
                f"this delivery is {current['state']} and takes no further attempts. "
                + current["detail"]
            )
        moment = self.moment()
        if not events.due(current, moment):
            raise DeliveryConflict(
                f"this delivery's next attempt is not due until {current['next_attempt_at']}, "
                f"{events.WEBHOOK_RETRY_SPACING_SECONDS} seconds after the attempt that failed. "
                "Recording one early would make the researched spacing untrue."
            )

        ok, detail = events.attempt_ok(payload)
        attempt = {
            "at": moment.isoformat(timespec="milliseconds"),
            "ok": ok,
            "status_code": detail.get("status_code"),
            "reason": detail.get("reason"),
        }
        history = list(data.get("attempts") or []) + [attempt]
        updated = self.store.update(
            event_id,
            {"attempts": history, **events.delivery(history)},
            actor=actor,
            source=source,
        )
        return {
            "outcome": "recorded",
            "attempt": attempt,
            "delivered": ok,
            "event": tasks.present(updated),
        }

    # -- webhook subscriptions ---------------------------------------------- #

    def subscribe(
        self, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Register a webhook subscription for the researched event types."""
        data = events.normalise_subscription(payload)
        if not data.get("target_url"):
            data["warnings"] = [
                {
                    "code": "no_target_url",
                    "severity": "warning",
                    "detail": (
                        "No target_url. The research names the event types and the retry "
                        "policy but does not publish a subscription's own field list, so a "
                        "target is accepted rather than demanded - and a subscription with "
                        "none has nowhere to deliver to."
                    ),
                }
            ]
        else:
            data["warnings"] = []
        created = self.store.create(WEBHOOKS, data, actor=actor, source=source)
        return {
            "outcome": "subscribed",
            "subscription": self.present(created),
            "warnings": data["warnings"],
        }

    def present(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "revision": record.get("revision"),
            **data,
        }

    def subscriptions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [
            self.present(record)
            for record in self.store.list(WEBHOOKS, limit=max(1, min(int(limit), 1000)))
        ]

    def unsubscribe(
        self, subscription_id: str, *, actor: str | None, source: str
    ) -> dict[str, Any]:
        record = self.store.get(subscription_id)
        if record is None or record.get("collection") != WEBHOOKS:
            raise SubscriptionNotFound(f"webhook subscription {subscription_id} not found")
        self.store.delete(subscription_id, actor=actor, source=source)
        return {
            "unsubscribed": True,
            "id": subscription_id,
            "event_types": (record.get("data") or {}).get("event_types"),
        }

    # -- summary ------------------------------------------------------------ #

    def summary(self, *, room_id: str | None) -> dict[str, Any]:
        """Counts for the page header, and the automation note beside them.

        Counted over this room's tasks rather than the whole collection, so a
        room's header says what happened in that room. The two numbers a reviewer
        should look at first are ``live_plays`` and ``tasks_from_automation``, because
        together they say how much of this workflow is actually running.
        """
        frameworks = self.frameworks(limit=1000)
        generated = self.generated_tasks(room_id=room_id, limit=1000)
        recorded = self.outcome_events(room_id=room_id, limit=1000)

        by_type: dict[str, int] = {}
        for task in generated:
            key = str(task.get("task_type"))
            by_type[key] = by_type.get(key, 0) + 1
        by_state: dict[str, int] = {name: 0 for name in tasks.TASK_STATES}
        for task in generated:
            key = str(task.get("state"))
            by_state[key] = by_state.get(key, 0) + 1
        by_delivery: dict[str, int] = {name: 0 for name in events.DELIVERY_STATES}
        for event in recorded:
            key = str(event.get("state"))
            by_delivery[key] = by_delivery.get(key, 0) + 1
        by_event_type: dict[str, int] = {}
        for event in recorded:
            key = str(event.get("event_type"))
            by_event_type[key] = by_event_type.get(key, 0) + 1

        return {
            "room_id": room_id,
            "frameworks": len(frameworks),
            "live_plays": sum(1 for play in frameworks if play.get("enabled") is True),
            "registered_not_enabled": sum(
                1 for play in frameworks if play.get("enabled") is not True
            ),
            "tasks": len(generated),
            "tasks_from_automation": len(generated),
            "open_tasks": by_state.get("open", 0),
            "completed_tasks": by_state.get("completed", 0),
            "unassigned_tasks": sum(1 for task in generated if task.get("assigned") is not True),
            "unroutable_tasks": sum(1 for task in generated if task.get("routable") is not True),
            "duplicates_dropped": sum(
                int(task.get("duplicate_attempts") or 0) for task in generated
            ),
            "by_task_type": [
                {"task_type": name, "count": count} for name, count in sorted(by_type.items())
            ],
            "events": len(recorded),
            "by_event_type": [
                {"event_type": name, "count": count}
                for name, count in sorted(by_event_type.items())
            ],
            "by_delivery_state": [
                {"state": name, "count": by_delivery.get(name, 0)}
                for name in events.DELIVERY_STATES
            ],
            "webhook_subscriptions": len(self.store.list(WEBHOOKS, limit=1000)),
            "automation_note": AUTOMATION_NOTE,
            "activation_path": vocabulary.ACTIVATION_PATH,
            "task_types": list(vocabulary.TASK_TYPES),
        }
