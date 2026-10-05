"""WF-107: the chase and reroute rules, as pure functions over values.

Every rule here is a statement about a duration, an instant, a list of names or a
schedule. None of them reads or writes anything. The engine beside this module turns
them into records. This module is what a test can call with two integers and a clock
and check.

The rules the rest of the workflow leans on
-------------------------------------------

**Both bounds on a duration are exclusive.** "The duration must be longer than 30
seconds and shorter than 14 days." :func:`require_duration` refuses 30 and refuses 14
days, not because the spec is vague but because "longer than" and "shorter than" are
both strict. The bound applies to the trigger's inactivity timer and to a Wait or
Snooze step's own duration, which are two separate numbers the research bounds
separately.

**Each trigger measures its own clock.** The customer trigger is anchored to the last
message of any kind, because the data flow says "Last-message timestamp on the
Conversation object -> inactivity elapsed -> trigger fires". The teammate trigger is
anchored to the customer's *first* message, because "if the customer sends 3 messages
in a row, the timer will be set against their first message, not last".
:func:`anchor_for` picks between them and :func:`anchor_instant` finds the instant, so
a burst of three customer messages leaves the teammate timer on the first.

**A trigger fires once per customer message.** "can only trigger once per customer
message". :func:`arm_token` mints one token per customer message and
:func:`should_fire` consumes it, so the second evaluation against the same token is a
skip rather than a second run.

**A conversation created through the REST API never triggers.** "This workflow won't
trigger for conversations created via our REST API." :func:`trigger_eligible` reports it
with the published skip code rather than raising, because "this one is exempt" is an
outcome of the sweep, not a fault in the caller's request.

**A workflow containing a Wait or Snooze decides when a conversation closes.**
"Any workflow containing a Wait or Snooze action will take precedence" over the global
auto-close setting. :func:`close_authority` reads the step list rather than a stored
flag, so a trigger cannot claim precedence it does not have.

**An interruption cancels the wait, and only the events the step listed.**
"configure the duration and which interruption events cancel the wait (teammate and
customer messages)". :func:`interruption_cancels` answers for one event against one
step, and :func:`first_interruption` answers for a list of parts.

**Office hours are walked, not added.** A message that arrives after hours plus a
15-minute target is not "half past five plus a quarter": it is nine-five the next
working day. :func:`expected_reply_time` walks forward minute by minute until the
result falls inside the schedule.

What this module does not decide
--------------------------------

Whether a message actually left this product. It computes when one is due and records
the decision; delivery is the integrator's. And whether a conversation is one this
workflow should touch at all, which is a sweep result rather than a rule.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.conversation_chase import vocabulary as vocab

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# Declared here and raised by nothing else in the product. That is what makes it safe
# for the feature module to map them: a handler for a shared type such as ``ValueError``
# would intercept that exception across the whole application.


class ChaseRefusal(ValueError):
    """A duration, trigger, step, schedule or state the workflow will not accept.

    Carries a published code from :data:`vocabulary.ERROR_CODES` and a field-keyed map,
    because the page puts each message beside the input that caused it rather than in
    one combined sentence.
    """

    def __init__(self, code: str, field: str | None = None, detail: str | None = None) -> None:
        message = detail or vocab.error_sentence(code)
        super().__init__(message)
        self.code = code
        self.status = vocab.error_status(code)
        self.detail = message
        self.errors: dict[str, str] = {field: message} if field else {"detail": message}


class TriggerNotFound(LookupError):
    """No such trigger.

    Its own type rather than the store's ``RecordNotFound``, for the same reason as every
    other workflow: a feature may only map error types it raises itself.
    """


class ConversationNotFound(LookupError):
    """No such conversation. Same reasoning as the class above."""


class RunNotFound(LookupError):
    """No such run. Same reasoning as the class above."""


# --------------------------------------------------------------------------- #
# The payload-side room reference
# --------------------------------------------------------------------------- #


def room_ref_of(data: Mapping[str, Any], record: Mapping[str, Any] | None = None) -> Any:
    """The room a payload belongs to: the payload's own key, else the envelope's."""

    value = data.get(vocab.ROOM_REF)
    if value:
        return value
    return (record or {}).get("room_id")


# --------------------------------------------------------------------------- #
# Instants
# --------------------------------------------------------------------------- #


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime | str | None = None) -> str:
    """An ISO 8601 instant in UTC, with the milliseconds kept.

    A stored string is accepted as well as a ``datetime``, because every instant this
    workflow records is written as a string and read back as one. Accepting both here
    means a caller holding a stored value never has to know which it has.
    """

    if moment is None:
        value = utcnow()
    elif isinstance(moment, datetime):
        value = moment
    else:
        value = coerce_instant(moment, "moment")
        if value is None:
            raise ChaseRefusal("not_an_instant", "moment")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def coerce_instant(value: Any, field: str = "at") -> datetime | None:
    """Read an ISO 8601 instant, or ``None`` when the value is not one.

    A trailing ``Z`` is accepted, because that is how the instants arrive from a client
    and from the store. A naive instant is read as UTC rather than refused: refusing
    would make a caller who omitted the offset fail for a reason the research never
    discusses, and the alternative of guessing local time is worse.
    """

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def instant_or_none(value: Any) -> datetime | None:
    """:func:`coerce_instant` with the field name defaulted, for reading stored values.

    Reading a stored timestamp is not a refusal: a record written by an older version
    may simply not carry the field, and a caller listing conversations should see it
    rather than get a 422.
    """

    return coerce_instant(value)


def elapsed_seconds(since: datetime, now: datetime) -> float:
    """Whole seconds from ``since`` to ``now``, never negative.

    Clamped at zero so a message that arrives with a future timestamp -- a client clock
    slightly ahead, or a row written by a test moving time backwards -- reads as zero
    elapsed rather than as a negative window that fires everything immediately.
    """

    return max(0.0, (now - since).total_seconds())


# --------------------------------------------------------------------------- #
# Durations
# --------------------------------------------------------------------------- #


def require_duration(value: Any, field: str = "duration_seconds") -> int:
    """A duration in whole seconds, strictly inside the researched bounds.

    "The duration must be longer than 30 seconds and shorter than 14 days." Both ends
    are exclusive, so exactly 30 and exactly 14 days are refused.

    A fractional number of seconds is refused rather than rounded: a timer that reads
    30.4 seconds is legal and one that reads 29.6 would round to 30 and then be
    refused, so the two would disagree about the same duration. A caller who wants
    seconds sends seconds.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ChaseRefusal("duration_not_a_number", field)

    if isinstance(value, float):
        if value != int(value):
            raise ChaseRefusal(
                "duration_not_a_number",
                field,
                f"{field} must be a whole number of seconds, not {value!r}.",
            )
        value = int(value)

    seconds = int(value)
    if seconds <= vocab.MIN_DURATION_SECONDS:
        raise ChaseRefusal(
            "duration_out_of_range",
            field,
            f"{field} must be longer than {vocab.MIN_DURATION_SECONDS} seconds; {seconds} is not.",
        )
    if seconds >= vocab.MAX_DURATION_SECONDS:
        raise ChaseRefusal(
            "duration_out_of_range",
            field,
            f"{field} must be shorter than 14 days; {seconds} seconds is not.",
        )
    return seconds


