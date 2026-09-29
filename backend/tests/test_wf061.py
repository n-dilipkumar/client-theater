"""Tests for WF-061: send conditional pre- and post-meeting reminders and SMS nudges.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-061.md``, which cites Chili
Piper's *Reminders and Messages* article and Cal.com's *Create a workflow* API
reference. Nothing here is a preference of this build unless it is listed in
:mod:`dsr.meeting_reminders.inferences`, and every inference in that registry has
a test that checks it is still named, still bounded and still changeable.

The researched half
-------------------

* The three **Reminder Types** and the three **firing conditions** of step 4,
  with a minutes/hours/days/weeks offset.
* The **response gate**: "only if the Primary Guest has not responded to the
  invite (**Accepted or Declined**)" - so declining is responding, and a guest
  who said no must not be chased.
* The two **advanced gates** of step 6: *Send only if meeting starts on*, and
  *Send if the meeting was booked more than selected timeframe* - whose own
  worked example is "booked one week in advance".
* The three **statuses** and the five **skip reasons**, quoted in the research's
  ``automations`` field, each one reachable.
* The **organisational preconditions**: a connected Twilio account, an
  organisation-owned one for reply forwarding, and a Phone field in the guest
  form - plus Cal's ``attendee.phoneNumber`` "becomes required when SMS reminders
  are enabled for the event type".
* The **reusability** rule: reminders are "reusable assets attachable to many
  Meeting Types", with ``Remove from Meeting Type`` and ``Delete`` as two
  different actions.
* ``user_flow`` step 8: the same logic as a Cal.com **Workflow**, with its
  ``cal-api-version`` header, its trigger enum, its offset units, its step
  actions and its step templates.
* The **dynamic tags**, in both spellings the research uses, and its note that
  the set is open ("dynamic tags *such as*").

Three bugs these tests were written to catch, each of which happened during the
build and would not have been visible without a behavioural test:

* Two separate substitution passes made ``{CP.Guest.FirstName}`` render as the
  literal text ``{Priya}`` and then report the guest's own first name as a
  missing token - because a substituted value was re-read by the next pass.
* ``%-I:%M %p`` is a glibc extension ``strftime`` rejects outright on Windows,
  so Cal's ``{START_TIME_h:mma}`` token raised on this platform and would have
  raised in production too.
* Passing a run's clock as the *planning* clock made every reminder that fired
  report "Reminder schedule time in the past", because by definition a reminder
  that has fired has a fire time behind it.

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
from dsr.meeting_reminders import (
    ATTACHMENT_COLLECTION,
    BOOKING_COLLECTION,
    DELIVERY_COLLECTION,
    MEETING_TYPE_COLLECTION,
    REMINDER_COLLECTION,
    REPLY_COLLECTION,
    ReminderEngine,
    ReminderError,
    cal,
    conditions,
    tags,
    vocabulary,
)
from dsr.meeting_reminders import inferences as reminder_inferences
from dsr.meeting_reminders.errors import ConfigurationRefused
from dsr.store import RecordStore

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-061"

#: What the pure-domain tests pass as ``source``. Deliberately the exact shape a
#: route passes, so a test asserting on an audit row is asserting on the real
#: thing rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/deliver"

MODULE = "wf061_send_conditional_pre_and_post_meeting_"
FEATURE_ID = "wf-061-send-conditional-pre-and-post-meeting-"

#: A Monday, so the weekday cases are legible and the weekend case is real.
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def db(tmp_path):
    database = AuditedDatabase(tmp_path / "reminders.db", mirror_dir=tmp_path / "mirror")
    yield database
    database.close()


@pytest.fixture()
def store(db):
    return RecordStore(db)


@pytest.fixture()
def engine(store):
    return ReminderEngine(store, clock=lambda: NOW)


@pytest.fixture()
def configured(engine):
    """An engine whose organisation has finished the researched setup.

    Both Twilio flags on, and a no-reply domain, because a test that wanted the
    preconditions to bite would have to unset them - and every other test here
    is about something else.
    """
    engine.save_org(
        {
            "noreply_domain": "no-reply.contoso.example",
            "number": "+15550100",
            "localNumber": "+35315550100",
            "connected": True,
            "own_account": True,
        },
        actor="dana",
        source=SOURCE,
    )
    return engine


@pytest.fixture()
def room(store):
    return store.create("room", {"name": "Northwind — Enterprise Evaluation", "account": "Northwind"}, actor="dana")


def org_view(engine) -> dict:
    return engine.org_settings()


def reminder(**overrides) -> dict:
    """A minimal valid email reminder, with the researched defaults filled in."""
    base = {
        "name": "24 hours before",
        "channel": vocabulary.EMAIL,
        "condition": vocabulary.BEFORE,
        "offset": {"value": 24, "unit": vocabulary.HOURS},
        "emailTo": vocabulary.PRIMARY_GUEST,
        "emailFrom": vocabulary.HOST_ADDRESS,
        "repliesTo": vocabulary.REPLY_TO_HOST,
        "subject": "Your evaluation on {CP.Meeting.Date}",
        "body": "Hi {CP.Guest.FirstName}",
    }
    base.update(overrides)
    return base


def sms_reminder(**overrides) -> dict:
    base = {
        "name": "Text an hour before",
        "channel": vocabulary.SMS,
        "condition": vocabulary.BEFORE,
        "offset": {"value": 1, "unit": vocabulary.HOURS},
        "smsFrom": vocabulary.LOCAL_AREA_NUMBER,
        "repliesTo": vocabulary.REPLY_TO_HOST,
        "subject": "Reminder",
        "body": "Your call is at {CP.Meeting.Time}.",
    }
    base.update(overrides)
    return base


BOOKING_DEFAULTS: dict = {
    "title": "Northwind — Enterprise Evaluation",
    # A Monday two weeks out, so no weekday gate is in play unless one is asked for.
    "start": "2026-10-19T14:00:00+00:00",
    "durationMinutes": 45,
    "bookedAt": "2026-09-20T09:00:00+00:00",
    "timezone": "Europe/Dublin",
    "timezoneOffsetMinutes": 60,
    "location": "Zoom",
    "meetingUrl": "https://meet.example/northwind",
    "rescheduleUrl": "https://meet.example/northwind/reschedule",
    "cancelUrl": "https://meet.example/northwind/cancel",
    "primaryGuest": {
        "firstName": "Priya",
        "name": "Priya Raman",
        "email": "priya.raman@northwind.example",
        "phone": "+15550100",
        "responseStatus": vocabulary.RESPONSE_ACCEPTED,
    },
    "guests": [
        {
            "firstName": "Priya",
            "name": "Priya Raman",
            "email": "priya.raman@northwind.example",
            "phone": "+15550100",
            "responseStatus": vocabulary.RESPONSE_ACCEPTED,
        },
        {
            "firstName": "Marcus",
            "name": "Marcus Webb",
            "email": "marcus.webb@northwind.example",
            "phone": "+15550101",
            "responseStatus": vocabulary.RESPONSE_ACCEPTED,
        },
    ],
    "host": {"firstName": "Dana", "name": "Dana Okoro", "email": "dana@contoso.example"},
    "booker": {"firstName": "Wen", "name": "Wen Li", "email": "wen.li@contoso.example"},
    "assignees": [{"name": "Alba Ries", "email": "alba.ries@contoso.example"}],
}


def booking(**overrides) -> dict:
    """A booking with the researched fields, overridable per case."""
    body = {**BOOKING_DEFAULTS}
    for key, value in overrides.items():
        if key in ("primaryGuest", "host", "booker"):
            body[key] = {**body[key], **value}
        elif key == "guests":
            body["guests"] = value
        else:
            body[key] = value
    return body


def at(offset_hours: float) -> datetime:
    return NOW + timedelta(hours=offset_hours)


@pytest.fixture()
def http(monkeypatch, tmp_path):
    """A client over a temporary database.

    ``get_reminders`` is a FastAPI dependency, so the real routes already build
    the engine from ``StoreDep`` and the suite needs no override: the engine
    holds nothing beyond the store, so the production path and the test path are
    the same path.
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

    Read from the OpenAPI schema rather than from ``app.routes``: this FastAPI
    version wraps an included router in a container object that does not expose
    ``path``/``methods``, so iterating ``app.routes`` silently sees only the core
    routes and would make the check below pass for the wrong reason - it would
    find no feature route to compare against and either fail spuriously or, worse,
    never exercise the comparison at all.

    The audit log is shared, so this has to include core routes too.
    """
    routes: set[tuple[str, str]] = set()
    for path, operations in client.get("/openapi.json").json()["paths"].items():
        for method in operations:
            if method.upper() in ("HEAD", "OPTIONS"):
                continue
            routes.add((method.upper(), path))
    return routes


def mounted_paths(client, feature_id=FEATURE_ID):
    """Every path the host mounted for one feature, from the OpenAPI schema."""
    return {path for _, path in mounted_routes(client, feature_id)}


def client_store(client):
    return client.app.state.store.db


def source_names_a_mounted_route(source: str, routes) -> bool:
    """Whether an audit ``source`` names a route the app really serves.

    The pattern is built from the **route**, not from the source, and that
    direction is the whole thing. A mounted route carries ``{room_id}`` while the
    source that named it carries ``room_abc123``, so a source-derived pattern
    would only ever match another source. Turning each route's parameter into a
    single-segment wildcard and matching the source against it is the way round
    that actually compares a concrete request to the route that served it.

    The wildcard also accepts a literal ``{param}``, so a feature that recorded
    the route *template* rather than the concrete path still passes - which is
    deliberate, because both spellings name a route the app serves.
    """
    match = re.match(r"^[A-Z]+\s+(\S+)$", source)
    if not match:
        return False
    path = match.group(1)
    for _, route_path in routes:
        # Re-joined with "/", which the split consumed: joining with "" silently
        # deletes every separator and the pattern then matches nothing.
        pattern = "/".join(
            _SEGMENT_WITH_PARAMETER if segment.startswith("{") else re.escape(segment)
            for segment in _split_segments(route_path)
        )
        try:
            compiled = re.compile(f"^{pattern}$")
        except re.error:
            continue
        if compiled.match(path):
            return True
    return False


#: One path segment: either the template's own ``{name}`` or any single segment.
_SEGMENT_WITH_PARAMETER = r"(?:\{[^/}]+\}|[^/]+)"


def _split_segments(route_path: str) -> list[str]:
    """A path split on ``/``, keeping each parameter as its own segment.

    Splitting first is what keeps a ``{param}`` from being escaped as a regex
    quantifier, which is the bug the naive ``re.escape``-then-substitute version
    of this check had.
    """
    return [segment for segment in route_path.split("/")]


# --------------------------------------------------------------------------- #
# The researched vocabulary
# --------------------------------------------------------------------------- #


def test_the_three_researched_channels_and_conditions_are_published():
    """step 2 names Email or SMS; step 4 names three firing conditions."""
    assert vocabulary.CHANNELS == ("email", "sms")
    assert vocabulary.CONDITIONS == (
        "before_meeting",
        "before_meeting_if_no_response",
        "after_meeting",
    )


def test_the_four_offset_units_are_the_researched_ones():
    """step 4: "a minutes/hours/days/weeks offset"."""
    assert vocabulary.UNITS == ("minutes", "hours", "days", "weeks")
    assert vocabulary.UNIT_MINUTES == {"minutes": 1, "hours": 60, "days": 1440, "weeks": 10080}


def test_the_three_statuses_and_five_skip_reasons_are_the_documented_ones():
    """``automations``: Scheduled / Sent / Skipped, and five named reasons."""
    assert vocabulary.STATUSES == ("scheduled", "sent", "skipped")
    assert set(vocabulary.SKIP_REASONS) == {
        "schedule_in_past",
        "condition_not_satisfied",
        "recipient_not_found",
        "phone_not_found",
        "violated_restriction",
    }
    # The wording is the contract: it is what an administrator reads and what a
    # support conversation quotes, so it is asserted verbatim.
    assert vocabulary.SKIP_REASONS["schedule_in_past"] == "Reminder schedule time in the past"
    assert vocabulary.SKIP_REASONS["condition_not_satisfied"] == "Reminder condition not satisfied"
    assert vocabulary.SKIP_REASONS["recipient_not_found"] == "Recipient not found"
    assert vocabulary.SKIP_REASONS["phone_not_found"] == "Phone not found"
    assert vocabulary.SKIP_REASONS["violated_restriction"] == "Violated restriction"


def test_the_five_skip_reasons_are_all_produced_by_the_demo(tmp_path):
    """A demo that cannot reach a documented reason is hiding a rule.

    Checked against the seed's own output rather than a hand-written list, so
    this fails the moment a reorganisation of the seed quietly stops producing
    one of the five.
    """
    module = load_feature(MODULE)
    database = AuditedDatabase(tmp_path / "seed.db", mirror_dir=tmp_path / "mirror")
    try:
        store = RecordStore(database)
        rooms = [
            (store.create("room", {"name": name}, actor="seed")["id"], name)
            for name in ("Northwind", "Contoso", "Fabrikam", "Alba")
        ]
        module.seed(database, {"room_ids": rooms, "now": NOW, "rng": None})
        rows = store.list(DELIVERY_COLLECTION, limit=1000)
        reached = {str(row["data"].get("reason")) for row in rows if row["data"].get("reason")}
        statuses = {str(row["data"].get("status")) for row in rows}
    finally:
        database.close()
    assert set(vocabulary.SKIP_REASONS) <= reached, f"the seed never produced {set(vocabulary.SKIP_REASONS) - reached}"
    assert set(vocabulary.STATUSES) <= statuses


def test_the_delivery_configuration_choices_are_the_researched_ones():
    """step 3: Send Email To, Send Email From, Send Replies To, Send SMS From."""
    assert vocabulary.EMAIL_TO == ("primary_guest", "all_guests")
    assert vocabulary.EMAIL_FROM == ("host", "booker", "noreply")
    assert vocabulary.REPLIES_TO == ("host", "booker", "assignees")
    assert vocabulary.SMS_FROM == ("any_number", "local_area_number")


def test_the_three_response_statuses_are_the_calendar_invites_ones():
    """``data_sources``: "calendar invite responseStatus (accepted/declined/needsAction)"."""
    assert set(vocabulary.RESPONSE_STATUSES) == {"accepted", "declined", "needsAction"}
    # Both accepted and declined are *responses*, per the researched rule.
    assert vocabulary.RESPONSED_STATUSES == {"accepted", "declined"}


def test_the_two_advanced_gates_are_the_ones_step_6_names():
    assert vocabulary.RESEARCHED_RULES == ("weekday", "lead_time")
    assert vocabulary.RESTRICTIONS == ("none", "weekday", "lead_time")


def test_match_modes_come_from_the_extensibility_note():
    """``extensibility``: "condition groups with matches: all|any|none"."""
    assert vocabulary.MATCH_MODES == ("all", "any", "none")


def test_every_published_term_carries_its_own_justification():
    """A vocabulary a reviewer cannot check against the research is a guess list."""
    published = vocabulary.published_vocabulary()
    for key in ("condition_detail", "email_to_roles", "email_from_roles", "replies_to_roles", "sms_from_roles"):
        assert published[key], f"{key} is empty"


# --------------------------------------------------------------------------- #
# Fire times
# --------------------------------------------------------------------------- #


def test_a_before_reminder_fires_an_offset_before_the_start():
    when = conditions.fire_at(reminder(offset={"value": 24, "unit": "hours"}), booking())
    assert when == datetime(2026, 10, 18, 14, 0, tzinfo=timezone.utc)


def test_every_offset_unit_is_honoured():
    book = booking()
    for value, unit, minutes in [
        (30, "minutes", 30),
        (2, "hours", 120),
        (3, "days", 4320),
        (1, "weeks", 10080),
    ]:
        when = conditions.fire_at(reminder(offset={"value": value, "unit": unit}), book)
        assert when == datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc) - timedelta(minutes=minutes)


def test_an_after_meeting_reminder_fires_from_the_meetings_end_not_its_start():
    """The researched use is a follow-up email, and duration is a researched field."""
    when = conditions.fire_at(
        reminder(condition=vocabulary.AFTER, offset={"value": 1, "unit": "hours"}), booking()
    )
    assert when == datetime(2026, 10, 19, 15, 45, tzinfo=timezone.utc)  # start + 45m + 1h


def test_an_after_meeting_reminder_with_no_duration_anchors_on_the_start():
    """A booking that forgot its duration must not silently skip the meeting."""
    book = booking(durationMinutes=None)
    when = conditions.fire_at(
        reminder(condition=vocabulary.AFTER, offset={"value": 1, "unit": "hours"}), book
    )
    assert when == datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)


def test_a_booking_with_no_usable_start_has_no_fire_time():
    assert conditions.fire_at(reminder(), booking(start="not a date")) is None


def test_an_offset_of_zero_or_negative_is_refused():
    for bad in (0, -1):
        with pytest.raises(ReminderError):
            vocabulary.require_offset(bad, "hours")


def test_an_absurd_offset_is_refused():
    """A bound on the arithmetic, not a business rule - but a datetime is finite."""
    with pytest.raises(ReminderError, match="five years"):
        vocabulary.require_offset(10**6, "days")


def test_an_unknown_offset_unit_is_refused_naming_the_published_set():
    with pytest.raises(ReminderError, match="fortnight"):
        vocabulary.require_offset(1, "fortnight")


# --------------------------------------------------------------------------- #
# The response gate - the researched rule this ticket is named for
# --------------------------------------------------------------------------- #


def test_the_conditional_reminder_fires_when_the_primary_guest_has_not_responded():
    book = booking(primaryGuest={"responseStatus": vocabulary.RESPONSE_NEEDS_ACTION})
    decision = conditions.evaluate(
        reminder(condition=vocabulary.BEFORE_IF_NO_RESPONSE, offset={"value": 2, "unit": "hours"}),
        book,
        now=datetime(2026, 10, 19, 12, 30, tzinfo=timezone.utc),
    )
    assert decision.status == vocabulary.SENT


def test_the_conditional_reminder_is_refused_when_the_guest_has_responded():
    book = booking(primaryGuest={"responseStatus": vocabulary.RESPONSE_ACCEPTED})
    decision = conditions.evaluate(
        reminder(condition=vocabulary.BEFORE_IF_NO_RESPONSE, offset={"value": 2, "unit": "hours"}),
        book,
        now=datetime(2026, 10, 19, 12, 30, tzinfo=timezone.utc),
    )
    assert decision.status == vocabulary.SKIPPED
    assert decision.reason == vocabulary.CONDITION_NOT_SATISFIED
    assert decision.reason_text == "Reminder condition not satisfied"


def test_a_declined_guest_has_responded_and_is_not_chased():
    """The researched parenthetical is "(Accepted or Declined)".

    This is the sharpest rule in the research and the easiest to get wrong: a
    "you have not responded" email to someone who declined is the single worst
    thing this feature could do.
    """
    book = booking(primaryGuest={"responseStatus": vocabulary.RESPONSE_DECLINED})
    decision = conditions.evaluate(
        reminder(condition=vocabulary.BEFORE_IF_NO_RESPONSE, offset={"value": 2, "unit": "hours"}),
        book,
        now=datetime(2026, 10, 19, 12, 30, tzinfo=timezone.utc),
    )
    assert decision.status == vocabulary.SKIPPED
    assert decision.reason == vocabulary.CONDITION_NOT_SATISFIED
    assert "Declined" in decision.detail


@pytest.mark.parametrize("spelling", ["needsAction", "needsaction", "NEEDSACTION", "needs_action", " needs action "])
def test_the_response_status_comparison_survives_a_calendar_providers_spelling(spelling):
    """A JSON payload may carry any casing; the research spells it camelCase."""
    book = booking(primaryGuest={"responseStatus": spelling})
    assert conditions.has_not_responded(book) is True
    for responded in ("accepted", "Accepted", "DECLINED", " declined "):
        assert conditions.has_not_responded(booking(primaryGuest={"responseStatus": responded})) is False


def test_an_absent_response_status_counts_as_unanswered():
    """An invite nobody has touched looks exactly like this from the outside."""
    assert conditions.has_not_responded(booking(primaryGuest={"responseStatus": None})) is True


def test_the_plain_before_reminder_ignores_the_response_status():
    """Only the conditional variant has a response gate."""
    book = booking(primaryGuest={"responseStatus": vocabulary.RESPONSE_ACCEPTED})
    decision = conditions.evaluate(
        reminder(), book, now=datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)
    )
    assert decision.status == vocabulary.SENT
    assert all(check["check"] != "primary_guest_responded" for check in decision.checks)


def test_the_after_meeting_reminder_ignores_the_response_status_too():
    book = booking(primaryGuest={"responseStatus": vocabulary.RESPONSE_DECLINED})
    decision = conditions.evaluate(
        reminder(condition=vocabulary.AFTER, offset={"value": 1, "unit": "hours"}),
        book,
        now=datetime(2026, 10, 19, 16, 0, tzinfo=timezone.utc),
    )
    assert decision.status == vocabulary.SENT


# --------------------------------------------------------------------------- #
# The advanced gates
# --------------------------------------------------------------------------- #


def test_the_weekday_gate_is_evaluated_against_the_meetings_start():
    book = booking(start="2026-10-19T14:00:00+00:00")  # a Monday
    gate = reminder(conditions={"match": "all", "rules": [{"kind": "weekday", "weekdays": ["monday"]}]})
    assert conditions.evaluate(gate, book, now=datetime(2026, 10, 18, 15, tzinfo=timezone.utc)).status == vocabulary.SENT

    # The same booking, gated on Saturday: refused, with the researched reason.
    gate = reminder(conditions={"match": "all", "rules": [{"kind": "weekday", "weekdays": ["saturday"]}]})
    decision = conditions.evaluate(gate, book, now=datetime(2026, 10, 18, 15, tzinfo=timezone.utc))
    assert decision.status == vocabulary.SKIPPED
    assert decision.reason == vocabulary.VIOLATED_RESTRICTION
    assert decision.reason_text == "Violated restriction"


def test_the_weekday_gate_reads_the_meetings_day_not_the_send_days():
    """'Send only if meeting starts on' - the subject is the meeting's start.

    A Monday meeting with a Friday send must not pass a Friday-only gate, which
    is what testing the send time would have produced.
    """
    book = booking(start="2026-10-19T14:00:00+00:00")  # Monday
    gate = reminder(conditions={"match": "all", "rules": [{"kind": "weekday", "weekdays": ["friday"]}]})
    decision = conditions.evaluate(gate, book, now=datetime(2026, 10, 18, 15, tzinfo=timezone.utc))
    assert decision.reason == vocabulary.VIOLATED_RESTRICTION


def test_the_weekday_gate_uses_the_bookings_local_day_not_utc():
    """A late-evening meeting east of UTC is the next day there.

    23:00 UTC on a Sunday is 09:00 on a Monday in Tokyo, so a Monday-only gate
    must pass for that guest and fail in UTC. Getting this backwards fires every
    weekend reminder on a weekday.
    """
    book = booking(start="2026-10-18T23:00:00+00:00", timezoneOffsetMinutes=540, timezone="Asia/Tokyo")
    gate = reminder(conditions={"match": "all", "rules": [{"kind": "weekday", "weekdays": ["monday"]}]})
    decision = conditions.evaluate(
        gate, {**book, "bookedAt": "2026-09-20T00:00:00+00:00"},
        now=datetime(2026, 10, 18, 15, tzinfo=timezone.utc),
    )
    assert decision.status == vocabulary.SENT
    # In UTC the same booking is a Sunday, so the same gate refuses it.
    utc_book = {**book, "timezoneOffsetMinutes": 0}
    refused = conditions.evaluate(
        gate,
        {**utc_book, "bookedAt": "2026-09-20T00:00:00+00:00"},
        now=datetime(2026, 10, 18, 15, tzinfo=timezone.utc),
    )
    assert refused.reason == vocabulary.VIOLATED_RESTRICTION


def test_the_weekday_index_is_monday_zero_as_published():
    """A silent off-by-one here fires every weekend reminder on a weekday."""
    assert list(vocabulary.WEEKDAY_INDEX.items())[0] == ("monday", 0)
    assert vocabulary.WEEKDAYS[datetime(2026, 10, 19).weekday()] == "monday"
    assert vocabulary.WEEKDAYS[datetime(2026, 10, 18).weekday()] == "sunday"


def test_the_lead_time_gate_uses_the_researchs_own_worked_example():
    """evidence: "the reminder will be sent only if the meeting is booked one
    week in advance from the current booking date"."""
    gate = reminder(conditions={"match": "all", "rules": [{"kind": "lead_time", "value": 1, "unit": "weeks"}]})
    far = booking(bookedAt="2026-09-20T09:00:00+00:00")  # a month ahead
    assert conditions.evaluate(gate, far, now=datetime(2026, 10, 18, 15, tzinfo=timezone.utc)).status == vocabulary.SENT

    late = booking(bookedAt="2026-10-17T09:00:00+00:00")  # two days ahead
    decision = conditions.evaluate(gate, late, now=datetime(2026, 10, 18, 15, tzinfo=timezone.utc))
    assert decision.reason == vocabulary.VIOLATED_RESTRICTION
    assert "lead_time" in decision.detail


