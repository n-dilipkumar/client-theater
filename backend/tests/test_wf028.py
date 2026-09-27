"""Tests for WF-028: turn a signal into an automatic seller action (Play registration).

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-028.md`` (section 13 of
``docs/research/raw/analytics-intent.md``). They are, in order:

* the Play request body - ``signal_registration_id``, localized
  ``name``/``label``/``description``, the ``indicators[]`` that should trigger it,
  and the ``attributes``;
* "These are the available Play task types: Call, Email, Add Person to a Cadence";
* "After registration, the registered Play must be enabled in the Salesloft UI. You
  can do so by going to Settings -> Workflow -> Plays -> Edit Play";
* "When a matching signal arrives, Salesloft creates the task (call / email /
  cadence membership) and assigns it via the precedence order User, Content,
  Person, Account";
* the Account row's own fallback - "The most engaged Person on the Account in the
  Last 30 days (Highest Buyer Engagement Score) / If there is no engagement,
  relate the task to the last person whose most recent contact was with the
  Account Owner";
* "An application can create more than one framework per signal registration";
* "A Play is an automation that generates a one-off action in response to an
  internal or external signal";
* "Track outcomes via Salesloft webhooks (``task_created``, ``task_completed``,
  ``step_created``, ``success_created``)";
* "A failing webhook is retried three additional times, spaced 15 seconds apart,
  before being marked as failed";
* "At this time, Dynamic Fields are not supported outside of email templates. The
  only exception here is that ``task_subject`` supports ``name``";
* "This workflow *is* the automation - signal -> task with no human in the loop
  until the seller acts".

The third of those - the activation sentence - is why so many tests below assert
that something is *absent* rather than present. A Play that fires on registration
puts a task in a seller's queue that nobody asked for, and that is the single bug
this feature cannot have.

Every part of the feature is reachable through its own router, so the HTTP tests
drive the mounted routes rather than calling handlers, and the audit-source tests
check every write against the route table the host actually reported.
"""

from __future__ import annotations

import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.db.audited import AuditedDatabase, RecordNotFound
from dsr.features import load_feature
from dsr.plays import (
    ActivationError,
    DeliveryConflict,
    DispatchError,
    EventError,
    FrameworkError,
    FrameworkInUse,
    PlayEngine,
    PlayError,
    PlayNotFound,
    UnknownSignal,
    UnknownSignalRegistration,
    UndeclaredTrigger,
    activation_state,
    amendment_findings,
    build_task,
    delivery,
    match_plays,
    missing_for,
    next_attempt_at,
    normalise_framework,
    normalise_subscription,
    render_subject,
    resolve_assignment,
    signal_indicator_keys,
)
from dsr.plays import events as event_rules
from dsr.plays import matching as matching_rules
from dsr.plays.assignment import RULES as ASSIGNMENT_RULES
from dsr.plays.assignment import describe as describe_assignment
from dsr.plays.inferences import INFERENCES, by_id
from dsr.plays.vocabulary import (
    ACTIVATION_PATH,
    ALL_ATTRIBUTE_KEYS,
    ATTRIBUTE_KEYS,
    AUTOMATION_NOTE,
    DISABLED_NOTE,
    ENGAGEMENT_WINDOW_DAYS,
    EVENT_TYPES,
    PLAY_EVENT_TYPES,
    SUPPORTED_DYNAMIC_FIELDS,
    TASK_TYPE_LABELS,
    TASK_TYPES,
    UNSOURCED_ATTRIBUTE_KEYS,
    WEBHOOK_RETRY_ATTEMPTS,
    WEBHOOK_RETRY_SPACING_SECONDS,
    find_dynamic_fields,
    locale_view,
    normalise_event_types,
    normalise_locale,
    require_event_type,
    require_locale_map,
    require_task_type,
    resolve_locale,
)
from dsr.store import RecordStore

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-028"

#: The source a route passes for a write. The pure-domain tests use the same
#: shape, so a test asserting on an audit row is asserting on the real thing
#: rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/dispatch"

MODULE = "wf028_turn_a_signal_into_an_automatic_seller"
FEATURE_ID = "wf-028-turn-a-signal-into-an-automatic-seller"

NOW = datetime(2026, 9, 27, 9, 0, tzinfo=timezone.utc)


def ago(minutes: float) -> str:
    return (NOW - timedelta(minutes=minutes)).isoformat()


def uuid4() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

REGISTRATION_ID = "signal_registration_demo_0001"

ROOM = {
    "name": "Northwind Traders — Enterprise Evaluation",
    "account": "Northwind Traders",
    "owner": "dana",
    "stage": "evaluation",
    "metadata": {"opportunity_id": "006NW"},
}

INDICATOR = {
    "key": "spent_more_than_30s_on_site",
    "metadata_shape": {
        "type": "object",
        "properties": {"time_in_seconds": {"type": "integer", "minimum": 0}},
        "required": ["time_in_seconds"],
    },
    "description": {"en": "Spent {time_in_seconds} seconds, past the threshold."},
}

REGISTRATION = {
    "signal_name": "Deep engagement with shared content",
    "type": "document_engagement",
    "integration_id": "dsr",
    "description": {"en": "{buyer_first_name} spent {time_in_seconds} seconds on {document_name}."},
    "data_shape": {
        "type": "object",
        "properties": {
            "document_name": {"type": "string", "minLength": 1},
            "time_in_seconds": {"type": "integer", "minimum": 0},
        },
        "required": ["document_name", "time_in_seconds"],
    },
    "indicators": [INDICATOR],
    "attribution": ["person_id", "account_id", "user_guid", "email_tracked_content_id"],
    "broadcast_notification": True,
}


def play_payload(**overrides: Any) -> dict[str, Any]:
    """A well-formed Play body, as the research describes one.

    Built by a function rather than a module constant so a test that changes one
    field is changing one field, and so the constant cannot drift from what the
    validator accepts.
    """
    payload: dict[str, Any] = {
        "signal_registration_id": REGISTRATION_ID,
        "name": {"en": "Call the engaged buyer", "fr": "Appeler l'acheteur engage"},
        "label": {"en": "Call engaged buyer"},
        "description": {"en": "A buyer spent real time on the pack. Call them today."},
        "indicators": ["spent_more_than_30s_on_site"],
        "attributes": {
            "task_type": "call",
            "task_subject": "Follow up with {name} on the security pack",
            "task_reminder_hours": 4,
        },
    }
    payload.update(overrides)
    return payload


def email_play_payload(**overrides: Any) -> dict[str, Any]:
    payload = play_payload(
        name={"en": "Email the engaged buyer"},
        label={"en": "Email engaged buyer"},
        indicators=["spent_more_than_30s_on_site"],
        attributes={
            "task_type": "email",
            "email_subject": "The implementation notes you asked about",
            "email_template": "implementation_notes",
        },
    )
    payload.update(overrides)
    return payload


def signal_payload(**overrides: Any) -> dict[str, Any]:
    """A signal as WF-027 stores one, which is what a Play fires on."""
    payload: dict[str, Any] = {
        "type": "document_engagement",
        "registration_id": REGISTRATION_ID,
        "data": {
            "document_name": "Security & Compliance Pack",
            "time_in_seconds": 214,
            "buyer_first_name": "Priya",
        },
        "indicators": [
            {"key": "spent_more_than_30s_on_site", "metadata": {"time_in_seconds": 214}}
        ],
        "urgency": "high",
        "occurred_at": ago(4),
        "idempotency_key": uuid4(),
        "attribution": {"user_guid": "usr_1042", "account_id": "acc_northwind"},
        "fields": {"name": "Priya"},
    }
    payload.update(overrides)
    return payload


def roster(score: int = 91, engaged_minutes: float = 60 * 24 * 3, **overrides: Any) -> list[dict[str, Any]]:
    """One Account candidate, in the shape the researched fallback reads."""
    entry = {
        "person_id": "per_000042",
        "engagement_score": score,
        "engaged_at": ago(engaged_minutes),
        "last_contact_at": (NOW - timedelta(days=9)).isoformat(),
        "contact_was_with_account_owner": True,
        "seller": "dana",
    }
    entry.update(overrides)
    return [entry]


