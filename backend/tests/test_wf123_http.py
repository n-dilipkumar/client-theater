"""WF-123: the HTTP surface of in-room qualification capture.

What is different from ``test_wf123.py``
----------------------------------------

The domain tests build the engine and call it directly. This file goes through the
router, so it is where four things are only observable:

* **The error mapping.** Each domain error is registered with its own handler and
  answers with the status the exception carries: a malformed form or answer is
  422, a form, room or submission nobody can find is 404, and an action that
  contradicts state (a form going live twice, a form on an archived room, a
  draft being routed) is 409.
* **The buyer view over the wire.** The submit and autosave routes answer with the
  buyer's view, and the test asserts the score and the band are absent from the
  JSON, not merely absent from a Python dict.
* **The audit source.** Every write names the route template it was served by, so
  the check is an equality against the routes the host actually mounted.
* **The refusals that are correct.** ``tools/verify_all_routes.py`` calls every
  advertised route with no parameters at all, so every write route here is called
  empty and has to answer 4xx rather than 5xx.

The honest limit is asserted once, through HTTP: the buyer never receives the
qualification, and the seller view is marked ``seller_only``.
"""

from __future__ import annotations

import dsr.features as host
import pytest
from dsr.features import load_feature
from dsr.qualification_capture import vocabulary as v

MODULE = "wf123_in_room_qualification_capture"
FEATURE_ID = "wf-123-qualification-capture"
PREFIX = "/api/wf-123"
ROOM = "room-1"

FULL_ANSWERS = {
    v.DIMENSION_BUDGET: "funded",
    v.DIMENSION_TIMELINE: "now",
    v.DIMENSION_AUTHORITY: "decision_maker",
    v.DIMENSION_PROCUREMENT_PATH: "known_process",
}


# --------------------------------------------------------------------------- #
# Provisioning helpers, over the store the routes share
# --------------------------------------------------------------------------- #


def _store(client):
    from dsr.api import app

    return app.state.store


def make_room(client, room_id=ROOM, **overrides):
    """A room in the core collection. Rooms are another feature's to create."""
    data = {"name": "Northwind Traders"}
    data.update(overrides)
    return _store(client).create("room", data, record_id=room_id, actor="dana", source="test")


def make_form(client, room_id=ROOM, live=True, **overrides):
    body = {"name": "Northwind qualification", "room_id": room_id, "deal_ref": "deal_northwind"}
    body.update(overrides)
    response = client.post(f"{PREFIX}/forms", json=body)
    assert response.status_code == 201, response.text
    form = response.json()
    if live:
        published = client.post(f"{PREFIX}/forms/{form['id']}/publish", json={})
        assert published.status_code == 200, published.text
    return form


def submit(client, form, room_id=ROOM, answers=None, token="t1", **extra):
    body = {"answers": answers or FULL_ANSWERS, "submission_token": token}
    body.update(extra)
    return client.post(f"{PREFIX}/rooms/{room_id}/forms/{form['id']}/submit", json=body)


@pytest.fixture
def live(client):
    """A client with one active room and one live form in it."""
    make_room(client)
    form = make_form(client)
    return client, form


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def test_the_registry_lists_this_feature_with_its_prefix_and_routes(client):
    listing = client.get("/api/features").json()
    record = next(f for f in listing["features"] if f["id"] == FEATURE_ID)
    assert record["ticket"] == "WF-123"
    assert record["prefix"] == PREFIX
    assert len(record["routes"]) == 17
    assert record["exception_handlers"] == [
        "CaptureConflict",
        "CaptureNotFound",
        "FormValidationError",
        "QualificationError",
    ]


def test_no_feature_failed_to_load(client):
    """A feature that raises on import is skipped rather than fatal, which is why
    the failure has to be asserted rather than assumed."""
    listing = client.get("/api/features").json()
    assert listing["failed_count"] == 0, [f["id"] for f in listing["failed"]]


def test_the_feature_lookup_route_answers(client):
    assert client.get(f"/api/features/{FEATURE_ID}").json()["prefix"] == PREFIX


def test_the_core_health_route_still_answers(client):
    assert client.get("/api/health").status_code == 200


# --------------------------------------------------------------------------- #
# Every route answers, and none of them 5xx
# --------------------------------------------------------------------------- #

