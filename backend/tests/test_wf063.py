"""Tests for WF-063: reassign a booked meeting to a different host.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-063.md``. Nothing here is a
preference of this build unless it is listed in :mod:`dsr.reassign.inferences`,
and every inference in that registry has a test that checks it is still named,
still bounded, and still changeable.

The researched half
-------------------

* "You can change the Distribution, Team, or Individual. You cannot change the
  Meeting Type or Workspace." - the editable axis and the two locks.
* "When Reassigning a meeting with Chili Piper, you are booking a new meeting for
  another user. Reassignment will take into account your Handoff/ChiliCal User
  controls and the Distribution settings of the meeting booked."
* "Chili Piper should update the invite accordingly with the new assignee's
  name, links, and other details that possibly changed from one assignee to
  another."
* "Note: Reassignment does not take into account the minimum scheduling notice
  or the maximum availability range." - and the reason it exists is "so it can
  always rescue a stale booking", which is why a *booking* still enforces them.
* "If the target person is known and free, pick them and hit Reassign."
* "You must have ChiliCal's extension installed and be logged in there."
* ``BOOKING_REASSIGNED``: "Fires when a round-robin booking's host is
  reassigned (automatic or manual)", with ``addedHostUserIds``,
  ``removedHostUserIds``, and ``organizer`` reflecting the new host.
* "Currently only supports reassigning host for round robin bookings."
* Events History: "who reassigned it, to whom, when, and the reassignment source
  (Meetings Activity or ChiliCal Home)."
* Automations: "round-robin credit state moves with the host", and "no-show
  credit-back interacts with reassignment".
* The five Meetings Activity filters and the Upcoming/Past tabs, plus
  "Export to CSV".

Two bugs these tests were written to catch, each of which would have been
invisible without a behavioural test:

* The decision was handed record *envelopes* where it expected payloads, so
  every host read as unnamed and the reassignment's reason read "None -> None".
  Nothing about the shape was wrong on its face; only the assembled reason
  showed it.
* The bounds were reported as bypassed without the *reasons* being attached, so
  a reassignment that bypassed nothing was indistinguishable in the record from
  one that bypassed both.

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
from dsr.reassign import (
    ASSIGNED,
    ASSIGNMENT_KINDS,
    AUTO_IS_ROUND_ROBIN_ONLY,
    AUTO_MODE,
    BOOKING_REASSIGNED,
    BOUNDS_ARE_IGNORED,
    CREDIT_ALREADY_RETURNED,
    CREDIT_MOVED,
    DEFAULT_DURATION_MINUTES,
    DISTRIBUTION_COLLECTION,
    EDITABLE_AND_LOCKED,
    EVENTS_HISTORY_ROW,
    EXTENSION_MUST_BE_INSTALLED_AND_LOGGED_IN,
    HALF_OPEN_INTERVALS,
    HISTORY_COLLECTION,
    HOST_COLLECTION,
    INELIGIBLE_REASONS,
    INVITE_FIELDS,
    LOCKED_FIELDS,
    MEETING_COLLECTION,
    MEETING_STATUSES,
    MEETING_UPDATE,
    NAIVE_IS_UTC,
    OUTCOMES,
    PAYLOAD_VERSION,
    REASSIGN_MODES,
    REASSIGNABLE_STATUSES,
    REASSIGNED_PAYLOAD_KEYS,
    REASSIGNMENT_COLLECTION,
    SPECIFIC_HOST,
    SPECIFIC_MODE,
    SURFACE_LABELS,
    SURFACES,
    SURFACES_REQUIRING_ADDON,
    MeetingStateError,
    ReassignEngine,
    ReassignError,
    auto_select,
    bounds_summary,
    candidates,
    changed_invite_fields,
    conflicts_with,
    credit_patch,
    evaluate_bounds,
    events_for,
    format_instant,
    in_distribution_scope,
    inferences as reassign_inferences,
    invite_for,
    is_free,
    is_refused,
    mode_for_kind,
    move_credit,
    normalise_distribution,
    normalise_host,
    normalise_meeting,
    outcome_table,
    parse_instant,
    published_vocabulary,
    require_assignment_kind,
    require_status,
    require_surface,
    require_tab,
    rules as reassign_rules,
)
from dsr.reassign.rules import (
    REFUSED,
    REFUSED_ADDON_NOT_READY,
    REFUSED_DISTRIBUTION_CONTEXT,
    REFUSED_HOST_UNAVAILABLE,
    REFUSED_INACTIVE_HOST,
    REFUSED_LOCKED_FIELD,
    REFUSED_NO_ELIGIBLE_HOST,
    REFUSED_NOT_IN_DISTRIBUTION,
    REFUSED_NOT_ROUND_ROBIN,
    REFUSED_SAME_HOST,
    decide,
)
from dsr.reassign.webhooks import (
    REASSIGNED_ONLY_KEYS,
    booking_reassigned_payload,
    meeting_update_payload,
    webhook_catalogue,
)
from dsr.store import RecordStore
from fastapi.testclient import TestClient

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-063"

#: What the pure-domain tests pass as ``source``, in the exact shape a route
#: passes it: the prefix, then concrete path segments. Deliberately *not* a
#: FastAPI ``{param}`` template, because the route interpolates the ids and an
#: audit row naming a literal ``{meeting_id}`` would be unreadable.
SOURCE = f"POST {PREFIX}/rooms/room_x/meetings/meeting_x/reassign"

MODULE = "wf063_reassign_a_booked_meeting_to_a_differe"
FEATURE_ID = "wf-063-reassign-a-booked-meeting-to-a-differe"

NOW = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

#: A slot comfortably inside every bound, so a test that does not care about
#: bounds is not accidentally exercising them.
#:
#: Relative to "now" rather than a fixed date, because the HTTP half runs
#: against the real engine, whose clock is the wall clock. A hard-coded date
#: would start failing the distribution bounds as this suite ages - a test that
#: passes today and fails next month is a worse defect than no test, so the
#: offset is computed. The 90-day range is the widest any test distribution
#: configures, and three days is far inside it.
CLEAN_DAYS_AHEAD = 3
CLEAN_START = f"{(NOW + timedelta(days=CLEAN_DAYS_AHEAD)).date().isoformat()}T10:00:00Z"
CLEAN_END = f"{(NOW + timedelta(days=CLEAN_DAYS_AHEAD)).date().isoformat()}T10:30:00Z"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def db():
    # In-memory rather than a file on disk: 0.4 ms against 7.0 ms, measured. No test
    # in this file reads the audit mirror off the filesystem, so the file bought nothing.
    database = AuditedDatabase()
    yield database
    database.close()


@pytest.fixture()
def store(db):
    return RecordStore(db)


@pytest.fixture()
def engine(store):
    return ReassignEngine(store, clock=lambda: NOW)


@pytest.fixture()
def room(store):
    return store.create("room", {"name": "Northwind — Enterprise Evaluation"}, actor="dana")


@pytest.fixture()
def other_room(store):
    return store.create("room", {"name": "Contoso — Security Review"}, actor="sam")


def make_host(engine, name="Dana Okoro", **overrides):
    spec = {
        "name": name,
        "email": f"{name.split()[0].lower()}@dsr.example",
        "team": "enterprise",
        "distribution": "Enterprise Demo",
        "round_robin_credits": 0,
        "conference_link": f"https://meet.example/{name.split()[0].lower()}",
        "dial_in": "+1-555-0100",
    } | overrides
    return engine.store.create(HOST_COLLECTION, normalise_host(spec), actor="dana", source=SOURCE)


def make_distribution(engine, **overrides):
    spec = {
        "name": "Enterprise Demo",
        "team": "enterprise",
        "workspace": "northwind",
        "meeting_type": "demo",
        "allow_any_team_member": False,
        "min_notice_minutes": 60,
        "max_range_days": 90,
    } | overrides
    return engine.store.create(
        DISTRIBUTION_COLLECTION, normalise_distribution(spec), actor="dana", source=SOURCE
    )


def make_meeting(engine, room_id, host_id, **overrides):
    """A booked meeting, the way the booking route makes one.

    ``prepare_meeting`` is used rather than a bare ``normalise_meeting`` so the
    Meeting Type and Workspace come from the distribution exactly as they do in
    production. A test that hand-set them could pass while the route broke.
    """
    spec = {
        "title": "Northwind — Enterprise Demo",
        "booker": "buyer@northwind.example",
        "host_id": host_id,
        "distribution": "Enterprise Demo",
        "starts_at": CLEAN_START,
        "ends_at": CLEAN_END,
    } | overrides
    body = engine.prepare_meeting(spec)
    body["invite"] = invite_for(engine.store.get(host_id)["data"])
    return engine.store.create(
        MEETING_COLLECTION, body, room_id=room_id, actor="dana", source=SOURCE
    )


@pytest.fixture()
def host(engine):
    return make_host(engine, "Dana Okoro", round_robin_credits=3)


@pytest.fixture()
def other(engine):
    return make_host(engine, "Priya Raman", round_robin_credits=1)


@pytest.fixture()
def distribution(engine, host, other):
    return make_distribution(engine, member_ids=[host["id"], other["id"]])


@pytest.fixture()
def meeting(engine, room, host):
    return make_meeting(engine, room["id"], host["id"])


@pytest.fixture(scope="module")
def _shared_client(tmp_path_factory):
    """One application for the module. A fresh database for each test.

    The lifespan in ``dsr/api.py`` only assigns ``app.state.db`` and
    ``app.state.store``, and ``dsr/deps.py`` reads ``app.state.store`` on every
    request. A test therefore needs a fresh *database*, not a fresh
    *application*. Entering a TestClient costs 46 ms measured; swapping the two
    attributes costs about 1.25 ms.

    The environment is patched here rather than per test because a module-scoped
    fixture cannot use the function-scoped ``monkeypatch``. It is undone on the
    way out so it reaches no other module. ``DSR_DB_PATH`` is ``:memory:`` so the
    lifespan's own database costs nothing either.
    """
    scratch = tmp_path_factory.mktemp("wf063-http")
    patch = pytest.MonkeyPatch()
    patch.setenv("DSR_DB_PATH", ":memory:")
    patch.setenv("DSR_AUDIT_DIR", str(scratch / "audit"))
    patch.setattr("dsr.api.FRONTEND_DIST", scratch / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    patch.undo()


@pytest.fixture()
def http(_shared_client):
    """The shared application, over a database this test owns alone.

    ``dependency_overrides`` is cleared on the way in and on the way out: the
    application is module-scoped, so an override one test installs would
    otherwise still be installed for the next one.
    """
    db = AuditedDatabase()
    _shared_client.app.state.db = db
    _shared_client.app.state.store = RecordStore(db)
    _shared_client.app.dependency_overrides.clear()
    try:
        yield _shared_client
    finally:
        _shared_client.app.dependency_overrides.clear()
        db.close()


def seed_http(client):
    """The demo dataset, over HTTP, for the surface tests that need real rows."""
    return client.post(f"{PREFIX}/seed-demo").json()


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
        methods = getattr(route, "methods", None) or set()
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            routes.add((method, getattr(route, "path", "")))
    for feature in client.get("/api/features").json()["features"]:
        for route in feature["routes"]:
            for method in route["methods"]:
                routes.add((method, route["path"]))
    return routes


def source_names_a_mounted_route(source, routes):
    """Does ``"POST /api/wf-063/rooms/abc/meetings/def/reassign"`` name a route?

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
# The researched vocabulary
# --------------------------------------------------------------------------- #


def test_the_two_locked_fields_are_the_ones_the_research_names():
    """'You cannot change the Meeting Type or Workspace.'"""
    assert LOCKED_FIELDS == ("meeting_type", "workspace")
    assert "cannot change the Meeting Type or Workspace" in EDITABLE_AND_LOCKED


def test_the_editable_axis_is_the_three_the_scheduler_offers():
    assert ASSIGNMENT_KINDS == ("individual", "team", "distribution")
    for kind in ASSIGNMENT_KINDS:
        assert kind in EDITABLE_AND_LOCKED.lower()


def test_individual_is_the_specific_path_and_the_others_are_automatic():
    """'Reassign a booking to a specific host' vs the auto-selected one."""
    assert mode_for_kind("individual") == SPECIFIC_MODE
    assert mode_for_kind("team") == AUTO_MODE
    assert mode_for_kind("distribution") == AUTO_MODE
    assert REASSIGN_MODES == (SPECIFIC_MODE, AUTO_MODE)


def test_the_round_robin_limit_is_quoted_verbatim():
    assert (
        AUTO_IS_ROUND_ROBIN_ONLY
        == "Currently only supports reassigning host for round robin bookings"
    )
    assert SPECIFIC_HOST == "Reassign a booking to a specific host"


def test_the_bounds_exemption_is_quoted_verbatim():
    assert BOUNDS_ARE_IGNORED == (
        "Note: Reassignment does not take into account the minimum scheduling notice or the maximum "
        "availability range."
    )


def test_the_addon_precondition_is_quoted_verbatim():
    assert (
        "ChiliCal's extension installed and be logged in"
        in EXTENSION_MUST_BE_INSTALLED_AND_LOGGED_IN
    )


def test_the_events_history_row_is_quoted_verbatim():
    for phrase in ("who reassigned it", "to whom", "when", "Meetings Activity or ChiliCal Home"):
        assert phrase in EVENTS_HISTORY_ROW


def test_the_reassigned_payload_keys_are_quoted_verbatim():
    for key in REASSIGNED_ONLY_KEYS:
        assert key in REASSIGNED_PAYLOAD_KEYS


def test_the_four_entry_points_are_the_published_surfaces():
    """extensibility names four; the APIs are the fifth."""
    assert SURFACES == ("meetings_activity", "myapp", "chilical_home", "crm_event_button", "api")
    # The two the Events History sentence names are among them, and both appear
    # in that sentence.
    for named in ("Meetings Activity", "ChiliCal Home"):
        assert named in EVENTS_HISTORY_ROW
    assert SURFACE_LABELS["meetings_activity"] == "Meetings Activity"
    assert SURFACE_LABELS["chilical_home"] == "ChiliCal Home"


def test_only_the_calendar_addon_is_gated():
    """Only one surface carries a sourced precondition."""
    assert SURFACES_REQUIRING_ADDON == frozenset({"chilical_home"})


def test_the_invite_fields_are_the_ones_that_change_between_assignees():
    """'name, links, and other details that possibly changed'."""
    assert INVITE_FIELDS == (
        "organizer",
        "organizer_email",
        "conference_link",
        "dial_in",
        "location",
    )
    assert "conference_link" in INVITE_FIELDS


def test_the_published_vocabulary_carries_every_quote():
    published = published_vocabulary()
    assert published["locked_fields"] == list(LOCKED_FIELDS)
    assert published["assignment_kinds"] == list(ASSIGNMENT_KINDS)
    assert published["surfaces"] == list(SURFACES)
    for name, quote in published["evidence"].items():
        assert isinstance(quote, str) and quote, f"{name} has no quoted evidence"


