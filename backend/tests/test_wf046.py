"""The domain rules of WF-046, tested against the researched sentences.

The specification is ``docs/research/digital-sales-room-workflows/wf/WF-046.md``
and the resolution is ``orchestration/decisions/WF-046-RESOLUTION.md``. Every test
below names the sentence it is holding up, because the module under test is
mostly a table of vendor quotes and a ladder of chosen numbers: a test that only
checked the shape of the output would pass on a table that had swapped 429 for
418.

What these tests are *for*, in the order the file reads:

* **the classification table** - one signal per vendor answer, each with the
  vendor's own basis, and the two refusals that keep it honest (a bare 403 is a
  permission, and a 423 belongs to the vendor that documents one);
* **the backoff ladder** - the rungs are the ones WF-040 shipped, the jitter is
  derived rather than drawn, and ``Retry-After`` is a floor rather than a
  ceiling;
* **the token bucket** - the pre-emptive half no shipped code had, including the
  three edges where a token bucket is usually wrong;
* **the policies** - the three vendors, and the line between a sourced number and
  a gap the research recorded;
* **the quota headers** - ``Sforce-Limit-Info`` and the HubSpot family, read for
  the one question a bucket asks;
* **the idempotency keys** - stable across attempts, distinct across rooms, and
  refused when a row has no external id;
* **the engine** - the five researched steps, end to end;
* **the delegation** - the last section is the resolution's own requirement: the
  429 classification and the ladder now live in ``dsr.throttle`` and the two
  sibling packages read them rather than copying them.

Every test builds its own store from the ``store`` fixture in ``conftest.py``, so
the file passes on its own and in any order under pytest-xdist.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from dsr.throttle import (
    backoff as throttle_backoff,
    bucket as throttle_bucket,
    classify as throttle_classify,
    keys as throttle_keys,
    policies as throttle_policies,
    quota as throttle_quota,
    timestamps as throttle_timestamps,
    vocabulary,
)
from dsr.throttle.engine import ThrottleEngine
from dsr.throttle.errors import (
    InvalidPayload,
    UnknownBatch,
    UnknownConnection,
    UnknownPolicy,
    UnknownRoom,
)

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def clock() -> dict[str, datetime]:
    """A clock a test moves by hand, so a deferral can be aged without sleeping."""
    return {"now": NOW}


@pytest.fixture()
def engine(store, clock) -> ThrottleEngine:
    """An engine over a fresh store and a movable clock."""
    return ThrottleEngine(store, clock=lambda: clock["now"])


@pytest.fixture()
def registered_policy():
    """Restore the process-wide policy registry after a test that registers one.

    **This fixture exists because the suite was order-dependent, and the cause was
    here.** :func:`dsr.throttle.policies.register` is the researched extension
    point and writes into a module-level registry that every later
    ``create_connection`` consults. A test that registers ``acme`` therefore leaves
    ``acme`` behind for the rest of the worker process, and under ``-n 0`` - or
    under xdist whenever two tests share a worker - a later test that declares an
    ``acme`` connection gets the *registered* window merged under its request. That
    is how ``test_a_policy_that_could_never_refill_is_422_not_400`` passed alone,
    passed under the default sharding, and failed under a single worker: with
    ``acme`` already registered it had a ``window_seconds`` to pair with its
    ``sustained``, so the policy was valid and the route answered 201 instead of
    422.

    Snapshot and restore, rather than a unique vendor name per test: a unique name
    only hides the next collision, and the property worth keeping is that *no* test
    in this file can change what another one sees.
    """
    before = dict(throttle_policies._REGISTRY)  # noqa: SLF001 - the registry is module state
    try:
        yield throttle_policies.register
    finally:
        throttle_policies._REGISTRY.clear()  # noqa: SLF001
        throttle_policies._REGISTRY.update(before)  # noqa: SLF001


@pytest.fixture()
def room(store) -> dict:
    return store.create(
        "room", {"name": "Northwind", "account": "Northwind Traders"}, source="test"
    )


def make_connection(engine: ThrottleEngine, room, vendor: str = "hubspot", **policy) -> dict:
    """A declared connection, with the policy fields a test needs."""
    return engine.create_connection(
        {"vendor": vendor, "policy": policy or None},
        room_id=room["id"],
        actor="dana",
        source="test",
    )


def rows(count: int, prefix: str = "ext") -> list[dict]:
    return [{"external_id": f"{prefix}-{index}"} for index in range(count)]


# --------------------------------------------------------------------------- #
# The classification table
# --------------------------------------------------------------------------- #


def test_a_429_is_the_rate_limit_signal_on_every_vendor():
    """HubSpot: "Any app or integration exceeding its rate limits will receive a
    429 error response for all subsequent API calls." Dataverse: "429 Too Many
    Requests Expect this status code when API limits are exceeded." """
    for vendor in ("hubspot", "salesforce", "dataverse", "acme"):
        signal = throttle_classify.signal_for(vendor, 429)
        assert signal is not None, vendor
        assert signal.id == "throttled"
        assert signal.kind == "rate_limit"
        assert signal.retryable is True


def test_a_403_with_the_salesforce_code_is_a_throttle_because_the_code_is_the_signal():
    """Quoted: "If the error code is REQUEST_LIMIT_EXCEEDED, you've exceeded API
    request limits in your org." The research adds that 429-class throttling is
    surfaced as REQUEST_LIMIT_EXCEEDED, so the code outranks the status."""
    signal = throttle_classify.signal_for("salesforce", 403, "REQUEST_LIMIT_EXCEEDED")
    assert signal.id == "salesforce-request-limit-exceeded"
    assert signal.status == 403
    assert signal.code == "REQUEST_LIMIT_EXCEEDED"


def test_the_request_limit_code_is_a_throttle_whatever_status_carries_it():
    """The code is the discriminator, so a 400 carrying it is still a throttle."""
    assert (
        throttle_classify.signal_for("salesforce", 400, "REQUEST_LIMIT_EXCEEDED").kind
        == "rate_limit"
    )


def test_a_403_without_that_code_is_not_a_throttle():
    """The researched breakdown puts a permission in `validation`. A 403 that is
    not a request limit is a permission, and calling it a throttle would empty
    every queue in the product."""
    assert throttle_classify.signal_for("salesforce", 403, "FORBIDDEN") is None
    assert throttle_classify.is_throttle_status(403) is False


def test_a_hubspot_423_is_a_lock_and_carries_the_vendors_own_two_second_floor():
    """Quoted: "Locks will last for 2 seconds, so if you receive a 423 error, you
    should include a delay of at least 2 seconds between your API requests." The
    floor is the vendor's, so it is a constant read from the documentation."""
    signal = throttle_classify.signal_for("hubspot", 423)
    assert signal.id == "hubspot-sync-lock"
    assert signal.kind == "lock"
    assert signal.minimum_delay_seconds == throttle_classify.LOCK_FLOOR_SECONDS == 2


def test_a_423_is_not_handed_to_a_vendor_that_has_documented_none():
    """Both 423 and 477 are quoted from HubSpot only. A vendor this build has no
    quote for is refused rather than answered from another vendor's sentence."""
    assert throttle_classify.signal_for("dataverse", 423) is None
    assert throttle_classify.signal_for("salesforce", 477) is None


def test_a_477_is_a_migration_and_says_so_in_its_own_basis():
    """Quoted: "HubSpot will return a Retry-After response header indicating how
    many seconds to wait before retrying the request (typically up to 24 hours)." """
    signal = throttle_classify.signal_for("hubspot", 477)
    assert signal.kind == "migration"
    assert signal.retryable is True
    assert "Retry-After" in signal.basis


