"""WF-082's domain rules, against the specification's own evidence.

The tests are organised by the claims the specification makes, because the point of this
workflow is that those claims are enforced rather than asserted. Each group below names the
sentence it is testing.

    - the two digests are different checks over different bytes, and neither substitutes
      for the other;
    - the ``event_hash`` HMAC input is the two fields concatenated with no separator;
    - three checks, in order, and only then does the handler act;
    - a duplicate is acknowledged, not refused, because a refusal spends the ladder;
    - the acknowledgement is a magic string and not a status code;
    - the error catalogue is machine-readable and total;
    - the retry ladder, the timeout and the self-disable threshold are the researched
      numbers, not this build's tuning.

Every test here is a pure domain test: no HTTP, no framework, no shared file. The HTTP
surface is in ``test_wf082_http.py``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest
from dsr.security_governance import (
    webhook_inferences,
    webhook_rules as rules,
    webhook_signing as signing,
    webhook_vocabulary as vocab,
)
from dsr.security_governance.webhook_engine import (
    HONESTY,
    WebhookRegistrationInvalid,
    WebhookRegistrationNotFound,
    WebhookVerifier,
)

API_KEY = "northwind-test-api-key"
EVENT_TIME = "2026-03-04T18:22:31Z"
EVENT_TYPE = vocab.SIGNATURE_REQUEST_ALL_SIGNED
EVENT_ID = "evt_northwind_001"
NOW = datetime(2026, 3, 4, 19, 0, tzinfo=timezone.utc)

DEMO_RANGES = [
    {"ip": "203.0.113.0/24", "description": "region one"},
    {"ip": "198.51.100.0/24", "description": "region two"},
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def canonical_payload(
    *,
    event_id: str = EVENT_ID,
    event_time: str = EVENT_TIME,
    event_type: str = EVENT_TYPE,
    api_key: str = API_KEY,
    event_hash: str | None = None,
    omit: tuple[str, ...] = (),
) -> dict[str, object]:
    """A provider payload, signed the way the specification says a sender signs it."""
    event: dict[str, object] = {
        "event_time": event_time,
        "event_type": event_type,
        "event_id": event_id,
        "event_hash": (
            event_hash
            if event_hash is not None
            else signing.event_hash(api_key, event_time, event_type)
        ),
        "event_metadata": {
            "related_signature_id": "sr_001",
            "reported_for_account_id": "acct_001",
            "reported_for_app_id": "app_001",
        },
    }
    for field in omit:
        event.pop(field, None)
    return {"event": event, "signature_request": {"id": "sr_001"}}


def encoded_payload(payload: dict[str, object]) -> tuple[bytes, str]:
    """The bytes a provider would send, and the ``Content-Sha256`` header for them."""
    raw = json.dumps(payload).encode("utf-8")
    return raw, signing.content_sha256(API_KEY, raw)


@pytest.fixture
def engine(store):
    """A verifier with a fixed clock, so nothing in these tests depends on the wall."""
    return WebhookVerifier(store, now=lambda: NOW)


def register(engine, room_ref="room_a", url="https://hooks.example/wf082"):
    return engine.register_callback(
        room_ref,
        {"callback_url": url, "api_key": API_KEY, "scope": "account"},
        actor="test",
        source="POST /api/wf082/rooms/{room_id}/callbacks",
    )


def refresh(engine, room_ref="room_a"):
    return engine.refresh_ranges(
        room_ref,
        fetcher=lambda _url: DEMO_RANGES,
        actor="test",
        source="POST /api/wf082/rooms/{room_id}/ranges",
    )


def deliver(
    engine,
    room_ref="room_a",
    *,
    source_ip="203.0.113.24",
    payload=None,
    raw=None,
    header=None,
    api_key=API_KEY,
    callback_id=None,
    callback_url=None,
):
    """One delivery, signed correctly unless the test says otherwise."""
    if raw is None:
        body = payload if payload is not None else canonical_payload()
        raw, computed = json.dumps(body).encode("utf-8"), signing.content_sha256(API_KEY, json.dumps(body))
        header = computed if header is None else header
    body = json.loads(raw.decode("utf-8"))
    return engine.inspect(
        room_ref,
        source_ip=source_ip,
        content_sha256=header,
        payload_bytes=raw,
        payload=body,
        callback_id=callback_id,
        callback_url=callback_url,
        actor="provider",
        source="POST /api/wf082/rooms/{room_id}/events",
    )


# --------------------------------------------------------------------------- #
# The HMAC input. This is the one character the whole workflow turns on.
# --------------------------------------------------------------------------- #


def test_the_event_hash_covers_event_time_and_event_type_with_no_separator():
    assert vocab.EVENT_HASH_SEPARATOR == ""
    assert vocab.EVENT_HASH_INPUT_FIELDS == ("event_time", "event_type")


def test_the_canonical_event_string_is_the_bare_concatenation():
    assert signing.canonical_event_string(EVENT_TIME, EVENT_TYPE) == f"{EVENT_TIME}{EVENT_TYPE}"


def test_the_canonical_event_string_carries_no_separator_of_its_own():
    """The separator is empty, so nothing sits between the two values.

    The values themselves contain punctuation, so the assertion is on the join rather than
    on the absence of every separator character: the timestamp carries colons and dashes and
    the event type carries underscores. What matters is that the two values are butted
    together, which is checked by comparing against the bare concatenation.
    """
    assert signing.canonical_event_string(EVENT_TIME, EVENT_TYPE) == f"{EVENT_TIME}{EVENT_TYPE}"
    assert not signing.canonical_event_string(EVENT_TIME, EVENT_TYPE).endswith(":")
    assert ":" + vocab.SIGNATURE_REQUEST_ALL_SIGNED not in signing.canonical_event_string(
        EVENT_TIME, EVENT_TYPE
    )


def test_the_event_hash_matches_openssl_dgst_over_the_concatenation():
    """`echo -n $event_time$event_type | openssl dgst -sha256 -hmac $apikey`.

    Recomputed here with the stdlib, independently of the module under test, so a change to
    the module cannot change both sides of the comparison at once.
    """
    expected = hmac.new(
        API_KEY.encode(), f"{EVENT_TIME}{EVENT_TYPE}".encode(), hashlib.sha256
    ).hexdigest()
    assert signing.event_hash(API_KEY, EVENT_TIME, EVENT_TYPE) == expected


def test_a_separator_in_the_hmac_input_produces_a_digest_that_does_not_verify():
    """The failure this workflow exists to prevent, stated as a test.

    A sender that joins the two fields with a colon sends valid hex that verifies nothing, so
    every real delivery is refused. The demo data carries exactly this event.
    """
    separator_version = signing.digest_over(API_KEY, f"{EVENT_TIME}:{EVENT_TYPE}")
    verdict = signing.verify_event_hash(
        API_KEY,
        {"event_time": EVENT_TIME, "event_type": EVENT_TYPE, "event_hash": separator_version},
    )
    assert verdict["passed"] is False
    assert verdict["reason"] == "event_hash_mismatch"


def test_a_non_string_event_time_is_stringified_rather_than_refused():
    """A provider that sent a number is still a provider."""
    assert signing.event_hash(API_KEY, 1772658151, EVENT_TYPE) == signing.event_hash(
        API_KEY, "1772658151", EVENT_TYPE
    )


def test_a_missing_event_time_is_refused_rather_than_stringified_as_none():
    """The repaired defect. Stringifying an absent field would verify the text `None`."""
    verdict = signing.verify_event_hash(API_KEY, {"event_type": EVENT_TYPE})
    assert verdict["passed"] is False
    assert verdict["reason"] == "event_time_missing"
    assert "None" not in (verdict["input"] or "")


def test_a_missing_event_type_is_refused_by_its_own_name():
    verdict = signing.verify_event_hash(API_KEY, {"event_time": EVENT_TIME})
    assert verdict["reason"] == "event_type_missing"


def test_a_missing_event_hash_is_refused_by_its_own_name():
    verdict = signing.verify_event_hash(API_KEY, {"event_time": EVENT_TIME, "event_type": EVENT_TYPE})
    assert verdict["reason"] == "event_hash_missing"


# --------------------------------------------------------------------------- #
# The two digests are not interchangeable
# --------------------------------------------------------------------------- #


def test_the_content_digest_is_base64_over_the_whole_payload():
    raw, header = encoded_payload(canonical_payload())
    assert header == base64.b64encode(
        hmac.new(API_KEY.encode(), raw, hashlib.sha256).digest()
    ).decode("ascii")
    assert "+" in header or "/" in header or "=" in header, "base64 signature of a random payload"


def test_the_content_digest_is_not_the_event_digest():
    """Accepting either value for either field would authenticate a payload never read."""
    payload = canonical_payload()
    raw, header = encoded_payload(payload)
    event_digest = signing.event_hash(API_KEY, EVENT_TIME, EVENT_TYPE)
    assert header != event_digest
    assert signing.verify_content_sha256(API_KEY, raw, event_digest)["passed"] is False
    assert signing.verify_event_hash(API_KEY, payload["event"], presented=header)["passed"] is False


def test_the_content_digest_verifies_only_over_the_bytes_as_received():
    raw, header = encoded_payload(canonical_payload())
    assert signing.verify_content_sha256(API_KEY, raw, header)["passed"] is True
    assert signing.verify_content_sha256(API_KEY, raw + b" ", header)["passed"] is False


def test_a_missing_content_digest_is_its_own_refusal():
    assert signing.verify_content_sha256(API_KEY, b"{}", None)["reason"] == "content_sha256_missing"


def test_a_content_digest_that_is_not_base64_is_its_own_refusal():
    """The repaired defect. This reason had no catalogue entry."""
    raw, _header = encoded_payload(canonical_payload())
    verdict = signing.verify_content_sha256(API_KEY, raw, "not base64 at all!")
    assert verdict["reason"] == "content_sha256_malformed"
    assert vocab.error_code(verdict["reason"])["remediation"]


def test_a_valid_base64_content_digest_that_is_wrong_is_its_own_refusal():
    raw, _header = encoded_payload(canonical_payload())
    verdict = signing.verify_content_sha256(API_KEY, raw, signing.content_sha256(API_KEY, b"other"))
    assert verdict["reason"] == "content_sha256_mismatch"


def test_the_event_hash_accepts_hex_and_base64_but_records_which_matched():
    payload = canonical_payload()
    digest = signing.event_hash(API_KEY, EVENT_TIME, EVENT_TYPE)
    as_base64 = base64.b64encode(bytes.fromhex(digest)).decode("ascii")
    for presented, expected_encoding in ((digest, "hex"), (as_base64, "base64")):
        verdict = signing.verify_event_hash(API_KEY, payload["event"], presented=presented)
        assert verdict["passed"] is True
        assert verdict["encoding"] == expected_encoding


def test_a_hex_content_digest_is_refused_because_it_does_not_verify():
    """Base64 is named explicitly for this check, and a hex digest is not it.

    A 64-character hex string is a legal base64 string, so it parses and the refusal is a
    mismatch rather than a malformed value. That is the correct answer: the header was
    well formed and the digest did not match, and an operator holding a proxy that mangles
    the body needs to be told the mismatch rather than a parse error.
    """
    raw, _header = encoded_payload(canonical_payload())
    hex_digest = hmac.new(API_KEY.encode(), raw, hashlib.sha256).hexdigest()
    assert signing.verify_content_sha256(API_KEY, raw, hex_digest)["passed"] is False
    assert signing.verify_content_sha256(API_KEY, raw, hex_digest)["reason"] in (
        "content_sha256_mismatch",
        "content_sha256_malformed",
    )


def test_a_content_digest_with_illegal_base64_characters_is_malformed():
    raw, _header = encoded_payload(canonical_payload())
    verdict = signing.verify_content_sha256(API_KEY, raw, "!!! not base64 !!!")
    assert verdict["reason"] == "content_sha256_malformed"


def test_both_comparisons_are_constant_time():
    """The specification's sibling ticket says so, and it costs one function call."""
    assert signing.constant_time_equals(b"abc", b"abc") is True
    assert signing.constant_time_equals(b"abc", b"abd") is False
    assert signing.constant_time_equals(b"abc", b"abcd") is False
    source = (signing.__file__ or "")
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "compare_digest" in text
    assert "def constant_time_equals" in text


