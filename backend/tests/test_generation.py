"""Tests for WF-012: generate a personalised room programmatically.

The workflow's promises are specific enough to be worth pinning down one by
one: a template is a shell and never a room, publication is opt-in, the expiry
clock starts at publication and not before, caller-supplied ids are unique, a
batch is one transaction, and nothing is validated against a declared schema.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.generation import (
    GenerationConflict,
    GenerationError,
    UnknownTemplate,
    derive_state,
    normalise_expiry,
    normalise_key,
    render,
    render_text,
)

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def client(monkeypatch):
    import dsr.api as api_module

    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "api.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr(api_module, "FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


TEMPLATE = {
    "name": "Enterprise Evaluation",
    "description": "Standard enterprise evaluation room",
    "variables": [
        {"key": "hello_world", "label": "Hello World", "type": "text", "required": True},
        {"key": "seats", "label": "Seats", "type": "number"},
        {"key": "line_items", "label": "Line Items", "type": "repeat"},
    ],
    "blocks": [
        {"id": "hero", "kind": "heading", "text": "Welcome to {{hello_world}}"},
        {"id": "intro", "kind": "text", "text": "Prepared for {{hello_world}} in {{region}}."},
        {
            "id": "capacity",
            "kind": "text",
            "text": "Licensed seats: {{seats}}",
            # A block-level pin. Per the source, this overwrites the page-level
            # value for this block only.
            "substitutions": {"seats": "to be confirmed"},
        },
        {
            "id": "quote",
            "kind": "line_items",
            "repeat": "line_items",
            "item": "{{item.description}} x{{item.quantity}} @ {{item.unit_price}}",
        },
        {"id": "footer", "kind": "text", "text": "Confidential. Ref {{reference}}."},
    ],
}


@pytest.fixture()
def template_id(client):
    response = client.post("/api/templates", json=TEMPLATE)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def generate(client, **overrides):
    payload = {
        "template_id": TEMPLATE["name"] and overrides.pop("tid", None),
        "name": "Acme — Enterprise Evaluation",
        "substitutions": {
            "hello_world": "Acme",
            "region": "EMEA",
            "line_items": [
                {"description": "Platform licence", "quantity": 40, "unit_price": "1200.00"},
                {"description": "Onboarding", "quantity": 1, "unit_price": "4500.00"},
            ],
            "reference": "NSQ-8842",
        },
    }
    payload["template_id"] = overrides.pop("tid", None) or payload["template_id"]
    payload.update(overrides)
    return client.post("/api/generations", json=payload)


# --------------------------------------------------------------------------- #
# The substitution engine (pure)
# --------------------------------------------------------------------------- #


def test_scalar_variables_are_substituted():
    rendered = render(TEMPLATE, {"hello_world": "Acme", "region": "EMEA"})

    assert rendered.content[0]["lines"] == ["Welcome to Acme"]
    assert rendered.content[1]["lines"] == ["Prepared for Acme in EMEA."]


def test_missing_variable_is_marked_not_silently_blanked():
    """The failure mode this guards: a half-personalised room reaching a buyer."""
    rendered = render(TEMPLATE, {"region": "EMEA"})

    hero = rendered.content[0]["lines"][0]
    assert hero == "Welcome to [unresolved: hello_world]"
    assert "hello_world" in rendered.unresolved
    assert not rendered.clean


def test_missing_variable_is_reported_once_per_key():
    template = {"blocks": [{"id": "a", "text": "{{x}} and {{x}}"}]}

    rendered = render(template, {})

    assert rendered.unresolved == ["x"]


def test_block_level_substitutions_override_page_level():
    """[sourced] Page values "can be overwritten if the same keys are defined in
    the block-level substitutions", confirmed with Jev (block_level_wins, 0.97)."""
    rendered = render(TEMPLATE, {"seats": 40})

    capacity = next(b for b in rendered.content if b["id"] == "capacity")
    assert capacity["lines"] == ["Licensed seats: to be confirmed"]


