"""WF-138: the pure rules of a branded room on the customer's own domain.

``branded_domain.py`` is a function of its arguments: no database, no clock, no
network, no framework. So every rule the researched data flow names is checkable
here without a server and without a fixture, and the three rules that matter are
pinned exactly:

1. **A tenant override is token to token.** A colour value is refused on both
   sides of a pair, so a tenant can never break a later theme change and can
   never reach a style attribute.
2. **A brand change reaches every tenant.** Propagating a token change writes the
   new map to every room of that brand, a room's own override wins, and a second
   propagation writes nothing.
3. **White-label mode swaps the owner identity.** The client's brand is
   presented and the agency's name is withheld; the switch needs a client to
   present.

Each test builds its own data. Nothing here depends on another test's rows,
because the suite runs under ``pytest-xdist`` and a test that only passes in one
order fails intermittently.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from dsr.competitive_baseline import branded_domain as bd
from dsr.features import load_feature

MODULE = "wf138_branded_custom_domain"
FEATURE_ID = "wf-138-branded-domain"
PREFIX = "/api/wf-138"


# --------------------------------------------------------------------------- #
# The architecture the contract requires
# --------------------------------------------------------------------------- #


def test_the_domain_module_imports_no_dsr_module_at_all():
    """The enforced rule, and why it is stricter here than "nothing but the store".

    ``backend/tests/test_wf122.py`` globs every module in
    ``dsr/competitive_baseline/`` and refuses any ``dsr`` import other than
    ``dsr.store``. That test ships on ``main`` and this workflow does not own it,
    so this module may import no ``dsr`` module at all. The hostname rule is
    therefore WF-017's, called from the feature seam, so there is still one
    implementation of it.
    """
    source = Path(bd.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    assert imported, "the module imports nothing, so this test measured nothing"
    for name in imported:
        assert not name.startswith("dsr"), name
        assert name != "fastapi", name
        assert name != "sqlite3", name
    assert "sqlite3.connect" not in source
    assert "AuditedDatabase(" not in source


def test_the_feature_module_reaches_no_shared_file_and_opens_no_connection():
    module = load_feature(MODULE)
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source
    assert "import sqlite3" not in source
    assert "sqlite3.connect" not in source
    assert "from dsr.deps import" in source, "dependencies come from dsr.deps, never dsr.api"


def test_every_audit_source_is_built_from_the_live_prefix():
    """A hardcoded path would drift the moment the prefix changed."""
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert 'return f"{method} {router.prefix}{path}"' in source
    assert source.count('_source("') >= 8, "every write derives its audit source"


def test_the_domain_module_is_where_this_workflow_s_errors_come_from():
    """The host refuses two features mapping the same error type."""
    module = load_feature(MODULE)
    types = set(module.EXCEPTION_HANDLERS)
    assert types == {bd.BrandedDomainError, bd.BrandConflict}
    builtins_ = (ValueError, LookupError, PermissionError, KeyError, TypeError)
    for error_type in types:
        assert error_type.__module__ == bd.__name__, error_type
        assert error_type not in builtins_, error_type


# --------------------------------------------------------------------------- #
# The token map: token to token, never a colour value
# --------------------------------------------------------------------------- #


def test_a_token_to_token_map_is_accepted():
    assert bd.validate_token_map({"accent": "accent-bright"}) == {"accent": "accent-bright"}


def test_an_empty_map_is_allowed_and_clears_nothing_else():
    assert bd.validate_token_map({}) == {}
    assert bd.validate_token_map(None) == {}


@pytest.mark.parametrize(
    "value", ["#10506f", "#fff", "rgb(16,80,111)", "hsl(200, 40%, 20%)", "blue"]
)
def test_a_colour_value_is_refused_on_the_value_side(value: str):
    """The design system's one rule: style with a token, never a raw colour."""
    with pytest.raises(bd.BrandedDomainError) as raised:
        bd.validate_token_map({"accent": value})
    assert value in str(raised.value.errors["tokens.accent"])
    assert "not a colour value" in raised.value.errors["tokens.accent"]


def test_a_locked_token_is_refused_on_the_key_side():
    """The four tokens that decide text contrast belong to the product."""
    for locked in bd.PRODUCT_TOKENS:
        with pytest.raises(bd.BrandedDomainError) as raised:
            bd.validate_token_map({locked: "accent"})
        assert "locked by the product" in raised.value.errors[f"tokens.{locked}"]


