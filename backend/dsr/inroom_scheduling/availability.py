"""The slot grid: what ``GET /v2/slots`` answers, and what it refuses.

The researched shape, verbatim from the research's ``apis_hit``:

    ``GET /v2/slots?eventTypeId=…&start=…&end=…&timeZone=…`` (also
    ``eventTypeSlug``+``username``+``organizationSlug``, or ``usernames=alice,bob``
    for dynamic multi-person slots, or ``teamSlug`` for team events) - header
    ``cal-api-version: 2024-09-04``.

Three rules in here are the research's rather than this build's, and each is
separately testable:

**A query names exactly one thing to ask about.** Four selector shapes are
documented, and naming two of them - an ``eventTypeId`` *and* a ``username``, or a
``teamSlug`` *and* a ``usernames`` list - cannot be resolved to one schedule.
Naming none is the same failure, and both are a refusal rather than a guess,
because a slot grid answered from the wrong event type books the meeting with the
wrong host.

**A dynamic query needs two or more names.** "Checking slots by usernames is used
mainly for dynamic events where there is no specific event but we just want to
know when 2 or more people are available." One name is not that feature: it is
the plain personal case spelled oddly, and answering it as a dynamic query would
report a slot available when only one person is free.

**A rescheduled booking does not block itself.** "``bookingUidToReschedule``:
When rescheduling an existing booking, provide the booking's unique identifier to
exclude its time slot from busy time calculations." Without the exclusion a
booking could never be moved to a time it already occupies, and to any time
shortly after it, because its own length is in its own busy set.

Everything else in this module - the reason a slot is unavailable, the label in
the caller's zone - exists so a refused slot is still answerable. A grid that
says "no" without saying why is one a prospect cannot act on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from dsr.inroom_scheduling.errors import SchedulingError, SelectorError, SlotUnavailable
from dsr.inroom_scheduling.schedules import (
    candidate_starts,
    iso,
    merge_ranges,
    overlaps,
    parse_instant,
    resolve_zone,
)
from dsr.inroom_scheduling.vocabulary import (
    MIN_DYNAMIC_USERNAMES,
    RESCHEDULE_PARAM,
    SLOT_SELECTORS,
)

#: Why a slot is not on offer. A closed vocabulary on purpose: the page renders a
#: message per reason, and a free-text reason would be a message nobody wrote.
UNAVAILABLE_REASONS: tuple[str, ...] = ("held", "booked", "seats", "hosts")

#: The researched field that excludes a rescheduled booking from busy time. The
#: camelCase the research writes, accepted under either spelling, because a client
#: copying a Cal request will send the researched one.
_RESCHEDULE_KEYS = (RESCHEDULE_PARAM, "booking_uid_to_reschedule", "reschedule")


def _first(params: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if params.get(key) not in (None, ""):
            return params[key]
    return None


@dataclass(frozen=True)
class Selector:
    """Which of the four researched ways of naming a schedule this query uses.

    ``event_type_id`` and ``slug``/``username``/``team_slug`` resolve to exactly
    one stored event type. ``usernames`` names no event type at all - the research
    says a dynamic query exists "where there is no specific event" - so it carries
    the host names and the caller assembles a schedule for them.
    """

    kind: str
    event_type_id: str | None = None
    slug: str | None = None
    username: str | None = None
    team_slug: str | None = None
    organization_slug: str | None = None
    usernames: tuple[str, ...] = ()

    @property
    def dynamic(self) -> bool:
        """The research's dynamic multi-person case."""
        return self.kind == "usernames"