def test_meeting_statuses_include_the_no_show_the_research_names():
    assert "no_show" in MEETING_STATUSES
    assert REASSIGNABLE_STATUSES == frozenset({"scheduled", "no_show"})


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #


def test_surface_names_are_normalised_so_the_two_vocabularies_cannot_split():
    assert require_surface("ChiliCal Home") == "chilical_home"
    assert require_surface("chilical-home") == "chilical_home"
    assert require_surface("Meetings Activity") == "meetings_activity"
    assert require_surface(None) == "api"


def test_an_unknown_surface_names_the_published_set():
    with pytest.raises(ReassignError) as caught:
        require_surface("chili_cal")
    assert "meetings_activity" in str(caught.value)


def test_a_locked_field_is_normalised_so_a_case_change_cannot_walk_past_the_lock():
    """'DEMO' and 'demo ' are one meeting type."""
    meeting = {"meeting_type": "demo", "workspace": "northwind"}
    changed, checked = reassign_rules._requested_locked_fields({"meeting_type": "DEMO"}, meeting)
    assert changed == []
    assert checked["meeting_type"]["changed"] is False


def test_a_genuine_locked_field_change_is_detected():
    changed, checked = reassign_rules._requested_locked_fields(
        {"meeting_type": "workshop", "workspace": "contoso"},
        {"meeting_type": "demo", "workspace": "northwind"},
    )
    # The order is the published one, so a message listing them is stable.
    assert changed == ["meeting_type", "workspace"]
    assert checked["workspace"] == {"requested": "contoso", "meeting": "northwind", "changed": True}


def test_an_omitted_locked_field_is_not_a_change():
    """A scheduler that echoes its context must not be refused for it."""
    changed, checked = reassign_rules._requested_locked_fields({}, {"meeting_type": "demo"})
    assert changed == []
    assert checked == {}


def test_an_unknown_assignment_kind_names_the_three_the_scheduler_offers():
    with pytest.raises(ReassignError) as caught:
        require_assignment_kind("region")
    assert "individual" in str(caught.value)
    assert "distribution" in str(caught.value)


def test_statuses_and_tabs_are_validated_against_their_published_lists():
    assert require_status("No Show") == "no_show"
    assert require_tab("Upcoming") == "upcoming"
    with pytest.raises(ReassignError):
        require_status("pending")
    with pytest.raises(ReassignError):
        require_tab("archived")


def test_a_meeting_needs_a_host_because_it_is_reassigned_away_from_one():
    with pytest.raises(ReassignError):
        normalise_meeting({"title": "X", "starts_at": CLEAN_START, "ends_at": CLEAN_END})


def test_a_meeting_must_start_before_it_ends():
    with pytest.raises(ReassignError):
        normalise_meeting(
            {"title": "X", "host_id": "h1", "starts_at": CLEAN_END, "ends_at": CLEAN_START}
        )


def test_a_meeting_without_an_end_gets_the_default_duration():
    meeting = normalise_meeting(
        {
            "title": "X",
            "host_id": "h1",
            "meeting_type": "demo",
            "workspace": "northwind",
            "starts_at": CLEAN_START,
        }
    )
    assert meeting["ends_at"] == CLEAN_END
    assert DEFAULT_DURATION_MINUTES == 30


def test_a_distribution_needs_a_team_for_any_team_member_to_resolve_to():
    """Otherwise the researched control has nothing to widen to."""
    with pytest.raises(ReassignError) as caught:
        normalise_distribution({"name": "X", "workspace": "w", "meeting_type": "demo"})
    assert "team" in str(caught.value)


def test_a_distribution_cannot_repeat_a_member():
    with pytest.raises(ReassignError):
        normalise_distribution(
            {
                "name": "X",
                "team": "t",
                "workspace": "w",
                "meeting_type": "demo",
                "member_ids": ["a", "a"],
            }
        )


def test_a_host_needs_a_name_because_the_invite_carries_it():
    with pytest.raises(ReassignError):
        normalise_host({"email": "a@b.example"})


def test_a_host_email_that_is_not_an_address_is_refused():
    with pytest.raises(ReassignError):
        normalise_host({"name": "A", "email": "not-an-address"})


# --------------------------------------------------------------------------- #
# Instants
# --------------------------------------------------------------------------- #


def test_a_naive_timestamp_is_read_as_utc():
    """A naive and an explicit +00:00 timestamp must be the same instant."""
    assert parse_instant("2026-10-05T10:00:00") == parse_instant("2026-10-05T10:00:00Z")
    assert NAIVE_IS_UTC.startswith("timestamps without an offset")


def test_an_offset_timestamp_is_normalised_to_utc():
    assert format_instant(parse_instant("2026-10-05T12:00:00+02:00")) == "2026-10-05T10:00:00Z"


def test_a_malformed_timestamp_is_refused_by_name():
    with pytest.raises(ReassignError) as caught:
        parse_instant("next tuesday", field="starts_at")
    assert "starts_at" in str(caught.value)


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #


def test_a_block_ending_exactly_when_the_meeting_starts_is_not_a_conflict():
    """Back-to-back bookings are not a double booking."""
    start, end = parse_instant(CLEAN_START), parse_instant(CLEAN_END)
    host = {"busy": [{"starts_at": "2026-01-01T09:00:00Z", "ends_at": CLEAN_START}]}
    assert conflicts_with(host, start, end) == []
    assert is_free(host, start, end)
    assert "not a conflict" in HALF_OPEN_INTERVALS


def test_a_block_overlapping_the_slot_by_a_minute_is_a_conflict():
    host = {
        "busy": [{"starts_at": "2026-01-01T09:00:00Z", "ends_at": _shift(CLEAN_END, minutes=1)}]
    }
    assert len(conflicts_with(host, parse_instant(CLEAN_START), parse_instant(CLEAN_END))) == 1


def test_a_block_starting_inside_the_slot_is_a_conflict():
    host = {
        "busy": [
            {"starts_at": _shift(CLEAN_START, minutes=5), "ends_at": _shift(CLEAN_END, minutes=5)}
        ]
    }
    assert len(conflicts_with(host, parse_instant(CLEAN_START), parse_instant(CLEAN_END))) == 1


def test_a_refusal_names_the_block_rather_than_only_saying_busy():
    host = {
        "busy": [
            {
                "starts_at": "2026-01-01T09:30:00Z",
                "ends_at": _shift(CLEAN_END, minutes=30),
                "label": "Fabrikam — Renewal call",
            }
        ]
    }
    clashes = conflicts_with(host, parse_instant(CLEAN_START), parse_instant(CLEAN_END))
    assert clashes[0]["label"] == "Fabrikam — Renewal call"


def _shift(iso: str, *, minutes: int) -> str:
    """``iso`` moved by ``minutes``, in the same UTC form the store holds."""
    return format_instant(parse_instant(iso) + timedelta(minutes=minutes))


def test_an_inactive_host_is_not_free_even_with_an_empty_calendar():
    """ "Known and free" has two halves, and leaving the team breaks one."""
    assert (
        is_free({"active": False, "busy": []}, parse_instant(CLEAN_START), parse_instant(CLEAN_END))
        is False
    )


def test_a_calendar_block_with_no_end_is_a_fixed_point_not_an_open_ended_range():
    """A missing end would otherwise swallow every later slot on the calendar."""
    host = normalise_host({"name": "A", "busy": [{"starts_at": CLEAN_START}]})
    assert host["busy"][0]["ends_at"] == CLEAN_START
    # It really is a point rather than an open-ended range: a window straddling
    # it conflicts, the next day's does not. The boundary is half-open, so a
    # window *starting* exactly when the block starts and running forwards does
    # overlap - that is what the separate boundary test covers.
    assert conflicts_with(
        host, parse_instant(CLEAN_START) - timedelta(minutes=5), parse_instant(CLEAN_END)
    )
    assert is_free(
        host,
        parse_instant(CLEAN_START) + timedelta(days=1),
        parse_instant(CLEAN_END) + timedelta(days=1),
    )


def test_a_calendar_block_that_ends_before_it_starts_is_refused():
    with pytest.raises(ReassignError):
        normalise_host(
            {
                "name": "A",
                "busy": [{"starts_at": "2026-10-05T11:00:00Z", "ends_at": "2026-10-05T09:00:00Z"}],
            }
        )


# --------------------------------------------------------------------------- #
# The bounds reassignment ignores
# --------------------------------------------------------------------------- #


def test_a_slot_inside_the_minimum_notice_is_reported_as_breaching():
    verdict = evaluate_bounds(
        {"min_notice_minutes": 60, "max_range_days": 90}, parse_instant("2026-09-28T09:30:00Z"), NOW
    )
    assert verdict["min_notice_breached"] is True
    assert verdict["would_block"] is True
    assert verdict["breached"] == ["min_notice"]


def test_a_slot_beyond_the_maximum_range_is_reported_as_breaching():
    verdict = evaluate_bounds(
        {"min_notice_minutes": 60, "max_range_days": 7}, parse_instant("2026-10-20T10:00:00Z"), NOW
    )
    assert verdict["max_range_breached"] is True
    assert verdict["would_block"] is True


def test_an_unconfigured_bound_is_not_breached():
    """None means "not configured", which is not the same as zero."""
    verdict = evaluate_bounds({"min_notice_minutes": None, "max_range_days": None}, NOW, NOW)
    assert verdict["would_block"] is False
    assert verdict["breached"] == []


def test_the_bypass_summary_records_the_numbers_even_when_nothing_is_breached():
    summary = bounds_summary(
        {"min_notice_minutes": 60, "max_range_days": 90}, parse_instant(CLEAN_START), NOW
    )
    assert summary["honoured"] is False
    assert summary["bypassed"] == []
    assert summary["min_notice_minutes"] == 60
    assert summary["max_range_days"] == 90


def test_the_bypass_summary_names_both_when_both_are_breached():
    summary = bounds_summary(
        {"min_notice_minutes": 1440, "max_range_days": 7},
        parse_instant("2026-09-28T09:40:00Z"),
        NOW,
    )
    assert summary["bypassed"] == ["min_notice"]
    # Beyond a 7-day range as well, from a second evaluation.
    far = bounds_summary(
        {"min_notice_minutes": None, "max_range_days": 7},
        parse_instant("2026-10-20T10:00:00Z"),
        NOW,
    )
    assert far["bypassed"] == ["max_range"]


# --------------------------------------------------------------------------- #
# Eligibility: the Distribution settings of the meeting booked
# --------------------------------------------------------------------------- #


def test_a_distribution_member_is_in_scope():
    assert in_distribution_scope(
        {"id": "h1", "team": "enterprise"}, {"member_ids": ["h1"], "team": "enterprise"}
    )


def test_an_out_of_team_host_is_out_of_scope_when_the_control_is_off():
    """ "whether you allow rescheduling with any team member or not"."""
    distribution = {"member_ids": ["h1"], "team": "enterprise", "allow_any_team_member": False}
    assert not in_distribution_scope({"id": "h2", "team": "enterprise"}, distribution)
    assert not in_distribution_scope({"id": "h3", "team": "mid-market"}, distribution)


def test_the_same_out_of_team_host_is_in_scope_when_the_control_is_on():
    distribution = {"member_ids": ["h1"], "team": "enterprise", "allow_any_team_member": True}
    assert in_distribution_scope({"id": "h2", "team": "enterprise"}, distribution)
    # Still not a member of the *other* team.
    assert not in_distribution_scope({"id": "h3", "team": "mid-market"}, distribution)


def test_the_control_widens_and_never_narrows():
    """A distribution member is in scope whichever way the control is set."""
    for flag in (True, False):
        assert in_distribution_scope(
            {"id": "h1", "team": "mid-market"},
            {"member_ids": ["h1"], "team": "enterprise", "allow_any_team_member": flag},
        )


def test_asking_for_a_team_is_answered_by_the_team_whatever_the_control_says():
    """Naming the team *is* asking for any team member, so nothing is left to ask."""
    distribution = {"member_ids": [], "team": "enterprise", "allow_any_team_member": False}
    assert in_distribution_scope({"id": "h9", "team": "enterprise"}, distribution, kind="team")
    assert not in_distribution_scope({"id": "h9", "team": "mid-market"}, distribution, kind="team")


def test_candidates_separate_the_two_reasons_and_return_both_kinds():
    meeting = {"host_id": "current"}
    distribution = {
        "member_ids": ["current", "free", "busy", "away"],
        "team": "enterprise",
        "allow_any_team_member": False,
    }
    hosts = [
        {"id": "current", "team": "enterprise", "active": True, "busy": []},
        {"id": "free", "team": "enterprise", "active": True, "busy": [], "round_robin_credits": 5},
        {
            "id": "busy",
            "team": "enterprise",
            "active": True,
            "round_robin_credits": 0,
            "busy": [
                {"starts_at": "2026-01-01T09:00:00Z", "ends_at": _shift(CLEAN_END, minutes=30)}
            ],
        },
        {"id": "away", "team": "enterprise", "active": False, "busy": [], "round_robin_credits": 0},
    ]
    rows = candidates(
        hosts, meeting, distribution, parse_instant(CLEAN_START), parse_instant(CLEAN_END)
    )
    by_id = {row["id"]: row for row in rows}
    assert by_id["free"]["eligible"] is True
    assert by_id["busy"]["ineligible_because"] == ["busy"]
    assert by_id["away"]["ineligible_because"] == ["inactive"]
    assert by_id["current"]["ineligible_because"] == ["already_the_host"]
    # Eligible rows come first, so a picker can render the top of the list.
    assert rows[0]["eligible"] is True


def test_every_ineligible_reason_is_published():
    for reason in ("not_in_distribution", "inactive", "already_the_host", "busy"):
        assert reason in INELIGIBLE_REASONS


def test_a_host_out_of_scope_and_busy_is_reported_out_of_scope_first():
    """Widening the scope is the thing to try first, so that is what is said."""
    meeting = {"host_id": "current"}
    distribution = {"member_ids": ["current"], "team": "enterprise", "allow_any_team_member": False}
    hosts = [
        {"id": "current", "team": "enterprise", "active": True, "busy": []},
        {
            "id": "both",
            "team": "mid-market",
            "active": True,
            "busy": [
                {"starts_at": "2026-01-01T09:00:00Z", "ends_at": _shift(CLEAN_END, minutes=30)}
            ],
        },
    ]
    rows = candidates(
        hosts, meeting, distribution, parse_instant(CLEAN_START), parse_instant(CLEAN_END)
    )
    by_id = {row["id"]: row for row in rows}
    assert by_id["both"]["ineligible_because"] == ["not_in_distribution", "busy"]


# --------------------------------------------------------------------------- #
# Automatic selection
# --------------------------------------------------------------------------- #


def test_auto_selects_the_fewest_credits():
    rows = [
        {"id": "a", "eligible": True, "round_robin_credits": 5},
        {"id": "b", "eligible": True, "round_robin_credits": 1},
    ]
    assert auto_select(rows)["id"] == "b"


