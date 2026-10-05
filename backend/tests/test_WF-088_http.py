"""WF-088: the HTTP surface, over the routes the app actually mounts.

The domain rules are in ``test_WF-088.py``. This file checks the parts a domain test
cannot: that the router is discovered and mounted, that each route answers with the
status the researched rule calls for, that a refusal carries the researched sentence
rather than a developer's paraphrase, and that every audit row the routes write names
a path this application serves.

The last one is the check worth having on this workflow in particular. A feature's
audit log kept naming a path the app had stopped serving in this codebase before, and
the only way to catch it is to compare the recorded source strings against the route
table the host built.

Every test here passes when this file is run on its own, which is what the brief asks
for. Nothing is shared with another module's fixtures beyond the ones in
``conftest.py``.
"""

from __future__ import annotations

import pytest
from dsr.quoting_proposals import price_book_vocabulary as vocab
from dsr.store import RecordStore
from fastapi.testclient import TestClient

PREFIX = "/api/WF-088"

BOOK_ENTERPRISE = {"id": "pb-ent", "name": "Enterprise list 2026"}
BOOK_STANDARD = {"id": "pb-std", "name": "Standard list"}
BOOK_PARTNER = {"id": "pb-prt", "name": "Preferred partner list"}


@pytest.fixture()
def store(client: TestClient) -> RecordStore:
    """A store on this test's own database, with one room in it.

    Taken from ``app.state`` rather than built separately, so the store the fixture
    writes through is the same handle the routes read from. A second handle would
    write to the same rows through a different connection and hide any ordering
    problem.
    """
    from dsr.api import app

    store = RecordStore(app.state.db)
    store.create(
        "room", {"name": "Northwind data room"}, record_id="room_a", actor="dana", source="fixture"
    )
    return store


@pytest.fixture()
def rule_body() -> dict:
    """A rule that assigns one book to a deal carrying a given segment."""
    return {
        "key": "enterprise",
        "label": "Enterprise deals",
        "price_book": BOOK_ENTERPRISE,
        "filters": [
            {"object": "deal", "property": "segment", "operator": "is", "value": "enterprise"}
        ],
    }


def make_deal(store: RecordStore, deal_id: str = "d1", **data) -> str:
    """One deal as a CRM mirror writes it."""
    store.create(
        vocab.SOURCE_DEALS,
        {"name": deal_id, **data},
        record_id=deal_id,
        room_id="room_a",
        actor="dana",
        source="fixture",
    )
    return deal_id


def make_quote(store: RecordStore, quote_id: str, deal_id: str) -> str:
    store.create(
        vocab.SOURCE_QUOTES,
        {"name": quote_id, "deal": deal_id, "creator": "dana"},
        record_id=quote_id,
        room_id="room_a",
        actor="dana",
        source="fixture",
    )
    return quote_id


def save_rule(client: TestClient, rule: dict) -> dict:
    answer = client.post(
        f"{PREFIX}/rules", json=rule, params={"room_id": "room_a", "actor": "dana"}
    )
    assert answer.status_code == 201, answer.text
    return answer.json()


def run(client: TestClient, deal_id: str = "d1", **params) -> dict:
    """The researched moment, through the route."""
    answer = client.post(
        f"{PREFIX}/deals/{deal_id}/assign",
        json={},
        params={"room_id": "room_a", "actor": "dana", **params},
    )
    assert answer.status_code == 201, answer.text
    return answer.json()


def _matches_a_served_route(method: str, path: str, served: set) -> bool:
    """Whether an audit source names a path the app serves.

    An audit row records the concrete request path, so a row written by
    ``POST /api/WF-088/deals/d1/assign`` carries ``d1`` where the route table carries
    ``{deal_id}``. Comparing the two literally would fail on every row this workflow
    writes, so a path parameter is matched by position.

    The check is deliberately not loosened to a prefix test. A row naming
    ``/api/WF-088/deals/d1/assign`` must be matched by a route that is really mounted
    and really accepts that method, because the defect this guards against is an audit
    log pointing at a path the app stopped serving.
    """
    if (method, path) in served:
        return True
    parts = path.split("/")
    for served_method, template in served:
        if served_method != method:
            continue
        template_parts = template.split("/")
        if len(template_parts) != len(parts):
            continue
        if all(
            wanted == found or (wanted.startswith("{") and wanted.endswith("}"))
            for wanted, found in zip(template_parts, parts, strict=True)
        ):
            return True
    return False


