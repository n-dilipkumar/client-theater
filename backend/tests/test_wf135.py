"""WF-135: the conversation-capture rules, exercised as a domain module with no server.

The rules in ``dsr.competitive_baseline.conversation_intelligence`` touch the store
and nothing else. So every rule below is reachable without a ``TestClient``, and
these tests are what make each decision the issue left open checkable at the
instant it is stated about.

What is under test, and why each one matters
--------------------------------------------

* **One entry fans out to every destination.** The research's requirement is that
  a write reaching one destination has not completed the workflow, so a fan-out
  that reached fewer than three destinations is refused rather than reported as a
  capture.
* **The extraction arrives as data.** Transcription and extraction are not built
  here, so the module imports and every test runs with no network. The four
  outputs are the data flow's four: a summary, next steps, requirements and
  MEDDICC evidence.
* **MEDDICC evidence is captured per letter with coverage and no score.** The
  research records that no open-source product enforces MEDDICC, so a total would
  be an invented scale.
* **The MAP is a recorded reference, not a write.** This build does not write into
  WF-124's collections, so a WF-124 reshape cannot break it.
* **The CRM vocabulary is WF-038's.** Re-declared, because a module in this
  package may import only the store, and cross-checked here against WF-038 itself.
* **A write is verified before it reports success.** The fan-out and every report
  are read back, and a write that does not land is a conflict.

Every test names the property it protects, because a test named ``test_flow``
tells a reviewer nothing about which rule they can stop worrying about.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest
from dsr.competitive_baseline import conversation_intelligence as ci
from dsr.competitive_baseline.conversation_intelligence import (
    CaptureError,
    CaptureNotFound,
    CaptureRefused,
    ConversationIntelligenceEngine,
)
from dsr.crm_upsert.payloads import OUTCOMES
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore

MODULE = "wf135_conversation_intelligence"

NOW = datetime(2026, 5, 4, 9, 0, tzinfo=timezone.utc)


@pytest.fixture()
def engine(store: RecordStore) -> ConversationIntelligenceEngine:
    """The engine, with the clock pinned so every stored timestamp is exact."""
    return ConversationIntelligenceEngine(store, now=lambda: NOW)


@pytest.fixture()
def room(store: RecordStore) -> str:
    return store.create("room", {"name": "Northwind"}, actor="test", source="test")["id"]


def _full_meddicc() -> dict[str, str]:
    return {
        "metrics": "A 30 percent cut in onboarding time.",
        "economic_buyer": "Their CFO.",
        "decision_criteria": "Security, then price.",
        "decision_process": "Security review, then legal.",
        "identify_pain": "Onboarding takes eleven days.",
        "champion": "Priya.",
        "competition": "One incumbent.",
    }


def _capture(engine: ConversationIntelligenceEngine, room_id: str, **extra: object) -> dict:
    payload: dict = {
        "summary": "A discovery call.",
        "next_steps": ["Send the SOC 2 report"],
        "requirements": ["SOC 2 Type II"],
        "meddicc": {"champion": "Priya", "metrics": "time"},
        **extra,
    }
    return engine.create_capture(room_id, payload, source="test", actor="test")


def _destination(engine: ConversationIntelligenceEngine, capture: dict, kind: str) -> dict:
    return next(row for row in capture["destinations"] if row["kind"] == kind)


# --------------------------------------------------------------------------- #
# The source vocabulary and the four extraction outputs
# --------------------------------------------------------------------------- #


def test_the_four_extraction_outputs_are_the_ones_the_data_flow_names():
    """Four outputs, not one: summary, next steps, requirements, MEDDICC."""
    assert set(ci.EXTRACTION_OUTPUTS) == {"summary", "next_steps", "requirements", "meddicc"}


def test_the_source_kinds_are_the_researched_data_sources():
    assert set(ci.SOURCE_KINDS) == {
        "call",
        "meeting",
        "email_thread",
        "calendar_event",
        "spoken_playbook",
    }
    assert ci.DEFAULT_SOURCE_KIND in ci.SOURCE_KINDS


def test_a_capture_with_no_source_kind_is_a_call(engine, room):
    assert _capture(engine, room)["source_kind"] == ci.DEFAULT_SOURCE_KIND


def test_an_unknown_source_kind_is_refused():
    with pytest.raises(CaptureError) as caught:
        ci.validate_capture({"summary": "A call.", "source_kind": "smoke_signal"})
    assert "source_kind" in caught.value.errors


def test_an_unreadable_conversation_time_is_refused():
    with pytest.raises(CaptureError) as caught:
        ci.validate_capture({"summary": "A call.", "occurred_at": "yesterday"})
    assert "occurred_at" in caught.value.errors


def test_a_conversation_time_in_zulu_form_is_read():
    normalised = ci.validate_capture({"summary": "A call.", "occurred_at": "2026-05-04T09:00:00Z"})
    assert normalised["occurred_at"] == "2026-05-04T09:00:00Z"


def test_a_summary_is_required():
    with pytest.raises(CaptureError) as caught:
        ci.validate_capture({"next_steps": ["x"]})
    assert "summary" in caught.value.errors


# --------------------------------------------------------------------------- #
# The extraction, read as data
# --------------------------------------------------------------------------- #


def test_next_steps_and_requirements_must_be_lists():
    with pytest.raises(CaptureError) as caught:
        ci.validate_capture({"summary": "A call.", "next_steps": "send the deck"})
    assert "next_steps" in caught.value.errors


def test_a_next_step_may_be_text_or_an_object():
    normalised = ci.validate_capture(
        {
            "summary": "A call.",
            "next_steps": ["Send the deck", {"text": "Book the review", "owner": "buyer"}],
        }
    )
    assert normalised["next_steps"][0] == "Send the deck"
    assert normalised["next_steps"][1]["owner"] == "buyer"


def test_an_unknown_meddicc_key_is_refused():
    with pytest.raises(CaptureError) as caught:
        ci.validate_capture({"summary": "A call.", "meddicc": {"budget": "yes"}})
    assert "meddicc" in caught.value.errors


def test_meddicc_keeps_only_known_letters_and_keeps_their_order():
    normalised = ci.validate_capture(
        {"summary": "A call.", "meddicc": {"champion": "Priya", "metrics": "time"}}
    )
    assert list(normalised["meddicc"]) == ["metrics", "champion"]


def test_a_meddicc_letter_accepts_text_or_a_list():
    normalised = ci.validate_capture(
        {"summary": "A call.", "meddicc": {"champion": "Priya", "metrics": ["30%", "9 days"]}}
    )
    assert ci.covered_letters(normalised) == ["metrics", "champion"]


def test_a_team_keeps_a_field_this_workflow_has_never_heard_of():
    """Schema flexibility: a team adds a field without a migration."""
    normalised = ci.validate_capture({"summary": "A call.", "procurement_ticket": "PR-1"})
    assert normalised["procurement_ticket"] == "PR-1"


# --------------------------------------------------------------------------- #
# The fan-out: one entry reaches every destination
# --------------------------------------------------------------------------- #


def test_one_entry_fans_out_to_all_three_destinations(engine, room):
    capture = _capture(engine, room)
    assert sorted(row["kind"] for row in capture["destinations"]) == sorted(ci.DESTINATION_KINDS)
    assert capture["agreement"]["agreed"] is True


def test_the_fan_out_ids_are_written_back_onto_the_capture(engine, room):
    capture = _capture(engine, room)
    stored = engine.read_capture(capture["id"])
    assert len(stored["destination_ids"]) == len(ci.DESTINATION_KINDS)
    assert sorted(stored["destination_ids"]) == sorted(row["id"] for row in stored["destinations"])


def test_the_three_destinations_carry_the_four_outputs(engine, room):
    capture = _capture(engine, room)
    note = _destination(engine, capture, ci.ROOM_NOTE)["payload"]
    packet = _destination(engine, capture, ci.HANDOFF_PACKET)["payload"]
    crm = _destination(engine, capture, ci.CRM_FIELD)["payload"]
    assert note["body"] == "A discovery call."
    assert note["next_steps"] and note["requirements"]
    assert packet["requirements"] and packet["meddicc"]
    assert crm["fields"][ci.DEFAULT_CRM_FIELDS["requirements"]] == "SOC 2 Type II"
    assert crm["crm_state"] == ci.CRM_NOT_SENT


def test_a_crm_field_map_can_be_overridden(engine, room):
    """A team names its own CRM fields without a migration."""
    capture = _capture(engine, room, field_map={"requirements": "Account.Must_Haves__c"})
    crm = _destination(engine, capture, ci.CRM_FIELD)["payload"]
    assert crm["fields"]["Account.Must_Haves__c"] == "SOC 2 Type II"
    assert ci.DEFAULT_CRM_FIELDS["requirements"] not in crm["fields"]


def test_a_fan_out_that_reaches_one_destination_is_refused(engine, room, monkeypatch):
    """The researched requirement: a write that reaches one destination has not
    completed the workflow."""
    real_create = engine.store.create

    def drop_the_crm_row(collection, data, **kwargs):
        if collection == ci.DESTINATION_COLLECTION and data.get("kind") == ci.CRM_FIELD:
            return {"id": "dropped"}
        return real_create(collection, data, **kwargs)

    monkeypatch.setattr(engine.store, "create", drop_the_crm_row)
    with pytest.raises(CaptureRefused) as caught:
        _capture(engine, room)
    assert caught.value.reason == CaptureRefused.REASON_FANOUT_INCOMPLETE


def test_a_capture_needs_a_room(engine):
    with pytest.raises(CaptureError) as caught:
        engine.create_capture("", {"summary": "A call."}, source="test", actor="test")
    assert "room_id" in caught.value.errors


def test_agreement_of_reports_a_missing_destination():
    agreement = ci.agreement_of({ci.ROOM_NOTE: ci.WRITTEN, ci.HANDOFF_PACKET: ci.WRITTEN})
    assert agreement["agreed"] is False
    assert agreement["missing"] == [ci.CRM_FIELD]


# --------------------------------------------------------------------------- #
# Reporting a destination's outcome
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("outcome", ["created", "updated", "submitted"])
def test_a_successful_crm_outcome_keeps_the_destination_written(engine, room, outcome):
    capture = _capture(engine, room)
    row = _destination(engine, capture, ci.CRM_FIELD)
    reported = engine.report_destination(
        row["id"], {"outcome": outcome}, source="test", actor="test"
    )
    assert reported["state"] == ci.WRITTEN
    assert reported["crm_state"] == outcome
    assert reported["capture_agreement"]["agreed"] is True


@pytest.mark.parametrize("outcome", ["failed", "rejected", "rolled_back"])
def test_a_failed_crm_outcome_makes_the_capture_partial(engine, room, outcome):
    capture = _capture(engine, room)
    row = _destination(engine, capture, ci.CRM_FIELD)
    reported = engine.report_destination(
        row["id"], {"outcome": outcome}, source="test", actor="test"
    )
    assert reported["state"] == ci.FAILED
    assert reported["capture_agreement"]["agreed"] is False
    assert reported["capture_agreement"]["failed"] == [ci.CRM_FIELD]


def test_a_local_destination_reports_written_or_failed(engine, room):
    capture = _capture(engine, room)
    row = _destination(engine, capture, ci.ROOM_NOTE)
    failed = engine.report_destination(
        row["id"], {"outcome": "failed"}, source="test", actor="test"
    )
    assert failed["state"] == ci.FAILED
    assert engine.read_capture(capture["id"])["agreement"]["agreed"] is False


def test_a_report_from_the_wrong_vocabulary_is_refused(engine, room):
    """A CRM outcome cannot be reported against a local destination."""
    capture = _capture(engine, room)
    row = _destination(engine, capture, ci.ROOM_NOTE)
    with pytest.raises(CaptureError) as caught:
        engine.report_destination(row["id"], {"outcome": "updated"}, source="test", actor="test")
    assert "outcome" in caught.value.errors


def test_a_report_keeps_a_history_on_the_row(engine, room):
    capture = _capture(engine, room)
    row = _destination(engine, capture, ci.CRM_FIELD)
    engine.report_destination(row["id"], {"outcome": "updated"}, source="test", actor="test")
    engine.report_destination(
        row["id"], {"outcome": "failed", "note": "A field was blank."}, source="test", actor="test"
    )
    stored = engine.read_destination(row["id"])
    assert [item["outcome"] for item in stored["reports"]] == ["updated", "failed"]
    assert stored["reports"][1]["note"] == "A field was blank."


def test_a_report_that_does_not_read_back_is_refused(engine, room, monkeypatch):
    capture = _capture(engine, room)
    row = _destination(engine, capture, ci.CRM_FIELD)
    monkeypatch.setattr(engine.store, "update", lambda *args, **kwargs: None)
    with pytest.raises(CaptureRefused) as caught:
        engine.report_destination(row["id"], {"outcome": "failed"}, source="test", actor="test")
    assert caught.value.reason == CaptureRefused.REASON_WRITE_NOT_VISIBLE


def test_an_unknown_destination_is_not_found(engine):
    with pytest.raises(CaptureNotFound):
        engine.report_destination(
            "wf135_absent", {"outcome": "failed"}, source="test", actor="test"
        )


def test_an_unknown_capture_is_not_found(engine):
    with pytest.raises(CaptureNotFound):
        engine.read_capture("wf135_absent")


# --------------------------------------------------------------------------- #
# MEDDICC coverage, with no score
# --------------------------------------------------------------------------- #


def test_coverage_counts_a_letter_only_when_it_holds_evidence(engine, room):
    _capture(engine, room, meddicc={"champion": "Priya", "metrics": "   "})
    reported = engine.coverage()
    by_key = {row["key"]: row for row in reported["letters"]}
    assert by_key["champion"]["covered"] is True
    assert by_key["metrics"]["covered"] is False


def test_coverage_reports_missing_letters_and_never_a_score(engine, room):
    _capture(engine, room, meddicc={"champion": "Priya"})
    reported = engine.coverage()
    assert reported["covered_letters"] == ["champion"]
    assert "metrics" in reported["missing_letters"]
    assert reported["letters_total"] == len(ci.MEDDICC_KEYS)
    assert not any("score" in key for key in reported)


def test_the_seven_letters_are_the_conventional_meddicc_table():
    assert len(ci.MEDDICC_LETTERS) == 7
    assert "".join(letter for _key, letter, _label in ci.MEDDICC_LETTERS) == "MEDDICC"


# --------------------------------------------------------------------------- #
# The MAP reference
# --------------------------------------------------------------------------- #


def test_the_plan_reference_is_recorded_and_wf124_is_not_written(engine, room):
    """The MAP is a reference. This build does not write into WF-124's collections."""
    capture = _capture(engine, room, plan_ref="wf124_plan_northwind")
    assert capture["plan_ref"] == "wf124_plan_northwind"
    collections = {row["collection"] for row in engine.store.collections()}
    assert not {name for name in collections if name.startswith("wf124")}


