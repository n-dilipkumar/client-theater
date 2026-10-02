"""Tests for WF-037: log a single buyer engagement event into the CRM.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-037.md``. This is a **build**, not a
port, so there is no branch to compare against: the specification is the research document,
and the researched sentence each test pins is quoted in the test that pins it.

Eight groups, in the order a reviewer would want them:

* **Vocabulary** - the three create surfaces, their success codes with the quotation
  behind each, where each vendor returns the new row's id, the ``Prefer`` tokens, and the
  queue states. This is the researched half, so it is the part that must not drift.
* **Transforms** - every named transform, its alias spellings, and the two classes of
  finding it can raise: soft, which still ships, and hard, which keeps the value off the
  wire.
* **Resolution and mapping** - canonical fields, synonym fall-through, the sync key, and the
  rule that an unresolved source is omitted rather than sent as null.
* **Payloads** - the per-vendor URL, body shape, headers, preference tokens, and the
  guarantee that a connector's token never appears in a recorded request.
* **Id extraction** - HubSpot's body, Dataverse's ``OData-EntityId`` header (the case a
  body-only parser cannot handle), Salesforce's unsourced guess, and the empty result that
  turns a vendor-accepted create into a failure needing a human.
* **Delivery** - the retry ladder, the 409 exclusion, the per-attempt record, and the
  per-vendor success test inside the retry loop.
* **The five researched steps** - record then enqueue, resolve, map, create, settle; plus
  every named block reason, and the promise that a blocked row is re-evaluated.
* **HTTP, audit and demo data** - every route through this feature's own router, the six
  error statuses, the guarantee that reads never write, the audit-source rule, and the
  states the seeder produces.

Three of these are regression tests for defects found while building, and they say so in
their names, because each one produced a Sync log that was confidently wrong:

``test_a_body_id_is_read_as_a_value_not_as_a_boolean``
    The extractor asked "is this path present?" and then returned the answer instead of the
    value, so every HubSpot create stored the literal string ``"True"`` as its
    ``crm_record_id``. The log looked perfect and the id was unusable.
``test_a_room_scoped_connector_is_not_read_as_unscoped``
    ``room_id`` is a column on the record, not a field in its payload, so a projection that
    copied only the payload made every room-scoped connector look like the installation
    default. Room one then wrote its engagement to room two's CRM, and the demo's
    ``connector_disabled`` state became unreachable.
``test_the_feed_resolves_fields_the_way_the_field_map_does``
    The feed read ``dwell_seconds`` literally while the mapper resolved ``dwell`` by
    synonym, so a room whose rows used the synonym showed a dwell total of zero beside a
    create that had sent the right number.

The HTTP fixture keeps one application for the whole module and gives each test its
own in-memory database. The engine is built per request from a dependency, so
``app.dependency_overrides`` is the seam - there is nothing on ``app.state`` to replace,
which is the point of not editing the shared app.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from dsr.api import app
from dsr.crm_engagement import (
    BLOCK_REASONS,
    CREATE_ENDPOINTS,
    PREFERENCES,
    QUEUE_STATES,
    RECORD_ID_LOCATIONS,
    EngagementSync,
    EngagementSyncError,
    InvalidConnector,
    InvalidEventType,
    InvalidFieldMap,
    SyncBook,
    SyncNotConfigured,
    UnknownRoom,
    build_create,
    delivery,
    describe_inferences,
    describe_vocabulary,
    extract_record_id,
    map_event,
    normalise_connector,
    normalise_event_type,
    normalise_field_map,
    payloads,
    post_create,
    queue as queue_module,
    success_codes,
)
from dsr.crm_engagement.delivery import CreateResult
from dsr.crm_engagement.engine import IDENTITY_SOURCES
from dsr.crm_engagement.inferences import INFERENCES
from dsr.crm_engagement.mapping import (
    HARD_FINDINGS,
    SOFT_FINDINGS,
    TRANSFORMS,
    as_number,
    as_text,
    canonical_transform,
    describe_transforms,
    dotted,
    read_source,
    run_transform,
)
from dsr.crm_engagement.payloads import (
    error_detail,
    explain_preferences,
    id_from_entity_id,
    preference_list,
)
from dsr.crm_engagement.vocabulary import FIELD_SYNONYMS, RETRYABLE_STATUS, SOURCED_QUOTES
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore
from fastapi.testclient import TestClient

#: The feature's own prefix. Duplicated rather than imported so renaming the route fails
#: here instead of following silently - which is what a test is for.
PREFIX = "/api/wf-037"

MODULE_NAME = "wf037_log_a_single_buyer_engagement_event_in"

#: What the pure-domain tests pass as ``source``: deliberately the exact shape a route
#: passes, so a test asserting on an audit row is asserting on the real thing.
SOURCE = f"POST {PREFIX}/rooms/room_1/engagements"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


class Scripted:
    """A transport that answers from a fixed script, in order.

    Counts its calls, so a test can assert how many attempts a retry ladder actually made
    rather than trusting a hand-written ``attempts: 2``.
    """

    def __init__(self, *results: CreateResult) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, body: bytes, headers: Any, timeout: float) -> CreateResult:
        self.calls.append(
            {
                "url": url,
                "body": json.loads(body) if body else None,
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        if not self.results:
            raise AssertionError(f"transport called more than {len(self.calls)} times")
        return self.results.pop(0)

    @property
    def count(self) -> int:
        return len(self.calls)


def ok(status: int, body: str = "", headers: dict[str, str] | None = None) -> CreateResult:
    return CreateResult(ok=True, status=status, body=body, headers=headers or {}, duration_ms=1.0)


def bad(status: int | None, body: str = "", error: str | None = None) -> CreateResult:
    return CreateResult(
        ok=False, status=status, body=body, error=error or f"HTTP {status}", duration_ms=1.0
    )


@pytest.fixture()
def store():
    # In-memory rather than a file on disk: 0.4 ms against 7.0 ms, measured. No test
    # in this file reads the audit mirror off the filesystem, so the file bought nothing.
    db = AuditedDatabase()
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def book(store):
    return SyncBook(store)


@pytest.fixture()
def room(store):
    return store.create(
        "room",
        {"name": "Northwind", "account": "Northwind Traders", "owner": "dana"},
        source="seed",
    )


@pytest.fixture()
def other_room(store):
    return store.create(
        "room", {"name": "Contoso", "account": "Contoso Health", "owner": "sam"}, source="seed"
    )


@pytest.fixture()
def engine(store):
    return EngagementSync(store, backoff=0, sleep=lambda _s: None)


def connector_spec(**overrides: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "vendor": "hubspot",
        "label": "Northwind HubSpot",
        "base_url": "https://api.hubapi.test",
        "object": "contacts",
    }
    spec.update(overrides)
    return spec


def map_spec(event_type: str, connector_id: str, **overrides: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "event_type": event_type,
        "connector_id": connector_id,
        "fields": [
            {"source": "type", "target": "engagement_type"},
            {"source": "asset", "target": "asset_name"},
            {"source": "buyer_email", "target": "email", "transform": "email.normalize"},
        ],
        "sync_key": {"source": "id", "target_property": "dsr_engagement_id"},
    }
    spec.update(overrides)
    return spec


def event_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "document_viewed",
        "occurred_at": "2026-09-24T08:14:00+00:00",
        "asset": "Enterprise Overview Deck",
        "dwell_seconds": 248,
        "buyer_email": "procurement@northwind.example",
    }
    payload.update(overrides)
    return payload


def configured(
    engine: EngagementSync, room_id: str, event_type: str = "document_viewed", **overrides: Any
):
    """A connector, a catalogue row and a field map, so a create can actually be sent."""
    engine.register_connector(
        connector_spec(**overrides.pop("connector", {})), room_id=room_id, source="seed"
    )
    connector_id = engine.connectors()[-1]["id"]
    engine.add_event_type({"event_type": event_type}, source="seed")
    engine.add_field_map(map_spec(event_type, connector_id, **overrides), source="seed")
    return connector_id


# --------------------------------------------------------------------------- #
# Vocabulary: the researched contract
# --------------------------------------------------------------------------- #


def test_the_three_vendors_are_the_ones_the_research_names():
    assert describe_vocabulary()["vendors"] == ["hubspot", "dataverse", "salesforce"]


def test_hubspot_posts_to_the_documented_object_endpoint():
    """[sourced] "To create one contact, make a `POST` request to `/crm/v3/objects/contacts`."""
    spec = CREATE_ENDPOINTS["hubspot"]
    assert spec["path_template"] == "/crm/v3/objects/{object}"
    assert spec["body_style"] == "properties_object"
    assert spec["object_example"] == "contacts"


def test_dataverse_posts_to_the_entity_set_under_the_documented_version():
    spec = CREATE_ENDPOINTS["dataverse"]
    assert spec["path_template"] == "/api/data/v9.2/{entity_set}"
    assert spec["body_style"] == "field_body"
    assert spec["object_field"] == "entity_set"


def test_the_dataverse_target_is_the_entity_set_not_the_table_name():
    """[sourced] "Select **Copy set name** to copy the entity set name for the table."

    So the connector is keyed on the entity *set*, and the vocabulary says so in words
    rather than leaving a reader to infer it from the field name.
    """
    assert "Copy set name" in SOURCED_QUOTES["dataverse_entity_set"]
    assert "entity *set*" in CREATE_ENDPOINTS["dataverse"]["object_note"]


def test_salesforce_posts_to_a_sobjects_path():
    spec = CREATE_ENDPOINTS["salesforce"]
    assert spec["path_template"].startswith("/services/data/vXX.X/sobjects/")
    assert spec["body_style"] == "field_body"


def test_dataverse_counts_exactly_201_and_204_as_a_successful_create():
    """[sourced] "a successful response has status 201 Created. … Without this preference,
    both operations return status 204 No Content."

    Exactly those two. Widening the set would make a 200 look like a create that happened.
    """
    assert success_codes("dataverse") == (201, 204)
    assert CREATE_ENDPOINTS["dataverse"]["sourced_success"] is True


def test_salesforce_does_not_count_204_as_a_successful_create():
    """[sourced] "`204` — 'No Content' success code, for DELETE requests and some PATCH
    requests." 204 is a success code in general and explicitly not a create success here."""
    assert success_codes("salesforce") == (201,)
    assert 204 not in success_codes("salesforce")


def test_hubspot_counts_any_2xx_and_says_that_no_code_was_quoted():
    assert success_codes("hubspot") == tuple(range(200, 300))
    assert CREATE_ENDPOINTS["hubspot"]["sourced_success"] is False
    assert "no status code" in CREATE_ENDPOINTS["hubspot"]["success_basis"]


def test_the_three_success_policies_are_genuinely_different():
    """The point of reading the research rather than guessing: one rule per vendor."""
    assert success_codes("hubspot") != success_codes("dataverse") != success_codes("salesforce")


def test_the_hubspot_id_is_read_from_the_body():
    """[sourced] "response returns the new record id (HubSpot `id`)"."""
    first = RECORD_ID_LOCATIONS["hubspot"][0]
    assert first == {
        "where": "body",
        "path": "id",
        "sourced": True,
        "basis": first["basis"],
    }


def test_the_dataverse_id_is_read_from_the_odata_entity_id_header():
    """[sourced] "`HTTP/1.1 204 No Content` … `OData-EntityId: [Organization URI]/api/data/v9.2/accounts(00aa00aa-…)`"

    A header, not a body - and on a 204 there is no body. The order matters: the header is
    tried first precisely because it is the only place the research shows the id.
    """
    first = RECORD_ID_LOCATIONS["dataverse"][0]
    assert first["where"] == "header"
    assert first["name"] == "OData-EntityId"
    assert first["sourced"] is True


def test_the_dataverse_body_fallback_is_marked_as_a_guess():
    second = RECORD_ID_LOCATIONS["dataverse"][1]
    assert second["where"] == "body"
    assert second["sourced"] is False


def test_the_salesforce_id_location_is_declared_unsourced():
    """[sourced gap] "the Salesforce claim rests on the status-code reference plus the
    general resource model, not a `sobjects` create page"."""
    assert RECORD_ID_LOCATIONS["salesforce"][0]["sourced"] is False
    assert "Not sourced" in RECORD_ID_LOCATIONS["salesforce"][0]["basis"]


def test_return_representation_is_documented_as_changing_204_to_201():
    preference = PREFERENCES["return=representation"]
    assert preference["vendors"] == ["dataverse"]
    assert "204 No Content to 201 Created" in preference["effect"]


def test_the_annotations_preference_is_documented_as_error_detail():
    """[sourced] "Dataverse `Prefer: odata.include-annotations` for enriched error detail"."""
    preference = PREFERENCES["odata.include-annotations"]
    assert "error" in preference["effect"]
    assert "annotations" in preference["effect"]


def test_respond_async_is_named_but_not_promised_a_job_poll():
    preference = PREFERENCES["respond-async"]
    assert "no completion poll" in preference["effect"]


def test_every_queue_row_ends_in_one_of_four_states():
    assert QUEUE_STATES == ("pending", "synced", "failed", "blocked")


def test_every_block_reason_is_a_sentence_a_rep_can_act_on():
    for reason, text in BLOCK_REASONS.items():
        assert text.endswith("."), reason
        assert len(text) > 30, reason


def test_every_reason_ships_with_a_tone_so_a_page_need_not_guess():
    """The presentation of a reason is server-owned, so a reason added later arrives with
    its own rather than falling back to whatever a client hard-coded."""
    vocabulary = describe_vocabulary()
    tones = {**vocabulary["block_tones"], **vocabulary["failure_tones"]}
    for reason in list(BLOCK_REASONS) + list(vocabulary["failure_reasons"]):
        assert tones.get(reason) in ("info", "warn", "danger", "good"), reason


def test_a_reason_with_no_tone_of_its_own_falls_back_to_informational():
    assert describe_vocabulary()["failure_tones"]["crm_id_absent"] == "danger"
    assert describe_vocabulary()["block_tones"]["event_type_unmapped"] == "warn"


def test_the_failure_reasons_include_the_two_the_research_creates():
    assert "crm_id_absent" in describe_vocabulary()["failure_reasons"]
    assert "sync_key_collision" in describe_vocabulary()["failure_reasons"]


def test_the_canonical_fields_are_the_ones_the_data_flow_names():
    """[sourced] "sales-room event (type, timestamp, asset, dwell time, buyer identity)"."""
    assert set(describe_vocabulary()["canonical_fields"]) == {
        "type",
        "occurred_at",
        "asset",
        "dwell_seconds",
        "buyer_email",
        "buyer_crm_id",
    }


def test_the_sourced_quotes_are_the_documents_own_words():
    for key, quote in SOURCED_QUOTES.items():
        assert isinstance(quote, str) and quote.strip(), key
    assert "OData-EntityId" in SOURCED_QUOTES["dataverse_created_uri"]
    assert "/crm/v3/objects/contacts" in SOURCED_QUOTES["hubspot_contacts"]


def test_the_vocabulary_names_the_two_surfaces_the_research_names():
    """[sourced] "Room **Analytics / Engagement feed**; room **Sync log / Errors** admin panel"."""
    assert describe_vocabulary()["surfaces"] == ["engagement_feed", "sync_log"]


# --------------------------------------------------------------------------- #
# Transforms
# --------------------------------------------------------------------------- #


def test_every_transform_named_by_the_research_is_registered():
    assert set(TRANSFORMS) == {
        "identity",
        "email.normalize",
        "date.iso8601",
        "picklist.map",
        "number",
    }


def test_transforms_carry_a_version():
    for name, entry in TRANSFORMS.items():
        assert entry["version"] >= 1, name


def test_the_registry_is_served_with_labels_and_aliases():
    rows = {row["name"]: row for row in describe_transforms()}
    assert rows["email.normalize"]["aliases"] == ["email", "lowercase_email"]
    assert rows["picklist.map"]["requires"] == "options"


def test_the_alias_spellings_all_reach_the_same_transform():
    assert canonical_transform("Email") == "email.normalize"
    assert canonical_transform("lowercase_email") == "email.normalize"
    assert canonical_transform("iso8601") == "date.iso8601"
    assert canonical_transform("picklist") == "picklist.map"
    assert canonical_transform("number_coerce") == "number"
    assert canonical_transform("none") == "identity"
    assert canonical_transform("") == "identity"
    assert canonical_transform(None) == "identity"


def test_an_unregistered_transform_name_is_returned_so_a_deployment_can_add_one():
    """[sourced] "a deployment can add a transform without touching the sync engine"."""
    assert canonical_transform("acme.censor") == "acme.censor"
    result = run_transform("acme.censor", "value")
    assert result.value is None
    assert result.findings[0]["code"] == "transform_error"
    assert "not a transform this build registers" in result.findings[0]["detail"]


def test_a_transform_that_raises_becomes_a_finding_rather_than_an_exception():
    def explode(value, spec):
        raise RuntimeError("boom")

    original = TRANSFORMS["number"]["run"]
    try:
        TRANSFORMS["number"]["run"] = explode
        result = run_transform("number", 1)
    finally:
        TRANSFORMS["number"]["run"] = original
    assert result.findings[0]["code"] == "transform_error"
    assert "RuntimeError: boom" in result.findings[0]["detail"]


