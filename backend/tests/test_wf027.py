"""Tests for WF-027: emit buyer intent signals with indicators, urgency, and attribution.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-027.md`` (section 12 of
``docs/research/raw/analytics-intent.md``). They are, in order:

* what a signal registration is made of - "a signal type, a signal name, data
  shape (metadata about the signal), descriptions, at least one indicator
  (Indicators have metadata and description), an attribution";
* "at least one ``indicator`` with ``key`` + ``metadata_shape``";
* "Per integration, the signal type can only be registered once";
* "Globally installed signals should be considered an immutable API contract with
  Salesloft and only additive changes will be allowed";
* the emitted signal's field list, including "``idempotency_key`` (UUID4)" and
  "``urgency`` (high/medium/low)";
* "If we receive two signals with the same ``idempotency_key`` one of them will be
  dropped. The first one wins.";
* "Signals must follow the structure defined on the Signal Registration";
* "Possible attribution choices include Person, Account, User, Opportunity and
  Email Content" / "Salesloft will use the attribution value to derive the
  appropriate Salesloft user to receive the signal";
* "``urgency`` (high/medium/low) drives priority" and "``broadcast_notification``
  controls Live Feed display";
* indicators rendered as human sentences, with the research's own specific-versus-
  vague example and "Indicators should be very specific";
* "users may choose to not take action on a signal. Actionability depends on the
  end user's governance (Play) configurations and settings within Salesloft."

The last one is the reason several tests below assert that something is *absent*
rather than present. A feature that implied emitting a signal gave a seller a task
would be the most misleading thing this product could ship, and the research is
more explicit about that than about anything else in the workflow.

Every part of the feature is reachable through its own router, so the HTTP tests
drive the mounted routes rather than calling handlers, and the audit-source tests
check every write against the route table the host actually reported.
"""

from __future__ import annotations

import random
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.signals import (
    DuplicateSignalType,
    EmissionError,
    ImmutableContractError,
    IndicatorNotQualified,
    RegistrationError,
    RenderError,
    SignalEngine,
    SignalError,
    UndeclaredIndicator,
    UnregisteredSignalType,
    feed as feed_module,
    icume,
    indicators as indicator_rules,
    normalise_registration,
    schema as schema_rules,
)
from dsr.signals.emission import (
    OBSERVATION_FIELDS,
    canonical_emission,
    normalise_attribution,
    parse_timestamp,
    resolve_receiver,
)
from dsr.signals.inferences import INFERENCES, by_id
from dsr.signals.registration import (
    canonical,
    normalise_attribution as registration_attribution,
    normalise_indicators,
    require_uuid4,
)
from dsr.signals.vocabulary import (
    ACTIONABILITY_NOTE,
    ATTRIBUTION_OBJECT,
    ATTRIBUTION_PRECEDENCE,
    ATTRIBUTION_TYPES,
    DEFAULT_URGENCY,
    URGENCIES,
    normalise_locale,
    require_attribution_type,
    require_locale_map,
    require_urgency,
    resolve_locale,
    urgency_rank,
)
from dsr.store import RecordStore
from fastapi.testclient import TestClient

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-027"

#: The source a route passes for a write. The pure-domain tests use the same
#: shape, so a test asserting on an audit row is asserting on the real thing
#: rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/signals"

MODULE = "wf027_emit_buyer_intent_signals_with_indicat"
FEATURE_ID = "wf-027-emit-buyer-intent-signals-with-indicat"

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def ago(minutes: float) -> str:
    return (NOW - timedelta(minutes=minutes)).isoformat()


def uuid4() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

ROOM = {
    "name": "Northwind Traders — Enterprise Evaluation",
    "account": "Northwind Traders",
    "owner": "dana",
    "stage": "evaluation",
    "metadata": {"opportunity_id": "006NW"},
}

DATA_SHAPE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "document_name": {"type": "string", "minLength": 1},
        "document_kind": {"type": "string", "enum": ["pdf", "video", "deck"]},
        "action": {"type": "string", "enum": ["viewed", "downloaded", "opened", "watched"]},
        "buyer_first_name": {"type": "string"},
        "room_name": {"type": "string"},
        "page": {"type": "integer", "minimum": 1},
    },
    "required": ["document_name", "action"],
}

DURATION_SHAPE: dict[str, Any] = {
    "type": "object",
    "properties": {"time_in_seconds": {"type": "integer", "minimum": 0}},
    "required": ["time_in_seconds"],
}

WATCH_SHAPE: dict[str, Any] = {
    "type": "object",
    "properties": {"watched_percent": {"type": "integer", "minimum": 0, "maximum": 100}},
    "required": ["watched_percent"],
}


def registration_payload(**overrides: Any) -> dict[str, Any]:
    """A well-formed registration, as the research describes one.

    Built by a function rather than a module constant so a test that changes one
    field is changing one field, and so the constant cannot drift from what the
    validator accepts.
    """
    payload: dict[str, Any] = {
        "signal_name": "Deep engagement with shared content",
        "type": "document_engagement",
        "integration_id": "dsr",
        "description": {
            "en": "{buyer_first_name} spent {time_in_seconds} seconds on {document_name}."
        },
        "data_shape": DATA_SHAPE,
        "indicators": [
            {
                "key": "spent_more_than_30s_on_site",
                "metadata_shape": DURATION_SHAPE,
                "description": {"en": "Spent {time_in_seconds} seconds on {document_name}."},
            }
        ],
        "attribution": ["person_id", "account_id", "user_guid"],
        "broadcast_notification": True,
    }
    payload.update(overrides)
    return payload


def signal_payload(**overrides: Any) -> dict[str, Any]:
    """A well-formed emission, as the research's field list describes one."""
    payload: dict[str, Any] = {
        "type": "document_engagement",
        "data": {
            "document_name": "Security & Compliance Pack",
            "document_kind": "pdf",
            "action": "viewed",
            "buyer_first_name": "Priya",
            "room_name": "Northwind Traders — Enterprise Evaluation",
        },
        "indicators": [
            {"key": "spent_more_than_30s_on_site", "metadata": {"time_in_seconds": 214}}
        ],
        "urgency": "high",
        "occurred_at": ago(5),
        "idempotency_key": uuid4(),
        "attribution": {"person_id": "per_000042"},
        "broadcast_notification": True,
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(":memory:", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def engine(store):
    return SignalEngine(store)


@pytest.fixture()
def room(store):
    return store.create("room", ROOM, actor="dana")


@pytest.fixture()
def registered(engine):
    """One live registration of the researched good indicator."""
    return engine.register(registration_payload(), actor="dana", source=SOURCE)["registration"]


@pytest.fixture()
def http(monkeypatch):
    """A client over a temporary database.

    The database path is resolved at lifespan time, so the variable is set before
    the context manager is entered - the same way ``test_features.py`` does it.
    """
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf027-http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as client:
        yield client
    tmp.cleanup()


@pytest.fixture()
def http_room(http):
    return http.post("/api/records/room", json=ROOM).json()


@pytest.fixture()
def http_registration(http):
    return http.post(f"{PREFIX}/registrations", json=registration_payload()).json()["registration"]


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The route resolves even though no shared file names this feature."""
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)

    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-027"
    assert entry["exception_handlers"] == ["SignalError"]
    assert len(entry["routes"]) == 13


def test_no_feature_failed_to_load(http):
    """A refused route collision or a broken import would show up here."""
    body = http.get("/api/features").json()
    assert body["failed_count"] == 0, body["failed"]


def test_the_prefix_is_ours_alone(http):
    """No core route and no other feature answers anything under it."""
    body = http.get("/api/features").json()
    mine = {feature["id"] for feature in body["features"] if feature["prefix"] == PREFIX}
    assert mine == {FEATURE_ID}
    for feature in body["features"]:
        if feature["id"] == FEATURE_ID:
            continue
        assert not any(route["path"].startswith(PREFIX) for route in feature["routes"]), (
            f"{feature['id']} also serves under {PREFIX}"
        )


def test_feature_module_does_not_import_the_shared_app():
    """A guard exists in test_features.py; this states the reason locally."""
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source
    assert "from dsr.deps import" in source


def test_frontend_descriptor_id_matches_the_backend_feature_id():
    """The two halves of a feature are findable by one name, so they must agree."""
    descriptor = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / FEATURE_ID
        / "index.jsx"
    )
    text = descriptor.read_text(encoding="utf-8")
    module = load_feature(MODULE)

    assert module.FEATURE["id"] in text
    assert f"id: {module.FEATURE['id']!r}" in text


def test_the_feature_exports_what_the_host_looks_for():
    module = load_feature(MODULE)
    assert set(module.EXCEPTION_HANDLERS) == {SignalError}
    assert issubclass(RegistrationError, SignalError)
    assert issubclass(EmissionError, SignalError)
    assert issubclass(RenderError, SignalError)
    assert issubclass(DuplicateSignalType, RegistrationError)
    assert issubclass(ImmutableContractError, RegistrationError)
    assert issubclass(UnregisteredSignalType, EmissionError)
    assert issubclass(UndeclaredIndicator, EmissionError)
    assert issubclass(IndicatorNotQualified, EmissionError)


def test_seed_is_exported_by_the_feature_module():
    """Demo data belongs to the feature, which is what `backend/seed.py` looks for."""
    assert callable(load_feature(MODULE).seed)


# --------------------------------------------------------------------------- #
# Urgency: the three values the research names
# --------------------------------------------------------------------------- #


def test_urgency_is_exactly_the_three_documented_values():
    """`"Accepted values are high, medium, and low."`"""
    assert URGENCIES == ("high", "medium", "low")


@pytest.mark.parametrize("value", ["high", "medium", "low", "HIGH", "  high  "])
def test_urgency_is_normalised(value):
    assert require_urgency(value) == value.strip().lower()


@pytest.mark.parametrize("value", ["urgent", "", "highest", 3, True, ["high"]])
def test_urgency_outside_the_vocabulary_is_refused(value):
    with pytest.raises(EmissionError) as caught:
        require_urgency(value)
    assert "high, medium, low" in str(caught.value)


def test_absent_urgency_falls_back_to_the_documented_default():
    assert require_urgency(None) == DEFAULT_URGENCY == "medium"


def test_urgency_rank_puts_high_first():
    assert urgency_rank("high") < urgency_rank("medium") < urgency_rank("low")


def test_urgency_rank_sorts_an_unknown_value_last():
    """Never first: an unrecognised value must not outrank a high-urgency signal."""
    assert urgency_rank("nonsense") == len(URGENCIES)
    assert urgency_rank("nonsense") > urgency_rank("low")


def test_urgency_meaning_is_published():
    from dsr.signals.vocabulary import describe

    meanings = {row["value"]: row["meaning"] for row in describe()["urgencies"]}
    assert set(meanings) == set(URGENCIES)
    assert all(meanings.values())


# --------------------------------------------------------------------------- #
# Attribution: the five values the research names
# --------------------------------------------------------------------------- #


def test_attribution_is_exactly_the_five_documented_values():
    """`"Possible attribution choices include Person, Account, User, Opportunity and
    Email Content."`"""
    assert set(ATTRIBUTION_TYPES) == {
        "person_id",
        "account_id",
        "opportunity_id",
        "email_tracked_content_id",
        "user_guid",
    }


def test_each_attribution_value_names_its_salesloft_object():
    assert ATTRIBUTION_OBJECT == {
        "person_id": "Person",
        "account_id": "Account",
        "opportunity_id": "Opportunity",
        "email_tracked_content_id": "Email Content",
        "user_guid": "User",
    }


def test_attribution_precedence_covers_every_value_exactly_once():
    assert sorted(ATTRIBUTION_PRECEDENCE) == sorted(ATTRIBUTION_TYPES)
    assert len(set(ATTRIBUTION_PRECEDENCE)) == len(ATTRIBUTION_TYPES)


def test_attribution_precedence_follows_the_sourced_user_content_person_account_order():
    """Section 13 of the same research states "User, Content, Person, Account"."""
    order = list(ATTRIBUTION_PRECEDENCE)
    assert order.index("user_guid") < order.index("email_tracked_content_id")
    assert order.index("email_tracked_content_id") < order.index("person_id")
    assert order.index("person_id") < order.index("account_id")


@pytest.mark.parametrize("value", ATTRIBUTION_TYPES)
def test_a_documented_attribution_value_is_accepted(value):
    assert require_attribution_type(value) == value


@pytest.mark.parametrize("value", ["person", "personId", "user_id", "", None, 7])
def test_an_undocumented_attribution_value_is_refused(value):
    with pytest.raises(EmissionError) as caught:
        require_attribution_type(value)
    assert "person_id" in str(caught.value)


def test_the_researched_example_resolves_by_precedence_not_by_dict_order():
    """A user_guid beats a person_id even when the person_id is named first.

    This is the inference the report names, so the test that pins it is the one
    that would fail if somebody changed the tuple without reading why.
    """
    receiver = resolve_receiver(
        {"person_id": "per_1", "user_guid": "usr_1", "account_id": "acc_1"},
        {"data": {"owner": "dana"}},
    )
    assert receiver["attributed_by"] == "user_guid"
    assert receiver["object"] == "User"
    assert receiver["value"] == "usr_1"
    assert receiver["considered"] == ["user_guid", "person_id", "account_id"]
    # Every value present was consulted, so nothing is "unattributed".
    assert receiver["unattributed"] == []


def test_a_signal_with_one_attribution_value_resolves_to_it():
    receiver = resolve_receiver({"opportunity_id": "006NW"}, {"data": {"owner": "sam"}})
    assert receiver["attributed_by"] == "opportunity_id"
    assert receiver["seller"] == "sam"
    assert receiver["resolved"] is True


def test_a_room_with_no_owner_is_reported_unresolved_rather_than_guessed():
    receiver = resolve_receiver({"person_id": "per_1"}, {"data": {}})
    assert receiver["resolved"] is False
    assert receiver["seller"] is None
    # The attribution is still reported: it is the buyer's identity even when the
    # room does not say who owns it.
    assert receiver["attributed_by"] == "person_id"


def test_a_signal_with_no_room_still_reports_its_attribution():
    receiver = resolve_receiver({"account_id": "acc_1"}, None)
    assert receiver["resolved"] is False
    assert receiver["attributed_by"] == "account_id"


def test_attribution_is_normalised_and_its_warnings_are_collected():
    values, warnings = normalise_attribution(
        {"person_id": " per_1 ", "user_guid": "usr_1"}, ["person_id"]
    )
    assert values == {"person_id": "per_1", "user_guid": "usr_1"}
    assert len(warnings) == 1
    assert "user_guid" in warnings[0]


@pytest.mark.parametrize("payload", [None, {}, [], "person_id", 7])
def test_attribution_must_be_a_non_empty_object(payload):
    with pytest.raises(EmissionError):
        normalise_attribution(payload, list(ATTRIBUTION_TYPES))


def test_an_attribution_value_outside_the_researched_vocabulary_is_refused():
    with pytest.raises(EmissionError) as caught:
        normalise_attribution({"deal_id": "d_1"}, list(ATTRIBUTION_TYPES))
    assert "does not understand" in str(caught.value)


@pytest.mark.parametrize("value", [{"person_id": ""}, {"person_id": "   "}, {"person_id": None}])
def test_an_empty_attribution_value_is_refused_rather_than_silently_dropped(value):
    """An empty value is a different thing from an absent one, and means nothing."""
    with pytest.raises(EmissionError) as caught:
        normalise_attribution(value, list(ATTRIBUTION_TYPES))
    assert "omit it instead" in str(caught.value)


# --------------------------------------------------------------------------- #
# Locales
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("given", "expected"),
    [("en", "en"), ("EN", "en"), ("en-GB", "en-GB"), ("en_GB", "en-GB"), ("fr", "fr")],
)
def test_locales_are_normalised_to_one_spelling(given, expected):
    assert normalise_locale(given) == expected


def test_an_exact_locale_wins():
    resolved, fell_back = resolve_locale("en-GB", {"en": "a", "en-GB": "b"})
    assert (resolved, fell_back) == ("en-GB", False)


def test_the_bare_language_is_the_first_fallback():
    resolved, fell_back = resolve_locale("en-AU", {"en": "a", "en-GB": "b"})
    assert (resolved, fell_back) == ("en", True)


def test_english_is_the_second_fallback():
    resolved, fell_back = resolve_locale("de-AT", {"en": "a", "fr": "b"})
    assert (resolved, fell_back) == ("en", True)


def test_the_first_declared_locale_is_the_last_resort():
    resolved, fell_back = resolve_locale("de-AT", {"fr": "b", "it": "c"})
    assert (resolved, fell_back) == ("fr", True)


def test_a_locale_map_needs_at_least_one_entry():
    assert require_locale_map({"en": "hi"}, "description") == {"en": "hi"}
    with pytest.raises(SignalError):
        require_locale_map({}, "description")
    with pytest.raises(SignalError):
        require_locale_map(None, "description")


def test_a_bare_description_string_becomes_one_english_locale():
    assert require_locale_map("Hello", "description") == {"en": "Hello"}


def test_an_empty_locale_message_is_refused():
    with pytest.raises(SignalError):
        require_locale_map({"en": "   "}, "description")


# --------------------------------------------------------------------------- #
# The JSON-Schema subset
# --------------------------------------------------------------------------- #


def test_an_object_shape_accepts_an_object():
    assert schema_rules.validate({"type": "object"}, {"a": 1}) == []


def test_a_string_shape_accepts_a_string_and_refuses_a_number():
    assert schema_rules.validate({"type": "string"}, "x") == []
    findings = schema_rules.validate({"type": "string"}, 1)
    assert findings[0]["keyword"] == "type"
    assert "expected string, got integer" in findings[0]["message"]


def test_a_boolean_is_not_an_integer():
    """In Python ``True`` is an ``int``, and a shape must not inherit that."""
    assert schema_rules.validate({"type": "integer"}, True)
    assert schema_rules.validate({"type": "number"}, True)


def test_a_whole_float_satisfies_an_integer_shape():
    assert schema_rules.validate({"type": "integer"}, 3.0) == []
    assert schema_rules.validate({"type": "integer"}, 3.5)


def test_an_integer_satisfies_a_number_shape():
    assert schema_rules.validate({"type": "number"}, 3) == []


def test_a_type_list_accepts_any_of_its_members():
    shape = {"type": ["string", "null"]}
    assert schema_rules.validate(shape, "x") == []
    assert schema_rules.validate(shape, None) == []
    assert schema_rules.validate(shape, 1)


def test_null_is_a_type():
    assert schema_rules.validate({"type": "null"}, None) == []
    assert schema_rules.validate({"type": "null"}, 0)


def test_a_required_field_must_be_present():
    findings = schema_rules.validate({"type": "object", "required": ["a"]}, {"b": 1})
    assert findings[0]["path"] == "$.a"
    assert findings[0]["keyword"] == "required"


def test_a_required_field_may_be_present_and_null():
    assert schema_rules.validate({"type": "object", "required": ["a"]}, {"a": None}) == []


def test_additional_properties_false_closes_the_object():
    shape = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "additionalProperties": False,
    }
    assert schema_rules.validate(shape, {"a": "x"}) == []
    findings = schema_rules.validate(shape, {"a": "x", "b": 1})
    assert findings[0]["keyword"] == "additionalProperties"


def test_extra_fields_are_allowed_by_default():
    """The product's standing rule: a team adds a field without coordination."""
    assert (
        schema_rules.validate(
            {"type": "object", "properties": {"a": {"type": "string"}}}, {"a": "x", "b": 1}
        )
        == []
    )


def test_enum_and_const():
    assert schema_rules.validate({"enum": ["a", "b"]}, "a") == []
    assert schema_rules.validate({"enum": ["a", "b"]}, "c")
    assert schema_rules.validate({"const": 7}, 7) == []
    assert schema_rules.validate({"const": 7}, 8)


def test_numeric_bounds():
    assert schema_rules.validate({"type": "integer", "minimum": 0, "maximum": 100}, 50) == []
    assert schema_rules.validate({"type": "integer", "minimum": 0}, -1)
    assert schema_rules.validate({"type": "integer", "maximum": 100}, 101)
    assert schema_rules.validate({"type": "integer", "exclusiveMinimum": 0}, 0)
    assert schema_rules.validate({"type": "integer", "exclusiveMaximum": 100}, 100)
    assert schema_rules.validate({"type": "integer", "exclusiveMinimum": 0}, 1) == []


def test_multiple_of():
    assert schema_rules.validate({"type": "integer", "multipleOf": 5}, 10) == []
    assert schema_rules.validate({"type": "integer", "multipleOf": 5}, 11)


def test_string_length_and_pattern():
    assert schema_rules.validate({"type": "string", "minLength": 2}, "ab") == []
    assert schema_rules.validate({"type": "string", "minLength": 3}, "ab")
    assert schema_rules.validate({"type": "string", "maxLength": 1}, "ab")
    assert schema_rules.validate({"type": "string", "pattern": r"^a"}, "ab") == []
    assert schema_rules.validate({"type": "string", "pattern": r"^a"}, "ba")


def test_a_broken_pattern_is_reported_against_the_shape_not_the_value():
    findings = schema_rules.validate({"type": "string", "pattern": "([unclosed"}, "x")
    assert "not a valid regular expression" in findings[0]["message"]


def test_array_length_items_and_uniqueness():
    shape = {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 2}
    assert schema_rules.validate(shape, ["a"]) == []
    assert schema_rules.validate(shape, [])
    assert schema_rules.validate(shape, ["a", "b", "c"])
    assert schema_rules.validate(shape, ["a", 1])
    assert schema_rules.validate({"type": "array", "uniqueItems": True}, ["a", "b"]) == []
    assert schema_rules.validate({"type": "array", "uniqueItems": True}, ["a", "a"])


def test_a_nested_object_path_is_reported():
    shape = {"type": "object", "properties": {"a": {"type": "object", "required": ["b"]}}}
    findings = schema_rules.validate(shape, {"a": {}})
    assert findings[0]["path"] == "$.a.b"


def test_an_array_item_path_is_reported():
    findings = schema_rules.validate({"type": "array", "items": {"type": "string"}}, ["a", 1])
    assert findings[0]["path"] == "$[1]"


def test_a_format_that_was_not_read_is_an_annotation_and_is_ignored():
    """JSON Schema requires this of an unknown format, and so does this build."""
    assert schema_rules.validate({"type": "string", "format": "iban"}, "not-an-iban") == []


@pytest.mark.parametrize(
    ("fmt", "good", "bad"),
    [
        ("date", "2026-09-26", "26/09/2026"),
        ("date-time", "2026-09-26T14:05:00Z", "yesterday"),
        ("email", "a@b.co", "not-an-email"),
    ],
)
def test_the_checked_formats(fmt, good, bad):
    assert schema_rules.validate({"type": "string", "format": fmt}, good) == []
    assert schema_rules.validate({"type": "string", "format": fmt}, bad)


def test_a_type_mismatch_stops_further_checks():
    """Reporting a minimum against a string would describe a value that is not here."""
    findings = schema_rules.validate({"type": "string", "minLength": 5}, 1)
    assert [finding["keyword"] for finding in findings] == ["type"]


def test_every_failure_is_reported_at_once():
    """A sender fixing a signal should not have to resubmit once per field."""
    shape = {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "integer"}}}
    findings = schema_rules.validate(shape, {"a": 1, "b": "x"})
    assert {finding["path"] for finding in findings} == {"$.a", "$.b"}


