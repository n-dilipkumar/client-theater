"""The two-step session, and the four researched rules that govern it.

This module is the reason the workflow is not a single call. The research is
unusually emphatic about it, and every one of these four rules is a rule a naive
implementation gets wrong:

**Single-use.**  "Sessions are single-use." A ``routeId`` may commit exactly one
meeting. :meth:`Session.consume` is the only path to a terminal state, and it
refuses to be called twice.

**No retry on failure.**  "On a schedule failure, do not retry the schedule call
with the same ``routeId`` - start again from the discover or route step." This is
the half that is easy to get wrong, and getting it wrong is a production bug
rather than a test failure: an implementation that consumes a session only on
*success* lets a caller retry forever against a session whose slots are stale.
So a *failed* schedule call consumes the session too. The remedy the research
names - go back to step 1 - is not a suggestion about a particular failure; it is
the rule.

**Short-lived.**  A server-side TTL, and Concierge's is settable per request as
``timeoutInMS`` while links and handoff use a server-side one. An expired session
cannot be booked.

**UTC, verbatim.**  "Slot times are UTC. The ``startTime`` in responses is
ISO-8601 UTC; pass it back verbatim on the book call." Both halves are enforced.
:func:`parse_start_time` refuses a value with no UTC designator rather than
guessing an offset, and :meth:`Session.resolve_slot` requires the caller's value
to be *one of the strings this session returned* - so a caller cannot round a
timestamp and believe it booked the slot they saw.

Why a path list, when the researched flow describes a flat one
-------------------------------------------------------------

The WF-056 flow says call #1 "returns a ``routeId`` and a list of ``startTimes``
under ``schedulingData``". The handoff endpoint in the same research returns
"``routingId`` + ``routers[].pathResults[].startTimes``" - one list per routing
path, because the handoff schedule URL carries a ``pathId`` that Concierge's and
Links' do not. So :class:`Session` holds a list of paths, and the two other
surfaces hold exactly one with ``path_id`` of ``None``. A flat list would have
been simpler and would have quietly refused every handoff booking that named a
path, which is the shape the research says handoff callers use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from dsr.headless_booking.availability import (
    DEFAULT_MEETING_MINUTES,
    DEFAULT_SLOT_FALLS_BACK_TO_MEETING,
    DEFAULT_SLOT_MINUTES,
    Busy,
    format_slot,
    parse_instant,
    slots as compute_slots,
)
from dsr.headless_booking.errors import HeadlessBookingError, refusal as _path_refusal
from dsr.headless_booking.vocabulary import (
    DEFAULT_TTL_MS,
    MAX_TTL_MS,
    MIN_TTL_MS,
    SECTIONS,
    TERMINAL_SESSION_STATES,
)


@dataclass(frozen=True)
class PathSlots:
    """One routing path's offered slots. ``path_id`` is ``None`` off handoff."""

    path_id: str | None
    label: str
    start_times: tuple[str, ...]


