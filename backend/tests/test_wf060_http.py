"""WF-060: the HTTP surface, and the contracts that hold across it.

The domain rules are in ``test_wf060.py``. This file covers what only a request can
check, and it is organised by the thing being defended:

``the audit-source rule``
    Every ``source=`` this workflow records names a route the host actually mounted.
    This is the test the build brief asks for by name, and it is checked against the
    routes the host really mounted rather than a list written beside the test.

``error mapping``
    Every status a caller can meet, and the research's own codes where the research
    gives them. ``409`` for a consent page that is off and ``404`` for an organiser
    with no user are Gong's, not ours, so a caller comparing our refusal to the
    vendor's does not need two codebooks.

``the researched reads``
    The vocabulary, the decision record, and the integration status. The decision
    record matters most: the research requires the derivation to be recorded rather
    than assumed, and a record nobody can read is not a record.

``the full cycle``
    A booking from open to recorded, and a declined booking that cannot be recorded,
    driven entirely over HTTP.

``discovery``
    That the router mounts by discovery alone, that ``dsr/api.py`` was not edited,
    and that this prefix does not collide with anything already mounted.
"""

from __future__ import annotations

import ast
import importlib
from collections.abc import Iterator
from pathlib import Path

import dsr.features as host
import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.recording_consent import decisions, vocabulary as vocab
from dsr.store import RecordStore
from fastapi.testclient import TestClient

PREFIX = "/api/wf-060"
FEATURE_ID = "wf-060-auto-join-and-record-with-consent"
FEATURE_MODULE = "dsr.features.wf060_auto_join_and_record_with_consent"

