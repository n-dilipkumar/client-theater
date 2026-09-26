"""Tests for WF-004: inviting buyers with a role and an access expiry.

The rules under test are the ones the research documents, not the ones that
happen to fall out of the implementation:

* an invitation has a 48-hour acceptance window;
* access ends at the end of the expiration date in UTC, silently;
* an invitee who already belongs to the room joins on send, not on accept;
* a Viewer cannot share, and only the owner may assign Room Collaborator;
* the Owner can never be changed or removed;
* access lapsing within seven days is surfaced as a row label and a banner.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from dsr.access import (
    ACCESS,
    DEFAULT_ROLE,
    EXPIRING_SOON_DAYS,
    INVITATIONS,
    INVITATION_TTL_HOURS,
    OWNER,
    AccessDenied,
    AccessInvalid,
    AccessMissing,
    AccessService,
    ConfirmationRequired,
    assignable_roles,
    can_share,
    end_of_day_utc,
    normalise_date,
    normalise_email,
    normalise_emails,
    role_vocabulary,
)
from dsr.db.audited import AuditedDatabase
from dsr.store import RecordStore

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path):
    database = AuditedDatabase(tmp_path / "wf004.db")
    yield database
    database.close()


@pytest.fixture()
def service(db):
    return AccessService(RecordStore(db), clock=lambda: NOW)


@pytest.fixture()
def room(service):
    return service.store.create("room", {"name": "Northwind Evaluation", "owner": "dana"})


def _grant(service, room_id, principal, role=DEFAULT_ROLE, access_valid_until=None, **extra):
    return service.store.create(
        ACCESS,
        {
            "principal": principal,
            "principal_type": "email",
            "role": role,
            "access_valid_until": access_valid_until,
            "source": "manual",
            **extra,
        },
        room_id=room_id,
        actor="dana",
        source="test",
    )


# -- role vocabulary and the delegation rule --------------------------------- #


def test_three_documented_roles_are_offered():
    ids = [role["id"] for role in role_vocabulary(OWNER)]
    assert ids == ["room_collaborator", "content_contributor", "viewer"]


def test_default_role_is_viewer():
    assert DEFAULT_ROLE == "viewer"


def test_each_role_carries_its_documented_description():
    descriptions = {role["id"]: role["description"] for role in role_vocabulary(OWNER)}
    assert "share the room" in descriptions["room_collaborator"]
    assert "upload documents" in descriptions["content_contributor"]
    assert "cannot upload documents" in descriptions["viewer"]


def test_only_the_owner_may_assign_room_collaborator():
    assert assignable_roles(OWNER) == ["room_collaborator", "content_contributor", "viewer"]
    assert "room_collaborator" not in assignable_roles("room_collaborator")
    assert "room_collaborator" not in assignable_roles("content_contributor")


def test_collaborators_and_contributors_assign_contributor_and_viewer():
    for role in ("room_collaborator", "content_contributor"):
        assert assignable_roles(role) == ["content_contributor", "viewer"]


def test_viewers_may_not_share():
    assert can_share("room_collaborator") is True
    assert can_share("content_contributor") is True
    assert can_share("viewer") is False
    assert can_share(None) is False


def test_vocabulary_marks_which_roles_the_actor_may_assign():
    viewer_view = {r["id"]: r["assignable"] for r in role_vocabulary("viewer")}
    assert viewer_view == {"room_collaborator": False, "content_contributor": False, "viewer": False}

    owner_view = {r["id"]: r["assignable"] for r in role_vocabulary(OWNER)}
    assert all(owner_view.values())


# -- addresses and dates ------------------------------------------------------ #


def test_email_is_normalised_to_lowercase():
    assert normalise_email("  A.Buyer@Northwind.Example ") == "a.buyer@northwind.example"


@pytest.mark.parametrize("bad", ["", "not-an-email", "a@b", "a b@example.com", "@example.com"])
def test_invalid_addresses_are_refused(bad):
    with pytest.raises(AccessInvalid):
        normalise_email(bad)


def test_duplicate_addresses_collapse_in_order():
    assert normalise_emails(
        ["B@Example.com", "a@example.com", "b@example.COM", "c@example.com"]
    ) == ["b@example.com", "a@example.com", "c@example.com"]


def test_a_bare_string_is_accepted_as_one_address():
    assert normalise_emails("a@example.com") == ["a@example.com"]


def test_an_empty_list_is_refused():
    with pytest.raises(AccessInvalid):
        normalise_emails([])


def test_dates_round_trip_and_blank_means_no_expiration():
    assert normalise_date("2026-12-31") == "2026-12-31"
    assert normalise_date(None) is None
    assert normalise_date("") is None


def test_a_malformed_date_is_refused():
    with pytest.raises(AccessInvalid):
        normalise_date("31/12/2026")


def test_access_ends_at_the_end_of_the_expiration_date_in_utc():
    assert end_of_day_utc("2026-12-31") == datetime(
        2026, 12, 31, 23, 59, 59, 999000, tzinfo=timezone.utc
    )
    assert end_of_day_utc(None) is None


# -- sending an invitation --------------------------------------------------- #


def test_invite_creates_one_invitation_per_address(service, room):
    result = service.invite(
        room["id"], ["a@example.com", "b@example.com"], actor="dana"
    )

    assert result["invites"] == 2
    assert result["role"] == DEFAULT_ROLE
    assert result["pending"] == ["a@example.com", "b@example.com"]
    invitations = service.store.list(INVITATIONS, room_id=room["id"])
    assert sorted(i["data"]["email"] for i in invitations) == ["a@example.com", "b@example.com"]
    assert {i["data"]["state"] for i in invitations} == {"pending"}


def test_invite_defaults_to_the_viewer_role(service, room):
    service.invite(room["id"], ["a@example.com"], actor="dana")

    invitation = service.store.list(INVITATIONS, room_id=room["id"])[0]
    assert invitation["data"]["role"] == "viewer"


def test_one_role_and_one_expiry_apply_to_the_whole_invitation(service, room):
    service.invite(
        room["id"],
        ["a@example.com", "b@example.com", "c@example.com"],
        role="content_contributor",
        access_valid_until="2027-01-31",
        actor="dana",
    )

    roles = {i["data"]["role"] for i in service.store.list(INVITATIONS, room_id=room["id"])}
    dates = {i["data"]["access_valid_until"] for i in service.store.list(INVITATIONS, room_id=room["id"])}
    assert roles == {"content_contributor"}
    assert dates == {"2027-01-31"}


def test_invitation_carries_a_48_hour_acceptance_window(service, room):
    service.invite(room["id"], ["a@example.com"], actor="dana")

    invitation = service.store.list(INVITATIONS, room_id=room["id"])[0]
    sent = datetime.fromisoformat(invitation["data"]["sent_at"].replace("Z", "+00:00"))
    deadline = datetime.fromisoformat(invitation["data"]["expires_at"].replace("Z", "+00:00"))

    assert INVITATION_TTL_HOURS == 48
    assert deadline - sent == timedelta(hours=48)


def test_invite_rejects_an_expiry_that_has_already_passed(service, room):
    with pytest.raises(AccessInvalid, match="in the past"):
        service.invite(room["id"], ["a@example.com"], access_valid_until="2020-01-01", actor="dana")


def test_invite_rejects_an_unknown_role(service, room):
    with pytest.raises(AccessInvalid, match="unknown role"):
        service.invite(room["id"], ["a@example.com"], role="superuser", actor="dana")


def test_invite_confirmation_message_states_the_role_and_window(service, room):
    result = service.invite(
        room["id"], ["a@example.com"], role="content_contributor", actor="dana"
    )

    assert "1 invitation sent as Content Contributor" in result["message"]
    assert "48 hours" in result["message"]


# -- who may invite ----------------------------------------------------------- #


def test_the_room_owner_may_share(service, room):
    assert service.invite(room["id"], ["a@example.com"], actor="dana")["invites"] == 1


def test_a_viewer_may_not_share(service, room):
    _grant(service, room["id"], "watcher@example.com", role="viewer")

    with pytest.raises(AccessDenied, match="cannot share"):
        service.invite(room["id"], ["a@example.com"], actor="watcher@example.com")


def test_someone_with_no_access_may_not_share(service, room):
    with pytest.raises(AccessDenied, match="cannot share"):
        service.invite(room["id"], ["a@example.com"], actor="stranger@example.com")


def test_a_contributor_may_not_assign_room_collaborator(service, room):
    _grant(service, room["id"], "lead@example.com", role="content_contributor")

    with pytest.raises(AccessDenied, match="only the room owner"):
        service.invite(room["id"], ["a@example.com"], role="room_collaborator", actor="lead@example.com")


def test_a_contributor_may_assign_viewer_and_contributor(service, room):
    _grant(service, room["id"], "lead@example.com", role="content_contributor")

    for role in ("viewer", "content_contributor"):
        result = service.invite(room["id"], [f"{role}@example.com"], role=role, actor="lead@example.com")
        assert result["invites"] == 1


def test_the_owner_may_assign_room_collaborator(service, room):
    assert service.invite(
        room["id"], ["peer@example.com"], role="room_collaborator", actor="dana"
    )["invites"] == 1


# -- accepting an invitation -------------------------------------------------- #


def test_accepting_creates_the_access_grant(service, room):
    service.invite(room["id"], ["a@example.com"], role="content_contributor", actor="dana")
    invitation = service.store.list(INVITATIONS, room_id=room["id"])[0]

    result = service.accept(invitation["id"], actor="a@example.com")

    grant = result["access"]
    assert grant["data"]["principal"] == "a@example.com"
    assert grant["data"]["role"] == "content_contributor"
    assert grant["data"]["joined_immediately"] is False
    assert grant["room_id"] == room["id"]


def test_accepting_marks_the_invitation_accepted(service, room):
    service.invite(room["id"], ["a@example.com"], actor="dana")
    invitation = service.store.list(INVITATIONS, room_id=room["id"])[0]

    service.accept(invitation["id"], actor="a@example.com")

    refreshed = service.invitation_record(invitation["id"])
    assert refreshed["data"]["state"] == "accepted"
    assert refreshed["data"]["access_id"]


def test_accepting_twice_is_refused(service, room):
    service.invite(room["id"], ["a@example.com"], actor="dana")
    invitation = service.store.list(INVITATIONS, room_id=room["id"])[0]
    service.accept(invitation["id"], actor="a@example.com")

    with pytest.raises(AccessInvalid, match="not pending"):
        service.accept(invitation["id"], actor="a@example.com")


def test_an_invitation_cannot_be_accepted_after_48_hours(db, room, service):
    service.invite(room["id"], ["a@example.com"], actor="dana")
    invitation = service.store.list(INVITATIONS, room_id=room["id"])[0]

    # 49 hours later: the window has closed.
    later = AccessService(RecordStore(db), clock=lambda: NOW + timedelta(hours=49))
    with pytest.raises(AccessInvalid, match="expired after 48 hours"):
        later.accept(invitation["id"], actor="a@example.com")

    assert service.invitation_record(invitation["id"])["data"]["state"] == "expired"
    assert service.grants(room["id"]) == []


def test_an_invitation_accepted_one_minute_before_the_deadline_still_works(db, room, service):
    service.invite(room["id"], ["a@example.com"], actor="dana")
    invitation = service.store.list(INVITATIONS, room_id=room["id"])[0]

    just_in_time = AccessService(RecordStore(db), clock=lambda: NOW + timedelta(hours=47, minutes=59))
    assert just_in_time.accept(invitation["id"], actor="a@example.com")["access"]["id"]


def test_accepting_an_unknown_invitation_is_a_miss(service):
    with pytest.raises(AccessMissing):
        service.accept("room_invitation_nope")


# -- immediate join for someone already in the room -------------------------- #


def test_someone_already_in_the_room_joins_on_send_not_on_accept(service, room):
    _grant(service, room["id"], "a@example.com", role="viewer")

    result = service.invite(room["id"], ["a@example.com"], role="content_contributor", actor="dana")

    assert result["joined_immediately"] == ["a@example.com"]
    assert result["pending"] == []
    grants = service.grants(room["id"])
    assert len(grants) == 1
    assert grants[0]["data"]["role"] == "content_contributor"
    assert grants[0]["data"]["joined_immediately"] is True


def test_an_immediate_join_closes_the_invitation_it_came_from(service, room):
    _grant(service, room["id"], "a@example.com")

    service.invite(room["id"], ["a@example.com"], actor="dana")

    invitation = service.store.list(INVITATIONS, room_id=room["id"])[0]
    assert invitation["data"]["state"] == "accepted"
    assert invitation["data"]["joined_immediately"] is True


def test_an_expired_grant_does_not_count_as_already_having_access(db, room, service):
    # A grant that lapsed yesterday is no longer access, so the recipient has
    # to go through the invitation again.
    _grant(service, room["id"], "a@example.com", access_valid_until="2026-09-25")

    result = service.invite(room["id"], ["a@example.com"], actor="dana")

    assert result["joined_immediately"] == []
    assert result["pending"] == ["a@example.com"]


def test_a_mixed_invitation_reports_who_joined_and_who_did_not(service, room):
    _grant(service, room["id"], "known@example.com")

    result = service.invite(
        room["id"], ["known@example.com", "new@example.com"], actor="dana"
    )

    assert result["joined_immediately"] == ["known@example.com"]
    assert result["pending"] == ["new@example.com"]


# -- the expiry cut-off ------------------------------------------------------- #


def test_access_survives_to_the_last_moment_of_the_expiration_date(db, room, service):
    _grant(service, room["id"], "a@example.com", access_valid_until="2026-09-26")

    end_of_day = AccessService(RecordStore(db), clock=lambda: datetime(2026, 9, 26, 23, 59, 59, tzinfo=timezone.utc))
    assert [m["principal"] for m in end_of_day.snapshot(room["id"], "dana")["members"]] == ["a@example.com"]


def test_access_is_gone_one_second_after_midnight_utc(db, room, service):
    _grant(service, room["id"], "a@example.com", access_valid_until="2026-09-26")

    next_day = AccessService(RecordStore(db), clock=lambda: datetime(2026, 9, 27, 0, 0, 0, tzinfo=timezone.utc))
    assert next_day.snapshot(room["id"], "dana")["members"] == []


def test_a_lapsed_grant_is_filtered_out_not_deleted(db, room, service):
    _grant(service, room["id"], "a@example.com", access_valid_until="2026-09-25")

    later = AccessService(RecordStore(db), clock=lambda: NOW)
    assert later.snapshot(room["id"], "dana")["members"] == []
    # The row survives, so the audit trail and the invitation remain readable.
    assert len(later.grants(room["id"])) == 1


def test_a_lapsed_grant_loses_its_share_capability(db, room, service):
    _grant(service, room["id"], "lead@example.com", role="content_contributor",
           access_valid_until="2026-09-25")

    later = AccessService(RecordStore(db), clock=lambda: NOW)
    with pytest.raises(AccessDenied):
        later.invite(room["id"], ["a@example.com"], actor="lead@example.com")


def test_no_expiry_means_no_expiry(service, room):
    _grant(service, room["id"], "a@example.com", access_valid_until=None)

    member = service.snapshot(room["id"], "dana")["members"][0]
    assert member["access_valid_until"] is None
    assert member["expires_at_utc"] is None
    assert member["expiring_soon"] is False


def test_no_expiry_never_becomes_expiring_soon(service, room):
    _grant(service, room["id"], "a@example.com", access_valid_until=None)

    assert service.snapshot(room["id"], "dana")["expiring_soon_count"] == 0


# -- imminent expiry warning -------------------------------------------------- #


def test_a_grant_lapsing_within_seven_days_is_flagged(service, room):
    soon = (NOW + timedelta(days=EXPIRING_SOON_DAYS - 1)).strftime("%Y-%m-%d")
    _grant(service, room["id"], "soon@example.com", access_valid_until=soon)

    snapshot = service.snapshot(room["id"], "dana")
    assert snapshot["expiring_soon_count"] == 1
    assert snapshot["members"][0]["expiring_soon"] is True


def test_a_grant_lapsing_beyond_seven_days_is_not_flagged(service, room):
    later = (NOW + timedelta(days=EXPIRING_SOON_DAYS + 3)).strftime("%Y-%m-%d")
    _grant(service, room["id"], "later@example.com", access_valid_until=later)

    assert service.snapshot(room["id"], "dana")["expiring_soon_count"] == 0


def test_the_banner_counts_lapsing_grants_only(service, room):
    soon = (NOW + timedelta(days=2)).strftime("%Y-%m-%d")
    _grant(service, room["id"], "one@example.com", access_valid_until=soon)
    _grant(service, room["id"], "two@example.com", access_valid_until=soon)
    _grant(service, room["id"], "safe@example.com", access_valid_until=None)
    service.invite(room["id"], ["pending@example.com"], actor="dana")

    snapshot = service.snapshot(room["id"], "dana")

    assert snapshot["expiring_soon_count"] == 2
    assert snapshot["banner"] == "2 users have access expiring within 7 days."


def test_the_banner_uses_singular_agreement_for_one_user(service, room):
    soon = (NOW + timedelta(days=2)).strftime("%Y-%m-%d")
    _grant(service, room["id"], "one@example.com", access_valid_until=soon)

    assert service.snapshot(room["id"], "dana")["banner"] == "1 user has access expiring within 7 days."


def test_no_banner_when_nobody_is_lapsing(service, room):
    assert service.snapshot(room["id"], "dana")["banner"] is None


def test_a_pending_invitation_never_counts_towards_the_banner(service, room):
    service.invite(room["id"], ["pending@example.com"], actor="dana")

    assert service.snapshot(room["id"], "dana")["expiring_soon_count"] == 0


# -- the owner --------------------------------------------------------------- #


def test_the_owner_is_reported_as_the_actor_not_as_a_grant(service, room):
    snapshot = service.snapshot(room["id"], "dana")
    assert snapshot["actor"]["role"] == OWNER
    assert snapshot["actor"]["is_owner"] is True
    assert snapshot["members"] == []


def test_the_owners_role_cannot_be_assigned(service, room):
    _grant(service, room["id"], "a@example.com")

    with pytest.raises(AccessInvalid, match="owner role cannot be assigned"):
        service.update_access(
            _only_grant(service, room["id"])["id"], role=OWNER, actor="dana", confirm=True
        )


def _only_grant(service, room_id):
    grants = service.grants(room_id)
    assert len(grants) == 1
    return grants[0]


def test_a_grant_for_the_owners_own_address_cannot_be_removed(service, room):
    _grant(service, room["id"], "dana")

    with pytest.raises(AccessInvalid, match="owner cannot be changed or removed"):
        service.remove_access(_only_grant(service, room["id"])["id"], actor="dana", confirm=True)


# -- changing access ---------------------------------------------------------- #


def test_changing_a_role_records_the_change(service, room):
    _grant(service, room["id"], "a@example.com", role="viewer")

    result = service.update_access(
        _only_grant(service, room["id"])["id"],
        role="content_contributor",
        actor="dana",
        confirm=True,
    )

    assert result["access"]["role"] == "content_contributor"
    assert result["access"]["role_label"] == "Content Contributor"


def test_a_role_change_needs_confirmation(service, room):
    _grant(service, room["id"], "a@example.com", role="viewer")

    with pytest.raises(ConfirmationRequired):
        service.update_access(
            _only_grant(service, room["id"])["id"], role="content_contributor", actor="dana"
        )


def test_reasserting_the_same_role_needs_no_confirmation(service, room):
    _grant(service, room["id"], "a@example.com", role="viewer")

    result = service.update_access(
        _only_grant(service, room["id"])["id"], role="viewer", actor="dana"
    )

    assert result["access"]["role"] == "viewer"


def test_changing_the_expiry_needs_no_confirmation(service, room):
    _grant(service, room["id"], "a@example.com", role="viewer")

    result = service.update_access(
        _only_grant(service, room["id"])["id"],
        access_valid_until="2027-03-31",
        set_expiry=True,
        actor="dana",
    )

    assert result["access"]["access_valid_until"] == "2027-03-31"


def test_the_expiry_can_be_cleared_back_to_no_expiration(service, room):
    _grant(service, room["id"], "a@example.com", access_valid_until="2027-03-31")

    result = service.update_access(
        _only_grant(service, room["id"])["id"], access_valid_until=None, set_expiry=True, actor="dana"
    )

    assert result["access"]["access_valid_until"] is None
    assert result["access"]["expiring_soon"] is False


def test_an_expiry_in_the_past_is_refused(service, room):
    _grant(service, room["id"], "a@example.com")

    with pytest.raises(AccessInvalid, match="in the past"):
        service.update_access(
            _only_grant(service, room["id"])["id"],
            access_valid_until="2020-01-01",
            set_expiry=True,
            actor="dana",
        )


def test_a_viewer_cannot_change_anyone_s_role(service, room):
    watcher = _grant(service, room["id"], "watcher@example.com", role="viewer")
    _grant(service, room["id"], "a@example.com", role="viewer")

    with pytest.raises(AccessDenied, match="cannot share"):
        service.update_access(
            watcher["id"], role="content_contributor", actor="watcher@example.com"
        )


def test_changing_an_unknown_grant_is_a_miss(service):
    with pytest.raises(AccessMissing):
        service.update_access("room_access_nope", role="viewer", actor="dana")


# -- removing access ---------------------------------------------------------- #


def test_removing_someone_soft_deletes_their_grant(service, room):
    _grant(service, room["id"], "a@example.com")

    service.remove_access(_only_grant(service, room["id"])["id"], actor="dana", confirm=True)

    assert service.grants(room["id"]) == []
    assert service.store.get(_only_grant_ids(service, room["id"])) is None


def _only_grant_ids(service, room_id):
    return service.store.list(ACCESS, room_id=room_id, include_deleted=True)[0]["id"]


def test_removal_needs_confirmation(service, room):
    _grant(service, room["id"], "a@example.com")

    with pytest.raises(ConfirmationRequired, match="needs confirmation"):
        service.remove_access(_only_grant(service, room["id"])["id"], actor="dana")


def test_a_removed_person_loses_their_share_capability(service, room):
    _grant(service, room["id"], "lead@example.com", role="content_contributor")

    service.remove_access(_only_grant(service, room["id"])["id"], actor="dana", confirm=True)

    with pytest.raises(AccessDenied):
        service.invite(room["id"], ["a@example.com"], actor="lead@example.com")


def test_removal_revokes_an_invitation_still_in_flight(service, room):
    _grant(service, room["id"], "a@example.com", access_valid_until="2026-09-25")
    # That grant has lapsed, so the invite below opens a fresh pending invitation.
    service.invite(room["id"], ["a@example.com"], actor="dana")
    invitation = service.store.list(INVITATIONS, room_id=room["id"])[0]
    assert invitation["data"]["state"] == "pending"

    live = _grant(service, room["id"], "a@example.com")
    service.remove_access(live["id"], actor="dana", confirm=True)

    assert service.invitation_record(invitation["id"])["data"]["state"] == "revoked"


def test_removing_an_unknown_grant_is_a_miss(service):
    with pytest.raises(AccessMissing):
        service.remove_access("room_access_nope", actor="dana", confirm=True)


# -- the snapshot the dialog renders ------------------------------------------ #


def test_the_snapshot_reports_the_actors_capability(service, room):
    _grant(service, room["id"], "watcher@example.com", role="viewer")

    assert service.snapshot(room["id"], "dana")["actor"]["can_share"] is True
    assert service.snapshot(room["id"], "watcher@example.com")["actor"]["can_share"] is False
    assert service.snapshot(room["id"], "stranger@example.com")["actor"]["can_share"] is False


def test_the_snapshot_reports_the_role_being_acted_in(service, room):
    _grant(service, room["id"], "watcher@example.com", role="viewer")

    actor = service.snapshot(room["id"], "watcher@example.com")["actor"]
    assert actor["role"] == "viewer"
    assert actor["role_label"] == "Viewer"
    assert actor["assignable_roles"] == []


def test_the_snapshot_lists_pending_invitations_with_time_left(service, room):
    service.invite(room["id"], ["a@example.com"], actor="dana")

    pending = service.snapshot(room["id"], "dana")["pending_invitations"]
    assert len(pending) == 1
    assert pending[0]["email"] == "a@example.com"
    assert pending[0]["hours_until_expiry"] == 48.0
    assert pending[0]["expired"] is False


def test_an_unknown_room_is_a_miss(service):
    with pytest.raises(AccessMissing):
        service.snapshot("room_nope", "dana")


def test_a_record_that_is_not_a_room_is_a_miss(service):
    document = service.store.create("document", {"title": "Deck"})

    with pytest.raises(AccessMissing):
        service.snapshot(document["id"], "dana")


# -- audit and schema flexibility --------------------------------------------- #


def test_every_access_mutation_is_audited(service, db, room):
    service.invite(room["id"], ["a@example.com"], role="content_contributor", actor="dana")
    invitation = service.store.list(INVITATIONS, room_id=room["id"])[0]
    service.accept(invitation["id"], actor="a@example.com")
    grant = service.grants(room["id"])[0]
    service.update_access(grant["id"], access_valid_until="2027-06-30", set_expiry=True, actor="dana")
    service.remove_access(grant["id"], actor="dana", confirm=True)

    # The room insert predates the workflow, so only the access mutations count.
    entries = [e for e in reversed(db.audit()) if e["collection"] in (INVITATIONS, ACCESS)]
    # invitation insert, grant insert, invitation update (accepted),
    # grant update (expiry), grant delete
    assert [e["action"] for e in entries] == ["insert", "insert", "update", "update", "delete"]
    assert all(e["room_id"] == room["id"] for e in entries)


def test_the_invite_is_attributed_to_whoever_pressed_invite(service, db, room):
    _grant(service, room["id"], "lead@example.com", role="content_contributor")

    service.invite(room["id"], ["a@example.com"], actor="lead@example.com")

    entry = db.audit(collection=INVITATIONS)[0]
    assert entry["actor"] == "lead@example.com"
    assert entry["summary"] == f"created {INVITATIONS} {entry['record_id']}"


def test_a_role_change_audits_before_and_after(service, db, room):
    _grant(service, room["id"], "a@example.com", role="viewer")
    grant = service.grants(room["id"])[0]

    service.update_access(grant["id"], role="content_contributor", actor="dana", confirm=True)

    entry = db.audit(record_id=grant["id"], action="update")[0]
    assert entry["before_state"]["role"] == "viewer"
    assert entry["after_state"]["role"] == "content_contributor"


def test_a_team_can_add_its_own_field_to_a_grant_without_a_migration(service, room):
    grant = _grant(
        service, room["id"], "a@example.com", cost_centre="CC-4417", region="anz"
    )

    stored = service.store.get(grant["id"])
    assert stored["data"]["cost_centre"] == "CC-4417"
    assert stored["data"]["region"] == "anz"


def test_a_team_added_field_is_filterable_through_the_dynamic_index(service, room):
    _grant(service, room["id"], "a@example.com", cost_centre="CC-4417")
    _grant(service, room["id"], "b@example.com", cost_centre="CC-9000")

    hits = service.store.find(ACCESS, {"cost_centre": "CC-4417"})
    assert [h["data"]["principal"] for h in hits] == ["a@example.com"]


def test_a_team_added_role_is_rejected_by_the_delegation_rule_not_silently_accepted(service, room):
    _grant(service, room["id"], "lead@example.com", role="content_contributor")

    with pytest.raises(AccessInvalid, match="unknown role"):
        service.invite(room["id"], ["a@example.com"], role="legal_counsel", actor="lead@example.com")


def test_an_unexpected_role_still_renders_with_a_readable_label(service, room):
    _grant(service, room["id"], "a@example.com", role="legal_counsel")

    member = service.snapshot(room["id"], "dana")["members"][0]
    assert member["role"] == "legal_counsel"
    assert member["role_label"] == "Legal Counsel"
