"""Tests for WF-022: triage the pipeline with saved workspace views.

Three layers, in the order a reviewer would want to read them.

**The researched rules** - the domain engine, tested with no HTTP and no
database, because :mod:`dsr.triage` is pure. The section that matters most is
``Active Pipeline``: the research says "Any workspace that has a 'Sales'
workspace type, **or** an opportunity or deal connected from your CRM", which is
a disjunction with a disjunction inside it. Both arms are tested separately as
well as together, because a rule that only finds one of them is the failure this
workflow cannot ship.

**The HTTP surface** - every route on this feature's own router, through the
mounted app. Including the audit-source rule: hard rule 4 of the build brief is
that a write's audit row must name the route that served it, and the same defect
has shipped in this repository before, so the assertion is against the set of
paths the host actually mounted rather than against a literal.

**The contract** - the structural promises: mounted by discovery, no shared-file
edit, no import of the app, a domain error the host maps, and demo data that
seeds the states the research says matter.
"""

from __future__ import annotations

import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.api import app
from dsr.features import load_feature
from dsr.triage import filters as engine, vocabulary as vocab
from dsr.triage.errors import TriageError
from dsr.triage.fields import (
    as_number,
    as_text,
    dig,
    first_text,
    first_time,
    iso,
    normalise_provider,
    parse_time,
)
from dsr.triage.rows import Engagement, aggregate_engagement, declared_sections
from fastapi.testclient import TestClient

PREFIX = "/api/wf-022"
FEATURE_ID = "wf-022-triage-the-pipeline-with-saved-workspa"
NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def client(monkeypatch):
    # A temporary database, exactly as test_features.py does it: the env var is
    # read at call time by dsr.deps, so every test gets its own store.
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf022.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def room(client, name="Acme", **payload):
    return client.post("/api/records/room", json={"name": name, **payload}).json()["id"]


def activity(client, room_id, action="viewed", when=None, **payload):
    return client.post(
        "/api/records/activity",
        json={
            "action": action,
            "person": "a.buyer@example",
            "occurred_at": (when or NOW).isoformat(),
            **payload,
        },
        params={"room_id": room_id},
    ).json()


def crm_link(client, room_id, **payload):
    return client.post(
        "/api/records/workspace_crm", json=payload, params={"room_id": room_id}
    ).json()


def order_form(client, room_id, **payload):
    return client.post("/api/records/order_form", json=payload, params={"room_id": room_id}).json()


def make_template(client, name, template_type=None):
    """A template record, returning the id the caller has to reference."""
    return client.post(
        "/api/records/workspace_template", json={"name": name, "type": template_type}
    ).json()["id"]


def row_of(client, room_id, *keys):
    """One workspace's joined row, with the researched ``properties`` selection.

    Omitting ``properties`` returns only ``id``/``object``/``url`` - that is the
    sourced behaviour and it is implemented literally - so a caller that wants
    values asks for them. ``row_of`` with no keys therefore asks for everything,
    which is what most of these tests mean by "the row".
    """
    requested = list(keys) or list(vocab.ALL_COLUMN_KEYS)
    return client.get(
        f"{PREFIX}/rooms/{room_id}/row", params={"properties": ",".join(requested)}
    ).json()["row"]


def add_view(client, actor="dana", **payload):
    response = client.post(f"{PREFIX}/views", json=payload, params={"actor": actor})
    assert response.status_code == 201, response.text
    return response.json()