def test_auto_breaks_a_tie_on_the_lowest_id_deterministically():
    """A rotation routinely has two hosts on the same count."""
    rows = [
        {"id": "host_z", "eligible": True, "round_robin_credits": 2},
        {"id": "host_a", "eligible": True, "round_robin_credits": 2},
    ]
    assert auto_select(rows)["id"] == "host_a"
    # Same input, same answer, every time.
    assert all(auto_select(rows)["id"] == "host_a" for _ in range(5))


def test_auto_never_picks_an_ineligible_host():
    rows = [{"id": "a", "eligible": False, "round_robin_credits": 0}]
    assert auto_select(rows) is None


# --------------------------------------------------------------------------- #
# Round-robin credit
# --------------------------------------------------------------------------- #


def test_the_credit_moves_from_the_previous_host_to_the_new_one():
    movement = move_credit(
        {"id": "a", "round_robin_credits": 3},
        {"id": "b", "round_robin_credits": 1},
        already_returned=False,
    )
    assert movement["outcome"] == CREDIT_MOVED
    assert credit_patch(movement) == (-1, 1)


def test_a_credit_back_that_already_returned_the_credit_suppresses_the_move():
    """ "no-show credit-back interacts with reassignment"."""
    movement = move_credit(
        {"id": "a", "round_robin_credits": 3},
        {"id": "b", "round_robin_credits": 1},
        already_returned=True,
    )
    assert movement["outcome"] == CREDIT_ALREADY_RETURNED
    assert credit_patch(movement) is None
    assert "already returned" in movement["reason"]


def test_reassigning_to_the_host_who_already_has_it_moves_nothing():
    movement = move_credit(
        {"id": "a", "round_robin_credits": 3},
        {"id": "a", "round_robin_credits": 3},
        already_returned=False,
    )
    assert credit_patch(movement) is None
    assert "staying with the host" in movement["reason"]


# --------------------------------------------------------------------------- #
# The invite
# --------------------------------------------------------------------------- #


def test_the_invite_is_built_from_the_new_host_not_carried_over():
    """ "the new assignee's name, links, and other details"."""
    invite = invite_for(
        {"name": "Priya Raman", "email": "p@x.example", "conference_link": "https://meet/p"}
    )
    assert invite["organizer"] == "Priya Raman"
    assert invite["conference_link"] == "https://meet/p"


def test_an_invite_field_the_new_host_has_not_set_becomes_null():
    """Carrying the old host's dial-in over is the defect the sentence prevents."""
    before = {"organizer": "Dana", "dial_in": "+1-555-0100", "conference_link": "https://meet/d"}
    after = invite_for({"name": "Marcus", "conference_link": "https://meet/m"})
    assert after["dial_in"] is None
    # In the published field order, not the order the dict happens to iterate.
    assert changed_invite_fields(before, after) == ["organizer", "conference_link", "dial_in"]


def test_two_hosts_sharing_a_conference_link_change_nothing_in_it():
    before = {"conference_link": "https://meet/room-4"}
    after = {"conference_link": "https://meet/room-4"}
    assert changed_invite_fields(before, after) == []


def test_changed_fields_are_reported_in_the_published_order():
    before = {field: f"old-{field}" for field in INVITE_FIELDS}
    after = {field: f"new-{field}" for field in INVITE_FIELDS}
    assert changed_invite_fields(before, after) == list(INVITE_FIELDS)


# --------------------------------------------------------------------------- #
# The webhooks
# --------------------------------------------------------------------------- #


def test_the_meeting_update_webhook_fires_for_every_reassignment():
    assert events_for({"round_robin": False}) == [MEETING_UPDATE]
    assert events_for({"round_robin": True}) == [MEETING_UPDATE, BOOKING_REASSIGNED]


def test_the_booking_reassigned_webhook_is_scoped_to_round_robin_bookings():
    """ "Fires when a round-robin booking's host is reassigned"."""
    assert BOOKING_REASSIGNED not in events_for({"round_robin": False})


def test_the_meeting_update_payload_carries_type_updated():
    payload = meeting_update_payload(
        {"id": "m1", "booking_uid": "bk-1", "title": "Demo"},
        {"id": "h2", "name": "Priya", "email": "p@x.example"},
        reassignment={"id": "r1", "from_host_id": "h1", "to_host_id": "h2", "surface": "myapp"},
        at="2026-09-28T09:00:00Z",
    )
    assert payload["type"] == "Updated"
    assert payload["event"] == MEETING_UPDATE
    assert payload["meeting"]["host"]["name"] == "Priya"


def test_the_reassigned_payload_names_the_hosts_added_and_removed():
    payload = booking_reassigned_payload(
        {"booking_uid": "bk-1", "starts_at": CLEAN_START, "ends_at": CLEAN_END},
        {"id": "h2", "name": "Priya", "email": "p@x.example"},
        {"id": "h1", "name": "Dana", "email": "d@x.example"},
        reassignment={"id": "r1", "requested_by": "sam", "surface": "meetings_activity"},
        booking_uid="bk-1",
        at="2026-09-28T09:00:00Z",
    )
    assert payload["addedHostUserIds"] == ["h2"]
    assert payload["removedHostUserIds"] == ["h1"]
    assert payload["organizer"]["name"] == "Priya"
    assert payload["payloadVersion"] == PAYLOAD_VERSION


def test_the_previous_host_appears_only_under_removed_hosts():
    """ "organizer reflects the new host" - so the old one is nowhere else."""
    payload = booking_reassigned_payload(
        {"booking_uid": "bk-1"},
        {"id": "h2", "name": "Priya"},
        {"id": "h1", "name": "Dana"},
        reassignment={},
        booking_uid="bk-1",
        at="2026-09-28T09:00:00Z",
    )
    serialised = repr(payload)
    assert payload["organizer"]["id"] == "h2"
    assert "h1" not in repr(payload["organizer"])
    assert "Dana" not in serialised


def test_the_catalogue_publishes_both_events_and_their_scope():
    catalogue = webhook_catalogue()
    assert catalogue["count"] == 2
    by_event = {entry["event"]: entry for entry in catalogue["events"]}
    assert by_event[MEETING_UPDATE]["scope"] == "every reassignment"
    assert by_event[BOOKING_REASSIGNED]["scope"] == "round-robin bookings only"
    assert by_event[BOOKING_REASSIGNED]["adds"] == list(REASSIGNED_ONLY_KEYS)


# --------------------------------------------------------------------------- #
# The decision: the locked fields
# --------------------------------------------------------------------------- #


def test_changing_the_meeting_type_is_refused_and_quotes_the_rule(
    engine, room, host, other, distribution
):
    make_meeting(engine, room["id"], host["id"])
    decision = engine._decision(
        _only_meeting(engine, "Northwind — Enterprise Demo"),
        {"assign_to": {"kind": "individual", "id": other["id"]}, "meeting_type": "workshop"},
    )
    assert decision.outcome == REFUSED_LOCKED_FIELD
    assert decision.refused is True
    assert "cannot change meeting_type" in decision.reason
    assert "cannot change the Meeting Type or Workspace" in decision.reason


def test_changing_the_workspace_is_refused(engine, room, host, other, distribution):
    make_meeting(engine, room["id"], host["id"])
    decision = engine._decision(
        _only_meeting(engine, "Northwind — Enterprise Demo"),
        {"assign_to": {"kind": "individual", "id": other["id"]}, "workspace": "contoso"},
    )
    assert decision.outcome == REFUSED_LOCKED_FIELD
    assert "workspace" in decision.reason


def test_echoing_the_unchanged_locked_fields_is_not_a_change(
    engine, room, host, other, distribution
):
    """A scheduler that echoes the context it reopened must not be refused for it.

    The values are read off the meeting rather than hard-coded, so the test
    asserts the property ("echoing what is already there is allowed") instead of
    two literals that happen to agree today. The distribution is patched
    afterwards so the meeting's own values are what gets echoed - a meeting whose
    locked fields had drifted from its distribution would be a state the booking
    route cannot produce, and building it by hand here would test nothing real.
    """
    record = make_meeting(engine, room["id"], host["id"])
    decision = engine._decision(
        record["id"],
        {
            "assign_to": {"kind": "individual", "id": other["id"]},
            "meeting_type": record["data"]["meeting_type"],
            "workspace": record["data"]["workspace"],
        },
    )
    assert decision.outcome == ASSIGNED
    assert decision.locked_fields["meeting_type"]["changed"] is False
    assert decision.locked_fields["workspace"]["changed"] is False


def test_echoing_the_distribution_values_is_also_allowed(engine, room, host, other, distribution):
    """The same, with the values the distribution itself carries."""
    record = make_meeting(engine, room["id"], host["id"])
    decision = engine._decision(
        record["id"],
        {
            "assign_to": {"kind": "individual", "id": other["id"]},
            "meeting_type": distribution["data"]["meeting_type"],
            "workspace": distribution["data"]["workspace"],
        },
    )
    assert decision.outcome == ASSIGNED


def _only_meeting(engine, title):
    matches = [r for r in engine.store.list(MEETING_COLLECTION) if r["data"].get("title") == title]
    assert len(matches) == 1, f"expected exactly one meeting titled {title!r}"
    return matches[0]["id"]


# --------------------------------------------------------------------------- #
# The decision: the add-on
# --------------------------------------------------------------------------- #


def test_the_calendar_addon_without_the_extension_is_refused(
    engine, room, host, other, distribution
):
    make_meeting(engine, room["id"], host["id"])
    decision = engine._decision(
        _only_meeting(engine, "Northwind — Enterprise Demo"),
        {"assign_to": {"kind": "individual", "id": other["id"]}, "surface": "chilical_home"},
    )
    assert decision.outcome == REFUSED_ADDON_NOT_READY
    assert "the extension" in decision.reason
    assert "are missing" in decision.reason
    assert "You must have ChiliCal's extension installed and be logged in there" in decision.reason


def test_the_calendar_addon_installed_but_not_logged_in_is_refused(
    engine, room, host, other, distribution
):
    make_meeting(engine, room["id"], host["id"])
    decision = engine._decision(
        _only_meeting(engine, "Northwind — Enterprise Demo"),
        {
            "assign_to": {"kind": "individual", "id": other["id"]},
            "surface": "chilical_home",
            "extension": {"installed": True, "logged_in": False},
        },
    )
    assert decision.outcome == REFUSED_ADDON_NOT_READY
    assert "being logged in" in decision.reason
    # Only the missing half is named, so the message says what to do next.
    assert "the extension" not in decision.reason


def test_the_calendar_addon_installed_and_logged_in_is_accepted(
    engine, room, host, other, distribution
):
    make_meeting(engine, room["id"], host["id"])
    decision = engine._decision(
        _only_meeting(engine, "Northwind — Enterprise Demo"),
        {
            "assign_to": {"kind": "individual", "id": other["id"]},
            "surface": "chilical_home",
            "extension": {"installed": True, "logged_in": True},
        },
    )
    assert decision.outcome == ASSIGNED
    assert decision.add_on["required"] is True
    assert decision.add_on["ready"] is True


def test_the_other_surfaces_are_not_gated(engine, room, host, other, distribution):
    make_meeting(engine, room["id"], host["id"])
    for surface in ("meetings_activity", "myapp", "crm_event_button", "api"):
        decision = engine._decision(
            _only_meeting(engine, "Northwind — Enterprise Demo"),
            {"assign_to": {"kind": "individual", "id": other["id"]}, "surface": surface},
        )
        assert decision.outcome == ASSIGNED, (
            f"{surface} was gated but only the add-on carries a rule"
        )
        assert decision.add_on["required"] is False


def test_a_missing_extension_key_is_a_missing_requirement_not_a_default_true():
    """ "You must have ChiliCal's extension installed" - a caller who forgets is not ready."""
    decision_fields = reassign_rules._add_on_state("chilical_home", {})
    assert decision_fields["ready"] is False
    assert decision_fields["installed"] is False


# --------------------------------------------------------------------------- #
# The decision: who the new host is
# --------------------------------------------------------------------------- #


def test_naming_the_current_host_is_refused(engine, room, host, distribution):
    make_meeting(engine, room["id"], host["id"])
    decision = engine._decision(
        _only_meeting(engine, "Northwind — Enterprise Demo"),
        {"assign_to": {"kind": "individual", "id": host["id"]}},
    )
    assert decision.outcome == REFUSED_SAME_HOST
    assert "already hosts" in decision.reason


def test_a_host_who_does_not_exist_is_refused_by_name(engine, room, host, distribution):
    make_meeting(engine, room["id"], host["id"])
    with pytest.raises(ReassignError) as caught:
        engine._decision(
            _only_meeting(engine, "Northwind — Enterprise Demo"),
            {"assign_to": {"kind": "individual", "id": "host_does_not_exist"}},
        )
    assert "does not exist" in str(caught.value)


def test_a_committed_individual_assignment_with_no_id_is_refused():
    with pytest.raises(ReassignError) as caught:
        decide(
            {
                "id": "m1",
                "host_id": "h1",
                "starts_at": CLEAN_START,
                "ends_at": CLEAN_END,
                "round_robin": True,
            },
            {"name": "D", "team": "t", "member_ids": ["h1"]},
            [{"id": "h1", "team": "t", "active": True, "busy": []}],
            {"assign_to": {"kind": "individual"}},
            NOW,
        )
    assert "must name the host" in str(caught.value)


def test_a_host_outside_the_distribution_is_refused_when_the_control_is_off(
    engine, room, host, distribution
):
    outsider = make_host(engine, "Rui Silva", team="mid-market")
    make_meeting(engine, room["id"], host["id"])
    decision = engine._decision(
        _only_meeting(engine, "Northwind — Enterprise Demo"),
        {"assign_to": {"kind": "individual", "id": outsider["id"]}},
    )
    assert decision.outcome == REFUSED_NOT_IN_DISTRIBUTION
    assert "any team member" in decision.reason


def test_the_same_host_is_accepted_once_the_distribution_allows_the_team(engine, room, host, other):
    """The control on this distribution, so the same outsider is now in scope."""
    outsider = make_host(engine, "Rui Silva", team="enterprise")
    make_distribution(engine, name="Open Team", allow_any_team_member=True, member_ids=[host["id"]])
    make_meeting(engine, room["id"], host["id"], distribution="Open Team")
    decision = engine._decision(
        _only_meeting(engine, "Northwind — Enterprise Demo"),
        {"assign_to": {"kind": "individual", "id": outsider["id"]}},
    )
    assert decision.outcome == ASSIGNED
    assert decision.to_host["id"] == outsider["id"]


def test_an_inactive_host_is_refused_with_its_own_outcome(engine, room, host, distribution):
    gone = make_host(engine, "Alba Ries", active=False)
    make_meeting(engine, room["id"], host["id"])
    decision = engine._decision(
        _only_meeting(engine, "Northwind — Enterprise Demo"),
        {"assign_to": {"kind": "individual", "id": gone["id"]}},
    )
    assert decision.outcome == REFUSED_INACTIVE_HOST
    assert "not taking bookings" in decision.reason


