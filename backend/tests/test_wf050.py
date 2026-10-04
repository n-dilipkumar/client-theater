"""WF-050 domain tests: the classification, the two comparisons, and the repair.

Every test here maps to a row of the test plan in
``docs/design/WF-050-reconcile-gaps-after-a-dropped-change.md`` section 10. The
"Pins" comment on each test names the numbered sourced item it quotes.

Isolation: every fixture builds its own in-memory database, so this file passes on
its own and under ``pytest-xdist`` in any order. Nothing is shared between tests,
and no test reads a row another test wrote.

The clock is injected rather than mocked, because the seven-day cursor rule is
about how long *the room* has held a token and the room's clock is the one that has
to be right.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from dsr.crm_integration import gap_vocabulary as vocab, reconcile as rules
from dsr.crm_integration.gap_errors import (
    DeletedSourceVendorMismatch,
    GapReconcileError,
    MalformedGapEvent,
    MismatchedCursorVendor,
    MissingEntity,
    MissingRecordId,
    NoDirtyRecord,
    UnknownDeletedSource,
    UnknownEntity,
    UnknownEvent,
    UnknownGapType,
    UnknownRun,
    UnsupportedVendor,
)
from dsr.crm_integration.gap_sources import SimulatedCrm, StoredCrm
from dsr.crm_integration.reconcile_engine import ReconcileEngine
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore

MODULE = "wf050_reconcile_gaps_after_a_dropped_change_st"
FEATURE_ID = "wf-050-reconcile-gaps-after-a-dropped-change-st"
PREFIX = "/api/wf-050"

ROOM = "room-1"
OTHER_ROOM = "room-2"
ENTITY = "Opportunity"
VENDOR = "salesforce"
DATAV = "dataverse"

SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/reconcile"
CHANGE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/change-events"
EVENT_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/gap-events"

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


class Clock:
    """A clock the test moves, because a vendor's own clock is not this product's."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: Any) -> datetime:
        self.now = self.now + timedelta(**kwargs)
        return self.now


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def store():
    # In-memory rather than a file on disk: the engine reads whole collections to
    # derive its delete diff, and a file per test buys nothing for this size.
    db = AuditedDatabase()
    try:
        yield RecordStore(db)
    finally:
        db.close()


@pytest.fixture()
def engine(store, clock):
    return ReconcileEngine(store, clock=clock)


def header(change_type: str, **overrides: Any) -> dict[str, Any]:
    """A gap or overflow header with the researched field names."""
    body = {
        "changeType": change_type,
        "transactionKey": "tx-1",
        "commitTimestamp": NOW.isoformat(),
        "entity": ENTITY,
        "recordIds": ["006A000001"],
    }
    body.update(overrides)
    return body


def source_row(engine: ReconcileEngine, record_id: str, **payload: Any) -> None:
    """One live row in the room's copy of the vendor's tables."""
    engine.put_source(
        ROOM,
        entity=ENTITY,
        record_id=record_id,
        payload={"Name": record_id, **payload},
        actor="test",
        source=SOURCE,
    )


def replica_row(engine: ReconcileEngine, record_id: str, deleted: bool = False) -> None:
    """One row in the replica the room held before the gap."""
    engine.store.create(
        "crm_gap_replica",
        {
            "entity": ENTITY,
            "record_id": record_id,
            "deleted": deleted,
            "payload": {"Name": record_id},
            "reason": "seeded",
        },
        room_id=ROOM,
        actor="test",
        source=SOURCE,
    )


# --------------------------------------------------------------------------- #
# Test plan row 1: the four gap types
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("change_type", "operation"),
    [
        ("GAP_CREATE", "create"),
        ("GAP_UPDATE", "update"),
        ("GAP_DELETE", "delete"),
        ("GAP_UNDELETE", "undelete"),
    ],
)
def test_each_of_the_four_gap_types_is_a_gap_and_names_its_entity_and_record(
    engine: ReconcileEngine, change_type: str, operation: str
):
    """Pins 1. "The changeType field in the gap event header ... can take one of
    these values: GAP_CREATE, GAP_UPDATE, GAP_DELETE, GAP_UNDELETE." """
    reported = engine.report_gap_event(ROOM, header(change_type), actor="test", source=EVENT_SOURCE)

    assert reported["kind"] == "gap"
    assert reported["event"]["change_type"] == change_type
    assert reported["event"]["operation"] == operation
    assert reported["event"]["entity"] == ENTITY
    assert reported["event"]["record_ids"] == ["006A000001"]
    assert [row["record_id"] for row in reported["dirty_records"]] == ["006A000001"]


def test_the_change_type_is_folded_but_an_unknown_word_is_refused(engine: ReconcileEngine):
    """The research's case-sensitivity rule is about channel names, not changeType."""
    assert vocab.normalise_change_type("gap_update") == "GAP_UPDATE"
    assert vocab.normalise_change_type("  Gap_Update  ") == "GAP_UPDATE"
    with pytest.raises(UnknownGapType) as caught:
        engine.report_gap_event(ROOM, header("GAP_REORG"), actor="test", source=EVENT_SOURCE)
    assert "GAP_CREATE" in str(caught.value)
    with pytest.raises(UnknownGapType):
        engine.report_gap_event(ROOM, header("CREATE"), actor="test", source=EVENT_SOURCE)


# --------------------------------------------------------------------------- #
# Test plan row 2: an overflow names no record
# --------------------------------------------------------------------------- #


def test_an_overflow_is_not_a_gap_and_carries_no_record_id(engine: ReconcileEngine):
    """Pins 2 and 3. "Overflow events include header fields but no record data and
    no record ID." """
    reported = engine.report_gap_event(
        ROOM,
        {
            "changeType": "GAP_OVERFLOW",
            "transactionKey": "tx-over",
            "commitTimestamp": NOW.isoformat(),
            "entity": ENTITY,
            "recordIds": ["006A000001"],
            "replayId": "replay-7",
        },
        actor="test",
        source=EVENT_SOURCE,
    )

    assert reported["kind"] == "overflow"
    assert reported["event"]["record_ids"] == [], "an overflow names no record"
    assert reported["event"]["operation"] == "", "an overflow names no operation either"
    assert reported["dirty_records"] == [], "an overflow marks no single record dirty"


def test_an_overflow_unsubscribes_and_stores_the_replay_id(engine: ReconcileEngine):
    """Pins 9, steps 1 and 2 of the overflow procedure. "After you receive an
    overflow event in your subscriber, unsubscribe from the channel ... Store the
    Replay ID of the overflow event." """
    reported = engine.report_gap_event(
        ROOM,
        header("GAP_OVERFLOW", recordIds=None, changeCount=150000, replayId="replay-7"),
        actor="test",
        source=EVENT_SOURCE,
    )

    assert reported["replay_id_stored"] is True
    assert reported["event"]["subscription_state"] == "unsubscribed"
    stored = engine.cursors(ROOM)
    assert [row["kind"] for row in stored] == ["replay_id"]
    assert stored[0]["position"] == "replay-7"
    assert stored[0]["scope"] == "entity"
    assert stored[0]["scope_value"] == ENTITY


def test_an_overflow_with_no_replay_id_is_still_recorded_and_still_unsubscribes(
    engine: ReconcileEngine,
):
    """The procedure says to store the Replay ID. A vendor that sends none has not
    sent a usable position, and the room must not invent one."""
    reported = engine.report_gap_event(
        ROOM,
        header("GAP_OVERFLOW", recordIds=None, changeCount=200000),
        actor="test",
        source=EVENT_SOURCE,
    )
    assert reported["event"]["subscription_state"] == "unsubscribed"
    assert engine.cursors(ROOM)[0]["position"] is None
    assert engine.cursors(ROOM)[0]["resumable"] is False


def test_a_gap_leaves_the_room_subscribed(engine: ReconcileEngine):
    """The unsubscribe step belongs to the overflow, not to every gap."""
    reported = engine.report_gap_event(
        ROOM, header("GAP_UPDATE"), actor="test", source=EVENT_SOURCE
    )
    assert reported["event"]["subscription_state"] == "subscribed"


# --------------------------------------------------------------------------- #
# Test plan row 3: the overflow threshold
# --------------------------------------------------------------------------- #


def test_the_overflow_threshold_is_the_named_constant_and_not_a_literal():
    """Pins 4. "Overflow events are generated when a single transaction involves
    more than 100,000 changes." """
    assert vocab.OVERFLOW_CHANGE_THRESHOLD == 100_000
    assert vocab.NUMBERS["overflow_change_threshold"]["value"] == 100_000
    assert vocab.exceeds_overflow_threshold(100_000) is False, "at the threshold is not past it"
    assert vocab.exceeds_overflow_threshold(100_001) is True
    assert vocab.exceeds_overflow_threshold("100001") is True
    assert vocab.exceeds_overflow_threshold(None) is False
    assert vocab.exceeds_overflow_threshold("many") is False
    assert vocab.exceeds_overflow_threshold(True) is False, "a boolean is not a count"


def test_an_overflow_header_records_whether_it_passed_the_threshold(engine: ReconcileEngine):
    reported = engine.report_gap_event(
        ROOM,
        header("GAP_OVERFLOW", recordIds=None, changeCount=100_001, replayId="r1"),
        actor="test",
        source=EVENT_SOURCE,
    )
    assert reported["event"]["change_count"] == 100_001
    assert reported["event"]["exceeds_overflow_threshold"] is True


