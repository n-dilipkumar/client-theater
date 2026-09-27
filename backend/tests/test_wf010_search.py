"""Tests for library search and room assembly over the audited store.

This is where the workflow's promises are checked end to end on the store side:
a search finds what the filter and the term asked for, paging is stable and
stops when the token expires, an index-backed filter agrees with a scanned one,
and assembling content into a room is one transaction with one audit row.

Converted from ``backend/tests/test_library_search.py`` on
``feature/WF-010-search-the-content-library-to-assemble-a-room``. Three things
changed, and none of them is the workflow's behaviour:

* :func:`dsr.search.scan` and its two siblings replaced ``store.scan()``,
  ``store.count()`` and ``store.audit_count()``. The branch got those by editing
  the shared ``dsr/store.py``; the port reconstructs them in
  :mod:`dsr.search.reads` on top of the API the store already exposes, so no
  shared file is touched.
* every ``assemble`` / ``save`` call now passes ``source=``. The branch defaulted
  it to a literal URL inside the domain function; the route owns that string now,
  and these tests pass the same value the route does.
"""

from __future__ import annotations

import pytest

from dsr.db.audited import AuditedDatabase
from dsr.search import (
    CursorCodec,
    LibraryAssembler,
    LibrarySchema,
    LibrarySearch,
    SearchError,
    SearchQuery,
    audit_count,
    count,
    scan,
)
from dsr.store import RecordStore

SCHEMA = LibrarySchema(fields={"name": "title", "format": "kind"})

#: The audit ``source`` a route would pass. Pinned here so the tests assert the
#: domain takes it from the caller rather than inventing a path of its own.
SOURCE = "POST /api/library/assemble"
SEARCH_SOURCE = "POST /api/library/searches"

LIBRARY = [
    {
        "title": "Enterprise Overview Deck",
        "description": "Company overview for enterprise buyers",
        "body": "Covers the platform, the teams, and the security model in depth.",
        "kind": "pptx",
        "profile": "deck",
        "pages": 24,
        "publishDate": "2026-01-15",
        "properties": {"Region": "APAC", "Owner": "dana"},
    },
    {
        "title": "Security and Compliance Pack",
        "description": "SOC 2, ISO 27001 and the penetration test summary",
        "body": "Every security question a buyer's review team asks, answered.",
        "kind": "pdf",
        "profile": "pack",
        "pages": 48,
        "publishDate": "2026-03-02",
        "properties": {"Region": "EMEA", "Owner": "sam"},
    },
    {
        "title": "Pricing One-Pager",
        "description": "What it costs, with no asterisks",
        "body": "Per-seat pricing with an enterprise band.",
        "kind": "pdf",
        "profile": "onepager",
        "pages": 2,
        "publishDate": "2026-05-20",
        "properties": {"Region": "APAC", "Owner": "dana"},
    },
    {
        "title": "API Integration Guide",
        "description": "REST and webhook reference",
        "body": "Authentication, rate limits, and every endpoint.",
        "kind": "pdf",
        "profile": "guide",
        "pages": 32,
        "publishDate": "2026-02-11",
        "properties": {"Region": "AMER", "Owner": "sam"},
    },
]


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(tmp_path / "library.db", mirror_dir=tmp_path / "mirror")
    record_store = RecordStore(db)
    record_store.bulk_create("document", LIBRARY, actor="seed", source="test")
    yield record_store
    db.close()


@pytest.fixture()
def clock():
    """A clock the tests move by hand, so token expiry is deterministic."""

    class Clock:
        now = 1_000_000.0

        def __call__(self):
            return self.now

        def advance(self, seconds):
            self.now += seconds

    return Clock()


@pytest.fixture()
def library(store, clock):
    return LibrarySearch(
        store,
        schema=SCHEMA,
        codec=CursorCodec(b"test-secret", ttl_seconds=900),
        clock=clock,
    )


def run(library, body=None, **kwargs):
    return library.run(SearchQuery.parse(body, SCHEMA), **kwargs)


def titles(result):
    return [document["name"] for document in result["documents"]]


# -- matching ---------------------------------------------------------------- #


def test_term_finds_documents_by_title_and_body(library):
    """Both match: one has "security" in its title, the other in its body."""
    result = run(library, {"term": "security"})

    assert sorted(titles(result)) == [
        "Enterprise Overview Deck",
        "Security and Compliance Pack",
    ]


def test_term_searches_the_description(library):
    result = run(library, {"term": "asterisks", "options": {"searchFields": ["description"]}})

    assert titles(result) == ["Pricing One-Pager"]


