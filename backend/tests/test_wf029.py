"""WF-029: the lead-score rules, tested against the domain rather than over HTTP.

Split from ``test029_http.py``, which drives the feature's own mounted router. This
file holds the parts of the workflow a reviewer most wants to check without reading
the diff, and each test names the sentence of the research it is checking:

* the score property and the two buckets, from steps 2 and 3 of the researched flow;
* the five Dock activity properties and the filters each publishes, from step 4 and
  from the two filter recommendations;
* the three outcomes of a match, and why the third one is not folded into the second;
* the score arithmetic, including the two decisions Jev took before the code was
  written.

Isolation: every test builds its own in-memory :class:`AuditedDatabase`. Nothing here
reads a shared file, reads an environment variable, or depends on another test having
run, which is what lets the suite run under ``pytest-xdist`` without an order
dependency.
"""

from __future__ import annotations

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.lead_score import names
from dsr.lead_score.activity import (
    MalformedActivity,
    criterion_task_name,
    normalise_activity,
    occurred_window,
    parse_timestamp,
    task_name_from_text,
)
from dsr.lead_score.criteria import (
    CRITERION_METADATA_KEYS,
    REASONS,
    lint_criterion,
    matches,
    parse_criterion,
    parse_score_value,
    tally_reasons,
)
from dsr.lead_score.engine import MAX_EVENTS_PER_CONTACT, LeadScoreEngine
from dsr.lead_score.errors import (
    CrmOrgDisabled,
    CrmOrgNotConnected,
    InvalidScoreValue,
    LeadScoreError,
    MissingCrmScope,
    UnknownCriterion,
    UnknownScoreBucket,
    UnknownScoreFamily,
    UnknownScoreProperty,
    UnsupportedRefinement,
)
from dsr.lead_score.scoring import (
    batch_plan_for,
    contributions_for,
    crm_plan_for,
    lifecycle_write_verdict,
    recompute,
)
from dsr.lead_score.vocabulary import (
    ALL_REFINEMENTS,
    BASELINE_QUOTE,
    BUCKET_SIGN,
    BUCKETS,
    CRM_PLAN,
    FILTER_FAMILIES,
    LIFECYCLE_CONSTRAINT,
    REFINEMENTS,
    REQUIRED_SCOPES,
    SCORE_PROPERTY,
    describe,
    dig,
    family_for_action,
    first_present,
    lifecycle_rank,
    published_family_names,
    require_bucket,
    require_family,
    require_refinement,
    require_score_property,
)

ACTOR = "dana"
SOURCE = "test"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def db():
    database = AuditedDatabase(":memory:", actor="test")
    try:
        yield database
    finally:
        database.close()


@pytest.fixture()
def engine(db):
    """An engine over a fresh database, with a frozen clock.

    The clock is frozen so every timestamp in a response is the same string, which is
    what lets a test assert on a whole row without it drifting between runs.
    """
    return LeadScoreEngine(db, now=lambda: "2026-10-04T00:00:00+00:00")


@pytest.fixture()
def armed(engine):
    """An engine with step 1 and step 1b done: a writable org and four properties.

    ``map_activity`` is left unprovisioned on purpose, so the "a criterion on a
    property nobody provisioned" state is available to every test that needs it.
    """
    engine.register_integration(
        {"scopes": list(REQUIRED_SCOPES), "deal_connections": {"room_1": {"deal_id": "d1"}}},
        actor=ACTOR,
        source=SOURCE,
    )
    engine.seed_properties(
        ("views", "clicks", "downloads", "interactions"), actor=ACTOR, source=SOURCE
    )
    return engine


def criterion_row(**overrides):
    """A minimal valid criterion payload, with the researched defaults."""
    payload = {"family": "downloads", "bucket": "positive", "score": 20}
    payload.update(overrides)
    return payload


def event(**overrides):
    """A DSR event in the shape a webhook sends."""
    payload = {
        "contact": "priya.raman@northwind.example",
        "action": "downloaded",
        "occurred_at": "2026-10-01T09:00:00+00:00",
    }
    payload.update(overrides)
    return normalise_activity(payload)


# --------------------------------------------------------------------------- #
# The vocabulary, as the research states it
# --------------------------------------------------------------------------- #


def test_the_score_property_is_named_as_the_research_names_it():
    """ "In HubSpot, lead scoring is automatically created as a contact property as
    'HubSpot Score.'" The capital S is part of the string somebody searches for."""
    assert SCORE_PROPERTY == "HubSpot Score"
    assert describe()["score_property"] == "HubSpot Score"


def test_there_are_exactly_two_buckets_and_each_carries_a_sign():
    """ "Click Add criteria for either positive or negative scores." Two, and the
    research reaches the score value afterwards, so the sign lives on the bucket."""
    assert BUCKETS == ("positive", "negative")
    assert BUCKET_SIGN == {"positive": 1, "negative": -1}
    assert [entry["name"] for entry in describe()["buckets"]] == list(BUCKETS)


def test_there_are_exactly_five_dock_properties():
    """The four analytics events plus MAP activity. Asserting the count is
    load-bearing: a reader who counted only the analytics events would publish four."""
    assert FILTER_FAMILIES == ("views", "clicks", "downloads", "interactions", "map_activity")
    assert len(FILTER_FAMILIES) == 5
    assert describe()["family_count"] == 5


def test_the_refinement_matrix_follows_the_two_filter_recommendations():
    """'Occurred' is a baseline on all five. The second filter is named per family,
    and Views is named in the recommendation sentence but has no second one."""
    assert REFINEMENTS == {
        "views": ("occurred",),
        "clicks": ("occurred", "link_name"),
        "downloads": ("occurred", "file_name"),
        "interactions": ("occurred", "link_name"),
        "map_activity": ("occurred", "task_name"),
    }
    for family, published in REFINEMENTS.items():
        assert published[0] == "occurred", family
    assert set(ALL_REFINEMENTS) == {"occurred", "link_name", "file_name", "task_name"}


def test_the_baseline_is_quoted_verbatim_because_it_is_a_recommendation():
    """The verb matters: 'we recommend', not 'you must'. A criterion without one is
    warned about, never refused."""
    served = describe()
    assert served["baseline_refinement"] == "occurred"
    assert served["baseline_quote"] == BASELINE_QUOTE
    assert "recommend" in served["baseline_quote"]


def test_the_two_researched_scopes_are_published():
    assert REQUIRED_SCOPES == ("crm.objects.contacts.read", "crm.objects.contacts.write")
    assert describe()["integration"]["required_scopes"] == list(REQUIRED_SCOPES)


def test_the_four_crm_endpoints_the_research_names_are_recorded():
    """Recorded rather than called. The batch endpoint is named in the same sentence
    as the single PATCH, and the extensibility path in the same research."""
    plan = describe()["crm_plan"]
    assert plan["write_contact"] == "PATCH /crm/v3/objects/contacts/{contactId}"
    assert plan["batch_upsert"] == "POST /crm/v3/objects/contacts/batch/upsert"
    assert (
        plan["association_labels"]
        == "POST /crm/v4/associations/{fromObjectType}/{toObjectType}/labels"
    )
    assert plan["create_property"] == "POST /crm/v3/properties"
    assert set(plan) == set(CRM_PLAN)


def test_the_lifecycle_constraint_is_quoted_because_the_build_must_design_around_it():
    """ "When you include the `lifecyclestage` property, you can only set the value
    *forward* in the stage order." A tuple, because a set cannot answer 'is this
    move backwards'."""
    assert "*forward*" in LIFECYCLE_CONSTRAINT
    assert lifecycle_rank("lead") < lifecycle_rank("opportunity")
    assert lifecycle_rank("customer") > lifecycle_rank("lead")
    assert lifecycle_rank("not-a-stage") is None
    assert describe()["lifecycle_stages"][0] == "subscriber"


def test_the_served_vocabulary_names_the_collections_a_record_lives_in(armed):
    """So a reader of the registry can see what a WF-029 record is without opening
    the domain package. Assembled by the engine, which is what the route serves."""
    served = armed.vocabulary()
    assert served["collections"] == {
        "integration": names.INTEGRATIONS,
        "property": names.PROPERTIES,
        "criterion": names.CRITERIA,
        "activity": names.ACTIVITY,
        "contact": names.CONTACTS,
        "run": names.RUNS,
    }


def test_the_auto_add_sentence_is_marked_out_of_scope_rather_than_reproduced():
    """The research offers it as an analogy for a different workflow over companies.
    A page that shows its own edges does not overstate itself."""
    note = describe()["auto_add_note"]
    assert "Out of scope" in note
    assert "companies" in note


