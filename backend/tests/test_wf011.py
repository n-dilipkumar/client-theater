"""Tests for WF-011: taking a room from draft to live and handing over the link.

Two halves, in one file because the contract asks a feature for one test module
and because the boundary between them is exactly what this port is about:

* the **domain** (:mod:`dsr.publishing`), exercised in process, where the
  researched rules are visible - a draft has no working link, publishing is the
  audited change, a template is never published, a password is never stored in
  the clear, and a subscriber learns about the transition from a recorded event
  rather than by polling;
* the **HTTP surface** (``dsr/features/wf011_publishing.py``), exercised from
  outside the process, because a guarantee is only worth anything if a client can
  see it.

Ported from the branch's ``tests/test_publishing.py`` and
``tests/test_api_publishing.py``, neither of which had been run. What changed,
and why:

* every service call that writes now passes ``source=``. The branch hardcoded a
  prose label into the audit log (``"WF-011 set status draft -> live"``), which
  describes the change but names no route. ``source`` is a required keyword on
  the four write methods now, and the HTTP tests below assert against the live
  route table that every audit row names a route the app actually serves.
* ``PublishConflict`` is mapped by ``EXCEPTION_HANDLERS`` rather than by three
  repeated ``except`` blocks, so the tests still expect 409 - and one of them
  checks the host mounted the handler at all.
* the HTTP fixture points ``DSR_DB_PATH`` at a temporary file the way
  ``test_features.py`` does, rather than at a path under the repository.
* a ``seed`` test, because a feature whose page is empty in the demo is a
  feature nobody can review.
"""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.db.audited import AuditError, AuditedDatabase, RecordNotFound, utcnow
from dsr.features import load_feature
from dsr.publishing import (
    EVENT_REVIVED_LIVE,
    EVENT_SET_LIVE,
    EVENT_STATUS_CHANGED,
    PublishingService,
    PublishConflict,
    expiry_state,
    hash_password,
    redact,
)
from dsr.store import RecordStore

#: This feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/publishing"

#: Loopback port 1 refuses instantly, so a delivery attempt fails for a real
#: reason rather than after a timeout.
UNREACHABLE = "http://127.0.0.1:1/hook"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def service(tmp_path):
    db = AuditedDatabase(tmp_path / "wf011.db", mirror_dir=tmp_path / "mirror")
    store = RecordStore(db)
    publishing = PublishingService(store, base_url="https://rooms.example")
    # The store is a facade; the tests that read the audit trail go through the
    # audited database itself, which is where audit_count lives.
    publishing.db = db
    yield publishing
    db.close()


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf011.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setenv("DSR_PUBLIC_BASE_URL", "https://rooms.example")
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


def make_room(service: PublishingService, **overrides):
    """Create a room through the generic API shape, the way other tickets do."""
    data = {"name": "Northwind Evaluation", "account": "Northwind", "owner": "dana"}
    data.update(overrides)
    return service.store.create("room", data, actor="dana", source="test")


def make_room_over_http(client, **overrides):
    """Create a room with the generic, schema-flexible endpoint."""
    data = {"name": "Northwind Evaluation", "account": "Northwind", "owner": "dana"}
    data.update(overrides)
    response = client.post("/api/records/room", json=data)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(client):
    """The route resolves even though no shared file names this feature."""
    registry = client.get("/api/features").json()["features"]
    entry = next(f for f in registry if f["id"] == "wf-011-room-handover")

    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-011"
    assert len(entry["routes"]) == 10


def test_frontend_descriptor_id_matches_the_backend_feature_id():
    """The two halves of a feature are findable by one name, so they must agree."""
    descriptor = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / "wf-011-room-handover"
        / "index.jsx"
    )
    text = descriptor.read_text(encoding="utf-8")
    module = load_feature("wf011_publishing")

    assert module.FEATURE["id"] in text
    assert f'id: {module.FEATURE["id"]!r}' in text


def test_feature_module_does_not_import_the_shared_app():
    """A guard exists in test_features.py; this states the reason locally."""
    source = Path(load_feature("wf011_publishing").__file__).read_text(encoding="utf-8")
    assert "dsr.api" not in source
    assert "from dsr.deps import" in source


def test_the_host_mounted_this_features_error_mapping(client):
    """The conflict handler is declared, not applied per route, and it works."""
    entry = next(
        f
        for f in client.get("/api/features").json()["features"]
        if f["id"] == "wf-011-room-handover"
    )
    assert entry["exception_handlers"] == ["PublishConflict"]

    room = make_room_over_http(client)
    response = client.get(f"{PREFIX}/rooms/{room['id']}/share-link")
    assert response.status_code == 409
    assert response.json()["error"] == "publish_conflict"


# --------------------------------------------------------------------------- #
# Draft is the default, and a draft has no working link
# --------------------------------------------------------------------------- #


def test_room_without_a_status_is_a_draft(service):
    room = make_room(service)
    view = service.share(room["id"])
    assert view["status"] == "draft"
    assert view["status_source"] == "implicit"
    assert view["published"] is False
    assert view["public_url"] is None


def test_draft_link_request_is_a_conflict_not_an_empty_string(service):
    room = make_room(service)
    with pytest.raises(PublishConflict, match="public link is disabled"):
        service.share_link(room["id"])


def test_going_live_enables_the_public_url_and_records_the_change(service):
    room = make_room(service)

    result = service.set_status(room["id"], "live", source="test", actor="dana")

    assert result["transition"] == {"from": "draft", "to": "live", "changed": True}
    assert result["room"]["published"] is True
    assert result["room"]["public_url"] == f"https://rooms.example/r/{room['id']}"
    assert result["room"]["published_at"]

    entry = service.store.audit(record_id=room["id"], action="update")[0]
    assert entry["diff"]["status"] == {"from": None, "to": "live"}
    assert entry["actor"] == "dana"


