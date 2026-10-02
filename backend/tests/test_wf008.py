"""Tests for WF-008: the external content library service, and the port itself.

The ported branch shipped two suites, ``test_library_api.py`` (the wire contract)
and ``test_library_sync.py`` (the service). They stay two files: the wire contract
lives in ``test_wf008_http.py``, because its fixtures build data through a
running app while these build it through a database the test opened, and a
shared fixture name across one module would let one silently answer for the
other.

The domain module is unchanged apart from one fix, so the branch's behavioural
tests carry over as they were, with the ``source=`` argument the write methods now
require. What is new here is the port, which is the part the branch's tests
could not have covered:

* the feature is mounted by discovery, with no edit to a shared file;
* the audit ``source`` is derived from ``router.prefix``, so it cannot name a
  path the app does not serve -- the defect the branch shipped, and
  :func:`test_the_domain_module_hard_codes_no_route` is what keeps it fixed;
* the service is built from ``StoreDep`` rather than from app state, so nothing
  in ``dsr/api.py`` has to learn this feature exists;
* ``seed(db, context)`` populates the demo without ``backend/seed.py``.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

# Importing the app is what runs ``load_features``. The mounting assertions below
# are about the host's discovery, so the host has to have run; relying on some
# other test file to have imported this module first would make them pass or fail
# depending on the order pytest collected in, which is how a green suite hides a
# broken mount.
import dsr.api  # noqa: F401
import pytest
from dsr.db.audited import AuditedDatabase
from dsr.external_library.errors import ExternalSyncError
from dsr.external_library.ratelimit import RateLimiter
from dsr.external_library.sources import (
    GOOGLE_DRIVE,
    SAMPLE_FILE_ID,
    STALE_FILE_ID,
    SimulatedDrive,
    SourceFile,
    register_source,
    source_registry,
    supported_sources,
    unregister_source,
)
from dsr.external_library.sync import IN_SYNC, LINKED, ORPHANED, ROOT, SNAPSHOT, ExternalLibrarySync
from dsr.features import REGISTRY, load_feature
from dsr.features.wf008_external_sync import seed as feature_seed
from dsr.store import RecordStore

SOURCE = GOOGLE_DRIVE

#: What the service is handed as the audit ``source`` by a direct caller. The
#: HTTP layer builds the same string from ``router.prefix``; the two suites
#: between them assert that the routes actually produce it.
ADD_SOURCE = "POST /api/library/external"
RESYNC_SOURCE = "POST /api/library/external/resync"


def seed_context(rooms):
    """What the seeder hands a feature: rooms, a clock, and a per-feature rng."""
    return {
        "room_ids": rooms,
        "now": datetime(2026, 9, 26, 9, 0, tzinfo=timezone.utc),
        "rng": None,
    }


class FakeClock:
    """A clock the tests move by hand, so no test ever sleeps."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --------------------------------------------------------------------------- #
# The service, over a temporary database
# --------------------------------------------------------------------------- #


@pytest.fixture()
def db(tmp_path):
    database = AuditedDatabase(tmp_path / "wf008.db", mirror_dir=tmp_path / "audit", actor="test")
    yield database
    database.close()


@pytest.fixture()
def store(db):
    return RecordStore(db)


@pytest.fixture()
def drive():
    return SimulatedDrive()


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def library(store, drive, clock):
    return ExternalLibrarySync(
        store, adapters={GOOGLE_DRIVE: drive}, limiter=RateLimiter(clock=clock)
    )


@pytest.fixture()
def room(store):
    return store.create("room", {"name": "Northwind", "stage": "evaluation"}, actor="dana")


@pytest.fixture()
def connection(store):
    return store.create(
        "external_connection",
        {
            "source": GOOGLE_DRIVE,
            "name": "Dana's Google Drive",
            "account": "dana@northwind.example",
            "status": "connected",
        },
        actor="dana",
    )


def add(library, room, **overrides):
    """Call the add operation with sensible defaults for the fixtures.

    The throttle is cleared first so each case starts from a full window. The
    tests that are *about* the throttle call ``library.add`` directly, because a
    helper that quietly resets the limiter would hide the very thing it claims
    to exercise.
    """
    library.limiter.reset()
    kwargs = {
        "external_source": GOOGLE_DRIVE,
        "external_content_id": SAMPLE_FILE_ID,
        "room_id": room["id"],
        "actor": "dana",
        "token": "dana",
        "auto_sync": True,
        "source": ADD_SOURCE,
    }
    kwargs.update(overrides)
    return library.add(**kwargs)


# -- the add operation ------------------------------------------------------ #


def test_add_creates_a_linked_item_not_a_copy(library, room, connection, db):
    result = add(library, room)

    item = db.require(result["content_id"])
    assert item["collection"] == "document"
    assert item["room_id"] == room["id"]
    # Linked means the bytes stay in the source: no body is stored, only a
    # pointer plus the metadata the source reported.
    assert "body" not in item["data"]
    assert item["data"]["source"]["external_content_id"] == SAMPLE_FILE_ID


