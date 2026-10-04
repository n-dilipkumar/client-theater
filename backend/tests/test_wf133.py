"""WF-133: the domain rules behind real-time buyer-intent alerting and routing.

What is under test
------------------

The package :mod:`dsr.intent_routing` holds the rules, and this file exercises them
without the framework. That is the point of keeping the package separate from the
feature module: a threshold, a suppression window and an account join can each be
read and checked on their own, and a test that goes through HTTP to check a number
cannot tell a broken rule from a broken handler.

The groups are:

* **The thresholds.** One is quoted by the research and four are derived, and the
  derived numbers are pinned here. A change to any of them is a recorded decision,
  so a test that fails on one is telling the truth about a change somebody made on
  purpose. Each case below says which is which.
* **Account resolution.** The join between a WF-031 company and a WF-042
  opportunity, including the cases where it must refuse rather than guess.
* **Routing.** Four rule kinds, the chain order, and the suppression window.
* **The payload and the chain.** The three researched alert facts, and the writes
  the engine makes when it is allowed to.
* **The refusals and the demo data.** Every error the package can raise, and the
  string the seeder prints on a Windows console.

Every test uses the suite's shared ``store`` fixture, which is an in-memory
``RecordStore`` over a database created for that test alone. No test here can see
another test's rows, which is what makes this file pass on its own and inside
``pytest-xdist`` without an ordering dependency.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from dsr.intent_routing import payload as payload_module, resolution, routing
from dsr.intent_routing.engine import IntentRouter
from dsr.intent_routing.errors import (
    DuplicateRule,
    DuplicateWatchlist,
    IntentRoutingError,
    InvalidAction,
    InvalidEngagement,
    InvalidRule,
    InvalidWatchlist,
    UnknownAlert,
    UnknownCompany,
    UnknownEngagementField,
    UnknownRule,
    UnknownSignal,
    UnknownTask,
    UnknownWatchlist,
    UnresolvedAccount,
)
from dsr.intent_routing.inferences import INFERENCES
from dsr.intent_routing.thresholds import evaluate, parse_engagement
from dsr.intent_routing.vocabulary import (
    DEMO_INTERACTIONS,
    DISTINCT_PAGES,
    DOWNLOADS,
    DWELL_SECONDS,
    NOTIFICATION_CHANNELS,
    REVISITS,
    SUPPRESSION_HOURS,
    THRESHOLDS,
    THRESHOLDS_TO_ALERT,
)
from dsr.store import RecordStore

ROOM = "room-1"
NOW = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
SOURCE = "test"

#: The two dependencies' collections, read by the account join. WF-031 owns the
#: first and WF-042 the rest; this file writes one row of each so the join has
#: something to join, which is the honest way to test a join between two features
#: that must not import each other.
COMPANIES = "identified_company"
CRM_RECORDS = "crm_read_record"
CRM_IDENTITIES = "crm_read_identity"


# --------------------------------------------------------------------------- #
# Provisioning
# --------------------------------------------------------------------------- #


def put_company(store, key="northwind", **overrides):
    """A WF-031 company record."""
    data = {
        "company_key": key,
        "identified_from": "capture",
        "name": "Northwind Energy",
        "website": "https://northwind.example",
        "size": "1000+",
        "segment": "enterprise",
        "tags": [],
        "countries": ["GB"],
        "contacts": [{"name": "Dana Okafor", "role": "VP Procurement"}],
        "page_views": 4,
        "paths": ["/overview", "/pricing"],
        "first_seen_at": NOW.isoformat(),
        "last_visit_at": NOW.isoformat(),
    }
    data.update(overrides)
    return store.create(COMPANIES, data, source=SOURCE)


def put_account(store, external_id="acct-nw", fields=None, **overrides):
    """A WF-042 CRM account row."""
    data = {
        "system": "salesforce",
        "object": "account",
        "external_id": external_id,
        "owner_id": "005-dana",
        "fields": fields or {"Name": "Northwind Energy", "Website": "https://northwind.example"},
    }
    data.update(overrides)
    return store.create(CRM_RECORDS, data, room_id=ROOM, source=SOURCE)


def put_deal(store, external_id="deal-nw", account_id="acct-nw", **overrides):
    """A WF-042 CRM deal row."""
    data = {
        "system": "salesforce",
        "object": "deal",
        "external_id": external_id,
        "owner_id": "005-dana",
        "fields": {"Name": "Rollout", "AccountId": account_id},
    }
    data.update(overrides)
    return store.create(CRM_RECORDS, data, room_id=ROOM, source=SOURCE)


def put_identity(store, account_id="acct-nw", deal_id="deal-nw", **overrides):
    """A WF-042 CRM identity, which is where the opportunity id is held."""
    data = {
        "system": "salesforce",
        "buyer_email": "buyer@northwind.example",
        "buyer_name": "Dana Okafor",
        "account_id": account_id,
        "contact_id": "con-nw",
        "deal_id": deal_id,
        "owner_id": "005-dana",
        "source": "room_mapping",
    }
    data.update(overrides)
    return store.create(CRM_IDENTITIES, data, room_id=ROOM, source=SOURCE)


@pytest.fixture
def wired(store):
    """A company, an account, a deal and an identity: the whole dependency chain.

    The minimum a real alert needs. Every refusal in this file is a refusal that
    happens when one piece of it is missing, so the tests that want a refusal
    provision everything except the piece they are testing.
    """
    put_company(store)
    put_account(store, crm_owner={"team": "enterprise-uk"})
    put_deal(store)
    put_identity(store)
    return store


@pytest.fixture
def router(store):
    """The engine over the test's own store."""
    return IntentRouter(store)


def observation(**overrides):
    """An observation that crosses all five thresholds.

    Four distinct pages, not three, because the derived page threshold is four and
    an observation that missed it would not be exercising the whole rule set.
    """
    payload = {
        "company_key": "northwind",
        "room_id": ROOM,
        "room_label": "the Northwind room",
        "pages": ["/overview", "/security", "/pricing", "/case-studies"],
        "dwell_seconds": 142,
        "total_dwell_seconds": 400,
        "revisits": 3,
        "downloads": 1,
        "demo_interactions": 1,
        "stakeholder": "Dana Okafor",
        "stakeholder_role": "VP Procurement",
        "last_seen_at": (NOW - timedelta(hours=2)).isoformat(),
    }
    payload.update(overrides)
    return payload


def put_watch(router_, room_id=ROOM, **overrides):
    payload = {
        "name": "Strategic",
        "tier": "strategic",
        "accounts": ["northwind"],
        "notify": ["lead@acme.example"],
    }
    payload.update(overrides)
    return router_.create_watchlist(payload, room_id=room_id, actor="test", source=SOURCE)


def raise_it(router_, now=NOW, **overrides):
    return router_.ingest(
        observation(**overrides), room_id=ROOM, actor="test", source=SOURCE, now=now
    )