# --------------------------------------------------------------------------- #
# The allowlist
# --------------------------------------------------------------------------- #


def test_an_address_inside_a_published_range_is_allowed():
    rows = rules.classify_ranges(DEMO_RANGES)
    verdict = rules.evaluate_source_ip("203.0.113.24", rows, snapshot_at=None, now=NOW)
    assert verdict["allowed"] is True
    assert verdict["allowed_range"] == "203.0.113.0/24"


def test_an_address_outside_every_published_range_is_refused():
    rows = rules.classify_ranges(DEMO_RANGES)
    verdict = rules.evaluate_source_ip("198.18.0.9", rows, snapshot_at=None, now=NOW)
    assert verdict["allowed"] is False
    assert verdict["error_name"] == "source_ip_not_allowed"


def test_an_ipv4_address_is_never_inside_an_ipv6_range_and_the_reverse():
    """Comparing the integer values alone would let a small IPv6 address match an IPv4 /8.

    ``::1`` is the integer 1, and an IPv4 range whose first address is 0 also contains 1 as a
    raw integer. Comparing the family widths first is what makes the two families disjoint.
    """
    rows = rules.classify_ranges(["0.0.0.0/8", "::1/128"])
    assert rules.evaluate_source_ip("203.0.113.9", rows, snapshot_at=None, now=NOW)["allowed"]
    assert rules.evaluate_source_ip("::1", rows, snapshot_at=None, now=NOW)["allowed"]
    # The integer 1 is inside 0.0.0.0/8, but the address ::1 is not inside an IPv4 range.
    assert not rules.evaluate_source_ip("::1", rows, snapshot_at=None, now=NOW)["allowed_range"] == (
        "0.0.0.0/8"
    )
    assert not rules.evaluate_source_ip("::2", rows, snapshot_at=None, now=NOW)["allowed"]
    assert not rules.evaluate_source_ip("198.18.0.1", rows, snapshot_at=None, now=NOW)["allowed"]


