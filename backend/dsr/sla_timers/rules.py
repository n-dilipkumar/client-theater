"""WF-108: the SLA timer rules, as pure functions over values.

Every rule here is a statement about an instant, a duration, a schedule or a list of
message parts. None of them reads or writes anything. The engine beside this module
turns them into records, and this module is what a test can call with a clock and a
list of parts and check.

The rules the workflow leans on
-------------------------------

**The clock counts office hours, not calendar time.** "if your office hours are set to
9am - 6pm, and your SLA first response time is 15 minutes, a message received at 5:50pm
will have an expected response time of 9:05am on the next working day."
:func:`working_seconds` counts only the seconds inside the schedule, and
:func:`chase_expected_reply_time` (WF-107's) walks forward to the office-hours deadline.

**The clock pauses, and the pauses are the policy's.** "pause the timer when the
conversation is `snoozed` or `waiting on customer`". :func:`pause_intervals` builds the
pause set from the conversation's snooze window and from the parts that leave the ball
in the customer's court, and :func:`working_seconds` subtracts it. A pause with no end
is an open pause: the clock is stopped now.

**Snoozing an already-breached timer does not hide the breach.** The pause rule and the
auto-unsnooze rule are in tension: if the timer pauses while snoozed it cannot be missed
while snoozed, yet the spec says a miss while snoozed reopens the conversation.
:func:`is_reopening_breach` resolves it: the pause applies to a timer that is not
already overdue, and a timer that is overdue when the conversation is snoozed is
reopened instead. The derivation is recorded in the inferences register.

**The badge has three values and no fourth.** "Red: Overdue. Orange: Less than 5
minutes left on the timer. Grey: More than 5 minutes left on the timer."
:func:`badge_for` returns exactly those.

**"fixed" is a recovered breach.** "aggregate hit/miss/fixed rates". A metric that met
its target is a hit. A metric that breached and was not reopened is a miss. A metric
that breached, was reopened by the auto-unsnooze rule, and then completed is fixed.
:func:`classify_outcome` is the only place that decides.

What this module does not decide
--------------------------------

Whether a message actually left this product, and whether a conversation should be
swept at all. Delivery and eligibility are the engine's.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from dsr.conversation_chase import rules as chase_rules, vocabulary as chase_vocab
from dsr.sla_timers import vocabulary as vocab

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# Declared here and raised by nothing else in the product, which is what makes it safe
# for the feature module to map them. A handler for a shared type such as ValueError
# would intercept that exception across the whole application.


class SlaRefusal(ValueError):
    """A target, metric, pause rule or state the workflow will not accept.

    Carries a published code from :data:`vocabulary.ERROR_CODES` and a field-keyed map,
    because the page puts each message beside the input that caused it.
    """

    def __init__(self, code: str, field: str | None = None, detail: str | None = None) -> None:
        message = detail or vocab.error_sentence(code)
        super().__init__(message)
        self.code = code
        self.status = vocab.error_status(code)
        self.detail = message
        self.errors: dict[str, str] = {field: message} if field else {"detail": message}


class PolicyNotFound(LookupError):
    """No such SLA policy. Its own type so the feature maps only what it raises."""


class TimerNotFound(LookupError):
    """No such SLA timer. Same reasoning as the class above."""


class ConversationNotFound(LookupError):
    """No such conversation. Same reasoning as the class above."""


# --------------------------------------------------------------------------- #
# Instants
# --------------------------------------------------------------------------- #
#
# WF-107's instant helpers are reused rather than reimplemented: one parser for every
# ISO 8601 instant in the product, so a stored value reads the same in both workflows.

coerce_instant = chase_rules.coerce_instant
stamp = chase_rules.stamp
elapsed_seconds = chase_rules.elapsed_seconds

#: How many days a walk will look forward before giving up. The office-hours rule is
#: WF-107's and carries the same bound, so the two workflows agree on how far a walk
#: may go before it is treated as "there is no open minute".
MAX_WALK_DAYS = chase_vocab.MAX_WALK_DAYS


# --------------------------------------------------------------------------- #
# Validators
# --------------------------------------------------------------------------- #


def require_metric(value: Any) -> str:
    text = vocab._slug(value)
    if text not in vocab.METRIC_KINDS:
        raise SlaRefusal(
            "unknown_metric",
            "metric",
            f"unknown metric {value!r}; the policy offers {', '.join(vocab.METRIC_KINDS)}",
        )
    return text


def require_object_kind(value: Any) -> str:
    try:
        return vocab.require_object_kind(value)
    except ValueError as exc:
        raise SlaRefusal("unknown_object_kind", "object_kind", str(exc)) from exc


def require_pause_reason(value: Any) -> str:
    try:
        return vocab.require_pause_reason(value)
    except ValueError as exc:
        raise SlaRefusal("unknown_pause_reason", "pause_reasons", str(exc)) from exc


def require_pause_reasons(value: Any) -> list[str]:
    """The pause rules a policy carries.

    ``None`` takes the researched default (both on); an empty list is a legitimate
    setting that turns both off, because the flow says the seller toggles them.
    """

    if value is None:
        return list(vocab.DEFAULT_PAUSE_REASONS)
    raw = [value] if isinstance(value, str) else list(value)
    chosen = {require_pause_reason(entry) for entry in raw}
    return [reason for reason in vocab.PAUSE_REASONS if reason in chosen]


def require_state(value: Any) -> str:
    try:
        return chase_vocab.require_state(value)
    except ValueError as exc:
        raise SlaRefusal("unknown_conversation_state", "state", str(exc)) from exc


def require_target(value: Any, metric: str) -> int:
    """A target in whole seconds, inside the permitted bounds."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SlaRefusal("target_not_a_number", metric, f"{metric} must be a number of seconds.")
    seconds = int(value)
    if seconds < vocab.MIN_TARGET_SECONDS:
        raise SlaRefusal(
            "target_out_of_range",
            metric,
            f"{metric} must be at least {vocab.MIN_TARGET_SECONDS} seconds; {seconds} is not.",
        )
    if seconds > vocab.MAX_TARGET_SECONDS:
        raise SlaRefusal(
            "target_out_of_range",
            metric,
            f"{metric} must be at most 30 days; {seconds} seconds is not.",
        )
    return seconds