def test_repeating_key_renders_one_line_per_element():
    rendered = render(
        TEMPLATE,
        {
            "line_items": [
                {"description": "Platform", "quantity": 40, "unit_price": "1200.00"},
                {"description": "Onboarding", "quantity": 1, "unit_price": "4500.00"},
            ]
        },
    )

    quote = next(b for b in rendered.content if b["id"] == "quote")
    assert quote["lines"] == ["Platform x40 @ 1200.00", "Onboarding x1 @ 4500.00"]


def test_repeating_key_with_no_value_renders_a_placeholder_and_reports_it():
    """[inference] An empty section reads as "nothing here"; a marker reads as
    "we forgot to pass this"."""
    rendered = render(TEMPLATE, {})

    quote = next(b for b in rendered.content if b["id"] == "quote")
    assert len(quote["lines"]) == 1
    assert "unresolved" in quote["lines"][0]
    assert "line_items" in rendered.unresolved


def test_repeating_key_of_the_wrong_type_reports_a_problem():
    rendered = render(TEMPLATE, {"line_items": "not-a-list"})

    codes = [p["code"] for p in rendered.problems]
    assert "repeat_type" in codes


def test_a_missing_item_property_does_not_list_an_unsuppliable_key():
    """`item.unit_price` is not a key the caller can pass, so listing it as
    unresolved would send them looking for a substitution that cannot exist."""
    rendered = render(TEMPLATE, {"line_items": [{"description": "Platform", "quantity": 40}]})

    quote = next(b for b in rendered.content if b["id"] == "quote")
    assert quote["lines"] == ["Platform x40 @ [unresolved: item.unit_price]"]
    assert not [k for k in rendered.unresolved if k.startswith("item.")]


def test_a_page_level_key_used_inside_an_item_template_is_still_reported():
    template = {
        "blocks": [
            {"id": "rows", "repeat": "items", "item": "{{item.name}} for {{currency}}"}
        ]
    }

    rendered = render(template, {"items": [{"name": "Licence"}]})

    assert rendered.unresolved == ["currency"]


def test_repeat_without_an_item_template_is_reported():
    template = {"blocks": [{"id": "rows", "repeat": "items"}]}

    rendered = render(template, {"items": [{"a": 1}]})

    assert "repeat_without_item" in [p["code"] for p in rendered.problems]


def test_dotted_reference_resolves_a_nested_value():
    text, missing = render_text("Region {{ account.region }}", {"account": {"region": "EMEA"}})

    assert text == "Region EMEA"
    assert missing == []


def test_unused_substitutions_are_reported_not_rejected():
    rendered = render(TEMPLATE, {"hello_world": "Acme", "internal_cost_centre": "CC-119"})

    assert rendered.unused == ["internal_cost_centre"]


def test_declared_variables_are_advisory():
    """[inference] A template's `variables` list must not be a validation gate,
    or adding a field would need coordination with whoever owns the template."""
    template = {"variables": [], "blocks": [{"id": "a", "text": "Seats {{seats}}"}]}

    rendered = render(template, {"seats": 12})

    assert rendered.content[0]["lines"] == ["Seats 12"]
    assert rendered.unresolved == []


def test_template_with_no_blocks_is_rejected():
    with pytest.raises(GenerationError, match="no blocks"):
        render({"blocks": []}, {})


def test_render_text_leaves_plain_text_untouched():
    assert render_text("no tokens here", {}) == ("no tokens here", [])


def test_values_are_stringified_not_dropped():
    text, _ = render_text("{{n}}/{{b}}/{{missing}}", {"n": 40, "b": True, "missing": None})

    assert text == "40/true/[unresolved: missing]"


# --------------------------------------------------------------------------- #
# Publication state (pure)
# --------------------------------------------------------------------------- #


def _now():
    return datetime.now(timezone.utc)


def test_draft_state_has_no_publication_or_deadline():
    state = derive_state({"published": False, "expiry": {"enabled": True, "days": 30}})

    assert state["status"] == "draft"
    assert state["published_at"] is None
    assert state["expires_at"] is None
    # The setting is retained, not discarded: it starts when the room publishes.
    assert state["expiry"] == {"enabled": True, "days": 30}


def test_published_state_derives_the_deadline_from_publication():
    started = _now()
    state = derive_state(
        {"published": True, "published_at": started.isoformat(), "expiry": {"enabled": True, "days": 30}}
    )

    assert state["status"] == "published"
    assert state["expires_at"] is not None
    assert state["expires_at"] > started.isoformat()