# --------------------------------------------------------------------------- #
# Test plan row 4: the dirty marker
# --------------------------------------------------------------------------- #


def test_a_gap_marks_the_record_dirty_as_of_the_gap_events_own_date(engine: ReconcileEngine):
    """Pins 5. "For the gap event, mark the corresponding record as dirty locally as
    of the date of the gap event." """
    stamped = NOW - timedelta(hours=3)
    engine.report_gap_event(
        ROOM,
        header("GAP_UPDATE", commitTimestamp=stamped.isoformat()),
        actor="test",
        source=EVENT_SOURCE,
    )

    dirty = engine.dirty_records(ROOM)
    assert len(dirty) == 1
    assert dirty[0]["record_id"] == "006A000001"
    assert dirty[0]["state"] == "dirty"
    assert dirty[0]["dirty"] is True
    assert dirty[0]["gap_commit_timestamp"] == stamped.astimezone(timezone.utc).isoformat()
    assert dirty[0]["change_type"] == "GAP_UPDATE"


def test_two_gaps_on_one_record_owone_re_read_not_two(engine: ReconcileEngine):
    """The dirty set is keyed by (room, entity, record_id), so a second gap on a
    record that is already dirty re-opens the marker and keeps the earlier date."""
    earlier = NOW - timedelta(hours=5)
    later = NOW - timedelta(hours=1)
    engine.report_gap_event(
        ROOM,
        header("GAP_UPDATE", commitTimestamp=earlier.isoformat()),
        actor="test",
        source=EVENT_SOURCE,
    )
    engine.report_gap_event(
        ROOM,
        header("GAP_DELETE", commitTimestamp=later.isoformat()),
        actor="test",
        source=EVENT_SOURCE,
    )

    dirty = engine.dirty_records(ROOM)
    assert len(dirty) == 1
    assert dirty[0]["gap_commit_timestamp"] == earlier.astimezone(timezone.utc).isoformat()
    assert dirty[0]["change_type"] == "GAP_DELETE", "the later gap's operation is what matters"


def test_the_same_record_id_in_two_rooms_is_two_markers(engine: ReconcileEngine):
    """A CRM id is unique per org, not per room, so the marker is keyed by room."""
    engine.report_gap_event(ROOM, header("GAP_UPDATE"), actor="test", source=EVENT_SOURCE)
    engine.report_gap_event(OTHER_ROOM, header("GAP_UPDATE"), actor="test", source=EVENT_SOURCE)
    assert len(engine.dirty_records(ROOM)) == 1
    assert len(engine.dirty_records(OTHER_ROOM)) == 1


def test_a_gap_event_with_no_record_id_is_refused(engine: ReconcileEngine):
    """Pins 1. Gap events "contain the record ID, which enables you to retrieve the
    record from Salesforce." """
    with pytest.raises(MissingRecordId) as caught:
        engine.report_gap_event(
            ROOM, header("GAP_UPDATE", recordIds=[]), actor="test", source=EVENT_SOURCE
        )
    assert "record ID" in str(caught.value)
    assert engine.gap_events(ROOM) == [], "a refused header writes nothing"


def test_a_header_with_no_entity_is_refused(engine: ReconcileEngine):
    """An overflow emits "one overflow event for each entity type in that set", so
    an event with no entity cannot say which entity it lost."""
    with pytest.raises(MissingEntity):
        engine.report_gap_event(
            ROOM, header("GAP_UPDATE", entity=""), actor="test", source=EVENT_SOURCE
        )


def test_a_header_whose_timestamp_cannot_be_read_is_refused(engine: ReconcileEngine):
    """A half-read header is refused rather than stored, because the marker's whole
    value is the gap's commit timestamp."""
    with pytest.raises(MalformedGapEvent) as caught:
        engine.report_gap_event(
            ROOM,
            header("GAP_UPDATE", commitTimestamp="last tuesday"),
            actor="test",
            source=EVENT_SOURCE,
        )
    assert "ISO-8601" in str(caught.value)


def test_the_record_ids_may_arrive_as_one_id_a_list_or_a_comma_separated_string(
    engine: ReconcileEngine,
):
    for index, ids in enumerate(
        ["006A000001", ["006A000002", "006A000003"], "006A000004, 006A000005"]
    ):
        engine.report_gap_event(
            ROOM,
            header("GAP_UPDATE", recordIds=ids, transactionKey=f"tx-{index}"),
            actor="test",
            source=EVENT_SOURCE,
        )
    assert len(engine.dirty_records(ROOM)) == 5


# --------------------------------------------------------------------------- #
# Test plan row 19: the vendor scope
# --------------------------------------------------------------------------- #


def test_a_gap_on_a_hubspot_stream_is_refused_and_the_message_quotes_the_research(
    engine: ReconcileEngine,
):
    """The research's gaps section: "HubSpot has no documented gap/overflow analogue
    (it uses webhook redelivery instead)." """
    for change_type in vocab.GAP_CHANGE_TYPES + (vocab.OVERFLOW_CHANGE_TYPE,):
        with pytest.raises(UnsupportedVendor) as caught:
            engine.report_gap_event(
                ROOM, header(change_type, vendor="hubspot"), actor="test", source=EVENT_SOURCE
            )
        assert "no documented gap/overflow analogue" in str(caught.value)
        assert "salesforce, dataverse" in str(caught.value)


def test_the_vocabulary_says_which_vendors_have_a_gap_mechanism():
    assert vocab.GAP_VENDORS == ("salesforce", "dataverse")
    assert vocab.UNSUPPORTED_GAP_VENDORS == ("hubspot",)
    assert "HubSpot" in vocab.UNSUPPORTED_VENDOR_QUOTE
    assert vocab.cursor_supports_gap("Salesforce") is True
    assert vocab.cursor_supports_gap("hubspot") is False
    assert vocab.cursor_supports_gap(None) is False


# --------------------------------------------------------------------------- #
# Test plan rows 6 and 7: the two comparisons
# --------------------------------------------------------------------------- #

GAP_AT = "2026-09-27T10:00:00+00:00"


@pytest.mark.parametrize(
    ("change_at", "after"),
    [
        ("2026-09-27T10:00:00+00:00", False),
        ("2026-09-27T09:59:59+00:00", False),
        ("2026-09-27T10:00:01+00:00", True),
        ("2026-09-27T12:00:00+00:00", True),
    ],
)
def test_comparison_a_asks_whether_the_change_is_after_the_gap(change_at: str, after: bool):
    """Pins 7. "To ensure that the change is after the gap event, compare the
    commitTimestamp fields of both events." """
    assert rules.change_is_after_the_gap(GAP_AT, change_at) is after


@pytest.mark.parametrize(
    ("record_at", "covered"),
    [
        ("2026-09-27T10:06:00+00:00", True),
        ("2026-09-27T10:05:00+00:00", True),
        ("2026-09-27T10:04:00+00:00", False),
    ],
)
def test_comparison_b_asks_whether_the_reread_already_carries_the_change(
    record_at: str, covered: bool
):
    """Pins 7. "To ensure that the change occurred before the data is reconciled,
    compare the LastModifiedDate fields on the change event and the record
    retrieved in the next step." """
    assert rules.change_is_already_in_the_re_read("2026-09-27T10:05:00+00:00", record_at) is covered


def test_both_comparisons_normalise_the_zone_before_comparing():
    """The research says nothing about the format, so a bare Z and an offset must
    order the same as each other. A naive local time is not orderable."""
    assert (
        rules.change_is_after_the_gap("2026-09-27T12:00:00+02:00", "2026-09-27T10:00:01Z") is True
    )
    assert (
        rules.change_is_after_the_gap("2026-09-27T10:00:00Z", "2026-09-27T10:00:01+00:00") is True
    )
    assert (
        rules.change_is_already_in_the_re_read("2026-09-27T10:00:00Z", "2026-09-27T12:06:00+02:00")
        is True
    )
    assert rules.parse_timestamp(NOW) == NOW.isoformat()
    assert rules.parse_timestamp("2026-09-27T10:00:00Z") == "2026-09-27T10:00:00+00:00"
    assert rules.parse_timestamp(NOW.timestamp()) == NOW.isoformat()
    with pytest.raises(MalformedGapEvent):
        rules.parse_timestamp("nope")


def test_the_compare_functions_read_the_researchs_own_field_name():
    """A record spells the field as the vendor spells it, not as this product
    would have named it."""
    assert rules.last_modified_of({"LastModifiedDate": "2026-09-27T10:00:00Z"}) == (
        "2026-09-27T10:00:00+00:00"
    )
    assert rules.last_modified_of({}) is None
    assert rules.last_modified_of(None) is None
    assert vocab.LAST_MODIFIED_FIELD == "LastModifiedDate"


# --------------------------------------------------------------------------- #
# Test plan rows 5, 8 and 3: the drop rule
# --------------------------------------------------------------------------- #


def decide(**overrides: Any) -> tuple[bool, str]:
    """One call to the drop rule with a stated world."""
    arguments = {
        "dirty_marker": {"gap_commit_timestamp": GAP_AT},
        "commit_timestamp": "2026-09-27T10:05:00+00:00",
        "last_modified": "2026-09-27T10:05:00+00:00",
        "record": {"LastModifiedDate": "2026-09-27T10:04:00+00:00"},
    }
    arguments.update(overrides)
    return rules.decide_change(**arguments)


