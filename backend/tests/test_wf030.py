"""Tests for WF-030: fire CRM workflows off DSR activity.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-030.md`` (section 15 of
``docs/research/raw/analytics-intent.md``). They are, in order:

* "You can create HubSpot workflows based on Dock activity filters", with the
  research's own three examples - trigger emails and/or slack notifications, change
  stages on onboarding or MAP tasks, update fields;
* "Dock only supports Contact based workflows since the activities are tied to the
  contact record";
* "When filter criteria is met", reached by selecting the integration;
* "When you select Dock, you'll see **five** different options for your filter ...
  Analytics events (Views, Clicks, Downloads, or Interactions), or MAP activity";
* the per-family refinements: "**Downloads:** filter by date and/or file name.
  **Views:** filter by date", "Clicks/Interactions by date or link URL", and MAP
  activity "Filter by activity text - e.g. 'completed task \"Sign up for free
  account\"' or 'completed task \"Intro call\"'";
* the four actions: "send emails, slack notifications, update fields, change stages
  and more!";
* the forward-only lifecycle-stage constraint, from the lead-scoring article this
  workflow also cites;
* "Publish the workflow; DSR activity then drives it with no further setup";
* "The workflow is the automation; it fires continuously on matching activity.
  **Nothing happens on the seller's screen.**"

The last one is why several tests below assert that something is *absent* rather
than present. The research is more explicit about the silence than about anything
else in the workflow, and a feature that implied an enrollment gave a seller a
task would be the most misleading thing this product could ship.

Every part of the feature is reachable through its own router, so the HTTP tests
drive the mounted routes rather than calling handlers, and the audit-source tests
check every write against the route table the host actually reported.
"""

from __future__ import annotations

import random
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from dsr.api import app
from dsr.crm_workflows import (
    ACTION_KINDS,
    ACTIVITY,
    DELIVERY_PATHS,
    ENROLLMENT_TYPES,
    ENROLLMENTS,
    FILTER_FAMILIES,
    INTEGRATIONS,
    LIFECYCLE_STAGES,
    REFINEMENTS,
    TRIGGER_MODES,
    WORKFLOWS,
    AlreadyPublished,
    AlreadyWithdrawn,
    IntegrationDisabled,
    IntegrationInUse,
    MalformedActivity,
    NoActions,
    NotContactBased,
    PublishedWorkflowIsImmutable,
    UnknownFilterFamily,
    UnknownIntegration,
    UnknownTriggerMode,
    UnsupportedRefinement,
    WorkflowEngine,
    WorkflowError,
    evaluate,
    evaluate_contact,
    lint_criteria,
    lint_workflow,
    matches,
    normalise_activity,
    normalise_workflow,
    parse_criteria,
    require_family,
    require_refinement,
)
from dsr.crm_workflows.actions import resolve_action, resolve_actions, summarise
from dsr.crm_workflows.activity import (
    activity_text_task_name,
    criterion_activity_name,
    occurred_window,
    parse_timestamp,
)
from dsr.crm_workflows.criteria import describe_vocabulary as matcher_vocabulary
from dsr.crm_workflows.definition import apply_patch, normalise_actions
from dsr.crm_workflows.inferences import INFERENCES, by_id
from dsr.crm_workflows.vocabulary import (
    ACTION_FAMILIES,
    ACTIONABILITY_NOTE,
    ALL_REFINEMENTS,
    DEFAULT_INTEGRATION,
    FIELD_ALIASES,
    describe as describe_vocabulary,
    lifecycle_rank,
)
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore
from fastapi.testclient import TestClient

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point of
#: a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-030"

#: The source each route passes for a write. The pure-domain tests use these same
#: strings, so a test asserting on an audit row is asserting on the real thing
#: rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/activity"
EVALUATE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/evaluate"
CREATE_SOURCE = f"POST {PREFIX}/workflows"
PUBLISH_SOURCE = f"POST {PREFIX}/workflows/{{workflow_id}}/publish"
UNPUBLISH_SOURCE = f"POST {PREFIX}/workflows/{{workflow_id}}/unpublish"
AMEND_SOURCE = f"PATCH {PREFIX}/workflows/{{workflow_id}}"
WITHDRAW_SOURCE = f"DELETE {PREFIX}/workflows/{{workflow_id}}"
INTEGRATION_SOURCE = f"POST {PREFIX}/integrations"

MODULE = "wf030_fire_crm_workflows_off_dsr_activity"
FEATURE_ID = "wf-030-fire-crm-workflows-off-dsr-activity"

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)

BUYER = "a.buyer@northwind.example"

ROOM = {
    "name": "Northwind Traders — Enterprise Evaluation",
    "account": "Northwind Traders",
    "owner": "dana",
    "stage": "evaluation",
}


def ago(minutes: float) -> str:
    return (NOW - timedelta(minutes=minutes)).isoformat()


# --------------------------------------------------------------------------- #
# Payload builders - functions, not constants, so a test that changes one field
# changes one field and the constants cannot drift from what the code accepts.
# --------------------------------------------------------------------------- #


def event(**overrides: Any) -> dict[str, Any]:
    """A well-formed DSR activity event, in this product's own vocabulary.

    ``file_name`` is omitted by default rather than derived from ``target``, so a
    test that cares about a filter refusing on a file name says so with one
    argument rather than by remembering a default.
    """
    payload: dict[str, Any] = {
        "person": BUYER,
        "account": "Northwind Traders",
        "action": "downloaded",
        "target": "Pricing One-Pager",
        "occurred_at": ago(10),
    }
    payload.update(overrides)
    return payload


def download(file_name: str = "Pricing One-Pager", **overrides: Any) -> dict[str, Any]:
    """A download of a named file - the shape the downloads criteria matches."""
    return event(file_name=file_name, **overrides)


def click(url: str | None = None, **overrides: Any) -> dict[str, Any]:
    """A click, with or without the URL it clicked."""
    return event(action="opened_link", url=url, **overrides)


def criteria(family: str = "downloads", **refinements: Any) -> dict[str, Any]:
    return {"family": family, "refinements": refinements}


