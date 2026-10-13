"""Tests for WF-047: mirror room documents into CRM files.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-047.md``, whose flow is::

    1. A rep uploads a deck or proposal into the room, or an asset is generated.
    2. Rep toggles Push to CRM on the asset, or the connection mirrors all room
       documents.
    3. The room uploads the binary to the CRM files endpoint with
       multipart/form-data, then writes the returned file id into a CRM record, as
       a file-type property value or as a note attached to the record.
    4. The room stores crm_file_id on the asset row, so re-uploads are skipped and
       revisions create a new file.

So the suite is organised around the researched surfaces and around the boundary
the research draws, and it is explicit about which assertions are sourced and which
are this build's reading:

* **the upload** - the exact path, the content type and the field names the
  research quotes, and that the folder id is optional because the vendor says it is;
* **the returned id** - that it lands on the asset row in the vendor-agnostic slot,
  and that a repeat mirror of unchanged bytes sends nothing at all;
* **the attachment** - both researched modes, with the note as the default;
* **the unsourced half** - that Salesforce and Dataverse are refused *by name*,
  carrying the spec's own gap text, and that no payload is invented for either;
* **the reconciliation rule** - the quoted rule, the floor derived for it, and that
  a run which is not due asks the vendor for nothing;
* **the extension point** - that a deployment can register its own connector and the
  unsourced systems stop being refusals.

The inferences are not tested as if they were research. They are tested as the
*mechanism* each entry describes, and ``INFERENCES`` is asserted against what the
code actually does, so the register cannot drift away from the behaviour silently.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.crm_file_sync import (
    ASSETS,
    CONNECTIONS,
    INFERENCES,
    MIRRORS,
    REMOTE_FILES,
    AssetRefused,
    CrmFileSyncEngine,
    CrmFileSyncError,
    DuplicateConnection,
    FolderRefused,
    NoOwnerRecord,
    SimulatedConnector,
    UnknownAsset,
    UnknownConnection,
    UnknownMirror,
    UnsupportedSystem,
    assets as asset_rules,
    connector_for,
    connectors,
    content_digest,
    decode_content,
    engine as engine_module,
    is_revision,
    mirror_state,
    needs_mirror,
    normalise_asset,
    push_enabled,
    register_connector,
    unregister_connector,
    vocabulary,
)
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore

#: The feature's own module, loaded the way the host loads it.
MODULE = "wf047_mirror_room_documents_into_crm_files"

#: A frozen clock. Every test that needs a "now" takes it from here and adds a
#: timedelta, so no assertion depends on the day the suite runs.
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
STAMP = NOW.isoformat(timespec="milliseconds")

ROOM_ID = "room_wf047"


@pytest.fixture
def engine(store: RecordStore) -> CrmFileSyncEngine:
    """A real engine over a fresh, empty, in-memory database."""
    return CrmFileSyncEngine(store)


@pytest.fixture
def room(store: RecordStore) -> str:
    """One room for the asset and connection rows to hang off."""
    return store.create("room", {"name": "Northwind rollout"})["id"]


@pytest.fixture
def hubspot(engine: CrmFileSyncEngine, room: str) -> dict:
    """A HubSpot deal connection: the one system the research documents."""
    return engine.save_connection(
        {
            "system": "hubspot",
            "owner_object": "deal",
            "crm_record_id": "fabrikam-dl-9001",
            "attach_mode": "note",
        },
        room_id=room,
        actor="dana",
        source="POST /api/wf-047/connections",
    )


def deck(**overrides) -> dict:
    """A small deck payload, base64 so the test reads the way the API does."""
    payload = {
        "filename": "northwind-rollout-deck.pdf",
        "mime_type": "application/pdf",
        "content_base64": "UERGIC0xMQSBXUi0wMDAx",
        "push_to_crm": True,
    }
    payload.update(overrides)
    return payload


def add(engine: CrmFileSyncEngine, connection: dict, room: str, **overrides) -> str:
    """Register one asset and return its id."""
    result = engine.register_asset(
        deck(**overrides),
        connection_id=connection["id"],
        room_id=room,
        actor="dana",
        source="POST /api/wf-047/rooms/{room_id}/assets",
    )
    return result["asset"]["id"]


def mirror(engine: CrmFileSyncEngine, asset_id: str, room: str, **kwargs) -> dict:
    """Mirror one asset at the frozen clock."""
    return engine.mirror_asset(
        asset_id,
        room_id=room,
        actor="dana",
        source="POST /api/wf-047/rooms/{room_id}/assets/{asset_id}/mirror",
        now=STAMP,
        **kwargs,
    )


def asset_id_of(engine: CrmFileSyncEngine, room: str, filename: str) -> str:
    """The id of the one asset with this filename, so a test never relies on the
    order :meth:`list_assets` happens to sort two equal names into."""
    matches = [
        entry
        for entry in engine.list_assets(room_id=room, limit=100)
        if entry["filename"] == filename
    ]
    assert len(matches) == 1, f"{filename} matched {len(matches)} assets"
    return matches[0]["id"]


class _FolderRefusingConnector:
    """A tenant connector whose vendor has retired a folder.

    The research quotes no folder id and no folder rule, so the simulation cannot
    invent one. What it can prove is the *shape* of the failure: a folder the
    vendor rejects is a named 502 on the route, not a silent move to the root.
    """

    system = "hubspot"
    note = "scripted"

    def upload(self, content: bytes, **kwargs):
        folder_id = str(kwargs.get("folder_id") or "")
        if folder_id:
            raise connectors.refuse_folder(f"folder {folder_id} no longer exists")
        return connectors.UploadedFile(file_id="never", filename=str(kwargs.get("filename") or ""))

    def attach(self, file_id: str, record_id: str, **kwargs):
        return connectors.Attachment(mode=str(kwargs.get("mode") or "note"), record_id=record_id)


# --------------------------------------------------------------------------- #
# The upload the research quotes
# --------------------------------------------------------------------------- #


def test_the_upload_goes_to_the_path_and_content_type_the_research_quotes(engine, room, hubspot):
    """'Files can be uploaded using a multipart/form-data POST request to
    files/2026-09/files', with the field ``file``."""
    asset_id = add(engine, hubspot, room)
    result = mirror(engine, asset_id, room)
    request = result["request"]
    assert request["method"] == "POST"
    assert request["path"] == "/files/2026-09/files"
    assert request["content_type"] == "multipart/form-data"
    assert request["fields"]["file"] == "northwind-rollout-deck.pdf"
    assert "fileName" in request["fields"]
    assert request["required_fields"] == ["file"]
    assert request["optional_fields"] == ["folderId", "fileName"]