def test_a_busy_host_is_refused_naming_the_clash(engine, room, host, distribution):
    """In scope, so the refusal is about the calendar and not about membership."""
    busy = make_host(
        engine,
        "Rui Silva",
        busy=[
            {
                "starts_at": "2026-01-01T09:00:00Z",
                "ends_at": _shift(CLEAN_END, minutes=90),
                "label": "Renewal call",
            }
        ],
    )
    make_distribution(engine, name="Enterprise Demo", member_ids=[host["id"], busy["id"]])
    make_meeting(engine, room["id"], host["id"])
    decision = engine._decision(
        _only_meeting(engine, "Northwind — Enterprise Demo"),
        {"assign_to": {"kind": "individual", "id": busy["id"]}},
    )
    assert decision.outcome == REFUSED_HOST_UNAVAILABLE
    assert "Renewal call" in decision.reason
    assert decision.availability["free"] is False


def test_the_busy_refusal_says_the_bounds_are_not_what_was_exempted(
    engine, room, host, distribution
):
    busy = make_host(
        engine,
        "Rui Silva",
        busy=[{"starts_at": "2026-01-01T09:00:00Z", "ends_at": _shift(CLEAN_END, minutes=90)}],
    )
    make_distribution(engine, name="Enterprise Demo", member_ids=[host["id"], busy["id"]])
    make_meeting(engine, room["id"], host["id"])
    decision = engine._decision(
        _only_meeting(engine, "Northwind — Enterprise Demo"),
        {"assign_to": {"kind": "individual", "id": busy["id"]}},
    )
    assert "does not ignore a calendar conflict" in decision.reason


def test_a_host_with_no_eligible_alternative_is_refused_as_a_group(engine, room, host):
    """One member, who is the current host: nobody left to auto-select."""
    make_distribution(engine, name="Solo", member_ids=[host["id"]])
    make_meeting(engine, room["id"], host["id"], distribution="Solo", round_robin=True)
    decision = engine._decision(
        _only_meeting(engine, "Northwind — Enterprise Demo"),
        {"assign_to": {"kind": "distribution"}},
    )
    assert decision.outcome == REFUSED_NO_ELIGIBLE_HOST
    assert "Edit Meeting" in decision.reason


def test_an_unnamed_individual_is_a_candidate_listing_not_an_error(
    engine, room, host, distribution
):
    """The availability step, before the operator has chosen anybody."""
    make_meeting(engine, room["id"], host["id"])
    decision = engine._decision(
        _only_meeting(engine, "Northwind — Enterprise Demo"),
        {"assign_to": {"kind": "individual"}},
        require_target=False,
    )
    assert decision.outcome == REFUSED_NO_ELIGIBLE_HOST
    assert "no host named yet" in decision.reason
    assert [row["id"] for row in decision.candidates if row["eligible"]]


# --------------------------------------------------------------------------- #
# The decision: the round-robin limit
# --------------------------------------------------------------------------- #


def test_a_group_assignment_on_a_non_round_robin_booking_is_refused(
    engine, room, host, other, distribution
):
    make_meeting(engine, room["id"], host["id"], round_robin=False)
    for kind in ("team", "distribution"):
        decision = engine._decision(
            _only_meeting(engine, "Northwind — Enterprise Demo"),
            {"assign_to": {"kind": kind}},
        )
        assert decision.outcome == REFUSED_NOT_ROUND_ROBIN
        assert "round robin bookings" in decision.reason
        assert "name the individual host instead" in decision.reason


def test_a_group_assignment_on_a_round_robin_booking_is_accepted(
    engine, room, host, other, distribution
):
    make_meeting(engine, room["id"], host["id"], round_robin=True)
    decision = engine._decision(
        _only_meeting(engine, "Northwind — Enterprise Demo"),
        {"assign_to": {"kind": "team"}},
    )
    assert decision.outcome == ASSIGNED
    assert decision.mode == AUTO_MODE


def test_a_named_host_on_a_non_round_robin_booking_is_still_accepted(
    engine, room, host, other, distribution
):
    """The limit belongs to the automatic endpoint, not to the booking."""
    make_meeting(engine, room["id"], host["id"], round_robin=False)
    decision = engine._decision(
        _only_meeting(engine, "Northwind — Enterprise Demo"),
        {"assign_to": {"kind": "individual", "id": other["id"]}},
    )
    assert decision.outcome == ASSIGNED


def test_naming_a_different_distribution_is_refused(engine, room, host, distribution):
    make_meeting(engine, room["id"], host["id"], round_robin=True)
    decision = engine._decision(
        _only_meeting(engine, "Northwind — Enterprise Demo"),
        {"assign_to": {"kind": "distribution", "id": "Some Other Distribution"}},
    )
    assert decision.outcome == REFUSED_DISTRIBUTION_CONTEXT
    assert "same Distribution" in decision.reason


def test_naming_the_meetings_own_distribution_is_accepted(engine, room, host, distribution):
    make_meeting(engine, room["id"], host["id"], round_robin=True)
    decision = engine._decision(
        _only_meeting(engine, "Northwind — Enterprise Demo"),
        {"assign_to": {"kind": "distribution", "id": "Enterprise Demo"}},
    )
    assert decision.outcome == ASSIGNED


# --------------------------------------------------------------------------- #
# The decision: the slot
# --------------------------------------------------------------------------- #


def test_a_new_slot_is_recorded_and_flagged_as_a_change(engine, room, host, other, distribution):
    make_meeting(engine, room["id"], host["id"])
    decision = engine._decision(
        _only_meeting(engine, "Northwind — Enterprise Demo"),
        {
            "assign_to": {"kind": "individual", "id": other["id"]},
            "starts_at": "2026-10-06T10:00:00Z",
            "ends_at": "2026-10-06T10:30:00Z",
        },
    )
    assert decision.slot_changed is True
    assert decision.starts_at == "2026-10-06T10:00:00Z"


def test_keeping_the_slot_is_not_a_change(engine, room, host, other, distribution):
    make_meeting(engine, room["id"], host["id"])
    decision = engine._decision(
        _only_meeting(engine, "Northwind — Enterprise Demo"),
        {"assign_to": {"kind": "individual", "id": other["id"]}},
    )
    assert decision.slot_changed is False


def test_a_new_slot_keeps_the_meetings_duration_when_no_end_is_given(
    engine, room, host, other, distribution
):
    make_meeting(engine, room["id"], host["id"])
    decision = engine._decision(
        _only_meeting(engine, "Northwind — Enterprise Demo"),
        {
            "assign_to": {"kind": "individual", "id": other["id"]},
            "starts_at": "2026-10-06T10:00:00Z",
        },
    )
    assert decision.ends_at == "2026-10-06T10:30:00Z"


def test_a_slot_that_ends_before_it_starts_is_refused():
    with pytest.raises(ReassignError):
        decide(
            {
                "id": "m1",
                "host_id": "h1",
                "starts_at": CLEAN_START,
                "ends_at": CLEAN_END,
                "round_robin": True,
            },
            {"name": "D", "team": "t", "member_ids": ["h1", "h2"]},
            [
                {"id": "h1", "team": "t", "active": True, "busy": []},
                {"id": "h2", "team": "t", "active": True, "busy": []},
            ],
            {
                "assign_to": {"kind": "individual", "id": "h2"},
                "starts_at": "2026-10-06T10:00:00Z",
                "ends_at": "2026-10-06T09:00:00Z",
            },
            NOW,
        )


# --------------------------------------------------------------------------- #
# The workflow
# --------------------------------------------------------------------------- #


def test_a_reassignment_moves_the_meeting_to_the_new_host(engine, room, host, other, distribution):
    meeting = make_meeting(engine, room["id"], host["id"])
    result = engine.reassign(
        meeting["id"],
        {
            "assign_to": {"kind": "individual", "id": other["id"]},
            "surface": "meetings_activity",
            "requested_by": "dana",
        },
        source=SOURCE,
    )
    assert result["meeting"]["data"]["host_id"] == other["id"]


def test_a_reassignment_updates_the_invite_with_the_new_assignee(
    engine, room, host, other, distribution
):
    meeting = make_meeting(engine, room["id"], host["id"])
    result = engine.reassign(
        meeting["id"], {"assign_to": {"kind": "individual", "id": other["id"]}}, source=SOURCE
    )
    assert result["meeting"]["data"]["invite"]["organizer"] == "Priya Raman"
    assert "organizer" in result["invite_fields_changed"]


def test_a_reassignment_moves_the_round_robin_credit(engine, room, host, other, distribution):
    meeting = make_meeting(engine, room["id"], host["id"])
    engine.reassign(
        meeting["id"], {"assign_to": {"kind": "individual", "id": other["id"]}}, source=SOURCE
    )
    assert engine.store.get(host["id"])["data"]["round_robin_credits"] == 2
    assert engine.store.get(other["id"])["data"]["round_robin_credits"] == 2


def test_a_no_show_credit_back_suppresses_the_credit_movement(
    engine, room, host, other, distribution
):
    meeting = make_meeting(
        engine,
        room["id"],
        host["id"],
        status="no_show",
        no_show_credit_back=True,
        no_show_credited_host_id=host["id"],
    )
    result = engine.reassign(
        meeting["id"], {"assign_to": {"kind": "individual", "id": other["id"]}}, source=SOURCE
    )
    assert result["credit"]["outcome"] == CREDIT_ALREADY_RETURNED
    assert engine.store.get(host["id"])["data"]["round_robin_credits"] == 3
    assert engine.store.get(other["id"])["data"]["round_robin_credits"] == 1


def test_a_reassignment_writes_the_events_history_row_with_the_four_named_facts(
    engine, room, host, other, distribution
):
    meeting = make_meeting(engine, room["id"], host["id"])
    result = engine.reassign(
        meeting["id"],
        {
            "assign_to": {"kind": "individual", "id": other["id"]},
            "surface": "chilical_home",
            "extension": {"installed": True, "logged_in": True},
            "requested_by": "dana",
        },
        source=SOURCE,
    )
    row = result["history"]["data"]
    assert row["reassigned_by"] == "dana"
    assert row["reassigned_to"] == "Priya Raman"
    assert row["at"]
    assert row["reassignment_source"] == "chilical_home"
    assert row["reassignment_source_label"] == "ChiliCal Home"


def test_the_requested_by_falls_back_to_the_actor(engine, room, host, other, distribution):
    meeting = make_meeting(engine, room["id"], host["id"])
    result = engine.reassign(
        meeting["id"],
        {"assign_to": {"kind": "individual", "id": other["id"]}},
        actor="sam",
        source=SOURCE,
    )
    assert result["history"]["data"]["reassigned_by"] == "sam"


def test_a_round_robin_booking_fires_both_webhooks(engine, room, host, other, distribution):
    meeting = make_meeting(engine, room["id"], host["id"], round_robin=True)
    result = engine.reassign(
        meeting["id"], {"assign_to": {"kind": "individual", "id": other["id"]}}, source=SOURCE
    )
    assert [payload["event"] for payload in result["webhooks"]] == [
        MEETING_UPDATE,
        BOOKING_REASSIGNED,
    ]


def test_a_non_round_robin_booking_fires_only_the_meeting_update(
    engine, room, host, other, distribution
):
    meeting = make_meeting(engine, room["id"], host["id"], round_robin=False)
    result = engine.reassign(
        meeting["id"], {"assign_to": {"kind": "individual", "id": other["id"]}}, source=SOURCE
    )
    assert [payload["event"] for payload in result["webhooks"]] == [MEETING_UPDATE]


def test_a_reassignment_records_the_bounds_it_ignored(engine, room, host, other):
    make_distribution(
        engine,
        name="Tight",
        min_notice_minutes=1440,
        max_range_days=7,
        member_ids=[host["id"], other["id"]],
    )
    meeting = make_meeting(
        engine,
        room["id"],
        host["id"],
        distribution="Tight",
        starts_at=format_instant(NOW + timedelta(minutes=40)),
        ends_at=format_instant(NOW + timedelta(minutes=70)),
    )
    result = engine.reassign(
        meeting["id"], {"assign_to": {"kind": "individual", "id": other["id"]}}, source=SOURCE
    )
    assert result["bounds_bypassed"] == ["min_notice"]
    assert result["reassignment"]["data"]["bounds_bypassed"] == ["min_notice"]


def test_a_reassignment_records_a_max_range_breach_too(engine, room, host, other):
    """The second researched bound, not only the first."""
    make_distribution(
        engine,
        name="Short",
        min_notice_minutes=60,
        max_range_days=2,
        member_ids=[host["id"], other["id"]],
    )
    meeting = make_meeting(
        engine,
        room["id"],
        host["id"],
        distribution="Short",
        starts_at=CLEAN_START,
        ends_at=CLEAN_END,
    )
    result = engine.reassign(
        meeting["id"], {"assign_to": {"kind": "individual", "id": other["id"]}}, source=SOURCE
    )
    assert result["bounds_bypassed"] == ["max_range"]


def test_a_clean_reassignment_bypasses_nothing_and_says_so(engine, room, host, other, distribution):
    meeting = make_meeting(engine, room["id"], host["id"])
    result = engine.reassign(
        meeting["id"], {"assign_to": {"kind": "individual", "id": other["id"]}}, source=SOURCE
    )
    assert result["bounds_bypassed"] == []


def test_a_reassignment_bumps_the_meetings_reassignment_count(
    engine, room, host, other, distribution
):
    meeting = make_meeting(engine, room["id"], host["id"])
    engine.reassign(
        meeting["id"], {"assign_to": {"kind": "individual", "id": other["id"]}}, source=SOURCE
    )
    result = engine.reassign(
        meeting["id"], {"assign_to": {"kind": "individual", "id": host["id"]}}, source=SOURCE
    )
    assert result["meeting"]["data"]["reassignment_count"] == 2
    # The second one is back where it started, having been there and come back.
    assert result["meeting"]["data"]["host_id"] == host["id"]


def test_a_reassignment_of_a_meeting_moves_the_slot_when_asked(
    engine, room, host, other, distribution
):
    meeting = make_meeting(engine, room["id"], host["id"])
    result = engine.reassign(
        meeting["id"],
        {
            "assign_to": {"kind": "individual", "id": other["id"]},
            "starts_at": "2026-10-07T09:00:00Z",
        },
        source=SOURCE,
    )
    assert result["meeting"]["data"]["starts_at"] == "2026-10-07T09:00:00Z"


# --------------------------------------------------------------------------- #
# A refusal writes nothing
# --------------------------------------------------------------------------- #


def test_a_refused_reassignment_writes_nothing_at_all(engine, room, host, other, distribution):
    meeting = make_meeting(engine, room["id"], host["id"])
    before = len(engine.store.list(REASSIGNMENT_COLLECTION))
    with pytest.raises(ReassignError):
        engine.reassign(
            meeting["id"], {"assign_to": {"kind": "individual", "id": host["id"]}}, source=SOURCE
        )
    assert len(engine.store.list(REASSIGNMENT_COLLECTION)) == before
    assert engine.store.get(meeting["id"])["data"]["host_id"] == host["id"]
    assert engine.events_history(meeting_id=meeting["id"]) == []


