"""WF-081: the domain rules, tested as rules.

Every researched boundary in the specification has a test here, and each one is
named after the sentence it comes from. That is deliberate. A boundary without a
test is a boundary a future edit will move without anybody noticing, and this
workflow's boundaries are the whole product: a deadline that rounded up, a sweep
that touched a signed row, or a reminder with no dedupe would each close an
agreement a buyer had a right to sign.

The rules are tested as pure functions wherever they are pure, with two integers
and a clock. The engine beside them is tested over a real store, because the
question there is whether the writes are the rows the specification describes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from dsr.security_governance import (
    expiry_inferences,
    expiry_rules as rules,
    expiry_vocabulary as vocab,
)
from dsr.security_governance.expiry_engine import ExpiryEngine
from dsr.store import RecordStore

#: One fixed instant, so every boundary in this file is the same boundary.
NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)
NOW_EPOCH = rules.epoch_seconds(NOW)

DAY = 86400
HOUR = 3600

SEND = "POST /api/wf-081/rooms/{room_id}/requests"
REMIND = "POST /api/wf-081/rooms/{room_id}/requests/{request_id}/reminders"
SIGN = "POST /api/wf-081/rooms/{room_id}/requests/{request_id}/sign"
SWEEP = "POST /api/wf-081/rooms/{room_id}/sweep"


@pytest.fixture()
def clock() -> dict:
    """A clock a test moves by hand.

    A dict rather than a list because a test that appends to a list cannot change
    the instant every other boundary was measured against, and a rule that only
    passes at one instant is not a rule.
    """
    return {"now": NOW}


@pytest.fixture()
def engine(store: RecordStore, clock: dict) -> ExpiryEngine:
    return ExpiryEngine(store, now=lambda: clock["now"])


@pytest.fixture()
def room(store: RecordStore) -> str:
    return store.create("room", {"name": "Northwind", "account": "Northwind"}, actor="test")["id"]


def days_out(days: float) -> int:
    return NOW_EPOCH + int(days * DAY)


def engine_at(store: RecordStore, moment: datetime) -> ExpiryEngine:
    return ExpiryEngine(store, now=lambda: moment)


def send(engine: ExpiryEngine, room_id: str, **overrides) -> dict:
    payload = {
        "subject": "Master services agreement",
        "requester_email": "dana@northwind.example",
        "signatures": [{"email": "buyer@northwind.example", "name": "Ada Byron"}],
    }
    payload.update(overrides)
    return engine.send(room_id, payload, actor="dana", source=SEND)


# --------------------------------------------------------------------------- #
# "expires_at must be an integer epoch timestamp in seconds between 1-90 days in
# the future."
# --------------------------------------------------------------------------- #


def test_a_deadline_inside_the_window_is_accepted():
    stored = rules.coerce_expires_at(days_out(30), NOW)
    assert stored == rules.round_down_to_hour(days_out(30))


@pytest.mark.parametrize("days", [0.5, 0.999, 0])
def test_a_deadline_inside_the_first_day_is_refused(days):
    """ "between 1-90 days in the future". One day is the floor."""
    with pytest.raises(rules.ExpiryError) as caught:
        rules.coerce_expires_at(days_out(days), NOW)
    assert caught.value.code == "expiry_out_of_range"


@pytest.mark.parametrize("days", [90.01, 91, 365])
def test_a_deadline_beyond_ninety_days_is_refused(days):
    """90 is the ceiling and it is the specification's own number."""
    with pytest.raises(rules.ExpiryError) as caught:
        rules.coerce_expires_at(days_out(days), NOW)
    assert caught.value.code == "expiry_out_of_range"


def test_exactly_one_day_and_exactly_ninety_days_are_inside_the_window():
    """ "between 1-90 days" is inclusive at both ends."""
    assert rules.coerce_expires_at(days_out(1), NOW) is not None
    assert rules.coerce_expires_at(days_out(90), NOW) is not None


def test_a_deadline_in_the_past_is_refused():
    with pytest.raises(rules.ExpiryError) as caught:
        rules.coerce_expires_at(NOW_EPOCH - DAY, NOW)
    assert caught.value.code == "expiry_out_of_range"


@pytest.mark.parametrize("value", [1.5, "not-a-number", [1700000000], {"at": 1}, "12.5", "1e9"])
def test_a_value_that_is_not_a_whole_number_of_seconds_is_refused(value):
    """ "must be an integer epoch timestamp in seconds"."""
    with pytest.raises(rules.ExpiryError) as caught:
        rules.coerce_expires_at(value, NOW)
    assert caught.value.code == "expiry_not_an_integer"


def test_an_empty_string_is_read_as_no_expiry():
    """An empty form field means the seller did not set one, which is legal."""
    assert rules.coerce_expires_at("", NOW) is None
    assert rules.coerce_expires_at("   ", NOW) is None


def test_a_boolean_is_not_a_deadline():
    """True is an int in Python. A boolean deadline is a caller's bug, not a date."""
    with pytest.raises(rules.ExpiryError):
        rules.coerce_expires_at(True, NOW)


def test_a_numeric_string_is_accepted_because_clients_send_them():
    assert rules.coerce_expires_at(str(days_out(10)), NOW) == rules.round_down_to_hour(days_out(10))


def test_a_float_that_is_whole_is_accepted_and_a_fractional_one_is_not():
    """A float that lost its fraction through a JSON round trip is still an integer."""
    assert rules.coerce_expires_at(float(days_out(10)), NOW) is not None
    with pytest.raises(rules.ExpiryError):
        rules.coerce_expires_at(days_out(10) + 0.5, NOW)


# --------------------------------------------------------------------------- #
# "expires_at will be rounded down to the nearest hour."
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("offset", [1, 59, 60, 61, 1800, 3599])
def test_the_stored_deadline_is_never_later_than_the_value_that_was_sent(offset):
    """Flooring, always. The rule may only ever bring a deadline forward.

    Rounding to the nearest hour would let a request for 17:30 live until 18:00,
    and that is half an hour in which a signer can sign an agreement the seller
    believed was closed.
    """
    requested = days_out(10) + offset
    stored = rules.coerce_expires_at(requested, NOW)
    assert stored <= requested
    assert stored % HOUR == 0
    assert requested - stored < HOUR