def context_for(store):
    """The alert payload for the standard observation, assembled the way the engine does."""
    engagement = evaluate(parse_engagement(observation(), now=NOW), now=NOW)
    account = resolution.resolve(store, "northwind", room_id=ROOM)
    stakeholder = resolution.stakeholder_of(engagement, account)
    return payload_module.payload_for(
        engagement, account, stakeholder, [entry.as_dict() for entry in engagement.crossed]
    )


# --------------------------------------------------------------------------- #
# The thresholds
# --------------------------------------------------------------------------- #


class TestTheQuotedNumber:
    def test_the_ninety_second_dwell_rule_is_the_only_sourced_threshold(self):
        """The research quotes one number, and this asserts nobody overwrote it."""
        assert DWELL_SECONDS == 90
        dwell = next(entry for entry in THRESHOLDS if entry["kind"] == "dwell")
        assert dwell["sourced"] is True
        assert "90 seconds" in dwell["derivation"]

    def test_the_other_four_are_derived_and_say_so(self):
        derived = [entry for entry in THRESHOLDS if not entry["sourced"]]
        assert [entry["kind"] for entry in derived] == [
            "pages",
            "revisit",
            "download",
            "demo_interaction",
        ]
        for entry in derived:
            assert len(entry["derivation"]) > 80, entry["kind"]

    def test_the_derived_numbers_are_the_recorded_decision(self):
        assert (DISTINCT_PAGES, REVISITS, DOWNLOADS, DEMO_INTERACTIONS) == (4, 3, 1, 1)


@pytest.mark.parametrize(
    "overrides,expected_kind,expected_observed",
    [
        ({"dwell_seconds": 90, "pages": ["/a"]}, "dwell", 90),
        ({"dwell_seconds": 0, "pages": ["/a", "/b", "/c", "/d"]}, "pages", 4),
        ({"dwell_seconds": 0, "pages": ["/a"], "revisits": 3}, "revisit", 3),
        ({"dwell_seconds": 0, "pages": ["/a"], "downloads": 1}, "download", 1),
        ({"dwell_seconds": 0, "pages": ["/a"], "demo_interactions": 1}, "demo_interaction", 1),
    ],
)
def test_each_threshold_crosses_at_its_value(overrides, expected_kind, expected_observed):
    payload = {"company_key": "co", "room_id": ROOM}
    payload.update(overrides)
    assessed = evaluate(parse_engagement(payload, now=NOW), now=NOW)
    crossed = {entry.kind: entry.observed for entry in assessed.crossed}
    assert crossed.get(expected_kind) == expected_observed
    assert assessed.qualifies is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"dwell_seconds": 89, "pages": ["/a"]},
        {"dwell_seconds": 0, "pages": ["/a", "/b", "/c"]},
        {"dwell_seconds": 0, "pages": ["/a"], "revisits": 2},
        {"dwell_seconds": 0, "pages": ["/a"], "downloads": 0},
        {"dwell_seconds": 0, "pages": ["/a"], "demo_interactions": 0},
    ],
)
def test_each_threshold_stays_closed_one_below_its_value(overrides):
    """One below every threshold, which is how a sourced rule is actually judged.

    A rule that fired at 89 seconds would have made the researched worked example
    meaningless, so the boundary is asserted rather than assumed.
    """
    payload = {"company_key": "co", "room_id": ROOM}
    payload.update(overrides)
    assessed = evaluate(parse_engagement(payload, now=NOW), now=NOW)
    assert assessed.crossed == ()
    assert assessed.qualifies is False


def test_one_crossing_is_enough():
    assert THRESHOLDS_TO_ALERT == 1
    assessed = evaluate(
        parse_engagement({"company_key": "co", "room_id": ROOM, "dwell_seconds": 90}, now=NOW),
        now=NOW,
    )
    assert len(assessed.crossed) == 1
    assert assessed.qualifies is True


def test_an_observation_older_than_the_window_crosses_nothing():
    """A dwell reading from a month ago is not evidence about today."""
    stale = (NOW - timedelta(days=40)).isoformat()
    assessed = evaluate(
        parse_engagement(
            {"company_key": "co", "room_id": ROOM, "dwell_seconds": 5000, "last_seen_at": stale},
            now=NOW,
        ),
        now=NOW,
    )
    assert assessed.crossed == ()
    assert assessed.qualifies is False


def test_a_crossing_reports_the_arithmetic_not_just_a_yes():
    assessed = evaluate(parse_engagement(observation(), now=NOW), now=NOW)
    dwell = next(entry for entry in assessed.crossed if entry.kind == "dwell")
    assert dwell.as_dict() == {
        "kind": "dwell",
        "label": "Time on one page",
        "unit": "seconds",
        "observed": 142,
        "threshold": 90,
        "margin": 52,
    }


def test_duplicate_pages_are_counted_once_and_the_reading_order_is_kept():
    """A rep reading /overview then /pricing learns something the same set does not."""
    assessed = parse_engagement(observation(pages=["/a", "/b", "/a"]), now=NOW)
    assert assessed.pages == ("/a", "/b")


# --------------------------------------------------------------------------- #
# Reading an observation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("missing", ["company_key", "room_id"])
def test_an_observation_without_who_or_where_is_refused(missing):
    with pytest.raises(InvalidEngagement):
        parse_engagement({"company_key": "co", "room_id": ROOM, missing: ""}, now=NOW)


def test_an_observation_carrying_an_unthresholded_field_is_refused_by_name():
    """The measured value of a crossing is the whole output, so the field list is closed."""
    with pytest.raises(UnknownEngagementField) as caught:
        parse_engagement(observation(email="buyer@northwind.example"), now=NOW)
    assert "email" in str(caught.value)
    assert caught.value.status == 422


def test_a_fractional_dwell_reading_is_refused_rather_than_rounded():
    """Rounding 89.7 up to 90 would fire the one threshold the research sourced."""
    with pytest.raises(InvalidEngagement):
        parse_engagement(observation(dwell_seconds=89.7), now=NOW)


@pytest.mark.parametrize("value", [-1, -90])
def test_a_negative_measurement_is_refused(value):
    with pytest.raises(InvalidEngagement):
        parse_engagement(observation(revisits=value), now=NOW)


def test_a_page_that_is_not_a_path_is_refused():
    with pytest.raises(InvalidEngagement):
        parse_engagement(observation(pages=["pricing"]), now=NOW)


def test_a_longest_page_longer_than_the_window_total_is_refused():
    with pytest.raises(InvalidEngagement):
        parse_engagement(observation(dwell_seconds=400, total_dwell_seconds=100), now=NOW)


def test_a_moment_with_no_offset_is_refused():
    with pytest.raises(InvalidEngagement):
        parse_engagement(observation(last_seen_at="2026-10-04T07:00:00"), now=NOW)


