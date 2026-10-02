"""Tests for WF-056: book a meeting with no scheduling UI (headless / AI agent).

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-056.md`` and section 6 of
``docs/research/raw/scheduling-meetings.md``. Nothing here is a preference of
this build unless it is listed in :mod:`dsr.headless_booking.inferences`, and
every inference in that registry has a test that checks it is still named, still
bounded, and still changeable.

The researched half
-------------------

* **Two calls.** "1. Discover or route - a first call returns a session with a
  list of available time slots and an identifier for the session (``routeId``);
  2. Book - a second call passes the ``routeId`` and a chosen ``startTime`` to
  commit the meeting." No one-call shortcut is offered, because every rule below
  is about the gap between them.
* **Single-use, and a failure spends one.** "Sessions are single-use. On a
  schedule failure, do not retry the schedule call with the same ``routeId`` -
  start again from the discover or route step." The failure half is the one an
  implementation gets wrong by omission, so it has the most tests here.
* **Short-lived.** A server-side TTL, ``timeoutInMS`` on Concierge and
  server-side on links/handoff.
* **UTC, verbatim.** "Slot times are UTC. The ``startTime`` in responses is
  ISO-8601 UTC; pass it back verbatim on the book call."
* **Scoped, admin-only, shown-once tokens.** Schedule per section plus Read where
  listing is needed; Admins only, and explicitly not Workspace Managers.
* **Immediate and complete.** "Calendar invites are sent immediately", and
  bookings immediately emit the ``For New Meeting`` webhook.
* **Three surfaces**, each with its own quoted endpoint pair and its own MCP
  tools; an Ownership link requires ``guestEmail``.

Bugs these tests were written to catch
-------------------------------------

Four, each of which was real during this build and none of which a happy-path
test would have found:

* The store strips envelope keys from ``data`` on write, so a session's
  ``created_at`` was silently dropped and every second call failed to parse it.
* A busy block filed against a host was read back through a key that did not
  match the one the asset side built, so the availability engine ignored the
  calendar entirely.
* Working hours compared ``timetz()`` against a naive ``time``, which raised
  rather than filtering - and had it not, would have wrapped at midnight and
  passed meetings that end after the working day.
* An unreadable timestamp was coerced to the epoch, which overlaps nothing, so a
  corrupt meeting row silently stopped being a conflict.

The HTTP half runs against the real app over a temporary database, the way
``test_features.py`` does, and asserts that every ``source`` recorded in the
audit log names a route the host actually mounted.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.headless_booking import (
    CALLS,
    DEFAULT_MEETING_MINUTES,
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
    Busy,
    HeadlessBooking,
    HeadlessBookingError,
    NotFound,
    PathSlots,
    PermissionDenied,
    Refusal,
    Session,
    format_slot,
    generate_token,
    host_calendar_key,
    inferences as headless_inferences,
    mask_token,
    meeting_link,
    normalise_asset,
    normalise_credential,
    parse_calendar_block,
    parse_instant,
    parse_interval,
    parse_start_time,
    published_vocabulary,
    require_generator_role,
    require_open_session,
    require_scope,
    resolve_timeout,
    slots,
    token_digest,
    tool_for,
    verify_token,
)
from dsr.headless_booking.availability import _floor_to_grid
from dsr.headless_booking.engine import (
    ASSET_COLLECTION,
    CALENDAR_COLLECTION,
    CALL_COLLECTION,
    CREDENTIAL_COLLECTION,
    INVITE_COLLECTION,
    MEETING_COLLECTION,
    SESSION_COLLECTION,
    WEBHOOK_COLLECTION,
)
from dsr.store import RecordStore
from fastapi.testclient import TestClient

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-056"

#: What the pure-domain tests pass as ``source``. Deliberately the exact shape a
#: route passes, so a test asserting on an audit row is asserting on the real
#: thing rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/sessions"
BOOK_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/sessions/{{route_id}}/book"

MODULE = "wf056_book_a_meeting_with_no_scheduling_ui_h"
FEATURE_ID = "wf-056-book-a-meeting-with-no-scheduling-ui-h"

#: A fixed clock, so the TTL rules are testable without sleeping. A session-expiry
#: test that depended on real time would either sleep or flake.
NOW = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def db(tmp_path):
    database = AuditedDatabase(tmp_path / "headless.db", mirror_dir=tmp_path / "mirror")
    yield database
    database.close()


@pytest.fixture()
def store(db):
    return RecordStore(db)


@pytest.fixture()
def clock():
    return lambda: NOW


@pytest.fixture()
def engine(store, clock):
    return HeadlessBooking(store, clock=clock)


@pytest.fixture()
def room(store):
    return store.create("room", {"name": "Northwind", "account": "Northwind"}, actor="dana")


@pytest.fixture()
def other_room(store):
    return store.create("room", {"name": "Contoso", "account": "Contoso"}, actor="sam")


def concierge_asset(engine, room_id, **overrides):
    # `slot_minutes` is deliberately absent: it falls back to the meeting length,
    # so a test that changes the duration changes the grid with it. Pinning it
    # here would make every duration test a test of the wrong default.
    spec = {
        "name": "Northwind enterprise",
        "section": "concierge",
        "router_slug": "northwind-enterprise",
        "host_name": "Dana Okafor",
        "host_email": "dana@example.com",
        "duration_minutes": 30,
        "work_start_hour": 9,
        "work_end_hour": 17,
        "utc_offset_minutes": 0,
    } | overrides
    return engine.create_asset(room_id, spec, actor="dana", source=SOURCE)


def link_asset(engine, room_id, link_type="personal", **overrides):
    spec = {
        "name": f"{link_type} link",
        "section": "links",
        "link_id": f"lnk-{link_type}",
        "link_type": link_type,
        "host_name": "Sam Ibrahim",
        "host_email": "sam@example.com",
        "duration_minutes": 30,
        "slot_minutes": 30,
    } | overrides
    return engine.create_asset(room_id, spec, actor="dana", source=SOURCE)


def handoff_asset(engine, room_id, **overrides):
    spec = {
        "name": "AE pod handoff",
        "section": "handoff",
        "workspace_id": "ws-ae",
        "booker_id": "usr-sdr",
        "host_email": "sdr@example.com",
        "duration_minutes": 30,
        "slot_minutes": 30,
        "paths": [
            {"path_id": "emea", "label": "EMEA", "host_email": "aisha@example.com"},
            {"path_id": "amer", "label": "AMER", "host_email": "marcus@example.com"},
        ],
    } | overrides
    return engine.create_asset(room_id, spec, actor="dana", source=SOURCE)


def future_interval(hours=36, start_delta=timedelta(days=1)):
    """An interval in the future that covers a whole working day.

    Anchored to midnight UTC the day after `NOW` rather than to `NOW` itself, so
    it cannot fall outside an asset's working hours depending on what time of
    day the test suite happens to run at. A 36-hour span covers one working day
    twice over, for any timezone the fixture assets configure.
    """
    starts = (NOW + start_delta).replace(hour=0, minute=0, second=0, microsecond=0)
    return {"startsAt": format_slot(starts), "duration": hours * 60}


def discover(engine, room_id, asset, **overrides):
    body = {
        "section": asset["data"]["section"],
        "asset_id": asset["id"],
        "interval": future_interval(),
        "guest": {"guestEmail": "buyer@example.com", "name": "Wen Li"},
    } | overrides
    return engine.discover(room_id, body, actor="dana", source=SOURCE)


def first_slot(session):
    for entry in session["schedulingData"]:
        if entry["startTimes"]:
            return entry["startTimes"][0]
    raise AssertionError("the session offered no slots")


def book_first(engine, room_id, session, **overrides):
    payload = {
        "startTime": first_slot(session),
        "guest": {"guestEmail": "buyer@example.com"},
    } | overrides
    return engine.book(room_id, session["routeId"], payload, actor="dana", source=BOOK_SOURCE)


@pytest.fixture()
def http(monkeypatch, tmp_path):
    """A client over a temporary database.

    ``get_headless`` is a FastAPI dependency, so the real routes already build the
    engine from ``StoreDep`` and the suite needs no override: the engine holds
    nothing beyond the store, so the production path and the test path are the
    same path.
    """
    monkeypatch.setenv("DSR_DB_PATH", str(tmp_path / "http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", tmp_path / "absent-frontend")
    with TestClient(app) as client:
        yield client


def client_store(client):
    return client.app.state.store.db


def mounted_routes(client, feature_id=FEATURE_ID):
    """Every (method, path) the host mounted for one feature, templates intact."""
    entry = next(
        (f for f in client.get("/api/features").json()["features"] if f["id"] == feature_id), None
    )
    assert entry is not None, f"{feature_id} is not mounted"
    return {(method, route["path"]) for route in entry["routes"] for method in route["methods"]}


def all_served_routes(client):
    """Every (method, path) the running app serves, core routes included.

    The audit log is shared, so a check of what it records has to be allowed to
    see a core write as well as a feature one.
    """
    routes = set()
    for route in app.routes:
        for method in getattr(route, "methods", None) or set():
            if method in ("HEAD", "OPTIONS"):
                continue
            routes.add((method, getattr(route, "path", "")))
    for feature in client.get("/api/features").json()["features"]:
        for route in feature["routes"]:
            for method in route["methods"]:
                routes.add((method, route["path"]))
    return routes


def source_names_a_mounted_route(source, routes):
    """Does ``"POST /api/wf-056/rooms/abc/book"`` name a route that exists?

    A recorded source carries concrete ids; a mounted path carries FastAPI's
    ``{param}`` placeholders. The pattern is built from the *template* and matched
    against the source, with each placeholder as one wildcard segment, and the
    literal parts escaped so a segment that happens to contain a regex
    metacharacter cannot make the pattern match something else.
    """
    method, _, path = source.partition(" ")
    for mounted_method, template in routes:
        if mounted_method != method:
            continue
        parts = re.split(r"(\{[^}]+\})", template)
        pattern = (
            "^"
            + "".join(r"[^/]+" if part.startswith("{") else re.escape(part) for part in parts)
            + "$"
        )
        if re.match(pattern, path):
            return True
    return False


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)
    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-056"
    assert entry["routes"]
    assert set(entry["exception_handlers"]) == {
        "HeadlessBookingError",
        "PermissionDenied",
        "NotFound",
    }


def test_feature_is_not_reported_as_failed(http):
    body = http.get("/api/features").json()
    assert FEATURE_ID not in {f["id"] for f in body["failed"]}
    assert any("route collision" in f["error"] for f in body["failed"]) is False


def test_the_prefix_is_ours_alone(http):
    body = http.get("/api/features").json()
    served = {
        (method, route["path"])
        for feature in body["features"]
        for route in feature["routes"]
        for method in route["methods"]
    }
    mine = {key for key in served if key[1].startswith(PREFIX)}
    others = {key for key in served if not key[1].startswith(PREFIX)}
    assert mine
    assert not mine & others


def test_feature_module_does_not_import_the_shared_app():
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "dsr.api" not in source
    assert "from dsr.deps import" in source


def test_frontend_descriptor_id_matches_the_backend_feature_id():
    descriptor = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / FEATURE_ID
        / "index.jsx"
    )
    text = descriptor.read_text(encoding="utf-8")
    assert load_feature(MODULE).FEATURE["id"] in text
    assert f"id: {FEATURE_ID!r}" in text


def test_the_two_researched_calls_get_the_two_researched_routes(http):
    """Call #1 and call #2 are the product, so they are the obvious routes."""
    routes = {path for _, path in mounted_routes(http)}
    assert f"{PREFIX}/rooms/{{room_id}}/sessions" in routes
    assert f"{PREFIX}/rooms/{{room_id}}/sessions/{{route_id}}/book" in routes


def test_there_is_no_one_call_shortcut(http):
    """A collapsing helper would hide the single-use rule, so none is offered.

    The research describes exactly two calls and is emphatic that the session
    between them is single-use. A route that opened a session and booked it in
    one request would make the rule impossible to exercise and would hide the
    retry the research forbids.
    """
    routes = {path for _, path in mounted_routes(http)}
    for path in routes:
        assert not path.endswith("/book-now")
        assert not path.endswith("/book-and-discover")


def test_room_scoped_paths_are_room_scoped(http):
    """The researched payload is a lead, and a lead belongs to a room."""
    routes = {path for _, path in mounted_routes(http)}
    for suffix in ("/sessions", "/meetings", "/calls"):
        assert f"{PREFIX}/rooms/{{room_id}}{suffix}" in routes
    assert f"{PREFIX}/rooms/{{room_id}}/sessions/{{route_id}}/book" in routes


def test_no_domain_module_imports_the_shared_app():
    """The rule exists for the whole package, not just the feature module."""
    package = Path(__file__).resolve().parents[1] / "dsr" / "headless_booking"
    for module in package.glob("*.py"):
        text = module.read_text(encoding="utf-8")
        assert "from dsr.api" not in text and "import dsr.api" not in text, module.name


# --------------------------------------------------------------------------- #
# The vocabulary: the quoted wire
# --------------------------------------------------------------------------- #


def test_the_three_sections_are_the_ones_the_research_names():
    """ "choosing the Schedule permission for the relevant section (Concierge /
    Scheduling-links / Handoff)" - three surfaces, and the permission is per one."""
    assert SECTIONS == ("concierge", "links", "handoff")


def test_each_section_has_its_quoted_init_path():
    assert INIT_ENDPOINTS["concierge"] == (
        "/api/fire-edge/v1/org/concierge/routers/{routerSlug}/rest"
    )
    assert INIT_ENDPOINTS["links"] == "/api/fire-edge/v1/org/schedulingLinks/init-simple"
    assert INIT_ENDPOINTS["handoff"] == (
        "/api/fire-edge/v1/org/handoff/workspace/{workspaceId}/booker/{userId}/init-simple"
    )


def test_each_section_has_its_quoted_schedule_path():
    assert SCHEDULE_ENDPOINTS["concierge"] == (
        "/api/fire-edge/v1/org/concierge/routing/{routeId}/schedule-simple"
    )
    assert SCHEDULE_ENDPOINTS["links"] == (
        "/api/fire-edge/v1/org/schedulingLinks/routing/{routeId}/schedule-simple"
    )
    assert SCHEDULE_ENDPOINTS["handoff"] == (
        "/api/fire-edge/v1/org/handoff/routing/{routingId}/router/{routerId}/path/{pathId}"
        "/booker/{userId}/schedule-simple"
    )


def test_only_the_handoff_schedule_path_carries_a_path_id():
    """The researched reason a handoff session can hold more than one path."""
    for section in ("concierge", "links"):
        assert "{pathId}" not in SCHEDULE_ENDPOINTS[section]
    assert "{pathId}" in SCHEDULE_ENDPOINTS["handoff"]


def test_the_six_mcp_tools_mirror_the_six_endpoints():
    assert MCP_TOOLS["concierge"] == ("concierge-route-by-slug", "concierge-schedule")
    assert MCP_TOOLS["links"] == ("scheduling-link-init", "scheduling-link-schedule")
    assert MCP_TOOLS["handoff"] == ("handoff-init", "handoff-schedule")


def test_every_mcp_tool_is_named_in_the_research():
    published = {tool for tools in MCP_TOOLS.values() for tool in tools}
    assert published == {
        "concierge-route-by-slug",
        "concierge-schedule",
        "scheduling-link-init",
        "scheduling-link-schedule",
        "handoff-init",
        "handoff-schedule",
    }


def test_the_discovery_tools_are_the_ones_the_research_lists():
    """ "workspace-list, user-find, scheduling-link-list-round-robin|ownership|
    group|admin-one-on-one." """
    assert DISCOVERY_TOOLS["handoff"] == ("workspace-list", "user-find")
    assert DISCOVERY_TOOLS["links"] == (
        "scheduling-link-list-round-robin",
        "scheduling-link-list-ownership",
        "scheduling-link-list-group",
        "scheduling-link-list-admin-one-on-one",
    )


def test_personal_is_a_link_type_with_no_discovery_tool():
    """The research names five link types and four discovery operations.

    Publishing both tuples, and the difference between them, is the honest
    answer: collapsing them would invent a ``scheduling-link-list-personal`` the
    research does not list.
    """
    assert "personal" in LINK_TYPES
    assert "personal" not in DISCOVERED_LINK_TYPES
    assert set(DISCOVERED_LINK_TYPES) == set(LINK_TYPES) - {"personal"}


def test_the_five_link_types_are_the_ones_the_research_names():
    """ "Scheduling Links (types: Personal / Admin (one-on-one) / Round Robin /
    Group / Ownership)" """
    assert LINK_TYPES == ("personal", "round_robin", "group", "ownership", "admin_one_on_one")


def test_the_three_meeting_providers_are_the_ones_data_sources_names():
    """ "Zoom/GMeet/Gong providers for the meeting link." """
    assert MEETING_PROVIDERS == ("zoom", "gmeet", "gong")


def test_the_two_permissions_are_schedule_and_read():
    """ "choosing the Schedule permission for the relevant section ... plus Read
    where listing assets is needed" """
    assert PERMISSIONS == ("schedule", "read")


def test_only_an_admin_may_generate_a_token():
    """ "Only users with the Admin role can generate API tokens in Command
    Center. Workspace Managers do not have access to the credentials page." """
    assert TOKEN_GENERATOR_ROLES == ("admin",)
    assert "workspace_manager" in TOKEN_GENERATOR_REFUSED_ROLES


def test_the_webhook_is_the_for_new_meeting_created_event():
    """ "Bookings immediately emit the For New Meeting webhook." """
    assert WEBHOOK_NAME == "For New Meeting"
    assert WEBHOOK_EVENT == "Created"


def test_the_two_calls_are_published_with_their_quotes():
    """ "1. Discover or route ... 2. Book ..." """
    assert [entry["id"] for entry in CALLS] == ["discover_or_route", "book"]
    for entry in CALLS:
        assert entry["sourced"]
        assert entry["returns"]


def test_on_book_carries_both_documented_consequences():
    """ "Calendar invites are sent immediately" and the webhook emission."""
    assert "Calendar invites are sent immediately" in ON_BOOK
    assert "For New Meeting" in ON_BOOK


def test_the_transport_guidance_is_quoted():
    assert "MCP" in TRANSPORT_GUIDANCE and "Edge" in TRANSPORT_GUIDANCE


def test_the_four_sourced_session_rules_are_published():
    ids = {entry["id"] for entry in SESSION_RULES}
    assert ids == {"single_use", "no_retry_on_failure", "short_lived", "utc_slots"}
    for entry in SESSION_RULES:
        assert entry["sourced"]
        assert entry["enforced_by"]