def test_rounding_down_never_moves_a_deadline_later():
    for offset in range(0, HOUR, 137):
        assert rules.round_down_to_hour(days_out(5) + offset) <= days_out(5) + offset


# --------------------------------------------------------------------------- #
# "Only signature requests that explicitly set an expires_at will expire. By
# default signature requests do not expire."
# --------------------------------------------------------------------------- #


def test_a_request_with_no_expiry_has_never_expired():
    """The most important test in this file.

    A missing field is not a deadline in the past. It is no deadline at all, and
    the defect this guards against is a sweep reading it as zero and closing every
    agreement the product has.
    """
    assert rules.has_expiry({}) is False
    assert rules.is_expired({}, NOW) is False
    assert rules.seconds_remaining({}, NOW) is None
    assert rules.request_status({}, NOW) == vocab.REQUEST_STATUS_PENDING
    assert rules.due_reminders({}, NOW) == []


def test_a_request_with_no_expiry_is_never_terminal():
    assert rules.is_terminal({}, NOW) is False


def test_an_empty_string_is_not_an_expiry():
    assert rules.has_expiry({vocab.EXPIRES_AT: ""}) is False


def test_an_unreadable_expiry_is_treated_as_absent_rather_than_guessed():
    """A stored value this build cannot read must not be coerced into a deadline."""
    assert rules.has_expiry({vocab.EXPIRES_AT: "soon"}) is False
    assert rules.expires_at_of({vocab.EXPIRES_AT: "soon"}) is None


# --------------------------------------------------------------------------- #
# The deadline passing
# --------------------------------------------------------------------------- #


def test_a_request_is_expired_at_the_deadline_and_one_second_after():
    data = {vocab.EXPIRES_AT: NOW_EPOCH}
    assert rules.is_expired(data, NOW) is True
    assert rules.is_expired(data, NOW + timedelta(seconds=1)) is True
    assert rules.is_expired(data, NOW - timedelta(seconds=1)) is False


def test_the_remaining_seconds_go_negative_after_the_deadline():
    """>A clamped zero would hide the fact that a request is late."""
    assert rules.seconds_remaining({vocab.EXPIRES_AT: NOW_EPOCH - 60}, NOW) == -60
    assert rules.days_remaining({vocab.EXPIRES_AT: NOW_EPOCH - 60}, NOW) == pytest.approx(
        -0.0007, abs=1e-3
    )


# --------------------------------------------------------------------------- #
# "Signature request reminder emails will be sent to the signer 3 and 7 days
# before the signature request expires"
# --------------------------------------------------------------------------- #


def test_the_cadence_is_seven_and_three_days():
    assert vocab.REMINDER_LEAD_DAYS == (7, 3)


@pytest.mark.parametrize(
    ("days", "due"),
    [
        # A window is the time a request has LEFT. "7 days before expiry" means
        # seven days remain, so a request 7.5 days out has not reached its window.
        (8.0, []),
        (7.5, []),
        (7.0, [7]),
        (6.5, [7]),
        (6.01, [7]),
        (6.0, []),
        (4.0, []),
        (3.5, []),
        (3.0, [3]),
        (2.99, [3]),
        (2.0, []),
        (1.5, []),
    ],
)
def test_which_reminder_window_is_open(days, due):
    """One window per lead day, closed at the near edge.

    The near edge is exclusive because the lead day is named and not the range:
    exactly 3 days remaining is "the 3-day reminder", and 2.99 days is inside it
    too, while 3.01 days has not arrived yet. Two windows must never be open at
    once, or a signer is told twice about one deadline.
    """
    data = {vocab.EXPIRES_AT: days_out(days)}
    assert rules.due_reminders(data, NOW) == due


def test_a_request_past_its_deadline_is_due_for_no_reminder():
    """The one message that must never go out."""
    data = {vocab.EXPIRES_AT: NOW_EPOCH - DAY}
    assert rules.due_reminders(data, NOW) == []


def test_the_two_windows_are_a_day_wide_and_never_overlap():
    """The windows are what the 24-hour dedupe is for, so they must not collide."""
    assert vocab.REMINDER_LEAD_DAYS[0] - vocab.REMINDER_LEAD_DAYS[1] == 4


# --------------------------------------------------------------------------- #
# "If a signer was already reminded within 24 hours, we will skip the automated
# reminder."
# --------------------------------------------------------------------------- #


def test_a_send_inside_the_window_blocks_the_next_one():
    assert rules.dedupe_blocks(rules.stamp(NOW - timedelta(hours=1)), NOW) is True


def test_a_send_exactly_one_day_old_does_not_block():
    """24 hours is the boundary and the boundary does not block."""
    assert rules.dedupe_blocks(rules.stamp(NOW - timedelta(hours=24)), NOW) is False
    assert rules.dedupe_blocks(rules.stamp(NOW - timedelta(hours=25)), NOW) is False


def test_no_previous_send_does_not_block():
    assert rules.dedupe_blocks(None, NOW) is False
    assert rules.dedupe_blocks("", NOW) is False


def test_a_stamp_this_build_cannot_read_does_not_block():
    """A misread timestamp must not silence a signer's reminder permanently."""
    assert rules.dedupe_blocks("not a timestamp", NOW) is False


def test_the_plan_reports_a_send_then_a_skip():
    data = {vocab.EXPIRES_AT: days_out(6.5)}
    first = rules.reminder_plan(data, [], NOW)
    assert first["due_now"] is True
    assert first["blocked_by_dedupe"] is False
    assert first["lead_days"] == 7

    ledger = [{"outcome": "sent", "at": rules.stamp(NOW - timedelta(hours=2))}]
    second = rules.reminder_plan(data, ledger, NOW)
    assert second["due_now"] is True
    assert second["blocked_by_dedupe"] is True