def rows_of(client, view_id, actor="dana", **params):
    params.setdefault("actor", actor)
    response = client.get(f"{PREFIX}/views/{view_id}/rows", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def names(body):
    return sorted(row["dock.name"] for row in body["rows"])


def audit(client, **params):
    return client.get("/api/audit", params={"limit": 1000, **params}).json()["entries"]


def condition(field, op, value=None):
    entry = {"field": field, "op": op}
    if value is not None or op in ("is", "is_not", "contains", "in_the_last", "gt", "lt"):
        entry["value"] = value
    return entry


def group(*conditions, join="and"):
    return {"join": join, "conditions": list(conditions)}


# =========================================================================== #
# 1. The researched rules: the pure domain
# =========================================================================== #


# -- the column vocabulary ---------------------------------------------------- #


def test_the_column_catalog_carries_every_field_the_research_lists():
    """The research names the available fields verbatim; all of them are here.

    "Available fields include 'Engagement analytics: Views, Actions, Last Client
    View', 'Salesforce data: Opportunity Stage, Opportunity Created Date, Opp
    Amount, Opportunity Type', 'Hubspot data: Deal Stage, Deal Type, Deal Closed
    Date, Deal Amount', 'Order forms: Status and Deal Type'."
    """
    keys = set(vocab.ALL_COLUMN_KEYS)
    assert {
        "engagement.views",
        "engagement.actions",
        "engagement.last_client_view",
        "salesforce.opportunity_stage",
        "salesforce.opportunity_created_date",
        "salesforce.opp_amount",
        "salesforce.opportunity_type",
        "hubspot.deal_stage",
        "hubspot.deal_type",
        "hubspot.deal_closed_date",
        "hubspot.deal_amount",
        "order_form.status",
        "order_form.deal_type",
    } <= keys


def test_deal_type_appears_twice_and_the_group_keeps_them_apart():
    """The research lists "Deal Type" under both HubSpot and Order forms.

    A flat vocabulary would have to invent a tie-break. The vendor resolves it
    with the group heading, so the keys are namespaced and the collision simply
    does not exist.
    """
    hubspot = vocab.column("hubspot.deal_type")
    order = vocab.column("order_form.deal_type")
    assert hubspot["label"] == order["label"] == "Deal Type"
    assert hubspot["group"] != order["group"]


def test_the_dock_columns_the_user_flow_names_are_published():
    """ "add **Views**, **Actions**, **Last Client View**, `Stage`, `Team`"."""
    labels = {column["label"] for column in vocab.COLUMNS}
    assert {"Views", "Actions", "Last Client View", "Stage", "Team"} <= labels


def test_workspace_and_domain_groups_split_on_the_join_in_the_data_flow():
    """The researched data flow has exactly two halves, and so do the filter params.

    "Dock workspace metadata + engagement metrics + CRM-synced opportunity/deal
    fields -> joined rows". A workspace filter reads the first two groups; a
    domain filter reads what only exists because something was joined in.
    """
    assert vocab.WORKSPACE_GROUPS == ("dock", "engagement")
    assert vocab.DOMAIN_GROUPS == ("salesforce", "hubspot", "order_form")
    for key in vocab.WORKSPACE_GROUPS:
        assert vocab.group_of(f"{key}.anything") == key


def test_every_column_declares_a_type_and_every_operator_knows_which_types_it_takes():
    for column in vocab.COLUMNS:
        assert column["type"] in (vocab.TEXT, vocab.NUMBER, vocab.DATE)
        assert column["label"] and column["key"].startswith(column["group"] + ".")
    for entry in vocab.OPERATORS:
        assert entry["applies_to"], entry["op"]
        assert set(entry["applies_to"]) <= {vocab.TEXT, vocab.NUMBER, vocab.DATE}


def test_in_the_last_is_the_operator_behind_recent_client_activity():
    """The research filters on "recent client activity" and names no operator."""
    spec = vocab.OPERATORS_BY_OP["in_the_last"]
    assert spec["applies_to"] == [vocab.DATE]
    assert spec["arity"] == 1


# -- the five default views -------------------------------------------------- #


def test_there_are_five_default_views_and_they_are_the_five_named():
    assert [view["id"] for view in vocab.DEFAULT_VIEWS] == [
        "all",
        "my",
        "active-pipeline",
        "deal-desk",
        "implementations",
    ]
    assert [view["label"] for view in vocab.DEFAULT_VIEWS] == [
        "All Workspaces",
        "My Workspaces",
        "Active Pipeline",
        "Deal Desk",
        "Implementations",
    ]


def test_the_two_views_the_research_defines_are_flagged_as_sourced():
    """Active Pipeline and Deal Desk have quoted definitions; the rest do not."""
    sourced = {view["id"] for view in vocab.DEFAULT_VIEWS if not view["inferred"]}
    assert sourced == {"all", "active-pipeline", "deal-desk"}
    assert vocab.default_view("active-pipeline")["quote"].startswith("Any workspace that has a")
    assert vocab.default_view("deal-desk")["quote"] == "Any workspaces that uses Dock's order forms"


def test_all_workspaces_is_the_catch_all_the_other_four_narrow():
    """A view with no conditions must match everything, not nothing.

    An empty group matching vacuously is what makes **All Workspaces** a
    reliable starting point rather than a view that comes back empty.
    """
    plan = engine.compile_filters(*_plan_args(vocab.default_view("all")))
    context = engine.FilterContext(actor="dana", reference=NOW)
    assert plan.matches({"dock.name": "anything"}, context)
    assert plan.workspace.matches({}, context)
    assert plan.domain.matches({}, context)
    assert plan.problems == []


def test_a_group_with_no_conditions_is_not_a_group_that_excludes_everything():
    plan = engine.compile_filters(group(), group(), "all")
    assert plan.matches({}, engine.FilterContext(reference=NOW))


# -- ACTIVE PIPELINE: the disjunction, arm by arm --------------------------- #


def _active_pipeline_plan():
    return engine.compile_filters(*_plan_args(vocab.default_view("active-pipeline")))


def _plan_args(definition):
    return (
        definition["workspace_filters"],
        definition["workspace_domain_filters"],
        definition["match"],
    )


def test_active_pipeline_is_a_disjunction_of_two_independent_arms():
    """The one rule in this workflow that has to fall through.

    "Any workspace that has a 'Sales' workspace type, **or** an opportunity or
    deal connected from your CRM." The arms are alternatives, so the two filter
    groups are ORed rather than ANDed. Getting this wrong hides half a pipeline.
    """
    plan = _active_pipeline_plan()
    assert plan.match == engine.MATCH_ANY
    assert plan.workspace.join == "and"
    assert plan.domain.join == "or", "opportunity OR deal is itself a disjunction"
    assert [condition["field"] for condition in plan.workspace.usable] == ["dock.type"]
    assert [condition["field"] for condition in plan.domain.usable] == [
        "salesforce.opportunity_stage",
        "hubspot.deal_stage",
    ]


def test_active_pipeline_arm_one_admits_a_sales_workspace_with_no_crm_at_all():
    """The arm that is easy to forget, and the one that loses the most rows.

    A workspace created last week with no opportunity attached is in the pipeline.
    A view that only asked the CRM would drop it, and most of a new pipeline is
    made of exactly those.
    """
    plan = _active_pipeline_plan()
    context = engine.FilterContext(reference=NOW)
    row = {"dock.type": "Sales", "salesforce.opportunity_stage": None, "hubspot.deal_stage": None}
    assert plan.workspace.matches(row, context)
    assert not plan.domain.matches(row, context)
    assert plan.matches(row, context), "arm one alone must be enough"


def test_active_pipeline_arm_two_admits_a_linked_workspace_that_is_not_typed_sales():
    plan = _active_pipeline_plan()
    context = engine.FilterContext(reference=NOW)
    row = {
        "dock.type": "General",
        "salesforce.opportunity_stage": "Negotiation/Review",
        "hubspot.deal_stage": None,
    }
    assert not plan.workspace.matches(row, context)
    assert plan.domain.matches(row, context)
    assert plan.matches(row, context), "arm two alone must be enough"


@pytest.mark.parametrize(
    "crm",
    [
        {"salesforce.opportunity_stage": "Qualification", "hubspot.deal_stage": None},
        {"salesforce.opportunity_stage": None, "hubspot.deal_stage": "Contract Sent"},
    ],
)
def test_either_joined_crm_object_satisfies_the_second_arm(crm):
    """ "an opportunity **or deal** connected from your CRM" - both are accepted."""
    plan = _active_pipeline_plan()
    row = {"dock.type": "General", **crm}
    assert plan.matches(row, engine.FilterContext(reference=NOW))


def test_active_pipeline_excludes_a_workspace_with_neither_arm():
    plan = _active_pipeline_plan()
    context = engine.FilterContext(reference=NOW)
    row = {
        "dock.type": "Implementation",
        "salesforce.opportunity_stage": None,
        "hubspot.deal_stage": None,
    }
    assert not plan.workspace.matches(row, context)
    assert not plan.domain.matches(row, context)
    assert not plan.matches(row, context), "the disjunction has to exclude something"


def test_active_pipeline_admits_a_workspace_satisfying_both_arms_once():
    """Both arms true is still one row, not two."""
    plan = _active_pipeline_plan()
    context = engine.FilterContext(reference=NOW)
    row = {
        "dock.type": "Sales",
        "salesforce.opportunity_stage": "Closed Won",
        "hubspot.deal_stage": None,
    }
    assert plan.workspace.matches(row, context)
    assert plan.domain.matches(row, context)
    assert plan.matches(row, context)


def test_the_sales_type_value_is_the_one_the_research_quotes():
    assert vocab.SOURCED_TYPE == "Sales"
    assert (
        vocab.default_view("active-pipeline")["workspace_filters"]["conditions"][0]["value"]
        == "Sales"
    )


# -- DEAL DESK ---------------------------------------------------------------- #


def test_deal_desk_keys_on_the_presence_of_an_order_form_not_its_status():
    """ "Any workspaces that uses Dock's order forms" - presence, not state."""
    plan = engine.compile_filters(*_plan_args(vocab.default_view("deal-desk")))
    conditions = plan.domain.usable
    assert [entry["field"] for entry in conditions] == ["order_form.status"]
    assert conditions[0]["op"] == "is_not_empty"


@pytest.mark.parametrize("status", ["completed", "sent", "voided"])
def test_a_voided_order_form_still_puts_the_workspace_on_the_deal_desk(status):
    """A voided form is the row that most needs a rep to look at it."""
    plan = engine.compile_filters(*_plan_args(vocab.default_view("deal-desk")))
    context = engine.FilterContext(reference=NOW)
    assert plan.matches({"order_form.status": status}, context)


def test_deal_desk_excludes_a_workspace_with_no_order_form():
    plan = engine.compile_filters(*_plan_args(vocab.default_view("deal-desk")))
    assert not plan.matches({"order_form.status": None}, engine.FilterContext(reference=NOW))


# -- MY WORKSPACES ------------------------------------------------------------ #


def test_my_workspaces_publishes_a_sentinel_because_a_default_cannot_know_its_reader():
    conditions = vocab.default_view("my")["workspace_filters"]["conditions"]
    assert conditions == [{"field": "dock.owner", "op": "is", "value": vocab.ME}]


def test_the_sentinel_is_substituted_in_the_stored_group_not_just_ignored():
    raw = {"join": "and", "conditions": [condition("dock.owner", "is", vocab.ME)]}
    resolved = engine.substitute_me_in_raw(raw, "dana")
    assert resolved["conditions"][0]["value"] == "dana"
    assert raw["conditions"][0]["value"] == vocab.ME, "the source group is not mutated"


def test_the_sentinel_substitutes_inside_a_list_too():
    raw = {"join": "and", "conditions": [condition("dock.owner", "in", [vocab.ME, "sam"])]}
    resolved = engine.substitute_me_in_raw(raw, "dana")
    assert resolved["conditions"][0]["value"] == ["dana", "sam"]


def test_an_unresolved_sentinel_matches_nothing_and_says_so():
    """The one answer that cannot be defended is matching everything."""
    plan = engine.compile_filters(
        {"join": "and", "conditions": [condition("dock.owner", "is", vocab.ME)]}, group(), "all"
    )
    assert not plan.matches({"dock.owner": "dana"}, engine.FilterContext(reference=NOW))
    problems = engine.unresolved_me_problems(plan)
    assert [problem["kind"] for problem in problems] == [engine.PROBLEM_UNRESOLVED_ME]


def test_a_view_with_no_owner_reports_the_unresolved_sentinel_rather_than_matching_everyone():
    plan = engine.compile_filters(
        {"join": "and", "conditions": [condition("dock.owner", "is", vocab.ME)]}, group(), "all"
    )
    assert (
        engine.substitute_me_in_raw(
            {"join": "and", "conditions": [condition("dock.owner", "is", vocab.ME)]}, None
        )["conditions"][0]["value"]
        == vocab.ME
    )
    assert engine.unresolved_me_problems(plan)


def test_implementations_is_flagged_as_inferred_because_its_type_string_is_not_sourced():
    """The view is sourced; the workspace-type value it selects is not."""
    view = vocab.default_view("implementations")
    assert view["inferred"] is True
    assert view["workspace_filters"]["conditions"][0]["value"] == vocab.INFERRED_IMPLEMENTATION_TYPE
    assert vocab.SOURCED_TYPE not in view["workspace_filters"]["conditions"][0]["value"]


# -- filters: every operator -------------------------------------------------- #


def _row(**overrides):
    base = {
        "dock.name": "Northwind",
        "dock.owner": "dana",
        "dock.stage": "evaluation",
        "dock.type": "Sales",
        "engagement.views": 12,
        "engagement.last_client_view": "2026-09-20T10:00:00+00:00",
        "salesforce.opp_amount": 50000,
        "hubspot.deal_stage": "Contract Sent",
        "order_form.status": "completed",
    }
    base.update(overrides)
    return base


def _matches(conditions, row=None, join="and", **extra):
    plan = engine.compile_filters({"join": join, "conditions": conditions}, group(), "all")
    return plan.matches(
        row if row is not None else _row(), engine.FilterContext(reference=NOW, **extra)
    )


@pytest.mark.parametrize(
    "op,value,expected",
    [
        ("is", "dana", True),
        ("is", "sam", False),
        ("is_not", "sam", True),
        ("is_not", "dana", False),
        ("in", ["dana", "sam"], True),
        ("in", ["sam"], False),
        ("not_in", ["sam"], True),
        ("is_empty", None, False),
        ("is_not_empty", None, True),
    ],
)
def test_every_text_operator_on_a_workspace_property(op, value, expected):
    assert _matches([condition("dock.owner", op, value)]) is expected


@pytest.mark.parametrize(
    "op,value,expected",
    [
        ("is", 12, True),
        ("is", 13, False),
        ("gt", 10, True),
        ("gt", 12, False),
        ("gte", 12, True),
        ("lt", 12, False),
        ("lte", 12, True),
    ],
)
def test_every_numeric_operator_on_a_count_column(op, value, expected):
    assert _matches([condition("engagement.views", op, value)]) is expected


@pytest.mark.parametrize(
    "op,value,expected",
    [("contains", "orth", True), ("contains", "ORTH", True), ("contains", "zzz", False)],
)
def test_contains_is_a_case_insensitive_substring_of_a_text_value(op, value, expected):
    assert _matches([condition("dock.name", op, value)]) is expected


def test_is_not_empty_matches_a_row_with_no_value_and_is_empty_does_not():
    blank = _row(**{"dock.stage": None})
    assert _matches([condition("dock.stage", "is_empty", None)], blank)
    assert not _matches([condition("dock.stage", "is_not_empty", None)], blank)


def test_a_negated_filter_does_not_sweep_in_rows_that_have_no_value():
    """ "Stage is not Closed Lost" must not list every unsynced workspace."""
    assert not _matches(
        [condition("salesforce.opportunity_stage", "is_not", "Closed Lost")],
        _row(**{"salesforce.opportunity_stage": None}),
    )
    assert not _matches(
        [condition("hubspot.deal_stage", "not_in", ["Contract Sent"])],
        _row(**{"hubspot.deal_stage": None}),
    )


def test_in_matches_a_list_value():
    assert _matches([condition("dock.stage", "in", ["evaluation", "discovery"])])
    assert not _matches([condition("dock.stage", "in", ["discovery"])])


def test_comparing_a_date_to_a_bare_date_matches_a_stored_timestamp():
    """``deal_closed_date is 2026-11-15`` must not miss ``2026-11-15T09:30Z``."""
    assert _matches(
        [condition("engagement.last_client_view", "is", "2026-09-20")],
        _row(**{"engagement.last_client_view": "2026-09-20T09:30:00+00:00"}),
    )


def test_dates_compare_as_instants_when_both_sides_carry_a_time():
    assert _matches([condition("engagement.last_client_view", "gt", "2026-09-19T00:00:00Z")])
    assert not _matches([condition("engagement.last_client_view", "lt", "2026-09-19T00:00:00Z")])


def test_in_the_last_is_the_recent_client_activity_filter():
    plan = engine.compile_filters(
        {
            "join": "and",
            "conditions": [condition("engagement.last_client_view", "in_the_last", 7)],
        },
        group(),
        "all",
    )
    context = engine.FilterContext(reference=NOW)
    fresh = {"engagement.last_client_view": (NOW - timedelta(days=2)).isoformat()}
    stale = {"engagement.last_client_view": (NOW - timedelta(days=30)).isoformat()}
    assert plan.matches(fresh, context)
    assert not plan.matches(stale, context)
    assert not plan.matches({"engagement.last_client_view": None}, context)


def test_in_the_last_does_not_match_a_timestamp_in_the_future():
    plan = engine.compile_filters(
        {"join": "and", "conditions": [condition("engagement.last_client_view", "in_the_last", 7)]},
        group(),
        "all",
    )
    assert not plan.matches(
        {"engagement.last_client_view": (NOW + timedelta(days=1)).isoformat()},
        engine.FilterContext(reference=NOW),
    )


def test_a_join_of_or_admits_either_condition():
    conditions = [condition("dock.type", "is", "Sales"), condition("dock.owner", "is", "sam")]
    assert _matches(conditions, join="or")
    assert not _matches(conditions, join="and")


def test_a_group_with_one_unreadable_condition_still_applies_its_readable_one():
    """Failing open is the researched sibling behaviour (WF-013's S8)."""
    conditions = [
        condition("dock.owner", "is", "dana"),
        condition("dock.owner", "sorts_alphabetically", "x"),
    ]
    assert _matches(conditions, join="and"), "the readable condition must still hold"
    assert not _matches(
        [
            condition("dock.owner", "is", "sam"),
            condition("dock.owner", "sorts_alphabetically", "x"),
        ],
        join="and",
    ), "and the unreadable one must not be treated as a pass"


def test_an_unknown_operator_is_dropped_and_reported_rather_than_ignored_silently():
    problems: list[dict] = []
    group_out = engine.compile_group(
        engine.WORKSPACE,
        {"join": "and", "conditions": [condition("dock.owner", "sounds_like", "dana")]},
        problems,
    )
    assert group_out.usable == []
    assert [problem["kind"] for problem in problems] == [engine.PROBLEM_UNKNOWN_OPERATOR]
    assert "sounds_like" in problems[0]["detail"]


def test_an_operator_that_cannot_apply_to_a_known_field_is_refused():
    """``contains`` on a count is a mistake in the request, not a new field."""
    problems: list[dict] = []
    engine.compile_group(
        engine.WORKSPACE,
        {"join": "and", "conditions": [condition("engagement.views", "contains", "1")]},
        problems,
    )
    assert [problem["kind"] for problem in problems] == [engine.PROBLEM_INAPPLICABLE_OPERATOR]


def test_an_unknown_field_is_evaluated_rather_than_refused():
    """ "new CRM fields flow through automatically" - a view may name one first."""
    plan = engine.compile_filters(
        {
            "join": "and",
            "conditions": [condition("salesforce.discount_pct", "gte", 10)],
        },
        group(),
        "all",
    )
    assert plan.problems == []
    assert plan.matches({"salesforce.discount_pct": 15}, engine.FilterContext(reference=NOW))
    assert not plan.matches({"salesforce.discount_pct": 5}, engine.FilterContext(reference=NOW))
    assert not plan.matches({"salesforce.discount_pct": None}, engine.FilterContext(reference=NOW))


def test_an_unknown_field_with_an_unknown_operator_is_still_dropped():
    problems: list[dict] = []
    engine.compile_group(
        engine.DOMAIN,
        {"join": "and", "conditions": [condition("crm.mystery", "sounds_like", 1)]},
        problems,
    )
    assert [problem["kind"] for problem in problems] == [engine.PROBLEM_UNKNOWN_OPERATOR]


@pytest.mark.parametrize(
    "entry,kind",
    [
        ({"field": "dock.owner", "op": "in", "value": "dana"}, engine.PROBLEM_BAD_ARITY),
        ({"field": "dock.owner", "op": "is"}, engine.PROBLEM_MISSING_VALUE),
        ({"field": "", "op": "is", "value": "x"}, engine.PROBLEM_MISSING_VALUE),
        (
            {"field": "engagement.last_client_view", "op": "in_the_last", "value": -3},
            engine.PROBLEM_INVALID_WINDOW,
        ),
        (
            {"field": "engagement.last_client_view", "op": "in_the_last", "value": "soon"},
            engine.PROBLEM_INVALID_WINDOW,
        ),
    ],
)
def test_a_condition_this_build_cannot_read_is_dropped_and_named(entry, kind):
    problems: list[dict] = []
    group_out = engine.compile_group(
        engine.WORKSPACE, {"join": "and", "conditions": [entry]}, problems
    )
    assert group_out.usable == []
    assert [problem["kind"] for problem in problems] == [kind]


def test_an_unary_operator_given_a_value_says_so_and_keeps_the_condition():
    problems: list[dict] = []
    group_out = engine.compile_group(
        engine.WORKSPACE,
        {"join": "and", "conditions": [{"field": "dock.stage", "op": "is_empty", "value": "x"}]},
        problems,
    )
    assert [problem["kind"] for problem in problems] == [engine.PROBLEM_BAD_ARITY]
    assert len(group_out.usable) == 1, "the condition still works; only the value is ignored"


def test_a_malformed_group_is_reported_rather_than_crashing_the_view():
    problems: list[dict] = []
    assert engine.compile_group(engine.WORKSPACE, "nonsense", problems).usable == []
    assert (
        engine.compile_group(engine.WORKSPACE, {"join": "xor", "conditions": []}, problems).join
        == "and"
    )
    assert engine.compile_group(engine.WORKSPACE, {"conditions": "nope"}, problems).usable == []
    assert (
        engine.compile_group(
            engine.WORKSPACE, {"join": "and", "conditions": ["nope"]}, problems
        ).usable
        == []
    )
    assert {problem["kind"] for problem in problems} == {"malformed_group", "malformed_condition"}


def test_an_unrecognised_match_mode_is_treated_as_all_and_reported():
    plan = engine.compile_filters(group(), group(), "either")
    assert plan.match == engine.MATCH_ALL
    assert [problem["kind"] for problem in plan.problems] == ["malformed_match"]


def test_the_camel_case_filter_parameter_names_are_accepted_too():
    """``workspaceFilters`` / ``workspaceDomainFilters`` are the researched names."""
    workspace, domain, match = engine.normalise_filter_set(
        {
            "workspaceFilters": group(condition("dock.type", "is", "Sales")),
            "workspaceDomainFilters": group(condition("order_form.status", "is_not_empty", None)),
            "match": "any",
        }
    )
    plan = engine.compile_filters(workspace, domain, match)
    assert plan.match == engine.MATCH_ANY
    assert plan.matches(
        {"dock.type": None, "order_form.status": "sent"}, engine.FilterContext(reference=NOW)
    )


# -- sorting ------------------------------------------------------------------ #


def _sort(values, field="dock.name", direction="asc"):
    rows = [{"id": f"r{index}", field: value} for index, value in enumerate(values)]
    return [
        row["id"]
        for row in engine.sort_rows(
            rows,
            {"field": field, "direction": direction},
            value_of=lambda row: row.get(field),
            key_of=lambda row: row["id"],
        )
    ]


def test_sorting_orders_ascending_and_descending():
    assert _sort(["b", "a", "c"]) == ["r1", "r0", "r2"]
    assert _sort(["b", "a", "c"], direction="desc") == ["r2", "r0", "r1"]


def test_sorting_a_number_column_orders_numerically_not_lexically():
    # 9, 100, 20 - lexically this is 100, 20, 9; numerically it is 9, 20, 100.
    assert _sort([9, 100, 20], field="salesforce.opp_amount") == ["r0", "r2", "r1"]
    assert _sort([9, 100, 20], field="salesforce.opp_amount", direction="desc") == [
        "r1",
        "r2",
        "r0",
    ]


def test_sorting_a_date_column_orders_by_instant():
    values = ["2026-01-01", "2026-12-01", "2026-06-01"]
    rows = [{"id": f"r{i}", "engagement.last_client_view": v} for i, v in enumerate(values)]
    ordered = engine.sort_rows(
        rows,
        {"field": "engagement.last_client_view", "direction": "desc"},
        value_of=lambda row: row.get("engagement.last_client_view"),
        key_of=lambda row: row["id"],
    )
    assert [row["engagement.last_client_view"] for row in ordered] == [
        "2026-12-01",
        "2026-06-01",
        "2026-01-01",
    ]


def test_rows_with_no_value_go_last_in_both_directions():
    """The nulls-last rule. A rep wants the empty rows out of the way, not on top."""
    values = [None, "b", None, "a"]
    assert _sort(values) == ["r3", "r1", "r0", "r2"]
    assert _sort(values, direction="desc") == ["r1", "r3", "r0", "r2"]


def test_equal_rows_are_ordered_by_id_so_a_refresh_does_not_reshuffle_them():
    """In *both* directions. A single ``reverse=True`` pass would reverse the
    tie-break along with the field, which is the subtlety the two-pass sort
    exists for."""
    rows = [
        {"id": "r2", "dock.stage": "x"},
        {"id": "r0", "dock.stage": "x"},
        {"id": "r1", "dock.stage": "x"},
    ]
    for direction in ("asc", "desc"):
        ordered = engine.sort_rows(
            rows,
            {"field": "dock.stage", "direction": direction},
            value_of=lambda row: row.get("dock.stage"),
            key_of=lambda row: row["id"],
        )
        assert [row["id"] for row in ordered] == ["r0", "r1", "r2"], direction


def test_the_nulls_also_keep_their_id_order():
    rows = [{"id": "r2"}, {"id": "r0"}, {"id": "r1", "dock.stage": "x"}]
    for direction in ("asc", "desc"):
        ordered = engine.sort_rows(
            rows,
            {"field": "dock.stage", "direction": direction},
            value_of=lambda row: row.get("dock.stage"),
            key_of=lambda row: row["id"],
        )
        assert [row["id"] for row in ordered] == ["r1", "r0", "r2"], direction


def test_sorting_survives_mixed_types_in_one_column():
    """Schema flexibility means a column can hold anything, and a sort must not 500.

    A value that is not a number in a number column cannot be ordered among
    numbers, so it sorts with the rows that have no value - last, in both
    directions - rather than being coerced into something it is not.
    """
    assert _sort([None, 5, "abc", "", 2.5], field="engagement.views") == [
        "r4",
        "r1",
        "r0",
        "r2",
        "r3",
    ]


def test_an_unknown_sort_field_is_refused_rather_than_guessed_at():
    """ "Opportunity Stage is not Close Date" is a wrong answer, not a partial one."""
    with pytest.raises(TriageError) as caught:
        engine.validate_sort({"field": "salesforce.close_date"})
    assert "salesforce.opp_amount" in str(caught.value)


def test_no_sort_spec_falls_back_to_a_stable_default():
    assert engine.validate_sort(None) == {"field": "dock.name", "direction": "asc"}


@pytest.mark.parametrize(
    "raw", [{}, {"field": ""}, {"field": "dock.name", "direction": "up"}, "dock.name", 7]
)
def test_a_malformed_sort_is_refused(raw):
    with pytest.raises(TriageError):
        engine.validate_sort(raw)


def test_dedupe_columns_keeps_the_first_position_and_reports_the_repeat():
    kept, dropped = engine.dedupe_columns(["a", "b", "a", "", "c", "b"])
    assert kept == ["a", "b", "c"]
    assert dropped == ["a", "b"]


# -- field extraction ---------------------------------------------------------- #


@pytest.mark.parametrize(
    "value,expected",
    [("Salesforce", "salesforce"), ("hubspot", "hubspot"), ("hubspot.com", "hubspot"), ("", "")],
)
def test_the_provider_is_read_however_a_team_spells_it(value, expected):
    assert normalise_provider(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (184000, 184000.0),
        ("184,000", 184000.0),
        ("not set", None),
        (None, None),
        (True, None),
        ("", None),
    ],
)
def test_a_number_is_never_invented_out_of_a_value_that_is_not_one(value, expected):
    """A missing amount and a zero amount are different facts about a deal."""
    assert as_number(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "2026-09-27",
        "2026-09-27T10:00:00Z",
        "2026-09-27T10:00:00+00:00",
        "2026-09-27T20:00:00+10:00",
    ],
)
def test_a_timestamp_is_parsed_to_utc(value):
    parsed = parse_time(value)
    assert parsed is not None and parsed.tzinfo is not None
    assert parsed.astimezone(timezone.utc).year == 2026