def test_every_published_failure_refuses_a_retry_on_the_same_route_id():
    """The researched instruction has to be something a caller can *act* on.

    Every schedule failure names the same remedy, and publishing
    ``retry_same_route_id: false`` for each is what turns a sentence in a
    message into a boolean a client can branch on.
    """
    for reason, entry in SCHEDULE_FAILURES.items():
        assert entry["retry_same_route_id"] == "no", reason
        assert entry["next_step"], reason
        assert entry["summary"], reason


def test_every_session_state_and_its_terminality_is_published():
    assert SESSION_STATES == ("open", "booked", "failed", "expired")
    assert set(TERMINAL_SESSION_STATES) == set(SESSION_STATES) - {"open"}


def test_the_vocabulary_serves_every_published_term():
    served = published_vocabulary()
    assert served["sections"] == list(SECTIONS)
    assert served["permissions"] == list(PERMISSIONS)
    assert served["link_types"] == list(LINK_TYPES)
    assert served["discovered_link_types"] == list(DISCOVERED_LINK_TYPES)
    assert served["session_states"] == list(SESSION_STATES)
    assert set(served["schedule_failures"]) == set(SCHEDULE_FAILURES)
    assert set(served["init_failures"]) == set(INIT_FAILURES)


def test_the_vocabulary_flags_the_ttl_as_unsourced():
    """The research publishes no duration, so the numbers say so themselves.

    A client rendering a picker cannot tell a sourced number from a chosen one
    unless the payload says which, and a reviewer reading a demo cannot either.
    """
    ttl = published_vocabulary()["ttl"]
    assert ttl["sourced"] is False
    assert ttl["settable_by_caller"] == ["concierge"]
    assert ttl["server_side_only"] == ["links", "handoff"]


def test_the_vocabulary_does_not_claim_a_personal_discovery_tool():
    served = published_vocabulary()
    assert "scheduling-link-list-personal" not in served["discovery_tools"]["links"]


def test_tool_for_maps_each_surface_and_call_to_its_mcp_tool():
    for section in SECTIONS:
        assert tool_for(section, "discover_or_route")
        assert tool_for(section, "book")


def test_tool_for_returns_none_for_an_unknown_pair_rather_than_raising():
    """A picker that 400s over an unknown value is worse than one showing nothing."""
    assert tool_for("concierge", "cancel") is None
    assert tool_for("nope", "book") is None


# --------------------------------------------------------------------------- #
# Timestamps: the UTC half of the researched rule
# --------------------------------------------------------------------------- #


def test_a_z_designator_is_accepted():
    assert parse_instant("2026-10-02T09:00:00Z", field="t") == datetime(
        2026, 10, 2, 9, 0, tzinfo=timezone.utc
    )


def test_a_bare_date_is_midnight_utc():
    """A date is unambiguous, so refusing it would refuse the common spelling."""
    assert parse_instant("2026-10-02", field="t") == datetime(2026, 10, 2, tzinfo=timezone.utc)


def test_a_naive_instant_is_refused_rather_than_assumed():
    """The researched rule exists precisely to stop a five-hour booking error.

    Picking a zone for the caller is the failure the rule prevents, and the
    direction of the error would be invisible in the audit log.
    """
    with pytest.raises(HeadlessBookingError) as caught:
        parse_instant("2026-10-02T09:00:00", field="interval.startsAt")
    assert "UTC" in str(caught.value)
    assert "no UTC designator" in str(caught.value)


def test_a_non_utc_offset_is_converted_not_refused():
    """It is a real instant; it is simply not one of the offered slot strings."""
    assert parse_instant("2026-10-02T14:30:00+05:30", field="t") == datetime(
        2026, 10, 2, 9, 0, tzinfo=timezone.utc
    )


def test_an_unparseable_instant_is_refused_by_name():
    with pytest.raises(HeadlessBookingError) as caught:
        parse_instant("next tuesday", field="interval.startsAt")
    assert "ISO-8601" in str(caught.value)


def test_an_empty_instant_is_refused_by_name():
    with pytest.raises(HeadlessBookingError) as caught:
        parse_instant("", field="interval.startsAt")
    assert "interval.startsAt is required" in str(caught.value)


def test_the_wire_format_is_utc_with_seconds_and_a_z():
    """ "The startTime in responses is ISO-8601 UTC" - the string is the rule."""
    assert format_slot(datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)) == "2026-10-02T09:00:00Z"


def test_every_offered_slot_carries_a_z():
    """Every string this layer emits must be passable back verbatim."""
    found = slots(
        window_start=datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc),
        window=timedelta(hours=8),
        busy=[],
        meeting_minutes=30,
        slot_minutes=30,
        work_start_hour=9,
        work_end_hour=17,
        now=datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc),
    )
    assert found
    assert all(value.endswith("Z") for value in found)
    assert all(len(value) == 20 for value in found)


def test_the_same_slot_parses_back_to_the_same_instant():
    """The whole verbatim rule rests on this round trip."""
    emitted = "2026-10-02T09:00:00Z"
    assert parse_start_time(emitted) == emitted


# --------------------------------------------------------------------------- #
# The interval
# --------------------------------------------------------------------------- #


def test_the_interval_is_what_makes_this_a_booking():
    """ "the difference is whether you pass an interval" - without one there are
    no slots, so the field is required rather than ignored."""
    with pytest.raises(HeadlessBookingError) as caught:
        parse_interval(None)
    assert "distinguishes scheduling" in str(caught.value)


def test_the_interval_needs_a_starts_at():
    with pytest.raises(HeadlessBookingError):
        parse_interval({"duration": 60})


def test_the_interval_needs_a_duration():
    """The research spells it ``interval{startsAt,duration}`` - both fields."""
    with pytest.raises(HeadlessBookingError) as caught:
        parse_interval({"startsAt": "2026-10-02T00:00:00Z"})
    assert "duration" in str(caught.value)


def test_the_interval_duration_is_read_as_minutes():
    start, window = parse_interval({"startsAt": "2026-10-02T00:00:00Z", "duration": 90})
    assert start == datetime(2026, 10, 2, tzinfo=timezone.utc)
    assert window == timedelta(minutes=90)


def test_a_negative_or_zero_interval_is_refused():
    for value in (0, -30):
        with pytest.raises(HeadlessBookingError):
            parse_interval({"startsAt": "2026-10-02T00:00:00Z", "duration": value})


def test_an_absurd_window_is_refused():
    """Without a cap, ``duration`` is unbounded work from an untrusted number."""
    with pytest.raises(HeadlessBookingError) as caught:
        parse_interval({"startsAt": "2026-10-02T00:00:00Z", "duration": 60 * 24 * 400})
    assert "90 days" in str(caught.value)


def test_a_non_numeric_duration_is_refused():
    with pytest.raises(HeadlessBookingError):
        parse_interval({"startsAt": "2026-10-02T00:00:00Z", "duration": "an hour"})


def test_an_interval_that_is_not_an_object_is_refused():
    with pytest.raises(HeadlessBookingError):
        parse_interval("tomorrow")


# --------------------------------------------------------------------------- #
# The availability engine
# --------------------------------------------------------------------------- #


def test_slots_land_inside_working_hours():
    found = slots(
        window_start=datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc),
        window=timedelta(hours=24),
        busy=[],
        meeting_minutes=30,
        slot_minutes=30,
        work_start_hour=9,
        work_end_hour=17,
        now=datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc),
    )
    assert found[0] == "2026-10-05T09:00:00Z"
    assert found[-1] == "2026-10-05T16:30:00Z"


def test_a_meeting_must_end_inside_the_working_day():
    """A 16:00 start on a 09:00-17:00 day with a 90 minute length is outside.

    The bug this catches: taking the time-of-day of the *local end* wraps at
    midnight, so a late meeting silently passes the check.
    """
    found = slots(
        window_start=datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc),
        window=timedelta(hours=24),
        busy=[],
        meeting_minutes=90,
        slot_minutes=30,
        work_start_hour=9,
        work_end_hour=17,
        now=datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc),
    )
    assert found[-1] == "2026-10-05T15:30:00Z"
    assert "2026-10-05T16:00:00Z" not in found


def test_slots_are_never_offered_on_a_weekend():
    found = slots(
        window_start=datetime(2026, 10, 3, 0, 0, tzinfo=timezone.utc),  # a Saturday
        window=timedelta(days=2),
        busy=[],
        meeting_minutes=30,
        slot_minutes=30,
        work_start_hour=9,
        work_end_hour=17,
        now=datetime(2026, 10, 3, 0, 0, tzinfo=timezone.utc),
    )
    assert all(datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").weekday() < 5 for value in found)


def test_a_configured_work_week_can_include_a_weekend():
    found = slots(
        window_start=datetime(2026, 10, 3, 0, 0, tzinfo=timezone.utc),
        window=timedelta(days=1),
        busy=[],
        meeting_minutes=30,
        slot_minutes=30,
        work_days=[5],
        work_start_hour=9,
        work_end_hour=17,
        now=datetime(2026, 10, 3, 0, 0, tzinfo=timezone.utc),
    )
    assert found


def test_working_hours_follow_the_assets_local_time_not_utc():
    """A 10:00-17:00 +05:30 desk is 04:30-11:30 UTC, and offers those slots.

    The last slot is 11:00Z, not 11:30Z: a 30-minute meeting has to *end* by
    17:00 local, so the final start is 16:30 local = 11:00Z.
    """
    found = slots(
        window_start=datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc),
        window=timedelta(hours=24),
        busy=[],
        meeting_minutes=30,
        slot_minutes=30,
        work_start_hour=10,
        work_end_hour=17,
        utc_offset_minutes=330,
        now=datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc),
    )
    assert found[0] == "2026-10-05T04:30:00Z"
    assert found[-1] == "2026-10-05T11:00:00Z"


def test_a_busy_block_removes_its_slots():
    """The bug this catches: a block filed under a key the asset side never asks
    for, so the calendar was read but ignored."""
    base = dict(
        window_start=datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc),
        window=timedelta(hours=24),
        meeting_minutes=30,
        slot_minutes=30,
        work_start_hour=9,
        work_end_hour=17,
        now=datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc),
    )
    free = slots(busy=[], **base)
    busy = slots(
        busy=[
            Busy(
                starts_at=datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc),
                ends_at=datetime(2026, 10, 5, 11, 0, tzinfo=timezone.utc),
                label="standup",
            )
        ],
        **base,
    )
    assert "2026-10-05T10:00:00Z" in free
    assert "2026-10-05T10:00:00Z" not in busy
    assert "2026-10-05T10:30:00Z" not in busy
    assert "2026-10-05T11:00:00Z" in busy


def test_a_meeting_ending_exactly_when_a_block_starts_is_not_a_conflict():
    """Half-open on both sides: a back-to-back booking is a booking."""
    block = Busy(
        starts_at=datetime(2026, 10, 5, 9, 30, tzinfo=timezone.utc),
        ends_at=datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc),
    )
    # 09:00-09:30 ends exactly when the block starts: no overlap.
    assert not block.overlaps(
        datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 5, 9, 30, tzinfo=timezone.utc),
    )
    # 09:15-09:45 straddles it: overlap.
    assert block.overlaps(
        datetime(2026, 10, 5, 9, 15, tzinfo=timezone.utc),
        datetime(2026, 10, 5, 9, 45, tzinfo=timezone.utc),
    )
    # 10:00-10:30 starts exactly when the block ends: no overlap.
    assert not block.overlaps(
        datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 5, 10, 30, tzinfo=timezone.utc),
    )


def test_a_slot_in_the_past_is_never_offered():
    """A session must not hand back a time that has already begun."""
    found = slots(
        window_start=datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc),
        window=timedelta(hours=8),
        busy=[],
        meeting_minutes=30,
        slot_minutes=30,
        work_start_hour=8,
        work_end_hour=17,
        now=datetime(2026, 10, 5, 10, 15, tzinfo=timezone.utc),
        lead_minutes=30,
    )
    assert found[0] == "2026-10-05T11:00:00Z"


def test_the_grid_is_anchored_so_overlapping_sessions_agree():
    """The bug this catches: a window-anchored grid.

    A grid anchored to each request's window would shift two lists against each
    other, and a caller holding both would see one hour under two spellings.
    """
    first = slots(
        window_start=datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc),
        window=timedelta(hours=2),
        busy=[],
        meeting_minutes=30,
        slot_minutes=30,
        work_start_hour=9,
        work_end_hour=17,
        now=datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc),
    )
    second = slots(
        window_start=datetime(2026, 10, 5, 9, 30, tzinfo=timezone.utc),
        window=timedelta(hours=2),
        busy=[],
        meeting_minutes=30,
        slot_minutes=30,
        work_start_hour=9,
        work_end_hour=17,
        now=datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc),
    )
    overlap = set(first) & set(second)
    assert overlap
    for value in overlap:
        assert parse_instant(value, field="t") in {
            parse_instant(other, field="t") for other in first
        }


def test_the_grid_floor_lands_on_the_epoch():
    assert _floor_to_grid(datetime(2026, 10, 5, 9, 7, tzinfo=timezone.utc), 30) == datetime(
        2026, 10, 5, 9, 30, tzinfo=timezone.utc
    )
    assert _floor_to_grid(datetime(2026, 10, 5, 9, 30, tzinfo=timezone.utc), 30) == datetime(
        2026, 10, 5, 9, 30, tzinfo=timezone.utc
    )


def test_a_grid_finer_than_the_meeting_is_allowed_and_yields_overlapping_starts():
    """45 minutes is the common sales demo, so forbidding it is not an option.

    An earlier version of this module refused any asset whose length was not a
    multiple of the grid, which forbade the ordinary case to prevent a corner
    one. The overlap is caught where it bites instead - at the book call - by the
    recheck that produces the researched `slot_taken` refusal.
    """
    found = slots(
        window_start=datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc),
        window=timedelta(hours=2),
        busy=[],
        meeting_minutes=45,
        slot_minutes=30,
        work_start_hour=9,
        work_end_hour=17,
        now=datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc),
    )
    assert "2026-10-05T09:00:00Z" in found
    assert "2026-10-05T09:30:00Z" in found


def test_a_finer_grid_double_booking_is_refused_at_the_book_call(engine, room, store):
    """The guard that replaced the configuration refusal, and the researched
    reason it produces."""
    asset = concierge_asset(
        engine,
        room["id"],
        duration_minutes=45,
        slot_minutes=30,
        work_start_hour=9,
        work_end_hour=12,
    )
    session = discover(engine, room["id"], asset)
    offered = session["schedulingData"][0]["startTimes"]
    assert len(offered) >= 2

    book_first(engine, room["id"], session, startTime=offered[0])
    second = engine.discover(
        room["id"],
        {
            "section": "concierge",
            "asset_id": asset["id"],
            "interval": future_interval(),
            "guest": {"guestEmail": "second@example.com"},
        },
        source=SOURCE,
    )
    # The overlapping start is no longer offered, because the booking is a block.
    assert offered[1] not in second["schedulingData"][0]["startTimes"]
    with pytest.raises(Refusal) as caught:
        book_first(engine, room["id"], second, startTime=offered[1])
    assert caught.value.reason == "start_time_not_offered"
    assert len(store.list(MEETING_COLLECTION)) == 1


def test_a_slot_grid_defaults_to_the_meeting_length_so_starts_cannot_overlap(engine, room):
    """The default configuration is the safe one, without a configuration rule.

    The gaps are *multiples* of 45 rather than all exactly 45, because the test
    window spans a day boundary and the overnight gap is not on the grid. What
    matters is that no two offered starts are closer together than a meeting.
    """
    asset = concierge_asset(engine, room["id"], duration_minutes=45)
    assert asset["data"]["slot_minutes"] == 45
    session = discover(engine, room["id"], asset)
    offered = session["schedulingData"][0]["startTimes"]
    starts = [parse_instant(value, field="t") for value in offered]
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(starts, starts[1:], strict=False)]
    assert gaps
    assert min(gaps) == 45
    assert all(gap % 45 == 0 for gap in gaps)


def test_impossible_working_hours_are_refused():
    with pytest.raises(HeadlessBookingError):
        slots(
            window_start=datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc),
            window=timedelta(hours=4),
            busy=[],
            meeting_minutes=30,
            slot_minutes=30,
            work_start_hour=17,
            work_end_hour=9,
        )


def test_an_empty_work_week_is_refused_by_the_asset_not_the_engine():
    """An asset with no working days yields no slots, which is its own state."""
    with pytest.raises(HeadlessBookingError) as caught:
        normalise_asset(
            {
                "name": "x",
                "section": "concierge",
                "router_slug": "x",
                "host_email": "a@example.com",
                "work_days": [],
            }
        )
    assert "work_days" in str(caught.value)


def test_the_slot_list_is_capped():
    """An unbounded slot list is not a decision a caller can make."""
    found = slots(
        window_start=datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc),
        window=timedelta(days=30),
        busy=[],
        meeting_minutes=30,
        slot_minutes=30,
        work_start_hour=9,
        work_end_hour=17,
        now=datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc),
        max_slots=5,
    )
    assert len(found) == 5


def test_a_non_positive_grid_or_cap_is_refused():
    base = dict(
        window_start=datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc),
        window=timedelta(hours=4),
        busy=[],
        meeting_minutes=30,
    )
    with pytest.raises(HeadlessBookingError):
        slots(slot_minutes=0, **base)
    with pytest.raises(HeadlessBookingError):
        slots(slot_minutes=30, max_slots=0, **base)
    zero_meeting = {**base, "meeting_minutes": 0}
    with pytest.raises(HeadlessBookingError):
        slots(slot_minutes=30, **zero_meeting)


# --------------------------------------------------------------------------- #
# Calendar blocks
# --------------------------------------------------------------------------- #


def test_a_calendar_block_is_read_from_an_explicit_pair():
    block = parse_calendar_block(
        {"startsAt": "2026-10-05T10:00:00Z", "endsAt": "2026-10-05T11:00:00Z"}
    )
    assert block.ends_at - block.starts_at == timedelta(hours=1)


def test_a_calendar_block_is_read_from_a_start_and_a_duration():
    block = parse_calendar_block({"startTime": "2026-10-05T10:00:00Z", "duration": 90})
    assert block.ends_at - block.starts_at == timedelta(minutes=90)


def test_a_calendar_block_with_no_end_is_refused():
    """An unbounded busy block makes every later slot unavailable.

    A booking system whose failure mode is "nothing is ever free" is not a
    failure mode, it is an outage.
    """
    with pytest.raises(HeadlessBookingError) as caught:
        parse_calendar_block({"startsAt": "2026-10-05T10:00:00Z"})
    assert "unbounded busy block" in str(caught.value)


