"""WF-085 domain rules: region, retention, the consent gate and per-subject erasure.

The rules are in ``dsr.security_governance.privacy_rules``, the vocabulary in
``residency`` and the writes in ``privacy_engine``. This file tests them without HTTP and
is organised by the claims the specification makes:

``a region change re-stamps every record the deployment holds``
    The evidence asserts the jurisdiction is fixed and is silent about records already
    written. These tests fail if the move starts leaving records behind, and the
    derivation was put to Jev as audit ``jev-20261004T182818-16136-98999``.
``a retention window can be shorter than the ceiling and never longer``
    The evidence says "up to", so the researched figures are maxima.
``the gate fails closed on every path that is not an explicit grant``
    The issue names this as the defect to avoid: "A gate that falls through to full
    tracking on an error is a defect."
``DNT is not a consent signal``
    The evidence says the vendor does not respond to it, so the gate must not read it.
``per-subject deletion is a path, not a room deletion``
    The evidence quotes the vendor's limitation and calls per-subject deletion the hard
    part.
``a blocking change is administrator-only, and the role is read not invented``
    The evidence names the check and the repository supplies the role.

Every test here passes with the file run on its own. Nothing in this module depends on a
test that ran before it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.permissions import INSTANCE_ADMIN
from dsr.security_governance import (
    privacy_inferences as inferences,
    privacy_rules as rules,
    residency as vocab,
)
from dsr.security_governance.privacy_engine import PrivacyEngine
from dsr.store import RecordStore

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
BASE_MS = int(NOW.timestamp() * 1000)
DAY_MS = 86400000
#: The repository's own administrator tier, read here rather than invented. The
#: domain rules take it as an argument because the package they live in may not
#: import dsr.permissions; see DERIVED_ADMINISTRATOR_ROLE_IS_READ_NOT_INVENTED.
ADMIN = INSTANCE_ADMIN

MODULES = ("residency.py", "privacy_rules.py", "privacy_inferences.py", "privacy_engine.py")
PACKAGE = Path(__file__).resolve().parents[1] / "dsr" / "security_governance"


@pytest.fixture
def engine(store: RecordStore) -> PrivacyEngine:
    """An engine with a clock the test moves by hand.

    The retention classes are the only rules here that depend on the current instant, and
    the boundaries worth testing are the ones a real clock cannot be steered to: a record
    exactly at its window, and one a millisecond past it.
    """

    return PrivacyEngine(store, administrator=ADMIN, now=lambda: NOW)


def make_view(store: RecordStore, room_id: str, email: str, *, days_ago: float, region=None):
    """One engagement row, as WF-075 writes it: a Unix millisecond ``viewed_at``."""

    data = {
        "link_id": "link_a",
        "dataroom_id": room_id,
        "viewer_email": email,
        "view_type": "link",
        "viewed_at": BASE_MS - int(days_ago * DAY_MS),
        "page_durations": [{"page_number": 1, "duration_seconds": 45}],
        "location": {"country": "Germany", "city": "Berlin"},
        "client": {"browser": "Edge", "os": "Windows", "device": "desktop"},
    }
    if region:
        data[vocab.RESIDENCY_FIELD] = region
    return store.create("wf075_view", data, room_id=room_id, actor="system", source="test")


def make_visitor(store: RecordStore, room_id: str, email: str, *, region=None):
    """One persistent identifiable row, as WF-075 writes it."""

    data = {
        "email": email,
        "dataroom_id": room_id,
        "invited_at": BASE_MS - 400 * DAY_MS,
        "last_viewed_at": BASE_MS - 400 * DAY_MS,
        "total_views": 2,
        "verified": False,
    }
    if region:
        data[vocab.RESIDENCY_FIELD] = region
    return store.create("wf075_visitor", data, room_id=room_id, actor="dana", source="test")


def pin(engine: PrivacyEngine, region: str = "eu-west", room_id: str = "room_a"):
    return engine.set_residency(
        region,
        transfer_mechanism=vocab.TRANSFER_SCC,
        room_id=room_id,
        actor="dana",
        role=ADMIN,
        source="POST /api/wf-085/residency",
    )


# --------------------------------------------------------------------------- #
# the dependency surface
# --------------------------------------------------------------------------- #


class TestTheDependencySurface:
    def test_no_domain_module_imports_the_app(self):
        """A domain module that reaches for the app cannot be tested on its own."""

        for name in MODULES:
            text = (PACKAGE / name).read_text(encoding="utf-8")
            assert "dsr.api" not in text, name

    def test_no_domain_module_opens_sqlite(self):
        """Every read and write goes through the audited store."""

        for name in MODULES:
            text = (PACKAGE / name).read_text(encoding="utf-8")
            assert "import sqlite3" not in text, name
            assert "sqlite3.connect" not in text, name

    def test_no_domain_module_imports_a_web_framework(self):
        """The domain is importable and testable without FastAPI in the way."""

        for name in MODULES:
            text = (PACKAGE / name).read_text(encoding="utf-8")
            assert "fastapi" not in text, name
            assert "starlette" not in text, name

    def test_the_only_product_modules_the_domain_imports_are_the_store_and_itself(self):
        """The repository's role surface is read by the feature module, not by the package.

        The domain package may depend on nothing inside ``dsr`` but the store and itself,
        and ``tests/test_wf073.py`` already enforces exactly that for this package. So the
        administrator tier is *supplied* by the feature module rather than imported here,
        and there is one import site for it in the whole workflow.
        """
        allowed = {"dsr.store", "dsr.security_governance"}
        for name in MODULES:
            for line in (PACKAGE / name).read_text(encoding="utf-8").splitlines():
                if not line.startswith(("import ", "from ")):
                    continue
                if line.startswith("from dsr"):
                    module = line.split()[1]
                    assert module in allowed, f"{name} imports {module}"

    def test_the_feature_module_is_the_one_place_the_role_surface_is_read(self):
        """One role vocabulary, one import site.

        The feature module is outside the domain package, so it is the one place allowed
        to read ``dsr.permissions``, and it hands the tier to the engine. A second reader
        anywhere else in this workflow would be a second place to change when the role
        vocabulary changes.

        Read from the tree rather than the text: three of the package's docstrings name
        ``dsr.permissions`` on purpose, to say where the tier is *not* read from.
        """

        import ast

        import dsr.features.wf085_meet_gdpr_ccpa_residency_retention_dsar as feature

        def reads_role_surface(path: Path) -> bool:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "") == "dsr.permissions":
                    return True
                if isinstance(node, ast.Import) and any(
                    alias.name == "dsr.permissions" for alias in node.names
                ):
                    return True
            return False

        readers = [name for name in MODULES if reads_role_surface(PACKAGE / name)]
        assert readers == [], f"the package must not read the role surface: {readers}"
        assert reads_role_surface(Path(feature.__file__))

    def test_an_engine_without_an_administrator_tier_is_refused(self, store: RecordStore):
        """A wiring fault is refused at construction rather than left ungated.

        Omitting the argument is a ``TypeError``, which is the strongest form of "you did
        not finish this"; passing a blank one is a ``PrivacyRefusal``, which is what a
        caller wiring a role from a settings value gets.
        """

        with pytest.raises(TypeError):
            PrivacyEngine(store)  # type: ignore[call-arg]

        with pytest.raises(rules.PrivacyRefusal):
            PrivacyEngine(store, administrator="   ")

    def test_the_retention_scope_names_collections_the_engagement_workflow_owns(self):
        """The dependency the issue names, checked in one place.

        If WF-075 ever renames a collection, this fails here rather than as an empty
        retention board nobody could explain.
        """

        from dsr.security_governance import engagement as wf075

        assert vocab.RETENTION_SCOPE[wf075.VIEW_COLLECTION]
        assert vocab.RETENTION_SCOPE[wf075.VISITOR_COLLECTION]
        assert vocab.INSTANT_FIELDS[wf075.VIEW_COLLECTION] == "viewed_at"
        assert vocab.INSTANT_FIELDS[wf075.VISITOR_COLLECTION] == "last_viewed_at"


# --------------------------------------------------------------------------- #
# a region change re-stamps every record
# --------------------------------------------------------------------------- #


class TestRegionMove:
    def test_a_move_rewrites_the_region_on_a_record_already_written(self, engine, store):
        """The derivation. One jurisdiction always holds the room's data."""

        make_view(store, "room_a", "buyer@northwind.example", days_ago=2, region="us-east")
        make_visitor(store, "room_a", "buyer@northwind.example", region="us-east")

        result = pin(engine, "eu-west")

        assert result["relocation"]["records_moved"] == 2
        assert result["residency_region"] == "eu-west"
        for record in store.list("wf075_view", room_id="room_a"):
            assert record["data"][vocab.RESIDENCY_FIELD] == "eu-west"

    def test_a_move_reports_what_it_scanned_and_what_it_moved(self, engine, store):
        """A change that wrote to every row must not report a single boolean."""

        make_view(store, "room_a", "buyer@northwind.example", days_ago=2)
        make_view(store, "room_a", "buyer@halcyon.example", days_ago=3)

        result = pin(engine, "eu-west")

        assert result["relocation"]["records_scanned"] >= 2
        assert result["relocation"]["records_moved"] >= 2
        assert result["relocation"]["by_collection"]["wf075_view"] == 2

    def test_a_second_move_to_the_same_region_writes_nothing(self, engine, store):
        """Re-stamping is idempotent, so a repeated move is not a repeated write."""

        make_view(store, "room_a", "buyer@northwind.example", days_ago=2)
        pin(engine, "eu-west")

        again = pin(engine, "eu-west")

        assert again["relocation"]["records_moved"] == 0
        assert again["relocation"]["records_scanned"] >= 1

    def test_a_move_records_where_the_deployment_was(self, engine):
        """The previous region is kept so a reader can see the move happened."""

        first = pin(engine, "eu-west")
        second = pin(engine, "us-east")

        assert first["previous_residency_region"] is None
        assert second["previous_residency_region"] == "eu-west"

    def test_a_move_is_administrator_only(self, engine):
        """Blocking changes are admin-only, and the check fails closed."""

        for role in (None, "", "room_collaborator", "content_contributor", "viewer", "nonsense"):
            with pytest.raises(rules.PrivacyAdministratorRequired):
                engine.set_residency(
                    "eu-west", room_id="room_a", actor="dana", role=role, source="test"
                )

    def test_the_refusal_names_the_role_that_would_have_worked(self, engine):
        with pytest.raises(rules.PrivacyAdministratorRequired) as caught:
            engine.set_residency(
                "eu-west", room_id="room_a", actor="dana", role="viewer", source="test"
            )

        body = caught.value.to_dict()
        assert body["presented_role"] == "viewer"
        assert body["required_role"] == ADMIN
        assert body["remediation"] == f"Pass role={ADMIN!r}."
        assert body["presented_role"] == "viewer"

    def test_the_tier_is_compared_for_spelling_but_still_fails_closed(self):
        """A caller who writes the role with a space or a hyphen is not refused for it."""

        assert rules.require_administrator("Instance Admin", "test", "instance_admin")
        assert rules.require_administrator("INSTANCE-ADMIN", "test", "instance_admin")

        for presented in ("admin", "administrator", "instance admin is not a role", None, "", 1):
            with pytest.raises(rules.PrivacyAdministratorRequired):
                rules.require_administrator(presented, "test", "instance_admin")

    def test_a_blank_tier_is_a_wiring_refusal_rather_than_an_ungated_change(self):
        """An empty tier would make an empty role the administrator."""

        with pytest.raises(rules.PrivacyRefusal) as caught:
            rules.require_administrator("anything", "test", "   ")

        assert "administrator" in caught.value.errors["role"]

    def test_an_unknown_region_is_refused_and_the_known_set_is_named(self):
        with pytest.raises(rules.PrivacyRefusal) as caught:
            rules.normalise_region("mars-central")

        assert "eu-west" in caught.value.errors["region"]

    def test_a_region_is_normalised_before_it_is_compared(self):
        assert rules.normalise_region(" EU_West ") == "eu-west"
        assert rules.jurisdiction_of("eu-west") == vocab.JURISDICTION_EEA
        assert rules.jurisdiction_of("us-east") == vocab.JURISDICTION_US

    def test_an_unknown_transfer_mechanism_is_refused(self):
        with pytest.raises(rules.PrivacyRefusal):
            rules.normalise_transfer_mechanism("teleportation")

    def test_no_transfer_mechanism_means_none_declared_rather_than_a_claim(self):
        """The honest default is "none declared", not "no transfer occurs"."""

        assert rules.normalise_transfer_mechanism(None) == vocab.TRANSFER_NONE
        assert rules.normalise_transfer_mechanism("") == vocab.TRANSFER_NONE


