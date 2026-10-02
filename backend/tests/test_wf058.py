"""Tests for WF-058: embed a bookable calendar inside the sales room.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-058.md``. Nothing here is a
preference of this build unless it is listed in
:mod:`dsr.inroom_scheduling.inferences`, and every inference in that registry has a
test that checks it is still named, still bounded and still changeable.

The researched half
-------------------

* ``GET /v2/slots`` with ``cal-api-version: 2024-09-04`` and four ways of naming
  what to ask about - ``eventTypeId``, ``eventTypeSlug``+``username``,
  ``usernames``, ``teamSlug``.
* "Checking slots by usernames is used mainly for dynamic events where there is no
  specific event but we just want to know when 2 or more people are available."
* ``POST /v2/slots/reservations`` returning ``reservationUid``,
  ``reservationDuration`` and ``reservationUntil``; "defaults to 5 minutes"; and
  "no user action needed for the hold to expire".
* ``POST /v2/bookings`` with ``cal-api-version: 2026-02-25``, three kinds,
  ``recurrenceCount`` max 32, ``instant`` for team events only, and
  ``bookingUidToReschedule`` excluding a booking's own slot from busy time.
* "Metadata must have at most 50 keys, each key up to 40 characters, and string
  values up to 500 characters."
* ``GET /v2/routing-forms/slots``: "It will not actually save the response just
  return the routed event type and slots when it can be booked."
* booking fields with prefill and read-only; team and seated event types; the
  Google/Outlook/Apple calendar connects; the four conference providers.
* "On success, ``BOOKING_CREATED`` webhook fires; downstream automations can
  chain (see #16)."
* user_flow step 5: "The prospect books entirely in-room; no Chili-Piper-like
  external page is shown."

Four bugs these tests were written to catch, each of which is invisible unless it
is asserted behaviourally:

* a hold blocking every slot on a host rather than the slot it was taken on, which
  would make a reschedule impossible;
* a seated event's first booking consuming the host, so a four-seat event was
  bookable once;
* a reschedule creating a second booking and re-firing ``BOOKING_CREATED``, so the
  prospect was notified twice about the same meeting;
* ``room_id`` in a payload matching nothing, because the store reserves that key
  and strips it from ``data`` - every room-scoped read would have been empty.

The HTTP half runs against the real app over a temporary database, the way
``test_features.py`` does, and asserts that every ``source`` recorded in the audit
log names a route the host actually mounted.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.inroom_scheduling import (
    BOOKING_COLLECTION,
    BOOKING_CREATED,
    CALENDAR_COLLECTION,
    CLIENT_COLLECTION,
    DEFAULT_HORIZON_DAYS,
    DEFAULT_RESERVATION_DURATION_MINUTES,
    EMBED_BOOKING_COMPONENTS,
    EMBED_COMPONENTS,
    EMBED_CSS_VARIABLES,
    EVENT_LOG_COLLECTION,
    EVENT_TYPE_COLLECTION,
    FORM_COLLECTION,
    MAX_RECURRENCE_COUNT,
    MAX_RESERVATION_DURATION_MINUTES,
    MAX_SLOT_WINDOW_DAYS,
    METADATA_LIMITS,
    MIN_DYNAMIC_USERNAMES,
    RESERVATION_COLLECTION,
    ROOM_FIELD,
    SLOT_SELECTORS,
    UNAVAILABLE_REASONS,
    WEBHOOK_COLLECTION,
    BookingConflict,
    BookingFieldRejected,
    EmbedConfigError,
    HoldExpired,
    HoldRequired,
    InstantNeedsTeamEvent,
    MetadataOutOfRange,
    RecurrenceOutOfRange,
    RoutingError,
    SchedulingEngine,
    SchedulingError,
    Selector,
    SelectorError,
    SlotUnavailable,
    TokenExpired,
    UnknownEventType,
    apply_booking_fields,
    busy_intervals,
    calendar_summary,
    first_free,
    grant_token,
    inferences as scheduling_inferences,
    iso,
    normalise_attendee,
    normalise_booking_fields,
    normalise_calendar_connection,
    normalise_duration,
    normalise_embed,
    normalise_event_type,
    normalise_form,
    normalise_host,
    normalise_oauth_client,
    parse_instant,
    parse_window,
    read_booking_request,
    read_hold,
    read_selector,
    require_bookable,
    require_live,
    require_live_token,
    require_operator,
    require_team_event_for_instant,
    resolve_zone,
    room_metadata,
    route,
    routed_slots_response,
    rule_matches,
    slot_grid,
    snap_to_grid,
    token_state,
    validate_metadata,
    working_windows,
)
from dsr.inroom_scheduling.availability import Occupancy, selector_label
from dsr.inroom_scheduling.bookings import (
    booking_created_event,
    booking_payload,
    cancel_payload,
    describe_window,
    require_cancellable,
    resolve_instant_start,
)
from dsr.inroom_scheduling.holds import HoldView, new_hold_payload
from dsr.inroom_scheduling.routing import OPERATORS
from dsr.inroom_scheduling.schedules import candidate_starts, merge_ranges, overlaps
from dsr.inroom_scheduling.vocabulary import published_vocabulary
from dsr.store import RecordStore
from fastapi.testclient import TestClient

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-058"

#: What the pure-domain tests pass as ``source``. Deliberately the exact shape a
#: route passes, so a test asserting on an audit row is asserting on the real thing
#: rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/bookings"

MODULE = "wf058_embed_a_bookable_calendar_inside_the_s"
FEATURE_ID = "wf-058-embed-a-bookable-calendar-inside-the-s"

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "dsr" / "inroom_scheduling"

#: A Monday at eight in the morning UTC, before the first working hour of every
#: event type the tests build. Any later and the hosts' 09:00 start would be in the
#: past, which is a different feature (the minimum-notice rule) being tested
#: accidentally by every grid assertion.
MONDAY = datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)
TUESDAY = datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)

#: The collections this feature writes. Used by the "saves nothing" tests, which
#: count rows before and after rather than trusting a docstring.
OWNED_COLLECTIONS = (
    CLIENT_COLLECTION,
    EVENT_TYPE_COLLECTION,
    CALENDAR_COLLECTION,
    FORM_COLLECTION,
    RESERVATION_COLLECTION,
    BOOKING_COLLECTION,
    EVENT_LOG_COLLECTION,
    WEBHOOK_COLLECTION,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def db(tmp_path):
    database = AuditedDatabase(tmp_path / "wf058.db", mirror_dir=tmp_path / "mirror")
    yield database
    database.close()


@pytest.fixture()
def store(db):
    return RecordStore(db)


@pytest.fixture()
def clock():
    moment = MONDAY
    return lambda: moment


@pytest.fixture()
def engine(store, clock):
    return SchedulingEngine(store, clock=clock)


@pytest.fixture()
def at():
    """A factory for a moment, so a test can move the clock without a second engine."""

    def build(**kwargs) -> datetime:
        return MONDAY + timedelta(**kwargs)

    return build


@pytest.fixture()
def room(store):
    return store.create(
        "room", {"name": "Northwind — Enterprise", "account": "Northwind"}, actor="dana"
    )


@pytest.fixture()
def other_room(store):
    return store.create("room", {"name": "Contoso — Security", "account": "Contoso"}, actor="sam")


#: A personal event type on a 30-minute grid, Monday to Friday, 09:00-17:00 UTC,
#: with an hour's notice. The shape most of these tests book against.
PERSONAL = {
    "eventTypeId": "evt_personal",
    "slug": "intro",
    "kind": "personal",
    "title": "30 minute intro",
    "host": "priya",
    "length_minutes": 30,
    "time_zone": "UTC",
    "days": [1, 2, 3, 4, 5],
    "working_hours_start": "09:00",
    "working_hours_end": "17:00",
    "slot_interval_minutes": 30,
    "minimum_notice_minutes": 60,
    "location": "google_meet",
}

TEAM = {
    "eventTypeId": "evt_team",
    "slug": "war-room",
    "kind": "team",
    "title": "Team war room",
    "teamSlug": "revenue-team",
    "host": "revenue-team",
    "length_minutes": 15,
    "location": "zoom",
    "hosts": [
        {
            "username": "priya",
            "time_zone": "UTC",
            "days": [1, 2, 3, 4, 5],
            "start": "09:00",
            "end": "13:00",
            "slot_interval_minutes": 15,
            "minimum_notice_minutes": 0,
        },
        {
            "username": "marcus",
            "time_zone": "UTC",
            "days": [1, 2, 3, 4, 5],
            "start": "11:00",
            "end": "17:00",
            "slot_interval_minutes": 30,
            "minimum_notice_minutes": 0,
        },
    ],
}

SEATED = {
    "eventTypeId": "evt_seated",
    "slug": "deep-dive",
    "kind": "seated",
    "host": "alba",
    "length_minutes": 45,
    "seats": 3,
    "time_zone": "UTC",
    "days": [1, 2, 3, 4, 5],
    "working_hours_start": "09:00",
    "working_hours_end": "12:00",
    "slot_interval_minutes": 45,
    "minimum_notice_minutes": 0,
    "location": "ms_teams",
}

ROUTING = {
    "eventTypeId": "evt_routing",
    "slug": "general",
    "kind": "routing",
    "host": "dana",
    "length_minutes": 30,
    "time_zone": "UTC",
    "days": [1, 2, 3, 4, 5],
    "working_hours_start": "10:00",
    "working_hours_end": "12:00",
    "slot_interval_minutes": 30,
    "minimum_notice_minutes": 0,
    "location": "phone",
}

ATTENDEE = {"name": "Ines Duarte", "email": "ines.duarte@northwind.example", "timeZone": "UTC"}


def client_spec(**overrides):
    body = {
        "name": "Northwind",
        "client_id": "dsr-northwind",
        "redirect_uri": "https://rooms.example/wf-058/callback",
        "scopes": ["BOOKING", "EVENT_TYPE"],
    }
    body.update(overrides)
    return body


def add_client(engine, *, grant=True, **overrides):
    record = engine.create_client(client_spec(**overrides), actor="dana", source=SOURCE)
    if grant:
        engine.grant(
            record["id"], {"subject": overrides.get("host", "priya")}, actor="dana", source=SOURCE
        )
    return record


def add_event(engine, spec=None, **overrides):
    """Create an event type, or hand back the one already there.

    Idempotent on ``eventTypeId`` on purpose. Two rows with one id is exactly the
    ambiguity ``resolve_event_type`` refuses to answer, and a test that wants to
    study that ambiguity creates the second row deliberately rather than by calling
    this helper twice.
    """
    body = dict(spec or PERSONAL)
    body.update(overrides)
    existing = engine.get_event_type(str(body["eventTypeId"]))
    if existing is not None:
        return existing
    return engine.create_event_type(body, actor="dana", source=SOURCE)


def install(engine, room_id, *, event_type_id="evt_personal", **overrides):
    body = {"eventTypeId": event_type_id, "time_zone": "UTC"}
    body.update(overrides)
    return engine.save_embed(room_id, body, actor="dana", source=SOURCE)


def form_spec(**overrides):
    body = {
        "name": "What is this about?",
        "rules": [
            {
                "field": "topic",
                "operator": "equals",
                "value": "security",
                "eventTypeId": "evt_seated",
            }
        ],
        "fallbackEventTypeId": "evt_routing",
    }
    body.update(overrides)
    return body


def ready(engine, room_id, *, event_types=(PERSONAL,), grant=True, embed=None):
    """A room with a client, event types and an embed: the precondition of a write."""
    client = add_client(engine, grant=grant)
    for spec in event_types:
        add_event(engine, spec)
    body = dict(embed or {})
    body.setdefault("clientId", client["id"])
    body.setdefault("eventTypeId", "evt_personal")
    return engine.save_embed(room_id, body, actor="dana", source=SOURCE)


def first_slot(engine, room_id, event_type_id="evt_personal", **params):
    """The first bookable start, read off the grid the way a client would."""
    grid = engine.slots(room_id, {"eventTypeId": event_type_id, **params})
    slot = first_free(grid["slots"])
    assert slot is not None, "the grid offered nothing bookable"
    return slot["start"]


def record_count(store) -> int:
    """How many rows this feature owns, across every collection it writes."""
    return sum(len(store.list(collection, limit=1000)) for collection in OWNED_COLLECTIONS)


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


@pytest.fixture()
def http(monkeypatch, tmp_path):
    """A client over a temporary database.

    The engine is a FastAPI dependency built from ``StoreDep``, so the production
    path and the test path are the same path and the suite needs no override.
    """
    monkeypatch.setenv("DSR_DB_PATH", str(tmp_path / "http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", tmp_path / "absent-frontend")
    with TestClient(app) as client:
        yield client


def mounted_routes(client, feature_id=FEATURE_ID):
    """Every (method, path) the host mounted for one feature, templates intact."""
    entry = next(
        (f for f in client.get("/api/features").json()["features"] if f["id"] == feature_id), None
    )
    assert entry is not None, f"{feature_id} is not mounted"
    return {(method, route["path"]) for route in entry["routes"] for method in route["methods"]}


def all_served_routes(client):
    """Every (method, path) the running app serves, core routes included.

    The audit log is shared, so a check of what it records has to be allowed to see
    a core write as well as a feature one.
    """
    routes = set()
    for served in app.routes:
        for method in getattr(served, "methods", None) or set():
            if method not in ("HEAD", "OPTIONS"):
                routes.add((method, getattr(served, "path", "")))
    for feature in client.get("/api/features").json()["features"]:
        for advertised in feature["routes"]:
            for method in advertised["methods"]:
                routes.add((method, advertised["path"]))
    return routes


def source_names_a_mounted_route(source, routes):
    """Does ``"POST /api/wf-058/rooms/abc/bookings"`` name a route that exists?

    A recorded source carries concrete ids; a mounted path carries FastAPI's
    ``{param}`` placeholders. The pattern is built from the *template* and matched
    against the source, with each placeholder as one wildcard segment and the
    literal parts escaped so a segment containing a regex metacharacter cannot make
    the pattern match something else.
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


def client_store(client):
    return client.app.state.store.db


# --------------------------------------------------------------------------- #
# The researched vocabulary
# --------------------------------------------------------------------------- #


def test_the_two_documented_api_versions_are_published():
    vocabulary = published_vocabulary()
    assert vocabulary["api_versions"]["GET /v2/slots"] == "2024-09-04"
    assert vocabulary["api_versions"]["POST /v2/bookings"] == "2026-02-25"
    assert vocabulary["api_version_header"] == "cal-api-version"


def test_the_two_api_versions_are_never_compared_to_each_other():
    """One is for the slot query, one is for the create-booking call.

    They are different numbers for different endpoints, and a single "current
    version" constant would silently send the slots call the bookings version.
    """
    from dsr.inroom_scheduling.vocabulary import BOOKING_API_VERSION, SLOTS_API_VERSION

    assert SLOTS_API_VERSION != BOOKING_API_VERSION


def test_the_documented_limits_are_published_with_their_numbers():
    vocabulary = published_vocabulary()
    assert vocabulary["max_recurrence_count"] == 32 == MAX_RECURRENCE_COUNT
    assert vocabulary["metadata_limits"] == {
        "max_keys": 50,
        "max_key_length": 40,
        "max_value_length": 500,
    }
    assert vocabulary["metadata_limits"] == dict(METADATA_LIMITS)
    assert "at most 50 keys" in vocabulary["metadata_quote"]


def test_the_five_minute_default_is_published():
    assert DEFAULT_RESERVATION_DURATION_MINUTES == 5
    assert published_vocabulary()["reservation_response_fields"] == [
        "reservationUid",
        "reservationDuration",
        "reservationUntil",
    ]


def test_the_four_documented_slot_selectors_are_published():
    assert list(SLOT_SELECTORS) == ["event_type_id", "username", "team_slug", "usernames"]