def test_a_backwards_calendar_block_is_refused():
    with pytest.raises(HeadlessBookingError):
        parse_calendar_block({"startsAt": "2026-10-05T11:00:00Z", "endsAt": "2026-10-05T10:00:00Z"})


def test_a_calendar_block_must_be_an_object():
    with pytest.raises(HeadlessBookingError):
        parse_calendar_block("10am")


# --------------------------------------------------------------------------- #
# Assets: the three surfaces, addressed three ways
# --------------------------------------------------------------------------- #


def test_a_concierge_asset_is_a_router_identified_by_its_slug():
    """/concierge/routers/{routerSlug}/rest - the identity is in the path."""
    spec = normalise_asset(
        {"name": "x", "section": "concierge", "router_slug": "nw", "host_email": "a@example.com"}
    )
    assert spec["kind"] == "router"
    assert spec["router_slug"] == "nw"


def test_a_concierge_asset_without_a_slug_is_refused_with_its_path():
    with pytest.raises(HeadlessBookingError) as caught:
        normalise_asset({"name": "x", "section": "concierge", "host_email": "a@example.com"})
    assert "{routerSlug}" in str(caught.value)


def test_a_scheduling_link_is_identified_by_its_id_and_type():
    """/schedulingLinks/init-simple carries neither, so they are in the body."""
    spec = normalise_asset(
        {
            "name": "x",
            "section": "links",
            "link_id": "lnk-1",
            "link_type": "round_robin",
            "host_email": "a@example.com",
        }
    )
    assert spec["kind"] == "scheduling_link"
    assert spec["link_id"] == "lnk-1"
    assert spec["link_type"] == "round_robin"


def test_an_ownership_link_records_that_it_requires_a_guest_email():
    """ "For Ownership links, also pass guestEmail in the init call." """
    spec = normalise_asset(
        {
            "name": "x",
            "section": "links",
            "link_id": "lnk-1",
            "link_type": OWNERSHIP_LINK_TYPE,
            "host_email": "a@example.com",
        }
    )
    assert spec["requires_guest_email"] is True


def test_a_non_ownership_link_does_not_require_a_guest_email():
    spec = normalise_asset(
        {
            "name": "x",
            "section": "links",
            "link_id": "l",
            "link_type": "personal",
            "host_email": "a@example.com",
        }
    )
    assert spec["requires_guest_email"] is False


def test_a_handoff_asset_is_a_workspace_a_booker_and_its_paths():
    """/handoff/workspace/{workspaceId}/booker/{userId}/init-simple"""
    spec = normalise_asset(
        {
            "name": "x",
            "section": "handoff",
            "workspace_id": "ws",
            "booker_id": "usr",
            "host_email": "a@example.com",
            "paths": [{"path_id": "p1", "host_email": "b@example.com"}],
        }
    )
    assert spec["kind"] == "handoff_router"
    assert spec["workspace_id"] == "ws"
    assert spec["booker_id"] == "usr"
    assert [path["path_id"] for path in spec["paths"]] == ["p1"]


def test_a_handoff_asset_without_a_booker_is_refused_with_its_path():
    with pytest.raises(HeadlessBookingError) as caught:
        normalise_asset(
            {
                "name": "x",
                "section": "handoff",
                "workspace_id": "ws",
                "host_email": "a@example.com",
                "paths": [{"path_id": "p", "host_email": "b@example.com"}],
            }
        )
    assert "{userId}" in str(caught.value)


def test_a_handoff_asset_needs_at_least_one_path():
    """The init response *is* a list of path results; an empty one cannot render."""
    with pytest.raises(HeadlessBookingError) as caught:
        normalise_asset(
            {
                "name": "x",
                "section": "handoff",
                "workspace_id": "ws",
                "booker_id": "u",
                "host_email": "a@example.com",
            }
        )
    assert "at least one path" in str(caught.value)


def test_two_paths_may_not_share_a_path_id():
    """The schedule call names one path by id, so duplicates are ambiguous."""
    with pytest.raises(HeadlessBookingError) as caught:
        normalise_asset(
            {
                "name": "x",
                "section": "handoff",
                "workspace_id": "ws",
                "booker_id": "u",
                "host_email": "a@example.com",
                "paths": [
                    {"path_id": "p", "host_email": "b@example.com"},
                    {"path_id": "p", "host_email": "c@example.com"},
                ],
            }
        )
    assert "duplicate ids" in str(caught.value)


def test_a_path_needs_a_host_because_availability_is_its_own_calendar():
    with pytest.raises(HeadlessBookingError) as caught:
        normalise_asset(
            {
                "name": "x",
                "section": "handoff",
                "workspace_id": "ws",
                "booker_id": "u",
                "host_email": "a@example.com",
                "paths": [{"path_id": "p"}],
            }
        )
    assert "host_email" in str(caught.value)


def test_a_path_may_not_be_declared_without_a_name():
    with pytest.raises(HeadlessBookingError):
        normalise_asset(
            {
                "name": "x",
                "section": "handoff",
                "workspace_id": "ws",
                "booker_id": "u",
                "host_email": "a@example.com",
            }
        )


def test_an_asset_needs_a_host_email_because_invites_go_out_immediately():
    with pytest.raises(HeadlessBookingError) as caught:
        normalise_asset({"name": "x", "section": "concierge", "router_slug": "nw"})
    assert "invites are sent immediately" in str(caught.value)


def test_the_kind_follows_from_the_section():
    with pytest.raises(HeadlessBookingError) as caught:
        normalise_asset(
            {
                "name": "x",
                "section": "concierge",
                "kind": "scheduling_link",
                "router_slug": "nw",
                "host_email": "a@example.com",
            }
        )
    assert "follows from the section" in str(caught.value)


def test_a_grid_finer_than_the_meeting_is_accepted_at_configuration_time():
    """The book call is the guard, not the configuration."""
    spec = normalise_asset(
        {
            "name": "x",
            "section": "concierge",
            "router_slug": "nw",
            "host_email": "a@example.com",
            "duration_minutes": 45,
            "slot_minutes": 30,
        }
    )
    assert spec["slot_minutes"] == 30
    assert spec["duration_minutes"] == 45


def test_a_non_positive_grid_is_refused():
    with pytest.raises(HeadlessBookingError) as caught:
        normalise_asset(
            {
                "name": "x",
                "section": "concierge",
                "router_slug": "nw",
                "host_email": "a@example.com",
                "slot_minutes": 0,
            }
        )
    assert "positive number of minutes" in str(caught.value)


def test_working_hours_must_run_forward():
    with pytest.raises(HeadlessBookingError):
        normalise_asset(
            {
                "name": "x",
                "section": "concierge",
                "router_slug": "nw",
                "host_email": "a@example.com",
                "work_start_hour": 17,
                "work_end_hour": 9,
            }
        )


def test_an_unknown_link_type_is_refused_by_name():
    with pytest.raises(HeadlessBookingError) as caught:
        normalise_asset(
            {
                "name": "x",
                "section": "links",
                "link_id": "l",
                "link_type": "vip",
                "host_email": "a@example.com",
            }
        )
    assert "vip" in str(caught.value)
    assert "round_robin" in str(caught.value)


def test_an_out_of_range_utc_offset_is_refused():
    with pytest.raises(HeadlessBookingError):
        normalise_asset(
            {
                "name": "x",
                "section": "concierge",
                "router_slug": "nw",
                "host_email": "a@example.com",
                "utc_offset_minutes": 900,
            }
        )


def test_the_meeting_link_is_derived_and_says_so():
    """A plausible-looking join link that was not fetched is worse than a
    derived one, so the record says which it is."""
    assert meeting_link("zoom", "mtg_1").endswith("mtg_1")
    assert "invalid" in meeting_link("zoom", "mtg_1")
    assert "invalid" in meeting_link("gmeet", "mtg_1")
    assert "invalid" in meeting_link("gong", "mtg_1")


def test_the_calendar_key_is_the_host_not_the_asset():
    """One person's calendar is the same whichever surface books them.

    A per-asset key would let this product double-book the same host across a
    Concierge router and a scheduling link.
    """
    spec = {"host_email": "dana@example.com"}
    assert host_calendar_key("concierge", spec) == "dana@example.com"
    assert host_calendar_key("links", spec) == "dana@example.com"


def test_a_handoff_path_gets_its_own_calendar_key():
    spec = {"host_email": "sdr@example.com"}
    path = {"path_id": "emea", "host_email": "aisha@example.com"}
    assert host_calendar_key("handoff", spec, path) == "aisha@example.com|emea"


def test_patching_an_asset_revalidates_the_merged_result(engine, room):
    """A patch cannot leave an asset that violates a configuration rule."""
    asset = concierge_asset(engine, room["id"])
    with pytest.raises(HeadlessBookingError) as caught:
        engine.update_asset(asset["id"], {"work_start_hour": 17, "work_end_hour": 9}, source=SOURCE)
    assert "must be before" in str(caught.value)


def test_patching_an_asset_cannot_drop_its_required_host(engine, room):
    asset = concierge_asset(engine, room["id"])
    with pytest.raises(HeadlessBookingError) as caught:
        engine.update_asset(asset["id"], {"host_email": ""}, source=SOURCE)
    assert "invites are sent immediately" in str(caught.value)


def test_patching_an_asset_changes_only_what_was_asked(engine, room):
    asset = concierge_asset(engine, room["id"], note="original")
    patched = engine.update_asset(asset["id"], {"provider": "gong"}, source=SOURCE)
    assert patched["data"]["provider"] == "gong"
    assert patched["data"]["note"] == "original"


def test_a_disabled_asset_refuses_rather_than_offering_slots(engine, room, store):
    """ "A bookable asset that is switched off must refuse rather than quietly
    offer slots nothing will honour." """
    asset = concierge_asset(engine, room["id"], enabled=False)
    with pytest.raises(Refusal) as caught:
        discover(engine, room["id"], asset)
    assert caught.value.reason == "asset_disabled"
    assert store.list(SESSION_COLLECTION) == []


# --------------------------------------------------------------------------- #
# Credentials: the Admin rule, per-section scope, shown once
# --------------------------------------------------------------------------- #


def test_a_token_is_prefixed_and_long_enough_to_be_a_credential():
    token = generate_token()
    assert token.startswith(TOKEN_PREFIX)
    assert len(token) > len(TOKEN_PREFIX) + 16


def test_two_tokens_differ():
    assert generate_token() != generate_token()


def test_the_stored_form_is_a_digest_not_the_token():
    """This is what makes "token is shown once" true in a sense that survives."""
    token = generate_token()
    stored = token_digest(token)
    assert stored != token
    assert token not in stored
    assert stored == token_digest(token)


def test_a_masked_hint_shows_a_prefix_and_the_last_four():
    token = generate_token()
    hint = mask_token(token)
    assert hint["token_prefix"].startswith(TOKEN_PREFIX)
    assert hint["token_last4"] == token[-4:]


def test_the_role_guard_admits_an_admin():
    assert require_generator_role("admin") == "admin"


def test_a_workspace_manager_is_refused_with_the_researchs_sentence():
    """ "Workspace Managers do not have access to the credentials page." """
    with pytest.raises(PermissionDenied) as caught:
        require_generator_role("workspace_manager")
    assert "Workspace Managers do not have access" in str(caught.value)
    assert caught.value.required == "role:admin"


def test_any_other_role_is_refused_naming_the_roles_that_may():
    with pytest.raises(PermissionDenied) as caught:
        require_generator_role("intern")
    assert "admin" in str(caught.value)


def test_a_credential_defaults_to_all_three_sections_and_both_permissions():
    spec = normalise_credential({"label": "x"})
    assert spec["sections"] == list(SECTIONS)
    assert spec["permissions"] == list(PERMISSIONS)


def test_a_credential_needs_a_label():
    with pytest.raises(HeadlessBookingError) as caught:
        normalise_credential({"permissions": ["read"]})
    assert "label" in str(caught.value)


def test_a_credential_scope_is_normalised_so_two_spellings_agree():
    first = normalise_credential({"label": "x", "sections": ["links", "concierge"]})
    second = normalise_credential({"label": "x", "sections": ["concierge", "links"]})
    assert first["sections"] == second["sections"] == ["concierge", "links"]


def test_a_credential_scope_accepts_a_comma_separated_string():
    """Both spellings reach the same stored value, in the published order."""
    spec = normalise_credential({"label": "x", "permissions": "read, schedule"})
    assert spec["permissions"] == ["schedule", "read"]


def test_an_unknown_section_is_refused_by_name():
    with pytest.raises(HeadlessBookingError) as caught:
        normalise_credential({"label": "x", "sections": ["chat"]})
    assert "chat" in str(caught.value)


def test_an_unknown_permission_is_refused_by_name():
    with pytest.raises(HeadlessBookingError) as caught:
        normalise_credential({"label": "x", "permissions": ["admin"]})
    assert "admin" in str(caught.value)


def test_an_empty_scope_is_allowed_because_the_research_does_not_forbid_it():
    """A read-nothing token is a legitimate thing to want from Generate Token."""
    spec = normalise_credential({"label": "x", "sections": []})
    assert spec["sections"] == list(SECTIONS)


def test_schedule_is_granted_only_for_the_sections_the_token_is_scoped_to():
    """ "the Schedule permission for the relevant section" - per section."""
    spec = {"sections": ["links"], "permissions": ["schedule", "read"], "enabled": True}
    assert require_scope(spec, "links", "schedule") == "links"
    with pytest.raises(PermissionDenied) as caught:
        require_scope(spec, "concierge", "schedule")
    assert caught.value.section == "concierge"


def test_a_token_without_the_schedule_permission_cannot_book():
    with pytest.raises(PermissionDenied) as caught:
        require_scope(
            {"sections": ["links"], "permissions": ["read"], "enabled": True}, "links", "schedule"
        )
    assert "Schedule to book" in str(caught.value)


def test_a_read_permission_is_what_gates_listing():
    with pytest.raises(PermissionDenied) as caught:
        require_scope(
            {"sections": ["links"], "permissions": ["schedule"], "enabled": True}, "links", "read"
        )
    assert caught.value.required == "read"


def test_a_disabled_credential_authorises_nothing():
    with pytest.raises(PermissionDenied) as caught:
        require_scope(
            {"sections": ["links"], "permissions": ["schedule"], "enabled": False},
            "links",
            "schedule",
        )
    assert "disabled" in str(caught.value)


def test_a_token_verifies_against_its_digest(engine, room):
    minted = engine.create_credential({"role": "admin", "label": "x"}, source=SOURCE)
    stored = store_credential(engine, minted["id"])
    assert verify_token(stored, minted["token"])["label"] == "x"


def test_a_wrong_token_does_not_verify(engine):
    minted = engine.create_credential({"role": "admin", "label": "x"}, source=SOURCE)
    stored = store_credential(engine, minted["id"])
    with pytest.raises(PermissionDenied):
        verify_token(stored, generate_token())


def test_a_revoked_credential_and_a_wrong_token_say_the_same_thing(engine):
    """Telling a caller a token was once valid is information a scanner wants."""
    minted = engine.create_credential({"role": "admin", "label": "x"}, source=SOURCE)
    engine.revoke_credential(minted["id"], source=SOURCE)
    with pytest.raises(PermissionDenied) as caught:
        verify_token(store_credential(engine, minted["id"], deleted=True), generate_token())
    assert "not valid" in str(caught.value)
    assert "revoked" in str(caught.value)


def test_an_empty_token_is_refused():
    with pytest.raises(PermissionDenied):
        verify_token({"data": {"token_hash": "abc"}}, "")


def test_a_missing_credential_is_a_404_not_a_403():
    with pytest.raises(NotFound):
        verify_token(None, "anything")


def test_the_created_credential_returns_its_token_exactly_once(engine, room):
    minted = engine.create_credential({"role": "admin", "label": "x"}, source=SOURCE)
    assert minted["token"] and minted["shown_once"] is True
    listed = engine.list_credentials()
    assert listed[0]["has_token"] is True
    assert "token" not in listed[0]
    assert "token" not in engine.get_credential(minted["id"])


def test_the_stored_credential_holds_a_digest_and_never_the_secret(engine):
    """Records are readable through the core API, so a cleartext token would be
    a credential sitting in a collection anybody can list."""
    minted = engine.create_credential({"role": "admin", "label": "x"}, source=SOURCE)
    stored = store_credential(engine, minted["id"])
    payload = str(stored["data"])
    assert minted["token"] not in payload
    assert token_digest(minted["token"]) in payload


def test_generating_a_token_writes_nothing_when_the_role_is_refused(engine, store, room):
    with pytest.raises(PermissionDenied):
        engine.create_credential({"role": "workspace_manager", "label": "x"}, source=SOURCE)
    assert store.list(CREDENTIAL_COLLECTION) == []


def store_credential(engine, credential_id, deleted=False):
    return engine.store.db.get(credential_id, include_deleted=deleted)


# --------------------------------------------------------------------------- #
# The session object: the four researched rules, in isolation
# --------------------------------------------------------------------------- #


def build_session(**overrides):
    paths = overrides.pop(
        "paths", [PathSlots(path_id=None, label="x", start_times=("2026-10-05T09:00:00Z",))]
    )
    body = {
        "route_id": "route_1",
        "section": "concierge",
        "asset_id": "asset_1",
        "room_id": "room_1",
        "paths": paths,
        "created_at": NOW,
        "expires_at": NOW + timedelta(minutes=15),
    } | overrides
    return Session(**body)


def test_a_fresh_session_is_open_and_not_expired():
    session = build_session()
    assert session.is_open(NOW)
    assert not session.is_expired(NOW)


def test_a_session_is_expired_at_its_ttl_boundary():
    """`>=` rather than `>`: a zero TTL would otherwise be alive for an instant."""
    session = build_session()
    assert session.is_expired(NOW + timedelta(minutes=15))


def test_a_session_is_not_expired_just_before_its_boundary():
    session = build_session()
    assert not session.is_expired(NOW + timedelta(minutes=15) - timedelta(seconds=1))


def test_consuming_a_session_makes_it_terminal():
    session = build_session()
    session.consume(NOW, "booked", "done")
    assert session.state == "booked"
    assert not session.is_open(NOW)


def test_a_session_cannot_be_consumed_twice():
    """The single-use rule, enforced in the one place that writes ``state``."""
    session = build_session()
    session.consume(NOW, "booked")
    with pytest.raises(HeadlessBookingError) as caught:
        session.consume(NOW, "booked")
    assert "single-use" in str(caught.value)


