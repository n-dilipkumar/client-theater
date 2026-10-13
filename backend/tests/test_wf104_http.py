"""WF-104's engine, its HTTP surface, its audit trail and its seed.

Two layers are exercised here. The engine is driven directly, with a clock the test owns, because
that is where the state machine lives. The router is then driven through the application's own
``TestClient``, so the discovery, the error mapping and the audit rows are all the real ones
rather than stand-ins.

**No test in this file carries a fixed date.** Every instant comes from ``CLOCK``, and every
duration is an offset from it. The ``client`` fixture points at a fresh database per test, so
nothing here can see another test's rows, and every test that needs to move time moves it rather
than waiting.

The audit-source rule gets its own section: the contract requires that every recorded source names
a route the host actually mounted, and this file checks that against the live registry rather than
against a list somebody wrote twice.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.features import wf104_nurture_series as feature
from dsr.nurture_series import rules, vocabulary as vocab
from dsr.nurture_series.engine import NurtureSeriesEngine
from dsr.nurture_series.errors import RunNotFound, SeriesNotFound, SeriesRefused
from dsr.store import RecordStore
from fastapi.routing import APIRoute

PREFIX = feature.router.prefix

#: A frozen clock. Every instant below is an offset from it.
CLOCK = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)

MINUTE = 60
HOUR = 60 * MINUTE
DAY = 24 * HOUR
THREE_DAYS = 3 * DAY
THIRTY_DAYS = 30 * DAY

SOURCE = feature.EVALUATE_SOURCE


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def engine(store: RecordStore) -> NurtureSeriesEngine:
    return NurtureSeriesEngine(store, now=lambda: CLOCK)


@pytest.fixture
def room_id(store: RecordStore) -> str:
    return store.create(
        "room", {"name": "Northwind Traders", "account": "Northwind"}, actor="dana"
    )["id"]


def at_engine(store: RecordStore, minutes: float) -> NurtureSeriesEngine:
    frozen = CLOCK + timedelta(minutes=minutes)
    return NurtureSeriesEngine(store, now=lambda: frozen)


BLOCKS = [
    {
        "kind": "rule",
        "id": "who_enters",
        "filters": [{"field": "plan", "op": "eq", "value": "enterprise"}],
        "match": "all",
    },
    {
        "kind": "content",
        "id": "intro",
        "channel": "in_app",
        "content_type": "post",
        "body": "Here is the tour.",
        "goal": "Get a reply",
        "send_window_seconds": THIRTY_DAYS,
        "fallback_channel": "email",
        "fallback_enabled": True,
    },
    {"kind": "wait", "id": "give_it_a_day", "duration_seconds": DAY},
    {
        "kind": "rule",
        "id": "clicked_link",
        "condition": "clicked_link",
        "wait_mode": "for",
        "match_within_seconds": THREE_DAYS,
    },
    {"kind": "tag", "id": "engaged", "tag": "nurtured"},
]


def make_series(engine: NurtureSeriesEngine, room_id: str, **payload: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "Enterprise nurture", "blocks": BLOCKS}
    body.update(payload)
    return engine.create_series(room_id, body, source=feature.SERIES_CREATE_SOURCE, actor="dana")


def go_live(engine: NurtureSeriesEngine, series_id: str) -> dict[str, Any]:
    return engine.go_live(series_id, source=feature.GO_LIVE_SOURCE, actor="dana")


def buyer(contact_id: str = "c1", **overrides: Any) -> dict[str, Any]:
    base = {
        "contact_id": contact_id,
        "attributes": {"email": f"{contact_id}@northwind.example", "plan": "enterprise"},
        "company": {"name": "Northwind Traders", "employees": 400},
        "online": True,
        "channels": ["in_app", "email"],
    }
    base.update(overrides)
    return base


def sweep(
    engine: NurtureSeriesEngine, room_id: str, audience: list[dict[str, Any]], minutes: float = 0
):
    return engine.evaluate(
        room_id,
        {"audience": audience, "now": rules.to_iso(CLOCK + timedelta(minutes=minutes))},
        source=SOURCE,
        actor="scheduler",
    )


def enter(
    engine: NurtureSeriesEngine, room_id: str, audience: list[dict[str, Any]]
) -> dict[str, Any]:
    """Go live and run one poll, returning the first run's id."""

    series = make_series(engine, room_id)
    go_live(engine, series["id"])
    result = sweep(engine, room_id, audience)
    return {
        "series": series,
        "sweep": result,
        "run_id": result["fired"][0]["id"] if result["fired"] else None,
    }


# --------------------------------------------------------------------------- #
# Feature registration
# --------------------------------------------------------------------------- #


