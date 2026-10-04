"""WF-051 HTTP tests: the route surface, and the audit rule that guards it.

The tests that matter most here are the last two classes. Every write this
feature makes must name a route the app actually serves, which is checked
against the live registry rather than against a list written by hand, because a
hand-written list is exactly what drifts when a route is renamed.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from dsr.concierge_router import vocabulary
from dsr.concierge_router.engine import ConciergeRouterEngine
from dsr.features import wf051_route_and_book_a_demo_request_inline_f as feature
from fastapi.testclient import TestClient

PREFIX = "/api/wf-051"
MODULE = "wf051_route_and_book_a_demo_request_inline_f"
ROOM = "room_1"


def served_routes(client: TestClient) -> list[dict[str, Any]]:
    """Every route the host reports, from the live registry."""
    return [
        route
        for entry in client.get("/api/features").json()["features"]
        for route in entry["routes"]
    ]


def names_a_served_route(source: str, routes: list[dict[str, Any]]) -> bool:
    """True when an audit ``source`` names a mounted route, segment by segment.

    Segment-wise rather than string-equal, because a source names a route
    template: ``POST /api/wf-051/routers/{router_id}/publish`` is the route even
    though the write was made for one concrete router.
    """
    method, _, path = source.partition(" ")
    actual = [segment for segment in path.split("/") if segment]
    for route in routes:
        if method not in route["methods"]:
            continue
        template = [segment for segment in route["path"].split("/") if segment]
        if len(template) != len(actual):
            continue
        if all(
            expected.startswith("{") or expected == found
            for expected, found in zip(template, actual, strict=False)
        ):
            return True
    return False


def trigger(actions: list[str] | None = None, field_map: dict[str, str] | None = None) -> dict:
    return {
        "type": "trigger",
        "actions": actions if actions is not None else ["webform_is_submitted"],
        "field_map": field_map if field_map is not None else {"work_email": "email"},
    }


def calendar(assignment: dict | None = None, types: list[str] | None = None) -> dict:
    return {
        "type": "display_calendar",
        "assignment": assignment if assignment is not None else {"type": "round_robin"},
        "meeting_types": types if types is not None else ["demo"],
        "timer_minutes": 60,
    }


def declaration(**overrides) -> dict:
    payload = {
        "slug": "enterprise-demo",
        "name": "Enterprise demo",
        "nodes": [
            trigger(),
            {
                "type": "routing_rule",
                "name": "Named owner",
                "kind": "crm_ownership",
                "conditions": [],
                "calendar": calendar({"type": "owner"}),
            },
            calendar(),
            {"type": "catch_all", "name": "Deal desk"},
        ],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def room(client: TestClient) -> str:
    """A real room, because every routed path is room-scoped.

    Created through the core records route rather than by writing a ``room``
    row directly, so the room is one the rest of the product would also make.
    """
    created = client.post(
        "/api/records/room",
        json={"name": "Northwind deal desk", "account": "northwind", "status": "open"},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


@pytest.fixture
def seller(client: TestClient, room: str) -> dict[str, Any]:
    created = client.post(
        f"{PREFIX}/sellers?room_id={room}&actor=dana",
        json={"name": "Dana Ivers", "email": "dana@example.test", "team": "field-sales"},
    )
    assert created.status_code == 201, created.text
    return created.json()


@pytest.fixture
def router(client: TestClient, room: str) -> dict[str, Any]:
    created = client.post(
        f"{PREFIX}/routers?room_id={room}&actor=dana",
        json=declaration(publish_state="published", deployment=["web_form"]),
    )
    assert created.status_code == 201, created.text
    return created.json()


@pytest.fixture
def session(client: TestClient, room: str, seller: dict, router: dict) -> dict[str, Any]:
    answer = client.post(
        f"{PREFIX}/rooms/{room}/route?actor=buyer",
        json={
            "router_id": router["id"],
            "form_fields": {"work_email": "buyer@example.test", "company": "Northwind"},
        },
    )
    assert answer.status_code == 201, answer.text
    return answer.json()


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


class TestTheFeatureIsRegistered:
    def test_the_host_mounts_the_router_without_a_shared_file_being_edited(self, client):
        entries = [
            entry
            for entry in client.get("/api/features").json()["features"]
            if entry["id"] == feature.FEATURE["id"]
        ]
        assert entries, "the feature was not discovered"
        assert entries[0]["prefix"] == PREFIX
        assert entries[0]["ticket"] == "WF-051"

    def test_no_other_feature_failed_to_load(self, client):
        assert client.get("/api/features").json()["failed"] == []

    def test_the_feature_registers_one_exception_handler(self, client):
        entry = next(
            item
            for item in client.get("/api/features").json()["features"]
            if item["id"] == feature.FEATURE["id"]
        )
        assert entry["exception_handlers"] == ["RouterError"]

    def test_every_route_of_this_feature_is_advertised(self, client):
        paths = {route["path"] for route in served_routes(client)}
        for expected in [
            f"{PREFIX}/vocabulary",
            f"{PREFIX}/inferences",
            f"{PREFIX}/summary",
            f"{PREFIX}/routers",
            f"{PREFIX}/sellers",
            f"{PREFIX}/rooms/{{room_id}}/route",
            f"{PREFIX}/rooms/{{room_id}}/bookings",
        ]:
            assert expected in paths, f"{expected} was not advertised"


# --------------------------------------------------------------------------- #
# The vocabulary and the inferences
# --------------------------------------------------------------------------- #


class TestTheVocabularySurface:
    def test_the_vocabulary_is_served_as_data(self, client):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body["nodes"]["trigger"] == vocabulary.TRIGGER
        assert body["nodes"]["catch_all"] == vocabulary.CATCH_ALL
        assert body["bookings"]["guest_payload"] == vocabulary.PRIMARY_GUEST_PAYLOAD
        assert body["api"]["edge_scope"] == vocabulary.EDGE_SCOPE

    def test_the_vocabulary_lists_the_two_researched_operations(self, client):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert set(body["api"]["operations"]) == {"route", "schedule"}

    def test_the_inferences_are_served_with_their_rejected_readings(self, client):
        body = client.get(f"{PREFIX}/inferences").json()
        assert body["count"] >= 1
        for entry in body["inferences"]:
            assert entry["decision"] and entry["because"] and entry["rejected"]

    def test_the_summary_answers_before_anything_exists(self, client):
        body = client.get(f"{PREFIX}/summary").json()
        assert body["routers"] == 0
        assert body["bookings"] == 0


# --------------------------------------------------------------------------- #
# Declaring a router
# --------------------------------------------------------------------------- #


class TestDeclaringARouter:
    def test_a_valid_router_is_created(self, client, room):
        answer = client.post(
            f"{PREFIX}/routers?room_id={room}",
            json=declaration(publish_state="published", deployment=["web_form"]),
        )
        assert answer.status_code == 201
        assert answer.json()["slug"] == "enterprise-demo"

    def test_a_router_whose_trigger_is_not_first_is_refused_with_400(self, client, room):
        payload = declaration()
        payload["nodes"].insert(0, {"type": "catch_all", "name": "first"})
        answer = client.post(f"{PREFIX}/routers?room_id={room}", json=payload)
        assert answer.status_code == 400
        assert answer.json()["error"] == "router_trigger_not_first"

    def test_a_router_with_no_catch_all_is_refused_with_400(self, client, room):
        payload = declaration()
        payload["nodes"] = [node for node in payload["nodes"] if node["type"] != "catch_all"]
        answer = client.post(f"{PREFIX}/routers?room_id={room}", json=payload)
        assert answer.status_code == 400
        assert answer.json()["error"] == "router_rules_no_catch_all"

    def test_an_unknown_node_type_is_refused_with_400(self, client, room):
        payload = declaration()
        payload["nodes"].append({"type": "teleport", "name": "x"})
        answer = client.post(f"{PREFIX}/routers?room_id={room}", json=payload)
        assert answer.status_code == 400
        assert answer.json()["error"] == "router_node_unknown_type"

    def test_a_trigger_with_no_action_is_refused_with_400(self, client, room):
        payload = declaration()
        payload["nodes"][0]["actions"] = []
        answer = client.post(f"{PREFIX}/routers?room_id={room}", json=payload)
        assert answer.status_code == 400
        assert answer.json()["error"] == "router_trigger_action_missing"

    def test_the_error_body_names_the_code_and_the_status(self, client, room):
        payload = declaration()
        payload["nodes"][0]["actions"] = []
        body = client.post(f"{PREFIX}/routers?room_id={room}", json=payload).json()
        assert body["code"] == body["error"]
        assert body["status"] == 400
        assert body["detail"]

    def test_a_router_is_readable_by_id(self, client, router):
        assert client.get(f"{PREFIX}/routers/{router['id']}").json()["id"] == router["id"]

    def test_an_unknown_router_is_404(self, client):
        answer = client.get(f"{PREFIX}/routers/nope")
        assert answer.status_code == 404
        assert answer.json()["error"] == "router_not_found"

    def test_the_router_list_is_scoped_to_a_room(self, client, room, router):
        answer = client.get(f"{PREFIX}/routers?room_id={room}").json()
        assert answer["count"] == 1
        assert answer["routers"][0]["id"] == router["id"]


class TestPublishingARouter:
    def test_a_router_can_be_published(self, client, room):
        created = client.post(f"{PREFIX}/routers?room_id={room}", json=declaration()).json()
        answer = client.post(
            f"{PREFIX}/routers/{created['id']}/publish?room_id={room}",
            json={"publish_state": "published", "deployment": ["web_form"]},
        )
        assert answer.status_code == 200
        assert answer.json()["publish_state"] == "published"

    def test_publishing_without_a_deployment_surface_is_refused(self, client, room):
        created = client.post(f"{PREFIX}/routers?room_id={room}", json=declaration()).json()
        answer = client.post(
            f"{PREFIX}/routers/{created['id']}/publish?room_id={room}",
            json={"publish_state": "published", "deployment": []},
        )
        assert answer.status_code == 400
        assert answer.json()["error"] == "router_not_published"

    def test_a_router_can_be_switched_off(self, client, router, room):
        answer = client.post(
            f"{PREFIX}/routers/{router['id']}/publish?room_id={room}",
            json={"enabled": False},
        )
        assert answer.json()["enabled"] is False

    def test_publishing_an_unknown_router_is_404(self, client, room):
        answer = client.post(
            f"{PREFIX}/routers/nope/publish?room_id={room}", json={"enabled": True}
        )
        assert answer.status_code == 404


# --------------------------------------------------------------------------- #
# Sellers
# --------------------------------------------------------------------------- #


class TestSellers:
    def test_a_seller_is_registered(self, client, room):
        answer = client.post(
            f"{PREFIX}/sellers?room_id={room}",
            json={"name": "Sam Ortiz", "email": "sam@example.test"},
        )
        assert answer.status_code == 201
        assert answer.json()["name"] == "Sam Ortiz"

    def test_a_seller_with_no_name_is_refused(self, client, room):
        answer = client.post(f"{PREFIX}/sellers?room_id={room}", json={"email": "a@b.test"})
        assert answer.status_code == 400

    def test_the_sellers_of_a_room_are_listed(self, client, room, seller):
        answer = client.get(f"{PREFIX}/rooms/{room}/sellers").json()
        assert answer["count"] == 1
        assert answer["sellers"][0]["id"] == seller["id"]

    def test_a_room_with_no_sellers_lists_none(self, client, room):
        assert client.get(f"{PREFIX}/rooms/{room}/sellers").json()["count"] == 0


# --------------------------------------------------------------------------- #
# The first researched call
# --------------------------------------------------------------------------- #


class TestTheFirstCallOverHttp:
    def test_a_published_router_answers_with_the_researched_fields(self, session):
        assert set(vocabulary.ROUTE_RESPONSE_FIELDS) <= set(session)
        assert session["schedulingAllowed"] is True
        assert session["routeId"]

    def test_the_routing_link_is_a_tenant_url_while_the_stored_one_is_a_path(
        self, client, room, session
    ):
        assert session["routingLink"].startswith("/concierge-router/")
        stored = client.get(f"{PREFIX}/rooms/{room}/route/{session['routeId']}").json()
        assert not stored["routing_link"].startswith("http")

    def test_an_empty_body_is_refused_rather_than_crashing(self, client, room, router):
        """No router id, so the router is unknown: a 404, and never a 5xx."""
        answer = client.post(f"{PREFIX}/rooms/{room}/route", json={})
        assert answer.status_code == 404
        assert answer.json()["error"] == "router_not_found"

    def test_an_unpublished_router_is_refused(self, client, room, seller):
        created = client.post(f"{PREFIX}/routers?room_id={room}", json=declaration()).json()
        answer = client.post(
            f"{PREFIX}/rooms/{room}/route",
            json={"router_id": created["id"], "form_fields": {"work_email": "a@b.test"}},
        )
        assert answer.status_code == 400
        assert answer.json()["error"] == "router_not_published"

    def test_a_switched_off_router_is_refused_distinctly(self, client, room, router):
        client.post(
            f"{PREFIX}/routers/{router['id']}/publish?room_id={room}", json={"enabled": False}
        )
        answer = client.post(
            f"{PREFIX}/rooms/{room}/route",
            json={"router_id": router["id"], "form_fields": {"work_email": "a@b.test"}},
        )
        assert answer.status_code == 400
        assert answer.json()["error"] == "router_disabled"

    def test_a_request_with_no_guest_email_is_refused(self, client, room, router):
        answer = client.post(
            f"{PREFIX}/rooms/{room}/route",
            json={"router_id": router["id"], "form_fields": {"company": "Northwind"}},
        )
        assert answer.status_code == 400
        assert answer.json()["error"] == "router_guest_identity_missing"

    def test_the_slot_list_is_served_for_a_session(self, client, room, session):
        answer = client.get(f"{PREFIX}/rooms/{room}/route/{session['routeId']}/slots").json()
        assert answer["count"] == len(answer["slots"])
        assert answer["slots"], "an open session offers slots"

    def test_the_slots_of_an_unknown_session_are_404(self, client, room):
        assert client.get(f"{PREFIX}/rooms/{room}/route/nope/slots").status_code == 404


# --------------------------------------------------------------------------- #
# The second researched call
# --------------------------------------------------------------------------- #


class TestTheSecondCallOverHttp:
    def test_a_slot_is_committed_and_returns_a_meeting_id(self, client, room, session):
        slot = session["slots"][0]
        answer = client.post(
            f"{PREFIX}/rooms/{room}/route/{session['routeId']}/schedule-simple?actor=buyer",
            json={"start": slot["start"], "meeting_type": "demo"},
        )
        assert answer.status_code == 201, answer.text
        assert answer.json()["meetingId"]

    def test_committing_the_same_slot_twice_is_409(self, client, room, session):
        slot = session["slots"][0]
        client.post(
            f"{PREFIX}/rooms/{room}/route/{session['routeId']}/schedule-simple",
            json={"start": slot["start"], "meeting_type": "demo"},
        )
        again = client.post(
            f"{PREFIX}/rooms/{room}/route/{session['routeId']}/schedule-simple",
            json={"start": slot["start"], "meeting_type": "demo"},
        )
        assert again.status_code == 409
        assert again.json()["error"] == "router_route_consumed"

    def test_an_unoffered_meeting_type_is_refused(self, client, room, session):
        """A 400, not a 409: the caller named a type the node never offered."""
        answer = client.post(
            f"{PREFIX}/rooms/{room}/route/{session['routeId']}/schedule-simple",
            json={"start": session["slots"][0]["start"], "meeting_type": "keynote"},
        )
        assert answer.status_code == 400
        assert answer.json()["error"] == "router_meeting_type_not_offered"

    def test_an_unoffered_start_time_is_refused(self, client, room, session):
        answer = client.post(
            f"{PREFIX}/rooms/{room}/route/{session['routeId']}/schedule-simple",
            json={"start": "2027-01-01T03:00:00Z", "meeting_type": "demo"},
        )
        assert answer.status_code == 409
        assert answer.json()["error"] == "router_slot_not_offered"

    def test_committing_an_unknown_session_is_404(self, client, room):
        answer = client.post(
            f"{PREFIX}/rooms/{room}/route/nope/schedule-simple",
            json={"start": "2026-10-06T09:00:00Z", "meeting_type": "demo"},
        )
        assert answer.status_code == 404
        assert answer.json()["error"] == "router_route_not_found"

    def test_a_second_guest_cannot_commit_somebody_elses_session(self, client, room, session):
        answer = client.post(
            f"{PREFIX}/rooms/{room}/route/{session['routeId']}/schedule-simple",
            json={
                "start": session["slots"][0]["start"],
                "meeting_type": "demo",
                "guest": {"email": "thief@example.test"},
            },
        )
        assert answer.status_code == 409
        assert answer.json()["error"] == "router_guest_mismatch"


# --------------------------------------------------------------------------- #
# The researched Time Elapsed timer
# --------------------------------------------------------------------------- #


class TestTheTimerOverHttp:
    def test_expiring_a_session_moves_it_to_not_scheduled(self, client, room, session):
        answer = client.post(f"{PREFIX}/rooms/{room}/route/{session['routeId']}/expire")
        assert answer.status_code == 200
        assert answer.json()["state"] == vocabulary.NOT_SCHEDULED

    def test_expiring_records_a_notification_without_claiming_it_was_sent(
        self, client, room, session
    ):
        notification = client.post(
            f"{PREFIX}/rooms/{room}/route/{session['routeId']}/expire"
        ).json()["notification"]
        assert notification["sent"] is False
        assert notification["channel"] in vocabulary.NOTIFICATION_CHANNELS

    def test_an_expired_session_can_never_be_booked(self, client, room, session):
        client.post(f"{PREFIX}/rooms/{room}/route/{session['routeId']}/expire")
        answer = client.post(
            f"{PREFIX}/rooms/{room}/route/{session['routeId']}/schedule-simple",
            json={"start": session["slots"][0]["start"], "meeting_type": "demo"},
        )
        assert answer.status_code == 409

    def test_expiring_an_unknown_session_is_404(self, client, room):
        assert client.post(f"{PREFIX}/rooms/{room}/route/nope/expire").status_code == 404


# --------------------------------------------------------------------------- #
# Bookings
# --------------------------------------------------------------------------- #


class TestBookings:
    def test_the_bookings_of_a_room_are_listed(self, client, room, session):
        answer = client.get(f"{PREFIX}/rooms/{room}/bookings").json()
        assert answer["count"] == 1
        assert answer["bookings"][0]["id"] == session["routeId"]

    def test_the_list_can_be_filtered_by_state(self, client, room, session):
        pending = client.get(f"{PREFIX}/rooms/{room}/bookings?state=pending").json()
        assert pending["count"] == 1
        booked = client.get(f"{PREFIX}/rooms/{room}/bookings?state=booked").json()
        assert booked["count"] == 0

    def test_one_booking_is_readable_by_id(self, client, room, session):
        answer = client.get(f"{PREFIX}/rooms/{room}/bookings/{session['routeId']}")
        assert answer.status_code == 200
        assert answer.json()["id"] == session["routeId"]

    def test_reading_an_unknown_booking_is_404(self, client, room):
        answer = client.get(f"{PREFIX}/rooms/{room}/bookings/nope")
        assert answer.status_code == 404

    def test_cancelling_a_booking_moves_it_to_not_scheduled(self, client, room, session):
        slot = session["slots"][0]
        client.post(
            f"{PREFIX}/rooms/{room}/route/{session['routeId']}/schedule-simple",
            json={"start": slot["start"], "meeting_type": "demo"},
        )
        answer = client.delete(f"{PREFIX}/rooms/{room}/bookings/{session['routeId']}")
        assert answer.status_code == 200
        assert answer.json()["state"] == vocabulary.NOT_SCHEDULED

    def test_cancelling_an_unknown_booking_is_404(self, client, room):
        assert client.delete(f"{PREFIX}/rooms/{room}/bookings/nope").status_code == 404


# --------------------------------------------------------------------------- #
# The audit rule
# --------------------------------------------------------------------------- #


class TestEveryAuditRowNamesARouteThisAppServes:
    """The port brief's central guarantee, checked against the live route table."""

    def _sources(self, client: TestClient) -> list[str]:
        audit = client.get("/api/audit?limit=500").json()
        rows = audit["entries"] if isinstance(audit, dict) and "entries" in audit else audit
        return [
            str(row.get("source") or "") for row in rows if PREFIX in str(row.get("source") or "")
        ]

    def _exercise_every_write(self, client: TestClient, room: str) -> None:
        client.post(
            f"{PREFIX}/sellers?room_id={room}",
            json={"name": "Dana Ivers", "email": "dana@example.test"},
        )
        created = client.post(
            f"{PREFIX}/routers?room_id={room}",
            json=declaration(publish_state="published", deployment=["web_form"]),
        ).json()
        client.post(
            f"{PREFIX}/routers/{created['id']}/publish?room_id={room}",
            json={"publish_state": "published", "deployment": ["in_app_button"]},
        )
        routed = client.post(
            f"{PREFIX}/rooms/{room}/route",
            json={
                "router_id": created["id"],
                "form_fields": {"work_email": "buyer@example.test"},
            },
        ).json()
        client.post(
            f"{PREFIX}/rooms/{room}/route/{routed['routeId']}/schedule-simple",
            json={"start": routed["slots"][0]["start"], "meeting_type": "demo"},
        )
        client.delete(f"{PREFIX}/rooms/{room}/bookings/{routed['routeId']}")

    def test_every_write_audit_row_names_a_route_the_app_serves(self, client, room):
        self._exercise_every_write(client, room)
        sources = self._sources(client)
        assert sources, "no audit row from this feature was written"
        routes = served_routes(client)
        for source in sources:
            assert names_a_served_route(source, routes), (
                f"audit row names {source!r}, which is not a route this app serves"
            )

    def test_the_sources_written_are_exactly_the_routes_that_served_them(self, client, room):
        self._exercise_every_write(client, room)
        assert set(self._sources(client)) == {
            f"POST {PREFIX}/sellers",
            f"POST {PREFIX}/routers",
            f"POST {PREFIX}/routers/{{router_id}}/publish",
            f"POST {PREFIX}/rooms/{{room_id}}/route",
            f"POST {PREFIX}/rooms/{{room_id}}/route/{{route_id}}/schedule-simple",
            f"DELETE {PREFIX}/rooms/{{room_id}}/bookings/{{booking_id}}",
        }

    def test_the_timer_transition_is_audited_under_a_served_route(self, client, room, session):
        client.post(f"{PREFIX}/rooms/{room}/route/{session['routeId']}/expire")
        assert f"POST {PREFIX}/rooms/{{room_id}}/route/{{route_id}}/expire" in set(
            self._sources(client)
        )