@pytest.mark.parametrize("value", ["not a date", "", None, 12345, {"a": 1}, "2026-13-45"])
def test_an_unparseable_timestamp_is_none_rather_than_the_epoch(value):
    """A recency filter must not treat an unreadable date as "long ago"."""
    assert parse_time(value) is None
    assert iso(value) is None


def test_a_naive_timestamp_is_read_as_utc_rather_than_local():
    """Otherwise "in the last 7 days" depends on which machine ran the query."""
    assert parse_time("2026-09-27T10:00:00") == parse_time("2026-09-27T10:00:00Z")


def test_a_field_is_found_under_any_of_its_synonyms():
    data = {"sponsor": "dana", "branding": {"theme": "dark"}}
    assert first_text(data, ("owner", "sponsor")) == "dana"
    assert dig(data, "branding.theme") == "dark"
    assert dig(data, "branding.missing.deeper") is None
    assert dig(data, "theme") is None
    assert as_text(None) == "" and as_text("  ") == ""


def test_first_time_takes_the_first_parsable_candidate():
    data = {"created_at": "nonsense", "occurred_at": "2026-09-20T00:00:00Z"}
    assert iso(first_time(data, ("occurred_at", "created_at"))) == "2026-09-20T00:00:00+00:00"


# -- engagement ---------------------------------------------------------------- #


def test_engagement_counts_views_and_actions_separately():
    """The research lists Views and Actions as two columns, so they are two numbers."""
    result = aggregate_engagement(
        [
            {"data": {"action": "viewed", "occurred_at": "2026-09-20T00:00:00Z"}},
            {"data": {"action": "downloaded", "occurred_at": "2026-09-21T00:00:00Z"}},
            {"data": {"action": "viewed", "occurred_at": "2026-09-22T00:00:00Z"}},
        ]
    )
    assert (result.views, result.actions) == (2, 3)
    assert result.last_client_view == "2026-09-22T00:00:00+00:00"


def test_a_workspace_with_no_activity_has_zeroes_and_no_timestamp():
    """ "Never viewed" and "viewed long ago" are different rows in a triage table."""
    empty = aggregate_engagement([])
    assert empty == Engagement(0, 0, None)
    assert empty.last_client_view is None


def test_a_view_with_an_unreadable_timestamp_still_counts_but_cannot_be_the_last_view():
    result = aggregate_engagement(
        [
            {"data": {"action": "viewed", "occurred_at": "whenever"}},
            {"data": {"action": "viewed", "occurred_at": "2026-01-01T00:00:00Z"}},
        ]
    )
    assert result.views == 2
    assert result.last_client_view == "2026-01-01T00:00:00+00:00"


def test_the_researched_workspace_event_names_all_count_as_a_view():
    """ "workspace.viewed", "workspace.page.viewed", "workspace.file.viewed"."""
    result = aggregate_engagement(
        [
            {"data": {"event": "workspace.page.viewed", "occurred_at": "2026-09-20T00:00:00Z"}},
            {"data": {"kind": "workspace.file.viewed", "occurred_at": "2026-09-21T00:00:00Z"}},
            {"data": {"action": "workspace.viewed", "occurred_at": "2026-09-22T00:00:00Z"}},
        ]
    )
    assert result.views == 3
    assert result.actions == 3


# -- declared sections ---------------------------------------------------------- #


@pytest.mark.parametrize(
    "declared,expected",
    [
        (["overview", "pricing"], ["overview", "pricing"]),
        ([{"key": "a"}, {"name": "b"}], ["a", "b"]),
        ({"key": "a"}, ["a"]),
        (None, []),
    ],
)
def test_sections_are_read_from_whatever_the_workspace_declares(declared, expected):
    """The research names the show/hide pattern and no section list, so there is
    no enum here."""
    assert [
        entry["key"] for entry in declared_sections({"data": {"sections": declared}})
    ] == expected


def test_a_repeated_section_key_is_kept_once():
    declared = [{"key": "a", "label": "First"}, {"key": "a", "label": "Second"}]
    assert declared_sections({"data": {"sections": declared}}) == [{"key": "a", "label": "First"}]