def test_identity_passes_a_value_through():
    assert run_transform("identity", "Deck").value == "Deck"


def test_identity_treats_an_empty_value_as_a_hard_finding():
    result = run_transform("identity", "   ")
    assert result.value is None
    assert result.findings[0]["code"] == "no_value"
    assert not result.usable


def test_email_normalize_lowercases_and_trims():
    assert run_transform("email.normalize", "  Procurement@Northwind.Example ").value == (
        "procurement@northwind.example"
    )


def test_email_without_an_at_sign_is_a_soft_finding_and_still_ships():
    """A CRM that rejects it will say so itself, in a better place than a guess made here."""
    result = run_transform("email.normalize", "procurement (northwind)")
    assert result.usable is True
    assert result.value == "procurement (northwind)"
    assert [f["code"] for f in result.findings] == ["email_without_at"]
    assert "email_without_at" in SOFT_FINDINGS
    assert "email_without_at" not in HARD_FINDINGS


def test_iso8601_accepts_a_z_suffixed_timestamp_and_normalises_to_utc():
    assert (
        run_transform("date.iso8601", "2026-09-24T08:14:00Z").value == "2026-09-24T08:14:00+00:00"
    )


def test_iso8601_shifts_an_offset_timestamp_to_utc():
    assert (
        run_transform("date.iso8601", "2026-09-24T10:14:00+02:00").value
        == "2026-09-24T08:14:00+00:00"
    )


def test_iso8601_flags_a_naive_timestamp_without_inventing_a_timezone():
    result = run_transform("date.iso8601", "2026-09-24T08:14:00")
    assert result.usable is True
    assert [f["code"] for f in result.findings] == ["naive_timestamp"]
    assert "no UTC offset" in result.findings[0]["detail"]


def test_iso8601_refuses_a_bare_number_rather_than_guessing_seconds_or_milliseconds():
    result = run_transform("date.iso8601", 1750000000)
    assert result.usable is False
    assert "not in the row" in result.findings[0]["detail"]


def test_iso8601_refuses_a_string_that_is_not_a_timestamp():
    result = run_transform("date.iso8601", "last tuesday")
    assert result.usable is False
    assert "not an ISO-8601 timestamp" in result.findings[0]["detail"]


def test_iso8601_accepts_a_date_object():
    from datetime import date, datetime, timezone

    assert run_transform("date.iso8601", date(2026, 9, 24)).value == "2026-09-24T00:00:00"
    aware = datetime(2026, 9, 24, 8, 14, tzinfo=timezone.utc)
    assert run_transform("date.iso8601", aware).value == "2026-09-24T08:14:00+00:00"


def test_picklist_map_translates_a_label_to_the_internal_value():
    spec = {"options": {"Evaluation": "opportunity", "Discovery": "lead"}}
    assert run_transform("picklist.map", "Evaluation", spec).value == "opportunity"


def test_picklist_map_matches_a_label_case_insensitively_and_trimmed():
    spec = {"options": {"Evaluation": "opportunity"}}
    assert run_transform("picklist.map", "  evaluation ", spec).value == "opportunity"


def test_picklist_map_accepts_a_list_of_label_value_rows():
    spec = {"options": [{"label": "Evaluation", "value": "opportunity"}]}
    assert run_transform("picklist.map", "Evaluation", spec).value == "opportunity"


def test_picklist_map_omits_a_label_that_is_not_in_the_table():
    """[sourced] the room "flags ... unsupported option values before any data is written"."""
    spec = {"options": {"Evaluation": "opportunity"}}
    result = run_transform("picklist.map", "Renewal", spec)
    assert result.usable is False
    assert result.findings[0]["code"] == "unmapped_option"
    assert "not in the option table" in result.findings[0]["detail"]


def test_picklist_map_passes_an_internal_value_through_untouched():
    spec = {"options": {"Evaluation": "opportunity"}}
    assert run_transform("picklist.map", "opportunity", spec).value == "opportunity"


def test_picklist_map_with_no_table_at_all_passes_the_value_through():
    assert run_transform("picklist.map", "anything", {"options": {}}).value == "anything"


def test_number_coerces_a_numeric_string():
    assert run_transform("number", "248").value == 248
    assert run_transform("number", "1,024").value == 1024
    assert run_transform("number", 12.5).value == 12.5


def test_number_keeps_an_integer_an_integer():
    """A CRM numeric column distinguishes the two, and 3.0 is a silent type change."""
    assert isinstance(run_transform("number", "3").value, int)
    assert isinstance(run_transform("number", "3.5").value, float)


def test_number_refuses_a_boolean_rather_than_sending_one():
    result = run_transform("number", True)
    assert result.usable is False
    assert result.findings[0]["code"] == "not_a_number"
    assert "boolean" in result.findings[0]["detail"]


def test_number_refuses_a_non_numeric_string():
    result = run_transform("number", "about four minutes")
    assert result.usable is False
    assert result.findings[0]["code"] == "not_a_number"


def test_hard_and_soft_findings_are_disjoint_and_exhaustive_for_these_transforms():
    assert not (HARD_FINDINGS & SOFT_FINDINGS)
    assert {"required_field_omitted", "sync_key_unresolved"} <= HARD_FINDINGS


def test_the_two_blocking_findings_are_hard_and_the_default_is_soft():
    """A finding whose severity says "soft" on a blocking problem is a lie in a column."""
    field_map = normalise_field_map(
        map_spec(
            "document_viewed",
            "c",
            fields=[{"source": "nope", "target": "wanted", "required": True}],
        )
    )
    severities = {
        finding["code"]: finding["severity"] for finding in map_event(_event(), field_map).findings
    }
    assert severities["required_field_omitted"] == "hard"

    keyless = normalise_field_map(map_spec("document_viewed", "c"))
    severities = {
        finding["code"]: finding["severity"]
        for finding in map_event({"id": "", "data": {}}, keyless).findings
    }
    assert severities["sync_key_unresolved"] == "hard"

    defaulted = normalise_field_map(
        map_spec(
            "document_viewed", "c", fields=[{"source": "nope", "target": "filled", "default": "x"}]
        )
    )
    codes = {
        finding["code"]: finding["severity"] for finding in map_event(_event(), defaulted).findings
    }
    assert codes["defaulted"] == "soft"


# --------------------------------------------------------------------------- #
# Tolerant readers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value,expected",
    [(None, ""), (True, "true"), (False, "false"), (3, "3"), (3.5, "3.5"), ("  x ", "x"), ({}, "")],
)
def test_as_text_is_total(value: Any, expected: str):
    assert as_text(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (3, 3.0),
        (3.5, 3.5),
        ("7", 7.0),
        ("1,024", 1024.0),
        (True, None),
        (False, None),
        ("x", None),
        (None, None),
    ],
)
def test_as_number_refuses_booleans_and_non_numbers(value: Any, expected: float | None):
    assert as_number(value) == expected


def test_dotted_reads_a_nested_path():
    assert dotted({"a": {"b": {"c": 1}}}, "a.b.c") == (True, 1)


def test_dotted_returns_not_found_rather_than_raising():
    assert dotted({"a": 1}, "a.b.c") == (False, None)
    assert dotted({"a": 1}, "") == (False, None)


def test_dotted_prefers_a_literal_key_over_walking_it():
    """A team's own field may legitimately be called ``buyer.email`` as a single key."""
    assert dotted({"buyer.email": "flat", "buyer": {"email": "nested"}}, "buyer.email") == (
        True,
        "flat",
    )


# --------------------------------------------------------------------------- #
# Resolution: the researched step 3's inputs
# --------------------------------------------------------------------------- #


def _event(**data: Any) -> dict[str, Any]:
    return {"id": "crm_engagement_abc", "room_id": "room_1", "data": data}


def test_the_envelope_is_readable_as_a_source():
    found, value, located = read_source(_event(), "id")
    assert (found, value, located) == (True, "crm_engagement_abc", "id")


def test_a_canonical_field_is_read_by_its_own_name():
    found, value, _ = read_source(_event(type="document_viewed"), "type")
    assert (found, value) == (True, "document_viewed")


def test_a_canonical_field_is_read_by_a_synonym_when_its_own_name_is_absent():
    """A team whose rows were written by a webhook should not have to reshape them."""
    found, value, located = read_source(_event(person="a@example.test"), "buyer_email")
    assert (found, value, located) == (True, "a@example.test", "person")


def test_the_exact_path_wins_over_a_synonym():
    found, value, located = read_source(
        _event(email="exact@x.test", person="synonym@x.test"), "buyer_email"
    )
    assert (found, value, located) == (True, "exact@x.test", "email")


def test_an_arbitrary_dotted_path_is_a_valid_source():
    found, value, _ = read_source(_event(meta={"campaign": {"id": 7}}), "meta.campaign.id")
    assert (found, value) == (True, 7)


def test_an_absent_source_reports_not_found_rather_than_none_as_a_value():
    found, value, _ = read_source(_event(), "asset")
    assert found is False
    assert value is None


def test_a_field_with_no_synonyms_never_resolves_through_guessing():
    assert FIELD_SYNONYMS["buyer_crm_id"] == (
        "buyer_contact_id",
        "crm_contact_id",
        "contact_id",
        "buyer_id",
        "external_id",
    )


def test_crm_record_id_is_not_a_synonym_for_the_buyer_s_id():
    """[sourced] the research gives ``crm_record_id`` to *this event's* CRM row.

    Treating it as another spelling of the buyer's id would let a field map send the
    engagement row's own id as the buyer's CRM record.
    """
    assert "crm_record_id" not in FIELD_SYNONYMS["buyer_crm_id"]
    found, value, _ = read_source(_event(crm_record_id="501"), "buyer_crm_id")
    assert found is False


def test_a_unit_variant_is_not_a_synonym():
    """``dwell_ms`` is a different number, not a different spelling of it."""
    assert "dwell_ms" not in FIELD_SYNONYMS["dwell_seconds"]
    found, value, _ = read_source(_event(dwell_ms=240_000), "dwell_seconds")
    assert found is False


def test_the_identity_sources_are_the_researchs_own_order():
    """[sourced] "buyer identity (CRM record id or email from W1/W2)" - an id is an answer,
    an email is a lookup, so the id is tried first."""
    assert IDENTITY_SOURCES == ("buyer_crm_id", "buyer_email")


# --------------------------------------------------------------------------- #
# Mapping: the researched step 3 and the mapping half of step 4
# --------------------------------------------------------------------------- #


def test_a_field_map_maps_every_outbound_field_and_injects_the_sync_key():
    field_map = normalise_field_map(map_spec("document_viewed", "conn_1"))
    mapped = map_event(_event(**event_payload()), field_map)
    assert mapped.properties == {
        "engagement_type": "document_viewed",
        "asset_name": "Enterprise Overview Deck",
        "email": "procurement@northwind.example",
        "dsr_engagement_id": "crm_engagement_abc",
    }
    assert mapped.sync_key == {
        "property": "dsr_engagement_id",
        "value": "crm_engagement_abc",
        "source": "id",
    }
    assert mapped.clean is True


def test_an_inbound_field_is_not_sent_on_a_create():
    """[sourced] each field has "its direction (in / out / both)"."""
    field_map = normalise_field_map(
        map_spec(
            "document_viewed",
            "conn_1",
            fields=[
                {"source": "type", "target": "sent"},
                {"source": "type", "target": "received", "direction": "in"},
                {"source": "type", "target": "both", "direction": "both"},
            ],
        )
    )
    mapped = map_event(_event(type="document_viewed"), field_map)
    assert "received" not in mapped.properties
    assert mapped.properties["sent"] == "document_viewed"
    assert mapped.properties["both"] == "document_viewed"


def test_an_unresolved_source_is_omitted_and_reported_rather_than_sent_as_null():
    """Sending null either fails the create or overwrites a real value with nothing."""
    field_map = normalise_field_map(map_spec("document_viewed", "conn_1"))
    mapped = map_event(_event(type="document_viewed"), field_map)
    assert "asset_name" not in mapped.properties
    assert "email" not in mapped.properties
    assert mapped.clean is False
    codes = {finding["code"] for finding in mapped.findings}
    assert codes == {"source_unresolved"}
    assert all(finding["severity"] == "hard" for finding in mapped.findings)


def test_one_unresolved_source_does_not_cost_the_other_fields():
    field_map = normalise_field_map(map_spec("document_viewed", "conn_1"))
    mapped = map_event(_event(type="document_viewed", buyer_email="a@b.test"), field_map)
    assert mapped.properties["engagement_type"] == "document_viewed"
    assert mapped.properties["email"] == "a@b.test"
    assert "asset_name" not in mapped.properties


def test_a_default_supplies_a_missing_value_and_says_so():
    field_map = normalise_field_map(
        map_spec(
            "document_viewed",
            "conn_1",
            fields=[{"source": "nope", "target": "filled", "default": "x"}],
        )
    )
    mapped = map_event(_event(), field_map)
    assert mapped.properties["filled"] == "x"
    assert [finding["code"] for finding in mapped.findings] == ["defaulted"]


def test_a_required_field_that_cannot_be_built_is_called_out_by_name():
    field_map = normalise_field_map(
        map_spec(
            "document_viewed",
            "conn_1",
            fields=[{"source": "nope", "target": "needed", "required": True}],
        )
    )
    mapped = map_event(_event(), field_map)
    codes = {finding["code"] for finding in mapped.findings}
    assert "required_field_omitted" in codes


def test_a_field_mapping_records_where_each_value_was_actually_read_from():
    field_map = normalise_field_map(map_spec("document_viewed", "conn_1"))
    mapped = map_event(_event(**event_payload()), field_map)
    assert mapped.located["asset_name"] == "asset"
    assert mapped.located["email"] == "buyer_email"


def test_a_soft_finding_still_ships_its_value():
    field_map = normalise_field_map(
        map_spec(
            "document_viewed",
            "conn_1",
            fields=[{"source": "buyer_email", "target": "email", "transform": "email.normalize"}],
        )
    )
    mapped = map_event(_event(buyer_email="procurement (contoso)"), field_map)
    assert mapped.properties["email"] == "procurement (contoso)"
    assert mapped.clean is False


def test_a_hard_finding_keeps_its_value_off_the_wire():
    field_map = normalise_field_map(
        map_spec(
            "document_viewed",
            "conn_1",
            fields=[
                {
                    "source": "buyer_email",
                    "target": "stage",
                    "transform": "picklist.map",
                    "options": {"Evaluation": "opportunity"},
                }
            ],
        )
    )
    mapped = map_event(_event(buyer_email="renewal"), field_map)
    assert "stage" not in mapped.properties
    assert mapped.findings[0]["code"] == "unmapped_option"


def test_a_missing_sync_key_is_a_hard_finding_against_the_whole_map():
    field_map = normalise_field_map(map_spec("document_viewed", "conn_1"))
    mapped = map_event({"id": "", "data": {}}, field_map)
    assert mapped.sync_key == {}
    codes = [finding["code"] for finding in mapped.findings]
    assert "sync_key_unresolved" in codes
    assert all(finding["severity"] == "hard" for finding in mapped.findings), (
        "an unusable sync key is not a soft problem"
    )


def test_the_sync_key_can_read_a_field_other_than_the_row_id():
    field_map = normalise_field_map(
        map_spec("document_viewed", "conn_1", sync_key={"source": "type", "target_property": "k"})
    )
    mapped = map_event(_event(type="document_viewed"), field_map)
    assert mapped.properties["k"] == "document_viewed"


def test_a_field_map_to_dict_is_json_safe():
    field_map = normalise_field_map(map_spec("document_viewed", "conn_1"))
    body = map_event(_event(**event_payload()), field_map).to_dict()
    assert json.loads(json.dumps(body))["sync_key"]["property"] == "dsr_engagement_id"


# --------------------------------------------------------------------------- #
# Field map and catalogue validation
# --------------------------------------------------------------------------- #


def test_a_field_map_needs_an_event_type():
    with pytest.raises(InvalidFieldMap, match="event_type is required"):
        normalise_field_map({"fields": [{"source": "type", "target": "t"}]})


def test_a_field_map_needs_at_least_one_field():
    with pytest.raises(InvalidFieldMap, match="no fields"):
        normalise_field_map({"event_type": "x", "fields": [], "sync_key": {"target_property": "k"}})


def test_a_field_needs_a_target_property():
    with pytest.raises(InvalidFieldMap, match="no target property"):
        normalise_field_map(
            {
                "event_type": "x",
                "fields": [{"source": "type"}],
                "sync_key": {"target_property": "k"},
            }
        )


def test_two_fields_cannot_claim_one_target():
    """One create cannot send the same property twice, and the second would silently win."""
    with pytest.raises(InvalidFieldMap, match="mapped twice"):
        normalise_field_map(
            map_spec(
                "x",
                "c",
                fields=[{"source": "type", "target": "t"}, {"source": "asset", "target": "t"}],
            )
        )


def test_a_field_map_needs_a_sync_key():
    """[sourced] the sync key is what lets the CRM "reject collisions"."""
    with pytest.raises(InvalidFieldMap, match="sync_key is required"):
        normalise_field_map({"event_type": "x", "fields": [{"source": "type", "target": "t"}]})