class Clock:
    """A clock a test can move, so the 15 second retry spacing is testable."""

    def __init__(self, start: datetime = NOW) -> None:
        self.at = start

    def __call__(self) -> str:
        return self.at.isoformat(timespec="milliseconds")

    def advance(self, seconds: float) -> None:
        self.at = self.at + timedelta(seconds=seconds)


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(tmp_path / "wf028.db", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def engine(store, clock):
    return PlayEngine(store, now=clock)


def play_for(registration: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """The same Play body, aimed at a registration that actually exists.

    The HTTP tests create their registration through the core records API, so its
    id is generated. A Play naming the module constant there would be refused by
    design, which is the rule working rather than a test to work around.
    """
    return play_payload(signal_registration_id=registration["id"], **overrides)


@pytest.fixture()
def registration(store):
    """One live signal registration, because a Play cannot hang off nothing.

    Created under the module's own id so the payload constant and the stored
    registration cannot drift apart without a test failing.
    """
    return store.create(
        "signal_registration", dict(REGISTRATION), record_id=REGISTRATION_ID, actor="dana", source=SOURCE
    )


@pytest.fixture()
def room(store):
    return store.create("room", ROOM, actor="dana", source=SOURCE)


@pytest.fixture()
def registered(engine, registration):
    """One Play, registered and not enabled - the state every Play is born in."""
    return engine.register(play_payload(), actor="dana", source=SOURCE)["play"]


@pytest.fixture()
def live(engine, registered):
    """One Play, registered and then enabled - the state a Play has to reach."""
    return engine.enable(registered["id"], actor="dana", source=SOURCE)["play"]


@pytest.fixture()
def http(monkeypatch):
    """A client over a temporary database.

    The database path is resolved at lifespan time, so the variable is set before
    the context manager is entered - the same way ``test_features.py`` does it.
    """
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf028-http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as client:
        yield client
    tmp.cleanup()


@pytest.fixture()
def http_registration(http):
    return http.post("/api/records/signal_registration", json=REGISTRATION).json()


@pytest.fixture()
def http_room(http):
    return http.post("/api/records/room", json=ROOM).json()


@pytest.fixture()
def http_play(http, http_registration):
    return http.post(
        f"{PREFIX}/play-frameworks", json=play_for(http_registration), params={"actor": "dana"}
    ).json()["play"]


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The route resolves even though no shared file names this feature."""
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)

    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-028"
    assert entry["exception_handlers"] == ["PlayError"]
    assert len(entry["routes"]) == 19


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
        assert not any(
            route["path"].startswith(PREFIX) for route in feature["routes"]
        ), f"{feature['id']} also serves under {PREFIX}"


def test_feature_module_does_not_import_the_shared_app():
    """A guard exists in test_features.py; this states the reason locally."""
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source
    assert "from dsr.deps import" in source


def test_the_domain_package_does_not_import_another_feature():
    """``dsr.plays`` reads the registration collection; it must not import its owner.

    Two features authored in separate worktrees cannot share a Python module and
    still merge cleanly, which is the whole premise of the plugin host. Reading a
    collection through the store is the supported way to need another feature's
    data. Checked against the parsed imports rather than the file text, so a
    docstring naming the neighbour does not read as a dependency.
    """
    import ast

    package = Path(load_feature(MODULE).__file__).resolve().parents[1] / "plays"
    for module in package.glob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        for name in imported:
            assert not name.startswith("dsr.signals"), (
                f"{module.name} imports {name}, another feature's package"
            )
            assert not name.startswith("dsr.features"), (
                f"{module.name} imports {name}, the feature host"
            )
            assert not name.startswith("dsr.api"), f"{module.name} imports the shared app"


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
    assert set(module.EXCEPTION_HANDLERS) == {PlayError}
    assert issubclass(FrameworkError, PlayError)
    assert issubclass(ActivationError, PlayError)
    assert issubclass(DispatchError, PlayError)
    assert issubclass(EventError, PlayError)
    assert issubclass(UnknownSignalRegistration, FrameworkError)
    assert issubclass(UndeclaredTrigger, FrameworkError)
    assert issubclass(FrameworkInUse, FrameworkError)
    assert issubclass(UnknownSignal, DispatchError)
    # The core app already maps RecordNotFound to 404. Claiming it would be a
    # handler collision the host refuses, and this feature has no use for it.
    assert RecordNotFound not in set(module.EXCEPTION_HANDLERS)


def test_seed_is_exported_by_the_feature_module():
    """Demo data belongs to the feature, which is what ``backend/seed.py`` looks for."""
    assert callable(load_feature(MODULE).seed)


# --------------------------------------------------------------------------- #
# The vocabulary the research fixes by name
# --------------------------------------------------------------------------- #


def test_the_task_types_are_exactly_the_three_documented_ones():
    assert TASK_TYPES == ("call", "email", "add-to-cadence")


def test_the_task_type_labels_are_the_researches_own_capitalisation():
    assert [TASK_TYPE_LABELS[name] for name in TASK_TYPES] == [
        "Call",
        "Email",
        "Add Person to a Cadence",
    ]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("call", "call"),
        ("CALL", "call"),
        ("  Call  ", "call"),
        ("Email", "email"),
        ("add-to-cadence", "add-to-cadence"),
        ("add_to_cadence", "add-to-cadence"),
        ("Add Person to a Cadence", "add-to-cadence"),
    ],
)
def test_a_documented_task_type_is_accepted_in_any_documented_spelling(value, expected):
    """The vendor's label and its wire spelling are one value, not two that look alike."""
    assert require_task_type(value) == expected


@pytest.mark.parametrize("value", ["sms", "", None, 3, ["call"], "add to conversation"])
def test_a_task_type_outside_the_vocabulary_is_refused(value):
    with pytest.raises(FrameworkError):
        require_task_type(value)


def test_the_event_types_include_the_four_the_research_says_to_track():
    for name in ("task_created", "task_completed", "step_created", "success_created"):
        assert name in EVENT_TYPES
    assert set(PLAY_EVENT_TYPES) == {"task_created", "task_completed", "step_created", "success_created"}


def test_the_event_type_vocabulary_is_the_researched_list():
    assert EVENT_TYPES == (
        "task_created",
        "task_updated",
        "task_completed",
        "step_created",
        "step_updated",
        "success_created",
        "email_updated",
        "conversation_created",
        "conversation_recording_created",
        "call_created",
        "meeting_booked",
        "link_swap",
    )


def test_the_retry_policy_is_the_researched_one_to_the_second():
    assert WEBHOOK_RETRY_ATTEMPTS == 3
    assert WEBHOOK_RETRY_SPACING_SECONDS == 15


def test_the_engagement_window_is_the_researched_thirty_days():
    assert ENGAGEMENT_WINDOW_DAYS == 30


def test_the_activation_path_is_the_one_the_research_names():
    assert ACTIVATION_PATH == "Settings → Workflow → Plays → Edit Play"
    assert ACTIVATION_PATH in DISABLED_NOTE


def test_a_bare_string_locale_becomes_one_english_entry():
    assert require_locale_map("Call the buyer", "name") == {"en": "Call the buyer"}


def test_an_empty_locale_map_is_refused():
    with pytest.raises(PlayError):
        require_locale_map({}, "name")


def test_an_empty_locale_message_is_refused():
    with pytest.raises(PlayError):
        require_locale_map({"en": "   "}, "description")


@pytest.mark.parametrize(
    "given,expected",
    [("en", "en"), ("en-GB", "en-GB"), ("EN_gb", "en-GB"), (" fr ", "fr")],
)
def test_locales_are_normalised_to_one_spelling(given, expected):
    assert normalise_locale(given) == expected


def test_an_exact_locale_wins():
    assert resolve_locale("fr", {"en": "a", "fr": "b"}) == ("fr", False)


def test_the_bare_language_is_the_first_fallback():
    assert resolve_locale("fr-CA", {"en": "a", "fr": "b"}) == ("fr", True)


def test_english_is_the_second_fallback():
    assert resolve_locale("de-AT", {"en": "a", "fr": "b"}) == ("en", True)


def test_a_locale_view_says_when_it_fell_back():
    view = locale_view({"en": "Call the buyer"}, "de-AT")
    assert view == {"locale": "en", "fell_back": True, "available": ["en"], "text": "Call the buyer"}


def test_a_bare_event_type_is_accepted_and_normalised():
    assert require_event_type("  Task_Created ") == "task_created"


def test_an_undocumented_event_type_is_refused():
    with pytest.raises(PlayError):
        require_event_type("task_exploded")


def test_event_types_are_deduplicated_and_ordered_by_the_vocabulary():
    assert normalise_event_types(["success_created", "task_created", "success_created"]) == [
        "task_created",
        "success_created",
    ]


def test_a_comma_separated_event_type_string_is_accepted():
    assert normalise_event_types("task_created, task_completed") == [
        "task_created",
        "task_completed",
    ]


def test_an_empty_event_type_list_is_refused():
    with pytest.raises(PlayError):
        normalise_event_types([])


# --------------------------------------------------------------------------- #
# Registering a Play
# --------------------------------------------------------------------------- #


def test_a_well_formed_play_is_registered_and_stored(engine, registration):
    result = engine.register(play_payload(), actor="dana", source=SOURCE)

    assert result["outcome"] == "registered"
    play = result["play"]
    assert play["id"].startswith("play_framework_")
    assert play["signal_registration_id"] == REGISTRATION_ID
    assert play["indicators"] == ["spent_more_than_30s_on_site"]
    assert play["attributes"]["task_type"] == "call"


def test_a_play_normalises_to_the_researched_fields(registered):
    for field in ("signal_registration_id", "name", "label", "description", "indicators", "attributes"):
        assert field in registered, field


def test_a_play_is_registered_disabled_and_says_the_researched_note(registered):
    """"After registration, the registered Play must be enabled in the Salesloft UI"."""
    assert registered["enabled"] is False
    assert registered["state"] == "disabled"
    assert registered["enabled_at"] is None
    assert registered["enabled_by"] is None
    assert registered["will_create_tasks"] is False
    assert "After registration" in registered["note"]


def test_registration_alone_creates_no_task_at_all(engine, registered, room):
    """The single most important assertion in this file.

    A Play that fires on registration puts a task in a seller's queue that nobody
    asked for, and the seller has no way to trace it back to the signal that caused
    it.
    """
    result = engine.dispatch(
        {"signal": signal_payload()},
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )

    assert result["created_count"] == 0
    assert engine.generated_tasks(room_id=room["id"]) == []
    assert [decision["reason"] for decision in result["decisions"]] == ["not_enabled"]


def test_a_second_play_on_one_registration_is_allowed(engine, registration):
    """"An application can create more than one framework per signal registration"."""
    first = engine.register(play_payload(), actor="dana", source=SOURCE)["play"]
    second = engine.register(email_play_payload(), actor="dana", source=SOURCE)["play"]

    assert first["id"] != second["id"]
    assert len(engine.frameworks(signal_registration_id=REGISTRATION_ID)) == 2


def test_a_play_against_a_registration_that_does_not_exist_is_refused(engine):
    """The researched flow registers the signal first, so the order is not optional."""
    with pytest.raises(UnknownSignalRegistration) as caught:
        engine.register(
            play_payload(signal_registration_id="signal_registration_nope"),
            actor="dana",
            source=SOURCE,
        )
    assert "signal_registration_nope" in str(caught.value)


def test_a_play_against_a_registration_in_another_collection_is_refused(engine, room):
    """A room id is not a registration id, and the lookup checks the collection."""
    with pytest.raises(UnknownSignalRegistration):
        engine.register(play_payload(signal_registration_id=room["id"]), actor="dana", source=SOURCE)


def test_a_trigger_the_registration_does_not_declare_is_refused(engine, registration):
    """A signal only ever carries indicators its registration declares."""
    with pytest.raises(FrameworkError) as caught:
        engine.register(
            play_payload(indicators=["watched_more_than_75_percent"]),
            actor="dana",
            source=SOURCE,
        )
    assert "not declared" in str(caught.value)
    assert engine.frameworks() == []


def test_the_refusal_names_the_declared_indicators(engine, registration):
    with pytest.raises(FrameworkError) as caught:
        engine.register(play_payload(indicators=["nope"]), actor="dana", source=SOURCE)
    assert "spent_more_than_30s_on_site" in str(caught.value)


def test_a_declared_indicator_is_accepted_as_a_bare_key_or_an_object(engine, registration):
    play = engine.register(
        play_payload(indicators=[{"key": "spent_more_than_30s_on_site"}]),
        actor="dana",
        source=SOURCE,
    )["play"]
    assert play["indicators"] == ["spent_more_than_30s_on_site"]


def test_a_duplicate_trigger_is_stored_once(engine, registration):
    play = engine.register(
        play_payload(indicators=["spent_more_than_30s_on_site", "spent_more_than_30s_on_site"]),
        actor="dana",
        source=SOURCE,
    )["play"]
    assert play["indicators"] == ["spent_more_than_30s_on_site"]


@pytest.mark.parametrize(
    "field",
    ["signal_registration_id", "name", "label", "description", "indicators", "attributes"],
)
def test_every_researched_field_is_required(field):
    payload = play_payload()
    payload.pop(field)
    with pytest.raises(PlayError):
        normalise_framework(payload)


def test_an_empty_indicator_list_is_refused():
    with pytest.raises(FrameworkError):
        normalise_framework(play_payload(indicators=[]))


def test_an_unknown_field_on_a_play_is_refused_rather_than_dropped():
    """A Play body is a vendor contract with an enumerated member list."""
    with pytest.raises(FrameworkError) as caught:
        normalise_framework(play_payload(priority="high"))
    assert "priority" in str(caught.value)


def test_a_caller_may_not_supply_this_products_own_bookkeeping():
    """``enabled`` is reached through its own route, so the audit row names it."""
    with pytest.raises(FrameworkError) as caught:
        normalise_framework(play_payload(enabled=True))
    assert "enable route" in str(caught.value)


def test_camel_case_play_fields_are_folded_onto_the_researched_names(engine, registration):
    play = engine.register(
        play_payload(
            signalRegistrationId=REGISTRATION_ID,
            attributes={
                "taskType": "call",
                "taskSubject": "Ring {name}",
                "taskReminderHours": 2,
            },
        ),
        actor="dana",
        source=SOURCE,
    )["play"]
    assert play["signal_registration_id"] == REGISTRATION_ID
    assert play["attributes"] == {
        "task_type": "call",
        "task_reminder_hours": 2,
        "task_subject": "Ring {name}",
    }


# --------------------------------------------------------------------------- #
# The attributes
# --------------------------------------------------------------------------- #


def test_the_sourced_attributes_are_the_researched_five():
    assert ATTRIBUTE_KEYS == (
        "task_type",
        "task_subject",
        "task_reminder_hours",
        "email_subject",
        "email_template",
    )


def test_the_unsourced_attribute_is_published_as_unsourced():
    """"Add Person to a Cadence" has no researched attribute naming a cadence."""
    assert UNSOURCED_ATTRIBUTE_KEYS == ("cadence_id",)
    assert "cadence_id" in ALL_ATTRIBUTE_KEYS
    assert "cadence_id" not in ATTRIBUTE_KEYS


def test_a_call_needs_a_task_subject():
    with pytest.raises(FrameworkError) as caught:
        normalise_framework(
            play_payload(attributes={"task_type": "call"})
        )
    assert "task_subject" in str(caught.value)


def test_an_email_needs_its_own_email_subject_not_a_task_subject():
    """The researched list carries two subjects; each type uses its own."""
    with pytest.raises(FrameworkError) as caught:
        normalise_framework(
            play_payload(
                attributes={"task_type": "email", "task_subject": "Call them"}
            )
        )
    assert "email_subject" in str(caught.value)


def test_a_cadence_play_needs_a_task_subject():
    with pytest.raises(FrameworkError) as caught:
        normalise_framework(play_payload(attributes={"task_type": "add-to-cadence"}))
    assert "task_subject" in str(caught.value)


def test_a_cadence_play_with_no_cadence_is_registered_and_warned_about(engine, registration):
    """The research's own gap, made a row rather than a sentence in a comment."""
    result = engine.register(
        play_payload(
            attributes={"task_type": "add-to-cadence", "task_subject": "Keep talking to {name}"}
        ),
        actor="dana",
        source=SOURCE,
    )
    codes = [warning["code"] for warning in result["warnings"]]
    assert "no_cadence_named" in codes
    assert result["play"]["attributes"].get("cadence_id") is None


def test_a_cadence_play_with_a_cadence_registers_without_that_warning(engine, registration):
    result = engine.register(
        play_payload(
            attributes={
                "task_type": "add-to-cadence",
                "task_subject": "Keep talking to {name}",
                "cadence_id": "cad_4471",
            }
        ),
        actor="dana",
        source=SOURCE,
    )
    assert [w["code"] for w in result["warnings"]] == []
    assert result["play"]["attributes"]["cadence_id"] == "cad_4471"


def test_an_unrecognised_attribute_is_refused_and_names_the_researched_list():
    with pytest.raises(FrameworkError) as caught:
        normalise_framework(
            play_payload(attributes={"task_type": "call", "task_subject": "Ring", "sms_body": "hi"})
        )
    assert "sms_body" in str(caught.value)
    assert "email_template" in str(caught.value)


def test_an_email_attribute_on_a_call_is_carried_but_warned_about():
    _, warnings = normalise_framework(
        play_payload(
            attributes={
                "task_type": "call",
                "task_subject": "Ring",
                "email_template": "follow_up",
            }
        )
    )
    assert "attribute_not_used_by_task_type" in [w["code"] for w in warnings]


@pytest.mark.parametrize("value", [-1, 1.5, "four", True, [4]])
def test_a_reminder_that_is_not_whole_non_negative_hours_is_refused(value):
    with pytest.raises(FrameworkError):
        normalise_framework(
            play_payload(
                attributes={
                    "task_type": "call",
                    "task_subject": "Ring",
                    "task_reminder_hours": value,
                }
            )
        )


def test_a_whole_float_reminder_is_accepted_as_hours():
    data, _ = normalise_framework(
        play_payload(attributes={"task_type": "call", "task_subject": "Ring", "task_reminder_hours": 4.0})
    )
    assert data["attributes"]["task_reminder_hours"] == 4


def test_a_zero_reminder_is_accepted():
    data, _ = normalise_framework(
        play_payload(attributes={"task_type": "call", "task_subject": "Ring", "task_reminder_hours": 0})
    )
    assert data["attributes"]["task_reminder_hours"] == 0


# --------------------------------------------------------------------------- #
# Dynamic fields - where they are allowed, and which
# --------------------------------------------------------------------------- #


def test_find_dynamic_fields_reads_the_simple_form():
    assert find_dynamic_fields("Hello {name}, see {room}") == ["name", "room"]


def test_find_dynamic_fields_ignores_a_string_with_no_field():
    assert find_dynamic_fields("just a sentence") == []


def test_the_supported_dynamic_field_is_name_and_only_name():
    """"The only exception here is that task_subject supports name"."""
    assert SUPPORTED_DYNAMIC_FIELDS == ("name",)


def test_task_subject_may_carry_the_name_field():
    data, _ = normalise_framework(
        play_payload(attributes={"task_type": "call", "task_subject": "Call {name} today"})
    )
    assert data["attributes"]["task_subject"] == "Call {name} today"


def test_task_subject_may_not_carry_any_other_field():
    with pytest.raises(FrameworkError) as caught:
        normalise_framework(
            play_payload(attributes={"task_type": "call", "task_subject": "Call {account}"})
        )
    assert "account" in str(caught.value)


def test_email_subject_may_not_carry_a_dynamic_field_at_all():
    """"Dynamic Fields are not supported outside of email templates"."""
    with pytest.raises(FrameworkError) as caught:
        normalise_framework(
            play_payload(
                attributes={
                    "task_type": "email",
                    "email_subject": "Hi {name}",
                    "email_template": "notes",
                }
            )
        )
    assert "email_template" in str(caught.value)


def test_an_email_template_may_carry_any_field():
    """The rule is about *where*, so a template's field names are not checked."""
    data, _ = normalise_framework(
        play_payload(
            attributes={
                "task_type": "email",
                "email_subject": "The notes you asked about",
                "email_template": "Hi {name}, room {room_name} is ready",
            }
        )
    )
    assert data["attributes"]["email_template"] == "Hi {name}, room {room_name} is ready"


def test_a_reminder_hours_cannot_carry_a_field_because_it_is_a_number():
    with pytest.raises(FrameworkError):
        normalise_framework(
            play_payload(
                attributes={
                    "task_type": "call",
                    "task_subject": "Call {name}",
                    "task_reminder_hours": "{name}",
                }
            )
        )


def test_an_unclosed_brace_is_warned_about_rather_than_refused():
    _, warnings = normalise_framework(
        play_payload(attributes={"task_type": "call", "task_subject": "Call {name today"})
    )
    assert "unclosed_brace" in [w["code"] for w in warnings]


# --------------------------------------------------------------------------- #
# The enable switch - the one moment with a human in the loop
# --------------------------------------------------------------------------- #


def test_enabling_records_who_and_when(engine, registered, clock):
    enabled = engine.enable(registered["id"], actor="dana", source=SOURCE)["play"]

    assert enabled["enabled"] is True
    assert enabled["state"] == "enabled"
    assert enabled["enabled_by"] == "dana"
    assert enabled["enabled_at"] == clock()
    assert enabled["will_create_tasks"] is True
    assert enabled["note"] is None


def test_enabling_twice_writes_nothing_and_says_so(engine, live, store):
    before = store.get(live["id"])["revision"]
    result = engine.enable(live["id"], actor="dana", source=SOURCE)

    assert result["outcome"] == "already_enabled"
    assert store.get(live["id"])["revision"] == before


def test_disabling_leaves_the_tasks_alone(engine, live, room):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    engine.disable(live["id"], actor="dana", source=SOURCE)

    assert engine.generated_tasks(room_id=room["id"]) != []


def test_disabling_twice_writes_nothing(engine, registered, store):
    before = store.get(registered["id"])["revision"]
    result = engine.disable(registered["id"], actor="dana", source=SOURCE)

    assert result["outcome"] == "already_disabled"
    assert store.get(registered["id"])["revision"] == before


def test_a_disabled_play_fires_nothing_and_a_re_enabled_one_does(engine, live, room, clock):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    engine.disable(live["id"], actor="dana", source=SOURCE)
    engine.dispatch(
        {"signal": signal_payload(idempotency_key=uuid4())},
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    engine.enable(live["id"], actor="dana", source=SOURCE)
    engine.dispatch(
        {"signal": signal_payload(idempotency_key=uuid4())},
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )

    assert len(engine.generated_tasks(room_id=room["id"])) == 2


def test_activation_state_is_derived_so_a_row_cannot_disagree_with_itself(registered):
    state = activation_state(registered)
    assert state["state"] == "disabled"
    assert state["enabled"] is False
    assert state["activation_path"] == ACTIVATION_PATH


def test_a_destroyed_play_cannot_be_enabled(engine, registered):
    """A retired template is not found, and reviving it is not an option."""
    engine.destroy(registered["id"], actor="dana", source=SOURCE)
    with pytest.raises(PlayNotFound):
        engine.enable(registered["id"], actor="dana", source=SOURCE)


def test_enabling_a_destroyed_record_is_refused_by_the_activation_rule(store):
    """The activation guard, reached directly rather than through the engine.

    The engine's lookup excludes soft-deleted rows, so this branch is only
    reachable when the record is handed in - which is exactly the situation a
    caller who cached a Play and then had it retired is in.
    """
    from dsr.plays import activation

    with pytest.raises(ActivationError):
        activation.enable({"deleted_at": "2026-09-27T09:00:00+00:00"}, actor="dana", now=lambda: "now")
    with pytest.raises(ActivationError):
        activation.disable({"deleted_at": "2026-09-27T09:00:00+00:00"}, actor="dana", now=lambda: "now")


# --------------------------------------------------------------------------- #
# Amending a Play
# --------------------------------------------------------------------------- #


def test_adding_a_locale_is_allowed_and_merges(engine, registered):
    amended = engine.amend(
        registered["id"],
        {"label": {"fr": "Appeler l'acheteur"}},
        actor="dana",
        source=SOURCE,
    )
    assert amended["label"] == {"en": "Call engaged buyer", "fr": "Appeler l'acheteur"}

def test_adding_a_locale_is_allowed_even_once_the_play_is_live(engine, live, room):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    amended = engine.amend(
        live["id"], {"label": {"de": "Engagierten Käufer anrufen"}}, actor="dana", source=SOURCE
    )
    assert "de" in amended["label"]


def test_removing_a_locale_is_refused_because_a_sentence_that_existed_is_gone(engine, registered):
    """A patch merges, so dropping a sentence has to be asked for explicitly."""
    with pytest.raises(FrameworkInUse) as caught:
        engine.amend(registered["id"], {"label": {"en": None}}, actor="dana", source=SOURCE)
    assert "label.en" in str(caught.value)


def test_a_patch_that_omits_a_locale_never_drops_one(engine, registered):
    amended = engine.amend(
        registered["id"],
        {"label": {"fr": "Appeler l'acheteur"}},
        actor="dana",
        source=SOURCE,
    )
    assert amended["label"] == {"en": "Call engaged buyer", "fr": "Appeler l'acheteur"}


def test_the_activation_fields_are_not_patchable(engine, registered):
    with pytest.raises(FrameworkInUse) as caught:
        engine.amend(registered["id"], {"enabled": True}, actor="dana", source=SOURCE)
    assert "own route" in str(caught.value)


def test_the_registration_is_identity_and_may_not_be_moved(engine, registered):
    with pytest.raises(FrameworkInUse) as caught:
        engine.amend(
            registered["id"],
            {"signal_registration_id": "signal_registration_other"},
            actor="dana",
            source=SOURCE,
        )
    assert "signal_registration_id" in str(caught.value)


def test_patching_the_registration_to_the_same_value_is_allowed(engine, registered):
    amended = engine.amend(
        registered["id"],
        {"signal_registration_id": REGISTRATION_ID},
        actor="dana",
        source=SOURCE,
    )
    assert amended["signal_registration_id"] == REGISTRATION_ID


def test_indicators_may_be_changed_while_the_play_has_fired_nothing(engine, registered):
    amended = engine.amend(
        registered["id"],
        {"indicators": ["spent_more_than_30s_on_site", "watched_more_than_75_percent"]},
        actor="dana",
        source=SOURCE,
    )
    assert len(amended["indicators"]) == 2


def test_indicators_are_frozen_once_the_play_has_created_a_task(engine, live, room):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    with pytest.raises(FrameworkInUse) as caught:
        engine.amend(
            live["id"],
            {"indicators": ["watched_more_than_75_percent"]},
            actor="dana",
            source=SOURCE,
        )
    assert "frozen" in str(caught.value)


def test_attributes_are_frozen_once_the_play_has_created_a_task(engine, live, room):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    with pytest.raises(FrameworkInUse):
        engine.amend(
            live["id"],
            {"attributes": {"task_type": "email", "email_subject": "Hi"}},
            actor="dana",
            source=SOURCE,
        )


def test_a_sellers_written_label_is_frozen_once_the_play_has_created_a_task(engine, live, room):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    with pytest.raises(FrameworkInUse):
        engine.amend(
            live["id"], {"label": {"en": "Something else entirely"}}, actor="dana", source=SOURCE
        )


def test_every_offending_path_is_reported_at_once():
    findings = amendment_findings(
        {
            "signal_registration_id": "reg_a",
            "name": {"en": "One", "fr": "Un"},
            "label": {"en": "Label"},
            "description": {"en": "Words"},
        },
        {"signal_registration_id": "reg_b", "label": {"fr": "Un", "de": "Etikett", "en": None}},
        task_count=2,
    )
    paths = {finding["path"] for finding in findings}
    assert "signal_registration_id" in paths
    assert "label.en" in paths


def test_an_amendment_reports_the_activation_field_rather_than_raising(engine, registered):
    """A rule violation is a finding beside the others, not the first hard refusal."""
    with pytest.raises(FrameworkInUse) as caught:
        engine.amend(registered["id"], {"enabled": True}, actor="dana", source=SOURCE)
    assert "enabled" in str(caught.value)


def test_every_finding_carries_its_meaning_and_a_detail():
    findings = amendment_findings(
        {"signal_registration_id": "reg_a", "label": {"en": "a", "fr": "b"}},
        {"signal_registration_id": "reg_b", "label": {"en": "c"}},
        task_count=0,
    )
    for finding in findings:
        assert set(finding) == {"path", "change", "detail"}
        assert finding["change"] and finding["detail"]


def test_an_amendment_with_no_recognised_field_is_refused_as_unknown(engine, registered):
    with pytest.raises(FrameworkError):
        engine.amend(registered["id"], {"priority": "high"}, actor="dana", source=SOURCE)


def test_an_amendment_of_an_unknown_play_is_refused(engine):
    with pytest.raises(PlayError):
        engine.amend("play_framework_nope", {"label": {"fr": "x"}}, actor="dana", source=SOURCE)


# --------------------------------------------------------------------------- #
# Destroying a Play
# --------------------------------------------------------------------------- #


def test_an_enabled_play_cannot_be_destroyed(engine, live):
    with pytest.raises(FrameworkInUse) as caught:
        engine.destroy(live["id"], actor="dana", source=SOURCE)
    assert "running automation" in str(caught.value)


def test_a_disabled_play_can_be_destroyed(engine, registered):
    result = engine.destroy(registered["id"], actor="dana", source=SOURCE)
    assert result["destroyed"] is True
    assert engine.framework(registered["id"]) is None


def test_a_destroyed_play_is_soft_deleted_so_its_tasks_still_name_it(engine, live, room, store):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    engine.disable(live["id"], actor="dana", source=SOURCE)
    engine.destroy(live["id"], actor="dana", source=SOURCE)

    kept = store.list("play_task", limit=10)
    assert kept and kept[0]["data"]["play_id"] == live["id"]
    assert store.get(live["id"]) is None
    assert store.db.get(live["id"], include_deleted=True) is not None


def test_a_destroyed_play_is_listed_only_when_asked_for(engine, registered):
    engine.destroy(registered["id"], actor="dana", source=SOURCE)
    assert engine.frameworks() == []
    assert len(engine.frameworks(include_destroyed=True)) == 1


def test_destroying_an_unknown_play_is_refused(engine):
    with pytest.raises(PlayError):
        engine.destroy("play_framework_nope", actor="dana", source=SOURCE)


# --------------------------------------------------------------------------- #
# Matching: which Plays a signal fires
# --------------------------------------------------------------------------- #


def test_the_signal_indicator_keys_are_read_from_the_researched_shape():
    assert signal_indicator_keys(signal_payload()) == ["spent_more_than_30s_on_site"]


def test_a_bare_indicator_key_is_accepted():
    assert signal_indicator_keys({"indicators": ["spent_more_than_30s_on_site"]}) == [
        "spent_more_than_30s_on_site"
    ]


def test_a_signal_with_no_indicators_matches_nothing():
    assert signal_indicator_keys({"indicators": []}) == []
    assert signal_indicator_keys({}) == []


def test_a_matching_signal_fires_an_enabled_play():
    decisions = match_plays([{"id": "p1", "indicators": ["k"], "enabled": True}], {"indicators": ["k"]})
    assert decisions[0]["fired"] is True
    assert decisions[0]["reason"] == "matched"
    assert decisions[0]["overlap"] == ["k"]


def test_a_disabled_play_does_not_fire_and_says_which_door_it_stopped_at():
    decisions = match_plays([{"id": "p1", "indicators": ["k"], "enabled": False}], {"indicators": ["k"]})
    assert decisions[0]["fired"] is False
    assert decisions[0]["reason"] == "not_enabled"
    assert "enabled in the Salesloft UI" in decisions[0]["detail"]


def test_a_live_play_with_no_overlap_does_not_fire():
    decisions = match_plays([{"id": "p1", "indicators": ["k"], "enabled": True}], {"indicators": ["other"]})
    assert decisions[0]["reason"] == "no_overlap"


def test_a_signal_with_no_indicators_does_not_fire_anything():
    decisions = match_plays([{"id": "p1", "indicators": ["k"], "enabled": True}], {"indicators": []})
    assert decisions[0]["reason"] == "no_indicators"


def test_a_signal_from_another_registration_does_not_fire_the_play():
    decisions = match_plays(
        [{"id": "p1", "indicators": ["k"], "enabled": True, "signal_registration_id": "reg_a"}],
        {"indicators": ["k"], "registration_id": "reg_b"},
    )
    assert decisions[0]["reason"] == "registration_mismatch"


def test_a_signal_naming_no_registration_matches_on_its_type():
    """Without the fallback an inline signal would match nothing at all."""
    decisions = match_plays(
        [{"id": "p1", "indicators": ["k"], "enabled": True, "signal_registration_id": "document_engagement"}],
        {"indicators": ["k"], "type": "document_engagement"},
    )
    assert decisions[0]["fired"] is True


def test_only_the_indicator_list_participates_in_the_match():
    """Not urgency, not the data, not the seller."""
    loud = match_plays(
        [{"id": "p1", "indicators": ["k"], "enabled": True}],
        {"indicators": ["k"], "urgency": "high", "data": {"anything": 1}},
    )
    quiet = match_plays(
        [{"id": "p1", "indicators": ["k"], "enabled": True}],
        {"indicators": ["k"], "urgency": "low", "data": {}},
    )
    assert loud[0]["fired"] == quiet[0]["fired"] is True


def test_every_reason_match_can_return_is_published():
    for reason in ("matched", "not_enabled", "registration_mismatch", "no_indicators", "no_overlap"):
        assert reason in matching_rules.MATCH_REASONS


def test_nothing_fired_with_no_plays_says_the_registry_is_empty():
    note = matching_rules.note_no_plays()
    assert note["reason"] == "no_plays_registered"


def test_nothing_fired_with_plays_says_the_gates():
    note = matching_rules.note_no_plays([{"id": "p1", "enabled": True}])
    assert note["reason"] == "no_play_fired"
    assert "gate" in note["detail"]


# --------------------------------------------------------------------------- #
# Assignment: User -> Content -> Person -> Account
# --------------------------------------------------------------------------- #


def test_a_user_outranks_every_other_object():
    resolved = resolve_assignment(
        {
            "user_guid": "usr_1",
            "email_tracked_content_id": "etc_1",
            "person_id": "per_1",
            "account_id": "acc_1",
        }
    )
    assert resolved["object"] == "User"
    assert resolved["rule"] == "user_precedence"
    assert resolved["object_id"] == "usr_1"


def test_content_outranks_person_and_account():
    resolved = resolve_assignment(
        {"email_tracked_content_id": "etc_1", "person_id": "per_1", "account_id": "acc_1"}
    )
    assert resolved["object"] == "Content"
    assert resolved["rule"] == "content_precedence"


def test_person_outranks_account():
    resolved = resolve_assignment({"person_id": "per_1", "account_id": "acc_1"})
    assert resolved["object"] == "Person"
    assert resolved["rule"] == "person_precedence"


def test_account_is_last_and_brings_its_own_rule():
    resolved = resolve_assignment({"account_id": "acc_1"}, candidates=roster(), now=NOW)
    assert resolved["object"] == "Account"
    assert resolved["rule"] == "account_engagement_score"
    assert resolved["person_id"] == "per_000042"


def test_the_researched_precedence_is_exactly_user_content_person_account():
    from dsr.plays.vocabulary import ASSIGNMENT_OBJECT, ASSIGNMENT_PRECEDENCE

    assert ASSIGNMENT_PRECEDENCE == ("user_guid", "email_tracked_content_id", "person_id", "account_id")
    assert [ASSIGNMENT_OBJECT[key] for key in ASSIGNMENT_PRECEDENCE] == [
        "User",
        "Content",
        "Person",
        "Account",
    ]


def test_the_considered_list_shows_all_four_and_which_decided():
    resolved = resolve_assignment({"person_id": "per_1", "account_id": "acc_1"})
    considered = resolved["considered"]
    assert len(considered) == 4
    assert [row["object"] for row in considered] == ["User", "Content", "Person", "Account"]
    assert [row["decides"] for row in considered] == [False, False, True, False]


def test_a_signal_naming_no_object_is_reported_not_guessed():
    resolved = resolve_assignment({})
    assert resolved["assigned"] is False
    assert resolved["object"] is None
    assert resolved["rule"] == "no_object"
    assert "User, Content, Person, Account" in resolved["reason"]


def test_a_non_object_attribution_is_refused():
    with pytest.raises(DispatchError):
        resolve_assignment("per_1")


def test_a_user_named_by_the_signal_is_the_seller_with_no_extra_data():
    resolved = resolve_assignment({"user_guid": "usr_1042"})
    assert resolved["assigned"] is True
    assert resolved["seller"] == "usr_1042"
    assert resolved["person_id"] is None


def test_a_named_object_this_product_does_not_hold_is_reported_unassigned():
    """The Person, Account and Content records live in the vendor's platform."""
    resolved = resolve_assignment({"person_id": "per_1"})
    assert resolved["object"] == "Person"
    assert resolved["assigned"] is False
    assert resolved["rule"] == "person_precedence"
    assert "no record of the object" in resolved["reason"]
    assert "subjects" in resolved["reason"]


def test_a_supplied_subject_resolves_the_person_and_the_seller():
    resolved = resolve_assignment(
        {"person_id": "per_1"}, subjects={"per_1": {"person_id": "per_1", "seller": "dana"}}
    )
    assert resolved["assigned"] is True
    assert resolved["seller"] == "dana"
    assert resolved["person_id"] == "per_1"


def test_every_rule_the_resolver_can_return_is_published():
    for reason in (
        "user_precedence",
        "content_precedence",
        "person_precedence",
        "account_engagement_score",
        "account_last_contact",
        "account_no_candidates",
        "no_object",
        "object_unresolved",
    ):
        assert reason in ASSIGNMENT_RULES


# --------------------------------------------------------------------------- #
# The Account fallback, in the research's own words
# --------------------------------------------------------------------------- #


def test_the_most_engaged_person_in_the_window_wins():
    candidates = [
        {"person_id": "per_low", "engagement_score": 10, "engaged_at": ago(60), "seller": "sam"},
        {"person_id": "per_high", "engagement_score": 91, "engaged_at": ago(600), "seller": "dana"},
    ]
    resolved = resolve_assignment({"account_id": "acc_1"}, candidates=candidates, now=NOW)
    assert resolved["person_id"] == "per_high"
    assert resolved["seller"] == "dana"


def test_engagement_older_than_the_window_does_not_count():
    stale = (NOW - timedelta(days=ENGAGEMENT_WINDOW_DAYS + 1)).isoformat()
    candidates = [
        {"person_id": "per_stale", "engagement_score": 99, "engaged_at": stale, "seller": "sam"},
        {"person_id": "per_fresh", "engagement_score": 5, "engaged_at": ago(60), "seller": "dana"},
    ]
    resolved = resolve_assignment({"account_id": "acc_1"}, candidates=candidates, now=NOW)
    assert resolved["person_id"] == "per_fresh"


def test_engagement_inside_the_window_on_its_last_day_counts():
    edge = (NOW - timedelta(days=ENGAGEMENT_WINDOW_DAYS, minutes=-1)).isoformat()
    candidates = [{"person_id": "per_edge", "engagement_score": 7, "engaged_at": edge, "seller": "dana"}]
    resolved = resolve_assignment({"account_id": "acc_1"}, candidates=candidates, now=NOW)
    assert resolved["person_id"] == "per_edge"


def test_a_tie_is_broken_on_the_more_recent_engagement_and_says_so():
    candidates = [
        {"person_id": "per_old", "engagement_score": 50, "engaged_at": ago(5000), "seller": "sam"},
        {"person_id": "per_new", "engagement_score": 50, "engaged_at": ago(60), "seller": "dana"},
    ]
    resolved = resolve_assignment({"account_id": "acc_1"}, candidates=candidates, now=NOW)
    assert resolved["person_id"] == "per_new"
    assert any(note["code"] == "engagement_score_tie" for note in resolved["notes"])


def test_an_unscored_candidate_inside_the_window_is_skipped_and_reported():
    """The research names a score and does not publish a formula, so none is invented."""
    candidates = [
        {"person_id": "per_unscored", "engaged_at": ago(60), "seller": "sam"},
        {"person_id": "per_scored", "engagement_score": 3, "engaged_at": ago(120), "seller": "dana"},
    ]
    resolved = resolve_assignment({"account_id": "acc_1"}, candidates=candidates, now=NOW)
    assert resolved["person_id"] == "per_scored"
    assert any(note["code"] == "no_engagement_score" for note in resolved["notes"])


def test_with_no_engagement_the_last_person_the_account_owner_contacted_wins():
    """"If there is no engagement, relate the task to the last person whose most
    recent contact was with the Account Owner"."""
    candidates = [
        {
            "person_id": "per_earlier",
            "last_contact_at": (NOW - timedelta(days=20)).isoformat(),
            "contact_was_with_account_owner": True,
            "seller": "sam",
        },
        {
            "person_id": "per_later",
            "last_contact_at": (NOW - timedelta(days=2)).isoformat(),
            "contact_was_with_account_owner": True,
            "seller": "dana",
        },
    ]
    resolved = resolve_assignment({"account_id": "acc_1"}, candidates=candidates, now=NOW)
    assert resolved["rule"] == "account_last_contact"
    assert resolved["person_id"] == "per_later"


def test_a_person_the_account_owner_did_not_contact_is_not_the_fallback():
    candidates = [
        {
            "person_id": "per_never",
            "last_contact_at": (NOW - timedelta(days=2)).isoformat(),
            "contact_was_with_account_owner": False,
            "seller": "sam",
        }
    ]
    resolved = resolve_assignment({"account_id": "acc_1"}, candidates=candidates, now=NOW)
    assert resolved["rule"] == "account_no_candidates"
    assert resolved["assigned"] is False


def test_an_account_with_nothing_to_fall_back_on_creates_an_unassigned_task():
    resolved = resolve_assignment({"account_id": "acc_1"}, candidates=[], now=NOW)
    assert resolved["rule"] == "account_no_candidates"
    assert resolved["person_id"] is None
    assert "both halves" in resolved["reason"]


def test_a_candidate_of_the_wrong_type_is_reported_rather_than_crashing():
    resolved = resolve_assignment({"account_id": "acc_1"}, candidates=["nope"], now=NOW)
    assert resolved["rule"] == "account_no_candidates"
    assert any(note["code"] == "candidate_not_an_object" for note in resolved["notes"])


def test_the_window_start_is_thirty_days_before_the_moment():
    from dsr.plays.assignment import window_start

    assert window_start(NOW) == NOW - timedelta(days=30)


def test_the_assignment_description_publishes_the_researched_fallback():
    described = describe_assignment()
    assert "Highest Buyer Engagement Score" in described["account_fallback"][0]
    assert "Account Owner" in described["account_fallback"][1]
    assert "supplied, not computed" in described["buyer_engagement_score"]


# --------------------------------------------------------------------------- #
# The one-off task
# --------------------------------------------------------------------------- #


def test_a_matching_signal_creates_exactly_one_task(engine, live, room):
    result = engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)

    assert result["created_count"] == 1
    task = result["created"][0]
    assert task["task_type"] == "call"
    assert task["state"] == "open"
    assert task["one_off"] is True


def test_the_task_records_that_no_human_was_in_the_loop(engine, live, room):
    result = engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    assert "no human in the loop" in result["created"][0]["room_note"]
    assert result["automation_note"] == AUTOMATION_NOTE


def test_the_task_carries_the_renders_researched_subject(engine, live, room):
    result = engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    assert result["created"][0]["subject"] == "Follow up with Priya on the security pack"


def test_the_task_keeps_the_unrendered_subject_too(engine, live, room):
    result = engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    task = result["created"][0]
    assert task["attributes"]["task_subject"] == "Follow up with {name} on the security pack"
    assert task["subject"] != task["attributes"]["task_subject"]


def test_a_missing_name_leaves_a_visible_field_rather_than_a_blank(engine, live, room):
    result = engine.dispatch(
        {"signal": signal_payload(fields={})}, room_id=room["id"], actor="dana", source=SOURCE
    )
    assert "{name}" in result["created"][0]["subject"]


def test_a_subject_with_no_field_renders_as_it_is():
    assert render_subject({"task_subject": "Call them today"}) == "Call them today"
    assert render_subject({}) == ""


def test_the_task_carries_its_resolved_assignment(engine, live, room):
    result = engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    assignment = result["created"][0]["assignment"]
    assert assignment["object"] == "User"
    assert assignment["seller"] == "usr_1042"
    assert assignment["rule"] == "user_precedence"


def test_the_reminder_hours_travel_to_the_task(engine, registered, registration, room):
    engine.enable(registered["id"], actor="dana", source=SOURCE)
    result = engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    assert result["created"][0]["reminder_hours"] == 4


def test_an_email_play_creates_an_email_task(engine, registration, room):
    play = engine.register(email_play_payload(), actor="dana", source=SOURCE)["play"]
    engine.enable(play["id"], actor="dana", source=SOURCE)
    result = engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)

    task = result["created"][0]
    assert task["task_type"] == "email"
    assert task["attributes"]["email_template"] == "implementation_notes"
    assert task["routable"] is True


def test_a_cadence_play_with_no_cadence_creates_an_unroutable_task_and_says_why(
    engine, registration, room
):
    """"Add Person to a Cadence" has nowhere to add anyone, and the task says so."""
    play = engine.register(
        play_payload(attributes={"task_type": "add-to-cadence", "task_subject": "Keep talking to {name}"}),
        actor="dana",
        source=SOURCE,
    )["play"]
    engine.enable(play["id"], actor="dana", source=SOURCE)
    result = engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)

    task = result["created"][0]
    assert task["routable"] is False
    assert task["missing"][0]["field"] == "attributes.cadence_id"
    assert "no cadence" in task["missing"][0]["detail"].lower()


