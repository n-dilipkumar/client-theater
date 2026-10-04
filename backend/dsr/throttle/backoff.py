"""How long to wait, and where that number comes from.

Three numbers decide every deferral this room makes, and each has a different
owner:

* **The ladder.** :data:`BASE_SECONDS` doubling to :data:`MAX_SECONDS`. This is
  the value ``dsr.partial_failures`` has shipped and its tests pin exactly, so it
  moved here rather than being re-chosen. :func:`backoff_seconds` is the ladder
  and nothing else: no jitter, no vendor reading, no header. A caller that needs
  the vendor's answer calls :func:`schedule`.
* **The vendor's own advice.** ``Retry-After`` where the vendor sent it, and the
  HubSpot lock's two-second floor, which the vendor states in prose rather than
  in a header. Both are floors, never ceilings.
* **The jitter**, which is what stops a hundred rooms that were throttled by the
  same vendor from retrying in the same second.

**The jitter is deterministic, and that is the whole point.** Exponential
backoff with jitter normally draws a random number, which is right for a live
client and wrong for a stored decision: this room writes the wait onto a batch
and a person reads it, a test asserts on it, and a retry must wait the *same*
number of seconds twice or the schedule it published is not the schedule it
keeps. So the jitter is a function of the batch's own idempotency key
(:func:`jitter_fraction`), which means every reader of the record derives the same
number without anything being stored, and two rooms with different keys still
spread out.

**The cap on ``Retry-After`` is inferred and recorded.** HubSpot says the header
says "how many seconds to wait (typically up to 24 hours)" and names no bound, so
:data:`RETRY_AFTER_CAP_SECONDS` takes the vendor's own upper word. A wait beyond
it is not retried automatically: :func:`schedule` reports ``beyond_cap`` and the
caller hands the batch to a person, because a queue that will not move for a day
is not a queue.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Mapping

from dsr.throttle.classify import LOCK_FLOOR_SECONDS, RETRY_AFTER_CAP_SECONDS, header
from dsr.throttle.timestamps import iso, parse_instant, plus_seconds

#: The first automatic wait, doubling each attempt.
#:
#: **Inferred, and it shipped as this number before it moved here.** WF-040's
#: research says the queue drains automatically for retryable classes and never
#: says how long the first wait is. Thirty seconds is long enough that a vendor
#: asked to slow down has probably finished the window that throttled us, and
#: short enough that a room does not look broken. It is patchable per connection.
BASE_SECONDS = 30

#: The ceiling on the ladder. Reached at the eighth attempt.
MAX_SECONDS = 1800

#: How many automatic attempts a batch gets before a person is asked.
MAX_ATTEMPTS = 5

#: The room's own label for the ladder, so a client renders one schedule rather
#: than three slightly different ones.
LABEL = "exponential from 30s, doubling, capped at 30m"

#: The largest share of the ladder that jitter may add.
#:
#: "Equal jitter" - half the ladder is fixed and half is derived from the key - so
#: a retry never comes earlier than half the wait the ladder asked for, and never
#: later than the ceiling. A jitter that could reach zero would let a retry beat
#: the floor the vendor stated.
JITTER_RATIO = 0.5

#: The exponent bound. Past 16 doublings the result is past
#: :data:`MAX_SECONDS` anyway, and ``2**17`` is where a naive implementation
#: starts costing real milliseconds to compute a number nobody will use.
_EXPONENT_BOUND = 16

#: Where a wait came from. Published so a client renders the reason beside the
#: number rather than guessing.
SOURCES: tuple[str, ...] = ("retry_after", "lock_floor", "backoff", "beyond_cap")


def backoff_seconds(attempt: int) -> int:
    """How long to wait before automatic attempt ``attempt + 1``.

    ``attempt`` is the number of attempts already made, so the first wait is
    :data:`BASE_SECONDS`. A negative or zero attempt is read as the first, because
    a caller that lost count should still get a real wait rather than zero.

    This function is the ladder verbatim, and that is deliberate:
    ``dsr.partial_failures`` called it before this package existed and its tests
    pin every rung. Moving a number is not changing it.
    """
    made = max(1, int(attempt))
    exponent = min(made - 1, _EXPONENT_BOUND)
    return int(min(BASE_SECONDS * (2**exponent), MAX_SECONDS))


def jitter_fraction(seed: str) -> float:
    """A stable fraction in ``[0, 1)`` derived from ``seed``.

    Derived, not drawn. ``hashlib`` rather than :func:`hash` because ``hash`` of a
    string is salted per process in CPython, which would make the same batch defer
    for a different number of seconds on every restart - and a schedule that
    changes when the room restarts is not a schedule.
    """
    digest = hashlib.blake2b(str(seed).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") / float(1 << 64)


def retry_after_seconds(
    headers: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
    cap: int = RETRY_AFTER_CAP_SECONDS,
) -> int | None:
    """The ``Retry-After`` the vendor sent, in seconds, or ``None``.

    Both spellings are read. RFC 9110 allows delta-seconds or an HTTP-date, and a
    vendor that sends the date form means the same thing by it: "wait until then".
    A date in the past is a wait of zero, which is what it says.

    A value the room cannot read is ``None`` rather than an exception: the header
    is advice, and advice that cannot be parsed must not fail the batch that
    reported it. The caller falls back to the ladder, which is always defined.
    """
    raw = header(headers, "Retry-After")
    if not raw:
        return None

    text = raw.strip()
    try:
        return max(0, min(int(float(text)), int(cap)))
    except (TypeError, ValueError):
        pass

    moment = parse_instant(_as_date(text))
    if moment is None:
        return None
    reference = now or datetime.now(timezone.utc)
    delta = (moment - reference).total_seconds()
    return max(0, min(int(delta), int(cap)))


def _as_date(text: str) -> datetime | None:
    """Read the HTTP-date form of ``Retry-After``, or ``None``."""
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def exceeds_cap(raw: Any, *, cap: int = RETRY_AFTER_CAP_SECONDS) -> bool:
    """Whether the vendor asked for a wait longer than the room will take alone.

    Read from the raw value rather than from :func:`retry_after_seconds`, because
    that function has already clamped by the time it returns. A caller that only
    kept the clamped number cannot tell "the vendor said one day" from "the vendor
    said one day and a bit and the room took the cap".

    Only the delta-seconds form is tested, because only that form can be longer
    than a cap expressed in seconds: an HTTP-date inside the cap is a wait the
    room will take, and one far in the future is a wait the vendor has scheduled
    for later rather than a cap breach.
    """
    text = str(raw or "").strip()
    if not text:
        return False
    try:
        return float(text) > float(cap)
    except (TypeError, ValueError):
        return False


def schedule(
    attempt: int,
    *,
    signal: Any = None,
    headers: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    seed: str = "",
    cap: int = RETRY_AFTER_CAP_SECONDS,
    lock_floor_seconds: int = LOCK_FLOOR_SECONDS,
) -> dict[str, Any]:
    """The wait for one deferral, and where the number came from.

    In precedence order, which is also the order a reader should trust them:

    1. **``retry_after``** - the vendor stated a wait. It is honoured up to
       ``cap``, and the floor is still applied because a vendor's advice can be
       shorter than the lock floor it also documented.
    2. **``beyond_cap``** - the vendor stated a wait this room will not take on
       its own. Nothing is scheduled; the batch waits for a person.
    3. **``lock_floor``** - the vendor's prose floor for this class (HubSpot's two
       seconds on a 423) has not been met by the ladder.
    4. **``backoff``** - the ladder, with deterministic jitter from ``seed``.

    Every branch returns ``{"seconds", "source", "at", "basis"}`` whether or not
    a wait applies, so a caller never has to test for the key.
    """
    moment = now or datetime.now(timezone.utc)
    made = max(1, int(attempt))
    ladder = backoff_seconds(made)
    kind = str(getattr(signal, "kind", "") or "")
    # The floor is the larger of two numbers, and both are floors rather than
    # ceilings: the signal's own ``minimum_delay_seconds`` (the sentence the
    # vendor wrote about this class) and the connection's ``lock_floor_seconds``
    # (the same vendor's documented floor for its locks). Taking only the signal's
    # would ignore a connection that has been told a stricter floor, and taking
    # only the policy's would ignore a class that documented its own.
    floor = max(
        int(getattr(signal, "minimum_delay_seconds", 0) or 0),
        int(lock_floor_seconds or 0),
    )

    def answer(seconds: int | None, source: str, basis: str) -> dict[str, Any]:
        return {
            "seconds": seconds,
            "source": source,
            "at": iso(plus_seconds(moment, seconds)) if seconds is not None else None,
            "attempt": made,
            "ladder_seconds": ladder,
            "floor_seconds": floor,
            "basis": basis,
        }

    raw = header(headers, "Retry-After")

    # The cap is tested *before* the header is honoured, because
    # retry_after_seconds() has already clamped by the time it returns: a caller
    # that checked it first would see 86400 and never learn the vendor asked for
    # longer. Beyond the cap the room schedules nothing and hands the batch to a
    # person, so the order decides whether that ever happens.
    if raw and exceeds_cap(raw, cap=cap):
        return answer(
            None,
            "beyond_cap",
            f'the vendor sent "Retry-After: {raw}", which is longer than the {cap}s this room '
            "will wait on its own, so the batch waits for a person instead of cycling",
        )

    asked = retry_after_seconds(headers, now=moment, cap=cap)
    if asked is not None:
        wait = asked
        raised_by_floor = False
        if floor and kind != "migration" and asked < floor:
            # The vendor's own advice is shorter than the floor it also documented
            # for this class. Taking the shorter one is a bet that the condition has
            # already ended, and the cost of being wrong is another refused call.
            wait = floor
            raised_by_floor = True
        basis = f'the vendor sent "Retry-After: {raw}", so the room waits {wait}s'
        if raised_by_floor:
            basis += (
                f", which is this class's documented {floor}s floor rather than the {asked}s it "
                "asked for"
            )
        elif wait != ladder:
            basis += f" rather than the ladder's {ladder}s"
        return answer(wait, "retry_after", basis)

    if floor and ladder < floor:
        return answer(
            floor,
            "lock_floor",
            f"this class is documented with a {floor}s floor, which is longer than the ladder's "
            f"{ladder}s at attempt {made}, so the vendor's floor wins",
        )

    fixed = int(ladder * (1.0 - JITTER_RATIO))
    spread = int(ladder * JITTER_RATIO)
    wait = min(MAX_SECONDS, fixed + int(jitter_fraction(seed or str(made)) * spread))
    return answer(
        wait,
        "backoff",
        f"attempt {made} of a ladder from {BASE_SECONDS}s doubling to {MAX_SECONDS}s, with the "
        f"jitter derived from this batch's own idempotency key so the wait is the same every time "
        f"it is read",
    )


def check_attempts(attempts: Any, *, maximum: int = MAX_ATTEMPTS) -> bool:
    """Whether the automatic queue may send this batch again.

    ``False`` is not a dead end: the researched automation waits for "admin action"
    for anything it will not retry, and a manual retry is not capped.
    """
    try:
        made = int(attempts)
    except (TypeError, ValueError):
        made = 0
    return made < max(0, int(maximum))


def describe() -> dict[str, Any]:
    """The ladder and its provenance, for ``GET /vocabulary``."""
    return {
        "base_seconds": BASE_SECONDS,
        "max_seconds": MAX_SECONDS,
        "max_attempts": MAX_ATTEMPTS,
        "label": LABEL,
        "jitter_ratio": JITTER_RATIO,
        "jitter_basis": "derived from the batch's idempotency key, so the same batch always waits "
        "the same number of seconds",
        "retry_after_cap_seconds": RETRY_AFTER_CAP_SECONDS,
        "sources": list(SOURCES),
        "rungs": {str(attempt): backoff_seconds(attempt) for attempt in range(1, 9)},
    }


__all__ = [
    "BASE_SECONDS",
    "MAX_SECONDS",
    "MAX_ATTEMPTS",
    "LABEL",
    "JITTER_RATIO",
    "SOURCES",
    "backoff_seconds",
    "jitter_fraction",
    "retry_after_seconds",
    "exceeds_cap",
    "schedule",
    "check_attempts",
    "describe",
]