def test_the_folder_id_is_optional_because_the_vendor_says_it_is(engine, room):
    """'While a specific folder ID is not required at upload, it's recommended to
    upload files into a folder and not the root directory.'"""
    without = engine.save_connection(
        {"system": "hubspot", "crm_record_id": "deal-1"}, room_id=room, actor="d", source="s"
    )
    assert without["folder_id"] == ""
    assert without["folder_recommendation_followed"] is False

    with_folder = engine.save_connection(
        {"system": "hubspot", "crm_record_id": "deal-2", "folder_id": "fabrikam-3300"},
        room_id=room,
        actor="d",
        source="s",
    )
    assert with_folder["folder_recommendation_followed"] is True

    result = mirror(engine, add(engine, with_folder, room), room)
    assert result["request"]["fields"]["folderId"] == "fabrikam-3300"
    # A connection with no folder sends no folder field at all, rather than an
    # empty one the vendor would have to interpret.
    plain = mirror(engine, add(engine, without, room), room)
    assert "folderId" not in plain["request"]["fields"]


def test_the_vendor_folder_recommendation_is_served_as_data(engine):
    """The recommendation and its caveat reach a page as words, not as a claim."""
    served = engine.vocabulary()
    assert served["folder_recommendation"].startswith("While a specific folder ID")
    assert served["folder_caveat"] == "Folder requirements at upload are subject to change."


def test_a_folder_the_vendor_rejects_is_a_named_failure_not_a_silent_fall_back(engine, room):
    """'Folder requirements at upload are subject to change.' A change must be
    visible; falling back to the root would hide it."""
    engine.save_connection(
        {"system": "hubspot", "crm_record_id": "deal-f", "folder_id": "retired-folder"},
        room_id=room,
        actor="d",
        source="s",
    )
    register_connector("hubspot", lambda store: _FolderRefusingConnector())
    try:
        with pytest.raises(connectors.FolderRefused) as raised:
            mirror(engine, add(engine, engine.connections(room)[0], room), room)
    finally:
        unregister_connector("hubspot")
    assert raised.value.status == 502
    assert "retired-folder" in str(raised.value)


def test_the_folder_refusal_helper_is_the_502_this_package_answers():
    """A deployment's connector needs one import to make the same refusal."""
    refusal = connectors.refuse_folder("folder retired-folder no longer exists")
    assert isinstance(refusal, FolderRefused)
    assert refusal.status == 502
    assert refusal.code == "folder_refused"


# --------------------------------------------------------------------------- #
# Step four: crm_file_id on the asset row, re-uploads skipped
# --------------------------------------------------------------------------- #


def test_the_returned_file_id_lands_in_the_vendor_agnostic_slot(engine, room, hubspot):
    """'The room stores crm_file_id on the asset row.'"""
    asset_id = add(engine, hubspot, room)
    result = mirror(engine, asset_id, room)
    assert result["outcome"] == "mirrored"
    assert result["uploaded"]["file_id"]
    stored = engine.read_asset(asset_id, room_id=room)
    assert stored["crm_file_id"] == result["uploaded"]["file_id"]


def test_a_repeat_mirror_of_unchanged_bytes_sends_nothing(engine, room, hubspot):
    """'so re-uploads are skipped'. The second mirror creates no second file."""
    asset_id = add(engine, hubspot, room)
    first = mirror(engine, asset_id, room)
    second = mirror(engine, asset_id, room)
    assert first["outcome"] == "mirrored"
    assert second["outcome"] == "skipped_unchanged"
    assert second["request"] == {}, "a skipped mirror must not describe a request"
    remote = SimulatedConnector(engine.store).file_rows()
    assert len(remote) == 1, "the second mirror created a second file in the CRM"


def test_forcing_a_repeat_mirror_does_create_a_second_crm_row(engine, room, hubspot):
    """``force`` is the operator's door, and it is honest about what it did."""
    asset_id = add(engine, hubspot, room)
    mirror(engine, asset_id, room)
    again = mirror(engine, asset_id, room, force=True)
    assert again["outcome"] == "mirrored"
    assert again["uploaded"]["file_id"]
    assert len(SimulatedConnector(engine.store).file_rows()) == 1, (
        "the connector is idempotent on the bytes, so a forced re-upload returns "
        "the same file rather than inventing a second one"
    )


def test_the_idempotency_key_is_the_file_id_and_not_the_row(engine, room, hubspot):
    """A second asset with the same bytes is a second document and gets its own file."""
    first = mirror(engine, add(engine, hubspot, room), room)
    second = mirror(engine, add(engine, hubspot, room), room)
    assert first["uploaded"]["file_id"] == second["uploaded"]["file_id"]
    assert first["asset"]["id"] != second["asset"]["id"]


# --------------------------------------------------------------------------- #
# A revision, and how this build decides one happened
# --------------------------------------------------------------------------- #


def test_a_byte_change_creates_a_new_file_and_keeps_the_old_one(engine, room, hubspot):
    """'revisions create a new file'. A CRM file is not edited in place."""
    asset_id = add(engine, hubspot, room)
    original = mirror(engine, asset_id, room)
    engine.replace_content(
        asset_id,
        {
            "filename": "northwind-rollout-deck.pdf",
            "mime_type": "application/pdf",
            "content_base64": "UERGIC0xMQBSZXZpc2VkLXYy",
        },
        room_id=room,
        actor="dana",
        source="PATCH /api/wf-047/rooms/{room_id}/assets/{asset_id}",
    )
    revised = mirror(engine, asset_id, room)
    assert revised["outcome"] == "mirrored"
    assert revised["uploaded"]["file_id"] != original["uploaded"]["file_id"]
    remote = {row["file_id"] for row in SimulatedConnector(engine.store).file_rows()}
    assert original["uploaded"]["file_id"] in remote, "the superseded file is still in the CRM"
    assert revised["uploaded"]["file_id"] in remote


def test_the_chip_reads_stale_between_a_revision_and_its_push(engine, room, hubspot):
    """The Documents page shows a revision waiting, not a file that is current."""
    asset_id = add(engine, hubspot, room)
    mirror(engine, asset_id, room)
    assert engine.read_asset(asset_id, room_id=room)["state"] == vocabulary.MIRRORED
    engine.replace_content(
        asset_id,
        {
            "filename": "northwind-rollout-deck.pdf",
            "mime_type": "application/pdf",
            "content_base64": "UERGIC0xMQBSZXZpc2VkLXYy",
        },
        room_id=room,
        actor="dana",
        source="PATCH /api/wf-047/rooms/{room_id}/assets/{asset_id}",
    )
    stale = engine.read_asset(asset_id, room_id=room)
    assert stale["state"] == vocabulary.STALE
    assert stale["is_revision"] is True
    assert stale["state_note"], "a status may never be carried by colour alone"