def test_a_cadence_play_with_a_cadence_is_routable(engine, registration, room):
    play = engine.register(
        play_payload(
            attributes={
                "task_type": "add-to-cadence",
                "task_subject": "Keep talking",
                "cadence_id": "cad_1",
            }
        ),
        actor="dana",
        source=SOURCE,
    )["play"]
    engine.enable(play["id"], actor="dana", source=SOURCE)
    result = engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    assert result["created"][0]["routable"] is True


def test_missing_for_names_what_an_email_task_lacks():
    missing = missing_for("email", {"email_subject": "Hi"})
    assert missing[0]["field"] == "attributes.email_template"


def test_a_call_needs_nothing_further():
    assert missing_for("call", {"task_subject": "Call"}) == []


def test_build_task_keeps_the_triggered_indicators():
    play = {"id": "p1", "label": {"en": "Call"}, "attributes": {"task_type": "call", "task_subject": "Call"}}
    task = build_task(
        play,
        signal_payload(),
        assignment={"assigned": True, "rule": "user_precedence"},
        room_id="room_1",
        occurred_at=ago(0),
        signal_key="sig_1",
        triggered_by=["spent_more_than_30s_on_site"],
        fields={"name": "Priya"},
    )
    assert task["triggered_by"] == ["spent_more_than_30s_on_site"]
    assert task["play_label"] == "Call"