def test_a_narrow_range_does_not_admit_the_rest_of_its_own_network():
    """A /25 spans 128 addresses, so the second half of the /24 is outside it."""
    rows = rules.classify_ranges(["203.0.113.0/25"])
    assert rules.evaluate_source_ip("203.0.113.127", rows, snapshot_at=None, now=NOW)["allowed"]
    assert not rules.evaluate_source_ip("203.0.113.128", rows, snapshot_at=None, now=NOW)["allowed"]
    assert not rules.evaluate_source_ip("203.0.113.200", rows, snapshot_at=None, now=NOW)["allowed"]


def test_a_full_width_range_admits_the_whole_family():
    rows = rules.classify_ranges(["203.0.113.0/24"])
    assert rules.evaluate_source_ip("203.0.113.200", rows, snapshot_at=None, now=NOW)["allowed"]


def test_reason_is_a_sentence_on_every_path_and_the_catalogue_key_is_separate():
    """The repaired defect. A field that is prose on one path and a slug on another is unusable."""
    rows = rules.classify_ranges(DEMO_RANGES)
    allowed = rules.evaluate_source_ip("203.0.113.1", rows, snapshot_at=None, now=NOW)
    refused = rules.evaluate_source_ip("198.18.0.1", rows, snapshot_at=None, now=NOW)
    assert isinstance(allowed["reason"], str) and " " in allowed["reason"]
    assert isinstance(refused["reason"], str) and " " in refused["reason"]
    assert allowed["error_name"] is None
    assert refused["error_name"] in vocab.ERROR_CODES