# --------------------------------------------------------------------------- #
# Require / normalise
# --------------------------------------------------------------------------- #


def test_require_family_accepts_the_five_and_refuses_a_sixth():
    assert require_family(" Downloads ") == "downloads"
    for family in FILTER_FAMILIES:
        assert require_family(family) == family
    with pytest.raises(UnknownScoreFamily) as excinfo:
        require_family("shares")
    message = str(excinfo.value)
    assert "unknown Dock property 'shares'" in message
    assert "would never match an event" in message


def test_require_family_refuses_an_empty_name_and_names_the_five():
    with pytest.raises(UnknownScoreFamily) as excinfo:
        require_family(None)
    for family in FILTER_FAMILIES:
        assert family in str(excinfo.value)


def test_require_refinement_refuses_a_filter_the_property_does_not_publish():
    """'Views: filter by date' in the sibling article, and no second filter named for
    it anywhere, so refining views by a file name is a category error."""
    assert require_refinement("views", "occurred") == "occurred"
    with pytest.raises(UnsupportedRefinement) as excinfo:
        require_refinement("views", "file_name")
    assert "names no other filter" in str(excinfo.value)


def test_require_refinement_resolves_the_spellings_a_client_might_send():
    """A form will produce link_url or activity_text; the published name is
    link_name or task_name, and both spellings mean the same criterion."""
    assert require_refinement("clicks", "link_url") == "link_name"
    assert require_refinement("clicks", "url") == "link_name"
    assert require_refinement("map_activity", "activity_text") == "task_name"
    assert require_refinement("downloads", "file_name") == "file_name"


def test_require_bucket_names_the_two_it_accepts():
    assert require_bucket(" Positive ") == "positive"
    assert require_bucket("negative") == "negative"
    with pytest.raises(UnknownScoreBucket) as excinfo:
        require_bucket("sideways")
    assert "positive, negative" in str(excinfo.value)


def test_require_score_property_defaults_to_the_researched_name():
    assert require_score_property(None) == SCORE_PROPERTY
    assert require_score_property("") == SCORE_PROPERTY
    assert require_score_property("  ") == SCORE_PROPERTY


def test_require_score_property_accepts_a_third_party_name_because_the_research_promises_one():
    """'Third parties can add their own criteria by writing custom contact
    properties.' Refusing the name would break the extensibility the sources
    promise, so the name is kept and reported unresolved elsewhere."""
    assert require_score_property("Showing SMB Intent") == "Showing SMB Intent"
    with pytest.raises(UnknownScoreProperty):
        require_score_property(17)


def test_family_for_action_maps_this_products_own_words_and_reports_the_rest():
    assert family_for_action("viewed") == "views"
    assert family_for_action("opened_link") == "clicks"
    assert family_for_action("completed_section") == "map_activity"
    assert family_for_action("teleported") is None
    assert family_for_action(None) is None


def test_published_family_names_canonises_order_and_drops_the_unknown():
    assert published_family_names(["downloads", "views", "shares"]) == ["views", "downloads"]
    assert published_family_names([]) == []


def test_dig_reads_a_dotted_path_and_indexes_a_list():
    payload = {"a": {"b": 1}, "list": [{"x": "first"}, {"x": "second"}]}
    assert dig(payload, "a.b") == 1
    assert dig(payload, "list.1.x") == "second"
    assert dig(payload, "list.9.x", "fallback") == "fallback"
    assert dig(payload, "a.missing", "fallback") == "fallback"


def test_first_present_skips_a_non_scalar_and_an_empty_string():
    """A webhook carries 'user': {'id': ..., 'email': ...}, so the alias resolves to an
    object rather than to the buyer. Returning the object would make every text read
    downstream compare a dict against a string and quietly never match."""
    payload = {"user": {"id": 7}, "person": "  ", "contact": "buyer@example.test"}
    assert first_present(payload, "contact") == "buyer@example.test"
    assert first_present({"user": {"id": 7}}, "contact") is None
    assert first_present(None, "contact") is None


# --------------------------------------------------------------------------- #
# Timestamps and the Occurred window
# --------------------------------------------------------------------------- #


def test_a_naive_timestamp_is_read_as_utc():
    """The product stores UTC and the researched filters are date filters, so a naive
    local reading would move a buyer's event across a day boundary."""
    parsed = parse_timestamp("2026-10-01T09:00:00")
    assert parsed is not None and parsed.utcoffset().total_seconds() == 0


def test_an_epoch_past_a_human_lifetime_is_read_as_milliseconds():
    assert parse_timestamp(1767225600).year == 2026
    assert parse_timestamp(1767225600000).year == 2026


def test_an_unreadable_timestamp_is_none_rather_than_a_guess():
    assert parse_timestamp("not a date") is None
    assert parse_timestamp(True) is None
    assert parse_timestamp(None) is None


def test_a_bare_date_is_that_whole_utc_day():
    """ "Occurred" is a date control in the research's source, so a bare date is a
    range rather than the single instant of midnight."""
    window = occurred_window("2026-10-01")
    assert window is not None
    start, end = window
    assert start.hour == 0 and end.hour == 23 and end.microsecond == 999999


def test_a_date_only_upper_bound_includes_the_whole_day():
    window = occurred_window({"from": "2026-10-01", "to": "2026-10-01"})
    assert window is not None
    start, end = window
    assert start.day == 1 and end.day == 1


def test_a_window_may_be_open_at_either_end():
    window = occurred_window({"from": "2026-10-01"})
    assert window is not None and window[1] is None
    window = occurred_window({"to": "2026-10-01"})
    assert window is not None and window[0] is None


def test_an_unreadable_or_backwards_window_is_none():
    assert occurred_window("nonsense") is None
    assert occurred_window({"from": "2026-10-05", "to": "2026-10-01"}) is None
    assert occurred_window(None) is None


# --------------------------------------------------------------------------- #
# The MAP task name
# --------------------------------------------------------------------------- #


def test_the_documented_task_sentence_yields_the_quoted_name():
    """The evidence gives two worked examples verbatim, both a 'completed task ...'
    sentence with the name quoted."""
    assert task_name_from_text('completed task "Sign up for free account"') == (
        "Sign up for free account"
    )
    assert task_name_from_text("completed task 'Intro call'") == "Intro call"
    assert task_name_from_text("completed section 'Security'") == "Security"


def test_a_bare_name_and_the_documented_sentence_name_the_same_criterion():
    assert criterion_task_name('completed task "Intro call"') == criterion_task_name("Intro call")


def test_text_outside_the_documented_shape_compares_whole():
    """A literal this product never documented still works, rather than being refused
    for being unfamiliar."""
    assert task_name_from_text("Signed the order form") is None
    assert criterion_task_name("Signed the order form") == "Signed the order form"


def test_an_empty_task_text_resolves_to_nothing():
    assert task_name_from_text(None) is None
    assert task_name_from_text("   ") is None
    assert criterion_task_name(17) is None


# --------------------------------------------------------------------------- #
# Reading an activity event
# --------------------------------------------------------------------------- #


def test_an_event_is_read_through_the_aliases():
    """A webhook payload and this product's own activity rows are the same fact under
    two vocabularies, so both land in the same function."""
    from_webhook = normalise_activity(
        {
            "user": {"id": 7, "email": "buyer@example.test"},
            "event": "downloaded",
            "filename": "Pricing One-Pager",
            "happened_at": "2026-10-01T09:00:00Z",
            "company": "Northwind Traders",
        }
    )
    assert from_webhook["contact"] == "buyer@example.test"
    assert from_webhook["action_family"] == "downloads"
    assert from_webhook["file_name"] == "Pricing One-Pager"
    assert from_webhook["occurred_at"] == "2026-10-01T09:00:00+00:00"
    assert from_webhook["account"] == "Northwind Traders"

    from_rows = normalise_activity({"person": "buyer@example.test", "action": "viewed"})
    assert from_rows["action_family"] == "views"


def test_an_event_with_no_contact_is_refused_because_it_could_not_score_anybody():
    """The research ties every scoreable activity to the contact record and the score
    lands on a contact property."""
    with pytest.raises(MalformedActivity) as excinfo:
        normalise_activity({"action": "viewed"})
    assert "must name a contact" in str(excinfo.value)


def test_an_event_that_is_not_an_object_is_refused():
    with pytest.raises(MalformedActivity):
        normalise_activity(["not", "an", "object"])


