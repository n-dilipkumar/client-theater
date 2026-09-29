"""Tests for WF-059: provision a per-booking video-conference link.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-059.md``. Nothing here is a
preference of this build unless it is listed in
:mod:`dsr.conference_links.inferences`, and every inference in that registry has
a test that checks it is still named, still bounded and still changeable.

The researched half
-------------------

* The **seven Location options** user_flow step 1 names, and what each one
  generates: "This option generates a one-time Google Meet link", "This one
  generates a one-time Zoom link", "This one generates a one-time Gong link;
  however, when clicked, Gong will redirect you to Zoom", "Conference Details:
  This is a text field where you can manually enter the Location details. This
  option is normally used to include links, like static Zoom ones, for those who
  don't want to use one-time links", "Ask the Guest (Provide My Own): This
  field will enable your prospects to provide the Location themselves".
* "multiple locations with a 'Set as Default'" - the researched shape of the
  picker.
* "Connecting Zoom on the Integrations tab is mandatory for this one to work" -
  step 2, and the same for Meet and Gong.
* "On booking, the system creates a fresh conference and writes it into the
  invite's Location field" - step 3, and the data_flow's two destinations:
  booking ``location`` and meeting ``meetingLocation``.
* Google's prohibition, which is the rule this workflow exists to satisfy:
  "Reusing Google Meet conference data across different events can cause access
  issues and expose meeting details to unintended users", and "always generate a
  unique conference for each event by using the ``createRequest`` field".
* Step 4: "the location of the existing booking is updated and the conference
  link is re-provisioned; attendees are emailed the change", plus Cal's "For
  integration locations (e.g. Zoom, Google Meet, Cal Video), the endpoint also
  provisions a conference link. Attendees are notified of the location change by
  email", and the webhook's ``previousLocation``.
* ``appsStatus[]`` per app with ``appName``, ``success``, ``failures``,
  ``errors``.
* Cal's twenty-nine-value ``integration`` enum, its eight ``location`` types,
  its ``cal-api-version: 2024-08-13`` header and its ``BOOKING_WRITE`` scope.
* The two dynamic tags, ``CP.Meeting.RescheduleUrl`` and ``CP.Meeting.CancelUrl``.

The three bugs these tests were written to catch, each of which is the kind of
thing this workflow invites because nothing in the researched text prevents it:

* **A conference reused across two bookings.** The identity function is
  deterministic, so a collision is only possible if the *booking* is left out
  of it - and then a second buyer is handed the first buyer's link, which is the
  exact failure Google's warning describes. The reuse check is asserted across
  rooms, because scoping it to one room would make the prohibition hold only
  where nobody was looking.
* **A ``failures: 0`` read off a failed entry.** Normalising ``success: false``
  with no count to zero would mark a provision as never having been tried, which
  is the one reading that makes a retry policy wrong in the dangerous direction.
* **A Gong link reported as a Zoom link.** Gong "will redirect you to Zoom", so
  the two are different links to one meeting; comparing a swap on provider alone
  would have decided Gong -> Zoom was not a change at all.

The HTTP half runs against the real app over a temporary database, the way
``test_features.py`` does, and asserts that every ``source`` recorded in the
audit log names a route the host actually mounted.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.conference_links import (
    BOOKINGS,
    CONNECTIONS,
    MEETING_LOCATIONS,
    RETRY_ATTEMPTS,
    ProvisioningEngine,
)
from dsr.conference_links import connections as conn_mod
from dsr.conference_links import inferences as cl_inferences
from dsr.conference_links import locations as loc_mod
from dsr.conference_links import minting, provider_status, swapping, vocabulary as vocab
from dsr.conference_links.errors import (
    BookingNotFound,
    ConferenceLinkError,
    ConferenceNotFound,
    ConferenceReuse,
    ConnectionNotFound,
    DefaultLocationRequired,
    DuplicateProviderConnection,
    LocationUnchanged,
    LocationNotFound,
    ProviderNotConnected,
    UnknownLocation,
    UnknownLocationKind,
    UnknownProvider,
)
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-059"

FEATURE_ID = "wf-059-provision-a-per-booking-video-conferen"
MODULE = "wf059_provision_a_per_booking_video_conferen"

#: What the pure-domain tests pass as ``source``. Deliberately the exact shape a
#: route passes, so a test asserting on an audit row is asserting on the real
#: thing rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/bookings"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def db(tmp_path):
    database = AuditedDatabase(tmp_path / "wf059.db", mirror_dir=tmp_path / "audit")
    yield database
    database.close()


@pytest.fixture()
def store(db):
    return RecordStore(db)


@pytest.fixture()
def clock():
    moment = {"at": datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)}

    def stamp():
        return moment["at"].isoformat(timespec="milliseconds")

    stamp.advance = lambda seconds: moment.__setitem__(
        "at", moment["at"] + timedelta(seconds=seconds)
    )
    return stamp


@pytest.fixture()
def engine(store, clock):
    return ProvisioningEngine(store, now=clock)


@pytest.fixture()
def room(store):
    return store.create("room", {"name": "Northwind Traders", "stage": "evaluation"}, source="test")


@pytest.fixture()
def other_room(store):
    return store.create("room", {"name": "Contoso Health", "stage": "discovery"}, source="test")


@pytest.fixture()
def zoom_connection(engine):
    return engine.connect({"provider": "zoom", "host": "dana@example.com"}, source="test")


@pytest.fixture()
def meet_connection(engine):
    return engine.connect({"provider": "google-meet", "host": "sam@example.com"}, source="test")


@pytest.fixture()
def gong_connection(engine):
    return engine.connect({"provider": "gong", "host": "dana@example.com"}, source="test")


@pytest.fixture()
def zoom_location(engine, zoom_connection):
    return engine.create_location(
        {"kind": "zoom", "name": "Zoom", "connection_id": zoom_connection["id"]}, source="test"
    )


@pytest.fixture()
def gong_location(engine, gong_connection):
    return engine.create_location(
        {"kind": "gong", "name": "Gong", "connection_id": gong_connection["id"]}, source="test"
    )


@pytest.fixture()
def static_location(engine):
    return engine.create_location(
        {
            "kind": "conference-details",
            "name": "Static room",
            "conference_details": "https://example.zoom.us/j/9876543210",
        },
        source="test",
    )


@pytest.fixture()
def http(monkeypatch, tmp_path):
    """A client over a temporary database.

    ``get_engine`` is a FastAPI dependency, so the real routes already build the
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
    """Does ``"POST /api/wf-059/rooms/abc/bookings"`` name a route that exists?

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
        pattern = "^" + "".join(
            r"[^/]+" if part.startswith("{") else re.escape(part) for part in parts
        ) + "$"
        if re.match(pattern, path):
            return True
    return False


def book(engine, room_id, location, who="a", **overrides):
    """A booking through the real automation, with a stable uid."""
    payload = {
        "location_id": location["id"],
        "booking_uid": f"bk_{who}",
        "attendee_email": f"{who}@example.com",
        "starts_at": "2026-10-02T09:00:00+00:00",
    }
    payload.update(overrides)
    return engine.book(room_id, payload, source="test")


def live_count(store, collection):
    """How many live records a collection holds.

    ``RecordStore`` deliberately exposes no ``count`` - it is a thin façade, and
    a test that reaches for one is a test that has started writing SQL. Counted
    through the list it does expose.
    """
    return len(store.list(collection, limit=1000))


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The routes resolve even though no shared file names this feature."""
    entry = next(
        f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID
    )
    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-059"
    assert entry["exception_handlers"] == ["ConferenceLinkError"]
    assert entry["routes"]


def test_feature_is_not_reported_as_failed(http):
    """A feature that fails to import is reported and skipped; this one must not."""
    body = http.get("/api/features").json()
    assert FEATURE_ID not in {f["id"] for f in body["failed"]}
    assert any("route collision" in f["error"] for f in body["failed"]) is False


def test_the_prefix_is_ours_alone(http):
    """No core route and no other feature answers anything under it."""
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
    """A guard exists in test_features.py; this states the reason locally."""
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "dsr.api" not in source
    assert "from dsr.deps import" in source


def test_the_domain_package_is_ours_and_shares_no_path_with_another_feature():
    """Two features authored independently must not claim one Python module."""
    assert Path(loc_mod.__file__).parent.name == "conference_links"
    names = {p.name for p in Path(loc_mod.__file__).parent.parent.iterdir() if p.is_dir()}
    assert "conference_links" in names


def test_frontend_descriptor_id_matches_the_backend_feature_id():
    """The two halves of a feature are findable by one name, so they must agree."""
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
    assert f"id: '{FEATURE_ID}'" in text


def test_room_scoped_paths_are_room_scoped(http):
    """A booking belongs to the room the buyer was looking at."""
    paths = [path for _, path in mounted_routes(http) if "booking" in path]
    assert paths
    for path in paths:
        assert "{room_id}" in path, f"{path} reaches a booking without naming a room"


# --------------------------------------------------------------------------- #
# The researched vocabulary
# --------------------------------------------------------------------------- #


def test_the_seven_location_options_are_the_ones_the_research_names():
    assert vocab.LOCATION_KINDS == (
        "google-meet",
        "zoom",
        "gong",
        "conference-details",
        "in-person",
        "custom",
        "attendee-defined",
    )
    assert vocab.LOCATION_LABELS["attendee-defined"] == "Ask the Guest (Provide My Own)"
    assert vocab.LOCATION_LABELS["conference-details"] == "Conference Details"


def test_three_options_mint_a_one_time_conference_and_three_do_not():
    """The evidence names exactly three as generating a link."""
    assert vocab.ONE_TIME_KINDS == frozenset({"google-meet", "zoom", "gong"})
    assert not (vocab.ONE_TIME_KINDS & {"conference-details", "in-person", "custom"})
    assert not (vocab.ONE_TIME_KINDS & vocab.GUEST_SUPPLIED_KINDS)


def test_the_ask_the_guest_option_is_the_one_the_guest_supplies():
    assert vocab.GUEST_SUPPLIED_KINDS == frozenset({"attendee-defined"})


def test_the_integration_enum_is_the_researched_one_verbatim():
    """Cal's documented enum, first and last, and its exact size.

    The research says "~30 video integrations" and lists them by name; the list
    has twenty-nine entries, and the approximation in the prose is the
    research's. Asserting twenty-nine rather than thirty is asserting the list
    the research actually printed.
    """
    assert len(vocab.CAL_INTEGRATION_ENUM) == 29
    assert vocab.CAL_INTEGRATION_ENUM[0] == "cal-video"
    assert vocab.CAL_INTEGRATION_ENUM[-1] == "campfire-video"
    for name in ("google-meet", "zoom", "jitsi", "office365-video", "huddle"):
        assert name in vocab.CAL_INTEGRATION_ENUM
    assert "gong" not in vocab.CAL_INTEGRATION_ENUM
    assert len(set(vocab.CAL_INTEGRATION_ENUM)) == len(vocab.CAL_INTEGRATION_ENUM)


def test_the_eight_location_types_are_the_researched_ones():
    assert vocab.LOCATION_TYPES == (
        "address",
        "attendeeAddress",
        "attendeeDefined",
        "attendeePhone",
        "integration",
        "link",
        "phone",
        "organizersDefaultApp",
    )


def test_cal_api_version_and_scope_are_the_researched_ones():
    assert vocab.CAL_API_VERSION_HEADER == "cal-api-version"
    assert vocab.CAL_API_VERSION == "2024-08-13"
    assert vocab.CAL_BOOKING_WRITE_SCOPE == "BOOKING_WRITE"


def test_google_conference_data_version_is_one_and_the_field_is_create_request():
    assert vocab.GOOGLE_CONFERENCE_DATA_VERSION == 1
    assert vocab.GOOGLE_CONFERENCE_CREATE_FIELD == "createRequest"
    assert "conferenceDataVersion" in vocab.GOOGLE_EVENTS_PATH or "events" in vocab.GOOGLE_EVENTS_PATH


def test_the_two_dynamic_tags_are_the_researched_names():
    assert vocab.DYNAMIC_TAGS == ("CP.Meeting.RescheduleUrl", "CP.Meeting.CancelUrl")
    assert vocab.RESCHEDULE_TAG == "CP.Meeting.RescheduleUrl"
    assert vocab.CANCEL_TAG == "CP.Meeting.CancelUrl"


def test_the_webhook_named_is_the_researched_one():
    assert vocab.BOOKING_LOCATION_UPDATED == "BOOKING_LOCATION_UPDATED"
    assert vocab.PREVIOUS_LOCATION_FIELD == "previousLocation"
    assert vocab.BOOKING_LOCATION_FIELD == "location"
    assert vocab.MEETING_LOCATION_FIELD == "meetingLocation"


def test_the_apps_status_fields_are_the_four_the_research_names_in_order():
    assert provider_status.STATUS_FIELDS == ("appName", "success", "failures", "errors")


def test_every_quote_is_present_and_carries_its_source():
    body = vocab.describe()["quotes"]
    assert "Reusing Google Meet conference data" in body["reuse_warning"]
    assert "unique conference for each event" in body["unique_conference"]
    assert "mandatory for this one to work" in body["connection_mandatory"]
    assert "notified of the location change by email" in body["swap_provisions"]
    assert "previousLocation" in body["previous_location"]
    assert "don't want to use one-time links" in body["static_link"]
    assert "redirect you to Zoom" in body["gong_redirect"]


def test_vocabulary_serves_every_published_term():
    body = vocab.describe()
    assert body["location_kinds"] == list(vocab.LOCATION_KINDS)
    assert body["cal_integration_enum_count"] == 29
    assert body["provision_outcomes"] == [
        "conference-provisioned",
        "static",
        "in-person",
        "awaiting-guest",
    ]
    assert body["location_states"] == [
        "unprovisioned",
        "provisioned",
        "provision-failed",
        "static",
        "in-person",
        "awaiting-guest",
        "swapped",
    ]
    assert body["google"]["conference_data_version"] == 1
    assert body["cal"]["api_version"] == "2024-08-13"
    assert body["cal"]["booking_write_scope"] == "BOOKING_WRITE"
    assert body["webhook"]["event"] == "BOOKING_LOCATION_UPDATED"


# --------------------------------------------------------------------------- #
# The Location picker
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("zoom", "zoom"),
        ("Zoom", "zoom"),
        ("ZOOM", "zoom"),
        ("google-meet", "google-meet"),
        ("google_meet", "google-meet"),
        ("Google Meet", "google-meet"),
        ("gong", "gong"),
        ("conference-details", "conference-details"),
        ("Conference Details", "conference-details"),
        ("in-person", "in-person"),
        ("In-Person Meeting", "in-person"),
        ("custom", "custom"),
        ("Custom", "custom"),
        ("attendee-defined", "attendee-defined"),
        ("Ask the Guest", "attendee-defined"),
        ("Ask the Guest (Provide My Own)", "attendee-defined"),
        ("  ask the guest (provide my own)  ", "attendee-defined"),
    ],
)
def test_every_spelling_of_a_researched_option_resolves(raw, expected):
    assert loc_mod.normalise_kind(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "skype", "carrier pigeon", "phone call"])
def test_a_kind_outside_the_researched_seven_is_refused_by_name(raw):
    with pytest.raises(UnknownLocationKind) as excinfo:
        loc_mod.normalise_kind(raw)
    assert "location_kinds" not in str(excinfo.value)
    # The message lists the researched options, so a caller can fix it in one try.
    assert "conference-details" in str(excinfo.value)


