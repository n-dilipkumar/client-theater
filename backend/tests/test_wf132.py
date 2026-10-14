"""WF-132: the domain surface of in-room conversation and champion self-service.

The engine is exercised here without a server, which is the point of the split:
the rules live in ``dsr.inroom_collaboration`` and every behaviour below is a
function of a store and a frozen clock. ``test_wf132_http.py`` then proves the
routes, the error mapping and the audit source over the wire.

The clock is frozen at :data:`NOW` everywhere. No test depends on today's date, so
none of them can turn red on a particular weekday or after a particular date.
"""

from __future__ import annotations

import ast
import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import dsr.roles as roles
import pytest
from dsr.inroom_collaboration import rules, vocabulary as v
from dsr.inroom_collaboration.engine import InRoomEngine
from dsr.inroom_collaboration.errors import (
    InRoomConflict,
    InRoomDenied,
    InRoomNotFound,
    InRoomValidation,
)
from dsr.store import RecordStore

FEATURE_MODULE = "wf132_in_room_collaboration_comments_threads_tagging_and_champion"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
ROOM = "room_1"
OWNER = "dana"
BUYER = "a.buyer@northwind.example"
CHAMPION = "legal@northwind.example"


# --------------------------------------------------------------------------- #
# Provisioning helpers
# --------------------------------------------------------------------------- #


def make_room(store, room_id=ROOM, owner=OWNER, **overrides):
    data = {"name": "Northwind Traders", "owner": owner}
    data.update(overrides)
    return store.create("room", data, record_id=room_id, actor=owner, source="test")


def grant(store, principal, role, *, room_id=ROOM, valid_until=None):
    return store.create(
        v.ACCESS_COLLECTION,
        {"principal": principal, "role": role, "access_valid_until": valid_until},
        room_id=room_id,
        actor=OWNER,
        source="test",
    )


class Clock:
    """A deterministic clock that advances a fixed step on every read.

    Freezing the clock to one instant would make two writes in one test share a
    millisecond, and an ordering assertion would then rest on the id tie-break
    instead of on time. A small fixed step keeps every test reproducible without
    depending on the wall clock.
    """

    def __init__(self, start=NOW, step=timedelta(milliseconds=10)):
        self.current = start
        self.step = step

    def __call__(self):
        value = self.current
        self.current = self.current + self.step
        return value

    def set(self, moment):
        self.current = moment


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def engine(store, clock):
    """An engine over an empty store with one room and two buyer grants."""
    make_room(store)
    grant(store, BUYER, v.ROLE_CONTRIBUTOR)
    grant(store, CHAMPION, v.ROLE_COLLABORATOR)
    return InRoomEngine(store, now=clock)


def open_thread(engine, **overrides):
    body = {
        "actor": BUYER,
        "anchor_type": v.ANCHOR_PAGE,
        "anchor_id": "page_1",
        "body": "Can we talk about the tiers?",
    }
    body.update(overrides)
    return engine.open_thread(ROOM, body, source="test")


# --------------------------------------------------------------------------- #
# The rules
# --------------------------------------------------------------------------- #


