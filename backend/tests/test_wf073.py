"""WF-073: apply confidential view and block screenshot shortcuts.

The researched specification is ``docs/research/digital-sales-room-workflows/wf/WF-073.md``,
quoted in full in issue 176. These tests are organised by the decision they defend,
because the point of this workflow is that the decisions were researched rather than
chosen - so a rule with no test is a rule that will be quietly dropped by the next
person to touch it.

The sections, and the researched rule each pins:

``the two defaults``
    "``enable_screenshot_protection`` (boolean, default ``false``), ``enable_confidential_view``
    (boolean, default ``false``)". A link with neither flag is both-off, and a flag that
    is not a boolean is refused rather than coerced.
``the tri-state update``
    Absent, set, and cleared - the three states that let one endpoint rotate a control
    and then remove it, and that let a preset baseline reach a new link.
``in place, and the URL unaffected``
    "Toggle either flag in place" and "the URL and existing viewers are unaffected": the
    id, the room and every other stored field survive a toggle.
``governance baselines``
    "Both flags are ``preset_id``-coverable, so they can be governance baselines", and
    they "are inherited by every link created from a preset".
``the focus band``
    "A narrow band of each page at a time", "content only resolves inside the focus
    band", and the property that makes it work: out-of-band content carries no glyphs.
``the shortcut list``
    "Block common screenshot / screen-recording shortcuts while viewing", plus the
    honesty rule that Print Screen is named as unblockable.
``an attempt is a report``
    The specification says blocking is "largely unenforceable from a browser". The
    outcome is derived from the list, never accepted from the caller.
``the domain imports nothing but the store``
    The architectural guard the brief names by name.
``the seed return string``
    Every character encodable by cp1252, and the states the seeder claims actually
    exist.

The HTTP surface is in ``test_wf073_http.py``.
"""

from __future__ import annotations

import ast
import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.security_governance import inferences, rules, vocabulary as vocab
from dsr.security_governance.engine import ConfidentialEngine
from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

NOW = datetime(2026, 10, 3, 14, 0, 0, tzinfo=timezone.utc)

FEATURE_MODULE = "dsr.features.wf073_apply_confidential_view_and_block_screenshot"
DOMAIN_PACKAGE = "dsr.security_governance"


class Clock:
    """A clock the test moves by hand.

    Every stamp this workflow writes comes from here, which is what makes the "the
    seed's own numbers are true" assertions below meaningful: they can look for a known
    instant and know it would have appeared if it appeared anywhere.
    """

    def __init__(self, start: datetime = NOW) -> None:
        self.at = start

    def __call__(self) -> datetime:
        return self.at

    def advance(self, **kwargs: float) -> datetime:
        self.at = self.at + timedelta(**kwargs)
        return self.at


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def engine(clock: Clock, store: RecordStore) -> ConfidentialEngine:
    store.create(
        "room", {"name": "Northwind data room"}, record_id="room_a", actor="dana", source="fixture"
    )
    store.create(
        "room", {"name": "Halcyon data room"}, record_id="room_b", actor="dana", source="fixture"
    )
    return ConfidentialEngine(store, now=clock)


def make_link(
    engine: ConfidentialEngine,
    *,
    room_id: str = "room_a",
    confidential: bool | None = None,
    protection: bool | None = None,
    preset_id: str | None = None,
    title: str = "Governed link",
    overrides: dict | None = None,
) -> dict:
    """Create one governed link.

    A flag passed as a keyword is set only when it is not ``None``, because ``None``
    is the ordinary "the caller did not mention this" case for the two boolean
    helpers. A caller that genuinely wants to send an explicit ``null`` passes it in
    ``overrides``, which is merged last and therefore always wins.
    """

    payload: dict = {"title": title}
    if confidential is not None:
        payload[vocab.CONFIDENTIAL_VIEW] = confidential
    if protection is not None:
        payload[vocab.SCREENSHOT_PROTECTION] = protection
    if preset_id is not None:
        payload["preset_id"] = preset_id
    payload.update(overrides or {})
    return engine.create_link(room_id, payload, source="fixture", actor="dana")


# --------------------------------------------------------------------------- #
# the two defaults
# --------------------------------------------------------------------------- #


class TestDefaults:
    def test_both_defaults_are_false(self):
        """The OpenAPI evidence names both, and it names them false."""
        assert vocab.DEFAULT_CONFIDENTIAL_VIEW is False
        assert vocab.DEFAULT_SCREENSHOT_PROTECTION is False

    def test_a_link_created_with_no_flags_is_both_off(self, engine: ConfidentialEngine):
        link = make_link(engine)
        assert link["flags"] == {
            vocab.CONFIDENTIAL_VIEW: False,
            vocab.SCREENSHOT_PROTECTION: False,
        }
        assert link["controls"] == []

    def test_the_flag_is_stored_as_a_boolean_not_a_string(self, engine: ConfidentialEngine, store):
        link = make_link(engine, confidential=True)
        stored = store.get(link["id"])["data"]
        assert stored[vocab.CONFIDENTIAL_VIEW] is True

    def test_a_string_that_is_not_a_cli_spelling_is_refused(self, engine: ConfidentialEngine):
        with pytest.raises(rules.ConfidentialError) as caught:
            make_link(engine, confidential="maybe")
        assert vocab.CONFIDENTIAL_VIEW in caught.value.errors

    def test_the_string_false_is_never_coerced_to_true(self, engine: ConfidentialEngine):
        """The defect this rule exists to prevent: a control silently the wrong way round."""
        link = make_link(engine, confidential="false")
        assert link["flags"][vocab.CONFIDENTIAL_VIEW] is False

    def test_an_integer_beyond_zero_and_one_is_refused(self, engine: ConfidentialEngine):
        """2 is truthy, and accepting it would honour a value nobody intended."""
        with pytest.raises(rules.ConfidentialError):
            make_link(engine, protection=2)

    def test_zero_and_one_are_accepted_as_booleans(self, engine: ConfidentialEngine):
        """A JSON round trip through some clients produces 0 and 1 for a boolean."""
        assert make_link(engine, confidential=1)["flags"][vocab.CONFIDENTIAL_VIEW] is True
        assert make_link(engine, confidential=0)["flags"][vocab.CONFIDENTIAL_VIEW] is False

    def test_the_cli_spelling_is_accepted_because_the_specification_names_it(self):
        assert rules.coerce_flag("on") is True
        assert rules.coerce_flag("off") is False
        assert rules.coerce_flag("ON") is True
        assert rules.coerce_flag("OFF") is False

    def test_null_means_the_caller_did_not_say_not_false(self):
        assert rules.coerce_flag(None) is None


