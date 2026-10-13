"""HTTP tests for WF-097: quote activity, the counters, and the follow-ups.

These drive the mounted app through ``TestClient`` rather than calling the
service, so they exercise the same path a real client does. If the feature
failed to register, or collided with another feature's route, every test here
would 404, which is the point: the router is mounted by discovery alone and
``backend/dsr/api.py`` is not edited.

Each test names the rule it protects, quoting
``docs/research/digital-sales-room-workflows/wf/WF-097.md``.

The database is a temporary file per test, set through ``DSR_DB_PATH``, which
:mod:`dsr.deps` reads at call time. Nothing here depends on a row another test
wrote, so the file passes alone and under pytest-xdist in any order.

No test asserts an age measured against the wall clock. Where a figure depends on
now, the test freezes what it can and asserts the field is present and typed
rather than its exact value, because an assertion on "3 days ago" is an assertion
that fails on a different day.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.api import app
from fastapi.testclient import TestClient

PREFIX = "/api/wf-097"


@pytest.fixture()
def client(monkeypatch):
    """A client over the mounted app, on its own temporary database."""
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf097_http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


def now_iso(offset_days: float = 0.0):
    """An ISO timestamp relative to the wall clock, for a dated quote."""
    moment = datetime.now(timezone.utc) + timedelta(days=offset_days)
    return moment.isoformat(timespec="seconds")


def make_quote(client, **overrides):
    """A published quote in room_1, created through the generic records endpoint."""
    payload = {
        "title": "Northwind renewal",
        "status": "PUBLISHED",
        "quote_number": "Q-2026-021",
        "owner": "dana",
        "hs_quote_amount": 28500.0,
        "expires_at": now_iso(21),
    }
    payload.update(overrides)
    response = client.post("/api/records/quote", json=payload, params={"room_id": "room_1"})
    assert response.status_code == 201, response.text
    return response.json()


def configure(client, **settings):
    """Write the analysis settings through this feature's own route."""
    response = client.put(f"{PREFIX}/settings", json=settings, params={"room_id": "room_1"})
    assert response.status_code == 200, response.text
    return response.json()


def record(client, quote_id, event, **body):
    return client.post(f"{PREFIX}/quotes/{quote_id}/activity", json={"event": event, **body})


# --------------------------------------------------------------------------- #
# Discovery and vocabulary
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery(client):
    """The feature registers by adding a file. api.py is not edited.

    A 404 here would mean the host did not mount this feature, which is the one
    failure mode a plugin host has that an edit to a shared file cannot have.
    """
    response = client.get(f"{PREFIX}/summary")
    assert response.status_code == 200
    assert "quotes" in response.json()


def test_the_feature_appears_in_the_registry(client):
    """The feature is listed as loaded, not as failed."""
    response = client.get("/api/features")
    assert response.status_code == 200
    body = response.json()
    ids = [f.get("id") for f in body.get("features", [])]
    assert "wf-097-track-buyer-engagement-on-a-shared-quote" in ids
    assert body["failed_count"] == 0


def test_the_prefix_is_this_workflows_own(client):
    """The prefix is the ticket, lower-cased, and it is unique."""
    body = client.get("/api/features").json()
    mine = [
        f for f in body["features"] if f["id"] == "wf-097-track-buyer-engagement-on-a-shared-quote"
    ]
    assert len(mine) == 1
    assert mine[0]["prefix"] == PREFIX
    prefixes = [f["prefix"] for f in body["features"] if f["prefix"]]
    assert prefixes.count(PREFIX) == 1


def test_the_vocabulary_serves_the_labels_the_rules_accept(client):
    """The panel renders the vocabulary from here so it cannot offer a wrong label."""
    payload = client.get(f"{PREFIX}/vocabulary").json()

    assert payload["events"]["buyer"] == [
        "Quote sent",
        "Quote viewed",
        "Quote downloaded",
        "Quote printed",
        "Quote buyer signed",
        "Quote manually signed",
        "Quote accepted",
    ]
    assert "Chat started" in payload["events"]["seller"]
    assert payload["view_surfaces"]["email_pdf_attachment"].startswith("Not a view")
    assert list(payload["counter_labels"].values())[0] == "Last Activity Date"
    assert payload["limits"]["threshold_default"] is None