def test_a_sync_key_needs_a_target_property():
    with pytest.raises(InvalidFieldMap, match="target_property is required"):
        normalise_field_map(
            {"event_type": "x", "fields": [{"source": "type", "target": "t"}], "sync_key": {}}
        )


def test_a_sync_key_cannot_collide_with_a_mapped_field():
    with pytest.raises(InvalidFieldMap, match="also a mapped field"):
        normalise_field_map(
            map_spec("x", "c", sync_key={"source": "id", "target_property": "email"})
        )


def test_an_unknown_direction_is_refused():
    with pytest.raises(InvalidFieldMap, match="is not one of"):
        normalise_field_map(
            map_spec("x", "c", fields=[{"source": "type", "target": "t", "direction": "sideways"}])
        )


def test_picklist_map_without_options_is_refused_because_nothing_could_be_translated():
    with pytest.raises(InvalidFieldMap, match="no options"):
        normalise_field_map(
            map_spec(
                "x", "c", fields=[{"source": "type", "target": "t", "transform": "picklist.map"}]
            )
        )


def test_a_field_that_is_not_an_object_is_refused():
    with pytest.raises(InvalidFieldMap, match="not an object"):
        normalise_field_map(map_spec("x", "c", fields=["nope"]))


def test_a_field_defaults_its_direction_to_out_and_its_transform_to_identity():
    spec = normalise_field_map(map_spec("x", "c", fields=[{"source": "type", "target": "t"}]))
    assert spec["fields"][0]["direction"] == "out"
    assert spec["fields"][0]["transform"] == "identity"


def test_an_event_type_row_needs_a_name():
    with pytest.raises(InvalidEventType, match="event_type is required"):
        normalise_event_type({"label": "no name"})


def test_an_event_type_row_derives_a_readable_label():
    assert normalise_event_type({"event_type": "cta_click"})["label"] == "Cta Click"


def test_a_connector_needs_a_known_vendor():
    with pytest.raises(InvalidConnector, match="is not one this build writes to"):
        normalise_connector({"vendor": "pipedrive", "base_url": "https://x.test", "object": "o"})


def test_a_hubspot_connector_needs_an_object_type():
    with pytest.raises(InvalidConnector, match="needs 'object'"):
        normalise_connector({"vendor": "hubspot", "base_url": "https://x.test"})


def test_a_dataverse_connector_needs_an_entity_set_not_an_object():
    """The two target fields are not interchangeable, so setting the wrong one is refused
    rather than stored and ignored."""
    with pytest.raises(InvalidConnector, match="needs 'entity_set'"):
        normalise_connector(
            {"vendor": "dataverse", "base_url": "https://x.test", "object": "accounts"}
        )


def test_a_connector_needs_an_absolute_base_url():
    with pytest.raises(InvalidConnector, match="absolute http"):
        normalise_connector(
            {"vendor": "hubspot", "base_url": "api.hubapi.com", "object": "contacts"}
        )


def test_a_trailing_slash_is_stripped_from_the_base_url():
    assert (
        normalise_connector(connector_spec(base_url="https://x.test/"))["base_url"]
        == "https://x.test"
    )


# --------------------------------------------------------------------------- #
# Payloads: one create per vendor
# --------------------------------------------------------------------------- #


def test_hubspot_gets_the_properties_object_body():
    """[sourced] "CRM create payload (`properties` object, or SOQL-shaped field body)"."""
    request = build_create("hubspot", connector_spec(), {"email": "a@b.test"})
    assert request.url == "https://api.hubapi.test/crm/v3/objects/contacts"
    assert request.body == {"properties": {"email": "a@b.test"}}


def test_dataverse_and_salesforce_get_the_flat_field_body():
    dataverse = build_create(
        "dataverse",
        {"vendor": "dataverse", "base_url": "https://c.test", "entity_set": "dsr_engagements"},
        {"a": 1},
    )
    salesforce = build_create(
        "salesforce",
        {"vendor": "salesforce", "base_url": "https://c.test", "object": "Room_Engagement__c"},
        {"a": 1},
    )
    assert dataverse.body == {"a": 1}
    assert salesforce.body == {"a": 1}


def test_the_dataverse_path_carries_the_documented_api_version():
    request = build_create(
        "dataverse",
        {"vendor": "dataverse", "base_url": "https://c.test", "entity_set": "accounts"},
        {},
    )
    assert request.url == "https://c.test/api/data/v9.2/accounts"


def test_the_salesforce_path_carries_the_sobjects_segment():
    request = build_create(
        "salesforce",
        {"vendor": "salesforce", "base_url": "https://c.test", "object": "Room_Engagement__c"},
        {},
    )
    assert request.url == "https://c.test/services/data/vXX.X/sobjects/Room_Engagement__c"


def test_the_object_name_is_url_quoted_because_it_is_caller_supplied():
    """A slash in a target would silently address a different route."""
    request = build_create(
        "salesforce", {"vendor": "salesforce", "base_url": "https://c.test", "object": "A/B"}, {}
    )
    assert request.url.endswith("/sobjects/A%2FB")


def test_a_create_carries_the_content_type_and_bearer_token():
    request = build_create("hubspot", connector_spec(token="secret-token"), {})
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["Authorization"] == "Bearer secret-token"


def test_preference_tokens_are_sent_as_one_comma_separated_header_in_order():
    request = build_create(
        "dataverse",
        {
            "vendor": "dataverse",
            "base_url": "https://c.test",
            "entity_set": "accounts",
            "preferences": ["return=representation", "odata.include-annotations"],
        },
        {},
    )
    assert request.headers["Prefer"] == "return=representation, odata.include-annotations"


def test_preference_tokens_are_de_duplicated_and_blank_ones_dropped():
    assert preference_list({"preferences": ["a", "a", " ", "", "b"]}) == ["a", "b"]


def test_a_bare_preference_string_is_accepted():
    assert preference_list({"preferences": "return=representation"}) == ["return=representation"]


def test_a_preference_this_build_cannot_explain_is_still_listed():
    rows = explain_preferences(["respond-async", "acme.magic"])
    assert rows[0]["known"] is True
    assert rows[1]["known"] is False
    assert "Not one this build can describe" in rows[1]["effect"]


def test_a_connector_without_a_base_url_cannot_be_addressed():
    with pytest.raises(ValueError, match="no base_url"):
        build_create("hubspot", {"vendor": "hubspot", "object": "contacts"}, {})


def test_a_connector_without_a_target_cannot_be_addressed():
    with pytest.raises(ValueError, match="no object"):
        build_create("hubspot", {"vendor": "hubspot", "base_url": "https://x.test"}, {})


def test_an_unknown_vendor_cannot_be_addressed():
    with pytest.raises(KeyError):
        build_create("pipedrive", {"vendor": "pipedrive", "base_url": "https://x.test"}, {})


def test_a_recorded_request_redacts_the_token():
    """The ``Authorization`` value *is* the token, and a sync-log row is readable."""
    request = build_create("hubspot", connector_spec(token="secret-token"), {"a": 1})
    recorded = request.to_dict()
    assert recorded["headers"]["Authorization"] == "Bearer ***redacted***"
    assert "secret-token" not in json.dumps(recorded)


def test_the_request_shape_is_served_as_data_for_the_page():
    for vendor in ("hubspot", "dataverse", "salesforce"):
        shape = payloads.describe_request_shape(vendor)
        assert shape["known"] is True
        assert shape["success_codes"] == list(success_codes(vendor))
        assert shape["record_id_locations"]


def test_an_unknown_vendors_request_shape_says_so():
    assert payloads.describe_request_shape("pipedrive") == {"vendor": "pipedrive", "known": False}


# --------------------------------------------------------------------------- #
# Id extraction
# --------------------------------------------------------------------------- #


def test_a_body_id_is_read_as_a_value_not_as_a_boolean():
    """Regression: the extractor asked whether the path was present and returned *that*.

    Every HubSpot create stored the literal string ``"True"`` as its ``crm_record_id``, and
    the log looked perfect.
    """
    located = extract_record_id("hubspot", 201, {}, json.dumps({"id": "501"}))
    assert located["id"] == "501"
    assert located["where"] == "body:id"
    assert located["sourced"] is True


def test_a_numeric_body_id_becomes_a_string():
    assert extract_record_id("hubspot", 201, {}, json.dumps({"id": 501}))["id"] == "501"


def test_the_dataverse_id_is_read_out_of_the_entity_uri_header():
    """[sourced] the quoted header is `…/accounts(00aa00aa-…)` on a 204 with no body."""
    headers = {
        "OData-EntityId": "https://contoso.crm.dynamics.com/api/data/v9.2/accounts(00AA00AA-1111-2222-3333-444444444444)"
    }
    located = extract_record_id("dataverse", 204, headers, "")
    assert located["id"] == "00aa00aa-1111-2222-3333-444444444444"
    assert located["where"] == "header:OData-EntityId"
    assert located["sourced"] is True


def test_the_entity_uri_header_is_matched_case_insensitively():
    headers = {
        "odata-entityid": "https://c.test/api/data/v9.2/accounts(00aa00aa-1111-2222-3333-444444444444)"
    }
    assert extract_record_id("dataverse", 204, headers, "")["id"].startswith("00aa00aa")


def test_a_204_with_no_id_anywhere_returns_nothing_rather_than_guessing():
    located = extract_record_id("dataverse", 204, {}, "")
    assert located["id"] == ""
    assert located["where"] is None
    assert "header:OData-EntityId" in located["searched"]


def test_the_dataverse_body_is_tried_only_after_the_header():
    headers = {
        "OData-EntityId": "https://c.test/api/data/v9.2/accounts(00aa00aa-1111-2222-3333-444444444444)"
    }
    body = json.dumps({"accountid": "body-guid"})
    assert extract_record_id("dataverse", 201, headers, body)["where"] == "header:OData-EntityId"


def test_the_dataverse_body_fallback_is_used_and_marked_unsourced():
    located = extract_record_id("dataverse", 201, {}, json.dumps({"accountid": "body-guid"}))
    assert located["id"] == "body-guid"
    assert located["sourced"] is False


def test_an_unparseable_salesforce_body_reports_no_id_rather_than_an_error():
    located = extract_record_id("salesforce", 201, {}, "")
    assert located["id"] == ""
    assert located["searched"] == ["body:id"]


def test_a_malformed_body_does_not_raise():
    assert extract_record_id("hubspot", 201, {}, "<html>not json</html>")["id"] == ""


def test_a_null_or_falsey_id_is_not_treated_as_found():
    assert extract_record_id("hubspot", 201, {}, json.dumps({"id": None}))["id"] == ""
    assert extract_record_id("hubspot", 201, {}, json.dumps({"id": ""}))["id"] == ""


def test_the_entity_uri_keeps_a_non_guid_value_verbatim():
    assert id_from_entity_id("https://c.test/api/data/v9.2/accounts(abc-123)") == "abc-123"


def test_the_entity_uri_without_parentheses_yields_nothing():
    assert id_from_entity_id("https://c.test/api/data/v9.2/accounts") == ""


# --------------------------------------------------------------------------- #
# Vendor error detail
# --------------------------------------------------------------------------- #


def test_odata_annotations_are_read_into_the_error_detail():
    """[sourced] `Prefer: odata.include-annotations` is for "enriched error detail"."""
    body = json.dumps(
        {
            "error": {
                "code": "0x80040220",
                "message": "The value is not valid.",
                "annotations": [{"message": "field: bad", "target": "field", "code": "0x1"}],
            }
        }
    )
    detail = error_detail(body)
    assert detail["code"] == "0x80040220"
    assert detail["message"] == "The value is not valid."
    assert detail["annotations"] == [{"message": "field: bad", "target": "field", "code": "0x1"}]


def test_a_hubspot_error_list_is_read():
    detail = error_detail(
        json.dumps({"errors": [{"message": "nope", "category": "VALIDATION_ERROR"}]})
    )
    assert detail["message"] == "nope"
    assert detail["code"] == "VALIDATION_ERROR"


def test_a_salesforce_error_is_read():
    detail = error_detail(
        json.dumps({"message": "Insufficient access", "errorCode": "INSUFFICIENT_ACCESS"})
    )
    assert detail["message"] == "Insufficient access"
    assert detail["code"] == "INSUFFICIENT_ACCESS"


def test_an_odata_inner_error_is_used_when_the_outer_message_is_absent():
    detail = error_detail(json.dumps({"error": {"code": "1", "innererror": {"message": "inner"}}}))
    assert detail["message"] == "inner"


def test_an_unparseable_error_body_keeps_a_raw_sample_rather_than_being_dropped():
    detail = error_detail("<html>gateway timeout</html>")
    assert detail["message"] is None
    assert detail["raw"] == "<html>gateway timeout</html>"


def test_an_empty_error_body_yields_nothing_but_does_not_raise():
    assert error_detail("")["annotations"] == []


# --------------------------------------------------------------------------- #
# Delivery: the retry ladder and the per-attempt record
# --------------------------------------------------------------------------- #


def test_a_retryable_status_is_retried():
    result = CreateResult(ok=False, status=503)
    assert result.retryable is True


def test_a_transport_error_with_no_status_is_retried():
    """A refused connection is this second's problem, not the request's."""
    assert CreateResult(ok=False, error="URLError").retryable is True


def test_a_409_is_not_retried():
    """[sourced] the sync key is unique "so the CRM itself rejects collisions" - repeating
    the create cannot make the collision go away."""
    assert CreateResult(ok=False, status=409).retryable is False


def test_a_400_is_not_retried():
    assert CreateResult(ok=False, status=400).retryable is False


def test_a_403_is_not_retried():
    assert CreateResult(ok=False, status=403).retryable is False


def test_every_retryable_status_is_documented_in_the_vocabulary():
    assert set(RETRYABLE_STATUS) == {408, 425, 429, 500, 502, 503, 504}
    assert describe_vocabulary()["retryable_status"] == sorted(RETRYABLE_STATUS)


def test_a_successful_create_stops_the_loop_after_one_attempt():
    transport = Scripted(ok(201, json.dumps({"id": "501"})))
    report = post_create(transport, build_create("hubspot", connector_spec(), {"a": 1}), backoff=0)
    assert transport.count == 1
    assert report.ok is True
    assert report.attempts == 1


def test_a_retried_create_really_makes_a_second_request():
    transport = Scripted(bad(429), ok(201, json.dumps({"id": "501"})))
    report = post_create(transport, build_create("hubspot", connector_spec(), {"a": 1}), backoff=0)
    assert transport.count == 2
    assert report.ok is True
    assert [r.status for r in report.history] == [429, 201]


def test_a_permanent_failure_is_not_retried():
    transport = Scripted(bad(400), ok(201))
    report = post_create(transport, build_create("hubspot", connector_spec(), {"a": 1}), backoff=0)
    assert transport.count == 1
    assert report.ok is False
    assert report.needs_manual_update is True


def test_a_2xx_outside_the_vendors_documented_set_is_not_treated_as_a_success():
    """Salesforce's own reference puts 204 among the success codes for DELETE, not POST."""
    transport = Scripted(ok(204, ""))
    report = post_create(
        transport,
        build_create(
            "salesforce", {"vendor": "salesforce", "base_url": "https://c.test", "object": "O"}, {}
        ),
        backoff=0,
    )
    assert report.ok is False
    assert report.result.status == 204


def test_a_dataverse_204_is_a_success():
    transport = Scripted(ok(204, ""))
    report = post_create(
        transport,
        build_create(
            "dataverse",
            {"vendor": "dataverse", "base_url": "https://c.test", "entity_set": "a"},
            {},
        ),
        backoff=0,
    )
    assert report.ok is True


def test_the_body_is_serialised_once_so_the_record_is_what_was_sent():
    transport = Scripted(ok(201, json.dumps({"id": "1"})))
    request = build_create("hubspot", connector_spec(), {"b": 2, "a": 1})
    post_create(transport, request, backoff=0)
    assert transport.calls[0]["body"] == {"properties": {"a": 1, "b": 2}}


def test_the_attempt_log_records_every_attempt_in_full():
    transport = Scripted(bad(429), bad(429), ok(201, json.dumps({"id": "9"})))
    report = post_create(transport, build_create("hubspot", connector_spec(), {"a": 1}), backoff=0)
    body = report.to_dict()
    assert body["attempt_statuses"] == [429, 429, 201]
    assert [entry["attempt"] for entry in body["attempt_log"]] == [1, 2, 3]
    assert body["attempts"] == 3


def test_a_successful_create_with_no_id_needs_a_human():
    """The researched step 5 needs an id to store, and none arrived."""
    transport = Scripted(ok(201, ""))
    report = post_create(
        transport,
        build_create(
            "salesforce", {"vendor": "salesforce", "base_url": "https://c.test", "object": "O"}, {}
        ),
        backoff=0,
    )
    assert report.ok is True
    assert report.needs_manual_update is True
    assert report.to_dict()["crm_record_id"] is None