def require_step_duration(value: Any, field: str = "duration_seconds") -> int:
    """A Wait or Snooze step's own duration.

    The same bounds as the trigger's timer and for the same reason: the spec bounds
    "the duration" and both numbers are a duration the builder accepts.
    """

    return require_duration(value, field)


# --------------------------------------------------------------------------- #
# Triggers
# --------------------------------------------------------------------------- #


def require_trigger_kind(value: Any) -> str:
    try:
        return vocab.require_trigger_kind(value)
    except ValueError as exc:
        raise ChaseRefusal("unknown_trigger_kind", "kind", str(exc)) from exc


def require_step_kind(value: Any) -> str:
    try:
        return vocab.require_step_kind(value)
    except ValueError as exc:
        raise ChaseRefusal("unknown_step_kind", "kind", str(exc)) from exc


def require_interruption_events(value: Any) -> list[str]:
    """The events a Wait or Snooze step is cancelled by.

    Only the two the research names are accepted. A third would be a cancellation rule
    nothing sources, and accepting arbitrary strings would let a step list an event
    that can never arrive, which reads on the page as protection that does not exist.
    """

    if value is None:
        return []
    raw = [value] if isinstance(value, str) else list(value)
    events: list[str] = []
    for entry in raw:
        text = vocab._slug(entry)
        if text not in vocab.INTERRUPTION_EVENTS:
            raise ChaseRefusal(
                "unknown_interruption_event",
                "interruption_events",
                f"unknown interruption event {entry!r}; the wait is cancelled by "
                f"{', '.join(vocab.INTERRUPTION_EVENTS)}",
            )
        if text not in events:
            events.append(text)
    return events