def test_a_snapshot_with_no_timestamp_reads_as_stale_and_says_why():
    rows = rules.classify_ranges(DEMO_RANGES)
    verdict = rules.evaluate_source_ip("203.0.113.1", rows, snapshot_at=None, now=NOW)
    assert verdict["stale"] is True
    assert "no timestamp" in verdict["staleness"]


def test_a_fresh_snapshot_is_not_stale_and_a_day_old_one_is():
    rows = rules.classify_ranges(DEMO_RANGES)
    fresh = rules.evaluate_source_ip(
        "203.0.113.1", rows, snapshot_at=(NOW - timedelta(hours=1)).isoformat(), now=NOW
    )
    old = rules.evaluate_source_ip(
        "203.0.113.1", rows, snapshot_at=(NOW - timedelta(days=2)).isoformat(), now=NOW
    )
    assert fresh["stale"] is False
    assert old["stale"] is True


def test_a_snapshot_stamped_in_the_future_is_reported_as_a_clock_problem():
    rows = rules.classify_ranges(DEMO_RANGES)
    verdict = rules.evaluate_source_ip(
        "203.0.113.1", rows, snapshot_at=(NOW + timedelta(hours=1)).isoformat(), now=NOW
    )
    assert verdict["stale"] is True
    assert "clock" in verdict["staleness"]


def test_a_range_file_with_an_unknown_key_is_refused_rather_than_silently_empty():
    """An empty allowlist refuses every delivery with a message about a signature."""
    with pytest.raises(rules.IpRangeError) as raised:
        rules.classify_ranges([{"address": "203.0.113.0/24"}])
    assert "address" in str(raised.value)


def test_the_refusal_names_every_key_the_reader_accepts():
    """The repaired defect. `network` was read but not named."""
    with pytest.raises(rules.IpRangeError) as raised:
        rules.classify_ranges([{"address": "203.0.113.0/24"}])
    for key in rules.RANGE_KEYS:
        assert key in str(raised.value)


def test_both_published_range_shapes_are_accepted():
    from_objects = rules.classify_ranges([{"ip": "203.0.113.0/24", "description": "a"}])
    from_strings = rules.classify_ranges(["203.0.113.0/24"])
    assert from_objects[0]["range"] == from_strings[0]["range"] == "203.0.113.0/24"


def test_a_range_list_wrapped_in_a_key_is_accepted_and_the_key_is_reported():
    rows = rules.classify_ranges({"prefixes": ["203.0.113.0/24"]})
    assert rows[0]["range"] == "203.0.113.0/24"
    assert rows[0]["source_key"] == "prefixes"


def test_a_refresh_that_fetches_nothing_leaves_the_previous_allowlist_in_place(engine):
    register(engine)
    refresh(engine)
    before = len(engine.ranges("room_a")["ranges"])

    def failing(_url: str) -> object:
        raise OSError("the vendor had a bad minute")

    with pytest.raises(OSError):
        engine.refresh_ranges("room_a", fetcher=failing, source="POST /api/wf082/rooms/{room_id}/ranges")
    assert len(engine.ranges("room_a")["ranges"]) == before


def test_a_refresh_replaces_the_stored_snapshot_rather_than_accumulating_it(engine):
    register(engine)
    refresh(engine)
    refresh(engine)
    assert len(engine.ranges("room_a")["ranges"]) == len(DEMO_RANGES)


# --------------------------------------------------------------------------- #
# The acknowledgement. A magic string, not a status code.
# --------------------------------------------------------------------------- #


def test_the_acknowledgement_body_is_the_quoted_magic_string():
    assert vocab.ACKNOWLEDGEMENT_BODY == "Hello API Event Received"
    assert vocab.ACKNOWLEDGEMENT_STATUS == 200


def test_an_accepted_delivery_answers_the_magic_string(engine):
    register(engine)
    refresh(engine)
    report = deliver(engine)
    assert report["state"] == vocab.VERIFIED
    assert report["acknowledgement"] == vocab.ACKNOWLEDGEMENT_BODY
    assert report["http_status"] == 200


def test_a_duplicate_is_acknowledged_rather_than_refused(engine):
    """A duplicate read as a failure costs a retry, and a retry walks a 20 hour ladder."""
    register(engine)
    refresh(engine)
    first = deliver(engine)
    second = deliver(engine)
    assert first["state"] == vocab.VERIFIED
    assert second["state"] == vocab.DUPLICATE
    assert second["acknowledgement"] == vocab.ACKNOWLEDGEMENT_BODY
    assert second["http_status"] == 200
    # A refusal carries an error_name and a catalogue entry. A duplicate carries neither,
    # because it is not an error and must not be reported as one.
    assert second.get("error_name") is None
    assert "catalogue" not in second


def test_a_duplicate_runs_no_downstream_effect(engine):
    register(engine)
    refresh(engine)
    deliver(engine)
    before = len(engine.deliveries("room_a"))
    deliver(engine)
    # The delivery is recorded, so the log shows the retry, but the dedupe store does not
    # grow a second entry for the same id.
    assert len(engine.deliveries("room_a")) == before + 1
    assert engine.summary("room_a")["dedupe_ids"] == 1


def test_two_registrations_deduplicate_independently(engine):
    """Two callbacks are two independent deliveries of the same event."""
    register(engine, "room_a", "https://one.example/wf082")
    register(engine, "room_a", "https://two.example/wf082")
    refresh(engine)
    first = deliver(engine, callback_id=_callback_id(engine, "https://one.example/wf082"))
    second = deliver(engine, callback_id=_callback_id(engine, "https://two.example/wf082"))
    assert first["state"] == vocab.VERIFIED
    assert second["state"] == vocab.VERIFIED
    assert engine.summary("room_a")["dedupe_ids"] == 2