def test_an_unclassified_action_is_kept_and_the_word_named():
    """The activity stays on the record with the reason, so the count of events and
    the count of events that can score are not quietly different numbers."""
    normalised = normalise_activity({"contact": "buyer@example.test", "action": "teleported"})
    assert normalised["action_family"] is None
    codes = {warning["code"] for warning in normalised["warnings"]}
    assert "action_unclassified" in codes
    assert "teleported" in str(normalised["warnings"])


def test_an_event_with_no_timestamp_is_kept_with_the_reason_it_cannot_be_filtered():
    normalised = normalise_activity({"contact": "buyer@example.test", "action": "viewed"})
    assert normalised["occurred_at"] is None
    assert "no_occurred_at" in {warning["code"] for warning in normalised["warnings"]}


def test_an_event_may_assert_the_dock_property_directly_and_conflict_is_reported():
    """A webhook sender knows the vendor's taxonomy better than this build's table."""
    asserted = normalise_activity(
        {"contact": "buyer@example.test", "action": "teleported", "action_family": "views"}
    )
    assert asserted["action_family"] == "views"

    conflicting = normalise_activity(
        {"contact": "buyer@example.test", "action": "viewed", "action_family": "downloads"}
    )
    assert conflicting["action_family"] == "downloads"
    assert "family_assertion_conflicts" in {w["code"] for w in conflicting["warnings"]}

    unknown = normalise_activity(
        {"contact": "buyer@example.test", "action": "viewed", "action_family": "shares"}
    )
    assert unknown["action_family"] == "views"
    assert "unknown_asserted_family" in {w["code"] for w in unknown["warnings"]}


def test_an_activity_under_a_data_envelope_is_read_the_same_way():
    normalised = normalise_activity({"data": {"contact": "buyer@example.test", "action": "viewed"}})
    assert normalised["contact"] == "buyer@example.test"


# --------------------------------------------------------------------------- #
# Parsing a criterion
# --------------------------------------------------------------------------- #


def test_a_valid_criterion_parses_into_the_researched_shape():
    parsed = parse_criterion(
        criterion_row(
            label="Read the pricing pack",
            refinements={"occurred": {"from": "2026-09-01"}, "file_name": "Pricing One-Pager"},
        )
    )
    assert parsed["family"] == "downloads"
    assert parsed["bucket"] == "positive"
    assert parsed["score"] == 20
    assert parsed["label"] == "Read the pricing pack"
    assert parsed["property_resolved"] is True
    assert set(parsed["refinements"]) == {"occurred", "file_name"}


def test_the_flat_spelling_is_accepted_because_a_client_will_produce_either():
    flat = parse_criterion(criterion_row(occurred="2026-10-01", file_name="Pricing One-Pager"))
    nested = parse_criterion(
        criterion_row(refinements={"occurred": "2026-10-01", "file_name": "Pricing One-Pager"})
    )
    assert flat["refinements"] == nested["refinements"]


def test_a_mistyped_filter_is_refused_rather_than_dropped():
    """The flat spelling means an unknown key is taken to be a filter name, so a typo
    in 'file_name' is reported instead of quietly scoring nothing."""
    with pytest.raises(UnsupportedRefinement) as excinfo:
        parse_criterion(criterion_row(occured="2026-10-01"))
    assert "file_name" in str(excinfo.value) or "occurred" in str(excinfo.value)


def test_every_criterion_field_is_excluded_from_the_flat_filter_read():
    """The flat spelling means any key that is not a known criterion field is taken to
    be a filter name. If one of these leaked, saving a criterion would fail with a
    message about a filter nobody wrote."""
    payload = criterion_row()
    # Only the keys that are *not* the three the validator reads directly; family,
    # bucket and score are checked in their own right and would fail on their own
    # meaning rather than on leaking into the filter set.
    filler = CRITERION_METADATA_KEYS - {"family", "action_family", "bucket", "score", "refinements"}
    payload.update({key: "x" for key in filler})
    payload["label"] = "Read the pricing pack"
    payload["enabled"] = True
    parsed = parse_criterion(payload)
    assert parsed["score"] == 20
    assert parsed["label"] == "Read the pricing pack"
    assert parsed["refinements"] == {}


def test_the_score_value_must_be_a_positive_whole_number():
    assert parse_score_value(20) == 20
    assert parse_score_value(20.0) == 20
    for bad in (0, -5, 2.5, "20", True, None):
        with pytest.raises(InvalidScoreValue):
            parse_score_value(bad)


def test_a_zero_or_negative_score_is_refused_and_the_bucket_is_named():
    """The researched order is bucket first, then value, so a negative number would be
    a criterion stored two ways."""
    with pytest.raises(InvalidScoreValue) as excinfo:
        parse_score_value(-10)
    assert "negative bucket" in str(excinfo.value)


def test_an_unreadable_occurred_filter_is_refused_with_the_three_shapes_it_accepts():
    with pytest.raises(LeadScoreError) as excinfo:
        parse_criterion(criterion_row(refinements={"occurred": "sometime last week"}))
    assert "from, to" in str(excinfo.value)


def test_an_empty_text_filter_is_refused():
    with pytest.raises(LeadScoreError) as excinfo:
        parse_criterion(criterion_row(refinements={"file_name": "   "}))
    assert "exact text to match" in str(excinfo.value)


def test_a_criterion_must_be_an_object():
    with pytest.raises(LeadScoreError):
        parse_criterion(["family", "downloads"])


def test_a_criterion_with_no_family_names_the_five():
    with pytest.raises(UnknownScoreFamily) as excinfo:
        parse_criterion({"bucket": "positive", "score": 5})
    assert "views" in str(excinfo.value)


def test_a_criterion_with_no_bucket_names_the_two():
    with pytest.raises(UnknownScoreBucket) as excinfo:
        parse_criterion({"family": "views", "score": 5})
    assert "positive, negative" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Lint
# --------------------------------------------------------------------------- #


def test_a_criterion_without_the_occurred_baseline_is_warned_about_not_refused():
    """The research says 'we recommend', so refusing would block something the sources
    permit. The warning carries the quote so the reader can see it is advice."""
    parsed = parse_criterion(criterion_row(family="views", refinements={}))
    codes = {warning["code"] for warning in lint_criterion(parsed)}
    assert "no_refinement" in codes
    assert "no_occurred_filter" in codes

    with_occurred = parse_criterion(criterion_row(refinements={"occurred": "2026-10-01"}))
    assert "no_occurred_filter" not in {w["code"] for w in lint_criterion(with_occurred)}


def test_a_criterion_writing_to_a_third_party_property_warns_and_still_saves():
    parsed = parse_criterion(criterion_row(score_property="Showing SMB Intent"))
    assert parsed["property_resolved"] is False
    warnings = [w for w in lint_criterion(parsed) if w["code"] == "unresolved_property"]
    assert warnings and "POST /crm/v3/properties" not in warnings[0]["message"]
    assert "Showing SMB Intent" in warnings[0]["message"]


def test_a_large_score_value_is_reported_because_the_research_publishes_no_scale():
    parsed = parse_criterion(criterion_row(score=200))
    codes = {warning["code"] for warning in lint_criterion(parsed)}
    assert "large_score_value" in codes


def test_a_fully_specified_criterion_lints_clean():
    parsed = parse_criterion(
        criterion_row(refinements={"occurred": "2026-10-01", "file_name": "Pricing One-Pager"})
    )
    assert lint_criterion(parsed) == []


# --------------------------------------------------------------------------- #
# Matching: three outcomes, not two
# --------------------------------------------------------------------------- #


def test_a_matching_event_reports_every_filter_that_held():
    criterion = parse_criterion(
        criterion_row(refinements={"file_name": "Pricing One-Pager", "occurred": "2026-10-01"})
    )
    verdict = matches(criterion, event(file_name="Pricing One-Pager"))
    assert verdict["matched"] is True
    assert verdict["reason"] == "matched"
    assert {row["refinement"] for row in verdict["checked"]} == {"file_name", "occurred"}
    assert verdict["failed"] == [] and verdict["unverifiable"] == []


def test_an_event_of_another_dock_property_is_a_family_mismatch():
    """ "Choose the Dock property you want to score against" is a statement about one
    property, not about all activity."""
    criterion = parse_criterion(criterion_row())
    verdict = matches(criterion, event(action="viewed"))
    assert verdict["matched"] is False
    assert verdict["reason"] == "family_mismatch"
    assert REASONS["family_mismatch"] in verdict["detail"]


def test_an_unclassifiable_event_matches_nothing_and_says_so():
    criterion = parse_criterion(criterion_row())
    verdict = matches(criterion, event(action="teleported"))
    assert verdict["matched"] is False
    assert verdict["reason"] == "action_unclassified"


