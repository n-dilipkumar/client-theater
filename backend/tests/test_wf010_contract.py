"""Tests for the content-search contract.

These are the limits the researched operation documents, pinned as unit tests
with no database and no HTTP: a term that is too long, a page size out of
range, a filter nested one level too deep, a cursor that has expired. If any of
these drift, a client that was written against the contract breaks silently, so
they are asserted on their exact message as well as their type.
"""

from __future__ import annotations

import pytest

from dsr.search.contract import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_RETURN_FIELDS,
    DEFAULT_SEARCH_FIELDS,
    MAX_FILTER_DEPTH,
    MAX_PAGE_SIZE,
    MAX_TERM_LENGTH,
    RELEVANCE,
    TOKEN_INVALID,
    Condition,
    CursorCodec,
    Group,
    LibrarySchema,
    SearchError,
    SearchQuery,
    contract,
    get_path,
    page_size_error,
)
from dsr.search.matching import Scorer, field_text, suggest, tokenize

SCHEMA = LibrarySchema(fields={"name": "title", "format": "kind"})


def parse(body=None):
    return SearchQuery.parse(body, SCHEMA)


# -- term -------------------------------------------------------------------- #


def test_empty_body_queries_everything():
    """Documented behaviour: an empty body is a valid query matching all content."""
    query = parse({})

    assert query.term == ""
    assert query.search_fields == DEFAULT_SEARCH_FIELDS
    assert query.return_fields == DEFAULT_RETURN_FIELDS
    assert query.page_size == DEFAULT_PAGE_SIZE
    assert query.enable_suggested_query_results is False
    assert query.filter is None
    assert query.repository == "library"


def test_none_body_is_the_same_as_empty_body():
    assert parse(None).term == ""


def test_term_is_trimmed():
    assert parse({"term": "  security  "}).term == "security"


def test_term_at_the_limit_is_accepted():
    assert parse({"term": "a" * MAX_TERM_LENGTH}).term == "a" * MAX_TERM_LENGTH


def test_term_over_the_limit_is_rejected_with_the_documented_message():
    with pytest.raises(SearchError) as excinfo:
        parse({"term": "a" * (MAX_TERM_LENGTH + 1)})

    assert str(excinfo.value) == "Search term should be less than 150 characters"


def test_non_string_term_is_rejected():
    with pytest.raises(SearchError, match="must be a string"):
        parse({"term": 42})


# -- page size --------------------------------------------------------------- #


@pytest.mark.parametrize("size", [0, 1, 50, MAX_PAGE_SIZE])
def test_page_size_within_range_is_accepted(size):
    assert parse({"options": {"pageSize": size}}).page_size == size


@pytest.mark.parametrize("size", [-1, MAX_PAGE_SIZE + 1, 150])
def test_page_size_outside_range_is_rejected_with_the_documented_message(size):
    with pytest.raises(SearchError) as excinfo:
        parse({"options": {"pageSize": size}})

    assert str(excinfo.value) == page_size_error(size)
    assert "between 0-100" in str(excinfo.value)


def test_page_size_of_the_wrong_type_is_rejected():
    with pytest.raises(SearchError, match="PageSize"):
        parse({"options": {"pageSize": "20"}})


def test_boolean_is_not_accepted_as_a_page_size():
    """``True`` is an int in Python; a page size of 1 is not what it means."""
    with pytest.raises(SearchError, match="PageSize"):
        parse({"options": {"pageSize": True}})


# -- options ----------------------------------------------------------------- #


def test_search_fields_default_to_the_documented_four():
    assert parse({}).search_fields == ("name", "description", "body", "properties")


def test_return_fields_default_to_the_documented_names():
    """The research says "9 returned by default" and enumerates eight.

    The enumerated names are what a caller depends on, so those are pinned. The
    count in the source is a documentation gap, not a field we failed to find.
    """
    assert parse({}).return_fields == (
        "repository",
        "name",
        "teamsiteId",
        "id",
        "versionId",
        "type",
        "applicationUrls",
        "format",
    )


def test_opt_in_return_fields_are_requestable():
    query = parse({"options": {"returnFields": ["id", "thumbnailUrl", "publishDate"]}})

    assert query.return_fields == ("id", "thumbnailUrl", "publishDate")


def test_unmapped_field_names_resolve_to_themselves():
    """The point of schema flexibility: a field added today is filterable today."""
    query = parse({"options": {"searchFields": ["title", "region"]}})

    assert query.search_fields == ("title", "region")
    assert SCHEMA.resolve("title") == "title"
    assert SCHEMA.resolve("region") == "region"