class TestFeatureRegistration:
    def test_it_exports_its_ticket_and_name(self):
        assert feature.FEATURE["ticket"] == "WF-104"
        assert feature.FEATURE["name"]

    def test_the_prefix_is_ticket_derived_and_under_api(self):
        assert feature.router.prefix == "/api/wf-104"

    def test_every_route_is_room_scoped_except_the_three_published_ones(self):
        unscoped = {
            route.path
            for route in feature.router.routes
            if isinstance(route, APIRoute)
            and "{room_id}" not in route.path
            and route.path
            not in {
                f"{PREFIX}/vocabulary",
                f"{PREFIX}/inferences",
                f"{PREFIX}/decisions/{{decision_id}}",
            }
        }
        assert unscoped == set()

    def test_it_maps_only_its_own_error_hierarchy(self):
        assert set(feature.EXCEPTION_HANDLERS) == {SeriesRefused}
        for mapped in feature.EXCEPTION_HANDLERS:
            assert mapped.__module__.startswith("dsr.nurture_series")

    def test_the_not_found_types_are_part_of_the_hierarchy(self):
        assert issubclass(SeriesNotFound, SeriesRefused)
        assert issubclass(RunNotFound, SeriesRefused)


# --------------------------------------------------------------------------- #
# Series lifecycle
# --------------------------------------------------------------------------- #


class TestSeriesLifecycle:
    def test_a_new_series_is_a_draft(self, engine, room_id):
        series = make_series(engine, room_id)
        assert series["state"] == vocab.STATE_DRAFT
        assert series["entry_rule"]["role"] == "entry"

    def test_going_live_flips_the_switch(self, engine, room_id):
        series = make_series(engine, room_id)
        live = go_live(engine, series["id"])
        assert live["state"] == vocab.STATE_LIVE
        assert live["went_live_at"]

    def test_going_live_twice_is_refused(self, engine, room_id):
        series = make_series(engine, room_id)
        go_live(engine, series["id"])
        with pytest.raises(SeriesRefused) as caught:
            go_live(engine, series["id"])
        assert caught.value.code == "series_already_live"
        assert caught.value.status == 409

    def test_an_unknown_series_is_a_not_found(self, engine):
        with pytest.raises(SeriesNotFound):
            engine.series_view("wf104_series_absent")

    def test_patching_revalidates_the_blocks(self, engine, room_id):
        series = make_series(engine, room_id)
        with pytest.raises(SeriesRefused) as caught:
            engine.update_series(
                series["id"],
                {"blocks": [{"kind": "webhook"}]},
                source=feature.SERIES_PATCH_SOURCE,
                actor="dana",
            )
        assert caught.value.code == "unknown_block_kind"

    def test_patching_a_name(self, engine, room_id):
        series = make_series(engine, room_id)
        edited = engine.update_series(
            series["id"], {"name": "Renamed"}, source=feature.SERIES_PATCH_SOURCE, actor="dana"
        )
        assert edited["name"] == "Renamed"

    def test_patching_the_state_live_goes_live(self, engine, room_id):
        series = make_series(engine, room_id)
        edited = engine.update_series(
            series["id"], {"state": "live"}, source=feature.SERIES_PATCH_SOURCE, actor="dana"
        )
        assert edited["state"] == vocab.STATE_LIVE

    def test_deleting_is_soft(self, engine, room_id, store: RecordStore):
        series = make_series(engine, room_id)
        deleted = engine.delete_series(
            series["id"], source=feature.SERIES_DELETE_SOURCE, actor="dana"
        )
        assert deleted["hard"] is False
        assert store.get(series["id"]) is None
        with pytest.raises(SeriesNotFound):
            engine.series_view(series["id"])

    def test_listing_filters_by_state(self, engine, room_id):
        first = make_series(engine, room_id)
        make_series(engine, room_id, name="Second")
        go_live(engine, first["id"])
        assert len(engine.list_series(room_id)) == 2
        live = engine.list_series(room_id, state=vocab.STATE_LIVE)
        assert [row["id"] for row in live] == [first["id"]]

    def test_an_unknown_state_filter_is_refused(self, engine, room_id):
        with pytest.raises(SeriesRefused):
            engine.list_series(room_id, state="paused")


# --------------------------------------------------------------------------- #
# The audience preview
# --------------------------------------------------------------------------- #


class TestPreview:
    def test_it_counts_who_enters(self, engine, room_id):
        series = make_series(engine, room_id)
        preview = engine.preview(
            room_id,
            series["id"],
            {"audience": [buyer("c1"), buyer("c2", attributes={"plan": "starter"})]},
        )
        assert preview["audience_size"] == 2
        assert preview["entered"] == 1
        assert preview["excluded"] == 1

    def test_it_says_why_a_buyer_is_excluded(self, engine, room_id):
        series = make_series(engine, room_id)
        preview = engine.preview(
            room_id,
            series["id"],
            {"audience": [buyer("c2", attributes={"plan": "starter", "email": "x@y.z"})]},
        )
        assert preview["contacts"][0]["entered"] is False
        assert preview["contacts"][0]["reasons"]

    def test_it_writes_nothing(self, engine, room_id, store: RecordStore):
        series = make_series(engine, room_id)
        engine.preview(room_id, series["id"], {"audience": [buyer("c1")]})
        assert store.list(vocab.RUN_COLLECTION, limit=10) == []