def test_the_report_records_where_the_id_came_from_and_whether_it_was_sourced():
    transport = Scripted(ok(201, json.dumps({"id": "501"})))
    report = post_create(transport, build_create("hubspot", connector_spec(), {"a": 1}), backoff=0)
    body = report.to_dict()
    assert body["crm_record_id"] == "501"
    assert body["crm_record_id_from"] == "body:id"
    assert body["crm_record_id_sourced"] is True


def test_the_vendor_error_is_carried_into_the_report():
    transport = Scripted(bad(400, json.dumps({"error": {"code": "0x1", "message": "no"}})))
    report = post_create(transport, build_create("hubspot", connector_spec(), {"a": 1}), backoff=0)
    assert report.to_dict()["vendor_error"]["message"] == "no"


def test_the_default_transport_never_raises_on_a_dead_host():
    result = delivery.UrllibTransport().post(
        "http://127.0.0.1:1/nothing", b"{}", {"Content-Type": "application/json"}, 0.2
    )
    assert result.ok is False
    assert result.retryable is True


# --------------------------------------------------------------------------- #
# The queue: two rows, four states
# --------------------------------------------------------------------------- #


def test_recording_an_event_writes_the_row_before_anything_is_sent(book, room):
    row = book.record_engagement(event_payload(), room_id=room["id"], source=SOURCE)
    assert row["collection"] == queue_module.ENGAGEMENT_COLLECTION
    assert row["data"]["type"] == "document_viewed"
    assert row["data"]["crm_record_id"] is None
    assert row["data"]["sync_state"] == "pending"


def test_the_engagement_payload_is_stored_verbatim_so_a_new_field_needs_no_coordination(book, room):
    row = book.record_engagement(
        event_payload(acme_new_field={"nested": [1, 2]}), room_id=room["id"], source=SOURCE
    )
    assert row["data"]["acme_new_field"] == {"nested": [1, 2]}


def test_an_event_with_no_type_is_refused(book, room):
    with pytest.raises(InvalidEventType, match="type is required"):
        book.record_engagement({"asset": "Deck"}, room_id=room["id"], source=SOURCE)


def test_a_queue_row_starts_pending_with_an_empty_attempt_history(book, room):
    engagement = book.record_engagement(event_payload(), room_id=room["id"], source=SOURCE)
    row = book.enqueue(engagement, source=SOURCE)
    data = row["data"]
    assert data["state"] == "pending"
    assert data["attempts"] == 0
    assert data["attempt_log"] == []
    assert data["last_source"] == SOURCE


def test_the_queue_is_read_oldest_first(book, room):
    for index in range(3):
        engagement = book.record_engagement(
            event_payload(asset=f"Asset {index}"), room_id=room["id"], source=SOURCE
        )
        book.enqueue(engagement, source=SOURCE)
    rows = book.queue_rows(room["id"], limit=10)
    assert len(rows) == 3
    assert [row["created_at"] for row in rows] == sorted(row["created_at"] for row in rows)


def test_the_queue_is_filtered_by_state_through_the_dynamic_index(book, room):
    engagement = book.record_engagement(event_payload(), room_id=room["id"], source=SOURCE)
    first = book.enqueue(engagement, source=SOURCE)
    other = book.record_engagement(event_payload(asset="B"), room_id=room["id"], source=SOURCE)
    second = book.enqueue(other, source=SOURCE)
    book.mark_blocked(first["id"], block_reason="event_type_unmapped", source=SOURCE)
    assert [row["id"] for row in book.queue_rows(room["id"], state="blocked")] == [first["id"]]
    assert [row["id"] for row in book.queue_rows(room["id"], state="pending")] == [second["id"]]


def test_a_blocked_row_carries_its_named_reason_and_its_explanation(book, room):
    engagement = book.record_engagement(event_payload(), room_id=room["id"], source=SOURCE)
    row = book.enqueue(engagement, source=SOURCE)
    settled = book.mark_blocked(row["id"], block_reason="event_type_unmapped", source=SOURCE)
    assert settled["data"]["state"] == "blocked"
    assert settled["data"]["block_reason"] == "event_type_unmapped"
    assert settled["data"]["block_detail"] == BLOCK_REASONS["event_type_unmapped"]


def test_an_undocumented_block_reason_is_refused_rather_than_stored(book, room):
    engagement = book.record_engagement(event_payload(), room_id=room["id"], source=SOURCE)
    row = book.enqueue(engagement, source=SOURCE)
    with pytest.raises(ValueError, match="documented block reason"):
        book.mark_blocked(row["id"], block_reason="because", source=SOURCE)


def test_a_synced_row_mirrors_its_crm_record_id_onto_the_engagement_row(book, room):
    """[sourced] step 5: "writes the returned CRM record id into its local row
    (`crm_record_id`) and marks the event as synced"."""
    engagement = book.record_engagement(event_payload(), room_id=room["id"], source=SOURCE)
    row = book.enqueue(engagement, source=SOURCE)
    book.mark_synced(
        row["id"],
        connector_id="conn_1",
        crm_record_id="501",
        crm_record_id_from="body:id",
        attempt_log=[{"attempt": 1}],
        attempt_statuses=[201],
        request={"vendor": "hubspot"},
        findings=[],
        resolution={},
        vendor_error={},
        http_status=201,
        source=SOURCE,
    )
    stored = book.engagement(engagement["id"])["data"]
    assert stored["crm_record_id"] == "501"
    assert stored["sync_state"] == "synced"


def test_both_rows_one_write_settles_carry_the_same_source(book, room, store):
    """They are one outcome; an audit trail that named the route for one and not the other
    would make a reader doubt the pair."""
    engagement = book.record_engagement(event_payload(), room_id=room["id"], source=SOURCE)
    row = book.enqueue(engagement, source=SOURCE)
    book.mark_blocked(row["id"], block_reason="no_connector", source=SOURCE)
    entries = store.audit(limit=50)
    settled = [e for e in entries if e["source"] == SOURCE and e["action"] == "update"]
    assert {e["record_id"] for e in settled} == {row["id"], engagement["id"]}


def test_the_sync_log_is_appended_per_write_rather_than_updated(book, room):
    engagement = book.record_engagement(event_payload(), room_id=room["id"], source=SOURCE)
    row = book.enqueue(engagement, source=SOURCE)
    first = book.log_attempt(
        queue_row=row,
        report={"attempts": 1, "attempt_statuses": [429]},
        outcome="failed",
        trigger="drain",
        source=SOURCE,
    )
    second = book.log_attempt(
        queue_row=row,
        report={"attempts": 2, "attempt_statuses": [429, 403]},
        outcome="failed",
        trigger="retry",
        source=SOURCE,
    )
    assert first["id"] != second["id"]
    assert len(book.sync_log(room["id"], limit=10)) == 2


def test_the_sync_log_filters_on_needs_a_human_through_the_dynamic_index(book, room):
    engagement = book.record_engagement(event_payload(), room_id=room["id"], source=SOURCE)
    row = book.enqueue(engagement, source=SOURCE)
    book.log_attempt(
        queue_row=row,
        report={"needs_manual_update": True},
        outcome="failed",
        trigger="drain",
        source=SOURCE,
    )
    book.log_attempt(
        queue_row=row,
        report={"needs_manual_update": False},
        outcome="synced",
        trigger="drain",
        source=SOURCE,
    )
    assert len(book.sync_log(room["id"], needs_manual_update=True, limit=10)) == 1


def test_an_unknown_room_is_a_404_domain_error(book):
    with pytest.raises(UnknownRoom):
        book.require_room("not-a-room")


def test_a_room_id_that_is_another_collection_is_not_a_room(book):
    engagement = book.record_engagement(event_payload(), room_id="room_1", source=SOURCE)
    with pytest.raises(UnknownRoom):
        book.require_room(engagement["id"])


# --------------------------------------------------------------------------- #
# Steps 1 and 2 through the engine
# --------------------------------------------------------------------------- #


def test_recording_an_event_stores_the_row_and_enqueues_before_sending(engine, room):
    connector_id = configured(engine, room["id"])
    transport = Scripted(ok(201, json.dumps({"id": "501"})))
    engine.transport = transport
    body = engine.record_event(room["id"], event_payload(), source=SOURCE)
    assert body["engagement"]["data"]["type"] == "document_viewed"
    assert body["fired"] is True
    assert body["result"]["state"] == "synced"
    assert body["result"]["crm_record_id"] == "501"
    assert connector_id


def test_a_room_event_does_not_write_a_crm_row_when_firing_is_deferred(engine, room):
    configured(engine, room["id"])
    transport = Scripted(ok(201, json.dumps({"id": "501"})))
    engine.transport = transport
    body = engine.record_event(room["id"], event_payload(), source=SOURCE, fire_queue=False)
    assert transport.count == 0
    assert body["fired"] is False
    assert body["queue"]["data"]["state"] == "pending"


def test_one_user_action_produces_the_write_with_no_second_one(engine, room):
    """[sourced] "the room's queue worker fires it without further user input"."""
    configured(engine, room["id"])
    engine.transport = Scripted(ok(201, json.dumps({"id": "501"})))
    engine.record_event(room["id"], event_payload(), source=SOURCE)
    assert engine.transport.count == 1


def test_a_deferred_row_waits_for_a_drain_and_is_sent_then(engine, room):
    configured(engine, room["id"])
    engine.transport = Scripted(ok(201, json.dumps({"id": "501"})))
    engine.record_event(room["id"], event_payload(), source=SOURCE, fire_queue=False)
    summary = engine.drain(room["id"], source=SOURCE)
    assert summary["counts"]["synced"] == 1
    assert engine.transport.count == 1


# --------------------------------------------------------------------------- #
# Step 3: resolving the buyer, and every named block reason
# --------------------------------------------------------------------------- #


def test_a_map_with_no_buyer_field_needs_no_resolution(engine, room):
    engine.register_connector(connector_spec(), room_id=room["id"], source="seed")
    connector_id = engine.connectors()[-1]["id"]
    engine.add_field_map(
        map_spec("room_opened", connector_id, fields=[{"source": "type", "target": "t"}]),
        source="seed",
    )
    engine.transport = Scripted(ok(201, json.dumps({"id": "1"})))
    body = engine.record_event(room["id"], {"type": "room_opened"}, source=SOURCE)
    assert body["result"]["state"] == "synced"


def test_a_map_that_sends_a_buyer_field_blocks_an_event_with_no_buyer(engine, room):
    configured(engine, room["id"])
    engine.transport = Scripted(ok(201, json.dumps({"id": "1"})))
    body = engine.record_event(
        room["id"], {"type": "document_viewed", "asset": "Deck"}, source=SOURCE
    )
    assert body["result"]["state"] == "blocked"
    assert body["result"]["reason"] == "buyer_unresolved"
    assert engine.transport.count == 0


def test_a_buyer_crm_record_id_is_tried_before_an_email(engine, room):
    engine.register_connector(connector_spec(), room_id=room["id"], source="seed")
    connector_id = engine.connectors()[-1]["id"]
    engine.add_field_map(
        map_spec(
            "document_viewed",
            connector_id,
            fields=[
                {"source": "buyer_crm_id", "target": "contact_id"},
                {"source": "buyer_email", "target": "email", "transform": "email.normalize"},
            ],
        ),
        source="seed",
    )
    engine.transport = Scripted(ok(201, json.dumps({"id": "1"})))
    body = engine.record_event(
        room["id"], event_payload(buyer_crm_id="c-77"), source=SOURCE, fire_queue=False
    )
    engine.drain(room["id"], source=SOURCE)
    detail = engine.event_detail(body["engagement"]["id"])
    assert detail["queue"][0]["data"]["resolution"]["by"] == "buyer_crm_id"
    assert detail["queue"][0]["data"]["resolution"]["value"] == "c-77"


def test_a_buyer_email_is_used_when_there_is_no_crm_record_id(engine, room):
    configured(engine, room["id"])
    engine.transport = Scripted(ok(201, json.dumps({"id": "1"})))
    body = engine.record_event(room["id"], event_payload(), source=SOURCE, fire_queue=False)
    engine.drain(room["id"], source=SOURCE)
    detail = engine.event_detail(body["engagement"]["id"])
    assert detail["queue"][0]["data"]["resolution"]["by"] == "buyer_email"


def test_no_connector_blocks_the_row_rather_than_dropping_the_event(engine, room):
    engine.transport = Scripted(ok(201, json.dumps({"id": "1"})))
    body = engine.record_event(room["id"], event_payload(), source=SOURCE)
    assert body["engagement"]["data"]["sync_state"] == "blocked"
    assert body["result"]["reason"] == "no_connector"
    assert engine.transport.count == 0


def test_a_disabled_connector_blocks_the_row(engine, room):
    engine.register_connector(connector_spec(enabled=False), room_id=room["id"], source="seed")
    body = engine.record_event(room["id"], event_payload(), source=SOURCE)
    assert body["result"]["reason"] == "connector_disabled"


def test_an_unmapped_event_type_blocks_the_row(engine, room):
    configured(engine, room["id"])
    body = engine.record_event(room["id"], event_payload(type="cta_click"), source=SOURCE)
    assert body["result"]["reason"] == "event_type_unmapped"


def test_adding_the_mapping_row_and_draining_again_sends_what_was_waiting(engine, room):
    """[sourced] adding a new event type "needs a mapping row, not a code path" - so the
    mapping row has to be enough, on the rows that were already blocked."""
    configured(engine, room["id"])
    engine.transport = Scripted(ok(201, json.dumps({"id": "1"})), ok(201, json.dumps({"id": "2"})))
    body = engine.record_event(room["id"], event_payload(type="cta_click"), source=SOURCE)
    assert body["result"]["reason"] == "event_type_unmapped"
    connector_id = engine.connectors()[-1]["id"]
    engine.add_field_map(map_spec("cta_click", connector_id), source="seed")
    # The blocked row is not a pending row, so a drain does not re-plan it by itself.
    assert engine.drain(room["id"], source=SOURCE)["counts"]["considered"] == 0
    retried = engine.retry(body["queue"]["id"], source=SOURCE)
    assert retried["state"] == "synced"
    assert retried["previous_state"] == "blocked"
    assert engine.transport.count == 1


def test_two_enabled_connectors_with_no_map_to_choose_between_them_is_blocked(
    engine, room, other_room
):
    engine.register_connector(connector_spec(), room_id=room["id"], source="seed")
    engine.register_connector(
        connector_spec(vendor="salesforce", object="O"), room_id=room["id"], source="seed"
    )
    body = engine.record_event(room["id"], event_payload(), source=SOURCE)
    assert body["result"]["reason"] == "ambiguous_connector"


def test_a_field_map_for_one_of_two_connectors_resolves_the_ambiguity(engine, room):
    engine.register_connector(connector_spec(), room_id=room["id"], source="seed")
    hubspot = engine.connectors()[-1]["id"]
    engine.register_connector(
        connector_spec(vendor="salesforce", object="O"), room_id=room["id"], source="seed"
    )
    engine.add_field_map(map_spec("document_viewed", hubspot), source="seed")
    engine.transport = Scripted(ok(201, json.dumps({"id": "1"})))
    body = engine.record_event(room["id"], event_payload(), source=SOURCE)
    assert body["result"]["state"] == "synced"


def test_a_room_scoped_connector_is_not_read_as_unscoped(engine, room, other_room):
    """Regression: ``room_id`` is a column, not a payload field.

    Left out of the projection, every room-scoped connector looked like the installation
    default, so room one wrote its engagement to room two's CRM.
    """
    engine.register_connector(connector_spec(), room_id=other_room["id"], source="seed")
    assert engine.connectors()[0]["room_id"] == other_room["id"]
    body = engine.record_event(room["id"], event_payload(), source=SOURCE)
    assert body["result"]["reason"] == "no_connector"


def test_an_unscoped_connector_is_the_fallback_for_a_room_with_none_of_its_own(
    engine, room, other_room
):
    engine.register_connector(connector_spec(), source="seed")
    engine.add_field_map(map_spec("document_viewed", engine.connectors()[-1]["id"]), source="seed")
    engine.transport = Scripted(ok(201, json.dumps({"id": "1"})))
    body = engine.record_event(other_room["id"], event_payload(), source=SOURCE)
    assert body["result"]["state"] == "synced"


def test_an_event_whose_queue_row_points_at_nothing_is_blocked(engine, room, store):
    configured(engine, room["id"])
    body = engine.record_event(room["id"], event_payload(), source=SOURCE, fire_queue=False)
    store.delete(body["engagement"]["id"], source=SOURCE)
    queue_id = body["queue"]["id"]
    # The engagement is soft-deleted, so the planner cannot read it and says so by name.
    summary = engine.drain(room["id"], source=SOURCE, only=[queue_id])
    assert summary["counts"]["blocked"] == 1


def test_a_sync_key_that_cannot_be_built_blocks_the_row(engine, room):
    engine.register_connector(connector_spec(), room_id=room["id"], source="seed")
    engine.add_field_map(
        map_spec(
            "document_viewed",
            engine.connectors()[-1]["id"],
            fields=[{"source": "type", "target": "t"}],
            sync_key={"source": "no_such_field", "target_property": "k"},
        ),
        source="seed",
    )
    body = engine.record_event(room["id"], event_payload(), source=SOURCE)
    assert body["result"]["reason"] == "sync_key_unresolved"