def test_term_searches_the_body(library):
    result = run(library, {"term": "rate", "options": {"searchFields": ["body"]}})

    assert titles(result) == ["API Integration Guide"]


def test_a_body_only_token_is_not_found_when_body_is_excluded(library):
    """The same token, searched only in the name, finds nothing."""
    result = run(library, {"term": "rate", "options": {"searchFields": ["name"]}})

    assert result["totalCount"] == 0


def test_search_fields_restrict_where_the_term_is_looked_for(library):
    """A field the caller excluded is a field that cannot produce a hit."""
    result = run(library, {"term": "asterisks", "options": {"searchFields": ["name"]}})

    assert result["totalCount"] == 0


def test_term_searches_custom_properties(library):
    result = run(library, {"term": "emea", "options": {"searchFields": ["properties"]}})

    assert titles(result) == ["Security and Compliance Pack"]


def test_every_term_token_must_be_present(library):
    assert run(library, {"term": "security pricing"})["totalCount"] == 0


def test_empty_query_returns_everything(library):
    result = run(library, {})

    assert result["totalCount"] == 4
    assert result["continuationToken"] is None


def test_title_match_outranks_a_body_match(library):
    result = run(library, {"term": "security"})

    # Both documents match; the one with it in the title comes first.
    assert result["documents"][0]["name"] == "Security and Compliance Pack"
    assert result["documents"][1]["name"] == "Enterprise Overview Deck"
    assert result["documents"][1]["_matchedFields"] == ["body"]


def test_total_count_counts_the_whole_result_set_not_the_page(library):
    result = run(library, {"options": {"pageSize": 2}})

    assert result["totalCount"] == 4
    assert len(result["documents"]) == 2


def test_a_field_a_team_added_today_is_searchable_today(store, library):
    """No migration, no redeploy: a new field is queryable the moment it is stored."""
    store.create("document", {"title": "Regional Playbook", "seats": 12})

    assert run(library, {"term": "regional"})["totalCount"] == 1
    assert run(library, {"filter": {"field": "seats", "value": 12}})["totalCount"] == 1


# -- return fields ----------------------------------------------------------- #


def test_only_the_requested_return_fields_are_returned(library):
    result = run(
        library,
        {"term": "security", "options": {"returnFields": ["id", "name", "pages"]}},
    )

    assert set(result["documents"][0]) == {"id", "name", "pages", "_matchedFields"}
    assert result["documents"][0]["pages"] == 48


def test_repository_is_synthesised_from_the_query(library):
    result = run(library, {"term": "security", "options": {"returnFields": ["repository", "name"]}})

    assert result["documents"][0]["repository"] == "library"
    assert run(library, {"repository": "WorkSpace", "options": {"returnFields": ["repository"]}})[
        "documents"
    ][0]["repository"] == "WorkSpace"


def test_missing_optional_fields_come_back_as_null(library):
    """Google-style documents have no downloadUrl; the field is still present."""
    store = library.store
    store.create("document", {"title": "Slide Deck", "kind": "gslide"})

    result = run(
        library,
        {"term": "slide", "options": {"returnFields": ["id", "name", "downloadUrl", "publishDate"]}},
    )

    assert result["documents"][0]["downloadUrl"] is None


def test_a_ten_second_asset_url_expiry_is_reported_with_every_result(library):
    """The researched pre-signed URLs last a day; say so on every response."""
    result = run(library, {"term": "security"})

    assert result["assetUrlTtlDays"] == 1
    assert result["assetUrlsExpireAt"]


# -- filters ----------------------------------------------------------------- #


def test_equality_filter(library):
    result = run(library, {"filter": {"field": "profile", "value": "pack"}})

    assert titles(result) == ["Security and Compliance Pack"]


def test_in_filter(library):
    result = run(
        library, {"filter": {"field": "profile", "operator": "in", "value": ["guide", "onepager"]}}
    )

    assert sorted(titles(result)) == ["API Integration Guide", "Pricing One-Pager"]


def test_custom_property_filter(library):
    result = run(library, {"filter": {"field": "custom.Region", "value": "APAC"}})

    assert sorted(titles(result)) == ["Enterprise Overview Deck", "Pricing One-Pager"]


def test_numeric_range_filter(library):
    result = run(library, {"filter": {"field": "pages", "operator": "greaterThan", "value": 30}})

    assert sorted(titles(result)) == ["API Integration Guide", "Security and Compliance Pack"]