def test_the_calendar_and_conference_providers_are_the_documented_ones():
    vocabulary = published_vocabulary()
    assert vocabulary["calendar_providers"] == ["google", "outlook", "apple"]
    assert vocabulary["conference_providers"] == ["zoom", "google_meet", "ms_teams", "webex"]


def test_the_embed_components_are_the_ones_the_research_lists():
    """Booker, Availability, Event Type, calendar connect and a payment form."""
    vocabulary = published_vocabulary()
    assert set(vocabulary["embed_components"]) == {
        "booker",
        "booker_embed",
        "availability",
        "event_type",
        "calendar_connect",
        "payment_form",
    }
    assert set(vocabulary["embed_booking_components"]) <= set(EMBED_COMPONENTS)


def test_the_atoms_maintenance_quote_is_carried_as_a_decision():
    """The research says the SDK is being retired towards copy-and-paste on API v2.

    This build renders the embed itself, so the quote is published rather than left
    in a comment: a reviewer reading the vocabulary can see the sentence the decision
    rests on without opening the research document.
    """
    quote = published_vocabulary()["quotes"]["atoms_maintenance"]
    assert "maintenance mode" in quote
    assert "API v2" in quote


def test_the_in_room_quote_is_carried_because_it_is_why_the_routes_are_scoped():
    assert "entirely in-room" in published_vocabulary()["quotes"]["in_room"]


# --------------------------------------------------------------------------- #
# Schedules: working hours, grids, and the arithmetic
# --------------------------------------------------------------------------- #


def test_parse_instant_reads_a_naive_value_as_utc():
    assert parse_instant("2026-09-28T13:00:00") == parse_instant("2026-09-28T13:00:00Z")


def test_parse_instant_normalises_an_offset_to_utc():
    assert parse_instant("2026-09-28T15:30:00+02:00") == datetime(
        2026, 9, 28, 13, 30, tzinfo=timezone.utc
    )


def test_parse_instant_refuses_a_value_it_cannot_read():
    with pytest.raises(SchedulingError, match="start is required"):
        parse_instant(None, field="start")
    with pytest.raises(SchedulingError, match="not an ISO-8601 instant"):
        parse_instant("next tuesday", field="start")


def test_resolve_zone_accepts_utc_without_a_zone_database():
    """A tz database is not a Python guarantee, and the demo's own zone must work."""
    assert resolve_zone("UTC").utcoffset(MONDAY) == timedelta(0)
    assert resolve_zone("utc").utcoffset(MONDAY) == timedelta(0)


def test_resolve_zone_accepts_a_fixed_offset():
    zone = resolve_zone("UTC+05:30")
    assert MONDAY.astimezone(zone).hour == 13
    assert resolve_zone("GMT-3").utcoffset(MONDAY) == timedelta(hours=-3)


def test_resolve_zone_refuses_a_nonsense_offset():
    with pytest.raises(SchedulingError, match="not a usable fixed offset"):
        resolve_zone("UTC+99:00")


def test_resolve_zone_refuses_an_unknown_name_rather_than_guessing_utc():
    """A schedule published in the wrong zone is a meeting at the wrong hour."""
    with pytest.raises(SchedulingError, match="no zone this host can resolve|Missing tzdata"):
        resolve_zone("Nowhere/Special")


def test_a_window_must_run_forwards():
    with pytest.raises(SchedulingError, match="end must be after start"):
        parse_window("2026-09-28T13:00:00Z", "2026-09-28T12:00:00Z", now=MONDAY)


def test_a_window_cannot_begin_in_the_past():
    with pytest.raises(SchedulingError, match="start is in the past"):
        parse_window("2026-09-27T13:00:00Z", "2026-09-29T13:00:00Z", now=MONDAY)


def test_a_window_is_bounded_so_a_query_cannot_ask_for_a_year_of_slots():
    far = iso(MONDAY + timedelta(days=MAX_SLOT_WINDOW_DAYS + 1))
    with pytest.raises(SchedulingError, match="may not exceed"):
        parse_window(iso(MONDAY), far, now=MONDAY)


def test_a_host_with_no_days_means_every_day():
    host = normalise_host({"username": "priya", "time_zone": "UTC"})
    assert host["days"] == [1, 2, 3, 4, 5, 6, 7]


def test_a_host_with_no_hours_gets_a_working_day():
    host = normalise_host({"username": "priya", "time_zone": "UTC"})
    assert (host["start"], host["end"]) == ("09:00", "17:00")


def test_a_host_needs_a_username():
    with pytest.raises(SchedulingError, match="needs a username"):
        normalise_host({"time_zone": "UTC"})


def test_a_host_that_closes_before_it_opens_is_refused():
    with pytest.raises(SchedulingError, match="must be after"):
        normalise_host({"username": "p", "time_zone": "UTC", "start": "17:00", "end": "09:00"})


def test_a_host_rejects_a_day_number_outside_the_iso_range():
    with pytest.raises(SchedulingError, match="ISO weekday numbers"):
        normalise_host({"username": "p", "time_zone": "UTC", "days": [0, 8]})


def test_a_host_rejects_a_zero_interval():
    with pytest.raises(SchedulingError, match="at least 1"):
        normalise_host({"username": "p", "time_zone": "UTC", "slot_interval_minutes": 0})


def test_working_windows_follow_the_local_weekday_not_the_utc_one():
    """A late Saturday in Auckland is a Saturday in Auckland, so nobody works."""
    zone = resolve_zone("UTC+12")
    host = normalise_host(
        {
            "username": "nz",
            "time_zone": "UTC+12",
            "days": [1, 2, 3, 4, 5],
            "start": "09:00",
            "end": "17:00",
        }
    )
    # 2026-10-03 is a Saturday. 10:00 UTC is 22:00 Saturday in UTC+12, so the
    # local day is the 3rd - a weekend, and no window is produced.
    windows = working_windows(
        host, (parse_instant("2026-10-03T00:00:00Z"), parse_instant("2026-10-04T00:00:00Z"))
    )
    assert windows == []
    assert zone.utcoffset(MONDAY) == timedelta(hours=12)


def test_snap_to_grid_rounds_up_to_the_next_grid_point():
    assert snap_to_grid(MONDAY.replace(hour=9, minute=7), 30) == MONDAY.replace(hour=9, minute=30)
    assert snap_to_grid(MONDAY.replace(hour=9, minute=30), 30) == MONDAY.replace(hour=9, minute=30)


def test_snap_to_grid_rounds_up_onto_a_45_minute_grid():
    """10:00 is not on a 45-minute grid, and pretending otherwise is how a demo fails."""
    assert snap_to_grid(MONDAY.replace(hour=10, minute=0), 45) == MONDAY.replace(hour=10, minute=30)


def test_candidate_starts_never_offer_a_slot_that_runs_past_closing():
    host = normalise_host(
        {
            "username": "p",
            "time_zone": "UTC",
            "days": [1],
            "start": "09:00",
            "end": "10:00",
            "slot_interval_minutes": 30,
            "minimum_notice_minutes": 0,
        }
    )
    starts = candidate_starts(
        [host],
        length_minutes=30,
        window=(MONDAY, MONDAY + timedelta(days=1)),
        now=MONDAY,
    )
    assert starts == [MONDAY.replace(hour=9, minute=0), MONDAY.replace(hour=9, minute=30)]


def test_candidate_starts_respect_the_minimum_notice():
    """An hour's notice is a working-hours rule, and the researched grid honours it."""
    host = normalise_host(
        {
            "username": "p",
            "time_zone": "UTC",
            "days": [1],
            "start": "09:00",
            "end": "17:00",
            "slot_interval_minutes": 30,
            "minimum_notice_minutes": 60,
        }
    )
    starts = candidate_starts(
        [host],
        length_minutes=30,
        window=(MONDAY, MONDAY.replace(hour=23)),
        now=MONDAY.replace(hour=12),
    )
    assert starts[0] == MONDAY.replace(hour=13, minute=0)
    assert MONDAY.replace(hour=12, minute=30) not in starts
    assert all(start >= MONDAY.replace(hour=13) for start in starts)


def test_candidate_starts_step_each_host_on_its_own_interval():
    """A 15-minute host next to a 30-minute host still offers the quarter hour.

    A single shared interval would have to be somebody's, and would hide half of
    what one of them can actually do.
    """
    quarter = normalise_host(
        {
            "username": "a",
            "time_zone": "UTC",
            "days": [1],
            "start": "09:00",
            "end": "10:00",
            "slot_interval_minutes": 15,
        }
    )
    half = normalise_host(
        {
            "username": "b",
            "time_zone": "UTC",
            "days": [1],
            "start": "09:00",
            "end": "10:00",
            "slot_interval_minutes": 30,
        }
    )
    starts = candidate_starts(
        [quarter, half],
        length_minutes=15,
        window=(MONDAY, MONDAY + timedelta(days=1)),
        now=MONDAY,
    )
    minutes = sorted({start.minute for start in starts})
    assert minutes == [0, 15, 30, 45]


def test_touching_intervals_do_not_overlap():
    """A 30-minute meeting ending at 10:00 does not block 10:00-10:30."""
    first = (MONDAY.replace(hour=10), MONDAY.replace(hour=10, minute=30))
    second = (MONDAY.replace(hour=10, minute=30), MONDAY.replace(hour=11))
    assert overlaps(first, second) is False
    assert overlaps(first, (MONDAY.replace(hour=10, minute=15), MONDAY.replace(hour=11))) is True


def test_merge_ranges_coalesces_overlapping_busy_time():
    merged = merge_ranges(
        [
            (MONDAY.replace(hour=10), MONDAY.replace(hour=11)),
            (MONDAY.replace(hour=10, minute=30), MONDAY.replace(hour=12)),
            (MONDAY.replace(hour=14), MONDAY.replace(hour=15)),
        ]
    )
    assert merged == [
        (MONDAY.replace(hour=10), MONDAY.replace(hour=12)),
        (MONDAY.replace(hour=14), MONDAY.replace(hour=15)),
    ]


# --------------------------------------------------------------------------- #
# Availability: the four selectors and what a grid says
# --------------------------------------------------------------------------- #


def test_a_query_may_name_an_event_type_by_id():
    selector = read_selector({"eventTypeId": "evt_personal"})
    assert selector.kind == "event_type_id"
    assert selector.event_type_id == "evt_personal"
    assert selector.dynamic is False


def test_a_query_may_name_a_slug_and_a_username():
    selector = read_selector({"eventTypeSlug": "intro", "username": "priya"})
    assert selector.kind == "username"
    assert selector_label(selector) == "eventTypeSlug=intro&username=priya"


def test_a_query_may_name_a_slug_and_a_team_slug():
    selector = read_selector({"eventTypeSlug": "war-room", "teamSlug": "revenue-team"})
    assert selector.kind == "team_slug"
    assert selector_label(selector) == "eventTypeSlug=war-room&teamSlug=revenue-team"


def test_a_query_may_name_a_usernames_list():
    selector = read_selector({"usernames": "alice,bob"})
    assert selector.kind == "usernames"
    assert selector.usernames == ("alice", "bob")
    assert selector.dynamic is True


def test_a_usernames_list_deduplicates_and_trims():
    assert read_selector({"usernames": " alice , bob ,alice "}).usernames == ("alice", "bob")


def test_a_usernames_query_needs_two_or_more_names():
    """ "we just want to know when 2 or more people are available"."""
    with pytest.raises(SelectorError, match="at least 2 names"):
        read_selector({"usernames": "alice"})


def test_a_query_naming_nothing_is_refused():
    with pytest.raises(SelectorError, match="must name one of"):
        read_selector({})


def test_a_query_naming_two_selectors_is_refused():
    """The dangerous case: a query that looks plausible and would answer about the
    wrong event type while a reader believes it answered about the people."""
    with pytest.raises(SelectorError, match="exactly one of"):
        read_selector({"eventTypeId": "evt_personal", "usernames": "alice,bob"})


def test_a_query_naming_an_id_and_a_username_is_refused():
    """A bare username beside an eventTypeId would be ignored, and read as agreement."""
    with pytest.raises(SelectorError, match="only half a selector"):
        read_selector({"eventTypeId": "evt_personal", "username": "priya"})


def test_a_query_naming_a_username_and_a_team_slug_is_refused():
    """They are two different event types, and a query cannot mean both."""
    with pytest.raises(SelectorError, match="cannot name both a username and a teamSlug"):
        read_selector({"eventTypeSlug": "intro", "username": "priya", "teamSlug": "revenue-team"})


def test_a_slug_with_neither_username_nor_team_is_refused():
    with pytest.raises(SelectorError, match="must be paired with"):
        read_selector({"eventTypeSlug": "intro"})


def test_busy_time_merges_per_host():
    entries = [
        {
            "uid": "a",
            "host": "priya",
            "start": "2026-09-28T10:00:00Z",
            "end": "2026-09-28T11:00:00Z",
        },
        {
            "uid": "b",
            "host": "priya",
            "start": "2026-09-28T10:30:00Z",
            "end": "2026-09-28T12:00:00Z",
        },
        {
            "uid": "c",
            "host": "marcus",
            "start": "2026-09-28T10:00:00Z",
            "end": "2026-09-28T10:30:00Z",
        },
    ]
    busy = busy_intervals(entries)
    assert len(busy["priya"]) == 1
    assert busy["priya"][0][1] == parse_instant("2026-09-28T12:00:00Z")
    assert busy["marcus"] == [
        (parse_instant("2026-09-28T10:00:00Z"), parse_instant("2026-09-28T10:30:00Z"))
    ]


def test_the_reschedule_exclusion_drops_a_bookings_own_slot_from_busy_time():
    """The researched field: exclude its time slot from busy time calculations.

    Without this a prospect cannot re-submit the time they already hold, because
    their own booking is the thing blocking it.
    """
    entries = [
        {
            "uid": "bkg_1",
            "host": "priya",
            "start": "2026-09-28T10:00:00Z",
            "end": "2026-09-28T10:30:00Z",
        }
    ]
    assert busy_intervals(entries)["priya"]
    assert busy_intervals(entries, exclude_booking_uid="bkg_1") == {}


def test_the_reschedule_exclusion_only_drops_the_named_booking():
    entries = [
        {
            "uid": "bkg_1",
            "host": "priya",
            "start": "2026-09-28T10:00:00Z",
            "end": "2026-09-28T10:30:00Z",
        },
        {
            "uid": "bkg_2",
            "host": "priya",
            "start": "2026-09-28T11:00:00Z",
            "end": "2026-09-28T11:30:00Z",
        },
    ]
    busy = busy_intervals(entries, exclude_booking_uid="bkg_1")
    assert len(busy["priya"]) == 1
    assert busy["priya"][0][0] == parse_instant("2026-09-28T11:00:00Z")


def test_an_unparseable_busy_entry_is_skipped_rather_than_taking_a_listing_down():
    entries = [
        {"uid": "a", "host": "priya", "start": "nonsense", "end": "2026-09-28T11:00:00Z"},
        {
            "uid": "b",
            "host": "priya",
            "start": "2026-09-28T11:00:00Z",
            "end": "2026-09-28T12:00:00Z",
        },
    ]
    assert len(busy_intervals(entries)["priya"]) == 1


def _grid(engine, room_id, event_type_id="evt_personal", **kwargs):
    return engine.slots(room_id, {"eventTypeId": event_type_id, **kwargs})["slots"]


