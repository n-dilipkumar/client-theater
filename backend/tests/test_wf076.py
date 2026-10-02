"""WF-076: revoke access early and keep the audit row.

Six researched operations, one guarantee. These tests are organised around that
guarantee rather than around the routes, because the routes are the easy part:

* :class:`RevokeLink` - the URL stops resolving now, and the row that says who
  did it survives the cut.
* :class:`GroupDelete` - the researched cascade, and its atomicity.
* :class:`ViewerIsKept` - "the underlying viewer is kept".
* :class:`Permissions`, :class:`AttachAndDetach`, :class:`Purge` - the rest.
* :class:`Resolution` - "request-time, not scheduled", as a pure function.
* :class:`AuditSource` - every audit row names a route the host really mounted.
* :class:`Seed` - the demo states the research says matter.

The adversarial cases are the ones worth reading: a revoke that half-applies, a
cascade that leaves a link live, a membership removal that takes the viewer with
it, a cross-team attach that is quietly allowed, and a purge that eats the team
library. Each is asserted against the thing the research promised, not against
what the code happens to do.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.revocation import (
    ALL_COLLECTIONS,
    CACHED_COPY_RECALL,
    CASCADE_COLLECTIONS,
    FROZEN_REFUSALS,
    GONE_CASCADE_GROUP,
    GONE_CASCADE_ROOM,
    GONE_MEMBER_REMOVED,
    GRACE_PERIOD_MINUTES,
    GRANTS,
    GROUPS,
    HARD_DELETE,
    IRREVERSIBLE,
    LINKS,
    MEMBERS,
    NOT_FOUND,
    PERMISSIONS,
    RESOLVES,
    REVOCATION_CAUSES,
    REVOKED_BY_GROUP,
    REVOKED_BY_ROOM,
    REVOKED_DIRECTLY,
    REVOKED_SLUG_PREFIX,
    TARGET_GROUP,
    TARGET_ROOM,
    TARGETS,
    VIEWERS,
    RevocationBadRequest,
    RevocationConflict,
    RevocationCrossTeam,
    RevocationFrozen,
    RevocationNotFound,
    RevocationRefusal,
    inferences as inference_module,
    is_custom_domain,
    payload,
    tombstone_slug,
)
from dsr.revocation.engine import READ_LIMIT, Revocation
from dsr.store import RecordStore
from fastapi.testclient import TestClient

MODULE = "dsr.features.wf076_revoke_access_early_and_keep_the_audit"
PREFIX = "/api/wf-076"
FEATURE_ID = "wf-076-revoke-access-early-and-keep-the-audit"
ROOMS = "room"
DOCUMENTS = "document"


def load_feature():
    """Load the feature module by path, the way its own tests do.

    Resolved from this file rather than the process CWD, because the suite runs
    with ``backend`` as its working directory.
    """
    here = Path(__file__).resolve().parent
    path = here.parent / "dsr" / "features" / "wf076_revoke_access_early_and_keep_the_audit.py"
    spec = importlib.util.spec_from_file_location("wf076", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def db():
    # In-memory rather than a file on disk: 0.4 ms against 7.0 ms, measured. No test
    # in this file reads the audit mirror off the filesystem, so the file bought nothing.
    database = AuditedDatabase()
    yield database
    database.close()


@pytest.fixture()
def store(db):
    return RecordStore(db)


@pytest.fixture()
def engine(store):
    return Revocation(store, source_prefix=PREFIX)


@pytest.fixture()
def room(store):
    return store.create(
        ROOMS,
        {"name": "Northwind Traders", "account": "Northwind Traders"},
        actor="dana",
    )


@pytest.fixture()
def room_id(room):
    return room["id"]


@pytest.fixture()
def other_room(store):
    return store.create(ROOMS, {"name": "Contoso Health", "account": "Contoso Health"}, actor="sam")


@pytest.fixture()
def group(engine, room_id):
    return engine.create_group(room_id, name="Procurement", source="test")


@pytest.fixture()
def link(engine, room_id):
    return engine.create_link(room_id, slug="northwind-overview", label="Overview", source="test")[
        "link"
    ]


@pytest.fixture()
def member(engine, room_id, group):
    return engine.add_member(room_id, group["id"], email="buyer@northwind.example", source="test")[
        "member"
    ]


@pytest.fixture(scope="module")
def _shared_client(tmp_path_factory):
    """One application for the module. A fresh database for each test.

    The lifespan in ``dsr/api.py`` only assigns ``app.state.db`` and
    ``app.state.store``, and ``dsr/deps.py`` reads ``app.state.store`` on every
    request. A test therefore needs a fresh *database*, not a fresh
    *application*. Entering a TestClient costs 46 ms measured; swapping the two
    attributes costs about 1.25 ms.

    The environment is patched here rather than per test because a module-scoped
    fixture cannot use the function-scoped ``monkeypatch``. It is undone on the
    way out so it reaches no other module. ``DSR_DB_PATH`` is ``:memory:`` so the
    lifespan's own database costs nothing either.
    """
    scratch = tmp_path_factory.mktemp("wf076-http")
    patch = pytest.MonkeyPatch()
    patch.setenv("DSR_DB_PATH", ":memory:")
    patch.setenv("DSR_AUDIT_DIR", str(scratch / "audit"))
    patch.setattr("dsr.api.FRONTEND_DIST", scratch / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    patch.undo()


@pytest.fixture()
def http(_shared_client):
    """The shared application, over a database this test owns alone.

    ``dependency_overrides`` is cleared on the way in and on the way out: the
    application is module-scoped, so an override one test installs would
    otherwise still be installed for the next one.
    """
    db = AuditedDatabase()
    _shared_client.app.state.db = db
    _shared_client.app.state.store = RecordStore(db)
    _shared_client.app.dependency_overrides.clear()
    try:
        yield _shared_client
    finally:
        _shared_client.app.dependency_overrides.clear()
        db.close()


def make_room(http, name="Northwind Traders", account="Northwind Traders", **extra):
    payload_body = {"name": name, "account": account, **extra}
    return http.post("/api/records/room", json=payload_body).json()["id"]


# --------------------------------------------------------------------------- #
# The module contract
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# What the plugin host needs in order to mount this without a collision.
# --------------------------------------------------------------------------- #


def test_the_feature_id_is_the_one_the_brief_names():
    module = load_feature()
    assert module.FEATURE["id"] == FEATURE_ID
    assert module.FEATURE["ticket"] == "WF-076"


def test_the_router_prefix_is_the_ticket_derived_one():
    module = load_feature()
    assert module.router.prefix == PREFIX
    assert module.router.prefix.startswith("/api/")


def test_the_prefix_is_not_claimed_by_another_feature():
    """A ticket-derived prefix cannot collide; assert it anyway.

    Read from the registry because ``app.routes`` hides mounted features behind
    ``_IncludedRouter`` wrappers on this FastAPI version - see
    :func:`mounted_route_shapes`.
    """
    from dsr.features import REGISTRY

    mine = load_feature().router.prefix
    claimed = [path for path in mounted_route_shapes() if path.startswith(mine + "/")]
    assert claimed, f"nothing is mounted under {mine}"
    owners = [f.id for f in REGISTRY.features if f.prefix == mine]
    assert owners == [FEATURE_ID]


def test_the_router_reports_no_route_of_another_feature():
    """Every path on this router is ours: prefix plus our own sub-paths."""
    module = load_feature()
    for route in module.router.routes:
        assert route.path.startswith(module.router.prefix + "/")


def test_no_route_path_collides_with_a_core_route():
    """The host refuses a collision; this says why it does not need to.

    ``GET /api/health`` is the canary: reusing a core path would be dead code,
    because Starlette matches in registration order and the core route wins.
    """
    module = load_feature()
    mine = {r.path for r in module.router.routes}
    core = {path for path in mounted_route_shapes() if not path.startswith(module.router.prefix)}
    assert mine.isdisjoint(core)
    assert "/api/health" not in mine


def test_the_module_does_not_import_the_app():
    """Hard rule 2. The host has a test for every feature; this pins mine."""
    source = (
        Path(load_feature().__file__).read_text(encoding="utf-8")
        if hasattr(load_feature(), "__file__")
        else ""
    )
    assert "from dsr.api" not in source and "import dsr.api" not in source


def test_the_domain_module_does_not_import_fastapi():
    """The rules must be testable without a request."""
    here = Path(__file__).resolve().parent.parent / "dsr" / "revocation"
    for path in sorted(here.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "from fastapi" not in text, path.name
        assert "import fastapi" not in text, path.name


def test_the_domain_module_never_opens_a_database_connection():
    """The audit guarantee lives in AuditedDatabase; nothing may bypass it."""
    here = Path(__file__).resolve().parent.parent / "dsr" / "revocation"
    for path in sorted(here.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "sqlite3" not in text, path.name
        assert "._conn" not in text, path.name


def test_every_refusal_type_is_covered_by_one_handler():
    module = load_feature()
    assert module.EXCEPTION_HANDLERS == {RevocationRefusal: module._refusal}


def test_every_refusal_subclass_maps_to_a_distinct_code():
    """Two refusals sharing an error code would be indistinguishable to a caller.

    Keyed on the code, not the status: a conflict and a frozen dataroom both
    answer 409 and are meant to, but a client has to be able to tell them apart.
    """
    module = load_feature()
    codes = [code for _, (_, code) in module._STATUS.items()]
    assert len(set(codes)) == len(codes), f"two refusals share a code: {codes}"
    assert set(codes) >= {"not_found", "conflict", "dataroom_frozen", "bad_request"}


def test_every_refusal_subclass_is_mapped():
    """An unmapped refusal would fall through to a 500."""
    module = load_feature()
    for refusal in (
        RevocationNotFound,
        RevocationConflict,
        RevocationFrozen,
        RevocationBadRequest,
        RevocationCrossTeam,
    ):
        assert refusal in module._STATUS, refusal


def test_a_conflict_and_a_frozen_dataroom_share_a_status_but_not_a_code():
    """409 for both is correct; `conflict` for both would not be."""
    module = load_feature()
    assert module._STATUS[RevocationConflict][0] == module._STATUS[RevocationFrozen][0] == 409
    assert module._STATUS[RevocationConflict][1] != module._STATUS[RevocationFrozen][1]


def test_the_collections_are_namespaced_to_this_feature():
    """A collision here would be two features silently writing one table."""
    for collection in ALL_COLLECTIONS:
        assert collection.startswith("revocation_"), collection
    assert len(set(ALL_COLLECTIONS)) == len(ALL_COLLECTIONS)


def test_the_cascade_collections_are_a_subset_of_all_of_them():
    assert set(CASCADE_COLLECTIONS) <= set(ALL_COLLECTIONS)


def test_viewers_are_not_cascaded():
    """Sourced: "The underlying viewer is kept"."""
    assert VIEWERS not in CASCADE_COLLECTIONS


def test_links_groups_members_permissions_and_grants_are_cascaded():
    for collection in (LINKS, GROUPS, MEMBERS, PERMISSIONS, GRANTS):
        assert collection in CASCADE_COLLECTIONS


# --------------------------------------------------------------------------- #
# The constants that encode the researched guarantees.
# --------------------------------------------------------------------------- #


def test_there_is_no_grace_period():
    assert GRACE_PERIOD_MINUTES == 0


def test_there_is_no_cached_copy_recall():
    assert CACHED_COPY_RECALL == "none"


def test_revocation_never_hard_deletes():
    assert HARD_DELETE is False


def test_nothing_in_this_workflow_is_reversible():
    assert IRREVERSIBLE is False


def test_the_targets_are_room_and_group():
    assert TARGETS == (TARGET_ROOM, TARGET_GROUP)


def test_the_three_revocation_causes_are_named():
    assert REVOCATION_CAUSES == (REVOKED_DIRECTLY, REVOKED_BY_GROUP, REVOKED_BY_ROOM)


def test_the_read_limit_is_an_honest_bound_not_a_silent_truncation():
    assert READ_LIMIT >= 1000


def test_the_frozen_refusals_are_only_the_three_the_research_names():
    assert FROZEN_REFUSALS == frozenset({"attach", "detach", "move"})


def test_freezing_never_refuses_revocation():
    """The absence is the point: an embargo must not leave stale links live."""
    assert "revoke_link" not in FROZEN_REFUSALS
    assert "delete_group" not in FROZEN_REFUSALS
    assert "purge" not in FROZEN_REFUSALS


# --------------------------------------------------------------------------- #
# Sourced: the slug is renamed only for a custom-domain link.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "domain,expected",
    [
        ("share.acme.example", True),
        ("CUSTOM.EXAMPLE", True),
        ("app", False),
        ("APP", False),
        ("", False),
        (None, False),
        ("   ", False),
    ],
)
def test_only_a_custom_domain_counts(domain, expected):
    assert is_custom_domain(domain) is expected


def test_the_tombstone_is_recognisable():
    slug = tombstone_slug("revocation_link_abcdef123456", "northwind-overview")
    assert slug.startswith(REVOKED_SLUG_PREFIX)


def test_the_tombstone_is_derived_from_the_link_not_the_clock():
    """A test should not have to freeze time to assert the rename."""
    first = tombstone_slug("revocation_link_abcdef123456", "northwind-overview")
    second = tombstone_slug("revocation_link_abcdef123456", "northwind-overview")
    assert first == second


def test_two_different_links_get_two_different_tombstones():
    one = tombstone_slug("revocation_link_aaaaaaaa", "slug")
    two = tombstone_slug("revocation_link_bbbbbbbb", "slug")
    assert one != two


def test_the_original_slug_survives_inside_the_tombstone():
    """The rename frees the original; it does not destroy the evidence."""
    slug = tombstone_slug("revocation_link_abcdef123456", "northwind-overview")
    assert "northwind-overview" in slug


def test_a_slug_with_a_path_in_it_cannot_produce_a_second_path_segment():
    slug = tombstone_slug("revocation_link_abcdef123456", "acme/deal")
    assert "/" not in slug


def test_an_empty_original_still_produces_a_usable_name():
    assert tombstone_slug("revocation_link_abcdef123456", "").startswith(REVOKED_SLUG_PREFIX)


# --------------------------------------------------------------------------- #
# Records arrive either wrapped in ``data`` or already flat.
# --------------------------------------------------------------------------- #


def test_a_wrapped_record_is_unwrapped():
    assert payload({"id": "x", "data": {"a": 1}}) == {"a": 1}


def test_a_flat_record_passes_through():
    assert payload({"a": 1}) == {"a": 1}


def test_a_missing_data_key_passes_through():
    assert payload({"id": "x"}) == {"id": "x"}


# --------------------------------------------------------------------------- #
# Room state
# --------------------------------------------------------------------------- #


def test_an_unknown_room_is_refused(engine):
    with pytest.raises(RevocationNotFound):
        engine.room("room_absent")


def test_a_room_with_no_frozen_field_is_not_frozen(engine):
    assert engine.is_frozen({"data": {"name": "legacy"}}) is False


def test_a_frozen_room_reads_as_frozen(engine):
    assert engine.is_frozen({"data": {"frozen": True}}) is True


def test_freezing_is_read_from_the_room_payload_so_a_team_can_set_it(store, engine):
    room = store.create(ROOMS, {"name": "Embargoed", "frozen": True}, actor="dana")
    assert engine.frozen(room["id"]) is True


def test_the_team_is_the_rooms_account(engine, room_id):
    assert engine.team_of(engine.room(room_id)) == "Northwind Traders"


def test_a_room_without_an_account_has_no_team(store, engine):
    room = store.create(ROOMS, {"name": "No account"}, actor="dana")
    assert engine.team_of(room) == ""


def test_a_record_from_another_room_is_not_found(engine, room_id, other_room, link):
    with pytest.raises(RevocationNotFound):
        engine.link(other_room["id"], link["id"])


def test_a_record_of_another_collection_is_not_found(engine, room_id, group):
    with pytest.raises(RevocationNotFound):
        engine.link(room_id, group["id"])


def test_state_reports_the_guarantee(engine, room_id):
    guarantee = engine.state(room_id)["guarantee"]
    assert guarantee["grace_period_minutes"] == 0
    assert guarantee["cached_copy_recall"] == "none"
    assert guarantee["row_kept_for_audit"] is True
    assert guarantee["request_time"] is True


def test_state_counts_live_and_revoked_links_separately(engine, room_id, link):
    engine.revoke_link(room_id, link["id"], source="test")
    counts = engine.state(room_id)["counts"]
    assert counts["links"] == 0
    assert counts["links_revoked"] == 1


def test_an_unfrozen_room_offers_attach_and_detach(engine, room_id):
    actions = engine.state(room_id)["available_actions"]
    assert "attach" in actions and "detach" in actions


def test_a_frozen_room_refuses_attach_and_detach_and_says_why(store, engine):
    room = store.create(ROOMS, {"name": "Embargoed", "frozen": True}, actor="dana")
    state = engine.state(room["id"])
    assert state["frozen"] is True
    assert "attach" not in state["available_actions"]
    assert state["refused_actions"]["attach"] == "the dataroom is frozen"
    assert state["refused_actions"]["detach"] == "the dataroom is frozen"


def test_a_frozen_room_still_offers_revocation(store, engine):
    room = store.create(ROOMS, {"name": "Embargoed", "frozen": True}, actor="dana")
    actions = engine.state(room["id"])["available_actions"]
    for action in ("revoke_link", "delete_group", "purge", "remove_member"):
        assert action in actions


def test_summary_counts_across_the_product(engine, room_id, link, other_room):
    engine.create_link(other_room["id"], slug="contoso", source="test")
    summary = engine.summary()
    assert summary["rooms"] == 2
    assert summary["by_room"][room_id][LINKS] == 1
    assert summary["guarantee"]["grace_period_minutes"] == 0


# --------------------------------------------------------------------------- #
# Issue a link
# --------------------------------------------------------------------------- #


def test_a_link_is_created_with_its_slug(engine, room_id, link):
    assert link["data"]["slug"] == "northwind-overview"
    assert link["room_id"] == room_id


def test_a_link_with_no_slug_is_refused(engine, room_id):
    with pytest.raises(RevocationBadRequest):
        engine.create_link(room_id, slug="  ", source="test")


def test_a_slug_a_live_link_holds_cannot_be_taken_twice(engine, room_id, link):
    with pytest.raises(RevocationConflict):
        engine.create_link(room_id, slug="northwind-overview", source="test")


def test_an_unknown_target_is_refused(engine, room_id):
    with pytest.raises(RevocationBadRequest):
        engine.create_link(room_id, slug="x", target="dataroom", source="test")


def test_a_group_link_needs_a_group(engine, room_id):
    with pytest.raises(RevocationBadRequest):
        engine.create_link(room_id, slug="x", target=TARGET_GROUP, source="test")


def test_a_group_link_pointing_at_no_group_is_refused(engine, room_id, group):
    with pytest.raises(RevocationBadRequest):
        engine.create_link(
            room_id, slug="x", target=TARGET_ROOM, group_id=group["id"], source="test"
        )


def test_a_group_link_to_an_unknown_group_is_refused(engine, room_id):
    with pytest.raises(RevocationNotFound):
        engine.create_link(
            room_id, slug="x", target=TARGET_GROUP, group_id="grp_absent", source="test"
        )


def test_a_custom_domain_is_recorded_on_the_link(engine, room_id):
    row = engine.create_link(room_id, slug="acme", domain="share.acme.example", source="test")[
        "link"
    ]
    assert row["data"]["custom_domain"] is True


def test_a_link_on_the_products_own_host_is_not_a_custom_domain(engine, room_id):
    row = engine.create_link(room_id, slug="acme", domain="app", source="test")["link"]
    assert row["data"]["custom_domain"] is False


def test_issuing_records_the_actor_and_the_source(engine, room_id):
    row = engine.create_link(room_id, slug="acme", actor="sam", source="POST /somewhere")["link"]
    assert row["actor"] == "sam"
    audit = engine.store.audit(record_id=row["id"], limit=5)
    assert audit[0]["source"] == "POST /somewhere"


# --------------------------------------------------------------------------- #
# Revoke a link: the headline
# --------------------------------------------------------------------------- #


def test_the_public_url_stops_resolving_immediately(engine, room_id, link):
    assert engine.resolve(room_id, "northwind-overview")["resolves"] is True
    engine.revoke_link(room_id, link["id"], source="test")
    answer = engine.resolve(room_id, "northwind-overview")
    assert answer["resolves"] is False
    assert answer["reason"] == "revoked"


def test_the_row_is_kept_in_the_database_for_audit(engine, store, room_id, link):
    """The researched sentence, quoted: the row is kept for audit."""
    engine.revoke_link(room_id, link["id"], source="test")
    retained = store.db.get(link["id"], include_deleted=True)
    assert retained is not None
    assert retained["deleted_at"] is not None


def test_the_row_is_gone_from_the_ordinary_read(engine, store, room_id, link):
    engine.revoke_link(room_id, link["id"], source="test")
    assert store.get(link["id"]) is None


def test_the_ordinary_link_list_no_longer_shows_it(engine, room_id, link):
    engine.revoke_link(room_id, link["id"], source="test")
    assert engine.links(room_id) == []


def test_the_revoked_link_list_still_shows_it(engine, room_id, link):
    engine.revoke_link(room_id, link["id"], source="test")
    rows = engine.links(room_id, include_revoked=True)
    assert [r["id"] for r in rows] == [link["id"]]


def test_the_audit_row_records_the_delete_and_the_state_before_it(engine, room_id, link):
    engine.revoke_link(room_id, link["id"], source="test")
    entries = engine.store.audit(record_id=link["id"], limit=10)
    actions = [e["action"] for e in entries]
    assert "delete" in actions
    deleted = next(e for e in entries if e["action"] == "delete")
    assert deleted["before_state"]["slug"] == "northwind-overview"


def test_the_retained_row_and_its_trail_are_readable_afterwards(engine, room_id, link):
    engine.revoke_link(room_id, link["id"], reason="deal went cold", source="test")
    retained = engine.retained(room_id, link["id"])
    assert retained["live"] is False
    assert retained["deleted_at"] is not None
    assert retained["data"]["revocation_reason"] == "deal went cold"
    assert {e["action"] for e in retained["audit"]} == {"insert", "update", "delete"}


def test_the_retained_surface_refuses_a_record_from_another_room(engine, room_id, other_room, link):
    engine.revoke_link(room_id, link["id"], source="test")
    with pytest.raises(RevocationNotFound):
        engine.retained(other_room["id"], link["id"])


def test_the_retained_surface_refuses_a_record_that_is_not_an_access_record(engine, room_id, room):
    with pytest.raises(RevocationNotFound):
        engine.retained(room_id, room["id"])


def test_retaining_an_unknown_record_is_refused(engine, room_id):
    with pytest.raises(RevocationNotFound):
        engine.retained(room_id, "revocation_link_absent")


def test_revoking_twice_is_a_conflict_not_a_no_op(engine, room_id, link):
    engine.revoke_link(room_id, link["id"], source="test")
    with pytest.raises(RevocationConflict):
        engine.revoke_link(room_id, link["id"], source="test")


def test_revoking_an_unknown_link_is_refused(engine, room_id):
    with pytest.raises(RevocationNotFound):
        engine.revoke_link(room_id, "revocation_link_absent", source="test")


def test_revoking_another_rooms_link_is_refused(engine, room_id, other_room):
    foreign = engine.create_link(other_room["id"], slug="contoso", source="test")["link"]
    with pytest.raises(RevocationNotFound):
        engine.revoke_link(room_id, foreign["id"], source="test")


def test_who_did_it_and_why_and_when_are_all_recorded(engine, room_id, link):
    result = engine.revoke_link(room_id, link["id"], reason="embargoed", actor="sam", source="test")
    assert result["revoked_by"] == "sam"
    assert result["reason"] == "embargoed"
    assert result["revoked_at"]
    retained = engine.retained(room_id, link["id"])
    assert retained["data"]["revoked_by"] == "sam"


def test_revocation_is_effective_at_the_moment_it_happened(engine, room_id, link):
    engine.revoke_link(room_id, link["id"], source="test")
    retained = engine.retained(room_id, link["id"])
    assert retained["data"]["effective_at"] == retained["data"]["revoked_at"]
    assert retained["data"]["grace_period_minutes"] == 0


def test_the_response_states_the_guarantee_rather_than_leaving_it_implied(engine, room_id, link):
    result = engine.revoke_link(room_id, link["id"], source="test")
    assert result["grace_period_minutes"] == 0
    assert result["cached_copy_recall"] == CACHED_COPY_RECALL
    assert result["row_kept"] is True
    assert result["reversible"] is IRREVERSIBLE


def test_the_cause_is_the_direct_one(engine, room_id, link):
    engine.revoke_link(room_id, link["id"], source="test")
    assert engine.retained(room_id, link["id"])["data"]["revoked_via"] == REVOKED_DIRECTLY


def test_a_frozen_dataroom_can_still_have_its_link_revoked(store, engine):
    """Freeze governs structure. Cutting access is the whole point of the freeze."""
    room = store.create(
        ROOMS, {"name": "Embargoed", "account": "Acme", "frozen": True}, actor="dana"
    )
    row = engine.create_link(room["id"], slug="acme", source="test")["link"]
    engine.revoke_link(room["id"], row["id"], source="test")
    assert engine.resolve(room["id"], "acme")["resolves"] is False


# --------------------------------------------------------------------------- #
# Sourced: "the slug is renamed so the original can be reused".
# --------------------------------------------------------------------------- #


def test_a_custom_domain_slug_is_renamed_on_revoke(engine, room_id):
    row = engine.create_link(room_id, slug="acme-deal", domain="share.acme.example", source="test")[
        "link"
    ]
    result = engine.revoke_link(room_id, row["id"], source="test")
    assert result["slug_released"] is True
    assert result["original_slug"] == "acme-deal"
    assert result["slug"].startswith(REVOKED_SLUG_PREFIX)


def test_the_original_slug_becomes_reusable(engine, room_id):
    row = engine.create_link(room_id, slug="acme-deal", domain="share.acme.example", source="test")[
        "link"
    ]
    assert engine.slug_available(room_id, "acme-deal") is False
    engine.revoke_link(room_id, row["id"], source="test")
    assert engine.slug_available(room_id, "acme-deal") is True


def test_the_released_slug_can_actually_be_issued_again(engine, room_id):
    first = engine.create_link(
        room_id, slug="acme-deal", domain="share.acme.example", source="test"
    )["link"]
    engine.revoke_link(room_id, first["id"], source="test")
    second = engine.create_link(
        room_id, slug="acme-deal", domain="share.acme.example", source="test"
    )
    assert second["link"]["data"]["slug"] == "acme-deal"


def test_a_link_on_the_products_own_host_keeps_its_slug(engine, room_id):
    """The research makes the rename conditional; the condition is honoured."""
    row = engine.create_link(room_id, slug="acme-deal", domain="app", source="test")["link"]
    result = engine.revoke_link(room_id, row["id"], source="test")
    assert result["slug_released"] is False
    assert result["slug"] == "acme-deal"


def test_a_link_with_no_domain_keeps_its_slug(engine, room_id, link):
    result = engine.revoke_link(room_id, link["id"], source="test")
    assert result["slug_released"] is False
    assert result["slug"] == "northwind-overview"


def test_the_original_slug_is_still_named_on_the_row(engine, room_id):
    row = engine.create_link(room_id, slug="acme-deal", domain="share.acme.example", source="test")[
        "link"
    ]
    engine.revoke_link(room_id, row["id"], source="test")
    retained = engine.retained(room_id, row["id"])
    assert retained["data"]["original_slug"] == "acme-deal"
    assert retained["data"]["slug_released"] is True


def test_the_old_slug_reports_revoked_rather_than_not_found(engine, room_id):
    """The deleted row is what makes the answer 'revoked' rather than 'never existed'."""
    row = engine.create_link(room_id, slug="acme-deal", domain="share.acme.example", source="test")[
        "link"
    ]
    engine.revoke_link(room_id, row["id"], source="test")
    answer = engine.resolve(room_id, "acme-deal")
    assert answer["resolves"] is False
    assert answer["withheld_by"] == REVOKED_DIRECTLY


def test_a_slug_held_by_a_live_link_is_not_available(engine, room_id, link):
    assert engine.slug_available(room_id, "northwind-overview") is False


def test_an_empty_slug_is_never_available(engine, room_id):
    assert engine.slug_available(room_id, "") is False


def test_the_holding_link_can_be_named(engine, room_id, link):
    assert [row["id"] for row in engine.links_with_slug(room_id, link["data"]["slug"])] == [
        link["id"]
    ]


def test_a_slug_is_room_scoped(engine, room_id, other_room):
    engine.create_link(other_room["id"], slug="shared-slug", source="test")
    assert engine.slug_available(room_id, "shared-slug") is True
    assert engine.slug_available(other_room["id"], "shared-slug") is False


# --------------------------------------------------------------------------- #
# Delete a group: the researched cascade
# --------------------------------------------------------------------------- #


@pytest.fixture()
def populated(engine, room_id):
    """A room with two groups, members, permissions and links of every kind."""
    procurement = engine.create_group(room_id, name="Procurement", source="test")
    legal = engine.create_group(room_id, name="Legal", source="test")
    engine.add_member(room_id, procurement["id"], email="buyer@northwind.example", source="test")
    engine.add_member(room_id, legal["id"], email="counsel@northwind.example", source="test")
    engine.set_permissions(
        room_id,
        legal["id"],
        {"document_id": "doc-1", "view": False, "download": False},
        source="test",
    )
    engine.create_link(
        room_id,
        slug="procurement-link",
        target=TARGET_GROUP,
        group_id=procurement["id"],
        source="test",
    )
    engine.create_link(
        room_id,
        slug="legal-link",
        target=TARGET_GROUP,
        group_id=legal["id"],
        domain="share.acme.example",
        source="test",
    )
    engine.create_link(room_id, slug="whole-room-link", target=TARGET_ROOM, source="test")
    return {"procurement": procurement, "legal": legal}


def test_the_group_is_removed(engine, room_id, populated):
    engine.delete_group(
        room_id, populated["legal"]["id"], confirm=populated["legal"]["id"], source="test"
    )
    with pytest.raises(RevocationNotFound):
        engine.group(room_id, populated["legal"]["id"])


def test_the_memberships_are_removed(engine, room_id, populated):
    before = len(engine.members(room_id, populated["legal"]["id"]))
    assert before == 1
    engine.delete_group(
        room_id, populated["legal"]["id"], confirm=populated["legal"]["id"], source="test"
    )
    assert engine.members(room_id, populated["legal"]["id"]) == []


def test_the_permissions_are_removed(engine, room_id, populated):
    engine.delete_group(
        room_id, populated["legal"]["id"], confirm=populated["legal"]["id"], source="test"
    )
    assert engine.permissions(room_id, populated["legal"]["id"]) == []


def test_every_link_pointing_at_the_group_is_removed(engine, store, room_id, populated):
    target = next(row for row in engine.links(room_id) if payload(row).get("slug") == "legal-link")
    result = engine.delete_group(
        room_id, populated["legal"]["id"], confirm=populated["legal"]["id"], source="test"
    )
    assert result["links_stopped"] == [target["id"]]
    assert store.db.get(target["id"], include_deleted=True) is not None


def test_active_group_links_stop_resolving_immediately(engine, room_id, populated):
    assert (
        engine.resolve(room_id, "legal-link", viewer="counsel@northwind.example")["resolves"]
        is True
    )
    engine.delete_group(
        room_id, populated["legal"]["id"], confirm=populated["legal"]["id"], source="test"
    )
    answer = engine.resolve(room_id, "legal-link", viewer="counsel@northwind.example")
    assert answer["resolves"] is False
    assert answer["reason"] == GONE_CASCADE_GROUP
    assert answer["withheld_by"] == REVOKED_BY_GROUP


def test_a_link_pointing_at_another_group_is_untouched(engine, room_id, populated):
    engine.delete_group(
        room_id, populated["legal"]["id"], confirm=populated["legal"]["id"], source="test"
    )
    assert (
        engine.resolve(room_id, "procurement-link", viewer="buyer@northwind.example")["resolves"]
        is True
    )


def test_a_room_wide_link_is_untouched(engine, room_id, populated):
    engine.delete_group(
        room_id, populated["legal"]["id"], confirm=populated["legal"]["id"], source="test"
    )
    assert engine.resolve(room_id, "whole-room-link")["resolves"] is True


def test_every_removed_row_is_retained_for_audit(engine, store, room_id, populated):
    result = engine.delete_group(
        room_id, populated["legal"]["id"], confirm=populated["legal"]["id"], source="test"
    )
    assert result["row_kept_for_audit"] is True
    for record_id in result["ids"]:
        assert store.db.get(record_id, include_deleted=True) is not None


def test_the_cascade_is_one_audit_row(engine, store, room_id, populated):
    result = engine.delete_group(
        room_id, populated["legal"]["id"], confirm=populated["legal"]["id"], source="test"
    )
    deletes = [
        entry
        for entry in store.audit(action="delete", limit=200)
        if entry["record_id"] is None and entry["source"] == "test"
    ]
    assert len(deletes) == 1
    assert len(deletes[0]["before_state"]["records"]) == result["removed"]


def test_the_cascade_audit_row_names_every_record_it_removed(engine, store, room_id, populated):
    engine.delete_group(
        room_id, populated["legal"]["id"], confirm=populated["legal"]["id"], source="test"
    )
    entry = next(
        e
        for e in store.audit(action="delete", limit=200)
        if e["record_id"] is None and e["source"] == "test"
    )
    collections = {r["collection"] for r in entry["before_state"]["records"]}
    assert collections == {GROUPS, MEMBERS, PERMISSIONS, LINKS}


def test_the_cascade_audit_row_records_the_slug_each_link_freed(engine, store, room_id, populated):
    engine.delete_group(
        room_id, populated["legal"]["id"], confirm=populated["legal"]["id"], source="test"
    )
    entry = next(
        e
        for e in store.audit(action="delete", limit=200)
        if e["record_id"] is None and e["source"] == "test"
    )
    slugs = {r["data"]["slug"] for r in entry["before_state"]["records"] if "slug" in r["data"]}
    assert "legal-link" in slugs


def test_the_freed_slug_is_reusable_afterwards(engine, room_id, populated):
    assert engine.slug_available(room_id, "legal-link") is False
    result = engine.delete_group(
        room_id, populated["legal"]["id"], confirm=populated["legal"]["id"], source="test"
    )
    assert result["slugs_available_now"]["legal-link"] is True


def test_the_underlying_viewers_are_kept(engine, room_id, populated):
    before = len(engine.viewers(room_id))
    engine.delete_group(
        room_id, populated["legal"]["id"], confirm=populated["legal"]["id"], source="test"
    )
    assert len(engine.viewers(room_id)) == before


def test_the_removal_counts_add_up(engine, room_id, populated):
    result = engine.delete_group(
        room_id, populated["legal"]["id"], confirm=populated["legal"]["id"], source="test"
    )
    assert result["by_collection"] == {
        GROUPS: 1,
        MEMBERS: 1,
        PERMISSIONS: 1,
        LINKS: 1,
    }
    assert result["removed"] == 4


def test_it_is_reported_as_irreversible(engine, room_id, populated):
    result = engine.delete_group(
        room_id, populated["legal"]["id"], confirm=populated["legal"]["id"], source="test"
    )
    assert result["reversible"] is IRREVERSIBLE


def test_without_a_confirmation_it_is_refused(engine, room_id, populated):
    with pytest.raises(RevocationBadRequest):
        engine.delete_group(room_id, populated["legal"]["id"], confirm="", source="test")


def test_a_confirmation_naming_something_else_is_refused(engine, room_id, populated):
    with pytest.raises(RevocationBadRequest):
        engine.delete_group(room_id, populated["legal"]["id"], confirm="yes", source="test")


def test_a_refused_delete_changes_nothing(engine, room_id, populated):
    with pytest.raises(RevocationBadRequest):
        engine.delete_group(room_id, populated["legal"]["id"], confirm="wrong", source="test")
    assert engine.group(room_id, populated["legal"]["id"]) is not None
    assert len(engine.members(room_id, populated["legal"]["id"])) == 1
    assert (
        engine.resolve(room_id, "legal-link", viewer="counsel@northwind.example")["resolves"]
        is True
    )


def test_deleting_a_group_from_another_room_is_refused(engine, room_id, other_room):
    foreign = engine.create_group(other_room["id"], name="Foreign", source="test")
    with pytest.raises(RevocationNotFound):
        engine.delete_group(room_id, foreign["id"], confirm=foreign["id"], source="test")


def test_deleting_an_unknown_group_is_refused(engine, room_id):
    with pytest.raises(RevocationNotFound):
        engine.delete_group(room_id, "revocation_group_absent", confirm="x", source="test")


def test_a_group_in_another_room_is_not_reached(engine, room_id, other_room, populated):
    foreign = engine.create_group(other_room["id"], name="Foreign", source="test")
    engine.delete_group(
        room_id, populated["legal"]["id"], confirm=populated["legal"]["id"], source="test"
    )
    assert engine.group(other_room["id"], foreign["id"]) is not None


def test_there_is_no_route_that_restores_a_deleted_group():
    """Sourced: "cannot be undone". Asserted against the route table."""
    module = load_feature()
    paths = [route.path for route in module.router.routes]
    assert not any("restore" in path or "undelete" in path for path in paths)


# --------------------------------------------------------------------------- #
# Remove one member
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Sourced: "The underlying viewer is kept - only their membership goes".
# --------------------------------------------------------------------------- #


def test_the_membership_is_removed(engine, store, room_id, group, member):
    engine.remove_member(room_id, group["id"], member["id"], source="test")
    assert engine.members(room_id, group["id"]) == []


def test_the_membership_row_is_retained_for_audit(engine, store, room_id, group, member):
    engine.remove_member(room_id, group["id"], member["id"], source="test")
    retained = store.db.get(member["id"], include_deleted=True)
    assert retained is not None
    assert retained["deleted_at"] is not None


def test_the_viewer_is_still_there(engine, store, room_id, group, member):
    viewer_id = member["data"]["viewer_id"]
    result = engine.remove_member(room_id, group["id"], member["id"], source="test")
    assert result["viewer_kept"] is True
    assert store.get(viewer_id) is not None


def test_removing_one_buyer_does_not_touch_the_others(engine, room_id, group):
    first = engine.add_member(room_id, group["id"], email="one@northwind.example", source="test")[
        "member"
    ]
    engine.add_member(room_id, group["id"], email="two@northwind.example", source="test")
    engine.remove_member(room_id, group["id"], first["id"], source="test")
    remaining = [m["data"]["viewer_email"] for m in engine.members(room_id, group["id"])]
    assert remaining == ["two@northwind.example"]


def test_removing_one_membership_does_not_touch_the_link(engine, room_id, group, member):
    engine.create_link(
        room_id, slug="procurement", target=TARGET_GROUP, group_id=group["id"], source="test"
    )
    engine.remove_member(room_id, group["id"], member["id"], source="test")
    assert engine.resolve(room_id, "procurement")["link_id"] is not None


def test_the_removed_buyer_no_longer_resolves(engine, room_id, group, member):
    engine.create_link(
        room_id, slug="procurement", target=TARGET_GROUP, group_id=group["id"], source="test"
    )
    result = engine.remove_member(room_id, group["id"], member["id"], source="test")
    answer = result["resolves_now"]
    assert answer["resolves"] is False
    assert answer["reason"] == GONE_MEMBER_REMOVED
    assert answer["viewer"] == "buyer@northwind.example"


def test_a_remaining_buyer_still_resolves(engine, room_id, group):
    engine.create_link(
        room_id, slug="procurement", target=TARGET_GROUP, group_id=group["id"], source="test"
    )
    first = engine.add_member(room_id, group["id"], email="one@northwind.example", source="test")[
        "member"
    ]
    engine.add_member(room_id, group["id"], email="two@northwind.example", source="test")
    engine.remove_member(room_id, group["id"], first["id"], source="test")
    assert (
        engine.resolve(room_id, "procurement", viewer="two@northwind.example")["resolves"] is True
    )


def test_removing_twice_is_refused(engine, room_id, group, member):
    engine.remove_member(room_id, group["id"], member["id"], source="test")
    with pytest.raises(RevocationNotFound):
        engine.remove_member(room_id, group["id"], member["id"], source="test")


def test_removing_a_member_of_another_group_is_refused(engine, room_id, group, member):
    other = engine.create_group(room_id, name="Legal", source="test")
    with pytest.raises(RevocationNotFound):
        engine.remove_member(room_id, other["id"], member["id"], source="test")


def test_removing_from_an_unknown_group_is_refused(engine, room_id, member):
    with pytest.raises(RevocationNotFound):
        engine.remove_member(room_id, "revocation_group_absent", member["id"], source="test")


def test_a_buyer_cannot_be_added_to_the_same_group_twice(engine, room_id, group, member):
    with pytest.raises(RevocationConflict):
        engine.add_member(room_id, group["id"], email="buyer@northwind.example", source="test")


def test_a_membership_needs_an_email(engine, room_id, group):
    with pytest.raises(RevocationBadRequest):
        engine.add_member(room_id, group["id"], email="  ", source="test")


def test_the_viewer_is_created_on_first_sight(engine, room_id, group):
    result = engine.add_member(room_id, group["id"], email="new@northwind.example", source="test")
    assert result["viewer"]["data"]["email"] == "new@northwind.example"


def test_a_second_membership_reuses_the_existing_viewer(engine, room_id, group):
    legal = engine.create_group(room_id, name="Legal", source="test")
    first = engine.add_member(room_id, group["id"], email="new@northwind.example", source="test")
    second = engine.add_member(room_id, legal["id"], email="new@northwind.example", source="test")
    assert first["viewer"]["id"] == second["viewer"]["id"]
    assert len(engine.viewers(room_id)) == 1


# --------------------------------------------------------------------------- #
# Permissions
# --------------------------------------------------------------------------- #


def test_an_item_can_be_hidden_by_turning_both_flags_off(engine, room_id, group):
    result = engine.set_permissions(
        room_id,
        group["id"],
        {"document_id": "doc-1", "view": False, "download": False},
        source="test",
    )
    applied = result["applied"][0]
    assert applied["view"] is False
    assert applied["download"] is False
    assert applied["hidden"] is True


def test_a_permission_row_is_created(engine, store, room_id, group):
    engine.set_permissions(
        room_id,
        group["id"],
        {"document_id": "doc-1", "view": True, "download": True},
        source="test",
    )
    rows = engine.permissions(room_id, group["id"])
    assert len(rows) == 1
    assert rows[0]["data"]["document_id"] == "doc-1"


def test_changing_an_existing_permission_updates_that_row(engine, room_id, group):
    engine.set_permissions(
        room_id,
        group["id"],
        {"document_id": "doc-1", "view": True, "download": True},
        source="test",
    )
    second = engine.set_permissions(
        room_id,
        group["id"],
        {"document_id": "doc-1", "view": False, "download": False},
        source="test",
    )
    assert second["applied"][0]["action"] == "updated"
    assert len(engine.permissions(room_id, group["id"])) == 1


def test_a_list_of_permissions_is_accepted(engine, room_id, group):
    result = engine.set_permissions(
        room_id,
        group["id"],
        [
            {"document_id": "doc-1", "view": False, "download": False},
            {"document_id": "doc-2", "view": True, "download": True},
        ],
        source="test",
    )
    assert len(result["applied"]) == 2


def test_a_nested_permissions_key_is_accepted(engine, room_id, group):
    result = engine.set_permissions(
        room_id,
        group["id"],
        {"permissions": [{"document_id": "doc-1", "view": True, "download": True}]},
        source="test",
    )
    assert len(result["applied"]) == 1


def test_download_without_view_is_refused(engine, room_id, group):
    """A document a buyer cannot see is not one they can download."""
    with pytest.raises(RevocationBadRequest):
        engine.set_permissions(
            room_id,
            group["id"],
            {"document_id": "doc-1", "view": False, "download": True},
            source="test",
        )


def test_a_refused_flag_pair_changes_nothing(engine, room_id, group):
    with pytest.raises(RevocationBadRequest):
        engine.set_permissions(
            room_id,
            group["id"],
            {"document_id": "doc-1", "view": False, "download": True},
            source="test",
        )
    assert engine.permissions(room_id, group["id"]) == []


def test_an_omitted_flag_is_off_not_on(engine, room_id, group):
    result = engine.set_permissions(room_id, group["id"], {"document_id": "doc-1"}, source="test")
    assert result["applied"][0]["view"] is False
    assert result["applied"][0]["download"] is False


def test_a_permission_needs_a_document(engine, room_id, group):
    with pytest.raises(RevocationBadRequest):
        engine.set_permissions(room_id, group["id"], {"view": False}, source="test")


def test_no_permissions_at_all_is_refused(engine, room_id, group):
    with pytest.raises(RevocationBadRequest):
        engine.set_permissions(room_id, group["id"], None, source="test")


def test_a_string_is_not_a_permission_list(engine, room_id, group):
    with pytest.raises(RevocationBadRequest):
        engine.set_permissions(room_id, group["id"], "view=off", source="test")


def test_permissions_on_an_unknown_group_are_refused(engine, room_id):
    with pytest.raises(RevocationNotFound):
        engine.set_permissions(
            room_id, "revocation_group_absent", {"document_id": "d"}, source="test"
        )


# --------------------------------------------------------------------------- #
# Attach and detach
# --------------------------------------------------------------------------- #


def test_attaching_creates_a_join_row(engine, room_id):
    row = engine.attach(room_id, document_id="doc-1", title="Deck", source="test")
    assert row["data"]["document_id"] == "doc-1"
    assert row["data"]["status"] == "attached"
    assert row["room_id"] == room_id


def test_attaching_the_same_document_twice_is_refused(engine, room_id):
    engine.attach(room_id, document_id="doc-1", source="test")
    with pytest.raises(RevocationConflict):
        engine.attach(room_id, document_id="doc-1", source="test")


def test_attaching_needs_a_document(engine, room_id):
    with pytest.raises(RevocationBadRequest):
        engine.attach(room_id, document_id="  ", source="test")


def test_a_cross_team_attach_is_refused(engine, room_id):
    with pytest.raises(RevocationCrossTeam):
        engine.attach(
            room_id,
            document_id="doc-1",
            document_team="Contoso Health",
            source="test",
        )


def test_a_same_team_attach_is_allowed(engine, room_id):
    row = engine.attach(
        room_id, document_id="doc-1", document_team="Northwind Traders", source="test"
    )
    assert row["data"]["document_team"] == "Northwind Traders"


def test_a_document_with_no_team_of_its_own_is_treated_as_this_datarooms(engine, room_id):
    """The core demo documents carry no team; failing closed would refuse everything."""
    row = engine.attach(room_id, document_id="doc-1", source="test")
    assert row["data"]["document_team"] == "Northwind Traders"


def test_a_frozen_dataroom_refuses_a_new_attachment(store, engine):
    room = store.create(
        ROOMS, {"name": "Embargoed", "account": "Acme", "frozen": True}, actor="dana"
    )
    with pytest.raises(RevocationFrozen):
        engine.attach(room["id"], document_id="doc-1", source="test")


def test_detaching_removes_the_join_row(engine, room_id):
    engine.attach(room_id, document_id="doc-1", source="test")
    result = engine.detach(room_id, "doc-1", source="test")
    assert result["detached"] is True
    assert engine.grants(room_id) == []


def test_detaching_keeps_the_join_row_for_audit(engine, store, room_id):
    engine.attach(room_id, document_id="doc-1", source="test")
    result = engine.detach(room_id, "doc-1", source="test")
    retained = store.db.get(result["grant_id"], include_deleted=True)
    assert retained is not None
    assert retained["deleted_at"] is not None


def test_detaching_leaves_the_library_document_alone(store, engine, room_id):
    document = store.create(DOCUMENTS, {"title": "Deck"}, actor="dana")
    engine.attach(room_id, document_id=document["id"], source="test")
    result = engine.detach(room_id, document["id"], source="test")
    assert result["document_kept"] is True
    assert store.get(document["id"]) is not None


def test_detaching_leaves_another_datarooms_attachment_intact(engine, room_id, other_room):
    engine.attach(room_id, document_id="doc-1", source="test")
    engine.attach(other_room["id"], document_id="doc-1", source="test")
    result = engine.detach(room_id, "doc-1", source="test")
    assert result["other_datarooms_intact"] == [other_room["id"]]
    assert [g["data"]["document_id"] for g in engine.grants(other_room["id"])] == ["doc-1"]


def test_the_detached_row_is_still_listed_with_its_history(engine, room_id):
    engine.attach(room_id, document_id="doc-1", source="test")
    engine.detach(room_id, "doc-1", source="test")
    rows = engine.grants(room_id, include_detached=True)
    assert len(rows) == 1
    assert rows[0]["deleted_at"] is not None


def test_detaching_twice_is_refused(engine, room_id):
    engine.attach(room_id, document_id="doc-1", source="test")
    engine.detach(room_id, "doc-1", source="test")
    with pytest.raises(RevocationNotFound):
        engine.detach(room_id, "doc-1", source="test")


def test_detaching_something_never_attached_is_refused(engine, room_id):
    with pytest.raises(RevocationNotFound):
        engine.detach(room_id, "doc-absent", source="test")


def test_a_frozen_dataroom_refuses_a_detach(store, engine):
    """Attach while open, freeze, then the detach is refused."""
    room = store.create(ROOMS, {"name": "Acme", "account": "Acme"}, actor="dana")
    engine.attach(room["id"], document_id="doc-1", source="test")
    store.update(room["id"], {"frozen": True}, actor="dana", source="test")
    with pytest.raises(RevocationFrozen):
        engine.detach(room["id"], "doc-1", source="test")


def test_the_join_row_survived_the_refused_detach(store, engine):
    room = store.create(ROOMS, {"name": "Acme", "account": "Acme"}, actor="dana")
    engine.attach(room["id"], document_id="doc-1", source="test")
    store.update(room["id"], {"frozen": True}, actor="dana", source="test")
    with pytest.raises(RevocationFrozen):
        engine.detach(room["id"], "doc-1", source="test")
    assert len(engine.grants(room["id"])) == 1


def test_a_document_can_be_attached_again_after_a_detach(engine, room_id):
    engine.attach(room_id, document_id="doc-1", source="test")
    engine.detach(room_id, "doc-1", source="test")
    row = engine.attach(room_id, document_id="doc-1", source="test")
    assert row["data"]["status"] == "attached"


# --------------------------------------------------------------------------- #
# Purge a dataroom's access graph
# --------------------------------------------------------------------------- #


@pytest.fixture()
def full(engine, room_id, populated):
    engine.attach(room_id, document_id="doc-1", source="test")
    return populated


def test_every_link_in_the_room_goes(engine, room_id, full):
    result = engine.purge(room_id, confirm=room_id, source="test")
    assert result["by_collection"][LINKS] == 3
    assert engine.links(room_id) == []


def test_every_group_and_membership_goes(engine, room_id, full):
    engine.purge(room_id, confirm=room_id, source="test")
    assert engine.groups(room_id) == []
    assert engine.members(room_id) == []


def test_every_join_row_goes(engine, room_id, full):
    engine.purge(room_id, confirm=room_id, source="test")
    assert engine.grants(room_id) == []


def test_the_team_library_survives(engine, store, room_id):
    document = store.create(DOCUMENTS, {"title": "Deck"}, actor="dana")
    engine.attach(room_id, document_id=document["id"], source="test")
    engine.purge(room_id, confirm=room_id, source="test")
    assert store.get(document["id"]) is not None


def test_the_purge_reports_that_the_library_survived(engine, room_id):
    engine.attach(room_id, document_id="doc-1", source="test")
    assert engine.purge(room_id, confirm=room_id, source="test")["documents_kept"] is True


def test_the_underlying_viewers_survive(engine, room_id, full):
    before = len(engine.viewers(room_id))
    result = engine.purge(room_id, confirm=room_id, source="test")
    assert result["viewers_kept"] == before
    assert len(engine.viewers(room_id)) == before


def test_the_room_record_is_not_this_features_to_delete(store, engine, room_id):
    """WF-001 owns the room row; eleven other features read it."""
    engine.purge(room_id, confirm=room_id, source="test")
    assert store.get(room_id) is not None


def test_every_purged_row_is_retained_for_audit(engine, store, room_id, full):
    result = engine.purge(room_id, confirm=room_id, source="test")
    assert result["row_kept_for_audit"] is True
    for record_id in result["ids"]:
        assert store.db.get(record_id, include_deleted=True) is not None


def test_the_purge_is_one_audit_row(engine, store, room_id, full):
    engine.purge(room_id, confirm=room_id, source="test")
    rows = [
        e
        for e in store.audit(action="delete", limit=200)
        if e["record_id"] is None and e["source"] == "test"
    ]
    assert len(rows) == 1
    assert len(rows[0]["before_state"]["records"]) > 5


def test_it_is_reported_as_unrecoverable(engine, room_id):
    assert engine.purge(room_id, confirm=room_id, source="test")["reversible"] is IRREVERSIBLE


def test_a_purge_without_a_confirmation_is_refused(engine, room_id, full):
    with pytest.raises(RevocationBadRequest):
        engine.purge(room_id, confirm="", source="test")


def test_a_confirmation_naming_another_room_is_refused(engine, room_id, full):
    with pytest.raises(RevocationBadRequest):
        engine.purge(room_id, confirm="room_elsewhere", source="test")


def test_a_refused_purge_changes_nothing(engine, room_id, full):
    with pytest.raises(RevocationBadRequest):
        engine.purge(room_id, confirm="room_elsewhere", source="test")
    assert len(engine.links(room_id)) == 3


def test_purging_an_empty_room_removes_nothing(engine, room_id):
    result = engine.purge(room_id, confirm=room_id, source="test")
    assert result["removed"] == 0
    assert result["documents_kept"] is True


def test_purging_an_unknown_room_is_refused(engine):
    with pytest.raises(RevocationNotFound):
        engine.purge("room_absent", confirm="room_absent", source="test")


def test_another_room_is_untouched(engine, room_id, other_room, full):
    engine.create_link(other_room["id"], slug="contoso", source="test")
    engine.purge(room_id, confirm=room_id, source="test")
    assert engine.resolve(other_room["id"], "contoso")["resolves"] is True


def test_a_frozen_dataroom_can_still_be_purged(store, engine):
    """The freeze refuses structure, and a purge is not a restructure."""
    room = store.create(ROOMS, {"name": "Acme", "account": "Acme", "frozen": True}, actor="dana")
    engine.create_link(room["id"], slug="acme", source="test")
    result = engine.purge(room["id"], confirm=room["id"], source="test")
    assert result["removed"] == 1


def test_the_cascade_covers_every_collection_it_claims_to(engine, room_id, full):
    result = engine.purge(room_id, confirm=room_id, source="test")
    for collection in CASCADE_COLLECTIONS:
        assert collection in result["by_collection"], collection


def test_nothing_left_live_in_the_purged_room(engine, room_id, full):
    engine.purge(room_id, confirm=room_id, source="test")
    for collection in CASCADE_COLLECTIONS:
        assert engine._rows(collection, room_id) == [], collection


# --------------------------------------------------------------------------- #
# Resolution: request-time, not scheduled
# --------------------------------------------------------------------------- #


def test_a_live_room_link_resolves(engine, room_id, link):
    answer = engine.resolve(room_id, "northwind-overview")
    assert answer["resolves"] is True
    assert answer["reason"] == RESOLVES
    assert answer["link_id"] == link["id"]


def test_an_unknown_slug_does_not_resolve(engine, room_id):
    assert engine.resolve(room_id, "never-existed")["reason"] == NOT_FOUND


def test_an_empty_slug_does_not_resolve(engine, room_id):
    answer = engine.resolve(room_id, "")
    assert answer["resolves"] is False
    assert answer["reason"] == NOT_FOUND


def test_a_group_link_resolves_for_a_member(engine, room_id, group, member):
    engine.create_link(
        room_id, slug="procurement", target=TARGET_GROUP, group_id=group["id"], source="test"
    )
    answer = engine.resolve(room_id, "procurement", viewer="buyer@northwind.example")
    assert answer["resolves"] is True
    assert answer["reason"] == RESOLVES


def test_a_group_link_does_not_resolve_for_a_stranger(engine, room_id, group, member):
    engine.create_link(
        room_id, slug="procurement", target=TARGET_GROUP, group_id=group["id"], source="test"
    )
    answer = engine.resolve(room_id, "procurement", viewer="stranger@elsewhere.example")
    assert answer["resolves"] is False
    assert answer["reason"] == GONE_MEMBER_REMOVED


def test_a_group_link_does_not_resolve_for_an_anonymous_visitor(engine, room_id, group, member):
    engine.create_link(
        room_id, slug="procurement", target=TARGET_GROUP, group_id=group["id"], source="test"
    )
    assert engine.resolve(room_id, "procurement")["resolves"] is False


def test_the_viewer_email_is_matched_case_insensitively(engine, room_id, group, member):
    engine.create_link(
        room_id, slug="procurement", target=TARGET_GROUP, group_id=group["id"], source="test"
    )
    assert (
        engine.resolve(room_id, "procurement", viewer="BUYER@Northwind.Example")["resolves"] is True
    )


def test_a_purged_room_reports_its_own_cause(engine, room_id, link):
    engine.purge(room_id, confirm=room_id, source="test")
    answer = engine.resolve(room_id, "northwind-overview")
    assert answer["resolves"] is False
    assert answer["reason"] == GONE_CASCADE_ROOM
    assert answer["withheld_by"] == REVOKED_BY_ROOM


def test_every_answer_states_the_no_grace_period_rule(engine, room_id, link):
    for slug in ("northwind-overview", "never-existed"):
        assert engine.resolve(room_id, slug)["grace_period_minutes"] == 0


def test_every_answer_states_that_copies_are_not_recalled(engine, room_id, link):
    assert engine.resolve(room_id, "northwind-overview")["cached_copy_recall"] == CACHED_COPY_RECALL


def test_resolution_is_room_scoped(engine, room_id, other_room, link):
    assert engine.resolve(other_room["id"], "northwind-overview")["resolves"] is False


def test_the_answer_carries_the_slug_that_was_asked_about(engine, room_id):
    assert engine.resolve(room_id, "  spaced  ")["slug"] == "spaced"


def test_a_revoked_link_reports_the_direct_cause(engine, room_id, link):
    engine.revoke_link(room_id, link["id"], source="test")
    answer = engine.resolve(room_id, "northwind-overview")
    assert answer["reason"] == "revoked"
    assert answer["withheld_by"] == REVOKED_DIRECTLY


# --------------------------------------------------------------------------- #
# The audit source rule
# --------------------------------------------------------------------------- #


def mounted_route_shapes():
    """Every (method, path) the host actually mounted, read from the registry.

    The registry, not ``app.routes``. On this FastAPI version a mounted feature
    appears in ``app.routes`` as an ``_IncludedRouter`` wrapper with no
    ``methods`` attribute, so walking that list silently sees zero of the 800
    feature routes behind it - and every check below would pass vacuously. The
    registry records each feature's route shapes at mount time, which is also
    what ``GET /api/features`` reports and therefore what a reviewer reads.

    The loader is called first because features are mounted when the app's
    lifespan runs; it is idempotent, so calling it makes these checks
    independent of whether another test happened to open a ``TestClient``.
    """
    from dsr.features import REGISTRY, load_features

    load_features(app)
    shapes: dict[str, set[str]] = {}
    for record in REGISTRY.features:
        for route in record.routes:
            shapes.setdefault(route["path"], set()).update(route["methods"])
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        shapes.setdefault(path, set()).update(m for m in methods if m not in ("HEAD", "OPTIONS"))
    return shapes


def mounted_route_templates():
    """Every path the host mounted, with its ``{placeholder}`` segments intact."""
    return set(mounted_route_shapes())


def path_matches(pattern: str, actual: str) -> bool:
    """Does a concrete path match a route template?

    Every literal segment must be equal; a ``{placeholder}`` matches any one
    segment. So a recorded source naming a route that was renamed or removed
    fails here, which is the defect the contract names by hand.
    """
    want = pattern.strip("/").split("/")
    got = actual.strip("/").split("/")
    if len(want) != len(got):
        return False
    return all(w.startswith("{") or w == g for w, g in zip(want, got, strict=True))


# --------------------------------------------------------------------------- #
# Hard rule 4: the audit row must name the route that served the write.
# --------------------------------------------------------------------------- #


@pytest.fixture()
def exercised(http):
    """Run every researched write through HTTP, on a real room."""
    room_id = make_room(http)
    group = http.post(f"{PREFIX}/rooms/{room_id}/groups", json={"name": "Procurement"}).json()
    http.post(
        f"{PREFIX}/rooms/{room_id}/groups/{group['id']}/members",
        json={"email": "buyer@northwind.example"},
    )
    http.post(
        f"{PREFIX}/rooms/{room_id}/groups/{group['id']}/permissions",
        json={"document_id": "doc-1", "view": False, "download": False},
    )
    http.post(
        f"{PREFIX}/rooms/{room_id}/documents",
        json={"document_id": "doc-1", "title": "Deck"},
    )
    link = http.post(
        f"{PREFIX}/rooms/{room_id}/links",
        json={"slug": "acme", "domain": "share.acme.example"},
    ).json()["link"]
    group_link = http.post(
        f"{PREFIX}/rooms/{room_id}/links",
        json={"slug": "procurement", "target": "group", "group_id": group["id"]},
    ).json()["link"]
    http.post(f"{PREFIX}/rooms/{room_id}/links/{link['id']}/revoke", json={"reason": "cold"})
    http.post(f"{PREFIX}/rooms/{room_id}/links/{group_link['id']}/revoke", json={})
    http.post(
        f"{PREFIX}/rooms/{room_id}/groups/{group['id']}/delete", json={"confirm": group["id"]}
    )
    return {"room_id": room_id}


def feature_sources(http):
    """Every audit row this feature's own routes wrote, read from the core log.

    Read from ``/api/audit`` rather than from the engine, because the point is
    that the *core* audit log names them - that is where a reviewer would look
    after an incident, and a reader scoped to this feature could not tell whether
    the rows are really there.
    """
    entries = http.get("/api/audit?limit=1000").json()["entries"]
    return [e for e in entries if PREFIX in str(e.get("source") or "")]


def test_the_trail_route_refuses_an_unknown_room(http):
    """The trail is room-scoped, so it answers 404 rather than listing everything."""
    assert http.get(f"{PREFIX}/rooms/room_absent/trail").status_code == 404


def test_every_write_reached_the_database(exercised, http):
    assert feature_sources(http), "no audit row names this feature"


def test_every_recorded_source_names_a_mounted_route(exercised, http):
    """The audit row must name the route that served the write, with its ids in.

    The concrete ids are in the source, so this cannot be an exact-string
    membership check; it matches against the mounted path with each segment
    compared. That is the claim being made: the path shape and the method are
    real, and only the id values differ.
    """
    shapes = mounted_route_shapes()
    sources = feature_sources(http)
    assert sources, "no audit row names this feature"
    for entry in sources:
        verb, _, path = str(entry["source"]).partition(" ")
        templates = [
            template
            for template in shapes
            if len(template.strip("/").split("/")) == len(path.strip("/").split("/"))
        ]
        assert templates, f"{path!r} has no mounted route of that shape"
        matched = [template for template in templates if path_matches(template, path)]
        assert matched, f"{entry['source']!r} matches no mounted route"
        assert any(verb in shapes[template] for template in matched), (
            f"{entry['source']!r} names a path mounted for a different method"
        )


def test_the_registry_reports_the_same_routes_the_app_serves(exercised, http):
    """The registry and the live route table cannot disagree.

    If they did, the audit-source check above would be asserting against a stale
    table - which is the failure mode this whole section exists to prevent.
    """
    from dsr.features import REGISTRY

    record = REGISTRY.by_id(FEATURE_ID)
    assert record is not None
    reported = {route["path"] for route in record.routes}
    served = {path for path in mounted_route_shapes() if path.startswith(PREFIX)}
    assert reported == served


def test_a_source_with_the_wrong_shape_is_detected():
    """The matcher is the thing under test, so it gets its own test."""
    shapes = {"/api/wf-076/rooms/{room_id}/links": {"POST"}}
    assert path_matches("/api/wf-076/rooms/{room_id}/links", "/api/wf-076/rooms/room_x/links")
    assert not path_matches("/api/wf-076/rooms/{room_id}/links", "/api/wf-076/rooms/room_x/nope")
    assert not path_matches(
        "/api/wf-076/rooms/{room_id}/links", "/api/wf-076/rooms/room_x/links/extra"
    )
    assert "POST" in shapes["/api/wf-076/rooms/{room_id}/links"]


def test_a_renamed_route_would_be_caught():
    """The defect this rule exists for: a log naming a path nobody serves.

    Proves the matcher has teeth. The stale path below is the revoke route with
    its last segment renamed - the exact shape of a feature whose route was
    renamed while its audit rows kept naming the old one - and the matcher must
    reject it.
    """
    shapes = mounted_route_shapes()
    real = f"{PREFIX}/rooms/{{room_id}}/links/{{link_id}}/revoke"
    assert real in shapes, "the revoke route is not mounted, so this test would be vacuous"
    stale = f"{PREFIX}/rooms/room_x/links/link_y/revoke-and-restore"
    assert not any(path_matches(template, stale) for template in shapes)


def test_the_matcher_accepts_the_real_route_with_its_ids_filled_in():
    """The counterpart, so a rejection above cannot pass by rejecting everything."""
    shapes = mounted_route_shapes()
    concrete = f"{PREFIX}/rooms/room_x/links/link_y/revoke"
    matched = [template for template in shapes if path_matches(template, concrete)]
    assert matched == [f"{PREFIX}/rooms/{{room_id}}/links/{{link_id}}/revoke"]
    assert "POST" in shapes[matched[0]]


def test_a_route_under_a_different_prefix_is_rejected():
    """Another feature's path is not this feature's route, however similar."""
    shapes = mounted_route_shapes()
    stale = "/api/wf-065/rooms/room_x/links/link_y/revoke"
    assert not any(path_matches(template, stale) for template in shapes)


def test_a_route_nobody_mounts_is_rejected():
    """The shape matcher must not accept a path of a length no route has."""
    shapes = mounted_route_shapes()
    assert not any(
        path_matches(template, f"{PREFIX}/rooms/room_x/deeper/still/nonexistent")
        for template in shapes
    )


def test_the_source_is_built_from_the_router_prefix():
    module = load_feature()
    assert module._source("POST", "/rooms/room_x/links") == (
        f"POST {module.router.prefix}/rooms/room_x/links"
    )


def test_the_source_changes_with_the_prefix():
    """Two mounts, two sources: the prefix is read, never written out."""
    module = load_feature()
    assert module.router.prefix in module._source("POST", "/x")


def test_every_write_route_really_is_the_one_the_source_names():
    """Every route on this router is mounted, for the method it declares.

    Read from the registry rather than from ``app.routes``: a mounted feature
    sits behind an ``_IncludedRouter`` there, so the list of feature routes
    would come back empty and this would assert nothing.
    """
    from dsr.features import REGISTRY

    record = REGISTRY.by_id(FEATURE_ID)
    shapes = mounted_route_shapes()
    assert record.routes, "the registry reports no routes for this feature"
    for route in record.routes:
        assert route["path"] in shapes, route["path"]
        for method in route["methods"]:
            assert method in shapes[route["path"]], (method, route["path"])


def test_every_route_on_the_router_is_one_the_registry_reports():
    """The module's own route table and the host's record agree, one for one."""
    from dsr.features import REGISTRY

    module = load_feature()
    declared = {(r.path, m) for r in module.router.routes for m in (r.methods or set())}
    reported = {
        (route["path"], method)
        for route in REGISTRY.by_id(FEATURE_ID).routes
        for method in route["methods"]
    }
    assert declared == reported


# --------------------------------------------------------------------------- #
# Over HTTP
# --------------------------------------------------------------------------- #


def test_the_feature_is_mounted_by_discovery_and_nothing_failed(http):
    registry = http.get("/api/features").json()
    entry = next(f for f in registry["features"] if f["id"] == FEATURE_ID)
    assert entry["loaded"] is True
    assert entry["error"] == ""
    assert entry["prefix"] == PREFIX
    assert registry["failed_count"] == 0


def test_it_advertises_every_route_it_mounts(http):
    entry = http.get(f"/api/features/{FEATURE_ID}").json()
    assert entry["routes"]
    assert all(route["path"].startswith(PREFIX) for route in entry["routes"])


def test_its_refusal_handler_is_registered(http):
    entry = http.get(f"/api/features/{FEATURE_ID}").json()
    assert "RevocationRefusal" in entry["exception_handlers"]


def test_the_vocabulary_route_needs_no_store(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert set(body["operations"]) >= {
        "revoke_link",
        "delete_group",
        "remove_member",
        "set_permissions",
        "attach",
        "detach",
        "purge",
    }


def test_the_vocabulary_carries_the_researched_quote(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    quote = body["operations"]["revoke_link"]["quote"].replace("*", "").lower()
    assert "the row is kept in the database for audit" in quote
    assert "soft-deletes the link" in quote


def test_the_vocabulary_carries_the_cascade_quote(http):
    quote = http.get(f"{PREFIX}/vocabulary").json()["operations"]["delete_group"]["quote"].lower()
    assert "every share link pointing at it" in quote
    assert "stop resolving immediately" in quote


def test_the_vocabulary_states_the_no_grace_period(http):
    guarantee = http.get(f"{PREFIX}/vocabulary").json()["guarantee"]
    assert guarantee["grace_period_minutes"] == 0
    assert guarantee["cached_copy_recall"] == "none"


def test_the_vocabulary_states_the_freeze_rule_and_its_scope(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["frozen_refusals"] == ["attach", "detach", "move"]
    assert "Revocation is not in that list" in body["frozen_rule"]


def test_the_inferences_route_lists_every_open_reading(http):
    body = http.get(f"{PREFIX}/inferences").json()
    ids = {entry["id"] for entry in body["inferences"]}
    assert {
        "cascade-keeps-rows",
        "membership-gates-resolution",
        "download-requires-view",
    } <= ids
    for entry in body["inferences"]:
        assert entry["claim"] and entry["reading"] and entry["basis"] and entry["change_it"]


def test_every_inference_is_reachable_and_fully_documented():
    """A reading with no basis or no lever is an opinion, not a decision."""
    described = inference_module.describe()
    assert described["count"] == len(inference_module.INFERENCES)
    for entry in inference_module.INFERENCES:
        for key in ("id", "claim", "reading", "basis", "change_it", "blast_radius"):
            assert entry.get(key), (entry["id"], key)
        assert inference_module.by_id(entry["id"]) == dict(entry)


def test_an_unknown_inference_id_is_none():
    assert inference_module.by_id("not-a-reading") is None


def test_the_readings_cover_the_gaps_this_build_actually_took():
    """Each inference must correspond to a decision the code really made."""
    ids = {entry["id"] for entry in inference_module.INFERENCES}
    assert {
        "cascade-keeps-rows",
        "cascade-does-not-rename-slugs",
        "membership-gates-resolution",
        "download-requires-view",
        "frozen-governs-structure-not-access",
        "irreversible-actions-echo-their-target",
        "document-team-defaults-to-the-dataroom",
        "purge-stops-at-the-access-graph",
        "a-room-flag-gates-the-freeze",
    } <= ids


def test_summary_answers_without_a_room(http):
    body = http.get(f"{PREFIX}/summary").json()
    assert body["guarantee"]["grace_period_minutes"] == 0
    assert body["collections"] == list(ALL_COLLECTIONS)


def test_an_unknown_room_is_a_404_on_every_room_scoped_read(http):
    for path in (
        "/state",
        "/links",
        "/groups",
        "/viewers",
        "/documents",
        "/trail",
        "/resolve?slug=x",
    ):
        response = http.get(f"{PREFIX}/rooms/room_absent{path}")
        assert response.status_code == 404, path


def test_a_full_revoke_over_http(http):
    room_id = make_room(http)
    created = http.post(
        f"{PREFIX}/rooms/{room_id}/links",
        json={"slug": "acme", "domain": "share.acme.example", "label": "Acme"},
    ).json()
    assert created["link"]["data"]["slug"] == "acme"

    revoked = http.post(
        f"{PREFIX}/rooms/{room_id}/links/{created['link']['id']}/revoke",
        json={"reason": "deal went cold"},
        headers={"X-Actor": "sam"},
    ).json()
    assert revoked["revoked"] is True
    assert revoked["revoked_by"] == "sam"
    assert revoked["row_kept"] is True
    assert revoked["slug_released"] is True

    assert http.get(f"{PREFIX}/rooms/{room_id}/resolve?slug=acme").json()["reason"] == "revoked"


def test_the_actor_defaults_rather_than_being_unknown(http):
    room_id = make_room(http)
    created = http.post(f"{PREFIX}/rooms/{room_id}/links", json={"slug": "acme"}).json()
    revoked = http.post(
        f"{PREFIX}/rooms/{room_id}/links/{created['link']['id']}/revoke", json={}
    ).json()
    assert revoked["revoked_by"] == "dana"


def test_the_retained_row_is_readable_over_http_after_a_revoke(http):
    room_id = make_room(http)
    created = http.post(f"{PREFIX}/rooms/{room_id}/links", json={"slug": "acme"}).json()
    link_id = created["link"]["id"]
    http.post(f"{PREFIX}/rooms/{room_id}/links/{link_id}/revoke", json={"reason": "cold"})

    ordinary = http.get(f"{PREFIX}/rooms/{room_id}/links/{link_id}?include_revoked=false")
    assert ordinary.status_code == 404

    retained = http.get(f"{PREFIX}/rooms/{room_id}/revocations/{link_id}")
    assert retained.status_code == 200
    body = retained.json()
    assert body["live"] is False
    assert body["data"]["revocation_reason"] == "cold"
    assert {e["action"] for e in body["audit"]} >= {"update", "delete"}


def test_a_double_revoke_is_a_409_over_http(http):
    room_id = make_room(http)
    created = http.post(f"{PREFIX}/rooms/{room_id}/links", json={"slug": "acme"}).json()
    link_id = created["link"]["id"]
    http.post(f"{PREFIX}/rooms/{room_id}/links/{link_id}/revoke", json={})
    again = http.post(f"{PREFIX}/rooms/{room_id}/links/{link_id}/revoke", json={})
    assert again.status_code == 409
    assert again.json()["error"] == "conflict"


def test_a_slug_already_held_is_a_409_over_http(http):
    room_id = make_room(http)
    http.post(f"{PREFIX}/rooms/{room_id}/links", json={"slug": "acme"})
    second = http.post(f"{PREFIX}/rooms/{room_id}/links", json={"slug": "acme"})
    assert second.status_code == 409


def test_a_missing_confirmation_is_a_400_over_http(http):
    room_id = make_room(http)
    group = http.post(f"{PREFIX}/rooms/{room_id}/groups", json={"name": "G"}).json()
    denied = http.post(f"{PREFIX}/rooms/{room_id}/groups/{group['id']}/delete", json={})
    assert denied.status_code == 400
    assert denied.json()["error"] == "bad_request"


def test_a_purge_without_a_confirmation_is_a_400_over_http(http):
    room_id = make_room(http)
    assert http.post(f"{PREFIX}/rooms/{room_id}/purge", json={}).status_code == 400


def test_a_frozen_attach_is_a_409_naming_the_freeze_over_http(http):
    room_id = make_room(http, frozen=True)
    denied = http.post(f"{PREFIX}/rooms/{room_id}/documents", json={"document_id": "doc-1"})
    assert denied.status_code == 409
    assert denied.json()["error"] == "dataroom_frozen"


def test_a_cross_team_attach_is_a_422_over_http(http):
    room_id = make_room(http)
    denied = http.post(
        f"{PREFIX}/rooms/{room_id}/documents",
        json={"document_id": "doc-1", "document_team": "Contoso Health"},
    )
    assert denied.status_code == 422
    assert denied.json()["error"] == "cross_team_refused"


def test_download_without_view_is_a_400_over_http(http):
    room_id = make_room(http)
    group = http.post(f"{PREFIX}/rooms/{room_id}/groups", json={"name": "G"}).json()
    denied = http.post(
        f"{PREFIX}/rooms/{room_id}/groups/{group['id']}/permissions",
        json={"document_id": "doc-1", "view": False, "download": True},
    )
    assert denied.status_code == 400


def test_the_group_cascade_over_http(http):
    room_id = make_room(http)
    group = http.post(f"{PREFIX}/rooms/{room_id}/groups", json={"name": "G"}).json()
    http.post(
        f"{PREFIX}/rooms/{room_id}/groups/{group['id']}/members",
        json={"email": "buyer@northwind.example"},
    )
    http.post(
        f"{PREFIX}/rooms/{room_id}/links",
        json={"slug": "procurement", "target": "group", "group_id": group["id"]},
    )
    deleted = http.post(
        f"{PREFIX}/rooms/{room_id}/groups/{group['id']}/delete",
        json={"confirm": group["id"]},
    ).json()
    assert deleted["by_collection"][LINKS] == 1
    assert deleted["row_kept_for_audit"] is True
    assert (
        http.get(f"{PREFIX}/rooms/{room_id}/resolve?slug=procurement").json()["reason"]
        == GONE_CASCADE_GROUP
    )


def test_the_member_removal_over_http(http):
    room_id = make_room(http)
    group = http.post(f"{PREFIX}/rooms/{room_id}/groups", json={"name": "G"}).json()
    added = http.post(
        f"{PREFIX}/rooms/{room_id}/groups/{group['id']}/members",
        json={"email": "buyer@northwind.example"},
    ).json()
    http.post(
        f"{PREFIX}/rooms/{room_id}/links",
        json={"slug": "procurement", "target": "group", "group_id": group["id"]},
    )
    removed = http.post(
        f"{PREFIX}/rooms/{room_id}/groups/{group['id']}/members/{added['member']['id']}/remove"
    ).json()
    assert removed["viewer_kept"] is True
    assert removed["resolves_now"]["reason"] == GONE_MEMBER_REMOVED

    viewers = http.get(f"{PREFIX}/rooms/{room_id}/viewers").json()
    assert viewers["count"] == 1
    assert viewers["viewers"][0]["in_any_group"] is False


def test_the_detach_over_http_leaves_the_library_document(http):
    room_id = make_room(http)
    document = http.post("/api/records/document", json={"title": "Deck"}).json()
    http.post(
        f"{PREFIX}/rooms/{room_id}/documents",
        json={"document_id": document["id"], "title": "Deck"},
    )
    detached = http.post(f"{PREFIX}/rooms/{room_id}/documents/{document['id']}/detach").json()
    assert detached["document_kept"] is True
    assert http.get(f"/api/records/document/{document['id']}").status_code == 200


def test_the_slug_status_route(http):
    room_id = make_room(http)
    created = http.post(
        f"{PREFIX}/rooms/{room_id}/links",
        json={"slug": "acme", "domain": "share.acme.example"},
    ).json()
    held = http.get(f"{PREFIX}/rooms/{room_id}/slugs/acme").json()
    assert held["available"] is False
    assert held["held_by"] == [created["link"]["id"]]

    http.post(f"{PREFIX}/rooms/{room_id}/links/{created['link']['id']}/revoke", json={})
    freed = http.get(f"{PREFIX}/rooms/{room_id}/slugs/acme").json()
    assert freed["available"] is True
    assert freed["held_by"] == []


def test_the_trail_shows_this_features_own_writes(http):
    room_id = make_room(http)
    created = http.post(f"{PREFIX}/rooms/{room_id}/links", json={"slug": "acme"}).json()
    http.post(f"{PREFIX}/rooms/{room_id}/links/{created['link']['id']}/revoke", json={})
    trail = http.get(f"{PREFIX}/rooms/{room_id}/trail").json()
    assert trail["count"] >= 3
    assert all(PREFIX in row["source"] for row in trail["trail"])


def test_the_group_view_assembles_its_parts(http):
    room_id = make_room(http)
    group = http.post(f"{PREFIX}/rooms/{room_id}/groups", json={"name": "G"}).json()
    http.post(
        f"{PREFIX}/rooms/{room_id}/groups/{group['id']}/members",
        json={"email": "buyer@northwind.example"},
    )
    body = http.get(f"{PREFIX}/rooms/{room_id}/groups/{group['id']}").json()
    assert body["member_count"] == 1
    assert body["permission_count"] == 0
    assert body["link_count"] == 0


def test_the_room_state_route(http):
    room_id = make_room(http, frozen=True)
    body = http.get(f"{PREFIX}/rooms/{room_id}/state").json()
    assert body["frozen"] is True
    assert body["team"] == "Northwind Traders"
    assert "attach" in body["refused_actions"]


def test_the_documents_route_lists_detached_rows_with_their_history(http):
    room_id = make_room(http)
    http.post(f"{PREFIX}/rooms/{room_id}/documents", json={"document_id": "doc-1", "title": "D"})
    http.post(f"{PREFIX}/rooms/{room_id}/documents/doc-1/detach")
    rows = http.get(f"{PREFIX}/rooms/{room_id}/documents").json()["documents"]
    assert len(rows) == 1
    assert rows[0]["attached"] is False


def test_every_advertised_route_answers_without_a_5xx(http):
    """The end-to-end check CI runs, over this feature's own routes."""
    room_id = make_room(http)
    entry = http.get(f"/api/features/{FEATURE_ID}").json()
    for route in entry["routes"]:
        concrete = re.sub(r"\{room_id\}", room_id, route["path"])
        concrete = re.sub(r"\{[^}]+\}", "absent_thing", concrete)
        for method in route["methods"]:
            response = http.request(
                method, concrete, json={} if method in ("POST", "PATCH", "PUT") else None
            )
            assert response.status_code < 500, (method, concrete, response.status_code)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