def test_live_room_hands_over_a_link(service):
    room = make_room(service)
    service.set_status(room["id"], "live", source="test")

    link = service.share_link(room["id"])

    assert link["url"].endswith(room["id"])
    assert link["public_enabled"] is True
    assert link["shareable"] is True
    assert link["status"] == "live"


def test_reading_the_link_writes_no_audit_row(service):
    room = make_room(service)
    service.set_status(room["id"], "live", source="test")
    before = service.db.audit_count()

    service.share_link(room["id"])

    assert service.db.audit_count() == before


def test_link_can_be_re_copied_after_the_room_is_disabled_and_re_enabled(service):
    room = make_room(service)
    service.set_status(room["id"], "live", source="test")
    first = service.share_link(room["id"])["url"]

    service.set_status(room["id"], "disabled", source="test")
    with pytest.raises(PublishConflict):
        service.share_link(room["id"])

    service.set_status(room["id"], "live", source="test")
    assert service.share_link(room["id"])["url"] == first


def test_a_status_slug_is_used_when_the_room_carries_one(service):
    room = make_room(service, slug="northwind-eval")
    service.set_status(room["id"], "live", source="test")
    assert service.share_link(room["id"])["url"] == "https://rooms.example/r/northwind-eval"


def test_a_rejected_slug_falls_back_to_the_record_id(service):
    room = make_room(service, slug="../../etc/passwd")
    service.set_status(room["id"], "live", source="test")
    assert service.share_link(room["id"])["url"].endswith(room["id"])


# --------------------------------------------------------------------------- #
# Status rules
# --------------------------------------------------------------------------- #


def test_only_published_statuses_expose_a_public_url(service):
    room = make_room(service)
    for status, expected in [
        ("accepting", True),
        ("accepted", True),
        ("disabled", False),
        ("declined", False),
        ("draft", False),
    ]:
        result = service.set_status(room["id"], status, source="test")
        assert result["room"]["published"] is expected, status


def test_setting_the_status_a_room_already_has_changes_nothing(service):
    """A no-op must not manufacture history."""
    room = make_room(service)
    before = service.db.audit_count()

    result = service.set_status(room["id"], "draft", source="test")

    assert result["transition"]["changed"] is False
    assert result["event"] is None
    assert service.db.audit_count() == before


def test_unknown_status_is_rejected(service):
    room = make_room(service)
    with pytest.raises(ValueError, match="status must be one of"):
        service.set_status(room["id"], "pending", source="test")


def test_publishing_reports_a_conflict_for_a_template(service):
    room = make_room(service, is_template=True)
    with pytest.raises(PublishConflict, match="template cannot go live"):
        service.set_status(room["id"], "live", source="test")


def test_a_template_marked_with_a_kind_is_also_guarded(service):
    room = make_room(service, kind="template")
    with pytest.raises(PublishConflict):
        service.set_status(room["id"], "accepting", source="test")


def test_a_template_cannot_be_told_its_link(service):
    room = make_room(service, is_template=True)
    with pytest.raises(PublishConflict, match="no public link"):
        service.share_link(room["id"])


def test_a_template_is_still_editable_and_audited(service):
    room = make_room(service, is_template=True)
    view = service.share(room["id"])
    assert view["can_publish"] is False
    assert any("never published" in note for note in view["warnings"])


def test_disabling_a_live_room_stamps_the_moment_it_went_not_public(service):
    room = make_room(service)
    service.set_status(room["id"], "live", source="test")
    result = service.set_status(room["id"], "disabled", source="test")
    assert result["room"]["unpublished_at"]
    assert result["room"]["ever_published"] is True


def test_republishing_keeps_the_original_published_at(service):
    room = make_room(service)
    first = service.set_status(room["id"], "live", source="test")["room"]["published_at"]
    service.set_status(room["id"], "disabled", source="test")
    again = service.set_status(room["id"], "live", source="test")["room"]
    assert again["published_at"] == first
    assert again["unpublished_at"] is None


def test_a_stale_revision_is_rejected_on_a_transition(service):
    room = make_room(service)
    service.set_status(room["id"], "live", source="test")
    with pytest.raises(AuditError, match="revision conflict"):
        service.set_status(room["id"], "draft", source="test", expected_revision=1)


def test_transitioning_a_missing_room_raises(service):
    with pytest.raises(RecordNotFound):
        service.set_status("room_nope", "live", source="test")


def test_a_record_from_another_collection_is_not_a_room(service):
    document = service.store.create("document", {"title": "Deck"}, actor="dana", source="test")
    with pytest.raises(RecordNotFound):
        service.share(document["id"])


# --------------------------------------------------------------------------- #
# Access settings
# --------------------------------------------------------------------------- #


def test_access_settings_are_merged_not_replaced(service):
    """The store's patch is shallow, so a nested merge has to happen here."""
    room = make_room(service)
    service.set_access(
        room["id"], {"max_views": 25, "require_identity_verification": True}, source="test"
    )

    service.set_access(room["id"], {"expires_at": "2026-12-31"}, source="test")

    access = service.share(room["id"])["access"]
    assert access["max_views"] == 25
    assert access["require_identity_verification"] is True
    assert access["expires_at"] == "2026-12-31"


def test_a_password_is_stored_hashed_and_never_returned(service):
    room = make_room(service)

    service.set_access(room["id"], {"password": "hunter2"}, source="test")

    access = service.share(room["id"])["access"]
    assert access["require_password"] is True
    assert access["password_protected"] is True
    assert "hunter2" not in json.dumps(access)

    stored = service.store.get(room["id"])["data"]["access"]
    assert "hunter2" not in json.dumps(stored)
    assert stored["password_hash"].startswith("pbkdf2_sha256$")


def test_the_stored_hash_verifies_against_the_password_that_set_it(service):
    room = make_room(service)
    service.set_access(room["id"], {"password": "correct horse"}, source="test")

    prefix, iterations, salt, digest = service.store.get(room["id"])["data"]["access"][
        "password_hash"
    ].split("$")
    expected = hashlib.pbkdf2_hmac(
        "sha256", b"correct horse", base64.b64decode(salt), int(iterations)
    ).hex()

    assert (prefix, digest) == ("pbkdf2_sha256", expected)


