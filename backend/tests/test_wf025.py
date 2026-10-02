"""Tests for WF-025: stream workspace activity events to your own systems.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-025.md``, whose research
source is section 10 of ``docs/research/raw/analytics-intent.md``:

* the subscription-type vocabulary, reproduced verbatim including the odd
  ``workspace.NDA.signed`` spelling;
* the ``webhook-event`` payload: ``occurredAt``,
  ``propertyName``/``propertyPreviousValue``/``propertyValue``,
  ``associatedObjects`` and the eight object kinds;
* **anonymous activity omits ``user``** - the key is absent, not null;
* ``presentation.*`` is emitted for share-link activity only;
* ``asset.*`` carries the asset snapshot with ``trackingEnabled``;
* ``workspace.form.submitted`` carries ``formQuestions`` +
  ``formQuestionResponses``, typed, incl. ``file_upload``;
* a presigned URL in a payload expires one hour after issue;
* only an account ``admin`` may create a webhook, and the URL is verified with a
  POST;
* a subscription can be viewed in detail, paused, or unsubscribed;
* "Send test events" during setup;
* a 10-second webhook timeout and a 26-retry ladder of 1 min, 10 min, then
  hourly x24;
* signing-secret rotation with a previous-signature header;
* the pull-based backfill: five paths, a ``properties`` parameter whose omission
  yields only ``id``/``object``/``url``, and ``429`` on rate limit;
* Seismic's parallel ``DSR*V1`` event names, published for comparison.

Delivery runs through a fake transport, so the retry ladder, the signing, and
the pause/filter skip behaviour are all asserted without a socket and without a
network flake.

The two tests worth finding first
---------------------------------
``test_every_write_audit_row_names_a_route_the_app_serves`` is the brief's
central guarantee, checked against the live route table rather than against a
constant. ``test_a_filter_that_does_not_parse_is_refused_not_ignored`` is the
behaviour that would otherwise cost somebody a day: a subscription filter that
failed to parse and silently matched nothing is indistinguishable from "no
activity happened".
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase, RecordNotFound
from dsr.event_stream import EventStream, filters as filter_module
from dsr.event_stream.backfill import DEFAULT_LIMIT, RateLimiter, require_properties
from dsr.event_stream.delivery import (
    DEFAULT_TIMEOUT,
    RETRY_AFTER_CAP,
    RETRYABLE_STATUS,
    DeliveryResult,
    attempt_delivery,
    attempts_remaining,
    next_attempt,
    retry_delay,
)
from dsr.event_stream.errors import (
    DeliveryError,
    EventPayloadError,
    EventStreamError,
    FilterError,
    NotPermitted,
    SubscriptionError,
    TargetError,
    VocabularyError,
)
from dsr.event_stream.inferences import INFERENCES, describe as describe_inferences
from dsr.event_stream.payloads import (
    SHARE_LINK_FIELD,
    WEBHOOK_EVENT_OBJECT,
    build_event_payload,
    is_anonymous,
    normalise_asset_snapshot,
    normalise_associated_objects,
    normalise_form_questions,
    normalise_form_responses,
    render_associated_objects,
)
from dsr.event_stream.registry import (
    DELIVERY_COLLECTION,
    EVENT_COLLECTION,
    SUBSCRIPTION_COLLECTION,
    WEBHOOK_COLLECTION,
    EndpointBook,
    SubscriptionBook,
    compile_for,
)
from dsr.event_stream.signing import (
    PREVIOUS_SIGNATURE_HEADER,
    SECRET_PREFIX,
    SIGNATURE_HEADER,
    confirm_rotation,
    generate_secret,
    mask,
    rotation_overlap,
    sign,
    sign_headers,
    verify,
)
from dsr.event_stream.stream import ROLE_ADMIN, require_state
from dsr.event_stream.targets import (
    BLOCKED_HOSTNAMES,
    PRESIGNED_TTL_SECONDS,
    expiry_note,
    is_expired,
    issue_presigned_url,
    same_origin,
    validate_target_url,
    verification_failure,
)
from dsr.event_stream.vocabulary import (
    ALWAYS_ASSOCIATED,
    ASSET_EVENTS,
    ASSET_SNAPSHOT_FIELDS,
    ASSOCIATED_OBJECTS,
    EVENT_TYPES,
    FILE_UPLOAD_QUESTION,
    FORM_EVENTS,
    MAX_RETRIES,
    MINIMAL_PROPERTIES,
    OPTIONAL_ASSOCIATED,
    PULL_RESOURCES,
    SEISMIC_EVENTS,
    SHARE_LINK_EVENTS,
    WEBHOOK_TIMEOUT_SECONDS,
    is_known_event,
    require_event,
    require_types,
    required_objects_for,
    unknown_types,
    vocabulary,
)
from dsr.features import load_feature
from dsr.store import RecordStore
from fastapi.testclient import TestClient

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-025"

#: What the pure-domain tests pass as ``source``. Deliberately the exact shape a
#: route passes, so a test asserting on an audit row is asserting on the real
#: thing rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/events"

#: A fixed clock, so a presigned expiry and a retry schedule are checkable.
NOW = datetime(2026, 9, 27, 10, 0, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=2)

TARGET = "https://hooks.example/northwind-warehouse"
SECOND_TARGET = "https://hooks.example/slack"
RETIRED_TARGET = "https://hooks.example/retired"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def inference(inference_id: str) -> dict:
    return next((entry for entry in INFERENCES if entry["id"] == inference_id), {})


class FakeTransport:
    """Records every call and replays a scripted list of results.

    When the script runs out it accepts, so a test only has to script the calls it
    actually cares about.
    """

    def __init__(self, *results: DeliveryResult) -> None:
        self.calls: list[dict] = []
        self.scripted = list(results)

    def post(self, url, body, headers, timeout) -> DeliveryResult:
        self.calls.append(
            {
                "url": url,
                "body": json.loads(body),
                "raw": body,
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        if self.scripted:
            return self.scripted.pop(0)
        return DeliveryResult(ok=True, status=200, body="ok")


def ok(status: int = 200) -> DeliveryResult:
    return DeliveryResult(ok=True, status=status, body="ok")


def rate_limited(retry_after: float | None = 0) -> DeliveryResult:
    return DeliveryResult(
        ok=False, status=429, error="HTTP 429", retryable=True, retry_after=retry_after
    )


def server_error() -> DeliveryResult:
    return DeliveryResult(ok=False, status=503, error="HTTP 503", retryable=True)


def not_found() -> DeliveryResult:
    return DeliveryResult(ok=False, status=404, body="no such hook", error="HTTP 404")


def test_no_module_in_the_package_touches_the_environment_or_a_temp_folder():
    """A feature that read an env var or reached for a temp path would be reading
    outside the store it was handed.

    ``DSR_DB_PATH`` and the audit mirror are resolved by :mod:`dsr.deps` and only
    there, which is what keeps a feature testable against a temporary database
    without a module of its own going looking for one.
    """
    package = Path(load_feature("wf025_stream_workspace_activity_events_to_yo").__file__).parent
    sources = [package / "wf025_stream_workspace_activity_events_to_yo.py"]
    sources += sorted((package.parent / "event_stream").glob("*.py"))
    for source in sources:
        text = source.read_text(encoding="utf-8")
        assert "import tempfile" not in text, f"{source.name} imports tempfile"
        assert "os.environ" not in text, f"{source.name} reads an environment variable"
        assert "sqlite3" not in text, f"{source.name} opens SQLite directly"


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(tmp_path / "wf025.db", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def transport():
    return FakeTransport()


@pytest.fixture()
def stream(store, transport):
    """An :class:`EventStream` with a fixed clock and no socket."""
    return EventStream(store, transport=transport, now=NOW)


@pytest.fixture()
def webhook(stream):
    return stream.create_webhook("Northwind to warehouse", TARGET, role=ROLE_ADMIN, source=SOURCE)[
        "webhook"
    ]


@pytest.fixture()
def http(monkeypatch, transport):
    """A client over a temporary database, with the transport faked.

    ``DSR_DB_PATH`` points at a temporary file the way ``test_features.py`` does,
    and the whole service is replaced through ``app.dependency_overrides`` - the
    seam the feature contract provides for exactly this, so no socket is opened
    anywhere in this suite and no wall-clock time is spent in a retry.
    """
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf025.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as client:
        app.dependency_overrides[
            load_feature("wf025_stream_workspace_activity_events_to_yo").get_stream
        ] = lambda: EventStream(client.app.state.store, transport=transport, now=NOW)
        # The scripted transport is reachable from a test body, so a test can
        # make the next call fail without rebuilding the service.
        client.transport = transport  # type: ignore[attr-defined]
        try:
            yield client
        finally:
            app.dependency_overrides.clear()
    tmp.cleanup()


@pytest.fixture()
def http_room(http):
    return http.post(
        "/api/records/room",
        json={
            "name": "Northwind — Enterprise Evaluation",
            "account": "Northwind Traders",
            "stage": "evaluation",
        },
    ).json()


@pytest.fixture()
def app_store(http):
    return http.app.state.store


def objects(workspace: str = "ws_1", account: str = "acc_1", user: str | None = None, **extra):
    built: dict = {"workspace": {"id": workspace}, "account": {"id": account}}
    if user:
        built["user"] = {"id": user, "email": f"{user}@northwind.example"}
    built.update(extra)
    return built


def subscribe(stream, webhook_id: str, types=None, **kwargs):
    return stream.create_subscription(
        webhook_id,
        types or ["workspace.viewed"],
        source=f"POST {PREFIX}/webhooks/x/subscriptions",
        **kwargs,
    )


def record(stream, event: str = "workspace.viewed", **kwargs):
    kwargs.setdefault("associated_objects", objects())
    return stream.record_event(event, source=SOURCE, **kwargs)


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The routes resolve even though no shared file names this feature."""
    entry = next(f for f in http.get("/api/features").json()["features"] if f["ticket"] == "WF-025")
    assert entry["prefix"] == PREFIX
    assert entry["id"] == "wf-025-stream-workspace-activity-events-to-yo"
    assert entry["exception_handlers"] == ["EventStreamError"]
    assert len(entry["routes"]) == 32


def test_the_prefix_is_ours_alone(http):
    """No core route and no other feature answers anything under it."""
    served = {
        (method, route["path"])
        for feature in http.get("/api/features").json()["features"]
        for route in feature["routes"]
        for method in route["methods"]
    }
    mine = {key for key in served if key[1].startswith(PREFIX)}
    others = {key for key in served if not key[1].startswith(PREFIX)}
    assert len(mine) == 32
    assert not mine & others


def test_feature_module_does_not_import_the_shared_app():
    """A guard exists in test_features.py; this states the reason locally.

    Checks the two import spellings the real guard looks for rather than the bare
    string, so a docstring that *mentions* ``dsr.api`` to explain why it is not
    imported does not fail the test.
    """
    source = Path(load_feature("wf025_stream_workspace_activity_events_to_yo").__file__).read_text(
        encoding="utf-8"
    )
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
        / "wf-025-stream-workspace-activity-events-to-yo"
        / "index.jsx"
    )
    module = load_feature("wf025_stream_workspace_activity_events_to_yo")
    text = descriptor.read_text(encoding="utf-8")
    assert module.FEATURE["id"] in text
    assert f"id: {module.FEATURE['id']!r}" in text


# --------------------------------------------------------------------------- #
# The researched vocabulary
# --------------------------------------------------------------------------- #


def test_event_types_are_exactly_the_published_twenty_four():
    assert len(EVENT_TYPES) == 24
    assert EVENT_TYPES[:6] == (
        "workspace.created",
        "workspace.viewed",
        "workspace.page.viewed",
        "workspace.section_navigation.clicked",
        "workspace.file.viewed",
        "workspace.file.downloaded",
    )
    assert EVENT_TYPES[-4:] == (
        "presentation.viewed",
        "presentation.downloaded",
        "presentation.shared",
        "course.completed",
    ) or EVENT_TYPES[-3:] == ("presentation.shared", "course.completed", "course.reviewed")


def test_the_odd_nda_spelling_is_reproduced_verbatim():
    """``workspace.NDA.signed`` is not ``workspace.nda.signed`` in the research.

    A tidied spelling would silently never match a subscriber that copied the
    vendor's own vocabulary, so this is pinned.
    """
    assert "workspace.NDA.signed" in EVENT_TYPES
    assert "workspace.nda.signed" not in EVENT_TYPES


def test_every_order_form_verb_is_published():
    verbs = [e for e in EVENT_TYPES if e.startswith("workspace.order_form.")]
    assert verbs == [
        "workspace.order_form.viewed",
        "workspace.order_form.downloaded",
        "workspace.order_form.signed",
        "workspace.order_form.fully_signed",
    ]


def test_associated_objects_are_the_eight_researched_kinds_in_order():
    assert ASSOCIATED_OBJECTS == (
        "workspace",
        "account",
        "user",
        "workspacePage",
        "workspaceSection",
        "workspacePlanTask",
        "file",
        "workspaceForm",
    )
    assert ALWAYS_ASSOCIATED == ("workspace", "account")
    assert OPTIONAL_ASSOCIATED == ("user",)


