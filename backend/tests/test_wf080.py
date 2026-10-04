"""WF-080: download the executed agreement from the e-vault, webhook-driven.

The researched specification is ``docs/research/digital-sales-room-workflows/wf/WF-080.md``,
quoted in full in issue 128. These tests are organised by the decision they defend,
because the point of this workflow is that the decisions were researched rather than
chosen - so a rule with no test is a rule that will be quietly dropped by the next person
to touch it.

The sections, and the researched rule each pins:

``the ready trigger``
    "Integrator creates a webhook subscription for ``document_completed_pdf_ready``", and
    "subscribe to the *ready* event rather than polling status". A subscription that cannot
    hear the ready event is refused, because nothing else in this package would tell the
    room the PDF exists.
``dedupe on the delivery id``
    The ``X-PandaDoc-Webhook-Event-Id`` header exists "to process each webhook notification
    once... even when PandaDoc retries delivery". A document id is never the key, and a
    notification with no id is refused rather than stored undeduplicated.
``the payload shape``
    "The handler extracts the document ``id`` from the payload", with no envelope quoted.
    Four shapes are read in a fixed order, and the order is the part that matters.
``202 is an outcome``
    "The signed document file is not ready yet... Retry after the indicated number of
    seconds. No response body is returned." A first-class stored state, a header, and no
    body.
``not completed is not back pressure``
    Back-pressure means work in progress. A document awaiting signatures is refused with
    its state, never answered with a wait.
``production keys only``
    "Production key only... You'll get a 401 Unauthorized error when trying to use a
    Sandbox key", and "sandbox-based integration tests must use the plain download
    endpoint".
``the two variants``
    "the ``/download-protected`` endpoint always returns the same digitally sealed PDF
    file, while ``/download`` allows for watermark customization". Byte-stability is
    asserted on the bytes, not claimed in prose.
``the throttle``
    "429 -> ``throttled``" and "Do not surface it as a generic failure". Named, bounded, and
    escapable.
``the domain imports nothing but the store``
    The architectural guard the brief names by name.
``the seed return string``
    Every character encodable by cp1252, and the states the seeder claims actually exist.

The HTTP surface is in ``test_wf080_http.py``.
"""

from __future__ import annotations

import ast
import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.security_governance import (
    evault_inferences,
    evault_rules as rules,
    evault_vocabulary as vocab,
)
from dsr.security_governance.evault_engine import EvaultEngine
from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

NOW = datetime(2026, 10, 4, 9, 0, 0, tzinfo=timezone.utc)

FEATURE_MODULE = "dsr.features.wf080_download_the_executed_agreement_from_the_e"
DOMAIN_PACKAGE = "dsr.security_governance"
DOMAIN_PREFIX = "evault_"


class Clock:
    """A clock the test moves by hand.

    The throttle window is measured in seconds, so a test that cannot choose the instant
    cannot test the boundary, and the boundary is the rule most likely to be wrong. Every
    stamp this workflow writes comes from here.
    """

    def __init__(self, start: datetime = NOW) -> None:
        self.at = start

    def __call__(self) -> datetime:
        return self.at

    def advance(self, **kwargs: float) -> datetime:
        self.at = self.at + timedelta(**kwargs)
        return self.at


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def engine(clock: Clock, store: RecordStore) -> EvaultEngine:
    store.create(
        "room", {"name": "Northwind data room"}, record_id="room_a", actor="dana", source="fixture"
    )
    store.create(
        "room", {"name": "Contoso data room"}, record_id="room_b", actor="dana", source="fixture"
    )
    return EvaultEngine(store, now=clock)


def make_subscription(engine: EvaultEngine, room_id: str = "room_a", **fields) -> dict:
    payload = {"triggers": [vocab.PDF_READY_TRIGGER]}
    payload.update(fields)
    return engine.create_subscription(room_id, payload, source="fixture", actor="dana")


def make_document(
    engine: EvaultEngine,
    room_id: str = "room_a",
    vendor_id: str = "pd_doc_1",
    state: str | None = None,
    **fields,
) -> dict:
    payload: dict = {"vendor_document_id": vendor_id, "subject": f"Agreement {vendor_id}"}
    if state is not None:
        payload["state"] = state
    payload.update(fields)
    return engine.register_document(room_id, payload, source="fixture", actor="dana")


def make_ready(
    engine: EvaultEngine, document: dict, vendor_id: str = "pd_doc_1", **headers
) -> dict:
    """Fire the ready event once, through the real dedupe path."""
    return engine.receive_event(
        document["room_id"],
        headers={vocab.DEDUPE_HEADER: f"evt_{vendor_id}", **headers},
        payload={"event": vocab.PDF_READY_TRIGGER, "data": {"id": vendor_id}},
        source="fixture",
        actor="vendor",
    )


def make_sealed(engine: EvaultEngine, document: dict, room_id: str = "room_a", **kwargs) -> dict:
    """A document whose PDF is in the vault and has been fetched once."""
    document_id = document["id"]
    engine.set_state(document_id, vocab.STATE_SEALED, source="fixture", actor="vendor")
    result = engine.retrieve(
        document_id, environment=vocab.ENVIRONMENT_PRODUCTION, source="fixture"
    )
    assert result["outcome"] == vocab.OUTCOME_RETRIEVED
    return result


# --------------------------------------------------------------------------- #
# the ready trigger
# --------------------------------------------------------------------------- #


class TestTheReadyTrigger:
    def test_the_trigger_is_the_name_the_specification_quotes(self):
        assert vocab.PDF_READY_TRIGGER == "document_completed_pdf_ready"

    def test_it_is_the_only_trigger_this_workflow_requires(self):
        assert vocab.REQUIRED_TRIGGERS == (vocab.PDF_READY_TRIGGER,)

    def test_a_subscription_without_it_is_refused(self, engine: EvaultEngine):
        with pytest.raises(rules.SubscriptionInvalid) as caught:
            engine.create_subscription(
                "room_a", {"triggers": ["document_sent"]}, source="fixture", actor="dana"
            )
        assert vocab.PDF_READY_TRIGGER in str(caught.value.errors["triggers"])

    def test_a_refused_subscription_writes_nothing(self, engine: EvaultEngine, store: RecordStore):
        before = store.db.count(vocab.SUBSCRIPTION_COLLECTION)
        with pytest.raises(rules.SubscriptionInvalid):
            engine.create_subscription(
                "room_a", {"triggers": ["document_sent"]}, source="fixture", actor="dana"
            )
        assert store.db.count(vocab.SUBSCRIPTION_COLLECTION) == before

    def test_another_documented_trigger_may_travel_alongside(self, engine: EvaultEngine):
        row = make_subscription(engine, triggers=[vocab.PDF_READY_TRIGGER, "document_completed"])
        assert row["hears_ready_event"] is True
        assert row["triggers"] == [vocab.PDF_READY_TRIGGER, "document_completed"]

    def test_no_triggers_at_all_is_refused(self, engine: EvaultEngine):
        with pytest.raises(rules.SubscriptionInvalid):
            engine.create_subscription("room_a", {}, source="fixture", actor="dana")

    def test_a_bare_string_is_not_a_trigger_list(self, engine: EvaultEngine):
        with pytest.raises(rules.SubscriptionInvalid):
            make_subscription(engine, triggers=vocab.PDF_READY_TRIGGER)

    def test_an_empty_trigger_name_is_refused_rather_than_dropped(self, engine: EvaultEngine):
        with pytest.raises(rules.SubscriptionInvalid):
            make_subscription(engine, triggers=[vocab.PDF_READY_TRIGGER, "  "])

    def test_duplicates_collapse_to_one(self, engine: EvaultEngine):
        row = make_subscription(engine, triggers=[vocab.PDF_READY_TRIGGER, vocab.PDF_READY_TRIGGER])
        assert row["triggers"] == [vocab.PDF_READY_TRIGGER]

    def test_the_response_states_that_this_room_does_not_poll(self, engine: EvaultEngine):
        row = make_subscription(engine)
        assert row[vocab.NO_POLLING_FIELD] == vocab.NO_POLLING
        assert "does not poll" in vocab.NO_POLLING