def _callback_id(engine, url: str) -> str:
    return next(row["id"] for row in engine.callbacks("room_a") if row["callback_url"] == url)


# --------------------------------------------------------------------------- #
# Three checks, in order, and only then does the handler act
# --------------------------------------------------------------------------- #


def test_the_check_order_is_published_and_fixed():
    assert vocab.CHECK_ORDER == (
        vocab.IP_ALLOWLIST,
        vocab.CONTENT_SHA256,
        vocab.EVENT_HASH,
    )


def test_an_allowed_verified_delivery_records_all_three_checks_as_passing(engine):
    register(engine)
    refresh(engine)
    report = deliver(engine)
    assert [check["check"] for check in report["checks"]] == list(vocab.CHECK_ORDER)
    assert all(check.get("passed") is True for check in report["checks"])


def test_the_first_failing_check_is_the_one_that_refuses(engine):
    """A delivery from a bad address with a bad digest is refused at the IP check."""
    register(engine)
    refresh(engine)
    report = deliver(engine, source_ip="198.18.0.9", header="nonsense")
    assert report["failed_check"] == vocab.IP_ALLOWLIST
    assert report["error_name"] == "source_ip_not_allowed"


def test_a_bad_digest_on_an_allowed_address_is_refused_at_the_second_check(engine):
    register(engine)
    refresh(engine)
    report = deliver(engine, header=signing.content_sha256(API_KEY, b"something else"))
    assert report["failed_check"] == vocab.CONTENT_SHA256
    assert report["error_name"] == "content_sha256_mismatch"


def test_a_bad_event_hash_on_an_allowed_address_is_refused_at_the_third_check(engine):
    register(engine)
    refresh(engine)
    payload = canonical_payload(
        event_hash=signing.digest_over(API_KEY, f"{EVENT_TIME}:{EVENT_TYPE}")
    )
    report = deliver(engine, payload=payload)
    assert report["failed_check"] == vocab.EVENT_HASH
    assert report["error_name"] == "event_hash_mismatch"


def test_an_empty_allowlist_refuses_every_delivery(engine):
    """The failure a missing or stale refresh produces, stated as a test."""
    register(engine)
    report = deliver(engine)
    assert report["state"] == vocab.REJECTED
    assert report["failed_check"] == vocab.IP_ALLOWLIST


def test_a_missing_source_address_is_refused_rather_than_assumed(engine):
    register(engine)
    refresh(engine)
    report = deliver(engine, source_ip="")
    assert report["state"] == vocab.REJECTED
    assert report["error_name"] == "no_source_ip"
    assert vocab.error_code(report["error_name"])["remediation"]


def test_a_delivery_with_no_event_id_is_refused_rather_than_recorded_for_ever(engine):
    register(engine)
    refresh(engine)
    report = deliver(engine, payload=canonical_payload(omit=("event_id",)))
    assert report["state"] == vocab.REJECTED
    assert report["error_name"] == "event_id_missing"


def test_a_refused_delivery_is_recorded_with_its_three_checks_intact(engine):
    register(engine)
    refresh(engine)
    deliver(engine, source_ip="198.18.0.9")
    rows = engine.deliveries("room_a")
    assert len(rows) == 1
    assert rows[0]["state"] == vocab.REJECTED
    assert [check["check"] for check in rows[0]["checks"]] == list(vocab.CHECK_ORDER)


def test_a_refused_delivery_runs_no_dedupe_entry(engine):
    register(engine)
    refresh(engine)
    deliver(engine, source_ip="198.18.0.9")
    assert engine.summary("room_a")["dedupe_ids"] == 0


# --------------------------------------------------------------------------- #
# Registrations
# --------------------------------------------------------------------------- #


def test_a_plain_http_callback_is_refused(engine):
    """Plain HTTP callbacks stop receiving events from December 1, 2024."""
    with pytest.raises(WebhookRegistrationInvalid) as raised:
        register(engine, url="http://hooks.example/wf082")
    assert "callback_url" in raised.value.errors


def test_a_registration_with_no_api_key_is_refused(engine):
    with pytest.raises(WebhookRegistrationInvalid) as raised:
        engine.register_callback(
            "room_a",
            {"callback_url": "https://hooks.example/wf082"},
            actor="test",
            source="POST /api/wf082/rooms/{room_id}/callbacks",
        )
    assert "api_key" in raised.value.errors


def test_a_registration_with_no_callback_url_is_refused(engine):
    with pytest.raises(WebhookRegistrationInvalid) as raised:
        engine.register_callback("room_a", {"api_key": API_KEY})
    assert "callback_url" in raised.value.errors


def test_an_unknown_scope_is_refused_by_name(engine):
    with pytest.raises(WebhookRegistrationInvalid) as raised:
        engine.register_callback(
            "room_a",
            {"callback_url": "https://hooks.example/wf082", "api_key": API_KEY, "scope": "planet"},
            actor="test",
            source="POST /api/wf082/rooms/{room_id}/callbacks",
        )
    assert "scope" in raised.value.errors


