"""WF-068: the HTTP surface, and the contracts that hold across it.

The domain rules are in ``test_wf068.py``. This file covers what only a request
can check, and it is organised by the thing being defended:

``discovery``
    That the router mounts by discovery alone, that ``dsr/api.py`` was not edited,
    and that this prefix collides with nothing already mounted.

``the audit-source rule``
    Every ``source=`` this workflow records names a route the host actually
    mounted. This is the check the build brief asks for by name, and it is read
    from the routes the host really mounted rather than from a list written beside
    the test.

``error mapping``
    Every status a caller can meet, and the researched codes where the research
    gives them. ``403`` for an unlicensed assignee and ``409`` for a contact with
    no parent account are the point: neither is a ``400``, because in neither case
    is the thing to fix inside the request.

``the researched reads``
    The vocabulary, the derivation record, and the summary.

``the full cycle``
    Prep page to dry run to assignment to queue, driven entirely over HTTP, and
    the batch split that the research makes the normal case.

``the daily schedule``
    That a date-triggered automation does not run on creation, over HTTP, and that
    the morning route is the only path that runs it.

``no 5xx``
    Every route, every refusal a caller can produce, and no server error from any
    of them. A feature that 500s takes the product offline for whoever hits it.
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
from dsr.meeting_followup import vocabulary as vocab
from dsr.store import RecordStore
from fastapi.testclient import TestClient

PREFIX = "/api/wf-068"
FEATURE_ID = "wf-068-prepare-for-the-meeting-then-run-the-post"
FEATURE_MODULE = "dsr.features.wf068_prepare_for_the_meeting_then_run_the_post"

OWNER = "dana@northwind.example"
UNLICENSED = "sam@northwind.example"
ROOM = "room_a"


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
    scratch = tmp_path_factory.mktemp("wf068-http")
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


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def add_user(client: TestClient, email: str = OWNER, licensed: bool = True) -> str:
    created = client.post(
        f"{PREFIX}/users",
        json={"email": email, "name": email.split("@")[0], vocab.ENGAGE_LICENSE_FIELD: licensed},
        params={"room_id": ROOM},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def add_record(client: TestClient, crm_id: str, object_type: str, **fields) -> dict:
    payload = {"object_type": object_type, "crm_id": crm_id, "name": crm_id, "operation": "create"}
    payload.update(fields)
    created = client.post(f"{PREFIX}/crm/records", json=payload, params={"room_id": ROOM})
    assert created.status_code == 201, created.text
    return created.json()["record"]


def make_flow(client: TestClient, flow_id: str = "f-1", **overrides) -> dict:
    payload = {
        "flow_id": flow_id,
        "name": "Renewal follow-up",
        "steps": [
            {"kind": "email", "subject": "Recap", "offset_days": 0},
            {"kind": "task", "subject": "Confirm next step", "offset_days": 2},
        ],
        **overrides,
    }
    created = client.post(f"{PREFIX}/flows", json=payload, params={"room_id": ROOM})
    assert created.status_code == 201, created.text
    return created.json()


def make_automation(client: TestClient, trigger: dict, **overrides) -> dict:
    payload = {
        "name": "Enrol on a CRM event",
        "flow_id": "f-1",
        "assignee_email": OWNER,
        "trigger": trigger,
        **overrides,
    }
    created = client.post(f"{PREFIX}/automations", json=payload, params={"room_id": ROOM})
    assert created.status_code == 201, created.text
    return created.json()


def ready(client: TestClient) -> None:
    """A licensed owner, an account, a parented contact and a flow.

    The four things every enrolment path needs, so a test that is not about a
    refusal does not have to build them one at a time.
    """
    add_user(client)
    add_record(client, "acc-1", "ACCOUNT")
    add_record(client, "con-1", "CONTACT", **{vocab.PARENT_ACCOUNT_FIELD: "acc-1"})
    make_flow(client)


def assign(client: TestClient, ids: list[str], flow_id: str = "f-1", owner: str = OWNER):
    return client.post(
        f"{PREFIX}/flows/prospects/assign",
        json={
            "crmProspectsIds": ids,
            "flowId": flow_id,
            "flowInstanceOwnerEmail": owner,
        },
        params={"room_id": ROOM},
    )


PREP = {
    "meeting_id": "m-1",
    "title": "Northwind renewal",
    "starts_at": "2026-10-14T14:00:00+00:00",
    "summary": {
        "objectives": ["Agree the renewal timeline"],
        "key_topics": ["Renewal pricing"],
        "suggested_questions": ["Who signs off?"],
        "action_items_for_you": ["Send the questionnaire"],
    },
    "participants": [{"email": "buyer@northwind.example", "activity": [{"kind": "calls"}]}],
}


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
        assert found["ticket"] == "WF-068"
        assert found["prefix"] == PREFIX
        assert found["exception_handlers"], "the error handlers were not registered"

    def test_the_prefix_cannot_collide_with_one_already_mounted(self):
        module = host.load_feature("wf068_prepare_for_the_meeting_then_run_the_post")
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
        """The guard refuses a branch that edits a shared file. This asserts the
        same thing from the other side: the feature's name appears in no shared
        file at all, so none of them was edited to register it."""
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
            text = (root / relative).read_text(encoding="utf-8")
            assert "wf068" not in text, relative

    def test_the_feature_module_imports_no_framework_and_no_sqlite(self):
        """The domain module imports the store and nothing else; this checks the
        feature module too, and it is the one that could reach for a framework."""
        path = Path(importlib.import_module(FEATURE_MODULE).__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.add(node.module or "")
        assert "dsr.api" not in modules
        assert not any(name.split(".")[0] == "sqlite3" for name in modules)
        assert any(name.startswith("dsr.deps") for name in modules)


# --------------------------------------------------------------------------- #
# the audit-source rule
# --------------------------------------------------------------------------- #


class TestAuditSources:
    def test_every_source_this_router_records_names_a_mounted_route(self):
        """The rule the build brief asks for by name. Read from the routes the
        host really mounted rather than from a list kept beside the test."""
        module = host.load_feature("wf068_prepare_for_the_meeting_then_run_the_post")
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
        assert assign(client, ["con-1"]).status_code == 200
        assert client.post(f"{PREFIX}/prep", json=PREP, params={"room_id": ROOM}).status_code == 201

        sources = {entry.get("source") for entry in store.audit(limit=500)}
        assert f"POST {PREFIX}/users" in sources
        assert f"POST {PREFIX}/crm/records" in sources
        assert f"POST {PREFIX}/flows" in sources
        assert f"POST {PREFIX}/flows/prospects/assign" in sources
        assert f"POST {PREFIX}/prep" in sources

    def test_a_refused_write_records_no_audit_row(self, client: TestClient, store: RecordStore):
        """A refusal is not a write. Recording one would put a row in the audit log
        for a change that never happened."""
        ready(client)
        add_user(client, UNLICENSED, licensed=False)
        before = len(store.audit(limit=1000))
        refused = client.post(
            f"{PREFIX}/automations",
            json={
                "name": "Unlicensed",
                "flow_id": "f-1",
                "assignee_email": UNLICENSED,
                "trigger": {"kind": "crm_entity_created", "object_type": "LEAD"},
            },
            params={"room_id": ROOM},
        )
        assert refused.status_code == 403
        assert len(store.audit(limit=1000)) == before


def _source_literals() -> set[str]:
    """Any module-level string that *is* a route-shaped source."""
    module = importlib.import_module(FEATURE_MODULE)
    prefixes = ("GET ", "POST ", "PATCH ", "PUT ", "DELETE ")
    return {
        value
        for value in vars(module).values()
        if isinstance(value, str) and value.startswith(prefixes)
    }


def _declared_sources() -> list[tuple[str, str]]:
    """Every ``_source("METHOD", "/path")`` the feature module writes.

    Read from the module's source, so a route that forgot to pass a source cannot
    pass this test by being absent from the list.
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
    def test_an_unlicensed_assignee_is_403_not_400(self, client: TestClient):
        """The caller did nothing wrong. The organisation's licence does not cover
        the assignee they named, which is a refusal and not a malformed request."""
        ready(client)
        add_user(client, UNLICENSED, licensed=False)
        response = client.post(
            f"{PREFIX}/automations",
            json={
                "name": "Unlicensed",
                "flow_id": "f-1",
                "assignee_email": UNLICENSED,
                "trigger": {"kind": "crm_entity_created", "object_type": "LEAD"},
            },
            params={"room_id": ROOM},
        )
        assert response.status_code == 403
        assert response.json()["error"] == "assignee_not_licensed"
        assert vocab.ENGAGE_LICENSE_QUOTE in response.json()["detail"]

    def test_a_contact_with_no_parent_account_is_a_per_prospect_failure(self, client: TestClient):
        """The researched behaviour is a split, not a refusal: one bad prospect does
        not fail the batch."""
        ready(client)
        add_record(client, "con-2", "CONTACT")
        response = assign(client, ["con-1", "con-2"])
        assert response.status_code == 200
        body = response.json()
        assert [e["crmProspectId"] for e in body["prospectsAssigned"]] == ["con-1"]
        failure = body["prospectsNotAssigned"][0]
        assert failure["crmProspectId"] == "con-2"
        assert failure["errorCode"] == "InvalidArgument"
        assert vocab.PARENT_ACCOUNT_QUOTE in failure["detail"]

    def test_a_personal_flow_is_409_over_http(self, client: TestClient):
        """Both objects are individually valid; the conflict is between them."""
        ready(client)
        make_flow(client, "f-personal", personal=True)
        response = assign(client, ["con-1"], flow_id="f-personal")
        assert response.status_code == 409
        assert response.json()["error"] == "personal_flow_refused"
        assert vocab.PERSONAL_FLOW_QUOTE in response.json()["detail"]

    def test_an_unlicensed_owner_refuses_the_whole_batch(self, client: TestClient):
        ready(client)
        add_record(client, "con-2", "CONTACT")
        add_user(client, UNLICENSED, licensed=False)
        response = assign(client, ["con-1", "con-2"], owner=UNLICENSED)
        assert response.status_code == 403
        assert response.json()["error"] == "assignee_not_licensed"

    def test_more_than_two_hundred_prospects_is_a_refusal_not_a_truncation(
        self, client: TestClient
    ):
        """Quoted: You can assign up to 200 prospects to a flow in a single request.
        A request over the cap is not a partial failure; it is a request the vendor
        does not accept at all."""
        ready(client)
        response = assign(client, [f"p-{n}" for n in range(201)])
        assert response.status_code == 400
        assert response.json()["error"] == "assign_batch_too_large"
        assert "200" in response.json()["detail"]

    def test_a_date_trigger_for_a_non_opportunity_is_400(self, client: TestClient):
        """(Opportunity only) current date is."""
        ready(client)
        response = client.post(
            f"{PREFIX}/automations",
            json={
                "name": "Contact date",
                "flow_id": "f-1",
                "assignee_email": OWNER,
                "trigger": {"kind": "current_date_is", "object_type": "CONTACT"},
            },
            params={"room_id": ROOM},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "date_trigger_object_refused"

    def test_a_bad_cadence_is_refused_and_names_the_researched_range(self, client: TestClient):
        ready(client)
        response = client.post(
            f"{PREFIX}/crm/sync",
            json={
                "operation": "update",
                "cadence_minutes": 60,
                "object_type": "DEAL",
                vocab.MODIFICATION_DATE_FIELD: "2026-10-09T00:00:00Z",
                "records": [],
            },
            params={"room_id": ROOM},
        )
        assert response.status_code == 400
        assert "1-5 minutes" in response.json()["detail"]

    def test_an_update_with_no_modification_date_is_refused(self, client: TestClient):
        ready(client)
        response = client.post(
            f"{PREFIX}/crm/sync",
            json={
                "operation": "update",
                "cadence_minutes": 1,
                "object_type": "DEAL",
                "records": [],
            },
            params={"room_id": ROOM},
        )
        assert response.status_code == 400
        assert vocab.MODIFICATION_DATE_FIELD in response.json()["errors"]

    def test_a_slack_reminder_is_refused_and_names_its_researched_timing(self, client: TestClient):
        ready(client)
        response = client.post(
            f"{PREFIX}/reminders",
            json={"channel": "slack", "due_at": "2026-11-30T00:00:00Z"},
            params={"room_id": ROOM},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "slack_channel_refused"
        assert "Slack" in response.json()["detail"]

    def test_an_illegal_status_change_is_409_and_names_what_was_available(self, client: TestClient):
        """The body carries the status and the alternatives, so a client can render
        a control rather than an error string.

        The instance is Pending because its first touch is two days out, and a
        Pending instance may not become Running while a touch is undelivered. That
        is what stops a Running instance with nothing sent.
        """
        ready(client)
        make_flow(
            client,
            "f-later",
            steps=[{"kind": "email", "subject": "Recap", "offset_days": 2}],
        )
        instance_id = assign(client, ["con-1"], flow_id="f-later").json()["prospectsAssigned"][0][
            "flowInstanceId"
        ]
        response = client.post(
            f"{PREFIX}/instances/{instance_id}/status",
            json={"status": "Running"},
        )
        assert response.status_code == 409
        body = response.json()
        assert body["error"] == "flow_instance_transition_refused"
        assert body["status"] == "Pending"
        assert body["requested"] == "Running"
        assert body["statuses"] == list(vocab.FLOW_INSTANCE_STATUSES)

    def test_an_unknown_cal_webhook_is_refused(self, client: TestClient):
        ready(client)
        response = client.post(
            f"{PREFIX}/webhooks/cal", json={"event": "BOOKING_CREATED", "bookingUid": "bk-1"}
        )
        assert response.status_code == 400
        assert response.json()["error"] == "webhook_invalid"

    def test_an_unknown_flow_and_an_unknown_meeting_are_404(self, client: TestClient):
        ready(client)
        assert client.get(f"{PREFIX}/flows/nope").status_code == 404
        assert client.get(f"{PREFIX}/prep/nope").status_code == 404
        assert client.get(f"{PREFIX}/instances/nope").status_code == 404
        assert client.get(f"{PREFIX}/automations/nope").status_code == 404

    def test_an_invalid_automation_is_400_with_a_field_keyed_map(self, client: TestClient):
        """Each message lands beside the input that caused it."""
        ready(client)
        response = client.post(
            f"{PREFIX}/automations",
            json={"name": "", "flow_id": "", "trigger": {"kind": "nonsense"}},
            params={"room_id": ROOM},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "automation_invalid"
        assert "name" in response.json()["errors"]

    def test_no_route_answers_500_for_any_state(self, client: TestClient):
        """Every route, every refusal a caller can produce, no 5xx.

        A feature that 500s takes the product offline for whoever hits it, and the
        host's own contract is that one broken feature must not be able to do that.
        """
        ready(client)
        add_user(client, UNLICENSED, licensed=False)
        calls = [
            ("GET", f"{PREFIX}/summary", None),
            ("GET", f"{PREFIX}/vocabulary", None),
            ("GET", f"{PREFIX}/decisions", None),
            ("GET", f"{PREFIX}/decisions/nope", None),
            ("GET", f"{PREFIX}/users", None),
            ("GET", f"{PREFIX}/crm/records", None),
            ("GET", f"{PREFIX}/crm/sync/runs", None),
            ("GET", f"{PREFIX}/flows", None),
            ("GET", f"{PREFIX}/flows/nope", None),
            ("GET", f"{PREFIX}/flows/nope/cal-workflow", None),
            ("GET", f"{PREFIX}/automations", None),
            ("GET", f"{PREFIX}/automations/nope", None),
            ("GET", f"{PREFIX}/instances", None),
            ("GET", f"{PREFIX}/instances/nope", None),
            ("GET", f"{PREFIX}/todos", None),
            ("GET", f"{PREFIX}/queue", {"owner_email": OWNER}),
            ("GET", f"{PREFIX}/prep", None),
            ("GET", f"{PREFIX}/prep/nope", None),
            ("GET", f"{PREFIX}/prep/nope/push-window", None),
            ("GET", f"{PREFIX}/trackers", None),
            ("GET", f"{PREFIX}/webhooks", None),
            ("GET", f"{PREFIX}/reminders", None),
            ("GET", f"{PREFIX}/assignments", None),
            ("POST", f"{PREFIX}/users", {"email": ""}),
            ("POST", f"{PREFIX}/crm/records", {"object_type": "PROSPECT"}),
            ("POST", f"{PREFIX}/crm/sync", {"operation": "update", "cadence_minutes": 99}),
            ("POST", f"{PREFIX}/flows", {"name": "", "steps": []}),
            ("POST", f"{PREFIX}/automations", {}),
            ("POST", f"{PREFIX}/automations/evaluate", {"kind": "nonsense"}),
            ("POST", f"{PREFIX}/automations/morning-run", None),
            ("POST", f"{PREFIX}/flows/prospects/assign", {}),
            ("POST", f"{PREFIX}/instances/nope/status", {"status": "Completed"}),
            ("POST", f"{PREFIX}/todos/nope/complete", None),
            ("POST", f"{PREFIX}/prep", {}),
            ("POST", f"{PREFIX}/prep/nope/dry-run", {}),
            ("POST", f"{PREFIX}/prep/nope/ask", {"question": "x"}),
            ("POST", f"{PREFIX}/trackers", {}),
            ("POST", f"{PREFIX}/webhooks/cal", {"event": "NOPE"}),
            ("POST", f"{PREFIX}/cal-workflows/validate", {}),
            ("POST", f"{PREFIX}/reminders", {"channel": "carrier_pigeon"}),
        ]
        for method, path, params in calls:
            kwargs: dict = {}
            if method in ("POST", "PATCH"):
                if params is not None:
                    response = client.request(method, path, json=params, **kwargs)
                else:
                    response = client.request(method, path, **kwargs)
            elif params:
                response = client.request(method, path, params=params, **kwargs)
            else:
                response = client.request(method, path, **kwargs)
            assert response.status_code < 500, (method, path, response.status_code, response.text)


# --------------------------------------------------------------------------- #
# the researched reads
# --------------------------------------------------------------------------- #


class TestResearchedReads:
    def test_the_vocabulary_serves_the_sets_the_rules_validate_against(self, client: TestClient):
        """Served rather than duplicated in the frontend, so a form cannot drift
        from the rules."""
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body["statuses"] == list(vocab.FLOW_INSTANCE_STATUSES)
        assert body["triggers"] == list(vocab.TRIGGERS)
        assert body["object_types"] == list(vocab.OBJECT_TYPES)
        assert body["prep"]["sections"] == list(vocab.PREP_SECTIONS)
        assert body["prep"]["recent_activity_kinds"] == list(vocab.RECENT_ACTIVITY_KINDS)
        assert body["assignment"]["max_prospects"] == vocab.MAX_PROSPECTS_PER_REQUEST
        assert body["assignment"]["error_codes"] == list(vocab.ASSIGN_ERROR_CODES)
        assert body["crm_sync"]["recommended_range_minutes"] == [1, 5]
        assert body["cal"]["transcription_formats"] == list(vocab.TRANSCRIPTION_FORMATS)
        assert body["collections"] == list(vocab.ALL_COLLECTIONS)

    def test_the_vocabulary_carries_the_vendor_attribution(self, client: TestClient):
        """Gong supplies the prep page, the flows and the CRM upload. Cal supplies
        the follow-up webhooks and the recording download links."""
        vendors = client.get(f"{PREFIX}/vocabulary").json()["vendors"]
        assert "meeting_prep" in vendors["gong"]
        assert "engage_flows" in vendors["gong"]
        assert "followup_webhooks" in vendors["cal"]
        assert "recording_downloads" in vendors["cal"]

    def test_the_vocabulary_quotes_the_three_researched_gates(self, client: TestClient):
        gates = client.get(f"{PREFIX}/vocabulary").json()["gates"]
        assert gates["engage_license"] == vocab.ENGAGE_LICENSE_QUOTE
        assert gates["parent_account"] == vocab.PARENT_ACCOUNT_QUOTE
        assert gates["personal_flow"] == vocab.PERSONAL_FLOW_QUOTE

    def test_the_vocabulary_reproduces_the_tracker_quote_as_written(self, client: TestClient):
        """The issue says: quote it as written if you cite it."""
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body["tracker_quote_with_typo"] == vocab.TRACKER_QUOTE_WITH_TYPO
        assert "AI AI" in body["tracker_quote_with_typo"]
        assert body["tracker_quote_note"]

    def test_the_schedule_quote_is_served_where_the_automation_list_is_read(
        self, client: TestClient
    ):
        """A seller looking at an automation needs the sentence that explains why it
        has not run yet, next to the automation."""
        ready(client)
        make_automation(
            client,
            {"kind": "current_date_is", "object_type": "DEAL", "offset_days": -30},
        )
        body = client.get(f"{PREFIX}/automations").json()
        assert body["automations"][0]["data"]["awaiting_morning_run"] is True
        assert body["automations"][0]["data"]["last_evaluated_at"] is None
        summary = client.get(f"{PREFIX}/summary", params={"room_id": ROOM}).json()
        assert summary["automations_awaiting_morning_run"] == 1
        assert summary["schedule_quote"] == vocab.DATE_TRIGGER_SCHEDULE_QUOTE

    def test_the_forecast_read_names_which_of_the_three_timings_are_sent(self, client: TestClient):
        body = client.get(f"{PREFIX}/reminders").json()
        assert len(body["researched"]) == 3
        assert body["implemented_channels"] == ["in_app"]
        assert body["not_implemented"] == ["slack_1h_before", "slack_summary_2h_after"]

    def test_the_derivation_record_is_served_with_what_each_decision_rejected(
        self, client: TestClient
    ):
        """The research requires it: must derive it and record the derivation, not
        assume it. A record nobody can read is not a record, so it is a route."""
        body = client.get(f"{PREFIX}/decisions").json()
        assert body["count"] >= 12
        required = {
            "id",
            "topic",
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

    def test_one_derivation_is_readable_by_id(self, client: TestClient):
        response = client.get(f"{PREFIX}/decisions/wf068-own-crm-collections")
        assert response.status_code == 200
        body = response.json()
        assert "Account, Contact, Deal or Lead" in body["question"]
        assert "jev-20261013T111405-12092-45997" in body["residual_risk"]

    def test_an_unknown_derivation_id_is_400(self, client: TestClient):
        assert client.get(f"{PREFIX}/decisions/not-a-decision").status_code == 400

    def test_the_summary_counts_what_a_reviewer_reads(self, client: TestClient):
        ready(client)
        add_record(client, "con-2", "CONTACT")
        assign(client, ["con-1", "con-2"])
        body = client.get(f"{PREFIX}/summary", params={"room_id": ROOM}).json()
        assert body["flows"] == 1
        assert body["instances"] == 1
        assert body["todos"] == 2
        assert body["todos_open"] == 2
        assert body["assignment_failures"] == 1
        assert body["statuses"] == list(vocab.FLOW_INSTANCE_STATUSES)


# --------------------------------------------------------------------------- #
# the full cycle
# --------------------------------------------------------------------------- #


class TestFullCycle:
    def test_the_prep_page_is_answered_with_its_six_sections_and_its_feed(self, client: TestClient):
        ready(client)
        client.post(f"{PREFIX}/prep", json=PREP, params={"room_id": ROOM})
        page = client.get(f"{PREFIX}/prep/m-1").json()
        assert page["sections"] == list(vocab.PREP_SECTIONS)
        assert page["summary"]["objectives"] == ["Agree the renewal timeline"]
        assert list(page["recent_activity"]) == list(vocab.RECENT_ACTIVITY_KINDS)
        assert page["recent_activity"]["calls"]
        assert page["keyboard_shortcut"] == "Shift+M"
        assert page["summary_source"] == "assembled_from_store"

    def test_the_mobile_view_omits_the_feed(self, client: TestClient):
        """Quoted: the mobile app does not display recent activities."""
        ready(client)
        client.post(f"{PREFIX}/prep", json=PREP, params={"room_id": ROOM})
        page = client.get(f"{PREFIX}/prep/m-1", params={"mobile": True}).json()
        assert page["recent_activity"] is None
        assert page["recent_activity_omitted_because"] is True

    def test_the_dry_run_is_recorded_as_derived_and_says_it_synthesised_nothing(
        self, client: TestClient
    ):
        ready(client)
        client.post(f"{PREFIX}/prep", json=PREP, params={"room_id": ROOM})
        response = client.post(
            f"{PREFIX}/prep/m-1/dry-run",
            json={"voice": "neutral", "covered": ["Agree the renewal timeline"]},
        )
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["derived"] is True
        assert data["participant"]["built_from"] == "meeting_context"
        assert data["feedback"]["objectives_covered"] == ["Agree the renewal timeline"]
        assert "No synthesis provider is named" in data["note"]

    def test_ask_anything_answers_from_the_prep_context_and_names_its_sources(
        self, client: TestClient
    ):
        ready(client)
        client.post(f"{PREFIX}/prep", json=PREP, params={"room_id": ROOM})
        response = client.post(
            f"{PREFIX}/prep/m-1/ask",
            json={"question": "what were the objectives?"},
        )
        assert response.status_code == 201
        body = response.json()["data"]
        assert "objectives" in body["sources"]
        assert body["derived"] is True

    def test_a_crm_event_drives_the_automation_over_http(self, client: TestClient):
        ready(client)
        make_automation(client, {"kind": "crm_entity_created", "object_type": "CONTACT"})
        response = client.post(
            f"{PREFIX}/automations/evaluate",
            json={"kind": "crm_entity", "operation": "create", "crm_id": "con-1"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["fired"] == 1
        assert body["enrolled"][0]["prospectsAssigned"][0]["crmProspectId"] == "con-1"

    def test_an_event_that_does_not_match_reports_why_rather_than_failing(self, client: TestClient):
        """A buyer's activity failing to match a filter is a fact about the buyer.
        A caller who got a 400 for it would retry forever."""
        ready(client)
        make_automation(client, {"kind": "crm_entity_created", "object_type": "CONTACT"})
        response = client.post(
            f"{PREFIX}/automations/evaluate",
            json={"kind": "crm_entity", "operation": "create", "crm_id": "con-1"},
        )
        assert response.status_code == 200
        # A tracker event does not match a CRM trigger.
        missed = client.post(
            f"{PREFIX}/automations/evaluate",
            json={"kind": "tracker_detected", "tracker_name": "Pricing Objection"},
        ).json()
        assert missed["fired"] == 0
        assert missed["decisions"][0]["reasons"] == ["trigger_not_matched"]

    def test_a_tracker_detection_is_recorded_and_evaluated_in_one_call(self, client: TestClient):
        ready(client)
        make_automation(client, {"kind": "tracker_detected", "tracker_name": "Pricing Objection"})
        response = client.post(
            f"{PREFIX}/trackers",
            json={
                "tracker_name": "Pricing Objection",
                "meeting_id": "m-1",
                "crm_id": "con-1",
            },
            params={"room_id": ROOM},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["evaluated"]["fired"] == 1
        assert client.get(f"{PREFIX}/trackers").json()["count"] == 1

    def test_the_queue_carries_the_researched_position_label(self, client: TestClient):
        """Quoted: 1/5 means you're working on the first of five to-dos in that queue."""
        ready(client)
        assign(client, ["con-1"])
        body = client.get(f"{PREFIX}/queue", params={"owner_email": OWNER}).json()
        assert body["queue_count"] == 1
        rows = body["queues"][0]["todos"]
        assert [row["position_label"] for row in rows] == ["1/2", "2/2"]
        assert all(row["flow_reference"] for row in rows)
        assert body["note"] == vocab.TODO_QUEUE_QUOTE

    def test_completing_a_todo_is_a_write_that_names_its_route(
        self, client: TestClient, store: RecordStore
    ):
        ready(client)
        assign(client, ["con-1"])
        todo = client.get(f"{PREFIX}/todos").json()["todos"][0]
        assert client.post(f"{PREFIX}/todos/{todo['todo_id']}/complete").status_code == 200
        sources = {entry.get("source") for entry in store.audit(limit=500)}
        assert f"POST {PREFIX}/todos/{{todo_id}}/complete" in sources

    def test_a_paused_instance_is_visible_as_the_third_status(self, client: TestClient):
        ready(client)
        instance_id = assign(client, ["con-1"]).json()["prospectsAssigned"][0]["flowInstanceId"]
        paused = client.post(f"{PREFIX}/instances/{instance_id}/status", json={"status": "Paused"})
        assert paused.status_code == 200
        assert paused.json()["data"]["status"] == "Paused"
        view = client.get(f"{PREFIX}/instances/{instance_id}").json()["view"]
        assert view["status"] == "Paused"
        assert view["terminal"] is False
        assert "Running" in view["allowed_transitions"]

    def test_the_assignment_response_carries_the_researched_fields(self, client: TestClient):
        """prospectsAssigned[]: flowInstanceId, flowInstanceStatus, exclusive, workspaceId."""
        ready(client)
        entry = assign(client, ["con-1"]).json()["prospectsAssigned"][0]
        assert set(entry) >= set(vocab.ASSIGNED_FIELDS)
        assert entry["flowInstanceStatus"] in vocab.FLOW_INSTANCE_STATUSES
        assert entry["exclusive"] is True
        assert entry["workspaceId"].startswith("ws_")

    def test_the_cal_webhook_records_the_link_and_does_not_claim_to_have_fetched_it(
        self, client: TestClient
    ):
        """A recording link carries a token that expires. A stored link is evidence
        that a recording existed; pretending to have fetched it would produce a link
        that fails the first time a rep clicked it."""
        ready(client)
        response = client.post(
            f"{PREFIX}/webhooks/cal",
            json={
                "event": "RECORDING_READY",
                "bookingUid": "bk-1",
                "downloadLink": "https://app.cal.com/api/video/recording?token=demo",
            },
        )
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["recording"]["has_link"] is True
        assert data["recording"]["fetched"] is False
        assert data["fetched"] is False

    def test_the_transcription_webhook_records_the_four_researched_formats(
        self, client: TestClient
    ):
        ready(client)
        response = client.post(
            f"{PREFIX}/webhooks/cal",
            json={
                "event": "RECORDING_TRANSCRIPTION_GENERATED",
                "bookingUid": "bk-1",
                "downloadLinks": {
                    "transcription": [
                        {"format": fmt, "link": f"https://app.cal.com/t.{fmt}"}
                        for fmt in vocab.TRANSCRIPTION_FORMATS
                    ]
                },
            },
        )
        data = response.json()["data"]
        assert data["transcription"]["available"] == list(vocab.TRANSCRIPTION_FORMATS)
        assert data["transcription"]["unknown_formats"] == []

    def test_the_cal_workflow_body_carries_the_researched_trigger(self, client: TestClient):
        """Quoted: POST /v2/workflows with trigger afterEvent (offset)."""
        ready(client)
        response = client.get(
            f"{PREFIX}/flows/f-1/cal-workflow",
            params={"booking_uid": "bk-1", "event_ended_at": "2026-10-14T15:00:00Z"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["endpoint"] == "/v2/workflows"
        assert body["trigger"]["type"] == "afterEvent"
        assert body["trigger"]["at"] == "2026-10-14T15:00:00Z"
        assert body["sent"] is False

    def test_the_crm_sync_run_records_the_exact_researched_request(self, client: TestClient):
        """The product is not a Gong client, so the request is recorded and not sent."""
        ready(client)
        response = client.post(
            f"{PREFIX}/crm/sync",
            json={
                "operation": "update",
                "cadence_minutes": 5,
                "object_type": "DEAL",
                vocab.MODIFICATION_DATE_FIELD: "2026-10-09T00:00:00Z",
                "records": [
                    {
                        "crm_id": "deal-1",
                        "name": "Renewal",
                        "operation": "update",
                        vocab.MODIFICATION_DATE_FIELD: "2026-10-09T00:00:00Z",
                    }
                ],
            },
            params={"room_id": ROOM},
        )
        assert response.status_code == 201
        request = response.json()["data"]["request"]
        assert request["endpoint"] == "/v2/crm/entities?objectType=DEAL"
        assert request["body"][vocab.MODIFICATION_DATE_FIELD] == "2026-10-09T00:00:00Z"
        assert request["sent"] is False


# --------------------------------------------------------------------------- #
# the daily schedule
# --------------------------------------------------------------------------- #


class TestDailyScheduleOverHttp:
    DEAL = {
        "operation": "update",
        vocab.MODIFICATION_DATE_FIELD: "2026-10-09T00:00:00Z",
        "fields": {"stage": "Negotiation", "close_date": "2026-11-30"},
    }

    def test_a_date_trigger_does_not_run_when_it_is_created(self, client: TestClient):
        """Quoted: They do not run immediately when an automation is created or edited."""
        ready(client)
        add_record(client, "deal-1", "DEAL", **self.DEAL)
        created = make_automation(
            client,
            {
                "kind": "current_date_is",
                "object_type": "DEAL",
                "date_field": "close_date",
                "offset_days": -30,
            },
            name="Renewal reminder",
        )
        assert created["data"]["pending_date_evaluation"] is True
        assert created["data"]["last_evaluated_at"] is None
        assert client.get(f"{PREFIX}/instances").json()["count"] == 0

    def test_the_morning_run_is_the_only_path_that_runs_one(self, client: TestClient):
        """2026-11-30 less 30 days is 2026-10-31, so that morning enrols the deal
        and no other does."""
        ready(client)
        add_record(client, "deal-1", "DEAL", **self.DEAL)
        add_record(client, "deal-2", "LEAD")
        make_automation(
            client,
            {
                "kind": "current_date_is",
                "object_type": "DEAL",
                "date_field": "close_date",
                "offset_days": -30,
            },
            name="Renewal reminder",
        )

        early = client.post(f"{PREFIX}/automations/morning-run", params={"on": "2026-11-01"})
        assert early.status_code == 200
        assert early.json()["fired"] == 0

        due = client.post(f"{PREFIX}/automations/morning-run", params={"on": "2026-10-31"})
        assert due.status_code == 200
        assert due.json()["fired"] == 1
        assert due.json()["schedule_quote"] == vocab.DATE_TRIGGER_SCHEDULE_QUOTE

        after = client.post(f"{PREFIX}/automations/morning-run", params={"on": "2026-11-05"})
        assert after.json()["fired"] == 0

    def test_a_morning_run_stamps_the_automation_as_evaluated(self, client: TestClient):
        ready(client)
        add_record(client, "deal-1", "DEAL", **self.DEAL)
        created = make_automation(
            client,
            {
                "kind": "current_date_is",
                "object_type": "DEAL",
                "date_field": "close_date",
                "offset_days": -30,
            },
        )
        client.post(f"{PREFIX}/automations/morning-run", params={"on": "2026-10-31"})
        after = client.get(f"{PREFIX}/automations/{created['id']}").json()
        assert after["data"]["last_evaluated_at"] is not None
        assert after["awaiting_morning_run"] is False

    def test_a_malformed_date_parameter_is_400(self, client: TestClient):
        ready(client)
        response = client.post(
            f"{PREFIX}/automations/morning-run", params={"on": "the first of November"}
        )
        assert response.status_code == 400

    def test_the_read_tells_the_seller_when_the_next_run_is(self, client: TestClient):
        """A date automation has not run, so the page needs to say when it will."""
        ready(client)
        created = make_automation(
            client,
            {"kind": "current_date_is", "object_type": "DEAL", "offset_days": -30},
        )
        body = client.get(f"{PREFIX}/automations/{created['id']}").json()
        assert body["awaiting_morning_run"] is True
        assert body["next_evaluation"] is not None