def require_channel(value: Any) -> str:
    text = vocab._slug(value)
    if text not in vocab.CHANNELS:
        raise ChaseRefusal(
            "unknown_channel",
            "channels",
            f"unknown channel {value!r}; it must be one of {', '.join(vocab.CHANNELS)}",
        )
    return text


def require_author_kind(value: Any) -> str:
    try:
        return vocab.require_author_kind(value)
    except ValueError as exc:
        raise ChaseRefusal("unknown_author_kind", "author_kind", str(exc)) from exc


def require_origin(value: Any) -> str:
    try:
        return vocab.require_origin(value)
    except ValueError as exc:
        raise ChaseRefusal("unknown_origin", "origin", str(exc)) from exc


def require_state(value: Any) -> str:
    try:
        return vocab.require_state(value)
    except ValueError as exc:
        raise ChaseRefusal("unknown_conversation_state", "state", str(exc)) from exc


def require_tag(value: Any) -> str:
    try:
        return vocab.require_tag(value)
    except ValueError as exc:
        raise ChaseRefusal("unknown_tag", "tag", str(exc)) from exc


def require_channels(value: Any) -> list[str]:
    """A trigger's channels, de-duplicated and in the vocabulary's order.

    Ordered by the vocabulary rather than by the caller's list so two triggers naming
    the same channels in a different order are the same trigger.
    """

    if value is None:
        return []
    raw = [value] if isinstance(value, str) else list(value)
    chosen = {require_channel(entry) for entry in raw}
    return [channel for channel in vocab.CHANNELS if channel in chosen]


def normalise_step(step: Mapping[str, Any] | None) -> dict[str, Any]:
    """One builder step, validated and reduced to the keys the rules read.

    Returns a single dict, not a one-element list: :func:`normalise_steps` is the list
    form and a caller holding one step should not have to know which of the two it called.
    """
    """One builder step, validated and reduced to the keys the rules read.

    Normalisation happens once, at the edge, so every later read of a step is looking
    at a value that was already checked. An unknown key is dropped rather than
    refused: the research names the blocks, not the fields the vendor's editor
    happens to persist, and a payload carrying one more key is not an error.
    """

    step = step or {}
    kind = require_step_kind(step.get("kind"))
    out: dict[str, Any] = {"kind": kind}

    if kind in vocab.DURATION_STEPS:
        raw = step.get("duration_seconds")
        if raw is None:
            # A step with no duration of its own inherits the trigger's own length, and
            # the builder's default is the number the spec names.
            raw = step.get("fallback_seconds", vocab.DEFAULT_WAIT_SECONDS)
        out["duration_seconds"] = require_step_duration(raw, "duration_seconds")

    if kind in vocab.HOLDING_STEPS:
        out["interruption_events"] = require_interruption_events(step.get("interruption_events"))

    if kind == vocab.STEP_TAG:
        out["tag"] = require_tag(step.get("tag"))

    if kind == vocab.STEP_ASSIGN:
        inbox = " ".join(str(step.get("inbox") or "").split())
        if not inbox:
            raise ChaseRefusal(
                "inbox_unknown", "inbox", "an assign step needs the inbox to reroute to"
            )
        out["inbox"] = inbox

    body = step.get("body")
    if body is not None:
        out["body"] = str(body)

    if kind == vocab.STEP_MARK_PRIORITY:
        out["priority"] = True

    return out


