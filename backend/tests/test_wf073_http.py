"""WF-073 over HTTP: the surface this feature's own router serves.

The domain rules are in ``test_wf073.py``. This file is the other half, and it is
organised by what a caller can observe:

``the route table``
    That the host mounted every route by discovery alone, with no shared file edited,
    and that no two of them collide.
``the seller surface``
    Creating a governed link, reading it back, and toggling either flag in place. The
    tri-state is the point: ``{}`` leaves a flag alone, ``true`` sets it, and ``null``
    returns it to the documented default.
``the buyer's surface``
    Resolving a page against a viewport, and reporting a capture attempt. Neither ever
    refuses a viewer, because neither is a gate.
``the error shapes``
    Every status code and body this router can produce, including the four the domain
    raises and the 404s the store would otherwise leak as a 500.
``the audit-source rule``
    Every ``source=`` this workflow records names a route the host actually mounted.
    This is the test the build brief asks for by name.
``the honesty rule``
    Every response carries the effect and the limitation, so no caller can read a
    confidentiality control without also reading what it is worth.
"""

from __future__ import annotations

import ast
import importlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import dsr.features as host
from dsr.security_governance import rules, vocabulary as vocab
from dsr.store import RecordStore
from fastapi.testclient import TestClient

FEATURE_MODULE = "dsr.features.wf073_apply_confidential_view_and_block_screenshot"
PREFIX = "/api/wf-073"
FEATURE_ID = "wf-073-apply-confidential-view-and-block-screenshot"

NOW = datetime(2026, 10, 3, 14, 0, tzinfo=timezone.utc)


def create_room(client: TestClient, room_id: str = "room_a") -> str:
    """One room to hang a governed link on.

    Created through the core API rather than straight into the store, so the room is a
    row the application itself would have written. A test that faked it could pass
    against a link whose room does not exist.
    """

    response = client.post(
        "/api/records/room",
        json={"name": f"Room {room_id}"},
        params={"record_id": room_id},
    )
    assert response.status_code in (200, 201), response.text
    return room_id