@pytest.fixture()
def seeded(store):
    """Run the feature's own seed over four rooms shaped like the demo dataset.

    Two library documents per room, because that is what ``backend/seed.py``
    produces and because the seed attaches two of them: a fixture that supplied
    one would make the seed decline, and the decline would look like a pass.
    """
    module = load_feature()
    from datetime import datetime, timezone

    rooms = [
        store.create(ROOMS, {"name": "Northwind", "account": "Northwind Traders"}, actor="dana"),
        store.create(ROOMS, {"name": "Contoso", "account": "Contoso Health"}, actor="sam"),
        store.create(ROOMS, {"name": "Fabrikam", "account": "Fabrikam Logistics"}, actor="dana"),
        store.create(ROOMS, {"name": "Adventure", "account": "Adventure Works"}, actor="sam"),
    ]
    room_ids = [(r["id"], r["data"]["account"]) for r in rooms]
    for room in rooms:
        for kind in ("deck", "pdf"):
            store.create(
                DOCUMENTS,
                {"title": f"{kind.upper()} for {room['data']['name']}", "kind": kind},
                room_id=room["id"],
                actor="dana",
                source="seed",
            )
    summary = module.seed(
        store.db, {"room_ids": room_ids, "now": datetime.now(timezone.utc), "rng": None}
    )
    return {
        "summary": summary,
        "northwind": room_ids[0][0],
        "frozen": room_ids[1][0],
        "fabrikam": room_ids[2][0],
        "store": store,
    }