# --------------------------------------------------------------------------- #
# retention
# --------------------------------------------------------------------------- #


class TestRetentionWindows:
    def test_the_three_researched_windows_are_the_ceilings(self):
        """The evidence gives three classes and three windows in one sentence."""

        assert vocab.MAX_RETENTION_DAYS[vocab.CLASS_SESSION_RECORDING] == 30
        assert (
            vocab.MAX_RETENTION_DAYS[vocab.CLASS_FAVOURITE_OR_SAMPLE] == vocab.DAYS_PER_NINE_MONTHS
        )
        assert vocab.MAX_RETENTION_DAYS[vocab.CLASS_HEATMAP] == vocab.DAYS_PER_NINE_MONTHS

    def test_a_configured_window_may_be_shorter(self):
        assert (
            rules.retention_window(
                vocab.CLASS_SESSION_RECORDING, {vocab.CLASS_SESSION_RECORDING: 7}
            )
            == 7
        )

    def test_a_configured_window_longer_than_the_ceiling_is_refused_not_clamped(self):
        """Clamping would leave an operator believing a longer window was agreed."""

        with pytest.raises(rules.PrivacyRefusal) as caught:
            rules.retention_window(
                vocab.CLASS_SESSION_RECORDING, {vocab.CLASS_SESSION_RECORDING: 90}
            )

        assert "30" in caught.value.errors[vocab.CLASS_SESSION_RECORDING]

    def test_a_window_of_zero_days_is_refused_with_the_floor_named(self):
        with pytest.raises(rules.PrivacyRefusal) as caught:
            rules.retention_window(
                vocab.CLASS_SESSION_RECORDING, {vocab.CLASS_SESSION_RECORDING: 0}
            )

        assert "at least 1" in caught.value.errors[vocab.CLASS_SESSION_RECORDING]

    def test_an_unknown_class_is_refused_and_the_three_are_named(self):
        with pytest.raises(rules.PrivacyRefusal) as caught:
            rules.normalise_class("session_recording_v2")

        assert vocab.CLASS_SESSION_RECORDING in caught.value.errors["class"]

    def test_the_policy_reports_the_configured_window_and_the_ceiling_apart(self, engine, store):
        """A board must never show one number as if it were the other."""

        make_view(store, "room_a", "buyer@northwind.example", days_ago=2)
        pin(engine, "eu-west")
        engine.set_retention_policy(
            {vocab.CLASS_SESSION_RECORDING: 14},
            room_id="room_a",
            actor="dana",
            role=ADMIN,
            source="POST /api/wf-085/retention/policy",
        )

        rows = {row["class"]: row for row in engine.retention_report("room_a")["policy"]}
        strict = rows[vocab.CLASS_SESSION_RECORDING]

        assert strict["configured_days"] == 14
        assert strict["ceiling_days"] == 30
        assert strict["collections"] == ["wf075_view"]

    def test_every_class_carries_the_sentence_it_came_from(self, engine):
        for row in engine.retention_report("room_a")["policy"]:
            assert row["evidence"], row["class"]
            assert "up to" in row["evidence"], row["class"]

    def test_a_collection_nothing_ages_is_named_rather_than_assumed_covered(self, engine, store):
        """The "no longer-lived side channels" check, made visible."""

        store.create(
            "wf085_side_channel",
            {"subject": "buyer@northwind.example"},
            room_id="room_a",
            actor="dana",
            source="test",
        )

        report = engine.retention_report("room_a")

        assert "wf085_side_channel" in report["unmapped_collections"]
        assert "wf075_view" not in report["unmapped_collections"]


