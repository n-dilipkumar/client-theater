"""WF-109: the SLA-breach escalation rules, as pure functions over values.

Every rule here is a statement about a workflow, an instant, a duration or a list of
message parts. None of them reads or writes anything. The engine beside this module turns
them into records, and this module is what a test can call with a clock and a timer view
and check.

The rules the workflow leans on
-------------------------------

**A pre-warning fires a chosen lead time before the target is due.**
"Before SLA breach - runs a chosen amount of time before an SLA target is due to breach."
:func:`decide` fires the before-breach workflow once the watched target's remaining time
drops to the lead time or below.

**The lead time must be shorter than the target.** "For the Before SLA breach trigger, the
lead time must be shorter than the SLA target's duration, or the Workflow won't run."
:func:`require_lead_time` refuses the workflow where the target is knowable; :func:`decide`
skips the run, with the published code, where the target is only knowable per conversation.

**Three conditions suppress a run.** "If a teammate replies, snoozes, or closes the
conversation before the trigger point, the Workflow doesn't run."
:func:`cancel_reason` names all three.

**The check is approximate.** "Intercom checks SLAs about once a minute, so a Workflow
runs within roughly a minute of the target time, rather than to the exact second." A
before-breach window the sweep missed is recorded as :data:`vocabulary.SKIP_HEAD_START_MISSED`
rather than fired late, because a warning after the breach is not a warning.

**The alert is a Data Connector execution.** The spec reports it on
``data_connector.execution.completed`` with ``source_type``, ``success``, ``http_status``,
``error_type`` and ``execution_time_ms``. :func:`alert_outcome` builds exactly that shape.

What this module does not decide
--------------------------------

Whether a message actually left this product, and whether a room should be swept at all.
Delivery and eligibility are the engine's.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from dsr.conversation_chase import rules as chase_rules, vocabulary as chase_vocab
from dsr.sla_escalation import vocabulary as vocab
from dsr.sla_timers import vocabulary as sla_vocab

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# Declared here and raised by nothing else in the product, which is what makes it safe for
# the feature module to map them. A handler for a shared type such as ValueError would
# intercept that exception across the whole application.


class EscalationRefusal(ValueError):
    """A workflow, trigger, metric, action or lead time the rules will not accept.

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


class WorkflowNotFound(LookupError):
    """No such escalation workflow. Its own type so the feature maps only what it raises."""


class ConversationNotFound(LookupError):
    """No such conversation. Same reasoning as the class above."""


class RunNotFound(LookupError):
    """No such escalation run. Same reasoning as the class above."""


# --------------------------------------------------------------------------- #
# Instants
# --------------------------------------------------------------------------- #
#
# WF-107's instant helpers are reused rather than reimplemented: one parser for every
# ISO 8601 instant in the product, so a stored value reads the same everywhere.

coerce_instant = chase_rules.coerce_instant
stamp = chase_rules.stamp
utcnow = chase_rules.utcnow


# --------------------------------------------------------------------------- #
# Validators
# --------------------------------------------------------------------------- #


def require_trigger_kind(value: Any) -> str:
    try:
        return vocab.require_trigger_kind(value)
    except ValueError as exc:
        raise EscalationRefusal("unknown_trigger_kind", "trigger", str(exc)) from exc


def require_metric(value: Any) -> str:
    """The SLA target to watch. WF-108's four metric ids, with the spec's four names."""

    text = vocab._slug(value) or vocab.DEFAULT_METRIC
    if text not in vocab.METRIC_KINDS:
        raise EscalationRefusal(
            "unknown_metric",
            "metric",
            f"unknown SLA target {value!r}; it is one of {', '.join(vocab.METRIC_KINDS)}",
        )
    return text


def require_action_kind(value: Any) -> str:
    try:
        return vocab.require_action_kind(value)
    except ValueError as exc:
        raise EscalationRefusal("unknown_action_kind", "kind", str(exc)) from exc