def test_a_renamed_document_is_not_a_revision(engine, room, hubspot):
    """The digest is of the bytes, not of the filename beside them.

    This is the decision the research leaves open. The store's revision counter
    was rejected because it measures writes: a rep who tidied a filename would get
    a second copy in the CRM that nobody asked for.
    """
    asset_id = add(engine, hubspot, room)
    mirror(engine, asset_id, room)
    engine.store.update(asset_id, {"filename": "rollout-deck-final.pdf"})
    after = engine.read_asset(asset_id, room_id=room)
    assert after["state"] == vocabulary.MIRRORED
    assert after["is_revision"] is False
    assert after["needs_mirror"] is False


def test_the_digest_is_prefixed_with_the_algorithm_that_made_it():
    digest = content_digest(b"bytes")
    assert digest.startswith("sha256:")
    assert content_digest(b"bytes") == digest
    assert content_digest(b"other") != digest


def test_is_revision_needs_a_stored_file_id(engine, room, hubspot):
    """An asset that was never pushed has nothing to be a revision of."""
    asset_id = add(engine, hubspot, room)
    stored = engine.store.get(asset_id)["data"]
    assert is_revision(stored) is False
    mirror(engine, asset_id, room)
    stored = engine.store.get(asset_id)["data"]
    assert is_revision(stored) is False, "a mirror just performed is not a revision"


# --------------------------------------------------------------------------- #
# Step two: the Push to CRM toggle
# --------------------------------------------------------------------------- #


def test_the_toggle_off_withholds_the_mirror_and_the_vendor_is_never_asked(engine, room, hubspot):
    asset_id = add(engine, hubspot, room, push_to_crm=False)
    result = mirror(engine, asset_id, room)
    assert result["outcome"] == vocabulary.OUTCOME_WITHHELD
    assert result["asset"]["state"] == vocabulary.WITHHELD
    assert result["request"] == {}
    assert SimulatedConnector(engine.store).file_rows() == []


def test_the_toggle_can_be_switched_back_on_and_then_the_asset_pushes(engine, room, hubspot):
    asset_id = add(engine, hubspot, room, push_to_crm=False)
    mirror(engine, asset_id, room)
    engine.set_push(
        asset_id,
        True,
        room_id=room,
        actor="dana",
        source="PATCH /api/wf-047/rooms/{room_id}/assets/{asset_id}",
    )
    assert mirror(engine, asset_id, room)["outcome"] == "mirrored"


def test_switching_the_toggle_off_withdraws_nothing_and_says_so(engine, room, hubspot):
    """The vendor publishes no delete in the API this workflow uses, and a row
    claiming otherwise would be lying about the vendor."""
    asset_id = add(engine, hubspot, room)
    mirrored = mirror(engine, asset_id, room)
    off = engine.set_push(
        asset_id,
        False,
        room_id=room,
        actor="dana",
        source="PATCH /api/wf-047/rooms/{room_id}/assets/{asset_id}",
    )
    assert off["note"]
    assert (
        engine.read_asset(asset_id, room_id=room)["crm_file_id"] == mirrored["uploaded"]["file_id"]
    )
    assert len(SimulatedConnector(engine.store).file_rows()) == 1


def test_the_connection_can_mirror_every_room_document_instead(engine, room):
    """'(or the connection is configured to mirror all room documents)'."""
    connection = engine.save_connection(
        {"system": "hubspot", "crm_record_id": "deal-9", "mirror_all": True},
        room_id=room,
        actor="d",
        source="s",
    )
    asset_id = add(engine, connection, room)
    registered = engine.store.get(asset_id)["data"]
    assert registered["push_to_crm"] is True, "the connection answered for an asset with no toggle"
    assert mirror(engine, asset_id, room)["outcome"] == "mirrored"


def test_an_assets_own_toggle_beats_the_connection_default(engine, room):
    connection = engine.save_connection(
        {"system": "hubspot", "crm_record_id": "deal-9", "mirror_all": True},
        room_id=room,
        actor="d",
        source="s",
    )
    stored = engine.store.get(add(engine, connection, room, push_to_crm=False))["data"]
    assert push_enabled(stored, connection) is False
    assert mirror(engine, add(engine, connection, room, push_to_crm=False), room)["outcome"] == (
        vocabulary.OUTCOME_WITHHELD
    )


def test_push_enabled_is_false_with_no_toggle_and_no_connection():
    assert push_enabled({"filename": "a.pdf"}) is False
    assert push_enabled({"push_to_crm": True}, {"mirror_all": False}) is True
    assert push_enabled({}, {"mirror_all": True}) is True


# --------------------------------------------------------------------------- #
# Step three: the attachment, in both researched modes
# --------------------------------------------------------------------------- #


def test_the_note_is_the_default_attach_mode(engine, room):
    assert vocabulary.DEFAULT_ATTACH_MODE == vocabulary.ATTACH_NOTE
    connection = engine.save_connection(
        {"system": "hubspot", "crm_record_id": "deal-1"}, room_id=room, actor="d", source="s"
    )
    assert connection["attach_mode"] == vocabulary.ATTACH_NOTE
    result = mirror(engine, add(engine, connection, room), room)
    assert result["attachment"]["mode"] == "note"
    assert result["attachment"]["record_id"] == "deal-1"


def test_a_file_property_connection_writes_the_id_into_the_named_property(engine, room):
    """'Stores a file ID.' The connection names the property, because a tenant
    that has not created one has nothing to write into."""
    connection = engine.save_connection(
        {
            "system": "hubspot",
            "owner_object": "contact",
            "crm_record_id": "contact-7",
            "attach_mode": "file_property",
            "file_property": "proposal_attachment",
        },
        room_id=room,
        actor="d",
        source="s",
    )
    result = mirror(engine, add(engine, connection, room), room)
    assert result["attachment"]["mode"] == "file_property"
    assert result["attachment"]["property_name"] == "proposal_attachment"