def test_the_window_hours_can_be_narrowed_but_not_to_nothing():
    assert parse_engagement(observation(window_hours=1), now=NOW).window_hours == 1
    with pytest.raises(InvalidEngagement):
        parse_engagement(observation(window_hours=0), now=NOW)


@pytest.mark.parametrize("bad", [["northwind"], "northwind", 7])
def test_a_non_object_observation_is_refused(bad):
    with pytest.raises(InvalidEngagement):
        parse_engagement(bad, now=NOW)


# --------------------------------------------------------------------------- #
# Account resolution
# --------------------------------------------------------------------------- #


class TestResolution:
    def test_an_unknown_company_is_404_not_a_guess(self, store):
        """No vendor is called for a reverse-IP lookup, so an unseen address is refused."""
        with pytest.raises(UnknownCompany) as caught:
            resolution.require_company(store, "never-seen")
        assert caught.value.status == 404
        assert "visitor-identification" in str(caught.value)

    def test_a_company_is_matched_to_its_opportunity_by_website_host(self, wired):
        account = resolution.resolve(wired, "northwind", room_id=ROOM)
        assert account.matched == resolution.MATCH_WEBSITE
        assert account.opportunity_id == "deal-nw"
        assert account.owner_id == "005-dana"
        assert account.resolved is True

    def test_a_company_spelled_differently_falls_back_to_its_name(self, store):
        put_company(store, name="Northwind Energy Holdings plc")
        put_account(store, fields={"Name": "Northwind Energy Limited"})
        put_deal(store)
        account = resolution.resolve(store, "northwind", room_id=ROOM)
        assert account.matched == resolution.MATCH_NAME
        assert account.opportunity_id == "deal-nw"

    def test_the_website_is_tried_before_the_name(self, store):
        """A domain is one canonical identifier; a name is a string a human typed twice.

        Both accounts are a plausible match: one by website, one by name. The
        website has to win, and the test is arranged so it can only win by being
        tried first.
        """
        put_company(store, name="Zebra Works", website="https://first.example")
        put_account(store, "acct-web", {"Name": "Unrelated", "Website": "https://first.example"})
        put_account(store, "acct-name", {"Name": "Zebra Works", "Website": "https://third.example"})
        put_deal(store, "deal-web", account_id="acct-web")
        put_identity(store, account_id="acct-web", deal_id="deal-web")
        account = resolution.resolve(store, "northwind", room_id=ROOM)
        assert account.account_id == "acct-web"
        assert account.matched == resolution.MATCH_WEBSITE
        assert account.opportunity_id == "deal-web"

    def test_a_company_nothing_matches_is_left_unresolved_rather_than_guessed(self, store):
        put_company(store, name="Nobody Imported", website="https://nobody.example")
        put_account(store, "acct-other", {"Name": "Somebody Else"})
        account = resolution.resolve(store, "northwind", room_id=ROOM)
        assert account.matched == resolution.MATCH_UNKNOWN
        assert account.resolved is False
        assert account.opportunity_id == ""

    def test_a_legal_suffix_on_only_one_side_is_not_a_miss(self):
        assert resolution.normalise_name("Tailwind and Friends Ltd") == resolution.normalise_name(
            "Tailwind and Friends"
        )

    def test_the_territory_is_read_from_both_spellings_and_tolerated_being_absent(self):
        assert resolution.crm_owner_team({}) == ""
        assert resolution.crm_owner_team({"team": "mid-market"}) == "mid-market"
        assert (
            resolution.crm_owner_team({"crm_owner": {"team": "enterprise-uk"}}) == "enterprise-uk"
        )

    def test_an_owner_address_is_used_when_the_vendor_carried_one_and_never_invented(self, wired):
        """WF-042 keys an owner by an opaque id and carries no address for them."""
        assert resolution.resolve(wired, "northwind", room_id=ROOM).owner_email == ""
        wired.create(
            CRM_RECORDS,
            {
                "system": "salesforce",
                "object": "contact",
                "external_id": "con-x",
                "owner_id": "005-dana",
                "owner_email": "dana@acme.example",
                "fields": {},
            },
            room_id=ROOM,
            source=SOURCE,
        )
        assert resolution.resolve(wired, "northwind", room_id=ROOM).owner_email == (
            "dana@acme.example"
        )

    def test_the_buyers_address_is_never_taken_for_the_owners(self, wired):
        """A contact row carries the buyer's address, which is a different person."""
        wired.create(
            CRM_RECORDS,
            {
                "system": "salesforce",
                "object": "contact",
                "external_id": "con-y",
                "owner_id": "005-dana",
                "email": "buyer@northwind.example",
                "fields": {},
            },
            room_id=ROOM,
            source=SOURCE,
        )
        assert resolution.resolve(wired, "northwind", room_id=ROOM).owner_email == ""

    def test_an_accounted_party_is_named_even_with_nothing_on_file(self, store):
        put_company(store)
        assert resolution.resolve(store, "northwind", room_id=ROOM).accountable == "northwind"

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://Northwind.example/pricing", "northwind.example"),
            ("www.northwind.example", "northwind.example"),
            ("northwind.example:8443/x", "northwind.example"),
            ("", ""),
            ("not a url", ""),
            ("localhost", ""),
        ],
    )
    def test_a_host_is_read_from_a_web_address_and_an_unparseable_one_is_not_a_match(
        self, url, expected
    ):
        assert resolution.host_of(url) == expected


# --------------------------------------------------------------------------- #
# Routing rules
# --------------------------------------------------------------------------- #