def normalise_steps(steps: Any) -> list[dict[str, Any]]:
    """A trigger's step list, validated in order.

    Order is preserved and meaningful: the research describes "a closing message block,
    then a Close conversation action, then Tag conversation", so a reordering would
    change what the workflow does. The engine walks this list by index.
    """

    if steps is None:
        return []
    if isinstance(steps, Mapping):
        steps = [steps]
    return [normalise_step(step) for step in steps]


def step_duration(step: Mapping[str, Any]) -> int | None:
    """A step's own duration, or ``None`` for a step that carries none.

    The read side of the same fact :func:`normalise_step` writes, so a caller inspecting
    a stored step list never has to ask whether ``show_expected_reply_time`` is one of the
    steps that has a number.
    """

    if str(step.get("kind")) not in vocab.DURATION_STEPS:
        return None
    return int(step.get("duration_seconds") or 0)


def has_precedence_step(steps: Iterable[Mapping[str, Any]]) -> bool:
    """Does this step list contain a Wait or a Snooze?

    "Any workflow containing a Wait or Snooze action will take precedence" over the
    global auto-close setting. Read off the steps rather than stored as a flag, so a
    trigger cannot carry precedence it does not have.
    """

    return any(str(step.get("kind")) in vocab.PRECEDENCE_STEPS for step in steps)


def close_authority(
    steps: Sequence[Mapping[str, Any]], global_auto_close: Any = None
) -> dict[str, Any]:
    """Who decides when this conversation closes.

    The researched precedence, resolved. A workflow carrying a Wait or a Snooze owns the
    close; anything else falls back to the account's global auto-close setting, which
    the research places "under ``Settings > AI & Automation``".

    ``global_auto_close`` is read as a flag: ``True`` closes after the account's
    default quiet period, ``False`` leaves it open, and ``None`` means the account has
    no global setting at all. The three answers are distinguishable because
    "nothing will close this" and "the global setting will close it" are different
    answers to a question a seller is looking at.
    """

    owns = has_precedence_step(steps)
    global_on = bool(global_auto_close)
    return {
        "authority": "workflow" if owns else ("global" if global_on else "none"),
        "workflow_owns_close": owns,
        "global_auto_close": global_on,
        "precedence_steps": sorted(
            {
                str(step.get("kind"))
                for step in steps
                if str(step.get("kind")) in vocab.PRECEDENCE_STEPS
            }
        ),
        "quote": vocab.WAIT_PRECEDENCE,
    }


# --------------------------------------------------------------------------- #
# Anchors and the inactivity window
# --------------------------------------------------------------------------- #


def parts_of(
    parts: Sequence[Mapping[str, Any]], author_kind: str | None = None
) -> list[Mapping[str, Any]]:
    """The parts a rule reads, in stored order.

    Filtered by author kind when asked. Ordered oldest-first by instant, because every
    rule here is about "first" or "last" and a list in arbitrary order would make both
    words mean whatever the store happened to return.
    """

    selected = [
        part for part in parts if author_kind is None or part.get("author_kind") == author_kind
    ]
    return sorted(selected, key=lambda part: instant_or_none(part.get("at")) or _EPOCH)


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def anchor_instant(
    parts: Sequence[Mapping[str, Any]], kind: str
) -> tuple[datetime | None, str | None]:
    """The instant a trigger of this kind measures from, and which anchor it read.

    Two answers, because the research gives two anchors and the choice is the whole of
    the difference between the triggers:

    * ``customer_idle`` reads the last message of *any* kind. The data flow is explicit:
      "Last-message timestamp on the Conversation object -> inactivity elapsed ->
      trigger fires". A teammate reply resets this clock, which is the point: it
      measures the buyer being unresponsive, and a rep answering means the buyer is no
      longer being left waiting.
    * ``teammate_idle`` reads the customer's **first** message. "if the customer sends 3
      messages in a row, the timer will be set against their first message, not last."
      Three in a row anchor to the first.

    ``None`` is returned when there is no message of the required kind, which the sweep
    reports as its own skip rather than treating as zero elapsed.
    """

    anchor = vocab.anchor_for(kind)

    if anchor == vocab.ANCHOR_FIRST_CUSTOMER_MESSAGE:
        ordered = parts_of(parts, vocab.AUTHOR_CUSTOMER)
        return (coerce_instant(ordered[0].get("at")) if ordered else None), anchor

    ordered = parts_of(parts)
    return (coerce_instant(ordered[-1].get("at")) if ordered else None), anchor


