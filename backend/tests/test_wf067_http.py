"""HTTP tests for WF-067: the routes, called the way the host serves them.

These run against the real application over a temporary database, so they answer
the questions a unit test cannot: does the host mount the router by discovery
alone, does the error handler turn a domain refusal into the status the refusal
carries, does the webhook answer 200 for a retry, and does every ``source`` the
feature records name a route the running app actually serves.

The last of those is the one this file exists for. The defect it catches shipped
in this codebase before: a feature's audit log kept naming a route the app had
stopped serving. The check is behavioural - it reads what the audit log recorded,
after driving every write endpoint, and asks the running app what it mounted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.scheduling_meetings import WEBHOOK_SECRET_HEADER
from dsr.store import RecordStore

#: The feature's own prefix, duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well.
PREFIX = "/api/wf-067"

MODULE = "wf067_send_a_mutual_action_plan_for_e_signature"
FEATURE_ID = "wf-067-send-a-mutual-action-plan-for-e-signature"

SECRET = "northwind-map-webhook-secret"
SIGNER = "buyer@northwind.example"
APPROVER = "legal@contoso.example"


def people() -> list[dict]:
    return [
        {"email": SIGNER, "name": "Ada Byron", "role": "SIGNER", "party": "buyer"},
        {"email": APPROVER, "name": "Luis Ortega", "role": "APPROVER", "party": "buyer"},
        {"email": "dana@northwind.example", "name": "Dana Reed", "role": "CC", "party": "seller"},
    ]


def plan_payload(**overrides) -> dict:
    payload = {
        "subject": "Mutual action plan",
        "message": "Three milestones.",
        "webhook_secret": SECRET,
        "recipients": people(),
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def http(client):
    """The shared application, over a database this test owns alone.

    ``client`` is conftest's: it assigns a fresh, empty database onto
    ``app.state`` and undoes it afterwards, so no test in this file can see
    another's rows and none of them leaks into the next module.
    """
    return client


@pytest.fixture()
def room(http):
    """A room created through the core records API, so its audit row names a core route."""
    return http.post("/api/records/room", json={"name": "Northwind", "account": "Northwind"}).json()


def client_store(http) -> RecordStore:
    """The store the running app is reading, so the audit rows are this test's."""
    return RecordStore(http.app.state.db)


def mounted_routes(client, feature_id=FEATURE_ID):
    """Every (method, path) the host mounted for one feature, templates intact."""
    entry = next(
        (f for f in client.get("/api/features").json()["features"] if f["id"] == feature_id), None
    )
    assert entry is not None, f"{feature_id} is not mounted"
    return {(method, route["path"]) for route in entry["routes"] for method in route["methods"]}


def all_served_routes(client):
    """Every (method, path) the running app serves, core routes included.

    The audit log is shared, so a check of what it records has to be allowed to
    see a core write as well as a feature one.
    """
    routes = set()
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            routes.add((method, getattr(route, "path", "")))
    for feature in client.get("/api/features").json()["features"]:
        for route in feature["routes"]:
            for method in route["methods"]:
                routes.add((method, route["path"]))
    return routes


def source_names_a_mounted_route(source, routes):
    """Does ``"POST /api/wf-067/rooms/abc/plans"`` name a route that exists?

    A recorded source carries concrete ids; a mounted path carries FastAPI's
    ``{param}`` placeholders. The pattern is built from the *template* and matched
    against the source, with each placeholder as one wildcard segment, and the
    literal parts escaped so a segment containing a regex metacharacter cannot
    make the pattern match something else.
    """
    method, _, path = source.partition(" ")
    for mounted_method, template in routes:
        if mounted_method != method:
            continue
        parts = re.split(r"(\{[^}]+\})", template)
        pattern = (
            "^"
            + "".join(r"[^/]+" if part.startswith("{") else re.escape(part) for part in parts)
            + "$"
        )
        if re.match(pattern, path):
            return True
    return False