class TestRoutingRules:
    def test_a_rule_without_a_kind_is_refused(self):
        with pytest.raises(InvalidRule):
            routing.normalise_rule({"name": "x"})

    def test_a_fifth_kind_is_refused(self):
        with pytest.raises(InvalidRule) as caught:
            routing.normalise_rule({"name": "x", "kind": "pager"})
        assert caught.value.status == 422

    def test_a_rule_matching_on_a_key_nobody_implements_is_refused(self):
        """A rule that silently never fires is worse than a rule nobody configured."""
        with pytest.raises(InvalidRule) as caught:
            routing.normalise_rule({"name": "x", "kind": "team", "match": {"moon": "full"}})
        assert "moon" in str(caught.value)

    def test_a_slack_handle_is_refused_because_nothing_can_send_it(self):
        with pytest.raises(InvalidRule):
            routing.normalise_rule({"name": "x", "kind": "team", "notify": ["@dana"]})

    def test_a_second_fallback_is_refused(self):
        with pytest.raises(InvalidRule):
            routing.normalise_rule({"name": "fallback", "kind": "fallback"}, fallback_count=1)

    def test_a_fallback_naming_recipients_is_refused(self):
        with pytest.raises(InvalidRule):
            routing.normalise_rule({"name": "f", "kind": "fallback", "notify": ["a@b.example"]})

    def test_the_crm_owner_rule_takes_no_recipients_of_its_own(self):
        with pytest.raises(InvalidRule):
            routing.normalise_rule({"name": "o", "kind": "crm_owner", "notify": ["a@b.example"]})

    def test_a_team_rule_with_nothing_to_say_is_refused(self):
        with pytest.raises(InvalidRule):
            routing.normalise_rule({"name": "t", "kind": "team"})

    def test_a_negative_position_is_refused(self):
        with pytest.raises(InvalidRule):
            routing.normalise_rule(
                {"name": "t", "kind": "team", "position": -1, "notify": ["a@b.example"]}
            )

    def test_the_opportunity_owner_is_reached_first_without_any_configuration(self, store):
        """The research names the account owner as the recipient, so it is not a rule."""
        put_company(store)
        put_account(store)
        put_deal(store)
        put_identity(store)
        account = resolution.resolve(store, "northwind", room_id=ROOM)
        result = routing.recipients_for([], account, watchlist=None, room_default="")
        assert [entry["who"] for entry in result["recipients"]] == ["005-dana"]
        assert result["consulted"] == ["Opportunity owner"]
        assert result["recipients"][0]["rule_id"] == ""

    def test_the_chain_is_walked_in_position_order(self, store):
        put_company(store)
        put_account(store, crm_owner={"team": "enterprise-uk"})
        account = resolution.resolve(store, "northwind", room_id=ROOM)
        rules = [
            {
                "id": "r1",
                "name": "team",
                "kind": "team",
                "match": {"team": "enterprise-uk"},
                "notify": ["team@acme.example"],
                "position": 1,
                "stop": False,
            },
            {
                "id": "r2",
                "name": "owner",
                "kind": "crm_owner",
                "match": {},
                "notify": [],
                "position": 0,
                "stop": False,
            },
        ]
        result = routing.recipients_for(
            rules, account, watchlist=None, room_default="fallback@acme.example"
        )
        assert [entry["who"] for entry in result["recipients"]] == ["005-dana", "team"]
        assert result["recipients"][0]["deliverable"] is False
        assert result["recipients"][1]["address"] == "team@acme.example"

    def test_a_configured_owner_rule_does_not_produce_a_second_recipient(self, store):
        put_company(store)
        put_account(store)
        put_deal(store)
        put_identity(store)
        account = resolution.resolve(store, "northwind", room_id=ROOM)
        result = routing.recipients_for(
            [
                {
                    "id": "r2",
                    "name": "owner",
                    "kind": "crm_owner",
                    "match": {},
                    "notify": [],
                    "position": 0,
                    "stop": True,
                }
            ],
            account,
            watchlist=None,
            room_default="",
        )
        assert [entry["who"] for entry in result["recipients"]] == ["005-dana"]

    def test_a_rule_that_says_stop_ends_the_chain(self, store):
        put_company(store)
        account = resolution.resolve(store, "northwind", room_id=ROOM)
        rules = [
            {
                "id": "r1",
                "name": "first",
                "kind": "team",
                "match": {},
                "notify": ["a@acme.example"],
                "position": 0,
                "stop": True,
            },
            {
                "id": "r2",
                "name": "second",
                "kind": "team",
                "match": {},
                "notify": ["b@acme.example"],
                "position": 1,
                "stop": True,
            },
        ]
        result = routing.recipients_for(
            rules, account, watchlist=None, room_default="fallback@acme.example"
        )
        assert [entry["address"] for entry in result["recipients"]] == ["a@acme.example"]

    def test_the_room_default_receives_an_alert_that_nobody_else_would(self, store):
        put_company(store)
        account = resolution.resolve(store, "northwind", room_id=ROOM)
        result = routing.recipients_for(
            [], account, watchlist=None, room_default="fallback@acme.example"
        )
        assert result["deliverable"] is True
        assert result["recipients"][0]["kind"] == resolution.MATCH_FALLBACK

    def test_a_country_criterion_matches_one_of_the_countries(self, store):
        put_company(store, countries=["GB", "IE"])
        account = resolution.resolve(store, "northwind", room_id=ROOM)
        rule = {
            "name": "uk",
            "kind": "team",
            "match": {"country": "ie"},
            "notify": [],
            "position": 0,
        }
        assert routing.rule_matches(rule, account, None) is True
        assert routing.rule_matches({**rule, "match": {"country": "fr"}}, account, None) is False


class TestContactSuppression:
    def _alert(self, store, dispatched_at, address="a@acme.example"):
        return store.create(
            "wf133_alert",
            {
                "company_key": "northwind",
                "dispatched_at": dispatched_at,
                "recipients": [{"who": "rep", "address": address}],
            },
            room_id=ROOM,
            source=SOURCE,
        )

    def test_an_account_told_within_the_window_is_suppressed(self, store):
        self._alert(store, (NOW - timedelta(hours=2)).isoformat())
        result = routing.suppressed_until(
            store, room_id=ROOM, company_key="northwind", addresses=["a@acme.example"], now=NOW
        )
        assert result["suppressed"] is True
        assert result["hours"] == SUPPRESSION_HOURS

    def test_an_account_told_before_the_window_is_not_suppressed(self, store):
        self._alert(store, (NOW - timedelta(hours=25)).isoformat())
        result = routing.suppressed_until(
            store, room_id=ROOM, company_key="northwind", addresses=["a@acme.example"], now=NOW
        )
        assert result["suppressed"] is False

    def test_suppression_is_per_recipient_not_per_account(self, store):
        """A second owner who has not been told is still told."""
        self._alert(store, NOW.isoformat())
        result = routing.suppressed_until(
            store, room_id=ROOM, company_key="northwind", addresses=["b@acme.example"], now=NOW
        )
        assert result["suppressed"] is False

    def test_an_account_with_nothing_on_file_cannot_be_suppressed(self, store):
        result = routing.suppressed_until(
            store, room_id=ROOM, company_key="northwind", addresses=[], now=NOW
        )
        assert result["suppressed"] is False


class TestDispatchStates:
    def test_email_is_queued_when_an_address_is_on_file(self):
        entry = routing.dispatch_for("email", [{"who": "dana", "address": "dana@acme.example"}])
        assert entry["state"] == "queued"
        assert entry["to"] == "dana@acme.example"
        assert entry["reason"] == ""

    def test_slack_is_held_and_says_why(self):
        entry = routing.dispatch_for("slack", [{"who": "dana", "address": "dana@acme.example"}])
        assert entry["state"] == "held_for_integration"
        assert "no Slack surface" in entry["reason"]

    def test_a_channel_with_no_address_is_skipped_and_names_the_accountable_party(self):
        entry = routing.dispatch_for("email", [{"who": "005-dana", "address": ""}])
        assert entry["state"] == "skipped"
        assert "005-dana" in entry["reason"]

    @pytest.mark.parametrize("channel", list(NOTIFICATION_CHANNELS))
    def test_there_is_no_sent_state_because_nothing_could_support_one(self, channel):
        """A state called sent would be a claim nothing in the codebase can support."""
        entry = routing.dispatch_for(channel, [{"who": "dana", "address": "dana@acme.example"}])
        assert entry["state"] != "sent"