def test_a_failed_booking_also_consumes_the_session():
    """The rule's failure half. Consuming only on success lets a caller retry
    forever against a stale slot list, which is the failure the research
    forbids."""
    session = build_session()
    session.consume(NOW, "failed", "the slot was taken")
    assert session.state == "failed"
    with pytest.raises(HeadlessBookingError):
        session.consume(NOW, "booked")


def test_consuming_an_expired_session_records_the_expiry_reason():
    session = build_session()
    with pytest.raises(HeadlessBookingError) as caught:
        session.consume(NOW + timedelta(hours=1), "booked")
    assert session.state == "expired"
    assert getattr(caught.value, "reason", "") == "session_expired"


def test_a_terminal_session_points_back_at_the_discover_call():
    """The researched remedy is a procedure, and a caller has to be told it."""
    session = build_session()
    assert "book" in session.next_step()
    session.consume(NOW, "failed")
    assert "discover or route" in session.next_step()


def test_a_session_resolves_one_of_its_own_slots():
    session = build_session()
    canonical, instant, path, wire = session.resolve_slot("2026-10-05T09:00:00Z", None)
    assert canonical == "2026-10-05T09:00:00Z"
    assert instant == datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)
    assert wire == "2026-10-05T09:00:00Z"
    assert path.path_id is None


def test_an_offset_equivalent_of_an_offered_slot_is_accepted():
    """A caller that re-serialises Z as +00:00 has booked the instant it meant,
    and refusing it would teach callers to stop using the API."""
    canonical, _, _, wire = build_session().resolve_slot("2026-10-05T09:00:00+00:00", None)
    assert canonical == "2026-10-05T09:00:00Z"
    assert wire.endswith("+00:00")
    assert wire != canonical


def test_a_time_that_was_never_offered_is_refused_as_not_offered():
    session = build_session()
    with pytest.raises(Refusal) as caught:
        session.resolve_slot("2026-10-05T09:17:00Z", None)
    assert caught.value.reason == "start_time_not_offered"


def test_a_rounded_time_is_refused_rather_than_snapped():
    """A booking is a commitment to a specific time; 'close enough' is not one."""
    session = build_session()
    with pytest.raises(HeadlessBookingError) as caught:
        session.resolve_slot("2026-10-05T09:00:01Z", None)
    assert "verbatim" in str(caught.value)


def test_a_naive_start_time_is_refused_naming_the_utc_rule():
    with pytest.raises(HeadlessBookingError) as caught:
        parse_start_time("2026-10-05T09:00:00")
    assert "UTC" in str(caught.value)


def test_a_missing_start_time_is_refused():
    with pytest.raises(HeadlessBookingError) as caught:
        parse_start_time("")
    assert "verbatim" in str(caught.value)


def test_a_path_id_on_a_single_path_session_is_refused_by_name():
    """/concierge/.../schedule-simple has no {pathId} in it, so there is none
    to honour - and silently ignoring it would make a caller believe they
    filtered the slots when they did not."""
    session = build_session()
    with pytest.raises(Refusal) as caught:
        session.resolve_slot("2026-10-05T09:00:00Z", "emea")
    assert caught.value.reason == "path_not_offered"
    assert "{pathId}" in str(caught.value)


def test_a_handoff_session_resolves_the_named_path():
    session = build_session(
        section="handoff",
        paths=[
            PathSlots(path_id="emea", label="EMEA", start_times=("2026-10-05T09:00:00Z",)),
            PathSlots(path_id="amer", label="AMER", start_times=("2026-10-05T13:00:00Z",)),
        ],
    )
    canonical, _, path, _ = session.resolve_slot("2026-10-05T13:00:00Z", "amer")
    assert path.path_id == "amer"
    assert canonical == "2026-10-05T13:00:00Z"


def test_a_slot_from_the_wrong_path_is_refused():
    session = build_session(
        section="handoff",
        paths=[
            PathSlots(path_id="emea", label="EMEA", start_times=("2026-10-05T09:00:00Z",)),
            PathSlots(path_id="amer", label="AMER", start_times=("2026-10-05T13:00:00Z",)),
        ],
    )
    with pytest.raises(Refusal) as caught:
        session.resolve_slot("2026-10-05T09:00:00Z", "amer")
    assert caught.value.reason == "start_time_not_offered"


def test_an_unknown_path_is_refused_by_name():
    session = build_session(section="handoff", paths=[PathSlots("emea", "EMEA", ())])
    with pytest.raises(Refusal) as caught:
        session.resolve_slot("2026-10-05T09:00:00Z", "apac")
    assert caught.value.reason == "path_unknown"
    assert "emea" in str(caught.value)


def test_the_scheduling_data_shape_is_one_entry_per_path():
    session = build_session(
        section="handoff",
        paths=[
            PathSlots("emea", "EMEA", ("2026-10-05T09:00:00Z",)),
            PathSlots("amer", "AMER", ("2026-10-05T13:00:00Z",)),
        ],
    )
    served = session.scheduling_data()
    assert [entry["pathId"] for entry in served] == ["emea", "amer"]
    assert served[1]["startTimes"] == ["2026-10-05T13:00:00Z"]


def test_a_single_path_session_publishes_a_null_path_id():
    """So a client that reads pathId and sends it back gets the researched
    refusal rather than a silently-ignored field."""
    assert build_session().scheduling_data()[0]["pathId"] is None


def test_a_session_round_trips_through_its_stored_record(store, room):
    original = build_session()
    record = store.create(SESSION_COLLECTION, original.to_dict(), room_id=room["id"], source=SOURCE)
    rebuilt = Session.from_record(record)
    assert rebuilt.route_id == original.route_id
    assert rebuilt.all_start_times == original.all_start_times
    assert rebuilt.state == "open"


def test_a_session_reads_its_creation_time_from_the_envelope_not_the_payload():
    """The bug this catches: the store strips envelope keys from ``data`` on
    write, so a ``created_at`` written into the payload is silently dropped and
    every second call fails to parse it."""
    record = {
        "id": "route_1",
        "room_id": "room_1",
        "created_at": "2026-09-28T09:00:00.000+00:00",
        "data": {
            "routeId": "route_1",
            "section": "concierge",
            "schedulingData": [],
            "expires_at": "2026-09-28T09:15:00Z",
        },
    }
    session = Session.from_record(record)
    assert session.created_at == NOW
    assert session.expires_at == NOW + timedelta(minutes=15)


def test_require_open_session_names_the_remedy_for_a_spent_session():
    session = build_session()
    session.consume(NOW, "failed", reason="start_time_not_offered")
    with pytest.raises(Refusal) as caught:
        require_open_session(session, NOW)
    assert caught.value.reason == "session_consumed"
    assert "start_time_not_offered" in str(caught.value)
    assert "discover or route" in str(caught.value)


def test_require_open_session_names_the_remedy_for_an_expired_session():
    with pytest.raises(Refusal) as caught:
        require_open_session(build_session(), NOW + timedelta(hours=1))
    assert caught.value.reason == "session_expired"


# --------------------------------------------------------------------------- #
# The TTL: settable on Concierge, server-side elsewhere
# --------------------------------------------------------------------------- #


def test_each_surface_has_a_default_ttl():
    assert set(DEFAULT_TTL_MS) == set(SECTIONS)
    assert all(value > 0 for value in DEFAULT_TTL_MS.values())


def test_concierge_accepts_a_caller_settable_ttl():
    """ "timeoutInMS for Concierge, per-router-path" """
    resolved, source = resolve_timeout("concierge", 120_000, NOW)
    assert resolved == 120_000
    assert source == "caller"


def test_links_and_handoff_use_a_server_side_ttl():
    """ "server-side TTL for links/handoff" """
    for section in ("links", "handoff"):
        resolved, source = resolve_timeout(section, None, NOW)
        assert resolved == DEFAULT_TTL_MS[section]
        assert source == "server default"


def test_timeout_in_ms_on_a_server_side_surface_is_refused_not_ignored():
    """A silently-ignored setting is what a caller debugs for an afternoon."""
    with pytest.raises(HeadlessBookingError) as caught:
        resolve_timeout("links", 120_000, NOW)
    assert "server-side TTL for links/handoff" in str(caught.value)


def test_an_out_of_range_ttl_is_refused():
    for value in (MIN_TTL_MS - 1, MAX_TTL_MS + 1):
        with pytest.raises(HeadlessBookingError) as caught:
            resolve_timeout("concierge", value, NOW)
        assert "hold on availability" in str(caught.value)


def test_a_non_numeric_ttl_is_refused():
    with pytest.raises(HeadlessBookingError):
        resolve_timeout("concierge", "soon", NOW)


def test_resolve_timeout_names_the_section_it_could_not_resolve():
    with pytest.raises(HeadlessBookingError) as caught:
        resolve_timeout("chat", None, NOW)
    assert "chat" in str(caught.value)


# --------------------------------------------------------------------------- #
# Call #1: discover or route
# --------------------------------------------------------------------------- #


def test_discover_returns_a_route_id_and_start_times(engine, room):
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    assert session["routeId"].startswith("route_")
    assert session["routingId"] == session["routeId"]
    assert session["schedulingData"][0]["startTimes"]


def test_the_stored_records_id_is_the_route_id(engine, room, store):
    """Call #2 addresses the session by that id, so two ids kept in sync would
    leave a window where a caller holds a routeId that resolves to nothing."""
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    assert store.get(session["routeId"]) is not None
    assert store.get(session["routeId"])["collection"] == SESSION_COLLECTION


def test_the_scheduled_times_live_under_scheduling_data(engine, room):
    """ "returns a routeId and a list of startTimes under schedulingData" """
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    assert "schedulingData" in session
    assert "startTimes" in session["schedulingData"][0]


def test_a_discovered_session_is_open_and_says_how_to_use_itself(engine, room):
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    assert session["state"] == "open"
    assert session["retry_with_same_route_id"] is True
    assert "book" in session["next_step"]


def test_discover_returns_the_researched_custom_api_instructions(engine, room):
    """ "Copy for URL + starter body and a Share Instructions button" """
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    steps = session["instructions"]
    assert steps["step_1"]["method"] == "POST"
    assert steps["step_1"]["path"] == INIT_ENDPOINTS["concierge"].replace(
        "{routerSlug}", "northwind-enterprise"
    )
    assert steps["step_2"]["path"].startswith("/api/fire-edge/v1/org/concierge/routing/")
    assert steps["credentials_required"] == ["schedule", "read"]
    assert steps["mcp_tools"] == list(MCP_TOOLS["concierge"])


def test_the_instructions_carry_the_three_researched_warnings(engine, room):
    asset = concierge_asset(engine, room["id"])
    steps = discover(engine, room["id"], asset)["instructions"]
    joined = " ".join(steps["warnings"])
    assert "single-use" in joined
    assert "verbatim" in joined
    assert "For New Meeting" in joined


def test_the_handoff_instructions_fill_in_every_path_parameter(engine, room):
    asset = handoff_asset(engine, room["id"])
    steps = discover(engine, room["id"], asset)["instructions"]
    assert "{workspaceId}" not in steps["step_1"]["path"]
    assert "{userId}" not in steps["step_1"]["path"]
    assert "pathId" in steps["step_2"]["body"]


def test_discover_needs_an_asset_id(engine, room):
    with pytest.raises(Refusal) as caught:
        engine.discover(
            room["id"],
            {"section": "concierge", "interval": future_interval()},
            source=SOURCE,
        )
    assert caught.value.reason == "asset_unknown"


def test_discover_refuses_an_asset_from_another_section(engine, room):
    """/concierge/routers/{routerSlug}/rest addresses a router; a scheduling
    link is not one."""
    link = link_asset(engine, room["id"])
    with pytest.raises(HeadlessBookingError) as caught:
        engine.discover(
            room["id"],
            {"section": "concierge", "asset_id": link["id"], "interval": future_interval()},
            source=SOURCE,
        )
    assert "asset" in str(caught.value)


def test_discover_needs_an_interval(engine, room):
    asset = concierge_asset(engine, room["id"])
    with pytest.raises(HeadlessBookingError) as caught:
        engine.discover(
            room["id"],
            {
                "section": "concierge",
                "asset_id": asset["id"],
                "guest": {"guestEmail": "a@b.example"},
            },
            source=SOURCE,
        )
    assert "interval" in str(caught.value)


def test_discover_refuses_a_window_that_has_already_passed(engine, room):
    asset = concierge_asset(engine, room["id"])
    with pytest.raises(Refusal) as caught:
        engine.discover(
            room["id"],
            {
                "section": "concierge",
                "asset_id": asset["id"],
                "interval": {"startsAt": "2020-01-01T00:00:00Z", "duration": 60},
                "guest": {"guestEmail": "a@b.example"},
            },
            source=SOURCE,
        )
    assert caught.value.reason == "interval_starts_at_in_past"


def test_an_ownership_link_refuses_a_session_with_no_guest_email(engine, room):
    """ "For Ownership links, also pass guestEmail in the init call - it is
    required so Chili Piper can resolve the owner from your CRM." """
    asset = link_asset(engine, room["id"], link_type="ownership")
    with pytest.raises(Refusal) as caught:
        engine.discover(
            room["id"],
            {"section": "links", "asset_id": asset["id"], "interval": future_interval()},
            source=SOURCE,
        )
    assert caught.value.reason == "guest_email_required"
    assert "owner can be resolved from the CRM" in str(caught.value)


def test_an_ownership_link_accepts_a_session_with_a_guest_email(engine, room):
    asset = link_asset(engine, room["id"], link_type="ownership")
    session = discover(engine, room["id"], asset)
    assert session["guest_email"] == "buyer@example.com"


def test_a_personal_link_does_not_require_a_guest_email_at_discover(engine, room):
    asset = link_asset(engine, room["id"], link_type="personal")
    session = engine.discover(
        room["id"],
        {"section": "links", "asset_id": asset["id"], "interval": future_interval()},
        source=SOURCE,
    )
    assert session["slot_count"] > 0


def test_a_handoff_session_returns_one_slot_list_per_path(engine, room):
    """ "one or more routing paths, each with its own pathId and startTimes" """
    asset = handoff_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    assert [entry["pathId"] for entry in session["schedulingData"]] == ["emea", "amer"]
    assert all(entry["startTimes"] for entry in session["schedulingData"])


def test_two_paths_with_different_hours_offer_different_slots(engine, room):
    """Each path's availability is its own host's calendar."""
    asset = handoff_asset(
        engine,
        room["id"],
        paths=[
            {
                "path_id": "morning",
                "host_email": "a@example.com",
                "work_start_hour": 9,
                "work_end_hour": 11,
            },
            {
                "path_id": "afternoon",
                "host_email": "b@example.com",
                "work_start_hour": 14,
                "work_end_hour": 16,
            },
        ],
    )
    session = discover(engine, room["id"], asset)
    morning = session["schedulingData"][0]["startTimes"]
    afternoon = session["schedulingData"][1]["startTimes"]
    assert morning and afternoon
    assert not set(morning) & set(afternoon)


def test_a_session_with_no_availability_is_refused_and_recorded(engine, room, store):
    """No slots means there is nothing to book, and a rep needs to know why.

    The host is made unavailable with a calendar block rather than by zero
    working hours, because `work_start_hour == work_end_hour` is a
    *configuration* error and this is a *scheduling* outcome - two different
    states a rep has to tell apart.
    """
    asset = concierge_asset(engine, room["id"])
    engine.add_calendar_block(
        room["id"],
        {
            "host_email": "dana@example.com",
            "startsAt": "2020-01-01T00:00:00Z",
            "endsAt": "2040-01-01T00:00:00Z",
        },
        source=SOURCE,
    )
    with pytest.raises(Refusal) as caught:
        discover(engine, room["id"], asset)
    assert caught.value.reason == "no_availability"
    assert store.list(SESSION_COLLECTION) == []
    outcomes = {row["data"]["outcome"] for row in store.list(CALL_COLLECTION)}
    assert "no_availability" in outcomes


def test_a_calendar_block_reaches_the_slot_list(engine, room):
    """The bug this catches: a block filed under a key the asset side never asks
    for, so the calendar was read and ignored."""
    asset = concierge_asset(engine, room["id"])
    assert discover(engine, room["id"], asset)["slot_count"] > 0
    engine.add_calendar_block(
        room["id"],
        {
            "host_email": "dana@example.com",
            "startsAt": "2020-01-01T00:00:00Z",
            "endsAt": "2040-01-01T00:00:00Z",
        },
        source=SOURCE,
    )
    with pytest.raises(Refusal) as caught:
        discover(engine, room["id"], asset)
    assert caught.value.reason == "no_availability"


def test_a_calendar_block_for_another_host_does_not_remove_slots(engine, room):
    asset = concierge_asset(engine, room["id"])
    baseline = discover(engine, room["id"], asset)["slot_count"]
    engine.add_calendar_block(
        room["id"],
        {
            "host_email": "someone.else@example.com",
            "startsAt": "2026-01-01T00:00:00Z",
            "endsAt": "2030-01-01T00:00:00Z",
        },
        source=SOURCE,
    )
    assert discover(engine, room["id"], asset)["slot_count"] == baseline


def test_a_calendar_block_needs_a_host(engine, room):
    with pytest.raises(HeadlessBookingError) as caught:
        engine.add_calendar_block(
            room["id"], {"startsAt": "2026-10-05T10:00:00Z", "duration": 30}, source=SOURCE
        )
    assert "host_email is required" in str(caught.value)


def test_a_session_records_how_many_busy_blocks_it_considered(engine, room):
    asset = concierge_asset(engine, room["id"])
    engine.add_calendar_block(
        room["id"],
        {
            "host_email": "dana@example.com",
            "startsAt": "2026-10-06T13:00:00Z",
            "endsAt": "2026-10-06T14:00:00Z",
        },
        source=SOURCE,
    )
    session = discover(engine, room["id"], asset)
    assert session["busy_considered"] == 1


def test_a_naive_clock_is_refused():
    """A naive `now` has no instant, and an availability engine needs one."""
    store_like = RecordStore(AuditedDatabase(":memory:"))
    engine = HeadlessBooking(store_like, clock=lambda: datetime(2026, 1, 1, 9, 0))
    with pytest.raises(HeadlessBookingError) as caught:
        engine.now()
    assert "aware datetime" in str(caught.value)


# --------------------------------------------------------------------------- #
# Call #2: book
# --------------------------------------------------------------------------- #


def test_booking_returns_a_meeting_id(engine, room):
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    booked = book_first(engine, room["id"], session)
    assert booked["meetingId"].startswith("mtg_")


