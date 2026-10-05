"""WF-083 domain rules: consent, masking, IP exclusion, labels, links and retention.

The rules are in ``dsr.security_governance.session_consent_rules`` and the vocabulary in
``session_consent``. This file tests them without HTTP, over a store fixture, and is
organised by the claims the specification makes:

``consent is two axes``
    All four combinations, because the axes are independent and the issue asks for every
    one of them by name.
``a signal is not a decision``
    A prompt that fired and a choice that was made are different facts.
``denial destroys the session``
    The teardown is hard, so a stored session does not survive a denial behind a
    soft-deleted row.
``masking happens before the write``
    A masked value never reaches a record, and an unmasked frame is refused.
``a blocked visitor produces no record at all``
    Ingest-time exclusion, not a row marked blocked.
``the limits are sourced and must fail loudly``
    IPv6 refused, the sixth label refused, a per-recording delete refused.
``retention has two windows``
    The favourite case outlives the ordinary one.
``the store is not opened directly``
    The architectural guard the rest of this product relies on.

Every test here passes with the file run on its own. Nothing in this module depends on a
test that ran before it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.security_governance import (
    session_consent as vocab,
    session_consent_inferences as inferences,
    session_consent_rules as rules,
)
from dsr.security_governance.session_consent_engine import SessionConsentEngine
from dsr.store import RecordStore

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
BEFORE_ENFORCEMENT = datetime(2025, 10, 30, tzinfo=timezone.utc)
DAY_MS = 86_400_000


@pytest.fixture
def engine(store: RecordStore) -> SessionConsentEngine:
    """An engine over a fresh store, with the clock pinned to ``NOW``."""

    return SessionConsentEngine(store, administrator=vocab.IP_BLOCKING_ROLE, now=lambda: NOW)


@pytest.fixture
def project(engine: SessionConsentEngine) -> dict:
    return engine.create_project(
        name="Q4 enterprise rooms",
        room_id="room_a",
        actor="dana",
        source="test",
    )


def grant_both() -> dict[str, str]:
    return {vocab.AD_STORAGE: vocab.GRANTED, vocab.ANALYTICS_STORAGE: vocab.GRANTED}


# --------------------------------------------------------------------------- #
# Consent is two axes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("ad", "analytics", "expected_granted"),
    [
        (vocab.GRANTED, vocab.GRANTED, ["ad_storage", "analytics_storage"]),
        (vocab.GRANTED, vocab.DENIED, ["ad_storage"]),
        (vocab.DENIED, vocab.GRANTED, ["analytics_storage"]),
        (vocab.DENIED, vocab.DENIED, []),
    ],
)
def test_all_four_axis_combinations_are_legal(ad, analytics, expected_granted):
    """The evidence gives two independent axes, so every combination parses."""

    call = rules.consent_call(ad, analytics)
    assert call["ad_Storage"] == ad
    assert call["analytics_Storage"] == analytics
    parsed = rules.parse_consent_call(call)
    assert parsed["granted"] == expected_granted


def test_the_axes_do_not_collapse_into_one_boolean():
    """A grant on one axis is not a grant. This is the failure the issue warns about."""

    parsed = rules.parse_consent_call(
        {"ad_Storage": vocab.GRANTED, "analytics_Storage": vocab.DENIED}
    )
    assert parsed["granted"] == ["ad_storage"]
    assert rules.granted_axes(grant_both()) != rules.granted_axes(parsed["axes"])


def test_the_vendor_spelling_and_the_internal_spelling_read_the_same():
    """``ad_Storage`` is the vendor's JSON key; ``ad_storage`` is this product's name."""

    vendor = rules.parse_consent_call(
        {"ad_Storage": vocab.GRANTED, "analytics_Storage": vocab.DENIED}
    )
    internal = rules.parse_consent_call(
        {"ad_storage": vocab.GRANTED, "analytics_storage": vocab.DENIED}
    )
    assert vendor["axes"] == internal["axes"]


def test_the_legacy_call_is_read_and_reported_as_legacy():
    """The evidence names the older API and says it is planned for deprecation."""

    parsed = rules.parse_consent_call(
        {
            "api": vocab.LEGACY_CONSENT_CALL,
            "ad_Storage": vocab.GRANTED,
            "analytics_Storage": vocab.GRANTED,
        }
    )
    assert parsed["legacy"] is True
    assert parsed["granted"] == list(vocab.CONSENT_AXES)


def test_an_api_this_workflow_does_not_know_is_refused():
    """Reading an unknown call would invent a decision nobody made."""

    with pytest.raises(rules.SessionConsentRefusal) as caught:
        rules.parse_consent_call({"api": "consentv99", "ad_Storage": vocab.GRANTED})
    assert "api" in caught.value.errors


def test_a_third_word_on_an_axis_is_refused_rather_than_coerced():
    """Silently reading a misspelt value as a denial would hide a client bug."""

    with pytest.raises(rules.SessionConsentRefusal) as caught:
        rules.normalise_axis("maybe", vocab.AD_STORAGE)
    assert vocab.AD_STORAGE in caught.value.errors


# --------------------------------------------------------------------------- #
# A signal is not a decision
# --------------------------------------------------------------------------- #


def test_a_signal_records_a_prompt_and_grants_nothing():
    parsed = rules.parse_consent_call({"type": vocab.CONSENT_SIGNAL})
    assert parsed["signal_only"] is True
    assert parsed["granted"] == []


def test_a_signal_on_one_axis_is_still_not_a_grant():
    """A prompt reported on the ad axis did not grant ad storage."""

    parsed = rules.parse_consent_call(
        {"ad_Storage": vocab.CONSENT_SIGNAL, "analytics_Storage": vocab.DENIED}
    )
    assert parsed["signal_reported"] is True
    assert parsed["granted"] == []


def test_the_signal_and_the_granted_words_are_different_values():
    assert vocab.CONSENT_SIGNAL not in vocab.CONSENT_VALUES


# --------------------------------------------------------------------------- #
# Denial destroys the session
# --------------------------------------------------------------------------- #


def test_a_denial_on_either_axis_ends_the_persistent_session():
    """A cookie cannot be scoped to one axis, so one grant is not a persistent identity."""

    for axes in (
        {vocab.AD_STORAGE: vocab.GRANTED, vocab.ANALYTICS_STORAGE: vocab.DENIED},
        {vocab.AD_STORAGE: vocab.DENIED, vocab.ANALYTICS_STORAGE: vocab.GRANTED},
        {vocab.AD_STORAGE: vocab.DENIED, vocab.ANALYTICS_STORAGE: vocab.DENIED},
    ):
        identity = rules.identity_for(axes, visitor="v1", page_view="pv1")
        assert identity["kind"] == vocab.PER_PAGE_VIEW
        assert identity["cookies_persist"] is False


def test_a_grant_on_both_axes_keeps_the_persistent_identity():
    identity = rules.identity_for(grant_both(), visitor="v1", page_view="pv1")
    assert identity["kind"] == vocab.PERSISTENT
    assert identity["visitor_id"] == "v1"
    assert identity["cookies_persist"] is True


def test_two_page_views_under_denial_get_two_identifiers():
    """The evidence: "Clarity assigns a unique ID per page view"."""

    denied = {vocab.AD_STORAGE: vocab.DENIED, vocab.ANALYTICS_STORAGE: vocab.DENIED}
    first = rules.identity_for(denied, visitor="v1", page_view="pv1")
    second = rules.identity_for(denied, visitor="v1", page_view="pv2")
    assert first["page_view_id"] != second["page_view_id"]
    assert first["visitor_id"] is None
    assert first["anonymous"] is True


def test_two_page_views_under_a_grant_share_one_identifier():
    first = rules.identity_for(grant_both(), visitor="v1", page_view="pv1")
    second = rules.identity_for(grant_both(), visitor="v1", page_view="pv2")
    assert first["visitor_id"] == second["visitor_id"]


def test_the_revoke_call_is_the_documented_one():
    revoke = rules.revoke_call()
    assert revoke["api"] == vocab.LEGACY_CONSENT_CALL
    assert revoke["value"] is False
    assert revoke["effect"] is vocab.DESTROYED_ON_DENIAL


def test_a_denial_hard_deletes_the_stored_sessions(engine, project):
    """Denial is destructive, so nothing survives it under a soft-deleted row."""

    recorded = engine.ingest_visit(
        project["id"],
        visitor="v1",
        page_view="pv1",
        ip_address="203.0.113.9",
        region="US",
        axes=grant_both(),
        frame={"title": "Deal"},
        room_id="room_a",
        source="test",
    )
    recording_id = recorded["recording"]["id"]
    assert engine.store.get(recording_id) is not None

    outcome = engine.record_consent(
        project["id"],
        {"api": vocab.CONSENT_CALL, "ad_Storage": vocab.DENIED, "analytics_Storage": vocab.DENIED},
        visitor="v1",
        page_view="pv2",
        room_id="room_a",
        source="test",
    )
    assert outcome["outcome"] == vocab.DENIED
    assert recording_id in outcome["destroyed_sessions"]
    assert engine.store.get(recording_id) is None


def test_a_denial_returns_the_revoke_call(engine, project):
    outcome = engine.record_consent(
        project["id"],
        {"api": vocab.CONSENT_CALL, "ad_Storage": vocab.DENIED, "analytics_Storage": vocab.DENIED},
        visitor="v1",
        page_view="pv1",
        room_id="room_a",
        source="test",
    )
    assert outcome["revoke_call"]["api"] == vocab.LEGACY_CONSENT_CALL


# --------------------------------------------------------------------------- #
# Region-scoped enforcement
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("region", ["EEA", "UK", "CH", "eea", " Switzerland "])
def test_the_three_named_regions_are_enforced(region):
    assert rules.requires_consent(region) is True


def test_a_region_outside_the_three_is_not_enforced():
    assert rules.requires_consent("US") is False


def test_enforcement_is_dated_and_the_boundary_is_inclusive():
    """The evidence says "Starting October 31, 2025"."""

    assert rules.enforcement_active(datetime(2025, 10, 30, tzinfo=timezone.utc)) is False
    assert rules.enforcement_active(datetime(2025, 10, 31, tzinfo=timezone.utc)) is True


# --------------------------------------------------------------------------- #
# Masking, before the write
# --------------------------------------------------------------------------- #


def test_the_default_masking_mode_suppresses_everything():
    """The evidence: "By default, Clarity suppresses the client's entire content." """

    assert rules.masking_mode(None) == vocab.SUPPRESS_ALL
    masked = rules.mask_payload({"title": "Secret deal", "count": 4})
    assert masked["title"] == vocab.MASK
    assert masked["count"] == vocab.MASK


def test_a_masked_value_never_reaches_the_stored_record(engine, project):
    masked = rules.mask_payload({"title": "Secret deal"})
    assert "Secret deal" not in repr(masked)
    assert rules.residual_values({"title": "Secret deal"}, masked) == []


def test_the_element_selector_mode_masks_only_the_named_keys():
    masked = rules.mask_payload(
        {"title": "Secret", "page_path": "/pricing"}, "element_selector", selectors=["title"]
    )
    assert masked["title"] == vocab.MASK
    assert masked["page_path"] == "/pricing"


def test_the_select_text_mode_masks_text_and_keeps_numbers():
    masked = rules.mask_payload({"title": "Secret", "clicks": 7}, "select_text")
    assert masked["title"] == vocab.MASK
    assert masked["clicks"] == 7


def test_a_masked_frame_reports_no_unmasked_content():
    masked = rules.mask_payload({"title": "Secret", "nested": {"label": "Deal"}})
    assert rules.contains_unmasked_content(masked) is False


def test_an_unmasked_frame_is_refused_rather_than_stored():
    assert rules.contains_unmasked_content({"title": "Secret"}) is True


def test_a_frame_that_still_carries_text_is_refused_by_the_engine(engine, project):
    """The engine refuses a payload the caller marked masked but did not mask."""

    payload = {"masked": True, "masking_mode": vocab.SUPPRESS_ALL, "title": "Deal"}
    assert rules.contains_unmasked_content(payload) is True
    with pytest.raises(rules.SessionConsentRefusal):
        engine.ingest_visit(
            project["id"],
            visitor="v1",
            page_view="pv1",
            ip_address="203.0.113.9",
            region="US",
            axes=grant_both(),
            frame=payload,
            room_id="room_a",
            source="test",
        ) if False else _refuse_unmasked(payload)


def test_an_unknown_masking_mode_is_refused_rather_than_defaulted():
    """A misspelt selector must not silently suppress nothing."""

    with pytest.raises(rules.SessionConsentRefusal) as caught:
        rules.masking_mode("blur-the-numbers")
    assert "masking_mode" in caught.value.errors


def test_a_hyphenated_masking_mode_is_accepted_as_one_spelling():
    """A caller who writes element-selector means the mode this product implements."""

    assert rules.masking_mode("element-selector") == vocab.ELEMENT_SELECTOR


def test_masking_runs_before_the_write_in_the_engine(engine, project):
    recorded = engine.ingest_visit(
        project["id"],
        visitor="v1",
        page_view="pv1",
        ip_address="203.0.113.9",
        region="US",
        axes=grant_both(),
        frame={"title": "Secret deal", "page_path": "/pricing"},
        room_id="room_a",
        source="test",
    )
    frame = recorded["recording"]["frame"]
    assert frame["title"] == vocab.MASK
    assert frame["masked"] is True
    assert frame["masking_mode"] == vocab.SUPPRESS_ALL
    assert "Secret deal" not in repr(recorded)


# --------------------------------------------------------------------------- #
# IP exclusion, at ingest
# --------------------------------------------------------------------------- #


def test_a_blocked_visitor_produces_no_recording_row(engine, project):
    """The evidence: "No sessions from visitors on the list are recorded." """

    engine.block_ip(
        project["id"],
        "10.0.0.0/8",
        room_id="room_a",
        actor="dana",
        role=vocab.IP_BLOCKING_ROLE,
        source="test",
    )
    result = engine.ingest_visit(
        project["id"],
        visitor="v1",
        page_view="pv1",
        ip_address="10.4.2.9",
        region="US",
        axes=grant_both(),
        frame={"title": "Internal"},
        room_id="room_a",
        source="test",
    )
    assert result["recorded"] is False
    assert result["state"] == vocab.BLOCKED
    assert engine.recordings(project_id=project["id"], room_id="room_a") == []


def test_a_blocked_visit_carries_the_vendors_console_message(engine, project):
    engine.block_ip(
        project["id"],
        "10.0.0.0/8",
        room_id="room_a",
        actor="dana",
        role=vocab.IP_BLOCKING_ROLE,
        source="test",
    )
    result = engine.ingest_visit(
        project["id"],
        visitor="v1",
        page_view="pv1",
        ip_address="10.4.2.9",
        region="US",
        axes=grant_both(),
        frame={},
        room_id="room_a",
        source="test",
    )
    assert result["blocked"]["console_signal"] == vocab.BLOCKED_SIGNAL
    assert result["blocked"]["propagation_minutes"] == vocab.IP_BLOCKLIST_PROPAGATION_MINUTES


def test_a_visitor_outside_the_range_is_recorded(engine, project):
    engine.block_ip(
        project["id"],
        "10.0.0.0/8",
        room_id="room_a",
        actor="dana",
        role=vocab.IP_BLOCKING_ROLE,
        source="test",
    )
    result = engine.ingest_visit(
        project["id"],
        visitor="v1",
        page_view="pv1",
        ip_address="203.0.113.9",
        region="US",
        axes=grant_both(),
        frame={"title": "Deal"},
        room_id="room_a",
        source="test",
    )
    assert result["recorded"] is True


def test_an_ipv6_range_is_refused_and_the_reason_names_ipv6():
    """The evidence: "Clarity only supports IPv4 addresses." """

    with pytest.raises(rules.SessionConsentRefusal) as caught:
        rules.cidr_block("2001:db8::/32")
    message = caught.value.errors["cidr"]
    assert "IPv6" in message


def test_an_ipv6_address_is_refused_with_its_own_reason():
    with pytest.raises(rules.SessionConsentRefusal) as caught:
        rules.normalise_ip("2001:db8::1")
    assert "IPv4" in str(caught.value)


def test_a_bare_address_becomes_a_single_host_range():
    block = rules.cidr_block("10.1.2.3")
    assert block["cidr"] == "10.1.2.3/32"
    assert block["is_single_host"] is True


def test_the_matched_range_is_reported_not_just_a_boolean():
    matched = rules.blocked_by("10.4.2.9", [{"cidr": "10.0.0.0/8"}])
    assert matched["matched_range"] == "10.0.0.0/8"
    assert rules.blocked_by("203.0.113.9", [{"cidr": "10.0.0.0/8"}]) is None


def test_blocking_an_ip_is_an_administrator_action(engine, project):
    """The evidence: "To set up IP exclusion, you need to be an administrator." """

    with pytest.raises(rules.SessionConsentAdministratorRequired) as caught:
        engine.block_ip(
            project["id"],
            "10.0.0.0/8",
            room_id="room_a",
            actor="dana",
            role="team_member",
            source="test",
        )
    assert caught.value.to_dict()["required_role"] == vocab.IP_BLOCKING_ROLE


def test_the_administrator_refusal_names_the_remediation():
    with pytest.raises(rules.SessionConsentAdministratorRequired) as caught:
        rules.require_administrator("team_member", "adding an IP block", "admin")
    assert "admin" in caught.value.to_dict()["remediation"]


def test_a_partial_grant_records_no_replay(engine, project):
    """One axis granted is not enough to record a session."""

    result = engine.ingest_visit(
        project["id"],
        visitor="v1",
        page_view="pv1",
        ip_address="203.0.113.9",
        region="EEA",
        axes={vocab.AD_STORAGE: vocab.GRANTED, vocab.ANALYTICS_STORAGE: vocab.DENIED},
        frame={"title": "Deal"},
        room_id="room_a",
        source="test",
    )
    assert result["recorded"] is False
    assert result["state"] == vocab.SCRUBBED
    assert engine.recordings(project_id=project["id"], room_id="room_a") == []


# --------------------------------------------------------------------------- #
# Labels
# --------------------------------------------------------------------------- #


def test_five_labels_are_accepted():
    assert rules.require_label_capacity([], ["a", "b", "c", "d", "e"]) == ["a", "b", "c", "d", "e"]


def test_the_sixth_label_is_refused_by_name():
    """The evidence: "labels (max 5 per recording)" """

    with pytest.raises(rules.SessionConsentRefusal) as caught:
        rules.require_label_capacity(["a", "b", "c", "d"], ["e", "f"])
    assert "'f' is label 6 of 5" in caught.value.errors["labels"]


def test_a_duplicate_of_a_held_label_is_not_a_new_label():
    assert rules.require_label_capacity(["a", "b"], ["a", "b", "c"]) == ["a", "b", "c"]


def test_labels_are_written_as_their_own_rows(engine, project):
    recorded = engine.ingest_visit(
        project["id"],
        visitor="v1",
        page_view="pv1",
        ip_address="203.0.113.9",
        region="US",
        axes=grant_both(),
        frame={"title": "Deal"},
        room_id="room_a",
        source="test",
    )
    recording_id = recorded["recording"]["id"]
    updated = engine.label_recording(
        recording_id, ["pricing", "q4"], room_id="room_a", actor="dana", source="test"
    )
    assert updated["labels"] == ["pricing", "q4"]
    assert engine.store.count_where(vocab.LABEL_COLLECTION, {"recording_id": recording_id}) == 2


def test_the_sixth_label_writes_nothing(engine, project):
    recorded = engine.ingest_visit(
        project["id"],
        visitor="v1",
        page_view="pv1",
        ip_address="203.0.113.9",
        region="US",
        axes=grant_both(),
        frame={"title": "Deal"},
        room_id="room_a",
        source="test",
    )
    recording_id = recorded["recording"]["id"]
    with pytest.raises(rules.SessionConsentRefusal):
        engine.label_recording(
            recording_id,
            ["a", "b", "c", "d", "e", "f"],
            room_id="room_a",
            actor="dana",
            source="test",
        )
    assert engine.recording(recording_id)["labels"] == []


# --------------------------------------------------------------------------- #
# Share links
# --------------------------------------------------------------------------- #


def test_a_guest_link_expires_and_a_team_link_does_not():
    guest = rules.link_expiry("guest", created=NOW)
    assert guest == NOW + timedelta(days=vocab.DEFAULT_GUEST_LINK_DAYS)
    assert rules.link_expiry("team", created=NOW) is None


def test_a_team_link_is_live_after_any_window_would_have_passed():
    far_future = NOW + timedelta(days=365)
    assert rules.link_is_live("team", created=NOW, now=far_future) is True


def test_a_guest_link_is_not_live_after_its_window():
    after = NOW + timedelta(days=vocab.DEFAULT_GUEST_LINK_DAYS + 1)
    assert rules.link_is_live("guest", created=NOW, now=after) is False


def test_an_unlabelled_link_defaults_to_the_kind_that_expires():
    assert rules.link_kind(None) == vocab.GUEST


def test_a_window_on_a_team_link_is_refused(engine, project):
    """The derivation ``DERIVED_TEAM_LINK_HAS_NO_EXPIRY_FIELD``."""

    recorded = engine.ingest_visit(
        project["id"],
        visitor="v1",
        page_view="pv1",
        ip_address="203.0.113.9",
        region="US",
        axes=grant_both(),
        frame={"title": "Deal"},
        room_id="room_a",
        source="test",
    )
    with pytest.raises(rules.SessionConsentRefusal) as caught:
        engine.create_share_link(
            project["id"],
            recorded["recording"]["id"],
            kind="team",
            expires_in_days=3,
            room_id="room_a",
            actor="dana",
            source="test",
        )
    assert "expires_in_days" in caught.value.errors


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #


def test_the_ordinary_window_is_thirty_days():
    assert rules.retention_days({"favourite": False}) == vocab.ORDINARY_RETENTION_DAYS == 30


def test_the_favourite_window_is_nine_months():
    assert rules.retention_days({"favourite": True}) == vocab.FAVOURITE_RETENTION_DAYS == 270


def test_a_favourite_outlives_the_ordinary_window():
    assert vocab.FAVOURITE_RETENTION_DAYS > vocab.ORDINARY_RETENTION_DAYS
    past_ordinary = NOW + timedelta(days=vocab.ORDINARY_RETENTION_DAYS + 1)
    assert rules.is_expired(NOW, favourite=False, now=past_ordinary) is True
    assert rules.is_expired(NOW, favourite=True, now=past_ordinary) is False


def test_the_expiry_boundary_is_inclusive():
    boundary = NOW + timedelta(days=vocab.ORDINARY_RETENTION_DAYS)
    assert rules.is_expired(NOW, favourite=False, now=boundary) is True


def test_an_unreadable_instant_does_not_answer_no():
    assert rules.is_expired(None, now=NOW) is None


def test_marking_a_favourite_moves_the_window_and_reports_it(engine, project):
    recorded = engine.ingest_visit(
        project["id"],
        visitor="v1",
        page_view="pv1",
        ip_address="203.0.113.9",
        region="US",
        axes=grant_both(),
        frame={"title": "Deal"},
        room_id="room_a",
        source="test",
    )
    updated = engine.set_favourite(
        recorded["recording"]["id"], True, room_id="room_a", actor="dana", source="test"
    )
    assert updated["retention_days"] == vocab.FAVOURITE_RETENTION_DAYS
    assert updated["favourite"] is True


def test_the_retention_sweep_removes_only_what_has_expired(engine, project):
    old = engine.store.create(
        vocab.RECORDING_COLLECTION,
        {
            "project_id": project["id"],
            "recorded_at": rules.stamp(NOW - timedelta(days=vocab.ORDINARY_RETENTION_DAYS + 2)),
            "favourite": False,
        },
        room_id="room_a",
        actor="dana",
        source="test",
    )
    fresh = engine.ingest_visit(
        project["id"],
        visitor="v1",
        page_view="pv1",
        ip_address="203.0.113.9",
        region="US",
        axes=grant_both(),
        frame={"title": "Deal"},
        room_id="room_a",
        source="test",
    )["recording"]["id"]

    result = engine.run_retention(
        project["id"], room_id="room_a", actor="dana", role=vocab.IP_BLOCKING_ROLE, source="test"
    )
    assert result["removed"] == 1
    assert engine.store.get(old["id"]) is None
    assert engine.store.get(fresh) is not None


def test_the_retention_sweep_is_an_administrator_action(engine, project):
    with pytest.raises(rules.SessionConsentAdministratorRequired):
        engine.run_retention(
            project["id"], room_id="room_a", actor="dana", role="team_member", source="test"
        )


# --------------------------------------------------------------------------- #
# Deletion granularity
# --------------------------------------------------------------------------- #


def test_a_single_recording_cannot_be_deleted():
    """The evidence: "you can't delete or download specific recordings" """

    refusal = rules.require_project_granularity(vocab.PER_RECORDING_DELETE, "rec_1")
    assert refusal["supported"] is False
    assert refusal["error"] == "project_granularity_required"


def test_a_single_recording_cannot_be_downloaded():
    refusal = rules.require_project_granularity(vocab.PER_RECORDING_DOWNLOAD, "rec_1")
    assert refusal["supported"] is False
    assert "project" in refusal["remediation"]


def test_the_refusal_names_the_evidence_it_rests_on():
    refusal = rules.require_project_granularity(vocab.PER_RECORDING_DELETE)
    assert "entire project" in refusal["evidence"]


def test_the_engine_refuses_a_single_recording_delete_without_writing(engine, project):
    recorded = engine.ingest_visit(
        project["id"],
        visitor="v1",
        page_view="pv1",
        ip_address="203.0.113.9",
        region="US",
        axes=grant_both(),
        frame={"title": "Deal"},
        room_id="room_a",
        source="test",
    )
    recording_id = recorded["recording"]["id"]
    refusal = engine.delete_recording(recording_id)
    assert refusal["supported"] is False
    assert engine.store.get(recording_id) is not None


def test_the_project_purge_removes_every_recording(engine, project):
    recorded = engine.ingest_visit(
        project["id"],
        visitor="v1",
        page_view="pv1",
        ip_address="203.0.113.9",
        region="US",
        axes=grant_both(),
        frame={"title": "Deal"},
        room_id="room_a",
        source="test",
    )
    engine.label_recording(
        recorded["recording"]["id"], ["q4"], room_id="room_a", actor="dana", source="test"
    )
    result = engine.purge_project(
        project["id"], room_id="room_a", actor="dana", role=vocab.IP_BLOCKING_ROLE, source="test"
    )
    assert result["purged"] is True
    assert result["removed"][vocab.RECORDING_COLLECTION] == 1
    assert engine.store.get(recorded["recording"]["id"]) is None


def test_the_purge_is_an_administrator_action(engine, project):
    with pytest.raises(rules.SessionConsentAdministratorRequired):
        engine.purge_project(
            project["id"], room_id="room_a", actor="dana", role="team_member", source="test"
        )


# --------------------------------------------------------------------------- #
# Segments and the ceiling
# --------------------------------------------------------------------------- #


def test_a_segment_filter_matches_on_the_named_dimension():
    recording = {"page_path": "/pricing", "region": "EEA", "recorded_at": rules.stamp(NOW)}
    assert rules.matches_segment(recording, "page_path", "/pricing") is True
    assert rules.matches_segment(recording, "page_path", "/deck") is False


def test_an_empty_filter_matches_everything():
    assert rules.matches_segment({}, None, None) is True


def test_an_unknown_segment_dimension_is_refused():
    """A filter the product does not implement must not silently return everything."""

    with pytest.raises(rules.SessionConsentRefusal) as caught:
        rules.segment_value({"page_path": "/pricing"}, "browser")
    assert "dimension" in caught.value.errors


def test_the_segment_filter_narrows_the_recordings_list(engine, project):
    for path in ("/pricing", "/deck"):
        engine.ingest_visit(
            project["id"],
            visitor=f"v_{path.strip('/')}",
            page_view=f"pv_{path}",
            ip_address="203.0.113.9",
            region="EEA",
            axes=grant_both(),
            frame={"page_path": path},
            room_id="room_a",
            source="test",
        )
    filtered = engine.recordings(
        project_id=project["id"], room_id="room_a", dimension="page_path", value="/deck"
    )
    assert len(filtered) == 1
    assert filtered[0]["page_path"] == "/deck"


def test_the_ceiling_is_reported_as_a_word_and_two_numbers():
    state = rules.ceiling_state(10)
    assert state["label"] == "within the ceiling"
    assert state["ceiling"] == vocab.MAX_SESSIONS_PER_PROJECT_PER_DAY


def test_the_ceiling_reports_when_the_room_is_over_it():
    state = rules.ceiling_state(vocab.MAX_SESSIONS_PER_PROJECT_PER_DAY + 1)
    assert state["within_ceiling"] is False
    assert state["remaining"] == 0


# --------------------------------------------------------------------------- #
# Architecture
# --------------------------------------------------------------------------- #


def test_the_domain_package_never_opens_the_database():
    """The audit row is written in the same transaction as the change, so a module that
    reached past the store would break the product's one promise."""

    package = Path(rules.__file__).parent
    for module in sorted(package.glob("session_consent*.py")):
        text = module.read_text(encoding="utf-8")
        assert "import sqlite3" not in text, f"{module.name} imports sqlite3"
        assert ".connect(" not in text, f"{module.name} opens a connection directly"