# --------------------------------------------------------------------------- #
# the tri-state update
# --------------------------------------------------------------------------- #


class TestTriState:
    def test_an_omitted_field_is_left_alone(self, engine: ConfidentialEngine):
        link = make_link(engine, confidential=True, protection=True)
        updated = engine.update_link(
            link["id"], {vocab.CONFIDENTIAL_VIEW: False}, source="t", actor="d"
        )
        assert updated["flags"][vocab.CONFIDENTIAL_VIEW] is False
        assert updated["flags"][vocab.SCREENSHOT_PROTECTION] is True, (
            "a patch that set one flag reset the other; that is the defect the tri-state "
            "update exists to prevent"
        )

    def test_an_explicit_null_returns_it_to_the_documented_default(
        self, engine: ConfidentialEngine
    ):
        link = make_link(engine, confidential=True)
        updated = engine.update_link(
            link["id"], {vocab.CONFIDENTIAL_VIEW: None}, source="t", actor="d"
        )
        assert updated["flags"][vocab.CONFIDENTIAL_VIEW] is False

    def test_sending_the_field_is_not_the_same_request_as_omitting_it(
        self, engine: ConfidentialEngine
    ):
        link = make_link(engine, confidential=True)
        omitted = engine.update_link(link["id"], {}, source="t", actor="d")
        cleared = engine.update_link(
            link["id"], {vocab.CONFIDENTIAL_VIEW: None}, source="t", actor="d"
        )
        assert omitted["flags"][vocab.CONFIDENTIAL_VIEW] is True
        assert cleared["flags"][vocab.CONFIDENTIAL_VIEW] is False

    def test_the_cli_spelling_works_on_a_patch_too(self, engine: ConfidentialEngine):
        link = make_link(engine)
        on = engine.update_link(link["id"], {vocab.CONFIDENTIAL_VIEW: "on"}, source="t", actor="d")
        off = engine.update_link(
            link["id"], {vocab.CONFIDENTIAL_VIEW: "off"}, source="t", actor="d"
        )
        assert on["flags"][vocab.CONFIDENTIAL_VIEW] is True
        assert off["flags"][vocab.CONFIDENTIAL_VIEW] is False

    def test_a_rejected_patch_writes_nothing(self, engine: ConfidentialEngine, store: RecordStore):
        link = make_link(engine, confidential=True)
        before = len(store.audit(limit=100))
        with pytest.raises(rules.ConfidentialError):
            engine.update_link(
                link["id"], {vocab.CONFIDENTIAL_VIEW: "perhaps"}, source="t", actor="d"
            )
        assert len(store.audit(limit=100)) == before, (
            "an audit trail carrying a row for a request that changed nothing is a trail "
            "a reader has to learn to discount"
        )
        assert engine.read_link(link["id"])["flags"][vocab.CONFIDENTIAL_VIEW] is True


# --------------------------------------------------------------------------- #
# in place, and the URL unaffected
# --------------------------------------------------------------------------- #


class TestInPlace:
    def test_the_id_and_the_room_survive_a_toggle(self, engine: ConfidentialEngine):
        link = make_link(engine, confidential=True)
        updated = engine.update_link(
            link["id"], {vocab.CONFIDENTIAL_VIEW: False}, source="t", actor="d"
        )
        assert updated["id"] == link["id"]
        assert updated["room_id"] == "room_a"

    def test_a_field_this_workflow_does_not_interpret_survives_a_toggle(
        self, engine: ConfidentialEngine, store: RecordStore
    ):
        """A link created here may be the same link another workflow's gate wrote to."""
        link = engine.create_link(
            "room_a",
            {"title": "Shared", "watermark_config": {"text": "Northwind"}, "allow_download": False},
            source="fixture",
            actor="dana",
        )
        engine.update_link(link["id"], {vocab.CONFIDENTIAL_VIEW: True}, source="t", actor="d")
        stored = store.get(link["id"])["data"]
        assert stored["watermark_config"] == {"text": "Northwind"}
        assert stored["allow_download"] is False

    def test_a_toggle_does_not_disturb_the_other_room(self, engine: ConfidentialEngine):
        first = make_link(engine, room_id="room_a", confidential=True)
        second = make_link(engine, room_id="room_b")
        engine.update_link(first["id"], {vocab.CONFIDENTIAL_VIEW: False}, source="t", actor="d")
        assert engine.read_link(second["id"])["flags"][vocab.CONFIDENTIAL_VIEW] is False

    def test_the_projection_always_carries_both_flags(self, engine: ConfidentialEngine):
        link = make_link(engine, confidential=True)
        assert set(link["flags"]) == set(vocab.FLAGS)

    def test_links_are_scoped_to_a_room(self, engine: ConfidentialEngine):
        make_link(engine, room_id="room_a")
        make_link(engine, room_id="room_b")
        assert len(engine.links("room_a")) == 1
        assert len(engine.links("room_b")) == 1
        assert len(engine.links()) == 2

    def test_an_unknown_link_is_not_found(self, engine: ConfidentialEngine):
        with pytest.raises(rules.LinkNotFound):
            engine.read_link("wf073_confidential_link_does_not_exist")

    def test_a_row_from_another_workflow_is_not_read_as_a_link(
        self, engine: ConfidentialEngine, store
    ):
        """A feature may not read across into another workflow's collection."""
        other = store.create("wf069_gated_link", {"password": "x"}, actor="dana", source="fixture")
        with pytest.raises(rules.LinkNotFound):
            engine.read_link(other["id"])


