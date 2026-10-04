"""HTTP tests for WF-081: the routes, called the way the host serves them.

These run against the real application over a temporary database, so they answer
the questions a unit test cannot. Does the host mount the router by discovery
alone. Does the error handler turn a domain refusal into the status the refusal
carries. Does every ``source`` this feature records name a route the running app
actually serves.

The last of those is what this file exists for. The defect it catches shipped in
this codebase before: a feature's audit log kept naming a route the app had
stopped serving. The check is behavioural. It reads what the audit log recorded
after driving every write endpoint, and asks the running app what it mounted.

The deadline is the one thing these tests cannot choose. The engine reads the real
clock through ``get_engine``, so a deadline inside the researched 1-to-90-day
window is always in the future. A sweep cannot be driven past a deadline from
over HTTP, because the rules refuse to store one that has already passed, and that
refusal is the rule. What is testable here is that the sweep route answers
correctly with nothing due, and that everything the sweep does is tested against
the engine in ``test_wf081.py``, where the clock is a parameter.
"""

from __future__ import annotations

import re
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.security_governance import expiry_rules as rules, expiry_vocabulary as vocab
from dsr.store import RecordStore

#: The feature's own prefix, duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well.
PREFIX = "/api/wf-081"

MODULE = "wf081_expire_an_agreement_and_drive_pre_expiry"
FEATURE_ID = "wf-081-expire-an-agreement-and-drive-pre-expiry"

SIGNER = "buyer@northwind.example"
SIGNER_TWO = "legal@contoso.example"
REQUESTER = "dana@northwind.example"


def days_out(days: float) -> int:
    """A deadline inside the researched window, measured from the real clock."""
    return rules.epoch_seconds() + int(days * timedelta(days=1).total_seconds())


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


def mounted_routes(http, feature_id=FEATURE_ID):
    """Every (method, path) the host mounted for one feature, templates intact."""
    entry = next(
        (f for f in http.get("/api/features").json()["features"] if f["id"] == feature_id), None
    )
    assert entry is not None, f"{feature_id} is not mounted"
    return {(method, route["path"]) for route in entry["routes"] for method in route["methods"]}


def all_served_routes(http):
    """Every (method, path) the running app serves, core routes included.

    The audit log is shared, so a check of what it records has to be allowed to
    see a core write as well as a feature one.
    """
    routes = set()
    for route in app.routes:
        for method in getattr(route, "methods", None) or set():
            if method in ("HEAD", "OPTIONS"):
                continue
            routes.add((method, getattr(route, "path", "")))
    for feature in http.get("/api/features").json()["features"]:
        for route in feature["routes"]:
            for method in route["methods"]:
                routes.add((method, route["path"]))
    return routes


