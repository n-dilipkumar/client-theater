"""Tests for WF-002: building a room's buyer-facing pages from DSR fragments.

Ported from ``backend/tests/test_pages.py`` on
``feature/WF-002-build-the-room-s-buyer-facing-pages-from``. The tests themselves
are the branch's, with three changes that the port forces:

* every path moved under the feature's own prefix, ``/api/wf-002``, because the
  branch's ``/api/rooms/...`` is a core-shaped path and the contract asks for a
  ticket-derived prefix;
* the fixture no longer patches ``dsr.api`` for anything, because the feature
  takes its store from ``dsr.deps``;
* new tests for the two things the port itself changed: the prefix is what the
  registry reports, and the audit ``source`` names the route that served each
  write rather than a string hardcoded in the domain layer.

The workflow's whole point is its last step. "Click *Publish*. The fragment
appears on the page the next time a member opens the room." So most of what
follows pins that one sentence down from the outside: a draft edit must be
invisible to the buyer read path, publishing must make it visible, and
unpublishing must take it away again without losing the draft.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import REGISTRY
from dsr.pages import PageService
from dsr.store import RecordStore
from fastapi.testclient import TestClient

#: The feature's prefix, spelled once. Everything below is relative to it, so a
#: prefix change is a one-line change rather than a find-and-replace across
#: fifty tests that each hard-code it.
PREFIX = "/api/wf-002"


@pytest.fixture()
def client(monkeypatch):
    """A client over a throwaway database, the way test_features.py does it.

    The feature's router is mounted by the host at import time, so pointing
    ``DSR_DB_PATH`` at a temporary file is all the isolation it needs. Only
    ``FRONTEND_DIST`` is still patched, and that is the shared SPA fallback, not
    anything this feature owns.
    """
    import dsr.api as api_module

    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf002.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr(api_module, "FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def make_room(client, **overrides):
    payload = {"name": "Northwind Traders", "account": "Northwind"}
    payload.update(overrides)
    return client.post("/api/records/room", json=payload).json()


def make_document(client, room_id, title="Security Pack", **overrides):
    payload = {"title": title, "kind": "pdf", "status": "published"}
    payload.update(overrides)
    return client.post("/api/records/document", params={"room_id": room_id}, json=payload).json()


def make_page(client, room_id, title="Welcome", *, actor=None, **overrides):
    payload = {"title": title}
    payload.update(overrides)
    response = client.post(
        f"{PREFIX}/rooms/{room_id}/pages",
        json=payload,
        params={"actor": actor} if actor else {},
    )
    assert response.status_code == 201, response.text
    return response.json()


def place(client, room_id, page_id, fragment, config=None, **params):
    body = {"fragment": fragment}
    if config is not None:
        body["config"] = config
    response = client.post(
        f"{PREFIX}/rooms/{room_id}/pages/{page_id}/blocks", json=body, params=params
    )
    return response


def publish(client, room_id, page_id, **params):
    return client.post(f"{PREFIX}/rooms/{room_id}/pages/{page_id}/publish", params=params)


def blocks_of(response_json):
    return response_json["data"]["blocks"]


# --------------------------------------------------------------------------- #
# The port itself: mounted by discovery, under its own prefix
# --------------------------------------------------------------------------- #


def test_the_feature_is_mounted_by_discovery(client):
    """The router was never named in api.py, yet its route resolves."""
    record = REGISTRY.by_id("wf-002-buyer-pages")

    assert record is not None, "the feature host did not load wf-002"
    assert record.loaded is True
    assert record.prefix == PREFIX
    assert record.ticket == "WF-002"
    assert record.exception_handlers == ["FragmentError", "PermissionDenied"]


def test_the_registry_advertises_the_routes(client):
    record = REGISTRY.by_id("wf-002-buyer-pages")

    paths = {route["path"] for route in record.routes}
    assert f"{PREFIX}/rooms/{{room_id}}/pages" in paths
    assert f"{PREFIX}/rooms/{{room_id}}/pages/{{page_id}}/publish" in paths
    assert f"{PREFIX}/rooms/{{room_id}}/view/{{slug}}" in paths
    # Every route carries the feature's own prefix: the branch's core-shaped
    # `/api/rooms/...` is the thing this port had to give up.
    assert all(path.startswith(PREFIX) for path in paths)


def test_the_feature_did_not_squat_on_a_core_route(client):
    """`/api/rooms/...` is core-shaped. Claiming it would shadow another feature."""
    core_shaped = {
        route["path"]
        for route in REGISTRY.by_id("wf-002-buyer-pages").routes
        if not route["path"].startswith(PREFIX)
    }
    assert core_shaped == set()


def test_a_write_records_the_route_that_served_it(client):
    """The defect the contract names: an audit row must not name a stale path.

    The branch hardcoded its write sources inside the domain module
    (``"POST fragment-set"``, ``f"create page in {room_id}"``). Every row below
    has to name this feature's actual route instead.
    """
    room = make_room(client)
    page = make_page(client, room["id"], actor="dana")
    place(client, room["id"], page["id"], "welcome", actor="dana")
    publish(client, room["id"], page["id"], actor="dana")

    sources = {
        entry["source"]
        for entry in client.get("/api/audit", params={"limit": 100}).json()["entries"]
        if entry["collection"] in ("page", "page_revision")
    }

    assert f"POST {PREFIX}/rooms/{room['id']}/pages" in sources
    assert f"POST {PREFIX}/rooms/{room['id']}/pages/{page['id']}/blocks" in sources
    # Publish writes twice on purpose, and both rows name the publish route.
    publish_rows = {s for s in sources if "/publish" in (s or "")}
    assert len(publish_rows) == 2, publish_rows
    assert all(s.startswith(f"POST {PREFIX}/") for s in sources), sources


def test_the_catalogue_write_records_its_own_route(client):
    client.post(
        f"{PREFIX}/fragment-sets",
        json={"key": "acme-blocks", "name": "Acme"},
        params={"actor": "dana"},
    )

    entry = client.get("/api/audit", params={"collection": "fragment_set"}).json()["entries"][0]
    assert entry["source"] == f"POST {PREFIX}/fragment-sets"


# --------------------------------------------------------------------------- #
# The shipped catalogue
# --------------------------------------------------------------------------- #


def test_three_shipped_fragment_sets_are_served(client):
    body = client.get(f"{PREFIX}/fragment-sets").json()

    assert [item["key"] for item in body["sets"]] == [
        "digital-sales-room",
        "digital-sales-room-analytics",
        "dsr-fragments",
    ]
    assert body["counts"]["shipped_sets"] == 3
    assert body["counts"]["shipped_fragments"] == 25


def test_each_shipped_set_has_the_documented_number_of_fragments(client):
    body = client.get(f"{PREFIX}/fragment-sets").json()
    sizes = {item["key"]: len(item["fragments"]) for item in body["sets"]}

    # 11, 10 and 4 as the research records them.
    assert sizes == {
        "digital-sales-room": 11,
        "digital-sales-room-analytics": 10,
        "dsr-fragments": 4,
    }


def test_documented_fragment_names_are_all_present(client):
    body = client.get(f"{PREFIX}/fragment-sets").json()
    names = {item["name"] for item in body["fragments"]}

    assert {
        # Digital Sales Room (11)
        "Document Gallery Block",
        "Gallery Block",
        "Header Main",
        "Header User",
        "Our Team Block",
        "PDF Preview Block",
        "Question and Answer Block",
        "Text Block",
        "Timeline Block",
        "Video Block",
        "Welcome Block",
        # Digital Sales Room Analytics (10)
        "Activity Log",
        "Documents Statistics",
        "Engagement Chart",
        "Frequency Chart",
        "Latest Activity",
        "Most Active Visitors",
        "Navigation",
        "Room General",
        "Room Statistics",
        "Room Trend",
        # DSR Fragments (4)
        "Page Bar",
        "Sidebar",
        "Sidebar Trigger",
        "Vertical Navigation",
    } == names


def test_timeline_declares_the_documented_fields_and_default(client):
    body = client.get(f"{PREFIX}/fragment-sets", params={"set": "digital-sales-room"}).json()
    timeline = next(f for f in body["fragments"] if f["key"] == "timeline")

    fields = {field["key"]: field for field in timeline["fields"]}
    assert fields["number_of_steps"]["default"] == 4
    assert "current_step" in fields
    assert timeline["documented_fields"] is True


def test_video_declares_url_dimensions_and_autoplay_off_by_default(client):
    body = client.get(f"{PREFIX}/fragment-sets", params={"set": "digital-sales-room"}).json()
    video = next(f for f in body["fragments"] if f["key"] == "video")

    fields = {field["key"]: field for field in video["fields"]}
    assert {"url", "width", "height", "autoplay"} <= set(fields)
    assert fields["autoplay"]["default"] is False


def test_console_fragments_declare_their_documented_aria_hooks(client):
    body = client.get(f"{PREFIX}/fragment-sets", params={"set": "dsr-fragments"}).json()
    aria = {
        fragment["key"]: {field["key"] for field in fragment["fields"] if field.get("aria")}
        for fragment in body["fragments"]
    }

    assert aria["page-bar"] == {"header_image_alt_description"}
    assert aria["sidebar"] == {"sidebar_aria_label"}
    assert aria["vertical-navigation"] == {"aria_label"}


def test_fragments_with_no_documented_fields_say_so(client):
    # The research names these fragments but documents no field list for them.
    # Inventing one would be a design inference presented as a specification.
    body = client.get(f"{PREFIX}/fragment-sets", params={"set": "digital-sales-room"}).json()
    undocumented = {
        fragment["key"] for fragment in body["fragments"] if not fragment["documented_fields"]
    }

    assert {"text", "welcome", "gallery", "our-team", "question-and-answer"} <= undocumented


# --------------------------------------------------------------------------- #
# Placing fragments
# --------------------------------------------------------------------------- #


def test_creating_a_page_starts_as_an_unpublished_draft(client):
    room = make_room(client)
    page = make_page(client, room["id"], "Deal overview")

    assert page["status"] == "draft"
    assert page["published"] is False
    assert page["blocks"] == []
    assert page["data"]["slug"] == "deal-overview"


def test_a_page_needs_a_title(client):
    room = make_room(client)
    assert client.post(f"{PREFIX}/rooms/{room['id']}/pages", json={}).status_code == 400


def test_two_pages_cannot_share_a_slug_in_a_room(client):
    room = make_room(client)
    make_page(client, room["id"], "Deal overview")
    clash = client.post(
        f"{PREFIX}/rooms/{room['id']}/pages",
        json={"title": "Other", "slug": "deal-overview"},
    )

    assert clash.status_code == 409


def test_placing_a_fragment_uses_its_documented_defaults(client):
    room = make_room(client)
    page = make_page(client, room["id"])

    response = place(client, room["id"], page["id"], "timeline")

    assert response.status_code == 201
    block = blocks_of(response.json())[0]
    assert block["fragment"] == "timeline"
    assert block["set"] == "digital-sales-room"
    # The documented defaults arrive without the caller naming them.
    assert block["config"] == {"number_of_steps": 4, "current_step": 0}


def test_placing_a_fragment_makes_it_draft_only(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    place(client, room["id"], page["id"], "welcome")

    assert client.get(f"{PREFIX}/rooms/{room['id']}/view").json()["count"] == 0


def test_an_unknown_fragment_is_rejected_with_a_usable_message(client):
    room = make_room(client)
    page = make_page(client, room["id"])

    response = place(client, room["id"], page["id"], "not-a-fragment")

    assert response.status_code == 400
    assert "not-a-fragment" in response.json()["detail"]


def test_configuring_a_fragment_merges_into_the_existing_config(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    block = blocks_of(place(client, room["id"], page["id"], "timeline").json())[0]

    response = client.patch(
        f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}/blocks/{block['id']}",
        json={"config": {"current_step": 2}},
    )

    config = blocks_of(response.json())[0]["config"]
    assert config["current_step"] == 2
    # The other field is untouched, so a caller can set one field at a time.
    assert config["number_of_steps"] == 4


def test_a_declared_number_field_rejects_a_non_number(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    block = blocks_of(place(client, room["id"], page["id"], "timeline").json())[0]

    response = client.patch(
        f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}/blocks/{block['id']}",
        json={"config": {"number_of_steps": "four"}},
    )

    assert response.status_code == 400
    assert "must be a number" in response.json()["detail"]


def test_a_declared_url_field_requires_an_http_url(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    block = blocks_of(place(client, room["id"], page["id"], "video").json())[0]

    response = client.patch(
        f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}/blocks/{block['id']}",
        json={"config": {"url": "javascript:alert(1)"}},
    )

    assert response.status_code == 400
    assert "http(s) URL" in response.json()["detail"]


def test_removing_a_fragment_takes_it_off_the_page(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    block = blocks_of(place(client, room["id"], page["id"], "welcome").json())[0]

    response = client.delete(f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}/blocks/{block['id']}")

    assert response.status_code == 200
    assert blocks_of(response.json()) == []


def test_reordering_requires_every_block_exactly_once(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    first = blocks_of(place(client, room["id"], page["id"], "welcome").json())[0]
    second = blocks_of(place(client, room["id"], page["id"], "text").json())[1]

    ok = client.put(
        f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}/blocks/order",
        json={"order": [second["id"], first["id"]]},
    )
    assert [b["id"] for b in blocks_of(ok.json())] == [second["id"], first["id"]]

    partial = client.put(
        f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}/blocks/order",
        json={"order": [second["id"]]},
    )
    assert partial.status_code == 400


def test_block_order_is_not_confused_with_a_block_id(client):
    """`/blocks/order` is a PUT and `/blocks/{id}` is a PATCH, so they coexist.

    Starlette matches in registration order, so this is the kind of thing that
    works until someone reorders two decorators.
    """
    room = make_room(client)
    page = make_page(client, room["id"])
    first = blocks_of(place(client, room["id"], page["id"], "welcome").json())[0]
    place(client, room["id"], page["id"], "text")

    reordered = client.put(
        f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}/blocks/order",
        json={"order": [first["id"]]},
    )
    assert reordered.status_code == 400, "an incomplete order must not be accepted"

    # And the real block is still addressable by its own id.
    configured = client.patch(
        f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}/blocks/{first['id']}",
        json={"config": {"body": "still addressable"}},
    )
    assert configured.status_code == 200
    assert blocks_of(configured.json())[0]["config"]["body"] == "still addressable"


# --------------------------------------------------------------------------- #
# The Document Gallery Block's documented four-selector limit
# --------------------------------------------------------------------------- #


def test_document_selectors_resolve_documents_from_the_room(client):
    room = make_room(client)
    document = make_document(client, room["id"], "Security Pack")
    page = make_page(client, room["id"])

    response = place(
        client, room["id"], page["id"], "document-gallery", {"document_1": document["id"]}
    )

    assert response.status_code == 201
    assert blocks_of(response.json())[0]["config"]["document_1"] == document["id"]


def test_a_document_selector_rejects_a_document_from_another_room(client):
    room = make_room(client)
    other = make_room(client, name="Contoso")
    foreign = make_document(client, other["id"], "Their pack")
    page = make_page(client, room["id"])

    response = place(
        client, room["id"], page["id"], "document-gallery", {"document_1": foreign["id"]}
    )

    assert response.status_code == 400
    assert "not a document in this room" in response.json()["detail"]


def test_a_fifth_document_selector_is_refused_with_the_documented_remedy(client):
    room = make_room(client)
    page = make_page(client, room["id"])

    response = place(client, room["id"], page["id"], "document-gallery", {"document_5": "whatever"})

    assert response.status_code == 400
    detail = response.json()["detail"]
    # "To show more than four documents on a page, add another Document
    # Gallery Block for each additional set of four."
    assert "fixed set of 4" in detail
    assert "add another Document Gallery Block" in detail


def test_a_second_document_gallery_block_allows_more_than_four_documents(client):
    # The documented remedy for the four-selector limit is another block, so
    # five documents across two blocks must be accepted.
    room = make_room(client)
    documents = [make_document(client, room["id"], f"Pack {n}")["id"] for n in range(5)]
    page = make_page(client, room["id"])

    first = place(
        client,
        room["id"],
        page["id"],
        "document-gallery",
        {f"document_{n}": documents[n - 1] for n in range(1, 5)},
    )
    second = place(client, room["id"], page["id"], "document-gallery", {"document_1": documents[4]})

    assert first.status_code == 201
    assert second.status_code == 201


# --------------------------------------------------------------------------- #
# Publish: the step that reaches the buyer
# --------------------------------------------------------------------------- #


def test_publishing_makes_the_fragment_visible_to_the_buyer(client):
    room = make_room(client)
    page = make_page(client, room["id"], "Welcome")
    place(client, room["id"], page["id"], "welcome")

    published = publish(client, room["id"], page["id"], actor="dana")

    assert published.status_code == 200
    assert published.json()["published"] is True
    view = client.get(f"{PREFIX}/rooms/{room['id']}/view/welcome").json()
    assert [block["fragment"] for block in view["blocks"]] == ["welcome"]
    assert view["revision"]["number"] == 1


def test_an_unpublished_edit_is_invisible_to_the_buyer(client):
    room = make_room(client)
    page = make_page(client, room["id"], "Welcome")
    place(client, room["id"], page["id"], "welcome")
    publish(client, room["id"], page["id"])

    # Now edit the draft without publishing.
    block = blocks_of(client.get(f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}").json())[0]
    client.patch(
        f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}/blocks/{block['id']}",
        json={"config": {"headline": "Q4 pricing"}},
    )

    view = client.get(f"{PREFIX}/rooms/{room['id']}/view/welcome").json()
    assert view["blocks"][0]["config"] == {}
    # The editor is told there is work waiting for the next Publish.
    editor = client.get(f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}").json()
    assert editor["has_unpublished_changes"] is True
    assert editor["status"] == "published"


def test_publishing_after_an_edit_reaches_the_buyer(client):
    room = make_room(client)
    page = make_page(client, room["id"], "Welcome")
    place(client, room["id"], page["id"], "welcome")
    publish(client, room["id"], page["id"])
    block = blocks_of(client.get(f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}").json())[0]
    client.patch(
        f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}/blocks/{block['id']}",
        json={"config": {"headline": "Q4 pricing"}},
    )

    publish(client, room["id"], page["id"])

    view = client.get(f"{PREFIX}/rooms/{room['id']}/view/welcome").json()
    assert view["blocks"][0]["config"]["headline"] == "Q4 pricing"
    assert view["revision"]["number"] == 2
    assert (
        client.get(f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}").json()[
            "has_unpublished_changes"
        ]
        is False
    )


def test_publishing_writes_an_immutable_revision_each_time(client):
    room = make_room(client)
    page = make_page(client, room["id"], "Welcome")
    place(client, room["id"], page["id"], "welcome")
    publish(client, room["id"], page["id"], actor="dana")
    client.post(
        f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}/publish",
        json={"note": "second pass"},
        params={"actor": "sam"},
    )

    revisions = client.get(f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}/revisions").json()

    assert revisions["count"] == 2
    assert [r["number"] for r in revisions["revisions"]] == [2, 1]
    assert revisions["revisions"][0]["note"] == "second pass"
    assert revisions["revisions"][0]["published_by"] == "sam"


def test_unpublishing_withdraws_the_page_and_keeps_the_draft(client):
    room = make_room(client)
    page = make_page(client, room["id"], "Welcome")
    place(client, room["id"], page["id"], "welcome")
    publish(client, room["id"], page["id"])

    client.post(f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}/unpublish")

    assert client.get(f"{PREFIX}/rooms/{room['id']}/view").json()["count"] == 0
    assert client.get(f"{PREFIX}/rooms/{room['id']}/view/welcome").status_code == 404
    editor = client.get(f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}").json()
    assert editor["status"] == "draft"
    assert len(editor["blocks"]) == 1
    assert (
        client.get(f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}/revisions").json()["count"] == 1
    )


def test_unpublishing_a_page_that_is_not_published_is_a_conflict(client):
    room = make_room(client)
    page = make_page(client, room["id"])

    response = client.post(f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}/unpublish")
    assert response.status_code == 400


def test_the_buyer_view_only_lists_published_pages_in_order(client):
    room = make_room(client)
    first = make_page(client, room["id"], "First", order=1)
    second = make_page(client, room["id"], "Second", order=2)
    place(client, room["id"], first["id"], "welcome")
    place(client, room["id"], second["id"], "text")
    publish(client, room["id"], second["id"])
    publish(client, room["id"], first["id"])

    body = client.get(f"{PREFIX}/rooms/{room['id']}/view").json()

    assert [page["slug"] for page in body["pages"]] == ["first", "second"]
    assert body["pages"][0]["room"]["name"] == "Northwind Traders"


def test_the_buyer_view_resolves_a_pages_document_selections(client):
    room = make_room(client)
    document = make_document(client, room["id"], "Security Pack", pages=48)
    page = make_page(client, room["id"], "Docs")
    place(client, room["id"], page["id"], "document-gallery", {"document_1": document["id"]})
    publish(client, room["id"], page["id"])

    view = client.get(f"{PREFIX}/rooms/{room['id']}/view/docs").json()
    block_id = view["blocks"][0]["id"]
    resolved = view["documents"][block_id][0]

    assert resolved["title"] == "Security Pack"
    assert resolved["pages"] == 48
    assert resolved["resolved"] is True


def test_a_page_reports_the_template_pair_it_was_built_from(client):
    # Seismic's read API reports the template and template version a page was
    # built from, so the pair is carried onto the buyer view.
    room = make_room(client)
    page = make_page(
        client,
        room["id"],
        "Welcome",
        template_id="4225d5c4-2df2-a622-2686-e82dc8b05745",
        template_version_id="6ee19b7a-32ce-42ab-23ff-ea5969855c3a",
    )
    place(client, room["id"], page["id"], "welcome")
    publish(client, room["id"], page["id"])

    view = client.get(f"{PREFIX}/rooms/{room['id']}/view/welcome").json()

    assert view["template_id"] == "4225d5c4-2df2-a622-2686-e82dc8b05745"
    assert view["template_version_id"] == "6ee19b7a-32ce-42ab-23ff-ea5969855c3a"
    assert view["fragment_sets"] == ["digital-sales-room"]


def test_a_stale_revision_is_a_conflict_not_a_silent_overwrite(client):
    # Two collaborators editing the same page: the second must not clobber the
    # first. The audited wrapper already offers optimistic concurrency, so the
    # page routes pass the caller's read revision through to it.
    room = make_room(client)
    page = make_page(client, room["id"], "Welcome")
    stale = page["revision"]

    place(client, room["id"], page["id"], "welcome")
    response = place(client, room["id"], page["id"], "text", expected_revision=stale)

    assert response.status_code == 409


# --------------------------------------------------------------------------- #
# Permissions: the documented Room Collaborator requirement
# --------------------------------------------------------------------------- #


def test_a_room_with_collaborators_refuses_a_non_collaborator(client):
    room = make_room(client, collaborators=["dana"], viewers=["buyer@example.com"])
    page = make_page(client, room["id"], actor="dana")

    refused = place(client, room["id"], page["id"], "welcome", actor="mallory")
    allowed = place(client, room["id"], page["id"], "welcome", actor="dana")

    assert refused.status_code == 403
    assert "Room Collaborator" in refused.json()["detail"]
    assert allowed.status_code == 201


def test_a_viewer_can_read_the_room_but_not_edit_its_pages(client):
    room = make_room(client, collaborators=["dana"], viewers=["buyer@example.com"])
    make_page(client, room["id"], actor="dana")

    assert client.get(f"{PREFIX}/rooms/{room['id']}/pages").status_code == 200
    assert client.get(f"{PREFIX}/rooms/{room['id']}/view").status_code == 200
    blocked = client.post(
        f"{PREFIX}/rooms/{room['id']}/pages",
        json={"title": "Sneaky"},
        params={"actor": "buyer@example.com"},
    )
    assert blocked.status_code == 403


def test_a_gated_room_refuses_an_anonymous_write(client):
    room = make_room(client, collaborators=["dana"])

    response = client.post(f"{PREFIX}/rooms/{room['id']}/pages", json={"title": "Welcome"})

    assert response.status_code == 403
    assert "named collaborator" in response.json()["detail"]


def test_a_room_can_grant_the_role_through_a_roles_map(client):
    room = make_room(client, roles={"dana": "Room Collaborator", "sam": "Viewer"})
    page = make_page(client, room["id"], actor="dana")

    assert place(client, room["id"], page["id"], "welcome", actor="dana").status_code == 201
    assert place(client, room["id"], page["id"], "welcome", actor="sam").status_code == 403


def test_the_pages_listing_reports_where_the_grant_comes_from(client):
    gated = make_room(client, collaborators=["dana"])
    open_room = make_room(client, name="Contoso")

    assert client.get(f"{PREFIX}/rooms/{gated['id']}/pages").json()["permission_source"] == "room"
    assert (
        client.get(f"{PREFIX}/rooms/{open_room['id']}/pages").json()["permission_source"]
        == "unconfigured"
    )


def test_a_malformed_grant_does_not_grant_access(client):
    # A room payload is arbitrary JSON, so collaborators may be the wrong type.
    # It must not crash the editor, and it must not accidentally grant access.
    room = make_room(client, collaborators="dana")
    page = make_page(client, room["id"], "Welcome")

    assert page["data"]["title"] == "Welcome"
    assert (
        client.get(f"{PREFIX}/rooms/{room['id']}/pages").json()["permission_source"]
        == "unconfigured"
    )


# --------------------------------------------------------------------------- #
# Extensibility: fragment sets are the documented extension mechanism
# --------------------------------------------------------------------------- #


def test_a_team_can_add_its_own_fragment_set_and_use_it(client):
    client.post(
        f"{PREFIX}/fragment-sets",
        json={"key": "acme-blocks", "name": "Acme Blocks", "summary": "Acme's own blocks."},
        params={"actor": "dana"},
    )
    client.post(
        f"{PREFIX}/fragments",
        json={
            "key": "acme-callout",
            "name": "Callout",
            "set": "acme-blocks",
            "fields": [
                {"key": "tone", "label": "Tone", "type": "select", "options": ["info", "warning"]}
            ],
        },
        params={"actor": "dana"},
    )

    catalogue = client.get(f"{PREFIX}/fragment-sets").json()
    assert catalogue["counts"] == {
        "sets": 4,
        "fragments": 26,
        "shipped_sets": 3,
        "shipped_fragments": 25,
    }
    assert next(s for s in catalogue["sets"] if s["key"] == "acme-blocks")["source"] == "custom"

    room = make_room(client)
    page = make_page(client, room["id"])
    response = place(client, room["id"], page["id"], "acme-callout", {"tone": "warning"})
    assert response.status_code == 201
    assert blocks_of(response.json())[0]["set"] == "acme-blocks"


def test_a_custom_fragments_declared_field_is_validated(client):
    client.post(f"{PREFIX}/fragment-sets", json={"key": "acme-blocks", "name": "Acme"})
    client.post(
        f"{PREFIX}/fragments",
        json={
            "key": "acme-callout",
            "name": "Callout",
            "set": "acme-blocks",
            "fields": [{"key": "tone", "label": "Tone", "type": "select", "options": ["info"]}],
        },
    )
    room = make_room(client)
    page = make_page(client, room["id"])

    bad = place(client, room["id"], page["id"], "acme-callout", {"tone": "chartreuse"})

    assert bad.status_code == 400
    assert "must be one of info" in bad.json()["detail"]


def test_a_custom_fragment_cannot_shadow_a_shipped_one(client):
    for path, payload in (
        (f"{PREFIX}/fragment-sets", {"key": "digital-sales-room", "name": "Impostor"}),
        (
            f"{PREFIX}/fragments",
            {"key": "timeline", "name": "Impostor", "set": "digital-sales-room"},
        ),
    ):
        assert client.post(path, json=payload).status_code == 409


def test_a_custom_fragment_must_name_an_existing_set(client):
    response = client.post(
        f"{PREFIX}/fragments", json={"key": "orphan", "name": "Orphan", "set": "nope"}
    )
    assert response.status_code == 400


def test_registering_a_fragment_set_is_audited(client):
    client.post(
        f"{PREFIX}/fragment-sets",
        json={"key": "acme-blocks", "name": "Acme"},
        params={"actor": "dana"},
    )

    audit = client.get("/api/audit", params={"collection": "fragment_set"}).json()
    assert audit["count"] == 1
    assert audit["entries"][0]["actor"] == "dana"


# --------------------------------------------------------------------------- #
# Schema flexibility
# --------------------------------------------------------------------------- #


def test_a_teams_own_field_on_a_fragment_survives_publish_without_a_migration(client):
    room = make_room(client)
    page = make_page(client, room["id"], "Welcome")
    block = blocks_of(place(client, room["id"], page["id"], "text").json())[0]

    # `campaign_id` is not a field the catalogue declares. It must be stored,
    # indexed and returned anyway, and must survive the publish snapshot.
    client.patch(
        f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}/blocks/{block['id']}",
        json={"config": {"campaign_id": "q4-enterprise", "tracking": {"utm_source": "email"}}},
    )
    publish(client, room["id"], page["id"])

    config = client.get(f"{PREFIX}/rooms/{room['id']}/view/welcome").json()["blocks"][0]["config"]
    assert config == {"campaign_id": "q4-enterprise", "tracking": {"utm_source": "email"}}


def test_a_teams_own_field_on_a_page_survives_publish(client):
    room = make_room(client)
    page = make_page(client, room["id"], "Welcome")
    client.patch(
        f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}",
        json={"campaign_id": "q4-enterprise"},
    )
    place(client, room["id"], page["id"], "welcome")
    publish(client, room["id"], page["id"])

    stored = client.get(f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}").json()
    assert stored["data"]["campaign_id"] == "q4-enterprise"


def test_a_page_can_be_created_whole_from_a_template(client):
    # A template hands over a title, a slug and an ordered block list; none of
    # that needs a declared shape.
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/pages",
        json={
            "title": "Enterprise overview",
            "slug": "overview",
            "template_id": "4225d5c4-2df2-a622-2686-e82dc8b05745",
            "template_version_id": "6ee19b7a-32ce-42ab-23ff-ea5969855c3a",
            "blocks": [
                {"fragment": "header-main"},
                {"fragment": "welcome"},
                {"fragment": "timeline", "config": {"number_of_steps": 5}},
            ],
        },
    )

    assert response.status_code == 201
    blocks = blocks_of(response.json())
    assert [b["fragment"] for b in blocks] == ["header-main", "welcome", "timeline"]
    assert blocks[2]["config"]["number_of_steps"] == 5


def test_creating_a_page_from_a_template_still_publishes_explicitly(client):
    room = make_room(client)
    page = client.post(
        f"{PREFIX}/rooms/{room['id']}/pages",
        json={"title": "Overview", "blocks": [{"fragment": "welcome"}]},
    ).json()

    assert client.get(f"{PREFIX}/rooms/{room['id']}/view").json()["count"] == 0

    publish(client, room["id"], page["id"])
    assert client.get(f"{PREFIX}/rooms/{room['id']}/view").json()["count"] == 1


# --------------------------------------------------------------------------- #
# The audit guarantee
# --------------------------------------------------------------------------- #


def test_building_and_publishing_a_page_is_fully_audited(client):
    room = make_room(client)
    page = make_page(client, room["id"], "Welcome", actor="dana")
    place(client, room["id"], page["id"], "welcome", actor="dana")
    publish(client, room["id"], page["id"], actor="dana")

    page_entries = client.get("/api/audit", params={"collection": "page"}).json()["entries"]
    revision_entries = client.get("/api/audit", params={"collection": "page_revision"}).json()[
        "entries"
    ]

    # create page, add block, publish pointer update.
    assert [e["action"] for e in page_entries] == ["update", "update", "insert"]
    # the publish itself wrote exactly one immutable revision
    assert [e["action"] for e in revision_entries] == ["insert"]
    assert revision_entries[0]["actor"] == "dana"
    assert {e["actor"] for e in page_entries} == {"dana"}


def test_reads_of_the_buyer_view_are_not_audited(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    place(client, room["id"], page["id"], "welcome")
    publish(client, room["id"], page["id"])
    before = client.get("/api/audit").json()["count"]

    client.get(f"{PREFIX}/rooms/{room['id']}/view")
    client.get(f"{PREFIX}/rooms/{room['id']}/view/welcome")
    client.get(f"{PREFIX}/fragment-sets")

    assert client.get("/api/audit").json()["count"] == before


def test_a_rejected_edit_changes_nothing_and_audits_nothing(client):
    room = make_room(client)
    page = make_page(client, room["id"], "Welcome")
    place(client, room["id"], page["id"], "welcome")
    before = client.get("/api/audit").json()["count"]

    bad = place(client, room["id"], page["id"], "text", config=[])

    assert bad.status_code == 400
    assert client.get("/api/audit").json()["count"] == before
    assert len(blocks_of(client.get(f"{PREFIX}/rooms/{room['id']}/pages/{page['id']}").json())) == 1


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def test_the_feature_seeds_a_reviewable_page(client):
    """A feature whose page is empty in the demo is a feature nobody can review."""
    from dsr.features import load_feature

    feature = load_feature("wf002_pages")

    tmp = tempfile.TemporaryDirectory()
    db = AuditedDatabase(Path(tmp.name) / "seeded.db", mirror_dir=str(Path(tmp.name) / "audit"))
    try:
        room = db.create("room", {"name": "Seeded"}, actor="dana", source="seed")
        db.create(
            "document",
            {"title": "Security Pack", "kind": "pdf"},
            room_id=room["id"],
            actor="dana",
            source="seed",
        )
        summary = feature.seed(db, {"room_ids": [(room["id"], "Seeded")], "now": None, "rng": None})
        assert summary, "seed() must describe what it added"

        store = RecordStore(db)
        published = PageService(store).published_pages(room["id"])

        assert [page["slug"] for page in published] == ["welcome"]
        assert published[0]["blocks"], "the published page rendered no blocks"
        # The document selector was wired to a document that exists, because the
        # selector is validated against the room's own documents.
        gallery = next(
            block for block in published[0]["blocks"] if block["fragment"] == "document-gallery"
        )
        assert published[0]["documents"][gallery["id"]][0]["resolved"] is True
        # A team's own field on the page survived the publish, with no migration.
        assert store.get(published[0]["id"])["data"]["campaign_id"] == "q4-enterprise"
    finally:
        db.close()
        tmp.cleanup()