def test_add_returns_a_content_id_that_resolves(library, room, connection):
    result = add(library, room)

    assert result["content_id"].startswith("document_")
    assert result["item"]["id"] == result["content_id"]
    # The research says to persist the returned contentId, so it is stored on
    # the item too and can be found by it.
    assert result["item"]["data"]["source"]["content_id"] == result["content_id"]


def test_add_keeps_the_researched_field_names(library, room, connection):
    source = add(library, room, auto_sync=True)["item"]["data"]["source"]

    assert source["external_source"] == GOOGLE_DRIVE
    assert source["external_content_id"] == SAMPLE_FILE_ID
    assert source["parent_folder_id"] == ROOT
    assert source["auto_sync"] is True


def test_title_comes_from_the_source_unless_overridden(library, room, connection):
    assert add(library, room)["item"]["data"]["title"] == "Enterprise Overview Deck.pptx"

    override = add(library, room, external_content_id=STALE_FILE_ID, title="Compliance Pack")
    assert override["item"]["data"]["title"] == "Compliance Pack"


def test_linkage_is_linked_when_auto_sync_is_true(library, room, connection):
    assert add(library, room, auto_sync=True)["status"]["linkage"] == LINKED


def test_linkage_is_snapshot_when_auto_sync_is_false(library, room, connection):
    assert add(library, room, auto_sync=False)["status"]["linkage"] == SNAPSHOT


def test_auto_sync_defaults_to_false_when_omitted(library, room, connection):
    # The research: "The backend defaults this value to false when the field is
    # omitted from the request body", and the omitted field means a snapshot.
    result = add(library, room, auto_sync=None)

    assert result["item"]["data"]["source"]["auto_sync"] is False
    assert result["status"]["linkage"] == SNAPSHOT


def test_explicit_false_and_omitted_agree(library, room, connection):
    explicit = add(library, room, auto_sync=False)["item"]["data"]["source"]
    omitted = add(library, room, external_content_id=STALE_FILE_ID, auto_sync=None)["item"]["data"][
        "source"
    ]

    assert explicit["linkage"] == omitted["linkage"] == SNAPSHOT


def test_add_records_lineage_back_to_the_connection(library, room, connection):
    source = add(library, room)["item"]["data"]["source"]

    assert source["external_connection_id"] == connection["id"]
    assert source["external_system_connection_name"] == "Dana's Google Drive"


def test_provider_fields_are_stored_not_dropped(library, room, connection):
    source = add(library, room)["item"]["data"]["source"]

    # Provider fields this project does not model still survive the trip.
    assert source["provider_metadata"] == {"owners": ["dana@northwind.example"], "trashed": False}


def test_arbitrary_metadata_is_stored_and_indexed(library, room, connection, store):
    result = add(
        library, room, metadata={"review_owner": "sam", "campaign": {"tier": "enterprise"}}
    )

    assert result["item"]["data"]["campaign"] == {"tier": "enterprise"}
    found = store.find("document", {"campaign.tier": "enterprise"})
    assert [r["id"] for r in found] == [result["content_id"]]


# -- the documented prerequisites ------------------------------------------- #


def test_no_connection_is_a_not_found_with_a_remedy(library, room):
    with pytest.raises(ExternalSyncError) as caught:
        add(library, room)

    error = caught.value
    assert error.code == "ExternalConnectionNotFound"
    assert error.status == 404
    assert "profile settings" in error.remediation
    assert error.correlation_id.startswith("corr_")


def test_a_disconnected_connection_does_not_satisfy_the_prerequisite(
    library, store, room, connection
):
    store.update(connection["id"], {"status": "disconnected"})

    with pytest.raises(ExternalSyncError) as caught:
        add(library, room)

    assert caught.value.code == "ExternalConnectionNotFound"


def test_a_connection_record_without_a_status_still_counts(library, store, room):
    # A team adding a field must not have to remember to add a second one.
    store.create("external_connection", {"source": GOOGLE_DRIVE, "name": "Implicit"})

    assert add(library, room)["created"] is True


def test_unknown_source_is_an_invalid_parameter(library, room, connection):
    with pytest.raises(ExternalSyncError) as caught:
        add(library, room, external_source="Dropbox")

    assert caught.value.code == "InvalidParameter"
    assert caught.value.status == 400
    assert caught.value.extra["supported_sources"] == [GOOGLE_DRIVE]


def test_unknown_file_is_a_not_found(library, room, connection):
    with pytest.raises(ExternalSyncError) as caught:
        add(library, room, external_content_id="1NoSuchFile")

    assert caught.value.code == "ExternalContentNotFound"
    assert caught.value.status == 404


