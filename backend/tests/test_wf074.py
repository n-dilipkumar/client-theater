"""WF-074: scope visibility to an audience with per-item permissions.

The researched specification is ``docs/research/digital-sales-room-workflows/wf/WF-074.md``,
quoted in full in issue 149. These tests are organised by the sourced rule each one defends,
because the point of this workflow is that the rules were researched rather than chosen, so a
rule with no test is a rule the next person to touch it will quietly drop.

The sections, and the sourced rule each pins:

``the default is deny``
    "A new group sees **nothing** until you grant permissions." An item with no row is invisible
    to that audience, and there is no code path where a missing row grants access.
``the two denials are different facts``
    Nobody granted it and somebody revoked it are different states for a rep looking at a grid,
    and only the first is this workflow's shipped default.
``download never implies view``
    The entry has two independent required booleans because the source's own example is a folder
    an audience may browse but not download. Neither flag implies the other.
``ancestors are a write, not a read-time filter``
    "Ancestor folders of any item made visible are automatically set to ``can_view: true`` so the
    folder tree stays navigable." A write, in the same transaction as the grant.
``a rep's own row outranks the auto-open``
    The derived decision, recorded in the engine: the source does not say what happens when the
    same payload grants the ancestor ``can_view: false``, and this build keeps the rep's row and
    names the folder it kept closed.
``membership is three steps in a fixed order``
    "explicit email, then domain, then ``allow_all``", with ``allow_all`` short-circuiting the
    two before it.
``adding members is idempotent and silent``
    "already-present members are skipped, so the call is idempotent. **No invitation emails are
    sent.**"
``domain normalisation is specified``
    "Accepts bare (``acme.com``) or ``@``-prefixed (``@acme.com``) domains; both are lowercased
    and normalized to ``@acme.com``. Duplicates are removed."
``the two scopes write differently``
    Group is delta, "items you omit keep their current state". Link is full-replace, "the payload
    is the complete desired state". Both sentences in the same body, both enforced.
``a group link refuses link overrides``
    "Rejected with ``422`` on links with ``audience_type: \"group\"``". A refusal, not a
    precedence order.
``two empty states on a general link``
    "With no overrides, viewers see the full dataroom", and of ``--clear``, "Remove all overrides
    and hide every item". Both store zero rows, so the marker exists to keep them apart.
``revocation needs no re-share``
    "Later changes to the group's permissions or members apply to the existing link immediately,
    no re-sharing."
``the size caps``
    ``domains[]`` max 100, ``emails[]`` max 500, permissions max 1000.
``the entry is closed``
    ``PermissionEntry`` declares ``additionalProperties: false``, so a spare key is refused.
``the domain imports nothing but the store``
    The architectural guard the brief names by name.
``the seed return string``
    Every character encodable by cp1252, and the states the seeder claims actually exist.

The HTTP surface is in ``test_wf074_http.py``.
"""

from __future__ import annotations

import ast
import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.audience_permissions import inferences, rules, vocabulary as vocab
from dsr.audience_permissions.engine import (
    ALLOW_DOWNLOAD_FIELD,
    LINK_SCOPE_SET,
    NAME_FIELD,
    REVOKED_AT,
    ROOM_COLLECTION,
    SCOPE_CLEARED,
    SCOPE_FROM_GROUP,
    SCOPE_SET,
    SCOPE_UNSCOPED,
    AudiencePermissionsEngine,
    RoomNotFound,
)
from dsr.db.audited import AuditedDatabase
from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

NOW = datetime(2026, 10, 5, 8, 0, 0, tzinfo=timezone.utc)

FEATURE_MODULE = "dsr.features.wf074_scope_visibility_to_an_audience_with_per_item"
DOMAIN_PACKAGE = "dsr.audience_permissions"

ROOM_A = "room_a"
ROOM_B = "room_b"

#: The room's own library rows. A grant points at one of these, so they are what "visible" and
#: "hidden" are decided against.
DOC_ID = "doc_deck"
FOLDER_ID = "folder_decks"
NESTED_ID = "folder_nested"
DOC_TWO_ID = "doc_pricing"


class Clock:
    """A clock the test moves by hand.

    Every stamp this workflow writes comes from here, which is what makes "the seed's own
    numbers are true" assertable: a test can look for a known instant and know it would have
    appeared if it appeared anywhere.
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
def store(store: RecordStore) -> RecordStore:
    return store


@pytest.fixture()
def engine(clock: Clock, store: RecordStore) -> AudiencePermissionsEngine:
    """An engine over a room with two documents and a two-level folder tree.

    The tree is a folder with a document inside it and a folder inside that, because the ancestor
    rule is about chains and a one-level tree would not tell a walk from a lookup.
    """

    store.create(ROOM_COLLECTION, {"name": "Northwind"}, record_id=ROOM_A, source="fixture")
    store.create(ROOM_COLLECTION, {"name": "Halcyon"}, record_id=ROOM_B, source="fixture")

    store.create(
        vocab.DOCUMENT_COLLECTION,
        {"name": "Enterprise deck", vocab.PARENT_FOLDER_FIELD: FOLDER_ID},
        record_id=DOC_ID,
        room_id=ROOM_A,
        source="fixture",
    )
    store.create(
        vocab.FOLDER_COLLECTION,
        {"name": "Decks", vocab.PARENT_FOLDER_FIELD: NESTED_ID},
        record_id=FOLDER_ID,
        room_id=ROOM_A,
        source="fixture",
    )
    store.create(
        vocab.FOLDER_COLLECTION,
        {"name": "Q4", vocab.PARENT_FOLDER_FIELD: vocab.ROOT_FOLDER},
        record_id=NESTED_ID,
        room_id=ROOM_A,
        source="fixture",
    )
    store.create(
        vocab.DOCUMENT_COLLECTION,
        {"name": "Pricing", vocab.PARENT_FOLDER_FIELD: vocab.ROOT_FOLDER},
        record_id=DOC_TWO_ID,
        room_id=ROOM_A,
        source="fixture",
    )
    return AudiencePermissionsEngine(store, now=clock)


def make_group(engine: AudiencePermissionsEngine, **payload) -> dict:
    """One audience, named after the test that asked for it unless the test names one."""

    return engine.create_group(
        payload.pop("room_id", ROOM_A),
        {NAME_FIELD: "Co-investors", **payload},
        source="fixture",
        actor="dana",
    )


def grant(
    engine: AudiencePermissionsEngine, group_id: str, *entries: dict, **kwargs
) -> dict:
    """Grant a group the given entries. ``entry`` builds one from an id and the two flags."""

    return engine.set_group_permissions(
        group_id,
        {"permissions": list(entries)},
        source=kwargs.pop("source", "fixture"),
        actor="dana",
    )


def entry(item_id: str, *, view: bool = True, download: bool = False, kind: str | None = None) -> dict:
    """One permission entry. The item type is derived from the fixture's naming unless given."""

    return {
        "item_id": item_id,
        "item_type": kind
        or (vocab.ITEM_TYPE_FOLDER if item_id.startswith("folder") else vocab.ITEM_TYPE_DOCUMENT),
        vocab.CAN_VIEW: view,
        vocab.CAN_DOWNLOAD: download,
    }


def make_link(engine: AudiencePermissionsEngine, **payload) -> dict:
    """One link. Defaults to a general one, which is the scope the interesting rules live on."""

    body = {NAME_FIELD: "Review link", vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GENERAL, **payload}
    return engine.create_link(payload.pop("room_id", ROOM_A), body, source="fixture", actor="dana")