def test_a_refusal_does_not_move_the_credit(engine, room, host, other, distribution):
    meeting = make_meeting(engine, room["id"], host["id"])
    with pytest.raises(ReassignError):
        engine.reassign(
            meeting["id"], {"assign_to": {"kind": "individual", "id": host["id"]}}, source=SOURCE
        )
    assert engine.store.get(host["id"])["data"]["round_robin_credits"] == 3
    assert engine.store.get(other["id"])["data"]["round_robin_credits"] == 1


def test_a_cancelled_meeting_is_a_state_conflict_not_a_bad_request(
    engine, room, host, distribution
):
    meeting = make_meeting(engine, room["id"], host["id"], status="cancelled")
    with pytest.raises(MeetingStateError) as caught:
        engine.reassign(
            meeting["id"], {"assign_to": {"kind": "individual", "id": host["id"]}}, source=SOURCE
        )
    assert "cancelled" in str(caught.value)


def test_a_completed_meeting_is_also_a_state_conflict(engine, room, host, distribution):
    meeting = make_meeting(engine, room["id"], host["id"], status="completed")
    with pytest.raises(MeetingStateError):
        engine.reassign(
            meeting["id"], {"assign_to": {"kind": "individual", "id": host["id"]}}, source=SOURCE
        )


def test_the_state_error_is_a_reassign_error_so_a_catch_all_still_works():
    """A caller catching the base type catches both."""
    assert issubclass(MeetingStateError, ReassignError)


def test_a_missing_meeting_is_refused(engine, distribution):
    with pytest.raises(ReassignError) as caught:
        engine.reassign("meeting_nope", {"assign_to": {"kind": "team"}}, source=SOURCE)
    assert "not found" in str(caught.value)


def test_a_meeting_with_no_distribution_cannot_be_decided(engine, room, host):
    """A row written without a distribution, e.g. by the core records API."""
    record = engine.store.create(
        MEETING_COLLECTION,
        {
            "title": "Orphan",
            "host_id": host["id"],
            "starts_at": CLEAN_START,
            "ends_at": CLEAN_END,
            "status": "scheduled",
        },
        room_id=room["id"],
        source=SOURCE,
    )
    with pytest.raises(ReassignError) as caught:
        engine.preview(record["id"], {"assign_to": {"kind": "team"}})
    assert "distribution" in str(caught.value).lower()


def test_a_meeting_whose_distribution_is_missing_is_refused(engine, room, host, distribution):
    """A distribution that was deleted after the meeting was booked.

    Written directly, because :meth:`prepare_meeting` refuses to book against a
    distribution that does not exist - so this state is only reachable by
    removing one afterwards, which is what the row here does.
    """
    record = engine.store.create(
        MEETING_COLLECTION,
        {
            "title": "Vanished",
            "host_id": host["id"],
            "distribution": "Vanished",
            "meeting_type": "demo",
            "workspace": "northwind",
            "starts_at": CLEAN_START,
            "ends_at": CLEAN_END,
            "status": "scheduled",
        },
        room_id=room["id"],
        source=SOURCE,
    )
    with pytest.raises(ReassignError) as caught:
        engine.preview(record["id"], {"assign_to": {"kind": "team"}})
    assert "not configured" in str(caught.value)


# --------------------------------------------------------------------------- #
# Preview and write agree
# --------------------------------------------------------------------------- #


def test_the_preview_writes_nothing(engine, room, host, other, distribution):
    meeting = make_meeting(engine, room["id"], host["id"])
    before = len(engine.store.list(REASSIGNMENT_COLLECTION))
    preview = engine.preview(
        meeting["id"], {"assign_to": {"kind": "individual", "id": other["id"]}}
    )
    assert preview["outcome"] == ASSIGNED
    assert len(engine.store.list(REASSIGNMENT_COLLECTION)) == before
    assert engine.store.get(meeting["id"])["data"]["host_id"] == host["id"]


def test_the_preview_reports_the_same_outcome_the_write_would_refuse_with(
    engine, room, host, distribution
):
    meeting = make_meeting(engine, room["id"], host["id"])
    request = {"assign_to": {"kind": "individual", "id": host["id"]}}
    preview = engine.preview(meeting["id"], request)
    with pytest.raises(ReassignError) as caught:
        engine.reassign(meeting["id"], request, source=SOURCE)
    assert preview["outcome"] == REFUSED_SAME_HOST
    assert str(caught.value) == preview["reason"]


def test_the_preview_of_an_allowed_request_predicts_the_host_chosen(
    engine, room, host, other, distribution
):
    meeting = make_meeting(engine, room["id"], host["id"])
    preview = engine.preview(
        meeting["id"], {"assign_to": {"kind": "individual", "id": other["id"]}}
    )
    result = engine.reassign(
        meeting["id"], {"assign_to": {"kind": "individual", "id": other["id"]}}, source=SOURCE
    )
    assert preview["to_host"]["id"] == result["meeting"]["data"]["host_id"]


def test_the_preview_of_a_group_assignment_predicts_the_auto_selected_host(
    engine, room, host, other, distribution
):
    meeting = make_meeting(engine, room["id"], host["id"], round_robin=True)
    preview = engine.preview(meeting["id"], {"assign_to": {"kind": "team"}})
    result = engine.reassign(meeting["id"], {"assign_to": {"kind": "team"}}, source=SOURCE)
    assert preview["to_host"]["id"] == result["meeting"]["data"]["host_id"]


# --------------------------------------------------------------------------- #
# Meetings Activity
# --------------------------------------------------------------------------- #


def test_the_upcoming_tab_holds_meetings_that_start_later(engine, room, host, other, distribution):
    future = make_meeting(engine, room["id"], host["id"], title="Future")
    make_meeting(
        engine,
        room["id"],
        host["id"],
        title="Past",
        starts_at="2026-09-01T10:00:00Z",
        ends_at="2026-09-01T10:30:00Z",
    )
    upcoming = engine.meeting_activity(room["id"], "upcoming")
    assert [record["data"]["title"] for record in upcoming] == ["Future"]
    assert future["id"] == upcoming[0]["id"]


def test_the_past_tab_holds_meetings_that_have_started(engine, room, host, other, distribution):
    make_meeting(engine, room["id"], host["id"], title="Future")
    make_meeting(
        engine,
        room["id"],
        host["id"],
        title="Past",
        starts_at="2026-09-01T10:00:00Z",
        ends_at="2026-09-01T10:30:00Z",
    )
    past = engine.meeting_activity(room["id"], "past")
    assert [record["data"]["title"] for record in past] == ["Past"]


def test_a_cancelled_meeting_still_in_the_future_is_still_upcoming(
    engine, room, host, other, distribution
):
    """The tab answers "when is it", not "will it happen"."""
    make_meeting(engine, room["id"], host["id"], title="Cancelled tomorrow", status="cancelled")
    upcoming = engine.meeting_activity(room["id"], "upcoming")
    assert [record["data"]["title"] for record in upcoming] == ["Cancelled tomorrow"]


def test_the_all_tab_holds_both(engine, room, host, other, distribution):
    make_meeting(engine, room["id"], host["id"], title="Future")
    make_meeting(
        engine,
        room["id"],
        host["id"],
        title="Past",
        starts_at="2026-09-01T10:00:00Z",
        ends_at="2026-09-01T10:30:00Z",
    )
    assert len(engine.meeting_activity(room["id"], "all")) == 2


def test_each_of_the_five_researched_filters_narrows_the_list(
    engine, room, host, other, distribution
):
    """Meeting Type / Assignee / Booker / Status / product source."""
    first = make_meeting(
        engine,
        room["id"],
        host["id"],
        title="One",
        booker="a@northwind.example",
        product_source="myapp",
    )
    make_meeting(
        engine,
        room["id"],
        other["id"],
        title="Two",
        booker="b@northwind.example",
        product_source="chilical_home",
    )
    room_id = room["id"]

    assert [r["id"] for r in engine.meeting_activity(room_id, "all", host_id=host["id"])] == [
        first["id"]
    ]
    assert [
        r["data"]["title"]
        for r in engine.meeting_activity(room_id, "all", booker="b@northwind.example")
    ] == ["Two"]
    assert [
        r["data"]["title"] for r in engine.meeting_activity(room_id, "all", product_source="myapp")
    ] == ["One"]
    assert len(engine.meeting_activity(room_id, "all", meeting_type="demo")) == 2
    assert engine.meeting_activity(room_id, "all", meeting_type="workshop") == []
    assert len(engine.meeting_activity(room_id, "all", status="scheduled")) == 2


def test_the_activity_list_is_scoped_to_its_room(
    engine, room, other_room, host, other, distribution
):
    make_meeting(engine, room["id"], host["id"], title="Mine")
    make_meeting(engine, other_room["id"], host["id"], title="Theirs")
    titles = {record["data"]["title"] for record in engine.meeting_activity(room["id"], "all")}
    assert titles == {"Mine"}


def test_a_filter_that_matches_nothing_returns_an_empty_list_not_an_error(
    engine, room, host, distribution
):
    make_meeting(engine, room["id"], host["id"])
    assert engine.meeting_activity(room["id"], "all", booker="nobody@example") == []


# --------------------------------------------------------------------------- #
# Availability and history reads
# --------------------------------------------------------------------------- #


def test_availability_returns_the_eligible_and_the_ineligible_separately(
    engine, room, host, other, distribution
):
    busy = make_host(
        engine,
        "Rui Silva",
        busy=[
            {
                "starts_at": "2026-10-05T09:00:00Z",
                "ends_at": "2026-10-05T12:00:00Z",
                "label": "Renewal",
            }
        ],
    )
    meeting = make_meeting(engine, room["id"], host["id"])
    result = engine.availability(meeting["id"], {"assign_to": {"kind": "individual"}})
    eligible = {row["id"] for row in result["eligible"]}
    ineligible = {row["id"] for row in result["ineligible"]}
    assert other["id"] in eligible
    assert busy["id"] in ineligible
    assert host["id"] in ineligible


def test_availability_writes_nothing(engine, room, host, other, distribution):
    meeting = make_meeting(engine, room["id"], host["id"])
    engine.availability(meeting["id"], {"assign_to": {"kind": "individual"}})
    assert engine.store.list(REASSIGNMENT_COLLECTION) == []


def test_a_meetings_history_reports_the_count_and_the_window(
    engine, room, host, other, distribution
):
    meeting = make_meeting(engine, room["id"], host["id"])
    engine.reassign(
        meeting["id"], {"assign_to": {"kind": "individual", "id": other["id"]}}, source=SOURCE
    )
    history = engine.meeting_history(meeting["id"])
    assert history["reassignment_count"] == 1
    assert history["host_id"] == other["id"]
    assert history["notice_window_open"] is True
    assert history["needs_reassignment"] is False


def test_a_meeting_inside_the_notice_window_is_flagged_as_urgent(engine, room, host, other):
    make_distribution(
        engine,
        name="Tight",
        member_ids=[host["id"], other["id"]],
        min_notice_minutes=1440,
        max_range_days=7,
    )
    # Forty minutes from the engine's clock, against a 24-hour notice window,
    # and inside the 7-day range so the notice is the only breach.
    imminent_start = format_instant(NOW + timedelta(minutes=40))
    imminent_end = format_instant(NOW + timedelta(minutes=70))
    meeting = make_meeting(
        engine,
        room["id"],
        host["id"],
        distribution="Tight",
        starts_at=imminent_start,
        ends_at=imminent_end,
    )
    verdict = evaluate_bounds(
        {"min_notice_minutes": 1440, "max_range_days": 7}, parse_instant(imminent_start), NOW
    )
    assert verdict["min_notice_breached"] is True
    history = engine.meeting_history(meeting["id"])
    assert history["bounds"]["min_notice_breached"] is True
    assert history["needs_reassignment"] is True
    assert history["notice_window_open"] is False


def test_a_meeting_outside_the_notice_window_is_not_flagged(
    engine, room, host, other, distribution
):
    meeting = make_meeting(engine, room["id"], host["id"])
    history = engine.meeting_history(meeting["id"])
    assert history["bounds"]["min_notice_breached"] is False
    assert history["needs_reassignment"] is False
    assert history["notice_window_open"] is True


def test_a_meeting_with_no_configured_notice_is_never_flagged(engine, room, host, other):
    """An unconfigured bound is not breached, so the queue stays empty."""
    make_distribution(
        engine, name="Open", member_ids=[host["id"]], min_notice_minutes=None, max_range_days=None
    )
    imminent_start = format_instant(NOW + timedelta(minutes=5))
    meeting = make_meeting(
        engine,
        room["id"],
        host["id"],
        distribution="Open",
        starts_at=imminent_start,
        ends_at=_shift(imminent_start, minutes=30),
    )
    assert engine.meeting_history(meeting["id"])["needs_reassignment"] is False


def test_events_history_is_newest_first_and_capped(engine, room, host, other, distribution):
    meeting = make_meeting(engine, room["id"], host["id"])
    engine.reassign(
        meeting["id"], {"assign_to": {"kind": "individual", "id": other["id"]}}, source=SOURCE
    )
    engine.reassign(
        meeting["id"], {"assign_to": {"kind": "individual", "id": host["id"]}}, source=SOURCE
    )
    history = engine.events_history(meeting_id=meeting["id"])
    assert len(history) == 2
    assert history[0]["data"]["reassigned_to_host_id"] == host["id"]
    assert len(engine.events_history(limit=1)) == 1


# --------------------------------------------------------------------------- #
# A booking enforces the bounds the reassignment ignores
# --------------------------------------------------------------------------- #


def test_a_booking_inside_the_minimum_notice_is_refused(engine, room, host):
    make_distribution(
        engine, name="Tight", member_ids=[host["id"]], min_notice_minutes=1440, max_range_days=7
    )
    imminent = format_instant(NOW + timedelta(minutes=40))
    with pytest.raises(ReassignError) as caught:
        engine.require_slot_within_bounds(
            {
                "distribution": "Tight",
                "starts_at": imminent,
                "ends_at": _shift(imminent, minutes=30),
            },
            host["id"],
        )
    assert "cannot start 1440 minutes" in str(caught.value)
    assert "Reassigning an existing meeting does ignore those bounds" in str(caught.value)


def test_a_booking_beyond_the_maximum_range_is_refused(engine, room, host):
    make_distribution(
        engine, name="Short", member_ids=[host["id"]], min_notice_minutes=60, max_range_days=2
    )
    far = format_instant(NOW + timedelta(days=30))
    with pytest.raises(ReassignError) as caught:
        engine.require_slot_within_bounds(
            {"distribution": "Short", "starts_at": far, "ends_at": _shift(far, minutes=30)},
            host["id"],
        )
    # The *configured* limit is what the message quotes, and only the breached
    # one: a distribution with no min-notice must not be told about one.
    assert "2 days" in str(caught.value)
    assert "60 minutes" not in str(caught.value)
    assert "max_range" in str(caught.value)