def test_the_four_drop_reasons_are_the_ones_the_research_describes():
    """Pins 6 and 7. The order is the research's order: not-after-the-gap first,
    then the comparison against the re-read."""
    assert rules.DROP_REASONS == (
        "no_dirty_marker",
        "older_than_the_gap",
        "covered_by_the_re_read",
        "dirty_and_newer_than_the_read",
    )


def test_a_change_for_a_clean_record_is_applied():
    assert decide(dirty_marker=None) == (True, "no_dirty_marker")


def test_a_change_that_did_not_commit_after_the_gap_is_dropped():
    assert decide(commit_timestamp=GAP_AT) == (False, "older_than_the_gap")
    assert decide(commit_timestamp="2026-09-27T09:00:00+00:00") == (False, "older_than_the_gap")


def test_a_change_the_reread_already_carries_is_applied_and_says_so():
    assert decide(record={"LastModifiedDate": "2026-09-27T10:06:00+00:00"}) == (
        True,
        "covered_by_the_re_read",
    )


def test_a_change_newer_than_the_reread_is_dropped():
    """This is the case the research's sentence exists for. The vendor is behind the
    stream, so applying the delta on top of a stale read would write a row that was
    never true."""
    assert decide() == (False, "dirty_and_newer_than_the_read")


def test_a_record_with_no_last_modified_date_cannot_cover_a_change():
    """Without a LastModifiedDate there is nothing to compare, so the change stays
    dropped and the next re-read has to see it."""
    assert decide(record=None) == (False, "dirty_and_newer_than_the_read")
    assert decide(record={"Name": "no date"}) == (False, "dirty_and_newer_than_the_read")


def test_a_change_event_for_a_dirty_record_is_dropped_and_the_replica_is_untouched(
    engine: ReconcileEngine,
):
    """Pins 6. "If you receive change events for new changes for the same record
    before the data has been reconciled, don't process them." """
    source_row(engine, "006A000001", LastModifiedDate="2026-09-27T10:04:00+00:00")
    replica_row(engine, "006A000001")
    before = engine.replica(ROOM)
    engine.report_gap_event(
        ROOM,
        header("GAP_UPDATE", commitTimestamp=GAP_AT),
        actor="test",
        source=EVENT_SOURCE,
    )

    outcome = engine.change_event(
        ROOM,
        {
            "entity": ENTITY,
            "record_id": "006A000001",
            "commit_timestamp": "2026-09-27T10:05:00+00:00",
            "LastModifiedDate": "2026-09-27T10:05:00+00:00",
            "payload": {"Name": "a value the vendor has not seen"},
        },
        actor="test",
        source=CHANGE_SOURCE,
    )

    assert outcome["applied"] is False
    assert outcome["reason"] == "dirty_and_newer_than_the_read"
    assert "has not reached this room" in outcome["detail"]
    assert engine.replica(ROOM) == before, "a dropped change writes nothing to the replica"
    assert len(engine.dirty_records(ROOM)) == 1, "and the marker stays open"


def test_a_change_event_for_a_clean_record_is_applied_to_the_replica(
    engine: ReconcileEngine,
):
    outcome = engine.change_event(
        ROOM,
        {
            "entity": ENTITY,
            "record_id": "006A000009",
            "commit_timestamp": "2026-09-27T10:05:00+00:00",
            "LastModifiedDate": "2026-09-27T10:05:00+00:00",
            "payload": {"Name": "Fabrikam pilot"},
        },
        actor="test",
        source=CHANGE_SOURCE,
    )
    assert outcome["applied"] is True
    assert outcome["reason"] == "no_dirty_marker"
    assert outcome["detail"].startswith("the record carries no dirty marker")
    rows = engine.replica(ROOM)
    assert [row["record_id"] for row in rows] == ["006A000009"]
    assert rows[0]["reason"] == "change_event"
    assert rows[0]["deleted"] is False


def test_a_change_event_naming_neither_entity_nor_record_applies_nothing(
    engine: ReconcileEngine,
):
    outcome = engine.change_event(ROOM, {}, actor="test", source=CHANGE_SOURCE)
    assert outcome["applied"] is False
    assert outcome["reason"] == "no_dirty_marker"
    assert "neither was sent" in outcome["detail"]
    assert engine.replica(ROOM) == []


def test_a_change_event_with_an_unreadable_timestamp_is_refused(engine: ReconcileEngine):
    with pytest.raises(MalformedGapEvent):
        engine.change_event(
            ROOM,
            {"entity": ENTITY, "record_id": "006A000001", "commit_timestamp": "soon"},
            actor="test",
            source=CHANGE_SOURCE,
        )


def test_the_vendor_record_wins_over_the_changes_own_payload(engine: ReconcileEngine):
    """The change event is a delta and the vendor record is the full row, so the
    full row is the base and the delta is layered on top."""
    source_row(engine, "006A000009", Stage="Closed")
    outcome = engine.change_event(
        ROOM,
        {
            "entity": ENTITY,
            "record_id": "006A000009",
            "commit_timestamp": "2026-09-27T10:05:00+00:00",
            "payload": {"Name": "Fabrikam pilot"},
        },
        actor="test",
        source=CHANGE_SOURCE,
    )
    payload = engine.replica(ROOM)[0]["payload"]
    assert payload["Name"] == "Fabrikam pilot"
    assert payload["Stage"] == "Closed"
    assert outcome["applied"] is True


# --------------------------------------------------------------------------- #
# Test plan rows 9 and 10: the per-record repair
# --------------------------------------------------------------------------- #


def test_a_reconcile_rereads_the_record_overwrites_the_replica_and_clears_the_flag(
    engine: ReconcileEngine,
):
    """Pins 8. "Reconcile the data for record C. Make a Salesforce API call ... to
    retrieve the full data for record C, and save it in your system. Then clear the
    dirty flag on that record." """
    source_row(engine, "006A000001", Amount=48000, LastModifiedDate="2026-09-27T11:00:00+00:00")
    reported = engine.report_gap_event(
        ROOM, header("GAP_UPDATE"), actor="test", source=EVENT_SOURCE
    )

    outcome = engine.reconcile(
        ROOM,
        {"event_id": reported["event"]["id"], "record_id": "006A000001"},
        actor="test",
        source=SOURCE,
    )

    assert outcome["scope"] == "record"
    assert outcome["dirty_cleared"] is True
    assert outcome["run"]["state"] == "complete"
    assert outcome["replica"]["payload"]["Amount"] == 48000
    assert outcome["replica"]["deleted"] is False
    assert outcome["replica"]["run_id"] == outcome["run"]["id"]
    assert engine.dirty_records(ROOM) == []
    assert engine.dirty_records(ROOM, state="reconciled")[0]["run_id"] == outcome["run"]["id"]


def test_a_gap_delete_whose_record_the_vendor_no_longer_holds_writes_a_tombstone(
    engine: ReconcileEngine,
):
    """Pins 8 and 9. A reader that answers None is a real answer, not a failure:
    this is the whole repair of a GAP_DELETE."""
    source_row(engine, "006A000003")
    engine.purge_source(ROOM, entity=ENTITY, record_id="006A000003", actor="test", source=SOURCE)
    replica_row(engine, "006A000003")
    reported = engine.report_gap_event(
        ROOM, header("GAP_DELETE", recordIds=["006A000003"]), actor="test", source=EVENT_SOURCE
    )

    outcome = engine.reconcile(
        ROOM,
        {"event_id": reported["event"]["id"], "record_id": "006A000003"},
        actor="test",
        source=SOURCE,
    )

    assert outcome["replica"]["deleted"] is True
    assert outcome["replica"]["record_id"] == "006A000003"
    assert engine.dirty_records(ROOM) == []
    events = [row["event"] for row in outcome["run"]["log"]]
    assert "replica_deleted" in events
    assert events.index("record_read") < events.index("replica_deleted")


def test_a_gap_undelete_brings_a_record_back_from_the_recycle_bin(
    engine: ReconcileEngine,
):
    """An undelete is repaired by the same full read; the record answers live
    again, so the replica row is live rather than a tombstone."""
    engine.put_source(
        ROOM,
        entity=ENTITY,
        record_id="006A000003",
        payload={"Name": "Fabrikam pilot"},
        is_deleted=True,
        actor="test",
        source=SOURCE,
    )
    reported = engine.report_gap_event(
        ROOM, header("GAP_UNDELETE", recordIds=["006A000003"]), actor="test", source=EVENT_SOURCE
    )
    engine.put_source(
        ROOM,
        entity=ENTITY,
        record_id="006A000003",
        payload={"Name": "Fabrikam pilot"},
        is_deleted=False,
        actor="test",
        source=SOURCE,
    )
    outcome = engine.reconcile(
        ROOM,
        {"event_id": reported["event"]["id"], "record_id": "006A000003"},
        actor="test",
        source=SOURCE,
    )
    assert outcome["replica"]["deleted"] is False


def test_a_reconcile_with_nothing_dirty_is_refused(engine: ReconcileEngine):
    """A clean record is not a gap, and repairing one would apply a read and a
    delete diff the room has no reason to run."""
    reported = engine.report_gap_event(
        ROOM, header("GAP_UPDATE"), actor="test", source=EVENT_SOURCE
    )
    engine.reconcile(
        ROOM,
        {"event_id": reported["event"]["id"], "record_id": "006A000001"},
        actor="test",
        source=SOURCE,
    )
    with pytest.raises(NoDirtyRecord) as caught:
        engine.reconcile(
            ROOM,
            {"event_id": reported["event"]["id"], "record_id": "006A000001"},
            actor="test",
            source=SOURCE,
        )
    assert "not dirty" in str(caught.value)