def make_link(client: TestClient, **flags: Any) -> dict[str, Any]:
    """Create a governed link over HTTP and return the created record."""

    room_id = create_room(client)
    response = client.post(
        f"{PREFIX}/rooms/{room_id}/links", json={"title": "Governed link", **flags}
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_preset(client: TestClient, **fields: Any) -> dict[str, Any]:
    response = client.post(f"{PREFIX}/presets", json={"name": "Baseline", "fields": fields})
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# the route table
# --------------------------------------------------------------------------- #


class TestRouteTable:
    def test_the_feature_is_installed_with_its_routes(self, client: TestClient):
        payload = client.get("/api/features").json()
        installed = next(f for f in payload["features"] if f["id"] == FEATURE_ID)
        paths = {route["path"] for route in installed["routes"]}
        assert f"{PREFIX}/summary" in paths
        assert f"{PREFIX}/links/{{link_id}}" in paths

    def test_the_feature_did_not_fail_to_load(self, client: TestClient):
        payload = client.get("/api/features").json()
        assert not [f for f in payload["failed"] if FEATURE_ID in str(f)]

    def test_the_host_mounted_it_without_a_shared_file_being_edited(self):
        """``backend/dsr/api.py`` discovers and mounts every feature router. This test
        states the claim by name so a reviewer can check it against the diff."""
        module = importlib.import_module(FEATURE_MODULE)
        record = host.REGISTRY.by_id(FEATURE_ID)
        assert record is not None
        assert module.router.prefix == PREFIX

    def test_every_route_is_reachable_and_none_collide(self):
        mounted = {
            f"{method} {route.path}"
            for route in importlib.import_module(FEATURE_MODULE).router.routes
            for method in route.methods
            if method not in ("HEAD", "OPTIONS")
        }
        assert len(mounted) == len(set(mounted))
        assert len(mounted) >= 14

    def test_the_prefix_is_ticket_derived(self):
        assert PREFIX == "/api/wf-073"


# --------------------------------------------------------------------------- #
# the seller surface
# --------------------------------------------------------------------------- #


class TestSellerSurface:
    def test_creating_a_link_returns_201_and_the_two_flags(self, client: TestClient):
        link = make_link(client, **{vocab.CONFIDENTIAL_VIEW: True})
        assert link["flags"][vocab.CONFIDENTIAL_VIEW] is True
        assert link["flags"][vocab.SCREENSHOT_PROTECTION] is False

    def test_a_link_with_no_flags_reads_both_off(self, client: TestClient):
        link = make_link(client)
        assert link["flags"] == {vocab.CONFIDENTIAL_VIEW: False, vocab.SCREENSHOT_PROTECTION: False}

    def test_reading_a_link_back(self, client: TestClient):
        link = make_link(client, **{vocab.SCREENSHOT_PROTECTION: True})
        response = client.get(f"{PREFIX}/links/{link['id']}")
        assert response.status_code == 200
        assert response.json()["flags"][vocab.SCREENSHOT_PROTECTION] is True

    def test_listing_the_links_of_a_room(self, client: TestClient):
        make_link(client)
        response = client.get(f"{PREFIX}/links")
        assert response.status_code == 200
        assert response.json()["count"] >= 1

    def test_the_panel_controls_come_back_with_the_link(self, client: TestClient):
        link = make_link(client, **{vocab.CONFIDENTIAL_VIEW: True})
        controls = {c["field"]: c for c in link["panel_controls"]}
        assert controls[vocab.CONFIDENTIAL_VIEW]["on"] is True
        assert controls[vocab.SCREENSHOT_PROTECTION]["on"] is False

    def test_the_panel_carries_the_specifications_own_descriptions(self, client: TestClient):
        link = make_link(client)
        summaries = {c["field"]: c["summary"] for c in link["panel_controls"]}
        assert summaries[vocab.CONFIDENTIAL_VIEW] == vocab.CONFIDENTIAL_VIEW_DESCRIPTION
        assert summaries[vocab.SCREENSHOT_PROTECTION] == vocab.SCREENSHOT_PROTECTION_DESCRIPTION

    def test_the_summary_reads_the_counts(self, client: TestClient):
        make_link(client, **{vocab.CONFIDENTIAL_VIEW: True})
        response = client.get(f"{PREFIX}/summary")
        assert response.status_code == 200
        assert response.json()["confidential_view"] >= 1


# --------------------------------------------------------------------------- #
# the tri-state update over HTTP
# --------------------------------------------------------------------------- #


class TestTriStateOverHttp:
    def test_an_empty_patch_leaves_a_flag_alone(self, client: TestClient):
        link = make_link(
            client, **{vocab.CONFIDENTIAL_VIEW: True, vocab.SCREENSHOT_PROTECTION: True}
        )
        response = client.patch(f"{PREFIX}/links/{link['id']}", json={})
        assert response.status_code == 200
        assert response.json()["flags"] == {
            vocab.CONFIDENTIAL_VIEW: True,
            vocab.SCREENSHOT_PROTECTION: True,
        }

    def test_a_boolean_sets_a_flag(self, client: TestClient):
        link = make_link(client)
        response = client.patch(
            f"{PREFIX}/links/{link['id']}", json={vocab.CONFIDENTIAL_VIEW: True}
        )
        assert response.status_code == 200
        assert response.json()["flags"][vocab.CONFIDENTIAL_VIEW] is True

    def test_an_explicit_null_returns_a_flag_to_the_default(self, client: TestClient):
        link = make_link(client, **{vocab.CONFIDENTIAL_VIEW: True})
        response = client.patch(
            f"{PREFIX}/links/{link['id']}", json={vocab.CONFIDENTIAL_VIEW: None}
        )
        assert response.status_code == 200
        assert response.json()["flags"][vocab.CONFIDENTIAL_VIEW] is False

    def test_the_cli_spelling_is_accepted_over_http(self, client: TestClient):
        """The specification names ``links update --confidential-view on|off``."""
        link = make_link(client)
        on = client.patch(f"{PREFIX}/links/{link['id']}", json={vocab.CONFIDENTIAL_VIEW: "on"})
        off = client.patch(f"{PREFIX}/links/{link['id']}", json={vocab.CONFIDENTIAL_VIEW: "off"})
        assert on.json()["flags"][vocab.CONFIDENTIAL_VIEW] is True
        assert off.json()["flags"][vocab.CONFIDENTIAL_VIEW] is False

    def test_a_toggle_does_not_change_the_id_or_the_url(self, client: TestClient):
        """ "the URL and existing viewers are unaffected"."""
        link = make_link(client, **{vocab.CONFIDENTIAL_VIEW: True})
        response = client.patch(
            f"{PREFIX}/links/{link['id']}", json={vocab.CONFIDENTIAL_VIEW: False}
        )
        assert response.json()["id"] == link["id"]
        assert response.json()["room_id"] == link["room_id"]

    def test_a_flag_set_on_one_link_does_not_touch_another(self, client: TestClient):
        first = make_link(client)
        second = make_link(client)
        client.patch(f"{PREFIX}/links/{first['id']}", json={vocab.CONFIDENTIAL_VIEW: True})
        assert (
            client.get(f"{PREFIX}/links/{second['id']}").json()["flags"][vocab.CONFIDENTIAL_VIEW]
            is False
        )


# --------------------------------------------------------------------------- #
# governance baselines over HTTP
# --------------------------------------------------------------------------- #


class TestBaselinesOverHttp:
    def test_a_baseline_is_created_and_lists(self, client: TestClient):
        preset = make_preset(client, **{vocab.CONFIDENTIAL_VIEW: True})
        assert preset["id"]
        listing = client.get(f"{PREFIX}/presets").json()
        assert listing["count"] >= 1
        assert any(row["id"] == preset["id"] for row in listing["presets"])

    def test_a_link_seeded_from_a_baseline_inherits_it(self, client: TestClient):
        preset = make_preset(
            client, **{vocab.CONFIDENTIAL_VIEW: True, vocab.SCREENSHOT_PROTECTION: True}
        )
        room_id = create_room(client)
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/links", json={"title": "Seeded", "preset_id": preset["id"]}
        )
        assert response.status_code == 201
        assert response.json()["flags"] == {
            vocab.CONFIDENTIAL_VIEW: True,
            vocab.SCREENSHOT_PROTECTION: True,
        }

    def test_reading_a_baseline_back(self, client: TestClient):
        preset = make_preset(client, **{vocab.SCREENSHOT_PROTECTION: True})
        response = client.get(f"{PREFIX}/presets/{preset['id']}")
        assert response.status_code == 200
        assert response.json()["fields"][vocab.SCREENSHOT_PROTECTION] is True

    def test_an_unknown_baseline_is_a_404(self, client: TestClient):
        response = client.get(f"{PREFIX}/presets/wf073_confidential_preset_nope")
        assert response.status_code == 404