class TestRules:
    def test_a_body_is_trimmed(self):
        assert rules.normalise_body("  hello  ") == "hello"

    def test_an_empty_body_is_refused(self):
        with pytest.raises(InRoomValidation) as raised:
            rules.normalise_body("   ")
        assert raised.value.errors

    def test_a_none_body_is_refused(self):
        with pytest.raises(InRoomValidation):
            rules.normalise_body(None)

    def test_control_characters_are_refused(self):
        with pytest.raises(InRoomValidation):
            rules.normalise_body("hello\x00world")

    def test_a_body_past_the_limit_is_refused(self):
        with pytest.raises(InRoomValidation):
            rules.normalise_body("x" * (v.MAX_COMMENT_LENGTH + 1))

    def test_mentions_are_read_in_order_and_deduped(self):
        body = "ping @Dana and @a.buyer@Northwind.example, then @dana again"
        assert rules.extract_mentions(body) == ["dana", "a.buyer@northwind.example"]

    def test_a_mention_stops_at_punctuation(self):
        assert rules.extract_mentions("cc @legal, please") == ["legal"]

    def test_no_mentions_is_an_empty_list(self):
        assert rules.extract_mentions("nothing tagged here") == []

    @pytest.mark.parametrize("kind", v.ANCHOR_KINDS)
    def test_every_researched_anchor_kind_is_accepted(self, kind):
        assert rules.validate_anchor(kind, "obj_1") == (kind, "obj_1")

    def test_an_unknown_anchor_kind_is_refused(self):
        with pytest.raises(InRoomValidation):
            rules.validate_anchor("spreadsheet", "x")

    def test_an_empty_anchor_id_is_refused(self):
        with pytest.raises(InRoomValidation):
            rules.validate_anchor(v.ANCHOR_PAGE, "  ")

    def test_anchor_label_falls_back_to_a_readable_id(self):
        assert rules.anchor_label(v.ANCHOR_PAGE, "Pricing", "p1") == "Pricing"
        assert rules.anchor_label(v.ANCHOR_PAGE, None, "p1") == "Page p1"

    def test_the_owner_is_the_seller(self):
        assert rules.side_for(OWNER, OWNER) == v.SIDE_SELLER

    def test_a_grant_holder_is_a_buyer(self):
        assert rules.side_for(BUYER, OWNER) == v.SIDE_BUYER

    @pytest.mark.parametrize("role", v.COMMENT_ROLES)
    def test_every_documented_role_may_comment(self, role):
        assert rules.can_comment(role)

    def test_no_role_cannot_comment(self):
        assert not rules.can_comment(None)

    def test_only_content_managing_roles_may_edit(self):
        assert rules.can_edit_content(v.ROLE_COLLABORATOR)
        assert rules.can_edit_content(v.ROLE_OWNER)
        assert not rules.can_edit_content(v.ROLE_CONTRIBUTOR)
        assert not rules.can_edit_content(v.ROLE_VIEWER)

    def test_a_buyer_collaborator_is_the_champion(self):
        assert rules.is_champion(v.ROLE_COLLABORATOR, v.SIDE_BUYER)

    def test_a_seller_collaborator_is_not_the_champion(self):
        assert not rules.is_champion(v.ROLE_COLLABORATOR, v.SIDE_SELLER)

    def test_role_labels_are_readable(self):
        assert rules.role_label(v.ROLE_COLLABORATOR) == "Room Collaborator"
        assert rules.role_label("costs_center_owner") == "Costs Center Owner"
        assert rules.role_label(None) == "No Role"