# --------------------------------------------------------------------------- #
# Steps 4 and 5: sending, settling, and the researched failure vocabulary
# --------------------------------------------------------------------------- #


def test_a_successful_create_writes_the_id_and_marks_the_event_synced(engine, room):
    configured(engine, room["id"])
    engine.transport = Scripted(ok(201, json.dumps({"id": "501"})))
    body = engine.record_event(room["id"], event_payload(), source=SOURCE)
    detail = engine.event_detail(body["engagement"]["id"])
    assert detail["sync_state"] == "synced"
    assert detail["crm_record_id"] == "501"
    assert detail["queue"][0]["data"]["state"] == "synced"


def test_a_dataverse_204_is_synced_with_the_id_from_the_header(engine, room):
    """The researched Dataverse case: 204, no body, id in a header."""
    configured(
        engine,
        room["id"],
        connector={"vendor": "dataverse", "entity_set": "dsr_engagements", "object": ""},
    )
    engine.transport = Scripted(
        ok(
            204,
            "",
            {
                "OData-EntityId": "https://c.test/api/data/v9.2/dsr_engagements(00aa00aa-1111-2222-3333-444444444444)"
            },
        )
    )
    body = engine.record_event(room["id"], event_payload(), source=SOURCE)
    assert body["result"]["state"] == "synced"
    assert body["result"]["crm_record_id_from"] == "header:OData-EntityId"


def test_a_429_then_a_403_is_failed_with_both_attempts_recorded(engine, room):
    configured(engine, room["id"])
    engine.transport = Scripted(
        bad(429), bad(403, json.dumps([{"message": "no", "errorCode": "X"}]))
    )
    body = engine.record_event(room["id"], event_payload(), source=SOURCE)
    assert body["result"]["state"] == "failed"
    assert body["result"]["attempts"] == 2
    assert body["result"]["failure_reason"] == "crm_refused"
    assert body["result"]["needs_manual_update"] is True
    detail = engine.event_detail(body["engagement"]["id"])
    assert detail["queue"][0]["data"]["attempt_statuses"] == [429, 403]


def test_a_409_is_reported_as_the_sync_key_working_and_is_not_retried(engine, room):
    configured(engine, room["id"])
    engine.transport = Scripted(bad(409, json.dumps({"error": {"message": "duplicate key"}})))
    body = engine.record_event(room["id"], event_payload(), source=SOURCE)
    assert body["result"]["failure_reason"] == "sync_key_collision"
    assert body["result"]["attempts"] == 1
    assert body["result"]["needs_manual_update"] is True


def test_a_create_accepted_with_no_id_is_failed_rather_than_synced(engine, room):
    """The researched gap, on the vendor the research admits it about."""
    configured(
        engine, room["id"], connector={"vendor": "salesforce", "object": "Room_Engagement__c"}
    )
    engine.transport = Scripted(ok(201, ""))
    body = engine.record_event(room["id"], event_payload(), source=SOURCE)
    assert body["result"]["state"] == "failed"
    assert body["result"]["failure_reason"] == "crm_id_absent"
    assert body["result"]["needs_manual_update"] is True
    assert engine.event_detail(body["engagement"]["id"])["crm_record_id"] is None


def test_a_transport_failure_is_reported_as_such(engine, room):
    configured(engine, room["id"])
    engine.transport = Scripted(
        CreateResult(ok=False, error="URLError: unreachable"),
        CreateResult(ok=False, error="URLError: unreachable"),
        CreateResult(ok=False, error="URLError: unreachable"),
    )
    body = engine.record_event(room["id"], event_payload(), source=SOURCE)
    assert body["result"]["failure_reason"] == "transport_error"
    assert body["result"]["attempts"] == 3


def test_an_annotated_error_reaches_the_sync_log(engine, room):
    configured(
        engine,
        room["id"],
        connector={"vendor": "dataverse", "entity_set": "dsr_engagements", "object": ""},
    )
    engine.transport = Scripted(
        bad(
            400,
            json.dumps(
                {
                    "error": {
                        "code": "0x1",
                        "message": "not valid",
                        "annotations": [{"message": "field: bad", "target": "field"}],
                    }
                }
            ),
        )
    )
    engine.record_event(room["id"], event_payload(), source=SOURCE)
    log = engine.sync_log(room["id"], limit=10)["entries"]
    assert log[0]["data"]["vendor_error"]["annotations"][0]["message"] == "field: bad"


def test_a_mapping_finding_is_carried_onto_the_queue_row_and_the_log(engine, room):
    engine.register_connector(connector_spec(), room_id=room["id"], source="seed")
    engine.add_field_map(
        map_spec(
            "document_viewed",
            engine.connectors()[-1]["id"],
            fields=[
                {"source": "type", "target": "t"},
                {"source": "nope", "target": "missing"},
            ],
        ),
        source="seed",
    )
    engine.transport = Scripted(ok(201, json.dumps({"id": "1"})))
    body = engine.record_event(room["id"], event_payload(), source=SOURCE)
    assert body["result"]["state"] == "synced"
    detail = engine.event_detail(body["engagement"]["id"])
    assert [f["code"] for f in detail["queue"][0]["data"]["findings"]] == ["source_unresolved"]


def test_a_failed_write_never_stores_a_crm_record_id(engine, room):
    configured(engine, room["id"])
    engine.transport = Scripted(bad(500), bad(500), bad(500))
    body = engine.record_event(room["id"], event_payload(), source=SOURCE)
    detail = engine.event_detail(body["engagement"]["id"])
    assert detail["crm_record_id"] is None
    assert detail["queue"][0]["data"]["crm_record_id"] is None


def test_the_token_appears_in_no_stored_row(engine, room, store):
    configured(engine, room["id"], connector={"token": "super-secret-token"})
    engine.transport = Scripted(ok(201, json.dumps({"id": "1"})))
    engine.record_event(room["id"], event_payload(), source=SOURCE)
    for collection in (
        queue_module.ENGAGEMENT_COLLECTION,
        queue_module.QUEUE_COLLECTION,
        queue_module.SYNC_LOG_COLLECTION,
    ):
        for row in store.list(collection, limit=1000):
            assert "super-secret-token" not in json.dumps(row, default=str), collection


# --------------------------------------------------------------------------- #
# The worker: drain, preview, retry
# --------------------------------------------------------------------------- #


def test_a_drain_takes_pending_rows_oldest_first(engine, room):
    configured(engine, room["id"])
    engine.transport = Scripted(ok(201, json.dumps({"id": "1"})), ok(201, json.dumps({"id": "2"})))
    for index in range(2):
        engine.record_event(
            room["id"], event_payload(asset=f"A{index}"), source=SOURCE, fire_queue=False
        )
    summary = engine.drain(room["id"], source=SOURCE)
    assert summary["counts"]["considered"] == 2
    assert summary["counts"]["synced"] == 2


def test_a_drain_is_idempotent_because_a_synced_row_is_never_re_sent(engine, room):
    """A drain that could run twice has to be safe to run on a timer."""
    configured(engine, room["id"])
    engine.transport = Scripted(ok(201, json.dumps({"id": "1"})))
    engine.record_event(room["id"], event_payload(), source=SOURCE, fire_queue=False)
    assert engine.drain(room["id"], source=SOURCE)["counts"]["synced"] == 1
    assert engine.drain(room["id"], source=SOURCE)["counts"]["considered"] == 0
    assert engine.transport.count == 1


def test_a_drain_does_not_retry_a_failed_row_on_its_own(engine, room):
    """A permanent failure waits for a human, not for a loop."""
    configured(engine, room["id"])
    engine.transport = Scripted(bad(403), bad(403), bad(403))
    engine.record_event(room["id"], event_payload(), source=SOURCE, fire_queue=False)
    assert engine.drain(room["id"], source=SOURCE)["counts"]["failed"] == 1
    assert engine.drain(room["id"], source=SOURCE)["counts"]["considered"] == 0


def test_a_drain_can_be_narrowed_to_specific_rows(engine, room):
    configured(engine, room["id"])
    engine.transport = Scripted(ok(201, json.dumps({"id": "1"})), ok(201, json.dumps({"id": "2"})))
    first = engine.record_event(
        room["id"], event_payload(asset="A"), source=SOURCE, fire_queue=False
    )
    engine.record_event(room["id"], event_payload(asset="B"), source=SOURCE, fire_queue=False)
    summary = engine.drain(room["id"], source=SOURCE, only=[first["queue"]["id"]])
    assert summary["counts"]["synced"] == 1
    assert summary["counts"]["skipped"] == 1


def test_a_preview_writes_nothing_at_all(engine, room, store):
    configured(engine, room["id"])
    engine.transport = Scripted(ok(201, json.dumps({"id": "1"})))
    engine.record_event(room["id"], event_payload(), source=SOURCE, fire_queue=False)
    before = len(store.audit(limit=1000))
    preview = engine.preview(room["id"])
    assert preview["counts"]["sendable"] == 1
    assert preview["plans"][0]["request"]["url"].endswith("/crm/v3/objects/contacts")
    assert len(store.audit(limit=1000)) == before
    assert engine.transport.count == 0


def test_a_preview_shows_the_block_reason_without_sending(engine, room):
    engine.transport = Scripted(ok(201))
    engine.record_event(room["id"], event_payload(), source=SOURCE, fire_queue=False)
    preview = engine.preview(room["id"])
    plan = preview["plans"][0]
    assert plan["sendable"] is False
    assert plan["blocked"] == "no_connector"
    assert plan["block_detail"] == BLOCK_REASONS["no_connector"]


def test_a_preview_shows_the_properties_and_findings_a_create_would_carry(engine, room):
    engine.register_connector(connector_spec(), room_id=room["id"], source="seed")
    engine.add_field_map(
        map_spec(
            "document_viewed",
            engine.connectors()[-1]["id"],
            fields=[{"source": "type", "target": "t"}, {"source": "nope", "target": "missing"}],
        ),
        source="seed",
    )
    engine.record_event(room["id"], event_payload(), source=SOURCE, fire_queue=False)
    plan = engine.preview(room["id"])["plans"][0]
    assert plan["properties"]["t"] == "document_viewed"
    assert "missing" not in plan["properties"]
    assert plan["findings"][0]["code"] == "source_unresolved"


def test_a_manual_retry_appends_to_the_attempt_history_rather_than_replacing_it(engine, room):
    configured(engine, room["id"])
    engine.transport = Scripted(bad(500), bad(500), bad(500), ok(201, json.dumps({"id": "9"})))
    body = engine.record_event(room["id"], event_payload(), source=SOURCE, fire_queue=False)
    queue_id = body["queue"]["id"]
    engine.drain(room["id"], source=SOURCE)
    retried = engine.retry(queue_id, source=SOURCE)
    assert retried["state"] == "synced"
    detail = engine.event_detail(body["engagement"]["id"])
    assert len(detail["sync_log"]) == 2
    assert detail["queue"][0]["data"]["attempt_log"][0]["status"] == 500


def test_a_synced_row_cannot_be_retried_because_that_would_create_a_second_crm_row(engine, room):
    configured(engine, room["id"])
    engine.transport = Scripted(ok(201, json.dumps({"id": "1"})))
    body = engine.record_event(room["id"], event_payload(), source=SOURCE)
    with pytest.raises(EngagementSyncError, match="would create a second CRM row"):
        engine.retry(body["queue"]["id"], source=SOURCE)


def test_a_pending_row_cannot_be_retried_because_the_drain_owns_it(engine, room):
    configured(engine, room["id"])
    body = engine.record_event(room["id"], event_payload(), source=SOURCE, fire_queue=False)
    with pytest.raises(EngagementSyncError, match="only a failed or blocked row"):
        engine.retry(body["queue"]["id"], source=SOURCE)


def test_retrying_an_unknown_row_is_refused(engine):
    with pytest.raises(EngagementSyncError, match="not found"):
        engine.retry("nope", source=SOURCE)


def test_draining_an_unconfigured_room_is_the_distinct_428(engine, room):
    with pytest.raises(SyncNotConfigured, match="no CRM connector applies"):
        engine.require_configured(room["id"])


def test_draining_a_room_with_only_disabled_connectors_is_the_distinct_428(engine, room):
    engine.register_connector(connector_spec(enabled=False), room_id=room["id"], source="seed")
    with pytest.raises(SyncNotConfigured, match="switched off"):
        engine.require_configured(room["id"])


def test_a_configured_room_passes_the_readiness_gate(engine, room):
    configured(engine, room["id"])
    engine.require_configured(room["id"])


# --------------------------------------------------------------------------- #
# The two researched surfaces
# --------------------------------------------------------------------------- #


def test_the_feed_reports_the_rooms_own_record_beside_the_crm_copy(engine, room):
    configured(engine, room["id"])
    engine.transport = Scripted(ok(201, json.dumps({"id": "501"})))
    engine.record_event(room["id"], event_payload(), source=SOURCE)
    feed = engine.feed(room["id"])
    assert feed["summary"]["synced"] == 1
    item = feed["events"][0]
    assert item["type"] == "document_viewed"
    assert item["asset"] == "Enterprise Overview Deck"
    assert item["dwell_seconds"] == 248
    assert item["crm_record_id"] == "501"


def test_the_feed_resolves_fields_the_way_the_field_map_does(engine, room):
    """Regression: the feed read ``dwell_seconds`` literally while the mapper resolved
    ``dwell`` by synonym, so a room using the synonym showed a dwell total of zero beside a
    create that had sent the right number."""
    configured(engine, room["id"], event_type="opened")
    engine.transport = Scripted(ok(201, json.dumps({"id": "1"})))
    engine.record_event(
        room["id"],
        {
            "type": "opened",
            "timestamp": "2026-09-24T08:14:00Z",
            "target": "Deck",
            "dwell": 240,
            "person": "a@b.test",
        },
        source=SOURCE,
    )
    item = engine.feed(room["id"])["events"][0]
    assert item["dwell_seconds"] == 240
    assert item["asset"] == "Deck"
    assert item["buyer_email"] == "a@b.test"
    assert item["occurred_at"] == "2026-09-24T08:14:00Z"
    assert engine.feed(room["id"])["summary"]["dwell_seconds"] == 240


def test_the_feed_never_writes(engine, room, store):
    configured(engine, room["id"])
    engine.transport = Scripted(ok(201, json.dumps({"id": "1"})))
    engine.record_event(room["id"], event_payload(), source=SOURCE)
    before = len(store.audit(limit=1000))
    engine.feed(room["id"])
    engine.configuration(room["id"])
    engine.preview(room["id"])
    engine.sync_log(room["id"])
    engine.event_types()
    engine.field_maps()
    engine.connectors()
    engine.vocabulary()
    engine.inferences()
    assert len(store.audit(limit=1000)) == before


def test_the_feed_can_be_filtered_by_sync_state_and_type(engine, room):
    configured(engine, room["id"])
    engine.transport = Scripted(bad(403), ok(201, json.dumps({"id": "1"})))
    engine.record_event(room["id"], event_payload(), source=SOURCE)
    engine.record_event(
        room["id"], event_payload(type="cta_click"), source=SOURCE, fire_queue=False
    )
    assert engine.feed(room["id"], sync_state="failed")["count"] == 1
    assert engine.feed(room["id"], sync_state="pending")["count"] == 1
    assert engine.feed(room["id"], type="cta_click")["count"] == 1


def test_the_feed_counts_dwell_only_where_it_is_a_number(engine, room):
    configured(engine, room["id"])
    engine.transport = Scripted(ok(201, json.dumps({"id": "1"})))
    engine.record_event(
        room["id"], event_payload(dwell_seconds="about four minutes"), source=SOURCE
    )
    assert engine.feed(room["id"])["summary"]["dwell_seconds"] == 0


def test_the_readiness_view_names_what_is_missing(engine, room):
    body = engine.configuration(room["id"])
    assert body["ready"] is False
    assert {entry["reason"] for entry in body["missing"]} == {"no_connector"}


def test_the_readiness_view_reports_a_ready_room(engine, room):
    configured(engine, room["id"])
    assert engine.configuration(room["id"])["ready"] is True


def test_the_readiness_view_names_an_unmapped_catalogue_entry(engine, room):
    configured(engine, room["id"])
    engine.add_event_type({"event_type": "cta_click"}, source="seed")
    body = engine.configuration(room["id"])
    assert body["ready"] is False
    assert any("cta_click has no field map" in entry["detail"] for entry in body["missing"])


def test_the_readiness_view_names_a_type_mapped_on_another_connector(engine, room, other_room):
    """A type that has a map *somewhere* is not mapped *here*.

    Checking coverage globally would report the room ready while the worker blocks every
    event of that type on it - which is the one thing this view exists to prevent a reader
    from concluding.
    """
    engine.register_connector(connector_spec(), room_id=room["id"], source="seed")
    engine.add_event_type({"event_type": "document_viewed"}, source="seed")
    engine.add_field_map(map_spec("document_viewed", engine.connectors()[-1]["id"]), source="seed")
    assert engine.configuration(room["id"])["ready"] is True

    # Now a second type goes into the catalogue and is mapped on a *different* room's
    # connector, which leaves this room unable to send it.
    engine.add_event_type({"event_type": "cta_click"}, source="seed")
    engine.register_connector(
        connector_spec(label="other room", vendor="salesforce", object="O"),
        room_id=other_room["id"],
        source="seed",
    )
    other = next(row for row in engine.connectors() if row["room_id"] == other_room["id"])
    engine.add_field_map(map_spec("cta_click", other["id"]), source="seed")

    body = engine.configuration(room["id"])
    assert body["ready"] is False
    assert any(
        entry["reason"] == "event_type_unmapped" and "cta_click" in entry["detail"]
        for entry in body["missing"]
    ), body["missing"]


