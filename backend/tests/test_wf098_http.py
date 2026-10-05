"""HTTP tests for WF-098: the routes, called the way the host serves them.

These run against the real application over a temporary database, so they answer the
questions a unit test cannot. Does the host mount the router by discovery alone. Does the
error handler turn a domain refusal into the status the refusal carries. Does every
``source`` this feature records name a route the running app actually serves.

The last of those is what this file exists for. The defect it catches shipped in this
codebase before: a feature's audit log kept naming a route the app had stopped serving.
The check is behavioural. It reads what the audit log recorded after driving every write
endpoint, and asks the running app what it mounted.

The clock is the one thing these tests cannot choose. The engine reads the real clock
through ``get_engine``, so an expiration date inside the researched 1-to-365-day window is
always in the future unless the caller asks for a past one, which the switch and the date
picker both allow. The expiry check therefore cannot be driven into a state where a quote
is due, because the rules refuse to let a test stage one, and that refusal is the rule.
What is testable here is that the check answers correctly with nothing due, that a quote
whose deadline is in the past is refused an acceptance, and that everything the check does
is tested against the engine in ``test_wf098.py``, where the clock is a parameter.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.api import app
from dsr.features import load_feature
from dsr.quoting_proposals import quote_expiry_rules as rules, quote_expiry_vocabulary as vocab
from dsr.store import RecordStore

#: The feature's own prefix, duplicated here rather than imported so a change to the
#: prefix has to be made deliberately in the test as well.
PREFIX = "/api/wf-098"

MODULE = "wf098_expire_a_quote_and_auto_send_buyer_reminders"
FEATURE_ID = "wf-098-expire-a-quote-and-auto-send-buyer-reminders"

DAY = 86400


def now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def in_days(days: float) -> str:
    """An ISO instant ``days`` from the real clock, comfortably inside the researched window."""

    moment = datetime.now(timezone.utc) + timedelta(days=days)
    return moment.isoformat(timespec="milliseconds")


def days_ago(days: float) -> str:
    return in_days(-days)


@pytest.fixture()
def http(client):
    """The shared application, over a database this test owns alone.

    ``client`` is conftest's: it assigns a fresh, empty database onto ``app.state`` and
    undoes it afterwards, so no test in this file can see another's rows and none of them
    leaks into the next module.
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

    The audit log is shared, so a check of what it records has to be allowed to see a core
    write as well as a feature one.
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