# --------------------------------------------------------------------------- #
# governance baselines
# --------------------------------------------------------------------------- #


class TestGovernanceBaselines:
    def test_a_preset_carries_its_id(self, engine: ConfidentialEngine):
        preset = engine.create_preset({"name": "Baseline"}, source="fixture", actor="dana")
        assert preset["id"], (
            "a baseline whose id is None cannot be attached to a link, so the one number "
            "a caller needs would be the one number missing"
        )

    def test_a_link_seeded_from_a_baseline_inherits_it(self, engine: ConfidentialEngine):
        preset = engine.create_preset(
            {
                "name": "Baseline",
                "fields": {vocab.CONFIDENTIAL_VIEW: True, vocab.SCREENSHOT_PROTECTION: True},
            },
            source="fixture",
            actor="dana",
        )
        link = make_link(engine, preset_id=preset["id"])
        assert link["flags"] == {vocab.CONFIDENTIAL_VIEW: True, vocab.SCREENSHOT_PROTECTION: True}

    def test_an_explicit_false_overrides_the_baseline(self, engine: ConfidentialEngine):
        preset = engine.create_preset(
            {"name": "Baseline", "fields": {vocab.CONFIDENTIAL_VIEW: True}},
            source="fixture",
            actor="dana",
        )
        link = make_link(engine, preset_id=preset["id"], confidential=False)
        assert link["flags"][vocab.CONFIDENTIAL_VIEW] is False

    def test_an_explicit_null_returns_to_the_default_not_to_the_baseline(
        self, engine: ConfidentialEngine
    ):
        preset = engine.create_preset(
            {"name": "Baseline", "fields": {vocab.CONFIDENTIAL_VIEW: True}},
            source="fixture",
            actor="dana",
        )
        link = make_link(engine, preset_id=preset["id"], overrides={vocab.CONFIDENTIAL_VIEW: None})
        assert link["flags"][vocab.CONFIDENTIAL_VIEW] is False

    def test_a_baseline_the_link_overrides_the_rest_of_still_applies(
        self, engine: ConfidentialEngine
    ):
        preset = engine.create_preset(
            {
                "name": "Baseline",
                "fields": {vocab.CONFIDENTIAL_VIEW: True, vocab.SCREENSHOT_PROTECTION: True},
            },
            source="fixture",
            actor="dana",
        )
        link = make_link(engine, preset_id=preset["id"], confidential=False)
        assert link["flags"][vocab.SCREENSHOT_PROTECTION] is True

    def test_an_unknown_baseline_is_refused(self, engine: ConfidentialEngine):
        with pytest.raises(rules.PresetNotFound):
            make_link(engine, preset_id="wf073_confidential_preset_nope")

    def test_a_row_from_another_workflow_is_not_read_as_a_baseline(
        self, engine: ConfidentialEngine, store: RecordStore
    ):
        other = store.create(
            "wf069_gate_preset", {"name": "Theirs"}, actor="dana", source="fixture"
        )
        with pytest.raises(rules.PresetNotFound):
            make_link(engine, preset_id=other["id"])

    def test_a_baseline_stores_fields_it_does_not_interpret(
        self, engine: ConfidentialEngine, store
    ):
        preset = engine.create_preset(
            {"name": "Baseline", "fields": {vocab.CONFIDENTIAL_VIEW: True, "password": "carried"}},
            source="fixture",
            actor="dana",
        )
        record = store.get(preset["id"])
        assert record["data"]["fields"]["password"] == "carried", (
            "the other sixteen preset-covered fields belong to other workflows; storing "
            "them is what makes a baseline survive the trip"
        )

    def test_a_baseline_reports_both_flags_even_when_it_names_one(self, engine: ConfidentialEngine):
        preset = engine.create_preset(
            {"name": "Baseline", "fields": {vocab.CONFIDENTIAL_VIEW: True}},
            source="fixture",
            actor="dana",
        )
        assert preset["fields"] == {
            vocab.CONFIDENTIAL_VIEW: True,
            vocab.SCREENSHOT_PROTECTION: False,
        }


# --------------------------------------------------------------------------- #
# the focus band
# --------------------------------------------------------------------------- #


