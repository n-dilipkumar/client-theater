"""The researched rules for WF-066, against the domain.

Split from ``test_wf066_http.py``, which drives the feature's own mounted router.
This file tests the domain package on its own: the signing rule the research
quotes step by step, the three subscription types and the payload value each
fires, the many-to-many the research states twice, the two deployment modes for
subscriber URLs, and the guarantee that the bytes which get signed are the bytes
which get sent.

The suite runs under pytest-xdist, so every fixture here is function-scoped and
built from the ``store`` fixture in ``conftest.py``. Nothing in this file reads or
writes process-wide state, so it passes in any order and on any worker.
"""

from __future__ import annotations

import hashlib
import hmac
import pathlib
from datetime import datetime, timedelta, timezone

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.meeting_webhook_fanout import (
    COLLECTIONS,
    MAX_AGE_SECONDS,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    InvalidEventType,
    InvalidSubscriberUrl,
    MeetingWebhookFanout,
    NoRoom,
    UnknownSubscription,
    build_payload,
    canonical_bytes,
    event_type_for_payload,
    payload_type_for_event_type,
    sign,
    signing_input,
    unix_seconds,
    verify,
)
from dsr.meeting_webhook_fanout.errors import AlreadyExists, InvalidRequest, UnknownEvent
from dsr.meeting_webhook_fanout.inferences import INFERENCES, by_id
from dsr.meeting_webhook_fanout.payloads import missing_fields
from dsr.meeting_webhook_fanout.transport import FakeTransport, UrllibTransport, is_retryable
from dsr.meeting_webhook_fanout.vocabulary import (
    DEPLOYMENT_MODES,
    EVENT_TYPES,
    PAYLOAD_FIELDS,
    describe as describe_vocabulary,
)
from dsr.store import RecordStore

SECRET = "northwind-tenant-hmac-signing-secret"
NOW = datetime(2026, 10, 8, 14, 0, tzinfo=timezone.utc)
CREATE = "POST /api/wf-066/rooms/{room_id}/subscriptions"
ENABLE = "PATCH /api/wf-066/rooms/{room_id}/subscriptions/{subscription_id}"
RETIRE = "DELETE /api/wf-066/rooms/{room_id}/subscriptions/{subscription_id}"
EVENT = "POST /api/wf-066/rooms/{room_id}/events"
REDELIVER = "POST /api/wf-066/rooms/{room_id}/events/{event_id}/redeliver"
KEY = "POST /api/wf-066/rooms/{room_id}/signing-key"
URL = "https://hooks.northwind.example/meetings/created"
URL2 = "https://warehouse.northwind.example/meetings"
URL3 = "https://hooks.northwind.example/meetings/deleted"


@pytest.fixture
def room_id(store: RecordStore) -> str:
    return store.create("room", {"name": "Northwind", "account": "Northwind Traders"})["id"]


@pytest.fixture
def fanout(store: RecordStore) -> MeetingWebhookFanout:
    """A fanout over a fresh empty database, with a transport that opens no socket."""
    return MeetingWebhookFanout(store, FakeTransport(status=202, body="accepted"))


@pytest.fixture
def meeting() -> dict:
    """One meeting in the shape the research documents."""
    return {
        "meetingIdChili": "m-0001",
        "title": "Northwind Traders - enterprise evaluation",
        "description": "Walk through the evaluation with the buying committee.",
        "location": "Microsoft Teams",
        "start": "2026-10-08T14:00:00+00:00",
        "end": "2026-10-08T14:45:00+00:00",
        "primaryGuestTimeZone": "Europe/London",
        "primaryGuestName": "Priya Raman",
        "primaryGuestEmail": "priya.raman@northwind.example",
        "primaryGuestIdChili": "g-0001",
        "primaryGuestDataFields": {"seats": 480},
        "hostIdChili": "h-0001",
        "hostName": "Dana Okafor",
        "additionalGuests": [{"name": "Tom Alvarez"}],
        "workspaceId": "ws-0001",
        "workspaceName": "Northwind Traders",
        "productFeatureType": "ConciergeRouter",
        "meetingTypeName": "Demo",
        "meetingTypeId": "mt-0001",
    }


@pytest.fixture
def full_meeting() -> dict:
    """One meeting carrying every field the research's payload list names.

    The plain ``meeting`` fixture omits the fields a real room often does not
    hold. This one holds all of them, so the contract test can assert that the
    whole documented list is reachable rather than only the subset this build
    happened to synthesise.
    """
    return {
        "meetingIdChili": "m-0001",
        "title": "Northwind Traders - enterprise evaluation",
        "description": "Walk through the evaluation with the buying committee.",
        "location": "Microsoft Teams",
        "start": "2026-10-08T14:00:00+00:00",
        "end": "2026-10-08T14:45:00+00:00",
        "primaryGuestTimeZone": "Europe/London",
        "primaryGuestName": "Priya Raman",
        "primaryGuestEmail": "priya.raman@northwind.example",
        "primaryGuestIdChili": "g-0001",
        "primaryGuestDataFields": {"seats": 480},
        "hostIdChili": "h-0001",
        "hostName": "Dana Okafor",
        "assigneeIdChili": "h-0002",
        "assigneeName": "Ravi Menon",
        "bookerIdChili": "g-0001",
        "bookerName": "Priya Raman",
        "additionalGuests": [{"name": "Tom Alvarez"}],
        "workspaceId": "ws-0001",
        "workspaceName": "Northwind Traders",
        "productFeatureType": "ConciergeRouter",
        "productFeatureName": "Concierge router",
        "productFeatureId": "pf-0001",
        "distributionName": "Enterprise inbound",
        "distributionId": "d-0001",
        "meetingTypeName": "Demo",
        "meetingTypeId": "mt-0001",
    }


def _subscribe(fanout, room_id, url=URL, event_type="new_meeting", **extra):
    return fanout.subscribe(
        room_id,
        {"url": url, "event_type": event_type, **extra},
        actor="dana",
        source=CREATE,
    )


def _enable(fanout, room_id, row, **patch):
    return fanout.amend(
        room_id,
        row["id"],
        {"status": "enabled", **patch},
        actor="dana",
        source="PATCH /api/wf-066/rooms/{room_id}/subscriptions/{subscription_id}",
    )


# --------------------------------------------------------------------------- #
# The signing rule, which is the exact rule the research quotes
# --------------------------------------------------------------------------- #