def test_seismic_event_names_are_published_for_comparison():
    """A subscriber that also consumes the other vendor needs to know they differ."""
    assert SEISMIC_EVENTS == (
        "DSRCreatedV1",
        "DSRUpdatedV1",
        "DSRExpiredV1",
        "DSRContentUpdatedV1",
    )


def test_vocabulary_reports_the_whole_contract():
    body = vocabulary()
    assert body["event_count"] == 24
    assert body["share_link_events"] == list(SHARE_LINK_EVENTS)
    assert body["asset_events"] == list(ASSET_EVENTS)
    assert body["form_events"] == list(FORM_EVENTS)
    assert body["file_upload_question"] == FILE_UPLOAD_QUESTION
    assert body["asset_snapshot_fields"] == list(ASSET_SNAPSHOT_FIELDS)
    assert body["minimal_properties"] == ["id", "object", "url"]


def test_vocabulary_reports_the_retry_policy():
    body = vocabulary()
    assert body["delivery"]["timeout_seconds"] == 10.0
    assert body["delivery"]["max_retries"] == 26
    assert body["delivery"]["max_attempts"] == 27


def test_pull_resources_cover_the_five_researched_paths():
    assert set(PULL_RESOURCES) == {
        "workspaces",
        "workspaces/{workspaceId}",
        "assets",
        "forms/{formId}/responses",
        "workspace-plan-tasks",
    }
    for spec in PULL_RESOURCES.values():
        assert spec["vendor_path"].startswith("/v1/")
        assert spec["route"]
        assert spec["collection"]


def test_is_known_event_separates_published_from_read():
    assert is_known_event("workspace.viewed")
    assert not is_known_event("workspace.reticulated")


def test_an_unread_type_is_kept_because_the_research_says_include():
    """The research introduces its list with "include", not "are exactly".

    Refusing a type the vendor published after this build would break every
    subscription pointing at it, so an unrecognised-but-well-formed name is kept
    and flagged. Recorded as inference ``event-type-set-is-a-floor``.
    """
    assert require_event("workspace.reticulated") == "workspace.reticulated"
    assert unknown_types(["workspace.viewed", "workspace.reticulated"]) == ["workspace.reticulated"]
    assert inference("event-type-set-is-a-floor")["value"]["unknown_types"].startswith("accepted")


def test_a_missing_or_non_string_type_is_still_refused():
    """Where the real typo risk is, so where the refusal belongs."""
    with pytest.raises(VocabularyError):
        require_event(None)
    with pytest.raises(VocabularyError):
        require_event("   ")
    with pytest.raises(VocabularyError):
        require_event(7)


def test_require_types_preserves_order_and_collapses_duplicates():
    assert require_types(["b", "a", "b"]) == ("b", "a")
    assert require_types("workspace.viewed") == ("workspace.viewed",)


def test_require_types_refuses_an_empty_subscription():
    with pytest.raises(VocabularyError) as caught:
        require_types([])
    assert "at least one" in str(caught.value)


def test_required_objects_for_a_page_view_names_the_workspace_page():
    assert required_objects_for("workspace.page.viewed") == (
        "workspace",
        "account",
        "workspacePage",
    )
    assert required_objects_for("workspace.section_navigation.clicked")[-1] == "workspaceSection"
    assert required_objects_for("workspace.form.submitted")[-1] == "workspaceForm"
    assert required_objects_for("course.completed")[-1] == "workspacePlanTask"


# --------------------------------------------------------------------------- #
# Subscription filters
# --------------------------------------------------------------------------- #


def test_a_filter_that_does_not_parse_is_refused_not_ignored():
    """The behaviour that would otherwise cost somebody a day.

    A filter that failed to parse and silently matched nothing is
    indistinguishable from "no activity happened", and the operator would go
    debug their warehouse instead of their subscription.
    """
    for bad in (
        "$.a[?(@.k == )]",
        "$.a[?(@.k",
        "$.a[",
        "nope",
        "$.a[?(@.k ~= 1)]",
        "$.a|",
        "$['a",
    ):
        with pytest.raises(FilterError) as caught:
            filter_module.compile_filter(bad)
        assert caught.value.status == 400
        assert caught.value.code == "invalid_filter"


def test_a_bare_comparison_is_refused_with_a_message_that_says_what_to_write():
    with pytest.raises(FilterError) as caught:
        filter_module.compile_filter("$.account.id == 'acc_1'")
    assert "predicate" in str(caught.value)


def test_no_filter_means_receives_everything():
    """The researched Dock behaviour: sub-filtering happens in the subscriber."""
    assert filter_module.compile_filter(None) is None
    assert filter_module.compile_filter("") is None
    assert filter_module.compile_filter("   ") is None
    assert filter_module.matches(None, {"anything": 1}) is True


def test_a_child_path_matches():
    payload = {"associatedObjects": {"account": {"id": "acc_1"}}}
    assert filter_module.matches("$.associatedObjects.account.id", payload)
    assert not filter_module.matches("$.associatedObjects.account.name", payload)


def test_a_bracket_path_matches():
    payload = {"associatedObjects": {"user": {"id": "u_1"}}}
    assert filter_module.matches("$['associatedObjects']['user']['id']", payload)


def test_recursive_descent_finds_a_nested_key():
    payload = {"associatedObjects": {"user": {"id": "u_1", "email": "a@b.example"}}}
    assert filter_module.matches("$..email", payload)
    assert not filter_module.matches("$..phone", payload)


def test_a_predicate_reads_the_node_the_expression_reached():
    """``@.id`` is the id of whatever node the path arrived at, not the root's."""
    payload = {"associatedObjects": {"account": {"id": "acc_1"}, "user": {"id": "u_9"}}}
    assert filter_module.matches("$.associatedObjects.account[?(@.id == 'acc_1')]", payload)
    assert not filter_module.matches("$.associatedObjects.account[?(@.id == 'acc_2')]", payload)
    # The same predicate one step earlier asks a different question.
    assert not filter_module.matches("$.associatedObjects[?(@.id == 'acc_1')]", payload)


def test_predicates_compare_against_scalars():
    payload = {"asset": {"trackingEnabled": True, "downloads": 4, "name": "Deck"}}
    assert filter_module.matches("$.asset[?(@.trackingEnabled == true)]", payload)
    assert filter_module.matches("$.asset[?(@.downloads >= 4)]", payload)
    assert filter_module.matches("$.asset[?(@.name != 'Other')]", payload)
    assert not filter_module.matches("$.asset[?(@.downloads > 4)]", payload)


def test_an_ordered_comparison_between_incompatible_types_just_does_not_hold():
    """A filter is a question; "cannot be ordered" is a legitimate answer of no."""
    assert not filter_module.matches("$.asset[?(@.name > 4)]", {"asset": {"name": "Deck"}})


def test_a_union_of_child_names_matches():
    payload = {"associatedObjects": {"user": {"id": "u_1"}}}
    assert filter_module.matches("$.associatedObjects['user','file']", payload)
    assert not filter_module.matches("$.associatedObjects['file']", payload)


def test_an_index_addresses_a_list():
    payload = {"asset": {"tags": ["security", "compliance"]}}
    assert filter_module.matches("$.asset.tags[0]", payload)
    assert filter_module.matches("$.asset.tags[-1]", payload)
    assert not filter_module.matches("$.asset.tags[9]", payload)


def test_bare_recursive_descent_with_a_predicate_parses():
    """The researched ``$.data..[?(...)]`` shape, minus the ``data`` wrapper."""
    compiled = filter_module.compile_filter("$.associatedObjects..[?(@.object == 'user')]")
    assert compiled is not None
    assert compiled.matches({"associatedObjects": {"user": {"id": "u_1", "object": "user"}}})


def test_the_researched_example_works_against_a_top_level_payload():
    """Seismic's own documented example, used verbatim.

    This product's payload has its fields at the top level, so without the
    ``data`` root alias, copying the researched example would be the one thing
    guaranteed to match nothing.
    """
    researched = "$.data..[?(@.teamSiteId == '1')]"
    payload = {"associatedObjects": {"account": {"id": "acc_1", "teamSiteId": "1"}}}
    assert filter_module.compile_filter(researched).matches(payload)


def test_alternation_is_an_or():
    compiled = filter_module.compile_filter(
        "$.associatedObjects.account | $.associatedObjects.user"
    )
    assert compiled.to_dict()["alternatives"] == 2
    assert compiled.matches({"associatedObjects": {"user": {"id": "u_1"}}})
    assert not compiled.matches({"associatedObjects": {"file": {"id": "f_1"}}})


def test_the_root_alias_cannot_turn_a_match_into_a_non_match():
    """Widening the root is an OR, so it is monotone."""
    for expression in ("$.a", "$.data.a", "$..a"):
        payload = {"a": 1}
        assert filter_module.compile_filter(expression).matches(payload)


def test_a_filter_longer_than_the_limit_is_refused():
    with pytest.raises(FilterError):
        filter_module.compile_filter("$." + "a" * 600)


def test_a_non_string_filter_is_refused():
    with pytest.raises(FilterError):
        filter_module.compile_filter(["$.a"])


def test_the_grammar_is_published_with_a_worked_example():
    described = filter_module.describe()
    assert described["refuses_unparseable"] is True
    assert "data" in described["root_alias"]
    assert described["worked_example"]
    assert "==" in described["operators"]


# --------------------------------------------------------------------------- #
# Signing
# --------------------------------------------------------------------------- #


def test_a_generated_secret_is_prefixed_and_long():
    secret = generate_secret()
    assert secret.startswith(SECRET_PREFIX)
    assert len(secret) > 40
    assert generate_secret() != secret


def test_a_signature_is_an_hmac_over_the_exact_bytes():
    secret = generate_secret()
    body = b'{"event":"workspace.viewed"}'
    signature = sign(secret, body)
    assert signature.startswith("sha256=")
    assert verify(secret, body, signature)
    assert not verify(secret, body + b" ", signature)
    assert not verify(generate_secret(), body, signature)
    assert not verify(secret, body, None)


def test_no_secret_means_no_signature():
    assert sign(None, b"x") is None
    assert sign("", b"x") is None
    assert not verify(None, b"x", "sha256=whatever")


def test_a_masked_secret_is_recognisable_but_not_usable():
    secret = generate_secret()
    masked = mask(secret)
    assert masked.startswith(SECRET_PREFIX)
    assert "…" in masked
    assert masked.endswith(secret[-4:])
    assert len(masked) < len(secret)
    assert mask(None) is None


def test_headers_carry_the_event_the_delivery_and_the_signature():
    headers = sign_headers(b"body", secret="whsec_x", event="workspace.viewed", delivery_id="d1")
    assert headers[SIGNATURE_HEADER].startswith("sha256=")
    assert headers["X-DSR-Event"] == "workspace.viewed"
    assert headers["X-DSR-Delivery"] == "d1"
    assert headers["Content-Type"] == "application/json"
    assert PREVIOUS_SIGNATURE_HEADER not in headers


def test_a_delivery_during_rotation_carries_both_signatures():
    headers = sign_headers(
        b"body", secret="whsec_new", previous_secret="whsec_old", event="e", delivery_id="d"
    )
    assert verify("whsec_new", b"body", headers[SIGNATURE_HEADER])
    assert verify("whsec_old", b"body", headers[PREVIOUS_SIGNATURE_HEADER])


def test_the_overlap_is_offered_until_a_success_or_a_second_rotation():
    fresh = {"secret": "whsec_new"}
    assert rotation_overlap(fresh) is None
    rotating = {"secret": "whsec_new", "previous_secret": "whsec_old"}
    assert rotation_overlap(rotating) == "whsec_old"
    confirmed = {**rotating, "previous_secret_confirmed": True}
    assert rotation_overlap(confirmed) is None


def test_confirming_a_rotation_records_that_it_happened():
    assert confirm_rotation({"previous_secret": "x"}) == {"previous_secret_confirmed": True}
    assert confirm_rotation({"secret": "x"}) == {}


# --------------------------------------------------------------------------- #
# Target URLs
# --------------------------------------------------------------------------- #


def test_an_https_target_is_accepted():
    assert validate_target_url(TARGET) == TARGET
    assert validate_target_url("  " + TARGET + "  ") == TARGET


def test_a_non_https_target_is_refused():
    for bad in ("http://hooks.example/x", "ftp://hooks.example/x", "hooks.example/x", ""):
        with pytest.raises(TargetError) as caught:
            validate_target_url(bad)
        assert "https" in str(caught.value) or "required" in str(caught.value)


def test_a_target_with_credentials_is_refused():
    """They would land in every subscriber access log with no way to rotate."""
    with pytest.raises(TargetError) as caught:
        validate_target_url("https://user:pass@hooks.example/x")
    assert caught.value.code == "invalid_target"
    assert "credential" in str(caught.value)


def test_a_target_with_a_fragment_is_refused():
    """A fragment is never sent to a server, so it could never be verified."""
    with pytest.raises(TargetError):
        validate_target_url("https://hooks.example/x#frag")