def _served_routes(client: TestClient) -> set:
    """Every (method, path) the registry advertises."""
    served: set = set()
    for feature in client.get("/api/features").json()["features"]:
        for route in feature.get("routes") or []:
            for method in route.get("methods") or []:
                served.add((method, route["path"]))
    return served


# --------------------------------------------------------------------------- #
# the router is discovered and mounted
# --------------------------------------------------------------------------- #


class TestDiscovery:
    def test_the_feature_is_registered(self, client: TestClient):
        """The host finds the module by walking the features package. Nothing registers it."""
        listed = client.get("/api/features").json()
        entry = next(one for one in listed["features"] if one["ticket"] == "WF-088")

        assert entry["prefix"] == PREFIX
        assert entry["loaded"] is True

    def test_the_feature_is_not_in_the_failed_list(self, client: TestClient):
        listed = client.get("/api/features").json()

        assert [one for one in listed["failed"] if one["ticket"] == "WF-088"] == []

    def test_the_whole_surface_answers(self, client: TestClient):
        """A route that 404s here would be a route no reviewer could ever see."""
        for path, params in (
            ("/vocabulary", {}),
            ("/inferences", {}),
            ("/rules", {}),
            ("/assignments", {}),
            ("/summary", {}),
        ):
            answer = client.get(f"{PREFIX}{path}", params=params)
            assert answer.status_code == 200, f"{path}: {answer.text}"

    def test_the_deal_scoped_reads_answer(self, client: TestClient, store: RecordStore):
        """The reads exist on an empty database, not only on a priced one."""
        make_deal(store)

        for path in ("/deals/d1/conditions", "/deals/d1/price-book"):
            answer = client.get(f"{PREFIX}{path}")
            assert answer.status_code == 200, f"{path}: {answer.text}"


# --------------------------------------------------------------------------- #
# the rules, over HTTP
# --------------------------------------------------------------------------- #


class TestRulesOverHttp:
    def test_a_rule_is_saved(self, client: TestClient, rule_body: dict):
        created = save_rule(client, rule_body)

        assert created["data"]["key"] == "enterprise"
        assert created["data"]["price_book"] == BOOK_ENTERPRISE
        assert created["data"]["enabled"] is True
        assert created["data"]["auto_assign"] is True

    def test_a_rule_with_no_price_book_is_422(self, client: TestClient, rule_body: dict):
        rule_body.pop("price_book")

        answer = client.post(f"{PREFIX}/rules", json=rule_body, params={"room_id": "room_a"})

        assert answer.status_code == 422
        assert answer.json()["error"] == "rule_needs_a_price_book"

    def test_a_rule_with_no_filter_is_422(self, client: TestClient, rule_body: dict):
        rule_body["filters"] = []

        answer = client.post(f"{PREFIX}/rules", json=rule_body, params={"room_id": "room_a"})

        assert answer.status_code == 422
        assert answer.json()["error"] == "rule_needs_a_filter"

    def test_the_refusal_carries_the_researched_sentence(self, client: TestClient):
        """Not a developer's paraphrase of it. The whole point of a code is the sentence.

        The key is long enough to pass the name check first, so the refusal under test is
        the missing price book and not the name. A one-character key is refused earlier,
        which is why an earlier draft of this test asserted on the wrong sentence.
        """
        answer = client.post(
            f"{PREFIX}/rules",
            json={
                "key": "software-industry",
                "filters": [
                    {"object": "deal", "property": "segment", "operator": "is", "value": "x"}
                ],
            },
            params={"room_id": "room_a"},
        )

        assert answer.status_code == 422
        assert "A rule assigns a price book" in answer.json()["detail"]

    def test_the_refusal_carries_a_field_keyed_map(self, client: TestClient, rule_body: dict):
        rule_body["filters"] = []

        answer = client.post(f"{PREFIX}/rules", json=rule_body, params={"room_id": "room_a"})

        assert "filters" in answer.json()["errors"]

    def test_one_rule_is_readable(self, client: TestClient, rule_body: dict):
        created = save_rule(client, rule_body)

        answer = client.get(f"{PREFIX}/rules/{created['id']}")

        assert answer.status_code == 200
        assert answer.json()["rule"]["id"] == created["id"]

    def test_an_unknown_rule_is_404(self, client: TestClient):
        answer = client.get(f"{PREFIX}/rules/rule_absent")

        assert answer.status_code == 404
        assert answer.json()["error"] == "unknown_assignment_rule"

    def test_the_auto_assigned_toggle_is_a_patch(self, client: TestClient, rule_body: dict):
        """Step two's "toggle **Auto-assigned** on", and its off state."""
        created = save_rule(client, rule_body)

        off = client.patch(
            f"{PREFIX}/rules/{created['id']}",
            json={"auto_assign": False},
            params={"actor": "dana"},
        )

        assert off.status_code == 200
        assert off.json()["data"]["auto_assign"] is False
        # A merge patch, so toggling one switch does not reset the filters.
        assert off.json()["data"]["price_book"] == BOOK_ENTERPRISE
        assert len(off.json()["data"]["filters"]) == 1

    def test_the_inactive_switch_is_a_patch(self, client: TestClient, rule_body: dict):
        """Step two's "toggle the price book's **Inactive** switch off to activate"."""
        created = save_rule(client, rule_body)

        answer = client.patch(
            f"{PREFIX}/rules/{created['id']}", json={"enabled": False}, params={"actor": "dana"}
        )

        assert answer.json()["data"]["enabled"] is False

    def test_a_patch_that_would_drop_the_book_is_422(self, client: TestClient, rule_body: dict):
        """A half-applied toggle is a rule that reads as configured and is not."""
        created = save_rule(client, rule_body)

        answer = client.patch(
            f"{PREFIX}/rules/{created['id']}",
            json={"price_book": None},
            params={"actor": "dana"},
        )

        assert answer.status_code == 422

    def test_a_rule_is_removed(self, client: TestClient, rule_body: dict):
        created = save_rule(client, rule_body)

        removed = client.delete(f"{PREFIX}/rules/{created['id']}", params={"actor": "dana"})

        assert removed.status_code == 200
        assert client.get(f"{PREFIX}/rules/{created['id']}").status_code == 404

    def test_the_rules_are_listed_oldest_first(self, client: TestClient, rule_body: dict):
        save_rule(client, {**rule_body, "key": "first"})
        save_rule(client, {**rule_body, "key": "second", "label": "Second"})

        listed = client.get(f"{PREFIX}/rules").json()

        assert [one["data"]["key"] for one in listed["rules"]] == ["first", "second"]

    def test_the_rules_can_be_scoped_to_a_room(self, client: TestClient, rule_body: dict):
        save_rule(client, rule_body)

        listed = client.get(f"{PREFIX}/rules", params={"room_id": "room_a"}).json()

        assert listed["count"] == 1
        assert listed["room_id"] == "room_a"