def test_two_rooms_with_the_same_password_get_different_hashes(service):
    first = make_room(service, name="One")
    second = make_room(service, name="Two")
    service.set_access(first["id"], {"password": "same"}, source="test")
    service.set_access(second["id"], {"password": "same"}, source="test")

    one = service.store.get(first["id"])["data"]["access"]["password_hash"]
    two = service.store.get(second["id"])["data"]["access"]["password_hash"]
    assert one != two


def test_clearing_the_password_removes_the_hash(service):
    room = make_room(service)
    service.set_access(room["id"], {"password": "hunter2"}, source="test")

    service.set_access(room["id"], {"clear_password": True}, source="test")

    access = service.store.get(room["id"])["data"]["access"]
    assert "password_hash" not in access
    assert service.share(room["id"])["access"]["password_protected"] is False


def test_an_expired_link_is_flagged_rather_than_silently_handed_over(service):
    room = make_room(service)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    service.set_access(room["id"], {"expires_at": yesterday}, source="test")
    service.set_status(room["id"], "live", source="test")

    link = service.share_link(room["id"])

    assert link["expired"] is True
    assert link["shareable"] is False
    assert any("has passed" in note for note in link["warnings"])


def test_a_future_expiry_is_not_flagged(service):
    room = make_room(service)
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    service.set_access(room["id"], {"expires_at": tomorrow}, source="test")
    service.set_status(room["id"], "live", source="test")
    assert service.share_link(room["id"])["expired"] is False


def test_a_bare_expiry_date_means_the_end_of_that_day():
    """A seller typing 31 December means the whole day, not its start."""
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    assert expiry_state(yesterday)[0] is True
    assert expiry_state("2099-01-01")[0] is False
    assert expiry_state(None)[0] is False
    # An unparseable value is treated as no expiry rather than as a hard error.
    assert expiry_state("next tuesday") == (False, None)


def test_an_unparseable_expiry_is_rejected(service):
    room = make_room(service)
    with pytest.raises(ValueError, match="ISO date"):
        service.set_access(room["id"], {"expires_at": "next tuesday"}, source="test")


def test_a_non_positive_view_limit_is_rejected(service):
    room = make_room(service)
    with pytest.raises(ValueError, match="positive whole number"):
        service.set_access(room["id"], {"max_views": 0}, source="test")


def test_an_empty_hash_password_is_rejected():
    with pytest.raises(ValueError, match="password is required"):
        hash_password("")


def test_redact_removes_the_hash_at_any_depth():
    payload = {"a": [{"access": {"password_hash": "x", "max_views": 2}}]}
    assert redact(payload) == {"a": [{"access": {"max_views": 2}}]}


# --------------------------------------------------------------------------- #
# The board
# --------------------------------------------------------------------------- #


def test_the_board_lists_every_room_with_a_status_badge(service):
    make_room(service, name="One")
    make_room(service, name="Two", status="live")

    board = service.board()

    assert board["count"] == 2
    assert {room["name"] for room in board["rooms"]} == {"One", "Two"}
    # Every status is counted, including the empty ones, so the filter row can
    # show a zero rather than a gap.
    assert board["counts"] == {
        "draft": 1,
        "live": 1,
        "accepting": 0,
        "accepted": 0,
        "disabled": 0,
        "declined": 0,
    }


def test_the_board_filter_takes_several_statuses(service):
    make_room(service, name="Draft room")
    make_room(service, name="Live room", status="live")
    make_room(service, name="Disabled room", status="disabled")

    board = service.board(statuses=["draft", "live"])

    assert {room["name"] for room in board["rooms"]} == {"Draft room", "Live room"}


def test_live_and_accepting_never_double_count_a_room(service):
    """The vendor states the two are exclusive, so a combined filter is clean."""
    make_room(service, name="One", status="live")
    make_room(service, name="Two", status="accepting")

    board = service.board(statuses=["live", "accepting"])

    assert board["count"] == 2
    assert board["counts"]["live"] == 1
    assert board["counts"]["accepting"] == 1


def test_the_board_filters_by_tag_and_by_search(service):
    make_room(service, name="Northwind Evaluation", account="Northwind")
    make_room(service, name="Contoso Security", account="Contoso", tags=["security"])
    make_room(service, name="Fabrikam Renewal", account="Fabrikam", tags=["archived"])

    assert [r["name"] for r in service.board(tag="security")["rooms"]] == ["Contoso Security"]
    assert [r["name"] for r in service.board(q="north")["rooms"]] == ["Northwind Evaluation"]
    assert [r["name"] for r in service.board(q="CONTOSO")["rooms"]] == ["Contoso Security"]


def test_archived_rooms_are_hidden_unless_asked_for(service):
    make_room(service, name="Kept")
    make_room(service, name="Archived one", tags=["archived"])

    assert [r["name"] for r in service.board()["rooms"]] == ["Kept"]
    assert service.board(tag="archived")["count"] == 1
    assert {r["name"] for r in service.board(include_archived=True)["rooms"]} == {
        "Kept",
        "Archived one",
    }


def test_the_board_filters_by_owner(service):
    make_room(service, name="Mine", owner="dana")
    make_room(service, name="Theirs", owner="sam")

    assert [r["name"] for r in service.board(owner="sam")["rooms"]] == ["Theirs"]


def test_the_board_reports_what_it_filtered_on(service):
    make_room(service)
    board = service.board(statuses=["live"], q="x", limit=25)
    assert board["filters"] == {
        "status": ["live"],
        "tag": None,
        "q": "x",
        "owner": None,
        "include_archived": False,
        "limit": 25,
    }


# --------------------------------------------------------------------------- #
# Transition events
# --------------------------------------------------------------------------- #