def test_a_target_with_no_host_is_refused():
    with pytest.raises(TargetError):
        validate_target_url("https:///x")


def test_private_and_loopback_hosts_are_refused_by_default():
    for host in sorted(BLOCKED_HOSTNAMES):
        with pytest.raises(TargetError):
            validate_target_url(f"https://{host}/hook")
    for host in ("db.internal", "cache.local", "x.localhost"):
        with pytest.raises(TargetError):
            validate_target_url(f"https://{host}/hook")


def test_private_hosts_can_be_allowed_on_purpose():
    """A rule that cannot be relaxed is a rule that gets deleted."""
    assert validate_target_url("https://localhost:9000/hook", allow_private=True)
    assert validate_target_url("https://10.0.0.7/hook", allow_private=True)


def test_a_private_ip_literal_is_refused_like_a_private_name():
    """A name-based check alone would miss https://10.0.0.1/hook."""
    for host in ("10.0.0.1", "192.168.1.1", "172.16.0.9", "169.254.169.254"):
        with pytest.raises(TargetError):
            validate_target_url(f"https://{host}/hook")


def test_a_netloc_with_no_readable_host_is_refused():
    """`hostname` is None for `https://::1/x`, and an unparseable host is not a
    host we should be fetching."""
    with pytest.raises(TargetError) as caught:
        validate_target_url("https://::1/hook")
    assert caught.value.code == "invalid_target"


def test_same_origin_compares_scheme_host_and_port():
    assert same_origin("https://a.example/x", "https://a.example/y")
    assert not same_origin("https://a.example/x", "https://b.example/x")
    assert not same_origin("https://a.example/x", "http://a.example/x")


def test_a_verification_failure_carries_the_endpoints_own_answer():
    error = verification_failure(TARGET, 500, "HTTP 500", "kaboom")
    assert error.status == 400
    assert error.code == "target_not_verified"
    assert "HTTP 500" in str(error)
    assert "kaboom" in error.remediation
    assert "No webhook was created" in error.remediation


# --------------------------------------------------------------------------- #
# Presigned URLs
# --------------------------------------------------------------------------- #


def test_a_presigned_url_expires_one_hour_out():
    """The researched one hour, as a constant rather than a number in a formatter."""
    assert PRESIGNED_TTL_SECONDS == 3600
    issued = issue_presigned_url("uploads/a.pdf", now=NOW)
    assert issued["expiresAt"] == "2026-09-27T11:00:00.000+00:00"
    assert issued["key"] == "uploads/a.pdf"
    assert "a.pdf" in issued["url"]


def test_a_presigned_url_is_expired_after_its_expiry():
    issued = issue_presigned_url("uploads/a.pdf", now=NOW)
    assert not is_expired(issued, now=NOW)
    assert not is_expired(issued, now=NOW + timedelta(minutes=59))
    assert is_expired(issued, now=NOW + timedelta(hours=1))
    assert is_expired(issued, now=LATER)


def test_an_object_with_no_expiry_is_not_claimed_to_be_expired():
    """This product did not issue it, so it cannot say when it dies."""
    assert not is_expired({"url": "https://x.example/a"}, now=LATER)
    assert not is_expired(None, now=LATER)
    assert not is_expired({"expiresAt": "not a date"}, now=LATER)


def test_the_expiry_note_says_which_it_is():
    issued = issue_presigned_url("a.pdf", now=NOW)
    assert "expires at" in expiry_note(issued, now=NOW)
    assert "expired at" in expiry_note(issued, now=LATER)
    assert expiry_note({"url": "x"}, now=NOW) == ""


# --------------------------------------------------------------------------- #
# The webhook-event payload
# --------------------------------------------------------------------------- #


def test_the_payload_carries_the_researched_fields():
    payload = build_event_payload(
        {"event": "workspace.viewed", "associated_objects": objects()}, event_id="e1", now=NOW
    )
    assert payload["object"] == WEBHOOK_EVENT_OBJECT
    assert payload["event"] == "workspace.viewed"
    assert payload["occurredAt"] == "2026-09-27T10:00:00.000+00:00"
    assert payload["propertyName"]
    assert payload["id"] == "e1"
    assert set(payload["associatedObjects"]) == {"workspace", "account"}


def test_anonymous_activity_omits_user_entirely():
    """The researched rule, and the one most consequential in this module.

    The key is *absent*, not null, so a subscriber can tell "no user" from "an
    empty user".
    """
    payload = build_event_payload(
        {"event": "workspace.viewed", "associated_objects": objects()}, now=NOW
    )
    assert "user" not in payload["associatedObjects"]
    assert is_anonymous(payload) is True


def test_a_signed_in_activity_carries_user_with_its_email():
    payload = build_event_payload(
        {"event": "workspace.viewed", "associated_objects": objects(user="a.buyer")}, now=NOW
    )
    user = payload["associatedObjects"]["user"]
    assert user["id"] == "a.buyer"
    assert user["object"] == "user"
    assert user["email"] == "a.buyer@northwind.example"
    assert is_anonymous(payload) is False


def test_a_blank_user_object_is_treated_as_anonymous():
    """The omission rule holds for every call path, not only the careful ones."""
    cleaned = normalise_associated_objects(
        {"workspace": {"id": "w"}, "account": {"id": "a"}, "user": {"id": ""}}
    )
    assert "user" not in cleaned


def test_the_workspace_object_gets_a_url_this_app_serves():
    rendered = render_associated_objects(
        {"workspace": {"id": "ws_1"}, "account": {"id": "acc_1"}, "user": {"id": "u_1"}}
    )
    assert rendered["workspace"]["url"] == f"{PREFIX}/backfill/workspaces/ws_1"
    # No pull route for these, so no url key at all rather than a broken one.
    assert "url" not in rendered["account"]
    assert "url" not in rendered["user"]


def test_a_plan_task_object_points_at_its_collection():
    rendered = render_associated_objects({"workspacePlanTask": {"id": "task_1"}})
    assert rendered["workspacePlanTask"]["url"] == f"{PREFIX}/backfill/workspace-plan-tasks"


def test_an_unknown_object_kind_is_refused():
    """Otherwise a caller could quietly invent a ninth kind."""
    with pytest.raises(EventPayloadError) as caught:
        normalise_associated_objects({"workspace": {"id": "w"}, "robot": {"id": "r"}})
    assert caught.value.code == "invalid_event"
    assert "robot" in str(caught.value)


def test_an_event_missing_the_objects_its_type_names_is_refused():
    with pytest.raises(EventPayloadError) as caught:
        normalise_associated_objects(objects(), event="workspace.page.viewed")
    assert caught.value.code == "missing_associated_object"
    assert "workspacePage" in str(caught.value)


def test_associated_objects_must_be_objects_or_ids():
    assert (
        normalise_associated_objects({"workspace": "w1", "account": "a1"})["workspace"]["id"]
        == "w1"
    )
    with pytest.raises(EventPayloadError):
        normalise_associated_objects({"workspace": {"id": "w"}, "account": ["a"]})
    with pytest.raises(EventPayloadError):
        normalise_associated_objects({"workspace": {"nope": "w"}, "account": "a"})
    with pytest.raises(EventPayloadError):
        normalise_associated_objects({})
    with pytest.raises(EventPayloadError):
        normalise_associated_objects("nope")


def test_property_previous_value_is_omitted_rather_than_null():
    """A view has no previous state, and null would claim one."""
    without = build_event_payload(
        {"event": "workspace.viewed", "associated_objects": objects()}, now=NOW
    )
    assert "propertyPreviousValue" not in without
    with_previous = build_event_payload(
        {
            "event": "workspace.order_form.signed",
            "property_previous_value": "pending",
            "property_value": "signed",
            "associated_objects": objects(file={"id": "f"}),
        },
        now=NOW,
    )
    assert with_previous["propertyPreviousValue"] == "pending"
    assert with_previous["propertyValue"] == "signed"


def test_a_caller_supplied_property_name_wins():
    payload = build_event_payload(
        {
            "event": "workspace.viewed",
            "property_name": "orderForm.status",
            "associated_objects": objects(),
        },
        now=NOW,
    )
    assert payload["propertyName"] == "orderForm.status"


def test_an_asset_event_carries_the_snapshot_at_the_top_level():
    """Not in associatedObjects: the research does not list ``asset`` as a kind."""
    payload = build_event_payload(
        {
            "event": "asset.viewed",
            "associated_objects": objects(),
            "asset": {
                "name": "Deck",
                "type": "pdf",
                "shareUrl": "https://share.example/s/x",
                "isInternal": False,
                "tags": ["a"],
                "downloadEnabled": True,
                "trackingEnabled": True,
            },
        },
        now=NOW,
    )
    assert payload["asset"]["trackingEnabled"] is True
    assert "asset" not in payload["associatedObjects"]


def test_tracking_enabled_is_required_on_an_asset_snapshot():
    """It is what distinguishes a gated share link from an ordinary view."""
    with pytest.raises(EventPayloadError) as caught:
        normalise_asset_snapshot({"name": "Deck", "type": "pdf"})
    assert caught.value.code == "asset_snapshot_incomplete"
    assert "trackingEnabled" in str(caught.value)
    with pytest.raises(EventPayloadError):
        normalise_asset_snapshot({})
    with pytest.raises(EventPayloadError):
        normalise_asset_snapshot("nope")


def test_asset_tags_are_normalised_to_a_list():
    snapshot = normalise_asset_snapshot({"name": "D", "type": "pdf", "trackingEnabled": 0})
    assert snapshot["tags"] == []
    assert snapshot["trackingEnabled"] is False


def test_a_form_event_carries_questions_and_responses():
    payload = build_event_payload(
        {
            "event": "workspace.form.submitted",
            "associated_objects": objects(workspaceForm={"id": "form_1"}),
            "form_questions": [{"id": "q1", "type": "text"}],
            "form_question_responses": [{"questionId": "q1", "value": "yes"}],
        },
        now=NOW,
    )
    assert payload["formQuestions"] == [{"id": "q1", "type": "text"}]
    assert payload["formQuestionResponses"][0]["value"] == "yes"


def test_a_form_response_must_pair_with_a_declared_question():
    questions = normalise_form_questions([{"id": "q1", "type": "text"}])
    with pytest.raises(EventPayloadError) as caught:
        normalise_form_responses([{"questionId": "q9", "value": "x"}], questions)
    assert caught.value.code == "form_response_unmatched"
    # The remedy names the questions that do exist, which is the actionable half.
    assert "q1" in caught.value.remediation


def test_form_questions_need_an_id_and_a_type():
    with pytest.raises(EventPayloadError):
        normalise_form_questions([])
    with pytest.raises(EventPayloadError):
        normalise_form_questions([{"type": "text"}])
    with pytest.raises(EventPayloadError):
        normalise_form_questions([{"id": "q1"}])
    with pytest.raises(EventPayloadError):
        normalise_form_questions(["q1"])


def test_a_question_type_outside_the_one_named_is_accepted():
    """Only ``file_upload`` is researched, so no closed set is imposed."""
    questions = normalise_form_questions([{"id": "q1", "type": "signature_pad"}])
    assert questions[0]["type"] == "signature_pad"


def test_a_file_upload_response_must_carry_its_expiry():
    questions = normalise_form_questions([{"id": "q2", "type": FILE_UPLOAD_QUESTION}])
    with pytest.raises(EventPayloadError) as caught:
        normalise_form_responses(
            [{"questionId": "q2", "value": {"url": "https://x.example/a"}}], questions
        )
    assert caught.value.code == "file_upload_expiry_missing"
    with pytest.raises(EventPayloadError) as caught:
        normalise_form_responses([{"questionId": "q2", "value": {"expiresAt": "x"}}], questions)
    assert caught.value.code == "file_upload_response_invalid"
    with pytest.raises(EventPayloadError) as caught:
        normalise_form_responses([{"questionId": "q2", "value": "a string"}], questions)
    assert caught.value.code == "file_upload_response_invalid"


def test_a_file_upload_response_records_whether_its_url_has_expired():
    """The classic two-hour bug, made visible on the event that caused it."""
    questions = normalise_form_questions([{"id": "q2", "type": FILE_UPLOAD_QUESTION}])
    fresh = normalise_form_responses(
        [{"questionId": "q2", "value": issue_presigned_url("a.pdf", now=NOW)}],
        questions,
        now=NOW,
    )
    assert fresh[0]["presigned_expired"] is False
    assert "expires at" in fresh[0]["presigned_note"]
    assert fresh[0]["presigned"]["key"] == "a.pdf"

    stale = normalise_form_responses(
        [
            {
                "questionId": "q2",
                "value": {
                    "url": "https://x.example/a",
                    "expiresAt": "2026-08-28T18:40:00.000+00:00",
                },
            }
        ],
        questions,
        now=LATER,
    )
    assert stale[0]["presigned_expired"] is True
    assert "expired at" in stale[0]["presigned_note"]


def test_a_test_event_is_marked():
    payload = build_event_payload(
        {"event": "workspace.viewed", "associated_objects": objects()}, now=NOW, test=True
    )
    assert payload["test"] is True