def test_an_event_outside_the_occurred_window_is_a_miss_with_the_side_named():
    criterion = parse_criterion(criterion_row(refinements={"occurred": "2026-10-01"}))
    before = matches(criterion, event(occurred_at="2026-09-30T09:00:00Z"))
    after = matches(criterion, event(occurred_at="2026-10-02T09:00:00Z"))
    assert before["reason"] == "occurred_before_window"
    assert after["reason"] == "occurred_after_window"


def test_each_text_filter_reports_its_own_mismatch():
    downloads = parse_criterion(criterion_row(refinements={"file_name": "Pricing One-Pager"}))
    assert matches(downloads, event(file_name="Contract Draft"))["reason"] == "file_name_mismatch"

    clicks = parse_criterion(
        criterion_row(family="clicks", refinements={"link_name": "https://x.test/a"})
    )
    assert matches(clicks, event(action="opened_link", link_url="https://x.test/b"))["reason"] == (
        "link_name_mismatch"
    )

    plan = parse_criterion(
        criterion_row(family="map_activity", refinements={"task_name": "Intro call"})
    )
    verdict = matches(plan, event(action="completed_task", activity_text="completed task 'Demo'"))
    assert verdict["reason"] == "task_name_mismatch"


def test_a_filter_the_event_does_not_carry_is_unverifiable_and_does_not_match():
    """A score change needs positive evidence, and an unanswered question must not
    move a score - but it is reported separately from a miss so a criterion that
    quietly does nothing can be diagnosed."""
    criterion = parse_criterion(criterion_row(refinements={"file_name": "Pricing One-Pager"}))
    verdict = matches(criterion, event())
    assert verdict["matched"] is False
    assert verdict["reason"] == "refinement_unverifiable"
    assert verdict["unverifiable"][0]["refinement"] == "file_name"
    assert verdict["failed"] == []


def test_a_filter_on_a_missing_timestamp_is_unverifiable_rather_than_a_miss():
    criterion = parse_criterion(criterion_row(refinements={"occurred": "2026-10-01"}))
    verdict = matches(criterion, event(occurred_at=None))
    assert verdict["reason"] == "refinement_unverifiable"


def test_a_compound_filter_reports_the_failures_and_the_held_filters_together():
    criterion = parse_criterion(
        criterion_row(refinements={"occurred": "2026-10-01", "file_name": "Contract Draft"})
    )
    verdict = matches(criterion, event(file_name="Pricing One-Pager"))
    assert verdict["matched"] is False
    assert {row["refinement"] for row in verdict["checked"]} == {"occurred"}
    assert {row["refinement"] for row in verdict["failed"]} == {"file_name"}


def test_a_file_name_comparison_ignores_case_but_a_link_comparison_does_not():
    """A file name is a human label spelled inconsistently; a URL path is an
    identifier whose case is significant."""
    downloads = parse_criterion(criterion_row(refinements={"file_name": "pricing one-pager"}))
    assert matches(downloads, event(file_name="Pricing One-Pager"))["matched"] is True

    clicks = parse_criterion(
        criterion_row(family="clicks", refinements={"link_name": "https://x.test/A"})
    )
    assert matches(clicks, event(action="opened_link", link_url="https://x.test/a"))["matched"] is (
        False
    )


def test_no_filter_is_a_substring_match_because_the_research_publishes_no_such_option():
    criterion = parse_criterion(criterion_row(refinements={"file_name": "Pricing One-Pager"}))
    assert matches(criterion, event(file_name="Pricing One-Pager 2024"))["matched"] is False


def test_a_task_filter_matches_the_documented_sentence_or_the_bare_name():
    criterion = parse_criterion(
        criterion_row(family="map_activity", refinements={"task_name": "Intro call"})
    )
    assert (
        matches(
            criterion, event(action="completed_task", activity_text="completed task 'Intro call'")
        )["matched"]
        is True
    )


def test_tally_reasons_counts_rather_than_sampling():
    """A criterion that moved nobody's score is usually 'almost everything was the
    wrong property, and one thing was the right one with the wrong link'."""
    tally = tally_reasons(
        [
            {"reason": "family_mismatch"},
            {"reason": "family_mismatch"},
            {"reason": "family_mismatch"},
            {"reason": "family_mismatch"},
            {"reason": "link_name_mismatch"},
        ]
    )
    assert tally == "link_name_mismatch x1, family_mismatch x4"
    assert tally_reasons([]) == "no events at all"
    assert tally_reasons(None) == "no events at all"
    assert tally_reasons([{"detail": "no reason"}]) == "no events at all"


# --------------------------------------------------------------------------- #
# The score arithmetic
# --------------------------------------------------------------------------- #


def test_the_score_is_the_signed_total_of_every_criterion_over_the_whole_history():
    """ "Added/subtracted from the HubSpot Score contact property", assembled from the
    history rather than incremented as events arrive - the recompute_from_history
    decision Jev took at audit jev-20261004T024258-6152-78859."""
    positive = parse_criterion(criterion_row(refinements={"file_name": "Pricing One-Pager"}))
    positive["id"] = "c_positive"
    negative = parse_criterion(
        criterion_row(
            family="clicks",
            bucket="negative",
            score=5,
            refinements={"link_name": "https://x.test/j"},
        )
    )
    negative["id"] = "c_negative"
    events = [
        event(file_name="Pricing One-Pager"),
        event(file_name="Pricing One-Pager"),
        event(action="opened_link", link_url="https://x.test/j"),
        event(action="opened_link", link_url="https://x.test/careers"),
    ]
    rows = contributions_for([positive, negative], events)
    total = recompute(rows, previous=0, minimum_score=None, evaluated_at="2026-10-04T00:00:00Z")
    assert total["raw_score"] == 40 - 5
    assert total["score"] == 35
    assert total["delta"] == 35 and total["changed"] is True
    assert total["floor_applied"] is False


def test_recomputation_is_idempotent_so_a_replay_moves_nothing():
    criterion = parse_criterion(criterion_row(refinements={"file_name": "Pricing One-Pager"}))
    events = [event(file_name="Pricing One-Pager")]
    rows = contributions_for([criterion], events)
    first = recompute(rows, previous=0, minimum_score=None, evaluated_at="t")
    second = recompute(rows, previous=first["score"], minimum_score=None, evaluated_at="t")
    assert second["score"] == first["score"]
    assert second["delta"] == 0 and second["changed"] is False


def test_only_criteria_that_scored_appear_as_contributions():
    criterion = parse_criterion(criterion_row(refinements={"file_name": "Pricing One-Pager"}))
    rows = contributions_for([criterion], [event(action="viewed")])
    assert rows[0]["points"] == 0
    assert rows[0]["reason"] == "family_mismatch"
    total = recompute(rows, previous=7, minimum_score=None, evaluated_at="t")
    assert total["contributions"] == []
    assert total["criteria"][0]["matched_count"] == 0


def test_a_criterion_with_no_events_at_all_says_so_distinctly():
    criterion = parse_criterion(criterion_row())
    rows = contributions_for([criterion], [])
    assert rows[0]["reason"] == "no_events"
    assert "no activity in this room" in rows[0]["detail"]


def test_each_contribution_names_the_criterion_the_event_scored_under():
    criterion = parse_criterion(criterion_row())
    criterion["id"] = "c_one"
    criterion["label"] = "Read the pricing pack"
    rows = contributions_for([criterion], [event()])
    total = recompute(rows, previous=0, minimum_score=None, evaluated_at="t")
    contribution = total["contributions"][0]
    assert contribution["criterion_id"] == "c_one"
    assert contribution["label"] == "Read the pricing pack"
    assert contribution["events"] == 1 and contribution["points"] == 20


def test_there_is_no_floor_unless_one_is_set():
    """The sources describe adding and subtracting and say nothing about a floor.
    Inventing one would make every negative criterion a no-op on a cold contact."""
    criterion = parse_criterion(
        criterion_row(family="clicks", bucket="negative", score=10, refinements={})
    )
    rows = contributions_for([criterion], [event(action="opened_link")])
    assert recompute(rows, previous=0, minimum_score=None, evaluated_at="t")["score"] == -10

    floored = recompute(rows, previous=0, minimum_score=0, evaluated_at="t")
    assert floored["score"] == 0
    assert floored["raw_score"] == -10
    assert floored["floor_applied"] is True