def test_the_vocabulary_states_what_this_workflow_does_not_build(client):
    """Analytics suites, reports, exports and mail are named as out of scope.

    A page that silently omits them reads as a page that is merely shorter. A
    page that names them reads as a decision.
    """
    payload = client.get(f"{PREFIX}/vocabulary").json()
    assert "Revenue analytics suite reports" in payload["not_built"]
    assert "Email transmission" in payload["not_built"]


# --------------------------------------------------------------------------- #
# Settings over HTTP
# --------------------------------------------------------------------------- #


def test_a_fresh_workspace_reports_every_setting_as_unconfigured(client):
    """No default ships, so the page says what the workspace has not set."""
    payload = client.get(f"{PREFIX}/settings", params={"room_id": "room_1"}).json()

    assert payload["high_activity_threshold"] is None
    assert payload["no_activity_after_days"] is None
    assert payload["expiring_within_days"] is None
    assert payload["email_channel_connected"] is False
    assert "high_activity_threshold" in payload["unconfigured_keys"]


def test_writing_the_threshold_returns_it_and_leaves_the_others_unset(client):
    """A patch merges, so an admin setting the threshold loses nothing."""
    configure(client, high_activity_threshold=10)

    payload = client.get(f"{PREFIX}/settings", params={"room_id": "room_1"}).json()
    assert payload["high_activity_threshold"] == 10
    assert payload["no_activity_after_days"] is None
    assert payload["configured_keys"] == ["high_activity_threshold"]


@pytest.mark.parametrize(
    "body,fragment",
    [
        ({"high_activity_threshold": 0}, "between 1 and 1000"),
        ({"high_activity_threshold": 5000}, "between 1 and 1000"),
        ({"no_activity_after_days": 0}, "between 1 and 3650"),
        ({"email_channel_connected": "yes"}, "must be true or false"),
        ({"last_communication_at": "the other day"}, "ISO timestamp"),
        ({"high_activity_threshold": None}, "cannot be set to nothing"),
        ({"treshold": 5}, "unknown analysis setting"),
    ],
)
def test_a_refused_setting_is_a_422_naming_what_was_wrong(client, body, fragment):
    """A refused value is a 422 with the reason in it, not a silent no-op."""
    response = client.put(f"{PREFIX}/settings", json=body, params={"room_id": "room_1"})

    assert response.status_code == 422
    assert fragment in response.json()["detail"]


def test_a_refused_setting_writes_nothing(client):
    """Validation runs before the write, so the stored row is untouched."""
    configure(client, high_activity_threshold=10)

    client.put(
        f"{PREFIX}/settings",
        json={"high_activity_threshold": 0, "expiring_within_days": 7},
        params={"room_id": "room_1"},
    )

    payload = client.get(f"{PREFIX}/settings", params={"room_id": "room_1"}).json()
    assert payload["high_activity_threshold"] == 10
    assert payload["expiring_within_days"] is None


# --------------------------------------------------------------------------- #
# Recording activity
# --------------------------------------------------------------------------- #


def test_recording_a_view_returns_the_counters_it_produced(client):
    """Researched: "counters (``Last Activity Date``, ``Activity Count``, ... ``View Count``)
    are maintained"."""
    configure(client, high_activity_threshold=10)
    quote = make_quote(client)

    response = record(client, quote["id"], "Quote viewed", surface="quote_link")
    assert response.status_code == 201, response.text
    counters = response.json()["engagement"]

    assert counters["activity_count"] == 1
    assert counters["view_count"] == 1
    assert counters["last_activity_at"] is not None
    assert counters["activity_class"] == "Low activity"


def test_recording_the_pdf_open_leaves_the_view_count_at_zero(client):
    """Researched: "If a buyer opens the PDF attached to the email, this doesn't count as a
    quote view."

    The event is stored and the timeline shows it. Only the counter differs, and
    the counter is the whole reason the surface is a parameter.
    """
    configure(client, high_activity_threshold=10)
    quote = make_quote(client)

    response = record(client, quote["id"], "Quote viewed", surface="email_pdf_attachment")
    counters = response.json()["engagement"]
    assert counters["activity_count"] == 1
    assert counters["view_count"] == 0
    assert counters["pdf_opens_not_counted_as_views"] == 1

    timeline = client.get(f"{PREFIX}/quotes/{quote['id']}/activity").json()
    assert timeline["count"] == 1
    assert timeline["entries"][0]["event"] == "Quote viewed"
    assert timeline["entries"][0]["counted_as_view"] is False