class TestUnansweredQuestions:
    def asked(self, *, author=BUYER, side=v.SIDE_BUYER, kind=v.KIND_QUESTION, at, cid="c1"):
        return {
            "id": cid,
            "author": author,
            "author_side": side,
            "author_role": v.ROLE_CONTRIBUTOR,
            "kind": kind,
            "at": at.isoformat(),
        }

    def test_a_question_inside_the_window_does_not_escalate(self):
        asked = NOW - timedelta(hours=1)
        questions = rules.unanswered_buyer_questions([self.asked(at=asked)], now=NOW)
        assert questions == []

    def test_a_question_past_the_window_escalates(self):
        asked = NOW - timedelta(hours=v.UNANSWERED_QUESTION_HOURS + 1)
        questions = rules.unanswered_buyer_questions([self.asked(at=asked)], now=NOW)
        assert len(questions) == 1
        assert questions[0]["hours_open"] > v.UNANSWERED_QUESTION_HOURS

    def test_a_seller_reply_answers_a_question(self):
        asked = NOW - timedelta(hours=v.UNANSWERED_QUESTION_HOURS + 1)
        reply = self.asked(
            author=OWNER,
            side=v.SIDE_SELLER,
            kind=v.KIND_COMMENT,
            at=NOW - timedelta(hours=2),
            cid="c2",
        )
        assert rules.unanswered_buyer_questions([self.asked(at=asked), reply], now=NOW) == []

    def test_a_champion_reply_answers_a_question(self):
        asked = NOW - timedelta(hours=v.UNANSWERED_QUESTION_HOURS + 1)
        reply = self.asked(
            author=CHAMPION,
            side=v.SIDE_BUYER,
            kind=v.KIND_COMMENT,
            at=NOW - timedelta(hours=2),
            cid="c2",
        )
        reply["author_role"] = v.ROLE_COLLABORATOR
        assert rules.unanswered_buyer_questions([self.asked(at=asked), reply], now=NOW) == []

    def test_a_seller_question_is_never_escalated(self):
        asked = NOW - timedelta(hours=v.UNANSWERED_QUESTION_HOURS + 1)
        seller = self.asked(author=OWNER, side=v.SIDE_SELLER, at=asked)
        assert rules.unanswered_buyer_questions([seller], now=NOW) == []

    def test_a_resolved_thread_has_no_unanswered_question(self):
        asked = NOW - timedelta(hours=v.UNANSWERED_QUESTION_HOURS + 1)
        result = rules.unanswered_buyer_questions(
            [self.asked(at=asked)], now=NOW, thread_state=v.STATE_RESOLVED
        )
        assert result == []

    def test_an_unreadable_timestamp_does_not_escalate(self):
        bad = self.asked(at=NOW)
        bad["at"] = "not a date"
        assert rules.unanswered_buyer_questions([bad], now=NOW) == []


# --------------------------------------------------------------------------- #
# The vocabulary, and the roles it reuses
# --------------------------------------------------------------------------- #


class TestVocabulary:
    def test_the_contested_flag_is_published(self):
        body = v.vocabulary()
        assert body["contested"]["flag"] is True
        assert "contested" in body["contested"]["reason"]

    def test_the_four_researched_anchor_kinds_are_named(self):
        assert list(v.ANCHOR_KINDS) == ["page", "section", "asset", "map_task"]

    def test_the_collection_names_are_unique(self):
        names = [
            v.THREAD_COLLECTION,
            v.COMMENT_COLLECTION,
            v.MENTION_COLLECTION,
            v.EDIT_COLLECTION,
            v.ESCALATION_COLLECTION,
            v.CONVERSION_COLLECTION,
        ]
        assert len(set(names)) == len(names)

    def test_the_comment_roles_are_exactly_wf004s_roles(self):
        """Reuse, made checkable: the two vocabularies must not drift apart."""
        assert set(v.COMMENT_ROLES) == set(roles.ROLE_IDS) | {roles.OWNER}

    def test_the_edit_roles_come_from_wf004s_roles(self):
        assert set(v.EDIT_ROLES) <= set(roles.ROLE_IDS) | {roles.OWNER}
        assert v.CHAMPION_ROLE in roles.ROLE_IDS

    def test_the_access_collection_is_wf004s(self):
        assert v.ACCESS_COLLECTION == roles.ACCESS

    def test_what_is_deliberately_absent_is_recorded(self):
        body = v.vocabulary()
        assert len(body["not_implemented"]) >= 3
        assert body["unvalidated_anchors"] == [v.ANCHOR_ASSET]

    def test_the_four_automations_are_named(self):
        automations = v.vocabulary()["automations"]
        assert set(automations) == {
            "auto_notify_on_mention",
            "auto_subscribe_watchers",
            "auto_escalate_unanswered_question",
            "auto_thread_to_task_conversion",
        }


# --------------------------------------------------------------------------- #
# Threads and comments
# --------------------------------------------------------------------------- #


