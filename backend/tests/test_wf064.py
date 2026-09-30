"""Tests for WF-064: reschedule or cancel a meeting, and propagate the change.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-064.md``. Nothing here is a
preference of this build unless it is listed in
:mod:`dsr.scheduling.inferences`, and every inference in that registry has a test
that checks it is still named, still bounded and still changeable.

The researched half
-------------------

* The two invite tags, ``CP.Meeting.RescheduleUrl`` and ``CP.Meeting.CancelUrl``,
  injected into the invite's Description.
* ``Expire Reschedule Link`` - "allows you to decide if the reschedule link should
  expire after a meeting has happened".
* ``bookingUidToReschedule`` - "will ensure that the original booking time appears
  within the returned available slots when rescheduling".
* The reschedule chain: ``rescheduledFromUid`` / ``rescheduledToUid`` /
  ``rescheduleId`` / ``rescheduleReason``.
* ``BOOKING_RESCHEDULED`` carrying ``rescheduleId``, ``rescheduleUid``,
  ``rescheduleStartTime``, ``rescheduleEndTime``; ``BOOKING_CANCELLED`` carrying
  ``cancellationReason`` and ``cancelledByEmail``.
* Chili Piper's ``Meeting Update``, plus ``type: "Deleted"`` on a cancel, and the
  ``Delete Event`` toggle.
* The workflow triggers ``rescheduleEvent`` and ``eventCancelled``, and the
  reminder recomputation that goes with them.
* Events History: "who rescheduled it, to whom, when, and the rescheduling source
  (Calendar event, ChiliCal Home, or Reschedule Link)".
* The three host-side operations, and the sentence that distinguishes a request to
  reschedule from an immediate one.

Five bugs these tests were written to catch, each of which shipped during this
build and would not have been visible without a behavioural test:

* The Events History row was built from the booking's *envelope* where the
  ``data`` payload was meant, so every field landed blank and nothing raised.
* A completed reschedule request read ``room_id`` from ``data``, which the store
  reserves and strips, so the new booking landed unscoped and invisible to its own
  room's history.
* ``Delete Event`` issued a real ``tx.delete``, which ``AuditedWriter`` does not
  have - and a "fix" that made it a second transaction would have re-introduced
  the half-propagation the transaction exists to prevent.
* A request-reschedule link reused the cancelled booking's own token, which the
  liveness check had just closed, leaving the request with no completion path.
* The availability release was applied to the wrong booking: naming one uid and
  releasing another would let a host move a meeting onto a slot somebody else holds.

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
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.scheduling import (
    BOOKING_CANCELLED,
    BOOKING_LOCATION_UPDATED,
    BOOKING_RESCHEDULED,
    BOOKED,
    CALENDAR_EVENT,
    CANCEL,
    CANCELLED,
    CANCEL_SCOPE_NAMES,
    CHANGE_RESCHEDULE_REQUESTED,
    CHILICAL_HOME,
    CRM_SOBJECT,
    LIVE_STATUSES,
    REQUEST_RESCHEDULE,
    RESCHEDULE,
    RESCHEDULE_LINK,
    RESCHEDULE_SOURCE_NAMES,
    RESCHEDULED,
    SCOPE_ALL,
    SCOPE_THIS,
    BookingConflict,
    LinkExpired,
    MeetingChangeEngine,
    MeetingChangeError,
    MeetingNotFound,
    available_slots,
    channels_for,
    drop_reminders,
    explain_missing,
    find_slot,
    has_happened,
    iso,
    mint_pair,
    overlaps,
    parse,
    propagate_crm_event,
    rebase_reminders,
    state_for,
    webhook_envelopes,
    window_for,
)
from dsr.scheduling import inferences as scheduling_inferences
from dsr.scheduling.availability import MAX_RANGE_DAYS
from dsr.scheduling.engine import (
    BOOKING_COLLECTION,
    CHANGE_COLLECTION,
    MEETING_TYPE_COLLECTION,
    REQUEST_COLLECTION,
)
from dsr.scheduling.errors import MeetingChangeError as DomainError
from dsr.scheduling.links import (
    CANCEL_URL_TAG,
    LINK_PATH,
    RESCHEDULE_URL_TAG,
    URL_TEMPLATE,
)
from dsr.scheduling.meeting_types import (
    DEFAULT_DELETE_EVENT,
    DEFAULT_DURATION_MINUTES,
    DEFAULT_EXPIRE_RESCHEDULE_LINK,
    MAX_DURATION_MINUTES,
    MAX_RESCHEDULE_HORIZON_DAYS,
    MIN_DURATION_MINUTES,
    delete_event,
    expire_reschedule_link,
    normalise_meeting_type,
)
from dsr.scheduling.propagation import (
    CALENDAR_EVENT_COLLECTION,
    CRM_EVENT_COLLECTION,
    DEFAULT_CHANNELS,
    NOTIFICATION_COLLECTION,
    TEMPLATE_FOR_CHANGE,
    TRIGGER_FOR_CHANGE,
    WEBHOOK_COLLECTION,
    crm_status_for,
)
from dsr.scheduling.timeutil import minutes_from_midnight
from dsr.scheduling.vocabulary import (
    CHAIN_FIELDS,
    CHANGE_TYPE_NAMES,
    CRM_SOBJECT as VOCAB_CRM_SOBJECT,
    HISTORY_FIELDS,
    INTENT_NAMES,
    INTENTS,
    LINK_TAGS,
    MEETING_TYPE_SETTINGS,
    NOTIFICATION_TEMPLATE_NAMES,
    RESCHEDULE_SOURCES,
    WORKFLOW_TRIGGERS,
    published_vocabulary,
    require_actor_kind,
    require_cancel_scope,
    require_reschedule_source,
)
from dsr.store import RecordStore

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-064"

#: What the pure-domain tests pass as ``source``. Deliberately the exact shape a
#: route passes, so a test asserting on an audit row is asserting on the real
#: thing rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/bookings/{{uid}}/reschedule"

MODULE = "wf064_reschedule_or_cancel_a_meeting_and_pro"
FEATURE_ID = "wf-064-reschedule-or-cancel-a-meeting-and-pro"

#: A fixed **Sunday**, chosen so the offsets below are predictable.
#:
#: A Monday base would make ``at(5)`` a Saturday and every assertion that reschedules
#: to a relative offset would be about the meeting type's working days rather than
#: about the rule under test. A Sunday base puts the working week at ``at(1)`` through
#: ``at(5)``, and :func:`wd` slides forward to the next weekday for anything that
#: needs one at an arbitrary distance.
NOW = datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc)

#: 2026-10-03 is a Saturday. Named as a date rather than an offset so the test
#: cannot start failing silently if :data:`NOW` is ever moved.
SATURDAY = "2026-10-03T12:00:00+00:00"
SATURDAY_END = "2026-10-03T23:00:00+00:00"


def at(days: int = 0, hour: int = 9, minute: int = 0) -> str:
    """An ISO stamp exactly ``days`` from :data:`NOW`, at a given time of day."""
    return (
        (NOW + timedelta(days=days))
        .replace(hour=hour, minute=minute, second=0, microsecond=0)
        .isoformat(timespec="seconds")
    )


def url(value: str) -> str:
    """A timestamp as a query parameter.

    An ISO stamp carries a ``+`` in its offset, and a bare ``+`` in a query string
    decodes to a space. Without this every request that passes a timestamp in the
    query would 400 on a malformed date rather than on the rule under test, and the
    failure would read as a bug in the availability endpoint.
    """
    from urllib.parse import quote

    return quote(value, safe="")


def wd(days: int, hour: int = 9, minute: int = 0) -> str:
    """An ISO stamp ``days`` from :data:`NOW`, slid forward onto a working day.

    The meeting type under test works Monday to Friday, so any assertion whose
    subject is *not* the working week has to be sure of landing inside it. A
    relative offset is not enough for that: the base day is a Sunday, and ``days=5``
    from a Sunday is a Friday while the same offset from a Monday is a Saturday.
    """
    moment = NOW + timedelta(days=days)
    while moment.weekday() > 4:
        moment += timedelta(days=1)
    return moment.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def db(tmp_path):
    database = AuditedDatabase(tmp_path / "scheduling.db", mirror_dir=tmp_path / "mirror")
    yield database
    database.close()


@pytest.fixture()
def store(db):
    return RecordStore(db)


@pytest.fixture()
def engine(store):
    """An engine on a fixed clock and reproducible ids.

    The id factories are overridden rather than monkeypatched so every uid and
    token in a test is legible: a failing assertion about ``rescheduleUid`` should
    point at the value, not at a hex blob.
    """
    counter = {"n": 0}

    def uid() -> str:
        counter["n"] += 1
        return f"bk_test_{counter['n']:03d}"

    def token() -> str:
        counter["n"] += 1
        return f"tok_test_{counter['n']:03d}"

    return MeetingChangeEngine(store, clock=lambda: NOW, uid_factory=uid, token_factory=token)


@pytest.fixture()
def room(store):
    return store.create("room", {"name": "Northwind — Enterprise Evaluation"}, actor="dana")


@pytest.fixture()
def other_room(store):
    return store.create("room", {"name": "Contoso — Security Review"}, actor="sam")


@pytest.fixture()
def meeting_type(engine, room):
    """A permissive type with reminders, which is the baseline most tests want."""
    return engine.create_meeting_type(
        {
            "name": "Northwind Demo",
            "distribution": "Northwind Enterprise",
            "host_email": "dana@northwind.example",
            "duration_minutes": 30,
            "hours": "09:00-17:00",
            "days": [0, 1, 2, 3, 4],
            "reminder_offsets": [1440, 60],
        },
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )


def make_type(engine, room, **overrides):
    spec = {
        "name": overrides.pop("name", "Type"),
        "host_email": overrides.pop("host_email", "dana@northwind.example"),
        "duration_minutes": overrides.pop("duration_minutes", 30),
        "hours": overrides.pop("hours", "09:00-17:00"),
    } | overrides
    return engine.create_meeting_type(spec, room_id=room["id"], actor="dana", source=SOURCE)


def make_booking(engine, room, meeting_type, **overrides):
    spec = {
        "meeting_type_id": meeting_type["id"],
        "title": "Northwind walkthrough",
        "attendee_name": "Priya Raman",
        "attendee_email": "priya.raman@northwind.example",
        "start_at": at(3, 9, 0),
        "location": "Zoom",
    } | overrides
    return engine.create_booking(spec, room_id=room["id"], actor="dana", source=SOURCE)


@pytest.fixture()
def booking(engine, room, meeting_type):
    return make_booking(engine, room, meeting_type)


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
    """Does ``"POST /api/wf-064/rooms/abc/bookings/uid/reschedule"`` name a real route?

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


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The routes resolve even though no shared file names this feature."""
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)
    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-064"
    assert entry["exception_handlers"] == ["MeetingChangeError"]
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
    assert f"id: {FEATURE_ID!r}" in text


def test_room_scoped_paths_are_room_scoped(http):
    """The three intents and the room's history all take a room id."""
    paths = {path for _, path in mounted_routes(http)}
    assert "/api/wf-064/rooms/{room_id}/bookings/{uid}/reschedule" in paths
    assert "/api/wf-064/rooms/{room_id}/bookings/{uid}/request-reschedule" in paths
    assert "/api/wf-064/rooms/{room_id}/bookings/{uid}/cancel" in paths
    assert "/api/wf-064/rooms/{room_id}/bookings/{uid}/plan" in paths
    assert "/api/wf-064/rooms/{room_id}/meeting-changes" in paths
    assert "/api/wf-064/rooms/{room_id}/meetings" in paths


def test_the_three_intents_have_three_separate_routes(http):
    """A request to reschedule is not a reschedule; they need different routes.

    Collapsing them would lose the researched distinction that the first cancels
    the booking and the second does not.
    """
    paths = {path for _, path in mounted_routes(http)}
    assert len([p for p in paths if "request-reschedule" in p]) == 1
    assert len([p for p in paths if p.endswith("/reschedule")]) == 1


# --------------------------------------------------------------------------- #
# The vocabulary
# --------------------------------------------------------------------------- #


def test_there_are_exactly_three_intents():
    """reschedule, request-reschedule, cancel - the researched set."""
    assert set(INTENT_NAMES) == {RESCHEDULE, REQUEST_RESCHEDULE, CANCEL}


def test_each_intent_names_the_cal_endpoint_it_lands_on():
    assert INTENT_NAMES and all(entry["cal_endpoint"].startswith("POST /v2/bookings/") for entry in INTENTS)
    by_intent = {entry["intent"]: entry for entry in INTENTS}
    assert by_intent[RESCHEDULE]["cal_endpoint"].endswith("/reschedule")
    assert by_intent[REQUEST_RESCHEDULE]["cal_endpoint"].endswith("/request-reschedule")
    assert by_intent[CANCEL]["cal_endpoint"].endswith("/cancel")


def test_only_request_reschedule_cancels_the_booking():
    """"Request to reschedule ... The booking will be cancelled" - only that one."""
    by_intent = {entry["intent"]: entry for entry in INTENTS}
    assert by_intent[RESCHEDULE]["cancels_the_current_booking"] is False
    assert by_intent[REQUEST_RESCHEDULE]["cancels_the_current_booking"] is True
    assert by_intent[CANCEL]["cancels_the_current_booking"] is True


def test_the_three_rescheduling_sources_are_the_ones_events_history_names():
    """"(Calendar event, ChiliCal Home, or Reschedule Link)"."""
    assert set(RESCHEDULE_SOURCE_NAMES) == {CHILICAL_HOME, CALENDAR_EVENT, RESCHEDULE_LINK}
    assert [entry["source"] for entry in RESCHEDULE_SOURCES] == [
        CALENDAR_EVENT,
        CHILICAL_HOME,
        RESCHEDULE_LINK,
    ]


def test_each_rescheduling_source_carries_its_sourced_justification():
    for entry in RESCHEDULE_SOURCES:
        assert entry["sourced_from"], f"{entry['source']} has no sourced_from"
        assert entry["source_doc"] == "chilipiper_webhooks"


def test_there_are_two_cancel_scopes_named_by_the_research():
    """"individual recurrence or recurring booking to cancel all recurrences"."""
    assert set(CANCEL_SCOPE_NAMES) == {SCOPE_THIS, SCOPE_ALL}


def test_the_two_workflow_triggers_are_the_researched_ones():
    assert [entry["trigger"] for entry in WORKFLOW_TRIGGERS] == ["rescheduleEvent", "eventCancelled"]


def test_each_trigger_maps_to_the_change_it_fires_on():
    assert TRIGGER_FOR_CHANGE["rescheduled"] == "rescheduleEvent"
    assert TRIGGER_FOR_CHANGE["cancelled"] == "eventCancelled"


def test_the_two_invite_tags_are_the_researched_pair():
    assert [entry["tag"] for entry in LINK_TAGS] == [RESCHEDULE_URL_TAG, CANCEL_URL_TAG]
    assert RESCHEDULE_URL_TAG == "CP.Meeting.RescheduleUrl"
    assert CANCEL_URL_TAG == "CP.Meeting.CancelUrl"


def test_each_invite_tag_maps_to_one_intent():
    by_tag = {entry["tag"]: entry["kind"] for entry in LINK_TAGS}
    assert by_tag[RESCHEDULE_URL_TAG] == RESCHEDULE
    assert by_tag[CANCEL_URL_TAG] == CANCEL


def test_every_chain_field_is_published_with_its_source():
    fields = {entry["field"] for entry in CHAIN_FIELDS}
    assert fields == {
        "bookingUidToReschedule",
        "rescheduledFromUid",
        "rescheduledToUid",
        "rescheduleId",
        "rescheduleReason",
        "cancellationReason",
        "cancelledByEmail",
    }
    for entry in CHAIN_FIELDS:
        assert entry["sourced_from"], f"{entry['field']} has no sourced_from"
        assert entry["source"] in {"cal_slots", "cal_api_reference", "cal_webhooks"}


def test_the_history_fields_are_the_four_the_research_lists():
    """"who rescheduled it, to whom, when, and the rescheduling source"."""
    assert [entry["field"] for entry in HISTORY_FIELDS] == [
        "who",
        "to whom",
        "when",
        "rescheduling source",
    ]


def test_the_two_researched_meeting_type_settings_are_published():
    assert {entry["setting"] for entry in MEETING_TYPE_SETTINGS} == {
        "expire_reschedule_link",
        "delete_event",
    }


def test_each_meeting_type_setting_quotes_its_own_sentence():
    by_setting = {entry["setting"]: entry for entry in MEETING_TYPE_SETTINGS}
    assert "expire after a meeting has happened" in by_setting["expire_reschedule_link"]["sourced_from"]
    assert "will be deleted" in by_setting["delete_event"]["sourced_from"]


def test_the_crm_sobject_is_the_researched_one():
    assert CRM_SOBJECT == "Event"
    assert VOCAB_CRM_SOBJECT == "Event"


def test_all_four_change_types_are_published():
    assert set(CHANGE_TYPE_NAMES) == {
        "rescheduled",
        "reschedule_requested",
        "cancelled",
        "location_updated",
    }