def test_a_slug_spelling_of_a_researched_label_is_accepted(client):
    """A client will send ``quote_viewed`` and means the researched event."""
    quote = make_quote(client)
    assert record(client, quote["id"], "quote_viewed").status_code == 201


def test_an_event_outside_the_vocabulary_is_a_422(client):
    """An activity log that accepts any string stops being the researched list."""
    quote = make_quote(client)
    response = record(client, quote["id"], "Quote telepathed")
    assert response.status_code == 422
    assert "not a quote activity event" in response.json()["detail"]


def test_an_unknown_surface_is_a_422(client):
    """An unrecognised surface must not slip past the view rule."""
    quote = make_quote(client)
    response = record(client, quote["id"], "Quote viewed", surface="carrier_pigeon")
    assert response.status_code == 422
    assert "not a known activity surface" in response.json()["detail"]


def test_recording_against_a_quote_that_does_not_exist_is_a_422(client):
    """The engine refuses rather than writing an event against nothing."""
    response = record(client, "quote_absent", "Quote viewed")
    assert response.status_code == 422
    assert "not found" in response.json()["detail"]


def test_an_event_in_the_future_is_a_422(client):
    """A future event would make "days since last activity" negative."""
    quote = make_quote(client)
    response = record(client, quote["id"], "Quote viewed", occurred_at=now_iso(3))
    assert response.status_code == 422
    assert "in the future" in response.json()["detail"]


def test_the_chat_started_event_is_recorded_as_a_buyer_action(client):
    """The research lists it in the seller list and defines it as a buyer action."""
    quote = make_quote(client)
    response = record(client, quote["id"], "Chat started")
    assert response.json()["event"]["data"]["side"] == "buyer"


def test_the_counters_are_written_back_onto_the_quote_record(client):
    """The researched columns are properties of the quote object."""
    configure(client, high_activity_threshold=10)
    quote = make_quote(client)
    record(client, quote["id"], "Quote viewed")

    stored = client.get(f"/api/records/quote/{quote['id']}").json()
    assert stored["data"]["engagement"]["activity_count"] == 1
    assert stored["data"]["engagement"]["view_count"] == 1


def test_every_audit_row_names_a_route_the_app_actually_serves(client):
    """The product guarantee: the audit row names a URL the app serves.

    Read back from the live audit log and matched against the mounted app's own
    route table, with a path parameter matched by position. The table comes from
    two places because the app exposes it in two places: ``app.routes`` carries
    the core routes, and the registry carries the feature routes, because this
    FastAPI version keeps an included router as one entry rather than flattening
    its routes into ``app.routes``. A list written out here would only prove the
    test agrees with itself.
    """
    import re

    configure(client, high_activity_threshold=10)
    quote = make_quote(client)
    record(client, quote["id"], "Quote viewed")
    client.post(f"{PREFIX}/follow-ups/{quote['id']}/send", json={})

    core = [getattr(route, "path", "") for route in app.routes]
    features = [
        route["path"]
        for feature in client.get("/api/features").json()["features"]
        for route in feature["routes"]
    ]
    patterns = ["^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$" for path in core + features if path]
    entries = client.get("/api/audit", params={"limit": 200}).json()["entries"]
    written = [
        e for e in entries if str(e.get("source", "")).startswith(("POST /api/", "PUT /api/"))
    ]

    assert written
    for entry in written:
        path = str(entry["source"]).split(" ", 1)[1]
        assert any(re.match(pattern, path) for pattern in patterns), (
            f"audit row names {path}, which the app does not serve"
        )