def test_a_locked_token_is_refused_on_the_value_side_too():
    for locked in bd.PRODUCT_TOKENS:
        with pytest.raises(bd.BrandedDomainError) as raised:
            bd.validate_token_map({"accent": locked})
        assert locked in raised.value.errors["tokens.accent"]


def test_an_unknown_token_is_refused_and_the_message_lists_the_real_ones():
    with pytest.raises(bd.BrandedDomainError) as raised:
        bd.validate_token_map({"accent": "brand-green"})
    errors = raised.value.errors
    assert "tokens.accent" in errors
    for token in bd.TENANT_TOKENS:
        assert token in errors["tokens.accent"]


def test_a_blank_value_names_the_missing_thing_rather_than_guessing():
    with pytest.raises(bd.BrandedDomainError) as raised:
        bd.validate_token_map({"accent": "  "})
    assert "point at" in raised.value.errors["tokens.accent"]


def test_a_non_object_map_is_refused():
    with pytest.raises(bd.BrandedDomainError) as raised:
        bd.validate_token_map(["accent"])
    assert "tokens" in raised.value.errors


def test_every_bad_field_is_reported_at_once():
    """A form puts each message beside its input, so one pass has to find them all."""
    with pytest.raises(bd.BrandedDomainError) as raised:
        bd.validate_token_map({"accent": "#fff", "not-a-token": "info"})
    assert set(raised.value.errors) == {"tokens.accent", "tokens.not-a-token"}


def test_the_render_form_is_a_custom_property_pointing_at_another_one():
    """No colour value is generated, stored or sent; the stylesheet keeps the hex."""
    variables = bd.token_css_variables({"accent": "accent-bright", "destructive": "warning"})
    assert variables == {
        "--color-accent": "var(--color-accent-bright)",
        "--color-destructive": "var(--color-warning)",
    }
    for value in variables.values():
        assert "var(" in value and "#" not in value


def test_the_product_s_and_the_tenant_s_tokens_partition_the_known_tokens():
    assert set(bd.PRODUCT_TOKENS) & set(bd.TENANT_TOKENS) == set()
    assert set(bd.TOKEN_NAMES) == set(bd.PRODUCT_TOKENS) | set(bd.TENANT_TOKENS)


# --------------------------------------------------------------------------- #
# Resolution of a token map
# --------------------------------------------------------------------------- #


def test_a_room_s_own_override_beats_the_brand_value_and_inherits_the_rest():
    brand = {"accent": "accent-bright", "primary": "info"}
    room = {"accent": "accent-soft"}
    assert bd.effective_tokens(brand, room) == {"accent": "accent-soft", "primary": "info"}


def test_a_room_with_no_override_renders_the_brand_map():
    brand = {"accent": "accent-bright"}
    assert bd.effective_tokens(brand, None) == brand


def test_a_new_room_inherits_the_brand_s_current_tokens_not_the_defaults():
    """The researched "auto-apply brand tokens to new rooms"."""
    theme = bd.adopt_room("acct_1", {"accent": "accent-bright"}, 7, "room_1")
    assert theme["tokens"] == {"accent": "accent-bright"}
    assert theme["brand_revision"] == 7
    assert theme["inherited"] is True
    assert theme["override"] == {}


# --------------------------------------------------------------------------- #
# Propagation: a brand change reaches every tenant
# --------------------------------------------------------------------------- #


def _room(
    theme_id: str, brand_id: str, *, room_ref: str, brand_revision: int, tokens=None, override=None
):
    return {
        "theme_id": theme_id,
        "room_ref": room_ref,
        "brand_id": brand_id,
        "brand_revision": brand_revision,
        "tokens": dict(tokens or {}),
        "override": dict(override or {}),
    }


def test_a_brand_change_reaches_every_room_of_the_brand():
    rooms = [
        _room("t1", "b1", room_ref="r1", brand_revision=1, tokens={"accent": "info"}),
        _room("t2", "b1", room_ref="r2", brand_revision=1, tokens={"accent": "info"}),
    ]
    plan = bd.propagation_plan("b1", {"accent": "accent-bright"}, 2, rooms)
    assert [row["room_ref"] for row in plan] == ["r1", "r2"]
    assert all(row["tokens"] == {"accent": "accent-bright"} for row in plan)
    assert all(row["changed"] is True for row in plan)
    assert all(row["brand_revision"] == 2 for row in plan)