def inactivity(anchor: datetime | None, now: datetime, duration_seconds: int) -> dict[str, Any]:
    """Has the window elapsed, and how much of it is left?

    A single place that answers "due or not", so the sweep, the summary and the page's
    countdown cannot disagree. ``seconds_remaining`` is 0 once the window has passed and
    ``seconds_overdue`` is 0 before it, which means a reader never has to compare a
    signed number against zero to know which side of the boundary they are on.
    """

    if anchor is None:
        return {
            "due": False,
            "seconds_elapsed": None,
            "seconds_remaining": None,
            "seconds_overdue": None,
            "anchor": None,
        }

    elapsed = elapsed_seconds(anchor, now)
    remaining = duration_seconds - elapsed
    return {
        "due": remaining <= 0,
        "seconds_elapsed": elapsed,
        "seconds_remaining": max(0.0, remaining) if remaining > 0 else 0.0,
        "seconds_overdue": -remaining if remaining < 0 else 0.0,
        "anchor": stamp(anchor),
    }


# --------------------------------------------------------------------------- #
# Firing once per customer message
# --------------------------------------------------------------------------- #


def arm_token(parts: Sequence[Mapping[str, Any]]) -> str | None:
    """The token that says which customer message a trigger may fire against.

    "can only trigger once per customer message". The token is the instant of the
    newest customer message, so a second message mints a new token and re-arms the
    trigger exactly once, while re-evaluating against the same token does not.

    ``None`` when the conversation has no customer message at all, which is a
    conversation this workflow has nothing to chase.
    """

    ordered = parts_of(parts, vocab.AUTHOR_CUSTOMER)
    if not ordered:
        return None
    return stamp(ordered[-1].get("at"))


def should_fire(token: str | None, consumed: Iterable[Any] | None) -> bool:
    """May a trigger fire against this token?

    ``consumed`` is the set of tokens already spent. The first evaluation of a token
    answers ``True``; every later one answers ``False``, which is the once-per-message
    rule as arithmetic rather than as prose.
    """

    if token is None:
        return False
    return token not in set(consumed or ())


# --------------------------------------------------------------------------- #
# Eligibility
# --------------------------------------------------------------------------- #


def trigger_eligible(conversation: Mapping[str, Any]) -> dict[str, Any]:
    """May this conversation's triggers fire at all?

    The researched exception, and the state rules, answered together. "This workflow
    won't trigger for conversations created via our REST API" is the first check and is
    answered with the published skip code, because a conversation created through the
    API is exempt whatever its state.

    A snoozed conversation is paused and a closed one is history, so both are skipped
    too, with their own codes rather than the API one. Three distinct reasons because
    they mean three different things to whoever reads the sweep.
    """

    # Through the refusing wrappers, not the vocabulary's own ``require_*``. The router
    # maps :class:`ChaseRefusal` and nothing else, so a bare ``ValueError`` escaping here
    # would reach the client as a 500 on a caller that sent a bad origin.
    origin = require_origin(conversation.get("origin"))
    state = require_state(conversation.get("state"))

    if origin == vocab.API_CREATED_ORIGIN:
        return {
            "eligible": False,
            "reason": vocab.SKIP_API_CREATED,
            "origin": origin,
            "state": state,
        }

    if state == vocab.STATE_SNOOZED:
        return {"eligible": False, "reason": vocab.SKIP_SNOOZED, "origin": origin, "state": state}

    if state == vocab.STATE_CLOSED:
        return {"eligible": False, "reason": vocab.SKIP_CLOSED, "origin": origin, "state": state}

    return {"eligible": True, "reason": None, "origin": origin, "state": state}


# --------------------------------------------------------------------------- #
# Waiting and interruption
# --------------------------------------------------------------------------- #