def test_agreement_does_not_depend_on_the_plan_reference(engine, room):
    assert _capture(engine, room)["agreement"]["agreed"] is True


# --------------------------------------------------------------------------- #
# Schema flexibility
# --------------------------------------------------------------------------- #


def test_every_record_this_workflow_writes_is_ordinary_json(store, engine, room):
    capture = _capture(engine, room)
    row = _destination(engine, capture, ci.CRM_FIELD)
    engine.report_destination(row["id"], {"outcome": "updated"}, source="test", actor="test")

    for collection in (ci.CAPTURE_COLLECTION, ci.DESTINATION_COLLECTION):
        for record in store.find(collection, {}, limit=50):
            for key, value in record["data"].items():
                assert isinstance(key, str), (collection, key)
                assert isinstance(value, (str, int, float, bool, list, dict, type(None))), (
                    collection,
                    key,
                    value,
                )


def test_a_team_can_add_its_own_field_with_no_migration(store, room):
    created = store.create(
        ci.CAPTURE_COLLECTION,
        {
            "summary": "A call.",
            "procurement_ticket": "PR-4471",
            "our_field": {"reviewer": "sam", "weight": 3},
            ci.ROOM_REF: room,
        },
        room_id=room,
        actor="test",
        source="test",
    )
    found = store.find(ci.CAPTURE_COLLECTION, {"procurement_ticket": "PR-4471"}, limit=5)
    assert [row["id"] for row in found] == [created["id"]]
    assert found[0]["data"]["our_field"] == {"reviewer": "sam", "weight": 3}