class TestThreads:
    def test_opening_a_thread_writes_its_first_comment(self, engine):
        thread = open_thread(engine)
        assert thread["state"] == v.STATE_OPEN
        assert thread["message_count"] == 1
        assert thread["anchor"] == {"type": "page", "id": "page_1", "label": "Page page_1"}
        assert [c["body"] for c in thread["comments"]] == ["Can we talk about the tiers?"]
        assert thread["contested"] is True

    def test_the_author_watches_their_own_thread(self, engine):
        thread = open_thread(engine)
        assert thread["watchers"] == [BUYER]

    def test_a_stranger_cannot_open_a_thread(self, engine):
        with pytest.raises(InRoomDenied):
            open_thread(engine, actor="nobody@example.com")

    def test_a_lapsed_grant_cannot_post(self, store):
        make_room(store)
        grant(store, BUYER, v.ROLE_CONTRIBUTOR, valid_until="2020-01-01")
        engine = InRoomEngine(store, now=lambda: NOW)
        with pytest.raises(InRoomDenied):
            open_thread(engine)

    def test_the_owner_can_reply(self, engine):
        thread = open_thread(engine)
        result = engine.add_comment(thread["id"], {"actor": OWNER, "body": "Yes."}, source="test")
        assert result["comment"]["author_side"] == v.SIDE_SELLER
        assert engine.read_thread(thread["id"])["message_count"] == 2

    def test_a_resolved_thread_refuses_a_comment(self, engine):
        thread = open_thread(engine)
        engine.resolve_thread(thread["id"], {"actor": OWNER}, source="test")
        with pytest.raises(InRoomConflict) as raised:
            engine.add_comment(thread["id"], {"actor": OWNER, "body": "x"}, source="test")
        assert raised.value.reason == InRoomConflict.REASON_THREAD_RESOLVED

    def test_a_resolved_thread_can_be_reopened(self, engine):
        thread = open_thread(engine)
        engine.resolve_thread(thread["id"], {"actor": OWNER}, source="test")
        reopened = engine.reopen_thread(thread["id"], {"actor": OWNER}, source="test")
        assert reopened["state"] == v.STATE_OPEN

    def test_resolving_twice_is_a_conflict(self, engine):
        thread = open_thread(engine)
        engine.resolve_thread(thread["id"], {"actor": OWNER}, source="test")
        with pytest.raises(InRoomConflict) as raised:
            engine.resolve_thread(thread["id"], {"actor": OWNER}, source="test")
        assert raised.value.reason == InRoomConflict.REASON_THREAD_ALREADY_RESOLVED

    def test_reopening_an_open_thread_is_a_conflict(self, engine):
        thread = open_thread(engine)
        with pytest.raises(InRoomConflict) as raised:
            engine.reopen_thread(thread["id"], {"actor": OWNER}, source="test")
        assert raised.value.reason == InRoomConflict.REASON_THREAD_NOT_RESOLVED

    def test_an_unknown_thread_is_not_found(self, engine):
        with pytest.raises(InRoomNotFound):
            engine.read_thread("nope")

    def test_an_unknown_room_is_not_found(self, engine):
        with pytest.raises(InRoomNotFound):
            engine.open_thread(
                "nope",
                {"actor": BUYER, "anchor_type": "page", "anchor_id": "p", "body": "hi"},
                source="test",
            )

    def test_a_reply_must_name_its_parent(self, engine):
        thread = open_thread(engine)
        with pytest.raises(InRoomValidation):
            engine.add_comment(
                thread["id"],
                {"actor": OWNER, "body": "reply", "kind": v.KIND_REPLY},
                source="test",
            )

    def test_an_unknown_comment_kind_is_refused(self, engine):
        thread = open_thread(engine)
        with pytest.raises(InRoomValidation):
            engine.add_comment(
                thread["id"], {"actor": OWNER, "body": "x", "kind": "shout"}, source="test"
            )

    def test_threads_can_be_listed_and_filtered(self, engine):
        first = open_thread(engine)
        open_thread(engine, anchor_id="page_2", actor=CHAMPION)
        assert len(engine.list_threads(room_id=ROOM)) == 2
        assert [t["id"] for t in engine.list_threads(room_id=ROOM, anchor_id="page_1")] == [
            first["id"]
        ]
        assert len(engine.list_threads(room_id=ROOM, principal=CHAMPION)) == 1
        assert engine.list_threads(room_id="other") == []