def test_booking_writes_the_meeting_with_the_slot_it_was_given(engine, room, store):
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    slot = first_slot(session)
    booked = book_first(engine, room["id"], session)
    record = store.get(booked["meetingId"])
    assert record["data"]["startTime"] == slot
    assert record["data"]["start_time_verbatim"] == slot
    assert record["data"]["start_time_was_verbatim"] is True


def test_booking_records_a_reserialised_start_time_as_not_verbatim(engine, room, store):
    """The record says whether it was byte-identical, so the rule is checkable."""
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    slot = first_slot(session)
    reserialised = slot.replace("Z", "+00:00")
    booked = book_first(engine, room["id"], session, startTime=reserialised)
    data = store.get(booked["meetingId"])["data"]
    assert data["startTime"] == slot
    assert data["start_time_verbatim"] == reserialised
    assert data["start_time_was_verbatim"] is False


def test_a_meeting_carries_the_session_it_came_from(engine, room):
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    booked = book_first(engine, room["id"], session)
    assert booked["routeId"] == session["routeId"]
    assert booked["meeting"]["data"]["routeId"] == session["routeId"]


def test_a_meeting_records_its_provider_and_a_derived_link(engine, room):
    asset = concierge_asset(engine, room["id"], provider="gmeet")
    session = discover(engine, room["id"], asset)
    booked = book_first(engine, room["id"], session)
    data = booked["meeting"]["data"]
    assert data["provider"] == "gmeet"
    assert data["meeting_link_is_derived"] is True
    assert booked["meetingId"] in data["meeting_link"]


def test_booking_writes_the_invites_in_the_same_transaction(engine, room, store):
    """ "Calendar invites are sent immediately" - the *commit* is implemented."""
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    booked = book_first(engine, room["id"], session)
    invites = store.find(INVITE_COLLECTION, {"meeting_id": booked["meetingId"]}, limit=10)
    assert {row["data"]["role"] for row in invites} == {"guest", "host"}


def test_an_invite_is_recorded_and_not_claimed_as_sent(engine, room, store):
    """A record claiming a send that did not happen is a lie in the audit log,
    which is the one artefact this product promises is truthful."""
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    booked = book_first(engine, room["id"], session)
    assert booked["invites_sent"] is False
    for row in store.find(INVITE_COLLECTION, {"meeting_id": booked["meetingId"]}, limit=10):
        assert row["data"]["sent"] is False
        assert row["data"]["delivery"] == "recorded"
    assert store.get(booked["meetingId"])["data"]["invites_sent"] is False


def test_booking_emits_the_for_new_meeting_created_event(engine, room, store):
    """ "Bookings immediately emit the For New Meeting webhook." """
    asset = concierge_asset(engine, room["id"], webhook_url="https://hooks.example.com/x")
    session = discover(engine, room["id"], asset)
    booked = book_first(engine, room["id"], session)
    events = store.find(WEBHOOK_COLLECTION, {"meeting_id": booked["meetingId"]}, limit=10)
    assert len(events) == 1
    assert events[0]["data"]["webhook"] == WEBHOOK_NAME
    assert events[0]["data"]["event"] == WEBHOOK_EVENT
    assert events[0]["data"]["endpoint"] == "https://hooks.example.com/x"


def test_a_webhook_event_is_recorded_but_not_delivered(engine, room, store):
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    booked = book_first(engine, room["id"], session)
    event = store.find(WEBHOOK_COLLECTION, {"meeting_id": booked["meetingId"]}, limit=1)[0]
    assert event["data"]["delivered"] is False
    assert event["data"]["delivery"] == "recorded"


def test_the_webhook_is_emitted_even_with_no_endpoint_configured(engine, room, store):
    """The emission is unconditional in the research; the delivery is the part
    this product cannot perform, and the two are kept apart."""
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    booked = book_first(engine, room["id"], session)
    event = store.find(WEBHOOK_COLLECTION, {"meeting_id": booked["meetingId"]}, limit=1)[0]
    assert event["data"]["endpoint"] is None
    assert event["data"]["event"] == WEBHOOK_EVENT


def test_booking_moves_the_session_to_booked(engine, room, store):
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    book_first(engine, room["id"], session)
    assert store.get(session["routeId"])["data"]["state"] == "booked"


def test_a_booking_needs_a_guest_email(engine, room):
    """There is nobody to send the immediate calendar invite to."""
    asset = concierge_asset(engine, room["id"])
    session = engine.discover(
        room["id"],
        {
            "section": "concierge",
            "asset_id": asset["id"],
            "interval": future_interval(),
        },
        source=SOURCE,
    )
    with pytest.raises(Refusal) as caught:
        engine.book(
            room["id"],
            session["routeId"],
            {"startTime": session["schedulingData"][0]["startTimes"][0]},
            source=BOOK_SOURCE,
        )
    assert caught.value.reason == "guest_email_required"


def test_booking_an_unknown_session_is_a_404_naming_the_lesson(engine, room):
    with pytest.raises(NotFound) as caught:
        engine.book(
            room["id"], "route_nope", {"startTime": "2026-10-05T09:00:00Z"}, source=BOOK_SOURCE
        )
    assert "single-use" in str(caught.value)


def test_booking_a_session_from_another_room_is_a_404(engine, room, other_room):
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    with pytest.raises(NotFound) as caught:
        book_first(engine, other_room["id"], session)
    assert "belongs to room" in str(caught.value)


# --------------------------------------------------------------------------- #
# The single-use rule, end to end
# --------------------------------------------------------------------------- #


def test_a_session_books_exactly_once(engine, room, store):
    """ "Sessions are single-use." """
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    book_first(engine, room["id"], session)
    with pytest.raises(Refusal) as caught:
        book_first(engine, room["id"], session)
    assert caught.value.reason == "session_consumed"
    assert len(store.list(MEETING_COLLECTION)) == 1


def test_a_refusal_reason_is_always_one_of_the_published_vocabulary(engine, room):
    """A client branches on `reason`, so a reason outside the vocabulary is a bug.

    Every refusal this workflow can produce is collected here and checked against
    `SCHEDULE_FAILURES` and `INIT_FAILURES`, which is what stops a reworded
    message from silently renaming a recorded outcome.
    """
    asset = concierge_asset(engine, room["id"])
    published = set(SCHEDULE_FAILURES) | set(INIT_FAILURES)
    seen = []

    def note(call, *args, **kwargs):
        with pytest.raises(Refusal) as caught:
            call(*args, **kwargs)
        seen.append(caught.value.reason)

    session = discover(engine, room["id"], asset)
    note(book_first, engine, room["id"], session, startTime="2026-01-01T09:00:00Z")
    note(book_first, engine, room["id"], session)  # a spent session
    fresh = discover(engine, room["id"], asset)
    note(book_first, engine, room["id"], fresh, pathId="emea")
    note(book_first, engine, room["id"], fresh, startTime="2026-10-05T09:00:00")  # naive

    assert seen
    for reason in seen:
        assert reason in published, f"{reason!r} is not in the published vocabulary"


def test_a_permission_refusal_is_not_one_of_the_researched_reasons(engine, room):
    """`require_credential` is a 403, deliberately outside the 400 vocabulary.

    The researched reasons describe *what* a schedule call could not do; a
    credential that may not act at all is a different HTTP answer with a
    different remedy, so it is not given a researched reason code.
    """
    asset = concierge_asset(engine, room["id"])
    with pytest.raises(PermissionDenied) as caught:
        discover(engine, room["id"], asset, require_credential=True)
    assert not getattr(caught.value, "reason", "")


def test_a_wrong_room_is_a_404_and_not_a_published_reason(engine, room, other_room):
    """The one refusal that is an id problem rather than a researched failure."""
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    with pytest.raises(NotFound) as caught:
        book_first(engine, other_room["id"], session)
    assert caught.value.resource == "session"


def test_an_expired_session_refusal_carries_its_own_reason(engine, room, store):
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    engine._clock = lambda: NOW + timedelta(hours=1)
    with pytest.raises(Refusal) as caught:
        book_first(engine, room["id"], session)
    assert caught.value.reason == "session_expired"
    assert store.get(session["routeId"])["data"]["state"] == "expired"
    assert store.list(MEETING_COLLECTION) == []


def test_a_second_call_on_a_spent_session_writes_no_second_meeting(engine, room, store):
    """The researched retry, refused - and the refusal leaves nothing behind."""
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    book_first(engine, room["id"], session)
    with pytest.raises(HeadlessBookingError):
        book_first(engine, room["id"], session)
    assert len(store.list(MEETING_COLLECTION)) == 1


def test_the_refusal_names_the_fresh_discover_as_the_remedy(engine, room):
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    book_first(engine, room["id"], session)
    with pytest.raises(HeadlessBookingError) as caught:
        book_first(engine, room["id"], session)
    assert "discover or route" in str(caught.value)


def test_the_retry_is_visible_in_the_call_log(engine, room):
    """The researched instruction becomes evidence rather than prose.

    The second attempt records `session_consumed` rather than the session's own
    outcome, because recording "booked" for a call that booked nothing is
    exactly how the mistake stays invisible in the log that exists to show it.
    """
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    book_first(engine, room["id"], session)
    with pytest.raises(HeadlessBookingError):
        book_first(engine, room["id"], session)
    rows = engine.calls(room_id=room["id"])
    assert len(rows) == 3  # opened, booked, refused
    assert [row["data"]["outcome"] for row in rows] == ["session_consumed", "booked", "opened"]
    assert all(row["data"]["route_id"] == session["routeId"] for row in rows)
    assert "fresh discover" in rows[0]["data"]["detail"]


def test_the_summary_counts_a_retry_as_a_retry(engine, room):
    """The count a rep reads, kept apart from the ordinary call totals."""
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    book_first(engine, room["id"], session)
    with pytest.raises(HeadlessBookingError):
        book_first(engine, room["id"], session)
    summary = engine.summary(room_id=room["id"])
    assert summary["retries_on_a_spent_session"] == 1
    assert summary["calls_by_outcome"]["session_consumed"] == 1
    assert summary["calls"] == 3


def test_a_failed_booking_spends_the_session(engine, room, store):
    """ "On a schedule failure, do not retry the schedule call with the same
    routeId - start again from the discover or route step." """
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    with pytest.raises(HeadlessBookingError):
        book_first(engine, room["id"], session, startTime="2026-01-01T09:00:00Z")
    assert store.get(session["routeId"])["data"]["state"] == "failed"
    assert store.list(MEETING_COLLECTION) == []


def test_a_failed_session_cannot_be_booked_afterwards(engine, room, store):
    """The bug this suite exists for: consuming a session only on success lets a
    caller retry forever against a stale slot list."""
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    with pytest.raises(HeadlessBookingError):
        book_first(engine, room["id"], session, startTime="2026-01-01T09:00:00Z")
    with pytest.raises(HeadlessBookingError) as caught:
        book_first(engine, room["id"], session)
    assert "single-use" in str(caught.value)
    assert store.list(MEETING_COLLECTION) == []


def test_a_refusal_before_the_scheduler_does_not_spend_the_session(engine, room, store):
    """A permission failure never reached the schedule call, so there is no
    result to invalidate - the one case the rule does not cover."""
    asset = concierge_asset(engine, room["id"])
    read_only = engine.create_credential(
        {"role": "admin", "label": "ro", "permissions": ["read"]}, source=SOURCE
    )
    session = discover(engine, room["id"], asset)
    with pytest.raises(PermissionDenied):
        book_first(engine, room["id"], session, credential_id=read_only["id"])
    assert store.get(session["routeId"])["data"]["state"] == "open"


def test_a_refused_booking_still_writes_its_call_log_row(engine, room):
    """ "Nothing happened, and here is why" is what a caller needs to read."""
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    with pytest.raises(HeadlessBookingError):
        book_first(engine, room["id"], session, startTime="2026-01-01T09:00:00Z")
    outcomes = [row["data"]["outcome"] for row in engine.calls(room_id=room["id"])]
    assert "start_time_not_offered" in outcomes


def test_a_refused_booking_writes_no_invites_and_no_webhook(engine, room, store):
    """A refusal that left a meeting behind would be counted as a write."""
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    with pytest.raises(HeadlessBookingError):
        book_first(engine, room["id"], session, startTime="2026-01-01T09:00:00Z")
    assert store.list(MEETING_COLLECTION) == []
    assert store.list(INVITE_COLLECTION) == []
    assert store.list(WEBHOOK_COLLECTION) == []


# --------------------------------------------------------------------------- #
# Expiry
# --------------------------------------------------------------------------- #


def test_an_expired_session_cannot_be_booked(engine, room, store, clock):
    """ "If step 2's session expired ... the caller re-runs step 1" """
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    clock_now = NOW + timedelta(hours=1)
    engine._clock = lambda: clock_now
    with pytest.raises(HeadlessBookingError) as caught:
        book_first(engine, room["id"], session)
    assert "expired" in str(caught.value)
    assert store.list(MEETING_COLLECTION) == []


def test_a_caller_settable_ttl_shortens_the_session(engine, room):
    """`timeoutInMS` is the caller's to set on Concierge, and it is honoured."""
    asset = concierge_asset(engine, room["id"])
    long_lived = discover(engine, room["id"], asset)
    short = engine.discover(
        room["id"],
        {
            "section": "concierge",
            "asset_id": asset["id"],
            "interval": future_interval(),
            "guest": {"guestEmail": "a@b.example"},
            "timeout_in_ms": 30_000,
        },
        source=SOURCE,
    )
    assert short["timeout_in_ms"] == 30_000
    assert long_lived["timeout_in_ms"] == DEFAULT_TTL_MS["concierge"]

    # One second past the short TTL, well inside the default one.
    engine._clock = lambda: NOW + timedelta(milliseconds=30_001)
    assert engine.get_session(short["routeId"])["data"]["state"] == "expired"
    assert engine.get_session(long_lived["routeId"])["data"]["state"] == "open"


def test_reading_an_expired_session_projects_the_state_without_writing(engine, room, store):
    """A read must never spend a session; it only reports what a book would do."""
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    engine._clock = lambda: NOW + timedelta(hours=1)
    read = engine.get_session(session["routeId"])
    assert read["data"]["state"] == "expired"
    assert read["data"]["state_is_projected"] is True
    assert store.get(session["routeId"])["data"]["state"] == "open"
    assert read["data"]["retry_with_same_route_id"] is False


def test_reading_an_open_session_says_a_retry_is_fine(engine, room):
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    read = engine.get_session(session["routeId"])
    assert read["data"]["retry_with_same_route_id"] is True
    assert "book" in read["data"]["next_step"]


# --------------------------------------------------------------------------- #
# The slot-taken case
# --------------------------------------------------------------------------- #


def test_booking_a_slot_taken_since_discover_is_refused(engine, room, other_room, store):
    """ "If step 2's session expired or the slot was taken, the caller re-runs
    step 1 with a fresh session." """
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    slot = first_slot(session)

    # Another room books the same host and slot through a separate session.
    rival_asset = concierge_asset(engine, other_room["id"])
    rival_session = discover(engine, other_room["id"], rival_asset)
    engine.book(
        other_room["id"],
        rival_session["routeId"],
        {"startTime": slot, "guest": {"guestEmail": "rival@example.com"}},
        source=BOOK_SOURCE,
    )
    assert len(store.list(MEETING_COLLECTION)) == 1

    with pytest.raises(Refusal) as caught:
        book_first(engine, room["id"], session, startTime=slot)
    assert caught.value.reason == "slot_taken"
    assert len(store.list(MEETING_COLLECTION)) == 1


def test_a_taken_slot_does_not_substitute_another_one(engine, room, other_room, store):
    """Booking a *different* time than the caller asked for is the worst of the
    available behaviours: a meetingId and an invite for a time nobody chose."""
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    slot = first_slot(session)

    rival_asset = concierge_asset(engine, other_room["id"])
    rival_session = discover(engine, other_room["id"], rival_asset)
    engine.book(
        other_room["id"],
        rival_session["routeId"],
        {"startTime": slot, "guest": {"guestEmail": "rival@example.com"}},
        source=BOOK_SOURCE,
    )
    with pytest.raises(HeadlessBookingError):
        book_first(engine, room["id"], session, startTime=slot)
    assert len(store.list(MEETING_COLLECTION)) == 1


def test_a_taken_slot_names_the_researched_remedy(engine, room, other_room):
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    slot = first_slot(session)
    rival_asset = concierge_asset(engine, other_room["id"])
    rival_session = discover(engine, other_room["id"], rival_asset)
    engine.book(
        other_room["id"],
        rival_session["routeId"],
        {"startTime": slot, "guest": {"guestEmail": "rival@example.com"}},
        source=BOOK_SOURCE,
    )
    with pytest.raises(HeadlessBookingError) as caught:
        book_first(engine, room["id"], session, startTime=slot)
    assert "fresh session" in str(caught.value)


def test_a_booked_meeting_is_not_offered_again_by_a_later_session(engine, room, store):
    """A meeting this product committed is a block, so the host is not free."""
    asset = concierge_asset(engine, room["id"])
    first = discover(engine, room["id"], asset)
    slot = first_slot(first)
    book_first(engine, room["id"], first, startTime=slot)

    second = discover(engine, room["id"], asset)
    assert slot not in second["schedulingData"][0]["startTimes"]


def test_a_taken_slot_distinguishes_the_calendar_from_our_own_bookings(engine, room, store):
    """A busy block is the host's calendar; a booked meeting is something this
    product did. The reader of the refusal needs to know which."""
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    slot = first_slot(session)
    engine.add_calendar_block(
        room["id"],
        {"host_email": "dana@example.com", "startTime": slot, "duration": 30},
        source=SOURCE,
    )
    with pytest.raises(Refusal) as caught:
        book_first(engine, room["id"], session, startTime=slot)
    assert "calendar" in str(caught.value)


def test_an_unreadable_meeting_timestamp_is_skipped_not_treated_as_a_conflict(engine, room, store):
    """The bug this catches: coercing an unreadable timestamp to the epoch, which
    overlaps nothing, so a corrupt row stopped being a conflict."""
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    slot = first_slot(session)
    store.create(
        MEETING_COLLECTION,
        {
            "meetingId": "mtg_broken",
            "host_email": "dana@example.com",
            "startTime": "not a timestamp",
            "endsAt": "also not a timestamp",
            "routeId": "route_other",
        },
        room_id=room["id"],
        source=SOURCE,
    )
    booked = book_first(engine, room["id"], session, startTime=slot)
    assert booked["meetingId"].startswith("mtg_")
    assert booked["meetingId"] != "mtg_broken"


# --------------------------------------------------------------------------- #
# Handoff, the one surface with paths
# --------------------------------------------------------------------------- #


