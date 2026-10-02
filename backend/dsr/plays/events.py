"""The outcome events a generated task emits, and the webhook subscription that
carries them.

"Track outcomes via Salesloft webhooks (``task_created``, ``task_completed``,
``step_created``, ``success_created``)." That is the last step of the researched
flow, and it is the only part of this workflow where time is a real input rather
than bookkeeping, because:

    "Note: A failing webhook is retried three additional times, spaced 15 seconds
    apart, before being marked as failed."

Three *additional* attempts after the first, 15 seconds apart, then failed. So a
delivery that has failed once is not yet failed, a delivery that has failed four
times is, and the next attempt after any failure is due exactly 15 seconds after
the one that failed. :func:`delivery` is that rule as a function, with no clock of
its own, so a test can hold it at any instant.

Events are stored rather than emitted, and nothing here opens a socket. This
product is the source of the buyer's event; the vendor's platform is what receives
a webhook, and an audit row or a test that claimed a POST had gone to
``api.salesloft.com`` would be describing a call this app never makes. What is
real is the decision: which event types a subscription asks for, which events a
task produced, and where each delivery stands in its retry schedule.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from dsr.plays.errors import EventError
from dsr.plays.vocabulary import (
    PLAY_EVENT_TYPES,
    WEBHOOK_RETRY_ATTEMPTS,
    WEBHOOK_RETRY_SPACING_SECONDS,
    require_event_type,
)

#: The states a delivery is in. ``pending`` has not been attempted, ``retrying`` has
#: failed at least once and has attempts left, ``delivered`` succeeded, ``failed`` used
#: them all up.
DELIVERY_STATES: tuple[str, ...] = ("pending", "retrying", "delivered", "failed")

#: The statuses that count as a success. A webhook that answers 2xx is delivered; a
#: 4xx is not, and neither is a 5xx. The research says "a failing webhook" without
#: defining failing, so this is the reading - and it is a reading, recorded as the
#: `delivery-success-is-2xx` inference.
SUCCESS_STATUS_RANGE = range(200, 300)

#: The event types this workflow follows, and what produces each. The mapping is
#: read off the researched data flow: "task/step/success events stream out via
#: webhooks" once the seller has acted.
EVENT_TRIGGERS: dict[str, str] = {
    "task_created": "a Play created the one-off task, with no human in the loop",
    "task_completed": "the seller acted on the task",
    "step_created": "the task added the buyer to a cadence, so a cadence step exists",
    "success_created": "a cadence step succeeded",
}


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def next_attempt_at(last_attempt_at: Any) -> str | None:
    """When the next attempt is due, given when the last one happened.

    "Spaced 15 seconds apart" is a fixed gap, not a backoff, so this is the same
    duration however many attempts have been made.
    """
    moment = _parse(last_attempt_at)
    if moment is None:
        return None
    return _iso(moment + timedelta(seconds=WEBHOOK_RETRY_SPACING_SECONDS))


def delivery(attempts: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    """Where a delivery stands, from its attempt history alone.

    No clock. ``now`` is only needed to say whether a scheduled retry is already
    due, and that is :func:`due`, so a caller can decide what to do with it rather
    than having this function decide silently.

    The count is called ``attempt_count`` rather than ``attempts`` on purpose: on an
    event record ``attempts`` *is* the history, and a summary field of the same name
    would silently overwrite it when the two are merged.
    """
    history = list(attempts or [])
    if not history:
        return {
            "state": "pending",
            "attempt_count": 0,
            "failures": 0,
            "retries_remaining": WEBHOOK_RETRY_ATTEMPTS,
            "next_attempt_at": None,
            "detail": "Not attempted yet.",
        }

    failures = sum(1 for attempt in history if not attempt.get("ok"))
    last = history[-1]
    if last.get("ok"):
        return {
            "state": "delivered",
            "attempt_count": len(history),
            "failures": failures,
            "retries_remaining": 0,
            "next_attempt_at": None,
            "detail": (
                f"Delivered on attempt {len(history)} of "
                f"{WEBHOOK_RETRY_ATTEMPTS + 1}."
                + (f" {failures} earlier attempt(s) failed first." if failures else "")
            ),
        }

    remaining = WEBHOOK_RETRY_ATTEMPTS - (failures - 1)
    if failures > WEBHOOK_RETRY_ATTEMPTS:
        return {
            "state": "failed",
            "attempt_count": len(history),
            "failures": failures,
            "retries_remaining": 0,
            "next_attempt_at": None,
            "detail": (
                f"Marked failed after {failures} attempts. The research is explicit: "
                '"A failing webhook is retried three additional times, spaced 15 seconds '
                'apart, before being marked as failed." No further attempt is scheduled.'
            ),
        }

    due = next_attempt_at(last.get("at"))
    return {
        "state": "retrying",
        "attempt_count": len(history),
        "failures": failures,
        "retries_remaining": remaining,
        "next_attempt_at": due,
        "detail": (
            f"Failed {failures} time(s); {remaining} of the {WEBHOOK_RETRY_ATTEMPTS} "
            f"additional attempts remain, the next due at {due} "
            f"({WEBHOOK_RETRY_SPACING_SECONDS} seconds after the last)."
        ),
    }


def due(delivery_state: Mapping[str, Any], now: datetime) -> bool:
    """Whether a scheduled retry is due yet, and whether it is due at all.

    A delivery with no ``next_attempt_at`` and a state of ``pending`` is due
    immediately, which is the ordinary first attempt.
    """
    if delivery_state.get("state") in ("delivered", "failed"):
        return False
    scheduled = _parse(delivery_state.get("next_attempt_at"))
    if scheduled is None:
        return delivery_state.get("state") == "pending"
    return now >= scheduled


def attempt_ok(payload: Mapping[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Read one delivery attempt out of a request body.

    ``ok`` may be given, or inferred from ``status_code``. When both are given and
    disagree, the status code wins and the disagreement is refused: a body that says
    the delivery worked while carrying a 500 is a caller bug, and quietly believing
    one of the two is how a failed webhook ends up marked delivered.
    """
    status = payload.get("status_code")
    if status is not None and (isinstance(status, bool) or not isinstance(status, int)):
        raise EventError(f"status_code must be an integer; got {status!r}")
    stated = payload.get("ok")
    if stated is not None and not isinstance(stated, bool):
        raise EventError(f"ok must be a boolean; got {stated!r}")

    if status is not None:
        actual = status in SUCCESS_STATUS_RANGE
        if stated is not None and stated != actual:
            raise EventError(
                f"ok={stated} disagrees with status_code={status}: a webhook answering "
                f"{status} has not been delivered. The status code is what decides, and a "
                "body that claims otherwise is refused rather than believed."
            )
        return actual, {
            "status_code": status,
            "reason": f"derived from status_code {status}",
        }
    if stated is None:
        raise EventError(
            "an attempt must carry ok or status_code. A webhook delivery has an outcome, "
            "and 'we did not find out' is not one of them."
        )
    return stated, {"status_code": None, "reason": "given as ok"}