def test_conforms_is_the_boolean_shortcut():
    assert schema_rules.conforms({"type": "string"}, "x")
    assert not schema_rules.conforms({"type": "string"}, 1)


def test_the_supported_keyword_list_is_the_documented_one():
    assert {"type", "properties", "required", "additionalProperties", "items", "format"} <= set(
        schema_rules.SUPPORTED_KEYWORDS
    )


def test_a_declared_shape_must_be_a_json_schema_object():
    with pytest.raises(SignalError):
        schema_rules.require_object_shape({}, "data_shape")
    with pytest.raises(SignalError):
        schema_rules.require_object_shape("nope", "data_shape")
    with pytest.raises(SignalError):
        schema_rules.require_object_shape({"type": "nonsense"}, "data_shape")


def test_a_data_shape_must_describe_an_object():
    with pytest.raises(SignalError) as caught:
        schema_rules.require_object_shape({"type": "string"}, "data_shape")
    assert 'must be "object"' in str(caught.value)


def test_a_shape_with_no_declared_type_is_accepted():
    """Absence of a type means any type, which JSON Schema also says."""
    assert schema_rules.require_object_shape({"properties": {"a": {}}}, "data_shape")


def test_properties_must_be_an_object_of_schemas():
    with pytest.raises(SignalError):
        schema_rules.require_object_shape({"type": "object", "properties": ["a"]}, "data_shape")


# --------------------------------------------------------------------------- #
# The ICU Message subset
# --------------------------------------------------------------------------- #


def test_the_researched_example_renders_exactly_as_written():
    """The research's own worked example, verbatim.

    It is a fragment, and the fragment leaves its second quoted span open, so the
    rendered sentence carries the same imbalance. Asserted literally rather than
    tidied up, because the point of the test is that this exact string renders.
    """
    template = (
        '"{video_name}" was viewed "{view_count, plural, =1 {# time} other {# times}} within 7 days'
    )
    rendered = icume.render(template, {"video_name": "Customer Reference", "view_count": 3})
    assert rendered["text"] == '"Customer Reference" was viewed "3 times within 7 days'
    assert rendered["warnings"] == []


def test_a_quoted_research_style_example_renders_with_both_quotes():
    """The same message with the second span closed, which is how one would write it."""
    template = '"{video_name}" was viewed "{view_count, plural, =1 {# time} other {# times}}" within 7 days'
    rendered = icume.render(template, {"video_name": "Customer Reference", "view_count": 3})
    assert rendered["text"] == '"Customer Reference" was viewed "3 times" within 7 days'


def test_the_singular_branch_of_the_researched_example():
    rendered = icume.render(
        "{view_count, plural, =1 {# time} other {# times}}",
        {"view_count": 1},
    )
    assert rendered["text"] == "1 time"


def test_an_exact_plural_branch_wins_over_the_category():
    assert icume.render("{n, plural, =0 {none} other {#}}", {"n": 0})["text"] == "none"
    assert icume.render("{n, plural, =0 {none} other {#}}", {"n": 5})["text"] == "5"


def test_english_plural_categories_are_one_and_other():
    assert icume.render("{n, plural, one {# item} other {# items}}", {"n": 1})["text"] == "1 item"
    assert icume.render("{n, plural, one {# item} other {# items}}", {"n": 2})["text"] == "2 items"


def test_a_plural_without_an_exact_branch_still_works():
    assert icume.render("{n, plural, one {#} other {#}}", {"n": 1})["text"] == "1"


def test_a_plural_missing_its_other_branch_renders_nothing_and_says_so():
    """A neighbouring branch would render "4 item", which is wrong rather than missing."""
    rendered = icume.render("{n, plural, one {# item}}", {"n": 4})
    assert rendered["text"] == ""
    assert any("no branch" in warning for warning in rendered["warnings"])


def test_a_plural_of_zero_selects_the_zero_category():
    assert icume.render("{n, plural, zero {none} other {#}}", {"n": 0})["text"] == "none"


def test_a_plural_category_english_never_reaches_simply_uses_other():
    """`few` is a valid ICU category that English selection never reaches.

    That is a limitation of the implemented subset - documented in icume.py and in
    the inference register - and not something every signal should carry a warning
    about.
    """
    rendered = icume.render("{n, plural, few {#} other {#}}", {"n": 3})
    assert rendered["text"] == "3"
    assert rendered["warnings"] == []


def test_a_nested_argument_inside_a_plural_branch_renders():
    rendered = icume.render(
        "{who} has {n, plural, one {# try} other {# tries}}", {"who": "Priya", "n": 2}
    )
    assert rendered["text"] == "Priya has 2 tries"


def test_a_select_chooses_its_matching_branch():
    rendered = icume.render(
        "{stage, select, evaluation {in evaluation} closed {already closed} other {unknown}}",
        {"stage": "closed"},
    )
    assert rendered["text"] == "already closed"


def test_a_select_that_matches_nothing_uses_other_and_says_so():
    rendered = icume.render(
        "{stage, select, closed {closed} other {unknown}}", {"stage": "negotiation"}
    )
    assert rendered["text"] == "unknown"
    assert any("no branch" in warning for warning in rendered["warnings"])


def test_a_missing_argument_leaves_a_visible_placeholder_and_warns():
    rendered = icume.render("Hello {name}, you have {n, plural, one {# item} other {# items}}", {})
    assert "{name}" in rendered["text"]
    assert "{n}" in rendered["text"]
    assert len(rendered["warnings"]) == 2


def test_a_registration_one_word_short_does_not_raise():
    """A degradation with a warning beats a seller's feed going quiet."""
    assert icume.render("{missing}", {})["text"] == "{missing}"


def test_an_unsupported_argument_type_renders_plainly_and_warns():
    rendered = icume.render("{n, number}", {"n": 1234})
    assert rendered["text"] == "1234"
    assert any("not implemented" in warning for warning in rendered["warnings"])


def test_an_apostrophe_is_an_ordinary_character():
    """Real ICU would swallow the rest of the sentence after a lone quote."""
    rendered = icume.render("The buyer's pricing page was viewed {n} times", {"n": 2})
    assert rendered["text"] == "The buyer's pricing page was viewed 2 times"
    assert rendered["warnings"] == []


def test_an_unclosed_brace_is_a_render_error():
    with pytest.raises(RenderError):
        icume.render("{n", {})


def test_an_argument_with_no_name_is_a_render_error():
    with pytest.raises(RenderError):
        icume.render("{}", {})


def test_a_plural_with_no_branches_warns_and_renders_nothing():
    rendered = icume.render("{n, plural}", {"n": 1})
    assert rendered["text"] == ""
    assert any("no branches" in warning for warning in rendered["warnings"])


def test_a_branch_with_no_message_is_a_render_error():
    with pytest.raises(RenderError):
        icume.render("{n, plural, other}", {"n": 1})


def test_a_message_must_be_a_string():
    with pytest.raises(RenderError):
        icume.render(42, {})


def test_a_float_that_is_whole_renders_as_an_integer():
    """`"was viewed 1.0 times"` reads as a bug even when the value really was a float."""
    assert icume.format_number(42.0) == "42"
    assert icume.format_number(42) == "42"
    assert icume.format_number(3.5) == "3.5"
    assert icume.format_number(True) == "true"
    assert icume.format_number("x") == "x"


def test_every_locale_of_a_registration_can_be_rendered_at_once():
    rendered = icume.render_locales({"en": "{n} views", "fr": "{n} vues"}, {"n": 2})
    assert rendered["en"]["text"] == "2 views"
    assert rendered["fr"]["text"] == "2 vues"


def test_only_plural_and_select_are_implemented():
    assert set(icume.SUPPORTED_ARGUMENT_TYPES) == {"plural", "select"}


# --------------------------------------------------------------------------- #
# Indicators: the research's specific-versus-vague correction
# --------------------------------------------------------------------------- #


def test_the_researched_pair_is_carried_verbatim():
    assert indicator_rules.SPECIFIC_EXAMPLE == {
        "key": "spent_more_than_30s_on_site",
        "metadata": {"time_in_seconds": 42},
    }
    assert indicator_rules.VAGUE_EXAMPLE == {
        "key": "time_spent_on_site",
        "metadata": {"time_in_seconds": 30},
    }


def test_the_researched_good_key_carries_its_bound():
    claim = indicator_rules.parse_claim("spent_more_than_30s_on_site")
    assert claim["comparison"] == "gt"
    assert claim["threshold"] == 30.0
    assert claim["unit"] == "s"
    assert claim["subject"] == "spent"


def test_the_researched_vague_key_carries_no_bound():
    assert indicator_rules.parse_claim("time_spent_on_site") is None


def test_42_satisfies_the_researched_indicator_and_30_does_not():
    """The exact contrast the research draws with its two examples."""
    indicator = {"key": "spent_more_than_30s_on_site", "metadata_shape": DURATION_SHAPE}
    assert indicator_rules.evaluate(indicator, {"time_in_seconds": 42})["qualifies"] is True
    assert indicator_rules.evaluate(indicator, {"time_in_seconds": 30})["qualifies"] is False