def test_a_reconcile_against_an_event_of_another_room_is_refused(engine: ReconcileEngine):
    reported = engine.report_gap_event(
        ROOM, header("GAP_UPDATE"), actor="test", source=EVENT_SOURCE
    )
    with pytest.raises(UnknownEvent):
        engine.reconcile(
            OTHER_ROOM,
            {"event_id": reported["event"]["id"], "record_id": "006A000001"},
            actor="test",
            source=SOURCE,
        )


def test_a_reconcile_of_an_overflow_repairs_the_entity_and_ignores_a_record_id(
    engine: ReconcileEngine,
):
    """An overflow "include[s] header fields but no record data and no record ID", so
    a record_id on the request changes nothing: the repair is a whole-entity read."""
    engine.put_source(
        ROOM, entity=ENTITY, record_id="006A000001", payload={}, actor="test", source=SOURCE
    )
    reported = seed_overflow(engine)

    outcome = engine.reconcile(
        ROOM,
        {"event_id": reported["event"]["id"], "record_id": "006A000001"},
        actor="test",
        source=SOURCE,
    )

    assert outcome["scope"] == "entity"
    assert outcome["run"]["kind"] == "overflow"
    assert [row["record_id"] for row in outcome["written"]] == ["006A000001"]


def test_a_reconcile_without_an_event_id_is_refused(engine: ReconcileEngine):
    with pytest.raises(UnknownEvent) as caught:
        engine.reconcile(ROOM, {}, actor="test", source=SOURCE)
    assert "needs the id" in str(caught.value)


# --------------------------------------------------------------------------- #
# Test plan rows 12, 13 and 14: the three ways the deleted set is derived
# --------------------------------------------------------------------------- #


def seed_overflow(
    engine: ReconcileEngine, vendor: str = VENDOR, **overrides: Any
) -> dict[str, Any]:
    """An overflow header. Defaults are built first so an override can replace any."""
    body = {
        "changeType": "GAP_OVERFLOW",
        "vendor": vendor,
        "transactionKey": "tx-overflow",
        "commitTimestamp": NOW.isoformat(),
        "entity": ENTITY,
        "recordIds": None,
        "changeCount": 150000,
        "replayId": "replay-7",
    }
    body.update(overrides)
    return engine.report_gap_event(ROOM, body, actor="test", source=EVENT_SOURCE)


def test_the_difference_source_deletes_exactly_what_the_room_held_and_the_read_did_not_return(
    engine: ReconcileEngine,
):
    """Pins 9, option a. "Get the non-deleted records from Salesforce, and
    synchronize." """
    for record_id in ("006A000001", "006A000002", "006A000003"):
        replica_row(engine, record_id)
    source_row(engine, "006A000001")
    source_row(engine, "006A000002")
    reported = seed_overflow(engine)

    outcome = engine.reconcile(
        ROOM,
        {"event_id": reported["event"]["id"], "entity": ENTITY, "deleted_source": "difference"},
        actor="test",
        source=SOURCE,
    )

    assert outcome["deleted_source"] == "difference"
    assert [row["record_id"] for row in outcome["deleted"]] == ["006A000003"]
    assert [row["record_id"] for row in outcome["written"]] == ["006A000001", "006A000002"]
    assert outcome["run"]["counts"] == {"written": 2, "deleted": 1}


def test_the_difference_is_sorted_and_set_valued(rules_module: Any = rules):
    """Set difference, so order on the way in cannot change the answer."""
    assert rules_module.deleted_by_difference(["b", "a", "c"], ["c", "a"]) == ["b"]
    assert rules_module.deleted_by_difference([], ["a"]) == []
    assert rules_module.deleted_by_difference(["a"], ["a"]) == []
    assert rules_module.deleted_by_difference(["a", "a"], []) == ["a"]


def test_the_recycle_bin_source_deletes_exactly_what_the_query_returned(engine: ReconcileEngine):
    """Pins 9, option b. "Query all records for the entity with isDeleted=true. You
    get all the soft-deleted records for that entity that are in the Recycle Bin." """
    source_row(engine, "006A000001")
    engine.put_source(
        ROOM,
        entity=ENTITY,
        record_id="006A000002",
        payload={},
        is_deleted=True,
        actor="test",
        source=SOURCE,
    )
    replica_row(engine, "006A000002")
    reported = seed_overflow(engine)

    outcome = engine.reconcile(
        ROOM,
        {"event_id": reported["event"]["id"], "entity": ENTITY, "deleted_source": "recycle_bin"},
        actor="test",
        source=SOURCE,
    )

    assert [row["record_id"] for row in outcome["deleted"]] == ["006A000002"]
    assert outcome["run"]["counts"] == {"written": 1, "deleted": 1}


def test_the_dataverse_source_reads_its_deletes_out_of_the_delta_response(
    engine: ReconcileEngine,
):
    """Pins 11. The delta response carries the deletions inline as $deletedEntity
    with reason deleted, so no delete diff is derived from it."""
    source_row(engine, "006A000001")
    engine.put_source(
        ROOM,
        entity=ENTITY,
        record_id="006A000004",
        payload={},
        is_deleted=True,
        actor="test",
        source=SOURCE,
    )
    replica_row(engine, "006A000004")
    engine.report_gap_event(ROOM, header("GAP_UPDATE"), actor="test", source=EVENT_SOURCE)
    reported = seed_overflow(engine, vendor=DATAV)

    outcome = engine.reconcile(
        ROOM,
        {
            "event_id": reported["event"]["id"],
            "entity": ENTITY,
            "deleted_source": "dataverse_delta",
        },
        actor="test",
        source=SOURCE,
    )

    assert outcome["deleted_source"] == "dataverse_delta"
    assert [row["record_id"] for row in outcome["deleted"]] == ["006A000004"]
    assert [row["record_id"] for row in outcome["written"]] == ["006A000001"]


def test_the_delta_page_reader_splits_live_rows_from_deletions():
    """The research's own example, read back verbatim."""
    page = {
        "@odata.deltaLink": "https://example.test/accounts/$delta?token=2",
        "value": [
            {"Id": "2e451703-a", "Name": "Northwind"},
            {
                "@odata.context": "https://example.test/api/data/v9.0/$metadata"
                "#accounts(name,telephone1,fax)/$delta",
                "id": "2e451703-c686-e711-80e5-00155db19e6d",
                "reason": "deleted",
            },
        ],
    }
    parsed = rules.delta_page(page)
    assert [row["Id"] for row in parsed["live"]] == ["2e451703-a"]
    assert parsed["deleted"] == ["2e451703-c686-e711-80e5-00155db19e6d"]
    assert parsed["delta_link"] == "https://example.test/accounts/$delta?token=2"


def test_a_delta_row_is_a_deletion_on_either_marker_alone():
    """The research's example carries both markers, and a room should not depend on
    a vendor sending only one."""
    by_reason = rules.delta_page({"value": [{"id": "a", "reason": "deleted"}]})
    assert by_reason["deleted"] == ["a"]
    by_context = rules.delta_page(
        {"value": [{"@odata.context": "https://example.test/accounts/$deletedEntity", "id": "b"}]}
    )
    assert by_context["deleted"] == ["b"]
    assert rules.delta_page(None) == {"live": [], "deleted": [], "delta_link": None}
    assert rules.delta_page({"value": "not a list"})["live"] == []


def test_the_stored_crm_reads_the_deletions_inline_from_its_own_tables(store, clock):
    """The reader a feature uses by default, answering with the researched shapes."""
    reader = StoredCrm(store)
    for record_id in ("006A000001", "006A000002"):
        store.create(
            "crm_gap_source_row",
            {
                "entity": ENTITY,
                "record_id": record_id,
                "is_deleted": False,
                "payload": {"Id": record_id},
            },
            room_id=ROOM,
            actor="test",
            source=SOURCE,
        )
    store.create(
        "crm_gap_source_row",
        {
            "entity": ENTITY,
            "record_id": "006A000003",
            "is_deleted": True,
            "payload": {"Id": "006A000003"},
        },
        room_id=ROOM,
        actor="test",
        source=SOURCE,
    )

    assert reader.entities(ROOM) == [ENTITY]
    assert [row["Id"] for row in reader.live_records(ROOM, ENTITY)] == ["006A000001", "006A000002"]
    assert [row["Id"] for row in reader.recycle_bin(ROOM, ENTITY)] == ["006A000003"]
    assert reader.record(ROOM, ENTITY, "006A000001") == {"Id": "006A000001"}
    assert reader.record(ROOM, ENTITY, "006A000003") is None, "a soft delete reads as gone"
    assert reader.record(ROOM, ENTITY, "absent") is None
    page = reader.delta(ROOM, ENTITY)
    assert page["@odata.deltaLink"] == f"dl:{ENTITY}:1"
    assert page["value"][-1]["reason"] == "deleted"
    assert page["value"][-1]["@odata.context"].endswith("$deletedEntity")
    assert reader.delta(ROOM, ENTITY, f"dl:{ENTITY}:1")["@odata.deltaLink"] == f"dl:{ENTITY}:2"