class TestRetentionAgeing:
    def test_a_record_past_its_window_is_due(self, engine, store):
        make_view(store, "room_a", "buyer@northwind.example", days_ago=31)
        pin(engine, "eu-west")

        rows = {row["class"]: row for row in engine.retention_schedule("room_a")["classes"]}

        assert rows[vocab.CLASS_SESSION_RECORDING]["due"] == 1

    def test_a_record_inside_its_window_is_retained(self, engine, store):
        make_view(store, "room_a", "buyer@northwind.example", days_ago=29)
        pin(engine, "eu-west")

        rows = {row["class"]: row for row in engine.retention_schedule("room_a")["classes"]}

        assert rows[vocab.CLASS_SESSION_RECORDING]["retained"] == 1
        assert rows[vocab.CLASS_SESSION_RECORDING]["due"] == 0

    def test_the_boundary_is_the_window_and_not_the_day_before_or_after(self):
        """A rule whose boundary cannot be tested is a rule nobody has tested."""

        recorded = NOW - timedelta(days=30)

        assert rules.is_due(recorded, 30, NOW) is True
        assert rules.is_due(recorded + timedelta(milliseconds=1), 30, NOW) is False

    def test_a_record_with_no_readable_instant_is_reported_rather_than_aged(self, engine, store):
        """Not fresh and not expired. Guessing a date would either purge early or keep
        a record past its window forever, which is the side channel the classes exist to
        prevent."""

        store.create(
            "wf075_view",
            {"link_id": "link_a", "dataroom_id": "room_a", "viewer_email": "x@y.example"},
            room_id="room_a",
            actor="system",
            source="test",
        )
        pin(engine, "eu-west")

        schedule = engine.retention_schedule("room_a")

        assert schedule["undated"] == 1
        assert schedule["undated_policy"] == vocab.UNDATED_POLICY
        assert sum(row["due"] for row in schedule["classes"]) == 0

    def test_two_instant_encodings_are_both_read(self):
        """The engagement rows store milliseconds and this workflow stores ISO 8601."""

        from_ms = rules.coerce_instant(BASE_MS, "viewed_at")
        from_iso = rules.coerce_instant(rules.stamp(NOW), "recorded_at")

        assert from_ms == NOW
        assert from_iso == NOW

    def test_an_unreadable_instant_is_a_refusal_and_not_the_epoch(self):
        """Treating it as the epoch would purge the record the moment it was written."""

        with pytest.raises(rules.PrivacyRefusal):
            rules.coerce_instant("last Tuesday", "viewed_at")

    def test_a_missing_instant_is_none_rather_than_a_refusal(self):
        assert rules.coerce_instant(None, "viewed_at") is None
        assert rules.coerce_instant("", "viewed_at") is None

    def test_a_run_erases_what_is_due_and_keeps_what_is_not(self, engine, store):
        """A hard delete, because a soft delete leaves a longer-lived copy behind."""

        make_view(store, "room_a", "buyer@northwind.example", days_ago=400)
        make_view(store, "room_a", "buyer@halcyon.example", days_ago=2)
        pin(engine, "eu-west")

        result = engine.run_retention(
            room_id="room_a", actor="dana", role=ADMIN, source="POST /api/wf-085/retention/run"
        )

        assert result["erased"] == 1
        assert result["erased_by_collection"] == {"wf075_view": 1}
        remaining = [
            row["data"]["viewer_email"] for row in store.list("wf075_view", room_id="room_a")
        ]
        assert remaining == ["buyer@halcyon.example"]

    def test_a_removed_record_is_gone_from_the_dynamic_index_too(self, engine, store):
        """A soft delete leaves the index entry behind, which is a side channel."""

        make_view(store, "room_a", "buyer@northwind.example", days_ago=400)
        pin(engine, "eu-west")
        engine.run_retention(
            room_id="room_a", actor="dana", role=ADMIN, source="POST /api/wf-085/retention/run"
        )

        assert store.find("wf075_view", {"viewer_email": "buyer@northwind.example"}) == []

    def test_a_run_is_administrator_only(self, engine):
        with pytest.raises(rules.PrivacyAdministratorRequired):
            engine.run_retention(room_id="room_a", actor="dana", role="viewer", source="test")

    def test_a_policy_cannot_be_set_before_a_region_is_recorded(self, engine):
        """One place a deployment's privacy configuration lives."""

        with pytest.raises(rules.PrivacyNotFound):
            engine.set_retention_policy(
                {vocab.CLASS_SESSION_RECORDING: 14},
                room_id="room_a",
                actor="dana",
                role=ADMIN,
                source="test",
            )

    def test_a_refused_window_leaves_no_partial_policy_behind(self, engine):
        """Validate everything first, so a refusal does not half-apply."""

        pin(engine, "eu-west")
        with pytest.raises(rules.PrivacyRefusal):
            engine.set_retention_policy(
                {vocab.CLASS_SESSION_RECORDING: 14, vocab.CLASS_HEATMAP: 9999},
                room_id="room_a",
                actor="dana",
                role=ADMIN,
                source="test",
            )

        report = engine.retention_report("room_a")
        assert all(row["configured_days"] == row["ceiling_days"] for row in report["policy"])