def test_the_lead_time_gate_fails_closed_when_there_is_no_booking_date():
    """The option says send *only* if; a gate that passes when it cannot be
    checked is not a gate."""
    gate = reminder(conditions={"match": "all", "rules": [{"kind": "lead_time", "value": 1, "unit": "weeks"}]})
    # `decide` rather than `evaluate`: with no `bookedAt` there is no planning
    # moment to work from, so `plan` refuses the reminder first and the gate is
    # only reachable from the run phase. The gate itself is what is under test.
    decision = conditions.decide(gate, booking(bookedAt=None), now=datetime(2026, 10, 18, 15, tzinfo=timezone.utc))
    assert decision.reason == vocabulary.VIOLATED_RESTRICTION


def test_a_weekday_and_a_lead_time_both_failing_records_both_verdicts():
    """First failure wins for the *reason*; the checks still show the rest.

    A reviewer debugging a skip needs to know which of two gates failed, and a
    short-circuit would hide the second.
    """
    gate = reminder(
        conditions={
            "match": "all",
            "rules": [
                {"kind": "weekday", "weekdays": ["friday"]},
                {"kind": "lead_time", "value": 1, "unit": "weeks"},
            ],
        }
    )
    decision = conditions.evaluate(
        gate,
        booking(start="2026-10-18T14:00:00+00:00", bookedAt="2026-10-17T09:00:00+00:00"),
        now=datetime(2026, 10, 18, 15, tzinfo=timezone.utc),
    )
    assert decision.reason == vocabulary.VIOLATED_RESTRICTION
    failed = {check["check"] for check in decision.checks if not check["passed"]}
    assert failed == {"condition_weekday", "condition_lead_time", "condition_group"}


def test_no_restriction_passes_for_every_booking():
    """"No Restriction" is the product's own label for an absent gate."""
    # Booked long enough before the reminder's fire time that "schedule in the
    # past" cannot be the answer, so an absent gate is what lets it through.
    book = booking(start="2026-10-18T02:00:00+00:00", bookedAt="2026-09-01T00:00:00+00:00")
    assert conditions.evaluate(reminder(), book, now=datetime(2026, 10, 18, 15, tzinfo=timezone.utc)).status == vocabulary.SENT


def test_an_unknown_rule_kind_is_refused_rather_than_ignored():
    """A rule that does not fall through is a rule that cannot be wrong silently."""
    with pytest.raises(ReminderError, match="names kind 'moon_phase'"):
        conditions.evaluate_group({"match": "all", "rules": [{"kind": "moon_phase"}]}, booking())


def test_match_any_passes_on_one_satisfied_rule():
    gate = reminder(
        conditions={
            "match": "any",
            "rules": [
                {"kind": "weekday", "weekdays": ["saturday"]},
                {"kind": "lead_time", "value": 1, "unit": "weeks"},
            ],
        }
    )
    decision = conditions.evaluate(gate, booking(), now=datetime(2026, 10, 18, 15, tzinfo=timezone.utc))
    assert decision.status == vocabulary.SENT


def test_match_none_refuses_when_any_rule_passes():
    gate = reminder(
        conditions={
            "match": "none",
            "rules": [{"kind": "weekday", "weekdays": ["monday"]}],
        }
    )
    decision = conditions.evaluate(gate, booking(), now=datetime(2026, 10, 18, 15, tzinfo=timezone.utc))
    assert decision.reason == vocabulary.VIOLATED_RESTRICTION


def test_an_unknown_weekday_is_refused_naming_the_published_days():
    with pytest.raises(ReminderError, match="published days"):
        vocabulary.require_weekdays(["funday"])


# --------------------------------------------------------------------------- #
# Recipients
# --------------------------------------------------------------------------- #


def test_send_email_to_primary_guest_addresses_one_person():
    decision = conditions.resolve_recipients(reminder(emailTo="primary_guest"), booking())
    assert [entry["email"] for entry in decision["recipients"]] == ["priya.raman@northwind.example"]


def test_send_email_to_all_guests_addresses_everyone_once():
    decision = conditions.resolve_recipients(reminder(emailTo="all_guests"), booking())
    addresses = [entry["email"] for entry in decision["recipients"]]
    assert addresses == ["priya.raman@northwind.example", "marcus.webb@northwind.example"]


def test_all_guests_does_not_send_the_primary_guest_twice():
    """The `guests` list usually repeats the primary guest; a double send is a
    customer receiving the same reminder twice."""
    book = booking()
    book["guests"] = [{"email": "priya.raman@northwind.example"}, {"email": "marcus.webb@northwind.example"}]
    decision = conditions.resolve_recipients(reminder(emailTo="all_guests"), book)
    assert len(decision["recipients"]) == 2


def test_all_guests_still_sends_to_the_addressable_ones():
    """One guest without an address is not a reason to stop notifying the rest."""
    book = booking(
        guests=[
            {"email": "priya.raman@northwind.example", "name": "Priya Raman"},
            {"name": "Marcus Webb"},
        ]
    )
    decision = conditions.resolve_recipients(reminder(emailTo="all_guests"), book)
    assert [entry["email"] for entry in decision["recipients"]] == ["priya.raman@northwind.example"]
    assert decision["unaddressable"] == ["Marcus Webb"]


def test_an_email_reminder_with_nobody_addressable_is_recipient_not_found():
    book = booking(primaryGuest={"name": "Wen Li", "email": ""}, guests=[{"name": "Wen Li", "email": ""}])
    decision = conditions.resolve_recipients(reminder(emailTo="all_guests"), book)
    assert decision["reason"] == vocabulary.RECIPIENT_NOT_FOUND
    assert decision["detail"]


def test_an_sms_reminder_never_carries_an_email_address():
    """The researched SMS address is the guest form's phone field, and an SMS
    delivery row has no reason to hold the guest's email address."""
    decision = conditions.resolve_recipients(sms_reminder(), booking())
    assert decision["recipients"]
    for entry in decision["recipients"]:
        assert "email" not in entry
        assert entry["phone"]


def test_an_email_reminder_carries_no_phone_number():
    """The same argument the other way round."""
    decision = conditions.resolve_recipients(reminder(), booking())
    for entry in decision["recipients"]:
        assert "phone" not in entry
        assert entry["email"]


def test_an_sms_reminder_without_a_phone_is_phone_not_found():
    """Two recipient reasons, not one, and the research separates them."""
    book = booking(primaryGuest={"phone": ""})
    decision = conditions.resolve_recipients(sms_reminder(), book)
    assert decision["reason"] == vocabulary.PHONE_NOT_FOUND
    assert "Guest Form" in decision["detail"]


def test_sms_goes_to_the_primary_guest_only():
    """step 3 offers 'Send Email To: All Guests' and no SMS equivalent, so the
    control is not invented here."""
    book = booking()
    book["guests"] = [
        {"email": "priya.raman@northwind.example", "phone": "+15550100", "name": "Priya Raman"},
        {"email": "marcus.webb@northwind.example", "phone": "+15550101", "name": "Marcus Webb"},
    ]
    decision = conditions.resolve_recipients(sms_reminder(), book)
    assert [entry["phone"] for entry in decision["recipients"]] == ["+15550100"]


# --------------------------------------------------------------------------- #
# Senders and reply-to
# --------------------------------------------------------------------------- #