def test_a_task_records_only_the_indicator_that_fired_its_own_play(engine, registration, room):
    """A signal can match two Plays on two indicators; neither task may claim both."""
    email = engine.register(email_play_payload(), actor="dana", source=SOURCE)["play"]
    call = engine.register(play_payload(), actor="dana", source=SOURCE)["play"]
    engine.amend(
        call["id"],
        {"indicators": ["spent_more_than_30s_on_site", "watched_more_than_75_percent"]},
        actor="dana",
        source=SOURCE,
    )
    engine.enable(email["id"], actor="dana", source=SOURCE)
    engine.enable(call["id"], actor="dana", source=SOURCE)

    result = engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    for task in result["created"]:
        assert task["triggered_by"] == ["spent_more_than_30s_on_site"]


# --------------------------------------------------------------------------- #
# One-off: a repeated signal does not create a second task
# --------------------------------------------------------------------------- #


def test_a_repeated_signal_reports_the_task_that_exists(engine, live, room):
    key = uuid4()
    first = engine.dispatch(
        {"signal": signal_payload(idempotency_key=key)}, room_id=room["id"], actor="dana", source=SOURCE
    )
    second = engine.dispatch(
        {"signal": signal_payload(idempotency_key=key)}, room_id=room["id"], actor="dana", source=SOURCE
    )

    assert first["created_count"] == 1
    assert second["created_count"] == 0
    assert second["already_dispatched"][0]["task_id"] == first["created"][0]["id"]