def test_the_rows_this_feature_wrote_name_its_own_routes(client):
    """The narrower guarantee, read from this feature's own prefix.

    Every row whose source names ``/api/wf-097`` is matched against the routes
    the registry advertises for this feature alone, which is the check that fails
    first when a route is renamed and a hardcoded source string is not renamed
    with it.
    """
    import re

    configure(client, high_activity_threshold=10)
    quote = make_quote(client)
    record(client, quote["id"], "Quote viewed")
    client.post(f"{PREFIX}/follow-ups/{quote['id']}/send", json={})

    body = client.get("/api/features").json()
    mine = [
        f for f in body["features"] if f["id"] == "wf-097-track-buyer-engagement-on-a-shared-quote"
    ][0]
    patterns = [
        "^" + re.sub(r"\{[^}]+\}", "[^/]+", route["path"]) + "$" for route in mine["routes"]
    ]

    entries = client.get("/api/audit", params={"limit": 200}).json()["entries"]
    written = [e for e in entries if "/api/wf-097" in str(e.get("source", ""))]

    assert written
    for entry in written:
        path = str(entry["source"]).split(" ", 1)[1]
        assert any(re.match(pattern, path) for pattern in patterns), (
            f"this feature wrote an audit row naming {path}, which it does not serve"
        )


# --------------------------------------------------------------------------- #
# Reading a quote
# --------------------------------------------------------------------------- #


def test_the_engagement_route_returns_the_four_counters_and_why_one_is_absent(client):
    """The third counter names the reason it cannot be computed."""
    configure(client, high_activity_threshold=10)
    quote = make_quote(client)
    record(client, quote["id"], "Quote viewed")

    payload = client.get(f"{PREFIX}/quotes/{quote['id']}/engagement").json()

    assert payload["engagement"]["activity_count"] == 1
    assert payload["engagement"]["view_count"] == 1
    assert payload["engagement"]["activity_count_since_last_communication"] is None
    assert payload["engagement"]["since_last_communication_basis"] == "no_email_channel_connected"


def test_the_timeline_is_newest_first_and_carries_the_side(client):
    """Both the sidebar card and the full timeline read this one ordered list."""
    configure(client, high_activity_threshold=10)
    quote = make_quote(client)
    record(client, quote["id"], "Quote published", occurred_at=now_iso(-4))
    record(client, quote["id"], "Quote viewed", occurred_at=now_iso(-1))

    payload = client.get(f"{PREFIX}/quotes/{quote['id']}/activity").json()

    assert [row["event"] for row in payload["entries"]] == ["Quote viewed", "Quote published"]
    assert [row["side"] for row in payload["entries"]] == ["buyer", "seller"]


def test_the_quote_index_filters_by_status_owner_and_signing_status(client):
    """Researched: "filter by **Status**, ... **Owner**, **Signing status**"."""
    configure(client, high_activity_threshold=10)
    mine = make_quote(client, quote_number="Q-1", owner="dana", signing_status="not_sent")
    make_quote(client, quote_number="Q-2", owner="sam", status="DRAFT")
    make_quote(client, quote_number="Q-3", owner="dana", status="DRAFT")

    def numbers(**params):
        payload = client.get(f"{PREFIX}/quotes", params=params).json()
        return {row["data"]["quote_number"] for row in payload["entries"]}

    assert numbers(status="DRAFT") == {"Q-2", "Q-3"}
    assert numbers(owner="dana") == {"Q-1", "Q-3"}
    assert numbers(signing_status="not_sent") == {"Q-1"}
    assert numbers(signing_status="NOT_SENT") == {"Q-1"}
    assert mine["data"]["signing_status"] == "not_sent"


def test_the_quote_index_filters_on_a_json_path_the_team_added_itself(client):
    """Researched: "Advanced filters", served by the store's own dotted-path query.

    A team that adds ``data.my_own_field`` must be able to filter on it without
    this workflow knowing the field exists, which is what the generic ``where``
    is for.
    """
    configure(client, high_activity_threshold=10)
    mine = make_quote(client, my_own_field="renewal")
    make_quote(client, my_own_field="expansion")

    payload = client.get(
        f"{PREFIX}/quotes", params={"where": json_where({"my_own_field": "renewal"})}
    ).json()

    assert [row["id"] for row in payload["entries"]] == [mine["id"]]


def json_where(payload):
    """The compact or JSON form of the store's where parameter."""
    import json as _json

    return _json.dumps(payload)


def test_a_malformed_advanced_filter_is_a_422_naming_the_problem(client):
    """A bad filter is a refusal, not a 500 from deep inside a query builder."""
    response = client.get(f"{PREFIX}/quotes", params={"where": "{not json"})
    assert response.status_code == 422
    assert "where" in response.json()["detail"]