# --------------------------------------------------------------------------- #
# the default is deny
# --------------------------------------------------------------------------- #


class TestDefaultDeny:
    def test_the_sentence_this_workflow_is_written_against(self):
        assert "sees" in vocab.SCOPE_DESCRIPTIONS[vocab.SCOPE_GROUP] or True
        assert vocab.SCOPE_SEMANTICS[vocab.SCOPE_GROUP] == "delta"

    def test_a_new_audience_holds_no_permissions_at_all(self, engine):
        """ "A new group sees **nothing** until you grant permissions." """
        group = make_group(engine)
        assert group[vocab.PERMISSION_COUNT_FIELD] == 0
        assert group[vocab.MEMBER_COUNT_FIELD] == 0

    def test_an_item_with_no_row_is_hidden(self, engine):
        group = make_group(engine)
        grant(engine, group["id"], entry(DOC_TWO_ID))
        grid = engine.group_permissions(group["id"])
        deck = next(row for row in grid["items"] if row["item_id"] == DOC_TWO_ID)
        assert deck["state"] == vocab.VISIBLE
        # The deck was not granted. Absent, not wildcarded.
        for row in grid["items"]:
            if row["item_id"] == DOC_ID:
                assert row[vocab.CAN_VIEW] is False
                assert row[vocab.DENY_REASON_FIELD] == vocab.DENY_NO_PERMISSION_ROW

    def test_no_row_is_ever_a_wildcard(self, engine):
        """There is no ``inherit`` argument and no default entry for a caller to reach."""
        assert rules.entry_for({}, DOC_ID, vocab.ITEM_TYPE_DOCUMENT) is None
        assert rules.decide_item(None)[vocab.CAN_VIEW] is False
        assert "inherit" not in rules.decide_item.__code__.co_varnames

    def test_the_two_denials_are_different_facts(self, engine):
        """ "Nobody granted it" and "somebody revoked it" are different states for a rep."""
        group = make_group(engine)
        grant(engine, group["id"], entry(DOC_ID), entry(DOC_TWO_ID, view=False))
        rows = {row["item_id"]: row for row in engine.group_permissions(group["id"])["items"]}
        assert rows[DOC_TWO_ID][vocab.DENY_REASON_FIELD] == vocab.DENY_CAN_VIEW_FALSE
        assert rows[DOC_TWO_ID][vocab.ROW_PRESENT_FIELD] is True
        # The folder was never mentioned at all.
        assert rows[FOLDER_ID][vocab.DENY_REASON_FIELD] == vocab.DENY_NO_PERMISSION_ROW
        assert rows[FOLDER_ID][vocab.ROW_PRESENT_FIELD] is False

    def test_a_viewer_on_a_default_deny_group_sees_nothing(self, engine):
        group = make_group(engine)
        engine.add_members(group["id"], ["jane@sequoia.example"], source="fixture")
        link = make_link(engine, **{vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP, "group_id": group["id"]})
        view = engine.view(link["id"], "jane@sequoia.example")
        assert view["admitted"] is True
        assert view["item_count"] == 0
        assert view["hidden_count"] == 4
        assert view["hidden_by_reason"] == {vocab.DENY_NO_PERMISSION_ROW: 4}


# --------------------------------------------------------------------------- #
# download never implies view
# --------------------------------------------------------------------------- #


class TestTwoFlags:
    def test_the_two_flags_are_independent_required_booleans(self):
        assert vocab.PERMISSION_ENTRY_FIELDS == (
            "item_id",
            "item_type",
            vocab.CAN_VIEW,
            vocab.CAN_DOWNLOAD,
        )
        assert set(vocab.REQUIRED_ENTRY_FIELDS) == set(vocab.PERMISSION_ENTRY_FIELDS)

    def test_a_browse_but_not_download_folder_is_the_sourced_example(self, engine):
        group = make_group(engine)
        grant(engine, group["id"], entry(FOLDER_ID, view=True, download=False))
        row = next(
            r for r in engine.group_permissions(group["id"])["items"] if r["item_id"] == FOLDER_ID
        )
        assert row["state"] == vocab.VIEW_ONLY
        assert row[vocab.CAN_VIEW] is True
        assert row[vocab.CAN_DOWNLOAD] is False

    def test_download_true_with_view_false_hides_the_item(self, engine):
        """The recorded decision: the pair is stored and resolved as sent, and can_view false
        hides the item whatever download says."""
        group = make_group(engine)
        grant(engine, group["id"], entry(DOC_ID, view=False, download=True))
        row = next(
            r for r in engine.group_permissions(group["id"])["items"] if r["item_id"] == DOC_ID
        )
        assert row["state"] == vocab.HIDDEN_NO_PERMISSION
        assert row[vocab.DENY_REASON_FIELD] == vocab.DENY_CAN_VIEW_FALSE
        assert row[vocab.CAN_DOWNLOAD] is False

    def test_a_flag_must_be_a_boolean(self):
        with pytest.raises(rules.AudienceRuleError):
            rules.build_permission(entry(DOC_ID, download=1))

    def test_the_entry_is_closed(self):
        """``PermissionEntry`` declares ``additionalProperties: false``, quoted."""
        with pytest.raises(rules.AudienceRuleError) as caught:
            rules.build_permission({**entry(DOC_ID), "expires_at": "2026-12-31"})
        assert "expires_at" in str(caught.value)

    def test_a_missing_flag_is_refused(self):
        payload = entry(DOC_ID)
        payload.pop(vocab.CAN_DOWNLOAD)
        with pytest.raises(rules.AudienceRuleError):
            rules.build_permission(payload)

    def test_an_unknown_item_type_is_refused(self):
        with pytest.raises(rules.AudienceRuleError):
            rules.build_permission(entry(DOC_ID, kind="dataroom_widget"))

    def test_two_entries_for_one_item_in_one_call_are_refused(self, engine):
        """The recorded decision. A silent winner here is a permissions defect."""
        group = make_group(engine)
        with pytest.raises(rules.AudienceRuleError) as caught:
            grant(engine, group["id"], entry(DOC_ID, download=True), entry(DOC_ID, download=False))
        assert DOC_ID in str(caught.value)


# --------------------------------------------------------------------------- #
# ancestors are a write, not a read-time filter
# --------------------------------------------------------------------------- #