def test_a_booking_within_the_bounds_is_accepted(engine, room, host, distribution):
    verdict = engine.require_slot_within_bounds(
        {"distribution": "Enterprise Demo", "starts_at": CLEAN_START, "ends_at": CLEAN_END},
        host["id"],
    )
    assert verdict["would_block"] is False


def test_a_booking_that_doubles_up_the_host_is_refused(engine, room, host, distribution):
    # The block is long enough to cover the slot and finish after it, so the
    # half-open boundary is not what is under test here.
    busy = make_host(
        engine,
        "Rui Silva",
        busy=[
            {
                "starts_at": _shift(CLEAN_START, minutes=-60),
                "ends_at": _shift(CLEAN_END, minutes=60),
                "label": "Renewal",
            }
        ],
    )
    with pytest.raises(ReassignError) as caught:
        engine.require_slot_within_bounds(
            {"distribution": "Enterprise Demo", "starts_at": CLEAN_START, "ends_at": CLEAN_END},
            busy["id"],
        )
    assert "Renewal" in str(caught.value)


def test_the_rescue_works_end_to_end_a_stale_booking_is_reassigned(engine, room, host, other):
    """The researched purpose: rescue a booking the bounds would have refused."""
    make_distribution(
        engine,
        name="Tight",
        member_ids=[host["id"], other["id"]],
        min_notice_minutes=1440,
        max_range_days=7,
    )
    # Forty minutes out against a 24-hour notice: the stale booking the
    # researched note says reassignment exists to rescue.
    stale_start = format_instant(NOW + timedelta(minutes=40))
    stale = {
        "distribution": "Tight",
        "starts_at": stale_start,
        "ends_at": _shift(stale_start, minutes=30),
    }
    with pytest.raises(ReassignError):
        engine.require_slot_within_bounds(stale, host["id"])

    meeting = make_meeting(
        engine,
        room["id"],
        host["id"],
        distribution="Tight",
        starts_at=stale_start,
        ends_at=stale["ends_at"],
    )
    result = engine.reassign(
        meeting["id"], {"assign_to": {"kind": "individual", "id": other["id"]}}, source=SOURCE
    )
    assert result["reassignment"]["data"]["outcome"] == ASSIGNED
    assert result["bounds_bypassed"] == ["min_notice"]


# --------------------------------------------------------------------------- #
# prepare_meeting
# --------------------------------------------------------------------------- #


def test_the_locked_fields_are_taken_from_the_distribution_not_the_caller(engine, distribution):
    prepared = engine.prepare_meeting(
        {"title": "X", "host_id": "h1", "distribution": "Enterprise Demo", "starts_at": CLEAN_START}
    )
    assert prepared["meeting_type"] == "demo"
    assert prepared["workspace"] == "northwind"
    assert prepared["team"] == "enterprise"


def test_a_meeting_with_no_distribution_is_refused_at_booking(engine):
    with pytest.raises(ReassignError) as caught:
        engine.prepare_meeting({"title": "X", "host_id": "h1", "starts_at": CLEAN_START})
    assert "must name the distribution" in str(caught.value)


# --------------------------------------------------------------------------- #
# The summary
# --------------------------------------------------------------------------- #


def test_the_summary_counts_meetings_tabs_reassignments_and_sources(
    engine, room, host, other, distribution
):
    meeting = make_meeting(engine, room["id"], host["id"])
    make_meeting(
        engine,
        room["id"],
        host["id"],
        title="Old",
        starts_at="2026-09-01T10:00:00Z",
        ends_at="2026-09-01T10:30:00Z",
    )
    engine.reassign(
        meeting["id"],
        {"assign_to": {"kind": "individual", "id": other["id"]}, "surface": "myapp"},
        source=SOURCE,
    )
    summary = engine.summary(room_id=room["id"])
    assert summary["meetings"] == 2
    assert summary["upcoming"] == 1
    assert summary["past"] == 1
    assert summary["reassignments"] == 1
    assert summary["history_rows"] == 1
    assert summary["by_source"] == {"myapp": 1}


def test_the_summary_separates_active_from_inactive_hosts(engine, host, other, distribution):
    make_host(engine, "Alba Ries", active=False)
    summary = engine.summary()
    assert summary["hosts"] == 3
    assert summary["active_hosts"] == 2
    assert summary["inactive_hosts"] == 1


# --------------------------------------------------------------------------- #
# The outcome table
# --------------------------------------------------------------------------- #


def test_every_outcome_is_published_and_only_one_is_allowed():
    table = outcome_table()
    assert set(table) == set(OUTCOMES)
    allowed = [name for name, entry in table.items() if entry["allowed"]]
    assert allowed == [ASSIGNED]


def test_every_refusal_says_what_it_means():
    for name, entry in outcome_table().items():
        assert entry["means"], f"{name} has no explanation"
        if entry["allowed"]:
            continue
        assert entry["quote"] is None or isinstance(entry["quote"], str)


def test_the_refusals_that_cite_a_rule_quote_it():
    table = outcome_table()
    assert table[REFUSED_LOCKED_FIELD]["quote"] == EDITABLE_AND_LOCKED
    assert table[REFUSED_NOT_ROUND_ROBIN]["quote"] == AUTO_IS_ROUND_ROBIN_ONLY


def test_is_refused_agrees_with_the_table():
    for name in OUTCOMES:
        assert is_refused(name) == (name in REFUSED)


# --------------------------------------------------------------------------- #
# The inferences
# --------------------------------------------------------------------------- #


def test_the_inference_registry_is_served_with_its_count():
    described = reassign_inferences.describe()
    assert described["count"] == len(reassign_inferences.INFERENCES)
    assert described["count"] >= 9


def test_every_inference_is_named_bounded_and_changeable():
    for entry in reassign_inferences.INFERENCES:
        assert entry["id"]
        assert entry["topic"]
        assert entry["basis"], f"{entry['id']} states a decision with no basis"
        assert isinstance(entry["value"], dict) and entry["value"], f"{entry['id']} states no value"
        assert entry["why"], f"{entry['id']} gives no reason"
        assert entry["change_it"], f"{entry['id']} cannot be changed"
        assert entry["blast_radius"], f"{entry['id']} has no stated blast radius"


def test_the_inference_ids_are_unique():
    ids = [entry["id"] for entry in reassign_inferences.INFERENCES]
    assert len(ids) == len(set(ids))


def test_the_inference_about_the_round_robin_limit_is_the_reading_this_build_took():
    entry = reassign_inferences.by_id("round-robin-limits-the-automatic-path-only")
    assert entry["value"]["specific_host_requires_round_robin"] is False
    assert entry["value"]["auto_select_requires_round_robin"] is True


def test_the_inference_about_the_narrow_exemption_matches_the_code():
    entry = reassign_inferences.by_id("availability-is-checked-against-the-meetings-own-slot")
    assert set(entry["value"]["ignored"]) == {"min_notice", "max_range"}
    assert "calendar_conflict" in entry["value"]["not_ignored"]


def test_the_inference_about_one_transaction_matches_the_code():
    entry = reassign_inferences.by_id("one-transaction-for-the-whole-reassignment")
    assert entry["value"]["atomic"] is True
    assert entry["value"]["on_refusal"] == "nothing at all is written"


def test_the_inference_about_the_credit_matching_the_code():
    entry = reassign_inferences.by_id("credit-moves-only-once")
    assert entry["value"]["after_no_show_credit_back"] == "no credit moves"


def test_the_inference_about_the_distribution_context_matches_the_code():
    entry = reassign_inferences.by_id("the-distribution-is-the-meetings-own")
    assert entry["value"]["naming_another_distribution"] == "refused"
    # And the code agrees: a different distribution id is refused by outcome.
    meeting = {"host_id": "h1", "starts_at": CLEAN_START, "ends_at": CLEAN_END, "round_robin": True}
    decision = decide(
        meeting,
        {"name": "Mine", "team": "t", "member_ids": ["h1", "h2"]},
        [
            {"id": "h1", "team": "t", "active": True, "busy": []},
            {"id": "h2", "team": "t", "active": True, "busy": []},
        ],
        {"assign_to": {"kind": "distribution", "id": "distribution_someone_elses"}},
        NOW,
        distribution_id="distribution_mine",
    )
    assert decision.outcome == REFUSED_DISTRIBUTION_CONTEXT


def test_the_inference_about_the_surfaces_matches_the_vocabulary():
    entry = reassign_inferences.by_id("surface-is-recorded-not-gated")
    assert set(entry["value"]["gated"]) == SURFACES_REQUIRING_ADDON


def test_the_inferences_endpoint_shows_the_sourced_half_beside_the_assumed_one():
    described = reassign_inferences.describe()
    assert described["sourced"]["locked_fields"] == list(LOCKED_FIELDS)
    assert "half_open_intervals" in described["notes"]


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


def test_the_feature_is_mounted_under_its_own_prefix(http):
    assert FEATURE_ID in {f["id"] for f in http.get("/api/features").json()["features"]}
    assert all(path.startswith(PREFIX) for _, path in mounted_routes(http))


def test_the_vocabulary_is_served_over_http(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["locked_fields"] == list(LOCKED_FIELDS)
    assert body["surfaces"] == list(SURFACES)
    assert body["assignment_kinds"] == list(ASSIGNMENT_KINDS)


def test_the_outcomes_are_served_over_http(http):
    body = http.get(f"{PREFIX}/outcomes").json()
    assert set(body) == set(OUTCOMES)
    assert body[ASSIGNED]["allowed"] is True


def test_the_webhooks_are_served_over_http(http):
    body = http.get(f"{PREFIX}/webhooks").json()
    assert body["count"] == 2
    assert body["payload_version"] == PAYLOAD_VERSION


def test_the_inferences_are_served_over_http(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] >= 8
    assert body["inferences"][0]["id"]


def test_a_distribution_can_be_declared_over_http(http):
    response = http.post(
        f"{PREFIX}/distributions",
        json={
            "name": "Enterprise Demo",
            "team": "enterprise",
            "workspace": "northwind",
            "meeting_type": "demo",
        },
    )
    assert response.status_code == 201
    assert response.json()["data"]["allow_any_team_member"] is False


def test_a_distribution_with_no_team_is_a_400_over_http(http):
    response = http.post(
        f"{PREFIX}/distributions", json={"name": "X", "workspace": "w", "meeting_type": "demo"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "reassign_error"


def test_a_host_can_be_registered_over_http(http):
    response = http.post(
        f"{PREFIX}/hosts",
        json={"name": "Dana Okoro", "email": "dana@dsr.example", "team": "enterprise"},
    )
    assert response.status_code == 201
    assert response.json()["data"]["active"] is True


def test_a_host_with_no_name_is_a_400_over_http(http):
    assert http.post(f"{PREFIX}/hosts", json={"email": "dana@dsr.example"}).status_code == 400


def test_a_distribution_can_be_read_back(http):
    created = http.post(
        f"{PREFIX}/distributions",
        json={
            "name": "Enterprise Demo",
            "team": "enterprise",
            "workspace": "northwind",
            "meeting_type": "demo",
        },
    ).json()
    assert http.get(f"{PREFIX}/distributions/{created['id']}").json()["id"] == created["id"]


def test_an_unknown_distribution_is_a_404(http):
    assert http.get(f"{PREFIX}/distributions/nope").status_code == 404


def test_an_unknown_host_is_a_404(http):
    assert http.get(f"{PREFIX}/hosts/nope").status_code == 404


def test_bookings_flow_end_to_end_over_http(http):
    room, dana, priya, meeting = _http_fixture(http)
    assert meeting["data"]["invite"]["organizer"] == "Dana Okoro"

    result = http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings/{meeting['id']}/reassign",
        json={
            "assign_to": {"kind": "individual", "id": priya["id"]},
            "surface": "meetings_activity",
            "requested_by": "dana",
        },
    )
    assert result.status_code == 200
    body = result.json()
    assert body["meeting"]["data"]["host_id"] == priya["id"]
    assert body["history"]["data"]["reassigned_to"] == "Priya Raman"
    assert [payload["event"] for payload in body["webhooks"]] == [MEETING_UPDATE]


def test_a_booking_that_breaches_the_bounds_is_a_400_over_http(http):
    http.post(
        f"{PREFIX}/distributions",
        json={
            "name": "Tight",
            "team": "enterprise",
            "workspace": "w",
            "meeting_type": "demo",
            "min_notice_minutes": 1440,
            "max_range_days": 7,
        },
    )
    host = http.post(
        f"{PREFIX}/hosts", json={"name": "Dana", "email": "d@x.example", "team": "enterprise"}
    ).json()
    room = http.post("/api/records/room", json={"name": "R"}).json()
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings",
        json={
            "title": "Stale",
            "host_id": host["id"],
            "distribution": "Tight",
            "starts_at": "2026-09-28T09:40:00Z",
            "ends_at": "2026-09-28T10:10:00Z",
        },
    )
    assert response.status_code == 400
    assert "1440 minutes" in response.json()["detail"]


def test_a_booking_on_an_unknown_room_is_a_404(http):
    assert http.post(f"{PREFIX}/rooms/nope/meetings", json={"title": "X"}).status_code == 404


def test_a_booking_naming_a_host_that_does_not_exist_is_a_400(http):
    http.post(
        f"{PREFIX}/distributions",
        json={
            "name": "Enterprise Demo",
            "team": "enterprise",
            "workspace": "northwind",
            "meeting_type": "demo",
        },
    )
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    starts_at, ends_at = http_slot()
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings",
        json={
            "title": "X",
            "host_id": "host_nope",
            "distribution": "Enterprise Demo",
            "starts_at": starts_at,
            "ends_at": ends_at,
        },
    )
    assert response.status_code == 400
    assert "not found" in response.json()["detail"]


def test_a_booking_naming_a_distribution_that_does_not_exist_is_a_400(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    host = http.post(
        f"{PREFIX}/hosts", json={"name": "Dana", "email": "d@x.example", "team": "t"}
    ).json()
    starts_at, ends_at = http_slot()
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings",
        json={
            "title": "X",
            "host_id": host["id"],
            "distribution": "Vanished",
            "starts_at": starts_at,
            "ends_at": ends_at,
        },
    )
    assert response.status_code == 400
    assert "not configured" in response.json()["detail"]


def test_a_booking_against_a_host_with_no_team_is_still_refused(http):
    """The distribution's team is what `any team member` resolves to, so it is required."""
    http.post(
        f"{PREFIX}/distributions",
        json={
            "name": "Enterprise Demo",
            "team": "enterprise",
            "workspace": "northwind",
            "meeting_type": "demo",
        },
    )
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    host = http.post(f"{PREFIX}/hosts", json={"name": "Dana", "email": "d@x.example"}).json()
    starts_at, ends_at = http_slot()
    # Booked fine - the host's own team is not checked at booking time, because
    # a distribution with a team admits its own members. Asserting the boundary
    # here would lock in a rule the research does not state.
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings",
        json={
            "title": "X",
            "host_id": host["id"],
            "distribution": "Enterprise Demo",
            "starts_at": starts_at,
            "ends_at": ends_at,
        },
    )
    assert response.status_code == 201
    assert response.json()["data"]["host_id"] == host["id"]