# --------------------------------------------------------------------------- #
# the gate fails closed
# --------------------------------------------------------------------------- #


class TestTheConsentGate:
    def test_only_the_exact_grant_word_allows_tracking(self):
        decision = rules.gate("eu-west", signal=vocab.CONSENT_GRANTED)

        assert decision["outcome"] == vocab.GATE_TRACK
        assert decision["effect"]["cookies"] is True

    def test_a_grant_word_with_different_casing_still_grants(self):
        """The word is compared case-insensitively because a CMP sends text."""

        assert rules.gate("eu-west", signal="GRANTED")["outcome"] == vocab.GATE_TRACK

    def test_a_missing_signal_denies(self):
        """The defect the issue names: a gate that falls through to full tracking."""

        decision = rules.gate("eu-west")

        assert decision["outcome"] == vocab.GATE_DENY
        assert "No consent signal" in decision["reason"]

    def test_an_empty_signal_denies(self):
        assert rules.gate("eu-west", signal="   ")["outcome"] == vocab.GATE_DENY

    def test_an_unknown_signal_denies_rather_than_defaulting_to_grant(self):
        decision = rules.gate("eu-west", signal="maybe")

        assert decision["outcome"] == vocab.GATE_DENY
        assert "not the explicit grant" in decision["reason"]

    def test_a_boolean_true_is_not_the_grant_word(self):
        """A JSON round trip that lost the string is a deny, not a grant."""

        assert rules.gate("eu-west", signal=True)["outcome"] == vocab.GATE_DENY
        assert rules.gate("eu-west", signal=1)["outcome"] == vocab.GATE_DENY

    def test_a_deny_assigns_a_unique_id_per_page_view_and_no_cookies(self):
        """The evidence's own sentence for the deny branch."""

        effect = rules.gate("eu-west")["effect"]

        assert effect["identifier"] == "unique_per_page_view"
        assert effect["cookies"] is False
        assert effect["persistent_identifier"] is False
        assert effect["tracking"] is False

    def test_a_global_privacy_control_opt_out_denies_whatever_else_was_sent(self):
        """ "Opt-out honoured automatically", and an opt-out outranks a grant."""

        decision = rules.gate("eu-west", signal=vocab.CONSENT_GRANTED, gpc="1")

        assert decision["outcome"] == vocab.GATE_DENY
        assert decision["opted_out"] == ["gpc"]

    def test_the_advertising_opt_out_list_denies_as_well(self):
        assert rules.gate("eu-west", daa=True)["outcome"] == vocab.GATE_DENY

    def test_an_opt_out_that_is_clear_does_not_grant(self):
        """ "gpc is false" means nobody opted out, which is not consent."""

        decision = rules.gate("eu-west", gpc="0")

        assert decision["outcome"] == vocab.GATE_DENY
        assert decision["granted"] is False

    def test_a_non_flag_opt_out_value_is_reported_rather_than_guessed(self):
        decision = rules.gate("eu-west", gpc="perhaps")

        assert decision["outcome"] == vocab.GATE_DENY
        assert any(row["signal"] == "gpc" for row in decision["signals_ignored"])

    def test_dnt_is_not_a_consent_signal(self):
        """The evidence records DNT as unsupported, so it must be ignored."""

        with_dnt = rules.gate("eu-west", signal="dnt")
        without = rules.gate("eu-west")

        assert with_dnt["outcome"] == vocab.GATE_DENY
        assert without["outcome"] == with_dnt["outcome"]

    def test_a_discarded_dnt_signal_is_reported_with_its_reason(self):
        """So a client can see the signal had no effect rather than inferring failure."""

        decision = rules.gate("eu-west", signal="dnt")

        assert [row["signal"] for row in decision["signals_ignored"]] == ["dnt"]
        assert "does not respond" in decision["signals_ignored"][0]["reason"]

    def test_an_absent_opt_out_is_not_reported_as_an_unreadable_one(self):
        """A client must not be told it sent something it did not send."""

        assert rules.gate("eu-west")["signals_ignored"] == []
        assert rules.gate("eu-west", gpc=None)["signals_ignored"] == []

    def test_an_unlisted_jurisdiction_reports_not_required_rather_than_tracking_silently(self):
        """Falling through to tracking is exactly "merely degrading"."""

        decision = rules.gate("us-east")

        assert decision["outcome"] == vocab.CONSENT_NOT_REQUIRED
        assert decision["consent_required"] is False
        assert decision["effect"]["cookies"] is True

    def test_the_three_jurisdictions_the_evidence_names_are_the_listed_ones(self):
        for region in ("eu-west", "uk-south", "ch-north"):
            assert rules.consent_required(region) is True
        assert rules.consent_required("us-east") is False
        assert rules.consent_required("ca-central") is False
        assert rules.consent_required("ap-southeast") is False

    def test_the_list_is_configurable_rather_than_hard_coded(self):
        assert rules.consent_required("us-east", consent_jurisdictions=["us"]) is True

    def test_every_answer_names_the_jurisdictions_it_consulted(self):
        """A misconfigured list must be visible on the page, not discovered in an audit."""

        decision = rules.gate("eu-west")

        assert decision["consent_jurisdictions"] == list(vocab.CONSENT_JURISDICTIONS)
        assert decision["jurisdiction"] == vocab.JURISDICTION_EEA