def test_a_skip_does_not_block_the_next_reminder():
    """A skip recorded as a send would silence this signer for good."""
    data = {vocab.EXPIRES_AT: days_out(2.5)}
    ledger = [{"outcome": "skipped", "at": rules.stamp(NOW - timedelta(minutes=5))}]
    plan = rules.reminder_plan(data, ledger, NOW)
    assert plan["due_now"] is True
    assert plan["blocked_by_dedupe"] is False


def test_the_plan_counts_sends_and_skips_apart():
    data = {vocab.EXPIRES_AT: days_out(6.5)}
    ledger = [
        {"outcome": "sent", "at": rules.stamp(NOW - timedelta(hours=3))},
        {"outcome": "skipped", "at": rules.stamp(NOW - timedelta(hours=2))},
    ]
    plan = rules.reminder_plan(data, ledger, NOW)
    assert plan["sent_count"] == 1
    assert plan["skipped_count"] == 1


def test_the_plan_of_a_request_with_no_expiry_is_never_due():
    plan = rules.reminder_plan({}, [], NOW)
    assert plan["has_expiry"] is False
    assert plan["due_now"] is False


# --------------------------------------------------------------------------- #
# "On expiry, unsigned signatures flip to expired ... Completed signers stay
# signed."
# --------------------------------------------------------------------------- #


def test_the_sweep_moves_every_incomplete_signer_and_keeps_the_signed_ones():
    data = {
        "signatures": [
            {"email": "a@example.com", "status_code": "signed"},
            {"email": "b@example.com", "status_code": "awaiting_signature"},
            {"email": "c@example.com", "status_code": "viewed"},
        ]
    }
    moved = rules.swept_signers(data)
    assert [row["email"] for row in moved] == ["b@example.com", "c@example.com"]

    swept = rules.apply_sweep(data)
    codes = {row["email"]: row["status_code"] for row in swept["signatures"]}
    assert codes == {
        "a@example.com": "signed",
        "b@example.com": "expired",
        "c@example.com": "expired",
    }


def test_the_sweep_does_not_mutate_the_payload_it_was_given():
    data = {"signatures": [{"email": "b@example.com", "status_code": "awaiting_signature"}]}
    rules.apply_sweep(data)
    assert data["signatures"][0]["status_code"] == "awaiting_signature"


def test_a_signer_with_no_status_is_treated_as_incomplete():
    """An unsigned row is the default, not an error."""
    data = {"signatures": [{"email": "b@example.com"}]}
    assert len(rules.swept_signers(data)) == 1
    assert rules.apply_sweep(data)["signatures"][0]["status_code"] == "expired"


def test_a_completed_signer_is_left_completed():
    data = {
        "signatures": [
            {"email": "a@example.com", "status_code": "signed"},
            {"email": "b@example.com", "status_code": "completed"},
        ]
    }
    assert rules.swept_signers(data) == []
    assert rules.is_complete(data) is True


def test_a_request_whose_signer_rows_are_not_mappings_is_left_alone():
    """A malformed row is skipped, not turned into an expired signature."""
    data = {"signatures": ["nonsense", {"email": "a@example.com"}]}
    assert [row["email"] for row in rules.swept_signers(data)] == ["a@example.com"]


# --------------------------------------------------------------------------- #
# "Once a signature request has expired, it is considered to be in a final status"
# --------------------------------------------------------------------------- #


def test_a_request_whose_every_signer_signed_is_completed_not_expired():
    """An agreement signed in time is not a failed agreement."""
    data = {
        vocab.EXPIRES_AT: NOW_EPOCH - DAY,
        "signatures": [{"email": "a@example.com", "status_code": "signed"}],
    }
    assert rules.request_status(data, NOW) == vocab.REQUEST_STATUS_COMPLETED
    assert rules.is_terminal(data, NOW) is True


def test_a_request_with_one_signer_out_is_expired():
    data = {
        vocab.EXPIRES_AT: NOW_EPOCH - DAY,
        "signatures": [
            {"email": "a@example.com", "status_code": "signed"},
            {"email": "b@example.com", "status_code": "awaiting_signature"},
        ],
    }
    assert rules.request_status(data, NOW) == vocab.REQUEST_STATUS_EXPIRED


def test_a_request_with_no_signers_is_never_complete():
    """An agreement nobody has to sign is not an agreement that is done."""
    assert rules.is_complete({}) is False
    assert rules.is_complete({"signatures": []}) is False


def test_an_open_request_is_pending_and_not_terminal():
    data = {
        vocab.EXPIRES_AT: days_out(3),
        "signatures": [{"email": "a@example.com"}],
    }
    assert rules.request_status(data, NOW) == vocab.REQUEST_STATUS_PENDING
    assert rules.is_terminal(data, NOW) is False


# --------------------------------------------------------------------------- #
# "Audit writes an expired audit event with the expiration date listed along with
# all the signers who did not sign by the expiration date."
# --------------------------------------------------------------------------- #


def test_the_audit_note_carries_the_date_and_every_signer_who_missed_it():
    data = {
        vocab.EXPIRES_AT: NOW_EPOCH - DAY,
        "signatures": [
            {"email": "signed@example.com", "status_code": "signed"},
            {"email": "missed@example.com", "status_code": "awaiting_signature"},
            {"email": "also-missed@example.com", "status_code": "viewed"},
        ],
    }
    note = rules.audit_note(data, NOW)
    assert "expired at" in note
    assert "missed@example.com" in note
    assert "also-missed@example.com" in note
    assert "signed@example.com" not in note


def test_the_audit_note_of_an_untouched_request_names_nobody():
    assert "nobody" in rules.audit_note({"signatures": []}, NOW)


# --------------------------------------------------------------------------- #
# "the signer will see the signature request expiration date in the banner next
# to the number of required fields ... in their own timezone"
# --------------------------------------------------------------------------- #