def require_notify_via(value: Any) -> str:
    try:
        return vocab.require_notify_via(value)
    except ValueError as exc:
        raise EscalationRefusal("unknown_notify_channel", "via", str(exc)) from exc


def require_state(value: Any) -> str:
    try:
        return chase_vocab.require_state(value)
    except ValueError as exc:
        raise EscalationRefusal("unknown_conversation_state", "state", str(exc)) from exc


def require_lead_seconds(value: Any) -> int:
    """A lead time in whole seconds, at or above the floor."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EscalationRefusal(
            "lead_not_a_number", "lead_seconds", "the lead time must be seconds."
        )
    seconds = int(value)
    if seconds < vocab.MIN_LEAD_SECONDS:
        raise EscalationRefusal(
            "lead_out_of_range",
            "lead_seconds",
            f"the lead time must be at least {vocab.MIN_LEAD_SECONDS} seconds; {seconds} is not.",
        )
    if seconds > sla_vocab.MAX_TARGET_SECONDS:
        raise EscalationRefusal(
            "lead_out_of_range",
            "lead_seconds",
            "the lead time must be at most 30 days.",
        )
    return seconds


def require_lead_shorter(lead_seconds: int, target_seconds: int, metric: str) -> int:
    """Refuse a lead time that is not shorter than the target it watches.

    "the lead time must be shorter than the SLA target's duration, or the Workflow won't
    run." This is the refusal path, used where the target is knowable: a workflow that
    names its SLA policy is checked against that policy's target for the watched metric.
    """

    if not vocab.lead_is_shorter(lead_seconds, target_seconds):
        label = vocab.METRIC_SPEC_LABELS.get(metric, metric)
        raise EscalationRefusal(
            "lead_not_shorter_than_target",
            "lead_seconds",
            f"the lead time ({lead_seconds}s) must be shorter than the {label} target "
            f"({target_seconds}s).",
        )
    return int(lead_seconds)


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def normalise_action(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """One action, validated and reduced to the fields the engine reads."""

    body = dict(payload or {})
    kind = require_action_kind(body.get("kind") or body.get("action"))
    action: dict[str, Any] = {"kind": kind}

    if kind == vocab.ACTION_ASSIGN:
        assignee = _text(body.get("assignee") or body.get("to"))
        team = _text(body.get("team"))
        if not assignee and not team:
            raise EscalationRefusal(
                "action_needs_a_target",
                "assignee",
                "an assign action needs an assignee or a team.",
            )
        action["assignee"] = assignee or None
        action["team"] = team or None

    elif kind == vocab.ACTION_NOTIFY:
        action["via"] = require_notify_via(body.get("via"))
        channel = _text(body.get("channel") or body.get("to"))
        if not channel:
            raise EscalationRefusal(
                "action_needs_a_target",
                "channel",
                "a notify action needs a teammate or a channel.",
            )
        action["channel"] = channel
        action["message"] = _text(body.get("message")) or None

    elif kind == vocab.ACTION_TAG:
        tag = _text(body.get("tag") or body.get("value")).lower() or vocab.DEFAULT_ESCALATION_TAG
        action["tag"] = tag

    elif kind == vocab.ACTION_NOTE:
        note = _text(body.get("body") or body.get("note"))
        if not note:
            raise EscalationRefusal(
                "action_needs_a_target",
                "body",
                "a note action needs the text of the note.",
            )
        action["body"] = note

    elif kind == vocab.ACTION_ALERT:
        action["connector_id"] = _text(body.get("connector_id")) or None
        action["url"] = _text(body.get("url")) or None
        action["method"] = (_text(body.get("method")) or "POST").upper()
        action["channel"] = _text(body.get("channel")) or None
        # A demo and a test may state the outcome they want to exercise. The default is
        # derived from the action's own configuration, in :func:`alert_outcome`.
        simulate = body.get("simulate_http_status")
        action["simulate_http_status"] = int(simulate) if simulate is not None else None
        action["simulate_execution_ms"] = int(body.get("simulate_execution_ms") or 0)

    return action


def normalise_actions(value: Any) -> list[dict[str, Any]]:
    """The action list a workflow carries. A workflow with no action is refused.

    The flow's step 5 is "Add actions", so an escalation workflow with no action cannot
    do the thing the flow names. An empty list is refused rather than silently accepted.
    """

    if value is None:
        raise EscalationRefusal(
            "workflow_needs_actions", "actions", "an escalation workflow needs at least one action."
        )
    raw = [value] if isinstance(value, Mapping) else list(value)
    if not raw:
        raise EscalationRefusal(
            "workflow_needs_actions", "actions", "an escalation workflow needs at least one action."
        )
    return [normalise_action(entry) for entry in raw]


def normalise_workflow(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """One escalation workflow, validated and reduced to the fields the rules read."""

    body = dict(payload or {})
    name = _text(body.get("name"))
    if not name:
        raise EscalationRefusal(
            "workflow_needs_a_name", "name", "an escalation workflow needs a name."
        )

    trigger = require_trigger_kind(body.get("trigger") or body.get("trigger_kind"))
    metric = require_metric(body.get("metric"))

    lead_seconds = None
    if trigger in vocab.LEAD_TRIGGERS:
        lead_seconds = require_lead_seconds(body.get("lead_seconds", vocab.DEFAULT_LEAD_SECONDS))
    else:
        # An on-breach workflow carries no lead time. A caller that sends one is not
        # refused; the field is simply not part of this trigger, and it is dropped so the
        # record cannot claim a lead time it does not use.
        lead_seconds = None

    return {
        "name": name,
        "description": _text(body.get("description")) or None,
        "trigger": trigger,
        "metric": metric,
        "lead_seconds": lead_seconds,
        "policy_id": _text(body.get("policy_id")) or None,
        "actions": normalise_actions(body.get("actions")),
        "live": bool(body.get("live")),
    }


# --------------------------------------------------------------------------- #
# The watched target
# --------------------------------------------------------------------------- #


def watched_entry(timer_view: Mapping[str, Any] | None, metric: str) -> dict[str, Any] | None:
    """The watched metric's progress entry from a live timer view, or ``None``.

    A timer view is WF-108's ``timer_for`` projection: it carries one entry per metric
    the policy's object kind earns. The watched metric that is not applicable is present
    as ``not_applicable`` and is returned as such, so a caller can name the skip.
    """

    if not timer_view:
        return None
    for entry in timer_view.get("metrics") or []:
        if entry.get("metric") == metric:
            return dict(entry)
    return None


def task_label(timer_view: Mapping[str, Any] | None, metric: str) -> str | None:
    """The SLA name and target this entry belongs to, for a run's recorded context."""

    if not timer_view:
        return None
    labels = {
        sla_vocab.METRIC_FRT: "First Response Time",
        sla_vocab.METRIC_NRT: "Next Response Time",
        sla_vocab.METRIC_TTC: "Time to Close",
        sla_vocab.METRIC_TTR: "Resolution Time",
    }
    return labels.get(metric, metric) if timer_view.get("policy_name") else None