def test_date_range_filter_compares_rfc3339_style_text(library):
    result = run(
        library,
        {
            "filter": {
                "and": [
                    {"field": "publishDate", "operator": "greaterThanOrEqual", "value": "2026-03-01"}
                ]
            }
        },
    )

    assert sorted(titles(result)) == ["Pricing One-Pager", "Security and Compliance Pack"]


def test_and_group_intersects(library):
    result = run(
        library,
        {
            "filter": {
                "and": [
                    {"field": "custom.Region", "value": "APAC"},
                    {"field": "kind", "value": "pdf"},
                ]
            }
        },
    )

    assert titles(result) == ["Pricing One-Pager"]


def test_or_group_unions(library):
    result = run(
        library,
        {
            "filter": {
                "or": [
                    {"field": "profile", "value": "pack"},
                    {"field": "profile", "value": "guide"},
                ]
            }
        },
    )

    assert sorted(titles(result)) == ["API Integration Guide", "Security and Compliance Pack"]


def test_a_filter_that_matches_nothing_returns_an_empty_page_not_an_error(library):
    result = run(library, {"filter": {"field": "profile", "value": "nothing-here"}})

    assert result["totalCount"] == 0
    assert result["documents"] == []
    assert result["continuationToken"] is None


def test_filter_narrows_a_term_search(library):
    result = run(
        library,
        {
            "term": "security",
            "filter": {"field": "custom.Region", "value": "AMER"},
        },
    )

    assert result["totalCount"] == 0


def test_a_filter_evaluates_the_same_way_however_it_is_written(library):
    """One evaluation path, so nesting cannot change the answer.

    An earlier version short-circuited `equal` through the dynamic index, which
    stores numbers numerically and text as text. A filter comparing a numeric
    field against a string value then found nothing while the in-memory
    comparison found a match, and the two forms of the same filter disagreed.
    """
    as_number = run(library, {"filter": {"field": "pages", "value": 48}})
    as_text = run(library, {"filter": {"field": "pages", "value": "48"}})
    nested = run(
        library,
        {"filter": {"and": [{"field": "kind", "value": "pdf"}, {"field": "pages", "value": "48"}]}},
    )

    assert titles(as_number) == ["Security and Compliance Pack"]
    assert titles(as_text) == titles(as_number)
    assert titles(nested) == titles(as_number)


def test_soft_deleted_library_records_are_not_searchable(store, library):
    doomed = store.create("document", {"title": "Retired Deck"})["id"]
    store.delete(doomed)

    assert run(library, {"term": "retired"})["totalCount"] == 0


# -- sort -------------------------------------------------------------------- #


def test_sort_by_a_field_ascending(library):
    result = run(library, {"sort": [{"field": "pages", "direction": "asc"}]})

    assert titles(result) == [
        "Pricing One-Pager",
        "Enterprise Overview Deck",
        "API Integration Guide",
        "Security and Compliance Pack",
    ]


def test_sort_by_an_unmapped_field_a_team_added(store, library):
    store.create("document", {"title": "Alpha", "priority": 1})
    store.create("document", {"title": "Beta", "priority": 9})

    result = run(library, {"filter": {"field": "priority", "operator": "greaterThan", "value": 0},
                           "sort": [{"field": "priority", "direction": "asc"}]})

    assert titles(result) == ["Alpha", "Beta"]


def test_sort_does_not_crash_on_mixed_types(store, library):
    """A sort field is schema-flexible, so one record may hold a number and
    another a label. That must degrade to a stable order, not a 500."""
    store.create("document", {"title": "Numeric", "score": 3})
    store.create("document", {"title": "Textual", "score": "high"})

    result = run(
        library,
        {
            "filter": {"field": "score", "operator": "in", "value": [3, "high"]},
            "sort": [{"field": "score", "direction": "desc"}],
        },
    )

    assert sorted(titles(result)) == ["Numeric", "Textual"]


def test_records_missing_a_sort_field_sink_rather_than_leading(store, library):
    store.create("document", {"title": "Scored", "rank": 1})
    store.create("document", {"title": "Unscored"})

    result = run(
        library,
        {
            "filter": {"field": "rank", "operator": "greaterThanOrEqual", "value": 0},
            "sort": [{"field": "rank", "direction": "asc"}],
        },
    )

    assert titles(result) == ["Scored"]