# --------------------------------------------------------------------------- #
# Mentions
# --------------------------------------------------------------------------- #


class TestMentions:
    def test_a_tag_resolves_to_a_live_grant(self, engine):
        thread = open_thread(engine, body="cc @legal")
        assert thread["mentions"][0]["principal"] == CHAMPION
        assert thread["mentions"][0]["state"] == v.MENTION_NOTIFIED
        assert CHAMPION in thread["watchers"]

    def test_a_local_part_resolves(self, engine):
        thread = open_thread(engine, body="cc @legal@northwind.example")
        assert thread["mentions"][0]["principal"] == CHAMPION

    def test_an_unknown_handle_is_unresolved(self, engine):
        thread = open_thread(engine, body="cc @ghost")
        assert thread["mentions"][0]["state"] == v.MENTION_UNRESOLVED
        assert thread["mentions"][0]["principal"] is None
        assert thread["unresolved_mentions"] == 1

    def test_a_lapsed_grant_is_not_a_recipient(self, store):
        make_room(store)
        grant(store, BUYER, v.ROLE_CONTRIBUTOR)
        grant(store, CHAMPION, v.ROLE_COLLABORATOR, valid_until="2020-01-01")
        engine = InRoomEngine(store, now=lambda: NOW)
        thread = open_thread(engine, body="cc @legal")
        assert thread["mentions"][0]["state"] == v.MENTION_UNRESOLVED

    def test_tagging_an_existing_thread_subscribes_the_person(self, engine):
        thread = open_thread(engine)
        result = engine.tag(thread["id"], {"actor": BUYER, "principals": ["@legal"]}, source="test")
        assert result["mentions"][0]["principal"] == CHAMPION
        assert CHAMPION in result["watchers"]

    def test_tagging_needs_at_least_one_handle(self, engine):
        thread = open_thread(engine)
        with pytest.raises(InRoomValidation):
            engine.tag(thread["id"], {"actor": BUYER, "principals": []}, source="test")

    def test_watching_adds_a_principal(self, engine):
        thread = open_thread(engine)
        result = engine.watch(thread["id"], {"actor": BUYER, "principal": OWNER}, source="test")
        assert OWNER in result["watchers"]


# --------------------------------------------------------------------------- #
# Champion self-service
# --------------------------------------------------------------------------- #


class TestChampionEdits:
    def edit(self, engine, actor=CHAMPION, **overrides):
        body = {
            "actor": actor,
            "section_ref": "page_1",
            "before": {"headline": "Pricing", "tiers": "3"},
            "after": {"headline": "Pricing for the rollout", "tiers": "4"},
        }
        body.update(overrides)
        return engine.record_edit(ROOM, body, source="test")

    def test_a_buyer_collaborator_edits_and_is_labelled_the_champion(self, engine):
        edit = self.edit(engine)
        assert edit["champion"] is True
        assert edit["side"] == v.SIDE_BUYER

    def test_the_delta_names_what_changed(self, engine):
        delta = self.edit(engine)["delta"]
        assert delta["headline"] == {"from": "Pricing", "to": "Pricing for the rollout"}
        assert delta["tiers"] == {"from": "3", "to": "4"}

    def test_a_viewer_cannot_edit(self, engine):
        grant(engine.store, "viewer@northwind.example", v.ROLE_VIEWER)
        with pytest.raises(InRoomDenied):
            self.edit(engine, actor="viewer@northwind.example")

    def test_a_content_contributor_cannot_edit(self, engine):
        with pytest.raises(InRoomDenied):
            self.edit(engine, actor=BUYER)

    def test_the_seller_owner_edits_but_is_not_champion(self, engine):
        assert self.edit(engine, actor=OWNER)["champion"] is False

    def test_a_section_is_required(self, engine):
        with pytest.raises(InRoomValidation):
            self.edit(engine, section_ref="")

    def test_a_before_or_after_is_required(self, engine):
        with pytest.raises(InRoomValidation):
            self.edit(engine, before={}, after={})

    def test_the_change_log_is_newest_first(self, engine):
        first = self.edit(engine, summary="first")
        second = self.edit(engine, summary="second")
        log = engine.list_edits(ROOM)
        assert [row["id"] for row in log] == [second["id"], first["id"]]

    def test_champion_activity_counts_only_the_champion(self, engine):
        self.edit(engine, actor=CHAMPION)
        self.edit(engine, actor=OWNER, section_ref="page_2")
        activity = engine.champion_activity(ROOM)
        assert activity["champion_edits"] == 1
        assert activity["editors"] == [{"principal": CHAMPION, "edits": 1}]