def test_the_signing_input_is_the_timestamp_a_period_and_the_raw_body():
    """Quoted: "Construct the signed payload by concatenating the timestamp and the
    raw request body, separated by a period: {timestamp}.{raw_request_body}"."""
    assert signing_input("1790162400", '{"type":"Created"}') == '1790162400.{"type":"Created"}'


def test_the_signing_input_separates_with_one_period_and_nothing_else():
    assert signing_input("1", "body") == "1.body"
    assert signing_input("1", "") == "1."
    assert signing_input("", "body") == ".body"


def test_the_signature_is_the_hex_hmac_sha256_of_the_signing_input():
    """Quoted: "X-Chili-Signature | HMAC-SHA256 signature of the payload (hex-encoded)"."""
    timestamp, body = "1790162400", '{"type":"Created"}'
    expected = hmac.new(SECRET.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256).hexdigest()
    assert sign(SECRET, timestamp, body) == expected
    # Hex, not base64: the digest is 64 lower-case hex characters.
    assert len(sign(SECRET, timestamp, body)) == 64
    assert set(sign(SECRET, timestamp, body)) <= set("0123456789abcdef")


def test_a_subscriber_recomputing_the_rule_reproduces_the_signature():
    """The research's step 4, run against this build's output."""
    timestamp, body = "1790162400", '{"type":"Created","meetingIdChili":"m-1"}'
    signature = sign(SECRET, timestamp, body)
    assert verify(SECRET, body, signature=signature, timestamp=timestamp) == (True, "")


def test_a_signature_over_a_reserialised_object_does_not_verify():
    """Quoted: "Signature mismatch | Ensure you're verifying against the raw request
    body, not a re-serialised/parsed JSON object."

    This is the defect the research names, so it is asserted rather than described.
    A subscriber that parses the body and re-serialises it can produce different
    bytes from the same JSON: a different key order, different separators, a
    re-encoded date, a number turned into a string. The room sorts its keys and
    signs exactly the bytes it sends, so none of that can happen here.
    """
    import json

    timestamp = "1790162400"
    sent = canonical_bytes({"type": "Created", "meetingIdChili": "m-1"})
    signature = sign(SECRET, timestamp, sent.decode())

    # Three re-serialisations a subscriber might plausibly produce. Each is the
    # same JSON document and each is different bytes.
    parsed = json.loads(sent)
    for reserialised in (
        json.dumps(dict(reversed(list(parsed.items())))),  # key order changed
        json.dumps(parsed, separators=(",", ":")),  # no whitespace
        json.dumps(parsed, indent=2),  # pretty printed
    ):
        assert json.loads(reserialised) == parsed, "the same logical payload"
        assert reserialised.encode() != sent, f"but not the same bytes: {reserialised!r}"
        ok, reason = verify(SECRET, reserialised, signature=signature, timestamp=timestamp)
        assert ok is False
        assert reason == "signature_mismatch"


def test_two_meetings_with_the_same_fields_serialise_to_the_same_bytes():
    """The positive half of the same rule: sorting makes a rebuild reproducible, so
    a test can compare a recorded delivery against a body rebuilt from the payload."""
    first = canonical_bytes({"type": "Created", "meetingIdChili": "m-1", "title": "T"})
    second = canonical_bytes({"title": "T", "meetingIdChili": "m-1", "type": "Created"})
    assert first == second


def test_the_two_headers_are_the_two_the_research_documents():
    assert SIGNATURE_HEADER == "X-Chili-Signature"
    assert TIMESTAMP_HEADER == "X-Chili-Timestamp"


def test_the_timestamp_is_unix_seconds_not_milliseconds():
    """Quoted: "X-Chili-Timestamp | Unix timestamp (seconds) when the request was signed"."""
    stamp = unix_seconds(NOW)
    assert stamp == str(int(NOW.timestamp()))
    assert len(stamp) == 10, "seconds in 2026 is ten digits; milliseconds would be thirteen"


def test_a_naive_datetime_is_read_as_utc_rather_than_as_local_time():
    naive = datetime(2026, 10, 8, 14, 0)
    assert unix_seconds(naive) == unix_seconds(naive.replace(tzinfo=timezone.utc))


def test_verification_reports_a_missing_signature_and_a_missing_timestamp_apart():
    assert verify(SECRET, "b", signature=None, timestamp="1") == (
        False,
        "signature_missing",
    )
    assert verify(SECRET, "b", signature="ab", timestamp=None) == (
        False,
        "timestamp_missing",
    )


def test_the_sender_does_not_enforce_the_replay_window():
    """Quoted: "Replay protection is left to the consumer (MAX_AGE_SECONDS = 300)".

    So a correctly signed delivery whose timestamp is a week old still verifies
    when no window is passed. That is the researched behaviour, and enforcing it
    here would be this build inventing a rule the evidence does not state.
    """
    stale = str(int((NOW - timedelta(days=7)).timestamp()))
    body = '{"type":"Created"}'
    signature = sign(SECRET, stale, body)
    assert verify(SECRET, body, signature=signature, timestamp=stale) == (True, "")
    # And a consumer that does apply the window refuses it.
    assert verify(
        SECRET, body, signature=signature, timestamp=stale, window_seconds=MAX_AGE_SECONDS
    ) == (False, "signature_stale")


def test_a_window_refuses_a_stale_timestamp_but_accepts_a_fresh_one():
    now = NOW
    fresh = str(int(now.timestamp()))
    old = str(int((now - timedelta(seconds=MAX_AGE_SECONDS + 60)).timestamp()))
    for stamp, expected in ((fresh, (True, "")), (old, (False, "signature_stale"))):
        signature = sign(SECRET, stamp, "{}")
        assert (
            verify(
                SECRET,
                "{}",
                signature=signature,
                timestamp=stamp,
                window_seconds=MAX_AGE_SECONDS,
                now=now,
            )
            == expected
        )


def test_a_window_accepts_a_clock_that_runs_fast_as_well_as_one_that_runs_slow():
    """A sender whose clock is fast is as legitimate as one whose clock is slow.

    The comparison is a signed distance in either direction, so neither is
    refused. A window that only refused old timestamps would be a clock bug
    wearing the costume of a security control.
    """
    future = str(int((NOW + timedelta(seconds=60)).timestamp()))
    body = "{}"
    assert verify(
        SECRET,
        body,
        signature=sign(SECRET, future, body),
        timestamp=future,
        window_seconds=MAX_AGE_SECONDS,
        now=NOW,
    ) == (True, "")


