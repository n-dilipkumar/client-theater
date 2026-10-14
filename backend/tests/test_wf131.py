"""WF-131 domain tests: the policy matrix, the allow-list, the evaluation, the log.

These drive :mod:`dsr.content_protection` directly, over a temporary database,
with no HTTP client. The HTTP surface has its own module, ``test_wf131_http.py``.

Every test names the rule it protects. The clock is frozen at :data:`NOW`, so no
test depends on today's date or on a weekday that will pass. The database is a
fresh one per test, through the shared ``store`` fixture, so this file passes
alone and under ``pytest-xdist`` in any order.
"""

from __future__ import annotations

import ast
import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import dsr.audit_export.vocabulary as audit_vocab
import dsr.content_protection as domain
import dsr.link_gating.rules as gate_rules
import dsr.view_only_access.vocabulary as download_vocab
import dsr.viewer_watermark.vocabulary as watermark_vocab
import dsr.workspace_roles.vocabulary as workspace_vocabulary
import pytest
from dsr.content_protection import (
    ACTION_CODES,
    ACTION_COLLECTION,
    POLICY_COLLECTION,
    ContentProtectionError,
    PolicyNotFound,
    ProtectionBook,
    ProtectionDenied,
    bulk_download_alert,
    effective_settings,
    evaluate,
    ip_allowed,
    normalize_allowlist,
    normalize_policy,
    normalize_recipient,
)
from dsr.link_gating.agreement import AgreementEngine
from dsr.link_gating.gate import GateEngine
from dsr.store import RecordStore

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

DOMAIN_DIR = Path(domain.__file__).parent
FEATURE_MODULE = Path(domain.__file__).parent.parent / "features" / "wf131_content_protection.py"