def test_the_sourced_transient_classes_and_the_unsourced_ones_are_reported_apart():
    """Quoted: "502/504/503/521/522/523/524/525/526 transient classes". A 5xx
    outside that list is still retryable, and is named differently so the
    difference between a sourced list and a range stays visible."""
    sourced = throttle_classify.signal_for("hubspot", 524)
    assert sourced.id == "transient-vendor-class"
    assert 524 in throttle_classify.TRANSIENT_STATUSES

    read_off_the_range = throttle_classify.signal_for("hubspot", 501)
    assert read_off_the_range.id == "vendor-fault"
    assert read_off_the_range.retryable is True


def test_a_success_is_not_a_signal():
    assert throttle_classify.signal_for("hubspot", 201) is None
    assert throttle_classify.signal_for("hubspot", None) is None


def test_a_status_that_is_not_a_number_is_refused_rather_than_guessed():
    with pytest.raises(InvalidPayload) as caught:
        throttle_classify.signal_for("hubspot", "too many")
    assert "must be a number" in str(caught.value)


def test_every_signal_carries_a_basis_a_reviewer_can_trace():
    """A classification nobody can trace to a sentence is a guess with a boolean
    attached. Every row in the table quotes something, and every row names the
    kind it is."""
    for signal in throttle_classify.SIGNALS:
        assert signal.basis.strip(), signal.id
        assert signal.kind in throttle_classify.THROTTLE_KINDS, signal.id
        assert signal.as_dict()["retryable"] is True


def test_the_catalogue_is_the_table_itself():
    assert [row["id"] for row in throttle_classify.catalogue()] == [
        signal.id for signal in throttle_classify.SIGNALS
    ]


def test_a_header_is_read_case_insensitively():
    """Vendors disagree on capitalisation and a caller may have lower-cased the
    whole mapping."""
    assert throttle_classify.header({"retry-after": "9"}, "Retry-After") == "9"
    assert throttle_classify.header({"Retry-After": "9"}, "retry-after") == "9"
    assert throttle_classify.header(None, "Retry-After") is None
    assert throttle_classify.header({}, "Retry-After") is None


def test_require_vendor_refuses_an_empty_name():
    with pytest.raises(InvalidPayload) as caught:
        throttle_classify.require_vendor("")
    assert "needs a vendor" in str(caught.value)


# --------------------------------------------------------------------------- #
# The backoff ladder
# --------------------------------------------------------------------------- #


def test_the_ladder_is_the_one_wf040_shipped_and_pins_every_rung():
    """30s doubling to 30m. The numbers moved into this package; they did not
    change, and this test is what would say so if they did."""
    assert [throttle_backoff.backoff_seconds(n) for n in range(1, 9)] == [
        30,
        60,
        120,
        240,
        480,
        960,
        1800,
        1800,
    ]
    assert throttle_backoff.BASE_SECONDS == 30
    assert throttle_backoff.MAX_SECONDS == 1800
    assert throttle_backoff.MAX_ATTEMPTS == 5
    assert throttle_backoff.LABEL == "exponential from 30s, doubling, capped at 30m"


def test_an_attempt_that_lost_count_still_gets_a_real_wait():
    """A caller that reset its counter to zero must not get a zero-second wait."""
    assert throttle_backoff.backoff_seconds(0) == 30
    assert throttle_backoff.backoff_seconds(-4) == 30


def test_the_jitter_is_derived_from_the_key_and_never_leaves_its_half():
    """Derived, not drawn: the room stores the wait and a person reads it, so the
    same batch has to produce the same number every time. And it never reaches
    zero, because a retry that beat the vendor's own floor is a retry into the
    limit."""
    assert throttle_backoff.jitter_fraction("abc") == throttle_backoff.jitter_fraction("abc")
    assert throttle_backoff.jitter_fraction("abc") != throttle_backoff.jitter_fraction("abd")
    for seed in ("a", "b", "c", "d", "ws-046", "", "0" * 32):
        assert 0.0 <= throttle_backoff.jitter_fraction(seed) < 1.0

    for attempt in range(1, 9):
        ladder = throttle_backoff.backoff_seconds(attempt)
        for seed in ("k1", "k2", "k3"):
            wait = throttle_backoff.schedule(attempt, seed=seed)["seconds"]
            assert ladder * (1.0 - throttle_backoff.JITTER_RATIO) <= wait <= ladder


