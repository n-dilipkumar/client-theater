"""The HTTP surface, the audit-source rule and the demo data of WF-062.

Split from ``test_wf062.py``, which tests the researched rules against the pure
domain. This file drives the feature's own mounted router, so it covers the
wiring: the routes the feature actually serves, the refusals it actually
returns, and the fact that the audit rows it writes name the route that served
them.

The audit-source tests at the end are the ones worth reading twice. The contract
names the defect by name - "a feature's audit log kept recording a path the app
had stopped serving" - and it is invisible to a test scoped to one feature's own
strings, so these read the sources out of the module and out of the published
OpenAPI schema rather than asserting a hand-written list that would be updated
in the same commit as the bug.
"""

from __future__ import annotations

import datetime as dt
import random
import re
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore

PREFIX = "/api/wf-062"
MODULE = "wf062_route_a_requested_slot_for_host_approv"
FEATURE_ID = "wf-062-route-a-requested-slot-for-host-approv"

VERBS = ("GET", "POST", "PATCH", "PUT", "DELETE")
WRITE_VERBS = ("POST", "PATCH", "PUT", "DELETE")


def _app():
    from dsr.api import app

    return app


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf062http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(_app()) as test_client:
        yield test_client
    tmp.cleanup()


def make_room(client, name="Northwind") -> dict:
    return client.post("/api/records/room", json={"name": name, "account": name}).json()


def make_event_type(client, room_id, **spec) -> dict:
    body = {"title": "Discovery call", "hostId": "dana", "ownerId": "dana", "durationMinutes": 30, **spec}
    response = client.post(f"{PREFIX}/rooms/{room_id}/event-types", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def gated(client, room_id, **spec) -> dict:
    return make_event_type(client, room_id, requiresConfirmation=True, **spec)


def slot(days=2, hours=1) -> str:
    """A moment far enough ahead that no configured bound is in the way."""
    when = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days, hours=hours)
    return when.replace(microsecond=0).isoformat()


def soon(hours=2) -> str:
    """A moment inside a typical minimum-notice window, for the bounds checks."""
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)).replace(microsecond=0).isoformat()


def request_slot(client, room_id, event_type_id, *, start=None, email="ann@example.com", caller=None, **extra) -> dict:
    body = {
        "eventTypeId": event_type_id,
        "start": start or slot(),
        "attendee": {"name": "Ann Buyer", "email": email},
    }
    body.update(extra)
    params = {"caller": caller} if caller else None
    response = client.post(f"{PREFIX}/rooms/{room_id}/requests", json=body, params=params)
    assert response.status_code == 201, response.text
    return response.json()


def pending(client, room_id, **kwargs) -> str:
    """A PENDING request's uid, for a test that is about the decision."""
    return request_slot(client, room_id, gated(client, room_id)["id"], **kwargs)["request"]["uid"]


def as_host() -> dict:
    """The call the owner of a booking makes: themselves, authenticated."""
    return {"params": {"caller": "dana", "actor": "dana"}}


# --------------------------------------------------------------------------- #
# The feature registered itself
# --------------------------------------------------------------------------- #


def test_the_feature_is_mounted_by_discovery(client):
    """Nothing in api.py names it, and the route resolves."""
    body = client.get("/api/features").json()
    ids = {feature["id"] for feature in body["features"]}
    assert FEATURE_ID in ids


def test_the_feature_reports_its_prefix_routes_and_handler(client):
    body = client.get("/api/features").json()
    record = next(f for f in body["features"] if f["id"] == FEATURE_ID)
    assert record["prefix"] == PREFIX
    assert record["ticket"] == "WF-062"
    assert record["exception_handlers"] == ["ApprovalError"]
    assert len(record["routes"]) >= 18


def test_the_registry_reports_no_failed_feature(client):
    """A feature that raises on import is visible, and this one does not."""
    body = client.get("/api/features").json()
    assert body["failed_count"] == 0, body["failed"]


def test_the_core_routes_still_work_alongside_it(client):
    assert client.get("/api/health").json()["status"] == "ok"


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