def test_unknown_folder_is_a_not_found(library, room, connection):
    with pytest.raises(ExternalSyncError) as caught:
        add(library, room, parent_folder_id="library_folder_missing")

    assert caught.value.code == "FolderNotFound"
    assert caught.value.extra["parent_folder_id"] == "library_folder_missing"


def test_a_folder_in_another_room_is_not_found(library, store, room, connection):
    other_room = store.create("room", {"name": "Contoso"})
    folder = store.create("library_folder", {"name": "Decks"}, room_id=other_room["id"])

    with pytest.raises(ExternalSyncError) as caught:
        add(library, room, parent_folder_id=folder["id"])

    assert caught.value.code == "FolderNotFound"


def test_a_real_folder_is_accepted(library, store, room, connection):
    folder = store.create("library_folder", {"name": "Q2 Decks"}, room_id=room["id"])

    result = add(library, room, parent_folder_id=folder["id"])

    assert result["item"]["data"]["source"]["parent_folder_id"] == folder["id"]
    assert result["item"]["data"]["source"]["parent_folder_name"] == "Q2 Decks"


def test_unknown_room_is_a_not_found(library, connection):
    with pytest.raises(ExternalSyncError) as caught:
        add(library, {"id": "room_missing"}, external_source=GOOGLE_DRIVE)

    assert caught.value.code == "RoomNotFound"


# -- the rate limit --------------------------------------------------------- #
#
# These call library.add directly rather than through the helper, because the
# helper clears the limiter and the limiter is the thing under test.


def test_a_second_add_within_the_window_is_rate_limited(library, room, connection):
    library.add(
        external_source=GOOGLE_DRIVE,
        external_content_id=SAMPLE_FILE_ID,
        room_id=room["id"],
        token="dana",
        source=ADD_SOURCE,
    )

    with pytest.raises(ExternalSyncError) as caught:
        library.add(
            external_source=GOOGLE_DRIVE,
            external_content_id=STALE_FILE_ID,
            room_id=room["id"],
            token="dana",
            source=ADD_SOURCE,
        )

    assert caught.value.code == "RateLimitExceeded"
    assert caught.value.status == 429
    assert caught.value.extra["retry_after_seconds"] > 0


def test_the_limit_is_per_token_not_global(library, room, connection, clock):
    library.add(
        external_source=GOOGLE_DRIVE,
        external_content_id=SAMPLE_FILE_ID,
        room_id=room["id"],
        token="dana",
        source=ADD_SOURCE,
    )

    # A different caller is unaffected by Dana's request.
    second = library.add(
        external_source=GOOGLE_DRIVE,
        external_content_id=STALE_FILE_ID,
        room_id=room["id"],
        token="sam",
        source=ADD_SOURCE,
    )
    assert second["created"] is True

    clock.advance(1.0)
    third = library.add(
        external_source=GOOGLE_DRIVE,
        external_content_id=STALE_FILE_ID,
        room_id=room["id"],
        token="dana",
        source=ADD_SOURCE,
    )
    assert third["created"] is False  # the throttle passed; the idempotent branch took over


def test_the_limit_expires_after_the_window(library, room, connection, clock):
    library.add(
        external_source=GOOGLE_DRIVE,
        external_content_id=SAMPLE_FILE_ID,
        room_id=room["id"],
        token="dana",
        source=ADD_SOURCE,
    )
    clock.advance(1.01)

    result = library.add(
        external_source=GOOGLE_DRIVE,
        external_content_id=STALE_FILE_ID,
        room_id=room["id"],
        token="dana",
        source=ADD_SOURCE,
    )
    assert result["created"] is True


def test_a_local_failure_does_not_spend_the_allowance(library, room):
    # No connection, so this fails on the local prerequisite. The allowance is
    # for the call to the source, and that call never happened, so the next
    # attempt must not be told it was throttled: the limiter must not mask the
    # real reason a call failed.
    for _ in range(2):
        with pytest.raises(ExternalSyncError) as caught:
            library.add(
                external_source=GOOGLE_DRIVE,
                external_content_id=SAMPLE_FILE_ID,
                room_id=room["id"],
                token="dana",
                source=ADD_SOURCE,
            )
        assert caught.value.code == "ExternalConnectionNotFound"


def test_a_relink_does_not_spend_the_allowance(library, room, connection):
    library.add(
        external_source=GOOGLE_DRIVE,
        external_content_id=SAMPLE_FILE_ID,
        room_id=room["id"],
        token="dana",
        source=ADD_SOURCE,
    )

    # A re-link returns before the source is contacted, so it is not throttled.
    again = library.add(
        external_source=GOOGLE_DRIVE,
        external_content_id=SAMPLE_FILE_ID,
        room_id=room["id"],
        token="dana",
        source=ADD_SOURCE,
    )
    assert again["created"] is False


# -- idempotence ------------------------------------------------------------ #