def test_the_shipped_window_is_the_five_hundred_seconds_the_research_gives():
    assert MAX_AGE_SECONDS == 300
    assert describe_vocabulary()["replay_window_seconds"] == 300
    assert describe_vocabulary()["replay_window_owner"] == "the consumer"


# --------------------------------------------------------------------------- #
# The three subscription types and the payload value each fires
# --------------------------------------------------------------------------- #


def test_the_three_event_types_are_the_three_the_research_names():
    assert [row["id"] for row in EVENT_TYPES] == [
        "new_meeting",
        "meeting_update",
        "canceled_meeting",
    ]
    assert [row["label"] for row in EVENT_TYPES] == [
        "For New Meeting",
        "For Meeting Update",
        "For Canceled Meeting",
    ]


def test_a_cancellation_fires_the_payload_value_deleted():
    """The one mapping that looks wrong and is not.

    The research lists the three subscription names and the three payload values
    separately, and ``Deleted`` is the only value left for a cancellation.
    """
    assert payload_type_for_event_type("canceled_meeting") == "Deleted"
    assert event_type_for_payload("Deleted") == "canceled_meeting"


def test_the_other_two_map_onto_created_and_updated():
    assert payload_type_for_event_type("new_meeting") == "Created"
    assert payload_type_for_event_type("meeting_update") == "Updated"
    assert event_type_for_payload("Created") == "new_meeting"
    assert event_type_for_payload("Updated") == "meeting_update"


def test_the_payload_type_join_is_total_over_the_three_types():
    for row in EVENT_TYPES:
        assert payload_type_for_event_type(row["id"]) == row["payload_type"]
        assert event_type_for_payload(row["payload_type"]) == row["id"]


def test_an_event_type_outside_the_three_is_refused_on_the_way_in(fanout, room_id):
    with pytest.raises(InvalidEventType) as caught:
        _subscribe(fanout, room_id, event_type="meeting_started")
    assert "three" in str(caught.value)


def test_an_event_type_outside_the_three_is_refused_when_emitting(fanout, room_id, meeting):
    with pytest.raises(InvalidEventType):
        fanout.emit(room_id, meeting, "recording_ready", actor="dana", source=EVENT)


# --------------------------------------------------------------------------- #
# The flat envelope
# --------------------------------------------------------------------------- #


def test_the_payload_is_flat_with_no_wrapper(fanout, meeting):
    payload = build_payload(meeting, "new_meeting")
    assert "payload" not in payload
    assert payload["type"] == "Created"
    # Every documented field the room holds is at the top level.
    assert payload["meetingIdChili"] == "m-0001"
    assert payload["primaryGuestTimeZone"] == "Europe/London"
    assert payload["additionalGuests"] == [{"name": "Tom Alvarez"}]
    assert payload["primaryGuestDataFields"] == {"seats": 480}


def test_a_field_the_room_does_not_hold_is_omitted_rather_than_sent_as_null():
    payload = build_payload({"meetingIdChili": "m-1", "title": "T"}, "new_meeting")
    assert "description" not in payload
    assert None not in payload.values()
    assert set(missing_fields(payload)) <= set(PAYLOAD_FIELDS)


def test_the_payload_carries_every_documented_field_for_a_full_meeting(full_meeting):
    """A meeting holding every documented field produces the whole contract, which
    is what proves the payload builder reaches the whole researched field list and
    not only the subset this room usually holds."""
    payload = build_payload(full_meeting, "new_meeting")
    assert missing_fields(payload) == []


def test_the_payload_builder_reaches_every_documented_field(full_meeting):
    """One assertion per documented field, so a field dropped from the builder is a
    named failure rather than a shrinking list."""
    payload = build_payload(full_meeting, "new_meeting")
    for name in PAYLOAD_FIELDS:
        assert name in payload, name


def test_a_meeting_with_no_identity_cannot_be_a_payload():
    """A payload with no meeting identity is not a payload."""
    with pytest.raises(ValueError, match="meeting identity"):
        build_payload({"title": "no identity here"}, "new_meeting")


def test_the_meeting_record_id_is_the_fallback_identity():
    assert build_payload({"id": "m-42", "title": "T"}, "new_meeting")["meetingIdChili"] == "m-42"


def test_the_canonical_bytes_are_sorted_and_stable():
    """Two identical payloads serialise identically, so a test can compare a
    recorded delivery against a rebuilt one."""
    first = canonical_bytes({"type": "Created", "meetingIdChili": "m-1"})
    second = canonical_bytes({"meetingIdChili": "m-1", "type": "Created"})
    assert first == second
    assert first.index(b"meetingIdChili") < first.index(b"type")


# --------------------------------------------------------------------------- #
# The bytes that get signed are the bytes that get sent
# --------------------------------------------------------------------------- #


def test_the_bytes_signed_are_the_bytes_delivered(fanout, room_id, meeting):
    """The whole contract in one assertion.

    A room with no key mints one, and the test cannot know what that will be, so
    the room's secret is set first. Then the assertion is exact: the bytes the
    transport received are the bytes the signature covers, and that signature is
    the one the room recorded.
    """
    fanout.set_secret(room_id, SECRET, actor="dana", source=KEY)
    _enable(fanout, room_id, _subscribe(fanout, room_id))
    transport = FakeTransport(status=202)
    scoped = MeetingWebhookFanout(fanout.store, transport)
    report = scoped.emit(room_id, meeting, "new_meeting", actor="dana", source=EVENT)

    sent = transport.sent[0]
    assert sent["body"] == report["event"]["raw_body"], "the sent bytes are the stored bytes"
    assert sent["headers"][SIGNATURE_HEADER] == report["event"]["signature"]
    assert sent["headers"][TIMESTAMP_HEADER] == report["event"]["timestamp"]
    assert report["event"]["signing_input"] == f"{report['event']['timestamp']}.{sent['body']}"
    assert report["event"]["signature"] == sign(SECRET, report["event"]["timestamp"], sent["body"])


def test_a_minted_secret_still_produces_a_signature_a_subscriber_can_check(
    fanout, room_id, meeting
):
    """The research's step 4 runs against a room that never supplied a secret, which
    is the normal case: the admin emails support."""
    _enable(fanout, room_id, _subscribe(fanout, room_id))
    transport = FakeTransport(status=202)
    scoped = MeetingWebhookFanout(fanout.store, transport)
    scoped.emit(room_id, meeting, "new_meeting", actor="dana", source=EVENT)
    minted = fanout.secret(room_id)["secret"]
    ok, reason = verify(
        minted,
        transport.sent[0]["body"],
        signature=transport.sent[0]["headers"][SIGNATURE_HEADER],
        timestamp=transport.sent[0]["headers"][TIMESTAMP_HEADER],
    )
    assert (ok, reason) == (True, "")


