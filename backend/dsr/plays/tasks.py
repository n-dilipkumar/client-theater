"""The one-off action a Play creates, and the task type it is.

"A Play is an automation that generates a one-off action in response to an
internal or external signal" and "When a matching signal arrives, Salesloft creates
the task (call / email / cadence membership) and assigns it via the precedence
order User, Content, Person, Account."

That makes the task the only thing in this workflow with a consequence a person
feels, which is why this module is mostly about two things the research is precise
about:

**One-off means one.** A signal that arrives twice must not leave two tasks. The
research's first-one-wins rule is stated for *signals*, and the drop happens where
the signal is emitted, so the practical consequence is that a Play must be a no-op
for a signal it has already fired on rather than relying on a sender being careful.
:func:`dedupe_key` is what makes that check a single indexed lookup rather than a
scan.

**The task type decides what is missing.** Each of the three researched types has
its own shape - a call has no body, an email has no task subject, a cadence step has
a cadence - so :func:`build_task` fills in what the type implies and
:func:`missing_for` names what could not be filled. A cadence Play with no
``cadence_id`` produces a task that exists and says it has nowhere to take the
buyer, which is the honest outcome of a research gap rather than a silent drop.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.plays.vocabulary import (
    AUTOMATION_NOTE,
    DYNAMIC_FIELD_EXEMPT_ATTRIBUTE,
    SUPPORTED_DYNAMIC_FIELDS,
    TASK_TYPE_LABELS,
    TASK_TYPE_MEANING,
)

#: The lifecycle a one-off task has. Two states, because a Play "generates a
#: one-off action" and the seller's completing it is the end of it.
TASK_STATES: tuple[str, ...] = ("open", "completed")

#: What each task type needs beyond the attribute the framework required. A call
#: needs nothing more; an email needs a template to render or it is a blank email;
#: a cadence step needs a cadence to belong to.
TASK_TYPE_REQUIRES: dict[str, tuple[str, ...]] = {
    "call": (),
    "email": ("email_template",),
    "add-to-cadence": ("cadence_id",),
}

#: The event types each task type produces over its life.
#:
#: "Track outcomes via Salesloft webhooks (``task_created``, ``task_completed``,
#: ``step_created``, ``success_created``)" - the researched vocabulary also lists
#: eight more, and a subscription may name any of them, but a *task* only produces
#: these four: it is created, a cadence step is created and succeeds, and the seller
#: completes it. Emitting ``call_created`` or ``email_updated`` from here would be
#: reporting on an action this product took, and it takes none.
TASK_TYPE_EVENTS: dict[str, tuple[str, ...]] = {
    "call": ("task_created", "task_completed"),
    "email": ("task_created", "task_completed"),
    "add-to-cadence": ("task_created", "step_created", "task_completed", "success_created"),
}


def dedupe_key(play_id: str, signal_key: str) -> str:
    """The one-off guard: one task per (Play, signal).

    Both parts are indexed JSON paths, so this is a ``find()`` rather than a scan,
    and it does not need a migration or a unique index to be correct - the store is
    single-writer, so the find-then-create here is sound for the same reason
    :meth:`dsr.signals.SignalEngine.register` is.
    """
    return f"{play_id}::{signal_key}"


def missing_for(task_type: str, attributes: Mapping[str, Any]) -> list[dict[str, str]]:
    """What this task cannot do, named.

    A task that exists but cannot be completed is not a failed task; it is a task
    whose framework was missing something, and the reason belongs on the task
    because the task is what a seller will look at.
    """
    missing: list[dict[str, str]] = []
    for key in TASK_TYPE_REQUIRES.get(task_type, ()):
        if not attributes.get(key):
            if key == "cadence_id":
                missing.append(
                    {
                        "field": "attributes.cadence_id",
                        "detail": (
                            "An \"Add Person to a Cadence\" Play has nowhere to add the buyer: "
                            "the researched attributes list (task_type, task_subject, "
                            "task_reminder_hours, email_subject, email_template) names no "
                            "cadence. Register the Play with attributes.cadence_id and it is "
                            "routable."
                        ),
                    }
                )
            else:
                missing.append(
                    {
                        "field": f"attributes.{key}",
                        "detail": (
                            f"a {task_type} task has no {key}, so there is nothing to "
                            f"{'render' if key == 'email_template' else 'use'}"
                        ),
                    }
                )
    return missing


def render_subject(attributes: Mapping[str, Any], fields: Mapping[str, Any] | None = None) -> str:
    """The subject of the task, with the one supported dynamic field substituted.

    Only ``task_subject`` may carry a field and only ``name`` is supported there, so
    this is a single substitution with a single value. Anything the framework's
    validator would have refused never reaches here; a field whose value is absent
    is left in place rather than blanked, because a visible ``{name}`` tells a
    seller the framework is wrong and an empty string does not.
    """
    subject = str(attributes.get(DYNAMIC_FIELD_EXEMPT_ATTRIBUTE) or "")
    if not subject:
        return ""
    for name in SUPPORTED_DYNAMIC_FIELDS:
        token = "{" + name + "}"
        if token in subject:
            value = (fields or {}).get(name)
            subject = subject.replace(token, str(value) if value is not None else token)
    return subject


def build_task(
    play: Mapping[str, Any],
    signal: Mapping[str, Any],
    *,
    assignment: Mapping[str, Any],
    room_id: str | None,
    occurred_at: str,
    signal_key: str,
    triggered_by: Sequence[str] = (),
    fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The task record for one (Play, signal) match.

    ``triggered_by`` is the indicator overlap the matcher found for *this* Play, not
    every indicator the signal carries: a signal can match two Plays on two
    different indicators, and a task that claimed both would misreport why it
    exists.

    The subject is rendered here rather than stored raw, because the rendered form
    is what a seller reads in their task list and the raw form is what a reviewer
    needs to see the framework. Both are kept.
    """
    attributes = dict(play.get("attributes") or {})
    task_type = str(attributes.get("task_type") or "")
    subject = render_subject(attributes, fields)
    missing = missing_for(task_type, attributes)
    return {
        "play_id": play.get("id"),
        "play_label": (play.get("label") or {}).get("en"),
        "signal_key": signal_key,
        "signal_type": signal.get("type"),
        "signal_occurred_at": signal.get("occurred_at"),
        "triggered_by": list(triggered_by),
        "task_type": task_type,
        "task_type_label": TASK_TYPE_LABELS.get(task_type, task_type),
        "attributes": attributes,
        "subject": subject,
        "reminder_hours": attributes.get("task_reminder_hours"),
        "assignment": dict(assignment),
        "assigned": bool(assignment.get("assigned")),
        "state": "open",
        "one_off": True,
        "routable": not missing,
        "missing": missing,
        "events": [],
        "created_at": occurred_at,
        "room_note": (
            f"Generated by a Play with no human in the loop, from a signal in room "
            f"{room_id}. {AUTOMATION_NOTE}"
        ),
    }


def present(record: Mapping[str, Any], data: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A task flattened to ``data`` plus the record id, the way a client reads it.

    Every read of a task goes through this, so there is exactly one shape for a task
    in this package and a caller never has to know that the id and the room live in
    the envelope rather than in ``data``.
    """
    payload = dict(data if data is not None else (record.get("data") or {}))
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "revision": record.get("revision"),
        **{key: value for key, value in payload.items()},
    }


def describe() -> dict[str, Any]:
    """The task rules, published for clients and for the page."""
    return {
        "states": list(TASK_STATES),
        "requires": {name: list(keys) for name, keys in TASK_TYPE_REQUIRES.items()},
        "events": {name: list(events) for name, events in TASK_TYPE_EVENTS.items()},
        "meaning": {name: TASK_TYPE_MEANING[name] for name in TASK_TYPE_MEANING},
        "automation_note": AUTOMATION_NOTE,
        "one_off": (
            "A Play generates a one-off action, so one signal creates at most one task per "
            "Play. A repeat of a signal the Play has already fired on reports the task that "
            "exists and creates nothing."
        ),
    }