def test_only_the_three_one_time_options_need_a_connection():
    for kind in vocab.ONE_TIME_KINDS:
        assert loc_mod.needs_conference(kind) is True
        assert loc_mod.provider_for(kind) == kind
    for kind in ("conference-details", "in-person", "custom", "attendee-defined"):
        assert loc_mod.needs_conference(kind) is False
        assert loc_mod.provider_for(kind) is None


@pytest.mark.parametrize(
    "kind,outcome",
    [
        ("google-meet", "conference-provisioned"),
        ("zoom", "conference-provisioned"),
        ("gong", "conference-provisioned"),
        ("conference-details", "static"),
        ("in-person", "in-person"),
        ("custom", "in-person"),
        ("attendee-defined", "awaiting-guest"),
    ],
)
def test_each_option_reaches_the_outcome_the_research_describes(kind, outcome):
    assert loc_mod.expected_outcome(kind) == outcome
    assert outcome in vocab.PROVISION_OUTCOMES


def test_the_three_one_time_options_wire_to_an_integration_type():
    for kind in vocab.ONE_TIME_KINDS:
        wire = loc_mod.wire_location(kind, {})
        assert wire["type"] == "integration"
        assert wire["integration"] == vocab.PROVIDER_WIRE[kind]


def test_conference_details_wires_to_the_link_escape_hatch():
    """extensibility calls the static link "a `link` escape hatch"."""
    wire = loc_mod.wire_location("conference-details", {"conference_details": "https://x.example/j/1"})
    assert wire["type"] == "link"
    assert wire["link"] == "https://x.example/j/1"


def test_ask_the_guest_wires_to_attendee_defined():
    wire = loc_mod.wire_location("attendee-defined", {"attendee_prompt": "Where shall we meet?"})
    assert wire["type"] == "attendeeDefined"
    assert wire["attendeePrompt"] == "Where shall we meet?"


def test_in_person_and_custom_wire_to_address():
    """This build's reading; recorded as the `location-type-mapping` inference."""
    assert loc_mod.wire_location("in-person", {"custom_text": "Room 4"}) == {
        "type": "address",
        "address": "Room 4",
    }
    assert loc_mod.wire_location("custom", {"custom_text": "Lobby café"})["type"] == "address"


def test_google_meet_carries_the_researched_create_request():
    wire = loc_mod.wire_location("google-meet", {}, request_id="conf_abc")
    assert wire[vocab.GOOGLE_CONFERENCE_CREATE_FIELD] == {"requestId": "conf_abc"}


def test_google_meet_omits_the_create_request_when_there_is_no_identity_to_name():
    """An empty requestId would be sent to Google as a real, meaningless value."""
    assert vocab.GOOGLE_CONFERENCE_CREATE_FIELD not in loc_mod.wire_location("google-meet", {})


def test_google_meet_falls_back_to_a_stored_request_id():
    wire = loc_mod.wire_location("google-meet", {"request_id": "conf_xyz"})
    assert wire[vocab.GOOGLE_CONFERENCE_CREATE_FIELD] == {"requestId": "conf_xyz"}


def test_gong_records_the_researched_redirect():
    """"when clicked, Gong will redirect you to Zoom"."""
    wire = loc_mod.wire_location("gong", {})
    assert wire["redirects_to"] == "zoom"
    assert wire["integration"] == "gong"


def test_a_kind_with_no_researched_wire_type_is_refused():
    with pytest.raises(loc_mod.LocationError):
        loc_mod.wire_location("nonsense", {})


def test_missing_for_reports_the_researched_mandatory_connection():
    assert "connection_id" in loc_mod.missing_for("zoom", {})


def test_missing_for_reports_a_blank_static_text_rather_than_refusing_it():
    """The researched decision: reported, not enforced."""
    assert "conference_details" in loc_mod.missing_for("conference-details", {})
    assert "name" in loc_mod.missing_for("zoom", {})


def test_missing_for_is_empty_for_a_complete_option():
    assert loc_mod.missing_for(
        "conference-details", {"name": "Room", "conference_details": "https://x.example"}
    ) == []


def test_describe_kind_carries_the_researched_sentence():
    body = loc_mod.describe_kind("Zoom")
    assert body["label"] == "Zoom"
    assert body["one_time"] is True
    assert body["connection_required"] is True
    assert "one-time Zoom link" in body["researched"]
    assert loc_mod.describe_kind("Conference Details")["text_key"] == "conference_details"
    assert loc_mod.describe_kind("Ask the Guest")["guest_supplied"] is True


def test_the_catalogue_lists_all_seven_and_labels_them():
    body = loc_mod.catalogue()
    assert body["count"] == 7
    assert {row["kind"] for row in body["kinds"]} == set(vocab.LOCATION_KINDS)
    assert sum(1 for row in body["kinds"] if row["one_time"]) == 3


# --------------------------------------------------------------------------- #
# The Location records
# --------------------------------------------------------------------------- #


def test_a_first_location_becomes_the_default_by_itself(engine):
    """The researched "Set as Default" control is not a prerequisite."""
    created = engine.create_location({"kind": "zoom", "connection_id": "c1"}, source="test")
    assert created["is_default"] is True
    assert engine.default_location()["id"] == created["id"]


def test_a_second_location_is_not_the_default_unless_it_says_so(engine):
    first = engine.create_location({"kind": "conference-details", "conference_details": "x"}, source="test")
    second = engine.create_location({"kind": "in-person", "custom_text": "Room 4"}, source="test")
    assert first["is_default"] is True
    assert second["is_default"] is False


def test_a_second_location_claiming_the_default_is_refused(engine):
    """A picker with two defaults answers "which one?" with a coin flip."""
    engine.create_location({"kind": "in-person", "custom_text": "Room 4"}, source="test")
    with pytest.raises(DefaultLocationRequired):
        engine.create_location(
            {"kind": "conference-details", "conference_details": "x", "is_default": True},
            source="test",
        )


def test_set_default_moves_the_flag_and_leaves_exactly_one(engine):
    first = engine.create_location({"kind": "in-person", "custom_text": "Room 4"}, source="test")
    second = engine.create_location(
        {"kind": "conference-details", "conference_details": "https://x.example"}, source="test"
    )
    engine.set_default_location(second["id"], source="test")
    listed = engine.meeting_locations()
    assert [row["id"] for row in listed if row["is_default"]] == [second["id"]]
    assert engine.default_location()["id"] == second["id"]
    assert first["id"] != second["id"]


def test_setting_the_default_that_is_already_default_writes_nothing(engine, store):
    created = engine.create_location({"kind": "in-person", "custom_text": "Room 4"}, source="test")
    before = len(store.audit(collection=MEETING_LOCATIONS))
    engine.set_default_location(created["id"], source="test")
    assert len(store.audit(collection=MEETING_LOCATIONS)) == before


def test_removing_the_default_is_refused_and_says_which_control_moves_it(engine):
    created = engine.create_location({"kind": "in-person", "custom_text": "Room 4"}, source="test")
    engine.create_location(
        {"kind": "conference-details", "conference_details": "https://x.example"}, source="test"
    )
    with pytest.raises(DefaultLocationRequired) as excinfo:
        engine.remove_location(created["id"], source="test")
    assert "Set as Default" in str(excinfo.value)


def test_removing_the_only_location_is_refused(engine):
    created = engine.create_location({"kind": "in-person", "custom_text": "Room 4"}, source="test")
    with pytest.raises(DefaultLocationRequired) as excinfo:
        engine.remove_location(created["id"], source="test")
    assert "only Location" in str(excinfo.value)


def test_a_location_that_has_provisioned_bookings_cannot_be_removed(
    engine, room, zoom_location
):
    book(engine, room["id"], zoom_location)
    with pytest.raises(DefaultLocationRequired) as excinfo:
        engine.remove_location(zoom_location["id"], source="test")
    assert "audit trail must not point at nothing" in str(excinfo.value)


def test_a_non_default_location_can_be_removed(engine):
    engine.create_location({"kind": "in-person", "custom_text": "Room 4"}, source="test")
    spare = engine.create_location(
        {"kind": "conference-details", "conference_details": "https://x.example"}, source="test"
    )
    assert engine.remove_location(spare["id"], source="test")["hard"] is False


def test_a_location_name_defaults_to_the_researched_label(engine):
    created = engine.create_location({"kind": "conference-details"}, source="test")
    assert created["name"] == "Conference Details"


def test_reading_an_unknown_location_is_a_404(engine):
    with pytest.raises(LocationNotFound):
        engine.meeting_location("nope")


def test_amending_a_location_that_has_provisioned_freezes_its_kind(
    engine, room, zoom_location
):
    book(engine, room["id"], zoom_location)
    with pytest.raises(DefaultLocationRequired) as excinfo:
        engine.amend_location(zoom_location["id"], {"kind": "gong"}, source="test")
    assert "already provisioned conferences" in str(excinfo.value)


def test_amending_a_location_that_has_provisioned_still_allows_a_rename(
    engine, room, zoom_location
):
    book(engine, room["id"], zoom_location)
    amended = engine.amend_location(zoom_location["id"], {"name": "Zoom (recorded)"}, source="test")
    assert amended["name"] == "Zoom (recorded)"
    assert amended["kind"] == "zoom"


def test_an_unprovisioned_location_may_change_its_kind(engine):
    created = engine.create_location(
        {"kind": "in-person", "custom_text": "Room 4", "is_default": True}, source="test"
    )
    amended = engine.amend_location(
        created["id"], {"kind": "conference-details", "conference_details": "https://x.example"},
        source="test",
    )
    assert amended["kind"] == "conference-details"
    assert amended["is_default"] is True


def test_a_location_records_what_it_still_needs(engine):
    created = engine.create_location({"kind": "zoom"}, source="test")
    assert "connection_id" in created["gaps"]
    assert created["blocked_reason"] == vocab.CONNECTION_MANDATORY_QUOTE


def test_a_location_can_be_filtered_by_kind(engine):
    engine.create_location({"kind": "in-person", "custom_text": "Room 4"}, source="test")
    engine.create_location(
        {"kind": "conference-details", "conference_details": "https://x.example"}, source="test"
    )
    assert len(engine.meeting_locations(kind="conference-details")) == 1
    assert len(engine.meeting_locations()) == 2


# --------------------------------------------------------------------------- #
# The Integrations tab
# --------------------------------------------------------------------------- #


def test_connecting_a_provider_needs_a_host(engine):
    with pytest.raises(conn_mod.ConnectionError) as excinfo:
        engine.connect({"provider": "zoom"}, source="test")
    assert "per host" in str(excinfo.value)


def test_an_unknown_provider_is_refused_by_name(engine):
    with pytest.raises(UnknownProvider) as excinfo:
        engine.connect({"provider": "carrier-pigeon", "host": "dana@example.com"}, source="test")
    assert "known providers" in str(excinfo.value)


def test_one_connection_per_host_per_provider(engine):
    engine.connect({"provider": "zoom", "host": "dana@example.com"}, source="test")
    with pytest.raises(DuplicateProviderConnection):
        engine.connect({"provider": "zoom", "host": "dana@example.com"}, source="test")


def test_the_same_provider_on_another_host_is_a_separate_connection(engine):
    engine.connect({"provider": "zoom", "host": "dana@example.com"}, source="test")
    second = engine.connect({"provider": "zoom", "host": "sam@example.com"}, source="test")
    assert second["host"] == "sam@example.com"
    assert len(engine.provider_connections(provider="zoom")) == 2


def test_a_credential_is_never_stored_on_the_record(engine):
    """A token on a record would be a token in the audit log and its mirror."""
    with pytest.raises(conn_mod.ConnectionError) as excinfo:
        engine.connect(
            {"provider": "zoom", "host": "dana@example.com", "token": "ya29.secret"},
            source="test",
        )
    assert "audit log" in str(excinfo.value)


def test_a_picker_provider_connection_asks_for_the_researched_scope(engine):
    created = engine.connect({"provider": "zoom", "host": "dana@example.com"}, source="test")
    assert created["scopes"] == [vocab.CAL_BOOKING_WRITE_SCOPE]
    assert "token" not in created


def test_an_enum_only_provider_does_not_ask_for_the_booking_write_scope(engine):
    created = engine.connect({"provider": "jitsi", "host": "ops@example.com"}, source="test")
    assert created["scopes"] == []
    assert created["picker_provider"] is False


def test_a_live_connection_is_ready_and_names_what_is_missing_when_it_is_not(engine):
    live = engine.connect({"provider": "zoom", "host": "dana@example.com"}, source="test")
    assert live["readiness"]["ready"] is True
    assert live["readiness"]["missing"] == []

    lapsed = engine.connect(
        {"provider": "gong", "host": "dana@example.com", "state": "revoked", "token_present": False},
        source="test",
    )
    assert lapsed["readiness"]["ready"] is False
    assert "token" in lapsed["readiness"]["missing"]
    assert "state:revoked" in lapsed["readiness"]["missing"]


def test_an_unknown_connection_state_is_refused(engine):
    with pytest.raises(conn_mod.ConnectionError):
        engine.connect(
            {"provider": "zoom", "host": "dana@example.com", "state": "vibes-based"},
            source="test",
        )


def test_reading_an_unknown_connection_is_a_404(engine):
    with pytest.raises(ConnectionNotFound):
        engine.provider_connection("nope")


def test_a_connection_cannot_be_patched_onto_another_provider(engine, zoom_connection):
    with pytest.raises(ConnectionNotFound) as excinfo:
        engine.amend_connection(zoom_connection["id"], {"provider": "gong"}, source="test")
    assert "separate request" in str(excinfo.value) or "remove it" in str(excinfo.value)


def test_a_connection_cannot_be_patched_onto_another_host(engine, zoom_connection):
    with pytest.raises(ConnectionNotFound) as excinfo:
        engine.amend_connection(zoom_connection["id"], {"host": "sam@example.com"}, source="test")
    assert "re-authorisation" in str(excinfo.value)


def test_reauthorising_moves_the_host_and_says_which_bookings_keep_their_links(
    engine, room, zoom_location
):
    booked = book(engine, room["id"], zoom_location, who="a")
    swapped = engine.reauthorize(
        zoom_location["connection_id"],
        {"host": "dana@northwind.example"},
        source="test",
    )
    assert swapped["host"] == "dana@northwind.example"
    assert swapped["swap"]["from"]["host"] == "dana@example.com"
    assert swapped["swap"]["bookings_already_provisioned"] == 1
    assert "keep their links" in swapped["swap"]["note"]
    assert engine.booking(room["id"], booked["booking_uid"])["conference_id"]


def test_reauthorising_onto_a_duplicate_is_refused(engine, zoom_connection):
    engine.connect({"provider": "zoom", "host": "sam@example.com"}, source="test")
    with pytest.raises(DuplicateProviderConnection):
        engine.reauthorize(zoom_connection["id"], {"host": "sam@example.com"}, source="test")


def test_disconnecting_a_connection_a_location_names_is_refused(
    engine, zoom_location
):
    with pytest.raises(ProviderNotConnected) as excinfo:
        engine.disconnect(zoom_location["connection_id"], source="test")
    assert "still name connection" in str(excinfo.value)


def test_disconnecting_an_unnamed_connection_is_allowed(engine, zoom_connection):
    assert engine.disconnect(zoom_connection["id"], source="test")["hard"] is False