# --------------------------------------------------------------------------- #
# dedupe on the delivery id
# --------------------------------------------------------------------------- #


class TestDedupe:
    def test_the_header_name_is_the_one_the_specification_quotes(self):
        assert vocab.DEDUPE_HEADER == "X-PandaDoc-Webhook-Event-Id"

    def test_the_header_is_read_case_insensitively(self):
        assert rules.delivery_id_of({"x-pandadoc-webhook-event-id": "evt_1"}, {}) == (
            "evt_1",
            vocab.DELIVERY_FROM_HEADER,
        )

    def test_the_header_wins_over_the_payload(self):
        found = rules.delivery_id_of({vocab.DEDUPE_HEADER: "evt_h"}, {"eventId": "evt_p"})
        assert found == ("evt_h", vocab.DELIVERY_FROM_HEADER)

    def test_a_payload_id_is_a_fallback_and_says_so(self):
        assert rules.delivery_id_of({}, {"eventId": "evt_p"}) == (
            "evt_p",
            vocab.DELIVERY_FROM_PAYLOAD,
        )

    def test_a_notification_with_no_id_is_refused(self):
        with pytest.raises(rules.SubscriptionInvalid) as caught:
            rules.delivery_id_of({}, {"event": "document_completed_pdf_ready"})
        assert vocab.DEDUPE_HEADER in caught.value.errors[vocab.DEDUPE_HEADER]

    def test_a_first_delivery_is_applied_and_a_repeat_is_not(self, engine: EvaultEngine):
        document = make_document(engine)
        first = make_ready(engine, document)
        assert first["outcome"] == vocab.OUTCOME_RETRIEVED
        assert first["deliveries"] == 1

        second = engine.receive_event(
            "room_a",
            headers={vocab.DEDUPE_HEADER: "evt_pd_doc_1"},
            payload={"event": vocab.PDF_READY_TRIGGER, "data": {"id": "pd_doc_1"}},
            source="fixture",
            actor="vendor",
        )
        assert second["outcome"] == vocab.OUTCOME_DUPLICATE
        assert second["deliveries"] == 2

    def test_a_repeat_does_not_move_the_state_again(self, engine: EvaultEngine):
        document = make_document(engine)
        make_ready(engine, document)
        engine.set_state(document["id"], vocab.STATE_SEALED, source="fixture", actor="vendor")
        repeat = engine.receive_event(
            "room_a",
            headers={vocab.DEDUPE_HEADER: "evt_pd_doc_1"},
            payload={"event": vocab.PDF_READY_TRIGGER, "data": {"id": "pd_doc_1"}},
            source="fixture",
            actor="vendor",
        )
        assert repeat["outcome"] == vocab.OUTCOME_DUPLICATE
        assert engine.read_document(document["id"])["state"] == vocab.STATE_SEALED

    def test_the_retry_is_visible_rather_than_collapsed_away(self, engine: EvaultEngine):
        document = make_document(engine)
        make_ready(engine, document)
        engine.receive_event(
            "room_a",
            headers={vocab.DEDUPE_HEADER: "evt_pd_doc_1"},
            payload={"event": vocab.PDF_READY_TRIGGER, "data": {"id": "pd_doc_1"}},
            source="fixture",
            actor="vendor",
        )
        rows = engine.deliveries("room_a")
        assert len(rows) == 1
        assert rows[0]["deliveries"] == 2
        assert rows[0]["retries"] == 1

    def test_two_deliveries_of_one_document_are_two_rows(self, engine: EvaultEngine):
        document = make_document(engine)
        make_ready(engine, document)
        engine.receive_event(
            "room_a",
            headers={vocab.DEDUPE_HEADER: "evt_second"},
            payload={"event": vocab.PDF_READY_TRIGGER, "data": {"id": "pd_doc_1"}},
            source="fixture",
            actor="vendor",
        )
        assert len(engine.deliveries("room_a")) == 2

    def test_a_document_id_is_never_the_dedupe_key(self, engine: EvaultEngine):
        """The whole rule: a document fires several notifications, so keying on the
        document would drop every delivery after the first."""
        document = make_document(engine)
        make_ready(engine, document)
        engine.receive_event(
            "room_a",
            headers={vocab.DEDUPE_HEADER: "evt_a"},
            payload={"event": vocab.PDF_READY_TRIGGER, "data": {"id": "pd_doc_1"}},
            source="fixture",
            actor="vendor",
        )
        engine.receive_event(
            "room_a",
            headers={vocab.DEDUPE_HEADER: "evt_b"},
            payload={"event": vocab.PDF_READY_TRIGGER, "data": {"id": "pd_doc_1"}},
            source="fixture",
            actor="vendor",
        )
        assert len(engine.deliveries("room_a")) == 3

    def test_a_notification_naming_an_unknown_document_is_refused(self, engine: EvaultEngine):
        with pytest.raises(rules.DocumentInvalid):
            engine.receive_event(
                "room_a",
                headers={vocab.DEDUPE_HEADER: "evt_unknown"},
                payload={"data": {"id": "pd_doc_absent"}},
                source="fixture",
                actor="vendor",
            )

    def test_a_refused_notification_writes_no_delivery(
        self, engine: EvaultEngine, store: RecordStore
    ):
        with pytest.raises(rules.DocumentInvalid):
            engine.receive_event(
                "room_a",
                headers={vocab.DEDUPE_HEADER: "evt_unknown"},
                payload={"data": {"id": "pd_doc_absent"}},
                source="fixture",
                actor="vendor",
            )
        assert store.db.count(vocab.DELIVERY_COLLECTION) == 0

    def test_the_same_delivery_id_in_another_room_is_not_deduped_against_this_one(
        self, engine: EvaultEngine
    ):
        """The join has to be unambiguous for the trail to be worth anything."""
        make_document(engine, "room_a", vendor_id="pd_doc_1")
        make_document(engine, "room_b", vendor_id="pd_doc_1")
        make_ready(engine, {"room_id": "room_a"}, "pd_doc_1")
        other = engine.receive_event(
            "room_b",
            headers={vocab.DEDUPE_HEADER: "evt_pd_doc_1"},
            payload={"data": {"id": "pd_doc_1"}},
            source="fixture",
            actor="vendor",
        )
        assert other["outcome"] == vocab.OUTCOME_RETRIEVED