def workflow_payload(**overrides: Any) -> dict[str, Any]:
    """A well-formed workflow definition, as the researched flow describes one."""
    payload: dict[str, Any] = {
        "name": "Pricing pack downloaded: follow up",
        "description": "A download filter and an email.",
        "enrollment_type": "contact",
        "trigger": {
            "mode": "filter_criteria_met",
            "integration": DEFAULT_INTEGRATION,
            "criteria": criteria("downloads", file_name="Pricing One-Pager"),
        },
        "actions": [
            {"kind": "send_email", "template": "pricing-follow-up"},
            {"kind": "update_field", "field": "pricing_pack_seen", "value": True},
        ],
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(":memory:", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def engine(store):
    # The clock is pinned to the same NOW the test payloads are built from.
    # Left on the real clock, `lookback_days` is measured from whenever the suite
    # happens to run while the events are pinned to 27 September 2026, so the
    # window silently widens out from under the data: the lookback test passed on
    # the 27th and 28th and failed from the 29th onward, permanently, because the
    # two clocks only ever drift further apart. The engine takes `now` for
    # exactly this.
    return WorkflowEngine(store, now=lambda: NOW.isoformat())


@pytest.fixture()
def room(store):
    return store.create("room", ROOM, actor="dana")


@pytest.fixture()
def integration(engine):
    return engine.register_integration(
        {"name": DEFAULT_INTEGRATION, "enabled": True, "connections": []},
        actor="dana",
        source=INTEGRATION_SOURCE,
    )


@pytest.fixture()
def connected(engine, integration, room):
    """The registered, enabled integration with this room linked to a deal.

    Depends on ``integration`` rather than registering its own, so a test that asks
    for both gets one registration rather than a duplicate-name refusal - which is
    the right behaviour, and unhelpful as a fixture-ordering accident.
    """
    return engine.amend_integration(
        integration["id"],
        {"connections": {room["id"]: {"deal_id": "006NW"}}},
        actor="dana",
        source=INTEGRATION_SOURCE,
    )


@pytest.fixture()
def draft(engine, connected):
    return engine.create(workflow_payload(), actor="dana", source=CREATE_SOURCE)


@pytest.fixture()
def published(engine, draft):
    return engine.publish(draft["id"], actor="dana", source=PUBLISH_SOURCE)


@pytest.fixture()
def fired(engine, published, room):
    engine.record_activity(room["id"], download(), actor="dana", source=SOURCE)
    return engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)


@pytest.fixture()
def http(monkeypatch):
    """A client over a temporary database.

    The database path is resolved at lifespan time, so the variable is set before
    the context manager is entered - the same way ``test_features.py`` does it.
    """
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf030-http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as client:
        yield client
    tmp.cleanup()


@pytest.fixture()
def http_room(http):
    return http.post("/api/records/room", json=ROOM).json()


@pytest.fixture()
def http_integration(http, http_room):
    return http.post(
        f"{PREFIX}/integrations",
        json={
            "name": DEFAULT_INTEGRATION,
            "enabled": True,
            "connections": {http_room["id"]: {"deal_id": "006NW"}},
        },
    ).json()


@pytest.fixture()
def http_workflow(http, http_integration):
    return http.post(f"{PREFIX}/workflows", json=workflow_payload()).json()


@pytest.fixture()
def http_published(http, http_workflow):
    return http.post(f"{PREFIX}/workflows/{http_workflow['id']}/publish").json()


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The route resolves even though no shared file names this feature."""
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)
    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-030"
    assert entry["exception_handlers"] == ["WorkflowError"]
    assert len(entry["routes"]) == 18


def test_no_feature_failed_to_load(http):
    """A refused route collision or a broken import would show up here."""
    body = http.get("/api/features").json()
    assert body["failed_count"] == 0, body["failed"]


def test_the_prefix_is_ours_alone(http):
    """No core route and no other feature answers anything under it."""
    body = http.get("/api/features").json()
    mine = {feature["id"] for feature in body["features"] if feature["prefix"] == PREFIX}
    assert mine == {FEATURE_ID}
    for feature in body["features"]:
        if feature["id"] == FEATURE_ID:
            continue
        assert not any(route["path"].startswith(PREFIX) for route in feature["routes"]), (
            f"{feature['id']} also serves under {PREFIX}"
        )


def test_no_other_feature_claims_this_error_type(http):
    """Two features may not map one error type; the host refuses the second.

    Checked for *our* type rather than across the whole product: several features
    on main register distinct classes that happen to share a class name, and a
    name is not a type.
    """
    features = http.get("/api/features").json()["features"]
    claiming = [
        feature["id"] for feature in features if "WorkflowError" in feature["exception_handlers"]
    ]
    assert claiming == [FEATURE_ID]


def test_feature_module_does_not_import_the_shared_app():
    """A guard exists in test_features.py; this states the reason locally."""
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source
    assert "from dsr.deps import" in source


def test_frontend_descriptor_id_matches_the_backend_feature_id():
    """The two halves of a feature are findable by one name, so they must agree."""
    descriptor = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / FEATURE_ID
        / "index.jsx"
    )
    text = descriptor.read_text(encoding="utf-8")
    module = load_feature(MODULE)
    assert module.FEATURE["id"] in text
    assert f"id: {module.FEATURE['id']!r}" in text


def test_the_feature_exports_what_the_host_looks_for():
    module = load_feature(MODULE)
    assert set(module.EXCEPTION_HANDLERS) == {WorkflowError}
    assert issubclass(UnknownFilterFamily, WorkflowError)
    assert issubclass(UnsupportedRefinement, WorkflowError)
    assert issubclass(NotContactBased, WorkflowError)
    assert issubclass(UnknownTriggerMode, WorkflowError)
    assert issubclass(NoActions, WorkflowError)
    assert issubclass(MalformedActivity, WorkflowError)
    assert issubclass(UnknownIntegration, WorkflowError)
    assert issubclass(IntegrationDisabled, WorkflowError)
    assert issubclass(IntegrationInUse, WorkflowError)
    assert issubclass(PublishedWorkflowIsImmutable, WorkflowError)
    assert issubclass(AlreadyPublished, WorkflowError)
    assert issubclass(AlreadyWithdrawn, WorkflowError)


def test_every_error_status_is_carried_on_the_exception_not_decided_in_the_handler():
    """400 for malformed, 409 for a conflict with state that already exists."""
    for error in (UnknownFilterFamily, UnsupportedRefinement, NotContactBased, NoActions):
        assert error.status == 400, error.__name__
    for error in (
        UnknownIntegration,
        IntegrationDisabled,
        IntegrationInUse,
        PublishedWorkflowIsImmutable,
        AlreadyPublished,
        AlreadyWithdrawn,
    ):
        assert error.status == 409, error.__name__


def test_seed_is_exported_by_the_feature_module():
    """Demo data belongs to the feature, which is what `backend/seed.py` looks for."""
    assert callable(load_feature(MODULE).seed)


# --------------------------------------------------------------------------- #
# Step 5: the five filter families
# --------------------------------------------------------------------------- #


def test_there_are_exactly_five_filter_families():
    """you'll see **five different options** for your filter"."""
    assert len(FILTER_FAMILIES) == 5
    assert FILTER_FAMILIES == ("views", "clicks", "downloads", "interactions", "map_activity")


def test_the_five_are_four_analytics_events_plus_map_activity():
    """Analytics events (Views, Clicks, Downloads, or Interactions), or MAP activity"."""
    served = describe_vocabulary()["filter_families"]
    analytics = [row for row in served if row["group"] == "analytics_events"]
    assert [row["name"] for row in analytics] == ["views", "clicks", "downloads", "interactions"]
    assert [row["name"] for row in served if row["group"] == "map_activity"] == ["map_activity"]


def test_a_reader_who_counted_only_the_analytics_events_would_publish_four():
    """The count is load-bearing, and the test says what a partial reading costs."""
    assert len([f for f in FILTER_FAMILIES if f != "map_activity"]) == 4
    assert "map_activity" in FILTER_FAMILIES


@pytest.mark.parametrize("name", list(FILTER_FAMILIES))
def test_every_published_family_is_accepted(name):
    assert require_family(name) == name
    assert require_family(name.upper()) == name
    assert require_family(f"  {name}  ") == name


@pytest.mark.parametrize("name", ["visit", "pageviews", "maptasks", "", None, 3, ["views"]])
def test_a_family_outside_the_five_is_refused_naming_them(name):
    with pytest.raises(UnknownFilterFamily) as caught:
        require_family(name)
    assert "views, clicks, downloads, interactions, map_activity" in str(caught.value)


def test_a_filter_refining_on_an_unpublished_family_is_refused_over_http(http):
    response = http.post(
        f"{PREFIX}/workflows", json=workflow_payload(trigger={"criteria": criteria("pageviews")})
    )
    assert response.status_code == 400
    assert response.json()["error"] == "unknown_filter_family"


# --------------------------------------------------------------------------- #
# Step 6: the per-family refinement matrix
# --------------------------------------------------------------------------- #


def test_the_refinement_matrix_is_exactly_what_the_source_publishes():
    """**Views:** filter by date", "**Downloads:** filter by date and/or file name",
    "Clicks/Interactions by date or link URL", MAP activity by activity text."""
    assert REFINEMENTS == {
        # "**Views:** filter by date." - date only.
        "views": ("occurred",),
        "clicks": ("occurred", "link_url"),
        "downloads": ("occurred", "file_name"),
        "interactions": ("occurred", "link_url"),
        # "Filter by activity text", plus the cited lead-scoring article's
        # "you can also refine by 'Occurred' ... refine by task name".
        "map_activity": ("occurred", "activity_text"),
    }


def test_views_may_not_be_refined_by_a_file_name():
    """**Views:** filter by date." - refining a view by file name is a category error."""
    with pytest.raises(UnsupportedRefinement) as caught:
        require_refinement("views", "file_name")
    assert "occurred" in str(caught.value)
    assert "file_name" in str(caught.value)


def test_downloads_may_not_be_refined_by_a_link_url():
    with pytest.raises(UnsupportedRefinement):
        require_refinement("downloads", "link_url")


@pytest.mark.parametrize(
    ("family", "refinement"),
    [
        ("views", "file_name"),
        ("views", "link_url"),
        ("views", "activity_text"),
        ("downloads", "link_url"),
        ("downloads", "activity_text"),
        ("clicks", "file_name"),
        ("clicks", "activity_text"),
        ("interactions", "file_name"),
        ("interactions", "activity_text"),
        ("map_activity", "file_name"),
        ("map_activity", "link_url"),
    ],
)
def test_every_unpublished_family_refinement_pair_is_refused(family, refinement):
    assert refinement in ALL_REFINEMENTS, "the refinement exists, just not for this family"
    with pytest.raises(UnsupportedRefinement):
        require_refinement(family, refinement)


@pytest.mark.parametrize(
    ("family", "refinement"),
    [
        ("views", "occurred"),
        ("clicks", "occurred"),
        ("clicks", "link_url"),
        ("downloads", "occurred"),
        ("downloads", "file_name"),
        ("interactions", "occurred"),
        ("interactions", "link_url"),
        ("map_activity", "occurred"),
        ("map_activity", "activity_text"),
    ],
)
def test_every_published_pair_is_accepted(family, refinement):
    assert require_refinement(family, refinement) == refinement


def test_the_served_matrix_matches_the_validated_matrix():
    """The editor a client builds and the validator cannot disagree."""
    served = describe_vocabulary()["refinement_matrix"]
    assert served == {family: list(names) for family, names in REFINEMENTS.items()}


def test_an_unpublished_refinement_is_refused_when_the_workflow_is_written_over_http(http):
    response = http.post(
        f"{PREFIX}/workflows",
        json=workflow_payload(trigger={"criteria": criteria("views", file_name="Anything")}),
    )
    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_refinement"


def test_an_occurred_refinement_needs_a_readable_value():
    with pytest.raises(WorkflowError) as caught:
        parse_criteria(criteria("views", occurred={"from": "not a date"}))
    assert "occurred" in str(caught.value)


@pytest.mark.parametrize("value", ["", "   ", 7, ["2026-09-01"]])
def test_a_text_refinement_needs_the_exact_text_to_match(value):
    with pytest.raises(WorkflowError):
        parse_criteria(criteria("downloads", file_name=value))


def test_a_refinement_given_as_null_is_ignored_rather_than_refused():
    """A form that submits every field sends nulls for the ones nobody filled in."""
    assert parse_criteria(criteria("downloads", file_name="X", occurred=None)) == {
        "family": "downloads",
        "label": "",
        "refinements": {"file_name": "X"},
    }


def test_a_criteria_may_be_written_flat():
    """A client building a form produces one spelling or the other; neither is wrong."""
    assert parse_criteria({"family": "downloads", "file_name": "X"})["refinements"] == {
        "file_name": "X"
    }


# --------------------------------------------------------------------------- #
# Step 3: contact based, and step 4: the trigger
# --------------------------------------------------------------------------- #


def test_a_workflow_must_be_contact_based():
    """Dock only supports Contact based workflows since the activities are tied to
    the contact record."""
    with pytest.raises(NotContactBased) as caught:
        normalise_workflow(workflow_payload(enrollment_type="company"))
    assert "tied to the contact record" in str(caught.value)


def test_contact_based_is_the_only_supported_type():
    assert ENROLLMENT_TYPES == ("contact",)
    assert normalise_workflow(workflow_payload())["enrollment_type"] == "contact"
    assert (
        normalise_workflow(workflow_payload(enrollment_type=None))["enrollment_type"] == "contact"
    )


def test_a_company_workflow_is_refused_over_http_with_the_quote(http):
    response = http.post(f"{PREFIX}/workflows", json=workflow_payload(enrollment_type="company"))
    assert response.status_code == 400
    assert response.json()["error"] == "workflow_must_be_contact_based"
    assert "contact record" in response.json()["detail"]


def test_the_only_documented_trigger_mode_is_filter_criteria_met():
    assert TRIGGER_MODES == ("filter_criteria_met",)
    with pytest.raises(UnknownTriggerMode) as caught:
        normalise_workflow(
            workflow_payload(trigger={"mode": "event_completed", "criteria": criteria()})
        )
    assert "When filter criteria is met" in str(caught.value)


def test_the_trigger_mode_defaults_to_the_documented_one():
    assert (
        normalise_workflow(workflow_payload(trigger={"criteria": criteria()}))["trigger"]["mode"]
        == "filter_criteria_met"
    )


def test_a_workflow_with_no_filter_is_refused():
    """The trigger *is* the filter; a workflow with none fires on all activity."""
    with pytest.raises(WorkflowError) as caught:
        normalise_workflow(workflow_payload(trigger={"criteria": []}))
    assert "When filter criteria is met" in str(caught.value)


def test_a_workflow_needs_at_least_one_action():
    """send emails, slack notifications, update fields, change stages" - the filter
    is the trigger and the actions are what it is for."""
    with pytest.raises(NoActions) as caught:
        normalise_workflow(workflow_payload(actions=[]))
    assert "enrol contacts and do nothing" in str(caught.value)


def test_a_missing_actions_key_is_the_same_refusal():
    payload = workflow_payload()
    payload.pop("actions")
    with pytest.raises(NoActions):
        normalise_workflow(payload)


def test_several_filters_on_one_workflow_are_accepted():
    """ANDed, and with no invented cap - see the `filters-are-anded-and-uncapped`."""
    definition = normalise_workflow(
        workflow_payload(
            trigger={
                "criteria": [
                    criteria("downloads", file_name="Pricing One-Pager"),
                    criteria("views", occurred={"from": "2026-09-01"}),
                ]
            }
        )
    )
    assert len(definition["trigger"]["criteria"]) == 2


def test_more_filters_than_families_are_still_accepted():
    """five different options" counts the families, not a workflow's filters."""
    definition = normalise_workflow(
        workflow_payload(
            trigger={
                "criteria": [
                    criteria("views"),
                    criteria("views"),
                    criteria("clicks"),
                    criteria("downloads"),
                    criteria("interactions"),
                    criteria("map_activity"),
                ]
            }
        )
    )
    assert len(definition["trigger"]["criteria"]) == 6


# --------------------------------------------------------------------------- #
# Step 7: the four actions, and the open action list
# --------------------------------------------------------------------------- #


def test_the_four_action_kinds_are_exactly_the_researched_ones():
    assert ACTION_KINDS == ("send_email", "slack_notification", "update_field", "change_stage")


def test_an_action_kind_outside_the_four_is_stored_not_dropped():
    """send emails, slack notifications, update fields, change stages **and more!**"""
    actions = normalise_actions([{"kind": "enroll_in_sequence", "sequence": "nurture"}])
    assert actions[0]["kind"] == "enroll_in_sequence"
    assert actions[0]["resolved"] is False
    assert "and more!" in actions[0]["reason"]


def test_an_unresolved_action_does_not_fail_the_workflow():
    definition = normalise_workflow(
        workflow_payload(actions=[{"kind": "make_a_phone_call"}, {"kind": "send_email"}])
    )
    assert [action["resolved"] for action in definition["actions"]] == [False, True]


def test_an_action_with_no_kind_is_refused():
    """No kind is not an unrecognised kind; it is a field with nothing in it."""
    with pytest.raises(WorkflowError) as caught:
        normalise_actions([{"template": "x"}])
    assert "needs a kind" in str(caught.value)


def test_an_update_field_without_a_property_is_refused():
    with pytest.raises(WorkflowError) as caught:
        normalise_actions([{"kind": "update_field", "value": 1}])
    assert "contact property" in str(caught.value)


def test_a_change_stage_without_a_stage_is_refused():
    with pytest.raises(WorkflowError) as caught:
        normalise_actions([{"kind": "change_stage", "stage_kind": "deal_stage"}])
    assert "needs a stage" in str(caught.value)


def test_the_two_stage_vocabularies_cannot_be_guessed():
    """A lifecyclestage obeys the forward-only rule and a deal_stage does not."""
    assert (
        normalise_actions([{"kind": "change_stage", "stage": "proposal"}])[0]["stage_kind"]
        == "deal_stage"
    )
    with pytest.raises(WorkflowError) as caught:
        normalise_actions([{"kind": "change_stage", "stage": "x", "stage_kind": "pipeline"}])
    assert "forward-only" in str(caught.value)


# --------------------------------------------------------------------------- #
# Step 1: the integration and the workspace-to-deal link
# --------------------------------------------------------------------------- #


def test_an_integration_is_registered_with_its_connections(engine):
    created = engine.register_integration(
        {"name": "hubspot", "connections": [{"room_id": "r1", "deal_id": "006"}]},
        actor="dana",
        source=INTEGRATION_SOURCE,
    )
    assert created["name"] == "hubspot"
    assert created["enabled"] is True
    assert created["connections"] == {"r1": {"deal_id": "006"}}


def test_connections_accept_both_shapes_a_client_builds(engine):
    """A table of rooms produces a list; a form produces an object keyed by room."""
    as_list = engine.register_integration(
        {"name": "a", "connections": [{"room_id": "r1", "deal_id": "d"}]},
        actor="dana",
        source=INTEGRATION_SOURCE,
    )["connections"]
    as_map = engine.register_integration(
        {"name": "b", "connections": {"r1": {"deal_id": "d"}}},
        actor="dana",
        source=INTEGRATION_SOURCE,
    )["connections"]
    as_shorthand = engine.register_integration(
        {"name": "c", "connections": {"r1": "d"}}, actor="dana", source=INTEGRATION_SOURCE
    )["connections"]
    assert as_list == as_map == as_shorthand == {"r1": {"deal_id": "d"}}


def test_a_second_record_claiming_one_integration_name_is_refused(engine):
    engine.register_integration({"name": "hubspot"}, actor="dana", source=INTEGRATION_SOURCE)
    with pytest.raises(WorkflowError) as caught:
        engine.register_integration({"name": "hubspot"}, actor="dana", source=INTEGRATION_SOURCE)
    assert "two possible integrations" in str(caught.value)


def test_an_integration_may_be_turned_off_while_nothing_depends_on_it(engine, integration):
    off = engine.amend_integration(
        integration["id"], {"enabled": False}, actor="dana", source=INTEGRATION_SOURCE
    )
    assert off["enabled"] is False


def test_turning_off_an_integration_a_published_workflow_depends_on_is_refused(
    engine, connected, published
):
    target = engine.integration_by_name(DEFAULT_INTEGRATION)
    with pytest.raises(IntegrationInUse) as caught:
        engine.amend_integration(
            target["id"], {"enabled": False}, actor="dana", source=INTEGRATION_SOURCE
        )
    assert published["name"] in str(caught.value)


def test_unlinking_a_connected_room_a_published_workflow_depends_on_is_refused(
    engine, connected, published, room
):
    """Step 1's check has to be able to become untrue, or it is not a check."""
    target = engine.integration_by_name(DEFAULT_INTEGRATION)
    with pytest.raises(IntegrationInUse):
        engine.amend_integration(
            target["id"],
            {"connections": {room["id"]: None}},
            actor="dana",
            source=INTEGRATION_SOURCE,
        )


def test_a_null_connection_unlinks_a_room_when_nothing_published_depends_on_it(
    engine, integration, draft, room
):
    target = engine.integration_by_name(DEFAULT_INTEGRATION)
    engine.amend_integration(
        target["id"],
        {"connections": {room["id"]: {"deal_id": "006NW"}}},
        actor="dana",
        source=INTEGRATION_SOURCE,
    )
    unlinked = engine.amend_integration(
        target["id"], {"connections": {room["id"]: None}}, actor="dana", source=INTEGRATION_SOURCE
    )
    assert unlinked["connections"] == {}


def test_a_draft_does_not_block_turning_the_integration_off(engine, integration, draft):
    target = engine.integration_by_name(DEFAULT_INTEGRATION)
    assert (
        engine.amend_integration(
            target["id"], {"enabled": False}, actor="dana", source=INTEGRATION_SOURCE
        )["enabled"]
        is False
    )


def test_publishing_against_an_unregistered_integration_is_a_missing_prerequisite(engine):
    definition = engine.create(workflow_payload(), actor="dana", source=CREATE_SOURCE)
    with pytest.raises(UnknownIntegration) as caught:
        engine.publish(definition["id"], actor="dana", source=PUBLISH_SOURCE)
    assert caught.value.status == 409
    assert "step 1" in str(caught.value).lower()


def test_publishing_against_a_disabled_integration_is_refused(engine, integration, draft):
    engine.amend_integration(
        integration["id"], {"enabled": False}, actor="dana", source=INTEGRATION_SOURCE
    )
    with pytest.raises(IntegrationDisabled) as caught:
        engine.publish(draft["id"], actor="dana", source=PUBLISH_SOURCE)
    assert "silently never fires" in str(caught.value)


def test_a_draft_may_be_written_before_the_integration_exists(engine):
    """Step 1 is a prerequisite for *firing*, not for writing a definition."""
    definition = engine.create(workflow_payload(), actor="dana", source=CREATE_SOURCE)
    assert definition["status"] == "draft"
    assert any(w["code"] == "integration_missing" for w in definition["warnings"])


# --------------------------------------------------------------------------- #
# Step 8: publish, and the immutability that follows
# --------------------------------------------------------------------------- #


def test_a_draft_does_not_fire(engine, connected, draft, room):
    engine.record_activity(room["id"], event(), actor="dana", source=SOURCE)
    result = engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)
    assert result["enrolled"] == 0
    assert [row["reason"] for row in result["skipped"]] == ["not_published"]
    assert "Only a published workflow is driven" in result["skipped"][0]["detail"]