class TestFocusBand:
    def test_exactly_one_band_is_sharp_at_a_position(self):
        geometry = rules.band_geometry(2000, 800, viewport_top=500)
        assert sum(1 for band in geometry["bands"] if band["sharp"]) == 1

    def test_the_sharp_band_is_the_one_containing_the_scroll(self):
        geometry = rules.band_geometry(2000, 800, viewport_top=500)
        sharp = geometry["bands"][geometry["sharp_index"]]
        assert sharp["top"] <= 500 < sharp["bottom"]

    def test_the_band_is_narrow(self):
        """ "A narrow band of each page at a time." A quarter or more would be a paragraph."""
        geometry = rules.band_geometry(2000, 800, viewport_top=0)
        assert geometry["sharp_fraction"] <= vocab.BAND_FRACTION
        assert geometry["sharp_fraction"] < 0.25

    def test_a_single_screenshot_cannot_capture_a_whole_page(self):
        """ "so a single screenshot cannot capture a whole page" - the property the control
        exists for. Every page above the band threshold must have a blurred region."""
        geometry = rules.band_geometry(2000, 800, viewport_top=0)
        assert geometry["blurred_count"] >= 1
        assert geometry["sharp_fraction"] < 1.0

    def test_a_blurred_band_carries_no_glyphs(self):
        """ "the full-resolution page never reaches the client for out-of-band regions"."""
        geometry = rules.band_geometry(2000, 800, viewport_top=0)
        for index, _band in enumerate(geometry["bands"]):
            resolved = rules.resolve_band(geometry, index)
            if not resolved["sharp"]:
                assert resolved["text"] is None, "a blurred band leaked text"
                assert resolved["text_length"] == 0

    def test_bands_overlap_so_text_stays_continuous(self):
        geometry = rules.band_geometry(2000, 800, viewport_top=0)
        for first, second in zip(geometry["bands"], geometry["bands"][1:], strict=False):
            assert second["top"] < first["bottom"], (
                "no overlap makes the boundary a hard edge, and a reader scrolling across "
                "it watches a line disappear and reappear"
            )

    def test_no_position_on_the_page_is_unresolved(self):
        for top in range(0, 2000, 37):
            geometry = rules.band_geometry(2000, 800, viewport_top=top)
            assert geometry["sharp_index"] >= 0
            assert sum(1 for band in geometry["bands"] if band["sharp"]) == 1

    def test_scrolling_past_the_end_resolves_the_last_band(self):
        """ "the bottom of a document is a place a reader reaches, and a viewer that went
        blank there would read as a broken product"."""
        geometry = rules.band_geometry(2000, 800, viewport_top=99999)
        assert geometry["bands"][geometry["sharp_index"]]["bottom"] == 2000

    def test_a_negative_scroll_resolves_the_first_band(self):
        geometry = rules.band_geometry(2000, 800, viewport_top=-500)
        assert geometry["sharp_index"] == 0

    def test_a_short_page_is_delivered_whole_and_says_why(self):
        """ "there is no out-of-band region on it, so there is nothing this control can
        withhold". The reason is reported rather than implied by the geometry."""
        geometry = rules.band_geometry(vocab.SMALL_PAGE_PIXELS - 1, 800, viewport_top=0)
        assert len(geometry["bands"]) == 1
        assert geometry["bands"][0]["reason"] == "page_shorter_than_one_band"
        assert geometry["sharp_fraction"] == 1.0

    def test_the_reason_survives_band_resolution(self):
        """The engine resolves every band through :func:`resolve_band`, so a ``reason``
        the geometry set and the resolver drops would never reach a caller. Found by
        driving the live flow over real HTTP, not by a unit test."""
        geometry = rules.band_geometry(vocab.SMALL_PAGE_PIXELS - 1, 800, viewport_top=0)
        resolved = rules.resolve_band(geometry, 0)
        assert resolved["reason"] == "page_shorter_than_one_band"

    def test_a_normal_band_carries_no_reason(self):
        """Only the short-page case has one. A band that carried a reason on every page
        would train a reader to ignore the field."""
        geometry = rules.band_geometry(2000, 800, viewport_top=0)
        assert "reason" not in rules.resolve_band(geometry, geometry["sharp_index"])

    def test_a_fractional_viewport_is_rounded_rather_than_refused(self):
        """ "a browser reports a fractional CSS pixel, from a zoom level or a device
        pixel ratio, and the band arithmetic is measured in whole pixels"."""
        geometry = rules.band_geometry(2000.4, 800.6, viewport_top=10.2)
        assert geometry["page_height"] == 2000
        assert geometry["viewport_height"] == 801

    def test_a_dimension_that_is_not_a_number_is_refused(self):
        with pytest.raises(rules.ConfidentialError):
            rules.band_geometry("tall", 800)

    def test_an_unknown_band_index_is_refused(self):
        geometry = rules.band_geometry(2000, 800, viewport_top=0)
        with pytest.raises(rules.ConfidentialError) as caught:
            rules.resolve_band(geometry, 999)
        assert "band_index" in caught.value.errors

    def test_the_band_arithmetic_keeps_a_wide_viewport_readable(self):
        """A viewport taller than the band still leaves the rest of the page blurred."""
        geometry = rules.band_geometry(4000, 3000, viewport_top=0)
        assert geometry["blurred_count"] >= 1


# --------------------------------------------------------------------------- #
# rendering a page against a viewport
# --------------------------------------------------------------------------- #