def source_names_a_mounted_route(source, routes) -> bool:
    """Does ``"POST /api/wf-081/rooms/abc/requests"`` name a route that exists?

    A recorded source carries concrete ids. A mounted path carries FastAPI's
    ``{param}`` placeholders. The pattern is built from the template and matched
    against the source, with each placeholder as one wildcard segment and the
    literal parts escaped, so a segment containing a regex metacharacter cannot
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


def send_request(http, room_id, **overrides):
    payload = {
        "subject": "Master services agreement",
        "requester_email": REQUESTER,
        "signatures": [{"email": SIGNER, "name": "Ada Byron", "preferred_timezone": "+05:30"}],
    }
    payload.update(overrides)
    return http.post(f"{PREFIX}/rooms/{room_id}/requests", json=payload)


def make_request(http, room_id, **overrides):
    response = send_request(http, room_id, **overrides)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery_alone(http):
    """api.py is not edited, and the routes resolve anyway."""
    routes = mounted_routes(http)
    assert ("GET", f"{PREFIX}/vocabulary") in routes
    assert ("GET", f"{PREFIX}/inferences") in routes
    assert ("POST", f"{PREFIX}/rooms/{{room_id}}/requests") in routes
    assert ("PUT", f"{PREFIX}/rooms/{{room_id}}/requests/{{request_id}}/expiry") in routes
    assert ("POST", f"{PREFIX}/rooms/{{room_id}}/requests/{{request_id}}/reminders") in routes
    assert ("POST", f"{PREFIX}/rooms/{{room_id}}/sweep") in routes
    assert ("GET", f"{PREFIX}/rooms/{{room_id}}/summary") in routes


def test_the_feature_reports_its_prefix_and_ticket(http):
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)
    assert entry["ticket"] == "WF-081"
    assert entry["prefix"] == PREFIX


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
    """The files this branch changed against main, relative to the root."""
    out = subprocess.run(
        ["git", "diff", "--name-only", "origin/main...HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return {line.strip().replace("\\", "/") for line in out.stdout.splitlines() if line.strip()}


def test_the_feature_module_opens_no_connection_and_imports_no_app():
    module = load_feature(MODULE)
    text = Path(module.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in text
    assert "import dsr.api" not in text
    assert "import sqlite3" not in text
    assert "sqlite3.connect" not in text


def test_the_domain_package_imports_nothing_but_the_store():
    """>Every rule reaches storage through the store the HTTP layer hands in."""
    package = Path(rules.__file__).parent
    for name in ("expiry_rules.py", "expiry_vocabulary.py", "expiry_inferences.py"):
        text = (package / name).read_text(encoding="utf-8")
        assert "import sqlite3" not in text, name
        assert "dsr.api" not in text, name


# --------------------------------------------------------------------------- #
# Vocabulary and inferences
# --------------------------------------------------------------------------- #


def test_the_vocabulary_serves_the_cadence_and_the_window(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["reminder_rule"]["lead_days"] == [7, 3]
    assert body["reminder_rule"]["dedupe_window_hours"] == 24
    assert body["expiry_rule"]["min_days"] == 1
    assert body["expiry_rule"]["max_days"] == 90


def test_the_vocabulary_serves_the_event_name_the_research_quotes(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["delivery"]["event"] == "signature_request_expired"
    assert body["delivery"]["email_muted_when_embedded"] is True


def test_the_vocabulary_serves_the_invariant_that_expiry_keeps_the_document(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert "will still have access to the document" in body["invariants"]["document_survives"]


def test_the_inferences_register_is_served_over_http(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["ticket"] == "WF-081"
    ids = {entry["id"] for entry in body["decisions"]}
    assert "which-expiry-shape" in ids
    assert "rounding-rounds-down-not-up" in ids
    assert "no-expiry-means-never" in ids


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #


def test_a_request_is_sent_with_its_deadline(http, room):
    body = make_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(10)})
    assert body["status"] == vocab.REQUEST_STATUS_PENDING
    assert body["has_expiry"] is True
    assert body["expires_at"] % 3600 == 0


def test_a_request_sent_with_no_expiry_never_expires(http, room):
    """By default signature requests do not expire."""
    body = make_request(http, room["id"])
    assert body["has_expiry"] is False
    assert body["expires_at"] is None
    assert "expiry_view" not in body


@pytest.mark.parametrize("days", [0.5, 91, 365])
def test_a_deadline_outside_the_window_is_refused_with_422(http, room, days):
    response = send_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(days)})
    assert response.status_code == 422
    assert response.json()["error"] == "expiry_out_of_range"


def test_a_deadline_that_is_not_an_integer_is_refused_with_422(http, room):
    response = send_request(http, room["id"], **{vocab.EXPIRES_AT: "soon"})
    assert response.status_code == 422
    assert response.json()["error"] == "expiry_not_an_integer"


def test_a_refused_send_leaves_nothing_behind(http, room):
    """A request that changed nothing must not appear in the audit log."""
    store = client_store(http)
    before = len(store.audit(limit=2000))
    send_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(200)})
    assert store.list(vocab.EXPIRY_COLLECTION) == []
    assert len(store.audit(limit=2000)) == before


def test_an_embedded_request_is_sent_with_its_email_muted(http, room):
    body = make_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(10), "flow": "embedded"})
    assert body["flow"] == vocab.FLOW_EMBEDDED
    assert body["email_muted"] is True


def test_an_unknown_delivery_mode_is_refused_without_a_5xx(http, room):
    response = send_request(http, room["id"], **{"flow": "carrier-pigeon"})
    assert response.status_code == 422


def test_a_signer_with_no_address_is_dropped(http, room):
    body = make_request(
        http,
        room["id"],
        **{vocab.EXPIRES_AT: days_out(10), "signatures": [{"name": "Nobody"}]},
    )
    assert body["signatures"] == []


def test_a_field_this_workflow_does_not_read_is_kept(http, room):
    """A team adding a field must need no coordination with anyone."""
    body = make_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(10), "deal_value": 42000})
    stored = client_store(http).get(body["id"])
    assert stored["data"]["deal_value"] == 42000


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def test_the_request_list_can_be_filtered_by_terminal_status(http, room):
    make_request(http, room["id"], subject="One", **{vocab.EXPIRES_AT: days_out(10)})
    make_request(http, room["id"], subject="Two")
    body = http.get(f"{PREFIX}/rooms/{room['id']}/requests", params={"status": "pending"}).json()
    assert body["count"] == 2
    expired = http.get(f"{PREFIX}/rooms/{room['id']}/requests", params={"status": "expired"}).json()
    assert expired["count"] == 0


def test_a_request_reads_in_the_callers_timezone(http, room):
    body = make_request(
        http,
        room["id"],
        **{vocab.EXPIRES_AT: days_out(10), "signatures": [{"email": SIGNER}]},
    )
    read = http.get(
        f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}", params={"tz": "+05:30"}
    ).json()
    assert read["expiry_view"]["offset_minutes"] == 330
    assert read["expiry_view"]["epoch_seconds"] == body["expires_at"]


def test_the_signer_banner_carries_the_deadline_and_the_field_count(http, room):
    body = make_request(
        http,
        room["id"],
        **{
            vocab.EXPIRES_AT: days_out(10),
            "signatures": [
                {"email": SIGNER, "preferred_timezone": "-04:00"},
                {"email": SIGNER_TWO},
            ],
        },
    )
    banner = body["signatures"][0]["expiry_banner"]
    assert banner["required_fields"] == 2
    assert banner["offset_minutes"] == -240
    assert banner["known"] is True


def test_an_unknown_request_is_refused_with_404(http, room):
    response = http.get(f"{PREFIX}/rooms/{room['id']}/requests/no-such-request")
    assert response.status_code == 404
    assert response.json()["error"] == "expiry_request_not_found"


def test_a_room_id_that_does_not_exist_does_not_5xx(http):
    response = http.get(f"{PREFIX}/rooms/absent-room/requests")
    assert response.status_code == 200
    assert response.json()["count"] == 0


# --------------------------------------------------------------------------- #
# Moving the deadline
# --------------------------------------------------------------------------- #


def test_a_deadline_can_be_moved(http, room):
    body = make_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(10)})
    moved = http.put(
        f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/expiry",
        json={vocab.EXPIRES_AT: days_out(30)},
    )
    assert moved.status_code == 200
    assert moved.json()["expires_at"] == rules.round_down_to_hour(days_out(30))


def test_a_deadline_can_be_cleared_with_an_explicit_null(http, room):
    body = make_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(10)})
    cleared = http.put(
        f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/expiry",
        json={vocab.EXPIRES_AT: None},
    )
    assert cleared.json()["has_expiry"] is False


def test_a_moved_deadline_outside_the_window_is_refused_with_422(http, room):
    body = make_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(10)})
    refused = http.put(
        f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/expiry",
        json={vocab.EXPIRES_AT: days_out(200)},
    )
    assert refused.status_code == 422
    assert refused.json()["error"] == "expiry_out_of_range"


def test_moving_a_deadline_on_an_unknown_request_is_refused_with_404(http, room):
    response = http.put(
        f"{PREFIX}/rooms/{room['id']}/requests/no-such-request/expiry",
        json={vocab.EXPIRES_AT: days_out(10)},
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Reminders
# --------------------------------------------------------------------------- #


def test_no_reminder_is_due_far_from_the_deadline(http, room):
    """>The cadence is 3 and 7 days, and 30 days out is neither."""
    body = make_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(30)})
    report = http.post(f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/reminders", json={})
    assert report.status_code == 201
    assert report.json()["sent"] == 0
    assert report.json()["decisions"][0]["reason"] == "no_window_open"


def test_a_reminder_inside_the_window_is_sent_and_recorded(http, room):
    body = make_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(2.5)})
    report = http.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/reminders", json={}
    ).json()
    assert report["sent"] == 1
    assert report["decisions"][0]["lead_days"] == 3
    assert report["decisions"][0]["channel"] == "email"

    ledger = http.get(
        f"{PREFIX}/rooms/{room['id']}/reminders", params={"request_id": body["id"]}
    ).json()
    assert ledger["count"] == 1
    assert ledger["sent"] == 1
    assert ledger["reminders"][0]["outcome"] == "sent"


def test_a_request_with_no_expiry_is_never_reminded(http, room):
    body = make_request(http, room["id"])
    report = http.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/reminders", json={}
    ).json()
    assert report["sent"] == 0
    assert report["decisions"][0]["reason"] == "no_window_open"


def test_an_embedded_request_records_an_event_and_no_email(http, room):
    """>"Emails are muted in all embedded signing flows"."""
    body = make_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(2.5), "flow": "embedded"})
    report = http.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/reminders", json={}
    ).json()
    assert report["email_muted"] is True
    assert report["decisions"][0]["channel"] == "event"

    events = http.get(
        f"{PREFIX}/rooms/{room['id']}/events", params={"event": vocab.EVENT_REMINDER_SENT}
    ).json()
    assert events["count"] == 1
    assert events["events"][0]["channel"] == "event"


def test_reminding_an_unknown_signer_is_refused_with_404(http, room):
    body = make_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(2.5)})
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/reminders",
        json={"email": "stranger@example.com"},
    )
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_signer"


def test_reminders_on_an_unknown_request_are_refused_with_404(http, room):
    response = http.post(f"{PREFIX}/rooms/{room['id']}/requests/no-such-request/reminders", json={})
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Signing
# --------------------------------------------------------------------------- #


def test_a_signer_signs_while_the_request_is_open(http, room):
    body = make_request(
        http,
        room["id"],
        **{
            vocab.EXPIRES_AT: days_out(10),
            "signatures": [{"email": SIGNER}, {"email": SIGNER_TWO}],
        },
    )
    signed = http.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/sign", json={"email": SIGNER}
    )
    assert signed.status_code == 201
    codes = {row["email"]: row["status_code"] for row in signed.json()["signatures"]}
    assert codes == {SIGNER: "signed", SIGNER_TWO: "awaiting_signature"}


def test_a_signer_signing_twice_is_refused_with_409(http, room):
    body = make_request(
        http,
        room["id"],
        **{
            vocab.EXPIRES_AT: days_out(10),
            "signatures": [{"email": SIGNER}, {"email": SIGNER_TWO}],
        },
    )
    http.post(f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/sign", json={"email": SIGNER})
    again = http.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/sign", json={"email": SIGNER}
    )
    assert again.status_code == 409
    assert again.json()["error"] == "already_signed"


def test_a_signer_not_on_the_request_is_refused_with_404(http, room):
    body = make_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(10)})
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/sign",
        json={"email": "stranger@example.com"},
    )
    assert response.status_code == 404


def test_signing_without_an_email_is_refused_with_404(http, room):
    body = make_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(10)})
    response = http.post(f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/sign", json={})
    assert response.status_code == 404


def test_can_sign_reports_open_for_a_signer_who_has_not_signed(http, room):
    body = make_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(10)})
    gate = http.get(
        f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/can-sign", params={"email": SIGNER}
    ).json()
    assert gate["can_sign"] is True
    assert gate["reason"] == "open"
    assert gate["closed"] is False


def test_can_sign_reports_closed_once_every_signer_has_signed(http, room):
    """A request every signer completed is complete, and complete is terminal."""
    body = make_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(10)})
    http.post(f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/sign", json={"email": SIGNER})
    gate = http.get(
        f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/can-sign", params={"email": SIGNER}
    ).json()
    assert gate["can_sign"] is False
    assert gate["reason"] == "already_signed"
    assert gate["status"] == vocab.REQUEST_STATUS_COMPLETED


def test_can_sign_without_an_email_reports_the_request_level_answer(http, room):
    body = make_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(10)})
    gate = http.get(f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/can-sign").json()
    assert gate["can_sign"] is True
    assert gate["reason"] == "open"


# --------------------------------------------------------------------------- #
# The sweep
# --------------------------------------------------------------------------- #


def test_a_sweep_with_nothing_due_sweeps_nothing_and_says_what_it_left(http, room):
    """No deadline is in the past, because the rules refuse to store one.

    That is the researched rule working: a request cannot be created already
    expired, so over HTTP the sweep always finds nothing due. It still has to
    answer correctly and name what it skipped.
    """
    make_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(30)})
    make_request(http, room["id"])
    report = http.post(f"{PREFIX}/rooms/{room['id']}/sweep", json={})
    assert report.status_code == 201
    body = report.json()
    assert body["swept_count"] == 0
    assert body["documents_deleted"] == 0
    reasons = {row["reason"] for row in body["skipped"]}
    assert reasons == {"deadline_not_passed", "no_expiry_set"}


def test_the_sweep_report_carries_the_invariants(http, room):
    body = http.post(f"{PREFIX}/rooms/{room['id']}/sweep", json={}).json()
    assert "will still have access to the document" in body["invariants"]["document_survives"]
    assert "Completed signers" in body["invariants"]["completed_signers_kept"]
    assert body["invariants"]["link_survives"]


def test_a_sweep_narrowed_to_one_request_answers_without_a_5xx(http, room):
    body = make_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(30)})
    report = http.post(f"{PREFIX}/rooms/{room['id']}/sweep", json={"request_id": body["id"]}).json()
    assert report["swept_count"] == 0


# --------------------------------------------------------------------------- #
# The summary
# --------------------------------------------------------------------------- #


def test_the_summary_counts_the_room_and_serves_the_invariants(http, room):
    make_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(30)})
    make_request(http, room["id"])
    body = http.get(f"{PREFIX}/rooms/{room['id']}/summary").json()
    assert body["requests"] == 2
    assert body["with_expiry"] == 1
    assert body["never_expires"] == 1
    assert body["pending"] == 2
    assert "explicitly set" in body["invariants"]["absent_expiry_never_expires"]


# --------------------------------------------------------------------------- #
# The audit rule, and the every-route rule CI runs
# --------------------------------------------------------------------------- #


def test_the_audit_source_names_the_route_that_served_the_write(http, room):
    """Every recorded source matches a route the app actually serves."""
    body = make_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(2.5)})
    http.put(
        f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/expiry",
        json={vocab.EXPIRES_AT: days_out(30)},
    )
    http.post(f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/reminders", json={})
    http.post(f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/sign", json={"email": SIGNER})
    http.post(f"{PREFIX}/rooms/{room['id']}/sweep", json={})

    store = client_store(http)
    rows = store.audit(limit=2000)
    assert rows, "the audit log is empty, so the check proved nothing"
    sources = {row["source"] for row in rows if row["source"]}
    routes = all_served_routes(http)
    # Checked against every route, core included, because the audit log is
    # shared: this test creates its room through the core records API.
    for source in sorted(sources):
        assert source_names_a_mounted_route(source, routes), (
            f"{source!r} names no route this app serves"
        )
    assert any(source.startswith(f"POST {PREFIX}/") for source in sources), (
        "this feature recorded no source of its own, so the check proved nothing"
    )


def test_every_source_this_feature_records_is_under_its_own_prefix(http, room):
    """The sharper half. A hardcoded but perfectly valid core path fails this."""
    body = make_request(http, room["id"], **{vocab.EXPIRES_AT: days_out(2.5)})
    http.post(f"{PREFIX}/rooms/{room['id']}/requests/{body['id']}/reminders", json={})
    http.post(f"{PREFIX}/rooms/{room['id']}/sweep", json={})

    sources = {
        row["source"]
        for row in client_store(http).audit(limit=2000)
        if row["source"] and vocab.EXPIRY_COLLECTION in row["collection"]
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
        path = template.replace("{room_id}", "absent-room").replace("{request_id}", "req_absent")
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            continue
        body = {} if method in {"POST", "PUT", "PATCH"} else None
        response = http.request(method, path, json=body)
        if response.status_code == 0 or response.status_code >= 500:
            faults.append((method, path, response.status_code))
    assert not faults, faults


# --------------------------------------------------------------------------- #
# The seeder
# --------------------------------------------------------------------------- #


def test_the_seeder_runs_and_its_return_string_is_printable_on_a_windows_console(tmp_path):
    """A single RIGHTWARDS ARROW in one recovered feature broke the whole seeder."""
    from datetime import datetime, timezone

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
                "now": datetime(2026, 10, 4, tzinfo=timezone.utc),
                "rng": None,
            },
        )
        assert isinstance(reported, str)
        reported.encode("cp1252")
        print(reported)
        # The states are named, because a demo of only green teaches a reviewer
        # nothing.
        for expected in (
            "no expires_at",
            "unsigned signature(s) to expired",
            "signer(s) signed",
            "24-hour dedupe",
            "signature_request_expired",
        ):
            assert expected in reported, expected
    finally:
        db.close()


def test_the_seeder_names_the_states_it_created_and_they_are_not_all_successes():
    module = load_feature(MODULE)
    assert callable(module.seed)