# --------------------------------------------------------------------------- #
# the payload shape
# --------------------------------------------------------------------------- #


class TestThePayloadShape:
    def test_a_nested_data_id_is_read(self):
        assert rules.document_id_of({"data": {"id": "pd_1"}}) == "pd_1"

    def test_a_flat_document_id_is_read(self):
        assert rules.document_id_of({"documentId": "pd_2"}) == "pd_2"

    def test_a_nested_document_object_is_read(self):
        assert rules.document_id_of({"document": {"id": "pd_3"}}) == "pd_3"

    def test_a_bare_id_is_the_last_resort(self):
        assert rules.document_id_of({"id": "pd_4"}) == "pd_4"

    def test_the_nested_document_wins_over_a_bare_notification_id(self):
        """`data.id` is the document. A bare top-level `id` in a webhook envelope is more
        often the notification's own identifier, which is what the dedupe header is for."""
        assert rules.document_id_of({"data": {"id": "pd_doc"}, "id": "evt_9"}) == "pd_doc"

    def test_a_payload_with_no_document_id_reads_empty(self):
        assert rules.document_id_of({"event": "document_completed_pdf_ready"}) == ""

    def test_the_event_name_is_read_from_any_of_the_three_keys(self):
        assert rules.event_name_of({"event": "a"}) == "a"
        assert rules.event_name_of({"eventType": "b"}) == "b"
        assert rules.event_name_of({"event_type": "c"}) == "c"
        assert rules.event_name_of({}) == ""

    def test_the_derivation_is_recorded(self):
        assert "DERIVED_EVENT_PAYLOAD_SHAPE" in evault_inferences.DECISIONS


# --------------------------------------------------------------------------- #
# 202 is an outcome
# --------------------------------------------------------------------------- #


class TestBackPressure:
    def test_a_generating_document_answers_202_with_a_wait(self, engine: EvaultEngine):
        document = make_document(engine)
        make_ready(engine, document)
        result = engine.retrieve(document["id"], source="fixture")
        assert result["outcome"] == vocab.OUTCOME_BACK_PRESSURE
        assert result["status"] == vocab.STATUS_ACCEPTED
        assert result["retry_after"] == vocab.RETRY_AFTER_SECONDS

    def test_the_wait_is_stored_as_an_attempt(self, engine: EvaultEngine):
        document = make_document(engine)
        make_ready(engine, document)
        engine.retrieve(document["id"], source="fixture")
        rows = engine.attempts("room_a", document["id"])
        assert len(rows) == 1
        assert rows[0]["outcome"] == vocab.OUTCOME_BACK_PRESSURE
        assert rows[0]["retry_after_seconds"] == vocab.RETRY_AFTER_SECONDS
        assert rows[0]["vendor_answered"] is True

    def test_the_served_response_carries_the_header_and_no_body(self, engine: EvaultEngine):
        document = make_document(engine)
        make_ready(engine, document)
        shaped = engine.serve_protected(document["id"], room_id="room_a")
        assert shaped["status"] == vocab.STATUS_ACCEPTED
        assert shaped["headers"] == {vocab.RETRY_AFTER_HEADER: str(vocab.RETRY_AFTER_SECONDS)}
        assert shaped["body"] == b""

    def test_a_202_writes_no_artifact(self, engine: EvaultEngine, store: RecordStore):
        document = make_document(engine)
        make_ready(engine, document)
        engine.serve_protected(document["id"], room_id="room_a")
        assert store.db.count(vocab.ARTIFACT_COLLECTION) == 0

    def test_the_wait_comes_from_the_subscription_when_it_is_raised(self, engine: EvaultEngine):
        make_subscription(engine, retry_after_seconds=17)
        document = make_document(engine)
        make_ready(engine, document)
        shaped = engine.serve_protected(document["id"], room_id="room_a")
        assert shaped["headers"][vocab.RETRY_AFTER_HEADER] == "17"

    def test_a_wait_of_zero_is_never_returned(self):
        assert rules.retry_after_seconds({"retry_after_seconds": 0}) == vocab.RETRY_AFTER_SECONDS

    def test_a_wait_below_one_second_is_raised_to_one(self):
        assert rules.retry_after_seconds({"retry_after_seconds": -5}) == 1

    def test_only_generating_is_back_pressure(self):
        assert rules.is_back_pressure(vocab.STATE_GENERATING) is True
        for state in vocab.DOCUMENT_STATES:
            if state == vocab.STATE_GENERATING:
                continue
            assert rules.is_back_pressure(state) is False

    def test_the_derivation_of_the_seconds_is_recorded(self):
        assert "DERIVED_RETRY_AFTER_SECONDS" in evault_inferences.DECISIONS

    def test_the_empty_body_derivation_is_recorded(self):
        assert "DERIVED_EMPTY_BODY_ON_202" in evault_inferences.DECISIONS


# --------------------------------------------------------------------------- #
# not completed is not back pressure
# --------------------------------------------------------------------------- #


class TestNotCompleted:
    def test_an_awaiting_document_is_refused_with_its_state(self, engine: EvaultEngine):
        document = make_document(engine)
        with pytest.raises(rules.NotCompleted) as caught:
            engine.retrieve(document["id"], source="fixture")
        assert caught.value.status == 409
        assert caught.value.state == vocab.STATE_AWAITING_SIGNATURES
        assert caught.value.code == "not_completed_awaiting_signatures"

    def test_a_failed_document_is_refused_too(self, engine: EvaultEngine):
        document = make_document(engine, state=vocab.STATE_FAILED)
        with pytest.raises(rules.NotCompleted):
            engine.retrieve(document["id"], source="fixture")

    def test_the_refusal_writes_no_attempt(self, engine: EvaultEngine, store: RecordStore):
        document = make_document(engine)
        with pytest.raises(rules.NotCompleted):
            engine.retrieve(document["id"], source="fixture")
        assert store.db.count(vocab.ATTEMPT_COLLECTION) == 0

    def test_the_served_route_refuses_the_same_way(self, engine: EvaultEngine):
        document = make_document(engine)
        with pytest.raises(rules.NotCompleted):
            engine.serve_protected(document["id"], room_id="room_a")

    def test_the_refusal_carries_no_wait(self, engine: EvaultEngine):
        """There is nothing in progress, so there is nothing to wait for."""
        document = make_document(engine)
        with pytest.raises(rules.NotCompleted) as caught:
            engine.retrieve(document["id"], source="fixture")
        assert not hasattr(caught.value, "retry_after_seconds")

    def test_the_derivation_is_recorded(self):
        assert "DERIVED_PRE_COMPLETION_REFUSAL" in evault_inferences.DECISIONS


# --------------------------------------------------------------------------- #
# production keys only
# --------------------------------------------------------------------------- #