def test_relinking_the_same_file_into_the_same_folder_changes_nothing(
    library, room, connection, db
):
    first = add(library, room, external_content_id=SAMPLE_FILE_ID)
    second = add(library, room, external_content_id=SAMPLE_FILE_ID)

    assert second["created"] is False
    assert second["content_id"] == first["content_id"]
    inserts = [e for e in db.audit(collection="document") if e["action"] == "insert"]
    assert len(inserts) == 1


def test_the_same_file_may_be_linked_into_two_folders(library, store, room, connection):
    decks = store.create("library_folder", {"name": "Decks"}, room_id=room["id"])

    first = add(library, room, external_content_id=SAMPLE_FILE_ID)
    second = add(library, room, external_content_id=SAMPLE_FILE_ID, parent_folder_id=decks["id"])

    assert first["content_id"] != second["content_id"]


# -- read models ------------------------------------------------------------ #


def test_list_items_returns_only_external_items(library, store, room, connection):
    store.create("document", {"title": "Uploaded deck"}, room_id=room["id"])
    linked = add(library, room, external_content_id=SAMPLE_FILE_ID)

    items = library.list_items()

    assert [i["id"] for i in items] == [linked["content_id"]]


def test_list_items_filters_on_any_json_path(library, store, room, connection):
    add(library, room, external_content_id=SAMPLE_FILE_ID, auto_sync=True)
    library.limiter.reset()
    add(library, room, external_content_id=STALE_FILE_ID, auto_sync=False)

    live = library.list_items(where={"source.auto_sync": True})
    snapshots = library.list_items(where={"source.auto_sync": False})

    assert len(live) == 1 and live[0]["data"]["source"]["auto_sync"] is True
    assert len(snapshots) == 1 and snapshots[0]["data"]["source"]["auto_sync"] is False


def test_list_items_scopes_to_a_room(library, store, room, connection):
    other_room = store.create("room", {"name": "Contoso"})
    add(library, room, external_content_id=SAMPLE_FILE_ID)
    library.limiter.reset()
    add(library, room, external_content_id=STALE_FILE_ID, room_id=other_room["id"])

    assert len(library.list_items(room_id=other_room["id"])) == 1


def test_status_of_a_fresh_link_is_in_sync(library, room, connection):
    status = add(library, room)["status"]

    assert status["state"] == IN_SYNC
    assert status["applied_version"] == "rev-7"
    assert status["auto_sync"] is True
    assert status["last_synced_at"]


def test_get_item_resolves_by_record_id_and_by_content_id(library, store, room, connection):
    content_id = add(library, room)["content_id"]

    # The two are the same for an item this service created, so the fallback path
    # gets its own case: a record whose id differs from its contentId, which is
    # what a team writing the field themselves would produce.
    other = store.create(
        "document",
        {
            "title": "Hand-written link",
            "source": {
                "kind": "external",
                "content_id": "content_handwritten",
                "external_source": GOOGLE_DRIVE,
                "auto_sync": True,
            },
        },
        room_id=room["id"],
    )

    assert library.get_item(content_id)["id"] == content_id
    assert library.get_item(other["id"])["id"] == other["id"]
    assert library.get_item("content_handwritten")["id"] == other["id"]


def test_get_item_of_an_unknown_id_is_a_not_found(library):
    with pytest.raises(ExternalSyncError) as caught:
        library.get_item("document_missing")

    assert caught.value.code == "ExternalContentNotFound"


def test_sync_status_is_addressable_by_content_id(library, room, connection):
    content_id = add(library, room)["content_id"]

    status = library.sync_status(content_id)

    assert status["content_id"] == content_id
    assert status["record_id"] == content_id
    assert status["external_source"] == GOOGLE_DRIVE
    assert status["external_content_id"] == SAMPLE_FILE_ID


def test_sync_status_of_a_snapshot_explains_itself(library, room, connection):
    content_id = add(library, room, auto_sync=False)["content_id"]

    status = library.sync_status(content_id)

    assert status["state"] == IN_SYNC
    assert "one-time snapshot" in status["reason"]


def test_sync_status_of_an_unknown_id_is_a_not_found(library, store, room):
    with pytest.raises(ExternalSyncError) as caught:
        library.sync_status("document_missing")

    assert caught.value.code == "ExternalContentNotFound"


def test_sources_reports_registration_and_connection_state(library, store):
    before = library.sources()
    assert before == [
        {
            "name": GOOGLE_DRIVE,
            "supported": True,
            "connection_required": True,
            "connections": 0,
            "connected": False,
        }
    ]

    store.create(
        "external_connection", {"source": GOOGLE_DRIVE, "name": "Dana", "status": "connected"}
    )

    after = library.sources()
    assert after[0]["connected"] is True
    assert after[0]["connections"] == 1


def test_connections_expose_the_lineage_fields(library, connection):
    entry = library.connections()[0]

    assert entry["external_connection_id"] == connection["id"]
    assert entry["external_system_connection_name"] == "Dana's Google Drive"
    assert entry["external_system_connection_mapping"]["account"] == "dana@northwind.example"
    assert entry["connected"] is True