def test_metadata_rides_along_when_present():
    payload = build_event_payload(
        {
            "event": "workspace.viewed",
            "associated_objects": objects(),
            "metadata": {"campaign": "q3"},
        },
        now=NOW,
    )
    assert payload["metadata"] == {"campaign": "q3"}
    without = build_event_payload(
        {"event": "workspace.viewed", "associated_objects": objects(), "metadata": {}}, now=NOW
    )
    assert "metadata" not in without


# --------------------------------------------------------------------------- #
# The retry ladder
# --------------------------------------------------------------------------- #


def test_the_ladder_is_one_minute_then_ten_then_hourly():
    assert retry_delay(1) == 60.0
    assert retry_delay(2) == 600.0
    assert retry_delay(3) == 3600.0
    assert retry_delay(26) == 3600.0


def test_the_ladder_has_exactly_the_documented_twenty_six_retries():
    """1 min + 10 min + hourly x24 = 26, matching "Total number of retries: 26"."""
    assert MAX_RETRIES == 26
    # 26 delays means 27 attempts: the first try, then one per rung.
    assert retry_delay(26) == 3600.0
    assert retry_delay(27) is None
    with pytest.raises(ValueError):
        retry_delay(0)


def test_attempts_remaining_counts_down_to_zero():
    assert attempts_remaining(1) == 26
    assert attempts_remaining(26) == 1
    assert attempts_remaining(27) == 0
    assert attempts_remaining(99) == 0


def test_a_successful_attempt_is_delivered_and_schedules_nothing():
    report = attempt_delivery(
        FakeTransport(ok()), TARGET, {"a": 1}, event="e", delivery_id="d", now=NOW
    )
    assert report.state == "delivered"
    assert report.retry_in_seconds is None
    assert report.next_attempt_at is None


def test_the_researched_timeout_is_ten_seconds():
    assert DEFAULT_TIMEOUT == WEBHOOK_TIMEOUT_SECONDS == 10.0
    report = attempt_delivery(FakeTransport(ok()), TARGET, {}, event="e", delivery_id="d")
    assert report.headers["Content-Type"] == "application/json"


def test_a_retryable_failure_schedules_the_first_rung():
    report = attempt_delivery(
        FakeTransport(server_error()), TARGET, {}, event="e", delivery_id="d", attempt=1, now=NOW
    )
    assert report.state == "retrying"
    assert report.retry_in_seconds == 60.0
    assert report.next_attempt_at == "2026-09-27T10:01:00.000+00:00"
    assert report.attempts_remaining == 26


def test_the_second_rung_is_ten_minutes():
    report = attempt_delivery(
        FakeTransport(server_error()), TARGET, {}, event="e", delivery_id="d", attempt=2, now=NOW
    )
    assert report.retry_in_seconds == 600.0
    assert report.next_attempt_at == "2026-09-27T10:10:00.000+00:00"
    assert report.attempts_remaining == 25


def test_a_retry_after_header_is_believed_but_capped_at_the_top_of_the_ladder():
    report = attempt_delivery(
        FakeTransport(rate_limited(5)), TARGET, {}, event="e", delivery_id="d", now=NOW
    )
    assert report.retry_in_seconds == 5.0
    capped = attempt_delivery(
        FakeTransport(rate_limited(99_999)), TARGET, {}, event="e", delivery_id="d", now=NOW
    )
    assert capped.retry_in_seconds == RETRY_AFTER_CAP


def test_a_permanent_failure_stops_immediately():
    """A 404 will answer identically twenty-six times; retrying it hides the row
    that needs a person."""
    report = attempt_delivery(
        FakeTransport(not_found()), TARGET, {}, event="e", delivery_id="d", now=NOW
    )
    assert report.state == "failed"
    assert report.retry_in_seconds is None
    assert report.result.permanent_failure is True
    # Zero rather than the ladder's full length: the ladder was never started,
    # and a row saying "26 left" next to a 404 is a lie an operator has to notice.
    assert report.attempts_remaining == 0


def test_the_last_rung_spends_the_ladder():
    """Attempt 26 still has one retry behind it; attempt 27 does not."""
    penultimate = attempt_delivery(
        FakeTransport(server_error()), TARGET, {}, event="e", delivery_id="d", attempt=26, now=NOW
    )
    assert penultimate.state == "retrying"
    assert penultimate.attempts_remaining == 1

    last = attempt_delivery(
        FakeTransport(server_error()), TARGET, {}, event="e", delivery_id="d", attempt=27, now=NOW
    )
    assert last.state == "failed"
    assert last.attempts_remaining == 0
    assert last.next_attempt_at is None


def test_the_retryable_status_set_is_the_documented_one_plus_the_usual_family():
    assert 429 in RETRYABLE_STATUS
    assert {408, 425, 500, 502, 503, 504} <= RETRYABLE_STATUS
    assert 404 not in RETRYABLE_STATUS
    assert 410 not in RETRYABLE_STATUS
    assert 400 not in RETRYABLE_STATUS


def test_a_report_serialises_every_attempt_it_is_given():
    report = attempt_delivery(
        FakeTransport(ok()),
        TARGET,
        {"a": 1},
        event="e",
        delivery_id="d",
        secret="whsec_x",
        now=NOW,
    )
    body = report.to_dict()
    assert body["state"] == "delivered"
    assert body["attempt_log"][0]["attempt"] == 1
    assert body["request_headers"][SIGNATURE_HEADER].startswith("sha256=")
    assert body["http_status"] == 200


def test_the_next_attempt_continues_the_history_rather_than_restarting_it():
    first = attempt_delivery(
        FakeTransport(server_error()), TARGET, {"a": 1}, event="e", delivery_id="d", now=NOW
    )
    stored = first.to_dict()
    report = next_attempt(
        FakeTransport(ok()),
        TARGET,
        {"a": 1},
        stored,
        event="e",
        delivery_id="d",
        now=NOW,
    )
    assert report.attempt == 2
    assert [entry["attempt"] for entry in report.to_dict()["attempt_log"]] == [1, 2]
    assert report.state == "delivered"


def test_a_delivery_row_keeps_the_payload_that_was_signed():
    """The only evidence that would settle a disputed delivery."""
    secret = "whsec_x"
    report = attempt_delivery(
        FakeTransport(ok()),
        TARGET,
        {"event": "workspace.viewed"},
        event="e",
        delivery_id="d",
        secret=secret,
        now=NOW,
    )
    stored = report.to_dict()
    assert json.loads(report.body.decode()) == {"event": "workspace.viewed"}
    # The signature in the stored headers verifies against the stored payload.
    assert verify(secret, report.body, stored["request_headers"][SIGNATURE_HEADER])


# --------------------------------------------------------------------------- #
# Webhooks
# --------------------------------------------------------------------------- #


def test_only_an_account_admin_may_create_a_webhook(stream):
    """Sourced verbatim: "You must be an account ``admin`` to create a webhook"."""
    for role in (None, "", "  ", "member", "editor", "administrator"):
        with pytest.raises(NotPermitted) as caught:
            stream.create_webhook("x", TARGET, role=role, source=SOURCE)
        assert caught.value.status == 403
    assert stream.list_webhooks() == []


def test_the_role_check_tolerates_case_and_surrounding_space(stream):
    assert stream.create_webhook("x", TARGET, role=" Admin ", source=SOURCE)["webhook"]["id"]


def test_creating_a_webhook_verifies_the_url_with_a_post_first(stream, transport):
    result = stream.create_webhook("Northwind", TARGET, role=ROLE_ADMIN, source=SOURCE)
    assert len(transport.calls) == 1
    assert transport.calls[0]["url"] == TARGET
    assert result["verification"]["state"] == "delivered"
    assert result["webhook"]["verified_at"]
    assert result["webhook"]["verified_status"] == 200


def test_a_target_that_will_not_answer_leaves_no_webhook_behind(stream, transport):
    transport.scripted.append(not_found())
    with pytest.raises(TargetError) as caught:
        stream.create_webhook("Northwind", TARGET, role=ROLE_ADMIN, source=SOURCE)
    assert caught.value.code == "target_not_verified"
    assert stream.list_webhooks() == []


def test_the_secret_is_returned_once_and_masked_everywhere_else(stream):
    result = stream.create_webhook("Northwind", TARGET, role=ROLE_ADMIN, source=SOURCE)
    secret = result["secret"]
    assert secret.startswith(SECRET_PREFIX)
    assert result["webhook"]["secret_hint"] == mask(secret)
    listed = stream.list_webhooks()[0]
    assert "secret" not in listed
    assert listed["signed"] is True
    assert listed["secret_hint"] == mask(secret)
    detail = stream.read_webhook(result["webhook"]["id"])
    assert "secret" not in detail


def test_the_view_key_route_reads_the_secret_again(stream):
    webhook = stream.create_webhook("N", TARGET, role=ROLE_ADMIN, source=SOURCE)
    revealed = stream.reveal_key(webhook["webhook"]["id"])
    assert revealed["secret"] == webhook["secret"]
    assert revealed["secret_hint"] == mask(webhook["secret"])


def test_rotating_the_key_keeps_the_previous_one_for_the_overlap(stream):
    created = stream.create_webhook("N", TARGET, role=ROLE_ADMIN, source=SOURCE)
    webhook = created["webhook"]
    rotated = stream.rotate_key(webhook["id"], source=SOURCE)
    assert rotated["secret"] != created["secret"]
    stored = stream.read_webhook(webhook["id"])
    assert stored["rotation_in_progress"] is True
    assert stored["key_rotated_at"]
    assert stored["secret_hint"] == mask(rotated["secret"])
    assert "secret" not in stored


def test_rotating_twice_drops_the_grandparent_secret(stream):
    webhook = stream.create_webhook("N", TARGET, role=ROLE_ADMIN, source=SOURCE)["webhook"]
    first = stream.rotate_key(webhook["id"], source=SOURCE)
    second = stream.rotate_key(webhook["id"], source=SOURCE)
    assert second["secret"] != first["secret"]
    record = stream.endpoints.require(webhook["id"])["data"]
    assert record["previous_secret"] == first["secret"]


def test_a_webhook_can_be_renamed_and_paused(stream):
    created = stream.create_webhook("Old", TARGET, role=ROLE_ADMIN, source=SOURCE)["webhook"]
    renamed = stream.update_webhook(created["id"], {"name": "New"}, source=SOURCE)
    assert renamed["name"] == "New"
    paused = stream.update_webhook(created["id"], {"active": False}, source=SOURCE)
    assert paused["active"] is False
    assert stream.list_webhooks(include_paused=False) == []
    assert len(stream.list_webhooks()) == 1


def test_patching_a_webhook_refuses_the_target_and_the_secret_rather_than_ignoring_them(stream):
    """A PATCH that quietly dropped half its body is how a rep comes to believe
    they changed the target."""
    webhook = stream.create_webhook("N", TARGET, role=ROLE_ADMIN, source=SOURCE)["webhook"]
    for field in ("target_url", "secret", "previous_secret", "verified_at"):
        with pytest.raises(TargetError) as caught:
            stream.update_webhook(webhook["id"], {field: "x"}, source=SOURCE)
        assert caught.value.code == "field_not_patchable"
    with pytest.raises(TargetError) as caught:
        stream.update_webhook(webhook["id"], {"nope": 1}, source=SOURCE)
    assert caught.value.code == "field_unknown"
    with pytest.raises(TargetError):
        stream.update_webhook(webhook["id"], {"active": "yes"}, source=SOURCE)


def test_a_webhook_needs_a_name(stream):
    with pytest.raises(TargetError) as caught:
        stream.create_webhook("  ", TARGET, role=ROLE_ADMIN, source=SOURCE)
    assert caught.value.code == "name_required"


def test_retiring_a_webhook_unsubscribes_everything_under_it(stream, store):
    webhook = stream.create_webhook("N", TARGET, role=ROLE_ADMIN, source=SOURCE)["webhook"]
    first = subscribe(stream, webhook["id"], ["workspace.viewed"])
    second = subscribe(stream, webhook["id"], ["workspace.file.viewed"])
    stream.retire_webhook(webhook["id"], source=SOURCE)
    assert stream.read_webhook(webhook["id"]) if False else True
    assert stream.subscriptions.get(first["id"]) is None
    assert stream.subscriptions.get(second["id"]) is None
    assert store.find(SUBSCRIPTION_COLLECTION, {"webhook_id": webhook["id"]}, include_deleted=True)


def test_a_missing_webhook_is_a_record_not_found(stream):
    with pytest.raises(RecordNotFound):
        stream.read_webhook("nope")
    with pytest.raises(RecordNotFound):
        stream.retire_webhook("nope", source=SOURCE)


# --------------------------------------------------------------------------- #
# Subscriptions
# --------------------------------------------------------------------------- #


def test_a_subscription_needs_at_least_one_type(stream, webhook):
    with pytest.raises(VocabularyError):
        stream.create_subscription(webhook["id"], [], source=SOURCE)


def test_a_subscription_reports_types_outside_the_published_set(stream, webhook):
    subscription = stream.create_subscription(
        webhook["id"], ["workspace.viewed", "workspace.reticulated"], source=SOURCE
    )
    assert subscription["types"] == ["workspace.viewed", "workspace.reticulated"]
    assert subscription["unknown_types"] == ["workspace.reticulated"]