class TestRenderPage:
    def test_a_link_with_the_control_off_is_answered_not_banded(self, engine: ConfidentialEngine):
        link = make_link(engine)
        rendered = engine.render_page(link["id"], {"page_height": 2000, "viewport_height": 800})
        assert rendered["applied"] is False
        assert rendered["reason"] == "confidential_view_off"

    def test_the_control_off_response_has_the_same_shape_as_the_control_on_one(
        self, engine: ConfidentialEngine
    ):
        """A page that has to branch on a missing key is a page that eventually breaks."""
        off = engine.render_page(
            make_link(engine)["id"], {"page_height": 2000, "viewport_height": 800}
        )
        on = engine.render_page(
            make_link(engine, confidential=True)["id"],
            {"page_height": 2000, "viewport_height": 800},
        )
        assert set(off) == set(on), (
            "a page that has to branch on a missing key is a page that eventually breaks; "
            "the control-off response carries the same keys as the control-on one"
        )

    def test_a_band_on_response_names_its_shape(self, engine: ConfidentialEngine):
        link = make_link(engine, confidential=True)
        rendered = engine.render_page(link["id"], {"page_height": 2000, "viewport_height": 800})
        assert rendered["shape"] == vocab.CHOSEN_RENDER_SHAPE

    def test_only_the_sharp_band_carries_a_text_length(self, engine: ConfidentialEngine):
        link = make_link(engine, confidential=True)
        rendered = engine.render_page(
            link["id"],
            {"page_height": 2000, "viewport_height": 800, "page_text_length": 2400},
        )
        sharp = rendered["bands"][rendered["sharp_index"]]
        blurred = [band for band in rendered["bands"] if not band["sharp"]]
        assert sharp["text_length"] == 2400
        assert all(band["text_length"] == 0 for band in blurred)

    def test_rendering_never_refuses_a_viewer(self, engine: ConfidentialEngine):
        """Confidential view is a rendering transformation, not a gate. Gating a link is
        WF-069's domain, and a control that refused would be a weaker second gate."""
        link = make_link(engine, confidential=True)
        assert engine.render_page(link["id"], {"page_height": 2000, "viewport_height": 800})

    def test_rendering_an_unknown_link_is_not_found(self, engine: ConfidentialEngine):
        with pytest.raises(rules.LinkNotFound):
            engine.render_page("nope", {"page_height": 2000, "viewport_height": 800})

    def test_the_page_index_is_echoed_back(self, engine: ConfidentialEngine):
        link = make_link(engine, confidential=True)
        rendered = engine.render_page(
            link["id"], {"page": 3, "page_height": 2000, "viewport_height": 800}
        )
        assert rendered["page"] == 3


# --------------------------------------------------------------------------- #
# the shortcut list
# --------------------------------------------------------------------------- #


class TestShortcutList:
    def test_the_documented_shortcuts_are_present(self):
        names = {shortcut["name"] for shortcut in vocab.SHORTCUT_KEYS}
        assert {"Command-Shift-3", "Command-Shift-5", "Print Screen"} <= names

    def test_print_screen_is_named_and_marked_unblockable(self):
        """A control that listed only the blockable keys would let a seller read "this
        link blocks screenshots" as true when the first thing a buyer tries still works."""
        entry = rules.shortcut_for([vocab.PRINT_SCREEN])
        assert entry["name"] == "Print Screen"
        assert entry["blockable"] is False

    def test_print_screen_is_excluded_from_the_blockable_list(self):
        assert "Print Screen" not in {s["name"] for s in rules.blockable_shortcuts()}

    def test_the_unblockable_list_names_it_rather_than_hiding_it(self):
        assert "Print Screen" in {s["name"] for s in rules.unblockable_shortcuts()}

    def test_command_shift_five_is_both_a_screenshot_and_a_recording(self):
        entry = rules.shortcut_for(["meta", "shift", "5"])
        assert entry["also_recording"] is True

    def test_modifier_order_does_not_matter(self):
        """A browser reports modifiers as flags and the character separately, and a
        sequence assembled in another order would silently match nothing."""
        assert rules.shortcut_for(["meta", "shift", "3"]) == rules.shortcut_for(
            ["3", "shift", "meta"]
        )

    def test_an_unknown_sequence_matches_nothing(self):
        assert rules.shortcut_for(["meta", "alt", "q"]) is None

    def test_a_non_sequence_matches_nothing(self):
        assert rules.shortcut_for("print_screen") is None
        assert rules.shortcut_for([]) is None

    def test_every_shortcut_names_an_action_the_vocabulary_declares(self):
        for shortcut in vocab.SHORTCUT_KEYS:
            assert shortcut["action"] in vocab.CAPTURE_ACTIONS


# --------------------------------------------------------------------------- #
# an attempt is a report
# --------------------------------------------------------------------------- #