class TestConsentRecording:
    def test_a_grant_is_recorded_as_active(self, engine):
        row = engine.record_consent(
            "eu-west",
            "buyer@northwind.example",
            signal=vocab.CONSENT_GRANTED,
            room_id="room_a",
            source="test",
        )

        assert row["state"] == vocab.ACTIVE
        assert row["granted"] is True
        assert row["identifier"] == "persistent"

    def test_a_deny_after_a_grant_is_a_revocation_and_clears_the_cookies(self, engine):
        """ "Revocation is immediate", and the row has to say so."""

        engine.record_consent(
            "eu-west",
            "buyer@northwind.example",
            signal=vocab.CONSENT_GRANTED,
            room_id="room_a",
            source="test",
        )
        row = engine.record_consent(
            "eu-west", "buyer@northwind.example", room_id="room_a", source="test"
        )

        assert row["state"] == vocab.REVOKED
        assert row["revoked"] is True
        assert row["cookies_cleared"] is True
        assert row["tracking_blocked"] is True
        assert row["previous_state"] == vocab.ACTIVE

    def test_a_deny_without_a_grant_is_just_a_denial(self, engine):
        row = engine.record_consent(
            "eu-west", "buyer@vantage.example", room_id="room_a", source="test"
        )

        assert row["state"] == vocab.CONSENT_DENIED
        assert row["revoked"] is False

    def test_the_newest_signal_is_the_one_that_governs(self, engine):
        engine.record_consent(
            "eu-west",
            "buyer@northwind.example",
            signal=vocab.CONSENT_GRANTED,
            room_id="room_a",
            source="test",
        )
        engine.record_consent("eu-west", "buyer@northwind.example", room_id="room_a", source="test")

        current = engine.consent_record("buyer@northwind.example", "room_a")

        assert current["state"] == vocab.REVOKED

    def test_recording_a_signal_is_not_administrator_gated(self, engine):
        """The specification says consent is captured "at the page", not by an operator."""

        row = engine.record_consent(
            "eu-west",
            "buyer@northwind.example",
            signal=vocab.CONSENT_GRANTED,
            room_id="room_a",
            source="test",
        )

        assert row["id"]

    def test_a_subject_is_normalised_so_one_address_is_one_subject(self, engine):
        engine.record_consent(
            "eu-west",
            "Buyer@Northwind.Example",
            signal=vocab.CONSENT_GRANTED,
            room_id="room_a",
            source="test",
        )

        current = engine.consent_record("buyer@northwind.example", "room_a")

        assert current["subject"] == "buyer@northwind.example"

    def test_a_malformed_subject_is_refused(self):
        with pytest.raises(rules.PrivacyRefusal) as caught:
            rules.normalise_subject("not-an-address")

        assert "subject" in caught.value.errors