def test_equal_scores_break_ties_deterministically(library):
    first = run(library, {"term": "the"})["documents"]
    second = run(library, {"term": "the"})["documents"]

    assert [d["id"] for d in first] == [d["id"] for d in second]


# -- paging ------------------------------------------------------------------ #


def test_paging_walks_the_whole_result_set_without_repeating(library):
    seen: list[str] = []
    token = None

    while True:
        result = run(library, {"options": {"pageSize": 3}}, continuation_token=token)
        seen.extend(document["id"] for document in result["documents"])
        token = result["continuationToken"]
        if not token:
            break

    assert len(seen) == 4
    assert len(set(seen)) == 4


def test_the_last_page_has_no_continuation_token(library):
    result = run(library, {"options": {"pageSize": 10}})

    assert result["continuationToken"] is None


def test_page_size_zero_returns_a_count_but_no_documents(library):
    result = run(library, {"options": {"pageSize": 0}})

    assert result["totalCount"] == 4
    assert result["documents"] == []


def test_an_expired_token_is_rejected_and_tells_the_caller_to_start_again(library, clock):
    first = run(library, {"options": {"pageSize": 2}})
    clock.advance(901)

    with pytest.raises(SearchError) as excinfo:
        run(library, {"options": {"pageSize": 2}}, continuation_token=first["continuationToken"])

    assert str(excinfo.value) == "continuationToken is invalid or expired. Please regenerate it."


def test_a_token_cannot_be_replayed_against_a_different_query(library):
    """Otherwise a caller would silently get a page of the wrong documents."""
    token = run(library, {"options": {"pageSize": 2}})["continuationToken"]

    assert token

    with pytest.raises(SearchError, match="invalid or expired"):
        run(library, {"term": "security", "options": {"pageSize": 2}}, continuation_token=token)


def test_a_forged_token_is_rejected(library):
    with pytest.raises(SearchError, match="invalid or expired"):
        run(library, {}, continuation_token="bm90LWEtcmVhbC10b2tlbg.deadbeef")


# -- zero-hit broadening ----------------------------------------------------- #


def test_broadening_is_off_by_default(library):
    result = run(library, {"term": "securty"})

    assert result["totalCount"] == 0
    assert result["actualSearchTerm"] is None


def test_broadening_finds_a_near_miss_and_reports_the_term_it_used(library):
    result = run(
        library, {"term": "securty", "options": {"enableSuggestedQueryResults": True}}
    )

    assert result["actualSearchTerm"] == "security"
    assert "Security and Compliance Pack" in titles(result)


def test_broadening_is_skipped_when_the_term_already_matched(library):
    """Broadening only fires on zero hits; it must not rewrite a real answer."""
    result = run(
        library, {"term": "security", "options": {"enableSuggestedQueryResults": True}}
    )

    assert result["actualSearchTerm"] is None


def test_broadening_cannot_rescue_a_term_with_no_neighbour(library):
    result = run(library, {"term": "zzzzz", "options": {"enableSuggestedQueryResults": True}})

    assert result["totalCount"] == 0
    assert result["actualSearchTerm"] is None


# -- response envelope ------------------------------------------------------- #


def test_response_carries_timings_and_the_searched_collection(library):
    result = run(library, {"term": "security"})

    assert result["queryTimeInMs"] >= 0
    assert result["serviceTimeInMs"] >= 0
    assert result["searchedCollection"] == "document"
    assert result["scanned"] == 4


def test_client_details_are_echoed_back(library):
    result = run(library, {"term": "security"}, client_details={"application": "sales-room-ui"})

    assert result["clientDetails"] == {"application": "sales-room-ui"}


def test_searching_writes_nothing_to_the_audit_log(store, library):
    """Reads are not changes. A search must not drown the trail."""
    before = audit_count(store)

    run(library, {"term": "security"})

    assert audit_count(store) == before


# -- discovery --------------------------------------------------------------- #


def test_fields_in_use_reports_what_is_actually_there(library):
    paths = {entry["path"] for entry in library.fields_in_use()}

    assert "title" in paths
    assert "properties.Region" in paths
    assert all(entry["records"] > 0 for entry in library.fields_in_use())


def test_contract_reports_the_configured_token_lifetime(library):
    assert library.contract()["limits"]["continuationTokenTtlSeconds"] == 900


# -- assembly ---------------------------------------------------------------- #


@pytest.fixture()
def assembler(store):
    return LibraryAssembler(store)


@pytest.fixture()
def room(store):
    return store.create("room", {"name": "Northwind Evaluation"}, actor="dana")["id"]