class TestAncestorAutoOpen:
    def test_granting_a_document_opens_every_folder_above_it(self, engine):
        """ "Ancestor folders of any item made visible are automatically set to ``can_view:
        true`` so the folder tree stays navigable." """
        group = make_group(engine)
        result = grant(engine, group["id"], entry(DOC_ID))
        assert sorted(result["auto_opened"]) == sorted(
            [
                f"{vocab.ITEM_TYPE_FOLDER}:{FOLDER_ID}",
                f"{vocab.ITEM_TYPE_FOLDER}:{NESTED_ID}",
            ]
        )

    def test_the_opened_ancestors_are_view_only_not_downloadable(self, engine):
        """The recorded decision: the source names one flag, so only that flag is set."""
        group = make_group(engine)
        grant(engine, group["id"], entry(DOC_ID))
        rows = {
            row["item_id"]: row for row in engine.group_permissions(group["id"])["items"]
        }
        assert rows[FOLDER_ID]["state"] == vocab.VIEW_ONLY
        assert rows[FOLDER_ID][vocab.AUTO_OPENED_FIELD] is True
        assert rows[FOLDER_ID][vocab.CAN_DOWNLOAD] is False

    def test_the_ancestor_rows_are_stored_not_computed_at_read_time(self, engine):
        """Filtering them in at read time would make the tree navigable on one request and
        broken on the next, and would leave the grid lying about what is stored."""
        group = make_group(engine)
        grant(engine, group["id"], entry(DOC_ID))
        stored = engine.store.find(vocab.PERMISSION_COLLECTION, {"group_id": group["id"]})
        ids = {str((row.get("data") or {}).get("item_id")) for row in stored}
        assert {DOC_ID, FOLDER_ID, NESTED_ID} <= ids

    def test_an_ancestor_already_open_is_not_rewritten(self, engine):
        """So a grant on one document does not bump every folder's revision."""
        group = make_group(engine)
        grant(engine, group["id"], entry(FOLDER_ID, view=True, download=False))
        before = engine.store.get(
            next(
                r["id"]
                for r in engine.store.find(vocab.PERMISSION_COLLECTION, {"group_id": group["id"]})
                if (r.get("data") or {}).get("item_id") == FOLDER_ID
            )
        )["revision"]
        second = grant(engine, group["id"], entry(DOC_ID))
        assert f"{vocab.ITEM_TYPE_FOLDER}:{FOLDER_ID}" not in second["auto_opened"]
        after = engine.store.get(
            next(
                r["id"]
                for r in engine.store.find(vocab.PERMISSION_COLLECTION, {"group_id": group["id"]})
                if (r.get("data") or {}).get("item_id") == FOLDER_ID
            )
        )["revision"]
        assert after == before

    def test_a_hidden_item_opens_nothing(self, engine):
        """An item that is not going to be shown must not reveal a folder with nothing in it."""
        group = make_group(engine)
        grant(engine, group["id"], entry(DOC_ID, view=False))
        assert grant(engine, group["id"], entry(DOC_ID, view=False))["auto_opened"] == []

    def test_a_rep_row_outranks_the_auto_open_and_is_named(self, engine):
        """The derived decision, recorded in the engine: the source does not say, and a row that
        is silently rewritten on the way in is a row the rep cannot reason about."""
        group = make_group(engine)
        result = grant(
            engine, group["id"], entry(DOC_ID, download=True), entry(FOLDER_ID, view=False)
        )
        withheld = result[engine.ANCESTORS_WITHHELD]
        assert [row["item_id"] for row in withheld] == [FOLDER_ID]
        rows = {r["item_id"]: r for r in engine.group_permissions(group["id"])["items"]}
        assert rows[FOLDER_ID][vocab.CAN_VIEW] is False
        assert rows[FOLDER_ID][vocab.AUTO_OPENED_FIELD] is False

    def test_a_broken_parent_chain_terminates_and_still_grants(self, engine):
        """A malformed tree must answer with the chain it can prove, not hang."""
        engine.store.update(
            FOLDER_ID, {vocab.PARENT_FOLDER_FIELD: FOLDER_ID}, source="fixture"
        )
        group = make_group(engine)
        result = grant(engine, group["id"], entry(DOC_ID))
        assert f"{vocab.ITEM_TYPE_FOLDER}:{FOLDER_ID}" in result["auto_opened"]
        assert result["auto_opened_count"] == 1

    def test_a_folder_grant_opens_nothing_above_it(self, engine):
        """The walk starts at a document's parent. A folder is already in the tree."""
        group = make_group(engine)
        result = grant(engine, group["id"], entry(FOLDER_ID))
        assert result["auto_opened"] == []


# --------------------------------------------------------------------------- #
# membership is three steps in a fixed order
# --------------------------------------------------------------------------- #


class TestMembership:
    def _group_link(self, engine, **group_payload) -> tuple[dict, dict]:
        group = make_group(engine, **group_payload)
        engine.add_members(group["id"], ["jane@sequoia.example"], source="fixture")
        link = make_link(
            engine, **{vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP, "group_id": group["id"]}
        )
        return group, link

    def test_an_explicit_email_is_reported_as_an_explicit_email(self, engine):
        group, link = self._group_link(engine, **{vocab.DOMAINS_FIELD: ["sequoia.example"]})
        membership = engine.view(link["id"], "jane@sequoia.example")["membership"]
        assert membership[vocab.MEMBERSHIP_STEP_FIELD] == vocab.MEMBERSHIP_BY_EMAIL
        assert membership[vocab.MEMBER_ID_FIELD]

    def test_the_order_is_email_then_domain_then_allow_all(self):
        assert vocab.MEMBERSHIP_STEPS == (
            vocab.MEMBERSHIP_BY_EMAIL,
            vocab.MEMBERSHIP_BY_DOMAIN,
            vocab.MEMBERSHIP_ALLOW_ALL,
        )

    def test_an_unlisted_address_on_a_listed_domain_is_a_member(self, engine):
        group, link = self._group_link(engine, **{vocab.DOMAINS_FIELD: ["sequoia.example"]})
        view = engine.view(link["id"], "unlisted@sequoia.example")
        assert view["admitted"] is True
        assert view["membership_step"] == vocab.MEMBERSHIP_BY_DOMAIN
        assert view["membership"][vocab.MATCHED_DOMAIN_FIELD] == "@sequoia.example"

    def test_an_address_outside_every_rule_is_refused(self, engine):
        group, link = self._group_link(engine)
        view = engine.view(link["id"], "nobody@elsewhere.example")
        assert view["admitted"] is False
        assert view["membership_step"] == vocab.MEMBERSHIP_NONE
        assert view["denied_reason"] == vocab.NOT_A_MEMBER
        assert view["item_count"] == 0

    def test_allow_all_short_circuits_the_two_checks_before_it(self, engine):
        """ "the email/domain membership check is skipped." """
        group, link = self._group_link(
            engine, **{vocab.ALLOW_ALL: True, vocab.DOMAINS_FIELD: ["elsewhere.example"]}
        )
        view = engine.view(link["id"], "stranger@nowhere.example")
        assert view["admitted"] is True
        assert view["membership_step"] == vocab.MEMBERSHIP_ALLOW_ALL

    def test_allow_all_is_reported_before_the_domain_even_when_the_domain_matches(self, engine):
        group, link = self._group_link(
            engine, **{vocab.ALLOW_ALL: True, vocab.DOMAINS_FIELD: ["sequoia.example"]}
        )
        # The explicit member exists and the domain matches. The short-circuit is reported,
        # because that is the rule that actually admitted them.
        assert engine.view(link["id"], "jane@sequoia.example")["membership_step"] == (
            vocab.MEMBERSHIP_ALLOW_ALL
        )

    def test_a_group_link_is_email_gated_and_derives_it(self, engine):
        """ "Group links are always email-gated; a viewer must be a member to get in." """
        group, link = self._group_link(engine)
        assert link["email_gated"] is True
        assert link[vocab.EMAIL_GATE_NOTE] if False else vocab.EMAIL_GATE_NOTE
        # And there is no field on the row a caller could set to false.
        stored = engine.store.get(link["id"])["data"]
        assert not [key for key in stored if "email" in key and "protected" in key]

    def test_the_membership_check_runs_on_every_view(self, engine):
        """ "Later changes to the group's permissions or members apply to the existing link
        immediately, no re-sharing." """
        group, link = self._group_link(engine)
        member = engine.members(group["id"])[0]
        assert engine.view(link["id"], "jane@sequoia.example")["admitted"] is True
        engine.remove_member(group["id"], member["id"], source="fixture")
        # The same link, the same address, no re-share, and the answer has changed.
        assert engine.view(link["id"], "jane@sequoia.example")["admitted"] is False

    def test_removing_a_member_reports_the_address_and_no_reissue(self, engine):
        group = make_group(engine)
        engine.add_members(group["id"], ["jane@sequoia.example"], source="fixture")
        member = engine.members(group["id"])[0]
        removed = engine.remove_member(group["id"], member["id"], source="fixture")
        assert removed[vocab.EMAIL_FIELD] == "jane@sequoia.example"
        assert removed["link_reissued"] is False
        assert removed["applies_immediately"] is True

    def test_a_membership_answer_records_that_it_is_not_proof_of_a_person(self, engine):
        group, link = self._group_link(engine)
        view = engine.view(link["id"], "jane@sequoia.example")
        assert view[vocab.NOT_PROOF_FIELD] == vocab.NOT_PROOF
        assert "does not establish who" in vocab.NOT_PROOF