ORGANIZER = "dana@northwind.example"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def _shared_client(tmp_path_factory) -> Iterator[TestClient]:
    """One application for the whole module.

    The lifespan in ``dsr/api.py`` only assigns ``app.state.db`` and
    ``app.state.store``, and ``dsr/deps.py`` reads ``app.state.store`` on every
    request. So a test needs a fresh *database*, not a fresh *application*.
    """
    scratch = tmp_path_factory.mktemp("wf060-http")
    patch = pytest.MonkeyPatch()
    patch.setenv("DSR_DB_PATH", ":memory:")
    patch.setenv("DSR_AUDIT_DIR", str(scratch / "audit"))
    patch.setattr("dsr.api.FRONTEND_DIST", scratch / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    patch.undo()


@pytest.fixture()
def client(_shared_client: TestClient) -> Iterator[TestClient]:
    """The shared application, over a database this test owns alone."""
    db = AuditedDatabase()
    _shared_client.app.state.db = db
    _shared_client.app.state.store = RecordStore(db)
    _shared_client.app.dependency_overrides.clear()
    yield _shared_client
    db.close()


@pytest.fixture()
def store(client: TestClient) -> RecordStore:
    """The store the request just used, so the audit log can be read back."""
    return client.app.state.store


PROFILE = {
    "name": "Standard recording consent",
    "description": "This call will be recorded for note taking.",
    "consent_page_enabled": True,
    "enforce_consent_page": True,
    "allow_join_without_consent": True,
    "providers": {"zoom": "dynamic_link"},
    "default_provider": "zoom",
}


def make_profile(client: TestClient, **overrides) -> str:
    created = client.post(
        f"{PREFIX}/profiles", json={**PROFILE, **overrides}, params={"room_id": "room_a"}
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def make_user(
    client: TestClient, email: str = ORGANIZER, profile_id: str | None = None, **overrides
):
    body = {"email": email, "name": overrides.pop("name", email.split("@")[0]), **overrides}
    if profile_id:
        body["profile_id"] = profile_id
    created = client.post(f"{PREFIX}/users", json=body)
    assert created.status_code == 201, created.text
    return created.json()["id"]


def ready(client: TestClient, **overrides) -> str:
    """A profile and an organiser assigned to it, which is what a booking needs."""
    profile_id = make_profile(client, **overrides)
    make_user(client, profile_id=profile_id)
    return profile_id


BOOKING = {
    "booking_id": "b-1",
    "organizer_email": ORGANIZER,
    "title": "Northwind walkthrough",
    "start_time": "2026-10-05T09:00:00+00:00",
    "end_time": "2026-10-05T09:30:00+00:00",
    "invitees": [
        {"email": "buyer@northwind.example", "name": "Buyer"},
        {"email": "analyst@contoso.example", "name": "Analyst"},
    ],
}


def open_booking(client: TestClient, **overrides):
    """Open a booking in the room the profiles live in, which is how the page calls it."""
    return client.post(
        f"{PREFIX}/bookings", json={**BOOKING, **overrides}, params={"room_id": "room_a"}
    )


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #


class TestDiscovery:
    def test_the_router_is_mounted_by_discovery_alone(self, client: TestClient):
        """``dsr/api.py`` was not edited. The route resolves because the host walks
        ``dsr/features/`` and found this file."""
        assert client.get(f"{PREFIX}/summary").status_code == 200

    def test_the_registry_names_this_feature_with_its_prefix(self, client: TestClient):
        found = client.get(f"/api/features/{FEATURE_ID}").json()
        assert found["ticket"] == "WF-060"
        assert found["prefix"] == PREFIX
        assert found["exception_handlers"], "the error handlers were not registered"

    def test_the_prefix_cannot_collide_with_one_already_mounted(self):
        module = host.load_feature("wf060_auto_join_and_record_with_consent")
        mounted = {
            (method, route.path)
            for route in module.router.routes
            for method in route.methods
            if method not in ("HEAD", "OPTIONS")
        }
        for record in host.REGISTRY.features:
            if record.id == FEATURE_ID:
                continue
            for route in record.routes:
                for method in route["methods"]:
                    assert (method, route["path"]) not in mounted, (method, route["path"])

    def test_no_shared_file_names_this_feature(self):
        """If any shared file had been edited to register it, the guard would have
        refused the branch. This asserts the same thing from the other side: the
        feature's name appears in no shared file at all."""
        root = Path(__file__).resolve().parents[2]
        shared = [
            "backend/dsr/api.py",
            "backend/dsr/deps.py",
            "backend/dsr/store.py",
            "backend/dsr/db/audited.py",
            "backend/seed.py",
            "frontend/src/App.jsx",
            "frontend/src/main.jsx",
            "frontend/src/lib/api.js",
            "frontend/src/lib/features.js",
            "frontend/src/components/ui.jsx",
            "frontend/vite.config.js",
        ]
        for relative in shared:
            assert "wf060" not in (root / relative).read_text(encoding="utf-8"), relative


# --------------------------------------------------------------------------- #
# the audit-source rule
# --------------------------------------------------------------------------- #


class TestAuditSources:
    def test_every_source_this_router_records_names_a_mounted_route(self):
        """The rule the build brief asks for by name: the audit row must name the route
        that actually served the write, so it is checked against the routes the host
        really mounted rather than against a hand-written list."""
        module = host.load_feature("wf060_auto_join_and_record_with_consent")
        mounted = {
            f"{method} {route.path}"
            for route in module.router.routes
            for method in route.methods
            if method not in ("HEAD", "OPTIONS")
        }
        declared = {
            f"{method} {module.router.prefix}{path}" for method, path in _declared_sources()
        }
        assert declared, "no _source() call found, so this test is measuring nothing"
        assert declared <= mounted, sorted(declared - mounted)
        assert not _source_literals(), f"a source literal crept in: {_source_literals()}"

    def test_a_write_over_http_reached_the_audit_log_with_its_route(
        self, client: TestClient, store: RecordStore
    ):
        ready(client)
        open_booking(client)
        client.post(f"{PREFIX}/bookings/b-1/consent", params={"decision": "granted"})

        sources = {entry.get("source") for entry in store.audit(limit=200)}
        assert f"POST {PREFIX}/profiles" in sources
        assert f"POST {PREFIX}/users" in sources
        assert f"POST {PREFIX}/bookings" in sources
        assert f"POST {PREFIX}/bookings/{{booking_id}}/consent" in sources

    def test_a_refused_write_records_no_audit_row(self, client: TestClient, store: RecordStore):
        """A refusal is not a write. Recording one would put a row in the audit log for a
        change that never happened, and the audit log is the product guarantee."""
        make_profile(client, consent_page_enabled=False)
        make_user(client, profile_id=client.get(f"{PREFIX}/profiles").json()["profiles"][0]["id"])
        before = len(store.audit(limit=500))
        assert open_booking(client).status_code == 409
        assert len(store.audit(limit=500)) == before


def _source_literals() -> set[str]:
    """Any module-level string that *is* a route-shaped source.

    A ``source`` written as a literal is the defect this rule exists to prevent, so the
    check is on the shape of the value rather than on its name.
    """
    module = importlib.import_module(FEATURE_MODULE)
    prefixes = ("GET ", "POST ", "PATCH ", "PUT ", "DELETE ")
    return {
        value
        for value in vars(module).values()
        if isinstance(value, str) and value.startswith(prefixes)
    }


def _declared_sources() -> list[tuple[str, str]]:
    """Every ``_source("METHOD", "/path")`` the feature module writes.

    Read from the module's source rather than from a list kept beside it, so a new
    route that forgot to pass a source cannot pass this test by being absent from the
    list.
    """
    module = importlib.import_module(FEATURE_MODULE)
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "_source"):
            continue
        if len(node.args) == 2 and all(isinstance(a, ast.Constant) for a in node.args):
            found.append((node.args[0].value, node.args[1].value))
    return found


# --------------------------------------------------------------------------- #
# error mapping
# --------------------------------------------------------------------------- #


class TestErrorMapping:
    def test_an_invalid_profile_is_400_with_a_field_keyed_map(self, client: TestClient):
        response = client.post(f"{PREFIX}/profiles", json={"name": "", "locales": ["kl"]})
        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "consent_profile_invalid"
        # Each message lands next to the input that caused it.
        assert "name" in body["errors"]
        assert "locales" in body["errors"]

    def test_an_unknown_profile_is_404(self, client: TestClient):
        assert client.get(f"{PREFIX}/profiles/nope").status_code == 404

    def test_an_unknown_booking_is_404(self, client: TestClient):
        assert client.get(f"{PREFIX}/bookings/nope").status_code == 404

    def test_a_consent_page_that_is_off_is_409_the_vendors_own_code(self, client: TestClient):
        """Gong documents 409 for exactly this state, and reproducing the vendor's code
        means a caller comparing our refusal to Gong's does not need two codebooks."""
        profile_id = make_profile(client, consent_page_enabled=False)
        make_user(client, profile_id=profile_id)
        response = open_booking(client)
        assert response.status_code == 409
        assert response.json()["error"] == "consent_page_disabled"
        assert vocab.DOCUMENTED_ERRORS[409] in response.json()["detail"]

    def test_an_unmapped_organiser_is_404_the_vendors_own_code(self, client: TestClient):
        """organizerEmail resolves by user, so a stranger has no profile and no consent
        rule. Gong documents the same case as 404."""
        ready(client)
        response = open_booking(client, organizer_email="stranger@elsewhere.example")
        assert response.status_code == 404
        assert response.json()["error"] == "organizer_unmapped"
        assert vocab.DOCUMENTED_ERRORS[404] in response.json()["detail"]

    def test_joining_without_consent_where_the_profile_forbids_it_is_403(self, client: TestClient):
        """The participant did nothing wrong, so this is not a 400. It is a refusal to
        admit them, and the message names the switch that would allow it."""
        ready(client, allow_join_without_consent=False)
        open_booking(client)
        response = client.post(
            f"{PREFIX}/bookings/b-1/consent", params={"decision": "joined_without_consent"}
        )
        assert response.status_code == 403
        assert response.json()["error"] == "join_without_consent_refused"
        assert vocab.JOIN_WITHOUT_CONSENT_SWITCH in response.json()["detail"]

    def test_an_illegal_transition_is_409_and_names_what_was_available(self, client: TestClient):
        """The body carries the state and the alternatives, so a client can show the user
        which step is available rather than only that this one is not."""
        ready(client)
        open_booking(client)
        response = client.post(f"{PREFIX}/bookings/b-1/recording/start")
        assert response.status_code == 409
        body = response.json()
        assert body["error"] == "illegal_transition"
        assert body["state"] == "awaiting_consent"
        assert body["step"] == "start_recording"
        assert "grant_consent" in body["allowed"]

    def test_a_superseded_link_is_409_and_an_unissued_one_is_404(self, client: TestClient):
        """Two different facts, so two different codes. A caller can act on the second
        by issuing a new link; the first it cannot."""
        ready(client)
        assert client.get(f"{PREFIX}/bookings/b-1/link").status_code == 404

        opened = open_booking(client).json()
        first_meeting_id = opened["data"]["consent_link"]["meeting_id"]
        assert client.post(f"{PREFIX}/bookings/b-1/link").status_code == 200

        response = client.get(
            f"{PREFIX}/bookings/b-1/link", params={"meeting_id": first_meeting_id}
        )
        assert response.status_code == 409
        assert response.json()["error"] == "link_superseded"
        assert first_meeting_id in response.json()["detail"]

    def test_an_unknown_decision_is_400_and_names_the_legal_set(self, client: TestClient):
        ready(client)
        open_booking(client)
        response = client.post(f"{PREFIX}/bookings/b-1/consent", params={"decision": "maybe"})
        assert response.status_code == 400
        for decision in vocab.DECISIONS:
            assert decision in response.json()["errors"]["decision"]

    def test_no_route_answers_500_for_any_state(self, client: TestClient):
        """Every route, every refusal a caller can produce, no 5xx. A feature that 500s
        is a feature that takes the product offline for whoever hits it."""
        profile_id = make_profile(client, consent_page_enabled=False)
        make_user(client, profile_id=profile_id)
        calls = [
            ("GET", f"{PREFIX}/summary", None),
            ("GET", f"{PREFIX}/vocabulary", None),
            ("GET", f"{PREFIX}/decisions", None),
            ("GET", f"{PREFIX}/decisions/nope", None),
            ("GET", f"{PREFIX}/integration/status", None),
            ("GET", f"{PREFIX}/profiles", None),
            ("GET", f"{PREFIX}/profiles/nope", None),
            ("GET", f"{PREFIX}/users", None),
            ("GET", f"{PREFIX}/users/resolve", {"organizer_email": ORGANIZER}),
            ("GET", f"{PREFIX}/bookings", None),
            ("GET", f"{PREFIX}/bookings/nope", None),
            ("GET", f"{PREFIX}/bookings/nope/link", None),
            ("GET", f"{PREFIX}/bookings/nope/runs", None),
            ("GET", f"{PREFIX}/bookings/nope/emails", None),
            ("GET", f"{PREFIX}/consent/nope", None),
            ("POST", f"{PREFIX}/profiles", {}),
            ("PATCH", f"{PREFIX}/profiles/nope", {"name": "x"}),
            ("POST", f"{PREFIX}/profiles/nope/default", None),
            ("GET", f"{PREFIX}/profiles/nope/consent-page", None),
            ("POST", f"{PREFIX}/users", {"email": "x@y.example", "name": "X"}),
            ("POST", f"{PREFIX}/bookings", {}),
            ("POST", f"{PREFIX}/bookings/nope/consent", None),
            ("POST", f"{PREFIX}/bookings/nope/recording/start", None),
            ("POST", f"{PREFIX}/bookings/nope/recording/finish", None),
            ("POST", f"{PREFIX}/bookings/nope/recording/cancel", None),
            ("POST", f"{PREFIX}/bookings/nope/link", None),
            ("POST", f"{PREFIX}/bookings/nope/precall-email", None),
        ]
        for method, path, params in calls:
            kwargs = {"params": params} if params is not None else {}
            if method in ("POST", "PATCH") and params is not None:
                response = client.request(method, path, json=params, **kwargs)
            elif params:
                response = client.request(method, path, **kwargs)
            else:
                response = client.request(method, path)
            assert response.status_code < 500, (method, path, response.status_code, response.text)


# --------------------------------------------------------------------------- #
# the researched reads
# --------------------------------------------------------------------------- #


class TestResearchedReads:
    def test_the_vocabulary_serves_the_sets_the_rules_validate_against(self, client: TestClient):
        """Served rather than duplicated in the frontend, so a form cannot drift from the
        rules."""
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert set(body["providers"]) == set(vocab.PROVIDERS)
        assert body["link_kinds"] == list(vocab.LINK_KINDS)
        assert body["states"] == list(vocab.STATES)
        assert body["consent_states"] == list(vocab.CONSENT_STATES)
        assert body["recording_states"] == list(vocab.RECORDING_STATES)
        assert body["steps"] == list(decisions.STEPS)
        assert body["precall_window_minutes"] == [10, 20]
        assert body["profile_resolution_key"] == "organizer_email"
        assert body["recording_bot_email"] == vocab.RECORDING_BOT_EMAIL
        assert body["documented_errors"]["409"] == vocab.DOCUMENTED_ERRORS[409]

    def test_the_prompt_default_is_served_with_the_other_mode_available(self, client: TestClient):
        """The research offers both and chooses neither. The choice is visible, and the
        other mode is still legal, so the decision is recorded rather than disguised as a
        restriction."""
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body["default_prompt_mode"] == vocab.DEFAULT_PROMPT_MODE
        assert "every_guest" in body["prompt_modes"]

    def test_the_decision_record_is_served_with_what_each_decision_rejected(
        self, client: TestClient
    ):
        """The research requires it: "must derive it and record the derivation, not assume
        it". A record nobody can read is not a record, so it is a route."""
        body = client.get(f"{PREFIX}/decisions").json()
        assert body["count"] >= 5
        required = {
            "id",
            "question",
            "decision",
            "evidence",
            "rejected",
            "cost_of_the_rejected_reading",
            "residual_risk",
            "surface",
        }
        for entry in body["decisions"]:
            assert required <= set(entry), entry["id"]

    def test_one_decision_is_readable_by_id(self, client: TestClient):
        response = client.get(f"{PREFIX}/decisions/wf060-enforcement-off-makes-the-page-advisory")
        assert response.status_code == 200
        body = response.json()
        assert "Enforce use of consent page" in body["question"]
        assert "still issued" in body["decision"]
        assert body["residual_risk"], "a decision with no residual risk recorded is not a record"

    def test_an_unknown_decision_id_is_400(self, client: TestClient):
        assert client.get(f"{PREFIX}/decisions/not-a-decision").status_code == 400

    def test_the_integration_status_names_the_three_ways_it_can_fail(self, client: TestClient):
        """The researched endpoint exists to "validate Gong meeting integration", and all
        three things that can make it fail are knowable without a call."""
        empty = client.get(f"{PREFIX}/integration/status").json()
        assert empty["ready"] is False
        assert [check["name"] for check in empty["checks"]] == [
            "consent_page_enabled",
            "users_mapped",
            "default_profile",
        ]
        # With nothing configured, the failure text is the vendor's own wording.
        assert empty["checks"][0]["detail"] == vocab.DOCUMENTED_ERRORS[409]
        assert empty["checks"][1]["detail"] == vocab.DOCUMENTED_ERRORS[404]

        profile_id = ready(client)
        client.post(f"{PREFIX}/profiles/{profile_id}/default")
        configured = client.get(f"{PREFIX}/integration/status").json()
        assert configured["ready"] is True
        assert configured["default_profile_id"] == profile_id
        assert all(check["ok"] for check in configured["checks"])


# --------------------------------------------------------------------------- #
# the full cycle
# --------------------------------------------------------------------------- #


class TestFullCycle:
    def test_a_granted_booking_reaches_recorded_over_http(self, client: TestClient):
        ready(client)
        assert open_booking(client).status_code == 201
        assert (
            client.post(
                f"{PREFIX}/bookings/b-1/consent", params={"decision": "granted"}
            ).status_code
            == 200
        )
        assert client.post(f"{PREFIX}/bookings/b-1/recording/start").status_code == 200
        finished = client.post(f"{PREFIX}/bookings/b-1/recording/finish")
        assert finished.status_code == 200

        data = finished.json()["data"]
        assert data["state"] == "recorded"
        assert data["recording_state"] == "complete"
        assert data["terminal"] is True

        runs = client.get(f"{PREFIX}/bookings/b-1/runs").json()
        assert [run["data"]["event"] for run in runs["runs"]] == [
            "opened",
            "granted",
            "start_recording",
            "finish_recording",
        ]

    def test_a_declined_booking_cannot_be_recorded(self, client: TestClient):
        """The seed's named non-success, driven over HTTP. A booking that requires consent
        and was refused must have no path to a recording."""
        ready(client)
        open_booking(client)
        declined = client.post(f"{PREFIX}/bookings/b-1/consent", params={"decision": "declined"})
        assert declined.json()["data"]["recording_state"] == "cancelled"

        assert client.post(f"{PREFIX}/bookings/b-1/recording/start").status_code == 409
        assert client.post(f"{PREFIX}/bookings/b-1/recording/finish").status_code == 409
        ended = client.post(f"{PREFIX}/bookings/b-1/recording/cancel")
        assert ended.json()["data"]["state"] == "cancelled"

    def test_a_decline_with_enforcement_off_still_records_the_call(self, client: TestClient):
        """The derived open point, over HTTP. The record carries both facts, so a reviewer
        sees a call that was recorded after a participant said no and why."""
        profile_id = ready(client, enforce_consent_page=False)
        assert profile_id
        opened = open_booking(client).json()["data"]
        assert opened["state"] == "scheduled"
        assert opened["recording_state"] == "armed"
        assert opened["enforced"] is False

        declined = client.post(
            f"{PREFIX}/bookings/b-1/consent", params={"decision": "declined"}
        ).json()["data"]
        assert declined["consent_state"] == "declined"
        assert declined["recording_state"] == "armed"

        assert client.post(f"{PREFIX}/bookings/b-1/recording/start").status_code == 200
        assert client.post(f"{PREFIX}/bookings/b-1/recording/finish").status_code == 200

    def test_the_participant_page_tells_the_participant_what_to_do(self, client: TestClient):
        """Never 403s. A closed booking answers with the page and a reason, because the
        participant did nothing wrong and a refusal would read as one."""
        ready(client)
        open_booking(client)
        page = client.get(f"{PREFIX}/consent/b-1").json()
        assert page["consent_required"] is True
        assert page["allowed_decisions"] == [
            "grant_consent",
            "decline_consent",
            "join_without_consent",
        ]
        assert page["recording_state"] == "blocked"
        assert page["profile"]["description"] == PROFILE["description"]
        assert page["profile"]["locales"] == ["en"]

    def test_the_participant_page_narrows_the_choices_when_the_join_is_forbidden(
        self, client: TestClient
    ):
        """`allowed_decisions` is computed from the machine and the profile together, so
        the page cannot offer a step the API would refuse."""
        ready(client, allow_join_without_consent=False)
        open_booking(client)
        page = client.get(f"{PREFIX}/consent/b-1").json()
        assert page["allow_join_without_consent"] is False
        assert page["allowed_decisions"] == ["grant_consent", "decline_consent"]

    def test_a_reissued_link_supersedes_the_previous_one_and_keeps_it_readable(
        self, client: TestClient
    ):
        """ "Each time you change this link, the previous link is disabled." As a state, so
        the audit row for the link that was replaced survives."""
        ready(client)
        opened = open_booking(client).json()["data"]
        first = opened["consent_link"]

        reissued = client.post(f"{PREFIX}/bookings/b-1/link").json()["data"]
        assert reissued["link_change_count"] == 1
        assert reissued["consent_link"]["link_state"] == "active"
        assert reissued["superseded_link"]["link_state"] == "superseded"
        assert (
            reissued["superseded_link"]["superseded_by"] == reissued["consent_link"]["meeting_id"]
        )
        # The old link is still on the record rather than deleted.
        assert reissued["superseded_link"]["meeting_url"] == first["meeting_url"]

        current = client.get(f"{PREFIX}/bookings/b-1/link")
        assert current.json()["meeting_id"] == reissued["consent_link"]["meeting_id"]

    def test_the_stored_record_carries_the_outbound_request_and_the_bot(self, client: TestClient):
        """ "the recording bot joins automatically". The bot is on the record, and so is the
        exact request the research quotes, so a reviewer can read the researched call
        without a network call having happened."""
        ready(client)
        data = open_booking(client).json()["data"]
        assert data["bot_invited"] == [vocab.RECORDING_BOT_EMAIL]
        request = data["outbound_request"]
        assert request["endpoint"] == vocab.MEETINGS_ENDPOINT
        assert request["scope"] == vocab.MEETING_CREATE_SCOPE
        assert set(request["body"]) == set(vocab.NEW_MEETING_REQUEST_FIELDS)
        assert data["profile_resolution_key"] == "organizer_email"

    def test_the_profile_resolves_by_organiser_email(self, client: TestClient):
        """The key the brief asks to be recorded, asserted over HTTP: two organisers in
        one room get two different profiles."""
        dana_profile = make_profile(client, name="Dana profile", is_default=True)
        sam_profile = make_profile(client, name="Sam profile")
        make_user(client, email="dana@northwind.example", profile_id=dana_profile)
        make_user(client, email="sam@northwind.example", profile_id=sam_profile)

        dana = client.get(
            f"{PREFIX}/users/resolve", params={"organizer_email": "DANA@Northwind.Example"}
        ).json()
        sam = client.get(
            f"{PREFIX}/users/resolve", params={"organizer_email": "sam@northwind.example"}
        ).json()
        assert dana["profile_id"] == dana_profile
        assert dana["source"] == "assigned"
        assert sam["profile_id"] == sam_profile
        # Case is folded, so the key is the address rather than the exact string.
        assert dana["user"]["email"] == "dana@northwind.example"

    def test_a_booking_whose_invitee_prevents_recording_is_refused(self, client: TestClient):
        """The researched third directory flag, refused before the invite goes out."""
        profile_id = make_profile(client)
        make_user(client, profile_id=profile_id)
        make_user(
            client,
            email="guest@blocked.example",
            name="Guest",
            blocks_recording=True,
        )
        response = open_booking(
            client,
            invitees=[*BOOKING["invitees"], {"email": "guest@blocked.example"}],
        )
        assert response.status_code == 400
        assert "guest@blocked.example" in response.json()["detail"]

    def test_the_can_record_read_tells_the_seller_before_the_booking(self, client: TestClient):
        profile_id = make_profile(client)
        make_user(client, profile_id=profile_id)
        make_user(client, email="quiet@northwind.example", name="Quiet", record_by_gong=False)
        blocked = client.get(f"{PREFIX}/organizers/quiet@northwind.example/can-record").json()
        assert blocked["can_record"] is False
        assert "record_by_gong" in blocked["reason"]
        assert (
            client.get(f"{PREFIX}/organizers/dana@northwind.example/can-record").json()[
                "can_record"
            ]
            is True
        )

    def test_the_summary_counts_what_a_reviewer_reads(self, client: TestClient):
        ready(client)
        open_booking(client, booking_id="b-1")
        open_booking(client, booking_id="b-2")
        client.post(f"{PREFIX}/bookings/b-2/consent", params={"decision": "granted"})
        client.post(f"{PREFIX}/bookings/b-2/recording/start")
        client.post(f"{PREFIX}/bookings/b-2/recording/finish")

        body = client.get(f"{PREFIX}/summary", params={"room_id": "room_a"}).json()
        assert body["bookings"] == 2
        assert body["by_state"]["awaiting_consent"] == 1
        assert body["by_state"]["recorded"] == 1
        assert body["recordings_blocked"] == 1
        assert body["recordings_complete"] == 1
        assert body["inconsistent"] == []
        assert body["resolution_key"] == "organizer_email"

    def test_the_precall_email_skip_is_200_and_carries_a_reason(self, client: TestClient):
        """A window that has not opened is not a failure. Refusing it would put a normal
        wait in the same place as a real fault."""
        ready(client)
        open_booking(client)
        response = client.post(f"{PREFIX}/bookings/b-1/precall-email")
        assert response.status_code == 200
        assert response.json()["sent"] is False
        assert response.json()["reason"] == "precall_email_disabled"
        assert client.get(f"{PREFIX}/bookings/b-1/emails").json()["count"] == 0

    def test_setting_a_default_clears_the_previous_one(self, client: TestClient):
        """Exactly one profile may be the default, or "which profile applies to an
        unassigned user" is unanswerable."""
        first = make_profile(client, name="First", is_default=True)
        second = make_profile(client, name="Second")
        assert client.post(f"{PREFIX}/profiles/{second}/default").status_code == 200

        rows = client.get(f"{PREFIX}/profiles").json()["profiles"]
        defaults = [row["id"] for row in rows if row["data"].get("is_default")]
        assert defaults == [second]
        assert first not in defaults

    def test_the_consent_page_preview_carries_what_step_3_named(self, client: TestClient):
        """ "Sets a default provider, company logo, and supported languages for the consent
        page." The preview is data, so what a reviewer reads is what a participant is
        consented against."""
        profile_id = ready(client, logo_url="https://cdn.example/logo.png", locales=["en", "de"])
        page = client.get(f"{PREFIX}/profiles/{profile_id}/consent-page").json()
        assert page["name"] == PROFILE["name"]
        assert page["description"] == PROFILE["description"]
        assert page["logo_url"] == "https://cdn.example/logo.png"
        assert page["locales"] == ["de", "en"]
        assert page["enforce_consent_page"] is True
        assert page["audio_prompt_suppressed"] is False

    def test_a_profile_patch_is_revalidated_whole(self, client: TestClient):
        """Turning the consent page on without a provider is refused, and the request that
        turns it on is rarely the request that adds the provider.

        Two patches rather than one, because `providers` and `default_provider` are two
        fields and clearing one alone leaves the other naming a provider the profile no
        longer has - which is a second error with its own message."""
        profile_id = make_profile(client, consent_page_enabled=False)

        # Clearing both together is valid while the page is off.
        cleared = client.patch(
            f"{PREFIX}/profiles/{profile_id}",
            json={"providers": None, "default_provider": None},
        )
        assert cleared.status_code == 200
        assert cleared.json()["data"]["providers"] == {}

        # Turning the page on with no provider behind it is refused, and the message
        # names the field rather than the switch.
        refused = client.patch(
            f"{PREFIX}/profiles/{profile_id}", json={"consent_page_enabled": True}
        )
        assert refused.status_code == 400
        assert "at least one provider is required" in refused.json()["errors"]["providers"]

        # Adding the provider and turning the page on in one patch is accepted, which is
        # what makes the revalidation useful rather than merely obstructive.
        accepted = client.patch(
            f"{PREFIX}/profiles/{profile_id}",
            json={
                "consent_page_enabled": True,
                "providers": {"webex": "static_link"},
                "default_provider": "webex",
            },
        )
        assert accepted.status_code == 200
        assert accepted.json()["data"]["default_provider"] == "webex"