def test_a_repeat_leaves_one_row_not_two(engine, live, room):
    key = uuid4()
    for _ in range(3):
        engine.dispatch(
            {"signal": signal_payload(idempotency_key=key)}, room_id=room["id"], actor="dana", source=SOURCE
        )
    assert len(engine.generated_tasks(room_id=room["id"])) == 1


def test_the_dropped_repeats_are_counted_on_the_task(engine, live, room):
    key = uuid4()
    for _ in range(4):
        engine.dispatch(
            {"signal": signal_payload(idempotency_key=key)}, room_id=room["id"], actor="dana", source=SOURCE
        )
    assert engine.generated_tasks(room_id=room["id"])[0]["duplicate_attempts"] == 3


def test_two_different_signals_both_fire_the_same_play(engine, live, room):
    for _ in range(2):
        engine.dispatch(
            {"signal": signal_payload(idempotency_key=uuid4())},
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    assert len(engine.generated_tasks(room_id=room["id"])) == 2


def test_a_stored_signal_can_be_dispatched_by_id(engine, live, room, store):
    stored = store.create(
        "intent_signal", signal_payload(), room_id=room["id"], actor="dana", source=SOURCE
    )
    result = engine.dispatch(
        {"signal_id": stored["id"]}, room_id=room["id"], actor="dana", source=SOURCE
    )
    assert result["created_count"] == 1
    assert result["signal_id"] == stored["id"]


def test_a_stored_signal_dispatched_twice_is_still_one_off(engine, live, room, store):
    stored = store.create(
        "intent_signal", signal_payload(), room_id=room["id"], actor="dana", source=SOURCE
    )
    for _ in range(2):
        engine.dispatch({"signal_id": stored["id"]}, room_id=room["id"], actor="dana", source=SOURCE)
    assert len(engine.generated_tasks(room_id=room["id"])) == 1


def test_an_unreadable_signal_id_is_refused(engine, live, room):
    with pytest.raises(UnknownSignal):
        engine.dispatch({"signal_id": "intent_signal_nope"}, room_id=room["id"], actor="dana", source=SOURCE)


def test_a_dispatch_naming_no_signal_at_all_is_refused(engine, live, room):
    with pytest.raises(PlayError):
        engine.dispatch({}, room_id=room["id"], actor="dana", source=SOURCE)


def test_a_signal_with_no_identity_is_refused(engine, live, room):
    """Without an id there is no way to tell a new signal from a repeat."""
    with pytest.raises(PlayError) as caught:
        engine.dispatch(
            {"signal": {"indicators": ["spent_more_than_30s_on_site"]}},
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    assert "one-off" in str(caught.value)


def test_a_signal_that_fires_nothing_raises_nothing_and_says_why(engine, live, room):
    result = engine.dispatch(
        {
            "signal": signal_payload(
                indicators=[{"key": "watched_more_than_75_percent", "metadata": {}}]
            )
        },
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert result["created_count"] == 0
    assert result["outcome"] == "no_play_fired"
    assert result["decisions"][0]["reason"] == "no_overlap"


def test_a_dispatch_against_an_empty_registry_says_the_registry_is_empty(engine, room, registration):
    result = engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    assert result["outcome"] == "no_plays_registered"
    assert result["created_count"] == 0


def test_two_plays_on_one_registration_both_fire_one_signal(engine, registration, room):
    call = engine.register(play_payload(), actor="dana", source=SOURCE)["play"]
    email = engine.register(email_play_payload(), actor="dana", source=SOURCE)["play"]
    engine.enable(call["id"], actor="dana", source=SOURCE)
    engine.enable(email["id"], actor="dana", source=SOURCE)

    result = engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    assert result["created_count"] == 2
    assert {task["task_type"] for task in result["created"]} == {"call", "email"}


def test_a_room_with_a_different_registration_fires_nothing(engine, live, room):
    """A task belongs to the room its signal was raised in."""
    result = engine.dispatch(
        {"signal": signal_payload(registration_id="signal_registration_elsewhere")},
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert result["decisions"] == [] or result["created_count"] == 0


# --------------------------------------------------------------------------- #
# Outcome events
# --------------------------------------------------------------------------- #


def test_dispatch_records_a_task_created_event_that_is_pending_not_delivered(engine, live, room):
    """The task was created; whether the webhook arrived is a separate fact."""
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    recorded = engine.outcome_events(room_id=room["id"])

    assert [event["event_type"] for event in recorded] == ["task_created"]
    assert recorded[0]["state"] == "pending"
    assert recorded[0]["attempts"] == []
    assert recorded[0]["attempt_count"] == 0


def test_an_event_that_no_one_has_delivered_is_pending():
    """A synthetic success would report "delivered" for a webhook nobody has sent."""
    state = delivery([])
    assert state["state"] == "pending"
    assert state["attempt_count"] == 0


def test_the_delivery_summary_does_not_overwrite_the_attempt_history():
    """``attempt_count`` and ``attempts`` are different things, and must stay so."""
    built = event_rules.build_event(
        "task_created",
        task={"id": "task_1", "play_id": "play_1", "subject": "Call"},
        room_id="room_1",
        occurred_at=ago(0),
    )
    assert built["attempts"] == []
    assert built["attempt_count"] == 0
    assert delivery([{"at": ago(0), "ok": True}])["attempt_count"] == 1


def test_an_event_payload_carrying_a_delivery_outcome_is_refused():
    """A delivery result belongs in the attempt history, not in the event body."""
    with pytest.raises(EventError):
        event_rules.build_event(
            "task_created",
            task={"id": "task_1"},
            room_id="room_1",
            occurred_at=ago(0),
            payload={"ok": True},
        )


def test_completing_a_task_records_the_outcome_events(engine, live, room):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    task = engine.generated_tasks(room_id=room["id"])[0]
    result = engine.complete_task(
        task["id"], {"note": "Called."}, room_id=room["id"], actor="dana", source=SOURCE
    )

    assert result["outcome"] == "completed"
    assert result["task"]["state"] == "completed"
    assert [event["event_type"] for event in result["events"]] == ["task_completed"]


def test_the_outcome_events_a_completion_returns_are_addressable(engine, live, room):
    """A client must be able to record a delivery against the event it was handed."""
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    task = engine.generated_tasks(room_id=room["id"])[0]
    result = engine.complete_task(task["id"], None, room_id=room["id"], actor="dana", source=SOURCE)

    event = result["events"][0]
    assert event["id"].startswith("play_event_")
    assert engine.record_attempt(
        event["id"], {"status_code": 200}, room_id=room["id"], actor="dana", source=SOURCE
    )["delivered"] is True


def test_a_cadence_step_records_step_created_and_success_created(engine, registration, room):
    play = engine.register(
        play_payload(
            attributes={
                "task_type": "add-to-cadence",
                "task_subject": "Keep talking",
                "cadence_id": "cad_1",
            }
        ),
        actor="dana",
        source=SOURCE,
    )["play"]
    engine.enable(play["id"], actor="dana", source=SOURCE)
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    task = engine.generated_tasks(room_id=room["id"])[0]
    result = engine.complete_task(task["id"], None, room_id=room["id"], actor="dana", source=SOURCE)

    assert "step_created" in [event["event_type"] for event in result["events"]]
    assert "success_created" in [event["event_type"] for event in result["events"]]


def test_the_task_is_not_created_twice_by_completion(engine, live, room):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    task = engine.generated_tasks(room_id=room["id"])[0]
    engine.complete_task(task["id"], None, room_id=room["id"], actor="dana", source=SOURCE)
    assert [e["event_type"] for e in engine.outcome_events(room_id=room["id"])].count("task_created") == 1


def test_completing_twice_writes_nothing(engine, live, room, store):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    task = engine.generated_tasks(room_id=room["id"])[0]
    engine.complete_task(task["id"], None, room_id=room["id"], actor="dana", source=SOURCE)
    before = store.get(task["id"])["revision"]

    result = engine.complete_task(task["id"], None, room_id=room["id"], actor="dana", source=SOURCE)
    assert result["outcome"] == "already_completed"
    assert store.get(task["id"])["revision"] == before


def test_completing_a_task_in_another_room_is_refused(engine, live, room, store):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    task = engine.generated_tasks(room_id=room["id"])[0]
    other = store.create("room", {"name": "Elsewhere"}, actor="dana", source=SOURCE)
    with pytest.raises(PlayError):
        engine.complete_task(task["id"], None, room_id=other["id"], actor="dana", source=SOURCE)


def test_a_completion_note_must_be_a_string(engine, live, room):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    task = engine.generated_tasks(room_id=room["id"])[0]
    with pytest.raises(PlayError):
        engine.complete_task(task["id"], {"note": 7}, room_id=room["id"], actor="dana", source=SOURCE)


def test_an_event_carries_a_researched_meaning():
    assert "no human in the loop" in event_rules.EVENT_TRIGGERS["task_created"]
    assert "seller acted" in event_rules.EVENT_TRIGGERS["task_completed"]


# --------------------------------------------------------------------------- #
# The webhook retry rule
# --------------------------------------------------------------------------- #


def test_a_delivery_with_no_attempt_is_pending():
    state = delivery([])
    assert state["state"] == "pending"
    assert state["retries_remaining"] == WEBHOOK_RETRY_ATTEMPTS


def test_a_successful_first_attempt_is_delivered():
    state = delivery([{"at": ago(0), "ok": True}])
    assert state["state"] == "delivered"
    assert state["next_attempt_at"] is None


def test_one_failure_is_retrying_with_three_left():
    state = delivery([{"at": ago(0), "ok": False}])
    assert state["state"] == "retrying"
    assert state["retries_remaining"] == 3


def test_three_failures_still_have_one_retry_left():
    history = [{"at": ago(0), "ok": False} for _ in range(3)]
    assert delivery(history)["retries_remaining"] == 1


def test_four_failures_is_marked_failed_and_stops():
    """"retried three additional times ... before being marked as failed"."""
    history = [{"at": ago(0), "ok": False} for _ in range(WEBHOOK_RETRY_ATTEMPTS + 1)]
    state = delivery(history)
    assert state["state"] == "failed"
    assert state["retries_remaining"] == 0
    assert state["next_attempt_at"] is None


def test_a_success_after_failures_is_delivered_and_says_so():
    history = [
        {"at": ago(60), "ok": False},
        {"at": ago(45), "ok": False},
        {"at": ago(0), "ok": True},
    ]
    state = delivery(history)
    assert state["state"] == "delivered"
    assert state["failures"] == 2
    assert "earlier attempt" in state["detail"]


def test_the_next_attempt_is_fifteen_seconds_after_the_last():
    assert next_attempt_at(ago(0)) == (NOW + timedelta(seconds=WEBHOOK_RETRY_SPACING_SECONDS)).isoformat(
        timespec="milliseconds"
    )


def test_a_pending_delivery_is_due_immediately():
    assert event_rules.due(delivery([]), NOW) is True


def test_a_retry_is_not_due_before_its_fifteen_seconds():
    state = delivery([{"at": ago(0), "ok": False}])
    assert event_rules.due(state, NOW) is False
    assert event_rules.due(state, NOW + timedelta(seconds=WEBHOOK_RETRY_SPACING_SECONDS)) is True


def test_a_finished_delivery_is_never_due():
    assert event_rules.due(delivery([{"at": ago(0), "ok": True}]), NOW + timedelta(days=1)) is False


def test_a_2xx_status_is_a_delivery():
    ok, _ = event_rules.attempt_ok({"status_code": 204})
    assert ok is True


@pytest.mark.parametrize("status", [199, 300, 400, 404, 500, 503])
def test_a_non_2xx_status_is_not_a_delivery(status):
    ok, _ = event_rules.attempt_ok({"status_code": status})
    assert ok is False


def test_a_body_claiming_ok_against_a_500_is_refused_rather_than_believed():
    with pytest.raises(EventError) as caught:
        event_rules.attempt_ok({"ok": True, "status_code": 500})
    assert "status code is what decides" in str(caught.value)


def test_an_attempt_with_neither_ok_nor_a_status_is_refused():
    with pytest.raises(EventError):
        event_rules.attempt_ok({})


def test_a_non_boolean_ok_is_refused():
    with pytest.raises(EventError):
        event_rules.attempt_ok({"ok": "yes"})


def test_a_non_integer_status_is_refused():
    with pytest.raises(EventError):
        event_rules.attempt_ok({"status_code": "200"})


def test_a_failing_delivery_is_retried_and_the_retry_is_recorded(engine, live, room, clock):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    event = engine.outcome_events(room_id=room["id"])[0]
    engine.record_attempt(
        event["id"], {"status_code": 503}, room_id=room["id"], actor="dana", source=SOURCE
    )
    clock.advance(WEBHOOK_RETRY_SPACING_SECONDS)
    result = engine.record_attempt(
        event["id"], {"status_code": 200}, room_id=room["id"], actor="dana", source=SOURCE
    )

    assert result["delivered"] is True
    assert result["event"]["state"] == "delivered"
    assert len(result["event"]["attempts"]) == 2
    assert result["event"]["failures"] == 1


def test_an_early_retry_is_refused_and_names_the_time_it_is_due(engine, live, room):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    event = engine.outcome_events(room_id=room["id"])[0]
    engine.record_attempt(
        event["id"], {"status_code": 503}, room_id=room["id"], actor="dana", source=SOURCE
    )
    with pytest.raises(PlayError) as caught:
        engine.record_attempt(
            event["id"], {"status_code": 503}, room_id=room["id"], actor="dana", source=SOURCE
        )
    assert "not due until" in str(caught.value)


def test_a_delivery_marked_failed_takes_no_further_attempts(engine, live, room, clock):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    event = engine.outcome_events(room_id=room["id"])[0]
    for _ in range(WEBHOOK_RETRY_ATTEMPTS + 1):
        clock.advance(WEBHOOK_RETRY_SPACING_SECONDS)
        try:
            engine.record_attempt(
                event["id"], {"status_code": 500}, room_id=room["id"], actor="dana", source=SOURCE
            )
        except PlayError:
            break
    with pytest.raises(PlayError) as caught:
        engine.record_attempt(
            event["id"], {"status_code": 500}, room_id=room["id"], actor="dana", source=SOURCE
        )
    assert "no further attempts" in str(caught.value)


def test_a_delivered_delivery_takes_no_further_attempts(engine, live, room):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    event = engine.outcome_events(room_id=room["id"])[0]
    engine.record_attempt(
        event["id"], {"status_code": 200}, room_id=room["id"], actor="dana", source=SOURCE
    )
    with pytest.raises(DeliveryConflict) as caught:
        engine.record_attempt(
            event["id"], {"status_code": 503}, room_id=room["id"], actor="dana", source=SOURCE
        )
    assert "delivered" in str(caught.value)


def test_recording_an_attempt_on_an_unknown_event_is_refused(engine, room):
    with pytest.raises(PlayError):
        engine.record_attempt(
            "play_event_nope", {"ok": True}, room_id=room["id"], actor="dana", source=SOURCE
        )


def test_an_event_in_another_room_cannot_have_an_attempt_recorded(engine, live, room, store):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    event = engine.outcome_events(room_id=room["id"])[0]
    other = store.create("room", {"name": "Elsewhere"}, actor="dana", source=SOURCE)
    with pytest.raises(PlayError):
        engine.record_attempt(event["id"], {"ok": True}, room_id=other["id"], actor="dana", source=SOURCE)


# --------------------------------------------------------------------------- #
# Webhook subscriptions
# --------------------------------------------------------------------------- #


def test_a_subscription_requires_event_types():
    with pytest.raises(EventError) as caught:
        normalise_subscription({"target_url": "https://hooks.example.invalid/dsr"})
    assert "event_types" in str(caught.value)


def test_a_subscription_accepts_the_four_researched_event_types():
    data = normalise_subscription({"event_types": list(PLAY_EVENT_TYPES)})
    assert data["event_types"] == list(PLAY_EVENT_TYPES)


def test_a_subscription_carries_the_researched_retry_policy():
    data = normalise_subscription({"event_types": ["task_created"]})
    assert data["retry_policy"] == {
        "additional_attempts": WEBHOOK_RETRY_ATTEMPTS,
        "spacing_seconds": WEBHOOK_RETRY_SPACING_SECONDS,
    }


def test_a_subscription_with_no_target_is_warned_about_rather_than_refused(engine):
    """The research publishes no field list for a subscription, so a target is optional."""
    result = engine.subscribe({"event_types": ["task_created"]}, actor="dana", source=SOURCE)
    assert [warning["code"] for warning in result["warnings"]] == ["no_target_url"]


def test_a_subscription_with_a_target_has_no_warnings(engine):
    result = engine.subscribe(
        {"event_types": ["task_created"], "target_url": "https://hooks.example.invalid/dsr"},
        actor="dana",
        source=SOURCE,
    )
    assert result["warnings"] == []


def test_an_unknown_subscription_field_is_refused():
    with pytest.raises(EventError) as caught:
        normalise_subscription({"event_types": ["task_created"], "secret": "s3cr3t"})
    assert "secret" in str(caught.value)


def test_a_subscription_can_be_removed(engine):
    created = engine.subscribe({"event_types": ["task_created"]}, actor="dana", source=SOURCE)["subscription"]
    removed = engine.unsubscribe(created["id"], actor="dana", source=SOURCE)
    assert removed["unsubscribed"] is True
    assert engine.subscriptions() == []


def test_removing_an_unknown_subscription_is_refused(engine):
    with pytest.raises(PlayError):
        engine.unsubscribe("play_webhook_nope", actor="dana", source=SOURCE)


# --------------------------------------------------------------------------- #
# Reading, filtering, and the summary
# --------------------------------------------------------------------------- #


def test_the_registry_can_be_filtered_by_task_type_through_the_dynamic_index(engine, registration):
    engine.register(play_payload(), actor="dana", source=SOURCE)
    engine.register(email_play_payload(), actor="dana", source=SOURCE)
    assert len(engine.frameworks(task_type="email")) == 1


def test_the_registry_can_be_filtered_by_a_nested_attribute(engine, registration):
    engine.register(
        play_payload(attributes={"task_type": "call", "task_subject": "Call"}),
        actor="dana",
        source=SOURCE,
    )
    assert len(engine.frameworks(task_type="call")) == 1


def test_the_registry_can_be_filtered_by_enablement(engine, registration):
    live = engine.register(play_payload(), actor="dana", source=SOURCE)["play"]
    engine.register(email_play_payload(), actor="dana", source=SOURCE)
    engine.enable(live["id"], actor="dana", source=SOURCE)

    assert len(engine.frameworks(enabled=True)) == 1
    assert len(engine.frameworks(enabled=False)) == 1


def test_the_registry_can_be_filtered_by_registration(engine, registration):
    engine.register(play_payload(), actor="dana", source=SOURCE)
    assert len(engine.frameworks(signal_registration_id=REGISTRATION_ID)) == 1
    assert engine.frameworks(signal_registration_id="signal_registration_other") == []


def test_the_registry_can_be_filtered_by_an_indicator(engine, registered):
    assert len(engine.frameworks(indicator="spent_more_than_30s_on_site")) == 1
    assert engine.frameworks(indicator="nothing") == []


def test_an_unknown_task_type_filter_is_refused(engine):
    with pytest.raises(FrameworkError):
        engine.frameworks(task_type="telepathy")


def test_tasks_can_be_filtered_by_state_and_assignment(engine, live, room):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    engine.dispatch({"signal": signal_payload(attribution={})}, room_id=room["id"], actor="dana", source=SOURCE)

    assert len(engine.generated_tasks(room_id=room["id"], state="open")) == 2
    assert len(engine.generated_tasks(room_id=room["id"], assigned=False)) == 1
    assert len(engine.generated_tasks(room_id=room["id"], assigned=True)) == 1


def test_tasks_can_be_filtered_by_play(engine, live, room):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    assert len(engine.generated_tasks(room_id=room["id"], play_id=live["id"])) == 1


def test_tasks_are_scoped_to_their_room(engine, live, room, store):
    other = store.create("room", {"name": "Elsewhere"}, actor="dana", source=SOURCE)
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    assert engine.generated_tasks(room_id=other["id"]) == []


def test_events_can_be_filtered_by_delivery_state(engine, live, room):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    assert len(engine.outcome_events(room_id=room["id"], delivery_state="pending")) == 1
    assert engine.outcome_events(room_id=room["id"], delivery_state="failed") == []


def test_an_unknown_delivery_state_filter_is_refused(engine):
    with pytest.raises(PlayError):
        engine.outcome_events(delivery_state="confused")


def test_the_summary_reports_the_automation_and_the_activations(engine, live, registration, room):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    engine.register(email_play_payload(), actor="dana", source=SOURCE)
    summary = engine.summary(room_id=room["id"])

    assert summary["tasks_from_automation"] == 1
    assert summary["live_plays"] == 1
    assert summary["registered_not_enabled"] == 1
    assert summary["events"] == 1
    assert summary["automation_note"] == AUTOMATION_NOTE
    assert summary["activation_path"] == ACTIVATION_PATH


def test_the_summary_counts_by_task_type_and_by_event_type(engine, registration, room):
    call = engine.register(play_payload(), actor="dana", source=SOURCE)["play"]
    email = engine.register(email_play_payload(), actor="dana", source=SOURCE)["play"]
    engine.enable(call["id"], actor="dana", source=SOURCE)
    engine.enable(email["id"], actor="dana", source=SOURCE)
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)

    summary = engine.summary(room_id=room["id"])
    assert {row["task_type"]: row["count"] for row in summary["by_task_type"]} == {"call": 1, "email": 1}
    assert {row["event_type"]: row["count"] for row in summary["by_event_type"]} == {"task_created": 2}


def test_the_summary_counts_the_unassigned_and_unroutable(engine, registration, room):
    play = engine.register(
        play_payload(attributes={"task_type": "add-to-cadence", "task_subject": "Keep talking"}),
        actor="dana",
        source=SOURCE,
    )["play"]
    engine.enable(play["id"], actor="dana", source=SOURCE)
    engine.dispatch(
        {"signal": signal_payload(attribution={})}, room_id=room["id"], actor="dana", source=SOURCE
    )
    summary = engine.summary(room_id=room["id"])
    assert summary["unassigned_tasks"] == 1
    assert summary["unroutable_tasks"] == 1


def test_a_task_of_another_collection_is_not_a_task(engine, room):
    other = engine.store.create("play_event", {"event_type": "task_created"}, room_id=room["id"])
    assert engine.task(other["id"]) is None


# --------------------------------------------------------------------------- #
# The inference register
# --------------------------------------------------------------------------- #


def test_the_sourced_quote_is_the_researched_request_body():
    from dsr.plays.inferences import SOURCED_QUOTE

    assert SOURCED_QUOTE.startswith("After registering a signal (see #12), register a Play")
    assert "add-to-cadence" in SOURCED_QUOTE


def test_every_inference_is_named_traceable_bounded_and_visible():
    for entry in INFERENCES:
        assert set(entry) >= {"id", "topic", "basis", "value", "why", "change_it", "blast_radius"}
        assert entry["basis"] and entry["why"] and entry["change_it"] and entry["blast_radius"]


def test_the_inference_ids_are_unique():
    ids = [entry["id"] for entry in INFERENCES]
    assert len(ids) == len(set(ids))


def test_the_activation_inference_is_the_headline_and_says_what_it_costs():
    headline = by_id("registration-is-not-activation")
    assert headline["value"]["enabled_on_registration"] is False
    assert "seller's queue" in headline["why"]


def test_the_engagement_score_inference_records_the_researchs_own_gap():
    gap = by_id("buyer-engagement-score-is-supplied")
    assert gap["value"]["computed_here"] is False
    assert "does not document how it is computed" in gap["basis"]


def test_the_gap_in_the_research_corpus_is_what_the_inference_cites():
    raw = (
        Path(__file__).resolve().parents[2] / "docs" / "research" / "raw" / "analytics-intent.md"
    ).read_text(encoding="utf-8")
    assert "Buyer Engagement Score" in raw
    assert "does not document how it is computed" in raw


def test_an_unknown_inference_is_none():
    assert by_id("no-such-inference") is None


def test_the_register_describes_everything_it_publishes():
    from dsr.plays.inferences import describe

    body = describe()
    assert body["count"] == len(INFERENCES)
    assert body["published_values"]["webhook_retry_spacing_seconds"] == 15
    assert body["published_values"]["sourced_attributes"] == list(ATTRIBUTE_KEYS)
    assert body["published_values"]["unsourced_attributes"] == list(UNSOURCED_ATTRIBUTE_KEYS)


# --------------------------------------------------------------------------- #
# The audit trail
# --------------------------------------------------------------------------- #


def test_registering_is_audited_to_the_route_that_served_it(store, engine, registration):
    engine.register(play_payload(), actor="dana", source=SOURCE)
    entries = store.audit(collection="play_framework")
    assert [entry["action"] for entry in entries] == ["insert"]
    assert entries[0]["source"] == SOURCE


def test_a_task_is_audited_against_the_dispatch_route(store, engine, live, room):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    entries = store.audit(collection="play_task")
    assert [entry["action"] for entry in entries] == ["insert"]
    assert entries[0]["source"] == SOURCE


def test_the_duplicate_increment_is_audited_as_an_update_against_the_dispatch_route(store, engine, live, room):
    key = uuid4()
    for _ in range(2):
        engine.dispatch(
            {"signal": signal_payload(idempotency_key=key)}, room_id=room["id"], actor="dana", source=SOURCE
        )
    entries = store.audit(collection="play_task")
    assert [entry["action"] for entry in entries] == ["update", "insert"]
    assert all(entry["source"] == SOURCE for entry in entries)


def test_enabling_is_audited_to_the_enable_route(store, engine, registered):
    engine.enable(registered["id"], actor="dana", source="POST /api/wf-028/play-frameworks/{play_id}/enable")
    entry = store.audit(collection="play_framework")[0]
    assert entry["action"] == "update"
    assert entry["source"] == "POST /api/wf-028/play-frameworks/{play_id}/enable"


def test_a_write_audits_the_route_template_not_one_requests_url(http, http_registration, http_room):
    """The source is a route, so a reader of the audit log is not reading one request.

    Interpolating the concrete id would make the log read as a list of URLs rather
    than a list of routes, and the house convention across the merged features is
    the templated spelling.
    """
    play = http.post(
        f"{PREFIX}/play-frameworks", json=play_for(http_registration), params={"actor": "dana"}
    ).json()["play"]
    http.post(f"{PREFIX}/play-frameworks/{play['id']}/enable", params={"actor": "dana"})
    http.post(f"{PREFIX}/play-frameworks/{play['id']}/disable", params={"actor": "dana"})
    http.post(f"{PREFIX}/play-frameworks/{play['id']}/disable", params={"actor": "dana"})

    sources = {
        entry["source"]
        for entry in http.get("/api/audit", params={"collection": "play_framework"}).json()["entries"]
    }
    assert sources == {
        f"POST {PREFIX}/play-frameworks",
        f"POST {PREFIX}/play-frameworks/{{play_id}}/enable",
        f"POST {PREFIX}/play-frameworks/{{play_id}}/disable",
    }
    assert play["id"] not in "".join(sources)


def test_a_refused_registration_writes_nothing_at_all(store, engine):
    before = len(store.audit(limit=1000))
    with pytest.raises(PlayError):
        engine.register(play_payload(indicators=["undeclared"]), actor="dana", source=SOURCE)
    assert len(store.audit(limit=1000)) == before
    assert engine.frameworks() == []


def test_a_refused_dispatch_writes_nothing_at_all(store, engine, live, room):
    before = len(store.audit(limit=1000))
    with pytest.raises(UnknownSignal):
        engine.dispatch({"signal_id": "nope"}, room_id=room["id"], actor="dana", source=SOURCE)
    assert len(store.audit(limit=1000)) == before


def test_a_refused_amendment_does_not_move_the_revision(store, engine, live, room):
    engine.dispatch({"signal": signal_payload()}, room_id=room["id"], actor="dana", source=SOURCE)
    before = store.get(live["id"])["revision"]
    with pytest.raises(FrameworkInUse):
        engine.amend(live["id"], {"indicators": ["other"]}, actor="dana", source=SOURCE)
    assert store.get(live["id"])["revision"] == before


def test_every_write_method_demands_a_source():
    """A hardcoded source is a defect; this is the guard against one coming back."""
    import inspect

    for name in (
        "register",
        "amend",
        "destroy",
        "enable",
        "disable",
        "dispatch",
        "complete_task",
        "record_attempt",
        "subscribe",
        "unsubscribe",
    ):
        method = getattr(PlayEngine, name)
        assert "source" in inspect.signature(method).parameters, name
        assert inspect.signature(method).parameters["source"].default is inspect.Parameter.empty, name


# --------------------------------------------------------------------------- #
# The HTTP surface, through this feature's own router
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_serves_every_rule(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert [row["value"] for row in body["task_types"]] == list(TASK_TYPES)
    assert body["webhook_retries"]["additional_attempts"] == WEBHOOK_RETRY_ATTEMPTS
    assert body["webhook_retries"]["spacing_seconds"] == WEBHOOK_RETRY_SPACING_SECONDS
    assert body["activation_path"] == ACTIVATION_PATH
    assert body["dynamic_fields"]["supported"] == list(SUPPORTED_DYNAMIC_FIELDS)


def test_the_vocabulary_route_marks_the_unsourced_attribute(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    cadence = next(row for row in body["attributes"] if row["key"] == "cadence_id")
    assert cadence["sourced"] is False
    assert cadence["required_for"] == []


def test_the_vocabulary_route_publishes_the_assignment_precedence(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert [row["object"] for row in body["assignment"]["precedence"]] == [
        "User",
        "Content",
        "Person",
        "Account",
    ]


def test_the_inferences_route_serves_the_register(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(INFERENCES)
    assert body["sourced_quote"].startswith("After registering a signal")


def test_registering_over_http_returns_201_and_a_disabled_play(http, http_registration):
    response = http.post(
        f"{PREFIX}/play-frameworks", json=play_for(http_registration), params={"actor": "dana"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["play"]["enabled"] is False
    assert body["play"]["id"].startswith("play_framework_")


def test_a_registration_against_nothing_is_409_over_http(http):
    response = http.post(
        f"{PREFIX}/play-frameworks", json=play_payload(signal_registration_id="signal_registration_nope")
    )
    assert response.status_code == 409
    assert response.json()["error"] == "signal_registration_not_found"


def test_an_undeclared_trigger_is_409_over_http(http, http_registration):
    response = http.post(
        f"{PREFIX}/play-frameworks",
        json=play_for(http_registration, indicators=["undeclared_indicator"]),
    )
    assert response.status_code == 409


def test_a_malformed_body_is_400_over_http(http, http_registration):
    response = http.post(
        f"{PREFIX}/play-frameworks",
        json=play_for(http_registration, attributes={"task_type": "sms"}),
    )
    assert response.status_code == 400
    assert response.json()["error"] == "play_framework_error"


def test_the_registry_route_reports_live_and_registered_counts(http, http_registration, http_play):
    body = http.get(f"{PREFIX}/play-frameworks").json()
    assert body["count"] == 1
    assert body["live"] == 0
    assert body["registered_not_enabled"] == 1


def test_the_registry_route_reports_a_live_play_after_enabling(http, http_play):
    http.post(f"{PREFIX}/play-frameworks/{http_play['id']}/enable", params={"actor": "dana"})
    body = http.get(f"{PREFIX}/play-frameworks").json()
    assert body["live"] == 1
    assert body["registered_not_enabled"] == 0


def test_reading_one_play_over_http(http, http_registration, http_play):
    body = http.get(f"{PREFIX}/play-frameworks/{http_play['id']}").json()
    assert body["signal_registration_id"] == http_registration["id"]


def test_reading_an_unknown_play_is_404_over_http(http):
    assert http.get(f"{PREFIX}/play-frameworks/play_framework_nope").status_code == 404


def test_enabling_over_http_returns_the_actor_and_the_stamp(http, http_play):
    response = http.post(f"{PREFIX}/play-frameworks/{http_play['id']}/enable", params={"actor": "dana"})
    body = response.json()
    assert response.status_code == 200
    assert body["outcome"] == "enabled"
    assert body["play"]["enabled_by"] == "dana"
    assert body["play"]["enabled_at"]


def test_enabling_twice_over_http_is_a_no_op(http, http_play):
    http.post(f"{PREFIX}/play-frameworks/{http_play['id']}/enable", params={"actor": "dana"})
    response = http.post(f"{PREFIX}/play-frameworks/{http_play['id']}/enable", params={"actor": "dana"})
    assert response.json()["outcome"] == "already_enabled"


def test_disabling_an_unknown_play_is_404_over_http(http):
    assert http.post(f"{PREFIX}/play-frameworks/play_framework_nope/disable").status_code == 404


def test_amending_a_label_over_http_adds_a_locale(http, http_play):
    response = http.patch(
        f"{PREFIX}/play-frameworks/{http_play['id']}", json={"label": {"fr": "Appeler"}}
    )
    assert response.status_code == 200
    assert response.json()["label"]["fr"] == "Appeler"

def test_amending_to_activation_is_409_over_http(http, http_play):
    response = http.patch(f"{PREFIX}/play-frameworks/{http_play['id']}", json={"enabled": True})
    assert response.status_code == 409


def test_destroying_an_enabled_play_is_409_over_http(http, http_play):
    http.post(f"{PREFIX}/play-frameworks/{http_play['id']}/enable", params={"actor": "dana"})
    response = http.delete(f"{PREFIX}/play-frameworks/{http_play['id']}")
    assert response.status_code == 409


def test_disabling_then_destroying_works_over_http(http, http_play):
    http.post(f"{PREFIX}/play-frameworks/{http_play['id']}/disable", params={"actor": "dana"})
    assert http.delete(f"{PREFIX}/play-frameworks/{http_play['id']}").status_code == 200


def test_a_dispatch_over_http_creates_a_task(http, http_registration, http_room, http_play):
    http.post(f"{PREFIX}/play-frameworks/{http_play['id']}/enable", params={"actor": "dana"})
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/dispatch",
        json={"signal": signal_payload(registration_id=http_registration["id"])},
        params={"actor": "dana"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 1
    assert body["created"][0]["task_type"] == "call"


def test_a_dispatch_on_a_disabled_play_creates_nothing_over_http(http, http_registration, http_room, http_play):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/dispatch",
        json={"signal": signal_payload(registration_id=http_registration["id"])},
    )
    assert response.json()["created_count"] == 0
    assert response.json()["decisions"][0]["reason"] == "not_enabled"


def test_a_dispatch_with_an_unreadable_signal_is_409_over_http(http, http_room):
    response = http.post(f"{PREFIX}/rooms/{http_room['id']}/dispatch", json={"signal_id": "nope"})
    assert response.status_code == 409
    assert response.json()["error"] == "signal_not_found"


def test_a_dispatch_naming_nothing_is_400_over_http(http, http_room):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/dispatch", json={}
    )
    assert response.status_code == 400


def test_listing_tasks_over_http_reports_unassigned_and_unroutable(http, http_registration, http_room, http_play):
    http.post(f"{PREFIX}/play-frameworks/{http_play['id']}/enable", params={"actor": "dana"})
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/dispatch",
        json={"signal": signal_payload(registration_id=http_registration["id"], attribution={})},
    )
    body = http.get(f"{PREFIX}/rooms/{http_room['id']}/tasks").json()
    assert body["count"] == 1
    assert body["unassigned"] == 1


def test_reading_a_task_carries_its_events_over_http(http, http_registration, http_room, http_play):
    http.post(f"{PREFIX}/play-frameworks/{http_play['id']}/enable", params={"actor": "dana"})
    dispatched = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/dispatch",
        json={"signal": signal_payload(registration_id=http_registration["id"])},
    ).json()
    task_id = dispatched["created"][0]["id"]

    body = http.get(f"{PREFIX}/rooms/{http_room['id']}/tasks/{task_id}").json()
    assert [event["event_type"] for event in body["events"]] == ["task_created"]


def test_reading_a_task_of_another_room_is_404_over_http(http, http_room, http_registration, http_play):
    http.post(f"{PREFIX}/play-frameworks/{http_play['id']}/enable", params={"actor": "dana"})
    dispatched = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/dispatch",
        json={"signal": signal_payload(registration_id=http_registration["id"])},
    ).json()
    other = http.post("/api/records/room", json={"name": "Elsewhere"}).json()
    assert (
        http.get(f"{PREFIX}/rooms/{other['id']}/tasks/{dispatched['created'][0]['id']}").status_code
        == 404
    )


def test_completing_a_task_over_http_records_the_outcome(http, http_registration, http_room, http_play):
    http.post(f"{PREFIX}/play-frameworks/{http_play['id']}/enable", params={"actor": "dana"})
    dispatched = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/dispatch",
        json={"signal": signal_payload(registration_id=http_registration["id"])},
    ).json()
    task_id = dispatched["created"][0]["id"]

    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/tasks/{task_id}/complete",
        json={"note": "Called."},
        params={"actor": "dana"},
    )
    assert response.status_code == 200
    assert response.json()["task"]["state"] == "completed"
    assert [event["event_type"] for event in response.json()["events"]] == ["task_completed"]


def test_completing_an_unknown_task_over_http_is_404(http, http_room):
    response = http.post(f"{PREFIX}/rooms/{http_room['id']}/tasks/play_task_nope/complete")
    assert response.status_code == 404


def test_listing_events_over_http_counts_the_failing_ones(http, http_registration, http_room, http_play):
    http.post(f"{PREFIX}/play-frameworks/{http_play['id']}/enable", params={"actor": "dana"})
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/dispatch",
        json={"signal": signal_payload(registration_id=http_registration["id"])},
    )
    body = http.get(f"{PREFIX}/rooms/{http_room['id']}/events").json()
    assert body["count"] == 1
    assert body["failing"] == 0
    assert body["retrying"] == 0


def test_recording_a_delivery_over_http_answers_201(http, http_registration, http_room, http_play):
    http.post(f"{PREFIX}/play-frameworks/{http_play['id']}/enable", params={"actor": "dana"})
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/dispatch",
        json={"signal": signal_payload(registration_id=http_registration["id"])},
    )
    event = http.get(f"{PREFIX}/rooms/{http_room['id']}/events").json()["events"][0]

    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/events/{event['id']}/deliveries", json={"status_code": 200}
    )
    assert response.status_code == 201
    assert response.json()["event"]["state"] == "delivered"