def test_the_seed_says_what_it_added(seeded):
    assert isinstance(seeded["summary"], str) and seeded["summary"]


def test_the_summary_reports_the_revoked_count(seeded):
    """The revoked count is part of the claim, and it must be a real number.

    An earlier version subtracted the all-room live total once per room, which
    printed "-7 already revoked" in the seed log. A seeder that writes a wrong
    number into a demo log is worse than one that writes none.
    """
    assert "already revoked)" in seeded["summary"]
    revoked = seeded["summary"].split("(")[1].split(" already revoked")[0]
    assert revoked.isdigit(), f"revoked count is not a plain number: {revoked!r}"
    assert int(revoked) >= 1, seeded["summary"]


def test_the_summary_is_measured_not_asserted(seeded):
    """A seed summary that drifts from the rows is worse than none.

    The seed prints its summary across every room it touched, so this compares
    against the same measure the summary reports rather than against one room -
    which is the check that would actually catch a drifted number.
    """
    eng = Revocation(seeded["store"], source_prefix=PREFIX)
    by_room = eng.summary()["by_room"]
    rooms = [seeded["northwind"], seeded["frozen"], seeded["fabrikam"]]

    def total(collection):
        return sum(by_room.get(room, {}).get(collection, 0) for room in rooms)

    assert f"{total(GROUPS)} groups" in seeded["summary"]
    assert f"{total(VIEWERS)} viewers" in seeded["summary"]
    assert f"{total(LINKS)} live links" in seeded["summary"]