def test_a_room_of_another_brand_is_not_touched():
    rooms = [
        _room("t1", "b1", room_ref="r1", brand_revision=1),
        _room("t2", "b2", room_ref="r2", brand_revision=1),
    ]
    plan = bd.propagation_plan("b1", {"accent": "accent-bright"}, 2, rooms)
    assert [row["room_ref"] for row in plan] == ["r1"]


def test_a_room_override_wins_and_is_marked_as_not_inheriting():
    rooms = [_room("t1", "b1", room_ref="r1", brand_revision=1, override={"accent": "accent-soft"})]
    plan = bd.propagation_plan("b1", {"accent": "accent-bright", "primary": "info"}, 2, rooms)
    row = plan[0]
    assert row["tokens"] == {"accent": "accent-soft", "primary": "info"}
    assert row["override"] == {"accent": "accent-soft"}
    assert row["inherited"] is False


def test_propagation_is_idempotent_by_revision():
    """A second call must write nothing, or every page load is a write."""
    rooms = [_room("t1", "b1", room_ref="r1", brand_revision=2, tokens={"accent": "accent-bright"})]
    plan = bd.propagation_plan("b1", {"accent": "accent-bright"}, 2, rooms)
    assert plan[0]["changed"] is False


def test_a_room_carrying_a_stale_map_at_the_current_revision_still_changes():
    """Revision alone is not the test: what is stored has to match as well."""
    rooms = [_room("t1", "b1", room_ref="r1", brand_revision=2, tokens={"accent": "info"})]
    plan = bd.propagation_plan("b1", {"accent": "accent-bright"}, 2, rooms)
    assert plan[0]["changed"] is True


def test_propagation_uses_the_record_id_the_caller_has_to_write():
    rooms = [_room("theme_abc", "b1", room_ref="r1", brand_revision=1)]
    plan = bd.propagation_plan("b1", {}, 2, rooms)
    assert plan[0]["theme_id"] == "theme_abc"


def test_a_brand_with_no_rooms_has_an_empty_plan():
    assert bd.propagation_plan("b1", {"accent": "info"}, 2, []) == []


# --------------------------------------------------------------------------- #
# The hostname claim's states
# --------------------------------------------------------------------------- #


def test_a_claim_moves_to_verified_when_its_check_passes():
    assert bd.claim_next_state(bd.CLAIM_UNVERIFIED, "verify_ok") == bd.CLAIM_VERIFIED
    assert bd.claim_next_state(bd.CLAIM_FAILED, "verify_ok") == bd.CLAIM_VERIFIED
    assert bd.claim_next_state(bd.CLAIM_RELEASED, "verify_ok") == bd.CLAIM_VERIFIED


def test_a_claim_moves_to_failed_when_its_check_fails():
    assert bd.claim_next_state(bd.CLAIM_VERIFIED, "verify_fail") == bd.CLAIM_FAILED


def test_releasing_a_claim_is_a_state_of_its_own():
    assert bd.claim_next_state(bd.CLAIM_VERIFIED, "release") == bd.CLAIM_RELEASED
    assert bd.claim_next_state(bd.CLAIM_UNVERIFIED, "release") == bd.CLAIM_RELEASED


def test_only_a_verified_claim_serves_traffic():
    for state in bd.CLAIM_STATES:
        assert bd.claim_is_serving(state) is (state == bd.CLAIM_VERIFIED)


def test_a_misspelled_event_or_state_is_refused_rather_than_ignored():
    with pytest.raises(bd.BrandedDomainError):
        bd.claim_next_state(bd.CLAIM_VERIFIED, "verifyed")
    with pytest.raises(bd.BrandedDomainError):
        bd.claim_next_state("almost", "verify_ok")


def test_the_states_carry_the_words_a_page_shows():
    """Status is never carried by colour alone, so every state has a sentence."""
    for state in bd.CLAIM_STATES:
        assert bd.CLAIM_EXPECTATIONS[state].strip()
    assert "24 hours" in bd.CLAIM_EXPECTATIONS[bd.CLAIM_UNVERIFIED]