def test_publishing_makes_it_fire(engine, connected, draft, room):
    engine.record_activity(room["id"], event(), actor="dana", source=SOURCE)
    engine.publish(draft["id"], actor="dana", source=PUBLISH_SOURCE)
    result = engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)
    assert result["enrolled"] == 1
    assert result["enrollments"][0]["outcome"] == "enrolled"


def test_publishing_twice_is_refused(engine, published):
    with pytest.raises(AlreadyPublished):
        engine.publish(published["id"], actor="dana", source=PUBLISH_SOURCE)


def test_a_published_workflow_cannot_be_amended_in_place(engine, published):
    """Publish the workflow" - the definition is what is already firing."""
    with pytest.raises(PublishedWorkflowIsImmutable) as caught:
        engine.amend(published["id"], {"name": "New name"}, actor="dana", source=AMEND_SOURCE)
    assert "may already have enrolled contacts" in str(caught.value)


def test_unpublish_then_amend_then_publish_is_the_documented_path(engine, published):
    engine.unpublish(published["id"], actor="dana", source=UNPUBLISH_SOURCE)
    assert (
        engine.amend(published["id"], {"name": "Renamed"}, actor="dana", source=AMEND_SOURCE)[
            "name"
        ]
        == "Renamed"
    )
    assert (
        engine.publish(published["id"], actor="dana", source=PUBLISH_SOURCE)["status"]
        == "published"
    )


def test_unpublishing_stops_it_firing_without_retiring_it(engine, published, room):
    engine.unpublish(published["id"], actor="dana", source=UNPUBLISH_SOURCE)
    engine.record_activity(room["id"], event(), actor="dana", source=SOURCE)
    assert engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)["enrolled"] == 0
    assert engine.workflow(published["id"])["status"] == "draft"


def test_unpublishing_a_draft_is_refused(engine, draft):
    with pytest.raises(WorkflowError) as caught:
        engine.unpublish(draft["id"], actor="dana", source=UNPUBLISH_SOURCE)
    assert "nothing to unpublish" in str(caught.value)


def test_a_lifecycle_field_cannot_be_amended_into_the_payload(engine, draft):
    """Publishing is its own route, because it is what starts the rule firing."""
    with pytest.raises(WorkflowError) as caught:
        apply_patch(draft, {"status": "published"})
    assert "their own routes" in str(caught.value)


def test_an_amendment_is_re_validated_whole_not_field_by_field(engine, draft):
    """A merge that validated only the changed key would let a nested edit through."""
    with pytest.raises(NotContactBased):
        engine.amend(
            draft["id"],
            {"trigger": {"enrollment_type": "company"}, "enrollment_type": "company"},
            actor="dana",
            source=AMEND_SOURCE,
        )


def test_withdrawing_soft_deletes_so_the_enrollments_still_name_something(engine, published, room):
    engine.record_activity(room["id"], event(), actor="dana", source=SOURCE)
    result = engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)
    enrollment_id = result["enrollments"][0]["enrollment"]["id"]

    outcome = engine.withdraw(published["id"], actor="dana", source=WITHDRAW_SOURCE)
    assert outcome["withdrawn"] is True
    assert outcome["was_published"] is True
    assert outcome["enrollments"] == 1

    # The row survives, and the enrollment still resolves to it.
    assert engine.workflow(published["id"]) is None
    row = engine.enrollment(room["id"], enrollment_id)
    assert row["workflow"] is None
    assert row["workflow_missing"] is True
    assert row["workflow_name"] == published["name"]


def test_a_withdrawn_workflow_is_listed_only_when_asked(engine, published):
    engine.withdraw(published["id"], actor="dana", source=WITHDRAW_SOURCE)
    assert [row["id"] for row in engine.workflows()] == []
    assert [row["id"] for row in engine.workflows(include_withdrawn=True)] == [published["id"]]


def test_a_withdrawn_workflow_is_not_amended_or_published_in_place(engine, published):
    engine.withdraw(published["id"], actor="dana", source=WITHDRAW_SOURCE)
    with pytest.raises(AlreadyWithdrawn):
        engine.publish(published["id"], actor="dana", source=PUBLISH_SOURCE)
    with pytest.raises(AlreadyWithdrawn):
        engine.amend(published["id"], {"name": "x"}, actor="dana", source=AMEND_SOURCE)


def test_withdrawing_twice_is_refused(engine, draft):
    engine.withdraw(draft["id"], actor="dana", source=WITHDRAW_SOURCE)
    with pytest.raises(AlreadyWithdrawn):
        engine.withdraw(draft["id"], actor="dana", source=WITHDRAW_SOURCE)


# --------------------------------------------------------------------------- #
# The source side: reading a DSR event as one of the five families
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("action", "family"),
    [
        ("viewed", "views"),
        ("downloaded", "downloads"),
        ("opened_link", "clicks"),
        ("commented", "interactions"),
        ("shared", "interactions"),
        ("completed_section", "map_activity"),
    ],
)
def test_every_documented_family_is_reachable_from_an_action_word(action, family):
    """One of the research's five, per family: view, click, download,
    interaction, and a movement related to a project plan."""
    assert ACTION_FAMILIES[action] == family
    assert normalise_activity({"person": BUYER, "action": action})["action_family"] == family


def test_an_action_word_in_no_family_is_reported_not_dropped():
    result = normalise_activity({"person": BUYER, "action": "booked_a_boat", "occurred_at": ago(5)})
    assert result["action_family"] is None
    assert [w["code"] for w in result["warnings"]] == ["action_unclassified"]
    assert "booked_a_boat" in result["warnings"][0]["message"]


def test_an_event_may_assert_its_family_directly():
    """A webhook sender knows the vendor's taxonomy better than this table does."""
    result = normalise_activity(
        {"person": BUYER, "action": "booked_a_boat", "action_family": "map_activity"}
    )
    assert result["action_family"] == "map_activity"


def test_asserting_a_family_the_table_does_not_have_is_reported_and_ignored():
    result = normalise_activity(
        {"person": BUYER, "action": "viewed", "action_family": "pageviews", "occurred_at": ago(5)}
    )
    assert result["action_family"] == "views"
    assert [w["code"] for w in result["warnings"]] == ["unknown_asserted_family"]


def test_an_assertion_that_conflicts_with_the_table_is_reported_and_the_assertion_wins():
    result = normalise_activity(
        {
            "person": BUYER,
            "action": "viewed",
            "action_family": "map_activity",
            "occurred_at": ago(5),
        }
    )
    assert result["action_family"] == "map_activity"
    assert [w["code"] for w in result["warnings"]] == ["family_assertion_conflicts"]


def test_a_non_scalar_alias_counts_as_absent_so_the_next_one_is_tried():
    """Dock sends `"user": {"id": ..., "email": ...}`, so the `user` alias resolves
    to an object. Returning it would make every text read compare a dict."""
    result = normalise_activity({"user": {"id": "u1", "email": BUYER}, "action": "viewed"})
    assert result["contact"] == BUYER


@pytest.mark.parametrize("key", FIELD_ALIASES["contact"][:5])
def test_the_buyer_may_be_named_by_any_of_the_documented_aliases(key):
    result = normalise_activity({key: BUYER, "action": "viewed"})
    assert result["contact"] == BUYER


def test_a_vendor_webhook_payload_and_a_product_activity_row_resolve_identically():
    vendor = normalise_activity(
        {
            "user": {"email": BUYER},
            "event": "downloaded",
            "file": "Pricing One-Pager",
            "at": ago(3),
        }
    )
    product = normalise_activity(download())
    assert vendor["contact"] == product["contact"] == BUYER
    assert vendor["action_family"] == product["action_family"] == "downloads"
    assert vendor["file_name"] == product["file_name"] == "Pricing One-Pager"


def test_an_event_with_no_contact_is_refused():
    """The activities are tied to the contact record, so an event with none owns
    nothing and could not enrol anybody."""
    with pytest.raises(MalformedActivity) as caught:
        normalise_activity({"action": "viewed", "target": "Deck"})
    # The message says "ties every filterable activity to the contact record"; this
    # is the substring that survives any rewording of the sentence around it.
    assert "to the contact record" in str(caught.value)