def test_send_email_from_resolves_each_of_the_three_modes(configured):
    book = booking()
    settings = org_view(configured)
    assert conditions.sender_for(reminder(emailFrom="host"), book, settings) == "dana@contoso.example"
    assert conditions.sender_for(reminder(emailFrom="booker"), book, settings) == "wen.li@contoso.example"
    assert conditions.sender_for(reminder(emailFrom="noreply"), book, settings) == "no-reply@no-reply.contoso.example"


def test_a_no_reply_sender_with_no_domain_is_refused_rather_than_sending_from_nothing(configured):
    """Defence in depth behind the configuration-time refusal.

    A no-reply sender with no domain would otherwise produce a message whose
    ``From`` header is empty. The five researched skip reasons have no entry for
    that, because it cannot be a delivery outcome - so it raises instead.
    """
    with pytest.raises(ConfigurationRefused, match="sending domain"):
        conditions.sender_for(reminder(emailFrom="noreply"), booking(), {})


def test_send_sms_from_picks_the_number_the_mode_names(configured):
    book = booking()
    settings = org_view(configured)
    assert conditions.sender_for(sms_reminder(smsFrom="local_area_number"), book, settings) == "+35315550100"
    assert conditions.sender_for(sms_reminder(smsFrom="any_number"), book, settings) == "+15550100"


@pytest.mark.parametrize(
    "choice,expected",
    [
        ("host", ["dana@contoso.example"]),
        ("booker", ["wen.li@contoso.example"]),
        ("assignees", ["alba.ries@contoso.example"]),
    ],
)
def test_send_replies_to_resolves_each_of_the_three_choices(choice, expected):
    assert conditions.reply_to_for(reminder(repliesTo=choice), booking()) == expected


# --------------------------------------------------------------------------- #
# The ladder is total
# --------------------------------------------------------------------------- #


def _every_combination() -> list[tuple[dict, dict]]:
    """The researched combinations, generated rather than listed.

    A hand-written list of cases is a list someone remembered to write. This
    enumerates the cross product of the three conditions, both channels, the four
    offset units, the three response states, three gate shapes, two lead-time
    arrangements and two addressability shapes, so a newly added gate is inside
    the sweep without the list being rewritten.

    The dimensions are chosen so the sweep can *fail* every one of the five
    documented reasons. A sweep that only ever produces sent messages is not a
    totality check, it is a smoke test with extra steps - and the first version
    of this one was, which is what the coverage assertion below caught.
    """
    cases: list[tuple[dict, dict]] = []
    conditions_list = list(vocabulary.CONDITIONS)
    units = list(vocabulary.UNITS)
    responses = [vocabulary.RESPONSE_ACCEPTED, vocabulary.RESPONSE_DECLINED, vocabulary.RESPONSE_NEEDS_ACTION]
    # A Monday start, a weekday gate the booking cannot satisfy, and a lead-time
    # gate a late booking cannot satisfy.
    gates = [
        None,
        {"match": "all", "rules": [{"kind": "weekday", "weekdays": ["saturday"]}]},
        {"match": "all", "rules": [{"kind": "lead_time", "value": 1, "unit": "weeks"}]},
    ]
    # One shape addressable by email and by phone, one addressable by neither.
    guests = [
        {"firstName": "Priya", "name": "Priya Raman", "email": "p@n.example", "phone": "+15550100"},
        {"firstName": "Wen", "name": "Wen Li"},
    ]
    # Booked a month ahead, and booked the day before, so the lead-time gate
    # passes in one arrangement and fails in the other.
    booked = ["2026-09-20T09:00:00+00:00", "2026-10-18T09:00:00+00:00"]
    for channel in (vocabulary.EMAIL, vocabulary.SMS):
        for condition in conditions_list:
            for unit in units:
                for response in responses:
                    for gate in gates:
                        for guest in guests:
                            for when in booked:
                                spec = sms_reminder() if channel == vocabulary.SMS else reminder()
                                spec = {**spec, "condition": condition, "offset": {"value": 2, "unit": unit}, "conditions": gate}
                                # The primary guest is replaced wholesale rather
                                # than merged, or the addressable shape's email
                                # would survive into the unaddressable case and
                                # the recipient gate could never fire.
                                cases.append(
                                    (
                                        spec,
                                        {
                                            **booking(),
                                            "bookedAt": when,
                                            "primaryGuest": {**guest, "responseStatus": response},
                                            "guests": [{**guest, "responseStatus": response}],
                                        },
                                    )
                                )
    return cases


#: The organisation's setup, for the pure-domain sweep. A sweep run with no org
#: cannot reach a `sent` decision on the SMS channel at all, because a message
#: with no sending number is refused rather than recorded as delivered.
SWEEP_ORG = {
    "noreply_domain": "no-reply.contoso.example",
    "number": "+15550100",
    "localNumber": "+35315550100",
}


def test_decide_has_no_fall_through_over_every_researched_combination():
    """Every combination ends in a status, and every skip carries a reason.

    This is the shape of the defect the build brief warns about: a rule that does
    not fall through is a bug someone hits in production, because the answer for
    the unforeseen combination is ``None`` and the caller has to remember to
    handle it. Asserted over a generated sweep rather than a written list, so a
    gate added later is inside the check.
    """
    combos = _every_combination()
    assert len(combos) >= 500, "the sweep shrank; it is no longer covering the space"
    seen_statuses: set[str] = set()
    seen_reasons: set[str] = set()
    for spec, book in combos:
        # Three moments, chosen to reach every rung: before planning, while the
        # reminder is pending, and long after it was due. A sweep that only ever
        # runs "now" would see one status and prove nothing about totality.
        for moment in (NOW, datetime(2026, 10, 19, 13, 0, tzinfo=timezone.utc), datetime(2026, 10, 21, tzinfo=timezone.utc)):
            decision = conditions.evaluate(spec, book, now=moment, org=SWEEP_ORG)
            assert decision.status in vocabulary.STATUSES, decision
            seen_statuses.add(decision.status)
            if decision.reason:
                seen_reasons.add(decision.reason)
            if decision.status == vocabulary.SKIPPED:
                assert decision.reason in vocabulary.SKIP_REASONS, decision
                assert decision.reason_text == vocabulary.SKIP_REASONS[decision.reason]
            else:
                assert decision.reason is None, decision
    # A sweep that only ever produces one answer proves nothing about totality.
    assert seen_statuses == set(vocabulary.STATUSES), seen_statuses
    assert seen_reasons == set(vocabulary.SKIP_REASONS), seen_reasons


def test_no_researched_condition_produces_a_reason_outside_the_documented_five():
    """The five reasons are a closed list in the research, and this build keeps
    it closed rather than inventing a sixth for a new situation."""
    for spec, book in _every_combination():
        decision = conditions.evaluate(spec, book, now=at(200), org=SWEEP_ORG)
        if decision.reason is not None:
            assert decision.reason in vocabulary.SKIP_REASONS


def test_a_sent_decision_always_names_its_recipients_and_sender():
    """A delivery that reports itself as sent and names nobody, or goes from
    nowhere, is a row nobody can audit - and the researched skip-reason list has
    no entry for it, because it cannot happen."""
    for spec, book in _every_combination():
        decision = conditions.evaluate(spec, book, now=at(200), org=SWEEP_ORG)
        if decision.status == vocabulary.SENT:
            assert decision.recipients
            assert decision.from_address


def test_a_missing_sending_address_is_refused_rather_than_recorded_as_sent():
    """Not a skip reason: an incomplete organisation is a mistake to fix, and the
    five documented reasons are a closed list this build keeps closed."""
    # Well past the fire times, or the run answers `scheduled` and the sender is
    # never resolved.
    past = datetime(2026, 10, 21, tzinfo=timezone.utc)
    with pytest.raises(ConfigurationRefused, match="has no local area number configured"):
        conditions.evaluate(sms_reminder(), booking(), now=past, org={})
    with pytest.raises(ConfigurationRefused, match="sending domain"):
        conditions.evaluate(reminder(emailFrom="noreply"), booking(), now=past, org={})
    with pytest.raises(ConfigurationRefused, match="no email address for the host"):
        conditions.evaluate(reminder(emailFrom="host"), booking(host={"email": ""}), now=past, org=SWEEP_ORG)


def test_an_sms_reminder_needs_a_number_behind_the_connection(configured, engine, tmp_path):
    """A connection with no number is a reminder that can be saved and never
    sent, and 'Sender not found' is not one of the five researched reasons.

    A fresh engine, because ``save_org`` is a *partial* update: on the configured
    one the numbers are already there, and testing an absent number against a
    setup that has one proves nothing.
    """
    fresh = ReminderEngine(store_on(tmp_path), clock=lambda: NOW)
    fresh.save_org({"connected": True}, actor="dana", source=SOURCE)
    with pytest.raises(ConfigurationRefused, match="needs a matching number"):
        fresh.create_reminder(sms_reminder(), actor="dana", source=SOURCE)
    fresh.save_org({"number": "+15550100"}, actor="dana", source=SOURCE)
    fresh.create_reminder(sms_reminder(smsFrom="any_number"), actor="dana", source=SOURCE)
    # ... and the local-area mode reads a different field, so one of the two is
    # still refused rather than silently sending from the wrong number.
    with pytest.raises(ConfigurationRefused, match="needs a matching number"):
        fresh.create_reminder(sms_reminder(smsFrom="local_area_number"), actor="dana", source=SOURCE)


def store_on(tmp_path) -> RecordStore:
    """A store over a throwaway database under the test's own temporary path."""
    database = AuditedDatabase(tmp_path / "org-numbers.db", mirror_dir=tmp_path / "mirror-numbers")
    return RecordStore(database)


def test_the_planning_and_running_questions_are_separate():
    """A reminder due in a fortnight is `scheduled` now and `sent` then; a
    reminder whose moment has gone reports the past, not a send."""
    spec = reminder(offset={"value": 1, "unit": "weeks"})
    book = booking()
    fire_time = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)

    assert conditions.plan(spec, book, now=NOW).status == vocabulary.SCHEDULED
    assert conditions.decide(spec, book, now=fire_time + timedelta(minutes=1)).status == vocabulary.SENT
    # Planned after its own fire time: the documented past reason.
    late = conditions.plan(spec, book, now=fire_time + timedelta(days=1))
    assert late.status == vocabulary.SKIPPED
    assert late.reason == vocabulary.SCHEDULE_IN_PAST
    assert late.reason_text == "Reminder schedule time in the past"


def test_the_planning_moment_comes_from_the_booking_not_the_callers_clock():
    """A scheduler that runs an hour late must not retroactively declare every
    due reminder "planned in the past".

    This is measured, not hypothetical: passing the run's clock as the planning
    clock made every reminder that fired report `schedule_in_past`, because a
    reminder that has fired has, by definition, a fire time behind it.
    """
    spec = reminder(offset={"value": 24, "unit": "hours"})
    book = booking(bookedAt="2026-09-20T09:00:00+00:00")
    fire_time = datetime(2026, 10, 18, 14, 0, tzinfo=timezone.utc)
    decision = conditions.evaluate(spec, book, now=fire_time + timedelta(hours=3))
    assert decision.status == vocabulary.SENT
    assert decision.reason is None


def test_a_reminder_planned_after_its_fire_time_reports_the_past(configured):
    """A "2 hours before" reminder attached to a meeting starting in an hour has
    no moment at which it could have been sent."""
    spec = reminder(offset={"value": 2, "unit": "hours"})
    book = booking(start="2026-10-05T10:00:00+00:00", bookedAt="2026-10-05T09:00:00+00:00")
    decision = conditions.evaluate(spec, book, now=NOW, org=org_view(configured))
    assert decision.status == vocabulary.SKIPPED
    assert decision.reason == vocabulary.SCHEDULE_IN_PAST


def test_a_booking_with_no_start_is_reported_not_raised():
    """One broken booking must not fail a whole run."""
    spec = reminder()
    book = booking(start="nonsense")
    decision = conditions.plan(spec, book, now=NOW)
    assert decision.status == vocabulary.SKIPPED
    assert decision.reason == vocabulary.SCHEDULE_IN_PAST


# --------------------------------------------------------------------------- #
# Dynamic tags
# --------------------------------------------------------------------------- #


def test_the_three_named_tags_resolve_for_a_real_booking():
    rendered = tags.render_message(
        "Hi {CP.Guest.FirstName}: {CP.Meeting.RescheduleUrl} or {CP.Meeting.CancelUrl}", "", booking()
    )
    assert rendered["subject"].startswith("Hi Priya:")
    assert "https://meet.example/northwind/reschedule" in rendered["subject"]
    assert "https://meet.example/northwind/cancel" in rendered["subject"]
    assert rendered["missing"] == []


def test_all_eight_cal_tokens_resolve():
    """``apis_hit`` quotes all eight, and two of them are not identifiers:
    ``{START_TIME_h:mma}`` has a colon and ``{EVENT_DATE_ddd, MMM D, YYYY h:mma}``
    has a comma and three spaces. A ``\\{(\\w+)\\}`` pattern matches neither."""
    book = booking(start="2026-10-19T14:00:00+00:00")
    body = " ".join(vocabulary.CAL_TOKENS)
    rendered = tags.render(body, book)
    assert rendered["missing"] == [], rendered["missing"]
    for token in vocabulary.CAL_TOKENS:
        assert token not in rendered["text"], f"{token} was not substituted"


def test_the_cal_time_token_renders_on_every_platform():
    """``%-I:%M %p`` is a glibc extension ``strftime`` rejects on Windows, and
    Cal's token is published - so it has to render identically everywhere."""
    rendered = tags.render("{START_TIME_h:mma}", booking(start="2026-10-19T14:00:00+00:00", timezoneOffsetMinutes=0))
    assert rendered["text"] == "2:00 pm"


def test_the_cal_date_token_renders_on_every_platform():
    rendered = tags.render(
        "{EVENT_DATE_ddd, MMM D, YYYY h:mma}", booking(start="2026-10-19T14:00:00+00:00", timezoneOffsetMinutes=0)
    )
    assert rendered["text"] == "Mon, Oct 19, 2026 2:00 pm"


def test_midnight_and_noon_render_as_twelve_not_zero():
    """A naive ``hour % 12`` gets both wrong, and a reminder that says
    "12:00 am" about a lunchtime meeting is the kind of error a customer
    notices."""
    noon = booking(start="2026-10-19T12:00:00+00:00", timezoneOffsetMinutes=0)
    assert tags.render("{START_TIME_h:mma}", noon)["text"] == "12:00 pm"
    midnight = booking(start="2026-10-19T00:00:00+00:00", timezoneOffsetMinutes=0)
    assert tags.render("{START_TIME_h:mma}", midnight)["text"] == "12:00 am"


def test_a_substituted_value_is_never_re_read_as_a_token():
    """``{CP.Guest.FirstName}`` must render as the name, not as ``{Priya}``.

    Two sequential substitution passes made this fail: the CP pass replaced the
    tag inside its braces, and the Cal pass then saw ``{Priya}`` - a brace body
    that is no longer a tag - and reported the guest's own first name as a
    missing token. One combined pass is what prevents it.
    """
    rendered = tags.render("Hello {CP.Guest.FirstName}!", booking())
    assert rendered["text"] == "Hello Priya!"
    assert rendered["missing"] == []
    assert "Priya" not in rendered["resolved"]


def test_a_braced_and_a_bare_spelling_are_the_same_tag():
    assert tags.render("{CP.Guest.FirstName}", booking())["text"] == tags.render("CP.Guest.FirstName", booking())["text"]


def test_an_unknown_tag_is_left_in_place_and_reported():
    """A body that reads "Hi , see you soon" goes out wrong and nobody notices;
    a preview still showing ``{CUSTOMER.TIER}`` is a problem someone can see."""
    rendered = tags.render("Your tier: {CUSTOMER.TIER}", booking())
    assert rendered["text"] == "Your tier: {CUSTOMER.TIER}"
    assert rendered["missing"] == ["{CUSTOMER.TIER}"]


def test_a_tag_the_booking_cannot_fill_is_left_in_place_and_reported():
    book = booking()
    book["primaryGuest"] = {"firstName": "Priya", "name": "Priya Raman", "email": "p@n.example"}
    rendered = tags.render("Call {CP.Guest.Phone} today", book)
    assert rendered["text"] == "Call {CP.Guest.Phone} today"
    assert rendered["missing"] == ["{CP.Guest.Phone}"]