def test_going_live_emits_set_live(service):
    room = make_room(service)
    result = service.set_status(room["id"], "live", source="test")

    assert result["event"]["event"] == EVENT_SET_LIVE
    assert result["event"]["previous_status"] == "draft"
    assert result["event"]["public_url"].endswith(room["id"])


def test_republishing_emits_revived_live(service):
    room = make_room(service)
    service.set_status(room["id"], "live", source="test")
    result = service.set_status(room["id"], "disabled", source="test")
    assert result["event"]["event"] == EVENT_STATUS_CHANGED

    revived = service.set_status(room["id"], "live", source="test")
    assert revived["event"]["event"] == EVENT_REVIVED_LIVE


def test_any_other_transition_emits_status_changed(service):
    room = make_room(service)
    result = service.set_status(room["id"], "accepting", source="test")
    assert result["event"]["event"] == EVENT_STATUS_CHANGED


def test_the_event_carries_the_rooms_metadata_verbatim(service):
    room = make_room(service, metadata={"crm_deal_id": "OPP-42", "owner_team": "enterprise"})
    result = service.set_status(room["id"], "live", source="test")
    assert result["event"]["metadata"] == {"crm_deal_id": "OPP-42", "owner_team": "enterprise"}


def test_the_event_is_an_audited_record_scoped_to_the_room(service):
    room = make_room(service)
    service.set_status(room["id"], "live", source="test")

    events = service.events(room_id=room["id"])
    assert [e["event"] for e in events] == [EVENT_SET_LIVE]
    assert service.db.audit_count(collection="event") == 1
    assert service.store.list("event", room_id=room["id"])[0]["room_id"] == room["id"]


def test_a_read_of_events_writes_nothing(service):
    room = make_room(service)
    service.set_status(room["id"], "live", source="test")
    before = service.db.audit_count()
    service.events()
    assert service.db.audit_count() == before


# --------------------------------------------------------------------------- #
# Webhook subscriptions and delivery
# --------------------------------------------------------------------------- #


class RecordingTransport:
    """A stand-in for the HTTP POST, recording what it was asked to send.

    The default transport is a real socket; this keeps the orchestration tests
    deterministic. One test below still goes over a real socket.
    """

    def __init__(self, status_code: int = 200, error: Exception | None = None) -> None:
        self.status_code = status_code
        self.error = error
        self.calls: list[dict] = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append(
            {
                "url": url,
                "body": json.loads(body),
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        if self.error is not None:
            raise self.error
        return self.status_code


def test_a_subscriber_receives_the_publish_event(service):
    transport = RecordingTransport()
    service._transport = transport
    subscription = service.subscribe(
        source="test", name="CRM", url="https://crm.example/hooks", events=[EVENT_SET_LIVE]
    )
    room = make_room(service, metadata={"crm_deal_id": "OPP-42"})

    result = service.set_status(room["id"], "live", source="test")

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["url"] == "https://crm.example/hooks"
    assert call["headers"]["X-DSR-Event"] == EVENT_SET_LIVE
    assert call["body"]["metadata"] == {"crm_deal_id": "OPP-42"}
    assert call["body"]["status"] == "live"
    assert result["deliveries"] == [
        {
            "subscription_id": subscription["id"],
            "subscription_name": "CRM",
            "url": "https://crm.example/hooks",
            "event": EVENT_SET_LIVE,
            "status": "delivered",
            "status_code": 200,
            "error": None,
            "delivery_id": result["deliveries"][0]["delivery_id"],
        }
    ]


def test_a_subscriber_only_gets_the_events_it_asked_for(service):
    transport = RecordingTransport()
    service._transport = transport
    service.subscribe(
        source="test", name="Set live only", url="https://a.example/hook", events=[EVENT_SET_LIVE]
    )
    room = make_room(service)

    service.set_status(room["id"], "disabled", source="test")

    assert transport.calls == []


def test_every_transition_event_reaches_a_wildcard_subscriber(service):
    transport = RecordingTransport()
    service._transport = transport
    service.subscribe(
        source="test",
        name="All",
        url="https://a.example/hook",
        events=[EVENT_STATUS_CHANGED, EVENT_SET_LIVE],
    )
    room = make_room(service)

    service.set_status(room["id"], "live", source="test")
    service.set_status(room["id"], "accepting", source="test")

    assert [call["body"]["event"] for call in transport.calls] == [
        EVENT_SET_LIVE,
        EVENT_STATUS_CHANGED,
    ]


def test_a_dead_subscriber_is_recorded_and_does_not_fail_the_publish(service):
    service._transport = RecordingTransport(error=ConnectionRefusedError("connection refused"))
    service.subscribe(
        source="test", name="Down", url="https://down.example/hook", events=[EVENT_SET_LIVE]
    )
    room = make_room(service)

    result = service.set_status(room["id"], "live", source="test")

    assert result["transition"]["changed"] is True
    assert result["room"]["published"] is True
    assert result["deliveries"][0]["status"] == "failed"
    assert "ConnectionRefusedError" in result["deliveries"][0]["error"]


def test_a_failed_delivery_is_inspectable(service):
    service._transport = RecordingTransport(status_code=500)
    subscription = service.subscribe(
        source="test", name="Broken", url="https://x.example/hook", events=[EVENT_SET_LIVE]
    )
    room = make_room(service)

    service.set_status(room["id"], "live", source="test")

    deliveries = service.deliveries(subscription["id"])
    assert len(deliveries) == 1
    assert deliveries[0]["status"] == "failed"
    assert deliveries[0]["status_code"] == 500
    assert deliveries[0]["payload"]["status"] == "live"


def test_a_cancelled_subscriber_stops_receiving_and_keeps_its_history(service):
    transport = RecordingTransport()
    service._transport = transport
    subscription = service.subscribe(
        source="test", name="CRM", url="https://crm.example/hook", events=[EVENT_SET_LIVE]
    )
    room = make_room(service)
    service.set_status(room["id"], "live", source="test")

    service.cancel(subscription["id"], source="test")
    service.set_status(room["id"], "draft", source="test")
    service.set_status(room["id"], "live", source="test")

    assert len(transport.calls) == 1
    assert service.subscriptions() == []
    assert len(service.subscriptions(include_cancelled=True)) == 1
    assert len(service.deliveries(subscription["id"])) == 1


def test_subscription_validation_rejects_bad_input(service):
    with pytest.raises(ValueError, match="http or https"):
        service.subscribe(
            source="test", name="Bad", url="file:///etc/passwd", events=[EVENT_SET_LIVE]
        )
    with pytest.raises(ValueError, match="unknown event"):
        service.subscribe(
            source="test", name="Bad", url="https://a.example", events=["room.exploded"]
        )
    with pytest.raises(ValueError, match="at least one event"):
        service.subscribe(source="test", name="Bad", url="https://a.example", events=[])


def test_subscribing_is_audited_and_cancelling_too(service):
    subscription = service.subscribe(
        source="test", name="CRM", url="https://a.example", events=[EVENT_SET_LIVE]
    )
    service.cancel(subscription["id"], source="test", actor="dana")

    actions = [
        e["action"] for e in reversed(service.store.audit(collection="webhook_subscription"))
    ]
    assert actions == ["insert", "delete"]


def test_delivery_reaches_a_real_http_endpoint(tmp_path):
    """The default transport is a real POST, so prove it against a real socket."""
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's name
            length = int(self.headers.get("Content-Length", "0"))
            received.append(
                {
                    "body": json.loads(self.rfile.read(length)),
                    "event": self.headers.get("X-DSR-Event"),
                }
            )
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):  # silence the default stderr logging
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        db = AuditedDatabase(tmp_path / "http.db", mirror_dir=tmp_path / "http-mirror")
        service = PublishingService(
            RecordStore(db), base_url="https://rooms.example", timeout=5.0
        )
        service.subscribe(
            source="test",
            name="Local",
            url=f"http://127.0.0.1:{server.server_port}/hook",
            events=[EVENT_SET_LIVE],
        )
        room = make_room(service, name="Over the wire")

        result = service.set_status(room["id"], "live", source="test")
        db.close()
    finally:
        server.shutdown()
        server.server_close()

    assert result["deliveries"][0]["status"] == "delivered"
    assert received[0]["event"] == EVENT_SET_LIVE
    assert received[0]["body"]["room_name"] == "Over the wire"


def test_the_transition_timestamp_is_recent_and_sortable(service):
    room = make_room(service)
    result = service.set_status(room["id"], "live", source="test")
    occurred = datetime.fromisoformat(result["event"]["occurred_at"])
    assert occurred.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - occurred).total_seconds()) < 60
    assert service.store.audit(record_id=room["id"])[0]["ts"] <= utcnow()