# --------------------------------------------------------------------------- #
# The poll
# --------------------------------------------------------------------------- #


class TestEvaluate:
    def test_a_room_with_no_live_series_reports_that(self, engine, room_id):
        result = sweep(engine, room_id, [buyer("c1")])
        assert result["fired_count"] == 0
        assert {row["reason"] for row in result["skipped"]} == {"no_live_series"}

    def test_a_draft_series_fires_nothing(self, engine, room_id):
        make_series(engine, room_id)
        result = sweep(engine, room_id, [buyer("c1")])
        assert result["fired_count"] == 0

    def test_a_live_series_creates_a_run_for_a_matching_buyer(self, engine, room_id):
        series = make_series(engine, room_id)
        go_live(engine, series["id"])
        result = sweep(engine, room_id, [buyer("c1")])
        assert result["fired_count"] == 1
        run = result["fired"][0]
        assert run["cursor"] == 1
        assert run["state"] == vocab.RUN_RUNNING

    def test_an_unmatched_buyer_is_skipped_with_its_reason(self, engine, room_id):
        series = make_series(engine, room_id)
        go_live(engine, series["id"])
        result = sweep(engine, room_id, [buyer("c2", attributes={"plan": "starter"})])
        assert result["fired_count"] == 0
        assert {row["reason"] for row in result["skipped"]} == {"entry_filters_unmatched"}

    def test_a_second_poll_does_not_enter_the_same_buyer_twice(self, engine, room_id):
        series = make_series(engine, room_id)
        go_live(engine, series["id"])
        sweep(engine, room_id, [buyer("c1")])
        again = sweep(engine, room_id, [buyer("c1")])
        assert again["fired_count"] == 0
        assert {row["reason"] for row in again["skipped"]} == {"already_entered"}

    def test_the_poll_reports_its_interval(self, engine, room_id):
        result = sweep(engine, room_id, [])
        assert result["poll"]["interval_seconds"] == vocab.POLL_INTERVAL_SECONDS


# --------------------------------------------------------------------------- #
# Content blocks, the send window and the fallback
# --------------------------------------------------------------------------- #


class TestContentDispatch:
    def test_an_online_buyer_is_delivered(self, engine, room_id):
        entered = enter(engine, room_id, [buyer("c1")])
        run = engine.advance(
            entered["run_id"], {}, source=feature.ADVANCE_SOURCE, actor="scheduler"
        )
        dispatches = run["dispatches"]
        assert dispatches[0]["state"] == "delivered"
        assert dispatches[0]["is_fallback"] is False
        assert dispatches[0]["sent_by_this_product"] is False
        assert dispatches[0]["channel_send_is_recorded"] is True
        assert run["waiting_kind"] == "wait"

    def test_an_offline_buyer_retries_then_falls_back(self, engine, room_id):
        entered = enter(engine, room_id, [buyer("c1", online=False)])
        run = engine.advance(
            entered["run_id"], {}, source=feature.ADVANCE_SOURCE, actor="scheduler"
        )
        assert run["waiting_kind"] == "send_window"
        assert run["dispatches"][0]["state"] == "retrying"

        resolved = at_engine(engine.store, THIRTY_DAYS).resolve(
            entered["run_id"], {}, source=feature.RESOLVE_SOURCE, actor="scheduler"
        )
        fallbacks = [row for row in resolved["dispatches"] if row["is_fallback"]]
        assert len(fallbacks) == 1
        assert fallbacks[0]["channel"] == "email"
        assert fallbacks[0]["reason"] == vocab.OFFLINE_FALLBACK_REASON

    def test_an_offline_buyer_inside_the_window_cannot_resolve(self, engine, room_id):
        entered = enter(engine, room_id, [buyer("c1", online=False)])
        engine.advance(entered["run_id"], {}, source=feature.ADVANCE_SOURCE, actor="scheduler")
        with pytest.raises(SeriesRefused) as caught:
            at_engine(engine.store, HOUR).resolve(
                entered["run_id"], {}, source=feature.RESOLVE_SOURCE, actor="scheduler"
            )
        assert caught.value.code == "wait_not_elapsed"

    def test_a_buyer_who_returns_inside_the_window_is_delivered_not_fallen_back(
        self, engine, room_id
    ):
        entered = enter(engine, room_id, [buyer("c1", online=False)])
        engine.advance(entered["run_id"], {}, source=feature.ADVANCE_SOURCE, actor="scheduler")
        # The buyer comes online before the window elapses.
        run_record = engine.store.get(entered["run_id"])
        contact = dict(run_record["data"]["contact"])
        contact["online"] = True
        engine.store.update(
            entered["run_id"],
            {"contact": contact},
            source=feature.ADVANCE_SOURCE,
            actor="scheduler",
        )
        resolved = at_engine(engine.store, HOUR).resolve(
            entered["run_id"], {}, source=feature.RESOLVE_SOURCE, actor="scheduler"
        )
        assert not any(row["is_fallback"] for row in resolved["dispatches"])
        assert resolved["dispatches"][-1]["state"] == "delivered"

    def test_a_block_without_a_fallback_records_not_delivered(self, engine, room_id):
        blocks = [dict(BLOCKS[0]), dict(BLOCKS[1], fallback_enabled=False)]
        series = make_series(engine, room_id, blocks=blocks)
        go_live(engine, series["id"])
        result = sweep(engine, room_id, [buyer("c1", online=False)])
        run = engine.advance(
            result["fired"][0]["id"], {}, source=feature.ADVANCE_SOURCE, actor="scheduler"
        )
        assert run["dispatches"][0]["state"] == "not_delivered"
        assert run["state"] == vocab.RUN_FINISHED