def test_a_resolved_tag_is_reported_as_resolved():
    rendered = tags.render("{CP.Guest.FirstName} and {CP.Meeting.Name}", booking())
    # Reported as they were written, braces included, because that is the text
    # the composer shows and the text a client searches for.
    assert rendered["resolved"] == ["{CP.Guest.FirstName}", "{CP.Meeting.Name}"]


def test_the_tag_catalogue_offers_every_tag_with_a_rendered_example():
    catalog = tags.token_catalog()
    assert catalog["chili_piper"]["count"] == len(tags.CP_TAGS)
    assert catalog["cal"]["count"] == len(tags.CAL_TOKENS)
    assert set(tags.NAMED_CP_TAGS) <= set(catalog["chili_piper"]["named_in_research"])
    for entry in catalog["chili_piper"]["tags"]:
        # Every published tag must render something against a real booking: a tag
        # that silently produces nothing is a dead control in the composer.
        assert entry["example"], entry["tag"]
    for entry in catalog["cal"]["tokens"]:
        assert entry["example"] and "{" not in entry["example"], entry


def test_translation_is_recorded_but_not_claimed():
    """The research names ``autoTranslateEnabled`` and ``sourceLocale`` and no
    catalogue, so the switches are real and the translation is not invented."""
    rendered = tags.render_translation("Hello {CP.Guest.FirstName}", booking(), "fr")
    assert rendered["text"] == "Hello Priya"
    assert rendered["locale"] == "fr"
    assert rendered["translated"] is False


# --------------------------------------------------------------------------- #
# The Cal.com projection
# --------------------------------------------------------------------------- #


def test_the_projection_carries_the_version_header_outside_the_body():
    """The Cal API reference requires ``cal-api-version`` on the request, not in
    the payload, and a projection that buried it in the body would be wrong in a
    way that only shows up at the vendor."""
    projected = cal.workflow(reminder(), meeting_type_ids=["mt-1"])
    assert projected["headers"] == {"cal-api-version": "2024-08-13"}
    assert "cal-api-version" not in projected["body"]
    assert projected["path"] == "/v2/workflows"


def test_the_projection_uses_a_published_trigger_and_offset_unit():
    projected = cal.workflow(reminder(offset={"value": 2, "unit": "hours"}), meeting_type_ids=[])
    trigger = projected["body"]["trigger"]
    assert trigger["type"] in vocabulary.CAL_TRIGGERS
    assert trigger["type"] == "beforeEvent"
    assert trigger["offset"] == {"value": 2, "unit": "hour"}  # Cal's unit is singular


def test_an_after_meeting_reminder_projects_onto_after_event():
    projected = cal.workflow(reminder(condition=vocabulary.AFTER), meeting_type_ids=[])
    assert projected["body"]["trigger"]["type"] == "afterEvent"


def test_a_weeks_offset_projects_onto_whole_days():
    """Cal's unit enum is ``hour|minute|day`` and has no week; one week is
    exactly seven days, so the conversion is exact rather than a gap."""
    for value, expected in ((1, 7), (2, 14), (6, 42)):
        projected = cal.workflow(reminder(offset={"value": value, "unit": "weeks"}), meeting_type_ids=[])
        assert projected["body"]["trigger"]["offset"] == {"value": expected, "unit": "day"}


def test_the_response_gate_becomes_a_filter_step():
    """Cal has no response-conditional pre-meeting trigger, so the researched
    rule has to become a filter - which is what Cal's step kinds are for."""
    projected = cal.workflow(
        reminder(condition=vocabulary.BEFORE_IF_NO_RESPONSE), meeting_type_ids=["mt-1"]
    )
    steps = projected["body"]["steps"]
    assert [step["type"] for step in steps] == ["filter", "action"]
    assert steps[0]["filter"] == {
        "field": "responseStatus",
        "operator": "not_in",
        "value": ["accepted", "declined"],
    }


def test_both_advanced_gates_project_as_filters_in_evaluation_order():
    projected = cal.workflow(
        reminder(
            conditions={
                "match": "all",
                "rules": [
                    {"kind": "weekday", "weekdays": ["monday"]},
                    {"kind": "lead_time", "value": 1, "unit": "weeks"},
                ],
            }
        ),
        meeting_type_ids=[],
    )
    filters = [step for step in projected["body"]["steps"] if step["type"] == "filter"]
    assert [step["filter"]["field"] for step in filters] == ["start", "bookedAt"]


def test_the_action_step_is_addressed_to_the_attendee():
    """A reminder is never addressed to the host, so ``email_host`` and the
    whatsapp and AI-call actions are published but unreachable."""
    for spec, expected in ((reminder(), "email_attendee"), (sms_reminder(), "sms_attendee")):
        step = cal.cal_step(spec)
        assert step["action"] == expected
        assert step["action"] in vocabulary.CAL_STEP_ACTIONS
    reachable = set(vocabulary.CAL_ACTION_FROM_CHANNEL.values())
    assert reachable == {"email_attendee", "sms_attendee"}
    assert "email_host" not in reachable


def test_the_projection_carries_the_four_message_options_cal_names():
    spec = reminder(
        includeCalendarEvent=True,
        skipNoShowAttendees=True,
        autoTranslateEnabled=True,
        sourceLocale="fr",
    )
    step = cal.cal_step(spec)
    assert step["includeCalendarEvent"] is True
    assert step["autoTranslateEnabled"] is True
    assert step["sourceLocale"] == "fr"


def test_a_composed_message_projects_as_the_custom_template():
    assert cal.cal_template(reminder(subject="Hi", body="There")) == "custom"
    assert cal.cal_template(reminder(subject="", body="")) == "reminder"
    assert cal.cal_template(reminder(condition=vocabulary.AFTER, subject="", body="")) == "completed"


def test_an_unknown_cal_template_is_refused_naming_the_published_set():
    with pytest.raises(ReminderError, match="calTemplate"):
        cal.cal_template(reminder(calTemplate="haiku"))


def test_activation_reflects_the_meeting_types_a_reminder_is_attached_to():
    assert cal.activation({}, ["mt-1", "mt-2"]) == {
        "isActiveOnAllEventTypes": False,
        "activeOnEventTypeIds": ["mt-1", "mt-2"],
    }
    # A freshly created, not-yet-attached reminder is active nowhere, and Cal's
    # own flag for "everywhere" would be a lie about it.
    assert cal.activation({}, []) == {"isActiveOnAllEventTypes": True, "activeOnEventTypeIds": []}


def test_an_after_meeting_projection_folds_the_duration_in_when_a_booking_is_given():
    """This product anchors a follow-up on the meeting's end; Cal's afterEvent
    fires relative to the event, so the two only agree once duration is added."""
    book = booking(durationMinutes=45)
    assert cal.cal_trigger(reminder(condition=vocabulary.AFTER, offset={"value": 1, "unit": "hours"}))["offset"] == {
        "value": 1,
        "unit": "hour",
    }
    folded = cal.cal_trigger(
        reminder(condition=vocabulary.AFTER, offset={"value": 1, "unit": "hours"}), book
    )
    assert folded["offset"] == {"value": 2, "unit": "hour"}  # 45 minutes rounds up


def test_a_cal_offset_converts_back_to_this_products_own():
    assert cal.offset_from_cal({"value": 2, "unit": "hour"}) == {"value": 2, "unit": "hours", "minutes": 120}
    assert cal.offset_from_cal({"value": 30, "unit": "minute"})["unit"] == "minutes"
    assert cal.offset_from_cal({"value": 7, "unit": "day"})["unit"] == "days"


def test_an_unreachable_cal_trigger_cannot_become_a_reminder_condition():
    with pytest.raises(ReminderError, match="beforeEvent"):
        cal.condition_from_cal("bookingRejected", {"value": 1, "unit": "hour"})


# --------------------------------------------------------------------------- #
# The Cal validator
# --------------------------------------------------------------------------- #


def test_a_projected_workflow_validates_against_its_own_enums():
    projected = cal.workflow(
        reminder(condition=vocabulary.BEFORE_IF_NO_RESPONSE), meeting_type_ids=["mt-1"]
    )
    result = cal.validate_workflow(projected)
    assert result["ok"] is True
    assert result["problems"] == []


def test_the_validator_rejects_an_unknown_trigger_unit_action_and_template():
    result = cal.validate_workflow(
        {
            "body": {
                "trigger": {"type": "beforeEvent", "offset": {"value": 1, "unit": "fortnight"}},
                "steps": [{"type": "action", "action": "pigeon_post", "template": "sonnet"}],
            }
        }
    )
    fields = {problem["field"] for problem in result["problems"]}
    assert fields == {"trigger.offset.unit", "steps[0].action", "steps[0].template"}
    assert result["ok"] is False


def test_a_valid_cal_trigger_this_product_does_not_act_on_is_reported_not_refused():
    """Cal's enum is wider than this workflow, and a workflow for Cal.com is not
    an error - it is a workflow for Cal.com."""
    result = cal.validate_workflow(
        {"body": {"trigger": {"type": "bookingRejected", "offset": {"value": 1, "unit": "hour"}}, "steps": []}}
    )
    assert "not" in result["problems"][0]["message"] or "does not" in result["problems"][0]["message"]


def test_a_workflow_with_no_action_step_sends_nothing_and_says_so():
    result = cal.validate_workflow(
        {"body": {"trigger": {"type": "beforeEvent", "offset": {"value": 1, "unit": "hour"}}, "steps": [{"type": "filter", "filter": {}}]}}
    )
    assert any(problem["field"] == "steps" for problem in result["problems"])


def test_a_delay_step_is_reported_as_modelled_by_cal_and_not_produced_here():
    result = cal.validate_workflow(
        {
            "body": {
                "trigger": {"type": "beforeEvent", "offset": {"value": 1, "unit": "hour"}},
                "steps": [{"type": "action", "action": "email_attendee", "template": "reminder"}, {"type": "delay"}],
            }
        }
    )
    note = next(problem for problem in result["problems"] if problem["field"] == "steps[1].type")
    assert "not projected" in note["message"]


def test_the_validator_publishes_which_triggers_and_actions_are_reachable():
    result = cal.validate_workflow({"body": {"trigger": {}, "steps": []}})
    assert set(result["published"]["reachable_triggers"]) == {"beforeEvent", "afterEvent"}
    assert set(result["published"]["reachable_actions"]) == {"email_attendee", "sms_attendee"}


# --------------------------------------------------------------------------- #
# The organisational preconditions
# --------------------------------------------------------------------------- #


def test_an_sms_reminder_needs_a_connected_twilio_account(engine):
    with pytest.raises(ConfigurationRefused, match="Command Center"):
        engine.create_reminder(sms_reminder(), actor="dana", source=SOURCE)


def test_a_connected_account_is_enough_to_send_but_not_to_forward_replies(engine, configured):
    """The research flags reply forwarding separately with a warning glyph, so
    these are two flags and not one."""
    settings = org_view(configured)
    engine.save_org({**settings, "own_account": False}, actor="dana", source=SOURCE)
    assert engine.twilio_ready() is True
    assert engine.reply_forwarding_ready() is False


def test_a_noreply_sender_needs_the_organisations_own_domain(engine):
    with pytest.raises(ConfigurationRefused, match="sending domain"):
        engine.create_reminder(reminder(emailFrom="noreply"), actor="dana", source=SOURCE)


def test_configuring_the_domain_makes_the_noreply_sender_legal(engine):
    engine.save_org({"noreply_domain": "no-reply.contoso.example"}, actor="dana", source=SOURCE)
    record = engine.create_reminder(reminder(emailFrom="noreply"), actor="dana", source=SOURCE)
    assert record["data"]["emailFrom"] == "noreply"


def test_the_configuration_refusal_is_about_setup_not_a_bad_request(engine):
    """A distinct type from ``ReminderError``, so the HTTP layer can answer 428
    rather than implying the administrator got the request wrong."""
    assert issubclass(ConfigurationRefused, ReminderError)
    assert ConfigurationRefused is not ReminderError


# --------------------------------------------------------------------------- #
# The engine: validation
# --------------------------------------------------------------------------- #


def test_a_reminder_needs_a_name(configured):
    """The research's own list is a list of *named* assets."""
    spec = reminder()
    del spec["name"]
    with pytest.raises(ReminderError, match="needs a name"):
        configured.create_reminder(spec, actor="dana", source=SOURCE)


def test_a_reminder_rejects_an_unknown_channel_naming_the_published_set(configured):
    with pytest.raises(ReminderError, match=r"\[.email., .sms.\]"):
        configured.create_reminder(reminder(channel="carrier_pigeon"), actor="dana", source=SOURCE)


def test_a_reminder_rejects_an_unknown_email_to(configured):
    with pytest.raises(ReminderError, match="emailTo"):
        configured.create_reminder(reminder(emailTo="the_boss"), actor="dana", source=SOURCE)


def test_a_reminder_rejects_an_unknown_replies_to(configured):
    with pytest.raises(ReminderError, match="repliesTo"):
        configured.create_reminder(reminder(repliesTo="the_intern"), actor="dana", source=SOURCE)


def test_a_patch_is_revalidated_against_the_merged_result(configured, engine):
    record = configured.create_reminder(reminder(), actor="dana", source=SOURCE)
    engine.save_org({"noreply_domain": None, "connected": False}, actor="dana", source=SOURCE)
    # Patching to SMS with the connection gone must not leave a broken reminder.
    with pytest.raises(ConfigurationRefused):
        engine.update_reminder(record["id"], {"channel": "sms"}, actor="dana", source=SOURCE)


def test_a_patch_that_keeps_the_configuration_legal_still_works(configured, engine):
    record = configured.create_reminder(reminder(), actor="dana", source=SOURCE)
    updated = engine.update_reminder(record["id"], {"subject": "New subject"}, actor="dana", source=SOURCE)
    assert updated["data"]["subject"] == "New subject"
    assert updated["data"]["condition"] == vocabulary.BEFORE


# --------------------------------------------------------------------------- #
# The engine: reusability, detach and delete
# --------------------------------------------------------------------------- #


def test_a_reminder_is_a_reusable_asset_not_scoped_to_one_room(configured, room):
    """'reminders are reusable assets attachable to many Meeting Types'."""
    record = configured.create_reminder(reminder(), actor="dana", source=SOURCE)
    assert record.get("room_id") is None
    assert room["id"]


def test_one_reminder_attaches_to_many_meeting_types(configured):
    first = configured.create_meeting_type({"name": "Evaluation"}, actor="dana", source=SOURCE)
    second = configured.create_meeting_type({"name": "Security review"}, actor="dana", source=SOURCE)
    asset = configured.create_reminder(reminder(), actor="dana", source=SOURCE)
    configured.attach(first["id"], asset["id"], actor="dana", source=SOURCE)
    configured.attach(second["id"], asset["id"], actor="dana", source=SOURCE)
    assert configured.attachment_ids_for(asset["id"]) == sorted([first["id"], second["id"]])


def test_attaching_twice_does_not_send_twice(configured):
    meeting_type = configured.create_meeting_type({"name": "Evaluation"}, actor="dana", source=SOURCE)
    asset = configured.create_reminder(reminder(), actor="dana", source=SOURCE)
    first = configured.attach(meeting_type["id"], asset["id"], actor="dana", source=SOURCE)
    second = configured.attach(meeting_type["id"], asset["id"], actor="dana", source=SOURCE)
    assert first["id"] == second["id"]
    assert len(configured.attachments(meeting_type["id"])) == 1


def test_remove_from_meeting_type_spares_the_asset_and_its_other_attachments(configured):
    """The researched counterpart to Delete, and the reason both exist."""
    first = configured.create_meeting_type({"name": "Evaluation"}, actor="dana", source=SOURCE)
    second = configured.create_meeting_type({"name": "Security review"}, actor="dana", source=SOURCE)
    asset = configured.create_reminder(reminder(), actor="dana", source=SOURCE)
    configured.attach(first["id"], asset["id"], actor="dana", source=SOURCE)
    configured.attach(second["id"], asset["id"], actor="dana", source=SOURCE)

    configured.detach(first["id"], asset["id"], actor="dana", source=SOURCE)
    assert configured.get_reminder(asset["id"]) is not None
    assert configured.attachment_ids_for(asset["id"]) == [second["id"]]


def test_detaching_something_that_is_not_attached_is_refused(configured):
    meeting_type = configured.create_meeting_type({"name": "Evaluation"}, actor="dana", source=SOURCE)
    asset = configured.create_reminder(reminder(), actor="dana", source=SOURCE)
    with pytest.raises(ReminderError, match="not attached"):
        configured.detach(meeting_type["id"], asset["id"], actor="dana", source=SOURCE)