class TestTheEnvironmentGate:
    def test_the_sealed_endpoint_is_production_only(self):
        assert vocab.SEALED_ENVIRONMENT == vocab.ENVIRONMENT_PRODUCTION

    def test_a_sandbox_key_is_refused_for_the_sealed_variant(self, engine: EvaultEngine):
        document = make_document(engine, state=vocab.STATE_SEALED)
        with pytest.raises(rules.SandboxKeyRejected) as caught:
            engine.retrieve(document["id"], environment=vocab.ENVIRONMENT_SANDBOX, source="fixture")
        assert caught.value.status == 401
        assert caught.value.code == "sandbox_key_rejected"

    def test_the_refusal_names_the_endpoint_that_does_work(self):
        assert vocab.PLAIN_PATH in vocab.SANDBOX_REMEDY

    def test_a_sandbox_key_serves_the_plain_variant(self, engine: EvaultEngine):
        document = make_document(
            engine, state=vocab.STATE_SEALED, environment=vocab.ENVIRONMENT_SANDBOX
        )
        result = engine.retrieve(
            document["id"],
            variant=vocab.VARIANT_PLAIN,
            environment=vocab.ENVIRONMENT_SANDBOX,
            source="fixture",
        )
        assert result["outcome"] == vocab.OUTCOME_RETRIEVED

    def test_the_refusal_writes_no_attempt(self, engine: EvaultEngine, store: RecordStore):
        document = make_document(engine, state=vocab.STATE_SEALED)
        with pytest.raises(rules.SandboxKeyRejected):
            engine.retrieve(document["id"], environment=vocab.ENVIRONMENT_SANDBOX, source="fixture")
        assert store.db.count(vocab.ATTEMPT_COLLECTION) == 0

    def test_the_gate_is_checked_before_the_completion_check(self, engine: EvaultEngine):
        """A sandbox caller asking for a document that has not started is told about the
        key, because the key is the thing they can change."""
        document = make_document(engine, environment=vocab.ENVIRONMENT_SANDBOX)
        with pytest.raises(rules.SandboxKeyRejected):
            engine.retrieve(document["id"], environment=vocab.ENVIRONMENT_SANDBOX, source="fixture")

    def test_an_unrecognised_environment_is_refused_rather_than_defaulted(self):
        with pytest.raises(rules.SubscriptionInvalid):
            rules.coerce_environment("staging")

    def test_the_projection_says_whether_the_key_may_reach_the_sealed_endpoint(
        self, engine: EvaultEngine
    ):
        production = make_document(engine, state=vocab.STATE_SEALED)
        sandbox = make_document(
            engine,
            vendor_id="pd_s",
            state=vocab.STATE_SEALED,
            environment=vocab.ENVIRONMENT_SANDBOX,
        )
        assert engine.read_document(production["id"])["sealed_allowed_here"] is True
        assert engine.read_document(sandbox["id"])["sealed_allowed_here"] is False


# --------------------------------------------------------------------------- #
# the two variants
# --------------------------------------------------------------------------- #


class TestTheTwoVariants:
    def test_the_two_endpoints_are_the_ones_the_guide_names(self):
        assert vocab.SEALED_PATH == "/public/v1/documents/{id}/download-protected"
        assert vocab.PLAIN_PATH == "/public/v1/documents/{id}/download"

    def test_only_the_sealed_variant_is_byte_stable(self):
        assert rules.byte_stable(vocab.VARIANT_SEALED) is True
        assert rules.byte_stable(vocab.VARIANT_PLAIN) is False

    def test_only_the_plain_variant_takes_a_watermark(self):
        assert rules.watermarkable(vocab.VARIANT_PLAIN) is True
        assert rules.watermarkable(vocab.VARIANT_SEALED) is False

    def test_the_builder_is_a_pure_function_of_its_arguments(self):
        first = rules.build_pdf("pd_1", rules.artifact_document("pd_1", vocab.VARIANT_SEALED))
        second = rules.build_pdf("pd_1", rules.artifact_document("pd_1", vocab.VARIANT_SEALED))
        assert first == second

    def test_the_bytes_are_a_pdf_with_a_correct_startxref(self):
        pdf = rules.build_pdf("pd_1", ["one", "two"])
        assert pdf.startswith(b"%PDF-1.4")
        assert pdf.rstrip().endswith(b"%%EOF")
        offset = int(pdf[pdf.index(b"startxref") + 9 :].split()[0])
        assert pdf[offset : offset + 4] == b"xref"

    def test_the_sealed_bytes_do_not_depend_on_the_caller(self, engine: EvaultEngine):
        document = make_document(engine, state=vocab.STATE_SEALED)
        first = make_sealed(engine, document)
        second = engine.retrieve(document["id"], source="fixture")
        assert first["artifact"]["sha256"] == second["artifact"]["sha256"]
        assert first["artifact"]["byte_length"] == second["artifact"]["byte_length"]

    def test_the_sealed_artifact_is_stored_once(self, engine: EvaultEngine):
        document = make_document(engine, state=vocab.STATE_SEALED)
        make_sealed(engine, document)
        engine.retrieve(document["id"], source="fixture")
        assert len(engine.artifacts("room_a", document["id"])) == 1

    def test_a_watermark_sent_to_the_sealed_variant_is_ignored(self, engine: EvaultEngine):
        """The sealed endpoint "always returns the same digitally sealed PDF file"."""
        document = make_document(engine, state=vocab.STATE_SEALED)
        result = engine.retrieve(document["id"], watermark="ACME", source="fixture")
        assert result["artifact"]["watermark"] == ""
        assert result["artifact"]["byte_stable"] is True

    def test_a_watermarked_plain_copy_is_a_different_file(self, engine: EvaultEngine):
        document = make_document(engine, state=vocab.STATE_SEALED)
        sealed = make_sealed(engine, document)
        plain = engine.retrieve(
            document["id"],
            variant=vocab.VARIANT_PLAIN,
            watermark="ACME CONFIDENTIAL",
            source="fixture",
        )
        assert plain["artifact"]["sha256"] != sealed["artifact"]["sha256"]
        assert plain["artifact"]["watermark"] == "ACME CONFIDENTIAL"
        assert plain["artifact"]["byte_stable"] is False

    def test_two_different_watermarks_produce_two_different_files(self, engine: EvaultEngine):
        document = make_document(engine, state=vocab.STATE_SEALED)
        one = engine.retrieve(
            document["id"], variant=vocab.VARIANT_PLAIN, watermark="ONE", source="fixture"
        )
        two = engine.retrieve(
            document["id"], variant=vocab.VARIANT_PLAIN, watermark="TWO", source="fixture"
        )
        assert one["artifact"]["sha256"] != two["artifact"]["sha256"]

    def test_a_watermark_cannot_break_the_pdf_string(self, engine: EvaultEngine):
        """An unescaped `)` would close the PDF string literal and change the file."""
        document = make_document(engine, state=vocab.STATE_SEALED)
        result = engine.retrieve(
            document["id"],
            variant=vocab.VARIANT_PLAIN,
            watermark=r"ACME \) \n /Type /Page",
            source="fixture",
        )
        assert result["outcome"] == vocab.OUTCOME_RETRIEVED
        assert ")" not in result["artifact"]["watermark"]
        assert "\\" not in result["artifact"]["watermark"]

    def test_the_pdf_is_still_well_formed_after_a_hostile_watermark(self, engine: EvaultEngine):
        document = make_document(engine, state=vocab.STATE_SEALED)
        make_sealed(engine, document)
        engine.retrieve(
            document["id"],
            variant=vocab.VARIANT_PLAIN,
            watermark=") endstream endobj",
            source="fixture",
        )
        shaped = engine.serve_protected(document["id"], room_id="room_a")
        assert shaped["status"] == vocab.STATUS_READY
        assert shaped["body"].startswith(b"%PDF-1.4")
        assert shaped["body"].rstrip().endswith(b"%%EOF")

    def test_the_served_bytes_are_the_stored_bytes(self, engine: EvaultEngine):
        document = make_document(engine, state=vocab.STATE_SEALED)
        fetched = make_sealed(engine, document)
        shaped = engine.serve_protected(document["id"], room_id="room_a")
        assert rules.sha256_hex(shaped["body"]) == fetched["artifact"]["sha256"]
        assert shaped["media_type"] == vocab.PDF_MEDIA_TYPE

    def test_the_digest_is_over_the_bytes_not_the_stored_copy(self):
        assert rules.sha256_hex(b"abc") == rules.sha256_hex("abc")

    def test_the_tradeoff_is_stated_once_and_readable(self):
        assert "immutable" in vocab.VARIANT_TRADEOFF
        assert vocab.PLAIN_PATH in vocab.VARIANT_TRADEOFF

    def test_the_describe_helper_reports_the_two_booleans(self):
        sealed = rules.describe_variant(vocab.VARIANT_SEALED)
        plain = rules.describe_variant(vocab.VARIANT_PLAIN)
        assert sealed["byte_stable"] is True and sealed["watermarkable"] is False
        assert plain["byte_stable"] is False and plain["watermarkable"] is True
        assert sealed["environments"] == [vocab.ENVIRONMENT_PRODUCTION]

    def test_the_artifact_bytes_derivation_is_recorded(self):
        assert "DERIVED_ARTIFACT_BYTES" in evault_inferences.DECISIONS

    def test_the_generated_artifact_scope_is_recorded(self):
        assert "DERIVED_GENERATED_ARTIFACT" in evault_inferences.DECISIONS

    def test_no_response_claims_a_signature_this_room_cannot_check(self):
        assert "does not validate a certificate chain" in vocab.SEAL_SCOPE
        assert vocab.EFFECT == "recorded_not_verified"