def test_a_deleted_source_the_research_does_not_describe_is_refused(engine: ReconcileEngine):
    reported = seed_overflow(engine)
    with pytest.raises(UnknownDeletedSource) as caught:
        engine.reconcile(
            ROOM,
            {"event_id": reported["event"]["id"], "entity": ENTITY, "deleted_source": "guesswork"},
            actor="test",
            source=SOURCE,
        )
    assert "difference" in str(caught.value)


def test_pairing_a_deleted_source_with_the_wrong_vendor_is_refused(engine: ReconcileEngine):
    """An empty answer read as "nothing was deleted" would delete nothing when the
    truth is that the room never asked the right question."""
    reported = seed_overflow(engine, vendor=DATAV)
    with pytest.raises(DeletedSourceVendorMismatch) as caught:
        engine.reconcile(
            ROOM,
            {
                "event_id": reported["event"]["id"],
                "entity": ENTITY,
                "deleted_source": "recycle_bin",
            },
            actor="test",
            source=SOURCE,
        )
    assert "salesforce" in str(caught.value)
    assert "never asked the right question" in str(caught.value)


def test_the_whole_entity_repair_clears_every_marker_on_that_entity(engine: ReconcileEngine):
    """A marker left open after a whole-entity read would ask for a second read of a
    record the run has just re-read in full."""
    engine.put_source(
        ROOM, entity=ENTITY, record_id="006A000001", payload={}, actor="test", source=SOURCE
    )
    for record_id in ("006A000001", "006A000002"):
        engine.report_gap_event(
            ROOM, header("GAP_UPDATE", recordIds=[record_id]), actor="test", source=EVENT_SOURCE
        )
    reported = seed_overflow(engine)

    outcome = engine.reconcile(
        ROOM,
        {"event_id": reported["event"]["id"], "entity": ENTITY, "deleted_source": "difference"},
        actor="test",
        source=SOURCE,
    )

    assert engine.dirty_records(ROOM) == []
    assert len(engine.dirty_records(ROOM, state="reconciled")) == 2
    assert outcome["run"]["state"] == "complete"


def test_the_overflow_run_logs_the_procedures_first_two_steps(engine: ReconcileEngine):
    """The research fixes the order: unsubscribe, then store the Replay ID, then
    reconcile. The log is where that order is visible."""
    source_row(engine, "006A000001")
    reported = seed_overflow(engine)
    outcome = engine.reconcile(
        ROOM,
        {"event_id": reported["event"]["id"], "entity": ENTITY, "deleted_source": "difference"},
        actor="test",
        source=SOURCE,
    )
    events = [row["event"] for row in outcome["run"]["log"]]
    assert events[:3] == ["run_opened", "unsubscribed", "replay_id_stored"]
    assert events[-1] == "run_complete"


# --------------------------------------------------------------------------- #
# Test plan rows 15 and 16: the seven-day deadline
# --------------------------------------------------------------------------- #


def test_the_researched_deadline_is_seven_days():
    assert vocab.default_expiry_days() == 7
    assert "seven days" in vocab.NUMBERS["change_tracking_expiry_days"]["quote"]
    assert vocab.CURSOR_SCOPE == {"replay_id": "entity", "delta_link": "org"}


def test_a_delta_link_past_seven_days_is_unresumable_and_names_the_fallback(
    engine: ReconcileEngine, clock: Clock
):
    """Pins 12. "Changes are returned if the last token is within a default value of
    seven days." """
    reported = seed_overflow(engine, vendor=DATAV)
    assert engine.cursors(ROOM)[0]["resumable"] is True

    clock.advance(days=8)
    verdict = engine.cursors(ROOM)[0]
    assert verdict["kind"] == "delta_link"
    assert verdict["resumable"] is False
    assert verdict["state"] == "expired"
    assert verdict["fallback"] == "full_reread"
    assert "throws" in verdict["reason"]
    assert verdict["age_days"] == 8.0
    assert reported["event"]["kind"] == "overflow"


def test_a_delta_link_inside_seven_days_is_still_resumable(engine: ReconcileEngine, clock: Clock):
    seed_overflow(engine, vendor=DATAV)
    clock.advance(days=6, hours=23)
    verdict = engine.cursors(ROOM)[0]
    assert verdict["resumable"] is True
    assert verdict["state"] == "resumable"
    assert verdict["age_days"] == 6.958


def test_a_replay_id_does_not_expire_even_after_seven_days(engine: ReconcileEngine, clock: Clock):
    """The seven-day sentence is about a Dataverse last token. Making the test
    kind-blind would declare a good Replay ID dead and make the overflow
    unrecoverable for no reason the vendor gave."""
    seed_overflow(engine)
    clock.advance(days=400)
    verdict = engine.cursors(ROOM)[0]
    assert verdict["kind"] == "replay_id"
    assert verdict["applies"] is False
    assert verdict["resumable"] is True
    assert verdict["age_days"] == 0.0


def test_a_reconcile_against_an_expired_delta_link_reads_nothing_and_writes_nothing(
    engine: ReconcileEngine, clock: Clock
):
    """Pins 12. The room refuses before it asks, so a run is never left half applied
    by a vendor exception discovered mid-transaction."""
    source_row(engine, "006A000001")
    replica_row(engine, "006A000009")
    reported = seed_overflow(engine, vendor=DATAV)
    before = engine.replica(ROOM)
    clock.advance(days=9)

    outcome = engine.reconcile(
        ROOM,
        {
            "event_id": reported["event"]["id"],
            "entity": ENTITY,
            "deleted_source": "dataverse_delta",
        },
        actor="test",
        source=SOURCE,
    )

    assert outcome["run"]["state"] == "expired_cursor"
    assert outcome["fallback"] == "full_reread"
    assert outcome["written"] == []
    assert outcome["deleted"] == []
    assert engine.replica(ROOM) == before, "an expired cursor writes nothing"
    events = [row["event"] for row in outcome["run"]["log"]]
    assert events == [
        "run_opened",
        "unsubscribed",
        "replay_id_stored",
        "delta_link_expired",
        "run_expired_cursor",
    ]


def test_a_cursor_verdict_for_nothing_stored_says_so():
    verdict = rules.cursor_verdict(None, NOW)
    assert verdict["present"] is False
    assert verdict["kind"] == "none"
    assert verdict["state"] == "absent"
    assert verdict["resumable"] is False
    assert verdict["fallback"] == "full_reread"


def test_the_expiry_helper_is_the_same_verdict():
    record = {
        "kind": "delta_link",
        "cursor": "dl:1",
        "updatedAt": (NOW - timedelta(days=8)).isoformat(),
    }
    assert rules.is_expired(record, NOW) is True
    assert rules.is_expired(record, NOW, expiry_days=30) is False
    assert rules.is_expired(None, NOW) is False
    assert (
        rules.is_expired(
            {"kind": "replay_id", "cursor": "r", "updatedAt": record["updatedAt"]}, NOW
        )
        is False
    )


def test_a_room_holding_a_replay_id_and_a_delta_link_reports_both(engine: ReconcileEngine):
    """The two are not the same shape and a room may hold both at once, which is
    exactly what a single cursor field would have to lie about."""
    seed_overflow(engine, vendor=VENDOR)
    seed_overflow(engine, vendor=DATAV)
    cursors = engine.cursors(ROOM)
    assert [row["kind"] for row in cursors] == ["delta_link", "replay_id"]
    assert [row["scope"] for row in cursors] == ["org", "entity"]
    assert all(row["present"] for row in cursors)
    assert all(row["resumable"] for row in cursors)
    assert [row["vendor"] for row in cursors] == ["dataverse", "salesforce"]


def test_pairing_a_stored_cursor_with_the_wrong_vendor_is_refused(engine: ReconcileEngine):
    """A Salesforce Replay ID cannot resume a Dataverse poll, and letting a caller
    pair them would skip or repeat every row in between."""
    seed_overflow(engine, vendor=DATAV, replayId="replay-9")
    with pytest.raises(MismatchedCursorVendor) as caught:
        engine._cursor_for(ROOM, VENDOR, ENTITY)
    assert "dataverse" in str(caught.value)
    assert "replay_id" in str(caught.value)

    # The refusal is not a hunt for a cursor that exists: the room's own one answers.
    assert engine._cursor_for(ROOM, DATAV, ENTITY)["data"]["kind"] == "delta_link"


# --------------------------------------------------------------------------- #
# Test plan row 17: the resubscription
# --------------------------------------------------------------------------- #


def test_resubscribing_records_the_resubscription_and_closes_the_open_overflow_run(
    engine: ReconcileEngine,
):
    """Pins 10. "Room resubscribes and records a reconciliation event in the sync
    log for audit." """
    source_row(engine, "006A000001")
    reported = seed_overflow(engine)
    outcome = engine.reconcile(
        ROOM,
        {"event_id": reported["event"]["id"], "entity": ENTITY, "deleted_source": "difference"},
        actor="test",
        source=SOURCE,
    )

    answer = engine.subscribe(ROOM, {"entity": ENTITY}, actor="test", source=SOURCE)

    assert answer["subscription_state"] == "subscribed"
    assert answer["resubscribed_at"]
    annotated = {row["id"] for row in answer["runs_annotated"]}
    assert outcome["run"]["id"] in annotated
    assert engine.run(ROOM, outcome["run"]["id"])["state"] == "complete"
    assert engine.gap_event(ROOM, reported["event"]["id"])["subscription_state"] == "subscribed"
    assert engine.gap_event(ROOM, reported["event"]["id"])["resubscribed_at"]
    events = [row["event"] for row in engine.run_log(ROOM, outcome["run"]["id"])]
    assert events[-1] == "resubscribed", "the resubscription is the last line of the audit trail"