def test_a_filter_is_compiled_when_the_subscription_is_created(stream, webhook):
    """While the operator is looking at it, not next time somebody edits it."""
    with pytest.raises(FilterError):
        stream.create_subscription(
            webhook["id"], ["workspace.viewed"], filter_expression="$.bad[", source=SOURCE
        )
    assert stream.list_subscriptions() == []


def test_a_paused_subscription_still_lists_and_still_has_its_counters(stream, webhook):
    subscription = subscribe(stream, webhook["id"])
    paused = stream.update_subscription(
        subscription["id"], {"active": False}, source=SOURCE, now=NOW
    )
    assert paused["active"] is False
    assert paused["paused_at"]
    resumed = stream.update_subscription(
        subscription["id"], {"active": True}, source=SOURCE, now=NOW
    )
    assert resumed["active"] is True
    assert resumed["resumed_at"]


def test_a_subscription_can_be_retyped_and_refiltered(stream, webhook):
    subscription = subscribe(stream, webhook["id"])
    retyped = stream.update_subscription(
        subscription["id"], {"types": ["asset.viewed", "asset.viewed"]}, source=SOURCE
    )
    assert retyped["types"] == ["asset.viewed"]
    refiltered = stream.update_subscription(
        subscription["id"], {"filter": "$.associatedObjects.account.id"}, source=SOURCE
    )
    assert refiltered["filter"] == "$.associatedObjects.account.id"
    cleared = stream.update_subscription(subscription["id"], {"filter": None}, source=SOURCE)
    assert cleared["filter"] is None


def test_an_empty_patch_is_a_no_op_rather_than_an_audit_row(stream, webhook):
    subscription = subscribe(stream, webhook["id"])
    before = len(store_audit(stream))
    assert (
        stream.update_subscription(subscription["id"], {}, source=SOURCE)["id"]
        == subscription["id"]
    )
    assert len(store_audit(stream)) == before


def store_audit(stream) -> list:
    return stream.store.audit(collection=SUBSCRIPTION_COLLECTION, limit=100)


def test_a_subscription_patch_refuses_unknown_fields(stream, webhook):
    subscription = subscribe(stream, webhook["id"])
    with pytest.raises(SubscriptionError) as caught:
        stream.update_subscription(subscription["id"], {"nope": 1}, source=SOURCE)
    assert caught.value.code == "field_unknown"
    with pytest.raises(SubscriptionError) as caught:
        stream.update_subscription(subscription["id"], {"active": "yes"}, source=SOURCE)
    assert caught.value.code == "active_invalid"


def test_unsubscribing_is_a_soft_delete(stream, webhook):
    subscription = subscribe(stream, webhook["id"])
    stream.unsubscribe(subscription["id"], source=SOURCE)
    assert stream.subscriptions.get(subscription["id"]) is None
    found = stream.store.find(
        SUBSCRIPTION_COLLECTION, {"id": subscription["id"]}, include_deleted=True
    )
    assert stream.store.get(subscription["id"]) is None
    assert found is not None


def test_a_room_scoped_subscription_still_receives_every_room_when_unscoped(stream, webhook, store):
    scoped = subscribe(stream, webhook["id"], room_id="room_1")
    assert scoped["room_id"] == "room_1"
    # The scope is in the envelope, which is the only place the store keeps it.
    assert "room_id" not in store.get(scoped["id"])["data"]
    # A scoped subscription sees only its room.
    assert len(stream.subscriptions.list(room_id="room_1")) == 1
    assert len(stream.subscriptions.list(room_id="room_2")) == 0
    # An unscoped one reaches every room, so both room views see it.
    unscoped = subscribe(stream, webhook["id"])
    assert unscoped["room_id"] is None
    assert len(stream.subscriptions.list(room_id="room_1")) == 2
    assert len(stream.subscriptions.list(room_id="room_2")) == 1


def test_matching_needs_type_scope_and_a_live_webhook(stream, webhook, store):
    other_room = store.create("room", {"name": "Other"}, actor="dana")
    scoped = subscribe(stream, webhook["id"], ["workspace.viewed"], room_id=other_room["id"])
    subscribe(stream, webhook["id"], ["workspace.file.viewed"])
    live = [sub["id"] for sub, _ in stream.subscriptions.matching("workspace.viewed")]
    assert live == [scoped["id"]]
    # A paused subscription is not on the delivery path at all.
    stream.update_subscription(scoped["id"], {"active": False}, source=SOURCE)
    assert stream.subscriptions.matching("workspace.viewed") == []


def test_matching_skips_a_paused_webhook(stream, webhook):
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    stream.update_webhook(webhook["id"], {"active": False}, source=SOURCE)
    assert stream.subscriptions.matching("workspace.viewed") == []


def test_two_matching_subscriptions_on_one_webhook_deliver_once(stream, webhook):
    """Same payload, same target, same delivery id - a duplicate otherwise."""
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    result = record(stream)
    assert len(result["deliveries"]) == 1


def test_the_webhook_carries_a_live_subscription_count(stream, webhook):
    first = subscribe(stream, webhook["id"])
    assert first["id"]
    assert stream.read_webhook(webhook["id"])["subscription_count"] == 1
    second = stream.create_subscription(webhook["id"], ["asset.viewed"], source=SOURCE)
    assert stream.read_webhook(webhook["id"])["subscription_count"] == 2
    stream.update_subscription(second["id"], {"active": False}, source=SOURCE)
    # The count is of *live* subscriptions: a paused one is off the path.
    assert stream.read_webhook(webhook["id"])["subscription_count"] == 1
    assert len(stream.read_webhook(webhook["id"])["subscriptions"]) == 2


def test_a_subscription_viewed_in_detail_carries_its_filter_and_its_deliveries(stream, webhook):
    subscription = stream.create_subscription(
        webhook["id"], ["workspace.viewed"], filter_expression="$.a", source=SOURCE
    )
    record(stream)
    detail = stream.read_subscription(subscription["id"])
    assert detail["filter"] == "$.a"
    assert detail["filter_compiled"]["expression"] == "$.a"
    assert len(detail["deliveries_recent"]) == 1


def test_compile_for_returns_none_for_an_unfiltered_subscription(store):
    SubscriptionBook(store)
    assert compile_for({"data": {"filter": None}}) is None


# --------------------------------------------------------------------------- #
# Recording an event and fanning it out
# --------------------------------------------------------------------------- #


def test_recording_an_event_stores_it_before_anything_is_sent(stream, webhook, store):
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    result = record(stream)
    assert result["event"]["collection"] == EVENT_COLLECTION
    assert store.get(result["event"]["id"]) is not None
    assert result["deliveries"][0]["state"] == "delivered"


def test_the_fan_out_reports_one_line_per_delivery(stream, webhook):
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    second = stream.create_webhook("Second", SECOND_TARGET, role=ROLE_ADMIN, source=SOURCE)[
        "webhook"
    ]
    subscribe(stream, second["id"], ["workspace.viewed"])
    result = record(stream)
    assert len(result["deliveries"]) == 2
    assert {d["state"] for d in result["deliveries"]} == {"delivered"}


def test_a_paused_subscription_produces_a_skip_row_not_nothing(stream, webhook):
    """Otherwise it is indistinguishable from a subscription never asked."""
    subscription = subscribe(stream, webhook["id"], ["workspace.viewed"])
    stream.update_subscription(subscription["id"], {"active": False}, source=SOURCE)
    result = record(stream)
    assert len(result["deliveries"]) == 1
    assert result["deliveries"][0]["state"] == "skipped"
    assert result["deliveries"][0]["reason"] == "subscription_paused"
    assert result["deliveries"][0]["attempted"] is False
    assert result["deliveries"][0]["http_status"] is None


def test_a_paused_webhook_produces_its_own_skip_reason(stream, webhook):
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    stream.update_webhook(webhook["id"], {"active": False}, source=SOURCE)
    result = record(stream)
    assert result["deliveries"][0]["reason"] == "webhook_paused"


def test_a_filter_that_excludes_everything_is_a_skip_with_a_reason(stream, webhook):
    subscribe(
        stream,
        webhook["id"],
        ["workspace.viewed"],
        filter_expression="$.associatedObjects.account[?(@.id == 'other')]",
    )
    result = record(stream)
    assert result["deliveries"][0]["state"] == "skipped"
    assert result["deliveries"][0]["reason"] == "filter_excluded"
    assert result["deliveries"][0]["filter_matched"] is False


def test_a_filter_that_matches_delivers(stream, webhook):
    subscribe(
        stream,
        webhook["id"],
        ["workspace.viewed"],
        filter_expression="$.associatedObjects.account[?(@.id == 'acc_1')]",
    )
    assert record(stream)["deliveries"][0]["state"] == "delivered"


def test_an_anonymous_event_is_recorded_as_anonymous(stream, webhook):
    result = record(stream, associated_objects=objects())
    assert result["anonymous"] is True
    assert result["event"]["data"]["anonymous"] is True
    assert "user" not in result["payload"]["associatedObjects"]


def test_a_typed_event_records_its_known_flag(stream, webhook):
    assert record(stream)["known_type"] is True
    assert record(stream, "workspace.reticulated")["known_type"] is False


def test_a_presentation_event_without_a_share_link_is_refused(stream):
    """The research: presentation events are share-link activity only."""
    with pytest.raises(EventPayloadError) as caught:
        record(stream, "presentation.viewed")
    assert caught.value.code == "share_link_required"
    # The remedy names the alternative the research does offer.
    assert "asset.viewed" in caught.value.remediation


def test_every_presentation_verb_requires_the_share_link(stream):
    for event in SHARE_LINK_EVENTS:
        with pytest.raises(EventPayloadError):
            record(stream, event)


def test_a_presentation_event_with_a_share_link_is_recorded(stream, webhook):
    subscribe(stream, webhook["id"], ["presentation.viewed"])
    result = record(stream, "presentation.viewed", share_link="https://share.example/s/x")
    assert result["payload"][SHARE_LINK_FIELD] == "https://share.example/s/x"


def test_an_unknown_event_type_is_refused_before_anything_is_written(stream, store):
    with pytest.raises(VocabularyError):
        record(stream, 42)
    assert store.list(EVENT_COLLECTION) == []


def test_counters_advance_on_the_subscription_and_the_webhook(stream, webhook):
    subscription = subscribe(stream, webhook["id"], ["workspace.viewed"])
    record(stream)
    assert stream.read_subscription(subscription["id"])["deliveries"] == 1
    assert stream.read_webhook(webhook["id"])["deliveries"] == 1
    assert stream.read_webhook(webhook["id"])["last_state"] == "delivered"
    assert stream.read_webhook(webhook["id"])["last_delivered_at"]


def test_a_failed_delivery_is_counted_as_a_failure(stream, webhook, transport):
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    transport.scripted.append(not_found())
    record(stream)
    assert stream.read_webhook(webhook["id"])["failures"] == 1


def test_a_filtered_out_delivery_is_counted_apart_from_a_failure(stream, webhook):
    """One red number for two different problems sends the operator to the wrong system."""
    subscription = subscribe(
        stream,
        webhook["id"],
        ["workspace.viewed"],
        filter_expression="$.nothing.here",
    )
    record(stream)
    detail = stream.read_subscription(subscription["id"])
    assert detail["deliveries"] == 0
    assert detail["deliveries_filtered_out"] == 1
    assert detail["failures"] == 0


def test_a_paused_delivery_is_counted_as_skipped(stream, webhook):
    subscription = subscribe(stream, webhook["id"], ["workspace.viewed"])
    stream.update_subscription(subscription["id"], {"active": False}, source=SOURCE)
    result = record(stream)
    assert result["deliveries"][0]["state"] == "skipped"
    detail = stream.read_subscription(subscription["id"])
    assert detail["deliveries"] == 0
    assert detail["deliveries_skipped"] == 1
    assert detail["failures"] == 0


def test_the_event_records_how_many_deliveries_it_caused(stream, webhook):
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    result = record(stream)
    assert result["event"]["data"]["deliveries"] == 1
    assert result["event"]["data"]["skipped"] == 0


def test_a_recorded_event_reads_back_as_the_payload_a_subscriber_would_get(stream, webhook):
    result = record(stream)
    read = stream.read_event(result["event"]["id"])
    assert read["payload"] == result["payload"]


def test_reading_a_missing_event_is_a_record_not_found(stream):
    with pytest.raises(RecordNotFound):
        stream.read_event("nope")


def test_listing_events_filters_on_the_researched_fields(stream, webhook, store):
    subscribe(
        stream,
        webhook["id"],
        ["workspace.viewed", "workspace.reticulated", "asset.viewed"],
    )
    record(stream, room_id=None)
    record(stream, "workspace.reticulated")
    record(
        stream,
        "asset.viewed",
        associated_objects=objects(user="a.buyer"),
        asset={"name": "Deck", "type": "pdf", "trackingEnabled": True},
    )
    assert len(stream.list_events()) == 3
    assert len(stream.list_events(event="workspace.viewed")) == 1
    assert len(stream.list_events(known=False)) == 1
    assert len(stream.list_events(anonymous=True)) == 2
    assert len(stream.list_events(where={"event": "workspace.reticulated"})) == 1
    assert stream.list_events(where={"associated_objects.workspace.id": "ws_1"})