# --------------------------------------------------------------------------- #
# The three researched alert facts
# --------------------------------------------------------------------------- #


class TestThePayload:
    def test_the_payload_carries_exactly_the_three_quoted_facts(self, wired):
        context = context_for(wired)
        assert set(payload_module.PAYLOAD_FIELDS) == {"pages", "dwell", "stakeholder"}
        assert set(context) >= {"pages", "dwell", "stakeholder"}
        assert context["pages"]["visited"] == [
            "/overview",
            "/security",
            "/pricing",
            "/case-studies",
        ]
        assert context["pages"]["count"] == 4
        assert context["dwell"]["longest_page_seconds"] == 142
        assert context["dwell"]["window_total_seconds"] == 400
        assert context["dwell"]["threshold_seconds"] == 90
        assert context["stakeholder"]["name"] == "Dana Okafor"
        assert context["stakeholder"]["role"] == "VP Procurement"

    def test_the_stakeholder_is_never_addressable_and_the_payload_says_so(self, wired):
        context = context_for(wired)
        assert context["stakeholder"]["addressable"] is False
        assert "cannot be written to" in context["stakeholder"]["note"]

    def test_the_observation_wins_over_the_company_contact_list(self, wired):
        engagement = evaluate(
            parse_engagement(observation(stakeholder="Ines Vogel"), now=NOW), now=NOW
        )
        account = resolution.resolve(wired, "northwind", room_id=ROOM)
        stakeholder = resolution.stakeholder_of(engagement, account)
        assert stakeholder["name"] == "Ines Vogel"
        assert stakeholder["source"] == "observation"

    def test_a_company_contact_is_used_when_the_observation_names_nobody(self, wired):
        engagement = evaluate(parse_engagement(observation(stakeholder=""), now=NOW), now=NOW)
        account = resolution.resolve(wired, "northwind", room_id=ROOM)
        stakeholder = resolution.stakeholder_of(engagement, account)
        assert stakeholder["name"] == "Dana Okafor"
        assert stakeholder["source"] == "company_contact"

    def test_an_unnamed_stakeholder_is_admitted_rather_than_substituted(self, wired):
        record = wired.find(COMPANIES, {"company_key": "northwind"}, limit=1)[0]
        wired.update(record["id"], {"contacts": []}, source=SOURCE)
        engagement = evaluate(parse_engagement(observation(stakeholder=""), now=NOW), now=NOW)
        account = resolution.resolve(wired, "northwind", room_id=ROOM)
        stakeholder = resolution.stakeholder_of(engagement, account)
        assert stakeholder["identified"] == "false"
        assert stakeholder["name"] == ""

    def test_the_subject_names_the_stakeholder_the_room_and_the_dwell(self, wired):
        subject = payload_module.subject_for(context_for(wired), "the Northwind room")
        assert subject == "Dana Okafor read 4 page(s) in the Northwind room, 142s on the longest"

    def test_the_body_reads_as_plain_text_and_carries_the_three_headings(self, wired):
        body = payload_module.body_for(context_for(wired), "the Northwind room")
        for heading in ("Which pages", "How long", "Which stakeholder", "Why this reached you"):
            assert heading in body, heading
        assert "\r" not in body

    def test_neither_the_subject_nor_the_body_holds_a_character_outside_cp1252(self, wired):
        """A rightwards arrow in one recovered feature broke the whole Windows seeder."""
        context = context_for(wired)
        for text in (
            payload_module.subject_for(context, "the Northwind room"),
            payload_module.body_for(context, "the Northwind room"),
        ):
            assert text.isascii(), text
            assert text.encode("cp1252").decode("cp1252") == text


# --------------------------------------------------------------------------- #
# Watchlists
# --------------------------------------------------------------------------- #


class TestWatchlists:
    def test_a_watchlist_is_saved_read_and_removed(self, router):
        created = put_watch(router)
        assert created["account_count"] == 1
        assert router.read_watchlist(created["id"])["name"] == "Strategic"
        router.delete_watchlist(created["id"], actor="test", source=SOURCE)
        assert router.watchlists(room_id=ROOM) == []

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"name": "  "},
            {"name": "x", "tier": "cosmic"},
            {"name": "x", "accounts": "northwind"},
            {"name": "x", "accounts": [""]},
            {"name": "x", "notify": ["@dana"]},
            {"name": "x", "notify": "not a list, but two"},
        ],
    )
    def test_a_malformed_watchlist_is_refused_by_name(self, router, payload):
        with pytest.raises(InvalidWatchlist):
            router.create_watchlist(payload, room_id=ROOM, actor="t", source=SOURCE)

    def test_two_watchlists_may_not_share_a_name_in_one_room(self, router):
        put_watch(router)
        with pytest.raises(DuplicateWatchlist):
            put_watch(router)

    def test_the_same_name_in_two_rooms_is_two_watchlists(self, router):
        put_watch(router, room_id="room-1")
        assert put_watch(router, room_id="room-2")["room_id"] == "room-2"

    def test_reading_or_removing_an_absent_watchlist_is_404(self, router):
        with pytest.raises(UnknownWatchlist):
            router.read_watchlist("wl_absent")
        with pytest.raises(UnknownWatchlist):
            router.delete_watchlist("wl_absent", actor="t", source=SOURCE)

    def test_membership_is_found_by_scanning_because_an_array_is_not_filterable(self, router):
        """`accounts` is a JSON array, so the dynamic index holds positional paths only."""
        put_watch(router, accounts=["alpha", "beta"])
        assert router.watchlist_for(room_id=ROOM, company_key="beta")["name"] == "Strategic"
        assert router.watchlist_for(room_id=ROOM, company_key="gamma") is None


# --------------------------------------------------------------------------- #
# Routing rule records
# --------------------------------------------------------------------------- #


class TestRuleRecords:
    def test_a_rule_is_saved_read_and_removed(self, router):
        created = router.create_rule(
            {"name": "Owner", "kind": "crm_owner", "position": 0},
            room_id=ROOM,
            actor="t",
            source=SOURCE,
        )
        assert created["kind"] == "crm_owner"
        assert [rule["name"] for rule in router.rules(room_id=ROOM)] == ["Owner"]
        router.delete_rule(created["id"], actor="t", source=SOURCE)
        assert router.rules(room_id=ROOM) == []

    def test_two_rules_may_not_share_a_name_in_one_room(self, router):
        payload = {"name": "Owner", "kind": "crm_owner"}
        router.create_rule(payload, room_id=ROOM, actor="t", source=SOURCE)
        with pytest.raises(DuplicateRule):
            router.create_rule(payload, room_id=ROOM, actor="t", source=SOURCE)

    def test_reading_or_removing_an_absent_rule_is_404(self, router):
        with pytest.raises(UnknownRule):
            router.read_rule("r_absent")
        with pytest.raises(UnknownRule):
            router.delete_rule("r_absent", actor="t", source=SOURCE)