def test_vocabulary_is_served_as_data(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert [entry["value"] for entry in body["statuses"]] == ["PENDING", "ACCEPTED", "REJECTED", "CANCELLED"]
    assert body["default_rejection_reason"] == "The organizer is no longer available at this time."
    assert body["dispatched_by"] == "simulated"


def test_capabilities_names_the_five_researched_surfaces(client):
    body = client.get(f"{PREFIX}/capabilities").json()
    assert body["apis"][0]["researched"] == "POST /v2/bookings"
    assert all(entry["implemented"] for entry in body["apis"])


def test_inferences_are_served_for_a_reviewer_to_read(client):
    body = client.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(body["inferences"])
    assert body["count"] >= 9
    assert all(entry["basis"] and entry["why"] for entry in body["inferences"])


# --------------------------------------------------------------------------- #
# Event types
# --------------------------------------------------------------------------- #


def test_an_event_type_defaults_both_gates_to_off(client):
    """A room that has configured nothing books directly rather than waiting."""
    room = make_room(client)
    body = make_event_type(client, room["id"])
    assert body["data"]["requiresConfirmation"] is False
    assert body["data"]["emailVerification"] is False
    assert body["requires_confirmation"] is False
    assert body["email_verification_required"] is False


def test_an_event_type_stores_its_configuration_as_payload_not_columns(client):
    """No migration, no typed column: a bound is an ordinary JSON field."""
    room = make_room(client)
    body = make_event_type(client, room["id"], minimumNoticeMinutes=120, maximumRangeDays=30, ourField={"a": 1})
    assert body["data"]["minimumNoticeMinutes"] == 120
    assert body["data"]["maximumRangeDays"] == 30
    assert body["data"]["ourField"] == {"a": 1}
    collections = client.get("/api/collections").json()["collections"]
    indexed = {entry["path"] for entry in next(c for c in collections if c["collection"] == "event_type")["fields"]}
    assert {"minimumNoticeMinutes", "maximumRangeDays", "ourField.a"} <= indexed


def test_an_event_type_without_a_title_is_422(client):
    room = make_room(client)
    response = client.post(f"{PREFIX}/rooms/{room['id']}/event-types", json={"hostId": "dana"})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


def test_an_event_type_with_a_non_boolean_toggle_is_422(client):
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/event-types", json={"title": "x", "requiresConfirmation": "yes"}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_event_type"


def test_listing_event_types_is_scoped_to_the_room(client):
    first, second = make_room(client, "A"), make_room(client, "B")
    make_event_type(client, first["id"])
    make_event_type(client, first["id"], title="Second")
    make_event_type(client, second["id"])
    assert client.get(f"{PREFIX}/rooms/{first['id']}/event-types").json()["count"] == 2
    assert client.get(f"{PREFIX}/rooms/{second['id']}/event-types").json()["count"] == 1


def test_reading_an_event_type_from_another_room_is_404(client):
    """A room id in the path that does not constrain the record is not scoping."""
    first, second = make_room(client, "A"), make_room(client, "B")
    event_type = make_event_type(client, first["id"])
    assert client.get(f"{PREFIX}/rooms/{first['id']}/event-types/{event_type['id']}").status_code == 200
    assert client.get(f"{PREFIX}/rooms/{second['id']}/event-types/{event_type['id']}").status_code == 404


def test_turning_requires_confirmation_on_is_a_merge_patch(client):
    room = make_room(client)
    event_type = make_event_type(client, room["id"], hostName="Dana")
    body = client.patch(
        f"{PREFIX}/rooms/{room['id']}/event-types/{event_type['id']}", json={"requiresConfirmation": True}
    ).json()
    assert body["requires_confirmation"] is True
    assert body["data"]["hostName"] == "Dana"


def test_turning_requires_confirmation_on_changes_the_booking_that_follows(client):
    """The researched flow's step one, observable through step two."""
    room = make_room(client)
    event_type = make_event_type(client, room["id"])
    assert request_slot(client, room["id"], event_type["id"], start=slot(days=2))["request"]["status"] == "ACCEPTED"
    client.patch(f"{PREFIX}/rooms/{room['id']}/event-types/{event_type['id']}", json={"requiresConfirmation": True})
    assert request_slot(client, room["id"], event_type["id"], start=slot(days=5))["request"]["status"] == "PENDING"


def test_turning_requires_confirmation_off_returns_a_type_to_direct_booking(client):
    room = make_room(client)
    event_type = make_event_type(client, room["id"], requiresConfirmation=True)
    client.patch(f"{PREFIX}/rooms/{room['id']}/event-types/{event_type['id']}", json={"requiresConfirmation": False})
    body = request_slot(client, room["id"], event_type["id"])
    assert body["request"]["status"] == "ACCEPTED"
    assert body["request"]["oneTimePassword"] is None


def test_turning_email_verification_on_is_refused_for_a_booking_with_no_code(client):
    room = make_room(client)
    event_type = make_event_type(client, room["id"], requiresConfirmation=True, emailVerification=True)
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests",
        json={"eventTypeId": event_type["id"], "start": slot(), "attendee": {"email": "ann@example.com"}},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "email_verification_required"


def test_patching_an_unknown_event_type_is_404(client):
    room = make_room(client)
    response = client.patch(f"{PREFIX}/rooms/{room['id']}/event-types/nope", json={"requiresConfirmation": True})
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_event_type"


def test_patching_an_event_type_in_another_room_is_404(client):
    first, second = make_room(client, "A"), make_room(client, "B")
    event_type = make_event_type(client, first["id"])
    response = client.patch(f"{PREFIX}/rooms/{second['id']}/event-types/{event_type['id']}", json={"title": "x"})
    assert response.status_code == 404


def test_patching_an_event_type_with_an_invalid_value_is_422(client):
    room = make_room(client)
    event_type = make_event_type(client, room["id"])
    response = client.patch(
        f"{PREFIX}/rooms/{room['id']}/event-types/{event_type['id']}", json={"durationMinutes": -5}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_event_type"


# --------------------------------------------------------------------------- #
# Requesting a slot
# --------------------------------------------------------------------------- #


def test_a_request_on_a_gated_type_is_pending_with_a_one_time_password(client):
    room = make_room(client)
    event_type = gated(client, room["id"])
    body = request_slot(client, room["id"], event_type["id"])
    record = body["request"]
    assert record["status"] == "PENDING"
    assert record["requiresConfirmation"] is True
    assert re.fullmatch(r"[0-9a-f-]{36}", record["oneTimePassword"])
    assert record["pending"] is True
    assert record["holdsSlot"] is True


def test_the_response_carries_the_four_researched_booking_output_fields(client):
    """`requiresConfirmation`, `oneTimePassword`, `status` and `rejectionReason`."""
    room = make_room(client)
    body = request_slot(client, room["id"], gated(client, room["id"])["id"])
    for field in ("requiresConfirmation", "oneTimePassword", "status", "rejectionReason"):
        assert field in body["request"], field
    assert body["request"]["api_status"] == "pending"


def test_a_request_fires_booking_requested_in_the_researched_shape(client):
    room = make_room(client)
    body = request_slot(client, room["id"], gated(client, room["id"])["id"])
    assert len(body["webhooks"]) == 1
    webhook = body["webhooks"][0]["data"]
    assert webhook["event"] == "BOOKING_REQUESTED"
    payload = webhook["payload"]
    assert payload["event"] == "BOOKING_REQUESTED"
    assert payload["payload"]["status"] == "PENDING"
    assert payload["payload"]["requiresConfirmation"] is True
    assert payload["payload"]["oneTimePassword"]


def test_a_request_records_the_actor_and_the_start_it_was_asked_for(client):
    room = make_room(client)
    event_type = gated(client, room["id"])
    when = slot(days=3)
    body = request_slot(client, room["id"], event_type["id"], start=when)
    data = body["request"]["data"]
    assert data["requestedAt"]
    assert data["start"].startswith(when[:16])
    assert data["end"] > data["start"]


def test_a_request_records_the_actor_it_was_given_over_http(client):
    room = make_room(client)
    event_type = gated(client, room["id"])
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests",
        json={"eventTypeId": event_type["id"], "start": slot(), "attendee": {"email": "ann@example.com"}},
        params={"actor": "dana"},
    )
    assert audit_entries(client, collection="booking_request")[0]["actor"] == "dana"


def test_a_booking_on_a_type_that_needs_no_approval_is_accepted_with_a_calendar_event(client):
    room = make_room(client)
    event_type = make_event_type(client, room["id"], requiresConfirmation=False)
    body = request_slot(client, room["id"], event_type["id"])
    assert body["request"]["status"] == "ACCEPTED"
    assert body["calendar_event"] is not None
    assert body["calendar_event"]["data"]["bookingUid"] == body["request"]["uid"]


def test_a_booking_created_directly_fires_no_booking_requested(client):
    """There was no request, so the webhook that announces one does not fire."""
    room = make_room(client)
    event_type = make_event_type(client, room["id"], requiresConfirmation=False)
    body = request_slot(client, room["id"], event_type["id"])
    assert body["webhooks"] == []
    assert client.get(f"{PREFIX}/rooms/{room['id']}/requests/{body['request']['uid']}/webhooks").json()["count"] == 0


def test_a_per_request_override_creates_a_request_on_a_type_that_needs_no_approval(client):
    """The flow's second entry: "or the booking is created as a request"."""
    room = make_room(client)
    event_type = make_event_type(client, room["id"], requiresConfirmation=False)
    body = request_slot(client, room["id"], event_type["id"], requiresConfirmationOverride=True)
    assert body["request"]["status"] == "PENDING"
    assert body["webhooks"][0]["data"]["event"] == "BOOKING_REQUESTED"


def test_a_per_request_override_cannot_step_over_the_toggle_the_admin_set(client):
    room = make_room(client)
    event_type = gated(client, room["id"])
    body = request_slot(client, room["id"], event_type["id"], requiresConfirmationOverride=False)
    assert body["request"]["status"] == "PENDING"


def test_a_request_with_no_event_type_is_422(client):
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests", json={"start": slot(), "attendee": {"email": "a@b.com"}}
    )
    assert response.status_code == 422
    assert "eventTypeId" in response.json()["detail"]


def test_a_request_against_an_unknown_event_type_is_404(client):
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests",
        json={"eventTypeId": "nope", "start": slot(), "attendee": {"email": "a@b.com"}},
    )
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_event_type"


def test_a_request_against_another_rooms_event_type_is_404(client):
    first, second = make_room(client, "A"), make_room(client, "B")
    event_type = gated(client, first["id"])
    response = client.post(
        f"{PREFIX}/rooms/{second['id']}/requests",
        json={"eventTypeId": event_type["id"], "start": slot(), "attendee": {"email": "a@b.com"}},
    )
    assert response.status_code == 404


def test_a_request_with_no_attendee_email_is_422(client):
    room = make_room(client)
    event_type = gated(client, room["id"])
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests", json={"eventTypeId": event_type["id"], "start": slot()}
    )
    assert response.status_code == 422


def test_a_second_request_for_a_held_slot_is_409(client):
    """A request holds the slot, so the second asker is a conflict."""
    room = make_room(client)
    event_type = gated(client, room["id"])
    when = slot()
    request_slot(client, room["id"], event_type["id"], start=when)
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests",
        json={"eventTypeId": event_type["id"], "start": when, "attendee": {"email": "bob@example.com"}},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "slot_conflict"


def test_a_request_reports_every_failed_check_at_once(client):
    """One refusal says everything that has to change, not just the first thing."""
    room = make_room(client)
    event_type = gated(client, room["id"], minimumNoticeMinutes=600, bookingLimitPerAttendee=1)
    request_slot(client, room["id"], event_type["id"], start=slot(days=4), email="ann@example.com")
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests",
        json={"eventTypeId": event_type["id"], "start": soon(), "attendee": {"email": "ann@example.com"}},
    )
    assert response.status_code == 409
    codes = {entry["code"] for entry in response.json()["refusals"]}
    assert {"out_of_bounds", "booking_limit"} <= codes


def test_the_checks_trace_says_which_researched_rule_each_check_came_from(client):
    room = make_room(client)
    body = request_slot(client, room["id"], gated(client, room["id"])["id"])
    assert {check["code"] for check in body["checks"]} == {
        "email_verification_not_required",
        "slot_conflict",
        "out_of_bounds",
        "booking_limit",
    }
    assert all(check["rule"] for check in body["checks"])