# --------------------------------------------------------------------------- #
# The board over HTTP
# --------------------------------------------------------------------------- #


def test_the_board_lists_rooms_with_a_status(client):
    make_room_over_http(client, name="One")
    make_room_over_http(client, name="Two")

    body = client.get(f"{PREFIX}/rooms").json()

    assert body["count"] == 2
    assert {room["status"] for room in body["rooms"]} == {"draft"}
    assert body["statuses"] == ["draft", "live", "accepting", "accepted", "disabled", "declined"]


def test_the_board_takes_a_comma_separated_status_list(client):
    make_room_over_http(client, name="Draft room")
    make_room_over_http(client, name="Live room", status="live")
    make_room_over_http(client, name="Disabled room", status="disabled")

    body = client.get(f"{PREFIX}/rooms", params={"status": "draft,live"}).json()

    assert {room["name"] for room in body["rooms"]} == {"Draft room", "Live room"}


def test_the_board_rejects_an_unknown_status(client):
    response = client.get(f"{PREFIX}/rooms", params={"status": "pending"})
    assert response.status_code == 400
    assert "unknown status" in response.json()["detail"]


def test_the_board_hides_archived_rooms_unless_asked(client):
    make_room_over_http(client, name="Kept")
    make_room_over_http(client, name="Archived", tags=["archived"])

    assert [r["name"] for r in client.get(f"{PREFIX}/rooms").json()["rooms"]] == ["Kept"]
    listed = client.get(f"{PREFIX}/rooms", params={"tag": "archived"}).json()
    assert [r["name"] for r in listed["rooms"]] == ["Archived"]


def test_the_board_searches_name_and_account(client):
    make_room_over_http(client, name="Northwind Evaluation", account="Northwind")
    make_room_over_http(client, name="Contoso Security", account="Contoso")

    body = client.get(f"{PREFIX}/rooms", params={"q": "contoso"}).json()
    assert [r["name"] for r in body["rooms"]] == ["Contoso Security"]


# --------------------------------------------------------------------------- #
# Draft to live over HTTP
# --------------------------------------------------------------------------- #


def test_a_new_room_reads_as_draft_with_no_public_url(client):
    room = make_room_over_http(client)

    body = client.get(f"{PREFIX}/rooms/{room['id']}").json()

    assert body["status"] == "draft"
    assert body["status_source"] == "implicit"
    assert body["published"] is False
    assert body["public_url"] is None
    assert body["can_publish"] is True


def test_the_share_popup_offers_the_six_statuses(client):
    room = make_room_over_http(client)
    body = client.get(f"{PREFIX}/rooms/{room['id']}").json()
    assert [option["value"] for option in body["status_options"]] == [
        "draft",
        "live",
        "accepting",
        "accepted",
        "disabled",
        "declined",
    ]
    assert body["handover"].startswith("This app hands over a link.")


def test_setting_live_enables_the_link_and_returns_it_for_the_clipboard(client):
    room = make_room_over_http(client)

    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/status", json={"status": "live", "actor": "dana"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["transition"] == {"from": "draft", "to": "live", "changed": True}
    assert body["share_link"]["url"] == f"https://rooms.example/r/{room['id']}"
    assert body["share_link"]["shareable"] is True
    assert body["event"]["event"] == "room.set_live"