# --------------------------------------------------------------------------- #
# the throttle
# --------------------------------------------------------------------------- #


class TestTheThrottle:
    def test_the_limit_is_reached_and_refused(self, engine: EvaultEngine):
        document = make_document(engine, state=vocab.STATE_SEALED)
        for _ in range(vocab.THROTTLE_LIMIT):
            engine.retrieve(document["id"], source="fixture")
        with pytest.raises(rules.Throttled) as caught:
            engine.retrieve(document["id"], source="fixture")
        assert caught.value.status == 429
        assert caught.value.code == "throttled"

    def test_the_refusal_carries_a_wait(self, engine: EvaultEngine):
        document = make_document(engine, state=vocab.STATE_SEALED)
        for _ in range(vocab.THROTTLE_LIMIT):
            engine.retrieve(document["id"], source="fixture")
        with pytest.raises(rules.Throttled) as caught:
            engine.retrieve(document["id"], source="fixture")
        assert caught.value.retry_after_seconds >= 1

    def test_a_throttled_attempt_is_not_counted_against_the_window(
        self, engine: EvaultEngine, clock: Clock
    ):
        """A client that keeps retrying must be able to recover."""
        document = make_document(engine, state=vocab.STATE_SEALED)
        for _ in range(vocab.THROTTLE_LIMIT):
            engine.retrieve(document["id"], source="fixture")
        with pytest.raises(rules.Throttled):
            engine.retrieve(document["id"], source="fixture")
        counted = rules.counted_attempts(engine._attempts(document["id"]), clock())
        assert counted == vocab.THROTTLE_LIMIT

    def test_the_window_expires(self, engine: EvaultEngine, clock: Clock):
        document = make_document(engine, state=vocab.STATE_SEALED)
        for _ in range(vocab.THROTTLE_LIMIT):
            engine.retrieve(document["id"], source="fixture")
        with pytest.raises(rules.Throttled):
            engine.retrieve(document["id"], source="fixture")
        clock.advance(seconds=vocab.THROTTLE_WINDOW_SECONDS + 1)
        assert (
            engine.retrieve(document["id"], source="fixture")["outcome"] == vocab.OUTCOME_RETRIEVED
        )

    def test_the_boundary_second_still_counts(self, engine: EvaultEngine, clock: Clock):
        """A window that excluded the boundary would allow one extra call per window."""
        document = make_document(engine, state=vocab.STATE_SEALED)
        engine.retrieve(document["id"], source="fixture")
        clock.advance(seconds=vocab.THROTTLE_WINDOW_SECONDS)
        assert rules.within_window(rules.stamp(clock()), clock(), vocab.THROTTLE_WINDOW_SECONDS)

    def test_only_vendor_answered_attempts_count(self):
        rows = [
            {"outcome": vocab.OUTCOME_RETRIEVED, "at": rules.stamp(NOW)},
            {"outcome": vocab.OUTCOME_BACK_PRESSURE, "at": rules.stamp(NOW)},
            {"outcome": vocab.OUTCOME_THROTTLED, "at": rules.stamp(NOW)},
            {"outcome": vocab.OUTCOME_SANDBOX_REJECTED, "at": rules.stamp(NOW)},
        ]
        assert rules.counted_attempts(rows, NOW) == 2

    def test_an_unreadable_stamp_is_outside_every_window(self):
        assert rules.within_window("not a stamp", NOW, 60) is False
        assert rules.within_window(None, NOW, 60) is False
        assert rules.parse_stamp("nonsense") is None

    def test_a_naive_stamp_is_read_as_utc(self):
        parsed = rules.parse_stamp("2026-10-04T09:00:00")
        assert parsed is not None and parsed.tzinfo is not None

    def test_the_throttle_is_per_document(self, engine: EvaultEngine):
        first = make_document(engine, vendor_id="pd_1", state=vocab.STATE_SEALED)
        second = make_document(engine, vendor_id="pd_2", state=vocab.STATE_SEALED)
        for _ in range(vocab.THROTTLE_LIMIT):
            engine.retrieve(first["id"], source="fixture")
        with pytest.raises(rules.Throttled):
            engine.retrieve(first["id"], source="fixture")
        assert engine.retrieve(second["id"], source="fixture")["outcome"] == vocab.OUTCOME_RETRIEVED

    def test_the_derivation_is_recorded(self):
        assert "DERIVED_THROTTLE_WINDOW" in evault_inferences.DECISIONS

    def test_the_code_is_the_specifications_own_word(self):
        assert vocab.OUTCOME_THROTTLED == "throttled"
        assert vocab.STATUS_THROTTLED == 429


# --------------------------------------------------------------------------- #
# the round trip
# --------------------------------------------------------------------------- #