def test_mapped_field_names_resolve_to_their_json_path():
    assert SCHEMA.resolve("name") == "title"
    assert SCHEMA.resolve("format") == "kind"


def test_search_fields_must_be_a_list_of_strings():
    with pytest.raises(SearchError, match="must be a list of field names"):
        parse({"options": {"searchFields": "name"}})

    with pytest.raises(SearchError, match="non-empty strings"):
        parse({"options": {"searchFields": ["name", ""]}})


def test_empty_field_lists_are_honoured_as_search_nothing():
    """An explicit empty list means the caller wants no fields, not the defaults."""
    assert parse({"options": {"searchFields": []}}).search_fields == ()


def test_enable_suggested_query_results_must_be_a_boolean():
    with pytest.raises(SearchError, match="must be a boolean"):
        parse({"options": {"enableSuggestedQueryResults": "yes"}})


# -- repositories ------------------------------------------------------------ #


def test_documented_repositories_are_accepted():
    for repository in ("library", "WorkSpace"):
        assert parse({"repository": repository}).repository == repository


def test_unknown_repository_is_rejected():
    with pytest.raises(SearchError, match="unknown repository"):
        parse({"repository": "dropbox"})


# -- filter expressions ------------------------------------------------------ #


def test_single_condition_needs_no_group():
    node = parse({"filter": {"field": "profile", "value": "deck"}}).filter

    assert isinstance(node, Condition)
    assert node.operator == "equal"
    assert node.depth == 1


def test_and_group_nests_one_level():
    node = parse(
        {
            "filter": {
                "and": [
                    {"field": "profile", "value": "deck"},
                    {"field": "custom.Region", "value": "APAC"},
                ]
            }
        }
    ).filter

    assert isinstance(node, Group)
    assert node.operator == "and"
    assert node.depth == 1
    assert [child.depth for child in node.children] == [2, 2]


def test_custom_property_field_resolves_under_the_properties_root():
    node = parse({"filter": {"field": "custom.Region", "value": "APAC"}}).filter

    assert node.path == "properties.Region"


def test_filter_depth_of_two_is_accepted():
    node = parse(
        {
            "filter": {
                "and": [
                    {"field": "profile", "value": "deck"},
                    {"or": [{"field": "format", "value": "pdf"}, {"field": "format", "value": "pptx"}]},
                ]
            }
        }
    ).filter

    inner = node.children[1]
    assert isinstance(inner, Group)
    assert inner.depth == 2


def test_filter_depth_of_three_is_rejected_with_the_documented_message():
    with pytest.raises(SearchError) as excinfo:
        parse(
            {
                "filter": {
                    "and": [
                        {"field": "profile", "value": "deck"},
                        {
                            "or": [
                                {"field": "format", "value": "pdf"},
                                {"and": [{"field": "pages", "operator": "greaterThan", "value": 3}]},
                            ]
                        },
                    ]
                }
            }
        )

    assert str(excinfo.value) == "Filter is too complex. Currently the max filter depth is 2."


def test_top_level_group_cannot_hold_another_group():
    """A three-level tree is refused whichever way round it is written."""
    with pytest.raises(SearchError, match="max filter depth is 2"):
        parse(
            {
                "filter": {
                    "and": [
                        {
                            "or": [
                                {"field": "a", "value": 1},
                                {"and": [{"field": "b", "value": 2}]},
                            ]
                        }
                    ]
                }
            }
        )


def test_filter_depth_limit_is_two():
    assert MAX_FILTER_DEPTH == 2


def test_unknown_operator_is_rejected():
    with pytest.raises(SearchError, match="unknown filter operator"):
        parse({"filter": {"field": "pages", "operator": "between", "value": 1}})


def test_condition_needs_a_value():
    with pytest.raises(SearchError, match="needs a 'value'"):
        parse({"filter": {"field": "profile"}})


def test_in_operator_needs_a_list():
    with pytest.raises(SearchError, match="needs a list of values"):
        parse({"filter": {"field": "profile", "operator": "in", "value": "deck"}})


def test_group_needs_a_non_empty_list():
    with pytest.raises(SearchError, match="non-empty list"):
        parse({"filter": {"and": []}})


def test_node_cannot_be_both_a_group_and_a_condition():
    with pytest.raises(SearchError, match="not both"):
        parse({"filter": {"and": [{"field": "a", "value": 1}], "field": "b"}})


def test_filter_must_be_an_object():
    with pytest.raises(SearchError, match="filter must be an object"):
        parse({"filter": "profile=deck"})


def test_custom_field_without_a_property_name_is_rejected():
    with pytest.raises(SearchError, match="needs a property name"):
        parse({"options": {"returnFields": ["custom."]}})