# --------------------------------------------------------------------------- #
# Claiming and resolution
# --------------------------------------------------------------------------- #


def _claim(claim_id: str, host: str, state: str, brand_id: str):
    return {"id": claim_id, "host": host, "state": state, "brand_id": brand_id}


def test_a_host_another_brand_holds_verified_cannot_be_claimed():
    holder = _claim("c1", "deals.acme.example", bd.CLAIM_VERIFIED, "b1")
    found = bd.claimable("deals.acme.example", [holder], brand_id="b2")
    assert found is holder


def test_a_brand_re_claiming_its_own_host_is_not_a_conflict_with_itself():
    mine = _claim("c1", "deals.acme.example", bd.CLAIM_VERIFIED, "b1")
    assert bd.claimable("deals.acme.example", [mine], brand_id="b1") is None


def test_a_saved_but_unverified_host_does_not_reserve_itself():
    """The researched propagation window: a host mid-DNS must not be locked away."""
    pending = _claim("c1", "deals.acme.example", bd.CLAIM_UNVERIFIED, "b1")
    assert bd.claimable("deals.acme.example", [pending], brand_id="b2") is None


def test_a_released_host_is_free_for_another_brand():
    released = _claim("c1", "deals.acme.example", bd.CLAIM_RELEASED, "b1")
    assert bd.claimable("deals.acme.example", [released], brand_id="b2") is None


def test_resolution_finds_the_brand_that_serves_the_host():
    claims = [_claim("c1", "deals.acme.example", bd.CLAIM_VERIFIED, "b1")]
    decision = bd.resolve_host("deals.acme.example", claims)
    assert decision["reason"] == "ok"
    assert decision["claim"]["brand_id"] == "b1"


def test_resolution_refuses_a_host_no_brand_holds():
    decision = bd.resolve_host("deals.acme.example", [])
    assert decision["claim"] is None
    assert decision["reason"] == "unclaimed"


def test_resolution_refuses_a_host_two_verified_claims_hold():
    """The hazard the issue names: two answers for one host is not an answer."""
    claims = [
        _claim("c1", "deals.acme.example", bd.CLAIM_VERIFIED, "b1"),
        _claim("c2", "deals.acme.example", bd.CLAIM_VERIFIED, "b2"),
    ]
    decision = bd.resolve_host("deals.acme.example", claims)
    assert decision["claim"] is None
    assert decision["reason"] == "ambiguous"


def test_resolution_ignores_a_claim_that_is_not_serving():
    claims = [_claim("c1", "deals.acme.example", bd.CLAIM_UNVERIFIED, "b1")]
    assert bd.resolve_host("deals.acme.example", claims)["reason"] == "unclaimed"


# --------------------------------------------------------------------------- #
# The verification report
# --------------------------------------------------------------------------- #


def test_the_report_is_ready_only_when_the_cname_matched_and_the_host_is_free():
    report = bd.verify_report(
        "deals.acme.example",
        matched=True,
        observed=["cname.dsr.test"],
        cname_target="cname.dsr.test",
    )
    assert report["ready"] is True
    assert report["status"] == bd.CLAIM_VERIFIED
    assert [check["name"] for check in report["checks"]] == ["cname", "format", "available"]
    assert report["observed"] == ["cname.dsr.test"]


def test_the_report_names_an_unresolved_host_as_unresolved():
    report = bd.verify_report(
        "deals.acme.example", matched=False, observed=[], cname_target="cname.dsr.test"
    )
    assert report["ready"] is False
    assert report["status"] == bd.CLAIM_FAILED
    assert report["checks"][0]["detail"] == "deals.acme.example does not resolve yet"


def test_the_report_refuses_availability_even_when_the_cname_is_right():
    holder = _claim("c1", "deals.acme.example", bd.CLAIM_VERIFIED, "b1")
    report = bd.verify_report(
        "deals.acme.example",
        matched=True,
        observed=["cname.dsr.test"],
        cname_target="cname.dsr.test",
        holder=holder,
    )
    assert report["ready"] is False
    available = next(check for check in report["checks"] if check["name"] == "available")
    assert "b1" in available["detail"]


def test_the_report_states_what_it_cannot_verify():
    """A verified CNAME is necessary and not sufficient, and says so."""
    report = bd.verify_report("deals.acme.example", matched=True, cname_target="cname.dsr.test")
    joined = " ".join(report["cannot_verify"])
    assert "TLS" in joined
    assert "reverse proxy" in joined
    assert "TXT" in joined