class TestCaptureAttempts:
    def test_a_blockable_shortcut_records_as_blocked(self, engine: ConfidentialEngine):
        link = make_link(engine, protection=True)
        attempt = engine.report_attempt(
            link["id"], {"keys": ["meta", "shift", "5"]}, source="t", actor="v"
        )
        assert attempt["outcome"] == vocab.ATTEMPT_BLOCKED

    def test_print_screen_records_as_reported_not_blocked(self, engine: ConfidentialEngine):
        """A row claiming a capture was prevented would be a claim the product cannot
        support."""
        link = make_link(engine, protection=True)
        attempt = engine.report_attempt(
            link["id"], {"keys": ["print_screen"]}, source="t", actor="v"
        )
        assert attempt["outcome"] == vocab.ATTEMPT_REPORTED
        assert attempt["blockable"] is False

    def test_the_caller_cannot_choose_the_outcome(self, engine: ConfidentialEngine):
        """Letting the browser report itself as blocked would make the log a
        self-assessment."""
        link = make_link(engine, protection=True)
        attempt = engine.report_attempt(
            link["id"],
            {"keys": ["meta", "shift", "5"], "outcome": vocab.ATTEMPT_BLOCKED, "blockable": False},
            source="t",
            actor="v",
        )
        assert attempt["blockable"] is True

    def test_an_unknown_shortcut_is_refused_rather_than_stored_blank(
        self, engine: ConfidentialEngine, store
    ):
        """A blank name in a governance log is worse than a refusal."""
        link = make_link(engine, protection=True)
        before = len(store.list(vocab.ATTEMPT_COLLECTION))
        with pytest.raises(rules.UnknownShortcut):
            engine.report_attempt(link["id"], {"keys": ["meta", "alt", "q"]}, source="t", actor="v")
        assert len(store.list(vocab.ATTEMPT_COLLECTION)) == before

    def test_an_attempt_with_no_keys_is_refused(self, engine: ConfidentialEngine):
        link = make_link(engine, protection=True)
        with pytest.raises(rules.UnknownShortcut):
            engine.report_attempt(link["id"], {}, source="t", actor="v")

    def test_the_shortcut_key_is_accepted_as_well_as_keys(self, engine: ConfidentialEngine):
        link = make_link(engine, protection=True)
        attempt = engine.report_attempt(
            link["id"], {"shortcut": ["print_screen"]}, source="t", actor="v"
        )
        assert attempt["shortcut"] == "Print Screen"

    def test_the_attempt_records_whether_the_control_was_on(self, engine: ConfidentialEngine):
        """The board has to be able to say that an attempt happened on a link that was
        not being guarded at all."""
        guarded = make_link(engine, protection=True)
        open_link = make_link(engine, protection=False)
        assert (
            engine.report_attempt(guarded["id"], {"keys": ["print_screen"]}, source="t", actor="v")[
                "screenshot_protection_on"
            ]
            is True
        )
        assert (
            engine.report_attempt(
                open_link["id"], {"keys": ["print_screen"]}, source="t", actor="v"
            )["screenshot_protection_on"]
            is False
        )

    def test_attempts_are_filterable_by_room_and_link(self, engine: ConfidentialEngine):
        first = make_link(engine, room_id="room_a")
        second = make_link(engine, room_id="room_b")
        engine.report_attempt(first["id"], {"keys": ["print_screen"]}, source="t", actor="v")
        engine.report_attempt(second["id"], {"keys": ["print_screen"]}, source="t", actor="v")
        assert len(engine.attempts(room_id="room_a")) == 1
        assert len(engine.attempts(link_id=second["id"])) == 1

    def test_an_attempt_on_an_unknown_link_is_not_found(self, engine: ConfidentialEngine):
        with pytest.raises(rules.LinkNotFound):
            engine.report_attempt("nope", {"keys": ["print_screen"]}, source="t", actor="v")


# --------------------------------------------------------------------------- #
# the board
# --------------------------------------------------------------------------- #


class TestSummary:
    def test_the_counts_match_the_links(self, engine: ConfidentialEngine):
        make_link(engine, confidential=True)
        make_link(engine, protection=True)
        make_link(engine)
        counts = engine.summary()
        assert counts["links"] == 3
        assert counts["confidential_view"] == 1
        assert counts["screenshot_protection"] == 1
        assert counts["no_controls"] == 1

    def test_the_both_on_count_is_separate_from_either(self, engine: ConfidentialEngine):
        make_link(engine, confidential=True, protection=True)
        counts = engine.summary()
        assert counts["both_controls"] == 1
        assert counts["confidential_view"] == 1
        assert counts["screenshot_protection"] == 1

    def test_the_board_separates_blockable_from_unblockable_attempts(
        self, engine: ConfidentialEngine
    ):
        link = make_link(engine, protection=True)
        engine.report_attempt(link["id"], {"keys": ["meta", "shift", "5"]}, source="t", actor="v")
        engine.report_attempt(link["id"], {"keys": ["print_screen"]}, source="t", actor="v")
        counts = engine.summary()
        assert counts["attempts"] == 2
        assert counts["attempts_blockable"] == 1
        assert counts["attempts_unblockable"] == 1

    def test_the_tally_is_sorted_largest_first_then_alphabetically(
        self, engine: ConfidentialEngine
    ):
        """Sorted so the page and the API agree on the order without either re-sorting."""
        link = make_link(engine, protection=True)
        engine.report_attempt(link["id"], {"keys": ["print_screen"]}, source="t", actor="v")
        engine.report_attempt(link["id"], {"keys": ["print_screen"]}, source="t", actor="v")
        engine.report_attempt(link["id"], {"keys": ["meta", "shift", "3"]}, source="t", actor="v")
        tally = engine.summary()["by_shortcut"]
        assert list(tally.values()) == sorted(tally.values(), reverse=True)
        assert tally["Print Screen"] == 2

    def test_an_empty_board_reads_zero_rather_than_failing(self, engine: ConfidentialEngine):
        counts = engine.summary()
        assert counts["links"] == 0
        assert counts["attempts"] == 0
        assert counts["by_shortcut"] == {}


# --------------------------------------------------------------------------- #
# the honesty rule
# --------------------------------------------------------------------------- #