class TestTheRetryLoop:
    def test_the_whole_flow_runs_without_polling(self, engine: EvaultEngine):
        document = make_document(engine)
        make_ready(engine, document)
        waiting = engine.retrieve(document["id"], source="fixture")
        assert waiting["outcome"] == vocab.OUTCOME_BACK_PRESSURE

        engine.set_state(document["id"], vocab.STATE_SEALED, source="fixture", actor="vendor")
        closed = engine.retrieve(document["id"], source="fixture")
        assert closed["outcome"] == vocab.OUTCOME_RETRIEVED

        shaped = engine.serve_protected(document["id"], room_id="room_a")
        assert shaped["status"] == vocab.STATUS_READY

    def test_the_ready_event_moves_the_state_to_generating(self, engine: EvaultEngine):
        document = make_document(engine)
        report = make_ready(engine, document)
        assert report["state_before"] == vocab.STATE_AWAITING_SIGNATURES
        assert report["state_after"] == vocab.STATE_GENERATING

    def test_a_generating_document_reports_back_pressure_on_the_projection(
        self, engine: EvaultEngine
    ):
        document = make_document(engine)
        make_ready(engine, document)
        view = engine.read_document(document["id"])
        assert view["back_pressure"] is True
        assert view["artifact_ready"] is False

    def test_a_sealed_document_reports_its_artifact(self, engine: EvaultEngine):
        document = make_document(engine, state=vocab.STATE_SEALED)
        make_sealed(engine, document)
        view = engine.read_document(document["id"])
        assert view["artifact_ready"] is True
        assert view["sealed_artifact"]["sha256"]
        assert view["plain_artifact"] is None

    def test_the_latest_attempt_is_on_the_projection(self, engine: EvaultEngine):
        document = make_document(engine)
        make_ready(engine, document)
        engine.retrieve(document["id"], source="fixture")
        view = engine.read_document(document["id"])
        assert view["latest_attempt"]["outcome"] == vocab.OUTCOME_BACK_PRESSURE

    def test_serving_writes_nothing_at_all(self, engine: EvaultEngine, store: RecordStore):
        document = make_document(engine, state=vocab.STATE_SEALED)
        make_sealed(engine, document)
        before = store.db.audit_count()
        engine.serve_protected(document["id"], room_id="room_a")
        engine.serve_protected(document["id"], room_id="room_a")
        assert store.db.audit_count() == before

    def test_the_state_field_is_a_closed_set(self):
        with pytest.raises(rules.DocumentInvalid):
            rules.coerce_state("somewhere_else")

    def test_the_variant_field_is_a_closed_set(self):
        with pytest.raises(rules.DocumentInvalid):
            rules.coerce_variant("protected")


# --------------------------------------------------------------------------- #
# room scoping
# --------------------------------------------------------------------------- #


class TestRoomScoping:
    def test_a_document_in_another_room_is_not_read(self, engine: EvaultEngine):
        document = make_document(engine, "room_a")
        with pytest.raises(rules.DocumentNotFound):
            engine.read_document(document["id"], room_id="room_b")

    def test_a_document_in_another_room_is_not_retrieved(self, engine: EvaultEngine):
        document = make_document(engine, "room_a", state=vocab.STATE_SEALED)
        with pytest.raises(rules.DocumentNotFound):
            engine.retrieve(document["id"], room_id="room_b", source="fixture")

    def test_a_document_in_another_room_is_not_served(self, engine: EvaultEngine):
        document = make_document(engine, "room_a", state=vocab.STATE_SEALED)
        make_sealed(engine, document)
        with pytest.raises(rules.DocumentNotFound):
            engine.serve_protected(document["id"], room_id="room_b")

    def test_two_rooms_may_each_hold_the_same_vendor_document_id(self, engine: EvaultEngine):
        first = make_document(engine, "room_a", vendor_id="pd_shared")
        second = make_document(engine, "room_b", vendor_id="pd_shared")
        assert first["id"] != second["id"]

    def test_one_room_may_not_hold_the_same_vendor_document_id_twice(self, engine: EvaultEngine):
        first = make_document(engine, "room_a", vendor_id="pd_shared")
        with pytest.raises(rules.DocumentInvalid) as caught:
            make_document(engine, "room_a", vendor_id="pd_shared")
        assert first["id"] in caught.value.errors["vendor_document_id"]

    def test_a_document_without_a_vendor_id_is_refused(self, engine: EvaultEngine):
        with pytest.raises(rules.DocumentInvalid):
            engine.register_document("room_a", {"subject": "no id"}, source="fixture", actor="dana")

    def test_a_row_from_another_collection_is_not_read_as_a_document(self, engine: EvaultEngine):
        room = make_subscription(engine)
        with pytest.raises(rules.DocumentNotFound):
            engine.read_document(room["id"])


# --------------------------------------------------------------------------- #
# the subscription lifecycle
# --------------------------------------------------------------------------- #


class TestTheSubscription:
    def test_a_created_subscription_carries_a_derived_shared_key(self, engine: EvaultEngine):
        row = make_subscription(engine)
        assert row["shared_key"].startswith("shr_")

    def test_the_shared_key_is_stable_for_one_document(self):
        assert rules.derive_shared_key("pd_1") == rules.derive_shared_key("pd_1")

    def test_the_shared_key_differs_between_documents(self):
        assert rules.derive_shared_key("pd_1") != rules.derive_shared_key("pd_2")

    def test_the_shared_key_derivation_is_recorded(self):
        assert "INFERRED_SUBSCRIPTION_SHARED_KEY" in evault_inferences.DECISIONS

    def test_an_omitted_trigger_list_is_left_alone_by_a_patch(self, engine: EvaultEngine):
        row = make_subscription(engine)
        updated = engine.update_subscription(
            row["id"], {"environment": "sandbox"}, source="fixture"
        )
        assert updated["triggers"] == [vocab.PDF_READY_TRIGGER]
        assert updated["environment"] == vocab.ENVIRONMENT_SANDBOX

    def test_a_supplied_trigger_list_replaces_the_whole_set(self, engine: EvaultEngine):
        row = make_subscription(engine)
        updated = engine.update_subscription(
            row["id"],
            {"triggers": [vocab.PDF_READY_TRIGGER, "document_completed"]},
            source="fixture",
        )
        assert len(updated["triggers"]) == 2

    def test_a_patch_that_drops_the_ready_trigger_is_refused_and_changes_nothing(
        self, engine: EvaultEngine
    ):
        row = make_subscription(engine)
        with pytest.raises(rules.SubscriptionInvalid):
            engine.update_subscription(row["id"], {"triggers": ["document_sent"]}, source="fixture")
        assert engine.read_subscription(row["id"])["triggers"] == [vocab.PDF_READY_TRIGGER]

    def test_a_null_trigger_list_leaves_the_field_out(self, engine: EvaultEngine):
        row = make_subscription(engine)
        updated = engine.update_subscription(row["id"], {"triggers": None}, source="fixture")
        assert updated["triggers"] == [vocab.PDF_READY_TRIGGER]

    def test_an_empty_patch_changes_nothing(self, engine: EvaultEngine):
        row = make_subscription(engine)
        assert engine.update_subscription(row["id"], {}, source="fixture")["id"] == row["id"]

    def test_cancelling_keeps_the_row_readable(self, engine: EvaultEngine):
        row = make_subscription(engine)
        cancelled = engine.cancel_subscription(row["id"], source="fixture")
        assert cancelled["active"] is False
        assert cancelled["state"] == "cancelled"
        assert engine.read_subscription(row["id"])["cancelled_at"]

    def test_cancelling_twice_is_idempotent(self, engine: EvaultEngine):
        row = make_subscription(engine)
        first = engine.cancel_subscription(row["id"], source="fixture")
        second = engine.cancel_subscription(row["id"], source="fixture")
        assert first["cancelled_at"] == second["cancelled_at"]

    def test_an_inactive_subscription_is_left_out_of_the_active_list(self, engine: EvaultEngine):
        row = make_subscription(engine)
        engine.cancel_subscription(row["id"], source="fixture")
        assert engine.active_subscriptions("room_a") == []
        assert len(engine.subscriptions("room_a")) == 1

    def test_a_row_from_another_collection_is_not_read_as_a_subscription(
        self, engine: EvaultEngine
    ):
        document = make_document(engine)
        with pytest.raises(rules.SubscriptionNotFound):
            engine.read_subscription(document["id"])

    def test_the_cancellation_derivation_is_recorded(self):
        assert "DERIVED_CANCEL_KEEPS_THE_ROW" in evault_inferences.DECISIONS