def test_the_readiness_view_uses_the_same_connector_the_worker_would(engine, room, other_room):
    """Otherwise it could report a room ready while the worker blocks every row on it."""
    engine.register_connector(
        connector_spec(enabled=False), room_id=other_room["id"], source="seed"
    )
    assert engine.configuration(other_room["id"])["ready"] is False


def test_the_sync_log_summarises_over_exactly_the_rows_returned(engine, room):
    configured(engine, room["id"])
    engine.transport = Scripted(bad(403), ok(201, json.dumps({"id": "1"})))
    engine.record_event(room["id"], event_payload(), source=SOURCE)
    engine.record_event(room["id"], event_payload(asset="B"), source=SOURCE)
    everything = engine.sync_log(room["id"], limit=10)
    assert everything["summary"] == {"synced": 1, "failed": 1, "needs_manual_update": 1}
    only_failures = engine.sync_log(room["id"], outcome="failed", limit=10)
    assert only_failures["summary"]["synced"] == 0
    assert only_failures["summary"]["failed"] == 1


def test_the_sync_log_can_be_filtered_by_vendor_and_by_a_human_needed(engine, room):
    configured(engine, room["id"])
    engine.transport = Scripted(bad(403))
    engine.record_event(room["id"], event_payload(), source=SOURCE)
    assert engine.sync_log(room["id"], vendor="hubspot", limit=10)["count"] == 1
    assert engine.sync_log(room["id"], vendor="salesforce", limit=10)["count"] == 0
    assert engine.sync_log(room["id"], needs_manual_update=True, limit=10)["count"] == 1


def test_the_event_detail_carries_the_queue_row_and_every_write(engine, room):
    configured(engine, room["id"])
    engine.transport = Scripted(ok(201, json.dumps({"id": "1"})))
    body = engine.record_event(room["id"], event_payload(), source=SOURCE)
    detail = engine.event_detail(body["engagement"]["id"])
    assert len(detail["queue"]) == 1
    assert len(detail["sync_log"]) == 1
    assert detail["sync_log"][0]["data"]["trigger"] == "fire_queue"


def test_an_unknown_engagement_is_a_404_domain_error(engine):
    with pytest.raises(UnknownRoom):
        engine.event_detail("nope")


# --------------------------------------------------------------------------- #
# Configuration through the engine
# --------------------------------------------------------------------------- #


def test_a_connector_read_never_carries_its_token(engine, room):
    engine.register_connector(connector_spec(token="secret"), room_id=room["id"], source="seed")
    row = engine.connectors()[0]
    assert "token" not in row
    assert row["has_token"] is True


def test_a_connector_read_explains_each_preference(engine, room):
    engine.register_connector(
        connector_spec(preferences=["return=representation", "acme.magic"]),
        room_id=room["id"],
        source="seed",
    )
    detail = engine.connectors()[0]["preference_detail"]
    assert detail[0]["known"] is True
    assert detail[1]["known"] is False


def test_a_connector_can_be_switched_off_by_a_patch(engine, room):
    engine.register_connector(connector_spec(), room_id=room["id"], source="seed")
    connector_id = engine.connectors()[-1]["id"]
    assert (
        engine.patch_connector(connector_id, {"enabled": False}, source=SOURCE)["enabled"] is False
    )
    assert (
        engine.record_event(room["id"], event_payload(), source=SOURCE)["result"]["reason"]
        == "connector_disabled"
    )


def test_adding_a_second_row_for_one_event_type_is_refused(engine, room):
    engine.add_event_type({"event_type": "cta_click"}, source="seed")
    with pytest.raises(InvalidEventType, match="already in the catalogue"):
        engine.add_event_type({"event_type": "cta_click"}, source="seed")


def test_a_field_map_needs_the_connector_it_writes_to(engine):
    with pytest.raises(InvalidFieldMap, match="connector_id is required"):
        engine.add_field_map(map_spec("x", ""), source=SOURCE)


def test_a_field_map_naming_its_own_connector_wins_over_another_for_the_same_type(engine, room):
    """With two connectors on a room, the map is what says which CRM a type belongs to."""
    engine.register_connector(connector_spec(), room_id=room["id"], source="seed")
    hubspot = engine.connectors()[-1]["id"]
    engine.register_connector(
        connector_spec(vendor="salesforce", object="O"), room_id=room["id"], source="seed"
    )
    salesforce = engine.connectors()[-1]["id"]
    engine.add_field_map(map_spec("document_viewed", hubspot), source="seed")
    engine.add_field_map(map_spec("document_viewed", salesforce), source="seed")
    # The second registration sorts first by label, so the answer cannot be an accident of
    # ordering: it has to come from the map that names the Salesforce connector.
    assert engine.effective_field_map("document_viewed", salesforce)["connector_id"] == salesforce
    assert engine.effective_field_map("document_viewed", hubspot)["connector_id"] == hubspot


def test_a_disabled_field_map_is_not_chosen(engine, room):
    engine.register_connector(connector_spec(), room_id=room["id"], source="seed")
    connector_id = engine.connectors()[-1]["id"]
    engine.add_field_map(map_spec("document_viewed", connector_id), source="seed")
    engine.patch_field_map(engine.field_maps()[0]["id"], {"enabled": False}, source=SOURCE)
    assert engine.effective_field_map("document_viewed", connector_id) is None
    assert (
        engine.record_event(room["id"], event_payload(), source=SOURCE)["result"]["reason"]
        == "event_type_unmapped"
    )


def test_reading_an_unknown_configuration_row_is_a_400_domain_error(engine):
    with pytest.raises(EngagementSyncError, match="not found"):
        engine.field_map("nope")
    with pytest.raises(EngagementSyncError, match="not found"):
        engine.event_type("nope")
    with pytest.raises(EngagementSyncError, match="not found"):
        engine.sync_log_entry("nope")
    with pytest.raises(InvalidConnector, match="not found"):
        engine.connector("nope")


# --------------------------------------------------------------------------- #
# The inference registry
# --------------------------------------------------------------------------- #


def test_every_inference_is_named_and_traceable():
    for entry in INFERENCES:
        assert entry["id"] and entry["topic"], entry
        assert entry["basis"], entry
        assert entry["why"], entry
        assert entry["change_it"], entry
        assert "blast_radius" in entry, entry


def test_the_inference_ids_are_unique():
    ids = [entry["id"] for entry in INFERENCES]
    assert len(ids) == len(set(ids))


def test_the_inference_endpoint_serves_the_registry_beside_the_sourced_quotes():
    body = describe_inferences()
    assert body["count"] == len(INFERENCES)
    assert "return_representation" in body["sourced_quotes"]
    assert body["sourced"]["success_codes"]["dataverse"] == [201, 204]
    assert body["sourced"]["success_codes"]["salesforce"] == [201]


def test_the_accepted_without_an_id_decision_is_recorded_and_explained():
    entry = next(e for e in INFERENCES if e["id"] == "accepted-without-an-id-is-a-failure")
    assert entry["value"]["state"] == "failed"
    assert entry["value"]["reason"] == "crm_id_absent"
    assert entry["value"]["needs_manual_update"] is True


def test_the_retry_ladder_decision_names_the_409_exclusion():
    entry = next(e for e in INFERENCES if e["id"] == "retry-ladder")
    assert "409" in entry["why"]
    assert 409 not in entry["value"]["retryable"]


def test_the_omitted_not_null_decision_is_recorded():
    entry = next(e for e in INFERENCES if e["id"] == "unresolved-sources-are-omitted-not-sent-null")
    assert entry["value"]["value"] == "the property is left out of the payload"


def test_the_blocked_rows_decision_keeps_the_extensibility_promise_honest():
    entry = next(e for e in INFERENCES if e["id"] == "blocked-rows-are-re-evaluated")
    assert "mapping row" in entry["why"]