def test_a_workspace_that_declares_no_sections_has_none():
    assert declared_sections({"data": {}}) == []
    assert declared_sections(None) == []


# =========================================================================== #
# 2. The HTTP surface
# =========================================================================== #


def test_the_feature_is_mounted_by_discovery_under_its_own_prefix(client):
    body = client.get("/api/features").json()
    record = {feature["id"]: feature for feature in body["features"]}[FEATURE_ID]
    assert record["prefix"] == PREFIX
    assert record["ticket"] == "WF-022"
    assert record["exception_handlers"] == ["TriageError"]
    assert body["failed_count"] == 0, body["failed"]


def test_no_other_feature_claims_this_prefix(client):
    """A ticket-derived prefix cannot collide, and this proves it is this
    feature's alone. Other features *do* share prefixes - three share
    ``/api/library`` - so the claim is about ownership, not uniqueness."""
    body = client.get("/api/features").json()
    owners = [feature["id"] for feature in body["features"] if feature["prefix"] == PREFIX]
    assert owners == [FEATURE_ID]


def test_vocabulary_serves_the_columns_operators_and_both_filter_groups(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert len(body["columns"]) == len(vocab.ALL_COLUMN_KEYS)
    assert body["groups"] == {
        "workspace": ["dock", "engagement"],
        "domain": ["salesforce", "hubspot", "order_form"],
    }
    assert body["match_modes"] == ["all", "any"]
    assert body["visibilities"] == ["private", "public"]
    assert body["me_sentinel"] == "$me"
    assert "default_views" in body


def test_default_views_endpoint_serves_the_five_with_their_definitions(client):
    body = client.get(f"{PREFIX}/default-views").json()
    assert body["count"] == 5
    quoted = [view for view in body["default_views"] if view.get("quote")]
    assert {view["id"] for view in quoted} == {"active-pipeline", "deal-desk"}


def test_inferences_serves_the_sourced_half_beside_the_inferred_half(client):
    body = client.get(f"{PREFIX}/inferences").json()
    assert body["count"] >= 12
    assert body["sourced"]["properties_fallback"] == ["id", "object", "url"]
    assert "active-pipeline-two-arms" in {entry["id"] for entry in body["inferences"]}
    for entry in body["inferences"]:
        assert {"id", "topic", "basis", "value", "why", "change_it", "blast_radius"} <= set(entry)


# -- Add view ------------------------------------------------------------------ #


def test_add_view_from_the_active_pipeline_default_copies_its_two_armed_filter(client):
    view = add_view(client, base="active-pipeline")
    assert view["name"] == "Active Pipeline"
    assert view["base"] == "active-pipeline"
    assert view["match"] == "any"
    assert view["workspace_filters"]["conditions"] == [
        {"field": "dock.type", "op": "is", "value": "Sales"}
    ]
    assert view["workspace_domain_filters"]["join"] == "or"
    assert view["visibility"] == "private"
    assert view["owner"] == "dana"


def test_add_view_from_my_workspaces_resolves_the_sentinel_to_the_actor(client):
    """The stored filter is a plain equality, so it stays indexable and readable."""
    view = add_view(client, actor="sam", base="my")
    assert view["workspace_filters"]["conditions"] == [
        {"field": "dock.owner", "op": "is", "value": "sam"}
    ]
    assert vocab.ME not in str(view)


def test_a_blank_view_needs_a_name(client):
    response = client.post(f"{PREFIX}/views", json={}, params={"actor": "dana"})
    assert response.status_code == 422
    assert "name is required" in response.json()["detail"]


def test_a_blank_view_gets_a_readable_default_column_set(client):
    """Twenty columns is a table nobody can read, and every one is one click away."""
    from dsr.triage.views import BLANK_COLUMNS

    view = add_view(client, name="Scratch")
    assert view["columns"] == list(BLANK_COLUMNS)
    assert view["workspace_filters"] == {"join": "and", "conditions": []}
    assert view["match"] == "all"


def test_an_unknown_default_is_refused_and_the_five_are_named(client):
    response = client.post(f"{PREFIX}/views", json={"base": "everything"}, params={"actor": "dana"})
    assert response.status_code == 422
    assert "active-pipeline" in response.json()["detail"]


def test_a_private_view_needs_an_owner(client):
    response = client.post(f"{PREFIX}/views", json={"name": "Orphan"})
    assert response.status_code == 422
    assert "owner" in response.json()["detail"]


def test_creating_a_view_writes_exactly_one_record_and_audits_the_route(client):
    add_view(client, name="Audited")
    entries = [entry for entry in audit(client) if PREFIX in (entry["source"] or "")]
    assert len(entries) == 1
    assert entries[0]["source"] == f"POST {PREFIX}/views"
    assert entries[0]["action"] == "insert"
    assert entries[0]["actor"] == "dana"


# -- the joined table ----------------------------------------------------------- #


def _pipeline(client):
    """A five-workspace dataset covering both arms, both providers and no activity.

    The arms are covered by construction, which is the point: a rule that only
    found one of them would still render a perfectly plausible table.
    """
    sales = make_template(client, "Sales room", "Sales")
    onboarding = make_template(client, "Customer onboarding", "Implementation")
    blank = make_template(client, "Uncategorised room")

    both = room(client, "Both arms", type="Sales", owner="dana", template_id=sales)
    crm_link(
        client,
        both,
        provider="salesforce",
        opportunity_stage="Negotiation/Review",
        opp_amount=184000,
    )
    # Arm one alone: typed Sales, and no CRM connection at all.
    arm_one = room(client, "Arm one only", type="Sales", owner="sam")
    # Arm two alone: a connected deal, and a type that is not Sales.
    arm_two = room(client, "Arm two only", owner="dana", template_id=onboarding)
    crm_link(client, arm_two, provider="hubspot", deal_stage="Contract Sent", deal_amount=42000)
    # Neither arm: a template that categorises nothing, and no CRM link.
    neither = room(client, "Neither arm", owner="sam", template_id=blank)
    # Arm two alone again, and never viewed by anybody.
    silent = room(client, "Never viewed", owner="dana")
    crm_link(client, silent, provider="hubspot", deal_stage="Contact Created", deal_amount=8000)

    activity(client, both, "viewed", when=NOW - timedelta(days=1))
    activity(client, both, "downloaded", when=NOW - timedelta(days=2))
    return {
        "both": both,
        "arm_one": arm_one,
        "arm_two": arm_two,
        "neither": neither,
        "silent": silent,
        "templates": {"sales": sales, "onboarding": onboarding, "blank": blank},
    }


def test_the_table_applies_both_arms_of_the_disjunction_over_real_workspaces(client):
    rooms = _pipeline(client)
    view = add_view(client, base="active-pipeline", name="Pipeline")
    body = rows_of(client, view["id"])
    assert names(body) == ["Arm one only", "Arm two only", "Both arms", "Never viewed"]
    assert rooms["neither"] not in {row["id"] for row in body["rows"]}
    assert body["problems"] == []


def test_the_table_reports_where_each_workspaces_type_came_from(client):
    _pipeline(client)
    view = add_view(client, base="all", name="Everything")
    body = rows_of(client, view["id"])
    sources = {row["dock.name"]: row["meta"]["type_source"] for row in body["rows"]}
    assert sources["Both arms"] == "workspace", "typed by hand, even though its template is typed"
    assert sources["Arm one only"] == "workspace"
    assert sources["Arm two only"] == "template", "inherited, because the workspace never chose"
    assert sources["Neither arm"] is None, "a template with no type categorises nothing"


def test_a_salesforce_column_is_null_on_a_hubspot_linked_workspace(client):
    """A stage from a system this workspace is not connected to is a wrong number."""
    _pipeline(client)
    view = add_view(
        client,
        name="Both providers",
        columns=[
            "dock.name",
            "hubspot.deal_stage",
            "salesforce.opportunity_stage",
            "salesforce.opp_amount",
        ],
    )
    body = rows_of(client, view["id"])
    hubspot_row = next(row for row in body["rows"] if row["dock.name"] == "Arm two only")
    assert hubspot_row["meta"]["crm_provider"] == "hubspot"
    assert hubspot_row["hubspot.deal_stage"] == "Contract Sent"
    assert hubspot_row["salesforce.opportunity_stage"] is None
    assert hubspot_row["salesforce.opp_amount"] is None


def test_a_salesforce_field_name_on_a_hubspot_record_is_still_gated_off(client):
    """Even a payload carrying both vocabularies only shows the connected one."""
    _pipeline(client)
    mixed = room(client, "Mixed vocabulary")
    crm_link(
        client,
        mixed,
        provider="hubspot",
        deal_stage="Qualified",
        opportunity_stage="Closed Lost",
        opp_amount=999999,
    )
    row = row_of(
        client, mixed, "hubspot.deal_stage", "salesforce.opportunity_stage", "salesforce.opp_amount"
    )
    assert row["hubspot.deal_stage"] == "Qualified"
    assert row["salesforce.opportunity_stage"] is None
    assert row["salesforce.opp_amount"] is None


def _room_named(client, name):
    listed = client.get("/api/records/room", params={"limit": 1000}).json()["records"]
    return next(record["id"] for record in listed if record["data"].get("name") == name)


def test_engagement_columns_are_computed_on_read_and_stored_nowhere(client):
    """Nothing is cached, which is what makes "CRM sync keeps the joined columns
    current without user action" true: there is no cached column to fall stale."""
    _pipeline(client)
    both = _room_named(client, "Both arms")
    row = row_of(client, both)
    assert row["engagement.views"] == 1
    assert row["engagement.actions"] == 2
    assert row["engagement.last_client_view"] == (NOW - timedelta(days=1)).isoformat(
        timespec="seconds"
    )
    stored = client.get("/api/records/room", params={"limit": 1000}).json()["records"]
    payload = next(record["data"] for record in stored if record["id"] == both)
    assert "engagement" not in payload and "views" not in payload


def test_a_new_activity_record_changes_the_number_without_anything_being_refreshed(client):
    _pipeline(client)
    both = _room_named(client, "Both arms")
    before = row_of(client, both, "engagement.actions")["engagement.actions"]
    activity(client, both, "commented", when=NOW)
    after = row_of(client, both, "engagement.actions")["engagement.actions"]
    assert after == before + 1


def test_a_workspace_nobody_viewed_has_an_empty_last_client_view(client):
    _pipeline(client)
    silent = _room_named(client, "Never viewed")
    row = row_of(client, silent, "engagement.views", "engagement.last_client_view")
    assert row["engagement.last_client_view"] is None
    assert row["engagement.views"] == 0


def test_the_table_carries_the_views_columns_in_the_views_order(client):
    room(client, "One", owner="dana")
    view = add_view(client, name="Ordered", columns=["dock.owner", "dock.name"])
    body = rows_of(client, view["id"])
    assert body["columns"] == ["dock.owner", "dock.name"]
    assert set(body["rows"][0]) >= {"id", "object", "url", "dock.owner", "dock.name", "meta"}


def test_the_table_paging_reports_the_total_not_the_page_size(client):
    for index in range(5):
        room(client, f"Room {index}")
    view = add_view(client, base="all", name="All")
    body = rows_of(client, view["id"], limit=2)
    assert body["count"] == 2
    assert body["total"] == 5
    assert body["limit"] == 2
    second = rows_of(client, view["id"], limit=2, offset=2)
    assert second["count"] == 2
    assert len({row["id"] for row in body["rows"]} & {row["id"] for row in second["rows"]}) == 0


def test_a_column_the_view_names_but_the_row_cannot_supply_is_present_and_null(client):
    room(client, "No CRM", owner="dana")
    view = add_view(client, name="Wide", columns=["dock.name", "hubspot.deal_amount"])
    row = rows_of(client, view["id"])["rows"][0]
    assert row["hubspot.deal_amount"] is None, "a blank cell, not a missing column"


def test_an_unknown_column_is_carried_and_flagged_not_refused(client):
    """ "the column/filter set is user-defined so new CRM fields flow through"."""
    room(client, "No CRM", owner="dana")
    view = add_view(client, name="Future", columns=["dock.name", "salesforce.discount_pct"])
    body = rows_of(client, view["id"])
    assert body["columns"] == ["dock.name", "salesforce.discount_pct"]
    flagged = [entry for entry in body["column_meta"] if entry["key"] == "salesforce.discount_pct"]
    assert flagged[0]["known"] is False
    assert flagged[0]["unknown_columns"] == ["salesforce.discount_pct"]
    assert body["rows"][0]["salesforce.discount_pct"] is None


def test_a_repeated_column_is_dropped_and_reported(client):
    view = add_view(client, name="Repeats", columns=["dock.name", "dock.owner", "dock.name"])
    assert view["columns"] == ["dock.name", "dock.owner"]
    assert view["dropped_duplicate_columns"] == ["dock.name"]


def test_a_view_with_no_columns_is_refused(client):
    response = client.post(
        f"{PREFIX}/views", json={"name": "Empty", "columns": []}, params={"actor": "dana"}
    )
    assert response.status_code == 422
    assert "at least one column" in response.json()["detail"]


def test_a_view_with_an_unreadable_filter_still_renders_and_says_why(client):
    """A triage table that hid rows for a typo it would not name is the failure
    this workflow cannot ship."""
    room(client, "Visible", owner="dana")
    view = add_view(
        client,
        name="Broken",
        workspace_filters=group(condition("dock.owner", "sounds_like", "dana")),
    )
    body = rows_of(client, view["id"])
    assert body["total"] == 1, "the unreadable condition is dropped, not fatal"
    assert [problem["kind"] for problem in body["problems"]] == [engine.PROBLEM_UNKNOWN_OPERATOR]
    assert client.get(f"{PREFIX}/views/{view['id']}", params={"actor": "dana"}).json()["problems"]


def test_sorting_through_the_table_puts_the_most_recently_viewed_first(client):
    _pipeline(client)
    view = add_view(
        client,
        name="Recency",
        columns=["dock.name", "engagement.last_client_view"],
        sort={"field": "engagement.last_client_view", "direction": "desc"},
    )
    body = rows_of(client, view["id"])
    ordered = [row["dock.name"] for row in body["rows"]]
    assert ordered[0] == "Both arms", "the only workspace anybody has looked at"
    assert set(ordered[1:]) == {"Arm one only", "Arm two only", "Neither arm", "Never viewed"}
    assert all(row["engagement.last_client_view"] is None for row in body["rows"][1:])


def test_an_unknown_sort_field_is_a_422_naming_the_published_columns(client):
    response = client.post(
        f"{PREFIX}/views",
        json={"name": "Bad sort", "sort": {"field": "crm.whatever"}},
        params={"actor": "dana"},
    )
    assert response.status_code == 422
    assert "hubspot.deal_amount" in response.json()["detail"]


# -- the properties parameter ---------------------------------------------------- #


def test_omitting_properties_returns_only_id_object_and_url(client):
    """ "If you omit it, the response contains only the resource's `id`, `object`,
    and `url`" - implemented literally."""
    target = room(client, "Just the envelope", owner="dana", stage="evaluation")
    body = client.get(f"{PREFIX}/rooms/{target}/row").json()
    assert set(body["row"]) == {"id", "object", "url"}
    assert body["row"]["object"] == "workspace"
    assert body["row"]["url"] == f"{PREFIX}/rooms/{target}/row"
    assert body["requested"] is None
    assert body["unknown"] == []


def test_properties_selects_which_fields_come_back(client):
    target = room(client, "Selected", owner="dana")
    crm_link(
        client, target, provider="salesforce", opportunity_stage="Qualification", opp_amount=1000
    )
    body = client.get(
        f"{PREFIX}/rooms/{target}/row", params={"properties": "dock.name,salesforce.opp_amount"}
    ).json()
    assert body["row"]["dock.name"] == "Selected"
    assert body["row"]["salesforce.opp_amount"] == 1000
    assert "dock.owner" not in body["row"]
    assert body["requested"] == ["dock.name", "salesforce.opp_amount"]


def test_an_unknown_property_is_reported_rather_than_refused(client):
    target = room(client, "Unknown property")
    body = client.get(
        f"{PREFIX}/rooms/{target}/row", params={"properties": "dock.name,crm.mystery"}
    ).json()
    assert body["unknown"] == ["crm.mystery"]
    assert body["row"]["dock.name"] == "Unknown property"
    assert "salesforce.opp_amount" in body["available"]


def test_an_empty_properties_value_still_returns_the_three_envelope_fields(client):
    target = room(client, "Empty selection")
    body = client.get(f"{PREFIX}/rooms/{target}/row", params={"properties": ""}).json()
    assert set(body["row"]) == {"id", "object", "url"}
    assert body["requested"] == []


def test_the_row_of_a_workspace_that_does_not_exist_is_a_422(client):
    response = client.get(f"{PREFIX}/rooms/room_missing/row")
    assert response.status_code == 422
    assert "not found" in response.json()["detail"]


def test_an_integral_amount_is_rendered_as_an_integer_not_a_float(client):
    target = room(client, "Money")
    crm_link(client, target, provider="salesforce", opp_amount=184000)
    assert row_of(client, target, "salesforce.opp_amount")["salesforce.opp_amount"] == 184000


# -- private, public, clone ------------------------------------------------------ #


def test_a_public_view_is_visible_to_everyone(client):
    view = add_view(client, actor="dana", name="Team", visibility="public")
    assert client.get(f"{PREFIX}/views/{view['id']}", params={"actor": "sam"}).status_code == 200
    listed = client.get(f"{PREFIX}/views", params={"actor": "sam"}).json()
    assert [entry["id"] for entry in listed["views"]] == [view["id"]]


def test_a_private_view_is_absent_from_another_users_list(client):
    view = add_view(client, actor="dana", name="Mine")
    listed = client.get(f"{PREFIX}/views", params={"actor": "sam"}).json()
    assert listed["count"] == 0
    assert client.get(f"{PREFIX}/views/{view['id']}", params={"actor": "sam"}).status_code == 422


def test_a_private_view_is_not_confirmable_from_the_status_code(client):
    """404 for a view you may not read, not 403 - a private view's existence is
    not another user's business. The two answers are the same shape; the message
    necessarily names different ids, so only the shape is compared."""
    view = add_view(client, actor="dana", name="Mine")
    denied = client.get(f"{PREFIX}/views/{view['id']}", params={"actor": "sam"})
    missing = client.get(f"{PREFIX}/views/saved_view_nope", params={"actor": "sam"})
    assert denied.status_code == missing.status_code == 422
    assert denied.json()["error"] == missing.json()["error"] == "invalid_view"
    assert denied.json()["detail"].endswith("not found")


def test_a_private_view_with_an_empty_owner_is_readable_by_nobody(client):
    """``owner`` is never empty for a view created through the API, so this is the
    guard against a hand-written record that forgot it."""
    from dsr.triage.views import TriageBoard

    assert TriageBoard.can_read({"visibility": "private", "owner": ""}, None) is False
    assert TriageBoard.can_read({"visibility": "private", "owner": ""}, "") is False
    assert TriageBoard.can_read({"visibility": "private", "owner": "dana"}, "dana") is True
    assert TriageBoard.can_read({"visibility": "public", "owner": ""}, None) is True


def test_a_teammate_may_edit_a_public_view(client):
    """ "public views for your entire team" - documented in ``public-views-are-team-editable``."""
    view = add_view(client, actor="dana", name="Team", visibility="public")
    patched = client.patch(
        f"{PREFIX}/views/{view['id']}", json={"name": "Team pipeline v2"}, params={"actor": "sam"}
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Team pipeline v2"


def test_a_teammate_may_not_edit_or_delete_someone_elses_private_view(client):
    view = add_view(client, actor="dana", name="Mine")
    assert (
        client.patch(
            f"{PREFIX}/views/{view['id']}", json={"name": "Hijacked"}, params={"actor": "sam"}
        ).status_code
        == 422
    )
    assert (
        client.request(
            "DELETE", f"{PREFIX}/views/{view['id']}", params={"actor": "sam"}
        ).status_code
        == 422
    )


def test_a_clone_of_a_public_view_is_private_and_belongs_to_the_cloner(client):
    """ "clone existing views to make your own customized copy", and a custom copy
    is one of the "private views for yourself"."""
    public = add_view(
        client, actor="dana", name="Team", visibility="public", base="active-pipeline"
    )
    clone = client.post(
        f"{PREFIX}/views/{public['id']}/clone", json={}, params={"actor": "sam"}
    ).json()
    assert clone["name"] == "Team (copy)"
    assert clone["visibility"] == "private"
    assert clone["owner"] == "sam"
    assert clone["cloned_from"] == public["id"]
    assert clone["base"] == "active-pipeline"
    assert clone["columns"] == public["columns"]
    assert clone["workspace_filters"] == public["workspace_filters"]
    assert clone["sort"] == public["sort"]


def test_a_clone_can_be_customised_in_the_same_request(client):
    public = add_view(client, actor="dana", name="Team", visibility="public")
    clone = client.post(
        f"{PREFIX}/views/{public['id']}/clone",
        json={
            "name": "Mine only",
            "columns": ["dock.name", "hubspot.deal_amount"],
            "sort": {"field": "hubspot.deal_amount", "direction": "desc"},
            "visibility": "public",
        },
        params={"actor": "sam"},
    ).json()
    assert clone["name"] == "Mine only"
    assert clone["columns"] == ["dock.name", "hubspot.deal_amount"]
    assert clone["sort"]["direction"] == "desc"
    assert clone["visibility"] == "private", "a clone is private whatever the body asks for"


def test_a_clone_of_someone_elses_private_view_is_a_422(client):
    private = add_view(client, actor="dana", name="Mine")
    assert (
        client.post(
            f"{PREFIX}/views/{private['id']}/clone", json={}, params={"actor": "sam"}
        ).status_code
        == 422
    )


def test_cloning_needs_an_actor_because_the_copy_belongs_to_somebody(client):
    public = add_view(client, actor="dana", name="Team", visibility="public")
    response = client.post(f"{PREFIX}/views/{public['id']}/clone", json={})
    assert response.status_code == 422
    assert "belongs to whoever made it" in response.json()["detail"]


def test_the_clone_route_audits_the_clone_route(client):
    public = add_view(client, actor="dana", name="Team", visibility="public")
    clone = client.post(
        f"{PREFIX}/views/{public['id']}/clone", json={}, params={"actor": "sam"}
    ).json()
    sources = [
        entry["source"]
        for entry in audit(client)
        if entry["source"] == f"POST {PREFIX}/views/{public['id']}/clone"
    ]
    assert len(sources) == 1
    assert clone["cloned_from"] == public["id"]


# -- editing a view ---------------------------------------------------------------- #


def test_editing_replaces_the_column_list_wholesale_so_it_can_be_rearranged(client):
    """ "Edit and rearrange **columns**" is a statement about the whole list."""
    view = add_view(client, name="Editable", columns=["dock.name", "dock.owner", "dock.stage"])
    reordered = client.patch(
        f"{PREFIX}/views/{view['id']}",
        json={"columns": ["dock.stage", "dock.name"]},
        params={"actor": "dana"},
    ).json()
    assert reordered["columns"] == ["dock.stage", "dock.name"]


def test_editing_a_filter_changes_what_the_table_returns(client):
    _pipeline(client)
    view = add_view(client, name="Owned by sam")
    assert rows_of(client, view["id"], actor="dana")["total"] == 5
    narrowed = client.patch(
        f"{PREFIX}/views/{view['id']}",
        json={"workspace_filters": group(condition("dock.owner", "is", "sam"))},
        params={"actor": "dana"},
    )
    assert narrowed.status_code == 200
    body = rows_of(client, view["id"], actor="dana")
    assert body["total"] == 2
    assert names(body) == ["Arm one only", "Neither arm"]


def test_editing_a_view_to_private_makes_the_editor_its_owner(client):
    """Otherwise a teammate could hide a team view with one flag and the original
    owner would lose sight of it."""
    public = add_view(client, actor="dana", name="Team", visibility="public")
    made_private = client.patch(
        f"{PREFIX}/views/{public['id']}", json={"visibility": "private"}, params={"actor": "sam"}
    ).json()
    assert made_private["owner"] == "sam"
    assert client.get(f"{PREFIX}/views/{public['id']}", params={"actor": "dana"}).status_code == 422


def test_an_empty_name_is_refused(client):
    view = add_view(client, name="Named")
    response = client.patch(
        f"{PREFIX}/views/{view['id']}", json={"name": "  "}, params={"actor": "dana"}
    )
    assert response.status_code == 422
    assert "name cannot be empty" in response.json()["detail"]


def test_an_unrecognised_visibility_is_refused_and_the_two_are_named(client):
    response = client.post(
        f"{PREFIX}/views",
        json={"name": "Secret", "visibility": "unlisted"},
        params={"actor": "dana"},
    )
    assert response.status_code == 422
    assert "private" in response.json()["detail"] and "public" in response.json()["detail"]


def test_columns_must_be_a_list(client):
    view = add_view(client, name="Named")
    response = client.patch(
        f"{PREFIX}/views/{view['id']}", json={"columns": "dock.name"}, params={"actor": "dana"}
    )
    assert response.status_code == 422


def test_editing_nothing_returns_the_view_unchanged(client):
    view = add_view(client, name="Named")
    response = client.patch(f"{PREFIX}/views/{view['id']}", json={}, params={"actor": "dana"})
    assert response.status_code == 200
    assert response.json()["name"] == "Named"
    assert (
        len([e for e in audit(client) if e["source"] == f"PATCH {PREFIX}/views/{view['id']}"]) == 0
    )


def test_deleting_a_view_is_a_soft_delete_that_keeps_the_record_auditable(client):
    view = add_view(client, name="Doomed")
    assert (
        client.request(
            "DELETE", f"{PREFIX}/views/{view['id']}", params={"actor": "dana"}
        ).status_code
        == 204
    )
    assert client.get(f"{PREFIX}/views/{view['id']}", params={"actor": "dana"}).status_code == 422
    assert (
        client.get("/api/records/saved_view", params={"include_deleted": True}).json()["count"] == 1
    )
    assert audit(client, action="delete")


def test_listing_views_can_be_narrowed_to_one_visibility(client):
    add_view(client, actor="dana", name="Team", visibility="public")
    add_view(client, actor="dana", name="Mine")
    public = client.get(f"{PREFIX}/views", params={"actor": "dana", "visibility": "public"}).json()
    assert [entry["name"] for entry in public["views"]] == ["Team"]
    assert public["public"] == 1 and public["private"] == 0
    assert (
        client.get(f"{PREFIX}/views", params={"actor": "dana", "visibility": "secret"}).status_code
        == 422
    )


# -- the remembered open set ------------------------------------------------------- #


def test_the_dashboard_opens_on_an_empty_set_for_an_account_that_has_never_visited(client):
    body = client.get(f"{PREFIX}/open-views", params={"actor": "dana"}).json()
    assert body == {
        "actor": "dana",
        "remembered": False,
        "view_ids": [],
        "active_view_id": None,
        "views": [],
        "missing": [],
    }


def test_the_open_set_is_remembered_per_account(client):
    first = add_view(client, actor="dana", name="Danas")
    second = add_view(client, actor="sam", name="Sams")
    client.put(f"{PREFIX}/open-views", json={"actor": "dana", "view_ids": [first["id"]]})
    assert client.get(f"{PREFIX}/open-views", params={"actor": "dana"}).json()["view_ids"] == [
        first["id"]
    ]
    assert client.get(f"{PREFIX}/open-views", params={"actor": "sam"}).json()["view_ids"] == []
    assert (
        second["id"]
        not in client.get(f"{PREFIX}/open-views", params={"actor": "dana"}).json()["view_ids"]
    )


def test_remembering_replaces_the_set_rather_than_merging_it(client):
    """A merge would keep a view the user has closed in the set for ever."""
    first = add_view(client, actor="dana", name="One")
    second = add_view(client, actor="dana", name="Two")
    client.put(
        f"{PREFIX}/open-views", json={"actor": "dana", "view_ids": [first["id"], second["id"]]}
    )
    body = client.put(
        f"{PREFIX}/open-views", json={"actor": "dana", "view_ids": [second["id"]]}
    ).json()
    assert body["view_ids"] == [second["id"]]


def test_the_active_view_must_be_one_of_the_open_views(client):
    view = add_view(client, actor="dana", name="One")
    other = add_view(client, actor="dana", name="Two")
    response = client.put(
        f"{PREFIX}/open-views",
        json={"actor": "dana", "view_ids": [view["id"]], "active_view_id": other["id"]},
    )
    assert response.status_code == 422
    assert "no way to show it" in response.json()["detail"]


def test_the_open_set_cannot_be_used_to_confirm_a_private_views_id(client):
    """Otherwise the remembered set is a side channel for discovering one."""
    private = add_view(client, actor="dana", name="Mine")
    assert (
        client.put(
            f"{PREFIX}/open-views", json={"actor": "sam", "view_ids": [private["id"]]}
        ).status_code
        == 422
    )
    assert (
        client.put(
            f"{PREFIX}/open-views", json={"actor": "sam", "view_ids": ["saved_view_invented"]}
        ).status_code
        == 422
    )


def test_remembering_the_open_set_needs_an_account(client):
    response = client.put(f"{PREFIX}/open-views", json={"view_ids": []})
    assert response.status_code == 422
    assert "per user account" in response.json()["detail"]


def test_a_view_deleted_since_it_was_remembered_is_reported_not_fatal(client):
    """ "We'll remember which views you had open" - the dashboard has to open."""
    first = add_view(client, actor="dana", name="One")
    second = add_view(client, actor="dana", name="Two")
    client.put(
        f"{PREFIX}/open-views",
        json={
            "actor": "dana",
            "view_ids": [first["id"], second["id"]],
            "active_view_id": first["id"],
        },
    )
    client.request("DELETE", f"{PREFIX}/views/{second['id']}", params={"actor": "dana"})
    body = client.get(f"{PREFIX}/open-views", params={"actor": "dana"}).json()
    assert body["view_ids"] == [first["id"], second["id"]]
    assert body["missing"] == [second["id"]]
    assert [view["id"] for view in body["views"]] == [first["id"]]
    assert client.get(f"{PREFIX}/dashboard", params={"actor": "dana"}).status_code == 200


def test_a_view_that_stopped_being_yours_is_reported_as_missing_too(client):
    """Public today, private tomorrow: the remembered set must not keep serving it."""
    view = add_view(client, actor="dana", name="Was team", visibility="public")
    client.put(f"{PREFIX}/open-views", json={"actor": "sam", "view_ids": [view["id"]]})
    client.patch(
        f"{PREFIX}/views/{view['id']}", json={"visibility": "private"}, params={"actor": "dana"}
    )
    body = client.get(f"{PREFIX}/open-views", params={"actor": "sam"}).json()
    assert body["missing"] == [view["id"]]
    assert body["views"] == []


def test_a_duplicate_id_in_the_open_set_is_collapsed(client):
    view = add_view(client, actor="dana", name="One")
    body = client.put(
        f"{PREFIX}/open-views", json={"actor": "dana", "view_ids": [view["id"], view["id"]]}
    ).json()
    assert body["view_ids"] == [view["id"]]


def test_the_open_set_must_be_a_list(client):
    assert (
        client.put(f"{PREFIX}/open-views", json={"actor": "dana", "view_ids": "abc"}).status_code
        == 422
    )


def test_the_dashboard_serves_the_remembered_views_the_defaults_and_your_own(client):
    team = add_view(client, actor="dana", name="Team", visibility="public")
    client.put(
        f"{PREFIX}/open-views",
        json={"actor": "dana", "view_ids": [team["id"]], "active_view_id": team["id"]},
    )
    body = client.get(f"{PREFIX}/dashboard", params={"actor": "dana"}).json()
    assert body["open_views"] == [team["id"]]
    assert body["active_view_id"] == team["id"]
    assert body["remembered"] is True
    assert body["missing_view_ids"] == []
    assert [view["id"] for view in body["views"]] == [team["id"]]
    assert len(body["default_views"]) == 5
    assert [view["name"] for view in body["your_views"]] == ["Team"]


def test_the_dashboard_for_an_anonymous_reader_still_offers_the_defaults(client):
    body = client.get(f"{PREFIX}/dashboard").json()
    assert body["actor"] is None
    assert body["open_views"] == []
    assert body["remembered"] is False
    assert len(body["default_views"]) == 5


# -- workspace type and its template ---------------------------------------------- #


def test_a_workspace_with_no_type_and_no_template_has_none(client):
    target = room(client, "Untyped")
    body = client.get(f"{PREFIX}/rooms/{target}/type").json()
    assert body == {
        "room_id": target,
        "type": None,
        "source": None,
        "own_type": None,
        "template_id": None,
    }


def test_a_workspace_inherits_its_templates_type(client):
    """The automation: "Any future workspaces created from that template will be
    automatically categorized"."""
    sales = client.post(
        "/api/records/workspace_template", json={"name": "Sales room", "type": "Sales"}
    ).json()["id"]
    target = room(client, "Inherited", template_id=sales)
    body = client.get(f"{PREFIX}/rooms/{target}/type").json()
    assert body["type"] == "Sales"
    assert body["source"] == "template"
    assert body["own_type"] is None
    assert body["template_id"] == sales


def test_a_workspaces_own_type_beats_its_templates(client):
    """A template change must never re-categorise a workspace somebody decided about."""
    sales = client.post(
        "/api/records/workspace_template", json={"name": "Sales", "type": "Sales"}
    ).json()["id"]
    target = room(client, "Decided", template_id=sales, type="Implementation")
    body = client.get(f"{PREFIX}/rooms/{target}/type").json()
    assert (body["type"], body["source"], body["own_type"]) == (
        "Implementation",
        "workspace",
        "Implementation",
    )


def test_changing_a_templates_type_reaches_the_workspaces_that_inherit_it(client):
    sales = client.post(
        "/api/records/workspace_template", json={"name": "Sales", "type": "Sales"}
    ).json()["id"]
    target = room(client, "Inherited", template_id=sales)
    assert client.get(f"{PREFIX}/rooms/{target}/type").json()["type"] == "Sales"
    client.patch(
        f"{PREFIX}/templates/{sales}", json={"type": "Implementation"}, params={"actor": "dana"}
    )
    assert client.get(f"{PREFIX}/rooms/{target}/type").json()["type"] == "Implementation"


def test_changing_a_templates_type_leaves_a_workspace_that_typed_itself_alone(client):
    sales = client.post(
        "/api/records/workspace_template", json={"name": "Sales", "type": "Sales"}
    ).json()["id"]
    target = room(client, "Decided", template_id=sales, type="Implementation")
    client.patch(f"{PREFIX}/templates/{sales}", json={"type": "Renewal"}, params={"actor": "dana"})
    assert client.get(f"{PREFIX}/rooms/{target}/type").json()["type"] == "Implementation"


def test_clearing_a_workspaces_type_returns_it_to_inheriting(client):
    sales = client.post(
        "/api/records/workspace_template", json={"name": "Sales", "type": "Sales"}
    ).json()["id"]
    target = room(client, "Decided", template_id=sales, type="Implementation")
    body = client.patch(
        f"{PREFIX}/rooms/{target}/type", json={"type": None}, params={"actor": "dana"}
    ).json()
    assert (body["type"], body["source"], body["own_type"]) == ("Sales", "template", None)


def test_writing_a_type_requires_the_field_so_a_clear_is_deliberate(client):
    target = room(client, "Typed", type="Sales")
    response = client.patch(f"{PREFIX}/rooms/{target}/type", json={}, params={"actor": "dana"})
    assert response.status_code == 422
    assert "to clear the override" in response.json()["detail"]


def test_typing_a_workspace_that_does_not_exist_is_a_422(client):
    assert (
        client.patch(
            f"{PREFIX}/rooms/room_missing/type", json={"type": "Sales"}, params={"actor": "dana"}
        ).status_code
        == 422
    )


def test_typing_a_template_that_does_not_exist_is_a_422(client):
    """A type written onto a template nobody can see has no effect and no way back."""
    response = client.patch(
        f"{PREFIX}/templates/workspace_template_missing",
        json={"type": "Sales"},
        params={"actor": "dana"},
    )
    assert response.status_code == 422
    assert "not found" in response.json()["detail"]


def test_the_templates_list_counts_only_the_workspaces_that_actually_inherit(client):
    sales = client.post(
        "/api/records/workspace_template", json={"name": "Sales", "type": "Sales"}
    ).json()["id"]
    room(client, "Inherits", template_id=sales)
    room(client, "Also inherits", template_id=sales)
    room(client, "Decided", template_id=sales, type="Implementation")
    listed = {entry["id"]: entry for entry in client.get(f"{PREFIX}/templates").json()["templates"]}
    assert listed[sales]["inheriting"] == 2
    assert listed[sales]["name"] == "Sales"
    assert listed[sales]["type"] == "Sales"


def test_a_template_with_no_type_categorises_nothing(client):
    blank = client.post("/api/records/workspace_template", json={"name": "Blank"}).json()["id"]
    target = room(client, "Uncategorised", template_id=blank)
    body = client.get(f"{PREFIX}/rooms/{target}/type").json()
    assert body["type"] is None
    assert body["template_id"] == blank
    assert body["source"] is None


def test_clearing_a_templates_type_stops_it_categorising(client):
    sales = client.post(
        "/api/records/workspace_template", json={"name": "Sales", "type": "Sales"}
    ).json()["id"]
    target = room(client, "Inherited", template_id=sales)
    client.patch(f"{PREFIX}/templates/{sales}", json={"type": None}, params={"actor": "dana"})
    assert client.get(f"{PREFIX}/rooms/{target}/type").json()["type"] is None


# -- dynamic sections ------------------------------------------------------------- #


def test_the_section_endpoint_reports_no_sections_for_an_empty_workspace(client):
    target = room(client, "No sections")
    body = client.get(f"{PREFIX}/rooms/{target}/sections").json()
    assert body["sections"] == []
    assert body["hidden"] == []
    assert body["rules"] == 0


def test_every_declared_section_is_visible_when_no_rule_says_otherwise(client):
    target = room(client, "Open", sections=["overview", "pricing"])
    body = client.get(f"{PREFIX}/rooms/{target}/sections").json()
    assert [(entry["key"], entry["visible"], entry["reason"]) for entry in body["sections"]] == [
        ("overview", True, "no_rule"),
        ("pricing", True, "no_rule"),
    ]


def test_a_section_whose_rule_does_not_match_is_hidden_with_its_reason(client):
    target = room(client, "Conditional", sections=["overview", "order-form"])
    order_form(client, target, status="voided")
    body = client.put(
        f"{PREFIX}/rooms/{target}/sections",
        json={
            "sections": [
                {
                    "section": "order-form",
                    "visible_when": group(condition("order_form.status", "is", "completed")),
                }
            ]
        },
        params={"actor": "dana"},
    ).json()
    assert body["hidden"] == ["order-form"]
    assert (
        next(entry for entry in body["sections"] if entry["key"] == "order-form")["reason"]
        == "rule_not_matched"
    )
    assert (
        next(entry for entry in body["sections"] if entry["key"] == "overview")["visible"] is True
    )


def test_the_same_rule_hides_nothing_once_the_thing_it_waits_for_has_happened(client):
    target = room(client, "Conditional", sections=["order-form"])
    order_form(client, target, status="completed")
    body = client.put(
        f"{PREFIX}/rooms/{target}/sections",
        json={
            "sections": [
                {
                    "section": "order-form",
                    "visible_when": group(condition("order_form.status", "is", "completed")),
                }
            ]
        },
        params={"actor": "dana"},
    ).json()
    assert body["hidden"] == []
    assert body["sections"][0]["reason"] == "rule_matched"


def test_the_section_rule_uses_the_same_operator_set_as_a_view_filter(client):
    target = room(client, "Conditional", sections=["pricing"])
    crm_link(client, target, provider="salesforce", opp_amount=0)
    body = client.put(
        f"{PREFIX}/rooms/{target}/sections",
        json={
            "sections": [
                {
                    "section": "pricing",
                    "visible_when": group(condition("salesforce.opp_amount", "gt", 0), join="or"),
                }
            ]
        },
        params={"actor": "dana"},
    ).json()
    assert body["hidden"] == ["pricing"], "a zero amount is not greater than zero"


def test_a_rule_for_a_section_the_workspace_does_not_declare_is_refused(client):
    target = room(client, "Sections", sections=["overview"])
    response = client.put(
        f"{PREFIX}/rooms/{target}/sections",
        json={"sections": [{"section": "pricing", "visible_when": group()}]},
        params={"actor": "dana"},
    )
    assert response.status_code == 422
    assert "overview" in response.json()["detail"]


def test_a_rule_with_an_unreadable_condition_is_refused_at_save_time(client):
    target = room(client, "Sections", sections=["overview"])
    response = client.put(
        f"{PREFIX}/rooms/{target}/sections",
        json={
            "sections": [
                {
                    "section": "overview",
                    "visible_when": group(condition("dock.stage", "sounds_like", "x")),
                }
            ]
        },
        params={"actor": "dana"},
    )
    assert response.status_code == 422
    assert "not readable" in response.json()["detail"]


def test_a_rule_left_in_storage_by_a_generic_write_leaves_the_section_visible_and_says_so(client):
    """A broken rule must not take a workspace's pages down with it."""
    target = room(client, "Sections", sections=["overview", "pricing"])
    client.post(
        "/api/records/workspace_section_rule",
        json={
            "section": "pricing",
            "visible_when": {
                "join": "and",
                "conditions": [{"field": "dock.stage", "op": "sounds_like", "value": "x"}],
            },
        },
        params={"room_id": target},
    )
    body = client.get(f"{PREFIX}/rooms/{target}/sections").json()
    pricing = next(entry for entry in body["sections"] if entry["key"] == "pricing")
    assert pricing["visible"] is True
    assert [problem["kind"] for problem in body["problems"]] == [
        engine.PROBLEM_UNKNOWN_OPERATOR,
        "unusable_rule",
    ]


def test_a_rule_for_a_section_the_workspace_stopped_declaring_is_reported(client):
    target = room(client, "Sections", sections=["overview", "pricing"])
    client.put(
        f"{PREFIX}/rooms/{target}/sections",
        json={
            "sections": [
                {"section": "pricing", "visible_when": group(condition("dock.stage", "is", "x"))}
            ]
        },
        params={"actor": "dana"},
    )
    client.patch("/api/records/room/" + target, json={"sections": ["overview"]})
    body = client.get(f"{PREFIX}/rooms/{target}/sections").json()
    assert [problem["kind"] for problem in body["problems"]] == ["unknown_section"]


def test_setting_section_rules_replaces_the_whole_ruleset(client):
    target = room(client, "Sections", sections=["overview", "pricing"])
    client.put(
        f"{PREFIX}/rooms/{target}/sections",
        json={
            "sections": [
                {"section": "pricing", "visible_when": group(condition("dock.stage", "is", "x"))}
            ]
        },
        params={"actor": "dana"},
    )
    body = client.put(
        f"{PREFIX}/rooms/{target}/sections", json={"sections": []}, params={"actor": "dana"}
    ).json()
    assert body["rules"] == 0
    assert body["hidden"] == []


@pytest.mark.parametrize(
    "body_payload",
    [
        {},
        {"sections": "pricing"},
        {"sections": ["pricing"]},
        {"sections": [{"visible_when": group()}]},
        {"sections": [{"section": "overview"}]},
        {
            "sections": [
                {"section": "overview", "visible_when": group()},
                {"section": "overview", "visible_when": group()},
            ]
        },
        {"sections": [{"section": "overview", "visible_when": {"join": "nope", "conditions": []}}]},
    ],
)
def test_a_malformed_ruleset_is_refused(client, body_payload):
    target = room(client, "Sections", sections=["overview"])
    assert (
        client.put(
            f"{PREFIX}/rooms/{target}/sections", json=body_payload, params={"actor": "dana"}
        ).status_code
        == 422
    )


def test_sections_of_a_workspace_that_does_not_exist_is_a_422(client):
    assert client.get(f"{PREFIX}/rooms/room_missing/sections").status_code == 422


# =========================================================================== #
# 3. The contract
# =========================================================================== #


def test_every_audit_source_this_feature_records_names_a_route_it_mounts(client):
    """Hard rule 4 of the build brief, as a test.

    Every write route is exercised, then every ``source`` this feature wrote is
    matched against the route templates the host actually mounted. A hard-coded
    string that drifted from the prefix fails here rather than sitting in the
    audit log naming a path nobody can call - the defect this repository has
    shipped once already.
    """
    room_id = room(client, "Audited", sections=["overview", "pricing"], type="Sales")
    sales = make_template(client, "Sales", "Sales")

    private = add_view(client, actor="dana", name="Audited", visibility="private")
    public = add_view(client, actor="dana", name="Shared", visibility="public")
    add_view(client, actor="dana", name="Third")
    clone = client.post(
        f"{PREFIX}/views/{public['id']}/clone", json={}, params={"actor": "sam"}
    ).json()
    client.patch(
        f"{PREFIX}/views/{private['id']}", json={"name": "Renamed"}, params={"actor": "dana"}
    )
    client.request("DELETE", f"{PREFIX}/views/{clone['id']}", params={"actor": "sam"})
    client.patch(f"{PREFIX}/rooms/{room_id}/type", json={"type": "Sales"}, params={"actor": "dana"})
    client.patch(f"{PREFIX}/templates/{sales}", json={"type": "Sales"}, params={"actor": "dana"})
    client.put(
        f"{PREFIX}/rooms/{room_id}/sections",
        json={
            "sections": [
                {"section": "pricing", "visible_when": group(condition("dock.stage", "is", "x"))}
            ]
        },
        params={"actor": "dana"},
    )
    client.put(f"{PREFIX}/open-views", json={"actor": "dana", "view_ids": [public["id"]]})

    sources = {entry["source"] for entry in audit(client) if PREFIX in (entry["source"] or "")}
    assert sources, "the exercise above must have produced at least one audit row"

    # The (method, path-template) pairs the host really mounted. Templates, so a
    # concrete id in a recorded path still matches the route that served it.
    mounted = {
        (method, route["path"])
        for feature in client.get("/api/features").json()["features"]
        for route in feature["routes"]
        for method in route["methods"]
    }

    for source in sources:
        method, _, path = source.partition(" ")
        assert any(
            method == mounted_method and re.fullmatch(_path_pattern(template), path)
            for mounted_method, template in mounted
        ), f"{source!r} does not name a mounted route"

    # And specifically, the ones this build's routes produced.
    assert f"POST {PREFIX}/views" in sources
    assert f"POST {PREFIX}/views/{public['id']}/clone" in sources
    assert f"PATCH {PREFIX}/views/{private['id']}" in sources
    assert f"DELETE {PREFIX}/views/{clone['id']}" in sources
    assert f"PATCH {PREFIX}/rooms/{room_id}/type" in sources
    assert f"PATCH {PREFIX}/templates/{sales}" in sources
    assert f"PUT {PREFIX}/rooms/{room_id}/sections" in sources
    assert f"PUT {PREFIX}/open-views" in sources


def _path_pattern(template: str):
    """Turn ``/rooms/{room_id}/row`` into a regex matching one concrete segment."""
    escaped = re.escape(template)
    return re.sub(r"\\\{[a-z_]+\\\}", r"[^/]+", escaped)


def test_every_source_this_feature_records_names_an_http_verb_and_a_path(client):
    """A source that is not a route is a label, and a label is not an audit trail."""
    add_view(client, name="Audited")
    for entry in audit(client):
        source = entry["source"] or ""
        if PREFIX not in source:
            continue
        verb, _, path = source.partition(" ")
        assert verb in ("GET", "POST", "PATCH", "PUT", "DELETE"), source
        assert path.startswith("/"), source


def test_no_domain_write_takes_a_default_source(client):
    """``source`` is a required keyword on every domain write, so this cannot
    silently regress to a hard-coded string."""
    text = _domain_source("views")
    for name in (
        "create_view",
        "update_view",
        "delete_view",
        "clone_view",
        "set_room_type",
        "set_template_type",
        "set_section_rules",
        "remember_open_views",
    ):
        signature = re.search(rf"def {name}\((.*?)\) ->", text, re.S)
        assert signature, name
        assert re.search(r"\bsource: str\b", signature.group(1)), name
        assert "source=" not in signature.group(1), name


def _domain_source(module_name: str) -> str:
    return (
        Path(load_feature("wf022_triage_the_pipeline_with_saved_workspa").__file__)
        .parent.parent.joinpath("triage", f"{module_name}.py")
        .read_text(encoding="utf-8")
    )


def test_the_feature_module_does_not_import_the_shared_app(client):
    """A guard exists in test_features.py; this states the reason locally."""
    source = Path(load_feature("wf022_triage_the_pipeline_with_saved_workspa").__file__).read_text(
        encoding="utf-8"
    )
    assert "dsr.api" not in source
    assert "from dsr.deps import" in source


def test_nothing_in_this_feature_opens_the_database_itself(client):
    """The audit row is written in the same transaction as the change; that only
    holds if every write goes through the audited wrapper."""
    feature = Path(load_feature("wf022_triage_the_pipeline_with_saved_workspa").__file__).read_text(
        encoding="utf-8"
    )
    assert "sqlite3" not in feature
    for name in (
        "filters",
        "vocabulary",
        "rows",
        "inferences",
        "fields",
        "errors",
        "views",
        "__init__",
    ):
        text = _domain_source(name)
        assert "sqlite3" not in text, name
        assert "dsr.api" not in text, name


def test_the_feature_imports_nothing_from_a_shared_module_it_should_not_need(client):
    """Dependencies come from ``dsr.deps`` and ``dsr.store``, never from the app
    and never from another feature."""
    feature = Path(load_feature("wf022_triage_the_pipeline_with_saved_workspa").__file__).read_text(
        encoding="utf-8"
    )
    imports = set(re.findall(r"^from ([\w.]+) import|^import ([\w.]+)", feature, re.M))
    flat = {name for pair in imports for name in pair if name}
    assert "dsr.api" not in flat
    assert not any(name.startswith("dsr.features.") for name in flat)
    assert "dsr.deps" in flat and "dsr.store" in flat


def test_the_frontend_descriptor_id_matches_the_backend_feature_id():
    """The two halves of a feature are findable by one name, so they must agree."""
    module = load_feature("wf022_triage_the_pipeline_with_saved_workspa")
    descriptor = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / FEATURE_ID
        / "index.jsx"
    )
    text = descriptor.read_text(encoding="utf-8")
    assert module.FEATURE["id"] in text
    assert f"id: {module.FEATURE['id']!r}" in text
    assert "export default {" in text
    assert "Component:" in text


def test_the_frontend_calls_its_own_api_through_api_request():
    """Not by adding a method to the shared ``api`` object, which is not ours."""
    folder = Path(__file__).resolve().parents[2] / "frontend" / "src" / "features" / FEATURE_ID
    for module in folder.glob("*.js"):
        text = module.read_text(encoding="utf-8")
        if "apiRequest" not in text:
            continue
        assert "from '@/lib/api'" in text
        assert not re.search(r"\bapi\.\w+\(", text), f"{module.name} calls a shared api method"
    assert any("apiRequest" in path.read_text(encoding="utf-8") for path in folder.glob("*.js"))


def test_the_frontend_uses_the_shared_primitives_and_adds_no_emoji_icons():
    folder = Path(__file__).resolve().parents[2] / "frontend" / "src" / "features" / FEATURE_ID
    combined = "\n".join(path.read_text(encoding="utf-8") for path in folder.glob("*.jsx"))
    assert "from '@/components/ui'" in combined
    assert not re.search(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", combined), "no emoji as icons"
    assert "min-h-11" in combined or "44" in combined


def test_every_glyph_name_the_page_uses_actually_exists():
    """``Glyph`` falls back to a default, so a typo would render the wrong picture
    silently. Found on this feature's first pass: ``check`` was requested and did
    not exist. This makes the fallback unreachable from this folder."""
    folder = Path(__file__).resolve().parents[2] / "frontend" / "src" / "features" / FEATURE_ID
    icons = (folder / "icons.jsx").read_text(encoding="utf-8")
    declared = set(re.findall(r"^\s{2}([a-z_]+): [A-Z_]+_ICON,", icons, re.M))

    used: set[str] = set()
    for path in folder.glob("*.jsx"):
        if path.name == "icons.jsx":
            continue
        text = path.read_text(encoding="utf-8")
        used.update(re.findall(r'<Glyph\s+name="([a-z_]+)"', text))
        used.update(re.findall(r"(?:good|warn|info):\s*'([a-z_]+)'", text))
        used.update(re.findall(r"glyph = \{[^}]*?'([a-z_]+)'", text))

    assert used, "the page draws at least one glyph"
    assert used <= declared, f"undeclared glyph names: {sorted(used - declared)}"


# -- the seed ---------------------------------------------------------------------- #


def _seed(client):
    """Run this feature's seed hook the way ``backend/seed.py`` does.

    A little buyer activity is written first, because the real seeder does that
    and the feature's own extra room is the *only* one meant to be silent. Without
    it every room would be silent and the assertion that exactly one is would
    prove nothing.
    """
    import os

    from dsr.db.audited import AuditedDatabase

    module = load_feature("wf022_triage_the_pipeline_with_saved_workspa")
    db = AuditedDatabase(
        os.environ["DSR_DB_PATH"], mirror_dir=os.environ["DSR_AUDIT_DIR"], actor="seed"
    )
    try:
        room_ids = []
        for index in range(4):
            room_id = db.create(
                "room",
                {"name": f"Demo {index}", "account": f"Account {index}"},
                actor="dana",
                source="seed",
            )["id"]
            room_ids.append((room_id, f"Account {index}"))
            for step in range(index + 1):
                db.create(
                    "activity",
                    {
                        "action": "viewed" if step % 2 == 0 else "downloaded",
                        "person": "a.buyer@example",
                        "occurred_at": (NOW - timedelta(days=step + 1)).isoformat(),
                    },
                    room_id=room_id,
                    actor="system",
                    source="seed",
                )
        summary = module.seed(db, {"room_ids": room_ids, "now": NOW, "rng": None})
    finally:
        db.close()
    return summary


def test_the_seed_hook_is_exported_and_describes_what_it_added(client):
    summary = _seed(client)
    assert "templates" in summary and "views" in summary
    assert "section rule" in summary
    assert "3 CRM links" in summary
    assert "1 completed, 1 sent, 1 voided" in summary


def test_the_seed_produces_a_public_view_a_private_view_and_a_clone(client):
    """All three visibilities the research describes, on a fresh database."""
    _seed(client)
    dana = {
        view["name"]: view
        for view in client.get(f"{PREFIX}/views", params={"actor": "dana"}).json()["views"]
    }
    assert dana["Team pipeline"]["visibility"] == "public"
    assert dana["My pipeline"]["visibility"] == "private"
    assert dana["My pipeline"]["owner"] == "dana"
    assert "Signed only" in dana

    sam = {
        view["name"]: view
        for view in client.get(f"{PREFIX}/views", params={"actor": "sam"}).json()["views"]
    }
    # The public team view is in sam's list too - that is what public means.
    assert sam["Team pipeline"]["visibility"] == "public"
    assert sam["Renewals I am chasing"]["visibility"] == "private"
    assert sam["Renewals I am chasing"]["cloned_from"] == dana["Team pipeline"]["id"]
    assert sam["Renewals I am chasing"]["owner"] == "sam"
    # And dana's own private views are not in it.
    assert "My pipeline" not in sam and "Signed only" not in sam


def test_the_seed_cloned_view_was_actually_rearranged(client):
    _seed(client)
    sam = client.get(f"{PREFIX}/views", params={"actor": "sam"}).json()["views"]
    copy = next(view for view in sam if view["cloned_from"])
    assert copy["columns"][0] == "dock.name"
    assert copy["sort"] == {"field": "hubspot.deal_amount", "direction": "desc"}


def test_the_seed_keeps_both_arms_of_the_disjunction_visible_in_the_demo(client):
    """A demo that only shows the happy arm teaches a reviewer nothing.

    The four demo rooms plus the one this seed adds: exactly one of the five is
    on a template that categorises nothing *and* has no CRM link, and that is the
    one Active Pipeline must exclude. If this count is ever 5, the disjunction has
    collapsed into a conjunction.
    """
    _seed(client)
    views = {
        entry["name"]: entry
        for entry in client.get(f"{PREFIX}/views", params={"actor": "dana"}).json()["views"]
    }
    body = rows_of(client, views["Team pipeline"]["id"])
    assert body["total"] == 4

    templates = client.get("/api/records/workspace_template", params={"limit": 1000}).json()[
        "records"
    ]
    blank = next(record for record in templates if record["data"].get("type") is None)
    excluded = [
        record
        for record in client.get("/api/records/room", params={"limit": 1000}).json()["records"]
        if (record["data"] or {}).get("template_id") == blank["id"]
    ]
    assert len(excluded) == 1
    assert excluded[0]["id"] not in {row["id"] for row in body["rows"]}

    # And the arm-one-only workspace is in the view with no CRM link at all. The
    # link's own `room_id` is the join key, not the record's own id.
    linked = {
        record["room_id"]
        for record in client.get("/api/records/workspace_crm", params={"limit": 1000}).json()[
            "records"
        ]
    }
    arm_one = [row for row in body["rows"] if row["id"] not in linked]
    assert [row["dock.type"] for row in arm_one] == ["Sales"]


def test_the_seed_produces_a_workspace_with_no_buyer_activity(client):
    """ "Never viewed" is a state a triage table has to be able to show, and the
    demo needs one so the null column is visible without any interaction."""
    _seed(client)
    view = add_view(
        client, name="Silent check", columns=["dock.name", "engagement.last_client_view"]
    )
    body = rows_of(client, view["id"])
    silent = [row for row in body["rows"] if row["engagement.last_client_view"] is None]
    assert len(silent) == 1, "the seed adds exactly one workspace nobody has looked at"
    assert silent[0]["meta"]["engagement"] == {"views": 0, "actions": 0, "last_client_view": None}
    assert silent[0]["meta"]["crm_provider"] == "hubspot"


def test_the_seed_produces_a_voided_order_form_a_completed_one_and_none_at_all(client):
    _seed(client)
    statuses = sorted(
        record["data"]["status"]
        for record in client.get("/api/records/order_form", params={"limit": 1000}).json()[
            "records"
        ]
    )
    assert statuses == ["completed", "sent", "voided"]
    view = add_view(
        client, name="Desk check", base="deal-desk", columns=["dock.name", "order_form.status"]
    )
    body = rows_of(client, view["id"])
    assert {row["order_form.status"] for row in body["rows"]} == {"completed", "sent", "voided"}
    assert body["total"] == 3, "two demo rooms and the extra one have no order form at all"


def test_the_seed_produces_a_section_hidden_by_a_rule_that_does_not_match(client):
    _seed(client)
    hidden = []
    for record in client.get("/api/records/room", params={"limit": 1000}).json()["records"]:
        body = client.get(f"{PREFIX}/rooms/{record['id']}/sections").json()
        hidden.extend(body["hidden"])
    assert hidden == ["order-form"]


def test_the_seed_produces_both_a_template_inherited_type_and_a_typed_one(client):
    """All three readings of a type - inherited, typed by hand, and none - are on
    screen, because they are the three a rep has to tell apart."""
    _seed(client)
    view = add_view(client, name="Types", columns=["dock.name", "dock.type"])
    sources = [row["meta"]["type_source"] for row in rows_of(client, view["id"])["rows"]]
    assert sources.count("template") == 2, "inherited from a typed template"
    assert sources.count("workspace") == 2, "typed by hand, overriding the template"
    assert sources.count(None) == 1, "on a template that categorises nothing"


def test_the_seed_remembered_an_open_set_for_two_accounts(client):
    _seed(client)
    assert client.get(f"{PREFIX}/open-views", params={"actor": "dana"}).json()["remembered"] is True
    assert client.get(f"{PREFIX}/open-views", params={"actor": "sam"}).json()["remembered"] is True
    dashboard = client.get(f"{PREFIX}/dashboard", params={"actor": "dana"}).json()
    assert dashboard["open_views"] and dashboard["active_view_id"] in dashboard["open_views"]


def test_the_seed_declines_gracefully_with_no_rooms_to_attach_to(client):
    """The seeder skips a feature loudly rather than aborting; this is the case."""
    import os

    from dsr.db.audited import AuditedDatabase
    from dsr.store import RecordStore

    module = load_feature("wf022_triage_the_pipeline_with_saved_workspa")
    db = AuditedDatabase(
        os.environ["DSR_DB_PATH"], mirror_dir=os.environ["DSR_AUDIT_DIR"], actor="seed"
    )
    try:
        summary = module.seed(db, {"room_ids": [], "now": NOW, "rng": None})
        assert "no rooms to attach to" in summary
        assert RecordStore(db).list("saved_view") == []
    finally:
        db.close()


def test_the_seed_writes_only_through_the_audited_store(client):
    _seed(client)
    seeded = [entry for entry in audit(client) if entry["source"] == "seed"]
    assert seeded, "the seed writes through the audited database, not around it"
    assert {entry["action"] for entry in seeded} == {"insert", "update"}
    assert all(entry["actor"] for entry in seeded)
    assert all(entry["collection"] for entry in seeded)