def test_the_crm_plan_carries_the_three_researched_requests_and_is_marked_unexecuted():
    plan = crm_plan_for(
        contact="priya.raman@northwind.example",
        room_id="room_1",
        account="Northwind Traders",
        score=35,
        score_property=SCORE_PROPERTY,
        from_score=0,
        evaluated_at="2026-10-04T00:00:00Z",
    )
    assert plan["executed"] is False
    purposes = [request["purpose"] for request in plan["requests"]]
    assert purposes == ["write_contact", "batch_upsert", "association_labels"]
    patch = plan["requests"][0]
    assert patch["method"] == "PATCH"
    assert patch["path"] == "/crm/v3/objects/contacts/priya.raman@northwind.example"
    assert patch["body"]["properties"] == {SCORE_PROPERTY: 35}
    assert plan["requests"][1]["body"]["inputs"][0]["idProperty"] == "email"
    assert plan["requests"][1]["path"] == "/crm/v3/objects/contacts/batch/upsert"
    assert plan["requests"][2]["path"] == "/crm/v4/associations/contacts/deals/labels"


def test_a_lifecyclestage_value_is_reported_with_the_stage_comparison_and_never_written():
    """ "When you include the `lifecyclestage` property, you can only set the value
    *forward* in the stage order." A score write has no business moving a stage, and a
    field dropped silently is the failure this codebase exists to avoid."""
    verdict = lifecycle_write_verdict("lead", "opportunity")
    assert verdict is not None
    assert verdict["accepted"] is False
    assert "at or behind" in verdict["comparison"]
    assert verdict["reason"] == LIFECYCLE_CONSTRAINT

    forward = lifecycle_write_verdict("customer", "lead")
    assert forward is not None and forward["accepted"] is False
    assert "ahead of" in forward["comparison"]

    assert lifecycle_write_verdict(None, "lead") is None
    assert lifecycle_write_verdict("  ", "lead") is None
    assert "not one of the eight" in lifecycle_write_verdict("champion", "lead")["comparison"]
    assert "no stage order can be read" in lifecycle_write_verdict("lead", "champion")["comparison"]


def test_a_dropped_lifecyclestage_appears_on_the_plan_rather_than_vanishing():
    plan = crm_plan_for(
        contact="buyer@example.test",
        room_id="room_1",
        account="Northwind Traders",
        score=5,
        score_property=SCORE_PROPERTY,
        from_score=0,
        evaluated_at="t",
        lifecyclestage="lead",
        current_lifecycle_stage="opportunity",
    )
    assert len(plan["dropped_properties"]) == 1
    assert "lifecyclestage" not in plan["requests"][0]["body"]["properties"]


def test_the_batch_plan_is_one_request_for_every_contact_a_run_touched():
    plan = batch_plan_for(
        [
            {"contact": "a@example.test", "score": 5, "score_property": SCORE_PROPERTY},
            {"contact": "b@example.test", "score": -3, "score_property": SCORE_PROPERTY},
        ]
    )
    assert plan["count"] == 2
    assert plan["executed"] is False
    assert [row["id"] for row in plan["body"]["inputs"]] == ["a@example.test", "b@example.test"]
    assert plan["body"]["inputs"][1]["properties"][SCORE_PROPERTY] == -3


def test_a_batch_plan_with_no_contacts_is_still_a_valid_empty_request():
    plan = batch_plan_for([])
    assert plan["count"] == 0 and plan["body"]["inputs"] == []


# --------------------------------------------------------------------------- #
# The engine: step 1 and the prerequisites
# --------------------------------------------------------------------------- #


def test_registering_an_organisation_reports_the_scopes_it_still_needs(engine):
    result = engine.register_integration(
        {"scopes": ["crm.objects.contacts.read"]}, actor=ACTOR, source=SOURCE
    )
    organisation = result["integration"]
    assert result["created"] is True
    assert organisation["id"]
    assert organisation["missing_scopes"] == ["crm.objects.contacts.write"]
    assert organisation["writable"] is False
    assert organisation["deal_connections"] == {}


def test_saving_a_criterion_without_an_organisation_is_refused_with_409(engine):
    """Step 1 is a step, not a formality: without one there is no contact property to
    write and no token to write it with."""
    with pytest.raises(CrmOrgNotConnected) as excinfo:
        engine.add_criterion(criterion_row(), actor=ACTOR, source=SOURCE)
    assert excinfo.value.status == 409
    assert "no CRM organisation is registered" in str(excinfo.value)


def test_saving_a_criterion_against_a_switched_off_integration_is_refused(engine):
    """ "Verify the HubSpot integration is on." A rule that can never fire is worse than
    one that will not save."""
    engine.register_integration(
        {"scopes": list(REQUIRED_SCOPES), "enabled": False}, actor=ACTOR, source=SOURCE
    )
    with pytest.raises(CrmOrgDisabled) as excinfo:
        engine.add_criterion(criterion_row(), actor=ACTOR, source=SOURCE)
    assert excinfo.value.status == 409


def test_saving_a_criterion_on_a_read_only_token_is_refused_and_names_the_scope(engine):
    """A token that can look a contact up cannot move its score, so a criterion armed
    on one would report a score nobody received."""
    engine.register_integration(
        {"scopes": ["crm.objects.contacts.read"]}, actor=ACTOR, source=SOURCE
    )
    with pytest.raises(MissingCrmScope) as excinfo:
        engine.add_criterion(criterion_row(), actor=ACTOR, source=SOURCE)
    assert "crm.objects.contacts.write" in str(excinfo.value)


def test_amending_an_organisation_switches_it_on_and_adds_the_missing_scope(engine):
    registered = engine.register_integration({}, actor=ACTOR, source=SOURCE)["integration"]
    amended = engine.amend_integration(
        registered["id"],
        {"enabled": True, "scopes": list(REQUIRED_SCOPES)},
        actor=ACTOR,
        source=SOURCE,
    )["integration"]
    assert amended["enabled"] is True
    assert amended["missing_scopes"] == []
    assert amended["writable"] is True


def test_a_null_deal_connection_removes_the_room(engine):
    """A merge patch is how 'remove this key' is expressed everywhere else in this
    product, and step 1's 'connected to a deal/account' has to be able to become untrue."""
    registered = engine.register_integration(
        {"scopes": list(REQUIRED_SCOPES), "deal_connections": {"room_1": {"deal_id": "d1"}}},
        actor=ACTOR,
        source=SOURCE,
    )["integration"]
    amended = engine.amend_integration(
        registered["id"], {"deal_connections": {"room_1": None}}, actor=ACTOR, source=SOURCE
    )["integration"]
    assert amended["deal_connections"] == {}
    assert amended["connected_rooms"] == []


def test_a_deal_connection_may_be_given_as_a_bare_deal_id(engine):
    registered = engine.register_integration({}, actor=ACTOR, source=SOURCE)["integration"]
    amended = engine.amend_integration(
        registered["id"],
        {"scopes": list(REQUIRED_SCOPES), "deal_connections": {"room_1": "deal_9"}},
        actor=ACTOR,
        source=SOURCE,
    )["integration"]
    assert amended["deal_connections"] == {"room_1": {"deal_id": "deal_9"}}


def test_amending_an_unknown_organisation_is_refused_and_names_the_step(engine):
    with pytest.raises(CrmOrgNotConnected):
        engine.amend_integration("lead_score_integration_nope", {}, actor=ACTOR, source=SOURCE)


def test_a_minimum_score_is_stored_as_a_setting_and_not_applied_until_set(armed):
    organisation = armed.integrations()["integrations"][0]
    assert organisation["minimum_score"] is None
    armed.amend_integration(organisation["id"], {"minimum_score": 0}, actor=ACTOR, source=SOURCE)
    assert armed.integrations()["integrations"][0]["minimum_score"] == 0


def test_a_non_object_deal_connection_map_is_refused(armed):
    organisation = armed.integrations()["integrations"][0]
    with pytest.raises(LeadScoreError):
        armed.amend_integration(
            organisation["id"], {"deal_connections": ["room_1"]}, actor=ACTOR, source=SOURCE
        )


# --------------------------------------------------------------------------- #
# The engine: the Dock properties
# --------------------------------------------------------------------------- #


def test_provisioning_reports_what_is_left_to_provision(armed):
    served = armed.properties()
    assert served["provisioned"] == ["views", "clicks", "downloads", "interactions"]
    assert served["awaiting_provisioning"] == ["map_activity"]
    assert served["families"] == list(FILTER_FAMILIES)
    assert "engagement object" in served["provisioning_note"]


def test_provisioning_the_same_property_twice_updates_rather_than_duplicates(armed):
    """The fifth property is the one the fixture leaves out, so the first call creates
    and the second updates."""
    first = armed.provision_property(
        {"family": "map_activity", "name": "dsr_map_activity"}, actor=ACTOR, source=SOURCE
    )
    assert first["created"] is True
    again = armed.provision_property(
        {"family": "map_activity", "name": "dsr_map_activity"}, actor=ACTOR, source=SOURCE
    )
    assert again["created"] is False
    assert armed.store.db.count(names.PROPERTIES) == 5
    assert armed.properties()["provisioned"] == list(FILTER_FAMILIES)
    assert armed.properties()["awaiting_provisioning"] == []