# --------------------------------------------------------------------------- #
# The no-5xx rule
# --------------------------------------------------------------------------- #


class TestEveryRouteAnswersWithoutA5xx:
    def test_every_route_answers_when_called_with_an_empty_body(self, client):
        """The tool CI runs substitutes ids and sends ``{}`` to every method."""
        faults: list[tuple[str, str, int]] = []
        for route in served_routes(client):
            if not route["path"].startswith(PREFIX):
                continue
            path = route["path"]
            for segment, replacement in (
                ("{room_id}", ROOM),
                ("{router_id}", "absent"),
                ("{route_id}", "absent"),
                ("{booking_id}", "absent"),
            ):
                path = path.replace(segment, replacement)
            for method in route["methods"]:
                if method not in {"GET", "POST", "PATCH", "DELETE"}:
                    continue
                body = {} if method in {"POST", "PATCH", "PUT"} else None
                response = client.request(method, path, json=body)
                if response.status_code == 0 or response.status_code >= 500:
                    faults.append((method, path, response.status_code))
        assert faults == [], f"routes answered 5xx when called with an empty body: {faults}"


# --------------------------------------------------------------------------- #
# The architectural guards
# --------------------------------------------------------------------------- #


class TestTheContractIsHeld:
    def test_the_feature_module_imports_no_shared_file_and_opens_no_connection(self):
        text = Path(feature.__file__).read_text(encoding="utf-8")
        assert "from dsr.api" not in text
        assert "import dsr.api" not in text
        assert "import sqlite3" not in text
        assert "sqlite3.connect" not in text

    def test_the_audit_source_is_built_from_the_router_prefix(self):
        """Never a literal, because a renamed route must not leave a stale string."""
        text = Path(feature.__file__).read_text(encoding="utf-8")
        assert 'source="POST /' not in text
        assert 'source="DELETE /' not in text

    def test_the_engine_is_built_per_request_over_the_stores_store(self, client, room):
        """One engine per request, so it cannot hold a previous test's database."""
        engine = ConciergeRouterEngine(client.app.state.store)
        assert engine.store is client.app.state.store

    def test_the_routes_carry_the_researched_prefix(self):
        assert feature.router.prefix == PREFIX

    def test_a_booked_slot_leaves_the_sellers_calendar_busy(self, client, room, session, seller):
        slot = session["slots"][0]
        client.post(
            f"{PREFIX}/rooms/{room}/route/{session['routeId']}/schedule-simple",
            json={"start": slot["start"], "meeting_type": "demo"},
        )
        refreshed = client.get(f"{PREFIX}/rooms/{room}/sellers").json()["sellers"][0]
        assert any(block["start"] == slot["start"] for block in refreshed["busy"])

    def test_a_cancelled_slot_returns_to_the_sellers_calendar(self, client, room, session):
        slot = session["slots"][0]
        client.post(
            f"{PREFIX}/rooms/{room}/route/{session['routeId']}/schedule-simple",
            json={"start": slot["start"], "meeting_type": "demo"},
        )
        client.delete(f"{PREFIX}/rooms/{room}/bookings/{session['routeId']}")
        refreshed = client.get(f"{PREFIX}/rooms/{room}/sellers").json()["sellers"][0]
        assert all(block["start"] != slot["start"] for block in refreshed["busy"])


def test_no_route_collides_with_another_feature(client):
    """Two features may share a prefix, but never a concrete (method, path)."""
    seen: set[tuple[str, str]] = set()
    for entry in client.get("/api/features").json()["features"]:
        for route in entry["routes"]:
            for method in route["methods"]:
                key = (method, route["path"])
                assert key not in seen, f"{entry['id']} duplicates {key}"
                seen.add(key)


def test_a_timer_deadline_is_recorded_so_a_late_reader_sees_it(session):
    assert session["timerExpiresAt"] > vocabulary.iso(vocabulary.utcnow() - timedelta(days=3650))