def normalise_subscription(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a webhook subscription body.

    The research names the endpoint and the event types and the retry policy, and
    does **not** publish a field list for a subscription. So this accepts
    ``event_types`` - which is sourced - plus a target, which is not, and refuses
    neither. It warns about a missing target rather than demanding one, because
    inventing a required field a vendor does not document is how a partner's first
    call fails.
    """
    if not isinstance(payload, Mapping):
        raise EventError("a webhook subscription must be a JSON object")

    events_raw = payload.get("event_types") or payload.get("eventTypes")
    if events_raw is None:
        raise EventError(
            "event_types is required. The researched event types this workflow follows "
            f"are {', '.join(PLAY_EVENT_TYPES)}"
        )
    if isinstance(events_raw, str):
        events_raw = [part.strip() for part in events_raw.split(",") if part.strip()]
    if not isinstance(events_raw, (list, tuple)) or not events_raw:
        raise EventError("event_types must name at least one event type")
    events = list(dict.fromkeys(require_event_type(entry) for entry in events_raw))

    # The research publishes no field list for a subscription, so the accepted set
    # is the event types, a target, a description and a switch - and nothing else,
    # because a field this workflow cannot honour should not be stored.
    accepted = ("event_types", "eventTypes", "target_url", "targetUrl", "description", "enabled")
    unknown = [str(key) for key in payload if str(key) not in accepted]
    if unknown:
        raise EventError(
            f"unrecognised subscription field(s) {', '.join(sorted(unknown))}. The research "
            "names event_types and the target; anything else is a field this workflow "
            "cannot honour."
        )

    target = payload.get("target_url", payload.get("targetUrl"))
    if target is not None and (not isinstance(target, str) or not target.strip()):
        raise EventError("target_url must be a non-empty string")

    description = payload.get("description")
    if description is not None and not isinstance(description, str):
        raise EventError("description must be a string")

    data: dict[str, Any] = {
        "event_types": events,
        "target_url": target.strip() if isinstance(target, str) else None,
        "description": description.strip() if isinstance(description, str) else None,
        "enabled": bool(payload.get("enabled", True)),
        "retry_policy": {
            "additional_attempts": WEBHOOK_RETRY_ATTEMPTS,
            "spacing_seconds": WEBHOOK_RETRY_SPACING_SECONDS,
        },
    }
    return data


def build_event(
    event_type: str,
    *,
    task: Mapping[str, Any],
    room_id: str | None,
    occurred_at: str,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One outcome event, with no delivery attempt recorded against it.

    The event is created ``pending``: the thing that happened is a fact, and
    whether the webhook carrying it arrived is a *different* fact that only an
    observed delivery answers. Recording a synthetic success here would report
    "delivered" for a webhook nobody has sent yet, and the researched retry policy
    is about the real thing.

    ``payload`` is carried for context and validated only if it carries an outcome,
    so a caller that does have a delivery result in hand fails loudly rather than
    having it silently dropped.
    """
    name = require_event_type(event_type)
    if payload and ("ok" in payload or "status_code" in payload):
        # Refused rather than stored: a body carrying a delivery result means the
        # caller believes a delivery happened, and that belongs in the attempt
        # history through `record_attempt`, which applies the researched schedule.
        # Carrying it on the event would report a delivery nobody recorded.
        raise EventError(
            "a delivery outcome does not belong on an event. An event is created pending; "
            "record the attempt through the delivery route, which applies the researched "
            "retry schedule."
        )
    return {
        "event_type": name,
        "task_id": task.get("id"),
        "play_id": task.get("play_id"),
        "task_type": task.get("task_type"),
        "subject": task.get("subject"),
        "assigned": bool(task.get("assigned")),
        "meaning": EVENT_TRIGGERS.get(
            name, "an outcome event this workflow records without a researched meaning"
        ),
        "payload": dict(payload or {}),
        "attempts": [],
        **delivery([]),
        "created_at": occurred_at,
        "room_note": f"Observed in room {room_id}." if room_id else None,
    }


def describe() -> dict[str, Any]:
    """The event and retry rules, published for clients and for the page."""
    return {
        "tracked_event_types": list(PLAY_EVENT_TYPES),
        "event_triggers": EVENT_TRIGGERS,
        "delivery_states": list(DELIVERY_STATES),
        "retry_policy": {
            "additional_attempts": WEBHOOK_RETRY_ATTEMPTS,
            "total_attempts": WEBHOOK_RETRY_ATTEMPTS + 1,
            "spacing_seconds": WEBHOOK_RETRY_SPACING_SECONDS,
            "success_status_range": [SUCCESS_STATUS_RANGE.start, SUCCESS_STATUS_RANGE.stop - 1],
            "rule": (
                "A failing webhook is retried three additional times, spaced 15 seconds "
                "apart, before being marked as failed."
            ),
        },
    }