def read_selector(params: Mapping[str, Any]) -> Selector:
    """Read the selector out of a query, refusing anything ambiguous.

    The four shapes the research documents, and the rule that exactly one of them
    must be present. Ambiguity is the dangerous case rather than absence: a query
    carrying both an ``eventTypeId`` and a ``usernames`` list looks plausible and
    would silently answer about the event type while a reader believes it answered
    about the people.

    Two half-selectors are refused too, because a bare ``username`` or ``teamSlug``
    is not one of the four documented shapes. Left alone it would be ignored next to
    an ``eventTypeId`` and read as agreement, when in fact the caller meant a
    different event type and got the one they named by coincidence.
    """
    event_type_id = _first(params, "eventTypeId", "event_type_id", "eventTypeID")
    slug = _first(params, "eventTypeSlug", "event_type_slug", "slug")
    username = _first(params, "username", "userName")
    team_slug = _first(params, "teamSlug", "team_slug")
    organization = _first(params, "organizationSlug", "organization_slug")
    raw_usernames = _first(params, "usernames", "userNames")

    named: list[str] = []
    if event_type_id is not None:
        named.append("event_type_id")
    if slug is not None and username is not None:
        named.append("username")
    if slug is not None and team_slug is not None:
        named.append("team_slug")
    if raw_usernames is not None:
        named.append("usernames")

    if username is not None and team_slug is not None:
        raise SelectorError(
            "a query cannot name both a username and a teamSlug. The research reaches an event type by "
            "eventTypeSlug+username+organizationSlug or by teamSlug, which are two different event "
            "types"
        )
    if slug is None:
        stray = [
            name
            for name, value in (("username", username), ("teamSlug", team_slug))
            if value is not None
        ]
        if stray:
            # Checked before the count, because a bare username beside an
            # eventTypeId is the case that matters: it would be ignored, and read
            # as agreement, when the caller meant a different event type.
            raise SelectorError(
                f"{' and '.join(stray)} is only half a selector. The research reaches an event type by "
                "eventTypeSlug+username+organizationSlug or by teamSlug, so a slug is required, and an "
                "eventTypeId or a usernames list is the other way to name one"
            )

    if len(named) > 1:
        raise SelectorError(
            "a slot query names exactly one of "
            + ", ".join(SLOT_SELECTORS)
            + f"; got {', '.join(sorted(named))}"
        )
    if not named:
        if slug is not None:
            raise SelectorError(
                "a slug must be paired with a username or a teamSlug; the research "
                "documents eventTypeSlug+username+organizationSlug and teamSlug"
            )
        raise SelectorError("a slot query must name one of " + ", ".join(SLOT_SELECTORS))

    kind = named[0]
    if kind == "usernames":
        names: list[str] = []
        for chunk in str(raw_usernames).split(","):
            name = chunk.strip()
            if name and name not in names:
                names.append(name)
        if len(names) < MIN_DYNAMIC_USERNAMES:
            raise SelectorError(
                "a usernames query is 'used mainly for dynamic events where there is "
                f"no specific event but we just want to know when {MIN_DYNAMIC_USERNAMES} "
                f"or more people are available'; it needs at least "
                f"{MIN_DYNAMIC_USERNAMES} names, got {len(names)}"
            )
        return Selector(kind=kind, usernames=tuple(names), organization_slug=organization)

    return Selector(
        kind=kind,
        event_type_id=str(event_type_id) if event_type_id is not None else None,
        slug=str(slug) if slug is not None else None,
        username=str(username) if username is not None else None,
        team_slug=str(team_slug) if team_slug is not None else None,
        organization_slug=organization,
    )


def reschedule_uid(params: Mapping[str, Any]) -> str | None:
    """The ``bookingUidToReschedule`` in a query, under either spelling."""
    value = _first(params, *_RESCHEDULE_KEYS)
    return str(value) if value is not None else None


def busy_intervals(
    entries: Sequence[Mapping[str, Any]],
    *,
    exclude_booking_uid: str | None = None,
) -> dict[str, list[tuple[datetime, datetime]]]:
    """Busy time per host, from bookings and live holds, with one exclusion.

    ``entries`` are the things that occupy a host: anything with ``host``,
    ``start`` and ``end``. ``exclude_booking_uid`` is the researched
    ``bookingUidToReschedule``, and the entry whose ``uid`` matches it is dropped
    before anything else happens.

    Dropping it here rather than at the comparison is the point. A booking is
    ``length_minutes`` long, so if its own interval stayed in the busy set a
    reschedule could never target the time it currently occupies, and every
    slot inside its own duration would read as busy. That is the failure the
    field exists to prevent, so it is prevented in one place and tested there.

    Ranges are merged per host, because a hold and a booking can cover the same
    minutes and a slot test wants one sorted list, not a set of puzzles.
    """
    per_host: dict[str, list[tuple[datetime, datetime]]] = {}
    for entry in entries:
        uid = entry.get("uid")
        if exclude_booking_uid is not None and uid is not None and str(uid) == exclude_booking_uid:
            continue
        host = str(entry.get("host") or "").strip()
        if not host:
            continue
        start = entry.get("start")
        end = entry.get("end")
        if start in (None, "") or end in (None, ""):
            continue
        try:
            span = (parse_instant(start, field="start"), parse_instant(end, field="end"))
        except SchedulingError:
            # A row this product wrote cannot be unparseable; a row that somehow
            # is should be skipped rather than take a whole listing down.
            continue
        per_host.setdefault(host, []).append(span)
    return {host: merge_ranges(spans) for host, spans in per_host.items()}


@dataclass
class Occupancy:
    """The two things that are not a host's working hours: holds and seats.

    Kept apart from :func:`busy_intervals` because they answer different
    questions. Busy time is about a host; a hold is about a slot, and a seat is
    about how many people can be in the room. A slot held by a rescheduling
    prospect is not a busy host, and treating it as one would make the hold block
    the very slot it is protecting.
    """

    held_by: dict[str, str] = field(default_factory=dict)
    seat_usage: dict[str, int] = field(default_factory=dict)