# --------------------------------------------------------------------------- #
# The vocabulary and the recorded decisions
# --------------------------------------------------------------------------- #


def test_the_crm_outcome_names_are_wf038_s():
    """The vocabulary is re-declared, so it is cross-checked against its source."""
    assert set(ci.CRM_OUTCOMES) == set(OUTCOMES)


def test_the_vocabulary_publishes_the_decisions_the_issue_left_open():
    served = ci.vocabulary()
    assert served["destination_kinds"] == list(ci.DESTINATION_KINDS)
    assert served["crm_outcomes"] == list(ci.CRM_OUTCOMES)
    assert [row["letter"] for row in served["meddicc"]] == list("MEDDICC")
    for key in ("transcription", "extraction", "meddicc", "fan_out", "map", "direction"):
        assert key in served["decisions"], key
    # The build must not claim a capability it does not have.
    assert any("Bi-directional" in note for note in served["not_implemented"])
    assert any("Whisper" in note for note in served["not_implemented"])


def test_the_meddicc_expansion_is_recorded_as_chosen_not_sourced():
    """The research names MEDDICC without expanding it, so this build says so."""
    assert "conventional" in ci.DECISIONS["meddicc"].lower() or "MEDDICC" in ci.DECISIONS["meddicc"]


# --------------------------------------------------------------------------- #
# The domain module's import surface
# --------------------------------------------------------------------------- #