def trigger_instant(entry: Mapping[str, Any], lead_seconds: int) -> datetime | None:
    """The instant the before-breach trigger point falls on.

    The entry's deadline is WF-108's office-hours-aware instant, so the trigger point is
    that deadline minus the lead time. ``None`` when the entry carries no deadline, which
    is the honest answer for a paused or already-breached timer.
    """

    deadline = coerce_instant(entry.get("deadline"))
    if deadline is None:
        return None
    return deadline - timedelta(seconds=int(lead_seconds))


# --------------------------------------------------------------------------- #
# Cancellations
# --------------------------------------------------------------------------- #


def teammate_reply_between(
    parts: Sequence[Mapping[str, Any]], start: datetime | None, end: datetime
) -> datetime | None:
    """The first teammate message at or after ``start`` and at or before ``end``.

    "If a teammate replies ... before the trigger point, the Workflow doesn't run." The
    window opens at the watched target's anchor and closes at the trigger point, so a
    reply that predates the target does not suppress a warning about it.
    """

    if start is None:
        return None
    for part in chase_rules.parts_of(parts):
        at = coerce_instant(part.get("at"))
        if at is None:
            continue
        if at < start:
            continue
        if at > end:
            break
        if part.get("author_kind") == chase_vocab.AUTHOR_TEAMMATE:
            return at
    return None