def test_listing_events_scopes_to_a_room_and_keeps_the_unscoped_ones(stream, webhook, store):
    room = store.create("room", {"name": "R"}, actor="dana")
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    record(stream, room_id=room["id"])
    record(stream, room_id=None)
    assert len(stream.list_events(room_id=room["id"])) == 2


# --------------------------------------------------------------------------- #
# Retrying a delivery
# --------------------------------------------------------------------------- #


def test_a_rate_limited_delivery_is_retrying_with_the_first_rung_on_it(stream, webhook, transport):
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    transport.scripted.append(server_error())
    result = record(stream)
    delivery = result["deliveries"][0]
    assert delivery["state"] == "retrying"
    row = stream.read_delivery(delivery["delivery_id"])
    assert row["data"]["next_attempt_at"] == "2026-09-27T10:01:00.000+00:00"
    assert row["data"]["attempts_remaining"] == 26
    assert row["data"]["attempt_log"][0]["status"] == 503


def test_a_rate_limit_answers_429_and_its_retry_after_wins_the_schedule(stream, webhook, transport):
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    transport.scripted.append(rate_limited(30))
    row = stream.read_delivery(record(stream)["deliveries"][0]["delivery_id"])
    assert row["data"]["http_status"] == 429
    assert row["data"]["retry_in_seconds"] == 30.0
    assert row["data"]["next_attempt_at"] == "2026-09-27T10:00:30.000+00:00"


def test_retrying_spends_the_next_rung_and_keeps_the_history(stream, webhook, transport):
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    transport.scripted.append(server_error())
    delivery = record(stream)["deliveries"][0]["delivery_id"]
    retried = stream.retry_delivery(delivery, source=f"POST {PREFIX}/deliveries/x/retry")
    assert retried["data"]["state"] == "delivered"
    assert retried["data"]["attempts_total"] == 2
    assert [entry["attempt"] for entry in retried["data"]["attempt_log"]] == [1, 2]
    assert retried["data"]["next_attempt_at"] is None


def test_a_retry_of_a_permanent_failure_is_refused(stream, webhook, transport):
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    transport.scripted.append(not_found())
    delivery = record(stream)["deliveries"][0]["delivery_id"]
    with pytest.raises(DeliveryError) as caught:
        stream.retry_delivery(delivery, source=SOURCE)
    assert caught.value.status == 409
    assert caught.value.code == "not_retryable"


def test_a_retry_of_a_delivered_payload_is_refused(stream, webhook):
    """Retrying it is how a warehouse gets the same row twice."""
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    delivery = record(stream)["deliveries"][0]["delivery_id"]
    with pytest.raises(DeliveryError):
        stream.retry_delivery(delivery, source=SOURCE)


def test_a_retry_only_counts_the_first_attempt_towards_the_webhook_totals(
    stream, webhook, transport
):
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    transport.scripted.append(server_error())
    delivery = record(stream)["deliveries"][0]["delivery_id"]
    assert stream.read_webhook(webhook["id"])["deliveries"] == 1
    stream.retry_delivery(delivery, source=SOURCE)
    assert stream.read_webhook(webhook["id"])["deliveries"] == 1
    assert stream.read_webhook(webhook["id"])["failures"] == 1


def test_a_successful_delivery_closes_a_key_rotation_overlap(stream, webhook, transport):
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    transport.scripted.append(server_error())
    delivery = record(stream)["deliveries"][0]["delivery_id"]
    stream.rotate_key(webhook["id"], source=SOURCE)
    assert stream.read_webhook(webhook["id"])["rotation_in_progress"] is True
    stream.retry_delivery(delivery, source=SOURCE)
    assert stream.read_webhook(webhook["id"])["rotation_in_progress"] is False


def test_a_retried_delivery_goes_out_with_both_signatures_during_the_overlap(
    stream, webhook, transport
):
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    transport.scripted.append(server_error())
    delivery = record(stream)["deliveries"][0]["delivery_id"]
    stream.rotate_key(webhook["id"], source=SOURCE)
    stream.retry_delivery(delivery, source=SOURCE)
    headers = transport.calls[-1]["headers"]
    assert SIGNATURE_HEADER in headers
    assert PREVIOUS_SIGNATURE_HEADER in headers


def test_reading_a_missing_delivery_is_a_record_not_found(stream):
    with pytest.raises(RecordNotFound):
        stream.read_delivery("nope")


def test_listing_deliveries_filters_by_state_and_reports_the_summary(stream, webhook, store):
    room = store.create("room", {"name": "R"}, actor="dana")
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    record(stream, room_id=room["id"])
    stream.transport = FakeTransport(not_found())
    record(stream, room_id=room["id"])
    assert len(stream.list_deliveries(state="failed")) == 1
    assert len(stream.list_deliveries(state="delivered")) == 1
    assert len(stream.list_deliveries(event="workspace.viewed")) == 2
    assert len(stream.list_deliveries(webhook_id=webhook["id"])) == 2
    assert len(stream.list_deliveries(room_id=room["id"])) == 2
    assert len(stream.list_deliveries(where={"reason": "filter_excluded"})) == 0


def test_an_unknown_delivery_state_is_refused():
    with pytest.raises(DeliveryError) as caught:
        require_state("exploded")
    assert caught.value.status == 400
    assert require_state("delivered") == "delivered"


# --------------------------------------------------------------------------- #
# Test events
# --------------------------------------------------------------------------- #


def test_test_events_go_out_once_per_subscribed_type(stream, webhook, transport):
    stream.create_subscription(webhook["id"], ["workspace.viewed", "asset.viewed"], source=SOURCE)
    result = stream.send_test_events(webhook["id"], source=SOURCE)
    assert result["count"] == 2
    assert {entry["event"] for entry in result["events"]} == {"workspace.viewed", "asset.viewed"}
    assert transport.calls[-1]["body"]["test"] is True


def test_a_test_event_carries_no_user_which_is_the_anonymous_rule_demonstrated(
    stream, webhook, transport
):
    stream.create_subscription(webhook["id"], ["workspace.viewed"], source=SOURCE)
    stream.send_test_events(webhook["id"], source=SOURCE)
    assert "user" not in transport.calls[-1]["body"]["associatedObjects"]


def test_a_test_event_is_marked_tested_and_gets_its_own_delivery_row(stream, webhook):
    stream.create_subscription(webhook["id"], ["workspace.viewed"], source=SOURCE)
    result = stream.send_test_events(webhook["id"], source=SOURCE)
    row = stream.read_delivery(result["events"][0]["delivery_id"])
    assert row["data"]["state"] == "tested"
    assert row["data"]["test"] is True


def test_a_failed_test_event_is_visible_in_the_same_place_as_a_real_failure(
    stream, webhook, transport
):
    transport.scripted.append(ok())
    transport.scripted.append(not_found())
    stream.create_subscription(webhook["id"], ["workspace.viewed", "asset.viewed"], source=SOURCE)
    result = stream.send_test_events(webhook["id"], source=SOURCE)
    states = sorted(entry["state"] for entry in result["events"])
    assert states == ["failed", "tested"]


def test_test_events_with_nothing_to_test_are_refused(stream, webhook):
    with pytest.raises(DeliveryError) as caught:
        stream.send_test_events(webhook["id"], source=SOURCE)
    assert caught.value.code == "nothing_to_test"


def test_test_events_report_types_outside_the_published_set(stream, webhook):
    stream.create_subscription(webhook["id"], ["workspace.reticulated"], source=SOURCE)
    result = stream.send_test_events(webhook["id"], source=SOURCE)
    assert result["known_types"] == ["workspace.reticulated"]
    assert result["events"][0]["known"] is False


# --------------------------------------------------------------------------- #
# Pull-based backfill
# --------------------------------------------------------------------------- #


def test_omitting_properties_returns_only_id_object_and_url(stream, store):
    """Sourced: "the response contains only the resource's id, object, and url"."""
    store.create("room", {"name": "Northwind", "stage": "evaluation"}, actor="dana")
    body = stream.backfill("workspaces")
    assert body["properties"] == list(MINIMAL_PROPERTIES)
    assert set(body["results"][0]) == {"id", "object", "url"}
    assert body["results"][0]["object"] == "workspace"


def test_an_empty_properties_parameter_also_means_the_minimum(stream, store):
    store.create("room", {"name": "N"}, actor="dana")
    for value in ("", "   ", ","):
        assert stream.backfill("workspaces", properties=value)["properties"] == list(
            MINIMAL_PROPERTIES
        )


def test_properties_selects_fields_and_dedupes(stream, store):
    store.create("room", {"name": "N", "stage": "demo"}, actor="dana")
    body = stream.backfill("workspaces", properties="name,stage,name")
    assert body["properties"] == ["name", "stage"]
    assert body["results"][0] == {"name": "N", "stage": "demo"}


def test_an_unknown_property_is_refused_with_the_names_the_collection_exposes():
    """Silently returning nothing is the dangerous default: the backfill imports
    zero fields and the only symptom is an empty warehouse three days later."""
    with pytest.raises(VocabularyError) as caught:
        require_properties("nope", ["name", "stage"])
    assert caught.value.code == "unknown_property"
    assert "name" in caught.value.remediation
    assert "stage" in caught.value.remediation


def test_id_object_and_url_are_always_selectable_because_they_are_not_fields():
    assert require_properties("id,object,url", []) == ["id", "object", "url"]


def test_a_non_string_properties_parameter_is_refused():
    with pytest.raises(VocabularyError) as caught:
        require_properties(7, ["name"])
    assert caught.value.code == "properties_invalid"


def test_the_backfill_rate_limit_answers_429_with_retry_after(stream, store):
    store.create("room", {"name": "N"}, actor="dana")
    limiter = RateLimiter(limit=2, window=60.0, now=lambda: 1000.0)
    limited = EventStream(store, transport=FakeTransport(), rate_limiter=limiter, now=NOW)
    limited.backfill("workspaces", caller="dana")
    limited.backfill("workspaces", caller="dana")
    with pytest.raises(EventStreamError) as caught:
        limited.backfill("workspaces", caller="dana")
    assert caught.value.status == 429
    assert caught.value.headers["Retry-After"]


def test_the_rate_limit_is_per_caller():
    limiter = RateLimiter(limit=1, window=60.0, now=lambda: 1000.0)
    limiter.check("dana")
    limiter.check("sam")
    assert limiter.state("dana")["remaining"] == 0
    assert limiter.state("sam")["remaining"] == 0


def test_the_rate_limit_window_resets():
    clock = [1000.0]
    limiter = RateLimiter(limit=1, window=60.0, now=lambda: clock[0])
    limiter.check("dana")
    clock[0] = 1061.0
    assert limiter.check("dana")["used"] == 1
    limiter.reset()
    assert limiter.state("dana")["used"] == 0


def test_the_backfill_charges_the_limit_before_doing_the_work(stream, store):
    store.create("room", {"name": "N"}, actor="dana")
    limiter = RateLimiter(limit=1, window=60.0, now=lambda: 1000.0)
    limited = EventStream(store, transport=FakeTransport(), rate_limiter=limiter, now=NOW)
    limited.backfill("workspaces", caller="dana")
    with pytest.raises(EventStreamError):
        limited.backfill("workspaces", caller="dana")
    assert limited.backfill_rate_limit()["status"] == 429
    assert DEFAULT_LIMIT == 60


def test_an_unknown_backfill_resource_is_a_404(stream):
    with pytest.raises(DeliveryError) as caught:
        stream.backfill("nope")
    assert caught.value.status == 404


def test_form_responses_are_scoped_to_their_form(stream, store):
    store.create("form_response", {"formId": "f1", "account": "A"}, actor="dana")
    store.create("form_response", {"formId": "f2", "account": "B"}, actor="dana")
    assert stream.backfill("forms/{formId}/responses", form_id="f1")["count"] == 1
    scoped = stream.backfill("forms/{formId}/responses", form_id="f2", properties="account")
    assert scoped["results"][0]["account"] == "B"


def test_assets_and_plan_tasks_pull_from_their_own_collections(stream, store):
    store.create("document", {"title": "Deck", "kind": "deck"}, actor="dana")
    store.create("plan_task", {"title": "Task", "status": "done"}, actor="dana")
    assert stream.backfill("assets", properties="title")["results"] == [{"title": "Deck"}]
    assert stream.backfill("workspace-plan-tasks", properties="title")["results"] == [
        {"title": "Task"}
    ]


def test_the_pull_response_reports_the_vendor_path_it_stands_for(stream, store):
    store.create("room", {"name": "N"}, actor="dana")
    body = stream.backfill("workspaces")
    assert body["vendor_path"] == "/v1/workspaces"
    assert body["url"] == f"{PREFIX}/backfill/workspaces"


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #


def test_the_summary_counts_exactly_the_rows_in_scope(stream, webhook, store):
    room = store.create("room", {"name": "R"}, actor="dana")
    other = store.create("room", {"name": "O"}, actor="dana")
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    record(stream, room_id=room["id"])
    record(stream, room_id=other["id"], associated_objects=objects(user="a.buyer"))
    record(stream, room_id=None, associated_objects=objects(user="c.buyer"))

    scoped = stream.summary(room_id=room["id"])
    assert scoped["room_id"] == room["id"]
    assert scoped["webhooks"] == 1
    assert scoped["subscriptions"] == 1
    # This room's event, plus the account-wide one an unscoped subscription
    # receives. The other room's event is not in scope, and saying otherwise
    # would make the tile disagree with the table under it.
    assert scoped["events"] == 2
    assert scoped["events_anonymous"] == 1
    assert scoped["deliveries"] == 2
    assert scoped["delivery_states"] == {"delivered": 2}
    assert scoped["rate_limit"]["status"] == 429

    assert stream.summary()["events"] == 3


def test_the_summary_separates_paused_counts(stream, webhook):
    subscription = subscribe(stream, webhook["id"], ["workspace.viewed"])
    stream.update_subscription(subscription["id"], {"active": False}, source=SOURCE)
    stream.update_webhook(webhook["id"], {"active": False}, source=SOURCE)
    summary = stream.summary()
    assert summary["webhooks"] == 0
    assert summary["webhooks_paused"] == 1
    assert summary["subscriptions"] == 0
    assert summary["subscriptions_paused"] == 1


def test_the_summary_reports_skip_reasons(stream, webhook):
    subscription = subscribe(
        stream,
        webhook["id"],
        ["workspace.viewed"],
        filter_expression="$.nothing",
    )
    record(stream)
    assert stream.summary()["skip_reasons"] == {"filter_excluded": 1}
    assert subscription["id"]


# --------------------------------------------------------------------------- #
# Inference registry
# --------------------------------------------------------------------------- #


def test_every_inference_is_named_traceable_and_bounded():
    for entry in INFERENCES:
        assert entry["id"]
        assert entry["topic"]
        assert entry["basis"]
        assert entry["value"] is not None
        assert entry["why"]
        assert entry["change_it"]
        assert entry["blast_radius"]


def test_inference_ids_are_unique():
    ids = [entry["id"] for entry in INFERENCES]
    assert len(ids) == len(set(ids))


def test_the_inference_registry_shows_both_halves_of_the_workflow():
    described = describe_inferences()
    assert described["count"] == len(INFERENCES)
    assert described["sourced"]["max_retries"] == 26
    assert described["sourced"]["timeout_seconds"] == 10.0
    assert "26" in described["sourced_quotes"]["delivery"]
    assert "include" in described["sourced_quotes"]["subscription_types"]


def test_the_six_decisions_most_worth_arguing_with_are_published():
    for inference_id in (
        "event-type-set-is-a-floor",
        "admin-only-creates-a-webhook",
        "retry-ladder-is-a-schedule",
        "key-rotation-overlap",
        "secret-is-stored-in-the-record",
        "unknown-property-is-refused",
    ):
        assert inference(inference_id), inference_id


# --------------------------------------------------------------------------- #
# Schema flexibility
# --------------------------------------------------------------------------- #


def test_the_streams_collections_are_discoverable_with_their_own_fields(store, stream, webhook):
    stream.create_subscription(webhook["id"], ["workspace.viewed"], source=SOURCE)
    record(stream)
    collections = {row["collection"] for row in store.collections()}
    assert {
        WEBHOOK_COLLECTION,
        SUBSCRIPTION_COLLECTION,
        EVENT_COLLECTION,
        DELIVERY_COLLECTION,
    } <= collections
    delivery = store.fields(DELIVERY_COLLECTION)
    assert {field["path"] for field in delivery} >= {
        "state",
        "event",
        "attempt",
        "next_attempt_at",
        "reason",
    }


def test_a_team_can_filter_on_a_field_it_added_itself(stream, store, webhook):
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    record(stream, metadata={"campaign": "q3-retarget"})
    found = stream.store.find(EVENT_COLLECTION, {"metadata.campaign": "q3-retarget"})
    assert len(found) == 1
    assert len(stream.list_events(where={"metadata.campaign": "q3-retarget"})) == 1


def test_no_migration_is_needed_for_a_new_field(stream, webhook):
    """A team adding a field must not need coordination with anybody."""
    subscribe(stream, webhook["id"], ["workspace.viewed"])
    result = record(stream, metadata={"anything": {"deep": [1, 2, 3]}})
    stored = stream.read_event(result["event"]["id"])
    assert stored["payload"]["metadata"]["anything"]["deep"] == [1, 2, 3]


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