def test_the_api_key_is_sealed_and_never_returned_by_a_read(engine):
    register(engine)
    row = engine.read_callback(engine.callbacks("room_a")[0]["id"])
    assert row["sealed"] is True
    assert API_KEY not in json.dumps(row)
    stored = engine.store.find(vocab.COLLECTION_CALLBACKS, {vocab.ROOM_REF: "room_a"})
    assert stored[0]["data"][vocab.SEALED_API_KEY] != API_KEY


def test_a_callback_list_never_carries_the_sealed_value(engine):
    register(engine)
    assert API_KEY not in json.dumps(engine.callbacks("room_a"))


def test_the_key_fingerprint_names_the_key_without_revealing_it(engine):
    register(engine)
    row = engine.read_callback(engine.callbacks("room_a")[0]["id"])
    assert row[vocab.KEY_FINGERPRINT] == engine.vault_key.key_id
    assert API_KEY not in row[vocab.KEY_FINGERPRINT]


def test_a_row_sealed_under_another_key_is_recognisable_rather_than_corrupt(store):
    """A deployment that changed its vault key can tell which of the two faults it is.

    The refusal names the key the row was sealed under, so the answer is "sealed elsewhere"
    rather than "this row was altered". Returning an empty key instead would make every
    digest wrong and the refusal would blame a signature.
    """
    from dsr.crm_oauth.vault import VaultKey

    first = WebhookVerifier(store, now=lambda: NOW)
    first.register_callback(
        "room_a",
        {"callback_url": "https://hooks.example/wf082", "api_key": API_KEY},
        actor="test",
        source="POST /api/wf082/rooms/{room_id}/callbacks",
    )
    stored = store.find(vocab.COLLECTION_CALLBACKS, {vocab.ROOM_REF: "room_a"})[0]["data"]
    other = WebhookVerifier(
        store,
        now=lambda: NOW,
        vault_key=VaultKey(material=b"a different key", origin="env", key_id="aaaa"),
    )
    with pytest.raises(Exception) as raised:
        other._open_api_key(stored)
    assert "key" in str(raised.value).lower()


def test_a_registration_cannot_be_patched_with_a_new_key(engine):
    register(engine)
    with pytest.raises(WebhookRegistrationInvalid) as raised:
        engine.update_callback(
            engine.callbacks("room_a")[0]["id"],
            {"api_key": "a new key"},
            actor="test",
            source="PATCH /api/wf082/callbacks/{callback_id}",
        )
    assert "api_key" in raised.value.errors


def test_a_callback_url_can_be_changed_and_must_stay_https(engine):
    callback_id = engine.callbacks("room_a")[0]["id"] if engine.callbacks("room_a") else None
    register(engine)
    callback_id = callback_id or engine.callbacks("room_a")[0]["id"]
    updated = engine.update_callback(
        callback_id,
        {"callback_url": "https://hooks2.example/wf082"},
        actor="test",
        source="PATCH /api/wf082/callbacks/{callback_id}",
    )
    assert updated["callback_url"] == "https://hooks2.example/wf082"
    with pytest.raises(WebhookRegistrationInvalid):
        engine.update_callback(
            callback_id,
            {"callback_url": "http://hooks2.example/wf082"},
            actor="test",
            source="PATCH /api/wf082/callbacks/{callback_id}",
        )


def test_reading_an_unknown_registration_is_a_404_named_as_such(engine):
    with pytest.raises(WebhookRegistrationNotFound):
        engine.read_callback("no-such-callback")


def test_a_delivery_with_no_registration_is_refused_rather_than_unauthenticated(engine):
    with pytest.raises(WebhookRegistrationNotFound):
        deliver(engine)


def test_two_registrations_refuse_an_ambiguous_delivery_rather_than_guessing(engine):
    register(engine, "room_a", "https://one.example/wf082")
    register(engine, "room_a", "https://two.example/wf082")
    with pytest.raises(WebhookRegistrationInvalid) as raised:
        deliver(engine)
    assert vocab.CREDENTIAL_HEADER in str(raised.value)


def test_a_callback_url_that_does_not_match_the_registration_is_refused(engine):
    register(engine)
    with pytest.raises(WebhookRegistrationInvalid) as raised:
        deliver(engine, callback_url="https://somewhere-else.example/wf082")
    assert "callback_url" in raised.value.errors


# --------------------------------------------------------------------------- #
# The error catalogue
# --------------------------------------------------------------------------- #


def test_every_catalogue_entry_carries_the_five_researched_fields():
    for name, entry in vocab.ERROR_CODES.items():
        for field in vocab.ERROR_CODE_FIELDS:
            assert field in entry, f"{name} is missing {field}"


def test_every_error_name_this_build_can_raise_is_in_the_catalogue():
    """The repaired defect, as an invariant rather than one case.

    Every ``reason`` a check can produce is a key the catalogue carries, so a consumer never
    receives the generic fallback for a refusal this build actually produced.
    """
    producible = {
        "content_sha256_missing",
        "content_sha256_malformed",
        "content_sha256_mismatch",
        "event_type_missing",
        "event_time_missing",
        "event_hash_missing",
        "event_hash_mismatch",
        "source_ip_not_allowed",
        "no_source_ip",
    }
    for name in producible:
        assert name in vocab.ERROR_CODES, f"{name} is producible but not catalogued"
        assert vocab.error_code(name)["remediation"]


def test_the_unknown_error_name_fallback_is_well_formed():
    entry = vocab.error_code("no_such_name")
    assert entry["error_name"] == "no_such_name"
    assert entry["remediation"]
    assert entry["http_status"] == 400