def test_a_full_reread_leaves_a_resumable_position_behind(engine: ReconcileEngine):
    """A Replay ID is "the starting point for the data reconciliation", so the
    position a room reconciles from is the one it must store."""
    answer = engine.subscribe(ROOM, {"entity": ENTITY}, actor="test", source=SOURCE)
    stored = answer["cursor"]
    assert stored["kind"] == "replay_id"
    assert stored["position"].startswith("reconciled:")
    assert stored["resumable"] is True


def test_resubscribing_keeps_a_replay_id_the_overflow_already_stored(engine: ReconcileEngine):
    seed_overflow(engine, replayId="replay-7")
    answer = engine.subscribe(ROOM, {"entity": ENTITY}, actor="test", source=SOURCE)
    assert answer["cursor"]["position"] == "replay-7"


# --------------------------------------------------------------------------- #
# Test plan row 18: the log order
# --------------------------------------------------------------------------- #


def test_every_log_line_carries_a_gapless_one_based_number(engine: ReconcileEngine):
    """SQLite breaks a tie on updated_at by insertion, so a log ordered by the
    database fails intermittently under parallel runs. The number is the order."""
    source_row(engine, "006A000001")
    engine.purge_source(ROOM, entity=ENTITY, record_id="absent", actor="test", source=SOURCE)
    reported = seed_overflow(engine)
    outcome = engine.reconcile(
        ROOM,
        {"event_id": reported["event"]["id"], "entity": ENTITY, "deleted_source": "difference"},
        actor="test",
        source=SOURCE,
    )

    numbers = [row["seq"] for row in outcome["run"]["log"]]
    assert numbers == sorted(numbers), f"the line numbers are out of order: {numbers}"
    assert numbers == list(range(1, len(numbers) + 1)), (
        f"the numbers are not consecutive: {numbers}"
    )
    assert all(row["run_id"] == outcome["run"]["id"] for row in outcome["run"]["log"])
    assert all(row["at"] for row in outcome["run"]["log"])
    assert all(row["detail"] for row in outcome["run"]["log"])


def test_a_per_record_run_numbers_its_own_lines_from_one(engine: ReconcileEngine):
    source_row(engine, "006A000001")
    reported = engine.report_gap_event(
        ROOM, header("GAP_UPDATE"), actor="test", source=EVENT_SOURCE
    )
    outcome = engine.reconcile(
        ROOM,
        {"event_id": reported["event"]["id"], "record_id": "006A000001"},
        actor="test",
        source=SOURCE,
    )
    assert [row["seq"] for row in outcome["run"]["log"]] == [1, 2, 3, 4, 5]
    assert [row["event"] for row in outcome["run"]["log"]] == [
        "run_opened",
        "record_read",
        "replica_overwritten",
        "dirty_flag_cleared",
        "run_complete",
    ]


def test_two_runs_number_their_lines_independently(engine: ReconcileEngine):
    source_row(engine, "006A000001")
    source_row(engine, "006A000002")
    first = engine.report_gap_event(
        ROOM, header("GAP_UPDATE", recordIds=["006A000001"]), actor="test", source=EVENT_SOURCE
    )
    second = engine.report_gap_event(
        ROOM, header("GAP_UPDATE", recordIds=["006A000002"]), actor="test", source=EVENT_SOURCE
    )
    run_one = engine.reconcile(
        ROOM,
        {"event_id": first["event"]["id"], "record_id": "006A000001"},
        actor="test",
        source=SOURCE,
    )
    run_two = engine.reconcile(
        ROOM,
        {"event_id": second["event"]["id"], "record_id": "006A000002"},
        actor="test",
        source=SOURCE,
    )
    assert run_one["run"]["id"] != run_two["run"]["id"]
    for run in (run_one, run_two):
        numbers = [row["seq"] for row in run["run"]["log"]]
        assert numbers == list(range(1, len(numbers) + 1))


def test_the_log_read_itself_is_ordered_by_the_number_not_by_the_database(
    engine: ReconcileEngine,
):
    source_row(engine, "006A000001")
    reported = seed_overflow(engine)
    outcome = engine.reconcile(
        ROOM,
        {"event_id": reported["event"]["id"], "entity": ENTITY, "deleted_source": "difference"},
        actor="test",
        source=SOURCE,
    )
    lines = engine.run_log(ROOM, outcome["run"]["id"])
    assert [row["seq"] for row in lines] == [1, 2, 3, 4, 5, 6]
    assert [row["event"] for row in lines] == [
        "run_opened",
        "unsubscribed",
        "replay_id_stored",
        "record_read",
        "rows_written",
        "run_complete",
    ]
    assert engine.run(ROOM, outcome["run"]["id"])["log"] == lines


def test_reading_the_log_of_a_run_in_another_room_is_refused(engine: ReconcileEngine):
    source_row(engine, "006A000001")
    reported = seed_overflow(engine)
    outcome = engine.reconcile(
        ROOM,
        {"event_id": reported["event"]["id"], "entity": ENTITY, "deleted_source": "difference"},
        actor="test",
        source=SOURCE,
    )
    with pytest.raises(UnknownRun):
        engine.run_log(OTHER_ROOM, outcome["run"]["id"])
    with pytest.raises(UnknownRun):
        engine.run(OTHER_ROOM, outcome["run"]["id"])
    with pytest.raises(UnknownRun):
        engine.run(ROOM, "run_absent")


# --------------------------------------------------------------------------- #
# The data-health view
# --------------------------------------------------------------------------- #


def test_the_health_view_counts_dirty_records_and_names_the_rooms_state(
    engine: ReconcileEngine,
):
    source_row(engine, "006A000001")
    reported = seed_overflow(engine)
    engine.reconcile(
        ROOM,
        {"event_id": reported["event"]["id"], "entity": ENTITY, "deleted_source": "difference"},
        actor="test",
        source=SOURCE,
    )
    engine.report_gap_event(ROOM, header("GAP_UPDATE"), actor="test", source=EVENT_SOURCE)

    health = engine.health(ROOM)
    assert health["room_id"] == ROOM
    assert health["dirty_count"] == 1
    assert health["clean"] is False
    assert "1 record(s) are dirty" in health["summary"]
    assert health["gap_event_count"] == 2
    assert health["open_run_count"] == 0
    assert health["cursors"][0]["kind"] == "replay_id"
    assert health["unresumable_cursors"] == []
    assert health["dirty_records"][0]["record_id"] == "006A000001"


def test_a_clean_room_says_so_in_words(engine: ReconcileEngine):
    health = engine.health(ROOM)
    assert health["clean"] is True
    assert health["dirty_count"] == 0
    assert "no record is dirty" in health["summary"]


def test_the_health_view_lists_the_unresumable_cursors(engine: ReconcileEngine, clock: Clock):
    seed_overflow(engine, vendor=DATAV)
    clock.advance(days=8)
    health = engine.health(ROOM)
    assert health["unresumable_cursors"] == ["delta_link"]


def test_the_dirty_list_is_ordered_by_the_gaps_own_date(engine: ReconcileEngine):
    """The question an operator asks is which gap happened first, so the order is
    the gap's commit timestamp and not the order the rows were written in."""
    engine.report_gap_event(
        ROOM,
        header("GAP_UPDATE", recordIds=["006A000003"], commitTimestamp="2026-09-27T11:00:00+00:00"),
        actor="test",
        source=EVENT_SOURCE,
    )
    engine.report_gap_event(
        ROOM,
        header("GAP_UPDATE", recordIds=["006A000001"], commitTimestamp="2026-09-27T09:00:00+00:00"),
        actor="test",
        source=EVENT_SOURCE,
    )
    engine.report_gap_event(
        ROOM,
        header("GAP_UPDATE", recordIds=["006A000002"], commitTimestamp="2026-09-27T10:00:00+00:00"),
        actor="test",
        source=EVENT_SOURCE,
    )
    assert [row["record_id"] for row in engine.dirty_records(ROOM)] == [
        "006A000001",
        "006A000002",
        "006A000003",
    ]


def test_the_dirty_view_reports_how_long_a_record_has_been_dirty(engine: ReconcileEngine):
    engine.report_gap_event(
        ROOM,
        header("GAP_UPDATE", commitTimestamp=(NOW - timedelta(days=3)).isoformat()),
        actor="test",
        source=EVENT_SOURCE,
    )
    assert engine.dirty_records(ROOM)[0]["age_note"] == "dirty for 3d"

    engine.report_gap_event(
        ROOM,
        header(
            "GAP_CREATE",
            recordIds=["006A000002"],
            commitTimestamp=(NOW - timedelta(hours=4)).isoformat(),
        ),
        actor="test",
        source=EVENT_SOURCE,
    )
    assert [row["age_note"] for row in engine.dirty_records(ROOM)] == [
        "dirty for 3d",
        "dirty for 4h",
    ]


def test_the_dirty_view_refuses_a_state_the_research_does_not_describe(
    engine: ReconcileEngine,
):
    with pytest.raises(UnknownEntity) as caught:
        engine.dirty_records(ROOM, state="confused")
    assert "dirty" in str(caught.value)