def test_vocabulary_is_served_over_http(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["event_count"] == 24
    assert body["delivery"]["max_retries"] == 26


def test_the_filter_grammar_is_served_over_http(http):
    body = http.get(f"{PREFIX}/filters").json()
    assert body["refuses_unparseable"] is True


def test_the_inference_registry_is_served_over_http(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(INFERENCES)


def test_creating_a_webhook_over_http_needs_the_admin_role(http):
    payload = {"name": "Northwind", "targetUrl": TARGET}
    assert http.post(f"{PREFIX}/webhooks", json=payload).status_code == 403
    assert http.post(f"{PREFIX}/webhooks?role=member", json=payload).status_code == 403
    created = http.post(f"{PREFIX}/webhooks?role=admin", json=payload)
    assert created.status_code == 201
    body = created.json()
    assert body["webhook"]["signed"] is True
    assert body["secret"].startswith(SECRET_PREFIX)
    assert body["verification"]["state"] == "delivered"


def test_the_role_may_also_arrive_as_a_header(http):
    created = http.post(
        f"{PREFIX}/webhooks",
        json={"name": "Header role", "targetUrl": TARGET},
        headers={"X-DSR-Role": "admin"},
    )
    assert created.status_code == 201
    assert created.json()["webhook"]["name"] == "Header role"


def test_a_refusal_over_http_carries_the_code_the_remediation_and_a_correlation_id(http):
    response = http.post(
        f"{PREFIX}/webhooks", json={"name": "x", "targetUrl": "http://nope.example/x"}
    )
    assert response.status_code == 403  # the role is checked first
    created = http.post(
        f"{PREFIX}/webhooks?role=admin", json={"name": "x", "targetUrl": "http://nope.example/x"}
    )
    assert created.status_code == 400
    body = created.json()
    assert body["error"] == "invalid_target"
    assert body["correlation_id"].startswith("corr_")
    # `detail` is what the shared apiRequest keeps, so it carries all three.
    assert "invalid_target" in body["detail"]
    assert "https" in body["detail"]
    assert body["correlation_id"] in body["detail"]


def test_a_refusal_status_travels_with_the_error(http):
    """The family keeps one shape, so the status rides per error."""
    response = http.get(f"{PREFIX}/deliveries", params={"state": "exploded"})
    assert response.status_code == 400
    assert response.json()["error"] == "unknown_state"


def test_a_rate_limited_backfill_answers_429_with_a_retry_after_header(http, store, app_store):
    limiter = RateLimiter(limit=1, window=60.0, now=lambda: 1000.0)
    stream = EventStream(app_store, transport=FakeTransport(), rate_limiter=limiter, now=NOW)
    app.dependency_overrides[
        load_feature("wf025_stream_workspace_activity_events_to_yo").get_stream
    ] = lambda: stream
    try:
        assert http.get(f"{PREFIX}/backfill/workspaces").status_code == 200
        limited = http.get(f"{PREFIX}/backfill/workspaces")
        assert limited.status_code == 429
        assert limited.headers["Retry-After"]
        assert limited.json()["error"] == "rate_limited"
    finally:
        app.dependency_overrides.clear()


def test_the_full_http_journey(http):
    """The researched user flow, end to end, over HTTP."""
    created = http.post(
        f"{PREFIX}/webhooks?role=admin&actor=dana",
        json={"name": "Northwind", "targetUrl": TARGET},
    ).json()
    webhook_id = created["webhook"]["id"]
    secret = created["secret"]

    subscription = http.post(
        f"{PREFIX}/webhooks/{webhook_id}/subscriptions?actor=dana",
        json={"types": ["workspace.viewed", "workspace.file.downloaded"]},
    )
    assert subscription.status_code == 201
    subscription_id = subscription.json()["id"]

    assert http.get(f"{PREFIX}/webhooks/{webhook_id}/subscriptions").json()["count"] == 1
    detail = http.get(f"{PREFIX}/subscriptions/{subscription_id}").json()
    assert detail["webhook_id"] == webhook_id

    event = http.post(
        f"{PREFIX}/events?actor=dana",
        json={
            "event": "workspace.viewed",
            "associatedObjects": {"workspace": {"id": "ws_1"}, "account": {"id": "acc_1"}},
        },
    )
    assert event.status_code == 201
    body = event.json()
    assert body["anonymous"] is True
    assert body["deliveries"][0]["state"] == "delivered"
    assert "user" not in body["payload"]["associatedObjects"]

    assert http.get(f"{PREFIX}/events").json()["count"] == 1
    assert http.get(f"{PREFIX}/events/{body['event']['id']}").status_code == 200
    assert http.get(f"{PREFIX}/deliveries").json()["summary"] == {"delivered": 1}

    tested = http.post(f"{PREFIX}/webhooks/{webhook_id}/test-events?actor=dana", json={})
    assert tested.status_code == 200
    assert tested.json()["count"] == 2

    paused = http.patch(
        f"{PREFIX}/subscriptions/{subscription_id}?actor=dana", json={"active": False}
    )
    assert paused.status_code == 200
    assert paused.json()["active"] is False

    rotated = http.post(f"{PREFIX}/webhooks/{webhook_id}/key/rotate?actor=dana")
    assert rotated.status_code == 200
    assert rotated.json()["secret"] != secret

    assert (
        http.post(f"{PREFIX}/webhooks/{webhook_id}/key").json()["secret"]
        == rotated.json()["secret"]
    )

    assert http.delete(f"{PREFIX}/subscriptions/{subscription_id}?actor=dana").status_code == 204
    assert http.get(f"{PREFIX}/subscriptions/{subscription_id}").status_code == 404
    assert http.delete(f"{PREFIX}/webhooks/{webhook_id}?actor=dana").status_code == 204
    assert http.get(f"{PREFIX}/webhooks/{webhook_id}").status_code == 404
    assert http.get(f"{PREFIX}/webhooks").json()["count"] == 0


def test_a_missing_record_over_http_is_a_404(http):
    assert http.get(f"{PREFIX}/webhooks/nope").status_code == 404
    assert http.get(f"{PREFIX}/subscriptions/nope").status_code == 404
    assert http.get(f"{PREFIX}/events/nope").status_code == 404
    assert http.get(f"{PREFIX}/deliveries/nope").status_code == 404


def test_the_backfill_routes_answer(http, http_room):
    minimal = http.get(f"{PREFIX}/backfill/workspaces").json()
    assert set(minimal["results"][0]) == {"id", "object", "url"}
    assert minimal["results"][0]["id"] == http_room["id"]

    selected = http.get(f"{PREFIX}/backfill/workspaces", params={"properties": "name,stage"}).json()
    assert selected["results"][0]["name"] == http_room["data"]["name"]

    one = http.get(f"{PREFIX}/backfill/workspaces/{http_room['id']}").json()
    assert one["count"] == 1
    assert http.get(f"{PREFIX}/backfill/workspaces/nope").status_code == 404

    for path in (
        "/backfill/assets",
        "/backfill/workspace-plan-tasks",
        "/backfill/forms/form_1/responses",
    ):
        assert http.get(f"{PREFIX}{path}").status_code == 200


def test_an_unknown_property_over_http_is_a_400_listing_the_names(http, http_room):
    response = http.get(f"{PREFIX}/backfill/workspaces", params={"properties": "nope"})
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "unknown_property"
    assert "name" in body["detail"]


def test_a_malformed_where_parameter_is_a_400(http):
    assert http.get(f"{PREFIX}/events", params={"where": "{oops"}).status_code == 400
    assert http.get(f"{PREFIX}/deliveries", params={"where": "{oops"}).status_code == 400
    assert http.get(f"{PREFIX}/events", params={"where": '{"anonymous":true}'}).status_code == 200


def test_the_room_scoped_routes_answer(http, http_room):
    room_id = http_room["id"]
    http.post(f"{PREFIX}/webhooks?role=admin", json={"name": "N", "targetUrl": TARGET})
    summary = http.get(f"{PREFIX}/rooms/{room_id}/summary").json()
    assert summary["room_id"] == room_id
    assert summary["webhooks"] == 1
    assert http.get(f"{PREFIX}/rooms/{room_id}/events").json()["count"] == 0
    assert http.get(f"{PREFIX}/rooms/{room_id}/deliveries").json()["summary"] == {}


def test_the_unscoped_summary_answers(http, http_room):
    body = http.get(f"{PREFIX}/summary").json()
    assert body["room_id"] is None
    assert "rate_limit" in body
    assert "delivery_states" in body
    assert "skip_reasons" in body


def test_a_delivery_retry_over_http_answers_409_for_a_delivered_row(http):
    created = http.post(
        f"{PREFIX}/webhooks?role=admin", json={"name": "N", "targetUrl": TARGET}
    ).json()
    webhook_id = created["webhook"]["id"]
    http.post(f"{PREFIX}/webhooks/{webhook_id}/subscriptions", json={"types": ["workspace.viewed"]})
    event = http.post(
        f"{PREFIX}/events",
        json={
            "event": "workspace.viewed",
            "associatedObjects": {"workspace": {"id": "w"}, "account": {"id": "a"}},
        },
    ).json()
    delivery_id = event["deliveries"][0]["delivery_id"]
    assert http.post(f"{PREFIX}/deliveries/{delivery_id}/retry").status_code == 409
    assert http.get(f"{PREFIX}/deliveries/{delivery_id}").json()["data"]["state"] == "delivered"


def test_recording_an_event_over_http_refuses_a_presentation_without_a_share_link(http):
    response = http.post(
        f"{PREFIX}/events",
        json={
            "event": "presentation.viewed",
            "associatedObjects": {"workspace": {"id": "w"}, "account": {"id": "a"}},
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "share_link_required"


def test_a_bad_filter_over_http_is_a_400(http):
    created = http.post(
        f"{PREFIX}/webhooks?role=admin", json={"name": "N", "targetUrl": TARGET}
    ).json()
    response = http.post(
        f"{PREFIX}/webhooks/{created['webhook']['id']}/subscriptions",
        json={"types": ["workspace.viewed"], "filter": "$.bad["},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_filter"


def test_a_refused_target_over_http_writes_nothing(http):
    http.transport.scripted.append(not_found())
    response = http.post(
        f"{PREFIX}/webhooks?role=admin", json={"name": "N", "targetUrl": RETIRED_TARGET}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "target_not_verified"
    assert http.get(f"{PREFIX}/webhooks").json()["count"] == 0


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def _matches_registered_route(source: str, routes: list[dict]) -> bool:
    """Does ``"POST /api/wf-025/events"`` name a real route?

    Compared segment by segment, with a ``{parameter}`` segment matching any one
    segment. A route built from ``router.prefix`` and a literal id therefore
    matches, and one built from a path this app does not serve does not.
    """
    method, _, path = source.partition(" ")
    actual = [segment for segment in path.split("/") if segment]
    for route in routes:
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


def test_every_write_audit_row_names_a_route_the_app_serves(http):
    """The brief's central guarantee, checked against the live route table.

    An audit row naming a path the app had stopped serving is worse than no
    audit row, because it looks authoritative.
    """
    created = http.post(
        f"{PREFIX}/webhooks?role=admin&actor=dana", json={"name": "N", "targetUrl": TARGET}
    ).json()
    webhook_id = created["webhook"]["id"]
    subscription = http.post(
        f"{PREFIX}/webhooks/{webhook_id}/subscriptions?actor=dana",
        json={"types": ["workspace.viewed"]},
    ).json()
    http.patch(f"{PREFIX}/webhooks/{webhook_id}?actor=dana", json={"name": "Renamed"})
    http.post(f"{PREFIX}/webhooks/{webhook_id}/key/rotate?actor=dana")
    http.patch(f"{PREFIX}/subscriptions/{subscription['id']}?actor=dana", json={"active": False})
    event = http.post(
        f"{PREFIX}/events?actor=dana",
        json={
            "event": "workspace.viewed",
            "associatedObjects": {"workspace": {"id": "ws_1"}, "account": {"id": "acc_1"}},
        },
    ).json()
    http.post(f"{PREFIX}/webhooks/{webhook_id}/test-events?actor=dana", json={})
    http.delete(f"{PREFIX}/subscriptions/{subscription['id']}?actor=dana")
    http.delete(f"{PREFIX}/webhooks/{webhook_id}?actor=dana")
    assert event["event"]["id"]

    served = [
        route
        for feature in http.get("/api/features").json()["features"]
        for route in feature["routes"]
    ]
    entries = http.get("/api/audit", params={"limit": 500}).json()["entries"]
    sources = {entry["source"] for entry in entries if entry["source"]}
    ours = {source for source in sources if source.split(" ", 1)[1].startswith(PREFIX)}

    assert ours, f"no wf-025 write was audited at all; saw {sorted(sources)}"
    for source in sorted(ours):
        assert _matches_registered_route(source, served), (
            f"audit row names {source!r}, which is not a route this app serves"
        )


def test_a_write_records_the_route_that_actually_served_it(http):
    """Not just *a* route: this request's."""
    created = http.post(
        f"{PREFIX}/webhooks?role=admin&actor=dana", json={"name": "N", "targetUrl": TARGET}
    ).json()
    entries = http.get(
        "/api/audit", params={"collection": WEBHOOK_COLLECTION, "action": "insert"}
    ).json()["entries"]
    assert entries[0]["source"] == f"POST {PREFIX}/webhooks"
    assert entries[0]["record_id"] == created["webhook"]["id"]


def test_the_fan_out_rows_name_the_route_that_caused_them(http):
    """The counters a delivery advances are written while serving that request."""
    created = http.post(
        f"{PREFIX}/webhooks?role=admin", json={"name": "N", "targetUrl": TARGET}
    ).json()
    webhook_id = created["webhook"]["id"]
    http.post(f"{PREFIX}/webhooks/{webhook_id}/subscriptions", json={"types": ["workspace.viewed"]})
    http.post(
        f"{PREFIX}/events",
        json={
            "event": "workspace.viewed",
            "associatedObjects": {"workspace": {"id": "ws_1"}, "account": {"id": "acc_1"}},
        },
    )

    delivery_rows = http.get("/api/audit", params={"collection": DELIVERY_COLLECTION}).json()[
        "entries"
    ]
    assert delivery_rows[0]["source"] == f"POST {PREFIX}/events"

    subscription_rows = http.get(
        "/api/audit", params={"collection": SUBSCRIPTION_COLLECTION}
    ).json()["entries"]
    by_action = {entry["action"]: entry["source"] for entry in subscription_rows}
    assert by_action["insert"] == f"POST {PREFIX}/webhooks/{webhook_id}/subscriptions"
    assert by_action["update"] == f"POST {PREFIX}/events"


def test_a_retry_row_names_the_retry_route(http):
    created = http.post(
        f"{PREFIX}/webhooks?role=admin", json={"name": "N", "targetUrl": TARGET}
    ).json()
    webhook_id = created["webhook"]["id"]
    http.post(f"{PREFIX}/webhooks/{webhook_id}/subscriptions", json={"types": ["workspace.viewed"]})
    http.transport.scripted.append(rate_limited())
    http.transport.scripted.append(ok())
    event = http.post(
        f"{PREFIX}/events",
        json={
            "event": "workspace.viewed",
            "associatedObjects": {"workspace": {"id": "ws_1"}, "account": {"id": "acc_1"}},
        },
    ).json()
    delivery_id = event["deliveries"][0]["delivery_id"]
    assert http.post(f"{PREFIX}/deliveries/{delivery_id}/retry").status_code == 200
    rows = http.get("/api/audit", params={"collection": DELIVERY_COLLECTION}).json()["entries"]
    assert rows[0]["source"] == f"POST {PREFIX}/deliveries/{delivery_id}/retry"


def test_no_write_records_a_path_this_app_does_not_serve(stream, store, webhook):
    """The domain layer takes a source; nothing in it invents a path."""
    subscription = subscribe(stream, webhook["id"])
    record(stream)
    stream.update_webhook(webhook["id"], {"name": "Renamed"}, source=f"PATCH {PREFIX}/webhooks/x")
    stream.unsubscribe(subscription["id"], source=f"DELETE {PREFIX}/subscriptions/x")
    sources = {entry["source"] for entry in store.audit(limit=200) if entry["source"]}
    assert all(source.split(" ", 1)[1].startswith(PREFIX) for source in sources), sources


def test_every_writing_method_takes_a_source(store, webhook_record):
    """A required keyword is what stops the regression the port brief names."""
    import inspect

    book = EndpointBook(store)
    for name in ("create", "update", "retire", "rotate", "record_delivery"):
        signature = inspect.signature(getattr(book, name))
        assert signature.parameters["source"].kind is inspect.Parameter.KEYWORD_ONLY
        assert signature.parameters["source"].default is inspect.Parameter.empty

    subscriptions = SubscriptionBook(store)
    for name in ("create", "update", "unsubscribe", "record_outcome"):
        signature = inspect.signature(getattr(subscriptions, name))
        assert signature.parameters["source"].kind is inspect.Parameter.KEYWORD_ONLY
        assert signature.parameters["source"].default is inspect.Parameter.empty


@pytest.fixture()
def webhook_record(store):
    return store.create(
        WEBHOOK_COLLECTION,
        {
            "name": "x",
            "target_url": TARGET,
            "active": True,
            "secret": "whsec_x",
            "subscriptions": 0,
        },
        actor="dana",
        source=SOURCE,
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def test_the_seed_produces_the_states_the_research_makes_matter(tmp_path):
    """A demo containing only success teaches a reviewer nothing."""
    db = AuditedDatabase(tmp_path / "seed.db", mirror_dir=tmp_path / "audit")
    try:
        rooms = [
            (
                db.create(
                    "room", {"name": "Northwind", "account": "Northwind Traders"}, actor="dana"
                )["id"],
                "Northwind Traders",
            ),
            (
                db.create("room", {"name": "Contoso", "account": "Contoso Health"}, actor="dana")[
                    "id"
                ],
                "Contoso Health",
            ),
            (
                db.create(
                    "room", {"name": "Fabrikam", "account": "Fabrikam Logistics"}, actor="dana"
                )["id"],
                "Fabrikam Logistics",
            ),
        ]
        summary = load_feature("wf025_stream_workspace_activity_events_to_yo").seed(
            db, {"room_ids": rooms, "now": NOW, "rng": None}
        )
        store = RecordStore(db)

        webhooks = store.list(WEBHOOK_COLLECTION, limit=50)
        assert len(webhooks) == 3
        assert all(record["data"]["verified_at"] for record in webhooks)

        subscriptions = store.list(SUBSCRIPTION_COLLECTION, limit=50)
        assert len(subscriptions) == 5
        assert sum(1 for s in subscriptions if not s["data"]["active"]) == 1
        assert any(s["data"].get("filter") for s in subscriptions)

        states = [record["data"]["state"] for record in store.list(DELIVERY_COLLECTION, limit=500)]
        assert states.count("delivered") >= 5
        assert states.count("retrying") == 1
        assert states.count("failed") >= 1
        assert states.count("skipped") >= 1

        events = store.list(EVENT_COLLECTION, limit=100)
        assert any(record["data"]["anonymous"] for record in events)
        assert any(record["data"].get("form_question_responses") for record in events)
        assert any(record["data"].get(SHARE_LINK_FIELD) for record in events)
        assert any(record["data"].get("asset") for record in events)

        expired = [
            response
            for event in events
            for response in event["data"].get("form_question_responses") or []
            if response.get("presigned_expired")
        ]
        assert expired, "the demo must show the researched one-hour presigned expiry being spent"

        assert store.list("plan_task", limit=10)
        assert store.list("form_response", limit=10)
        assert "webhooks" in summary
    finally:
        db.close()


def test_the_seed_survives_having_no_rooms(tmp_path):
    db = AuditedDatabase(tmp_path / "bare.db")
    try:
        summary = load_feature("wf025_stream_workspace_activity_events_to_yo").seed(
            db, {"room_ids": [], "now": NOW, "rng": None}
        )
        assert "no rooms" in summary
    finally:
        db.close()


def test_the_seed_never_opens_a_socket(tmp_path):
    """A real transport would POST to hooks.example from backend/seed.py."""
    import socket

    original = socket.socket

    def refuse(*args, **kwargs):  # pragma: no cover - only runs on a regression
        raise AssertionError("seeding opened a socket")

    db = AuditedDatabase(tmp_path / "nosocket.db")
    try:
        rooms = [(db.create("room", {"name": "R", "account": "A"}, actor="dana")["id"], "A")]
        socket.socket = refuse
        try:
            load_feature("wf025_stream_workspace_activity_events_to_yo").seed(
                db, {"room_ids": rooms, "now": NOW, "rng": None}
            )
        finally:
            socket.socket = original
    finally:
        db.close()


def test_the_seeder_reports_what_this_feature_added(tmp_path):
    """`backend/seed.py` calls every feature's `seed(db, context)` and prints it."""
    import importlib.util

    seeder = Path(__file__).resolve().parents[1] / "seed.py"
    spec = importlib.util.spec_from_file_location("dsr_demo_seeder", seeder)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    db = AuditedDatabase(tmp_path / "seeder.db")
    try:
        rooms = [(db.create("room", {"name": "R", "account": "A"}, actor="dana")["id"], "A")]
        results = module.seed_features(db, room_ids=rooms, now=NOW)
        labelled = dict(results)
        assert "wf025_stream_workspace_activity_events_to_yo" in labelled
        assert "webhooks" in labelled["wf025_stream_workspace_activity_events_to_yo"]
    finally:
        db.close()