def test_an_event_with_no_timestamp_is_reported_rather_than_assumed_to_be_now():
    result = normalise_activity({"person": BUYER, "action": "viewed"})
    assert result["occurred_at"] is None
    assert "no_occurred_at" in [w["code"] for w in result["warnings"]]


@pytest.mark.parametrize(
    "value",
    [
        "2026-09-27T12:00:00Z",
        "2026-09-27T12:00:00+00:00",
        "2026-09-27T12:00:00",
        "2026-09-27",
        datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
        date(2026, 9, 27),
    ],
)
def test_a_timestamp_is_read_in_utc(value):
    """A naive local reading would move a buyer's event across a day boundary."""
    assert parse_timestamp(value) == datetime(
        2026, 9, 27, 12, 0, tzinfo=timezone.utc
    ) or parse_timestamp(value).date() == date(2026, 9, 27)


def test_a_naive_timestamp_is_read_as_utc_not_as_local_time():
    assert parse_timestamp("2026-09-27T12:00:00").tzinfo == timezone.utc


@pytest.mark.parametrize("value", [1788307200, 1788307200000])
def test_an_epoch_is_read_in_seconds_or_milliseconds(value):
    """Both spellings are the same instant; 1e12 seconds is the year 33658."""
    parsed = parse_timestamp(value)
    assert parsed == datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc)
    assert parsed == parse_timestamp(1788307200)


def test_an_unreadable_timestamp_is_none_rather_than_the_epoch():
    assert parse_timestamp("not a date") is None
    assert parse_timestamp(True) is None


# --------------------------------------------------------------------------- #
# The occurred window, and the "by date" reading
# --------------------------------------------------------------------------- #


def test_a_bare_date_is_the_whole_utc_day():
    start, end = occurred_window("2026-09-27")
    assert start == datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 27, 23, 59, 59, 999999, tzinfo=timezone.utc)


def test_a_bare_timestamp_is_a_point_not_a_day():
    start, end = occurred_window("2026-09-27T14:30:00Z")
    assert start == datetime(2026, 9, 27, 14, 30, tzinfo=timezone.utc)
    assert end is None


def test_a_date_only_upper_bound_includes_the_whole_day():
    _, end = occurred_window({"from": "2026-09-01", "to": "2026-09-01"})
    assert end == datetime(2026, 9, 1, 23, 59, 59, 999999, tzinfo=timezone.utc)


def test_a_window_may_be_open_at_either_end():
    assert occurred_window({"from": "2026-09-01"})[1] is None
    assert occurred_window({"to": "2026-09-01"})[0] is None


def test_a_backwards_window_is_not_a_window():
    assert occurred_window({"from": "2026-09-02", "to": "2026-09-01"}) is None


def test_a_single_midnight_does_not_lose_the_day_it_names():
    """filter by date" that means one instant of midnight silently discards the day."""
    _, end = occurred_window("2026-09-27")
    assert parse_timestamp("2026-09-27T23:59:00+00:00") <= end


# --------------------------------------------------------------------------- #
# The MAP activity text grammar
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ('completed task "Sign up for free account"', "Sign up for free account"),
        ("completed task 'Sign up for free account'", "Sign up for free account"),
        ('completed task "Intro call"', "Intro call"),
        ("completed task 'Intro call'", "Intro call"),
        ('completed section "Security review"', "Security review"),
        # The research's two examples, verbatim.
        ('completed task "Sign up for free account"', "Sign up for free account"),
        ('completed task "Intro call"', "Intro call"),
    ],
)
def test_the_documented_activity_text_yields_its_task_name(text, name):
    assert activity_text_task_name(text) == name


def test_an_unfamiliar_activity_text_yields_no_task_name_so_the_raw_text_is_compared():
    assert activity_text_task_name("moved to legal review") is None
    assert activity_text_task_name("") is None
    assert activity_text_task_name(None) is None


def test_a_criterion_naming_the_documented_form_or_the_bare_name_is_the_same_one():
    assert criterion_activity_name('completed task "Intro call"') == "Intro call"
    assert criterion_activity_name("Intro call") == "Intro call"


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


def test_a_matching_event_is_a_match_with_its_reasoning():
    result = matches(
        criteria("downloads", file_name="Pricing One-Pager"), normalise_activity(event())
    )
    assert result["matched"] is True
    assert result["reason"] == "matched"
    assert [row["refinement"] for row in result["checked"]] == ["file_name"]
    assert result["failed"] == [] and result["unverifiable"] == []


def test_the_wrong_family_is_a_family_mismatch_not_a_refinement_failure():
    result = matches(criteria("views"), normalise_activity(event()))
    assert result["matched"] is False
    assert result["reason"] == "family_mismatch"
    assert result["checked"] == []


def test_an_event_in_no_family_matches_nothing_at_all():
    result = matches(criteria("views"), normalise_activity({"person": BUYER, "action": "napped"}))
    assert result["matched"] is False
    assert result["reason"] == "action_unclassified"


def test_a_different_file_name_is_a_miss_naming_both_sides():
    result = matches(criteria("downloads", file_name="Contract Draft"), normalise_activity(event()))
    assert result["matched"] is False
    assert result["reason"] == "file_name_mismatch"
    assert "Pricing One-Pager" in result["detail"]
    assert "Contract Draft" in result["detail"]


def test_a_file_name_matches_case_insensitively_and_whitespace_insensitively():
    """The research's own two task names differ in capitalisation."""
    result = matches(
        criteria("downloads", file_name="pricing  one-pager"), normalise_activity(event())
    )
    assert result["matched"] is True


def test_a_link_url_is_compared_exactly():
    """ "filter by date or link URL" publishes no partial-match option."""
    assert (
        matches(
            criteria("clicks", link_url="https://n.example/p"),
            normalise_activity(click("https://n.example/p")),
        )["matched"]
        is True
    )
    assert (
        matches(
            criteria("clicks", link_url="https://n.example/p"),
            normalise_activity(click("https://n.example/pricing")),
        )["reason"]
        == "link_url_mismatch"
    )


def test_a_link_url_is_case_sensitive_because_a_path_is_an_identifier():
    result = matches(
        criteria("clicks", link_url="https://n.example/Pricing"),
        normalise_activity(click("https://n.example/pricing")),
    )
    assert result["matched"] is False


def test_an_event_with_no_link_url_cannot_be_checked_rather_than_missing():
    result = matches(
        criteria("clicks", link_url="https://n.example/p"), normalise_activity(click())
    )
    assert result["matched"] is False
    assert result["reason"] == "refinement_unverifiable"
    assert "cannot be checked" in result["detail"]


def test_an_event_with_no_file_name_cannot_be_checked_rather_than_missing():
    result = matches(
        criteria("downloads", file_name="Anything"),
        normalise_activity({"person": BUYER, "action": "downloaded"}),
    )
    assert result["reason"] == "refinement_unverifiable"


def test_an_unverifiable_refinement_is_reported_apart_from_a_plain_miss():
    """The distinction is the whole point: a "no" and an unanswered question differ."""
    result = matches(
        criteria("downloads", file_name="Anything"),
        normalise_activity({"person": BUYER, "action": "downloaded"}),
    )
    assert result["reason"] == "refinement_unverifiable"
    assert [row["refinement"] for row in result["unverifiable"]] == ["file_name"]
    assert result["failed"] == []


def test_an_event_outside_the_window_declines_on_both_sides():
    c = criteria("views", occurred={"from": "2026-09-26", "to": "2026-09-26"})
    old = matches(c, normalise_activity(event(action="viewed", occurred_at="2026-09-20T10:00:00Z")))
    new = matches(c, normalise_activity(event(action="viewed", occurred_at="2026-09-28T10:00:00Z")))
    assert old["reason"] == "occurred_before_window"
    assert new["reason"] == "occurred_after_window"


def test_an_event_within_the_window_matches():
    c = criteria("views", occurred="2026-09-27")
    assert matches(c, normalise_activity(event(action="viewed", occurred_at=ago(5))))["matched"]


def test_every_refinement_that_holds_is_reported_not_only_the_failing_one():
    result = matches(
        criteria("downloads", file_name="Pricing One-Pager", occurred="2026-09-27"),
        normalise_activity(event()),
    )
    assert result["matched"] is True
    assert sorted(row["refinement"] for row in result["checked"]) == ["file_name", "occurred"]


def test_map_activity_matches_the_documented_activity_text_whatever_the_capitalisation():
    c = criteria("map_activity", activity_text='completed task "Intro call"')
    event_with = normalise_activity(
        {
            "person": BUYER,
            "action": "completed_section",
            "activity_text": "completed task 'Intro call'",
        }
    )
    assert matches(c, event_with)["matched"] is True


def test_map_activity_matches_the_bare_task_name_too():
    c = criteria("map_activity", activity_text="Intro call")
    event_with = normalise_activity(
        {
            "person": BUYER,
            "action": "completed_section",
            "activity_text": "completed task 'Intro call'",
        }
    )
    assert matches(c, event_with)["matched"] is True


def test_a_different_task_declines():
    c = criteria("map_activity", activity_text='completed task "Sign up for free account"')
    event_with = normalise_activity(
        {
            "person": BUYER,
            "action": "completed_section",
            "activity_text": "completed task 'Intro call'",
        }
    )
    assert matches(c, event_with)["reason"] == "activity_text_mismatch"


def test_an_unfamiliar_activity_text_criterion_still_works_as_a_literal():
    c = criteria("map_activity", activity_text="moved to legal review")
    event_with = normalise_activity(
        {"person": BUYER, "action": "completed_section", "activity_text": "moved to legal review"}
    )
    assert matches(c, event_with)["matched"] is True


def test_a_criteria_with_no_refinement_matches_every_event_of_its_family():
    assert matches(criteria("views"), normalise_activity(event(action="viewed")))["matched"] is True
    assert matches(criteria("views"), normalise_activity(event()))["matched"] is False


# --------------------------------------------------------------------------- #
# Combining filters
# --------------------------------------------------------------------------- #


def test_several_refinements_of_one_family_must_all_hold():
    c = criteria("downloads", file_name="Pricing One-Pager", occurred="2026-09-27")
    assert evaluate([c], normalise_activity(download()))["matched"] is True
    outside = evaluate([c], normalise_activity(download(occurred_at="2026-09-20T10:00:00Z")))
    assert outside["matched"] is False
    assert outside["reason"] == "occurred_before_window"


def test_per_event_anding_is_available_and_kept_as_a_helper():
    """A single event can never be both a view and a download, so this is the
    reading that makes a two-family workflow unfireable - kept, not discarded."""
    result = evaluate(
        [criteria("views"), criteria("downloads")], normalise_activity(event(action="viewed"))
    )
    assert result["matched"] is False
    assert result["failed_count"] == 1
    assert result["criteria"][0]["matched"] is True
    assert result["criteria"][1]["matched"] is False


def test_the_contact_level_reading_lets_a_two_family_workflow_fire():
    """The filter 'evaluates criteria per contact', so each filter needs only some
    event to satisfy it. A per-event AND would refuse this rule silently."""
    events = [
        normalise_activity(event(action="viewed"), record_id="a1"),
        normalise_activity(download(), record_id="a2"),
    ]
    result = evaluate_contact([criteria("views"), criteria("downloads")], events)
    assert result["matched"] is True
    assert sorted(result["hit_activity_ids"]) == ["a1", "a2"]
    assert [row["match_count"] for row in result["criteria"]] == [1, 1]


def test_the_contact_level_reading_still_needs_every_filter_satisfied():
    events = [normalise_activity(event(action="viewed"), record_id="a1")]
    result = evaluate_contact([criteria("views"), criteria("downloads")], events)
    assert result["matched"] is False
    assert result["criteria"][0]["matched"] is True
    assert result["criteria"][1]["matched"] is False
    assert result["reason"] == "family_mismatch"
    assert "family_mismatch x1" in result["criteria"][1]["detail"]


def test_the_contact_level_reading_says_so_when_the_contact_did_nothing():
    result = evaluate_contact([criteria("views")], [])
    assert result["matched"] is False
    assert result["criteria"][0]["reason"] == "no_events"
    assert "no activity in this room" in result["criteria"][0]["detail"]


def test_a_workflow_with_no_filter_never_matches_at_either_granularity():
    """The catch-all that must not fall through: no criteria means nothing fires."""
    per_event = evaluate([], normalise_activity(download()))
    per_contact = evaluate_contact([], [normalise_activity(download(), record_id="a1")])
    assert per_event["matched"] is False
    assert per_event["reason"] == "no_criteria"
    assert per_contact["matched"] is False
    assert per_contact["reason"] == "no_criteria"
    assert "no filter" in per_event["detail"]