# -- the automation --------------------------------------------------------- #


def test_resync_applies_a_new_source_version(library, room, connection, drive, db):
    content_id = add(library, room, auto_sync=True)["content_id"]
    drive.advance(SAMPLE_FILE_ID, version="rev-8", modified_at="2026-09-26T08:00:00.000+00:00")

    report = library.resync(actor="sync-worker", source=RESYNC_SOURCE)

    assert report["checked"] == 1
    assert report["updated"] == [
        {"content_id": content_id, "from_version": "rev-7", "to_version": "rev-8"}
    ]
    item = db.require(content_id)
    assert item["data"]["source"]["source_version"] == "rev-8"
    assert item["data"]["size_bytes"] == 18_432_000


def test_resync_writes_nothing_when_the_source_has_not_moved(library, room, connection, db):
    add(library, room, auto_sync=True)
    before = len(db.audit(collection="document"))

    report = library.resync(source=RESYNC_SOURCE)

    assert report["updated"] == []
    assert report["in_sync"] == [
        {"content_id": report["in_sync"][0]["content_id"], "version": "rev-7"}
    ]
    # A pass that discovers nothing must not manufacture audit rows.
    assert len(db.audit(collection="document")) == before


def test_resync_never_follows_a_snapshot(library, room, connection, drive, db):
    content_id = add(library, room, auto_sync=False)["content_id"]
    drive.advance(SAMPLE_FILE_ID, version="rev-8", modified_at="2026-09-26T08:00:00.000+00:00")

    report = library.resync(source=RESYNC_SOURCE)

    assert report["updated"] == []
    assert report["skipped"] == [
        {"content_id": content_id, "reason": "snapshot: auto sync was not requested for this item"}
    ]
    assert db.require(content_id)["data"]["source"]["source_version"] == "rev-7"


def test_resync_marks_an_item_orphaned_when_its_source_is_gone(
    library, room, connection, drive, db
):
    content_id = add(library, room, auto_sync=True)["content_id"]
    drive.remove(SAMPLE_FILE_ID)

    report = library.resync(source=RESYNC_SOURCE)

    assert len(report["failed"]) == 1
    assert report["failed"][0]["error"] == "ExternalContentNotFound"
    item = db.require(content_id)
    assert item["data"]["source"]["status"] == ORPHANED
    assert "no longer visible" in library.status_of(item)["reason"]


def test_resync_leaves_an_already_orphaned_item_alone(library, room, connection, drive, db):
    add(library, room, auto_sync=True)
    drive.remove(SAMPLE_FILE_ID)
    library.resync(source=RESYNC_SOURCE)
    audit_after_first = len(db.audit(collection="document"))

    library.resync(source=RESYNC_SOURCE)

    # Still one audit row for the transition; the second pass changed nothing.
    assert len(db.audit(collection="document")) == audit_after_first


def test_resync_recovers_an_orphaned_item_when_the_source_returns(
    library, room, connection, drive, db
):
    content_id = add(library, room, auto_sync=True)["content_id"]
    drive.remove(SAMPLE_FILE_ID)
    library.resync(source=RESYNC_SOURCE)
    assert db.require(content_id)["data"]["source"]["orphaned_at"]

    drive.add(
        SourceFile(
            source=GOOGLE_DRIVE,
            file_id=SAMPLE_FILE_ID,
            name="Enterprise Overview Deck.pptx",
            mime_type="application/pdf",
            size_bytes=99,
            modified_at="2026-09-26T09:00:00.000+00:00",
            version="rev-9",
        )
    )

    report = library.resync(source=RESYNC_SOURCE)

    assert report["updated"] == [
        {"content_id": content_id, "from_version": "rev-7", "to_version": "rev-9"}
    ]
    recovered = db.require(content_id)["data"]["source"]
    assert recovered["status"] == IN_SYNC
    # The marker must not outlive the fact that caused it.
    assert recovered["orphaned_at"] is None
    assert recovered["orphaned_reason"] is None


def test_resync_keeps_the_item_in_its_folder_and_its_title(
    library, store, room, connection, drive, db
):
    folder = store.create("library_folder", {"name": "Q2 Decks"}, room_id=room["id"])
    content_id = add(
        library, room, parent_folder_id=folder["id"], title="Board-approved deck", auto_sync=True
    )["content_id"]
    drive.advance(
        SAMPLE_FILE_ID,
        version="rev-8",
        modified_at="2026-09-26T08:00:00.000+00:00",
        name="Enterprise Overview Deck (renamed upstream).pptx",
    )

    library.resync(source=RESYNC_SOURCE)

    item = db.require(content_id)["data"]
    # Placement and the human label are this project's, not the source's.
    assert item["source"]["parent_folder_id"] == folder["id"]
    assert item["title"] == "Board-approved deck"
    # Provider metadata that did change is applied.
    assert item["source"]["source_version"] == "rev-8"
    assert item["size_bytes"] == 18_432_000