def slot_grid(
    *,
    hosts: Sequence[Mapping[str, Any]],
    length_minutes: int,
    window: tuple[datetime, datetime],
    now: datetime,
    time_zone: str,
    busy: Mapping[str, list[tuple[datetime, datetime]]],
    occupancy: Occupancy,
    event_type_id: str | None,
    seats: int | None = None,
    min_available_hosts: int = 1,
) -> list[dict[str, Any]]:
    """Every candidate start in the window, with why it is or is not bookable.

    The grid is generated from the hosts' working hours and then marked, never the
    other way round. Generating only free slots would be a shorter function and a
    worse product: "09:00 is not available because Priya already has a booking" and
    "09:00 is not available because the event type has no seats left" are different
    answers and a prospect choosing between tomorrow and next week needs both.

    ``min_available_hosts`` is the dynamic case: the research asks for slots "when
    2 or more people are available", so a slot is available when at least that
    many named hosts are free, and the ones that are free are named on the slot so
    the page can say who.
    """
    zone = resolve_zone(time_zone)
    required = max(1, int(min_available_hosts))
    length_minutes = _require_positive(length_minutes)
    slots: list[dict[str, Any]] = []

    for start in candidate_starts(hosts, length_minutes=length_minutes, window=window, now=now):
        end = start + timedelta(minutes=length_minutes)
        free_hosts = [
            str(host["username"])
            for host in hosts
            if not any(overlaps((start, end), span) for span in busy.get(str(host["username"]), []))
        ]
        key = iso(start)
        holder = occupancy.held_by.get(key)
        taken_seats = int(occupancy.seat_usage.get(key, 0))
        seats_left = (seats - taken_seats) if seats is not None else None

        if holder is not None:
            reason = "held"
        elif len(free_hosts) < required:
            reason = "hosts"
        elif seats_left is not None and seats_left <= 0:
            reason = "seats"
        else:
            reason = None

        slots.append(
            {
                "start": key,
                "end": iso(end),
                "event_type_id": event_type_id,
                "label": start.astimezone(zone).strftime("%H:%M"),
                "available": reason is None,
                "reason": reason,
                "held_by": holder,
                "hosts_available": free_hosts,
                "hosts_required": required,
                "seats_left": seats_left,
                "duration_minutes": length_minutes,
            }
        )
    return slots


def _require_positive(length_minutes: int) -> int:
    if int(length_minutes) < 1:
        raise SchedulingError("an event type's length must be at least one minute")
    return int(length_minutes)


def first_free(slots: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The earliest bookable slot, or ``None``.

    This is what an instant booking with no ``start`` takes: "``instant``: true"
    on a team event means the meeting happens now-ish, so the product picks the
    soonest slot it can rather than refusing for want of a timestamp the caller
    could not have known.
    """
    for slot in slots:
        if slot.get("available"):
            return dict(slot)
    return None


def require_bookable(
    slots: Sequence[Mapping[str, Any]], start: str, *, field: str = "start"
) -> dict[str, Any]:
    """The slot at ``start``, or a refusal carrying the alternatives.

    Two different failures, told apart because they need different answers from
    the page. A start that is not on the grid at all is the caller's to fix and
    answers 400. A start that is on the grid but unavailable is a legitimate
    state of the data, so the refusal names the reason and offers the next free
    slots - the booked/expired cases are the ones the researched flow actually
    produces, and a bare 400 would throw away the recovery the grid already has.
    """
    wanted = iso(parse_instant(start, field=field))
    for slot in slots:
        if slot["start"] == wanted:
            if slot.get("available"):
                return dict(slot)
            alternatives = [dict(entry) for entry in slots if entry.get("available")][:3]
            raise SlotUnavailable(
                f"{field} {wanted} is not bookable ({slot.get('reason')})",
                reason=str(slot.get("reason")),
                slot=dict(slot),
                alternatives=alternatives,
            )
    raise SlotUnavailable(
        f"{field} {wanted} is not one of the offered slots",
        reason="not_offered",
        slot={"start": wanted},
        alternatives=[dict(entry) for entry in slots if entry.get("available")][:3],
    )


def selector_label(selector: Selector) -> str:
    """A one-line description of what a query asked about, for the audit trail.

    Recorded on the slot read so that "which event type was this grid for" is
    answerable after the fact without reconstructing it from a URL.
    """
    if selector.dynamic:
        return "usernames=" + ",".join(selector.usernames)
    if selector.kind == "event_type_id":
        return f"eventTypeId={selector.event_type_id}"
    if selector.kind == "username":
        return f"eventTypeSlug={selector.slug}&username={selector.username}"
    return f"eventTypeSlug={selector.slug}&teamSlug={selector.team_slug}"


def as_utc(moment: datetime) -> datetime:
    """A moment in UTC. The one form every comparison in this package happens in.

    Every interval is stored and compared as UTC, and a zone only ever appears at
    the two edges: turning a host's working hours into intervals, and rendering a
    label. That is what keeps a half-hour zone offset from producing an
    off-by-one-hour grid.
    """
    return moment.astimezone(timezone.utc)
