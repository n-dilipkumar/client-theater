"""Tests for the room document library (WF-003).

These exercise the library directly, so a failure names the rule that broke
rather than an HTTP status. The API tests in ``test_documents_api.py`` cover
the same rules from outside the process.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from dsr.db.audited import AuditedDatabase
from dsr.documents import (
    FOLDER,
    GALLERY_SLOTS,
    DocumentLibrary,
    DocumentNotFound,
    InvalidDocument,
    InvalidTransition,
    PermissionDenied,
    RoomNotFound,
    guess_format,
)
from dsr.permissions import CONTENT_CONTRIBUTOR, INSTANCE_ADMIN, ROOM_COLLABORATOR, VIEWER
from dsr.store import RecordStore

CONTRIBUTOR = {"actor": "sam", "role": CONTENT_CONTRIBUTOR}
COLLABORATOR = {"actor": "dana", "role": ROOM_COLLABORATOR}

#: Every write names the route that served it, because ``source`` is a required
#: argument on the library. In production the router supplies the path it
#: actually mounted; a test is a caller too, and it names one from here.
SOURCE = "POST /api/wf-003/rooms/{room_id}/documents (test)"


@pytest.fixture()
def library():
    db = AuditedDatabase(":memory:", actor="test")
    store = RecordStore(db)
    lib = DocumentLibrary(store)
    lib.room = store.create("room", {"name": "Northwind", "status": "active"}, actor="dana")
    yield lib
    db.close()


def add(lib, name="Proposal.pdf", **kwargs):
    who = kwargs.pop("who", CONTRIBUTOR)
    return lib.add_document(lib.room["id"], {"name": name, **kwargs}, **who, source=SOURCE)


def archive(lib):
    lib.store.update(lib.room["id"], {"status": "archived"}, actor="dana")


# -- the folder is the view ------------------------------------------------- #


def test_new_document_lands_in_the_documents_folder(library):
    row = add(library)
    assert row["library"]["folder"] == FOLDER
    assert row["data"]["folder"] == FOLDER
    assert row["room_id"] == library.room["id"]


def test_documents_from_another_room_are_not_listed(library):
    other = library.store.create("room", {"name": "Contoso"}, actor="sam")
    library.add_document(library.room["id"], {"name": "A.pdf"}, **CONTRIBUTOR, source=SOURCE)
    library.add_document(other["id"], {"name": "B.pdf"}, **CONTRIBUTOR, source=SOURCE)

    listed = library.list_documents(library.room["id"])

    assert [d["library"]["name"] for d in listed["documents"]] == ["A.pdf"]


def test_assets_stored_outside_the_folder_do_not_appear(library):
    # Sourced: images added through other fragments "are stored outside the
    # room's documents folder and don't appear in the Documents view".
    library.store.create(
        "document",
        {"name": "hero.png", "folder": "page-assets", "status": "published"},
        room_id=library.room["id"],
        actor="dana",
    )

    assert library.list_documents(library.room["id"])["total"] == 0


def test_a_document_can_be_moved_out_of_the_folder_by_updating_it(library):
    row = add(library)
    library.update_document(
        library.room["id"], row["id"], {"folder": "archive"}, **CONTRIBUTOR, source=SOURCE
    )

    assert library.list_documents(library.room["id"])["total"] == 0


def test_unknown_room_is_reported_not_guessed(library):
    with pytest.raises(RoomNotFound):
        library.list_documents("room_missing")


# -- row metadata ------------------------------------------------------------ #


def test_row_reports_name_status_and_last_modifier(library):
    row = add(library)
    library.update_document(library.room["id"], row["id"], {"title": "Revised"}, **COLLABORATOR, source=SOURCE)

    refreshed = library.get_document(library.room["id"], row["id"])
    # Sourced: each document shows "who last modified it, and its workflow
    # status".
    assert refreshed["library"]["last_modified_by"] == "dana"
    assert refreshed["library"]["uploaded_by"] == "sam"
    assert refreshed["library"]["status"] == "draft"
    assert refreshed["library"]["title"] == "Revised"


def test_thumbnail_is_pending_until_a_url_exists(library):
    # Sourced: "thumbnail rendering is asynchronous and may lag the upload by
    # several seconds. Empty when no thumbnail has been generated yet."
    pending = add(library, name="Deck.pptx")
    assert pending["library"]["thumbnail"] == {"state": "pending", "url": None}

    ready = library.update_document(
        library.room["id"], pending["id"], {"thumbnail_url": "https://cdn/x.png"},
        **CONTRIBUTOR, source=SOURCE,
    )
    assert ready["library"]["thumbnail"]["state"] == "ready"
    assert ready["library"]["thumbnail"]["url"] == "https://cdn/x.png"


def test_format_is_guessed_from_the_file_name_and_can_be_overridden(library):
    assert add(library, name="Deck.pptx")["library"]["format"] == "deck"
    assert add(library, name="Photo.png")["library"]["format"] == "image"
    assert add(library, name="Notes", format="sheet")["library"]["format"] == "sheet"
    assert guess_format("Archive.tar.gz") == "archive"
    assert guess_format("NoExtension") == "file"


def test_document_without_a_name_is_rejected(library):
    with pytest.raises(InvalidDocument):
        library.add_document(library.room["id"], {"description": "no name"}, **CONTRIBUTOR, source=SOURCE)


# -- expiry ------------------------------------------------------------------ #


def test_expiry_must_be_in_the_future_when_set(library):
    # Sourced: expiresAt "Must be a future date when set."
    past = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    with pytest.raises(InvalidDocument):
        add(library, expires_at=past)


def test_a_future_expiry_is_stored_and_not_yet_expired(library):
    future = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
    row = add(library, expires_at=future)
    assert row["library"]["expires_at"] == future
    assert row["library"]["expired"] is False


def test_unparseable_expiry_is_rejected(library):
    with pytest.raises(InvalidDocument):
        add(library, expires_at="next tuesday")


def test_a_document_past_its_expiry_reads_as_expired(library):
    future = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
    row = add(library, expires_at=future)
    # Move the stored date backwards; the derivation is computed on read, so
    # nothing has to be kept in step by a job.
    library.store.update(row["id"], {"expires_at": "2000-01-01"}, actor="clock")
    assert library.get_document(library.room["id"], row["id"])["library"]["expired"] is True


# -- workflow status --------------------------------------------------------- #


def test_new_documents_start_as_draft(library):
    assert add(library)["library"]["status"] == "draft"


def test_draft_can_be_published(library):
    row = add(library)
    published = library.set_status(library.room["id"], row["id"], "published", **CONTRIBUTOR, source=SOURCE)
    assert published["library"]["status"] == "published"


def test_published_cannot_go_straight_back_to_review(library):
    row = add(library)
    library.set_status(library.room["id"], row["id"], "published", **CONTRIBUTOR, source=SOURCE)
    with pytest.raises(InvalidTransition):
        library.set_status(library.room["id"], row["id"], "in_review", **CONTRIBUTOR, source=SOURCE)


def test_viewer_cannot_change_status(library):
    row = add(library)
    with pytest.raises(PermissionDenied):
        library.set_status(
            library.room["id"], row["id"], "published", actor="buyer", role=VIEWER, source=SOURCE
        )


def test_a_teams_own_status_is_accepted_and_rendered(library):
    # The status column is free-form: a team adds a state without a migration
    # and without asking us to extend the vocabulary.
    row = library.add_document(
        library.room["id"], {"name": "MSA.pdf", "status": "legal_review"},
        **CONTRIBUTOR, source=SOURCE,
    )
    assert row["library"]["status"] == "legal_review"
    assert row["library"]["status_known"] is False

    found = library.list_documents(library.room["id"], status="legal_review")
    assert [d["id"] for d in found["documents"]] == [row["id"]]


def test_an_unknown_current_status_may_move_anywhere(library):
    # We police our own state machine, not a team's.
    row = library.add_document(
        library.room["id"], {"name": "MSA.pdf", "status": "legal_review"},
        **CONTRIBUTOR, source=SOURCE,
    )
    moved = library.set_status(library.room["id"], row["id"], "published", **CONTRIBUTOR, source=SOURCE)
    assert moved["library"]["status"] == "published"


def test_status_filter_resolves_through_the_dynamic_index(library):
    add(library, name="A.pdf")
    second = add(library, name="B.pdf")
    library.set_status(library.room["id"], second["id"], "published", **CONTRIBUTOR, source=SOURCE)

    published = library.list_documents(library.room["id"], status="published")
    drafts = library.list_documents(library.room["id"], status="draft")

    assert [d["library"]["name"] for d in published["documents"]] == ["B.pdf"]
    assert [d["library"]["name"] for d in drafts["documents"]] == ["A.pdf"]


def test_workflow_vocabulary_is_published_for_clients(library):
    vocabulary = library.workflow_vocabulary()
    assert vocabulary["default"] == "draft"
    assert set(vocabulary["known"]) == {"draft", "in_review", "published"}
    assert "published" in vocabulary["transitions"]["draft"]


# -- the upload gate --------------------------------------------------------- #


@pytest.mark.parametrize("role", [CONTENT_CONTRIBUTOR, ROOM_COLLABORATOR, INSTANCE_ADMIN])
def test_uploader_roles_are_accepted(library, role):
    row = library.add_document(
        library.room["id"], {"name": "A.pdf"}, actor="x", role=role, source=SOURCE
    )
    assert row["library"]["name"] == "A.pdf"


def test_viewer_cannot_add_to_the_library(library):
    with pytest.raises(PermissionDenied):
        library.add_document(
            library.room["id"], {"name": "A.pdf"}, actor="buyer", role=VIEWER, source=SOURCE
        )


def test_archived_room_refuses_uploads_from_contributors(library):
    archive(library)
    with pytest.raises(PermissionDenied):
        add(library)


def test_archived_room_still_accepts_an_instance_admin(library):
    archive(library)
    row = add(library, who={"actor": "root", "role": INSTANCE_ADMIN})
    assert row["library"]["name"] == "Proposal.pdf"


# -- the delete gate --------------------------------------------------------- #


def test_uploader_can_delete_their_own_document(library):
    row = add(library)
    library.remove_document(library.room["id"], row["id"], **CONTRIBUTOR, source=SOURCE)
    assert library.list_documents(library.room["id"])["total"] == 0


def test_collaborator_cannot_delete_someone_elses_document(library):
    # Sourced: "neither can delete a document someone else uploaded".
    row = add(library, who=COLLABORATOR)
    with pytest.raises(PermissionDenied):
        library.remove_document(library.room["id"], row["id"], **CONTRIBUTOR, source=SOURCE)


def test_instance_admin_can_delete_anyones_document(library):
    row = add(library, who=COLLABORATOR)
    library.remove_document(
        library.room["id"], row["id"], actor="root", role=INSTANCE_ADMIN, source=SOURCE
    )
    assert library.list_documents(library.room["id"])["total"] == 0


def test_a_document_from_another_room_cannot_be_deleted_through_this_one(library):
    other = library.store.create("room", {"name": "Contoso"}, actor="sam")
    foreign = library.add_document(other["id"], {"name": "Secret.pdf"}, **CONTRIBUTOR, source=SOURCE)
    with pytest.raises(DocumentNotFound):
        library.remove_document(library.room["id"], foreign["id"], **CONTRIBUTOR, source=SOURCE)


def test_deleting_is_audited(library):
    row = add(library)
    library.remove_document(library.room["id"], row["id"], **CONTRIBUTOR, source=SOURCE)
    actions = [e["action"] for e in library.store.audit(record_id=row["id"])]
    assert actions == ["delete", "insert"]


# -- schema flexibility ------------------------------------------------------ #


def test_a_team_field_needs_no_migration_and_is_queryable(library):
    row = library.add_document(
        library.room["id"],
        {"name": "Contract.pdf", "legal": {"reviewer": "priya", "value": 250000}},
        **CONTRIBUTOR,
        source=SOURCE,
    )

    assert row["data"]["legal"] == {"reviewer": "priya", "value": 250000}
    # The dynamic index makes a brand-new nested path filterable immediately.
    ids = library.store.find("document", {"legal.reviewer": "priya"})
    assert [r["id"] for r in ids] == [row["id"]]


def test_the_row_returns_the_raw_payload_untouched(library):
    row = library.add_document(
        library.room["id"], {"name": "X.pdf", "vendor": {"name": "Globex"}},
        **CONTRIBUTOR, source=SOURCE,
    )
    assert row["data"]["vendor"] == {"name": "Globex"}
    assert row["library"]["name"] == "X.pdf"


def test_the_uploader_cannot_be_rewritten_through_a_patch(library):
    # The delete gate is computed from uploaded_by, so it must not be
    # patchable into a value that grants the right to delete.
    row = add(library, who=COLLABORATOR)
    patched = library.update_document(
        library.room["id"], row["id"], {"uploaded_by": "sam"}, **CONTRIBUTOR, source=SOURCE
    )
    assert patched["library"]["uploaded_by"] == "dana"
    with pytest.raises(PermissionDenied):
        library.remove_document(library.room["id"], row["id"], **CONTRIBUTOR, source=SOURCE)


# -- search ------------------------------------------------------------------ #


def test_search_matches_name_title_and_description(library):
    add(library, name="Security-Pack.pdf", description="SOC 2 evidence")
    add(library, name="Pricing.pdf")

    assert library.list_documents(library.room["id"], search="security")["total"] == 1
    assert library.list_documents(library.room["id"], search="SOC 2")["total"] == 1
    assert library.list_documents(library.room["id"], search="pricing")["total"] == 1
    assert library.list_documents(library.room["id"], search="absent")["total"] == 0


def test_search_and_status_compose(library):
    first = add(library, name="Draft-Proposal.pdf")
    library.set_status(library.room["id"], first["id"], "published", **CONTRIBUTOR, source=SOURCE)
    add(library, name="Draft-Roadmap.pdf")

    found = library.list_documents(
        library.room["id"], search="proposal", status="published"
    )
    assert [d["id"] for d in found["documents"]] == [first["id"]]


# -- Document Gallery Block -------------------------------------------------- #


def test_gallery_block_takes_at_most_four_documents(library):
    rows = [add(library, name=f"D{i}.pdf") for i in range(5)]

    with pytest.raises(InvalidDocument):
        library.save_gallery(
            library.room["id"], {"documents": [r["id"] for r in rows]},
            **CONTRIBUTOR, source=SOURCE,
        )

    block = library.save_gallery(
        library.room["id"], {"documents": [r["id"] for r in rows[:GALLERY_SLOTS]]},
        **CONTRIBUTOR, source=SOURCE,
    )
    assert len(block["documents"]) == GALLERY_SLOTS
    assert block["empty_slots"] == 0


def test_gallery_only_accepts_documents_from_the_same_room(library):
    other = library.store.create("room", {"name": "Contoso"}, actor="sam")
    foreign = library.add_document(other["id"], {"name": "Foreign.pdf"}, **CONTRIBUTOR, source=SOURCE)

    with pytest.raises(DocumentNotFound):
        library.save_gallery(library.room["id"], {"documents": [foreign["id"]]}, **CONTRIBUTOR, source=SOURCE)


def test_gallery_rejects_documents_stored_outside_the_folder(library):
    outside = library.store.create(
        "document",
        {"name": "hero.png", "folder": "page-assets"},
        room_id=library.room["id"],
        actor="dana",
    )
    with pytest.raises(InvalidDocument):
        library.save_gallery(library.room["id"], {"documents": [outside["id"]]}, **CONTRIBUTOR, source=SOURCE)


def test_gallery_block_is_replaced_in_place_and_audited(library):
    rows = [add(library, name=f"D{i}.pdf") for i in range(3)]
    block = library.save_gallery(
        library.room["id"], {"label": "Overview", "documents": [r["id"] for r in rows]},
        **CONTRIBUTOR, source=SOURCE,
    )
    updated = library.save_gallery(
        library.room["id"],
        {"documents": [rows[0]["id"]]},
        block_id=block["id"],
        **CONTRIBUTOR,
        source=SOURCE,
    )

    assert updated["id"] == block["id"]
    assert [d["id"] for d in updated["documents"]] == [rows[0]["id"]]
    assert library.list_galleries(library.room["id"])["count"] == 1
    assert [e["action"] for e in library.store.audit(record_id=block["id"])] == [
        "update",
        "insert",
    ]


def test_gallery_skips_duplicate_and_blank_selectors(library):
    row = add(library)
    block = library.save_gallery(
        library.room["id"], {"documents": [row["id"], "", row["id"]]},
        **CONTRIBUTOR, source=SOURCE,
    )
    assert [d["id"] for d in block["documents"]] == [row["id"]]


def test_gallery_documents_open_in_a_new_tab(library):
    # Sourced: the buyer opens the document "in a new tab".
    row = add(library)
    block = library.save_gallery(library.room["id"], {"documents": [row["id"]]}, **CONTRIBUTOR, source=SOURCE)
    assert block["open_in_new_tab"] is True


def test_viewer_cannot_edit_a_gallery_block(library):
    with pytest.raises(PermissionDenied):
        library.save_gallery(
            library.room["id"], {"documents": []}, actor="buyer", role=VIEWER, source=SOURCE
        )


# -- capabilities surfaced to the client ------------------------------------- #


def test_list_response_carries_the_caller_capabilities(library):
    body = library.list_documents(library.room["id"], role=VIEWER, actor="buyer")
    assert body["capabilities"]["can_upload"] is False
    assert body["capabilities"]["role_label"] == "Viewer"


def test_row_permissions_reflect_who_owns_the_document(library):
    row = add(library, who=COLLABORATOR)
    as_owner = library.get_document(library.room["id"], row["id"], role=CONTENT_CONTRIBUTOR, actor="dana")
    as_other = library.get_document(library.room["id"], row["id"], role=CONTENT_CONTRIBUTOR, actor="sam")
    assert as_owner["permissions"]["can_delete"] is True
    assert as_other["permissions"]["can_delete"] is False


def test_pagination_reports_the_unfiltered_total(library):
    for index in range(5):
        add(library, name=f"D{index}.pdf")

    page = library.list_documents(library.room["id"], limit=2, offset=0)
    assert page["count"] == 2
    assert page["total"] == 5