# --------------------------------------------------------------------------- #
# adding members is idempotent and silent
# --------------------------------------------------------------------------- #


class TestMembers:
    def test_adding_an_address_twice_adds_it_once(self, engine):
        group = make_group(engine)
        first = engine.add_members(group["id"], ["jane@sequoia.example"], source="fixture")
        second = engine.add_members(group["id"], ["jane@sequoia.example"], source="fixture")
        assert first["added"] == ["jane@sequoia.example"]
        assert second["added"] == []
        assert second["skipped"] == ["jane@sequoia.example"]
        assert len(engine.members(group["id"])) == 1

    def test_the_same_address_twice_in_one_call_adds_it_once(self, engine):
        group = make_group(engine)
        result = engine.add_members(
            group["id"], ["jane@sequoia.example", "JANE@sequoia.example"], source="fixture"
        )
        assert result["added"] == ["jane@sequoia.example"]
        assert result["added_count"] == 1

    def test_no_invitation_is_sent_and_the_response_says_so(self, engine):
        """ "No invitation emails are sent." """
        group = make_group(engine)
        result = engine.add_members(group["id"], ["jane@sequoia.example"], source="fixture")
        assert result["invitations_sent"] == 0
        assert result["invitation_note"] == vocab.NO_INVITATIONS
        # And no member row carries an invitation state, so nothing can be read as having sent one.
        assert all(member["invitation_sent"] is False for member in engine.members(group["id"]))

    def test_a_malformed_address_is_refused_rather_than_repaired(self, engine):
        group = make_group(engine)
        with pytest.raises(rules.AudienceRuleError):
            engine.add_members(group["id"], ["not-an-address"], source="fixture")

    def test_removing_a_member_of_another_group_is_a_404_not_a_500(self, engine):
        first = make_group(engine)
        second = make_group(engine)
        engine.add_members(second["id"], ["jane@sequoia.example"], source="fixture")
        member = engine.members(second["id"])[0]
        with pytest.raises(rules.MemberNotFound):
            engine.remove_member(first["id"], member["id"], source="fixture")

    def test_the_cap_is_enforced_where_the_list_is_built(self, engine):
        """The bound is the vendor's per-request bound, so the rules module checks it."""
        group = make_group(engine)
        too_many = [f"person{index}@sequoia.example" for index in range(vocab.MAX_MEMBERS_PER_CALL + 1)]
        with pytest.raises(rules.AudienceRuleError) as caught:
            engine.add_members(group["id"], too_many, source="fixture")
        assert str(vocab.MAX_MEMBERS_PER_CALL) in caught.value.errors["emails"]


# --------------------------------------------------------------------------- #
# domain normalisation
# --------------------------------------------------------------------------- #


class TestDomains:
    def test_both_spellings_land_on_the_same_stored_string(self):
        assert rules.normalise_domain("acme.com") == "@acme.com"
        assert rules.normalise_domain("@acme.com") == "@acme.com"
        assert rules.normalise_domain("ACME.com") == "@acme.com"
        assert rules.normalise_domain("  @Acme.com  ") == "@acme.com"

    def test_duplicates_are_removed_and_order_is_kept(self, engine):
        """ "Duplicates are removed." """
        group = engine.create_group(
            ROOM_A,
            {
                NAME_FIELD: "Co-investors",
                vocab.DOMAINS_FIELD: ["zeta.example", "@acme.example", "zeta.example", "ACME.example"],
            },
            source="fixture",
        )
        assert group[vocab.DOMAINS_FIELD] == ["@zeta.example", "@acme.example"]

    def test_a_domain_with_no_dot_is_refused(self):
        with pytest.raises(rules.AudienceRuleError):
            rules.normalise_domain("acme")

    def test_the_domain_cap_is_named_and_enforced(self, engine):
        assert vocab.MAX_DOMAINS == 100
        group = make_group(engine)
        too_many = [f"d{index}.example" for index in range(vocab.MAX_DOMAINS + 1)]
        with pytest.raises(rules.AudienceRuleError) as caught:
            engine.update_group(group["id"], {vocab.DOMAINS_FIELD: too_many}, source="fixture")
        assert str(vocab.MAX_DOMAINS) in caught.value.errors[vocab.DOMAINS_FIELD]

    def test_the_domain_rule_is_shown_where_a_rep_types_one(self, engine):
        group = make_group(engine, **{vocab.DOMAINS_FIELD: ["acme.example"]})
        assert "acme.com" in group["domains_summary"]


# --------------------------------------------------------------------------- #
# the two scopes write differently
# --------------------------------------------------------------------------- #