def test_a_handoff_booking_names_its_path(engine, room, store):
    asset = handoff_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    entry = next(e for e in session["schedulingData"] if e["pathId"] == "amer")
    booked = engine.book(
        room["id"],
        session["routeId"],
        {
            "startTime": entry["startTimes"][0],
            "pathId": "amer",
            "guest": {"guestEmail": "lead@example.com"},
        },
        source=BOOK_SOURCE,
    )
    assert store.get(booked["meetingId"])["data"]["pathId"] == "amer"


def test_a_handoff_meeting_records_its_own_host(engine, room, store):
    asset = handoff_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    entry = next(e for e in session["schedulingData"] if e["pathId"] == "emea")
    booked = engine.book(
        room["id"],
        session["routeId"],
        {
            "startTime": entry["startTimes"][0],
            "pathId": "emea",
            "guest": {"guestEmail": "l@example.com"},
        },
        source=BOOK_SOURCE,
    )
    assert store.get(booked["meetingId"])["data"]["host_email"] == "aisha@example.com"


def test_a_handoff_booking_without_a_path_is_refused(engine, room):
    asset = handoff_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    with pytest.raises(Refusal) as caught:
        book_first(engine, room["id"], session)
    assert caught.value.reason == "path_unknown"


def test_a_handoff_path_with_no_slots_is_still_listed(engine, room):
    """The init response returns the paths; an empty one is information, not a
    reason to hide the path.

    The path is emptied with a calendar block for *that path's* host, which is
    the same mechanism that empties a single-path surface's slots.
    """
    asset = handoff_asset(
        engine,
        room["id"],
        paths=[
            {"path_id": "free", "host_email": "a@example.com"},
            {"path_id": "blocked", "host_email": "b@example.com"},
        ],
    )
    engine.add_calendar_block(
        room["id"],
        {
            "host_email": "b@example.com",
            "path_id": "blocked",
            "startsAt": "2020-01-01T00:00:00Z",
            "endsAt": "2040-01-01T00:00:00Z",
        },
        source=SOURCE,
    )
    session = discover(engine, room["id"], asset)
    by_path = {entry["pathId"]: entry["startTimes"] for entry in session["schedulingData"]}
    assert by_path["free"]
    assert by_path["blocked"] == []


# --------------------------------------------------------------------------- #
# The CRM writeback
# --------------------------------------------------------------------------- #


def test_a_crm_writeback_is_off_by_default(engine, room):
    """ "optional CRM writeback" - optional, and this build records rather than
    calls."""
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    booked = book_first(engine, room["id"], session)
    assert booked["meeting"]["data"]["crm_writeback"]["written"] is False


def test_a_configured_crm_writeback_is_recorded_not_performed(engine, room):
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    booked = book_first(
        engine, room["id"], session, crm_writeback={"object": "Event", "fields": ["startTime"]}
    )
    writeback = booked["meeting"]["data"]["crm_writeback"]
    assert writeback["written"] is True
    assert writeback["mode"] == "recorded"
    assert writeback["object"] == "Event"


# --------------------------------------------------------------------------- #
# Authorisation
# --------------------------------------------------------------------------- #


def test_a_call_with_no_credential_is_made_as_the_installation(engine, room):
    """A stated third option rather than an implied one, so a reader is never
    misled about how a call was authorised."""
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    assert session["authorised_as"] == "installation"
    assert session["credential_id"] is None


def test_a_call_with_a_credential_records_how_it_authorised(engine, room):
    token = engine.create_credential({"role": "admin", "label": "x"}, source=SOURCE)
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset, credential_id=token["id"])
    assert session["authorised_as"] == "credential_id"
    assert session["credential_id"] == token["id"]


def test_a_call_with_a_token_records_how_it_authorised(engine, room):
    token = engine.create_credential({"role": "admin", "label": "x"}, source=SOURCE)
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset, token=token["token"])
    assert session["authorised_as"] == "token"


def test_a_token_may_book_when_it_holds_schedule(engine, room):
    token = engine.create_credential({"role": "admin", "label": "x"}, source=SOURCE)
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset, credential_id=token["id"])
    booked = book_first(engine, room["id"], session, credential_id=token["id"])
    assert booked["meetingId"]


def test_a_schedule_only_token_cannot_discover(engine, room):
    """Read and Schedule are separate permissions, and listing needs Read."""
    token = engine.create_credential(
        {"role": "admin", "label": "x", "permissions": ["schedule"]}, source=SOURCE
    )
    asset = concierge_asset(engine, room["id"])
    with pytest.raises(PermissionDenied) as caught:
        discover(engine, room["id"], asset, credential_id=token["id"])
    assert caught.value.required == "read"


def test_a_read_only_token_cannot_book(engine, room, store):
    token = engine.create_credential(
        {"role": "admin", "label": "x", "permissions": ["read"]}, source=SOURCE
    )
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset, credential_id=token["id"])
    with pytest.raises(PermissionDenied) as caught:
        book_first(engine, room["id"], session, credential_id=token["id"])
    assert caught.value.required == "schedule"
    assert store.list(MEETING_COLLECTION) == []


def test_a_token_scoped_to_another_section_cannot_use_this_one(engine, room):
    """The scope is per section, which is what makes it worth having at all."""
    token = engine.create_credential(
        {"role": "admin", "label": "x", "sections": ["links"]}, source=SOURCE
    )
    asset = concierge_asset(engine, room["id"])
    with pytest.raises(PermissionDenied) as caught:
        discover(engine, room["id"], asset, credential_id=token["id"])
    assert caught.value.section == "concierge"


def test_require_credential_makes_the_installation_unable_to_act(engine, room):
    """One branch, and the summary says which: a deployment that wants
    always-on scoping turns this on."""
    asset = concierge_asset(engine, room["id"])
    with pytest.raises(PermissionDenied):
        discover(engine, room["id"], asset, require_credential=True)


def test_a_revoked_credential_stops_authorising(engine, room):
    token = engine.create_credential({"role": "admin", "label": "x"}, source=SOURCE)
    engine.revoke_credential(token["id"], source=SOURCE)
    asset = concierge_asset(engine, room["id"])
    with pytest.raises(NotFound):
        discover(engine, room["id"], asset, credential_id=token["id"])


def test_a_token_sent_with_a_mismatched_credential_id_is_refused(engine, room):
    """A token is resolved first, and a token matching nothing is refused.

    A caller that sends a token and an id and expects the id to win has a bug,
    and honouring the weaker one silently is how a permission check gets
    bypassed - so a token that does not verify stops the call before the id is
    ever consulted. The refusal is identical to a plain wrong token, because
    confirming which credentials exist is information a scanner wants.
    """
    first = engine.create_credential({"role": "admin", "label": "a"}, source=SOURCE)
    second = engine.create_credential({"role": "admin", "label": "b"}, source=SOURCE)
    asset = concierge_asset(engine, room["id"])
    with pytest.raises(PermissionDenied):
        discover(
            engine,
            room["id"],
            asset,
            token=first["token"],
            credential_id=second["id"],
        )


def test_a_matching_token_and_credential_id_are_both_accepted(engine, room):
    """The same credential presented both ways is not a mismatch."""
    token = engine.create_credential({"role": "admin", "label": "x"}, source=SOURCE)
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset, token=token["token"], credential_id=token["id"])
    assert session["authorised_as"] == "token"


def test_an_unknown_credential_id_is_a_404(engine, room):
    asset = concierge_asset(engine, room["id"])
    with pytest.raises(NotFound) as caught:
        discover(engine, room["id"], asset, credential_id="cred_nope")
    assert caught.value.resource == "credential"


def test_a_wrong_token_is_refused(engine, room):
    asset = concierge_asset(engine, room["id"])
    with pytest.raises(PermissionDenied):
        discover(engine, room["id"], asset, token=generate_token())


# --------------------------------------------------------------------------- #
# Reads and the summary
# --------------------------------------------------------------------------- #


def test_sessions_filter_by_section_and_state(engine, room):
    asset = concierge_asset(engine, room["id"])
    first = discover(engine, room["id"], asset)
    book_first(engine, room["id"], first)
    discover(engine, room["id"], asset)
    assert len(engine.list_sessions(room_id=room["id"])) == 2
    assert len(engine.list_sessions(room_id=room["id"], state="booked")) == 1
    assert len(engine.list_sessions(room_id=room["id"], state="open")) == 1
    assert len(engine.list_sessions(room_id=room["id"], section="links")) == 0


def test_sessions_are_room_scoped(engine, room, other_room):
    asset = concierge_asset(engine, room["id"])
    discover(engine, room["id"], asset)
    assert len(engine.list_sessions(room_id=room["id"])) == 1
    assert len(engine.list_sessions(room_id=other_room["id"])) == 0


def test_assets_filter_by_section_link_type_and_enabled(engine, room):
    concierge_asset(engine, room["id"])
    link_asset(engine, room["id"], link_type="ownership")
    concierge_asset(engine, room["id"], enabled=False)
    assert len(engine.list_assets(room_id=room["id"])) == 3
    assert len(engine.list_assets(room_id=room["id"], section="links")) == 1
    assert len(engine.list_assets(room_id=room["id"], enabled=False)) == 1
    assert len(engine.list_assets(room_id=room["id"], link_type="ownership")) == 1


def test_meetings_filter_by_section_and_host(engine, room):
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    book_first(engine, room["id"], session)
    assert len(engine.list_meetings(room_id=room["id"])) == 1
    assert len(engine.list_meetings(room_id=room["id"], section="links")) == 0
    assert len(engine.list_meetings(room_id=room["id"], host_email="dana@example.com")) == 1
    assert len(engine.list_meetings(room_id=room["id"], host_email="other@example.com")) == 0


def test_reading_a_meeting_returns_its_invites_and_webhook(engine, room):
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    booked = book_first(engine, room["id"], session)
    meeting = engine.get_meeting(booked["meetingId"])
    assert len(meeting["invites"]) == 2
    assert len(meeting["webhook"]) == 1


def test_reading_an_unknown_meeting_is_a_404(engine):
    with pytest.raises(NotFound) as caught:
        engine.get_meeting("mtg_nope")
    assert caught.value.resource == "meeting"


def test_the_summary_counts_states_meetings_and_retries(engine, room):
    asset = concierge_asset(engine, room["id"])
    first = discover(engine, room["id"], asset)
    book_first(engine, room["id"], first)
    with pytest.raises(HeadlessBookingError):
        book_first(engine, room["id"], first)
    second = discover(engine, room["id"], asset)
    with pytest.raises(HeadlessBookingError):
        book_first(engine, room["id"], second, startTime="2026-01-01T09:00:00Z")

    summary = engine.summary(room_id=room["id"])
    assert summary["sessions"] == 2
    assert summary["sessions_by_state"]["booked"] == 1
    assert summary["sessions_by_state"]["failed"] == 1
    assert summary["meetings"] == 1
    assert summary["retries_on_a_spent_session"] == 1


def test_the_summary_keeps_sent_and_recorded_apart(engine, room):
    """Collapsing them would invite a reader to believe something left the
    process."""
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    book_first(engine, room["id"], session)
    summary = engine.summary(room_id=room["id"])
    assert summary["invites_sent"] == 0
    assert summary["invites_recorded"] == 2
    assert summary["webhooks_delivered"] == 0
    assert summary["webhooks_recorded"] == 1


def test_the_summary_is_room_scoped(engine, room, other_room):
    asset = concierge_asset(engine, room["id"])
    discover(engine, room["id"], asset)
    assert engine.summary(room_id=room["id"])["sessions"] == 1
    assert engine.summary(room_id=other_room["id"])["sessions"] == 0


def test_calls_filter_by_outcome_and_route(engine, room):
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    book_first(engine, room["id"], session)
    assert len(engine.calls(room_id=room["id"], outcome="opened")) == 1
    assert len(engine.calls(room_id=room["id"], route_id=session["routeId"])) == 2
    assert engine.calls(room_id=room["id"], outcome="nope") == []


def test_a_call_row_names_the_mcp_tool_it_mirrors(engine, room):
    """ "MCP tools mirror these" - so the log names the tool, not just a verb."""
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    book_first(engine, room["id"], session)
    tools = {row["data"]["tool"] for row in engine.calls(room_id=room["id"])}
    assert tools == set(MCP_TOOLS["concierge"])


def test_a_call_row_names_its_section(engine, room):
    asset = concierge_asset(engine, room["id"])
    discover(engine, room["id"], asset)
    assert {row["data"]["section"] for row in engine.calls(room_id=room["id"])} == {"concierge"}


# --------------------------------------------------------------------------- #
# The audit trail, and the source rule
# --------------------------------------------------------------------------- #


def test_every_write_method_requires_a_source(engine, room):
    """The defect this prevents: an audit row naming a path nobody served.

    ``source`` is keyword-only and has no default, so a caller that forgets it
    fails loudly rather than writing a null into the audit log.
    """
    with pytest.raises(TypeError):
        engine.create_asset(room["id"], {"name": "x"})
    with pytest.raises(TypeError):
        engine.create_credential({"role": "admin", "label": "x"})
    with pytest.raises(TypeError):
        engine.discover(room["id"], {})
    with pytest.raises(TypeError):
        engine.book(room["id"], "route_x", {})
    with pytest.raises(TypeError):
        engine.update_asset("x", {})
    with pytest.raises(TypeError):
        engine.delete_asset("x")
    with pytest.raises(TypeError):
        engine.add_calendar_block(room["id"], {})
    with pytest.raises(TypeError):
        engine.revoke_credential("x")


def test_a_session_is_audited_with_the_source_it_was_given(engine, store, room):
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    entries = store.audit(collection=SESSION_COLLECTION)
    assert entries[0]["source"] == SOURCE
    assert entries[0]["room_id"] == room["id"]
    assert store.get(session["routeId"])["id"] == session["routeId"]


def test_a_booking_is_audited_under_the_book_routes_source(engine, store, room):
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    book_first(engine, room["id"], session)
    sources = {row["source"] for row in store.audit(collection=MEETING_COLLECTION)}
    assert sources == {BOOK_SOURCE}


def test_the_invites_and_the_webhook_are_audited_under_the_same_source(engine, store, room):
    """They are writes, they are audited, and an audit row that cannot be traced
    back to the request that caused it is not an audit trail."""
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    book_first(engine, room["id"], session)
    for collection in (INVITE_COLLECTION, WEBHOOK_COLLECTION):
        assert {row["source"] for row in store.audit(collection=collection)} == {BOOK_SOURCE}


def test_the_session_move_is_audited_under_the_books_source(engine, store, room):
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    book_first(engine, room["id"], session)
    actions = [row["action"] for row in store.audit(collection=SESSION_COLLECTION)]
    assert actions == ["update", "insert"]


def test_a_refused_booking_still_audits_what_it_did(engine, store, room):
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    with pytest.raises(HeadlessBookingError):
        book_first(engine, room["id"], session, startTime="2026-01-01T09:00:00Z")
    assert store.audit(collection=SESSION_COLLECTION)
    assert store.audit(collection=CALL_COLLECTION)


def test_the_actor_reaches_the_audit_row(engine, store, room):
    asset = concierge_asset(engine, room["id"])
    engine.discover(
        room["id"],
        {
            "section": "concierge",
            "asset_id": asset["id"],
            "interval": future_interval(),
            "guest": {"guestEmail": "a@b.example"},
        },
        actor="sam",
        source=SOURCE,
    )
    assert store.audit(collection=SESSION_COLLECTION)[0]["actor"] == "sam"
    assert store.list(SESSION_COLLECTION)[0]["actor"] == "sam"


def test_the_audit_source_names_the_route_that_served_the_write(http):
    """Every recorded source matches a route the app actually serves.

    The defect this exists to catch shipped in this codebase before: a feature's
    audit log kept naming a route the app had stopped serving. The check is
    behavioural - it reads what the audit log actually recorded, after driving
    every write endpoint, and asks the running app what it actually mounted.

    Checked against *every* route, core included, because the audit log is
    shared: the test creates its room through the core records API, and that
    write's source is a core route. Asserting only against this feature's routes
    would pass for the wrong reason and fail for an unrelated one.
    """
    http.post(f"{PREFIX}/credentials", json={"role": "admin", "label": "x"})
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    asset = http.post(
        f"{PREFIX}/assets",
        params={"room_id": room["id"]},
        json={
            "name": "Northwind",
            "section": "concierge",
            "router_slug": "nw",
            "host_email": "dana@example.com",
        },
    ).json()
    http.post(
        f"{PREFIX}/calendar",
        params={"room_id": room["id"]},
        json={
            "host_email": "dana@example.com",
            "startTime": "2026-10-06T13:00:00Z",
            "duration": 60,
        },
    )
    http.patch(f"{PREFIX}/assets/{asset['id']}", json={"provider": "gong"})
    session = http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions",
        json={
            "section": "concierge",
            "asset_id": asset["id"],
            "interval": {"startsAt": "2026-10-05T08:00:00Z", "duration": 720},
            "guest": {"guestEmail": "buyer@example.com"},
        },
    ).json()
    http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions/{session['routeId']}/book",
        json={
            "startTime": session["schedulingData"][0]["startTimes"][0],
            "guest": {"guestEmail": "buyer@example.com"},
        },
    )
    http.delete(f"{PREFIX}/assets/{asset['id']}")

    store = RecordStore(client_store(http))
    sources = {row["source"] for row in store.audit(limit=1000) if row["source"]}
    routes = all_served_routes(http)

    assert sources, "no source was recorded, so the check proved nothing"
    for source in sorted(sources):
        assert source_names_a_mounted_route(source, routes), (
            f"{source!r} names no route this app serves"
        )