def test_resync_fails_an_item_whose_connection_went_away(
    library, store, room, connection, drive, db
):
    content_id = add(library, room, auto_sync=True)["content_id"]
    store.update(connection["id"], {"status": "disconnected"})

    report = library.resync(source=RESYNC_SOURCE)

    assert report["failed"][0]["error"] == "ExternalConnectionNotFound"
    assert db.require(content_id)["data"]["source"]["status"] == ORPHANED


def test_resync_scopes_to_a_room(library, store, room, connection, drive):
    other_room = store.create("room", {"name": "Contoso"})
    add(library, room, external_content_id=SAMPLE_FILE_ID)
    library.limiter.reset()
    add(library, room, external_content_id=STALE_FILE_ID, room_id=other_room["id"])
    drive.advance(SAMPLE_FILE_ID, version="rev-8", modified_at="2026-09-26T08:00:00.000+00:00")
    drive.advance(STALE_FILE_ID, version="rev-4", modified_at="2026-09-26T08:00:00.000+00:00")

    report = library.resync(room_id=other_room["id"], source=RESYNC_SOURCE)

    assert report["checked"] == 1
    assert report["updated"][0]["to_version"] == "rev-4"


def test_a_moved_source_reads_in_sync_until_a_pass_runs(library, room, connection, drive):
    content_id = add(library, room, auto_sync=True)["content_id"]
    drive.advance(SAMPLE_FILE_ID, version="rev-8", modified_at="2026-09-26T08:00:00.000+00:00")

    # The store cannot know the source moved without asking it, so status stays
    # in_sync and the reason names the version the item actually applied. Drift
    # surfaces in the resync report instead of being guessed at here.
    before = library.status_of(library.store.get(content_id))
    assert before["state"] == IN_SYNC
    assert "rev-7" in before["reason"]

    report = library.resync(source=RESYNC_SOURCE)
    assert report["updated"] == [
        {"content_id": content_id, "from_version": "rev-7", "to_version": "rev-8"}
    ]
    assert "rev-8" in library.status_of(library.store.get(content_id))["reason"]


# -- the audited-store guarantee -------------------------------------------- #


def test_every_write_reaches_the_audit_log(library, room, connection, drive, db):
    content_id = add(library, room, auto_sync=True)["content_id"]
    drive.advance(SAMPLE_FILE_ID, version="rev-8", modified_at="2026-09-26T08:00:00.000+00:00")
    library.resync(actor="sync-worker", source=RESYNC_SOURCE)

    entries = db.audit(record_id=content_id)
    assert [e["action"] for e in entries] == ["update", "insert"]
    assert entries[0]["actor"] == "sync-worker"
    assert entries[0]["source"] == RESYNC_SOURCE
    assert entries[0]["diff"]["source"]["to"]["source_version"] == "rev-8"
    # The insert is audited under the caller's own source, not the re-sync one.
    assert entries[1]["source"] == ADD_SOURCE


def test_the_orphan_marker_is_audited_under_the_pass_that_found_it(
    library, room, connection, drive, db
):
    content_id = add(library, room, auto_sync=True)["content_id"]
    drive.remove(SAMPLE_FILE_ID)

    library.resync(actor="sync-worker", source=RESYNC_SOURCE)

    entry = db.audit(record_id=content_id)[0]
    assert entry["action"] == "update"
    assert entry["source"] == RESYNC_SOURCE
    assert entry["diff"]["source"]["to"]["status"] == ORPHANED


def test_a_failed_add_writes_nothing(library, room, db):
    before = len(db.audit())

    with pytest.raises(ExternalSyncError):
        add(library, room)  # no connection configured

    assert len(db.audit()) == before


# -- source registry -------------------------------------------------------- #


def test_the_default_registry_offers_only_googledrive():
    assert supported_sources() == [GOOGLE_DRIVE]


class BoxAdapter:
    """A second provider, standing in for one a deployment would register."""

    source = "Box"

    def describe(self, connection, file_id):
        return SourceFile(
            source=self.source,
            file_id=file_id,
            name="Board deck.pptx",
            mime_type="application/pdf",
            size_bytes=1,
            modified_at="2026-09-26T10:00:00.000+00:00",
            version="b-1",
        )


@pytest.fixture()
def box_registered():
    """Register Box for one test and take it back out afterwards.

    The registry is process-wide, so a test that registered into it and left it
    would make every later assertion about the supported set depend on test
    order. This is what ``unregister_source`` exists for.
    """
    register_source(BoxAdapter())
    yield "Box"
    unregister_source("Box")