@dataclass
class Session:
    """A discovered routing session: a single-use handle on a slot list."""

    route_id: str
    section: str
    asset_id: str
    room_id: str | None
    #: One entry for Concierge and Links, one per routing path for Handoff.
    paths: list[PathSlots]
    created_at: datetime
    expires_at: datetime
    state: str = "open"
    #: Why a session reached a terminal state, for the caller's next step.
    outcome: str = ""
    detail: str = ""
    timeout_in_ms: int = 0
    interval_starts_at: str = ""
    interval_duration_minutes: int = 0
    guest_email: str = ""
    #: The credential this session was opened with, if any. ``None`` means the
    #: call was made as the installation, which the record says rather than
    #: leaving a reader to guess.
    credential_id: str | None = None
    authorised_as: str = "installation"
    busy_considered: int = 0

    # -- the four rules ----------------------------------------------------- #

    def is_expired(self, now: datetime) -> bool:
        """Rule 3: the TTL is server-side and an expired session is dead.

        ``>=`` rather than ``>``: a TTL of zero milliseconds would be alive for
        exactly one instant under ``>``, and a caller must not be able to book a
        session whose whole life has passed.
        """
        return now >= self.expires_at

    def is_open(self, now: datetime) -> bool:
        return self.state == "open" and not self.is_expired(now)

    def consume(
        self, now: datetime, state: str, detail: str = "", reason: str = ""
    ) -> None:
        """Move to a terminal state. Refuses a second transition.

        This is the single-use rule, and it is enforced in one place so there is
        no way to book a session that was already used: the method is the only
        writer of ``state`` and it raises if the session is not open.

        ``state`` and ``reason`` are separate on purpose. ``state`` is one of the
        four published session states - ``booked``, ``failed`` or ``expired`` -
        and ``reason`` is the researched *why* from
        :data:`~dsr.headless_booking.vocabulary.SCHEDULE_FAILURES`
        (``start_time_not_offered``, ``slot_taken``, and so on). Conflating them
        would put eight failure reasons into a three-value state machine, and
        every summary counting "failed" would be a summary counting one
        particular failure.
        """
        if state not in TERMINAL_SESSION_STATES:
            raise HeadlessBookingError(
                f"{state!r} is not a terminal session state; expected one of "
                f"{', '.join(TERMINAL_SESSION_STATES)}"
            )
        if self.state != "open":
            raise HeadlessBookingError(
                f"session {self.route_id} is already {self.state!r} "
                f"({self.outcome or 'no reason recorded'}). Sessions are single-use: start again "
                "from the discover or route step with a fresh session rather than retrying this one."
            )
        if self.is_expired(now):
            self.mark_expired(now, detail)
            raise _path_refusal(
                "session_expired",
                f"session {self.route_id} expired at {format_slot(self.expires_at)}; "
                "re-run the discover or route call for a fresh session",
            )
        self.state = state
        self.outcome = reason or state
        self.detail = detail

    def mark_expired(self, now: datetime, detail: str = "") -> None:
        """Record the expiry without raising.

        Split out from :meth:`consume` because ``consume`` raises, and a caller
        that needs to *persist* the expiry before refusing would otherwise lose
        it: the state is set in memory and then the exception unwinds past the
        write. An early version of this had exactly that bug, and the symptom was
        a session that read as ``open`` forever after its TTL had passed - so the
        session list showed expired sessions as bookable.
        """
        self.state = "expired"
        self.outcome = "session_expired"
        self.detail = detail or "the session's server-side TTL elapsed before the book call"

    # -- the UTC / verbatim rule -------------------------------------------- #

    def resolve_slot(
        self, start_time: Any, path_id: str | None
    ) -> tuple[str, datetime, PathSlots, str]:
        """Rule 4: the caller's ``startTime`` must be one of ours, verbatim.

        Returns ``(canonical, instant, path, wire_value)``. The canonical string
        and its parsed instant are the session's own; the wire value is exactly
        what the caller sent, kept so the meeting record can show whether it was
        byte-identical.

        The comparison is on the *canonical* form, so ``+00:00`` and ``Z`` are
        the same instant and both work, while a naive value is refused outright:
        a string with no designator has no single instant, and picking one for
        the caller would be the failure the researched rule exists to prevent.
        """
        instant = parse_instant(start_time, field="startTime")
        canonical = format_slot(instant)
        if self.section == "handoff":
            chosen = next((entry for entry in self.paths if entry.path_id == path_id), None)
            if chosen is None:
                known = ", ".join(str(entry.path_id) for entry in self.paths)
                raise _path_refusal(
                    "path_unknown",
                    f"pathId {path_id!r} is not one of session {self.route_id}'s routing paths "
                    f"({known or 'none'})",
                )
        else:
            if path_id not in (None, ""):
                raise _path_refusal(
                    "path_not_offered",
                    f"a {self.section!r} session has one set of start times, not a pathId to "
                    "honour: only the handoff schedule URL carries {pathId}",
                )
            chosen = self.paths[0]
        if canonical not in chosen.start_times:
            raise _path_refusal(
                "start_time_not_offered",
                f"startTime {start_time!r} is not one of the slots this session returned. Slot "
                "times are UTC and must be passed back verbatim; pick a value from the "
                f"schedulingData of session {self.route_id}.",
            )
        return canonical, instant, chosen, str(start_time)

    # -- projections --------------------------------------------------------- #

    @property
    def all_start_times(self) -> list[str]:
        """Every slot this session offered, across every path, in order."""
        return [value for entry in self.paths for value in entry.start_times]

    @property
    def slot_count(self) -> int:
        """How many slots this session offered, across every path."""
        return len(self.all_start_times)

    def scheduling_data(self) -> list[dict[str, Any]]:
        """The ``schedulingData`` the researched init call returns.

        One entry per path, each with its own ``pathId`` and ``startTimes``,
        which is the shape the handoff endpoint documents. For Concierge and
        Links the single entry carries ``pathId: null``, because those two
        schedule URLs have no ``{pathId}`` in them - so a client that reads
        ``pathId`` and sends it back gets the researched ``path_not_offered``
        refusal rather than a silently-ignored field.
        """
        return [
            {
                "pathId": entry.path_id,
                "label": entry.label,
                "startTimes": list(entry.start_times),
            }
            for entry in self.paths
        ]

    def ttl_ms(self) -> int:
        remaining = self.expires_at - self.created_at
        return int(remaining.total_seconds() * 1000)

    def next_step(self) -> str:
        """What a caller should do next, in words.

        The researched instruction is a *procedure*, and a caller holding a
        refusal has to be told it rather than left to infer it from a message.
        Every terminal state points back at call #1, which is what "start again
        from the discover or route step" means.
        """
        if self.state == "open":
            return (
                f"call the book endpoint with this routeId and one of the {len(self.all_start_times)} "
                "start times under schedulingData, passed back verbatim"
            )
        return (
            "re-run the discover or route call for a fresh session; sessions are single-use and "
            "this one is spent"
        )

    def to_dict(self) -> dict[str, Any]:
        """The stored payload. Envelope keys are deliberately *not* in it.

        The store owns the envelope: it strips ``created_at``, ``updated_at``,
        ``room_id`` and friends from whatever is written, so putting them here
        would mean they were silently dropped on the way in and had to be read
        back off the record. :meth:`to_payload` merges the two for a response.
        """
        return {
            "routeId": self.route_id,
            "routingId": self.route_id,
            "section": self.section,
            "asset_id": self.asset_id,
            "state": self.state,
            "outcome": self.outcome,
            "detail": self.detail,
            "schedulingData": self.scheduling_data(),
            "slot_count": self.slot_count,
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
            "expires_at": self.expires_at.isoformat().replace("+00:00", "Z"),
            "timeout_in_ms": self.timeout_in_ms,
            "interval_starts_at": self.interval_starts_at,
            "interval_duration_minutes": self.interval_duration_minutes,
            "guest_email": self.guest_email,
            "credential_id": self.credential_id,
            "authorised_as": self.authorised_as,
            "busy_considered": self.busy_considered,
        }

    def to_payload(self, record_id: str | None = None, room_id: str | None = None) -> dict[str, Any]:
        """:meth:`to_dict` plus the response-only projections."""
        return {
            **self.to_dict(),
            "id": record_id or self.route_id,
            "room_id": room_id if room_id is not None else self.room_id,
            "next_step": self.next_step(),
            "retry_with_same_route_id": self.state == "open",
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "Session":
        """Rebuild a session from its stored *envelope*.

        The envelope is where ``created_at`` and ``room_id`` actually live - the
        store strips them from ``data`` on write - so this reads the payload for
        the domain fields and the envelope for everything the store owns.

        The stored ``schedulingData`` is the authority, not the assets table: a
        session's slots are what *it* offered, and re-deriving them from a
        changed asset would let a config edit retroactively change what a caller
        was told.
        """
        data = dict(record.get("data") or {})
        created_raw = data.get("created_at") or record.get("created_at")
        return cls(
            route_id=str(data.get("routeId") or data.get("routingId") or record.get("id") or ""),
            section=str(data.get("section") or ""),
            asset_id=str(data.get("asset_id") or ""),
            room_id=record.get("room_id"),
            paths=[
                PathSlots(
                    path_id=entry.get("pathId"),
                    label=str(entry.get("label") or entry.get("pathId") or ""),
                    start_times=tuple(entry.get("startTimes") or ()),
                )
                for entry in (data.get("schedulingData") or [])
            ],
            created_at=parse_instant(created_raw, field="created_at"),
            expires_at=parse_instant(data.get("expires_at"), field="expires_at"),
            state=str(data.get("state") or "open"),
            outcome=str(data.get("outcome") or ""),
            detail=str(data.get("detail") or ""),
            timeout_in_ms=int(data.get("timeout_in_ms") or 0),
            interval_starts_at=str(data.get("interval_starts_at") or ""),
            interval_duration_minutes=int(data.get("interval_duration_minutes") or 0),
            guest_email=str(data.get("guest_email") or ""),
            credential_id=data.get("credential_id"),
            authorised_as=str(data.get("authorised_as") or "installation"),
            busy_considered=int(data.get("busy_considered") or 0),
        )


def parse_start_time(value: Any) -> str:
    """A caller's ``startTime``, canonicalised to the wire form, or refused.

    A value that parses but carries no UTC designator is refused with a message
    naming the researched rule. A value with a non-zero offset is *not* refused
    here - it is canonicalised, and then rejected by
    :meth:`Session.resolve_slot` because it is not one of the offered strings,
    which is the more useful message: the caller's instant is real, it is just
    not a slot.
    """
    if value in (None, ""):
        raise HeadlessBookingError(
            "startTime is required: pass back one of the start times under schedulingData, "
            "verbatim"
        )
    return format_slot(parse_instant(value, field="startTime"))


def resolve_timeout(section: str, timeout_in_ms: Any, now: datetime) -> tuple[int, str]:
    """The session's lifetime, and where the number came from.

    Concierge's TTL is the caller's to set, as ``timeoutInMS``. Links and handoff
    use a server-side one, so passing ``timeoutInMS`` for them is refused rather
    than ignored - a silently-ignored setting is the kind of thing a caller
    debugs for an afternoon.

    The bounds are unsourced and flagged as such. A session is a *hold* on
    availability, and a hold that outlives the interval it was computed for is a
    guarantee this layer cannot make.
    """
    if section not in SECTIONS:
        raise HeadlessBookingError(f"section {section!r} is not one of {', '.join(SECTIONS)}")
    if timeout_in_ms in (None, ""):
        resolved = DEFAULT_TTL_MS[section]
        source = "server default"
    else:
        if section != "concierge":
            raise HeadlessBookingError(
                f"timeoutInMS is a Concierge setting ('timeoutInMS for Concierge, per-router-path; "
                f"server-side TTL for links/handoff'), so it cannot be set on a {section!r} session"
            )
        try:
            resolved = int(timeout_in_ms)
        except (TypeError, ValueError) as exc:
            raise HeadlessBookingError(f"timeoutInMS {timeout_in_ms!r} is not a number of milliseconds") from exc
        if not MIN_TTL_MS <= resolved <= MAX_TTL_MS:
            raise HeadlessBookingError(
                f"timeoutInMS must be between {MIN_TTL_MS} and {MAX_TTL_MS}; a session is a hold on "
                "availability, and this layer will not hold one longer than that"
            )
        source = "caller"
    return resolved, source


def open_session(
    *,
    route_id: str,
    section: str,
    asset: Mapping[str, Any],
    room_id: str | None,
    window_start: datetime,
    window: timedelta,
    paths_spec: Sequence[Mapping[str, Any]],
    busy_by_key: Mapping[str, list[Busy]],
    booked_by_key: Mapping[str, list[tuple[datetime, datetime]]],
    now: datetime,
    timeout_in_ms: Any = None,
    guest_email: str = "",
    credential_id: str | None = None,
    authorised_as: str = "installation",
    max_slots: int | None = None,
) -> Session:
    """Compute the slot list and open a session on it.

    ``paths_spec`` is the asset's own path list, and an empty one becomes a
    single implicit path - which is how the two single-path surfaces are
    modelled without a special case in the slot loop.

    ``booked_by_key`` is separate from ``busy_by_key`` on purpose: a busy block
    comes from the host's calendar, while a booked meeting is something *this
    product* committed. Both make a host unavailable, and conflating them would
    make the difference between "the calendar says so" and "we already did this"
    unrecoverable from the record.
    """
    ttl_ms, _source = resolve_timeout(section, timeout_in_ms, now)
    if not paths_spec:
        paths_spec = [{}]

    paths: list[PathSlots] = []
    busy_count = 0
    for spec in paths_spec:
        from dsr.headless_booking.assets import host_calendar_key

        key = host_calendar_key(section, asset, spec or None)
        blocks = list(busy_by_key.get(key, []))
        for start, end in booked_by_key.get(key, []):
            blocks.append(Busy(starts_at=start, ends_at=end, label="already booked"))
        busy_count += len(blocks)
        config = dict(asset)
        if spec:
            # A routing path's own host, grid and hours win over the asset's:
            # the research says each path has its own availability, and a
            # technical-review path with a different length is the normal case
            # for a handoff router, not the exotic one.
            for field_name, value in spec.items():
                if field_name in ("path_id", "label", "note"):
                    continue
                config[field_name] = value
        meeting_minutes = int(config.get("duration_minutes") or DEFAULT_MEETING_MINUTES)
        found = compute_slots(
            window_start=window_start,
            window=window,
            busy=blocks,
            # The start grid defaults to the meeting length, so by default the
            # slot list cannot contain two starts that overlap. A deployment
            # that sets a finer grid gets overlapping starts on purpose, and
            # the second booking is refused as `slot_taken` rather than the
            # configuration being forbidden. See
            # availability.DEFAULT_SLOT_FALLS_BACK_TO_MEETING.
            slot_minutes=int(
                config.get("slot_minutes")
                or (meeting_minutes if DEFAULT_SLOT_FALLS_BACK_TO_MEETING else DEFAULT_SLOT_MINUTES)
            ),
            meeting_minutes=meeting_minutes,
            work_start_hour=int(config.get("work_start_hour") or 9),
            work_end_hour=int(config.get("work_end_hour") or 17),
            work_days=config.get("work_days") or [0, 1, 2, 3, 4],
            utc_offset_minutes=int(config.get("utc_offset_minutes") or 0),
            now=now,
            lead_minutes=int(config.get("lead_minutes") or 30),
            max_slots=int(max_slots or config.get("max_slots") or 40),
        )
        paths.append(
            PathSlots(
                path_id=(spec or {}).get("path_id"),
                label=str((spec or {}).get("label") or asset.get("name") or ""),
                start_times=tuple(found),
            )
        )

    return Session(
        route_id=route_id,
        section=section,
        asset_id=str(asset.get("id") or ""),
        room_id=room_id,
        paths=paths,
        created_at=now,
        expires_at=now + timedelta(milliseconds=ttl_ms),
        timeout_in_ms=ttl_ms,
        interval_starts_at=format_slot(window_start),
        interval_duration_minutes=int(window.total_seconds() // 60),
        guest_email=guest_email,
        credential_id=credential_id,
        authorised_as=authorised_as,
        busy_considered=busy_count,
    )


def require_open_session(session: Session, now: datetime) -> None:
    """Refuse a session that is spent or stale, naming the researched remedy."""
    if session.state in TERMINAL_SESSION_STATES:
        # Always `session_consumed`, whatever ended the session. The *reason* it
        # ended is on the session and in its detail, but the thing the caller
        # needs to branch on is "this routeId is spent", and that is the same
        # answer whichever way it was spent.
        raise _path_refusal(
            "session_consumed",
            f"session {session.route_id} is {session.state} ({session.outcome or 'no reason'}). "
            f"{session.detail or 'Sessions are single-use.'} "
            f"Next step: {session.next_step()}",
        )
    if session.is_expired(now):
        raise _path_refusal(
            "session_expired",
            f"session {session.route_id} expired at {format_slot(session.expires_at)}. "
            f"Next step: {session.next_step()}",
        )