def source_names_a_mounted_route(source: str, routes) -> bool:
    """Does ``"POST /api/wf-098/rooms/abc/quotes"`` name a route that exists?

    A recorded source carries concrete ids. A mounted path carries FastAPI's ``{param}``
    placeholders. The pattern is built from the template and matched against the source,
    with each placeholder as one wildcard segment and the literal parts escaped, so a
    segment containing a regex metacharacter cannot make the pattern match something else.
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


def quote_payload(**overrides) -> dict:
    payload = {
        "title": "Enterprise platform - Northwind",
        "seller_email": "dana@northwind.example",
        "recipients": [{"email": "buyer@northwind.example", "name": "Ada Byron"}],
        vocab.EXPIRATION_DATE: in_days(10),
        vocab.EFFECTIVE_DATE: days_ago(5),
    }
    payload.update(overrides)
    return payload


def make_quote(http, room_id, **overrides):
    response = http.post(f"{PREFIX}/rooms/{room_id}/quotes", json=quote_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def sent_quote(http, room_id, **overrides):
    created = make_quote(http, room_id, **overrides)
    response = http.post(f"{PREFIX}/rooms/{room_id}/quotes/{created['id']}/send", json={})
    assert response.status_code == 201, response.text
    return response.json()


def save_settings(http, room_id, **overrides):
    payload = {
        vocab.DEFAULT_EXPIRATION_DAYS: 30,
        vocab.ACCOUNT_TIMEZONE: "UTC",
        vocab.REMINDER_SEND_TIME: "09:00",
        vocab.AUTOMATED_REMINDERS_ENABLED: True,
    }
    payload.update(overrides)
    response = http.put(f"{PREFIX}/rooms/{room_id}/settings", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def add_rule(http, room_id, **overrides):
    payload = {"offset_kind": vocab.OFFSET_AFTER_SEND, "days": 3}
    payload.update(overrides)
    response = http.post(f"{PREFIX}/rooms/{room_id}/reminder-rules", json=payload)
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
    assert ("GET", f"{PREFIX}/decisions/{{decision_id}}") in routes
    assert ("GET", f"{PREFIX}/rooms/{{room_id}}/settings") in routes
    assert ("PUT", f"{PREFIX}/rooms/{{room_id}}/settings") in routes
    assert ("GET", f"{PREFIX}/rooms/{{room_id}}/reminder-rules") in routes
    assert ("POST", f"{PREFIX}/rooms/{{room_id}}/reminder-rules") in routes
    assert ("PATCH", f"{PREFIX}/rooms/{{room_id}}/reminder-rules/{{rule_id}}") in routes
    assert ("DELETE", f"{PREFIX}/rooms/{{room_id}}/reminder-rules/{{rule_id}}") in routes
    assert ("GET", f"{PREFIX}/rooms/{{room_id}}/reminder-rules/{{rule_id}}/preview") in routes
    assert ("POST", f"{PREFIX}/rooms/{{room_id}}/quotes") in routes
    assert ("GET", f"{PREFIX}/rooms/{{room_id}}/quotes") in routes
    assert ("PUT", f"{PREFIX}/rooms/{{room_id}}/quotes/{{quote_id}}/expiration") in routes
    assert ("POST", f"{PREFIX}/rooms/{{room_id}}/quotes/{{quote_id}}/send") in routes
    assert ("POST", f"{PREFIX}/rooms/{{room_id}}/quotes/{{quote_id}}/acceptance") in routes
    assert ("GET", f"{PREFIX}/rooms/{{room_id}}/quotes/{{quote_id}}/can-accept") in routes
    assert ("POST", f"{PREFIX}/rooms/{{room_id}}/quotes/{{quote_id}}/void") in routes
    assert ("POST", f"{PREFIX}/rooms/{{room_id}}/quotes/{{quote_id}}/archive") in routes
    assert ("POST", f"{PREFIX}/rooms/{{room_id}}/reminders") in routes
    assert ("GET", f"{PREFIX}/rooms/{{room_id}}/reminders") in routes
    assert ("POST", f"{PREFIX}/rooms/{{room_id}}/expiry-check") in routes
    assert ("GET", f"{PREFIX}/rooms/{{room_id}}/activities") in routes
    assert ("GET", f"{PREFIX}/rooms/{{room_id}}/summary") in routes


def test_the_feature_reports_its_prefix_and_ticket(http):
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)
    assert entry["ticket"] == "WF-098"
    assert entry["prefix"] == PREFIX


def test_no_feature_failed_to_load(http):
    """A module that raises on import is recorded rather than taking the app down."""

    body = http.get("/api/features").json()
    assert body.get("failed_count", 0) == 0, body.get("failed")


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
    for name in (
        "quote_expiry_rules.py",
        "quote_expiry_vocabulary.py",
        "quote_expiry_inferences.py",
        "quote_expiry_engine.py",
    ):
        text = (package / name).read_text(encoding="utf-8")
        assert "import sqlite3" not in text, name
        assert "dsr.api" not in text, name


def test_the_domain_package_declares_no_migration_and_no_typed_column():
    """Payloads stay schema-flexible: the envelope is the only fixed vocabulary."""

    package = Path(rules.__file__).parent
    for name in (
        "quote_expiry_rules.py",
        "quote_expiry_vocabulary.py",
        "quote_expiry_inferences.py",
        "quote_expiry_engine.py",
    ):
        text = (package / name).read_text(encoding="utf-8").lower()
        assert "create table" not in text, name
        assert "alter table" not in text, name
        assert "add column" not in text, name


# --------------------------------------------------------------------------- #
# Vocabulary and inferences
# --------------------------------------------------------------------------- #


def test_the_vocabulary_serves_the_window_the_research_quotes(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["expiration_rule"]["min_default_days"] == 1
    assert body["expiration_rule"]["max_default_days"] == 365


def test_the_vocabulary_serves_both_offset_labels_verbatim(http):
    quotes = http.get(f"{PREFIX}/vocabulary").json()["reminder_rule"]["offset_quotes"]
    assert quotes[vocab.OFFSET_AFTER_SEND] == "Days after sending quote"
    assert quotes[vocab.OFFSET_BEFORE_EXPIRY] == "Days before expiration date"


def test_the_vocabulary_serves_exactly_three_surviving_actions(http):
    acceptance = http.get(f"{PREFIX}/vocabulary").json()["acceptance"]
    assert acceptance["surviving_actions"] == ["accepted", "e_signed", "marked_signed"]
    assert "won't expire" in acceptance["survival_quote"]


def test_the_vocabulary_serves_the_three_acceptance_methods(http):
    methods = http.get(f"{PREFIX}/vocabulary").json()["acceptance"]["methods"]
    assert [row["method"] for row in methods] == list(vocab.ACCEPTANCE_METHODS)


def test_the_vocabulary_serves_the_survives_and_void_and_archive_sentences(http):
    invariants = http.get(f"{PREFIX}/vocabulary").json()["invariants"]
    assert "downloaded, cloned, voided or archived" in invariants["expiry_survives"]
    assert "lost the ability to accept" in invariants["acceptance_closed"]
    assert "new send" in invariants["resend_is_a_new_send"]


def test_the_vocabulary_serves_the_recorded_settings_api_gap(http):
    gap = http.get(f"{PREFIX}/vocabulary").json()["reminder_rule"]["settings_api_gap"]
    assert "no documented public write API" in gap


def test_the_vocabulary_serves_the_pandadoc_webhook_names(http):
    webhook = http.get(f"{PREFIX}/vocabulary").json()["webhook"]
    assert webhook["event"] == "document_state_changed"
    assert webhook["expiration_field"] == "expiration_date"


def test_the_vocabulary_serves_every_skip_reason_with_text(http):
    reasons = http.get(f"{PREFIX}/vocabulary").json()["reminder_rule"]["skip_reasons"]
    assert len(reasons) == len(vocab.SKIP_REASONS)
    assert all(row["text"].strip() for row in reasons)


def test_the_inferences_register_is_served_over_http(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["ticket"] == "WF-098"
    assert body["issue"] == 130
    ids = {entry["id"] for entry in body["decisions"]}
    assert "job-driver" in ids
    assert "domain-package-placement" in ids


def test_the_inferences_register_names_both_jev_audits(http):
    audits = http.get(f"{PREFIX}/inferences").json()["jev_design_audits"]
    assert audits["domain_package_placement"].startswith("jev-")
    assert audits["job_driver"].startswith("jev-")


def test_every_served_decision_names_its_rejected_alternative(http):
    for entry in http.get(f"{PREFIX}/inferences").json()["decisions"]:
        assert entry["rejected"].strip(), entry["id"]
        assert entry["consequence"].strip(), entry["id"]


def test_one_decision_can_be_read_by_id(http):
    body = http.get(f"{PREFIX}/decisions/job-driver").json()
    assert body["found"] is True
    assert body["id"] == "job-driver"


def test_an_unknown_decision_answers_found_false_rather_than_404(http):
    """A report about a missing record is a report, not a crash."""

    body = http.get(f"{PREFIX}/decisions/no-such-decision").json()
    assert body["found"] is False


# --------------------------------------------------------------------------- #
# Step 1: the default expiration period
# --------------------------------------------------------------------------- #


def test_settings_for_an_unconfigured_room_answer_with_defaults(http, room):
    body = http.get(f"{PREFIX}/rooms/{room['id']}/settings").json()
    assert body["stored"] is False
    assert body[vocab.DEFAULT_EXPIRATION_DAYS] is None
    assert body[vocab.REMINDER_SEND_TIME] == "09:00"
    assert body[vocab.AUTOMATED_REMINDERS_ENABLED] is True
    assert "no documented public write API" in body["gap"]


@pytest.mark.parametrize("days", [1, 30, 365])
def test_a_default_window_inside_the_range_is_accepted(http, room, days):
    body = save_settings(http, room["id"], **{vocab.DEFAULT_EXPIRATION_DAYS: days})
    assert body[vocab.DEFAULT_EXPIRATION_DAYS] == days
    assert body["action"] == "created"


@pytest.mark.parametrize("days", [0, -5, 366, 1000])
def test_a_default_window_outside_the_range_is_refused_with_422(http, room, days):
    response = http.put(
        f"{PREFIX}/rooms/{room['id']}/settings", json={vocab.DEFAULT_EXPIRATION_DAYS: days}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "default_expiration_out_of_range"


def test_a_refused_default_window_writes_nothing(http, room):
    """A rejected setting leaves no row and no audit row describing a change."""

    http.put(f"{PREFIX}/rooms/{room['id']}/settings", json={vocab.DEFAULT_EXPIRATION_DAYS: 400})
    assert http.get(f"{PREFIX}/rooms/{room['id']}/settings").json()["stored"] is False


def test_settings_saved_twice_are_updated_rather_than_duplicated(http, room):
    first = save_settings(http, room["id"])
    assert first["action"] == "created"
    second = save_settings(http, room["id"], **{vocab.DEFAULT_EXPIRATION_DAYS: 60})
    assert second["action"] == "updated"
    assert second[vocab.DEFAULT_EXPIRATION_DAYS] == 60


def test_a_cleared_default_is_accepted(http, room):
    """ "Any new quotes created after the setting is turned on" - a cleared default is a
    real operation and not an omission."""

    save_settings(http, room["id"])
    cleared = http.put(
        f"{PREFIX}/rooms/{room['id']}/settings", json={vocab.DEFAULT_EXPIRATION_DAYS: None}
    ).json()
    assert cleared[vocab.DEFAULT_EXPIRATION_DAYS] is None


@pytest.mark.parametrize("value", ["9am", "24:00", "noon", "09:60"])
def test_a_send_time_that_is_not_a_wall_clock_reading_is_refused(http, room, value):
    response = http.put(
        f"{PREFIX}/rooms/{room['id']}/settings", json={vocab.REMINDER_SEND_TIME: value}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "send_time_not_a_time"


def test_a_zone_this_build_cannot_resolve_is_stored_and_reported_as_unknown(http, room):
    body = save_settings(http, room["id"], **{vocab.ACCOUNT_TIMEZONE: "Mars/Olympus"})
    assert body[vocab.ACCOUNT_TIMEZONE] == "Mars/Olympus"
    assert body["zone_known"] is False
    assert body["timezone_note"] == vocab.NOTE_UNRESOLVED_TIMEZONE


def test_a_fixed_offset_zone_is_always_resolved(http, room):
    body = save_settings(http, room["id"], **{vocab.ACCOUNT_TIMEZONE: "+05:30"})
    assert body["zone_known"] is True
    assert body["timezone_note"] is None


# --------------------------------------------------------------------------- #
# Step 2: the reminder schedule
# --------------------------------------------------------------------------- #


def test_the_schedule_starts_empty_and_reports_the_offset_kinds(http, room):
    body = http.get(f"{PREFIX}/rooms/{room['id']}/reminder-rules").json()
    assert body["count"] == 0
    assert [row["kind"] for row in body["offset_kinds"]] == list(vocab.OFFSET_KINDS)
    assert "no documented public write API" in body["gap"]


def test_a_rule_is_added_with_the_vendors_own_offset_quote(http, room):
    rule = add_rule(http, room["id"], offset_kind=vocab.OFFSET_AFTER_SEND, days=3)
    assert rule["offset_quote"] == "Days after sending quote"
    assert rule["days"] == 3
    assert rule["enabled"] is True


def test_a_pre_expiry_rule_reports_its_own_offset_quote(http, room):
    rule = add_rule(http, room["id"], offset_kind=vocab.OFFSET_BEFORE_EXPIRY, days=2)
    assert rule["offset_quote"] == "Days before expiration date"


def test_two_rules_with_the_same_days_are_two_rules(http, room):
    first = add_rule(http, room["id"], days=3)
    second = add_rule(http, room["id"], days=3)
    assert first["id"] != second["id"]
    assert http.get(f"{PREFIX}/rooms/{room['id']}/reminder-rules").json()["count"] == 2


def test_a_rule_with_no_offset_kind_is_refused_with_422(http, room):
    response = http.post(f"{PREFIX}/rooms/{room['id']}/reminder-rules", json={"days": 3})
    assert response.status_code == 422
    assert response.json()["error"] == "unknown_offset_kind"
    assert http.get(f"{PREFIX}/rooms/{room['id']}/reminder-rules").json()["count"] == 0


@pytest.mark.parametrize("days", [-1, 1.5, "three"])
def test_a_rule_with_an_impossible_number_of_days_is_refused(http, room, days):
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/reminder-rules",
        json={"offset_kind": vocab.OFFSET_AFTER_SEND, "days": days},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "offset_days_not_an_integer"


def test_a_rule_can_be_patched_and_names_what_changed(http, room):
    rule = add_rule(http, room["id"], days=3)
    response = http.patch(
        f"{PREFIX}/rooms/{room['id']}/reminder-rules/{rule['id']}", json={"days": 5}
    )
    assert response.status_code == 200
    assert response.json()["days"] == 5
    assert response.json()["changed"] == ["days"]


def test_a_rule_can_be_disabled_without_being_deleted(http, room):
    rule = add_rule(http, room["id"], days=3)
    http.patch(f"{PREFIX}/rooms/{room['id']}/reminder-rules/{rule['id']}", json={"enabled": False})
    body = http.get(f"{PREFIX}/rooms/{room['id']}/reminder-rules").json()
    assert body["count"] == 1
    assert body["rules"][0]["enabled"] is False


def test_a_deleted_rule_is_gone_and_its_ledger_is_kept(http, room):
    rule = add_rule(http, room["id"], days=3)
    response = http.delete(f"{PREFIX}/rooms/{room['id']}/reminder-rules/{rule['id']}")
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert http.get(f"{PREFIX}/rooms/{room['id']}/reminder-rules").json()["count"] == 0


def test_an_unknown_rule_is_404_over_http(http, room):
    response = http.get(f"{PREFIX}/rooms/{room['id']}/reminder-rules/rule_absent")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_reminder_rule"


def test_deleting_an_unknown_rule_is_404_over_http(http, room):
    response = http.delete(f"{PREFIX}/rooms/{room['id']}/reminder-rules/rule_absent")
    assert response.status_code == 404


def test_the_preview_route_answers_with_the_reminder_text(http, room):
    rule = add_rule(http, room["id"], days=3)
    sent_quote(http, room["id"])
    body = http.get(f"{PREFIX}/rooms/{room['id']}/reminder-rules/{rule['id']}/preview").json()
    assert body["subject"].startswith("Reminder: ")
    assert body["channel"] == "email"
    assert "no documented public write API" in body["gap"]
    assert body["fields"]["title"] == "Enterprise platform - Northwind"


def test_the_preview_route_accepts_a_named_quote(http, room):
    rule = add_rule(http, room["id"], days=3)
    one = sent_quote(http, room["id"], title="First")
    sent_quote(http, room["id"], title="Second")
    body = http.get(
        f"{PREFIX}/rooms/{room['id']}/reminder-rules/{rule['id']}/preview?quote_id={one['id']}"
    ).json()
    assert body["fields"]["title"] == "First"


# --------------------------------------------------------------------------- #
# Step 3: the quote and its three controls
# --------------------------------------------------------------------------- #


def test_a_quote_is_tracked_with_its_stated_date(http, room):
    body = make_quote(http, room["id"])
    assert body[vocab.EXPIRATION_DATE] is not None
    assert body["expiration_source"] == vocab.EXPIRY_SOURCE_QUOTED
    assert body["state"] == vocab.QUOTE_DRAFT


def test_a_quote_with_no_date_and_no_default_never_expires(http, room):
    body = make_quote(http, room["id"], **{vocab.EXPIRATION_DATE: None})
    assert body[vocab.EXPIRATION_DATE] is None
    assert body["expiration_source"] == vocab.EXPIRY_SOURCE_NONE


def test_a_quote_inherits_the_account_default_when_no_date_is_stated(http, room):
    save_settings(http, room["id"], **{vocab.DEFAULT_EXPIRATION_DAYS: 30})
    body = make_quote(http, room["id"], **{vocab.EXPIRATION_DATE: None})
    assert body["expiration_source"] == vocab.EXPIRY_SOURCE_ACCOUNT_DEFAULT
    assert body["expiration_window_days"] == 30


def test_a_stated_date_wins_over_the_account_default(http, room):
    """ "properties set on the quote overriding the quote template's settings" """

    save_settings(http, room["id"], **{vocab.DEFAULT_EXPIRATION_DAYS: 30})
    body = make_quote(http, room["id"], **{vocab.EXPIRATION_DATE: in_days(7)})
    assert body["expiration_source"] == vocab.EXPIRY_SOURCE_QUOTED


def test_the_switch_off_beats_the_account_default(http, room):
    save_settings(http, room["id"], **{vocab.DEFAULT_EXPIRATION_DAYS: 30})
    body = make_quote(
        http, room["id"], **{vocab.EXPIRATION_DATE: None, vocab.EXPIRATION_ENABLED: False}
    )
    assert body["expiration_enabled"] is False
    assert body[vocab.EXPIRATION_DATE] is None


def test_a_past_effective_date_is_accepted_over_http(http, room):
    """ "because a past Effective date is allowed" """

    body = make_quote(
        http,
        room["id"],
        **{vocab.EXPIRATION_DATE: in_days(5), vocab.EFFECTIVE_DATE: days_ago(10)},
    )
    assert body["sign_by_deadline"] is True
    assert body[vocab.EXPIRATION_DATE] is not None


def test_a_future_effective_date_is_not_a_sign_by_deadline(http, room):
    body = make_quote(
        http,
        room["id"],
        **{vocab.EXPIRATION_DATE: in_days(30), vocab.EFFECTIVE_DATE: in_days(5)},
    )
    assert body["sign_by_deadline"] is False


def test_an_unreadable_date_is_refused_with_422(http, room):
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/quotes",
        json=quote_payload(**{vocab.EXPIRATION_DATE: "whenever"}),
    )
    assert response.status_code == 422
    assert response.json()["error"] == "expiration_not_an_instant"


def test_a_label_can_be_edited_without_clearing_the_date(http, room):
    quote = make_quote(http, room["id"])
    response = http.put(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/expiration",
        json={vocab.EXPIRATION_LABEL: "Sign by Friday"},
    )
    assert response.status_code == 200
    assert response.json()["expiration_label"] == "Sign by Friday"
    assert response.json()[vocab.EXPIRATION_DATE] == quote[vocab.EXPIRATION_DATE]


def test_turning_the_switch_off_over_http_writes_an_activity(http, room):
    quote = make_quote(http, room["id"])
    http.put(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/expiration",
        json={vocab.EXPIRATION_ENABLED: False},
    )
    body = http.get(f"{PREFIX}/rooms/{room['id']}/activities").json()
    assert vocab.ACTIVITY_EXPIRATION_OFF in {row["activity"] for row in body["activities"]}


def test_an_explicit_null_clears_the_date_over_http(http, room):
    quote = make_quote(http, room["id"])
    body = http.put(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/expiration",
        json={vocab.EXPIRATION_DATE: None},
    ).json()
    assert body[vocab.EXPIRATION_DATE] is None


def test_a_date_can_be_moved(http, room):
    quote = make_quote(http, room["id"])
    body = http.put(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/expiration",
        json={vocab.EXPIRATION_DATE: in_days(60)},
    ).json()
    assert body["expiration_date_only"] == rules.iso_date(rules.coerce_instant(in_days(60)))


def test_an_unknown_quote_is_404_on_every_quote_route(http, room):
    for path, method in [
        (f"{PREFIX}/rooms/{room['id']}/quotes/quote_absent", "get"),
        (f"{PREFIX}/rooms/{room['id']}/quotes/quote_absent/send", "post"),
        (f"{PREFIX}/rooms/{room['id']}/quotes/quote_absent/void", "post"),
        (f"{PREFIX}/rooms/{room['id']}/quotes/quote_absent/archive", "post"),
        (f"{PREFIX}/rooms/{room['id']}/quotes/quote_absent/acceptance", "post"),
        (f"{PREFIX}/rooms/{room['id']}/quotes/quote_absent/can-accept", "get"),
    ]:
        # A GET takes no body: `TestClient.get` takes no `json` keyword, and passing one
        # raises before the request is built rather than reaching the route.
        response = http.post(path, json={}) if method == "post" else http.get(path)
        assert response.status_code == 404, path
        assert response.json()["error"] == "unknown_quote", path


def test_the_quote_list_reports_the_room_it_read(http, room):
    make_quote(http, room["id"])
    body = http.get(f"{PREFIX}/rooms/{room['id']}/quotes").json()
    assert body["room_id"] == room["id"]
    assert body["count"] == 1


def test_the_quote_list_filters_by_state(http, room):
    make_quote(http, room["id"], title="Draft")
    sent_quote(http, room["id"], title="Sent")
    assert http.get(f"{PREFIX}/rooms/{room['id']}/quotes?state=sent").json()["count"] == 1
    assert http.get(f"{PREFIX}/rooms/{room['id']}/quotes?state=draft").json()["count"] == 1


def test_the_expiring_soon_filter_reports_its_window(http, room):
    sent_quote(http, room["id"], title="Soon", **{vocab.EXPIRATION_DATE: in_days(2)})
    sent_quote(http, room["id"], title="Later", **{vocab.EXPIRATION_DATE: in_days(90)})
    body = http.get(f"{PREFIX}/rooms/{room['id']}/quotes?expiring_soon=true").json()
    assert body["expiring_soon_days"] == vocab.EXPIRING_SOON_DAYS
    assert [row["title"] for row in body["quotes"]] == ["Soon"]


def test_reading_a_quote_with_a_timezone_does_not_move_the_instant(http, room):
    """A deadline that moved with a timezone is a deadline two people disagree about."""

    quote = sent_quote(http, room["id"])
    utc = http.get(f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}?tz=UTC").json()
    kolkata = http.get(f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}?tz=%2B05:30").json()
    assert utc["expiration_view"]["epoch_seconds"] == kolkata["expiration_view"]["epoch_seconds"]
    assert kolkata["expiration_view"]["offset_minutes"] == 330


def test_a_quote_carries_its_invariants_on_the_wire(http, room):
    body = make_quote(http, room["id"])
    assert "downloaded, cloned, voided or archived" in body["invariants"]["expiry_survives"]
    assert body["survivable_actions"] == ["download", "clone", "void", "archive"]


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #


def test_a_send_counts_one_and_consumes_one(http, room):
    body = sent_quote(http, room["id"])
    assert body[vocab.SEND_COUNT] == 1
    assert body[vocab.ESIGNATURE_QUOTA_CONSUMED] == 1
    assert body["state"] == vocab.QUOTE_SENT


def test_a_resend_counts_two_and_consumes_two(http, room):
    """ "resending counts as a new send (consuming e-signature quota again)" """

    quote = sent_quote(http, room["id"])
    again = http.post(f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/send", json={}).json()
    assert again[vocab.SEND_COUNT] == 2
    assert again[vocab.ESIGNATURE_QUOTA_CONSUMED] == 2
    assert again["send"]["resent"] is True


def test_a_send_does_not_publish_unless_asked(http, room):
    body = sent_quote(http, room["id"])
    assert body[vocab.PUBLISHED_AT] is None
    assert body[vocab.PUBLISHED] is False


def test_a_publish_writes_the_publish_instant_and_the_activity(http, room):
    quote = sent_quote(http, room["id"])
    body = http.post(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/send", json={"publish": True}
    ).json()
    assert body[vocab.PUBLISHED_AT] is not None
    assert body[vocab.PUBLISHED] is True
    activities = http.get(f"{PREFIX}/rooms/{room['id']}/activities").json()["activities"]
    assert vocab.ACTIVITY_PUBLISHED in {row["activity"] for row in activities}


def test_the_send_writes_the_sent_activity(http, room):
    sent_quote(http, room["id"])
    activities = http.get(f"{PREFIX}/rooms/{room['id']}/activities").json()["activities"]
    assert vocab.ACTIVITY_SENT in {row["activity"] for row in activities}


# --------------------------------------------------------------------------- #
# Acceptance
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("method", vocab.ACCEPTANCE_METHODS)
def test_each_acceptance_method_is_recorded_verbatim_over_http(http, room, method):
    quote = sent_quote(http, room["id"])
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/acceptance",
        json={"action": vocab.ACTION_ACCEPTED, "method": method},
    )
    assert response.status_code == 201
    assert response.json()["acceptance"]["method"] == method


def test_an_acceptance_survives_the_deadline_and_the_response_says_so(http, room):
    quote = sent_quote(http, room["id"], **{vocab.EXPIRATION_DATE: in_days(5)})
    body = http.post(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/acceptance",
        json={"action": vocab.ACTION_ACCEPTED},
    ).json()
    assert body["acceptance"]["by_deadline"] is True
    assert body["survives_expiry"] is True
    assert body["state"] == vocab.QUOTE_ACCEPTED


def test_a_signed_quote_reports_signed(http, room):
    quote = sent_quote(http, room["id"])
    body = http.post(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/acceptance",
        json={"action": vocab.ACTION_E_SIGNED, "method": vocab.METHOD_E_SIGNATURE},
    ).json()
    assert body["state"] == vocab.QUOTE_SIGNED


def test_a_marked_signed_quote_reports_signed(http, room):
    quote = sent_quote(http, room["id"])
    body = http.post(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/acceptance",
        json={"action": vocab.ACTION_MARKED_SIGNED},
    ).json()
    assert body["state"] == vocab.QUOTE_SIGNED


def test_an_unknown_action_is_refused_with_422(http, room):
    quote = sent_quote(http, room["id"])
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/acceptance",
        json={"action": "shrugged"},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "unknown_buyer_action"


def test_an_unknown_method_is_refused_with_422(http, room):
    quote = sent_quote(http, room["id"])
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/acceptance",
        json={"action": vocab.ACTION_ACCEPTED, "method": "telepathy"},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "unknown_acceptance_method"


def test_an_expired_quote_refuses_an_acceptance_with_409(http, room):
    """ "the buyer loses the ability to accept" """

    quote = sent_quote(http, room["id"], **{vocab.EXPIRATION_DATE: days_ago(1)})
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/acceptance",
        json={"action": vocab.ACTION_ACCEPTED},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "quote_acceptance_closed"


def test_an_archived_quote_refuses_an_acceptance_with_409(http, room):
    quote = sent_quote(http, room["id"])
    http.post(f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/archive", json={})
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/acceptance",
        json={"action": vocab.ACTION_ACCEPTED},
    )
    assert response.status_code == 409


def test_a_voided_quote_refuses_an_acceptance_with_409(http, room):
    quote = sent_quote(http, room["id"])
    http.post(f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/void", json={})
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/acceptance",
        json={"action": vocab.ACTION_ACCEPTED},
    )
    assert response.status_code == 409


def test_the_can_accept_report_answers_for_an_open_quote(http, room):
    quote = sent_quote(http, room["id"])
    body = http.get(f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/can-accept").json()
    assert body["can_accept"] is True
    assert body["reason"] == "open"
    assert "lost the ability to accept" in body["note"]


def test_the_can_accept_report_refuses_an_expired_quote_and_names_why(http, room):
    quote = sent_quote(http, room["id"], **{vocab.EXPIRATION_DATE: days_ago(1)})
    body = http.get(f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/can-accept").json()
    assert body["can_accept"] is False
    assert body["reason"] == "quote_expired"


def test_the_can_accept_report_refuses_an_archived_quote_for_its_own_reason(http, room):
    quote = sent_quote(http, room["id"])
    http.post(f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/archive", json={})
    body = http.get(f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/can-accept").json()
    assert body["reason"] == "quote_archived"


def test_the_can_accept_report_refuses_a_voided_quote_for_its_own_reason(http, room):
    quote = sent_quote(http, room["id"])
    http.post(f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/void", json={})
    body = http.get(f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/can-accept").json()
    assert body["reason"] == "quote_voided"


def test_an_accepted_quote_reports_that_the_buyer_already_acted(http, room):
    quote = sent_quote(http, room["id"])
    http.post(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/acceptance",
        json={"action": vocab.ACTION_ACCEPTED},
    )
    body = http.get(f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/can-accept").json()
    assert body["reason"] == "already_acted"


# --------------------------------------------------------------------------- #
# Void and archive
# --------------------------------------------------------------------------- #


def test_void_deactivates_the_link_and_nothing_else(http, room):
    quote = sent_quote(http, room["id"], **{vocab.EXPIRATION_DATE: in_days(20)})
    body = http.post(f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/void", json={}).json()
    assert body[vocab.LINK_ACTIVE] is False
    assert body[vocab.HIDDEN_FROM_INDEX] is False
    assert body[vocab.BUYER_ACCESS] is True
    assert body["state"] == vocab.QUOTE_VOIDED


def test_archive_unpublishes_hides_and_blocks(http, room):
    quote = sent_quote(http, room["id"], **{vocab.EXPIRATION_DATE: in_days(20)})
    body = http.post(f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/archive", json={}).json()
    assert body[vocab.PUBLISHED] is False
    assert body[vocab.HIDDEN_FROM_INDEX] is True
    assert body[vocab.BUYER_ACCESS] is False
    assert body["state"] == vocab.QUOTE_ARCHIVED


def test_void_and_archive_are_distinguishable_on_the_wire(http, room):
    """Three researched outcomes, so a client can tell which one it is looking at."""

    voided = sent_quote(http, room["id"], title="Voided")
    archived = sent_quote(http, room["id"], title="Archived")
    http.post(f"{PREFIX}/rooms/{room['id']}/quotes/{voided['id']}/void", json={})
    http.post(f"{PREFIX}/rooms/{room['id']}/quotes/{archived['id']}/archive", json={})
    rows = {
        row["title"]: row
        for row in http.get(f"{PREFIX}/rooms/{room['id']}/quotes").json()["quotes"]
    }
    assert rows["Voided"][vocab.LINK_ACTIVE] is False
    assert rows["Voided"][vocab.BUYER_ACCESS] is True
    assert rows["Archived"][vocab.BUYER_ACCESS] is False
    assert rows["Archived"][vocab.HIDDEN_FROM_INDEX] is True


def test_an_expired_quote_can_still_be_voided_and_archived_over_http(http, room):
    quote = sent_quote(http, room["id"], **{vocab.EXPIRATION_DATE: days_ago(1)})
    assert quote["state"] == vocab.QUOTE_EXPIRED
    voided = http.post(f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/void", json={})
    assert voided.status_code == 201
    other = sent_quote(http, room["id"], **{vocab.EXPIRATION_DATE: days_ago(1)})
    archived = http.post(f"{PREFIX}/rooms/{room['id']}/quotes/{other['id']}/archive", json={})
    assert archived.status_code == 201


def test_an_expired_quote_refuses_to_have_its_date_moved_with_409(http, room):
    quote = sent_quote(http, room["id"], **{vocab.EXPIRATION_DATE: days_ago(1)})
    response = http.put(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/expiration",
        json={vocab.EXPIRATION_DATE: in_days(60)},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "quote_not_editable"
    assert "new send" in response.json()["remediation"]


def test_an_archived_quote_refuses_to_have_its_date_moved_with_409(http, room):
    quote = sent_quote(http, room["id"])
    http.post(f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/archive", json={})
    response = http.put(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/expiration",
        json={vocab.EXPIRATION_DATE: in_days(60)},
    )
    assert response.status_code == 409


# --------------------------------------------------------------------------- #
# The dispatch and the expiry check
# --------------------------------------------------------------------------- #


def test_a_dispatch_over_http_reports_nothing_due_rather_than_pretending(http, room):
    """Nothing expires until somebody calls the route, and the page shows the gap."""

    save_settings(http, room["id"])
    add_rule(http, room["id"], days=30)
    sent_quote(http, room["id"])
    body = http.post(f"{PREFIX}/rooms/{room['id']}/reminders", json={}).json()
    assert body["sent"] == 0
    assert body["skipped"] == 1
    assert body["decisions"][0]["reason"] == "not_yet_due"


def test_a_dispatch_over_http_names_its_recipients_and_claims_no_delivery(http, room):
    save_settings(http, room["id"])
    add_rule(http, room["id"], days=3)
    sent_quote(http, room["id"])
    body = http.post(f"{PREFIX}/rooms/{room['id']}/reminders", json={}).json()
    row = body["decisions"][0]
    assert row["recipients"] == ["buyer@northwind.example"]
    assert row["delivered"] is False
    assert body["delivery"]["sent_by_this_product"] is False


def test_the_ledger_over_http_reports_the_delivery_gap(http, room):
    save_settings(http, room["id"])
    add_rule(http, room["id"], days=3)
    sent_quote(http, room["id"])
    http.post(f"{PREFIX}/rooms/{room['id']}/reminders", json={})
    body = http.get(f"{PREFIX}/rooms/{room['id']}/reminders").json()
    assert body["count"] == 1
    assert body["skipped"] == 1
    assert body["delivery"]["sent_by_this_product"] is False


def test_the_ledger_can_be_filtered_by_quote_over_http(http, room):
    save_settings(http, room["id"])
    add_rule(http, room["id"], days=3)
    one = sent_quote(http, room["id"], title="One")
    sent_quote(http, room["id"], title="Two")
    http.post(f"{PREFIX}/rooms/{room['id']}/reminders", json={})
    body = http.get(f"{PREFIX}/rooms/{room['id']}/reminders?quote_id={one['id']}").json()
    assert body["count"] == 1


def test_a_dispatch_can_be_narrowed_to_one_quote_over_http(http, room):
    save_settings(http, room["id"])
    add_rule(http, room["id"], days=3)
    one = sent_quote(http, room["id"], title="One")
    sent_quote(http, room["id"], title="Two")
    body = http.post(f"{PREFIX}/rooms/{room['id']}/reminders", json={"quote_id": one["id"]}).json()
    assert body["quotes_evaluated"] == 1


def test_a_dispatch_reports_the_settings_it_used(http, room):
    save_settings(
        http, room["id"], **{vocab.REMINDER_SEND_TIME: "07:30", vocab.ACCOUNT_TIMEZONE: "+05:30"}
    )
    body = http.post(f"{PREFIX}/rooms/{room['id']}/reminders", json={}).json()
    assert body["settings"]["reminder_send_time"] == "07:30"
    assert body["settings"]["account_timezone"] == "+05:30"
    assert "no documented public write API" in body["gap"]


def test_a_switched_off_quote_is_skipped_by_the_dispatch_over_http(http, room):
    save_settings(http, room["id"])
    add_rule(http, room["id"], days=3)
    sent_quote(http, room["id"], **{vocab.EXPIRATION_ENABLED: False})
    body = http.post(f"{PREFIX}/rooms/{room['id']}/reminders", json={}).json()
    assert body["decisions"][0]["reason"] == vocab.SKIP_EXPIRATION_OFF


def test_an_accepted_quote_is_skipped_by_the_dispatch_over_http(http, room):
    save_settings(http, room["id"])
    add_rule(http, room["id"], days=3)
    quote = sent_quote(http, room["id"])
    http.post(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/acceptance",
        json={"action": vocab.ACTION_ACCEPTED},
    )
    body = http.post(f"{PREFIX}/rooms/{room['id']}/reminders", json={}).json()
    assert body["decisions"][0]["reason"] == vocab.SKIP_QUOTE_ACCEPTED


def test_the_expiry_check_answers_with_nothing_due_and_says_it_deleted_nothing(http, room):
    save_settings(http, room["id"])
    sent_quote(http, room["id"], **{vocab.EXPIRATION_DATE: in_days(10)})
    body = http.post(f"{PREFIX}/rooms/{room['id']}/expiry-check", json={}).json()
    assert body["expired_count"] == 0
    assert body["skipped_count"] == 1
    assert body["quotes_deleted"] == 0
    assert body["skipped"][0]["reason"] == "deadline_not_passed"


def test_the_expiry_check_leaves_a_switched_off_quote_and_says_which_switch(http, room):
    save_settings(http, room["id"])
    sent_quote(http, room["id"], **{vocab.EXPIRATION_ENABLED: False})
    body = http.post(f"{PREFIX}/rooms/{room['id']}/expiry-check", json={}).json()
    assert body["skipped"][0]["reason"] == vocab.SKIP_EXPIRATION_OFF


def test_the_expiry_check_expires_an_overdue_quote_over_http(http, room):
    """The one case a caller can reach over HTTP without a clock of its own."""

    save_settings(http, room["id"])
    sent_quote(http, room["id"], **{vocab.EXPIRATION_DATE: days_ago(1)})
    body = http.post(f"{PREFIX}/rooms/{room['id']}/expiry-check", json={}).json()
    assert body["expired_count"] == 1
    assert body["expired"][0]["activity"] == "Quote expired"
    assert body["expired"][0]["state"] == vocab.QUOTE_EXPIRED


def test_the_expiry_check_writes_the_activity_name_the_research_quotes(http, room):
    save_settings(http, room["id"])
    sent_quote(http, room["id"], **{vocab.EXPIRATION_DATE: days_ago(1)})
    http.post(f"{PREFIX}/rooms/{room['id']}/expiry-check", json={})
    activities = http.get(
        f"{PREFIX}/rooms/{room['id']}/activities?activity={vocab.ACTIVITY_EXPIRED}"
    ).json()
    assert activities["count"] == 1
    assert activities["activities"][0]["activity"] == "Quote expired"


def test_the_expiry_check_twice_writes_one_activity(http, room):
    """A second pass must not tell a buyer twice."""

    save_settings(http, room["id"])
    sent_quote(http, room["id"], **{vocab.EXPIRATION_DATE: days_ago(1)})
    http.post(f"{PREFIX}/rooms/{room['id']}/expiry-check", json={})
    second = http.post(f"{PREFIX}/rooms/{room['id']}/expiry-check", json={}).json()
    assert second["expired_count"] == 0
    activities = http.get(
        f"{PREFIX}/rooms/{room['id']}/activities?activity={vocab.ACTIVITY_EXPIRED}"
    ).json()
    assert activities["count"] == 1


def test_the_expiry_check_leaves_an_accepted_quote_alone(http, room):
    save_settings(http, room["id"])
    quote = sent_quote(http, room["id"], **{vocab.EXPIRATION_DATE: in_days(5)})
    http.post(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/acceptance",
        json={"action": vocab.ACTION_ACCEPTED},
    )
    body = http.post(f"{PREFIX}/rooms/{room['id']}/expiry-check", json={}).json()
    assert body["expired_count"] == 0
    assert body["skipped"][0]["reason"] == "deadline_not_passed"


def test_the_activities_route_reports_the_vendors_own_names(http, room):
    save_settings(http, room["id"])
    sent_quote(http, room["id"], **{vocab.EXPIRATION_DATE: days_ago(1)})
    http.post(f"{PREFIX}/rooms/{room['id']}/expiry-check", json={})
    body = http.get(f"{PREFIX}/rooms/{room['id']}/activities").json()
    assert vocab.QUOTE_EXPIRED_ACTIVITY in body["types"]
    assert vocab.ACTIVITY_EXPIRED in {row["activity"] for row in body["activities"]}


def test_the_activities_route_can_be_filtered_by_quote(http, room):
    one = sent_quote(http, room["id"], title="One")
    sent_quote(http, room["id"], title="Two")
    body = http.get(f"{PREFIX}/rooms/{room['id']}/activities?quote_id={one['id']}").json()
    assert body["count"] >= 1
    assert all(row["quote_id"] == one["id"] for row in body["activities"])


# --------------------------------------------------------------------------- #
# The summary
# --------------------------------------------------------------------------- #


def test_the_summary_counts_read_back_from_the_store_over_http(http, room):
    save_settings(http, room["id"])
    make_quote(http, room["id"], title="Draft")
    sent_quote(http, room["id"], title="Sent")
    voided = sent_quote(http, room["id"], title="Voided")
    http.post(f"{PREFIX}/rooms/{room['id']}/quotes/{voided['id']}/void", json={})
    body = http.get(f"{PREFIX}/rooms/{room['id']}/summary").json()
    assert body["quotes"] == 3
    assert body["draft"] == 1
    assert body["voided"] == 1
    assert body["room_id"] == room["id"]


def test_the_summary_reports_the_settings_it_used_over_http(http, room):
    save_settings(
        http, room["id"], **{vocab.DEFAULT_EXPIRATION_DAYS: 45, vocab.ACCOUNT_TIMEZONE: "-05:00"}
    )
    body = http.get(f"{PREFIX}/rooms/{room['id']}/summary").json()
    assert body["default_expiration_days"] == 45
    assert body["account_timezone"] == "-05:00"


def test_the_summary_carries_the_invariants_over_http(http, room):
    body = http.get(f"{PREFIX}/rooms/{room['id']}/summary").json()
    assert body["invariants"]["survival"] == vocab.SURVIVAL_QUOTE
    assert "downloaded, cloned, voided or archived" in body["invariants"]["expiry_survives"]


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def test_every_source_this_feature_records_names_a_mounted_route(http, room):
    """The defect this check exists for: an audit row naming a route the app stopped serving.

    Every write endpoint is driven, then the audit log is read and each recorded source is
    matched against the routes the running app actually mounted.
    """

    store = client_store(http)
    save_settings(http, room["id"])
    rule = add_rule(http, room["id"], days=3)
    quote = sent_quote(http, room["id"], **{vocab.EXPIRATION_DATE: days_ago(1)})
    http.put(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/expiration",
        json={vocab.EXPIRATION_LABEL: "Sign by"},
    )
    http.patch(f"{PREFIX}/rooms/{room['id']}/reminder-rules/{rule['id']}", json={"days": 4})
    http.post(
        f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/acceptance",
        json={
            "action": vocab.ACTION_ACCEPTED,
            "at": days_ago(2),
        },
    )
    http.post(f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/void", json={})
    http.post(f"{PREFIX}/rooms/{room['id']}/quotes/{quote['id']}/archive", json={})
    http.post(f"{PREFIX}/rooms/{room['id']}/reminders", json={})
    http.post(f"{PREFIX}/rooms/{room['id']}/expiry-check", json={})
    http.delete(f"{PREFIX}/rooms/{room['id']}/reminder-rules/{rule['id']}")

    routes = all_served_routes(http)
    ours = [row for row in store.audit(limit=500) if PREFIX in str(row.get("source") or "")]
    assert ours, "no audit row named this feature's prefix"

    offenders = [
        row["source"]
        for row in ours
        if not source_names_a_mounted_route(str(row["source"]), routes)
    ]
    assert not offenders, offenders


def test_the_seeder_runs_this_feature_and_its_string_is_cp1252_encodable(tmp_path):
    """A single RIGHTWARDS ARROW in a feature's seed string broke the whole seeder once."""

    import importlib

    from dsr.db.audited import AuditedDatabase

    module = importlib.import_module(f"dsr.features.{MODULE}")
    path = tmp_path / "seed.db"
    db = AuditedDatabase(path, mirror_dir=tmp_path / "audit", actor="test")
    try:
        store = RecordStore(db)
        room = store.create("room", {"name": "Northwind", "account": "Northwind"}, actor="test")
        summary = module.seed(
            db, {"room_ids": [(room["id"], "Northwind")], "now": datetime.now(timezone.utc)}
        )
    finally:
        db.close()

    assert isinstance(summary, str) and summary.strip()
    # The assertion that broke the seeder: encoding it the way a Windows console does.
    summary.encode("cp1252")
    assert "expired" in summary
    assert "sign-by deadline" in summary