def test_deleting_a_reminder_keeps_its_delivery_history_auditable(configured, room, store):
    """A reminder's history outliving the reminder is the point of an audit log."""
    meeting_type = configured.create_meeting_type({"name": "Evaluation"}, room_id=room["id"], actor="dana", source=SOURCE)
    asset = configured.create_reminder(reminder(), actor="dana", source=SOURCE)
    configured.attach(meeting_type["id"], asset["id"], actor="dana", source=SOURCE)
    booking_record = configured.create_booking(
        room["id"], {**booking(), "meetingTypeId": meeting_type["id"]}, actor="dana", source=SOURCE
    )
    configured.deliver(room["id"], booking_record["id"], actor="dana", source=SOURCE, now=at(400))
    assert len(configured.deliveries(limit=100)) > 0

    configured.delete_reminder(asset["id"], actor="dana", source=SOURCE)
    assert configured.get_reminder(asset["id"]) is None
    assert len(configured.deliveries(limit=100)) > 0
    # And the deleted reminder is no longer attached, so it cannot fire again.
    assert configured.attached_reminders(meeting_type["id"]) == []


# --------------------------------------------------------------------------- #
# The engine: the phone requirement
# --------------------------------------------------------------------------- #


def test_attaching_an_sms_reminder_makes_the_meeting_types_form_need_a_phone(configured):
    """Cal: ``attendee.phoneNumber`` "becomes required when SMS reminders are
    enabled for the event type"."""
    meeting_type = configured.create_meeting_type({"name": "Evaluation"}, actor="dana", source=SOURCE)
    assert configured.phone_required(meeting_type["id"]) is False
    asset = configured.create_reminder(sms_reminder(), actor="dana", source=SOURCE)
    configured.attach(meeting_type["id"], asset["id"], actor="dana", source=SOURCE)
    assert configured.phone_required(meeting_type["id"]) is True


def test_a_disabled_sms_reminder_does_not_require_a_phone(configured):
    meeting_type = configured.create_meeting_type({"name": "Evaluation"}, actor="dana", source=SOURCE)
    asset = configured.create_reminder(sms_reminder(enabled=False), actor="dana", source=SOURCE)
    configured.attach(meeting_type["id"], asset["id"], actor="dana", source=SOURCE)
    assert configured.phone_required(meeting_type["id"]) is False


def test_a_booking_without_a_phone_is_refused_against_such_a_meeting_type(configured, room):
    meeting_type = configured.create_meeting_type({"name": "Evaluation"}, actor="dana", source=SOURCE)
    asset = configured.create_reminder(sms_reminder(), actor="dana", source=SOURCE)
    configured.attach(meeting_type["id"], asset["id"], actor="dana", source=SOURCE)
    spec = {**booking(), "meetingTypeId": meeting_type["id"]}
    spec["primaryGuest"] = {**spec["primaryGuest"], "phone": ""}
    spec["guests"] = [{**spec["guests"][0], "phone": ""}]
    with pytest.raises(ConfigurationRefused, match="Guest Form"):
        configured.create_booking(room["id"], spec, actor="dana", source=SOURCE)


def test_a_booking_made_before_the_sms_reminder_takes_the_skip_reason_instead(configured, room):
    """The researched consequence has two halves, because a booking cannot be
    refused retroactively."""
    meeting_type = configured.create_meeting_type({"name": "Evaluation"}, actor="dana", source=SOURCE)
    spec = {**booking(), "meetingTypeId": meeting_type["id"]}
    spec["primaryGuest"] = {**spec["primaryGuest"], "phone": ""}
    record = configured.create_booking(room["id"], spec, actor="dana", source=SOURCE, enforce_phone=False)
    assert record["data"]["phoneRequiredAtBooking"] is False

    asset = configured.create_reminder(sms_reminder(), actor="dana", source=SOURCE)
    configured.attach(meeting_type["id"], asset["id"], actor="dana", source=SOURCE)
    result = configured.deliver(room["id"], record["id"], actor="dana", source=SOURCE, now=at(1000))
    sms_row = next(row for row in result["deliveries"] if row["data"]["channel"] == vocabulary.SMS)
    assert sms_row["data"]["status"] == vocabulary.SKIPPED
    assert sms_row["data"]["reason"] == vocabulary.PHONE_NOT_FOUND


def test_a_booking_must_name_a_meeting_type(configured, room):
    with pytest.raises(ReminderError, match="meetingTypeId"):
        configured.create_booking(room["id"], booking(), actor="dana", source=SOURCE)


def test_a_booking_against_a_room_that_does_not_exist_is_a_not_found(configured):
    from dsr.db.audited import RecordNotFound

    meeting_type = configured.create_meeting_type({"name": "Evaluation"}, actor="dana", source=SOURCE)
    with pytest.raises(RecordNotFound):
        configured.create_booking("no-such-room", {**booking(), "meetingTypeId": meeting_type["id"]}, actor="dana", source=SOURCE)


# --------------------------------------------------------------------------- #
# The engine: plan, fire, deliver
# --------------------------------------------------------------------------- #


@pytest.fixture()
def wired(configured, room):
    """A configured engine with one meeting type, two reminders and a booking."""
    meeting_type = configured.create_meeting_type(
        {"name": "Enterprise Evaluation", "durationMinutes": 45}, room_id=room["id"], actor="dana", source=SOURCE
    )
    email = configured.create_reminder(reminder(), actor="dana", source=SOURCE)
    conditional = configured.create_reminder(
        reminder(name="Nudge", condition=vocabulary.BEFORE_IF_NO_RESPONSE, offset={"value": 2, "unit": "hours"}),
        actor="dana",
        source=SOURCE,
    )
    configured.attach(meeting_type["id"], email["id"], actor="dana", source=SOURCE)
    configured.attach(meeting_type["id"], conditional["id"], actor="dana", source=SOURCE)
    booking_record = configured.create_booking(
        room["id"], {**booking(), "meetingTypeId": meeting_type["id"]}, actor="dana", source=SOURCE
    )
    return {
        "engine": configured,
        "room": room,
        "meeting_type": meeting_type,
        "email": email,
        "conditional": conditional,
        "booking": booking_record,
    }


def test_planning_records_a_scheduled_row_per_attached_reminder(wired):
    result = wired["engine"].plan(wired["booking"], actor="dana", source=SOURCE)
    assert result["planned"] == 2
    assert {row["data"]["status"] for row in result["deliveries"]} == {vocabulary.SCHEDULED}
    assert all(row["data"]["fire_at"] for row in result["deliveries"])


def test_planning_skips_a_disabled_reminder_entirely(configured, room):
    meeting_type = configured.create_meeting_type({"name": "Evaluation"}, room_id=room["id"], actor="dana", source=SOURCE)
    asset = configured.create_reminder(reminder(enabled=False), actor="dana", source=SOURCE)
    configured.attach(meeting_type["id"], asset["id"], actor="dana", source=SOURCE)
    booking_record = configured.create_booking(
        room["id"], {**booking(), "meetingTypeId": meeting_type["id"]}, actor="dana", source=SOURCE
    )
    result = configured.plan(booking_record, actor="dana", source=SOURCE)
    assert result["planned"] == 0
    assert configured.deliveries(limit=100) == []


def test_firing_turns_the_scheduled_rows_into_their_outcome(wired):
    wired["engine"].plan(wired["booking"], actor="dana", source=SOURCE)
    fired = wired["engine"].fire(now=at(400), source=SOURCE)
    assert fired["fired"] == 2
    # One sent, one refused: the demo booking's guest has accepted, so the
    # researched "if the primary guest has not responded" rule does not apply.
    assert fired["sent"] == 1
    assert fired["skipped"] == 1
    # The conditional one is refused: the demo booking's guest has accepted.
    statuses = {row["data"]["reminderName"]: row["data"]["status"] for row in fired["deliveries"]}
    assert statuses["24 hours before"] == vocabulary.SENT
    assert statuses["Nudge"] == vocabulary.SKIPPED


def test_a_scheduled_row_keeps_what_planning_resolved(wired):
    wired["engine"].plan(wired["booking"], actor="dana", source=SOURCE)
    scheduled = wired["engine"].deliveries(limit=10)[0]
    planned_message = scheduled["data"]["message"]["subject"]
    planned_fire = scheduled["data"]["fire_at"]
    wired["engine"].fire(now=at(400), source=SOURCE)
    fired = wired["engine"].get_delivery(scheduled["id"])
    assert fired["data"]["message"]["subject"] == planned_message
    assert fired["data"]["fire_at"] == planned_fire
    assert fired["data"]["planned_at"] is not None


def test_firing_twice_does_not_rewrite_an_audit_row(wired):
    wired["engine"].plan(wired["booking"], actor="dana", source=SOURCE)
    first = wired["engine"].fire(now=at(400), source=SOURCE)
    second = wired["engine"].fire(now=at(500), source=SOURCE)
    assert first["fired"] == 2
    assert second["fired"] == 0
    assert len(wired["engine"].deliveries(limit=100)) == 2


def test_firing_before_the_fire_time_schedules_rather_than_sends(wired):
    wired["engine"].fire(now=NOW, source=SOURCE)
    assert {row["data"]["status"] for row in wired["engine"].deliveries(limit=10)} == {vocabulary.SCHEDULED}


def test_firing_picks_up_a_booking_planned_by_nobody_yet(wired):
    """The scheduler plans and fires, so a reminder attached since the last run
    is not left waiting for a separate pass."""
    fired = wired["engine"].fire(now=at(400), source=SOURCE)
    assert fired["fired"] == 2
    assert all(row["data"]["status"] in (vocabulary.SENT, vocabulary.SKIPPED) for row in fired["deliveries"])


def test_delivering_one_reminder_leaves_the_others_alone(wired):
    result = wired["engine"].deliver(
        wired["room"]["id"], wired["booking"]["id"], reminder_id=wired["conditional"]["id"], actor="dana", source=SOURCE, now=at(400)
    )
    assert result["count"] == 1
    assert result["deliveries"][0]["data"]["reminderId"] == wired["conditional"]["id"]


def test_delivering_a_booking_on_another_room_is_refused(wired):
    from dsr.db.audited import RecordNotFound

    other = wired["engine"].store.create("room", {"name": "Other"}, actor="dana")
    with pytest.raises(ReminderError, match="not in room"):
        wired["engine"].deliver(other["id"], wired["booking"]["id"], actor="dana", source=SOURCE)
    with pytest.raises(RecordNotFound):
        wired["engine"].deliver("no-such-room", wired["booking"]["id"], actor="dana", source=SOURCE)


def test_preview_writes_nothing_at_all(wired, store):
    before = len(store.list(DELIVERY_COLLECTION))
    result = wired["engine"].preview(wired["email"]["data"], wired["booking"]["data"], now=at(400))
    assert len(store.list(DELIVERY_COLLECTION)) == before
    assert result["decision"]["status"] == vocabulary.SENT
    assert result["message"]["subject"] == "Your evaluation on 2026-10-19"


def test_a_preview_agrees_with_the_delivery(wired):
    """Same evaluation, so a preview cannot promise something the send will not
    do - which is the only thing that makes a preview worth reading."""
    preview = wired["engine"].preview(wired["conditional"]["data"], wired["booking"]["data"], now=at(400))
    result = wired["engine"].deliver(
        wired["room"]["id"], wired["booking"]["id"], reminder_id=wired["conditional"]["id"], actor="dana", source=SOURCE, now=at(400)
    )
    assert preview["decision"]["status"] == result["deliveries"][0]["data"]["status"]
    assert preview["decision"]["reason"] == result["deliveries"][0]["data"]["reason"]


def test_a_composed_message_carries_the_calendar_event_when_asked(configured, room):
    meeting_type = configured.create_meeting_type({"name": "Evaluation"}, room_id=room["id"], actor="dana", source=SOURCE)
    asset = configured.create_reminder(reminder(includeCalendarEvent=True), actor="dana", source=SOURCE)
    configured.attach(meeting_type["id"], asset["id"], actor="dana", source=SOURCE)
    booking_record = configured.create_booking(
        room["id"], {**booking(), "meetingTypeId": meeting_type["id"]}, actor="dana", source=SOURCE
    )
    result = configured.deliver(room["id"], booking_record["id"], actor="dana", source=SOURCE, now=at(400))
    attachments = result["deliveries"][0]["data"]["message"]["attachments"]
    assert [entry["filename"] for entry in attachments] == ["invite.ics"]
    assert attachments[0]["content_type"] == "text/calendar"


def test_no_show_attendees_are_left_out_of_the_recipient_list(configured, room):
    meeting_type = configured.create_meeting_type({"name": "Evaluation"}, room_id=room["id"], actor="dana", source=SOURCE)
    asset = configured.create_reminder(reminder(emailTo="all_guests", skipNoShowAttendees=True), actor="dana", source=SOURCE)
    configured.attach(meeting_type["id"], asset["id"], actor="dana", source=SOURCE)
    guests = [dict(guest, noShow=True) for guest in BOOKING_DEFAULTS["guests"]]
    spec = booking(guests=guests, primaryGuest={"noShow": True})
    booking_record = configured.create_booking(
        room["id"], {**spec, "meetingTypeId": meeting_type["id"]}, actor="dana", source=SOURCE
    )
    result = configured.deliver(room["id"], booking_record["id"], actor="dana", source=SOURCE, now=at(400))
    # The filter is applied when the audience is resolved, not only when the
    # message is composed: a delivery that claims to have reached a guest it
    # skipped is a row nobody can audit.
    assert result["deliveries"][0]["data"]["recipients"] == []
    assert result["deliveries"][0]["data"]["reason"] == vocabulary.RECIPIENT_NOT_FOUND


def test_a_delivery_records_the_locale_it_went_out_in(configured, room):
    meeting_type = configured.create_meeting_type({"name": "Evaluation"}, room_id=room["id"], actor="dana", source=SOURCE)
    asset = configured.create_reminder(reminder(autoTranslateEnabled=True, sourceLocale="fr"), actor="dana", source=SOURCE)
    configured.attach(meeting_type["id"], asset["id"], actor="dana", source=SOURCE)
    booking_record = configured.create_booking(
        room["id"], {**booking(), "meetingTypeId": meeting_type["id"]}, actor="dana", source=SOURCE
    )
    result = configured.deliver(room["id"], booking_record["id"], actor="dana", source=SOURCE, now=at(400))
    message = result["deliveries"][0]["data"]["message"]
    assert message["locale"] == "fr"
    assert message["auto_translate_enabled"] is True
    assert message["translated"] is False


# --------------------------------------------------------------------------- #
# The engine: inbound SMS replies
# --------------------------------------------------------------------------- #


@pytest.fixture()
def sms_wired(configured, room):
    meeting_type = configured.create_meeting_type({"name": "Evaluation"}, room_id=room["id"], actor="dana", source=SOURCE)
    asset = configured.create_reminder(sms_reminder(repliesTo=vocabulary.REPLY_TO_ASSIGNEES), actor="dana", source=SOURCE)
    configured.attach(meeting_type["id"], asset["id"], actor="dana", source=SOURCE)
    booking_record = configured.create_booking(
        room["id"], {**booking(), "meetingTypeId": meeting_type["id"]}, actor="dana", source=SOURCE
    )
    # Well past the reminder's fire time, or the delivery would be `scheduled`
    # and would have resolved no reply-forwarding address yet.
    result = configured.deliver(room["id"], booking_record["id"], actor="dana", source=SOURCE, now=at(1200))
    return {"engine": configured, "room": room, "delivery": result["deliveries"][0]}


def test_an_inbound_reply_is_forwarded_to_the_send_replies_to_addresses(sms_wired):
    record = sms_wired["engine"].record_reply(
        sms_wired["delivery"]["id"],
        {"from": "+15550100", "body": "Can we move to Thursday?"},
        actor="dana",
        source=SOURCE,
    )
    assert record["data"]["forwardedTo"] == ["alba.ries@contoso.example"]
    assert record["data"]["body"] == "Can we move to Thursday?"