def test_retry_after_is_read_in_both_the_spellings_rfc9110_allows():
    assert throttle_backoff.retry_after_seconds({"Retry-After": "45"}) == 45
    assert throttle_backoff.retry_after_seconds({"retry-after": " 45 "}) == 45
    # An HTTP-date that is 90 seconds away from now.
    when = (NOW + timedelta(seconds=90)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert throttle_backoff.retry_after_seconds({"Retry-After": when}, now=NOW) == 90


def test_a_retry_after_the_room_cannot_parse_falls_back_to_the_ladder():
    """Advice that cannot be parsed must not fail the batch that reported it."""
    assert throttle_backoff.retry_after_seconds({"Retry-After": "soon"}) is None
    assert throttle_backoff.retry_after_seconds({"Retry-After": ""}) is None
    assert (
        throttle_backoff.schedule(1, headers={"Retry-After": "soon"}, now=NOW, seed="k")["source"]
        == "backoff"
    )


def test_retry_after_is_clamped_to_the_cap_and_the_clamp_is_reported_separately():
    """The cap is inferred: HubSpot names no bound, so the room takes the vendor's
    own upper word of 24 hours and reports a longer wait rather than taking it."""
    assert throttle_backoff.retry_after_seconds({"Retry-After": "999999"}, now=NOW) == 86_400
    assert throttle_backoff.exceeds_cap("999999") is True
    assert throttle_backoff.exceeds_cap("600") is False

    answer = throttle_backoff.schedule(1, headers={"Retry-After": "999999"}, now=NOW, seed="k")
    assert answer["source"] == "beyond_cap"
    assert answer["seconds"] is None
    assert answer["at"] is None
    assert "waits for a person" in answer["basis"]


def test_a_retry_after_is_honoured_over_the_ladder():
    answer = throttle_backoff.schedule(
        1,
        signal=throttle_classify.signal_for("hubspot", 429),
        headers={"Retry-After": "45"},
        now=NOW,
        seed="k",
    )
    assert answer["seconds"] == 45
    assert answer["source"] == "retry_after"
    assert answer["at"] == "2026-09-27T12:00:45+00:00"


def test_a_retry_after_shorter_than_the_lock_floor_still_waits_the_floor():
    """Inferred: the vendor's advice and the vendor's own documented floor are
    both promises about how long the condition lasts, so the longer one wins.
    A 423 carrying "Retry-After: 1" waits two seconds, not one."""
    signal = throttle_classify.signal_for("hubspot", 423)
    answer = throttle_backoff.schedule(
        1, signal=signal, headers={"Retry-After": "1"}, now=NOW, seed="k"
    )
    assert answer["seconds"] == 2
    assert answer["source"] == "retry_after"
    # The basis has to name both numbers, or a reader cannot tell which one won and
    # why: the vendor asked for 1, and the floor of 2 overrode it.
    assert "documented 2s floor" in answer["basis"]
    assert "rather than the 1s it asked for" in answer["basis"]


def test_a_477_is_taken_at_face_value_rather_than_floored():
    """Inferred: the ladder exists to avoid hammering a limit that resets on a
    known schedule. A migration has none, and the vendor has said when it ends."""
    signal = throttle_classify.signal_for("hubspot", 477)
    answer = throttle_backoff.schedule(
        1, signal=signal, headers={"Retry-After": "1"}, now=NOW, seed="k"
    )
    assert answer["seconds"] == 1


def test_the_lock_floor_wins_when_the_ladder_is_shorter_than_it():
    """At attempt 1 the ladder is 30s, so the floor is not reached; a caller that
    asks for attempt 0 semantics with a short ladder is the case that matters."""
    signal = throttle_classify.signal_for("hubspot", 423)
    answer = throttle_backoff.schedule(
        1, signal=signal, headers={}, now=NOW, seed="k", lock_floor_seconds=900
    )
    assert answer["seconds"] == 900
    assert answer["source"] == "lock_floor"


def test_a_schedule_always_answers_with_the_four_keys_a_caller_reads():
    for headers in ({}, {"Retry-After": "45"}, {"Retry-After": "999999"}):
        answer = throttle_backoff.schedule(2, headers=headers, now=NOW, seed="k")
        assert set(answer) == {
            "seconds",
            "source",
            "at",
            "attempt",
            "ladder_seconds",
            "floor_seconds",
            "basis",
        }
        assert answer["source"] in throttle_backoff.SOURCES
        assert answer["attempt"] == 2
        assert answer["ladder_seconds"] == 60


def test_the_attempt_bound_is_not_a_dead_end():
    """A person who has just fixed the cause retries by hand, and that retry is
    not capped: refusing an explicit action because a counter ran out would make
    the researched waiting state a dead end."""
    assert throttle_backoff.check_attempts(4) is True
    assert throttle_backoff.check_attempts(5) is False
    assert throttle_backoff.check_attempts(None) is True
    assert throttle_backoff.check_attempts(0, maximum=0) is False


def test_describe_publishes_every_rung_and_the_provenance():
    described = throttle_backoff.describe()
    assert described["rungs"]["1"] == 30
    assert described["rungs"]["7"] == 1800
    assert described["max_attempts"] == 5
    assert described["retry_after_cap_seconds"] == 86_400
    assert "idempotency key" in described["jitter_basis"]


# --------------------------------------------------------------------------- #
# The token bucket
# --------------------------------------------------------------------------- #


def hubspot_policy() -> dict:
    return throttle_policies.get("hubspot")


def test_a_hubspot_bucket_starts_full_and_refills_at_its_published_rate():
    """Quoted: "110 requests every 10 seconds"."""
    state = throttle_bucket.new_state(hubspot_policy(), NOW)
    assert state["tokens"] == 110
    assert throttle_bucket.refill_rate(hubspot_policy()) == pytest.approx(11.0)

    an_hour = throttle_bucket.refill(state, hubspot_policy(), NOW + timedelta(hours=1))
    assert an_hour["tokens"] == 110, "a bucket that refills past its capacity will burst at the top"


def test_refill_is_continuous_so_a_waiting_room_does_not_spend_the_whole_bucket_at_once():
    """Waiting for the window boundary would burst 110 calls at the top of every
    ten seconds, which is the opposite of what the limit asks for."""
    state = throttle_bucket.new_state(hubspot_policy(), NOW)
    half = throttle_bucket.refill(state, hubspot_policy(), NOW + timedelta(seconds=5))
    assert half["tokens"] == pytest.approx(165.0 - 55.0)


def test_a_clock_that_went_backwards_does_not_hand_out_more_than_capacity():
    """An NTP correction must not compute a negative refill, and a negative
    refill would produce a bucket with more tokens than it holds."""
    state = throttle_bucket.new_state(hubspot_policy(), NOW)
    back = throttle_bucket.refill(state, hubspot_policy(), NOW - timedelta(hours=2))
    assert back["tokens"] == 110


def test_the_bucket_refuses_a_batch_it_cannot_afford_and_says_when_to_come_back():
    state = throttle_bucket.new_state(hubspot_policy(), NOW) | {"tokens": 0.0}
    verdict = throttle_bucket.decide(state, hubspot_policy(), NOW, cost=1)
    assert verdict["allowed"] is False
    assert verdict["reason"] == "empty"
    assert verdict["retry_in_seconds"] == 1, "one token at 11 a second is under a tenth of a second"

    bulk = throttle_bucket.decide(state, hubspot_policy(), NOW, cost=100)
    assert bulk["allowed"] is False
    assert bulk["retry_in_seconds"] == 10, "100 tokens at 11 a second is 9.1s, rounded up"


def test_a_paused_connection_sends_nothing_and_schedules_nothing():
    state = throttle_bucket.new_state(hubspot_policy(), NOW)
    verdict = throttle_bucket.decide(state | {"paused": True}, hubspot_policy(), NOW, cost=1)
    assert verdict["allowed"] is False
    assert verdict["reason"] == "paused"
    assert verdict["retry_in_seconds"] is None
    assert "resume it" in verdict["detail"]


def test_a_vendor_with_no_published_burst_is_blind_rather_than_blocked():
    """Salesforce publishes no burst number this build could source. A bucket that
    reported zero would refuse every call forever, so it counts and says so."""
    salesforce = throttle_policies.get("salesforce")
    state = throttle_bucket.new_state(salesforce, NOW)
    assert state["known"] is False
    verdict = throttle_bucket.decide(state | {"tokens": 0.0}, salesforce, NOW, cost=1)
    assert verdict["allowed"] is True
    assert verdict["reason"] == "unknown_capacity"
    assert "429" in verdict["detail"]


def test_spending_a_token_happens_after_the_vendor_answers_not_before():
    """A token the room spent and the vendor refused still came off the limit, so
    spending only on success would make the room's count drift exactly when the
    count matters most."""
    state = throttle_bucket.new_state(hubspot_policy(), NOW)
    after = throttle_bucket.spend(state, hubspot_policy(), NOW, cost=1)
    assert after["tokens"] == 109
    assert after["sent_in_window"] == 1
    assert after["last_spent_at"]


def test_a_spent_bucket_never_goes_below_zero_whatever_the_cost():
    state = throttle_bucket.new_state(hubspot_policy(), NOW)
    after = throttle_bucket.spend(state | {"tokens": 1.0}, hubspot_policy(), NOW, cost=50)
    assert after["tokens"] == 0


# --------------------------------------------------------------------------- #
# The policies: sourced numbers, and the gaps the research recorded
# --------------------------------------------------------------------------- #


def test_the_hubspot_policy_carries_the_numbers_the_research_quoted():
    """Quoted: "110 requests every 10 seconds" and the per-app/per-account table
    for Free, Professional and Enterprise."""
    policy = throttle_policies.get("hubspot")
    assert (policy["burst"], policy["sustained"], policy["window_seconds"]) == (110, 110, 10)
    assert policy["daily"] == 250_000
    assert policy["sourced"] is True
    assert "110 requests" in policy["basis"]
    assert "1,000,000 / account" in policy["basis"]


def test_the_salesforce_policy_names_its_limit_and_declares_no_number():
    """The research quotes the limit names and says the per-edition allocations
    live in tables that were not fetched. So the policy names the limit, and no
    invented number is added to fill the gap."""
    policy = throttle_policies.get("salesforce")
    assert policy["limit_name"] == "DailyApiRequests"
    assert policy["burst"] is None
    assert policy["daily"] is None
    assert policy["sourced"] is True
    assert any("per-edition numeric" in gap for gap in policy["gaps"])
    assert policy["retry_hints"]["usage_header"].startswith("Sforce-Limit-Info")


def test_the_dataverse_policy_is_a_status_and_a_gap_and_no_number():
    """The research says the Service Protection page "was not located at a
    readable URL - every candidate path 404'd - so no Dataverse numeric limit is
    quoted". Nothing is invented to cover it."""
    policy = throttle_policies.get("dataverse")
    assert policy["burst"] is None
    assert policy["daily"] is None
    assert policy["retry_hints"]["throttle_status"] == 429
    assert any("404" in gap for gap in policy["gaps"])
    assert "429 Too Many Requests" in policy["basis"]


def test_only_hubspot_can_refuse_a_call_before_it_is_sent():
    described = throttle_policies.describe("hubspot")
    assert described["preemptive"] is True
    assert described["tokens_per_second"] == pytest.approx(11.0)
    assert described["seconds_per_token"] == pytest.approx(0.091, abs=1e-3)
    for vendor in ("salesforce", "dataverse"):
        assert throttle_policies.describe(vendor)["preemptive"] is False
        assert throttle_policies.describe(vendor)["tokens_per_second"] == 0.0


def test_a_rate_with_no_window_is_refused_rather_than_accepted():
    """A bucket with a rate but no window never refills, so it would empty once
    and refuse every call after that."""
    with pytest.raises(UnknownPolicy) as caught:
        throttle_policies.prepare({"vendor": "acme", "sustained": 100})
    assert "cannot refill" in str(caught.value)


def test_a_window_with_no_rate_is_refused_too():
    with pytest.raises(UnknownPolicy) as caught:
        throttle_policies.prepare({"vendor": "acme", "window_seconds": 10})
    assert "cannot refill" in str(caught.value)


@pytest.mark.parametrize(
    "patch, expected",
    [
        ({"vendor": "acme", "burst": 0}, "a limit is positive"),
        ({"vendor": "acme", "burst": -5}, "a limit is positive"),
        ({"vendor": "acme", "burst": "many"}, "must be a number"),
        ({"vendor": "", "burst": 5}, "needs a vendor name"),
        ({"vendor": "a b", "burst": 5}, "needs a vendor name"),
    ],
)
def test_a_policy_that_would_produce_a_bucket_which_lies_is_refused(patch, expected):
    with pytest.raises(UnknownPolicy) as caught:
        throttle_policies.prepare(patch)
    assert expected in str(caught.value)


def test_registering_a_policy_is_the_documented_extension_point(registered_policy):
    """Quoted: "adding a vendor means filling in a policy object, not writing a
    new backoff algorithm"."""
    registered = registered_policy(
        {"vendor": "acme", "burst": 30, "sustained": 30, "window_seconds": 60}
    )
    assert registered["vendor"] == "acme"
    assert throttle_policies.known("acme") is True
    assert throttle_policies.describe("acme")["preemptive"] is True
    assert throttle_policies.describe("acme")["seconds_per_token"] == pytest.approx(2.0)


def test_a_vendor_with_no_policy_still_runs_and_says_it_is_unsourced():
    """The researched extensibility note would be untrue if an unknown vendor were
    refused. It gets a policy with no numbers, flagged sourced: false."""
    policy = throttle_policies.get("nowhere")
    assert policy["sourced"] is False
    assert policy["burst"] is None
    assert "No published limit" in policy["basis"]
    assert throttle_policies.known("nowhere") is False


def test_the_patchable_fields_are_the_policy_numbers_and_not_the_provenance():
    """A connection's basis and gaps are what the research said, not what an
    operator chose, so they are not patchable."""
    assert "basis" not in throttle_policies.PATCHABLE
    assert "gaps" not in throttle_policies.PATCHABLE
    assert "burst" in throttle_policies.PATCHABLE
    assert "retry_after_cap_seconds" in throttle_policies.PATCHABLE


# --------------------------------------------------------------------------- #
# The quota headers
# --------------------------------------------------------------------------- #


def test_sforce_limit_info_reads_both_segments_with_the_researched_example():
    """Quoted: "Sforce-Limit-Info: api-usage=10018/100000; api-bursts=1/750"."""
    reading = throttle_quota.sforce_limit_info(
        {"Sforce-Limit-Info": "api-usage=10018/100000; api-bursts=1/750"}
    )
    assert reading["daily"] == {"used": 10018.0, "total": 100000.0, "remaining": 89982.0}
    assert reading["burst"] == {"used": 1.0, "total": 750.0, "remaining": 749.0}
    assert reading["known"] is True


def test_sforce_limit_info_names_the_segment_it_could_not_read():
    """A segment with no reading is reported rather than dropped, so a vendor that
    adds one is visible instead of invisible."""
    reading = throttle_quota.sforce_limit_info(
        {"Sforce-Limit-Info": "api-usage=1/10; api-something-new=2/20; junk"}
    )
    assert reading["daily"]["remaining"] == 9
    assert any("api-something-new" in note for note in reading["ignored"])
    assert any("not a name=value pair" in note for note in reading["ignored"])


def test_a_missing_sforce_header_is_unknown_rather_than_zero():
    reading = throttle_quota.sforce_limit_info({})
    assert reading["known"] is False
    assert reading["daily"]["remaining"] is None
    assert any("no Sforce-Limit-Info" in note for note in reading["ignored"])


def test_a_malformed_api_usage_segment_is_reported_not_guessed():
    reading = throttle_quota.sforce_limit_info({"Sforce-Limit-Info": "api-usage=abc/10"})
    assert reading["daily"]["remaining"] is None
    assert reading["known"] is False


def test_the_hubspot_headers_give_both_halves_and_the_window_in_seconds():
    """Quoted: "a value of 10000 would be a window of 10 seconds"."""
    reading = throttle_quota.hubspot_rate_limit(
        {
            "X-HubSpot-RateLimit-Max": "100",
            "X-HubSpot-RateLimit-Remaining": "87",
            "X-HubSpot-RateLimit-Interval-Milliseconds": "10000",
            "X-HubSpot-RateLimit-Daily": "250000",
            "X-HubSpot-RateLimit-Daily-Remaining": "249812",
        }
    )
    assert reading["window"] == {
        "used": 13.0,
        "total": 100.0,
        "remaining": 87.0,
        "window_seconds": 10.0,
    }
    assert reading["daily"]["remaining"] == 249812.0
    assert reading["known"] is True


def test_an_oauth_response_with_no_daily_header_is_unknown_not_zero():
    """Quoted: "Note that this header is not included in the response to API
    requests authorized using OAuth". Reporting a zero daily budget there would
    refuse every call forever."""
    reading = throttle_quota.hubspot_rate_limit(
        {"X-HubSpot-RateLimit-Max": "100", "X-HubSpot-RateLimit-Remaining": "87"}
    )
    assert reading["window"]["remaining"] == 87.0
    assert reading["daily"]["remaining"] is None


def test_a_vendor_with_no_header_surface_returns_the_gap_rather_than_nothing():
    reading = throttle_quota.read("dataverse", {})
    assert reading["known"] is False
    assert "429" in reading["gap"]


def test_read_dispatches_on_the_vendor_and_returns_one_shape():
    assert (
        throttle_quota.read("salesforce", {"Sforce-Limit-Info": "api-usage=1/2"})["known"] is True
    )
    assert throttle_quota.read("hubspot", {"X-HubSpot-RateLimit-Remaining": "3"})["known"] is True
    assert throttle_quota.read("nowhere", {})["source"] is None


def test_remaining_of_reads_one_half_and_says_none_when_there_is_nothing():
    reading = throttle_quota.read("salesforce", {"Sforce-Limit-Info": "api-usage=10/100"})
    assert throttle_quota.remaining_of(reading, "daily") == 90.0
    assert throttle_quota.remaining_of(reading, "window") is None
    assert throttle_quota.remaining_of(None) is None
    assert throttle_quota.remaining_of({"daily": {"remaining": "not a number"}}) is None


# --------------------------------------------------------------------------- #
# The idempotency keys
# --------------------------------------------------------------------------- #


def test_a_key_is_stable_across_calls_which_is_the_whole_promise():
    """The researched promise is "retry with the same idempotency key (so retries
    never duplicate CRM rows)". A key that changed per call would break it."""
    first = throttle_keys.for_row(
        vendor="hubspot",
        connection_id="c1",
        external_id="ext-1",
        object_name="contacts",
        room_id="r1",
    )
    again = throttle_keys.for_row(
        vendor="hubspot",
        connection_id="c1",
        external_id="ext-1",
        object_name="contacts",
        room_id="r1",
    )
    assert first == again
    assert len(first) == throttle_keys.DIGEST_LENGTH


def test_two_rooms_sending_the_same_external_id_do_not_collide():
    left = throttle_keys.for_row(
        vendor="hubspot", connection_id="c1", external_id="ext-1", room_id="r1"
    )
    right = throttle_keys.for_row(
        vendor="hubspot", connection_id="c1", external_id="ext-1", room_id="r2"
    )
    assert left != right


def test_two_rows_in_one_batch_never_share_a_key():
    entries = throttle_keys.for_rows(
        [{"external_id": "a"}, {"external_id": "b"}], vendor="hubspot", connection_id="c1"
    )
    assert len({entry["idempotency_key"] for entry in entries}) == 2


def test_a_row_with_no_external_id_is_refused_rather_than_keyed_from_nothing():
    """A key derived from nothing is the same key for every row in the batch, and
    one row's success would then silence the rest."""
    with pytest.raises(InvalidPayload) as caught:
        throttle_keys.for_row(vendor="hubspot", connection_id="c1", external_id="")
    assert "same key for every row" in str(caught.value)


def test_two_rows_sharing_an_external_id_are_refused_rather_than_silently_deduped():
    entries = [{"external_id": "a"}, {"external_id": "a"}]
    with pytest.raises(InvalidPayload) as caught:
        throttle_keys.for_rows(entries, vendor="hubspot", connection_id="c1")
    assert "silences the second" in str(caught.value)


def test_a_row_that_is_not_an_object_is_refused():
    with pytest.raises(InvalidPayload):
        throttle_keys.for_rows(["nope"], vendor="hubspot", connection_id="c1")


def test_same_reads_two_absences_as_no_match_rather_than_as_a_kept_key():
    """The question is "did the retry keep the key the first attempt used", and an
    attempt with no key kept nothing. Reporting True here would let a caller write
    "keys reused" on a batch that never had a key, which is the one thing the
    researched promise must never be able to say."""
    assert throttle_keys.same("k", "k") is True
    assert throttle_keys.same("  k  ", "k") is True, "a trimmed key is the same key"
    assert throttle_keys.same(None, "") is False
    assert throttle_keys.same("", "") is False
    assert throttle_keys.same("k", "j") is False


# --------------------------------------------------------------------------- #
# Instants, and the one that must not crash the queue
# --------------------------------------------------------------------------- #


def test_an_instant_is_written_with_seconds_so_two_lines_of_one_decision_can_tie():
    """Seconds precision, not laziness: the throttle log's ordering breaks a tie on
    insertion order, and a deferral writes several lines at the same instant."""
    assert throttle_timestamps.iso(NOW) == "2026-09-27T12:00:00+00:00"
    assert throttle_timestamps.iso(NOW.replace(microsecond=123456)) == "2026-09-27T12:00:00+00:00"


def test_a_naive_instant_is_read_as_utc_rather_than_as_local_time():
    """Treating it as local would make the same row defer for a different length of
    time depending on which machine read it."""
    assert throttle_timestamps.parse_instant(datetime(2026, 9, 27, 12, 0)) == NOW
    assert throttle_timestamps.parse_instant("2026-09-27T12:00:00") == NOW
    assert throttle_timestamps.parse_instant(NOW) == NOW


def test_an_instant_the_room_cannot_read_is_absent_rather_than_an_exception():
    """A corrupt ``next_attempt_at`` must not take the queue down. Absent means
    "due now", which is what a batch nobody holds a schedule for should be."""
    for junk in ("not a date", "", None, 0, [], {}):
        assert throttle_timestamps.parse_instant(junk) is None, junk


def test_a_corrupt_schedule_does_not_stop_the_drain(engine, room):
    """The end-to-end consequence of the line above, on the route that reads it.

    The batch is deferred first, because that is the state the drain works on: a
    batch nobody has sent yet is ``proceeding``, and the drain correctly leaves it
    alone. Driven through the engine rather than by poking the parser, because a
    test that calls the parser proves the parser works and not that the queue
    survives a record written by something else.
    """
    connection = make_connection(engine, room, "salesforce")
    batch = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1)}, source="test"
    )
    engine.observe(
        room["id"],
        batch["id"],
        {"status": 429, "headers": {"Retry-After": "600"}},
        source="test",
    )
    stored = engine.store.get(batch["id"])
    engine.store.update(
        batch["id"],
        {**stored["data"], "next_attempt_at": "the day before yesterday"},
        source="test",
    )
    # Unreadable means absent, and absent means due now, so the queue sends it
    # rather than skipping a batch it can never read a schedule for.
    assert batch["id"] in engine.drain(room["id"], source="test")["retried"]