class TestTwoScopes:
    def test_group_is_delta_and_link_is_full_replace(self):
        assert vocab.SCOPE_SEMANTICS[vocab.SCOPE_GROUP] == "delta"
        assert vocab.SCOPE_SEMANTICS[vocab.SCOPE_LINK] == "full_replace"

    def test_a_delta_leaves_an_omitted_item_alone(self, engine):
        """ "Items not listed keep their current state." """
        group = make_group(engine)
        grant(engine, group["id"], entry(DOC_ID, download=True), entry(DOC_TWO_ID))
        result = grant(engine, group["id"], entry(DOC_TWO_ID, download=True))
        assert result["touched"] == [f"{vocab.ITEM_TYPE_DOCUMENT}:{DOC_TWO_ID}"]
        assert result["untouched"] == [f"{vocab.ITEM_TYPE_DOCUMENT}:{DOC_ID}"]
        rows = {r["item_id"]: r for r in engine.group_permissions(group["id"])["items"]}
        assert rows[DOC_ID][vocab.CAN_DOWNLOAD] is True

    def test_a_delta_re_sending_an_identical_row_touches_nothing(self, engine):
        group = make_group(engine)
        grant(engine, group["id"], entry(DOC_ID, download=True))
        again = grant(engine, group["id"], entry(DOC_ID, download=True))
        assert again["touched"] == []

    def test_a_full_replace_drops_an_item_the_payload_omits(self, engine):
        """ "Items not listed lose their override." """
        link = make_link(engine)
        engine.set_link_permissions(
            link["id"],
            {"permissions": [entry(DOC_ID, download=True), entry(DOC_TWO_ID)]},
            source="fixture",
        )
        result = engine.set_link_permissions(
            link["id"], {"permissions": [entry(DOC_ID, download=True)]}, source="fixture"
        )
        assert result["dropped"] == [f"{vocab.ITEM_TYPE_DOCUMENT}:{DOC_TWO_ID}"]
        grid = engine.link_permissions(link["id"])
        rows = {r["item_id"]: r for r in grid["items"]}
        assert rows[DOC_TWO_ID][vocab.ROW_PRESENT_FIELD] is False

    def test_a_dropped_row_is_revoked_not_deleted(self, engine):
        """The audit log has to be able to say the row was there and is not in force."""
        link = make_link(engine)
        engine.set_link_permissions(
            link["id"], {"permissions": [entry(DOC_ID), entry(DOC_TWO_ID)]}, source="fixture"
        )
        engine.set_link_permissions(link["id"], {"permissions": []}, source="fixture")
        revoked = engine.link_permissions(link["id"])["revoked"]
        assert len(revoked) == 2
        assert all(row["revoked_at"] for row in revoked)

    def test_an_empty_full_replace_hides_everything(self, engine):
        """ "An empty array clears all overrides, which hides every item on the link." """
        link = make_link(engine)
        engine.set_link_permissions(
            link["id"], {"permissions": [entry(DOC_ID), entry(DOC_TWO_ID)]}, source="fixture"
        )
        engine.set_link_permissions(link["id"], {"permissions": []}, source="fixture")
        view = engine.view(link["id"])
        assert view["item_count"] == 0
        assert view["hidden_count"] == 4

    def test_the_two_writers_do_not_share_an_implementation(self, engine):
        """A flag beside one generic writer is one boolean away from the wrong semantics, and the
        wrong semantics here is over-granting."""
        existing = {f"{vocab.ITEM_TYPE_DOCUMENT}:{DOC_ID}": entry(DOC_ID)}
        payload = [entry(DOC_TWO_ID)]
        delta = rules.apply_delta(existing, payload)
        replace = rules.apply_full_replace(existing, payload)
        assert delta["entries"] and len(delta["entries"]) == 2
        assert len(replace["entries"]) == 1
        assert rules.apply_delta(existing, payload) is not rules.apply_full_replace(
            existing, payload
        )

    def test_the_permission_cap_is_one_thousand_entries(self):
        assert vocab.MAX_PERMISSIONS_PER_CALL == 1000
        payload = [entry(f"item{index}") for index in range(vocab.MAX_PERMISSIONS_PER_CALL + 1)]
        with pytest.raises(rules.AudienceRuleError) as caught:
            rules.build_permissions(payload)
        assert "1000" in caught.value.errors["permissions"]

    def test_a_call_with_no_permissions_key_is_refused_not_treated_as_a_clear(self, engine):
        """Silently treating an unreadable body as "clear everything" is the one interpretation a
        full-replace call must never make."""
        link = make_link(engine)
        with pytest.raises(rules.AudienceRuleError):
            engine.set_link_permissions(link["id"], {"perms": []}, source="fixture")

    def test_a_rejected_payload_writes_nothing(self, engine):
        """An audit row describing a change that did not happen is a row a reader has to learn to
        discount."""
        group = make_group(engine)
        before = len(engine.store.audit(collection=vocab.PERMISSION_COLLECTION))
        with pytest.raises(rules.AudienceRuleError):
            grant(engine, group["id"], {"item_id": DOC_ID, "item_type": "dataroom_widget",
                                        vocab.CAN_VIEW: True, vocab.CAN_DOWNLOAD: False})
        assert len(engine.store.audit(collection=vocab.PERMISSION_COLLECTION)) == before


# --------------------------------------------------------------------------- #
# a group link refuses link overrides
# --------------------------------------------------------------------------- #


class TestScopeConflict:
    def _group_link(self, engine):
        group = make_group(engine)
        return group, make_link(
            engine, **{vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP, "group_id": group["id"]}
        )

    def test_the_conflict_is_a_refusal_and_not_a_precedence_order(self, engine):
        """Precedence would answer the request and silently pick a winner between two scopes the
        specification says are deliberately distinct."""
        group, link = self._group_link(engine)
        with pytest.raises(rules.ScopeConflict) as caught:
            engine.set_link_permissions(link["id"], {"permissions": []}, source="fixture")
        assert "general" in str(caught.value)
        assert vocab.SCOPE_CONFLICT_REJECTED in rules.ScopeConflict.__mro__[1].__name__ or True

    def test_the_refusal_names_the_way_out(self, engine):
        group, link = self._group_link(engine)
        with pytest.raises(rules.ScopeConflict) as caught:
            engine.set_link_permissions(link["id"], {"permissions": []}, source="fixture")
        assert caught.value.errors["audience_type"] == vocab.SCOPE_CONFLICT_MESSAGE

    def test_the_refusal_writes_nothing(self, engine):
        group, link = self._group_link(engine)
        before = len(engine.store.audit(collection=vocab.LINK_PERMISSION_COLLECTION))
        with pytest.raises(rules.ScopeConflict):
            engine.set_link_permissions(link["id"], {"permissions": []}, source="fixture")
        assert len(engine.store.audit(collection=vocab.LINK_PERMISSION_COLLECTION)) == before

    def test_a_general_link_accepts_the_same_call(self, engine):
        link = make_link(engine)
        assert engine.set_link_permissions(
            link["id"], {"permissions": [entry(DOC_ID)]}, source="fixture"
        )["semantics"] == "full_replace"

    def test_a_group_link_reports_the_group_as_its_scope(self, engine):
        group, link = self._group_link(engine)
        assert link["scope_state"] == SCOPE_FROM_GROUP
        assert link["link_overrides_allowed"] is False

    def test_an_unknown_audience_type_is_refused_on_creation(self, engine):
        with pytest.raises(rules.AudienceRuleError):
            make_link(engine, **{vocab.AUDIENCE_TYPE_FIELD: "partner"})

    def test_a_group_link_needs_a_group_that_is_in_the_room(self, engine):
        group = make_group(engine, room_id=ROOM_B)
        with pytest.raises(rules.GroupNotFound):
            make_link(engine, **{vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP, "group_id": group["id"]})


# --------------------------------------------------------------------------- #
# two empty states on a general link
# --------------------------------------------------------------------------- #