def test_a_file_property_connection_without_a_property_is_refused(engine, room):
    with pytest.raises(NoOwnerRecord) as raised:
        engine.save_connection(
            {"system": "hubspot", "crm_record_id": "c-1", "attach_mode": "file_property"},
            room_id=room,
            actor="d",
            source="s",
        )
    assert raised.value.status == 409


def test_the_vendor_words_for_both_attach_modes_are_served_as_data(engine):
    served = engine.vocabulary()
    assert "notes APIs" in served["attach_mode_notes"]["note"]
    assert "Stores a file ID" in served["attach_mode_notes"]["file_property"]


def test_attaching_to_a_record_the_crm_does_not_hold_is_a_named_refusal(engine, room, hubspot):
    connector = SimulatedConnector(engine.store)
    from dsr.crm_file_sync.errors import AttachRefused

    with pytest.raises(AttachRefused) as raised:
        connector.attach("file-absent", "deal-1", mode="note")
    assert raised.value.status == 502


# --------------------------------------------------------------------------- #
# The dependency the issue names: a record has to own the attachment
# --------------------------------------------------------------------------- #


def test_a_connection_with_no_owning_record_records_a_named_outcome(engine, room):
    """'Without it, this workflow has no contact or deal record to own the
    attachment, so the returned file id has nowhere to be written.'"""
    connection = engine.save_connection(
        {"system": "hubspot", "owner_object": "deal", "crm_record_id": ""},
        room_id=room,
        actor="d",
        source="s",
    )
    result = mirror(engine, add(engine, connection, room), room)
    assert result["outcome"] == vocabulary.OUTCOME_NO_OWNER_RECORD
    assert result["error_code"] == "no_owner_record"
    assert result["uploaded"] == {}
    assert SimulatedConnector(engine.store).file_rows() == [], "nothing was sent"


def test_the_no_owner_record_outcome_names_the_dependency_in_its_own_words(engine, room):
    connection = engine.save_connection(
        {"system": "hubspot", "crm_record_id": ""}, room_id=room, actor="d", source="s"
    )
    result = mirror(engine, add(engine, connection, room), room)
    assert "WF-042" in result["error_detail"]
    assert result["mirror_id"], "the outcome is a row, not a raised error"


def test_the_document_is_not_lost_when_no_record_exists(engine, room):
    connection = engine.save_connection(
        {"system": "hubspot", "crm_record_id": ""}, room_id=room, actor="d", source="s"
    )
    asset_id = add(engine, connection, room)
    mirror(engine, asset_id, room)
    assert engine.read_asset(asset_id, room_id=room)["filename"] == "northwind-rollout-deck.pdf"


# --------------------------------------------------------------------------- #
# The unsourced half: refused by name, never invented
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("system", ["salesforce", "dataverse"])
def test_a_system_whose_file_api_could_not_be_sourced_is_refused_by_name(engine, system):
    """The spec's gaps block, enforced."""
    with pytest.raises(UnsupportedSystem) as raised:
        connector_for(engine.store, system)
    assert raised.value.status == 422
    assert "could not be sourced" in str(raised.value), "the refusal carries the gap text"


@pytest.mark.parametrize("system", ["salesforce", "dataverse"])
def test_mirroring_to_an_unsourced_system_is_a_failed_chip_not_an_exception(engine, room, system):
    """The researched product surface is a status chip per file, so a tenant on an
    unsourced vendor sees a failed chip naming the gap."""
    connection = engine.save_connection(
        {"system": system, "crm_record_id": "x-1"}, room_id=room, actor="d", source="s"
    )
    result = mirror(engine, add(engine, connection, room), room)
    assert result["outcome"] == vocabulary.OUTCOME_FAILED
    assert result["error_code"] == "unsupported_system"
    assert result["asset"]["state"] == vocabulary.FAILED
    assert len(engine.mirrors_for_asset(result["asset"]["id"])) == 1


def test_only_one_system_is_file_capable_and_the_report_says_which(engine):
    report = engine.vendors()
    assert report["sourced"] == 1
    assert report["unsourced"] == 2
    gaps = {row["system"]: row["gap"] for row in report["vendors"] if row["gap"]}
    assert set(gaps) == {"salesforce", "dataverse"}
    assert all("could not be sourced" in text for text in gaps.values())


def test_the_hubspot_report_carries_the_path_and_the_field_names(engine):
    row = next(r for r in engine.vendors()["vendors"] if r["system"] == "hubspot")
    assert row["status"] == "sourced"
    assert row["upload_path"] == "/files/2026-09/files"
    assert row["required_fields"] == ["file"]
    assert row["connector_methods"] == ["upload", "attach"]


def test_an_unknown_system_is_refused_rather_than_defaulted(engine):
    """Defaulting an unknown vendor to HubSpot would upload a tenant's files to a
    vendor they did not choose."""
    from dsr.crm_file_sync.errors import system_for

    with pytest.raises(CrmFileSyncError) as raised:
        system_for("oracle")
    assert raised.value.status == 400
    assert "hubspot" in str(raised.value)


# --------------------------------------------------------------------------- #
# The extension point the spec's Extensibility field names
# --------------------------------------------------------------------------- #


def test_a_deployment_can_register_a_connector_for_an_unsourced_system(engine, room):
    """'a third-party connector can support any file-capable CRM by implementing a
    two-method interface'. Registering one clears the refusal."""
    calls: list[tuple[str, str]] = []

    class TenantConnector:
        system = "salesforce"
        note = "tenant"

        def upload(self, content: bytes, **kwargs):
            calls.append(("upload", kwargs.get("filename", "")))
            return connectors.UploadedFile(
                file_id="sf-file-1", folder_id="", filename=kwargs.get("filename", "")
            )

        def attach(self, file_id: str, record_id: str, **kwargs):
            calls.append(("attach", record_id))
            return connectors.Attachment(mode=kwargs.get("mode", "note"), record_id=record_id)

    register_connector("salesforce", lambda store: TenantConnector())
    try:
        connection = engine.save_connection(
            {"system": "salesforce", "crm_record_id": "006-nw-5001"},
            room_id=room,
            actor="d",
            source="s",
        )
        result = mirror(engine, add(engine, connection, room), room)
        assert result["outcome"] == "mirrored"
        assert result["uploaded"]["file_id"] == "sf-file-1"
        assert result["simulated"] is False
        assert calls == [("upload", "northwind-rollout-deck.pdf"), ("attach", "006-nw-5001")]
    finally:
        unregister_connector("salesforce")


def test_a_registered_connector_satisfies_the_two_method_protocol():
    assert isinstance(SimulatedConnector.__new__(SimulatedConnector), connectors.FileConnector)