# -- sort -------------------------------------------------------------------- #


def test_sort_defaults_to_nothing_which_means_relevance():
    assert parse({}).sort == ()


def test_sort_parses_field_and_direction():
    keys = parse({"sort": [{"field": "publishDate", "direction": "asc"}]}).sort

    assert keys[0].field == "publishDate"
    assert keys[0].descending is False
    assert keys[0].path == "publishDate"


def test_sort_defaults_to_descending():
    assert parse({"sort": [{"field": "publishDate"}]}).sort[0].descending is True


def test_relevance_sort_key_is_available():
    keys = parse({"sort": [{"field": RELEVANCE}]}).sort

    assert keys[0].path == RELEVANCE


def test_bad_sort_direction_is_rejected():
    with pytest.raises(SearchError, match="must be 'asc' or 'desc'"):
        parse({"sort": [{"field": "publishDate", "direction": "sideways"}]})


# -- fingerprints ------------------------------------------------------------ #


def test_fingerprint_is_stable_for_an_equivalent_query():
    left = parse({"term": "deck", "options": {"pageSize": 5}})
    right = parse({"term": "deck", "options": {"pageSize": 90}})

    assert left.fingerprint() == right.fingerprint()


def test_fingerprint_changes_with_the_term_filter_and_sort():
    base = parse({"term": "deck"}).fingerprint()

    assert parse({"term": "other"}).fingerprint() != base
    assert parse({"term": "deck", "filter": {"field": "profile", "value": "x"}}).fingerprint() != base
    assert parse({"term": "deck", "sort": [{"field": "pages"}]}).fingerprint() != base
    assert parse({"term": "deck", "repository": "WorkSpace"}).fingerprint() != base


# -- round trip -------------------------------------------------------------- #


def test_query_round_trips_through_its_own_body():
    original = {
        "term": "security",
        "options": {"searchFields": ["name"], "returnFields": ["id", "name"], "pageSize": 5},
        "filter": {"and": [{"field": "profile", "value": "deck"}]},
        "sort": [{"field": "publishDate", "direction": "asc"}],
        "repository": "WorkSpace",
    }

    again = SearchQuery.parse(SearchQuery.parse(original, SCHEMA).as_dict(), SCHEMA)

    assert again.as_dict() == SearchQuery.parse(original, SCHEMA).as_dict()
    assert again.term == "security"
    assert again.page_size == 5
    assert again.repository == "WorkSpace"


# -- continuation tokens ----------------------------------------------------- #


def test_token_round_trips():
    codec = CursorCodec(b"secret", ttl_seconds=900)

    token = codec.issue(40, "abc123", now=1000.0)
    cursor = codec.redeem(token, now=1001.0)

    assert cursor.offset == 40
    assert cursor.fingerprint == "abc123"


def test_expired_token_is_rejected_with_the_documented_message():
    codec = CursorCodec(b"secret", ttl_seconds=10)
    token = codec.issue(40, "abc123", now=1000.0)

    with pytest.raises(SearchError) as excinfo:
        codec.redeem(token, now=1011.0)

    assert str(excinfo.value) == TOKEN_INVALID


def test_tampered_token_is_rejected():
    codec = CursorCodec(b"secret")
    body, _, signature = codec.issue(40, "abc", now=1000.0).partition(".")

    with pytest.raises(SearchError, match="invalid or expired"):
        codec.redeem(f"{body}.{signature[:-2]}xx", now=1001.0)


def test_token_from_a_different_secret_is_rejected():
    token = CursorCodec(b"one").issue(10, "abc", now=1000.0)

    with pytest.raises(SearchError, match="invalid or expired"):
        CursorCodec(b"two").redeem(token, now=1001.0)


@pytest.mark.parametrize("token", ["", "garbage", "no-dot-here", "."])
def test_malformed_tokens_are_rejected(token):
    with pytest.raises(SearchError, match="invalid or expired"):
        CursorCodec(b"secret").redeem(token, now=1000.0)


def test_a_codec_needs_a_secret():
    with pytest.raises(ValueError, match="secret is required"):
        CursorCodec(b"")


# -- JSON paths -------------------------------------------------------------- #


@pytest.mark.parametrize(
    "payload,path,expected",
    [
        ({"a": {"b": 1}}, "a.b", 1),
        ({"a": [{"b": 2}]}, "a.0.b", 2),
        ({"a": 1}, "a", 1),
        ({"a": 1}, "b", None),
        ({"a": {"b": 1}}, "a.b.c", None),
        ({"a": []}, "a.0", None),
        ({"a": [1, 2]}, "a.5", None),
    ],
)
def test_get_path(payload, path, expected):
    assert get_path(payload, path) == expected