def test_an_active_room_gets_two_audiences(seeded):
    eng = Revocation(seeded["store"], source_prefix=PREFIX)
    names = {g["data"]["name"] for g in eng.groups(seeded["northwind"])}
    assert names == {"Procurement", "Legal review"}


def test_a_custom_domain_link_exists_so_the_rename_has_something_to_do(seeded):
    eng = Revocation(seeded["store"], source_prefix=PREFIX)
    custom = [
        row for row in eng.links(seeded["northwind"]) if payload(row).get("custom_domain") is True
    ]
    assert len(custom) == 1


def test_a_frozen_room_is_seeded(seeded):
    eng = Revocation(seeded["store"], source_prefix=PREFIX)
    assert eng.frozen(seeded["frozen"]) is True


def test_the_frozen_room_still_has_a_live_link(seeded):
    eng = Revocation(seeded["store"], source_prefix=PREFIX)
    assert eng.links(seeded["frozen"])


def test_the_frozen_room_has_a_link_that_can_still_be_revoked(seeded):
    eng = Revocation(seeded["store"], source_prefix=PREFIX)
    row = eng.links(seeded["frozen"])[0]
    slug = payload(row)["slug"]
    eng.revoke_link(seeded["frozen"], row["id"], source="test")
    assert eng.resolve(seeded["frozen"], slug)["resolves"] is False