def test_every_source_this_feature_records_is_under_its_own_prefix(http):
    """And the sharper half: this feature never records a route that is not its own.

    The weaker check above would pass even if a domain function hardcoded a
    perfectly valid *core* path. This one cannot.
    """
    http.post(f"{PREFIX}/credentials", json={"role": "admin", "label": "x"})
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    asset = http.post(
        f"{PREFIX}/assets",
        params={"room_id": room["id"]},
        json={
            "name": "Northwind",
            "section": "concierge",
            "router_slug": "nw",
            "host_email": "dana@example.com",
        },
    ).json()
    http.post(
        f"{PREFIX}/calendar",
        params={"room_id": room["id"]},
        json={
            "host_email": "dana@example.com",
            "startTime": "2026-10-06T13:00:00Z",
            "duration": 60,
        },
    )
    session = http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions",
        json={
            "section": "concierge",
            "asset_id": asset["id"],
            "interval": {"startsAt": "2026-10-05T08:00:00Z", "duration": 720},
            "guest": {"guestEmail": "buyer@example.com"},
        },
    ).json()
    http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions/{session['routeId']}/book",
        json={
            "startTime": session["schedulingData"][0]["startTimes"][0],
            "guest": {"guestEmail": "buyer@example.com"},
        },
    )
    http.patch(f"{PREFIX}/assets/{asset['id']}", json={"provider": "gong"})

    store = RecordStore(client_store(http))
    mine = {
        row["source"]
        for row in store.audit(limit=1000)
        if row["collection"]
        in (
            SESSION_COLLECTION,
            MEETING_COLLECTION,
            INVITE_COLLECTION,
            WEBHOOK_COLLECTION,
            CALL_COLLECTION,
            CREDENTIAL_COLLECTION,
            ASSET_COLLECTION,
            CALENDAR_COLLECTION,
        )
        and row["source"]
    }
    routes = mounted_routes(http)

    assert len(mine) >= 6, "the feature recorded fewer sources than it has write routes"
    for source in sorted(mine):
        assert (
            source.startswith(f"POST {PREFIX}")
            or source.startswith(f"PATCH {PREFIX}")
            or (source.startswith(f"DELETE {PREFIX}"))
        ), f"{source!r} does not name a route under this feature's own prefix"
        assert source_names_a_mounted_route(source, routes), f"{source!r} names no mounted route"


def test_a_session_written_over_http_records_its_own_route(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    asset = http.post(
        f"{PREFIX}/assets",
        params={"room_id": room["id"]},
        json={
            "name": "Northwind",
            "section": "concierge",
            "router_slug": "nw",
            "host_email": "dana@example.com",
        },
    ).json()
    created = http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions",
        json={
            "section": "concierge",
            "asset_id": asset["id"],
            "interval": {"startsAt": "2026-10-05T08:00:00Z", "duration": 720},
            "guest": {"guestEmail": "buyer@example.com"},
        },
    )
    assert created.status_code == 201
    store = RecordStore(client_store(http))
    sources = {row["source"] for row in store.audit(collection=SESSION_COLLECTION)}
    assert sources == {f"POST {PREFIX}/rooms/{room['id']}/sessions"}


def test_a_booking_writes_five_records_and_five_audit_rows_in_one_transaction(engine, store, room):
    """Meeting, two invites, the webhook, and the session move commit together.

    The research says a commit produces all of them, and a crash between them
    would leave a meeting with no invite - a half-state the data flow does not
    describe.
    """
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    before = store.db.audit_count()
    book_first(engine, room["id"], session)
    # Five inside the transaction - meeting, two invites, the webhook, and the
    # session's move to `booked` - plus one outside it, for the call-log row the
    # attempt handler always writes.
    assert store.db.audit_count() - before == 6
    assert len(store.list(MEETING_COLLECTION)) == 1
    assert len(store.list(INVITE_COLLECTION)) == 2
    assert len(store.list(WEBHOOK_COLLECTION)) == 1


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


def test_vocabulary_is_served_over_http(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["sections"] == list(SECTIONS)
    assert body["permissions"] == list(PERMISSIONS)
    assert body["session_states"] == list(SESSION_STATES)
    assert body["ttl"]["sourced"] is False
    assert body["collections"]
    assert body["assets"]["defaults_sourced"] is False


def test_inferences_are_served_over_http(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(headless_inferences.INFERENCES)
    assert body["sourced_quote"]
    assert len(body["sourced"]["session_rules"]) == 4


def test_the_sourced_rules_are_served_on_their_own(http):
    body = http.get(f"{PREFIX}/rules").json()
    assert len(body["rules"]) == 4
    assert body["quotes"]["single_use"]
    assert body["on_book"]


def test_the_summary_over_http_is_room_scoped_when_asked(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    assert http.get(f"{PREFIX}/summary").json()["sessions"] == 0
    assert http.get(f"{PREFIX}/summary", params={"room_id": room["id"]}).json()["sessions"] == 0


def test_generating_a_credential_over_http_returns_the_token_once(http):
    created = http.post(f"{PREFIX}/credentials", json={"role": "admin", "label": "x"})
    assert created.status_code == 201
    body = created.json()
    assert body["token"].startswith(TOKEN_PREFIX)
    listed = http.get(f"{PREFIX}/credentials").json()
    assert "token" not in listed["credentials"][0]
    assert "token" not in http.get(f"{PREFIX}/credentials/{body['id']}").json()


def test_a_workspace_manager_is_a_403_over_http_naming_the_rule(http):
    response = http.post(f"{PREFIX}/credentials", json={"role": "workspace_manager", "label": "x"})
    assert response.status_code == 403
    body = response.json()
    assert body["error"] == "permission_denied"
    assert "Workspace Managers do not have access" in body["detail"]


def test_a_malformed_credential_is_a_400_over_http(http):
    response = http.post(f"{PREFIX}/credentials", json={"role": "admin"})
    assert response.status_code == 400
    assert response.json()["error"] == "headless_booking_error"
    assert "label" in response.json()["detail"]


def test_an_unknown_credential_is_a_404_over_http(http):
    assert http.get(f"{PREFIX}/credentials/nope").status_code == 404
    assert http.delete(f"{PREFIX}/credentials/nope").status_code == 404


def test_revoking_a_credential_is_a_204_over_http(http):
    created = http.post(f"{PREFIX}/credentials", json={"role": "admin", "label": "x"}).json()
    assert http.delete(f"{PREFIX}/credentials/{created['id']}").status_code == 204
    assert http.get(f"{PREFIX}/credentials/{created['id']}").status_code == 404


def http_asset(http, room_id, **overrides):
    payload = {
        "name": "Northwind",
        "section": "concierge",
        "router_slug": "nw",
        "host_name": "Dana",
        "host_email": "dana@example.com",
    } | overrides
    return http.post(f"{PREFIX}/assets", params={"room_id": room_id}, json=payload).json()


def test_the_asset_lifecycle_over_http(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    asset = http_asset(http, room["id"])
    assert http.get(f"{PREFIX}/assets").json()["count"] == 1
    assert http.get(f"{PREFIX}/assets", params={"section": "concierge"}).json()["count"] == 1
    assert http.get(f"{PREFIX}/assets", params={"section": "links"}).json()["count"] == 0
    assert http.get(f"{PREFIX}/assets/{asset['id']}").json()["data"]["provider"] == "zoom"
    assert (
        http.patch(f"{PREFIX}/assets/{asset['id']}", json={"provider": "gong"}).status_code == 200
    )
    assert http.delete(f"{PREFIX}/assets/{asset['id']}").status_code == 204
    assert http.get(f"{PREFIX}/assets/{asset['id']}").status_code == 404


def test_an_unknown_asset_is_a_404_over_http(http):
    assert http.get(f"{PREFIX}/assets/nope").status_code == 404
    assert http.delete(f"{PREFIX}/assets/nope").status_code == 404
    assert http.patch(f"{PREFIX}/assets/nope", json={"provider": "zoom"}).status_code == 404


def test_a_bad_asset_is_a_400_over_http(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    response = http.post(
        f"{PREFIX}/assets",
        params={"room_id": room["id"]},
        json={"name": "x", "section": "concierge", "host_email": "a@example.com"},
    )
    assert response.status_code == 400
    assert "{routerSlug}" in response.json()["detail"]


def test_the_calendar_block_over_http(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    created = http.post(
        f"{PREFIX}/calendar",
        params={"room_id": room["id"]},
        json={
            "host_email": "dana@example.com",
            "startTime": "2026-10-06T13:00:00Z",
            "duration": 60,
        },
    )
    assert created.status_code == 201
    assert http.get(f"{PREFIX}/calendar").json()["count"] == 1
    assert (
        http.get(f"{PREFIX}/calendar", params={"host_email": "dana@example.com"}).json()["count"]
        == 1
    )
    assert (
        http.get(f"{PREFIX}/calendar", params={"host_email": "other@example.com"}).json()["count"]
        == 0
    )


def test_a_calendar_block_with_no_end_is_a_400_over_http(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    response = http.post(
        f"{PREFIX}/calendar",
        params={"room_id": room["id"]},
        json={"host_email": "dana@example.com", "startTime": "2026-10-06T13:00:00Z"},
    )
    assert response.status_code == 400
    assert "unbounded busy block" in response.json()["detail"]


def test_the_two_calls_over_http_commit_a_meeting(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    asset = http_asset(http, room["id"])
    session = http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions",
        json={
            "section": "concierge",
            "asset_id": asset["id"],
            "interval": {"startsAt": "2026-10-05T08:00:00Z", "duration": 720},
            "guest": {"guestEmail": "buyer@example.com"},
        },
    )
    assert session.status_code == 201
    body = session.json()
    assert body["routeId"]
    assert body["schedulingData"][0]["startTimes"]

    booked = http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions/{body['routeId']}/book",
        json={
            "startTime": body["schedulingData"][0]["startTimes"][0],
            "guest": {"guestEmail": "buyer@example.com"},
        },
    )
    assert booked.status_code == 201
    assert booked.json()["meetingId"]
    assert booked.json()["invites_sent"] is False
    assert booked.json()["webhook_event"] == WEBHOOK_EVENT


def test_the_researched_retry_is_a_400_over_http_saying_so(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    asset = http_asset(http, room["id"])
    session = http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions",
        json={
            "section": "concierge",
            "asset_id": asset["id"],
            "interval": {"startsAt": "2026-10-05T08:00:00Z", "duration": 720},
            "guest": {"guestEmail": "buyer@example.com"},
        },
    ).json()
    payload = {
        "startTime": session["schedulingData"][0]["startTimes"][0],
        "guest": {"guestEmail": "buyer@example.com"},
    }
    assert (
        http.post(
            f"{PREFIX}/rooms/{room['id']}/sessions/{session['routeId']}/book", json=payload
        ).status_code
        == 201
    )

    retry = http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions/{session['routeId']}/book", json=payload
    )
    assert retry.status_code == 400
    body = retry.json()
    # The reason code is what a client branches on, and it is in the published
    # vocabulary rather than prose a caller has to pattern-match.
    assert body["reason"] == "session_consumed"
    assert body["error"] == "headless_booking_error"
    assert "discover or route" in body["detail"]
    assert http.get(f"{PREFIX}/rooms/{room['id']}/meetings").json()["count"] == 1


def test_a_bad_section_over_http_is_a_400_naming_the_published_set(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions",
        json={"section": "chat", "interval": {"startsAt": "2026-10-05T00:00:00Z", "duration": 60}},
    )
    assert response.status_code == 400
    for section in SECTIONS:
        assert section in response.json()["detail"]


def test_every_refusal_over_http_carries_a_published_reason(http):
    """A 400 with no reason code is a 400 a caller can only read."""
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    asset = http_asset(http, room["id"])
    published = set(SCHEDULE_FAILURES) | set(INIT_FAILURES)

    session = http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions",
        json={
            "section": "concierge",
            "asset_id": asset["id"],
            "interval": {"startsAt": "2026-10-05T08:00:00Z", "duration": 720},
            "guest": {"guestEmail": "buyer@example.com"},
        },
    ).json()
    not_offered = http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions/{session['routeId']}/book",
        json={"startTime": "2026-01-01T09:00:00Z", "guest": {"guestEmail": "buyer@example.com"}},
    )
    assert not_offered.status_code == 400
    assert not_offered.json()["reason"] in published
    assert not_offered.json()["reason"] == "start_time_not_offered"


def test_a_start_time_that_was_never_offered_is_a_400_over_http(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    asset = http_asset(http, room["id"])
    session = http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions",
        json={
            "section": "concierge",
            "asset_id": asset["id"],
            "interval": {"startsAt": "2026-10-05T08:00:00Z", "duration": 720},
            "guest": {"guestEmail": "buyer@example.com"},
        },
    ).json()
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions/{session['routeId']}/book",
        json={"startTime": "2026-01-01T09:00:00Z", "guest": {"guestEmail": "a@example.com"}},
    )
    assert response.status_code == 400
    assert "verbatim" in response.json()["detail"]


def test_booking_an_unknown_session_is_a_404_over_http(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions/route_nope/book",
        json={"startTime": "2026-10-05T09:00:00Z"},
    )
    assert response.status_code == 404
    assert response.json()["resource"] == "session"


def test_a_read_only_token_is_a_403_over_http(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    asset = http_asset(http, room["id"])
    token = http.post(
        f"{PREFIX}/credentials", json={"role": "admin", "label": "ro", "permissions": ["read"]}
    ).json()
    session = http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions",
        json={
            "section": "concierge",
            "asset_id": asset["id"],
            "interval": {"startsAt": "2026-10-05T08:00:00Z", "duration": 720},
            "guest": {"guestEmail": "buyer@example.com"},
            "credential_id": token["id"],
        },
    ).json()
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions/{session['routeId']}/book",
        json={
            "startTime": session["schedulingData"][0]["startTimes"][0],
            "guest": {"guestEmail": "buyer@example.com"},
            "credential_id": token["id"],
        },
    )
    assert response.status_code == 403
    assert response.json()["required_permission"] == "schedule"


def test_reading_a_session_over_http_reports_whether_it_can_be_retried(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    asset = http_asset(http, room["id"])
    session = http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions",
        json={
            "section": "concierge",
            "asset_id": asset["id"],
            "interval": {"startsAt": "2026-10-05T08:00:00Z", "duration": 720},
            "guest": {"guestEmail": "buyer@example.com"},
        },
    ).json()
    read = http.get(f"{PREFIX}/rooms/{room['id']}/sessions/{session['routeId']}")
    assert read.status_code == 200
    assert read.json()["data"]["retry_with_same_route_id"] is True
    assert read.json()["data"]["next_step"]


def test_sessions_are_listed_and_filtered_over_http(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    asset = http_asset(http, room["id"])
    http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions",
        json={
            "section": "concierge",
            "asset_id": asset["id"],
            "interval": {"startsAt": "2026-10-05T08:00:00Z", "duration": 720},
            "guest": {"guestEmail": "buyer@example.com"},
        },
    )
    assert http.get(f"{PREFIX}/rooms/{room['id']}/sessions").json()["count"] == 1
    assert (
        http.get(f"{PREFIX}/rooms/{room['id']}/sessions", params={"state": "open"}).json()["count"]
        == 1
    )
    assert (
        http.get(f"{PREFIX}/rooms/{room['id']}/sessions", params={"state": "booked"}).json()[
            "count"
        ]
        == 0
    )


def test_a_meeting_is_read_with_its_invites_and_webhook_over_http(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    asset = http_asset(http, room["id"])
    session = http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions",
        json={
            "section": "concierge",
            "asset_id": asset["id"],
            "interval": {"startsAt": "2026-10-05T08:00:00Z", "duration": 720},
            "guest": {"guestEmail": "buyer@example.com"},
        },
    ).json()
    booked = http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions/{session['routeId']}/book",
        json={
            "startTime": session["schedulingData"][0]["startTimes"][0],
            "guest": {"guestEmail": "buyer@example.com"},
        },
    ).json()

    listed = http.get(f"{PREFIX}/rooms/{room['id']}/meetings").json()
    assert listed["count"] == 1
    assert listed["by_provider"] == {"zoom": 1}

    one = http.get(f"{PREFIX}/rooms/{room['id']}/meetings/{booked['meetingId']}")
    assert one.status_code == 200
    assert len(one.json()["invites"]) == 2
    assert len(one.json()["webhook"]) == 1


def test_a_meeting_from_another_room_is_a_404_over_http(http):
    first = http.post("/api/records/room", json={"name": "One"}).json()
    second = http.post("/api/records/room", json={"name": "Two"}).json()
    asset = http_asset(http, first["id"])
    session = http.post(
        f"{PREFIX}/rooms/{first['id']}/sessions",
        json={
            "section": "concierge",
            "asset_id": asset["id"],
            "interval": {"startsAt": "2026-10-05T08:00:00Z", "duration": 720},
            "guest": {"guestEmail": "buyer@example.com"},
        },
    ).json()
    booked = http.post(
        f"{PREFIX}/rooms/{first['id']}/sessions/{session['routeId']}/book",
        json={
            "startTime": session["schedulingData"][0]["startTimes"][0],
            "guest": {"guestEmail": "buyer@example.com"},
        },
    ).json()
    assert (
        http.get(f"{PREFIX}/rooms/{second['id']}/meetings/{booked['meetingId']}").status_code == 404
    )
    assert (
        http.get(f"{PREFIX}/rooms/{second['id']}/sessions/{session['routeId']}").status_code == 404
    )


def test_the_call_log_over_http_counts_outcomes(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    asset = http_asset(http, room["id"])
    session = http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions",
        json={
            "section": "concierge",
            "asset_id": asset["id"],
            "interval": {"startsAt": "2026-10-05T08:00:00Z", "duration": 720},
            "guest": {"guestEmail": "buyer@example.com"},
        },
    ).json()
    payload = {
        "startTime": session["schedulingData"][0]["startTimes"][0],
        "guest": {"guestEmail": "buyer@example.com"},
    }
    http.post(f"{PREFIX}/rooms/{room['id']}/sessions/{session['routeId']}/book", json=payload)
    http.post(f"{PREFIX}/rooms/{room['id']}/sessions/{session['routeId']}/book", json=payload)

    body = http.get(f"{PREFIX}/rooms/{room['id']}/calls").json()
    assert body["count"] == 3
    assert body["by_outcome"]["opened"] == 1
    # The retry is its own outcome rather than a second "booked".
    assert body["by_outcome"]["booked"] == 1
    assert body["by_outcome"]["session_consumed"] == 1
    assert (
        http.get(
            f"{PREFIX}/rooms/{room['id']}/calls", params={"outcome": "session_consumed"}
        ).json()["count"]
        == 1
    )