def test_a_marker_with_an_unreadable_date_reports_no_age_rather_than_raising(
    engine: ReconcileEngine,
):
    engine.report_gap_event(ROOM, header("GAP_UPDATE"), actor="test", source=EVENT_SOURCE)
    marker = engine.store.list("crm_gap_dirty", room_id=ROOM)[0]
    engine.store.update(
        marker["id"], {"gap_commit_timestamp": "not a date"}, actor="test", source=SOURCE
    )
    assert engine.dirty_records(ROOM)[0]["age_note"] == ""


def test_the_ledger_and_the_runs_are_newest_first(engine: ReconcileEngine):
    source_row(engine, "006A000001")
    first = engine.report_gap_event(ROOM, header("GAP_UPDATE"), actor="test", source=EVENT_SOURCE)
    engine.reconcile(
        ROOM,
        {"event_id": first["event"]["id"], "record_id": "006A000001"},
        actor="test",
        source=SOURCE,
    )
    second = seed_overflow(engine)
    engine.reconcile(
        ROOM,
        {"event_id": second["event"]["id"], "entity": ENTITY, "deleted_source": "difference"},
        actor="test",
        source=SOURCE,
    )
    events = engine.gap_events(ROOM)
    assert [row["id"] for row in events] == [second["event"]["id"], first["event"]["id"]]
    runs = engine.runs(ROOM)
    assert [row["kind"] for row in runs] == ["overflow", "gap"]
    assert all(row["terminal"] for row in runs)
    assert all(row["state"] in vocab.RUN_STATES for row in runs)
    assert all(row["entity"] == ENTITY for row in runs)


def test_reading_an_event_from_another_room_is_refused(engine: ReconcileEngine):
    reported = engine.report_gap_event(
        ROOM, header("GAP_UPDATE"), actor="test", source=EVENT_SOURCE
    )
    with pytest.raises(UnknownEvent):
        engine.gap_event(OTHER_ROOM, reported["event"]["id"])
    with pytest.raises(UnknownEvent):
        engine.gap_event(ROOM, "gap_absent")


def test_the_replica_view_filters_by_entity_and_by_deletion(engine: ReconcileEngine):
    replica_row(engine, "006A000001")
    replica_row(engine, "006A000002", deleted=True)
    engine.store.create(
        "crm_gap_replica",
        {"entity": "Contact", "record_id": "003A000001", "deleted": False, "payload": {}},
        room_id=ROOM,
        actor="test",
        source=SOURCE,
    )
    assert len(engine.replica(ROOM)) == 3
    assert len(engine.replica(ROOM, entity=ENTITY)) == 2
    assert [row["record_id"] for row in engine.replica(ROOM, include_deleted=False)] == [
        "003A000001",
        "006A000001",
    ]


# --------------------------------------------------------------------------- #
# The room's copy of the vendor's tables
# --------------------------------------------------------------------------- #


def test_a_row_can_be_moved_to_the_recycle_bin_and_taken_out_of_it(
    engine: ReconcileEngine,
):
    engine.put_source(
        ROOM, entity=ENTITY, record_id="006A000001", payload={}, actor="test", source=SOURCE
    )
    assert engine.reader.record(ROOM, ENTITY, "006A000001") is not None

    engine.remove_source(ROOM, entity=ENTITY, record_id="006A000001", actor="test", source=SOURCE)
    assert engine.reader.record(ROOM, ENTITY, "006A000001") is None
    assert len(engine.reader.recycle_bin(ROOM, ENTITY)) == 1

    engine.put_source(
        ROOM, entity=ENTITY, record_id="006A000001", payload={}, actor="test", source=SOURCE
    )
    assert engine.reader.record(ROOM, ENTITY, "006A000001") is not None

    engine.purge_source(ROOM, entity=ENTITY, record_id="006A000001", actor="test", source=SOURCE)
    assert engine.reader.record(ROOM, ENTITY, "006A000001") is None
    assert engine.reader.recycle_bin(ROOM, ENTITY) == []


def test_operating_on_a_row_that_is_not_there_reports_nothing(engine: ReconcileEngine):
    assert (
        engine.remove_source(ROOM, entity=ENTITY, record_id="absent", actor="test", source=SOURCE)
        is None
    )
    assert (
        engine.purge_source(ROOM, entity=ENTITY, record_id="absent", actor="test", source=SOURCE)
        is None
    )
    assert engine.reader.entities(ROOM) == []


def test_the_in_memory_reader_records_which_question_each_repair_asked():
    """A repair that silently read the wrong thing is the failure this recovery path
    cannot afford, so the reader keeps its own call log."""
    reader = SimulatedCrm({ENTITY: {"006A000001": {"Name": "Northwind"}}})
    engine = ReconcileEngine(RecordStore(AuditedDatabase()), reader=reader, clock=Clock())
    assert engine.reader.entities(ROOM) == [ENTITY]
    assert reader.record(ROOM, ENTITY, "006A000001") == {"Name": "Northwind"}
    assert reader.calls == [("entities", (ROOM,)), ("record", (ENTITY, "006A000001"))]
    reader.delete(ENTITY, "006A000001")
    assert reader.record(ROOM, ENTITY, "006A000001") is None
    assert len(reader.recycle_bin(ROOM, ENTITY)) == 1
    assert reader.purge(ENTITY, "006A000001") is True
    assert reader.purge(ENTITY, "006A000001") is False


# --------------------------------------------------------------------------- #
# The served data
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_serves_everything_the_api_will_accept(engine: ReconcileEngine):
    served = engine.vocabulary()
    assert served["gap_change_types"] == [
        "GAP_CREATE",
        "GAP_UPDATE",
        "GAP_DELETE",
        "GAP_UNDELETE",
    ]
    assert served["overflow_change_type"] == "GAP_OVERFLOW"
    assert served["vendors"] == ["salesforce", "dataverse"]
    assert served["unsupported_vendors"] == ["hubspot"]
    assert served["dirty_states"] == ["dirty", "reconciled"]
    assert served["dirty_key"] == ["room_id", "entity", "record_id"]
    assert served["last_modified_field"] == "LastModifiedDate"
    assert [row["value"] for row in served["run_states"]] == list(vocab.RUN_STATES)
    assert [row["value"] for row in served["run_states"] if row["terminal"]] == [
        "complete",
        "expired_cursor",
        "refused",
    ]
    assert [row["kind"] for row in served["cursor_kinds"]] == ["replay_id", "delta_link"]
    assert [row["value"] for row in served["deleted_sources"]] == [
        "difference",
        "recycle_bin",
        "dataverse_delta",
    ]
    assert served["numbers"]["overflow_change_threshold"]["value"] == 100_000
    assert served["numbers"]["change_tracking_expiry_days"]["value"] == 7
    assert "repair" in served["classification"]
    assert set(vocab.LOG_EVENTS) <= set(served["log_events"])


def test_the_inferences_route_separates_the_sourced_from_the_decided(
    engine: ReconcileEngine,
):
    served = engine.inferences()
    assert served["count"] == len(served["inferred"]) >= 6
    assert served["ids"] == [row["id"] for row in served["inferred"]]
    assert "GAP_UNDELETE" in served["sourced"]["gap_types"]
    assert "100,000 changes" in served["sourced"]["overflow"]
    assert "no record ID" in served["sourced"]["record_id_difference"]
    assert "don't process them" in served["sourced"]["drop_and_order"]
    assert "clear the dirty flag" in served["sourced"]["repair"]
    assert "seven days" in served["sourced"]["deadline"]
    assert "not reconciled in a single source" in served["research_gap"]

    decisions = {row["question"].split("?")[0]: row for row in served["inferred"]}
    vendors = next(row for row in served["inferred"] if row["chosen"].startswith("no."))
    assert "no documented gap/overflow analogue" in vendors["because"]
    assert vendors["source"].endswith("gaps")
    for row in served["inferred"]:
        assert row["id"] and row["decision"] and row["chosen"] and row["because"] and row["source"]
    assert decisions  # the questions are all present and non-empty


def test_an_inference_can_be_looked_up_by_its_slug():
    from dsr.crm_integration import gap_inferences

    assert gap_inferences.ids() == (
        "dirty-marker-key",
        "two-named-cursors",
        "expired-cursor-fallback",
        "vendor-scope",
        "local-vendor-tables",
        "replica-ownership",
    )
    entry = gap_inferences.by_id("vendor-scope")
    assert entry is not None
    assert entry["chosen"].startswith("no.")
    assert gap_inferences.by_id("a-slug-nobody-used") is None


# --------------------------------------------------------------------------- #
# The audit contract
# --------------------------------------------------------------------------- #


def test_every_writing_method_requires_a_source_as_a_keyword_with_no_default():
    """A hardcoded path inside a domain method is a defect this makes impossible."""
    import inspect

    writers = (
        "report_gap_event",
        "change_event",
        "reconcile",
        "subscribe",
        "put_source",
        "remove_source",
        "purge_source",
    )
    for name in writers:
        parameters = inspect.signature(getattr(ReconcileEngine, name)).parameters
        assert parameters.get("source") is not None, f"{name} does not require source="
        assert parameters["source"].default is inspect.Parameter.empty, f"{name} defaults source="
        assert parameters["source"].kind is inspect.Parameter.KEYWORD_ONLY, name