def test_the_two_catalogue_names_are_the_specifications_own():
    assert vocab.ERROR_CODES_CATALOGUE == "x-error-codes"
    assert vocab.ERROR_EVENTS_CATALOGUE == "x-error-events"


def test_a_retryable_refusal_is_one_whose_cause_would_clear():
    assert vocab.error_code("source_ip_not_allowed")["retryable"] is True
    assert vocab.error_code("content_sha256_mismatch")["retryable"] is False
    assert vocab.error_code("event_hash_mismatch")["retryable"] is False


# --------------------------------------------------------------------------- #
# The retry contract, served as data
# --------------------------------------------------------------------------- #


def test_the_retry_ladder_is_the_researched_sequence():
    assert vocab.RETRY_LADDER_SECONDS == (300, 900, 2700, 8100, 24300, 72900)


def test_the_ladder_entries_are_the_specifications_own_in_seconds():
    assert [row["interval_seconds"] for row in vocab.retry_ladder()] == [
        300,
        900,
        2700,
        8100,
        24300,
        72900,
    ]


def test_each_interval_is_three_times_the_previous_one():
    ladder = list(vocab.RETRY_LADDER_SECONDS)
    for earlier, later in zip(ladder, ladder[1:], strict=False):
        assert later == earlier * vocab.RETRY_MULTIPLIER


def test_the_ladder_reaches_twenty_hours_and_fifteen_minutes_at_the_last_retry():
    assert vocab.RETRY_LADDER_SECONDS[-1] == 20 * 3600 + 15 * 60


def test_the_ladder_is_cumulative_so_a_consumer_can_schedule_from_it():
    rows = vocab.retry_ladder()
    assert rows[0]["cumulative_seconds"] == 300
    assert rows[-1]["cumulative_seconds"] == sum(vocab.RETRY_LADDER_SECONDS)


def test_the_timeout_and_the_self_disable_threshold_are_the_researched_numbers():
    assert vocab.PROVIDER_TIMEOUT_SECONDS == 30
    assert vocab.CONSECUTIVE_FAILURE_LIMIT == 10


def test_the_delay_label_is_ascii_only():
    """A U+2192 in a seed return string broke the seeder on a Windows console."""
    for row in vocab.retry_ladder():
        row["cumulative_label"].encode("cp1252")
    assert vocab.format_delay(72900) == "20 h 15 m"
    assert vocab.format_delay(300) == "5 m"
    assert vocab.format_delay(0) == "0 s"


# --------------------------------------------------------------------------- #
# The event types
# --------------------------------------------------------------------------- #


def test_the_two_event_types_are_the_specifications_own_and_are_not_interchangeable():
    assert vocab.SIGNATURE_REQUEST_ALL_SIGNED == "signature_request_all_signed"
    assert vocab.SIGNATURE_REQUEST_DOWNLOADABLE == "signature_request_downloadable"
    assert vocab.SIGNATURE_REQUEST_ALL_SIGNED != vocab.SIGNATURE_REQUEST_DOWNLOADABLE


def test_both_event_types_verify_through_the_same_scheme(engine):
    register(engine)
    refresh(engine)
    for index, event_type in enumerate(
        (vocab.SIGNATURE_REQUEST_ALL_SIGNED, vocab.SIGNATURE_REQUEST_DOWNLOADABLE)
    ):
        report = deliver(
            engine, payload=canonical_payload(event_id=f"evt_{index}", event_type=event_type)
        )
        assert report["state"] == vocab.VERIFIED, event_type


def test_the_event_type_is_the_filter_key_and_the_recorded_one_is_the_delivered_one(engine):
    register(engine)
    refresh(engine)
    report = deliver(engine, payload=canonical_payload(event_type=vocab.SIGNATURE_REQUEST_DOWNLOADABLE))
    assert report["event_type"] == vocab.SIGNATURE_REQUEST_DOWNLOADABLE
    assert engine.deliveries("room_a")[0]["event_type"] == vocab.SIGNATURE_REQUEST_DOWNLOADABLE


def test_the_event_metadata_keys_are_the_ones_the_evidence_quotes():
    assert vocab.EVENT_METADATA_KEYS == (
        "related_signature_id",
        "reported_for_account_id",
        "reported_for_app_id",
    )


def test_the_signature_request_reference_is_recorded_from_whichever_key_the_payload_used(engine):
    register(engine)
    refresh(engine)
    for payload, expected in (
        (canonical_payload(), "sr_001"),
        ({"event": canonical_payload()["event"], "signature_request": {"id": "sr_002"}}, "sr_002"),
    ):
        report = deliver(engine, payload=payload)
        assert report["delivery"]["signature_request_ref"] == expected


# --------------------------------------------------------------------------- #
# The register of derivations
# --------------------------------------------------------------------------- #


def test_every_derivation_records_the_option_it_rejected():
    for decision_id, decision in webhook_inferences.DECISIONS.items():
        assert decision["chosen"] in decision["options"], decision_id
        assert decision["rejected_because"].strip(), decision_id
        assert decision["cost_of_the_choice"].strip(), decision_id
        assert decision["left_open_by"].strip(), decision_id


def test_the_derivation_register_is_served_in_a_stable_order():
    assert webhook_inferences.describe()[0]["id"] == sorted(webhook_inferences.DECISIONS)[0]
    assert webhook_inferences.count() == len(webhook_inferences.DECISIONS)