def test_a_reply_is_riding_along_on_the_delivery(sms_wired):
    engine = sms_wired["engine"]
    engine.record_reply(sms_wired["delivery"]["id"], {"from": "+1", "body": "one"}, actor="dana", source=SOURCE)
    engine.record_reply(sms_wired["delivery"]["id"], {"from": "+1", "body": "two"}, actor="dana", source=SOURCE)
    delivery = engine.get_delivery(sms_wired["delivery"]["id"])
    assert len(delivery["data"]["replies"]) == 2
    assert len(engine.replies(delivery_id=delivery["id"], limit=10)) == 2


def test_a_reply_without_an_organisation_owned_account_is_refused(configured, sms_wired):
    """The research's own warning: forwarding SMS replies requires your
    company's own Twilio account."""
    configured.save_org({**org_view(configured), "own_account": False}, actor="dana", source=SOURCE)
    with pytest.raises(ConfigurationRefused, match="own Twilio account"):
        configured.record_reply(sms_wired["delivery"]["id"], {"from": "+1", "body": "hi"}, actor="dana", source=SOURCE)


def test_a_reply_to_an_email_reminder_is_refused(configured, wired):
    result = wired["engine"].deliver(
        wired["room"]["id"], wired["booking"]["id"], reminder_id=wired["email"]["id"], actor="dana", source=SOURCE, now=at(400)
    )
    with pytest.raises(ReminderError, match="only an SMS reminder"):
        wired["engine"].record_reply(result["deliveries"][0]["id"], {"from": "+1", "body": "hi"}, actor="dana", source=SOURCE)


def test_a_reply_with_no_body_is_refused(sms_wired):
    with pytest.raises(ReminderError, match="needs a body"):
        sms_wired["engine"].record_reply(sms_wired["delivery"]["id"], {"from": "+1"}, actor="dana", source=SOURCE)


def test_a_reply_to_a_booking_with_no_forwarding_address_is_refused(configured, room):
    meeting_type = configured.create_meeting_type({"name": "Evaluation"}, room_id=room["id"], actor="dana", source=SOURCE)
    asset = configured.create_reminder(sms_reminder(repliesTo=vocabulary.REPLY_TO_HOST), actor="dana", source=SOURCE)
    configured.attach(meeting_type["id"], asset["id"], actor="dana", source=SOURCE)
    spec = booking(host={"email": "", "name": "Dana Okoro", "firstName": "Dana"}, booker={"email": ""})
    booking_record = configured.create_booking(
        room["id"], {**spec, "meetingTypeId": meeting_type["id"]}, actor="dana", source=SOURCE
    )
    result = configured.deliver(room["id"], booking_record["id"], actor="dana", source=SOURCE, now=at(1200))
    with pytest.raises(ReminderError, match="no reply-forwarding address"):
        configured.record_reply(result["deliveries"][0]["id"], {"from": "+1", "body": "hi"}, actor="dana", source=SOURCE)


# --------------------------------------------------------------------------- #
# The summary
# --------------------------------------------------------------------------- #


def test_the_summary_counts_by_status_reason_and_channel(wired):
    wired["engine"].deliver(wired["room"]["id"], wired["booking"]["id"], actor="dana", source=SOURCE, now=at(400))
    summary = wired["engine"].summary()
    assert summary["by_status"] == {vocabulary.SENT: 1, vocabulary.SKIPPED: 1}
    assert summary["by_reason"] == {vocabulary.CONDITION_NOT_SATISFIED: 1}
    assert summary["by_channel"] == {vocabulary.EMAIL: 2}
    assert summary["recipients"] == 1


def test_a_needs_human_skip_is_counted_apart_from_a_working_rule(configured, room):
    """'Recipient not found' is a data problem someone must fix on the booking;
    a weekday restriction is the rule working. Same status, different meaning."""
    meeting_type = configured.create_meeting_type({"name": "Evaluation"}, room_id=room["id"], actor="dana", source=SOURCE)
    unreachable = configured.create_reminder(reminder(name="No address"), actor="dana", source=SOURCE)
    gated = configured.create_reminder(
        reminder(name="Saturdays only", conditions={"match": "all", "rules": [{"kind": "weekday", "weekdays": ["saturday"]}]}),
        actor="dana",
        source=SOURCE,
    )
    for asset in (unreachable, gated):
        configured.attach(meeting_type["id"], asset["id"], actor="dana", source=SOURCE)
    spec = booking(primaryGuest={"name": "Wen Li", "email": ""})
    booking_record = configured.create_booking(
        room["id"], {**spec, "meetingTypeId": meeting_type["id"]}, actor="dana", source=SOURCE
    )
    configured.deliver(room["id"], booking_record["id"], actor="dana", source=SOURCE, now=at(400))
    summary = configured.summary()
    assert summary["by_status"] == {vocabulary.SKIPPED: 2}
    assert summary["needs_human"] == 1  # only the unreachable recipient


def test_a_room_scoped_summary_sees_only_that_rooms_rows(wired, store):
    other = store.create("room", {"name": "Other"}, actor="dana")
    wired["engine"].deliver(wired["room"]["id"], wired["booking"]["id"], actor="dana", source=SOURCE, now=at(400))
    assert wired["engine"].summary(room_id=wired["room"]["id"])["deliveries"] == 2
    assert wired["engine"].summary(room_id=other["id"])["deliveries"] == 0


# --------------------------------------------------------------------------- #
# The audit trail, and the source rule
# --------------------------------------------------------------------------- #


def test_every_write_method_requires_a_source(engine, room, configured):
    """The defect this prevents: an audit row naming a path nobody served.

    ``source`` is keyword-only and has no default, so a caller that forgets it
    fails loudly rather than writing a null into the audit log.
    """
    for call in (
        lambda: engine.save_org({}, actor="dana"),
        lambda: engine.create_reminder(reminder(), actor="dana"),
        lambda: engine.update_reminder("x", {}, actor="dana"),
        lambda: engine.delete_reminder("x", actor="dana"),
        lambda: engine.create_meeting_type({"name": "x"}, actor="dana"),
        lambda: engine.attach("x", "y", actor="dana"),
        lambda: engine.detach("x", "y", actor="dana"),
        lambda: engine.create_booking(room["id"], booking(), actor="dana"),
        lambda: engine.deliver(room["id"], "y", actor="dana"),
        lambda: engine.plan({"id": "y", "data": {}}, actor="dana"),
        lambda: engine.fire(),
        lambda: engine.record_reply("y", {"body": "hi"}, actor="dana"),
    ):
        with pytest.raises(TypeError):
            call()


def test_a_delivery_is_audited_under_the_source_it_was_given(wired, store):
    wired["engine"].deliver(wired["room"]["id"], wired["booking"]["id"], actor="dana", source=SOURCE, now=at(400))
    entries = store.audit(collection=DELIVERY_COLLECTION)
    assert len(entries) == 2
    assert {entry["source"] for entry in entries} == {SOURCE}
    assert all(entry["room_id"] == wired["room"]["id"] for entry in entries)


def test_the_fired_row_is_audited_under_the_fire_routes_source(wired, store):
    wired["engine"].plan(wired["booking"], actor="dana", source=SOURCE)
    wired["engine"].fire(now=at(400), source="POST /api/wf-061/fire")
    actions = [(row["action"], row["source"]) for row in store.audit(collection=DELIVERY_COLLECTION)]
    assert ("update", "POST /api/wf-061/fire") in actions
    assert ("insert", SOURCE) in actions


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
    http.patch(f"{PREFIX}/messaging", json={"noreply_domain": "no-reply.contoso.example", "connected": True, "own_account": True})
    meeting_type = http.post(f"{PREFIX}/meeting-types", json={"name": "Evaluation"}).json()
    reminder_id = http.post(
        f"{PREFIX}/reminders", json={"name": "24h", "offset": {"value": 24, "unit": "hours"}}
    ).json()["id"]
    http.post(f"{PREFIX}/meeting-types/{meeting_type['id']}/reminders", json={"reminder_id": reminder_id})
    http.patch(f"{PREFIX}/reminders/{reminder_id}", json={"subject": "Changed"})

    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    booking_id = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings",
        json={
            "meetingTypeId": meeting_type["id"],
            "start": "2026-10-19T14:00:00+00:00",
            "durationMinutes": 45,
            "bookedAt": "2026-09-20T09:00:00+00:00",
            "primaryGuest": {"name": "Priya Raman", "email": "p@n.example", "responseStatus": "accepted"},
            "host": {"name": "Dana", "email": "d@x.example"},
        },
    ).json()["id"]
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings/{booking_id}/plan")
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings/{booking_id}/deliver", json={})
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings/{booking_id}/preview", json={"reminder_id": reminder_id})
    http.post(f"{PREFIX}/fire")
    http.delete(f"{PREFIX}/meeting-types/{meeting_type['id']}/reminders/{reminder_id}")
    http.delete(f"{PREFIX}/reminders/{reminder_id}")

    store = RecordStore(client_store(http))
    sources = {row["source"] for row in store.audit(limit=1000) if row["source"]}
    routes = all_served_routes(http)

    assert sources, "no source was recorded, so the check proved nothing"
    for source in sorted(sources):
        assert source_names_a_mounted_route(source, routes), f"{source!r} names no route this app serves"


def test_the_openapi_schema_does_list_this_features_routes(http):
    """The check above depends on it, and a schema that silently omits a feature
    would make that check compare against a set the feature is not in."""
    paths = mounted_paths(http)
    assert f"{PREFIX}/rooms/{{room_id}}/bookings/{{booking_id}}/deliver" in paths
    assert f"{PREFIX}/meeting-types/{{meeting_type_id}}/reminders/{{reminder_id}}" in paths


def test_the_source_check_accepts_a_concrete_path_and_a_template_one():
    """The helper's own contract, since a wrong helper makes the audit checks
    pass for the wrong reason - which is worse than not having them."""
    routes = {("DELETE", f"{PREFIX}/meeting-types/{{meeting_type_id}}/reminders/{{reminder_id}}")}
    concrete = f"DELETE {PREFIX}/meeting-types/mt_1/reminders/rem_2"
    template = f"DELETE {PREFIX}/meeting-types/{{meeting_type_id}}/reminders/{{reminder_id}}"
    assert source_names_a_mounted_route(concrete, routes)
    assert source_names_a_mounted_route(template, routes)
    # A different resource entirely, and a path the app does not serve.
    assert not source_names_a_mounted_route(f"DELETE {PREFIX}/meeting-types/mt_1/reminders", routes)
    assert not source_names_a_mounted_route("DELETE /api/health", routes)
    assert not source_names_a_mounted_route("not a source at all", routes)


def test_every_source_this_feature_records_is_under_its_own_prefix(http):
    """And the sharper half: this feature never records a route that is not its own.

    The weaker check above would pass even if a domain function hardcoded a
    perfectly valid *core* path. This one cannot.
    """
    http.patch(f"{PREFIX}/messaging", json={"noreply_domain": "no-reply.contoso.example", "connected": True})
    meeting_type = http.post(f"{PREFIX}/meeting-types", json={"name": "Evaluation"}).json()
    reminder_id = http.post(f"{PREFIX}/reminders", json={"name": "24h"}).json()["id"]
    http.post(f"{PREFIX}/meeting-types/{meeting_type['id']}/reminders", json={"reminder_id": reminder_id})
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    booking_id = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings",
        json={
            "meetingTypeId": meeting_type["id"],
            "start": "2026-10-19T14:00:00+00:00",
            "primaryGuest": {"name": "Priya Raman", "email": "p@n.example"},
            "host": {"name": "Dana", "email": "d@x.example"},
        },
    ).json()["id"]
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings/{booking_id}/plan")
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings/{booking_id}/deliver", json={})
    http.post(f"{PREFIX}/fire")
    http.delete(f"{PREFIX}/meeting-types/{meeting_type['id']}/reminders/{reminder_id}")
    http.delete(f"{PREFIX}/reminders/{reminder_id}")

    store = RecordStore(client_store(http))
    mine = {
        row["source"]
        for row in store.audit(limit=1000)
        if row["collection"]
        in (DELIVERY_COLLECTION, BOOKING_COLLECTION, REMINDER_COLLECTION, ATTACHMENT_COLLECTION, MEETING_TYPE_COLLECTION)
        and row["source"]
    }
    routes = mounted_routes(http)

    assert len(mine) >= 6, f"the feature recorded fewer sources than it has write routes: {mine}"
    for source in sorted(mine):
        assert source.startswith(f"POST {PREFIX}") or source.startswith(f"PATCH {PREFIX}") or (
            source.startswith(f"DELETE {PREFIX}")
        ) or source.startswith(f"PATCH {PREFIX}"), f"{source!r} does not name a route under this feature's own prefix"
        assert source_names_a_mounted_route(source, routes), f"{source!r} names no mounted route"


def test_a_delivery_written_over_http_records_its_own_route(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    meeting_type = http.post(f"{PREFIX}/meeting-types", json={"name": "Evaluation"}).json()
    reminder_id = http.post(f"{PREFIX}/reminders", json={"name": "24h"}).json()["id"]
    http.post(f"{PREFIX}/meeting-types/{meeting_type['id']}/reminders", json={"reminder_id": reminder_id})
    booking_id = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings",
        json={
            "meetingTypeId": meeting_type["id"],
            "start": "2026-10-19T14:00:00+00:00",
            "primaryGuest": {"name": "Priya Raman", "email": "p@n.example"},
            "host": {"name": "Dana", "email": "d@x.example"},
        },
    ).json()["id"]
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings/{booking_id}/deliver", json={})
    store = RecordStore(client_store(http))
    sources = {row["source"] for row in store.audit(collection=DELIVERY_COLLECTION)}
    assert sources == {f"POST {PREFIX}/rooms/{room['id']}/bookings/{booking_id}/deliver"}


def test_every_source_string_the_feature_builds_is_under_its_own_prefix():
    """Structural, not behavioural: every write route's source is derived from
    ``router.prefix``, so a renamed route cannot leave a stale string behind."""
    module = load_feature(MODULE)
    found: list[str] = []
    for route in module.router.routes:
        for verb in ("POST", "PATCH", "PUT", "DELETE"):
            found.append(f"{verb} {module.router.prefix}")
    assert found and all(entry.startswith(f"POST {PREFIX}") or entry.endswith(PREFIX) for entry in found)


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


def test_the_feature_is_mounted_with_its_routes(http):
    body = http.get("/api/features").json()
    entry = next(feature for feature in body["features"] if feature["id"] == FEATURE_ID)
    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-061"
    assert entry["exception_handlers"] == ["ConfigurationRefused", "ReminderError"]
    assert body["failed_count"] == 0
    assert len(entry["routes"]) >= 20


