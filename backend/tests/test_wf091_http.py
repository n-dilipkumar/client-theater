"""WF-091: the HTTP surface, over the routes the app actually mounts.

The domain rules are in ``test_wf091.py``. This file checks the parts a domain test
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
from dsr.quoting_proposals import quote_approval_vocabulary as vocab
from dsr.store import RecordStore
from fastapi.testclient import TestClient

PREFIX = "/api/WF-091"

#: The researched statuses, spelled as the vendor spells them.
DRAFT = vocab.STATE_DRAFT
PENDING = vocab.STATE_PENDING_APPROVAL
APPROVED = vocab.STATE_APPROVED
REJECTED = vocab.STATE_REJECTED


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
    """A rule that fires on a line item discounted above 25 percent."""
    return {
        "key": "deep-line-discount",
        "label": "Deep discount on any line item",
        "filters": [{"object": "line_item", "property": "discount", "operator": "gt", "value": 25}],
        "approvers": ["dana", "sam", "priya"],
        "requirement": "all",
        "approval_note": "Check the margin.",
    }


def make_quote(client: TestClient, store: RecordStore, quote_id: str = "q1", **data) -> str:
    """One quote as WF-086 writes it, and a line item for the filters to read."""
    payload = {"name": quote_id, vocab.HS_STATUS: DRAFT, "creator": "dana", **data}
    store.create(
        vocab.SOURCE_QUOTES,
        payload,
        record_id=quote_id,
        room_id="room_a",
        actor="dana",
        source="fixture",
    )
    if data.pop("_with_line", True):
        store.create(
            "wf086_line_item",
            {"quote_id": quote_id, "sku": "SEAT-STD", "discount": data.get("discount", 40)},
            room_id="room_a",
            actor="dana",
            source="fixture",
        )
    return quote_id


def enrol(client: TestClient, rule: dict, **overrides) -> dict:
    """Save a rule and submit a matching quote. Returns the enrolment view."""
    client.post(f"{PREFIX}/rules", json=rule, params={"room_id": "room_a", "actor": "dana"})
    answer = client.post(
        f"{PREFIX}/quotes/q1/submit",
        json={"notes_to_approver": "Please review."},
        params={"room_id": "room_a", "actor": "dana"},
    )
    assert answer.status_code == 201, answer.text
    return answer.json()["enrolment"]


def _matches_a_served_route(method: str, path: str, served: set) -> bool:
    """Whether an audit source names a path the app serves.

    An audit row records the concrete request path, so a row written by
    ``POST /api/WF-091/quotes/q1/share`` carries ``q1`` where the route table carries
    ``{quote_id}``. Comparing the two literally would fail on every row this workflow
    writes, so a path parameter is matched by position.

    The check is deliberately not loosened to a prefix test. A row naming
    ``/api/WF-091/quotes/q1/share`` must be matched by a route that is really
    mounted and really accepts that method, because the defect this guards against is
    an audit log pointing at a path the app stopped serving.
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


# --------------------------------------------------------------------------- #
# the router is discovered and mounted
# --------------------------------------------------------------------------- #


class TestDiscovery:
    def test_the_feature_is_registered(self, client: TestClient):
        """The host finds the module by walking the features package. Nothing registers it."""
        listed = client.get("/api/features").json()
        entry = next(one for one in listed["features"] if one["ticket"] == "WF-091")

        assert entry["prefix"] == PREFIX
        assert entry["loaded"] is True

    def test_the_feature_is_not_in_the_failed_list(self, client: TestClient):
        listed = client.get("/api/features").json()

        assert [one for one in listed["failed"] if one["ticket"] == "WF-091"] == []

    def test_the_whole_surface_answers(self, client: TestClient):
        """A route that 404s here would be a route no reviewer could ever see."""
        for path, params in (
            ("/vocabulary", {}),
            ("/inferences", {}),
            ("/rules", {}),
            ("/summary", {}),
            ("/notifications", {}),
            ("/requests", {}),
        ):
            answer = client.get(f"{PREFIX}{path}", params=params)
            assert answer.status_code == 200, f"{path}: {answer.text}"

    def test_the_vocabulary_carries_the_researched_numbers(self, client: TestClient):
        data = client.get(f"{PREFIX}/vocabulary").json()

        assert data["approvers"]["max"] == 10
        assert {row["requirement"] for row in data["approvers"]["requirements"]} == {"all", "any"}
        assert data["unlock_target_states"] == ["DRAFT", "PENDING_APPROVAL", "REJECTED"]

    def test_the_inference_route_serves_the_self_approval_reading(self, client: TestClient):
        data = client.get(f"{PREFIX}/inferences").json()

        assert data["self_approval"]["chosen"] == "enrolment_removal"
        assert data["self_approval"]["jev_audit_id"] == "jev-20261005T064607-13024-67413"
        assert data["count"] == len(data["decisions"])