# --------------------------------------------------------------------------- #
# per-subject deletion, not per-room deletion
# --------------------------------------------------------------------------- #


class TestTheDataSubjectPath:
    def test_discovery_finds_the_records_the_evidence_names(self, engine, store):
        make_view(store, "room_a", "buyer@northwind.example", days_ago=2)
        make_visitor(store, "room_a", "buyer@northwind.example")
        make_view(store, "room_a", "buyer@halcyon.example", days_ago=3)
        pin(engine, "eu-west")

        found = engine.discover("buyer@northwind.example", room_id="room_a")

        assert found["records"] == 2
        assert found["by_collection"] == {"wf075_view": 1, "wf075_visitor": 1}

    def test_the_personal_fields_are_reported_as_found_and_as_missing(self, engine, store):
        """A plan that listed a field no record holds could not be fulfilled."""

        make_view(store, "room_a", "buyer@northwind.example", days_ago=2)
        pin(engine, "eu-west")

        found = engine.discover("buyer@northwind.example", room_id="room_a")
        fields = found["personal_data"][0]["personal_data"]

        assert "viewer_email" in fields["fields"]
        assert "location.city" in fields["fields"]
        assert "page_durations" in fields["fields"]

    def test_erasure_removes_the_subjects_records_and_only_theirs(self, engine, store):
        """The vendor's limitation was whole-project deletion. This is not that."""

        make_view(store, "room_a", "buyer@northwind.example", days_ago=2)
        make_visitor(store, "room_a", "buyer@northwind.example")
        other = make_view(store, "room_a", "buyer@halcyon.example", days_ago=3)
        pin(engine, "eu-west")

        request = engine.open_dsar(
            "buyer@northwind.example", room_id="room_a", actor="dana", role=ADMIN, source="test"
        )
        result = engine.fulfil_dsar(
            request["id"],
            actor="dana",
            role=ADMIN,
            source="POST /api/wf-085/dsar/requests/x/fulfil",
        )

        assert result["erased"] == 2
        assert store.get(other["id"]) is not None
        assert store.find("wf075_view", {"viewer_email": "buyer@northwind.example"}) == []
        assert store.find("wf075_visitor", {"email": "buyer@northwind.example"}) == []

    def test_erasure_reports_the_audit_rows_it_could_not_remove(self, engine, store):
        """The non-success state: it found the subject and left a trace it must name."""

        make_view(store, "room_a", "buyer@northwind.example", days_ago=2)
        pin(engine, "eu-west")
        request = engine.open_dsar(
            "buyer@northwind.example", room_id="room_a", actor="dana", role=ADMIN, source="test"
        )
        result = engine.fulfil_dsar(request["id"], actor="dana", role=ADMIN, source="test")

        assert result["residue"]["audit_rows"] > 0
        assert vocab.RESIDUE_AUDIT_TRAIL in result["residue"]["reasons"]
        assert result["state"] == vocab.DSAR_PARTIAL

    def test_the_request_row_keeps_itself_and_is_reported_as_residue(self, engine, store):
        """It names the subject and it is the evidence the erasure happened."""

        make_view(store, "room_a", "buyer@northwind.example", days_ago=2)
        pin(engine, "eu-west")
        request = engine.open_dsar(
            "buyer@northwind.example", room_id="room_a", actor="dana", role=ADMIN, source="test"
        )
        engine.fulfil_dsar(request["id"], actor="dana", role=ADMIN, source="test")

        assert store.get(request["id"]) is not None

    def test_a_request_that_finds_nothing_says_so(self, engine, store):
        pin(engine, "eu-west")

        request = engine.open_dsar(
            "buyer@nowhere.example", room_id="room_a", actor="dana", role=ADMIN, source="test"
        )

        assert request["found"] == 0
        assert request["state"] == vocab.DSAR_NOTHING_FOUND

    def test_opening_and_fulfilling_are_both_administrator_only(self, engine, store):
        make_view(store, "room_a", "buyer@northwind.example", days_ago=2)
        pin(engine, "eu-west")

        with pytest.raises(rules.PrivacyAdministratorRequired):
            engine.open_dsar(
                "buyer@northwind.example",
                room_id="room_a",
                actor="dana",
                role="viewer",
                source="test",
            )
        request = engine.open_dsar(
            "buyer@northwind.example", room_id="room_a", actor="dana", role=ADMIN, source="test"
        )
        with pytest.raises(rules.PrivacyAdministratorRequired):
            engine.fulfil_dsar(request["id"], actor="dana", role="room_collaborator", source="test")

    def test_fulfilling_a_request_that_does_not_exist_is_a_not_found(self, engine):
        with pytest.raises(rules.PrivacyNotFound):
            engine.fulfil_dsar("no-such-request", actor="dana", role=ADMIN, source="test")

    def test_a_record_in_another_workflows_collection_is_never_matched(self, engine, store):
        """Matching on anything softer would delete other buyers' rows."""

        store.create(
            "wf085_side_channel",
            {"country": "Germany"},
            room_id="room_a",
            actor="dana",
            source="test",
        )
        pin(engine, "eu-west")

        assert (
            rules.matches_subject("wf085_side_channel", {"country": "Germany"}, "a@b.example")
            is False
        )
        assert engine.discover("a@b.example", room_id="room_a")["records"] == 0