# --------------------------------------------------------------------------- #
# The whole chain
# --------------------------------------------------------------------------- #


class TestIngestion:
    def test_a_qualifying_observation_writes_a_signal_an_alert_and_a_task(self, wired, router):
        put_watch(router)
        result = raise_it(router)
        assert result["raised"] is True
        assert result["wrote"] is True
        assert result["signal"]["company_key"] == "northwind"
        assert result["alert"]["channel_states"] == {
            "email": "queued",
            "slack": "held_for_integration",
        }
        assert result["task"]["opportunity_id"] == "deal-nw"
        assert len(router.signals(room_id=ROOM)) == 1
        assert len(router.alerts(room_id=ROOM)) == 1
        assert len(router.tasks(room_id=ROOM)) == 1

    def test_the_alert_carries_the_three_facts_and_the_arithmetic(self, wired, router):
        put_watch(router)
        alert = raise_it(router)["alert"]
        assert alert["payload"]["pages"]["count"] == 4
        assert alert["payload"]["dwell"]["longest_page_seconds"] == 142
        assert alert["payload"]["stakeholder"]["name"] == "Dana Okafor"
        kinds = {entry["kind"] for entry in alert["payload"]["triggered_by"]}
        assert kinds == {"dwell", "pages", "revisit", "download", "demo_interaction"}

    def test_the_alert_names_the_owner_and_the_watchlist(self, wired, router):
        put_watch(router)
        alert = raise_it(router)["alert"]
        assert "005-dana" in {entry["who"] for entry in alert["recipients"]}
        assert "Strategic" in {entry["who"] for entry in alert["recipients"]}
        assert alert["consulted"] == ["Opportunity owner"]

    def test_the_consulted_list_names_the_rules_that_ran(self, wired, router):
        put_watch(router)
        router.create_rule(
            {
                "name": "Enterprise",
                "kind": "team",
                "match": {"team": "enterprise-uk"},
                "notify": ["team@acme.example"],
                "position": 1,
            },
            room_id=ROOM,
            actor="t",
            source=SOURCE,
        )
        router.create_rule(
            {
                "name": "Nowhere",
                "kind": "team",
                "match": {"team": "apac"},
                "notify": ["x@y.example"],
            },
            room_id=ROOM,
            actor="t",
            source=SOURCE,
        )
        assert raise_it(router)["alert"]["consulted"] == ["Opportunity owner", "Enterprise"]

    def test_an_observation_below_every_threshold_writes_nothing(self, wired, router):
        put_watch(router)
        result = raise_it(
            router,
            dwell_seconds=5,
            pages=["/overview"],
            revisits=0,
            downloads=0,
            demo_interactions=0,
        )
        assert result["raised"] is False
        assert result["wrote"] is False
        assert "no threshold was met" in result["reason"]
        assert router.signals(room_id=ROOM) == []

    def test_a_company_on_no_watchlist_raises_nothing_and_says_why(self, wired, router):
        result = raise_it(router)
        assert result["raised"] is False
        assert "not a target account" in result["reason"]
        assert router.alerts(room_id=ROOM) == []

    def test_an_account_with_no_opportunity_is_refused_rather_than_written(self, store, router):
        """The researched flow ends with a task on the opportunity, so there is nowhere to go."""
        put_company(store)
        put_watch(router)
        with pytest.raises(UnresolvedAccount) as caught:
            raise_it(router)
        assert caught.value.status == 409
        assert router.alerts(room_id=ROOM) == []
        assert router.tasks(room_id=ROOM) == []

    def test_an_unknown_company_is_404(self, wired, router):
        put_watch(router)
        with pytest.raises(UnknownCompany):
            raise_it(router, company_key="never-seen")

    def test_the_second_signal_for_one_account_is_suppressed_not_dropped(self, wired, router):
        """Nothing is lost when the alert is suppressed: the signal is still written."""
        put_watch(router)
        raise_it(router)
        again = raise_it(router)
        assert again["alert"]["suppressed"] is True
        assert again["alert"]["channel_states"]["email"] == "suppressed"
        assert len(router.signals(room_id=ROOM)) == 2
        assert again["alert"]["dispatched_at"] == ""

    def test_a_signal_past_the_suppression_window_is_queued_again(self, wired, router):
        """The window is measured from the dispatch, not from the reading."""
        put_watch(router)
        raise_it(router)
        later = raise_it(router, now=NOW + timedelta(hours=SUPPRESSION_HOURS + 1))
        assert later["alert"]["suppressed"] is False
        assert later["alert"]["channel_states"]["email"] == "queued"

    def test_a_room_with_no_address_on_file_still_names_the_accountable_party(self, wired, router):
        put_watch(router, notify=[])
        alert = raise_it(router)["alert"]
        assert alert["channel_states"] == {"email": "skipped", "slack": "skipped"}
        assert alert["accountable"] == "005-dana"

    def test_the_evaluate_path_writes_nothing_and_gives_the_same_reason(self, wired, router):
        put_watch(router)
        report = router.evaluate(observation(), now=NOW)
        assert report["wrote"] is False
        assert report["raised"] is True
        assert router.signals(room_id=ROOM) == []

    def test_every_collection_is_room_scoped(self, wired, router):
        put_watch(router)
        raise_it(router)
        assert router.signals(room_id="room-2") == []
        assert router.alerts(room_id="room-2") == []
        assert router.tasks(room_id="room-2") == []
        assert router.watchlists(room_id="room-2") == []

    def test_the_reported_thresholds_are_the_recorded_ones(self, wired, router):
        report = router.evaluate(observation(), now=NOW)
        by_kind = {entry["kind"]: entry for entry in report["crossed"]}
        assert by_kind["dwell"]["threshold"] == DWELL_SECONDS
        assert by_kind["pages"]["threshold"] == DISTINCT_PAGES
        assert report["thresholds_to_alert"] == THRESHOLDS_TO_ALERT


# --------------------------------------------------------------------------- #
# Who is engaged and who is not
# --------------------------------------------------------------------------- #