# --------------------------------------------------------------------------- #
# The engine, step by step
# --------------------------------------------------------------------------- #


def test_a_connection_gets_a_bucket_and_a_policy_on_arrival(engine, room):
    connection = make_connection(engine, room)
    assert connection["vendor"] == "hubspot"
    assert connection["bucket"]["tokens"] == 110
    assert connection["effective"]["preemptive"] is True
    assert connection["policy_sourced"] is True
    assert connection["paused"] is False


def test_a_connection_to_a_vendor_with_no_registered_policy_is_recorded_as_unsourced(engine, room):
    connection = make_connection(engine, room, "acme-vendor")
    assert connection["registered"] is False
    assert connection["policy_sourced"] is False
    assert connection["effective"]["preemptive"] is False


def test_creating_a_connection_without_a_vendor_is_refused(engine, room):
    with pytest.raises(InvalidPayload) as caught:
        engine.create_connection({}, room_id=room["id"], source="test")
    assert "needs a vendor" in str(caught.value)


def test_creating_a_connection_in_a_room_that_does_not_exist_is_a_lookup_error(engine):
    with pytest.raises(UnknownRoom):
        engine.create_connection({"vendor": "hubspot"}, room_id="room_nope", source="test")


def test_a_patch_merges_rather_than_resets_the_other_fields(engine, room):
    """Patching the daily cap must not put the burst back to the vendor default."""
    connection = make_connection(engine, room)
    patched = engine.patch_connection(
        connection["id"], {"policy": {"daily": 900_000}}, source="test"
    )
    assert patched["policy"]["daily"] == 900_000
    assert patched["policy"]["burst"] == 110