# --------------------------------------------------------------------------- #
# Wait blocks and rule branches
# --------------------------------------------------------------------------- #


class TestWaitAndRule:
    def _entered(self, engine, room_id, audience=None):
        return enter(engine, room_id, audience or [buyer("c1")])

    def test_a_wait_holds_until_its_window_elapses(self, engine, room_id):
        entered = self._entered(engine, room_id)
        engine.advance(entered["run_id"], {}, source=feature.ADVANCE_SOURCE, actor="scheduler")
        with pytest.raises(SeriesRefused) as caught:
            at_engine(engine.store, MINUTE).resolve(
                entered["run_id"], {}, source=feature.RESOLVE_SOURCE, actor="scheduler"
            )
        assert caught.value.code == "wait_not_elapsed"

    def test_resolving_the_wait_reaches_the_rule_branch(self, engine, room_id):
        entered = self._entered(engine, room_id)
        engine.advance(entered["run_id"], {}, source=feature.ADVANCE_SOURCE, actor="scheduler")
        resolved = at_engine(engine.store, DAY).resolve(
            entered["run_id"], {}, source=feature.RESOLVE_SOURCE, actor="scheduler"
        )
        assert resolved["waiting_kind"] == "rule"

    def test_a_rule_for_a_click_resolves_early_when_the_click_arrives(self, engine, room_id):
        entered = self._entered(engine, room_id)
        engine.advance(entered["run_id"], {}, source=feature.ADVANCE_SOURCE, actor="scheduler")
        at_engine(engine.store, DAY).resolve(
            entered["run_id"], {}, source=feature.RESOLVE_SOURCE, actor="scheduler"
        )
        at_engine(engine.store, DAY + 60).record_event(
            entered["run_id"], {"event": "click"}, source=feature.EVENT_SOURCE, actor="buyer"
        )
        run = at_engine(engine.store, DAY + 60).resolve(
            entered["run_id"], {}, source=feature.RESOLVE_SOURCE, actor="scheduler"
        )
        assert run["state"] == vocab.RUN_FINISHED
        assert run["tags"] == ["nurtured"]

    def test_a_rule_for_with_no_click_exits_once_the_window_elapses(self, engine, room_id):
        entered = self._entered(engine, room_id)
        engine.advance(entered["run_id"], {}, source=feature.ADVANCE_SOURCE, actor="scheduler")
        at_engine(engine.store, DAY).resolve(
            entered["run_id"], {}, source=feature.RESOLVE_SOURCE, actor="scheduler"
        )
        run = at_engine(engine.store, DAY + THREE_DAYS).resolve(
            entered["run_id"], {}, source=feature.RESOLVE_SOURCE, actor="scheduler"
        )
        assert run["state"] == vocab.RUN_EXITED

    def test_a_rule_for_cannot_resolve_before_it_matches_or_elapses(self, engine, room_id):
        entered = self._entered(engine, room_id)
        engine.advance(entered["run_id"], {}, source=feature.ADVANCE_SOURCE, actor="scheduler")
        at_engine(engine.store, DAY).resolve(
            entered["run_id"], {}, source=feature.RESOLVE_SOURCE, actor="scheduler"
        )
        with pytest.raises(SeriesRefused) as caught:
            at_engine(engine.store, DAY + HOUR).resolve(
                entered["run_id"], {}, source=feature.RESOLVE_SOURCE, actor="scheduler"
            )
        assert caught.value.code == "wait_not_elapsed"

    def test_a_rule_once_exits_immediately_without_a_match(self, engine, room_id):
        blocks = [
            dict(BLOCKS[0]),
            dict(BLOCKS[3], wait_mode="once", match_within_seconds=None),
            dict(BLOCKS[4]),
        ]
        series = make_series(engine, room_id, blocks=blocks)
        go_live(engine, series["id"])
        result = sweep(engine, room_id, [buyer("c1")])
        run = engine.advance(
            result["fired"][0]["id"], {}, source=feature.ADVANCE_SOURCE, actor="scheduler"
        )
        assert run["state"] == vocab.RUN_EXITED

    def test_advancing_a_waiting_run_is_refused(self, engine, room_id):
        entered = self._entered(engine, room_id)
        engine.advance(entered["run_id"], {}, source=feature.ADVANCE_SOURCE, actor="scheduler")
        with pytest.raises(SeriesRefused) as caught:
            engine.advance(entered["run_id"], {}, source=feature.ADVANCE_SOURCE, actor="scheduler")
        assert caught.value.code == "run_not_advancing"

    def test_advancing_a_finished_run_is_refused(self, engine, room_id):
        entered = self._entered(engine, room_id)
        engine.advance(entered["run_id"], {}, source=feature.ADVANCE_SOURCE, actor="scheduler")
        at_engine(engine.store, DAY).resolve(
            entered["run_id"], {}, source=feature.RESOLVE_SOURCE, actor="scheduler"
        )
        at_engine(engine.store, DAY + THREE_DAYS).resolve(
            entered["run_id"], {}, source=feature.RESOLVE_SOURCE, actor="scheduler"
        )
        with pytest.raises(SeriesRefused) as caught:
            engine.advance(entered["run_id"], {}, source=feature.ADVANCE_SOURCE, actor="scheduler")
        assert caught.value.code == "run_not_advancing"

    def test_resolving_a_running_run_is_refused(self, engine, room_id):
        entered = self._entered(engine, room_id)
        with pytest.raises(SeriesRefused) as caught:
            engine.resolve(entered["run_id"], {}, source=feature.RESOLVE_SOURCE, actor="scheduler")
        assert caught.value.code == "run_not_waiting"

    def test_an_unknown_run_is_a_not_found(self, engine):
        with pytest.raises(RunNotFound):
            engine.run_view("wf104_run_absent")


