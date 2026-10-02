"""Bookable assets: what a headless caller is actually booking.

The research names three surfaces and each is addressed differently, and that
difference is load-bearing rather than cosmetic:

``concierge``
    Addressed by a **router slug**. The init path carries ``{routerSlug}``, so a
    Concierge asset is a router and its identity is its slug.

``links``
    Addressed by a **link id and a link type**. The init path is
    ``schedulingLinks/init-simple`` and carries neither, so the link's type is in
    the *body* - and that is where the researched extra requirement lives: "For
    **Ownership** links, also pass ``guestEmail`` in the init call - it is
    required so Chili Piper can resolve the owner from your CRM." An Ownership
    asset with no guest email cannot open a session, and that refusal is this
    module's to make.

``handoff``
    Addressed by a **workspace, a booker, and a router's paths**. The init path
    carries ``{workspaceId}`` and ``{userId}``, and the schedule path carries
    ``pathId`` on top of those. So a handoff asset holds *paths*, and a session
    over it holds one ``startTimes`` list per path - which is why
    :mod:`dsr.headless_booking.sessions` has to model a path list at all, when
    the researched user flow for WF-056 describes a single flat list of
    ``startTimes``.

Working hours, the grid and the meeting length live on the asset
------------------------------------------------------------------

These are configuration, not policy, and they belong here because they are what
a *deployment* sets rather than what the *research* says. A router configured
for a 30-minute demo and a router configured for a 90-minute technical review
are both bookable through the same two calls. The research publishes no
duration, so every default in this module is flagged as unsourced and the
inference ``slot-generation-is-a-grid`` governs them.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.headless_booking.availability import (
    DEFAULT_LEAD_MINUTES,
    DEFAULT_MAX_SLOTS,
    DEFAULT_MEETING_MINUTES,
    DEFAULT_SLOT_FALLS_BACK_TO_MEETING,
    DEFAULT_SLOT_MINUTES,
    DEFAULT_WORK_END_HOUR,
    DEFAULT_WORK_START_HOUR,
)
from dsr.headless_booking.errors import HeadlessBookingError
from dsr.headless_booking.vocabulary import (
    ASSET_KINDS,
    LINK_TYPES,
    MEETING_PROVIDERS,
    OWNERSHIP_LINK_TYPE,
    require_link_type,
    require_provider,
    require_section,
)

#: A handoff router's paths. The research describes "one or more routing paths,
#: each with its own ``pathId`` and ``startTimes``", so a handoff asset with no
#: declared paths is a configuration error rather than a zero-path session: the
#: init response's whole shape is a list of paths, and an empty one would be a
#: response this product cannot render.
MAX_PATHS = 20


def normalise(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate an asset and resolve every default the research leaves open.

    Validated before a row is written, so a rejected asset cannot leave a
    half-configured bookable target behind. Everything is ordinary JSON on the
    way in and ordinary JSON on the way out: no key is required beyond
    ``section`` and ``name``, because a team adding a field to its own bookable
    assets must not need a migration.
    """
    body = dict(payload or {})
    section = require_section(body.get("section"))

    name = str(body.get("name") or "").strip()
    if not name:
        raise HeadlessBookingError("a bookable asset needs a name, so a list of them is readable")

    kind = str(body.get("kind") or ASSET_KINDS[section]).strip().lower()
    expected_kind = ASSET_KINDS[section]
    if kind != expected_kind:
        raise HeadlessBookingError(
            f"a {section!r} asset is a {expected_kind!r}; {kind!r} is not one. "
            "The kind follows from the section because the researched endpoints address "
            "each surface differently."
        )

    duration_minutes = _minutes(
        body.get("duration_minutes"), DEFAULT_MEETING_MINUTES, "duration_minutes"
    )
    spec: dict[str, Any] = {
        "section": section,
        "kind": kind,
        "name": name,
        "enabled": bool(body.get("enabled", True)),
        "host_name": str(body.get("host_name") or body.get("host") or "").strip(),
        "host_email": str(body.get("host_email") or "").strip().lower(),
        "duration_minutes": duration_minutes,
        # The start grid falls back to the meeting length rather than to a fixed
        # 30 minutes, so by default a slot list cannot contain two starts that
        # overlap. See availability.DEFAULT_SLOT_FALLS_BACK_TO_MEETING for why
        # the finer-grid case is allowed and caught at the book call instead.
        "slot_minutes": _minutes(
            body.get("slot_minutes"),
            duration_minutes if DEFAULT_SLOT_FALLS_BACK_TO_MEETING else DEFAULT_SLOT_MINUTES,
            "slot_minutes",
        ),
        "work_start_hour": _hour(
            body.get("work_start_hour"), DEFAULT_WORK_START_HOUR, "work_start_hour"
        ),
        "work_end_hour": _hour(body.get("work_end_hour"), DEFAULT_WORK_END_HOUR, "work_end_hour"),
        "work_days": _work_days(body.get("work_days")),
        "utc_offset_minutes": _offset(body.get("utc_offset_minutes")),
        "lead_minutes": _minutes(body.get("lead_minutes"), DEFAULT_LEAD_MINUTES, "lead_minutes"),
        "max_slots": _minutes(body.get("max_slots"), DEFAULT_MAX_SLOTS, "max_slots"),
        "note": str(body.get("note") or ""),
    }

    if spec["work_start_hour"] >= spec["work_end_hour"]:
        raise HeadlessBookingError(
            f"work_start_hour ({spec['work_start_hour']}) must be before work_end_hour "
            f"({spec['work_end_hour']})"
        )

    # The three sections are addressed differently, so each asks for its own
    # identity fields. Concierge's is a slug because the endpoint path says
    # `{routerSlug}`; handoff's is a workspace plus a booker because the endpoint
    # path says `{workspaceId}` and `{userId}`.
    if section == "concierge":
        slug = str(body.get("router_slug") or body.get("slug") or "").strip()
        if not slug:
            raise HeadlessBookingError(
                "a concierge asset needs a router_slug: the researched init path is "
                "/concierge/routers/{routerSlug}/rest, so the router's identity is its slug"
            )
        spec["router_slug"] = slug
    elif section == "links":
        link_id = str(body.get("link_id") or body.get("slug") or "").strip()
        if not link_id:
            raise HeadlessBookingError(
                "a scheduling link needs a link_id: the researched init path is "
                "/schedulingLinks/init-simple, so the link is named in the body"
            )
        spec["link_id"] = link_id
        spec["link_type"] = require_link_type(body.get("link_type") or "personal")
        if spec["link_type"] not in LINK_TYPES:
            # Unreachable while require_link_type publishes the same set, but the
            # check is the one place a future LINK_TYPES edit would be caught.
            raise HeadlessBookingError(f"unknown link type {spec['link_type']!r}")
        if spec["link_type"] == OWNERSHIP_LINK_TYPE:
            # The researched requirement is on the *call*, not the asset: the
            # guest's email is what the owner is resolved from, and it arrives
            # with the lead rather than being fixed configuration. Recorded here
            # so the page can ask for it before the caller tries.
            spec["requires_guest_email"] = True
        else:
            spec["requires_guest_email"] = False
    else:
        workspace_id = str(body.get("workspace_id") or "").strip()
        booker_id = str(body.get("booker_id") or "").strip()
        if not workspace_id:
            raise HeadlessBookingError(
                "a handoff asset needs a workspace_id: the researched init path is "
                "/handoff/workspace/{workspaceId}/booker/{userId}/init-simple"
            )
        if not booker_id:
            raise HeadlessBookingError(
                "a handoff asset needs a booker_id: the researched init path is "
                "/handoff/workspace/{workspaceId}/booker/{userId}/init-simple, and the research "
                "has an SDR book for an AE"
            )
        spec["workspace_id"] = workspace_id
        spec["booker_id"] = booker_id
        spec["paths"] = _paths(body.get("paths"))

    if not spec["host_email"]:
        # A host with no calendar address cannot be the one a meeting is booked
        # with, and "Calendar invites are sent immediately" is the researched
        # consequence of booking. Refusing at configuration time means the
        # session does not fail later for a reason nobody can act on.
        raise HeadlessBookingError(
            "a bookable asset needs a host_email: calendar invites are sent immediately on "
            "booking, and there is nobody to send them to without it"
        )
    spec["provider"] = require_provider(body.get("provider") or MEETING_PROVIDERS[0])
    spec["webhook_url"] = str(body.get("webhook_url") or "").strip()

    return spec