def test_a_delivery_route_refuses_an_attempt_with_no_outcome(http, http_registration, http_room, http_play):
    http.post(f"{PREFIX}/play-frameworks/{http_play['id']}/enable", params={"actor": "dana"})
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/dispatch",
        json={"signal": signal_payload(registration_id=http_registration["id"])},
    )
    event = http.get(f"{PREFIX}/rooms/{http_room['id']}/events").json()["events"][0]
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/events/{event['id']}/deliveries", json={}
    )
    assert response.status_code == 400


def test_a_delivery_route_refuses_a_retry_on_a_finished_delivery(
    http, http_registration, http_room, http_play
):
    http.post(f"{PREFIX}/play-frameworks/{http_play['id']}/enable", params={"actor": "dana"})
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/dispatch",
        json={"signal": signal_payload(registration_id=http_registration["id"])},
    )
    event = http.get(f"{PREFIX}/rooms/{http_room['id']}/events").json()["events"][0]
    first = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/events/{event['id']}/deliveries", json={"status_code": 200}
    )
    assert first.status_code == 201

    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/events/{event['id']}/deliveries", json={"status_code": 503}
    )
    assert response.status_code == 409
    assert "no further attempts" in response.json()["detail"]


def test_a_delivery_route_refuses_an_early_retry_over_http(
    http, http_registration, http_room, http_play
):
    """The 15 second spacing is enforced at the route, not only in the domain."""
    http.post(f"{PREFIX}/play-frameworks/{http_play['id']}/enable", params={"actor": "dana"})
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/dispatch",
        json={"signal": signal_payload(registration_id=http_registration["id"])},
    )
    event = http.get(f"{PREFIX}/rooms/{http_room['id']}/events").json()["events"][0]
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/events/{event['id']}/deliveries", json={"status_code": 503}
    )
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/events/{event['id']}/deliveries", json={"status_code": 503}
    )
    assert response.status_code == 409
    assert "not due until" in response.json()["detail"]