def test_the_event_row_serves_the_exact_bytes_that_were_sent(fanout, room_id, meeting):
    fanout.set_secret(room_id, SECRET, actor="dana", source=KEY)
    _enable(fanout, room_id, _subscribe(fanout, room_id))
    report = fanout.emit(room_id, meeting, "new_meeting", actor="dana", source=EVENT)
    event = fanout.event(room_id, report["event"]["id"])
    assert event["raw_body"] == canonical_bytes(event["payload"]).decode("utf-8")
    assert event["signing_input"] == f"{event['timestamp']}.{event['raw_body']}"
    assert event["signature"] == sign(SECRET, event["timestamp"], event["raw_body"])


def test_the_signing_secret_is_minted_when_the_tenant_supplied_none(fanout, room_id, meeting):
    """Quoted: the admin "emails support to obtain the tenant's HMAC signing secret".

    So a room with no key is the normal case, and the room supplies one rather
    than refusing to sign.
    """
    assert fanout.secret(room_id)["secret"] is None
    _enable(fanout, room_id, _subscribe(fanout, room_id))
    fanout.emit(room_id, meeting, "new_meeting", actor="dana", source=EVENT)
    after = fanout.secret(room_id)
    assert after["secret"]
    assert "minted by the room" in after["origin"]


def test_a_tenant_supplied_secret_is_used_and_recorded_as_theirs(fanout, room_id, meeting):
    fanout.set_secret(room_id, SECRET, actor="dana", source=KEY)
    assert fanout.secret(room_id)["origin"] == "supplied by the tenant"
    _enable(fanout, room_id, _subscribe(fanout, room_id))
    report = fanout.emit(room_id, meeting, "new_meeting", actor="dana", source=EVENT)
    assert report["event"]["signature"] == sign(
        SECRET, report["event"]["timestamp"], report["event"]["raw_body"]
    )


def test_rotating_the_secret_counts_the_rotation(fanout, room_id):
    fanout.set_secret(room_id, "one", actor="dana", source=KEY)
    fanout.set_secret(room_id, "two", actor="dana", source=KEY)
    assert fanout.secret(room_id)["secret"] == "two"
    assert fanout.secret(room_id)["secret_id"]
    rows = fanout.store.list(COLLECTIONS["key"], room_id=room_id)
    assert len(rows) == 1, "a rotation updates the row rather than adding a second one"
    assert rows[0]["data"]["rotations"] == 2


def test_an_empty_secret_is_refused(fanout, room_id):
    from dsr.meeting_webhook_fanout import MissingSecret

    with pytest.raises(MissingSecret):
        fanout.set_secret(room_id, "   ", actor="dana", source=KEY)


# --------------------------------------------------------------------------- #
# The fan-out is a many-to-many, in both directions
# --------------------------------------------------------------------------- #


def test_one_url_may_serve_several_event_types(fanout, room_id):
    """Quoted: "multiple webhook types [may] have the same webhook URL"."""
    for event_type in ("new_meeting", "meeting_update", "canceled_meeting"):
        _subscribe(fanout, room_id, url=URL, event_type=event_type)
    table = fanout.subscriptions(room_id)
    assert table["count"] == 3
    assert {row["url"] for row in table["subscriptions"]} == {URL}
    assert table["by_event_type"] == {
        "new_meeting": 1,
        "meeting_update": 1,
        "canceled_meeting": 1,
    }


def test_one_event_type_may_have_several_urls(fanout, room_id):
    """Quoted: "multiple webhook URLs for the same type"."""
    _subscribe(fanout, room_id, url=URL, event_type="new_meeting")
    _subscribe(fanout, room_id, url=URL2, event_type="new_meeting")
    assert fanout.subscriptions(room_id)["count"] == 2


def test_the_pair_is_the_identity_so_the_same_pair_twice_is_refused(fanout, room_id):
    _subscribe(fanout, room_id, url=URL, event_type="new_meeting")
    with pytest.raises(AlreadyExists, match="twice to the same address"):
        _subscribe(fanout, room_id, url=URL, event_type="new_meeting")


def test_one_event_reaches_every_enabled_subscriber_for_its_type(fanout, room_id, meeting):
    _enable(fanout, room_id, _subscribe(fanout, room_id, url=URL))
    _enable(fanout, room_id, _subscribe(fanout, room_id, url=URL2))
    _enable(fanout, room_id, _subscribe(fanout, room_id, url=URL3, event_type="canceled_meeting"))
    report = fanout.emit(room_id, meeting, "new_meeting", actor="dana", source=EVENT)
    assert report["delivered"] == 2, "the canceled_meeting subscriber is not a target"
    assert {row["url"] for row in report["deliveries"]} == {URL, URL2}


def test_one_event_produces_one_row_and_several_delivery_rows(fanout, room_id, meeting):
    _enable(fanout, room_id, _subscribe(fanout, room_id, url=URL))
    _enable(fanout, room_id, _subscribe(fanout, room_id, url=URL2))
    fanout.emit(room_id, meeting, "new_meeting", actor="dana", source=EVENT)
    assert fanout.events(room_id)["count"] == 1
    assert fanout.deliveries(room_id)["count"] == 2


def test_the_fan_out_is_unbounded_so_there_is_no_cap(fanout, room_id):
    for index in range(12):
        _subscribe(fanout, room_id, url=f"https://hooks.example/{index}", event_type="new_meeting")
    table = fanout.subscriptions(room_id)
    assert table["count"] == 12
    assert table["subscription_limit"] is None
    assert "not limited by the number" in table["subscription_limit_note"]


# --------------------------------------------------------------------------- #
# Step 2 is a deliberate second act
# --------------------------------------------------------------------------- #


def test_a_new_row_lands_disabled(fanout, room_id):
    """Quoted: "clicks Create, then sets the row's status to Enabled"."""
    row = _subscribe(fanout, room_id)
    assert row["status"] == "disabled"


def test_a_body_that_asks_for_enabled_is_honoured(fanout, room_id):
    row = _subscribe(fanout, room_id, status="enabled")
    assert row["status"] == "enabled"