def _zone_resolves(name: str) -> bool:
    """Is an IANA zone this build can actually resolve?

    The timezone database is a separate package and a Windows interpreter without
    it resolves every named zone to UTC. The tests below branch on the answer
    rather than assuming, because a test that asserts a London offset fails on a
    host with no tzdata for a reason that has nothing to do with this workflow.
    """
    return rules._zone(name) is not timezone.utc  # noqa: SLF001 - the boundary under test


def test_a_fixed_offset_is_always_honoured_because_it_carries_its_own_answer():
    """A mobile client sends "+05:30", not "Asia/Kolkata", and needs no database."""
    for name, minutes in (("+05:30", 330), ("-04:00", -240), ("+00:00", 0)):
        view = rules.local_text(NOW_EPOCH, name)
        assert view["offset_minutes"] == minutes
        assert view["known"] is True
        assert view["note"] is None


def test_an_iana_name_is_honoured_where_the_timezone_database_exists():
    if not _zone_resolves("Asia/Kolkata"):
        pytest.skip("this interpreter has no timezone database installed")
    view = rules.local_text(NOW_EPOCH, "Asia/Kolkata")
    assert view["offset_minutes"] == 330
    assert view["known"] is True


def test_an_iana_name_falls_back_to_utc_and_says_it_did():
    """>The instant is still exact, and the response does not pretend otherwise.

    A banner with the wrong local time is cosmetic. Guessing a plausible offset
    would not be, because a signer who trusts it signs on the wrong date.
    """
    view = rules.local_text(NOW_EPOCH, "Asia/Kolkata")
    if view["known"]:
        assert view["offset_minutes"] == 330
        return
    assert view["offset_minutes"] == 0
    assert view["known"] is False
    assert "exact" in view["note"]
    # The exact instant is reported whatever the timezone does.
    assert view["epoch_seconds"] == NOW_EPOCH
    assert view["iso_utc"].startswith("2026-10-04T12:00")


def test_the_local_reading_differs_by_offset():
    deadline = NOW_EPOCH + 5 * DAY
    kolkata = rules.local_text(deadline, "+05:30")
    new_york = rules.local_text(deadline, "-04:00")
    assert kolkata["local"] != new_york["local"]
    assert kolkata["epoch_seconds"] == new_york["epoch_seconds"] == deadline


# --------------------------------------------------------------------------- #
# Signers
# --------------------------------------------------------------------------- #


def test_a_signer_needs_an_email_and_nothing_else():
    assert rules.normalise_signers([{"email": " a@example.com "}]) == [
        {
            "email": "a@example.com",
            "status_code": "awaiting_signature",
            "name": "a@example.com",
            "preferred_timezone": None,
        }
    ]


def test_a_signer_with_no_address_is_dropped_rather_than_stored():
    """A signer with no email cannot be reminded and cannot be swept."""
    rows = rules.normalise_signers([{"name": "Nobody"}, {"email": ""}, "nope", None])
    assert rows == []


def test_a_signer_keeps_the_fields_this_workflow_does_not_read():
    """The store is schema-flexible and a team adding a field needs no coordination."""
    rows = rules.normalise_signers([{"email": "a@example.com", "company": "Northwind"}])
    assert rows[0]["company"] == "Northwind"


def test_a_signer_list_that_is_not_a_list_is_empty():
    assert rules.normalise_signers(None) == []
    assert rules.normalise_signers("a@example.com") == []


def test_the_timezone_spellings_are_both_read():
    rows = rules.normalise_signers([{"email": "a@example.com", "timezone": "Europe/London"}])
    assert rows[0]["preferred_timezone"] == "Europe/London"


# --------------------------------------------------------------------------- #
# The room reference
# --------------------------------------------------------------------------- #


def test_the_room_is_read_from_the_payload_then_the_envelope():
    assert rules.room_ref_of({rules.ROOM_REF: "r1"}) == "r1"
    assert rules.room_ref_of({}, {"room_id": "r2"}) == "r2"
    assert rules.room_ref_of({rules.ROOM_REF: "r1"}, {"room_id": "r2"}) == "r1"


def test_the_payload_key_is_not_the_envelopes_room_id():
    """>`room_id` is stripped from ``data`` before the dynamic index is built.

    A payload that stored its room there would be unfilterable, and a "filter by
    room" that silently returns nothing is the defect this key exists to prevent.
    """
    assert rules.ROOM_REF != "room_id"


# --------------------------------------------------------------------------- #
# The vocabulary and the register
# --------------------------------------------------------------------------- #


def test_the_catalogue_serves_the_numbers_the_rules_enforce():
    """The served list and the enforced rule cannot disagree if both read this."""
    body = vocab.catalogue()
    assert body["expiry_rule"]["min_days"] == vocab.MIN_EXPIRY_DAYS
    assert body["expiry_rule"]["max_days"] == vocab.MAX_EXPIRY_DAYS
    assert body["reminder_rule"]["lead_days"] == list(vocab.REMINDER_LEAD_DAYS)
    assert body["reminder_rule"]["dedupe_window_hours"] == vocab.DEDUPE_WINDOW_HOURS
    assert body["delivery"]["event"] == "signature_request_expired"
    assert body["delivery"]["email_muted_when_embedded"] is True


def test_the_catalogue_marks_exactly_which_signer_statuses_the_sweep_moves():
    swept = {row["status_code"] for row in vocab.catalogue()["signer_statuses"] if row["swept"]}
    assert swept == {"awaiting_signature", "viewed"}


def test_the_event_name_is_the_vendors_own():
    """An integration subscribed to the researched name would not fire on ours."""
    assert vocab.EVENT_EXPIRED == "signature_request_expired"


def test_the_invariants_are_the_researchs_own_sentences():
    assert "will still have access to the document" in vocab.DOCUMENT_SURVIVES
    assert "is not deleted" in vocab.LINK_SURVIVES


def test_the_register_records_every_decision_with_the_alternative_it_rejected():
    register = expiry_inferences.register()
    assert register["ticket"] == "WF-081"
    assert register["count"] == len(expiry_inferences.DECISIONS)
    for entry in register["decisions"]:
        assert entry["chosen"]
        assert entry["rejected"]
        assert entry["consequence"]
        assert entry["question"]