def cancel_reason(
    conversation: Mapping[str, Any],
    parts: Sequence[Mapping[str, Any]],
    entry: Mapping[str, Any],
    fire_at: datetime | None,
    now: datetime,
) -> str | None:
    """The reason this run is suppressed, or ``None`` if it may fire.

    The three the spec names are implemented in one place. A closed conversation is
    history, a snoozed conversation is paused, and a teammate reply before the trigger
    point is the wrap-up the workflow exists to avoid duplicating.
    """

    state = chase_vocab._slug(conversation.get("state")) or chase_vocab.DEFAULT_STATE
    if state == vocab.STATE_CLOSED:
        return vocab.CANCEL_CLOSED
    if state == vocab.STATE_SNOOZED:
        return vocab.CANCEL_SNOOZED

    anchor = coerce_instant(entry.get("anchor"))
    point = fire_at or now
    if teammate_reply_between(parts, anchor, point) is not None:
        return vocab.CANCEL_TEAMMATE_REPLY
    return None


# --------------------------------------------------------------------------- #
# The decision
# --------------------------------------------------------------------------- #


def _decision(
    fire: bool,
    *,
    trigger: str,
    metric: str,
    reason: str | None = None,
    level: str | None = None,
    fire_at: datetime | None = None,
    remaining_seconds: float | None = None,
    deadline: datetime | None = None,
    target_seconds: int | None = None,
    cancel: str | None = None,
) -> dict[str, Any]:
    return {
        "fire": fire,
        "trigger": trigger,
        "metric": metric,
        "level": level,
        "reason": reason,
        "reason_text": vocab.SKIP_REASON_TEXT.get(reason) if reason else None,
        "cancel": cancel,
        "cancel_text": vocab.CANCEL_LABELS.get(cancel) if cancel else None,
        "fire_at": stamp(fire_at) if fire_at else None,
        "remaining_seconds": remaining_seconds,
        "deadline": stamp(deadline) if deadline else None,
        "target_seconds": target_seconds,
    }