#: Every route, with the method that answers it, called with no parameters at
#: all - which is what ``tools/verify_all_routes.py`` does. A 4xx is the correct
#: answer for a write route with nothing to write.
ALL_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/vocabulary"),
    ("GET", "/inferences"),
    ("POST", "/forms"),
    ("GET", "/forms"),
    ("GET", "/forms/form_absent"),
    ("PATCH", "/forms/form_absent"),
    ("POST", "/forms/form_absent/publish"),
    ("DELETE", "/forms/form_absent"),
    ("POST", "/forms/form_absent/restore"),
    ("GET", "/rooms/room_absent/form"),
    ("POST", "/rooms/room_absent/forms/form_absent/autosave"),
    ("POST", "/rooms/room_absent/forms/form_absent/submit"),
    ("GET", "/submissions"),
    ("GET", "/submissions/sub_absent"),
    ("POST", "/submissions/sub_absent/route"),
    ("GET", "/routes"),
    ("GET", "/rooms/room_absent/qualification"),
)


@pytest.mark.parametrize("method,path", ALL_ROUTES, ids=[f"{m} {p}" for m, p in ALL_ROUTES])
def test_every_route_answers_with_no_5xx(client, method, path):
    response = client.request(method, f"{PREFIX}{path}", json={})
    assert response.status_code < 500, f"{method} {path} -> {response.status_code} {response.text}"


def test_every_route_this_feature_advertises_is_called_by_this_file():
    """The list above has to keep up with the router, or the 5xx sweep is theatre."""
    advertised = set()
    for route in host.REGISTRY.by_id(FEATURE_ID).routes:
        tail = route["path"][len(PREFIX) :]
        tail = tail.replace("{form_id}", "form_absent")
        tail = tail.replace("{room_id}", "room_absent")
        tail = tail.replace("{submission_id}", "sub_absent")
        for method in route["methods"]:
            advertised.add((method, tail))
    assert set(ALL_ROUTES) == advertised