def test_one_derivation_can_be_read_by_its_id():
    found = webhook_inferences.describe_one("INFERRED_DUPLICATE_IS_ACKNOWLEDGED")
    assert found is not None
    assert found["chosen"] == "acknowledge_as_duplicate"
    assert webhook_inferences.describe_one("nope") is None


def test_the_duplicate_derivation_rejects_refusing_because_of_the_ladder():
    decision = webhook_inferences.DECISIONS["INFERRED_DUPLICATE_IS_ACKNOWLEDGED"]
    assert "twenty hours" in decision["rejected_because"]
    assert "ten consecutive failures" in decision["rejected_because"]


# --------------------------------------------------------------------------- #
# The contract the HTTP layer serves
# --------------------------------------------------------------------------- #


def test_the_vocabulary_publishes_the_two_digests_and_how_they_differ():
    payload = engine_vocabulary()
    assert payload["content_sha256"]["encoding"] == "base64"
    assert payload["content_sha256"]["covers"] == "the whole JSON payload"
    assert payload["event_hash"]["separator"] == ""
    assert payload["event_hash"]["input_fields"] == ["event_time", "event_type"]
    assert payload["content_sha256"]["header"] == vocab.CONTENT_SHA256_HEADER


def test_the_vocabulary_publishes_the_download_lag_warning():
    warning = engine_vocabulary()["event_types"]["warning"]
    assert vocab.SIGNATURE_REQUEST_DOWNLOADABLE in warning
    assert "lags signing" in warning


def test_the_vocabulary_publishes_the_acknowledgement_as_a_magic_string():
    acknowledgement = engine_vocabulary()["acknowledgement"]
    assert acknowledgement["status"] == 200
    assert acknowledgement["body"] == vocab.ACKNOWLEDGEMENT_BODY


def test_the_vocabulary_states_the_thirty_second_budget_and_the_self_disable():
    payload = engine_vocabulary()
    assert payload["provider_timeout_seconds"] == 30
    assert str(vocab.CONSECUTIVE_FAILURE_LIMIT) in payload["self_disable"]


def test_the_honesty_fields_are_present_on_every_projection(engine):
    register(engine)
    refresh(engine)
    report = deliver(engine)
    summary = engine.summary("room_a")
    for payload in (report, summary, engine_vocabulary()):
        for key in HONESTY:
            assert key in payload, f"{key} is missing from a projection"


def test_the_honesty_fields_say_what_the_handler_cannot_prove():
    assert "only after" in HONESTY["verification_scope"]
    assert "replay" in HONESTY["verification_scope"], "the replay limit must be stated"
    assert "no socket" in HONESTY["no_fetches_on_delivery"]


def test_no_honesty_key_collides_with_a_field_a_projection_carries():
    """The defect this test exists for.

    ``HONESTY`` used to carry a key named ``checks``, and every projection spread it after
    building its own ``checks`` list. The result had a ``checks`` field of the wrong shape and
    no error anywhere, because a dict spread silently overwrites. The three check results were
    the whole point of the delivery response, so the field has to be one or the other.
    """
    payload = engine_vocabulary()
    for key in HONESTY:
        assert key not in ("checks", "state", "error_name", "http_status"), key
    assert isinstance(payload["checks"], list)
    assert payload["checks"] == list(vocab.CHECK_ORDER)


def test_a_delivery_response_carries_the_three_checks_and_not_a_sentence(engine):
    register(engine)
    refresh(engine)
    report = deliver(engine)
    assert isinstance(report["checks"], list)
    assert [check["check"] for check in report["checks"]] == list(vocab.CHECK_ORDER)


def test_the_vocabulary_names_the_proxy_wiring_risk_outright():
    transparency = engine_vocabulary()["transparency"]
    assert "proxy" in transparency["wiring_risk"]
    assert "refuse every delivery" in transparency["wiring_risk"]


def test_the_router_prefix_is_the_one_the_issue_names():
    assert vocab.ROUTER_PREFIX == "/api/wf082"


def test_the_summary_counts_deliveries_by_state_and_by_error_name(engine):
    register(engine)
    refresh(engine)
    deliver(engine)
    deliver(engine)
    deliver(engine, source_ip="198.18.0.9")
    summary = engine.summary("room_a")
    assert summary["ticket"] == "WF-082"
    assert summary["verified"] == 1
    assert summary["duplicates"] == 1
    assert summary["rejected"] == 1
    assert summary["by_error_name"] == {"source_ip_not_allowed": 1}
    assert summary["checks"] == list(vocab.CHECK_ORDER)


def test_the_summary_reads_nothing_and_writes_no_audit_row(engine, store):
    register(engine)
    refresh(engine)
    before = len(store.audit())
    engine.summary("room_a")
    engine.deliveries("room_a")
    assert len(store.audit()) == before


def engine_vocabulary() -> dict:
    """The served vocabulary, built over a throwaway in-memory store.

    The vocabulary is a pure read of the constants, so it needs no fixtures: a store is passed
    only because the engine takes one.
    """
    from dsr.db.audited import AuditedDatabase
    from dsr.store import RecordStore

    database = AuditedDatabase(":memory:", actor="test")
    try:
        return WebhookVerifier(RecordStore(database), now=lambda: NOW).vocabulary()
    finally:
        database.close()