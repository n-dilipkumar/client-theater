"""The free/busy read, and the request that would carry it (WF-057).

This module is the researched part of the provider side, and it is deliberately
split from the slot arithmetic in :mod:`dsr.panel_time.slots`. That split *is* the
extensibility claim:

    "free/busy is decoupled from slot data - an integrator can layer their own
    scoring/ranking, house rules (no Friday afternoons, no back-to-back), or book
    into a room resource."

So nothing here knows what a candidate slot is. It answers one question - "which
of these calendars are busy when" - in a shape the slot side can intersect
against anything: a stored directory, a real Google response, or a busy map a
caller built by hand.

Three things live here:

:func:`render_free_busy_request`
    The researched request, per dialect. For Google: the exact body the research
    enumerates - ``{timeMin, timeMax, timeZone, groupExpansionMax,
    calendarExpansionMax, items[{id}]}`` - against the researched URL and
    scope. For Graph: the researched ``findMeetingTimes`` path, the
    ``Prefer: outlook.timezone`` header, and the researched body fields,
    including ``returnSuggestionReasons`` and ``minAttendeePercentage``.
:func:`expand_invited`
    Group expansion for whole distribution lists, bounded by the two researched
    capacity knobs. A group whose members cannot be expanded is *not* dropped:
    it becomes an unreadable calendar, so it scores the researched 49% and the
    panel learns why.
:class:`LocalDirectory`
    The provider that answers from stored calendar rows, so a demo and a test
    can exercise the whole flow with no socket and no network flake.
:class:`UrllibProvider`
    The real Google read, for a deployment that points a connector at a real
    tenant.

**A note on the two dialects, and the reading this build takes.** The research's
data flow is one pipeline - "provider free/busy read → per-attendee availability
(free=100%, unknown=49%, busy=0%) → averaged confidence score, sorted high→low
then chronologically" - and that is what both dialects run here. The research's
*Graph* endpoint is a different shape: ``findMeetingTimes`` returns suggestions
the provider has already ranked, not free/busy blocks. Rendering that request is
sourced and this module does it exactly; merging a provider's own
``meetingTimeSuggestions`` into this engine's shortlist is not something any cited
source describes, so it is not done, and the response says so rather than
quietly preferring one ranking over the other. :func:`UrllibProvider.send`
therefore refuses the Graph dialect by name instead of pretending to send it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence
from urllib import error as urlerror
from urllib import request as urlrequest

from dsr.panel_time.errors import CalendarShapeError, LimitExceeded, PanelTimeNotConfigured
from dsr.panel_time.timeutils import format_instant, parse_instant
from dsr.panel_time.vocabulary import (
    CALENDAR_EXPANSION_MAX_LIMIT,
    CALENDAR_EXPANSION_MAX_QUOTE,
    GOOGLE_CONFERENCE_SOLUTION_KEY,
    GOOGLE_EVENTS_PATH,
    GOOGLE_FREEBUSY_SCOPE,
    GOOGLE_FREEBUSY_URL,
    GRAPH_DELEGATED_SCOPE,
    GRAPH_FIND_MEETING_TIMES_BY_USER_PATH,
    GRAPH_FIND_MEETING_TIMES_PATH,
    GRAPH_PREFER_HEADER,
    GROUP_EXPANSION_MAX_LIMIT,
    PROVIDER_GOOGLE,
    PROVIDER_GRAPH,
    PROVIDERS,
)

#: The three kinds a registered calendar can be. [inferred] The research's data
#: sources name three things - a person's calendar, a distribution list to expand,
#: and a room resource - and those are exactly these three.
KIND_PERSON = "person"
KIND_GROUP = "group"
KIND_ROOM = "room"
KINDS: tuple[str, ...] = (KIND_PERSON, KIND_ROOM, KIND_GROUP)


@dataclass(frozen=True)
class BusyMap:
    """What a free/busy read came back with, normalised across both dialects.

    ``busy`` is the whole answer the slot side needs: calendar id to its busy
    blocks as half-open UTC intervals. ``unreadable`` is the researched *unknown*
    case, kept apart from ``busy`` because it scores 49% and a busy block scores
    0%, and a system that collapsed the two would silently prefer a calendar
    nobody could read over one that is genuinely free.
    """

    busy: dict[str, list[tuple[datetime, datetime]]] = field(default_factory=dict)
    unreadable: dict[str, str] = field(default_factory=dict)
    requested: tuple[str, ...] = ()

    def blocks(self, calendar_id: str) -> list[tuple[datetime, datetime]]:
        if calendar_id in self.unreadable:
            return []
        return list(self.busy.get(calendar_id, ()))

    def is_unreadable(self, calendar_id: str) -> bool:
        return calendar_id in self.unreadable

    def describe(self) -> dict[str, Any]:
        return {
            "requested": list(self.requested),
            "busy": {
                key: [{"start": format_instant(a), "end": format_instant(b)} for a, b in blocks]
                for key, blocks in sorted(self.busy.items())
            },
            "unreadable": dict(sorted(self.unreadable.items())),
        }


@dataclass(frozen=True)
class Expansion:
    """Who is actually invited, after group expansion and the researched caps."""

    invited: tuple[str, ...] = ()
    expanded: tuple[dict[str, Any], ...] = ()
    unexpanded: tuple[dict[str, Any], ...] = ()
    groups_expansion_max: int = 0
    calendar_expansion_max: int = 0
    refusal: str = ""

    def describe(self) -> dict[str, Any]:
        return {
            "invited": list(self.invited),
            "groups_expanded": [dict(entry) for entry in self.expanded],
            "groups_unexpanded": [dict(entry) for entry in self.unexpanded],
            "group_expansion_max": self.groups_expansion_max,
            "calendar_expansion_max": self.calendar_expansion_max,
            "refusal": self.refusal,
        }


def _check_provider(provider: object, *, field_name: str = "provider") -> str:
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise CalendarShapeError(
            f"{field_name} must be one of {list(PROVIDERS)}; got {provider!r}"
        )
    return provider


def _check_kind(kind: object) -> str:
    if not isinstance(kind, str) or kind not in KINDS:
        raise CalendarShapeError(f"kind must be one of {list(KINDS)}; got {kind!r}")
    return kind


# --------------------------------------------------------------------------- #
# Group expansion
# --------------------------------------------------------------------------- #


def expand_invited(
    calendars: Sequence[Mapping[str, Any]],
    *,
    group_expansion_max: int = GROUP_EXPANSION_MAX_LIMIT,
    calendar_expansion_max: int = CALENDAR_EXPANSION_MAX_LIMIT,
    index: Mapping[str, Mapping[str, Any]] | None = None,
) -> Expansion:
    """Replace distribution lists with their members, inside the researched caps.

    [sourced] "``groupExpansionMax`` (max 100) and ``calendarExpansionMax`` (max
    50) are explicit capacity knobs" and "group expansion for whole distribution
    lists" are both named in the research's features and extensibility lines.

    Three rules, and each is a rule the research forces:

    * A cap above its documented maximum is refused here rather than sent, so
      the request this engine renders is one the provider would accept.
    * Expansion stops at ``group_expansion_max`` groups, and the groups past it
      are recorded as *unexpanded* - not silently dropped. A panel that invited
      eight distribution lists and only got four expanded must say so.
    * A group that expands is **replaced** by its members, never added alongside
      them. A group invited as well as its members would be scored twice - once
      as its members and once as a 49% unknown - and would quietly halve the
      confidence of a panel whose members are all free.
    * A group that expands to *nothing* - past the cap, unresolvable, or
      memberless - is invited as an **unreadable** calendar, which is the
      researched ``unknown`` status and its 49%. There is no fourth outcome in
      which a distribution list is simply gone.

    ``index`` is the *whole* registry keyed by lowercased address, so a group can
    resolve a member the panel never named. Without it a panel inviting one
    distribution list would expand only into the members that happened to be
    listed individually, which is the difference between "expand the list" and
    "expand the two of its three members that were already there".
    """
    if not isinstance(group_expansion_max, int) or isinstance(group_expansion_max, bool):
        raise CalendarShapeError("group_expansion_max must be an integer")
    if not isinstance(calendar_expansion_max, int) or isinstance(calendar_expansion_max, bool):
        raise CalendarShapeError("calendar_expansion_max must be an integer")
    if group_expansion_max < 1 or group_expansion_max > GROUP_EXPANSION_MAX_LIMIT:
        raise LimitExceeded(
            f"group_expansion_max is {group_expansion_max}; the documented maximum is "
            f"{GROUP_EXPANSION_MAX_LIMIT}"
        )
    if calendar_expansion_max < 1 or calendar_expansion_max > CALENDAR_EXPANSION_MAX_LIMIT:
        raise LimitExceeded(
            f"calendar_expansion_max is {calendar_expansion_max}; the documented maximum is "
            f"{CALENDAR_EXPANSION_MAX_LIMIT}: \"{CALENDAR_EXPANSION_MAX_QUOTE}\""
        )

    by_address: dict[str, Mapping[str, Any]] = {}
    registry = list(index.values()) if isinstance(index, Mapping) else list(index or [])
    for record in [*registry, *calendars]:
        address = (record.get("data") or {}).get("email")
        if isinstance(address, str) and address:
            # A record the panel named wins over the registry's entry for the
            # same address, because the list is walked in that order and the
            # panel is the thing being honoured.
            by_address[address.lower()] = record

    invited: list[str] = []
    expanded: list[dict[str, Any]] = []
    unexpanded: list[dict[str, Any]] = []
    groups_seen = 0

    def add(calendar_id: str) -> None:
        if calendar_id not in invited:
            invited.append(calendar_id)

    for record in calendars:
        data = record.get("data", {}) or {}
        kind = _check_kind(data.get("kind", KIND_PERSON))
        if kind != KIND_GROUP:
            add(str(record["id"]))
            continue

        groups_seen += 1
        address = str(data.get("email") or data.get("group_address") or record["id"])
        if groups_seen > group_expansion_max:
            unexpanded.append(
                {
                    "id": record["id"],
                    "email": address,
                    "reason": "group_expansion_max",
                    "detail": (
                        f"group_expansion_max is {group_expansion_max} and this is group "
                        f"{groups_seen}; the group is invited as an unreadable calendar "
                        f"and scores the researched 49% for an unknown status"
                    ),
                }
            )
            add(str(record["id"]))
            continue

        members = data.get("members") or []
        resolved: list[str] = []
        missing: list[str] = []
        for member in members:
            if not isinstance(member, str) or "@" not in member:
                missing.append(str(member))
                continue
            found = by_address.get(member.lower())
            if found is None:
                missing.append(member)
            else:
                resolved.append(str(found["id"]))
                add(str(found["id"]))

        if missing:
            unexpanded.append(
                {
                    "id": record["id"],
                    "email": address,
                    "reason": "members_not_registered",
                    "missing": missing,
                    "detail": (
                        f"{len(missing)} member(s) are not registered as calendars: "
                        f"{', '.join(missing)}"
                    ),
                }
            )
        if resolved:
            # A group that expanded is *represented by* its members. Inviting the
            # group as well would score it a second time - as a 49% unknown - and
            # quietly halve the confidence of a panel whose members are all free.
            # So it is replaced, not added.
            expanded.append({"id": record["id"], "email": address, "members": resolved})
        else:
            # It expanded to nothing, so the only way its availability can reach
            # the average at all is as the researched unknown case. That is the
            # fourth outcome the research leaves no room for: a distribution
            # list is never simply gone.
            unexpanded.append(
                {
                    "id": record["id"],
                    "email": address,
                    "reason": "no_members" if not missing else "members_not_registered",
                    "detail": (
                        "the group declares no members, so it expands to nobody"
                        if not missing
                        else "no member of this group is a registered calendar"
                    ),
                }
            )
            add(str(record["id"]))

    # The researched cap binds on the *calendars* the read is asked about, which
    # is what Google's parameter name says: the maximal number of calendars for
    # which FreeBusy information is to be *provided*. A group that expands to
    # members pushes the count up, so this is checked after expansion.
    if len(invited) > calendar_expansion_max:
        raise LimitExceeded(
            f"this panel resolves to {len(invited)} calendars after group expansion, and "
            f"calendarExpansionMax is capped at {calendar_expansion_max} "
            "(\"Maximal number of calendars for which FreeBusy information is to be "
            "provided. Optional. Maximum value is 50.\"). Remove an attendee, or split "
            "the panel, rather than raising the knob past its documented maximum."
        )

    return Expansion(
        invited=tuple(invited),
        expanded=tuple(expanded),
        unexpanded=tuple(unexpanded),
        groups_expansion_max=group_expansion_max,
        calendar_expansion_max=calendar_expansion_max,
    )


# --------------------------------------------------------------------------- #
# The researched request
# --------------------------------------------------------------------------- #


def render_free_busy_request(
    *,
    provider: str,
    calendar_ids: Sequence[str],
    time_min: datetime,
    time_max: datetime,
    time_zone: str,
    group_expansion_max: int = GROUP_EXPANSION_MAX_LIMIT,
    calendar_expansion_max: int = CALENDAR_EXPANSION_MAX_LIMIT,
    meeting_duration: str = "PT1H",
    min_attendee_percentage: int = 0,
    return_suggestion_reasons: bool = True,
    attendees: Sequence[Mapping[str, Any]] = (),
    time_constraint: Mapping[str, Any] | None = None,
    location_constraint: Mapping[str, Any] | None = None,
    is_online_meeting: bool = False,
    by_user: str | None = None,
) -> dict[str, Any]:
    """The request this search would put on the wire, per researched dialect.

    Returned on every preview and every suggestion so a reviewer reads the
    researched body rather than a paraphrase of it. Nothing here is sent by
    :func:`expand_invited` or by the local provider; the point is that the body
    is *built* and can be compared with the documentation.

    For Google the body is exactly the set the research enumerates. For Graph it
    is the ``findMeetingTimes`` body the research names field by field, against
    the ``Prefer: outlook.timezone`` header and the ``Calendars.Read.Shared``
    least-privileged delegated scope.
    """
    dialect = _check_provider(provider)
    if time_max <= time_min:
        from dsr.panel_time.errors import ConstraintError

        raise ConstraintError("time_max must be after time_min")

    if dialect == PROVIDER_GOOGLE:
        return {
            "provider": dialect,
            "method": "POST",
            "url": GOOGLE_FREEBUSY_URL,
            "headers": {
                "Authorization": "Bearer <oauth access token>",
                "Content-Type": "application/json",
            },
            "scope": GOOGLE_FREEBUSY_SCOPE,
            "body": {
                "timeMin": format_instant(time_min),
                "timeMax": format_instant(time_max),
                "timeZone": time_zone,
                "groupExpansionMax": group_expansion_max,
                "calendarExpansionMax": calendar_expansion_max,
                "items": [{"id": calendar_id} for calendar_id in calendar_ids],
            },
            "reads": "calendars[key].busy[]",
            "unreadable": "calendars[key].errors[]",
        }

    url = (
        GRAPH_FIND_MEETING_TIMES_PATH
        if not by_user
        else GRAPH_FIND_MEETING_TIMES_BY_USER_PATH.replace("{id|userPrincipalName}", by_user)
    )
    # [sourced] Graph's attendee shape is ``{"type": "required", "emailAddress":
    # {"address": …, "name": …}}``. A bare ``email`` key is not the documented
    # spelling, and a deployment wiring this to a real tenant would be refused by
    # the provider, so the shape is rendered as documented rather than as this
    # engine's internal dict.
    rendered: list[dict[str, Any]] = []
    for entry in attendees:
        address = str(entry.get("email") or "")
        if not address:
            continue
        attendee: dict[str, Any] = {"type": "required", "emailAddress": {"address": address}}
        if entry.get("name"):
            attendee["emailAddress"]["name"] = str(entry["name"])
        rendered.append(attendee)

    body: dict[str, Any] = {
        "attendees": rendered,
        "timeConstraint": dict(time_constraint or {}),
        "meetingDuration": meeting_duration,
        "minAttendeePercentage": min_attendee_percentage,
        "returnSuggestionReasons": bool(return_suggestion_reasons),
        "isOnlineMeeting": bool(is_online_meeting),
    }
    if location_constraint:
        body["locationConstraint"] = dict(location_constraint)
    return {
        "provider": dialect,
        "method": "POST",
        "url": url,
        "headers": {
            "Authorization": "Bearer <delegated access token>",
            "Prefer": GRAPH_PREFER_HEADER,
            "Content-Type": "application/json",
        },
        "scope": GRAPH_DELEGATED_SCOPE,
        "delegated": True,
        "body": body,
        "reads": "meetingTimeSuggestionsResult",
        "unreadable": "meetingTimeSuggestionsResult.emptySuggestionsReason",
        "provider_note": (
            "This engine renders the researched Graph request and then runs the researched "
            "data flow - free/busy read to per-attendee availability to an averaged confidence "
            "score - over its own availability. The provider's own ranked "
            "meetingTimeSuggestions are not merged in: no cited source describes combining "
            "them, and picking one ranking over the other silently would be a decision "
            "nobody made."
        ),
    }


def render_commit_request(
    *,
    provider: str,
    calendar_id: str,
    summary: str,
    start: datetime,
    end: datetime,
    attendees: Sequence[Mapping[str, Any]],
    location_constraint: Mapping[str, Any] | None = None,
    create_conference: bool = True,
    time_zone: str = "UTC",
) -> dict[str, Any]:
    """The researched commit: an event on the **organizer's** calendar.

    [sourced] step 5: "On pick, the app creates the event on the organizer's
    calendar (optionally creating a fresh conference)."

    The Google body shape is this build's reading of ``events.insert`` - the
    research names the endpoint and the two behaviours but quotes no body - so
    it is returned on every booking for a reviewer to check rather than
    discovered in production.
    """
    dialect = _check_provider(provider)
    who: list[dict[str, Any]] = [{"email": str(entry.get("email", ""))} for entry in attendees]
    location: dict[str, Any] = {"type": "default"}
    if location_constraint:
        kind = str(location_constraint.get("type") or "suggest")
        location = {"type": "room" if kind == "room" else "other"}
        if location_constraint.get("room_id"):
            location["locationEmailAddress"] = str(location_constraint["room_id"])
        if location_constraint.get("display_name"):
            location["locationUri"] = str(location_constraint["display_name"])

    if dialect == PROVIDER_GRAPH:
        body: dict[str, Any] = {
            "subject": summary,
            "start": {"dateTime": format_instant(start), "timeZone": time_zone},
            "end": {"dateTime": format_instant(end), "timeZone": time_zone},
            "attendees": [
                {"emailAddress": {"address": str(entry.get("email", ""))}, "type": "required"}
                for entry in attendees
            ],
            "isOnlineMeeting": bool(create_conference),
            "location": {
                "displayName": str(
                    (location_constraint or {}).get("display_name")
                    or "Microsoft Teams Meeting"
                )
            },
        }
        return {
            "provider": dialect,
            "method": "POST",
            "url": "/users/{organizerId}/events",
            "headers": {"Prefer": GRAPH_PREFER_HEADER, "Content-Type": "application/json"},
            "scope": GRAPH_DELEGATED_SCOPE,
            "body": body,
        }

    body = {
        "summary": summary,
        "description": f"Panel booked from the Digital Sales Room, {len(who)} invitee(s).",
        "start": {"dateTime": format_instant(start), "timeZone": time_zone},
        "end": {"dateTime": format_instant(end), "timeZone": time_zone},
        "attendees": who,
        "location": location,
    }
    if create_conference:
        body["conferenceData"] = {
            "createRequest": {
                "requestId": f"dsr-{calendar_id}-{int(start.timestamp())}",
                "conferenceSolutionKey": dict(GOOGLE_CONFERENCE_SOLUTION_KEY),
            }
        }
    return {
        "provider": dialect,
        "method": "POST",
        "url": GOOGLE_EVENTS_PATH.replace("{calendarId}", calendar_id),
        "headers": {"Content-Type": "application/json"},
        "scope": GOOGLE_FREEBUSY_SCOPE,
        "body": body,
        "create_conference": bool(create_conference),
    }


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


class LocalDirectory:
    """The provider that answers from stored calendar rows.

    A deployment demoing this feature has no Google tenant, and a test must not
    open a socket. The stored row is the availability: ``busy`` is a list of
    ``{start, end}`` intervals in the calendar's own payload, and ``readable: false``
    - or an ``unavailable_reason`` the operator wrote - makes the calendar the
    researched *unknown* case, which is how a rep meets a distribution list
    nobody has published.

    This is the seam the extensibility claim is about: :mod:`dsr.panel_time.slots`
    is handed a :class:`BusyMap` and never learns where it came from.
    """

    name = "local"

    def send(
        self,
        calendars: Sequence[Mapping[str, Any]],
        *,
        time_min: datetime,
        time_max: datetime,
        provider: str,
    ) -> BusyMap:
        _check_provider(provider)
        busy: dict[str, list[tuple[datetime, datetime]]] = {}
        unreadable: dict[str, str] = {}
        requested: list[str] = []
        for record in calendars:
            calendar_id = str(record["id"])
            requested.append(calendar_id)
            data = record.get("data", {}) or {}
            if data.get("readable") is False:
                unreadable[calendar_id] = str(
                    data.get("unavailable_reason")
                    or "the calendar is registered as unreadable, so its availability is unknown"
                )
                continue
            if data.get("unavailable_reason"):
                unreadable[calendar_id] = str(data["unavailable_reason"])
                continue
            try:
                blocks = _busy_blocks(data.get("busy") or [])
            except CalendarShapeError as exc:
                unreadable[calendar_id] = str(exc)
                continue
            relevant = [
                (start, end)
                for start, end in blocks
                if start < time_max and time_min < end
            ]
            if relevant:
                busy[calendar_id] = relevant
        return BusyMap(busy=busy, unreadable=unreadable, requested=tuple(requested))

    def commit(
        self,
        request: Mapping[str, Any],
        *,
        calendar_id: str,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """The local equivalent of ``events.insert``: an event id and a join link.

        The event id is derived from the request so the *same* commit produces the
        *same* id - so a seeded demo booking and a booking a reviewer triggers
        from the page are recognisably the same meeting, and so a test can assert
        on it without a random value in the middle.
        """
        body = request.get("body", {}) or {}
        start = (body.get("start") or {}).get("dateTime", "")
        # hashlib rather than hash(): PYTHONHASHSEED randomises str hashing per
        # process, so a builtin hash() would mint a different event id on every
        # restart and a re-run of the demo would double-book itself.
        digest = int(
            hashlib.sha256(
                f"{calendar_id}|{body.get('summary')}|{start}".encode("utf-8")
            ).hexdigest()[:12],
            16,
        )
        event_id = f"evt_{digest:012d}"
        conference = None
        if body.get("conferenceData"):
            conference = {
                "entry_point": f"https://meet.example.com/{event_id}",
                "solution": GOOGLE_CONFERENCE_SOLUTION_KEY["type"],
                "created": True,
            }
        return {"event_id": event_id, "conference": conference, "provider": request.get("provider")}


class UrllibProvider:
    """The real Google free/busy read, for a deployment that has a tenant.

    Only the Google dialect is sent. The Graph ``findMeetingTimes`` response is
    ``meetingTimeSuggestions``, not ``calendars[key].busy``, and the research
    quotes no way to read free/busy from Graph at all - so rather than invent an
    endpoint this provider refuses the Graph dialect *by name*, with the reason
    in the message. A deployment that needs it supplies its own adapter behind
    the same one-method interface.
    """

    name = "urllib"

    def __init__(self, *, timeout: float = 10.0, access_token: str | None = None) -> None:
        self.timeout = timeout
        self.access_token = access_token

    def send(
        self,
        calendars: Sequence[Mapping[str, Any]],
        *,
        time_min: datetime,
        time_max: datetime,
        provider: str,
    ) -> BusyMap:
        dialect = _check_provider(provider)
        if dialect != PROVIDER_GOOGLE:
            raise PanelTimeNotConfigured(
                f"the urllib provider sends only the researched Google free/busy endpoint "
                f"({GOOGLE_FREEBUSY_URL}). The Graph findMeetingTimes response is a ranked "
                "suggestion list rather than free/busy blocks, and the research sources no "
                "Graph free/busy endpoint, so this provider will not guess one. Register "
                "calendars with the local directory, or supply an adapter with the same "
                "send() signature."
            )
        if not self.access_token:
            raise PanelTimeNotConfigured(
                "the urllib provider needs an OAuth access token. The research says the "
                f"free/busy read needs only the {GOOGLE_FREEBUSY_SCOPE} scope, so that is "
                "the scope to ask for - not calendar.events or calendar.read."
            )

        body = render_free_busy_request(
            provider=dialect,
            calendar_ids=[str(record["id"]) for record in calendars],
            time_min=time_min,
            time_max=time_max,
            time_zone="UTC",
        )["body"]
        http = urlrequest.Request(
            GOOGLE_FREEBUSY_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlrequest.urlopen(http, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urlerror.HTTPError as exc:  # a provider refusal is the caller's to fix
            raise PanelTimeNotConfigured(
                f"Google refused the free/busy read with HTTP {exc.code}: {exc.reason}"
            ) from exc
        except urlerror.URLError as exc:
            raise PanelTimeNotConfigured(f"could not reach {GOOGLE_FREEBUSY_URL}: {exc.reason}") from exc
        return parse_google_free_busy(payload, requested=[str(r["id"]) for r in calendars])


def parse_google_free_busy(payload: Mapping[str, Any], *, requested: Sequence[str] = ()) -> BusyMap:
    """``{"calendars": {key: {"busy": [...], "errors": [...]}}}`` → a :class:`BusyMap`.

    The researched response shape, and the researched failure shape: Google's
    free/busy answer reports per-calendar ``errors`` alongside ``busy``, which is
    exactly the distinction the 49% weight exists to express. A calendar with an
    error is **unknown**, not free.
    """
    calendars = payload.get("calendars")
    if not isinstance(calendars, Mapping):
        raise CalendarShapeError(
            "a Google free/busy response must carry a 'calendars' object; got "
            f"{sorted(payload)}"
        )
    busy: dict[str, list[tuple[datetime, datetime]]] = {}
    unreadable: dict[str, str] = {}
    for key, entry in calendars.items():
        errors = (entry or {}).get("errors") or []
        if errors:
            unreadable[str(key)] = "; ".join(
                f"{error.get('domain', 'global')}: {error.get('reason', 'unknown')}"
                for error in errors
                if isinstance(error, Mapping)
            ) or "the provider reported an error for this calendar"
            continue
        busy[str(key)] = _busy_blocks((entry or {}).get("busy") or [])
    for key in requested:
        if key not in calendars:
            unreadable[str(key)] = "the provider returned no entry for this calendar"
    return BusyMap(busy=busy, unreadable=unreadable, requested=tuple(requested))


def _busy_blocks(raw: Any) -> list[tuple[datetime, datetime]]:
    """``[{"start": ..., "end": ...}]`` → half-open UTC intervals, ordered.

    Google returns one form, Graph another, and a hand-written demo row a third.
    All three are read here, because a panel whose availability is a column in
    the operator's own table is the normal case, not the exotic one.
    """
    if not isinstance(raw, (list, tuple)):
        raise CalendarShapeError(f"a busy list must be a list of intervals; got {type(raw).__name__}")
    blocks: list[tuple[datetime, datetime]] = []
    for entry in raw:
        if isinstance(entry, Mapping):
            start = _edge(entry.get("start") or entry.get("dateTime") or entry.get("from"))
            end = _edge(entry.get("end") or entry.get("to") or entry.get("dateTime"))
            if start is None or end is None:
                raise CalendarShapeError(
                    f"a busy interval needs both a start and an end; got {sorted(entry)}"
                )
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            start, end = _edge(entry[0]), _edge(entry[1])
        else:
            raise CalendarShapeError(
                f"a busy interval must be {{start, end}} or [start, end]; got {entry!r}"
            )
        begins = parse_instant(start, field="busy.start")
        ends = parse_instant(end, field="busy.end")
        if ends <= begins:
            raise CalendarShapeError(
                f"a busy interval must end after it starts; got {start!r} to {end!r}"
            )
        blocks.append((begins, ends))
    return sorted(blocks)


def _edge(value: object) -> object | None:
    """Unwrap a Graph ``dateTimeTimeZone`` wrapper to its ``dateTime``.

    Graph writes a schedule item's edges as ``{"dateTime": "…", "timeZone": "…"}``
    where Google writes a bare RFC 3339 string, and a hand-written demo row
    usually writes a bare string too. All three reach this function, so all three
    are read here rather than in three call sites.
    """
    if isinstance(value, Mapping):
        return value.get("dateTime") or value.get("date_time")
    return value
