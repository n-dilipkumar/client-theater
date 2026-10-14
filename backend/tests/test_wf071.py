"""WF-071 domain tests: the config rules, the tokens, the render, the plan gate.

These drive :mod:`dsr.viewer_watermark` directly, over a temporary database, with
no HTTP client. The HTTP surface has its own module, ``test_wf071_http.py``.

Every test names the rule it protects, quoting
``docs/research/digital-sales-room-workflows/wf/WF-071.md`` where the research is
the source. The clock is frozen at :data:`NOW`, so no test depends on today's date
or on a weekday that will pass - the brief calls that a time bomb.

The database is a fresh one per test, through the shared ``store`` fixture, so
this file passes alone and under ``pytest-xdist`` in any order.
"""

from __future__ import annotations

import ast
import importlib
from datetime import datetime, timezone
from pathlib import Path

import dsr.tracked_deal_link as tracked
import dsr.viewer_watermark as domain
import pytest
from dsr.pdf_analytics import AnalyticsBook
from dsr.store import RecordStore
from dsr.tracked_deal_link import (
    LinkClosed,
    ViewerRequired,
    rules as link_rules,
    vocabulary as tracked_vocab,
)
from dsr.viewer_watermark import (
    COLLECTION,
    STAMP_COLLECTION,
    WatermarkBook,
    WatermarkLinkNotFound,
    WatermarkPlanError,
    WatermarkValidationError,
    interpolate,
    normalise_config,
    plan_allows,
    render_pages,
    require_plan,
    tokens_used,
)
from dsr.workspace_roles import vocabulary as workspace_vocabulary

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

DOMAIN_DIR = Path(domain.__file__).parent
FEATURE_MODULE = (
    Path(domain.__file__).parent.parent
    / "features"
    / "wf071_stamp_a_dynamic_per_viewer_watermark.py"
)