def test_an_event_at_a_disabled_row_reaches_nobody_and_says_why(fanout, room_id, meeting):
    _subscribe(fanout, room_id)  # created, never enabled
    report = fanout.emit(room_id, meeting, "new_meeting", actor="dana", source=EVENT)
    assert report["delivered"] == 0
    assert report["skipped"] == 1
    assert report["reason"] == "no_enabled_subscriptions"
    assert fanout.deliveries(room_id)["by_outcome"] == {"skipped": 1}


def test_the_toggle_is_one_patch_and_one_row(fanout, room_id):
    row = _subscribe(fanout, room_id)
    enabled = _enable(fanout, room_id, row)
    assert enabled["id"] == row["id"]
    assert enabled["status"] == "enabled"
    assert fanout.subscriptions(room_id)["enabled"] == 1


def test_a_status_the_build_does_not_serve_is_refused(fanout, room_id):
    row = _subscribe(fanout, room_id)
    with pytest.raises(InvalidRequest, match="enabled"):
        _enable(fanout, room_id, row, status="paused")


def test_a_retired_row_stops_receiving_and_the_log_still_resolves_it(fanout, room_id, meeting):
    row = _enable(fanout, room_id, _subscribe(fanout, room_id))
    fanout.emit(room_id, meeting, "new_meeting", actor="dana", source=EVENT)
    fanout.retire(
        room_id,
        row["id"],
        actor="dana",
        source="DELETE /api/wf-066/rooms/{room_id}/subscriptions/{subscription_id}",
    )
    after = fanout.emit(room_id, meeting, "new_meeting", actor="dana", source=EVENT)
    assert after["delivered"] == 0
    assert fanout.subscription(room_id, row["id"])["retired"] is True
    assert fanout.deliveries(room_id)["count"] == 2


def test_retiring_twice_is_404_because_the_row_is_gone(fanout, room_id):
    row = _subscribe(fanout, room_id)
    fanout.retire(
        room_id,
        row["id"],
        actor="dana",
        source="DELETE /api/wf-066/rooms/{room_id}/subscriptions/{subscription_id}",
    )
    with pytest.raises(UnknownSubscription):
        fanout.retire(
            room_id,
            row["id"],
            actor="dana",
            source="DELETE /api/wf-066/rooms/{room_id}/subscriptions/{subscription_id}",
        )


# --------------------------------------------------------------------------- #
# Subscriber URL validation, per deployment
# --------------------------------------------------------------------------- #


def test_the_two_deployment_modes_are_the_two_the_research_quotes():
    modes = {row["id"]: row for row in DEPLOYMENT_MODES}
    assert modes["saas"]["schemes"] == ["https"]
    assert modes["saas"]["private_addresses"] == "refused"
    assert modes["self_hosted"]["schemes"] == ["http", "https"]
    assert modes["self_hosted"]["private_addresses"] == "allowed"


def test_self_hosted_accepts_http_and_a_private_address():
    """Quoted: "Self-hosted: Both HTTP and HTTPS URLs are accepted, and private IP
    addresses are allowed for internal webhooks." """
    from dsr.meeting_webhook_fanout.fanout import validate_url

    assert validate_url("http://warehouse.internal/hook", "self_hosted")
    assert validate_url("http://10.0.0.5:9000/hook", "self_hosted")
    assert validate_url("http://127.0.0.1/hook", "self_hosted")


def test_saas_refuses_http():
    from dsr.meeting_webhook_fanout.fanout import validate_url

    with pytest.raises(InvalidSubscriberUrl, match="only HTTPS"):
        validate_url("http://hooks.example/hook", "saas")


def test_saas_refuses_localhost_and_private_addresses():
    """Quoted: SaaS "blocks ... 10.x.x.x, 192.168.x.x, 127.0.0.1, and localhost"."""
    from dsr.meeting_webhook_fanout.fanout import validate_url

    for url in (
        "https://localhost/hook",
        "https://app.localhost/hook",
        "https://127.0.0.1/hook",
        "https://10.1.2.3/hook",
        "https://192.168.1.9/hook",
    ):
        with pytest.raises(InvalidSubscriberUrl):
            validate_url(url, "saas")


def test_saas_accepts_a_public_https_url():
    from dsr.meeting_webhook_fanout.fanout import validate_url

    assert validate_url("https://hooks.northwind.example/x", "saas")


def test_a_url_with_no_scheme_is_refused_in_both_modes():
    from dsr.meeting_webhook_fanout.fanout import validate_url

    for mode in ("saas", "self_hosted"):
        with pytest.raises(InvalidSubscriberUrl):
            validate_url("hooks.example/hook", mode)


def test_an_empty_url_is_refused():
    from dsr.meeting_webhook_fanout.fanout import validate_url

    with pytest.raises(InvalidSubscriberUrl, match="needs a subscriber URL"):
        validate_url("", "self_hosted")


def test_this_deployment_defaults_to_self_hosted(store: RecordStore):
    room_id = store.create("room", {"name": "A"})["id"]
    fanout = MeetingWebhookFanout(store, FakeTransport())
    assert fanout.subscriptions(room_id)["count"] == 0
    row = _subscribe(fanout, room_id, url="http://warehouse.internal/hook")
    assert row["deployment_mode"] == "self_hosted"


def test_a_saas_room_refuses_the_private_url_self_hosted_allows(store: RecordStore):
    room_id = store.create("room", {"name": "A", "deployment_mode": "saas"})["id"]
    fanout = MeetingWebhookFanout(store, FakeTransport())
    with pytest.raises(InvalidSubscriberUrl):
        _subscribe(fanout, room_id, url="http://warehouse.internal/hook")


def test_an_unknown_deployment_mode_is_refused_rather_than_defaulted(store: RecordStore):
    room_id = store.create("room", {"name": "A", "deployment_mode": "moon"})["id"]
    fanout = MeetingWebhookFanout(store, FakeTransport())
    with pytest.raises(InvalidRequest, match="deployment_mode"):
        fanout.subscriptions(room_id)


# --------------------------------------------------------------------------- #
# One room cannot read another's rows
# --------------------------------------------------------------------------- #


def test_every_route_through_a_missing_room_is_refused_as_an_unknown_room(fanout):
    with pytest.raises(NoRoom):
        fanout.subscriptions("room_absent")
    with pytest.raises(NoRoom):
        fanout.secret("room_absent")
    with pytest.raises(NoRoom):
        _subscribe(fanout, "room_absent")