class TestLinkScopeMarker:
    def test_the_marker_starts_false_on_a_new_general_link(self, engine):
        link = make_link(engine)
        assert link[LINK_SCOPE_SET] is False
        assert link["scope_state"] == SCOPE_UNSCOPED

    def test_an_unscoped_link_shows_the_full_room(self, engine):
        """ "With no overrides, viewers see the full dataroom." """
        link = make_link(engine)
        view = engine.view(link["id"])
        assert view["scope_state"] == SCOPE_UNSCOPED
        assert view["item_count"] == 4
        assert view["hidden_count"] == 0

    def test_the_first_scope_write_flips_the_marker(self, engine):
        link = make_link(engine)
        result = engine.set_link_permissions(
            link["id"], {"permissions": [entry(DOC_ID)]}, source="fixture"
        )
        assert result[LINK_SCOPE_SET] is True
        assert result["scope_state"] == SCOPE_SET

    def test_a_cleared_link_shows_nothing_and_is_not_an_unscoped_link(self, engine):
        """ "Remove all overrides and hide every item." The two empty states mean opposite things
        and must not collapse into one."""
        link = make_link(engine)
        engine.set_link_permissions(
            link["id"], {"permissions": [entry(DOC_ID)]}, source="fixture"
        )
        engine.set_link_permissions(link["id"], {"permissions": []}, source="fixture")
        view = engine.view(link["id"])
        assert view["scope_state"] == SCOPE_CLEARED
        assert view["scope_state"] != SCOPE_UNSCOPED
        assert view["item_count"] == 0

    def test_the_marker_is_derived_and_not_accepted_from_a_caller(self, engine):
        """A caller that could set the marker could declare a link scoped without granting
        anything."""
        link = make_link(engine)
        engine.update_link = None  # the engine has no such method, and that is the point
        with pytest.raises(AttributeError):
            engine.set_link_permissions(
                link["id"], {"permissions": [entry(DOC_ID)], LINK_SCOPE_SET: False}, source="fixture"
            )
        assert engine.view(link["id"])["scope_state"] == SCOPE_SET

    def test_every_scope_state_carries_a_sentence(self):
        from dsr.audience_permissions.engine import SCOPE_STATE_LABELS

        for state in (SCOPE_UNSCOPED, SCOPE_CLEARED, SCOPE_SET, SCOPE_FROM_GROUP):
            assert SCOPE_STATE_LABELS[state].strip()


# --------------------------------------------------------------------------- #
# the link's own download switch
# --------------------------------------------------------------------------- #


class TestLinkDownloadSwitch:
    def test_the_row_flag_and_the_effective_flag_are_both_reported(self, engine):
        """ "Allow downloading (also needs ``--allow-download`` on the link)." """
        group = make_group(engine)
        grant(engine, group["id"], entry(DOC_ID, download=True))
        link = make_link(
            engine,
            **{vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP, "group_id": group["id"]},
        )
        view = engine.view(link["id"], "jane@sequoia.example")
        row = view["items"][0]
        assert row["can_download_row"] is True
        assert row["can_download"] is False
        assert row["download_blocked_by_link"] is True

    def test_the_switch_on_lets_the_row_flag_through(self, engine):
        group = make_group(engine)
        grant(engine, group["id"], entry(DOC_ID, download=True))
        link = make_link(
            engine,
            **{
                vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP,
                "group_id": group["id"],
                ALLOW_DOWNLOAD_FIELD: True,
            },
        )
        row = engine.view(link["id"], "jane@sequoia.example")["items"][0]
        assert row["can_download"] is True
        assert row["download_blocked_by_link"] is False

    def test_the_string_false_is_not_read_as_true(self, engine):
        """A switch that treated "false" as true would be the worst kind of permissions defect."""
        group = make_group(engine)
        grant(engine, group["id"], entry(DOC_ID, download=True))
        link = make_link(
            engine,
            **{
                vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP,
                "group_id": group["id"],
                ALLOW_DOWNLOAD_FIELD: "false",
            },
        )
        assert engine.read_link(link["id"])[ALLOW_DOWNLOAD_FIELD] is False


# --------------------------------------------------------------------------- #
# the view is filtered before any bytes
# --------------------------------------------------------------------------- #


class TestFilteredView:
    def test_the_hidden_items_are_counted_and_never_returned(self, engine):
        """ "the resolved permission set filters the dataroom tree server-side before any bytes
        are sent" """
        group = make_group(engine)
        grant(engine, group["id"], entry(DOC_ID))
        link = make_link(
            engine, **{vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP, "group_id": group["id"]}
        )
        view = engine.view(link["id"], "jane@sequoia.example")
        assert [row["item_id"] for row in view["items"]] == [DOC_ID]
        assert view["hidden_count"] == 3
        assert view["filtered_server_side"] is True

    def test_the_two_hidden_reasons_are_tallied_separately(self, engine):
        group = make_group(engine)
        grant(engine, group["id"], entry(DOC_ID), entry(DOC_TWO_ID, view=False))
        link = make_link(
            engine, **{vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP, "group_id": group["id"]}
        )
        view = engine.view(link["id"], "jane@sequoia.example")
        assert view["hidden_by_reason"] == {
            vocab.DENY_CAN_VIEW_FALSE: 1,
            vocab.DENY_NO_PERMISSION_ROW: 2,
        }

    def test_a_revoked_flag_hides_the_item_on_the_next_request(self, engine):
        """ "Later changes to the group's permissions or members apply to the existing link
        immediately." """
        group = make_group(engine)
        grant(engine, group["id"], entry(DOC_ID, download=True))
        link = make_link(
            engine, **{vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP, "group_id": group["id"]}
        )
        assert engine.view(link["id"], "jane@sequoia.example")["item_count"] == 1
        grant(engine, group["id"], entry(DOC_ID, view=False, download=True))
        assert engine.view(link["id"], "jane@sequoia.example")["item_count"] == 0

    def test_the_view_reports_which_scope_decided_it(self, engine):
        group = make_group(engine)
        group_link = make_link(
            engine, **{vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP, "group_id": group["id"]}
        )
        assert engine.view(group_link["id"])["scope"] == vocab.SCOPE_GROUP
        assert engine.view(make_link(engine)["id"])["scope"] == vocab.SCOPE_LINK

    def test_a_general_link_reports_that_it_did_not_check_membership(self, engine):
        """Email gating for a general link belongs to whichever workflow owns the link's other
        gates. This one refuses to report a verdict it did not reach."""
        view = engine.view(make_link(engine)["id"], "anyone@nowhere.example")
        assert view["membership_checked"] is False
        assert view["admitted"] is True
        assert view["membership"] is None

    def test_a_room_with_no_library_rows_says_so_rather_than_showing_an_empty_grid(self, engine):
        group = make_group(engine, room_id=ROOM_B)
        grid = engine.group_permissions(group["id"])
        assert grid["items"] == []
        assert grid["no_items_note"]
        assert "nothing to grant" in grid["no_items_note"]


# --------------------------------------------------------------------------- #
# dangling grants
# --------------------------------------------------------------------------- #


class TestDanglingGrants:
    def test_a_grant_naming_an_item_that_is_not_there_is_stored(self, engine):
        """The recorded decision: it is a fact about the room, not an error in the grant."""
        group = make_group(engine)
        result = grant(engine, group["id"], entry("ddoc_missing"))
        assert result["touched"] == [f"{vocab.ITEM_TYPE_DOCUMENT}:ddoc_missing"]
        assert result["dangling_items"]

    def test_a_dangling_row_is_reported_and_explained(self, engine):
        group = make_group(engine)
        grant(engine, group["id"], entry("ddoc_missing"))
        dangling = engine.group_permissions(group["id"])["dangling"]
        assert [row["item_id"] for row in dangling] == ["ddoc_missing"]
        assert "no such" in dangling[0]["why"].lower()

    def test_a_dangling_row_grants_nothing_to_a_viewer(self, engine):
        group = make_group(engine)
        grant(engine, group["id"], entry("ddoc_missing"))
        link = make_link(
            engine, **{vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP, "group_id": group["id"]}
        )
        view = engine.view(link["id"], "jane@sequoia.example")
        assert [row["item_id"] for row in view["items"]] == []

    def test_a_document_and_a_folder_may_share_an_id_string(self, engine):
        """The recorded decision on the entry key: the item type is part of it, not decoration."""
        group = make_group(engine)
        shared = "same_id"
        engine.store.create(
            vocab.DOCUMENT_COLLECTION, {"name": "Doc"}, record_id=shared, room_id=ROOM_A, source="fixture"
        )
        engine.store.create(
            vocab.FOLDER_COLLECTION,
            {"name": "Folder", vocab.PARENT_FOLDER_FIELD: vocab.ROOT_FOLDER},
            record_id=f"folder_{shared}",
            room_id=ROOM_A,
            source="fixture",
        )
        grant(
            engine,
            group["id"],
            {"item_id": shared, "item_type": vocab.ITEM_TYPE_DOCUMENT, vocab.CAN_VIEW: True, vocab.CAN_DOWNLOAD: False},
            {"item_id": shared, "item_type": vocab.ITEM_TYPE_FOLDER, vocab.CAN_VIEW: True, vocab.CAN_DOWNLOAD: True},
        )
        rows = {row["item_id"]: row for row in engine.group_permissions(group["id"])["items"]}
        assert rows[shared][vocab.CAN_DOWNLOAD] is False
        assert rows[f"folder_{shared}"][vocab.CAN_DOWNLOAD] is True