def test_assemble_attaches_documents_to_the_room(store, assembler, room):
    deck = next(d for d in scan(store, "document") if d["data"]["title"] == "Enterprise Overview Deck")

    result = assembler.assemble(
        room, [{"id": deck["id"], "name": deck["data"]["title"]}], source=SOURCE, actor="dana"
    )

    assert result["added_count"] == 1
    assert result["skipped_count"] == 0
    attached = scan(store, "room_content", room_id=room)
    assert attached[0]["data"]["content_id"] == deck["id"]
    assert attached[0]["data"]["name"] == "Enterprise Overview Deck"


def test_assembling_the_same_document_twice_skips_it_rather_than_duplicating(
    store, assembler, room
):
    document = scan(store, "document")[0]
    assembler.assemble(room, [{"id": document["id"]}], source=SOURCE)

    second = assembler.assemble(room, [{"id": document["id"]}], source=SOURCE)

    assert second["added_count"] == 0
    assert second["skipped"] == [document["id"]]
    assert len(scan(store, "room_content", room_id=room)) == 1


def test_duplicate_ids_in_one_request_are_added_once(store, assembler, room):
    document = scan(store, "document")[0]["id"]

    result = assembler.assemble(room, [{"id": document}, {"id": document}], source=SOURCE)

    assert result["added_count"] == 1
    assert result["skipped"] == [document]


def test_assembly_is_one_transaction_and_one_audit_row(store, assembler, room):
    before = audit_count(store)
    documents = scan(store, "document")

    assembler.assemble(room, [{"id": d["id"]} for d in documents], source=SOURCE, actor="dana")

    assert audit_count(store) - before == 1
    entry = store.audit(collection="room_content")[0]
    assert entry["action"] == "insert"
    assert entry["room_id"] == room


def test_assembly_records_where_it_came_from(store, assembler, room):
    document = scan(store, "document")[0]

    assembler.assemble(
        room,
        [{"id": document["id"]}],
        source=SOURCE,
        search_id="library_search_abc",
        client_details={"application": "sales-room-ui"},
    )

    data = scan(store, "room_content", room_id=room)[0]["data"]
    assert data["search_id"] == "library_search_abc"
    assert data["client_application"] == "sales-room-ui"


def test_assembly_carries_arbitrary_extra_fields_without_a_schema_change(
    store, assembler, room
):
    document = scan(store, "document")[0]

    assembler.assemble(
        room,
        [{"id": document["id"], "pinToTop": True, "teamNote": "ask about SSO"}],
        source=SOURCE,
    )

    data = scan(store, "room_content", room_id=room)[0]["data"]
    assert data["pinToTop"] is True
    assert data["teamNote"] == "ask about SSO"


def test_content_does_not_leak_between_rooms(store, assembler, room):
    other = store.create("room", {"name": "Contoso Review"})["id"]
    document = scan(store, "document")[0]

    assembler.assemble(room, [{"id": document["id"]}], source=SOURCE)

    assert scan(store, "room_content", room_id=other) == []


def test_assembling_into_a_missing_room_raises_rather_than_creating_one(store, assembler):
    with pytest.raises(KeyError):
        assembler.assemble("room_missing", [{"id": "document_x"}], source=SOURCE)

    assert count(store, "room_content") == 0


def test_an_item_without_an_id_is_rejected_before_anything_is_written(store, assembler, room):
    with pytest.raises(SearchError, match="needs an 'id'"):
        assembler.assemble(room, [{"name": "no id here"}], source=SOURCE)

    assert count(store, "room_content") == 0


def test_assemble_refuses_to_run_without_a_source(store, assembler, room):
    """The audit row must name a route, so there is no default to fall back on.

    A hard-coded default in the domain is how a feature ends up logging a path the
    app stopped serving, so the parameter is required rather than merely
    conventional.
    """
    with pytest.raises(TypeError):
        assembler.assemble(room, [{"id": scan(store, "document")[0]["id"]}])


# -- saved searches ---------------------------------------------------------- #


def test_saved_searches_round_trip(store):
    from dsr.search import SavedSearches

    saved = SavedSearches(store, schema=SCHEMA)
    record = saved.save(
        "Security packs",
        {"term": "security", "filter": {"field": "profile", "value": "pack"}},
        source=SEARCH_SOURCE,
    )

    assert record["data"]["name"] == "Security packs"
    assert record["data"]["query"]["term"] == "security"
    assert saved.get(record["id"])["id"] == record["id"]
    assert [r["id"] for r in saved.list()] == [record["id"]]