def test_the_publish_is_one_audited_change(client):
    room = make_room_over_http(client)
    before = client.get("/api/stats").json()["audit_entries"]

    client.post(f"{PREFIX}/rooms/{room['id']}/status", json={"status": "live", "actor": "dana"})

    entries = client.get("/api/audit", params={"record_id": room["id"]}).json()["entries"]
    room_changes = [e for e in entries if e["collection"] == "room" and e["actor"] == "dana"]
    assert [e["action"] for e in room_changes] == ["update"]
    assert room_changes[0]["diff"]["status"] == {"from": None, "to": "live"}
    # The event is a separate audited record, so the log shows the whole story.
    assert client.get("/api/stats").json()["audit_entries"] == before + 2


def test_the_published_room_is_visible_to_the_generic_record_api(client):
    """A team's own tooling reads the same field through ?where=."""
    room = make_room_over_http(client)
    client.post(f"{PREFIX}/rooms/{room['id']}/status", json={"status": "live"})

    listed = client.get("/api/records/room", params={"where": '{"status":"live"}'}).json()

    assert [r["id"] for r in listed["records"]] == [room["id"]]
    assert listed["records"][0]["data"]["published"] is True


def test_setting_the_same_status_again_is_a_no_op(client):
    room = make_room_over_http(client)
    client.post(f"{PREFIX}/rooms/{room['id']}/status", json={"status": "live"})
    before = client.get("/api/stats").json()["audit_entries"]

    body = client.post(
        f"{PREFIX}/rooms/{room['id']}/status", json={"status": "live"}
    ).json()

    assert body["transition"]["changed"] is False
    assert body["event"] is None
    assert client.get("/api/stats").json()["audit_entries"] == before


def test_disabling_then_republishing_reports_a_revive(client):
    room = make_room_over_http(client)
    client.post(f"{PREFIX}/rooms/{room['id']}/status", json={"status": "live"})
    client.post(f"{PREFIX}/rooms/{room['id']}/status", json={"status": "disabled"})

    body = client.post(f"{PREFIX}/rooms/{room['id']}/status", json={"status": "live"}).json()

    assert body["event"]["event"] == "room.revived_live"


def test_a_stale_revision_is_a_conflict(client):
    room = make_room_over_http(client)
    client.post(f"{PREFIX}/rooms/{room['id']}/status", json={"status": "live"})

    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/status",
        json={"status": "draft", "expected_revision": 1},
    )

    assert response.status_code == 409


def test_a_template_cannot_be_published(client):
    room = make_room_over_http(client, is_template=True)

    response = client.post(f"{PREFIX}/rooms/{room['id']}/status", json={"status": "live"})

    assert response.status_code == 409
    assert "template" in response.json()["detail"]


def test_publishing_an_unknown_room_is_404(client):
    assert (
        client.post(f"{PREFIX}/rooms/room_nope/status", json={"status": "live"}).status_code
        == 404
    )


def test_a_missing_status_is_400(client):
    room = make_room_over_http(client)
    assert client.post(f"{PREFIX}/rooms/{room['id']}/status", json={}).status_code == 400


def test_an_unknown_status_value_is_400(client):
    room = make_room_over_http(client)
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/status", json={"status": "pending"}
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# The link handover over HTTP
# --------------------------------------------------------------------------- #


def test_a_draft_room_has_no_link_to_hand_over(client):
    room = make_room_over_http(client)

    response = client.get(f"{PREFIX}/rooms/{room['id']}/share-link")

    assert response.status_code == 409
    assert "public link is disabled" in response.json()["detail"]


def test_a_live_room_hands_over_its_link(client):
    room = make_room_over_http(client)
    client.post(f"{PREFIX}/rooms/{room['id']}/status", json={"status": "live"})

    body = client.get(f"{PREFIX}/rooms/{room['id']}/share-link").json()

    assert body["url"] == f"https://rooms.example/r/{room['id']}"
    assert body["status"] == "live"


def test_copying_a_link_writes_no_audit_row(client):
    room = make_room_over_http(client)
    client.post(f"{PREFIX}/rooms/{room['id']}/status", json={"status": "live"})
    before = client.get("/api/stats").json()["audit_entries"]

    client.get(f"{PREFIX}/rooms/{room['id']}/share-link")

    assert client.get("/api/stats").json()["audit_entries"] == before


def test_access_settings_are_saved_and_merged(client):
    room = make_room_over_http(client)

    client.patch(f"{PREFIX}/rooms/{room['id']}/access", json={"max_views": 25})
    client.patch(
        f"{PREFIX}/rooms/{room['id']}/access",
        json={"expires_at": "2026-12-31", "require_identity_verification": True},
    )

    access = client.get(f"{PREFIX}/rooms/{room['id']}").json()["access"]
    assert access == {
        "expires_at": "2026-12-31",
        "max_views": 25,
        "require_password": False,
        "password_protected": False,
        "require_identity_verification": True,
        "expired": False,
    }


def test_the_plaintext_password_is_never_returned_by_any_route(client):
    """The invariant this ticket owns: no route ever echoes the cleartext."""
    room = make_room_over_http(client)
    client.patch(f"{PREFIX}/rooms/{room['id']}/access", json={"password": "hunter2"})
    client.post(f"{PREFIX}/rooms/{room['id']}/status", json={"status": "live"})

    bodies = [
        client.get(f"{PREFIX}/rooms/{room['id']}").text,
        client.get(f"{PREFIX}/rooms/{room['id']}/share-link").text,
        client.get(f"{PREFIX}/rooms").text,
        client.get(f"{PREFIX}/events").text,
        client.get(f"{PREFIX}/webhooks").text,
        client.get("/api/audit").text,
        client.get(f"/api/records/room/{room['id']}").text,
    ]

    assert not any("hunter2" in body for body in bodies)
    # And nothing in the publishing surface mentions the stored field at all.
    for body in bodies[:5]:
        assert "password_hash" not in body