def test_a_webhook_subscription_round_trips_over_http(http):
    created = http.post(
        f"{PREFIX}/webhook-subscriptions",
        json={
            "event_types": ["task_created", "success_created"],
            "target_url": "https://hooks.example.invalid/dsr",
        },
    )
    assert created.status_code == 201
    assert created.json()["subscription"]["event_types"] == ["task_created", "success_created"]

    listed = http.get(f"{PREFIX}/webhook-subscriptions").json()
    assert listed["count"] == 1

    removed = http.delete(f"{PREFIX}/webhook-subscriptions/{created.json()['subscription']['id']}")
    assert removed.status_code == 200
    assert http.get(f"{PREFIX}/webhook-subscriptions").json()["count"] == 0


def test_an_undocumented_event_type_over_http_is_refused(http):
    response = http.post(f"{PREFIX}/webhook-subscriptions", json={"event_types": ["task_exploded"]})
    assert response.status_code == 400


def test_the_room_summary_route_serves_the_automation_note(http, http_room):
    body = http.get(f"{PREFIX}/rooms/{http_room['id']}/summary").json()
    assert body["room_id"] == http_room["id"]
    assert body["automation_note"] == AUTOMATION_NOTE
    assert body["tasks_from_automation"] == 0


def test_the_registry_route_accepts_a_where_style_filter(http, http_registration, http_play):
    assert http.get(
        f"{PREFIX}/play-frameworks", params={"task_type": "call"}
    ).json()["count"] == 1
    assert http.get(
        f"{PREFIX}/play-frameworks", params={"task_type": "email"}
    ).json()["count"] == 0


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
            for expected, found in zip(template, actual)
        ):
            return True
    return False