def test_the_vocabulary_is_served_over_http(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["channels"] == list(vocabulary.CHANNELS)
    assert body["conditions"] == list(vocabulary.CONDITIONS)
    assert body["statuses"] == list(vocabulary.STATUSES)
    assert body["skip_reasons"] == vocabulary.SKIP_REASONS
    assert body["cal"]["triggers"] == list(vocabulary.CAL_TRIGGERS)
    assert body["cal"]["api_version"] == "2024-08-13"


def test_the_inferences_are_served_over_http(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(reminder_inferences.INFERENCES)
    assert body["sourced_quote"]
    assert body["sourced"]["skip_reasons"] == vocabulary.SKIP_REASONS


def test_the_tags_are_served_with_examples_over_http(http):
    body = http.get(f"{PREFIX}/tags").json()
    assert body["chili_piper"]["count"] == len(tags.CP_TAGS)
    assert body["cal"]["count"] == len(tags.CAL_TOKENS)
    assert {entry["token"] for entry in body["cal"]["tokens"]} == set(vocabulary.CAL_TOKENS)


def test_a_missing_configuration_is_a_428_not_a_400(http):
    """'Not configured yet' and 'you got the request wrong' are different
    answers, and the shared client puts the status on the error so a caller can
    tell them apart."""
    reminder_id = http.post(f"{PREFIX}/reminders", json={"name": "text", "channel": "sms"})
    assert reminder_id.status_code == 428
    assert reminder_id.json()["error"] == "configuration_required"
    assert "Command Center" in reminder_id.json()["detail"]
    # And a second attempt refuses the same way rather than behaving differently.
    assert http.post(f"{PREFIX}/reminders", json={"name": "text", "channel": "sms"}).status_code == 428


def test_an_invalid_reminder_is_a_400_naming_the_published_set(http):
    response = http.post(f"{PREFIX}/reminders", json={"name": "x", "channel": "carrier_pigeon"})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_reminder"
    assert "email" in response.json()["detail"] and "sms" in response.json()["detail"]


def test_the_messaging_setup_round_trips_over_http(http):
    assert http.get(f"{PREFIX}/messaging").json()["sms_ready"] is False
    saved = http.patch(
        f"{PREFIX}/messaging", json={"noreply_domain": "no-reply.contoso.example", "connected": True}
    ).json()
    assert saved["sms_ready"] is True
    assert saved["reply_forwarding_ready"] is False
    read = http.get(f"{PREFIX}/messaging").json()
    assert read["settings"]["noreply_domain"] == "no-reply.contoso.example"
    assert read["quotes"]["guest_form_phone"]


def test_the_messaging_setup_is_a_singleton(http):
    first = http.patch(f"{PREFIX}/messaging", json={"noreply_domain": "one.example"}).json()
    second = http.patch(f"{PREFIX}/messaging", json={"noreply_domain": "two.example"}).json()
    assert first["org"]["id"] == second["org"]["id"]
    assert second["org"]["data"]["noreply_domain"] == "two.example"


def test_a_reminder_round_trips_over_http(http):
    created = http.post(
        f"{PREFIX}/reminders",
        json={
            "name": "24 hours before",
            "channel": "email",
            "condition": "before_meeting_if_no_response",
            "offset": {"value": 24, "unit": "hours"},
            "emailTo": "all_guests",
            "subject": "Hi {CP.Guest.FirstName}",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["data"]["condition"] == "before_meeting_if_no_response"
    assert body["data"]["offset"] == {"value": 24, "unit": "hours"}
    assert body["data"]["conditions"] == {"match": "all", "rules": []}

    fetched = http.get(f"{PREFIX}/reminders/{body['id']}").json()
    assert fetched["attached_meeting_types"] == []
    assert fetched["cal_template"] == "custom"


def test_the_reminder_list_filters_by_channel_and_condition_over_http(http):
    http.post(f"{PREFIX}/reminders", json={"name": "email one", "condition": "after_meeting"})
    http.patch(f"{PREFIX}/messaging", json={"connected": True, "number": "+15550100"})
    http.post(f"{PREFIX}/reminders", json={"name": "sms one", "channel": "sms", "condition": "after_meeting"})
    http.post(f"{PREFIX}/reminders", json={"name": "nudge", "condition": "before_meeting_if_no_response"})
    assert http.get(f"{PREFIX}/reminders", params={"channel": "sms"}).json()["count"] == 1
    assert http.get(f"{PREFIX}/reminders", params={"condition": "after_meeting"}).json()["count"] == 2
    assert http.get(f"{PREFIX}/reminders").json()["count"] == 3


def test_attaching_and_detaching_over_http(http):
    meeting_type = http.post(f"{PREFIX}/meeting-types", json={"name": "Evaluation"}).json()
    reminder_id = http.post(f"{PREFIX}/reminders", json={"name": "24h"}).json()["id"]
    assert http.post(f"{PREFIX}/meeting-types/{meeting_type['id']}/reminders", json={"reminder_id": reminder_id}).status_code == 201
    view = http.get(f"{PREFIX}/meeting-types/{meeting_type['id']}").json()
    assert view["reminder_count"] == 1
    assert view["phone_required"] is False

    assert http.delete(f"{PREFIX}/meeting-types/{meeting_type['id']}/reminders/{reminder_id}").status_code == 204
    assert http.get(f"{PREFIX}/meeting-types/{meeting_type['id']}").json()["reminder_count"] == 0
    # The asset survives, which is the whole difference from Delete.
    assert http.get(f"{PREFIX}/reminders/{reminder_id}").status_code == 200


def test_detaching_something_not_attached_is_a_404_over_http(http):
    meeting_type = http.post(f"{PREFIX}/meeting-types", json={"name": "Evaluation"}).json()
    reminder_id = http.post(f"{PREFIX}/reminders", json={"name": "24h"}).json()["id"]
    response = http.delete(f"{PREFIX}/meeting-types/{meeting_type['id']}/reminders/{reminder_id}")
    assert response.status_code == 404


def test_deleting_a_reminder_over_http_is_204_and_soft(http):
    reminder_id = http.post(f"{PREFIX}/reminders", json={"name": "24h"}).json()["id"]
    assert http.delete(f"{PREFIX}/reminders/{reminder_id}").status_code == 204
    assert http.delete(f"{PREFIX}/reminders/{reminder_id}").status_code == 404


def test_attaching_without_a_reminder_id_is_a_400_over_http(http):
    meeting_type = http.post(f"{PREFIX}/meeting-types", json={"name": "Evaluation"}).json()
    assert http.post(f"{PREFIX}/meeting-types/{meeting_type['id']}/reminders", json={}).status_code == 400


def test_the_meeting_type_view_reports_the_phone_requirement_over_http(http):
    http.patch(f"{PREFIX}/messaging", json={"connected": True, "number": "+15550100"})
    meeting_type = http.post(f"{PREFIX}/meeting-types", json={"name": "Evaluation"}).json()
    reminder_id = http.post(f"{PREFIX}/reminders", json={"name": "text", "channel": "sms"}).json()["id"]
    http.post(f"{PREFIX}/meeting-types/{meeting_type['id']}/reminders", json={"reminder_id": reminder_id})
    view = http.get(f"{PREFIX}/meeting-types/{meeting_type['id']}").json()
    assert view["phone_required"] is True
    assert "becomes required" in view["phone_required_quote"]


def test_a_booking_on_a_missing_room_is_a_404_over_http(http):
    meeting_type = http.post(f"{PREFIX}/meeting-types", json={"name": "Evaluation"}).json()
    response = http.post(
        f"{PREFIX}/rooms/no-such-room/bookings", json={"meetingTypeId": meeting_type["id"], "start": "2026-10-19T14:00:00Z"}
    )
    assert response.status_code == 404


def test_a_booking_without_a_phone_on_an_sms_meeting_type_is_a_428_over_http(http):
    http.patch(f"{PREFIX}/messaging", json={"connected": True, "number": "+15550100"})
    meeting_type = http.post(f"{PREFIX}/meeting-types", json={"name": "Evaluation"}).json()
    reminder_id = http.post(f"{PREFIX}/reminders", json={"name": "text", "channel": "sms"}).json()["id"]
    http.post(f"{PREFIX}/meeting-types/{meeting_type['id']}/reminders", json={"reminder_id": reminder_id})
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings",
        json={
            "meetingTypeId": meeting_type["id"],
            "start": "2026-10-19T14:00:00+00:00",
            "primaryGuest": {"name": "Priya Raman", "email": "p@n.example"},
        },
    )
    assert response.status_code == 428
    assert "Guest Form" in response.json()["detail"]


def test_plan_deliver_and_read_a_meetinging_activity_over_http(http):
    http.patch(f"{PREFIX}/messaging", json={"noreply_domain": "no-reply.contoso.example", "connected": True})
    meeting_type = http.post(f"{PREFIX}/meeting-types", json={"name": "Evaluation"}).json()
    reminder_id = http.post(
        f"{PREFIX}/reminders",
        json={"name": "24h", "condition": "before_meeting_if_no_response", "offset": {"value": 24, "unit": "hours"}},
    ).json()["id"]
    http.post(f"{PREFIX}/meeting-types/{meeting_type['id']}/reminders", json={"reminder_id": reminder_id})
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    booking_id = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings",
        json={
            "meetingTypeId": meeting_type["id"],
            "start": "2026-10-19T14:00:00+00:00",
            "bookedAt": "2026-09-20T09:00:00+00:00",
            "primaryGuest": {"name": "Priya Raman", "email": "p@n.example", "responseStatus": "needsAction"},
            "host": {"name": "Dana", "email": "d@x.example"},
        },
    ).json()["id"]

    planned = http.post(f"{PREFIX}/rooms/{room['id']}/bookings/{booking_id}/plan").json()
    assert planned["planned"] == 1
    assert planned["deliveries"][0]["data"]["status"] == "scheduled"

    delivered = http.post(f"{PREFIX}/rooms/{room['id']}/bookings/{booking_id}/deliver", json={}).json()
    assert delivered["deliveries"][0]["data"]["status"] == "scheduled"  # 24h out from a real clock

    activity = http.get(f"{PREFIX}/rooms/{room['id']}/deliveries").json()
    assert activity["count"] == 2
    assert all(row["data"]["status"] == "scheduled" for row in activity["deliveries"])


def test_the_preview_route_writes_nothing_over_http(http):
    http.patch(f"{PREFIX}/messaging", json={"noreply_domain": "no-reply.contoso.example"})
    meeting_type = http.post(f"{PREFIX}/meeting-types", json={"name": "Evaluation"}).json()
    reminder_id = http.post(
        f"{PREFIX}/reminders", json={"name": "24h", "subject": "Hi {CP.Guest.FirstName}"}
    ).json()["id"]
    http.post(f"{PREFIX}/meeting-types/{meeting_type['id']}/reminders", json={"reminder_id": reminder_id})
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    booking_id = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings",
        json={
            "meetingTypeId": meeting_type["id"],
            "start": "2099-10-19T14:00:00+00:00",
            "primaryGuest": {"name": "Priya Raman", "email": "p@n.example"},
            "host": {"name": "Dana", "email": "d@x.example"},
        },
    ).json()["id"]
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings/{booking_id}/preview", json={"reminder_id": reminder_id}
    )
    assert response.status_code == 200
    assert response.json()["message"]["subject"] == "Hi Priya"
    assert http.get(f"{PREFIX}/rooms/{room['id']}/deliveries").json()["count"] == 0


def test_the_preview_falls_back_to_the_first_attached_reminder_over_http(http):
    meeting_type = http.post(f"{PREFIX}/meeting-types", json={"name": "Evaluation"}).json()
    reminder_id = http.post(f"{PREFIX}/reminders", json={"name": "24h"}).json()["id"]
    http.post(f"{PREFIX}/meeting-types/{meeting_type['id']}/reminders", json={"reminder_id": reminder_id})
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    booking_id = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings",
        json={
            "meetingTypeId": meeting_type["id"],
            "start": "2099-10-19T14:00:00+00:00",
            "primaryGuest": {"name": "Priya Raman", "email": "p@n.example"},
            "host": {"name": "Dana", "email": "d@x.example"},
        },
    ).json()["id"]
    assert http.post(f"{PREFIX}/rooms/{room['id']}/bookings/{booking_id}/preview", json={}).status_code == 200


def test_a_preview_of_a_booking_with_no_reminders_is_a_404_over_http(http):
    meeting_type = http.post(f"{PREFIX}/meeting-types", json={"name": "Evaluation"}).json()
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    booking_id = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings",
        json={"meetingTypeId": meeting_type["id"], "start": "2099-10-19T14:00:00+00:00", "primaryGuest": {"name": "W"}},
    ).json()["id"]
    response = http.post(f"{PREFIX}/rooms/{room['id']}/bookings/{booking_id}/preview", json={})
    assert response.status_code == 404
    assert "no reminders attached" in response.json()["detail"]


def test_the_cal_workflow_route_returns_the_projection_over_http(http):
    reminder_id = http.post(f"{PREFIX}/reminders", json={"name": "24h", "offset": {"value": 1, "unit": "weeks"}}).json()["id"]
    body = http.get(f"{PREFIX}/reminders/{reminder_id}/cal-workflow").json()
    assert body["workflow"]["headers"] == {"cal-api-version": "2024-08-13"}
    assert body["workflow"]["body"]["trigger"]["offset"] == {"value": 7, "unit": "day"}
    assert body["notes"]["outbound"] is False


def test_the_cal_workflow_route_folds_a_bookings_duration_when_given(http):
    meeting_type = http.post(f"{PREFIX}/meeting-types", json={"name": "Evaluation"}).json()
    reminder_id = http.post(
        f"{PREFIX}/reminders", json={"name": "follow-up", "condition": "after_meeting", "offset": {"value": 1, "unit": "hours"}}
    ).json()["id"]
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    booking_id = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings",
        json={"meetingTypeId": meeting_type["id"], "start": "2026-10-19T14:00:00+00:00", "durationMinutes": 45, "primaryGuest": {"name": "W"}},
    ).json()["id"]
    without = http.get(f"{PREFIX}/reminders/{reminder_id}/cal-workflow").json()
    with_booking = http.get(
        f"{PREFIX}/reminders/{reminder_id}/cal-workflow", params={"booking_id": booking_id}
    ).json()
    assert without["workflow"]["body"]["trigger"]["offset"]["value"] == 1
    assert with_booking["workflow"]["body"]["trigger"]["offset"]["value"] == 2


def test_the_cal_workflow_route_404s_on_an_unknown_booking(http):
    reminder_id = http.post(f"{PREFIX}/reminders", json={"name": "24h"}).json()["id"]
    assert http.get(f"{PREFIX}/reminders/{reminder_id}/cal-workflow", params={"booking_id": "nope"}).status_code == 404


def test_the_cal_validator_route_over_http(http):
    assert http.post(f"{PREFIX}/cal-workflows/validate", json={"body": {"trigger": {"type": "beforeEvent", "offset": {"value": 1, "unit": "hour"}}, "steps": [{"type": "action", "action": "email_attendee", "template": "reminder"}]}}).json()["ok"] is True
    assert http.post(f"{PREFIX}/cal-workflows/validate", json={"body": {"trigger": {"type": "nope"}, "steps": []}}).json()["ok"] is False


def test_a_delivery_can_be_read_and_filtered_over_http(http):
    http.patch(f"{PREFIX}/messaging", json={"connected": True, "number": "+15550100"})
    meeting_type = http.post(f"{PREFIX}/meeting-types", json={"name": "Evaluation"}).json()
    reminder_id = http.post(f"{PREFIX}/reminders", json={"name": "24h"}).json()["id"]
    http.post(f"{PREFIX}/meeting-types/{meeting_type['id']}/reminders", json={"reminder_id": reminder_id})
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    booking_id = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings",
        json={
            "meetingTypeId": meeting_type["id"],
            "start": "2026-10-19T14:00:00+00:00",
            "primaryGuest": {"name": "Priya Raman", "email": "p@n.example", "responseStatus": "accepted"},
            "host": {"name": "Dana", "email": "d@x.example"},
        },
    ).json()["id"]
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings/{booking_id}/deliver", json={})
    delivery_id = http.get(f"{PREFIX}/deliveries", params={"limit": 5}).json()["deliveries"][0]["id"]

    read = http.get(f"{PREFIX}/deliveries/{delivery_id}").json()
    assert read["data"]["status"] == "scheduled"
    assert read["replies"] == []
    assert http.get(f"{PREFIX}/deliveries", params={"channel": "sms"}).json()["count"] == 0
    assert http.get(f"{PREFIX}/deliveries", params={"status": "scheduled"}).json()["count"] == 1
    assert http.get(f"{PREFIX}/deliveries", params={"status": "sent"}).json()["count"] == 0
    assert http.get(f"{PREFIX}/deliveries", params={"reason": "phone_not_found"}).json()["count"] == 0


def test_an_unknown_delivery_is_a_404_over_http(http):
    assert http.get(f"{PREFIX}/deliveries/nope").status_code == 404


def test_an_unknown_filter_value_is_a_400_naming_the_published_set(http):
    response = http.get(f"{PREFIX}/deliveries", params={"status": "teleported"})
    assert response.status_code == 400
    assert "status" in response.json()["detail"]


def test_the_sms_reply_route_refuses_without_an_own_account(http):
    http.patch(f"{PREFIX}/messaging", json={"connected": True, "number": "+15550100"})
    meeting_type = http.post(f"{PREFIX}/meeting-types", json={"name": "Evaluation"}).json()
    reminder_id = http.post(f"{PREFIX}/reminders", json={"name": "text", "channel": "sms"}).json()["id"]
    http.post(f"{PREFIX}/meeting-types/{meeting_type['id']}/reminders", json={"reminder_id": reminder_id})
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    booking_id = http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings",
        json={
            "meetingTypeId": meeting_type["id"],
            "start": "2026-10-19T14:00:00+00:00",
            "primaryGuest": {"name": "Priya Raman", "email": "p@n.example", "phone": "+15550100"},
            "host": {"name": "Dana", "email": "d@x.example"},
        },
    ).json()["id"]
    http.post(f"{PREFIX}/rooms/{room['id']}/bookings/{booking_id}/deliver", json={})
    delivery_id = http.get(f"{PREFIX}/deliveries", params={"channel": "sms"}).json()["deliveries"][0]["id"]
    response = http.post(f"{PREFIX}/deliveries/{delivery_id}/sms-replies", json={"from": "+1", "body": "hi"})
    assert response.status_code == 428
    assert "own Twilio account" in response.json()["detail"]