# -- tokenization and scoring ------------------------------------------------ #


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Security Overview", ["security", "overview"]),
        ("SOC 2 Type II", ["soc", "2", "type", "ii"]),
        (None, []),
        (True, []),
        (12, ["12"]),
    ],
)
def test_tokenize(value, expected):
    assert tokenize(value) == expected


def test_tokenize_walks_objects_and_arrays():
    assert sorted(tokenize({"Region": "APAC"}, include_keys=True)) == ["apac", "region"]


def test_field_text_normalises_whitespace():
    text = field_text("  Security   OVERVIEW \n")

    assert text.normalized == "security overview"
    assert text.tokens == frozenset({"security", "overview"})


def test_scorer_requires_every_term_token():
    scorer = Scorer(SCHEMA)
    record = {"id": "d1", "data": {"title": "Security Overview Deck"}}

    assert scorer.score(record, ["security", "deck"], ("name",)) is not None
    assert scorer.score(record, ["security", "pricing"], ("name",)) is None


def test_scorer_ranks_a_title_match_above_a_body_match():
    scorer = Scorer(SCHEMA)
    in_title = {"id": "a", "data": {"title": "Security Overview"}}
    in_body = {"id": "b", "data": {"title": "Overview", "body": "covers security at length"}}

    left = scorer.score(in_title, ["security"], ("name", "body"))
    right = scorer.score(in_body, ["security"], ("name", "body"))

    assert left.score > right.score


def test_scorer_reports_which_fields_matched():
    scorer = Scorer(SCHEMA)
    record = {"id": "d1", "data": {"title": "Security Deck", "body": "nothing here"}}

    match = scorer.score(record, ["security"], ("name", "body"))

    assert match.matched_fields == ("name",)


def test_empty_term_matches_everything_at_a_flat_score():
    scorer = Scorer(SCHEMA)
    record = {"id": "d1", "data": {"title": "anything"}}

    assert scorer.score(record, [], ("name",)).score == 0.0


def test_custom_properties_are_searched_by_name_and_value():
    scorer = Scorer(SCHEMA)
    record = {"id": "d1", "data": {"title": "Deck", "properties": {"Region": "APAC"}}}

    assert scorer.score(record, ["apac"], ("properties",)) is not None
    assert scorer.score(record, ["region"], ("properties",)) is not None


# -- suggestions ------------------------------------------------------------- #


def test_suggest_prefers_an_extension_of_the_term_ranked_by_frequency():
    counts = {"security": 12, "securable": 3}

    assert suggest("secur", counts) == ["security", "securable"]


def test_suggest_falls_back_to_character_overlap():
    counts = {"security": 12}

    assert suggest("securty", counts) == ["security"]


def test_suggest_ranks_by_document_frequency():
    counts = {"security": 2, "securityreview": 40}

    assert suggest("secur", counts)[0] == "securityreview"


def test_suggest_returns_nothing_for_nonsense():
    assert suggest("zzz", {"security": 3}) == []


def test_suggest_respects_its_limit():
    counts = {f"secur{i}": i for i in range(10)}

    assert len(suggest("secur", counts, limit=2)) == 2


def test_suggest_ignores_an_empty_term():
    assert suggest("   ", {"security": 3}) == []


# -- the discoverable contract ----------------------------------------------- #


def test_contract_reports_the_documented_limits():
    body = contract(SCHEMA, token_ttl_seconds=600)

    assert body["limits"]["maxTermLength"] == 150
    assert body["limits"]["minPageSize"] == 0
    assert body["limits"]["maxPageSize"] == 100
    assert body["limits"]["defaultPageSize"] == 40
    assert body["limits"]["maxFilterDepth"] == 2
    assert body["limits"]["continuationTokenTtlSeconds"] == 600


def test_contract_lists_the_vocabulary_a_client_needs():
    body = contract(SCHEMA)

    assert body["searchFields"] == ["name", "description", "body", "properties"]
    assert body["operators"]["condition"] == [
        "in",
        "equal",
        "greaterThan",
        "greaterThanOrEqual",
        "lessThan",
        "lessThanOrEqual",
    ]
    assert body["operators"]["group"] == ["and", "or"]
    assert body["repositories"] == ["library", "WorkSpace"]
    assert body["customPropertyPrefix"] == "custom."


def test_contract_warns_that_asset_urls_expire():
    """The researched pre-signed URLs last a day; the response has to say so."""
    body = contract(SCHEMA)

    assert body["assetUrls"]["assetUrlTtlDays"] == 1
    assert body["assetUrls"]["assetUrlsExpireAt"]