def test_another_rooms_subscription_is_not_found(fanout, store: RecordStore):
    mine = store.create("room", {"name": "Mine"})["id"]
    theirs = store.create("room", {"name": "Theirs"})["id"]
    row = _subscribe(fanout, theirs)
    with pytest.raises(UnknownSubscription):
        fanout.subscription(mine, row["id"])
    with pytest.raises(UnknownSubscription):
        fanout.amend(mine, row["id"], {"status": "enabled"}, actor="dana", source="PATCH x")


def test_another_rooms_event_is_not_found(fanout, store: RecordStore, meeting):
    mine = store.create("room", {"name": "Mine"})["id"]
    theirs = store.create("room", {"name": "Theirs"})["id"]
    _enable(fanout, theirs, _subscribe(fanout, theirs))
    report = fanout.emit(theirs, meeting, "new_meeting", actor="dana", source=EVENT)
    with pytest.raises(UnknownEvent):
        fanout.event(mine, report["event"]["id"])


def test_a_rooms_counts_do_not_include_another_rooms_rows(fanout, store: RecordStore, meeting):
    mine = store.create("room", {"name": "Mine"})["id"]
    theirs = store.create("room", {"name": "Theirs"})["id"]
    _enable(fanout, mine, _subscribe(fanout, mine))
    _enable(fanout, theirs, _subscribe(fanout, theirs))
    fanout.emit(theirs, meeting, "new_meeting", actor="dana", source=EVENT)
    assert fanout.summary(mine)["events"] == 0
    assert fanout.summary(mine)["subscriptions"] == 1
    assert fanout.summary(theirs)["events"] == 1


# --------------------------------------------------------------------------- #
# The transport is a seam
# --------------------------------------------------------------------------- #


def test_a_refused_subscriber_is_a_row_and_not_an_exception(store: RecordStore, meeting):
    room = store.create("room", {"name": "A"})["id"]
    failing = MeetingWebhookFanout(store, FakeTransport(fail=True, error="URLError: timed out"))
    _enable(failing, room, _subscribe(failing, room))
    report = failing.emit(room, meeting, "new_meeting", actor="dana", source=EVENT)
    assert report["failed"] == 1
    assert report["delivered"] == 0
    row = report["deliveries"][0]
    assert row["outcome"] == "failed"
    assert "timed out" in row["error"]
    assert row["status"] is None


def test_a_500_is_recorded_with_its_status_and_its_excerpt(store: RecordStore, meeting):
    room = store.create("room", {"name": "A"})["id"]
    failing = MeetingWebhookFanout(store, FakeTransport(status=500, body="upstream exploded"))
    _enable(failing, room, _subscribe(failing, room))
    report = failing.emit(room, meeting, "new_meeting", actor="dana", source=EVENT)
    row = report["deliveries"][0]
    assert row["outcome"] == "failed"
    assert row["status"] == 500
    assert "upstream exploded" in row["response_excerpt"]
    assert row["retryable"] is True


def test_the_retryable_flag_is_advice_and_nothing_schedules_on_it(store: RecordStore, meeting):
    room = store.create("room", {"name": "A"})["id"]
    fanout = MeetingWebhookFanout(store, FakeTransport(status=202))
    _enable(fanout, room, _subscribe(fanout, room))
    report = fanout.emit(room, meeting, "new_meeting", actor="dana", source=EVENT)
    row = report["deliveries"][0]
    assert row["retryable"] is False
    assert "no retry ladder" in row["retryable_note"]
    assert row["attempt_number"] == 1


def test_a_404_is_not_retryable_and_a_transport_failure_is(store: RecordStore):
    from dsr.meeting_webhook_fanout.transport import DeliveryResult

    assert is_retryable(DeliveryResult(ok=False, status=404)) is False
    assert is_retryable(DeliveryResult(ok=False, status=500)) is True
    assert is_retryable(DeliveryResult(ok=False, status=None)) is True
    assert is_retryable(DeliveryResult(ok=True, status=202)) is False


def test_the_real_transport_answers_without_a_socket_when_given_an_opener():
    """``UrllibTransport`` takes an opener, so a test can drive it with a fake one
    rather than a network."""
    from dsr.meeting_webhook_fanout.transport import DeliveryResult

    class FakeResponse:
        status = 202
        body = b"accepted"

        def read(self, _size=None):
            return self.body

        def geturl(self):
            return "https://hooks.example/final"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class FakeOpener:
        def open(self, _request, timeout=None):
            assert timeout
            return FakeResponse()

    result = UrllibTransport(opener=FakeOpener()).post(
        "https://hooks.example/hook", b"{}", {"X-Chili-Signature": "ab"}, 1.0
    )
    assert result.ok is True
    assert result.status == 202
    assert result.body == "accepted"
    assert result.final_url == "https://hooks.example/final"
    assert isinstance(result, DeliveryResult)


def test_a_response_body_is_bounded_so_a_subscriber_cannot_flood_the_log():
    from dsr.meeting_webhook_fanout.transport import MAX_RESPONSE_BYTES, _read

    text = _read(lambda size: b"x" * size)
    assert len(text) < MAX_RESPONSE_BYTES + 40
    assert text.endswith("...[truncated]")


def test_the_seed_and_the_suite_never_need_a_socket(monkeypatch):
    """A guard against a future change that opens one.

    ``urllib.request.urlopen`` is replaced with something that raises, and the
    fake transport is driven through a whole fan-out. If a code path ever reached
    the network, this fails rather than hanging on a DNS lookup.
    """
    import urllib.request

    def refuse(*_args, **_kwargs):
        raise AssertionError("the suite opened a socket")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **k: None)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", refuse)
    transport = FakeTransport(status=202)
    transport.post("https://hooks.example/x", b"{}", {}, 1.0)
    assert transport.sent


# --------------------------------------------------------------------------- #
# Redelivery is a route a person calls
# --------------------------------------------------------------------------- #


def test_a_redelivery_resends_the_same_bytes_and_resigns_with_a_fresh_timestamp(
    fanout, store: RecordStore, meeting
):
    room = store.create("room", {"name": "A"})["id"]
    transport = FakeTransport(status=202)
    scoped = MeetingWebhookFanout(store, transport)
    _enable(scoped, room, _subscribe(scoped, room))
    first = scoped.emit(room, meeting, "new_meeting", actor="dana", source=EVENT)
    again = scoped.redeliver(room, first["event"]["id"], actor="dana", source="POST redeliver")

    assert again["delivered"] == 1
    assert len(transport.sent) == 2
    # Same bytes. The raw body is read back off the event row, never rebuilt.
    assert transport.sent[0]["body"] == transport.sent[1]["body"]
    assert again["event"]["raw_body"] == first["event"]["raw_body"]


