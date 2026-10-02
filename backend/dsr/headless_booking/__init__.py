"""WF-056: book a meeting with no scheduling UI, headlessly.

A researched workflow, not a port: there is no source branch, and
``docs/research/digital-sales-room-workflows/wf/WF-056.md`` is the
specification. The researched decisions are the product. What they are:

* **Two calls, always.** "1. **Discover or route** - a first call returns a
  session with a list of available time slots and an identifier for the session
  (``routeId``); 2. **Book** - a second call passes the ``routeId`` and a chosen
  ``startTime`` to commit the meeting." No one-call shortcut is offered, because
  the rules below are about the gap between the two calls.
* **Sessions are single-use, and a *failure* spends one.** "Sessions are
  single-use. On a schedule failure, do not retry the schedule call with the same
  ``routeId`` - start again from the discover or route step." The failure half is
  the one that is easy to get wrong and is enforced here.
* **Short-lived, with a server-side TTL.** Settable per request as
  ``timeoutInMS`` on Concierge; server-side on links and handoff, where passing
  it is refused rather than ignored.
* **Slot times are UTC and passed back verbatim.** Enforced on both halves: a
  value with no UTC designator is refused rather than guessed at, and a value
  that is not one of the strings the session returned is refused as
  not-offered.
* **Scoped, admin-only, shown-once tokens.** "choosing the ``Schedule``
  permission for the relevant section ... plus ``Read`` where listing assets is
  needed. Token is shown once. Admins only." What is stored is a digest, so
  "shown once" is true in the only sense that survives an audit.
* **The commit is immediate and complete.** "Calendar invites are sent
  immediately", and bookings emit the ``For New Meeting`` webhook. The commit is
  implemented - meeting, invites and webhook event in one transaction - and the
  transmission is not, and every record says so.
* **Three surfaces, addressed three different ways**, with the six quoted
  endpoint pairs and the six MCP tools that mirror them.

Module map, in dependency order:

``errors``
    Three domain error types and the three HTTP answers they map to.
``vocabulary``
    The researched terms: sections, quoted endpoint paths, tool names,
    permissions, roles, the four session rules, and both failure vocabularies.
``availability``
    The slot engine: ``interval{startsAt,duration}`` plus a host's calendar
    becomes a list of wire-form UTC slots.
``credentials``
    ``Generate Token``: the Admin rule, per-section scope, and the digest that
    makes "shown once" true.
``assets``
    Bookable targets, and the three ways the research addresses them.
``sessions``
    The two-step session and the four researched rules that govern it.
``engine``
    The flow end to end over the audited store, with ``source`` required on
    every write and a booking committed in one transaction.
``inferences``
    Every judgement call the research leaves open, named and served so a
    reviewer can disagree with one by name.
"""

from __future__ import annotations

from dsr.headless_booking.assets import (
    host_calendar_key,
    meeting_link,
    normalise as normalise_asset,
)
from dsr.headless_booking.availability import (
    DEFAULT_MAX_SLOTS,
    DEFAULT_MEETING_MINUTES,
    DEFAULT_SLOT_MINUTES,
    Busy,
    format_slot,
    parse_calendar_block,
    parse_instant,
    parse_interval,
    slots,
)
from dsr.headless_booking.credentials import (
    digest as token_digest,
    generate_token,
    mask as mask_token,
    normalise as normalise_credential,
    public as public_credential,
    require_generator_role,
    require_scope,
    verify as verify_token,
)
from dsr.headless_booking.engine import (
    ASSET_COLLECTION,
    CALENDAR_COLLECTION,
    CALL_COLLECTION,
    COLLECTIONS,
    CREDENTIAL_COLLECTION,
    INVITE_COLLECTION,
    MEETING_COLLECTION,
    SESSION_COLLECTION,
    WEBHOOK_COLLECTION,
    HeadlessBooking,
    instructions,
)
from dsr.headless_booking.errors import (
    HeadlessBookingError,
    NotFound,
    PermissionDenied,
    Refusal,
    refusal,
)
from dsr.headless_booking.sessions import (
    PathSlots,
    Session,
    open_session,
    parse_start_time,
    require_open_session,
    resolve_timeout,
)
from dsr.headless_booking.vocabulary import (
    ASSET_KINDS,
    CALLS,
    DEFAULT_TTL_MS,
    DISCOVERED_LINK_TYPES,
    DISCOVERY_TOOLS,
    INIT_ENDPOINTS,
    INIT_FAILURES,
    LINK_TYPES,
    MAX_TTL_MS,
    MCP_TOOLS,
    MEETING_PROVIDERS,
    MIN_TTL_MS,
    ON_BOOK,
    OWNERSHIP_LINK_TYPE,
    PERMISSIONS,
    SCHEDULE_ENDPOINTS,
    SCHEDULE_FAILURES,
    SECTION_PERMISSIONS,
    SECTIONS,
    SESSION_RULES,
    SESSION_STATES,
    TERMINAL_SESSION_STATES,
    TOKEN_GENERATOR_REFUSED_ROLES,
    TOKEN_GENERATOR_ROLES,
    TOKEN_PREFIX,
    TRANSPORT_GUIDANCE,
    WEBHOOK_EVENT,
    WEBHOOK_NAME,
    discovery_tools,
    published_vocabulary,
    tool_for,
)

__all__ = [
    "ASSET_COLLECTION",
    "ASSET_KINDS",
    "Busy",
    "CALLS",
    "CALENDAR_COLLECTION",
    "CALL_COLLECTION",
    "COLLECTIONS",
    "CREDENTIAL_COLLECTION",
    "DEFAULT_MAX_SLOTS",
    "DEFAULT_MEETING_MINUTES",
    "DEFAULT_SLOT_MINUTES",
    "DEFAULT_TTL_MS",
    "DISCOVERED_LINK_TYPES",
    "DISCOVERY_TOOLS",
    "HeadlessBooking",
    "HeadlessBookingError",
    "INIT_ENDPOINTS",
    "INIT_FAILURES",
    "INVITE_COLLECTION",
    "LINK_TYPES",
    "MAX_TTL_MS",
    "MCP_TOOLS",
    "MEETING_COLLECTION",
    "MEETING_PROVIDERS",
    "MIN_TTL_MS",
    "ON_BOOK",
    "OWNERSHIP_LINK_TYPE",
    "PERMISSIONS",
    "PathSlots",
    "SCHEDULE_ENDPOINTS",
    "SCHEDULE_FAILURES",
    "SECTION_PERMISSIONS",
    "SECTIONS",
    "SESSION_COLLECTION",
    "SESSION_RULES",
    "SESSION_STATES",
    "Session",
    "NotFound",
    "PermissionDenied",
    "Refusal",
    "refusal",
    "TERMINAL_SESSION_STATES",
    "TOKEN_GENERATOR_REFUSED_ROLES",
    "TOKEN_GENERATOR_ROLES",
    "TOKEN_PREFIX",
    "TRANSPORT_GUIDANCE",
    "WEBHOOK_COLLECTION",
    "WEBHOOK_EVENT",
    "WEBHOOK_NAME",
    "token_digest",
    "discovery_tools",
    "format_slot",
    "generate_token",
    "host_calendar_key",
    "instructions",
    "mask_token",
    "meeting_link",
    "normalise_asset",
    "normalise_credential",
    "open_session",
    "parse_calendar_block",
    "parse_instant",
    "parse_interval",
    "parse_start_time",
    "public_credential",
    "published_vocabulary",
    "require_generator_role",
    "require_open_session",
    "require_scope",
    "resolve_timeout",
    "slots",
    "tool_for",
    "verify_token",
]