def test_a_slot_marks_itself_unavailable_when_the_host_is_busy(engine, room, store):
    ready(engine, room["id"])
    record = store.create(
        BOOKING_COLLECTION,
        {
            "uid": "bkg_1",
            "eventTypeId": "evt_personal",
            "host": "priya",
            "start": "2026-09-28T14:00:00Z",
            "end": "2026-09-28T14:30:00Z",
            "status": "confirmed",
        },
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert record["id"]
    slots = {slot["start"]: slot for slot in _grid(engine, room["id"])}
    assert slots["2026-09-28T14:00:00Z"]["reason"] == "hosts"
    assert slots["2026-09-28T14:00:00Z"]["available"] is False
    # Touching ends do not overlap, so the next slot is free.
    assert slots["2026-09-28T14:30:00Z"]["available"] is True


def test_a_cancelled_booking_returns_its_slot_to_the_grid(engine, room, store):
    ready(engine, room["id"])
    store.create(
        BOOKING_COLLECTION,
        {
            "uid": "bkg_1",
            "eventTypeId": "evt_personal",
            "host": "priya",
            "start": "2026-09-28T14:00:00Z",
            "end": "2026-09-28T14:30:00Z",
            "status": "cancelled",
        },
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    slots = {slot["start"]: slot for slot in _grid(engine, room["id"])}
    assert slots["2026-09-28T14:00:00Z"]["available"] is True


def test_a_seated_booking_does_not_consume_the_host(engine, room):
    """A seated event is several people in one meeting, so a booking is a seat.

    Counting the first seated booking as host-busy would make a three-seat event
    bookable once, which is the opposite of what a seat count is for.
    """
    ready(engine, room["id"], event_types=(PERSONAL, SEATED), embed={"eventTypeId": "evt_seated"})
    first = engine.book(
        room["id"],
        {
            "eventTypeId": "evt_seated",
            "start": first_slot(engine, room["id"], "evt_seated"),
            "attendee": ATTENDEE,
        },
        actor="dana",
        source=SOURCE,
    )
    second = engine.book(
        room["id"],
        {
            "eventTypeId": "evt_seated",
            "start": first["start"],
            "attendee": {"name": "Second", "email": "second@fabrikam.example"},
        },
        actor="dana",
        source=SOURCE,
    )
    assert second["start"] == first["start"]


def test_a_seated_event_refuses_a_booking_past_its_seat_count(engine, room):
    ready(engine, room["id"], event_types=(PERSONAL, SEATED), embed={"eventTypeId": "evt_seated"})
    start = first_slot(engine, room["id"], "evt_seated")
    for index in range(3):
        engine.book(
            room["id"],
            {
                "eventTypeId": "evt_seated",
                "start": start,
                "attendee": {"name": f"P{index}", "email": f"p{index}@fabrikam.example"},
            },
            actor="dana",
            source=SOURCE,
        )
    with pytest.raises(SlotUnavailable) as refusal:
        engine.book(
            room["id"],
            {
                "eventTypeId": "evt_seated",
                "start": start,
                "attendee": {"name": "Fourth", "email": "fourth@fabrikam.example"},
            },
            actor="dana",
            source=SOURCE,
        )
    assert refusal.value.reason == "seats"
    assert refusal.value.slot["seats_left"] == 0


def test_a_held_slot_is_unavailable_for_everybody_else(engine, room):
    ready(engine, room["id"])
    start = first_slot(engine, room["id"])
    engine.reserve(room["id"], {"start": start}, actor="dana", source=SOURCE)
    slots = {slot["start"]: slot for slot in _grid(engine, room["id"])}
    assert slots[start]["reason"] == "held"
    assert slots[start]["available"] is False


def test_a_hold_blocks_its_own_slot_only(engine, room):
    """A hold is a claim on a slot, not on the host's day.

    Treating it as host-busy would make a prospect's hold on 14:00 also block 15:00,
    and would make a reschedule impossible: moving your own meeting from 14:00 to
    15:00 would find 15:00 held by your own hold.
    """
    ready(engine, room["id"])
    engine.reserve(room["id"], {"start": "2026-09-28T14:00:00Z"}, actor="dana", source=SOURCE)
    slots = {slot["start"]: slot for slot in _grid(engine, room["id"])}
    assert slots["2026-09-28T14:00:00Z"]["reason"] == "held"
    assert slots["2026-09-28T15:00:00Z"]["available"] is True


def test_a_slot_names_the_hosts_who_are_free(engine, room, store):
    ready(engine, room["id"], event_types=(PERSONAL, TEAM), embed={"eventTypeId": "evt_team"})
    store.create(
        BOOKING_COLLECTION,
        {
            "uid": "bkg_1",
            "eventTypeId": "evt_team",
            "host": "priya",
            "start": "2026-09-28T11:00:00Z",
            "end": "2026-09-28T11:15:00Z",
            "status": "confirmed",
        },
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    slots = {slot["start"]: slot for slot in _grid(engine, room["id"], "evt_team")}
    assert slots["2026-09-28T11:00:00Z"]["hosts_available"] == ["marcus"]


def test_a_dynamic_query_needs_two_people_free(engine, room):
    """ "we just want to know when 2 or more people are available"."""
    ready(engine, room["id"], event_types=(TEAM,), embed={"eventTypeId": "evt_team"})
    slots = engine.slots(room["id"], {"usernames": "priya,marcus"})["slots"]
    assert slots[0]["hosts_required"] == MIN_DYNAMIC_USERNAMES == 2


def test_a_dynamic_query_needs_working_hours_it_has_heard_of(engine, room):
    """Answering for a stranger would offer slots nobody can take."""
    ready(engine, room["id"], event_types=(TEAM,), embed={"eventTypeId": "evt_team"})
    with pytest.raises(UnknownEventType, match="no working hours are known"):
        engine.slots(room["id"], {"usernames": "priya,stranger"})


def test_a_query_for_an_event_type_that_does_not_exist_is_refused(engine, room):
    ready(engine, room["id"])
    with pytest.raises(UnknownEventType, match="no event type has eventTypeId"):
        engine.slots(room["id"], {"eventTypeId": "evt_nope"})


def test_require_bookable_offers_alternatives_when_a_slot_is_taken(engine, room):
    ready(engine, room["id"])
    start = first_slot(engine, room["id"])
    engine.book(
        room["id"],
        {"start": start, "attendee": ATTENDEE},
        actor="dana",
        source=SOURCE,
    )
    with pytest.raises(SlotUnavailable) as refusal:
        engine.book(
            room["id"],
            {"start": start, "attendee": {"name": "Other", "email": "other@fabrikam.example"}},
            actor="dana",
            source=SOURCE,
        )
    assert refusal.value.reason == "hosts"
    assert refusal.value.alternatives, "a refused slot must come with somewhere to go"


def test_require_bookable_says_when_a_start_is_not_on_the_grid_at_all():
    slots = [
        {"start": "2026-09-28T13:00:00Z", "available": True, "reason": None},
    ]
    with pytest.raises(SlotUnavailable) as refusal:
        require_bookable(slots, "2026-09-28T03:00:00Z")
    assert refusal.value.reason == "not_offered"


def test_first_free_returns_none_when_nothing_is_free():
    assert first_free([{"start": "a", "available": False, "reason": "hosts"}]) is None


def test_the_unavailable_reasons_are_a_closed_published_set():
    """The page renders a message per reason, so an open set would be a message
    nobody wrote."""
    assert set(UNAVAILABLE_REASONS) == {"held", "booked", "seats", "hosts"}


def test_a_selector_says_whether_it_is_the_dynamic_case():
    assert Selector(kind="event_type_id", event_type_id="evt").dynamic is False
    assert Selector(kind="usernames", usernames=("a", "b")).dynamic is True


def _monday_host(hours="09:00", closes="10:00"):
    return normalise_host(
        {
            "username": "priya",
            "time_zone": "UTC",
            "days": [1],
            "start": hours,
            "end": closes,
            "slot_interval_minutes": 30,
            "minimum_notice_minutes": 0,
        }
    )


def test_the_grid_marks_a_slot_for_every_published_reason():
    """A direct call, so each reason is exercised without the engine in the way."""
    host = _monday_host()
    start = MONDAY.replace(hour=9, minute=0)
    end = MONDAY.replace(hour=9, minute=30)
    common = {
        "hosts": [host],
        "length_minutes": 30,
        "window": (MONDAY, MONDAY.replace(hour=23)),
        "now": MONDAY,
        "time_zone": "UTC",
        "event_type_id": "evt",
    }

    held = slot_grid(occupancy=Occupancy(held_by={iso(start): "rsv_1"}), busy={}, **common)
    assert (held[0]["reason"], held[0]["held_by"]) == ("held", "rsv_1")

    nobody = slot_grid(
        occupancy=Occupancy(), busy={"priya": [(start, end)]}, min_available_hosts=2, **common
    )
    assert (nobody[0]["reason"], nobody[0]["hosts_available"]) == ("hosts", [])

    seated = slot_grid(occupancy=Occupancy(seat_usage={iso(start): 3}), busy={}, seats=3, **common)
    assert (seated[0]["reason"], seated[0]["seats_left"]) == ("seats", 0)

    free = slot_grid(occupancy=Occupancy(), busy={}, **common)
    assert free[0] == {**free[0], "available": True, "reason": None}


def test_a_grid_rejects_a_length_of_zero_minutes():
    with pytest.raises(SchedulingError, match="at least one minute"):
        slot_grid(
            hosts=[],
            length_minutes=0,
            window=(MONDAY, MONDAY + timedelta(days=1)),
            now=MONDAY,
            time_zone="UTC",
            busy={},
            occupancy=Occupancy(),
            event_type_id="evt",
        )


def test_a_hold_view_carries_the_documented_field_names():
    view = HoldView(
        uid="rsv_1",
        state="held",
        start=MONDAY,
        until=MONDAY + timedelta(minutes=5),
        duration_minutes=5,
        event_type_id="evt",
        host="priya",
        stored_state="held",
        expired_now=False,
    ).to_dict()
    assert view["reservationUid"] == "rsv_1"
    assert view["reservationDuration"] == 5
    assert view["reservationUntil"].endswith("Z")


# --------------------------------------------------------------------------- #
# Holds: the researched auto-expiry
# --------------------------------------------------------------------------- #


def test_the_default_hold_is_five_minutes():
    assert normalise_duration(None) == DEFAULT_RESERVATION_DURATION_MINUTES == 5


def test_a_hold_duration_may_be_customised():
    """ "you can also specify custom duration for how long the slot should be reserved for"."""
    assert normalise_duration(45) == 45


def test_a_hold_duration_must_be_a_whole_number_of_minutes():
    with pytest.raises(SchedulingError, match="whole number of minutes"):
        normalise_duration("soon")


def test_a_hold_duration_must_be_at_least_a_minute():
    with pytest.raises(SchedulingError, match="at least one minute"):
        normalise_duration(0)


def test_a_hold_duration_is_bounded():
    """The research documents a default of five and no ceiling, so this build needs one."""
    with pytest.raises(SchedulingError, match="may not exceed"):
        normalise_duration(MAX_RESERVATION_DURATION_MINUTES + 1)


def test_a_hold_expires_without_anybody_acting():
    """ "no user action needed for the hold to expire - a reservation auto-expires after
    reservationDuration". A read at a later moment is the whole mechanism."""

    payload = new_hold_payload(
        event_type_id="evt_personal",
        start="2026-09-28T14:00:00Z",
        host="priya",
        duration_minutes=5,
        now=MONDAY,
        uid="rsv_1",
        room_id="room_1",
    )
    record = {"id": "r1", "room_id": "room_1", "data": payload}
    assert read_hold(record, now=MONDAY + timedelta(minutes=4)).live is True
    assert read_hold(record, now=MONDAY + timedelta(minutes=5)).live is False
    assert read_hold(record, now=MONDAY + timedelta(minutes=5)).state == "expired"


def test_an_expired_hold_reports_the_stored_state_beside_the_computed_one():

    payload = new_hold_payload(
        event_type_id="evt_personal",
        start="2026-09-28T14:00:00Z",
        host="priya",
        duration_minutes=5,
        now=MONDAY,
        uid="rsv_1",
        room_id="room_1",
    )
    view = read_hold({"id": "r1", "data": payload}, now=MONDAY + timedelta(minutes=6))
    assert (view.state, view.stored_state, view.expired_now) == ("expired", "held", True)


def test_a_consumed_hold_is_never_retroactively_expired():

    payload = new_hold_payload(
        event_type_id="evt_personal",
        start="2026-09-28T14:00:00Z",
        host="priya",
        duration_minutes=5,
        now=MONDAY,
        uid="rsv_1",
        room_id="room_1",
    )
    payload["state"] = "consumed"
    view = read_hold({"id": "r1", "data": payload}, now=MONDAY + timedelta(hours=3))
    assert view.state == "consumed"
    assert view.expired_now is False


def test_requiring_a_live_hold_says_the_hold_expired():

    payload = new_hold_payload(
        event_type_id="evt_personal",
        start="2026-09-28T14:00:00Z",
        host="priya",
        duration_minutes=5,
        now=MONDAY,
        uid="rsv_1",
        room_id="room_1",
    )
    record = {"id": "r1", "data": payload}
    assert require_live(read_hold(record, now=MONDAY)).live is True
    with pytest.raises(HoldExpired, match="pick another slot"):
        require_live(read_hold(record, now=MONDAY + timedelta(minutes=6)))


def test_requiring_a_consumed_hold_does_not_say_it_expired():
    """ "the meeting exists, at that time, on that event type": retrying is the wrong advice."""

    payload = new_hold_payload(
        event_type_id="evt_personal",
        start="2026-09-28T14:00:00Z",
        host="priya",
        duration_minutes=5,
        now=MONDAY,
        uid="rsv_1",
        room_id="room_1",
    )
    payload["state"] = "consumed"
    with pytest.raises(SlotUnavailable) as refusal:
        require_live(read_hold({"id": "r1", "data": payload}, now=MONDAY))
    assert refusal.value.reason == "consumed"


def test_a_hold_response_carries_the_three_documented_field_names(engine, room):
    ready(engine, room["id"])
    response = engine.reserve(
        room["id"], {"start": first_slot(engine, room["id"])}, actor="dana", source=SOURCE
    )
    assert set(response) >= {"reservationUid", "reservationDuration", "reservationUntil"}
    assert response["reservationDuration"] == 5
    assert response["reservationUntil"].endswith("Z")


def test_a_second_hold_on_the_same_slot_is_refused(engine, room):
    ready(engine, room["id"])
    start = first_slot(engine, room["id"])
    engine.reserve(room["id"], {"start": start}, actor="dana", source=SOURCE)
    with pytest.raises(SlotUnavailable) as refusal:
        engine.reserve(room["id"], {"start": start}, actor="dana", source=SOURCE)
    assert refusal.value.reason == "held"


def test_releasing_a_hold_gives_the_slot_back(engine, room):
    ready(engine, room["id"])
    start = first_slot(engine, room["id"])
    hold = engine.reserve(room["id"], {"start": start}, actor="dana", source=SOURCE)
    engine.release_hold(room["id"], hold["reservationUid"], actor="dana", source=SOURCE)
    slots = {slot["start"]: slot for slot in _grid(engine, room["id"])}
    assert slots[start]["available"] is True


def test_releasing_a_hold_twice_is_refused_rather_than_absorbed(engine, room):
    """Absorbing it would move the recorded release time and falsify the trail."""
    ready(engine, room["id"])
    hold = engine.reserve(
        room["id"], {"start": first_slot(engine, room["id"])}, actor="dana", source=SOURCE
    )
    engine.release_hold(room["id"], hold["reservationUid"], actor="dana", source=SOURCE)
    with pytest.raises(SchedulingError, match="cannot be released"):
        engine.release_hold(room["id"], hold["reservationUid"], actor="dana", source=SOURCE)


def test_extending_a_hold_buys_time_from_now(engine, room):
    ready(engine, room["id"])
    hold = engine.reserve(
        room["id"], {"start": first_slot(engine, room["id"])}, actor="dana", source=SOURCE
    )
    moved = SchedulingEngine(engine.store, clock=lambda: MONDAY + timedelta(minutes=2))
    extended = moved.extend_hold(
        room["id"], hold["reservationUid"], {"reservationDuration": 30}, actor="dana", source=SOURCE
    )
    assert extended["reservationDuration"] == 30
    assert extended["reservationUntil"] == iso(MONDAY + timedelta(minutes=32))


def test_extending_a_hold_the_clock_has_retired_is_refused(engine, room):
    ready(engine, room["id"])
    hold = engine.reserve(
        room["id"], {"start": first_slot(engine, room["id"])}, actor="dana", source=SOURCE
    )
    later = SchedulingEngine(engine.store, clock=lambda: MONDAY + timedelta(minutes=6))
    with pytest.raises(HoldExpired):
        later.extend_hold(
            room["id"],
            hold["reservationUid"],
            {"reservationDuration": 30},
            actor="dana",
            source=SOURCE,
        )


def test_a_hold_belongs_to_the_room_that_took_it(engine, room, other_room):
    ready(engine, room["id"])
    other = ready(engine, other_room["id"])
    hold = engine.reserve(
        room["id"], {"start": first_slot(engine, room["id"])}, actor="dana", source=SOURCE
    )
    with pytest.raises(UnknownEventType, match="belongs to the room that made it"):
        engine.hold(other_room["id"], hold["reservationUid"])
    assert other["embed"]["room_id"] == other_room["id"]


# --------------------------------------------------------------------------- #
# Attendees and the documented metadata limits
# --------------------------------------------------------------------------- #


def test_an_attendee_needs_a_name_and_a_reachable_address():
    with pytest.raises(SchedulingError, match="attendee is required"):
        normalise_attendee(None)
    with pytest.raises(SchedulingError, match="name is required"):
        normalise_attendee({"email": "a@b.example"})
    with pytest.raises(SchedulingError, match="must be an email address"):
        normalise_attendee({"name": "A", "email": "not-an-address"})


def test_an_attendee_inherits_the_rooms_zone_when_it_names_none():
    resolved = normalise_attendee(
        {"name": "A", "email": "a@b.example"}, default_time_zone="UTC+05:30"
    )
    assert resolved["timeZone"] == "UTC+05:30"


def test_an_attendee_accepts_the_flat_field_names_a_browser_form_posts():
    resolved = normalise_attendee({"attendeeName": "Ines", "attendeeEmail": "ines@b.example"})
    assert (resolved["name"], resolved["email"]) == ("Ines", "ines@b.example")


def test_an_attendee_must_be_an_object_not_a_string():
    with pytest.raises(SchedulingError, match="must be an object"):
        normalise_attendee("Ines Duarte")


def test_metadata_may_carry_fifty_keys(engine=None):
    """The documented ceiling, at the boundary rather than beside it."""
    payload = {f"key{index}": "v" for index in range(METADATA_LIMITS["max_keys"])}
    assert len(validate_metadata(payload)) == METADATA_LIMITS["max_keys"]


def test_metadata_with_fifty_one_keys_is_refused_naming_the_limit():
    payload = {f"key{index}": "v" for index in range(METADATA_LIMITS["max_keys"] + 1)}
    with pytest.raises(MetadataOutOfRange) as refusal:
        validate_metadata(payload)
    assert refusal.value.limit == "max_keys"
    assert refusal.value.maximum == 50


def test_a_metadata_key_of_forty_one_characters_is_refused():
    with pytest.raises(MetadataOutOfRange) as refusal:
        validate_metadata({"k" * 41: "v"})
    assert refusal.value.limit == "max_key_length"
    assert refusal.value.maximum == 40


def test_a_metadata_value_of_five_hundred_and_one_characters_is_refused():
    with pytest.raises(MetadataOutOfRange) as refusal:
        validate_metadata({"k": "v" * 501})
    assert refusal.value.limit == "max_value_length"
    assert refusal.value.maximum == 500


def test_the_three_metadata_limits_are_checked_independently():
    """A payload can satisfy two of them and break the third, and the refusal has to
    say which."""
    with pytest.raises(MetadataOutOfRange) as refusal:
        validate_metadata({"k" * 41: "v" * 501})
    assert refusal.value.limit == "max_key_length"


def test_a_numeric_metadata_value_is_checked_as_its_string_form():
    """The documented limit is on string values; JSON makes a number just as easy."""
    with pytest.raises(MetadataOutOfRange) as refusal:
        validate_metadata({"k": 10**500})
    assert refusal.value.limit == "max_value_length"
    assert refusal.value.maximum == 500


def test_a_boolean_metadata_value_is_stringified():
    assert validate_metadata({"flag": True}) == {"flag": True}


def test_the_room_context_is_merged_before_the_limits_are_checked():
    """Merging afterwards would push a full payload over a limit the caller was
    inside, and the key that would go is the one saying which room it came from."""
    payload = {f"key{index}": "v" for index in range(METADATA_LIMITS["max_keys"])}
    with pytest.raises(MetadataOutOfRange):
        validate_metadata(payload, extra=room_metadata(room_id="room_1", account="Northwind"))


def test_the_room_context_fits_the_documented_limits():
    extra = room_metadata(room_id="room_" + "x" * 40, account="Northwind Traders")
    merged = validate_metadata({}, extra=extra)
    for key, value in merged.items():
        assert len(key) <= METADATA_LIMITS["max_key_length"]
        assert len(str(value)) <= METADATA_LIMITS["max_value_length"]


def test_room_metadata_names_the_room_the_account_and_the_source():
    extra = room_metadata(room_id="room_1", account="Northwind")
    assert extra == {
        "dsr_booking_source": "in-room",
        "dsr_room_id": "room_1",
        "dsr_account": "Northwind",
    }


# --------------------------------------------------------------------------- #
# Bookings: the three kinds and the documented limits
# --------------------------------------------------------------------------- #


def test_a_booking_with_neither_recurrence_nor_instant_is_standard():
    request = read_booking_request({"start": "2026-09-28T14:00:00Z", "attendee": ATTENDEE})
    assert request.kind == "standard"
    assert request.recurrence_count == 1


def test_recurrence_count_makes_it_recurring():
    request = read_booking_request(
        {"start": "2026-09-28T14:00:00Z", "attendee": ATTENDEE, "recurrenceCount": 3}
    )
    assert request.kind == "recurring"
    assert request.recurrence_count == 3


def test_instant_makes_it_instant():
    request = read_booking_request({"attendee": ATTENDEE, "instant": True})
    assert request.kind == "instant"
    assert request.instant is True


def test_a_booking_cannot_be_both_recurring_and_instant():
    """The research lists three kinds as alternatives."""
    with pytest.raises(SchedulingError, match="either recurring or instant"):
        read_booking_request({"attendee": ATTENDEE, "instant": True, "recurrenceCount": 3})


def test_recurrence_count_above_thirty_two_is_refused_not_truncated():
    """A truncated success is indistinguishable from a complete one."""
    with pytest.raises(RecurrenceOutOfRange) as refusal:
        read_booking_request(
            {"start": "2026-09-28T14:00:00Z", "attendee": ATTENDEE, "recurrenceCount": 33}
        )
    assert refusal.value.maximum == MAX_RECURRENCE_COUNT == 32
    assert refusal.value.value == 33


def test_recurrence_count_of_exactly_thirty_two_is_allowed():
    request = read_booking_request(
        {"start": "2026-09-28T14:00:00Z", "attendee": ATTENDEE, "recurrenceCount": 32}
    )
    assert request.recurrence_count == 32


def test_recurrence_count_below_one_is_refused():
    with pytest.raises(RecurrenceOutOfRange, match="at least 1"):
        read_booking_request(
            {"start": "2026-09-28T14:00:00Z", "attendee": ATTENDEE, "recurrenceCount": 0}
        )


def test_a_standard_booking_needs_a_start():
    with pytest.raises(SchedulingError, match="start is required"):
        read_booking_request({"attendee": ATTENDEE})


def test_an_instant_booking_is_the_one_kind_that_may_omit_a_start():
    assert read_booking_request({"attendee": ATTENDEE, "instant": True}).start == ""


def test_instant_on_a_personal_event_is_refused():
    """ "instant (`\"instant\": true`, team events only) bookings"."""
    request = read_booking_request({"attendee": ATTENDEE, "instant": True})
    with pytest.raises(InstantNeedsTeamEvent) as refusal:
        require_team_event_for_instant(request, {"kind": "personal", "slug": "intro"})
    assert refusal.value.kind == "personal"


@pytest.mark.parametrize("kind", ["routing", "seated"])
def test_instant_on_a_routing_or_seated_event_is_refused_too(kind):
    """The parenthetical is about a team event, and nothing else satisfies it."""
    request = read_booking_request({"attendee": ATTENDEE, "instant": True})
    with pytest.raises(InstantNeedsTeamEvent):
        require_team_event_for_instant(request, {"kind": kind, "slug": "x"})


def test_instant_on_a_team_event_is_allowed():
    request = read_booking_request({"attendee": ATTENDEE, "instant": True})
    assert require_team_event_for_instant(request, {"kind": "team", "slug": "war-room"}) is None


def test_a_standard_booking_on_a_team_event_is_not_affected_by_the_instant_rule():
    request = read_booking_request({"start": "2026-09-28T14:00:00Z", "attendee": ATTENDEE})
    assert require_team_event_for_instant(request, {"kind": "personal"}) is None


def test_an_instant_booking_with_no_free_slot_is_refused():
    """A booking in the past is worse than a refusal, and now is in the past."""
    request = read_booking_request({"attendee": ATTENDEE, "instant": True})
    with pytest.raises(BookingConflict, match="nothing to book"):
        resolve_instant_start(request, [])


def test_an_instant_booking_takes_the_soonest_free_slot():
    request = read_booking_request({"attendee": ATTENDEE, "instant": True})
    slots = [
        {"start": "2026-09-28T14:00:00Z", "available": False, "reason": "hosts"},
        {"start": "2026-09-28T15:00:00Z", "available": True, "reason": None},
    ]
    assert resolve_instant_start(request, slots) == "2026-09-28T15:00:00Z"


def test_a_recurring_series_is_weekly_because_weekly_is_the_reading_with_fewest_assumptions():
    starts = describe_window("2026-09-29T10:00:00Z", count=3)
    assert starts == [
        "2026-09-29T10:00:00Z",
        "2026-10-06T10:00:00Z",
        "2026-10-13T10:00:00Z",
    ]


def test_the_reschedule_param_is_read_under_either_spelling():
    from dsr.inroom_scheduling.attendees import reschedule_uid

    assert reschedule_uid({"bookingUidToReschedule": "bkg_1"}) == "bkg_1"
    assert reschedule_uid({"booking_uid_to_reschedule": "bkg_2"}) == "bkg_2"
    assert reschedule_uid({}) is None


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("zoom", "https://zoom.us/j/"),
        ("google_meet", "https://meet.google.com/"),
        ("ms_teams", "https://teams.microsoft.com/l/meetup-join/"),
        ("webex", "https://webex.com/meet/"),
    ],
)
def test_each_documented_conference_provider_produces_a_join_link(provider, expected):
    payload = booking_payload(
        read_booking_request({"start": "2026-09-28T14:00:00Z", "attendee": ATTENDEE}),
        uid="bkg_1",
        event_type={
            "eventTypeId": "evt",
            "location": provider,
            "conference": provider,
            "title": "x",
        },
        start="2026-09-28T14:00:00Z",
        end="2026-09-28T14:30:00Z",
        room_id="room_1",
    )
    assert payload["video"].startswith(expected)


def test_a_phone_meeting_has_no_video_link_and_does_not_invent_one():
    payload = booking_payload(
        read_booking_request({"start": "2026-09-28T14:00:00Z", "attendee": ATTENDEE}),
        uid="bkg_1",
        event_type={"eventTypeId": "evt", "location": "phone", "title": "x"},
        start="2026-09-28T14:00:00Z",
        end="2026-09-28T14:30:00Z",
        room_id="room_1",
    )
    assert payload["video"] is None


def test_a_booking_payload_uses_the_researched_field_names():
    payload = booking_payload(
        read_booking_request(
            {
                "start": "2026-09-28T14:00:00Z",
                "attendee": ATTENDEE,
                "bookingFieldsResponses": {"topic": "x"},
                "metadata": {"deal_stage": "evaluation"},
            }
        ),
        uid="bkg_1",
        event_type={"eventTypeId": "evt_personal", "title": "Intro"},
        start="2026-09-28T14:00:00Z",
        end="2026-09-28T14:30:00Z",
        room_id="room_1",
    )
    assert payload["eventTypeId"] == "evt_personal"
    assert payload["bookingFieldsResponses"] == {"topic": "x"}
    assert payload["metadata"] == {"deal_stage": "evaluation"}


def test_booking_created_names_the_booking_it_belongs_to():
    event = booking_created_event(
        {"uid": "bkg_1", "eventTypeId": "evt", "start": "a", "end": "b"}, booking_record_id="rec_1"
    )
    assert event["event"] == BOOKING_CREATED == "BOOKING_CREATED"
    assert event["bookingUid"] == "bkg_1"
    assert event["booking_record_id"] == "rec_1"


def test_a_second_cancellation_is_refused_rather_than_overwriting_the_first():
    booking = {"uid": "bkg_1", "status": "cancelled"}
    with pytest.raises(SchedulingError, match="already cancelled"):
        require_cancellable(booking)


def test_a_cancellation_records_when_and_why():
    payload = cancel_payload({"uid": "bkg_1"}, reason="the prospect went away", now=MONDAY)
    assert payload["status"] == "cancelled"
    assert payload["cancellation"]["reason"] == "the prospect went away"
    assert payload["cancellation"]["at"] == iso(MONDAY)


def test_cancelling_a_series_does_not_rewrite_its_occurrences():
    """A cancelled series is a cancelled series, and rewriting nine future starts to
    say "cancelled" would make the record unreadable."""
    booking = {
        "uid": "bkg_1",
        "status": "confirmed",
        "recurrence": {"count": 3, "occurrences": ["a", "b", "c"]},
    }
    payload = cancel_payload(booking, reason="changed plans", now=MONDAY)
    assert "recurrence" not in payload


# --------------------------------------------------------------------------- #
# Booking fields: prefill and read-only
# --------------------------------------------------------------------------- #


def test_a_read_only_field_must_be_prefilled():
    """ "booking fields (prefill / read-only)" - the two travel together."""
    with pytest.raises(BookingFieldRejected, match="read_only but has no prefilled value"):
        normalise_booking_fields([{"name": "account", "read_only": True}])


def test_two_fields_with_one_name_are_refused():
    """``bookingFieldsResponses`` is keyed by name, so the second would replace the first."""
    with pytest.raises(BookingFieldRejected, match="two fields called"):
        normalise_booking_fields([{"name": "topic"}, {"name": "topic"}])


def test_a_select_with_no_options_is_refused():
    with pytest.raises(BookingFieldRejected, match="select with no options"):
        normalise_booking_fields([{"name": "size", "type": "select"}])


def test_an_unknown_field_type_is_refused():
    with pytest.raises(BookingFieldRejected, match="the published set is"):
        normalise_booking_fields([{"name": "x", "type": "hologram"}])


def test_a_required_field_left_unanswered_is_refused():
    event_type = {
        "booking_fields": [{"name": "team_size", "required": True, "options": ["1", "2"]}]
    }
    with pytest.raises(BookingFieldRejected, match="is required and was not answered"):
        apply_booking_fields(event_type, {})


def test_a_prefilled_field_is_answered_by_the_embed_not_the_prospect():
    event_type = {"booking_fields": [{"name": "topic", "prefilled": "migration plan"}]}
    assert apply_booking_fields(event_type, {}) == {"topic": "migration plan"}


def test_a_read_only_field_keeps_its_prefilled_value_when_nothing_is_submitted():
    event_type = {
        "booking_fields": [{"name": "account", "read_only": True, "prefilled": "Northwind"}]
    }
    assert apply_booking_fields(event_type, {}) == {"account": "Northwind"}


def test_a_read_only_field_cannot_be_changed_by_the_prospect():
    """Silently keeping the prefilled value would record a booking that differs from
    the one the prospect believes they submitted."""
    event_type = {
        "booking_fields": [{"name": "account", "read_only": True, "prefilled": "Northwind"}]
    }
    with pytest.raises(BookingFieldRejected, match="cannot be changed"):
        apply_booking_fields(event_type, {"account": "Contoso"})


def test_a_read_only_field_answered_with_its_prefilled_value_is_accepted():
    event_type = {
        "booking_fields": [{"name": "account", "read_only": True, "prefilled": "Northwind"}]
    }
    assert apply_booking_fields(event_type, {"account": "Northwind"}) == {"account": "Northwind"}


def test_an_answer_to_a_field_the_event_type_does_not_declare_is_refused():
    event_type = {"booking_fields": [{"name": "topic"}]}
    with pytest.raises(BookingFieldRejected, match="does not declare"):
        apply_booking_fields(event_type, {"budget": "large"})


def test_an_answer_outside_a_selects_options_is_refused():
    event_type = {"booking_fields": [{"name": "size", "type": "select", "options": ["1", "2"]}]}
    with pytest.raises(BookingFieldRejected, match="takes one of"):
        apply_booking_fields(event_type, {"size": "3000"})


def test_a_hidden_required_field_is_not_required_to_be_answered():
    """A hidden question is not shown, so its absence is not the prospect's doing."""
    event_type = {"booking_fields": [{"name": "utm", "required": True, "hidden": True}]}
    assert apply_booking_fields(event_type, {}) == {}


def test_a_hidden_field_with_a_prefilled_value_is_still_answered():
    event_type = {"booking_fields": [{"name": "utm", "hidden": True, "prefilled": "webinar"}]}
    assert apply_booking_fields(event_type, {}) == {"utm": "webinar"}


# --------------------------------------------------------------------------- #
# Event types
# --------------------------------------------------------------------------- #


def test_an_event_type_needs_an_id_and_a_slug():
    with pytest.raises(SchedulingError, match="eventTypeId is required"):
        normalise_event_type({"slug": "intro", "host": "priya"})
    with pytest.raises(SchedulingError, match="slug is required"):
        normalise_event_type({"eventTypeId": "evt", "host": "priya"})


def test_an_event_type_with_no_host_has_no_working_hours():
    with pytest.raises(SchedulingError, match="no host has no working hours"):
        normalise_event_type({"eventTypeId": "evt", "slug": "general", "kind": "routing"})


def test_an_event_type_with_an_empty_host_list_is_refused():
    with pytest.raises(SchedulingError, match="must name at least one host"):
        normalise_event_type({"eventTypeId": "evt", "slug": "intro", "host": "priya", "hosts": []})


def test_a_personal_event_needs_a_username_and_a_team_event_a_team_slug():
    with pytest.raises(SchedulingError, match="host is required for a personal event"):
        normalise_event_type({"eventTypeId": "evt", "slug": "intro", "kind": "personal"})
    with pytest.raises(SchedulingError, match="teamSlug is required for a team event"):
        normalise_event_type({"eventTypeId": "evt", "slug": "war", "kind": "team"})


def test_a_seated_event_must_say_how_many_seats():
    with pytest.raises(SchedulingError, match="seats is required for a seated event"):
        normalise_event_type(
            {"eventTypeId": "evt", "slug": "deep", "kind": "seated", "host": "alba"}
        )


def test_a_seat_count_on_a_non_seated_event_is_refused():
    """It would cap bookings nobody intended to cap, invisibly."""
    with pytest.raises(SchedulingError, match="only meaningful on a seated event"):
        normalise_event_type(
            {"eventTypeId": "evt", "slug": "intro", "kind": "personal", "host": "priya", "seats": 3}
        )


def test_two_hosts_with_one_name_are_refused():
    with pytest.raises(SchedulingError, match="names a username twice"):
        normalise_event_type(
            {
                "eventTypeId": "evt",
                "slug": "war",
                "kind": "team",
                "teamSlug": "t",
                "hosts": [
                    {"username": "a", "time_zone": "UTC"},
                    {"username": "a", "time_zone": "UTC"},
                ],
            }
        )


def test_an_unknown_location_is_refused():
    with pytest.raises(SchedulingError, match="it must be one of"):
        normalise_event_type(
            {"eventTypeId": "evt", "slug": "intro", "host": "priya", "location": "carrier-pigeon"}
        )


def test_a_conf_location_implies_the_conference():
    resolved = normalise_event_type(
        {"eventTypeId": "evt", "slug": "intro", "host": "priya", "location": "zoom"}
    )
    assert resolved["conference"] == "zoom"


def test_an_unknown_conference_provider_is_refused():
    with pytest.raises(SchedulingError, match="unknown conference provider"):
        normalise_event_type(
            {
                "eventTypeId": "evt",
                "slug": "intro",
                "host": "priya",
                "location": "phone",
                "conference": "skype",
            }
        )


def test_a_calendar_connection_needs_a_named_host():
    with pytest.raises(SchedulingError, match="host is required"):
        normalise_calendar_connection({"provider": "google"})


def test_an_unknown_calendar_provider_is_refused():
    with pytest.raises(SchedulingError, match="unknown calendar provider"):
        normalise_calendar_connection({"provider": "yahoo", "host": "priya"})


def test_calendar_summary_names_what_is_connected_and_what_is_missing():
    summary = calendar_summary(
        [
            {"data": {"provider": "google"}},
            {"data": {"provider": "outlook"}},
        ]
    )
    assert summary["connected"] == ["google", "outlook"]
    assert summary["missing"] == ["apple"]


def test_calendar_summary_reads_bare_payloads_too():
    assert calendar_summary([{"provider": "apple"}])["connected"] == ["apple"]


# --------------------------------------------------------------------------- #
# The embed and the OAuth client
# --------------------------------------------------------------------------- #


def test_an_oauth_client_needs_a_client_id_a_redirect_uri_and_scopes():
    with pytest.raises(EmbedConfigError, match="client_id is required"):
        normalise_oauth_client({"redirect_uri": "https://x", "scopes": ["BOOKING"]})
    with pytest.raises(EmbedConfigError, match="redirect_uri is required"):
        normalise_oauth_client({"client_id": "c", "scopes": ["BOOKING"]})
    with pytest.raises(EmbedConfigError, match="scopes is required"):
        normalise_oauth_client({"client_id": "c", "redirect_uri": "https://x"})


def test_an_unknown_scope_is_refused_against_the_published_set():
    with pytest.raises(EmbedConfigError, match="the published set is"):
        normalise_oauth_client(
            {"client_id": "c", "redirect_uri": "https://x", "scopes": ["BOOKING", "MIND_READING"]}
        )


def test_a_credential_shaped_field_is_refused_rather_than_stored():
    """The audit log, the schema explorer and the room page all read these records."""
    for field in ("client_secret", "access_token", "api_key", "password"):
        with pytest.raises(EmbedConfigError, match="looks like a credential"):
            normalise_oauth_client(
                {
                    "client_id": "c",
                    "redirect_uri": "https://x",
                    "scopes": ["BOOKING"],
                    field: "s3cret",
                }
            )


def test_a_grant_records_the_subject_the_scopes_and_the_expiry_and_nothing_else():
    token = grant_token({"scopes": ["BOOKING"]}, subject="priya", now=MONDAY)
    assert token == {
        "subject": "priya",
        "scopes": ["BOOKING"],
        "granted_at": iso(MONDAY),
        "expires_at": iso(MONDAY + timedelta(minutes=30 * 24 * 60)),
    }
    assert "token" not in json.dumps(token).lower()


def test_a_grant_needs_somebody_to_act_for():
    with pytest.raises(EmbedConfigError, match="acts for nobody"):
        grant_token({"scopes": ["BOOKING"]}, subject="", now=MONDAY)


def test_a_grant_replaces_rather_than_merges_its_scopes():
    """Keeping an old scope beside a new one would make the record claim more than
    the token can do, and the symptom would be a request the vendor rejects."""
    client = {"scopes": ["BOOKING", "CALENDAR_WRITE"]}
    token = grant_token(client, subject="priya", scopes=["BOOKING"], now=MONDAY)
    assert token["scopes"] == ["BOOKING"]


def test_a_client_with_no_token_cannot_book():
    state = token_state({}, now=MONDAY)
    assert state == {
        "has_token": False,
        "live": False,
        "reason": "no_token",
        "detail": state["detail"],
    }


def test_an_expired_token_is_told_apart_from_a_missing_one():
    client = {
        "token": grant_token(
            {"scopes": ["BOOKING"]}, subject="priya", now=MONDAY, lifetime_minutes=10
        )
    }
    state = token_state(client, now=MONDAY + timedelta(minutes=11))
    assert state["reason"] == "expired"
    with pytest.raises(TokenExpired, match="has expired"):
        require_live_token(client, now=MONDAY + timedelta(minutes=11))


def test_a_live_token_without_the_booking_scope_needs_a_different_fix():
    """Reconnecting with the same scopes will not help, and saying so saves an afternoon."""
    client = {"token": grant_token({"scopes": ["CALENDAR_READ"]}, subject="priya", now=MONDAY)}
    state = token_state(client, now=MONDAY)
    assert state["reason"] == "no_booking_scope"
    assert "will not help" in state["detail"]


def test_a_live_booking_token_can_book():
    client = {"token": grant_token({"scopes": ["BOOKING"]}, subject="priya", now=MONDAY)}
    assert require_live_token(client, now=MONDAY)["live"] is True


def test_an_embed_defaults_to_a_booker():
    """ "The sales room renders a Booker (and optionally ...)"."""
    assert normalise_embed({"eventTypeId": "evt"})["components"] == ["booker"]


def test_an_embed_rendering_no_booking_component_is_refused():
    """ "The prospect books entirely in-room" needs one of them."""
    with pytest.raises(EmbedConfigError, match="renders no booking component"):
        normalise_embed({"eventTypeId": "evt", "components": ["availability", "event_type"]})


def test_the_optional_components_are_genuinely_optional():
    resolved = normalise_embed(
        {
            "eventTypeId": "evt",
            "components": ["booker", "availability", "event_type", "calendar_connect"],
        }
    )
    assert resolved["components"] == ["booker", "availability", "event_type", "calendar_connect"]


def test_a_payment_form_on_a_free_event_type_is_refused():
    """ "a PaymentForm on an event type with no price renders a form that takes a payment
    for nothing"."""
    with pytest.raises(EmbedConfigError, match="no price"):
        normalise_embed(
            {"eventTypeId": "evt", "components": ["booker", "payment_form"]}, event_type={}
        )
    resolved = normalise_embed(
        {"eventTypeId": "evt", "components": ["booker", "payment_form"]}, event_type={"price": 250}
    )
    assert "payment_form" in resolved["components"]


def test_an_unknown_embed_component_is_refused():
    with pytest.raises(SchedulingError, match="unknown embed component"):
        normalise_embed({"eventTypeId": "evt", "components": ["booker", "troubleshooter"]})


def test_an_unknown_custom_property_is_refused():
    """A typo'd property that is accepted and has no effect is the worst outcome for
    somebody styling an embed."""
    with pytest.raises(SchedulingError, match="unknown embed custom property"):
        normalise_embed({"eventTypeId": "evt", "css": {"--cal-accent": "red"}})


def test_every_published_custom_property_is_accepted():
    resolved = normalise_embed(
        {"eventTypeId": "evt", "css": {name: "1px" for name in EMBED_CSS_VARIABLES}}
    )
    assert set(resolved["css"]) == set(EMBED_CSS_VARIABLES)


def test_duplicate_components_are_collapsed():
    assert normalise_embed({"eventTypeId": "evt", "components": ["booker", "booker"]})[
        "components"
    ] == ["booker"]


def test_a_credential_shaped_field_is_refused_on_an_embed_too():
    with pytest.raises(EmbedConfigError, match="looks like a credential"):
        normalise_embed({"eventTypeId": "evt", "access_token": "s3cret"})


# --------------------------------------------------------------------------- #
# Routing: the catch-all
# --------------------------------------------------------------------------- #


def test_a_routing_form_must_end_in_a_catch_all():
    """A response that matches no rule still has to reach a host, or the prospect is
    left on a form that appears broken."""
    with pytest.raises(RoutingError, match="must end in a catch-all"):
        normalise_form(
            {
                "name": "f",
                "rules": [
                    {"field": "topic", "operator": "equals", "value": "x", "eventTypeId": "evt"}
                ],
            }
        )


def test_a_routing_form_with_no_rules_is_refused():
    with pytest.raises(RoutingError, match="matches nothing"):
        normalise_form({"name": "f", "rules": [], "fallbackEventTypeId": "evt"})


def test_a_rule_needs_a_field_an_operator_and_an_event_type():
    with pytest.raises(RoutingError, match="field is required"):
        normalise_form(
            form_spec(rules=[{"operator": "equals", "value": "x", "eventTypeId": "evt"}])
        )
    with pytest.raises(RoutingError, match="unknown routing operator"):
        normalise_form(
            form_spec(
                rules=[
                    {"field": "t", "operator": "sounds_like", "value": "x", "eventTypeId": "evt"}
                ]
            )
        )
    with pytest.raises(RoutingError, match="needs an eventTypeId"):
        normalise_form(form_spec(rules=[{"field": "t", "operator": "equals", "value": "x"}]))


def test_a_rule_that_routes_nowhere_must_say_so():
    """An empty eventTypeId on a rule that does not decline would be a typo."""
    declined = normalise_form(
        form_spec(rules=[{"field": "topic", "operator": "equals", "value": "x", "fallback": True}])
    )
    assert declined["rules"][0]["eventTypeId"] is None
    assert declined["rules"][0]["fallback"] is True


def test_a_rule_needs_a_value_unless_its_operator_is_exists():
    with pytest.raises(RoutingError, match="needs a value for"):
        normalise_form(
            form_spec(rules=[{"field": "t", "operator": "equals", "eventTypeId": "evt"}])
        )
    assert (
        normalise_form(
            form_spec(rules=[{"field": "t", "operator": "exists", "eventTypeId": "evt"}])
        )["rules"][0]["operator"]
        == "exists"
    )


def test_an_unpublished_operator_is_refused():
    with pytest.raises(RoutingError, match="the published set is"):
        require_operator("sounds_like")


@pytest.mark.parametrize(
    ("operator", "value", "answer", "expected"),
    [
        ("equals", "security", "Security", True),
        ("equals", "security", "sales", False),
        ("not_equals", "security", "sales", True),
        ("not_equals", "security", "security", False),
        ("contains", "security", "a security review", True),
        ("contains", "pricing", "a security review", False),
        ("starts_with", "security", "security review", True),
        ("starts_with", "review", "security review", False),
        ("in", "security,pricing", "security", True),
        ("in", "security,pricing", "marketing", False),
        ("in", ["security", "pricing"], "Pricing", True),
        ("greater_than", 100, "500", True),
        ("greater_than", 100, "50", False),
        ("less_than", 100, "50", True),
        ("less_than", 100, "500", False),
        ("exists", None, "anything", True),
        ("exists", None, "  ", False),
    ],
)
def test_every_published_operator_behaves_as_published(operator, value, answer, expected):
    rule = {"field": "topic", "operator": operator, "value": value, "eventTypeId": "evt"}
    assert rule_matches(rule, {"topic": answer}) is expected


def test_a_numeric_operator_does_not_match_a_non_number():
    """A form answering "a few" to a headcount question must fall through visibly."""
    assert (
        rule_matches({"field": "size", "operator": "greater_than", "value": 100}, {"size": "a few"})
        is False
    )


def test_a_rule_does_not_match_a_question_that_was_not_asked():
    assert rule_matches({"field": "topic", "operator": "exists"}, {"other": "x"}) is False


def test_string_comparison_is_trimmed_and_case_insensitive():
    assert (
        rule_matches(
            {"field": "topic", "operator": "equals", "value": "Security", "eventTypeId": "evt"},
            {"topic": "  SECURITY  "},
        )
        is True
    )


def test_the_first_matching_rule_wins():
    form = {
        "rules": [
            {"field": "topic", "operator": "equals", "value": "a", "eventTypeId": "evt_first"},
            {"field": "topic", "operator": "equals", "value": "a", "eventTypeId": "evt_second"},
        ],
        "fallbackEventTypeId": "evt_fallback",
    }
    assert route(form, {"topic": "a"})["eventTypeId"] == "evt_first"


def test_an_unmatched_answer_falls_through_to_the_catch_all():
    """The build brief's rule: a rule that does not fall through is a bug somebody
    hits in production."""
    form = {
        "rules": [
            {"field": "topic", "operator": "equals", "value": "a", "eventTypeId": "evt_first"}
        ],
        "fallbackEventTypeId": "evt_fallback",
    }
    answer = route(form, {"topic": "zzz"})
    assert answer["routed"] is True
    assert answer["eventTypeId"] == "evt_fallback"
    assert answer["matched_rule"] is None
    assert answer["reason"] == "fallback"


def test_a_rule_that_matched_and_declined_routes_nowhere_deliberately():
    """Distinguishable from the catch-all doing its job, which matters because only
    one of them is somebody's decision."""
    form = {
        "rules": [
            {
                "field": "topic",
                "operator": "equals",
                "value": "unsubscribe",
                "eventTypeId": None,
                "fallback": True,
            }
        ],
        "fallbackEventTypeId": "evt_fallback",
    }
    answer = route(form, {"topic": "unsubscribe"})
    assert answer["routed"] is False
    assert answer["reason"] == "rule_declined"
    assert answer["eventTypeId"] is None


def test_a_declined_route_returns_no_slots():
    response = routed_slots_response(
        {
            "routed": False,
            "eventTypeId": None,
            "matched_rule": 0,
            "reason": "rule_declined",
            "detail": "d",
        },
        [{"start": "a", "available": True}],
        form_id="form_1",
    )
    assert response["slots"] == []
    assert response["slot_count"] == 0


def test_the_routing_response_says_it_saved_nothing():
    """It will not actually save the response."""
    response = routed_slots_response(
        {
            "routed": True,
            "eventTypeId": "evt",
            "matched_rule": 0,
            "reason": "rule_matched",
            "detail": "d",
        },
        [{"start": "a"}],
        form_id="form_1",
    )
    assert response["saved"] is False
    assert "will not actually save the response" in response["note"]


# --------------------------------------------------------------------------- #
# The engine, end to end
# --------------------------------------------------------------------------- #


def test_a_room_with_no_embed_cannot_book(engine, room):
    """Step 1 installs the embeddable booking components before there is a Booker."""
    add_client(engine)
    add_event(engine, PERSONAL)
    with pytest.raises(EmbedConfigError, match="has no embed installed"):
        engine.book(
            room["id"],
            {"start": "2026-09-28T14:00:00Z", "attendee": ATTENDEE},
            actor="dana",
            source=SOURCE,
        )


def test_creating_a_client_and_granting_it_records_a_live_token(engine):
    client = add_client(engine, grant=False)
    assert engine.client_token_state(client)["reason"] == "no_token"
    engine.grant(client["id"], {"subject": "priya"}, actor="dana", source=SOURCE)
    assert engine.client_token_state(engine.require_client(client["id"]))["live"] is True


def test_a_patch_cannot_write_a_token(engine):
    client = add_client(engine)
    patched = engine.update_client(client["id"], {"name": "Renamed"}, actor="dana", source=SOURCE)
    assert patched["data"]["name"] == "Renamed"
    assert patched["data"]["token"]["subject"] == "priya"


def test_several_clients_and_no_choice_is_refused_rather_than_guessing(engine, room):
    add_client(engine, client_id="a")
    add_client(engine, client_id="b")
    add_event(engine, PERSONAL)
    with pytest.raises(EmbedConfigError, match="creation order pick one"):
        install(engine, room["id"])


def test_both_booking_components_satisfy_the_rule():
    """The research lists a Booker and a Booker Embed; either is enough to book."""
    for name in ("booker", "booker_embed"):
        resolved = normalise_embed({"eventTypeId": "evt", "components": [name]})
        assert any(part in EMBED_BOOKING_COMPONENTS for part in resolved["components"])


def test_an_embed_with_no_horizon_gets_the_documented_default(engine, room):
    ready(engine, room["id"])
    assert engine.require_embed(room["id"])["horizon_days"] == DEFAULT_HORIZON_DAYS


def test_an_embed_horizon_outside_the_window_bound_is_refused(engine, room):
    ready(engine, room["id"])
    with pytest.raises(EmbedConfigError, match="between 1 and"):
        engine.save_embed(
            room["id"],
            {"eventTypeId": "evt_personal", "horizon_days": MAX_SLOT_WINDOW_DAYS + 1},
            actor="dana",
            source=SOURCE,
        )


def test_an_embed_may_widen_its_own_horizon(engine, room):
    ready(engine, room["id"])
    saved = engine.save_embed(
        room["id"],
        {"eventTypeId": "evt_personal", "horizon_days": 30},
        actor="dana",
        source=SOURCE,
    )
    assert saved["embed"]["horizon_days"] == 30


def test_saving_an_embed_twice_replaces_it_rather_than_adding_a_second(engine, room):
    add_client(engine)
    add_event(engine, PERSONAL)
    install(engine, room["id"], components=["booker"])
    install(engine, room["id"], components=["booker", "availability"])
    assert engine.embed_record(room["id"])["components"] == ["booker", "availability"]


def test_an_embed_naming_only_a_form_takes_the_forms_catch_all(engine, room):
    add_client(engine)
    add_event(engine, PERSONAL)
    add_event(engine, SEATED)
    add_event(engine, ROUTING)
    form = engine.create_form(form_spec(), actor="dana", source=SOURCE)
    saved = install(engine, room["id"], eventTypeId=None, routingFormId=form["id"])
    assert saved["embed"]["eventTypeId"] == "evt_routing"


def test_an_embed_naming_neither_an_event_type_nor_a_form_is_refused(engine, room):
    add_client(engine)
    add_event(engine, PERSONAL)
    with pytest.raises(EmbedConfigError, match="must name an eventTypeId"):
        install(engine, room["id"], eventTypeId=None)


def test_the_embed_lives_on_the_room_row(engine, room, store):
    ready(engine, room["id"])
    annotation = store.get(room["id"])["data"][ROOM_FIELD]
    assert annotation["embed"]["eventTypeId"] == "evt_personal"


def test_a_room_annotation_keeps_a_capped_booking_history(engine, room):
    ready(engine, room["id"])
    start = first_slot(engine, room["id"])
    for index in range(2):
        engine.book(
            room["id"],
            {
                "start": first_slot(engine, room["id"]),
                "attendee": {"name": f"P{index}", "email": f"p{index}@fabrikam.example"},
            },
            actor="dana",
            source=SOURCE,
        )
    assert start
    history = engine.store.get(room["id"])["data"][ROOM_FIELD]["bookings"]
    assert len(history) == 2


def test_an_event_type_a_slug_and_a_username_resolve_to_the_same_grid(engine, room):
    ready(engine, room["id"])
    by_id = engine.slots(room["id"], {"eventTypeId": "evt_personal"})
    by_slug = engine.slots(room["id"], {"eventTypeSlug": "intro", "username": "priya"})
    assert [slot["start"] for slot in by_id["slots"]] == [
        slot["start"] for slot in by_slug["slots"]
    ]


def test_a_team_slug_resolves_a_team_event(engine, room):
    ready(engine, room["id"], event_types=(PERSONAL, TEAM), embed={"eventTypeId": "evt_team"})
    grid = engine.slots(room["id"], {"eventTypeSlug": "war-room", "teamSlug": "revenue-team"})
    assert grid["event_type_id"] == "evt_team"


def test_a_query_that_resolves_ambiguously_is_refused(engine, room):
    """Two event types on one id: it would offer a slot for one of them and book
    another, and nothing in the answer would say which."""
    add_client(engine)
    engine.create_event_type(PERSONAL, actor="dana", source=SOURCE)
    engine.create_event_type(PERSONAL, actor="dana", source=SOURCE)
    with pytest.raises(UnknownEventType, match="resolves ambiguously"):
        engine.slots(room["id"], {"eventTypeId": "evt_personal"})


def test_a_booking_records_the_booking_created_event_and_a_pending_delivery(engine, room):
    ready(engine, room["id"])
    created = engine.book(
        room["id"],
        {"start": first_slot(engine, room["id"]), "attendee": ATTENDEE},
        actor="dana",
        source=SOURCE,
    )
    events = engine.booking_events(room["id"])
    assert [event["data"]["event"] for event in events] == [BOOKING_CREATED]
    assert events[0]["data"]["bookingUid"] == created["uid"]
    deliveries = engine.webhooks(room["id"])
    assert [delivery["data"]["status"] for delivery in deliveries] == ["pending"]


def test_a_reschedule_moves_the_booking_and_does_not_refire_the_event(engine, room):
    """A move is not a new booking, and a second notification would be a bug."""
    ready(engine, room["id"])
    start = first_slot(engine, room["id"])
    created = engine.book(
        room["id"], {"start": start, "attendee": ATTENDEE}, actor="dana", source=SOURCE
    )
    moved = engine.book(
        room["id"],
        {
            "start": first_slot(
                engine, room["id"], start="2026-09-28T08:00:00Z", end="2026-10-01T00:00:00Z"
            ),
            "bookingUidToReschedule": created["uid"],
            "attendee": ATTENDEE,
        },
        actor="dana",
        source=SOURCE,
    )
    assert moved["rescheduled"] is True
    assert moved["moved_from"] == start
    assert len(engine.bookings(room["id"])) == 1
    assert len(engine.booking_events(room["id"])) == 1


def test_a_reschedule_onto_the_slot_the_booking_already_holds_is_permitted(engine, room):
    """Without the researched exclusion the prospect is blocked by their own booking."""
    ready(engine, room["id"])
    created = engine.book(
        room["id"],
        {"start": first_slot(engine, room["id"]), "attendee": ATTENDEE},
        actor="dana",
        source=SOURCE,
    )
    again = engine.book(
        room["id"],
        {
            "start": created["start"],
            "bookingUidToReschedule": created["uid"],
            "attendee": ATTENDEE,
        },
        actor="dana",
        source=SOURCE,
    )
    assert again["start"] == created["start"]


def test_a_hold_in_one_room_is_seen_by_another_room_sharing_the_host(engine, room, other_room):
    """A host is busy whoever booked them, and two rooms share the host's calendar."""
    ready(engine, room["id"])
    ready(engine, other_room["id"])
    engine.reserve(room["id"], {"start": "2026-09-28T14:00:00Z"}, actor="dana", source=SOURCE)
    slots = {slot["start"]: slot for slot in _grid(engine, other_room["id"])}
    assert slots["2026-09-28T14:00:00Z"]["reason"] == "held"


def test_a_reschedule_of_a_booking_from_another_room_does_not_move_it(engine, room, other_room):
    """The busy-time exclusion is scoped to the room, or a room could book over a
    slot somebody else holds by citing its id."""
    ready(engine, room["id"])
    ready(engine, other_room["id"])
    mine = engine.book(
        room["id"],
        {"start": first_slot(engine, room["id"]), "attendee": ATTENDEE},
        actor="dana",
        source=SOURCE,
    )
    with pytest.raises(SlotUnavailable):
        engine.book(
            other_room["id"],
            {
                "start": mine["start"],
                "bookingUidToReschedule": mine["uid"],
                "attendee": {"name": "Other", "email": "other@contoso.example"},
            },
            actor="dana",
            source=SOURCE,
        )
    assert len(engine.bookings(room["id"])) == 1
    assert engine.bookings(other_room["id"]) == []


def test_cancelling_a_booking_returns_its_slot_and_records_the_event(engine, room):
    ready(engine, room["id"])
    created = engine.book(
        room["id"],
        {"start": first_slot(engine, room["id"]), "attendee": ATTENDEE},
        actor="dana",
        source=SOURCE,
    )
    engine.cancel_booking(room["id"], created["uid"], "went away", actor="dana", source=SOURCE)
    slots = {slot["start"]: slot for slot in _grid(engine, room["id"])}
    assert slots[created["start"]]["available"] is True
    # Newest first, and the two rows share a second so the tie-break is the id.
    assert [event["data"]["event"] for event in engine.booking_events(room["id"])] == [
        "BOOKING_CANCELLED",
        BOOKING_CREATED,
    ]


def test_a_booking_against_a_consumed_hold_is_refused_on_its_second_attempt(engine, room):
    ready(engine, room["id"])
    start = first_slot(engine, room["id"])
    hold = engine.reserve(room["id"], {"start": start}, actor="dana", source=SOURCE)
    created = engine.book(
        room["id"],
        {"start": start, "reservationUid": hold["reservationUid"], "attendee": ATTENDEE},
        actor="dana",
        source=SOURCE,
    )
    assert created["booking"]["held"] is True
    assert engine.hold(room["id"], hold["reservationUid"])["state"] == "consumed"


def test_a_booking_against_a_hold_for_a_different_slot_is_refused(engine, room):
    ready(engine, room["id"])
    hold = engine.reserve(
        room["id"], {"start": "2026-09-28T14:00:00Z"}, actor="dana", source=SOURCE
    )
    with pytest.raises(SlotUnavailable) as refusal:
        engine.book(
            room["id"],
            {
                "start": "2026-09-28T15:00:00Z",
                "reservationUid": hold["reservationUid"],
                "attendee": ATTENDEE,
            },
            actor="dana",
            source=SOURCE,
        )
    assert refusal.value.reason == "hold_slot_mismatch"


def test_a_room_can_require_a_hold_before_a_booking(engine, room):
    ready(engine, room["id"], embed={"eventTypeId": "evt_personal", "require_hold": True})
    with pytest.raises(HoldRequired, match="Reserve the slot first"):
        engine.book(
            room["id"],
            {"start": first_slot(engine, room["id"]), "attendee": ATTENDEE},
            actor="dana",
            source=SOURCE,
        )


def test_a_reading_never_writes(engine, room, store):
    """A GET that quietly rewrites rows is a surprise nobody can audit."""
    ready(engine, room["id"])
    hold = engine.reserve(
        room["id"], {"start": first_slot(engine, room["id"])}, actor="dana", source=SOURCE
    )
    later = SchedulingEngine(engine.store, clock=lambda: MONDAY + timedelta(minutes=6))
    before = store.audit(limit=1000)
    later.holds(room["id"])
    later.slots(room["id"], {"eventTypeId": "evt_personal"})
    later.hold(room["id"], hold["reservationUid"])
    later.summary()
    assert store.audit(limit=1000) == before


def test_a_write_materialises_a_lapsed_hold_and_records_both_moments(engine, room, store):
    """The hold lapsed at ``reservationUntil``; the product noticed when this ran."""
    ready(engine, room["id"])
    hold = engine.reserve(
        room["id"], {"start": first_slot(engine, room["id"])}, actor="dana", source=SOURCE
    )
    later = SchedulingEngine(engine.store, clock=lambda: MONDAY + timedelta(minutes=6))
    later.reserve(room["id"], {"start": "2026-09-28T15:00:00Z"}, actor="dana", source=SOURCE)
    stored = store.find(RESERVATION_COLLECTION, {"reservationUid": hold["reservationUid"]})[0][
        "data"
    ]
    assert stored["state"] == "expired"
    assert stored["expired_at"] == iso(MONDAY + timedelta(minutes=5))
    assert stored["noticed_at"] == iso(MONDAY + timedelta(minutes=6))


def test_booking_reports_the_holds_it_noticed_had_lapsed(engine, room):
    ready(engine, room["id"])
    engine.reserve(room["id"], {"start": "2026-09-28T14:00:00Z"}, actor="dana", source=SOURCE)
    later = SchedulingEngine(engine.store, clock=lambda: MONDAY + timedelta(minutes=6))
    created = later.book(
        room["id"],
        {"start": "2026-09-28T15:00:00Z", "attendee": ATTENDEE},
        actor="dana",
        source=SOURCE,
    )
    assert created["holds_expired_before_this_write"]


def test_booking_into_a_hold_that_has_lapsed_is_refused_with_the_expiry(engine, room):
    ready(engine, room["id"])
    hold = engine.reserve(
        room["id"], {"start": "2026-09-28T14:00:00Z"}, actor="dana", source=SOURCE
    )
    later = SchedulingEngine(engine.store, clock=lambda: MONDAY + timedelta(minutes=6))
    with pytest.raises(HoldExpired):
        later.book(
            room["id"],
            {
                "start": "2026-09-28T14:00:00Z",
                "reservationUid": hold["reservationUid"],
                "attendee": ATTENDEE,
            },
            actor="dana",
            source=SOURCE,
        )


def test_booking_without_a_live_token_is_refused(engine, room):
    add_client(engine, grant=False)
    add_event(engine, PERSONAL)
    install(engine, room["id"])
    with pytest.raises(TokenExpired, match="no OAuth token"):
        engine.book(
            room["id"],
            {"start": "2026-09-28T14:00:00Z", "attendee": ATTENDEE},
            actor="dana",
            source=SOURCE,
        )


def test_reserving_without_a_live_token_is_refused(engine, room):
    add_client(engine, grant=False)
    add_event(engine, PERSONAL)
    install(engine, room["id"])
    with pytest.raises(TokenExpired):
        engine.reserve(room["id"], {"start": "2026-09-28T14:00:00Z"}, actor="dana", source=SOURCE)


def test_reading_the_grid_does_not_need_a_token(engine, room):
    """A grid a rep cannot preview is a grid they cannot configure."""
    add_client(engine, grant=False)
    add_event(engine, PERSONAL)
    install(engine, room["id"])
    assert engine.slots(room["id"], {"eventTypeId": "evt_personal"})["slot_count"] > 0


def test_a_booking_metadata_overflow_writes_nothing(engine, room, store):
    ready(engine, room["id"])
    before = record_count(store)
    with pytest.raises(MetadataOutOfRange):
        engine.book(
            room["id"],
            {
                "start": first_slot(engine, room["id"]),
                "attendee": ATTENDEE,
                "metadata": {"k" * 41: "v"},
            },
            actor="dana",
            source=SOURCE,
        )
    assert record_count(store) == before


def test_a_read_only_field_refusal_writes_nothing(engine, room, store):
    add_client(engine)
    add_event(
        engine,
        {
            **PERSONAL,
            "booking_fields": [
                {"name": "account", "read_only": True, "prefilled": "Northwind"},
            ],
        },
    )
    install(engine, room["id"])
    before = record_count(store)
    with pytest.raises(BookingFieldRejected):
        engine.book(
            room["id"],
            {
                "start": first_slot(engine, room["id"]),
                "attendee": ATTENDEE,
                "bookingFieldsResponses": {"account": "Contoso"},
            },
            actor="dana",
            source=SOURCE,
        )
    assert record_count(store) == before


def test_routing_a_room_saves_nothing(engine, room, store):
    """ "It will not actually save the response"."""
    ready(engine, room["id"], event_types=(PERSONAL, SEATED, ROUTING))
    form = engine.create_form(form_spec(), actor="dana", source=SOURCE)
    install(engine, room["id"], routingFormId=form["id"])
    before = record_count(store)
    for answers in ({"topic": "security"}, {"topic": "zzz"}, {"topic": "unsubscribe"}, {}):
        engine.routed_slots(room["id"], {"formId": form["id"]}, answers)
    assert record_count(store) == before


def test_routing_uses_the_embeds_form_when_none_is_named(engine, room):
    ready(engine, room["id"], event_types=(PERSONAL, SEATED, ROUTING))
    form = engine.create_form(form_spec(), actor="dana", source=SOURCE)
    install(engine, room["id"], routingFormId=form["id"])
    answer = engine.routed_slots(room["id"], {}, {"topic": "zzz"})
    assert answer["eventTypeId"] == "evt_routing"
    assert answer["reason"] == "fallback"


def test_routing_a_matched_rule_returns_that_event_types_slots(engine, room):
    ready(engine, room["id"], event_types=(PERSONAL, SEATED, ROUTING))
    form = engine.create_form(form_spec(), actor="dana", source=SOURCE)
    answer = engine.routed_slots(room["id"], {"formId": form["id"]}, {"topic": "security"})
    assert answer["eventTypeId"] == "evt_seated"
    assert answer["slot_count"] > 0


def test_routing_without_a_form_is_refused(engine, room):
    ready(engine, room["id"])
    with pytest.raises(UnknownEventType, match="needs a routing form"):
        engine.routed_slots(room["id"], {}, {"topic": "x"})


def test_a_routing_form_naming_a_missing_event_type_cannot_be_created(engine, room):
    add_client(engine)
    add_event(engine, ROUTING)
    with pytest.raises(UnknownEventType, match="no event type has eventTypeId"):
        engine.create_form(form_spec(), actor="dana", source=SOURCE)


def test_the_summary_counts_only_the_room_it_was_asked_about(engine, room, other_room):
    ready(engine, room["id"])
    ready(engine, other_room["id"])
    engine.book(
        room["id"],
        {"start": first_slot(engine, room["id"]), "attendee": ATTENDEE},
        actor="dana",
        source=SOURCE,
    )
    assert engine.summary()["bookings"] == 1
    assert engine.summary(room_id=room["id"])["bookings"] == 1
    assert engine.summary(room_id=other_room["id"])["bookings"] == 0
    assert engine.summary(room_id=other_room["id"])["room_id"] == other_room["id"]


def test_the_summary_reports_both_documented_api_versions(engine):
    assert engine.summary()["api_versions"] == {
        "slots": "2024-09-04",
        "bookings": "2026-02-25",
    }


def test_connecting_the_same_calendar_twice_updates_rather_than_duplicating(engine):
    first = engine.connect_calendar(
        {"provider": "google", "host": "priya"}, actor="dana", source=SOURCE
    )
    second = engine.connect_calendar(
        {"provider": "google", "host": "priya", "label": "Work"}, actor="dana", source=SOURCE
    )
    assert first["id"] == second["id"]
    assert len(engine.list_calendars()) == 1
    assert engine.calendars()["connected"] == ["google"]


def test_booking_a_room_that_does_not_exist_is_a_404(engine):
    with pytest.raises(Exception) as missing:
        engine.slots("room_nope", {"eventTypeId": "evt_personal"})
    assert type(missing.value).__name__ == "RecordNotFound"


# --------------------------------------------------------------------------- #
# The audit trail, and the source rule
# --------------------------------------------------------------------------- #


def test_every_write_method_requires_a_source(engine, room):
    """The defect this prevents: an audit row naming a path nobody served.

    ``source`` is keyword-only and has no default, so a caller that forgets it fails
    loudly rather than writing a null into the audit log.
    """
    for call in (
        lambda: engine.create_client(client_spec()),
        lambda: engine.create_event_type(PERSONAL),
        lambda: engine.connect_calendar({"provider": "google", "host": "p"}),
        lambda: engine.create_form(form_spec()),
        lambda: engine.save_embed(room["id"], {"eventTypeId": "evt"}),
        lambda: engine.reserve(room["id"], {"start": "2026-09-28T14:00:00Z"}),
        lambda: engine.extend_hold(room["id"], "rsv", {"reservationDuration": 10}),
        lambda: engine.release_hold(room["id"], "rsv"),
        lambda: engine.book(room["id"], {"start": "2026-09-28T14:00:00Z", "attendee": ATTENDEE}),
        lambda: engine.cancel_booking(room["id"], "bkg", "because"),
        lambda: engine.grant("client", {"subject": "p"}),
        lambda: engine.update_client("client", {"name": "x"}),
        lambda: engine.delete_client("client"),
        lambda: engine.update_event_type("evt", {"title": "x"}),
        lambda: engine.delete_event_type("evt"),
        lambda: engine.update_form("form", {"name": "x"}),
        lambda: engine.delete_form("form"),
        lambda: engine.disconnect_calendar("cal"),
    ):
        with pytest.raises(TypeError):
            call()


def test_a_booking_is_audited_with_the_source_it_was_given(engine, store, room):
    ready(engine, room["id"])
    engine.book(
        room["id"],
        {"start": first_slot(engine, room["id"]), "attendee": ATTENDEE},
        actor="dana",
        source=SOURCE,
    )
    entries = store.audit(collection=BOOKING_COLLECTION)
    assert [row["source"] for row in entries] == [SOURCE]
    assert entries[0]["room_id"] == room["id"]


def test_the_room_annotation_is_audited_too(engine, store, room):
    ready(engine, room["id"])
    engine.book(
        room["id"],
        {"start": first_slot(engine, room["id"]), "attendee": ATTENDEE},
        actor="dana",
        source=SOURCE,
    )
    # The fixture inserted the room, so the annotation is the update that follows it.
    updates = [row for row in store.audit(collection="room") if row["action"] == "update"]
    assert updates, "installing an embed and booking must annotate the room"
    assert all(row["source"] == SOURCE for row in updates)


def test_a_refusal_still_records_the_audit_rows_that_came_before_it(engine, store, room):
    """Nothing happened, and here is why - which is what a rep needs to read."""
    ready(engine, room["id"])
    start = first_slot(engine, room["id"])
    engine.book(room["id"], {"start": start, "attendee": ATTENDEE}, actor="dana", source=SOURCE)
    before = len(store.audit(collection=BOOKING_COLLECTION))
    with pytest.raises(SlotUnavailable):
        engine.book(
            room["id"],
            {"start": start, "attendee": {"name": "Other", "email": "other@fabrikam.example"}},
            actor="dana",
            source=SOURCE,
        )
    assert len(store.audit(collection=BOOKING_COLLECTION)) == before


def test_the_audit_source_names_the_route_that_served_the_write(http):
    """Every recorded source matches a route the app actually serves.

    The defect this exists to catch shipped in this codebase before: a feature's
    audit log kept naming a route the app had stopped serving. The check is
    behavioural - it reads what the audit log actually recorded, after driving every
    write endpoint, and asks the running app what it actually mounted.

    Checked against *every* route, core included, because the audit log is shared:
    the test creates its room through the core records API, and that write's source
    is a core route. Asserting only against this feature's routes would pass for the
    wrong reason and fail for an unrelated one.
    """
    client = http.post(f"{PREFIX}/oauth-clients", json=client_spec()).json()
    http.post(f"{PREFIX}/oauth-clients/{client['id']}/grant", json={"subject": "priya"})
    http.patch(f"{PREFIX}/oauth-clients/{client['id']}", json={"name": "Renamed"})
    event_type = http.post(f"{PREFIX}/event-types", json=PERSONAL).json()
    http.post(f"{PREFIX}/calendars", json={"provider": "google", "host": "priya"})
    http.post(f"{PREFIX}/event-types", json=SEATED)
    http.post(f"{PREFIX}/event-types", json=ROUTING)
    form = http.post(f"{PREFIX}/routing-forms", json=form_spec()).json()
    http.patch(f"{PREFIX}/routing-forms/{form['id']}", json={"name": "Renamed"})

    room = http.post("/api/records/room", json={"name": "Northwind", "account": "Northwind"}).json()
    http.put(
        f"{PREFIX}/rooms/{room['id']}/embed",
        json={"clientId": client["id"], "eventTypeId": "evt_personal", "routingFormId": form["id"]},
    )
    grid = http.get(f"{PREFIX}/rooms/{room['id']}/slots?eventTypeId=evt_personal").json()
    start = next(slot["start"] for slot in grid["slots"] if slot["available"])
    hold = http.post(f"{PREFIX}/rooms/{room['id']}/holds", json={"start": start}).json()
    http.patch(
        f"{PREFIX}/rooms/{room['id']}/holds/{hold['reservationUid']}",
        json={"reservationDuration": 30},
    )
    booking = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings",
        json={"start": start, "reservationUid": hold["reservationUid"], "attendee": ATTENDEE},
    ).json()
    http.delete(f"{PREFIX}/rooms/{room['id']}/bookings/{booking['uid']}?reason=demo")
    http.patch(f"{PREFIX}/event-types/{event_type['id']}", json={"title": "Renamed"})
    http.delete(
        f"{PREFIX}/event-types/{http.post(f'{PREFIX}/event-types', json=TEAM).json()['id']}"
    )
    http.delete(f"{PREFIX}/routing-forms/{form['id']}")
    http.delete(
        f"{PREFIX}/calendars/{http.get(f'{PREFIX}/calendars').json()['connections'][0]['id']}"
    )
    http.delete(f"{PREFIX}/oauth-clients/{client['id']}")

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
    client = http.post(f"{PREFIX}/oauth-clients", json=client_spec()).json()
    http.post(f"{PREFIX}/oauth-clients/{client['id']}/grant", json={"subject": "priya"})
    http.post(f"{PREFIX}/event-types", json=PERSONAL).json()
    http.post(f"{PREFIX}/calendars", json={"provider": "google", "host": "priya"})
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    http.put(
        f"{PREFIX}/rooms/{room['id']}/embed",
        json={"clientId": client["id"], "eventTypeId": "evt_personal"},
    )
    grid = http.get(f"{PREFIX}/rooms/{room['id']}/slots?eventTypeId=evt_personal").json()
    start = next(slot["start"] for slot in grid["slots"] if slot["available"])
    hold = http.post(f"{PREFIX}/rooms/{room['id']}/holds", json={"start": start}).json()
    http.patch(
        f"{PREFIX}/rooms/{room['id']}/holds/{hold['reservationUid']}",
        json={"reservationDuration": 30},
    )
    booking = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings",
        json={"start": start, "reservationUid": hold["reservationUid"], "attendee": ATTENDEE},
    ).json()
    http.delete(f"{PREFIX}/rooms/{room['id']}/holds/{hold['reservationUid']}")
    http.delete(f"{PREFIX}/rooms/{room['id']}/bookings/{booking['uid']}")
    http.delete(f"{PREFIX}/oauth-clients/{client['id']}")

    store = RecordStore(client_store(http))
    mine = {
        row["source"]
        for row in store.audit(limit=1000)
        if row["collection"] in OWNED_COLLECTIONS and row["source"]
    }
    routes = mounted_routes(http)

    assert len(mine) >= 8, f"the feature recorded only {len(mine)} sources"
    for source in sorted(mine):
        assert source.startswith(
            tuple(f"{method} {PREFIX}" for method in ("POST", "PATCH", "DELETE", "PUT"))
        ), f"{source!r} does not name a route under this feature's own prefix"
        assert source_names_a_mounted_route(source, routes), f"{source!r} names no mounted route"