# --------------------------------------------------------------------------- #
# the recorded decisions
# --------------------------------------------------------------------------- #


class TestRecordedDecisions:
    def test_every_decision_names_an_option_and_the_one_that_was_taken(self):
        for key, decision in inferences.DECISIONS.items():
            assert decision["chosen"] in decision["options"], key
            assert len(decision["options"]) >= 2, key

    def test_every_decision_names_what_left_it_open_and_what_the_choice_cost(self):
        for key, decision in inferences.DECISIONS.items():
            assert decision["left_open_by"], key
            assert decision["rejected_because"], key
            assert decision["cost_of_the_choice"], key

    def test_the_region_move_derivation_names_the_jev_audit_that_chose_it(self):
        """A gate on a design document answers a different question from a gate on a
        derivation, so the audit id is recorded rather than implied."""

        record = inferences.describe_one("DERIVED_REGION_MOVE_RESTAMPS_EVERY_RECORD")

        assert "jev-20261004T182818-16136-98999" in record["rejected_because"]
        assert record["chosen"] == "restamp_in_place"

    def test_the_seven_questions_the_issue_leaves_open_are_all_answered(self):
        expected = {
            "DERIVED_REGION_MOVE_RESTAMPS_EVERY_RECORD",
            "DERIVED_RETENTION_CLASS_MAPPING",
            "DERIVED_RETENTION_WINDOW_IS_A_CEILING",
            "DERIVED_CONSENT_GATE_IS_A_REGION_LIST_NOT_A_DATE",
            "DERIVED_CONSENT_GATE_ENFORCES_IN_LISTED_JURISDICTIONS_ONLY",
            "DERIVED_CONSENT_GATE_FAILS_CLOSED_ON_EVERY_NON_GRANT",
            "DERIVED_DNT_IS_NOT_A_CONSENT_SIGNAL",
            "DERIVED_DSAR_RETAINS_THE_AUDIT_TRAIL_AS_RESIDUE",
            "DERIVED_ADMINISTRATOR_ROLE_IS_READ_NOT_INVENTED",
            "DERIVED_VENDOR_CERTIFICATIONS_ARE_UNVERIFIED_CLAIMS",
        }

        assert expected <= set(inferences.DECISIONS)

    def test_one_decision_can_be_described_alone_and_an_unknown_one_is_empty(self):
        assert inferences.describe_one("DERIVED_DNT_IS_NOT_A_CONSENT_SIGNAL")["id"]
        assert inferences.describe_one("NOT_A_DECISION") == {}


# --------------------------------------------------------------------------- #
# the wording this workflow is not allowed to use
# --------------------------------------------------------------------------- #


class TestWhatIsNotClaimed:
    def test_no_certification_is_claimed_for_this_deployment(self):
        """The issue forbids rendering a certification badge as if this repository held
        the certificate."""

        for claim in vocab.VENDOR_CLAIMS:
            assert claim["verified"] is False
            assert claim["subject"] != "this deployment"

        report = PrivacyEngine(
            RecordStore(AuditedDatabase(":memory:")), administrator=ADMIN
        ).residency_report()
        assert "no certification is claimed" in report["certification_note"].lower()

    def test_no_vendor_entity_is_stored_as_this_deployments_controller(self):
        """The researched entity describes a vendor's customers, not this room.

        The docstrings quote the evidence on purpose - that is provenance - so this reads
        the module's *string constants* rather than its source. A constant would be a
        claim; a docstring is a citation.
        """

        import ast

        # A docstring is a citation. A string constant in an assignment is a claim. The
        # distinction is the whole test, so it is made by reading the tree rather than
        # by searching the source for a word.
        assigned: list[str] = []
        for name in MODULES:
            tree = ast.parse((PACKAGE / name).read_text(encoding="utf-8"))
            docstrings = {
                ast.get_docstring(node, clean=False)
                for node in ast.walk(tree)
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
            }
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value not in docstrings
                ):
                    assigned.append(node.value)

        for value in assigned:
            assert "miol" not in value.lower(), value
            assert "microsoft" not in value.lower(), value
        assert assigned, "the vocabulary must contribute some string constants"

    def test_no_record_this_workflow_writes_carries_a_vendor_entity(self, engine, store):
        """The stronger claim: the erasure and the residency record hold no entity."""

        pin(engine, "eu-west")
        engine.record_consent(
            "eu-west",
            "buyer@northwind.example",
            signal=vocab.CONSENT_GRANTED,
            room_id="room_a",
            source="test",
        )
        request = engine.open_dsar(
            "buyer@northwind.example", room_id="room_a", actor="dana", role=ADMIN, source="test"
        )
        engine.fulfil_dsar(request["id"], actor="dana", role=ADMIN, source="test")

        for collection in (
            vocab.RESIDENCY_COLLECTION,
            vocab.CONSENT_COLLECTION,
            vocab.DSAR_COLLECTION,
        ):
            for record in store.list(collection, room_id="room_a", limit=50):
                text = repr(record["data"]).lower()
                assert "microsoft" not in text
                assert "miol" not in text

    def test_the_vendor_enforcement_date_is_recorded_and_not_implemented(self):
        """The date is provenance. The gate reads a list and no clock."""

        dates = [claim for claim in vocab.VENDOR_CLAIMS if "October 31, 2025" in claim["claim"]]

        assert dates, "the researched date must not be lost"
        assert dates[0]["verified"] is False

        source = (PACKAGE / "privacy_rules.py").read_text(encoding="utf-8")
        assert "October 31" not in source

    def test_screen_text_is_recorded_as_suppressed(self):
        """The evidence says it is suppressed by default, and it is why a view event here
        is not a transcript."""

        assert vocab.SCREEN_TEXT_DEFAULT == "suppressed"

    def test_the_transfer_mechanism_note_names_mechanisms_and_not_entities(self):
        assert "no contracting entity is stored" in vocab.TRANSFER_MECHANISM_NOTE