def _minutes(value: Any, default: int, field: str) -> int:
    if value in (None, ""):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise HeadlessBookingError(f"{field} {value!r} is not a whole number of minutes") from exc
    if number <= 0:
        raise HeadlessBookingError(f"{field} must be a positive number of minutes")
    return number


def _hour(value: Any, default: int, field: str) -> int:
    if value in (None, ""):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise HeadlessBookingError(f"{field} {value!r} is not an hour of the day") from exc
    if not 0 <= number <= 24:
        raise HeadlessBookingError(f"{field} must be between 0 and 24")
    return number


def _offset(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise HeadlessBookingError(f"utc_offset_minutes {value!r} is not a whole number") from exc
    if not -14 * 60 <= number <= 14 * 60:
        raise HeadlessBookingError("utc_offset_minutes must be between -840 and 840")
    return number


def _work_days(value: Any) -> list[int]:
    """Working weekdays, 0 = Monday. Defaults to Mon-Fri."""
    if value in (None, ""):
        return [0, 1, 2, 3, 4]
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        raise HeadlessBookingError("work_days must be a list of 0=Monday to 6=Sunday")
    days: list[int] = []
    for part in parts:
        try:
            day = int(part)
        except (TypeError, ValueError) as exc:
            raise HeadlessBookingError(f"work_days entry {part!r} is not an integer 0-6") from exc
        if not 0 <= day <= 6:
            raise HeadlessBookingError(
                "work_days entries must be between 0 (Monday) and 6 (Sunday)"
            )
        days.append(day)
    if not days:
        raise HeadlessBookingError(
            "work_days cannot be empty; an asset with no working days yields no slots"
        )
    return sorted(set(days))


def _paths(value: Any) -> list[dict[str, Any]]:
    """A handoff router's routing paths, each with its own availability.

    The research says the init response "returns one or more routing paths, each
    with its own ``pathId`` and ``startTimes``". So a path here is not a label:
    it carries its own host and therefore its own calendar, which is what makes
    one session's path list differ from another's.
    """
    if value in (None, ""):
        raise HeadlessBookingError(
            "a handoff asset needs at least one path: the researched init response returns one "
            "or more routing paths, each with its own pathId and startTimes"
        )
    if not isinstance(value, (list, tuple)):
        raise HeadlessBookingError("paths must be a list of routing paths")
    if len(value) > MAX_PATHS:
        raise HeadlessBookingError(
            f"a handoff asset may declare at most {MAX_PATHS} paths; the init response is one "
            "list of path results and an unbounded one is not a decision a caller can make"
        )
    paths: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise HeadlessBookingError("each routing path must be an object")
        path_id = str(entry.get("path_id") or entry.get("pathId") or "").strip()
        if not path_id:
            raise HeadlessBookingError(
                f"routing path {index} needs a path_id: the schedule path carries {{pathId}}, so a "
                "path the caller cannot name is a path it cannot book"
            )
        if path_id in seen:
            raise HeadlessBookingError(
                f"two routing paths share the path_id {path_id!r}; the schedule call names one "
                "path by id, so duplicate ids are ambiguous"
            )
        seen.add(path_id)
        path_duration = _minutes(
            entry.get("duration_minutes"), DEFAULT_MEETING_MINUTES, "duration_minutes"
        )
        path: dict[str, Any] = {
            "path_id": path_id,
            "label": str(entry.get("label") or path_id),
            "host_name": str(entry.get("host_name") or entry.get("host") or "").strip(),
            "host_email": str(entry.get("host_email") or "").strip().lower(),
            "duration_minutes": path_duration,
            "slot_minutes": _minutes(
                entry.get("slot_minutes"),
                path_duration if DEFAULT_SLOT_FALLS_BACK_TO_MEETING else DEFAULT_SLOT_MINUTES,
                "slot_minutes",
            ),
            "work_start_hour": _hour(
                entry.get("work_start_hour"), DEFAULT_WORK_START_HOUR, "work_start_hour"
            ),
            "work_end_hour": _hour(
                entry.get("work_end_hour"), DEFAULT_WORK_END_HOUR, "work_end_hour"
            ),
            "work_days": _work_days(entry.get("work_days")),
            "utc_offset_minutes": _offset(entry.get("utc_offset_minutes")),
            "note": str(entry.get("note") or ""),
        }
        if not path["host_email"]:
            raise HeadlessBookingError(
                f"routing path {path_id!r} needs a host_email: a path's availability is its own "
                "host's calendar, so a path with no host has no availability to read"
            )
        paths.append(path)
    return paths


def host_calendar_key(
    section: str, spec: Mapping[str, Any], path: Mapping[str, Any] | None = None
) -> str:
    """The key a busy block is filed under.

    Calendar blocks are shared: a host's Google or Outlook calendar is the same
    whether it is reached through a Concierge router, a scheduling link, or a
    handoff path. So the key is the *host*, not the asset - otherwise booking a
    meeting through one surface would leave the host apparently free on the other
    two, and the same person would be double-booked.

    The one wrinkle is a handoff path, which is a different host within one
    asset, and is why the key includes the path id when there is one.
    """
    host = str((path or {}).get("host_email") or spec.get("host_email") or "").strip().lower()
    if not host:
        return ""
    if path is not None:
        return f"{host}|{path.get('path_id')}"
    return host


def meeting_link(provider: str, record_id: str) -> str:
    """A deterministic stand-in for the provider's real join link.

    The research lists "Zoom/GMeet/Gong providers for the meeting link" as a data
    source, and this build calls none of them. So the link is *derived* from the
    provider and the meeting id rather than fetched, it is obviously not a real
    join URL, and the meeting record says which it is. A demo that showed a
    plausible-looking join link without saying so would be worse than one that
    shows a derived one.
    """
    hosts = {
        "zoom": "zoom.invalid",
        "gmeet": "meet.invalid",
        "gong": "app.gong.invalid",
    }
    return f"https://{hosts.get(provider, 'example.invalid')}/j/{record_id}"


def published_assets() -> dict[str, Any]:
    """The asset vocabulary, for a client that renders a create form."""
    return {
        "sections": {name: kind for name, kind in ASSET_KINDS.items()},
        "link_types": list(LINK_TYPES),
        "providers": list(MEETING_PROVIDERS),
        "defaults": {
            "duration_minutes": DEFAULT_MEETING_MINUTES,
            "slot_minutes": "the meeting length, so a slot list cannot contain two overlapping starts",
            "work_start_hour": DEFAULT_WORK_START_HOUR,
            "work_end_hour": DEFAULT_WORK_END_HOUR,
            "work_days": [0, 1, 2, 3, 4],
            "utc_offset_minutes": 0,
            "lead_minutes": DEFAULT_LEAD_MINUTES,
            "max_slots": DEFAULT_MAX_SLOTS,
        },
        "defaults_sourced": False,
        "note": (
            "The research names the three surfaces, the five link types, and that availability "
            "comes from Google/Outlook calendars. It publishes no durations, grid or working "
            "hours, so every number in `defaults` is this build's choice."
        ),
    }