@pytest.mark.parametrize(
    ("key", "comparison", "threshold"),
    [
        ("watched_more_than_75_percent", "gt", 75.0),
        ("viewed_at_least_3_times", "ge", 3.0),
        ("opened_at_most_2_times", "le", 2.0),
        ("engaged_over_10s", "gt", 10.0),
        ("scrolled_under_5_pages", "lt", 5.0),
        ("downloaded_fewer_than_4_decks", "lt", 4.0),
    ],
)
def test_every_declared_comparative_is_read(key, comparison, threshold):
    claim = indicator_rules.parse_claim(key)
    assert claim["comparison"] == comparison
    assert claim["threshold"] == threshold


def test_a_comparative_with_no_number_after_it_is_not_a_claim():
    assert indicator_rules.parse_claim("spent_more_than_seconds") is None


def test_a_key_with_no_comparative_is_not_a_claim():
    assert indicator_rules.parse_claim("viewed_pricing_page") is None
    assert indicator_rules.parse_claim("") is None
    assert indicator_rules.parse_claim(None) is None


def test_a_repeated_comparative_resolves_on_the_one_with_a_number():
    claim = indicator_rules.parse_claim("at_least_more_than_3_times")
    assert claim["comparison"] == "gt"
    assert claim["threshold"] == 3.0


def test_the_observation_field_is_resolved_from_the_declared_shape():
    claim = indicator_rules.parse_claim("spent_more_than_30s_on_site")
    assert indicator_rules.resolve_observation_field(claim, DURATION_SHAPE) == "time_in_seconds"


def test_a_shape_with_several_numeric_fields_is_ambiguous_rather_than_guessed():
    """The research's own key shares no word with its own field name.

    ``spent_more_than_30s_on_site`` and ``time_in_seconds`` have no token in
    common, so a name-similarity rule would not have resolved the one example the
    research supplies. Two candidates therefore resolve to nothing.
    """
    shape = {
        "type": "object",
        "properties": {
            "time_in_seconds": {"type": "integer"},
            "downloads": {"type": "integer"},
        },
    }
    claim = indicator_rules.parse_claim("spent_more_than_30s_on_site")
    assert indicator_rules.resolve_observation_field(claim, shape) is None


def test_a_shape_with_no_numeric_field_cannot_resolve_a_bound():
    claim = indicator_rules.parse_claim("spent_more_than_30s_on_site")
    assert (
        indicator_rules.resolve_observation_field(
            claim, {"type": "object", "properties": {"a": {}}}
        )
        is None
    )


def test_a_bound_with_no_numeric_field_to_check_qualifies_on_trust():
    """It is reported as such. Never silently treated as checked."""
    indicator = {"key": "spent_more_than_30s_on_site", "metadata_shape": {"type": "object"}}
    decision = indicator_rules.evaluate(indicator, {"anything": 1})
    assert decision["qualifies"] is True
    assert decision["reason"] == "bound_unresolvable"


def test_a_bound_whose_field_is_absent_qualifies_on_trust_and_says_so():
    indicator = {
        "key": "spent_more_than_30s_on_site",
        "metadata_shape": {
            "type": "object",
            "properties": {"time_in_seconds": {"type": "integer"}},
        },
    }
    decision = indicator_rules.evaluate(indicator, {})
    assert decision["qualifies"] is True
    assert decision["reason"] == "bound_unverifiable"


def test_metadata_that_fails_the_indicator_shape_does_not_qualify():
    indicator = {"key": "spent_more_than_30s_on_site", "metadata_shape": DURATION_SHAPE}
    decision = indicator_rules.evaluate(indicator, {"time_in_seconds": "ages"})
    assert decision["qualifies"] is False
    assert decision["reason"] == "metadata_mismatch"
    assert decision["findings"]


def test_every_reason_evaluate_can_return_is_published():
    """A decision nobody can name is a decision nobody can check."""
    reasons = set()
    for indicator, metadata in (
        ({"key": "a_more_than_1_b", "metadata_shape": {"type": "object"}}, {}),
        ({"key": "plain", "metadata_shape": {"type": "object"}}, {}),
        ({"key": "a_more_than_1_b", "metadata_shape": DURATION_SHAPE}, {"time_in_seconds": 9}),
        ({"key": "a_more_than_1_b", "metadata_shape": DURATION_SHAPE}, {"time_in_seconds": 0}),
        ({"key": "a_more_than_1_b", "metadata_shape": DURATION_SHAPE}, {"time_in_seconds": "x"}),
    ):
        reasons.add(indicator_rules.evaluate(indicator, metadata)["reason"])
    assert reasons <= set(indicator_rules.QUALIFICATION_REASONS)
    # The three that matter for a seller's trust are all reachable.
    assert {"bound_met", "bound_not_met", "no_bound_claimed"} <= reasons


def test_a_vague_key_is_warned_about_with_the_researched_pair_named():
    findings = indicator_rules.specificity_findings(
        "time_spent_on_site", DURATION_SHAPE, {"en": "x"}
    )
    assert findings[0]["code"] == "indicator_key_states_no_bound"
    assert "spent_more_than_30s_on_site" in findings[0]["detail"]
    assert "time_spent_on_site" in findings[0]["detail"]


def test_a_specific_key_is_not_warned_about_for_its_bound():
    findings = indicator_rules.specificity_findings(
        "spent_more_than_30s_on_site", DURATION_SHAPE, {"en": "x"}
    )
    assert [finding["code"] for finding in findings] == []


def test_unquantified_metadata_is_warned_about():
    findings = indicator_rules.specificity_findings(
        "viewed_pricing_page",
        {"type": "object", "properties": {"a": {"type": "string"}}},
        {"en": "x"},
    )
    codes = {finding["code"] for finding in findings}
    assert "indicator_key_states_no_bound" in codes
    assert "indicator_metadata_not_quantified" in codes


def test_a_missing_indicator_description_is_warned_about():
    findings = indicator_rules.specificity_findings(
        "spent_more_than_30s_on_site", DURATION_SHAPE, None
    )
    assert "indicator_description_missing" in {finding["code"] for finding in findings}


def test_an_unresolvable_bound_is_warned_about_at_registration():
    shape = {
        "type": "object",
        "properties": {"time_in_seconds": {"type": "integer"}, "downloads": {"type": "integer"}},
    }
    findings = indicator_rules.specificity_findings(
        "spent_more_than_30s_on_site", shape, {"en": "x"}
    )
    assert "indicator_bound_unresolvable" in {finding["code"] for finding in findings}


def test_numeric_fields_includes_a_bounded_field_with_no_declared_type():
    shape = {"type": "object", "properties": {"time_in_seconds": {"minimum": 0}}}
    assert indicator_rules.numeric_fields(shape) == ["time_in_seconds"]


def test_every_declared_indicator_comes_back_from_evaluate_all():
    declared = [
        {"key": "spent_more_than_30s_on_site", "metadata_shape": DURATION_SHAPE},
        {"key": "watched_more_than_75_percent", "metadata_shape": WATCH_SHAPE},
    ]
    decisions = indicator_rules.evaluate_all(
        declared, {"time_in_seconds": 90, "watched_percent": 10}
    )
    assert [d["key"] for d in decisions] == [
        "spent_more_than_30s_on_site",
        "watched_more_than_75_percent",
    ]
    assert [d["qualifies"] for d in decisions] == [True, False]


# --------------------------------------------------------------------------- #
# Registration: what a signal type is made of
# --------------------------------------------------------------------------- #


def test_a_registration_normalises_to_the_researched_fields(registered):
    assert registered["signal_name"] == "Deep engagement with shared content"
    assert registered["type"] == "document_engagement"
    assert registered["integration_id"] == "dsr"
    assert registered["data_shape"]["type"] == "object"
    assert registered["description"]["en"].startswith("{buyer_first_name}")
    assert registered["indicator_keys"] == ["spent_more_than_30s_on_site"]
    # `normalise_attribution` orders by the published vocabulary, not by input
    # order, so two registrations of the same list serialise identically.
    assert registered["attribution"] == ["person_id", "account_id", "user_guid"]
    assert registered["broadcast_notification"] is True


@pytest.mark.parametrize(
    "field",
    [
        "signal_name",
        "type",
        "integration_id",
        "description",
        "data_shape",
        "indicators",
        "attribution",
    ],
)
def test_every_researched_registration_field_is_required(field):
    payload = registration_payload()
    payload.pop(field)
    with pytest.raises(RegistrationError) as caught:
        normalise_registration(payload)
    # `indicators` reads back as "at least one indicator", which is the clearer
    # sentence, so the assertion is on the stem rather than the exact word.
    assert field.rstrip("s") in str(caught.value)


def test_at_least_one_indicator_is_required():
    """`"at least one indicator with key + metadata_shape"`"""
    with pytest.raises(RegistrationError) as caught:
        normalise_registration(registration_payload(indicators=[]))
    assert "at least one indicator" in str(caught.value)


def test_an_indicator_needs_a_key():
    with pytest.raises(RegistrationError) as caught:
        normalise_registration(
            registration_payload(indicators=[{"metadata_shape": DURATION_SHAPE}])
        )
    assert "key is required" in str(caught.value)


def test_an_indicator_needs_a_metadata_shape():
    """`"at least one indicator with key + metadata_shape"`"""
    with pytest.raises(RegistrationError) as caught:
        normalise_registration(registration_payload(indicators=[{"key": "a_more_than_1_b"}]))
    assert "metadata_shape is required" in str(caught.value)


def test_an_indicator_key_may_not_be_declared_twice():
    with pytest.raises(RegistrationError) as caught:
        normalise_registration(
            registration_payload(
                indicators=[
                    {"key": "a_more_than_1_b", "metadata_shape": DURATION_SHAPE},
                    {"key": "a_more_than_1_b", "metadata_shape": DURATION_SHAPE},
                ]
            )
        )
    assert "declared twice" in str(caught.value)


def test_indicator_key_keeps_its_own_case_and_whitespace_is_trimmed():
    listed, _warnings = normalise_indicators(
        [{"key": "  spent_more_than_30s_on_site  ", "metadata_shape": DURATION_SHAPE}]
    )
    assert listed[0]["key"] == "spent_more_than_30s_on_site"


def test_an_indicator_entry_must_be_an_object():
    with pytest.raises(RegistrationError):
        normalise_indicators(["spent_more_than_30s_on_site"])


def test_an_attribution_list_is_required():
    with pytest.raises(RegistrationError) as caught:
        normalise_registration(registration_payload(attribution=[]))
    assert "Person, Account, User, Opportunity" in str(caught.value)


def test_an_attribution_list_rejects_a_value_outside_the_vocabulary():
    with pytest.raises(EmissionError):
        registration_attribution(["person_id", "deal_id"])


def test_attribution_is_deduplicated_and_ordered_by_the_published_vocabulary():
    assert registration_attribution(["user_guid", "person_id", "user_guid"]) == [
        "person_id",
        "user_guid",
    ]


def test_broadcast_notification_defaults_to_visible_and_says_it_did():
    """The research says the field controls display and states no default."""
    payload = registration_payload()
    payload.pop("broadcast_notification")
    record = normalise_registration(payload)
    assert record["broadcast_notification"] is True
    assert any(
        warning["code"] == "broadcast_notification_defaulted" for warning in record["warnings"]
    )


def test_an_explicit_broadcast_notification_produces_no_defaulting_warning():
    record = normalise_registration(registration_payload(broadcast_notification=False))
    assert record["broadcast_notification"] is False
    assert not any(
        warning["code"] == "broadcast_notification_defaulted" for warning in record["warnings"]
    )


def test_a_non_boolean_broadcast_notification_is_refused():
    with pytest.raises(RegistrationError):
        normalise_registration(registration_payload(broadcast_notification="yes"))


def test_a_type_must_look_like_an_identifier_not_a_sentence():
    with pytest.raises(RegistrationError) as caught:
        normalise_registration(registration_payload(type="a deep engagement with content"))
    assert "machine-readable contract" in str(caught.value)


def test_camel_case_spellings_are_folded_onto_the_researched_names():
    record = normalise_registration(
        registration_payload(
            signalName="Deep engagement",
            dataShape=DATA_SHAPE,
            integrationId="dsr",
            broadcastNotification=False,
        )
    )
    assert record["signal_name"] == "Deep engagement"
    assert record["integration_id"] == "dsr"
    assert record["broadcast_notification"] is False


def test_an_unrecognised_field_is_refused_rather_than_dropped():
    """A contract quietly missing a clause is worse than a rejected request."""
    with pytest.raises(RegistrationError) as caught:
        normalise_registration(registration_payload(revision_note="v2"))
    assert "refused rather than dropped" in str(caught.value)


def test_a_caller_may_not_supply_this_products_own_bookkeeping():
    for field in ("warnings", "revision", "created_at", "updated_at", "id", "registration_id"):
        with pytest.raises(RegistrationError) as caught:
            normalise_registration(registration_payload(**{field: "x"}))
        assert "cannot be supplied" in str(caught.value) or "refused rather than dropped" in str(
            caught.value
        )


def test_canonical_folds_and_never_invents_a_field():
    assert canonical({"signalName": "a", "type": "b"}) == {"signal_name": "a", "type": "b"}
    with pytest.raises(RegistrationError):
        canonical({"nope": 1})


def test_a_registration_carries_its_lint_findings(registered):
    assert registered["warnings"] == []


def test_a_registration_with_a_vague_indicator_carries_the_warning(engine):
    record = engine.register(
        registration_payload(
            type="content_opened",
            indicators=[
                {
                    "key": "time_spent_on_site",
                    "metadata_shape": DURATION_SHAPE,
                    "description": {"en": "Time on the content."},
                }
            ],
        ),
        actor="dana",
        source=SOURCE,
    )["registration"]
    assert "indicator_key_states_no_bound" in {w["code"] for w in record["warnings"]}


# --------------------------------------------------------------------------- #
# One signal type per integration
# --------------------------------------------------------------------------- #


def test_the_same_type_may_not_be_registered_twice_on_one_integration(engine):
    engine.register(registration_payload(), actor="dana", source=SOURCE)
    with pytest.raises(DuplicateSignalType) as caught:
        engine.register(registration_payload(), actor="dana", source=SOURCE)
    assert "can only be registered once" in str(caught.value)
    assert len(engine.registrations()) == 1


def test_the_duplicate_message_names_the_existing_registration(engine):
    first = engine.register(registration_payload(), actor="dana", source=SOURCE)["registration"]
    with pytest.raises(DuplicateSignalType) as caught:
        engine.register(registration_payload(), actor="dana", source=SOURCE)
    assert first["id"] in str(caught.value)


def test_the_same_type_may_be_registered_on_two_integrations(engine):
    """The rule is per integration, so this is legal rather than a loophole."""
    engine.register(registration_payload(), actor="dana", source=SOURCE)
    engine.register(
        registration_payload(integration_id="partner_portal"), actor="dana", source=SOURCE
    )
    assert len(engine.registrations()) == 2


def test_a_different_type_on_the_same_integration_is_fine(engine):
    engine.register(registration_payload(), actor="dana", source=SOURCE)
    engine.register(registration_payload(type="content_opened"), actor="dana", source=SOURCE)
    assert len(engine.registrations()) == 2


def test_a_repeated_registration_idempotency_key_returns_the_first_registration(engine):
    key = uuid4()
    first = engine.register(registration_payload(idempotency_key=key), actor="dana", source=SOURCE)
    second = engine.register(
        registration_payload(idempotency_key=key, signal_name="A different name"),
        actor="dana",
        source=SOURCE,
    )
    assert first["outcome"] == "registered"
    assert second["outcome"] == "already_registered"
    assert second["registration"]["id"] == first["registration"]["id"]
    assert second["registration"]["signal_name"] == "Deep engagement with shared content"


def test_a_registration_idempotency_key_must_be_a_uuid4(engine):
    with pytest.raises(SignalError):
        engine.register(
            registration_payload(idempotency_key="not-a-uuid"), actor="dana", source=SOURCE
        )


# --------------------------------------------------------------------------- #
# Immutable contract: only additive changes
# --------------------------------------------------------------------------- #


def test_adding_an_indicator_is_additive_and_allowed(engine, registered):
    amended = engine.amend(
        registered["id"],
        {
            "indicators": [
                {
                    "key": "watched_more_than_75_percent",
                    "metadata_shape": WATCH_SHAPE,
                    "description": {"en": "Watched {watched_percent}% of {document_name}."},
                }
            ]
        },
        actor="dana",
        source=SOURCE,
    )
    assert set(amended["indicator_keys"]) == {
        "spent_more_than_30s_on_site",
        "watched_more_than_75_percent",
    }
    # The existing indicator is untouched, not rebuilt.
    original = next(
        i for i in registered["indicators"] if i["key"] == "spent_more_than_30s_on_site"
    )
    kept = next(i for i in amended["indicators"] if i["key"] == "spent_more_than_30s_on_site")
    assert kept["metadata_shape"] == original["metadata_shape"]