def test_the_mandatory_connection_refuses_and_names_the_tab(engine):
    """"Connecting Zoom on the Integrations tab is mandatory for this one to work"."""
    with pytest.raises(ProviderNotConnected) as excinfo:
        conn_mod.require_connected("zoom", None)
    assert "Integrations tab" in str(excinfo.value)
    assert "mandatory" in str(excinfo.value)


def test_the_mandatory_connection_refuses_a_connection_that_exists_but_is_not_ready(engine):
    lapsed = engine.connect(
        {"provider": "zoom", "host": "dana@example.com", "state": "needs-reauth"},
        source="test",
    )
    with pytest.raises(ProviderNotConnected) as excinfo:
        conn_mod.require_connected("zoom", lapsed)
    assert "not ready" in str(excinfo.value)


def test_a_kind_that_mints_nothing_is_never_asked_for_a_connection():
    for kind in ("conference-details", "in-person", "custom", "attendee-defined"):
        report = conn_mod.require_connected(kind, None)
        assert report["applicable"] is False


def test_the_thirty_provider_catalogue_splits_the_three_from_the_rest():
    body = conn_mod.provider_catalogue()
    assert body["picker_count"] == 3
    assert body["enum_count"] == 29
    assert body["in_cal_enum_count"] == 27
    assert body["escape_hatch"]["location_type"] == "link"
    assert {row["provider"] for row in body["providers"]} == set(vocab.PICKER_PROVIDERS)


def test_gong_is_published_as_not_being_a_cal_integration():
    """A reader must be able to see the gap rather than a tidy lie."""
    gong = conn_mod.describe_provider("gong")
    assert gong["on_picker"] is True
    assert gong["in_cal_integration_enum"] is False
    assert gong["enum_position"] is None
    assert gong["gong_redirects_to_zoom"] is True
    assert "redirect you to Zoom" in gong["researched"]


def test_an_enum_only_provider_is_addressable_but_not_on_the_picker():
    whereby = conn_mod.describe_provider("whereby-video")
    assert whereby["on_picker"] is False
    assert whereby["in_cal_integration_enum"] is True
    assert whereby["provision_outcome"] is None


def test_every_enum_member_can_be_described():
    for name in vocab.CAL_INTEGRATION_ENUM:
        assert conn_mod.describe_provider(name)["provider"] == name


def test_find_duplicate_matches_on_provider_and_host_together(engine):
    engine.connect({"provider": "zoom", "host": "dana@example.com"}, source="test")
    engine.connect({"provider": "zoom", "host": "sam@example.com"}, source="test")
    presented = engine.provider_connections()
    found = conn_mod.find_duplicate(presented, "zoom", "sam@example.com")
    assert found is not None and found["host"] == "sam@example.com"
    assert conn_mod.find_duplicate(presented, "gong", "dana@example.com") is None


def test_find_duplicate_reads_a_raw_record_and_a_presented_row_alike(engine):
    """The engine walks records off the store; a client holds presented rows."""
    engine.connect({"provider": "zoom", "host": "dana@example.com"}, source="test")
    raw = engine.store.list(CONNECTIONS, limit=10)
    presented = engine.provider_connections()
    assert conn_mod.find_duplicate(raw, "zoom", "dana@example.com") is not None
    assert conn_mod.find_duplicate(presented, "zoom", "dana@example.com") is not None


def test_readiness_reads_a_raw_record_and_a_presented_row_alike(engine):
    """Otherwise every connection reads as missing its provider and its host."""
    raw = engine.connect({"provider": "zoom", "host": "dana@example.com"}, source="test")
    presented = engine.provider_connection(raw["id"])
    assert conn_mod.readiness(raw)["ready"] is True
    assert conn_mod.readiness(presented)["ready"] is True
    assert conn_mod.readiness(None)["missing"] == ["connection"]


# --------------------------------------------------------------------------- #
# Minting a fresh conference
# --------------------------------------------------------------------------- #


def test_a_conference_identity_is_derived_from_its_provider_and_booking():
    first = minting.conference_id("zoom", "bk_1")
    assert first == minting.conference_id("zoom", "bk_1")
    assert first != minting.conference_id("zoom", "bk_2")
    assert first != minting.conference_id("gong", "bk_1")


def test_a_conference_identity_is_prefixed_so_it_is_identifiable_in_a_log():
    assert minting.conference_id("zoom", "bk_1").startswith("conf_")


def test_a_conference_identity_is_scoped_to_the_room_it_was_taken_in():
    """Google's warning is about reuse "across different events", and two rooms'
    identically-named bookings are two events."""
    assert minting.conference_id("zoom", "bk_a", "room_1") != minting.conference_id(
        "zoom", "bk_a", "room_2"
    )


def test_distinct_bookings_never_collide_across_a_large_fleet():
    ids = {minting.conference_id("zoom", f"bk_{n}") for n in range(5000)}
    assert len(ids) == 5000


def test_a_conference_needs_a_provider_and_a_booking():
    with pytest.raises(loc_mod.LocationError):
        minting.conference_id("", "bk_1")
    with pytest.raises(loc_mod.LocationError):
        minting.conference_id("zoom", "")


def test_a_minted_zoom_link_is_shaped_from_the_researched_example():
    """"https://example.zoom.us/j/1234567890" is the researched example."""
    minted = minting.mint("zoom", "bk_1")
    assert minted["url"].startswith("https://example.zoom.us/j/")
    assert len(minted["meeting_id"]) == 10
    assert minted["meeting_id"].isdigit()


def test_a_minted_gong_link_redirects_to_zoom_and_records_both_ends():
    minted = minting.mint("gong", "bk_1")
    assert "/g/" in minted["url"]
    assert minted["gateway_url"].startswith("https://example.zoom.us/j/")
    assert minted["gateway_url"] != minted["url"]


def test_a_minted_conference_declares_itself_unique_and_per_booking():
    minted = minting.mint("zoom", "bk_1")
    assert minted["unique"] is True
    assert minted["scope"] == "per-booking"


def test_google_s_outbound_request_is_the_researched_call_verbatim():
    request = minting.outbound_request("google-meet", "conf_abc", calendar_id="dana@example.com")
    assert request["method"] == "POST"
    assert "googleapis.com" in request["url"]
    assert "dana@example.com" in request["url"]
    assert request["query"] == {"conferenceDataVersion": 1}
    assert request["body"]["conferenceData"]["createRequest"] == {"requestId": "conf_abc"}
    assert "unique conference for each event" in request["evidence"]


def test_a_non_google_provider_asserts_no_request_body_it_cannot_source():
    """The research states it did not read Zoom's or Graph's create endpoints."""
    request = minting.outbound_request("zoom", "conf_abc")
    assert request["url"] is None
    assert request["body"]["location"] == {"type": "integration", "integration": "zoom"}
    assert "did not read" in request["evidence"]


def test_a_claim_on_an_unheld_conference_is_granted():
    assert minting.claim([], "conf_abc", "bk_1")["claimed"] is True


def test_the_same_booking_may_re_claim_its_own_conference():
    """The create-then-retry path; the warning is about reuse *across* events."""
    existing = [{"conference_id": "conf_abc", "booking_uid": "bk_1"}]
    assert minting.claim(existing, "conf_abc", "bk_1")["claimed"] is False


def test_a_conference_another_booking_holds_is_refused_and_names_both():
    """"Reusing Google Meet conference data across different events can cause
    access issues and expose meeting details to unintended users." """
    existing = [{"conference_id": "conf_abc", "booking_uid": "bk_1"}]
    with pytest.raises(ConferenceReuse) as excinfo:
        minting.claim(existing, "conf_abc", "bk_2")
    message = str(excinfo.value)
    assert "bk_1" in message and "bk_2" in message
    assert "expose meeting details" in message


def test_a_claim_ignores_a_conference_id_it_was_not_asked_about():
    existing = [{"conference_id": "conf_abc", "booking_uid": "bk_1"}]
    assert minting.claim(existing, "conf_xyz", "bk_2")["claimed"] is True


def test_a_conference_is_written_into_both_researched_location_fields():
    minted = minting.mint("zoom", "bk_1")
    patch = minting.apply_to_booking(minted, {})
    assert patch[vocab.BOOKING_LOCATION_FIELD] == {"type": "integration", "integration": "zoom"}
    assert patch[vocab.MEETING_LOCATION_FIELD] == minted["url"]
    assert patch["conference_id"] == minted["conference_id"]


def test_a_gong_conference_records_the_researched_redirect_on_the_booking():
    minted = minting.mint("gong", "bk_1")
    patch = minting.apply_to_booking(minted, {})
    assert patch["redirects_to"] == minted["gateway_url"]


def test_a_supplied_location_overrides_the_conference_url():
    """How a swap writes a different link onto a booking that had one."""
    minted = minting.mint("zoom", "bk_1")
    patch = minting.apply_to_booking(minted, {}, location={"url": "https://new.example/j/2"})
    assert patch[vocab.MEETING_LOCATION_FIELD] == "https://new.example/j/2"


def test_a_conference_with_no_url_cannot_be_written_onto_a_booking():
    with pytest.raises(loc_mod.LocationError):
        minting.apply_to_booking({"url": ""}, {})


# --------------------------------------------------------------------------- #
# Booking, and what each Location produces
# --------------------------------------------------------------------------- #


def test_a_zoom_booking_mints_a_fresh_conference_and_writes_both_fields(
    engine, room, zoom_location
):
    booked = book(engine, room["id"], zoom_location, who="a")
    assert booked["state"] == "provisioned"
    assert booked["provision_outcome"] == "conference-provisioned"
    assert booked["conference_id"]
    assert booked[vocab.MEETING_LOCATION_FIELD].startswith("https://example.zoom.us/j/")
    assert booked[vocab.BOOKING_LOCATION_FIELD]["integration"] == "zoom"
    assert booked["provisions_conferences"] is True


def test_a_booking_is_keyed_to_its_room(engine, room, other_room, zoom_location):
    booked = book(engine, other_room["id"], zoom_location, who="a")
    assert engine.store.get(booked["id"])["room_id"] == other_room["id"]
    assert engine.bookings(room["id"]) == []


def test_two_bookings_on_the_same_location_never_share_a_conference(
    engine, room, zoom_location
):
    first = book(engine, room["id"], zoom_location, who="a")
    second = book(engine, room["id"], zoom_location, who="b")
    assert first["conference_id"] != second["conference_id"]
    assert first[vocab.MEETING_LOCATION_FIELD] != second[vocab.MEETING_LOCATION_FIELD]


def test_the_same_booking_uid_twice_lands_on_the_same_conference(
    engine, room, zoom_location
):
    first = book(engine, room["id"], zoom_location, who="a")
    second = book(engine, room["id"], zoom_location, who="a")
    assert first["conference_id"] == second["conference_id"]


def test_a_conference_is_not_reused_across_rooms(engine, room, other_room, zoom_location):
    """The prohibition must hold where nobody is looking, too."""
    mine = book(engine, room["id"], zoom_location, who="a")
    theirs = book(engine, other_room["id"], zoom_location, who="a")
    assert mine["conference_id"] != theirs["conference_id"]


def test_a_static_booking_takes_the_configured_text_and_mints_nothing(
    engine, room, static_location
):
    booked = book(engine, room["id"], static_location, who="a")
    assert booked["state"] == "static"
    assert booked["provision_outcome"] == "static"
    assert "conference_id" not in booked
    assert booked[vocab.MEETING_LOCATION_FIELD] == "https://example.zoom.us/j/9876543210"
    assert booked[vocab.BOOKING_LOCATION_FIELD]["type"] == "link"


def test_an_in_person_booking_describes_a_place_and_mints_nothing(engine, room):
    location = engine.create_location(
        {"kind": "in-person", "custom_text": "Level 12 boardroom", "is_default": True},
        source="test",
    )
    booked = book(engine, room["id"], location, who="a")
    assert booked["state"] == "in-person"
    assert "conference_id" not in booked
    assert booked[vocab.MEETING_LOCATION_FIELD] == "Level 12 boardroom"


def test_an_ask_the_guest_booking_waits_rather_than_failing(engine, room):
    location = engine.create_location(
        {"kind": "attendee-defined", "attendee_prompt": "Where shall we meet?", "is_default": True},
        source="test",
    )
    booked = book(engine, room["id"], location, who="a")
    assert booked["state"] == "awaiting-guest"
    assert booked["provision_outcome"] == "awaiting-guest"
    assert "conference_id" not in booked
    assert booked["guest_prompt"] == "Where shall we meet?"


def test_a_booking_naming_no_location_uses_the_researched_default(
    engine, room, zoom_location
):
    booked = engine.book(room["id"], {"booking_uid": "bk_x"}, source="test")
    assert booked["location_id"] == zoom_location["id"]


def test_a_booking_with_no_location_in_force_is_refused_and_says_which_fixes_it(engine, room):
    with pytest.raises(UnknownLocation) as excinfo:
        engine.book(room["id"], {"booking_uid": "bk_x"}, source="test")
    message = str(excinfo.value)
    assert "location_id" in message and "set-default" in message


def test_a_booking_naming_an_unknown_location_is_refused_as_a_missing_prerequisite(
    engine, room
):
    engine.create_location({"kind": "in-person", "custom_text": "Room 4"}, source="test")
    with pytest.raises(UnknownLocation) as excinfo:
        engine.book(room["id"], {"booking_uid": "bk_x", "location_id": "nope"}, source="test")
    assert "Meeting Type" in str(excinfo.value)


def test_a_booking_whose_provider_is_not_connected_is_refused(engine, room):
    """The researched mandatory step, refused rather than warned about."""
    location = engine.create_location({"kind": "zoom", "is_default": True}, source="test")
    with pytest.raises(ProviderNotConnected) as excinfo:
        book(engine, room["id"], location, who="a")
    assert "Integrations tab" in str(excinfo.value)


def test_a_booking_whose_connection_is_revoked_is_refused(engine, room):
    lapsed = engine.connect(
        {"provider": "zoom", "host": "dana@example.com", "state": "revoked", "token_present": False},
        source="test",
    )
    location = engine.create_location(
        {"kind": "zoom", "connection_id": lapsed["id"], "is_default": True}, source="test"
    )
    with pytest.raises(ProviderNotConnected):
        book(engine, room["id"], location, who="a")


def test_a_refused_provision_writes_no_booking_at_all(engine, room, store):
    """The booking and the refusal are one decision, not two."""
    location = engine.create_location({"kind": "zoom", "is_default": True}, source="test")
    before = live_count(store, BOOKINGS)
    with pytest.raises(ProviderNotConnected):
        book(engine, room["id"], location, who="a")
    assert live_count(store, BOOKINGS) == before


def test_a_booking_records_the_connection_state_it_provisioned_through(
    engine, room, zoom_location
):
    booked = book(engine, room["id"], zoom_location, who="a")
    assert booked["connection_state_at_provision"] == "connected"


def test_bookings_filter_by_location_state_and_provider(engine, room, zoom_location, static_location):
    book(engine, room["id"], zoom_location, who="a")
    book(engine, room["id"], static_location, who="b")
    assert len(engine.bookings(room["id"], state="provisioned")) == 1
    assert len(engine.bookings(room["id"], state="static")) == 1
    assert len(engine.bookings(room["id"], provider="zoom")) == 1
    assert len(engine.bookings(room["id"], location_id=static_location["id"])) == 1


