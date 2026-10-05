"""The researched vocabulary for WF-056, and nothing else.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-056.md`` and the
section 6 of ``docs/research/raw/scheduling-meetings.md`` it points at. Every
constant here is either quoted from one of the three cited sources or is an
explicitly *unsourced* choice, and the two are kept apart by the ``sourced``
flag rather than left to the reader's judgement.

What the research pins down
---------------------------

* Three sections, each with its own init/schedule endpoint pair, and the exact
  paths. The paths below are quoted, not invented.
* Six MCP tools that mirror those six endpoints, named in the research.
* The discovery tools: ``workspace-list``, ``user-find``, and the four
  ``scheduling-link-list-*`` operations. Note what is *absent* from that list -
  there is no ``scheduling-link-list-personal`` - which is why
  :data:`DISCOVERED_LINK_TYPES` and :data:`LINK_TYPES` are different tuples and
  the difference is worth publishing rather than smoothing over.
* The token permissions: ``Schedule`` per section, plus ``Read`` where listing
  assets is needed. The role: ``Admin`` only, and explicitly not Workspace
  Manager.
* The four scheduled states a session can be in, and the sourced rules that
  make them: single-use, short-lived, UTC, verbatim.
* The webhook event name, ``Created``, on the ``For New Meeting`` webhook.

What the research leaves open
----------------------------

The session TTL *durations*. The research establishes that a TTL exists, that
Concierge's is settable per request as ``timeoutInMS`` and that links/handoff use
a server-side one, but publishes no number. :data:`DEFAULT_TTL_MS` is this build's
choice and is flagged ``sourced: False``; the inference that governs it is
``session-ttl-durations`` in :mod:`dsr.headless_booking.inferences`.

A rule with a published vocabulary is a rule a client can send, and a client that
sends the wrong one gets a message naming the published set. That is the whole
reason these tuples are exported rather than inlined at their call sites.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Sections: the three surfaces the research names, each with its own scope
# --------------------------------------------------------------------------- #

#: The three bookable surfaces. "Concierge / Scheduling-links / Handoff" is the
#: research's own phrasing of the token's per-section ``Schedule`` permission.
SECTIONS: tuple[str, ...] = ("concierge", "links", "handoff")

#: Call #1 - "discover or route". Quoted paths, one per section.
INIT_ENDPOINTS: dict[str, str] = {
    "concierge": "/api/fire-edge/v1/org/concierge/routers/{routerSlug}/rest",
    "links": "/api/fire-edge/v1/org/schedulingLinks/init-simple",
    "handoff": (
        "/api/fire-edge/v1/org/handoff/workspace/{workspaceId}/booker/{userId}/init-simple"
    ),
}

#: Call #2 - "book". Quoted paths, one per section. Only the handoff one carries
#: a ``pathId``, which is the researched reason a handoff session can hold more
#: than one path: "one or more routing paths, each with its own ``pathId`` and
#: ``startTimes``".
SCHEDULE_ENDPOINTS: dict[str, str] = {
    "concierge": "/api/fire-edge/v1/org/concierge/routing/{routeId}/schedule-simple",
    "links": "/api/fire-edge/v1/org/schedulingLinks/routing/{routeId}/schedule-simple",
    "handoff": (
        "/api/fire-edge/v1/org/handoff/routing/{routingId}/router/{routerId}/path/{pathId}"
        "/booker/{userId}/schedule-simple"
    ),
}

#: The MCP tools that mirror those endpoints, "plus list discovery tools".
MCP_TOOLS: dict[str, tuple[str, ...]] = {
    "concierge": ("concierge-route-by-slug", "concierge-schedule"),
    "links": ("scheduling-link-init", "scheduling-link-schedule"),
    "handoff": ("handoff-init", "handoff-schedule"),
}

#: The discovery tools, by the surface they enumerate. `concierge-route-by-slug`
#: is listed under concierge because it is the router-discovery tool, and it is
#: also the name of the MCP init tool - the research lists it in both places and
#: this build does not try to reconcile the collision, it publishes it.
DISCOVERY_TOOLS: dict[str, tuple[str, ...]] = {
    "concierge": ("concierge-route-by-slug",),
    "links": (
        "scheduling-link-list-round-robin",
        "scheduling-link-list-ownership",
        "scheduling-link-list-group",
        "scheduling-link-list-admin-one-on-one",
    ),
    "handoff": ("workspace-list", "user-find"),
}

#: The researched guidance on which of the two surfaces to build against:
#: "Choose MCP 'when you want an assistant to pick the tool dynamically; choose
#: Edge when you are writing deterministic integration code.'" The MCP tools
#: mirror the Edge endpoints one-for-one, so neither is a different workflow -
#: they are the same two calls with a different caller.
TRANSPORT_GUIDANCE = (
    "Choose MCP when you want an assistant to pick the tool dynamically; choose Edge when "
    "you are writing deterministic integration code."
)

# --------------------------------------------------------------------------- #
# Assets: what is actually being booked
# --------------------------------------------------------------------------- #

#: What a bookable asset *is* on each surface. A Concierge section is addressed
#: by a router slug; a scheduling link by its link id and type; a handoff
#: section by a workspace and a booker, with the router's paths inside it.
ASSET_KINDS: dict[str, str] = {
    "concierge": "router",
    "links": "scheduling_link",
    "handoff": "handoff_router",
}

#: The five scheduling-link types the research names: "Personal / Admin
#: (one-on-one) / Round Robin / Group / Ownership".
LINK_TYPES: tuple[str, ...] = (
    "personal",
    "round_robin",
    "group",
    "ownership",
    "admin_one_on_one",
)

#: The subset with a published discovery operation. ``personal`` has none, so
#: it is in :data:`LINK_TYPES` and *not* here. Publishing both is the honest
#: answer; collapsing them would invent a discovery tool the research does not
#: list.
DISCOVERED_LINK_TYPES: tuple[str, ...] = (
    "round_robin",
    "ownership",
    "group",
    "admin_one_on_one",
)

#: The one link type with a documented *extra required field*: "For **Ownership**
#: links, also pass ``guestEmail`` in the init call - it is required so Chili
#: Piper can resolve the owner from your CRM." Enforced at session creation.
OWNERSHIP_LINK_TYPE = "ownership"
OWNERSHIP_REQUIRES_GUEST_EMAIL = True

#: The meeting-link providers the research lists as data sources. The meeting
#: records which one produced the link; this build does not call any of them.
MEETING_PROVIDERS: tuple[str, ...] = ("zoom", "gmeet", "gong")

#: The two credential permissions, as the research words them.
PERMISSIONS: tuple[str, ...] = ("schedule", "read")

#: The permission each surface's operations need. `schedule` is needed to *book*;
#: `read` is needed to *list*, per "plus ``Read`` where listing assets is
#: needed". A surface needs both in order to be driven end to end headlessly,
#: which is the case the research's own flow describes.
SECTION_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "concierge": ("schedule", "read"),
    "links": ("schedule", "read"),
    "handoff": ("schedule", "read"),
}

#: The role rule, quoted: "Only users with the **Admin** role can generate API
#: tokens in Command Center. Workspace Managers do not have access to the
#: credentials page."
TOKEN_GENERATOR_ROLES: tuple[str, ...] = ("admin",)

#: Named only so the refusal can say the sentence that produced it. Listed
#: alongside the roles that are allowed, because "who cannot" is the half of the
#: rule a reviewer needs to see.
TOKEN_GENERATOR_REFUSED_ROLES: tuple[str, ...] = ("workspace_manager", "user")

#: The prefix on a generated token. Distinctive so a token pasted into a log is
#: recognisable, which matters for a credential shown exactly once.
TOKEN_PREFIX = "dsr_hbt_"

# --------------------------------------------------------------------------- #
# Sessions: the researched two-step, and the four rules that make it work
# --------------------------------------------------------------------------- #

#: A session's four states. ``open`` is the only one that can be booked from.
#: The transition rule is the researched one: "Sessions are single-use", so
#: every terminal state is terminal - including the states reached by a *failed*
#: schedule call, which is the half of the rule that is easy to get wrong.
SESSION_STATES: tuple[str, ...] = ("open", "booked", "failed", "expired")

#: The states a schedule call refuses to act on, and the reason each carries.
TERMINAL_SESSION_STATES: tuple[str, ...] = ("booked", "failed", "expired")

#: Every schedule refusal, with the researched sentence each one comes from.
#: Served as data so a client can branch on `reason` instead of pattern-matching
#: prose, which is what makes the researched instruction - "do not retry the
#: schedule call with the same `routeId`" - something a caller can *act* on.
SCHEDULE_FAILURES: dict[str, dict[str, str]] = {
    "session_expired": {
        "summary": "The session's server-side TTL elapsed before the book call arrived.",
        "sourced": "The two-step session has a server-side TTL; step 5 re-runs step 1 if it expired.",
        "retry_same_route_id": "no",
        "next_step": "re-run the discover or route call for a fresh session",
    },
    "session_consumed": {
        "summary": "This routeId has already been used. Sessions are single-use.",
        "sourced": "Sessions are single-use.",
        "retry_same_route_id": "no",
        "next_step": "re-run the discover or route call for a fresh session",
    },
    "start_time_not_offered": {
        "summary": "The startTime is not one of the slots this session returned.",
        "sourced": "Pass it back verbatim on the book call.",
        "retry_same_route_id": "no",
        "next_step": "re-run the discover or route call and pick from the slots it returns",
    },
    "start_time_not_utc": {
        "summary": "startTime carried no UTC designator, so its instant is ambiguous.",
        "sourced": "Slot times are UTC. The startTime in responses is ISO-8601 UTC.",
        "retry_same_route_id": "no",
        "next_step": "re-run the discover or route call and pass one of its strings back verbatim",
    },
    "start_time_wrong_path": {
        "summary": "The startTime belongs to a different routing path than the one named.",
        "sourced": "Each routing path has its own pathId and startTimes.",
        "retry_same_route_id": "no",
        "next_step": "re-run the discover or route call and pick a path and a slot from it",
    },
    "path_not_offered": {
        "summary": "This surface returns one path, so it has no pathId to honour.",
        "sourced": "Only the handoff schedule URL carries a pathId.",
        "retry_same_route_id": "no",
        "next_step": "drop pathId and book the session you have",
    },
    "path_unknown": {
        "summary": "The named pathId is not one of this session's routing paths.",
        "sourced": "The init response returns one or more routing paths, each with its own pathId.",
        "retry_same_route_id": "no",
        "next_step": "re-run the discover or route call and pick a path from it",
    },
    "slot_taken": {
        "summary": "The slot was taken between the discover call and this book call.",
        "sourced": "If step 2's session expired or the slot was taken, the caller re-runs step 1.",
        "retry_same_route_id": "no",
        "next_step": "re-run the discover or route call for a fresh session",
    },
    "host_unavailable": {
        "summary": "The host has no availability left in the requested interval.",
        "sourced": "Availability is read from the host's connected calendar.",
        "retry_same_route_id": "no",
        "next_step": "re-run the discover or route call with a wider interval",
    },
}

#: Every refusal at session creation, distinct from the schedule failures above
#: because none of them is a schedule call - so none of them consumes a session,
#: since there is no session yet.
INIT_FAILURES: dict[str, dict[str, str]] = {
    "unknown_section": {
        "summary": "The section is not one of the three the research names.",
        "retry_same_route_id": "n/a",
    },
    "asset_unknown": {
        "summary": "No such bookable asset.",
        "retry_same_route_id": "n/a",
    },
    "asset_section_mismatch": {
        "summary": "The asset is not on the section the call named.",
        "retry_same_route_id": "n/a",
    },
    "asset_disabled": {
        "summary": "The asset is switched off, so it yields no availability.",
        "retry_same_route_id": "n/a",
    },
    "interval_required": {
        "summary": "interval{startsAt,duration} is what distinguishes scheduling from returning a booking URL.",
        "retry_same_route_id": "n/a",
    },
    "interval_starts_at_not_utc": {
        "summary": "interval.startsAt carried no UTC designator.",
        "retry_same_route_id": "n/a",
    },
    "interval_starts_at_in_past": {
        "summary": (
            "The whole interval has already passed, so no slot inside it remains to be offered. "
            "An interval whose start is in the past but which has not yet ended is not this "
            "failure: the elapsed part is dropped and the rest is offered."
        ),
        "retry_same_route_id": "n/a",
    },
    "interval_duration_missing": {
        "summary": "interval.duration is required; the research spells it startsAt plus duration.",
        "retry_same_route_id": "n/a",
    },
    "guest_email_required": {
        "summary": "This link type requires guestEmail so the owner can be resolved from the CRM.",
        "retry_same_route_id": "n/a",
    },
    "booker_required": {
        "summary": "A handoff init is addressed to a booker, so booker_id is required.",
        "retry_same_route_id": "n/a",
    },
    "timeout_in_ms_not_settable": {
        "summary": "timeoutInMS is a Concierge setting; this surface uses a server-side TTL.",
        "retry_same_route_id": "n/a",
    },
    "timeout_out_of_range": {
        "summary": "timeoutInMS is outside the range this layer will accept.",
        "retry_same_route_id": "n/a",
    },
    "no_availability": {
        "summary": "The host has no free slot inside the interval, so there is nothing to book.",
        "retry_same_route_id": "n/a",
    },
    "permission_denied": {
        "summary": "The credential lacks the Read or Schedule permission for this section.",
        "retry_same_route_id": "n/a",
    },
}

# --------------------------------------------------------------------------- #
# TTL: the durations, which the research does not publish
# --------------------------------------------------------------------------- #

#: Session lifetimes in milliseconds. The research establishes *that* a TTL
#: exists, that Concierge's is settable per request as ``timeoutInMS`` and that
#: links and handoff use a server-side one. It publishes **no durations**. These
#: are this build's numbers, and every entry says so.
DEFAULT_TTL_MS: dict[str, int] = {
    "concierge": 900_000,
    "links": 600_000,
    "handoff": 900_000,
}

#: Only Concierge's TTL is caller-settable, and only within these bounds.
#: Unsourced; the inference is ``session-ttl-durations``.
MIN_TTL_MS = 30_000
MAX_TTL_MS = 1_800_000

# --------------------------------------------------------------------------- #
# The two calls
# --------------------------------------------------------------------------- #

#: The researched call sequence, verbatim in shape.
CALLS: tuple[dict[str, str], ...] = (
    {
        "id": "discover_or_route",
        "n": "1",
        "name": "Discover or route",
        "returns": "a session with a list of available time slots and an identifier for the session (routeId)",
        "sourced": (
            "1. Discover or route - a first call returns a session with a list of available "
            "time slots and an identifier for the session (routeId)"
        ),
    },
    {
        "id": "book",
        "n": "2",
        "name": "Book",
        "returns": "a meetingId",
        "sourced": "2. Book - a second call passes the routeId and a chosen startTime to commit the meeting.",
    },
)

#: What the research says happens the moment a meeting is committed. Not an
#: automation the caller must configure - it is the documented consequence of
#: the book call.
ON_BOOK = (
    "Calendar invites are sent immediately. Bookings immediately emit the For New Meeting webhook."
)

#: The webhook a booking emits, and its event name.
WEBHOOK_NAME = "For New Meeting"
WEBHOOK_EVENT = "Created"

#: The four researched rules about a session, each with the sentence it comes
#: from. Served as data because these are the rules a caller is most likely to
#: get wrong, and a caller who can read them cannot.
SESSION_RULES: tuple[dict[str, str], ...] = (
    {
        "id": "single_use",
        "rule": "A session may be booked exactly once. A second book call with the same routeId is refused.",
        "sourced": "Sessions are single-use.",
        "enforced_by": "sessions.Session.consume",
    },
    {
        "id": "no_retry_on_failure",
        "rule": (
            "A *failed* book call also consumes the session. The researched instruction is explicit "
            "that the remedy is to start again from the discover or route step, not to retry."
        ),
        "sourced": (
            "On a schedule failure, do not retry the schedule call with the same routeId - start "
            "again from the discover or route step"
        ),
        "enforced_by": "sessions.Session.consume",
    },
    {
        "id": "short_lived",
        "rule": "A session expires on a server-side TTL, and an expired session cannot be booked.",
        "sourced": "The two-step session has a server-side TTL.",
        "enforced_by": "sessions.Session.is_expired",
    },
    {
        "id": "utc_slots",
        "rule": (
            "startTime is ISO-8601 UTC in responses and must be passed back verbatim. A value with "
            "no UTC designator is refused rather than guessed at."
        ),
        "sourced": "Slot times are UTC. The startTime in responses is ISO-8601 UTC; pass it back verbatim.",
        "enforced_by": "sessions.parse_start_time",
    },
)


def require_section(section: Any) -> str:
    """Normalise and validate a section name against the published three."""
    from dsr.headless_booking.errors import HeadlessBookingError

    value = str(section or "").strip().lower().replace("-", "_")
    if value not in SECTIONS:
        raise HeadlessBookingError(
            f"section {section!r} is not one of the published sections: {', '.join(SECTIONS)}"
        )
    return value


def require_permission(permission: Any) -> str:
    """Normalise and validate a token permission against the published two."""
    from dsr.headless_booking.errors import HeadlessBookingError

    value = str(permission or "").strip().lower()
    if value not in PERMISSIONS:
        raise HeadlessBookingError(
            f"permission {permission!r} is not one of the published permissions: "
            f"{', '.join(PERMISSIONS)}"
        )
    return value


def require_link_type(link_type: Any) -> str:
    """Normalise and validate a scheduling-link type against the published five."""
    from dsr.headless_booking.errors import HeadlessBookingError

    value = str(link_type or "").strip().lower().replace("-", "_")
    if value not in LINK_TYPES:
        raise HeadlessBookingError(
            f"link type {link_type!r} is not one of the published link types: "
            f"{', '.join(LINK_TYPES)}"
        )
    return value


def require_provider(provider: Any) -> str:
    """Normalise and validate a meeting-link provider against the published three."""
    from dsr.headless_booking.errors import HeadlessBookingError

    value = str(provider or "").strip().lower()
    if value not in MEETING_PROVIDERS:
        raise HeadlessBookingError(
            f"provider {provider!r} is not one of the published providers: "
            f"{', '.join(MEETING_PROVIDERS)}"
        )
    return value


def require_role(role: Any) -> str:
    """Normalise a role name. Every role is accepted; the *check* is elsewhere.

    Normalising rather than refusing is deliberate: the research's rule is about
    which roles may generate a token, not about which role names exist, and a
    caller whose role this build has never heard of must still get the researched
    refusal rather than a different error about spelling.
    """
    return str(role or "").strip().lower().replace(" ", "_").replace("-", "_")


def discovery_tools(section: str) -> list[str]:
    """The discovery tools for a section, as a list a client can render."""
    return list(DISCOVERY_TOOLS.get(section, ()))


def tool_for(section: str, call: str) -> str | None:
    """The MCP tool that mirrors one of the two calls on a section.

    ``call`` is ``"discover_or_route"`` or ``"book"``. Returns ``None`` for an
    unknown pair rather than raising, because this is a lookup a *client* makes
    when rendering a picker, and a picker that 400s over an unknown value is
    worse than a picker that shows nothing.
    """
    index = 0 if call in ("discover_or_route", "init", "route") else 1 if call == "book" else -1
    tools = MCP_TOOLS.get(section, ())
    return tools[index] if 0 <= index < len(tools) else None


def published_vocabulary() -> dict[str, Any]:
    """Everything a client needs to drive this workflow, in one response.

    A client renders its section picker, its permission picker and its call
    sequence from this, and its error handling from ``schedule_failures``, so
    the terms a caller sends and the terms it branches on cannot drift apart.
    """
    return {
        "sections": list(SECTIONS),
        "init_endpoints": dict(INIT_ENDPOINTS),
        "schedule_endpoints": dict(SCHEDULE_ENDPOINTS),
        "mcp_tools": {name: list(tools) for name, tools in MCP_TOOLS.items()},
        "discovery_tools": {name: list(tools) for name, tools in DISCOVERY_TOOLS.items()},
        "transport_guidance": TRANSPORT_GUIDANCE,
        "asset_kinds": dict(ASSET_KINDS),
        "link_types": list(LINK_TYPES),
        "discovered_link_types": list(DISCOVERED_LINK_TYPES),
        "ownership_requires_guest_email": OWNERSHIP_REQUIRES_GUEST_EMAIL,
        "meeting_providers": list(MEETING_PROVIDERS),
        "permissions": list(PERMISSIONS),
        "section_permissions": {name: list(perms) for name, perms in SECTION_PERMISSIONS.items()},
        "token_generator_roles": list(TOKEN_GENERATOR_ROLES),
        "token_generator_refused_roles": list(TOKEN_GENERATOR_REFUSED_ROLES),
        "token_prefix": TOKEN_PREFIX,
        "session_states": list(SESSION_STATES),
        "terminal_session_states": list(TERMINAL_SESSION_STATES),
        "session_rules": [dict(entry) for entry in SESSION_RULES],
        "calls": [dict(entry) for entry in CALLS],
        "on_book": ON_BOOK,
        "webhook": {"name": WEBHOOK_NAME, "event": WEBHOOK_EVENT},
        "schedule_failures": {name: dict(entry) for name, entry in SCHEDULE_FAILURES.items()},
        "init_failures": {name: dict(entry) for name, entry in INIT_FAILURES.items()},
        "ttl": {
            "default_ms": dict(DEFAULT_TTL_MS),
            "min_ms": MIN_TTL_MS,
            "max_ms": MAX_TTL_MS,
            "settable_by_caller": ["concierge"],
            "server_side_only": ["links", "handoff"],
            "sourced": False,
        },
    }