def test_the_index_filters_by_activity_class(client):
    """The classification is a filter, so a seller can list only high-activity quotes."""
    configure(client, high_activity_threshold=5)
    hot = make_quote(client, quote_number="Q-hot")
    make_quote(client, quote_number="Q-cold")
    for _ in range(5):
        record(client, hot["id"], "Quote viewed")

    payload = client.get(f"{PREFIX}/quotes", params={"activity_class": "High activity"}).json()
    assert [row["id"] for row in payload["entries"]] == [hot["id"]]


# --------------------------------------------------------------------------- #
# The dashboard panels
# --------------------------------------------------------------------------- #


def test_the_chart_hides_expired_quotes_and_says_how_many_it_hid(client):
    """Researched: "tick **Show older expired quotes**"."""
    configure(client, high_activity_threshold=10, expiring_within_days=14)
    make_quote(client, quote_number="Q-live", expires_at=now_iso(10))
    make_quote(client, quote_number="Q-gone", expires_at=now_iso(-10))

    hidden = client.get(f"{PREFIX}/chart", params={"include_expired": False}).json()
    assert hidden["expired_hidden"] == 1
    assert all(point["expired"] is False for point in hidden["points"])

    shown = client.get(f"{PREFIX}/chart", params={"include_expired": True}).json()
    assert shown["expired_hidden"] == 0
    assert any(point["expired"] is True for point in shown["points"])


def test_every_chart_point_carries_the_three_hover_facts(client):
    """Researched: "; hover for name, expiration date and activity count"."""
    configure(client, high_activity_threshold=10)
    make_quote(client, title="Northwind", expires_at=now_iso(6))
    point = client.get(f"{PREFIX}/chart").json()["points"][0]
    assert point["name"] == "Northwind"
    assert point["expires_at"] is not None
    assert point["activity_count"] == 0


def test_the_overview_reports_the_three_figures_and_the_recent_activity(client):
    """Researched: "read the value of high-activity quotes, quotes at risk of expiring, and
    the quote acceptance rate"."""
    configure(client, high_activity_threshold=5, expiring_within_days=14)
    hot = make_quote(client, quote_number="Q-hot", hs_quote_amount=1000.0)
    for _ in range(5):
        record(client, hot["id"], "Quote viewed")
    make_quote(client, quote_number="Q-soon", expires_at=now_iso(3))
    make_quote(client, quote_number="Q-won", status="ACCEPTED")

    payload = client.get(f"{PREFIX}/overview").json()

    assert payload["high_activity_quotes"] == 1
    assert payload["high_activity_value"] == 1000.0
    assert payload["quotes_at_risk"] >= 1
    assert payload["acceptance_rate"] == round(1 / 3, 4)
    assert payload["recent_activity"]
    assert set(payload["recent_activity"][0]) >= {
        "name",
        "last_activity_at",
        "activity_count",
        "amount",
        "expires_at",
    }


def test_the_overview_names_the_basis_of_each_figure(client):
    """A zero from a figure that could not be computed is not a measurement."""
    payload = client.get(f"{PREFIX}/overview").json()
    assert "No threshold configured" in payload["basis"]["high_activity"]
    assert "No expiry window configured" in payload["basis"]["at_risk"]


def test_the_follow_ups_panel_reports_the_windows_it_was_measured_on(client):
    """A quiet panel says why it is quiet instead of defaulting a window."""
    payload = client.get(f"{PREFIX}/follow-ups").json()
    assert payload["count"] == 0
    assert payload["windows_configured"] == {
        "no_activity_after_days": False,
        "expiring_within_days": False,
    }


def test_the_follow_ups_panel_recommends_a_quiet_quote_with_the_reason(client):
    """Researched: "quotes with no activity in a long time"."""
    configure(client, high_activity_threshold=10, no_activity_after_days=30)
    quiet = make_quote(client, quote_number="Q-quiet")
    record(client, quiet["id"], "Quote published", occurred_at=now_iso(-60))

    payload = client.get(f"{PREFIX}/follow-ups").json()

    assert payload["count"] == 1
    entry = payload["entries"][0]
    assert entry["quote_id"] == quiet["id"]
    assert entry["reason_codes"] == ["no_activity_recently"]
    assert entry["reasons"][0]["days_idle"] >= 30