def test_the_actor_query_parameter_reaches_the_audit_row_over_http(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    asset = http_asset(http, room["id"])
    http.post(
        f"{PREFIX}/rooms/{room['id']}/sessions",
        params={"actor": "sam"},
        json={
            "section": "concierge",
            "asset_id": asset["id"],
            "interval": {"startsAt": "2026-10-05T08:00:00Z", "duration": 720},
            "guest": {"guestEmail": "buyer@example.com"},
        },
    )
    store = RecordStore(client_store(http))
    assert store.audit(collection=SESSION_COLLECTION)[0]["actor"] == "sam"


def test_the_core_app_still_works_with_the_feature_mounted(http):
    assert http.get("/api/health").status_code == 200
    assert http.get("/api/records/room").status_code == 200
    assert http.get("/api/audit").status_code == 200


# --------------------------------------------------------------------------- #
# The inferences
# --------------------------------------------------------------------------- #


def test_every_inference_is_named_and_its_ids_are_unique():
    ids = [entry["id"] for entry in headless_inferences.INFERENCES]
    assert all(ids)
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("entry_id", [entry["id"] for entry in headless_inferences.INFERENCES])
def test_every_inference_says_why_it_chooses_and_how_to_change_it(entry_id):
    """A judgement call with no `change_it` cannot be argued with."""
    entry = headless_inferences.by_id(entry_id)
    assert entry is not None
    for field in ("topic", "basis", "value", "why", "change_it", "blast_radius"):
        assert entry[field], f"{entry_id} is missing {field}"


def test_the_inference_registry_describes_both_halves_of_the_workflow():
    body = headless_inferences.describe()
    assert body["sourced_quote"] == headless_inferences.SINGLE_USE_QUOTE
    assert len(body["sourced"]["session_rules"]) == 4
    assert body["sourced"]["webhook"]["event"] == WEBHOOK_EVENT
    assert body["sourced"]["ttl"]["published"] is False


def test_the_failure_rule_inference_matches_what_the_session_does():
    entry = headless_inferences.by_id("failure-consumes-the-session")
    assert entry["value"]["consumes_on_failure"] is True
    session = build_session()
    session.consume(NOW, "failed")
    assert session.state == "failed"
    with pytest.raises(HeadlessBookingError):
        session.consume(NOW, "booked")


def test_the_verbatim_inference_matches_what_resolve_slot_does():
    entry = headless_inferences.by_id("verbatim-means-one-of-the-offered-strings")
    assert entry["value"]["refused_no_designator"] is True
    with pytest.raises(HeadlessBookingError):
        parse_start_time("2026-10-05T09:00:00")
    assert (
        build_session().resolve_slot("2026-10-05T09:00:00+00:00", None)[0] == "2026-10-05T09:00:00Z"
    )


def test_the_grid_inference_matches_the_published_defaults():
    entry = headless_inferences.by_id("slot-generation-is-a-grid")
    assert entry["value"]["default_slot_minutes"] == "the meeting length"
    assert entry["value"]["default_meeting_minutes"] == DEFAULT_MEETING_MINUTES
    spec = normalise_asset(
        {
            "name": "x",
            "section": "concierge",
            "router_slug": "n",
            "host_email": "a@example.com",
            "duration_minutes": 45,
        }
    )
    assert spec["slot_minutes"] == spec["duration_minutes"] == 45


def test_the_grid_inference_admits_a_finer_grid_is_allowed_not_forbidden():
    """The inference has to say what the code does, including the trade it made."""
    entry = headless_inferences.by_id("slot-generation-is-a-grid")
    assert "slot_taken" in entry["value"]["overlapping_starts"]
    assert "45-minute" in entry["why"]


def test_the_local_time_inference_admits_it_is_not_dst_aware():
    """A reviewer can see what is insufficient rather than finding it in a diff."""
    entry = headless_inferences.by_id("local-time-is-one-fixed-offset")
    assert entry["value"]["dst_aware"] is False
    spec = normalise_asset(
        {
            "name": "x",
            "section": "concierge",
            "router_slug": "n",
            "host_email": "a@example.com",
            "utc_offset_minutes": 330,
        }
    )
    assert spec["utc_offset_minutes"] == 330


def test_the_ttl_inference_matches_the_published_numbers():
    entry = headless_inferences.by_id("session-ttl-durations")
    for section, value in entry["value"].items():
        if isinstance(value, int) and section in DEFAULT_TTL_MS:
            assert DEFAULT_TTL_MS[section] == value


def test_the_failed_book_inference_matches_what_a_refusal_leaves_behind(engine, room, store):
    entry = headless_inferences.by_id("failed-book-writes-nothing-but-its-record")
    assert entry["value"]["meeting"] == "never written"
    assert entry["value"]["call_log"] == "always written, refused or not"
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    with pytest.raises(HeadlessBookingError):
        book_first(engine, room["id"], session, startTime="2026-01-01T09:00:00Z")
    assert store.list(MEETING_COLLECTION) == []
    assert store.list(INVITE_COLLECTION) == []
    assert store.list(WEBHOOK_COLLECTION) == []
    assert store.list(CALL_COLLECTION)  # the exception the entry names


def test_the_invites_inference_matches_what_a_booking_records(engine, room, store):
    entry = headless_inferences.by_id("invites-and-webhook-are-recorded-not-sent")
    assert entry["value"]["invites_written"] is True
    assert entry["value"]["invites_sent"] is False
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    booked = book_first(engine, room["id"], session)
    assert store.list(INVITE_COLLECTION)
    assert store.list(WEBHOOK_COLLECTION)
    assert booked["invites_sent"] is False


def test_the_crm_writeback_inference_matches_the_meeting(engine, room):
    entry = headless_inferences.by_id("crm-writeback-is-recorded")
    assert entry["value"]["writes_to_a_crm"] is False
    assert entry["value"]["off_by_default"] is True
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    assert (
        book_first(engine, room["id"], session)["meeting"]["data"]["crm_writeback"]["written"]
        is False
    )


def test_the_authorisation_inference_matches_what_a_bare_call_records(engine, room):
    entry = headless_inferences.by_id("token-lookup-by-id-or-by-secret")
    assert "credential id" in str(entry["value"]["accepted"])
    assert "require_credential" in entry["value"]
    assert "refused" in entry["value"]["a_token_matching_nothing"]
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    assert session["authorised_as"] == "installation"


def test_a_token_matching_no_credential_is_refused_not_treated_as_absent(engine, room):
    """The failure that would otherwise turn a bad credential into full access."""
    asset = concierge_asset(engine, room["id"])
    with pytest.raises(PermissionDenied) as caught:
        discover(engine, room["id"], asset, token=generate_token())
    assert "not valid for any active credential" in str(caught.value)


def test_the_read_permission_inference_matches_the_two_gated_calls(engine, room):
    """ "Read where listing assets is needed" - and a single fetch leaks the same."""
    entry = headless_inferences.by_id("list-and-lookup-are-one-permission")
    assert entry["value"]["read_gates"] == ["list", "read-one"]
    token = engine.create_credential(
        {"role": "admin", "label": "x", "permissions": ["schedule"]}, source=SOURCE
    )
    asset = concierge_asset(engine, room["id"])
    with pytest.raises(PermissionDenied):
        discover(engine, room["id"], asset, credential_id=token["id"])


def test_the_digest_inference_matches_what_is_stored(engine):
    entry = headless_inferences.by_id("stored-token-is-a-digest")
    assert entry["value"]["stored"] == "sha-256 digest plus a prefix and last four"
    minted = engine.create_credential({"role": "admin", "label": "x"}, source=SOURCE)
    assert token_digest(minted["token"]) in str(store_credential(engine, minted["id"])["data"])


def test_the_path_inference_matches_the_two_single_path_surfaces():
    """/concierge/.../schedule-simple has no {pathId}, so there is none to send."""
    entry = headless_inferences.by_id("handoff-paths-single-path-elsewhere")
    assert entry["value"]["concierge"] == "one path, pathId null"
    assert build_session().scheduling_data()[0]["pathId"] is None


def test_the_slot_taken_inference_matches_what_a_taken_slot_does(engine, room, other_room, store):
    entry = headless_inferences.by_id("slot-taken-is-a-refusal-not-a-queue")
    assert entry["value"]["substitutes_another_slot"] is False
    assert entry["value"]["consumes_the_session"] is True
    asset = concierge_asset(engine, room["id"])
    session = discover(engine, room["id"], asset)
    slot = first_slot(session)
    rival = concierge_asset(engine, other_room["id"])
    rival_session = discover(engine, other_room["id"], rival)
    engine.book(
        other_room["id"],
        rival_session["routeId"],
        {"startTime": slot, "guest": {"guestEmail": "r@example.com"}},
        source=BOOK_SOURCE,
    )
    with pytest.raises(HeadlessBookingError):
        book_first(engine, room["id"], session, startTime=slot)
    assert len(store.list(MEETING_COLLECTION)) == 1
    assert store.get(session["routeId"])["data"]["state"] == "failed"


def test_the_no_outbound_call_inference_matches_the_engine():
    entry = headless_inferences.by_id("no-outbound-call")
    assert entry["value"]["calls_outbound"] is False
    assert "busy_by_key" in entry["change_it"]


# --------------------------------------------------------------------------- #
# The demo data
# --------------------------------------------------------------------------- #


@pytest.fixture()
def seed_module():
    return load_feature(MODULE)


@pytest.fixture()
def seeded(db):
    """The demo dataset, seeded against three rooms."""
    store = RecordStore(db)
    rooms = [
        (store.create("room", {"name": name}, actor="dana", source="core")["id"], name)
        for name in ("Northwind", "Contoso", "Fabrikam", "Adventure")
    ]
    load_feature(MODULE).seed(db, {"room_ids": rooms, "now": NOW})
    return store, rooms


def test_the_seed_reports_what_it_added(db, seed_module):
    store = RecordStore(db)
    rooms = [
        (store.create("room", {"name": name}, actor="dana", source="core")["id"], name)
        for name in ("Northwind", "Contoso", "Fabrikam", "Adventure")
    ]
    summary = seed_module.seed(db, {"room_ids": rooms, "now": NOW})
    assert isinstance(summary, str)
    assert "5 bookable assets" in summary
    assert "2 scoped tokens" in summary
    assert "4 calendar blocks" in summary


def test_the_seed_covers_all_three_surfaces(seeded, seed_module):
    sections = {row["data"]["section"] for row in seeded[0].list(ASSET_COLLECTION)}
    assert sections == set(SECTIONS)


def test_the_seed_includes_both_link_types_the_ownership_rule_depends_on(seeded):
    link_types = {
        row["data"].get("link_type")
        for row in seeded[0].list(ASSET_COLLECTION)
        if row["data"].get("section") == "links"
    }
    assert "ownership" in link_types
    assert "personal" in link_types


def test_the_seed_includes_a_disabled_asset(seeded):
    assert any(row["data"].get("enabled") is False for row in seeded[0].list(ASSET_COLLECTION))


def test_the_seed_includes_a_handoff_asset_with_two_paths(seeded):
    handoff = next(
        row for row in seeded[0].list(ASSET_COLLECTION) if row["data"].get("section") == "handoff"
    )
    assert len(handoff["data"]["paths"]) == 2


def test_the_seed_produces_a_booked_meeting(seeded):
    store, _ = seeded
    assert store.list(MEETING_COLLECTION)


def test_the_seed_produces_both_invites_and_a_webhook_for_each_meeting(seeded):
    store, _ = seeded
    for meeting in store.list(MEETING_COLLECTION):
        invites = store.find(INVITE_COLLECTION, {"meeting_id": meeting["id"]}, limit=10)
        events = store.find(WEBHOOK_COLLECTION, {"meeting_id": meeting["id"]}, limit=10)
        assert len(invites) == 2
        assert len(events) == 1
        assert events[0]["data"]["event"] == WEBHOOK_EVENT


def test_the_seed_produces_a_failed_session_not_only_a_booked_one(seeded):
    """ "demo data containing only success teaches a reviewer nothing" """
    store, _ = seeded
    states = {row["data"]["state"] for row in store.list(SESSION_COLLECTION)}
    assert "booked" in states
    assert "failed" in states


def test_the_seed_shows_the_researched_retry_being_refused(seeded):
    store, _ = seeded
    outcomes = {row["data"]["outcome"] for row in store.list(CALL_COLLECTION)}
    assert "session_consumed" in outcomes


def test_the_seed_shows_the_ownership_guest_email_rule_being_enforced(seeded, seed_module):
    """A refusal the seeder runs and expects is the demo asserting the rule.

    The ownership case raises at session creation, so it leaves no row; what it
    leaves is a `Refusal` the seed swallowed. The demo states it in its own
    return value, and *this* test re-runs it against the seeded data to prove the
    rule still holds rather than trusting the label.
    """
    store, _ = seeded
    ownership = next(
        row for row in store.list(ASSET_COLLECTION) if row["data"].get("link_type") == "ownership"
    )
    from dsr.headless_booking.engine import HeadlessBooking

    engine = HeadlessBooking(store, clock=lambda: NOW + timedelta(days=30))
    with pytest.raises(Refusal) as caught:
        engine.discover(
            ownership["room_id"],
            {
                "section": "links",
                "asset_id": ownership["id"],
                "interval": {
                    "startsAt": format_slot(NOW + timedelta(days=31)),
                    "duration": 480,
                },
                # No guest. Deliberate.
                "guest": {"name": "Procurement"},
            },
            source="seed",
        )
    assert caught.value.reason == "guest_email_required"
    assert seed_module.DEMO_ASSETS  # the label the seeder prints comes from here


def test_the_seed_shows_a_read_only_token_being_refused(seeded):
    """A per-section, per-permission scope rule is demonstrated, not described."""
    store, _ = seeded
    authorised = {row["data"].get("authorised_as") for row in store.list(CALL_COLLECTION)}
    assert "credential_id" in authorised
    # The read-only token is scoped to one section and holds only `read`, so it
    # never reaches a booking - which is why no call row carries it as the
    # booker's credential.
    scoped = [
        row
        for row in store.list(CALL_COLLECTION)
        if row["data"].get("authorised_as") == "credential_id"
    ]
    assert scoped


def test_the_seed_shows_a_handoff_booking_naming_its_path(seeded):
    store, _ = seeded
    handoff_meetings = [
        row for row in store.list(MEETING_COLLECTION) if row["data"].get("section") == "handoff"
    ]
    assert handoff_meetings
    assert handoff_meetings[0]["data"]["pathId"] in ("path-emea", "path-amer")


def test_the_seed_produces_a_session_with_a_local_time_offset(seeded):
    """A +05:30 desk offers 04:30-11:30 UTC, which a UTC-only reader would miss."""
    store, _ = seeded
    priya = next(
        row
        for row in store.list(ASSET_COLLECTION)
        if row["data"].get("host_email") == "priya.raman@example.com"
    )
    assert priya["data"]["utc_offset_minutes"] == 330


def test_the_seed_audits_its_own_writes_under_the_seed_source(seeded):
    store, _ = seeded
    sources = {row["source"] for row in store.audit(collection=MEETING_COLLECTION)}
    assert sources == {"seed"}


def test_the_seed_survives_being_run_with_no_rooms(db, seed_module):
    """A feature that cannot seed itself is visible rather than silently empty."""
    summary = seed_module.seed(db, {"room_ids": [], "now": NOW})
    assert "no rooms" in summary


#: The seed has to work on every day of the week, and the fixed ``NOW`` above is
#: a Monday. It was the reason this bug survived a green suite: every seeder test
#: anchored on a Monday, the window was always fine, and the failure only ever
#: appeared through ``backend/seed.py``, which uses the real clock. Anchoring the
#: window on ``now + 1 day`` broke specifically on a Friday, when tomorrow is a
#: Saturday and every demo asset books Monday to Friday.
SEED_CLOCKS = [
    datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc),  # Monday, as NOW
    datetime(2026, 9, 29, 23, 59, tzinfo=timezone.utc),  # Tuesday, late
    datetime(2026, 10, 2, 0, 0, tzinfo=timezone.utc),  # Friday, midnight
    datetime(2026, 10, 2, 9, 30, tzinfo=timezone.utc),  # Friday, the day CI broke
    datetime(2026, 10, 2, 23, 59, tzinfo=timezone.utc),  # Friday, late
    datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc),  # Saturday
    datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc),  # Sunday
]

#: The mixed demo the seeder is supposed to produce. Asserted as a list rather
#: than as substrings: ``booked`` is a substring of ``booked_handoff``, so a
#: substring check would pass on a seed that lost its first booking.
SEED_OUTCOMES = ["booked", "failed_offered", "retry_refused", "booked_handoff"]


def _rooms_for(store):
    return [
        (store.create("room", {"name": name}, actor="dana", source="core")["id"], name)
        for name in ("Northwind", "Contoso", "Fabrikam", "Adventure")
    ]


def _instant(value):
    from dsr.headless_booking.availability import parse_instant

    return parse_instant(value, field="startTime")


@pytest.mark.parametrize("clock", SEED_CLOCKS, ids=lambda c: f"{c:%a-%H%M}")
def test_the_seed_books_on_every_day_of_the_week(db, seed_module, clock):
    """The demo books every day the seed might run on, and the booking is ahead.

    On the old code this raised ``Refusal: no availability`` on the three Fridays
    and passed on the rest, which is the whole shape of the bug: not an expiry,
    a weekday.
    """
    store = RecordStore(db)
    summary = seed_module.seed(db, {"room_ids": _rooms_for(store), "now": clock})

    outcomes = summary.split("sessions: ", 1)[1].split(", 1 ownership")[0]
    assert [part.strip() for part in outcomes.split(",")] == SEED_OUTCOMES, summary
    # The point of the window being relative: the meeting it books is in the
    # future relative to the clock that asked for it.
    assert all(
        _instant(row["data"]["startTime"]) > clock for row in store.list(MEETING_COLLECTION)
    ), summary


def test_the_demo_window_always_lands_on_a_working_day(seed_module):
    """The refusal this fix exists for, stated as the rule it enforces.

    Every demo asset declares Monday-to-Fri ``work_days``, so a window anchored
    on a weekend has no slots in it and the workflow refuses with *no
    availability*. Asserted directly on the anchor so the guarantee does not
    depend on the rest of the seed happening to notice.
    """
    for clock in SEED_CLOCKS:
        day = seed_module._demo_day(clock)
        assert day.isoweekday() <= 5, f"{clock:%a} anchored the demo on {day:%a}"
        assert day > clock


def test_the_demo_calendar_blocks_land_inside_the_demo_window(seed_module):
    """The blocks are resolved against the window, so they still block anything.

    Dated absolutely they drift out of the moving window and quietly stop
    blocking: four calendar rows and a wall of free time.
    """
    for clock in SEED_CLOCKS:
        day = seed_module._demo_day(clock)
        window_end = day + timedelta(hours=36)
        for block in seed_module.DEMO_CALENDAR:
            start = _instant(seed_module._stamp(block["startsAt"], day))
            end = _instant(seed_module._stamp(block["endsAt"], day))
            assert day <= start < window_end, (clock, block["label"], start)
            assert start < end <= window_end, (clock, block["label"], end)


def test_the_seeded_assets_are_all_valid_against_the_assets_validator(seeded, seed_module):
    """Every demo row passes the same validation a create request does."""
    from dsr.headless_booking.assets import normalise

    for spec in seed_module.DEMO_ASSETS:
        assert normalise(spec)["section"] == spec["section"]