# --------------------------------------------------------------------------- #
# Escalation and conversion
# --------------------------------------------------------------------------- #


class TestEscalations:
    def ask(self, engine, *, actor=BUYER, hours=0):
        clock_now = NOW - timedelta(hours=hours)
        scoped = InRoomEngine(engine.store, now=lambda: clock_now)
        return scoped.open_thread(
            ROOM,
            {
                "actor": actor,
                "anchor_type": v.ANCHOR_MAP_TASK,
                "anchor_id": "task_1",
                "body": "Who signs the DPA?",
                "kind": v.KIND_QUESTION,
            },
            source="test",
        )

    def test_an_overdue_question_escalates_to_the_owner(self, engine):
        self.ask(engine, hours=v.UNANSWERED_QUESTION_HOURS + 4)
        created = engine.refresh_escalations(ROOM, source="test")
        assert len(created) == 1
        assert created[0]["escalated_to"] == OWNER

    def test_a_fresh_question_does_not_escalate(self, engine):
        self.ask(engine)
        assert engine.refresh_escalations(ROOM, source="test") == []

    def test_the_sweep_is_idempotent(self, engine):
        self.ask(engine, hours=v.UNANSWERED_QUESTION_HOURS + 4)
        engine.refresh_escalations(ROOM, source="test")
        assert engine.refresh_escalations(ROOM, source="test") == []
        assert len(engine.escalations(room_id=ROOM)) == 1

    def test_a_seller_reply_stops_the_escalation(self, engine):
        thread = self.ask(engine, hours=v.UNANSWERED_QUESTION_HOURS + 4)
        engine.add_comment(thread["id"], {"actor": OWNER, "body": "I do."}, source="test")
        assert engine.refresh_escalations(ROOM, source="test") == []


class TestConversion:
    def test_a_thread_converts_to_a_task_record(self, engine):
        thread = open_thread(engine)
        conversion = engine.convert_to_task(
            thread["id"],
            {"actor": OWNER, "owner": OWNER, "title": "Follow up on tiers"},
            source="test",
        )
        assert conversion["thread_ref"] == thread["id"]
        assert conversion["title"] == "Follow up on tiers"

    def test_a_conversion_defaults_its_title_from_the_anchor(self, engine):
        thread = open_thread(engine)
        conversion = engine.convert_to_task(thread["id"], {"actor": OWNER}, source="test")
        assert conversion["title"] == "Page page_1"

    def test_linking_a_map_task_marks_the_thread(self, engine):
        thread = open_thread(engine)
        engine.convert_to_task(
            thread["id"], {"actor": OWNER, "map_task_ref": "task_9"}, source="test"
        )
        assert engine.read_thread(thread["id"])["map_task_ref"] == "task_9"

    def test_converting_twice_is_a_conflict(self, engine):
        thread = open_thread(engine)
        engine.convert_to_task(
            thread["id"], {"actor": OWNER, "map_task_ref": "task_9"}, source="test"
        )
        with pytest.raises(InRoomConflict) as raised:
            engine.convert_to_task(
                thread["id"], {"actor": OWNER, "map_task_ref": "task_10"}, source="test"
            )
        assert raised.value.reason == InRoomConflict.REASON_ALREADY_CONVERTED