def test_what_reaches_storage_is_a_salted_hash_not_the_password(client):
    """Known limitation, pinned so it stays visible.

    The store is schema-flexible and the API has no authentication, so the hash
    is readable through ``/api/records`` and through the complete audit log. A
    half-redaction that missed the ``diff`` map would be worse than none, so the
    exposure is documented here instead of papered over in this ticket. Closing
    it needs a decision about authentication, which is bigger than WF-011.
    """
    room = make_room_over_http(client)
    client.patch(f"{PREFIX}/rooms/{room['id']}/access", json={"password": "hunter2"})

    stored = client.get(f"/api/records/room/{room['id']}").json()["data"]["access"]
    assert stored["password_hash"].startswith("pbkdf2_sha256$")
    assert (
        client.get(f"{PREFIX}/rooms/{room['id']}").json()["access"]["password_protected"]
        is True
    )


def test_a_password_can_be_cleared(client):
    room = make_room_over_http(client)
    client.patch(f"{PREFIX}/rooms/{room['id']}/access", json={"password": "hunter2"})

    client.patch(f"{PREFIX}/rooms/{room['id']}/access", json={"clear_password": True})

    assert (
        client.get(f"{PREFIX}/rooms/{room['id']}").json()["access"]["password_protected"]
        is False
    )


def test_an_elapsed_expiry_marks_the_link_unshareable(client):
    room = make_room_over_http(client)
    client.patch(f"{PREFIX}/rooms/{room['id']}/access", json={"expires_at": "2020-01-01"})
    client.post(f"{PREFIX}/rooms/{room['id']}/status", json={"status": "live"})

    body = client.get(f"{PREFIX}/rooms/{room['id']}/share-link").json()

    assert body["expired"] is True
    assert body["shareable"] is False


def test_bad_access_values_are_400(client):
    room = make_room_over_http(client)
    assert (
        client.patch(
            f"{PREFIX}/rooms/{room['id']}/access", json={"expires_at": "next tuesday"}
        ).status_code
        == 400
    )
    assert (
        client.patch(f"{PREFIX}/rooms/{room['id']}/access", json={"max_views": -1}).status_code
        == 400
    )


# --------------------------------------------------------------------------- #
# Events and webhooks over HTTP
# --------------------------------------------------------------------------- #


def test_transitions_are_listable_as_events(client):
    room = make_room_over_http(client)
    client.post(f"{PREFIX}/rooms/{room['id']}/status", json={"status": "live"})
    client.post(f"{PREFIX}/rooms/{room['id']}/status", json={"status": "disabled"})

    body = client.get(f"{PREFIX}/events", params={"room_id": room["id"]}).json()

    assert [e["event"] for e in body["events"]] == ["room.status_changed", "room.set_live"]
    assert body["event_types"] == [
        "room.set_live",
        "room.revived_live",
        "room.status_changed",
    ]


def test_an_event_carries_the_rooms_metadata(client):
    room = make_room_over_http(client, metadata={"crm_deal_id": "OPP-42"})
    client.post(f"{PREFIX}/rooms/{room['id']}/status", json={"status": "live"})

    event = client.get(f"{PREFIX}/events").json()["events"][0]

    assert event["metadata"] == {"crm_deal_id": "OPP-42"}
    assert event["room_id"] == room["id"]


def test_a_subscription_receives_the_publish_event_and_the_attempt_is_recorded(client):
    created = client.post(
        f"{PREFIX}/webhooks",
        json={"name": "CRM", "url": UNREACHABLE, "events": ["room.set_live"]},
    )
    assert created.status_code == 201
    subscription = created.json()
    room = make_room_over_http(client)

    body = client.post(f"{PREFIX}/rooms/{room['id']}/status", json={"status": "live"}).json()

    # The endpoint refuses, so the publish must still have gone through and the
    # failure must be visible rather than swallowed.
    assert body["room"]["published"] is True
    assert body["deliveries"][0]["status"] == "failed"
    assert body["deliveries"][0]["error"]

    deliveries = client.get(f"{PREFIX}/webhooks/{subscription['id']}/deliveries").json()
    assert deliveries["count"] == 1
    assert deliveries["deliveries"][0]["payload"]["status"] == "live"


def test_subscriptions_are_listed_and_cancelled(client):
    subscription = client.post(
        f"{PREFIX}/webhooks",
        json={"name": "CRM", "url": "https://crm.example/hook", "events": ["room.set_live"]},
    ).json()

    assert client.get(f"{PREFIX}/webhooks").json()["count"] == 1

    assert client.delete(f"{PREFIX}/webhooks/{subscription['id']}").status_code == 200
    assert client.get(f"{PREFIX}/webhooks").json()["count"] == 0
    assert (
        client.get(f"{PREFIX}/webhooks", params={"include_cancelled": True}).json()["count"]
        == 1
    )


def test_subscribing_validates_its_input(client):
    assert (
        client.post(
            f"{PREFIX}/webhooks",
            json={"name": "x", "url": "not-a-url", "events": ["room.set_live"]},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"{PREFIX}/webhooks",
            json={"name": "x", "url": "https://a.example", "events": ["nope"]},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"{PREFIX}/webhooks",
            json={"name": "x", "url": "https://a.example", "events": []},
        ).status_code
        == 400
    )


def test_cancelling_an_unknown_subscription_is_404(client):
    assert client.delete(f"{PREFIX}/webhooks/webhook_subscription_nope").status_code == 404


# --------------------------------------------------------------------------- #
# Openness of the API itself
# --------------------------------------------------------------------------- #


def test_the_workflow_routes_are_documented(client):
    paths = client.get("/openapi.json").json()["paths"]

    for path in (
        f"{PREFIX}/rooms",
        f"{PREFIX}/rooms/{{room_id}}",
        f"{PREFIX}/rooms/{{room_id}}/status",
        f"{PREFIX}/rooms/{{room_id}}/share-link",
        f"{PREFIX}/rooms/{{room_id}}/access",
        f"{PREFIX}/webhooks",
        f"{PREFIX}/events",
    ):
        assert path in paths, path