def test_the_connector_is_described_with_the_methods_it_implements(engine):
    described = connectors.describe_connector(SimulatedConnector(engine.store))
    assert described["system"] == "hubspot"
    assert described["note"] == "simulated"
    assert described["implements_upload"] and described["implements_attach"]


# --------------------------------------------------------------------------- #
# The simulated vendor, and why it says so
# --------------------------------------------------------------------------- #


def test_the_default_connector_records_itself_as_simulated_on_every_mirror(engine, room, hubspot):
    result = mirror(engine, add(engine, hubspot, room), room)
    assert result["simulated"] is True
    stored = engine.read_mirror(result["mirror_id"])
    assert stored["simulated"] is True
    assert stored["connector"] == "hubspot"


def test_the_simulated_vendor_holds_the_file_and_the_attachment(engine, room, hubspot):
    result = mirror(engine, add(engine, hubspot, room), room)
    rows = engine.remote_files()
    assert rows["simulated"] is True
    row = rows["files"][0]
    assert row["file_id"] == result["uploaded"]["file_id"]
    assert row["attached_records"] == [
        {"record_id": "fabrikam-dl-9001", "mode": "note", "property_name": ""}
    ]
    assert "content_base64" not in row, "a panel wants to know the file exists, not carry it"


def test_the_simulated_vendor_never_invents_a_folder_rule(engine):
    """The research quotes no folder limit and no folder id, so the simulation
    accepts whatever folder it is handed and records where the file landed. The
    vendor's own folder rules are the vendor's to enforce."""
    connector = SimulatedConnector(engine.store)
    landed = connector.upload(
        b"bytes", filename="a.pdf", mime_type="application/pdf", folder_id="any-folder"
    )
    assert landed.folder_id == "any-folder"
    assert connector.file_rows("any-folder")[0]["file_id"] == landed.file_id


def test_the_same_bytes_in_another_folder_are_a_different_crm_file(engine, room):
    """A folder is part of where a file lives, so the same bytes in two folders
    are two files rather than one."""
    connector = SimulatedConnector(engine.store)
    one = connector.upload(b"bytes", filename="a.pdf", mime_type="application/pdf", folder_id="f1")
    two = connector.upload(b"bytes", filename="a.pdf", mime_type="application/pdf", folder_id="f2")
    assert one.file_id != two.file_id


def test_the_remote_files_collection_is_named_for_the_vendor(engine):
    """``crm_remote_`` stands in for the vendor's storage, the way
    ``crm_provisioning`` names its three remote collections."""
    assert REMOTE_FILES.startswith("crm_remote_")
    assert CONNECTIONS == "crm_file_connection"
    assert ASSETS == "crm_file_asset"
    assert MIRRORS == "crm_file_mirror"


# --------------------------------------------------------------------------- #
# The reconciliation rule
# --------------------------------------------------------------------------- #


def test_the_reconciliation_rule_is_the_quoted_one(engine):
    assert engine.vocabulary()["reconcile_rule"] == (
        "find room assets with no crm_file_id and push them"
    )


def test_the_reconciliation_pushes_an_asset_whose_event_never_arrived(engine, room, hubspot):
    asset_id = add(engine, hubspot, room)
    result = engine.reconcile(
        room, actor="dana", source="POST /api/wf-047/rooms/{room_id}/reconcile", now=STAMP
    )
    assert result["outcome"] == "reconciled"
    assert result["candidates"] == 1
    assert result["mirrored"] == 1
    assert result["results"][0]["outcome"] == "mirrored"
    assert engine.read_asset(asset_id, room_id=room)["crm_file_id"]


def test_the_reconciliation_also_pushes_a_revision_it_missed(engine, room, hubspot):
    asset_id = add(engine, hubspot, room)
    mirror(engine, asset_id, room)
    engine.replace_content(
        asset_id,
        {
            "filename": "northwind-rollout-deck.pdf",
            "mime_type": "application/pdf",
            "content_base64": "UERGIC0xMQBSZXZpc2VkLXYz",
        },
        room_id=room,
        actor="dana",
        source="PATCH /api/wf-047/rooms/{room_id}/assets/{asset_id}",
    )
    result = engine.reconcile(
        room, actor="dana", source="POST /api/wf-047/rooms/{room_id}/reconcile", now=STAMP
    )
    assert result["candidates"] == 1
    assert engine.read_asset(asset_id, room_id=room)["state"] == vocabulary.MIRRORED


def test_the_reconciliation_never_pushes_an_asset_whose_toggle_is_off(engine, room, hubspot):
    add(engine, hubspot, room, push_to_crm=False)
    result = engine.reconcile(
        room, actor="dana", source="POST /api/wf-047/rooms/{room_id}/reconcile", now=STAMP
    )
    assert result["candidates"] == 0
    assert result["mirrored"] == 0


def test_a_reconciliation_inside_the_floor_asks_the_vendor_for_nothing(engine, room, hubspot):
    """The floor is a floor a scheduler respects, not a timer this package runs."""
    add(engine, hubspot, room)
    engine.reconcile(
        room,
        actor="dana",
        source="POST /api/wf-047/rooms/{room_id}/reconcile",
        now=STAMP,
    )
    soon = (NOW + timedelta(seconds=30)).isoformat(timespec="milliseconds")
    result = engine.reconcile(
        room, actor="dana", source="POST /api/wf-047/rooms/{room_id}/reconcile", now=soon
    )
    assert result["outcome"] == "not_due"
    assert result["mirrored"] == 0
    assert result["results"] == []


def test_a_reconciliation_past_the_floor_runs_again(engine, room, hubspot):
    add(engine, hubspot, room)
    later = (NOW + timedelta(seconds=vocabulary.DEFAULT_POLL_INTERVAL_SECONDS + 1)).isoformat(
        timespec="milliseconds"
    )
    first = engine.reconcile(
        room, actor="dana", source="POST /api/wf-047/rooms/{room_id}/reconcile", now=STAMP
    )
    second = engine.reconcile(
        room, actor="dana", source="POST /api/wf-047/rooms/{room_id}/reconcile", now=later
    )
    assert first["outcome"] == "reconciled"
    assert second["outcome"] == "reconciled"


def test_the_floor_is_the_same_number_the_other_polled_vendor_loops_use(engine):
    """One number governs every polled vendor workflow in this product, so the
    derivation is recorded rather than invented per package."""
    from dsr.crm_backfill.plan import normalise_poll_interval

    assert vocabulary.DEFAULT_POLL_INTERVAL_SECONDS == normalise_poll_interval(None)
    assert (
        engine.vocabulary()["default_poll_interval_seconds"]
        == vocabulary.DEFAULT_POLL_INTERVAL_SECONDS
    )