# --------------------------------------------------------------------------- #
# HTTP: the whole surface, through this feature's own router
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def _shared_client(tmp_path_factory):
    """One application for the module. A fresh database for each test."""
    scratch = tmp_path_factory.mktemp("wf037-http")
    patch = pytest.MonkeyPatch()
    patch.setenv("DSR_DB_PATH", ":memory:")
    patch.setenv("DSR_AUDIT_DIR", str(scratch / "audit"))
    patch.setattr("dsr.api.FRONTEND_DIST", scratch / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    patch.undo()


@pytest.fixture()
def http(_shared_client, monkeypatch):
    """The shared application, over a database this test owns alone, with no socket.

    Every test that would send a create installs a scripted transport through the
    dependency override instead. The ``UrllibTransport`` patch is the backstop: a
    test that forgets to script one fails loudly rather than reaching the network.
    """

    def refuse(self, url, body, headers, timeout):
        raise AssertionError(f"a test must not open a socket; got {url}")

    monkeypatch.setattr(delivery.UrllibTransport, "post", refuse)
    db = AuditedDatabase()
    _shared_client.app.state.db = db
    _shared_client.app.state.store = RecordStore(db)
    _shared_client.app.dependency_overrides.clear()
    try:
        yield _shared_client
    finally:
        _shared_client.app.dependency_overrides.clear()
        db.close()


def _configure(http, room_id: str, **overrides: Any) -> dict[str, Any]:
    connector = http.post(
        f"{PREFIX}/connectors",
        params={"room_id": room_id, "actor": "dana"},
        json=connector_spec(**overrides),
    ).json()
    http.post(
        f"{PREFIX}/event-types", params={"actor": "dana"}, json={"event_type": "document_viewed"}
    )
    field_map = http.post(
        f"{PREFIX}/field-maps",
        params={"actor": "dana"},
        json=map_spec("document_viewed", connector["id"]),
    ).json()
    return {"connector": connector, "field_map": field_map}


def _room(http) -> str:
    return http.post("/api/records/room", json={"name": "HTTP room", "account": "Acme"}).json()[
        "id"
    ]


def _with_transport(http, results: list[CreateResult]) -> EngagementSync:
    """Install a scripted transport for the duration of one request.

    The engine is built per request from a dependency, so the dependency is the seam -
    there is nothing on ``app.state`` to replace, which is the point of not editing the
    shared app.
    """
    engine = EngagementSync(
        http.app.state.store,
        transport=Scripted(*results),
        backoff=0,
        sleep=lambda _s: None,
    )
    app.dependency_overrides[load_feature(MODULE_NAME).get_sync] = lambda: engine
    return engine


def _drop_transport() -> None:
    app.dependency_overrides.pop(load_feature(MODULE_NAME).get_sync, None)


# -- the contract, served as data --------------------------------------------- #


def test_the_vocabulary_route_serves_the_researched_contract(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["vendors"] == ["hubspot", "dataverse", "salesforce"]
    assert body["queue_states"] == ["pending", "synced", "failed", "blocked"]


def test_the_transforms_route_serves_the_registry(http):
    body = http.get(f"{PREFIX}/transforms").json()
    assert {row["name"] for row in body["transforms"]} == set(TRANSFORMS)


def test_the_inferences_route_serves_the_registry(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(INFERENCES)


# -- connectors --------------------------------------------------------------- #


def test_registering_a_connector_is_a_201_and_hides_its_token(http):
    room_id = _room(http)
    response = http.post(
        f"{PREFIX}/connectors",
        params={"room_id": room_id, "actor": "dana"},
        json=connector_spec(token="secret"),
    )
    assert response.status_code == 201
    assert "token" not in response.json()
    assert response.json()["has_token"] is True


def test_a_connector_with_an_unknown_vendor_is_a_422(http):
    response = http.post(
        f"{PREFIX}/connectors", json={"vendor": "pipedrive", "base_url": "https://x.test"}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_connector"


def test_a_connector_without_its_target_is_a_422(http):
    response = http.post(
        f"{PREFIX}/connectors", json={"vendor": "hubspot", "base_url": "https://x.test"}
    )
    assert response.status_code == 422
    assert "object" in response.json()["detail"]


def test_a_connector_can_be_read_patched_and_deleted(http):
    room_id = _room(http)
    created = http.post(
        f"{PREFIX}/connectors", params={"room_id": room_id}, json=connector_spec()
    ).json()
    assert http.get(f"{PREFIX}/connectors/{created['id']}").status_code == 200
    patched = http.patch(f"{PREFIX}/connectors/{created['id']}", json={"label": "Renamed"})
    assert patched.json()["label"] == "Renamed"
    assert http.delete(f"{PREFIX}/connectors/{created['id']}").status_code == 204
    assert http.get(f"{PREFIX}/connectors/{created['id']}").status_code == 422


def test_patching_a_connector_that_does_not_exist_is_a_422(http):
    assert http.patch(f"{PREFIX}/connectors/nope", json={"label": "x"}).status_code == 422


def test_the_connectors_route_lists_what_was_registered(http):
    room_id = _room(http)
    http.post(f"{PREFIX}/connectors", params={"room_id": room_id}, json=connector_spec())
    body = http.get(f"{PREFIX}/connectors").json()
    assert body["count"] == 1
    assert body["connectors"][0]["vendor"] == "hubspot"


# -- the event catalogue ------------------------------------------------------ #


def test_adding_an_event_type_is_a_201(http):
    response = http.post(f"{PREFIX}/event-types", json={"event_type": "cta_click"})
    assert response.status_code == 201
    assert response.json()["label"] == "Cta Click"


def test_an_event_type_with_no_name_is_a_422(http):
    response = http.post(f"{PREFIX}/event-types", json={"label": "nameless"})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_event_type"


def test_a_duplicate_event_type_is_a_422(http):
    http.post(f"{PREFIX}/event-types", json={"event_type": "cta_click"})
    assert http.post(f"{PREFIX}/event-types", json={"event_type": "cta_click"}).status_code == 422


def test_an_event_type_can_be_read_patched_and_deleted(http):
    created = http.post(f"{PREFIX}/event-types", json={"event_type": "cta_click"}).json()
    assert http.get(f"{PREFIX}/event-types/{created['id']}").json()["event_type"] == "cta_click"
    assert (
        http.patch(f"{PREFIX}/event-types/{created['id']}", json={"enabled": False}).json()[
            "enabled"
        ]
        is False
    )
    assert http.delete(f"{PREFIX}/event-types/{created['id']}").status_code == 204
    assert http.get(f"{PREFIX}/event-types/nope").status_code == 400


# -- field maps ---------------------------------------------------------------- #


def test_adding_a_field_map_is_a_201(http):
    room_id = _room(http)
    _configure(http, room_id)
    body = http.get(f"{PREFIX}/field-maps").json()
    assert body["count"] == 1
    assert body["field_maps"][0]["sync_key"]["target_property"] == "dsr_engagement_id"


def test_a_field_map_with_no_sync_key_is_a_422(http):
    response = http.post(
        f"{PREFIX}/field-maps",
        json={
            "event_type": "x",
            "connector_id": "c",
            "fields": [{"source": "type", "target": "t"}],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_field_map"


def test_a_field_map_with_no_connector_is_a_422(http):
    response = http.post(
        f"{PREFIX}/field-maps", json=map_spec("x", "").copy() | {"connector_id": ""}
    )
    assert response.status_code == 422
    assert "connector_id is required" in response.json()["detail"]


def test_field_maps_can_be_filtered_by_event_type_and_connector(http):
    room_id = _room(http)
    config = _configure(http, room_id)
    assert (
        http.get(f"{PREFIX}/field-maps", params={"event_type": "document_viewed"}).json()["count"]
        == 1
    )
    assert http.get(f"{PREFIX}/field-maps", params={"event_type": "other"}).json()["count"] == 0
    assert (
        http.get(f"{PREFIX}/field-maps", params={"connector_id": config["connector"]["id"]}).json()[
            "count"
        ]
        == 1
    )


def test_a_field_map_can_be_read_patched_and_deleted(http):
    room_id = _room(http)
    config = _configure(http, room_id)
    field_map_id = config["field_map"]["id"]
    assert http.get(f"{PREFIX}/field-maps/{field_map_id}").status_code == 200
    assert (
        http.patch(f"{PREFIX}/field-maps/{field_map_id}", json={"enabled": False}).json()["enabled"]
        is False
    )
    assert http.delete(f"{PREFIX}/field-maps/{field_map_id}").status_code == 204
    assert http.get(f"{PREFIX}/field-maps/{field_map_id}").status_code == 400


# -- steps 1 and 2 over HTTP --------------------------------------------------- #


def test_recording_an_engagement_is_a_201_and_returns_the_queue_row(http):
    room_id = _room(http)
    _configure(http, room_id)
    engine = _with_transport(http, [ok(201, json.dumps({"id": "501"}))])
    try:
        response = http.post(
            f"{PREFIX}/rooms/{room_id}/engagements", params={"actor": "dana"}, json=event_payload()
        )
    finally:
        _drop_transport()
    assert response.status_code == 201
    body = response.json()
    assert body["engagement"]["data"]["type"] == "document_viewed"
    assert body["result"]["state"] == "synced"
    assert body["result"]["crm_record_id"] == "501"
    assert engine.transport.count == 1


def test_recording_with_firing_deferred_leaves_the_row_pending(http):
    room_id = _room(http)
    _configure(http, room_id)
    engine = _with_transport(http, [])
    try:
        response = http.post(
            f"{PREFIX}/rooms/{room_id}/engagements",
            params={"actor": "dana", "fire_queue": "false"},
            json=event_payload(),
        )
    finally:
        _drop_transport()
    assert response.json()["queue"]["data"]["state"] == "pending"
    assert engine.transport.count == 0


def test_recording_an_event_with_no_type_is_a_422(http):
    room_id = _room(http)
    assert (
        http.post(f"{PREFIX}/rooms/{room_id}/engagements", json={"asset": "Deck"}).status_code
        == 422
    )


def test_recording_into_a_room_that_does_not_exist_is_a_404(http):
    response = http.post(f"{PREFIX}/rooms/nope/engagements", json=event_payload())
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_recording_on_an_unconfigured_room_still_stores_the_event_and_blocks_the_queue(http):
    """The buyer's action happened and the room is the system of record for it."""
    room_id = _room(http)
    response = http.post(
        f"{PREFIX}/rooms/{room_id}/engagements", params={"actor": "dana"}, json=event_payload()
    )
    assert response.status_code == 201
    assert response.json()["result"]["reason"] == "no_connector"
    assert response.json()["engagement"]["data"]["sync_state"] == "blocked"


def test_the_feed_says_where_the_crm_record_id_came_from(engine, room):
    """Three vendors answer that question differently, and a Dataverse id arrives in a
    header - so a rep cannot be told "is that really the row the CRM made" without it."""
    configured(
        engine,
        room["id"],
        connector={"vendor": "dataverse", "entity_set": "dsr_engagements", "object": ""},
    )
    engine.transport = Scripted(
        ok(
            204,
            "",
            {
                "OData-EntityId": "https://c.test/api/data/v9.2/dsr_engagements(00aa00aa-1111-2222-3333-444444444444)"
            },
        )
    )
    engine.record_event(room["id"], event_payload(), source=SOURCE)
    item = engine.feed(room["id"])["events"][0]
    assert item["crm_record_id_from"] == "header:OData-EntityId"
    assert item["crm_record_id_sourced"] is True


def test_the_feed_marks_an_unsourced_id_as_such(engine, room):
    """Salesforce's id location is not sourced, so the feed says so rather than implying it."""
    configured(
        engine, room["id"], connector={"vendor": "salesforce", "object": "Room_Engagement__c"}
    )
    engine.transport = Scripted(ok(201, json.dumps({"id": "001XX000001"})))
    engine.record_event(room["id"], event_payload(), source=SOURCE)
    item = engine.feed(room["id"])["events"][0]
    assert item["crm_record_id_from"] == "body:id"
    assert item["crm_record_id_sourced"] is False


def test_the_feed_route_shows_the_event_and_its_sync_state(http):
    room_id = _room(http)
    _configure(http, room_id)
    _with_transport(http, [ok(201, json.dumps({"id": "501"}))])
    try:
        http.post(
            f"{PREFIX}/rooms/{room_id}/engagements", params={"actor": "dana"}, json=event_payload()
        )
    finally:
        _drop_transport()
    body = http.get(f"{PREFIX}/rooms/{room_id}/engagements").json()
    assert body["count"] == 1
    assert body["summary"]["synced"] == 1
    assert body["events"][0]["crm_record_id"] == "501"


def test_the_feed_route_accepts_filters(http):
    room_id = _room(http)
    _configure(http, room_id)
    http.post(
        f"{PREFIX}/rooms/{room_id}/engagements",
        params={"fire_queue": "false"},
        json=event_payload(),
    )
    assert (
        http.get(f"{PREFIX}/rooms/{room_id}/engagements", params={"sync_state": "pending"}).json()[
            "count"
        ]
        == 1
    )
    assert (
        http.get(f"{PREFIX}/rooms/{room_id}/engagements", params={"type": "other"}).json()["count"]
        == 0
    )


def test_the_event_detail_route_carries_the_request_and_the_attempts(http):
    room_id = _room(http)
    _configure(http, room_id)
    _with_transport(http, [bad(429), bad(429), bad(429)])
    try:
        created = http.post(
            f"{PREFIX}/rooms/{room_id}/engagements", params={"actor": "dana"}, json=event_payload()
        ).json()
    finally:
        _drop_transport()
    body = http.get(f"{PREFIX}/rooms/{room_id}/engagements/{created['engagement']['id']}").json()
    assert body["sync_state"] == "failed"
    assert [entry["status"] for entry in body["sync_log"][0]["data"]["attempt_log"]] == [
        429,
        429,
        429,
    ]


def test_the_event_detail_route_is_a_404_for_an_unknown_event(http):
    room_id = _room(http)
    assert http.get(f"{PREFIX}/rooms/{room_id}/engagements/nope").status_code == 404


def test_the_readiness_route_reports_what_is_missing(http):
    room_id = _room(http)
    body = http.get(f"{PREFIX}/rooms/{room_id}/readiness").json()
    assert body["ready"] is False
    assert body["missing"][0]["reason"] == "no_connector"


def test_the_readiness_route_is_a_404_for_an_unknown_room(http):
    assert http.get(f"{PREFIX}/rooms/nope/readiness").status_code == 404


# -- the queue over HTTP ------------------------------------------------------- #


def test_the_queue_route_summarises_by_state(http):
    room_id = _room(http)
    _configure(http, room_id)
    http.post(
        f"{PREFIX}/rooms/{room_id}/engagements",
        params={"fire_queue": "false"},
        json=event_payload(),
    )
    body = http.get(f"{PREFIX}/rooms/{room_id}/queue").json()
    assert body["summary"]["pending"] == 1


def test_the_queue_route_can_be_filtered_by_state(http):
    room_id = _room(http)
    _configure(http, room_id)
    http.post(
        f"{PREFIX}/rooms/{room_id}/engagements",
        params={"fire_queue": "false"},
        json=event_payload(),
    )
    assert (
        http.get(f"{PREFIX}/rooms/{room_id}/queue", params={"state": "synced"}).json()["count"] == 0
    )


def test_the_queue_route_is_a_404_for_an_unknown_room(http):
    assert http.get(f"{PREFIX}/rooms/nope/queue").status_code == 404


def test_the_preview_route_writes_nothing_and_shows_the_request(http):
    room_id = _room(http)
    _configure(http, room_id)
    http.post(
        f"{PREFIX}/rooms/{room_id}/engagements",
        params={"fire_queue": "false"},
        json=event_payload(),
    )
    before = http.get("/api/audit", params={"limit": 1000}).json()["count"]
    body = http.post(f"{PREFIX}/rooms/{room_id}/queue/preview").json()
    assert body["counts"]["sendable"] == 1
    assert body["plans"][0]["request"]["body"]["properties"]["engagement_type"] == "document_viewed"
    assert http.get("/api/audit", params={"limit": 1000}).json()["count"] == before


def test_the_drain_route_fires_the_queue_and_reports_what_happened(http):
    room_id = _room(http)
    _configure(http, room_id)
    http.post(
        f"{PREFIX}/rooms/{room_id}/engagements",
        params={"fire_queue": "false"},
        json=event_payload(),
    )
    _with_transport(http, [ok(201, json.dumps({"id": "501"}))])
    try:
        body = http.post(f"{PREFIX}/rooms/{room_id}/queue/drain", params={"actor": "dana"}).json()
    finally:
        _drop_transport()
    assert body["counts"]["synced"] == 1
    assert body["results"][0]["crm_record_id"] == "501"


def test_the_endpoint_draining_an_unconfigured_room_is_the_distinct_428(http):
    room_id = _room(http)
    response = http.post(f"{PREFIX}/rooms/{room_id}/queue/drain")
    assert response.status_code == 428
    assert response.json()["error"] == "not_configured"


def test_draining_accepts_a_list_of_row_ids(http):
    room_id = _room(http)
    _configure(http, room_id)
    first = http.post(
        f"{PREFIX}/rooms/{room_id}/engagements",
        params={"fire_queue": "false"},
        json=event_payload(asset="A"),
    ).json()
    http.post(
        f"{PREFIX}/rooms/{room_id}/engagements",
        params={"fire_queue": "false"},
        json=event_payload(asset="B"),
    )
    _with_transport(http, [ok(201, json.dumps({"id": "1"}))])
    try:
        body = http.post(
            f"{PREFIX}/rooms/{room_id}/queue/drain", json={"queue_ids": [first["queue"]["id"]]}
        ).json()
    finally:
        _drop_transport()
    assert body["counts"]["synced"] == 1
    assert body["counts"]["skipped"] == 1


def test_the_retry_route_fires_one_blocked_row_again(http):
    room_id = _room(http)
    created = http.post(
        f"{PREFIX}/rooms/{room_id}/engagements", params={"actor": "dana"}, json=event_payload()
    ).json()
    assert created["result"]["reason"] == "no_connector"
    http.post(f"{PREFIX}/connectors", params={"room_id": room_id}, json=connector_spec())
    connector_id = http.get(f"{PREFIX}/connectors").json()["connectors"][0]["id"]
    http.post(f"{PREFIX}/field-maps", json=map_spec("document_viewed", connector_id))
    _with_transport(http, [ok(201, json.dumps({"id": "9"}))])
    try:
        body = http.post(
            f"{PREFIX}/rooms/{room_id}/queue/{created['queue']['id']}/retry",
            params={"actor": "dana"},
        ).json()
    finally:
        _drop_transport()
    assert body["previous_state"] == "blocked"
    assert body["state"] == "synced"
    assert body["crm_record_id"] == "9"


def test_retrying_a_synced_row_is_a_400(http):
    room_id = _room(http)
    _configure(http, room_id)
    _with_transport(http, [ok(201, json.dumps({"id": "1"}))])
    try:
        created = http.post(
            f"{PREFIX}/rooms/{room_id}/engagements", params={"actor": "dana"}, json=event_payload()
        ).json()
    finally:
        _drop_transport()
    response = http.post(f"{PREFIX}/rooms/{room_id}/queue/{created['queue']['id']}/retry")
    assert response.status_code == 400
    assert response.json()["error"] == "sync_error"


def test_retrying_an_unknown_row_is_a_400(http):
    room_id = _room(http)
    assert http.post(f"{PREFIX}/rooms/{room_id}/queue/nope/retry").status_code == 400


# -- the Sync log over HTTP ---------------------------------------------------- #


def test_the_sync_log_route_lists_the_writes_the_worker_made(http):
    room_id = _room(http)
    _configure(http, room_id)
    _with_transport(http, [ok(201, json.dumps({"id": "501"}))])
    try:
        http.post(
            f"{PREFIX}/rooms/{room_id}/engagements", params={"actor": "dana"}, json=event_payload()
        )
    finally:
        _drop_transport()
    body = http.get(f"{PREFIX}/sync-log", params={"room_id": room_id}).json()
    assert body["count"] == 1
    assert body["summary"] == {"synced": 1, "failed": 0, "needs_manual_update": 0}
    assert body["entries"][0]["data"]["vendor"] == "hubspot"


def test_the_sync_log_route_filters_and_summarises_only_what_it_returned(http):
    room_id = _room(http)
    _configure(http, room_id)
    _with_transport(http, [bad(403), ok(201, json.dumps({"id": "1"}))])
    try:
        http.post(
            f"{PREFIX}/rooms/{room_id}/engagements", params={"actor": "dana"}, json=event_payload()
        )
        http.post(
            f"{PREFIX}/rooms/{room_id}/engagements",
            params={"actor": "dana"},
            json=event_payload(asset="B"),
        )
    finally:
        _drop_transport()
    assert http.get(f"{PREFIX}/sync-log", params={"outcome": "failed"}).json()["count"] == 1
    assert http.get(f"{PREFIX}/sync-log", params={"needs_manual_update": True}).json()["count"] == 1
    assert http.get(f"{PREFIX}/sync-log", params={"vendor": "salesforce"}).json()["count"] == 0


def test_a_sync_log_entry_can_be_read_on_its_own(http):
    room_id = _room(http)
    _configure(http, room_id, token="demo-token")
    _with_transport(http, [ok(201, json.dumps({"id": "501"}))])
    try:
        http.post(
            f"{PREFIX}/rooms/{room_id}/engagements", params={"actor": "dana"}, json=event_payload()
        )
    finally:
        _drop_transport()
    entry = http.get(f"{PREFIX}/sync-log", params={"room_id": room_id}).json()["entries"][0]
    body = http.get(f"{PREFIX}/sync-log/{entry['id']}").json()
    assert body["data"]["crm_record_id"] == "501"
    assert body["data"]["request"]["headers"]["Authorization"] == "Bearer ***redacted***"


def test_an_unknown_sync_log_entry_is_a_400(http):
    assert http.get(f"{PREFIX}/sync-log/nope").status_code == 400


# -- the guarantee that reads never write -------------------------------------- #


def test_no_read_route_writes_a_row(http):
    room_id = _room(http)
    _configure(http, room_id)
    http.post(
        f"{PREFIX}/rooms/{room_id}/engagements",
        params={"fire_queue": "false"},
        json=event_payload(),
    )
    before = len(http.get("/api/audit", params={"limit": 1000}).json()["entries"])
    for path in (
        f"{PREFIX}/vocabulary",
        f"{PREFIX}/transforms",
        f"{PREFIX}/inferences",
        f"{PREFIX}/connectors",
        f"{PREFIX}/event-types",
        f"{PREFIX}/field-maps",
        f"{PREFIX}/rooms/{room_id}/engagements",
        f"{PREFIX}/rooms/{room_id}/queue",
        f"{PREFIX}/rooms/{room_id}/readiness",
        f"{PREFIX}/sync-log",
    ):
        assert http.get(path).status_code == 200, path
    assert http.post(f"{PREFIX}/rooms/{room_id}/queue/preview").status_code == 200
    after = len(http.get("/api/audit", params={"limit": 1000}).json()["entries"])
    assert after == before


# -- the audit-source rule ----------------------------------------------------- #


def _matches_registered_route(source: str, routes: list[dict[str, Any]]) -> bool:
    """Whether an audit ``source`` names a route the host actually mounted.

    ``POST /api/wf-037/rooms/{room_id}/queue/{queue_id}/retry`` is a template, so the
    recorded source has real ids in those positions. Each segment of the recorded path is
    matched against the template's, with ``{...}`` accepting anything - which is what a
    route's own path parameter does.
    """
    method, _, path = source.partition(" ")
    actual = [segment for segment in path.split("/") if segment]
    for route in routes:
        if method not in route["methods"]:
            continue
        template = [segment for segment in route["path"].split("/") if segment]
        if len(template) != len(actual):
            continue
        if all(
            expected.startswith("{") or expected == found
            for expected, found in zip(template, actual, strict=False)
        ):
            return True
    return False


def test_every_write_is_audited_with_the_route_that_served_it(http):
    room_id = _room(http)
    config = _configure(http, room_id)
    event_type_id = http.get(f"{PREFIX}/event-types").json()["event_types"][0]["id"]
    _with_transport(http, [ok(201, json.dumps({"id": "1"}))])
    try:
        created = http.post(
            f"{PREFIX}/rooms/{room_id}/engagements", params={"actor": "dana"}, json=event_payload()
        ).json()
    finally:
        _drop_transport()
    http.patch(
        f"{PREFIX}/connectors/{config['connector']['id']}",
        params={"actor": "dana"},
        json={"label": "New"},
    )
    http.patch(f"{PREFIX}/field-maps/{config['field_map']['id']}", json={"label": "New"})
    http.patch(f"{PREFIX}/event-types/{event_type_id}", json={"label": "New"})
    http.delete(f"{PREFIX}/connectors/{config['connector']['id']}", params={"actor": "dana"})
    http.delete(f"{PREFIX}/field-maps/{config['field_map']['id']}", params={"actor": "dana"})
    http.delete(f"{PREFIX}/event-types/{event_type_id}", params={"actor": "dana"})

    entries = http.get("/api/audit", params={"limit": 1000}).json()["entries"]
    ours = {
        entry["source"]
        for entry in entries
        if entry["source"] and entry["source"].split(" ")[1].startswith(PREFIX)
    }
    assert ours == {
        f"POST {PREFIX}/connectors",
        f"POST {PREFIX}/event-types",
        f"POST {PREFIX}/field-maps",
        f"POST {PREFIX}/rooms/{room_id}/engagements",
        f"PATCH {PREFIX}/connectors/{config['connector']['id']}",
        f"PATCH {PREFIX}/field-maps/{config['field_map']['id']}",
        f"PATCH {PREFIX}/event-types/{event_type_id}",
        f"DELETE {PREFIX}/connectors/{config['connector']['id']}",
        f"DELETE {PREFIX}/field-maps/{config['field_map']['id']}",
        f"DELETE {PREFIX}/event-types/{event_type_id}",
    }
    assert {entry["action"] for entry in entries if entry["source"] in ours} >= {
        "insert",
        "update",
        "delete",
    }
    assert created["engagement"]["id"]


def test_every_write_audit_row_names_a_route_the_app_serves(http):
    """The build brief's central guarantee, checked against the live route table.

    An audit row naming a path the app had stopped serving has shipped in this codebase
    before, and it is invisible until somebody tries to follow the row. All thirteen write
    routes are exercised, so a stale literal in any one of them fails here.
    """
    room_id = _room(http)
    config = _configure(http, room_id)
    event_type_id = http.get(f"{PREFIX}/event-types").json()["event_types"][0]["id"]

    # One write per route: the synchronous record, the deferred one, the drain, the retry.
    _with_transport(http, [ok(201, json.dumps({"id": "1"}))])
    try:
        http.post(
            f"{PREFIX}/rooms/{room_id}/engagements", params={"actor": "dana"}, json=event_payload()
        )
        blocked = http.post(
            f"{PREFIX}/rooms/{room_id}/engagements",
            params={"actor": "dana"},
            json=event_payload(type="cta_click"),
        ).json()
        pending = http.post(
            f"{PREFIX}/rooms/{room_id}/engagements",
            params={"actor": "dana", "fire_queue": "false"},
            json=event_payload(asset="Pending"),
        ).json()
    finally:
        _drop_transport()
    _with_transport(http, [ok(201, json.dumps({"id": "2"}))])
    try:
        http.post(f"{PREFIX}/rooms/{room_id}/queue/drain", params={"actor": "sam"})
    finally:
        _drop_transport()
    _with_transport(http, [ok(201, json.dumps({"id": "3"}))])
    try:
        http.post(
            f"{PREFIX}/rooms/{room_id}/queue/{blocked['queue']['id']}/retry",
            params={"actor": "dana"},
        )
    finally:
        _drop_transport()

    http.patch(f"{PREFIX}/connectors/{config['connector']['id']}", json={"label": "New"})
    http.patch(f"{PREFIX}/field-maps/{config['field_map']['id']}", json={"label": "New"})
    http.patch(f"{PREFIX}/event-types/{event_type_id}", json={"label": "New"})
    http.delete(f"{PREFIX}/connectors/{config['connector']['id']}", params={"actor": "dana"})
    http.delete(f"{PREFIX}/field-maps/{config['field_map']['id']}", params={"actor": "dana"})
    http.delete(f"{PREFIX}/event-types/{event_type_id}", params={"actor": "dana"})

    served = [
        route
        for feature in http.get("/api/features").json()["features"]
        for route in feature["routes"]
    ]
    entries = http.get("/api/audit", params={"limit": 1000}).json()["entries"]
    sources = {entry["source"] for entry in entries if entry["source"]}
    ours = {source for source in sources if source.split(" ")[1].startswith(PREFIX)}
    assert ours, f"no wf-037 write was audited at all; saw {sorted(sources)}"
    for source in sorted(ours):
        assert _matches_registered_route(source, served), (
            f"audit row names {source!r}, which is not a route this app serves"
        )
    # The three worker-driven writes are named too, so a stale drain or retry literal
    # cannot hide behind the configuration routes passing.
    assert f"POST {PREFIX}/rooms/{room_id}/queue/drain" in ours
    assert f"POST {PREFIX}/rooms/{room_id}/queue/{blocked['queue']['id']}/retry" in ours
    assert pending["queue"]["data"]["state"] == "pending"
    assert http.get(f"{PREFIX}/rooms/{room_id}/queue").json()["summary"]["synced"] == 2


def test_the_source_is_built_from_the_prefix_not_typed_out():
    """Rename the prefix and the recorded source follows it, because it is built from
    ``router.prefix`` rather than written as a literal."""
    module = load_feature(MODULE_NAME)
    source = Path(module.__file__).read_text(encoding="utf-8")
    for literal in (
        'source=f"POST {router.prefix}/connectors"',
        'source=f"POST {router.prefix}/event-types"',
        'source=f"POST {router.prefix}/field-maps"',
        'source=f"POST {router.prefix}/rooms/{room_id}/engagements"',
        'source=f"POST {router.prefix}/rooms/{room_id}/queue/drain"',
        'source=f"POST {router.prefix}/rooms/{room_id}/queue/{queue_id}/retry"',
        'source=f"PATCH {router.prefix}/connectors/{connector_id}"',
        'source=f"PATCH {router.prefix}/event-types/{event_type_id}"',
        'source=f"PATCH {router.prefix}/field-maps/{field_map_id}"',
        'source=f"DELETE {router.prefix}/connectors/{connector_id}"',
        'source=f"DELETE {router.prefix}/event-types/{event_type_id}"',
        'source=f"DELETE {router.prefix}/field-maps/{field_map_id}"',
    ):
        assert literal in source, literal
    assert f'prefix="{PREFIX}"' in source


def test_the_actor_reaches_the_audit_row(http):
    _room(http)
    http.post(f"{PREFIX}/event-types", params={"actor": "sam"}, json={"event_type": "cta_click"})
    entries = http.get("/api/audit", params={"actor": "sam", "limit": 1000}).json()["entries"]
    assert any(entry["source"] == f"POST {PREFIX}/event-types" for entry in entries)


def test_source_is_required_rather_than_defaulted():
    """A caller that forgets to pass ``source`` is a type error, not a wrong audit row."""
    for name in (
        "save_connector",
        "update_connector",
        "delete_connector",
        "save_event_type",
        "update_event_type",
        "delete_event_type",
        "save_field_map",
        "update_field_map",
        "delete_field_map",
        "record_engagement",
        "enqueue",
        "mark_synced",
        "mark_failed",
        "mark_blocked",
        "log_attempt",
    ):
        parameter = inspect.signature(getattr(SyncBook, name)).parameters["source"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, (
            f"{name}: source must be keyword-only"
        )
        assert parameter.default is inspect.Parameter.empty, f"{name}: source must be required"


# -- the guard rails ----------------------------------------------------------- #


def test_the_domain_never_opens_a_database_of_its_own():
    """Every read and write goes through the audited store. Nothing here imports sqlite3 or
    constructs a connection, and a feature that did could bypass the audit log the product
    is built on."""
    package = Path(__file__).resolve().parents[1] / "dsr" / "crm_engagement"
    for module in package.glob("*.py"):
        text = module.read_text(encoding="utf-8")
        assert "import sqlite3" not in text, f"{module.name} imports sqlite3"
        assert "sqlite3.connect" not in text, f"{module.name} opens its own connection"
    feature = load_feature(MODULE_NAME)
    assert "sqlite3" not in Path(feature.__file__).read_text(encoding="utf-8")


def test_the_feature_module_does_not_import_the_app():
    module = load_feature(MODULE_NAME)
    text = Path(module.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in text
    assert "import dsr.api" not in text


def test_no_module_here_adds_a_table_or_a_column():
    """Schema flexibility is a hard requirement: payloads are arbitrary JSON in
    ``records.data`` and a team adding a field must not need coordination with anyone."""
    package = Path(__file__).resolve().parents[1] / "dsr" / "crm_engagement"
    for module in list(package.glob("*.py")) + [Path(load_feature(MODULE_NAME).__file__)]:
        text = module.read_text(encoding="utf-8").upper()
        for statement in (
            "CREATE TABLE",
            "ALTER TABLE",
            "ADD COLUMN",
            "DROP TABLE",
            "SCHEMA_VERSION",
        ):
            assert statement not in text, f"{module.name} mentions {statement}"


def test_the_feature_mounts_without_a_route_collision():
    from dsr.features import REGISTRY

    record = REGISTRY.by_id("wf-037-log-a-single-buyer-engagement-event-in")
    assert record is not None
    assert record.prefix == PREFIX
    assert record.loaded is True
    assert len(record.routes) == 28
    # The host refuses a concrete (method, path) clash at load time, so reaching this
    # point with a router mounted at all is half the guarantee. The other half is
    # that none of this feature's concrete paths is claimed by another feature.
    mine = {(method, route["path"]) for route in record.routes for method in route["methods"]}
    others = {
        (method, route["path"])
        for feature in REGISTRY.features
        if feature.id != record.id
        for route in feature.routes
        for method in route["methods"]
    }
    assert mine and not (mine & others), sorted(mine & others)


def test_the_feature_registers_its_own_error_types_and_no_others():
    module = load_feature(MODULE_NAME)
    assert sorted(exc.__name__ for exc in module.EXCEPTION_HANDLERS) == [
        "EngagementSyncError",
        "InvalidConnector",
        "InvalidEventType",
        "InvalidFieldMap",
        "SyncNotConfigured",
        "UnknownRoom",
    ]


def test_no_claim_is_made_on_a_type_the_core_already_handles():
    from dsr.db.audited import AuditError, RecordNotFound

    module = load_feature(MODULE_NAME)
    assert RecordNotFound not in module.EXCEPTION_HANDLERS
    assert AuditError not in module.EXCEPTION_HANDLERS


def test_the_feature_id_matches_the_brief():
    assert (
        load_feature(MODULE_NAME).FEATURE["id"] == "wf-037-log-a-single-buyer-engagement-event-in"
    )
    assert load_feature(MODULE_NAME).FEATURE["ticket"] == "WF-037"


def test_the_every_collection_this_feature_writes_is_namespaced():
    """A collection is shared state; a feature that claimed a generic name would collide
    with the eleven already on main."""
    mine = {
        queue_module.CONNECTOR_COLLECTION,
        queue_module.EVENT_TYPE_COLLECTION,
        queue_module.FIELD_MAP_COLLECTION,
        queue_module.ENGAGEMENT_COLLECTION,
        queue_module.QUEUE_COLLECTION,
        queue_module.SYNC_LOG_COLLECTION,
    }
    assert all(name.startswith("crm_") for name in mine)
    assert len(mine) == 6


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def _seed(tmp_path):
    from datetime import datetime, timezone

    module = load_feature(MODULE_NAME)
    db = AuditedDatabase(tmp_path / "seed.db")
    rooms = [
        (
            db.create("room", {"name": name, "account": account, "owner": owner}, source="seed")[
                "id"
            ],
            account,
        )
        for name, account, owner in (
            ("Northwind", "Northwind Traders", "dana"),
            ("Contoso", "Contoso Health", "sam"),
            ("Fabrikam", "Fabrikam Logistics", "dana"),
            ("Adventure", "Adventure Works", "sam"),
        )
    ]
    summary = module.seed(
        db, {"room_ids": rooms, "now": datetime(2026, 9, 26, tzinfo=timezone.utc), "rng": None}
    )
    return db, module.SyncBook(RecordStore(db)), rooms, summary


def test_the_seeder_returns_a_description_of_the_interesting_states(tmp_path):
    _db, _book, _rooms, summary = _seed(tmp_path)
    assert "synced" in summary
    assert "failed" in summary
    assert "blocked" in summary


def test_the_seeder_produces_a_synced_row_with_its_id_from_the_response_body(tmp_path):
    _db, book, rooms, _summary = _seed(tmp_path)
    rows = [
        row for row in book.queue_rows(rooms[0][0], limit=50) if row["data"]["state"] == "synced"
    ]
    assert rows
    assert rows[0]["data"]["crm_record_id_from"] == "body:id"


def test_the_seeder_produces_a_synced_dataverse_row_with_the_id_from_the_header(tmp_path):
    """The researched case: a 204 with no body, and the id in ``OData-EntityId``."""
    _db, book, rooms, _summary = _seed(tmp_path)
    rows = [
        row
        for row in book.queue_rows(rooms[1][0], limit=50)
        if row["data"].get("crm_record_id_from") == "header:OData-EntityId"
    ]
    assert len(rows) == 1
    assert rows[0]["data"]["http_status"] == 204
    assert rows[0]["data"]["crm_record_id"].startswith("00aa00aa-")


def test_the_seeder_produces_a_row_whose_mapped_property_was_omitted_and_flagged(tmp_path):
    """[sourced] the room "flags ... unsupported option values before any data is written" -
    and the other properties still go."""
    _db, book, rooms, _summary = _seed(tmp_path)
    flagged = [
        row
        for row in book.queue_rows(rooms[0][0], limit=50)
        if any(f["code"] == "unmapped_option" for f in row["data"].get("findings") or [])
    ]
    assert len(flagged) == 1
    properties = flagged[0]["data"]["request"]["body"]["properties"]
    assert "lifecyclestage" not in properties
    assert properties["dsr_engagementtype"] == "document_viewed"
    assert properties["email"] == "a.buyer@northwind.example"


def test_the_seeder_produces_a_failure_whose_annotations_reached_the_sync_log(tmp_path):
    _db, book, rooms, _summary = _seed(tmp_path)
    entries = [entry for entry in book.sync_log(rooms[1][0], outcome="failed", limit=50)]
    assert len(entries) == 1
    annotations = entries[0]["data"]["vendor_error"]["annotations"]
    assert annotations and annotations[0]["target"] == "dsr_buyeremail"


def test_the_seeder_produces_a_soft_finding_that_was_shipped_anyway(tmp_path):
    _db, book, rooms, _summary = _seed(tmp_path)
    rows = [
        row
        for row in book.queue_rows(rooms[1][0], limit=50)
        if any(f["code"] == "email_without_at" for f in row["data"].get("findings") or [])
    ]
    assert len(rows) == 1
    assert rows[0]["data"]["request"]["body"]["dsr_buyeremail"] == "procurement (contoso)"


def test_the_seeder_produces_a_sync_key_collision_that_needs_a_human(tmp_path):
    _db, book, rooms, _summary = _seed(tmp_path)
    rows = [
        row
        for row in book.queue_rows(rooms[2][0], limit=50)
        if row["data"].get("failure_reason") == "sync_key_collision"
    ]
    assert len(rows) == 1
    assert rows[0]["data"]["needs_manual_update"] is True
    assert rows[0]["data"]["attempts"] == 1


def test_the_seeder_produces_a_write_that_was_retried_and_still_failed(tmp_path):
    _db, book, rooms, _summary = _seed(tmp_path)
    rows = [
        row
        for row in book.queue_rows(rooms[3][0], limit=50)
        if row["data"].get("attempt_statuses") == [429, 403]
    ]
    assert len(rows) == 1
    assert rows[0]["data"]["needs_manual_update"] is True


def test_the_seeder_produces_a_create_accepted_with_no_id_at_all(tmp_path):
    """The gap the research admits about Salesforce, reported rather than marked synced."""
    _db, book, rooms, _summary = _seed(tmp_path)
    rows = [
        row
        for row in book.queue_rows(rooms[3][0], limit=50)
        if row["data"].get("failure_reason") == "crm_id_absent"
    ]
    assert len(rows) == 1
    assert rows[0]["data"]["http_status"] == 201
    assert rows[0]["data"]["crm_record_id"] is None


def test_the_seeder_produces_a_blocked_row_for_an_unmapped_event_type(tmp_path):
    _db, book, rooms, _summary = _seed(tmp_path)
    rows = [
        row
        for row in book.queue_rows(rooms[0][0], limit=50)
        if row["data"].get("block_reason") == "event_type_unmapped"
    ]
    assert len(rows) == 1


def test_the_seeder_never_stores_the_demo_token(tmp_path):
    _db, book, rooms, _summary = _seed(tmp_path)
    for room_id, _account in rooms:
        for entry in book.sync_log(room_id, limit=50):
            assert "demo-token-not-real" not in json.dumps(entry, default=str)


def test_the_seeder_leaves_the_installation_default_switched_off(tmp_path):
    """So a room with no connector of its own reports a setup problem rather than nothing."""
    _db, book, _rooms, _summary = _seed(tmp_path)
    unscoped = [row for row in book.connectors() if not row.get("room_id")]
    assert len(unscoped) == 1
    assert unscoped[0]["enabled"] is False


def test_the_seeder_runs_the_real_engine_rather_than_writing_rows_by_hand(tmp_path):
    """Every queued row has a matching Sync log entry, which only a real send produces."""
    _db, book, rooms, _summary = _seed(tmp_path)
    for room_id, _account in rooms:
        queued = book.queue_rows(room_id, limit=50)
        logged = {entry["data"]["queue_id"] for entry in book.sync_log(room_id, limit=50)}
        for row in queued:
            if row["data"]["state"] in ("synced", "failed"):
                assert row["id"] in logged, row["data"]["state"]


def test_the_seeder_never_opens_a_socket(tmp_path, monkeypatch):
    import urllib.request as request_module

    def refuse(*args, **kwargs):
        raise AssertionError("the seeder must not open a socket")

    monkeypatch.setattr(request_module, "urlopen", refuse)
    _db, _book, _rooms, summary = _seed(tmp_path)
    assert "events" in summary


def test_the_seeder_degrades_rather_than_raising_when_there_are_no_rooms(tmp_path):
    from datetime import datetime, timezone

    module = load_feature(MODULE_NAME)
    db = AuditedDatabase(tmp_path / "empty.db")
    summary = module.seed(db, {"room_ids": [], "now": datetime.now(timezone.utc), "rng": None})
    assert "no rooms to scope them to" in summary
    db.close()