def normalise_targets(value: Any) -> dict[str, int]:
    """The four targets, filled from the derived defaults.

    A caller who sends ``{}`` is refused: an SLA policy with no target of its own is not
    a policy. A caller who sends nothing gets the derived defaults, because the flow's
    step 1 sets targets after naming the policy and a draft still has to be readable.
    """

    if value is None:
        return vocab.default_targets()
    if not isinstance(value, Mapping):
        raise SlaRefusal("policy_needs_targets", "targets", "targets must be an object of metrics.")
    if not value:
        raise SlaRefusal(
            "policy_needs_targets", "targets", "an SLA policy needs at least one target."
        )
    targets = vocab.default_targets()
    for key, raw in value.items():
        metric = require_metric(key)
        targets[metric] = require_target(raw, metric)
    return targets


def _clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    raw = [value] if isinstance(value, str) else list(value)
    out: list[str] = []
    for entry in raw:
        text = " ".join(str(entry).split())
        if text and text not in out:
            out.append(text)
    return out


def normalise_policy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """One SLA policy, validated and reduced to the fields the rules read."""

    body = dict(payload or {})
    name = " ".join(str(body.get("name") or "").split())
    if not name:
        raise SlaRefusal("policy_needs_a_name", "name", "an SLA policy needs a name.")
    workflow_name = " ".join(str(body.get("workflow_name") or "").split()) or None
    return {
        "name": name,
        "description": str(body.get("description") or "").strip() or None,
        "targets": normalise_targets(body.get("targets")),
        "pause_reasons": require_pause_reasons(body.get("pause_reasons")),
        "object_kind": require_object_kind(body.get("object_kind")),
        "teams": _clean_list(body.get("teams")),
        "workflow_name": workflow_name,
        "live": bool(body.get("live")),
    }


def normalise_schedule(value: Any) -> dict[str, Any]:
    """A weekly office-hours schedule, refusing anything unusable.

    WF-107's normaliser does the work; this wrapper only translates its refusal into
    this feature's own error type, so the feature's one handler catches it.
    """

    try:
        return chase_rules.normalise_office_hours(value)
    except chase_rules.ChaseRefusal as exc:
        raise SlaRefusal("invalid_office_hours", "schedule", exc.detail) from exc


def default_schedule() -> dict[str, Any]:
    """The derived default, which is WF-107's and therefore the room's only default."""
    return chase_rules.default_office_hours()