def test_a_patch_of_an_unknown_field_is_refused_rather_than_ignored(engine, room):
    """An operator who typed daily_limit and got no error would believe they had
    raised a cap they had not."""
    connection = make_connection(engine, room)
    with pytest.raises(InvalidPayload) as caught:
        engine.patch_connection(connection["id"], {"policy": {"daily_limit": 5}}, source="test")
    assert "not a policy field" in str(caught.value)


def test_a_pause_stops_the_sending_and_the_read_still_answers(engine, room):
    connection = make_connection(engine, room)
    paused = engine.set_paused(connection["id"], True, reason="looking at it", source="test")
    assert paused["paused"] is True
    assert paused["pause_reason"] == "looking at it"
    assert engine.connection(connection["id"])["paused"] is True

    batch = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1)}, source="test"
    )
    assert batch["decision_reason"] == "paused"
    assert batch["state"] == "deferred"


def test_a_resume_does_not_hand_the_connection_a_full_bucket(engine, room, clock):
    """A pause is usually a response to a throttle, and a resumed connection with
    a full bucket goes straight back into the limit it was pulled out of."""
    connection = make_connection(engine, room)
    engine.submit(room["id"], {"connection_id": connection["id"], "rows": rows(5)}, source="test")
    engine.set_paused(connection["id"], True, source="test")
    clock["now"] = NOW + timedelta(minutes=1)
    resumed = engine.set_paused(connection["id"], False, source="test")
    assert resumed["bucket"]["tokens"] < 110
    assert resumed["paused"] is False