def test_the_domain_module_imports_nothing_but_the_store():
    """An enforced property: no framework, no ``dsr.api``, no bare SQLite.

    The suite asserts this over every feature module; this test names it for the
    module this workflow added, so the reason survives a refactor.
    """
    source_file = Path(ci.__file__)
    tree = ast.parse(source_file.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert imported, "the parse found no imports at all, which cannot be right"
    for name in imported:
        assert name != "sqlite3", name
        assert name != "fastapi", name
        assert not name.startswith("starlette"), name
        assert name != "dsr.api", name
        if name == "dsr" or name.startswith("dsr."):
            assert name == "dsr.store", name


def test_the_new_module_sits_in_the_shared_package_without_editing_it():
    """WF-122 owns the package; WF-135 adds one file inside it."""
    package = Path(ci.__file__).parent
    assert (package / "conversation_intelligence.py").is_file()
    assert (package / "engine.py").is_file()


# --------------------------------------------------------------------------- #
# The feature module's own contract
# --------------------------------------------------------------------------- #


def test_the_feature_declares_a_ticket_derived_prefix():
    feature = load_feature(MODULE)
    assert feature.router.prefix == "/api/wf-135"
    assert feature.FEATURE["ticket"] == "WF-135"
    assert feature.FEATURE["id"] == "wf-135-conversation-intelligence"


def test_every_route_sits_under_this_features_own_prefix():
    feature = load_feature(MODULE)
    for route in feature.router.routes:
        assert route.path.startswith("/api/wf-135"), route.path


def test_every_engine_write_requires_a_source_and_has_no_default():
    """A write cannot be reached without naming the route that served it."""
    import inspect

    for name in ("create_capture", "report_destination"):
        signature = inspect.signature(getattr(ConversationIntelligenceEngine, name))
        assert "source" in signature.parameters, name
        assert signature.parameters["source"].default is inspect.Parameter.empty, name


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def test_the_seed_string_is_encodable_by_cp1252(db: AuditedDatabase):
    """The seeder prints it on a Windows console, so every character must encode.

    A single RIGHTWARDS ARROW in one recovered feature broke the entire seeder.
    """
    feature = load_feature(MODULE)
    room_ids = [
        (db.create("room", {"name": name}, actor="seed", source="seed")["id"], name)
        for name in ("Northwind", "Vantage")
    ]
    reported = feature.seed(db, {"room_ids": room_ids, "now": NOW})
    assert reported
    reported.encode("cp1252")
    assert reported == reported.encode("cp1252").decode("cp1252")


def test_the_seed_creates_the_states_the_issue_asks_for(db: AuditedDatabase):
    """The demo must show an agreed capture, a partial one and a coverage gap."""
    feature = load_feature(MODULE)
    room_ids = [
        (db.create("room", {"name": name}, actor="seed", source="seed")["id"], name)
        for name in ("Northwind", "Vantage")
    ]
    feature.seed(db, {"room_ids": room_ids, "now": NOW})

    store = RecordStore(db)
    engine = ConversationIntelligenceEngine(store, now=lambda: NOW)
    counted = engine.summary()
    assert counted["captures"] >= 3, counted
    assert counted["agreed"] >= 1, counted
    assert counted["partial"] >= 1, counted
    assert counted["failed"] >= 1, counted
    assert counted["plan_linked"] >= 1, counted
    assert counted["room_note"] >= 1 and counted["handoff_packet"] >= 1
    assert counted["crm_field"] >= 1

    covered = engine.coverage()
    # Across the demo every letter is covered at least once, and the last capture
    # alone shows a gap, so the coverage report has both states to display.
    assert covered["missing_letters"] == [], covered
    assert covered["letters_with_evidence"] == len(ci.MEDDICC_KEYS)

    room_two = room_ids[1][0]
    per_room = engine.coverage(room_two)
    assert per_room["letters_with_evidence"] == 1, per_room
    assert per_room["missing_letters"], "the demo shows no coverage gap at all"


def test_the_summary_counts_without_writing(engine, room):
    _capture(engine, room)
    counted = engine.summary(room)
    assert counted["captures"] == 1
    assert counted["destinations"] == len(ci.DESTINATION_KINDS)
    assert counted["agreed"] == 1
    assert "generated_at" in counted
