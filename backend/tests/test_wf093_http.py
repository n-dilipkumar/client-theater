"""WF-093 over HTTP: the surface this feature's own router serves.

The domain rules are in ``test_wf093.py``. This file is the other half, and it is
organised by what a caller can observe:

``the route table``
    That the host mounted every route by discovery alone, with no shared file edited,
    and that no two of them collide.
``templates``
    Create, replace, read and list, with the module order and the custom-module flag.
``brands``
    The brand kit, and the refusal for a colour a browser cannot draw.
``the quotes this workflow reads``
    That they are read and never written here, and where they come from.
``the merge``
    Preview without writing, instantiate with writing, and the report each carries.
``the non-retroactive rule``
    Publishing freezes a document, and a frozen one refuses a re-render with a 403.
``the error shapes``
    Every status code and body this router can produce.
``the audit-source rule``
    Every ``source=`` this workflow records names a route the host actually mounted.
``the read-path rule``
    The reads write no audit rows, because a route that logged every read would fill this
    product's own guarantee with entries describing no change.

Every test here passes with the file run on its own.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any

from dsr.quoting_proposals import vocabulary as vocab
from fastapi.testclient import TestClient

FEATURE_MODULE = "dsr.features.wf093_build_a_branded_proposal_from_a_template"
PREFIX = "/api/wf-093"
FEATURE_ID = "wf-093-build-a-branded-proposal-from-a-template"

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)


def _store(client: TestClient):
    from dsr.api import app

    return app.state.store


def make_brand(client: TestClient, **extra: Any) -> dict[str, Any]:
    response = client.post(
        f"{PREFIX}/brands", params={"room_id": "room_a"}, json={"name": "Halcyon Cloud", **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_template(client: TestClient, **extra: Any) -> dict[str, Any]:
    response = client.post(
        f"{PREFIX}/templates",
        params={"room_id": "room_a"},
        json={"name": "Standard proposal", **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_quote(client: TestClient, **extra: Any) -> dict[str, Any]:
    """A quote row, written the way WF-086 writes one.

    This workflow reads ``wf086_quote`` and never writes it through a route, so the
    fixture writes the row as data. That is the read-only decision
    (``DERIVED_QUOTE_IS_READ_AS_DATA``) asserted from the outside.
    """

    return _store(client).create(
        vocab.QUOTES,
        {
            "title": "Northwind renewal",
            "deal": {"seller": {"name": "Halcyon Cloud"}},
            "company": {"name": "Northwind Logistics"},
            "currency_label": "USD",
            "issue_date": NOW.isoformat(),
            **extra,
        },
        room_id="room_a",
        actor="dana",
        source="wf-093 test fixture",
    )


def make_line_items(client: TestClient, quote_id: str, amounts: tuple[float, ...]) -> None:
    store = _store(client)
    for position, amount in enumerate(amounts):
        store.create(
            vocab.LINE_ITEMS,
            {
                "quote_id": quote_id,
                "name": f"Item {position + 1}",
                "amount": amount,
                "position": position + 1,
            },
            room_id="room_a",
            actor="dana",
            source="wf-093 test fixture",
        )


def instantiate(
    client: TestClient, template_id: str, quote_id: str, **params: Any
) -> dict[str, Any]:
    response = client.post(
        f"{PREFIX}/documents",
        params={"room_id": "room_a", "actor": "dana", **params},
        json={"template_id": template_id, "quote_id": quote_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# The route table
# --------------------------------------------------------------------------- #


def test_the_host_mounted_the_feature_by_discovery_alone(client: TestClient):
    """No shared file was edited, so mounting is the only registration step."""

    from dsr.api import app
    from dsr.features import load_features

    registry = load_features(app)
    found = registry.by_id(FEATURE_ID)
    assert found is not None, [f.id for f in registry.failed]
    assert found.loaded
    assert found.prefix == PREFIX
    assert found.error == ""


def test_the_feature_owns_its_ticket_derived_prefix():
    from dsr.features import wf093_build_a_branded_proposal_from_a_template as feature

    assert feature.router.prefix == PREFIX
    assert feature.FEATURE["ticket"] == "WF-093"
    assert feature.FEATURE["name"]


def test_no_route_of_this_feature_collides_with_another():
    """The host refuses a collision, so a mounted feature has none by construction."""

    from dsr.api import app
    from dsr.features import load_features

    registry = load_features(app)
    found = registry.by_id(FEATURE_ID)
    assert found.error == ""
    assert found not in registry.failed


def test_every_route_this_router_serves_answers(client: TestClient):
    """Each read answers 200 on an empty store.

    A board that 500s on a fresh room is a broken feature, so every read is exercised
    before any row exists rather than only after a seed.
    """

    for path in (
        "/summary",
        "/vocabulary",
        "/decisions",
        "/templates",
        "/brands",
        "/quotes",
        "/documents",
    ):
        response = client.get(f"{PREFIX}{path}")
        assert response.status_code == 200, (path, response.text)


def test_the_feature_module_reaches_no_shared_file_and_opens_no_connection():
    """The enforced isolation rule, asserted on this feature's own module."""

    from pathlib import Path

    from dsr import features

    path = Path(features.__file__).parent / "wf093_build_a_branded_proposal_from_a_template.py"
    text = path.read_text(encoding="utf-8")
    assert "from dsr.api" not in text
    assert "import dsr.api" not in text
    assert "import sqlite3" not in text
    assert "sqlite3.connect" not in text