def test_a_batch_the_bucket_allows_proceeds_and_spends_its_tokens(engine, room):
    """Step 1 of the research: the bucket sizes the call before it goes out."""
    connection = make_connection(engine, room)
    batch = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(3), "calls": 1}, source="test"
    )
    assert batch["state"] == "proceeding"
    assert batch["decision"] == "proceed"
    assert batch["cost"] == 1
    assert batch["bucket"]["tokens"] == 109
    assert len(batch["keys"]) == 3


def test_an_empty_bucket_defers_the_batch_before_anything_is_sent(engine, room):
    """The pre-emptive half, which no shipped code had: the room refuses to send
    rather than sending into the limit and being refused back."""
    connection = make_connection(engine, room, burst=2, sustained=2, window_seconds=10)
    engine.submit(room["id"], {"connection_id": connection["id"], "rows": rows(2)}, source="test")
    blocked = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1)}, source="test"
    )
    assert blocked["state"] == "deferred"
    assert blocked["decision_reason"] == "empty"
    assert blocked["schedule"]["seconds"] >= 1
    assert blocked["next_attempt_at"] > blocked["submitted_at"]


def test_a_batch_without_a_connection_is_refused_because_there_is_no_budget_to_size_it(
    engine, room
):
    with pytest.raises(InvalidPayload) as caught:
        engine.submit(room["id"], {"rows": rows(1)}, source="test")
    assert "per-connector" in str(caught.value)


def test_a_row_with_no_external_id_stops_the_batch_being_keyed(engine, room):
    connection = make_connection(engine, room)
    with pytest.raises(InvalidPayload):
        engine.submit(
            room["id"],
            {"connection_id": connection["id"], "rows": [{"email": "a@b.test"}]},
            source="test",
        )


def test_a_batch_on_another_rooms_connection_is_a_lookup_error(engine, room, store):
    other = store.create("room", {"name": "Other"}, source="test")
    connection = make_connection(engine, room)
    with pytest.raises(UnknownConnection):
        engine.submit(
            other["id"], {"connection_id": connection["id"], "rows": rows(1)}, source="test"
        )


def test_accepted_rows_complete_the_batch(engine, room):
    connection = make_connection(engine, room)
    batch = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(2)}, source="test"
    )
    answer = engine.observe(
        room["id"],
        batch["id"],
        {"status": 201, "accepted": True, "headers": {"X-HubSpot-RateLimit-Remaining": "99"}},
        source="test",
    )
    assert answer["batch"]["state"] == "complete"
    assert answer["batch"]["terminal"] is True
    assert answer["quota"]["window"]["remaining"] == 99


def test_a_429_defers_the_batch_under_the_vendors_own_retry_after(engine, room):
    """Steps 2 and 3: the headers are written to the meter, the tokens are spent,
    and the wait is the vendor's Retry-After rather than the ladder.

    The bucket arithmetic is asserted as two spends rather than as one total,
    because that is what the code does and why: submitting spends the batch's
    cost, and the answer spends it again. A refused call still came off the
    limit, so a room that only spent on success would see its own count of the
    budget drift upward every time a batch was throttled.
    """
    connection = make_connection(engine, room)
    batch = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(2)}, source="test"
    )
    assert batch["cost"] == 2, "no calls_per_batch policy, so a row costs a token"
    assert batch["bucket"]["tokens"] == 108, "submitting spends the batch's cost"
    answer = engine.observe(
        room["id"],
        batch["id"],
        {
            "status": 429,
            "code": "RATE_LIMIT",
            "headers": {
                "X-HubSpot-RateLimit-Remaining": "0",
                "X-HubSpot-RateLimit-Daily-Remaining": "249001",
                "Retry-After": "45",
            },
        },
        source="test",
    )
    stored = answer["batch"]
    assert stored["state"] == "deferred"
    assert stored["signal"]["kind"] == "rate_limit"
    assert stored["schedule"]["seconds"] == 45
    assert stored["schedule"]["source"] == "retry_after"
    assert stored["next_attempt_at"] == "2026-09-27T12:00:45+00:00"
    assert stored["bucket"]["tokens"] == 106, "the refused call is charged again"
    assert answer["quota"]["daily"]["remaining"] == 249001.0


def test_a_423_is_held_at_the_vendors_two_second_floor_and_not_the_ladder(engine, room):
    """Step 4: "you should include a delay of at least 2 seconds between your API
    requests". The floor is the vendor's, so it beats the Retry-After of 1 that
    arrives beside it."""
    connection = make_connection(engine, room)
    batch = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1)}, source="test"
    )
    answer = engine.observe(
        room["id"], batch["id"], {"status": 423, "headers": {"Retry-After": "1"}}, source="test"
    )
    stored = answer["batch"]
    assert stored["signal"]["kind"] == "lock"
    assert stored["schedule"]["seconds"] == 2
    assert stored["schedule"]["basis"].count("floor") >= 1


def test_a_salesforce_request_limit_is_deferred_and_the_usage_header_is_read(engine, room):
    """A 403 that is really a throttle, and the header the connection's budget
    comes from."""
    connection = make_connection(engine, room, "salesforce")
    batch = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1)}, source="test"
    )
    answer = engine.observe(
        room["id"],
        batch["id"],
        {
            "status": 403,
            "code": "REQUEST_LIMIT_EXCEEDED",
            "headers": {"Sforce-Limit-Info": "api-usage=10018/100000; api-bursts=1/750"},
        },
        source="test",
    )
    assert answer["batch"]["signal"]["id"] == "salesforce-request-limit-exceeded"
    assert answer["quota"]["daily"]["remaining"] == 89982.0
    assert answer["quota"]["burst"]["remaining"] == 749.0


def test_an_answer_the_room_does_not_recognise_waits_for_a_person(engine, room):
    """Nothing in the source set says this clears on its own, so the room does not
    guess and does not cycle."""
    connection = make_connection(engine, room)
    batch = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1)}, source="test"
    )
    answer = engine.observe(room["id"], batch["id"], {"status": 418}, source="test")
    assert answer["batch"]["state"] == "needs_action"


def test_a_batch_past_the_attempt_bound_stops_being_retried_by_the_queue(engine, room):
    """The researched automation drains what clears on its own, and a class that
    survives five attempts is not a throttle."""
    connection = make_connection(engine, room, "salesforce")
    batch = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1)}, source="test"
    )
    stored = engine.store.get(batch["id"])
    engine.store.update(
        batch["id"], {**stored["data"], "attempt": throttle_backoff.MAX_ATTEMPTS}, source="test"
    )
    answer = engine.observe(
        room["id"], batch["id"], {"status": 429, "headers": {"Retry-After": "60"}}, source="test"
    )
    assert answer["batch"]["state"] == "needs_action"
    assert answer["batch"]["schedule"]["seconds"] == 60, "the wait is still computed and shown"


def test_a_retry_reuses_the_keys_the_batch_was_first_given(engine, room, clock):
    """The researched promise, and the thing that makes a blind retry safe."""
    connection = make_connection(engine, room)
    batch = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(2)}, source="test"
    )
    engine.observe(
        room["id"], batch["id"], {"status": 429, "headers": {"Retry-After": "30"}}, source="test"
    )
    clock["now"] = NOW + timedelta(seconds=60)

    outcome = engine.retry(room["id"], batch["id"], source="test")
    assert outcome["sent"] is True
    assert [entry["idempotency_key"] for entry in outcome["batch"]["keys"]] == [
        entry["idempotency_key"] for entry in batch["keys"]
    ]
    assert outcome["batch"]["keys_reused"] is True
    assert outcome["batch"]["state"] == "retrying"