def test_a_property_row_carries_the_record_id_rather_than_a_copy_inside_the_payload(armed):
    """The store mints the id on insert, so a copy inside ``data`` is either empty or
    costs a second write to fill in."""
    served = armed.properties()["properties"][0]
    assert served["id"].startswith(names.PROPERTIES)
    assert served["object"] == "contact"


# --------------------------------------------------------------------------- #
# The engine: the criteria lifecycle
# --------------------------------------------------------------------------- #


def test_a_saved_criterion_is_armed_when_its_dock_property_exists(armed):
    saved = armed.add_criterion(
        criterion_row(refinements={"file_name": "Pricing One-Pager"}), actor=ACTOR, source=SOURCE
    )["criterion"]
    assert saved["armed"] is True
    assert saved["provisioned"] is True
    assert saved["id"].startswith(names.CRITERIA)
    assert saved["sign"] == 1


def test_a_criterion_on_an_unprovisioned_property_saves_but_is_not_armed(armed):
    """The issue states the consequence: without the engagement object provisioned,
    every rule matches nothing and the score never moves. Refusing to save would stop
    a seller preparing their criteria before provisioning arrives."""
    saved = armed.add_criterion(
        criterion_row(family="map_activity", refinements={"task_name": "Intro call"}),
        actor=ACTOR,
        source=SOURCE,
    )["criterion"]
    assert saved["family"] == "map_activity"
    assert saved["armed"] is False
    assert armed.criteria()["armed"] == 0


def test_the_criterion_list_can_be_filtered_by_bucket_and_by_property(armed):
    armed.add_criterion(criterion_row(), actor=ACTOR, source=SOURCE)
    armed.add_criterion(
        criterion_row(family="clicks", bucket="negative", score=5), actor=ACTOR, source=SOURCE
    )
    assert armed.criteria()["count"] == 2
    assert armed.criteria(bucket="negative")["count"] == 1
    assert armed.criteria(family="clicks")["count"] == 1
    assert armed.criteria(family="views")["count"] == 0


def test_a_criterion_is_amendable_while_armed_and_needs_no_republishing(armed):
    saved = armed.add_criterion(criterion_row(), actor=ACTOR, source=SOURCE)["criterion"]
    amended = armed.amend_criterion(saved["id"], {"score": 50}, actor=ACTOR, source=SOURCE)[
        "criterion"
    ]
    assert amended["score"] == 50
    assert amended["armed"] is True
    assert amended["created_at"] == saved["created_at"]


def test_amending_an_unknown_criterion_is_404_not_400(armed):
    with pytest.raises(UnknownCriterion) as excinfo:
        armed.amend_criterion("lead_score_criterion_nope", {"score": 5}, actor=ACTOR, source=SOURCE)
    assert excinfo.value.status == 404


def test_withdrawing_a_criterion_removes_it_from_the_live_list_but_keeps_the_row(armed):
    saved = armed.add_criterion(criterion_row(), actor=ACTOR, source=SOURCE)["criterion"]
    armed.withdraw_criterion(saved["id"], actor=ACTOR, source=SOURCE)
    assert armed.criteria()["count"] == 0
    assert armed.store.list(names.CRITERIA, include_deleted=True, limit=10)


def test_withdrawing_an_unknown_criterion_is_404(armed):
    with pytest.raises(UnknownCriterion):
        armed.withdraw_criterion("nope", actor=ACTOR, source=SOURCE)


def test_a_disabled_criterion_does_not_arm_but_is_still_listed(armed):
    armed.add_criterion(
        criterion_row(refinements={"file_name": "Pricing One-Pager"}), actor=ACTOR, source=SOURCE
    )
    stored = armed.criteria()["criteria"][0]
    armed.store.update(stored["id"], {"enabled": False}, actor=ACTOR, source=SOURCE)
    assert armed.criteria()["criteria"][0]["armed"] is False


# --------------------------------------------------------------------------- #
# The engine: activity and the continuous rule
# --------------------------------------------------------------------------- #


def test_an_event_scores_its_contact_and_stores_both(armed):
    armed.add_criterion(
        criterion_row(refinements={"file_name": "Pricing One-Pager"}), actor=ACTOR, source=SOURCE
    )
    result = armed.record_activity(
        "room_1",
        {
            "contact": "priya.raman@northwind.example",
            "action": "downloaded",
            "file_name": "Pricing One-Pager",
            "occurred_at": "2026-10-01T09:00:00Z",
        },
        actor=ACTOR,
        source=SOURCE,
    )
    assert result["created"] is True and result["duplicate"] is False
    assert result["score"]["score"] == 20
    assert result["score"]["from_score"] == 0
    assert result["score"]["changed"] is True
    assert armed.store.db.count(names.ACTIVITY) == 1
    assert armed.store.db.count(names.CONTACTS) == 1


def test_a_second_event_raises_the_score_by_the_criterion_value_again(armed):
    """Continuous: every matching event re-evaluates, and the recomputed total grows
    because the history now holds two matching events."""
    armed.add_criterion(
        criterion_row(refinements={"file_name": "Pricing One-Pager"}), actor=ACTOR, source=SOURCE
    )
    for hour in ("09", "11"):
        armed.record_activity(
            "room_1",
            {
                "contact": "buyer@example.test",
                "action": "downloaded",
                "file_name": "Pricing One-Pager",
                "occurred_at": f"2026-10-01T{hour}:00:00Z",
            },
            actor=ACTOR,
            source=SOURCE,
        )
    assert armed.contact_score("room_1", "buyer@example.test")["score"] == 40


def test_a_repeated_event_with_the_same_key_is_a_counter_and_moves_nothing(armed):
    armed.add_criterion(
        criterion_row(refinements={"file_name": "Pricing One-Pager"}), actor=ACTOR, source=SOURCE
    )
    payload = {
        "contact": "buyer@example.test",
        "action": "downloaded",
        "file_name": "Pricing One-Pager",
        "occurred_at": "2026-10-01T09:00:00Z",
        "idempotency_key": "wh-1",
    }
    armed.record_activity("room_1", payload, actor=ACTOR, source=SOURCE)
    again = armed.record_activity("room_1", payload, actor=ACTOR, source=SOURCE)
    assert again["created"] is False and again["duplicate"] is True
    assert again["score"]["score"] == 20
    assert again["score"]["changed"] is False
    assert armed.store.db.count(names.ACTIVITY) == 1
    listed = armed.activities("room_1")["activities"]
    assert listed[0]["duplicate_attempts"] == 1


def test_withdrawing_a_criterion_takes_its_points_off_at_the_next_event(armed):
    """The consequence of recomputing rather than accumulating, and the reason Jev
    chose it: a withdrawn rule leaves no claim on the contact."""
    positive = armed.add_criterion(
        criterion_row(refinements={"file_name": "Pricing One-Pager"}), actor=ACTOR, source=SOURCE
    )["criterion"]
    armed.record_activity(
        "room_1",
        {
            "contact": "buyer@example.test",
            "action": "downloaded",
            "file_name": "Pricing One-Pager",
            "occurred_at": "2026-10-01T09:00:00Z",
        },
        actor=ACTOR,
        source=SOURCE,
    )
    assert armed.contact_score("room_1", "buyer@example.test")["score"] == 20
    armed.withdraw_criterion(positive["id"], actor=ACTOR, source=SOURCE)
    result = armed.score("room_1", {"contact": "buyer@example.test"}, actor=ACTOR, source=SOURCE)
    assert result["score"] == 0
    assert result["delta"] == -20


def test_an_event_in_one_room_never_scores_a_contact_in_another(armed):
    """Room scoping has a trap here: ``room_id`` is a column and not part of ``data``,
    so filtering on it through the dynamic index matches nothing and says so quietly.
    This test is the guard against that returning."""
    armed.add_criterion(
        criterion_row(refinements={"file_name": "Pricing One-Pager"}), actor=ACTOR, source=SOURCE
    )
    armed.amend_integration(
        armed.integrations()["integrations"][0]["id"],
        {"deal_connections": {"room_1": {"deal_id": "d1"}, "room_2": {"deal_id": "d2"}}},
        actor=ACTOR,
        source=SOURCE,
    )
    armed.record_activity(
        "room_1",
        {
            "contact": "buyer@example.test",
            "action": "downloaded",
            "file_name": "Pricing One-Pager",
            "occurred_at": "2026-10-01T09:00:00Z",
        },
        actor=ACTOR,
        source=SOURCE,
    )
    assert armed.contact_score("room_1", "buyer@example.test")["score"] == 20
    assert armed.contact_score("room_2", "buyer@example.test") is None
    other = armed.score("room_2", {"contact": "buyer@example.test"}, actor=ACTOR, source=SOURCE)
    assert other["score"] == 0 and other["event_count"] == 0