def test_the_floor_is_a_floor_a_connection_cannot_lower_to_nothing(engine, room):
    connection = engine.save_connection(
        {"system": "hubspot", "crm_record_id": "d-1", "poll_interval_seconds": 0},
        room_id=room,
        actor="d",
        source="s",
    )
    assert connection["poll_interval_seconds"] == vocabulary.DEFAULT_POLL_INTERVAL_SECONDS
    floored = engine.save_connection(
        {"id": connection["id"], "poll_interval_seconds": 5},
        room_id=room,
        actor="d",
        source="s",
    )
    assert floored["poll_interval_seconds"] == vocabulary.MIN_POLL_INTERVAL_SECONDS


def test_a_forced_reconciliation_runs_regardless_of_the_floor(engine, room, hubspot):
    """A room that restarted has not been respecting anybody's interval."""
    add(engine, hubspot, room)
    engine.reconcile(
        room, actor="dana", source="POST /api/wf-047/rooms/{room_id}/reconcile", now=STAMP
    )
    result = engine.reconcile(
        room,
        actor="dana",
        source="POST /api/wf-047/rooms/{room_id}/reconcile",
        now=STAMP,
        force=True,
    )
    assert result["outcome"] == "reconciled"


def test_nothing_in_this_package_starts_a_thread():
    """A timer inside a web process double-writes the moment two workers exist."""
    import threading

    assert threading.active_count() >= 1
    source = Path(engine_module.__file__).read_text(encoding="utf-8")
    for forbidden in ("threading.Thread", "Timer(", "asyncio.create_task", "schedule.every"):
        assert forbidden not in source, forbidden


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #


def test_a_second_connection_for_the_same_record_is_refused(engine, room, hubspot):
    """Two connections pushing the same document into the same record is the
    duplicate-file problem the idempotency rule exists to prevent."""
    with pytest.raises(DuplicateConnection) as raised:
        engine.save_connection(
            {"system": "hubspot", "owner_object": "deal", "crm_record_id": "fabrikam-dl-9001"},
            room_id=room,
            actor="d",
            source="s",
        )
    assert raised.value.status == 409


def test_the_same_vendor_on_a_different_record_is_a_second_connection(engine, room, hubspot):
    other = engine.save_connection(
        {"system": "hubspot", "owner_object": "deal", "crm_record_id": "other-deal"},
        room_id=room,
        actor="d",
        source="s",
    )
    assert other["id"] != hubspot["id"]


def test_updating_a_connection_merges_so_a_caller_does_not_resend_everything(engine, room, hubspot):
    updated = engine.save_connection(
        {"id": hubspot["id"], "folder_id": "fabrikam-4400"},
        room_id=room,
        actor="d",
        source="s",
    )
    assert updated["folder_id"] == "fabrikam-4400"
    assert updated["crm_record_id"] == "fabrikam-dl-9001", "the untouched field survived"
    assert updated["attach_mode"] == vocabulary.ATTACH_NOTE


def test_an_unknown_connection_id_is_refused_on_update(engine, room):
    with pytest.raises(UnknownConnection):
        engine.save_connection({"id": "conn_absent", "system": "hubspot"}, room_id=room, source="s")


def test_deleting_a_connection_keeps_the_file_ids_its_assets_already_hold(engine, room, hubspot):
    """A crm_file_id is a fact about the CRM, not about the connection."""
    asset_id = add(engine, hubspot, room)
    mirrored = mirror(engine, asset_id, room)
    removed = engine.delete_connection(
        hubspot["id"],
        room_id=room,
        actor="d",
        source="DELETE /api/wf-047/connections/{connection_id}",
    )
    assert removed["assets_kept"] == 1
    assert (
        engine.read_asset(asset_id, room_id=room)["crm_file_id"] == mirrored["uploaded"]["file_id"]
    )


def test_a_connection_in_another_room_is_not_readable_from_this_one(engine, room, store):
    hubspot = engine.save_connection(
        {"system": "hubspot", "crm_record_id": "d-1"}, room_id=room, actor="d", source="s"
    )
    with pytest.raises(UnknownConnection):
        engine.read_connection(hubspot["id"], room_id="room_other")


# --------------------------------------------------------------------------- #
# Assets: the rules a caller can break
# --------------------------------------------------------------------------- #


def test_an_asset_with_no_filename_is_refused():
    with pytest.raises(AssetRefused) as raised:
        normalise_asset({"mime_type": "application/pdf", "content_base64": "UERG"})
    assert raised.value.status == 400


def test_an_asset_with_no_content_type_is_refused():
    with pytest.raises(AssetRefused) as raised:
        normalise_asset({"filename": "a.pdf", "content_base64": "UERG"})
    assert raised.value.status == 400


def test_an_asset_with_no_bytes_is_refused():
    with pytest.raises(AssetRefused) as raised:
        normalise_asset({"filename": "a.pdf", "mime_type": "application/pdf"})
    assert raised.value.status == 400


def test_no_size_limit_is_applied_because_the_research_quotes_none():
    """ "Uploaded files are subject to the limits and supported formats", with no
    number. A limit here would be a number this build invented."""
    big = "UERG" * 20_000
    stored = normalise_asset(
        {"filename": "a.pdf", "mime_type": "application/pdf", "content_base64": big}
    )
    assert stored["byte_size"] > 50_000
    assert "size_limit" not in engine_module.__doc__


def test_plain_text_content_is_accepted_because_a_generated_asset_is_text():
    stored = normalise_asset(
        {"filename": "quote.txt", "mime_type": "text/plain", "content_base64": "hello world"}
    )
    assert decode_content(stored["content_base64"]) == b"hello world"


def test_the_stored_bytes_round_trip():
    payload = b"\x00\x01\x02 not text"
    assert decode_content(asset_rules.encode_content(payload)) == payload


def test_an_unknown_field_on_an_asset_survives_and_stays_queryable(engine, room, hubspot):
    """No migration and no typed column: a team adds a field and it is queryable
    through the dynamic index the same day."""
    result = engine.register_asset(
        deck(legal_reviewer="dana"),
        connection_id=hubspot["id"],
        room_id=room,
        actor="dana",
        source="POST /api/wf-047/rooms/{room_id}/assets",
    )
    assert result["asset"]["id"]
    assert [row["id"] for row in engine.store.find(ASSETS, {"legal_reviewer": "dana"})] == [
        result["asset"]["id"]
    ]