# --------------------------------------------------------------------------- #
# Engagement events
# --------------------------------------------------------------------------- #


class TestEvents:
    def test_a_click_is_recorded_against_the_buyer(self, engine, room_id):
        entered = enter(engine, room_id, [buyer("c1")])
        row = engine.record_event(
            entered["run_id"], {"event": "click"}, source=feature.EVENT_SOURCE, actor="buyer"
        )
        assert row["event"] == "click"
        assert row["contact_id"] == "c1"
        assert row["target"] == "contact"

    def test_an_unknown_event_is_refused(self, engine, room_id):
        entered = enter(engine, room_id, [buyer("c1")])
        with pytest.raises(SeriesRefused) as caught:
            engine.record_event(
                entered["run_id"],
                {"event": "unsubscribed"},
                source=feature.EVENT_SOURCE,
                actor="buyer",
            )
        assert caught.value.code == "unknown_engagement_event"

    def test_a_series_exit_event_ends_the_run(self, engine, room_id):
        entered = enter(engine, room_id, [buyer("c1")])
        engine.record_event(
            entered["run_id"], {"event": "series_exit"}, source=feature.EVENT_SOURCE, actor="buyer"
        )
        assert engine.run_view(entered["run_id"])["state"] == vocab.RUN_EXITED


# --------------------------------------------------------------------------- #
# Export and summary
# --------------------------------------------------------------------------- #


class TestExport:
    def test_it_exposes_the_researched_four_fields(self, engine, room_id):
        series = make_series(engine, room_id)
        export = engine.export_series(series["id"])
        assert set(vocab.EXPORT_FIELDS) <= set(export)
        assert export["targeting"]["filters"]
        assert export["embedded_rules"]

    def test_the_attributes_are_the_fields_the_series_reads(self, engine, room_id):
        series = make_series(engine, room_id)
        assert engine.export_series(series["id"])["attributes"] == ["plan"]

    def test_an_unknown_series_is_a_not_found(self, engine):
        with pytest.raises(SeriesNotFound):
            engine.export_series("wf104_series_absent")


class TestSummary:
    def test_it_counts_series_runs_and_fallbacks(self, engine, room_id):
        entered = enter(engine, room_id, [buyer("c1", online=False)])
        engine.advance(entered["run_id"], {}, source=feature.ADVANCE_SOURCE, actor="scheduler")
        at_engine(engine.store, THIRTY_DAYS).resolve(
            entered["run_id"], {}, source=feature.RESOLVE_SOURCE, actor="scheduler"
        )
        summary = engine.summary(room_id)
        assert summary["series_by_state"][vocab.STATE_LIVE] == 1
        assert summary["runs"] == 1
        assert summary["fallbacks"] == 1

    def test_an_empty_room_summarises_to_zeroes(self, engine, room_id):
        summary = engine.summary(room_id)
        assert summary["series"] == 0
        assert summary["runs"] == 0
        assert summary["due"] == 0


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