def chase_expected_reply_time(anchor: datetime, duration_seconds: int, schedule: Mapping[str, Any]):
    """WF-107's office-hours walk, reused so both workflows answer the same instant."""
    return chase_rules.expected_reply_time(anchor, duration_seconds, schedule)


# --------------------------------------------------------------------------- #
# Office hours
# --------------------------------------------------------------------------- #


def day_window(schedule: Mapping[str, Any], moment: datetime) -> tuple[int, int] | None:
    """The open/close pair for ``moment``'s weekday, or ``None`` when it is closed."""

    entry = schedule.get(chase_vocab.WEEKDAYS[moment.weekday()]) or {}
    opens = entry.get(chase_vocab.OFFICE_OPEN)
    closes = entry.get(chase_vocab.OFFICE_CLOSE)
    if opens is None or closes is None:
        return None
    return int(opens), int(closes)


def is_open(moment: datetime, schedule: Mapping[str, Any]) -> bool:
    window = day_window(schedule, moment)
    if window is None:
        return False
    opens, closes = window
    minutes = moment.hour * 60 + moment.minute
    return opens <= minutes < closes


def _day_start(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


# --------------------------------------------------------------------------- #
# Pause intervals
# --------------------------------------------------------------------------- #


def merge_intervals(intervals: Sequence[tuple[datetime, datetime | None]]) -> list[tuple]:
    """Sort and coalesce overlapping intervals. An open interval ends the merge."""

    cleaned = [(start, end) for start, end in intervals if start is not None]
    if not cleaned:
        return []
    cleaned.sort(key=lambda pair: pair[0])
    merged: list[tuple[datetime, datetime | None]] = [cleaned[0]]
    for start, end in cleaned[1:]:
        last_start, last_end = merged[-1]
        if last_end is None:
            continue  # an open interval already swallows everything after it
        if start <= last_end:
            merged[-1] = (last_start, None if end is None else max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def clip_intervals(
    intervals: Sequence[tuple[datetime, datetime | None]], start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    """The closed intervals of ``intervals`` that fall inside ``[start, end)``."""

    out: list[tuple[datetime, datetime]] = []
    for open_at, close_at in intervals:
        if open_at is None:
            continue
        lo = max(open_at, start)
        hi = end if close_at is None else min(close_at, end)
        if hi > lo:
            out.append((lo, hi))
    return merge_intervals(out)  # type: ignore[return-value]


def paused_now(intervals: Sequence[tuple[datetime, datetime | None]], now: datetime) -> bool:
    """Is the clock stopped at ``now`` by any pause interval?"""

    for open_at, close_at in intervals:
        if open_at is None or open_at > now:
            continue
        if close_at is None or now < close_at:
            return True
    return False


def snooze_interval(
    conversation: Mapping[str, Any],
) -> tuple[datetime, datetime | None] | None:
    """The window a conversation was snoozed for, or ``None``.

    WF-107 stores ``snoozed_at`` and ``unsnoozed_at`` on the conversation. A snoozed
    conversation is open-ended: the window closes when a message reopens it, or not at
    all. Only the most recent snooze cycle is recorded, which is the cycle the timer is
    currently inside.
    """

    snoozed_at = coerce_instant(conversation.get("snoozed_at"))
    if snoozed_at is None:
        return None
    if conversation.get("state") == chase_vocab.STATE_SNOOZED:
        return (snoozed_at, None)
    unsnoozed_at = coerce_instant(conversation.get("unsnoozed_at"))
    if unsnoozed_at is not None and unsnoozed_at >= snoozed_at:
        return (snoozed_at, unsnoozed_at)
    return None


def ordered_parts(parts: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The parts oldest-first, using WF-107's one ordering rule."""

    return chase_rules.parts_of(parts)


def waiting_intervals(
    parts: Sequence[Mapping[str, Any]],
) -> tuple[list[tuple[datetime, datetime]], datetime | None]:
    """The windows the ball was in the customer's court.

    A window opens at a teammate message and closes at the next customer message. The
    window that is still open is returned separately, because a caller needs to know the
    clock is stopped now rather than at some past instant.
    """

    open_at: datetime | None = None
    closed: list[tuple[datetime, datetime]] = []
    for part in ordered_parts(parts):
        at = coerce_instant(part.get("at"))
        if at is None:
            continue
        author = part.get("author_kind")
        if author == chase_vocab.AUTHOR_TEAMMATE:
            if open_at is None:
                open_at = at
        elif author == chase_vocab.AUTHOR_CUSTOMER and open_at is not None:
            closed.append((open_at, at))
            open_at = None
    return closed, open_at


def pause_intervals(
    policy: Mapping[str, Any],
    conversation: Mapping[str, Any],
    parts: Sequence[Mapping[str, Any]],
) -> list[tuple[datetime, datetime | None]]:
    """Every window the policy's pause rules stop the clock for.

    Returned unclipped, so a caller can tell an open pause (the clock is stopped now)
    from a closed one.
    """

    reasons = policy.get("pause_reasons") or []
    intervals: list[tuple[datetime, datetime | None]] = []
    if vocab.PAUSE_SNOOZED in reasons:
        window = snooze_interval(conversation)
        if window is not None:
            intervals.append(window)
    if vocab.PAUSE_WAITING_ON_CUSTOMER in reasons:
        closed, open_since = waiting_intervals(parts)
        intervals.extend(closed)
        if open_since is not None:
            intervals.append((open_since, None))
    return merge_intervals(intervals)


# --------------------------------------------------------------------------- #
# Working time
# --------------------------------------------------------------------------- #


def _overlap(
    intervals: Sequence[tuple[datetime, datetime]], start: datetime, end: datetime
) -> float:
    total = 0.0
    for open_at, close_at in intervals:
        lo = max(open_at, start)
        hi = min(close_at, end)
        if hi > lo:
            total += (hi - lo).total_seconds()
    return total


def working_seconds(
    start: datetime,
    end: datetime,
    schedule: Mapping[str, Any],
    pauses: Sequence[tuple[datetime, datetime]] | None = None,
) -> float:
    """Seconds of ``[start, end)`` inside office hours and not inside a pause.

    This is the quantity an SLA counts. Of the sixty minutes between 17:50 and 18:50 on
    a weekday, ten are inside a 09:00 to 18:00 office, so ten minutes of a fifteen-minute
    first-reply target are spent by 18:50. The span is bounded: it never walks past
    ``end``. A pause that overlaps the span is subtracted, so a conversation snoozed for
    the whole of it spends nothing.
    """

    if end <= start:
        return 0.0
    pause_list = merge_intervals(pauses or [])  # type: ignore[arg-type]
    total = 0.0
    day = _day_start(start)
    for _ in range(MAX_WALK_DAYS + 1):
        if day >= end:
            break
        window = day_window(schedule, day)
        if window is not None:
            opens, closes = window
            w_start = max(day + timedelta(minutes=opens), start)
            w_end = min(day + timedelta(minutes=closes), end)
            if w_end > w_start:
                span = (w_end - w_start).total_seconds()
                total += max(0.0, span - _overlap(pause_list, w_start, w_end))
        day = day + timedelta(days=1)
    return total


# --------------------------------------------------------------------------- #
# Anchors and completions
# --------------------------------------------------------------------------- #


def first_customer_at(parts: Sequence[Mapping[str, Any]]) -> datetime | None:
    for part in ordered_parts(parts):
        if part.get("author_kind") == chase_vocab.AUTHOR_CUSTOMER:
            return coerce_instant(part.get("at"))
    return None


def last_customer_at(parts: Sequence[Mapping[str, Any]]) -> datetime | None:
    found: datetime | None = None
    for part in ordered_parts(parts):
        if part.get("author_kind") == chase_vocab.AUTHOR_CUSTOMER:
            found = coerce_instant(part.get("at"))
    return found


def first_teammate_after(parts: Sequence[Mapping[str, Any]], moment: datetime) -> datetime | None:
    for part in ordered_parts(parts):
        at = coerce_instant(part.get("at"))
        if at is None or at < moment:
            continue
        if part.get("author_kind") == chase_vocab.AUTHOR_TEAMMATE:
            return at
    return None


def metric_inputs(
    metric: str,
    conversation: Mapping[str, Any],
    parts: Sequence[Mapping[str, Any]],
    applied_at: datetime,
) -> tuple[datetime | None, datetime | None]:
    """The instant a metric measures from, and the instant it was met.

    FRT measures from the customer's first message to the teammate's first reply. NRT
    measures from the customer's last message to the teammate's next reply. Both the
    terminal metrics measure from the moment the SLA was applied to the conversation's
    close, because the room product has one terminal state and two names for the metric
    that reaches it.
    """

    kind = require_metric(metric)
    if kind == vocab.METRIC_FRT:
        anchor = first_customer_at(parts)
        return anchor, first_teammate_after(parts, anchor) if anchor else None
    if kind == vocab.METRIC_NRT:
        anchor = last_customer_at(parts)
        return anchor, first_teammate_after(parts, anchor) if anchor else None
    if kind in vocab.TERMINAL_METRICS:
        return applied_at, coerce_instant(conversation.get("closed_at"))
    return None, None


# --------------------------------------------------------------------------- #
# One metric's progress
# --------------------------------------------------------------------------- #


def _metric(
    metric: str,
    target_seconds: int,
    state: str,
    *,
    anchor: datetime | None = None,
    completion: datetime | None = None,
    elapsed: float | None = None,
    deadline: datetime | None = None,
) -> dict[str, Any]:
    remaining = None if elapsed is None else max(0.0, target_seconds - elapsed)
    overdue = None if elapsed is None else max(0.0, elapsed - target_seconds)
    return {
        "metric": metric,
        "label": vocab.METRIC_LABELS[metric],
        "short": vocab.METRIC_SHORT[metric],
        "target_seconds": target_seconds,
        "state": state,
        "state_label": vocab.METRIC_STATE_LABELS[state],
        "anchor": stamp(anchor) if anchor else None,
        "completion": stamp(completion) if completion else None,
        "deadline": stamp(deadline) if deadline else None,
        "elapsed_seconds": elapsed,
        "remaining_seconds": remaining,
        "overdue_seconds": overdue,
    }


def metric_progress(
    metric: str,
    anchor: datetime | None,
    completion: datetime | None,
    target_seconds: int,
    schedule: Mapping[str, Any],
    pauses: Sequence[tuple[datetime, datetime | None]],
    now: datetime,
) -> dict[str, Any]:
    """One metric's state, worked seconds, remaining seconds and deadline.

    Completed first: a metric whose milestone has happened is met or breached and cannot
    change. A metric with no anchor is not applicable, because there is no instant to
    measure from. A metric whose clock is stopped is paused, and its deadline is ``None``
    rather than a guess, because when the pause ends is not knowable from here.
    """

    if anchor is None:
        return _metric(metric, target_seconds, vocab.METRIC_NOT_APPLICABLE)

    clipped = clip_intervals(pauses, anchor, now)

    if completion is not None and completion <= now:
        worked = working_seconds(anchor, completion, schedule, clipped)
        state = vocab.METRIC_BREACHED if worked > target_seconds else vocab.METRIC_MET
        return _metric(
            metric,
            target_seconds,
            state,
            anchor=anchor,
            completion=completion,
            elapsed=worked,
            deadline=completion,
        )

    worked = working_seconds(anchor, now, schedule, clipped)
    # A breach is checked before the pause: the pause rule applies to a timer that is
    # not already overdue, so a stopped clock does not hide a breach that already
    # happened. This is the order the auto-unsnooze reconciliation depends on.
    if worked >= target_seconds:
        return _metric(
            metric,
            target_seconds,
            vocab.METRIC_BREACHED,
            anchor=anchor,
            elapsed=worked,
            deadline=now,
        )
    if paused_now(pauses, now) and anchor <= now:
        return _metric(metric, target_seconds, vocab.METRIC_PAUSED, anchor=anchor, elapsed=worked)
    remaining = target_seconds - worked
    deadline = chase_expected_reply_time(now, int(remaining), schedule)
    return _metric(
        metric,
        target_seconds,
        vocab.METRIC_PENDING,
        anchor=anchor,
        elapsed=worked,
        deadline=deadline,
    )


def progress_for_policy(
    policy: Mapping[str, Any],
    conversation: Mapping[str, Any],
    parts: Sequence[Mapping[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    """Every metric the policy's object kind earns, in the order the flow names them."""

    applied_at = coerce_instant(policy.get("applied_at"))
    if applied_at is None:
        applied_at = now
    schedule = policy.get("schedule") or default_schedule()
    pauses = pause_intervals(policy, conversation, parts)
    kind = require_object_kind(policy.get("object_kind"))
    targets = policy.get("targets") or vocab.default_targets()
    out: list[dict[str, Any]] = []
    for metric in vocab.metrics_for_kind(kind):
        anchor, completion = metric_inputs(metric, conversation, parts, applied_at)
        out.append(
            metric_progress(
                metric,
                anchor,
                completion,
                int(targets.get(metric, vocab.DEFAULT_TARGETS[metric])),
                schedule,
                pauses,
                now,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Badge, timer state and outcome
# --------------------------------------------------------------------------- #


def badge_for(remaining_seconds: float | None, breached: bool = False) -> str:
    """The three the spec names: grey, orange under five minutes, red overdue."""

    if breached or remaining_seconds is None or remaining_seconds <= 0:
        return vocab.BADGE_RED
    if remaining_seconds < vocab.BADGE_ORANGE_SECONDS:
        return vocab.BADGE_ORANGE
    return vocab.BADGE_GREY


def nearest_target(metrics: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The metric an inbox badge should show.

    A breach wins, and the largest overdue breach wins among breaches. Otherwise the
    running or paused metric with the least time left wins, so "Next SLA target" sorts
    by the number this returns.
    """

    breached = [
        entry
        for entry in metrics
        if entry["state"] == vocab.METRIC_BREACHED and entry.get("overdue_seconds") is not None
    ]
    if breached:
        chosen = max(breached, key=lambda entry: entry["overdue_seconds"])
        return {
            "metric": chosen["metric"],
            "short": chosen["short"],
            "breached": True,
            "seconds": chosen["overdue_seconds"],
            "deadline": chosen.get("deadline"),
            "badge": vocab.BADGE_RED,
            "paused": False,
        }
    live = [
        entry
        for entry in metrics
        if entry["state"] in (vocab.METRIC_PENDING, vocab.METRIC_PAUSED)
        and entry.get("remaining_seconds") is not None
    ]
    if not live:
        return None
    chosen = min(live, key=lambda entry: entry["remaining_seconds"])
    return {
        "metric": chosen["metric"],
        "short": chosen["short"],
        "breached": False,
        "seconds": chosen["remaining_seconds"],
        "deadline": chosen.get("deadline"),
        "badge": badge_for(chosen["remaining_seconds"]),
        "paused": chosen["state"] == vocab.METRIC_PAUSED,
    }


def timer_state(metrics: Sequence[Mapping[str, Any]]) -> str:
    """The one word for a whole timer.

    A breach outranks everything. A timer with nothing breached but a stopped clock is
    paused. A timer whose every applicable metric is met is met. Otherwise it runs.
    """

    applicable = [entry for entry in metrics if entry["state"] != vocab.METRIC_NOT_APPLICABLE]
    if any(entry["state"] == vocab.METRIC_BREACHED for entry in applicable):
        return vocab.TIMER_BREACHED
    if any(entry["state"] == vocab.METRIC_PAUSED for entry in applicable):
        return vocab.TIMER_PAUSED
    if applicable and all(entry["state"] == vocab.METRIC_MET for entry in applicable):
        return vocab.TIMER_MET
    return vocab.TIMER_RUNNING


def is_reopening_breach(
    metrics: Sequence[Mapping[str, Any]], conversation: Mapping[str, Any]
) -> bool:
    """Should this snoozed, already-breached timer reopen its conversation?

    The reconciliation of the two rules the spec states. The timer pauses while snoozed,
    so it cannot *become* breached inside a pause. A timer that is already overdue when
    the conversation is snoozed, though, is the case the auto-unsnooze sentence
    describes: leaving it paused would hide a breach that already happened.
    """

    if conversation.get("state") != chase_vocab.STATE_SNOOZED:
        return False
    return any(entry["state"] == vocab.METRIC_BREACHED for entry in metrics)


def classify_outcome(metric_entry: Mapping[str, Any], reopened_after_breach: bool) -> str | None:
    """``hit``, ``miss``, ``fixed``, or ``None`` while the metric is undecided.

    A hit met its target. A miss breached and stayed breached. A fixed breached, was
    reopened by the auto-unsnooze rule, and then completed. A pending or paused metric
    has no outcome yet and is not counted in the rates.
    """

    state = metric_entry.get("state")
    if state == vocab.METRIC_MET:
        return vocab.OUTCOME_HIT
    if state == vocab.METRIC_BREACHED:
        return vocab.OUTCOME_FIXED if reopened_after_breach else vocab.OUTCOME_MISS
    return None