def test_the_register_names_the_shape_this_build_implemented():
    ids = {entry["id"] for entry in expiry_inferences.DECISIONS}
    assert "which-expiry-shape" in ids
    assert "rounding-rounds-down-not-up" in ids
    assert "no-expiry-means-never" in ids
    assert "sms-reminders-are-not-built" in ids


def test_the_register_says_which_decisions_the_research_did_not_source():
    """A gap in the evidence is stated, not papered over."""
    unsourced = {entry["id"] for entry in expiry_inferences.DECISIONS if entry["unsourced"]}
    assert "the-ledger-is-a-collection-not-a-field" in unsourced
    assert "reminders-are-computed-not-scheduled" in unsourced
    # The two the research states outright are not marked unsourced.
    assert "no-expiry-means-never" not in unsourced
    assert "a-completed-signer-is-not-swept" not in unsourced


# --------------------------------------------------------------------------- #
# The errors
# --------------------------------------------------------------------------- #


def test_every_error_carries_a_status_and_the_specifications_sentence():
    for code, (status, sentence) in vocab.ERROR_CODES.items():
        assert 400 <= status < 500, code
        assert sentence
        error = rules.ExpiryError(code)
        assert error.status == status
        assert str(error) == sentence


def test_an_unknown_error_code_still_raises_something_answerable():
    error = rules.ExpiryError("no_such_code")
    assert error.status == 422
    assert str(error)


def test_the_closed_message_is_the_one_the_research_quotes():
    """>"an error stating that the signature request is closed"."""
    error = rules.ExpiryError("request_closed")
    assert "signature request is closed" in str(error)
    assert error.status == 409


# --------------------------------------------------------------------------- #
# The engine, over a real store
# --------------------------------------------------------------------------- #


def test_a_sent_request_is_open_and_pending(engine: ExpiryEngine, room: str):
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(10)})
    assert view["status"] == vocab.REQUEST_STATUS_PENDING
    assert view["closed"] is False
    assert view["has_expiry"] is True
    assert view["expires_at"] == rules.round_down_to_hour(days_out(10))


def test_a_sent_request_with_no_expiry_never_closes(engine: ExpiryEngine, room: str):
    view = send(engine, room)
    assert view["has_expiry"] is False
    assert view["expires_at"] is None
    assert "expiry_view" not in view


def test_a_refused_deadline_writes_nothing(store: RecordStore, room: str):
    """>A refused send must leave no trace.

    The audit log is this product's guarantee, and a row describing a request that
    changed nothing is a row a reader has to learn to discount.
    """
    engine = ExpiryEngine(store, now=lambda: NOW)
    before = len(store.audit(limit=2000))
    with pytest.raises(rules.ExpiryError):
        send(engine, room, **{vocab.EXPIRES_AT: days_out(200)})
    assert store.list(vocab.EXPIRY_COLLECTION) == []
    assert len(store.audit(limit=2000)) == before


def test_a_deadline_can_be_moved(engine: ExpiryEngine, room: str):
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(10)})
    moved = engine.update_expiry(view["id"], {vocab.EXPIRES_AT: days_out(30)}, source="PUT /x")
    assert moved["expires_at"] == rules.round_down_to_hour(days_out(30))


def test_an_explicit_null_clears_the_deadline(engine: ExpiryEngine, room: str):
    """>By default signature requests do not expire."""
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(10)})
    cleared = engine.update_expiry(view["id"], {vocab.EXPIRES_AT: None}, source="PUT /x")
    assert cleared["has_expiry"] is False
    assert cleared["expires_at"] is None


def test_omitting_the_field_leaves_the_deadline_alone(engine: ExpiryEngine, room: str):
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(10)})
    untouched = engine.update_expiry(view["id"], {}, source="PUT /x")
    assert untouched["expires_at"] == view["expires_at"]


def test_a_moved_deadline_outside_the_window_is_refused(engine: ExpiryEngine, room: str):
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(10)})
    with pytest.raises(rules.ExpiryError):
        engine.update_expiry(view["id"], {vocab.EXPIRES_AT: days_out(200)}, source="PUT /x")


def test_a_terminal_request_refuses_a_deadline_change(store: RecordStore, room: str):
    """>They will not be able to sign or modify the signature request."""
    engine = ExpiryEngine(store, now=lambda: NOW)
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(10)})
    later = ExpiryEngine(store, now=lambda: NOW + timedelta(days=30))
    later.sweep(room, source=SWEEP)
    with pytest.raises(rules.ExpiryError) as caught:
        later.update_expiry(view["id"], {vocab.EXPIRES_AT: days_out(40)}, source="PUT /x")
    assert caught.value.code == "request_closed"
    assert caught.value.status == 409


def test_a_signer_can_sign_while_the_request_is_open(engine: ExpiryEngine, room: str):
    view = send(
        engine, room, **{vocab.EXPIRES_AT: days_out(10), "signatures": [{"email": "a@example.com"}]}
    )
    signed = engine.sign(view["id"], {"email": "a@example.com"}, source=SIGN)
    assert signed["signatures"][0]["status_code"] == "signed"
    assert signed["status"] == vocab.REQUEST_STATUS_COMPLETED


def test_a_signer_cannot_sign_twice(engine: ExpiryEngine, room: str):
    """Two signers, so the request stays open and the refusal is the signer's own."""
    view = send(
        engine,
        room,
        **{
            vocab.EXPIRES_AT: days_out(10),
            "signatures": [{"email": "a@example.com"}, {"email": "b@example.com"}],
        },
    )
    engine.sign(view["id"], {"email": "a@example.com"}, source=SIGN)
    with pytest.raises(rules.ExpiryError) as caught:
        engine.sign(view["id"], {"email": "a@example.com"}, source=SIGN)
    assert caught.value.code == "already_signed"
    assert caught.value.status == 409