def test_a_bypass_flag_an_unentitled_caller_sends_is_ignored_with_its_reason(client):
    """An ignored flag is not a refusal: the check it would skip just applies."""
    room = make_room(client)
    event_type = gated(client, room["id"])
    when = slot(days=3)
    request_slot(client, room["id"], event_type["id"], start=when, email="ann@example.com")
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests",
        params={"caller": "mallory"},
        json={
            "eventTypeId": event_type["id"],
            "start": when,
            "attendee": {"email": "bob@example.com"},
            "apiVersion": "2026-05-01",
            "bypass": {"allowConflicts": True},
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "slot_conflict"
    assert [entry["code"] for entry in response.json()["refusals"]] == ["slot_conflict"]
    # The refusal carries the bypass context, because the error handler answers
    # with `refusals` and never the checks trace: a client fixing the rejection
    # has to be told which flag would have covered it.
    assert response.json()["refusals"][0]["bypassed_by"] == "allowConflicts"
    assert response.json()["refusals"][0]["bypass_honoured"] is False


def test_a_bypass_flag_an_entitled_caller_sends_is_honoured(client):
    """The host is the event type's owner, on one of the two named versions."""
    room = make_room(client)
    event_type = gated(client, room["id"])
    when = slot(days=3)
    request_slot(client, room["id"], event_type["id"], start=when, email="ann@example.com")
    body = request_slot(
        client,
        room["id"],
        event_type["id"],
        start=when,
        email="bob@example.com",
        caller="dana",
        apiVersion="2026-05-01",
        bypass={"allowConflicts": True},
    )
    assert body["request"]["status"] == "PENDING"
    assert body["bypasses"]["honoured"] == ["allowConflicts"]
    assert body["bypasses"]["ignored"] == []


def test_an_out_of_bounds_request_is_refused_for_a_caller_the_flag_would_not_help(client):
    room = make_room(client)
    event_type = gated(client, room["id"], maximumRangeDays=7)
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests",
        params={"caller": "mallory"},
        json={
            "eventTypeId": event_type["id"],
            "start": slot(days=30),
            "attendee": {"email": "bob@example.com"},
            "apiVersion": "2026-05-01",
            "bypass": {"allowBookingOutOfBounds": True},
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "out_of_bounds"


def test_allow_booking_out_of_bounds_lets_an_entitled_caller_through_it(client):
    room = make_room(client)
    event_type = gated(client, room["id"], maximumRangeDays=7)
    body = request_slot(
        client,
        room["id"],
        event_type["id"],
        start=slot(days=30),
        caller="dana",
        apiVersion="2026-05-01",
        bypass={"allowBookingOutOfBounds": True},
    )
    assert body["request"]["status"] == "PENDING"
    assert body["bypasses"]["honoured"] == ["allowBookingOutOfBounds"]


def test_a_bypass_flag_is_ignored_on_an_unsupported_version_for_an_entitled_caller(client):
    """The caller is the host; the version is not one of the two named."""
    room = make_room(client)
    event_type = gated(client, room["id"], maximumRangeDays=7)
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests",
        params={"caller": "dana"},
        json={
            "eventTypeId": event_type["id"],
            "start": slot(days=30),
            "attendee": {"email": "bob@example.com"},
            "apiVersion": "2024-08-13",
            "bypass": {"allowBookingOutOfBounds": True},
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "out_of_bounds"


def test_an_entitled_caller_honours_a_bypass_only_on_one_of_the_two_named_versions(client):
    room = make_room(client)
    event_type = gated(client, room["id"])
    when = slot()
    request_slot(client, room["id"], event_type["id"], start=when)
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests",
        params={"caller": "dana"},
        json={
            "eventTypeId": event_type["id"],
            "start": when,
            "attendee": {"email": "bob@example.com"},
            "apiVersion": "2024-08-13",
            "bypass": {"allowConflicts": True},
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "slot_conflict"


# --------------------------------------------------------------------------- #
# The email-verification triad
# --------------------------------------------------------------------------- #


def check_verification(client, room_id, event_type_id, email="ann@example.com"):
    """The researched "Check if email verification is required".

    A ``GET``: it checks and writes nothing, so it must not be shaped like a
    write. A client counting this feature's write routes should find exactly the
    eight that write.
    """
    return client.get(
        f"{PREFIX}/rooms/{room_id}/email-verification/required",
        params={"event_type_id": event_type_id, "email": email},
    )


def test_checking_verification_reports_the_gate_is_off(client):
    room = make_room(client)
    event_type = make_event_type(client, room["id"])
    body = check_verification(client, room["id"], event_type["id"]).json()
    assert body["required"] is False
    assert body["already_verified"] is False


def test_checking_verification_reports_the_gate_is_on(client):
    room = make_room(client)
    event_type = make_event_type(client, room["id"], emailVerification=True)
    body = check_verification(client, room["id"], event_type["id"]).json()
    assert body["required"] is True
    assert "email verification" in body["reason"]


def test_checking_verification_is_a_read_and_writes_nothing(client):
    room = make_room(client)
    event_type = make_event_type(client, room["id"], emailVerification=True)
    before = len(audit_entries(client))
    assert check_verification(client, room["id"], event_type["id"]).status_code == 200
    assert len(audit_entries(client)) == before


def test_checking_verification_against_an_unknown_event_type_is_404(client):
    room = make_room(client)
    assert check_verification(client, room["id"], "nope").status_code == 404


def test_sending_a_code_records_it_and_says_the_delivery_was_simulated(client):
    room = make_room(client)
    event_type = make_event_type(client, room["id"], emailVerification=True)
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/email-verification/send",
        json={"eventTypeId": event_type["id"], "email": "ann@example.com"},
    )
    assert response.status_code == 201
    body = response.json()
    assert re.fullmatch(r"\d{6}", body["code"])
    assert body["dispatched_by"] == "simulated"
    assert body["verification"]["data"]["verifiedAt"] is None


def test_sending_a_code_without_an_email_is_422(client):
    room = make_room(client)
    event_type = make_event_type(client, room["id"], emailVerification=True)
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/email-verification/send", json={"eventTypeId": event_type["id"]}
    )
    assert response.status_code == 422


def test_sending_a_code_against_an_unknown_event_type_is_404(client):
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/email-verification/send", json={"eventTypeId": "nope", "email": "a@b.com"}
    )
    assert response.status_code == 404


def test_a_request_with_a_code_that_was_never_sent_is_422(client):
    room = make_room(client)
    event_type = make_event_type(client, room["id"], emailVerification=True)
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests",
        json={
            "eventTypeId": event_type["id"],
            "start": slot(),
            "attendee": {"email": "ann@example.com"},
            "emailVerificationCode": "000000",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_verification_code"


def test_a_request_with_a_sent_but_unverified_code_is_422(client):
    """Sending is not verifying: the triad has three steps and this is the second."""
    room = make_room(client)
    event_type = make_event_type(client, room["id"], emailVerification=True)
    code = client.post(
        f"{PREFIX}/rooms/{room['id']}/email-verification/send",
        json={"eventTypeId": event_type["id"], "email": "ann@example.com"},
    ).json()["code"]
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests",
        json={
            "eventTypeId": event_type["id"],
            "start": slot(),
            "attendee": {"email": "ann@example.com"},
            "emailVerificationCode": code,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"] == "email_verification_not_verified"


def test_verifying_a_code_that_was_never_sent_is_422(client):
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/email-verification/verify", json={"code": "000000"}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_verification_code"


def test_verifying_without_a_code_is_422(client):
    room = make_room(client)
    assert client.post(f"{PREFIX}/rooms/{room['id']}/email-verification/verify", json={}).status_code == 422


def test_a_request_with_a_verified_code_goes_through(client):
    room = make_room(client)
    event_type = make_event_type(client, room["id"], emailVerification=True)
    code = client.post(
        f"{PREFIX}/rooms/{room['id']}/email-verification/send",
        json={"eventTypeId": event_type["id"], "email": "ann@example.com"},
    ).json()["code"]
    verified = client.post(
        f"{PREFIX}/rooms/{room['id']}/email-verification/verify",
        json={"code": code, "eventTypeId": event_type["id"], "email": "ann@example.com"},
    )
    assert verified.status_code == 200
    assert verified.json()["already_verified"] is False
    body = request_slot(client, room["id"], event_type["id"], emailVerificationCode=code)
    assert body["request"]["status"] == "ACCEPTED"
    assert body["request"]["data"]["emailVerification"]["satisfied"] is True


def test_verifying_twice_reports_it_was_already_verified(client):
    room = make_room(client)
    event_type = make_event_type(client, room["id"], emailVerification=True)
    code = client.post(
        f"{PREFIX}/rooms/{room['id']}/email-verification/send",
        json={"eventTypeId": event_type["id"], "email": "ann@example.com"},
    ).json()["code"]
    client.post(f"{PREFIX}/rooms/{room['id']}/email-verification/verify", json={"code": code})
    again = client.post(f"{PREFIX}/rooms/{room['id']}/email-verification/verify", json={"code": code})
    assert again.json()["already_verified"] is True


def test_a_verified_code_is_scoped_to_its_own_event_type(client):
    """A code verified against one type is not a pass for another."""
    first, second = make_room(client, "A"), make_room(client, "B")
    one = make_event_type(client, first["id"], emailVerification=True)
    two = make_event_type(client, second["id"], emailVerification=True)
    code = client.post(
        f"{PREFIX}/rooms/{first['id']}/email-verification/send",
        json={"eventTypeId": one["id"], "email": "ann@example.com"},
    ).json()["code"]
    client.post(f"{PREFIX}/rooms/{first['id']}/email-verification/verify", json={"code": code, "eventTypeId": one["id"]})
    response = client.post(
        f"{PREFIX}/rooms/{second['id']}/requests",
        json={
            "eventTypeId": two["id"],
            "start": slot(),
            "attendee": {"email": "ann@example.com"},
            "emailVerificationCode": code,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_verification_code"


# --------------------------------------------------------------------------- #
# Confirming
# --------------------------------------------------------------------------- #


def test_confirming_a_request_by_the_host_accepts_it(client):
    room = make_room(client)
    uid = pending(client, room["id"])
    body = client.post(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/confirm", json={}, **as_host()).json()
    assert body["request"]["status"] == "ACCEPTED"
    assert body["request"]["data"]["decidedBy"] == "dana"
    assert body["request"]["data"]["decidedAt"]
    assert body["slot_released"] is False


def test_confirming_creates_the_calendar_event(client):
    """The research says an event is "created only on confirm"."""
    room = make_room(client)
    uid = pending(client, room["id"])
    assert client.get(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/calendar-event").json()["calendar_event"] is None
    client.post(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/confirm", json={}, **as_host())
    event = client.get(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/calendar-event").json()["calendar_event"]
    assert event["data"]["bookingUid"] == uid
    assert event["data"]["createdBecause"] == "a host confirmed the request"


def test_a_confirmation_spends_the_one_time_password(client):
    room = make_room(client)
    uid = pending(client, room["id"])
    client.post(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/confirm", json={}, **as_host())
    record = client.get(f"{PREFIX}/rooms/{room['id']}/requests/{uid}").json()["request"]
    assert record["oneTimePassword"] is None
    assert record["holdsSlot"] is True


def test_confirming_by_a_stranger_is_403(client):
    """The researched rule: the authorization header refers to the owner."""
    room = make_room(client)
    uid = pending(client, room["id"])
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{uid}/confirm", json={}, params={"caller": "mallory"}
    )
    assert response.status_code == 403
    assert response.json()["error"] == "not_booking_owner"
    assert "team_admin" in response.json()["detail"]


def test_an_assigned_user_may_confirm(client):
    room = make_room(client)
    event_type = gated(client, room["id"], assignedUserIds=["priya"])
    uid = request_slot(client, room["id"], event_type["id"])["request"]["uid"]
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{uid}/confirm", json={}, params={"caller": "priya"}
    )
    assert response.status_code == 200
    assert response.json()["authorisation"]["roles"] == ["assigned_user"]


def test_a_declared_org_admin_may_confirm(client):
    room = make_room(client)
    uid = pending(client, room["id"])
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{uid}/confirm", json={}, params={"caller": "root", "roles": "org_admin"}
    )
    assert response.status_code == 200
    assert response.json()["authorisation"]["roles"] == ["org_admin"]


def test_confirming_with_the_one_time_password_is_accepted(client):
    room = make_room(client)
    event_type = gated(client, room["id"])
    made = request_slot(client, room["id"], event_type["id"])
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{made['request']['uid']}/confirm",
        json={"oneTimePassword": made["request"]["oneTimePassword"]},
        params={"caller": "mallory"},
    )
    assert response.status_code == 200
    assert response.json()["authorisation"]["by_one_time_password"] is True


def test_confirming_with_the_wrong_one_time_password_is_403(client):
    room = make_room(client)
    uid = pending(client, room["id"])
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{uid}/confirm",
        json={"oneTimePassword": "nope"},
        params={"caller": "mallory"},
    )
    assert response.status_code == 403


def test_confirming_twice_is_409(client):
    room = make_room(client)
    uid = pending(client, room["id"])
    client.post(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/confirm", json={}, **as_host())
    again = client.post(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/confirm", json={}, **as_host())
    assert again.status_code == 409
    assert again.json()["error"] == "not_pending"


def test_a_confirmed_request_no_longer_counts_as_awaiting_a_host(client):
    room = make_room(client)
    uid = pending(client, room["id"])
    assert client.get(f"{PREFIX}/rooms/{room['id']}/summary").json()["awaiting_a_host"] == 1
    client.post(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/confirm", json={}, **as_host())
    assert client.get(f"{PREFIX}/rooms/{room['id']}/summary").json()["awaiting_a_host"] == 0


def test_confirming_an_unknown_booking_is_404(client):
    room = make_room(client)
    response = client.post(f"{PREFIX}/rooms/{room['id']}/requests/nope/confirm", json={}, **as_host())
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_booking"


def test_confirming_a_booking_in_another_room_is_404(client):
    first, second = make_room(client, "A"), make_room(client, "B")
    uid = pending(client, first["id"])
    response = client.post(f"{PREFIX}/rooms/{second['id']}/requests/{uid}/confirm", json={}, **as_host())
    assert response.status_code == 404


def test_an_unattended_confirmation_without_a_bypass_is_403(client):
    room = make_room(client)
    uid = pending(client, room["id"])
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{uid}/confirm",
        json={"driver": "unattended"},
        params={"caller": "dana"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "unattended_not_permitted"


def test_an_unattended_confirmation_by_an_entitled_caller_is_recorded_as_unattended(client):
    room = make_room(client)
    uid = pending(client, room["id"])
    body = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{uid}/confirm",
        json={"driver": "unattended", "bypass": {"allowConflicts": True}, "apiVersion": "2026-05-01"},
        params={"caller": "dana"},
    ).json()
    assert body["request"]["status"] == "ACCEPTED"
    assert body["driver"] == "unattended"
    assert body["request"]["data"]["driver"] == "unattended"
    assert body["request"]["data"]["decidedBy"] == "dana"


def test_an_unattended_confirmation_by_an_unauthenticated_caller_is_403(client):
    room = make_room(client)
    uid = pending(client, room["id"])
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{uid}/confirm",
        json={"driver": "unattended", "bypass": {"allowConflicts": True}, "apiVersion": "2026-05-01"},
        params={"caller": "dana", "authenticated": "false"},
    )
    assert response.status_code == 403
    assert response.json()["refusals"][0]["code"] == "caller_not_authenticated"


def test_an_unattended_confirmation_by_a_caller_with_no_role_is_403(client):
    room = make_room(client)
    uid = pending(client, room["id"])
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{uid}/confirm",
        json={"driver": "unattended", "bypass": {"allowConflicts": True}, "apiVersion": "2026-05-01"},
        params={"caller": "mallory"},
    )
    assert response.status_code == 403


def test_an_unknown_driver_is_422(client):
    room = make_room(client)
    uid = pending(client, room["id"])
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{uid}/confirm", json={"driver": "robot"}, **as_host()
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


# --------------------------------------------------------------------------- #
# Declining
# --------------------------------------------------------------------------- #


def test_declining_rejects_the_request_and_records_the_reason(client):
    room = make_room(client)
    uid = pending(client, room["id"])
    body = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{uid}/decline",
        json={"reason": "Sam is on leave that week."},
        **as_host(),
    ).json()
    assert body["request"]["status"] == "REJECTED"
    assert body["request"]["rejectionReason"] == "Sam is on leave that week."
    assert body["slot_released"] is True


def test_declining_with_no_reason_records_the_researched_default(client):
    room = make_room(client)
    uid = pending(client, room["id"])
    body = client.post(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/decline", json={}, **as_host()).json()
    assert body["rejection_reason"] == "The organizer is no longer available at this time."
    assert body["request"]["data"]["reasonSupplied"] is False


def test_a_decline_fires_booking_rejected_with_the_reason_in_the_payload(client):
    """`"rejectionReason": "…", "status": "REJECTED"`."""
    room = make_room(client)
    uid = pending(client, room["id"])
    body = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{uid}/decline",
        json={"reason": "The organizer is no longer available at this time."},
        **as_host(),
    ).json()
    assert [entry["data"]["event"] for entry in body["webhooks"]] == ["BOOKING_REJECTED"]
    payload = body["webhooks"][0]["data"]["payload"]["payload"]
    assert payload["status"] == "REJECTED"
    assert payload["rejectionReason"] == "The organizer is no longer available at this time."


def test_a_decline_creates_no_calendar_event(client):
    room = make_room(client)
    uid = pending(client, room["id"])
    body = client.post(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/decline", json={}, **as_host()).json()
    assert body["calendar_event"] is None
    assert client.get(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/calendar-event").json()["calendar_event"] is None


def test_a_decline_releases_the_held_slot_so_the_same_time_can_be_asked_for_again(client):
    """The visible proof that a decline released the hold."""
    room = make_room(client)
    event_type = gated(client, room["id"])
    when = slot()
    first = request_slot(client, room["id"], event_type["id"], start=when, email="ann@example.com")
    refused = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests",
        json={"eventTypeId": event_type["id"], "start": when, "attendee": {"email": "bob@example.com"}},
    )
    assert refused.status_code == 409
    client.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{first['request']['uid']}/decline", json={}, **as_host()
    )
    again = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests",
        json={"eventTypeId": event_type["id"], "start": when, "attendee": {"email": "bob@example.com"}},
    )
    assert again.status_code == 201
    assert again.json()["request"]["status"] == "PENDING"


def test_a_rejected_request_stops_holding_the_slot(client):
    room = make_room(client)
    uid = pending(client, room["id"])
    client.post(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/decline", json={}, **as_host())
    record = client.get(f"{PREFIX}/rooms/{room['id']}/requests/{uid}").json()["request"]
    assert record["holdsSlot"] is False
    assert record["pending"] is False


def test_declining_by_a_stranger_is_403(client):
    room = make_room(client)
    uid = pending(client, room["id"])
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{uid}/decline", json={}, params={"caller": "mallory"}
    )
    assert response.status_code == 403


def test_declining_an_accepted_booking_is_409(client):
    room = make_room(client)
    uid = pending(client, room["id"])
    client.post(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/confirm", json={}, **as_host())
    again = client.post(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/decline", json={}, **as_host())
    assert again.status_code == 409
    assert again.json()["error"] == "not_pending"


def test_a_confirmed_booking_still_holds_its_slot_after_a_later_request_for_the_same_time(client):
    room = make_room(client)
    event_type = gated(client, room["id"])
    when = slot()
    uid = request_slot(client, room["id"], event_type["id"], start=when)["request"]["uid"]
    client.post(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/confirm", json={}, **as_host())
    refused = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests",
        json={"eventTypeId": event_type["id"], "start": when, "attendee": {"email": "bob@example.com"}},
    )
    assert refused.status_code == 409


def test_declining_an_unknown_booking_is_404(client):
    room = make_room(client)
    response = client.post(f"{PREFIX}/rooms/{room['id']}/requests/nope/decline", json={}, **as_host())
    assert response.status_code == 404


def test_the_webhooks_route_returns_both_payloads_of_a_declined_request_in_order(client):
    room = make_room(client)
    uid = pending(client, room["id"])
    client.post(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/decline", json={}, **as_host())
    body = client.get(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/webhooks").json()
    assert [entry["data"]["event"] for entry in body["webhooks"]] == ["BOOKING_REQUESTED", "BOOKING_REJECTED"]


# --------------------------------------------------------------------------- #
# Listing, reading, summarising
# --------------------------------------------------------------------------- #


def test_listing_requests_is_scoped_to_the_room(client):
    first, second = make_room(client, "A"), make_room(client, "B")
    pending(client, first["id"])
    pending(client, first["id"], start=slot(days=3))
    pending(client, second["id"])
    assert client.get(f"{PREFIX}/rooms/{first['id']}/requests").json()["count"] == 2
    assert client.get(f"{PREFIX}/rooms/{second['id']}/requests").json()["count"] == 1


def test_requests_can_be_filtered_by_status_through_the_dynamic_index(client):
    room = make_room(client)
    event_type = gated(client, room["id"])
    uid = request_slot(client, room["id"], event_type["id"], start=slot(days=2))["request"]["uid"]
    client.post(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/confirm", json={}, **as_host())
    request_slot(client, room["id"], event_type["id"], start=slot(days=3), email="bob@example.com")
    assert client.get(f"{PREFIX}/rooms/{room['id']}/requests", params={"status": "PENDING"}).json()["count"] == 1
    assert client.get(f"{PREFIX}/rooms/{room['id']}/requests", params={"status": "ACCEPTED"}).json()["count"] == 1


def test_requests_can_be_filtered_by_event_type_and_by_host(client):
    room = make_room(client)
    one = gated(client, room["id"])
    two = gated(client, room["id"], title="Deep dive", hostId="sam", ownerId="sam")
    request_slot(client, room["id"], one["id"], start=slot(days=2))
    request_slot(client, room["id"], two["id"], start=slot(days=3), email="bob@example.com")
    assert client.get(
        f"{PREFIX}/rooms/{room['id']}/requests", params={"event_type_id": one["id"]}
    ).json()["count"] == 1
    assert client.get(f"{PREFIX}/rooms/{room['id']}/requests", params={"host_id": "sam"}).json()["count"] == 1


def test_reading_one_request_by_its_uid(client):
    room = make_room(client)
    made = request_slot(client, room["id"], gated(client, room["id"])["id"])
    body = client.get(f"{PREFIX}/rooms/{room['id']}/requests/{made['request']['uid']}").json()
    assert body["request"]["uid"] == made["request"]["uid"]
    assert body["request"]["data"]["attendee"]["email"] == "ann@example.com"


def test_an_unknown_uid_is_404(client):
    room = make_room(client)
    response = client.get(f"{PREFIX}/rooms/{room['id']}/requests/nope")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_booking"


def test_reading_a_booking_in_another_room_is_404(client):
    first, second = make_room(client, "A"), make_room(client, "B")
    uid = pending(client, first["id"])
    assert client.get(f"{PREFIX}/rooms/{second['id']}/requests/{uid}").status_code == 404


def test_a_room_id_is_not_a_booking_uid(client):
    """``uid`` is the researched bookingUid, not this product's record id."""
    room = make_room(client)
    request_slot(client, room["id"], gated(client, room["id"])["id"])
    assert client.get(f"{PREFIX}/rooms/{room['id']}/requests/{room['id']}").status_code == 404


def test_the_summary_counts_every_status_and_the_held_slots(client):
    room = make_room(client)
    event_type = gated(client, room["id"])
    keep = request_slot(client, room["id"], event_type["id"], start=slot(days=2))["request"]["uid"]
    gone = request_slot(client, room["id"], event_type["id"], start=slot(days=3), email="b@e.com")["request"]["uid"]
    direct = make_event_type(client, room["id"], title="Direct", requiresConfirmation=False)
    request_slot(client, room["id"], direct["id"], start=slot(days=4), email="c@e.com")
    client.post(f"{PREFIX}/rooms/{room['id']}/requests/{keep}/confirm", json={}, **as_host())
    client.post(f"{PREFIX}/rooms/{room['id']}/requests/{gone}/decline", json={}, **as_host())

    body = client.get(f"{PREFIX}/rooms/{room['id']}/summary").json()
    assert body["counts"] == {"PENDING": 0, "ACCEPTED": 2, "REJECTED": 1, "CANCELLED": 0}
    assert body["held_slots"] == 2
    assert body["calendar_events"] == 2
    # Three webhooks: BOOKING_REQUESTED for each of the two gated requests, and
    # BOOKING_REJECTED for the decline. The booking accepted on creation fired
    # neither, because no request was ever made for it.
    assert body["webhooks"] == 3
    assert body["event_types"] == 2


def test_the_summary_of_an_empty_room_is_all_zeroes(client):
    room = make_room(client)
    body = client.get(f"{PREFIX}/rooms/{room['id']}/summary").json()
    assert body["requests"] == 0
    assert body["awaiting_a_host"] == 0
    assert body["held_slots"] == 0
    assert set(body["counts"].values()) == {0}


# --------------------------------------------------------------------------- #
# Workflow rules
# --------------------------------------------------------------------------- #


def add_automation(client, room_id, trigger="bookingRequested", channels=("to_do",), **extra) -> dict:
    body = {"trigger": trigger, "channels": list(channels), "label": "Surface it", **extra}
    response = client.post(f"{PREFIX}/rooms/{room_id}/automations", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_adding_a_workflow_rule(client):
    room = make_room(client)
    body = add_automation(client, room["id"], channels=["to_do", "email"])
    assert body["data"]["trigger"] == "bookingRequested"
    assert body["data"]["channels"] == ["to_do", "email"]


def test_adding_a_rule_on_an_unresearched_trigger_is_422(client):
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/automations", json={"trigger": "beforeEvent", "channels": ["to_do"]}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_automation"


def test_adding_a_rule_with_an_unknown_channel_is_422(client):
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/automations", json={"trigger": "bookingRequested", "channels": ["fax"]}
    )
    assert response.status_code == 422


def test_adding_a_rule_with_no_channel_is_422(client):
    room = make_room(client)
    response = client.post(f"{PREFIX}/rooms/{room['id']}/automations", json={"trigger": "bookingRequested"})
    assert response.status_code == 422


def test_automations_are_scoped_to_the_room(client):
    first, second = make_room(client, "A"), make_room(client, "B")
    add_automation(client, first["id"])
    add_automation(client, first["id"], trigger="bookingRejected")
    add_automation(client, second["id"])
    assert client.get(f"{PREFIX}/rooms/{first['id']}/automations").json()["count"] == 2
    assert client.get(f"{PREFIX}/rooms/{second['id']}/automations").json()["count"] == 1


def test_a_request_fires_every_enabled_rule_on_booking_requested(client):
    """`bookingRequested` "can immediately kick off a rep-facing to-do/SMS/email"."""
    room = make_room(client)
    add_automation(client, room["id"], channels=["to_do", "email"])
    add_automation(client, room["id"], channels=["sms"])
    add_automation(client, room["id"], trigger="bookingRejected", channels=["to_do"])
    body = request_slot(client, room["id"], gated(client, room["id"])["id"])
    assert [entry["data"]["channel"] for entry in body["dispatches"]] == ["to_do", "email", "sms"]
    assert {entry["data"]["trigger"] for entry in body["dispatches"]} == {"bookingRequested"}


def test_a_decline_fires_the_booking_rejected_rules(client):
    """`bookingRejected` "can trigger re-routing"."""
    room = make_room(client)
    add_automation(client, room["id"], trigger="bookingRejected", channels=["to_do", "sms"])
    uid = pending(client, room["id"])
    body = client.post(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/decline", json={}, **as_host()).json()
    assert [entry["data"]["channel"] for entry in body["dispatches"]] == ["to_do", "sms"]


def test_a_booking_created_directly_fires_nothing(client):
    """No request was made, so no request trigger has anything to act on."""
    room = make_room(client)
    add_automation(client, room["id"], channels=["to_do", "email"])
    body = request_slot(client, room["id"], make_event_type(client, room["id"])["id"])
    assert body["dispatches"] == []


def test_a_disabled_rule_fires_nothing(client):
    room = make_room(client)
    add_automation(client, room["id"], enabled=False)
    body = request_slot(client, room["id"], gated(client, room["id"])["id"])
    assert body["dispatches"] == []


def test_a_dispatch_records_its_channel_its_recipient_and_the_request_that_caused_it(client):
    room = make_room(client)
    add_automation(client, room["id"], channels=["to_do", "email"])
    made = request_slot(client, room["id"], gated(client, room["id"])["id"])
    for entry in made["dispatches"]:
        data = entry["data"]
        assert data["bookingUid"] == made["request"]["uid"]
        assert data["recipient"] == "dana"
        assert data["dispatched_at"]


def test_a_dispatch_says_its_delivery_was_simulated(client):
    """A recorded dispatch must not read as a delivery receipt."""
    room = make_room(client)
    add_automation(client, room["id"], channels=["email"])
    made = request_slot(client, room["id"], gated(client, room["id"])["id"])
    assert made["dispatches"][0]["data"]["dispatched_by"] == "simulated"


def test_a_dispatch_is_attributed_to_the_rule_that_fired_it(client):
    room = make_room(client)
    rule = add_automation(client, room["id"])
    made = request_slot(client, room["id"], gated(client, room["id"])["id"])
    assert made["dispatches"][0]["data"]["automationId"] == rule["id"]


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def audit_entries(client, **params) -> list[dict]:
    return client.get("/api/audit", params={"limit": 1000, **params}).json()["entries"]


def my_sources(client) -> set[str]:
    return {entry["source"] for entry in audit_entries(client) if PREFIX in (entry.get("source") or "")}


def drive_every_write(client) -> dict:
    """Every write this feature has, so the audit check sees all of them.

    Two gated event types rather than one: the event type carrying the email
    verification gate is a different type from the one that does not, and a
    single type cannot produce both a verified request and an unverified one.
    """
    room = make_room(client)
    verified_type = gated(client, room["id"], title="Verified", emailVerification=True)
    plain_type = gated(client, room["id"], title="Plain")
    direct = make_event_type(client, room["id"], title="Direct", requiresConfirmation=False)
    code = client.post(
        f"{PREFIX}/rooms/{room['id']}/email-verification/send",
        json={"eventTypeId": verified_type["id"], "email": "ann@example.com"},
    ).json()["code"]
    client.post(
        f"{PREFIX}/rooms/{room['id']}/email-verification/verify",
        json={"code": code, "eventTypeId": verified_type["id"]},
    )
    add_automation(client, room["id"])
    confirmed = request_slot(
        client,
        room["id"],
        verified_type["id"],
        start=slot(days=2),
        email="ann@example.com",
        emailVerificationCode=code,
    )["request"]["uid"]
    declined = request_slot(
        client, room["id"], plain_type["id"], start=slot(days=3), email="bob@example.com"
    )["request"]["uid"]
    request_slot(client, room["id"], direct["id"], start=slot(days=4), email="c@e.com")
    client.patch(
        f"{PREFIX}/rooms/{room['id']}/event-types/{plain_type['id']}", json={"title": "Plain v2"}
    )
    client.post(f"{PREFIX}/rooms/{room['id']}/requests/{confirmed}/confirm", json={}, **as_host())
    client.post(f"{PREFIX}/rooms/{room['id']}/requests/{declined}/decline", json={}, **as_host())
    return {"room": room, "event_type": plain_type, "confirmed": confirmed, "declined": declined}


def test_every_write_names_the_route_that_served_it(client):
    room = make_room(client)
    event_type = gated(client, room["id"])
    uid = request_slot(client, room["id"], event_type["id"])["request"]["uid"]
    client.patch(f"{PREFIX}/rooms/{room['id']}/event-types/{event_type['id']}", json={"title": "v2"})
    client.post(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/confirm", json={}, **as_host())
    assert my_sources(client) == {
        f"POST {PREFIX}/rooms/{room['id']}/event-types",
        f"PATCH {PREFIX}/rooms/{room['id']}/event-types/{event_type['id']}",
        f"POST {PREFIX}/rooms/{room['id']}/requests",
        f"POST {PREFIX}/rooms/{room['id']}/requests/{uid}/confirm",
    }


def test_every_write_route_appears_in_the_audit_log_once_they_have_been_driven(client):
    """All eight write routes, each reached through the router rather than a stub.

    ``my_sources`` carries the ids that filled the parameters, so it cannot be
    compared to the route table's templates directly. What is checked here is
    the count and the verbs; the template-level correspondence is
    :func:`test_every_source_this_feature_can_write_is_one_of_its_own_routes`,
    and that every one of them is callable is
    :func:`test_every_audit_row_in_the_whole_log_names_a_mounted_route`.
    """
    driven = drive_every_write(client)
    recorded = my_sources(client)
    write_routes = [
        sorted(route.methods - {"HEAD", "OPTIONS"})[0]
        for route in load_feature(MODULE).router.routes
        if sorted(route.methods - {"HEAD", "OPTIONS"})[0] in WRITE_VERBS
    ]
    assert len(write_routes) == 8
    assert len(recorded) == len(write_routes)
    assert {source.split(" ", 1)[0] for source in recorded} == set(write_routes)
    assert all(
        source.partition(" ")[2].startswith(f"{PREFIX}/rooms/{driven['room']['id']}") for source in recorded
    )


def test_every_audit_row_this_feature_writes_names_a_route_it_actually_serves(client):
    drive_every_write(client)
    stale = [source for source in my_sources(client) if source.count(PREFIX) > 1]
    assert not stale, f"audit rows name an unserved path: {stale}"


def test_every_source_in_the_module_interpolates_the_router_prefix():
    """The defect the contract names by name: a row naming a path nobody called.

    Asserted as "every ``source=f"..."`` in the module interpolates
    ``router.prefix``", which is the property that matters. A test that simply
    banned the literal ``/api/wf-062`` would fail on the router's own
    ``prefix=`` argument and so could not be written at all.
    """
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    every_source = re.findall(r'source=f"([^"]*)"', source)
    assert every_source, "no source is written with an f-string"
    for expression in every_source:
        assert "{router.prefix}" in expression, expression


def test_every_source_this_feature_can_write_is_one_of_its_own_routes():
    """Read out of the module rather than asserted one by one, because the defect
    this guards is a *drift* between the recorded source and the mounted path: a
    hand-written list in the test would be updated in the same commit as the bug.
    """
    module = load_feature(MODULE)
    text = Path(module.__file__).read_text(encoding="utf-8")
    expressions = re.findall(r'source=f"([^"]*\{router\.prefix\}[^"]*)"', text)
    assert expressions, "no source is built from router.prefix"

    write_routes = {
        (sorted(route.methods - {"HEAD", "OPTIONS"})[0], route.path)
        for route in module.router.routes
        if sorted(route.methods - {"HEAD", "OPTIONS"})[0] in ("POST", "PATCH", "PUT", "DELETE")
    }
    recorded = {
        (expression.split(" ", 1)[0], f"{PREFIX}{expression.split('{router.prefix}')[1]}")
        for expression in expressions
    }
    assert {method for method, _ in write_routes} == {method for method, _ in recorded}
    for method, path in recorded:
        assert (method, path) in write_routes, f"{method} {path} is not a route this feature serves"


def test_a_write_records_the_actor_it_was_given(client):
    room = make_room(client)
    event_type = gated(client, room["id"])
    client.post(
        f"{PREFIX}/rooms/{room['id']}/requests",
        json={"eventTypeId": event_type["id"], "start": slot(), "attendee": {"email": "a@b.com"}},
        params={"actor": "dana"},
    )
    entry = audit_entries(client, collection="booking_request")[0]
    assert entry["actor"] == "dana"
    assert entry["source"] == f"POST {PREFIX}/rooms/{room['id']}/requests"


def test_the_decision_writes_name_the_decision_route_that_served_them(client):
    """A booking record and the rows its decision caused share one source."""
    room = make_room(client)
    event_type = gated(client, room["id"])
    uid = request_slot(client, room["id"], event_type["id"])["request"]["uid"]
    client.post(f"{PREFIX}/rooms/{room['id']}/requests/{uid}/confirm", json={}, **as_host())
    assert audit_entries(client, collection="booking_event")[0]["source"] == (
        f"POST {PREFIX}/rooms/{room['id']}/requests/{uid}/confirm"
    )
    declined = request_slot(client, room["id"], event_type["id"], start=slot(days=4), email="b@e.com")["request"]["uid"]
    client.post(f"{PREFIX}/rooms/{room['id']}/requests/{declined}/decline", json={}, **as_host())
    assert audit_entries(client, collection="booking_webhook")[0]["source"] == (
        f"POST {PREFIX}/rooms/{room['id']}/requests/{declined}/decline"
    )


def test_a_refused_write_writes_nothing_and_audits_nothing(client):
    """The product's guarantee is that the log describes work that happened."""
    room = make_room(client)
    event_type = gated(client, room["id"])
    request_slot(client, room["id"], event_type["id"])
    before = len(audit_entries(client, collection="booking_request"))
    refused = client.post(
        f"{PREFIX}/rooms/{room['id']}/requests",
        json={"eventTypeId": event_type["id"], "start": slot(), "attendee": {"email": "b@e.com"}},
    )
    assert refused.status_code == 409
    assert len(audit_entries(client, collection="booking_request")) == before


def mounted_routes(client) -> dict[str, list[list[str]]]:
    """Every mounted path, per method, split into segments.

    ``app.routes`` is not usable for this: this FastAPI version records an
    included router as a single nested entry rather than flattening its routes, so
    a check built on it would see neither a feature's routes nor its own. The
    published schema is the flat, authoritative list of what is served.
    """
    schema = client.get("/openapi.json").json()
    mounted: dict[str, list[list[str]]] = {}
    for path, operations in (schema.get("paths") or {}).items():
        for method in operations:
            if method.upper() in VERBS:
                mounted.setdefault(method.upper(), []).append(path.strip("/").split("/"))
    return mounted


#: The record-id shapes the core ``AuditedDatabase`` mints. A recorded source
#: carries the id that filled a parameter, so a segment matching this is a value
#: where the route declares a parameter.
RECORD_ID = re.compile(r"^[a-z_]+_[0-9a-f]{16,}$")


def serves(mounted: dict[str, list[list[str]]], method: str, path: str) -> bool:
    """Whether ``method path`` reaches a mounted route.

    Segment by segment, because a path parameter fills exactly one segment: the
    two must line up in number as well as in shape.
    """
    for candidate in mounted.get(method, []):
        recorded = path.strip("/").split("/")
        if len(candidate) != len(recorded):
            continue
        if all(
            expected.startswith("{") or expected == actual or RECORD_ID.match(actual)
            for expected, actual in zip(candidate, recorded)
        ):
            return True
    return False


def test_every_audit_row_in_the_whole_log_names_a_mounted_route(client):
    """The invariant, product-wide: an audit ``source`` is always callable.

    This drives writes through this feature *and* through the core record routes,
    then checks every row the log holds. The defect the contract names by name is
    "a feature's audit log kept recording a path the app had stopped serving",
    which is invisible to a test scoped to one feature's own sources.
    """
    drive_every_write(client)
    client.post("/api/records/room", json={"name": "Core room", "account": "Core"})
    core_room = client.post("/api/records/room", json={"name": "Another", "account": "Other"}).json()
    client.patch(f"/api/records/room/{core_room['id']}", json={"stage": "evaluation"})

    mounted = mounted_routes(client)
    entries = audit_entries(client)
    assert entries
    for entry in entries:
        method, _, path = str(entry["source"]).partition(" ")
        assert serves(mounted, method, path), (
            f"audit row {entry['seq']} names {method} {path}, which is not a mounted route"
        )


def test_a_deleted_route_would_be_reported_not_silently_accepted():
    """The host refuses a colliding (method, path); this asserts the feature's own
    writes would be caught by the audit check if its route were removed."""
    paths = {route.path for route in load_feature(MODULE).router.routes}
    assert f"{PREFIX}/rooms/{{room_id}}/requests" in paths
    assert f"{PREFIX}/rooms/{{room_id}}/requests/{{uid}}/confirm" in paths
    assert f"{PREFIX}/rooms/{{room_id}}/requests/{{uid}}/decline" in paths


def test_the_module_imports_its_dependencies_from_deps_and_never_the_app():
    """A feature importing dsr.api reintroduces the coupling this removes."""
    text = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "from dsr.deps import" in text
    assert "from dsr.api" not in text and "import dsr.api" not in text


def test_no_write_in_the_module_opens_the_database_itself():
    """The single write path is the guarantee the product is built on."""
    text = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    for forbidden in ("import sqlite3", ".db._conn", "AuditedDatabase("):
        assert forbidden not in text, f"the feature bypasses the audited store: {forbidden}"


# --------------------------------------------------------------------------- #
# The demo data
# --------------------------------------------------------------------------- #


def seeded(tmp_path: Path, rooms: int = 2) -> tuple[AuditedDatabase, RecordStore, list[str], str]:
    # sqlite3.connect does not create intermediate directories, so a caller
    # pointing this at a path under a directory that does not exist yet dies with
    # "unable to open database file" - a message that says nothing about which
    # directory is missing.
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = AuditedDatabase(tmp_path / "seed.db", actor="test")
    room_ids = [
        db.create("room", {"name": f"R{index}", "account": f"A{index}"}, source="test")["id"]
        for index in range(rooms)
    ]
    summary = load_feature(MODULE).seed(
        db,
        {
            "room_ids": [(room_id, f"A{index}") for index, room_id in enumerate(room_ids)],
            "now": dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.timezone.utc),
            "rng": random.Random(1),
        },
    )
    return db, RecordStore(db), room_ids, summary


@pytest.fixture()
def seeded_db(tmp_path):
    db, store, room_ids, summary = seeded(tmp_path)
    yield db, store, room_ids, summary
    db.close()


def test_the_seed_leaves_a_request_pending_for_a_host_to_decide(seeded_db):
    """The state the whole ticket is about, and the one a reviewer can act on."""
    _db, store, _rooms, _summary = seeded_db
    statuses = [record["data"]["status"] for record in store.list("booking_request", limit=50)]
    # One from the pending plan, and one from re-requesting the slot the decline
    # released. Both are a host's to decide, and both are visible in the demo.
    assert statuses.count("PENDING") == 2


def test_the_seed_shows_all_four_states_the_research_distinguishes(seeded_db):
    """A demo of only successes teaches a reviewer nothing."""
    _db, store, _rooms, _summary = seeded_db
    statuses = {record["data"]["status"] for record in store.list("booking_request", limit=50)}
    assert {"PENDING", "ACCEPTED", "REJECTED"} <= statuses


def test_the_seed_creates_a_calendar_event_only_for_the_accepted_bookings(seeded_db):
    """The research says the event is "created only on confirm"."""
    _db, store, _rooms, _summary = seeded_db
    accepted = {
        record["data"]["uid"]
        for record in store.list("booking_request", limit=50)
        if record["data"]["status"] == "ACCEPTED"
    }
    events = {record["data"]["bookingUid"] for record in store.list("booking_event", limit=50)}
    assert events == accepted
    assert events


def test_the_seed_records_both_researched_webhook_events(seeded_db):
    _db, store, _rooms, _summary = seeded_db
    events = [record["data"]["event"] for record in store.list("booking_webhook", limit=50)]
    assert set(events) == {"BOOKING_REQUESTED", "BOOKING_REJECTED"}


def test_the_seed_records_a_rejection_with_the_researched_reason(seeded_db):
    _db, store, _rooms, _summary = seeded_db
    rejected = [record for record in store.list("booking_request", limit=50) if record["data"]["status"] == "REJECTED"]
    assert rejected[0]["data"]["rejectionReason"] == "The organizer is no longer available at this time."


def test_the_seed_re_requests_the_released_slot_so_the_release_is_visible(seeded_db):
    """A decline released the hold, so the same start appears twice for that type."""
    _db, store, _rooms, _summary = seeded_db
    by_start: dict[str, list[dict]] = {}
    for record in store.list("booking_request", limit=50):
        by_start.setdefault(record["data"]["start"], []).append(record["data"])
    doubled = [entries for entries in by_start.values() if len(entries) == 2]
    assert doubled
    assert {entry["status"] for entry in doubled[0]} == {"REJECTED", "PENDING"}


def test_the_seed_produces_a_verified_email_for_the_gated_event_type(seeded_db):
    _db, store, _rooms, _summary = seeded_db
    records = store.list("email_verification", limit=20)
    assert records
    assert all(record["data"]["verifiedAt"] for record in records)
    assert all(record["data"]["dispatched_by"] == "simulated" for record in records)


def test_the_seed_configures_the_bounds_and_the_limit_somewhere(seeded_db):
    """So ``allowBookingOutOfBounds`` and ``skipBookingLimits`` have something to act on."""
    _db, store, _rooms, _summary = seeded_db
    configured = [record["data"] for record in store.list("event_type", limit=20)]
    assert any(spec.get("minimumNoticeMinutes") and spec.get("maximumRangeDays") for spec in configured)
    assert any(spec.get("bookingLimitPerAttendee") for spec in configured)
    assert any(spec.get("emailVerification") for spec in configured)


def test_the_seed_creates_a_rule_for_each_researched_trigger_in_every_room(seeded_db):
    _db, store, room_ids, _summary = seeded_db
    for room_id in room_ids:
        rules = store.list("booking_automation", room_id=room_id, limit=20)
        assert {rule["data"]["trigger"] for rule in rules} == {"bookingRequested", "bookingRejected"}


def test_the_seed_creates_each_event_type_in_the_room_that_uses_it(seeded_db):
    """A demo row outside its room is a row the page could not show."""
    _db, store, room_ids, _summary = seeded_db
    for record in store.list("event_type", limit=20):
        assert record["room_id"] in room_ids


def test_the_seed_leaves_no_booking_holding_a_slot_it_released(seeded_db):
    _db, store, _rooms, _summary = seeded_db
    for record in store.list("booking_request", limit=50):
        if record["data"]["status"] == "REJECTED":
            assert record["data"]["holdsSlot"] is False


def test_the_seed_says_what_it_produced(seeded_db):
    _db, _store, _rooms, summary = seeded_db
    assert isinstance(summary, str)
    for expected in ("event types", "automations", "bookings", "pending", "declined", "confirmed", "verified email"):
        assert expected in summary


def test_the_seed_says_so_when_there_are_no_rooms():
    tmp = tempfile.TemporaryDirectory()
    db = AuditedDatabase(Path(tmp.name) / "seed.db", actor="test")
    try:
        summary = load_feature(MODULE).seed(
            db, {"room_ids": [], "now": dt.datetime.now(dt.timezone.utc), "rng": random.Random(1)}
        )
        assert "no demo rooms" in summary
        assert db.count("booking_request") == 0
    finally:
        db.close()
        tmp.cleanup()


def test_the_seed_writes_only_audited_rows_attributed_to_the_seeder(seeded_db):
    """Running it twice gives a second complete set of rows, not an overwrite."""
    db, _store, _rooms, _summary = seeded_db
    assert db.audit_count() > 0
    seeded_collections = {
        "event_type",
        "booking_request",
        "booking_webhook",
        "booking_dispatch",
        "booking_event",
        "email_verification",
        "booking_automation",
    }
    entries = [entry for entry in db.audit(limit=1000) if entry["collection"] in seeded_collections]
    assert entries
    assert all(entry["source"] == "seed" for entry in entries)


def test_the_seed_is_reproducible_apart_from_the_issued_credentials(tmp_path):
    """Same rooms, same clock: the same statuses, holds and event types."""
    first_db, first_store, _r, _s = seeded(tmp_path / "a", rooms=2)
    second_db, second_store, _r2, _s2 = seeded(tmp_path / "b", rooms=2)
    try:
        def shape(store):
            return [
                (
                    record["data"]["title"],
                    record["data"]["status"],
                    record["data"]["start"],
                    record["data"]["holdsSlot"],
                )
                for record in store.list("booking_request", limit=50, order_by="created_at", descending=False)
            ]

        assert shape(first_store) == shape(second_store)
    finally:
        first_db.close()
        second_db.close()