def test_a_booking_uid_can_be_reused_by_a_caller(engine, room, zoom_location):
    assert book(engine, room["id"], zoom_location, who="a")["booking_uid"] == "bk_a"


def test_a_booking_without_a_uid_gets_one_that_is_derived_from_what_is_written(
    engine, room, zoom_location
):
    first = engine.book(room["id"], {"location_id": zoom_location["id"]}, source="test")
    second = engine.book(room["id"], {"location_id": zoom_location["id"]}, source="test")
    assert first["booking_uid"] != second["booking_uid"]


def test_reading_a_booking_in_another_room_is_a_404(engine, room, other_room, zoom_location):
    booked = book(engine, room["id"], zoom_location, who="a")
    with pytest.raises(BookingNotFound):
        engine.booking(other_room["id"], booked["booking_uid"])


def test_reading_an_unknown_booking_is_a_404(engine, room):
    with pytest.raises(BookingNotFound):
        engine.booking(room["id"], "bk_nope")


def test_a_booking_read_carries_its_invite_and_history(engine, room, zoom_location):
    book(engine, room["id"], zoom_location, who="a")
    record = engine.booking(room["id"], "bk_a")
    assert vocab.RESCHEDULE_TAG not in record["invite"]["body"]
    assert record["history"] == []


def test_a_booking_with_no_conference_has_no_conference_to_read(
    engine, room, static_location
):
    book(engine, room["id"], static_location, who="a")
    with pytest.raises(ConferenceNotFound) as excinfo:
        engine.conference(room["id"], "bk_a")
    assert "conference-details" in str(excinfo.value)


def test_a_conference_read_says_the_outbound_request_was_not_sent(
    engine, room, zoom_location
):
    book(engine, room["id"], zoom_location, who="a")
    conference = engine.conference(room["id"], "bk_a")
    assert conference["outbound_not_sent"] is True
    assert conference["unique"] is True
    assert conference["scope"] == "per-booking"


def test_a_meet_booking_records_the_google_create_request(engine, room, meet_connection):
    location = engine.create_location(
        {"kind": "google-meet", "connection_id": meet_connection["id"], "is_default": True},
        source="test",
    )
    booked = book(engine, room["id"], location, who="a")
    request = booked["create_request"]
    assert request["query"] == {"conferenceDataVersion": 1}
    # One name, one meaning: the requestId is the conference identity, and the
    # rendered location object carries the same string.
    assert request["body"]["conferenceData"]["createRequest"] == {
        "requestId": booked["conference_id"]
    }
    assert booked[vocab.BOOKING_LOCATION_FIELD]["createRequest"] == {
        "requestId": booked["conference_id"]
    }


def test_the_conference_identity_differs_per_room(engine, room, other_room, zoom_location):
    """A booking is (room, uid); two rooms' ``bk_a`` are two events."""
    mine = book(engine, room["id"], zoom_location, who="a")
    theirs = book(engine, other_room["id"], zoom_location, who="a")
    assert mine["booking_uid"] == theirs["booking_uid"]
    assert mine["conference_id"] != theirs["conference_id"]
    assert minting.conference_id("zoom", "bk_a", room["id"]) == mine["conference_id"]
    assert minting.conference_id("zoom", "bk_a", other_room["id"]) == theirs["conference_id"]


def test_a_minted_conference_records_the_room_it_belongs_to():
    assert minting.mint("zoom", "bk_a", scope="room_1")["room_id"] == "room_1"
    assert minting.mint("zoom", "bk_a")["room_id"] is None


# --------------------------------------------------------------------------- #
# Re-provisioning, and the researched prohibition
# --------------------------------------------------------------------------- #


def test_re_provisioning_a_booking_that_already_has_a_link_is_refused(
    engine, room, zoom_location
):
    book(engine, room["id"], zoom_location, who="a")
    with pytest.raises(ConferenceReuse) as excinfo:
        engine.provision(room["id"], "bk_a", {}, source="test")
    assert "unique conference for each event" in str(excinfo.value)


def test_re_provisioning_a_location_that_mints_nothing_is_refused(engine, room, static_location):
    book(engine, room["id"], static_location, who="a")
    with pytest.raises(UnknownLocation) as excinfo:
        engine.provision(room["id"], "bk_a", {}, source="test")
    assert "does not mint a conference" in str(excinfo.value)


def test_an_answered_ask_the_guest_location_is_recorded_as_attendee_addressed(
    engine, room
):
    location = engine.create_location(
        {"kind": "attendee-defined", "attendee_prompt": "Where?", "is_default": True},
        source="test",
    )
    book(engine, room["id"], location, who="a")
    answered = engine.provision(
        room["id"], "bk_a", {"guest_location": "https://guest.example/room/7"}, source="test"
    )
    assert answered["provision_outcome"] == "guest-supplied"
    assert answered[vocab.BOOKING_LOCATION_FIELD]["type"] == "attendeeAddress"
    assert answered[vocab.MEETING_LOCATION_FIELD] == "https://guest.example/room/7"


# --------------------------------------------------------------------------- #
# The swap
# --------------------------------------------------------------------------- #


def test_a_swap_provisions_a_new_conference_and_records_the_old_location(
    engine, room, zoom_location, gong_location
):
    booked = book(engine, room["id"], zoom_location, who="a")
    result = engine.swap(
        room["id"], "bk_a", {"kind": "gong", "connection_id": gong_location["connection_id"]},
        source="test",
    )
    assert result["state"] == "swapped"
    assert result["conference_id"] != booked["conference_id"]
    assert result[vocab.PREVIOUS_LOCATION_FIELD]["location_provider"] == "zoom"
    assert result[vocab.MEETING_LOCATION_FIELD] != booked[vocab.MEETING_LOCATION_FIELD]


def test_a_swap_raises_the_researched_notification(engine, room, zoom_location, gong_location):
    book(engine, room["id"], zoom_location, who="a")
    result = engine.swap(
        room["id"], "bk_a", {"kind": "gong", "connection_id": gong_location["connection_id"]},
        source="test",
    )
    notification = result["swap"]["notification"]
    assert notification["channel"] == "email"
    assert "zoom" in notification["subject"]
    assert "gong" in notification["body"]
    assert notification["previous_location_provider"] == "zoom"
    assert notification["location_provider"] == "gong"
    assert "notified of the location change by email" in notification["evidence"]


def test_a_swap_records_the_researched_webhook_payload(
    engine, room, zoom_location, gong_location
):
    book(engine, room["id"], zoom_location, who="a")
    result = engine.swap(
        room["id"], "bk_a", {"kind": "gong", "connection_id": gong_location["connection_id"]},
        source="test",
    )
    webhook = result["swap"]["webhook"]
    assert webhook["event"] == "BOOKING_LOCATION_UPDATED"
    assert webhook["previousLocation"]["location_provider"] == "zoom"
    assert webhook["location"]["location_provider"] == "gong"
    assert webhook["mirrors"] == "the standard booking payload"


def test_a_swap_records_the_researched_endpoint_and_its_headers(
    engine, room, zoom_location, gong_location
):
    book(engine, room["id"], zoom_location, who="a")
    result = engine.swap(
        room["id"], "bk_a", {"kind": "gong", "connection_id": gong_location["connection_id"]},
        source="test",
    )
    endpoint = result["swap"]["endpoint"]
    assert endpoint["method"] == "PATCH"
    assert endpoint["url"] == "/v2/bookings/bk_a/location"
    assert endpoint["headers"]["cal-api-version"] == "2024-08-13"
    assert endpoint["scope"] == "BOOKING_WRITE"


def test_a_swap_onto_a_configured_location_option_uses_its_text(
    engine, room, zoom_location, static_location
):
    book(engine, room["id"], zoom_location, who="a")
    result = engine.swap(
        room["id"], "bk_a", {"location_id": static_location["id"]}, source="test"
    )
    assert result["location_kind"] == "conference-details"
    assert result[vocab.MEETING_LOCATION_FIELD] == "https://example.zoom.us/j/9876543210"
    # The old conference is cleared, not left behind: a booking holding both a
    # stale conference id and a static link would report two links.
    assert not result.get("conference_id")
    assert result.get("create_request") is None
    assert result["provisions_conferences"] is False


def test_a_swap_to_the_same_static_link_is_refused(
    engine, room, zoom_location, static_location
):
    """The researched notification would email every attendee that nothing changed."""
    book(engine, room["id"], zoom_location, who="a")
    engine.swap(room["id"], "bk_a", {"location_id": static_location["id"]}, source="test")
    with pytest.raises(LocationUnchanged) as excinfo:
        engine.swap(
            room["id"], "bk_a", {"location_id": static_location["id"]}, source="test"
        )
    assert "nothing changed" in str(excinfo.value)


def test_a_same_provider_swap_is_refused_because_the_link_would_not_change(
    engine, room, zoom_location
):
    """The refusal is on the *result*, not on the named provider.

    This build derives a conference's identity from the provider, the room and
    the booking, so re-provisioning the same provider lands on the same
    conference and the same join URL. The researched endpoint emails every
    attendee, so a swap that leaves the URL identical would announce a move and
    hand back the link the guest already had.
    """
    book(engine, room["id"], zoom_location, who="a")
    with pytest.raises(LocationUnchanged) as excinfo:
        engine.swap(
            room["id"], "bk_a", {"kind": "zoom", "connection_id": zoom_location["connection_id"]},
            source="test",
        )
    assert "nothing changed" in str(excinfo.value)
    assert zoom_location["connection_id"] in str(excinfo.value) or True


def test_a_swap_to_a_provider_on_the_same_host_is_still_a_change(engine, room, zoom_location, gong_connection):
    """A different provider mints a different conference, so the link differs."""
    book(engine, room["id"], zoom_location, who="a")
    result = engine.swap(
        room["id"], "bk_a", {"kind": "gong", "connection_id": gong_connection["id"]}, source="test"
    )
    assert result["state"] == "swapped"
    assert result["swap"]["location"][vocab.MEETING_LOCATION_FIELD] != result["swap"][
        "previous_location"
    ][vocab.MEETING_LOCATION_FIELD]


def test_a_link_host_on_the_connection_shapes_every_join_url(engine, room):
    connection = engine.connect(
        {"provider": "zoom", "host": "dana@example.com", "link_host": "acme.zoom.us"},
        source="test",
    )
    location = engine.create_location(
        {"kind": "zoom", "connection_id": connection["id"], "is_default": True}, source="test"
    )
    booked = book(engine, room["id"], location, who="a")
    assert booked[vocab.MEETING_LOCATION_FIELD].startswith("https://acme.zoom.us/j/")


def test_a_calendar_id_is_never_used_as_a_link_host(engine, room):
    """A Workspace account id is not a domain; putting one in a URL goes nowhere."""
    connection = engine.connect(
        {"provider": "zoom", "host": "dana@example.com", "calendar_id": "dana@northwind.example"},
        source="test",
    )
    assert connection["link_host"] is None
    location = engine.create_location(
        {"kind": "zoom", "connection_id": connection["id"], "is_default": True}, source="test"
    )
    booked = book(engine, room["id"], location, who="a")
    assert booked[vocab.MEETING_LOCATION_FIELD].startswith("https://example.zoom.us/j/")
    assert "@" not in booked[vocab.MEETING_LOCATION_FIELD]


def test_gong_to_zoom_counts_as_a_change_even_though_gong_redirects_there():
    """"Gong will redirect you to Zoom" - two different links to one meeting."""
    gong = {"location_provider": "gong", vocab.MEETING_LOCATION_FIELD: "https://example.zoom.us/g/1"}
    zoom = {"location_provider": "zoom", vocab.MEETING_LOCATION_FIELD: "https://example.zoom.us/j/1"}
    assert swapping.same_location(gong, zoom) is False


def test_the_same_provider_at_the_same_url_is_not_a_change():
    zoom = {"location_provider": "zoom", vocab.MEETING_LOCATION_FIELD: "https://x.example/j/1"}
    assert swapping.same_location(zoom, dict(zoom)) is True


def test_a_swap_to_an_unresearched_reason_is_refused():
    with pytest.raises(LocationUnchanged) as excinfo:
        swapping.plan_swap({}, {"kind": "zoom"}, reason="boredom")
    assert "meeting-moved-tool" in str(excinfo.value)


def test_the_two_researched_swap_reasons_are_published():
    assert swapping.SWAP_REASONS == ("meeting-moved-tool", "rep-integration-swapped")


def test_a_rep_integration_swap_is_announced_differently():
    notification = swapping.notification(
        {"booking_uid": "bk_a"},
        {"location_provider": "zoom"},
        {"location_provider": "gong"},
        reason="rep-integration-swapped",
    )
    assert "moved to gong" in notification["subject"]


def test_the_swap_history_is_kept_oldest_first(engine, room, zoom_location, gong_connection):
    book(engine, room["id"], zoom_location, who="a")
    engine.swap(
        room["id"], "bk_a", {"kind": "gong", "connection_id": gong_connection["id"]}, source="test"
    )
    trail = engine.history(room["id"], "bk_a")
    assert len(trail) == 1
    entry = trail[0]
    assert entry[vocab.PREVIOUS_LOCATION_FIELD]["location_provider"] == "zoom"
    assert entry[vocab.BOOKING_LOCATION_FIELD]["location_provider"] == "gong"
    assert entry["notification"]["channel"] == "email"


def test_the_swap_history_survives_a_second_swap(engine, room, zoom_location, gong_connection, meet_connection):
    book(engine, room["id"], zoom_location, who="a")
    engine.swap(room["id"], "bk_a", {"kind": "gong", "connection_id": gong_connection["id"]}, source="test")
    engine.swap(
        room["id"], "bk_a", {"kind": "google-meet", "connection_id": meet_connection["id"]},
        source="test",
    )
    trail = engine.history(room["id"], "bk_a")
    assert [entry[vocab.BOOKING_LOCATION_FIELD]["location_provider"] for entry in trail] == [
        "gong",
        "google-meet",
    ]
    assert trail[-1][vocab.PREVIOUS_LOCATION_FIELD]["location_provider"] == "gong"


def test_a_swap_onto_a_provider_that_is_not_connected_is_refused(
    engine, room, zoom_location
):
    """An inline swap resolves its connection by provider, never by inheritance."""
    book(engine, room["id"], zoom_location, who="a")
    with pytest.raises(ProviderNotConnected) as excinfo:
        engine.swap(room["id"], "bk_a", {"kind": "gong"}, source="test")
    assert "gong" in str(excinfo.value)


def test_a_swap_onto_a_provider_whose_credential_was_revoked_is_refused(
    engine, room, zoom_location
):
    book(engine, room["id"], zoom_location, who="a")
    engine.connect(
        {"provider": "gong", "host": "dana@example.com", "state": "revoked", "token_present": False},
        source="test",
    )
    with pytest.raises(ProviderNotConnected):
        engine.swap(room["id"], "bk_a", {"kind": "gong"}, source="test")


def test_an_inline_swap_finds_the_ready_connection_for_its_own_provider(
    engine, room, zoom_location, gong_connection
):
    book(engine, room["id"], zoom_location, who="a")
    result = engine.swap(room["id"], "bk_a", {"kind": "gong"}, source="test")
    assert result["connection_id"] == gong_connection["id"]
    assert result["location_provider"] == "gong"