def test_saving_validates_the_query_so_it_can_always_be_re_run(store):
    from dsr.search import SavedSearches

    saved = SavedSearches(store, schema=SCHEMA)

    with pytest.raises(SearchError):
        saved.save("Broken", {"term": "a" * 200}, source=SEARCH_SOURCE)

    assert count(store, "library_search") == 0


def test_a_saved_search_carries_extra_fields(store):
    from dsr.search import SavedSearches

    saved = SavedSearches(store, schema=SCHEMA)
    record = saved.save(
        "Owned", {"term": "deck", "ticket": "JIRA-42"}, source=SEARCH_SOURCE
    )

    assert record["data"]["ticket"] == "JIRA-42"


def test_a_saved_search_audits_the_source_the_caller_named(store):
    """The audit row records the path that served the write, not a baked-in one."""
    from dsr.search import SavedSearches

    saved = SavedSearches(store, schema=SCHEMA)
    record = saved.save("Owned", {"term": "deck"}, source=SEARCH_SOURCE)

    entry = store.audit(collection="library_search")[0]
    assert entry["source"] == SEARCH_SOURCE
    assert entry["record_id"] == record["id"]


def test_save_refuses_to_run_without_a_source(store):
    from dsr.search import SavedSearches

    saved = SavedSearches(store, schema=SCHEMA)

    with pytest.raises(TypeError):
        saved.save("No source", {"term": "deck"})


# -- the reconstructed reads -------------------------------------------------- #
#
# ``dsr.search.reads`` is new code in this port, standing in for three methods the
# branch added to the shared ``dsr/store.py``. It is the part of the port most
# likely to be wrong quietly, so it is pinned directly.


def test_scan_returns_every_live_record_in_id_order(store):
    found = scan(store, "document")

    assert [record["id"] for record in found] == sorted(r["id"] for r in found)
    assert len(found) == 4


def test_scan_pages_past_the_store_per_call_ceiling(tmp_path):
    """The store clamps a list call to 1000 rows; scan must not inherit that cap.

    This is the whole reason ``scan`` is a loop rather than a one-liner, so it is
    checked at more than one page rather than assumed.
    """
    db = AuditedDatabase(tmp_path / "big.db", mirror_dir=tmp_path / "mirror")
    try:
        big = RecordStore(db)
        total = 1_050
        items = [{"title": f"Bulk Deck {number}"} for number in range(total)]
        for start in range(0, total, 500):
            big.bulk_create(
                "document", items[start : start + 500], actor="seed", source="test"
            )

        assert count(big, "document") == total
        assert len(scan(big, "document")) == total
        # Still in id order across the page boundary, which is what makes an
        # offset paging cursor safe over the result.
        ids = [record["id"] for record in scan(big, "document")]
        assert ids == sorted(ids)
    finally:
        db.close()


def test_scan_hides_soft_deleted_records_unless_asked(store):
    doomed = store.create("document", {"title": "Retired Deck"})["id"]
    store.delete(doomed)

    assert doomed not in {r["id"] for r in scan(store, "document")}
    assert doomed in {r["id"] for r in scan(store, "document", include_deleted=True)}


def test_scan_scopes_to_one_room(store):
    first = store.create("room", {"name": "Northwind"})["id"]
    second = store.create("room", {"name": "Contoso"})["id"]
    store.bulk_create("room_content", [{"content_id": "a"}, {"content_id": "b"}], room_id=first, source="test")
    store.bulk_create("room_content", [{"content_id": "c"}], room_id=second, source="test")

    assert count(store, "room_content", room_id=first) == 2
    assert count(store, "room_content", room_id=second) == 1
    assert count(store, "room_content") == 3


def test_audit_count_honours_the_same_filters_as_the_audit_log(store):
    # The fixture already bulk-created the library, so this asserts on the
    # delta rather than an absolute the fixture would have to know about.
    base = audit_count(store)
    store.create("room", {"name": "Northwind"}, source="test")
    store.create("room", {"name": "Contoso"}, source="test")

    assert audit_count(store) - base == 2
    assert audit_count(store, collection="room") == 2
    assert audit_count(store, collection="document") == base
    assert audit_count(store, action="delete") == 0


def test_the_reads_never_write_to_the_audit_log(store):
    """A read is not a change, so scanning must leave the trail alone."""
    before = audit_count(store)

    scan(store, "document")
    count(store, "document")
    audit_count(store)

    assert audit_count(store) == before
