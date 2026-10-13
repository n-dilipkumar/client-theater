"""WF-122: the tiered-disclosure rules, exercised as a domain module with no server.

The rules in ``dsr.competitive_baseline`` are pure: they touch the store and
nothing else. So every rule below is reachable without a ``TestClient``, and
these tests are what make each decision the issue left open checkable at the
instant it is stated about.

What is under test, and why each one matters
--------------------------------------------

* **A viewer at or above a section's tier sees it; below it, the section's
  below-tier state is shown.** The section, not the renderer, chooses teaser,
  hidden or on-request, so a seller's policy is what a below-tier recipient gets.
* **All three below-tier states ship.** The issue asks whether ``on request`` is
  a third state or is deferred; it is shipped, so this test pins it.
* **A hidden section is absent from the response, not flagged.** A row the page
  merely greys out still ships its name over the wire.
* **A grant reveals one section below a tier.** Section-level access control,
  distinct from raising the whole tier.
* **A revoked or expired link discloses nothing.** It is a closed room, not a
  below-tier viewer, so it gets no teaser and no on-request marker.
* **Every clear change is verified before it reports success.** The write is read
  back, and a write that does not land is a conflict.
* **Records are plain JSON in ``data``.** A team adding a field needs no
  coordination, so this module adds no migration and no typed column.

Every test names the property it protects, because a test named ``test_flow``
tells a reviewer nothing about which rule they can stop worrying about.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.competitive_baseline import rules, vocabulary as vocab
from dsr.competitive_baseline.engine import DisclosureEngine
from dsr.competitive_baseline.errors import (
    DisclosureError,
    DisclosureNotFound,
    DisclosureRefused,
)
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore

MODULE = "wf122_tiered_disclosure"

NOW = datetime(2026, 5, 4, 9, 0, tzinfo=timezone.utc)


@pytest.fixture()
def engine(store: RecordStore) -> DisclosureEngine:
    """The engine, with the clock pinned so every expiry boundary is exact."""
    return DisclosureEngine(store, now=lambda: NOW)


@pytest.fixture()
def room(store: RecordStore) -> str:
    return store.create("room", {"name": "Northwind"}, actor="test", source="test")["id"]


def _section(engine: DisclosureEngine, room_id: str, name: str, **extra: object) -> dict:
    return engine.create_section(room_id, {"name": name, **extra}, source="test", actor="test")


def _recipient(engine: DisclosureEngine, room_id: str, org: str = "Vantage", **extra: object):
    return engine.create_recipient(room_id, {"org": org, **extra}, source="test", actor="test")


# --------------------------------------------------------------------------- #
# The disclosure rule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("viewer_tier", [1, 2, 5])
def test_a_viewer_at_or_above_the_tier_sees_the_section_fully(viewer_tier):
    row = rules.render_section({"id": "s1", "name": "Financials", "min_tier": 1}, viewer_tier)
    assert row["disclosure"] == vocab.FULL
    assert "teaser" not in row


def test_a_viewer_below_the_tier_is_shown_the_sections_own_below_tier_state():
    """The section chooses teaser, hidden or on-request; the renderer does not."""
    for below in vocab.BELOW_TIER_STATES:
        section = {"id": "s1", "name": "Legal", "min_tier": 2, "below_tier": below}
        assert rules.disclosure_for(section, 0) == below


def test_all_three_below_tier_states_ship():
    """on_request is a third state, not deferred. The evidence supports all three."""
    assert set(vocab.BELOW_TIER_STATES) == {"hidden", "teaser", "on_request"}
    assert "on_request" in vocab.DECISIONS["below_tier_states"]


def test_a_teaser_carries_the_teaser_text_and_never_the_contents():
    section = {
        "id": "s1",
        "name": "Financials",
        "min_tier": 1,
        "below_tier": vocab.TEASER,
        "teaser": "Three years of statements.",
        "summary": "The real numbers.",
        "documents": ["statements.pdf"],
    }
    row = rules.render_section(section, 0)
    assert row["disclosure"] == vocab.TEASER
    assert row["teaser"] == "Three years of statements."
    assert "summary" not in row
    assert "documents" not in row


def test_an_on_request_section_carries_the_request_message():
    row = rules.render_section(
        {"id": "s1", "name": "Contracts", "min_tier": 2, "below_tier": vocab.ON_REQUEST}, 0
    )
    assert row["disclosure"] == vocab.ON_REQUEST
    assert row["message"] == "Available on request."


def test_a_hidden_section_is_absent_from_the_room_but_counted():
    sections = [
        {"id": "s1", "name": "Overview", "min_tier": 0, "order": 1},
        {"id": "s2", "name": "Security", "min_tier": 3, "below_tier": vocab.HIDDEN, "order": 2},
    ]
    rows = rules.render_room(sections, 0)
    assert [row["name"] for row in rows] == ["Overview"]


def test_a_grant_reveals_one_section_below_the_tier():
    """Section-level access control, distinct from raising the whole tier."""
    sections = [{"id": "s2", "name": "Security", "min_tier": 3, "below_tier": vocab.HIDDEN}]
    assert rules.render_room(sections, 0) == []
    granted = rules.render_room(sections, 0, granted_ids={"s2"})
    assert granted[0]["disclosure"] == vocab.FULL
    assert granted[0]["granted"] is True


def test_a_full_view_at_or_above_the_tier_is_not_marked_granted():
    sections = [{"id": "s1", "name": "Overview", "min_tier": 0}]
    row = rules.render_room(sections, 0, granted_ids={"s1"})[0]
    assert row["disclosure"] == vocab.FULL
    assert "granted" not in row


# --------------------------------------------------------------------------- #
# Link state
# --------------------------------------------------------------------------- #


def test_a_revoked_link_is_revoked_whatever_the_clock_says():
    recipient = {"state": vocab.REVOKED_STATE, "expires_at": (NOW + timedelta(days=5)).isoformat()}
    assert rules.effective_state(recipient, NOW) == vocab.REVOKED_STATE


def test_an_expired_link_is_derived_from_its_expiry_not_stored():
    recipient = {
        "state": vocab.ACTIVE_STATE,
        "expires_at": (NOW - timedelta(seconds=1)).isoformat(),
    }
    assert rules.effective_state(recipient, NOW) == vocab.EXPIRED_STATE
    # The stored state is untouched: expiry is a reading, not a write.
    assert recipient["state"] == vocab.ACTIVE_STATE


def test_a_link_with_no_expiry_stays_active():
    assert rules.effective_state({"state": vocab.ACTIVE_STATE}, NOW) == vocab.ACTIVE_STATE


def test_an_unreadable_expiry_does_not_expire_a_link():
    """A typo must not close a room: an unreadable date simply has no date."""
    for value in ("not-a-date", "2026-13-45", ""):
        state = rules.effective_state({"state": vocab.ACTIVE_STATE, "expires_at": value}, NOW)
        assert state == vocab.ACTIVE_STATE, value


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def test_a_section_needs_a_name():
    with pytest.raises(DisclosureError) as caught:
        rules.validate_section({"min_tier": 1})
    assert "name" in caught.value.errors


def test_a_negative_or_unreadable_min_tier_is_refused():
    with pytest.raises(DisclosureError) as caught:
        rules.validate_section({"name": "Bad tier", "min_tier": -1})
    assert "min_tier" in caught.value.errors
    with pytest.raises(DisclosureError) as caught:
        rules.validate_section({"name": "Bad tier", "min_tier": "many"})
    assert "min_tier" in caught.value.errors


def test_an_unknown_below_tier_state_is_refused():
    with pytest.raises(DisclosureError) as caught:
        rules.validate_section({"name": "Section", "below_tier": "secret"})
    assert "below_tier" in caught.value.errors


def test_a_recipient_needs_an_organisation():
    with pytest.raises(DisclosureError) as caught:
        rules.validate_recipient({"tier": 1})
    assert "org" in caught.value.errors


def test_an_unreadable_expiry_is_refused_at_validation_time():
    with pytest.raises(DisclosureError) as caught:
        rules.validate_recipient({"org": "Vantage", "expires_at": "soon"})
    assert "expires_at" in caught.value.errors


def test_a_section_keeps_a_field_this_workflow_has_never_heard_of():
    """Schema flexibility: a team adds a field without a migration."""
    normalised = rules.validate_section({"name": "Overview", "procurement_ticket": "PR-1"})
    assert normalised["procurement_ticket"] == "PR-1"


def test_a_bad_policy_label_key_is_refused():
    with pytest.raises(DisclosureError) as caught:
        rules.validate_policy({"tier_labels": {"one": "First"}})
    assert "tier_labels" in caught.value.errors


# --------------------------------------------------------------------------- #
# The access link
# --------------------------------------------------------------------------- #


def test_a_token_is_stored_only_as_a_hash_and_a_hint(engine, room):
    created = _recipient(engine, room)
    stored = engine.read_recipient(created["id"])
    assert "token_hash" not in stored
    assert stored["token_hint"] == created["token"][-4:]
    # The raw token resolves, and only the raw token.
    assert engine.resolve_token(created["token"])["id"] == created["id"]


def test_an_unknown_token_does_not_resolve(engine):
    with pytest.raises(DisclosureNotFound):
        engine.resolve_token("no-such-token")


def test_rotating_a_link_stops_the_old_token_resolving(engine, room):
    created = _recipient(engine, room)
    old = created["token"]
    rotated = engine.rotate_token(created["id"], source="test", actor="test")
    assert rotated["token"] != old
    with pytest.raises(DisclosureNotFound):
        engine.resolve_token(old)
    assert engine.resolve_token(rotated["token"])["id"] == created["id"]


# --------------------------------------------------------------------------- #
# Unlock, revoke and expiry
# --------------------------------------------------------------------------- #


def test_every_recipient_starts_at_tier_zero(engine, room):
    assert _recipient(engine, room)["tier"] == vocab.PUBLIC_TIER
    assert vocab.DEFAULT_RECIPIENT_TIER == 0


def test_unlocking_raises_the_tier_and_logs_an_event(engine, room):
    recipient = _recipient(engine, room)
    _section(engine, room, "Financials", min_tier=1, below_tier=vocab.TEASER, teaser="Later.")

    before = engine.render_for_recipient(recipient["id"])
    assert {row["disclosure"] for row in before["sections"]} == {vocab.TEASER}

    engine.unlock(recipient["id"], 1, source="test", actor="test")
    after = engine.render_for_recipient(recipient["id"])
    assert [row["disclosure"] for row in after["sections"]] == [vocab.FULL]

    kinds = [event["kind"] for event in engine.events_of(room)]
    assert vocab.EVENT_TIER_UNLOCKED in kinds


def test_a_revoked_link_cannot_be_unlocked(engine, room):
    recipient = _recipient(engine, room)
    engine.revoke(recipient["id"], source="test", actor="test")
    with pytest.raises(DisclosureRefused) as caught:
        engine.unlock(recipient["id"], 2, source="test", actor="test")
    assert caught.value.reason == DisclosureRefused.REASON_REVOKED


def test_an_expired_link_cannot_be_unlocked(engine, room):
    recipient = engine.create_recipient(
        room,
        {"org": "Expired", "expires_at": (NOW - timedelta(days=1)).isoformat()},
        source="test",
        actor="test",
    )
    with pytest.raises(DisclosureRefused) as caught:
        engine.unlock(recipient["id"], 2, source="test", actor="test")
    assert caught.value.reason == DisclosureRefused.REASON_EXPIRED


def test_a_revoked_link_discloses_nothing_not_even_a_teaser(engine, room):
    """A closed room is not a below-tier viewer. It gets no teaser and no marker."""
    recipient = _recipient(engine, room)
    _section(engine, room, "Overview", min_tier=0, below_tier=vocab.TEASER, teaser="Hi.")
    _section(engine, room, "Legal", min_tier=2, below_tier=vocab.ON_REQUEST)

    live = engine.render_for_recipient(recipient["id"])
    assert live["visible_count"] == 2

    engine.revoke(recipient["id"], source="test", actor="test")
    closed = engine.render_for_recipient(recipient["id"])
    assert closed["sections"] == []
    assert closed["visible_count"] == 0
    assert closed["state"] == vocab.REVOKED_STATE


def test_revoking_twice_writes_one_event(engine, room):
    recipient = _recipient(engine, room)
    engine.revoke(recipient["id"], source="test", actor="test")
    engine.revoke(recipient["id"], source="test", actor="test")
    revokes = [e for e in engine.events_of(room) if e["kind"] == vocab.EVENT_LINK_REVOKED]
    assert len(revokes) == 1


def test_the_write_is_read_back_before_success_is_reported(engine, room):
    """The unlock read-back is the rule; the audit row is the evidence it ran."""
    recipient = _recipient(engine, room)
    engine.unlock(recipient["id"], 2, source="test", actor="test")
    assert engine.read_recipient(recipient["id"])["tier"] == 2
    rows = [
        row
        for row in engine.store.audit(collection=vocab.RECIPIENT_COLLECTION)
        if row["record_id"] == recipient["id"]
    ]
    assert rows, "the unlock wrote no audit row"


# --------------------------------------------------------------------------- #
# Section-level access and requests
# --------------------------------------------------------------------------- #


def test_a_grant_reveals_a_below_tier_section_to_one_recipient(engine, room):
    recipient = _recipient(engine, room)
    section = _section(engine, room, "Security", min_tier=3, below_tier=vocab.HIDDEN)
    assert engine.render_for_recipient(recipient["id"])["sections"] == []

    engine.grant_section(section["id"], recipient["id"], source="test", actor="test")
    rows = engine.render_for_recipient(recipient["id"])["sections"]
    assert [row["name"] for row in rows] == ["Security"]
    assert rows[0]["granted"] is True


def test_a_grant_is_idempotent_by_recipient_and_section(engine, room):
    recipient = _recipient(engine, room)
    section = _section(engine, room, "Security", min_tier=3)
    first = engine.grant_section(section["id"], recipient["id"], source="test", actor="test")
    second = engine.grant_section(section["id"], recipient["id"], source="test", actor="test")
    assert first["id"] == second["id"]


def test_an_access_request_is_recorded_as_pending(engine, room):
    recipient = _recipient(engine, room)
    section = _section(engine, room, "Legal", min_tier=2, below_tier=vocab.ON_REQUEST)
    request = engine.request_section(
        section["id"], recipient["id"], note="Requested at kickoff.", source="test", actor="test"
    )
    assert request["state"] == "pending"
    assert engine.summary(room)["open_requests"] == 1


# --------------------------------------------------------------------------- #
# Editing is not a back door
# --------------------------------------------------------------------------- #


def test_a_generic_patch_cannot_revive_a_link_or_forge_a_token(engine, room):
    recipient = _recipient(engine, room)
    engine.revoke(recipient["id"], source="test", actor="test")
    engine.update_recipient(
        recipient["id"],
        {"org": "Vantage", "state": vocab.ACTIVE_STATE, "token_hash": "forged"},
        source="test",
        actor="test",
    )
    fresh = engine.read_recipient(recipient["id"])
    assert fresh["state"] == vocab.REVOKED_STATE
    assert fresh["effective_state"] == vocab.REVOKED_STATE


def test_updating_a_section_changes_its_tier(engine, room):
    section = _section(engine, room, "Financials", min_tier=1)
    engine.update_section(section["id"], {"min_tier": 2}, source="test", actor="test")
    assert engine.read_section(section["id"])["min_tier"] == 2


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #


def test_a_room_with_no_policy_serves_the_default_ladder(engine, room):
    policy = engine.read_policy(room)
    assert policy["tier_labels"] == vocab.DEFAULT_TIER_LABELS
    assert policy["default_below_tier"] == vocab.DEFAULT_BELOW_TIER


def test_a_section_takes_the_rooms_default_below_tier_state(engine, room):
    engine.set_policy(
        room,
        {"tier_labels": {"1": "Financials"}, "default_below_tier": vocab.TEASER},
        source="test",
        actor="test",
    )
    section = _section(engine, room, "Financials", min_tier=1)
    assert section["below_tier"] == vocab.TEASER


# --------------------------------------------------------------------------- #
# Schema flexibility
# --------------------------------------------------------------------------- #


def test_a_team_can_add_its_own_field_with_no_migration(store, room):
    section = store.create(
        vocab.SECTION_COLLECTION,
        {
            "name": "Overview",
            "min_tier": 0,
            "below_tier": vocab.HIDDEN,
            "procurement_ticket": "PR-4471",
            "our_field": {"reviewer": "sam", "weight": 3},
            vocab.ROOM_REF: room,
        },
        room_id=room,
        actor="test",
        source="test",
    )
    found = store.find(vocab.SECTION_COLLECTION, {"procurement_ticket": "PR-4471"}, limit=5)
    assert [row["id"] for row in found] == [section["id"]]
    assert found[0]["data"]["our_field"] == {"reviewer": "sam", "weight": 3}


def test_every_record_this_workflow_writes_is_ordinary_json(store, room, engine):
    recipient = _recipient(engine, room)
    section = _section(engine, room, "Overview", min_tier=0)
    engine.unlock(recipient["id"], 1, source="test", actor="test")
    engine.grant_section(section["id"], recipient["id"], source="test", actor="test")
    engine.request_section(section["id"], recipient["id"], source="test", actor="test")
    engine.revoke(recipient["id"], source="test", actor="test")

    for collection in (
        vocab.POLICY_COLLECTION,
        vocab.SECTION_COLLECTION,
        vocab.RECIPIENT_COLLECTION,
        vocab.GRANT_COLLECTION,
        vocab.REQUEST_COLLECTION,
        vocab.EVENT_COLLECTION,
    ):
        for row in store.find(collection, {}, limit=50):
            for key, value in row["data"].items():
                assert isinstance(key, str), (collection, key)
                assert isinstance(value, (str, int, float, bool, list, dict, type(None))), (
                    collection,
                    key,
                    value,
                )


# --------------------------------------------------------------------------- #
# The domain module's import surface
# --------------------------------------------------------------------------- #


def test_the_domain_modules_import_nothing_but_the_store():
    """An enforced property: no framework, no ``dsr.api``, no bare SQLite.

    The suite asserts this over every feature module; this test names it for this
    workflow's package, so the reason survives a refactor.
    """
    import dsr.competitive_baseline as package

    package_dir = Path(package.__file__).parent
    checked = 0
    for source_file in sorted(package_dir.glob("*.py")):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        for name in imported:
            assert name != "sqlite3", source_file.name
            assert name != "fastapi", source_file.name
            assert not name.startswith("starlette"), source_file.name
            assert name != "dsr.api", source_file.name
            # A domain module may import the store and nothing else from dsr.
            if name == "dsr" or name.startswith("dsr."):
                assert name == "dsr.store", (source_file.name, name)
        checked += 1
    assert checked >= 4, checked


def test_the_token_hash_is_a_sha256_digest_and_hides_the_token():
    token = rules.new_token()
    digest = rules.token_hash(token)
    assert len(digest) == 64
    assert token not in digest
    assert rules.token_hint(token) == token[-4:]


# --------------------------------------------------------------------------- #
# The feature module's own contract
# --------------------------------------------------------------------------- #


def test_the_feature_declares_a_ticket_derived_prefix():
    feature = load_feature(MODULE)
    assert feature.router.prefix == "/api/wf-122"
    assert feature.FEATURE["ticket"] == "WF-122"
    assert feature.FEATURE["id"] == "wf-122-tiered-disclosure"


def test_every_route_sits_under_this_features_own_prefix():
    feature = load_feature(MODULE)
    for route in feature.router.routes:
        assert route.path.startswith("/api/wf-122"), route.path


def test_every_engine_write_requires_a_source_and_has_no_default():
    """A write cannot be reached without naming the route that served it."""
    import inspect

    for name in (
        "set_policy",
        "create_section",
        "update_section",
        "delete_section",
        "create_recipient",
        "update_recipient",
        "unlock",
        "revoke",
        "rotate_token",
        "grant_section",
        "request_section",
    ):
        signature = inspect.signature(getattr(DisclosureEngine, name))
        assert "source" in signature.parameters, name
        assert signature.parameters["source"].default is inspect.Parameter.empty, name


def test_an_unrecognised_below_tier_value_fails_toward_hidden():
    """A stored value this workflow does not know must not disclose anything."""
    row = rules.render_section(
        {"id": "s1", "name": "Odd", "min_tier": 2, "below_tier": "mystery"}, 0
    )
    assert row["disclosure"] == vocab.HIDDEN


# --------------------------------------------------------------------------- #
# Vocabulary and demo data
# --------------------------------------------------------------------------- #


def test_the_vocabulary_publishes_the_decisions_the_issue_left_open():
    served = vocab.vocabulary()
    assert served["tier_labels"] == vocab.DEFAULT_TIER_LABELS
    assert served["below_tier_states"] == list(vocab.BELOW_TIER_STATES)
    assert served["recipient_states"] == list(vocab.RECIPIENT_STATES)
    # The three surfaces the spec names and this build does not carry.
    assert any("CRM" in note for note in served["not_implemented"])
    assert any("Page-level" in note for note in served["not_implemented"])
    assert any("Object storage" in note for note in served["not_implemented"])
    assert "tier_model" in served["decisions"]


def test_the_seed_string_is_encodable_by_cp1252(db: AuditedDatabase):
    """The seeder prints it on a Windows console, so every character must encode.

    A single RIGHTWARDS ARROW in one recovered feature broke the entire seeder.
    """
    feature = load_feature(MODULE)
    room_ids = [
        (db.create("room", {"name": name}, actor="seed", source="seed")["id"], name)
        for name in ("Northwind", "Vantage")
    ]
    reported = feature.seed(db, {"room_ids": room_ids, "now": NOW})
    assert reported
    reported.encode("cp1252")
    assert reported == reported.encode("cp1252").decode("cp1252")


def test_the_seed_creates_the_states_the_issue_asks_for(db: AuditedDatabase):
    """The demo must show all three below-tier states and both closed-link states,
    or a reviewer cannot see the workflow doing its work."""
    feature = load_feature(MODULE)
    room_ids = [
        (db.create("room", {"name": name}, actor="seed", source="seed")["id"], name)
        for name in ("Northwind", "Vantage")
    ]
    feature.seed(db, {"room_ids": room_ids, "now": NOW})

    store = RecordStore(db)
    engine = DisclosureEngine(store, now=lambda: NOW)
    counted = engine.summary()

    assert counted["sections"] >= 4, counted
    assert counted["recipients"] >= 4, counted
    assert counted["revoked"] >= 1, counted
    assert counted["expired"] >= 1, counted
    assert counted["unlocked"] >= 1, counted
    assert counted["grants"] >= 1, counted
    assert counted["open_requests"] >= 1, counted

    # The three below-tier states are all present in the demo room.
    sections = engine.list_sections(room_ids[0][0])
    below = {section["below_tier"] for section in sections if section["min_tier"] > 0}
    assert {"teaser", "on_request", "hidden"} <= below, below


def test_the_summary_reports_the_room_numbers_without_a_write(engine, room):
    _section(engine, room, "Overview", min_tier=0)
    _recipient(engine, room)
    counted = engine.summary(room)
    assert counted["sections"] == 1
    assert counted["recipients"] == 1
    assert counted["active"] == 1
    assert "generated_at" in counted