def test_the_feature_module_takes_its_dependencies_from_deps():
    module = __import__(FEATURE_MODULE, fromlist=["router"])
    source = inspect.getsource(module)
    assert "from dsr.deps import" in source
    assert "from dsr.api import" not in source


def test_the_feature_maps_only_error_types_it_raises_itself():
    """A handler for a shared type would intercept that exception across the product."""

    from dsr.features import wf093_build_a_branded_proposal_from_a_template as feature
    from dsr.quoting_proposals import rules

    assert set(feature.EXCEPTION_HANDLERS) == {
        rules.ProposalRefusal,
        rules.ProposalNotFound,
        rules.DocumentFrozen,
    }


def test_this_workflow_maps_no_error_type_another_feature_already_maps():
    from dsr.api import app
    from dsr.features import load_features

    registry = load_features(app)
    mine = {
        handler
        for feature in registry.features
        if feature.id == FEATURE_ID
        for handler in feature.exception_handlers
    }
    assert "ProposalRefusal" in mine
    assert "PrivacyRefusal" not in mine


# --------------------------------------------------------------------------- #
# The board and the research
# --------------------------------------------------------------------------- #


def test_the_summary_is_zero_on_an_empty_store(client: TestClient):
    body = client.get(f"{PREFIX}/summary").json()
    assert body["templates"] == 0
    assert body["documents"] == 0
    assert body["unresolved_bindings"] == 0


def test_the_summary_names_the_collections_it_reads(client: TestClient):
    collections = client.get(f"{PREFIX}/summary").json()["collections"]
    assert collections["quotes_read_only"] == vocab.QUOTES
    assert collections["line_items_read_only"] == vocab.LINE_ITEMS
    assert collections["templates"] == vocab.TEMPLATES