# --------------------------------------------------------------------------- #
# the buyer's surface
# --------------------------------------------------------------------------- #


class TestBuyerSurface:
    def test_rendering_a_page_resolves_one_sharp_band(self, client: TestClient):
        link = make_link(client, **{vocab.CONFIDENTIAL_VIEW: True})
        response = client.post(
            f"{PREFIX}/links/{link['id']}/render",
            json={"page_height": 2000, "viewport_height": 800, "viewport_top": 0},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["applied"] is True
        assert sum(1 for band in body["bands"] if band["sharp"]) == 1

    def test_a_blurred_band_never_carries_text(self, client: TestClient):
        """ "the full-resolution page never reaches the client for out-of-band regions"."""
        link = make_link(client, **{vocab.CONFIDENTIAL_VIEW: True})
        body = client.post(
            f"{PREFIX}/links/{link['id']}/render",
            json={"page_height": 2000, "viewport_height": 800, "page_text_length": 3000},
        ).json()
        for band in body["bands"]:
            if not band["sharp"]:
                assert band["text"] is None
                assert band["text_length"] == 0

    def test_rendering_never_refuses_a_viewer(self, client: TestClient):
        """Confidential view is a rendering transformation, not a gate."""
        link = make_link(client, **{vocab.CONFIDENTIAL_VIEW: True})
        response = client.post(
            f"{PREFIX}/links/{link['id']}/render",
            json={"page_height": 2000, "viewport_height": 800},
        )
        assert response.status_code == 200

    def test_rendering_a_link_with_the_control_off_is_200_not_refused(self, client: TestClient):
        link = make_link(client)
        response = client.post(
            f"{PREFIX}/links/{link['id']}/render",
            json={"page_height": 2000, "viewport_height": 800},
        )
        assert response.status_code == 200
        assert response.json()["applied"] is False

    def test_the_read_only_render_answers_the_same_question(self, client: TestClient):
        link = make_link(client, **{vocab.CONFIDENTIAL_VIEW: True})
        response = client.get(
            f"{PREFIX}/links/{link['id']}/render",
            params={"page_height": 2000, "viewport_height": 800, "viewport_top": 400},
        )
        assert response.status_code == 200
        assert response.json()["applied"] is True

    def test_a_reported_attempt_records_the_shortcut(self, client: TestClient):
        link = make_link(client, **{vocab.SCREENSHOT_PROTECTION: True})
        response = client.post(
            f"{PREFIX}/links/{link['id']}/attempts", json={"keys": ["meta", "shift", "5"]}
        )
        assert response.status_code == 201
        assert response.json()["shortcut"] == "Command-Shift-5"

    def test_print_screen_is_reported_not_blocked(self, client: TestClient):
        """A row claiming a capture was prevented would be a claim the product cannot
        support."""
        link = make_link(client, **{vocab.SCREENSHOT_PROTECTION: True})
        response = client.post(
            f"{PREFIX}/links/{link['id']}/attempts", json={"keys": ["print_screen"]}
        )
        assert response.status_code == 201
        assert response.json()["outcome"] == vocab.ATTEMPT_REPORTED

    def test_the_caller_cannot_claim_its_own_attempt_was_blocked(self, client: TestClient):
        link = make_link(client, **{vocab.SCREENSHOT_PROTECTION: True})
        response = client.post(
            f"{PREFIX}/links/{link['id']}/attempts",
            json={"keys": ["print_screen"], "outcome": vocab.ATTEMPT_BLOCKED},
        )
        assert response.json()["outcome"] == vocab.ATTEMPT_REPORTED

    def test_listing_the_attempts_on_one_link(self, client: TestClient):
        link = make_link(client, **{vocab.SCREENSHOT_PROTECTION: True})
        client.post(f"{PREFIX}/links/{link['id']}/attempts", json={"keys": ["print_screen"]})
        response = client.get(f"{PREFIX}/links/{link['id']}/attempts")
        assert response.status_code == 200
        assert response.json()["count"] == 1

    def test_listing_every_attempt(self, client: TestClient):
        make_link(client, **{vocab.SCREENSHOT_PROTECTION: True})
        response = client.get(f"{PREFIX}/attempts")
        assert response.status_code == 200
        assert "attempts" in response.json()


# --------------------------------------------------------------------------- #
# the error shapes
# --------------------------------------------------------------------------- #


class TestErrorShapes:
    def test_a_bad_flag_is_a_400_with_a_field_keyed_map(self, client: TestClient):
        room_id = create_room(client)
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/links",
            json={"title": "Bad", vocab.CONFIDENTIAL_VIEW: "perhaps"},
        )
        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "confidential_settings_invalid"
        assert vocab.CONFIDENTIAL_VIEW in body["errors"]

    def test_a_bad_flag_on_a_patch_is_also_a_400(self, client: TestClient):
        link = make_link(client)
        response = client.patch(
            f"{PREFIX}/links/{link['id']}", json={vocab.CONFIDENTIAL_VIEW: "perhaps"}
        )
        assert response.status_code == 400

    def test_an_unknown_link_is_a_404(self, client: TestClient):
        response = client.get(f"{PREFIX}/links/wf073_confidential_link_nope")
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"

    def test_patching_an_unknown_link_is_a_404(self, client: TestClient):
        response = client.patch(f"{PREFIX}/links/wf073_confidential_link_nope", json={})
        assert response.status_code == 404

    def test_an_unknown_baseline_on_a_create_is_a_404(self, client: TestClient):
        room_id = create_room(client)
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/links", json={"preset_id": "wf073_confidential_preset_nope"}
        )
        assert response.status_code == 404

    def test_an_unknown_shortcut_is_a_422_not_a_400(self, client: TestClient):
        """A real key sequence this build has no record for is unprocessable, not
        malformed. The distinction is the point of the code."""
        link = make_link(client, **{vocab.SCREENSHOT_PROTECTION: True})
        response = client.post(
            f"{PREFIX}/links/{link['id']}/attempts", json={"keys": ["meta", "alt", "q"]}
        )
        assert response.status_code == 422
        assert response.json()["error"] == "unknown_shortcut"

    def test_the_422_names_the_shortcuts_this_build_does_know(self, client: TestClient):
        link = make_link(client, **{vocab.SCREENSHOT_PROTECTION: True})
        response = client.post(
            f"{PREFIX}/links/{link['id']}/attempts", json={"keys": ["meta", "alt", "q"]}
        )
        assert "Print Screen" in response.json()["known_shortcuts"]

    def test_an_unknown_decision_is_a_404(self, client: TestClient):
        assert client.get(f"{PREFIX}/decisions/NOT_A_DECISION").status_code == 404

    def test_a_rejected_request_never_5xx(self, client: TestClient):
        """The one shape every error route has to keep, whatever the cause."""
        attempts = [
            ("GET", f"{PREFIX}/links/nope", None),
            ("PATCH", f"{PREFIX}/links/nope", {}),
            ("POST", f"{PREFIX}/rooms/room_a/links", {vocab.CONFIDENTIAL_VIEW: "perhaps"}),
            ("GET", f"{PREFIX}/presets/nope", None),
            ("POST", f"{PREFIX}/rooms/room_a/links", {"preset_id": "nope"}),
        ]
        for method, url, payload in attempts:
            response = client.request(method, url, json=payload)
            assert response.status_code < 500, f"{method} {url} -> {response.status_code}"