# --------------------------------------------------------------------------- #
# The lint
# --------------------------------------------------------------------------- #


def test_a_filter_with_nothing_narrowing_it_is_reported():
    """A views filter that matches every view is usually not the one intended."""
    codes = [w["code"] for w in lint_criteria(criteria("views"))]
    assert "no_refinement" in codes
    assert "no_occurred_filter" in codes


def test_the_no_refinement_message_names_what_the_family_could_be_refined_by():
    message = lint_criteria(criteria("views"))[0]["message"]
    assert "occurred" in message


def test_a_refined_filter_is_not_asked_for_a_refinement():
    codes = [w["code"] for w in lint_criteria(criteria("downloads", file_name="X"))]
    assert "no_refinement" not in codes
    assert "no_occurred_filter" in codes


def test_a_workflow_whose_action_kind_is_unknown_is_flagged_on_the_listing():
    warnings = lint_workflow(
        normalise_workflow(workflow_payload(actions=[{"kind": "teleport"}])), None
    )
    assert any(w["code"] == "action_unresolved" for w in warnings)


def test_an_update_field_with_no_value_is_flagged_as_writing_empty():
    warnings = lint_workflow(
        normalise_workflow(workflow_payload(actions=[{"kind": "update_field", "field": "x"}])),
        None,
    )
    assert any(w["code"] == "action_writes_empty" for w in warnings)


def test_the_lint_reports_a_missing_integration_without_refusing_the_draft():
    warnings = lint_workflow(normalise_workflow(workflow_payload()), None)
    assert any(w["code"] == "integration_missing" for w in warnings)


def test_the_lint_reports_a_disabled_integration(engine, integration, draft):
    engine.amend_integration(
        integration["id"], {"enabled": False}, actor="dana", source=INTEGRATION_SOURCE
    )
    codes = [w["code"] for w in engine.workflow(draft["id"])["warnings"]]
    assert "integration_disabled" in codes


# --------------------------------------------------------------------------- #
# The automation: enrollment
# --------------------------------------------------------------------------- #


def test_the_whole_researched_data_flow_in_one_call(engine, connected, draft, room):
    """Activity -> the filter evaluates per contact -> enrollment -> actions."""
    engine.publish(draft["id"], actor="dana", source=PUBLISH_SOURCE)
    engine.record_activity(room["id"], download(), actor="dana", source=SOURCE)
    result = engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)

    assert result["contact"] == BUYER
    assert result["room_id"] == room["id"]
    assert result["enrolled"] == 1
    enrollment = result["enrollments"][0]["enrollment"]
    assert enrollment["contact"] == BUYER
    assert enrollment["enrollment_type"] == "contact"
    assert enrollment["integration"] == DEFAULT_INTEGRATION
    assert enrollment["match_count"] == 1
    assert [action["kind"] for action in enrollment["action_plan"]] == [
        "send_email",
        "update_field",
    ]


def test_enrollment_is_a_function_of_the_contact_workflow_pair(engine, connected, published, room):
    """Contact based, and the activities are tied to the contact record - so a second
    matching event counts on the row already there rather than enrolling twice."""
    engine.record_activity(room["id"], download(), actor="dana", source=SOURCE)
    first = engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)
    again = engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)

    assert first["enrollments"][0]["outcome"] == "enrolled"
    assert again["enrollments"][0]["outcome"] == "already_enrolled"
    assert again["enrollments"][0]["enrollment"]["match_count"] == 2
    assert len(engine.enrollments(room_id=room["id"])) == 1


def test_a_second_contact_is_enrolled_separately(engine, connected, published, room):
    engine.record_activity(room["id"], download(), actor="dana", source=SOURCE)
    engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)
    other = engine.evaluate(
        room["id"], "b.buyer@northwind.example", actor="dana", source=EVALUATE_SOURCE
    )
    assert other["enrolled"] == 0
    assert other["misses"][0]["reason"] == "no_matching_activity"
    assert other["misses"][0]["criteria"][0]["reason"] == "no_events"


def test_another_buyers_activity_does_not_enrol_this_one(engine, connected, published, room):
    engine.record_activity(room["id"], download(), actor="dana", source=SOURCE)
    result = engine.evaluate(
        room["id"], "someone.else@example.com", actor="dana", source=EVALUATE_SOURCE
    )
    assert result["enrolled"] == 0
    assert result["events_considered"] == 0


def test_a_room_with_no_deal_connection_enrols_nobody_and_says_why(engine, integration, room):
    """Step 1 pairs the integration being on with the workspace being connected.

    Built from ``integration`` rather than ``connected`` on purpose: the ``connected``
    fixture links *this* room, so asking for both would connect it.
    """
    definition = engine.create(workflow_payload(), actor="dana", source=CREATE_SOURCE)
    engine.publish(definition["id"], actor="dana", source=PUBLISH_SOURCE)
    engine.record_activity(room["id"], download(), actor="dana", source=SOURCE)
    result = engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)
    assert result["enrolled"] == 0
    assert [row["reason"] for row in result["skipped"]] == ["room_not_connected"]
    assert "no stage to change" in result["skipped"][0]["detail"]


def test_the_same_workflow_does_fire_once_the_room_is_linked(engine, integration, room):
    definition = engine.create(workflow_payload(), actor="dana", source=CREATE_SOURCE)
    engine.publish(definition["id"], actor="dana", source=PUBLISH_SOURCE)
    engine.record_activity(room["id"], download(), actor="dana", source=SOURCE)
    engine.amend_integration(
        integration["id"],
        {"connections": {room["id"]: {"deal_id": "006NW"}}},
        actor="dana",
        source=INTEGRATION_SOURCE,
    )
    assert engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)["enrolled"] == 1


def test_the_same_workflow_does_fire_in_a_connected_room(engine, connected, published, room):
    engine.record_activity(room["id"], download(), actor="dana", source=SOURCE)
    assert engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)["enrolled"] == 1


def test_a_workflow_naming_an_integration_nobody_registered_is_skipped_naming_it(
    engine, connected, published, room
):
    """Disabling the integration is *refused* while a workflow depends on it, so the
    reachable way to lose one is the core API removing the record. The branch still
    has to report rather than crash, and it has to say which of the two it was."""
    engine.record_activity(room["id"], download(), actor="dana", source=SOURCE)
    engine.store.delete(
        connected["id"], actor="dana", source="DELETE /api/records/crm_integration/x"
    )
    result = engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)
    assert result["enrolled"] == 0
    assert [row["reason"] for row in result["skipped"]] == ["integration_unavailable"]
    assert "not registered" in result["skipped"][0]["detail"]


def test_a_draft_is_reported_as_a_draft_rather_than_ignored(engine, connected, draft, room):
    engine.record_activity(room["id"], download(), actor="dana", source=SOURCE)
    result = engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)
    assert [row["reason"] for row in result["skipped"]] == ["not_published"]


def test_a_workflow_that_matched_nothing_reports_why_with_a_sample(engine, connected, draft, room):
    engine.publish(draft["id"], actor="dana", source=PUBLISH_SOURCE)
    engine.record_activity(room["id"], event(action="viewed"), actor="dana", source=SOURCE)
    result = engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)
    miss = result["misses"][0]
    assert miss["reason"] == "no_matching_activity"
    assert miss["events_considered"] == 1
    assert miss["sample"][0]["reason"] == "family_mismatch"
    assert "family_mismatch x1" in miss["criteria"][0]["detail"]


def test_the_sample_puts_the_informative_reasons_first(engine, connected, draft, room):
    """A sample of five family mismatches tells a person nothing about their filter."""
    engine.publish(draft["id"], actor="dana", source=PUBLISH_SOURCE)
    # A link-URL filter, so the refinement is actually reached and declined, plus a
    # click with no URL at all for the other way a link-URL criterion can decline.
    link_rule = engine.create(
        workflow_payload(
            name="Pricing page clicked",
            trigger={"criteria": criteria("clicks", link_url="https://n.example/p")},
        ),
        actor="dana",
        source=CREATE_SOURCE,
    )
    engine.publish(link_rule["id"], actor="dana", source=PUBLISH_SOURCE)
    engine.record_activity(
        room["id"], click("https://n.example/other"), actor="dana", source=SOURCE
    )
    engine.record_activity(room["id"], click(), actor="dana", source=SOURCE)
    result = engine.evaluate(
        room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE, workflow_ids=[link_rule["id"]]
    )
    reasons = [row["reason"] for row in result["misses"][0]["sample"]]
    assert reasons[0] == "refinement_unverifiable"
    assert "link_url_mismatch" in reasons


def test_the_sample_covers_each_filter_of_a_two_filter_workflow(engine, connected, draft, room):
    both = engine.create(
        workflow_payload(
            name="Two filters",
            trigger={
                "criteria": [
                    criteria("clicks", link_url="https://n.example/p"),
                    criteria("downloads", file_name="Never Downloaded.pdf"),
                ]
            },
        ),
        actor="dana",
        source=CREATE_SOURCE,
    )
    engine.publish(both["id"], actor="dana", source=PUBLISH_SOURCE)
    engine.record_activity(
        room["id"], download(file_name="Something Else.pdf"), actor="dana", source=SOURCE
    )
    engine.record_activity(
        room["id"], click("https://n.example/other"), actor="dana", source=SOURCE
    )
    result = engine.evaluate(
        room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE, workflow_ids=[both["id"]]
    )
    families = {row["family"] for row in result["misses"][0]["sample"]}
    assert families == {"clicks", "downloads"}


def test_an_evaluation_of_an_empty_room_reports_no_events_rather_than_erroring(
    engine, connected, published, room
):
    result = engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)
    assert result["events_considered"] == 0
    assert result["enrolled"] == 0
    assert "no activity in this room" in result["misses"][0]["criteria"][0]["detail"]


def test_an_evaluation_may_be_limited_to_named_workflows(engine, connected, published, room):
    other = engine.create(
        workflow_payload(name="Second", trigger={"criteria": criteria("views")}),
        actor="dana",
        source=SOURCE,
    )
    engine.publish(other["id"], actor="dana", source=PUBLISH_SOURCE)
    engine.record_activity(room["id"], event(), actor="dana", source=SOURCE)
    result = engine.evaluate(
        room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE, workflow_ids=[other["id"]]
    )
    assert result["workflows_considered"] == 1
    assert result["enrolled"] == 0


def test_a_lookback_narrows_the_events_considered(engine, connected, draft, room):
    engine.publish(
        engine.amend(draft["id"], {"lookback_days": 1}, actor="dana", source=AMEND_SOURCE)["id"],
        actor="dana",
        source=PUBLISH_SOURCE,
    )
    engine.record_activity(
        room["id"], download(occurred_at=ago(60 * 24 * 30)), actor="dana", source=SOURCE
    )
    result = engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)
    assert result["enrolled"] == 0
    assert result["misses"][0]["criteria"][0]["reason"] == "no_events"


def test_a_lookback_is_measured_from_now_not_from_the_latest_event(engine, connected, draft, room):
    """Measured from the latest event, one old event always satisfies any window, so
    the setting would appear to do nothing exactly when somebody was using it."""
    engine.publish(
        engine.amend(draft["id"], {"lookback_days": 1}, actor="dana", source=AMEND_SOURCE)["id"],
        actor="dana",
        source=PUBLISH_SOURCE,
    )
    engine.record_activity(room["id"], download(occurred_at=ago(60)), actor="dana", source=SOURCE)
    assert engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)["enrolled"] == 1


def test_without_a_lookback_activity_of_any_age_still_fires(engine, connected, published, room):
    """A default window would stop a rule from firing on its own earlier activity."""
    engine.record_activity(
        room["id"], download(occurred_at=ago(60 * 24 * 30)), actor="dana", source=SOURCE
    )
    assert engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)["enrolled"] == 1


def test_the_webhook_alternative_path_is_recorded(engine, connected, published, room):
    """ "Dock's alternative path is to send the same events as webhooks into a HubSpot
    workflow webhook endpoint." """
    assert DELIVERY_PATHS == ("filter", "webhook")
    engine.record_activity(room["id"], download(delivery="webhook"), actor="dana", source=SOURCE)
    enrollment = engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)[
        "enrollments"
    ][0]["enrollment"]
    assert enrollment["via"] == "webhook"


def test_an_evaluation_needs_a_contact(engine, connected, published, room):
    with pytest.raises(WorkflowError) as caught:
        engine.evaluate(room["id"], "  ", actor="dana", source=EVALUATE_SOURCE)
    assert "needs a contact" in str(caught.value)


# --------------------------------------------------------------------------- #
# The actions, resolved
# --------------------------------------------------------------------------- #