def test_the_vocabulary_serves_the_chosen_model_and_both_rejections(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    chosen = [row for row in body["document_models"] if row["chosen"]]
    assert len(chosen) == 1
    assert chosen[0]["id"] == vocab.DOCUMENT_MODEL_HTML_JSON
    rejected = [row for row in body["document_models"] if not row["chosen"]]
    assert len(rejected) == 2
    assert all(row["rejection"] for row in rejected)


def test_the_vocabulary_serves_every_module_and_binding_root(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert [row["id"] for row in body["modules"]] == list(vocab.MODULES)
    assert body["association_type_id"] == "286"


def test_the_decisions_route_serves_every_derivation(client: TestClient):
    body = client.get(f"{PREFIX}/decisions").json()
    assert body["count"] >= 8
    assert all(row["chosen"] for row in body["decisions"])


def test_one_decision_is_readable_by_id(client: TestClient):
    response = client.get(f"{PREFIX}/decisions/DERIVED_DOCUMENT_MODEL_IS_HTML_JSON")
    assert response.status_code == 200
    assert response.json()["chosen"] == "html_json_document_model"


def test_an_unknown_decision_id_is_a_404(client: TestClient):
    assert client.get(f"{PREFIX}/decisions/NOT_A_DECISION").status_code == 404


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #


def test_a_template_is_created_and_reported_as_created(client: TestClient):
    body = make_template(client)
    assert body["action"] == "created"
    assert body["key"] == "standard-proposal"
    assert body["rendered"]


def test_a_template_lists_and_reads_back(client: TestClient):
    created = make_template(client)
    listed = client.get(f"{PREFIX}/templates", params={"room_id": "room_a"}).json()
    assert listed["count"] == 1
    assert listed["templates"][0]["id"] == created["id"]
    read = client.get(f"{PREFIX}/templates/{created['id']}")
    assert read.status_code == 200
    assert read.json()["name"] == "Standard proposal"


def test_a_template_saved_twice_is_updated_not_duplicated(client: TestClient):
    first = make_template(client)
    second = make_template(client, terms="Net 45.")
    assert second["action"] == "updated"
    assert second["id"] == first["id"]
    assert second["revision"] == first["revision"] + 1
    assert client.get(f"{PREFIX}/templates", params={"room_id": "room_a"}).json()["count"] == 1


def test_a_template_with_no_name_is_a_400(client: TestClient):
    response = client.post(f"{PREFIX}/templates", json={})
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_proposal"
    assert "name" in body["errors"]


def test_a_template_binding_with_an_unreadable_root_is_a_400(client: TestClient):
    response = client.post(
        f"{PREFIX}/templates",
        json={"name": "Bad", "bindings": {"parties": [{"field": "buyer", "path": "x.y"}]}},
    )
    assert response.status_code == 400
    assert "bindings" in response.json()["errors"]


def test_a_template_hides_a_module_and_the_response_says_so(client: TestClient):
    body = make_template(client, hidden=[vocab.MODULE_ACCEPTANCE])
    assert vocab.MODULE_ACCEPTANCE in body["hidden"]
    assert vocab.MODULE_ACCEPTANCE not in body["rendered"]
    assert vocab.MODULE_HEADER in body["rendered"]


def test_a_template_reports_the_custom_module_limit_on_itself(client: TestClient):
    body = make_template(client, carry_custom_modules=True)
    assert body["carry_custom_modules"] is True
    assert body["custom_module_advisory"]["authored_via_api"] is False


def test_reading_an_unknown_template_is_a_404(client: TestClient):
    assert client.get(f"{PREFIX}/templates/wf093_template_missing").status_code == 404


def test_the_templates_list_carries_the_custom_module_advisory(client: TestClient):
    make_template(client)
    body = client.get(f"{PREFIX}/templates", params={"room_id": "room_a"}).json()
    assert body["custom_module_advisory"]["custom_modules"] == "select_only"


# --------------------------------------------------------------------------- #
# Brands
# --------------------------------------------------------------------------- #


def test_a_brand_is_created_with_normalised_tokens(client: TestClient):
    body = make_brand(client, accent="#10506F")
    assert body["action"] == "created"
    assert body["tokens"]["accent"]["value"] == "#10506f"


def test_a_brand_lists_and_reports_the_logo_precedence(client: TestClient):
    make_brand(client, accent="#10506f")
    body = client.get(f"{PREFIX}/brands", params={"room_id": "room_a"}).json()
    assert body["count"] == 1
    assert list(body["logo_sources"]) == list(vocab.LOGO_SOURCES)
    assert "account branding" in body["logo_source_note"]


def test_a_brand_with_a_colour_a_browser_cannot_draw_is_a_400(client: TestClient):
    response = client.post(f"{PREFIX}/brands", json={"name": "Bad", "accent": "navy"})
    assert response.status_code == 400
    assert "accent" in response.json()["errors"]


def test_a_brand_with_no_name_is_a_400(client: TestClient):
    assert client.post(f"{PREFIX}/brands", json={"accent": "#10506f"}).status_code == 400


# --------------------------------------------------------------------------- #
# The quotes this workflow reads
# --------------------------------------------------------------------------- #


def test_the_quotes_list_names_where_the_rows_come_from(client: TestClient):
    body = client.get(f"{PREFIX}/quotes").json()
    assert body["count"] == 0
    assert body["collection"] == vocab.QUOTES
    assert body["provisioned_by"] == "WF-086"
    assert body["read_only"] is True


def test_a_quote_is_read_with_its_line_items(client: TestClient):
    quote = make_quote(client)
    make_line_items(client, quote["id"], (100.0, 50.0))
    body = client.get(f"{PREFIX}/quotes/{quote['id']}").json()
    assert body["title"] == "Northwind renewal"
    assert body["line_item_count"] == 2
    assert body["line_item_collection"] == vocab.LINE_ITEMS
    assert body["read_only"] is True


def test_a_quote_reports_the_association_type_id(client: TestClient):
    """The evidence fixes it at 286, and the quote is where it is set."""

    body = client.get(f"{PREFIX}/quotes").json()
    assert body["association_type_id"] == vocab.ASSOCIATION_TYPE_ID


def test_reading_an_unknown_quote_is_a_404_naming_its_collection(client: TestClient):
    response = client.get(f"{PREFIX}/quotes/wf086_quote_missing")
    assert response.status_code == 404
    assert vocab.QUOTES in response.json()["detail"]


def test_no_route_of_this_feature_writes_a_quote(client: TestClient):
    """The read-only decision, asserted over HTTP rather than described.

    Every write route this router serves is exercised here, and the quote count is
    compared before and after. A route that created a quote would change it.
    """

    store = _store(client)
    template = make_template(client)
    quote = make_quote(client)

    before = len(store.list(vocab.QUOTES))
    instantiate(client, template["id"], quote["id"])
    client.post(f"{PREFIX}/brands", json={"name": "Another"})
    client.post(f"{PREFIX}/templates", json={"name": "Another"})
    client.get(f"{PREFIX}/quotes")
    client.get(f"{PREFIX}/quotes/{quote['id']}")
    assert len(store.list(vocab.QUOTES)) == before


# --------------------------------------------------------------------------- #
# The merge
# --------------------------------------------------------------------------- #


def test_a_preview_merges_without_writing_anything(client: TestClient):
    store = _store(client)
    template = make_template(client)
    quote = make_quote(client)
    make_line_items(client, quote["id"], (100.0,))
    before_documents = len(store.list(vocab.DOCUMENTS))
    before_audit = len(store.audit())

    response = client.get(
        f"{PREFIX}/preview",
        params={"template_id": template["id"], "quote_id": quote["id"]},
    )
    assert response.status_code == 200
    assert response.json()["modules"]
    assert len(store.list(vocab.DOCUMENTS)) == before_documents
    assert len(store.audit()) == before_audit


def test_a_preview_needs_both_ids(client: TestClient):
    """A merge with one input is not a merge, so the route says so rather than guessing."""

    assert client.get(f"{PREFIX}/preview", params={"quote_id": "x"}).status_code == 422


def test_a_preview_of_an_unknown_template_is_a_404(client: TestClient):
    quote = make_quote(client)
    response = client.get(
        f"{PREFIX}/preview",
        params={"template_id": "wf093_template_missing", "quote_id": quote["id"]},
    )
    assert response.status_code == 404


def test_instantiating_stores_the_document(client: TestClient):
    template = make_template(client)
    quote = make_quote(client)
    make_line_items(client, quote["id"], (100.0, 50.0))
    body = instantiate(client, template["id"], quote["id"])
    assert body["state"] == vocab.STATE_INSTANTIATED
    assert body["line_item_count"] == 2
    assert body["document_model"] == vocab.DOCUMENT_MODEL_HTML_JSON


def test_the_stored_document_is_readable_afterwards(client: TestClient):
    template = make_template(client)
    quote = make_quote(client)
    made = instantiate(client, template["id"], quote["id"])
    read = client.get(f"{PREFIX}/documents/{made['id']}")
    assert read.status_code == 200
    assert read.json()["id"] == made["id"]


def test_the_document_reports_the_precedence_the_merge_applied(client: TestClient):
    template = make_template(client)
    quote = make_quote(client)
    body = instantiate(client, template["id"], quote["id"])
    assert body[vocab.REPORT_PRECEDENCE] == vocab.PRECEDENCE_QUOTE_OVER_TEMPLATE


def test_the_document_reports_an_unresolved_binding_rather_than_failing(client: TestClient):
    template = make_template(
        client, bindings={"parties": [{"field": "buyer", "path": "company.no_such_field"}]}
    )
    quote = make_quote(client)
    body = instantiate(client, template["id"], quote["id"])
    assert body[vocab.REPORT_UNRESOLVED] == [
        {"module": vocab.MODULE_PARTIES, "field": "buyer", "path": "company.no_such_field"}
    ]


def test_the_document_reports_a_truncated_line_items_module(client: TestClient):
    template = make_template(client)
    quote = make_quote(client)
    make_line_items(client, quote["id"], tuple([10.0] * 120))
    body = instantiate(client, template["id"], quote["id"])
    assert body[vocab.REPORT_TRUNCATED]["dropped"] == 20
    assert body[vocab.REPORT_TRUNCATED]["cap"] == 100


def test_a_quote_property_overrides_the_template_on_the_document(client: TestClient):
    """The evidence: "properties set on the quote overriding the quote template's settings"."""

    template = make_template(
        client, bindings={"header": [{"field": "po_number", "path": "quote.po_number"}]}
    )
    quote = make_quote(client, po_number="PO-4417")
    body = instantiate(client, template["id"], quote["id"])
    header = next(m for m in body["modules"] if m["module"] == vocab.MODULE_HEADER)
    assert header["po_number"] == "PO-4417"
    assert header["fields"]["po_number"]["status"] == "resolved"


def test_the_brand_drives_the_logo_on_the_document(client: TestClient):
    brand = make_brand(client, logo_url="https://x.example/halcyon.png")
    template = make_template(client, brand_id=brand["id"])
    quote = make_quote(client)
    body = instantiate(client, template["id"], quote["id"])
    header = next(m for m in body["modules"] if m["module"] == vocab.MODULE_HEADER)
    assert header["logo_url"] == "https://x.example/halcyon.png"
    assert header[vocab.REPORT_LOGO_SOURCE] == vocab.LOGO_SOURCE_BRAND_KIT


def test_the_company_name_fallback_reports_that_it_applied(client: TestClient):
    brand = make_brand(
        client,
        company_name="Halcyon Cloud",
        **{vocab.FALLBACK_NAME_WHEN_LOGO_ABSENT: True},
    )
    template = make_template(
        client,
        brand_id=brand["id"],
        branding={vocab.FALLBACK_NAME_WHEN_LOGO_ABSENT: True},
    )
    quote = make_quote(client)
    body = instantiate(client, template["id"], quote["id"])
    header = next(m for m in body["modules"] if m["module"] == vocab.MODULE_HEADER)
    assert header["logo_url"] == "Halcyon Cloud"
    assert header["logo_fallback_applied"] is True


def test_the_brand_kit_colour_reaches_the_document(client: TestClient):
    brand = make_brand(client, accent="#10506f")
    template = make_template(client, brand_id=brand["id"])
    quote = make_quote(client)
    body = instantiate(client, template["id"], quote["id"])
    assert body["branding"]["tokens"]["accent"]["value"] == "#10506f"
    assert body["branding"]["brand_id"] == brand["id"]


def test_instantiating_an_unknown_quote_is_a_404(client: TestClient):
    template = make_template(client)
    response = client.post(
        f"{PREFIX}/documents",
        json={"template_id": template["id"], "quote_id": "wf086_quote_missing"},
    )
    assert response.status_code == 404
    assert vocab.QUOTES in response.json()["detail"]


def test_instantiating_an_unknown_template_is_a_404(client: TestClient):
    quote = make_quote(client)
    response = client.post(
        f"{PREFIX}/documents",
        json={"template_id": "wf093_template_missing", "quote_id": quote["id"]},
    )
    assert response.status_code == 404


def test_the_documents_list_carries_the_non_retroactive_quote(client: TestClient):
    body = client.get(f"{PREFIX}/documents").json()
    assert body["count"] == 0
    assert "won't update existing published quotes" in body["no_retroactive_application_quote"]
    assert {row["id"] for row in body["states"]} == set(vocab.DOCUMENT_STATES)


def test_reading_an_unknown_document_is_a_404(client: TestClient):
    assert client.get(f"{PREFIX}/documents/wf093_document_missing").status_code == 404


# --------------------------------------------------------------------------- #
# The non-retroactive rule
# --------------------------------------------------------------------------- #


def test_publishing_a_document_moves_it_and_records_when(client: TestClient):
    template = make_template(client)
    quote = make_quote(client)
    made = instantiate(client, template["id"], quote["id"])
    response = client.post(
        f"{PREFIX}/documents/{made['id']}/transition", json={"action": "publish"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == vocab.STATE_PUBLISHED
    assert body["published_at"]
    assert body["retroactivity"]["rerenderable"] is False


def test_a_published_document_refuses_a_re_render_with_a_403(client: TestClient):
    """The refusal carries the remediation, because a 403 without one is a support ticket."""

    template = make_template(client)
    quote = make_quote(client)
    made = instantiate(client, template["id"], quote["id"])
    client.post(f"{PREFIX}/documents/{made['id']}/transition", json={"action": "publish"})

    response = client.post(
        f"{PREFIX}/documents/{made['id']}/transition", json={"action": "rerender"}
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"] == "document_frozen"
    assert body["state"] == vocab.STATE_PUBLISHED
    assert "Issue the quote again" in body["remediation"]
    assert "won't update existing published quotes" in body["evidence"]


def test_an_unpublished_document_re_renders_into_a_new_one(client: TestClient):
    template = make_template(client)
    quote = make_quote(client)
    made = instantiate(client, template["id"], quote["id"])
    response = client.post(
        f"{PREFIX}/documents/{made['id']}/transition", json={"action": "rerender"}
    )
    assert response.status_code == 200
    assert response.json()["id"] != made["id"]


def test_a_brand_change_does_not_alter_a_published_document(client: TestClient):
    """The rule is true of this store because the document holds a snapshot."""

    brand = make_brand(client, accent="#10506f")
    template = make_template(client, brand_id=brand["id"])
    quote = make_quote(client)
    made = instantiate(client, template["id"], quote["id"])
    client.post(f"{PREFIX}/documents/{made['id']}/transition", json={"action": "publish"})

    client.post(
        f"{PREFIX}/brands",
        json={"name": "Halcyon Cloud", "accent": "#0c1620"},
        params={"brand_id": brand["id"]},
    )

    reread = client.get(f"{PREFIX}/documents/{made['id']}").json()
    assert reread["branding"]["tokens"]["accent"]["value"] == "#10506f"


def test_an_unknown_move_is_a_400_naming_where_the_document_is(client: TestClient):
    template = make_template(client)
    quote = make_quote(client)
    made = instantiate(client, template["id"], quote["id"])
    response = client.post(
        f"{PREFIX}/documents/{made['id']}/transition", json={"action": "archive"}
    )
    assert response.status_code == 400
    assert "instantiated" in response.json()["detail"]


def test_transitioning_an_unknown_document_is_a_404(client: TestClient):
    response = client.post(
        f"{PREFIX}/documents/wf093_document_missing/transition", json={"action": "publish"}
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def test_every_source_this_router_can_record_names_a_mounted_route(client: TestClient):
    """No hand-written source string can survive this.

    A domain function that hardcodes a URL leaves the audit log naming a route the app
    stopped serving. Every source below is built by ``_source`` from the router, and the
    check is on the *produced* value: whatever route a write names is asserted to be a
    concrete route the host mounted.
    """

    from dsr.api import app
    from dsr.features import (
        load_features,
        wf093_build_a_branded_proposal_from_a_template as feature,
    )

    registry = load_features(app)
    mounted = {
        (method, shape["path"])
        for shape in registry.by_id(FEATURE_ID).routes
        for method in shape["methods"]
    }

    # `_source` derives from the router rather than carrying a literal, so no route string
    # in this module can drift from the prefix the host mounted.
    assert "router.prefix" in inspect.getsource(feature._source)

    # Every route this workflow writes through, and the source it therefore records.
    for method, path in (
        ("POST", "/templates"),
        ("POST", "/brands"),
        ("POST", "/documents"),
        ("POST", "/documents/{document_id}/transition"),
    ):
        source = feature._source(method, path)
        assert source == f"{method} {PREFIX}{path}"
        assert (method, f"{PREFIX}{path}") in mounted, (method, path)


def test_a_write_records_an_audit_row_naming_the_route_that_served_it(client: TestClient):
    store = _store(client)
    template = make_template(client)
    quote = make_quote(client)
    made = instantiate(client, template["id"], quote["id"])

    row = store.audit(collection=vocab.DOCUMENTS, limit=1)[0]
    assert row["source"] == "POST /api/wf-093/documents"
    assert row["record_id"] == made["id"]
    assert row["action"] == "insert"


def test_a_transition_records_an_audit_row(client: TestClient):
    store = _store(client)
    template = make_template(client)
    quote = make_quote(client)
    made = instantiate(client, template["id"], quote["id"])
    client.post(f"{PREFIX}/documents/{made['id']}/transition", json={"action": "publish"})

    row = store.audit(collection=vocab.DOCUMENTS, action="update", limit=1)[0]
    assert row["source"] == "POST /api/wf-093/documents/{document_id}/transition"
    assert row["record_id"] == made["id"]


def test_a_brand_write_records_the_brands_route(client: TestClient):
    store = _store(client)
    make_brand(client, accent="#10506f")
    row = store.audit(collection=vocab.BRANDS, limit=1)[0]
    assert row["source"] == "POST /api/wf-093/brands"


def test_a_template_write_records_the_templates_route(client: TestClient):
    store = _store(client)
    make_template(client)
    row = store.audit(collection=vocab.TEMPLATES, limit=1)[0]
    assert row["source"] == "POST /api/wf-093/templates"


# --------------------------------------------------------------------------- #
# The read-path rule
# --------------------------------------------------------------------------- #


def test_the_reads_write_no_audit_rows(client: TestClient):
    """A route that logged every read would fill the guarantee with entries describing no change."""

    store = _store(client)
    template = make_template(client)
    quote = make_quote(client)
    make_line_items(client, quote["id"], (100.0,))
    instantiate(client, template["id"], quote["id"])

    before = len(store.audit())
    for path in (
        "/summary",
        "/vocabulary",
        "/decisions",
        "/decisions/DERIVED_DOCUMENT_MODEL_IS_HTML_JSON",
        "/templates",
        "/brands",
        "/quotes",
        f"/quotes/{quote['id']}",
        "/documents",
    ):
        assert client.get(f"{PREFIX}{path}").status_code == 200, path
    assert client.get(f"{PREFIX}/templates/{template['id']}").status_code == 200
    assert (
        client.get(
            f"{PREFIX}/preview",
            params={"template_id": template["id"], "quote_id": quote["id"]},
        ).status_code
        == 200
    )
    assert len(store.audit()) == before


# --------------------------------------------------------------------------- #
# The isolation rule, over HTTP
# --------------------------------------------------------------------------- #


def test_this_feature_writes_only_its_own_collections_and_the_fixture_ones(
    client: TestClient,
):
    """Every write this router performs lands in a collection this workflow owns.

    The quote and line-item collections are written by the fixture on purpose, because
    WF-086 provisions them and this workflow reads them. No route of this feature writes
    either, which ``test_no_route_of_this_feature_writes_a_quote`` asserts separately.
    """

    store = _store(client)
    owned = {vocab.TEMPLATES, vocab.BRANDS, vocab.DOCUMENTS}
    template = make_template(client)
    quote = make_quote(client)
    instantiate(client, template["id"], quote["id"])

    written = {
        row["collection"]
        for row in store.audit(limit=500)
        if row["collection"] and row["source"] and PREFIX in row["source"]
    }
    assert written <= owned


def test_the_seed_runs_and_its_message_is_encodable_by_cp1252(client: TestClient):
    """The seeder prints this string to a Windows console."""

    from dsr.db.audited import AuditedDatabase
    from dsr.features import wf093_build_a_branded_proposal_from_a_template as feature

    database = AuditedDatabase(":memory:", actor="test")
    try:
        store = _store(client)
        room = store.create("room", {"name": "Demo"}, actor="test", source="test")
        message = feature.seed(
            database, {"room_ids": [(room["id"], "Demo")], "now": NOW, "rng": None}
        )
    finally:
        database.close()

    assert message
    assert message.encode("cp1252")
    print(message)


def test_the_seed_without_a_room_returns_an_empty_string_rather_than_raising():
    from dsr.db.audited import AuditedDatabase
    from dsr.features import wf093_build_a_branded_proposal_from_a_template as feature

    database = AuditedDatabase(":memory:", actor="test")
    try:
        assert feature.seed(database, {"room_ids": [], "now": NOW, "rng": None}) == ""
    finally:
        database.close()