def test_a_run_in_a_room_with_no_deal_connected_reports_it_rather_than_refusing(armed):
    """One unconnected room does not invalidate a criterion for every other room."""
    armed.add_criterion(
        criterion_row(refinements={"file_name": "Pricing One-Pager"}), actor=ACTOR, source=SOURCE
    )
    result = armed.score(
        "room_unconnected", {"contact": "buyer@example.test"}, actor=ACTOR, source=SOURCE
    )
    codes = {finding["code"] for finding in result["findings"]}
    assert "room_not_deal_connected" in codes
    assert result["score"] == 0


def test_a_run_reports_a_criterion_whose_property_is_not_provisioned(armed):
    """The dependency the issue records, as a finding on the run rather than a mystery."""
    armed.add_criterion(
        criterion_row(family="map_activity", refinements={"task_name": "Intro call"}),
        actor=ACTOR,
        source=SOURCE,
    )
    result = armed.score("room_1", {"contact": "buyer@example.test"}, actor=ACTOR, source=SOURCE)
    findings = [f for f in result["findings"] if f["code"] == "property_not_provisioned"]
    assert findings and "map_activity" in findings[0]["message"]


def test_a_run_with_no_criteria_saved_says_so(armed):
    result = armed.score("room_1", {"contact": "buyer@example.test"}, actor=ACTOR, source=SOURCE)
    assert "no_criteria_saved" in {finding["code"] for finding in result["findings"]}
    assert result["score"] == 0


def test_a_run_without_a_contact_is_refused(engine):
    """The researched score lands on a contact property, so there is no run without
    one."""
    engine.register_integration({"scopes": list(REQUIRED_SCOPES)}, actor=ACTOR, source=SOURCE)
    with pytest.raises(LeadScoreError) as excinfo:
        engine.score("room_1", {}, actor=ACTOR, source=SOURCE)
    assert "needs the contact" in str(excinfo.value)


def test_a_run_carries_the_batch_plan_and_the_property_write_plan(armed):
    armed.add_criterion(
        criterion_row(score_property="Showing SMB Intent"), actor=ACTOR, source=SOURCE
    )
    result = armed.score("room_1", {"contact": "buyer@example.test"}, actor=ACTOR, source=SOURCE)
    assert result["batch_plan"]["count"] == 1
    assert result["batch_plan"]["executed"] is False
    assert result["payload_plan"]["endpoint"] == "POST /crm/v3/properties"
    assert result["payload_plan"]["unresolved_properties"] == ["Showing SMB Intent"]
    # The unresolved property is not the one the run writes.
    assert result["crm_plan"]["score_property"] == SCORE_PROPERTY


def test_the_run_records_its_driver_so_a_page_can_see_what_fired(armed):
    """One contact has one score row, so the driver a reader sees is the last run's.
    The history is where every driver is visible."""
    armed.add_criterion(criterion_row(), actor=ACTOR, source=SOURCE)
    manual = armed.score(
        "room_1", {"contact": "buyer@example.test"}, actor=ACTOR, source=SOURCE, driver="manual"
    )
    assert manual["driver"] == "manual"
    armed.record_activity(
        "room_1",
        {
            "contact": "buyer@example.test",
            "action": "downloaded",
            "file_name": "Pricing One-Pager",
            "occurred_at": "2026-10-01T09:00:00Z",
        },
        actor=ACTOR,
        source=SOURCE,
    )
    assert armed.scores("room_1")["scores"][0]["last_driver"] == "activity_event"
    assert {row["driver"] for row in armed.history("room_1")["runs"]} == {
        "manual",
        "activity_event",
    }


def test_a_replayed_event_records_its_own_driver_in_the_history(armed):
    armed.add_criterion(criterion_row(), actor=ACTOR, source=SOURCE)
    payload = {
        "contact": "buyer@example.test",
        "action": "downloaded",
        "idempotency_key": "wh-9",
    }
    armed.record_activity("room_1", payload, actor=ACTOR, source=SOURCE)
    armed.record_activity("room_1", payload, actor=ACTOR, source=SOURCE)
    assert "duplicate_event" in {row["driver"] for row in armed.history("room_1")["runs"]}


def test_a_contact_with_a_long_history_is_reported_as_truncated_rather_than_cut(armed):
    """>= MAX is the trigger, and the finding names the number. A report that quietly
    drops the oldest events would produce a score nobody can account for."""
    assert MAX_EVENTS_PER_CONTACT == 1000
    armed.add_criterion(
        criterion_row(family="views", refinements={"occurred": {"from": "2000-01-01"}}),
        actor=ACTOR,
        source=SOURCE,
    )
    db = armed.store.db
    payload = {
        "contact": "buyer@example.test",
        "action": "viewed",
        "occurred_at": "2026-10-01T09:00:00Z",
    }
    with db.transaction(actor=ACTOR, source=SOURCE) as writer:
        for _ in range(MAX_EVENTS_PER_CONTACT):
            writer.create(names.ACTIVITY, payload, room_id="room_1")
    result = armed.score("room_1", {"contact": "buyer@example.test"}, actor=ACTOR, source=SOURCE)
    assert "history_truncated" in {finding["code"] for finding in result["findings"]}


# --------------------------------------------------------------------------- #
# The engine: reads
# --------------------------------------------------------------------------- #


def test_scores_are_ordered_highest_first_and_counted(armed):
    """Highest first means numerically highest, so a contact below zero sorts after one
    above it however much activity each had."""
    armed.add_criterion(criterion_row(family="clicks", refinements={}), actor=ACTOR, source=SOURCE)
    armed.add_criterion(
        criterion_row(family="clicks", bucket="negative", score=30, refinements={}),
        actor=ACTOR,
        source=SOURCE,
    )
    for contact, count in (("cold@example.test", 1), ("warm@example.test", 4)):
        for index in range(count):
            armed.record_activity(
                "room_1",
                {
                    "contact": contact,
                    "action": "opened_link",
                    "occurred_at": f"2026-10-0{index + 1}T09:00:00Z",
                },
                actor=ACTOR,
                source=SOURCE,
            )
    served = armed.scores("room_1")
    by_contact = {row["contact"]: row["score"] for row in served["scores"]}
    # One event is worth +20 from the positive criterion and -30 from the negative one.
    assert by_contact == {"warm@example.test": -40, "cold@example.test": -10}
    assert [row["contact"] for row in served["scores"]] == [
        "cold@example.test",
        "warm@example.test",
    ]
    assert served["total"] == 2


def test_a_contact_above_zero_sorts_above_one_below_zero(armed):
    armed.add_criterion(
        criterion_row(family="clicks", bucket="positive", score=10, refinements={}),
        actor=ACTOR,
        source=SOURCE,
    )
    armed.add_criterion(
        criterion_row(family="downloads", bucket="negative", score=10, refinements={}),
        actor=ACTOR,
        source=SOURCE,
    )
    armed.record_activity(
        "room_1",
        {
            "contact": "cold@example.test",
            "action": "downloaded",
            "occurred_at": "2026-10-01T09:00:00Z",
        },
        actor=ACTOR,
        source=SOURCE,
    )
    armed.record_activity(
        "room_1",
        {
            "contact": "warm@example.test",
            "action": "opened_link",
            "occurred_at": "2026-10-01T09:00:00Z",
        },
        actor=ACTOR,
        source=SOURCE,
    )
    served = armed.scores("room_1")
    assert [row["contact"] for row in served["scores"]] == [
        "warm@example.test",
        "cold@example.test",
    ]


def test_a_contact_score_carries_its_recent_runs_newest_first(armed):
    armed.add_criterion(
        criterion_row(refinements={"file_name": "Pricing One-Pager"}), actor=ACTOR, source=SOURCE
    )
    for hour in ("09", "11"):
        armed.record_activity(
            "room_1",
            {
                "contact": "buyer@example.test",
                "action": "downloaded",
                "file_name": "Pricing One-Pager",
                "occurred_at": f"2026-10-01T{hour}:00:00Z",
            },
            actor=ACTOR,
            source=SOURCE,
        )
    row = armed.contact_score("room_1", "buyer@example.test")
    assert row["score"] == 40
    assert len(row["history"]) == 2
    assert row["history"][0]["to_score"] >= row["history"][1]["to_score"]


