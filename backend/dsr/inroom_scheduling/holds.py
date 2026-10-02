"""The slot hold: ``POST /v2/slots/reservations`` and the three operations on it.

Everything here comes from two sentences the research quotes.

The first is the automation: "no user action needed for the hold to expire - a
reservation auto-expires after ``reservationDuration``." That single sentence
decides three things and they are worth stating separately, because the third is
the one a reviewer will look for.

**Expiry is a function of time, not of a sweep.** A hold is live exactly while
``now < reservationUntil``. Nothing has to notice, run, or be scheduled for the
hold to stop being live; the answer is the same on every read, at any moment, with
no background job. :func:`live_state` is that function, and it takes the moment
as an argument so a test can be certain about the boundary rather than sleeping.

**A read reports the state it computes.** Listing holds therefore reports an
expired hold as ``expired`` even if the stored row still says ``held``, and the
engine rewrites the row on the next read so the stored answer converges. The
audit row records the moment the product noticed, which is a real fact and not the
same as the moment the hold lapsed - both timestamps are kept.

**A hold is a claim on a slot, not on a host.** It is keyed by the slot's start,
so a prospect rescheduling their own booking does not block the very slot they are
moving to, and two holds on different slots of the same hour do not collide. That
is also why ``require_bookable`` in :mod:`dsr.inroom_scheduling.availability`
consults holds separately from busy time.

The second sentence gives the default: "you can also specify custom duration for
how long the slot should be reserved for (defaults to 5 minutes)". The duration
belongs on the hold rather than on the embed, so a bespoke flow can ask for
longer than five minutes without a configuration change, which is what "custom
duration" is for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping

from dsr.inroom_scheduling.errors import HoldExpired, SchedulingError, SlotUnavailable
from dsr.inroom_scheduling.schedules import iso, parse_instant
from dsr.inroom_scheduling.vocabulary import (
    DEFAULT_RESERVATION_DURATION_MINUTES,
    RESERVATION_RESPONSE_FIELDS,
)

#: The four states. ``held`` and ``expired`` are the interesting pair: both are
#: "this hold does not protect the slot", for different reasons, and only one of
#: them is anybody's fault.
HELD = "held"
CONSUMED = "consumed"
RELEASED = "released"
EXPIRED = "expired"

#: The states in which a hold no longer protects its slot. ``expired`` is here
#: because the research's own sentence puts it there, not because this build chose
#: to.
DEAD_STATES: frozenset[str] = frozenset({CONSUMED, RELEASED, EXPIRED})

#: The longest a hold may be kept alive in one request, in minutes. The research
#: documents a default of five and says a custom duration may be given, but sets
#: no ceiling - so this build needs one, and needs it to be visible.
MAX_RESERVATION_DURATION_MINUTES = 60


def reservation_uid(seed: Mapping[str, Any], *, now: datetime) -> str:
    """A deterministic-looking uid for a hold.

    Derived from the slot, the host and the moment rather than random, so a seed
    run twice produces the same demo and a test can assert on a uid it built. Not
    a security property and not claimed to be one: a hold uid is not a credential,
    it names a claim inside this product's own store.
    """
    import hashlib

    material = f"{seed.get('event_type_id')}|{seed.get('start')}|{seed.get('host')}|{iso(now)}"
    return "rsv_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def normalise_duration(value: Any) -> int:
    """The hold duration, defaulting to the researched five minutes.

    Whole minutes, and refused rather than clamped outside
    :data:`MAX_RESERVATION_DURATION_MINUTES`. A hold nobody releases and no clock
    is checked against would be a slot that silently never frees, so a ceiling is
    the safer reading of "custom duration".
    """
    if value in (None, ""):
        return DEFAULT_RESERVATION_DURATION_MINUTES
    try:
        minutes = int(value)
    except (TypeError, ValueError) as exc:
        raise SchedulingError("reservationDuration must be a whole number of minutes") from exc
    if minutes < 1:
        raise SchedulingError("reservationDuration must be at least one minute")
    if minutes > MAX_RESERVATION_DURATION_MINUTES:
        raise SchedulingError(
            f"reservationDuration may not exceed {MAX_RESERVATION_DURATION_MINUTES} minutes; "
            "the research documents a default of five and no ceiling"
        )
    return minutes


def reservation_until(now: datetime, duration_minutes: int) -> datetime:
    """The researched ``reservationUntil``: the moment the hold lapses."""
    return now + timedelta(minutes=duration_minutes)


@dataclass(frozen=True)
class HoldView:
    """A hold read at a moment, with the state that moment implies.

    The stored row is not always the answer. A hold written a minute ago says
    ``held``; read six minutes later with the researched default of five, it is
    ``expired`` - and the read is what makes that true, without anybody acting.
    """

    uid: str
    state: str
    start: datetime
    until: datetime
    duration_minutes: int
    event_type_id: str | None
    host: str | None
    stored_state: str
    expired_now: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "reservationUid": self.uid,
            "reservationDuration": self.duration_minutes,
            "reservationUntil": iso(self.until),
            "start": iso(self.start),
            "state": self.state,
            "stored_state": self.stored_state,
            "expired_now": self.expired_now,
            "event_type_id": self.event_type_id,
            "host": self.host,
        }

    @property
    def live(self) -> bool:
        return self.state == HELD


def read_hold(record: Mapping[str, Any], *, now: datetime) -> HoldView:
    """A stored hold, read at ``now``.

    Three outcomes, in this order:

    1. the row already names a terminal state, and that stands;
    2. the row is ``held`` and the clock is past ``reservationUntil``, so the state
       *is* ``expired`` even though nobody wrote it;
    3. otherwise the row's state stands.

    A ``consumed`` hold is never retroactively expired. It already stopped
    protecting the slot by being taken, and relabelling it would rewrite history.
    """
    data = dict(record.get("data") or {})
    stored = str(data.get("state") or HELD)
    start = parse_instant(data.get("start"), field="reservation.start")
    until = parse_instant(data.get("reservationUntil"), field="reservation.reservationUntil")
    duration = int(data.get("reservationDuration") or DEFAULT_RESERVATION_DURATION_MINUTES)

    expired_now = stored == HELD and now >= until
    state = EXPIRED if expired_now else stored
    return HoldView(
        uid=str(data.get("reservationUid") or record.get("id")),
        state=state,
        start=start,
        until=until,
        duration_minutes=duration,
        event_type_id=data.get("event_type_id"),
        host=data.get("host"),
        stored_state=stored,
        expired_now=expired_now,
    )


def live_holds(records: list[Mapping[str, Any]], *, now: datetime) -> dict[str, HoldView]:
    """Every hold that still protects its slot, keyed by the slot's start.

    Keyed by start rather than by uid because that is the question being asked:
    "is this slot held?". Two holds on two different slots in the same hour are
    two claims and neither shadows the other; two holds on the same slot are the
    same claim, and the caller resolves that separately.
    """
    live: dict[str, HoldView] = {}
    for record in records:
        view = read_hold(record, now=now)
        if view.live:
            live[iso(view.start)] = view
    return live


def expired_uids(records: list[Mapping[str, Any]], *, now: datetime) -> list[str]:
    """The uids of holds whose stored state has fallen behind the clock.

    Returned rather than written, so the engine can decide whether the read that
    found them should also correct the row. A read that silently rewrote history
    would be worse than one that reported it.
    """
    return [
        str((record.get("data") or {}).get("reservationUid") or record.get("id"))
        for record in records
        if read_hold(record, now=now).expired_now
    ]


def require_live(view: HoldView) -> HoldView:
    """The hold, or a refusal that says which of the two dead states it is in.

    ``HoldExpired`` for a hold the clock retired, because the researched flow is
    "hold the slot, then submit the booking" and the recovery is to pick another
    slot. A *consumed* hold gets ``SlotUnavailable`` instead, because retrying with
    a fresh slot is the wrong advice: the meeting exists, at that time, on that
    event type, and the caller is looking at a second submission of the same one.
    """
    if view.live:
        return view
    if view.state == EXPIRED:
        raise HoldExpired(
            f"reservation {view.uid} expired at {iso(view.until)}; "
            "a reservation auto-expires after reservationDuration, so pick another slot",
            uid=view.uid,
            until=iso(view.until),
            alternatives=[],
        )
    raise SlotUnavailable(
        f"reservation {view.uid} is {view.state} and no longer protects the slot",
        reason=str(view.state),
        slot={"start": iso(view.start), "reservationUid": view.uid},
    )


def new_hold_payload(
    *,
    event_type_id: str | None,
    start: str,
    host: str | None,
    duration_minutes: int,
    now: datetime,
    uid: str,
    room_id: str | None,
    attendee: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The body of a new hold, in the researched response field names.

    ``reservationUid``, ``reservationDuration`` and ``reservationUntil`` are the
    three fields the research says ``POST /v2/slots/reservations`` returns, and they
    are spelled exactly that way on the stored record. A reviewer matching this
    against the source should not have to hold a snake_case map in their head to
    do it.
    """
    until = reservation_until(now, duration_minutes)
    payload: dict[str, Any] = {
        "reservationUid": uid,
        "reservationDuration": duration_minutes,
        "reservationUntil": iso(until),
        "start": iso(parse_instant(start, field="start")),
        "event_type_id": event_type_id,
        "host": host,
        "room_id": room_id,
        "state": HELD,
        "reserved_at": iso(now),
        "response_fields": list(RESERVATION_RESPONSE_FIELDS),
    }
    if attendee:
        payload["attendee"] = dict(attendee)
    return payload