def test_the_summary_route_over_http(http):
    body = http.get(f"{PREFIX}/summary").json()
    assert body["deliveries"] == 0
    assert body["sms_ready"] is False
    assert body["reply_forwarding_ready"] is False


def test_the_fire_route_is_a_no_op_with_nothing_attached(http):
    assert http.post(f"{PREFIX}/fire").json() == {"fired": 0, "sent": 0, "skipped": 0, "deliveries": []}


def test_the_fire_route_reports_what_it_did_over_http(http):
    meeting_type = http.post(f"{PREFIX}/meeting-types", json={"name": "Evaluation"}).json()
    reminder_id = http.post(f"{PREFIX}/reminders", json={"name": "24h"}).json()["id"]
    http.post(f"{PREFIX}/meeting-types/{meeting_type['id']}/reminders", json={"reminder_id": reminder_id})
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    http.post(
        f"{PREFIX}/rooms/{room['id']}/bookings",
        json={
            "meetingTypeId": meeting_type["id"],
            "start": "2020-10-19T14:00:00+00:00",
            "bookedAt": "2020-09-20T09:00:00+00:00",
            "primaryGuest": {"name": "Priya Raman", "email": "p@n.example", "responseStatus": "accepted"},
            "host": {"name": "Dana", "email": "d@x.example"},
        },
    )
    fired = http.post(f"{PREFIX}/fire").json()
    assert fired["fired"] == 1
    assert fired["sent"] == 1


# --------------------------------------------------------------------------- #
# The inference registry
# --------------------------------------------------------------------------- #


def test_every_inference_is_named_bounded_and_changeable():
    """A judgement call left as a comment in a function body is one nobody
    re-reads, and a wrong one becomes product behaviour without anyone noticing.
    """
    for entry in reminder_inferences.INFERENCES:
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", entry["id"]), entry["id"]
        assert entry["topic"] and entry["basis"] and entry["why"], entry
        assert isinstance(entry["value"], dict) and entry["value"], entry
        assert entry["change_it"] and entry["blast_radius"], entry
    ids = [entry["id"] for entry in reminder_inferences.INFERENCES]
    assert len(ids) == len(set(ids)), "two inferences share an id"


def test_the_skip_reason_precedence_inference_matches_the_evaluation_order():
    """The named entry has to describe the code, or it is decoration."""
    entry = reminder_inferences.by_id("skip-reason-precedence")
    assert entry is not None
    assert "condition_not_satisfied" in " ".join(entry["value"]["order"])
    assert "violated_restriction" in " ".join(entry["value"]["order"])

    # A booking that fails both the response gate and the restriction records the
    # response gate - the first one the flow applies.
    spec = reminder(
        condition=vocabulary.BEFORE_IF_NO_RESPONSE,
        offset={"value": 2, "unit": "hours"},
        conditions={"match": "all", "rules": [{"kind": "weekday", "weekdays": ["saturday"]}]},
    )
    book = booking(primaryGuest={"responseStatus": vocabulary.RESPONSE_DECLINED})
    decision = conditions.evaluate(spec, book, now=datetime(2026, 10, 19, 12, 30, tzinfo=timezone.utc))
    assert decision.reason == vocabulary.CONDITION_NOT_SATISFIED
    # ... and the restriction's verdict is still recorded.
    assert any(check["check"] == "condition_group" and not check["passed"] for check in decision.checks)


def test_the_declined_is_responding_inference_is_the_behaviour():
    entry = reminder_inferences.by_id("declined-counts-as-responding")
    assert entry["value"]["declined"] == "responded"
    assert conditions.has_not_responded(booking(primaryGuest={"responseStatus": "declined"})) is False


def test_the_after_meeting_anchor_inference_matches_the_code():
    entry = reminder_inferences.by_id("after-meeting-anchors-on-the-end")
    assert entry["value"]["anchor"] == "start + durationMinutes"
    assert vocabulary.CONDITION_ANCHOR[vocabulary.AFTER] == "end"
    assert conditions.fire_at(
        reminder(condition=vocabulary.AFTER, offset={"value": 1, "unit": "hours"}), booking()
    ) == datetime(2026, 10, 19, 15, 45, tzinfo=timezone.utc)


def test_the_timezone_inference_matches_the_fixed_offset_the_code_reads():
    entry = reminder_inferences.by_id("timezone-is-a-fixed-offset")
    assert entry["value"]["carried_as"] == "timezoneOffsetMinutes on the booking"
    book = booking(start="2026-10-19T12:00:00+00:00", timezoneOffsetMinutes=-300)
    assert conditions.local_timezone(book).utcoffset(None) == timedelta(minutes=-300)
    # No offset at all is UTC, not a crash and not a guess.
    assert conditions.local_timezone(booking(timezoneOffsetMinutes=None)).utcoffset(None) == timedelta(0)
    # An unparseable offset is UTC too, because a bad field must not stop a run.
    assert conditions.local_timezone(booking(timezoneOffsetMinutes="banana")).utcoffset(None) == timedelta(0)


def test_the_no_outbound_send_inference_matches_the_code():
    entry = reminder_inferences.by_id("no-outbound-send")
    assert entry["value"]["calls_outbound"] is False
    import inspect

    from dsr.meeting_reminders import engine as engine_module

    for name in ("deliver", "fire", "plan", "record_reply", "create_reminder"):
        source = inspect.getsource(getattr(engine_module.ReminderEngine, name))
        for banned in ("requests.", "httpx", "urllib", "socket", "smtplib", "http.client"):
            assert banned not in source, f"{name} looks like it opens a socket: {banned}"


def test_the_translation_inference_matches_the_recorded_flag():
    entry = reminder_inferences.by_id("translation-is-not-claimed")
    assert entry["value"]["translation_performed"] is False
    assert tags.render_translation("hi", booking(), "fr")["translated"] is False


def test_the_detach_is_not_delete_inference_matches_the_two_methods():
    entry = reminder_inferences.by_id("detach-is-not-delete")
    assert "detach" in entry["change_it"] and "delete" in entry["change_it"]
    from dsr.meeting_reminders.engine import ReminderEngine

    assert hasattr(ReminderEngine, "detach") and hasattr(ReminderEngine, "delete_reminder")


def test_the_unresolved_tags_inference_matches_the_renderer():
    entry = reminder_inferences.by_id("unresolved-tags-are-reported-not-blanked")
    assert entry["value"]["never"] == "replaced with an empty string"
    book = booking()
    book["primaryGuest"] = {key: value for key, value in book["primaryGuest"].items() if key != "phone"}
    assert tags.render("Call {CP.Guest.Phone}", book)["text"] == "Call {CP.Guest.Phone}"


def test_the_sms_primary_guest_inference_matches_the_resolver():
    entry = reminder_inferences.by_id("sms-goes-to-the-primary-guest")
    assert entry["value"]["sms_recipients"] == "the primary guest only"
    book = booking(guests=[{"email": "a@x.example", "phone": "+1"}, {"email": "b@x.example", "phone": "+2"}])
    decision = conditions.resolve_recipients(sms_reminder(), book)
    assert len(decision["recipients"]) == 1


def test_the_weekday_evaluates_the_meeting_inference_matches_the_code():
    entry = reminder_inferences.by_id("weekday-gate-evaluates-the-meeting-not-the-send")
    assert "meeting start" in entry["value"]["tested_against"]
    book = booking(start="2026-10-19T14:00:00+00:00")  # Monday
    assert conditions.weekday_allowed(book, ["monday"]) is True
    assert conditions.weekday_allowed(book, ["tuesday"]) is False


def test_the_lead_time_fails_closed_inference_matches_the_code():
    entry = reminder_inferences.by_id("lead-time-gate-fails-closed")
    assert conditions.booked_far_enough(booking(bookedAt=None), vocabulary.require_offset(1, "weeks")) is False


def test_the_schedule_in_the_past_inference_matches_the_two_step_api():
    entry = reminder_inferences.by_id("schedule-in-the-past-is-a-planning-question")
    assert "planning" in entry["value"]["checked_at"]
    # Planning reports it; running does not, because at run time it is always past.
    assert conditions.plan(reminder(), booking(), now=datetime(2027, 1, 1, tzinfo=timezone.utc)).reason == vocabulary.SCHEDULE_IN_PAST
    assert conditions.decide(reminder(), booking(), now=datetime(2027, 1, 1, tzinfo=timezone.utc)).status == vocabulary.SENT


def test_the_phone_required_inference_names_both_halves():
    entry = reminder_inferences.by_id("phone-required-when-sms-is-enabled")
    assert "refused" in entry["value"]["booking_time"]
    assert "Phone not found" in entry["value"]["legacy_bookings"]


def test_the_sms_twilio_inference_names_both_requirements():
    entry = reminder_inferences.by_id("sms-requires-a-connected-twilio-account")
    assert "connected" in entry["value"]["at_configuration_time"]
    assert "own_account" in entry["value"]["reply_forwarding"]


def test_the_weeks_offset_conversion_is_named_as_an_inference():
    """Cal's unit enum has no week; the conversion is a judgement a reviewer may
    disagree with, so it has to be in the registry rather than only in a comment."""
    entry = reminder_inferences.by_id("weeks-offset-converts-to-days")
    assert entry["value"]["cal_unit"] == "day"
    assert cal.cal_offset(reminder(offset={"value": 1, "unit": "weeks"})) == {"value": 7, "unit": "day"}


# --------------------------------------------------------------------------- #
# The feature module's own contract
# --------------------------------------------------------------------------- #


def test_the_feature_module_exports_what_the_host_needs():
    module = load_feature(MODULE)
    assert module.FEATURE["id"] == FEATURE_ID
    assert module.FEATURE["ticket"] == "WF-061"
    assert module.router.prefix == PREFIX
    assert callable(module.seed)


def test_the_feature_module_does_not_import_the_app():
    """Importing ``dsr.api`` from a feature reintroduces the coupling the host
    exists to remove. There is a test on main for every feature; this asserts
    the same rule for this one without depending on that test's file list."""
    import ast

    path = Path(load_feature(MODULE).__file__)
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            assert node.module not in ("dsr.api", "api"), f"{path.name} imports {node.module}"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "dsr.api", f"{path.name} imports {alias.name}"


def test_the_feature_takes_its_dependencies_from_ds_r_deps():
    """The one-way dependency direction: api -> features -> deps."""
    import ast

    path = Path(load_feature(MODULE).__file__)
    imported = {
        node.module
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "dsr.deps" in imported
    assert "dsr.store" in imported


def test_every_route_on_the_router_is_under_its_own_prefix():
    for route in load_feature(MODULE).router.routes:
        assert route.path.startswith(PREFIX), route.path


def test_no_two_of_this_features_routes_share_a_method_and_path():
    """The host refuses a colliding (method, path) and reports the feature as
    failed rather than shadowing, so a duplicate here is a failed feature."""
    module = load_feature(MODULE)
    seen: set[tuple[str, str]] = set()
    for route in module.router.routes:
        for method in (getattr(route, "methods", None) or set()) - {"HEAD", "OPTIONS"}:
            key = (method, route.path)
            assert key not in seen, f"duplicate route {key}"
            seen.add(key)


def test_the_seed_reaches_every_status_and_reason_and_says_so(tmp_path):
    """The seed's own return string names what it produced, so a reviewer sees
    the mix without counting rows - and a gap is stated rather than hidden."""
    module = load_feature(MODULE)
    database = AuditedDatabase(tmp_path / "summary.db", mirror_dir=tmp_path / "mirror")
    try:
        store = RecordStore(database)
        rooms = [(store.create("room", {"name": name}, actor="seed")["id"], name) for name in ("a", "b", "c", "d")]
        summary = module.seed(database, {"room_ids": rooms, "now": NOW, "rng": None})
    finally:
        database.close()
    assert "statuses:" in summary
    for status in vocabulary.STATUSES:
        assert status in summary, f"{status} is not in the seed's own summary: {summary}"
    assert "NOT reached" not in summary, summary


def test_the_seed_never_raises_and_leaves_the_demo_non_empty(tmp_path):
    """A seed that raises is skipped loudly by the seeder, with the whole
    feature's demo lost, so the seed is run here exactly as the seeder runs it."""
    module = load_feature(MODULE)
    database = AuditedDatabase(tmp_path / "raise.db", mirror_dir=tmp_path / "mirror")
    try:
        store = RecordStore(database)
        rooms = [(store.create("room", {"name": name}, actor="seed")["id"], name) for name in ("a", "b", "c", "d")]
        module.seed(database, {"room_ids": rooms, "now": NOW, "rng": None})
        assert len(store.list(DELIVERY_COLLECTION, limit=1000)) > 20
        assert len(store.list(REPLY_COLLECTION, limit=100)) == 1
    finally:
        database.close()


def test_the_seed_says_so_when_there_are_no_rooms(tmp_path):
    module = load_feature(MODULE)
    database = AuditedDatabase(tmp_path / "empty.db", mirror_dir=tmp_path / "mirror")
    try:
        assert "no rooms" in module.seed(database, {"room_ids": [], "now": NOW, "rng": None})
    finally:
        database.close()


def test_the_seed_is_repeatable_on_a_fresh_database_at_any_clock(tmp_path):
    """Written from relative offsets, so the demo does not rot: a seed pinned to
    absolute dates would put every reminder in the past the first time the clock
    moved past them, and the interesting states would silently disappear."""
    module = load_feature(MODULE)
    for index, clock in enumerate(
        [
            datetime(2027, 4, 11, 3, 0, tzinfo=timezone.utc),  # a Sunday
            datetime(2028, 2, 29, 23, 0, tzinfo=timezone.utc),  # a leap day
            datetime(2029, 12, 31, 18, 0, tzinfo=timezone.utc),  # a Tuesday
        ]
    ):
        database = AuditedDatabase(tmp_path / f"clock-{index}.db", mirror_dir=tmp_path / "mirror")
        try:
            store = RecordStore(database)
            rooms = [(store.create("room", {"name": name}, actor="seed")["id"], name) for name in ("a", "b", "c", "d")]
            summary = module.seed(database, {"room_ids": rooms, "now": clock, "rng": None})
            assert "NOT reached" not in summary, f"clock {clock} lost a state: {summary}"
        finally:
            database.close()


# --------------------------------------------------------------------------- #
# Schema flexibility
# --------------------------------------------------------------------------- #


def test_a_team_can_add_its_own_field_to_a_reminder_without_a_migration(tmp_path):
    """The envelope is the only fixed vocabulary; the payload is arbitrary JSON."""
    database = AuditedDatabase(tmp_path / "schema.db", mirror_dir=tmp_path / "mirror")
    try:
        engine = ReminderEngine(RecordStore(database), clock=lambda: NOW)
        engine.save_org({"noreply_domain": "no-reply.contoso.example"}, actor="dana", source=SOURCE)
        record = engine.create_reminder(
            reminder(ourOwnField={"tier": "gold"}, anotherOne=[1, 2, 3]),
            actor="dana",
            source=SOURCE,
        )
        assert record["data"]["ourOwnField"] == {"tier": "gold"}
        assert record["data"]["anotherOne"] == [1, 2, 3]
        # And it is filterable through the dynamic index, which is what makes the
        # flexibility real rather than cosmetic.
        found = engine.store.find(REMINDER_COLLECTION, {"ourOwnField.tier": "gold"}, limit=10)
        assert [entry["id"] for entry in found] == [record["id"]]
    finally:
        database.close()


def test_the_only_fixed_vocabulary_is_the_envelope(tmp_path):
    """id, collection, room_id, revision, created_at, updated_at, deleted_at."""
    database = AuditedDatabase(tmp_path / "envelope.db", mirror_dir=tmp_path / "mirror")
    try:
        engine = ReminderEngine(RecordStore(database), clock=lambda: NOW)
        engine.save_org({"connected": True}, actor="dana", source=SOURCE)
        record = engine.create_reminder(reminder(), actor="dana", source=SOURCE)
        for field in ("id", "collection", "room_id", "revision", "created_at", "updated_at", "deleted_at"):
            assert field in record, field
    finally:
        database.close()