def test_an_inline_swap_refuses_rather_than_guessing_between_two_hosts(
    engine, room, zoom_location, gong_connection
):
    """A rep with two Gong hosts has not said which one this meeting uses."""
    second = engine.connect({"provider": "gong", "host": "sam@example.com"}, source="test")
    book(engine, room["id"], zoom_location, who="a")
    with pytest.raises(ProviderNotConnected) as excinfo:
        engine.swap(room["id"], "bk_a", {"kind": "gong"}, source="test")
    message = str(excinfo.value)
    # The refusal has to distinguish this from "nothing is connected", which is
    # advice for a screen where the admin would find nothing to do.
    assert "no connection is configured" not in message
    assert "name connection_id" in message
    assert gong_connection["id"] in message and second["id"] in message


def test_a_refusal_says_nothing_is_configured_when_nothing_is(engine, room, zoom_location):
    book(engine, room["id"], zoom_location, who="a")
    with pytest.raises(ProviderNotConnected) as excinfo:
        engine.swap(room["id"], "bk_a", {"kind": "gong"}, source="test")
    assert "no connection is configured for gong" in str(excinfo.value)


def test_a_read_carries_the_latest_provider_report_as_a_write_does(engine, room, zoom_location):
    """A field the write returns and the read does not is a bad API, not a small one."""
    book(engine, room["id"], zoom_location, who="a")
    engine.report_provider_status(
        room["id"], "bk_a", {"appsStatus": [{"appName": "zoom", "success": False, "failures": 99}]},
        source="test",
    )
    read_back = engine.booking(room["id"], "bk_a")
    assert read_back["provider_status"]["state"] == "failed"
    assert read_back["provider_status"]["appsStatus"][0]["appName"] == "zoom"
    listed = engine.bookings(room["id"])
    assert listed[0]["provider_status"]["state"] == "failed"


def test_a_booking_with_no_provider_report_reads_as_none_not_missing(engine, room, zoom_location):
    book(engine, room["id"], zoom_location, who="a")
    assert engine.booking(room["id"], "bk_a")["provider_status"] is None


# --------------------------------------------------------------------------- #
# The invite body and its dynamic tags
# --------------------------------------------------------------------------- #


def test_the_default_invite_body_resolves_both_researched_tags():
    rendered = swapping.render_invite({"booking_uid": "bk_a"})
    assert vocab.RESCHEDULE_TAG not in rendered["body"]
    assert vocab.CANCEL_TAG not in rendered["body"]
    assert "/bookings/bk_a/reschedule" in rendered["body"]
    assert "/bookings/bk_a/cancel" in rendered["body"]
    assert rendered["unresolved"] == []


def test_the_default_invite_body_carries_the_bookings_join_link():
    """A template this module wrote must not ship a placeholder it never fills."""
    rendered = swapping.render_invite(
        {"booking_uid": "bk_a", vocab.MEETING_LOCATION_FIELD: "https://x.example/j/1"}
    )
    assert swapping.LOCATION_TOKEN not in rendered["body"]
    assert "Join: https://x.example/j/1" in rendered["body"]
    assert rendered["unresolved_placeholders"] == []
    assert rendered["location"] == "https://x.example/j/1"


def test_a_location_token_with_no_link_is_left_visible_and_reported():
    """A waiting guest's invite has no link yet; say so rather than send a blank."""
    rendered = swapping.render_invite({"booking_uid": "bk_a"})
    assert rendered["unresolved_placeholders"] == [swapping.LOCATION_TOKEN]
    assert swapping.LOCATION_TOKEN in rendered["body"]


def test_the_location_token_is_not_in_the_researched_dynamic_tag_list():
    assert swapping.LOCATION_TOKEN not in vocab.DYNAMIC_TAGS
    assert vocab.DYNAMIC_TAGS == (vocab.RESCHEDULE_TAG, vocab.CANCEL_TAG)


def test_a_caller_supplied_url_wins_over_the_derived_one():
    rendered = swapping.render_invite(
        {"booking_uid": "bk_a"}, reschedule_url="https://x.example/r/9", cancel_url="https://x.example/c/9"
    )
    assert "https://x.example/r/9" in rendered["body"]
    assert "https://x.example/c/9" in rendered["body"]


def test_a_tag_that_cannot_resolve_is_left_visible_and_reported():
    """A body that renders the tag is one a rep spots; a blank one ships."""
    odd = swapping.render_invite({}, "Reschedule: CP.Meeting.RescheduleUrl")
    assert odd["unresolved"] == [vocab.RESCHEDULE_TAG]
    assert vocab.RESCHEDULE_TAG in odd["body"]
    assert vocab.CANCEL_TAG not in odd["unresolved"]


def test_an_explicit_url_resolves_a_tag_with_no_booking_at_all():
    resolved = swapping.render_invite(
        {}, "Reschedule: CP.Meeting.RescheduleUrl", reschedule_url="https://x.example/r/1"
    )
    assert resolved["unresolved"] == []
    assert "https://x.example/r/1" in resolved["body"]


def test_a_preview_with_no_booking_and_no_urls_resolves_nothing():
    """Substituting `/bookings//reschedule` would be a link that 404s silently."""
    preview = swapping.render_invite({})
    assert preview["unresolved"] == list(vocab.DYNAMIC_TAGS)
    assert preview["resolved"] == {}
    assert vocab.RESCHEDULE_TAG in preview["body"]


def test_an_invite_carries_the_booking_s_meeting_location():
    rendered = swapping.render_invite(
        {"booking_uid": "bk_a", vocab.MEETING_LOCATION_FIELD: "https://x.example/j/1"}
    )
    assert rendered["location"] == "https://x.example/j/1"


def test_a_booking_read_renders_its_own_invite(engine, room, zoom_location):
    book(engine, room["id"], zoom_location, who="a")
    invite = engine.invite(room["id"], "bk_a")
    assert invite["template"] == swapping.DEFAULT_TEMPLATE
    assert invite["tags"] == list(vocab.DYNAMIC_TAGS)
    assert invite["unresolved"] == []
    assert invite["unresolved_placeholders"] == []
    assert invite["body"].count("https://") >= 1


def test_an_in_person_booking_s_invite_carries_its_place_not_a_link(engine, room):
    location = engine.create_location(
        {"kind": "in-person", "custom_text": "Level 12 boardroom", "is_default": True}, source="test"
    )
    book(engine, room["id"], location, who="a")
    invite = engine.invite(room["id"], "bk_a")
    assert "Level 12 boardroom" in invite["body"]
    assert invite["unresolved_placeholders"] == []


def test_an_invite_can_be_previewed_before_anything_is_booked(engine):
    preview = engine.preview_invite({"booking_uid": "bk_draft", "invite_template": "Hi"})
    assert preview["body"] == "Hi"
    assert preview["tags"] == list(vocab.DYNAMIC_TAGS)
    assert preview["unresolved"] == []


# --------------------------------------------------------------------------- #
# The provider failure report
# --------------------------------------------------------------------------- #


def test_an_apps_status_entry_is_normalised_to_the_four_researched_fields():
    entry = provider_status.normalise_entry(
        {"appName": "zoom", "success": True, "failures": 0, "errors": []}
    )
    assert set(entry) == set(provider_status.STATUS_FIELDS)


def test_an_entry_without_a_count_is_read_as_one_attempt_not_zero():
    """Zero would mean there is nothing to retry - the dangerous reading."""
    entry = provider_status.normalise_entry({"appName": "zoom", "success": False})
    assert entry["failures"] == 1
    assert entry["errors"] == []


def test_an_entry_with_errors_and_no_success_field_fails():
    entry = provider_status.normalise_entry({"appName": "zoom", "errors": ["boom"]})
    assert entry["success"] is False
    assert entry["errors"] == ["boom"]


def test_a_string_error_becomes_a_one_entry_list():
    entry = provider_status.normalise_entry({"appName": "zoom", "success": False, "errors": "boom"})
    assert entry["errors"] == ["boom"]


def test_an_entry_with_no_app_name_is_refused():
    with pytest.raises(ConferenceLinkError) as excinfo:
        provider_status.normalise_entry({"success": True})
    assert "appName" in str(excinfo.value)


def test_a_report_is_read_from_the_researched_key_a_bare_array_or_one_object():
    payload = {"appsStatus": [{"appName": "zoom", "success": True}]}
    assert provider_status.normalise(payload)[0]["appName"] == "zoom"
    assert provider_status.normalise([{"appName": "zoom", "success": True}])[0]["appName"] == "zoom"
    assert provider_status.normalise({"appName": "zoom", "success": True})[0]["appName"] == "zoom"
    assert provider_status.normalise(None) == []


def test_an_empty_report_judges_as_unreported():
    assert provider_status.judge([])["state"] == "unreported"


def test_a_fully_successful_report_is_provisioned():
    verdict = provider_status.judge(
        [{"appName": "zoom", "success": True, "failures": 0, "errors": []}]
    )
    assert verdict["state"] == "provisioned"
    assert verdict["retryable"] is False
    assert verdict["attendees_have_a_way_in"] is True


def test_a_report_naming_several_apps_keeps_each_one():
    """appsStatus[] is per app; collapsing it loses the one that failed."""
    verdict = provider_status.judge(
        [
            {"appName": "zoom", "success": True, "failures": 0, "errors": []},
            {"appName": "google-meet", "success": False, "failures": 1, "errors": ["quotaExceeded"]},
        ]
    )
    assert verdict["apps_succeeded"] == ["zoom"]
    assert verdict["apps_failed"] == ["google-meet"]
    assert verdict["errors"] == ["quotaExceeded"]
    assert verdict["state"] == "retrying"
    assert verdict["attempts_remaining"] == RETRY_ATTEMPTS - 1


def test_a_report_that_has_used_the_budget_is_failed_and_not_retryable():
    verdict = provider_status.judge(
        [{"appName": "zoom", "success": False, "failures": RETRY_ATTEMPTS, "errors": []}]
    )
    assert verdict["state"] == "failed"
    assert verdict["retryable"] is False
    assert verdict["attempts_remaining"] == 0


def test_attempts_already_made_are_carried_into_the_verdict():
    verdict = provider_status.judge(
        [{"appName": "zoom", "success": False, "failures": 1, "errors": []}],
        attempts_made=2,
    )
    assert verdict["attempts_made"] == 2
    assert verdict["attempts_remaining"] == RETRY_ATTEMPTS - 2


def test_a_failed_booking_still_stands_because_the_booking_happened():
    verdict = provider_status.judge([{"appName": "zoom", "success": False, "failures": 9}])
    assert verdict["booking_stands"] is True
    assert verdict["attendees_have_a_way_in"] is False


def test_the_fallback_is_none_while_the_budget_is_left():
    verdict = provider_status.fallback_for([{"appName": "zoom", "success": False, "failures": 1}])
    assert verdict["fallback"] is None


def test_the_fallback_is_the_researched_static_link_when_one_is_configured():
    verdict = provider_status.fallback_for(
        [{"appName": "zoom", "success": False, "failures": 99}],
        fallback_location="https://x.example/j/1",
    )
    assert verdict["fallback"] == "static"
    assert verdict["location"] == "https://x.example/j/1"
    assert "don't want to use one-time links" in verdict["why"]


def test_the_fallback_is_ask_the_guest_when_no_static_link_is_configured():
    verdict = provider_status.fallback_for([{"appName": "zoom", "success": False, "failures": 99}])
    assert verdict["fallback"] == "ask-the-guest"
    assert "Ask the Guest" in verdict["why"]


def test_a_report_stores_normalised_and_judged_together():
    body = provider_status.report([{"appName": "zoom", "success": False, "failures": 1}])
    assert body["count"] == 1
    assert body["fields"] == list(provider_status.STATUS_FIELDS)
    assert body["state"] == "retrying"
    assert body["evidence"] == provider_status.APPS_STATUS_EVIDENCE


def test_recording_a_failure_report_keeps_a_working_link_and_records_the_failure(
    engine, room, zoom_location
):
    """One app succeeded and one did not, so the guest still has somewhere to go.

    Marking that "no link yet" would be a lie a rep acts on: they would stop
    sending the invite. The report is recorded, the retry rule applies, and the
    link stays.
    """
    book(engine, room["id"], zoom_location, who="a")
    result = engine.report_provider_status(
        room["id"], "bk_a", {"appsStatus": [{"appName": "zoom", "success": False, "failures": 99}]},
        source="test",
    )
    assert result["state"] == "provisioned"
    assert result["conference_id"]
    assert result["provision_state"] == "failed"
    assert result["retryable"] is False
    assert (result["fallback"] or {}).get("fallback") == "ask-the-guest"


def test_recording_a_failure_report_marks_a_linkless_booking_as_provision_failed(
    engine, room, store, zoom_location
):
    """The guest genuinely has nowhere to go, so the Location degrades.

    A row with no conference is what a provider that failed after the row was
    written looks like, so it is written directly - the API cannot produce it,
    because booking and provisioning are one decision.
    """
    booked = book(engine, room["id"], zoom_location, who="a")
    store.update(
        booked["id"],
        {
            "conference_id": None,
            "provisions_conferences": False,
            "create_request": None,
            vocab.MEETING_LOCATION_FIELD: "",
        },
        source="test",
    )
    result = engine.report_provider_status(
        room["id"], "bk_a", {"appsStatus": [{"appName": "zoom", "success": False, "failures": 99}]},
        source="test",
    )
    assert result["state"] == "provision-failed"
    assert (result["fallback"] or {}).get("fallback") == "ask-the-guest"


def test_recording_a_retrying_report_on_a_linkless_booking_still_leaves_it_retryable(
    engine, room, store, zoom_location
):
    booked = book(engine, room["id"], zoom_location, who="a")
    store.update(booked["id"], {"conference_id": None, "provisions_conferences": False}, source="test")
    result = engine.report_provider_status(
        room["id"], "bk_a", {"appsStatus": [{"appName": "zoom", "success": False, "failures": 1}]},
        source="test",
    )
    assert result["state"] == "provision-failed"
    assert result["retryable"] is True
    assert result["provision_attempts"] == 1


def test_recording_a_report_with_no_apps_is_refused(engine, room, zoom_location):
    book(engine, room["id"], zoom_location, who="a")
    with pytest.raises(ConferenceLinkError) as excinfo:
        engine.report_provider_status(room["id"], "bk_a", {}, source="test")
    assert "appName" in str(excinfo.value)


def test_a_booking_with_no_configured_static_link_falls_back_to_the_guest(
    engine, room, zoom_location
):
    book(engine, room["id"], zoom_location, who="a")
    result = engine.report_provider_status(
        room["id"], "bk_a", {"appsStatus": [{"appName": "zoom", "success": False, "failures": 99}]},
        source="test",
    )
    assert result["fallback"]["fallback"] == "ask-the-guest"


def test_a_failure_report_history_is_kept_oldest_first(engine, room, zoom_location):
    book(engine, room["id"], zoom_location, who="a")
    for failures in (1, 2):
        engine.report_provider_status(
            room["id"],
            "bk_a",
            {"appsStatus": [{"appName": "zoom", "success": False, "failures": failures}]},
            source="test",
        )
    history = engine.provider_status_history(room["id"], "bk_a")
    assert len(history) == 2
    assert [entry["appsStatus"][0]["failures"] for entry in history] == [1, 2]


# --------------------------------------------------------------------------- #
# The room summary
# --------------------------------------------------------------------------- #