class TestVocabularyAndInferenceRoutes:
    def test_the_vocabulary_route_needs_no_room(self, client):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body["block_kinds"] == list(vocab.BLOCK_KINDS)
        assert body["poll"]["interval_seconds"] == vocab.POLL_INTERVAL_SECONDS
        assert body["series_failures"]["series_name_required"]["status"] == 400

    def test_the_inferences_route_names_the_jev_audit(self, client):
        body = client.get(f"{PREFIX}/inferences").json()
        assert body["ticket"] == "WF-104"
        assert body["jev_audits"]["audience-mechanism"].startswith("jev-")

    def test_one_decision_reads_by_id(self, client):
        assert client.get(f"{PREFIX}/decisions/audience-mechanism").json()["id"] == (
            "audience-mechanism"
        )

    def test_an_unknown_decision_is_a_404(self, client):
        response = client.get(f"{PREFIX}/decisions/no-such-decision")
        assert response.status_code == 404
        assert response.json()["error"] == "decision_not_found"


class TestSeriesRoutes:
    def test_creating_a_series(self, client, db: AuditedDatabase):
        room_id = RecordStore(db).create("room", {"name": "R"}, actor="dana")["id"]
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/series", json={"name": "N", "blocks": BLOCKS}
        )
        assert response.status_code == 201
        assert response.json()["state"] == vocab.STATE_DRAFT

    def test_a_series_with_no_name_is_a_400(self, client, db):
        room_id = RecordStore(db).create("room", {"name": "R"}, actor="dana")["id"]
        response = client.post(f"{PREFIX}/rooms/{room_id}/series", json={"blocks": BLOCKS})
        assert response.status_code == 400
        assert response.json()["error"] == "series_name_required"

    def test_an_unknown_block_kind_is_a_400(self, client, db):
        room_id = RecordStore(db).create("room", {"name": "R"}, actor="dana")["id"]
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/series", json={"name": "N", "blocks": [{"kind": "webhook"}]}
        )
        assert response.status_code == 400
        assert response.json()["error"] == "unknown_block_kind"

    def test_going_live_then_going_live_again(self, client, db):
        room_id = RecordStore(db).create("room", {"name": "R"}, actor="dana")["id"]
        series = client.post(
            f"{PREFIX}/rooms/{room_id}/series", json={"name": "N", "blocks": BLOCKS}
        ).json()
        first = client.post(f"{PREFIX}/rooms/{room_id}/series/{series['id']}/go-live")
        assert first.status_code == 200
        second = client.post(f"{PREFIX}/rooms/{room_id}/series/{series['id']}/go-live")
        assert second.status_code == 409
        assert second.json()["error"] == "series_already_live"

    def test_an_unknown_series_is_a_404(self, client, db):
        room_id = RecordStore(db).create("room", {"name": "R"}, actor="dana")["id"]
        response = client.get(f"{PREFIX}/rooms/{room_id}/series/wf104_series_absent")
        assert response.status_code == 404
        assert response.json()["error"] == "series_not_found"

    def test_listing_and_reading(self, client, db):
        room_id = RecordStore(db).create("room", {"name": "R"}, actor="dana")["id"]
        created = client.post(
            f"{PREFIX}/rooms/{room_id}/series", json={"name": "N", "blocks": BLOCKS}
        ).json()
        listed = client.get(f"{PREFIX}/rooms/{room_id}/series").json()
        assert listed["count"] == 1
        read = client.get(f"{PREFIX}/rooms/{room_id}/series/{created['id']}").json()
        assert read["id"] == created["id"]

    def test_patching_and_deleting(self, client, db):
        room_id = RecordStore(db).create("room", {"name": "R"}, actor="dana")["id"]
        created = client.post(
            f"{PREFIX}/rooms/{room_id}/series", json={"name": "N", "blocks": BLOCKS}
        ).json()
        patched = client.patch(
            f"{PREFIX}/rooms/{room_id}/series/{created['id']}", json={"name": "Renamed"}
        )
        assert patched.json()["name"] == "Renamed"
        deleted = client.delete(f"{PREFIX}/rooms/{room_id}/series/{created['id']}")
        assert deleted.json()["deleted"] is True