def test_a_send_email_resolves_to_the_contact():
    """A contact given as a mapping under any documented alias, or as a bare string."""
    from_mapping = resolve_action(
        {"kind": "send_email", "index": 0, "resolved": True, "template": "t"},
        contact={"contact": BUYER},
    )
    from_string = resolve_action(
        {"kind": "send_email", "index": 0, "resolved": True, "template": "t"},
        contact=BUYER,
    )
    assert from_mapping["write"]["to"] == BUYER
    assert from_string["write"]["to"] == BUYER
    assert from_mapping["write"]["template"] == "t"
    assert from_mapping["executed"] is False


def test_a_slack_notification_records_the_channel_rather_than_calling_slack():
    row = resolve_action(
        {"kind": "slack_notification", "index": 0, "resolved": True, "channel": "#deals"},
        contact={"contact": BUYER},
    )
    assert row["write"]["channel"] == "#deals"
    assert row["write"]["contact"] == BUYER
    assert "through the HubSpot workflow action" in row["note"]


def test_an_update_field_writes_a_literal_value():
    row = resolve_action(
        {"kind": "update_field", "index": 0, "resolved": True, "field": "seen", "value": True},
        room={"id": "r1"},
    )
    assert row["write"] == {"kind": "update_field", "field": "seen", "value": True}
    assert "PATCH /crm/v3/objects/contacts/{contactId}" in row["api"]


def test_an_update_field_can_read_its_value_out_of_the_room():
    row = resolve_action(
        {
            "kind": "update_field",
            "index": 0,
            "resolved": True,
            "field": "stage",
            "value": {"from": "stage"},
        },
        room={"stage": "evaluation"},
    )
    assert row["write"]["value"] == "evaluation"


def test_a_path_that_resolves_to_nothing_yields_none_rather_than_crashing_the_run():
    row = resolve_action(
        {
            "kind": "update_field",
            "index": 0,
            "resolved": True,
            "field": "x",
            "value": {"from": "nope.deeper"},
        },
        room={"stage": "evaluation"},
    )
    assert row["write"]["value"] is None


def test_a_lifecycle_stage_may_only_move_forward():
    """you can only set the value *forward* in the stage order"."""
    action = {"kind": "change_stage", "index": 0, "resolved": True, "stage_kind": "lifecyclestage"}
    forward = resolve_action({**action, "stage": "salesqualifiedlead"}, lifecycle_stage="lead")
    assert forward["status"] == "planned"
    assert "forward in stage order" in forward["reason"]


def test_a_lifecycle_stage_at_or_behind_the_current_one_is_refused_for_that_action():
    action = {"kind": "change_stage", "index": 0, "resolved": True, "stage_kind": "lifecyclestage"}
    same = resolve_action({**action, "stage": "lead"}, lifecycle_stage="lead")
    back = resolve_action({**action, "stage": "lead"}, lifecycle_stage="opportunity")
    assert same["status"] == "refused"
    assert back["status"] == "refused"
    assert "only move forward" in back["reason"]


def test_a_deal_stage_moves_in_either_direction():
    """Change stages in HubSpot based on onboarding or mutual action plan tasks" is a
    pipeline, and a pipeline moves backwards."""
    action = {"kind": "change_stage", "index": 0, "resolved": True, "stage_kind": "deal_stage"}
    row = resolve_action({**action, "stage": "closed lost"})
    assert row["status"] == "planned"
    assert "not subject to the forward-only" in row["reason"]


def test_a_stage_outside_the_documented_lifecycle_list_is_refused():
    row = resolve_action(
        {
            "kind": "change_stage",
            "index": 0,
            "resolved": True,
            "stage_kind": "lifecyclestage",
            "stage": "quantum",
        },
        lifecycle_stage="lead",
    )
    assert row["status"] == "refused"
    assert "documented lifecycle stages" in row["reason"]


def test_a_refused_stage_does_not_refuse_the_workflow():
    """The constraint is about the property, not about the rule."""
    plan = resolve_actions(
        [
            {
                "kind": "change_stage",
                "index": 0,
                "resolved": True,
                "stage_kind": "lifecyclestage",
                "stage": "lead",
            },
            {
                "kind": "update_field",
                "index": 1,
                "resolved": True,
                "field": "reviewed",
                "value": True,
            },
        ],
        contact={"contact": BUYER},
        lifecycle_stage="opportunity",
    )
    counts = summarise(plan)
    assert [row["status"] for row in plan] == ["refused", "planned"]
    assert counts["refused"] == 1
    assert counts["planned"] == 1


def test_an_unknown_action_kind_is_reported_unresolved_and_kept():
    row = resolve_action({"kind": "teleport", "index": 0, "resolved": False})
    assert row["status"] == "unresolved"
    assert row["write"] is None
    assert "and more!" in row["reason"]


def test_nothing_is_executed_and_the_response_says_so():
    """The write side is HubSpot's CRM API; this build records what it would write."""
    counts = summarise([resolve_action({"kind": "send_email", "index": 0, "resolved": True})])
    assert counts["executed"] == 0
    assert "Recorded, not executed" in counts["execution"]


def test_the_lifecycle_stage_order_is_a_tuple_because_the_order_is_the_whole_point():
    assert lifecycle_rank("lead") < lifecycle_rank("opportunity")
    assert lifecycle_rank("subscriber") == 0
    assert lifecycle_rank("nope") is None
    assert len(LIFECYCLE_STAGES) == 8


# --------------------------------------------------------------------------- #
# Nothing happens on the seller's screen
# --------------------------------------------------------------------------- #


def test_the_research_says_nothing_happens_on_the_sellers_screen():
    assert "Nothing happens on the seller's screen" in ACTIONABILITY_NOTE


def test_an_evaluation_claims_nothing_is_actionable_and_says_why(fired):
    assert fired["actionable"] == 0
    assert fired["seller_visible"] is False
    assert fired["actionability_note"] == ACTIONABILITY_NOTE


def test_an_enrollment_row_carries_the_note_so_a_later_reader_still_sees_it(engine, fired, room):
    row = engine.enrollments(room_id=room["id"])[0]
    assert row["actionable"] == 0
    assert row["seller_visible"] is False
    assert "Nothing happens on the seller's screen" in row["actionability_note"]


def test_a_summary_claims_nothing_was_executed_and_nothing_is_actionable(engine, fired, room):
    summary = engine.summary(room_id=room["id"])
    assert summary["executed"] == 0
    assert summary["actionable"] == 0
    assert summary["seller_visible"] is False


def test_no_enrollment_carries_a_field_that_claims_a_seller_must_act(store, fired):
    """A feature that implied an enrollment gave a seller a task would mislead."""
    for record in store.list(ENROLLMENTS):
        data = record["data"]
        assert "task" not in data
        assert data["actionable"] == 0
        assert all(action["executed"] is False for action in data["action_plan"])


# --------------------------------------------------------------------------- #
# Activity recording and the idempotency key
# --------------------------------------------------------------------------- #


def test_recording_activity_writes_to_the_products_own_stream(engine, room):
    """one collection, one fact" - so this page and every other analytics page agree."""
    result = engine.record_activity(room["id"], event(), actor="dana", source=SOURCE)
    assert result["outcome"] == "recorded"
    assert engine.store.list(ACTIVITY, room_id=room["id"])[0]["id"] == result["activity"]["id"]


def test_a_repeated_webhook_is_not_stored_twice(engine, room):
    """A retried webhook must not inflate a match count."""
    first = engine.record_activity(
        room["id"], event(idempotency_key="k1"), actor="dana", source=SOURCE
    )
    second = engine.record_activity(
        room["id"], event(idempotency_key="k1"), actor="dana", source=SOURCE
    )
    assert first["outcome"] == "recorded"
    assert second["outcome"] == "duplicate"
    assert second["duplicate_attempts"] == 1
    assert len(engine.store.list(ACTIVITY)) == 1


def test_the_duplicate_counter_moves_on_a_third_attempt(engine, room):
    for _ in range(3):
        last = engine.record_activity(
            room["id"], event(idempotency_key="k1"), actor="dana", source=SOURCE
        )
    assert last["duplicate_attempts"] == 2
    assert len(engine.store.list(ACTIVITY)) == 1


def test_an_event_with_no_key_is_unaffected(engine, room):
    for _ in range(2):
        engine.record_activity(room["id"], event(), actor="dana", source=SOURCE)
    assert len(engine.store.list(ACTIVITY)) == 2


def test_a_row_another_feature_wrote_into_the_shared_stream_is_reported_not_raised(engine, room):
    """The `activity` collection is the product's own stream and is shared.

    A row one of the analytics features wrote may not carry a contact under any of
    the documented aliases. Raising on it would make this feature fail on a database
    with nothing wrong with it, and the refusal belongs on the *write* path where the
    caller is the one who can fix it. On the read path the row is kept and shown as
    belonging to no filter family - which is also the honest answer, because no
    filter can match it.
    """
    engine.store.create(
        ACTIVITY,
        {"action": "viewed", "target": "Deck", "seconds_on_page": 41},
        room_id=room["id"],
        actor="system",
        source="seed",
    )
    rows = engine.activity(room_id=room["id"])
    assert len(rows) == 1
    assert rows[0]["action_family"] is None
    assert rows[0]["contact"] is None
    assert [w["code"] for w in rows[0]["warnings"]] == ["unreadable_activity"]
    # And it matches nothing rather than breaking the run.
    assert matches(criteria("views"), rows[0])["reason"] == "action_unclassified"


def test_an_unreadable_row_does_not_break_a_summary_or_an_evaluation(engine, room, integration):
    definition = engine.create(workflow_payload(), actor="dana", source=CREATE_SOURCE)
    engine.publish(definition["id"], actor="dana", source=PUBLISH_SOURCE)
    engine.store.create(
        ACTIVITY, {"action": "viewed"}, room_id=room["id"], actor="system", source="seed"
    )
    result = engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)
    assert result["enrolled"] == 0
    assert engine.summary(room_id=room["id"])["activity_unclassified"] == 1


def test_activity_is_listed_oldest_first(engine, room):
    """An evaluation reads events in the order they happened."""
    for minutes in (30, 20, 10):
        engine.record_activity(
            room["id"], event(occurred_at=ago(minutes)), actor="dana", source=SOURCE
        )
    stamps = [row["occurred_at"] for row in engine.activity(room_id=room["id"])]
    assert stamps == sorted(stamps)


def test_activity_can_be_filtered_by_contact_and_by_family(engine, room):
    engine.record_activity(room["id"], event(action="viewed"), actor="dana", source=SOURCE)
    engine.record_activity(
        room["id"],
        event(action="downloaded", person="other@example.com"),
        actor="dana",
        source=SOURCE,
    )
    assert len(engine.activity(room_id=room["id"], family="downloads")) == 1
    assert len(engine.activity(room_id=room["id"], contact="other@example.com")) == 1
    assert len(engine.activity(room_id=room["id"])) == 2


# --------------------------------------------------------------------------- #
# Schema flexibility
# --------------------------------------------------------------------------- #


def test_a_field_a_team_added_is_filterable_with_no_migration(engine, room):
    definition = engine.create(
        workflow_payload(
            name="Team's own rule",
            trigger={"criteria": criteria("downloads")},
            owner_team="revenue-engineering",
        ),
        actor="dana",
        source=SOURCE,
    )
    found = engine.store.find(WORKFLOWS, {"owner_team": "revenue-engineering"}, limit=10)
    assert [row["id"] for row in found] == [definition["id"]]
    assert engine.workflows(integration=DEFAULT_INTEGRATION)[0]["name"] == definition["name"]


def test_a_field_a_team_added_to_a_connection_needs_no_migration(engine, room, connected):
    target = engine.integration_by_name(DEFAULT_INTEGRATION)
    engine.amend_integration(
        target["id"],
        {"connections": {room["id"]: {"deal_id": "006NW", "opportunity_id": "opp_1"}}},
        actor="dana",
        source=INTEGRATION_SOURCE,
    )
    stored = engine.integration(target["id"])
    assert stored["connections"][room["id"]]["opportunity_id"] == "opp_1"


def test_an_unknown_filter_in_a_query_is_a_json_path_not_a_column(engine, room):
    """`?where=` and find() resolve dotted paths through the dynamic index."""
    engine.record_activity(room["id"], event(campaign="q3-enterprise"), actor="dana", source=SOURCE)
    found = engine.store.find(ACTIVITY, {"campaign": "q3-enterprise"}, limit=10)
    assert len(found) == 1


# --------------------------------------------------------------------------- #
# The vocabulary and inference endpoints
# --------------------------------------------------------------------------- #