# --------------------------------------------------------------------------- #
# the board and the research
# --------------------------------------------------------------------------- #


class TestTheBoard:
    def test_an_empty_room_reads_zero_rather_than_failing(self, engine: EvaultEngine):
        summary = engine.summary("room_a")
        assert summary["documents"] == 0
        assert summary["deliveries"] == 0
        assert summary["artifacts"] == 0

    def test_the_counts_match_what_was_written(self, engine: EvaultEngine):
        document = make_document(engine, state=vocab.STATE_SEALED)
        make_sealed(engine, document)
        make_ready(engine, document)
        summary = engine.summary("room_a")
        assert summary["documents"] == 1
        assert summary["attempts"] == 1
        assert summary["artifacts"] == 1
        assert summary["deliveries"] == 1

    def test_the_retry_tally_is_separate_from_the_delivery_count(self, engine: EvaultEngine):
        document = make_document(engine)
        make_ready(engine, document)
        engine.receive_event(
            "room_a",
            headers={vocab.DEDUPE_HEADER: "evt_pd_doc_1"},
            payload={"data": {"id": "pd_doc_1"}},
            source="fixture",
            actor="vendor",
        )
        summary = engine.summary("room_a")
        assert summary["deliveries"] == 1
        assert summary["retries_deduped"] == 1

    def test_the_outcomes_are_tallied_by_name(self, engine: EvaultEngine):
        document = make_document(engine)
        make_ready(engine, document)
        engine.retrieve(document["id"], source="fixture")
        summary = engine.summary("room_a")
        assert summary["attempts_by_outcome"][vocab.OUTCOME_BACK_PRESSURE] == 1

    def test_the_summary_carries_the_four_invariants(self, engine: EvaultEngine):
        invariants = engine.summary("room_a")["invariants"]
        assert invariants["no_polling"] == vocab.NO_POLLING
        assert vocab.SEALED_ENVIRONMENT in invariants["sealed_is_production_only"]
        assert "byte-stable" in invariants["sealed_is_byte_stable"]
        assert "certificate chain" in invariants["verification_scope"]

    def test_the_vocabulary_serves_every_published_term(self):
        served = rules.vocabulary()
        assert served["trigger"] == vocab.PDF_READY_TRIGGER
        assert served["dedupe_header"] == vocab.DEDUPE_HEADER
        assert {v["variant"] for v in served["variants"]} == set(vocab.VARIANTS)
        assert {s["state"] for s in served["document_states"]} == set(vocab.DOCUMENT_STATES)
        assert {o["outcome"] for o in served["fetch_outcomes"]} == set(vocab.FETCH_OUTCOMES)
        assert served["media_type"] == "application/pdf"
        assert set(served["collections"]) == set(vocab.ALL_COLLECTIONS)

    def test_every_variant_is_described_with_its_environment(self):
        served = rules.vocabulary()
        by_variant = {row["variant"]: row for row in served["variants"]}
        assert by_variant["sealed"]["environments"] == ["production"]
        assert set(by_variant["plain"]["environments"]) == set(vocab.ENVIRONMENTS)

    def test_the_throttle_settings_are_served(self):
        throttle = rules.vocabulary()["throttle"]
        assert throttle["limit"] == vocab.THROTTLE_LIMIT
        assert throttle["window_seconds"] == vocab.THROTTLE_WINDOW_SECONDS
        assert throttle["code"] == "throttled"


# --------------------------------------------------------------------------- #
# the architectural guards
# --------------------------------------------------------------------------- #