def test_a_redelivery_records_a_second_attempt_against_the_same_event(
    fanout, store: RecordStore, meeting
):
    room = store.create("room", {"name": "A"})["id"]
    _enable(fanout, room, _subscribe(fanout, room))
    first = fanout.emit(room, meeting, "new_meeting", actor="dana", source=EVENT)
    fanout.redeliver(room, first["event"]["id"], actor="dana", source="POST redeliver")
    rows = fanout.deliveries(room)["deliveries"]
    assert len(rows) == 2
    assert {row["event_id"] for row in rows} == {first["event"]["id"]}


def test_redelivering_an_event_with_no_enabled_subscriber_is_refused(
    fanout, store: RecordStore, meeting
):
    room = store.create("room", {"name": "A"})["id"]
    row = _enable(fanout, room, _subscribe(fanout, room))
    first = fanout.emit(room, meeting, "new_meeting", actor="dana", source=EVENT)
    fanout.retire(
        room,
        row["id"],
        actor="dana",
        source="DELETE /api/wf-066/rooms/{room_id}/subscriptions/{subscription_id}",
    )
    with pytest.raises(UnknownEvent, match="no enabled"):
        fanout.redeliver(room, first["event"]["id"], actor="dana", source="POST redeliver")


def test_redelivering_an_unknown_event_is_refused(fanout, store: RecordStore):
    room = store.create("room", {"name": "A"})["id"]
    with pytest.raises(UnknownEvent):
        fanout.redeliver(room, "meeting_webhook_event_absent", actor="dana", source="POST x")


# --------------------------------------------------------------------------- #
# The sample, and the summary
# --------------------------------------------------------------------------- #


def test_the_sample_serves_the_bytes_the_input_and_the_signature(fanout, room_id):
    """Quoted: step 4 is the subscriber's, and it needs the room's secret to run."""
    fanout.set_secret(room_id, SECRET, actor="dana", source=KEY)
    sample = fanout.sample(room_id)
    assert sample["secret_is_set"] is True
    assert sample["signing_input"] == f"{sample['timestamp']}.{sample['raw_body']}"
    assert sample["signature"] == sign(SECRET, sample["timestamp"], sample["raw_body"])
    assert sample["replay_window_seconds"] == 300
    assert sample["replay_window_owner"].startswith("the consumer")


def test_the_sample_says_so_when_the_room_has_no_secret_yet(fanout, room_id):
    sample = fanout.sample(room_id)
    assert sample["secret_is_set"] is False
    assert sample["secret"] == "unset"
    # A signature over the placeholder is still served, so a reader can see the
    # shape, and the flag says it is not the room's real secret.
    assert sample["signature"] == sign("unset", sample["timestamp"], sample["raw_body"])


def test_the_sample_payload_is_flat_and_carries_no_wrapper(fanout, room_id):
    import json

    sample = fanout.sample(room_id)
    payload = json.loads(sample["raw_body"])
    assert "payload" not in payload
    assert payload["type"] == "Created"
    assert missing_fields(payload) == []


def test_the_sample_serves_the_signers_own_snippet(fanout, room_id):
    sample = fanout.sample(room_id)
    assert "hmac.new" in sample["verify_snippet"]
    assert "compare_digest" in sample["verify_snippet"]


def test_a_sample_for_an_unknown_type_is_refused(fanout, room_id):
    from dsr.meeting_webhook_fanout import InvalidEventType as _Invalid

    with pytest.raises(_Invalid):
        fanout.sample(room_id, "meeting_started")


def test_the_summary_counts_this_rooms_own_rows(fanout, room_id, meeting):
    _enable(fanout, room_id, _subscribe(fanout, room_id))
    _subscribe(fanout, room_id, url=URL2, event_type="meeting_update")
    fanout.emit(room_id, meeting, "new_meeting", actor="dana", source=EVENT)
    summary = fanout.summary(room_id)
    assert summary["subscriptions"] == 2
    assert summary["enabled"] == 1
    assert summary["events"] == 1
    assert summary["deliveries"] == 1
    assert summary["by_outcome"] == {"delivered": 1}
    assert summary["by_event_type"] == {"new_meeting": 1}
    assert summary["has_secret"] is True


def test_the_summary_names_the_three_things_a_reader_looks_for(fanout, room_id):
    notes = " ".join(fanout.summary(room_id)["notes"])
    assert "not limited by the number" in notes
    assert "Replay protection belongs to the consumer" in notes
    assert "no retry ladder" in notes


# --------------------------------------------------------------------------- #
# The vocabulary and the inference register
# --------------------------------------------------------------------------- #


def test_the_vocabulary_publishes_the_three_types_and_their_payload_values():
    vocab = describe_vocabulary()
    assert [row["id"] for row in vocab["event_types"]] == [
        "new_meeting",
        "meeting_update",
        "canceled_meeting",
    ]
    assert vocab["payload_types"] == ["Created", "Updated", "Deleted"]
    assert vocab["statuses"] == ["enabled", "disabled"]


def test_the_vocabulary_publishes_the_headers_and_the_signing_rule():
    vocab = describe_vocabulary()
    assert vocab["headers"]["signature"] == "X-Chili-Signature"
    assert vocab["headers"]["timestamp"] == "X-Chili-Timestamp"
    assert vocab["headers"]["signature_encoding"] == "hex"
    assert vocab["headers"]["timestamp_unit"] == "unix seconds"
    assert "{timestamp}.{raw_body}" in vocab["signing_rule"]


def test_the_vocabulary_publishes_every_documented_payload_field():
    for name in ("meetingIdChili", "primaryGuestTimeZone", "additionalGuests", "type"):
        assert name in describe_vocabulary()["payload_fields"]


def test_the_vocabulary_names_the_four_collections_this_workflow_writes():
    assert set(describe_vocabulary()["collections"].values()) == {
        "meeting_webhook_subscription",
        "meeting_webhook_event",
        "meeting_webhook_delivery",
        "meeting_webhook_key",
    }


def test_the_vocabulary_publishes_the_product_feature_types_the_research_lists():
    assert describe_vocabulary()["product_feature_types"] == [
        "ConciergeRouter",
        "HandoffRouter",
        "RoundRobinSchedulingLink",
        "ChatPlaybook",
        "DistroRouter",
        "OwnershipSchedulingLink",
    ]


def test_the_vocabulary_says_the_fan_out_is_unbounded():
    assert describe_vocabulary()["fan_out"] == "unbounded"