def test_the_vocabulary_publishes_the_five_families_with_their_refinements(engine):
    served = engine.vocabulary()
    assert served["filter_family_count"] == 5
    assert {row["name"] for row in served["filter_families"]} == set(FILTER_FAMILIES)
    assert served["refinement_matrix"] == {f: list(n) for f, n in REFINEMENTS.items()}
    assert served["actionability_note"] == ACTIONABILITY_NOTE


def test_the_vocabulary_states_why_one_list_refuses_and_the_other_warns(engine):
    served = engine.vocabulary()
    assert "closed" in served["action_kind_note"]
    assert served["enrollment_types"] == ["contact"]
    assert served["trigger_modes"] == ["filter_criteria_met"]
    assert served["delivery_paths"] == ["filter", "webhook"]


def test_the_vocabulary_says_the_write_side_is_recorded_not_executed(engine):
    assert "Recorded, not executed" in engine.vocabulary()["execution_note"]


def test_the_matcher_publishes_its_reasons_and_its_unverifiable_policy():
    served = matcher_vocabulary()
    assert "refinement_unverifiable" in served["match_reasons"]
    assert "positive evidence" in served["unverifiable_policy"]
    assert "ANDed" in served["combination"]


def test_every_inference_is_named_traceable_and_bounded():
    assert len(INFERENCES) >= 12
    seen = set()
    for entry in INFERENCES:
        assert entry["id"] not in seen, f"duplicate inference id {entry['id']}"
        seen.add(entry["id"])
        for field in ("topic", "basis", "value", "why", "change_it", "blast_radius"):
            assert entry.get(field), f"{entry['id']} has no {field}"


def test_the_register_says_which_parts_are_sourced_and_which_are_inferred():
    served = WorkflowEngine(RecordStore(AuditedDatabase(":memory:"))).inferences()
    assert served["sourced_quote"].startswith("When you select Dock")
    assert served["sourced"]["filter_families"] == list(FILTER_FAMILIES)
    assert served["sourced"]["lifecycle_constraint"].startswith("When you include")
    assert "only set the value *forward*" in served["sourced"]["lifecycle_constraint"]


@pytest.mark.parametrize(
    "identifier",
    [
        "unverifiable-refinement-does-not-match",
        "amend-published-workflow",
        "first-enrollment-wins",
        "occurred-window-is-a-whole-utc-day",
        "activity-text-matches-the-quoted-task-name",
        "link-url-is-exact-file-name-is-not",
        "filters-are-anded-and-uncapped",
        "action-list-is-open-filter-list-is-closed",
        "lookback-defaults-to-unlimited",
        "activity-idempotency-key",
        "lifecycle-stage-constraint-refuses-one-action",
        "lifecycle-stages-are-not-deal-stages",
        "integration-disabled-refuses-publish",
        "unconnected-room-enrols-nobody",
        "contact-is-the-activity-owner",
        "not-built",
    ],
)
def test_the_judgement_calls_this_build_made_are_all_named(identifier):
    assert by_id(identifier) is not None


def test_the_register_says_what_was_deliberately_not_built():
    entry = by_id("not-built")
    assert entry["value"]["lead_scoring"].startswith("a different workflow")
    assert entry["value"]["outbound_crm_calls"].startswith("not built")


# --------------------------------------------------------------------------- #
# The HTTP surface, through this feature's own router
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_is_served(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["filter_family_count"] == 5
    assert body["actionability_note"] == ACTIONABILITY_NOTE


def test_the_inferences_route_is_served(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] >= 12
    assert body["sourced_quote"].startswith("When you select Dock")


def test_the_whole_flow_over_http(http, http_room, http_published):
    created = http_published
    assert created["status"] == "published"

    recorded = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/activity", json=download(), params={"actor": "dana"}
    )
    assert recorded.status_code == 201
    assert recorded.json()["outcome"] == "recorded"

    result = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/evaluate",
        json={"contact": BUYER},
        params={"actor": "dana"},
    )
    assert result.status_code == 200
    body = result.json()
    assert body["enrolled"] == 1
    assert body["actionable"] == 0


def test_a_duplicate_webhook_answers_200_not_201_over_http(http, http_room, http_integration):
    payload = event(idempotency_key="k-http")
    first = http.post(f"{PREFIX}/rooms/{http_room['id']}/activity", json=payload)
    second = http.post(f"{PREFIX}/rooms/{http_room['id']}/activity", json=payload)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["outcome"] == "duplicate"


def test_the_contact_may_arrive_as_a_query_parameter(http, http_room, http_published):
    http.post(f"{PREFIX}/rooms/{http_room['id']}/activity", json=download())
    body = http.post(f"{PREFIX}/rooms/{http_room['id']}/evaluate", params={"contact": BUYER}).json()
    assert body["enrolled"] == 1


def test_an_evaluation_with_no_contact_is_a_400(http, http_room):
    response = http.post(f"{PREFIX}/rooms/{http_room['id']}/evaluate", json={})
    assert response.status_code == 400
    assert response.json()["error"] == "crm_workflow_error"


def test_a_404_for_an_unknown_workflow(http):
    assert http.get(f"{PREFIX}/workflows/nope").status_code == 404


def test_a_404_for_an_enrollment_in_another_room(http, http_room, http_published):
    other = http.post("/api/records/room", json={**ROOM, "name": "Other"}).json()
    http.post(f"{PREFIX}/rooms/{http_room['id']}/activity", json=download())
    body = http.post(f"{PREFIX}/rooms/{http_room['id']}/evaluate", json={"contact": BUYER}).json()
    enrollment_id = body["enrollments"][0]["enrollment"]["id"]
    assert (
        http.get(f"{PREFIX}/rooms/{http_room['id']}/enrollments/{enrollment_id}").status_code == 200
    )
    assert http.get(f"{PREFIX}/rooms/{other['id']}/enrollments/{enrollment_id}").status_code == 404


def test_the_workflow_listing_carries_the_lint_and_a_flag_count(http, http_workflow):
    body = http.get(f"{PREFIX}/workflows").json()
    assert body["count"] == 1
    assert body["actionability_note"] == ACTIONABILITY_NOTE
    assert "warnings" in body["workflows"][0]
    assert "flagged" in body["workflows"][0]


def test_the_library_can_be_filtered_by_status_over_http(http, http_workflow, http_published):
    assert http.get(f"{PREFIX}/workflows", params={"status": "draft"}).json()["count"] == 0
    assert http.get(f"{PREFIX}/workflows", params={"status": "published"}).json()["count"] == 1


def test_the_library_can_be_filtered_by_integration_over_http(http, http_workflow):
    assert (
        http.get(f"{PREFIX}/workflows", params={"integration": "salesforce"}).json()["count"] == 0
    )
    assert (
        http.get(f"{PREFIX}/workflows", params={"integration": DEFAULT_INTEGRATION}).json()["count"]
        == 1
    )


def test_a_withdrawn_workflow_is_listed_only_when_asked_over_http(http, http_workflow):
    http.delete(f"{PREFIX}/workflows/{http_workflow['id']}")
    assert http.get(f"{PREFIX}/workflows").json()["count"] == 0
    assert http.get(f"{PREFIX}/workflows", params={"include_withdrawn": True}).json()["count"] == 1


def test_the_summary_route_is_room_scoped(http, http_room, http_published):
    http.post(f"{PREFIX}/rooms/{http_room['id']}/activity", json=download())
    http.post(f"{PREFIX}/rooms/{http_room['id']}/evaluate", json={"contact": BUYER})
    body = http.get(f"{PREFIX}/rooms/{http_room['id']}/summary").json()
    assert body["room_id"] == http_room["id"]
    assert body["published"] == 1
    assert body["enrollments"] == 1
    assert body["executed"] == 0
    families = {row["family"]: row["count"] for row in body["activity_by_family"]}
    assert families["downloads"] == 1
    assert families["views"] == 0
    assert set(families) == set(FILTER_FAMILIES)


def test_the_summary_counts_activity_in_no_family_separately(http, http_room):
    http.post(f"{PREFIX}/rooms/{http_room['id']}/activity", json=event(action="booked_a_boat"))
    body = http.get(f"{PREFIX}/rooms/{http_room['id']}/summary").json()
    assert body["activity_unclassified"] == 1


def test_the_enrollments_route_is_room_scoped_and_filterable(http, http_room, http_published):
    http.post(f"{PREFIX}/rooms/{http_room['id']}/activity", json=download())
    http.post(f"{PREFIX}/rooms/{http_room['id']}/evaluate", json={"contact": BUYER})
    listed = http.get(
        f"{PREFIX}/rooms/{http_room['id']}/enrollments", params={"contact": BUYER}
    ).json()
    assert listed["count"] == 1
    assert listed["executed"] == 0
    assert (
        http.get(
            f"{PREFIX}/rooms/{http_room['id']}/enrollments", params={"contact": "nobody"}
        ).json()["count"]
        == 0
    )


def test_the_activity_route_groups_by_family(http, http_room):
    http.post(f"{PREFIX}/rooms/{http_room['id']}/activity", json=event(action="viewed"))
    http.post(f"{PREFIX}/rooms/{http_room['id']}/activity", json=download())
    body = http.get(f"{PREFIX}/rooms/{http_room['id']}/activity").json()
    assert body["count"] == 2
    assert {row["family"] for row in body["by_family"]} == {"views", "downloads"}


def test_an_event_with_no_contact_is_a_400_over_http(http, http_room):
    response = http.post(f"{PREFIX}/rooms/{http_room['id']}/activity", json={"action": "viewed"})
    assert response.status_code == 400
    assert response.json()["error"] == "malformed_activity"


def test_publishing_over_http_reports_the_missing_integration_as_409(http):
    created = http.post(f"{PREFIX}/workflows", json=workflow_payload()).json()
    response = http.post(f"{PREFIX}/workflows/{created['id']}/publish")
    assert response.status_code == 409
    assert response.json()["error"] == "integration_not_registered"


def test_amending_a_published_workflow_over_http_is_409(http, http_published):
    response = http.patch(f"{PREFIX}/workflows/{http_published['id']}", json={"name": "New"})
    assert response.status_code == 409
    assert response.json()["error"] == "published_workflow_is_immutable"


def test_the_error_body_carries_the_code_the_status_and_the_detail(http):
    body = http.post(f"{PREFIX}/workflows", json=workflow_payload(enrollment_type="company")).json()
    assert body["error"] == "workflow_must_be_contact_based"
    assert body["status"] == 400


# --------------------------------------------------------------------------- #
# The audit trail
# --------------------------------------------------------------------------- #


def test_recording_activity_is_audited_to_the_route_that_served_it(store, engine, room):
    engine.record_activity(room["id"], download(), actor="dana", source=SOURCE)
    entries = store.audit(collection=ACTIVITY)
    assert entries[0]["source"] == SOURCE
    assert entries[0]["action"] == "insert"


def test_the_duplicate_counter_is_audited_as_an_update(store, engine, room):
    engine.record_activity(room["id"], download(idempotency_key="k"), actor="dana", source=SOURCE)
    engine.record_activity(room["id"], download(idempotency_key="k"), actor="dana", source=SOURCE)
    entries = store.audit(collection=ACTIVITY)
    assert [entry["action"] for entry in entries] == ["update", "insert"]
    assert all(entry["source"] == SOURCE for entry in entries)


def test_creating_a_workflow_is_audited_to_the_create_route(store, engine, connected):
    engine.create(workflow_payload(), actor="dana", source=CREATE_SOURCE)
    assert store.audit(collection=WORKFLOWS)[0]["source"] == CREATE_SOURCE


def test_publishing_is_audited_to_the_publish_route(store, engine, draft):
    engine.publish(draft["id"], actor="dana", source=PUBLISH_SOURCE)
    entries = store.audit(collection=WORKFLOWS)
    assert entries[0]["action"] == "update"
    assert entries[0]["source"] == PUBLISH_SOURCE


def test_unpublishing_is_audited_to_the_unpublish_route(store, engine, published):
    engine.unpublish(published["id"], actor="dana", source=UNPUBLISH_SOURCE)
    assert store.audit(collection=WORKFLOWS)[0]["source"] == UNPUBLISH_SOURCE


def test_amending_is_audited_to_the_amend_route(store, engine, draft):
    engine.amend(draft["id"], {"name": "Renamed"}, actor="dana", source=AMEND_SOURCE)
    assert store.audit(collection=WORKFLOWS)[0]["source"] == AMEND_SOURCE


def test_enrolling_is_audited_to_the_evaluate_route(store, fired):
    assert store.audit(collection=ENROLLMENTS)[0]["source"] == EVALUATE_SOURCE