def make_plan(client, room_id, **overrides):
    response = client.post(f"{PREFIX}/rooms/{room_id}/plans", json=plan_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def event(client, room_id, external_id, name, *, secret=SECRET, **extra):
    """One delivery, the way the vendor sends it.

    The header name comes from the package rather than being typed here, so the
    test fails if the research's own header name ever changes.
    """
    return client.post(
        f"{PREFIX}/rooms/{room_id}/webhook",
        json={
            "event": name,
            "eventId": extra.pop("event_id", f"evt-{name}-{extra.get('recipientEmail', '')}"),
            "externalId": external_id,
            **extra,
        },
        headers={WEBHOOK_SECRET_HEADER: secret} if secret else {},
    )


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery_alone(http):
    """api.py is not edited, and the routes resolve anyway."""
    routes = mounted_routes(http)
    assert ("GET", f"{PREFIX}/vocabulary") in routes
    assert ("POST", f"{PREFIX}/rooms/{{room_id}}/plans") in routes
    assert ("POST", f"{PREFIX}/rooms/{{room_id}}/webhook") in routes


def test_the_feature_reports_its_prefix_and_ticket(http):
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)
    assert entry["ticket"] == "WF-067"
    assert entry["prefix"] == PREFIX


def test_the_feature_module_opens_no_connection_and_imports_no_app():
    module = load_feature(MODULE)
    text = Path(module.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in text
    assert "import dsr.api" not in text
    assert "import sqlite3" not in text
    assert "sqlite3.connect" not in text


def test_the_feature_owns_no_shared_file():
    """The guard CI runs, read off the diff this branch would produce."""
    shared = {
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
    }
    root = Path(__file__).resolve().parents[2]
    changed = subprocess_changed_files(root)
    assert not (changed & shared), sorted(changed & shared)


def subprocess_changed_files(root: Path) -> set[str]:
    """The files this branch changed against ``main``, by path relative to the root."""
    import subprocess

    out = subprocess.run(
        ["git", "diff", "--name-only", "origin/main...HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return {line.strip().replace("\\", "/") for line in out.stdout.splitlines() if line.strip()}


# --------------------------------------------------------------------------- #
# Vocabulary and inferences
# --------------------------------------------------------------------------- #


def test_the_vocabulary_serves_the_five_roles_and_the_approver_rule(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert [row["role"] for row in body["roles"]] == [
        "SIGNER",
        "APPROVER",
        "CC",
        "VIEWER",
        "ASSISTANT",
    ]
    approver = next(row for row in body["roles"] if row["role"] == "APPROVER")
    assert approver["meaning"] == "APPROVER | Must approve before signers can sign"


def test_the_vocabulary_serves_fourteen_events_and_seven_milestones(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert len(body["events"]) == 14
    assert len(body["milestones"]) == 7
    refusals = {row["milestone"] for row in body["milestones"] if row["is_a_refusal"]}
    assert refusals == {"refused_by_signer", "refused_by_approver"}


def test_the_vocabulary_serves_the_two_invariants(http):
    invariants = http.get(f"{PREFIX}/vocabulary").json()["invariants"]
    assert "recipients must sign themselves" in invariants["never_sign_quote"]


def test_the_inferences_register_is_served_over_http(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["ticket"] == "WF-067"
    ids = {entry["id"] for entry in body["decisions"]}
    assert "external-id-is-derived" in ids
    assert "approver-refusal-differs-from-signer-refusal" in ids
    assert "invitation-path-is-the-embed" in ids


def test_the_webhook_contract_names_the_header_and_the_path(http):
    body = http.get(f"{PREFIX}/whoami").json()
    assert body["header"] == "X-Documenso-Secret"
    assert body["path_template"] == f"POST {PREFIX}/rooms/{{room_id}}/webhook"
    assert body["join_key"] == "externalId"
    assert len(body["events"]) == 14


# --------------------------------------------------------------------------- #
# Templates, plans, distribution
# --------------------------------------------------------------------------- #


def test_a_template_is_created_with_its_roles_and_fields(http, room):
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/templates",
        json={
            "name": "Standard mutual action plan",
            "roles": ["SIGNER", "APPROVER"],
            "fields": [{"type": "SIGNATURE", "positionX": 10, "positionY": 60, "width": 25}],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["roles"] == ["SIGNER", "APPROVER"]
    assert body["fields"][0]["positionX"] == 10.0


def test_a_template_with_no_name_is_refused(http, room):
    response = http.post(f"{PREFIX}/rooms/{room['id']}/templates", json={})
    assert response.status_code == 422


def test_a_plan_is_created_with_its_recipients_and_join_key(http, room):
    body = make_plan(http, room["id"])
    assert body["milestone"] == "draft"
    assert body["status"] == "DRAFT"
    assert len(body["recipients"]) == 3
    assert body["plan"]["external_id"].startswith("dsr-map.")


def test_a_plan_with_no_signing_recipient_is_refused_with_422(http, room):
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/plans",
        json=plan_payload(recipients=[{"email": "ops@b.example", "name": "Ops", "role": "VIEWER"}]),
    )
    assert response.status_code == 422
    assert response.json()["error"] == "plan_needs_a_signing_recipient"


def test_a_recipient_with_an_unknown_role_is_refused_with_422(http, room):
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/plans",
        json=plan_payload(recipients=[{"email": "a@b.example", "name": "Ada", "role": "NOTARY"}]),
    )
    assert response.status_code == 422
    assert response.json()["error"] == "unknown_recipient_role"


def test_a_field_past_the_edge_of_the_page_is_refused_with_422(http, room):
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/plans",
        json=plan_payload(
            recipients=[
                {
                    "email": SIGNER,
                    "name": "Ada",
                    "role": "SIGNER",
                    "fields": [{"type": "SIGNATURE", "positionX": 140}],
                }
            ]
        ),
    )
    assert response.status_code == 422
    assert response.json()["error"] == "malformed_field"


def test_distributing_moves_the_plan_from_draft_to_pending(http, room):
    plan = make_plan(http, room["id"])
    response = http.post(f"{PREFIX}/rooms/{room['id']}/plans/{plan['id']}/distribute")
    assert response.status_code == 200
    body = response.json()
    assert body["milestone"] == "awaiting_signature"
    assert body["status"] == "PENDING"


def test_distributing_twice_is_refused_with_409(http, room):
    plan = make_plan(http, room["id"])
    http.post(f"{PREFIX}/rooms/{room['id']}/plans/{plan['id']}/distribute")
    again = http.post(f"{PREFIX}/rooms/{room['id']}/plans/{plan['id']}/distribute")
    assert again.status_code == 409
    assert again.json()["error"] == "plan_already_distributed"


def test_cancelling_an_approved_plan_is_refused_with_409(http, room):
    plan = make_plan(http, room["id"])
    http.post(f"{PREFIX}/rooms/{room['id']}/plans/{plan['id']}/distribute")
    event(http, room["id"], plan["plan"]["external_id"], "DOCUMENT_COMPLETED")
    refused = http.post(f"{PREFIX}/rooms/{room['id']}/plans/{plan['id']}/cancel")
    assert refused.status_code == 409


def test_can_sign_names_the_approver_still_blocking(http, room):
    plan = make_plan(http, room["id"])
    http.post(f"{PREFIX}/rooms/{room['id']}/plans/{plan['id']}/distribute")
    response = http.get(
        f"{PREFIX}/rooms/{room['id']}/plans/{plan['id']}/can-sign", params={"email": SIGNER}
    )
    body = response.json()
    assert body["can_sign"] is False
    assert body["reason"] == "awaiting_approver"
    assert "must approve before signers can sign" in body["detail"]


# --------------------------------------------------------------------------- #
# The webhook
# --------------------------------------------------------------------------- #


def test_an_event_without_the_secret_is_refused_with_401_and_writes_nothing(http, room):
    plan = make_plan(http, room["id"])
    http.post(f"{PREFIX}/rooms/{room['id']}/plans/{plan['id']}/distribute")
    before = len(client_store(http).audit(limit=2000))
    refused = event(
        http,
        room["id"],
        plan["plan"]["external_id"],
        "DOCUMENT_OPENED",
        secret="wrong",
        recipientEmail=SIGNER,
    )
    assert refused.status_code == 401
    assert refused.json()["error"] == "event_unauthenticated"
    assert client_store(http).list("map_event") == []
    # The refusal is not even an audit row: an unauthenticated caller must not be
    # able to make this product write anything.
    assert len(client_store(http).audit(limit=2000)) == before


def test_an_event_with_no_secret_header_at_all_is_refused_with_401(http, room):
    plan = make_plan(http, room["id"])
    http.post(f"{PREFIX}/rooms/{room['id']}/plans/{plan['id']}/distribute")
    refused = event(
        http,
        room["id"],
        plan["plan"]["external_id"],
        "DOCUMENT_OPENED",
        secret=None,
        recipientEmail=SIGNER,
    )
    assert refused.status_code == 401


def test_an_unknown_event_name_is_refused_with_422(http, room):
    plan = make_plan(http, room["id"])
    http.post(f"{PREFIX}/rooms/{room['id']}/plans/{plan['id']}/distribute")
    refused = event(http, room["id"], plan["plan"]["external_id"], "DOCUMENT_SENT_TO_MOON")
    assert refused.status_code == 422
    assert refused.json()["error"] == "unknown_event_type"


def test_an_event_with_no_join_key_is_refused_with_409(http, room):
    make_plan(http, room["id"])
    refused = http.post(
        f"{PREFIX}/rooms/{room['id']}/webhook",
        json={"event": "DOCUMENT_OPENED", "recipientEmail": SIGNER},
        headers={"X-Documenso-Secret": SECRET},
    )
    assert refused.status_code == 409
    assert refused.json()["error"] == "plan_unresolved"


def test_an_event_on_an_undelivered_plan_is_refused_with_409(http, room):
    """Believing it would approve a plan no buyer was ever sent."""
    plan = make_plan(http, room["id"])
    refused = event(
        http, room["id"], plan["plan"]["external_id"], "DOCUMENT_SIGNED", recipientEmail=SIGNER
    )
    assert refused.status_code == 409
    assert refused.json()["error"] == "event_before_distribution"


def test_a_verified_event_is_applied_and_answers_201(http, room):
    plan = make_plan(http, room["id"])
    http.post(f"{PREFIX}/rooms/{room['id']}/plans/{plan['id']}/distribute")
    applied = event(
        http, room["id"], plan["plan"]["external_id"], "DOCUMENT_OPENED", recipientEmail=SIGNER
    )
    assert applied.status_code == 201
    assert applied.json()["outcome"] == "applied"


def test_a_retried_event_answers_200_and_is_applied_once(http, room):
    """Webhooks may be retried, so handle duplicate events."""
    plan = make_plan(http, room["id"])
    http.post(f"{PREFIX}/rooms/{room['id']}/plans/{plan['id']}/distribute")
    first = event(
        http, room["id"], plan["plan"]["external_id"], "DOCUMENT_OPENED", recipientEmail=SIGNER
    )
    assert first.status_code == 201
    retry = event(
        http, room["id"], plan["plan"]["external_id"], "DOCUMENT_OPENED", recipientEmail=SIGNER
    )
    assert retry.status_code == 200
    assert retry.json()["outcome"] == "duplicate"
    events = http.get(f"{PREFIX}/rooms/{room['id']}/events").json()["events"]
    assert len(events) == 1
    assert events[0]["attempts"] == 2


def test_completion_flips_the_milestone_to_approved_over_http(http, room):
    """The sales room consumes DOCUMENT_COMPLETED to flip the milestone to Approved."""
    plan = make_plan(http, room["id"])
    http.post(f"{PREFIX}/rooms/{room['id']}/plans/{plan['id']}/distribute")
    event(http, room["id"], plan["plan"]["external_id"], "DOCUMENT_COMPLETED")
    view = http.get(f"{PREFIX}/rooms/{room['id']}/plans/{plan['id']}").json()
    assert view["milestone"] == "approved"
    assert view["status"] == "COMPLETED"


def test_an_approver_refusal_and_a_signer_refusal_are_two_http_outcomes(http, room):
    approver_plan = make_plan(http, room["id"], subject="Approver refuses")
    http.post(f"{PREFIX}/rooms/{room['id']}/plans/{approver_plan['id']}/distribute")
    event(
        http,
        room["id"],
        approver_plan["plan"]["external_id"],
        "DOCUMENT_REJECTED",
        recipientEmail=APPROVER,
        rejectionReason="Clause 7.2 is not acceptable.",
    )
    refused = http.get(f"{PREFIX}/rooms/{room['id']}/plans/{approver_plan['id']}").json()
    assert refused["milestone"] == "refused_by_approver"

    signer_plan = make_plan(http, room["id"], subject="Signer refuses")
    http.post(f"{PREFIX}/rooms/{room['id']}/plans/{signer_plan['id']}/distribute")
    event(
        http,
        room["id"],
        signer_plan["plan"]["external_id"],
        "DOCUMENT_REJECTED",
        recipientEmail=SIGNER,
        rejectionReason="The timeline does not work for us.",
    )
    also_refused = http.get(f"{PREFIX}/rooms/{room['id']}/plans/{signer_plan['id']}").json()
    assert also_refused["milestone"] == "refused_by_signer"


def test_an_event_body_that_is_not_json_is_refused_without_a_5xx(http, room):
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/webhook",
        content=b"not json at all",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_an_event_body_that_is_not_an_object_is_refused_without_a_5xx(http, room):
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/webhook",
        json=["a", "list"],
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_an_event_for_another_rooms_join_key_is_refused_with_409(http, room):
    other = http.post("/api/records/room", json={"name": "Contoso", "account": "Contoso"}).json()
    plan = make_plan(http, room["id"])
    refused = event(
        http, other["id"], plan["plan"]["external_id"], "DOCUMENT_OPENED", recipientEmail=SIGNER
    )
    assert refused.status_code == 409
    assert refused.json()["error"] == "plan_unresolved"


# --------------------------------------------------------------------------- #
# Reading, notices, summary
# --------------------------------------------------------------------------- #


def test_the_plan_list_reports_the_milestone_each_plan_is_at(http, room):
    make_plan(http, room["id"], subject="One")
    make_plan(http, room["id"], subject="Two")
    body = http.get(f"{PREFIX}/rooms/{room['id']}/plans").json()
    assert body["count"] == 2
    assert {row["milestone"] for row in body["plans"]} == {"draft"}


def test_the_plan_list_can_be_filtered_by_milestone(http, room):
    plan = make_plan(http, room["id"])
    http.post(f"{PREFIX}/rooms/{room['id']}/plans/{plan['id']}/distribute")
    body = http.get(
        f"{PREFIX}/rooms/{room['id']}/plans", params={"milestone": "awaiting_signature"}
    ).json()
    assert body["count"] == 1
    assert body["plans"][0]["id"] == plan["id"]


def test_the_summary_serves_the_two_invariants_over_http(http, room):
    body = http.get(f"{PREFIX}/rooms/{room['id']}/summary").json()
    assert body["invariants"]["never_sign_for_a_recipient"] is True
    assert body["invariants"]["signed_pdf_before_completion"] is False


def test_a_notice_can_be_acknowledged_over_http(http, room):
    plan = make_plan(http, room["id"])
    http.post(f"{PREFIX}/rooms/{room['id']}/plans/{plan['id']}/distribute")
    notices = http.get(f"{PREFIX}/rooms/{room['id']}/notices").json()
    assert notices["unread"] >= 1
    acknowledged = http.post(f"{PREFIX}/rooms/{room['id']}/notices/acknowledge", json={})
    assert acknowledged.json()["acknowledged"] >= 1
    assert http.get(f"{PREFIX}/rooms/{room['id']}/notices").json()["unread"] == 0


# --------------------------------------------------------------------------- #
# The audit rule, and the every-route rule CI runs
# --------------------------------------------------------------------------- #


def test_the_audit_source_names_the_route_that_served_the_write(http, room):
    """Every recorded source matches a route the app actually serves.

    Read after driving every write endpoint, and checked against *every* route,
    core included, because the audit log is shared: this test creates its room
    through the core records API, and that write's source is a core route.
    """
    plan = make_plan(http, room["id"])
    http.post(f"{PREFIX}/rooms/{room['id']}/templates", json={"name": "Standard"})
    http.post(f"{PREFIX}/rooms/{room['id']}/plans/{plan['id']}/distribute")
    event(http, room["id"], plan["plan"]["external_id"], "DOCUMENT_OPENED", recipientEmail=SIGNER)
    http.post(f"{PREFIX}/rooms/{room['id']}/notices/acknowledge", json={})

    store = client_store(http)
    rows = store.audit(limit=2000)
    assert rows, "the audit log is empty, so the check proved nothing"
    sources = {row["source"] for row in rows if row["source"]}
    routes = all_served_routes(http)
    # Checked against *every* route, core included, because the audit log is
    # shared: this test creates its room through the core records API, and that
    # write's source is a core route.
    for source in sorted(sources):
        assert source_names_a_mounted_route(source, routes), (
            f"{source!r} names no route this app serves"
        )
    assert any(source.startswith(f"POST {PREFIX}/") for source in sources), (
        "this feature recorded no source of its own, so the check proved nothing"
    )


def test_every_source_this_feature_records_is_under_its_own_prefix(http, room):
    """The sharper half. A hardcoded but perfectly valid core path fails this."""
    plan = make_plan(http, room["id"])
    http.post(f"{PREFIX}/rooms/{room['id']}/templates", json={"name": "Standard"})
    http.post(f"{PREFIX}/rooms/{room['id']}/plans/{plan['id']}/distribute")
    event(http, room["id"], plan["plan"]["external_id"], "DOCUMENT_OPENED", recipientEmail=SIGNER)
    http.post(f"{PREFIX}/rooms/{room['id']}/notices/acknowledge", json={})

    sources = {
        row["source"]
        for row in client_store(http).audit(limit=2000)
        if row["source"] and "map_" in row["collection"]
    }
    assert sources
    for source in sorted(sources):
        assert source.startswith(f"POST {PREFIX}/"), source


def test_every_route_answers_without_a_5xx_when_called_with_an_empty_body(http):
    """The tool CI runs substitutes ids and sends ``{}`` to every method it finds.

    A write route that 500s on an empty body fails the build, so this walks the
    same route table the tool walks and asserts the same rule.
    """
    faults: list[tuple[str, str, int]] = []
    for method, template in sorted(all_served_routes(http)):
        if not template.startswith(PREFIX):
            continue
        path = (
            template.replace("{room_id}", "absent-room")
            .replace("{plan_id}", "plan_absent")
            .replace("{template_id}", "tpl_absent")
        )
        if method not in {"GET", "POST", "PATCH", "DELETE"}:
            continue
        body = {} if method in {"POST", "PATCH", "PUT"} else None
        response = http.request(method, path, json=body)
        if response.status_code == 0 or response.status_code >= 500:
            faults.append((method, path, response.status_code))
    assert not faults, faults


def test_the_seeder_runs_and_its_return_string_is_printable_on_a_windows_console(tmp_path):
    """A single RIGHTWARDS ARROW in one recovered feature broke the whole seeder."""
    module = load_feature(MODULE)
    db = AuditedDatabase(tmp_path / "seed.db", actor="seed")
    try:
        store = RecordStore(db)
        store.create(
            "room",
            {"name": "Northwind", "account": "Northwind"},
            room_id="room-seed",
            actor="seed",
            source="seed",
        )
        reported = module.seed(
            db,
            {
                "room_ids": [("room-seed", "Northwind")],
                "now": datetime_now(),
                "rng": None,
            },
        )
        assert isinstance(reported, str)
        reported.encode("cp1252")
        print(reported)
        # The refusal states are named, because a demo of only green teaches a
        # reviewer nothing.
        for expected in (
            "event_before_distribution",
            "event_unauthenticated",
            "refused by the approver",
            "refused by a signer",
            "duplicate",
        ):
            assert expected in reported, expected
    finally:
        db.close()


def datetime_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def test_the_seeder_names_the_states_it_created_and_they_are_not_all_successes():
    """The seeder prints this string, so it must name what it did."""
    module = load_feature(MODULE)
    assert callable(module.seed)