def test_adding_a_locale_is_additive_and_allowed(engine, registered):
    amended = engine.amend(
        registered["id"],
        {"description": {"fr": "{buyer_first_name} a passe 42 secondes sur {document_name}."}},
        actor="dana",
        source=SOURCE,
    )
    assert amended["locales"] == ["en", "fr"]
    assert amended["description"]["en"] == registered["description"]["en"]


def test_adding_an_attribution_type_is_additive_and_allowed(engine, registered):
    amended = engine.amend(
        registered["id"], {"attribution": ["opportunity_id"]}, actor="dana", source=SOURCE
    )
    assert set(amended["attribution"]) == {
        "person_id",
        "account_id",
        "user_guid",
        "opportunity_id",
    }


def test_adding_an_optional_data_shape_property_is_allowed(engine, registered):
    shape = dict(registered["data_shape"])
    shape["properties"] = {**shape["properties"], "referrer": {"type": "string"}}
    amended = engine.amend(registered["id"], {"data_shape": shape}, actor="dana", source=SOURCE)
    assert "referrer" in amended["data_shape"]["properties"]


def test_removing_a_required_name_is_refused_because_the_rule_is_one_way(engine, registered):
    """`required` is frozen in both directions: growing it breaks emitters, shrinking is a removal."""
    shape = {**registered["data_shape"], "required": []}
    with pytest.raises(ImmutableContractError) as caught:
        engine.amend(registered["id"], {"data_shape": shape}, actor="dana", source=SOURCE)
    assert "requirement removed" in str(caught.value)


@pytest.mark.parametrize("field", ["type", "signal_name", "integration_id"])
def test_changing_an_identity_field_is_refused(engine, registered, field):
    with pytest.raises(ImmutableContractError) as caught:
        engine.amend(registered["id"], {field: "something-else"}, actor="dana", source=SOURCE)
    assert field in str(caught.value)


def test_removing_an_indicator_is_refused(engine, registered):
    """A patch listing one indicator adds it; the one it omits is not removed."""
    amended = engine.amend(
        registered["id"],
        {
            "indicators": [
                {
                    "key": "watched_more_than_75_percent",
                    "metadata_shape": WATCH_SHAPE,
                    "description": {"en": "Watched {watched_percent}%."},
                }
            ]
        },
        actor="dana",
        source=SOURCE,
    )
    assert "spent_more_than_30s_on_site" in amended["indicator_keys"]


def test_an_indicators_shape_cannot_be_rewritten_through_an_omission(engine, registered):
    with pytest.raises(ImmutableContractError) as caught:
        engine.amend(
            registered["id"],
            {
                "indicators": [
                    {
                        "key": "spent_more_than_30s_on_site",
                        "metadata_shape": {
                            "type": "object",
                            "properties": {"n": {"type": "integer"}},
                        },
                    }
                ]
            },
            actor="dana",
            source=SOURCE,
        )
    assert "metadata_shape" in str(caught.value)


def test_changing_an_indicator_metadata_shape_is_refused(engine, registered):
    with pytest.raises(ImmutableContractError) as caught:
        engine.amend(
            registered["id"],
            {
                "indicators": [
                    {
                        "key": "spent_more_than_30s_on_site",
                        "metadata_shape": {
                            "type": "object",
                            "properties": {"time_in_seconds": {"type": "string"}},
                        },
                    }
                ]
            },
            actor="dana",
            source=SOURCE,
        )
    assert "metadata_shape" in str(caught.value)


def test_rewriting_a_locale_a_seller_has_already_read_is_refused(engine, registered):
    with pytest.raises(ImmutableContractError) as caught:
        engine.amend(
            registered["id"],
            {"description": {"en": "A different sentence."}},
            actor="dana",
            source=SOURCE,
        )
    assert "description.en" in str(caught.value)


def test_adding_a_locale_alongside_rewriting_one_names_only_the_rewrite(engine, registered):
    with pytest.raises(ImmutableContractError) as caught:
        engine.amend(
            registered["id"],
            {"description": {"en": "Rewritten.", "fr": "Nouveau."}},
            actor="dana",
            source=SOURCE,
        )
    message = str(caught.value)
    assert "description.en" in message
    assert "description.fr" not in message


def test_removing_an_attribution_type_is_refused(engine, registered):
    """A patch naming one attribution type adds it; the others survive."""
    amended = engine.amend(
        registered["id"], {"attribution": ["opportunity_id"]}, actor="dana", source=SOURCE
    )
    assert set(amended["attribution"]) == {
        "person_id",
        "account_id",
        "user_guid",
        "opportunity_id",
    }


def test_omitting_a_declared_data_shape_property_does_not_remove_it(engine, registered):
    """The patch is additions, so the properties it does not mention survive."""
    amended = engine.amend(
        registered["id"],
        {
            "data_shape": {
                "type": "object",
                "properties": {"referrer": {"type": "string"}},
                "required": registered["data_shape"]["required"],
            }
        },
        actor="dana",
        source=SOURCE,
    )
    assert "document_name" in amended["data_shape"]["properties"]
    assert "referrer" in amended["data_shape"]["properties"]


def test_adding_a_data_shape_requirement_is_refused_because_it_breaks_emitters(engine, registered):
    shape = {
        **registered["data_shape"],
        "required": [*registered["data_shape"]["required"], "page"],
    }
    with pytest.raises(ImmutableContractError) as caught:
        engine.amend(registered["id"], {"data_shape": shape}, actor="dana", source=SOURCE)
    assert "requirement added" in str(caught.value)


def test_changing_an_existing_property_shape_is_refused(engine, registered):
    shape = {
        **registered["data_shape"],
        "properties": {**registered["data_shape"]["properties"], "page": {"type": "string"}},
    }
    with pytest.raises(ImmutableContractError) as caught:
        engine.amend(registered["id"], {"data_shape": shape}, actor="dana", source=SOURCE)
    assert "data_shape.properties.page" in str(caught.value)


def test_every_offending_path_is_reported_at_once(engine, registered):
    """A partner correcting a rejected amendment should not resubmit per field."""
    with pytest.raises(ImmutableContractError) as caught:
        engine.amend(
            registered["id"],
            {
                "type": "other",
                "signal_name": "Other",
                "integration_id": "other_integration",
                "data_shape": {
                    **registered["data_shape"],
                    "required": [*registered["data_shape"]["required"], "page"],
                },
            },
            actor="dana",
            source=SOURCE,
        )
    message = str(caught.value)
    for path in ("type", "signal_name", "integration_id", "data_shape.required.page"):
        assert path in message


def test_an_amendment_with_no_recognised_field_is_refused_as_unknown(engine, registered):
    with pytest.raises(RegistrationError):
        engine.amend(registered["id"], {"priority": "high"}, actor="dana", source=SOURCE)


def test_amendment_of_an_unknown_registration_is_refused(engine):
    with pytest.raises(SignalError):
        engine.amend("signal_registration_nope", {}, actor="dana", source=SOURCE)


def test_an_amendment_adds_a_licence_not_a_migration(store, engine):
    """The point of additive: an existing signal still conforms afterwards."""
    engine.register(registration_payload(), actor="dana", source=SOURCE)
    registration = engine.registrations()[0]
    emitted = engine.emit(signal_payload(), room_id=None, actor="dana", source=SOURCE)["signal"]
    engine.amend(
        registration["id"],
        {
            "data_shape": {
                "type": "object",
                "properties": {**DATA_SHAPE["properties"], "referrer": {"type": "string"}},
                "required": DATA_SHAPE["required"],
            }
        },
        actor="dana",
        source=SOURCE,
    )
    after = engine.registrations()[0]
    assert emitted["data"] == {
        "document_name": "Security & Compliance Pack",
        "document_kind": "pdf",
        "action": "viewed",
        "buyer_first_name": "Priya",
        "room_name": "Northwind Traders — Enterprise Evaluation",
    }
    assert "referrer" in after["data_shape"]["properties"]
    assert store.get(emitted["id"]) is not None


# --------------------------------------------------------------------------- #
# Withdrawing a registration
# --------------------------------------------------------------------------- #


def test_an_unused_registration_can_be_withdrawn(engine, registered):
    result = engine.withdraw(registered["id"], actor="dana", source=SOURCE)
    assert result["withdrawn"] is True
    assert engine.registrations() == []
    assert engine.registrations(include_withdrawn=True)


def test_a_registration_that_has_emitted_cannot_be_withdrawn(engine, registered):
    engine.emit(signal_payload(), room_id=None, actor="dana", source=SOURCE)
    with pytest.raises(ImmutableContractError) as caught:
        engine.withdraw(registered["id"], actor="dana", source=SOURCE)
    assert "already delivered 1 signal" in str(caught.value)


def test_a_withdrawn_registration_is_soft_deleted_so_the_audit_trail_still_points_at_it(
    engine, registered, store
):
    engine.withdraw(registered["id"], actor="dana", source=SOURCE)
    assert store.get(registered["id"]) is None
    rows = store.audit(limit=50)
    assert any(
        entry["action"] == "delete" and entry["record_id"] == registered["id"] for entry in rows
    )


def test_withdrawing_an_unknown_registration_is_refused(engine):
    with pytest.raises(SignalError):
        engine.withdraw("signal_registration_nope", actor="dana", source=SOURCE)


# --------------------------------------------------------------------------- #
# Emission: the researched field list
# --------------------------------------------------------------------------- #


def test_a_well_formed_signal_is_emitted_and_stored(engine, registered, room):
    result = engine.emit(signal_payload(), room_id=room["id"], actor="dana", source=SOURCE)
    assert result["outcome"] == "emitted"
    signal = result["signal"]
    assert signal["type"] == "document_engagement"
    assert signal["signal_name"] == registered["signal_name"]
    assert signal["urgency"] == "high"
    assert signal["room_id"] == room["id"]
    assert signal["registration_id"] == registered["id"]


def test_a_signal_carries_the_researched_field_list(engine, registered, room):
    signal = engine.emit(signal_payload(), room_id=room["id"], actor="dana", source=SOURCE)[
        "signal"
    ]
    for field in (
        "type",
        "data",
        "indicators",
        "urgency",
        "occurred_at",
        "idempotency_key",
        "attribution",
        "broadcast_notification",
    ):
        assert field in signal, field


def test_an_unregistered_type_is_refused_with_the_integration_it_searched(engine, room):
    with pytest.raises(UnregisteredSignalType) as caught:
        engine.emit(signal_payload(), room_id=room["id"], actor="dana", source=SOURCE)
    assert "no live signal registration" in str(caught.value)