def decide(
    workflow: Mapping[str, Any],
    timer_view: Mapping[str, Any] | None,
    conversation: Mapping[str, Any],
    parts: Sequence[Mapping[str, Any]],
    now: datetime,
    *,
    already_fired: bool = False,
    target_seconds: int | None = None,
) -> dict[str, Any]:
    """Should this workflow fire for this conversation at ``now``?

    ``target_seconds`` is the watched target's duration when the caller already knows it
    (a workflow that names its policy); it is used to apply the lead-shorter rule up
    before anything else. Otherwise the rule is applied against the timer's own target.

    The order below is the order the rules are written in: a met target has nothing to
    warn about; an unapplicable target cannot be watched; a workflow that already fired
    must not fire twice; a lead time that is not shorter than its target is the spec's
    own refusal; a missed pre-warning window is recorded rather than fired late; and the
    three cancellations are checked last, immediately before the run.
    """

    trigger = require_trigger_kind(workflow.get("trigger"))
    metric = require_metric(workflow.get("metric"))
    entry = watched_entry(timer_view, metric)

    if entry is None:
        return _decision(False, trigger=trigger, metric=metric, reason=vocab.SKIP_NO_ACTIVE_SLA)

    if entry.get("state") == sla_vocab.METRIC_NOT_APPLICABLE:
        return _decision(
            False,
            trigger=trigger,
            metric=metric,
            reason=vocab.SKIP_METRIC_NOT_APPLICABLE,
            target_seconds=entry.get("target_seconds"),
        )

    if entry.get("state") == sla_vocab.METRIC_MET:
        return _decision(
            False,
            trigger=trigger,
            metric=metric,
            reason=vocab.SKIP_METRIC_ALREADY_MET,
            target_seconds=entry.get("target_seconds"),
        )

    if already_fired:
        return _decision(
            False,
            trigger=trigger,
            metric=metric,
            reason=vocab.SKIP_ALREADY_FIRED,
            target_seconds=entry.get("target_seconds"),
        )

    target = target_seconds or entry.get("target_seconds") or sla_vocab.DEFAULT_TARGETS[metric]
    remaining = entry.get("remaining_seconds")
    deadline = coerce_instant(entry.get("deadline"))
    level = vocab.level_for_trigger(trigger)

    if trigger in vocab.LEAD_TRIGGERS:
        lead = int(workflow.get("lead_seconds") or vocab.DEFAULT_LEAD_SECONDS)
        if not vocab.lead_is_shorter(lead, int(target)):
            return _decision(
                False,
                trigger=trigger,
                metric=metric,
                reason=vocab.SKIP_LEAD_NOT_SHORTER,
                target_seconds=int(target),
            )
        if entry.get("state") == sla_vocab.METRIC_BREACHED:
            # The target breached before the sweep saw the window. A pre-warning after the
            # breach is not a pre-warning, so this is recorded as missed, not fired late.
            return _decision(
                False,
                trigger=trigger,
                metric=metric,
                reason=vocab.SKIP_HEAD_START_MISSED,
                remaining_seconds=0.0,
                target_seconds=int(target),
            )
        if remaining is None or float(remaining) > lead:
            return _decision(
                False,
                trigger=trigger,
                metric=metric,
                reason=vocab.SKIP_LEAD_WINDOW_NOT_REACHED,
                remaining_seconds=remaining,
                deadline=deadline,
                target_seconds=int(target),
            )
        point = trigger_instant(entry, lead) or now
        cancel = cancel_reason(conversation, parts, entry, point, now)
        if cancel:
            return _decision(
                False,
                trigger=trigger,
                metric=metric,
                reason=vocab.SKIP_CANCELLED,
                cancel=cancel,
                fire_at=point,
                remaining_seconds=remaining,
                target_seconds=int(target),
            )
        return _decision(
            True,
            trigger=trigger,
            metric=metric,
            level=level,
            fire_at=point,
            remaining_seconds=remaining,
            deadline=deadline,
            target_seconds=int(target),
        )

    # on_breach
    if entry.get("state") != sla_vocab.METRIC_BREACHED:
        return _decision(
            False,
            trigger=trigger,
            metric=metric,
            reason=vocab.SKIP_METRIC_NOT_BREACHED,
            remaining_seconds=remaining,
            deadline=deadline,
            target_seconds=int(target),
        )
    cancel = cancel_reason(conversation, parts, entry, now, now)
    if cancel:
        return _decision(
            False,
            trigger=trigger,
            metric=metric,
            reason=vocab.SKIP_CANCELLED,
            cancel=cancel,
            fire_at=now,
            target_seconds=int(target),
        )
    return _decision(
        True,
        trigger=trigger,
        metric=metric,
        level=level,
        fire_at=now,
        target_seconds=int(target),
    )