class TestTheEngagementView:
    def test_the_list_is_the_watchlist_so_an_unengaged_account_still_appears(self, wired, router):
        """A view built from the alerts could only ever show who is engaged."""
        put_watch(router, accounts=["northwind", "meridian"])
        view = router.engagement(room_id=ROOM, now=NOW)
        assert {row["company_key"] for row in view["accounts"]} == {"northwind", "meridian"}
        assert view["engaged"] == 0
        assert view["not_engaged"] == 2

    def test_an_account_that_alerted_is_engaged_and_an_untouched_one_is_not(self, wired, router):
        put_company(
            router.store, "meridian", name="Meridian Foods", website="https://meridian.example"
        )
        put_watch(router, accounts=["northwind", "meridian"])
        raise_it(router)
        rows = {
            row["company_key"]: row for row in router.engagement(room_id=ROOM, now=NOW)["accounts"]
        }
        assert rows["northwind"]["engagement"] == "engaged"
        assert rows["meridian"]["engagement"] == "not_engaged"
        assert rows["meridian"]["hours_since_seen"] is None
        assert rows["northwind"]["hours_since_seen"] == 2

    def test_a_dismissed_account_leaves_the_engaged_half(self, wired, router):
        put_watch(router)
        result = raise_it(router)
        router.log_action(
            result["signal"]["id"],
            {"kind": "dismissed", "note": "Read it last quarter too."},
            actor="dana",
            source=SOURCE,
            now=NOW,
        )
        assert router.engagement(room_id=ROOM, now=NOW)["engaged"] == 0

    def test_the_view_reports_its_own_reading(self, router):
        assert "watchlist" in router.engagement(room_id=ROOM)["reads"]


# --------------------------------------------------------------------------- #
# Rep action logged
# --------------------------------------------------------------------------- #


class TestRepActions:
    def _raised(self, router):
        put_watch(router)
        return raise_it(router)["signal"]["id"]

    def test_a_rep_acknowledging_moves_the_alert_and_the_state_is_derived(self, wired, router):
        result = router.log_action(
            self._raised(router),
            {"kind": "acknowledged", "note": "Calling her."},
            actor="dana",
            source=SOURCE,
            now=NOW,
        )
        assert result["state"] == "acknowledged"
        assert result["alert"]["state"] == "acknowledged"
        assert result["action"]["resulting_state"] == "acknowledged"
        assert result["action"]["actor"] == "dana"

    def test_contacting_after_acknowledging_carries_the_furthest_point(self, wired, router):
        signal_id = self._raised(router)
        router.log_action(
            signal_id,
            {"kind": "acknowledged", "note": "Calling."},
            actor="dana",
            source=SOURCE,
            now=NOW,
        )
        result = router.log_action(
            signal_id,
            {"kind": "contacted", "note": "Voicemail."},
            actor="dana",
            source=SOURCE,
            now=NOW,
        )
        assert result["state"] == "contacted"
        assert len(result["alert"]["actions"]) == 2

    def test_a_dismissal_is_terminal(self, wired, router):
        """ "I decided this was not worth a call" is not undone by looking again."""
        signal_id = self._raised(router)
        router.log_action(
            signal_id,
            {"kind": "dismissed", "note": "Not real intent."},
            actor="dana",
            source=SOURCE,
            now=NOW,
        )
        result = router.log_action(
            signal_id,
            {"kind": "acknowledged", "note": "On it."},
            actor="dana",
            source=SOURCE,
            now=NOW,
        )
        assert result["state"] == "dismissed"

    def test_a_dismissal_without_a_note_is_refused(self, wired, router):
        """The log exists to answer which alerts were not worth a call."""
        with pytest.raises(InvalidAction) as caught:
            router.log_action(
                self._raised(router), {"kind": "dismissed"}, actor="dana", source=SOURCE, now=NOW
            )
        assert caught.value.status == 422

    def test_an_unknown_action_kind_is_refused(self, wired, router):
        with pytest.raises(InvalidAction):
            router.log_action(
                self._raised(router), {"kind": "escalated"}, actor="dana", source=SOURCE, now=NOW
            )

    def test_a_note_is_optional_for_the_kinds_that_are_not_a_refusal(self, wired, router):
        result = router.log_action(
            self._raised(router), {"kind": "noted"}, actor="dana", source=SOURCE, now=NOW
        )
        assert result["state"] == "open"

    def test_the_action_log_is_append_only_and_filterable_by_signal(self, wired, router):
        signal_id = self._raised(router)
        router.log_action(
            signal_id, {"kind": "noted", "note": "Seen."}, actor="a", source=SOURCE, now=NOW
        )
        router.log_action(signal_id, {"kind": "contacted"}, actor="a", source=SOURCE, now=NOW)
        log = router.actions(room_id=ROOM, signal_id=signal_id)
        assert [entry["kind"] for entry in log] == ["noted", "contacted"]

    def test_an_action_on_an_absent_signal_is_404(self, router):
        with pytest.raises(UnknownSignal):
            router.log_action("sig_absent", {"kind": "noted"}, actor="a", source=SOURCE, now=NOW)

    def test_the_log_cannot_be_asked_to_set_a_state_it_does_not_name(self, wired, router):
        """The state is derived from the log, so no payload can set one directly."""
        signal_id = self._raised(router)
        with pytest.raises(InvalidAction):
            router.log_action(
                signal_id, {"kind": "open", "state": "contacted"}, actor="a", source=SOURCE, now=NOW
            )
        assert router.read_signal(signal_id)["id"] == signal_id
        assert router.alerts(room_id=ROOM)[0]["state"] == "open"


# --------------------------------------------------------------------------- #
# Reading the chain back
# --------------------------------------------------------------------------- #


class TestReads:
    def test_an_absent_signal_alert_or_task_is_404_by_name(self, router):
        with pytest.raises(UnknownSignal):
            router.read_signal("sig_absent")
        with pytest.raises(UnknownAlert):
            router.read_alert("al_absent")
        with pytest.raises(UnknownTask):
            router.read_task("task_absent")

    def test_signals_can_be_filtered_by_company(self, wired, router):
        store = router.store
        put_company(store, "meridian", name="Meridian", website="https://meridian.example")
        put_account(store, "acct-me", {"Name": "Meridian", "Website": "https://meridian.example"})
        put_deal(store, "deal-me", account_id="acct-me")
        put_watch(router, accounts=["northwind", "meridian"])
        raise_it(router)
        raise_it(router, company_key="meridian")
        assert len(router.signals(room_id=ROOM, company_key="meridian")) == 1
        assert len(router.signals(room_id=ROOM)) == 2

    def test_alerts_can_be_filtered_by_state(self, wired, router):
        put_watch(router)
        signal_id = raise_it(router)["signal"]["id"]
        router.log_action(signal_id, {"kind": "contacted"}, actor="dana", source=SOURCE, now=NOW)
        assert len(router.alerts(room_id=ROOM, state="contacted")) == 1
        assert router.alerts(room_id=ROOM, state="open") == []

    def test_tasks_can_be_filtered_by_company(self, wired, router):
        put_watch(router)
        raise_it(router)
        assert len(router.tasks(room_id=ROOM, company_key="northwind")) == 1
        assert router.tasks(room_id=ROOM, company_key="meridian") == []