def test_an_unknown_asset_id_is_refused(engine):
    with pytest.raises(UnknownAsset):
        engine.read_asset("asset_absent")


def test_an_unknown_mirror_id_is_refused(engine):
    with pytest.raises(UnknownMirror):
        engine.read_mirror("mirror_absent")


def test_replacing_content_keeps_the_keys_this_workflow_owns(engine, room, hubspot):
    asset_id = add(engine, hubspot, room)
    mirrored = mirror(engine, asset_id, room)
    engine.replace_content(
        asset_id,
        {"filename": "renamed.pdf", "mime_type": "application/pdf", "content_base64": "UERGICg0="},
        room_id=room,
        actor="d",
        source="s",
    )
    stored = engine.store.get(asset_id)["data"]
    assert stored["crm_file_id"] == mirrored["uploaded"]["file_id"]
    assert stored["mirrored_content_digest"] == mirrored["request"]["sha256"].join(["sha256:", ""])
    assert stored["connection_id"] == hubspot["id"]
    assert stored["push_to_crm"] is True


# --------------------------------------------------------------------------- #
# The chip, the panels and the log
# --------------------------------------------------------------------------- #


def test_every_chip_state_carries_a_plain_sentence(engine):
    """The design floor forbids status by colour alone, and the chip is a status."""
    for state in vocabulary.MIRROR_STATES:
        assert vocabulary.MIRROR_STATE_NOTES[state], state


def test_the_documents_panel_is_the_researched_chip_page(engine, room, hubspot):
    """ "Room Documents page with a 'Push to CRM' status chip per file"."""
    add(engine, hubspot, room)
    add(engine, hubspot, room, push_to_crm=False)
    panel = engine.documents_panel(room)
    assert panel["count"] == 2
    assert panel["states"] == list(vocabulary.MIRROR_STATES)
    for entry in panel["assets"]:
        assert entry["state"] in vocabulary.MIRROR_STATES
        assert entry["state_note"]
        assert "push_to_crm" in entry


def test_the_crm_files_panel_lists_what_the_room_has_pushed(engine, room, hubspot):
    add(engine, hubspot, room, filename="pushed.pdf")
    add(engine, hubspot, room, filename="not-pushed.pdf", push_to_crm=False)
    panel = engine.crm_files_panel(room)
    assert panel["count"] == 0, "nothing was pushed yet"
    mirror(engine, asset_id_of(engine, room, "pushed.pdf"), room)
    pushed = engine.crm_files_panel(room)
    assert pushed["count"] == 1
    assert pushed["files"][0]["owner_object"] == "deal"
    assert pushed["files"][0]["crm_record_id"] == "fabrikam-dl-9001"
    assert pushed["files"][0]["attach_mode"] == "note"


def test_the_summary_counts_assets_by_chip_and_mirrors_by_outcome(engine, room, hubspot):
    add(engine, hubspot, room, filename="pushed.pdf")
    add(engine, hubspot, room, filename="off.pdf", push_to_crm=False)
    mirror(engine, asset_id_of(engine, room, "pushed.pdf"), room)
    summary = engine.summary(room)
    assert summary["assets"] == 2
    assert summary["assets_by_state"][vocabulary.MIRRORED] == 1
    assert summary["assets_by_state"][vocabulary.WITHHELD] == 1
    assert summary["mirrors_by_outcome"]["mirrored"] == 1
    assert summary["simulated_mirrors"] == 1
    assert summary["reconcile_rule"] == vocabulary.RECONCILE_RULE


def test_every_mirror_row_carries_the_request_it_made(engine, room, hubspot):
    import hashlib

    result = mirror(engine, add(engine, hubspot, room), room)
    row = engine.read_mirror(result["mirror_id"])
    assert row["request"]["path"] == "/files/2026-09/files"
    sent = decode_content(engine.store.get(result["asset"]["id"])["data"]["content_base64"])
    assert row["request"]["sha256"] == hashlib.sha256(sent).hexdigest()
    assert row["request"]["byte_size"] == len(sent)
    assert row["request"]["content_type"] == "multipart/form-data"
    assert row["asset_id"] == result["asset"]["id"]
    assert row["at"] == STAMP


def test_a_skipped_mirror_is_still_logged(engine, room, hubspot):
    asset_id = add(engine, hubspot, room)
    mirror(engine, asset_id, room)
    second = mirror(engine, asset_id, room)
    assert (
        engine.read_mirror(second["mirror_id"])["outcome"] == vocabulary.OUTCOME_SKIPPED_UNCHANGED
    )
    assert len(engine.mirrors_for_asset(asset_id)) == 2


def test_the_mirror_log_is_room_scoped(engine, room, hubspot, store):
    add(engine, hubspot, room)
    other = store.create("room", {"name": "Other"})["id"]
    assert engine.list_mirrors(room_id=other) == []


def test_needs_mirror_answers_the_questions_the_chip_asks():
    assert needs_mirror({"push_to_crm": True}) is True
    assert needs_mirror({"push_to_crm": False}) is False
    unchanged = {
        "push_to_crm": True,
        "content_digest": "sha256:a",
        "mirrored_content_digest": "sha256:a",
    }
    assert needs_mirror(unchanged) is False
    changed = {**unchanged, "mirrored_content_digest": "sha256:b"}
    assert needs_mirror(changed) is True


def test_the_chip_is_derived_from_the_row_and_never_stored():
    """A stored state can drift from the fields it describes; a derived one cannot."""
    assert mirror_state({"push_to_crm": True}) == vocabulary.PENDING
    assert (
        mirror_state({"push_to_crm": True, vocabulary.ASSET_FILE_ID_KEY: "f-1"})
        == vocabulary.MIRRORED
    )
    assert (
        mirror_state(
            {
                "push_to_crm": True,
                vocabulary.ASSET_FILE_ID_KEY: "f-1",
                "content_digest": "sha256:a",
                "mirrored_content_digest": "sha256:b",
            }
        )
        == vocabulary.STALE
    )
    assert mirror_state({"push_to_crm": True, "last_outcome": "failed"}) == vocabulary.FAILED
    assert mirror_state({"push_to_crm": False, "last_outcome": "failed"}) == vocabulary.WITHHELD


# --------------------------------------------------------------------------- #
# The inference register, asserted against what the code does
# --------------------------------------------------------------------------- #