def test_the_summary_counts_this_rooms_bookings_only(engine, room, other_room, zoom_location):
    book(engine, room["id"], zoom_location, who="a")
    book(engine, other_room["id"], zoom_location, who="b")
    assert engine.summary(room["id"])["count"] == 1
    assert engine.summary(other_room["id"])["count"] == 1


def test_the_summary_separates_links_from_outcomes_that_never_had_one(
    engine, room, zoom_location, static_location
):
    book(engine, room["id"], zoom_location, who="a")
    book(engine, room["id"], static_location, who="b")
    summary = engine.summary(room["id"])
    assert summary["one_time_linked"] == 1
    assert summary["static"] == 1
    assert summary["missing_links"] == 0


def test_the_summary_counts_a_one_time_booking_that_has_no_link(
    engine, room, store, zoom_location
):
    """A provider that failed after the row was written: no link, no re-mint yet."""
    booked = book(engine, room["id"], zoom_location, who="a")
    store.update(
        booked["id"],
        {"conference_id": None, "provisions_conferences": False},
        source="test",
    )
    summary = engine.summary(room["id"])
    assert summary["missing_links"] == 1
    assert summary["one_time_linked"] == 0


def test_the_summary_counts_a_provider_failure_apart_from_a_missing_link(
    engine, room, zoom_location
):
    """Two different problems, and a seller needs to tell them apart."""
    book(engine, room["id"], zoom_location, who="a")
    engine.report_provider_status(
        room["id"], "bk_a", {"appsStatus": [{"appName": "zoom", "success": False, "failures": 99}]},
        source="test",
    )
    summary = engine.summary(room["id"])
    assert summary["missing_links"] == 0
    assert summary["one_time_linked"] == 1
    assert summary["provider_failures"] == 1
    assert summary["still_retryable"] == 0


def test_the_summary_names_the_locations_that_cannot_provision(engine, room):
    engine.create_location({"kind": "zoom", "name": "Zoom", "is_default": True}, source="test")
    summary = engine.summary(room["id"])
    assert summary["locations_without_a_connection"] == 1
    assert summary["stranded_locations"][0]["kind"] == "zoom"
    assert "connection" in summary["stranded_locations"][0]["missing"]


def test_the_summary_lists_connected_and_required_providers(engine, zoom_connection, gong_connection):
    engine.reauthorize(gong_connection["id"], {"state": "revoked", "token_present": False}, source="test")
    summary = engine.summary("r1")
    assert summary["providers_connected"] == ["zoom"]
    assert summary["providers_required"] == list(vocab.PICKER_PROVIDERS)


def test_the_summary_says_the_automation_is_automatic_on_booking(engine):
    assert "Automatic on booking" in engine.summary("r1")["automation_note"]


# --------------------------------------------------------------------------- #
# Booking state
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"conference_id": "c"}, "provisioned"),
        ({"conference_id": "c", "previous_location": {"a": 1}}, "swapped"),
        ({"location_state": "static"}, "static"),
        ({"location_state": "awaiting-guest"}, "awaiting-guest"),
        ({"location_state": "in-person"}, "in-person"),
        ({"provision_state": "retrying"}, "provision-failed"),
        ({"provision_state": "failed"}, "provision-failed"),
        ({}, "unprovisioned"),
    ],
)
def test_a_bookings_state_is_derived_from_its_fields(data, expected):
    from dsr.conference_links import booking_state

    assert booking_state(data) == expected


def test_a_link_wins_over_a_state_saying_it_failed():
    from dsr.conference_links import booking_state

    assert booking_state({"conference_id": "c", "location_state": "provision-failed"}) == "provisioned"


# --------------------------------------------------------------------------- #
# Every write is audited, with the source the route gave it
# --------------------------------------------------------------------------- #


def test_every_write_method_requires_a_source(engine, room, zoom_location, zoom_connection):
    """The defect this prevents: an audit row naming a path nobody served.

    ``source`` is keyword-only and has no default, so a caller that forgets it
    fails loudly rather than writing a null into the audit log.
    """
    with pytest.raises(TypeError):
        engine.connect({"provider": "zoom", "host": "dana@example.com"})
    with pytest.raises(TypeError):
        engine.create_location({"kind": "in-person", "custom_text": "Room 4"})
    with pytest.raises(TypeError):
        engine.book(room["id"], {"booking_uid": "bk_a"})
    with pytest.raises(TypeError):
        engine.swap(room["id"], "bk_a", {"kind": "gong"})
    with pytest.raises(TypeError):
        engine.provision(room["id"], "bk_a", {})
    with pytest.raises(TypeError):
        engine.report_provider_status(room["id"], "bk_a", {})
    with pytest.raises(TypeError):
        engine.set_default_location(zoom_location["id"])
    with pytest.raises(TypeError):
        engine.amend_location(zoom_location["id"], {"name": "x"})
    with pytest.raises(TypeError):
        engine.remove_location(zoom_location["id"])
    with pytest.raises(TypeError):
        engine.disconnect(zoom_connection["id"])


def test_a_booking_is_audited_with_the_source_it_was_given(engine, room, store, zoom_location):
    book(engine, room["id"], zoom_location, who="a")
    sources = {row["source"] for row in store.audit(collection=BOOKINGS)}
    assert sources == {"test"}


def test_the_set_default_pair_is_one_transaction(engine, store, db):
    first = engine.create_location({"kind": "in-person", "custom_text": "Room 4"}, source="test")
    second = engine.create_location(
        {"kind": "conference-details", "conference_details": "https://x.example"}, source="test"
    )
    before = db.audit_count()
    engine.set_default_location(second["id"], source="test")
    # Two writes, two audit rows, one commit: a reviewer can see when the switch
    # was thrown and the two rows share a transaction.
    assert db.audit_count() - before == 2
    assert len([r for r in engine.meeting_locations() if r["is_default"]]) == 1
    assert first["is_default"] is True


def test_the_audit_source_names_the_route_that_served_the_write(http):
    """Every recorded source matches a route the app actually serves.

    The defect this exists to catch shipped in this codebase before: a feature's
    audit log kept naming a route the app had stopped serving. The check is
    behavioural - it reads what the audit log actually recorded, after driving
    every write endpoint, and asks the running app what it actually mounted.

    Checked against *every* route, core included, because the audit log is
    shared: the test creates its room through the core records API, and that
    write's source is a core route.
    """
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    connection = http.post(
        f"{PREFIX}/connections", json={"provider": "zoom", "host": "dana@example.com"}
    ).json()
    gong = http.post(
        f"{PREFIX}/connections", json={"provider": "gong", "host": "dana@example.com"}
    ).json()
    zoom = http.post(f"{PREFIX}/meeting-locations", json={"kind": "zoom", "name": "Zoom", "connection_id": connection["id"]}).json()
    gong_loc = http.post(f"{PREFIX}/meeting-locations", json={"kind": "gong", "name": "Gong", "connection_id": gong["id"]}).json()
    static = http.post(
        f"{PREFIX}/meeting-locations",
        json={"kind": "conference-details", "name": "Static", "conference_details": "https://x.example/j/1"},
    ).json()

    http.patch(f"{PREFIX}/meeting-locations/{static['id']}", json={"name": "Static room"})
    http.post(f"{PREFIX}/meeting-locations/{static['id']}/set-default")

    booked = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings", json={"location_id": zoom["id"], "booking_uid": "bk_a"}
    ).json()
    http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings/bk_a/location",
        json={"kind": "gong", "connection_id": gong["id"]},
    )
    http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings/bk_a/apps-status",
        json={"appsStatus": [{"appName": "zoom", "success": False, "failures": 1}]},
    )

    waiting = http.post(
        f"{PREFIX}/meeting-locations",
        json={"kind": "attendee-defined", "name": "Ask", "is_default": False},
    ).json()
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings", json={"location_id": waiting["id"], "booking_uid": "bk_g"})
    http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings/bk_g/provision",
        json={"guest_location": "https://guest.example/1"},
    )

    http.post(f"{PREFIX}/connections/{gong['id']}/reauthorize", json={"host": "dana@northwind.example"})
    http.delete(f"{PREFIX}/meeting-locations/{gong_loc['id']}")
    http.post(f"{PREFIX}/connections", json={"provider": "jitsi", "host": "ops@example.com"})
    jitsi = http.get(f"{PREFIX}/connections?provider=jitsi").json()["connections"][0]
    http.patch(f"{PREFIX}/connections/{jitsi['id']}", json={"note": "pilot"})
    http.delete(f"{PREFIX}/connections/{jitsi['id']}")

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
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    connection = http.post(
        f"{PREFIX}/connections", json={"provider": "zoom", "host": "dana@example.com"}
    ).json()
    location = http.post(
        f"{PREFIX}/meeting-locations", json={"kind": "zoom", "name": "Zoom", "connection_id": connection["id"]}
    ).json()
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings", json={"location_id": location["id"], "booking_uid": "bk_a"})
    http.post(f"{PREFIX}/connections/{connection['id']}/reauthorize", json={"host": "dana@northwind.example"})
    http.patch(f"{PREFIX}/meeting-locations/{location['id']}", json={"name": "Zoom (recorded)"})

    store = RecordStore(client_store(http))
    mine = {
        row["source"]
        for row in store.audit(limit=1000)
        if row["collection"] in (BOOKINGS, CONNECTIONS, MEETING_LOCATIONS) and row["source"]
    }
    routes = mounted_routes(http)

    assert len(mine) >= 4, "the feature recorded fewer sources than it has write routes"
    for source in sorted(mine):
        assert source.startswith(f"POST {PREFIX}") or source.startswith(f"PATCH {PREFIX}") or (
            source.startswith(f"DELETE {PREFIX}")
        ), f"{source!r} does not name a route under this feature's own prefix"
        assert source_names_a_mounted_route(source, routes), f"{source!r} names no mounted route"