def skip_decision(
    workflow: Mapping[str, Any],
    reason: str,
    entry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A decision that did not fire, for a reason the engine determined rather than the rules.

    Used for the policy-mismatch skip, which the caller knows but the pure rules cannot
    see. The shape is identical to :func:`decide`, so the sweep reports one kind of record.
    """

    trigger = require_trigger_kind(workflow.get("trigger"))
    metric = require_metric(workflow.get("metric"))
    return _decision(
        False,
        trigger=trigger,
        metric=metric,
        reason=reason,
        target_seconds=(entry or {}).get("target_seconds"),
    )


# --------------------------------------------------------------------------- #
# The Data Connector execution result
# --------------------------------------------------------------------------- #


def alert_outcome(action: Mapping[str, Any]) -> dict[str, Any]:
    """The ``data_connector.execution.completed`` payload for one alert action.

    The spec reports the outcome with ``source_type``, ``success``, ``http_status``,
    ``error_type`` and ``execution_time_ms``. This product makes no outbound HTTP call, so
    the outcome is derived from the action's own configuration: an alert with no
    ``connector_id`` or no ``url`` is the ``request_configuration_error`` at 400 that the
    webhook evidence quotes, and a fully configured alert succeeds at 200. A caller may
    also state ``simulate_http_status`` to exercise a failure in a demo or a test.
    """

    # The honesty flag is load-bearing: if the product ever gains a connector credential,
    # this function is the one place the recorded outcome changes from derived to measured.
    forced = action.get("simulate_http_status")
    if forced is not None:
        status = int(forced)
        error = vocab.ERROR_NONE if status < 400 else vocab.ERROR_HTTP
    elif not action.get("connector_id") or not action.get("url"):
        status = vocab.CONFIGURATION_HTTP_STATUS
        error = vocab.ERROR_REQUEST_CONFIGURATION
    else:
        status = vocab.SUCCESS_HTTP_STATUS
        error = vocab.ERROR_NONE

    execution_ms = int(action.get("simulate_execution_ms") or 0)
    if not execution_ms:
        # A configured connector's call takes time. When nothing is simulated, the
        # reported duration is the request's own size, so the field is never a bare zero.
        execution_ms = 24 + len(str(action.get("url") or ""))

    return {
        "source_type": vocab.SOURCE_TYPE_WORKFLOW,
        "success": status < 400,
        "http_status": status,
        "error_type": error,
        "error_type_label": vocab.ERROR_TYPE_LABELS[error],
        "execution_time_ms": execution_ms,
        "connector_id": action.get("connector_id"),
        "url": action.get("url"),
        "method": action.get("method") or "POST",
        "sent_by_this_product": vocab.SENT_BY_THIS_PRODUCT,
    }


# --------------------------------------------------------------------------- #
# The inbox priority
# --------------------------------------------------------------------------- #


def escalation_rank(level: str | None) -> int:
    """A level's rank, so the inbox can sort an escalated conversation above the rest."""

    return vocab.LEVEL_RANK.get(str(level or ""), 0)


def priority_for(level: str | None) -> bool:
    """Whether a level marks a conversation as priority. Both levels do."""

    return escalation_rank(level) > 0


# --------------------------------------------------------------------------- #
# Small derivations the engine reads
# --------------------------------------------------------------------------- #


def action_summary(action: Mapping[str, Any]) -> str:
    """One human sentence for what an action did, for the run's recorded actions."""

    kind = action.get("kind")
    if kind == vocab.ACTION_ASSIGN:
        who = action.get("assignee") or action.get("team") or "a teammate"
        return f"assigned the conversation to {who}"
    if kind == vocab.ACTION_TAG:
        return f"tagged the conversation '{action.get('tag')}'"
    if kind == vocab.ACTION_NOTE:
        return "left an internal note"
    if kind == vocab.ACTION_NOTIFY:
        return f"notified {action.get('channel')} on {action.get('via')}"
    if kind == vocab.ACTION_ALERT:
        return f"sent a Data Connector alert to {action.get('connector_id') or action.get('url') or 'nowhere'}"
    return str(kind)


def merge_tag(existing: Any, tag: str) -> list[str]:
    """The tag list with ``tag`` added once, preserving order."""

    tags = [str(entry) for entry in (existing or [])]
    lowered = str(tag).lower()
    if lowered in {entry.lower() for entry in tags}:
        return tags
    return [*tags, lowered]
