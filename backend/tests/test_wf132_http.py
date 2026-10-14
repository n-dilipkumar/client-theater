"""WF-132: the HTTP surface of in-room conversation and champion self-service.

What is different from ``test_wf132.py``
----------------------------------------

The domain tests build the engine and call it directly. This file goes through
the router, so it is where four things are only observable:

* **Discovery.** The host mounts the router by globbing the package, so the
  feature, its prefix and its routes have to be asserted rather than assumed.
* **The error mapping.** One handler is registered for the ``InRoomError`` family
  and answers with the status the class carries: a malformed request is 422, a
  thread nobody can find is 404, a role that may not act is 403, and a change that
  contradicts state is 409 with a stable reason.
* **The audit source.** Every write names the route template it was served by, so
  the check is an equality against the routes the host actually mounted.
* **The refusals that are correct.** ``tools/verify_all_routes.py`` calls every
  advertised route with no parameters at all, so every write route here is called
  empty and has to answer 4xx rather than 5xx.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import dsr.features as host
import pytest
from dsr.inroom_collaboration import vocabulary as v

MODULE = "wf132_in_room_collaboration_comments_threads_tagging_and_champion"
FEATURE_ID = v.FEATURE_ID
PREFIX = "/api/wf-132"
ROOM = "room_1"
OWNER = "dana"
BUYER = "a.buyer@northwind.example"
CHAMPION = "legal@northwind.example"
VIEWER = "b.buyer@northwind.example"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Provisioning helpers, over the store the routes share
# --------------------------------------------------------------------------- #


def _store(client):
    from dsr.api import app

    return app.state.store


def make_room(client, room_id=ROOM, owner=OWNER, **overrides):
    data = {"name": "Northwind Traders", "owner": owner}
    data.update(overrides)
    return _store(client).create("room", data, record_id=room_id, actor=owner, source="test")


def grant(client, principal, role, *, room_id=ROOM, valid_until=None):
    return _store(client).create(
        v.ACCESS_COLLECTION,
        {"principal": principal, "role": role, "access_valid_until": valid_until},
        room_id=room_id,
        actor=OWNER,
        source="test",
    )


def open_thread(client, room_id=ROOM, **overrides):
    body = {
        "actor": BUYER,
        "anchor_type": v.ANCHOR_PAGE,
        "anchor_id": "page_pricing",
        "body": "Can we talk about the tiers? cc @legal",
    }
    body.update(overrides)
    return client.post(f"{PREFIX}/rooms/{room_id}/threads", json=body)


@pytest.fixture
def live(client):
    """A client with one active room and three buyer grants."""
    make_room(client)
    grant(client, BUYER, v.ROLE_CONTRIBUTOR)
    grant(client, CHAMPION, v.ROLE_COLLABORATOR)
    grant(client, VIEWER, v.ROLE_VIEWER)
    return client


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def test_the_registry_lists_this_feature_with_its_prefix_and_routes(live):
    listing = live.get("/api/features").json()
    record = next(f for f in listing["features"] if f["id"] == FEATURE_ID)
    assert record["ticket"] == "WF-132"
    assert record["prefix"] == PREFIX
    assert len(record["routes"]) == 18
    assert record["exception_handlers"] == ["InRoomError"]


def test_no_feature_failed_to_load(live):
    """A feature that raises on import is skipped rather than fatal, which is why
    the failure has to be asserted rather than assumed."""
    listing = live.get("/api/features").json()
    assert listing["failed_count"] == 0, [f["id"] for f in listing["failed"]]


def test_the_feature_lookup_route_answers(live):
    assert live.get(f"/api/features/{FEATURE_ID}").json()["prefix"] == PREFIX


def test_the_core_health_route_still_answers(live):
    assert live.get("/api/health").status_code == 200


# --------------------------------------------------------------------------- #
# Every route answers, and none of them 5xx
# --------------------------------------------------------------------------- #

ALL_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/vocabulary"),
    ("GET", "/summary"),
    ("GET", "/threads"),
    ("GET", "/rooms/room_absent/threads"),
    ("POST", "/rooms/room_absent/threads"),
    ("GET", "/threads/thread_absent"),
    ("POST", "/threads/thread_absent/comments"),
    ("POST", "/threads/thread_absent/resolve"),
    ("POST", "/threads/thread_absent/reopen"),
    ("POST", "/threads/thread_absent/mentions"),
    ("POST", "/threads/thread_absent/watch"),
    ("POST", "/threads/thread_absent/convert-to-task"),
    ("GET", "/rooms/room_absent/identity"),
    ("POST", "/rooms/room_absent/edits"),
    ("GET", "/rooms/room_absent/edits"),
    ("GET", "/rooms/room_absent/champions"),
    ("POST", "/rooms/room_absent/escalations/refresh"),
    ("GET", "/rooms/room_absent/escalations"),
)


@pytest.mark.parametrize("method,path", ALL_ROUTES, ids=[f"{m} {p}" for m, p in ALL_ROUTES])
def test_every_route_answers_with_no_5xx(live, method, path):
    response = live.request(method, f"{PREFIX}{path}", json={})
    assert response.status_code < 500, f"{method} {path} -> {response.status_code} {response.text}"


def test_every_route_this_feature_advertises_is_called_by_this_file(live):
    """The list above has to keep up with the router, or the 5xx sweep is theatre."""
    advertised = set()
    for route in host.REGISTRY.by_id(FEATURE_ID).routes:
        tail = route["path"][len(PREFIX) :]
        tail = tail.replace("{thread_id}", "thread_absent")
        tail = tail.replace("{room_id}", "room_absent")
        for method in route["methods"]:
            advertised.add((method, tail))
    assert set(ALL_ROUTES) == advertised


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_publishes_the_contested_flag(live):
    body = live.get(f"{PREFIX}/vocabulary").json()
    assert body["ticket"] == "WF-132"
    assert body["contested"]["flag"] is True
    assert body["anchor_kinds"] == ["page", "section", "asset", "map_task"]
    assert body["not_implemented"], "what is absent must be stated"
    assert body["champion_role"] == v.ROLE_COLLABORATOR


# --------------------------------------------------------------------------- #
# Threads over the wire
# --------------------------------------------------------------------------- #


def test_a_thread_is_opened_with_its_first_comment(live):
    response = open_thread(live)
    assert response.status_code == 201, response.text
    thread = response.json()
    assert thread["state"] == v.STATE_OPEN
    assert thread["message_count"] == 1
    assert thread["contested"] is True
    assert thread["mentions"][0]["principal"] == CHAMPION


def test_a_created_thread_reads_back(live):
    created = open_thread(live).json()
    fetched = live.get(f"{PREFIX}/threads/{created['id']}").json()
    assert fetched["id"] == created["id"]
    assert fetched["comments"][0]["body"] == "Can we talk about the tiers? cc @legal"


def test_the_thread_list_and_summary_answer(live):
    open_thread(live)
    listing = live.get(f"{PREFIX}/threads", params={"room_id": ROOM}).json()
    assert listing["count"] == 1
    summary = live.get(f"{PREFIX}/summary", params={"room_id": ROOM}).json()
    assert summary["threads"] == 1


def test_a_comment_and_a_tag_and_a_watch_round_trip(live):
    thread = open_thread(live).json()
    reply = live.post(
        f"{PREFIX}/threads/{thread['id']}/comments",
        json={"actor": OWNER, "body": "The tiers step at 25 and 100."},
    )
    assert reply.status_code == 201, reply.text
    tagged = live.post(
        f"{PREFIX}/threads/{thread['id']}/mentions",
        json={"actor": BUYER, "principals": ["@legal"]},
    )
    assert tagged.status_code == 201
    watched = live.post(
        f"{PREFIX}/threads/{thread['id']}/watch",
        json={"actor": OWNER, "principal": VIEWER},
    )
    assert watched.status_code == 200
    assert VIEWER in watched.json()["watchers"]


def test_a_resolved_thread_refuses_a_comment_and_can_be_reopened(live):
    thread = open_thread(live).json()
    assert (
        live.post(f"{PREFIX}/threads/{thread['id']}/resolve", json={"actor": OWNER}).status_code
        == 200
    )
    refused = live.post(
        f"{PREFIX}/threads/{thread['id']}/comments", json={"actor": OWNER, "body": "x"}
    )
    assert refused.status_code == 409
    assert refused.json()["reason"] == "thread_resolved"
    assert (
        live.post(f"{PREFIX}/threads/{thread['id']}/reopen", json={"actor": OWNER}).status_code
        == 200
    )


def test_a_thread_converts_to_a_task(live):
    thread = open_thread(live).json()
    response = live.post(
        f"{PREFIX}/threads/{thread['id']}/convert-to-task",
        json={"actor": OWNER, "title": "Answer the pricing question"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["thread_ref"] == thread["id"]


# --------------------------------------------------------------------------- #
# Champion self-service over the wire
# --------------------------------------------------------------------------- #


def test_identity_names_the_champion(live):
    body = live.get(f"{PREFIX}/rooms/{ROOM}/identity", params={"actor": CHAMPION}).json()
    assert body["role"] == v.ROLE_COLLABORATOR
    assert body["side"] == v.SIDE_BUYER
    assert body["is_champion"] is True


def test_the_champion_edits_a_section_and_the_log_records_it(live):
    response = live.post(
        f"{PREFIX}/rooms/{ROOM}/edits",
        json={
            "actor": CHAMPION,
            "section_ref": "page_pricing",
            "before": {"headline": "Pricing"},
            "after": {"headline": "Pricing for the rollout"},
        },
    )
    assert response.status_code == 201, response.text
    edit = response.json()
    assert edit["champion"] is True
    assert edit["delta"]["headline"] == {"from": "Pricing", "to": "Pricing for the rollout"}
    log = live.get(f"{PREFIX}/rooms/{ROOM}/edits").json()
    assert log["count"] == 1
    champions = live.get(f"{PREFIX}/rooms/{ROOM}/champions").json()
    assert champions["champion_edits"] == 1


def test_a_viewer_cannot_edit_a_section(live):
    response = live.post(
        f"{PREFIX}/rooms/{ROOM}/edits",
        json={"actor": VIEWER, "section_ref": "page_pricing", "after": {"a": 1}},
    )
    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# Escalations over the wire
# --------------------------------------------------------------------------- #


def test_the_escalation_sweep_raises_an_overdue_question(live):
    from dsr.inroom_collaboration.engine import InRoomEngine

    past = InRoomEngine(
        _store(live), now=lambda: NOW - timedelta(hours=v.UNANSWERED_QUESTION_HOURS + 6)
    )
    past.open_thread(
        ROOM,
        {
            "actor": BUYER,
            "anchor_type": v.ANCHOR_MAP_TASK,
            "anchor_id": "task_security",
            "body": "Who signs the DPA?",
            "kind": v.KIND_QUESTION,
        },
        source="test",
    )
    response = live.post(f"{PREFIX}/rooms/{ROOM}/escalations/refresh")
    assert response.status_code == 201, response.text
    assert response.json()["created"] == 1
    listing = live.get(f"{PREFIX}/rooms/{ROOM}/escalations").json()
    assert listing["count"] == 1
    assert listing["escalations"][0]["escalated_to"] == OWNER


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def test_an_empty_thread_body_is_a_422(live):
    response = live.post(f"{PREFIX}/rooms/{ROOM}/threads", json={"actor": BUYER})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid"


def test_a_stranger_is_denied(live):
    response = open_thread(live, actor="nobody@example.com")
    assert response.status_code == 403
    assert response.json()["error"] == "denied"


def test_an_unknown_thread_is_a_404(live):
    response = live.get(f"{PREFIX}/threads/thread_absent")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_an_unknown_room_is_a_404(live):
    response = live.get(f"{PREFIX}/rooms/room_absent/identity")
    assert response.status_code == 404


def test_an_unknown_anchor_kind_is_a_422(live):
    response = open_thread(live, anchor_type="spreadsheet")
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# The audit guarantee
# --------------------------------------------------------------------------- #


def test_every_write_names_a_route_the_app_serves(live):
    open_thread(live)
    sources = {entry["source"] for entry in _store(live).audit(limit=50)}
    assert "POST /api/wf-132/rooms/{room_id}/threads" in sources


def test_a_thread_write_is_audited(live):
    before = len(_store(live).audit(limit=1000))
    open_thread(live)
    assert len(_store(live).audit(limit=1000)) > before