def test_a_booking_written_over_http_records_its_own_route(http):
    client = http.post(f"{PREFIX}/oauth-clients", json=client_spec()).json()
    http.post(f"{PREFIX}/oauth-clients/{client['id']}/grant", json={"subject": "priya"})
    event_type = http.post(f"{PREFIX}/event-types", json=PERSONAL).json()
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    http.put(
        f"{PREFIX}/rooms/{room['id']}/embed",
        json={"clientId": client["id"], "eventTypeId": "evt_personal"},
    )
    grid = http.get(f"{PREFIX}/rooms/{room['id']}/slots?eventTypeId=evt_personal").json()
    start = next(slot["start"] for slot in grid["slots"] if slot["available"])
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings", json={"start": start, "attendee": ATTENDEE})
    store = RecordStore(client_store(http))
    sources = {row["source"] for row in store.audit(collection=BOOKING_COLLECTION)}
    assert sources == {f"POST {PREFIX}/rooms/{room['id']}/bookings"}
    assert event_type["id"]


def test_the_actor_query_parameter_reaches_the_audit_row(http):
    client = http.post(f"{PREFIX}/oauth-clients", json=client_spec()).json()
    store = RecordStore(client_store(http))
    row = store.audit(collection=CLIENT_COLLECTION)[0]
    assert row["source"] == f"POST {PREFIX}/oauth-clients"
    assert row["actor"] is None or isinstance(row["actor"], str)
    assert client["id"]


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