# --------------------------------------------------------------------------- #
# Vocabulary and the decision record
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_publishes_the_four_dimensions(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["ticket"] == "WF-123"
    assert body["dimensions"] == ["budget", "timeline", "authority", "procurement_path"]
    assert body["bands"] == ["qualified", "developing", "unqualified"]
    assert body["route_target_by_band"]["qualified"] == "account_executive"
    assert body["sourced_gaps"], "the inferred scale must say it is inferred"


def test_the_inferences_route_serves_every_decision_with_its_basis(client):
    body = client.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(body["entries"])
    assert body["count"] >= 6
    for entry in body["entries"]:
        assert {"id", "question", "options", "choice", "basis"} <= set(entry), entry
        assert entry["basis"] in {"sourced", "inferred"}


# --------------------------------------------------------------------------- #
# Forms
# --------------------------------------------------------------------------- #


def test_a_form_is_created_published_and_read_back_over_http(client):
    make_room(client)
    form = make_form(client, live=False)
    assert form["status"] == v.FORM_STATUS_DRAFT
    assert form["dimensions"] == list(v.DIMENSIONS)
    assert client.get(f"{PREFIX}/forms/{form['id']}").json()["live"] is False

    published = client.post(f"{PREFIX}/forms/{form['id']}/publish", json={})
    assert published.status_code == 200
    assert published.json()["live"] is True

    listed = client.get(f"{PREFIX}/forms", params={"room_id": ROOM}).json()
    assert listed["count"] == 1
    assert listed["entries"][0]["id"] == form["id"]


def test_a_form_without_a_room_is_422_over_http(client):
    response = client.post(f"{PREFIX}/forms", json={"name": "Qual"})
    assert response.status_code == 422
    assert response.json()["error"] == "form_invalid"


def test_a_form_on_an_unknown_room_is_404_over_http(client):
    response = client.post(f"{PREFIX}/forms", json={"name": "Qual", "room_id": "room_absent"})
    assert response.status_code == 404
    assert response.json()["error"] == "qualification_not_found"


def test_a_form_can_be_patched_over_http(client):
    make_room(client)
    form = make_form(client, live=False)
    patched = client.patch(f"{PREFIX}/forms/{form['id']}", json={"name": "Renamed"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "Renamed"


def test_a_form_cannot_go_live_on_an_archived_room_over_http(client):
    make_room(client, status=v.ARCHIVED)
    form = make_form(client, live=False)
    response = client.post(f"{PREFIX}/forms/{form['id']}/publish", json={})
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "qualification_conflict"
    assert body["reason"] == "room_not_active"


def test_a_form_cannot_go_live_twice_over_http(client):
    make_room(client)
    form = make_form(client, live=False)
    client.post(f"{PREFIX}/forms/{form['id']}/publish", json={})
    again = client.post(f"{PREFIX}/forms/{form['id']}/publish", json={})
    assert again.status_code == 409
    assert again.json()["reason"] == "already_live"


def test_a_form_can_be_deleted_and_restored_over_http(client):
    make_room(client)
    form = make_form(client, live=False)
    assert client.delete(f"{PREFIX}/forms/{form['id']}").status_code == 200
    assert client.get(f"{PREFIX}/forms/{form['id']}").status_code == 404
    assert client.post(f"{PREFIX}/forms/{form['id']}/restore", json={}).status_code == 200
    assert client.get(f"{PREFIX}/forms/{form['id']}").status_code == 200


def test_reading_an_absent_form_is_404_over_http(client):
    response = client.get(f"{PREFIX}/forms/form_absent")
    assert response.status_code == 404
    assert response.json()["error"] == "qualification_not_found"


# --------------------------------------------------------------------------- #
# The buyer widget
# --------------------------------------------------------------------------- #


def test_the_buyer_form_over_http_carries_the_questions_and_no_seller_config(live):
    client, form = live
    body = client.get(f"{PREFIX}/rooms/{ROOM}/form").json()
    assert body["id"] == form["id"]
    assert body["visibility"] == v.VISIBILITY_BUYER
    assert "score" not in body and "routing" not in body
    assert [question["id"] for question in body["questions"]] == list(v.DIMENSIONS)


def test_a_room_with_no_live_form_is_404_over_http(client):
    make_room(client)
    make_form(client, live=False)
    assert client.get(f"{PREFIX}/rooms/{ROOM}/form").status_code == 404


def test_an_autosave_over_http_answers_with_an_unscored_buyer_view(live):
    client, form = live
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/forms/{form['id']}/autosave",
        json={"answers": {v.DIMENSION_BUDGET: "approved"}, "submission_token": "t1"},
    )
    assert response.status_code == 201, response.text
    submission = response.json()["submission"]
    assert submission["status"] == v.SUBMISSION_STATUS_DRAFT
    assert "score" not in submission and "band" not in submission


def test_submitting_over_http_answers_with_a_buyer_view_that_has_no_score(live):
    client, form = live
    response = submit(client, form)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["received"] is True
    submission = body["submission"]
    assert submission["visibility"] == v.VISIBILITY_BUYER
    for hidden in v.SELLER_ONLY_FIELDS:
        assert hidden not in submission, hidden
    assert submission["answers"] == FULL_ANSWERS


def test_submitting_missing_a_required_answer_is_422_over_http(live):
    client, form = live
    response = submit(client, form, answers={v.DIMENSION_BUDGET: "funded"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "form_invalid"
    assert body["errors"]


def test_submitting_to_a_non_live_form_is_409_over_http(client):
    make_room(client)
    form = make_form(client, live=False)
    response = submit(client, form)
    assert response.status_code == 409
    assert response.json()["reason"] == "form_not_live"


# --------------------------------------------------------------------------- #
# The seller view, the routing and the summary
# --------------------------------------------------------------------------- #


def test_the_seller_submission_list_carries_the_score_and_band_over_http(live):
    client, form = live
    submit(client, form)
    body = client.get(f"{PREFIX}/submissions", params={"room_id": ROOM}).json()
    assert body["seller_only"] is True
    assert body["count"] == 1
    entry = body["entries"][0]
    assert entry["score"] == 12
    assert entry["band"] == v.BAND_QUALIFIED
    assert entry["route_target"] == "account_executive"


def test_reading_one_submission_over_http_carries_its_events(live):
    client, form = live
    submission_id = submit(client, form).json()["submission"]["id"]
    body = client.get(f"{PREFIX}/submissions/{submission_id}").json()
    assert body["score"] == 12
    assert [event["event"] for event in body["events"]] == [
        v.EVENT_SUBMITTED,
        v.EVENT_QUALIFIED,
        v.EVENT_ROUTED,
    ]


def test_submissions_can_be_filtered_by_band_over_http(live):
    client, form = live
    submit(client, form, token="hot")
    submit(
        client,
        form,
        token="cold",
        answers={
            v.DIMENSION_BUDGET: "no_budget",
            v.DIMENSION_TIMELINE: "later",
            v.DIMENSION_AUTHORITY: "unknown",
            v.DIMENSION_PROCUREMENT_PATH: "unknown",
        },
    )
    qualified = client.get(
        f"{PREFIX}/submissions", params={"room_id": ROOM, "band": v.BAND_QUALIFIED}
    ).json()
    assert qualified["count"] == 1


def test_a_draft_cannot_be_routed_over_http(live):
    client, form = live
    draft_id = client.post(
        f"{PREFIX}/rooms/{ROOM}/forms/{form['id']}/autosave",
        json={"answers": {v.DIMENSION_BUDGET: "approved"}, "submission_token": "t1"},
    ).json()["submission"]["id"]
    response = client.post(f"{PREFIX}/submissions/{draft_id}/route", json={})
    assert response.status_code == 409
    assert response.json()["reason"] == "not_submitted"


def test_a_submission_can_be_re_routed_over_http(live):
    client, form = live
    submission_id = submit(client, form).json()["submission"]["id"]
    response = client.post(f"{PREFIX}/submissions/{submission_id}/route", json={})
    assert response.status_code == 200
    assert response.json()["routed"] is True
    routes = client.get(f"{PREFIX}/routes", params={"room_id": ROOM}).json()
    assert routes["count"] == 2


def test_the_qualification_summary_over_http_is_seller_only(live):
    client, form = live
    submit(client, form)
    client.post(
        f"{PREFIX}/rooms/{ROOM}/forms/{form['id']}/autosave",
        json={"answers": {v.DIMENSION_BUDGET: "approved"}, "submission_token": "draft"},
    )
    body = client.get(f"{PREFIX}/rooms/{ROOM}/qualification").json()
    assert body["seller_only"] is True
    assert body["submissions"] == 1
    assert body["in_progress"] == 1
    assert body["by_band"][v.BAND_QUALIFIED] == 1


# --------------------------------------------------------------------------- #
# The audit source: the route that served the write
# --------------------------------------------------------------------------- #


def _mounted() -> set[str]:
    """``METHOD /path`` for every route the host mounted for this feature.

    Read out of the registry rather than out of a list written here, so the check
    is against what the app actually serves.
    """
    record = host.REGISTRY.by_id(FEATURE_ID)
    return {f"{method} {route['path']}" for route in record.routes for method in route["methods"]}


def _http_sources(client) -> set[str]:
    """The audit rows this feature's routes wrote, and nothing else.

    Narrowed to rows whose source starts with an HTTP method, because the test
    provisions its room by writing straight to the store with the source
    ``test``. Those are real audit rows; they are simply not rows a route served.
    """
    from dsr.api import app

    verbs = ("POST ", "PATCH ", "DELETE ", "PUT ")
    return {
        entry["source"]
        for entry in app.state.db.audit()
        if entry.get("source") and entry["source"].startswith(verbs)
    }


def test_every_write_route_leaves_an_audit_row_naming_a_mounted_route(live):
    client, form = live
    submission_id = submit(client, form).json()["submission"]["id"]
    client.post(f"{PREFIX}/submissions/{submission_id}/route", json={})
    client.post(
        f"{PREFIX}/rooms/{ROOM}/forms/{form['id']}/autosave",
        json={"answers": {v.DIMENSION_BUDGET: "approved"}, "submission_token": "draft"},
    )
    client.patch(f"{PREFIX}/forms/{form['id']}", json={"name": "Renamed"})
    client.delete(f"{PREFIX}/forms/{form['id']}")

    mounted = _mounted()
    ours = _http_sources(client)
    assert ours, "no write was audited at all"
    for source in ours:
        assert source in mounted, f"audit row names {source!r}, which is not a mounted route"


def test_the_expected_routes_are_the_ones_that_wrote(live):
    client, form = live
    submission_id = submit(client, form).json()["submission"]["id"]
    client.post(f"{PREFIX}/submissions/{submission_id}/route", json={})
    sources = _http_sources(client)
    assert sources == {
        f"POST {PREFIX}/forms",
        f"POST {PREFIX}/forms/{{form_id}}/publish",
        f"POST {PREFIX}/rooms/{{room_id}}/forms/{{form_id}}/submit",
        f"POST {PREFIX}/submissions/{{submission_id}}/route",
    }, sorted(sources)


# --------------------------------------------------------------------------- #
# The feature module, read rather than exercised
# --------------------------------------------------------------------------- #


def test_the_feature_module_is_importable_on_its_own():
    """Discovery mounted it; this proves it does not need the app to load."""
    feature = load_feature(MODULE)
    assert feature.FEATURE["id"] == FEATURE_ID
    assert feature.router.prefix == PREFIX
    assert callable(feature.seed)


def test_the_domain_module_imports_nothing_but_the_store():
    """The enforced rule in ``test_features.py`` greps for ``dsr.api``; this pins the
    whole import surface of the package so a new framework import is caught here."""
    from pathlib import Path

    package = Path(__file__).resolve().parents[1] / "dsr" / "qualification_capture"
    allowed_ds = {
        "dsr.store",
        "dsr.qualification_capture",
        "dsr.room_lifecycle",
        "dsr.access",
    }
    for module in sorted(package.glob("*.py")):
        text = module.read_text(encoding="utf-8")
        assert "dsr.api" not in text, module.name
        assert "sqlite3" not in text, module.name
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            for token in stripped.split():
                if token.startswith("dsr.") and not token.startswith("__"):
                    root = ".".join(token.split(".")[:2])
                    if root.startswith("dsr.") and root not in allowed_ds:
                        assert (
                            token.startswith("dsr.qualification_capture") or root in allowed_ds
                        ), f"{module.name} imports {token}, which is outside the store"