def test_a_link_revoked_before_you_arrived_is_seeded(seeded):
    eng = Revocation(seeded["store"], source_prefix=PREFIX)
    revoked = eng.links(seeded["fabrikam"], include_revoked=True)
    assert len(revoked) == 1
    assert revoked[0]["deleted_at"] is not None


def test_that_pre_revoked_link_has_a_retained_row_and_a_trail(seeded):
    eng = Revocation(seeded["store"], source_prefix=PREFIX)
    row = eng.links(seeded["fabrikam"], include_revoked=True)[0]
    retained = eng.retained(seeded["fabrikam"], row["id"])
    assert retained["live"] is False
    assert "delete" in {entry["action"] for entry in retained["audit"]}


def test_that_pre_revoked_link_left_its_slug_free(seeded):
    eng = Revocation(seeded["store"], source_prefix=PREFIX)
    assert eng.slug_available(seeded["fabrikam"], "fabrikam-diligence") is True


def test_that_pre_revoked_link_shows_up_in_the_trail(seeded):
    eng = Revocation(seeded["store"], source_prefix=PREFIX)
    trail = eng.trail(seeded["fabrikam"])
    assert trail
    assert all(PREFIX in row["source"] for row in trail)
    assert any(row["action"] == "delete" for row in trail)


def test_a_viewer_in_no_group_is_seeded(seeded):
    eng = Revocation(seeded["store"], source_prefix=PREFIX)
    by_id = {row["id"]: row for row in eng.viewers(seeded["northwind"])}
    members = {str(payload(m).get("viewer_id") or "") for m in eng.members(seeded["northwind"])}
    assert set(by_id) - members, "every seeded viewer is in a group"