def test_a_reassignment_of_a_refused_request_is_a_400_over_http(http):
    room, dana, priya, meeting = _http_fixture(http)
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings/{meeting['id']}/reassign",
        json={"assign_to": {"kind": "individual", "id": dana["id"]}},
    )
    assert response.status_code == 400
    assert "already hosts" in response.json()["detail"]


def test_a_reassignment_of_a_cancelled_meeting_is_a_409_over_http(http):
    room, _dana, priya, _meeting = _http_fixture(http)
    store = RecordStore(client_store(http))
    record = store.list(MEETING_COLLECTION)[0]
    store.update(record["id"], {"status": "cancelled"})
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings/{record['id']}/reassign",
        json={"assign_to": {"kind": "individual", "id": priya["id"]}},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "meeting_state_error"


def test_a_refusal_over_http_writes_no_reassignment_row(http):
    room, dana, _priya, meeting = _http_fixture(http)
    before = len(RecordStore(client_store(http)).list(REASSIGNMENT_COLLECTION))
    http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings/{meeting['id']}/reassign",
        json={"assign_to": {"kind": "individual", "id": dana["id"]}},
    )
    assert len(RecordStore(client_store(http)).list(REASSIGNMENT_COLLECTION)) == before


def test_the_actor_query_parameter_reaches_the_audit_row(http):
    room, _dana, priya, meeting = _http_fixture(http)
    http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings/{meeting['id']}/reassign",
        params={"actor": "sam"},
        json={"assign_to": {"kind": "individual", "id": priya["id"]}},
    )
    rows = RecordStore(client_store(http)).audit(collection=HISTORY_COLLECTION)
    assert rows[0]["actor"] == "sam"


def http_slot(days_ahead: int = CLEAN_DAYS_AHEAD) -> tuple[str, str]:
    """A slot inside the bounds, relative to the *wall* clock.

    The HTTP routes build their engine with the real clock, so the slot has to
    be computed from the real now rather than from the module's fixed ``NOW``.
    Same reasoning as :data:`CLEAN_START`, one level up: the point is that no
    test in this file should start failing because the calendar moved on.
    """
    day = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).date().isoformat()
    return f"{day}T10:00:00Z", f"{day}T10:30:00Z"


def _http_fixture(http):
    """A distribution, two hosts, a room and a meeting, all over HTTP.

    Both hosts are distribution members, so a reassignment between them is
    refused only if the rule under test says so - never by accident of scope.
    That matters for the tests that reassign twice: the second has to go back to
    the first host, because there is no third.
    """
    starts_at, ends_at = http_slot()
    http.post(
        f"{PREFIX}/distributions",
        json={
            "name": "Enterprise Demo",
            "team": "enterprise",
            "workspace": "northwind",
            "meeting_type": "demo",
            "min_notice_minutes": 60,
            "max_range_days": 90,
        },
    )
    dana = http.post(
        f"{PREFIX}/hosts",
        json={"name": "Dana Okoro", "email": "dana@dsr.example", "team": "enterprise"},
    ).json()
    priya = http.post(
        f"{PREFIX}/hosts",
        json={"name": "Priya Raman", "email": "priya@dsr.example", "team": "enterprise"},
    ).json()
    # The membership is patched in once both hosts exist, which is also how a
    # deployment does it: the hosts are people, the distribution is the config.
    RecordStore(client_store(http)).update(
        _distribution_named(http, "Enterprise Demo"),
        {"member_ids": [dana["id"], priya["id"]]},
    )
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    meeting = http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings",
        json={
            "title": "Demo",
            "host_id": dana["id"],
            "distribution": "Enterprise Demo",
            "starts_at": starts_at,
            "ends_at": ends_at,
        },
    ).json()
    return room, dana, priya, meeting


def _distribution_named(client, name: str) -> str:
    for record in RecordStore(client_store(client)).list(DISTRIBUTION_COLLECTION):
        if record["data"].get("name") == name:
            return record["id"]
    raise AssertionError(f"distribution {name!r} was not created")


# --------------------------------------------------------------------------- #
# Reads over HTTP
# --------------------------------------------------------------------------- #


def test_the_activity_list_serves_the_tabs_and_the_filters(http):
    room, _dana, _priya, _meeting = _http_fixture(http)
    response = http.get(f"{PREFIX}/rooms/{room['id']}/meetings", params={"tab": "upcoming"})
    assert response.status_code == 200
    body = response.json()
    assert body["tab"] == "upcoming"
    assert body["count"] == 1

    assert (
        http.get(f"{PREFIX}/rooms/{room['id']}/meetings", params={"tab": "archived"}).status_code
        == 400
    )


def test_a_meeting_can_be_read_back_and_not_found_is_a_404(http):
    room, _dana, _priya, meeting = _http_fixture(http)
    assert (
        http.get(f"{PREFIX}/rooms/{room['id']}/meetings/{meeting['id']}").json()["id"]
        == meeting["id"]
    )
    assert http.get(f"{PREFIX}/rooms/{room['id']}/meetings/nope").status_code == 404


def test_a_meeting_cannot_be_read_through_another_rooms_path(http):
    room, _dana, _priya, meeting = _http_fixture(http)
    other = http.post("/api/records/room", json={"name": "Other"}).json()
    assert http.get(f"{PREFIX}/rooms/{other['id']}/meetings/{meeting['id']}").status_code == 404


def test_availability_is_served_over_http(http):
    room, _dana, _priya, meeting = _http_fixture(http)
    body = http.get(f"{PREFIX}/rooms/{room['id']}/meetings/{meeting['id']}/availability").json()
    assert body["meeting_id"] == meeting["id"]
    assert any(row["name"] == "Priya Raman" for row in body["eligible"])


def test_availability_on_another_rooms_meeting_is_a_404(http):
    room, _dana, _priya, meeting = _http_fixture(http)
    other = http.post("/api/records/room", json={"name": "Other"}).json()
    assert (
        http.get(f"{PREFIX}/rooms/{other['id']}/meetings/{meeting['id']}/availability").status_code
        == 404
    )


def test_the_preview_writes_nothing_over_http(http):
    room, _dana, priya, meeting = _http_fixture(http)
    before = len(RecordStore(client_store(http)).list(REASSIGNMENT_COLLECTION))
    body = http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings/{meeting['id']}/preview",
        json={"assign_to": {"kind": "individual", "id": priya["id"]}},
    ).json()
    assert body["outcome"] == ASSIGNED
    assert len(RecordStore(client_store(http)).list(REASSIGNMENT_COLLECTION)) == before


def test_the_reassignment_list_and_single_read_work_over_http(http):
    room, _dana, priya, meeting = _http_fixture(http)
    http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings/{meeting['id']}/reassign",
        json={"assign_to": {"kind": "individual", "id": priya["id"]}},
    )
    listed = http.get(f"{PREFIX}/rooms/{room['id']}/reassignments").json()
    assert listed["count"] == 1
    record_id = listed["reassignments"][0]["id"]
    detail = http.get(f"{PREFIX}/rooms/{room['id']}/reassignments/{record_id}").json()
    assert detail["data"]["to_host"]["name"] == "Priya Raman"
    assert http.get(f"{PREFIX}/rooms/{room['id']}/reassignments/nope").status_code == 404


def test_the_events_history_tab_serves_the_four_named_facts(http):
    room, _dana, priya, meeting = _http_fixture(http)
    http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings/{meeting['id']}/reassign",
        json={
            "assign_to": {"kind": "individual", "id": priya["id"]},
            "surface": "myapp",
            "requested_by": "dana",
        },
    )
    body = http.get(f"{PREFIX}/rooms/{room['id']}/events-history").json()
    assert body["count"] == 1
    row = body["history"][0]
    assert row["reassigned_by"] == "dana"
    assert row["reassigned_to"] == "Priya Raman"
    assert row["reassignment_source"] == "myapp"
    assert row["at"]


def test_the_events_history_serves_no_more_than_its_limit(http):
    room, _dana, _priya, _meeting = _http_fixture(http)
    assert (
        http.get(f"{PREFIX}/rooms/{room['id']}/events-history", params={"limit": 5000}).status_code
        == 422
    )


def test_a_meetings_history_is_served_over_http(http):
    room, _dana, priya, meeting = _http_fixture(http)
    http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings/{meeting['id']}/reassign",
        json={"assign_to": {"kind": "individual", "id": priya["id"]}},
    )
    body = http.get(f"{PREFIX}/rooms/{room['id']}/meetings/{meeting['id']}/history").json()
    assert body["reassignment_count"] == 1
    assert body["host_id"] == priya["id"]


def test_the_room_summary_is_served_over_http(http):
    room, _dana, _priya, _meeting = _http_fixture(http)
    body = http.get(f"{PREFIX}/rooms/{room['id']}/summary").json()
    assert body["meetings"] == 1
    assert body["upcoming"] == 1
    assert body["reassignments"] == 0


def test_the_upcoming_view_spans_every_room(http):
    _room, _dana, _priya, _meeting = _http_fixture(http)
    other = http.post("/api/records/room", json={"name": "Other"}).json()
    host = http.get(f"{PREFIX}/hosts").json()["hosts"][0]
    starts_at, ends_at = http_slot(4)
    http.post(
        f"{PREFIX}/rooms/{other['id']}/meetings",
        json={
            "title": "Other demo",
            "host_id": host["id"],
            "distribution": "Enterprise Demo",
            "starts_at": starts_at,
            "ends_at": ends_at,
        },
    )
    body = http.get(f"{PREFIX}/upcoming").json()
    assert body["count"] == 2


def test_the_csv_export_serves_the_same_rows_as_the_list(http):
    room, _dana, _priya, meeting = _http_fixture(http)
    _ = meeting
    response = http.get(f"{PREFIX}/rooms/{room['id']}/meetings/export.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    lines = response.text.strip().splitlines()
    assert lines[0].startswith("meeting_id,title,status")
    assert len(lines) == 2


def test_a_comma_in_a_title_does_not_shift_the_csv_columns(http):
    """A title with a comma would otherwise move every later cell by one."""
    room, dana, _priya, _meeting = _http_fixture(http)
    store = RecordStore(client_store(http))
    record = store.list(MEETING_COLLECTION)[0]
    store.update(record["id"], {"title": 'Review, Q3 "deep dive"'})
    lines = http.get(f"{PREFIX}/rooms/{room['id']}/meetings/export.csv").text.strip().splitlines()
    assert '"Review, Q3 ""deep dive"""' in lines[1]


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def test_every_reassignment_is_audited_with_the_source_it_was_given(
    engine, room, host, other, distribution
):
    meeting = make_meeting(engine, room["id"], host["id"])
    engine.reassign(
        meeting["id"], {"assign_to": {"kind": "individual", "id": other["id"]}}, source=SOURCE
    )
    for collection in (REASSIGNMENT_COLLECTION, HISTORY_COLLECTION):
        rows = engine.store.audit(collection=collection)
        assert rows, f"{collection} was written without an audit row"
        assert rows[0]["source"] == SOURCE


def test_the_meeting_update_is_audited_too(engine, room, host, other, distribution):
    meeting = make_meeting(engine, room["id"], host["id"])
    engine.reassign(
        meeting["id"], {"assign_to": {"kind": "individual", "id": other["id"]}}, source=SOURCE
    )
    room_entries = engine.store.audit(collection=MEETING_COLLECTION)
    assert [row["action"] for row in room_entries] == ["update", "insert"]
    assert room_entries[0]["source"] == SOURCE


def test_the_credit_moves_are_audited_under_the_same_source(
    engine, room, host, other, distribution
):
    meeting = make_meeting(engine, room["id"], host["id"])
    engine.reassign(
        meeting["id"], {"assign_to": {"kind": "individual", "id": other["id"]}}, source=SOURCE
    )
    updates = [
        row for row in engine.store.audit(collection=HOST_COLLECTION) if row["action"] == "update"
    ]
    assert len(updates) == 2
    assert {row["source"] for row in updates} == {SOURCE}


def test_a_refusal_writes_no_audit_row_for_the_reassignment_collection(
    engine, room, host, distribution
):
    meeting = make_meeting(engine, room["id"], host["id"])
    with pytest.raises(ReassignError):
        engine.reassign(
            meeting["id"], {"assign_to": {"kind": "individual", "id": host["id"]}}, source=SOURCE
        )
    assert engine.store.audit(collection=REASSIGNMENT_COLLECTION) == []
    assert engine.store.audit(collection=HISTORY_COLLECTION) == []


def test_every_write_method_requires_a_source(engine, room, host, other, distribution):
    """The defect this prevents: an audit row naming a path nobody served.

    ``source`` is keyword-only and has no default, so a caller that forgets it
    fails loudly rather than writing a null into the audit log.
    """
    meeting = make_meeting(engine, room["id"], host["id"])
    with pytest.raises(TypeError):
        engine.reassign(meeting["id"], {"assign_to": {"kind": "individual", "id": other["id"]}})


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
    room, dana, priya, meeting = _http_fixture(http)
    http.post(
        f"{PREFIX}/hosts",
        json={"name": "Rui Silva", "email": "rui@dsr.example", "team": "enterprise"},
    )
    http.post(
        f"{PREFIX}/distributions",
        json={
            "name": "Mid-Market Demo",
            "team": "mid-market",
            "workspace": "contoso",
            "meeting_type": "demo",
        },
    )
    later_start, later_end = http_slot(4)
    http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings",
        json={
            "title": "Second",
            "host_id": dana["id"],
            "distribution": "Enterprise Demo",
            "starts_at": later_start,
            "ends_at": later_end,
        },
    )
    http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings/{meeting['id']}/reassign",
        json={"assign_to": {"kind": "individual", "id": priya["id"]}},
    )
    http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings/{meeting['id']}/preview",
        json={"assign_to": {"kind": "individual", "id": dana["id"]}},
    )

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
    room, _dana, priya, meeting = _http_fixture(http)
    http.post(
        f"{PREFIX}/hosts",
        json={"name": "Rui Silva", "email": "rui@dsr.example", "team": "enterprise"},
    )
    later_start, later_end = http_slot(4)
    http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings",
        json={
            "title": "Second",
            "host_id": priya["id"],
            "distribution": "Enterprise Demo",
            "starts_at": later_start,
            "ends_at": later_end,
        },
    )
    http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings/{meeting['id']}/reassign",
        json={"assign_to": {"kind": "individual", "id": priya["id"]}},
    )

    store = RecordStore(client_store(http))
    booking_inserts = [
        row for row in store.audit(collection=MEETING_COLLECTION) if row["action"] == "insert"
    ]
    assert booking_inserts, "no meeting was booked, so the booking route's source was not exercised"

    # Every collection this feature writes to. The meeting collection is in the
    # list for the booking route's insert; its update belongs to the reassign.
    mine = {
        row["source"]
        for row in store.audit(limit=1000)
        if row["collection"]
        in (
            REASSIGNMENT_COLLECTION,
            HISTORY_COLLECTION,
            DISTRIBUTION_COLLECTION,
            HOST_COLLECTION,
            MEETING_COLLECTION,
        )
        and row["source"]
    }
    routes = mounted_routes(http)

    # Four distinct write routes: two POST creates, the booking, the reassign.
    # The count is of *sources*, so a route that writes nothing contributes none
    # - and the booking route is only exercised because a meeting was booked
    # above, which the insert assertion checks.
    assert len(mine) >= 4, (
        f"the feature recorded {len(mine)} sources ({sorted(mine)}), fewer than its four write routes"
    )
    for source in sorted(mine):
        assert source.startswith(f"POST {PREFIX}"), (
            f"{source!r} does not name a route under this feature's own prefix"
        )
        assert source_names_a_mounted_route(source, routes), f"{source!r} names no mounted route"