def test_an_already_enrolled_match_is_audited_as_an_update(
    store, engine, connected, published, room
):
    engine.record_activity(room["id"], download(), actor="dana", source=SOURCE)
    engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)
    engine.record_activity(room["id"], download(), actor="dana", source=SOURCE)
    engine.evaluate(room["id"], BUYER, actor="dana", source=EVALUATE_SOURCE)
    entries = store.audit(collection=ENROLLMENTS)
    assert [entry["action"] for entry in entries] == ["update", "insert"]
    assert all(entry["source"] == EVALUATE_SOURCE for entry in entries)


def test_the_audit_row_carries_the_actor_the_route_was_given(store, engine, room):
    engine.record_activity(room["id"], download(), actor="dana", source=SOURCE)
    assert store.audit(collection=ACTIVITY)[0]["actor"] == "dana"


def test_the_audit_row_carries_the_room_the_event_belongs_to(store, engine, room):
    engine.record_activity(room["id"], download(), actor="dana", source=SOURCE)
    assert store.audit(collection=ACTIVITY)[0]["room_id"] == room["id"]


def test_the_audit_row_carries_the_room_the_enrollment_belongs_to(store, fired, room):
    assert store.audit(collection=ENROLLMENTS)[0]["room_id"] == room["id"]


def test_registering_an_integration_is_audited_to_its_route(store, engine):
    engine.register_integration({"name": "hubspot"}, actor="dana", source=INTEGRATION_SOURCE)
    assert store.audit(collection=INTEGRATIONS)[0]["source"] == INTEGRATION_SOURCE


def test_a_withdrawn_workflow_is_soft_deleted_so_the_audit_trail_still_points_at_it(
    store, engine, published
):
    engine.withdraw(published["id"], actor="dana", source=WITHDRAW_SOURCE)
    entries = store.audit(collection=WORKFLOWS)
    assert entries[0]["action"] == "delete"
    assert entries[0]["source"] == WITHDRAW_SOURCE
    assert engine.store.get(published["id"]) is None
    # The row is still there, soft-deleted, so the enrollments naming it resolve to
    # something rather than to nothing.
    assert any(
        row.get("id") == published["id"]
        for row in engine.store.list(WORKFLOWS, limit=1000, include_deleted=True)
    )


def test_a_withdrawn_workflow_reports_itself_as_withdrawn_not_as_missing(engine, draft):
    engine.withdraw(draft["id"], actor="dana", source=WITHDRAW_SOURCE)
    with pytest.raises(AlreadyWithdrawn) as caught:
        engine.publish(draft["id"], actor="dana", source=PUBLISH_SOURCE)
    assert "still name it" in str(caught.value)


def test_nothing_ever_records_a_vendor_url_as_the_audit_source(http, http_room, http_published):
    """The researched write side is HubSpot's API; this product is not a proxy for it."""
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/activity", json=download(), params={"actor": "dana"}
    )
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/evaluate",
        json={"contact": BUYER},
        params={"actor": "dana"},
    )
    for entry in http.get("/api/audit", params={"limit": 500}).json()["entries"]:
        source = (entry.get("source") or "").lower()
        assert "hubspot" not in source
        assert "crm/v3" not in source
        assert "http" not in source


def test_no_route_interpolates_a_concrete_id_into_its_audit_source():
    """The audit row must name the *route*, not one of the thousands of paths onto it.

    `f".../workflows/{workflow_id}"` interpolates the concrete id, so the log fills
    with one-off paths no reader can match against the route table - and the day a
    route renames its parameter, every row naming the old one is stale. The house
    convention is a doubled brace, and this is the test that keeps it.
    """
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    concrete = [
        line.strip()
        for line in source.splitlines()
        if "source=f" in line and re.search(r"(?<!\{)\{(workflow_id|integration_id)\}", line)
    ]
    assert not concrete, "a route interpolates a concrete id into its source: " + "; ".join(
        concrete
    )


def test_every_source_the_feature_module_passes_names_its_own_prefix():
    """A source that does not start with this feature's prefix cannot be its route."""
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    prefixes = set(re.findall(r'source=f"([A-Z]+) \{router\.prefix\}', source))
    assert prefixes == {"POST", "PATCH", "DELETE"}, prefixes
    for line in source.splitlines():
        if "source=f" in line:
            assert "{router.prefix}" in line, line.strip()


# --------------------------------------------------------------------------- #
# The audit-source rule, checked against the live route table
# --------------------------------------------------------------------------- #


def _matches_registered_route(source: str, served: list[dict[str, Any]]) -> bool:
    parts = source.split(" ", 1)
    if len(parts) != 2:
        return False
    method, path = parts
    if not any(method in route["methods"] for route in served):
        return False
    actual = [segment for segment in path.split("/") if segment]
    for route in served:
        if method not in route["methods"]:
            continue
        template = [segment for segment in route["path"].split("/") if segment]
        if len(template) != len(actual):
            continue
        if all(
            expected.startswith("{") or expected == found
            for expected, found in zip(template, actual, strict=False)
        ):
            return True
    return False


def test_every_write_audit_row_names_a_route_the_app_serves(http, http_room):
    """The branch's central guarantee, checked against the route table the host reports.

    The same class of bug has shipped in this codebase before: a feature's audit log
    kept naming a path the app had stopped serving.
    """
    http.post(f"{PREFIX}/integrations", json={"name": "hubspot", "enabled": True})
    http.post(f"{PREFIX}/integrations", json={"name": "salesforce", "enabled": False})
    created = http.post(f"{PREFIX}/workflows", json=workflow_payload()).json()
    http.patch(f"{PREFIX}/workflows/{created['id']}", json={"description": "narrowed"})
    http.post(f"{PREFIX}/workflows/{created['id']}/publish")
    http.post(f"{PREFIX}/workflows/{created['id']}/unpublish")
    http.post(f"{PREFIX}/workflows/{created['id']}/publish")
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/activity", json=download(), params={"actor": "dana"}
    )
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/evaluate",
        json={"contact": BUYER},
        params={"actor": "dana"},
    )
    http.delete(f"{PREFIX}/workflows/{created['id']}")

    served = [
        route
        for feature in http.get("/api/features").json()["features"]
        for route in feature["routes"]
    ]
    entries = http.get("/api/audit", params={"limit": 500}).json()["entries"]
    sources = {entry["source"] for entry in entries if entry["source"]}

    # The core records API and the seeder write with their own sources; only the rows
    # this feature's HTTP layer produced are in scope here.
    ours = {source for source in sources if source.split(" ", 1)[1].startswith(PREFIX)}
    assert ours, f"no wf-030 write was audited at all; saw {sorted(sources)}"
    for source in sorted(ours):
        assert _matches_registered_route(source, served), (
            f"audit row names {source!r}, which is not a route this app serves"
        )


def test_the_evaluate_route_audits_under_its_own_route_not_the_activity_route(
    http, http_room, http_published
):
    http.post(f"{PREFIX}/rooms/{http_room['id']}/activity", json=download())
    http.post(f"{PREFIX}/rooms/{http_room['id']}/evaluate", json={"contact": BUYER})
    entries = http.get("/api/audit", params={"collection": ENROLLMENTS}).json()["entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == f"POST {PREFIX}/rooms/{{room_id}}/evaluate"


def test_the_activity_route_audits_under_its_own_route(http, http_room):
    http.post(f"{PREFIX}/rooms/{http_room['id']}/activity", json=download())
    entries = http.get("/api/audit", params={"collection": ACTIVITY}).json()["entries"]
    assert entries[0]["source"] == f"POST {PREFIX}/rooms/{{room_id}}/activity"


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed_rooms(store: RecordStore) -> list[tuple[str, str]]:
    """Demo rooms in the shape ``backend/seed.py`` passes: ``[(room_id, account)]``."""
    made = []
    for name, account in (
        ("Northwind Traders — Enterprise Evaluation", "Northwind Traders"),
        ("Contoso Health — Security Review", "Contoso Health"),
        ("Fabrikam Logistics — Renewal", "Fabrikam Logistics"),
    ):
        record = store.create("room", {**ROOM, "name": name, "account": account}, actor="dana")
        made.append((record["id"], account))
    return made


def run_seed(tmp_path, rooms=None):
    # `tmp_path / "a"` does not exist, and sqlite3.connect does not create
    # intermediate directories - the same trap `backend/seed.py` documents.
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = AuditedDatabase(tmp_path / "seeded.db")
    store = RecordStore(db)
    context = {
        "room_ids": rooms if rooms is not None else seed_rooms(store),
        "now": NOW,
        "rng": random.Random("wf030"),
    }
    summary = load_feature(MODULE).seed(db, context)
    return summary, store, db


def test_the_seed_produces_published_workflows_and_activity(tmp_path):
    summary, store, db = run_seed(tmp_path)
    assert "4 published workflows" in summary
    assert len(store.list(WORKFLOWS)) == 5
    assert len(store.list(INTEGRATIONS)) == 1
    assert len(store.list(ENROLLMENTS)) == 3
    db.close()


def test_the_seed_produces_the_states_that_are_not_all_successes(tmp_path):
    summary, store, db = run_seed(tmp_path)
    # A draft, so not_published is a row.
    assert any(row["data"]["status"] == "draft" for row in store.list(WORKFLOWS))
    # A filter that matched nothing.
    assert "1 matched nothing" in summary
    # A lifecycle stage that cannot move forward, refused for that action alone.
    assert "1 action refused for a lifecycle stage" in summary
    assert "the update_field beside it still applied" in summary
    # A duplicate webhook that was not stored.
    assert "1 duplicate webhook not stored" in summary
    # An activity word in no family.
    assert "1 event in no filter family" in summary
    # A room with no deal connection.
    assert "workflow(s) skipped for an unconnected room" in summary
    db.close()


def test_the_seed_produces_an_action_outside_the_four_reported_unresolved(tmp_path):
    _, store, db = run_seed(tmp_path)
    plans = [row["data"]["action_plan"] for row in store.list(ENROLLMENTS)]
    unresolved = [action for plan in plans for action in plan if action["status"] == "unresolved"]
    assert len(unresolved) == 1
    assert unresolved[0]["kind"] == "enroll_in_sequence"
    db.close()


def test_the_seed_shows_a_link_url_criterion_declining_two_ways(tmp_path):
    summary, _, db = run_seed(tmp_path)
    assert "link_url_mismatch" in summary
    assert "refinement_unverifiable" in summary
    db.close()


def test_the_seed_never_claims_anything_happened_on_the_sellers_screen(tmp_path):
    _, store, db = run_seed(tmp_path)
    for row in store.list(ENROLLMENTS):
        assert row["data"]["actionable"] == 0
        assert row["data"]["seller_visible"] is False
        assert "Nothing happens on the seller's screen" in row["data"]["actionability_note"]
    db.close()


def test_the_seeds_own_data_satisfies_the_criteria_that_fired_it(tmp_path):
    """A demo whose own activity does not satisfy its own filter teaches a reviewer
    nothing, and this is the failure mode a hand-written demo has."""
    _, store, db = run_seed(tmp_path)
    engine = WorkflowEngine(store)
    for row in store.list(ENROLLMENTS):
        data = row["data"]
        definition = engine.workflow(str(data["workflow_id"]))
        matched = []
        for activity_id in data["matched_activity_ids"]:
            record = store.get(activity_id)
            verdict = evaluate(
                (definition.get("trigger") or {}).get("criteria") or [],
                engine.present_activity(record),
            )
            assert verdict["matched"] is True, (
                f"enrollment {row['id']} claims activity {activity_id} matched, but it did not"
            )
            matched.append(activity_id)
        assert matched
    db.close()


def test_the_seed_is_reproducible_from_its_own_generator(tmp_path):
    first, _, db = run_seed(tmp_path / "a")
    db.close()
    second, _, db = run_seed(tmp_path / "b")
    db.close()
    assert first == second


def test_the_seed_copes_with_a_context_carrying_no_rooms(tmp_path):
    db = AuditedDatabase(tmp_path / "empty.db")
    summary = load_feature(MODULE).seed(db, {"room_ids": [], "now": NOW})
    assert "0 activity events" in summary
    assert len(db.list(WORKFLOWS)) == 5
    db.close()


def test_the_seed_copes_with_a_bare_room_id_rather_than_a_tuple(tmp_path):
    db = AuditedDatabase(tmp_path / "bare.db")
    store = RecordStore(db)
    room = store.create("room", ROOM, actor="dana")
    summary = load_feature(MODULE).seed(db, {"room_ids": [room["id"]], "now": NOW})
    assert "1 room(s) connected" in summary
    db.close()