def test_a_signer_told_they_signed_is_not_told_the_request_closed(engine: ExpiryEngine, room: str):
    """>The two refusals send a signer to two different places, so they are named apart."""
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(10)})
    engine.sign(view["id"], {"email": "buyer@northwind.example"}, source=SIGN)
    with pytest.raises(rules.ExpiryError) as caught:
        engine.sign(view["id"], {"email": "buyer@northwind.example"}, source=SIGN)
    assert "already completed their part" in str(caught.value)


def test_a_signer_on_an_expired_request_is_told_the_request_is_closed(
    store: RecordStore, room: str
):
    """>"they will receive an error stating that the signature request is closed"."""
    engine = ExpiryEngine(store, now=lambda: NOW)
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(10)})
    later = ExpiryEngine(store, now=lambda: NOW + timedelta(days=30))
    later.sweep(room, source=SWEEP)
    with pytest.raises(rules.ExpiryError) as caught:
        later.sign(view["id"], {"email": "buyer@northwind.example"}, source=SIGN)
    assert caught.value.code == "request_closed"


def test_a_signer_not_on_the_request_is_refused(engine: ExpiryEngine, room: str):
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(10)})
    with pytest.raises(rules.ExpirySignerNotFound):
        engine.sign(view["id"], {"email": "stranger@example.com"}, source=SIGN)
    with pytest.raises(rules.ExpirySignerNotFound):
        engine.sign(view["id"], {}, source=SIGN)


def test_a_signer_not_on_the_request_cannot_read_the_gate(engine: ExpiryEngine, room: str):
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(10)})
    with pytest.raises(rules.ExpirySignerNotFound):
        engine.can_sign(view["id"], "stranger@example.com")


def test_can_sign_reports_rather_than_refuses_while_the_request_is_open(
    engine: ExpiryEngine, room: str
):
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(10)})
    gate = engine.can_sign(view["id"], "buyer@northwind.example")
    assert gate["can_sign"] is True
    assert gate["reason"] == "open"


def test_can_sign_names_the_closure_after_a_sweep(store: RecordStore, room: str):
    engine = ExpiryEngine(store, now=lambda: NOW)
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(10)})
    later = ExpiryEngine(store, now=lambda: NOW + timedelta(days=30))
    later.sweep(room, source=SWEEP)
    gate = later.can_sign(view["id"], "buyer@northwind.example")
    assert gate["can_sign"] is False
    assert gate["reason"] == "request_closed"


# --------------------------------------------------------------------------- #
# The ledger, over a real store
# --------------------------------------------------------------------------- #


def test_a_due_reminder_is_sent_and_recorded(engine: ExpiryEngine, room: str):
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(6.5)})
    report = engine.remind(view["id"], source=REMIND)
    assert report["sent"] == 1
    assert report["skipped"] == 0
    assert report["decisions"][0]["lead_days"] == 7
    assert report["decisions"][0]["channel"] == "email"

    ledger = engine.reminders(room, view["id"])
    assert len(ledger) == 1
    assert ledger[0]["outcome"] == "sent"
    assert ledger[0]["channel"] == "email"


def test_a_second_pass_inside_24_hours_is_skipped_and_recorded(
    store: RecordStore, room: str, clock: dict
):
    engine = ExpiryEngine(store, now=lambda: clock["now"])
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(6.5)})
    engine.remind(view["id"], source=REMIND)

    clock["now"] = NOW + timedelta(hours=2)
    again = ExpiryEngine(store, now=lambda: clock["now"])
    report = again.remind(view["id"], source=REMIND)
    assert report["sent"] == 0
    assert report["decisions"][0]["reason"] == "deduped_within_24h"

    ledger = again.reminders(room, view["id"])
    assert [row["outcome"] for row in ledger] == ["skipped", "sent"]


def test_a_signer_who_signed_is_skipped_with_its_own_reason(engine: ExpiryEngine, room: str):
    view = send(
        engine,
        room,
        **{
            vocab.EXPIRES_AT: days_out(6.5),
            "signatures": [
                {"email": "a@example.com"},
                {"email": "b@example.com"},
            ],
        },
    )
    engine.sign(view["id"], {"email": "a@example.com"}, source=SIGN)
    report = engine.remind(view["id"], source=REMIND)
    reasons = {row["email"]: row["reason"] for row in report["decisions"]}
    assert reasons == {"a@example.com": "already_signed", "b@example.com": "window_open"}


def test_one_signer_being_deduped_does_not_silence_another(store: RecordStore, room: str, clock):
    """The dedupe is per signer, and this pins that."""
    engine = ExpiryEngine(store, now=lambda: clock["now"])
    view = send(
        engine,
        room,
        **{
            vocab.EXPIRES_AT: days_out(6.5),
            "signatures": [{"email": "a@example.com"}, {"email": "b@example.com"}],
        },
    )
    engine.remind(view["id"], {"email": "a@example.com"}, source=REMIND)
    clock["now"] = NOW + timedelta(hours=1)
    again = ExpiryEngine(store, now=lambda: clock["now"])
    report = again.remind(view["id"], source=REMIND)
    outcomes = {row["email"]: row["outcome"] for row in report["decisions"]}
    assert outcomes == {"a@example.com": "skipped", "b@example.com": "sent"}


def test_a_reminder_narrowed_to_a_signer_leaves_that_signer(store: RecordStore, room: str):
    engine = ExpiryEngine(store, now=lambda: NOW)
    view = send(
        engine,
        room,
        **{
            vocab.EXPIRES_AT: days_out(6.5),
            "signatures": [{"email": "a@example.com"}, {"email": "b@example.com"}],
        },
    )
    report = engine.remind(view["id"], {"email": "b@example.com"}, source=REMIND)
    assert [row["email"] for row in report["decisions"]] == ["b@example.com"]
    with pytest.raises(rules.ExpirySignerNotFound):
        engine.remind(view["id"], {"email": "stranger@example.com"}, source=REMIND)


def test_a_request_with_no_expiry_is_never_reminded(engine: ExpiryEngine, room: str):
    view = send(engine, room)
    report = engine.remind(view["id"], source=REMIND)
    assert report["sent"] == 0
    assert report["decisions"][0]["reason"] == "no_window_open"