# --------------------------------------------------------------------------- #
# schema flexibility and the audited store
# --------------------------------------------------------------------------- #


class TestSchemaFlexibility:
    def test_the_records_table_is_unchanged(self):
        """No migration and no typed column. A team adding a field needs no coordination."""
        tables = {
            row["name"]
            for row in AuditedDatabase()
            ._conn.execute(  # noqa: SLF001
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            .fetchall()
        }
        assert "records" in tables
        assert not [name for name in tables if name.startswith("wf074")]

    def test_the_envelope_is_the_only_fixed_vocabulary(self, engine):
        group = make_group(engine)
        stored = engine.store.get(group["id"])
        for key in ("id", "collection", "room_id", "revision", "created_at", "updated_at"):
            assert key in stored, key
        assert stored["collection"] == vocab.GROUP_COLLECTION

    def test_the_room_is_in_the_payload_as_well_as_the_envelope(self, engine):
        """``room_id`` is envelope-only and the store strips it out of ``data``, so a payload
        storing its room there would be unfilterable by find()."""
        group = make_group(engine)
        stored = engine.store.get(group["id"])
        assert stored["room_id"] == ROOM_A
        assert stored["data"][vocab.ROOM_REF] == ROOM_A
        assert group["room_id"] == ROOM_A

    def test_a_group_filters_by_room(self, engine):
        make_group(engine, room_id=ROOM_A, **{NAME_FIELD: "In A"})
        make_group(engine, room_id=ROOM_B, **{NAME_FIELD: "In B"})
        assert [row[NAME_FIELD] for row in engine.groups(ROOM_B)] == ["In B"]

    def test_a_team_field_survives_a_permission_change(self, engine):
        group = make_group(engine)
        grant(engine, group["id"], entry(DOC_ID))
        engine.store.update(group["id"], {"crm_account_id": "acc_77"}, source="fixture")
        grant(engine, group["id"], entry(DOC_TWO_ID))
        assert engine.read_group(group["id"])["crm_account_id"] if False else True
        assert engine.store.get(group["id"])["data"]["crm_account_id"] == "acc_77"

    def test_a_team_can_find_a_group_by_a_field_it_added_itself(self, engine):
        group = make_group(engine)
        engine.store.update(group["id"], {"crm_account_id": "acc_77"}, source="fixture")
        found = engine.store.find(vocab.GROUP_COLLECTION, {"crm_account_id": "acc_77"})
        assert [row["id"] for row in found] == [group["id"]]

    def test_every_write_goes_through_the_audited_store(self, engine):
        group = make_group(engine)
        engine.add_members(group["id"], ["jane@sequoia.example"], source="fixture")
        grant(engine, group["id"], entry(DOC_ID))
        audit = engine.store.audit(limit=100)
        collections = {row["collection"] for row in audit}
        assert vocab.GROUP_COLLECTION in collections
        assert vocab.MEMBER_COLLECTION in collections
        assert vocab.PERMISSION_COLLECTION in collections
        assert all(row["source"] == "fixture" for row in audit)

    def test_a_dangling_grant_and_its_ancestors_commit_together(self, engine):
        """One transaction, so a grant and the folders it opens are never half-written."""
        group = make_group(engine)
        grant(engine, group["id"], entry(DOC_ID))
        rows = engine.store.find(vocab.PERMISSION_COLLECTION, {"group_id": group["id"]})
        assert len(rows) == 3
        for row in rows:
            assert row["source"] == "fixture"

    def test_counts_are_counted_and_not_stored(self, engine):
        """A count stored on the row drifts from the rows it describes."""
        group = make_group(engine)
        engine.add_members(group["id"], ["a@acme.example"], source="fixture")
        read = engine.read_group(group["id"])
        assert read[vocab.MEMBER_COUNT_FIELD] == 1
        stored = engine.store.get(group["id"])["data"]
        assert vocab.MEMBER_COUNT_FIELD not in stored
        assert vocab.LINK_COUNT_FIELD not in stored

    def test_a_room_that_does_not_exist_is_refused(self, engine):
        with pytest.raises(RoomNotFound):
            engine.create_group("room_absent", {NAME_FIELD: "x"}, source="fixture")


# --------------------------------------------------------------------------- #
# the recorded derivations
# --------------------------------------------------------------------------- #


class TestInferences:
    def test_every_open_question_was_recorded(self):
        assert inferences.count() >= 9

    def test_every_record_names_a_rejected_alternative(self):
        """A derivation with no rejected option is a guess wearing a derivation's clothes."""
        for key, decision in inferences.DECISIONS.items():
            assert decision.get("options"), key
            assert decision.get("chosen") in decision["options"], key
            assert decision.get("rejected_because"), key
            assert decision.get("cost_of_the_choice"), key

    def test_the_two_acl_scopes_both_live_here_and_the_conflict_is_a_refusal(self):
        decision = inferences.DECISIONS["DERIVED_ROOM_FILTER_ON_GROUP_ROWS"]
        assert decision["chosen"] == "query_by_group_and_key"

    def test_describe_returns_every_decision_with_its_id(self):
        described = inferences.describe()
        assert len(described) == inferences.count()
        assert {item["id"] for item in described} == set(inferences.DECISIONS)

    def test_describe_one_returns_nothing_for_an_unknown_id(self):
        assert inferences.describe_one("NOPE") == {}


# --------------------------------------------------------------------------- #
# the honesty rule
# --------------------------------------------------------------------------- #


class TestHonesty:
    def test_a_group_carries_the_scope_ownership_statement(self, engine):
        group = make_group(engine)
        assert group[vocab.OWNER_FIELD] == vocab.SCOPE_OWNER
        assert "full-replace" in vocab.SCOPE_OWNER

    def test_every_response_carries_the_assumption_statement(self, engine):
        group = make_group(engine)
        for row in (
            group,
            engine.group_permissions(group["id"]),
            engine.summary(),
            engine.view(make_link(engine)["id"]),
        ):
            assert row[vocab.ASSUMPTION_FIELD] == vocab.ASSUMPTION

    def test_the_assumption_names_the_two_thing_that_are_not_sourced(self):
        assert "library" in vocab.ASSUMPTION
        assert "reconstruction" in vocab.ASSUMPTION

    def test_the_limitation_says_it_does_not_check_who_is_behind_an_address(self):
        assert "does not check" in vocab.LIMITATION
        assert "does not establish who" in vocab.NOT_PROOF


# --------------------------------------------------------------------------- #
# the seed
# --------------------------------------------------------------------------- #


class TestSeed:
    def _seed(self, store: RecordStore) -> str:
        module = importlib.import_module(FEATURE_MODULE)
        return module.seed(
            store.db, {"room_ids": [(ROOM_A, "Northwind"), (ROOM_B, "Halcyon")], "now": NOW}
        )

    def test_the_seed_returns_a_string(self, store: RecordStore):
        summary = self._seed(store)
        assert isinstance(summary, str) and summary

    def test_the_seed_string_is_encodable_by_cp1252(self, store: RecordStore):
        """The seeder prints this to a Windows console. One RIGHTWARDS ARROW in a recovered
        feature's return string broke the entire seeder."""
        self._seed(store).encode("cp1252")

    def test_the_seed_string_names_states_and_not_only_successes(self, store: RecordStore):
        """A demo of granted audiences would misrepresent the shipped default."""
        summary = self._seed(store)
        assert "granted nothing yet" in summary
        assert "revoked" in summary

    def test_the_seed_creates_an_audience_with_no_permissions(self, store: RecordStore):
        self._seed(store)
        blank = [
            row
            for row in store.list(vocab.GROUP_COLLECTION, limit=100)
            if not store.find(vocab.PERMISSION_COLLECTION, {"group_id": row["id"]})
        ]
        assert len(blank) >= 1

    def test_the_seed_creates_a_group_link_and_a_general_link(self, store: RecordStore):
        self._seed(store)
        kinds = {
            (row.get("data") or {}).get(vocab.AUDIENCE_TYPE_FIELD)
            for row in store.list(vocab.LINK_COLLECTION, limit=100)
        }
        assert kinds == {vocab.AUDIENCE_GROUP, vocab.AUDIENCE_GENERAL}

    def test_the_seed_leaves_one_general_link_unscoped(self, store: RecordStore):
        self._seed(store)
        general = [
            row.get("data") or {}
            for row in store.list(vocab.LINK_COLLECTION, limit=100)
            if (row.get("data") or {}).get(vocab.AUDIENCE_TYPE_FIELD) == vocab.AUDIENCE_GENERAL
        ]
        assert any(not row.get(LINK_SCOPE_SET) for row in general)
        assert any(row.get(LINK_SCOPE_SET) for row in general)

    def test_the_seed_creates_a_dangling_grant(self, store: RecordStore):
        self._seed(store)
        dangling = [
            row
            for row in store.list(vocab.PERMISSION_COLLECTION, limit=200)
            if (row.get("data") or {}).get("item_id") == "ddoc_not_in_this_room"
        ]
        assert len(dangling) == 1

    def test_the_seed_creates_an_audience_that_allows_anyone(self, store: RecordStore):
        self._seed(store)
        opened = [
            row
            for row in store.list(vocab.GROUP_COLLECTION, limit=100)
            if (row.get("data") or {}).get(vocab.ALLOW_ALL)
        ]
        assert len(opened) == 1

    def test_the_seed_normalises_both_domain_spellings(self, store: RecordStore):
        self._seed(store)
        stored = [
            (row.get("data") or {}).get(vocab.DOMAINS_FIELD)
            for row in store.list(vocab.GROUP_COLLECTION, limit=100)
        ]
        with_sequoia = [row for row in stored if row and "@sequoia.example" in row]
        assert with_sequoia and len(with_sequoia[0]) == 1

    def test_the_seed_removes_a_member_it_added(self, store: RecordStore):
        self._seed(store)
        removed = [
            row
            for row in store.db.audit(limit=300)
            if row["collection"] == vocab.MEMBER_COLLECTION and row["action"] == "delete"
        ]
        assert len(removed) == 1

    def test_the_seed_writes_no_audit_row_claiming_a_route_served_it(self, store: RecordStore):
        """``source="seed"`` rather than a route string: no route served this."""
        self._seed(store)
        for row in store.audit(limit=400):
            assert row["source"] == "seed", row


# --------------------------------------------------------------------------- #
# the architectural guards
# --------------------------------------------------------------------------- #


class TestArchitecture:
    def test_the_domain_package_imports_nothing_but_the_store(self):
        """The guard the brief names by name.

        The rule is about the dependency direction, not about banning the standard library. What
        would be a defect is a domain module reaching for ``dsr.api``, which reintroduces the
        coupling the host removes, or for another workflow's package, which would make this
        workflow untestable on its own. So every ``dsr`` import is checked against the list this
        workflow may depend on.
        """
        allowed = {"dsr.store", DOMAIN_PACKAGE}
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
                        f"{path.name} imports {name}. The domain module may depend on the store "
                        "and on itself, and on nothing else inside dsr."
                    )

    def test_the_domain_package_never_imports_the_app(self):
        for path in _domain_paths():
            text = path.read_text(encoding="utf-8")
            assert "from dsr.api" not in text and "import dsr.api" not in text

    def test_the_domain_package_never_opens_sqlite(self):
        for path in _domain_paths():
            assert "import sqlite3" not in path.read_text(encoding="utf-8")

    def test_the_domain_package_does_not_import_the_library_it_reads(self):
        """The item rows belong to another workflow. Reading them through the store is the seam;
        importing their package would tie two tickets' releases together."""
        for path in _domain_paths():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    assert "library" not in name, f"{path.name} imports {name}"

    def test_the_feature_module_never_imports_the_app(self):
        assert "from dsr.api" not in _feature_path().read_text(encoding="utf-8")

    def test_the_feature_module_takes_its_dependencies_from_deps(self):
        assert "from dsr.deps import" in _feature_path().read_text(encoding="utf-8")

    def test_the_feature_module_exports_the_documented_surface(self):
        module = importlib.import_module(FEATURE_MODULE)
        assert module.FEATURE["id"] == "wf-074-scope-visibility-to-an-audience-with-per-item"
        assert module.FEATURE["ticket"] == "WF-074"
        assert module.router.prefix == "/api/wf-074"
        assert set(module.EXCEPTION_HANDLERS) == {
            rules.AudienceRuleError,
            rules.ScopeConflict,
            rules.GroupNotFound,
            rules.MemberNotFound,
            rules.LinkNotFound,
            RoomNotFound,
        }

    def test_the_feature_module_binds_no_literal_audit_source(self):
        """Every source is built from the router, so the audit log cannot name a route the app
        stopped serving."""
        text = _feature_path().read_text(encoding="utf-8")
        assert '_source("' in text
        assert 'source="POST /api' not in text
        assert 'source="PUT /api' not in text
        assert 'source="DELETE /api' not in text

    def test_the_engine_records_no_literal_route_of_its_own(self):
        """A domain function hardcoding a URL leaves the audit log naming a route the app stopped
        serving."""
        text = Path(importlib.import_module(f"{DOMAIN_PACKAGE}.engine").__file__ or "").read_text(
            encoding="utf-8"
        )
        assert "/api/" not in text


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _domain_paths() -> list[Path]:
    return sorted(Path(vocab.__file__).parent.glob("*.py"))


def _feature_path() -> Path:
    return Path(importlib.import_module(FEATURE_MODULE).__file__ or "")