# --------------------------------------------------------------------------- #
# the researched moment
# --------------------------------------------------------------------------- #


class TestAssignOverHttp:
    def test_one_match_writes_the_book(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)

        answer = run(client)

        assert answer["outcome"] == vocab.ASSIGNMENT_ASSIGNED
        assert answer["written"] is True
        assert answer["price_book"] == BOOK_ENTERPRISE
        assert answer["state"] == vocab.BOOK_STATE_ASSIGNED

    def test_the_book_is_on_the_deal_afterwards(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)
        run(client)

        assert store.get("d1")["data"][vocab.PRICE_BOOK]["id"] == BOOK_ENTERPRISE["id"]

    def test_two_matches_assign_nothing(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_deal(store, segment="mid", amount=45000)
        # Two rules that both match this deal, each by a different property, so the
        # count is a real collision rather than two copies of one filter.
        save_rule(
            client,
            {
                **rule_body,
                "key": "mid-standard",
                "price_book": BOOK_STANDARD,
                "filters": [
                    {"object": "deal", "property": "segment", "operator": "is", "value": "mid"}
                ],
            },
        )
        save_rule(
            client,
            {
                "key": "mid-big",
                "label": "Mid partner",
                "price_book": BOOK_PARTNER,
                "filters": [
                    {"object": "deal", "property": "amount", "operator": "gte", "value": 20000}
                ],
            },
        )

        answer = run(client)

        assert answer["outcome"] == vocab.ASSIGNMENT_NEEDS_CHOICE
        assert answer["written"] is False
        assert answer["state"] == vocab.BOOK_STATE_NEEDS_CHOICE
        assert {one["price_book"]["id"] for one in answer["candidates"]} == {"pb-std", "pb-prt"}

    def test_an_update_trigger_assigns_nothing(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)

        answer = run(client, trigger=vocab.TRIGGER_UPDATE)

        assert answer["outcome"] == vocab.ASSIGNMENT_NOT_ON_CREATE
        assert vocab.PRICE_BOOK not in store.get("d1")["data"]

    def test_an_unknown_trigger_is_422(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)

        answer = client.post(f"{PREFIX}/deals/d1/assign", json={}, params={"trigger": "whenever"})

        assert answer.status_code == 422
        assert answer.json()["error"] == "unknown_trigger"

    def test_the_trigger_may_arrive_in_the_body(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """An integration posting the message needs not know this build's query spelling."""
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)

        answer = client.post(
            f"{PREFIX}/deals/d1/assign",
            json={"trigger": vocab.TRIGGER_UPDATE},
            params={"room_id": "room_a"},
        )

        assert answer.json()["outcome"] == vocab.ASSIGNMENT_NOT_ON_CREATE

    def test_a_second_run_does_not_reassign(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)
        run(client)

        answer = run(client)

        assert answer["outcome"] == vocab.ASSIGNMENT_ALREADY_ASSIGNED

    def test_an_unknown_deal_is_404(self, client: TestClient):
        answer = client.post(f"{PREFIX}/deals/d_absent/assign", json={})

        assert answer.status_code == 404
        assert answer.json()["error"] == "unknown_deal"

    def test_no_rule_is_still_201_with_a_reason(self, client: TestClient, store: RecordStore):
        """ "Nothing was assigned" and "a book was assigned" are both real results."""
        make_deal(store, segment="enterprise")

        answer = client.post(f"{PREFIX}/deals/d1/assign", json={}, params={"room_id": "room_a"})

        assert answer.status_code == 201
        assert answer.json()["outcome"] == vocab.ASSIGNMENT_NO_MATCH
        assert answer.json()["explanation"]

    def test_the_answer_carries_the_assignment_row(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """So a caller does not have to make a second call to learn what happened."""
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)

        answer = run(client)

        assert answer["assignment"]["outcome"] == vocab.ASSIGNMENT_ASSIGNED
        assert answer["assignment"]["rule_label"] == "Enterprise deals"

    def test_the_answer_carries_the_create_only_quote(self, client: TestClient, store: RecordStore):
        """The sourced sentence, on the response that enforces it."""
        make_deal(store)

        assert client.post(f"{PREFIX}/deals/d1/assign", json={}).json()["create_only_quote"] == (
            vocab.CREATE_ONLY_QUOTE
        )


# --------------------------------------------------------------------------- #
# the conditions panel
# --------------------------------------------------------------------------- #


class TestConditionsOverHttp:
    def test_the_panel_writes_nothing(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """A GET because it checks and writes nothing."""
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)

        answer = client.get(f"{PREFIX}/deals/d1/conditions")

        assert answer.status_code == 200
        assert answer.json()["outcome"] == vocab.ASSIGNMENT_ASSIGNED
        assert store.list(vocab.ASSIGNMENTS, limit=10) == []
        assert vocab.PRICE_BOOK not in store.get("d1")["data"]

    def test_the_panel_names_the_rule_that_did_not_match(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """So the panel can "review the matching deals" and the misses with them."""
        make_deal(store, segment="pilot")
        save_rule(client, rule_body)

        panel = client.get(f"{PREFIX}/deals/d1/conditions").json()

        assert panel["outcome"] == vocab.ASSIGNMENT_NO_MATCH
        assert panel["rules"][0]["matched"] is False
        assert panel["rules"][0]["filters"][0]["actual"] == "pilot"

    def test_the_panel_names_the_inactive_rules(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """Because the fix is a switch, not a rewritten filter."""
        make_deal(store, segment="enterprise")
        save_rule(client, {**rule_body, "enabled": False})

        panel = client.get(f"{PREFIX}/deals/d1/conditions").json()

        assert panel["outcome"] == vocab.ASSIGNMENT_NO_MATCH_INACTIVE_RULE
        assert panel["inactive_rules"] == ["Enterprise deals"]

    def test_the_panel_names_the_test_first_rules(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_deal(store, segment="enterprise")
        save_rule(client, {**rule_body, "auto_assign": False})

        panel = client.get(f"{PREFIX}/deals/d1/conditions").json()

        assert panel["outcome"] == vocab.ASSIGNMENT_NO_AUTO_ASSIGN
        assert panel["test_first_rules"] == ["Enterprise deals"]
        assert panel["mode"] == vocab.MODE_TEST_ONLY

    def test_the_panel_reports_the_line_items(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """Because a change of book removes them, and a seller has to see them first."""
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)
        store.create(
            vocab.LINE_ITEMS,
            {"deal_id": "d1", "sku": "SEAT-STD"},
            room_id="room_a",
            actor="dana",
            source="fixture",
        )

        panel = client.get(f"{PREFIX}/deals/d1/conditions").json()

        assert len(panel["line_items"]) == 1


# --------------------------------------------------------------------------- #
# the price book on a deal, and the override
# --------------------------------------------------------------------------- #


class TestTheDealPriceBookOverHttp:
    def test_an_unpriced_deal_reads_none(self, client: TestClient, store: RecordStore):
        make_deal(store)

        answer = client.get(f"{PREFIX}/deals/d1/price-book").json()

        assert answer["price_book"] is None
        assert answer["price_book_label"] == vocab.PRICE_BOOK_NONE_LABEL
        assert answer["state"] == vocab.BOOK_STATE_UNASSIGNED

    def test_a_priced_deal_reads_its_book(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)
        run(client)

        answer = client.get(f"{PREFIX}/deals/d1/price-book").json()

        assert answer["price_book_label"] == BOOK_ENTERPRISE["name"]
        assert answer["state"] == vocab.BOOK_STATE_ASSIGNED

    def test_the_override_is_the_dropdown(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """ "click **Price book: None** -> **Price book** dropdown -> **Change price book**"."""
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)
        run(client)

        answer = client.post(
            f"{PREFIX}/deals/d1/price-book",
            json={"price_book": BOOK_PARTNER},
            params={"room_id": "room_a", "actor": "sam"},
        )

        assert answer.status_code == 200
        assert answer.json()["outcome"] == vocab.ASSIGNMENT_CHANGED
        assert store.get("d1")["data"][vocab.PRICE_BOOK]["id"] == BOOK_PARTNER["id"]

    def test_the_override_names_the_lines_it_removed(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """The sourced sentence, with a count, on the response that performed it."""
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)
        run(client)
        line = store.create(
            vocab.LINE_ITEMS,
            {"deal_id": "d1", "sku": "SEAT-STD"},
            room_id="room_a",
            actor="dana",
            source="fixture",
        )

        answer = client.post(
            f"{PREFIX}/deals/d1/price-book",
            json={"price_book": BOOK_PARTNER},
            params={"room_id": "room_a", "actor": "sam"},
        ).json()

        assert answer["line_items_removed"] == [line["id"]]
        assert answer["line_items_removed_count"] == 1
        assert answer["line_items_removed_quote"] == vocab.LINE_ITEMS_REMOVED_QUOTE

    def test_setting_the_same_book_is_409(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """A no-op that removes nothing still reads as a change of book on the trail."""
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)
        run(client)

        answer = client.post(f"{PREFIX}/deals/d1/price-book", json={"price_book": BOOK_ENTERPRISE})

        assert answer.status_code == 409
        assert answer.json()["error"] == "override_to_the_same_price_book"

    def test_an_override_with_no_book_is_422(self, client: TestClient, store: RecordStore):
        make_deal(store)

        answer = client.post(f"{PREFIX}/deals/d1/price-book", json={})

        assert answer.status_code == 422
        assert answer.json()["error"] == "price_book_needs_an_id_or_a_name"

    def test_a_book_id_alone_is_accepted(self, client: TestClient, store: RecordStore):
        """A deployment may identify its books by id, and the dropdown may send only that."""
        make_deal(store)

        answer = client.post(
            f"{PREFIX}/deals/d1/price-book", json={"price_book_id": BOOK_PARTNER["id"]}
        )

        assert answer.status_code == 200
        assert answer.json()["price_book"]["id"] == BOOK_PARTNER["id"]

    def test_an_override_on_an_unknown_deal_is_404(self, client: TestClient):
        answer = client.post(
            f"{PREFIX}/deals/d_absent/price-book", json={"price_book": BOOK_PARTNER}
        )

        assert answer.status_code == 404


# --------------------------------------------------------------------------- #
# the quote
# --------------------------------------------------------------------------- #


class TestTheQuoteOverHttp:
    def test_a_quote_inherits_its_deals_book(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)
        run(client)
        make_quote(store, "q1", "d1")

        answer = client.get(f"{PREFIX}/quotes/q1/price-book").json()

        assert answer["price_book"]["id"] == BOOK_ENTERPRISE["id"]
        assert answer["authority"] == vocab.INHERITANCE_AUTHORITY_DEAL

    def test_there_is_no_route_that_sets_a_quote_price_book(self, client: TestClient):
        """ "Users can't select a price book when creating a quote"."""
        served = _served_routes(client)

        assert not [
            (method, path)
            for method, path in served
            if method in ("POST", "PATCH", "PUT") and path.startswith(f"{PREFIX}/quotes/")
        ]

    def test_a_quote_with_no_deal_reports_none(self, client: TestClient, store: RecordStore):
        make_quote(store, "q1", "d_absent")

        answer = client.get(f"{PREFIX}/quotes/q1/price-book").json()

        assert answer["authority"] == vocab.INHERITANCE_AUTHORITY_NONE
        assert answer["settable_here"] is False
        assert answer["evidence"] == vocab.QUOTE_INHERITS_QUOTE

    def test_an_unknown_quote_is_404(self, client: TestClient):
        answer = client.get(f"{PREFIX}/quotes/q_absent/price-book")

        assert answer.status_code == 404
        assert answer.json()["error"] == "unknown_quote"


# --------------------------------------------------------------------------- #
# the log and the board
# --------------------------------------------------------------------------- #


class TestTheLogAndTheBoard:
    def test_every_evaluation_is_recorded(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """Including the ones that wrote nothing, which is the question a seller asks."""
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)
        run(client)
        make_deal(store, deal_id="d2", segment="pilot")
        run(client, "d2")

        listed = client.get(f"{PREFIX}/assignments").json()

        assert listed["count"] == 2
        assert {one["outcome"] for one in listed["assignments"]} == {
            vocab.ASSIGNMENT_ASSIGNED,
            vocab.ASSIGNMENT_NO_MATCH,
        }

    def test_the_log_can_be_filtered_by_deal(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)
        run(client)
        make_deal(store, deal_id="d2", segment="pilot")
        run(client, "d2")

        listed = client.get(f"{PREFIX}/assignments", params={"deal_id": "d2"}).json()

        assert [one["deal_id"] for one in listed["assignments"]] == ["d2"]

    def test_the_log_can_be_filtered_by_reason(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)
        run(client)
        make_deal(store, deal_id="d2", segment="pilot")
        run(client, "d2")

        listed = client.get(
            f"{PREFIX}/assignments", params={"outcome": vocab.ASSIGNMENT_NO_MATCH}
        ).json()

        assert [one["deal_id"] for one in listed["assignments"]] == ["d2"]

    def test_the_log_serves_the_published_reasons(self, client: TestClient):
        """So a client can render a filter from the server's list rather than its own."""
        listed = client.get(f"{PREFIX}/assignments").json()

        assert set(listed["reasons"]) == set(vocab.ASSIGNMENT_REASONS)

    def test_one_assignment_is_readable(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)
        assignment = run(client)["assignment"]

        answer = client.get(f"{PREFIX}/assignments/{assignment['id']}")

        assert answer.status_code == 200
        assert answer.json()["assignment"]["id"] == assignment["id"]

    def test_an_unknown_assignment_is_404(self, client: TestClient):
        answer = client.get(f"{PREFIX}/assignments/a_absent")

        assert answer.status_code == 404
        assert answer.json()["error"] == "unknown_assignment"

    def test_the_board_counts_what_a_reviewer_acts_on(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_deal(store, segment="mid", amount=45000)
        save_rule(
            client,
            {
                **rule_body,
                "key": "mid-standard",
                "price_book": BOOK_STANDARD,
                "filters": [
                    {"object": "deal", "property": "segment", "operator": "is", "value": "mid"}
                ],
            },
        )
        save_rule(
            client,
            {
                "key": "mid-big",
                "label": "Mid partner",
                "price_book": BOOK_PARTNER,
                "filters": [
                    {"object": "deal", "property": "amount", "operator": "gte", "value": 20000}
                ],
            },
        )
        run(client)

        board = client.get(f"{PREFIX}/summary").json()

        assert board["awaiting_a_choice"] == 1
        assert board["rules"] == 2
        assert board["mode"] == vocab.MODE_AUTO_ASSIGN

    def test_the_board_reports_the_catalogue(self, client: TestClient, store: RecordStore):
        """WF-087 has not shipped, and the page has to be able to say so."""
        board = client.get(f"{PREFIX}/summary").json()

        assert board["catalogue_found"] is False
        assert board["catalogue_note"]

    def test_the_board_carries_both_open_readings(self, client: TestClient):
        board = client.get(f"{PREFIX}/summary").json()

        assert board["multiple_matches"]["chosen"] == "needs_choice"
        assert board["override_removes_lines"]["chosen"] == "remove_and_name"


# --------------------------------------------------------------------------- #
# the published vocabulary
# --------------------------------------------------------------------------- #


class TestTheVocabularyOverHttp:
    def test_the_vocabulary_is_served(self, client: TestClient):
        served = client.get(f"{PREFIX}/vocabulary").json()

        assert served["collections"]["price_book_rules"] == vocab.PRICE_BOOK_RULES
        assert served["price_book"]["field"] == vocab.PRICE_BOOK

    def test_the_vocabulary_carries_the_dynamics_names(self, client: TestClient):
        """A client written against the Dynamics documentation looks for these."""
        dynamics = client.get(f"{PREFIX}/vocabulary").json()["dynamics"]

        assert dynamics["message"] == "GetDefaultPriceLevelRequest"
        assert dynamics["connection_role"] == "Territory Default Pricelist"
        assert (
            dynamics["system_setting"] == "Organization.UseInbuiltRuleForDefaultPriceSelectionRule"
        )

    def test_the_vocabulary_serves_all_four_inheritance_reasons(self, client: TestClient):
        """A client that renders the sentence needs the whole set, not the one it hit.

        Added after review found the deal-present-but-unpriced case and the
        no-associated-deal case arriving as one sentence, and a quote naming a deal that
        is not there reported as having no deal at all. Publishing only the sentences that
        were reachable would have left a client unable to tell any of them apart.
        """
        served = client.get(f"{PREFIX}/vocabulary").json()["quote_inheritance"]["reasons"]

        assert served == [
            vocab.INHERITANCE_REASON_FROM_DEAL,
            vocab.INHERITANCE_REASON_DEAL_HAS_NO_BOOK,
            vocab.INHERITANCE_REASON_NO_DEAL,
            vocab.INHERITANCE_REASON_DEAL_MISSING,
        ]
        assert len(set(served)) == 4

    def test_the_vocabulary_names_both_filter_objects(self, client: TestClient):
        filters = client.get(f"{PREFIX}/vocabulary").json()["filters"]

        assert {one["object"] for one in filters["objects"]} == set(vocab.FILTER_OBJECTS)

    def test_the_vocabulary_names_both_conjunctions(self, client: TestClient):
        filters = client.get(f"{PREFIX}/vocabulary").json()["filters"]

        assert {one["mode"] for one in filters["match_modes"]} == set(vocab.FILTER_MATCH_MODES)
        assert filters["group_quote"] == vocab.FILTER_GROUP_QUOTE

    def test_the_vocabulary_serves_every_reason_and_its_code(self, client: TestClient):
        assignments = client.get(f"{PREFIX}/vocabulary").json()["assignments"]

        assert {one["reason"] for one in assignments} == set(vocab.ASSIGNMENT_REASONS)
        assert all(one["label"] for one in assignments)

    def test_the_vocabulary_marks_the_writing_reasons(self, client: TestClient):
        served = client.get(f"{PREFIX}/vocabulary").json()["assignments"]
        writing = {one["reason"] for one in served if one["writes"]}

        assert writing == set(vocab.WRITING_REASONS)

    def test_the_vocabulary_serves_every_error_code(self, client: TestClient):
        served = client.get(f"{PREFIX}/vocabulary").json()["error_codes"]

        assert set(served) == set(vocab.ERROR_CODES)

    def test_the_inferences_are_served(self, client: TestClient):
        served = client.get(f"{PREFIX}/inferences").json()

        assert served["count"] == 8
        assert len(served["decisions"]) == 8

    def test_the_multiple_match_reading_is_served_on_its_own(self, client: TestClient):
        """Because its answer is "nothing was written", which needs the evidence beside it."""
        reading = client.get(f"{PREFIX}/inferences").json()["multiple_matches"]

        assert reading["code"] == vocab.ASSIGNMENT_NEEDS_CHOICE
        assert len(reading["sourced"]) == 2
        assert reading["jev_audit_id"]
        assert reading["why"]

    def test_the_override_reading_is_served_on_its_own(self, client: TestClient):
        """Because a page has to warn before it offers something destructive."""
        reading = client.get(f"{PREFIX}/inferences").json()["override_removes_lines"]

        assert reading["quote"] == vocab.LINE_ITEMS_REMOVED_QUOTE
        assert reading["chosen"] == "remove_and_name"

    def test_every_decision_names_its_rejected_alternative(self, client: TestClient):
        """Otherwise a reader cannot tell a judgement from an accident."""
        for decision in client.get(f"{PREFIX}/inferences").json()["decisions"]:
            assert decision["rejected_because"], decision["id"]
            assert len(decision["options"]) >= 2, decision["id"]
            assert decision["chosen"] in decision["options"], decision["id"]


# --------------------------------------------------------------------------- #
# every audit row names a path the app serves
# --------------------------------------------------------------------------- #


class TestTheAuditRows:
    def _audit_sources(self, store: RecordStore) -> list[tuple[str, str]]:
        """Every (method, path) pair the audit trail names, split from the stored string.

        A source is stored as ``"POST /api/WF-088/deals/d1/assign"``, so it has to be
        split before the method can be matched against the route table. Comparing the
        whole string against a served route would fail on every row for a reason that
        has nothing to do with the defect this test is looking for.
        """
        pairs: list[tuple[str, str]] = []
        for row in store.audit(limit=500):
            source = row.get("source")
            if not source:
                continue
            method, _, path = source.partition(" ")
            pairs.append((method, path))
        return pairs

    def test_every_audit_row_names_a_served_route(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """The check worth having here, and the reason this module passes ``source``.

        A feature's audit log kept naming a path the app had stopped serving in this
        codebase before. Nothing else in the product would have caught it: the routes
        answer, the tests pass, and the log points at nothing.
        """
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)
        run(client)
        client.post(
            f"{PREFIX}/deals/d1/price-book",
            json={"price_book": BOOK_PARTNER},
            params={"room_id": "room_a", "actor": "sam"},
        )
        run(client, trigger=vocab.TRIGGER_UPDATE)

        served = _served_routes(client)
        pairs = [pair for pair in self._audit_sources(store) if pair[1].startswith(PREFIX)]

        assert pairs, "the routes wrote no audit row naming their own prefix"
        for method, path in pairs:
            assert _matches_a_served_route(method, path, served), f"{method} {path}"

    def test_the_audit_rows_name_the_concrete_deal(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """The id is substituted into the path, so the row is clickable back to the request."""
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)
        run(client)

        pairs = self._audit_sources(store)

        assert ("POST", f"{PREFIX}/deals/d1/assign") in pairs
        assert ("POST", f"{PREFIX}/rules") in pairs

    def test_the_audit_trail_records_the_deal_itself(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """The write onto the deal is audited in the same transaction as the change."""
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)
        run(client)

        actions = [
            (row["action"], row["source"])
            for row in store.audit(limit=500)
            if row["collection"] == vocab.SOURCE_DEALS
        ]

        assert ("update", f"POST {PREFIX}/deals/d1/assign") in actions

    def test_the_removed_line_item_is_audited(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """A destructive write has to be as traceable as the assignment that caused it."""
        make_deal(store, segment="enterprise")
        save_rule(client, rule_body)
        run(client)
        line = store.create(
            vocab.LINE_ITEMS,
            {"deal_id": "d1", "sku": "SEAT-STD"},
            room_id="room_a",
            actor="dana",
            source="fixture",
        )
        client.post(
            f"{PREFIX}/deals/d1/price-book",
            json={"price_book": BOOK_PARTNER},
            params={"room_id": "room_a", "actor": "sam"},
        )

        deletes = [
            row
            for row in store.audit(limit=500)
            if row["action"] == "delete" and row["record_id"] == line["id"]
        ]

        assert deletes
        assert deletes[0]["source"] == f"POST {PREFIX}/deals/d1/price-book"


# --------------------------------------------------------------------------- #
# the demo
# --------------------------------------------------------------------------- #


class TestTheSeedOverHttp:
    def test_a_seeded_deal_is_readable_through_the_routes(
        self, client: TestClient, store: RecordStore
    ):
        """The seed writes through the engine, so the routes answer on the demo data."""
        import importlib

        module = importlib.import_module(
            "dsr.features.WF-088_auto_assign_the_correct_price_book_or_price"
        )
        from dsr.api import app

        module.seed(app.state.db, {"room_ids": [("room_a", "Northwind")], "now": None, "rng": None})

        deals = store.list(vocab.SOURCE_DEALS, limit=50)
        assert deals

        for record in deals:
            answer = client.get(f"{PREFIX}/deals/{record['id']}/price-book")
            assert answer.status_code == 200, answer.text

    def test_the_seeded_rules_are_readable_through_the_routes(
        self, client: TestClient, store: RecordStore
    ):
        import importlib

        module = importlib.import_module(
            "dsr.features.WF-088_auto_assign_the_correct_price_book_or_price"
        )
        from dsr.api import app

        module.seed(app.state.db, {"room_ids": [("room_a", "Northwind")], "now": None, "rng": None})

        listed = client.get(f"{PREFIX}/rules").json()

        assert listed["count"] == 7
        # The three researched modes are all present in the demo, so a reviewer can see
        # each one without configuring anything.
        board = client.get(f"{PREFIX}/summary").json()
        assert board["by_outcome"][vocab.ASSIGNMENT_NEEDS_CHOICE] >= 1
        assert board["by_outcome"][vocab.ASSIGNMENT_NO_AUTO_ASSIGN] >= 1
        assert board["by_outcome"][vocab.ASSIGNMENT_CHANGED] >= 1