def test_room_past_its_expiry_reads_as_declined():
    started = _now() - timedelta(days=31)
    state = derive_state(
        {"published": True, "published_at": started.isoformat(), "expiry": {"enabled": True, "days": 30}}
    )

    assert state["status"] == "declined"
    assert state["expires_at"] is not None


def test_room_within_its_expiry_still_reads_as_published():
    started = _now() - timedelta(days=29)
    state = derive_state(
        {"published": True, "published_at": started.isoformat(), "expiry": {"enabled": True, "days": 30}}
    )

    assert state["status"] == "published"


def test_expiry_requires_a_positive_day_count():
    with pytest.raises(GenerationError, match="greater than zero"):
        normalise_expiry({"enabled": True, "days": 0})


def test_expiry_enabled_without_days_is_rejected():
    with pytest.raises(GenerationError, match="days is required"):
        normalise_expiry({"enabled": True})


def test_expiry_records_when_the_count_starts():
    # [sourced] The day count starts when the page is published, not before.
    assert normalise_expiry({"days": 14}) == {"enabled": True, "days": 14, "starts_on": "publish"}


def test_reference_key_is_derived_from_a_label():
    """[sourced] The editor label is `Hello World`, the API key is `hello_world`."""
    assert normalise_key("Hello World") == "hello_world"
    assert normalise_key("  Account  Name! ") == "account_name"


# --------------------------------------------------------------------------- #
# Declaring a template
# --------------------------------------------------------------------------- #


def test_template_is_stored_as_an_ordinary_audited_record(client):
    client.post("/api/templates", json=TEMPLATE)

    record = client.get("/api/records/template").json()["records"][0]
    assert record["data"]["name"] == "Enterprise Evaluation"
    audit = client.get("/api/audit", params={"collection": "template"}).json()
    assert audit["count"] == 1


def test_template_accepts_fields_nothing_declares(client):
    """A team adds a field; no migration, no redeploy."""
    payload = {**TEMPLATE, "review_cadence": "quarterly", "compliance": {"soc2": True}}

    created = client.post("/api/templates", json=payload).json()

    assert created["data"]["review_cadence"] == "quarterly"
    assert created["data"]["compliance"] == {"soc2": True}


def test_template_requires_blocks(client):
    response = client.post("/api/templates", json={"name": "Empty"})

    assert response.status_code == 400
    assert "blocks" in response.json()["detail"]


def test_template_rejects_a_block_that_is_not_an_object(client):
    """Silently skipping it would produce a room with a blank section the buyer sees."""
    response = client.post(
        "/api/templates", json={"name": "Broken", "blocks": [{"id": "a", "text": "x"}, "oops"]}
    )

    assert response.status_code == 400
    assert "blocks[1]" in response.json()["detail"]


def test_generating_from_a_blockless_template_written_directly_is_400(client):
    """The generic records API can write a template without going through
    declare, so the generation path cannot assume the shape was checked."""
    shell = client.post(
        "/api/records/template", json={"name": "Shell", "blocks": []}
    ).json()

    response = client.post(
        "/api/generations", json={"template_id": shell["id"], "name": "X"}
    )

    assert response.status_code == 400
    assert "no blocks" in response.json()["detail"]


def test_template_can_be_updated_in_place(client, template_id):
    updated = client.post(
        "/api/templates", json={"template_id": template_id, **TEMPLATE, "name": "Renamed"}
    )

    assert updated.status_code == 200
    assert updated.json()["data"]["name"] == "Renamed"
    assert updated.json()["revision"] == 2


def test_generating_from_an_unknown_template_is_404(client):
    response = client.post("/api/generations", json={"template_id": "template_nope", "name": "X"})

    assert response.status_code == 404
    assert response.json()["error"] == "unknown_template"


# --------------------------------------------------------------------------- #
# Preview
# --------------------------------------------------------------------------- #