def test_a_retry_before_the_wait_is_refused_so_the_queue_does_not_hammer_the_vendor(engine, room):
    connection = make_connection(engine, room)
    batch = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1)}, source="test"
    )
    engine.observe(
        room["id"], batch["id"], {"status": 429, "headers": {"Retry-After": "600"}}, source="test"
    )
    outcome = engine.retry(room["id"], batch["id"], source="test")
    assert outcome["sent"] is False
    assert outcome["batch"]["state"] == "deferred"


def test_a_retry_that_would_change_the_keys_is_refused(engine, room, clock):
    """A fresh key is a duplicate write, so the room refuses rather than
    regenerating."""
    connection = make_connection(engine, room)
    batch = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1)}, source="test"
    )
    clock["now"] = NOW + timedelta(hours=1)
    with pytest.raises(InvalidPayload) as caught:
        engine.retry(
            room["id"],
            batch["id"],
            {"keys": [{"idempotency_key": "something-else"}]},
            source="test",
        )
    assert "duplicate write" in str(caught.value)


def test_a_complete_batch_is_not_retried_at_all(engine, room):
    """Every row is already there, so a retry under the same keys would re-write
    rows the vendor has accepted."""
    connection = make_connection(engine, room)
    batch = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1)}, source="test"
    )
    engine.observe(room["id"], batch["id"], {"status": 201, "accepted": True}, source="test")
    with pytest.raises(InvalidPayload) as caught:
        engine.retry(room["id"], batch["id"], source="test")
    assert "is complete" in str(caught.value)


def test_a_manual_retry_is_not_capped_even_when_the_bound_is_reached(engine, room, clock):
    """The bound governs the automatic drain. Refusing a person's explicit action
    because a counter ran out would make the waiting state a dead end."""
    connection = make_connection(engine, room, "salesforce")
    batch = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1)}, source="test"
    )
    stored = engine.store.get(batch["id"])
    engine.store.update(
        batch["id"],
        {**stored["data"], "attempt": throttle_backoff.MAX_ATTEMPTS, "state": "needs_action"},
        source="test",
    )
    outcome = engine.retry(room["id"], batch["id"], source="test")
    assert outcome["sent"] is True


def test_the_drain_retries_only_what_is_due(engine, room, clock):
    """The queue worker's route. A batch whose wait has not elapsed is left alone,
    because sending one early is the immediate retry into a rate limit."""
    connection = make_connection(engine, room)
    due = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1, "due")}, source="test"
    )
    waiting = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1, "later")}, source="test"
    )
    engine.observe(
        room["id"], due["id"], {"status": 429, "headers": {"Retry-After": "30"}}, source="test"
    )
    engine.observe(
        room["id"],
        waiting["id"],
        {"status": 429, "headers": {"Retry-After": "6000"}},
        source="test",
    )

    clock["now"] = NOW + timedelta(seconds=60)
    drained = engine.drain(room["id"], source="test")
    assert drained["retried"] == [due["id"]]
    assert drained["scheduled"] == [waiting["id"]]
    assert drained["keys_reused"] is True


def test_the_drain_respects_its_limit_and_its_batch_selector(engine, room, clock):
    connection = make_connection(engine, room)
    first = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1, "a")}, source="test"
    )
    second = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1, "b")}, source="test"
    )
    for batch in (first, second):
        engine.observe(
            room["id"],
            batch["id"],
            {"status": 429, "headers": {"Retry-After": "30"}},
            source="test",
        )
    clock["now"] = NOW + timedelta(seconds=60)
    assert engine.drain(room["id"], {"limit": 1}, source="test")["retried_count"] == 1
    picked = engine.drain(room["id"], {"batch_id": second["id"]}, source="test")
    assert picked["retried"] == [second["id"]]


def test_draining_a_room_that_does_not_exist_is_a_lookup_error(engine):
    with pytest.raises(UnknownRoom):
        engine.drain("room_nope", source="test")


def test_the_quota_meter_reports_what_the_vendor_said_and_not_a_guess(engine, room):
    """Step 2 read back. A half the vendor did not send is known: false rather
    than zero, so the page cannot refuse every call by drawing a bar at empty."""
    connection = make_connection(engine, room)
    batch = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1)}, source="test"
    )
    engine.observe(
        room["id"],
        batch["id"],
        {"status": 201, "accepted": True, "headers": {"X-HubSpot-RateLimit-Remaining": "42"}},
        source="test",
    )
    meter = engine.quota(room["id"])
    assert meter["count"] == 1
    assert meter["meters"][0]["window_remaining"] == 42.0
    assert meter["meters"][0]["daily_remaining"] is None
    assert "holds no credential" in meter["note"]


def test_the_quota_meter_keeps_only_the_latest_reading_per_connection(engine, room):
    connection = make_connection(engine, room)
    for remaining in ("10", "9"):
        batch = engine.submit(
            room["id"], {"connection_id": connection["id"], "rows": rows(1)}, source="test"
        )
        engine.observe(
            room["id"],
            batch["id"],
            {
                "status": 201,
                "accepted": True,
                "headers": {"X-HubSpot-RateLimit-Remaining": remaining},
            },
            source="test",
        )
    assert engine.quota(room["id"])["count"] == 1


def test_the_throttle_log_tells_the_story_in_the_order_it_happened(engine, room):
    connection = make_connection(engine, room)
    batch = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1)}, source="test"
    )
    engine.observe(
        room["id"], batch["id"], {"status": 429, "headers": {"Retry-After": "30"}}, source="test"
    )
    entries = engine.events(room_id=room["id"])
    assert [entry["event"] for entry in entries] == [
        "batch_submitted_proceed",
        "batch_deferred_rate_limit",
    ]
    stamps = [entry["at"] for entry in entries]
    assert stamps == sorted(stamps), "a log read backwards is not a story"
    assert "Retry-After: 30" in entries[-1]["detail"]


def test_batches_can_be_filtered_by_state_connection_and_any_json_path(engine, room):
    connection = make_connection(engine, room)
    other = engine.create_connection({"vendor": "salesforce"}, room_id=room["id"], source="test")
    first = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1)}, source="test"
    )
    engine.submit(room["id"], {"connection_id": other["id"], "rows": rows(1)}, source="test")
    engine.observe(room["id"], first["id"], {"status": 418}, source="test")

    assert len(engine.batches(room_id=room["id"], state="needs_action")) == 1
    assert len(engine.batches(room_id=room["id"], connection_id=other["id"])) == 1
    assert len(engine.batches(room_id=room["id"], where={"vendor": "hubspot"})) == 1
    assert len(engine.batches(room_id=room["id"], where={"counters.rows": 1})) == 2
    assert len(engine.batches(room_id=room["id"], where={"vendor": "nowhere"})) == 0


def test_reading_a_batch_that_is_not_on_this_room_is_a_lookup_error(engine, room, store):
    connection = make_connection(engine, room)
    batch = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1)}, source="test"
    )
    other = store.create("room", {"name": "Other"}, source="test")
    with pytest.raises(UnknownBatch):
        engine.batch(other["id"], batch["id"])
    with pytest.raises(UnknownBatch):
        engine.batch(room["id"], "throttle_batch_nope")


def test_the_summary_counts_states_and_kinds_for_the_page_header(engine, room):
    connection = make_connection(engine, room)
    good = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1, "a")}, source="test"
    )
    engine.observe(room["id"], good["id"], {"status": 201, "accepted": True}, source="test")
    bad = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1, "b")}, source="test"
    )
    engine.observe(
        room["id"], bad["id"], {"status": 429, "headers": {"Retry-After": "30"}}, source="test"
    )

    summary = engine.summary(room_id=room["id"])
    assert summary["batches"] == 2
    assert summary["counts"]["complete"] == 1
    assert summary["deferred"] == 1
    assert summary["longest_wait_seconds"] == 30
    assert summary["kinds"] == ["rate_limit"]
    assert summary["connections"] == 1
    assert summary["preemptive"] == 1


