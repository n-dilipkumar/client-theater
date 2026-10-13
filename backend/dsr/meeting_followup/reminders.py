"""Gong Forecast reminders: the three researched timings, and the two we refuse.

The evidence gives the timings exactly: "in-app 24h before due, Slack 1h before to
AEs, Slack summary 2h after to managers". The issue asks: "Record whether you
implement them."

The answer is one of the three, and this module makes it visible rather than
implicit:

* the **in-app** reminder is modelled, because this product has an in-app surface
  and a due date it can read;
* the two **Slack** rows are refused at validation, because the product has no
  confirmed Slack surface. A reminder that posts nowhere is a rule that looks armed
  and delivers nothing, so it is not stored.

The three timings are still served, so a page can show the seller what the vendor
does and that this build does not yet send two of them.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping

from dsr.meeting_followup import vocabulary as vocab
from dsr.meeting_followup.errors import SlackChannelRefused

#: The three researched reminders, in the order the evidence names them.
FORECAST_REMINDERS: tuple[dict[str, Any], ...] = (
    {
        "id": "in_app_24h_before",
        "channel": "in_app",
        "timing": "before",
        "hours": vocab.FORECAST_IN_APP_HOURS_BEFORE,
        "implemented": True,
        "why": "This product has an in-app surface and a due date it can read.",
    },
    {
        "id": "slack_1h_before",
        "channel": "slack",
        "timing": "before",
        "hours": vocab.FORECAST_SLACK_HOURS_BEFORE,
        "implemented": False,
        "why": "The product has no confirmed Slack surface.",
    },
    {
        "id": "slack_summary_2h_after",
        "channel": "slack",
        "timing": "after",
        "hours": vocab.FORECAST_SLACK_SUMMARY_HOURS_AFTER,
        "implemented": False,
        "why": "The product has no confirmed Slack surface.",
    },
)


def validate_reminder(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one Forecast reminder, refusing the Slack channels.

    The refusal names the researched timing it would have carried, so a seller who
    reads the message learns what the vendor does rather than only that this build
    will not do it.
    """
    channel = str(payload.get("channel") or "in_app").strip().lower()
    if channel not in vocab.FORECAST_CHANNELS:
        raise SlackChannelRefused(
            f"channel {channel!r} is not one of the researched Forecast channels",
            {"channel": f"one of: {', '.join(vocab.FORECAST_CHANNELS)}"},
        )
    if channel not in vocab.FORECAST_IMPLEMENTED_CHANNELS:
        known = next(
            (r for r in FORECAST_REMINDERS if r["channel"] == channel),
            None,
        )
        timing = f"{known['hours']}h {known['timing']}" if known else "a researched offset"
        raise SlackChannelRefused(
            f"The {timing} Slack reminder is not sent by this product: it has no "
            "confirmed Slack surface. The researched timings are served so the "
            "rule is visible rather than silently absent.",
            {"channel": "in_app only; the two Slack rows are recorded, not sent"},
        )

    due = str(payload.get("due_at") or "").strip()
    if not due:
        raise SlackChannelRefused(
            "due_at is required for an in-app reminder",
            {"due_at": "ISO 8601"},
        )
    return {
        "channel": channel,
        "due_at": due,
        "hours_before": vocab.FORECAST_IN_APP_HOURS_BEFORE,
        "implemented": True,
    }


def reminder_moment(due_at: str, now: datetime) -> bool:
    """Whether the in-app reminder window is open.

    The window is 24 hours before the due date and closes at it: "in-app 24h before
    due". A reminder that arrives after the due date is noise.
    """
    try:
        due = datetime.fromisoformat(str(due_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    opens = due - timedelta(hours=vocab.FORECAST_IN_APP_HOURS_BEFORE)
    return opens <= now <= due


def served() -> list[dict[str, Any]]:
    """The three researched timings and which of them this build sends."""
    return [dict(entry) for entry in FORECAST_REMINDERS]