def test_every_write_audit_row_names_a_route_the_app_serves(http, http_room):
    """The port's central guarantee, checked against the route table the host reports.

    The same class of bug has shipped in this codebase before: a feature's audit
    log kept naming a path the app had stopped serving.
    """
    registration = http.post("/api/records/signal_registration", json=REGISTRATION).json()
    payload = play_payload(signal_registration_id=registration["id"])
    play = http.post(f"{PREFIX}/play-frameworks", json=payload).json()["play"]
    http.patch(f"{PREFIX}/play-frameworks/{play['id']}", json={"label": {"fr": "Appeler"}})
    http.post(f"{PREFIX}/play-frameworks/{play['id']}/enable", params={"actor": "dana"})
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/dispatch",
        json={"signal": signal_payload(registration_id=registration["id"])},
        params={"actor": "dana"},
    )
    task = http.get(f"{PREFIX}/rooms/{http_room['id']}/tasks").json()["tasks"][0]
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/tasks/{task['id']}/complete",
        json={"note": "Called."},
        params={"actor": "dana"},
    )
    http.post(f"{PREFIX}/webhook-subscriptions", json={"event_types": ["task_created"]})
    http.post(f"{PREFIX}/play-frameworks/{play['id']}/disable", params={"actor": "dana"})
    http.delete(f"{PREFIX}/play-frameworks/{play['id']}")

    served = [
        route
        for feature in http.get("/api/features").json()["features"]
        for route in feature["routes"]
    ]
    entries = http.get("/api/audit", params={"limit": 500}).json()["entries"]
    sources = {entry["source"] for entry in entries if entry["source"]}

    # The core records API and the seeder write with their own sources; only the
    # rows this feature's HTTP layer produced are in scope here.
    ours = {source for source in sources if source.split(" ", 1)[1].startswith(PREFIX)}
    assert ours, f"no wf-028 write was audited at all; saw {sorted(sources)}"
    for source in sorted(ours):
        assert _matches_registered_route(source, served), (
            f"audit row names {source!r}, which is not a route this app serves"
        )


def test_the_completion_route_audits_under_its_own_route(http, http_registration, http_room, http_play):
    http.post(f"{PREFIX}/play-frameworks/{http_play['id']}/enable", params={"actor": "dana"})
    dispatched = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/dispatch",
        json={"signal": signal_payload(registration_id=http_registration["id"])},
    ).json()
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/tasks/{dispatched['created'][0]['id']}/complete",
        params={"actor": "dana"},
    )
    entries = http.get("/api/audit", params={"collection": "play_task"}).json()["entries"]
    assert entries[0]["source"] == f"POST {PREFIX}/rooms/{{room_id}}/tasks/{{task_id}}/complete"


def test_the_delivery_route_audits_under_its_own_route(http, http_registration, http_room, http_play):
    http.post(f"{PREFIX}/play-frameworks/{http_play['id']}/enable", params={"actor": "dana"})
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/dispatch",
        json={"signal": signal_payload(registration_id=http_registration["id"])},
    )
    event = http.get(f"{PREFIX}/rooms/{http_room['id']}/events").json()["events"][0]
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/events/{event['id']}/deliveries", json={"status_code": 200}
    )
    entries = http.get("/api/audit", params={"collection": "play_event"}).json()["entries"]
    assert entries[0]["source"].endswith("/deliveries")


def test_writes_do_not_record_a_path_this_app_does_not_serve(http, http_registration, http_room, http_play):
    """Explicitly: no hardcoded URL, and no salesloft.com path in the audit log.

    The second half matters because the researched API is
    ``https://api.salesloft.com/v2/integrations/signals/registrations/plays``. This
    product is the source of the buyer's event, not a proxy for the vendor, and an
    audit row naming the vendor's URL would tell a reviewer a request went
    somewhere this app never sends one.
    """
    http.post(f"{PREFIX}/play-frameworks", json=play_payload(signal_registration_id=http_registration["id"]))
    http.post(f"{PREFIX}/play-frameworks/{http_play['id']}/enable", params={"actor": "dana"})
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/dispatch",
        json={"signal": signal_payload(registration_id=http_registration["id"])},
    )
    entries = http.get("/api/audit", params={"limit": 500}).json()["entries"]
    for entry in entries:
        source = entry.get("source") or ""
        assert "salesloft" not in source.lower()
        assert "http" not in source.lower()
        if source:
            assert source.split(" ")[0] in {"POST", "PATCH", "DELETE", "PUT"}


def test_the_audit_row_carries_the_actor_the_route_was_given(http, http_registration, http_room, http_play):
    http.post(f"{PREFIX}/play-frameworks", json=play_payload(signal_registration_id=http_registration["id"]))
    http.post(f"{PREFIX}/play-frameworks/{http_play['id']}/enable", params={"actor": "dana"})
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/dispatch",
        json={"signal": signal_payload(registration_id=http_registration["id"])},
        params={"actor": "dana"},
    )
    entries = http.get("/api/audit", params={"collection": "play_task"}).json()["entries"]
    assert entries[0]["actor"] == "dana"


# --------------------------------------------------------------------------- #
# The demo data
# --------------------------------------------------------------------------- #


def test_the_seed_runs_and_reports_what_it_added(store, tmp_path):
    room = store.create("room", ROOM, actor="seed", source="seed")
    summary = load_feature(MODULE).seed(
        store.db, {"room_ids": [(room["id"], "Northwind Traders")], "now": NOW, "rng": None}
    )

    assert "Play frameworks" in summary
    assert "tasks generated with no human in the loop" in summary
    assert "1 webhook subscription" in summary


def test_the_seed_leaves_one_play_registered_and_not_enabled(store):
    """The activation sentence is the workflow's most important line; the demo shows it."""
    room = store.create("room", ROOM, actor="seed", source="seed")
    load_feature(MODULE).seed(store.db, {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": None})

    engine = PlayEngine(store)
    assert engine.summary(room_id=room["id"])["registered_not_enabled"] == 1
    assert engine.summary(room_id=room["id"])["live_plays"] == 2


def test_the_seed_produces_the_states_that_are_not_all_successes(store):
    room = store.create("room", ROOM, actor="seed", source="seed")
    load_feature(MODULE).seed(store.db, {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": None})

    engine = PlayEngine(store)
    generated = engine.generated_tasks(limit=200)
    assert any(task["assigned"] is True for task in generated)
    assert any(task["assigned"] is False for task in generated)
    assert any(task["routable"] is False for task in generated)
    assert any(task["duplicate_attempts"] for task in generated)
    assert any(task["state"] == "completed" for task in generated)


def test_the_seed_produces_a_delivery_that_failed_and_one_that_recovered(store):
    room = store.create("room", ROOM, actor="seed", source="seed")
    load_feature(MODULE).seed(store.db, {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": None})

    engine = PlayEngine(store)
    recorded = engine.outcome_events(limit=200)
    assert any(event["state"] == "failed" for event in recorded)
    assert any(event["state"] == "delivered" and len(event["attempts"]) > 1 for event in recorded)


def test_the_seed_is_deterministic(tmp_path):
    """Two seeder runs on two fresh databases produce the same demo.

    Determinism is worth a test because a demo whose rows change every run cannot
    be compared: a reviewer looking at two databases would be reading the clock.
    """
    import random as _random

    module = load_feature(MODULE)

    def run(name: str) -> list[str]:
        db = AuditedDatabase(tmp_path / f"{name}.db", mirror_dir=tmp_path / name)
        try:
            store = RecordStore(db)
            room = store.create("room", ROOM, actor="seed", source="seed")
            module.seed(
                store.db,
                {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": _random.Random("wf028")},
            )
            engine = PlayEngine(store)
            # The signal key, the type, the subject and the state - the parts the
            # demo decides. Record ids are generated by the store and are random
            # by design, so comparing them would be comparing uuid4 output.
            return sorted(
                f"{task['signal_key']}|{task['task_type']}|{task['subject']}|{task['state']}"
                f"|{task['assigned']}|{task['routable']}"
                for task in engine.generated_tasks(limit=200)
            )
        finally:
            db.close()

    first = run("first")
    assert first == run("second")
    assert first, "the seed produced no task to compare"


def test_the_seed_with_no_rooms_says_so_rather_than_failing(store):
    summary = load_feature(MODULE).seed(store.db, {"room_ids": [], "now": NOW, "rng": None})
    assert "0 tasks" in summary


def test_the_seed_runs_with_bare_room_ids_and_no_rng(store):
    """A caller assembling a context by hand should not have to know the tuple shape."""
    room = store.create("room", ROOM, actor="seed", source="seed")
    summary = load_feature(MODULE).seed(store.db, {"room_ids": [room["id"]]})
    assert "Play frameworks" in summary


def test_the_seed_writes_only_through_the_audited_store(store):
    """Every row the demo adds has an audit row, because the store is the only path.

    Asserting the *set* of sources rather than that "seed" is among them: a demo
    that opened a connection of its own would add a row with no audit entry at all,
    and a source outside this set is the visible sign of that.
    """
    room = store.create("room", ROOM, actor="seed", source="seed")
    load_feature(MODULE).seed(store.db, {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": None})

    sources = {entry["source"] for entry in store.audit(limit=1000)}
    assert sources <= {"seed"}, f"the seed wrote outside the store's own paths: {sorted(sources)}"
    collections = {entry["collection"] for entry in store.audit(limit=1000)}
    assert {"play_framework", "play_task", "play_event", "play_webhook"} <= collections