def test_a_new_source_can_be_registered_without_touching_the_workflow(
    store, room, clock, box_registered
):
    library = ExternalLibrarySync(
        store, adapters=source_registry(), limiter=RateLimiter(clock=clock)
    )
    store.create("external_connection", {"source": "Box", "name": "Board", "status": "connected"})

    result = library.add(
        external_source="Box",
        external_content_id="98765",
        room_id=room["id"],
        auto_sync=True,
        source=ADD_SOURCE,
    )

    assert result["item"]["data"]["source"]["external_source"] == "Box"
    assert result["item"]["data"]["source"]["linkage"] == LINKED
    assert [s["name"] for s in library.sources()] == ["Box", GOOGLE_DRIVE]


def test_the_registry_is_back_to_one_source_after_a_registration_is_removed():
    assert supported_sources() == [GOOGLE_DRIVE]


def test_an_unsupported_source_names_the_supported_ones(library, room, connection):
    with pytest.raises(ExternalSyncError) as caught:
        add(library, room, external_source="Box")

    assert caught.value.extra["supported_sources"] == [GOOGLE_DRIVE]
    assert "GoogleDrive" in caught.value.remediation


def test_registering_a_duplicate_source_is_refused():
    with pytest.raises(ValueError):
        register_source(SimulatedDrive())


# -- the limiter on its own ------------------------------------------------- #


def test_limiter_reports_how_long_to_wait(clock):
    limiter = RateLimiter(clock=clock)
    limiter.check("dana")

    assert limiter.retry_after("dana") == pytest.approx(1.0)
    assert limiter.retry_after("sam") == 0.0
    clock.advance(1.0)
    assert limiter.retry_after("dana") == 0.0


def test_limiter_rejects_a_nonsense_configuration():
    with pytest.raises(ValueError):
        RateLimiter(max_calls=0)
    with pytest.raises(ValueError):
        RateLimiter(window_seconds=0)


def test_an_anonymous_caller_is_still_limited(clock):
    limiter = RateLimiter(clock=clock)
    limiter.check(None)

    with pytest.raises(ExternalSyncError) as caught:
        limiter.check(None)

    assert caught.value.code == "RateLimitExceeded"


def test_limiter_allows_more_than_one_call_when_configured(clock):
    limiter = RateLimiter(max_calls=2, window_seconds=1.0, clock=clock)
    limiter.check("dana")
    limiter.check("dana")

    with pytest.raises(ExternalSyncError):
        limiter.check("dana")


# -- the state vocabulary --------------------------------------------------- #


def test_state_words_are_the_documented_ones():
    assert (LINKED, SNAPSHOT) == ("linked", "snapshot")
    assert (IN_SYNC, ORPHANED) == ("in_sync", "orphaned")


def test_every_status_word_is_covered(library):
    # A state the code can produce is a state a test names. If the vocabulary
    # grows, this fails until someone decides what the new word means.
    produced = {
        library.status_of({"data": {}})["state"],
        library.status_of(
            {"data": {"source": {"linkage": LINKED, "auto_sync": True, "source_version": "v1"}}}
        )["state"],
        library.status_of({"data": {"source": {"linkage": SNAPSHOT, "auto_sync": False}}})["state"],
        library.status_of(
            {"data": {"source": {"linkage": LINKED, "auto_sync": True, "status": ORPHANED}}}
        )["state"],
    }

    assert produced == {"not_external", IN_SYNC, ORPHANED}


# --------------------------------------------------------------------------- #
# The port itself: mounting, sources, and demo data
# --------------------------------------------------------------------------- #


def test_the_feature_is_mounted_by_discovery():
    """No shared file names this feature, yet its routes resolve."""
    record = REGISTRY.by_id("wf-008-external-sync")

    assert record is not None, "the host did not mount wf-008-external-sync"
    assert record.prefix == "/api/library"
    assert record.exception_handlers == ["ExternalSyncError"]
    paths = {route["path"] for route in record.routes}
    assert paths == {
        "/api/library/sources",
        "/api/library/connections",
        "/api/library/external",
        "/api/library/external/resync",
        "/api/library/external/{content_id}",
        "/api/library/external/{content_id}/sync-status",
        "/api/library/folders",
    }


def test_the_module_owns_the_prefix_its_routes_are_built_from():
    """The audit source is derived, so a moved prefix moves the audit row too.

    The branch hard-coded ``POST /api/library/external`` in two places. Here
    there is one string, ``router.prefix``, and both sources are built from it --
    which is what makes it impossible for the audit log to name a route the app
    has stopped serving.
    """
    module = load_feature("wf008_external_sync")
    prefix = module.router.prefix

    assert prefix == "/api/library"
    for suffix in ("/external", "/external/resync"):
        assert module._source("POST", suffix) == f"POST {prefix}{suffix}"


