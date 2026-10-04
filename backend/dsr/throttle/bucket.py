"""The pre-emptive half: spend before the vendor says stop.

Every throttle that has shipped in this codebase is **reactive**. It answers a
refusal, so the first call of a burst has already gone out and the refusal is the
cost. A token bucket is the other shape: the room keeps a count of what it is
allowed to send in the current window, spends one per call, and when the count
runs out it stops *before* sending. The research's step 1 asks for exactly this -
"keeps a per-connection token bucket sized from the vendor's published limits" -
and it is the one piece of this workflow that no shipped code has.

The arithmetic is the standard one, and the three edge cases are where a token
bucket is usually wrong:

* **Refill is continuous, not per window.** The bucket refills at
  ``sustained / window_seconds`` tokens per second. Waiting for a window boundary
  would spend the whole bucket every ten seconds on HubSpot and make the room
  burst at the top of each window.
* **Refill is capped at capacity.** A bucket that refills past its capacity while
  nothing is being sent is a bucket that will burst at full rate the moment
  sending resumes, which is the opposite of what the limit asks for.
* **Time only moves forward.** A clock that went backwards - a test fixture, an
  NTP correction - would compute a negative refill and a bucket with more tokens
  than its capacity. Elapsed time is clamped at zero.

**A bucket with no capacity does not block.** Salesforce publishes no burst
number this build could source and Dataverse publishes none at all, so their
policies carry ``burst: None``. A bucket whose capacity is unknown is *not*
treated as empty and *not* treated as unlimited by default: the room records that
it is pre-emptively blind on that connection and falls back to the vendor's own
refusal, which is the only signal it actually has.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Mapping

from dsr.throttle.timestamps import iso, parse_instant


def refill_rate(policy: Mapping[str, Any]) -> float:
    """Tokens per second, or ``0.0`` when the policy names no sustained rate."""
    sustained = policy.get("sustained")
    window = policy.get("window_seconds")
    if sustained in (None, "") or window in (None, "") or float(window) <= 0:
        return 0.0
    return float(sustained) / float(window)


def capacity(policy: Mapping[str, Any]) -> float:
    """How many tokens the bucket holds, or ``0.0`` when the policy names no burst."""
    burst = policy.get("burst")
    if burst in (None, ""):
        return 0.0
    return max(0.0, float(burst))


def new_state(policy: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """A full bucket. A connection starts able to spend its whole window."""
    limit = capacity(policy)
    return {
        "tokens": limit,
        "capacity": limit,
        "rate_per_second": round(refill_rate(policy), 6),
        "updated_at": iso(now),
        "window_started_at": iso(now),
        "sent_in_window": 0,
        "known": bool(limit),
    }


def refill(state: Mapping[str, Any], policy: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """The same bucket, topped up for the time that has passed.

    Returns a new dict rather than mutating: the record it comes from is a stored
    payload, and mutating a payload a caller still holds is how two of them end up
    disagreeing about the same row.
    """
    updated = dict(state)
    limit = capacity(policy)
    updated["capacity"] = limit
    updated["rate_per_second"] = round(refill_rate(policy), 6)
    updated["known"] = bool(limit)

    previous = parse_instant(state.get("updated_at")) or now
    elapsed = max(0.0, (now - previous).total_seconds())
    rate = refill_rate(policy)
    tokens = float(state.get("tokens") or 0.0) + elapsed * rate
    if limit:
        tokens = min(tokens, limit)
    elif rate:
        # No published capacity, but a published rate: the count is still worth
        # keeping, because "sent in this window" is a fact an operator reads even
        # when the bucket cannot refuse anything.
        tokens = max(tokens, 0.0)
    updated["tokens"] = round(tokens, 6)
    updated["updated_at"] = iso(now)
    return updated


def decide(
    state: Mapping[str, Any],
    policy: Mapping[str, Any],
    now: datetime,
    *,
    cost: int = 1,
) -> dict[str, Any]:
    """Whether this batch may go out now, and when it may try again.

    The answer has four parts, because the four reasons a bucket says no are four
    different problems for whoever is looking:

    ``allowed``
        Whether to send. ``False`` for a paused connection, an empty bucket, and a
        bucket whose capacity this build has no sourced number for.
    ``reason``
        ``proceed``, ``paused``, ``empty`` or ``unknown_capacity``. A client
        renders these as four different things, so they are four words and not a
        boolean with a note.
    ``retry_in_seconds``
        How long until the bucket holds enough for this call. ``None`` when
        nothing is scheduled, which means a person has to act.
    ``bucket``
        The state as it now stands, so a caller can store what it decided on
        rather than re-deriving it.

    A pause is read from either the policy or the state, because the engine knows
    the pause from the connection record and the policy is a numbers declaration:
    a caller that set ``paused`` on the state it passes in has paused it.

    The empty-bucket wait is computed from the rate rather than guessed: one token
    at ``sustained / window_seconds`` tokens per second. When the bucket is empty
    and the cost is more than a whole window's worth of tokens the wait is the
    cost's own time, not one token's, because waiting for a single token would
    still leave the batch short.
    """
    limit = capacity(policy)
    rate = refill_rate(policy)
    spent = max(1, int(cost))

    if policy.get("paused") or state.get("paused"):
        return {
            "allowed": False,
            "reason": "paused",
            "retry_in_seconds": None,
            "bucket": dict(state),
            "detail": "an admin paused this connection, so no call goes out until they resume it",
        }

    if not limit:
        return {
            "allowed": True,
            "reason": "unknown_capacity",
            "retry_in_seconds": None,
            "bucket": dict(state),
            "detail": (
                f"{policy.get('vendor', 'this vendor')} publishes no burst capacity in the "
                "researched source set, so the room cannot refuse a call before the vendor does; "
                "the bucket counts what was sent and the vendor's own 429 is the signal that stops "
                "the sending"
            ),
        }

    available = float(state.get("tokens") or 0.0)
    if available >= spent:
        return {
            "allowed": True,
            "reason": "proceed",
            "retry_in_seconds": 0,
            "bucket": dict(state),
            "detail": f"{available:g} of {limit:g} tokens were available, and this batch costs "
            f"{spent}",
        }

    wait = _wait_for(available, spent, rate)
    return {
        "allowed": False,
        "reason": "empty",
        "retry_in_seconds": wait,
        "bucket": dict(state),
        "detail": f"the bucket holds {available:g} of {limit:g} tokens and this batch costs {spent}; "
        f"the room waits {wait}s for the bucket to refill rather than sending into the limit",
    }


def _wait_for(available: float, cost: int, rate: float) -> int:
    """Seconds until the bucket holds ``cost``, given a refill ``rate``."""
    if rate <= 0:
        # A capacity with no rate is a policy this build cannot schedule against.
        # Reporting a wait would be a number nobody computed.
        return 0
    deficit = max(0.0, float(cost) - available)
    return max(1, int(math.ceil(deficit / rate)))


def spend(
    state: Mapping[str, Any], policy: Mapping[str, Any], now: datetime, cost: int = 1
) -> dict:
    """The bucket after ``cost`` tokens have been taken out of it.

    Called **after** the vendor answers, not before: a token the room spent and
    the vendor refused still came off the limit, and refusing to count it would
    make the room's own view of the budget drift away from the vendor's.
    """
    updated = refill(state, policy, now)
    limit = capacity(policy)
    tokens = max(0.0, float(updated.get("tokens") or 0.0) - max(1, int(cost)))
    if limit:
        tokens = min(tokens, limit)
    updated["tokens"] = round(tokens, 6)
    updated["sent_in_window"] = int(updated.get("sent_in_window") or 0) + max(1, int(cost))
    updated["updated_at"] = iso(now)
    updated["last_spent_at"] = iso(now)
    return updated


__all__ = [
    "refill_rate",
    "capacity",
    "new_state",
    "refill",
    "decide",
    "spend",
]