class TestArchitecture:
    def test_the_domain_package_imports_nothing_but_the_store(self):
        """The guard the brief names by name.

        The rule is about the dependency direction, not about banning the standard
        library. ``datetime`` and ``hashlib`` are ordinary Python; what would be a defect
        is a domain module reaching for ``dsr.api`` (which reintroduces the coupling the
        host removes) or for any other part of ``dsr``. So the standard library is allowed
        and every ``dsr`` import is checked against the list this workflow may depend on.
        """
        allowed = {"dsr.store", "dsr.security_governance"}
        for path in _domain_paths():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if not name.startswith("dsr"):
                        continue
                    assert name in allowed, (
                        f"{path.name} imports {name}. The domain module may depend on the "
                        f"store and on itself, and on nothing else inside dsr."
                    )

    def test_the_domain_package_never_imports_the_app(self):
        for path in _domain_paths():
            text = path.read_text(encoding="utf-8")
            assert "from dsr.api" not in text and "import dsr.api" not in text

    def test_the_domain_package_never_opens_sqlite(self):
        for path in _domain_paths():
            assert "import sqlite3" not in path.read_text(encoding="utf-8")

    def test_the_feature_module_never_imports_the_app(self):
        assert "from dsr.api" not in _feature_path().read_text(encoding="utf-8")

    def test_the_feature_module_exports_the_documented_surface(self):
        module = importlib.import_module(FEATURE_MODULE)
        assert module.FEATURE["id"] == "wf-080-download-the-executed-agreement-from-the-e-vault"
        assert module.FEATURE["ticket"] == "WF-080"
        assert module.router.prefix == "/api/wf-080"
        assert set(module.EXCEPTION_HANDLERS) == {
            rules.SubscriptionInvalid,
            rules.DocumentInvalid,
            rules.SubscriptionNotFound,
            rules.DocumentNotFound,
            rules.ArtifactNotFound,
            rules.NotCompleted,
            rules.SandboxKeyRejected,
            rules.Throttled,
        }

    def test_the_shared_package_initializer_is_untouched(self):
        """WF-073 and WF-075 ship in this package. Appending four modules to its
        initializer would collide with another workflow branch on the same lines for no
        benefit, because Python imports a submodule without the package listing it."""
        initializer = Path(_domain_package_dir()) / "__init__.py"
        text = initializer.read_text(encoding="utf-8")
        assert "evault" not in text

    def test_the_records_are_plain_json_with_no_migration(self):
        """A team adding a field must need no coordination with anyone."""
        database = AuditedDatabase(":memory:")
        try:
            rows = database._conn.execute(  # noqa: SLF001 - reading the schema to prove absence
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        finally:
            database.close()
        names = {row["name"] for row in rows}
        assert "records" in names
        assert not any(name.startswith("wf080") for name in names), (
            "this workflow must add its data as records.data, not as a typed column"
        )

    def test_every_collection_is_namespaced_with_the_ticket(self):
        for name in vocab.ALL_COLLECTIONS:
            assert name.startswith("wf080_")

    def test_the_sealed_variant_is_the_default(self):
        assert rules.coerce_variant(None) == vocab.VARIANT_SEALED

    def test_every_mapped_error_is_raised_somewhere_in_this_build(self):
        """An error type nothing raises, mapped to a handler that never runs, is a claim the
        code cannot back.

        The handler mapping is a contract with the host, so both halves are asserted: every
        mapped type is raised by this build, and every type this build raises is mapped. A
        type declared and forgotten fails the first half. A type raised and unmapped fails
        the second, and would surface as a 500 instead of the status the research names.
        """
        module = importlib.import_module(FEATURE_MODULE)
        mapped = {error_type.__name__ for error_type in module.EXCEPTION_HANDLERS}

        declared = _declared_error_types()
        raised = _raised_error_types()

        assert mapped, "the router exports no handlers, so this test measures nothing"
        assert mapped == declared, f"declared but unmapped: {sorted(declared - mapped)}"
        # `mapped <= raised` rather than equality, because `raised` also finds the built-in
        # ValueErrors this module raises and catches inside one function to turn a bad
        # request body into a 400. Those never reach a handler and are not what this rule is
        # about; a mapped type going unraised is.
        assert mapped <= raised, f"mapped but never raised: {sorted(mapped - raised)}"


# --------------------------------------------------------------------------- #
# the recorded derivations
# --------------------------------------------------------------------------- #


class TestInferences:
    def test_every_open_question_was_recorded(self):
        assert evault_inferences.count() >= 8

    def test_the_derivation_the_specification_marked_inferred_is_recorded(self):
        assert "INFERRED_SUBSCRIPTION_SHARED_KEY" in evault_inferences.DECISIONS

    def test_every_record_names_a_rejected_alternative(self):
        """A derivation with no rejected option recorded is a guess wearing a derivation's
        clothes, and a reviewer cannot tell the two apart."""
        for key, decision in evault_inferences.DECISIONS.items():
            assert decision.get("options"), key
            assert decision.get("chosen") in decision["options"], key
            assert decision.get("rejected_because"), key
            assert decision.get("cost_of_the_choice"), key

    def test_describe_returns_every_decision_with_its_id(self):
        described = evault_inferences.describe()
        assert len(described) == evault_inferences.count()
        assert {item["id"] for item in described} == set(evault_inferences.DECISIONS)

    def test_describe_one_returns_nothing_for_an_unknown_id(self):
        assert evault_inferences.describe_one("NOPE") == {}

    def test_the_engine_serves_the_register(self):
        served = EvaultEngine(RecordStore(AuditedDatabase(":memory:"))).inferences()
        assert served["count"] == evault_inferences.count()
        assert served["decisions"]


# --------------------------------------------------------------------------- #
# the seed
# --------------------------------------------------------------------------- #


class TestSeed:
    def _seed(self, db: AuditedDatabase, room_ids=None):
        module = importlib.import_module(FEATURE_MODULE)
        return module.seed(db, {"room_ids": room_ids or [], "now": NOW, "rng": None})

    def test_the_return_string_encodes_as_cp1252(self, memory_db: AuditedDatabase):
        """The seeder prints this to a Windows console, and one RIGHTWARDS ARROW in a
        single feature broke the entire seeder on this host. Asserted by encoding it, not
        by reading it."""
        summary = self._seed(memory_db)
        summary.encode("cp1252")
        summary.encode("ascii")

    def test_the_seed_states_that_it_is_deterrence(self, memory_db: AuditedDatabase):
        assert "not the sealed artifact" in self._seed(memory_db)

    def test_the_seed_names_states_that_are_not_all_successes(self, memory_db: AuditedDatabase):
        """The acceptance criterion: the seed prints states that are not all successes."""
        summary = self._seed(memory_db)
        for expected in (
            vocab.OUTCOME_DUPLICATE,
            vocab.OUTCOME_BACK_PRESSURE,
            vocab.OUTCOME_RETRIEVED,
            "not_completed_awaiting_signatures",
            vocab.OUTCOME_SANDBOX_REJECTED,
            "throttled",
        ):
            assert expected in summary, expected

    def test_the_seed_refuses_nothing_it_claims_to_refuse(self, memory_db: AuditedDatabase):
        summary = self._seed(memory_db)
        assert "not refused" not in summary
        assert "not throttled" not in summary
        assert "NOT SEALED" not in summary
        assert "NO RETRY RECORDED" not in summary
        assert "RETRY NOT DEDUPED" not in summary

    def test_the_seed_uses_the_rooms_the_seeder_handed_over(self, db: AuditedDatabase):
        store = RecordStore(db)
        store.create(
            "room", {"name": "Given"}, record_id="room_given", actor="dana", source="fixture"
        )
        summary = self._seed(db, [("room_given", "Given")])
        assert "1 room(s) from the seeder" in summary
        assert "2 room(s) created" in summary

    def test_the_seed_writes_through_the_audited_store(self, memory_db: AuditedDatabase):
        before = memory_db.audit_count()
        self._seed(memory_db)
        assert memory_db.audit_count() > before


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _domain_package_dir() -> Path:
    module = importlib.import_module(f"{DOMAIN_PACKAGE}.{DOMAIN_PREFIX}rules")
    return Path(module.__file__).parent


def _domain_paths() -> list[Path]:
    """Only this workflow's four modules.

    The package is shared with WF-073 and WF-075, so a guard that read every file in it
    would be asserting this workflow's rules over another ticket's code.
    """

    return sorted(_domain_package_dir().glob(f"{DOMAIN_PREFIX}*.py"))


def _feature_path() -> Path:
    module = importlib.import_module(FEATURE_MODULE)
    return Path(module.__file__)


def _this_workflows_python() -> list[Path]:
    """The four domain modules and the one feature module. Nothing else in the package."""
    return [*_domain_paths(), _feature_path()]


def _declared_error_types() -> set[str]:
    """Every error class this workflow declares.

    Found by parsing rather than by reading, so a class defined on a line the grep would
    miss is still counted.
    """
    names: set[str] = set()
    for path in _this_workflows_python():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                base_name = base.id if isinstance(base, ast.Name) else None
                if base_name in {"ValueError", "LookupError", "RuntimeError", "PermissionError"}:
                    names.add(node.name)
    return names


def _raised_error_types() -> set[str]:
    """Every error class this workflow raises.

    The attribute is ignored on purpose: the engine raises ``rules.NotCompleted`` and the
    seeder catches ``rules.NotCompleted``, and both spellings are the same class. What
    matters is that the name appears as a raised expression somewhere.
    """
    names: set[str] = set()
    for path in _this_workflows_python():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names