def interruption_cancels(step: Mapping[str, Any], part: Mapping[str, Any]) -> bool:
    """Does this part cancel this step's wait?

    Two conditions, both required:

    1. The step listed the event. A wait with no ``interruption_events`` is not
       cancelled by anything, which is what "configure ... which interruption events
       cancel the wait" means when the seller clears the list.
    2. The part is from the right author. The research names two events, one per kind
       of message, so a teammate message does not cancel a wait that listed only
       customer messages.

    A part the workflow itself wrote never cancels anything, whatever the step listed.
    The workflow's own message block is the thing the wait is waiting on, so letting it
    interrupt its own wait would make every chase workflow stop the moment it spoke.
    """

    listed = [vocab._slug(event) for event in (step.get("interruption_events") or [])]
    if not listed:
        return False

    event = vocab.event_for_author_kind(part.get("author_kind"))
    if event is None:
        # No event is raised by a workflow's own part, or by anything outside the two
        # kinds the research names.
        return False

    return event in listed


def first_interruption(
    step: Mapping[str, Any], parts: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    """The earliest part that cancels this wait, if any.

    Parts that arrived before the wait started do not count. A wait is a window from
    its own start instant, so a message the buyer sent an hour before the workflow began
    waiting is history rather than an interruption, and counting it would make the wait
    impossible to satisfy.
    """

    started = coerce_instant(step.get("started_at") or step.get("wait_started_at"))
    for part in parts_of(parts):
        at = coerce_instant(part.get("at"))
        if at is None:
            continue
        if started is not None and at < started:
            continue
        if interruption_cancels(step, part):
            return part
    return None


def wait_state(
    step: Mapping[str, Any], parts: Sequence[Mapping[str, Any]], now: datetime
) -> dict[str, Any]:
    """Is a wait satisfied by its clock, interrupted, or still running?

    Asked in that order. Interruption wins over the clock, because a buyer who answers
    during the window means the wait is over however much time was left; if the clock
    ran out first and a message arrived in the same instant, the conversation is still
    the buyer's, so the interruption is the answer that ends the run.
    """

    interrupting = first_interruption(step, parts)
    if interrupting is not None:
        return {
            "state": "interrupted",
            "interrupted_by": interrupting.get("author_kind"),
            "at": stamp(interrupting.get("at")),
            "quote": vocab.INTERRUPTION_EVENTS_QUOTE,
        }

    started = coerce_instant(step.get("started_at") or step.get("wait_started_at"))
    if started is None:
        return {"state": "running", "interrupted_by": None, "at": None}

    duration = int(step.get("duration_seconds") or 0)
    elapsed = elapsed_seconds(started, now)
    if elapsed >= duration:
        return {
            "state": "elapsed",
            "elapsed_seconds": elapsed,
            "duration_seconds": duration,
            "remaining_seconds": 0.0,
        }

    return {
        "state": "waiting",
        "elapsed_seconds": elapsed,
        "duration_seconds": duration,
        "remaining_seconds": duration - elapsed,
    }


# --------------------------------------------------------------------------- #
# Office hours
# --------------------------------------------------------------------------- #


def default_office_hours() -> dict[str, Any]:
    """The derived default schedule.

    Derived rather than researched: the spec names office-hours configuration as a data
    source and sources no model. Monday to Friday 09:00 to 18:00, because the research
    corpus's own worked example is "if your office hours are set to 9am - 6pm", and a
    nine-to-five would contradict the sentence the derivation is taken from.
    """

    schedule: dict[str, Any] = {}
    for day in vocab.WEEKDAYS:
        if day in vocab.DEFAULT_OFFICE_WEEKDAYS:
            schedule[day] = {
                vocab.OFFICE_OPEN: vocab.DEFAULT_OFFICE_OPEN,
                vocab.OFFICE_CLOSE: vocab.DEFAULT_OFFICE_CLOSE,
            }
        else:
            schedule[day] = {vocab.OFFICE_OPEN: None, vocab.OFFICE_CLOSE: None}
    return schedule


def normalise_office_hours(value: Any) -> dict[str, Any]:
    """A weekly schedule, checked for being usable.

    A day is either a pair of minutes or a pair of ``None``. A pair whose close is not
    after its open is refused, because a schedule that opens at 18:00 and closes at
    09:00 has no inside and would make :func:`expected_reply_time` walk forever.

    A missing day is closed rather than inherited: an absent key and an explicit
    ``None`` are the same statement about a day nobody works, and treating them
    differently would make two equal-looking schedules behave differently.
    """

    raw = value if isinstance(value, Mapping) else {}
    schedule: dict[str, Any] = {}
    for day in vocab.WEEKDAYS:
        entry = raw.get(day)
        if entry is None:
            schedule[day] = {vocab.OFFICE_OPEN: None, vocab.OFFICE_CLOSE: None}
            continue
        if isinstance(entry, Mapping):
            opens = entry.get(vocab.OFFICE_OPEN)
            closes = entry.get(vocab.OFFICE_CLOSE)
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            opens, closes = entry
        else:
            raise ChaseRefusal(
                "invalid_office_hours",
                day,
                f"{day} must be an open/close pair of minutes or null for closed",
            )
        if opens is None and closes is None:
            schedule[day] = {vocab.OFFICE_OPEN: None, vocab.OFFICE_CLOSE: None}
            continue
        if opens is None or closes is None:
            raise ChaseRefusal(
                "invalid_office_hours",
                day,
                f"{day} must set both open and close, or neither for a closed day",
            )
        opens_i = _minutes(opens, day, vocab.OFFICE_OPEN)
        closes_i = _minutes(closes, day, vocab.OFFICE_CLOSE)
        if closes_i <= opens_i:
            raise ChaseRefusal(
                "invalid_office_hours",
                day,
                f"{day} closes at or before it opens; there is no inside to schedule in",
            )
        schedule[day] = {vocab.OFFICE_OPEN: opens_i, vocab.OFFICE_CLOSE: closes_i}
    return schedule


def _minutes(value: Any, day: str, field: str) -> int:
    """Minutes past midnight, from an int, a ``"HH:MM"`` string or a datetime.

    A datetime is read as its own wall-clock time, so a schedule persisted as
    ``{"open": "09:00"}`` and one persisted as ``{"open": 540}`` are one schedule.
    """

    if isinstance(value, datetime):
        return value.hour * 60 + value.minute
    if isinstance(value, bool):
        raise ChaseRefusal("invalid_office_hours", day, f"{day}.{field} is not a time")
    if isinstance(value, (int, float)):
        minutes = int(value)
        if not 0 <= minutes <= vocab.MINUTES_PER_DAY:
            raise ChaseRefusal("invalid_office_hours", day, f"{day}.{field} is out of range")
        return minutes
    text = str(value or "").strip()
    if ":" in text:
        hours, _, rest = text.partition(":")
        try:
            hours_i = int(hours)
            minutes_i = int(rest[:2])
        except ValueError as exc:
            raise ChaseRefusal(
                "invalid_office_hours", day, f"{day}.{field} is not a HH:MM time"
            ) from exc
        total = hours_i * 60 + minutes_i
        if not 0 <= total <= vocab.MINUTES_PER_DAY:
            raise ChaseRefusal("invalid_office_hours", day, f"{day}.{field} is out of range")
        return total
    raise ChaseRefusal("invalid_office_hours", day, f"{day}.{field} is not a time")


def _day_window(schedule: Mapping[str, Any], day: str) -> tuple[int, int] | None:
    entry = schedule.get(day) or {}
    opens = entry.get(vocab.OFFICE_OPEN)
    closes = entry.get(vocab.OFFICE_CLOSE)
    if opens is None or closes is None:
        return None
    return int(opens), int(closes)


def office_minutes_between(
    start: datetime, duration_seconds: int, schedule: Mapping[str, Any]
) -> int:
    """How many minutes of a calendar span fall inside the office's opening hours.

    "Office hours" only means something against a schedule. Of the sixty minutes between
    17:50 and 18:50 on a weekday, ten are inside a 09:00 to 18:00 office and fifty are not,
    so this returns ten rather than sixty.

    The span is **calendar-bounded**: it measures the window from ``start`` to
    ``start + duration_seconds`` and counts the open minutes inside it. It never walks
    further than that, so a three-day span is three days of calendar time however little
    of it was worked.

    That is a different question from the one :func:`expected_reply_time` asks. There, a
    fifteen-minute response target means fifteen minutes of *working* time, so the walk
    continues past the close until the budget is spent. Here the span is fixed by the
    caller and the answer is a fraction of it. Two functions rather than one with a flag,
    because confusing the two produces an answer that is confidently wrong: a three-day
    span reported as three days of office time when only one and a half were worked.

    A caller that wants elapsed rather than office minutes asks :func:`elapsed_seconds`.
    """

    end = start + timedelta(seconds=max(0, duration_seconds))
    counted = 0.0
    cursor = start

    for _ in range(vocab.MAX_WALK_DAYS):
        if cursor >= end:
            break

        window = _day_window(schedule, _weekday(cursor))
        if window is None:
            cursor = _start_of_next_day(cursor)
            continue

        opens, closes = window
        minutes_now = cursor.hour * 60 + cursor.minute

        # The part of today's window that is still ahead of the cursor.
        window_start = _at_minutes(cursor, max(minutes_now, opens))
        window_end = _at_minutes(cursor, closes)
        overlap_start = max(window_start, cursor)
        overlap_end = min(window_end, end)
        if overlap_end > overlap_start:
            counted += (overlap_end - overlap_start).total_seconds()

        cursor = _start_of_next_day(cursor)

    return int(counted // vocab.SECONDS_PER_MINUTE)


def expected_reply_time(
    anchor: datetime, duration_seconds: int, schedule: Mapping[str, Any]
) -> datetime:
    """When a reply is expected, accounting for office hours.

    "Show expected reply time ... uses office hours". The target is the duration added
    to the anchor, walked forward until it lands inside the schedule.

    The walk is a loop over opening windows rather than one arithmetic step, because a
    schedule can close a day, close a week, or close everything, and the answer in each
    case is "the next open minute, then however much duration is left". A closed-form
    calculation would have to guess all three.

    Worked cases, each a test: a weekday 17:50 anchor plus 15 minutes reads 09:05 the
    next working day, because ten of those minutes pass before the close and only five
    are left over; a Saturday anchor reads 09:15 Monday, because no time at all passes on
    Saturday; an anchor whose target already falls inside the window is unchanged.

    A schedule with no open day at all returns the plain anchor-plus-duration. There is
    no instant inside such a schedule, so any other answer would be a fiction.
    """

    remaining = max(0.0, float(duration_seconds))
    cursor = anchor

    # Bounded by the researched maximum duration expressed in days, plus a margin for
    # landing on an open minute. A schedule with no open day at all therefore terminates
    # and returns the raw target rather than looping.
    for _ in range(vocab.MAX_WALK_DAYS):
        if remaining <= 0:
            return cursor

        window = _day_window(schedule, _weekday(cursor))
        if window is None:
            cursor = _start_of_next_day(cursor)
            continue

        opens, closes = window
        minutes_now = cursor.hour * 60 + cursor.minute

        if minutes_now < opens:
            # Before opening today. Moving to the open minute consumes no duration, so
            # what is left to spend is unchanged.
            cursor = _at_minutes(cursor, opens)
            continue

        if minutes_now >= closes:
            cursor = _start_of_next_day(cursor)
            continue

        # Inside today's opening window: as much of what remains as fits before the close
        # is spent here, and the rest waits for the next open minute.
        available = (closes - minutes_now) * vocab.SECONDS_PER_MINUTE
        if remaining <= available:
            return cursor + timedelta(seconds=remaining)

        remaining -= available
        cursor = _start_of_next_day(cursor)

    return anchor + timedelta(seconds=duration_seconds)


def _at_minutes(moment: datetime, minutes: int) -> datetime:
    base = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(minutes=minutes)


def _start_of_next_day(moment: datetime) -> datetime:
    base = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(days=1)


def _weekday(moment: datetime) -> str:
    return vocab.WEEKDAYS[moment.weekday()]