def test_preview_returns_the_buyer_visible_content(client, template_id):
    response = client.post(
        "/api/templates/preview",
        json={
            "template_id": template_id,
            "name": "Acme",
            "substitutions": {"hello_world": "Acme", "region": "EMEA", "reference": "NSQ-1"},
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["content"][0]["lines"] == ["Welcome to Acme"]
    assert body["unresolved_variables"] == ["line_items"]


def test_preview_writes_nothing(client, template_id):
    client.post(
        "/api/templates/preview", json={"template_id": template_id, "name": "Acme", "substitutions": {}}
    )

    assert client.get("/api/records/room").json()["count"] == 0
    assert client.get("/api/audit", params={"collection": "room"}).json()["count"] == 0


def test_preview_shows_the_deadline_the_room_would_get(client, template_id):
    body = client.post(
        "/api/templates/preview",
        json={"template_id": template_id, "name": "Acme", "expiry": {"days": 30}},
    ).json()

    assert body["if_published_now"]["expires_at"] is not None


# --------------------------------------------------------------------------- #
# Generating one room
# --------------------------------------------------------------------------- #


def test_generation_produces_an_audited_personalised_room(client, template_id):
    response = generate(client, tid=template_id, account="Acme", owner_id="dana", tags=["enterprise", "q3"])

    assert response.status_code == 201
    body = response.json()
    assert body["collection"] == "room"
    assert body["data"]["template_id"] == template_id
    assert body["data"]["content"][0]["lines"] == ["Welcome to Acme"]
    assert body["data"]["account"] == "Acme"
    assert body["data"]["owner_id"] == "dana"
    # Case-sensitive, stored verbatim.
    assert body["data"]["tags"] == ["enterprise", "q3"]

    audit = client.get("/api/audit", params={"record_id": body["id"]}).json()
    assert audit["count"] == 1
    assert audit["entries"][0]["action"] == "insert"


def test_generation_defaults_to_a_draft(client, template_id):
    """[sourced] `published` defaults to false. Nothing is published implicitly."""
    body = generate(client, tid=template_id).json()

    assert body["data"]["published"] is False
    assert body["generation"]["status"] == "draft"
    assert body["generation"]["published_at"] is None


def test_generation_can_publish_immediately(client, template_id):
    body = generate(client, tid=template_id, published=True).json()

    assert body["generation"]["status"] == "published"
    assert body["generation"]["published_at"] is not None


def test_published_string_false_is_not_truthy(client, template_id):
    """A room published because someone posted "false" is a room nobody meant to send."""
    body = generate(client, tid=template_id, published="false").json()

    assert body["data"]["published"] is False


def test_generation_records_template_provenance(client, template_id):
    body = generate(client, tid=template_id).json()

    assert body["data"]["template_revision"] == 1
    assert body["data"]["template_name"] == "Enterprise Evaluation"


def test_generation_reports_what_it_could_not_resolve(client, template_id):
    body = generate(
        client,
        tid=template_id,
        substitutions={"hello_world": "Acme", "region": "EMEA", "reference": "R1"},
    ).json()

    assert body["data"]["unresolved_variables"] == ["line_items"]


def test_metadata_is_stored_verbatim(client, template_id):
    metadata = {"crm_opportunity": "0061b0001", "crm_system": "salesforce", "amount": 48000}

    body = generate(client, tid=template_id, metadata=metadata).json()

    assert body["data"]["metadata"] == metadata


def test_generation_keeps_fields_the_template_never_declared(client, template_id):
    body = generate(
        client,
        tid=template_id,
        substitutions={"hello_world": "Acme", "region": "EMEA", "reference": "R1", "extra_field": "kept"},
    ).json()

    assert body["data"]["substitutions"]["extra_field"] == "kept"
    assert "extra_field" in body["data"]["unused_substitutions"]


def test_missing_name_is_400(client, template_id):
    response = client.post("/api/generations", json={"template_id": template_id})

    assert response.status_code == 400
    assert "name" in response.json()["detail"]


def test_non_object_substitutions_is_400(client, template_id):
    response = client.post(
        "/api/generations", json={"template_id": template_id, "name": "X", "substitutions": ["a"]}
    )

    assert response.status_code == 400
    assert "substitutions must be an object" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# Caller-defined ids
# --------------------------------------------------------------------------- #


def test_caller_supplied_external_id_is_stored(client, template_id):
    body = generate(client, tid=template_id, external_id="sf-op-0001").json()

    assert body["data"]["external_id"] == "sf-op-0001"


def test_external_id_must_be_unique(client, template_id):
    generate(client, tid=template_id, external_id="sf-op-0001")

    response = generate(client, tid=template_id, external_id="sf-op-0001")

    assert response.status_code == 409
    assert "delete that room to reuse the id" in response.json()["detail"]


def test_deleting_a_room_frees_its_external_id(client, template_id):
    """[sourced] The vendor's rule: to use a different id, delete and recreate."""
    first = generate(client, tid=template_id, external_id="sf-op-0001").json()
    client.delete(f"/api/records/room/{first['id']}")

    second = generate(client, tid=template_id, external_id="sf-op-0001")

    assert second.status_code == 201
    assert second.json()["data"]["external_id"] == "sf-op-0001"


def test_generated_rooms_are_findable_by_external_id(client, template_id):
    generate(client, tid=template_id, external_id="sf-op-0001")

    found = client.get("/api/generations", params={"where": '{"external_id":"sf-op-0001"}'}).json()

    assert found["count"] == 1
    assert found["rooms"][0]["data"]["name"] == "Acme — Enterprise Evaluation"


# --------------------------------------------------------------------------- #
# Batch generation
# --------------------------------------------------------------------------- #


def _item(template_id, name, **extra):
    return {
        "template_id": template_id,
        "name": name,
        "substitutions": {"hello_world": name, "region": "EMEA", "reference": "R1"},
        **extra,
    }


def test_batch_is_one_transaction_and_one_audit_entry(client, template_id):
    items = [_item(template_id, f"Room {i}") for i in range(3)]

    response = client.post("/api/generations/bulk", json=items)

    assert response.status_code == 201
    assert response.json()["count"] == 3
    assert client.get("/api/audit", params={"collection": "room"}).json()["count"] == 1


def test_batch_is_all_or_nothing(client, template_id):
    """A bad item in position 2 must not leave items 0 and 1 committed."""
    items = [
        _item(template_id, "Good 1"),
        {"template_id": template_id},  # no name
        _item(template_id, "Good 3"),
    ]

    response = client.post("/api/generations/bulk", json=items)

    assert response.status_code == 400
    assert "items[1]" in response.json()["detail"]
    assert client.get("/api/records/room").json()["count"] == 0


def test_batch_rejects_a_duplicate_external_id_inside_the_payload(client, template_id):
    items = [
        _item(template_id, "A", external_id="dup"),
        _item(template_id, "B", external_id="dup"),
    ]

    response = client.post("/api/generations/bulk", json=items)

    assert response.status_code == 409
    assert "twice in this batch" in response.json()["detail"]
    assert client.get("/api/records/room").json()["count"] == 0


def test_batch_rolls_back_when_a_later_item_clashes_with_an_existing_id(client, template_id):
    generate(client, tid=template_id, external_id="taken")
    items = [
        _item(template_id, "Free", external_id="free-id"),
        _item(template_id, "Clashing", external_id="taken"),
    ]

    response = client.post("/api/generations/bulk", json=items)

    assert response.status_code == 409
    assert client.get("/api/records/room").json()["count"] == 1


def test_batch_rejects_more_than_the_documented_ceiling(client, template_id):
    from dsr.generation import MAX_BATCH

    ok = client.post(
        "/api/generations/bulk", json=[_item(template_id, f"R{i}") for i in range(MAX_BATCH)]
    )
    assert ok.status_code == 201

    too_many = client.post(
        "/api/generations/bulk", json=[_item(template_id, f"R{i}") for i in range(MAX_BATCH + 1)]
    )
    assert too_many.status_code == 400
    assert f"at most {MAX_BATCH}" in too_many.json()["detail"]


def test_empty_batch_is_400(client, template_id):
    assert client.post("/api/generations/bulk", json=[]).status_code == 400


# --------------------------------------------------------------------------- #
# Publishing
# --------------------------------------------------------------------------- #


def test_publishing_starts_the_expiry_clock(client, template_id):
    """[sourced] A draft keeps the expiry setting; the count starts at publish."""
    body = generate(client, tid=template_id, expiry={"days": 30}).json()
    assert body["generation"]["expires_at"] is None
    assert body["data"]["expiry"] == {"enabled": True, "days": 30, "starts_on": "publish"}

    published = client.post(f"/api/generations/{body['id']}/publish")

    assert published.status_code == 200
    body = published.json()
    state = body["generation"]
    assert state["status"] == "published"
    assert state["expires_at"] is not None
    # The deadline is counted from publication, not from creation: the room sat
    # as a draft, and the deadline sits 30 days after the publish instant.
    assert state["expires_at"] > state["published_at"]
    assert state["published_at"] >= body["created_at"]


def test_publishing_is_audited(client, template_id):
    body = generate(client, tid=template_id).json()

    client.post(f"/api/generations/{body['id']}/publish", params={"actor": "dana"})

    actions = [e["action"] for e in client.get("/api/audit", params={"record_id": body["id"]}).json()["entries"]]
    assert actions == ["update", "insert"]


def test_publishing_twice_is_409(client, template_id):
    body = generate(client, tid=template_id, published=True).json()

    response = client.post(f"/api/generations/{body['id']}/publish")

    assert response.status_code == 409
    assert "already published" in response.json()["detail"]


def test_publishing_a_plain_room_is_409(client):
    plain = client.post("/api/records/room", json={"name": "Hand-made"}).json()

    response = client.post(f"/api/generations/{plain['id']}/publish")

    assert response.status_code == 409
    assert "not generated from a template" in response.json()["detail"]


def test_publishing_an_unknown_room_is_404(client):
    assert client.post("/api/generations/room_nope/publish").status_code == 404


# --------------------------------------------------------------------------- #
# Reading generated rooms
# --------------------------------------------------------------------------- #


def test_read_returns_stored_data_and_derived_state_separately(client, template_id):
    body = generate(client, tid=template_id, published=True, expiry={"days": 7}).json()

    read = client.get(f"/api/generations/{body['id']}").json()

    # Stored facts live in data...
    assert read["data"]["published"] is True
    # ...and only what is computed on read lives in generation.
    assert set(read["generation"]) == {"published", "status", "published_at", "expires_at", "expiry"}
    assert "status" not in read["data"]


def test_listing_generations_excludes_plain_rooms(client, template_id):
    client.post("/api/records/room", json={"name": "Hand-made"})
    generate(client, tid=template_id)

    listed = client.get("/api/generations").json()

    assert listed["count"] == 1
    assert listed["rooms"][0]["data"]["name"] == "Acme — Enterprise Evaluation"


def test_generations_can_be_filtered_on_any_json_path(client, template_id):
    generate(client, tid=template_id, published=True, owner_id="dana")
    generate(client, tid=template_id, published=False, owner_id="sam")

    published = client.get("/api/generations", params={"where": '{"published":true}'}).json()
    by_owner = client.get("/api/generations", params={"where": "owner_id=dana"}).json()

    assert published["count"] == 1
    assert by_owner["count"] == 1


def test_reading_a_plain_room_as_a_generation_is_409(client):
    plain = client.post("/api/records/room", json={"name": "Hand-made"}).json()

    response = client.get(f"/api/generations/{plain['id']}")

    assert response.status_code == 409


# --------------------------------------------------------------------------- #
# The audit guarantee, end to end
# --------------------------------------------------------------------------- #


def test_every_generation_step_is_audited_and_reads_are_not(client, template_id):
    body = generate(client, tid=template_id).json()
    client.get(f"/api/generations/{body['id']}")
    client.get("/api/generations")
    client.post(f"/api/generations/{body['id']}/publish")

    entries = client.get("/api/audit", params={"record_id": body["id"]}).json()["entries"]
    assert [e["action"] for e in entries] == ["update", "insert"]


def test_generation_is_visible_to_the_generic_records_api(client, template_id):
    """The workflow adds invariants; it does not fork the data out of reach."""
    body = generate(client, tid=template_id).json()

    fetched = client.get(f"/api/records/room/{body['id']}").json()

    assert fetched["data"]["content"][0]["lines"] == ["Welcome to Acme"]


def test_stats_count_generated_rooms(client, template_id):
    generate(client, tid=template_id)

    assert client.get("/api/stats").json()["by_collection"]["room"] == 1