def test_a_cross_team_library_document_is_seeded(seeded):
    store = seeded["store"]
    cross = [
        row
        for row in store.list(DOCUMENTS, room_id=seeded["frozen"], limit=50)
        if payload(row).get("team") == "Contoso Health"
    ]
    assert cross, "nothing in the library names a different team"


def test_attaching_that_document_to_another_room_is_refused(seeded):
    eng = Revocation(seeded["store"], source_prefix=PREFIX)
    store = seeded["store"]
    cross = next(
        row
        for row in store.list(DOCUMENTS, room_id=seeded["frozen"], limit=50)
        if payload(row).get("team") == "Contoso Health"
    )
    with pytest.raises(RevocationCrossTeam):
        eng.attach(seeded["northwind"], document_id=cross["id"], source="test")


def test_a_hidden_item_is_seeded_so_the_permission_rule_is_visible(seeded):
    eng = Revocation(seeded["store"], source_prefix=PREFIX)
    hidden = [
        row for row in eng.permissions(seeded["northwind"]) if payload(row).get("view") is False
    ]
    assert hidden


def test_the_seed_declines_gracefully_with_too_few_rooms(store):
    module = load_feature()
    assert module.seed(store.db, {"room_ids": [], "now": None, "rng": None}) is None


def test_the_seed_declines_gracefully_without_a_first_room(store):
    module = load_feature()
    room_ids = [("room_x", "Acme")] * 3
    assert module.seed(store.db, {"room_ids": room_ids, "now": None, "rng": None}) is None