# --------------------------------------------------------------------------- #
# White-label mode
# --------------------------------------------------------------------------- #


def test_without_white_label_the_owner_is_presented():
    presented = bd.presented_owner({"owner_name": "Northwind Studio", "white_label": False})
    assert presented == {
        "presented": "Northwind Studio",
        "role": "owner",
        "white_labelled": False,
        "withheld": None,
    }


def test_white_label_mode_presents_the_client_and_withholds_the_owner():
    presented = bd.presented_owner(
        {
            "owner_name": "Fabrikam Advisory",
            "client_name": "Fabrikam Logistics",
            "white_label": True,
        }
    )
    assert presented["presented"] == "Fabrikam Logistics"
    assert presented["role"] == "client"
    assert presented["white_labelled"] is True
    assert presented["withheld"] == "Fabrikam Advisory"


def test_white_label_mode_without_a_client_is_refused_rather_than_hiding_the_brand():
    with pytest.raises(bd.BrandedDomainError) as raised:
        bd.presented_owner({"owner_name": "Fabrikam Advisory", "white_label": True})
    assert "client_name" in raised.value.errors


def test_white_label_mode_needs_a_client_at_the_door_too():
    with pytest.raises(bd.BrandedDomainError) as raised:
        bd.validate_brand({"name": "Fabrikam", "owner_name": "Fabrikam", "white_label": True})
    assert "client_name" in raised.value.errors


def test_a_brand_needs_a_name_and_an_owner():
    with pytest.raises(bd.BrandedDomainError) as raised:
        bd.validate_brand({})
    assert set(raised.value.errors) == {"name", "owner_name"}


def test_a_brand_keeps_a_field_this_workflow_has_never_heard_of():
    """Teams add fields without a migration, so validation passes the rest through."""
    brand = bd.validate_brand(
        {"name": "Acme", "owner_name": "Acme", "logo_url": "https://cdn.acme.example/logo.svg"}
    )
    assert brand["logo_url"] == "https://cdn.acme.example/logo.svg"
    assert brand["white_label"] is False
    assert brand["client_name"] is None


# --------------------------------------------------------------------------- #
# The published vocabulary
# --------------------------------------------------------------------------- #


def test_the_vocabulary_publishes_the_tokens_the_states_and_the_decisions():
    published = bd.vocabulary()
    assert published["token_names"] == list(bd.TOKEN_NAMES)
    assert published["tenant_tokens"] == list(bd.TENANT_TOKENS)
    assert published["product_tokens"] == list(bd.PRODUCT_TOKENS)
    assert published["claim_states"] == list(bd.CLAIM_STATES)
    assert published["collections"]["brand"] == bd.BRAND_COLLECTION
    for key in (
        "token_overrides",
        "locked_tokens",
        "white_label",
        "host_identity",
        "verification",
        "propagation",
    ):
        assert published["decisions"][key].strip()


def test_the_decisions_that_matter_name_the_alternative_they_rejected():
    for key in ("token_overrides", "white_label", "propagation", "host_identity"):
        assert "Rejected" in bd.DECISIONS[key], key


def test_the_unverifiable_list_names_the_infrastructure_this_repo_does_not_own():
    joined = " ".join(bd.UNVERIFIABLE_HERE)
    for named in ("TLS", "reverse proxy", "Per-customer DNS", "TXT", "CDN", "asset manager"):
        assert named in joined, named


def test_the_unverified_vendor_claim_stays_unverified_and_docker_is_not_cited():
    """The research flags one claim unverified and marks one source unused."""
    note = bd.DECISIONS["hosted_vendors"]
    assert "Liferay" in note and "not cited" in note
    assert "docker.com" in note and "not used" in note
    assert "Papermark" in note and "hosted-plan" in note
    assert "Liferay" not in " ".join(bd.UNVERIFIABLE_HERE)


def test_the_design_tool_and_the_dam_are_recorded_as_not_built():
    joined = " ".join(bd.UNVERIFIABLE_HERE)
    assert "drag-and-drop" in joined
    assert bd.DECISIONS["design_tool"].strip()
    assert bd.DECISIONS["brand_assets"].strip()
