"""Reading the timestamps this workflow is handed.

Two spellings arrive for the same thing. The research's extensibility note is
explicit about it: "Recompute externally from ``workspace.*`` webhooks
(``occurredAt``)". A Dock webhook carries ``occurredAt`` in camelCase; this
product's own records are snake_case. Accepting both means a webhook forwarder
needs no translation layer, and a client written against the source API's body
works unchanged.

The one thing this module will not do is guess. An unreadable ``occurredAt`` is
refused with the value that could not be read, because an engagement event
stored with a fabricated timestamp silently moves a room between buckets and
nothing in the product would ever say so.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.trend_health.errors import InvalidTimestamp

#: The keys a timestamp may arrive under, in order of preference. The researched
#: camelCase first, because that is what the source sends.
TIMESTAMP_KEYS: tuple[str, ...] = ("occurredAt", "occurred_at", "at", "timestamp")

#: How far ahead of the server clock an event may be stamped and still be stored.
#:
#: Webhook delivery is not instantaneous and the sender's clock is not ours, so a
#: small positive skew is normal and rejecting it would throw away real events. A
#: large one is a fault, and an event dated next month would land outside every
#: window in :mod:`dsr.trend_health.windows` - a room could be pushed to Cooling
#: by a sender with a broken clock and there would be nothing to see.
#:
#: Stored-skewed events are still refused rather than clamped: a clamp writes a
#: time the sender never claimed.
CLOCK_SKEW_TOLERANCE_SECONDS = 300


def parse_timestamp(value: Any, *, required: bool = True) -> datetime | None:
    """Parse one timestamp into an aware UTC datetime, or refuse it.

    Accepts an ISO-8601 string with or without a ``Z``, a space separator, a date
    on its own (treated as midnight UTC), or a POSIX epoch in seconds or
    milliseconds. Anything else raises :class:`InvalidTimestamp` naming the value,
    which is the difference between a sender that can fix its payload and a bug
    report three weeks later.

    ``required=False`` inverts the refusal: an absent *or* unreadable value comes
    back as ``None``. That is what a reader wants - the classifier must survive a
    row someone wrote by hand with a broken timestamp - and it is why intake
    calls this with ``required=True``, so a payload that cannot be read is still
    refused rather than stored at a guessed time.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    if isinstance(value, bool) or value is None:
        parsed = None
    elif isinstance(value, (int, float)):
        parsed = _from_epoch(float(value))
    else:
        text = str(value).strip()
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        if " " in text and "T" not in text:
            text = text.replace(" ", "T", 1)
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = _from_epoch(_looks_like_epoch(text))

    if parsed is None:
        if not required:
            return None
        raise InvalidTimestamp(
            f"{value!r} is not a timestamp this workflow can read; "
            "use ISO-8601 (2026-09-27T12:00:00Z) or an epoch in seconds"
        )
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _from_epoch(number: float) -> datetime | None:
    # Heuristic: anything past year 5138 in seconds is a millisecond stamp. Ten
    # seconds of slack so a caller is not off by a rounding error.
    if number <= 0:
        return None
    if number > 100_000_000_000:
        number = number / 1000.0
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _looks_like_epoch(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        return 0.0


def timestamp_of(payload: Mapping[str, Any], *, default: datetime | None = None) -> tuple[datetime, bool]:
    """The event's own time, or the moment it arrived, and which one was used.

    Returns ``(when, defaulted)``. A payload with no timestamp is not refused -
    a person recording "the buyer opened the room just now" from the UI should
    not have to supply a time - but the stored record says which of the two it
    was, so a reader can tell a received-at stamp from a sender's claim.
    """
    for key in TIMESTAMP_KEYS:
        if key in payload and payload[key] not in (None, ""):
            return parse_timestamp(payload[key]), False
    if default is None:
        raise InvalidTimestamp(
            "no timestamp in the payload; send one of " + ", ".join(TIMESTAMP_KEYS)
        )
    return default, True


def check_not_ahead(when: datetime, *, now: datetime, tolerance: int = CLOCK_SKEW_TOLERANCE_SECONDS) -> None:
    """Refuse an event stamped further into the future than the tolerance allows."""
    ahead = (when - now).total_seconds()
    if ahead > tolerance:
        raise InvalidTimestamp(
            f"occurredAt is {int(ahead)}s in the future, beyond the {tolerance}s clock-skew "
            "tolerance; an event cannot predate the deal it is evidence for"
        )