# --------------------------------------------------------------------------- #
# the rule builder
# --------------------------------------------------------------------------- #


class TestTheRuleBuilder:
    def test_a_rule_is_saved(self, client: TestClient, store: RecordStore, rule_body: dict):
        answer = client.post(
            f"{PREFIX}/rules", json=rule_body, params={"room_id": "room_a", "actor": "dana"}
        )

        assert answer.status_code == 201
        assert answer.json()["data"]["key"] == "deep-line-discount"
        assert answer.json()["data"]["enabled"] is True

    def test_a_rule_is_listed(self, client: TestClient, store: RecordStore, rule_body: dict):
        client.post(f"{PREFIX}/rules", json=rule_body, params={"room_id": "room_a"})

        listed = client.get(f"{PREFIX}/rules", params={"room_id": "room_a"}).json()

        assert listed["count"] == 1

    def test_the_switch_can_be_turned_off_and_back_on(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        created = client.post(
            f"{PREFIX}/rules", json=rule_body, params={"room_id": "room_a"}
        ).json()

        off = client.patch(
            f"{PREFIX}/rules/{created['id']}", json={"enabled": False}, params={"actor": "dana"}
        )
        on = client.patch(
            f"{PREFIX}/rules/{created['id']}", json={"enabled": True}, params={"actor": "dana"}
        )

        assert off.json()["data"]["enabled"] is False
        assert on.json()["data"]["enabled"] is True

    def test_a_toggle_does_not_reset_the_filters(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """A half-applied toggle is a rule that reads as enabled and matches nothing."""
        created = client.post(
            f"{PREFIX}/rules", json=rule_body, params={"room_id": "room_a"}
        ).json()

        patched = client.patch(f"{PREFIX}/rules/{created['id']}", json={"enabled": False}).json()

        assert patched["data"]["filters"] == created["data"]["filters"]

    def test_a_rule_with_no_approver_is_a_422(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        rule_body["approvers"] = []

        answer = client.post(f"{PREFIX}/rules", json=rule_body, params={"room_id": "room_a"})

        assert answer.status_code == 422
        assert answer.json()["error"] == "rule_needs_at_least_one_approver"

    def test_an_eleventh_approver_is_a_422_naming_the_cap(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        rule_body["approvers"] = [f"user{index}" for index in range(11)]

        answer = client.post(f"{PREFIX}/rules", json=rule_body, params={"room_id": "room_a"})

        assert answer.status_code == 422
        assert "up to 10 approvers" in answer.json()["errors"]["approvers"]

    def test_a_rule_with_no_filter_is_a_422(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        rule_body["filters"] = []

        answer = client.post(f"{PREFIX}/rules", json=rule_body, params={"room_id": "room_a"})

        assert answer.status_code == 422
        assert answer.json()["error"] == "rule_needs_a_filter"

    def test_an_unknown_operator_is_a_422(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        rule_body["filters"][0]["operator"] = "roughly"

        answer = client.post(f"{PREFIX}/rules", json=rule_body, params={"room_id": "room_a"})

        assert answer.status_code == 422
        assert answer.json()["error"] == "unknown_operator"

    def test_a_numeric_filter_needs_a_number(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        rule_body["filters"][0]["value"] = "a lot"

        answer = client.post(f"{PREFIX}/rules", json=rule_body, params={"room_id": "room_a"})

        assert answer.status_code == 422
        assert answer.json()["error"] == "numeric_filter_value_is_not_a_number"

    def test_a_refusal_carries_the_field_it_applies_to(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """A page puts each message beside the input that caused it."""
        rule_body["approvers"] = []

        errors = client.post(
            f"{PREFIX}/rules", json=rule_body, params={"room_id": "room_a"}
        ).json()["errors"]

        assert "approvers" in errors

    def test_an_unknown_rule_is_a_404(self, client: TestClient, store: RecordStore):
        answer = client.get(f"{PREFIX}/rules/no-such-rule")

        assert answer.status_code == 404
        assert answer.json()["error"] == "unknown_approval_rule"

    def test_a_deleted_rule_is_gone(self, client: TestClient, store: RecordStore, rule_body: dict):
        created = client.post(
            f"{PREFIX}/rules", json=rule_body, params={"room_id": "room_a"}
        ).json()

        client.delete(f"{PREFIX}/rules/{created['id']}", params={"actor": "dana"})

        assert client.get(f"{PREFIX}/rules/{created['id']}").status_code == 404


# --------------------------------------------------------------------------- #
# conditions and state
# --------------------------------------------------------------------------- #


class TestConditionsAndState:
    def test_the_conditions_panel_says_why_approval_is_required(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_quote(client, store, discount=40)
        client.post(f"{PREFIX}/rules", json=rule_body, params={"room_id": "room_a"})

        data = client.get(f"{PREFIX}/quotes/q1/conditions").json()

        assert data["required"] is True
        assert data["rules"][0]["matched_filters"][0]["actual"] == 40

    def test_the_panel_names_the_removed_creator(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_quote(client, store, discount=40)
        client.post(f"{PREFIX}/rules", json=rule_body, params={"room_id": "room_a"})

        data = client.get(f"{PREFIX}/quotes/q1/conditions").json()

        assert data["rules"][0]["removed_approvers"] == ["dana"]
        assert data["rules"][0]["approvers"] == ["sam", "priya"]

    def test_the_panel_says_when_no_approval_is_required(
        self, client: TestClient, store: RecordStore
    ):
        make_quote(client, store, discount=1)

        data = client.get(f"{PREFIX}/quotes/q1/conditions").json()

        assert data["required"] is False
        assert data["rules"] == []

    def test_the_state_route_reports_both_statuses(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """The quote's own hs_status is the older fact and the enrolment is the newer one."""
        make_quote(client, store, discount=40)
        enrol(client, rule_body)

        data = client.get(f"{PREFIX}/quotes/q1/state").json()

        assert data["stated_status"] == DRAFT
        assert data["effective_status"] == PENDING
        assert data["authority"] == "approval_enrolment"

    def test_the_state_route_carries_the_three_unlock_targets(
        self, client: TestClient, store: RecordStore
    ):
        make_quote(client, store)

        data = client.get(f"{PREFIX}/quotes/q1/state").json()

        assert data["unlock_targets"] == ["DRAFT", "PENDING_APPROVAL", "REJECTED"]

    def test_an_unknown_quote_is_a_404(self, client: TestClient, store: RecordStore):
        answer = client.get(f"{PREFIX}/quotes/no-such-quote/conditions")

        assert answer.status_code == 404
        assert answer.json()["error"] == "unknown_quote"


# --------------------------------------------------------------------------- #
# submitting
# --------------------------------------------------------------------------- #


class TestSubmitting:
    def test_a_matching_quote_is_enrolled_as_pending(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_quote(client, store, discount=40)
        created = enrol(client, rule_body)

        assert created["status"] == PENDING
        assert created["approvers"] == ["sam", "priya"]

    def test_the_notes_to_approver_are_kept(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """Step four: click Request approval, add Notes to approver."""
        make_quote(client, store, discount=40)
        client.post(f"{PREFIX}/rules", json=rule_body, params={"room_id": "room_a"})

        answer = client.post(
            f"{PREFIX}/quotes/q1/submit",
            json={"notes_to_approver": "This one is urgent."},
            params={"room_id": "room_a", "actor": "dana"},
        )

        assert answer.json()["enrolment"]["notes_to_approver"] == "This one is urgent."

    def test_the_approval_note_from_the_rule_is_copied_onto_the_enrolment(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """Step three: enter With this approval note."""
        make_quote(client, store, discount=40)

        assert enrol(client, rule_body)["data"][vocab.APPROVAL_NOTE] == "Check the margin."

    def test_a_quote_matching_nothing_says_so_rather_than_creating_a_request(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_quote(client, store, discount=5)
        client.post(f"{PREFIX}/rules", json=rule_body, params={"room_id": "room_a"})

        answer = client.post(
            f"{PREFIX}/quotes/q1/submit", params={"room_id": "room_a", "actor": "dana"}
        )

        assert answer.json()["enrolled"] is False
        assert answer.json()["reason"] == "no_rule_matched"

    def test_an_inactive_rule_says_turn_the_switch_on(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_quote(client, store, discount=40)
        created = client.post(
            f"{PREFIX}/rules", json=rule_body, params={"room_id": "room_a"}
        ).json()
        client.patch(f"{PREFIX}/rules/{created['id']}", json={"enabled": False})

        answer = client.post(
            f"{PREFIX}/quotes/q1/submit", params={"room_id": "room_a", "actor": "dana"}
        )

        assert answer.json()["reason"] == "matched_rule_is_inactive"
        assert "switch is off" in answer.json()["explanation"]

    def test_the_publish_trigger_enrols_the_quote(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """Enrolment fires on the publish attempt, not only on Request approval."""
        make_quote(client, store, discount=40)
        client.post(f"{PREFIX}/rules", json=rule_body, params={"room_id": "room_a"})

        answer = client.post(
            f"{PREFIX}/quotes/q1/submit",
            params={"room_id": "room_a", "actor": "dana", "trigger": "publish"},
        )

        assert answer.json()["enrolment"]["status"] == PENDING
        assert answer.json()["enrolment"]["trigger"] == "publish"

    def test_an_unknown_trigger_is_a_422(self, client: TestClient, store: RecordStore):
        make_quote(client, store)

        answer = client.post(
            f"{PREFIX}/quotes/q1/submit",
            params={"room_id": "room_a", "actor": "dana", "trigger": "whenever"},
        )

        assert answer.status_code == 422
        assert answer.json()["error"] == "unknown_trigger"


# --------------------------------------------------------------------------- #
# deciding
# --------------------------------------------------------------------------- #


class TestDeciding:
    def test_one_approval_under_an_at_least_one_rule_settles_it(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        rule_body["requirement"] = "any"
        make_quote(client, store, discount=40)
        created = enrol(client, rule_body)

        answer = client.post(
            f"{PREFIX}/requests/{created['id']}/decide",
            json={"decision": "approve", "message": "Fine."},
            params={"approver": "sam", "actor": "sam"},
        )

        assert answer.status_code == 200
        assert answer.json()["state"] == APPROVED

    def test_an_all_rule_waits_for_everybody(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_quote(client, store, discount=40)
        created = enrol(client, rule_body)

        answer = client.post(
            f"{PREFIX}/requests/{created['id']}/decide",
            json={"decision": "approve"},
            params={"approver": "sam"},
        )

        assert answer.json()["state"] == PENDING
        assert answer.json()["tally"]["outstanding"] == ["priya"]

    def test_the_second_approval_settles_an_all_rule(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_quote(client, store, discount=40)
        created = enrol(client, rule_body)
        client.post(
            f"{PREFIX}/requests/{created['id']}/decide",
            json={"decision": "approve"},
            params={"approver": "sam"},
        )

        answer = client.post(
            f"{PREFIX}/requests/{created['id']}/decide",
            json={"decision": "approve"},
            params={"approver": "priya"},
        )

        assert answer.json()["state"] == APPROVED
        assert answer.json()["shareable"] is True

    def test_a_change_request_ends_the_round_as_rejected(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_quote(client, store, discount=40)
        created = enrol(client, rule_body)

        answer = client.post(
            f"{PREFIX}/requests/{created['id']}/decide",
            json={"decision": "request_changes", "message": "Cut it to 15 percent."},
            params={"approver": "sam"},
        )

        assert answer.json()["state"] == REJECTED
        assert answer.json()["activity"] == vocab.ACTIVITY_REJECTED

    def test_a_change_request_with_no_reason_is_a_422(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """A change request with nothing in it is not an instruction the seller can act on."""
        make_quote(client, store, discount=40)
        created = enrol(client, rule_body)

        answer = client.post(
            f"{PREFIX}/requests/{created['id']}/decide",
            json={"decision": "request_changes"},
            params={"approver": "sam"},
        )

        assert answer.status_code == 422
        assert answer.json()["error"] == "reason_required_to_request_changes"

    def test_somebody_outside_the_approver_list_is_a_403(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_quote(client, store, discount=40)
        created = enrol(client, rule_body)

        answer = client.post(
            f"{PREFIX}/requests/{created['id']}/decide",
            json={"decision": "approve"},
            params={"approver": "stranger"},
        )

        assert answer.status_code == 403
        assert answer.json()["error"] == "approver_is_not_on_this_request"

    def test_the_creator_cannot_approve_their_own_quote(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """Approvers can't approve their own quotes. The creator is off the list."""
        make_quote(client, store, discount=40)
        created = enrol(client, rule_body)

        answer = client.post(
            f"{PREFIX}/requests/{created['id']}/decide",
            json={"decision": "approve"},
            params={"approver": "dana"},
        )

        assert answer.status_code == 403
        assert answer.json()["error"] == "approver_is_not_on_this_request"

    def test_a_second_decision_on_a_settled_request_is_a_409(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        rule_body["requirement"] = "any"
        make_quote(client, store, discount=40)
        created = enrol(client, rule_body)
        client.post(
            f"{PREFIX}/requests/{created['id']}/decide",
            json={"decision": "approve"},
            params={"approver": "sam"},
        )

        answer = client.post(
            f"{PREFIX}/requests/{created['id']}/decide",
            json={"decision": "approve"},
            params={"approver": "priya"},
        )

        assert answer.status_code == 409
        assert answer.json()["error"] == "quote_is_not_pending"

    def test_an_unknown_decision_is_a_422(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_quote(client, store, discount=40)
        created = enrol(client, rule_body)

        answer = client.post(
            f"{PREFIX}/requests/{created['id']}/decide",
            json={"decision": "shrug"},
            params={"approver": "sam"},
        )

        assert answer.status_code == 422
        assert answer.json()["error"] == "unknown_decision"

    def test_an_unknown_request_is_a_404(self, client: TestClient, store: RecordStore):
        answer = client.post(
            f"{PREFIX}/requests/no-such-request/decide", json={"decision": "approve"}
        )

        assert answer.status_code == 404
        assert answer.json()["error"] == "unknown_approval_request"

    def test_a_re_submission_starts_a_fresh_round(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """Every approver approves again when the quote is re-submitted."""
        make_quote(client, store, discount=40)
        created = enrol(client, rule_body)
        client.post(
            f"{PREFIX}/requests/{created['id']}/decide",
            json={"decision": "request_changes", "message": "No."},
            params={"approver": "sam"},
        )

        again = client.post(
            f"{PREFIX}/quotes/q1/submit", params={"room_id": "room_a", "actor": "dana"}
        ).json()

        assert again["enrolment"]["id"] != created["id"]
        assert all(one["decision"] is None for one in again["enrolment"]["decisions"])
        assert again["enrolment"]["status"] == PENDING


# --------------------------------------------------------------------------- #
# the share gate
# --------------------------------------------------------------------------- #


class TestTheShareGate:
    def test_a_share_before_approval_is_a_409_quoting_the_research(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """Only on approval can the quote be Share-d and sent to the buyer."""
        make_quote(client, store, discount=40)
        enrol(client, rule_body)

        answer = client.post(
            f"{PREFIX}/quotes/q1/share", params={"room_id": "room_a", "actor": "dana"}
        )

        assert answer.status_code == 409
        assert "only on approval" in answer.json()["detail"]

    def test_a_share_after_approval_succeeds(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        rule_body["requirement"] = "any"
        make_quote(client, store, discount=40)
        created = enrol(client, rule_body)
        client.post(
            f"{PREFIX}/requests/{created['id']}/decide",
            json={"decision": "approve"},
            params={"approver": "sam"},
        )

        answer = client.post(
            f"{PREFIX}/quotes/q1/share", params={"room_id": "room_a", "actor": "dana"}
        )

        assert answer.status_code == 200
        assert answer.json()["state"] == "SHARED"

    def test_a_refused_share_is_written_to_the_activity_log(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_quote(client, store, discount=40)
        enrol(client, rule_body)

        client.post(f"{PREFIX}/quotes/q1/share", params={"room_id": "room_a", "actor": "dana"})
        log = client.get(f"{PREFIX}/quotes/q1/activity").json()

        assert vocab.ACTIVITY_SHARE_REFUSED in [one["activity"] for one in log["activity"]]

    def test_a_share_does_not_write_to_the_quote(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """The quote belongs to WF-086. This workflow reads it and never writes it."""
        rule_body["requirement"] = "any"
        make_quote(client, store, discount=40)
        created = enrol(client, rule_body)
        client.post(
            f"{PREFIX}/requests/{created['id']}/decide",
            json={"decision": "approve"},
            params={"approver": "sam"},
        )
        client.post(f"{PREFIX}/quotes/q1/share", params={"room_id": "room_a", "actor": "dana"})

        reread = store.require("q1")["data"]

        assert reread[vocab.HS_STATUS] == DRAFT


# --------------------------------------------------------------------------- #
# reading the queue
# --------------------------------------------------------------------------- #


class TestReadingTheQueue:
    def test_requests_can_be_filtered_to_pending(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """The researched quotes index page filtered to Pending approval."""
        make_quote(client, store, discount=40)
        enrol(client, rule_body)

        listed = client.get(f"{PREFIX}/requests", params={"status": PENDING}).json()

        assert listed["count"] == 1
        assert listed["requests"][0]["status"] == PENDING

    def test_requests_can_be_filtered_to_one_approver(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_quote(client, store, discount=40)
        enrol(client, rule_body)

        listed = client.get(f"{PREFIX}/requests", params={"approver": "sam"}).json()

        assert listed["count"] == 1
        assert listed["requests"][0]["for_you"]["can_decide"] is True

    def test_an_approver_who_is_not_on_it_gets_nothing(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_quote(client, store, discount=40)
        enrol(client, rule_body)

        assert (
            client.get(f"{PREFIX}/requests", params={"approver": "stranger"}).json()["count"] == 0
        )

    def test_one_request_is_readable_with_its_conditions(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_quote(client, store, discount=40)
        created = enrol(client, rule_body)

        data = client.get(f"{PREFIX}/requests/{created['id']}").json()["request"]

        assert data["conditions"]["total"] == 1
        assert data["conditions"]["required"] is True

    def test_the_activity_log_carries_the_sourced_names(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_quote(client, store, discount=40)
        enrol(client, rule_body)

        names = [
            one["activity"] for one in client.get(f"{PREFIX}/quotes/q1/activity").json()["activity"]
        ]

        assert vocab.ACTIVITY_REQUESTED in names

    def test_notifications_are_recorded_and_never_claim_delivery(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_quote(client, store, discount=40)
        enrol(client, rule_body)

        rows = client.get(f"{PREFIX}/notifications", params={"quote_id": "q1"}).json()[
            "notifications"
        ]

        assert rows
        for row in rows:
            assert row["delivered"] is False
            assert row["dispatched_by"] == "recorded"

    def test_the_board_counts_the_pending_queue(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_quote(client, store, discount=40)
        enrol(client, rule_body)

        board = client.get(f"{PREFIX}/summary", params={"room_id": "room_a"}).json()

        assert board["by_state"][PENDING] == 1
        assert board["awaiting_an_approver"] == 1
        assert board["rules"] == 1

    def test_the_board_carries_the_self_approval_reading(
        self, client: TestClient, store: RecordStore
    ):
        board = client.get(f"{PREFIX}/summary").json()

        assert board["self_approval"]["jev_audit_id"] == "jev-20261005T064607-13024-67413"


# --------------------------------------------------------------------------- #
# the audit rows name real routes
# --------------------------------------------------------------------------- #


class TestTheAuditRows:
    def test_every_audit_row_names_a_path_this_app_serves(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """The defect that shipped before: an audit log naming a path the app stopped serving.

        The registry is the authority on what is mounted, so the comparison is against
        the route table the host built rather than against a list written here.
        """
        make_quote(client, store, discount=40)
        created = enrol(client, rule_body)
        client.post(
            f"{PREFIX}/requests/{created['id']}/decide",
            json={"decision": "approve"},
            params={"approver": "sam"},
        )
        client.post(f"{PREFIX}/quotes/q1/share", params={"actor": "dana"})

        served = {
            (method, route["path"])
            for feature in client.get("/api/features").json()["features"]
            for route in feature["routes"]
            for method in route["methods"]
        }
        entries = client.get(
            "/api/audit",
            params={"limit": 1000},
        ).json()["entries"]
        # The source is "METHOD /path", so the prefix is matched inside it rather than
        # at the start of the string.
        mine = [one for one in entries if PREFIX in str(one.get("source") or "")]

        assert mine, "the routes wrote no audit row"
        for entry in mine:
            method, _, path = str(entry["source"]).partition(" ")
            assert _matches_a_served_route(method, path, served), entry["source"]

    def test_a_refused_share_still_names_a_route(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        """A refusal writes the activity row too, so its source is an audit row as well."""
        make_quote(client, store, discount=40)
        enrol(client, rule_body)

        client.post(f"{PREFIX}/quotes/q1/share", params={"actor": "dana"})

        sources = [
            one["source"]
            for one in client.get("/api/audit", params={"limit": 1000}).json()["entries"]
            if one["collection"] == vocab.ACTIVITIES
        ]
        assert f"POST {PREFIX}/quotes/q1/share" in sources

    def test_the_submit_row_names_the_submit_route(
        self, client: TestClient, store: RecordStore, rule_body: dict
    ):
        make_quote(client, store, discount=40)
        enrol(client, rule_body)

        sources = [
            one["source"]
            for one in client.get("/api/audit", params={"limit": 1000}).json()["entries"]
            if one["collection"] == vocab.APPROVAL_REQUESTS
        ]

        assert f"POST {PREFIX}/quotes/q1/submit" in sources