class TestTheSpecificationBar:
    """The specification says: "Screenshot blocking is largely unenforceable from a
    browser; treat as deterrence, and do not sell it as protection."

    These tests are the machine-checkable half of that sentence. They fail if a later
    edit upgrades the wording into a protection claim, which is the one thing the
    specification forbids.
    """

    def test_the_effect_is_deterrent_not_protection(self):
        assert vocab.EFFECT == "deterrent"

    def test_the_limitation_says_neither_control_prevents_a_capture(self):
        assert "Neither one prevents it" in vocab.LIMITATION

    def test_the_limitation_names_the_browsers_own_limit(self):
        assert "largely unenforceable from a browser" in vocab.LIMITATION

    def test_every_projection_carries_the_effect_and_the_limitation(
        self, engine: ConfidentialEngine
    ):
        link = make_link(engine, confidential=True, protection=True)
        for payload in (
            link,
            engine.read_link(link["id"]),
            engine.summary(),
            engine.render_page(link["id"], {"page_height": 2000, "viewport_height": 800}),
            engine.report_attempt(link["id"], {"keys": ["print_screen"]}, source="t", actor="v"),
        ):
            assert payload[vocab.EFFECT_FIELD] == vocab.EFFECT, payload
            assert vocab.LIMITATION_FIELD in payload, payload

    def test_no_response_or_copy_calls_the_control_protection(self):
        """The vendor named the second flag ``enable_screenshot_protection``. This
        product adopts the name, not the claim."""
        forbidden = (
            "protects the document",
            "prevents screenshots",
            "stops a capture",
            "secure your",
        )
        for module_path in [*_domain_paths(), _feature_path()]:
            text = module_path.read_text(encoding="utf-8").lower()
            for phrase in forbidden:
                assert phrase not in text, f"{module_path.name} claims protection: {phrase!r}"

    def test_the_page_would_not_sell_it_either(self):
        """Same rule against the shipped frontend copy.

        Test files are excluded, and deliberately so. A test has to be able to write
        the phrase it is forbidding in order to assert the phrase is absent, and a guard
        that flagged its own test file would fail on correct work. What ships is the
        page, its api wrapper and its primitives; a banned word there is a defect.
        """
        page = _frontend_dir()
        shipped = [
            path
            for path in page.glob("*")
            if path.suffix in (".jsx", ".js") and ".test." not in path.name
        ]
        assert shipped, "no shipped frontend files were found, so this test measures nothing"
        for path in shipped:
            lowered = path.read_text(encoding="utf-8").lower()
            for phrase in ("prevents screenshots", "stops a capture", "cannot be screenshotted"):
                assert phrase not in lowered, f"{path.name} claims protection: {phrase!r}"

    def test_the_page_states_the_limitation_in_words(self):
        """The positive half of the same rule. A page that merely avoids the word
        "protection" has not met the specification's instruction either - it has to say
        what the controls are worth."""
        page = _frontend_dir()
        shipped = [
            path
            for path in page.glob("*")
            if path.suffix in (".jsx", ".js") and ".test." not in path.name
        ]
        joined = " ".join(path.read_text(encoding="utf-8") for path in shipped).lower()
        assert "deterrence, not protection" in joined
        assert "does not prevent" in joined or "neither one prevents" in joined


# --------------------------------------------------------------------------- #
# the architectural guards
# --------------------------------------------------------------------------- #