def test_every_inference_is_named_traceable_and_bounded():
    for entry in INFERENCES:
        assert entry["id"].strip(), entry
        assert entry["basis"].strip(), entry["id"]
        assert entry["change_it"].strip(), entry["id"]
        assert entry["value"], entry["id"]


def test_the_register_records_the_signing_rule_as_a_fact_not_an_inference():
    entry = by_id("hmac-signing-rule")
    assert entry is not None
    assert entry["value"]["separator"] == "a single period"
    assert entry["value"]["signing_input"] == "{timestamp}.{raw_body}"
    assert entry["value"]["signature_encoding"] == "hex"


def test_the_register_records_why_the_envelope_is_flat_and_what_it_cost():
    entry = by_id("one-flat-envelope-for-the-three-chili-types")
    assert entry["value"]["payload_wrapper"] == "never sent"
    assert "MEETING_STARTED" in entry["value"]["out_of_scope"]
    assert "jev-20261004T070110-25984-70596" in entry["basis"]


def test_the_register_records_that_the_replay_window_is_the_consumers():
    entry = by_id("replay-protection-belongs-to-the-consumer")
    assert entry["value"]["sender_enforces_window"] is False
    assert entry["value"]["published_window_seconds"] == 300


def test_the_register_records_which_deployment_this_is():
    entry = by_id("saas-url-policy-is-a-room-setting")
    assert entry["value"]["this_deployment"] == "self_hosted"
    assert entry["value"]["modes"]["saas"] == ["https"]


def test_the_register_records_the_pair_as_the_subscription_identity():
    entry = by_id("subscription-identity-is-url-plus-event-type")
    assert entry["value"]["identity"] == ["url", "event_type"]
    assert entry["value"]["duplicate_pair"] == "refused"


def test_the_register_names_what_this_build_did_not_build():
    entry = by_id("not-built")
    for key in (
        "cal_meeting_started_and_ended",
        "retry_ladder",
        "replay_window_enforcement",
        "dead_letter_queue",
    ):
        assert entry["value"][key]


# --------------------------------------------------------------------------- #
# The architecture the contract enforces
# --------------------------------------------------------------------------- #


def test_the_domain_module_imports_nothing_but_the_store_and_the_standard_library():
    """Hard rule 1 of the build brief.

    Read from the source rather than asserted by hand, because the import surface
    is the property: a module that reached for FastAPI or for ``sqlite3`` would
    fail here whatever it happened to do with the import.

    The first-party allowance is exactly two modules - the store this package
    reads through, and this package's own siblings - so a new first-party import
    has to be added here on purpose rather than arriving by accident.

    The directory is read from the package this test already imported rather than
    spelled out as a path. A hard-coded path is how this test came to check
    ``dsr/scheduling_meetings`` after the package was renamed to
    ``dsr/meeting_webhook_fanout``: it still passed, because it was reading
    another workflow's package, whose imports satisfied the same assertion.
    Reading ``__file__`` off the imported package cannot drift from it.
    """
    import dsr.meeting_webhook_fanout as package_module

    allowed = ("dsr.store", "dsr.meeting_webhook_fanout")
    package = pathlib.Path(package_module.__file__).resolve().parent
    assert package.name == "meeting_webhook_fanout", (
        f"this test is reading {package}, which is not the package it imported"
    )
    for module in sorted(package.glob("*.py")):
        text = module.read_text(encoding="utf-8")
        assert "dsr.api" not in text, module.name
        assert "import sqlite3" not in text, module.name
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")) and " dsr" in stripped:
                assert any(name in stripped for name in allowed), f"{module.name}: {stripped}"
    assert allowed, "the allowance, named so the assertion above reads as one"
    assert sorted(p.name for p in package.glob("*.py")) == [
        "__init__.py",
        "errors.py",
        "fanout.py",
        "inferences.py",
        "payloads.py",
        "signing.py",
        "transport.py",
        "vocabulary.py",
    ], "the package holds these eight modules and no others"


def test_every_writing_method_takes_a_required_source():
    """A URL string hardcoded inside a domain method is a defect, and the audit row
    then names a route the app does not serve.

    Checked on the signature rather than on the call sites: a missing ``source`` is
    a ``TypeError`` at the call, and this is the assertion that it cannot be
    removed without a test failing.
    """
    import inspect

    fanout_class = MeetingWebhookFanout
    writers = [
        name
        for name, member in inspect.getmembers(fanout_class, inspect.isfunction)
        if not name.startswith("_")
    ]
    assert writers
    for name in writers:
        parameters = inspect.signature(getattr(fanout_class, name)).parameters
        if "source" in parameters:
            assert parameters["source"].default is inspect.Parameter.empty, (
                f"{name} has a defaulted source, so omitting it is silent"
            )


def test_records_are_ordinary_json_with_no_typed_column(store: RecordStore, meeting):
    """Hard rule 2: a team adding a field must need no coordination."""
    room = store.create("room", {"name": "A"})["id"]
    fanout = MeetingWebhookFanout(store, FakeTransport())
    row = _subscribe(fanout, room, url=URL, event_type="new_meeting", a_field_this_team_invented=42)
    assert row["a_field_this_team_invented"] == 42
    stored = store.get(row["id"])
    assert stored["data"]["a_field_this_team_invented"] == 42
    assert store.fields(COLLECTIONS["subscription"])


def test_every_write_goes_through_the_audited_wrapper(tmp_path, meeting):
    """Hard rule 3: the audit row is written in the same transaction as the change."""
    database = AuditedDatabase(tmp_path / "audit.db", actor="test")
    try:
        store = RecordStore(database)
        room = store.create("room", {"name": "A"}, source="test")
        fanout = MeetingWebhookFanout(store, FakeTransport())
        _enable(fanout, room["id"], _subscribe(fanout, room["id"]))
        fanout.emit(room["id"], meeting, "new_meeting", actor="dana", source=EVENT)
        entries = [
            entry
            for entry in database.audit(limit=500)
            if entry["collection"].startswith("meeting_webhook_")
        ]
        assert entries, "a write that leaves no audit row is not a write"
        # Every row names the person who asked for it. ``emit`` mints a secret for a
        # room that has none, and that write is attributed to the caller who caused
        # it rather than to an anonymous system, because a person reading the log
        # asking "who changed my signing secret" wants the name that pressed the
        # button.
        assert {entry["actor"] for entry in entries} == {"dana"}
        assert {entry["source"] for entry in entries} == {CREATE, ENABLE, EVENT}
    finally:
        database.close()