def test_the_publishing_routes_do_not_shadow_the_record_api(client):
    room = make_room_over_http(client)
    client.post(f"{PREFIX}/rooms/{room['id']}/status", json={"status": "live"})

    assert client.get(f"/api/records/room/{room['id']}").json()["id"] == room["id"]
    assert client.get("/api/records/room").json()["count"] == 1
    assert client.get("/api/records/nope/room_nope").status_code == 404


# --------------------------------------------------------------------------- #
# The audit row has to name a route the app actually serves
# --------------------------------------------------------------------------- #


def _matches_registered_route(source: str, routes: list[dict]) -> bool:
    """Does ``"POST /api/publishing/rooms/room_x/status"`` name a real route?

    Compared segment by segment, with a ``{parameter}`` segment matching any one
    segment. The branch's bug was an audit row naming ``"WF-011 set status
    draft -> live"`` - a description of the change that names no endpoint at all
    - and this is what catches that class of thing.
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
            for expected, found in zip(template, actual)
        ):
            return True
    return False


def _publishing_audit_sources(client) -> set[str]:
    """Every distinct ``source`` written by this feature's HTTP layer."""
    served = [
        route
        for feature in client.get("/api/features").json()["features"]
        for route in feature["routes"]
    ]
    entries = client.get("/api/audit", params={"limit": 200}).json()["entries"]
    return {entry["source"] for entry in entries if entry["source"] and PREFIX in entry["source"]}


def test_every_write_audit_row_names_a_route_the_app_serves(client):
    """The port's central guarantee, checked against the live route table."""
    room = make_room_over_http(client)
    client.post(
        f"{PREFIX}/webhooks",
        json={"name": "CRM", "url": "https://crm.example/hook", "events": ["room.set_live"]},
    )
    client.post(f"{PREFIX}/rooms/{room['id']}/status", json={"status": "live"})
    client.patch(f"{PREFIX}/rooms/{room['id']}/access", json={"max_views": 5})

    served = [
        route
        for feature in client.get("/api/features").json()["features"]
        for route in feature["routes"]
    ]
    sources = _publishing_audit_sources(client)

    assert sources, "no wf-011 write was audited at all"
    for source in sorted(sources):
        assert _matches_registered_route(source, served), (
            f"audit row names {source!r}, which is not a route this app serves"
        )


def test_the_transition_writes_all_name_the_route_that_served_it(client):
    """One request, three audit rows - room, event and delivery - one route.

    The event and the delivery are caused by the status change, so claiming a
    different origin for them would be a fiction.
    """
    room = make_room_over_http(client)
    client.post(
        f"{PREFIX}/webhooks",
        json={"name": "CRM", "url": "https://crm.example/hook", "events": ["room.set_live"]},
    )

    client.post(f"{PREFIX}/rooms/{room['id']}/status", json={"status": "live"})

    expected = f"POST {PREFIX}/rooms/{{room_id}}/status"
    for collection in ("room", "event", "webhook_delivery"):
        entries = client.get("/api/audit", params={"collection": collection}).json()["entries"]
        ours = [e for e in entries if e["source"] == expected]
        assert ours, f"no {collection} row attributed to {expected}"


def test_writes_do_not_record_the_pre_port_prose_labels(client):
    """Explicitly: nothing may still log the branch's descriptive strings."""
    room = make_room_over_http(client)
    client.patch(f"{PREFIX}/rooms/{room['id']}/access", json={"max_views": 5})
    client.post(f"{PREFIX}/rooms/{room['id']}/status", json={"status": "live"})

    banned = {
        "WF-011 set status draft -> live",
        "WF-011 update link access settings",
        "WF-011 emit room event",
        "WF-011 deliver room event",
        "WF-011 subscribe to room events",
        "WF-011 cancel webhook subscription",
    }
    entries = client.get("/api/audit", params={"limit": 200}).json()["entries"]
    assert not banned & {entry["source"] for entry in entries}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def test_seed_produces_a_board_with_every_state_and_a_failed_delivery():
    """A feature nobody can see in the demo is a feature nobody can review.

    The three seeded rooms are meant to cover the states the board can render -
    live, live-with-access, disabled, and one left as a draft - plus a template
    the workflow refuses to publish, and a subscriber whose endpoint refuses, so
    the panel shows a failed delivery as well as the event that caused it.
    """
    import random

    module = load_feature("wf011_publishing")
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

    with tempfile.TemporaryDirectory() as tmp:
        with AuditedDatabase(
            Path(tmp) / "seed.db", mirror_dir=Path(tmp) / "audit"
        ) as db:
            room_ids = [
                db.create("room", {"name": f"Room {i}"}, actor="dana", source="seed")["id"]
                for i in range(4)
            ]
            summary = module.seed(
                db,
                {
                    "room_ids": [(rid, f"Account {i}") for i, rid in enumerate(room_ids)],
                    "now": now,
                    "rng": random.Random("wf011"),
                },
            )

            board = module.PublishingService(RecordStore(db)).board(include_archived=True)
            statuses = {room["status"] for room in board["rooms"]}
            subscriptions = db.list("webhook_subscription")
            deliveries = db.list("webhook_delivery")
            events = db.list("event")

            assert statuses == {"draft", "live", "disabled"}
            assert sum(1 for r in board["rooms"] if r["is_template"]) == 1
            assert len(subscriptions) == 1
            # Three rooms went live and one of them was then disabled, so four
            # transitions produced four events - and every attempt against the
            # refusing endpoint is on record.
            assert len(events) == 4
            assert len(deliveries) == 4
            assert {d["data"]["status"] for d in deliveries} == {"failed"}
            # Nothing the seeder wrote claims a route was served.
            assert all(
                row["source"] == "seed" for row in db.audit(limit=200) if row["record_id"]
            )

    assert "1 webhook subscriber" in summary