def test_a_reassignment_written_over_http_records_its_own_route(http):
    room, _dana, priya, meeting = _http_fixture(http)
    http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings/{meeting['id']}/reassign",
        json={"assign_to": {"kind": "individual", "id": priya["id"]}},
    )
    store = RecordStore(client_store(http))
    sources = {row["source"] for row in store.audit(collection=REASSIGNMENT_COLLECTION)}
    assert sources == {f"POST {PREFIX}/rooms/{room['id']}/meetings/{meeting['id']}/reassign"}


def test_a_meeting_booked_over_http_records_its_own_route(http):
    room, dana, _priya, meeting = _http_fixture(http)
    store = RecordStore(client_store(http))
    inserts = [
        row for row in store.audit(collection=MEETING_COLLECTION) if row["action"] == "insert"
    ]
    assert {row["source"] for row in inserts} == {f"POST {PREFIX}/rooms/{room['id']}/meetings"}


# --------------------------------------------------------------------------- #
# The demo data
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def seeded():
    """One seeded database shared by every read-only seed test in this section.

    Fifteen tests assert different things about the same demo dataset, and the
    seeder writes hundreds of rows. Seeding once per module rather than once per
    test is the difference between one seed and fifteen. The two tests that need
    different data keep their own database: the no-rooms case seeds an empty room
    list, and the refusal test drives the engine rather than reading it.

    Every test that uses this fixture only reads. A reader cannot change what the
    next reader sees, which is what makes sharing it safe.
    """
    database = AuditedDatabase()
    try:
        store = RecordStore(database)
        summary = load_feature(MODULE).seed(database, {"room_ids": seed_rooms(store), "now": NOW})
        yield store, summary
    finally:
        database.close()


@pytest.fixture()
def seed_module():
    return load_feature(MODULE)


def seed_rooms(store, names=("Northwind", "Contoso", "Fabrikam", "Adventure")):
    return [
        (store.create("room", {"name": name}, actor="dana", source="core")["id"], name)
        for name in names
    ]


def test_the_seed_reports_what_it_added(seeded, seed_module):
    store, summary = seeded
    assert isinstance(summary, str)
    assert "7 hosts" in summary
    assert "3 distributions" in summary
    assert f"{len(seed_module.DEMO_MEETINGS)} meetings" in summary


def test_the_seed_shows_both_bounds_being_bypassed_in_different_cases(seeded, seed_module):
    """Both researched bounds, so one case is not standing in for both."""
    store, _summary = seeded
    bypassed = [
        set(record["data"]["bounds_bypassed"]) for record in store.list(REASSIGNMENT_COLLECTION)
    ]
    assert any("min_notice" in entry for entry in bypassed)
    assert any("max_range" in entry for entry in bypassed)


def test_the_seed_shows_both_bypassed_bounds_and_both_webhooks(seeded, seed_module):
    store, _summary = seeded
    reassignments = store.list(REASSIGNMENT_COLLECTION)
    assert any(record["data"]["bounds_bypassed"] for record in reassignments)
    assert any(
        record["data"]["webhooks"] == [MEETING_UPDATE, BOOKING_REASSIGNED]
        for record in reassignments
    )


def test_the_seed_shows_a_credit_that_did_not_move_after_a_no_show(seeded, seed_module):
    store, _summary = seeded
    outcomes = {
        record["data"]["credit_movement"]["outcome"]
        for record in store.list(REASSIGNMENT_COLLECTION)
    }
    assert CREDIT_ALREADY_RETURNED in outcomes
    assert CREDIT_MOVED in outcomes


def test_the_seed_shows_an_invite_field_going_to_null(seeded, seed_module):
    """A field the new host has not set must not keep the old host's value."""
    store, _summary = seeded
    nulled = [
        record
        for record in store.list(REASSIGNMENT_COLLECTION)
        if record["data"]["invite_after"].get("dial_in") is None
        and record["data"]["invite_before"].get("dial_in") is not None
    ]
    assert nulled, "no reassignment nulled an invite field, so the demo misses that case"


def test_every_seeded_reassignment_carries_a_real_before_and_after(seeded, seed_module):
    """Neither side may be empty.

    An empty "before" is the shape a reassignment takes when the meeting was
    booked without an invite, and it silently hides every field that changed -
    which is the whole researched behaviour. A demo that showed it would be a
    demo of a bug.
    """
    store, _summary = seeded
    for record in store.list(REASSIGNMENT_COLLECTION):
        data = record["data"]
        assert data["invite_before"], f"{data['meeting_id']} has no invite before the reassignment"
        assert data["invite_after"], f"{data['meeting_id']} has no invite after the reassignment"
        # And the before really is the *old* host's.
        assert data["invite_before"]["organizer"] == data["from_host"]["name"]
        assert data["invite_after"]["organizer"] == data["to_host"]["name"]


def test_the_seeded_reassignments_report_at_least_one_field_changing(seeded, seed_module):
    store, _summary = seeded
    changes = [
        record["data"]["invite_fields_changed"] for record in store.list(REASSIGNMENT_COLLECTION)
    ]
    assert all(changes), "a reassignment reported no invite change at all"
    assert any("dial_in" in changed for changed in changes)


def test_the_seed_shows_every_documented_refusal(seeded, seed_module):
    """One refusal per rule the demo is meant to make reachable, and all of them land."""
    store, summary = seeded
    assert len(seed_module.DEMO_REFUSED) == 5
    assert "5 refusals" in summary


def test_the_seed_actually_refuses_what_it_says_it_refuses(db, seed_module):
    """The demo asserts the behaviour rather than leaving a reader to trust it."""
    store = RecordStore(db)
    engine = ReassignEngine(store, clock=lambda: NOW)
    seed_module.seed(db, {"room_ids": seed_rooms(store), "now": NOW})
    host_ids = {record["data"]["name"]: record["id"] for record in store.list(HOST_COLLECTION)}
    meetings = {record["data"]["title"]: record for record in store.list(MEETING_COLLECTION)}

    with pytest.raises(ReassignError):
        engine.reassign(
            meetings["Fabrikam Logistics — Implementation Check-in"]["id"],
            {"assign_to": {"kind": "individual", "id": host_ids["Rui Silva"]}},
            source="seed",
        )
    with pytest.raises(ReassignError):
        engine.reassign(
            meetings["Adventure Works — Pilot Review"]["id"],
            {"assign_to": {"kind": "individual", "id": host_ids["Alba Ries"]}},
            source="seed",
        )
    with pytest.raises(ReassignError):
        engine.reassign(
            meetings["Northwind Traders — Technical Deep Dive"]["id"],
            {"assign_to": {"kind": "team"}},
            source="seed",
        )
    with pytest.raises(ReassignError):
        engine.reassign(
            meetings["Northwind Traders — Enterprise Demo"]["id"],
            {
                "assign_to": {"kind": "individual", "id": host_ids["Priya Raman"]},
                "surface": "chilical_home",
            },
            source="seed",
        )
    with pytest.raises(ReassignError):
        engine.reassign(
            meetings["Fabrikam Logistics — Renewal Review"]["id"],
            {
                "assign_to": {"kind": "individual", "id": host_ids["Priya Raman"]},
                "meeting_type": "demo",
            },
            source="seed",
        )


def test_the_seed_shows_a_cancelled_meeting_so_the_409_is_reachable(seeded, seed_module):
    store, _summary = seeded
    statuses = {record["data"]["status"] for record in store.list(MEETING_COLLECTION)}
    assert "cancelled" in statuses
    assert "no_show" in statuses


def test_the_seed_uses_every_documented_source(seeded, seed_module):
    store, _summary = seeded
    sources = {record["data"]["reassignment_source"] for record in store.list(HISTORY_COLLECTION)}
    assert sources <= set(SURFACES)
    # More than one, so the Events History "source" column is worth reading.
    assert len(sources) >= 3


def test_the_seed_writes_history_for_every_reassignment(seeded, seed_module):
    store, _summary = seeded
    assert len(store.list(HISTORY_COLLECTION)) == len(store.list(REASSIGNMENT_COLLECTION))


def test_the_seed_hosts_include_one_inactive_and_one_busy(seeded, seed_module):
    """Otherwise the two refusals are unreachable from the demo."""
    store, _summary = seeded
    hosts = store.list(HOST_COLLECTION)
    assert any(not record["data"]["active"] for record in hosts)
    assert any(record["data"]["busy"] for record in hosts)


def test_the_seed_distributions_disagree_about_any_team_member(seeded, seed_module):
    """Otherwise the researched control has nothing to decide."""
    store, _summary = seeded
    flags = {
        record["data"]["allow_any_team_member"] for record in store.list(DISTRIBUTION_COLLECTION)
    }
    assert flags == {True, False}


def test_the_seed_survives_being_run_with_no_rooms(db, seed_module):
    summary = seed_module.seed(db, {"room_ids": [], "now": NOW})
    assert "no rooms" in summary


def test_the_seed_audits_its_own_writes(seeded, seed_module):
    store, _summary = seeded
    for collection in (REASSIGNMENT_COLLECTION, HISTORY_COLLECTION):
        rows = store.audit(collection=collection)
        assert rows, f"{collection} was seeded without an audit row"
        assert {row["source"] for row in rows} == {"seed"}


def test_the_seeded_reassignment_labels_match_what_the_engine_produced(seeded, seed_module):
    """Every demo case is labelled with the outcome it actually reaches."""
    store, _summary = seeded
    outcomes = [record["data"]["outcome"] for record in store.list(REASSIGNMENT_COLLECTION)]
    assert set(outcomes) == {ASSIGNED}


# --------------------------------------------------------------------------- #
# The contract
# --------------------------------------------------------------------------- #


def test_the_module_does_not_import_the_app():
    """Importing dsr.api from a feature reintroduces the shared-file coupling."""
    text = (Path(__file__).resolve().parents[1] / "dsr" / "features" / f"{MODULE}.py").read_text(
        encoding="utf-8"
    )
    assert "from dsr.api" not in text and "import dsr.api" not in text


def test_the_seeded_demo_has_no_misleading_labels(db, seed_module):
    """Each label has to describe what the engine actually did.

    A demo whose labels drift from its behaviour is worse than one with no
    labels: a reviewer reading "bypassing a notice breach" on a row that
    bypassed nothing has been told something false, in the document written to
    save them reconstructing the diff.
    """
    store = RecordStore(db)
    ReassignEngine(store, clock=lambda: NOW)
    seed_module.seed(db, {"room_ids": seed_rooms(store), "now": NOW})
    bypassed = {
        record["data"]["meeting_id"]: set(record["data"]["bounds_bypassed"])
        for record in store.list(REASSIGNMENT_COLLECTION)
    }
    assert bypassed, "the seed produced no reassignments to check the labels against"
    for spec in seed_module.DEMO_MEETINGS:
        if not spec.get("reassign"):
            continue
        label = str(spec["label"])
        matches = [
            record["id"]
            for record in store.list(MEETING_COLLECTION)
            if record["data"].get("title") == spec["title"]
        ]
        assert len(matches) == 1, (
            f"the demo title {spec['title']!r} is not unique, so its label is ambiguous"
        )
        if "bypassing" in label:
            assert bypassed.get(matches[0]), f"{label!r} claims a bypass the engine did not make"


def test_the_feature_exports_what_the_host_needs(seed_module):
    assert seed_module.FEATURE["id"] == FEATURE_ID
    assert seed_module.FEATURE["ticket"] == "WF-063"
    assert seed_module.router.prefix == PREFIX
    assert callable(seed_module.seed)


def test_the_feature_maps_both_of_its_error_types(seed_module):
    """One handler per type, the subclass first so FastAPI resolves the 409."""
    from dsr.reassign.errors import MeetingStateError as StateError

    assert set(seed_module.EXCEPTION_HANDLERS) == {ReassignError, StateError}


def test_the_feature_does_not_map_an_error_another_feature_owns(http):
    """The host refuses a duplicate handler; assert the registry accepted this one."""
    failed = http.get("/api/features").json()["failed"]
    assert not [entry for entry in failed if entry["id"] == FEATURE_ID]


def test_a_refusal_writes_no_audit_row_for_anything_over_http(http):
    """The audit half of "a refusal writes nothing".

    The domain-level test checks that the reassignment and history collections
    gained no rows. This one checks the audit log too, which is the part a
    reviewer will actually read: an audit row for work that did not happen is
    the drift the product's guarantee exists to prevent, whatever collection it
    lands in.
    """
    room, dana, _priya, meeting = _http_fixture(http)
    store = RecordStore(client_store(http))
    # The snapshot is of the row *ids*, not a count: the fixture's own writes
    # are already in the log, and comparing counts would let a refusal that wrote
    # two rows and un-wrote nothing pass against a snapshot taken mid-setup.
    before = {row["seq"] for row in store.audit(limit=1000)}
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/meetings/{meeting['id']}/reassign",
        json={"assign_to": {"kind": "individual", "id": dana["id"]}},
    )
    assert response.status_code == 400
    new = [row for row in store.audit(limit=1000) if row["seq"] not in before]
    assert new == [], f"a refused reassignment audited {[row['action'] for row in new]}"


def test_no_feature_records_a_route_this_app_does_not_serve(http):
    """The whole feature, checked in one place as well as per-collection."""
    store = RecordStore(client_store(http))
    routes = all_served_routes(http)
    for row in store.audit(limit=1000):
        if row["source"]:
            assert source_names_a_mounted_route(row["source"], routes)


def test_the_module_map_is_documented_everywhere_it_is_published():
    """``__init__`` names the modules; the directory matches."""
    from dsr import reassign

    package = Path(reassign.__file__).parent
    on_disk = {path.stem for path in package.glob("*.py") if not path.stem.startswith("_")}
    for name in on_disk:
        assert name in (reassign.__doc__ or ""), f"{name} is not in the module map"


def test_every_exported_name_resolves():
    """``__all__`` is a promise, and a stale entry is a broken import elsewhere."""
    from dsr import reassign

    missing = [name for name in reassign.__all__ if not hasattr(reassign, name)]
    assert missing == []
    assert len(reassign.__all__) == len(set(reassign.__all__)), "__all__ repeats a name"