# --------------------------------------------------------------------------- #
# the seed
# --------------------------------------------------------------------------- #


class TestSeed:
    def _seed(self, db: AuditedDatabase) -> str:
        import dsr.features.wf085_meet_gdpr_ccpa_residency_retention_dsar as feature

        return feature.seed(
            db, {"room_ids": [("room_a", "Northwind"), ("room_b", "Contoso")], "now": NOW}
        )

    def test_the_seed_string_is_encodable_by_cp1252(self, db: AuditedDatabase):
        """The seeder prints it to a Windows console.

        One RIGHTWARDS ARROW in a recovered feature's return string broke the entire
        seeder, so this is asserted rather than assumed.
        """

        assert self._seed(db).encode("cp1252", errors="strict")

    def test_the_seed_creates_rows_a_reviewer_can_see(self, db: AuditedDatabase):
        store = RecordStore(db)
        self._seed(db)

        assert store.list(vocab.RESIDENCY_COLLECTION, limit=20)
        assert store.list(vocab.CONSENT_COLLECTION, limit=20)
        assert store.list(vocab.DSAR_COLLECTION, limit=20)

    def test_the_seed_names_the_ceiling_the_window_is_configured_below(self, db: AuditedDatabase):
        returned = self._seed(db)

        assert "14 days against a 30-day ceiling" in returned

    def test_the_seed_says_a_state_that_is_not_a_success(self, db: AuditedDatabase):
        """The brief asks for it: a request that found a subject and could not remove
        every trace of them."""

        returned = self._seed(db)

        assert "audit row(s) that still name the buyer" in returned
        assert "partial" in returned
        assert "not fulfilled" in returned

    def test_the_seed_names_the_revocation_and_the_opt_out(self, db: AuditedDatabase):
        returned = self._seed(db)

        assert "revoked" in returned
        assert "global privacy control opt-out" in returned

    def test_the_seed_leaves_a_due_record_and_a_retained_one(self, db: AuditedDatabase):
        """A board with only one of the two cannot show the strict class working."""

        returned = self._seed(db)

        assert "past their window" in returned
        assert "still inside it" in returned

    def test_the_seed_with_no_room_returns_nothing(self, db: AuditedDatabase):
        import dsr.features.wf085_meet_gdpr_ccpa_residency_retention_dsar as feature

        assert feature.seed(db, {"room_ids": [], "now": NOW}) == ""

    def test_the_seed_writes_every_write_through_the_audited_store(self, db: AuditedDatabase):
        before = db.stats()["audit_entries"]
        self._seed(db)

        assert db.stats()["audit_entries"] > before
        assert db.stats()["records"] > 0


# --------------------------------------------------------------------------- #
# the board
# --------------------------------------------------------------------------- #


class TestTheBoard:
    def test_an_empty_store_answers_with_empty_states_and_not_an_error(self, engine):
        board = engine.summary("room_a")

        assert board[vocab.RESIDENCY_FIELD_NAME] is None
        assert board["consent_signals"] == 0
        assert board["retention_due"] == 0
        assert board["admin_role"] == ADMIN

    def test_the_board_counts_every_consent_state(self, engine):
        engine.record_consent(
            "eu-west",
            "buyer@northwind.example",
            signal=vocab.CONSENT_GRANTED,
            room_id="room_a",
            source="test",
        )
        engine.record_consent("eu-west", "buyer@northwind.example", room_id="room_a", source="test")
        engine.record_consent("eu-west", "buyer@vantage.example", room_id="room_a", source="test")

        board = engine.summary("room_a")

        assert board["consent_states"][vocab.REVOKED] == 1
        assert board["consent_states"][vocab.CONSENT_DENIED] == 1
        assert board["consent_states"][vocab.ACTIVE] == 1

    def test_the_board_reports_whether_this_room_needs_consent(self, engine):
        engine.set_residency("eu-west", room_id="room_a", actor="dana", role=ADMIN, source="test")
        engine.set_residency("us-east", room_id="room_b", actor="dana", role=ADMIN, source="test")

        assert engine.summary("room_a")["consent_required"] is True
        assert engine.summary("room_b")["consent_required"] is False

    def test_the_board_names_the_instant_formats_it_accepts(self, engine):
        assert engine.retention_report("room_a")["instant_formats"] == list(vocab.INSTANT_FORMATS)