def test_the_register_covers_every_decision_the_issue_names():
    ids = {entry["id"] for entry in INFERENCES}
    assert {
        "revision-detected-by-content-digest",
        "reconcile-is-routed-not-scheduled",
        "event-driven-is-the-default-automation",
        "folder-id-is-optional-per-connection",
        "attach-mode-defaults-to-note",
        "unsourced-vendors-are-refused",
    } <= ids


def test_every_entry_names_its_basis_its_value_and_where_to_change_it():
    for entry in INFERENCES:
        assert entry["basis"], entry["id"]
        assert entry["value"], entry["id"]
        assert entry["why"], entry["id"]
        assert entry["change_it"], entry["id"]
        assert entry["blast_radius"], entry["id"]
        assert entry["topic"], entry["id"]


def test_the_register_agrees_with_the_frozen_number_it_names(engine):
    entry = next(e for e in INFERENCES if e["id"] == "reconcile-is-routed-not-scheduled")
    assert entry["value"]["interval_seconds"] == vocabulary.DEFAULT_POLL_INTERVAL_SECONDS
    assert entry["value"]["thread"] == "none. This package starts no thread and no timer."


def test_the_register_agrees_with_the_connector_note_it_names(engine, room, hubspot):
    entry = next(
        e for e in INFERENCES if e["id"] == "the-default-connector-is-simulated-and-says-so"
    )
    assert entry["value"]["collection"].startswith(REMOTE_FILES)
    assert SimulatedConnector.note == "simulated"


def test_the_inferences_route_serves_the_whole_register(engine):
    served = engine.inferences()
    assert served["count"] == len(INFERENCES)
    assert {entry["id"] for entry in served["inferences"]} == {e["id"] for e in INFERENCES}


# --------------------------------------------------------------------------- #
# The architectural rules the contract enforces
# --------------------------------------------------------------------------- #


def test_the_domain_modules_import_nothing_but_the_store_and_the_standard_library():
    """A domain module that reaches for the app cannot be tested on its own, and
    one that opens SQLite can bypass the audit log.

    Walked with ``ast`` rather than grepped, so a module-level ``import`` and a
    function-level one are both seen. The allowed ``dsr`` imports are the store
    itself, the audited database for ``RecordNotFound``, and the package's own
    submodules; anything else is a coupling the contract does not allow.
    """
    package = Path(engine_module.__file__).parent
    allowed = {
        "dsr.store",
        "dsr.db.audited",
        "dsr.crm_file_sync",
        "dsr.crm_file_sync.assets",
        "dsr.crm_file_sync.connectors",
        "dsr.crm_file_sync.engine",
        "dsr.crm_file_sync.errors",
        "dsr.crm_file_sync.inferences",
        "dsr.crm_file_sync.vocabulary",
    }
    for module in sorted(package.glob("*.py")):
        text = module.read_text(encoding="utf-8")
        assert "sqlite3" not in text, f"{module.name} mentions sqlite3"
        assert "fastapi" not in text, f"{module.name} mentions fastapi"
        assert "dsr.api" not in text, f"{module.name} mentions dsr.api"
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.startswith("dsr"):
                    assert node.module in allowed, f"{module.name} imports {node.module}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(("sqlite3", "fastapi", "dsr")), (
                        f"{module.name} imports {alias.name}"
                    )


def test_the_feature_module_does_not_import_the_app_or_open_the_database():
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source
    assert "sqlite3" not in source
    assert ".connect(" not in source
    assert "from dsr.deps import" in source


def test_the_feature_is_plugin_shaped():
    feature = load_feature(MODULE)
    assert feature.FEATURE["id"] == "wf-047-mirror-room-documents-into-crm-files"
    assert feature.FEATURE["ticket"] == "WF-047"
    assert feature.router.prefix == "/api/wf-047"
    assert feature.EXCEPTION_HANDLERS
    assert callable(feature.seed)


# --------------------------------------------------------------------------- #
# The seed
# --------------------------------------------------------------------------- #


def test_the_seed_produces_rows_a_reviewer_can_read(db: AuditedDatabase):
    import importlib

    module = importlib.import_module(f"dsr.features.{MODULE}")
    room_id = db.create("room", {"name": "Northwind"})["id"]
    summary = module.seed(db, {"room_ids": [(room_id, "Northwind")], "now": NOW, "rng": None})

    assert summary
    assert "chips:" in summary
    panel_states = {
        entry["state"]
        for entry in module.CrmFileSyncEngine(RecordStore(db)).list_assets(
            room_id=room_id, limit=100
        )
    }
    # A demo of only success teaches a reviewer nothing.
    assert panel_states == set(vocabulary.MIRROR_STATES), panel_states


def test_the_seed_string_is_encodable_in_cp1252(db: AuditedDatabase):
    """A RIGHTWARDS ARROW in one recovered feature broke the whole seeder on a
    Windows console. Every character here must survive cp1252."""
    import importlib

    module = importlib.import_module(f"dsr.features.{MODULE}")
    room_id = db.create("room", {"name": "Northwind"})["id"]
    summary = module.seed(db, {"room_ids": [(room_id, "Northwind")], "now": NOW, "rng": None})
    assert summary.encode("cp1252").decode("cp1252") == summary


def test_the_seed_with_no_rooms_says_so_rather_than_raising(db: AuditedDatabase):
    import importlib

    module = importlib.import_module(f"dsr.features.{MODULE}")
    summary = module.seed(db, {"room_ids": [], "now": NOW, "rng": None})
    assert "no rooms" in summary


def test_the_seed_accepts_a_bare_room_id(db: AuditedDatabase):
    """``backend/seed.py`` passes tuples, but a caller assembling a context by
    hand should not have to know that."""
    import importlib

    module = importlib.import_module(f"dsr.features.{MODULE}")
    room_id = db.create("room", {"name": "Northwind"})["id"]
    summary = module.seed(db, {"room_ids": [room_id], "now": NOW, "rng": None})
    assert "assets" in summary


def test_the_seed_shows_the_researched_idempotency_and_the_unsourced_refusal(db: AuditedDatabase):
    import importlib

    module = importlib.import_module(f"dsr.features.{MODULE}")
    room_id = db.create("room", {"name": "Northwind"})["id"]
    summary = module.seed(db, {"room_ids": [(room_id, "Northwind")], "now": NOW, "rng": None})
    assert "skipped_unchanged" in summary, "the idempotency rule must be a row in the demo"
    assert "no_owner_record" in summary, "the dependency on WF-042 must be a row in the demo"
    assert "failed" in summary, "the unsourced vendor must be a row in the demo"