class TestRunRoutesThroughHTTP:
    def test_preview(self, client, db):
        room_id = RecordStore(db).create("room", {"name": "R"}, actor="dana")["id"]
        series = client.post(
            f"{PREFIX}/rooms/{room_id}/series", json={"name": "N", "blocks": BLOCKS}
        ).json()
        body = client.post(
            f"{PREFIX}/rooms/{room_id}/series/{series['id']}/preview",
            json={"audience": [buyer("c1")]},
        ).json()
        assert body["entered"] == 1

    def test_evaluate_and_walk_a_run(self, client, db):
        room_id = RecordStore(db).create("room", {"name": "R"}, actor="dana")["id"]
        series = client.post(
            f"{PREFIX}/rooms/{room_id}/series", json={"name": "N", "blocks": BLOCKS}
        ).json()
        client.post(f"{PREFIX}/rooms/{room_id}/series/{series['id']}/go-live")
        sweep_body = {
            "audience": [buyer("c1")],
            "now": rules.to_iso(CLOCK),
        }
        result = client.post(f"{PREFIX}/rooms/{room_id}/evaluate", json=sweep_body).json()
        assert result["fired_count"] == 1
        run_id = result["fired"][0]["id"]
        advanced = client.post(f"{PREFIX}/rooms/{room_id}/runs/{run_id}/advance", json={})
        assert advanced.status_code == 200
        assert advanced.json()["dispatches"][0]["state"] == "delivered"

        listed = client.get(f"{PREFIX}/rooms/{room_id}/runs").json()
        assert listed["count"] == 1
        read = client.get(f"{PREFIX}/rooms/{room_id}/runs/{run_id}").json()
        assert read["sent_by_this_product"] is False

    def test_an_unknown_run_is_a_404(self, client, db):
        room_id = RecordStore(db).create("room", {"name": "R"}, actor="dana")["id"]
        response = client.get(f"{PREFIX}/rooms/{room_id}/runs/wf104_run_absent")
        assert response.status_code == 404
        assert response.json()["error"] == "run_not_found"

    def test_the_summary_route(self, client, db):
        room_id = RecordStore(db).create("room", {"name": "R"}, actor="dana")["id"]
        body = client.get(f"{PREFIX}/rooms/{room_id}/summary").json()
        assert body["room_id"] == room_id
        assert body["series"] == 0

    def test_the_export_route(self, client, db):
        room_id = RecordStore(db).create("room", {"name": "R"}, actor="dana")["id"]
        series = client.post(
            f"{PREFIX}/rooms/{room_id}/series", json={"name": "N", "blocks": BLOCKS}
        ).json()
        body = client.get(f"{PREFIX}/rooms/{room_id}/series/{series['id']}/export").json()
        assert body["embedded_rules"]


class TestHostIntegration:
    def test_the_feature_is_mounted_by_discovery(self, client):
        body = client.get("/api/features").json()
        found = [entry for entry in body["features"] if entry["id"] == feature.FEATURE["id"]]
        assert found, sorted(entry["id"] for entry in body["features"])
        assert body["failed_count"] == 0

    def test_no_route_of_this_feature_collides_with_another(self, client):
        body = client.get("/api/features").json()
        seen: dict[tuple[str, str], str] = {}
        for entry in body["features"]:
            for route in entry["routes"]:
                for method in route["methods"]:
                    key = (method, route["path"])
                    assert key not in seen, f"{key} claimed by {seen[key]} and {entry['id']}"
                    seen[key] = entry["id"]


# --------------------------------------------------------------------------- #
# The audit trail
# --------------------------------------------------------------------------- #


class TestAuditSources:
    def _audit_sources(self, target: Any) -> list[str]:
        rows = (
            target.db.audit(limit=5000)
            if isinstance(target, RecordStore)
            else target.audit(limit=5000)
        )
        return [str(row.get("source")) for row in rows if row.get("source")]

    def _mounted(self) -> list[tuple[str, str]]:
        return [
            (method, route.path)
            for route in feature.router.routes
            if isinstance(route, APIRoute)
            for method in route.methods
        ]

    def test_every_seed_source_names_a_mounted_route(self):
        placeholder = re.compile(r"\{[a-z_]+\}")
        mounted = self._mounted()
        for source in feature.SEED_SOURCES:
            method, _, path = source.partition(" ")
            wanted = placeholder.sub("[^/]+", path)
            assert any(m == method and re.fullmatch(wanted, p) for m, p in mounted), source

    def test_every_source_constant_is_built_from_the_router_prefix(self):
        for source in feature.SEED_SOURCES:
            assert PREFIX in source, source

    def test_an_http_write_names_its_own_route(self, client, db: AuditedDatabase):
        room_id = RecordStore(db).create("room", {"name": "R"}, actor="dana")["id"]
        client.post(f"{PREFIX}/rooms/{room_id}/series", json={"name": "N", "blocks": BLOCKS})
        assert feature.SERIES_CREATE_SOURCE in self._audit_sources(db)

    def test_every_recorded_source_names_a_mounted_route(self, engine, room_id, store: RecordStore):
        entered = enter(engine, room_id, [buyer("c1", online=False)])
        engine.advance(entered["run_id"], {}, source=feature.ADVANCE_SOURCE, actor="scheduler")
        at_engine(engine.store, THIRTY_DAYS).resolve(
            entered["run_id"], {}, source=feature.RESOLVE_SOURCE, actor="scheduler"
        )
        placeholder = re.compile(r"\{[a-z_]+\}")
        mounted = self._mounted()
        for source in self._audit_sources(store):
            method, _, path = source.partition(" ")
            wanted = placeholder.sub("[^/]+", path)
            assert any(m == method and re.fullmatch(wanted, p) for m, p in mounted), (
                f"{source} names no mounted route"
            )

    def test_a_soft_delete_is_audited(self, engine, room_id, store: RecordStore):
        series = make_series(engine, room_id)
        engine.delete_series(series["id"], source=feature.SERIES_DELETE_SOURCE, actor="dana")
        assert feature.SERIES_DELETE_SOURCE in self._audit_sources(store)