def test_an_embedded_request_sends_no_email_and_publishes_an_event(engine: ExpiryEngine, room: str):
    """>"Emails are muted in all embedded signing flows"."""
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(6.5), "flow": "embedded"})
    report = engine.remind(view["id"], source=REMIND)
    assert report["email_muted"] is True
    assert report["decisions"][0]["channel"] == "event"

    ledger = engine.reminders(room, view["id"])
    assert ledger[0]["channel"] == "event"

    events = engine.events(room, view["id"])
    assert events[0]["event"] == vocab.EVENT_REMINDER_SENT
    assert events[0]["channel"] == "event"


def test_the_embed_spelling_the_vendor_uses_is_accepted(engine: ExpiryEngine, room: str):
    """``invite_path: embed`` is the research's own spelling."""
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(10), "invite_path": "embed"})
    assert view["flow"] == vocab.FLOW_EMBEDDED
    assert view["email_muted"] is True


def test_an_unknown_delivery_mode_is_refused(engine: ExpiryEngine, room: str):
    with pytest.raises(rules.ExpiryError):
        send(engine, room, **{"flow": "carrier-pigeon"})


# --------------------------------------------------------------------------- #
# The sweep, over a real store
# --------------------------------------------------------------------------- #


def test_the_sweep_closes_a_due_request_and_keeps_the_signed_signer(store: RecordStore, room: str):
    engine = ExpiryEngine(store, now=lambda: NOW)
    view = send(
        engine,
        room,
        **{
            vocab.EXPIRES_AT: days_out(10),
            "signatures": [{"email": "a@example.com"}, {"email": "b@example.com"}],
        },
    )
    engine.sign(view["id"], {"email": "a@example.com"}, source=SIGN)

    later = ExpiryEngine(store, now=lambda: NOW + timedelta(days=11))
    report = later.sweep(room, source=SWEEP)
    assert report["swept_count"] == 1
    assert report["documents_deleted"] == 0

    row = report["swept"][0]
    assert [entry["email"] for entry in row["swept"]] == ["b@example.com"]
    assert row["kept_signed"] == ["a@example.com"]


def test_the_record_still_reads_after_expiry(store: RecordStore, room: str):
    """>"All parties to the signature request will still have access to the
    document including audit trail, similar to declined signature requests."

    The single most important property in this workflow, so it is a test rather
    than a claim.
    """
    engine = ExpiryEngine(store, now=lambda: NOW)
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(10)})
    later = ExpiryEngine(store, now=lambda: NOW + timedelta(days=11))
    later.sweep(room, source=SWEEP)

    after = later.request_view(view["id"])
    assert after["id"] == view["id"]
    assert after["status"] == vocab.REQUEST_STATUS_EXPIRED
    assert after["closed"] is True
    assert after["signatures"][0]["status_code"] == "expired"
    assert after["document"] == view["document"]
    assert store.get(view["id"]) is not None


def test_the_sweep_leaves_a_request_with_no_expiry_alone(store: RecordStore, room: str):
    """>"Only signature requests that explicitly set an expires_at will expire"."""
    engine = ExpiryEngine(store, now=lambda: NOW)
    view = send(engine, room)
    later = ExpiryEngine(store, now=lambda: NOW + timedelta(days=365))
    report = later.sweep(room, source=SWEEP)
    assert report["swept_count"] == 0
    assert report["skipped"][0]["reason"] == "no_expiry_set"
    assert later.request_view(view["id"])["status"] == vocab.REQUEST_STATUS_PENDING


def test_the_sweep_leaves_a_request_before_its_deadline_alone(engine: ExpiryEngine, room: str):
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(10)})
    report = engine.sweep(room, source=SWEEP)
    assert report["swept_count"] == 0
    assert report["skipped"][0]["id"] == view["id"]
    assert report["skipped"][0]["reason"] == "deadline_not_passed"


def test_the_sweep_leaves_a_complete_request_alone(store: RecordStore, room: str):
    engine = ExpiryEngine(store, now=lambda: NOW)
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(10)})
    engine.sign(view["id"], {"email": "buyer@northwind.example"}, source=SIGN)

    later = ExpiryEngine(store, now=lambda: NOW + timedelta(days=11))
    report = later.sweep(room, source=SWEEP)
    assert report["swept_count"] == 0
    assert report["skipped"][0]["reason"] == "already_complete"
    assert later.request_view(view["id"])["status"] == vocab.REQUEST_STATUS_COMPLETED


def test_sweeping_twice_changes_nothing_the_second_time(store: RecordStore, room: str):
    """The sweep has to be safe to run twice.

    A scheduler nobody ran for an hour is normal, and a second pass must not
    write a second ``signature_request_expired`` event for one deadline. An
    integration counting those events would otherwise count a buyer told twice.
    """
    engine = ExpiryEngine(store, now=lambda: NOW)
    send(engine, room, **{vocab.EXPIRES_AT: days_out(10)})
    later = ExpiryEngine(store, now=lambda: NOW + timedelta(days=11))
    first = later.sweep(room, source=SWEEP)
    revision = store.list(vocab.EXPIRY_COLLECTION)[0]["revision"]
    events = len(later.events(room, event=vocab.EVENT_EXPIRED))

    second = later.sweep(room, source=SWEEP)
    assert first["swept_count"] == 1
    assert second["swept_count"] == 0
    assert second["skipped"][0]["reason"] == "already_closed"
    assert store.list(vocab.EXPIRY_COLLECTION)[0]["revision"] == revision
    assert len(later.events(room, event=vocab.EVENT_EXPIRED)) == events