class TestArchitecture:
    def test_the_domain_package_imports_nothing_but_the_store(self):
        """The guard the brief names by name.

        The rule is about the dependency direction, not about banning the standard
        library. ``collections.abc`` and ``datetime`` are ordinary Python; what would
        be a defect is a domain module reaching for ``dsr.api`` (which reintroduces the
        coupling the host removes) or for ``dsr.store`` from somewhere that does not
        need it. So the standard library is allowed and every ``dsr`` import is checked
        against the list of what this workflow is permitted to depend on.
        """
        allowed = {
            "dsr.store",
            "dsr.security_governance",
        }
        for path in _domain_paths():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if not name.startswith("dsr"):
                        continue
                    assert name in allowed, (
                        f"{path.name} imports {name}. The domain module may depend on "
                        f"the store and on itself, and on nothing else inside dsr."
                    )

    def test_the_domain_package_never_imports_the_app(self):
        for path in _domain_paths():
            text = path.read_text(encoding="utf-8")
            assert "from dsr.api" not in text and "import dsr.api" not in text

    def test_the_domain_package_never_opens_sqlite(self):
        for path in _domain_paths():
            assert "import sqlite3" not in path.read_text(encoding="utf-8")

    def test_the_feature_module_never_imports_the_app(self):
        """The enforced test in test_features.py checks every module; this one states why
        for this feature."""
        assert "from dsr.api" not in _feature_path().read_text(encoding="utf-8")

    def test_the_feature_module_exports_the_documented_surface(self):
        module = importlib.import_module(FEATURE_MODULE)
        assert module.FEATURE["id"]
        assert module.FEATURE["ticket"] == "WF-073"
        assert module.router.prefix == "/api/wf-073"
        assert set(module.EXCEPTION_HANDLERS) == {
            rules.ConfidentialError,
            rules.LinkNotFound,
            rules.PresetNotFound,
            rules.UnknownShortcut,
        }

    def test_the_records_are_plain_json_with_no_migration(self):
        """A team adding a field must need no coordination with anyone."""
        engine_columns = (
            AuditedDatabase()
            ._conn.execute(  # noqa: SLF001
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            .fetchall()
        )
        names = {row["name"] for row in engine_columns}
        assert "records" in names
        assert not any(name.startswith("wf073") for name in names), (
            "this workflow must add its data as records.data, not as a typed column"
        )


# --------------------------------------------------------------------------- #
# the recorded derivations
# --------------------------------------------------------------------------- #


class TestInferences:
    def test_every_open_question_was_recorded(self):
        assert inferences.count() >= 8

    def test_the_two_the_specification_marked_inferred_are_recorded(self):
        assert "INFERRED_ACCESS_CONTROLS_PANEL" in inferences.DECISIONS

    def test_the_two_the_specification_named_but_did_not_choose_are_recorded(self):
        assert "DERIVED_DELIVERY_SHAPE" in inferences.DECISIONS
        assert "DERIVED_SHORTCUT_LIST" in inferences.DECISIONS

    def test_every_record_names_a_rejected_alternative(self):
        """A derivation with no rejected option recorded is a guess wearing a
        derivation's clothes, and a reviewer cannot tell the two apart."""
        for key, decision in inferences.DECISIONS.items():
            assert decision.get("options"), key
            assert decision.get("chosen") in decision["options"], key
            assert decision.get("rejected_because"), key
            assert decision.get("cost_of_the_choice"), key

    def test_the_chosen_shape_is_the_one_that_is_built(self):
        chosen = inferences.DECISIONS["DERIVED_DELIVERY_SHAPE"]["chosen"]
        assert chosen == "viewport_bands"
        assert vocab.CHOSEN_RENDER_SHAPE == "viewport_bands"

    def test_both_shapes_stay_in_the_vocabulary(self):
        """ "so a later workflow can add the grid beside this one without changing this
        one's data"."""
        assert set(vocab.RENDER_SHAPES) == {"viewport_bands", "tile_shards"}

    def test_describe_returns_every_decision_with_its_id(self):
        described = inferences.describe()
        assert len(described) == inferences.count()
        assert {item["id"] for item in described} == set(inferences.DECISIONS)

    def test_describe_one_returns_nothing_for_an_unknown_id(self):
        assert inferences.describe_one("NOPE") == {}


# --------------------------------------------------------------------------- #
# the seed
# --------------------------------------------------------------------------- #


class TestSeed:
    def test_the_return_string_encodes_as_cp1252(self, engine: ConfidentialEngine):
        """The seeder prints this to a Windows console, and one RIGHTWARDS ARROW in a
        recovered feature's return string broke the whole seeder."""
        module = importlib.import_module(FEATURE_MODULE)
        message = module.seed(engine.store.db, {"room_ids": [("room_a", "Northwind")], "now": NOW})
        message.encode("cp1252")

    def test_the_seed_states_that_it_is_deterrence(self, engine: ConfidentialEngine):
        module = importlib.import_module(FEATURE_MODULE)
        message = module.seed(engine.store.db, {"room_ids": [("room_a", "Northwind")], "now": NOW})
        assert "deterrence, not protection" in message

    def test_the_seed_names_a_state_that_is_not_all_success(self, engine: ConfidentialEngine):
        """A demo row that only shows blocked attempts would misrepresent the control it
        is demonstrating, so the seed must print the unblockable count too."""
        module = importlib.import_module(FEATURE_MODULE)
        message = module.seed(engine.store.db, {"room_ids": [("room_a", "Northwind")], "now": NOW})
        assert "a page cannot" in message

    def test_the_seed_returns_nothing_without_a_room(self, engine: ConfidentialEngine):
        module = importlib.import_module(FEATURE_MODULE)
        assert module.seed(engine.store.db, {"room_ids": [], "now": NOW}) == ""

    def test_the_states_the_seed_claims_all_exist(self, engine: ConfidentialEngine):
        """Every number is read back from the store, so this asserts the line the seeder
        prints describes the rows it actually wrote."""
        module = importlib.import_module(FEATURE_MODULE)
        module.seed(engine.store.db, {"room_ids": [("room_a", "A"), ("room_b", "B")], "now": NOW})
        counts = engine.summary()
        assert counts["links"] == 5
        assert counts["both_controls"] == 1
        assert counts["presets"] == 1
        assert counts["attempts"] == 2
        assert counts["attempts_blockable"] == 1
        assert counts["attempts_unblockable"] == 1

    def test_the_seed_leaves_one_link_with_neither_control(self, engine: ConfidentialEngine):
        """The documented default has to be on the board as a state, not only implied."""
        module = importlib.import_module(FEATURE_MODULE)
        module.seed(engine.store.db, {"room_ids": [("room_a", "A"), ("room_b", "B")], "now": NOW})
        assert engine.summary()["no_controls"] == 2

    def test_the_seed_rotates_one_link_off_in_place(self, engine: ConfidentialEngine):
        module = importlib.import_module(FEATURE_MODULE)
        module.seed(engine.store.db, {"room_ids": [("room_a", "A"), ("room_b", "B")], "now": NOW})
        rotated = [link for link in engine.links() if link["title"].startswith("Meridian")]
        assert rotated and rotated[0]["flags"][vocab.CONFIDENTIAL_VIEW] is False

    def test_the_seed_writes_only_through_the_engine(self, engine: ConfidentialEngine, store):
        """Every row is produced by the real engine, so the demo cannot show a shape the
        HTTP routes would not produce."""
        module = importlib.import_module(FEATURE_MODULE)
        module.seed(engine.store.db, {"room_ids": [("room_a", "A")], "now": NOW})
        sources = {entry["source"] for entry in store.audit(limit=200)}
        assert sources == {"seed", "fixture"}, sources


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _domain_paths() -> list[Path]:
    return sorted(Path(vocab.__file__).parent.glob("*.py"))


def _feature_path() -> Path:
    return Path(importlib.import_module(FEATURE_MODULE).__file__ or "")


def _frontend_dir() -> Path:
    """The shipped frontend folder for this feature.

    Derived from the backend feature id rather than hard-coded, because the id is the
    one name both halves of the feature share and a hard-coded path would keep working
    after the folder was renamed. The folder name and the id are the same string in
    every other feature in this product, so no mapping is needed.
    """

    import dsr

    feature_id = importlib.import_module(FEATURE_MODULE).FEATURE["id"]
    root = Path(dsr.__file__ or "").resolve().parents[2]
    folder = root / "frontend" / "src" / "features" / feature_id
    if not folder.exists():
        pytest.skip("frontend sources are not present in this checkout")
    return folder
