"""Tests for the role gate (WF-003).

The rules under test are the documented ones: the *New* button belongs to Room
Collaborators and Content Contributors, nobody below instance administrator
can delete a document someone else uploaded, and an archived room closes
uploads to everyone but an instance administrator.
"""

from __future__ import annotations

import pytest
from dsr.permissions import (
    CONTENT_CONTRIBUTOR,
    INSTANCE_ADMIN,
    ROOM_COLLABORATOR,
    VIEWER,
    capabilities,
    normalise_role,
    role_vocabulary,
)

# -- vocabulary -------------------------------------------------------------- #


def test_unknown_role_fails_closed_to_viewer():
    assert normalise_role("superuser") == VIEWER
    assert normalise_role(None) == VIEWER
    assert normalise_role(42) == VIEWER


def test_role_names_are_normalised_not_rejected():
    assert normalise_role("Room Collaborator") == ROOM_COLLABORATOR
    assert normalise_role("content-contributor") == CONTENT_CONTRIBUTOR
    assert normalise_role("  INSTANCE_ADMIN ") == INSTANCE_ADMIN


def test_vocabulary_exposes_every_role_with_a_label():
    ids = [entry["id"] for entry in role_vocabulary()]
    assert ids == [VIEWER, CONTENT_CONTRIBUTOR, ROOM_COLLABORATOR, INSTANCE_ADMIN]
    assert all(entry["label"] for entry in role_vocabulary())


# -- upload gate ------------------------------------------------------------- #


@pytest.mark.parametrize("role", [CONTENT_CONTRIBUTOR, ROOM_COLLABORATOR, INSTANCE_ADMIN])
def test_contributors_collaborators_and_admins_can_upload(role):
    assert capabilities(role).can_upload is True


def test_viewer_cannot_upload():
    # Sourced: Viewers "can view documents and add comments, but cannot upload
    # documents."
    assert capabilities(VIEWER).can_upload is False


def test_missing_role_cannot_upload():
    # A caller that presents no role gets the closed default, not an open door.
    assert capabilities(None).can_upload is False


# -- delete gate ------------------------------------------------------------- #


def test_contributor_cannot_delete_someone_elses_document():
    gate = capabilities(CONTENT_CONTRIBUTOR, uploaded_by="dana", actor="sam")
    assert gate.can_delete is False
    assert gate.can_delete_others is False


def test_uploader_can_delete_their_own_document():
    gate = capabilities(CONTENT_CONTRIBUTOR, uploaded_by="sam", actor="sam")
    assert gate.can_delete is True


def test_instance_admin_can_delete_anyones_document():
    gate = capabilities(INSTANCE_ADMIN, uploaded_by="dana", actor="root")
    assert gate.can_delete is True
    assert gate.can_delete_others is True


def test_viewer_cannot_delete_even_their_own_upload():
    # A viewer cannot upload, so treating them as a deletable uploader would be
    # a way around the upload gate.
    assert capabilities(VIEWER, uploaded_by="sam", actor="sam").can_delete is False


def test_delete_is_refused_without_an_actor():
    gate = capabilities(CONTENT_CONTRIBUTOR, uploaded_by="sam", actor=None)
    assert gate.can_delete is False


# -- archived rooms ---------------------------------------------------------- #


def test_archived_room_closes_uploads_for_contributors():
    gate = capabilities(CONTENT_CONTRIBUTOR, room_status="archived")
    assert gate.can_upload is False


def test_archived_room_keeps_the_new_button_for_instance_admins():
    # Sourced: "In an archived room, only instance administrators see the New
    # button."
    gate = capabilities(INSTANCE_ADMIN, room_status="archived")
    assert gate.can_upload is True


def test_archived_room_is_read_only_for_everyone_else():
    # Inference: the research lists archived-room read-only behaviour without
    # enumerating which writes close.
    gate = capabilities(ROOM_COLLABORATOR, room_status="archived")
    assert gate.can_manage_status is False
    assert gate.can_delete is False
    assert gate.can_view is True


def test_unknown_room_status_is_treated_as_active():
    # A team with its own lifecycle states must not lock itself out.
    gate = capabilities(CONTENT_CONTRIBUTOR, room_status="quiet_hours")
    assert gate.can_upload is True


def test_capabilities_serialise_for_the_client():
    payload = capabilities(CONTENT_CONTRIBUTOR, room_status="active").to_dict()
    assert payload["role"] == CONTENT_CONTRIBUTOR
    assert payload["role_label"] == "Content Contributor"
    assert payload["can_upload"] is True