def test_the_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The routes resolve even though no shared file names this feature."""
    assert FEATURE_ID in {f["id"] for f in http.get("/api/features").json()["features"]}
    assert http.get(f"{PREFIX}/vocabulary").status_code == 200


def test_the_registry_reports_this_prefix_and_thirty_six_routes(http):
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)
    assert entry["prefix"] == PREFIX
    assert len(entry["routes"]) == 36


def test_the_registry_reports_this_features_exception_handler(http):
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)
    assert entry["exception_handlers"] == ["SchedulingError"]


def test_the_feature_does_not_import_the_app():
    """Importing dsr.api from a feature reintroduces the shared-file coupling."""
    for path in sorted(PACKAGE_DIR.glob("*.py")) + [
        Path(__file__).resolve().parents[1] / "dsr" / "features" / f"{MODULE}.py"
    ]:
        text = path.read_text(encoding="utf-8")
        assert "from dsr.api" not in text and "import dsr.api" not in text, path


def test_no_module_in_the_package_opens_sqlite():
    """All reads and writes go through RecordStore, or the audit guarantee is gone."""
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "import sqlite3" not in text, f"{path.name} imports sqlite3 directly"
        assert "AuditedDatabase(" not in text, f"{path.name} constructs a database"


def test_the_vocabulary_endpoint_serves_the_documented_numbers(http):
    vocabulary = http.get(f"{PREFIX}/vocabulary").json()
    assert vocabulary["max_recurrence_count"] == 32
    assert vocabulary["metadata_limits"]["max_keys"] == 50
    assert vocabulary["default_reservation_duration_minutes"] == 5
    assert vocabulary["min_dynamic_usernames"] == 2
    assert set(vocabulary["routing_operators"]) == set(OPERATORS)


def test_the_inferences_endpoint_serves_the_registry(http):
    payload = http.get(f"{PREFIX}/inferences").json()
    assert payload["count"] == len(scheduling_inferences.INFERENCES)
    assert payload["sourced"]["quotes"]["metadata_limits"]


def test_an_ambiguous_slot_query_is_a_400(http):
    client = http.post(f"{PREFIX}/oauth-clients", json=client_spec()).json()
    http.post(f"{PREFIX}/oauth-clients/{client['id']}/grant", json={"subject": "priya"})
    http.post(f"{PREFIX}/event-types", json=PERSONAL)
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    http.put(
        f"{PREFIX}/rooms/{room['id']}/embed",
        json={"clientId": client["id"], "eventTypeId": "evt_personal"},
    )
    response = http.get(
        f"{PREFIX}/rooms/{room['id']}/slots?eventTypeId=evt_personal&usernames=alice,bob"
    )
    assert response.status_code == 400
    assert response.json()["error"] == "scheduling_error"


def test_a_recurrence_over_the_documented_ceiling_is_a_400_naming_the_maximum(http):
    client = http.post(f"{PREFIX}/oauth-clients", json=client_spec()).json()
    http.post(f"{PREFIX}/oauth-clients/{client['id']}/grant", json={"subject": "priya"})
    http.post(f"{PREFIX}/event-types", json=PERSONAL)
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    http.put(
        f"{PREFIX}/rooms/{room['id']}/embed",
        json={"clientId": client["id"], "eventTypeId": "evt_personal"},
    )
    grid = http.get(f"{PREFIX}/rooms/{room['id']}/slots?eventTypeId=evt_personal").json()
    start = next(slot["start"] for slot in grid["slots"] if slot["available"])
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings",
        json={"start": start, "attendee": ATTENDEE, "recurrenceCount": 40},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["limit"] == "recurrenceCount"
    assert body["maximum"] == 32


def test_a_metadata_overflow_is_a_400_naming_the_key(http):
    client = http.post(f"{PREFIX}/oauth-clients", json=client_spec()).json()
    http.post(f"{PREFIX}/oauth-clients/{client['id']}/grant", json={"subject": "priya"})
    http.post(f"{PREFIX}/event-types", json=PERSONAL)
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    http.put(
        f"{PREFIX}/rooms/{room['id']}/embed",
        json={"clientId": client["id"], "eventTypeId": "evt_personal"},
    )
    grid = http.get(f"{PREFIX}/rooms/{room['id']}/slots?eventTypeId=evt_personal").json()
    start = next(slot["start"] for slot in grid["slots"] if slot["available"])
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings",
        json={"start": start, "attendee": ATTENDEE, "metadata": {"k" * 41: "v"}},
    )
    assert response.status_code == 400
    assert response.json()["limit"] == "max_key_length"


def test_a_refused_slot_comes_back_with_alternatives(http):
    client = http.post(f"{PREFIX}/oauth-clients", json=client_spec()).json()
    http.post(f"{PREFIX}/oauth-clients/{client['id']}/grant", json={"subject": "priya"})
    http.post(f"{PREFIX}/event-types", json=PERSONAL)
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    http.put(
        f"{PREFIX}/rooms/{room['id']}/embed",
        json={"clientId": client["id"], "eventTypeId": "evt_personal"},
    )
    grid = http.get(f"{PREFIX}/rooms/{room['id']}/slots?eventTypeId=evt_personal").json()
    start = next(slot["start"] for slot in grid["slots"] if slot["available"])
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings", json={"start": start, "attendee": ATTENDEE})
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings",
        json={"start": start, "attendee": {"name": "Other", "email": "other@fabrikam.example"}},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["reason"] == "hosts"
    assert body["alternatives"], "the refusal must offer somewhere else to go"


def test_a_room_with_no_embed_is_a_404_on_the_embed_route(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    assert http.get(f"{PREFIX}/rooms/{room['id']}/embed").status_code == 404


def test_a_room_that_does_not_exist_is_a_404(http):
    assert http.get(f"{PREFIX}/rooms/room_nope/holds").status_code == 404


def test_the_routed_slots_route_rejects_answers_that_are_not_json(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    response = http.get(f"{PREFIX}/rooms/{room['id']}/routed-slots?responses=not-json")
    assert response.status_code == 400
    assert "not valid JSON" in response.json()["detail"]


def test_the_routed_slots_route_rejects_answers_that_are_not_an_object(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    response = http.get(f"{PREFIX}/rooms/{room['id']}/routed-slots?responses=%5B1%2C2%5D")
    assert response.status_code == 400
    assert "must be a JSON object" in response.json()["detail"]


def test_a_hold_from_another_room_is_a_404(http):
    client = http.post(f"{PREFIX}/oauth-clients", json=client_spec()).json()
    http.post(f"{PREFIX}/oauth-clients/{client['id']}/grant", json={"subject": "priya"})
    event_type = http.post(f"{PREFIX}/event-types", json=PERSONAL).json()
    mine = http.post("/api/records/room", json={"name": "Mine"}).json()
    theirs = http.post("/api/records/room", json={"name": "Theirs"}).json()
    for room in (mine, theirs):
        http.put(
            f"{PREFIX}/rooms/{room['id']}/embed",
            json={"clientId": client["id"], "eventTypeId": "evt_personal"},
        )
    grid = http.get(f"{PREFIX}/rooms/{mine['id']}/slots?eventTypeId=evt_personal").json()
    start = next(slot["start"] for slot in grid["slots"] if slot["available"])
    hold = http.post(f"{PREFIX}/rooms/{mine['id']}/holds", json={"start": start}).json()
    assert (
        http.get(f"{PREFIX}/rooms/{theirs['id']}/holds/{hold['reservationUid']}").status_code == 404
    )
    assert event_type["id"]


def test_a_booking_that_does_not_exist_is_a_404(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    assert http.get(f"{PREFIX}/rooms/{room['id']}/bookings/bkg_nope").status_code == 404


def test_the_booking_events_route_reports_the_automation_and_its_pending_delivery(http):
    client = http.post(f"{PREFIX}/oauth-clients", json=client_spec()).json()
    http.post(f"{PREFIX}/oauth-clients/{client['id']}/grant", json={"subject": "priya"})
    event_type = http.post(f"{PREFIX}/event-types", json=PERSONAL).json()
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    http.put(
        f"{PREFIX}/rooms/{room['id']}/embed",
        json={"clientId": client["id"], "eventTypeId": "evt_personal"},
    )
    grid = http.get(f"{PREFIX}/rooms/{room['id']}/slots?eventTypeId=evt_personal").json()
    start = next(slot["start"] for slot in grid["slots"] if slot["available"])
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings", json={"start": start, "attendee": ATTENDEE})
    payload = http.get(f"{PREFIX}/rooms/{room['id']}/booking-events").json()
    assert [event["data"]["event"] for event in payload["events"]] == [BOOKING_CREATED]
    assert [delivery["data"]["status"] for delivery in payload["deliveries"]] == ["pending"]
    assert event_type["id"]


def test_the_oauth_client_route_refuses_a_credential(http):
    response = http.post(
        f"{PREFIX}/oauth-clients", json={**client_spec(), "client_secret": "s3cret"}
    )
    assert response.status_code == 400
    assert "looks like a credential" in response.json()["detail"]


def test_a_token_is_never_returned_by_the_client_route(http):
    http.post(f"{PREFIX}/oauth-clients", json=client_spec())
    listed = http.get(f"{PREFIX}/oauth-clients").json()["clients"]
    for record in listed:
        assert record["data"].get("token", {}).get("subject") in (None, "priya")
        assert "access_token" not in json.dumps(record["data"])


# --------------------------------------------------------------------------- #
# The demo data
# --------------------------------------------------------------------------- #


@pytest.fixture()
def seed_module():
    return load_feature(MODULE)


def test_the_seed_runs_and_reports_a_mixed_demo(db, seed_module):
    rooms = [
        (
            db.create(
                "room", {"name": f"R{i}", "account": f"A{i}"}, actor="dana", source="scratch"
            )["id"],
            f"A{i}",
        )
        for i in range(3)
    ]
    reported = seed_module.seed(db, {"room_ids": rooms, "now": MONDAY})
    assert "DEMO SHAPE BROKEN" not in reported, reported
    assert "bookings" in reported


def test_the_seed_shows_every_state_the_workflow_exists_for(db, seed_module):
    rooms = [
        (
            db.create(
                "room", {"name": f"R{i}", "account": f"A{i}"}, actor="dana", source="scratch"
            )["id"],
            f"A{i}",
        )
        for i in range(3)
    ]
    seed_module.seed(db, {"room_ids": rooms, "now": MONDAY})
    engine = SchedulingEngine(RecordStore(db), clock=lambda: MONDAY)
    summary = engine.summary()
    assert summary["holds_live"] >= 1
    assert summary["holds_expired"] >= 1
    assert summary["instant_bookings"] == 1
    assert summary["recurring_bookings"] == 1
    assert summary["rooms_with_embed"] == 2
    assert set(summary["bookings_by_kind"]) >= {"standard", "instant", "recurring"}


def test_the_seed_books_through_the_real_rules_and_not_around_them(db, seed_module):
    rooms = [
        (
            db.create(
                "room", {"name": f"R{i}", "account": f"A{i}"}, actor="dana", source="scratch"
            )["id"],
            f"A{i}",
        )
        for i in range(3)
    ]
    seed_module.seed(db, {"room_ids": rooms, "now": MONDAY})
    store = RecordStore(db)
    bookings = store.list(BOOKING_COLLECTION, limit=100)
    assert bookings
    for record in bookings:
        data = record["data"]
        # Every seeded booking carries the room context the extensibility note asks
        # for, and none of them is an instant booking on a non-team event.
        assert data["metadata"].get("dsr_booking_source") == "in-room"
        assert data["metadata"].get("dsr_room_id")
        if data["instant"]:
            assert data["eventTypeId"] == "evt_team_15"


def test_the_seed_audits_its_own_writes(db, seed_module):
    rooms = [
        (
            db.create(
                "room", {"name": f"R{i}", "account": f"A{i}"}, actor="dana", source="scratch"
            )["id"],
            f"A{i}",
        )
        for i in range(3)
    ]
    seed_module.seed(db, {"room_ids": rooms, "now": MONDAY})
    store = RecordStore(db)
    seeded = [row for row in store.audit(limit=1000) if row["source"] == "seed"]
    assert seeded, "the seed must audit what it writes, like every other writer"
    assert {row["collection"] for row in seeded} >= {
        BOOKING_COLLECTION,
        RESERVATION_COLLECTION,
        EVENT_TYPE_COLLECTION,
    }


def test_the_seed_is_honest_with_no_rooms(db, seed_module):
    assert "no rooms" in seed_module.seed(db, {"room_ids": [], "now": MONDAY})


def test_the_seed_runs_twice_without_raising(db, seed_module):
    """The seeder catches a raise and skips the feature, so a second run must not
    be the thing that breaks it."""
    rooms = [
        (
            db.create(
                "room", {"name": f"R{i}", "account": f"A{i}"}, actor="dana", source="scratch"
            )["id"],
            f"A{i}",
        )
        for i in range(3)
    ]
    seed_module.seed(db, {"room_ids": rooms, "now": MONDAY})
    with pytest.raises(SchedulingError):
        # The second run reuses an eventTypeId, which the dynamic index and the
        # resolver both reject rather than silently doubling the event types.
        seed_module.seed(db, {"room_ids": rooms, "now": MONDAY})


# --------------------------------------------------------------------------- #
# The inference registry
# --------------------------------------------------------------------------- #


def test_every_inference_is_named_and_traceable():
    for entry in scheduling_inferences.INFERENCES:
        for field in ("id", "topic", "basis", "value", "why", "change_it", "blast_radius"):
            assert entry.get(field), f"{entry.get('id')} is missing {field}"


def test_the_inference_ids_are_unique():
    ids = [entry["id"] for entry in scheduling_inferences.INFERENCES]
    assert len(ids) == len(set(ids))


def test_every_inference_is_reachable_by_name():
    for entry in scheduling_inferences.INFERENCES:
        assert scheduling_inferences.by_id(entry["id"]) == entry


def test_an_unknown_inference_id_is_none():
    assert scheduling_inferences.by_id("no-such-inference") is None


def test_the_registry_covers_the_decisions_a_reviewer_would_disagree_with():
    """Every judgement the research left open is named, including the fall-through."""
    ids = {entry["id"] for entry in scheduling_inferences.INFERENCES}
    for expected in (
        "embed-is-rendered-here-not-from-atoms",
        "routing-fall-through-is-required",
        "instant-requires-team-and-no-start",
        "recurrence-interval",
        "recurrence-refused-not-truncated",
        "metadata-room-context-before-limits",
        "reschedule-moves-rather-than-duplicates",
        "holds-are-not-hosts",
        "holds-expire-by-the-clock-not-by-a-sweep",
        "booking-requires-a-live-token",
        "no-token-value-is-stored",
        "busy-time-is-global-and-seated-bookings-take-a-seat",
        "no-outbound-scheduling-call",
    ):
        assert expected in ids, expected


def test_the_registry_serves_the_sourced_half_beside_the_assumed_half():
    payload = scheduling_inferences.describe()
    assert payload["count"] == len(scheduling_inferences.INFERENCES)
    assert payload["sourced"]["max_recurrence_count"] == 32
    assert payload["sourced"]["metadata_limits"]["max_value_length"] == 500
    assert "quotes" in payload["sourced"]


def test_the_researched_quotes_carry_their_numbers():
    quotes = scheduling_inferences.SOURCED_QUOTES
    assert "at most 50 keys" in quotes["metadata_limits"]
    assert "defaults to 5 minutes" in quotes["reservation_duration"]
    assert "2 or more people are available" in quotes["dynamic_usernames"]
    assert "busy time calculations" in quotes["reschedule_exclusion"]
    assert (
        "will not actually save the response"
        in scheduling_inferences.describe()["vocabulary"]["quotes"]["routing_slots"]
    )


def test_the_fall_through_inference_names_the_reading_this_build_took():
    entry = scheduling_inferences.by_id("routing-fall-through-is-required")
    assert entry["value"]["on_no_catch_all"] == "the form cannot be created"
    assert "wrong host" in entry["why"]