# --------------------------------------------------------------------------- #
# the vocabulary and the recorded decisions
# --------------------------------------------------------------------------- #


class TestVocabularyRoute:
    def test_the_vocabulary_names_the_two_flags_and_their_defaults(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body["flags"] == list(vocab.FLAGS)
        assert body["defaults"] == dict(vocab.DEFAULTS)

    def test_the_vocabulary_serves_the_cli_flag_names(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body["cli_flags"][vocab.CONFIDENTIAL_VIEW] == "--confidential-view"
        assert body["cli_flags"][vocab.SCREENSHOT_PROTECTION] == "--screenshot-protection"

    def test_the_vocabulary_marks_print_screen_unblockable(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert "Print Screen" in body["unblockable_shortcuts"]
        assert "Print Screen" not in body["blockable_shortcuts"]

    def test_the_vocabulary_names_the_panel_the_specification_marked_inferred(
        self, client: TestClient
    ):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body["panel"]["title"] == vocab.PANEL_TITLE
        assert len(body["panel"]["controls"]) == 2

    def test_the_vocabulary_reports_the_chosen_render_shape(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body["render"]["chosen_shape"] == vocab.CHOSEN_RENDER_SHAPE
        assert set(body["render"]["shapes"]) == {"viewport_bands", "tile_shards"}

    def test_every_decision_is_served_over_http(self, client: TestClient):
        """The specification says an implementer "must derive it and record the
        derivation, not assume it". This is that record, readable."""
        body = client.get(f"{PREFIX}/decisions").json()
        assert body["count"] >= 8
        ids = {item["id"] for item in body["decisions"]}
        assert "INFERRED_ACCESS_CONTROLS_PANEL" in ids
        assert "DERIVED_DELIVERY_SHAPE" in ids

    def test_one_decision_is_readable_by_id(self, client: TestClient):
        body = client.get(f"{PREFIX}/decisions/DERIVED_DELIVERY_SHAPE").json()
        assert body["chosen"] == "viewport_bands"
        assert body["options"]


# --------------------------------------------------------------------------- #
# the audit-source rule
# --------------------------------------------------------------------------- #


class TestAuditSources:
    """The audit log the routes actually wrote to.

    Read through ``app.state.store`` rather than the ``store`` fixture: the ``client``
    fixture builds its own database and puts it on the application, and the routes write
    to that one. Reading a different database would return an empty log and the
    assertions below would pass for the wrong reason, which is worse than failing.
    """

    @staticmethod
    def audit_log() -> list[dict[str, Any]]:
        """The audit rows the routes have written so far.

        A method rather than a fixture, and that is the whole point. A fixture body
        runs *before* the test body, so a fixture reading the log would always see an
        empty database and every assertion built on it would pass for the wrong reason.
        Called from inside a test, it reads after that test's writes.
        """

        from dsr.api import app

        return list(app.state.store.audit(limit=200))

    def test_every_source_this_router_records_names_a_mounted_route(self):
        """The rule the build brief asks for by name.

        Checked against the routes the host really mounted rather than against a
        hand-written list, and the sources are read out of the module's own source
        rather than from a list kept beside it. That is what stops a new route from
        passing this test by being absent from the list.
        """
        module = importlib.import_module(FEATURE_MODULE)
        mounted = {
            f"{method} {route.path}"
            for route in module.router.routes
            for method in route.methods
            if method not in ("HEAD", "OPTIONS")
        }
        declared = {
            f"{method} {module.router.prefix}{path}" for method, path in _declared_sources()
        }
        assert declared, "the module records no source at all, so this test measures nothing"
        assert declared <= mounted, sorted(declared - mounted)

    def test_no_source_was_written_as_a_literal(self):
        """A ``source`` written as a literal is the defect this rule prevents: the audit
        log ends up naming a path the app stopped serving."""
        module = importlib.import_module(FEATURE_MODULE)
        prefixes = ("GET ", "POST ", "PATCH ", "PUT ", "DELETE ")
        literals = {
            value
            for value in vars(module).values()
            if isinstance(value, str) and value.startswith(prefixes)
        }
        assert not literals, f"a source literal crept in: {literals}"

    def test_a_create_names_the_room_scoped_route(self, client: TestClient):
        make_link(client)
        sources = [entry["source"] for entry in self.audit_log()]
        assert f"POST {PREFIX}/rooms/{{room_id}}/links" in sources

    def test_a_toggle_names_the_patch_route(self, client: TestClient):
        link = make_link(client)
        client.patch(f"{PREFIX}/links/{link['id']}", json={vocab.CONFIDENTIAL_VIEW: True})
        sources = [entry["source"] for entry in self.audit_log()]
        assert f"PATCH {PREFIX}/links/{{link_id}}" in sources

    def test_an_attempt_names_the_attempts_route(self, client: TestClient):
        link = make_link(client, **{vocab.SCREENSHOT_PROTECTION: True})
        client.post(f"{PREFIX}/links/{link['id']}/attempts", json={"keys": ["print_screen"]})
        sources = [entry["source"] for entry in self.audit_log()]
        assert f"POST {PREFIX}/links/{{link_id}}/attempts" in sources

    def test_the_renderer_writes_nothing_and_so_records_nothing(self, client: TestClient):
        """Rendering is a read. It resolves a viewport against stored flags and changes
        no state, so it must not write an audit row either - a route that wrote on every
        read would fill the guarantee's own log with rows describing nothing having
        changed. The source string it is given is still checked by the forward test
        above, so the rule holds without the route needing to lie about having served a
        write."""
        link = make_link(client, **{vocab.CONFIDENTIAL_VIEW: True})
        before = len(self.audit_log())
        response = client.post(
            f"{PREFIX}/links/{link['id']}/render",
            json={"page_height": 2000, "viewport_height": 800},
        )
        assert response.status_code == 200
        assert len(self.audit_log()) == before

    def test_a_baseline_names_the_presets_route(self, client: TestClient):
        make_preset(client, **{vocab.CONFIDENTIAL_VIEW: True})
        sources = [entry["source"] for entry in self.audit_log()]
        assert f"POST {PREFIX}/presets" in sources

    def test_every_write_reached_the_audit_log(self, client: TestClient):
        """The audit row is written in the same transaction as the change, which is the
        guarantee this product is built on."""
        link = make_link(client, **{vocab.SCREENSHOT_PROTECTION: True})
        client.post(f"{PREFIX}/links/{link['id']}/attempts", json={"keys": ["print_screen"]})
        collections = {entry["collection"] for entry in self.audit_log()}
        assert vocab.LINK_COLLECTION in collections
        assert vocab.ATTEMPT_COLLECTION in collections

    def test_no_audit_row_names_a_route_this_router_does_not_serve(self, client: TestClient):
        """The other half of the rule: every source this prefix recorded must be one of
        the routes the router really mounted. This is the direction that catches a
        renamed or deleted route, which the forward check cannot."""
        served = {
            f"{method} {route.path}"
            for route in importlib.import_module(FEATURE_MODULE).router.routes
            for method in route.methods
            if method not in ("HEAD", "OPTIONS")
        }
        link = make_link(client, **{vocab.SCREENSHOT_PROTECTION: True})
        client.post(f"{PREFIX}/links/{link['id']}/attempts", json={"keys": ["print_screen"]})
        recorded = {
            str(entry["source"]) for entry in self.audit_log() if PREFIX in str(entry["source"])
        }
        assert recorded, "the test made writes but read no rows back; the log is not being checked"
        assert recorded <= served, sorted(recorded - served)


# --------------------------------------------------------------------------- #
# the honesty rule
# --------------------------------------------------------------------------- #


class TestTheSpecificationBarOverHttp:
    """The specification forbids selling these controls as protection. Every response
    carries the caveat, so no caller can read a control without reading what it is
    worth."""

    def test_every_read_carries_the_effect_and_the_limitation(self, client: TestClient):
        link = make_link(
            client, **{vocab.CONFIDENTIAL_VIEW: True, vocab.SCREENSHOT_PROTECTION: True}
        )
        make_preset(client, **{vocab.CONFIDENTIAL_VIEW: True})
        bodies = [
            client.get(f"{PREFIX}/summary").json(),
            client.get(f"{PREFIX}/links/{link['id']}").json(),
            client.get(f"{PREFIX}/links").json()["links"][0],
            client.get(f"{PREFIX}/vocabulary").json(),
            client.get(f"{PREFIX}/presets").json()["presets"][0],
            client.get(f"{PREFIX}/attempts").json(),
        ]
        for body in bodies:
            assert body[vocab.EFFECT_FIELD] == vocab.EFFECT, body
            assert vocab.LIMITATION_FIELD in body, body

    def test_a_write_response_carries_it_too(self, client: TestClient):
        link = make_link(client)
        for body in (
            client.patch(
                f"{PREFIX}/links/{link['id']}", json={vocab.CONFIDENTIAL_VIEW: True}
            ).json(),
            client.post(
                f"{PREFIX}/links/{link['id']}/render",
                json={"page_height": 2000, "viewport_height": 800},
            ).json(),
            client.post(
                f"{PREFIX}/links/{link['id']}/attempts", json={"keys": ["print_screen"]}
            ).json(),
        ):
            assert body[vocab.EFFECT_FIELD] == vocab.EFFECT, body

    def test_the_error_bodies_carry_it_as_well(self, client: TestClient):
        """A refusal is exactly where a buyer most needs to know what the control is
        worth."""
        link = make_link(client, **{vocab.SCREENSHOT_PROTECTION: True})
        body = client.post(
            f"{PREFIX}/links/{link['id']}/attempts", json={"keys": ["meta", "alt", "q"]}
        ).json()
        assert body[vocab.EFFECT_FIELD] == vocab.EFFECT

    def test_the_vocabulary_states_the_limit_in_words(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert "largely unenforceable from a browser" in body[vocab.LIMITATION_FIELD]
        assert body[vocab.EFFECT_FIELD] == "deterrent"

    def test_no_response_claims_the_control_stops_a_capture(self, client: TestClient):
        link = make_link(
            client, **{vocab.CONFIDENTIAL_VIEW: True, vocab.SCREENSHOT_PROTECTION: True}
        )
        payloads = [
            client.get(f"{PREFIX}/summary").text,
            client.get(f"{PREFIX}/links/{link['id']}").text,
            client.get(f"{PREFIX}/vocabulary").text,
        ]
        for text in payloads:
            lowered = text.lower()
            assert "prevents screenshots" not in lowered
            assert "cannot be screenshotted" not in lowered


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _declared_sources() -> list[tuple[str, str]]:
    """Every ``_source("METHOD", "/path")`` the feature module writes.

    Read from the module's source rather than from a list kept beside it, so a new
    route that forgot to pass a source cannot pass the test above by being absent from
    the list.
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
        if len(node.args) == 2 and all(isinstance(arg, ast.Constant) for arg in node.args):
            found.append((node.args[0].value, node.args[1].value))
    return found


# --------------------------------------------------------------------------- #
# the seed over the real engine
# --------------------------------------------------------------------------- #


class TestSeedOverHttp:
    def test_the_seed_runs_against_a_fresh_database_and_returns_cp1252_text(self, db):
        """Every character encodable by cp1252. One RIGHTWARDS ARROW in a recovered
        feature's return string broke the whole seeder on a Windows console."""
        module = importlib.import_module(FEATURE_MODULE)
        store = RecordStore(db)
        store.create("room", {"name": "A"}, record_id="room_a", actor="dana", source="fixture")
        store.create("room", {"name": "B"}, record_id="room_b", actor="dana", source="fixture")
        message = module.seed(db, {"room_ids": [("room_a", "A"), ("room_b", "B")], "now": NOW})
        message.encode("cp1252")
        assert message

    def test_the_seed_writes_only_under_this_workflows_collections(self, db):
        module = importlib.import_module(FEATURE_MODULE)
        store = RecordStore(db)
        store.create("room", {"name": "A"}, record_id="room_a", actor="dana", source="fixture")
        module.seed(db, {"room_ids": [("room_a", "A")], "now": NOW})
        collections = {record["collection"] for record in store.list(vocab.LINK_COLLECTION)}
        assert collections == {vocab.LINK_COLLECTION}
        assert rules.ROOM_REF == "room_ref"