def _code_strings(source: str) -> list[str]:
    """Every string literal in a module that is not a docstring.

    Docstrings are excluded on purpose: they are allowed to *talk about* the
    route strings, because explaining the rule is not depending on it. Only the
    code is held to it.
    """
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and getattr(node, "body", None)
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_the_domain_module_hard_codes_no_route():
    """The defect the port brief names, asserted rather than remembered.

    The audit source is the one string in an audit row that says where a change
    came from. If it can be typed into the domain it can go stale, and a stale one
    reads as authoritative -- which is exactly what shipped on the branch, where
    the re-sync pass wrote ``POST /api/library/external/resync`` as a literal.
    Every write method takes the source as an argument instead, so in the code
    there is no such string left to be wrong.
    """
    package = Path(load_feature("wf008_external_sync").__file__).parent.parent / "external_library"
    modules = sorted(package.glob("*.py"))
    assert modules, "the domain package was not found where the feature says it is"

    for module_path in modules:
        offenders = [
            text
            for text in _code_strings(module_path.read_text(encoding="utf-8"))
            if "/api/" in text
        ]
        assert not offenders, f"{module_path.name} hard-codes a route: {offenders}"


def test_the_service_depends_on_the_store_not_on_app_state(store):
    """Building the service must not require an edit to ``dsr/api.py``."""
    module = load_feature("wf008_external_sync")
    service = module.get_library(store)

    assert service.store is store
    # Cached per store, because the documented per-token rate limit is a property
    # of the process rather than of a request: a service built per request would
    # hand every request a full allowance and the limit would not exist.
    assert module.get_library(store) is service

    other_db = AuditedDatabase(":memory:")
    try:
        other = RecordStore(other_db)
        assert module.get_library(other) is not service
    finally:
        other_db.close()


def test_neither_the_feature_nor_its_domain_opens_the_database(store):
    """The audit row is written in the same transaction as the change, so a
    feature that reached past the store would break the product's one promise."""
    package = Path(load_feature("wf008_external_sync").__file__).parent
    files = [
        package / "wf008_external_sync.py",
        *sorted((package.parent / "external_library").glob("*.py")),
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "import sqlite3" not in text, f"{path.name} imports sqlite3 directly"
        assert ".connect(" not in text, f"{path.name} opens a connection directly"


def test_the_domain_package_does_not_shadow_wf007s_library_module():
    """A directory wins over a module of the same name, so the names must differ.

    WF-007's ``ContentLibrary`` lives in ``dsr/library.py``, which is on main.
    This branch's domain arrived as ``dsr/library/``, and Python resolves a
    package before a module of the same name on the same path -- so the package
    silently hid ``ContentLibrary`` and WF-007's whole feature would have failed
    to import with no error anywhere pointing here. The domain therefore lives at
    ``dsr/external_library/``.

    This test is the reason the rename cannot be undone by a later rename: it
    asserts both halves, not just this one.
    """
    import dsr.external_library
    import dsr.library

    assert hasattr(dsr.library, "ContentLibrary"), "WF-007's dsr/library.py is shadowed"
    assert Path(dsr.library.__file__).suffix == ".py", "dsr.library resolved to a package"
    assert hasattr(dsr.external_library, "ExternalLibrarySync")
    assert Path(dsr.external_library.__file__).name == "__init__.py"


def test_seed_adds_demo_data_without_touching_the_core_seeder(db):
    """A feature whose page is empty in the demo is a feature nobody can review."""
    rooms = [
        (db.create("room", {"name": "Northwind"}, actor="dana")["id"], "northwind.example"),
        (db.create("room", {"name": "Contoso"}, actor="sam")["id"], "contoso.example"),
    ]
    summary = feature_seed(db, seed_context(rooms))

    assert summary, "the seeder prints what a feature added; returning nothing hides it"
    assert db.count("external_connection") == 2
    assert db.count("library_folder") == 2
    assert db.count("document") == 3
    # Every seeded write is audited, and under the seeder as its source.
    assert {e["source"] for e in db.audit(collection="external_connection")} == {"seed"}


def test_seed_leaves_one_item_behind_its_source(store, db, drive):
    """The first "Run re-sync" in the UI has real drift to apply."""
    rooms = [
        (db.create("room", {"name": "Northwind"}, actor="dana")["id"], "northwind.example"),
        (db.create("room", {"name": "Contoso"}, actor="sam")["id"], "contoso.example"),
    ]
    feature_seed(db, seed_context(rooms))

    library = ExternalLibrarySync(store, adapters={GOOGLE_DRIVE: drive})
    report = library.resync(source=RESYNC_SOURCE)

    assert report["counts"]["updated"] == 1
    assert report["counts"]["skipped"] == 1  # the seeded snapshot
    assert report["counts"]["in_sync"] == 1


def test_seed_declines_rather_than_failing_on_a_small_demo(db):
    """The seeder skips a feature that raises; this one must not need to."""
    rooms = [(db.create("room", {"name": "Only"}, actor="dana")["id"], "one.example")]

    assert feature_seed(db, seed_context(rooms)) == ""