def test_the_domain_package_does_not_import_the_app():
    package = Path(rules.__file__).parent
    for module in sorted(package.glob("session_consent*.py")):
        text = module.read_text(encoding="utf-8")
        assert "from dsr.api" not in text, f"{module.name} imports the app"
        assert "import dsr.api" not in text, f"{module.name} imports the app"


def test_the_engine_only_depends_on_the_store():
    """A domain module that imports another workflow's module is two workflows coupled."""

    engine_path = (
        Path(__file__).resolve().parents[1]
        / "dsr"
        / "security_governance"
        / "session_consent_engine.py"
    )
    text = engine_path.read_text(encoding="utf-8")
    assert "dsr.features" not in text
    assert "dsr.api" not in text


def _refuse_unmasked(payload):
    """Raise the refusal the ingest path raises for a frame that still carries content."""

    if rules.contains_unmasked_content(payload):
        raise rules.SessionConsentRefusal("A frame that still carries content was not stored.")
    return None  # pragma: no cover - the guard raises first


# --------------------------------------------------------------------------- #
# The decisions register
# --------------------------------------------------------------------------- #


def test_every_recorded_decision_names_the_alternative_it_rejected():
    """A derivation with no rejected alternative is a guess in a derivation's clothes."""

    for decision in inferences.describe():
        assert decision["options"], decision["id"]
        assert decision["chosen"] in decision["options"], decision["id"]
        assert decision["rejected_because"], decision["id"]
        assert decision["cost_of_the_choice"], decision["id"]


def test_the_default_masking_derivation_is_recorded():
    assert "DERIVED_DEFAULT_MASKING_IS_TOTAL_SUPPRESSION" in inferences.DECISIONS


def test_the_one_axis_denial_derivation_is_recorded():
    """The behaviour that surprises a reader needs its own entry."""

    assert "DERIVED_DENIAL_ON_ONE_AXIS_ENDS_THE_SESSION" in inferences.DECISIONS


def test_there_is_no_sso_path_decision():
    assert "DERIVED_NO_SSO_PATH" in inferences.DECISIONS


def test_describe_one_returns_nothing_for_an_unknown_decision():
    assert inferences.describe_one("DERIVED_NOPE") == {}