# --------------------------------------------------------------------------- #
# The seed
# --------------------------------------------------------------------------- #


class TestSeed:
    def test_it_returns_a_string(self, db: AuditedDatabase):
        room_id = RecordStore(db).create("room", {"name": "R"}, actor="dana")["id"]
        summary = feature.seed(db, {"room_ids": [(room_id, "R")], "now": CLOCK, "rng": None})
        assert isinstance(summary, str)
        assert summary

    def test_the_returned_string_is_cp1252_encodable(self, db: AuditedDatabase):
        room_id = RecordStore(db).create("room", {"name": "R"}, actor="dana")["id"]
        summary = feature.seed(db, {"room_ids": [(room_id, "R")], "now": CLOCK, "rng": None})
        summary.encode("cp1252")

    def test_it_names_the_states_it_created(self, db: AuditedDatabase):
        room_id = RecordStore(db).create("room", {"name": "R"}, actor="dana")["id"]
        summary = feature.seed(db, {"room_ids": [(room_id, "R")], "now": CLOCK, "rng": None})
        for phrase in (
            "live series",
            "entered",
            "excluded by the entry rule",
            "fallback to email",
            "left the path",
            "click",
        ):
            assert phrase in summary, phrase

    def test_it_produces_rows_of_every_kind(self, db: AuditedDatabase):
        room_id = RecordStore(db).create("room", {"name": "R"}, actor="dana")["id"]
        feature.seed(db, {"room_ids": [(room_id, "R")], "now": CLOCK, "rng": None})
        store = RecordStore(db)
        for collection, minimum in (
            (vocab.SERIES_COLLECTION, 1),
            (vocab.RUN_COLLECTION, 3),
            (vocab.DISPATCH_COLLECTION, 4),
            (vocab.ENGAGEMENT_COLLECTION, 1),
            (vocab.ACTIVITY_COLLECTION, 1),
        ):
            assert len(store.list(collection, room_id=room_id, limit=200)) >= minimum, collection

    def test_it_seeds_the_states_that_are_not_successes(self, db: AuditedDatabase):
        """A demo of only green teaches a reviewer nothing."""

        room_id = RecordStore(db).create("room", {"name": "R"}, actor="dana")["id"]
        feature.seed(db, {"room_ids": [(room_id, "R")], "now": CLOCK, "rng": None})
        store = RecordStore(db)
        runs = [row["data"] for row in store.list(vocab.RUN_COLLECTION, room_id=room_id, limit=200)]
        states = {row["state"] for row in runs}
        assert vocab.RUN_FINISHED in states
        assert vocab.RUN_EXITED in states
        dispatches = [
            row["data"] for row in store.list(vocab.DISPATCH_COLLECTION, room_id=room_id, limit=200)
        ]
        assert any(row["is_fallback"] for row in dispatches)

    def test_it_runs_with_no_room_at_all(self, db: AuditedDatabase):
        summary = feature.seed(db, {"room_ids": [], "now": CLOCK, "rng": None})
        assert "room created" in summary

    def test_a_second_run_in_the_same_room_still_works(self, db: AuditedDatabase):
        room_id = RecordStore(db).create("room", {"name": "R"}, actor="dana")["id"]
        first = feature.seed(db, {"room_ids": [(room_id, "R")], "now": CLOCK, "rng": None})
        second = feature.seed(db, {"room_ids": [(room_id, "R")], "now": CLOCK, "rng": None})
        assert "2 live series of 2" in second
        assert "1 live series of 1" in first

    def test_every_seeded_instant_derives_from_the_clock(self, db: AuditedDatabase):
        room_id = RecordStore(db).create("room", {"name": "R"}, actor="dana")["id"]
        feature.seed(db, {"room_ids": [(room_id, "R")], "now": CLOCK, "rng": None})
        store = RecordStore(db)
        runs = [row["data"] for row in store.list(vocab.RUN_COLLECTION, room_id=room_id, limit=200)]
        entered = [rules.parse_instant(row["entered_at"]) for row in runs]
        assert entered and all(moment is not None for moment in entered)
        for moment in entered:
            assert abs(moment - CLOCK) < timedelta(days=1)