def test_the_seeder_reports_states_that_are_not_all_successes(tmp_path):
    """A demo of only green teaches a reviewer nothing."""

    import importlib

    from dsr.db.audited import AuditedDatabase

    module = importlib.import_module(f"dsr.features.{MODULE}")
    path = tmp_path / "seed.db"
    db = AuditedDatabase(path, mirror_dir=tmp_path / "audit", actor="test")
    try:
        store = RecordStore(db)
        room = store.create("room", {"name": "Northwind", "account": "Northwind"}, actor="test")
        summary = module.seed(
            db, {"room_ids": [(room["id"], "Northwind")], "now": datetime.now(timezone.utc)}
        )
    finally:
        db.close()
    lowered = summary.lower()
    assert "expired" in lowered
    assert "switch off" in lowered
    assert "survival rule" in lowered


def test_every_demo_audit_row_names_a_mounted_route(tmp_path, http, room):
    """The seeder writes audit rows too, and they have to name real routes."""

    import importlib

    from dsr.db.audited import AuditedDatabase

    module = importlib.import_module(f"dsr.features.{MODULE}")
    path = tmp_path / "seed-audit.db"
    db = AuditedDatabase(path, mirror_dir=tmp_path / "audit", actor="test")
    try:
        store = RecordStore(db)
        seeded_room = store.create("room", {"name": "Contoso", "account": "Contoso"}, actor="test")
        module.seed(
            db, {"room_ids": [(seeded_room["id"], "Contoso")], "now": datetime.now(timezone.utc)}
        )
        rows = store.audit(limit=500)
    finally:
        db.close()

    routes = mounted_routes(http)
    ours = [row for row in rows if PREFIX in str(row.get("source") or "")]
    assert ours, "the seed wrote no audit row naming this feature's prefix"
    offenders = [
        row["source"]
        for row in ours
        if not source_names_a_mounted_route(str(row["source"]), routes)
    ]
    assert not offenders, offenders


def test_the_seeder_creates_the_demo_room_when_the_seeder_supplies_none(tmp_path):
    """A room-less context still produces a reviewable demo."""

    import importlib

    from dsr.db.audited import AuditedDatabase

    module = importlib.import_module(f"dsr.features.{MODULE}")
    path = tmp_path / "seed-bare.db"
    db = AuditedDatabase(path, mirror_dir=tmp_path / "audit", actor="test")
    try:
        summary = module.seed(db, {"room_ids": [], "now": datetime.now(timezone.utc)})
        rooms = db.list("room", limit=10)
    finally:
        db.close()
    summary.encode("cp1252")
    assert any("quote expiry" in str(row.get("data", {}).get("name", "")) for row in rooms)