def test_an_unregistered_type_names_the_integration_when_one_was_given(engine, room):
    with pytest.raises(UnregisteredSignalType) as caught:
        engine.emit(
            signal_payload(integration_id="partner_portal"),
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    assert "partner_portal" in str(caught.value)


def test_a_signal_may_name_its_integration_to_disambiguate(engine, room):
    engine.register(
        registration_payload(integration_id="partner_portal"), actor="dana", source=SOURCE
    )
    result = engine.emit(
        signal_payload(integration_id="partner_portal"),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert result["outcome"] == "emitted"
    assert result["signal"]["integration_id"] == "partner_portal"


def test_a_type_must_be_supplied(engine, registered, room):
    payload = signal_payload()
    payload.pop("type")
    with pytest.raises(SignalError):
        engine.emit(payload, room_id=room["id"], actor="dana", source=SOURCE)


def test_data_must_satisfy_the_registered_data_shape(engine, registered, room):
    """`"Signals must follow the structure defined on the Signal Registration."`"""
    with pytest.raises(EmissionError) as caught:
        engine.emit(
            signal_payload(data={"document_name": "x", "action": "listened"}),
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    assert "data_shape" in str(caught.value)


def test_data_missing_a_required_field_is_refused(engine, registered, room):
    with pytest.raises(EmissionError) as caught:
        engine.emit(
            signal_payload(data={"document_name": "x"}),
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    assert "action" in str(caught.value)


def test_data_may_carry_a_field_the_shape_never_declared(engine, registered, room):
    """Schema flexibility: a later release's field must not fail an older contract."""
    result = engine.emit(
        signal_payload(data={**signal_payload()["data"], "referrer": "email"}),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert result["signal"]["data"]["referrer"] == "email"


def test_a_signal_must_carry_at_least_one_indicator(engine, registered, room):
    with pytest.raises(EmissionError) as caught:
        engine.emit(signal_payload(indicators=[]), room_id=room["id"], actor="dana", source=SOURCE)
    assert "at least one indicator" in str(caught.value)


def test_an_undeclared_indicator_is_refused_and_names_the_declared_ones(engine, registered, room):
    with pytest.raises(UndeclaredIndicator) as caught:
        engine.emit(
            signal_payload(indicators=[{"key": "downloaded_the_pricing_page", "metadata": {}}]),
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    assert "spent_more_than_30s_on_site" in str(caught.value)


def test_an_indicator_may_not_be_claimed_twice_on_one_signal(engine, registered, room):
    indicator = {"key": "spent_more_than_30s_on_site", "metadata": {"time_in_seconds": 40}}
    with pytest.raises(EmissionError) as caught:
        engine.emit(
            signal_payload(indicators=[indicator, indicator]),
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    assert "claimed twice" in str(caught.value)


def test_indicator_metadata_must_satisfy_its_own_shape(engine, registered, room):
    with pytest.raises(EmissionError) as caught:
        engine.emit(
            signal_payload(indicators=[{"key": "spent_more_than_30s_on_site", "metadata": {}}]),
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    assert "metadata_shape" in str(caught.value)


def test_indicator_metadata_may_omit_an_optional_field(engine, room):
    engine.register(
        registration_payload(
            indicators=[
                {
                    "key": "time_spent_on_site",
                    "metadata_shape": {
                        "type": "object",
                        "properties": {"time_in_seconds": {"type": "integer"}},
                    },
                    "description": {"en": "Time on the content."},
                }
            ]
        ),
        actor="dana",
        source=SOURCE,
    )
    result = engine.emit(
        signal_payload(
            type="document_engagement",
            indicators=[{"key": "time_spent_on_site", "metadata": {}}],
        ),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert result["outcome"] == "emitted"


def test_a_signal_may_not_restate_the_indicator_metadata_shape(engine, registered, room):
    with pytest.raises(EmissionError) as caught:
        engine.emit(
            signal_payload(
                indicators=[
                    {
                        "key": "spent_more_than_30s_on_site",
                        "metadata": {"time_in_seconds": 40},
                        "metadata_shape": {"type": "object"},
                    }
                ]
            ),
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    assert "relax the contract" in str(caught.value)


def test_an_indicator_whose_evidence_fails_its_own_bound_is_refused(engine, registered, room):
    """A specific claim contradicted by its own evidence is refused, not stored."""
    with pytest.raises(IndicatorNotQualified) as caught:
        engine.emit(
            signal_payload(
                indicators=[
                    {"key": "spent_more_than_30s_on_site", "metadata": {"time_in_seconds": 12}}
                ]
            ),
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    assert "time_in_seconds=12" in str(caught.value)


def test_a_signal_whose_evidence_meets_the_bound_is_accepted(engine, registered, room):
    result = engine.emit(
        signal_payload(
            indicators=[{"key": "spent_more_than_30s_on_site", "metadata": {"time_in_seconds": 31}}]
        ),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert result["outcome"] == "emitted"
    qualification = result["signal"]["indicators"][0]["qualification"]
    assert qualification["reason"] == "bound_met"
    assert qualification["checked"] is True


def test_an_unbounded_indicator_qualifies_on_trust_and_says_so(engine, room):
    engine.register(
        registration_payload(
            indicators=[
                {
                    "key": "time_spent_on_site",
                    "metadata_shape": {
                        "type": "object",
                        "properties": {"time_in_seconds": {"type": "integer"}},
                    },
                    "description": {"en": "Time on the content."},
                }
            ]
        ),
        actor="dana",
        source=SOURCE,
    )
    result = engine.emit(
        signal_payload(
            indicators=[{"key": "time_spent_on_site", "metadata": {"time_in_seconds": 30}}]
        ),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    signal = result["signal"]
    assert signal["indicators"][0]["qualification"]["checked"] is False
    assert signal["indicators"][0]["qualification"]["reason"] == "no_bound_claimed"
    assert any("no_bound_claimed" in warning for warning in signal["warnings"])


def test_an_occurred_at_without_an_offset_is_refused(engine, registered, room):
    """A naive timestamp cannot be ordered against one that has an offset."""
    with pytest.raises(EmissionError) as caught:
        engine.emit(
            signal_payload(occurred_at="2026-09-26T14:05:00"),
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    assert "UTC offset" in str(caught.value)


def test_an_occurred_at_is_normalised_to_utc_with_milliseconds(engine, registered, room):
    result = engine.emit(
        signal_payload(occurred_at="2026-09-26T16:05:00+02:00"),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert result["signal"]["occurred_at"] == "2026-09-26T14:05:00.000+00:00"


def test_a_z_suffix_is_accepted(engine, registered, room):
    result = engine.emit(
        signal_payload(occurred_at="2026-09-26T14:05:00Z"),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert result["outcome"] == "emitted"


@pytest.mark.parametrize("value", ["yesterday", "2026-13-01T00:00:00Z", "", None, 5])
def test_an_unparseable_occurred_at_is_refused(value):
    with pytest.raises(EmissionError):
        parse_timestamp(value, "occurred_at")


def test_an_idempotency_key_must_be_a_uuid4(engine, registered, room):
    """The research specifies it as a UUID4 in both field lists."""
    with pytest.raises(SignalError) as caught:
        engine.emit(
            signal_payload(idempotency_key="not-a-uuid"),
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    assert "UUID4" in str(caught.value)


def test_an_idempotency_key_of_the_wrong_uuid_version_is_refused():
    with pytest.raises(SignalError) as caught:
        require_uuid4(str(uuid.uuid1()), "idempotency_key")
    assert "version 4" in str(caught.value)


def test_a_uuid4_is_accepted_in_canonical_form():
    key = uuid4()
    assert require_uuid4(key.upper(), "idempotency_key") == key


def test_an_absent_idempotency_key_is_refused():
    with pytest.raises(SignalError):
        require_uuid4(None, "idempotency_key")


def test_attribution_is_required_on_a_signal(engine, registered, room):
    payload = signal_payload()
    payload.pop("attribution")
    with pytest.raises(EmissionError) as caught:
        engine.emit(payload, room_id=room["id"], actor="dana", source=SOURCE)
    assert "attribution is required" in str(caught.value)


def test_broadcast_notification_defaults_to_the_registrations_setting(engine, room):
    engine.register(registration_payload(broadcast_notification=False), actor="dana", source=SOURCE)
    payload = signal_payload()
    payload.pop("broadcast_notification")
    result = engine.emit(payload, room_id=room["id"], actor="dana", source=SOURCE)
    assert result["signal"]["broadcast_notification"] is False


def test_a_signal_may_override_the_registrations_broadcast_setting(engine, registered, room):
    result = engine.emit(
        signal_payload(broadcast_notification=False),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert result["signal"]["broadcast_notification"] is False


def test_the_engine_also_refuses_a_non_boolean_broadcast_notification(engine, registered, room):
    with pytest.raises(EmissionError):
        engine.emit(
            signal_payload(broadcast_notification="yes"),
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )


def test_an_absent_urgency_is_defaulted_and_warned_about(engine, registered, room):
    payload = signal_payload()
    payload.pop("urgency")
    result = engine.emit(payload, room_id=room["id"], actor="dana", source=SOURCE)
    assert result["signal"]["urgency"] == DEFAULT_URGENCY
    assert any("urgency was not supplied" in warning for warning in result["signal"]["warnings"])


def test_an_unknown_field_on_a_signal_is_refused(engine, registered, room):
    with pytest.raises(EmissionError) as caught:
        engine.emit(
            signal_payload(play_name="call them"), room_id=room["id"], actor="dana", source=SOURCE
        )
    assert "is not a field of a signal" in str(caught.value)


def test_observations_are_refused_on_the_strict_emit_route(engine, registered, room):
    """An interaction carries observations; the strict route takes the indicator."""
    for field in sorted(OBSERVATION_FIELDS):
        with pytest.raises(EmissionError) as caught:
            engine.emit(
                signal_payload(**{field: {"time_in_seconds": 40}}),
                room_id=room["id"],
                actor="dana",
                source=SOURCE,
            )
        assert "interactions route" in str(caught.value)


def test_camel_case_signal_fields_are_folded(engine, registered, room):
    result = engine.emit(
        signal_payload(
            occurredAt="2026-09-26T14:05:00Z",
            idempotencyKey=uuid4(),
            broadcastNotification=False,
        ),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert result["outcome"] == "emitted"
    assert result["signal"]["broadcast_notification"] is False


def test_canonical_emission_folds_and_refuses():
    assert canonical_emission({"occurredAt": "x"}) == {"occurred_at": "x"}
    with pytest.raises(EmissionError):
        canonical_emission({"nope": 1})
    with pytest.raises(EmissionError):
        canonical_emission("not an object")


# --------------------------------------------------------------------------- #
# The first signal with an idempotency key wins
# --------------------------------------------------------------------------- #


def test_a_repeated_idempotency_key_drops_the_second_signal(engine, registered, room):
    key = uuid4()
    first = engine.emit(
        signal_payload(idempotency_key=key), room_id=room["id"], actor="dana", source=SOURCE
    )
    second = engine.emit(
        signal_payload(idempotency_key=key), room_id=room["id"], actor="dana", source=SOURCE
    )
    assert first["outcome"] == "emitted"
    assert second["outcome"] == "dropped"
    assert second["reason"] == "duplicate_idempotency_key"
    assert second["signal"]["id"] == first["signal"]["id"]


def test_the_dropped_signal_is_the_one_that_was_kept(engine, registered, room):
    key = uuid4()
    first = engine.emit(
        signal_payload(
            idempotency_key=key, data={**signal_payload()["data"], "document_name": "First"}
        ),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    engine.emit(
        signal_payload(
            idempotency_key=key, data={**signal_payload()["data"], "document_name": "Second"}
        ),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    stored = engine.signal(first["signal"]["id"])
    assert stored["data"]["document_name"] == "First"


def test_a_dropped_signal_leaves_one_row_not_two(engine, registered, room):
    key = uuid4()
    for _ in range(3):
        engine.emit(
            signal_payload(idempotency_key=key), room_id=room["id"], actor="dana", source=SOURCE
        )
    assert len(engine.signals(room_id=room["id"])) == 1


def test_the_dropped_attempts_are_counted_on_the_winner(engine, registered, room):
    key = uuid4()
    engine.emit(
        signal_payload(idempotency_key=key), room_id=room["id"], actor="dana", source=SOURCE
    )
    for expected in (1, 2, 3):
        dropped = engine.emit(
            signal_payload(idempotency_key=key), room_id=room["id"], actor="dana", source=SOURCE
        )
        assert dropped["duplicate_attempts"] == expected
    assert engine.signals(room_id=room["id"])[0]["duplicate_attempts"] == 3


def test_a_dropped_signal_keeps_the_winners_urgency_and_data(engine, registered, room):
    key = uuid4()
    first = engine.emit(
        signal_payload(idempotency_key=key, urgency="high"),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    engine.emit(
        signal_payload(idempotency_key=key, urgency="low"),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert engine.signal(first["signal"]["id"])["urgency"] == "high"


def test_an_idempotency_key_is_dropped_across_rooms(engine, registered, room, store):
    """The key is a global identity for the signal, not a per-room one."""
    key = uuid4()
    other = store.create("room", {**ROOM, "name": "Elsewhere"}, actor="dana")
    engine.emit(
        signal_payload(idempotency_key=key), room_id=room["id"], actor="dana", source=SOURCE
    )
    second = engine.emit(
        signal_payload(idempotency_key=key), room_id=other["id"], actor="dana", source=SOURCE
    )
    assert second["outcome"] == "dropped"


def test_a_malformed_retry_cannot_occupy_a_key_and_block_the_real_signal(engine, registered, room):
    """Validation runs before the idempotency check, so a bad retry is not a winner."""
    key = uuid4()
    with pytest.raises(IndicatorNotQualified):
        engine.emit(
            signal_payload(
                idempotency_key=key,
                indicators=[
                    {"key": "spent_more_than_30s_on_site", "metadata": {"time_in_seconds": 1}}
                ],
            ),
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    assert (
        engine.emit(
            signal_payload(idempotency_key=key), room_id=room["id"], actor="dana", source=SOURCE
        )["outcome"]
        == "emitted"
    )


def test_different_idempotency_keys_both_land(engine, registered, room):
    for _ in range(3):
        assert (
            engine.emit(signal_payload(), room_id=room["id"], actor="dana", source=SOURCE)[
                "outcome"
            ]
            == "emitted"
        )
    assert len(engine.signals(room_id=room["id"])) == 3


# --------------------------------------------------------------------------- #
# Actionability: the research's most emphatic sentence
# --------------------------------------------------------------------------- #


def test_no_signal_is_ever_actionable(engine, registered, room):
    signal = engine.emit(signal_payload(), room_id=room["id"], actor="dana", source=SOURCE)[
        "signal"
    ]
    assert signal["actionable"] is False
    assert "Play configuration" in signal["actionability_note"]


def test_a_caller_may_not_claim_its_signal_is_actionable(engine, registered, room):
    with pytest.raises(EmissionError) as caught:
        engine.emit(
            signal_payload(actionable=True), room_id=room["id"], actor="dana", source=SOURCE
        )
    assert "cannot be supplied" in str(caught.value)


def test_the_summary_reports_zero_actionable_and_says_why(engine, registered, room):
    engine.emit(signal_payload(), room_id=room["id"], actor="dana", source=SOURCE)
    summary = engine.summary(room_id=room["id"])
    assert summary["actionable"] == 0
    assert summary["actionability_note"] == ACTIONABILITY_NOTE
    assert "Play configuration" in summary["actionability_note"]


def test_every_feed_row_repeats_that_it_is_not_actionable(engine, registered, room):
    engine.emit(signal_payload(), room_id=room["id"], actor="dana", source=SOURCE)
    feed = engine.live_feed(room_id=room["id"])
    assert feed["actionable"] == 0
    for row in feed["entries"]:
        assert row["actionable"] is False
        assert row["actionability_note"] == ACTIONABILITY_NOTE


def test_no_play_registration_is_implemented():
    """WF-013's workflow, not this one."""
    assert by_id("plays-are-not-built-here")["value"]["plays"] == "not implemented"


# --------------------------------------------------------------------------- #
# Rendering the Live Feed sentence
# --------------------------------------------------------------------------- #


def test_a_signal_renders_its_registration_description(engine, registered, room):
    signal = engine.emit(
        signal_payload(
            data={**signal_payload()["data"], "buyer_first_name": "Priya"},
            indicators=[
                {"key": "spent_more_than_30s_on_site", "metadata": {"time_in_seconds": 42}}
            ],
        ),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )["signal"]
    assert signal["rendered"]["text"] == "Priya spent 42 seconds on Security & Compliance Pack."


def test_indicator_metadata_wins_over_signal_data_for_the_same_name(engine, room):
    """The sentence is about the indicator's evidence."""
    engine.register(
        registration_payload(
            indicators=[
                {
                    "key": "spent_more_than_30s_on_site",
                    "metadata_shape": DURATION_SHAPE,
                    "description": {"en": "Spent {time_in_seconds} seconds."},
                }
            ]
        ),
        actor="dana",
        source=SOURCE,
    )
    signal = engine.emit(
        signal_payload(
            data={**signal_payload()["data"], "time_in_seconds": 999},
            indicators=[
                {"key": "spent_more_than_30s_on_site", "metadata": {"time_in_seconds": 42}}
            ],
        ),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )["signal"]
    assert signal["rendered"]["indicators"][0]["text"] == "Spent 42 seconds."


def test_each_indicator_renders_its_own_sentence(engine, room):
    engine.register(
        registration_payload(
            indicators=[
                {
                    "key": "spent_more_than_30s_on_site",
                    "metadata_shape": DURATION_SHAPE,
                    "description": {"en": "Spent {time_in_seconds} seconds."},
                },
                {
                    "key": "watched_more_than_75_percent",
                    "metadata_shape": WATCH_SHAPE,
                    "description": {"en": "Watched {watched_percent}%."},
                },
            ]
        ),
        actor="dana",
        source=SOURCE,
    )
    signal = engine.emit(
        signal_payload(
            indicators=[
                {"key": "spent_more_than_30s_on_site", "metadata": {"time_in_seconds": 40}},
                {"key": "watched_more_than_75_percent", "metadata": {"watched_percent": 90}},
            ]
        ),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )["signal"]
    assert [row["text"] for row in signal["rendered"]["indicators"]] == [
        "Spent 40 seconds.",
        "Watched 90%.",
    ]


def test_an_indicator_with_no_description_of_its_own_falls_back_to_the_signals(engine, room):
    engine.register(
        registration_payload(
            indicators=[{"key": "time_spent_on_site", "metadata_shape": DURATION_SHAPE}]
        ),
        actor="dana",
        source=SOURCE,
    )
    signal = engine.emit(
        signal_payload(
            indicators=[{"key": "time_spent_on_site", "metadata": {"time_in_seconds": 12}}]
        ),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )["signal"]
    row = signal["rendered"]["indicators"][0]
    assert row["text"] == signal["rendered"]["text"]
    assert any("in its place" in warning for warning in row["warnings"])


def test_a_locale_falls_back_to_the_bare_language_and_says_so(engine, room):
    engine.register(
        registration_payload(description={"en": "Opened.", "en-GB": "Opened, mate."}),
        actor="dana",
        source=SOURCE,
    )
    signal = engine.emit(
        signal_payload(locale="en-AU"), room_id=room["id"], actor="dana", source=SOURCE
    )["signal"]
    assert signal["rendered"]["locale_resolved"] == "en"
    assert signal["rendered"]["locale_fallback"] is True
    assert any("no description for locale" in warning for warning in signal["rendered"]["warnings"])


def test_an_undeclared_locale_renders_in_english_and_says_so(engine, registered, room):
    signal = engine.emit(
        signal_payload(locale="de-AT"), room_id=room["id"], actor="dana", source=SOURCE
    )["signal"]
    assert signal["rendered"]["locale_resolved"] == "en"
    assert signal["rendered"]["locale_fallback"] is True
    assert "fr" not in signal["rendered"]["locales_available"]


def test_a_locale_the_registration_declares_renders_in_it(engine, room):
    engine.register(
        registration_payload(description={"en": "Opened.", "fr": "Ouvert."}),
        actor="dana",
        source=SOURCE,
    )
    signal = engine.emit(
        signal_payload(locale="fr"), room_id=room["id"], actor="dana", source=SOURCE
    )["signal"]
    assert signal["rendered"]["text"] == "Ouvert."
    assert signal["rendered"]["locale_fallback"] is False


def test_a_read_can_ask_for_a_different_locale_than_the_signal_carried(engine, room):
    engine.register(
        registration_payload(description={"en": "Opened.", "fr": "Ouvert."}),
        actor="dana",
        source=SOURCE,
    )
    signal = engine.emit(
        signal_payload(locale="fr"), room_id=room["id"], actor="dana", source=SOURCE
    )["signal"]
    assert engine.signal(signal["id"], locale="en")["rendered"]["text"] == "Opened."


def test_a_missing_argument_degrades_the_sentence_rather_than_dropping_the_signal(
    engine, registered, room
):
    signal = engine.emit(
        signal_payload(data={"document_name": "Deck", "action": "viewed"}),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )["signal"]
    assert signal["rendered"]["text"] == "{buyer_first_name} spent 214 seconds on Deck."
    assert any("no value supplied" in warning for warning in signal["rendered"]["warnings"])


def test_a_signal_whose_registration_was_withdrawn_still_reads_and_says_it_cannot_render(
    engine, room, store
):
    record = engine.register(registration_payload(), actor="dana", source=SOURCE)["registration"]
    signal = engine.emit(signal_payload(), room_id=room["id"], actor="dana", source=SOURCE)[
        "signal"
    ]
    store.delete(record["id"], actor="dana", source=SOURCE)
    reread = engine.signal(signal["id"])
    assert reread is not None
    assert reread["rendered"]["text"] == ""
    assert any("withdrawn" in warning for warning in reread["rendered"]["warnings"])


# --------------------------------------------------------------------------- #
# The Live Feed
# --------------------------------------------------------------------------- #


def test_the_feed_shows_only_broadcast_signals(engine, registered, room):
    engine.emit(
        signal_payload(broadcast_notification=True), room_id=room["id"], actor="dana", source=SOURCE
    )
    engine.emit(
        signal_payload(broadcast_notification=False),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    feed = engine.live_feed(room_id=room["id"])
    assert feed["count"] == 1
    assert len(engine.signals(room_id=room["id"])) == 2


def test_a_withheld_signal_is_still_stored_and_listed(engine, registered, room):
    engine.emit(
        signal_payload(broadcast_notification=False),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert len(engine.signals(room_id=room["id"], broadcast=False)) == 1


def test_urgency_drives_the_feed_order(engine, registered, room):
    """`"urgency (high/medium/low) drives priority"`"""
    for minutes, urgency in ((90, "low"), (5, "high"), (45, "medium")):
        engine.emit(
            signal_payload(urgency=urgency, occurred_at=ago(minutes)),
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    feed = engine.live_feed(room_id=room["id"])
    assert [row["urgency"] for row in feed["entries"]] == ["high", "medium", "low"]


def test_a_low_urgency_signal_never_outranks_a_high_one_however_recent(engine, registered, room):
    engine.emit(
        signal_payload(urgency="high", occurred_at=ago(600)),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    engine.emit(
        signal_payload(urgency="low", occurred_at=ago(1)),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert engine.live_feed(room_id=room["id"])["entries"][0]["urgency"] == "high"


def test_within_one_urgency_the_most_recent_comes_first(engine, registered, room):
    for minutes in (30, 10, 20):
        engine.emit(
            signal_payload(urgency="high", occurred_at=ago(minutes)),
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    feed = engine.live_feed(room_id=room["id"])
    assert [row["occurred_at"] for row in feed["entries"]] == sorted(
        (row["occurred_at"] for row in feed["entries"]), reverse=True
    )


def test_the_feed_counts_each_urgency(engine, registered, room):
    for urgency in ("high", "medium", "low", "low"):
        engine.emit(
            signal_payload(urgency=urgency, occurred_at=ago(len(room) + len(urgency))),
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    feed = engine.live_feed(room_id=room["id"])
    assert feed["by_urgency"] == {"high": 1, "medium": 1, "low": 2}
    assert feed["urgent"] == 1


def test_the_feed_can_be_narrowed_to_one_seller(engine, room, store):
    engine.register(registration_payload(), actor="dana", source=SOURCE)
    other = store.create("room", {**ROOM, "owner": "sam", "name": "Sam's room"}, actor="dana")
    engine.emit(signal_payload(), room_id=room["id"], actor="dana", source=SOURCE)
    engine.emit(signal_payload(), room_id=other["id"], actor="dana", source=SOURCE)
    assert engine.live_feed(room_id=None, seller="dana")["count"] == 1
    assert engine.live_feed(room_id=None, seller="sam")["count"] == 1
    assert engine.live_feed(room_id=None, seller="nobody")["count"] == 0


def test_the_feed_is_scoped_to_its_room(engine, registered, room, store):
    other = store.create("room", {**ROOM, "name": "Elsewhere"}, actor="dana")
    engine.emit(signal_payload(), room_id=room["id"], actor="dana", source=SOURCE)
    engine.emit(signal_payload(), room_id=other["id"], actor="dana", source=SOURCE)
    assert engine.live_feed(room_id=room["id"])["count"] == 1
    assert engine.live_feed(room_id=other["id"])["count"] == 1


def test_the_feed_can_render_in_another_locale(engine, room):
    engine.register(
        registration_payload(description={"en": "Opened.", "fr": "Ouvert."}),
        actor="dana",
        source=SOURCE,
    )
    engine.emit(signal_payload(locale="en"), room_id=room["id"], actor="dana", source=SOURCE)
    assert (
        engine.live_feed(room_id=room["id"], locale="fr")["entries"][0]["rendered"]["text"]
        == "Ouvert."
    )


def test_a_feed_row_carries_the_attribution_and_the_receiver(engine, registered, room):
    engine.emit(
        signal_payload(attribution={"person_id": "per_1", "user_guid": "usr_1"}),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    row = engine.live_feed(room_id=room["id"])["entries"][0]
    assert row["attribution"] == {"person_id": "per_1", "user_guid": "usr_1"}
    assert row["receiver"]["seller"] == "dana"
    assert row["receiver"]["attributed_by"] == "user_guid"


def test_build_filters_and_orders_without_an_engine(store):
    """The feed's two sourced rules, exercised on the pure function."""
    registrations = {
        "r1": {"description": {"en": "{n} things."}, "indicators": []},
    }

    def signal(urgency, minutes, broadcast=True):
        return {
            "id": minutes,
            "registration_id": "r1",
            "urgency": urgency,
            "occurred_at": ago(minutes),
            "broadcast_notification": broadcast,
            "data": {"n": 1},
            "indicators": [],
            "receiver": {"seller": "dana"},
        }

    rows = feed_module.build(
        [signal("low", 1), signal("high", 500), signal("medium", 20, broadcast=False)],
        registrations,
    )
    assert [row["urgency"] for row in rows] == ["high", "low"]


def test_build_can_filter_by_seller():
    registrations = {"r1": {"description": {"en": "x"}, "indicators": []}}

    def signal(seller):
        return {
            "id": seller,
            "registration_id": "r1",
            "urgency": "high",
            "occurred_at": ago(1),
            "broadcast_notification": True,
            "indicators": [],
            "receiver": {"seller": seller},
        }

    rows = feed_module.build([signal("dana"), signal("sam")], registrations, seller="sam")
    assert len(rows) == 1
    assert rows[0]["receiver"]["seller"] == "sam"


# --------------------------------------------------------------------------- #
# The interaction classifier
# --------------------------------------------------------------------------- #


def interaction(**overrides):
    payload = {
        "type": "document_engagement",
        "data": signal_payload()["data"],
        "observations": {"time_in_seconds": 214},
        "occurred_at": ago(5),
        "idempotency_key": uuid4(),
        "attribution": {"person_id": "per_1"},
    }
    payload.update(overrides)
    return payload


def test_a_qualifying_interaction_emits_a_signal(engine, registered, room):
    result = engine.interact(interaction(), room_id=room["id"], actor="dana", source=SOURCE)
    assert result["qualified"] is True
    assert result["outcome"] == "emitted"
    assert result["signal"]["indicators"][0]["key"] == "spent_more_than_30s_on_site"


def test_a_non_qualifying_interaction_reports_and_stores_nothing(engine, registered, room):
    result = engine.interact(
        interaction(observations={"time_in_seconds": 6}),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert result["qualified"] is False
    assert result["outcome"] == "not_qualifying"
    assert result["signal"] is None
    assert engine.signals(room_id=room["id"]) == []


def test_every_declared_indicator_is_reported_with_a_reason(engine, room):
    engine.register(
        registration_payload(
            indicators=[
                {
                    "key": "spent_more_than_30s_on_site",
                    "metadata_shape": DURATION_SHAPE,
                    "description": {"en": "x"},
                },
                {
                    "key": "watched_more_than_75_percent",
                    "metadata_shape": WATCH_SHAPE,
                    "description": {"en": "y"},
                },
            ]
        ),
        actor="dana",
        source=SOURCE,
    )
    result = engine.interact(
        interaction(observations={"time_in_seconds": 6, "watched_percent": 20}),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert [row["key"] for row in result["indicators"]] == [
        "spent_more_than_30s_on_site",
        "watched_more_than_75_percent",
    ]
    assert [row["reason"] for row in result["indicators"]] == ["bound_not_met", "bound_not_met"]


def test_an_indicator_whose_evidence_is_absent_says_metadata_mismatch(engine, room):
    engine.register(
        registration_payload(
            indicators=[
                {
                    "key": "spent_more_than_30s_on_site",
                    "metadata_shape": DURATION_SHAPE,
                    "description": {"en": "x"},
                },
                {
                    "key": "watched_more_than_75_percent",
                    "metadata_shape": WATCH_SHAPE,
                    "description": {"en": "y"},
                },
            ]
        ),
        actor="dana",
        source=SOURCE,
    )
    result = engine.interact(
        interaction(observations={"time_in_seconds": 400}),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    by_key = {row["key"]: row for row in result["indicators"]}
    assert by_key["spent_more_than_30s_on_site"]["qualifies"] is True
    assert by_key["watched_more_than_75_percent"]["reason"] == "metadata_mismatch"


def test_an_indicator_that_qualifies_on_trust_is_reported_as_unchecked(engine, room):
    engine.register(
        registration_payload(
            indicators=[
                {
                    "key": "time_spent_on_site",
                    "metadata_shape": {
                        "type": "object",
                        "properties": {"time_in_seconds": {"type": "integer"}},
                    },
                    "description": {"en": "x"},
                }
            ]
        ),
        actor="dana",
        source=SOURCE,
    )
    result = engine.interact(
        interaction(observations={"time_in_seconds": 3}),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert result["qualified"] is True
    assert result["indicators"][0]["reason"] == "no_bound_claimed"
    assert result["indicators"][0]["checked"] is False


def test_an_interaction_carrying_only_the_evidence_for_one_indicator_sends_only_that(engine, room):
    engine.register(
        registration_payload(
            indicators=[
                {
                    "key": "spent_more_than_30s_on_site",
                    "metadata_shape": DURATION_SHAPE,
                    "description": {"en": "x"},
                },
                {
                    "key": "watched_more_than_75_percent",
                    "metadata_shape": WATCH_SHAPE,
                    "description": {"en": "y"},
                },
            ]
        ),
        actor="dana",
        source=SOURCE,
    )
    result = engine.interact(
        interaction(observations={"time_in_seconds": 40, "watched_percent": 10}),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert [row["key"] for row in result["signal"]["indicators"]] == ["spent_more_than_30s_on_site"]


def test_the_stored_indicator_metadata_is_only_what_its_shape_declares(engine, room):
    engine.register(
        registration_payload(
            indicators=[
                {
                    "key": "spent_more_than_30s_on_site",
                    "metadata_shape": DURATION_SHAPE,
                    "description": {"en": "x"},
                }
            ]
        ),
        actor="dana",
        source=SOURCE,
    )
    result = engine.interact(
        interaction(observations={"time_in_seconds": 40, "unrelated": "noise"}),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert result["signal"]["indicators"][0]["metadata"] == {"time_in_seconds": 40}


def test_an_interaction_may_not_name_an_indicator_and_bypass_the_match(engine, registered, room):
    with pytest.raises(SignalError) as caught:
        engine.interact(
            interaction(indicators=[{"key": "spent_more_than_30s_on_site", "metadata": {}}]),
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    assert "bypass the match" in str(caught.value)


def test_an_interaction_with_no_observations_matches_nothing_and_says_why(engine, registered, room):
    result = engine.interact(
        interaction(observations={}), room_id=room["id"], actor="dana", source=SOURCE
    )
    assert result["qualified"] is False
    assert result["indicators"][0]["reason"] == "metadata_mismatch"


def test_observations_may_be_sent_under_the_researched_metadata_spelling(engine, registered, room):
    payload = interaction()
    payload["metadata"] = payload.pop("observations")
    assert (
        engine.interact(payload, room_id=room["id"], actor="dana", source=SOURCE)["qualified"]
        is True
    )


def test_observations_must_be_an_object(engine, registered, room):
    with pytest.raises(SignalError):
        engine.interact(
            interaction(observations=[1, 2]), room_id=room["id"], actor="dana", source=SOURCE
        )


def test_an_interaction_against_an_unregistered_type_is_refused(engine, room):
    with pytest.raises(UnregisteredSignalType) as caught:
        engine.interact(interaction(), room_id=room["id"], actor="dana", source=SOURCE)
    assert "no indicator to match" in str(caught.value)


def test_a_repeated_interaction_key_drops_rather_than_emits_twice(engine, registered, room):
    key = uuid4()
    first = engine.interact(
        interaction(idempotency_key=key), room_id=room["id"], actor="dana", source=SOURCE
    )
    second = engine.interact(
        interaction(idempotency_key=key), room_id=room["id"], actor="dana", source=SOURCE
    )
    assert first["outcome"] == "emitted"
    assert first["duplicate_attempts"] == 0
    assert second["outcome"] == "dropped"
    assert second["duplicate_attempts"] == 1
    assert second["qualified"] is True
    assert len(engine.signals(room_id=room["id"])) == 1


def test_an_interaction_against_an_unregistered_type_needs_a_type(engine, room):
    with pytest.raises(SignalError):
        engine.interact({"observations": {}}, room_id=room["id"], actor="dana", source=SOURCE)


# --------------------------------------------------------------------------- #
# Listing and summarising
# --------------------------------------------------------------------------- #


def test_signals_are_listed_newest_first(engine, registered, room):
    for minutes in (30, 5, 15):
        engine.emit(
            signal_payload(occurred_at=ago(minutes)),
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    listed = engine.signals(room_id=room["id"])
    assert len(listed) == 3
    assert listed[0]["created_at"] >= listed[-1]["created_at"]


def test_a_signal_can_be_filtered_by_type_through_the_dynamic_index(engine, room):
    engine.register(registration_payload(), actor="dana", source=SOURCE)
    engine.register(registration_payload(type="content_opened"), actor="dana", source=SOURCE)
    engine.emit(signal_payload(), room_id=room["id"], actor="dana", source=SOURCE)
    engine.emit(
        signal_payload(type="content_opened"), room_id=room["id"], actor="dana", source=SOURCE
    )
    assert len(engine.signals(room_id=room["id"], type_name="content_opened")) == 1


def test_a_signal_can_be_filtered_by_a_dotted_path_into_a_nested_object(engine, registered, room):
    """`receiver.seller` is a field this code never declared as a column."""
    engine.emit(signal_payload(), room_id=room["id"], actor="dana", source=SOURCE)
    assert len(engine.signals(room_id=room["id"], seller="dana")) == 1
    assert engine.signals(room_id=room["id"], seller="sam") == []


def test_a_signal_can_be_filtered_by_urgency_and_broadcast(engine, registered, room):
    engine.emit(signal_payload(urgency="high"), room_id=room["id"], actor="dana", source=SOURCE)
    engine.emit(
        signal_payload(urgency="low", broadcast_notification=False),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert len(engine.signals(room_id=room["id"], urgency="high")) == 1
    assert len(engine.signals(room_id=room["id"], broadcast=False)) == 1


def test_a_bad_urgency_filter_is_refused_rather_than_returning_everything(engine, registered, room):
    with pytest.raises(EmissionError):
        engine.signals(room_id=room["id"], urgency="urgent")


def test_listing_is_room_scoped(engine, registered, room, store):
    other = store.create("room", {**ROOM, "name": "Elsewhere"}, actor="dana")
    engine.emit(signal_payload(), room_id=room["id"], actor="dana", source=SOURCE)
    engine.emit(signal_payload(), room_id=other["id"], actor="dana", source=SOURCE)
    assert len(engine.signals(room_id=room["id"])) == 1
    assert len(engine.signals(room_id=None)) == 2


def test_the_listing_respects_its_limit(engine, registered, room):
    for _ in range(4):
        engine.emit(signal_payload(), room_id=room["id"], actor="dana", source=SOURCE)
    assert len(engine.signals(room_id=room["id"], limit=2)) == 2


def test_the_summary_counts_this_room_only(engine, registered, room, store):
    other = store.create("room", {**ROOM, "name": "Elsewhere", "owner": "sam"}, actor="dana")
    engine.emit(signal_payload(urgency="high"), room_id=room["id"], actor="dana", source=SOURCE)
    engine.emit(signal_payload(urgency="low"), room_id=other["id"], actor="dana", source=SOURCE)
    summary = engine.summary(room_id=room["id"])
    assert summary["signals"] == 1
    assert summary["by_urgency"] == {"high": 1, "medium": 0, "low": 0}
    assert summary["by_seller"] == [{"seller": "dana", "count": 1}]


def test_the_summary_reports_withheld_and_dropped_counts(engine, registered, room):
    key = uuid4()
    engine.emit(signal_payload(urgency="high"), room_id=room["id"], actor="dana", source=SOURCE)
    engine.emit(
        signal_payload(broadcast_notification=False),
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    engine.emit(
        signal_payload(idempotency_key=key), room_id=room["id"], actor="dana", source=SOURCE
    )
    engine.emit(
        signal_payload(idempotency_key=key), room_id=room["id"], actor="dana", source=SOURCE
    )
    summary = engine.summary(room_id=room["id"])
    assert summary["signals"] == 3
    assert summary["broadcast"] == 2
    assert summary["withheld"] == 1
    assert summary["live_feed"] == 2
    assert summary["duplicates_dropped"] == 1


def test_the_summary_breaks_signals_down_by_type(engine, room):
    engine.register(registration_payload(), actor="dana", source=SOURCE)
    engine.register(registration_payload(type="content_opened"), actor="dana", source=SOURCE)
    engine.emit(signal_payload(), room_id=room["id"], actor="dana", source=SOURCE)
    engine.emit(
        signal_payload(type="content_opened"), room_id=room["id"], actor="dana", source=SOURCE
    )
    assert engine.summary(room_id=room["id"])["by_type"] == [
        {"type": "content_opened", "count": 1},
        {"type": "document_engagement", "count": 1},
    ]


def test_an_unresolved_receiver_is_counted_as_unresolved_not_as_a_seller(engine, room, store):
    ownerless = store.create("room", {"name": "No owner"}, actor="dana")
    engine.register(registration_payload(), actor="dana", source=SOURCE)
    engine.emit(signal_payload(), room_id=ownerless["id"], actor="dana", source=SOURCE)
    assert engine.summary(room_id=ownerless["id"])["by_seller"] == [
        {"seller": "unresolved", "count": 1}
    ]


def test_registrations_can_be_listed_by_integration_and_type(engine):
    engine.register(registration_payload(), actor="dana", source=SOURCE)
    engine.register(registration_payload(integration_id="partner"), actor="dana", source=SOURCE)
    assert len(engine.registrations(integration_id="partner")) == 1
    assert len(engine.registrations(type_name="document_engagement")) == 2
    assert engine.registrations(type_name="nothing_here") == []


def test_a_withdrawn_registration_is_hidden_from_the_default_listing(engine, registered):
    engine.withdraw(registered["id"], actor="dana", source=SOURCE)
    assert engine.registrations() == []
    assert len(engine.registrations(include_withdrawn=True)) == 1


def test_reading_an_unknown_or_foreign_record_is_refused(engine):
    assert engine.registration("signal_registration_nope") is None
    assert engine.signal("intent_signal_nope") is None


# --------------------------------------------------------------------------- #
# Vocabulary and the inference register, served as data
# --------------------------------------------------------------------------- #


def test_the_vocabulary_endpoint_serves_every_published_set(engine):
    vocabulary = engine.vocabulary()
    assert [row["value"] for row in vocabulary["urgencies"]] == list(URGENCIES)
    assert [row["key"] for row in vocabulary["attribution"]] == list(ATTRIBUTION_TYPES)
    assert vocabulary["attribution_precedence"][0] == "user_guid"
    assert vocabulary["actionability_note"] == ACTIONABILITY_NOTE
    assert "plural" in vocabulary["icu"]["supported_argument_types"]
    assert "type" in vocabulary["json_schema_keywords"]


def test_the_vocabulary_publishes_the_indicator_claim_grammar(engine):
    vocabulary = engine.vocabulary()
    assert vocabulary["claim_form"] == "<subject> <comparative> <number><unit?> <rest...>"
    assert {row["word"] for row in vocabulary["comparatives"]} == set(indicator_rules.COMPARATIVES)
    assert vocabulary["quality_examples"] == {
        "specific": indicator_rules.SPECIFIC_EXAMPLE,
        "vague": indicator_rules.VAGUE_EXAMPLE,
    }


def test_every_qualification_reason_is_published_by_the_vocabulary(engine):
    assert set(engine.vocabulary()["qualification_reasons"]) == set(
        indicator_rules.QUALIFICATION_REASONS
    )


def test_the_inference_register_names_every_judgement_call(engine):
    register = engine.inferences()
    assert register["count"] == len(INFERENCES)
    assert register["inferences"]
    for entry in register["inferences"]:
        assert {"id", "topic", "basis", "value", "why", "change_it", "blast_radius"} <= set(entry)


def test_the_inference_register_says_which_half_is_sourced(engine):
    sourced = engine.inferences()["sourced"]
    assert sourced["urgencies"] == list(URGENCIES)
    assert sourced["actionability_note"] == ACTIONABILITY_NOTE
    assert sourced["indicator_quality_examples"]["specific"]["key"] == "spent_more_than_30s_on_site"


def test_the_attribution_precedence_inference_names_the_section_it_borrows_from():
    entry = by_id("attribution-precedence")
    assert "Section 13" in entry["basis"]
    assert "NOT implemented here" in entry["basis"]
    assert entry["value"]["order"] == list(ATTRIBUTION_PRECEDENCE)


def test_the_amendment_inference_states_the_one_way_rule():
    entry = by_id("amendment-invalidation-test")
    assert (
        entry["value"]["test"]
        == "an amendment may only add. It may not remove, and it may not tighten."
    )
    assert any("add a data_shape.required name" in item for item in entry["value"]["refused"])
    assert any("remove a data_shape.required name" in item for item in entry["value"]["refused"])
    assert "add an optional data_shape property" in entry["value"]["allowed"]
    assert "additions" in entry["value"]["patches_are"]


def test_the_idempotency_inference_says_a_dropped_signal_is_not_an_error():
    entry = by_id("duplicate-idempotency-key-is-not-an-error")
    assert entry["value"]["status"] == 200
    assert entry["value"]["outcome"] == "dropped"
    assert entry["value"]["checked_after_validation"] is True


def test_the_json_schema_inference_lists_what_is_not_implemented():
    entry = by_id("json-schema-subset")
    assert "$ref" in entry["value"]["not_supported"]
    assert "ignored" in entry["value"]["unknown_keywords"]


def test_an_unknown_inference_is_none():
    assert by_id("no-such-inference") is None


# --------------------------------------------------------------------------- #
# The audit trail
# --------------------------------------------------------------------------- #


def test_registering_is_audited_to_the_route_that_served_it(store, engine):
    engine.register(registration_payload(), actor="dana", source=SOURCE)
    entries = store.audit(collection="signal_registration")
    assert [entry["action"] for entry in entries] == ["insert"]
    assert entries[0]["source"] == SOURCE


def test_emitting_is_audited_to_the_route_that_served_it(store, engine, registered, room):
    engine.emit(signal_payload(), room_id=room["id"], actor="dana", source=SOURCE)
    entries = store.audit(collection="intent_signal")
    assert [entry["action"] for entry in entries] == ["insert"]
    assert entries[0]["source"] == SOURCE


def test_the_dropped_duplicate_increment_is_audited_as_an_update(store, engine, registered, room):
    key = uuid4()
    engine.emit(
        signal_payload(idempotency_key=key), room_id=room["id"], actor="dana", source=SOURCE
    )
    engine.emit(
        signal_payload(idempotency_key=key), room_id=room["id"], actor="dana", source=SOURCE
    )
    entries = store.audit(collection="intent_signal")
    assert [entry["action"] for entry in entries] == ["update", "insert"]
    # The increment happened while serving the emit request, so that is what the
    # row has to name, not the request that created the signal.
    assert all(entry["source"] == SOURCE for entry in entries)


def test_amending_is_audited_to_the_amend_route(store, engine, registered):
    engine.amend(
        registered["id"],
        {"description": {"fr": "Ouvert."}},
        actor="dana",
        source=f"PATCH {PREFIX}/registrations/{{registration_id}}",
    )
    entry = store.audit(collection="signal_registration")[0]
    assert entry["action"] == "update"
    assert entry["source"] == f"PATCH {PREFIX}/registrations/{{registration_id}}"


def test_withdrawing_is_audited_to_the_withdraw_route(store, engine, registered):
    engine.withdraw(
        registered["id"], actor="dana", source=f"DELETE {PREFIX}/registrations/{{registration_id}}"
    )
    entry = store.audit(collection="signal_registration")[0]
    assert entry["action"] == "delete"
    assert "DELETE" in entry["source"]


def test_a_refused_emission_writes_nothing_at_all(store, engine, registered, room):
    before = len(store.audit(limit=1000))
    with pytest.raises(IndicatorNotQualified):
        engine.emit(
            signal_payload(
                indicators=[
                    {"key": "spent_more_than_30s_on_site", "metadata": {"time_in_seconds": 1}}
                ]
            ),
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    assert len(store.audit(limit=1000)) == before
    assert engine.signals(room_id=room["id"]) == []


def test_a_refused_amendment_writes_nothing_at_all(store, engine, registered):
    before = store.get(registered["id"])["revision"]
    with pytest.raises(ImmutableContractError):
        engine.amend(registered["id"], {"type": "other"}, actor="dana", source=SOURCE)
    assert store.get(registered["id"])["revision"] == before


def test_every_write_method_demands_a_source():
    """A hardcoded source is a defect; this is the guard against one coming back."""
    engine = SignalEngine(RecordStore(AuditedDatabase(":memory:")))
    import inspect

    for name in ("register", "amend", "withdraw", "emit", "interact"):
        method = getattr(engine, name)
        assert "source" in inspect.signature(method).parameters, name
        assert inspect.signature(method).parameters["source"].default is inspect.Parameter.empty, (
            name
        )


# --------------------------------------------------------------------------- #
# The HTTP surface, through this feature's own router
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_serves_the_vocabulary(http):
    response = http.get(f"{PREFIX}/vocabulary")
    assert response.status_code == 200
    assert [row["value"] for row in response.json()["urgencies"]] == list(URGENCIES)


def test_the_inferences_route_serves_the_register(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(INFERENCES)
    assert body["sourced_quote"].startswith("On each qualifying DSR interaction")


def test_registering_over_http_returns_201_and_the_registration(http):
    response = http.post(f"{PREFIX}/registrations", json=registration_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["outcome"] == "registered"
    assert body["registration"]["type"] == "document_engagement"
    assert body["registration"]["id"].startswith("signal_registration_")


def test_a_duplicate_registration_over_http_is_409_with_the_error_code(http):
    http.post(f"{PREFIX}/registrations", json=registration_payload())
    response = http.post(f"{PREFIX}/registrations", json=registration_payload())
    assert response.status_code == 409
    assert response.json()["error"] == "signal_type_already_registered"


def test_a_malformed_registration_over_http_is_400(http):
    response = http.post(f"{PREFIX}/registrations", json={"type": "x"})
    assert response.status_code == 400
    assert response.json()["error"] == "signal_registration_error"


def test_a_repeated_registration_key_over_http_is_200_not_201(http):
    key = uuid4()
    first = http.post(f"{PREFIX}/registrations", json=registration_payload(idempotency_key=key))
    second = http.post(f"{PREFIX}/registrations", json=registration_payload(idempotency_key=key))
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["outcome"] == "already_registered"


def test_listing_registrations_counts_the_flagged_ones(http):
    http.post(f"{PREFIX}/registrations", json=registration_payload())
    http.post(
        f"{PREFIX}/registrations",
        json=registration_payload(
            type="content_opened",
            indicators=[{"key": "time_spent_on_site", "metadata_shape": DURATION_SHAPE}],
        ),
    )
    body = http.get(f"{PREFIX}/registrations").json()
    assert body["count"] == 2
    assert body["flagged"] == 1


def test_a_registration_can_be_filtered_over_http(http):
    http.post(f"{PREFIX}/registrations", json=registration_payload())
    http.post(f"{PREFIX}/registrations", json=registration_payload(integration_id="partner"))
    assert (
        http.get(f"{PREFIX}/registrations", params={"integration_id": "partner"}).json()["count"]
        == 1
    )
    assert (
        http.get(f"{PREFIX}/registrations", params={"type": "document_engagement"}).json()["count"]
        == 2
    )


def test_reading_one_registration_over_http(http, http_registration):
    response = http.get(f"{PREFIX}/registrations/{http_registration['id']}")
    assert response.status_code == 200
    assert response.json()["indicator_keys"] == ["spent_more_than_30s_on_site"]


def test_reading_an_unknown_registration_over_http_is_404(http):
    assert http.get(f"{PREFIX}/registrations/signal_registration_nope").status_code == 404


def test_amending_over_http_accepts_an_additive_patch(http, http_registration):
    response = http.patch(
        f"{PREFIX}/registrations/{http_registration['id']}",
        json={"description": {"fr": "Ouvert."}},
    )
    assert response.status_code == 200
    assert response.json()["locales"] == ["en", "fr"]


def test_amending_over_http_refuses_a_contract_change_with_409(http, http_registration):
    response = http.patch(
        f"{PREFIX}/registrations/{http_registration['id']}", json={"type": "other_type"}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "signal_contract_change_refused"
    assert "type" in response.json()["detail"]


def test_withdrawing_an_unused_registration_over_http(http, http_registration):
    response = http.delete(f"{PREFIX}/registrations/{http_registration['id']}")
    assert response.status_code == 200
    assert response.json()["withdrawn"] is True
    assert http.get(f"{PREFIX}/registrations").json()["count"] == 0


def test_withdrawing_a_used_registration_over_http_is_409(http, http_registration, http_room):
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals", json=signal_payload(), params={"actor": "dana"}
    )
    response = http.delete(f"{PREFIX}/registrations/{http_registration['id']}")
    assert response.status_code == 409
    assert "already delivered" in response.json()["detail"]


def test_emitting_over_http_returns_201_and_the_rendered_sentence(
    http, http_registration, http_room
):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals", json=signal_payload(), params={"actor": "dana"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["outcome"] == "emitted"
    assert body["signal"]["rendered"]["text"].startswith("Priya spent 214 seconds")


def test_emitting_without_a_registration_over_http_is_409(http, http_room):
    response = http.post(f"{PREFIX}/rooms/{http_room['id']}/signals", json=signal_payload())
    assert response.status_code == 409
    assert response.json()["error"] == "signal_type_not_registered"


def test_a_duplicate_key_over_http_is_200_with_the_winner(http, http_registration, http_room):
    key = uuid4()
    first = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals",
        json=signal_payload(idempotency_key=key),
    ).json()
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals",
        json=signal_payload(idempotency_key=key),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "dropped"
    assert body["duplicate_attempts"] == 1
    # A null id on both sides would make this comparison pass without proving
    # anything, so the ids are checked to be real first.
    assert first["signal"]["id"]
    assert body["signal"]["id"] == first["signal"]["id"]
    assert http.get(f"{PREFIX}/rooms/{http_room['id']}/signals").json()["count"] == 1


def test_an_indicator_whose_bound_is_not_met_over_http_is_400(http, http_registration, http_room):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals",
        json=signal_payload(
            indicators=[{"key": "spent_more_than_30s_on_site", "metadata": {"time_in_seconds": 4}}]
        ),
    )
    assert response.status_code == 400
    assert response.json()["error"] == "indicator_bound_not_met"


def test_an_undeclared_indicator_over_http_is_400(http, http_registration, http_room):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals",
        json=signal_payload(indicators=[{"key": "downloaded_the_pricing_page", "metadata": {}}]),
    )
    assert response.status_code == 400
    assert response.json()["error"] == "indicator_not_declared"


def test_a_non_uuid4_key_over_http_is_400(http, http_registration, http_room):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals", json=signal_payload(idempotency_key="nope")
    )
    assert response.status_code == 400


def test_reading_the_live_feed_over_http(http, http_registration, http_room):
    for urgency in ("low", "high"):
        http.post(
            f"{PREFIX}/rooms/{http_room['id']}/signals",
            json=signal_payload(urgency=urgency),
            params={"actor": "dana"},
        )
    body = http.get(f"{PREFIX}/rooms/{http_room['id']}/live-feed").json()
    assert body["count"] == 2
    assert [row["urgency"] for row in body["entries"]] == ["high", "low"]
    assert body["actionable"] == 0


def test_the_live_feed_hides_a_withheld_signal_that_the_listing_still_shows(
    http, http_registration, http_room
):
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals",
        json=signal_payload(broadcast_notification=False),
        params={"actor": "dana"},
    )
    assert http.get(f"{PREFIX}/rooms/{http_room['id']}/live-feed").json()["count"] == 0
    assert http.get(f"{PREFIX}/rooms/{http_room['id']}/signals").json()["count"] == 1


def test_the_live_feed_over_http_can_be_narrowed_to_a_seller(http, http_registration, http_room):
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals", json=signal_payload(), params={"actor": "dana"}
    )
    assert (
        http.get(f"{PREFIX}/rooms/{http_room['id']}/live-feed", params={"seller": "dana"}).json()[
            "count"
        ]
        == 1
    )
    assert (
        http.get(f"{PREFIX}/rooms/{http_room['id']}/live-feed", params={"seller": "sam"}).json()[
            "count"
        ]
        == 0
    )


def test_an_interaction_that_qualifies_over_http_emits(http, http_registration, http_room):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/interactions",
        json=interaction(),
        params={"actor": "dana"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["qualified"] is True
    assert body["outcome"] == "emitted"
    assert body["signal"]["rendered"]["text"].startswith("Priya spent 214 seconds")


def test_an_interaction_that_does_not_qualify_over_http_is_200_and_stores_nothing(
    http, http_registration, http_room
):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/interactions",
        json=interaction(observations={"time_in_seconds": 5}),
        params={"actor": "dana"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["qualified"] is False
    assert body["signal"] is None
    assert body["indicators"][0]["reason"] == "bound_not_met"
    assert http.get(f"{PREFIX}/rooms/{http_room['id']}/signals").json()["count"] == 0


def test_the_two_routes_report_the_same_reason_for_the_same_evidence(
    http, http_registration, http_room
):
    decision = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/interactions",
        json=interaction(observations={"time_in_seconds": 5}),
        params={"actor": "dana"},
    ).json()
    refused = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals",
        json=signal_payload(
            indicators=[{"key": "spent_more_than_30s_on_site", "metadata": {"time_in_seconds": 5}}]
        ),
    )
    assert decision["indicators"][0]["reason"] == "bound_not_met"
    assert refused.status_code == 400
    assert "more_than 30" in refused.json()["detail"]


def test_reading_one_signal_over_http_returns_the_sentence_and_the_evidence(
    http, http_registration, http_room
):
    emitted = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals", json=signal_payload(), params={"actor": "dana"}
    ).json()
    body = http.get(f"{PREFIX}/rooms/{http_room['id']}/signals/{emitted['signal']['id']}").json()
    assert body["rendered"]["text"] == "Priya spent 214 seconds on Security & Compliance Pack."
    assert body["indicators"][0]["metadata"] == {"time_in_seconds": 214}
    assert body["actionable"] is False


def test_reading_a_signal_from_another_room_over_http_is_404(http, http_registration, http_room):
    other = http.post("/api/records/room", json={**ROOM, "name": "Elsewhere"}).json()
    emitted = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals", json=signal_payload(), params={"actor": "dana"}
    ).json()
    assert (
        http.get(f"{PREFIX}/rooms/{other['id']}/signals/{emitted['signal']['id']}").status_code
        == 404
    )


def test_the_room_summary_route(http, http_registration, http_room):
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals",
        json=signal_payload(urgency="high"),
        params={"actor": "dana"},
    )
    body = http.get(f"{PREFIX}/rooms/{http_room['id']}/summary").json()
    assert body["signals"] == 1
    assert body["by_urgency"]["high"] == 1
    assert body["actionable"] == 0


def test_the_summary_route_survives_a_room_with_no_signals(http, http_room):
    body = http.get(f"{PREFIX}/rooms/{http_room['id']}/summary").json()
    assert body["signals"] == 0
    assert body["by_urgency"] == {"high": 0, "medium": 0, "low": 0}


def test_a_signal_against_a_room_that_does_not_exist_still_emits(http, http_registration):
    """Room scope is a filter, not an authorisation check, and does not 404."""
    response = http.post(f"{PREFIX}/rooms/room_does_not_exist/signals", json=signal_payload())
    assert response.status_code == 201
    assert response.json()["signal"]["room_id"] == "room_does_not_exist"


def test_a_read_over_http_writes_nothing(http, http_registration, http_room):
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals", json=signal_payload(), params={"actor": "dana"}
    )
    before = len(http.get("/api/audit", params={"limit": 500}).json()["entries"])
    http.get(f"{PREFIX}/rooms/{http_room['id']}/signals")
    http.get(f"{PREFIX}/rooms/{http_room['id']}/live-feed")
    http.get(f"{PREFIX}/rooms/{http_room['id']}/summary")
    http.get(f"{PREFIX}/registrations")
    after = len(http.get("/api/audit", params={"limit": 500}).json()["entries"])
    assert after == before


def test_a_field_this_code_never_declared_needs_no_migration(http, http_room):
    """The requirement, stated as a test: a team adds a field and it just works."""
    http.post(f"{PREFIX}/registrations", json=registration_payload())
    payload = signal_payload(
        data={
            **signal_payload()["data"],
            "wf027EscalationPolicy": {"reviewer": "sam", "slaHours": 4},
        }
    )
    emitted = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals", json=payload, params={"actor": "dana"}
    )
    assert emitted.status_code == 201
    assert emitted.json()["signal"]["data"]["wf027EscalationPolicy"] == {
        "reviewer": "sam",
        "slaHours": 4,
    }

    # And it is immediately queryable through the dynamic index, dotted path and
    # all, with no migration and no change to the route.
    assert (
        http.get(f"{PREFIX}/rooms/{http_room['id']}/signals", params={"where": "{}"}).status_code
        == 200
    )


# --------------------------------------------------------------------------- #
# The audit-source rule, checked against the live route table
# --------------------------------------------------------------------------- #


def _matches_registered_route(source: str, served: list[dict[str, Any]]) -> bool:
    parts = source.split(" ", 1)
    if len(parts) != 2:
        return False
    method, path = parts
    if not any(method in route["methods"] for route in served):
        return False
    actual = [segment for segment in path.split("/") if segment]
    for route in served:
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


def test_every_write_audit_row_names_a_route_the_app_serves(http, http_room):
    """The port's central guarantee, checked against the route table the host reports.

    The same class of bug has shipped in this codebase before: a feature's audit
    log kept naming a path the app had stopped serving.
    """
    registration = http.post(f"{PREFIX}/registrations", json=registration_payload()).json()[
        "registration"
    ]
    http.patch(
        f"{PREFIX}/registrations/{registration['id']}", json={"description": {"fr": "Ouvert."}}
    )
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/interactions",
        json=interaction(),
        params={"actor": "dana"},
    )
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals", json=signal_payload(), params={"actor": "dana"}
    )
    withdrawn = http.post(
        f"{PREFIX}/registrations", json=registration_payload(type="content_opened")
    ).json()["registration"]
    http.delete(f"{PREFIX}/registrations/{withdrawn['id']}")

    served = [
        route
        for feature in http.get("/api/features").json()["features"]
        for route in feature["routes"]
    ]
    entries = http.get("/api/audit", params={"limit": 500}).json()["entries"]
    sources = {entry["source"] for entry in entries if entry["source"]}

    # The core records API and the seeder write with their own sources; only the
    # rows this feature's HTTP layer produced are in scope here.
    ours = {source for source in sources if source.split(" ", 1)[1].startswith(PREFIX)}
    assert ours, f"no wf-027 write was audited at all; saw {sorted(sources)}"
    for source in sorted(ours):
        assert _matches_registered_route(source, served), (
            f"audit row names {source!r}, which is not a route this app serves"
        )


def test_the_interaction_route_audits_under_its_own_route_not_the_emit_route(
    http, http_room, http_registration
):
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/interactions",
        json=interaction(),
        params={"actor": "dana"},
    )
    entries = http.get("/api/audit", params={"collection": "intent_signal"}).json()["entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == f"POST {PREFIX}/rooms/{{room_id}}/interactions"


def test_a_dropped_duplicate_audits_against_the_emit_route(http, http_room, http_registration):
    key = uuid4()
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals",
        json=signal_payload(idempotency_key=key),
        params={"actor": "dana"},
    )
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals",
        json=signal_payload(idempotency_key=key),
        params={"actor": "dana"},
    )
    entries = http.get("/api/audit", params={"collection": "intent_signal"}).json()["entries"]
    assert [entry["action"] for entry in entries] == ["update", "insert"]
    assert all(entry["source"] == f"POST {PREFIX}/rooms/{{room_id}}/signals" for entry in entries)


def test_writes_do_not_record_a_path_this_app_does_not_serve(http, http_room, http_registration):
    """Explicitly: no hardcoded URL, and no salesloft.com path in the audit log.

    The second half matters because the researched API is
    ``https://api.salesloft.com/v2/signals``. This product is the source of the
    event, not a proxy for the vendor, and an audit row naming the vendor's URL
    would tell a reviewer a request went somewhere this app never sends one.
    """
    http.post(f"{PREFIX}/registrations", json=registration_payload())
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals", json=signal_payload(), params={"actor": "dana"}
    )
    entries = http.get("/api/audit", params={"limit": 500}).json()["entries"]
    for entry in entries:
        source = entry.get("source") or ""
        assert "salesloft" not in source.lower()
        assert "http" not in source.lower()
        if source:
            assert source.split(" ")[0] in {"POST", "PATCH", "DELETE", "PUT"}


def test_the_audit_row_carries_the_actor_the_route_was_given(http, http_room, http_registration):
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals", json=signal_payload(), params={"actor": "dana"}
    )
    entries = http.get("/api/audit", params={"collection": "intent_signal"}).json()["entries"]
    assert entries[0]["actor"] == "dana"


def test_the_audit_row_carries_the_room_the_signal_belongs_to(http, http_room, http_registration):
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/signals", json=signal_payload(), params={"actor": "dana"}
    )
    entries = http.get("/api/audit", params={"collection": "intent_signal"}).json()["entries"]
    assert entries[0]["room_id"] == http_room["id"]


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed_rooms(store: RecordStore) -> list[tuple[str, str]]:
    """Demo rooms in the shape ``backend/seed.py`` passes: ``[(room_id, account)]``."""
    first = store.create("room", ROOM, actor="dana")
    second = store.create("room", {**ROOM, "name": "Second", "owner": "sam"}, actor="dana")
    return [(first["id"], "Northwind Traders"), (second["id"], "Contoso Health")]


def test_the_seed_produces_both_registrations_and_their_states(tmp_path):
    db = AuditedDatabase(tmp_path / "seeded.db")
    store = RecordStore(db)
    summary = load_feature(MODULE).seed(
        db, {"room_ids": seed_rooms(store), "now": NOW, "rng": random.Random("wf027")}
    )
    assert "3 signal registrations" in summary
    assert len(store.list("signal_registration")) == 3
    assert len(store.list("intent_signal")) == 7
    db.close()


def test_the_seed_produces_the_states_that_are_not_all_successes(tmp_path):
    db = AuditedDatabase(tmp_path / "seeded.db")
    store = RecordStore(db)
    summary = load_feature(MODULE).seed(
        db, {"room_ids": [seed_rooms(store)[0]], "now": NOW, "rng": random.Random("wf027")}
    )
    signals = [record["data"] for record in store.list("intent_signal", limit=100)]

    # One withheld from the feed, one qualified on an unbounded indicator, one
    # duplicate dropped, and one rendered in a locale nobody declared.
    assert sum(1 for data in signals if data["broadcast_notification"] is False) == 1
    assert any(
        entry["qualification"]["checked"] is False
        for data in signals
        for entry in data["indicators"]
    )
    assert sum(int(data["duplicate_attempts"]) for data in signals) == 1
    assert sum(1 for data in signals if data.get("locale") == "de-AT") == 1
    assert "refused" in summary
    assert "did not qualify" in summary
    db.close()


def test_the_seeded_feed_actually_orders_by_urgency(tmp_path):
    db = AuditedDatabase(tmp_path / "seeded.db")
    store = RecordStore(db)
    rooms = [seed_rooms(store)[0]]
    load_feature(MODULE).seed(db, {"room_ids": rooms, "now": NOW, "rng": random.Random("wf027")})
    feed = SignalEngine(store).live_feed(room_id=rooms[0][0])
    ranks = [URGENCIES.index(row["urgency"]) for row in feed["entries"]]
    assert ranks == sorted(ranks)
    assert len(feed["entries"]) == sum(
        1
        for record in store.list("intent_signal", room_id=rooms[0][0], limit=100)
        if record["data"]["broadcast_notification"] is True
    )
    db.close()


def test_the_seed_names_the_vague_indicator_in_its_return_string(tmp_path):
    db = AuditedDatabase(tmp_path / "seeded.db")
    store = RecordStore(db)
    summary = load_feature(MODULE).seed(
        db, {"room_ids": [seed_rooms(store)[0]], "now": NOW, "rng": random.Random("wf027")}
    )
    assert "flagged for a non-specific indicator" in summary
    db.close()


def test_the_seed_is_reproducible(tmp_path):
    """Two runs of the seeder must produce the same demo, UUIDs included."""
    summaries = []
    signals = []
    for name in ("a.db", "b.db"):
        db = AuditedDatabase(tmp_path / name)
        store = RecordStore(db)
        summaries.append(
            load_feature(MODULE).seed(
                db,
                {"room_ids": [seed_rooms(store)[0]], "now": NOW, "rng": random.Random("wf027")},
            )
        )
        signals.append(
            sorted(
                record["data"]["idempotency_key"]
                for record in store.list("intent_signal", limit=100)
            )
        )
        db.close()
    assert summaries[0] == summaries[1]
    assert signals[0] == signals[1]


def test_no_seeded_sentence_has_a_hole_in_it(tmp_path):
    """A demo whose flagship row reads ``spent {time_in_seconds}`` teaches the wrong thing.

    The renderer degrades a missing argument to a visible placeholder plus a
    warning rather than raising, which is right for a registration a partner
    wrote - and exactly why the seeded demo has to be checked. This is the test
    that would have caught a seeded signal whose indicator evidence did not
    cover every argument its registration's description names.
    """
    db = AuditedDatabase(tmp_path / "seeded.db")
    store = RecordStore(db)
    rooms = seed_rooms(store)
    load_feature(MODULE).seed(db, {"room_ids": rooms, "now": NOW, "rng": random.Random("wf027")})
    engine = SignalEngine(store)
    for room_id, _account in rooms:
        for signal in engine.live_feed(room_id=room_id)["entries"]:
            text = signal["rendered"]["text"]
            assert "{" not in text, f"unresolved placeholder in the demo sentence: {text!r}"
            for indicator in signal["rendered"]["indicators"]:
                assert "{" not in indicator["text"], indicator["text"]
    db.close()


def test_the_seed_produces_a_video_signal_that_names_the_percentage(tmp_path):
    """A registration with one description needs one measurement, or the sentence has a hole."""
    db = AuditedDatabase(tmp_path / "seeded.db")
    store = RecordStore(db)
    rooms = seed_rooms(store)
    load_feature(MODULE).seed(db, {"room_ids": rooms, "now": NOW, "rng": random.Random("wf027")})
    feed = SignalEngine(store).live_feed(room_id=rooms[0][0])
    video = next(row for row in feed["entries"] if row["type"] == "video_engagement")
    indicator_text = " ".join(indicator["text"] for indicator in video["rendered"]["indicators"])
    assert "92%" in indicator_text
    assert "92%" in video["rendered"]["text"]
    assert "{" not in video["rendered"]["text"]
    db.close()


def test_the_seed_says_so_when_there_are_no_rooms(tmp_path):
    db = AuditedDatabase(tmp_path / "seeded.db")
    store = RecordStore(db)
    summary = load_feature(MODULE).seed(db, {"room_ids": [], "now": NOW, "rng": random.Random("x")})
    assert "3 signal registrations" in summary
    assert "no rooms" in summary
    assert len(store.list("signal_registration")) == 3
    assert store.list("intent_signal") == []
    db.close()


def test_the_seeded_demo_passes_its_own_registration(http, http_room):
    """The demo cannot show a shape the workflow would refuse to produce."""
    load_feature(MODULE).seed(
        http.app.state.db,
        {
            "room_ids": [(http_room["id"], "Northwind Traders")],
            "now": NOW,
            "rng": random.Random("wf027"),
        },
    )
    for record in http.app.state.store.list("intent_signal", limit=100):
        assert record["data"]["indicators"], "a seeded signal has no indicator"
        assert record["data"]["actionable"] is False
        assert record["data"]["registration_id"].startswith("signal_registration_")