# --------------------------------------------------------------------------- #
# Vocabulary and the decision record
# --------------------------------------------------------------------------- #


class TestPublishedVocabulary:
    def test_the_vocabulary_names_the_one_sourced_threshold_and_four_derived(self, router):
        published = router.vocabulary()
        assert published["sourced_thresholds"] == ["dwell"]
        assert len(published["derived_thresholds"]) == 4
        assert published["thresholds_to_alert"] == 1

    def test_the_vocabulary_quotes_the_three_alert_context_fields(self, router):
        assert router.vocabulary()["alert_context_fields"] == ["pages", "dwell", "stakeholder"]

    def test_the_vocabulary_says_plainly_that_nothing_is_sent(self, router):
        published = router.vocabulary()
        assert published["sends_mail"] is False
        assert published["sends_slack"] is False
        assert "no clean open-source equivalent" in published["why_no_vendor"]

    def test_every_collection_is_named_and_distinct(self, router):
        collections = router.vocabulary()["collections"]
        assert len(set(collections.values())) == len(collections) == 6

    def test_the_published_dispatch_states_include_the_held_one(self, router):
        assert "held_for_integration" in router.vocabulary()["dispatch_states"]
        assert "sent" not in router.vocabulary()["dispatch_states"]


class TestTheDecisionRecord:
    def test_every_inference_is_shaped_so_a_client_can_render_it(self, router):
        assert len(INFERENCES) >= 14
        for entry in INFERENCES:
            assert {"id", "question", "reading", "why", "change", "risk"} <= set(entry), entry["id"]
            assert entry["reading"].strip()
            assert entry["change"].strip()

    def test_the_inference_ids_are_unique(self):
        ids = [entry["id"] for entry in INFERENCES]
        assert len(set(ids)) == len(ids)

    def test_the_derived_thresholds_are_recorded_where_the_numbers_are(self):
        """Four derived numbers and the record that says so must not drift apart."""
        joined = " ".join(entry["reading"] for entry in INFERENCES)
        assert "Pages 4, revisit 3, download 1, demo interaction 1" in joined

    def test_the_two_sourced_entries_are_the_ones_the_research_actually_states(self):
        sourced = {entry["id"] for entry in INFERENCES if entry.get("sourced")}
        assert sourced == {"no_paid_reverse_ip_provider", "declined_open_source_tools"}

    @pytest.mark.parametrize(
        "tool",
        [
            "PostHog",
            "Umami",
            "Matomo",
            "OpenReplay",
            "Snowplow",
            "Node-RED",
            "n8n",
            "Kestra",
            "Superset",
            "Metabase",
        ],
    )
    def test_every_open_source_tool_the_research_lists_is_accounted_for(self, tool):
        declined = next(e for e in INFERENCES if e["id"] == "declined_open_source_tools")
        assert tool in declined["reading"], tool

    def test_the_overlap_with_wf134_is_recorded_as_a_boundary(self):
        entry = next(e for e in INFERENCES if e["id"] == "overlap_with_wf134_and_wf031")
        assert "health score" in entry["reading"]

    def test_the_route_serves_them_with_a_count(self, router):
        served = router.inferences()
        assert served["count"] == len(served["inferences"]) == len(INFERENCES)

    def test_every_refusal_this_package_raises_is_an_intent_routing_error(self):
        """The host refuses two handlers for one type, so the hierarchy hangs off one base."""
        for error in (
            InvalidEngagement,
            UnknownEngagementField,
            UnknownCompany,
            UnresolvedAccount,
            InvalidWatchlist,
            DuplicateWatchlist,
            UnknownWatchlist,
            InvalidRule,
            DuplicateRule,
            UnknownRule,
            UnknownSignal,
            UnknownAlert,
            UnknownTask,
            InvalidAction,
        ):
            assert issubclass(error, IntentRoutingError), error


# --------------------------------------------------------------------------- #
# The demo data
# --------------------------------------------------------------------------- #


class TestTheSeed:
    def _seed(self, database, room_id=ROOM):
        from dsr.features import load_feature

        module = load_feature("wf133_real_time_buyer_intent_alerting_and_routing")
        return module.seed(database, {"room_ids": [(room_id, "Northwind")], "now": NOW})

    def test_the_seed_reports_the_states_it_created(self, db):
        """A demo where every alert is open teaches nothing about a closed one."""
        summary = self._seed(db)
        assert "alerts" in summary
        for state in ("contacted", "dismissed", "open", "suppressed"):
            assert state in summary, state

    def test_every_character_of_the_summary_encodes_as_cp1252(self, db):
        """The seeder prints it on a Windows console, and one arrow broke it once."""
        summary = self._seed(db)
        assert summary.isascii(), summary
        assert summary.encode("cp1252").decode("cp1252") == summary

    def test_the_seed_provisions_its_own_prerequisites(self, db):
        """A demo that appears only when another branch's seed runs first is not a demo."""
        self._seed(db)
        assert len(db.list(COMPANIES, limit=99)) >= 3
        assert len(db.list(CRM_RECORDS, room_id=ROOM, limit=99)) >= 4

    def test_the_seed_is_idempotent(self, db):
        first = self._seed(db)
        second = self._seed(db)
        assert "alerts" in first
        assert "already seeded" in second
        assert len(db.list("wf133_intent_signal", room_id=ROOM, limit=99)) == 3

    def test_the_seed_leaves_one_watched_account_that_never_alerted(self, db):
        """The engagement view needs a not_engaged row or the demo cannot show one."""
        self._seed(db)
        view = IntentRouter(RecordStore(db)).engagement(room_id=ROOM, now=NOW)
        rows = {row["company_key"]: row for row in view["accounts"]}
        assert rows["meridian-foods"]["engagement"] == "not_engaged"
        assert rows["northwind-energy"]["engagement"] == "engaged"

    def test_no_record_the_seed_writes_holds_a_character_outside_cp1252(self, db):
        """The seeder prints samples of what it wrote, so this is a seeder-wide hazard."""
        self._seed(db)
        for collection in ("wf133_alert", "wf133_crm_task", "wf133_intent_signal"):
            for record in db.list(collection, limit=99):
                text = str(record.get("data") or {})
                assert text.encode("cp1252").decode("cp1252") == text, collection

    def test_the_seed_returns_nothing_when_there_are_no_rooms(self, db):
        """A seeder with no rooms is not a failure, and saying nothing is correct."""
        from dsr.features import load_feature

        module = load_feature("wf133_real_time_buyer_intent_alerting_and_routing")
        assert module.seed(db, {"room_ids": [], "now": NOW}) == ""