#: ``dsr.api`` / ``dsr.features`` are absent because a domain module must never
#: reach for the app or for another workflow's feature module. The four bundled
#: workflows and the two primitives are present because that is exactly what this
#: bundle composes.
ALLOWED_DSR_PREFIXES = (
    "dsr.store",
    "dsr.db.audited",
    "dsr.content_protection",
    "dsr.link_gating",
    "dsr.view_only_access",
    "dsr.viewer_watermark",
    "dsr.workspace_roles",
    "dsr.audit_export",
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def room(store: RecordStore) -> dict:
    return store.create(
        "room", {"name": "Northwind", "account": "Northwind"}, actor="dana", source="test"
    )


@pytest.fixture()
def links(store: RecordStore) -> GateEngine:
    return GateEngine(store, now=lambda: NOW)


@pytest.fixture()
def agreements(store: RecordStore) -> AgreementEngine:
    return AgreementEngine(store, now=lambda: NOW)


@pytest.fixture()
def link(links: GateEngine, room: dict) -> dict:
    """A WF-069 link with an email gate and a passcode, as the gate itself makes it."""
    return links.create_link(
        room["id"],
        {
            "title": "Northwind evaluation",
            "document_id": "doc-demo",
            "email_protected": True,
            "password": "northwind-2026",
        },
        actor="dana",
        source="POST /api/wf-069/links",
    )


@pytest.fixture()
def agreement(agreements: AgreementEngine, room: dict) -> dict:
    return agreements.create_agreement(
        room["id"],
        {"title": "Northwind mutual NDA", "body": "Keep it confidential.", "kind": "nda"},
        actor="dana",
        source="POST /api/wf-070/agreements",
    )


@pytest.fixture()
def book(store: RecordStore) -> ProtectionBook:
    return ProtectionBook(store, now=lambda: NOW)


def make_policy(book: ProtectionBook, link: dict, room: dict, **fields) -> dict:
    payload = {"link_id": link["id"], "recipient": "a.buyer@northwind.example", **fields}
    return book.create_policy(
        room["id"], payload, actor="dana", source="POST /api/wf-131/links/{link_id}/policies"
    )


# --------------------------------------------------------------------------- #
# The vocabulary is reused, not re-declared
# --------------------------------------------------------------------------- #


class TestReusedVocabulary:
    def test_the_seven_borrowed_fields_are_wf069s_own_names(self):
        """WF-069 quotes this list verbatim from the research's extensibility note."""
        for field in domain.REUSED_FIELDS:
            assert field in gate_rules.PRESET_COVERED_FIELDS, field

    def test_only_the_recipient_and_the_allowlist_are_this_workflows_own(self):
        assert set(domain.OWNED_FIELDS) == {"require_passcode", "ip_allowlist"}
        assert set(domain.OWNED_FIELDS).isdisjoint(domain.REUSED_FIELDS)

    def test_the_watermark_field_is_wf071s_own(self):
        assert domain.FIELD_ENABLE_WATERMARK == watermark_vocab.ENABLE_FIELD

    def test_the_download_field_is_wf072s_own(self):
        assert domain.FIELD_ALLOW_DOWNLOAD == download_vocab.ALLOW_DOWNLOAD

    def test_the_action_codes_are_wf079s_own_where_it_names_them(self):
        assert ACTION_CODES["view"] == audit_vocab.DOCUMENT_VIEWED
        assert ACTION_CODES["share"] == audit_vocab.DOCUMENT_FORWARDED
        assert ACTION_CODES["revoke"] == audit_vocab.DOCUMENT_EXPIRED

    def test_the_only_minted_code_sits_above_wf079s_floor(self):
        """WF-079 names no download code, so this build mints one above its floor."""
        assert ACTION_CODES["download"] >= audit_vocab.WORKSPACE_CODE_FLOOR
        assert ACTION_CODES["download"] not in audit_vocab.NAMED_LIFECYCLE.values()

    def test_the_plan_tier_is_wf077s_own(self):
        assert domain.PLAN_REQUIRED == workspace_vocabulary.PLAN_BUSINESS
        assert domain.PLAN_REQUIRED in workspace_vocabulary.PLANS

    def test_identity_is_wf069s_own_function(self):
        assert normalize_recipient("Buyer@Northwind.example") == "buyer@northwind.example"

    def test_the_policy_collection_is_not_a_dependency_collection(self):
        assert POLICY_COLLECTION not in gate_rules.PRESET_COVERED_FIELDS
        assert POLICY_COLLECTION.startswith("wf131_")
        assert ACTION_COLLECTION.startswith("wf131_")

    def test_the_published_vocabulary_names_each_source(self):
        published = domain.describe()
        assert published["ticket"] == "WF-131"
        assert published["reused"]["link_collection"] == "wf069_gated_link"
        assert published["reused"]["agreement_collection"] == "wf070_agreement"
        assert "WF-079" in published["reused"]["action_codes_source"]
        sources = {entry["field"]: entry["source"] for entry in published["fields"]}
        assert "WF-069" in sources["email_protected"]
        assert "WF-071" in sources["enable_watermark"]
        assert "WF-072" in sources["allow_download"]


# --------------------------------------------------------------------------- #
# The recipients and the allow-list
# --------------------------------------------------------------------------- #


class TestTheAllowList:
    def test_an_exact_address_becomes_a_host_network(self):
        assert normalize_allowlist(["203.0.113.9"]) == ["203.0.113.9/32"]

    def test_a_cidr_block_is_kept(self):
        assert normalize_allowlist("10.0.0.0/8") == ["10.0.0.0/8"]

    def test_a_comma_or_newline_string_is_accepted(self):
        assert normalize_allowlist("203.0.113.9, 10.0.0.0/8\n198.51.100.1") == [
            "203.0.113.9/32",
            "10.0.0.0/8",
            "198.51.100.1/32",
        ]

    def test_a_duplicate_is_collapsed(self):
        assert normalize_allowlist(["10.0.0.0/8", "10.0.0.0/8"]) == ["10.0.0.0/8"]

    def test_an_empty_value_is_open(self):
        assert normalize_allowlist(None) == []
        assert normalize_allowlist("") == []

    @pytest.mark.parametrize("bad", ["not-an-ip", "999.1.1.1", "10.0.0.0/99"])
    def test_an_entry_that_is_not_an_address_is_refused(self, bad):
        with pytest.raises(ContentProtectionError) as refused:
            normalize_allowlist([bad])
        assert "ip_allowlist" in refused.value.errors

    def test_an_empty_allowlist_permits_from_anywhere(self):
        assert ip_allowed("203.0.113.9", []) is True

    def test_an_exact_address_matches_only_itself(self):
        assert ip_allowed("203.0.113.9", ["203.0.113.9/32"]) is True
        assert ip_allowed("203.0.113.10", ["203.0.113.9/32"]) is False

    def test_a_block_contains_an_address(self):
        assert ip_allowed("10.4.5.6", ["10.0.0.0/8"]) is True
        assert ip_allowed("11.4.5.6", ["10.0.0.0/8"]) is False

    def test_a_missing_address_fails_closed(self):
        assert ip_allowed(None, ["10.0.0.0/8"]) is False
        assert ip_allowed("garbage", ["10.0.0.0/8"]) is False

    def test_an_ipv4_address_is_not_in_an_ipv6_block(self):
        assert ip_allowed("203.0.113.9", ["2001:db8::/32"]) is False


# --------------------------------------------------------------------------- #
# The policy matrix
# --------------------------------------------------------------------------- #


class TestThePolicyRules:
    def test_every_switch_defaults_to_the_safe_direction(self):
        policy = normalize_policy({})
        for field in domain.POLICY_FIELDS:
            if field in (domain.FIELD_IP_ALLOWLIST, domain.FIELD_AGREEMENT_ID):
                continue
            if field == domain.FIELD_EXPIRES_AT:
                continue
            assert policy[field] is False, field
        assert policy[domain.FIELD_IP_ALLOWLIST] == []
        assert policy[domain.FIELD_AGREEMENT_ID] is None

    def test_an_nda_gate_without_an_agreement_is_refused(self):
        """WF-070's own rule, honoured per recipient."""
        with pytest.raises(ContentProtectionError) as refused:
            normalize_policy({"enable_agreement": True})
        assert "agreement_id" in refused.value.errors

    def test_a_null_agreement_clears_the_selection(self):
        policy = normalize_policy(
            {"agreement_id": None}, base={"agreement_id": "agr_1", "enable_agreement": False}
        )
        assert policy["agreement_id"] is None

    def test_an_empty_agreement_id_is_refused(self):
        with pytest.raises(ContentProtectionError):
            normalize_policy({"agreement_id": "  "})

    def test_a_bad_boolean_is_refused(self):
        with pytest.raises(ContentProtectionError):
            normalize_policy({"allow_download": "maybe"})

    def test_the_cli_on_off_spelling_is_accepted(self):
        assert normalize_policy({"allow_download": "on"})["allow_download"] is True
        assert normalize_policy({"allow_download": "off"})["allow_download"] is False

    def test_an_expiry_null_clears_it(self):
        policy = normalize_policy({"expires_at": None}, base={"expires_at": "2026-01-01"})
        assert policy["expires_at"] is None

    def test_a_backwards_expiry_string_is_refused(self):
        with pytest.raises(ContentProtectionError):
            normalize_policy({"expires_at": "not-a-date"})


class TestEffectiveSettings:
    def test_a_policy_cannot_remove_the_links_email_gate(self):
        settings = effective_settings({"email_protected": True}, {"email_protected": False})
        assert settings["email_required"] is True

    def test_a_policy_can_add_a_gate_the_link_does_not_have(self):
        settings = effective_settings({}, {"enable_agreement": True, "agreement_id": "a"})
        assert settings["agreement_required"] is True

    def test_the_earlier_expiry_wins(self):
        settings = effective_settings(
            {"expires_at": "2026-06-01T00:00:00+00:00"},
            {"expires_at": "2026-05-01T00:00:00+00:00"},
        )
        assert settings["expires_at"].startswith("2026-05-01")

    def test_a_policy_expiry_cannot_extend_the_links(self):
        settings = effective_settings(
            {"expires_at": "2026-05-01T00:00:00+00:00"},
            {"expires_at": "2027-01-01T00:00:00+00:00"},
        )
        assert settings["expires_at"].startswith("2026-05-01")

    def test_a_passcode_is_only_required_when_the_link_has_one(self):
        assert effective_settings({}, {"require_passcode": True})["passcode_required"] is False
        assert (
            effective_settings({"password_hash": "x"}, {"require_passcode": True})[
                "passcode_required"
            ]
            is True
        )

    def test_a_revoked_policy_is_revoked(self):
        assert effective_settings({}, {"revoked": True})["revoked"] is True
        assert effective_settings({"deleted_at": "2026-01-01"}, {})["revoked"] is True


# --------------------------------------------------------------------------- #
# The evaluation
# --------------------------------------------------------------------------- #


def context(**overrides):
    base = {"email_verified": True, "passcode_ok": True, "nda_accepted": True, "ip": "10.4.5.6"}
    base.update(overrides)
    return base


class TestEvaluate:
    def test_everything_satisfied_is_allowed(self):
        policy = normalize_policy({"require_passcode": True})
        decision = evaluate(
            {"email_protected": True, "password_hash": "x"},
            policy,
            context(),
            now=NOW,
        )
        assert decision["allowed"] is True
        assert decision["decision"] == "allow"
        assert decision["denied_by"] == []

    def test_the_watermark_is_reported_and_never_denies(self):
        decision = evaluate({}, normalize_policy({"enable_watermark": True}), context(), now=NOW)
        assert decision["allowed"] is True
        assert decision["watermark"]["enabled"] is True
        assert decision["protections"]["watermark"]["allowed"] is True

    def test_an_off_list_address_is_denied_and_named(self):
        decision = evaluate(
            {},
            normalize_policy({"ip_allowlist": ["10.0.0.0/8"]}),
            context(ip="198.51.100.7"),
            now=NOW,
        )
        assert decision["allowed"] is False
        assert "ip" in decision["denied_by"]
        assert decision["reasons"] == [ProtectionDenied.REASON_IP]

    def test_a_missing_verified_email_is_denied(self):
        decision = evaluate(
            {"email_protected": True}, normalize_policy({}), context(email_verified=False), now=NOW
        )
        assert decision["denied_by"] == ["email"]

    def test_a_wrong_passcode_is_denied(self):
        decision = evaluate(
            {"password_hash": "x"},
            normalize_policy({"require_passcode": True}),
            context(passcode_ok=False),
            now=NOW,
        )
        assert "passcode" in decision["denied_by"]

    def test_an_unaccepted_nda_is_denied(self):
        decision = evaluate(
            {},
            normalize_policy({"enable_agreement": True, "agreement_id": "a"}),
            context(nda_accepted=False),
            now=NOW,
        )
        assert "nda" in decision["denied_by"]

    def test_several_refusals_are_all_reported(self):
        decision = evaluate(
            {"email_protected": True},
            normalize_policy({"enable_agreement": True, "agreement_id": "a"}),
            {"email_verified": False, "nda_accepted": False},
            now=NOW,
        )
        assert set(decision["denied_by"]) == {"email", "nda"}

    def test_an_expired_policy_is_denied_with_the_link_expired_wording(self):
        decision = evaluate(
            {},
            normalize_policy({"expires_at": "2026-01-01"}),
            context(),
            now=NOW,
        )
        assert "expiry" in decision["denied_by"]
        assert decision["message"] == ProtectionDenied.MESSAGES[ProtectionDenied.REASON_EXPIRED]

    def test_a_boundary_expiry_is_still_open_at_the_instant(self):
        """WF-069's rule: ``now > expires_at``, so at the instant it is still open."""
        decision = evaluate(
            {}, normalize_policy({"expires_at": NOW.isoformat()}), context(), now=NOW
        )
        assert decision["allowed"] is True

    def test_a_revoked_policy_is_denied_with_the_closed_wording(self):
        decision = evaluate({}, normalize_policy({}) | {"revoked": True}, context(), now=NOW)
        assert "revocation" in decision["denied_by"]
        assert decision["message"] == ProtectionDenied.MESSAGES[ProtectionDenied.REASON_CLOSED]

    def test_the_download_decision_is_wf072s_three_way_and(self):
        policy = normalize_policy({"allow_download": True})
        decision = evaluate(
            {},
            policy,
            context(download={"bulk_download": True, "can_download": False}),
            now=NOW,
        )
        # The room and the item do not permit it, so the file is withheld though the
        # link's own switch is on - WF-072's AND, not a fourth toggling here.
        assert decision["download"]["download"] is False

    def test_a_denied_file_does_not_deny_the_room(self):
        decision = evaluate({}, normalize_policy({"allow_download": True}), context(), now=NOW)
        assert decision["allowed"] is True
        assert decision["download"]["download"] is False


# --------------------------------------------------------------------------- #
# The bulk-download alert
# --------------------------------------------------------------------------- #


def download(recipient, *, minutes_ago, **overrides):
    row = {
        "recipient": recipient,
        "action": "download",
        "occurred_at": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
    }
    row.update(overrides)
    return row


class TestBulkAlert:
    def test_a_recipients_repeated_downloads_raise_the_alert(self):
        rows = [download("a@b.example", minutes_ago=1) for _ in range(5)]
        alert = bulk_download_alert(rows, now=NOW, threshold=5)
        assert alert["alert"] is True
        assert alert["recipients"] == ["a@b.example"]

    def test_a_count_below_the_threshold_does_not(self):
        rows = [download("a@b.example", minutes_ago=1) for _ in range(4)]
        assert bulk_download_alert(rows, now=NOW, threshold=5)["alert"] is False

    def test_five_different_recipients_is_not_a_pattern(self):
        rows = [download(f"{index}@b.example", minutes_ago=1) for index in range(5)]
        assert bulk_download_alert(rows, now=NOW, threshold=5)["alert"] is False

    def test_a_download_outside_the_window_is_not_counted(self):
        rows = [download("a@b.example", minutes_ago=200) for _ in range(6)]
        assert (
            bulk_download_alert(rows, now=NOW, threshold=5, window=timedelta(minutes=60))["alert"]
            is False
        )

    def test_a_view_does_not_count_towards_the_pattern(self):
        rows = [download("a@b.example", minutes_ago=1, action="view") for _ in range(6)]
        assert bulk_download_alert(rows, now=NOW, threshold=5)["alert"] is False

    def test_the_alert_says_it_is_not_a_block(self):
        alert = bulk_download_alert([], now=NOW)
        assert "block" in alert["note"].lower()


# --------------------------------------------------------------------------- #
# The book
# --------------------------------------------------------------------------- #


class TestTheBook:
    def test_a_policy_is_stored_in_this_workflows_own_collection(
        self, store: RecordStore, book: ProtectionBook, link: dict, room: dict
    ):
        view = make_policy(book, link, room, allow_download=True)
        rows = store.find(POLICY_COLLECTION, {"link_id": link["id"]})
        assert len(rows) == 1
        assert rows[0]["data"]["allow_download"] is True
        assert view["recipient"] == "a.buyer@northwind.example"

    def test_two_recipients_are_two_rows_on_one_link(
        self, book: ProtectionBook, link: dict, room: dict
    ):
        make_policy(book, link, room)
        make_policy(book, link, room, recipient="b.buyer@northwind.example")
        policies = book.list_policies(link["id"])
        assert {policy["recipient"] for policy in policies} == {
            "a.buyer@northwind.example",
            "b.buyer@northwind.example",
        }

    def test_a_policy_for_a_link_that_is_not_there_is_refused(
        self, book: ProtectionBook, room: dict
    ):
        with pytest.raises(PolicyNotFound):
            book.create_policy(
                room["id"],
                {"link_id": "not-a-link", "recipient": "a@b.example"},
                source="POST",
            )

    def test_a_passcode_requirement_on_a_link_without_one_is_refused(
        self, book: ProtectionBook, links: GateEngine, room: dict
    ):
        ungated = links.create_link(
            room["id"], {"document_id": "doc", "email_protected": True}, source="POST"
        )
        with pytest.raises(ContentProtectionError) as refused:
            make_policy(book, ungated, room, require_passcode=True)
        assert "require_passcode" in refused.value.errors

    def test_an_nda_gate_on_a_missing_agreement_is_refused(
        self, book: ProtectionBook, link: dict, room: dict
    ):
        with pytest.raises(ContentProtectionError):
            make_policy(book, link, room, enable_agreement=True, agreement_id="missing")

    def test_an_update_moves_one_switch_and_leaves_the_rest(
        self, book: ProtectionBook, link: dict, room: dict
    ):
        created = make_policy(book, link, room, allow_download=False)
        updated = book.update_policy(
            created["id"], {"allow_download": True}, source="PATCH /api/wf-131/policies/{policy_id}"
        )
        assert updated["policy"]["allow_download"] is True
        assert updated["policy"]["email_protected"] is False

    def test_a_revoke_sets_the_value_and_keeps_the_row(
        self, store: RecordStore, book: ProtectionBook, link: dict, room: dict
    ):
        created = make_policy(book, link, room)
        revoked = book.revoke_policy(created["id"], actor="dana", source="POST revoke")
        assert revoked["revoked"] is True
        assert revoked["revoked_at"]
        assert store.find(POLICY_COLLECTION, {"link_id": link["id"]})  # the row survives

    def test_a_revoke_logs_a_revoke_action(
        self, store: RecordStore, book: ProtectionBook, link: dict, room: dict
    ):
        created = make_policy(book, link, room)
        book.revoke_policy(created["id"], actor="dana", source="POST revoke")
        actions = store.find(ACTION_COLLECTION, {"link_id": link["id"]})
        assert any(row["data"]["action"] == "revoke" for row in actions)

    def test_a_revoked_policy_refuses_admission(self, book: ProtectionBook, link: dict, room: dict):
        created = make_policy(book, link, room)
        book.revoke_policy(created["id"], source="POST revoke")
        with pytest.raises(ProtectionDenied) as refused:
            book.admit(created["id"], context(), source="POST admit")
        assert refused.value.reason == ProtectionDenied.REASON_REVOKED
        # The wording is the closed-link wording: a buyer holding the link must not
        # be able to read "you were cut off" off the message.
        assert str(refused.value) == ProtectionDenied.MESSAGES[ProtectionDenied.REASON_CLOSED]

    def test_a_restore_reopens_the_policy(self, book: ProtectionBook, link: dict, room: dict):
        created = make_policy(book, link, room)
        book.revoke_policy(created["id"], source="POST revoke")
        restored = book.restore_policy(created["id"], source="POST restore")
        assert restored["revoked"] is False

    def test_an_update_cannot_clear_a_revocation(
        self, book: ProtectionBook, link: dict, room: dict
    ):
        created = make_policy(book, link, room)
        book.revoke_policy(created["id"], source="POST revoke")
        updated = book.update_policy(created["id"], {"allow_download": True}, source="PATCH policy")
        assert updated["revoked"] is True

    def test_admit_logs_the_attempt_and_returns_the_decision(
        self, store: RecordStore, book: ProtectionBook, link: dict, room: dict
    ):
        created = make_policy(book, link, room)
        decision = book.admit(
            created["id"], context(), source="POST /api/wf-131/policies/{policy_id}/admit"
        )
        assert decision["allowed"] is True
        rows = store.find(ACTION_COLLECTION, {"link_id": link["id"], "action": "view"})
        assert len(rows) == 1
        assert rows[0]["data"]["allowed"] is True

    def test_a_refused_attempt_is_still_logged(
        self, store: RecordStore, book: ProtectionBook, link: dict, room: dict
    ):
        """WF-079: a rejected attempt is as visible as a successful one."""
        created = make_policy(book, link, room, ip_allowlist=["10.0.0.0/8"])
        with pytest.raises(ProtectionDenied):
            book.admit(created["id"], context(ip="198.51.100.7"), source="POST admit")
        rows = store.find(ACTION_COLLECTION, {"link_id": link["id"], "action": "view"})
        assert rows and rows[0]["data"]["allowed"] is False
        assert rows[0]["data"]["denied_by"] == ["ip"]

    def test_an_empty_policy_set_still_reports_compose_honesty(
        self, book: ProtectionBook, link: dict
    ):
        composed = book.compose_link(link["id"])
        assert composed["recipients"] == 0
        assert composed["limitation"] and composed["scope_owner"] and composed["read_source"]

    def test_the_compose_joins_the_bundle_onto_one_link(
        self, book: ProtectionBook, link: dict, room: dict, agreement: dict
    ):
        make_policy(book, link, room, enable_watermark=True)
        composed = book.compose_link(link["id"])
        assert composed["link"]["id"] == link["id"]
        assert composed["recipients"] == 1
        assert composed["scope_owner"]
        assert composed["limitation"]


class TestThePlanGate:
    def test_the_flip_gate_delegates_to_wf071(self):
        """The plan gate is WF-071's rule, so its error type is WF-071's own."""
        from dsr.features.wf131_content_protection import _require_plan_on_flip
        from dsr.viewer_watermark import WatermarkPlanError

        _require_plan_on_flip(None, was=False, now=True)  # no plan named: not refused
        _require_plan_on_flip("starter", was=True, now=True)  # already on: not re-checked
        with pytest.raises(WatermarkPlanError):
            _require_plan_on_flip("starter", was=False, now=True)


# --------------------------------------------------------------------------- #
# The action log and the audited store
# --------------------------------------------------------------------------- #


class TestTheActionLog:
    def test_an_action_carries_the_wf079_code_as_event_code(
        self, store: RecordStore, book: ProtectionBook, link: dict, room: dict
    ):
        book.record_action(link["id"], "a@b.example", action="download", source="POST action")
        row = store.find(ACTION_COLLECTION, {"link_id": link["id"]})[0]["data"]
        assert row["event_code"] == ACTION_CODES["download"]
        assert row["action"] == "download"

    def test_an_unknown_action_is_refused(self, book: ProtectionBook, link: dict):
        with pytest.raises(ContentProtectionError):
            book.record_action(link["id"], "a@b.example", action="delete", source="POST action")

    def test_a_recipient_is_normalised_like_every_other_workflow(
        self, store: RecordStore, book: ProtectionBook, link: dict
    ):
        book.record_action(
            link["id"], "Buyer@Northwind.example", action="view", source="POST action"
        )
        row = store.find(ACTION_COLLECTION, {"link_id": link["id"]})[0]["data"]
        assert row["recipient"] == "buyer@northwind.example"

    def test_actions_are_filterable_by_recipient(self, book: ProtectionBook, link: dict):
        book.record_action(link["id"], "a@b.example", action="view", source="POST action")
        book.record_action(link["id"], "b@b.example", action="view", source="POST action")
        assert len(book.list_actions(link["id"], "a@b.example")) == 1

    def test_the_book_bulk_alert_reads_the_log(self, book: ProtectionBook, link: dict, room: dict):
        for _ in range(5):
            book.record_action(
                link["id"],
                "a@b.example",
                action="download",
                room_id=room["id"],
                source="POST action",
            )
        alert = book.bulk_alert(link["id"], threshold=5)
        assert alert["alert"] is True

    def test_every_write_lands_an_audit_row_naming_its_route(
        self, store: RecordStore, book: ProtectionBook, link: dict, room: dict
    ):
        source = "POST /api/wf-131/links/{link_id}/policies"
        make_policy(book, link, room)
        rows = store.audit(limit=50)
        assert any(row.get("source") == source for row in rows)


# --------------------------------------------------------------------------- #
# Architecture
# --------------------------------------------------------------------------- #


def _domain_modules() -> list[Path]:
    return sorted(DOMAIN_DIR.glob("*.py"))


class TestArchitecture:
    def test_the_domain_package_never_imports_the_app_or_a_feature(self):
        for path in _domain_modules():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    assert name != "dsr.api" and not name.startswith("dsr.api."), path.name
                    assert name != "dsr.features" and not name.startswith("dsr.features."), (
                        path.name
                    )
                    assert not name.startswith("fastapi"), path.name
            text = path.read_text(encoding="utf-8")
            assert "import sqlite3" not in text, path.name
            assert "sqlite3.connect" not in text, path.name

    def test_the_domain_package_imports_only_allowed_dsr_packages(self):
        for path in _domain_modules():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if not name.startswith("dsr"):
                        continue
                    assert name.startswith(ALLOWED_DSR_PREFIXES), f"{path.name} imports {name}"

    def test_the_feature_module_takes_its_dependencies_from_deps(self):
        text = FEATURE_MODULE.read_text(encoding="utf-8")
        assert "from dsr.deps import" in text
        assert "from dsr.api" not in text and "import dsr.api" not in text
        assert "import sqlite3" not in text and "sqlite3.connect" not in text

    def test_the_feature_module_declares_its_ticket_prefix_and_handlers(self):
        module = importlib.import_module("dsr.features.wf131_content_protection")
        assert module.FEATURE["id"] == "wf-131-content-protection"
        assert module.FEATURE["ticket"] == "WF-131"
        assert module.router.prefix == "/api/wf-131"
        assert set(module.EXCEPTION_HANDLERS) == set(domain_domain_errors())

    def test_the_error_types_are_this_workflows_own(self):
        assert domain.ContentProtectionError.__module__ == "dsr.content_protection.errors"
        assert domain.ProtectionDenied.__module__ == "dsr.content_protection.errors"
        assert domain.PolicyNotFound.__module__ == "dsr.content_protection.errors"


def domain_domain_errors():
    return (domain.ContentProtectionError, domain.ProtectionDenied, domain.PolicyNotFound)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


class TestSeed:
    def test_the_demo_seeds_the_matrix_and_shows_the_states(self, db):
        scoped = RecordStore(db)
        room = scoped.create("room", {"name": "Northwind"}, actor="dana", source="test")
        summary = feature_seed(
            db, {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": None}
        )
        assert isinstance(summary, str) and summary
        # A Windows console prints this string; a non-cp1252 character once broke
        # the whole seeder, so the encoding is asserted rather than assumed.
        summary.encode("cp1252")
        assert "refused" in summary
        assert "revoked" in summary
        assert len(scoped.list(POLICY_COLLECTION, limit=10)) == 2
        assert len(scoped.list(ACTION_COLLECTION, limit=50)) >= 8

    def test_the_demo_reports_nothing_when_there_is_no_room(self, db):
        summary = feature_seed(db, {"room_ids": [], "now": NOW, "rng": None})
        assert "0 recipient policies" in summary
        summary.encode("cp1252")


def feature_seed(db, context):
    module = importlib.import_module("dsr.features.wf131_content_protection")
    return module.seed(db, context)