#: ``dsr.api`` / ``dsr.features`` are absent because a domain module must never
#: reach for the app or for another workflow's feature module.
ALLOWED_DSR_PREFIXES = (
    "dsr.store",
    "dsr.db.audited",
    "dsr.tracked_deal_link",
    "dsr.link_gating",
    "dsr.workspace_roles",
    "dsr.viewer_watermark",
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def room(store: RecordStore) -> dict:
    return store.create(
        "room", {"name": "Northwind", "account": "Northwind"}, actor="dana", source="test"
    )


@pytest.fixture()
def asset(store: RecordStore, room: dict) -> dict:
    return AnalyticsBook(store).register_asset(
        {
            "externalId": "deck-demo",
            "name": "Enterprise Security & Compliance Overview",
            "type": "pdf",
            "pageCount": 8,
        },
        room_id=room["id"],
        actor="dana",
        source="test",
    )


@pytest.fixture()
def book(store: RecordStore) -> WatermarkBook:
    """The book with a frozen clock, so the stamped date and time are exact."""
    return WatermarkBook(store, now=lambda: NOW)


@pytest.fixture()
def links(store: RecordStore) -> tracked.TrackedLinkBook:
    return tracked.TrackedLinkBook(store, now=lambda: NOW)


@pytest.fixture()
def link(links: tracked.TrackedLinkBook, room: dict, asset: dict) -> dict:
    """A WF-121 link the watermark is set on, as the API projects it."""
    return links.create_link(
        room["id"],
        {
            "asset_id": "deck-demo",
            "title": "Security overview",
            "recipients": ["a.buyer@northwind.example"],
            "share_url": "/api/wf-121/links/demo",
        },
        actor="dana",
        source="POST /api/wf-121/links",
    )


# --------------------------------------------------------------------------- #
# The vocabulary is reused, not re-declared
# --------------------------------------------------------------------------- #


class TestReusedVocabulary:
    def test_the_link_and_view_collections_are_wf121s_own(self):
        assert domain.LINK_COLLECTION == tracked.LINK_COLLECTION == "wf121_tracked_link"
        assert domain.VIEW_COLLECTION == tracked.VIEW_COLLECTION == "wf121_link_page_view"

    def test_the_room_reference_is_wf121s_own_key(self):
        assert domain.ROOM_REF == tracked.ROOM_REF == "room_ref"

    def test_the_viewer_rule_is_wf121s_own_function(self):
        """Identity is one spelling in both workflows, or the trace names a ghost."""
        assert link_rules.__name__ == "dsr.tracked_deal_link.rules"
        assert tracked_vocab.require_viewer("Buyer@Northwind.example") == "buyer@northwind.example"

    def test_the_plan_tier_is_wf077s_own_vocabulary(self):
        from dsr.viewer_watermark import vocabulary as vocab

        assert vocab.PLAN_REQUIRED == workspace_vocabulary.PLAN_BUSINESS
        assert vocab.PLAN_REQUIRED in workspace_vocabulary.PLANS

    def test_the_published_vocabulary_names_the_workflow_each_value_came_from(self):
        published = domain.describe_vocabulary()
        assert published["reused"]["link_collection"] == "wf121_tracked_link"
        assert "WF-121" in published["reused"]["link_collection_source"]
        assert "WF-069" in published["reused"]["viewer_source"]
        assert "WF-077" in published["reused"]["plan_gate_source"]
        assert published["collections"]["configs"] == COLLECTION


# --------------------------------------------------------------------------- #
# The config rules
# --------------------------------------------------------------------------- #


class TestTheConfigRules:
    def test_every_missing_field_is_filled_from_the_documented_default(self):
        config = normalise_config({})
        assert config == {
            "text": "Confidential",
            "is_tiled": False,
            "position": "middle-center",
            "rotation": 45,
            "color": "#000000",
            "font_size": 24,
            "opacity": 0.5,
        }

    def test_an_empty_body_is_a_valid_config_because_all_seven_have_defaults(self):
        assert set(normalise_config(None)) == set(domain.REQUIRED_FIELDS)

    def test_all_seven_fields_are_validated_and_kept(self):
        config = normalise_config(
            {
                "text": "Confidential",
                "is_tiled": True,
                "position": "top-left",
                "rotation": 90,
                "color": "#abc",
                "font_size": 96,
                "opacity": 1,
            }
        )
        assert config["is_tiled"] is True
        assert config["rotation"] == 90
        assert config["color"] == "#abc"
        assert config["font_size"] == 96
        assert config["opacity"] == 1.0

    def test_the_tiled_alias_resolves_to_is_tiled(self):
        assert normalise_config({"tiled": "true"})["is_tiled"] is True

    @pytest.mark.parametrize("position", ["middle", "center", "top", "", None])
    def test_a_position_outside_the_nine_is_refused(self, position):
        if position in (None, ""):
            assert normalise_config({"position": position})["position"] == "middle-center"
            return
        with pytest.raises(WatermarkValidationError) as refused:
            normalise_config({"position": position})
        assert refused.value.context["field"] == "position"

    @pytest.mark.parametrize("rotation", [0, 30, 45, 90, 180])
    def test_every_researched_rotation_is_accepted(self, rotation):
        assert normalise_config({"rotation": rotation})["rotation"] == rotation

    def test_a_rotation_outside_the_const_set_is_refused(self):
        with pytest.raises(WatermarkValidationError) as refused:
            normalise_config({"rotation": 60})
        assert refused.value.context["allowed"] == [0, 30, 45, 90, 180]

    @pytest.mark.parametrize("color", ["#abc", "#ABCDEF", "#123456"])
    def test_a_three_or_six_digit_hex_colour_is_accepted(self, color):
        assert normalise_config({"color": color})["color"] == color

    @pytest.mark.parametrize("color", ["black", "#12", "#12345", "rgb(0,0,0)"])
    def test_a_colour_that_is_not_the_schema_pattern_is_refused(self, color):
        with pytest.raises(WatermarkValidationError) as refused:
            normalise_config({"color": color})
        assert refused.value.context["field"] == "color"

    def test_an_absent_colour_falls_back_to_the_documented_black(self):
        assert normalise_config({"color": ""})["color"] == "#000000"

    @pytest.mark.parametrize("size", [0, 97, -1, 1000])
    def test_a_font_size_outside_one_to_96_is_refused(self, size):
        with pytest.raises(WatermarkValidationError) as refused:
            normalise_config({"font_size": size})
        assert refused.value.context["field"] == "font_size"

    @pytest.mark.parametrize("opacity", [-0.1, 1.1, 2])
    def test_an_opacity_outside_zero_to_one_is_refused(self, opacity):
        with pytest.raises(WatermarkValidationError) as refused:
            normalise_config({"opacity": opacity})
        assert refused.value.context["field"] == "opacity"

    def test_a_boolean_is_not_a_number(self):
        with pytest.raises(WatermarkValidationError):
            normalise_config({"font_size": True})

    def test_a_text_longer_than_the_cap_is_refused(self):
        with pytest.raises(WatermarkValidationError) as refused:
            normalise_config({"text": "x" * 201})
        assert refused.value.context["field"] == "text"

    def test_an_unknown_token_is_refused_at_authoring_time(self):
        """A typo'd token would render literally onto every page of a buyer's view."""
        with pytest.raises(WatermarkValidationError) as refused:
            normalise_config({"text": "Confidential {{emial}}"})
        assert refused.value.context["unknown_tokens"] == ["emial"]

    def test_the_five_researched_tokens_are_each_accepted(self):
        config = normalise_config({"text": "{{email}} {{date}} {{time}} {{link}} {{ipAddress}}"})
        assert tokens_used(config["text"]) == ["email", "date", "time", "link", "ipAddress"]


# --------------------------------------------------------------------------- #
# The tokens
# --------------------------------------------------------------------------- #


class TestInterpolation:
    def test_every_token_is_substituted_from_the_view(self):
        view = {
            "viewer": "buyer@northwind.example",
            "ip_address": "203.0.113.9",
            "share_url": "/api/wf-121/links/ln_1",
            "occurred_at": NOW,
        }
        rendered, unresolved = interpolate(
            "{{email}} | {{date}} | {{time}} | {{link}} | {{ipAddress}}", view
        )
        assert rendered == (
            "buyer@northwind.example | 2026-05-01 | 12:00:00 | /api/wf-121/links/ln_1 | 203.0.113.9"
        )
        assert unresolved == []

    def test_the_email_token_without_a_viewer_is_the_recorded_placeholder(self):
        rendered, unresolved = interpolate("{{email}}", {"occurred_at": NOW})
        assert rendered == "anonymous"
        assert unresolved == ["email"]

    def test_the_ip_token_without_an_address_is_the_recorded_placeholder(self):
        rendered, unresolved = interpolate("{{ipAddress}}", {"occurred_at": NOW})
        assert rendered == "unknown"
        assert unresolved == ["ipAddress"]

    def test_a_repeated_missing_token_is_reported_once(self):
        _, unresolved = interpolate("{{email}} and {{email}}", {"occurred_at": NOW})
        assert unresolved == ["email"]

    def test_the_view_timestamp_drives_date_and_time_not_the_wall_clock(self):
        view = {"occurred_at": datetime(2031, 12, 31, 23, 59, 59, tzinfo=timezone.utc)}
        rendered, _ = interpolate("{{date}} {{time}}", view)
        assert rendered == "2031-12-31 23:59:59"

    def test_whitespace_inside_the_braces_is_tolerated(self):
        rendered, _ = interpolate("{{ email }}", {"viewer": "buyer@northwind.example"})
        assert rendered == "buyer@northwind.example"


# --------------------------------------------------------------------------- #
# The render
# --------------------------------------------------------------------------- #


class TestRenderPerPage:
    def test_the_overlay_is_composited_onto_every_page(self):
        config = normalise_config({"text": "Confidential {{email}}"})
        view = {"viewer": "buyer@northwind.example", "occurred_at": NOW}
        rendered = render_pages(config, view, page_count=4)
        assert [page["page"] for page in rendered["pages"]] == [1, 2, 3, 4]
        assert all(
            page["overlay"]["text"] == "Confidential buyer@northwind.example"
            for page in rendered["pages"]
        )
        assert rendered["page_count"] == 4

    def test_a_link_with_no_page_count_still_renders_one_page(self):
        rendered = render_pages(normalise_config({}), {"occurred_at": NOW}, page_count=0)
        assert rendered["page_count"] == 1

    def test_the_overlay_carries_the_seven_rendered_fields(self):
        rendered = render_pages(normalise_config({"is_tiled": True}), {"occurred_at": NOW})
        overlay = rendered["overlay"]
        assert overlay["is_tiled"] is True
        assert overlay["position"] == "middle-center"
        assert overlay["rotation"] == 45
        assert overlay["color"] == "#000000"
        assert overlay["font_size"] == 24
        assert overlay["opacity"] == 0.5


# --------------------------------------------------------------------------- #
# Plan entitlement
# --------------------------------------------------------------------------- #


class TestThePlanGate:
    @pytest.mark.parametrize("plan", ["business", "enterprise", "BUSINESS"])
    def test_a_plan_at_or_above_the_required_tier_entitles_a_watermark(self, plan):
        assert plan_allows(plan) is True

    def test_a_plan_below_the_required_tier_does_not(self):
        assert plan_allows("starter") is False

    def test_an_absent_plan_is_not_refused(self):
        """There is no plan context on this surface; that asymmetry is an inference."""
        assert plan_allows(None) is True
        assert plan_allows("") is True

    def test_a_refusal_carries_the_researched_403_code(self):
        with pytest.raises(WatermarkPlanError) as refused:
            require_plan("starter")
        assert refused.value.status_code == 403
        assert refused.value.code == "forbidden_plan_feature"
        assert refused.value.context["feature"] == "watermark"
        assert "enforced independently of scopes" in refused.value.context["rule"]


# --------------------------------------------------------------------------- #
# The book
# --------------------------------------------------------------------------- #


class TestTheBook:
    def test_read_always_reports_watermark_config_or_null(self, book: WatermarkBook, link: dict):
        result = book.read(link["id"])
        assert "watermark_config" in result
        assert result["watermark_config"] is None
        assert result["enable_watermark"] is False
        assert result["link"]["id"] == link["id"]

    def test_enabling_stores_the_config_in_this_workflows_collection(
        self, store: RecordStore, book: WatermarkBook, link: dict
    ):
        result = book.enable(
            link["id"],
            {"text": "{{email}}", "is_tiled": True},
            actor="dana",
            source="POST /api/wf-071/links/{link_id}/watermark",
        )
        assert result["enable_watermark"] is True
        assert result["watermark_config"]["text"] == "{{email}}"
        assert result["watermark_config"]["is_tiled"] is True
        rows = store.find(COLLECTION, {"link_id": link["id"]})
        assert len(rows) == 1 and rows[0]["data"]["enable_watermark"] is True

    def test_the_link_row_is_untouched_so_wf121_cannot_drop_the_config(
        self, store: RecordStore, book: WatermarkBook, link: dict, links: tracked.TrackedLinkBook
    ):
        """The chosen storage survives an edit made through WF-121's own routes."""
        book.enable(link["id"], {"text": "Confidential"}, source="POST watermark")
        links.update_link(
            link["id"], {"alert_on_page": 5}, source="PATCH /api/wf-121/links/{link_id}"
        )
        assert book.read(link["id"])["watermark_config"]["text"] == "Confidential"

    def test_an_unknown_link_is_not_found(self, book: WatermarkBook):
        with pytest.raises(WatermarkLinkNotFound):
            book.read("ln_absent")

    def test_a_link_for_an_unknown_room_is_not_found(self, book: WatermarkBook):
        with pytest.raises(WatermarkLinkNotFound):
            book.read("no-such-reference")

    def test_enabling_under_a_lower_plan_is_refused_403(self, book: WatermarkBook, link: dict):
        with pytest.raises(WatermarkPlanError) as refused:
            book.enable(
                link["id"], {"text": "Confidential"}, source="POST watermark", plan="starter"
            )
        assert refused.value.code == "forbidden_plan_feature"

    def test_the_gate_is_not_re_checked_when_the_toggle_is_already_on(
        self, book: WatermarkBook, link: dict
    ):
        """Researched: an existing link keeps working after a downgrade."""
        book.enable(link["id"], {"text": "Confidential"}, source="POST watermark", plan="business")
        edited = book.enable(
            link["id"],
            {"text": "Confidential \u2014 {{email}}"},
            source="POST watermark",
            plan="starter",
        )
        assert edited["watermark_config"]["text"].startswith("Confidential")

    def test_patching_replaces_the_config(self, book: WatermarkBook, link: dict):
        book.enable(link["id"], {"text": "Confidential"}, source="POST watermark")
        patched = book.patch(link["id"], {"font_size": 40}, source="PATCH watermark")
        assert patched["watermark_config"]["font_size"] == 40
        assert patched["watermark_config"]["text"] == "Confidential"

    def test_patching_with_a_null_config_clears_it_and_leaves_the_toggle_on(
        self, book: WatermarkBook, link: dict
    ):
        """The researched PATCH: clear the config without disabling the toggle."""
        book.enable(link["id"], {"text": "Confidential"}, source="POST watermark")
        cleared = book.patch(link["id"], {"watermark_config": None}, source="PATCH watermark")
        assert cleared["watermark_config"] is None
        assert cleared["enable_watermark"] is True
        assert cleared["cleared"] is True

    def test_a_patch_before_any_config_is_an_enable_and_meets_the_plan_gate(
        self, book: WatermarkBook, link: dict
    ):
        with pytest.raises(WatermarkPlanError):
            book.patch(
                link["id"], {"text": "Confidential"}, source="PATCH watermark", plan="starter"
            )

    def test_disabling_keeps_the_config_so_re_enabling_restores_it(
        self, book: WatermarkBook, link: dict
    ):
        book.enable(link["id"], {"text": "Confidential {{email}}"}, source="POST watermark")
        off = book.disable(link["id"], source="DELETE watermark")
        assert off["enable_watermark"] is False
        assert off["watermark_config"]["text"] == "Confidential {{email}}"
        back = book.enable(link["id"], {}, source="POST watermark", plan="business")
        assert back["enable_watermark"] is True
        assert back["watermark_config"]["text"] == "Confidential {{email}}"
        # The toggle flipped back on, so the gate applies again.
        book.disable(link["id"], source="DELETE watermark")
        with pytest.raises(WatermarkPlanError):
            book.enable(link["id"], {}, source="POST watermark", plan="starter")

    def test_a_disabled_config_composites_nothing(
        self, store: RecordStore, book: WatermarkBook, link: dict
    ):
        book.enable(link["id"], {"text": "Confidential"}, source="POST watermark")
        book.disable(link["id"], source="DELETE watermark")
        result = book.stamp(link["id"], {"viewer": "a@b.example"}, source="POST stamp")
        assert result["enable_watermark"] is False
        assert result["stamped"] is False
        assert result["pages"] == []
        assert store.find(STAMP_COLLECTION, {"link_id": link["id"]}) == []

    def test_a_stamp_composites_the_link_page_count_and_records_the_view(
        self, store: RecordStore, book: WatermarkBook, link: dict
    ):
        book.enable(
            link["id"],
            {"text": "Confidential \u2014 {{email}} \u2014 {{date}}"},
            source="POST watermark",
        )
        result = book.stamp(
            link["id"],
            {"viewer": "Buyer@Northwind.example", "ip_address": "203.0.113.9"},
            source="POST /api/wf-071/links/{link_id}/stamp",
        )
        assert result["stamped"] is True
        # The link registered an 8-page deck; the overlay reaches every page.
        assert result["page_count"] == 8
        assert (
            result["rendered_text"]
            == "Confidential \u2014 buyer@northwind.example \u2014 2026-05-01"
        )
        rows = store.find(STAMP_COLLECTION, {"link_id": link["id"]})
        assert len(rows) == 1
        assert rows[0]["data"]["viewer"] == "buyer@northwind.example"
        assert rows[0]["data"]["ip_address"] == "203.0.113.9"

    def test_a_stamp_honours_the_views_own_page_count(self, book: WatermarkBook, link: dict):
        book.enable(link["id"], {"text": "Confidential"}, source="POST watermark")
        result = book.stamp(
            link["id"], {"viewer": "a@b.example", "page_count": 3}, source="POST stamp"
        )
        assert result["page_count"] == 3

    def test_a_stamp_without_a_viewer_is_refused_by_wf121s_own_rule(
        self, book: WatermarkBook, link: dict
    ):
        book.enable(link["id"], {"text": "{{email}}"}, source="POST watermark")
        with pytest.raises(ViewerRequired):
            book.stamp(link["id"], {"ip_address": "203.0.113.9"}, source="POST stamp")

    def test_an_anonymous_link_stamps_and_names_the_unresolved_tokens(
        self, book: WatermarkBook, links: tracked.TrackedLinkBook, room: dict, asset: dict
    ):
        anonymous = links.create_link(
            room["id"],
            {"asset_id": "deck-demo", "require_viewer": False},
            source="POST /api/wf-121/links",
        )
        book.enable(anonymous["id"], {"text": "{{email}} {{ipAddress}}"}, source="POST watermark")
        result = book.stamp(anonymous["id"], {}, source="POST stamp")
        assert result["rendered_text"] == "anonymous unknown"
        assert result["unresolved_tokens"] == ["email", "ipAddress"]

    def test_a_stamp_on_a_closed_link_is_refused(self, book: WatermarkBook, links, room, asset):
        expired = links.create_link(
            room["id"], {"asset_id": "deck-demo", "expires_at": "2026-01-01"}, source="POST links"
        )
        book.enable(expired["id"], {"text": "Confidential"}, source="POST watermark")
        with pytest.raises(LinkClosed):
            book.stamp(expired["id"], {"viewer": "a@b.example"}, source="POST stamp")

    def test_the_stamp_view_uses_the_views_own_timestamp(self, book: WatermarkBook, link: dict):
        book.enable(link["id"], {"text": "{{date}} {{time}}"}, source="POST watermark")
        result = book.stamp(
            link["id"],
            {
                "viewer": "a@b.example",
                "occurred_at": datetime(2031, 1, 2, 3, 4, 5, tzinfo=timezone.utc).isoformat(),
            },
            source="POST stamp",
        )
        assert result["rendered_text"] == "2031-01-02 03:04:05"

    def test_the_stamp_view_reuses_wf121s_payload_privacy_boundary(
        self, book: WatermarkBook, link: dict
    ):
        """A document body is refused by WF-121's own rule, so no body is stored."""
        book.enable(link["id"], {"text": "Confidential"}, source="POST watermark")
        with pytest.raises(tracked.LinkValidationError):
            book.stamp(
                link["id"], {"viewer": "a@b.example", "content": "the deck"}, source="POST stamp"
            )

    def test_preview_writes_nothing(self, store: RecordStore, book: WatermarkBook):
        result = book.preview(
            {
                "text": "Confidential \u2014 {{email}}",
                "viewer": "Buyer@Northwind.example",
                "page_count": 2,
            }
        )
        assert result["rendered_text"] == "Confidential \u2014 buyer@northwind.example"
        assert result["page_count"] == 2
        assert store.list(COLLECTION, limit=10) == []

    def test_the_summary_counts_the_states_a_reviewer_checks(
        self, book: WatermarkBook, links, room, link
    ):
        book.enable(link["id"], {"text": "{{email}}", "is_tiled": True}, source="POST watermark")
        book.stamp(link["id"], {"viewer": "a@b.example"}, source="POST stamp")
        anonymous = links.create_link(
            room["id"], {"asset_id": "deck-demo", "require_viewer": False}, source="POST links"
        )
        book.enable(anonymous["id"], {"text": "Draft"}, source="POST watermark")
        book.patch(anonymous["id"], {"watermark_config": None}, source="PATCH watermark")
        summary = book.summary(room["id"])
        assert summary["links"] == 2
        assert summary["watermarked"] == 2
        assert summary["tiled"] == 1
        assert summary["cleared"] == 1
        assert summary["stamps"] == 1

    def test_list_links_joins_the_settings_onto_every_link(
        self, book: WatermarkBook, links, room, link
    ):
        book.enable(link["id"], {"text": "Confidential"}, source="POST watermark")
        listed = book.list_links(room["id"])
        assert len(listed) == 1
        assert listed[0]["enable_watermark"] is True
        assert listed[0]["watermark_config"]["text"] == "Confidential"


# --------------------------------------------------------------------------- #
# The audited store
# --------------------------------------------------------------------------- #


class TestTheAuditedStore:
    def test_every_write_lands_an_audit_row_naming_its_route(
        self, store: RecordStore, book: WatermarkBook, link: dict
    ):
        source = "POST /api/wf-071/links/{link_id}/watermark"
        book.enable(link["id"], {"text": "Confidential"}, actor="dana", source=source)
        rows = store.audit(limit=50)
        assert any(row.get("source") == source for row in rows)


# --------------------------------------------------------------------------- #
# Domain reuse and the feature module
# --------------------------------------------------------------------------- #


def _domain_modules() -> list[Path]:
    return sorted(DOMAIN_DIR.glob("*.py"))


class TestArchitecture:
    def test_the_domain_package_never_imports_the_app_or_a_feature(self):
        for path in _domain_modules():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    assert name != "dsr.api" and not name.startswith("dsr.api."), path.name
                    assert name != "dsr.features" and not name.startswith("dsr.features."), (
                        path.name
                    )
                    assert not name.startswith("fastapi"), path.name
            text = path.read_text(encoding="utf-8")
            assert "import sqlite3" not in text, path.name
            assert "sqlite3.connect" not in text, path.name

    def test_the_domain_package_imports_only_allowed_dsr_packages(self):
        for path in _domain_modules():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if not name.startswith("dsr"):
                        continue
                    assert name.startswith(ALLOWED_DSR_PREFIXES), f"{path.name} imports {name}"

    def test_the_feature_module_takes_its_dependencies_from_deps(self):
        text = FEATURE_MODULE.read_text(encoding="utf-8")
        assert "from dsr.deps import" in text
        assert "from dsr.api" not in text and "import dsr.api" not in text
        assert "import sqlite3" not in text
        assert "sqlite3.connect" not in text

    def test_the_feature_module_declares_its_ticket_prefix_and_handler(self):
        module = importlib.import_module("dsr.features.wf071_stamp_a_dynamic_per_viewer_watermark")
        assert module.FEATURE["id"] == "wf-071-watermark"
        assert module.FEATURE["ticket"] == "WF-071"
        assert module.router.prefix == "/api/wf-071"
        assert list(module.EXCEPTION_HANDLERS) == [domain.ViewerWatermarkError]

    def test_the_error_type_is_this_workflows_own(self):
        assert domain.ViewerWatermarkError.__module__ == "dsr.viewer_watermark.errors"
        assert domain.WatermarkPlanError.__module__ == "dsr.viewer_watermark.errors"

    def test_a_document_body_is_refused_by_the_reused_boundary(self):
        from dsr.tracked_deal_link import LinkValidationError, refuse_document_bodies

        with pytest.raises(LinkValidationError):
            refuse_document_bodies({"content": "the deck"})


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


class TestSeed:
    def test_the_demo_seeds_a_watermarked_link_and_shows_a_refusal(self, db):
        scoped = RecordStore(db)
        room = scoped.create("room", {"name": "Northwind"}, actor="dana", source="test")
        summary = domain_feature_seed(
            db, {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": None}
        )
        assert isinstance(summary, str) and summary
        # A Windows console prints this string; a non-cp1252 character once broke
        # the whole seeder, so the encoding is asserted rather than assumed.
        summary.encode("cp1252")
        assert "refused" in summary
        assert "cleared" in summary
        configs = scoped.list(COLLECTION, limit=10)
        assert len(configs) == 2
        stamps = scoped.list(STAMP_COLLECTION, limit=10)
        assert len(stamps) == 2

    def test_the_demo_reports_nothing_when_there_is_no_room(self, db):
        summary = domain_feature_seed(db, {"room_ids": [], "now": NOW, "rng": None})
        assert "0 watermarked links" in summary
        summary.encode("cp1252")


def domain_feature_seed(db, context):
    """Call the feature module's ``seed`` without importing the feature lazily."""
    module = importlib.import_module("dsr.features.wf071_stamp_a_dynamic_per_viewer_watermark")
    return module.seed(db, context)


# --------------------------------------------------------------------------- #
# The stored text keeps the researched em dash, and the summary does not need it
# --------------------------------------------------------------------------- #


def test_a_stored_watermark_text_may_hold_an_em_dash():
    config = normalise_config({"text": "Confidential \u2014 {{email}}"})
    assert "\u2014" in config["text"]
    # The rendered overlay is composited, never printed by the seeder.
    rendered, _ = interpolate(config["text"], {"viewer": "a@b.example", "occurred_at": NOW})
    assert rendered == "Confidential \u2014 a@b.example"
    rendered.encode("cp1252")


def test_a_link_needs_a_library_asset_like_wf121(links: tracked.TrackedLinkBook, room: dict):
    with pytest.raises(tracked.LinkValidationError):
        links.create_link(room["id"], {"asset_id": "no-such-asset"}, source="POST links")


def test_the_revisit_gap_is_wf121s_own():
    """Referenced so a rename in WF-121's vocabulary fails in this file too."""
    assert tracked.REVISIT_GAP_SECONDS == 1800