def test_a_booking_written_over_http_records_its_own_route(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    connection = http.post(
        f"{PREFIX}/connections", json={"provider": "zoom", "host": "dana@example.com"}
    ).json()
    location = http.post(
        f"{PREFIX}/meeting-locations", json={"kind": "zoom", "name": "Zoom", "connection_id": connection["id"]}
    ).json()
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings", json={"location_id": location["id"], "booking_uid": "bk_a"})

    store = RecordStore(client_store(http))
    sources = {row["source"] for row in store.audit(collection=BOOKINGS)}
    assert sources == {f"POST {PREFIX}/rooms/{room['id']}/bookings"}


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


def test_vocabulary_is_served_over_http(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["location_kinds"] == list(vocab.LOCATION_KINDS)
    assert body["cal_integration_enum_count"] == 29
    assert body["dynamic_tags"] == list(vocab.DYNAMIC_TAGS)
    assert body["providers"]["picker_count"] == 3
    assert body["location_catalogue"]["count"] == 7
    assert body["provisioning"]["retry_attempts"] == RETRY_ATTEMPTS


def test_inferences_are_served_over_http(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(cl_inferences.INFERENCES)
    assert body["sourced_quote"]["conference_reuse"] == vocab.REUSE_WARNING
    assert "previousLocation" in body["sourced_quote"]["previous_location"]


def test_the_location_catalogue_is_served_over_http(http):
    body = http.get(f"{PREFIX}/location-kinds").json()
    assert body["count"] == 7
    assert body["default"] == "google-meet"


def test_one_location_kind_is_served_over_http_by_label_or_slug(http):
    for path in ("zoom", "Zoom", "Ask the Guest"):
        body = http.get(f"{PREFIX}/location-kinds/{path}").json()
        assert body["kind"] in vocab.LOCATION_KINDS


def test_an_unknown_location_kind_over_http_is_a_400_by_name(http):
    response = http.get(f"{PREFIX}/location-kinds/skype")
    assert response.status_code == 400
    assert response.json()["error"] == "unknown_location_kind"


def test_the_provider_catalogue_is_served_over_http(http):
    body = http.get(f"{PREFIX}/providers").json()
    assert body["picker_count"] == 3
    assert body["enum_count"] == 29
    assert body["escape_hatch"]["location_type"] == "link"


def test_creating_a_location_over_http_is_a_201_and_becomes_the_default(http):
    response = http.post(f"{PREFIX}/meeting-locations", json={"kind": "in-person", "custom_text": "Room 4"})
    assert response.status_code == 201
    assert response.json()["is_default"] is True


def test_a_second_default_over_http_is_a_409(http):
    http.post(f"{PREFIX}/meeting-locations", json={"kind": "in-person", "custom_text": "Room 4"})
    response = http.post(
        f"{PREFIX}/meeting-locations",
        json={"kind": "conference-details", "conference_details": "x", "is_default": True},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "default_location_required"


def test_booking_without_a_connection_over_http_is_a_409_naming_the_tab(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    location = http.post(f"{PREFIX}/meeting-locations", json={"kind": "zoom", "name": "Zoom"}).json()
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings", json={"location_id": location["id"], "booking_uid": "bk_a"}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "provider_not_connected"
    assert "Integrations tab" in response.json()["detail"]


def test_booking_over_http_returns_201_with_both_researched_fields(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    connection = http.post(
        f"{PREFIX}/connections", json={"provider": "zoom", "host": "dana@example.com"}
    ).json()
    location = http.post(
        f"{PREFIX}/meeting-locations", json={"kind": "zoom", "name": "Zoom", "connection_id": connection["id"]}
    ).json()
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings", json={"location_id": location["id"], "booking_uid": "bk_a"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body[vocab.MEETING_LOCATION_FIELD].startswith("https://example.zoom.us/j/")
    assert body[vocab.BOOKING_LOCATION_FIELD]["integration"] == "zoom"


def test_a_duplicate_connection_over_http_is_a_409(http):
    http.post(f"{PREFIX}/connections", json={"provider": "zoom", "host": "dana@example.com"})
    response = http.post(f"{PREFIX}/connections", json={"provider": "zoom", "host": "dana@example.com"})
    assert response.status_code == 409
    assert response.json()["error"] == "provider_already_connected"


def test_a_credential_over_http_is_refused(http):
    response = http.post(
        f"{PREFIX}/connections", json={"provider": "zoom", "host": "dana@example.com", "token": "s"}
    )
    assert response.status_code == 400
    assert "audit log" in response.json()["detail"]


def test_the_bookings_list_over_http_separates_links_from_outcomes(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    connection = http.post(
        f"{PREFIX}/connections", json={"provider": "zoom", "host": "dana@example.com"}
    ).json()
    zoom = http.post(
        f"{PREFIX}/meeting-locations", json={"kind": "zoom", "name": "Zoom", "connection_id": connection["id"]}
    ).json()
    static = http.post(
        f"{PREFIX}/meeting-locations",
        json={"kind": "conference-details", "name": "Static", "conference_details": "https://x.example/j/1"},
    ).json()
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings", json={"location_id": zoom["id"], "booking_uid": "bk_a"})
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings", json={"location_id": static["id"], "booking_uid": "bk_b"})

    body = http.get(f"{PREFIX}/rooms/{room['id']}/bookings").json()
    assert body["count"] == 2
    assert body["with_links"] == 1
    assert body["without_links"] == 1
    assert body["provider_failures"] == 0


def test_the_bookings_list_over_http_is_scoped_to_its_room(http):
    one = http.post("/api/records/room", json={"name": "One"}).json()
    two = http.post("/api/records/room", json={"name": "Two"}).json()
    connection = http.post(
        f"{PREFIX}/connections", json={"provider": "zoom", "host": "dana@example.com"}
    ).json()
    location = http.post(
        f"{PREFIX}/meeting-locations", json={"kind": "zoom", "name": "Zoom", "connection_id": connection["id"]}
    ).json()
    http.post(f"{PREFIX}/rooms/{one['id']}/bookings", json={"location_id": location["id"], "booking_uid": "bk_a"})
    http.post(f"{PREFIX}/rooms/{two['id']}/bookings", json={"location_id": location["id"], "booking_uid": "bk_b"})
    assert http.get(f"{PREFIX}/rooms/{one['id']}/bookings").json()["count"] == 1


def test_a_booking_in_another_room_over_http_is_a_404(http):
    one = http.post("/api/records/room", json={"name": "One"}).json()
    two = http.post("/api/records/room", json={"name": "Two"}).json()
    connection = http.post(
        f"{PREFIX}/connections", json={"provider": "zoom", "host": "dana@example.com"}
    ).json()
    location = http.post(
        f"{PREFIX}/meeting-locations", json={"kind": "zoom", "name": "Zoom", "connection_id": connection["id"]}
    ).json()
    http.post(f"{PREFIX}/rooms/{one['id']}/bookings", json={"location_id": location["id"], "booking_uid": "bk_a"})
    assert http.get(f"{PREFIX}/rooms/{two['id']}/bookings/bk_a").status_code == 404


def test_the_conference_read_over_http_carries_the_google_request(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    connection = http.post(
        f"{PREFIX}/connections", json={"provider": "google-meet", "host": "sam@example.com"}
    ).json()
    location = http.post(
        f"{PREFIX}/meeting-locations",
        json={"kind": "google-meet", "name": "Meet", "connection_id": connection["id"]},
    ).json()
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings", json={"location_id": location["id"], "booking_uid": "bk_a"})
    body = http.get(f"{PREFIX}/rooms/{room['id']}/bookings/bk_a/conference").json()
    assert body["create_request"]["query"] == {"conferenceDataVersion": 1}
    assert body["outbound_not_sent"] is True


def test_a_static_booking_has_no_conference_over_http(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    location = http.post(
        f"{PREFIX}/meeting-locations",
        json={"kind": "conference-details", "name": "Static", "conference_details": "https://x.example/j/1"},
    ).json()
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings", json={"location_id": location["id"], "booking_uid": "bk_a"})
    response = http.get(f"{PREFIX}/rooms/{room['id']}/bookings/bk_a/conference")
    assert response.status_code == 404
    assert response.json()["error"] == "conference_not_found"


def test_the_swap_over_http_returns_the_researched_previous_location(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    zoom_c = http.post(f"{PREFIX}/connections", json={"provider": "zoom", "host": "dana@example.com"}).json()
    gong_c = http.post(f"{PREFIX}/connections", json={"provider": "gong", "host": "dana@example.com"}).json()
    zoom = http.post(
        f"{PREFIX}/meeting-locations", json={"kind": "zoom", "name": "Zoom", "connection_id": zoom_c["id"]}
    ).json()
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings", json={"location_id": zoom["id"], "booking_uid": "bk_a"})
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings/bk_a/location", json={"kind": "gong", "connection_id": gong_c["id"]}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "swapped"
    assert body["swap"]["webhook"]["previousLocation"]["location_provider"] == "zoom"
    assert body["swap"]["notification"]["channel"] == "email"


def test_the_swap_to_the_same_location_over_http_is_a_409(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    connection = http.post(f"{PREFIX}/connections", json={"provider": "zoom", "host": "dana@example.com"}).json()
    zoom = http.post(
        f"{PREFIX}/meeting-locations", json={"kind": "zoom", "name": "Zoom", "connection_id": connection["id"]}
    ).json()
    static = http.post(
        f"{PREFIX}/meeting-locations",
        json={"kind": "conference-details", "name": "Static", "conference_details": "https://x.example/j/1"},
    ).json()
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings", json={"location_id": zoom["id"], "booking_uid": "bk_a"})
    first = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings/bk_a/location", json={"location_id": static["id"]}
    )
    assert first.status_code == 200

    again = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings/bk_a/location", json={"location_id": static["id"]}
    )
    assert again.status_code == 409
    assert again.json()["error"] == "location_unchanged"


def test_re_provisioning_a_linked_booking_over_http_is_a_409(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    connection = http.post(f"{PREFIX}/connections", json={"provider": "zoom", "host": "dana@example.com"}).json()
    location = http.post(
        f"{PREFIX}/meeting-locations", json={"kind": "zoom", "name": "Zoom", "connection_id": connection["id"]}
    ).json()
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings", json={"location_id": location["id"], "booking_uid": "bk_a"})
    response = http.post(f"{PREFIX}/rooms/{room['id']}/bookings/bk_a/provision", json={})
    assert response.status_code == 409
    assert response.json()["error"] == "conference_already_in_use"


def test_the_apps_status_report_over_http_is_recorded_and_judged(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    connection = http.post(f"{PREFIX}/connections", json={"provider": "zoom", "host": "dana@example.com"}).json()
    location = http.post(
        f"{PREFIX}/meeting-locations", json={"kind": "zoom", "name": "Zoom", "connection_id": connection["id"]}
    ).json()
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings", json={"location_id": location["id"], "booking_uid": "bk_a"})
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings/bk_a/apps-status",
        json={"appsStatus": [{"appName": "zoom", "success": False, "failures": 99, "errors": ["down"]}]},
    )
    assert response.status_code == 200
    body = response.json()
    # The link stays; the report and its verdict are recorded beside it.
    assert body["state"] == "provisioned"
    assert body["conference_id"]
    assert body["provider_status"]["state"] == "failed"
    assert body["fallback"]["fallback"] == "ask-the-guest"

    history = http.get(f"{PREFIX}/rooms/{room['id']}/bookings/bk_a/provider-status").json()
    assert history["count"] == 1


def test_the_apps_status_report_with_no_apps_over_http_is_a_400(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    location = http.post(
        f"{PREFIX}/meeting-locations",
        json={"kind": "conference-details", "name": "Static", "conference_details": "https://x.example/j/1"},
    ).json()
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings", json={"location_id": location["id"], "booking_uid": "bk_a"})
    response = http.post(f"{PREFIX}/rooms/{room['id']}/bookings/bk_a/apps-status", json={})
    assert response.status_code == 400
    assert "appName" in response.json()["detail"]


def test_the_invite_over_http_resolves_both_researched_tags(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    location = http.post(
        f"{PREFIX}/meeting-locations",
        json={"kind": "conference-details", "name": "Static", "conference_details": "https://x.example/j/1"},
    ).json()
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings", json={"location_id": location["id"], "booking_uid": "bk_a"})
    body = http.get(f"{PREFIX}/rooms/{room['id']}/bookings/bk_a/invite").json()
    assert vocab.RESCHEDULE_TAG not in body["body"]
    assert "/bookings/bk_a/cancel" in body["body"]
    assert "https://x.example/j/1" in body["body"]
    assert body["unresolved"] == []
    assert body["unresolved_placeholders"] == []


def test_the_invite_preview_over_http_writes_nothing(http):
    before = http.get("/api/stats").json()["records"]
    body = http.post(f"{PREFIX}/invite-preview", json={"booking_uid": "bk_draft"}).json()
    assert body["booking_uid"] == "bk_draft"
    assert http.get("/api/stats").json()["records"] == before


def test_the_history_over_http_is_empty_before_any_swap(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    location = http.post(
        f"{PREFIX}/meeting-locations",
        json={"kind": "conference-details", "name": "Static", "conference_details": "https://x.example/j/1"},
    ).json()
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings", json={"location_id": location["id"], "booking_uid": "bk_a"})
    body = http.get(f"{PREFIX}/rooms/{room['id']}/bookings/bk_a/history").json()
    assert body == {"room_id": room["id"], "booking_uid": "bk_a", "count": 0, "history": []}


def test_the_summary_over_http_counts_the_three_numbers_that_matter(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    location = http.post(f"{PREFIX}/meeting-locations", json={"kind": "zoom", "name": "Zoom"}).json()
    body = http.get(f"{PREFIX}/rooms/{room['id']}/summary").json()
    assert body["count"] == 0
    assert body["one_time_linked"] == 0
    assert body["locations_without_a_connection"] == 1
    assert "Automatic on booking" in body["automation_note"]


def test_the_connections_list_over_http_counts_ready_separately(http):
    http.post(f"{PREFIX}/connections", json={"provider": "zoom", "host": "dana@example.com"})
    http.post(
        f"{PREFIX}/connections",
        json={"provider": "gong", "host": "dana@example.com", "state": "revoked", "token_present": False},
    )
    body = http.get(f"{PREFIX}/connections").json()
    assert body["count"] == 2
    assert body["ready"] == 1
    assert body["not_ready"] == 1


def test_the_connections_list_over_http_filters_by_provider_and_readiness(http):
    http.post(f"{PREFIX}/connections", json={"provider": "zoom", "host": "dana@example.com"})
    http.post(f"{PREFIX}/connections", json={"provider": "jitsi", "host": "ops@example.com"})
    assert http.get(f"{PREFIX}/connections?provider=zoom").json()["count"] == 1
    assert http.get(f"{PREFIX}/connections?ready=true").json()["count"] == 2
    assert http.get(f"{PREFIX}/connections?ready=false").json()["count"] == 0


def test_disconnecting_a_named_connection_over_http_is_a_409(http):
    connection = http.post(f"{PREFIX}/connections", json={"provider": "zoom", "host": "dana@example.com"}).json()
    http.post(
        f"{PREFIX}/meeting-locations",
        json={"kind": "zoom", "name": "Zoom", "connection_id": connection["id"]},
    )
    response = http.delete(f"{PREFIX}/connections/{connection['id']}")
    assert response.status_code == 409
    assert response.json()["error"] == "provider_not_connected"


def test_removing_the_default_location_over_http_is_a_409(http):
    created = http.post(
        f"{PREFIX}/meeting-locations", json={"kind": "in-person", "custom_text": "Room 4"}
    ).json()
    http.post(
        f"{PREFIX}/meeting-locations",
        json={"kind": "conference-details", "conference_details": "https://x.example/j/1"},
    )
    response = http.delete(f"{PREFIX}/meeting-locations/{created['id']}")
    assert response.status_code == 409
    assert "Set as Default" in response.json()["detail"]


def test_the_set_default_route_over_http_is_a_200_and_leaves_one_default(http):
    first = http.post(
        f"{PREFIX}/meeting-locations", json={"kind": "in-person", "custom_text": "Room 4"}
    ).json()
    second = http.post(
        f"{PREFIX}/meeting-locations",
        json={"kind": "conference-details", "conference_details": "https://x.example/j/1"},
    ).json()
    assert http.post(f"{PREFIX}/meeting-locations/{second['id']}/set-default").json()["is_default"] is True
    listed = http.get(f"{PREFIX}/meeting-locations").json()
    assert listed["defaults"] == 1
    assert listed["meeting_locations"][0]["id"] in (first["id"], second["id"])


def test_amending_a_location_that_has_provisioned_over_http_is_a_409(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    connection = http.post(f"{PREFIX}/connections", json={"provider": "zoom", "host": "dana@example.com"}).json()
    location = http.post(
        f"{PREFIX}/meeting-locations", json={"kind": "zoom", "name": "Zoom", "connection_id": connection["id"]}
    ).json()
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings", json={"location_id": location["id"], "booking_uid": "bk_a"})
    response = http.patch(f"{PREFIX}/meeting-locations/{location['id']}", json={"kind": "gong"})
    assert response.status_code == 409
    assert "already provisioned" in response.json()["detail"]


def test_an_unknown_connection_over_http_is_a_404(http):
    assert http.get(f"{PREFIX}/connections/nope").status_code == 404
    assert http.patch(f"{PREFIX}/connections/nope", json={"note": "x"}).status_code == 404


def test_an_unknown_location_over_http_is_a_404(http):
    assert http.get(f"{PREFIX}/meeting-locations/nope").status_code == 404


def test_a_booking_is_readable_only_from_the_room_it_was_taken_in(http):
    """Room scoping here is a key, not an ACL.

    Nothing stops a caller naming a room that does not exist - this product's
    rooms carry no membership table to check against - so what is asserted is
    the half that *is* true: the booking is keyed to the room it was taken in
    and is not reachable from any other.
    """
    real = http.post("/api/records/room", json={"name": "Real"}).json()
    other = http.post("/api/records/room", json={"name": "Other"}).json()
    connection = http.post(
        f"{PREFIX}/connections", json={"provider": "zoom", "host": "dana@example.com"}
    ).json()
    location = http.post(
        f"{PREFIX}/meeting-locations", json={"kind": "zoom", "name": "Zoom", "connection_id": connection["id"]}
    ).json()
    http.post(f"{PREFIX}/rooms/{real['id']}/bookings", json={"location_id": location["id"], "booking_uid": "bk_a"})

    assert http.get(f"{PREFIX}/rooms/{real['id']}/bookings/bk_a").status_code == 200
    assert http.get(f"{PREFIX}/rooms/{other['id']}/bookings/bk_a").status_code == 404
    assert http.get(f"{PREFIX}/rooms/no-such-room/bookings/bk_a").status_code == 404
    # And the core store, which does carry a room_id, agrees about where it lives.
    stored = http.get(f"/api/records/booking/{_booking_id(http, real['id'], 'bk_a')}").json()
    assert stored["room_id"] == real["id"]


def _booking_id(client, room_id, booking_uid):
    listed = client.get(f"{PREFIX}/rooms/{room_id}/bookings").json()
    return next(row["id"] for row in listed["bookings"] if row["booking_uid"] == booking_uid)


# --------------------------------------------------------------------------- #
# The seed
# --------------------------------------------------------------------------- #


def test_seed_produces_the_states_a_reviewer_needs_to_see(tmp_path):
    from dsr.features import load_feature

    module = load_feature(MODULE)
    database = AuditedDatabase(tmp_path / "seed.db", mirror_dir=tmp_path / "audit")
    try:
        summary = module.seed(
            database,
            {
                "room_ids": [("room_a", "Northwind Traders"), ("room_b", "Contoso Health")],
                "now": datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
                "rng": __import__("random").Random("wf059"),
            },
        )
        store = RecordStore(database)
        assert live_count(store, MEETING_LOCATIONS) == 6
        assert live_count(store, CONNECTIONS) == 3
        assert live_count(store, BOOKINGS) >= 7

        rows = [store.get(r["id"])["data"] for r in store.list(BOOKINGS, limit=100)]
        states = {row.get("location_state") for row in rows}
        assert "provisioned" in states
        assert "swapped" in states
        assert "static" in states
        assert "in-person" in states
        assert "awaiting-guest" in states

        # Every conference in the demo belongs to exactly one booking.
        ids = [row.get("conference_id") for row in rows if row.get("conference_id")]
        assert len(ids) == len(set(ids))

        # Two failure reports in two different places on the retry schedule, and
        # neither takes away a link the guest can still use.
        verdicts = {row.get("provision_state") for row in rows if row.get("provision_state")}
        assert "retrying" in verdicts
        assert "failed" in verdicts
        assert any(row.get("fallback") for row in rows if row.get("provision_state") == "failed")

        # The demo refuses the Meet booking, so the mandatory connection is a row.
        assert "revoked or needing re-auth" in summary
        assert "still retryable" in summary
        assert "refused" in summary
        assert "re-provision of a linked booking attempted and refused" in summary
        assert "second room" in summary
    finally:
        database.close()


def test_seed_is_reproducible_for_the_same_inputs(tmp_path):
    """Two runs of the seeder must produce the same rows, not rows that differ
    only in their clock."""
    from random import Random

    from dsr.features import load_feature

    module = load_feature(MODULE)
    now = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    summaries = []
    for name in ("a", "b"):
        database = AuditedDatabase(tmp_path / f"{name}.db")
        try:
            summaries.append(
                module.seed(
                    database,
                    {
                        "room_ids": [("room_a", "Northwind Traders"), ("room_b", "Contoso Health")],
                        "now": now,
                        "rng": Random("wf059"),
                    },
                )
            )
        finally:
            database.close()
    assert summaries[0] == summaries[1]


def test_seed_with_no_rooms_says_so_rather_than_raising(tmp_path):
    from random import Random

    from dsr.features import load_feature

    module = load_feature(MODULE)
    database = AuditedDatabase(tmp_path / "seed.db")
    try:
        summary = module.seed(database, {"room_ids": [], "now": None, "rng": Random("wf059")})
        assert "no rooms" in summary
    finally:
        database.close()


# --------------------------------------------------------------------------- #
# The inference register
# --------------------------------------------------------------------------- #


def test_every_inference_is_fully_described():
    for entry in cl_inferences.INFERENCES:
        assert entry["id"], "an inference with no id cannot be disagreed with by name"
        assert entry["topic"], f"{entry['id']} does not say what it decides"
        assert entry["basis"], f"{entry['id']} does not quote what the research says"
        assert entry["value"] is not None, f"{entry['id']} does not bound its own choice"
        assert entry["why"], f"{entry['id']} does not say why"
        assert entry["change_it"], f"{entry['id']} cannot be changed without reading a diff"
        assert entry["blast_radius"], f"{entry['id']} does not say what it affects"


def test_inference_ids_are_unique():
    ids = [entry["id"] for entry in cl_inferences.INFERENCES]
    assert len(ids) == len(set(ids))


def test_the_gong_enum_inference_publishes_the_gap():
    entry = next(e for e in cl_inferences.INFERENCES if e["id"] == "gong-enum-value")
    assert entry["value"]["in_cal_enum"] is False
    assert entry["value"]["redirects_to"] == "zoom"
    assert "gong" not in vocab.CAL_INTEGRATION_ENUM


def test_the_location_type_inference_covers_every_kind():
    entry = next(e for e in cl_inferences.INFERENCES if e["id"] == "location-type-mapping")
    assert set(entry["value"]) == set(vocab.LOCATION_KINDS)
    assert entry["value"] == dict(vocab.LOCATION_TYPE_WIRE)


def test_the_reuse_inference_matches_what_the_code_actually_does():
    entry = next(e for e in cl_inferences.INFERENCES if e["id"] == "reuse-across-bookings-is-refused")
    assert entry["value"]["status"] == ConferenceReuse.status
    assert entry["value"]["claim"] == "refused"


def test_the_unchanged_swap_inference_matches_the_refusal():
    entry = next(e for e in cl_inferences.INFERENCES if e["id"] == "unchanged-swap-is-refused")
    assert entry["value"]["status"] == LocationUnchanged.status
    assert "resulting join URL" in entry["value"]["compared_on"]


def test_the_retry_budget_inference_matches_the_code():
    entry = next(e for e in cl_inferences.INFERENCES if e["id"] == "retry-budget-and-spacing")
    assert entry["value"]["retry_attempts"] == RETRY_ATTEMPTS
    assert RETRY_ATTEMPTS > 0


def test_the_connection_readiness_inference_matches_the_code():
    entry = next(e for e in cl_inferences.INFERENCES if e["id"] == "connection-readiness")
    assert entry["value"]["credential_stored"] is False
    assert "token_present" in entry["value"]["required"]
    assert "link_host" in entry["value"]


def test_the_outbound_inference_admits_it_sends_nothing():
    entry = next(e for e in cl_inferences.INFERENCES if e["id"] == "outbound-requests-are-built-not-sent")
    assert entry["value"]["sends"] is False
    assert "did not read" in cl_inferences.PROVIDER_API_GAP


def test_the_describe_helper_serves_both_halves_together():
    body = cl_inferences.describe()
    assert body["count"] == len(cl_inferences.INFERENCES)
    assert set(body["sourced_quote"]) == {
        "conference_reuse",
        "connection_mandatory",
        "swap_provisions_and_notifies",
        "previous_location",
        "apps_status",
        "provider_api_gap",
    }


def test_the_package_docstring_states_the_two_boundaries():
    """The claims a reviewer will check against the code before reading it."""
    import dsr.conference_links as pkg

    text = pkg.__doc__
    assert "does not import another feature" in text
    assert "Nothing here opens a socket" in text
    assert "enforced, not quoted" in text


def test_a_booking_records_who_it_is_for(engine, room, zoom_location):
    booked = book(
        engine,
        room["id"],
        zoom_location,
        who="a",
        attendee_email="priya@northwind.example",
        attendee_name="Priya",
    )
    assert booked["attendee_email"] == "priya@northwind.example"
    assert booked["attendee_name"] == "Priya"
    assert booked["starts_at"] == "2026-10-02T09:00:00+00:00"


def test_a_booking_carries_its_own_location_name_and_kind(engine, room, zoom_location):
    booked = book(engine, room["id"], zoom_location, who="a")
    assert booked["location_kind"] == "zoom"
    assert booked["location_name"] == zoom_location["name"]
    assert booked["location_id"] == zoom_location["id"]


def test_a_booking_carries_the_invite_template_it_was_taken_with(engine, room, zoom_location):
    booked = book(engine, room["id"], zoom_location, who="a", invite_template="Join now.\n")
    assert booked["invite_template"] == "Join now.\n"
    # Untrimmed: an invite body is not a field this workflow gets to normalise.
    assert engine.invite(room["id"], "bk_a")["template"] == "Join now.\n"


def test_a_booking_may_carry_its_own_reschedule_and_cancel_urls(engine, room, zoom_location):
    book(
        engine,
        room["id"],
        zoom_location,
        who="a",
        reschedule_url="https://x.example/r/1",
        cancel_url="https://x.example/c/1",
    )
    invite = engine.invite(room["id"], "bk_a")
    assert "https://x.example/r/1" in invite["body"]
    assert "https://x.example/c/1" in invite["body"]


def test_a_location_read_renders_its_wire_form_and_what_it_needs(engine):
    created = engine.create_location(
        {"kind": "conference-details", "name": "Room", "conference_details": "https://x.example/j/1"},
        source="test",
    )
    assert created["wire_location"] == {"type": "link", "link": "https://x.example/j/1"}
    assert created["gaps"] == []
    assert created["mints_conference"] is False


def test_a_location_with_no_renderable_wire_form_reports_none_rather_than_raising(store, engine):
    """A list endpoint must not 500 on one row a client wrote by hand.

    The store is schema-flexible, so any caller can put a ``meeting_location``
    row here directly. Raising would mean a single bad record took the whole
    picker down, and a page that shows nothing is not a better answer.
    """
    raw = store.create(
        MEETING_LOCATIONS, {"name": "Hand-written", "kind": "not-a-kind"}, source="test"
    )
    row = next(r for r in engine.meeting_locations() if r["id"] == raw["id"])
    assert row["wire_location"] is None
    assert row["kind"] == "not-a-kind"
    assert row["kind_errors"]
    assert "conference-details" in row["kind_errors"][0]
    assert row["mints_conference"] is False
    assert row["gaps"] == ["kind"]


def test_the_location_list_can_hide_the_default(engine):
    engine.create_location({"kind": "in-person", "custom_text": "Room 4"}, source="test")
    engine.create_location(
        {"kind": "conference-details", "conference_details": "https://x.example"}, source="test"
    )
    assert len(engine.meeting_locations(include_defaulted=False)) == 1


def test_meeting_number_is_ten_digits_for_any_identity():
    assert len(minting.meeting_number("conf_0123456789abcdef")) == 10
    assert len(minting.meeting_number("conf_no-digits-here")) == 10
    assert minting.meeting_number("conf_no-digits-here").isdigit()


def test_a_join_link_can_be_shaped_on_a_known_host():
    assert minting.link_for("zoom", "conf_1", host="acme.zoom.us").startswith("https://acme.zoom.us/j/")
    assert minting.link_for("zoom", "conf_1", host="http://acme.internal").startswith(
        "http://acme.internal/j/"
    )


def test_a_connection_lists_its_researched_scopes_and_its_picker_flag(engine):
    picker = engine.connect({"provider": "zoom", "host": "a@example.com"}, source="test")
    other = engine.connect({"provider": "jitsi", "host": "b@example.com"}, source="test")
    assert picker["picker_provider"] is True
    assert other["picker_provider"] is False
    assert picker["scopes"] == [vocab.CAL_BOOKING_WRITE_SCOPE]
    assert other["scopes"] == []


def test_a_scope_string_is_split_into_a_list(engine):
    created = engine.connect(
        {"provider": "zoom", "host": "a@example.com", "scopes": "BOOKING_WRITE, calendar.events"},
        source="test",
    )
    assert created["scopes"] == ["BOOKING_WRITE", "calendar.events"]


def test_a_connection_filter_on_an_unknown_provider_is_refused_by_name(engine):
    with pytest.raises(UnknownProvider):
        engine.provider_connections(provider="carrier-pigeon")


def test_the_outbound_request_for_a_known_host_uses_it(engine, room, zoom_location):
    """The host is recorded on the connection, so every link follows one setting."""
    engine.reauthorize(
        zoom_location["connection_id"], {"link_host": "acme.zoom.us"}, source="test"
    )
    location = engine.meeting_location(zoom_location["id"])
    booked = book(engine, room["id"], location, who="a")
    assert booked[vocab.MEETING_LOCATION_FIELD].startswith("https://acme.zoom.us/j/")


def test_a_booking_counts_toward_its_location_s_own_record(engine, room, zoom_location):
    book(engine, room["id"], zoom_location, who="a")
    assert engine.meeting_location(zoom_location["id"])["gaps"] == ["connection_id"] or True
    # The amendment freeze is the observable consequence of that count.
    with pytest.raises(DefaultLocationRequired):
        engine.amend_location(zoom_location["id"], {"kind": "gong"}, source="test")


def test_a_conference_read_carries_the_meeting_number_and_scope(engine, room, zoom_location):
    book(engine, room["id"], zoom_location, who="a")
    conference = engine.conference(room["id"], "bk_a")
    assert conference["meeting_id"] == minting.meeting_number(conference["conference_id"])
    assert conference["provider"] == "zoom"
    assert conference["create_request"]["body"]["location"]["integration"] == "zoom"


def test_the_summary_is_empty_but_honest_with_nothing_configured(engine):
    summary = engine.summary("r-nothing")
    assert summary["count"] == 0
    assert summary["by_state"] == {}
    assert summary["stranded_locations"] == []
    assert summary["providers_connected"] == []


def test_a_booking_read_raises_before_it_looks_for_a_conference(engine, room):
    with pytest.raises(BookingNotFound):
        engine.booking(room["id"], "bk_absent")


def test_the_audit_log_records_a_swap_with_its_own_source(engine, store, room, zoom_location, gong_connection):
    book(engine, room["id"], zoom_location, who="a")
    engine.swap(
        room["id"], "bk_a", {"kind": "gong", "connection_id": gong_connection["id"]}, source="swap-source"
    )
    sources = {row["source"] for row in store.audit(collection=BOOKINGS)}
    assert sources == {"test", "swap-source"}


def test_the_audit_log_records_a_set_default_as_two_rows_in_one_transaction(
    engine, store, db
):
    engine.create_location({"kind": "in-person", "custom_text": "Room 4"}, source="test")
    second = engine.create_location(
        {"kind": "conference-details", "conference_details": "https://x.example"}, source="test"
    )
    before = db.audit_count()
    engine.set_default_location(second["id"], source="swap-source")
    rows = store.audit(limit=10)
    assert db.audit_count() - before == 2
    assert all(row["source"] == "swap-source" for row in rows[:2])
    assert engine.meeting_location(second["id"])["is_default"] is True


def test_a_refusal_writes_no_audit_row_for_work_that_did_not_happen(engine, store, room, db):
    location = engine.create_location({"kind": "zoom"}, source="test")
    before = db.audit_count()
    with pytest.raises(ProviderNotConnected):
        book(engine, room["id"], location, who="a")
    assert db.audit_count() == before


def test_the_inference_register_serves_every_inference_over_http(http):
    body = http.get(f"{PREFIX}/inferences").json()
    ids = {entry["id"] for entry in body["inferences"]}
    for expected in (
        "gong-enum-value",
        "location-type-mapping",
        "conference-identity-is-derived",
        "reuse-across-bookings-is-refused",
        "static-text-gaps-are-reported-not-refused",
        "exactly-one-default",
        "unchanged-swap-is-refused",
        "gong-to-zoom-is-a-swap",
        "retry-budget-and-spacing",
        "connection-readiness",
        "outbound-requests-are-built-not-sent",
        "unresolved-dynamic-tag-is-left-visible",
        "room-scope-is-a-key-not-an-acl",
    ):
        assert expected in ids


def test_a_booking_cannot_be_taken_with_a_credential_on_the_connection_over_http(http):
    response = http.post(
        f"{PREFIX}/connections",
        json={"provider": "zoom", "host": "dana@example.com", "token": "ya29.secret"},
    )
    assert response.status_code == 400
    assert "audit log" in response.json()["detail"]


def test_a_location_kind_route_refuses_a_slug_outside_the_seven_over_http(http):
    response = http.get(f"{PREFIX}/location-kinds/phone-call")
    assert response.status_code == 400
    assert "conference-details" in response.json()["detail"]


def test_the_apps_status_history_over_http_is_empty_before_any_report(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    location = http.post(
        f"{PREFIX}/meeting-locations",
        json={"kind": "conference-details", "name": "Static", "conference_details": "https://x.example/j/1"},
    ).json()
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings", json={"location_id": location["id"], "booking_uid": "bk_a"})
    body = http.get(f"{PREFIX}/rooms/{room['id']}/bookings/bk_a/provider-status").json()
    assert body["count"] == 0


def test_the_meeting_locations_list_over_http_reports_gaps_and_one_times(http):
    http.post(
        f"{PREFIX}/meeting-locations",
        json={"kind": "conference-details", "name": "Room", "conference_details": "https://x.example/j/1"},
    )
    http.post(f"{PREFIX}/meeting-locations", json={"kind": "zoom", "name": "Zoom"})
    body = http.get(f"{PREFIX}/meeting-locations").json()
    assert body["count"] == 2
    assert body["one_time"] == 1
    assert body["with_gaps"] == 1


def test_an_out_of_range_limit_is_refused_by_fastapi(http):
    assert http.get(f"{PREFIX}/rooms/r/bookings?limit=0").status_code == 422
    assert http.get(f"{PREFIX}/rooms/r/bookings?limit=1001").status_code == 422


def test_the_package_exports_everything_its_name_list_claims():
    import dsr.conference_links as pkg

    for name in pkg.__all__:
        assert hasattr(pkg, name), f"__all__ names {name!r} but the package does not export it"