class TestIdentityAndSummary:
    def test_identity_reports_the_champion(self, engine):
        who = engine.identity(ROOM, CHAMPION)
        assert who["role"] == v.ROLE_COLLABORATOR
        assert who["side"] == v.SIDE_BUYER
        assert who["is_champion"] is True

    def test_identity_reports_a_stranger_as_roleless(self, engine):
        assert engine.identity(ROOM, "nobody@example.com")["role"] is None

    def test_summary_counts_the_room(self, engine):
        open_thread(engine, body="cc @ghost")
        engine.record_edit(
            ROOM,
            {"actor": CHAMPION, "section_ref": "page_1", "after": {"a": 1}},
            source="test",
        )
        totals = engine.summary(ROOM)
        assert totals["threads"] == 1
        assert totals["unresolved_mentions"] == 1
        assert totals["champion_edits"] == 1
        assert totals["contested"] is True


# --------------------------------------------------------------------------- #
# The architectural guards
# --------------------------------------------------------------------------- #


class TestArchitecture:
    def domain_paths(self):
        package = importlib.import_module("dsr.inroom_collaboration")
        return sorted(Path(package.__file__).parent.glob("*.py"))

    def test_the_domain_package_imports_nothing_but_the_store(self):
        for path in self.domain_paths():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    if not name.startswith("dsr"):
                        continue
                    assert name == "dsr.store" or name.startswith("dsr.inroom_collaboration"), (
                        f"{path.name} imports {name}"
                    )

    def test_the_domain_package_never_imports_the_app_or_sqlite(self):
        for path in self.domain_paths():
            text = path.read_text(encoding="utf-8")
            assert "from dsr.api" not in text and "import dsr.api" not in text
            assert "import sqlite3" not in text

    def test_no_domain_module_imports_another_workflow_package(self):
        for path in self.domain_paths():
            assert "dsr.features" not in path.read_text(encoding="utf-8")

    def test_the_feature_module_never_imports_the_app(self):
        module = importlib.import_module(f"dsr.features.{FEATURE_MODULE}")
        text = Path(module.__file__).read_text(encoding="utf-8")
        assert "from dsr.api" not in text and "import dsr.api" not in text

    def test_the_feature_exports_a_descriptor_and_a_ticket_prefix(self):
        module = importlib.import_module(f"dsr.features.{FEATURE_MODULE}")
        assert module.FEATURE["ticket"] == "WF-132"
        assert module.FEATURE["id"] == v.FEATURE_ID
        assert module.router.prefix == "/api/wf-132"


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


class TestSeed:
    def test_the_seed_creates_every_researched_state_and_returns_ascii(self, db):
        from dsr.features import load_feature

        store = RecordStore(db)
        make_room(store)
        grant(store, BUYER, v.ROLE_CONTRIBUTOR)
        grant(store, CHAMPION, v.ROLE_COLLABORATOR)
        module = load_feature(FEATURE_MODULE)

        summary = module.seed(
            db, {"room_ids": [(ROOM, "Northwind Traders")], "now": NOW, "rng": None}
        )

        assert summary
        summary.encode("cp1252")
        assert store.list(v.THREAD_COLLECTION, room_id=ROOM, limit=50)
        assert store.list(v.ESCALATION_COLLECTION, room_id=ROOM, limit=50)
        assert store.list(v.EDIT_COLLECTION, room_id=ROOM, limit=50)
        assert store.list(v.CONVERSION_COLLECTION, room_id=ROOM, limit=50)

    def test_the_seed_with_no_rooms_returns_an_empty_string(self, db):
        from dsr.features import load_feature

        module = load_feature(FEATURE_MODULE)
        assert module.seed(db, {"room_ids": [], "now": NOW, "rng": None}) == ""