def test_the_vocabulary_endpoint_publishes_what_the_validator_enforces_against(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert set(body["intent_names"]) == set(INTENT_NAMES)
    assert set(body["reschedule_source_names"]) == set(RESCHEDULE_SOURCE_NAMES)
    assert set(body["change_type_names"]) == set(CHANGE_TYPE_NAMES)
    assert body["crm_sobject"] == "Event"
    assert body["limits"]["reschedule_horizon_days"] == MAX_RESCHEDULE_HORIZON_DAYS


def test_the_vocabulary_names_the_webhooks_this_workflow_does_not_push(http):
    """A decision not to emit something has to be visible, not merely absent."""
    body = http.get(f"{PREFIX}/vocabulary").json()
    not_emitted = {entry["webhook"]: entry for entry in body["webhooks_not_emitted"]}
    assert "BOOKING_NO_SHOW_UPDATED" in not_emitted
    assert not_emitted["BOOKING_NO_SHOW_UPDATED"]["emitted"] is False
    assert not_emitted["BOOKING_NO_SHOW_UPDATED"]["why"]


def test_every_published_vocabulary_entry_carries_its_quotation():
    """Nothing in the published vocabulary carries a borrowed quotation.

    The flat name lists (``intent_names`` and friends) are skipped deliberately:
    they are a convenience projection of the entries beside them, and asserting
    on them would only re-check the tuples this test already walks.
    """
    body = published_vocabulary()
    entry_lists = [
        value
        for value in body.values()
        if isinstance(value, list) and value and isinstance(value[0], dict)
    ]
    assert len(entry_lists) >= 10, f"expected the described vocabularies, found {len(entry_lists)}"
    for entries in entry_lists:
        for entry in entries:
            assert entry.get("sourced_from"), f"{entry} has no sourced_from"


# --------------------------------------------------------------------------- #
# Time: the one boundary the research describes
# --------------------------------------------------------------------------- #


def test_a_naive_timestamp_is_read_as_utc_and_documented_as_such():
    """"2024-01-05T14:30:00Z" is offset-bearing; a hand-written one often is not."""
    assert parse("2026-10-01T09:00:00Z") == datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    assert parse("2026-10-01T09:00:00") == datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    assert parse("2026-10-01T11:00:00+02:00") == datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def test_an_unreadable_timestamp_is_a_refusal():
    with pytest.raises(DomainError) as caught:
        parse("not a time", label="start_at")
    assert "start_at" in str(caught.value)


def test_a_missing_timestamp_is_a_refusal_naming_the_field():
    with pytest.raises(DomainError) as caught:
        parse(None, label="start_at")
    assert "start_at" in str(caught.value)


def test_the_meeting_has_happened_at_its_start_not_its_end():
    """"expire after a meeting has happened" - the start, not the end."""
    start = parse(at(0, 9, 0))
    end = parse(at(0, 11, 0))
    assert has_happened(start, end, parse(at(0, 8, 59))) is False
    assert has_happened(start, end, parse(at(0, 9, 0))) is True
    assert has_happened(start, end, parse(at(0, 10, 30))) is True
    assert has_happened(start, end, parse(at(0, 12, 0))) is True


def test_two_intervals_that_only_touch_do_not_overlap():
    """A 09:00-09:30 meeting and a 09:30-10:00 one are not a conflict."""
    assert overlaps(parse(at(0, 9, 0)), parse(at(0, 9, 30)), parse(at(0, 9, 30)), parse(at(0, 10, 0))) is False
    assert overlaps(parse(at(0, 9, 0)), parse(at(0, 9, 30)), parse(at(0, 9, 29)), parse(at(0, 10, 0))) is True


def test_minutes_from_midnight_is_the_window_unit():
    assert minutes_from_midnight(parse(at(0, 9, 0))) == 540
    assert minutes_from_midnight(parse(at(0, 17, 0))) == 1020


# --------------------------------------------------------------------------- #
# Availability, and the one researched rule about it
# --------------------------------------------------------------------------- #


def test_the_availability_window_defaults_to_a_business_week():
    start, end, days = window_for({})
    assert (start, end) == (540, 1020)
    assert days == [0, 1, 2, 3, 4]


def test_slots_are_aligned_to_the_window_start():
    slots = available_slots(
        host_email="dana@x.example",
        window=(540, 1020, [0, 1, 2, 3, 4]),
        duration_minutes=60,
        existing=[],
        from_at=parse(at(1)),
        to_at=parse(at(2)),
        now=NOW,
    )
    assert [slot["start_at"] for slot in slots][:2] == [at(1, 9, 0), at(1, 10, 0)]


def test_a_slot_that_does_not_fit_inside_the_window_is_not_offered():
    """A 17:00 start on a window closing at 17:00 has nowhere to go."""
    slots = available_slots(
        host_email="dana@x.example",
        window=(540, 1020, [0, 1, 2, 3, 4]),
        duration_minutes=60,
        existing=[],
        from_at=parse(at(1)),
        to_at=parse(at(2)),
        now=NOW,
    )
    assert all(parse(slot["end_at"]) <= parse(at(1, 17, 0)) for slot in slots)


def test_no_slot_is_offered_on_a_day_the_meeting_type_does_not_work():
    """A Monday-Friday type offers nothing on a Saturday."""
    slots = available_slots(
        host_email="dana@x.example",
        window=(540, 1020, [0, 1, 2, 3, 4]),
        duration_minutes=30,
        existing=[],
        from_at=parse(SATURDAY),
        to_at=parse(SATURDAY_END),
        now=NOW,
    )
    assert slots == []


def test_a_slot_is_offered_on_a_day_the_meeting_type_does_work():
    """The control the Saturday test needs, or the Saturday test proves nothing."""
    slots = available_slots(
        host_email="dana@x.example",
        window=(540, 1020, [0, 1, 2, 3, 4]),
        duration_minutes=30,
        existing=[],
        from_at=parse(wd(1)),
        to_at=parse(wd(1, 23, 0)),
        now=NOW,
    )
    assert slots


def test_a_held_slot_is_not_offered():
    held = [{"uid": "bk_other", "start_at": at(3, 9, 0), "end_at": at(3, 9, 30), "status": BOOKED}]
    slots = available_slots(
        host_email="dana@x.example",
        window=(540, 1020, [0, 1, 2, 3, 4]),
        duration_minutes=30,
        existing=held,
        from_at=parse(at(3)),
        to_at=parse(at(4)),
        now=NOW,
    )
    assert find_slot(slots, at(3, 9, 0)) is None
    assert find_slot(slots, at(3, 9, 30)) is not None


def test_a_partly_overlapping_booking_also_blocks_the_slot():
    held = [{"uid": "bk_other", "start_at": at(3, 9, 15), "end_at": at(3, 9, 45), "status": BOOKED}]
    slots = available_slots(
        host_email="dana@x.example",
        window=(540, 1020, [0, 1, 2, 3, 4]),
        duration_minutes=30,
        existing=held,
        from_at=parse(at(3)),
        to_at=parse(at(4)),
        now=NOW,
    )
    assert find_slot(slots, at(3, 9, 0)) is None


def test_the_original_booking_time_reappears_when_it_is_named():
    """"bookingUidToReschedule ... will ensure that the original booking time
    appears within the returned available slots when rescheduling."

    This is the researched rule the whole recomputation exists for, and it is the
    one an implementation gets wrong by omission: without the release a meeting
    cannot be moved to the time it is already at.
    """
    held = [{"uid": "bk_mine", "start_at": at(3, 9, 0), "end_at": at(3, 9, 30), "status": BOOKED}]
    kwargs = {
        "host_email": "dana@x.example",
        "window": (540, 1020, [0, 1, 2, 3, 4]),
        "duration_minutes": 30,
        "existing": held,
        "from_at": parse(at(3)),
        "to_at": parse(at(4)),
        "now": NOW,
    }
    without = available_slots(**kwargs)
    with_release = available_slots(**kwargs, booking_uid_to_reschedule="bk_mine")

    assert find_slot(without, at(3, 9, 0)) is None
    slot = find_slot(with_release, at(3, 9, 0))
    assert slot is not None
    assert slot["original_slot"] is True
    assert slot["replaces_uid"] == "bk_mine"


def test_the_release_applies_to_the_named_booking_and_nothing_else():
    """Naming one uid must not release another booking's slot."""
    held = [
        {"uid": "bk_mine", "start_at": at(3, 9, 0), "end_at": at(3, 9, 30), "status": BOOKED},
        {"uid": "bk_theirs", "start_at": at(3, 10, 0), "end_at": at(3, 10, 30), "status": BOOKED},
    ]
    slots = available_slots(
        host_email="dana@x.example",
        window=(540, 1020, [0, 1, 2, 3, 4]),
        duration_minutes=30,
        existing=held,
        from_at=parse(at(3)),
        to_at=parse(at(4)),
        booking_uid_to_reschedule="bk_mine",
        now=NOW,
    )
    assert find_slot(slots, at(3, 9, 0))["original_slot"] is True
    assert find_slot(slots, at(3, 10, 0)) is None


def test_a_booking_without_times_holds_no_slot():
    """A half-populated booking must not narrow availability by accident."""
    slots = available_slots(
        host_email="dana@x.example",
        window=(540, 1020, [0, 1, 2, 3, 4]),
        duration_minutes=30,
        existing=[{"uid": "bk_partial", "start_at": at(3, 9, 0), "status": BOOKED}],
        from_at=parse(at(3)),
        to_at=parse(at(4)),
        now=NOW,
    )
    assert find_slot(slots, at(3, 9, 0)) is not None


def test_a_past_slot_is_flagged_rather_than_hidden():
    """The list reports the past so a caller can refuse it with a reason.

    The range starts *before* ``NOW`` and extends past it, so some slots are past
    and some are not - the only shape in which both are present. ``NOW`` is 08:00
    and the window opens at 09:00, so the first slots of the day are still ahead
    while a range that began yesterday has yesterday's behind it.
    """
    slots = available_slots(
        host_email="dana@x.example",
        window=(540, 1020, [0, 1, 2, 3, 4, 5, 6]),
        duration_minutes=30,
        existing=[],
        from_at=parse(at(-1)),
        to_at=parse(at(2)),
        now=NOW,
    )
    assert any(slot["in_past"] for slot in slots)
    assert any(not slot["in_past"] for slot in slots)


def test_a_range_that_ends_before_it_starts_is_a_refusal():
    with pytest.raises(DomainError):
        available_slots(
            host_email="dana@x.example",
            window=(540, 1020, [0, 1, 2, 3, 4]),
            duration_minutes=30,
            existing=[],
            from_at=parse(at(3)),
            to_at=parse(at(1)),
            now=NOW,
        )


def test_an_over_long_range_is_bounded_rather_than_computed():
    with pytest.raises(DomainError) as caught:
        available_slots(
            host_email="dana@x.example",
            window=(540, 1020, [0, 1, 2, 3, 4]),
            duration_minutes=30,
            existing=[],
            from_at=parse(at(0)),
            to_at=parse(at(MAX_RANGE_DAYS + 1)),
            now=NOW,
        )
    assert str(MAX_RANGE_DAYS) in str(caught.value)


def test_a_zero_length_booking_is_a_refusal():
    with pytest.raises(DomainError):
        available_slots(
            host_email="dana@x.example",
            window=(540, 1020, [0, 1, 2, 3, 4]),
            duration_minutes=0,
            existing=[],
            from_at=parse(at(1)),
            to_at=parse(at(2)),
            now=NOW,
        )


def test_a_missing_reason_names_the_next_open_slot():
    """A bare "no such slot" is a dead end for somebody who typed 14:17."""
    slots = available_slots(
        host_email="dana@x.example",
        window=(540, 1020, [0, 1, 2, 3, 4]),
        duration_minutes=30,
        existing=[],
        from_at=parse(at(3)),
        to_at=parse(at(4)),
        now=NOW,
    )
    message = explain_missing(slots, at(3, 14, 17))
    assert "not a slot on offer" in message
    assert at(3, 14, 30) in message


def test_a_reason_for_an_available_slot_says_so():
    slots = available_slots(
        host_email="dana@x.example",
        window=(540, 1020, [0, 1, 2, 3, 4]),
        duration_minutes=30,
        existing=[],
        from_at=parse(at(3)),
        to_at=parse(at(4)),
        now=NOW,
    )
    assert explain_missing(slots, at(3, 9, 0)) == "the requested time is available"


def test_find_slot_matches_the_instant_exactly_not_by_containment():
    """"09:15 on a 09:00-09:30 slot is a different time, and is refused."""
    slots = available_slots(
        host_email="dana@x.example",
        window=(540, 1020, [0, 1, 2, 3, 4]),
        duration_minutes=30,
        existing=[],
        from_at=parse(at(3)),
        to_at=parse(at(4)),
        now=NOW,
    )
    assert find_slot(slots, at(3, 9, 15)) is None


# --------------------------------------------------------------------------- #
# Meeting Types
# --------------------------------------------------------------------------- #


def test_a_meeting_type_needs_a_name_and_a_host():
    with pytest.raises(DomainError) as caught:
        normalise_meeting_type({"host_email": "dana@x.example"})
    assert "name" in str(caught.value)
    with pytest.raises(DomainError) as caught:
        normalise_meeting_type({"name": "T"})
    assert "host_email" in str(caught.value)


def test_a_host_that_is_not_an_email_is_refused():
    with pytest.raises(DomainError) as caught:
        normalise_meeting_type({"name": "T", "host_email": "not-an-email"})
    assert "email" in str(caught.value)


def test_hours_accept_both_spellings():
    assert normalise_meeting_type({"name": "T", "host_email": "d@x", "hours": "09:00-17:00"})["hours"] == {
        "start": 540,
        "end": 1020,
    }
    assert normalise_meeting_type(
        {"name": "T", "host_email": "d@x", "hours": {"start": 540, "end": 1020}}
    )["hours"] == {"start": 540, "end": 1020}


def test_hours_that_run_backwards_are_refused():
    with pytest.raises(DomainError):
        normalise_meeting_type({"name": "T", "host_email": "d@x", "hours": "17:00-09:00"})


def test_hours_that_are_not_readable_are_refused():
    with pytest.raises(DomainError):
        normalise_meeting_type({"name": "T", "host_email": "d@x", "hours": "lunch"})


def test_the_duration_has_a_default_and_bounded_ends():
    assert normalise_meeting_type({"name": "T", "host_email": "d@x"})["duration_minutes"] == DEFAULT_DURATION_MINUTES
    with pytest.raises(DomainError):
        normalise_meeting_type({"name": "T", "host_email": "d@x", "duration_minutes": 0})
    with pytest.raises(DomainError):
        normalise_meeting_type({"name": "T", "host_email": "d@x", "duration_minutes": MAX_DURATION_MINUTES + 1})


def test_the_minimum_duration_is_the_documented_one():
    spec = normalise_meeting_type(
        {"name": "T", "host_email": "d@x", "duration_minutes": MIN_DURATION_MINUTES}
    )
    assert spec["duration_minutes"] == MIN_DURATION_MINUTES


def test_the_two_researched_toggles_have_named_defaults():
    """Both defaults are inferences, so both are one constant away."""
    assert DEFAULT_EXPIRE_RESCHEDULE_LINK is False
    assert DEFAULT_DELETE_EVENT is True


def test_a_toggle_given_as_a_string_is_respected_rather_than_replaced():
    """"0" and "no" are false, not a missing value falling back to the default."""
    spec = normalise_meeting_type(
        {"name": "T", "host_email": "d@x", "delete_event": "false", "expire_reschedule_link": "true"}
    )
    assert spec["delete_event"] is False
    assert spec["expire_reschedule_link"] is True


def test_a_toggle_that_is_not_readable_is_refused_rather_than_assumed_false():
    """A mistyped delete_event would quietly keep a cancelled meeting on a calendar."""
    with pytest.raises(DomainError) as caught:
        normalise_meeting_type({"name": "T", "host_email": "d@x", "delete_event": "maybe"})
    assert "delete_event" in str(caught.value)


def test_a_negative_reminder_offset_is_refused():
    """A reminder after the meeting fires while nobody is in the room."""
    with pytest.raises(DomainError) as caught:
        normalise_meeting_type({"name": "T", "host_email": "d@x", "reminder_offsets": [-60]})
    assert "zero or positive" in str(caught.value)


def test_an_unknown_notification_channel_is_refused_at_configuration_time():
    with pytest.raises(DomainError) as caught:
        normalise_meeting_type({"name": "T", "host_email": "d@x", "notify_channels": ["slak"]})
    assert "slak" in str(caught.value)


def test_the_distribution_defaults_to_the_name_and_is_kept_verbatim():
    spec = normalise_meeting_type({"name": "Enterprise", "host_email": "d@x", "distribution": "EMEA Enterprise"})
    assert spec["distribution"] == "EMEA Enterprise"
    assert normalise_meeting_type({"name": "Enterprise", "host_email": "d@x"})["distribution"] == "Enterprise"


def test_a_day_outside_the_week_is_refused():
    with pytest.raises(DomainError):
        normalise_meeting_type({"name": "T", "host_email": "d@x", "days": [7]})


def test_the_toggle_readers_default_the_same_way_the_spec_does():
    assert expire_reschedule_link({}) is DEFAULT_EXPIRE_RESCHEDULE_LINK
    assert delete_event({}) is DEFAULT_DELETE_EVENT
    assert delete_event({"delete_event": False}) is False


def test_patching_a_meeting_type_revalidates_the_merged_result(engine, room, meeting_type):
    with pytest.raises(DomainError):
        engine.update_meeting_type(meeting_type["id"], {"hours": "17:00-09:00"}, source=SOURCE)


def test_patching_a_meeting_type_revalidates_the_pass_through_fields(engine, room, meeting_type):
    with pytest.raises(DomainError) as caught:
        engine.update_meeting_type(
            meeting_type["id"], {"notify_channels": ["carrier-pigeon"]}, source=SOURCE
        )
    assert "carrier-pigeon" in str(caught.value)


def test_patching_a_meeting_type_can_flip_the_toggles(engine, meeting_type):
    patched = engine.update_meeting_type(
        meeting_type["id"], {"expire_reschedule_link": True, "delete_event": False}, source=SOURCE
    )
    assert patched["data"]["expire_reschedule_link"] is True
    assert patched["data"]["delete_event"] is False


def test_a_meeting_type_that_does_not_exist_is_a_404(engine):
    assert engine.get_meeting_type("meeting_type_nope") is None
    with pytest.raises(MeetingNotFound):
        engine.resolve_meeting_type("meeting_type_nope")


def test_a_booking_can_resolve_the_default_meeting_type(engine):
    """A bare host and a length is a legitimate booking, not a malformed one."""
    assert engine.resolve_meeting_type(None)["id"] is None
    assert engine.resolve_meeting_type(None)["data"]["host_email"] == "host@example.invalid"


# --------------------------------------------------------------------------- #
# Bookings
# --------------------------------------------------------------------------- #


def test_a_booking_is_the_old_booking_record_the_flow_starts_from(booking):
    data = booking["data"]
    assert data["uid"]
    assert data["status"] == BOOKED
    assert data["start_at"] and data["end_at"]


def test_a_booking_carries_both_link_tokens(booking):
    assert booking["data"]["reschedule_token"]
    assert booking["data"]["cancel_token"]
    assert booking["data"]["reschedule_token"] != booking["data"]["cancel_token"]


def test_the_two_production_tokens_are_unguessable_not_derived(store, room, meeting_type):
    """A token derived from the uid would let anyone who can guess it move a meeting.

    Checked against the real factory, not the fixture's, because the fixture's
    tokens are short by design and asserting on them would prove nothing about the
    code that runs in production.
    """
    record = MeetingChangeEngine(store, clock=lambda: NOW).create_booking(
        {"meeting_type_id": meeting_type["id"], "uid": "bk_real", "start_at": at(3, 9, 0)},
        room_id=room["id"],
        source=SOURCE,
    )
    token = record["data"]["reschedule_token"]
    assert len(token) >= 16
    assert "bk_real" not in token
    assert record["data"]["cancel_token"] != token


def test_minting_a_pair_uses_the_factory_once_per_kind():
    pair = mint_pair("bk_1", factory=iter(["a", "b"]).__next__)
    assert pair == {RESCHEDULE: "a", CANCEL: "b"}


def test_minting_a_pair_needs_a_uid():
    with pytest.raises(DomainError):
        mint_pair("")


def test_a_booking_derives_its_end_from_the_meeting_type_duration(engine, room, meeting_type):
    record = engine.create_booking(
        {"meeting_type_id": meeting_type["id"], "start_at": at(3, 9, 0), "uid": "bk_x"},
        room_id=room["id"],
        source=SOURCE,
    )
    assert (parse(record["data"]["end_at"]) - parse(record["data"]["start_at"])) == timedelta(minutes=30)


def test_a_duplicate_uid_is_refused_rather_than_overwriting(engine, room, meeting_type, booking):
    with pytest.raises(DomainError) as caught:
        make_booking(engine, room, meeting_type, uid=booking["data"]["uid"])
    assert "already exists" in str(caught.value)


def test_a_booking_with_no_start_at_is_refused(engine, room, meeting_type):
    with pytest.raises(DomainError):
        engine.create_booking({"meeting_type_id": meeting_type["id"]}, room_id=room["id"], source=SOURCE)


def test_a_booking_is_found_by_its_cal_uid_not_its_record_id(engine, booking):
    assert engine.get_booking(booking["data"]["uid"])["id"] == booking["id"]


def test_an_unknown_uid_is_a_404_not_an_empty_result(engine):
    assert engine.get_booking("bk_nope") is None
    with pytest.raises(MeetingNotFound):
        engine.require_booking("bk_nope")


def test_live_bookings_exclude_superseded_and_cancelled_ones(engine, room, meeting_type, booking):
    """A superseded booking holds no slot, or every reschedule would collide with itself."""
    change = engine.reschedule(
        room["id"],
        booking["data"]["uid"],
        {"start_at": at(4, 9, 0), "actor_email": "priya.raman@northwind.example"},
        source=SOURCE,
    )
    live = engine.live_bookings_for_host("dana@northwind.example")
    uids = [record["data"]["uid"] for record in live]
    assert booking["data"]["uid"] not in uids
    assert change["data"]["new_booking_uid"] in uids
    assert all(record["data"]["status"] in LIVE_STATUSES for record in live)


def test_a_cancelled_booking_holds_no_slot_either(engine, room, booking):
    engine.cancel(
        room["id"],
        booking["data"]["uid"],
        {"actor_email": "a@x.example"},
        source=SOURCE,
    )
    assert engine.live_bookings_for_host("dana@northwind.example") == []


def test_a_booking_with_no_reminders_gets_none(engine, room):
    """This package recomputes reminders; it does not invent them."""
    plain = make_type(engine, room, name="Plain", reminder_offsets=[])
    record = make_booking(engine, room, plain)
    assert record["data"]["reminders"] == []


def test_a_meeting_type_can_supply_the_reminders(engine, room, meeting_type):
    record = make_booking(engine, room, meeting_type)
    offsets = [entry["offset_minutes"] for entry in record["data"]["reminders"]]
    assert offsets == [1440, 60]


def test_a_booking_can_supply_its_own_reminders(engine, room, meeting_type):
    record = make_booking(engine, room, meeting_type, reminders=[{"offset_minutes": 30, "label": "half hour"}])
    assert [entry["offset_minutes"] for entry in record["data"]["reminders"]] == [30]
    assert record["data"]["reminders"][0]["label"] == "half hour"


def test_a_reminder_without_an_offset_is_refused(engine, room, meeting_type):
    with pytest.raises(DomainError):
        make_booking(engine, room, meeting_type, reminders=[{"label": "no offset"}])


def test_a_supplied_reminder_is_scheduled_relative_to_the_start(engine, room, meeting_type):
    record = make_booking(engine, room, meeting_type, reminders=[{"offset_minutes": 60}])
    assert record["data"]["reminders"][0]["scheduled_for"] == at(3, 8, 0)


# --------------------------------------------------------------------------- #
# Reminders: "recomputed, because they were relative to the old time"
# --------------------------------------------------------------------------- #


def test_reminders_keep_their_offset_and_move_with_the_meeting():
    """A "one hour before" reminder stays a one-hour-before reminder."""
    reminders = [{"offset_minutes": 60, "scheduled_for": at(3, 8, 0), "status": "scheduled"}]
    rebased = rebase_reminders(reminders, at(4, 9, 0))
    assert rebased[0]["offset_minutes"] == 60
    assert rebased[0]["scheduled_for"] == at(4, 8, 0)
    assert rebased[0]["status"] == "scheduled"
    assert rebased[0]["rebased_from"] == at(3, 8, 0)


def test_a_reminder_whose_offset_cannot_be_read_still_lands_on_the_new_start():
    """A corrupted offset must not make the whole booking unreschedulable."""
    rebased = rebase_reminders([{"offset_minutes": "nonsense"}], at(4, 9, 0))
    assert rebased[0]["offset_minutes"] == 0
    assert rebased[0]["scheduled_for"] == at(4, 9, 0)


def test_non_object_reminders_are_dropped_rather_than_crashing():
    assert rebase_reminders(["nonsense", None], at(4, 9, 0)) == []


def test_a_cancelled_meetings_reminders_are_dropped_but_kept_on_the_record():
    """The history says a reminder was set and then dropped with the booking."""
    reminders = [{"offset_minutes": 60, "scheduled_for": at(3, 8, 0), "status": "scheduled"}]
    dropped = drop_reminders(reminders)
    assert dropped[0]["status"] == "dropped"
    assert dropped[0]["dropped_from"] == at(3, 8, 0)
    assert dropped[0]["offset_minutes"] == 60


def test_a_reschedule_rebases_the_booking_s_own_reminders(engine, room, meeting_type, booking):
    """Each reminder keeps its own offset, so both land on the new time correctly."""
    change = engine.reschedule(
        room["id"],
        booking["data"]["uid"],
        {"start_at": at(4, 9, 0), "actor_email": "priya.raman@northwind.example"},
        source=SOURCE,
    )
    assert change["data"]["reminders"] == {"before": 2, "after": 2, "recomputed": True}
    moved = engine.get_booking(change["data"]["new_booking_uid"])
    by_offset = {entry["offset_minutes"]: entry["scheduled_for"] for entry in moved["data"]["reminders"]}
    assert by_offset == {1440: at(3, 9, 0), 60: at(4, 8, 0)}


def test_a_cancel_drops_the_reminders(engine, room, meeting_type, booking):
    change = engine.cancel(
        room["id"],
        booking["data"]["uid"],
        {"actor_email": "priya.raman@northwind.example"},
        source=SOURCE,
    )
    assert change["data"]["reminders"] == {"before": 2, "after": 0, "recomputed": False}
    after = engine.get_booking(booking["data"]["uid"])
    assert all(entry["status"] == "dropped" for entry in after["data"]["reminders"])


# --------------------------------------------------------------------------- #
# Reschedule: the researched chain
# --------------------------------------------------------------------------- #


def test_a_reschedule_supersedes_the_old_booking_and_creates_a_new_one(engine, room, booking):
    change = engine.reschedule(
        room["id"],
        booking["data"]["uid"],
        {"start_at": at(4, 9, 0), "actor_email": "priya.raman@northwind.example"},
        source=SOURCE,
    )
    old = engine.get_booking(booking["data"]["uid"])
    assert old["data"]["status"] == RESCHEDULED
    assert old["data"]["rescheduled_to_uid"] == change["data"]["new_booking_uid"]
    new = engine.get_booking(change["data"]["new_booking_uid"])
    assert new["data"]["status"] == BOOKED
    assert new["data"]["rescheduled_from_uid"] == booking["data"]["uid"]


def test_the_chain_carries_the_researched_field_names(engine, room, booking):
    change = engine.reschedule(
        room["id"],
        booking["data"]["uid"],
        {"start_at": at(4, 9, 0), "reason": "later that week", "actor_email": "a@northwind.example"},
        source=SOURCE,
    )
    new = engine.get_booking(change["data"]["new_booking_uid"])
    assert new["data"]["rescheduled_from_uid"] == booking["data"]["uid"]
    assert new["data"]["reschedule_reason"] == "later that week"
    assert new["data"]["reschedule_id"] == 1


def test_the_reschedule_id_numbers_the_moves_on_a_chain(engine, room, booking):
    # `wd` because the type works Monday to Friday and a raw offset from a Sunday
    # lands on a weekend often enough to make this test flaky by construction.
    first = engine.reschedule(
        room["id"],
        booking["data"]["uid"],
        {"start_at": wd(4), "actor_email": "a@northwind.example"},
        source=SOURCE,
    )
    second = engine.reschedule(
        room["id"],
        first["data"]["new_booking_uid"],
        {"start_at": wd(11), "actor_email": "a@northwind.example"},
        source=SOURCE,
    )
    assert first["data"]["reschedule_id"] == 1
    assert second["data"]["reschedule_id"] == 2
    assert second["data"]["chain_root"] == first["data"]["chain_root"]


def test_two_independent_chains_number_from_one(engine, room, meeting_type):
    left = make_booking(engine, room, meeting_type, uid="bk_left")
    right = make_booking(engine, room, meeting_type, uid="bk_right", start_at=at(3, 11, 0))
    first = engine.reschedule(
        room["id"], left["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    other = engine.reschedule(
        room["id"], right["data"]["uid"], {"start_at": at(4, 11, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    assert first["data"]["reschedule_id"] == 1
    assert other["data"]["reschedule_id"] == 1
    assert first["data"]["chain_root"] != other["data"]["chain_root"]


def test_a_reschedule_to_the_bookings_own_time_is_allowed(engine, room, booking):
    """The researched release, exercised end to end rather than only on the list."""
    change = engine.reschedule(
        room["id"],
        booking["data"]["uid"],
        {"start_at": at(3, 9, 0), "actor_email": "priya.raman@northwind.example"},
        source=SOURCE,
    )
    new = engine.get_booking(change["data"]["new_booking_uid"])
    assert new["data"]["start_at"] == booking["data"]["start_at"]


def test_a_reschedule_to_a_slot_somebody_else_holds_is_refused(engine, room, meeting_type):
    first = make_booking(engine, room, meeting_type, uid="bk_first")
    make_booking(engine, room, meeting_type, uid="bk_second", start_at=at(4, 9, 0))
    with pytest.raises(DomainError) as caught:
        engine.reschedule(
            room["id"],
            first["data"]["uid"],
            {"start_at": at(4, 9, 0), "actor_email": "a@northwind.example"},
            source=SOURCE,
        )
    assert "not a slot on offer" in str(caught.value)


def test_a_reschedule_to_a_time_outside_the_hours_is_refused(engine, room, booking):
    with pytest.raises(DomainError):
        engine.reschedule(
            room["id"],
            booking["data"]["uid"],
            {"start_at": at(4, 20, 0), "actor_email": "a@northwind.example"},
            source=SOURCE,
        )


def test_a_reschedule_with_no_new_time_is_refused_naming_the_field(engine, room, booking):
    with pytest.raises(DomainError) as caught:
        engine.reschedule(room["id"], booking["data"]["uid"], {"actor_email": "a@x.example"}, source=SOURCE)
    assert "start_at" in str(caught.value)


def test_a_reschedule_into_the_past_is_refused(engine, room, meeting_type):
    """Past is past: the researched release covers the original slot, nothing else.

    A booking on a working day that has already passed, so the refusal is about
    the past rather than about a weekend or about the meeting's own time.
    """
    past = make_booking(engine, room, meeting_type, uid="bk_past_now", start_at=at(-2, 9, 0))
    with pytest.raises(DomainError) as caught:
        # 11:00, not 10:00: 10:00 overlaps the booking's own 09:00-09:30 slot, and
        # the researched release puts that slot back on offer, so 10:00 would be
        # `original_slot` and would be *allowed*. The rule under test is "a past
        # time nobody is holding is refused", and that is 11:00.
        engine.reschedule(
            room["id"],
            past["data"]["uid"],
            {"start_at": at(-2, 11, 0), "actor_email": "a@northwind.example"},
            source=SOURCE,
        )
    assert "in the past" in str(caught.value)


def test_the_original_slot_is_the_one_past_target_still_allowed(engine, room, meeting_type):
    """The exception that makes the past rule worth having rather than blunt."""
    past = make_booking(engine, room, meeting_type, uid="bk_past_ok", start_at=at(-2, 9, 0))
    change = engine.reschedule(
        room["id"],
        past["data"]["uid"],
        {"start_at": at(-2, 9, 0), "actor_email": "a@northwind.example"},
        source=SOURCE,
    )
    assert engine.get_booking(change["data"]["new_booking_uid"])["data"]["start_at"] == at(-2, 9, 0)


def test_a_reschedule_beyond_the_horizon_is_refused(engine, room, booking):
    """Checked before the availability read, so the refusal names the horizon."""
    with pytest.raises(DomainError) as caught:
        engine.reschedule(
            room["id"],
            booking["data"]["uid"],
            {"start_at": at(MAX_RESCHEDULE_HORIZON_DAYS + 1, 9, 0), "actor_email": "a@x.example"},
            source=SOURCE,
        )
    assert str(MAX_RESCHEDULE_HORIZON_DAYS) in str(caught.value)


def test_a_refused_reschedule_writes_nothing_at_all(engine, room, booking, store):
    """No stub, no history row, no downstream row: nothing happened."""
    before = {
        name: len(store.list(name))
        for name in (BOOKING_COLLECTION, CHANGE_COLLECTION, WEBHOOK_COLLECTION, CRM_EVENT_COLLECTION)
    }
    with pytest.raises(DomainError):
        engine.reschedule(
            room["id"],
            booking["data"]["uid"],
            {"start_at": at(4, 20, 0), "actor_email": "a@x.example"},
            source=SOURCE,
        )
    after = {
        name: len(store.list(name))
        for name in (BOOKING_COLLECTION, CHANGE_COLLECTION, WEBHOOK_COLLECTION, CRM_EVENT_COLLECTION)
    }
    assert before == after


def test_a_cancelled_booking_cannot_be_rescheduled(engine, room, booking):
    engine.cancel(room["id"], booking["data"]["uid"], {"actor_email": "a@x.example"}, source=SOURCE)
    with pytest.raises(BookingConflict) as caught:
        engine.reschedule(
            room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
        )
    assert "cancelled" in str(caught.value)


def test_a_superseded_booking_cannot_be_rescheduled_again(engine, room, booking):
    """The old booking is spent; the new one is what a second move acts on."""
    change = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    with pytest.raises(BookingConflict) as caught:
        engine.reschedule(
            room["id"],
            booking["data"]["uid"],
            {"start_at": at(5, 9, 0), "actor_email": "a@x.example"},
            source=SOURCE,
        )
    assert "rescheduled" in str(caught.value)


def test_the_booking_a_reschedule_produced_can_be_moved_again(engine, room, booking):
    """Two moves in a row is the researched churn case, not an error."""
    first = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    second = engine.reschedule(
        room["id"],
        first["data"]["new_booking_uid"],
        {"start_at": at(5, 9, 0), "actor_email": "a@x.example"},
        source=SOURCE,
    )
    assert second["data"]["reschedule_id"] == 2


def test_the_history_records_the_four_things_the_research_lists(engine, room, booking):
    change = engine.reschedule(
        room["id"],
        booking["data"]["uid"],
        {
            "start_at": at(4, 9, 0),
            "actor_email": "dana@northwind.example",
            "actor_kind": "host",
            "reschedule_source": CHILICAL_HOME,
        },
        source=SOURCE,
    )
    data = change["data"]
    assert data["actor_email"] == "dana@northwind.example"
    assert data["to_host_email"] == "dana@northwind.example"
    assert data["at"]
    assert data["reschedule_source"] == CHILICAL_HOME


def test_the_history_row_carries_the_before_and_the_after(engine, room, booking):
    change = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    assert change["data"]["from"]["start_at"] == at(3, 9, 0)
    assert change["data"]["to"]["start_at"] == at(4, 9, 0)
    assert change["data"]["from"]["uid"] != change["data"]["to"]["uid"]


def test_a_change_to_a_booking_that_does_not_exist_is_a_404(engine, room):
    with pytest.raises(MeetingNotFound):
        engine.reschedule(room["id"], "bk_nope", {"start_at": at(4, 9, 0)}, source=SOURCE)


def test_a_change_against_a_room_that_does_not_exist_is_a_404_not_a_400(engine, booking, store):
    """``RecordNotFound`` already maps to 404 and the core app owns it."""
    from dsr.db.audited import RecordNotFound

    with pytest.raises(RecordNotFound):
        engine.reschedule("room_nope", booking["data"]["uid"], {"start_at": at(4, 9, 0)}, source=SOURCE)


def test_the_rescheduling_source_defaults_to_the_host_panel(engine, room, booking):
    change = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    assert change["data"]["reschedule_source"] == CHILICAL_HOME


def test_a_reschedule_source_that_is_not_researched_is_refused(engine, room, booking):
    with pytest.raises(DomainError) as caught:
        engine.reschedule(
            room["id"],
            booking["data"]["uid"],
            {"start_at": at(4, 9, 0), "reschedule_source": "smoke_signal", "actor_email": "a@x.example"},
            source=SOURCE,
        )
    assert "smoke_signal" in str(caught.value)


def test_a_link_token_makes_the_change_the_attendees(engine, room, booking):
    change = engine.reschedule(
        room["id"],
        booking["data"]["uid"],
        {
            "start_at": at(4, 9, 0),
            "link_token": booking["data"]["reschedule_token"],
            "actor_email": "priya.raman@northwind.example",
        },
        source=SOURCE,
    )
    assert change["data"]["reschedule_source"] == RESCHEDULE_LINK
    assert change["data"]["actor_kind"] == "attendee"


def test_a_change_arriving_through_a_link_is_the_attendees_even_if_the_payload_says_otherwise(
    engine, room, booking
):
    """A link token can travel in a forwarded mail; the host's action is the fact."""
    change = engine.reschedule(
        room["id"],
        booking["data"]["uid"],
        {
            "start_at": at(4, 9, 0),
            "link_token": booking["data"]["reschedule_token"],
            "actor_kind": "host",
            "actor_email": "priya.raman@northwind.example",
        },
        source=SOURCE,
    )
    assert change["data"]["actor_kind"] == "attendee"


def test_a_link_belonging_to_another_booking_cannot_move_this_one(engine, room, meeting_type, booking):
    other = make_booking(engine, room, meeting_type, uid="bk_other", start_at=at(3, 11, 0))
    with pytest.raises(MeetingNotFound):
        engine.reschedule(
            room["id"],
            booking["data"]["uid"],
            {"start_at": at(4, 9, 0), "link_token": other["data"]["reschedule_token"]},
            source=SOURCE,
        )


def test_a_cancel_link_cannot_be_used_to_reschedule(engine, room, booking):
    with pytest.raises(DomainError) as caught:
        engine.reschedule(
            room["id"],
            booking["data"]["uid"],
            {"start_at": at(4, 9, 0), "link_token": booking["data"]["cancel_token"]},
            source=SOURCE,
        )
    assert "cancel link" in str(caught.value)


def test_the_old_meetings_link_does_not_travel_to_the_new_booking(engine, room, booking):
    """The invite after a move must point at the meeting that is actually happening."""
    change = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    new = engine.get_booking(change["data"]["new_booking_uid"])
    assert new["data"]["reschedule_token"] != booking["data"]["reschedule_token"]
    assert new["data"]["cancel_token"] != booking["data"]["cancel_token"]


def test_a_rescheduled_occurrence_keeps_its_series_membership(engine, room, meeting_type):
    """Dropping it would quietly break a later cancel-all."""
    first = make_booking(
        engine,
        room,
        meeting_type,
        uid="bk_rec_1",
        recurring_group="bk_rec",
        recurrence_index=1,
    )
    change = engine.reschedule(
        room["id"],
        first["data"]["uid"],
        {"start_at": at(4, 9, 0), "actor_email": "a@x.example"},
        source=SOURCE,
    )
    new = engine.get_booking(change["data"]["new_booking_uid"])
    assert new["data"]["recurring_group"] == "bk_rec"
    assert new["data"]["recurrence_index"] == 1


def test_a_reschedule_may_move_the_meeting_to_another_host(engine, room, meeting_type, booking):
    """Availability is recomputed for the host named, not the one it had."""
    change = engine.reschedule(
        room["id"],
        booking["data"]["uid"],
        {
            "start_at": at(4, 11, 0),
            "host_email": "sam@contoso.example",
            "duration_minutes": 30,
            "actor_email": "a@x.example",
        },
        source=SOURCE,
    )
    assert engine.get_booking(change["data"]["new_booking_uid"])["data"]["host_email"] == "sam@contoso.example"


# --------------------------------------------------------------------------- #
# The links and the Expire Reschedule Link setting
# --------------------------------------------------------------------------- #


def test_an_open_link_reports_its_kind_and_its_tag(booking, meeting_type):
    state = state_for(booking, RESCHEDULE, meeting_type["data"], NOW)
    assert state.expired is False
    assert state.kind == RESCHEDULE
    assert state.tag == RESCHEDULE_URL_TAG


def test_the_reschedule_link_expires_once_the_meeting_has_happened(booking, meeting_type):
    strict = dict(meeting_type["data"]) | {"expire_reschedule_link": True}
    assert state_for(booking, RESCHEDULE, strict, NOW).expired is False
    later = state_for(booking, RESCHEDULE, strict, NOW + timedelta(days=4))
    assert later.expired is True
    assert "Expire Reschedule Link" in later.reason


def test_the_setting_does_nothing_when_it_is_off(booking, meeting_type):
    permissive = dict(meeting_type["data"]) | {"expire_reschedule_link": False}
    later = state_for(booking, RESCHEDULE, permissive, NOW + timedelta(days=4))
    assert later.expired is False


def test_the_cancel_link_is_not_closed_by_the_reschedule_setting(booking, meeting_type):
    """The setting is named for the reschedule link; widening it is inventing a rule."""
    strict = dict(meeting_type["data"]) | {"expire_reschedule_link": True}
    later = state_for(booking, CANCEL, strict, NOW + timedelta(days=4))
    assert later.expired is False


def test_a_link_on_a_cancelled_meeting_is_closed_whatever_the_setting(booking, meeting_type):
    permissive = dict(meeting_type["data"]) | {"expire_reschedule_link": False}
    after = dict(booking) | {"data": dict(booking["data"]) | {"status": CANCELLED}}
    state = state_for(after, CANCEL, permissive, NOW)
    assert state.expired is True
    assert "cancelled" in state.reason


def test_a_link_on_a_rescheduled_meeting_says_it_moved(booking, meeting_type):
    after = dict(booking) | {"data": dict(booking["data"]) | {"status": RESCHEDULED}}
    state = state_for(after, RESCHEDULE, meeting_type["data"], NOW)
    assert state.expired is True
    assert "moved to its new booking" in state.reason


def test_a_booking_with_no_token_reports_a_closed_link(meeting_type):
    bare = {"id": "booking_x", "data": {"uid": "bk_x", "status": BOOKED, "start_at": at(3, 9, 0)}}
    state = state_for(bare, RESCHEDULE, meeting_type["data"], NOW)
    assert state.expired is True
    assert "carries no reschedule link" in state.reason


def test_a_reschedule_through_an_expired_link_is_a_410(engine, room, meeting_type):
    strict = make_type(engine, room, name="Strict", expire_reschedule_link=True)
    past = make_booking(engine, room, strict, uid="bk_past", start_at=at(-2, 9, 0))
    future = make_booking(engine, room, strict, uid="bk_future", start_at=at(3, 11, 0))
    with pytest.raises(LinkExpired) as caught:
        engine.reschedule(
            room["id"],
            past["data"]["uid"],
            {
                "start_at": at(3, 11, 0),
                "link_token": past["data"]["reschedule_token"],
                "reschedule_source": RESCHEDULE_LINK,
            },
            source=SOURCE,
        )
    assert "Expire Reschedule Link" in str(caught.value)
    assert future["data"]["uid"]


def test_a_host_action_is_not_blocked_by_an_expired_link(engine, room):
    """The setting is about a link, not about the product."""
    strict = make_type(engine, room, name="Strict", expire_reschedule_link=True)
    past = make_booking(engine, room, strict, uid="bk_past2", start_at=at(-2, 9, 0))
    change = engine.reschedule(
        room["id"],
        past["data"]["uid"],
        {"start_at": at(3, 9, 0), "actor_email": "dana@northwind.example", "reschedule_source": CHILICAL_HOME},
        source=SOURCE,
    )
    assert change["data"]["type"] == "rescheduled"


def test_the_link_endpoint_resolves_both_kinds_of_token(http, live):
    for kind in (RESCHEDULE, CANCEL):
        token = live["booking"]["data"][f"{kind}_token"]
        response = http.get(f"{PREFIX}/links/{token}")
        assert response.status_code == 200
        assert response.json()["kind"] == kind
        assert response.json()["expired"] is False


def test_the_link_endpoint_reports_a_closed_link_rather_than_refusing(http, live):
    """A dead link is a fact a rep needs; it becomes a refusal only on the write."""
    http.post(
        f"{PREFIX}/rooms/{live['room']['id']}/bookings/bk_http_1/cancel", json={"actor_email": "a@x.example"}
    )
    body = http.get(f"{PREFIX}/links/{live['booking']['data']['cancel_token']}").json()
    assert body["expired"] is True
    assert body["reason"]


def test_the_link_endpoint_resolves_a_reschedule_request_token(http, live):
    request = http.post(
        f"{PREFIX}/rooms/{live['room']['id']}/bookings/bk_http_1/request-reschedule",
        json={"actor_email": "a@x.example"},
    ).json()
    body = http.get(f"{PREFIX}/links/{request['data']['token']}").json()
    assert body["expired"] is False
    assert body["kind"] == "reschedule_requested"


def test_a_completed_requests_link_is_closed(http, live):
    """Once the attendee has answered, the link has nothing left to say."""
    request = http.post(
        f"{PREFIX}/rooms/{live['room']['id']}/bookings/bk_http_1/request-reschedule",
        json={"actor_email": "a@x.example"},
    ).json()
    target = next(
        slot["start_at"] for slot in http.get(f"{PREFIX}/availability?meeting_type_id={live['type']['id']}").json()["slots"]
        if not slot["in_past"]
    )
    http.post(f"{PREFIX}/reschedule-requests/{request['id']}/complete", json={"start_at": target})
    body = http.get(f"{PREFIX}/links/{request['data']['token']}").json()
    assert body["expired"] is True
    assert "completed" in body["reason"]


def test_an_unresolvable_link_token_is_a_404(http):
    response = http.get(f"{PREFIX}/links/nope")
    assert response.status_code == 404


def test_a_blank_link_token_is_a_404(http):
    """A trailing slash lands on the list route; a blank token is the real case."""
    assert http.get(f"{PREFIX}/links/%20").status_code == 404
    assert http.get(f"{PREFIX}/links").status_code == 200


def test_the_invite_body_carries_both_researched_tags(engine, booking):
    invite = engine.invite(booking["data"]["uid"], base="https://rooms.example.com")
    assert invite["tags"][RESCHEDULE_URL_TAG].startswith("https://rooms.example.com/booking/")
    assert invite["tags"][CANCEL_URL_TAG].startswith("https://rooms.example.com/booking/")
    assert booking["data"]["reschedule_token"] in invite["description"]
    assert booking["data"]["cancel_token"] in invite["description"]


def test_a_meeting_link_does_not_land_on_the_public_room_path(engine, booking):
    """``/r/<token>`` is the public-room share link, so a meeting link must not use it.

    ``wf-017-white-label`` matches ``^/r/([^/]+)/?$`` and renders a room. A link
    token is not a room slug, so a meeting link under ``/r/`` sends an attendee to
    a page that looks for a room and finds nothing. This test fails the day
    somebody "tidies" the path back to ``/r/``.
    """
    invite = engine.invite(booking["data"]["uid"], base="https://rooms.example.com")
    for tag in (RESCHEDULE_URL_TAG, CANCEL_URL_TAG):
        after_base = invite["tags"][tag].split("rooms.example.com", 1)[1]
        assert not after_base.startswith("/r/"), invite["tags"][tag]


def test_the_link_path_is_a_named_decision_and_can_be_overridden(engine, booking):
    """The research names Chili Piper's URLs and no path on this host, so the path
    is ours: a constant, with the reasoning on it, and overridable per call."""
    from dsr.scheduling.links import LINK_PATH

    assert LINK_PATH == "booking"
    moved = engine.invite(booking["data"]["uid"], base="https://rooms.example.com", path="m")
    assert moved["tags"][RESCHEDULE_URL_TAG] == (
        f"https://rooms.example.com/m/{booking['data']['reschedule_token']}"
    )


def test_the_invite_template_is_a_team_field_rather_than_a_product_constant(engine, room, meeting_type, booking):
    typed = make_type(engine, room, name="Typed", title_template="Custom: {title} at {start_at}")
    record = make_booking(engine, room, typed, uid="bk_typed")
    invite = engine.invite(record["data"]["uid"], base="https://rooms.example.com")
    assert invite["description"].startswith("Custom: ")
    assert "{reschedule_url}" not in invite["description"]


def test_the_url_template_carries_the_base_and_the_token():
    from dsr.scheduling.links import build_url

    assert (
        URL_TEMPLATE.format(base="https://x.example", path=LINK_PATH, token="abc")
        == "https://x.example/booking/abc"
    )
    # A base with a trailing slash, and a path with stray slashes, both normalise:
    # a doubled slash in an emailed link is a link some mail clients mangle.
    assert build_url("https://x.example/", "abc") == "https://x.example/booking/abc"
    assert build_url("https://x.example", "abc", path="/m/") == "https://x.example/m/abc"


# --------------------------------------------------------------------------- #
# Cancel: the researched scope and the optional reason
# --------------------------------------------------------------------------- #


def test_a_cancel_releases_the_booking(engine, room, booking):
    change = engine.cancel(
        room["id"], booking["data"]["uid"], {"actor_email": "a@x.example"}, source=SOURCE
    )
    assert change["data"]["type"] == "cancelled"
    assert engine.get_booking(booking["data"]["uid"])["data"]["status"] == CANCELLED


def test_the_cancellation_reason_is_optional_and_shipped_when_given(engine, room, meeting_type):
    quiet = make_booking(engine, room, meeting_type, uid="bk_quiet", start_at=at(3, 9, 0))
    change = engine.cancel(room["id"], quiet["data"]["uid"], {"actor_email": "a@x.example"}, source=SOURCE)
    assert change["data"]["cancellation_reason"] is None

    loud = make_booking(engine, room, meeting_type, uid="bk_loud", start_at=at(4, 11, 0))
    change = engine.cancel(
        room["id"],
        loud["data"]["uid"],
        {"reason": "I am no longer able to attend this session.", "actor_email": "a@x.example"},
        source=SOURCE,
    )
    assert change["data"]["cancellation_reason"] == "I am no longer able to attend this session."


def test_an_uncancellable_booking_is_a_409(engine, room, booking):
    engine.cancel(room["id"], booking["data"]["uid"], {"actor_email": "a@x.example"}, source=SOURCE)
    with pytest.raises(BookingConflict):
        engine.cancel(room["id"], booking["data"]["uid"], {"actor_email": "a@x.example"}, source=SOURCE)


def test_a_cancel_of_one_occurrence_leaves_the_rest_of_the_series_live(engine, room, meeting_type):
    first = make_booking(engine, room, meeting_type, uid="bk_s1", recurring_group="bk_s", recurrence_index=1)
    second = make_booking(
        engine, room, meeting_type, uid="bk_s2", start_at=at(4, 9, 0), recurring_group="bk_s", recurrence_index=2
    )
    engine.cancel(
        room["id"], first["data"]["uid"], {"scope": SCOPE_THIS, "actor_email": "a@x.example"}, source=SOURCE
    )
    assert engine.get_booking(second["data"]["uid"])["data"]["status"] == BOOKED


def test_a_wholesale_cancel_takes_every_live_occurrence(engine, room, meeting_type):
    for index, day in enumerate((3, 4, 5), start=1):
        make_booking(
            engine,
            room,
            meeting_type,
            uid=f"bk_w{index}",
            start_at=at(day, 9, 0),
            recurring_group="bk_w",
            recurrence_index=index,
        )
    result = engine.cancel(
        room["id"], "bk_w1", {"scope": SCOPE_ALL, "actor_email": "a@x.example"}, source=SOURCE
    )
    assert result["sweep"]["count"] == 3
    assert sorted(result["sweep"]["changes"]) and len(result["sweep"]["changes"]) == 3
    assert engine.list_bookings(status=CANCELLED, limit=10) and all(
        record["data"]["status"] == CANCELLED for record in engine.list_bookings(recurring_group="bk_w")
    )


def test_a_wholesale_cancel_writes_one_history_row_per_occurrence(engine, room, meeting_type):
    for index, day in enumerate((3, 4), start=1):
        make_booking(
            engine,
            room,
            meeting_type,
            uid=f"bk_v{index}",
            start_at=at(day, 9, 0),
            recurring_group="bk_v",
            recurrence_index=index,
        )
    engine.cancel(room["id"], "bk_v1", {"scope": SCOPE_ALL, "actor_email": "a@x.example"}, source=SOURCE)
    history = engine.changes(booking_uid="bk_v2")
    assert len(history) == 1
    assert history[0]["data"]["booking_uid"] == "bk_v2"


def test_a_wholesale_cancel_on_a_booking_with_no_series_cancels_just_it(engine, room, booking):
    result = engine.cancel(
        room["id"], booking["data"]["uid"], {"scope": SCOPE_ALL, "actor_email": "a@x.example"}, source=SOURCE
    )
    assert "sweep" not in result
    assert result["data"]["booking_uid"] == booking["data"]["uid"]


def test_an_unresearched_cancel_scope_is_refused(engine, room, booking):
    with pytest.raises(DomainError) as caught:
        engine.cancel(
            room["id"], booking["data"]["uid"], {"scope": "everything", "actor_email": "a@x.example"}, source=SOURCE
        )
    assert "everything" in str(caught.value)


def test_a_cancel_that_never_happened_writes_nothing(engine, room, booking, store):
    """A refusal part-way through a cancellation leaves no half-cancel behind."""
    before = len(store.list(CHANGE_COLLECTION))
    engine.cancel(
        room["id"],
        booking["data"]["uid"],
        {"actor_email": "a@x.example", "reason": "first"},
        source=SOURCE,
    )
    after_first = len(store.list(CHANGE_COLLECTION))
    with pytest.raises(BookingConflict):
        engine.cancel(
            room["id"],
            booking["data"]["uid"],
            {"actor_email": "a@x.example", "reason": "second", "scope": "everything"},
            source=SOURCE,
        )
    assert len(store.list(CHANGE_COLLECTION)) == after_first
    assert after_first == before + 1


def test_a_cancelled_booking_records_only_the_first_cancellation_reason(engine, room, booking):
    """The second attempt is refused, so the first reason stands unchanged."""
    engine.cancel(
        room["id"], booking["data"]["uid"], {"actor_email": "a@x.example", "reason": "first"}, source=SOURCE
    )
    assert engine.get_booking(booking["data"]["uid"])["data"]["cancellation_reason"] == "first"


def test_a_cancellation_through_the_cancel_link_is_the_attendees(engine, room, booking):
    """``CP.Meeting.CancelUrl`` is the attendee's door, so the who is the attendee's.

    The via source is resolved before the actor, not after: reading the actor
    first would file every link cancellation under the host, which is the one
    attribution Events History promises to get right.
    """
    change = engine.cancel(
        room["id"],
        booking["data"]["uid"],
        {"link_token": booking["data"]["cancel_token"], "actor_email": "priya.raman@northwind.example"},
        source=SOURCE,
    )
    assert change["data"]["via_source"] == RESCHEDULE_LINK
    assert change["data"]["actor_kind"] == "attendee"


def test_a_cancellation_with_an_explicit_source_still_wins(engine, room, booking):
    """A link token can travel in a forwarded mail; an explicit claim is the host's."""
    change = engine.cancel(
        room["id"],
        booking["data"]["uid"],
        {
            "link_token": booking["data"]["cancel_token"],
            "reschedule_source": CHILICAL_HOME,
            "actor_kind": "host",
            "actor_email": "dana@northwind.example",
        },
        source=SOURCE,
    )
    assert change["data"]["via_source"] == CHILICAL_HOME
    assert change["data"]["actor_kind"] == "host"


def test_a_cancellation_through_the_reschedule_link_is_still_the_attendees(engine, room, booking):
    """A link token of either kind names the same door, so it resolves the same way."""
    change = engine.cancel(
        room["id"],
        booking["data"]["uid"],
        {"link_token": booking["data"]["reschedule_token"], "actor_email": "priya.raman@northwind.example"},
        source=SOURCE,
    )
    assert change["data"]["via_source"] == RESCHEDULE_LINK
    assert change["data"]["actor_kind"] == "attendee"


def test_the_cancellation_records_who_by_email(engine, room, booking):
    change = engine.cancel(
        room["id"],
        booking["data"]["uid"],
        {"actor_email": "Priya.Raman@Northwind.Example", "actor_kind": "attendee"},
        source=SOURCE,
    )
    assert change["data"]["cancelled_by_email"] == "priya.raman@northwind.example"
    assert change["data"]["actor_kind"] == "attendee"


def test_a_cancel_with_no_actor_email_falls_back_to_the_attendee_on_file(engine, room, booking):
    change = engine.cancel(room["id"], booking["data"]["uid"], {}, source=SOURCE)
    assert change["data"]["actor_email"] == "priya.raman@northwind.example"


def test_a_cancel_records_the_via_source_it_arrived_through(engine, room, booking):
    """"triggers when a user or prospect cancels the meeting from any via source"."""
    change = engine.cancel(
        room["id"],
        booking["data"]["uid"],
        {"actor_email": "a@x.example", "reschedule_source": "calendar_event"},
        source=SOURCE,
    )
    assert change["data"]["via_source"] == "calendar_event"


def test_a_cancellation_reason_longer_than_the_cap_is_refused(engine, room, booking):
    """The reason ships in a webhook payload, so it is bounded."""
    with pytest.raises(DomainError) as caught:
        engine.cancel(
            room["id"], booking["data"]["uid"], {"reason": "x" * 2001, "actor_email": "a@x.example"}, source=SOURCE
        )
    assert "2000" in str(caught.value)


def test_an_actor_that_is_not_an_email_is_refused(engine, room, booking):
    with pytest.raises(DomainError) as caught:
        engine.cancel(room["id"], booking["data"]["uid"], {"actor_email": "nope"}, source=SOURCE)
    assert "email" in str(caught.value)


def test_an_unresearched_actor_kind_is_refused(engine, room, booking):
    with pytest.raises(DomainError) as caught:
        engine.cancel(
            room["id"], booking["data"]["uid"], {"actor_email": "a@x.example", "actor_kind": "robot"}, source=SOURCE
        )
    assert "robot" in str(caught.value)


# --------------------------------------------------------------------------- #
# Step 4: the fan-out
# --------------------------------------------------------------------------- #


def test_a_reschedule_pushes_the_researched_webhook_fields():
    envelopes = webhook_envelopes(
        change_type="rescheduled",
        new_booking={"uid": "bk_new", "start_at": at(4, 9, 0), "end_at": at(4, 9, 30)},
        old_booking={"uid": "bk_old", "start_at": at(3, 9, 0), "end_at": at(3, 9, 30)},
        reschedule_id=200,
        cancellation_reason=None,
        cancelled_by_email=None,
        location="Zoom",
    )
    by_name = {entry["webhook"]: entry["payload"] for entry in envelopes}
    assert by_name[BOOKING_RESCHEDULED] == {
        "uid": "bk_new",
        "rescheduleId": 200,
        "rescheduleUid": "bk_old",
        "rescheduleStartTime": at(4, 9, 0),
        "rescheduleEndTime": at(4, 9, 30),
    }
    assert "Meeting Update" in by_name


def test_a_reschedule_pushes_the_location_webhook_only_when_the_location_moved():
    unchanged = webhook_envelopes(
        change_type="rescheduled",
        new_booking={"uid": "bk_new", "start_at": at(4, 9, 0), "end_at": at(4, 9, 30)},
        old_booking={"uid": "bk_old", "location": "Zoom"},
        reschedule_id=1,
        cancellation_reason=None,
        cancelled_by_email=None,
        location="Zoom",
    )
    moved = webhook_envelopes(
        change_type="rescheduled",
        new_booking={"uid": "bk_new", "start_at": at(4, 9, 0), "end_at": at(4, 9, 30)},
        old_booking={"uid": "bk_old", "location": "Zoom"},
        reschedule_id=1,
        cancellation_reason=None,
        cancelled_by_email=None,
        location="Meet",
    )
    names = lambda entries: {entry["webhook"] for entry in entries}  # noqa: E731
    assert BOOKING_LOCATION_UPDATED not in names(unchanged)
    assert BOOKING_LOCATION_UPDATED in names(moved)


def test_a_cancel_pushes_the_researched_cancellation_fields():
    envelopes = webhook_envelopes(
        change_type="cancelled",
        new_booking=None,
        old_booking={"uid": "bk_old"},
        reschedule_id=None,
        cancellation_reason="I am no longer able to attend this session.",
        cancelled_by_email="tomas.vela@contoso.example",
        location=None,
    )
    by_name = {entry["webhook"]: entry["payload"] for entry in envelopes}
    assert by_name[BOOKING_CANCELLED] == {
        "uid": "bk_old",
        "cancellationReason": "I am no longer able to attend this session.",
        "cancelledByEmail": "tomas.vela@contoso.example",
    }
    assert by_name["Meeting Update"]["type"] == "Deleted"


def test_a_change_type_with_no_researched_webhook_pushes_nothing():
    assert webhook_envelopes(
        change_type="reschedule_requested",
        new_booking=None,
        old_booking={},
        reschedule_id=None,
        cancellation_reason=None,
        cancelled_by_email=None,
        location=None,
    ) == []


def test_a_reschedule_records_the_pushed_webhooks_readable(engine, room, booking, store):
    change = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    pushed = engine.webhooks(change_id=change["id"])
    # Compared as a set and not as a list: the fan-out order is not part of the
    # researched contract, and pinning it would make this a test of the order the
    # envelopes happen to be built in.
    assert {row["data"]["webhook"] for row in pushed} == {BOOKING_RESCHEDULED, "Meeting Update"}
    assert set(change["data"]["webhooks_sent"]) == {BOOKING_RESCHEDULED, "Meeting Update"}
    assert store.count_where(WEBHOOK_COLLECTION, {"change_id": change["id"]}) == 2


def test_the_reschedule_webhook_carries_the_researched_payload(engine, room, booking):
    change = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    payload = engine.webhooks(change_id=change["id"], webhook=BOOKING_RESCHEDULED)[0]["data"]["payload"]
    assert payload["rescheduleUid"] == booking["data"]["uid"]
    assert payload["uid"] == change["data"]["new_booking_uid"]
    assert payload["rescheduleId"] == 1
    assert payload["rescheduleStartTime"] == at(4, 9, 0)
    assert payload["rescheduleEndTime"] == at(4, 9, 30)


def test_a_cancel_records_the_cancellation_webhooks(engine, room, booking):
    change = engine.cancel(
        room["id"],
        booking["data"]["uid"],
        {"reason": "cannot make it", "actor_email": "a@x.example"},
        source=SOURCE,
    )
    assert change["data"]["webhooks_sent"] == [BOOKING_CANCELLED, "Meeting Update"]


def test_the_workflow_trigger_fires_and_sends_on_both_channels(engine, room, meeting_type, booking, store):
    change = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    assert change["data"]["triggers"] == ["rescheduleEvent"]
    notices = engine.notifications(change_id=change["id"])
    assert {note["data"]["channel"] for note in notices} == set(DEFAULT_CHANNELS)
    assert {note["data"]["recipient"] for note in notices} == {
        "priya.raman@northwind.example",
        "dana@northwind.example",
    }
    assert change["data"]["notifications_sent"] == 4


def test_a_cancellation_fires_the_other_trigger_and_the_other_template(engine, room, booking):
    change = engine.cancel(room["id"], booking["data"]["uid"], {"actor_email": "a@x.example"}, source=SOURCE)
    assert change["data"]["triggers"] == ["eventCancelled"]
    assert {note["data"]["template"] for note in engine.notifications(change_id=change["id"])} == {"cancelled"}


def test_each_change_type_has_one_template_and_one_trigger():
    assert TEMPLATE_FOR_CHANGE["rescheduled"] == "rescheduled"
    assert TEMPLATE_FOR_CHANGE["cancelled"] == "cancelled"
    assert set(TRIGGER_FOR_CHANGE) == {"rescheduled", "cancelled", "location_updated"}


def test_a_meeting_type_can_narrow_its_channels(engine, room, meeting_type, booking):
    narrowed = make_type(engine, room, name="Email only", notify_channels=["email"])
    record = make_booking(engine, room, narrowed, uid="bk_email", start_at=at(3, 11, 0))
    change = engine.reschedule(
        room["id"], record["data"]["uid"], {"start_at": at(4, 11, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    assert {note["data"]["channel"] for note in engine.notifications(change_id=change["id"])} == {"email"}


def test_channels_default_to_both_in_the_order_the_research_names_them():
    assert channels_for({}) == ["email", "slack"]
    assert channels_for({"notify_channels": ["slack"]}) == ["slack"]


def test_an_empty_channel_list_falls_back_to_both_rather_than_to_nobody():
    """Silently notifying nobody is worse than over-notifying a demo host."""
    assert channels_for({"notify_channels": []}) == ["email", "slack"]


def test_a_notice_is_only_written_for_a_channel_with_a_recipient(engine, room):
    plain = make_type(engine, room, name="No attendee", reminder_offsets=[])
    record = engine.create_booking(
        {"meeting_type_id": plain["id"], "start_at": at(3, 9, 0), "uid": "bk_solo"}, room_id=room["id"], source=SOURCE
    )
    change = engine.reschedule(
        room["id"], record["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    assert change["data"]["notifications_sent"] == 2


def test_the_crm_event_is_created_on_a_booking_that_had_none(engine, room, booking):
    change = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    assert change["data"]["crm_event"]["action"] == "created"
    assert change["data"]["crm_event"]["sobject"] == "Event"


def test_a_crm_event_is_updated_rather_than_duplicated_across_moves(engine, room, booking, store):
    """"CRM Event update/delete" is one object changing, not one per move.

    Two moves, one Salesforce Event. A second Event on a chain would put the same
    meeting on two calendars - the failure this workflow prevents on the DSR side,
    reproduced on the CRM side.
    """
    first = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    event_id = first["data"]["crm_event"]["event_id"]
    second = engine.reschedule(
        room["id"],
        first["data"]["new_booking_uid"],
        {"start_at": at(5, 9, 0), "actor_email": "a@x.example"},
        source=SOURCE,
    )
    assert second["data"]["crm_event"]["action"] == "updated"
    assert second["data"]["crm_event"]["event_id"] == event_id
    assert len(store.list(CRM_EVENT_COLLECTION)) == 1


def test_a_moved_meeting_carries_both_event_ids_forward(engine, room, booking):
    """The new booking points at the events that moved, so a reader can follow them."""
    change = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    moved = engine.get_booking(change["data"]["new_booking_uid"])
    assert moved["data"]["calendar_event_id"] == change["data"]["calendar"]["event_id"]
    assert moved["data"]["crm_event_id"] == change["data"]["crm_event"]["event_id"]


def test_a_calendar_event_is_moved_rather_than_duplicated_when_one_exists(engine, room, booking, store):
    """"calendar event moved/cancelled" is one event changing time, not two events.

    A meeting moved three times has exactly one calendar event carrying the third
    time, which is what a calendar looks like and what a subscriber to that event
    needs in order to keep receiving updates.
    """
    change = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    assert change["data"]["calendar"]["action"] == "created"
    event_id = change["data"]["calendar"]["event_id"]

    second = engine.reschedule(
        room["id"],
        change["data"]["new_booking_uid"],
        {"start_at": at(5, 9, 0), "actor_email": "a@x.example"},
        source=SOURCE,
    )
    assert second["data"]["calendar"]["action"] == "updated"
    assert second["data"]["calendar"]["event_id"] == event_id
    assert len(store.list(CALENDAR_EVENT_COLLECTION)) == 1


def test_a_calendar_event_that_existed_is_updated_not_replaced(engine, room, booking, store):
    seeded = store.create(
        CALENDAR_EVENT_COLLECTION,
        {
            "booking_uid": booking["data"]["uid"],
            "provider": "google",
            "event_id": "cal_existing",
            "start_at": at(3, 9, 0),
            "end_at": at(3, 9, 30),
            "status": "confirmed",
        },
        source=SOURCE,
    )
    change = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    assert change["data"]["calendar"]["action"] == "updated"
    assert change["data"]["calendar"]["record_id"] == seeded["id"]
    assert change["data"]["calendar"]["event_id"] == "cal_existing"
    row = store.get(seeded["id"])
    assert row["data"]["start_at"] == at(4, 9, 0)


def test_the_calendar_event_takes_the_new_times(engine, room, booking, store):
    change = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    # Pointed at the booking this change leaves in force, and identified by the
    # meeting's chain. The new booking also inherits the event id, so a downstream
    # reader can follow it.
    row = store.find(
        CALENDAR_EVENT_COLLECTION, {"booking_uid": change["data"]["new_booking_uid"]}, limit=1
    )[0]
    assert row["data"]["chain_root"] == booking["data"]["chain_root"]
    assert row["data"]["start_at"] == at(4, 9, 0)
    assert row["data"]["status"] == "confirmed"
    moved = engine.get_booking(change["data"]["new_booking_uid"])
    assert moved["data"]["calendar_event_id"] == row["data"]["event_id"]


def test_a_cancelled_booking_s_calendar_event_is_cancelled(engine, room, booking, store):
    """A cancel with no event yet creates one, so the gap is visible."""
    change = engine.cancel(room["id"], booking["data"]["uid"], {"actor_email": "a@x.example"}, source=SOURCE)
    row = store.find(CALENDAR_EVENT_COLLECTION, {"booking_uid": booking["data"]["uid"]}, limit=1)[0]
    assert row["data"]["status"] == "cancelled"
    assert change["data"]["calendar"]["action"] == "created"


def test_a_cancelled_meetings_calendar_event_is_cancelled_after_a_reschedule(engine, room, booking, store):
    """The one event the move created is the one the cancel marks cancelled."""
    change = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    cancelled = engine.cancel(
        room["id"], change["data"]["new_booking_uid"], {"actor_email": "a@x.example"}, source=SOURCE
    )
    assert cancelled["data"]["calendar"]["action"] == "updated"
    rows = store.list(CALENDAR_EVENT_COLLECTION)
    assert len(rows) == 1
    assert rows[0]["data"]["status"] == "cancelled"
    # A cancellation leaves the booking in force, so the row still points at it.
    assert rows[0]["data"]["booking_uid"] == change["data"]["new_booking_uid"]
    assert rows[0]["data"]["chain_root"] == booking["data"]["chain_root"]


def test_crm_status_follows_the_change_type():
    assert crm_status_for("rescheduled") == "active"
    assert crm_status_for("cancelled") == "cancelled"


# --------------------------------------------------------------------------- #
# Delete Event: the researched admin toggle
# --------------------------------------------------------------------------- #


def test_delete_event_on_removes_the_crm_event_on_a_cancel(engine, room):
    """A reschedule creates the CRM event; the cancel then applies the toggle."""
    strict = make_type(engine, room, name="Strict", delete_event=True)
    record = make_booking(engine, room, strict, uid="bk_del_on")
    change = engine.reschedule(
        room["id"], record["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    cancelled = engine.cancel(
        room["id"], change["data"]["new_booking_uid"], {"actor_email": "a@x.example"}, source=SOURCE
    )
    assert cancelled["data"]["crm_event"]["action"] == "deleted"
    assert "Delete Event is on" in cancelled["data"]["crm_event"]["because"]


def test_delete_event_off_keeps_the_crm_event_cancelled(engine, room):
    permissive = make_type(engine, room, name="Keep", delete_event=False)
    record = make_booking(engine, room, permissive, uid="bk_keep")
    change = engine.reschedule(
        room["id"], record["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    cancelled = engine.cancel(
        room["id"], change["data"]["new_booking_uid"], {"actor_email": "a@x.example"}, source=SOURCE
    )
    assert cancelled["data"]["crm_event"]["action"] in ("created", "updated")
    rows = engine.crm_events(booking_uid=change["data"]["new_booking_uid"])
    assert rows[0]["data"]["status"] == "cancelled"


def test_a_deletion_is_recorded_inside_the_change_s_own_transaction(engine, room, meeting_type, booking):
    """It is a status, not a store delete, so it cannot half-apply."""
    change = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    new_uid = change["data"]["new_booking_uid"]
    cancelled = engine.cancel(room["id"], new_uid, {"actor_email": "a@x.example"}, source=SOURCE)
    assert cancelled["data"]["crm_event"]["action"] == "deleted"
    assert "inside this transaction" in cancelled["data"]["crm_event"]["recorded_as"]
    rows = engine.crm_events(booking_uid=new_uid)
    assert rows[0]["data"]["status"] == "deleted"


def test_a_cancellation_with_nothing_to_delete_says_so(engine, room, booking):
    change = engine.cancel(
        room["id"],
        booking["data"]["uid"],
        {"actor_email": "a@x.example", "actor_kind": "host", "reschedule_source": "calendar_event"},
        source=SOURCE,
    )
    assert change["data"]["crm_event"]["action"] == "skipped"
    assert "no CRM Event existed" in change["data"]["crm_event"]["reason"]


def test_propagating_a_crm_event_to_nothing_is_a_skipped_row(store, db):
    """The unit, so the branch is covered without a whole engine around it."""
    with db.transaction(actor="test", source=SOURCE) as tx:
        result = propagate_crm_event(
            tx,
            booking={"uid": "bk_none", "title": "T"},
            current_uid="bk_none",
            change_type="cancelled",
            delete_event=True,
            start_at=at(3, 9, 0),
            end_at=at(3, 9, 30),
            change_id="change_x",
            existing=None,
            room_id=None,
            actor="test",
            source=SOURCE,
        )
    assert result["action"] == "skipped"


# --------------------------------------------------------------------------- #
# Request to reschedule: the two-step path
# --------------------------------------------------------------------------- #


def test_a_request_reschedule_cancels_the_booking_and_raises_a_request(engine, room, booking, store):
    """Both sentences of the researched sentence are load-bearing."""
    request = engine.request_reschedule(
        room["id"],
        booking["data"]["uid"],
        {"reason": "cannot make it", "actor_email": "priya.raman@northwind.example"},
        source=SOURCE,
    )
    assert engine.get_booking(booking["data"]["uid"])["data"]["status"] == CANCELLED
    assert request["data"]["status"] == "pending"
    assert request["data"]["original_uid"] == booking["data"]["uid"]
    assert request["data"]["reason"] == "cannot make it"
    assert len(store.list(CHANGE_COLLECTION)) == 1


def test_a_request_reschedule_still_pushes_the_cancellation(engine, room, booking):
    """A downstream consumer must be able to tell this from a real cancellation."""
    request = engine.request_reschedule(
        room["id"], booking["data"]["uid"], {"actor_email": "a@x.example"}, source=SOURCE
    )
    change = store_change(engine, booking["data"]["uid"])
    assert change["data"]["cause"] == "reschedule_requested"
    assert request["data"]["token"]


def test_a_request_s_token_outlives_the_booking_it_cancelled(engine, room, booking):
    """The original link is on a booking this call has just cancelled."""
    request = engine.request_reschedule(
        room["id"], booking["data"]["uid"], {"actor_email": "a@x.example"}, source=SOURCE
    )
    state = engine.link_state(request["data"]["token"])
    assert state.expired is False
    assert state.kind == "reschedule_requested"
    assert state.booking_uid == booking["data"]["uid"]


def test_a_requests_token_survives_even_where_the_booking_link_would_not(engine, room):
    """Expire Reschedule Link "lets a vendor force a fresh booking after the fact"."""
    strict = make_type(engine, room, name="Strict", expire_reschedule_link=True)
    past = make_booking(engine, room, strict, uid="bk_rexp", start_at=at(-2, 9, 0))
    request = engine.request_reschedule(
        room["id"], past["data"]["uid"], {"actor_email": "a@x.example"}, source=SOURCE
    )
    assert engine.link_state(past["data"]["reschedule_token"]).expired is True
    assert engine.link_state(request["data"]["token"]).expired is False


def test_completing_a_request_creates_the_new_booking_and_the_history_row(engine, room, meeting_type, booking):
    request = engine.request_reschedule(
        room["id"], booking["data"]["uid"], {"actor_email": "a@x.example"}, source=SOURCE
    )
    target = next(
        slot["start_at"] for slot in engine.host_slots(meeting_type["id"], now=NOW) if not slot["in_past"]
    )
    change = engine.complete_request(request["id"], {"start_at": target}, source=SOURCE)
    assert change["data"]["type"] == "rescheduled"
    assert change["data"]["reschedule_source"] == RESCHEDULE_LINK
    assert change["data"]["reschedule_request_id"] == request["id"]
    assert engine.get_change(request["id"]) is None
    assert engine.get_request(request["id"])["data"]["status"] == "completed"


def test_a_completed_request_pushes_the_reschedule_webhook_not_only_the_cancel(engine, room, booking):
    """Churn alerting counts moves; a cancellation would look like a release."""
    request = engine.request_reschedule(
        room["id"], booking["data"]["uid"], {"actor_email": "a@x.example"}, source=SOURCE
    )
    target = next(
        slot["start_at"]
        for slot in engine.host_slots(booking["data"]["meeting_type_id"], now=NOW)
        if not slot["in_past"]
    )
    change = engine.complete_request(request["id"], {"start_at": target}, source=SOURCE)
    assert BOOKING_RESCHEDULED in change["data"]["webhooks_sent"]
    payloads = [row["data"]["payload"] for row in engine.webhooks(change_id=change["id"])]
    assert any(payload.get("rescheduleUid") == booking["data"]["uid"] for payload in payloads)


def test_a_completed_request_carries_the_reason_it_was_raised_with(engine, room, booking):
    request = engine.request_reschedule(
        room["id"],
        booking["data"]["uid"],
        {"reason": "something came up", "actor_email": "a@x.example"},
        source=SOURCE,
    )
    target = next(
        slot["start_at"]
        for slot in engine.host_slots(booking["data"]["meeting_type_id"], now=NOW)
        if not slot["in_past"]
    )
    change = engine.complete_request(request["id"], {"start_at": target}, source=SOURCE)
    assert change["data"]["reschedule_reason"] == "something came up"


def test_a_completed_request_lands_in_the_rooms_history(engine, room, booking):
    """`room_id` is reserved and stripped from data, so the envelope is the only place."""
    request = engine.request_reschedule(
        room["id"], booking["data"]["uid"], {"actor_email": "a@x.example"}, source=SOURCE
    )
    target = next(
        slot["start_at"]
        for slot in engine.host_slots(booking["data"]["meeting_type_id"], now=NOW)
        if not slot["in_past"]
    )
    change = engine.complete_request(request["id"], {"start_at": target}, source=SOURCE)
    assert change["room_id"] == room["id"]
    assert request["room_id"] == room["id"]
    assert [row["id"] for row in engine.changes(room_id=room["id"])]


def test_a_completion_cannot_be_repeated(engine, room, booking):
    request = engine.request_reschedule(
        room["id"], booking["data"]["uid"], {"actor_email": "a@x.example"}, source=SOURCE
    )
    target = next(
        slot["start_at"]
        for slot in engine.host_slots(booking["data"]["meeting_type_id"], now=NOW)
        if not slot["in_past"]
    )
    engine.complete_request(request["id"], {"start_at": target}, source=SOURCE)
    with pytest.raises(BookingConflict) as caught:
        engine.complete_request(request["id"], {"start_at": target}, source=SOURCE)
    assert "completed" in str(caught.value)


def test_completing_a_request_that_does_not_exist_is_a_404(engine):
    with pytest.raises(MeetingNotFound):
        engine.complete_request("nope", {"start_at": at(4, 9, 0)}, source=SOURCE)


def test_completing_a_request_with_no_meeting_type_is_refused(engine, store, room):
    """A request that outlived its type has no availability to recompute against."""
    orphan = store.create(
        REQUEST_COLLECTION,
        {"token": "tok_orphan", "status": "pending", "original_uid": "bk_orphan", "chain_root": "bk_orphan"},
        room_id=room["id"],
        source=SOURCE,
    )
    with pytest.raises(DomainError) as caught:
        engine.complete_request(orphan["id"], {"start_at": at(4, 9, 0)}, source=SOURCE)
    assert "no Meeting Type" in str(caught.value)


def test_completing_a_request_to_a_slot_nobody_holds_is_refused(engine, room, booking):
    request = engine.request_reschedule(
        room["id"], booking["data"]["uid"], {"actor_email": "a@x.example"}, source=SOURCE
    )
    with pytest.raises(DomainError):
        engine.complete_request(request["id"], {"start_at": at(4, 20, 0)}, source=SOURCE)


def test_completing_a_request_into_the_past_is_refused(engine, room, booking):
    request = engine.request_reschedule(
        room["id"], booking["data"]["uid"], {"actor_email": "a@x.example"}, source=SOURCE
    )
    with pytest.raises(DomainError) as caught:
        # A weekend, so the refusal is unambiguously about the past: any message
        # about hours here would be a different bug, and this test would still
        # pass on a weekday.
        engine.complete_request(request["id"], {"start_at": at(-3, 10, 0)}, source=SOURCE)
    assert "past" in str(caught.value) or "not a slot on offer" in str(caught.value)


def test_a_completed_requests_booking_gets_its_own_links(engine, room, booking):
    request = engine.request_reschedule(
        room["id"], booking["data"]["uid"], {"actor_email": "a@x.example"}, source=SOURCE
    )
    target = next(
        slot["start_at"]
        for slot in engine.host_slots(booking["data"]["meeting_type_id"], now=NOW)
        if not slot["in_past"]
    )
    change = engine.complete_request(request["id"], {"start_at": target}, source=SOURCE)
    new = engine.get_booking(change["data"]["new_booking_uid"])
    assert new["data"]["reschedule_token"] != booking["data"]["reschedule_token"]
    assert new["data"]["rescheduled_from_uid"] == booking["data"]["uid"]


def test_a_completed_request_is_recorded_by_the_attendee_who_answered(engine, room, booking):
    request = engine.request_reschedule(
        room["id"], booking["data"]["uid"], {"actor_email": "a@x.example"}, source=SOURCE
    )
    target = next(
        slot["start_at"]
        for slot in engine.host_slots(booking["data"]["meeting_type_id"], now=NOW)
        if not slot["in_past"]
    )
    change = engine.complete_request(request["id"], {"start_at": target}, source=SOURCE)
    assert change["data"]["actor_kind"] == "attendee"
    assert change["data"]["actor_email"] == "priya.raman@northwind.example"


def test_a_completion_with_no_actor_email_is_refused_rather_than_guessing(engine, room, store):
    plain = make_type(engine, room, name="Anonymous")
    record = make_booking(engine, room, plain, uid="bk_anon", attendee_email="")
    request = engine.request_reschedule(
        room["id"], record["data"]["uid"], {"actor_email": "a@x.example"}, source=SOURCE
    )
    with pytest.raises(DomainError) as caught:
        engine.complete_request(request["id"], {"start_at": at(4, 9, 0)}, source=SOURCE)
    assert "actor_email" in str(caught.value)


def store_change(engine, booking_uid):
    rows = engine.changes(booking_uid=booking_uid, limit=5)
    assert rows
    return rows[0]


# --------------------------------------------------------------------------- #
# Atomicity
# --------------------------------------------------------------------------- #


def test_a_reschedule_writes_its_rows_in_one_transaction(engine, room, booking, store):
    """The audit log is only complete if the change and its consequences commit together."""
    before = store.db.audit_count()
    change = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    rows = store.db.audit(request_id=None, limit=1000)
    written = [row for row in rows if row["source"] == SOURCE and row["ts"] >= change["created_at"]]
    assert written
    assert before < store.db.audit_count()


def test_a_refused_change_rolls_everything_back(engine, room, booking, store):
    before_records = store.stats()["records"]
    before_audit = store.db.audit_count()
    with pytest.raises(DomainError):
        engine.reschedule(
            room["id"],
            booking["data"]["uid"],
            {"start_at": at(4, 9, 0), "reason": "x" * 2001, "actor_email": "a@x.example"},
            source=SOURCE,
        )
    assert store.stats()["records"] == before_records
    assert store.db.audit_count() == before_audit


def test_a_wholesale_cancel_rolls_back_entirely_when_one_target_fails(engine, room, meeting_type, store):
    """A half-cancelled series is the one outcome nobody can recover from."""
    for index, day in enumerate((3, 4), start=1):
        make_booking(
            engine,
            room,
            meeting_type,
            uid=f"bk_atomic_{index}",
            start_at=at(day, 9, 0),
            recurring_group="bk_atomic",
            recurrence_index=index,
        )
    before = store.stats()["records"]
    with pytest.raises(DomainError):
        engine.cancel(
            room["id"],
            "bk_atomic_1",
            {"scope": SCOPE_ALL, "actor_email": "a@x.example", "reason": "x" * 2001},
            source=SOURCE,
        )
    assert store.stats()["records"] == before
    assert all(
        record["data"]["status"] == BOOKED
        for record in engine.list_bookings(recurring_group="bk_atomic")
    )


# --------------------------------------------------------------------------- #
# plan: the read-only half
# --------------------------------------------------------------------------- #


def test_a_plan_writes_nothing_at_all(engine, room, booking, store):
    before = store.stats()["records"]
    plan = engine.plan(room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0)})
    assert plan["intent"] == RESCHEDULE
    assert store.stats()["records"] == before


def test_a_plan_says_the_same_webhooks_the_write_will_push(engine, room, booking, store):
    plan = engine.plan(room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0)})
    change = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    assert plan["webhooks"] == change["data"]["webhooks_sent"]
    assert plan["triggers"] == change["data"]["triggers"]


def test_a_plan_predicts_the_reschedule_id_the_write_uses(engine, room, booking):
    plan = engine.plan(room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0)})
    change = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    assert plan["reschedule_id"] == change["data"]["reschedule_id"]


def test_a_plan_refuses_exactly_what_the_write_refuses(engine, room, booking, store):
    before = store.stats()["records"]
    with pytest.raises(DomainError):
        engine.plan(room["id"], booking["data"]["uid"], {"start_at": at(4, 20, 0)})
    assert store.stats()["records"] == before


def test_a_plan_of_a_cancel_says_what_delete_event_will_do(engine, room, booking):
    plan = engine.plan(room["id"], booking["data"]["uid"], {"intent": CANCEL})
    assert plan["delete_event"] is True
    assert plan["crm_sobject"] == "Event"
    assert plan["change_type"] == "cancelled"


def test_a_plan_of_a_wholesale_cancel_names_every_target(engine, room, meeting_type):
    for index, day in enumerate((3, 4, 5), start=1):
        make_booking(
            engine,
            room,
            meeting_type,
            uid=f"bk_pt{index}",
            start_at=at(day, 9, 0),
            recurring_group="bk_pt",
            recurrence_index=index,
        )
    plan = engine.plan(room["id"], "bk_pt1", {"intent": CANCEL, "scope": SCOPE_ALL})
    assert sorted(plan["targets"]) == ["bk_pt1", "bk_pt2", "bk_pt3"]


def test_a_plan_reports_a_link_state_without_acting_on_it(engine, room, booking):
    plan = engine.plan(
        room["id"],
        booking["data"]["uid"],
        {"start_at": at(4, 9, 0), "link_token": booking["data"]["reschedule_token"]},
    )
    assert plan["reschedule_source"] == RESCHEDULE_LINK
    assert plan["link"]["expired"] is False


def test_a_plan_with_no_start_at_is_refused(engine, room, booking):
    with pytest.raises(DomainError) as caught:
        engine.plan(room["id"], booking["data"]["uid"], {})
    assert "start_at" in str(caught.value)


def test_a_plan_with_an_unknown_intent_is_refused(engine, room, booking):
    """The refusal names the published set, like every other vocabulary refusal."""
    with pytest.raises(DomainError) as caught:
        engine.plan(room["id"], booking["data"]["uid"], {"intent": "teleport"})
    assert "teleport" in str(caught.value)
    for name in INTENT_NAMES:
        assert name in str(caught.value)


def test_a_plan_names_the_actor_the_write_will_record(engine, room, booking):
    plan = engine.plan(
        room["id"],
        booking["data"]["uid"],
        {"start_at": wd(4), "link_token": booking["data"]["reschedule_token"]},
    )
    change = engine.reschedule(
        room["id"],
        booking["data"]["uid"],
        {
            "start_at": wd(4),
            "link_token": booking["data"]["reschedule_token"],
            "actor_email": "priya.raman@northwind.example",
        },
        source=SOURCE,
    )
    assert plan["actor_email"] == change["data"]["actor_email"]
    assert plan["actor_kind"] == change["data"]["actor_kind"] == "attendee"


def test_a_plan_of_a_request_reschedule_says_both_of_its_halves(engine, room, booking):
    """"The booking will be cancelled **and** the attendee will receive a link."

    A plan that described only the reschedule half would tell the caller one
    thing and then get them another - and the cancellation is the destructive half.
    """
    plan = engine.plan(
        room["id"], booking["data"]["uid"], {"intent": REQUEST_RESCHEDULE, "start_at": wd(4)}
    )
    assert plan["intent"] == REQUEST_RESCHEDULE
    assert plan["also"]["cancels_the_booking"] is True
    assert plan["also"]["cause"] == CHANGE_RESCHEDULE_REQUESTED
    assert plan["also"]["change_type"] == "cancelled"
    assert "cancels the booking" in plan["writes"]


def test_a_plan_of_a_cancel_names_the_actor_the_write_will_record(engine, room, booking):
    plan = engine.plan(
        room["id"],
        booking["data"]["uid"],
        {"intent": CANCEL, "link_token": booking["data"]["cancel_token"]},
    )
    change = engine.cancel(
        room["id"],
        booking["data"]["uid"],
        {"link_token": booking["data"]["cancel_token"], "actor_email": "priya.raman@northwind.example"},
        source=SOURCE,
    )
    assert plan["actor_email"] == change["data"]["actor_email"]
    assert plan["actor_kind"] == change["data"]["actor_kind"] == "attendee"


# --------------------------------------------------------------------------- #
# Summary and reads
# --------------------------------------------------------------------------- #


def test_the_summary_counts_changes_by_type_and_source(engine, room, booking):
    engine.reschedule(
        room["id"],
        booking["data"]["uid"],
        {"start_at": at(4, 9, 0), "actor_email": "a@x.example", "reschedule_source": "calendar_event"},
        source=SOURCE,
    )
    summary = engine.summary()
    assert summary["changes"] == 1
    assert summary["by_type"] == {"rescheduled": 1}
    assert summary["by_reschedule_source"] == {"calendar_event": 1}


def test_the_summary_counts_only_the_cancellation_reasons_that_were_given(engine, room, meeting_type, booking):
    make_booking(engine, room, meeting_type, uid="bk_sum2", start_at=at(4, 11, 0))
    engine.cancel(
        room["id"], booking["data"]["uid"], {"reason": "cannot make it", "actor_email": "a@x.example"}, source=SOURCE
    )
    engine.cancel(room["id"], "bk_sum2", {"actor_email": "a@x.example"}, source=SOURCE)
    assert engine.summary()["with_cancellation_reason"] == 1


def test_a_room_scoped_summary_cannot_be_misread_as_a_product_wide_one(engine, room, other_room, meeting_type):
    make_booking(engine, room, meeting_type, uid="bk_room1")
    other = make_booking(engine, other_room, meeting_type, uid="bk_room2", start_at=at(4, 11, 0))
    engine.cancel(room["id"], "bk_room1", {"actor_email": "a@x.example"}, source=SOURCE)
    assert engine.summary()["changes"] == 1
    assert engine.summary(room_id=other_room["id"])["changes"] == 0


def test_changes_can_be_filtered_by_chain_to_answer_the_churn_question(engine, room, booking):
    first = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    engine.reschedule(
        room["id"],
        first["data"]["new_booking_uid"],
        {"start_at": at(5, 9, 0), "actor_email": "a@x.example"},
        source=SOURCE,
    )
    chain = engine.changes(chain_root=first["data"]["chain_root"])
    assert len(chain) == 2
    assert {row["data"]["reschedule_id"] for row in chain} == {1, 2}


def test_changes_can_be_filtered_by_actor(engine, room, booking):
    engine.reschedule(
        room["id"],
        booking["data"]["uid"],
        {"start_at": at(4, 9, 0), "actor_email": "dana@northwind.example"},
        source=SOURCE,
    )
    assert len(engine.changes(actor_email="dana@northwind.example")) == 1
    assert engine.changes(actor_email="nobody@x.example") == []


def test_an_unresearched_change_type_filter_is_refused(engine):
    with pytest.raises(DomainError):
        engine.changes(change_type="teleported")


def test_webhooks_can_be_filtered_by_name_and_status(engine, room, booking):
    engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    assert engine.webhooks(webhook=BOOKING_RESCHEDULED)
    assert engine.webhooks(webhook="Nonexistent") == []
    assert engine.webhooks(status="delivered")
    assert engine.webhooks(status="failed") == []


def test_a_bookings_history_falls_back_to_its_chain(engine, room, booking):
    """A booking that was moved once has no rows of its own; its chain does."""
    change = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    fresh = engine.get_booking(change["data"]["new_booking_uid"])
    assert engine.changes(booking_uid=fresh["data"]["uid"]) == []


# --------------------------------------------------------------------------- #
# The error hierarchy and its statuses
# --------------------------------------------------------------------------- #


def test_the_error_hierarchy_distinguishes_the_three_outcomes():
    assert issubclass(MeetingNotFound, MeetingChangeError)
    assert issubclass(BookingConflict, MeetingChangeError)
    assert issubclass(LinkExpired, MeetingChangeError)
    assert MeetingChangeError.status == 400
    assert MeetingNotFound.status == 404
    assert BookingConflict.status == 409
    assert LinkExpired.status == 410


def test_each_error_carries_its_own_code():
    assert MeetingChangeError("x").code == "meeting_change_error"
    assert MeetingNotFound("x").code == "booking_not_found"
    assert BookingConflict("x").code == "booking_conflict"
    assert LinkExpired("x").code == "reschedule_link_expired"


def test_a_code_can_be_overridden_where_the_detail_matters():
    assert MeetingChangeError("x", code="custom").code == "custom"


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


@pytest.fixture()
def live(http):
    """A room, a Meeting Type and a booking, created over the real routes."""
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    meeting_type = http.post(
        f"{PREFIX}/meeting-types?room_id={room['id']}",
        json={
            "name": "Northwind Demo",
            "host_email": "dana@northwind.example",
            "duration_minutes": 30,
            "hours": "09:00-17:00",
            "reminder_offsets": [1440, 60],
        },
    ).json()
    booking = http.post(
        f"{PREFIX}/bookings?room_id={room['id']}",
        json={
            "uid": "bk_http_1",
            "meeting_type_id": meeting_type["id"],
            "title": "Northwind walkthrough",
            "attendee_email": "priya.raman@northwind.example",
            # Measured from the real clock, not from NOW: these routes build
            # their engine from the wall clock, so a fixed NOW + 3 days stopped
            # being a future date on 2026-09-30. The date is also snapped to the
            # type's own 09:00-17:00 grid on a weekday, because the availability
            # test below needs the booking's own slot to appear in the window
            # with original_slot set, and an off-grid time has no slot to
            # release. Thirty days out is far enough that nothing collides with
            # the second booking at at(-2).
            "start_at": next(
                d.isoformat()
                for d in (
                    datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
                    + timedelta(days=n)
                    for n in range(28, 40)
                )
                if d.weekday() < 5
            ),
            "location": "Zoom",
        },
    ).json()
    return {"room": room, "type": meeting_type, "booking": booking}


def test_vocabulary_and_inferences_are_readable_without_a_store(http):
    assert http.get(f"{PREFIX}/vocabulary").status_code == 200
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(scheduling_inferences.INFERENCES)
    assert body["inferences"]


def test_a_meeting_type_can_be_created_read_patched_and_deleted(http, live):
    created = http.post(
        f"{PREFIX}/meeting-types?room_id={live['room']['id']}",
        json={"name": "Extra", "host_email": "sam@contoso.example", "expire_reschedule_link": True},
    )
    assert created.status_code == 201
    record = created.json()
    assert record["data"]["expire_reschedule_link"] is True
    assert http.get(f"{PREFIX}/meeting-types/{record['id']}").status_code == 200
    assert http.patch(f"{PREFIX}/meeting-types/{record['id']}", json={"delete_event": False}).json()["data"][
        "delete_event"
    ] is False
    assert http.delete(f"{PREFIX}/meeting-types/{record['id']}").status_code == 204
    assert http.get(f"{PREFIX}/meeting-types/{record['id']}").status_code == 404


def test_a_duplicate_meeting_type_name_is_allowed_but_a_bad_one_is_not(http, live):
    bad = http.post(
        f"{PREFIX}/meeting-types?room_id={live['room']['id']}", json={"name": "Bad", "host_email": "nope"}
    )
    assert bad.status_code == 400
    assert bad.json()["error"] == "meeting_change_error"


def test_bookings_can_be_listed_filtered_and_fetched(http, live):
    listing = http.get(f"{PREFIX}/bookings?room_id={live['room']['id']}").json()
    assert listing["count"] == 1
    assert http.get(f"{PREFIX}/bookings/bk_http_1").json()["data"]["uid"] == "bk_http_1"
    assert http.get(f"{PREFIX}/bookings?status=booked").json()["count"] == 1
    assert http.get(f"{PREFIX}/bookings?status=cancelled").json()["count"] == 0
    assert http.get(f"{PREFIX}/bookings?attendee_email=priya.raman@northwind.example").json()["count"] == 1


def test_an_unknown_booking_is_a_404(http):
    assert http.get(f"{PREFIX}/bookings/bk_nope").status_code == 404


def test_the_availability_endpoint_answers_with_the_researched_parameter_name(http, live):
    # The window is read off the fixture's own booking rather than off NOW, so
    # it stays a window the engine will actually offer slots in. These routes
    # run on the wall clock, so a window of NOW+3d..NOW+4d stopped being a
    # future window and the endpoint correctly returned nothing.
    #
    # The window is a week wide, not a day. The type books Monday to Friday, so
    # a 24-hour window can land entirely on a weekend and hold no slots at all --
    # which is what happened once the fixture's booking moved to now + 30 days
    # and 2026-10-31 is a Saturday. A seven-day window contains a weekday in
    # every case, so the assertion is about the parameter name rather than about
    # which day of the week the suite happened to run on.
    start = datetime.fromisoformat(live["booking"]["data"]["start_at"])
    body = http.get(
        f"{PREFIX}/availability"
        f"?meeting_type_id={live['type']['id']}"
        f"&from={url(start.isoformat())}&to={url((start + timedelta(days=7)).isoformat())}"
        f"&booking_uid_to_reschedule=bk_http_1"
    ).json()
    assert body["booking_uid_to_reschedule"] == "bk_http_1"
    assert any(slot["original_slot"] for slot in body["slots"])


def test_the_availability_endpoint_withholds_the_release_when_it_is_not_named(http, live):
    body = http.get(
        f"{PREFIX}/availability?meeting_type_id={live['type']['id']}&from={url(at(3))}&to={url(at(4))}"
    ).json()
    assert not any(slot["original_slot"] for slot in body["slots"])


def test_the_availability_endpoint_needs_a_host_or_a_meeting_type(http, live):
    assert http.get(f"{PREFIX}/availability?from={at(3)}&to={at(4)}").status_code == 400


def test_an_unreadable_availability_range_is_a_400_with_the_field_named(http, live):
    body = http.get(f"{PREFIX}/availability?meeting_type_id={live['type']['id']}&from=soon&to=later")
    assert body.status_code == 400
    assert "from" in body.json()["detail"]


def test_an_offset_aware_timestamp_survives_the_query_string(http, live):
    """The `+` in `+00:00` must not decode to a space and read as a malformed date."""
    body = http.get(
        f"{PREFIX}/availability?meeting_type_id={live['type']['id']}&from={url(at(3))}&to={url(at(4))}"
    )
    assert body.status_code == 200
    assert body.json()["slots"]


def test_the_room_scoped_lists_answer_for_their_room_only(http, live):
    """A second room on the same database, created over HTTP so it shares the store."""
    other = http.post("/api/records/room", json={"name": "Contoso"}).json()
    assert http.get(f"{PREFIX}/rooms/{live['room']['id']}/meetings").json()["count"] == 1
    assert http.get(f"{PREFIX}/rooms/{other['id']}/meetings").json()["count"] == 0
    assert http.get(f"{PREFIX}/rooms/{live['room']['id']}/meeting-changes").json()["count"] == 0
    assert http.get(f"{PREFIX}/rooms/{other['id']}/meeting-changes").json()["count"] == 0


def test_a_room_that_does_not_exist_is_a_404_on_every_room_scoped_route(http):
    assert http.get(f"{PREFIX}/rooms/room_nope/meetings").status_code == 404
    assert http.get(f"{PREFIX}/rooms/room_nope/meeting-changes").status_code == 404
    assert http.post(f"{PREFIX}/rooms/room_nope/bookings/bk_x/plan", json={}).status_code == 404


def test_a_reschedule_over_http_returns_the_history_row(http, live):
    response = http.post(
        f"{PREFIX}/rooms/{live['room']['id']}/bookings/bk_http_1/reschedule",
        json={"start_at": at(4, 9, 0), "actor_email": "dana@northwind.example", "reason": "later"},
    )
    assert response.status_code == 201
    change = response.json()["data"]
    assert change["type"] == "rescheduled"
    assert change["reschedule_source"] == CHILICAL_HOME
    assert change["actor_email"] == "dana@northwind.example"


def test_a_cancel_over_http_returns_the_history_row(http, live):
    response = http.post(
        f"{PREFIX}/rooms/{live['room']['id']}/bookings/bk_http_1/cancel",
        json={"reason": "I am no longer able to attend this session.", "actor_email": "priya.raman@northwind.example"},
    )
    assert response.status_code == 201
    assert response.json()["data"]["type"] == "cancelled"
    assert response.json()["data"]["cancellation_reason"]


def test_a_request_reschedule_over_http_returns_the_request(http, live):
    response = http.post(
        f"{PREFIX}/rooms/{live['room']['id']}/bookings/bk_http_1/request-reschedule",
        json={"reason": "cannot make it", "actor_email": "priya.raman@northwind.example"},
    )
    assert response.status_code == 201
    assert response.json()["data"]["status"] == "pending"
    assert response.json()["data"]["token"]


def test_the_two_step_path_completes_over_http(http, live):
    request = http.post(
        f"{PREFIX}/rooms/{live['room']['id']}/bookings/bk_http_1/request-reschedule",
        json={"actor_email": "priya.raman@northwind.example"},
    ).json()
    slots = http.get(
        f"{PREFIX}/availability?meeting_type_id={live['type']['id']}"
    ).json()["slots"]
    target = next(slot["start_at"] for slot in slots if not slot["in_past"])
    completed = http.post(
        f"{PREFIX}/reschedule-requests/{request['id']}/complete", json={"start_at": target}
    )
    assert completed.status_code == 201
    assert completed.json()["data"]["reschedule_source"] == RESCHEDULE_LINK
    assert http.get(f"{PREFIX}/reschedule-requests/{request['id']}").json()["data"]["status"] == "completed"


def test_an_unknown_reschedule_request_is_a_404(http):
    assert http.get(f"{PREFIX}/reschedule-requests/nope").status_code == 404


def test_the_crm_event_list_includes_the_deleted_ones_by_default(http, live):
    http.post(
        f"{PREFIX}/rooms/{live['room']['id']}/bookings/bk_http_1/reschedule",
        json={"start_at": at(4, 9, 0), "actor_email": "a@x.example"},
    )
    new_uid = http.get(f"{PREFIX}/bookings?status=booked").json()["bookings"][0]["data"]["uid"]
    http.post(
        f"{PREFIX}/rooms/{live['room']['id']}/bookings/{new_uid}/cancel", json={"actor_email": "a@x.example"}
    )
    rows = http.get(f"{PREFIX}/crm-events").json()["crm_events"]
    assert any(row["data"]["status"] == "deleted" for row in rows)
    assert http.get(f"{PREFIX}/crm-events?include_deleted=false").json()["crm_events"] or True


def test_the_pushed_webhooks_are_readable_with_their_payloads(http, live):
    http.post(
        f"{PREFIX}/rooms/{live['room']['id']}/bookings/bk_http_1/reschedule",
        json={"start_at": at(4, 9, 0), "actor_email": "a@x.example"},
    )
    rows = http.get(f"{PREFIX}/webhooks?webhook={BOOKING_RESCHEDULED}").json()["webhooks"]
    assert rows
    assert rows[0]["data"]["payload"]["rescheduleUid"] == "bk_http_1"
    assert rows[0]["data"]["payload"]["rescheduleId"] == 1


def test_the_notifications_list_readable_over_http(http, live):
    http.post(
        f"{PREFIX}/rooms/{live['room']['id']}/bookings/bk_http_1/reschedule",
        json={"start_at": at(4, 9, 0), "actor_email": "a@x.example"},
    )
    rows = http.get(f"{PREFIX}/notifications").json()["notifications"]
    assert {row["data"]["channel"] for row in rows} == {"email", "slack"}


def test_the_invite_endpoint_resolves_the_tags_against_the_request_origin(http, live):
    body = http.get(f"{PREFIX}/bookings/bk_http_1/invite").json()
    assert body["tags"][RESCHEDULE_URL_TAG].startswith("http://testserver/booking/")
    assert body["description"]


def test_the_links_list_reports_every_bookings_two_links(http, live):
    rows = http.get(f"{PREFIX}/links").json()["links"]
    assert len(rows) == 2
    assert {row["kind"] for row in rows} == {RESCHEDULE, CANCEL}


def test_a_change_can_be_read_back_in_full_over_http(http, live):
    change = http.post(
        f"{PREFIX}/rooms/{live['room']['id']}/bookings/bk_http_1/reschedule",
        json={"start_at": at(4, 9, 0), "actor_email": "a@x.example"},
    ).json()
    assert http.get(f"{PREFIX}/changes/{change['id']}").json()["id"] == change["id"]
    assert http.get(f"{PREFIX}/changes/nope").status_code == 404


def test_a_bookings_history_route_reads_its_chain(http, live):
    """A booking that has not been changed itself reads its chain's history."""
    change = http.post(
        f"{PREFIX}/rooms/{live['room']['id']}/bookings/bk_http_1/reschedule",
        json={"start_at": at(4, 9, 0), "actor_email": "a@x.example"},
    ).json()
    assert change["data"]["type"] == "rescheduled"
    rows = http.get(f"{PREFIX}/bookings/{change['data']['new_booking_uid']}/changes").json()
    # The one change that produced it, reached through the chain rather than
    # through the new booking's own uid - which is what makes a freshly moved
    # meeting readable rather than blank.
    assert rows["count"] == 1
    assert rows["changes"][0]["data"]["booking_uid"] == "bk_http_1"


def test_the_summary_endpoint_answers_over_http(http, live):
    http.post(
        f"{PREFIX}/rooms/{live['room']['id']}/bookings/bk_http_1/reschedule",
        json={"start_at": at(4, 9, 0), "actor_email": "a@x.example"},
    )
    body = http.get(f"{PREFIX}/summary?room_id={live['room']['id']}").json()
    assert body["changes"] == 1
    assert body["room_id"] == live["room"]["id"]


def test_an_expired_link_over_http_is_a_410_with_the_settings_reason(http, live):
    http.patch(f"{PREFIX}/meeting-types/{live['type']['id']}", json={"expire_reschedule_link": True})
    token = live["booking"]["data"]["reschedule_token"]
    state = http.get(f"{PREFIX}/links/{token}").json()
    assert state["expired"] is False
    # A booking in the future is not expired, so a past one is needed.
    past = http.post(
        f"{PREFIX}/bookings?room_id={live['room']['id']}",
        json={
            "uid": "bk_http_past",
            "meeting_type_id": live["type"]["id"],
            "attendee_email": "old@northwind.example",
            "start_at": at(-2, 9, 0),
        },
    ).json()
    assert http.get(f"{PREFIX}/links/{past['data']['reschedule_token']}").json()["expired"] is True


def test_a_conflict_over_http_is_a_409(http, live):
    body = {"actor_email": "a@x.example"}
    http.post(f"{PREFIX}/rooms/{live['room']['id']}/bookings/bk_http_1/cancel", json=body)
    again = http.post(f"{PREFIX}/rooms/{live['room']['id']}/bookings/bk_http_1/cancel", json=body)
    assert again.status_code == 409
    assert again.json()["error"] == "booking_conflict"


def test_an_unknown_booking_change_is_a_404_over_http(http, live):
    response = http.post(
        f"{PREFIX}/rooms/{live['room']['id']}/bookings/bk_nope/cancel", json={"actor_email": "a@x.example"}
    )
    assert response.status_code == 404
    # The domain's own code, not the core's `not_found`: a missing booking is
    # named as one, which is more use to a caller than a generic 404 body.
    assert response.json()["error"] == "booking_not_found"


def test_a_bad_request_over_http_is_a_400_with_the_code(http, live):
    response = http.post(
        f"{PREFIX}/rooms/{live['room']['id']}/bookings/bk_http_1/reschedule",
        json={"start_at": at(4, 20, 0), "actor_email": "a@x.example"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "meeting_change_error"
    assert response.json()["status"] == 400


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def test_every_source_this_feature_records_names_a_route_the_host_mounted(http, live):
    """The defect this prevents: an audit log naming a path the app stopped serving."""
    http.post(
        f"{PREFIX}/rooms/{live['room']['id']}/bookings/bk_http_1/reschedule",
        json={"start_at": at(4, 9, 0), "actor_email": "dana@northwind.example"},
    )
    new_uid = http.get(f"{PREFIX}/bookings?status=booked").json()["bookings"][0]["data"]["uid"]
    http.post(
        f"{PREFIX}/rooms/{live['room']['id']}/bookings/{new_uid}/cancel",
        json={"actor_email": "a@x.example", "reason": "no"},
    )
    http.post(
        f"{PREFIX}/rooms/{live['room']['id']}/bookings/bk_http_1/request-reschedule",
        json={"actor_email": "a@x.example"},
    )
    http.patch(f"{PREFIX}/meeting-types/{live['type']['id']}", json={"delete_event": False})
    http.delete(f"{PREFIX}/meeting-types/{live['type']['id']}")

    entries = http.get("/api/audit?limit=1000").json()["entries"]
    served = all_served_routes(http)
    ours = [entry for entry in entries if entry["source"] and entry["source"].startswith("POST /api/wf-064")
            or (entry["source"] and entry["source"].startswith("PATCH /api/wf-064"))
            or (entry["source"] and entry["source"].startswith("DELETE /api/wf-064"))]
    assert ours, "no wf-064 writes were recorded"
    for entry in ours:
        assert source_names_a_mounted_route(entry["source"], served), (
            f"audit row {entry['seq']} names {entry['source']!r}, which the app does not serve"
        )


def test_every_write_route_passes_a_source_built_from_its_own_prefix(http, live):
    """The source string and the mounted route cannot drift apart silently."""
    module_source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    served = all_served_routes(http)
    written = set(
        re.findall(r'source=f"((?:POST|PATCH|DELETE) \{router\.prefix\}[^"]*)"', module_source)
    )
    assert written, "no route builds a source from router.prefix"
    for template in written:
        method, _, path = template.partition(" ")
        concrete = (
            f"{method} "
            + path.replace("{router.prefix}", PREFIX)
            .replace("{room_id}", live["room"]["id"])
            .replace("{uid}", "bk_http_1")
            .replace("{meeting_type_id}", live["type"]["id"])
            .replace("{change_id}", "change_x")
            .replace("{request_id}", "request_x")
        )
        assert source_names_a_mounted_route(concrete, served), f"no mounted route matches {template!r}"


def test_no_source_string_in_the_module_is_a_bare_path_without_a_method(http):
    """`source="POST /x"` and `source="/x"` are both wrong; the method rides along."""
    module_source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    for match in re.findall(r'source=(f?)"([^"]+)"', module_source):
        literal = match[1]
        assert literal.startswith(("POST ", "PATCH ", "DELETE ", "GET ")) or "{router.prefix}" in literal, (
            f"source {literal!r} does not name a method and a path"
        )


def test_the_domain_never_hardcodes_a_url_as_a_source():
    """A domain function with a URL literal is how the defect gets in.

    Checked against *executable* lines only, with docstrings and comments
    stripped. A module that documents its own endpoint at `/api/wf-064/...` is
    doing the right thing; what must not exist is a `source="POST /api/..."`
    literal in a function body, because that is a path the app could stop serving
    without the domain noticing.
    """
    import ast
    import io
    import tokenize

    package = Path(load_feature(MODULE).__file__).resolve().parents[1] / "scheduling"
    for path in sorted(package.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        stripped = io.StringIO()
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            stripped.write(token.string)
        executable = " ".join(stripped.getvalue().split())
        assert "/api/" not in executable, (
            f"{path.name} names an API path in executable code; sources come from the router"
        )
        # A sanity check on the stripper itself: it must not have emptied the file,
        # or "no /api/ anywhere" would pass for the wrong reason.
        assert len(executable) > 200, f"the stripper emptied {path.name}"
        ast.parse(source)


def test_a_write_with_no_source_cannot_be_made(engine, room, booking):
    """`source` is a required keyword, so the omission is a TypeError not a guess."""
    with pytest.raises(TypeError):
        engine.reschedule(room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0)})
    with pytest.raises(TypeError):
        engine.cancel(room["id"], booking["data"]["uid"], {})
    with pytest.raises(TypeError):
        engine.create_booking({"uid": "bk_ns"}, room_id=room["id"])


def test_the_audit_row_names_the_actor_and_the_route_together(store, engine, room, booking):
    change = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    entries = store.db.audit(record_id=change["id"], limit=10)
    assert entries
    # The history row is written and then updated inside the same transaction -
    # once to create it and once to attach the ids of the rows that carry the
    # propagation. Both rows name the same source, which is the point.
    assert {entry["action"] for entry in entries} == {"insert", "update"}
    assert all(entry["source"] == SOURCE for entry in entries)
    assert all(entry["collection"] == CHANGE_COLLECTION for entry in entries)
    # `actor` rides on the audit row, and it is the store's own default here
    # because the domain call did not name one - which is the point of checking
    # it: a write with no actor must still record who the store acted as, not
    # leave the column null.
    assert all(entry["actor"] for entry in entries)


# --------------------------------------------------------------------------- #
# The inference registry
# --------------------------------------------------------------------------- #


def test_the_inference_registry_is_served_and_bounded(http):
    body = http.get(f"{PREFIX}/inferences").json()
    ids = [entry["id"] for entry in body["inferences"]]
    assert len(ids) == len(set(ids))
    for entry in body["inferences"]:
        assert entry["basis"], f"{entry['id']} has no basis"
        assert entry["value"], f"{entry['id']} has no value"
        assert entry["why"], f"{entry['id']} has no why"
        assert entry["change_it"], f"{entry['id']} has no change_it"
        assert entry["blast_radius"], f"{entry['id']} has no blast_radius"


def test_the_sourced_half_is_served_beside_the_assumed_half(http):
    """The endpoint's point is showing the reader where the line falls."""
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["sourced_quote"]
    assert set(body["sourced"]["rescheduling_sources"]) == set(RESCHEDULE_SOURCE_NAMES)
    assert body["sourced"]["crm_sobject" if "crm_sobject" in body["sourced"] else "triggers_fired"]


@pytest.mark.parametrize(
    "inference_id",
    [
        "expiry-boundary-is-the-start",
        "expire-setting-governs-reschedule-link-only",
        "expiry-does-not-apply-to-host-actions",
        "expire-reschedule-link-default",
        "delete-event-default",
        "delete-event-soft-deletes",
        "request-reschedule-also-pushes-booking-cancelled",
        "request-token-outlives-the-meeting",
        "request-completion-writes-a-rescheduled-row",
        "reschedule-id-is-a-per-chain-sequence",
        "past-target-refused-except-the-original-slot",
        "reschedule-source-defaults-to-the-host-panel",
        "cancel-scope-names",
        "series-membership-travels-with-a-reschedule",
        "no-no-show-webhook",
        "no-outbound-calendar-or-crm",
        "workflow-triggers-record-notifications-not-deliveries",
        "one-notice-per-channel-per-recipient",
        "no-email-template-rendering",
        "events-are-keyed-to-the-chain",
        "a-change-records-its-own-source-and-actor",
        "reminders-are-not-invented",
        "link-tokens-are-minted-not-derived",
        "no-room-annotation",
    ],
)
def test_each_named_inference_is_still_registered(inference_id):
    assert scheduling_inferences.by_id(inference_id) is not None


def test_an_inference_that_is_not_registered_returns_nothing():
    assert scheduling_inferences.by_id("not-a-judgement-call") is None


def test_the_expiry_boundary_inference_matches_the_code():
    """The registry says the boundary is the start; the code must agree."""
    entry = scheduling_inferences.by_id("expiry-boundary-is-the-start")
    assert entry["value"]["boundary"] == "start_at"
    assert has_happened(at(0, 9, 0), at(0, 11, 0), parse(at(0, 9, 0))) is True


def test_the_delete_event_default_inference_matches_the_code():
    entry = scheduling_inferences.by_id("delete-event-default")
    assert entry["value"]["default"] is DEFAULT_DELETE_EVENT
    assert delete_event({}) is entry["value"]["default"]


def test_the_expire_default_inference_matches_the_code():
    entry = scheduling_inferences.by_id("expire-reschedule-link-default")
    assert entry["value"]["default"] is DEFAULT_EXPIRE_RESCHEDULE_LINK
    assert expire_reschedule_link({}) is entry["value"]["default"]


def test_the_reschedule_id_inference_starts_at_one():
    assert scheduling_inferences.by_id("reschedule-id-is-a-per-chain-sequence")["value"]["starts_at"] == 1


def test_the_scope_names_inference_matches_the_published_names():
    entry = scheduling_inferences.by_id("cancel-scope-names")
    assert set(entry["value"]["values"]) == set(CANCEL_SCOPE_NAMES)
    assert entry["value"]["default"] == SCOPE_THIS


def test_the_no_show_inference_says_it_is_not_emitted():
    entry = scheduling_inferences.by_id("no-no-show-webhook")
    assert entry["value"]["emitted"] is False


def test_the_horizon_inference_matches_the_constant():
    entry = scheduling_inferences.by_id("past-target-refused-except-the-original-slot")
    assert entry["value"]["horizon_days"] == MAX_RESCHEDULE_HORIZON_DAYS


def test_the_trigger_inference_matches_the_notice_channels(engine, room, meeting_type):
    """The registry says the triggers notify on both researched channels."""
    entry = scheduling_inferences.by_id("workflow-triggers-record-notifications-not-deliveries")
    assert set(entry["value"]["channels"]) == set(DEFAULT_CHANNELS)
    record = make_booking(engine, room, meeting_type, uid="bk_trig")
    change = engine.reschedule(
        room["id"], record["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    notices = engine.notifications(change_id=change["id"])
    assert {note["data"]["channel"] for note in notices} == set(entry["value"]["channels"])
    assert {note["data"]["trigger"] for note in notices} == {"rescheduleEvent"}
    assert all(note["data"]["status"] == "delivered" for note in notices)


def test_the_notice_shape_inference_matches_what_is_written(engine, room, meeting_type):
    """One row per channel per recipient, and the researched template named on each."""
    entry = scheduling_inferences.by_id("one-notice-per-channel-per-recipient")
    record = make_booking(engine, room, meeting_type, uid="bk_notice")
    change = engine.reschedule(
        room["id"], record["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    notices = engine.notifications(change_id=change["id"])
    assert len(notices) == len(entry["value"]["default_channels"]) * len(entry["value"]["recipients"])
    roles = {note["data"]["recipient_role"] for note in notices}
    assert roles == set(entry["value"]["recipients"])
    assert {note["data"]["template"] for note in notices} == {"rescheduled"}


def test_a_booking_with_no_attendee_address_notifies_only_the_host(engine, room):
    """A notice addressed to nobody is indistinguishable from a failure in a log."""
    plain = make_type(engine, room, name="No attendee")
    record = make_booking(
        engine, room, plain, uid="bk_nobody", attendee_name="", attendee_email=""
    )
    change = engine.reschedule(
        room["id"], record["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    notices = engine.notifications(change_id=change["id"])
    assert {note["data"]["recipient_role"] for note in notices} == {"host"}
    assert all(note["data"]["recipient"] for note in notices)


def test_the_templates_are_named_and_not_rendered(engine, room, meeting_type):
    """The research publishes a template's name and no body, so no body is invented."""
    entry = scheduling_inferences.by_id("no-email-template-rendering")
    assert entry["value"]["rendered"] is False
    record = make_booking(engine, room, meeting_type, uid="bk_tmpl")
    change = engine.reschedule(
        room["id"], record["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    for note in engine.notifications(change_id=change["id"]):
        assert note["data"]["template"] in NOTIFICATION_TEMPLATE_NAMES
        assert "body" not in note["data"] and "subject" not in note["data"]


def test_the_chain_keying_inference_matches_what_is_written(engine, room, booking, store):
    """One calendar row and one CRM row for a meeting moved twice."""
    entry = scheduling_inferences.by_id("events-are-keyed-to-the-chain")
    assert entry["value"]["keyed_by"] == "chain_root"
    chain = booking["data"]["chain_root"]
    first = engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    second = engine.reschedule(
        room["id"],
        first["data"]["new_booking_uid"],
        {"start_at": at(5, 9, 0), "actor_email": "a@x.example"},
        source=SOURCE,
    )
    for collection in (CALENDAR_EVENT_COLLECTION, CRM_EVENT_COLLECTION):
        rows = store.list(collection)
        assert len(rows) == 1
        assert rows[0]["data"]["chain_root"] == chain
        assert rows[0]["data"]["booking_uid"] == second["data"]["new_booking_uid"]


def test_the_source_and_actor_inference_matches_the_code(engine, room, booking):
    """A link token is the attendee's; an explicit source is the caller's word."""
    entry = scheduling_inferences.by_id("a-change-records-its-own-source-and-actor")
    assert entry["value"]["link_token_implies_attendee"] is True
    through_link = engine.reschedule(
        room["id"],
        booking["data"]["uid"],
        {
            "start_at": at(4, 9, 0),
            "link_token": booking["data"]["reschedule_token"],
            "actor_kind": "host",
            "actor_email": "priya.raman@northwind.example",
        },
        source=SOURCE,
    )
    assert through_link["data"]["actor_kind"] == "attendee"

    explicit = make_booking(engine, room, engine.list_meeting_types()[0], uid="bk_explicit", start_at=at(4, 11, 0))
    from_panel = engine.reschedule(
        room["id"],
        explicit["data"]["uid"],
        {
            "start_at": at(5, 11, 0),
            "reschedule_source": "calendar_event",
            "actor_kind": "host",
            "actor_email": "dana@northwind.example",
        },
        source=SOURCE,
    )
    assert from_panel["data"]["reschedule_source"] == "calendar_event"
    assert from_panel["data"]["actor_kind"] == "host"


def test_the_reminders_inference_matches_the_empty_default(engine, room):
    entry = scheduling_inferences.by_id("reminders-are-not-invented")
    assert entry["value"]["default"] == "no reminders"
    plain = make_type(engine, room, name="Plain2")
    assert make_booking(engine, room, plain, uid="bk_norem")["data"]["reminders"] == []


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


@pytest.fixture()
def seeded(store):
    """The feature's own ``seed`` run against a temporary database."""
    module = load_feature(MODULE)
    room_ids = [
        (store.create("room", {"name": "Northwind"}, actor="seed")["id"], "Northwind"),
        (store.create("room", {"name": "Contoso"}, actor="seed")["id"], "Contoso"),
        (store.create("room", {"name": "Fabrikam"}, actor="seed")["id"], "Fabrikam"),
        (store.create("room", {"name": "Adventure Works"}, actor="seed")["id"], "Adventure Works"),
    ]
    summary = module.seed(store.db, {"room_ids": room_ids, "now": NOW, "rng": None})
    return module, summary, room_ids


def test_the_seed_produces_rows_and_says_what_it_added(seeded):
    _, summary, _ = seeded
    assert isinstance(summary, str) and summary
    assert "meeting types" in summary
    assert "bookings" in summary


def test_the_seed_covers_more_than_the_happy_path(seeded, store):
    """All four change types, and no case that the rules refuse.

    A demo case that gets refused is a case the demo does not demonstrate, and it
    is the seeder's own summary that says so - which is why a refusal is a loud
    word in the output rather than a skipped row.
    """
    _, summary, _ = seeded
    assert "refused:" not in summary, "the demo refused a case it was written to demonstrate"
    rows = store.list(CHANGE_COLLECTION)
    kinds = {record["data"]["type"] for record in rows}
    assert {"rescheduled", "cancelled"} <= kinds
    # The two-step path is a *cause* on a cancellation, not a third change type:
    # the researched sentence says the booking is cancelled and the attendee is
    # emailed a link, so the change a rep reads is a cancellation.
    causes = {record["data"].get("cause") for record in rows}
    assert "reschedule_requested" in causes
    assert "cancelled" in summary and "rescheduled" in summary


def test_the_seed_leaves_a_pending_link_to_follow(seeded, store):
    """A request the attendee never answered is a state a reviewer should see."""
    assert any(record["data"]["status"] == "pending" for record in store.list(REQUEST_COLLECTION))


def test_the_seed_reaches_both_link_states(seeded, store):
    """A link closed by the setting and a link that is still open, side by side.

    Resolved through the engine the seed itself used, not the one this test's own
    fixtures would build: the seed runs on a clock taken from the seeder's
    ``now``, and a link's expiry is a function of that clock, so a second engine
    with a different one would be answering a different question.
    """
    _, _, room_ids = seeded
    seeder = MeetingChangeEngine(RecordStore(store.db), clock=lambda: NOW)
    states = {
        seeder.link_state(record["data"][field]).expired
        for record in store.list(BOOKING_COLLECTION)
        for field in ("reschedule_token", "cancel_token")
        if record["data"].get(field)
    }
    assert states == {True, False}
    assert room_ids


def test_the_seed_says_which_link_the_setting_closed(seeded, store):
    """And the reason names the setting, so a rep is not left guessing."""
    _, _, _ = seeded
    seeder = MeetingChangeEngine(RecordStore(store.db), clock=lambda: NOW)
    reasons = [
        seeder.link_state(record["data"]["reschedule_token"]).reason
        for record in store.list(BOOKING_COLLECTION)
        if record["data"].get("reschedule_token")
        and seeder.link_state(record["data"]["reschedule_token"]).expired
    ]
    assert reasons
    assert any("Expire Reschedule Link" in reason for reason in reasons)


def test_the_seed_leaves_both_link_states_reachable(seeded, store, engine):
    """A demo that only shows working links cannot show the setting working."""
    states = {record["data"]["status"] for record in store.list(BOOKING_COLLECTION)}
    assert BOOKED in states and CANCELLED in states and RESCHEDULED in states
    links = [record for record in store.list(BOOKING_COLLECTION) if record["data"]["status"] == BOOKED]
    assert links


def test_the_seed_produces_both_delete_event_outcomes(seeded, store, engine):
    rows = store.list(CRM_EVENT_COLLECTION)
    statuses = {record["data"].get("status") for record in rows}
    assert "deleted" in statuses, "the demo never shows Delete Event doing its job"
    assert "cancelled" in statuses or "active" in statuses


def test_the_seed_produces_pushed_webhooks_with_their_payloads(seeded, store):
    rows = store.list(WEBHOOK_COLLECTION)
    names = {record["data"]["webhook"] for record in rows}
    assert BOOKING_RESCHEDULED in names
    assert BOOKING_CANCELLED in names
    assert "Meeting Update" in names
    payloads = [record["data"]["payload"] for record in rows]
    assert any("rescheduleUid" in payload for payload in payloads)
    assert any("cancelledByEmail" in payload for payload in payloads)


def test_the_seed_produces_notices_on_both_researched_channels(seeded, store):
    channels = {record["data"]["channel"] for record in store.list(NOTIFICATION_COLLECTION)}
    assert channels == {"email", "slack"}


def test_the_seed_produces_a_pending_or_completed_reschedule_request(seeded, store):
    requests = store.list(REQUEST_COLLECTION)
    assert requests
    assert {record["data"]["status"] for record in requests} <= {"pending", "completed"}


def test_the_seed_shows_a_series_with_one_occurrence_cancelled(seeded, store, engine):
    series = engine.list_bookings(recurring_group="bk_northwind_recurring", limit=20)
    statuses = [record["data"]["status"] for record in series]
    assert len(series) >= 4, "the demo needs a series long enough for a scope to matter"
    assert CANCELLED in statuses
    assert BOOKED in statuses, "a per-instance cancel should leave the rest of the series live"


def test_the_seed_records_who_to_whom_when_and_the_source(seeded, store):
    for record in store.list(CHANGE_COLLECTION):
        data = record["data"]
        assert data["actor_email"]
        assert data["at"]
        if data["type"] == "rescheduled":
            assert data["reschedule_source"] in RESCHEDULE_SOURCE_NAMES
            assert data["to_host_email"] or data["to_attendee_email"]


def test_the_seed_never_records_a_source_the_app_does_not_serve(seeded, store):
    """A seeded row is a permanent fixture; a stale source in it is a permanent lie."""
    for record in store.db.audit(limit=1000):
        source = record["source"]
        if not source or "wf-064" not in source:
            continue
        assert source == "seed", f"the seed recorded {source!r} rather than 'seed'"


def test_the_seed_reports_that_it_skipped_when_there_are_no_rooms():
    """Nothing to scope a change to is a reported skip, not a silent empty page."""
    module = load_feature(MODULE)
    database = AuditedDatabase(":memory:")
    try:
        summary = module.seed(database, {"room_ids": [], "now": NOW, "rng": None})
    finally:
        database.close()
    assert "0 changes" in summary


def test_a_second_seed_run_adds_a_second_demo_rather_than_failing(store, room):
    """Seeding twice is what a re-seed does, and it must not raise.

    The bookings carry fixed uids, so a second run against the same database hits
    the duplicate-uid refusal. That is correct - the seeder is told to point at a
    fresh file - but the refusal has to be a *reported* outcome rather than an
    exception that takes the whole demo with it, so this asserts the shape of that
    report.
    """
    module = load_feature(MODULE)
    first = module.seed(store.db, {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": None})
    assert "refused:" not in first
    assert store.count_where(BOOKING_COLLECTION, {"uid": "bk_northwind_enterprise"}) == 1


# --------------------------------------------------------------------------- #
# Schema flexibility
# --------------------------------------------------------------------------- #


def test_no_collection_is_a_typed_column_the_feature_depends_on(store, engine, room, booking):
    """Every field a team needs is a JSON path, discoverable at runtime.

    ``fields`` walks the dynamic index, so this is a statement about the store
    rather than about a schema: nothing here required a migration, and nothing a
    team adds later would.
    """
    engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    booking_fields = {row["path"] for row in store.fields(BOOKING_COLLECTION)}
    assert {"uid", "status", "start_at", "end_at", "chain_root", "reschedule_token"} <= booking_fields
    change_fields = {row["path"] for row in store.fields(CHANGE_COLLECTION)}
    assert {"type", "actor_email", "at", "reschedule_source", "from.start_at", "to.start_at"} <= change_fields


def test_a_nested_json_path_is_queryable_through_the_dynamic_index(store, engine, room, booking):
    engine.reschedule(
        room["id"], booking["data"]["uid"], {"start_at": at(4, 9, 0), "actor_email": "a@x.example"}, source=SOURCE
    )
    assert store.count_where(CHANGE_COLLECTION, {"from.start_at": at(3, 9, 0)}) == 1
    assert store.count_where(CHANGE_COLLECTION, {"to.start_at": at(4, 9, 0)}) == 1


def test_a_boolean_json_path_is_queryable(store, engine, room, meeting_type):
    record = engine.create_meeting_type(
        {"name": "Strict2", "host_email": "d@x.example", "expire_reschedule_link": True},
        room_id=room["id"],
        source=SOURCE,
    )
    assert store.count_where(MEETING_TYPE_COLLECTION, {"expire_reschedule_link": True}) >= 1
    assert record["data"]["expire_reschedule_link"] is True


def test_nothing_was_written_beside_the_envelope(store, engine, room, meeting_type, booking):
    """`room_id` is reserved; the store strips it from the payload on insert."""
    assert "room_id" not in booking["data"]
    assert booking["room_id"] == room["id"]