def test_an_unknown_contact_has_no_score_rather_than_a_zero_one(armed):
    assert armed.contact_score("room_1", "nobody@example.test") is None


def test_history_can_be_filtered_to_the_runs_that_moved(armed):
    """The researched rule is continuous, so a run that moved nothing is still the
    record of the rule having fired - and both views are wanted."""
    armed.add_criterion(
        criterion_row(refinements={"file_name": "Pricing One-Pager"}), actor=ACTOR, source=SOURCE
    )
    armed.record_activity(
        "room_1",
        {
            "contact": "buyer@example.test",
            "action": "downloaded",
            "file_name": "Pricing One-Pager",
            "occurred_at": "2026-10-01T09:00:00Z",
        },
        actor=ACTOR,
        source=SOURCE,
    )
    armed.record_activity(
        "room_1",
        {
            "contact": "buyer@example.test",
            "action": "viewed",
            "occurred_at": "2026-10-01T11:00:00Z",
        },
        actor=ACTOR,
        source=SOURCE,
    )
    everything = armed.history("room_1")
    moved = armed.history("room_1", moved_only=True)
    assert everything["count"] == 2
    assert moved["count"] == 1
    assert moved["runs"][0]["delta"] == 20


def test_a_history_row_says_why_the_criteria_that_scored_nothing_did_not(armed):
    armed.add_criterion(criterion_row(), actor=ACTOR, source=SOURCE)
    armed.score("room_1", {"contact": "buyer@example.test"}, actor=ACTOR, source=SOURCE)
    row = armed.history("room_1")["runs"][0]
    assert row["miss_tally"] == "no_events x1"
    assert row["criterion_count"] == 1 and row["matched_criteria"] == 0


def test_the_activity_list_can_be_filtered_by_contact(armed):
    for contact in ("a@example.test", "b@example.test"):
        armed.record_activity(
            "room_1",
            {"contact": contact, "action": "viewed", "occurred_at": "2026-10-01T09:00:00Z"},
            actor=ACTOR,
            source=SOURCE,
        )
    assert armed.activities("room_1")["count"] == 2
    assert armed.activities("room_1", contact="a@example.test")["count"] == 1
    assert armed.activities("room_1", limit=1)["count"] == 1


def test_the_summary_reports_the_numbers_and_the_states_that_are_not_successes(armed):
    armed.add_criterion(
        criterion_row(family="clicks", bucket="negative", score=10, refinements={}),
        actor=ACTOR,
        source=SOURCE,
    )
    armed.add_criterion(
        criterion_row(family="map_activity", refinements={"task_name": "Intro call"}),
        actor=ACTOR,
        source=SOURCE,
    )
    armed.record_activity(
        "room_1",
        {
            "contact": "buyer@example.test",
            "action": "opened_link",
            "occurred_at": "2026-10-01T09:00:00Z",
        },
        actor=ACTOR,
        source=SOURCE,
    )
    served = armed.summary("room_1")
    assert served["criteria_count"] == 2
    assert served["criteria_by_bucket"] == {"positive": 1, "negative": 1}
    assert served["criteria_by_family"]["map_activity"] == 1
    assert served["contacts_scored"] == 1
    assert served["contacts_below_zero"] == 1
    assert served["top_score"] == -10
    assert served["average_score"] == -10
    assert served["integration_state"] == "writable"
    assert served["deal_connected"] is True
    assert served["awaiting_provisioning"] == ["map_activity"]
    codes = {entry["code"]: entry for entry in served["states"]}
    assert codes["property_not_provisioned"]["count"] == 1
    assert codes["no_criteria_saved"]["count"] == 0
    assert codes["negative_scores"]["count"] == 1
    assert codes["runs_that_moved_nothing"]["count"] == 0


def test_a_summary_with_no_organisation_reports_that_rather_than_raising(engine):
    served = engine.summary("room_1")
    assert served["integration_state"] == "not_registered"
    assert served["missing_scopes"] == list(REQUIRED_SCOPES)
    assert served["criteria_count"] == 0
    assert served["top_score"] == 0
    assert served["crm_plan"] == dict(CRM_PLAN)


def test_a_summary_of_a_room_with_no_deal_says_so(armed):
    assert armed.summary("room_2")["deal_connected"] is False


def test_the_preview_matches_an_unsaved_criterion_and_sends_nothing(armed):
    served = armed.preview(
        {
            "criterion": {
                "family": "downloads",
                "bucket": "positive",
                "score": 15,
                "refinements": {"file_name": "Pricing One-Pager"},
            },
            "event": {
                "contact": "buyer@example.test",
                "action": "downloaded",
                "file_name": "Pricing One-Pager",
                "occurred_at": "2026-10-01T09:00:00Z",
            },
        }
    )
    assert served["sends_nothing"] is True
    assert served["verdict"]["matched"] is True
    assert served["would_add"] == 15
    assert armed.criteria()["count"] == 0
    assert armed.store.db.count(names.ACTIVITY) == 0


def test_the_preview_reports_a_negative_criterion_as_a_subtraction(armed):
    served = armed.preview(
        {
            "criterion": {"family": "clicks", "bucket": "negative", "score": 8},
            "event": {"contact": "b@example.test", "action": "opened_link"},
        }
    )
    assert served["would_add"] == -8


def test_the_preview_reports_a_miss_as_zero_points(armed):
    served = armed.preview(
        {
            "criterion": {"family": "views", "bucket": "positive", "score": 3},
            "event": {"contact": "b@example.test", "action": "downloaded"},
        }
    )
    assert served["would_add"] == 0
    assert served["verdict"]["reason"] == "family_mismatch"


def test_the_vocabulary_route_adds_the_matcher_and_the_collections(armed):
    served = armed.vocabulary()
    assert served["matcher"]["match_reasons"] == REASONS
    assert "recommendation is not a requirement" in served["matcher"]["baseline_policy"]
    assert served["collections"]["run"] == names.RUNS
    assert served["actionability_note"].startswith("Nothing happens on the seller's screen")


# --------------------------------------------------------------------------- #
# The inferences register
# --------------------------------------------------------------------------- #


def test_the_inferences_are_served_with_the_sourced_half_beside_the_inferred_one(armed):
    served = armed.inferences()
    assert served["count"] == len(served["inferences"])
    assert served["sourced"]["score_property"] == SCORE_PROPERTY
    assert served["sourced"]["buckets"] == list(BUCKETS)
    assert served["sourced"]["required_scopes"] == list(REQUIRED_SCOPES)
    assert "Click Add criteria for either positive or negative scores" in served["sourced_quote"]


def test_every_inference_is_named_traceable_bounded_and_visible(armed):
    """A judgement call in a comment is one nobody re-reads. Each entry has to say
    what was decided, on what basis, how to change it, and what it affects."""
    required = {"id", "topic", "basis", "value", "why", "change_it", "blast_radius"}
    for entry in armed.inferences()["inferences"]:
        assert required <= set(entry), entry["id"]
        assert entry["why"].strip()
        assert entry["blast_radius"].strip()


def test_the_three_jev_decisions_are_recorded_with_their_audit_ids(armed):
    """Each decision was put to Jev before the code was written, and the audit id is
    the record of the reasoning."""
    decisions = {entry["id"]: entry["audit"] for entry in armed.inferences()["jev_decisions"]}
    assert decisions == {
        "score-is-recomputed-from-the-whole-history": "jev-20261004T024258-6152-78859",
        "no-outbound-crm-call": "jev-20261004T024259-6152-79173",
        "self-contained-domain-package": "jev-20261004T024139-28028-99762",
    }


def test_the_inference_ids_are_unique(armed):
    ids = [entry["id"] for entry in armed.inferences()["inferences"]]
    assert len(ids) == len(set(ids))


def test_the_boundary_is_recorded_rather_than_left_unstated(armed):
    """A feature whose page does not show its own edges overstates itself."""
    served = armed.inferences()
    boundary = next(e for e in served["inferences"] if e["id"] == "not-built")
    assert boundary["value"]["outbound_crm_calls"].startswith("not built")
    assert "auto_add_boundary" in boundary["value"]
    assert "third_party_property_writes" in boundary["value"]


def test_the_floor_inference_matches_what_the_code_does(armed):
    entry = next(
        e
        for e in armed.inferences()["inferences"]
        if e["id"] == "there-is-no-score-floor-unless-one-is-set"
    )
    assert entry["value"]["default_minimum"] is None
    assert armed.integrations()["integrations"][0]["minimum_score"] is None