def test_a_status_that_is_not_a_number_in_an_answer_is_refused(engine, room):
    """A numeric *string* is a number and is accepted, because a connector that
    JSON-serialises its status hands it over as text all the time. Text that is
    not a number is refused rather than read as an absence."""
    connection = make_connection(engine, room)
    batch = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1)}, source="test"
    )
    accepted = engine.observe(
        room["id"], batch["id"], {"status": "201", "accepted": True}, source="test"
    )
    assert accepted["batch"]["last_status"] == 201

    other = engine.submit(
        room["id"], {"connection_id": connection["id"], "rows": rows(1, "b")}, source="test"
    )
    with pytest.raises(InvalidPayload):
        engine.observe(room["id"], other["id"], {"status": "429 Too Many Requests"}, source="test")


def test_a_cost_the_room_cannot_read_is_refused(engine, room):
    connection = make_connection(engine, room)
    with pytest.raises(InvalidPayload):
        engine.submit(
            room["id"],
            {"connection_id": connection["id"], "rows": rows(1), "calls": "many"},
            source="test",
        )


def test_rows_must_be_a_list(engine, room):
    connection = make_connection(engine, room)
    with pytest.raises(InvalidPayload) as caught:
        engine.submit(
            room["id"], {"connection_id": connection["id"], "rows": "nope"}, source="test"
        )
    assert "must be a list" in str(caught.value)


# --------------------------------------------------------------------------- #
# The vocabulary served to clients
# --------------------------------------------------------------------------- #


def test_the_vocabulary_carries_the_five_researched_steps_and_the_data_flow():
    described = vocabulary.describe()
    assert [step["step"] for step in described["flow"]] == [1, 2, 3, 4, 5]
    assert "idempotency key" in described["data_flow"]
    assert described["live_states"] == ["proceeding", "deferred", "retrying"]
    assert {row["value"] for row in described["batch_states"]} == set(vocabulary.BATCH_STATES)


def test_the_vocabulary_states_exactly_where_the_throttle_lives():
    """The resolution is that there is one owner. The page says which module owns
    each step so a reader does not have to go looking."""
    owners = {step["served_by"].split(" ")[0] for step in vocabulary.describe()["flow"]}
    assert all(owner.startswith("dsr.throttle") for owner in owners)


def test_the_vocabulary_publishes_the_collections_and_the_idempotency_basis():
    described = vocabulary.describe()
    assert described["collections"]["batch"] == "throttle_batch"
    assert described["collections"]["connection"] == "throttle_connection"
    assert "the row's own external id" in described["idempotency"]["derived_from"]
    assert described["bucket_reasons"] == ["proceed", "paused", "empty", "unknown_capacity"]


def test_every_batch_state_has_a_note_and_only_the_live_ones_are_not_terminal():
    for row in vocabulary.describe()["batch_states"]:
        assert row["what"].strip(), row["value"]
        assert row["terminal"] is (row["value"] not in vocabulary.LIVE_STATES)


# --------------------------------------------------------------------------- #
# The resolution's own requirement: one owner, and the siblings delegate
# --------------------------------------------------------------------------- #


def test_partial_failures_reads_its_throttle_rows_from_this_package():
    """The 429 and the 403 REQUEST_LIMIT_EXCEEDED rows of WF-040's table are no
    longer written here; they are read from the throttle table. The ids, the
    ordering and the retryable values are unchanged, which is why WF-040's own
    tests pass untouched."""
    from dsr.partial_failures.normalise import CLASSIFICATION

    by_id = {entry.id: entry for entry in CLASSIFICATION}
    throttled = by_id["throttled"]
    request_limit = by_id["salesforce-request-limit-exceeded"]

    assert throttled.http_status == throttle_classify.THROTTLE_STATUS == 429
    assert throttled.retryable is True
    assert request_limit.code == throttle_classify.REQUEST_LIMIT_EXCEEDED
    assert request_limit.http_status == throttle_classify.REQUEST_LIMIT_HTTP_STATUS
    assert request_limit.connector == "salesforce"
    # And the rows are still in the order the classifier walks them: the Salesforce
    # code before the bare status, so a 403 carrying the code is read by the first.
    ids = [entry.id for entry in CLASSIFICATION]
    assert ids.index("salesforce-request-limit-exceeded") < ids.index("throttled")


def test_partial_failures_reads_its_wait_ladder_from_this_package():
    """The ladder moved rather than being re-chosen, so WF-040's own pins on every
    rung still hold."""
    from dsr.partial_failures import retry as wf040_retry

    assert wf040_retry.BASE_BACKOFF_SECONDS == throttle_backoff.BASE_SECONDS
    assert wf040_retry.MAX_BACKOFF_SECONDS == throttle_backoff.MAX_SECONDS
    assert wf040_retry.MAX_ATTEMPTS == throttle_backoff.MAX_ATTEMPTS
    assert wf040_retry.BACKOFF_LABEL == throttle_backoff.LABEL
    for attempt in range(1, 9):
        assert wf040_retry.backoff_seconds(attempt) == throttle_backoff.backoff_seconds(attempt)


def test_partial_failures_still_applies_no_jitter_to_its_published_schedule():
    """The jitter is a modifier, not the ladder. WF-040 stores a wait on a row
    people read and its tests pin every rung, so adding jitter there would change
    running behaviour rather than move a number."""
    from dsr.partial_failures import retry as wf040_retry

    assert wf040_retry.backoff_seconds(1) == 30
    assert wf040_retry.backoff_seconds(3) == 120
    # The jittered form is a separate call, and it never returns the bare rung for
    # every seed.
    waits = {
        throttle_backoff.schedule(1, seed=f"seed-{index}", now=NOW)["seconds"] for index in range(8)
    }
    assert waits != {30}


def test_integ_monitor_asks_this_table_whether_a_status_is_a_throttle():
    """Its HubSpot header parsing stays; the classification of a status as a
    throttle is asked of the one table."""
    from dsr.integ_monitor.health import STATUS_CLASSES, class_from_status

    assert dict(STATUS_CLASSES)[429] == "throttle"
    assert 429 == throttle_classify.THROTTLE_STATUS
    assert class_from_status(429) == "throttle"
    assert class_from_status(403) == "validation", "a permission is not a throttle"
    assert class_from_status(503) == "vendor_5xx"
    assert class_from_status(200) is None


def test_integ_monitor_still_owns_the_hubspot_header_parsing_it_already_had():
    """The resolution says the HubSpot-specific parsing may stay there. This is
    the check that it still does, and that the two readers do not collide."""
    from dsr.integ_monitor.quota import normalise as integ_normalise

    reading = integ_normalise(
        "hubspot",
        "rate_limit_headers",
        {
            "headers": {
                "X-HubSpot-RateLimit-Max": "100",
                "X-HubSpot-RateLimit-Remaining": "87",
                "X-HubSpot-RateLimit-Interval-Milliseconds": "10000",
            }
        },
    )
    assert reading["window"]["remaining"] == 87
    assert reading["window"]["window_seconds"] == 10.0


def test_the_domain_module_imports_nothing_but_the_store():
    """The contract's architectural guard, restated for this package: the rules are
    pure, and everything that writes goes through the store."""
    import ast
    from pathlib import Path

    package = Path(__import__("dsr.throttle", fromlist=["__file__"]).__file__).parent
    allowed = {"dsr.store"}
    for module in sorted(package.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("dsr"):
                name = node.module
                assert name in allowed or name.startswith("dsr.throttle"), (
                    f"{module.name} imports {name}"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("sqlite3"), f"{module.name} imports sqlite3"
                    assert alias.name not in ("dsr.api", "fastapi"), (
                        f"{module.name} imports {alias.name}"
                    )