def test_the_writes_reach_the_audit_log_with_the_room_they_belong_to(engine: ReconcileEngine):
    engine.put_source(
        ROOM, entity=ENTITY, record_id="006A000001", payload={}, actor="test", source=SOURCE
    )
    reported = engine.report_gap_event(
        ROOM, header("GAP_UPDATE"), actor="test", source=EVENT_SOURCE
    )
    engine.reconcile(
        ROOM,
        {"event_id": reported["event"]["id"], "record_id": "006A000001"},
        actor="test",
        source=SOURCE,
    )
    engine.change_event(
        ROOM,
        {"entity": ENTITY, "record_id": "006A000001", "commit_timestamp": NOW.isoformat()},
        actor="test",
        source=CHANGE_SOURCE,
    )

    entries = engine.store.audit(limit=500)
    mine = [row for row in entries if PREFIX in str(row.get("source") or "")]
    assert mine, "no audit row from this feature was written"
    assert {row["source"] for row in mine} <= {
        f"POST {PREFIX}/rooms/{{room_id}}/gap-events",
        f"POST {PREFIX}/rooms/{{room_id}}/change-events",
        f"POST {PREFIX}/rooms/{{room_id}}/reconcile",
        f"POST {PREFIX}/rooms/{{room_id}}/subscribe",
    }
    assert all(row["room_id"] == ROOM for row in mine)
    assert all(row["actor"] for row in mine)
    assert all(row["action"] == "insert" or row["action"] == "update" for row in mine)


def test_a_drop_writes_no_audit_row_at_all(engine: ReconcileEngine):
    """A dropped change changes nothing, so there is nothing to audit."""
    engine.report_gap_event(ROOM, header("GAP_UPDATE"), actor="test", source=EVENT_SOURCE)
    engine.change_event(
        ROOM,
        {"entity": ENTITY, "record_id": "006A000001", "commit_timestamp": NOW.isoformat()},
        actor="test",
        source=CHANGE_SOURCE,
    )
    sources = [row["source"] for row in engine.store.audit(limit=500)]
    assert CHANGE_SOURCE not in sources


def test_the_feature_added_no_table_and_no_typed_column(store):
    """Records are ordinary JSON in records.data. A migration or a typed column for a
    team's field would need coordination with everyone."""
    before = {
        row["name"]
        for row in store.db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    ReconcileEngine(store).vocabulary()
    after = {
        row["name"]
        for row in store.db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert before == after


def test_every_collection_this_feature_owns_is_namespaced_to_it():
    from dsr.crm_integration.reconcile_engine import OWNED_COLLECTIONS

    assert OWNED_COLLECTIONS == (
        "crm_gap_event",
        "crm_gap_dirty",
        "crm_gap_cursor",
        "crm_gap_run",
        "crm_gap_log",
        "crm_gap_replica",
        "crm_gap_source_row",
    )
    assert all(name.startswith("crm_gap_") for name in OWNED_COLLECTIONS)


def test_the_whole_hierarchy_hangs_off_one_base_class():
    """Two features may not map the same exception type, and the host refuses the
    second. WF-042's package already claims its own base, so this one is new."""
    import dsr.crm_integration.gap_errors as module

    classes = [
        value
        for value in vars(module).values()
        if isinstance(value, type) and issubclass(value, GapReconcileError)
    ]
    assert len(classes) >= 15
    for cls in classes:
        assert cls.code, f"{cls.__name__} carries no code"
        assert isinstance(cls.status, int)
        assert 400 <= cls.status <= 409


# --------------------------------------------------------------------------- #
# The architecture the contract requires
# --------------------------------------------------------------------------- #


def test_the_domain_package_depends_on_nothing_but_the_store():
    """Read off the package on disk rather than off a claim."""
    package = Path(ReconcileEngine.__module__.replace(".", "/")).name
    root = Path(__file__).resolve().parents[1] / "dsr" / "crm_integration"
    modules = [
        "gap_errors.py",
        "gap_inferences.py",
        "gap_sources.py",
        "gap_vocabulary.py",
        "reconcile.py",
        "reconcile_engine.py",
    ]
    assert package
    for name in modules:
        text = (root / name).read_text(encoding="utf-8")
        assert "import sqlite3" not in text, name
        assert "sqlite3.connect" not in text, name
        assert "from dsr.api" not in text, name
        assert "dsr.deps" not in text, name
        assert "fastapi" not in text, name
        assert "AuditedDatabase(" not in text, name


def test_the_feature_module_reaches_no_shared_file_and_opens_no_connection():
    module = load_feature(MODULE)
    text = Path(module.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in text
    assert "import dsr.api" not in text
    assert "import sqlite3" not in text
    assert "sqlite3.connect" not in text
    assert "from dsr.deps import" in text, "dependencies come from dsr.deps, never dsr.api"


def test_every_source_the_module_writes_is_built_from_the_live_prefix():
    """A hardcoded path would drift the moment the prefix changed."""
    import re

    module = load_feature(MODULE)
    text = Path(module.__file__).read_text(encoding="utf-8")
    expressions = re.findall(r'source=f"([^"]*)"', text)
    assert expressions, "no source is written with an f-string"
    for expression in expressions:
        assert "{router.prefix}" in expression, expression
    served = {
        (method, route.path)
        for route in module.router.routes
        for method in route.methods
        if method in {"POST", "PATCH", "PUT", "DELETE"}
    }
    # The source is written with doubled braces so an f-string renders them single,
    # which is the shape a mounted route reports. Collapse before comparing.
    recorded = {
        (
            expression.split(" ", 1)[0],
            f"{PREFIX}{expression.split('{router.prefix}')[1]}".replace("{{", "{").replace(
                "}}", "}"
            ),
        )
        for expression in expressions
    }
    assert recorded == served, f"the sources and the write routes differ: {recorded} vs {served}"


def test_the_feature_declares_the_prefix_the_contract_names():
    module = load_feature(MODULE)
    assert module.router.prefix == PREFIX
    assert module.FEATURE["ticket"] == "WF-050"
    assert module.FEATURE["id"] == FEATURE_ID
    assert module.FEATURE["name"]
    assert module.FEATURE["description"]


def test_the_frontend_descriptor_id_matches_the_backend_feature_id():
    descriptor = Path(__file__).resolve().parents[2] / "frontend" / "src" / "features" / FEATURE_ID
    text = (descriptor / "index.jsx").read_text(encoding="utf-8")
    module = load_feature(MODULE)
    assert module.FEATURE["id"] in text
    assert f"id: {module.FEATURE['id']!r}" in text
    assert "Component:" in text
    assert "iconPath:" in text


def test_the_seeder_returns_a_string_a_windows_console_can_print(tmp_path):
    """A rightwards arrow in one recovered feature broke the entire seeder."""
    module = load_feature(MODULE)
    db = AuditedDatabase(tmp_path / "seed.db", actor="seed")
    try:
        store = RecordStore(db)
        store.create("room", {"name": "Demo"}, room_id="room-seed", actor="seed", source="seed")
        store.create("room", {"name": "Other"}, room_id="room-seed-2", actor="seed", source="seed")
        reported = module.seed(
            db, {"room_ids": [("room-seed", "northwind"), ("room-seed-2", "contoso")]}
        )
    finally:
        db.close()

    assert isinstance(reported, str) and reported
    reported.encode("cp1252")
    print(reported)
    assert "dirty" in reported
    assert "tombstone" in reported
    assert "overflow" in reported


def test_the_seeder_says_so_when_there_are_no_rooms(tmp_path):
    module = load_feature(MODULE)
    db = AuditedDatabase(tmp_path / "seed-empty.db", actor="seed")
    try:
        reported = module.seed(db, {"room_ids": []})
    finally:
        db.close()
    reported.encode("cp1252")
    assert "no rooms to scope them to" in reported


def test_the_seeded_state_is_reviewable_and_not_all_successes(tmp_path):
    """A feature whose demo page is empty is a feature nobody can review."""
    import random
    from datetime import datetime as _dt

    module = load_feature(MODULE)
    db = AuditedDatabase(tmp_path / "seed.db", actor="seed")
    try:
        room = db.create("room", {"name": "Demo", "account": "Northwind"}, source="seed")
        module.seed(
            db,
            {
                "room_ids": [(room["id"], "Northwind")],
                "now": _dt(2026, 9, 27, 12, 0, tzinfo=timezone.utc),
                "rng": random.Random(1),
            },
        )
        engine = ReconcileEngine(RecordStore(db), clock=Clock())
        room_id = room["id"]
        health = engine.health(room_id)
        assert health["dirty_count"] >= 1, "the data-health view needs a row to show"
        assert health["gap_event_count"] == 4
        assert health["open_run_count"] == 0
        kinds = [row["kind"] for row in engine.gap_events(room_id)]
        assert kinds.count("overflow") == 1
        assert kinds.count("gap") == 3
        assert any(row["deleted"] for row in engine.replica(room_id)), "the tombstone must show"
        unsubscribed = [
            row for row in engine.gap_events(room_id) if row["subscription_state"] == "unsubscribed"
        ]
        assert len(unsubscribed) == 1, "the overflow must leave the room unsubscribed"
        assert engine.cursors(room_id)[0]["position"].startswith("seed-replay-")
        for run in engine.runs(room_id):
            numbers = [row["seq"] for row in engine.run_log(room_id, run["id"])]
            assert numbers == list(range(1, len(numbers) + 1))
    finally:
        db.close()