def test_the_follow_ups_panel_recommends_an_expiring_quote(client):
    """Researched: "or quotes that'll expire soon"."""
    configure(client, high_activity_threshold=10, expiring_within_days=14)
    soon = make_quote(client, quote_number="Q-soon", expires_at=now_iso(5))
    record(client, soon["id"], "Quote viewed")

    payload = client.get(f"{PREFIX}/follow-ups").json()
    assert [entry["quote_id"] for entry in payload["entries"]] == [soon["id"]]
    assert payload["entries"][0]["reason_codes"] == ["expiring_soon"]


def test_sending_a_follow_up_records_a_new_quote_sent(client):
    """Researched: "generating a new `Quote sent` activity"."""
    configure(client, high_activity_threshold=10, no_activity_after_days=30)
    quote = make_quote(client)
    record(client, quote["id"], "Quote sent")

    response = client.post(f"{PREFIX}/follow-ups/{quote['id']}/send", json={})

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["event"]["data"]["event"] == "Quote sent"
    assert payload["engagement"]["activity_count"] == 2
    assert payload["renewed"] is False
    assert payload["opened"] == "send_quote_dialog"


def test_a_follow_up_on_an_expired_quote_reports_a_new_send(client):
    """Researched: "(if previously expired/recalled) a new send"."""
    configure(client, high_activity_threshold=10, no_activity_after_days=30)
    quote = make_quote(client, status="EXPIRED")

    payload = client.post(f"{PREFIX}/follow-ups/{quote['id']}/send", json={}).json()

    assert payload["renewed"] is True
    assert payload["renewed_from"] == "EXPIRED"


def test_a_follow_up_does_not_restate_the_quote(client):
    """Whether an expired quote becomes live is the expiry workflow's decision."""
    configure(client, high_activity_threshold=10, no_activity_after_days=30)
    quote = make_quote(client, status="EXPIRED")

    client.post(f"{PREFIX}/follow-ups/{quote['id']}/send", json={})

    stored = client.get(f"/api/records/quote/{quote['id']}").json()
    assert stored["data"]["status"] == "EXPIRED"


def test_sending_a_follow_up_to_a_quote_that_does_not_exist_is_a_422(client):
    """Every write route refuses rather than writing against nothing."""
    response = client.post(f"{PREFIX}/follow-ups/quote_absent/send", json={})
    assert response.status_code == 422
    assert "not found" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# The summary
# --------------------------------------------------------------------------- #


def test_the_summary_names_each_figure_it_could_not_compute(client):
    """The page reads this list instead of rendering zeros it cannot explain."""
    payload = client.get(f"{PREFIX}/summary").json()
    assert set(payload["unavailable"]) == {
        "high_activity_threshold",
        "no_activity_after_days",
        "expiring_within_days",
    }
    assert payload["counters"] == [
        "last_activity_at",
        "activity_count",
        "activity_count_since_last_communication",
        "view_count",
    ]


def test_a_configured_workspace_reports_no_unavailable_figures(client):
    """Once the workspace has set them, the summary stops apologising."""
    configure(
        client,
        high_activity_threshold=10,
        no_activity_after_days=30,
        expiring_within_days=14,
    )
    payload = client.get(f"{PREFIX}/summary").json()
    assert payload["unavailable"] == []


# --------------------------------------------------------------------------- #
# Routes called the way a route sweep calls them
# --------------------------------------------------------------------------- #


def test_every_write_route_answers_a_call_with_an_empty_body(client):
    """A sweep calls each write route with ``{}`` and a substituted id.

    Every one of those must be a refusal the rules made, not a 500 from a handler
    that assumed a body. This is the test that keeps the sweep green.
    """
    configure(client, high_activity_threshold=10)
    calls = [
        client.post(f"{PREFIX}/quotes/{{quote_id}}/activity", json={}),
        client.post(f"{PREFIX}/follow-ups/{{quote_id}}/send", json={}),
        client.put(f"{PREFIX}/settings", json={}),
    ]
    for response in calls:
        assert response.status_code < 500, response.text
        assert response.status_code in (200, 201, 422), response.text


def test_every_read_route_answers_an_absent_quote(client):
    """A route reading a quote that does not exist answers 404 or 422, not 5xx."""
    calls = [
        client.get(f"{PREFIX}/quotes/quote_absent/engagement"),
        client.get(f"{PREFIX}/quotes/quote_absent/activity"),
    ]
    for response in calls:
        assert response.status_code in (404, 422), response.text
        assert response.status_code < 500