def test_the_sweep_publishes_the_expiry_event_with_the_audit_sentence(
    store: RecordStore, room: str
):
    engine = ExpiryEngine(store, now=lambda: NOW)
    view = send(
        engine,
        room,
        **{
            vocab.EXPIRES_AT: days_out(10),
            "signatures": [{"email": "missed@example.com"}],
        },
    )
    later = ExpiryEngine(store, now=lambda: NOW + timedelta(days=11))
    later.sweep(room, source=SWEEP)

    events = later.events(room, view["id"], event=vocab.EVENT_EXPIRED)
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["expired_at"] == view["expires_at"]
    assert payload["did_not_sign"] == ["missed@example.com"]
    assert "expired at" in payload["audit_note"]
    assert "missed@example.com" in payload["audit_note"]


def test_the_sweep_is_scoped_to_one_room(store: RecordStore, room: str):
    """A sweep in one room must not close an agreement in another.

    The request in the other room still reads as expired, because its deadline
    has passed and the status is derived rather than stored. What the sweep did
    not do is write to it: no audit row, no new revision, no closed flag.
    """
    other = store.create("room", {"name": "Contoso", "account": "Contoso"}, actor="test")["id"]
    engine = ExpiryEngine(store, now=lambda: NOW)
    view = send(engine, other, **{vocab.EXPIRES_AT: days_out(10)})
    before = store.get(view["id"])["revision"]

    later = ExpiryEngine(store, now=lambda: NOW + timedelta(days=11))
    report = later.sweep(room, source=SWEEP)
    assert report["swept_count"] == 0
    assert [row["id"] for row in report["skipped"]] == []

    after = store.get(view["id"])
    assert after["revision"] == before
    assert not after["data"].get("closed")
    assert after["data"]["signatures"][0]["status_code"] == "awaiting_signature"


def test_the_sweep_can_be_narrowed_to_one_request(store: RecordStore, room: str):
    first_deadline = days_out(10)
    second_deadline = days_out(20)
    engine = ExpiryEngine(store, now=lambda: NOW)
    first = send(engine, room, **{vocab.EXPIRES_AT: first_deadline})
    send(engine, room, **{vocab.EXPIRES_AT: second_deadline})
    listed = store.list(vocab.EXPIRY_COLLECTION, limit=10)

    later = ExpiryEngine(store, now=lambda: NOW + timedelta(days=11))
    report = later.sweep(room, {"request_id": first["id"]}, source=SWEEP)
    assert [row["id"] for row in report["swept"]] == [first["id"]]
    # The other agreement was not written to at all.
    untouched = [row for row in listed if row["id"] != first["id"]][0]
    assert untouched["revision"] == 1
    assert not untouched["data"].get("closed")


def test_an_unknown_request_is_refused(engine: ExpiryEngine, room: str):
    with pytest.raises(rules.ExpiryRequestNotFound):
        engine.request_view("no-such-request")
    with pytest.raises(rules.ExpiryRequestNotFound):
        engine.sign("no-such-request", {"email": "a@example.com"}, source=SIGN)


def test_a_record_from_another_workflow_is_not_read_as_one_of_ours(
    engine: ExpiryEngine, store: RecordStore
):
    """>A feature may not read across into another collection."""
    other = store.create("room", {"name": "Somebody else's room"}, actor="test")
    with pytest.raises(rules.ExpiryRequestNotFound):
        engine.request_view(other["id"])


# --------------------------------------------------------------------------- #
# The projection
# --------------------------------------------------------------------------- #


def test_every_projection_carries_the_invariants(engine: ExpiryEngine, room: str):
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(10)})
    invariants = view["invariants"]
    assert "will still have access to the document" in invariants["document_survives"]
    assert "explicitly set" in invariants["absent_expiry_never_expires"]


def test_the_banner_shows_the_deadline_and_the_field_count(engine: ExpiryEngine, room: str):
    view = send(
        engine,
        room,
        **{
            vocab.EXPIRES_AT: days_out(10),
            "signatures": [
                # A fixed offset, so the banner is testable on any host: an IANA name
                # needs a timezone database this interpreter may not have.
                {"email": "a@example.com", "preferred_timezone": "+05:30"},
                {"email": "b@example.com", "status_code": "signed"},
            ],
        },
    )
    banner = view["signatures"][0]["expiry_banner"]
    assert banner["required_fields"] == 1
    assert banner["offset_minutes"] == 330
    assert banner["known"] is True
    assert banner["epoch_seconds"] == view["expires_at"]


def test_a_signer_with_no_expiry_has_no_banner(engine: ExpiryEngine, room: str):
    """>The absence of a deadline is not a missing field to be invented."""
    view = send(engine, room)
    assert view["signatures"][0]["expiry_banner"] is None


def test_a_callers_timezone_overrides_the_stored_one(engine: ExpiryEngine, room: str):
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(10)})
    overridden = engine.request_view(view["id"], tz_name="-04:00")["expiry_view"]
    assert overridden["offset_minutes"] == -240
    # And it moved only the reading, never the instant the sweep compares.
    assert overridden["epoch_seconds"] == view["expires_at"]


def test_the_request_list_can_be_read_by_room(engine: ExpiryEngine, room: str, store):
    other = store.create("room", {"name": "Contoso", "account": "Contoso"}, actor="test")["id"]
    send(engine, room, **{vocab.EXPIRES_AT: days_out(10)})
    send(engine, other, **{vocab.EXPIRES_AT: days_out(10)})
    assert len(engine.requests(room)) == 1
    assert len(engine.requests(other)) == 1
    assert len(engine.requests()) == 2


def test_a_fields_the_workflow_does_not_read_are_stored_and_served(
    engine: ExpiryEngine, room: str, store: RecordStore
):
    """>A team adding a field must need no coordination with anyone."""
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(10), "deal_value": 42000})
    stored = store.get(view["id"])
    assert stored["data"]["deal_value"] == 42000


def test_the_reminder_ledger_is_readable_by_room_and_request(engine: ExpiryEngine, room: str):
    view = send(engine, room, **{vocab.EXPIRES_AT: days_out(6.5)})
    engine.remind(view["id"], source=REMIND)
    assert len(engine.reminders(room)) == 1
    assert len(engine.reminders(room, view["id"])) == 1
    assert len(engine.reminders("other-room")) == 0
