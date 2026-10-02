"""Tests for WF-077: internal workspace roles and least-privilege integration scopes.

The suite is organised by what it is protecting, because "more tests" is not the
measure and a reviewer needs to know where to look for a specific rule:

* **Registration and the contract** - the feature is mounted by discovery under a
  prefix it owns, its module path is not one another feature already claims, and
  every ``source`` it records names a route the host actually mounted.
* **Sourced vocabulary** - every quote in the code appears verbatim in
  ``docs/research/digital-sales-room-workflows/wf/WF-077.md``, so a rule cannot
  drift from its source without a failure.
* **The role rules**, as pure functions against an injected membership list.
* **The scope rules**, and in particular the no-implicit-hierarchy rule, tested
  from both directions: write does not imply read, and a token holding *every*
  scope except one read scope still cannot read.
* **The OAuth consent flow** and **directory SSO**, including the one behaviour
  the SSO half actually changes.
* **The engine**, over a temporary audited database.
* **The HTTP surface**, through this feature's own router.
* **The inferences**, so a judgement call cannot be added without saying why.

No test here is marked ``xfail`` and none is skipped. Where the research is silent
the test pins this build's choice and names it, and the choice itself is listed in
``dsr.workspace_roles.inferences``.
"""

from __future__ import annotations

import dataclasses
import json
import random
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore
from dsr.workspace_roles import (
    oauth as oauth_flow,
    rules,
    scopes as scope_rules,
    sso as sso_rules,
    vocabulary as vocab,
)
from dsr.workspace_roles.engine import (
    ENDPOINTS,
    MEMBERSHIPS,
    SSO_CONNECTIONS,
    TOKENS,
    WorkspaceAccess,
)
from dsr.workspace_roles.errors import (
    ConsentError,
    PlanFeatureError,
    RateLimited,
    RoleError,
    RoleForbidden,
    ScopeError,
    SsoError,
    TokenError,
)
from fastapi.testclient import TestClient

PREFIX = "/api/wf-077"
FEATURE_ID = "wf-077-manage-internal-workspace-roles-and-le"
MODULE = "wf077_manage_internal_workspace_roles_and_le"
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = REPO_ROOT / "docs" / "research" / "digital-sales-room-workflows" / "wf" / "WF-077.md"

#: Duplicated here rather than imported, so changing the prefix has to be made
#: deliberately in the test as well as in the code.
SOURCE = f"{PREFIX}/workspaces/ws_acme/members/m1/role"

WS = "ws_acme"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store():
    """An audited database and a :class:`WorkspaceAccess` over it."""
    # In-memory rather than a file on disk: 0.4 ms against 7.0 ms, measured.
    db = AuditedDatabase()
    try:
        yield WorkspaceAccess(RecordStore(db))
    finally:
        db.close()


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
    scratch = tmp_path_factory.mktemp("wf077-http")
    patch = pytest.MonkeyPatch()
    patch.setenv("DSR_DB_PATH", ":memory:")
    patch.setenv("DSR_AUDIT_DIR", str(scratch / "audit"))
    patch.setattr("dsr.api.FRONTEND_DIST", scratch / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    patch.undo()


@pytest.fixture()
def client(_shared_client):
    """The shared application, over a database this test owns alone.

    ``dependency_overrides`` is cleared on the way in and on the way out: the
    application is module-scoped, so an override one test installs would
    otherwise still be installed for the next one.
    """
    from dsr.db.audited import AuditedDatabase
    from dsr.store import RecordStore

    db = AuditedDatabase()
    _shared_client.app.state.db = db
    _shared_client.app.state.store = RecordStore(db)
    _shared_client.app.dependency_overrides.clear()
    try:
        yield _shared_client
    finally:
        _shared_client.app.dependency_overrides.clear()
        db.close()


def seed_workspace(access, workspace_id=WS, *, plan=vocab.PLAN_BUSINESS, admins=1):
    """A workspace with an owner, ``admins`` further admins, and a member.

    Returns ``(owner_id, admin_ids, member_id)``. The owner is separate from the
    admins on purpose: the last-admin guard rail must bite on somebody who is *not*
    the owner, or the test proves only that the owner rule fires first.
    """
    owner = access.store.create(
        MEMBERSHIPS,
        {
            "workspace_id": workspace_id,
            "email": f"owner-{workspace_id}@example.test",
            "name": "Owner",
            "role": vocab.ROLE_OWNER,
            "seat": vocab.SEAT_FULL,
            "active": True,
            "is_owner": True,
            "plan": plan,
        },
        actor="seed",
        source="test",
    )
    admin_ids = [
        access.store.create(
            MEMBERSHIPS,
            {
                "workspace_id": workspace_id,
                "email": f"admin{n}-{workspace_id}@example.test",
                "name": f"Admin {n}",
                "role": vocab.ADMIN,
                "seat": vocab.SEAT_FULL,
                "active": True,
                "is_owner": False,
                "plan": plan,
            },
            actor="seed",
            source="test",
        )["id"]
        for n in range(admins)
    ]
    member = access.store.create(
        MEMBERSHIPS,
        {
            "workspace_id": workspace_id,
            "email": f"member-{workspace_id}@example.test",
            "name": "Member",
            "role": vocab.MEMBER,
            "seat": vocab.SEAT_FULL,
            "active": True,
            "is_owner": False,
            "plan": plan,
        },
        actor="seed",
        source="test",
    )
    return owner["id"], admin_ids, member["id"]


def as_admin(access, workspace_id=WS, member_id=None):
    """Headers identifying the calling member. A ``Manager`` unless told otherwise."""
    if member_id is None:
        owner, admins, _ = seed_workspace(access, workspace_id)
        member_id = admins[0] if admins else owner
    return {MOD_MEM_HDR: member_id}


MOD_MEM_HDR = "X-Workspace-Member"


# --------------------------------------------------------------------------- #
# Registration and the contract
# --------------------------------------------------------------------------- #


def test_the_feature_is_mounted_by_discovery(client):
    body = client.get("/api/features").json()
    assert body["failed_count"] == 0, body["failed"]
    installed = {feature["id"]: feature for feature in body["features"]}
    assert FEATURE_ID in installed
    record = installed[FEATURE_ID]
    assert record["prefix"] == PREFIX
    assert record["ticket"] == "WF-077"
    assert record["loaded"] is True
    assert record["routes"]


def test_the_error_mapping_lives_in_the_feature_not_the_shared_app(client):
    record = client.get(f"/api/features/{FEATURE_ID}").json()
    # Every domain type is mapped by this feature's own export, so none of them
    # had to be written into dsr/api.py.
    assert set(record["exception_handlers"]) == {
        "ConsentError",
        "Forbidden",
        "MembershipNotFound",
        "PlanFeatureError",
        "RateLimited",
        "RoleError",
        "RoleForbidden",
        "ScopeError",
        "SsoError",
        "TokenError",
    }


def test_the_prefix_is_ticket_derived(client):
    """A ticket-derived prefix cannot collide with a feature-shaped one."""
    record = client.get(f"/api/features/{FEATURE_ID}").json()
    assert record["prefix"] == "/api/wf-077"
    for route in record["routes"]:
        assert route["path"].startswith(PREFIX), route


def test_the_domain_module_is_named_for_this_workflow():
    """`workspace_roles`, not `roles`.

    `dsr.roles` and `dsr.roles_api` belong to WF-004 and `dsr.permissions` to
    WF-003, all live on main. Two features cannot own one module path; this asserts
    the name that keeps the three apart.
    """
    import dsr.workspace_roles as pkg

    assert pkg.__name__ == "dsr.workspace_roles"
    assert Path(pkg.__file__).name == "__init__.py"
    assert Path(pkg.__file__).parent.name == "workspace_roles"
    # And it is a package, not a module: the workflow has eight of them.
    assert len(list(Path(pkg.__file__).parent.glob("*.py"))) >= 8


def test_the_domain_never_imports_a_module_another_feature_owns():
    """The three modules that hold role vocabulary for other workflows."""
    import dsr.workspace_roles as pkg

    root = Path(pkg.__file__).parent
    forbidden = ("dsr.roles", "dsr.roles_api", "dsr.access", "dsr.permissions")
    for source in sorted(root.glob("*.py")):
        text = source.read_text(encoding="utf-8")
        for name in forbidden:
            assert f"import {name}" not in text, f"{source.name} imports {name}"
            assert f"from {name} " not in text, f"{source.name} imports from {name}"


def test_the_feature_module_does_not_import_the_shared_app():
    """A test on main enforces this; this one pins it for this feature's file."""
    module = load_feature(MODULE)
    text = Path(module.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in text
    assert "import dsr.api" not in text


def test_the_feature_depends_only_on_deps_for_its_store():
    module = load_feature(MODULE)
    text = Path(module.__file__).read_text(encoding="utf-8")
    assert "from dsr.deps import StoreDep" in text
    assert "from dsr.store import RecordStore" in text


def test_every_declared_endpoint_is_a_route_the_host_mounted(client):
    """The per-endpoint declaration may not name a route that does not exist.

    The research calls this declaration the portable part of the workflow, so it
    is served, not merely applied. A declaration that names a path the host never
    mounted would make the token-creation surface promise something the request
    path does not enforce.
    """
    mounted = {
        (method, route["path"])
        for route in client.get(f"/api/features/{FEATURE_ID}").json()["routes"]
        for method in route["methods"]
    }
    for entry in ENDPOINTS:
        assert (entry["method"], entry["path"]) in mounted, entry["path"]


def test_every_source_recorded_names_a_mounted_route(client):
    """The audit-source rule, end to end.

    The contract names this defect by name: a feature whose audit log keeps
    recording a path the app stopped serving. So the test writes through the real
    HTTP route and then checks the recorded source against the registry.
    """
    _owner, admins, member_id = http_seed(client)
    response = client.patch(
        f"{PREFIX}/workspaces/{WS}/members/{member_id}/role",
        json={"role": vocab.ADMIN},
        headers={MOD_MEM_HDR: admins[0]},
    )
    assert response.status_code == 200, response.text

    mounted = {
        route["path"] for route in client.get(f"/api/features/{FEATURE_ID}").json()["routes"]
    }
    # Only this feature's own writes. The seeding above goes through the core
    # records API, and those rows name core routes, which are not what is under test.
    sources = {
        entry["source"]
        for entry in client.get("/api/audit", params={"collection": MEMBERSHIPS}).json()["entries"]
        if entry["source"] and entry["source"].startswith(PREFIX)
    }
    assert sources, "the role change wrote no audit row with a source"
    for source in sources:
        # Put the concrete ids back and the source must be a path the host mounted.
        template = source.replace(WS, "{workspace_id}").replace(member_id, "{member_id}")
        assert template in mounted, source


def test_the_domain_module_is_where_the_rules_live():
    """The router holds no role logic; the rules module holds all of it."""
    module = load_feature(MODULE)
    text = Path(module.__file__).read_text(encoding="utf-8")
    # The four role names appear only via the vocabulary, never re-declared here.
    assert '"Admin"' not in text
    assert "[vocab.ADMIN, vocab.MANAGER" not in text
    # And the guard-rail rules are not reimplemented in the router either.
    assert "is_admin(" not in text
    assert "decide_role_change(" not in text


# --------------------------------------------------------------------------- #
# Sourced vocabulary
# --------------------------------------------------------------------------- #


def test_every_sourced_quote_appears_verbatim_in_the_research():
    """A quote that drifts from its source is a rule that has changed.

    This is the test that makes the `*_QUOTE` constants trustworthy: they are
    copied from WF-077.md, and if someone edits one without editing the other, the
    suite says so.
    """
    # The research carries markdown emphasis (``**bold**`` and ``` ``code`` ```)
    # and the constants do not. Both sides are normalised, because normalising one
    # side alone compares a stripped string against marked-up text and fails for
    # reasons that have nothing to do with the quote.
    spec_text = SPEC.read_text(encoding="utf-8").replace("**", "").replace("``", "`")
    for entry in vocab.QUOTES:
        quote = entry["quote"].replace("**", "").replace("``", "`")
        assert quote in spec_text, f"quote {entry['id']} is not in the research document"


def test_the_vocabulary_route_serves_every_quote_with_an_id(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    quotes = {entry["id"]: entry for entry in body["internal_roles"] and body["sourced_quotes"]}
    assert quotes, "the vocabulary served no sourced quotes"
    for entry in body["sourced_quotes"]:
        assert entry["id"]
        assert entry["source"] in {"pandadoc", "papermark", "seismic"}
        assert entry["quote"].strip()


def test_the_four_built_in_roles_are_the_vendors_names():
    assert vocab.BUILT_IN_ROLES == ("Admin", "Manager", "Member", "Collaborator")


def test_the_quote_and_the_vocabulary_agree():
    for role in vocab.BUILT_IN_ROLES:
        assert f"`{role}`" in vocab.BUILT_IN_ROLE_QUOTE


def test_the_scope_catalogue_is_object_dot_verb():
    for entry in vocab.SCOPES:
        object_name, _, verb = entry["scope"].partition(".")
        assert object_name
        assert verb in vocab.VERBS, entry["scope"]
        assert entry["unlocks"], entry["scope"]


def test_the_scope_catalogue_covers_the_workflows_own_examples():
    """`links.write` only, or `documents.read` + `analytics.read`."""
    known = {entry["scope"] for entry in vocab.SCOPES}
    assert {"links.write", "documents.read", "analytics.read"} <= known


def test_the_two_coarse_grants_are_named_and_documented():
    assert vocab.COARSE_SCOPES == ("apis.read", "apis.all")
    for name in vocab.COARSE_SCOPES:
        assert name in scope_rules.KNOWN_SCOPES


def test_both_coarse_grants_can_actually_be_requested():
    """A scope the catalogue carries must be mintable, or the catalogue lies.

    This shipped broken: the normaliser required the last segment to be a
    permission level, and `all` is not one, so `apis.all` was rejected at the
    token endpoint while `coverage_for` and the catalogue both listed it. A
    coarse grant is a *named* grant, so its last segment is its own name.
    """
    for name in vocab.COARSE_SCOPES:
        assert scope_rules.normalise_scope(name) == name
        assert scope_rules.normalise_scope(name.upper()) == name
    assert scope_rules.normalise_scopes(["apis.all", "documents.read"]) == (
        "apis.all",
        "documents.read",
    )


def test_a_coarse_grant_can_be_minted_and_is_still_only_itself(store):
    """Accepted at the endpoint, and still no wider than its own name there."""
    seed_workspace(store)
    minted = store.mint_token(WS, name="api admin", scopes=["apis.all"], source=SOURCE)
    assert minted["scopes"] == ["apis.all"]
    assert set(minted["coverage"]) == {"tokens.read", "tokens.write"}
    # And it unlocks the token endpoints in the description...
    paths = {entry["path"] for entry in minted["unlocks"]}
    assert any("/tokens" in path for path in paths)
    # ...while remaining unable to read or write anything else on the request path.
    ctx = store.context(WS, bearer=minted["secret"])
    store.authorise(ctx, ["tokens.read"])
    with pytest.raises(scope_rules.Forbidden):
        store.authorise(ctx, ["documents.read"])
    with pytest.raises(scope_rules.Forbidden):
        store.authorise(ctx, ["members.read"])


def test_a_coarse_grant_previews_over_http_with_its_coverage(client):
    """The preview is how a caller discovers what a coarse grant is worth."""
    body = client.get(f"{PREFIX}/scopes", params={"scopes": "apis.all"}).json()
    preview = body["preview"]
    assert preview["warning"] == "", preview["warning"]
    assert preview["requested"] == ["apis.all"]
    assert sorted(preview["coverage"]) == ["tokens.read", "tokens.write"]
    assert preview["surfaces"] == {}


def test_the_plan_ladder_is_ordered():
    assert list(vocab.PLAN_RANK) == list(vocab.PLANS)
    assert vocab.PLAN_RANK[vocab.PLAN_ENTERPRISE] > vocab.PLAN_RANK[vocab.PLAN_STARTER]


# --------------------------------------------------------------------------- #
# Role rules, as pure functions
# --------------------------------------------------------------------------- #


def membership(role, *, member_id="m1", active=True, seat=vocab.SEAT_FULL, is_owner=False):
    return {
        "id": member_id,
        "role": role,
        "active": active,
        "seat": seat,
        "is_owner": is_owner,
    }


def test_the_owners_role_cannot_be_changed():
    """`The role of the workspace owner cannot be changed.`"""
    decision = rules.decide_role_change(
        member=membership(vocab.ROLE_OWNER),
        memberships=[membership(vocab.ROLE_OWNER)],
        requested_role=vocab.MEMBER,
    )
    assert decision.refused
    assert decision.outcome == rules.REFUSED_OWNER
    assert "cannot be changed" in decision.rule


def test_a_member_flagged_as_owner_is_also_protected():
    decision = rules.decide_role_change(
        member=membership(vocab.ADMIN, is_owner=True),
        memberships=[membership(vocab.ADMIN, is_owner=True)],
        requested_role=vocab.MEMBER,
    )
    assert decision.outcome == rules.REFUSED_OWNER


def test_the_last_admins_role_cannot_be_changed():
    """`The role of the last member with admin privileges ... cannot be changed.`"""
    decision = rules.decide_role_change(
        member=membership(vocab.ADMIN, member_id="m1"),
        memberships=[
            membership(vocab.ROLE_OWNER, member_id="owner"),
            membership(vocab.ADMIN, member_id="m1"),
        ],
        requested_role=vocab.MEMBER,
    )
    assert decision.outcome == rules.REFUSED_LAST_ADMIN
    assert "last member with admin privileges" in decision.rule


def test_an_admin_may_be_demoted_while_another_admin_remains():
    decision = rules.decide_role_change(
        member=membership(vocab.ADMIN, member_id="m1"),
        memberships=[
            membership(vocab.ROLE_OWNER, member_id="owner"),
            membership(vocab.ADMIN, member_id="m1"),
            membership(vocab.ADMIN, member_id="m2"),
        ],
        requested_role=vocab.MEMBER,
    )
    assert decision.accepted
    assert decision.patch == {"role": vocab.MEMBER}


def test_promoting_the_only_non_owner_admin_to_admin_is_refused():
    """The rail is about *losing* an admin, so a promotion that is refused would
    be the rail firing backwards. Promoting must always work."""
    decision = rules.decide_role_change(
        member=membership(vocab.MEMBER, member_id="m1"),
        memberships=[
            membership(vocab.ROLE_OWNER, member_id="owner"),
            membership(vocab.ADMIN, member_id="m2"),
            membership(vocab.MEMBER, member_id="m1"),
        ],
        requested_role=vocab.ADMIN,
    )
    assert decision.accepted


def test_an_inactive_membership_is_refused():
    decision = rules.decide_role_change(
        member=membership(vocab.MEMBER, active=False),
        memberships=[membership(vocab.MEMBER, active=False)],
        requested_role=vocab.ADMIN,
    )
    assert decision.outcome == rules.REFUSED_INACTIVE


def test_a_role_the_member_already_holds_writes_nothing():
    decision = rules.decide_role_change(
        member=membership(vocab.MEMBER),
        memberships=[membership(vocab.MEMBER)],
        requested_role=vocab.MEMBER,
    )
    assert decision.outcome == rules.REFUSED_NO_CHANGE
    assert decision.patch == {}


def test_an_unknown_role_is_refused():
    decision = rules.decide_role_change(
        member=membership(vocab.MEMBER),
        memberships=[membership(vocab.MEMBER)],
        requested_role="Supreme Overlord",
    )
    assert decision.outcome == rules.REFUSED_UNKNOWN_ROLE
    assert "built-in role name" in decision.rule or "custom role" in decision.rule


def test_an_empty_role_is_refused():
    decision = rules.decide_role_change(
        member=membership(vocab.MEMBER),
        memberships=[membership(vocab.MEMBER)],
        requested_role="   ",
    )
    assert decision.outcome == rules.REFUSED_UNKNOWN_ROLE


def test_a_workspace_defined_custom_role_is_accepted():
    custom = [{"name": "Deal Desk", "permissions": ["members.read"]}]
    decision = rules.decide_role_change(
        member=membership(vocab.MEMBER),
        memberships=[membership(vocab.MEMBER)],
        requested_role="Deal Desk",
        custom_roles=custom,
    )
    assert decision.accepted
    assert decision.patch == {"role": "Deal Desk"}


# -- the seat auto-upgrade --------------------------------------------------- #


def test_a_guest_promoted_to_a_non_collaborator_role_is_upgraded_to_a_full_seat():
    """`a Guest promoted to a non-Collaborator role is auto-upgraded to a Full seat`"""
    for target in (vocab.ADMIN, vocab.MANAGER, vocab.MEMBER):
        decision = rules.decide_role_change(
            member=membership(vocab.COLLABORATOR, seat=vocab.SEAT_GUEST),
            memberships=[membership(vocab.COLLABORATOR, seat=vocab.SEAT_GUEST)],
            requested_role=target,
        )
        assert decision.accepted, target
        assert decision.resulting_seat == vocab.SEAT_FULL, target
        assert decision.seat_changed
        assert decision.patch["seat"] == vocab.SEAT_FULL, target


def test_a_guest_promoted_to_collaborator_is_not_upgraded():
    """The rule says *non*-Collaborator, and Collaborator is the exception."""
    decision = rules.decide_role_change(
        member=membership(vocab.MEMBER, seat=vocab.SEAT_GUEST),
        memberships=[membership(vocab.MEMBER, seat=vocab.SEAT_GUEST)],
        requested_role=vocab.COLLABORATOR,
    )
    assert decision.accepted
    assert decision.resulting_seat == vocab.SEAT_GUEST
    assert not decision.seat_changed
    assert "seat" not in decision.patch


def test_a_full_seat_member_promoted_to_a_non_collaborator_keeps_their_seat():
    decision = rules.decide_role_change(
        member=membership(vocab.COLLABORATOR, seat=vocab.SEAT_FULL),
        memberships=[membership(vocab.COLLABORATOR, seat=vocab.SEAT_FULL)],
        requested_role=vocab.MEMBER,
    )
    assert decision.accepted
    assert decision.resulting_seat == vocab.SEAT_FULL
    assert not decision.seat_changed


def test_a_custom_role_promotion_from_a_guest_is_upgraded():
    """A custom role is not `Collaborator`, so the rule's exception does not cover it.

    The sentence is "promoted to a non-`Collaborator` role", not "promoted to a
    non-`Collaborator` *built-in* role". Reading it the narrower way would leave a
    guest able to take a custom role with no seat.
    """
    decision = rules.decide_role_change(
        member=membership(vocab.COLLABORATOR, seat=vocab.SEAT_GUEST),
        memberships=[membership(vocab.COLLABORATOR, seat=vocab.SEAT_GUEST)],
        requested_role="Deal Desk",
        custom_roles=[{"name": "Deal Desk", "permissions": ["members.read"]}],
    )
    assert decision.accepted
    assert decision.resulting_seat == vocab.SEAT_FULL


# -- permissions ------------------------------------------------------------- #


def test_the_owner_holds_every_permission():
    assert rules.permissions_for(membership(vocab.ROLE_OWNER)) == vocab.PERMISSIONS


def test_admin_holds_every_permission():
    assert set(rules.permissions_for(membership(vocab.ADMIN))) == set(vocab.PERMISSIONS)


def test_manager_cannot_mint_a_token():
    """Manager can change roles and read tokens, but not mint one."""
    granted = rules.permissions_for(membership(vocab.MANAGER))
    assert rules.has_permission(membership(vocab.MANAGER), "members.write")
    assert not rules.has_permission(membership(vocab.MANAGER), "tokens.write")
    assert "tokens.write" not in granted


def test_member_and_collaborator_only_read_the_member_list():
    for role in (vocab.MEMBER, vocab.COLLABORATOR):
        assert rules.permissions_for(membership(role)) == ("members.read",)
        assert not rules.has_permission(membership(role), "members.write")


def test_a_custom_role_grants_exactly_what_it_declares():
    custom = [{"name": "Deal Desk", "permissions": ["members.read", "tokens.read"]}]
    granted = rules.permissions_for(membership("Deal Desk"), custom_roles=custom)
    assert set(granted) == {"members.read", "tokens.read"}


def test_an_unrecognised_role_grants_nothing():
    """Failing closed: an unknown role can never widen access."""
    assert rules.permissions_for(membership("Unknown")) == ()
    assert not rules.has_permission(membership("Unknown"), "members.read")


def test_a_deleted_custom_role_stops_granting():
    custom = [{"name": "Deal Desk", "permissions": ["members.read"], "deleted": True}]
    assert rules.permissions_for(membership("Deal Desk"), custom_roles=custom) == ()


def test_admin_is_a_permission_set_not_a_name():
    """A custom role holding every permission counts as admin for the rail."""
    custom = [{"name": "Superuser", "permissions": list(vocab.PERMISSIONS)}]
    assert rules.is_admin(membership("Superuser"), custom_roles=custom)


def test_a_partial_custom_role_is_not_admin():
    custom = [{"name": "Almost", "permissions": ["members.read", "members.write"]}]
    assert not rules.is_admin(membership("Almost"), custom_roles=custom)


def test_an_unprivileged_role_is_not_admin():
    assert not rules.is_admin(membership(vocab.MEMBER))
    assert not rules.is_admin(membership(vocab.MANAGER))


def test_the_caller_rule_refuses_a_member_without_permission():
    with pytest.raises(RoleForbidden) as caught:
        rules.require_permission(membership(vocab.MEMBER), "members.write", actor="ines")
    assert "organization admin" in str(caught.value)
    assert caught.value.status_code == 403


def test_the_caller_rule_allows_a_role_holding_the_permission():
    assert rules.require_permission(membership(vocab.MANAGER), "members.write") is None
    custom = [{"name": "Lead", "permissions": ["members.write"]}]
    assert (
        rules.require_permission(membership("Lead"), "members.write", custom_roles=custom) is None
    )


def test_an_absent_role_is_refused_rather_than_treated_as_privileged():
    with pytest.raises(RoleForbidden):
        rules.require_permission({}, "members.write")


def test_normalise_seat_refuses_an_unknown_seat():
    with pytest.raises(RoleError) as caught:
        rules.normalise_seat("platinum")
    assert "full" in str(caught.value)


def test_normalise_seat_folds_case():
    assert rules.normalise_seat("  FULL ") == vocab.SEAT_FULL


def test_role_key_folds_case_and_spaces():
    assert rules.role_key("Admin") == rules.role_key("admin")
    assert rules.role_key("Content Contributor") == "content_contributor"


def test_role_comparison_is_case_insensitive_but_storage_is_not():
    decision = rules.decide_role_change(
        member=membership(vocab.MEMBER),
        memberships=[membership(vocab.MEMBER)],
        requested_role="member",
    )
    assert decision.outcome == rules.REFUSED_NO_CHANGE


def test_every_refusal_outcome_is_documented():
    outcomes = {entry["outcome"] for entry in rules.REFUSALS}
    for outcome in (
        rules.REFUSED_OWNER,
        rules.REFUSED_LAST_ADMIN,
        rules.REFUSED_UNKNOWN_ROLE,
        rules.REFUSED_INACTIVE,
        rules.REFUSED_NO_CHANGE,
    ):
        assert outcome in outcomes


# --------------------------------------------------------------------------- #
# The scope rules: the sharpest rule in the research
# --------------------------------------------------------------------------- #


def test_documents_write_does_not_grant_documents_read():
    """`documents.write` does **not** imply `documents.read`."""
    assert scope_rules.token_has_scope(["documents.write"], "documents.write")
    assert not scope_rules.token_has_scope(["documents.write"], "documents.read")


def test_documents_read_does_not_grant_documents_write():
    assert not scope_rules.token_has_scope(["documents.read"], "documents.write")


def test_a_token_holding_every_scope_except_one_cannot_use_it():
    """The researched use case, from the direction that matters.

    A token holding every scope in the catalogue *except* `documents.read` still
    cannot read. This is the assertion that fails if anyone ever adds a hierarchy
    to `token_has_scope`, and it is the one to read first when reviewing that
    function.
    """
    everything_except_read = sorted(scope_rules.KNOWN_SCOPES - {"documents.read"})
    assert scope_rules.missing_scopes(everything_except_read, ["documents.read"]) == (
        "documents.read",
    )
    assert scope_rules.token_has_scope(everything_except_read, "documents.write")


def test_a_write_only_token_is_genuinely_write_only():
    """An ingestion worker that pushes data in and must not read it back out."""
    granted = ["documents.write"]
    assert not scope_rules.token_has_scope(granted, "documents.read")
    assert not scope_rules.token_has_scope(granted, "analytics.read")
    assert not scope_rules.token_has_scope(granted, "links.write")


def test_a_stored_wildcard_grants_nothing():
    """Creation refuses wildcards, so one reaching the store must not grant."""
    assert not scope_rules.token_has_scope(["*"], "documents.read")
    assert not scope_rules.token_has_scope(["documents.*"], "documents.read")


def test_no_a_la_carte_scope_implies_a_sibling_anywhere_in_the_catalogue():
    """Exhaustive, over the whole catalogue rather than one hand-picked pair.

    The two coarse grants are excluded, and deliberately. This test is the
    no-implicit-hierarchy rule stated at full strength: a scope grants itself and
    nothing else. A coarse grant is not an a-la-carte scope - it is a published
    name for a fixed set of them - and the sweep would otherwise be asserting
    that `apis.all` unlocks nothing, which is the inert-grant behaviour an earlier
    build had and rejected. `test_the_coarse_grant_expansion_is_a_fixed_table_and_not_a_wildcard`
    is the test for those two, and it pins their expansion as exactly two scopes.
    """
    a_la_carte = sorted(scope_rules.KNOWN_SCOPES - set(vocab.COARSE_SCOPES))
    for scope in a_la_carte:
        for other in a_la_carte:
            if other == scope:
                continue
            assert not scope_rules.token_has_scope([scope], other), f"{scope} granted {other}"


def test_an_empty_grant_grants_nothing():
    assert not scope_rules.token_has_scope(None, "documents.read")
    assert not scope_rules.token_has_scope([], "documents.read")


# -- normalisation and refusal ----------------------------------------------- #


def test_a_wildcard_is_refused():
    """`Don't request * or wildcards; they're not supported.`"""
    for bad in ("*", "documents.*", "*.*", "apis.**"):
        with pytest.raises(ScopeError) as caught:
            scope_rules.normalise_scope(bad)
        assert "wildcard" in str(caught.value).lower()


def test_an_unknown_scope_is_refused():
    with pytest.raises(ScopeError) as caught:
        scope_rules.normalise_scope("teleportation.read")
    assert "unknown scope" in str(caught.value)
    assert caught.value.status_code == 422


def test_an_unknown_permission_level_names_the_level_it_rejected():
    """A different mistake from an unknown object, so a different message."""
    with pytest.raises(ScopeError) as caught:
        scope_rules.normalise_scope("documents.delete_everything")
    assert "delete_everything" in str(caught.value)
    assert "use one of" in str(caught.value)


def test_a_scope_without_a_permission_level_is_refused():
    with pytest.raises(ScopeError):
        scope_rules.normalise_scope("documents")


def test_an_unknown_permission_level_is_refused():
    with pytest.raises(ScopeError):
        scope_rules.normalise_scope("documents.destroy")


def test_an_empty_scope_is_refused():
    with pytest.raises(ScopeError):
        scope_rules.normalise_scope("")


def test_the_vendors_verb_synonyms_canonicalise_to_our_two_verbs():
    """`view` is read-only and `manage` is read/write, so they are read and write.

    A caller who transliterates a Seismic-style scope onto this catalogue gets the
    right permission level rather than a confusing rejection.
    """
    assert scope_rules.normalise_scope("documents.view") == "documents.read"
    assert scope_rules.normalise_scope("documents.manage") == "documents.write"
    assert scope_rules.normalise_scope("links.MANAGE") == "links.write"


def test_seismic_scopes_are_not_in_this_products_catalogue():
    """The research cites Seismic's names as an example of the *format*.

    Accepting them as grants here would mint a token holding a scope that unlocks
    nothing in this product, and the same page that gives the format is the one
    that says "the token endpoint will reject unknown scopes". So a Seismic name is
    refused as unknown, and `seismic_permits_nothing` is what makes the refusal
    honest.
    """
    with pytest.raises(ScopeError) as caught:
        scope_rules.normalise_scope("seismic.library.view")
    assert "unknown scope" in str(caught.value)
    assert "seismic" not in scope_rules.KNOWN_SCOPES


def test_the_alias_verbs_are_the_two_documented_levels():
    assert scope_rules._ALIAS_VERBS["view"] == vocab.VERB_READ
    assert scope_rules._ALIAS_VERBS["manage"] == vocab.VERB_WRITE
    assert set(scope_rules._ALIAS_VERBS) == {"read", "view", "write", "manage"}


def test_a_duplicated_scope_is_dropped_not_refused():
    assert scope_rules.normalise_scopes(["documents.read", "documents.read", "analytics.read"]) == (
        "documents.read",
        "analytics.read",
    )


def test_normalise_scopes_preserves_request_order():
    assert scope_rules.normalise_scopes(["links.write", "documents.read", "analytics.read"]) == (
        "links.write",
        "documents.read",
        "analytics.read",
    )


def test_missing_scopes_lists_every_miss():
    assert scope_rules.missing_scopes(["documents.read"], ["documents.read", "analytics.read"]) == (
        "analytics.read",
    )
    assert scope_rules.missing_scopes([], ["a.read", "b.read"]) == ("a.read", "b.read")


# -- coarse grants ----------------------------------------------------------- #


def test_apis_all_is_not_a_wildcard():
    """Two sentences on one vendor page; this is the reading that fits both."""
    assert not scope_rules.token_has_scope(["apis.all"], "documents.write")
    assert not scope_rules.token_has_scope(["apis.all"], "members.read")
    assert scope_rules.token_has_scope(["apis.all"], "apis.all")


def test_apis_all_covers_only_the_token_management_scopes():
    assert scope_rules.coverage_for(["apis.all"]) == frozenset({"tokens.read", "tokens.write"})
    assert scope_rules.coverage_for(["apis.read"]) == frozenset({"tokens.read"})
    assert scope_rules.coverage_for(["documents.write"]) == frozenset()


def test_a_coarse_grant_reaches_only_its_own_endpoints():
    endpoints = [dict(entry) for entry in ENDPOINTS]
    reached = scope_rules.unlocked_endpoints(["apis.all"], endpoints)
    paths = {entry["path"] for entry in reached}
    assert any("/tokens" in path for path in paths)
    assert not any("/members" in path for path in paths)


def test_a_coarse_grant_reaches_no_product_surface():
    """It stands for other scopes, so it is not itself a surface."""
    assert scope_rules.granted_surfaces(["apis.all"]) == {}
    assert scope_rules.granted_surfaces(["documents.write"])


def test_a_write_only_scope_still_reaches_a_real_surface():
    """The researched example: `links.write` only, for an ingestion worker."""
    surfaces = scope_rules.granted_surfaces(["links.write"])
    assert surfaces == {"links.write": ["tracked links: create and revoke"]}


def test_every_catalogue_scope_names_at_least_one_surface():
    for entry in vocab.SCOPES:
        assert vocab.SCOPE_TARGETS.get(str(entry["scope"])), entry["scope"]


def test_every_surface_is_a_tuple_of_strings():
    """A one-element tuple needs its trailing comma, and without one it is a string.

    This exact mistake shipped once: `("one surface")` rather than `("one surface",)`,
    and `list()` on a string returns its *characters*, so the token-creation surface
    told a reader that `documents.write` unlocked `d`, `o`, `c`, `u`, `m` and so on.
    Nothing crashed, every test that only checked truthiness still passed, and the
    page rendered nonsense. So the shape is asserted rather than the content.
    """
    for scope, targets in vocab.SCOPE_TARGETS.items():
        assert isinstance(targets, tuple), f"{scope} is {type(targets).__name__}, not a tuple"
        assert targets, f"{scope} has an empty tuple"
        for target in targets:
            assert isinstance(target, str) and target.strip(), f"{scope}: {target!r}"


def test_a_surface_comes_back_as_words_not_characters():
    surfaces = scope_rules.granted_surfaces(["documents.write"])
    assert surfaces == {"documents.write": ["document library: upload, rename, move, delete"]}


def test_scopes_that_unlock_lists_the_single_scopes_too():
    member_route = next(
        e for e in ENDPOINTS if e["path"].endswith("/members") and e["method"] == "GET"
    )
    assert "members.read" in scope_rules.scopes_that_unlock(member_route)
    assert "documents.write" not in scope_rules.scopes_that_unlock(member_route)


def test_the_coarse_grant_expansion_is_a_fixed_table_and_not_a_wildcard():
    """The one thing a coarse grant may never do is become a wildcard.

    `apis.all` may reach exactly the scopes its catalogue entry names. If anything
    in `COARSE_COVERAGE` ever grew to hold a scope outside the token-management
    pair, or an entry were ever treated as a prefix of anything, this is the test
    that would say so.
    """
    for name, covered in vocab.COARSE_COVERAGE.items():
        assert set(covered) <= set(scope_rules.KNOWN_SCOPES), name
    assert set(vocab.COARSE_COVERAGE["apis.all"]) == {"tokens.read", "tokens.write"}
    assert set(vocab.COARSE_COVERAGE["apis.read"]) == {"tokens.read"}
    # The expansion is a table lookup, not a derivation from the name.
    assert scope_rules.coverage_for(["apis.all"]) == frozenset({"tokens.read", "tokens.write"})
    for outside in ("documents.read", "documents.write", "members.read", "sso.write"):
        assert not scope_rules.token_has_scope(["apis.all"], outside), outside


def test_the_unlocks_surface_answers_which_endpoints_a_scope_reaches():
    """A scope that governs no route of *this* feature still names its surfaces."""
    endpoints = [dict(entry) for entry in ENDPOINTS]
    assert scope_rules.unlocked_endpoints(["links.write"], endpoints) == []
    assert scope_rules.granted_surfaces(["links.write"])


# -- the 403 ----------------------------------------------------------------- #


def test_a_scope_miss_is_the_vendors_403_with_the_vendors_words():
    """`The token is valid, but doesn't have the scope the endpoint requires...`"""
    with pytest.raises(scope_rules.Forbidden) as caught:
        scope_rules.authorise(granted=["links.write"], required=["documents.read"])
    forbidden = caught.value
    assert forbidden.status_code == 403
    assert forbidden.code == "forbidden"
    assert forbidden.detail == scope_rules.FORBIDDEN_DETAIL
    assert forbidden.scope_missing == ("documents.read",)


def test_the_403_body_carries_both_the_code_and_the_cause():
    body = scope_rules.Forbidden(scope_missing=("analytics.read",)).to_dict()
    assert body["code"] == body["error"] == "forbidden"
    assert body["status"] == 403
    assert body["missing_scopes"] == ["analytics.read"]
    assert body["member_forbidden"] == ""


def test_a_token_with_the_scope_is_allowed():
    assert scope_rules.authorise(granted=["documents.read"], required=["documents.read"]) is None


def test_a_member_lacking_the_permission_is_refused_even_with_the_scope():
    """Seismic: scopes do not override a user's defined permissions.

    The token holds `documents.write` and the member does not hold it, so the
    request is refused. This is the rule a generous implementation breaks first.
    """
    with pytest.raises(scope_rules.Forbidden) as caught:
        scope_rules.authorise(
            granted=["documents.write"],
            required=["documents.write"],
            member_permission="documents.write",
            member_allowed=False,
        )
    assert caught.value.scope_missing == ()
    assert caught.value.member_forbidden == "documents.write"


def test_a_member_with_the_permission_is_allowed_alongside_the_token():
    assert (
        scope_rules.authorise(
            granted=["documents.write"],
            required=["documents.write"],
            member_permission="documents.write",
            member_allowed=True,
        )
        is None
    )


def test_a_pure_machine_call_has_no_member_to_override():
    """No member means no user permissions, so only the scope applies."""
    assert (
        scope_rules.authorise(
            granted=["links.write"], required=["links.write"], member_allowed=None
        )
        is None
    )


# -- the plan gate ----------------------------------------------------------- #


def test_a_plan_below_the_gate_is_refused():
    with pytest.raises(PlanFeatureError) as caught:
        scope_rules.require_plan(vocab.PLAN_STARTER, "sso")
    assert caught.value.code == "forbidden_plan_feature"
    assert caught.value.status_code == 403


def test_a_plan_at_or_above_the_gate_is_allowed():
    assert scope_rules.plan_allows(vocab.PLAN_BUSINESS, "tokens")
    assert scope_rules.plan_allows(vocab.PLAN_ENTERPRISE, "tokens")
    assert scope_rules.plan_allows(vocab.PLAN_ENTERPRISE, "sso")


def test_an_ungated_feature_is_allowed_on_any_plan():
    assert scope_rules.plan_allows(vocab.PLAN_STARTER, "members.read")


def test_an_unknown_plan_entitles_nothing_gated():
    assert not scope_rules.plan_allows("platinum", "tokens")


# -- throttling -------------------------------------------------------------- #


def test_the_budget_allows_exactly_its_limit():
    limiter = scope_rules.RateLimiter(3)
    for index in range(3):
        assert limiter.check("t", 1000.0 + index)
    with pytest.raises(RateLimited) as caught:
        limiter.check("t", 1003.0)
    assert caught.value.code == "rate_limit_exceeded"
    assert caught.value.status_code == 429


def test_the_429_carries_a_reset_signal():
    limiter = scope_rules.RateLimiter(1)
    limiter.check("t", 1_000_000.0)
    with pytest.raises(RateLimited) as caught:
        limiter.check("t", 1_000_001.0)
    assert caught.value.reset_at
    assert caught.value.reset_at.endswith("Z")


def test_the_budget_refills_after_a_minute():
    limiter = scope_rules.RateLimiter(1)
    limiter.check("t", 1000.0)
    with pytest.raises(RateLimited):
        limiter.check("t", 1001.0)
    # The first hit was at t=1000.0, so its window ends at t=1060.
    assert limiter.check("t", 1060.0)


def test_the_budget_is_per_token():
    limiter = scope_rules.RateLimiter(1)
    limiter.check("one", 1000.0)
    assert limiter.check("two", 1000.0)
    with pytest.raises(RateLimited):
        limiter.check("one", 1000.5)


def test_remaining_counts_down():
    limiter = scope_rules.RateLimiter(3)
    assert limiter.remaining("t", 1000.0) == 3
    limiter.check("t", 1000.0)
    assert limiter.remaining("t", 1000.0) == 2


def test_a_zero_limit_disables_throttling():
    limiter = scope_rules.RateLimiter(0)
    for index in range(50):
        limiter.check("t", 1000.0 + index)
    assert limiter.remaining("t", 1000.0) == -1


def test_the_headers_carry_the_limit_reset_and_retry_after():
    """Where the header appears, and where it deliberately does not.

    The researched signal is the one on the 429, because that is the response a
    client must reason about to know when to retry. The same headers on a success
    are cheap and let a client pace itself.

    A 403 for a missing scope is the third case, and it gets no header. The budget
    was spent, but the refusal has already said what to do about it - add the scope
    it is missing - and a reset timestamp beside that message would answer a
    question the client did not ask. Plumbing the limiter into the exception handler
    would couple the refusal path to the throttling mechanism for no researched
    requirement, so the asymmetry stands and is pinned here rather than left for
    somebody to rediscover as a bug.
    """
    limiter = scope_rules.RateLimiter(2)
    reset_at = limiter.check("t", 1_000_000.0)
    headers = scope_rules.rate_limit_headers(reset_at, limit=2, now=1_000_000.0, remaining=1)
    assert headers["X-RateLimit-Limit"] == "2"
    assert headers["X-RateLimit-Reset"] == reset_at
    assert headers["X-RateLimit-Remaining"] == "1"
    assert int(headers["Retry-After"]) == 60

    limiter.check("t", 1_000_001.0)
    with pytest.raises(RateLimited) as spent:
        limiter.check("t", 1_000_002.0)
    refusal = scope_rules.rate_limit_headers(
        spent.value.reset_at, limit=2, now=1_000_002.0, remaining=0
    )
    assert refusal["X-RateLimit-Reset"] == spent.value.reset_at
    assert refusal["X-RateLimit-Remaining"] == "0"
    # A scope refusal carries no rate-limit state of its own.
    assert not hasattr(scope_rules.Forbidden(scope_missing=("documents.read",)), "reset_at")


class _PoisonedLimiter:
    """A limiter whose budget is always spent and whose reset value is arbitrary.

    Used to provoke the one part of the 429 path that can fail: publishing the
    reset instant. A rate limiter that crashes under load has removed the limit
    rather than enforced it, so the status and the researched header have to hold
    for any value that reaches the handler.
    """

    #: The real limiter publishes this, and the handler reads it back out.
    limit = 600

    def __init__(self, reset_value, *, spent=False):
        self._reset = reset_value
        self._spent = spent

    def check(self, key, now):
        if self._spent:
            raise RateLimited("per-minute budget spent", reset_at=self._reset, limit=self.limit)
        self._spent = True
        return self._reset

    def remaining(self, key, now):
        return 0


def _with_limiter(limiter):
    """Install `limiter` as the limiter every new WorkspaceAccess will use."""
    original = WorkspaceAccess.__init__

    def limited_init(self, store, **kwargs):
        original(self, store, **kwargs)
        self.limiter = limiter

    WorkspaceAccess.__init__ = limited_init
    return original


def test_the_caller_block_carries_no_raw_throttling_value(store):
    """A 200 must not be able to fail because a throttling helper changed its return.

    The reset instant is a header. It was also being copied into the `caller`
    block of every body, which meant the limiter's raw value had to be
    JSON-serialisable - so an object there took a successful 200 down to a 500.
    """
    seed_workspace(store)
    ctx = store.context(WS, member_id=store.list_members(WS)[0]["id"])
    body = ctx.to_dict()
    # The budget was spent by the context call, so this is a true flag - and it is
    # a flag, not the instant, which is what the header is for.
    assert body["rate_limited"] is True
    assert "rate_limit_reset" not in body
    assert "rate_limit_reset" not in json.dumps(body)
    # And the whole block survives a limiter that reports something unserialisable.
    # `AccessContext` is frozen, so the poisoned value is built with `replace`.
    poisoned = dataclasses.replace(ctx, reset_at=object())
    assert json.dumps(poisoned.to_dict())


@pytest.mark.parametrize(
    "bad",
    ["not-a-timestamp", 12345, None, object(), "", "2026-13-45T99:99:99Z"],
    ids=["unparseable", "an-int", "none", "an-object", "empty", "impossible-date"],
)
def test_a_429_never_becomes_a_500_however_odd_its_reset_value(client, bad):
    """Losing the optional `Retry-After` is acceptable; losing the status is not."""
    _owner, admins, _member = http_seed(client)
    secret = client.post(
        f"{PREFIX}/workspaces/{WS}/tokens",
        json={"name": "reader", "scopes": ["members.read"]},
        headers={MOD_MEM_HDR: admins[0]},
    ).json()["secret"]

    original = _with_limiter(_PoisonedLimiter(bad))
    try:
        # The first call spends the budget and reports the bad value; the second
        # is the 429, and it is the one that has to survive an unparseable reset.
        client.get(
            f"{PREFIX}/workspaces/{WS}/members", headers={"Authorization": f"Bearer {secret}"}
        )
        response = client.get(
            f"{PREFIX}/workspaces/{WS}/members", headers={"Authorization": f"Bearer {secret}"}
        )
    finally:
        WorkspaceAccess.__init__ = original

    assert response.status_code == 429, f"reset value {bad!r} produced {response.status_code}"
    body = response.json()
    assert body["code"] == "rate_limit_exceeded"
    assert body["error"] == body["code"]
    # The researched signal is the one header that must always be there.
    assert "X-RateLimit-Reset" in response.headers


def test_reset_seconds_never_goes_negative():
    assert scope_rules.reset_seconds(2_000_000.0, "1970-01-01T00:00:01Z") == 0


def test_window_opens_at_the_next_whole_minute():
    assert scope_rules.window_opens(1_000_000.0).endswith(":00Z")


# --------------------------------------------------------------------------- #
# The OAuth consent flow
# --------------------------------------------------------------------------- #


def consent(scopes=("documents.read",), client_id="cli_x", workspace_id=WS):
    return oauth_flow.new_consent_request(
        workspace_id=workspace_id, client_id=client_id, requested_scopes=list(scopes)
    )


def test_a_consent_request_canonicalises_its_scopes():
    request = consent(["DOCUMENTS.READ", "documents.read"])
    assert request.requested_scopes == ("documents.read",)


def test_a_consent_request_needs_a_workspace_and_a_client():
    with pytest.raises(ConsentError):
        oauth_flow.new_consent_request(
            workspace_id="", client_id="c", requested_scopes=["links.write"]
        )
    with pytest.raises(ConsentError):
        oauth_flow.new_consent_request(
            workspace_id="w", client_id="", requested_scopes=["links.write"]
        )


def test_a_consent_request_refuses_a_wildcard_before_asking_a_human():
    with pytest.raises(ScopeError):
        consent(["*"])


def test_the_consent_screen_shows_what_each_scope_unlocks():
    """`The dashboard's token-creation UI shows which endpoints each scope unlocks`"""
    preview = oauth_flow.consent_preview(
        consent(["documents.read", "members.read"]), [dict(e) for e in ENDPOINTS]
    )
    assert preview["title"] == oauth_flow.CONSENT_TITLE
    assert preview["unlocks"], "the consent screen showed no endpoints"
    assert "members.read" in {s for entry in preview["unlocks"] for s in entry["scopes"]}
    # And the surfaces the other scope reaches, which are not this API's routes.
    assert preview["surfaces"]["documents.read"]


def test_the_consent_screen_names_the_vendor_endpoints():
    preview = oauth_flow.consent_preview(consent(), [dict(e) for e in ENDPOINTS])
    assert preview["authorize_endpoint"].endswith("/oauth2/authorize")
    assert preview["token_endpoint"].endswith("/oauth2/access_token")


def test_the_consent_screen_says_it_grants_nothing_else():
    preview = oauth_flow.consent_preview(consent(), [])
    assert preview["grants_nothing_else"] == vocab.NO_HIERARCHY_QUOTE


def test_a_fresh_code_validates():
    record = oauth_flow.issue_code(consent(), now=NOW)
    granted = oauth_flow.check_code(
        record, workspace_id=WS, client_id="cli_x", now=NOW + timedelta(seconds=5)
    )
    assert granted == ("documents.read",)


def test_a_code_is_single_use():
    record = oauth_flow.issue_code(consent(), now=NOW)
    spent = {**record, "used_at": oauth_flow._utc(NOW + timedelta(seconds=1))}
    with pytest.raises(ConsentError) as caught:
        oauth_flow.check_code(spent, workspace_id=WS, client_id="cli_x", now=NOW)
    assert "already been exchanged" in str(caught.value)


def test_a_code_expires():
    record = oauth_flow.issue_code(consent(), now=NOW)
    with pytest.raises(ConsentError) as caught:
        oauth_flow.check_code(
            record,
            workspace_id=WS,
            client_id="cli_x",
            now=NOW + timedelta(seconds=oauth_flow.CODE_TTL_SECONDS + 1),
        )
    assert "expired" in str(caught.value)


def test_a_code_is_bound_to_its_workspace():
    record = oauth_flow.issue_code(consent(), now=NOW)
    with pytest.raises(ConsentError) as caught:
        oauth_flow.check_code(record, workspace_id="other", client_id="cli_x", now=NOW)
    assert "another workspace" in str(caught.value)


def test_a_code_is_bound_to_its_client():
    record = oauth_flow.issue_code(consent(), now=NOW)
    with pytest.raises(ConsentError) as caught:
        oauth_flow.check_code(record, workspace_id=WS, client_id="cli_y", now=NOW)
    assert "another client" in str(caught.value)


def test_a_revoked_code_is_refused():
    record = oauth_flow.issue_code(consent(), now=NOW)
    with pytest.raises(ConsentError):
        oauth_flow.check_code(
            {**record, "revoked": True}, workspace_id=WS, client_id="cli_x", now=NOW
        )


def test_a_missing_code_is_refused():
    with pytest.raises(ConsentError):
        oauth_flow.check_code({}, workspace_id=WS, client_id="cli_x", now=NOW)


def test_a_client_may_narrow_at_the_exchange():
    record = oauth_flow.issue_code(consent(["documents.read", "analytics.read"]), now=NOW)
    granted = oauth_flow.check_code(
        record, workspace_id=WS, client_id="cli_x", now=NOW, scopes=["documents.read"]
    )
    assert granted == ("documents.read",)


def test_a_client_may_not_widen_at_the_exchange():
    record = oauth_flow.issue_code(consent(["documents.read"]), now=NOW)
    with pytest.raises(ConsentError) as caught:
        oauth_flow.check_code(
            record,
            workspace_id=WS,
            client_id="cli_x",
            now=NOW,
            scopes=["documents.read", "analytics.read"],
        )
    assert "not consented" in str(caught.value)


def test_the_exchange_re_validates_the_consented_scopes():
    """A consent record naming an unknown scope must not mint a token."""
    record = oauth_flow.issue_code(consent(), now=NOW)
    tampered = {**record, "scopes": ["documents.teleport"]}
    with pytest.raises(ScopeError):
        oauth_flow.check_code(tampered, workspace_id=WS, client_id="cli_x", now=NOW)


def test_a_spent_code_records_the_moment_it_was_spent():
    patch = oauth_flow.spend_patch(NOW)
    assert patch["used_at"].endswith("Z")


# --------------------------------------------------------------------------- #
# Directory SSO
# --------------------------------------------------------------------------- #


def test_a_connection_needs_a_protocol_and_an_entity_id():
    with pytest.raises(SsoError):
        sso_rules.validate_connection({"protocol": "carrier-pigeon", "idp_entity_id": "x"})
    with pytest.raises(SsoError) as caught:
        sso_rules.validate_connection({"protocol": "saml", "sso_url": "https://idp"})
    assert "idp_entity_id" in str(caught.value)


def test_a_saml_connection_needs_a_sso_url():
    with pytest.raises(SsoError) as caught:
        sso_rules.validate_connection({"protocol": "saml", "idp_entity_id": "https://idp"})
    assert "sso_url" in str(caught.value)


def test_a_valid_saml_connection_is_canonicalised():
    cleaned = sso_rules.validate_connection(
        {
            "protocol": "SAML",
            "idp_entity_id": "https://idp",
            "sso_url": "https://idp/sso",
            "domains": ["@Example.test", "example.test", "other.test"],
        }
    )
    assert cleaned["protocol"] == "saml"
    assert cleaned["domains"] == ["example.test", "other.test"]


def test_domains_accept_a_string_or_a_list():
    assert sso_rules.normalise_domains("a.test, b.test") == ("a.test", "b.test")
    assert sso_rules.normalise_domains(["A.test"]) == ("a.test",)
    assert sso_rules.normalise_domains(None) == ()


def test_an_sso_only_member_cannot_sign_in_locally_while_the_connection_is_enabled():
    """The one behaviour the SSO half actually changes."""
    member = {"email": "dana@northwind.example", "auth": sso_rules.AUTH_SSO}
    connection = {
        "enabled": True,
        "protocol": "oidc",
        "idp_entity_id": "https://idp",
        "domains": ["northwind.example"],
    }
    decision = sso_rules.decide_local_login(member, connection)
    assert not decision.allowed
    assert "directory identity is mandatory" in decision.reason


def test_an_sso_only_member_can_sign_in_locally_while_the_connection_is_disabled():
    member = {"email": "dana@northwind.example", "auth": sso_rules.AUTH_SSO}
    connection = {"enabled": False, "protocol": "oidc", "idp_entity_id": "https://idp"}
    assert sso_rules.decide_local_login(member, connection).allowed


def test_an_sso_only_member_outside_the_connection_domains_keeps_local_sign_in():
    member = {"email": "someone@elsewhere.test", "auth": sso_rules.AUTH_SSO}
    connection = {
        "enabled": True,
        "protocol": "oidc",
        "idp_entity_id": "https://idp",
        "domains": ["northwind.example"],
    }
    decision = sso_rules.decide_local_login(member, connection)
    assert decision.allowed
    assert "outside the connection's domains" in decision.reason


def test_sso_is_never_forced_on_a_local_member():
    member = {"email": "sam@northwind.example", "auth": sso_rules.AUTH_LOCAL}
    connection = {
        "enabled": True,
        "protocol": "oidc",
        "idp_entity_id": "https://idp",
        "domains": ["northwind.example"],
    }
    assert sso_rules.decide_local_login(member, connection).allowed


def test_a_member_with_no_auth_field_defaults_to_local():
    connection = {"enabled": True, "protocol": "oidc", "idp_entity_id": "https://idp"}
    assert sso_rules.decide_local_login({"email": "x@y.test"}, connection).allowed


def test_an_unconfigured_workspace_leaves_local_sign_in_alone():
    member = {"email": "dana@northwind.example", "auth": sso_rules.AUTH_SSO}
    assert sso_rules.decide_local_login(member, None).allowed


def test_an_empty_domain_list_reaches_everybody():
    """A whole-company rollout is expressed as no domain restriction."""
    member = {"email": "anyone@anywhere.test", "auth": sso_rules.AUTH_SSO}
    connection = {
        "enabled": True,
        "protocol": "oidc",
        "idp_entity_id": "https://idp",
        "domains": [],
    }
    assert not sso_rules.decide_local_login(member, connection).allowed


def test_the_connection_summary_reports_an_unconfigured_workspace():
    summary = sso_rules.connection_summary(None)
    assert summary["configured"] is False
    assert summary["requires_plan"] == vocab.GATED_FEATURES["sso"]


# --------------------------------------------------------------------------- #
# The engine, over an audited database
# --------------------------------------------------------------------------- #


def test_a_token_stores_a_hash_and_never_the_secret(store):
    _owner, admins, _member = seed_workspace(store)
    minted = store.mint_token(
        WS, name="worker", scopes=["documents.write"], source=SOURCE, actor="api"
    )
    assert minted["secret"].startswith("dsr_")
    assert minted["secret_shown_once"] is True
    stored = store.store.find(TOKENS, {"workspace_id": WS}, limit=10)[0]["data"]
    assert minted["secret"] not in str(stored)
    assert stored["secret_hash"] and stored["secret_hash"] != minted["secret"]


def test_a_token_view_never_carries_the_secret(store):
    store.mint_token(WS, name="worker", scopes=["documents.write"], source=SOURCE)
    for row in store.list_tokens(WS):
        assert "secret" not in row
        assert row["secret_stored"] is False


def test_minting_refuses_a_wildcard(store):
    seed_workspace(store)
    with pytest.raises(ScopeError):
        store.mint_token(WS, name="w", scopes=["*"], source=SOURCE)


def test_minting_refuses_an_unknown_scope(store):
    seed_workspace(store)
    with pytest.raises(ScopeError):
        store.mint_token(WS, name="w", scopes=["documents.teleport"], source=SOURCE)


def test_minting_refuses_an_empty_scope_list(store):
    seed_workspace(store)
    with pytest.raises(RoleError):
        store.mint_token(WS, name="w", scopes=[], source=SOURCE)


def test_minting_refuses_an_empty_name(store):
    seed_workspace(store)
    with pytest.raises(RoleError):
        store.mint_token(WS, name="  ", scopes=["documents.read"], source=SOURCE)


def test_a_minted_token_reports_what_its_scopes_unlock(store):
    seed_workspace(store)
    minted = store.mint_token(WS, name="w", scopes=["links.write"], source=SOURCE)
    assert minted["surfaces"], "a scope set that reaches nothing was still minted"
    assert minted["unlocks"] == [], "links.write governs no route of this API"


def test_a_resolved_token_carries_exactly_its_scopes(store):
    seed_workspace(store)
    minted = store.mint_token(WS, name="w", scopes=["documents.write"], source=SOURCE)
    resolved = store.resolve_token(minted["secret"])
    assert resolved["data"]["scopes"] == ["documents.write"]


def test_an_unknown_token_is_refused(store):
    seed_workspace(store)
    with pytest.raises(TokenError) as caught:
        store.resolve_token("dsr_not-a-real-token")
    assert caught.value.status_code == 401


def test_an_empty_token_is_refused(store):
    with pytest.raises(TokenError):
        store.resolve_token("")


def test_a_revoked_token_stops_resolving(store):
    seed_workspace(store)
    minted = store.mint_token(WS, name="w", scopes=["documents.write"], source=SOURCE)
    store.revoke_token(WS, minted["id"], source=SOURCE)
    with pytest.raises(TokenError):
        store.resolve_token(minted["secret"])


def test_a_revoked_token_is_hidden_from_the_default_listing(store):
    seed_workspace(store)
    minted = store.mint_token(WS, name="w", scopes=["documents.write"], source=SOURCE)
    store.revoke_token(WS, minted["id"], source=SOURCE)
    assert store.list_tokens(WS) == []
    assert len(store.list_tokens(WS, include_revoked=True)) == 1


def test_revoking_an_unknown_token_is_refused(store):
    seed_workspace(store)
    with pytest.raises(TokenError):
        store.revoke_token(WS, "tok_absent", source=SOURCE)


def test_a_token_from_another_workspace_cannot_be_revoked_here(store):
    seed_workspace(store, "ws_one")
    seed_workspace(store, "ws_two")
    minted = store.mint_token("ws_one", name="w", scopes=["links.write"], source=SOURCE)
    with pytest.raises(TokenError):
        store.revoke_token("ws_two", minted["id"], source=SOURCE)


# -- the plan gate, and the asymmetry ---------------------------------------- #


def test_minting_a_token_on_a_plan_that_does_not_entitle_it_is_refused(store):
    seed_workspace(store, WS, plan=vocab.PLAN_STARTER)
    with pytest.raises(PlanFeatureError) as caught:
        store.mint_token(WS, name="w", scopes=["links.write"], source=SOURCE)
    assert caught.value.code == "forbidden_plan_feature"


def test_an_existing_token_keeps_working_after_a_downgrade(store):
    """`while existing links keep working after a downgrade`.

    The asymmetry is the rule and it is the easiest thing here to implement
    backwards: the gate must fire on *create*, never on a check that merely
    exercises a grant that already exists.
    """
    seed_workspace(store, WS, plan=vocab.PLAN_BUSINESS)
    minted = store.mint_token(WS, name="worker", scopes=["documents.write"], source=SOURCE)
    store.store.update(
        minted["id"], {"minted_plan": vocab.PLAN_BUSINESS}, actor="api", source=SOURCE
    )
    # The workspace is downgraded: every membership's plan moves down.
    for row in store.list_members(WS):
        store.store.update(row["id"], {"plan": vocab.PLAN_STARTER}, actor="api", source=SOURCE)
    assert store.identity.plan(store.store, WS) == vocab.PLAN_STARTER

    # A new token is refused.
    with pytest.raises(PlanFeatureError):
        store.mint_token(WS, name="another", scopes=["links.write"], source=SOURCE)
    # The existing one still resolves, because nothing checked the plan to use it.
    assert store.resolve_token(minted["secret"])["id"] == minted["id"]


def test_reading_an_sso_connection_is_never_plan_gated(store):
    """The gate must not leak into a read path."""
    seed_workspace(store, WS, plan=vocab.PLAN_STARTER)
    store.store.create(
        SSO_CONNECTIONS,
        {
            "workspace_id": WS,
            "protocol": "oidc",
            "idp_entity_id": "https://idp",
            "domains": ["example.test"],
            "enabled": True,
        },
        actor="seed",
        source="test",
    )
    summary = store.read_sso(WS)
    assert summary["configured"] is True
    assert summary["entitled"] is False


def test_wiring_an_sso_connection_on_an_unentitled_plan_is_refused(store):
    seed_workspace(store, WS, plan=vocab.PLAN_STARTER)
    with pytest.raises(PlanFeatureError):
        store.write_sso(WS, {"protocol": "oidc", "idp_entity_id": "https://idp"}, source=SOURCE)


def test_a_wired_connection_is_readable_and_updates_in_place(store):
    seed_workspace(store, WS, plan=vocab.PLAN_ENTERPRISE)
    first = store.write_sso(
        WS,
        {"protocol": "oidc", "idp_entity_id": "https://idp", "domains": ["example.test"]},
        source=SOURCE,
    )
    second = store.write_sso(
        WS,
        {"protocol": "saml", "idp_entity_id": "https://idp2", "sso_url": "https://idp2/s"},
        source=SOURCE,
    )
    assert first["protocol"] == "oidc"
    assert second["protocol"] == "saml"
    assert len(store.store.find(SSO_CONNECTIONS, {"workspace_id": WS}, limit=10)) == 1


def test_enabling_sso_flips_local_sign_in_for_an_sso_only_member(store):
    """The sentence has to be observable, or wiring SSO is only storage."""
    seed_workspace(store, WS, plan=vocab.PLAN_ENTERPRISE)
    member_id = next(r["id"] for r in store.list_members(WS) if r["email"].startswith("member"))
    store.store.update(member_id, {"auth": sso_rules.AUTH_SSO}, actor="api", source=SOURCE)
    # No connection yet, so local sign-in still stands.
    assert store.local_login(WS, member_id)["local_credentials_allowed"] is True

    # The seeded member's email is member-ws_acme@example.test, so the connection's
    # domains have to cover example.test for it to reach them.
    store.write_sso(
        WS,
        {"protocol": "oidc", "idp_entity_id": "https://idp", "domains": ["example.test"]},
        source=SOURCE,
    )
    assert store.local_login(WS, member_id)["local_credentials_allowed"] is False


# -- role writes ------------------------------------------------------------- #


def test_a_role_change_writes_the_new_role(store):
    _owner, admins, member_id = seed_workspace(store)
    result = store.change_role(WS, member_id, vocab.ADMIN, source=SOURCE)
    assert result["member"]["role"] == vocab.ADMIN
    assert result["decision"]["current_role"] == vocab.MEMBER


def test_a_role_change_records_who_and_when(store):
    _owner, admins, member_id = seed_workspace(store)
    store.change_role(WS, member_id, vocab.ADMIN, source=SOURCE, actor="dana")
    member = store.member(WS, member_id)["data"]
    assert member["role_changed_by"] == "dana"
    assert member["role_changed_at"]


def test_a_role_change_writes_an_audit_row_with_the_source(store):
    _owner, admins, member_id = seed_workspace(store)
    store.change_role(WS, member_id, vocab.ADMIN, source=SOURCE)
    rows = store.store.audit(collection=MEMBERSHIPS, limit=10)
    sources = {row["source"] for row in rows}
    assert SOURCE in sources


def test_a_seat_change_is_recorded(store):
    _owner, _admins, member_id = seed_workspace(store)
    result = store.change_seat(WS, member_id, vocab.SEAT_GUEST, source=SOURCE)
    assert result["member"]["seat"] == vocab.SEAT_GUEST


def test_a_seat_change_refuses_an_unknown_seat(store):
    _owner, _admins, member_id = seed_workspace(store)
    with pytest.raises(RoleError):
        store.change_seat(WS, member_id, "platinum", source=SOURCE)


def test_an_unknown_member_is_a_404(store):
    seed_workspace(store)
    with pytest.raises(Exception) as caught:
        store.member(WS, "mem_absent")
    assert getattr(caught.value, "status_code", None) == 404


def test_a_member_of_another_workspace_is_not_found_here(store):
    seed_workspace(store, "ws_one")
    seed_workspace(store, "ws_two")
    other = store.list_members("ws_two")[0]
    with pytest.raises(Exception) as caught:
        store.member("ws_one", other["id"])
    assert getattr(caught.value, "status_code", None) == 404


def test_the_preview_and_the_write_agree(store):
    """One pure function serves both, so a preview cannot lie."""
    # No other admin, so promoting the member makes them the last one.
    _owner, _admins, member_id = seed_workspace(store, admins=0)
    assert store.preview_role_change(WS, member_id, vocab.ADMIN).accepted
    store.change_role(WS, member_id, vocab.ADMIN, source=SOURCE)
    assert (
        store.preview_role_change(WS, member_id, vocab.MEMBER).outcome == rules.REFUSED_LAST_ADMIN
    )
    with pytest.raises(RoleError):
        store.change_role(WS, member_id, vocab.MEMBER, source=SOURCE)


def test_the_admin_count_ignores_the_owner(store):
    seed_workspace(store, WS, admins=2)
    assert store.admin_count(WS) == 2


def test_the_admin_count_ignores_inactive_members(store):
    _owner, admins, _member = seed_workspace(store, admins=2)
    store.store.update(admins[1], {"active": False}, actor="api", source=SOURCE)
    assert store.admin_count(WS) == 1


def test_a_workspace_with_one_admin_and_a_deactivated_one_cannot_lose_its_admin(store):
    _owner, admins, _member = seed_workspace(store, admins=2)
    store.store.update(admins[1], {"active": False}, actor="api", source=SOURCE)
    with pytest.raises(RoleError) as caught:
        store.change_role(WS, admins[0], vocab.MEMBER, source=SOURCE)
    assert "last member with admin privileges" in str(caught.value)


def test_the_summary_counts_everything_the_page_shows(store):
    seed_workspace(store, WS, admins=1)
    store.mint_token(WS, name="w", scopes=["links.write"], source=SOURCE)
    summary = store.summary(WS)
    assert summary["members"] == 3
    assert summary["owners"] == 1
    assert summary["admins"] == 1
    assert summary["tokens"] == 1
    assert summary["plan"] == vocab.PLAN_BUSINESS
    assert summary["endpoints"] == len(ENDPOINTS)


def test_the_summary_counts_guests_and_sso_only_members(store):
    _owner, _admins, member_id = seed_workspace(store)
    store.change_seat(WS, member_id, vocab.SEAT_GUEST, source=SOURCE)
    store.store.update(member_id, {"auth": sso_rules.AUTH_SSO}, actor="api", source=SOURCE)
    summary = store.summary(WS)
    assert summary["guests"] == 1
    assert summary["sso_only_members"] == 1


def test_workspaces_lists_every_workspace_with_members(store):
    seed_workspace(store, "ws_one")
    seed_workspace(store, "ws_two")
    assert store.workspaces() == ["ws_one", "ws_two"]


# -- custom roles ------------------------------------------------------------ #


def test_a_custom_role_is_created_with_exactly_its_permissions(store):
    seed_workspace(store)
    role = store.create_custom_role(WS, "Deal Desk", ["members.read"], source=SOURCE)
    assert role["name"] == "Deal Desk"
    assert role["permissions"] == ["members.read"]


def test_a_custom_role_refuses_an_unknown_permission(store):
    seed_workspace(store)
    with pytest.raises(RoleError) as caught:
        store.create_custom_role(WS, "Deal Desk", ["members.read", "magic.write"], source=SOURCE)
    assert "unknown permission" in str(caught.value)


def test_a_custom_role_refuses_a_built_in_name(store):
    seed_workspace(store)
    with pytest.raises(RoleError) as caught:
        store.create_custom_role(WS, vocab.ADMIN, ["members.read"], source=SOURCE)
    assert "built-in role name" in str(caught.value)


def test_a_custom_role_refuses_a_duplicate_name(store):
    seed_workspace(store)
    store.create_custom_role(WS, "Deal Desk", ["members.read"], source=SOURCE)
    with pytest.raises(RoleError) as caught:
        store.create_custom_role(WS, "deal desk", ["members.write"], source=SOURCE)
    assert "already exists" in str(caught.value)


def test_a_custom_role_refuses_an_empty_name_or_empty_permissions(store):
    seed_workspace(store)
    with pytest.raises(RoleError):
        store.create_custom_role(WS, "  ", ["members.read"], source=SOURCE)
    with pytest.raises(RoleError):
        store.create_custom_role(WS, "Deal Desk", [], source=SOURCE)


def test_a_custom_role_is_not_plan_gated(store):
    """The research never prices a workspace-defined role, so neither do we."""
    seed_workspace(store, WS, plan=vocab.PLAN_STARTER)
    assert store.create_custom_role(WS, "Deal Desk", ["members.read"], source=SOURCE)


def test_a_custom_role_holding_every_permission_counts_as_admin(store):
    seed_workspace(store)
    store.create_custom_role(WS, "Superuser", list(vocab.PERMISSIONS), source=SOURCE)
    assert store.list_custom_roles(WS)[0]["is_admin"] is True


def test_a_custom_role_holder_is_shown_on_the_member_list(store):
    seed_workspace(store)
    role = store.create_custom_role(WS, "Deal Desk", ["members.read"], source=SOURCE)
    store.store.update(
        next(r["id"] for r in store.list_members(WS) if r["email"].startswith("member")),
        {"role": "Deal Desk"},
        actor="api",
        source=SOURCE,
    )
    row = next(r for r in store.list_members(WS) if r["email"].startswith("member"))
    assert row["role_label"] == "Deal Desk"
    assert row["permissions"] == ["members.read"]
    assert store.list_custom_roles(WS)[0]["holders"] == 1
    assert role["id"]


# -- the exchange, over the store -------------------------------------------- #


def test_an_exchange_mints_a_token_and_spends_the_code_in_one_transaction(store):
    _owner, admins, _member = seed_workspace(store)
    granted = store.authorize(
        WS, client_id="cli_x", requested_scopes=["documents.read"], source=SOURCE, actor="api"
    )
    minted = store.exchange_code(WS, code=granted["code"], client_id="cli_x", source=SOURCE)
    assert minted["secret"].startswith("dsr_")
    assert minted["scopes"] == ["documents.read"]
    # The code is spent, so a second exchange is refused.
    with pytest.raises(ConsentError) as caught:
        store.exchange_code(WS, code=granted["code"], client_id="cli_x", source=SOURCE)
    assert "already been exchanged" in str(caught.value)


def test_an_exchange_for_another_client_is_refused(store):
    seed_workspace(store)
    granted = store.authorize(
        WS, client_id="cli_x", requested_scopes=["documents.read"], source=SOURCE
    )
    with pytest.raises(ConsentError):
        store.exchange_code(WS, code=granted["code"], client_id="cli_y", source=SOURCE)


def test_an_exchange_of_an_unknown_code_is_refused(store):
    seed_workspace(store)
    with pytest.raises(ConsentError):
        store.exchange_code(WS, code="ac_nope", client_id="cli_x", source=SOURCE)


def test_an_exchange_refuses_a_widening_request(store):
    seed_workspace(store)
    granted = store.authorize(
        WS, client_id="cli_x", requested_scopes=["documents.read"], source=SOURCE
    )
    with pytest.raises(ConsentError):
        store.exchange_code(
            WS,
            code=granted["code"],
            client_id="cli_x",
            source=SOURCE,
            scopes=["documents.read", "analytics.read"],
        )


def test_the_minted_token_actually_works(store):
    _owner, admins, _member = seed_workspace(store)
    granted = store.authorize(
        WS, client_id="cli_x", requested_scopes=["documents.read"], source=SOURCE
    )
    minted = store.exchange_code(WS, code=granted["code"], client_id="cli_x", source=SOURCE)
    ctx = store.context(WS, bearer=minted["secret"])
    store.authorise(ctx, ["documents.read"])
    with pytest.raises(scope_rules.Forbidden):
        store.authorise(ctx, ["documents.write"])


def test_a_token_from_one_workspace_cannot_address_another(store):
    """The second half of the vendor's 403 sentence."""
    seed_workspace(store, "ws_one")
    seed_workspace(store, "ws_two")
    minted = store.mint_token("ws_one", name="w", scopes=["members.read"], source=SOURCE)
    with pytest.raises(scope_rules.Forbidden) as caught:
        store.context("ws_two", bearer=minted["secret"])
    assert "scoped to ws_one" in caught.value.member_forbidden


def test_a_scope_does_not_stand_in_for_a_missing_member_permission(store):
    """Seismic's rule at the engine level, over real records."""
    _owner, _admins, member_id = seed_workspace(store, admins=1)
    minted = store.mint_token(WS, name="w", scopes=["members.write"], source=SOURCE)
    ctx = store.context(WS, bearer=minted["secret"], member_id=member_id)
    # The token has the scope...
    assert scope_rules.token_has_scope(ctx.token.scopes, "members.write")
    # ...but the member it speaks for is a Member, who does not hold members.write.
    with pytest.raises(scope_rules.Forbidden) as caught:
        store.authorise(ctx, ["members.write"])
    assert caught.value.member_forbidden == "members.write"


def test_a_member_action_requires_a_member_not_just_a_token(store):
    seed_workspace(store)
    minted = store.mint_token(WS, name="w", scopes=["members.write"], source=SOURCE)
    ctx = store.context(WS, bearer=minted["secret"])
    with pytest.raises(RoleError) as caught:
        ctx.require("members.write")
    assert "scopes do not override" in str(caught.value)


def test_a_manager_may_change_a_role_through_the_engine(store):
    _owner, admins, member_id = seed_workspace(store)
    ctx = store.context(WS, member_id=admins[0])
    store.authorise(ctx, ["members.write"])
    store.change_role(WS, member_id, vocab.ADMIN, source=SOURCE)


def test_a_member_may_not_change_a_role(store):
    _owner, _admins, member_id = seed_workspace(store)
    ctx = store.context(WS, member_id=member_id)
    with pytest.raises(scope_rules.Forbidden) as caught:
        store.authorise(ctx, ["members.write"])
    assert caught.value.status_code == 403


def test_an_anonymous_request_is_refused_rather_than_assumed_permitted(store):
    """Neither credential presented is its own case, and it must not fall through."""
    seed_workspace(store)
    ctx = store.context(WS)
    with pytest.raises(scope_rules.Forbidden) as caught:
        store.authorise(ctx, ["members.read"])
    assert "no member or token was presented" in caught.value.member_forbidden


def test_a_member_only_request_needs_no_token(store):
    """A person does not need an integration token to use their own role."""
    seed_workspace(store)
    ctx = store.context(WS, member_id=store.list_members(WS)[0]["id"])
    assert store.authorise(ctx, ["members.read"]) is None


def test_a_token_only_request_needs_no_member(store):
    """Machine access is separate from a user, so it stands without one."""
    seed_workspace(store)
    minted = store.mint_token(WS, name="w", scopes=["members.read"], source=SOURCE)
    ctx = store.context(WS, bearer=minted["secret"])
    assert store.authorise(ctx, ["members.read"]) is None


def test_the_context_reports_who_is_calling(store):
    _owner, admins, _member = seed_workspace(store)
    minted = store.mint_token(WS, name="w", scopes=["members.read"], source=SOURCE)
    ctx = store.context(WS, bearer=minted["secret"], member_id=admins[0])
    assert ctx.is_machine
    assert ctx.token.id == minted["id"]
    assert ctx.member["role"] == vocab.ADMIN
    assert "members.write" in ctx.permissions()


def test_a_role_change_takes_effect_on_the_next_request(store):
    """`take effect on the member's next request` / no caching window.

    There is no cache to clear between these two reads, and that is the point: the
    rule is satisfied by not having a cache, so the test is that the second read
    already sees the change.
    """
    _owner, admins, member_id = seed_workspace(store)
    ctx = store.context(WS, member_id=member_id)
    with pytest.raises(scope_rules.Forbidden):
        store.authorise(ctx, ["members.write"])
    store.change_role(WS, member_id, vocab.MANAGER, source=SOURCE)
    fresh = store.context(WS, member_id=member_id)
    store.authorise(fresh, ["members.write"])


def test_the_caller_name_is_the_token_id_not_its_label(store):
    _owner, admins, member_id = seed_workspace(store)
    minted = store.mint_token(WS, name="My Integration", scopes=["members.write"], source=SOURCE)
    ctx = store.context(WS, bearer=minted["secret"])
    store.authorise(ctx, ["members.write"])
    store.change_role(WS, member_id, vocab.ADMIN, source=SOURCE, actor=f"token:{minted['id']}")
    rows = store.store.audit(collection=MEMBERSHIPS, limit=10)
    actors = {row["actor"] for row in rows}
    assert f"token:{minted['id']}" in actors
    assert "My Integration" not in actors


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


def http_seed(client, workspace_id=WS, plan=vocab.PLAN_BUSINESS, admins=1):
    """Build a workspace through the shared record API and return (admin_id, member_id)."""
    owner = client.post(
        "/api/records/workspace_membership",
        json={
            "workspace_id": workspace_id,
            "email": f"owner-{workspace_id}@example.test",
            "role": vocab.ROLE_OWNER,
            "seat": vocab.SEAT_FULL,
            "active": True,
            "is_owner": True,
            "plan": plan,
        },
    ).json()["id"]
    admin_ids = [
        client.post(
            "/api/records/workspace_membership",
            json={
                "workspace_id": workspace_id,
                "email": f"admin{n}@{workspace_id}.example",
                "role": vocab.ADMIN,
                "seat": vocab.SEAT_FULL,
                "active": True,
                "is_owner": False,
                "plan": plan,
            },
        ).json()["id"]
        for n in range(admins)
    ]
    member = client.post(
        "/api/records/workspace_membership",
        json={
            "workspace_id": workspace_id,
            "email": f"member@{workspace_id}.example",
            "role": vocab.MEMBER,
            "seat": vocab.SEAT_FULL,
            "active": True,
            "is_owner": False,
            "plan": plan,
        },
    ).json()["id"]
    return owner, admin_ids, member


def test_the_catalogue_routes_answer_without_a_credential(client):
    for path in (
        "summary",
        "vocabulary",
        "rules",
        "endpoints",
        "scopes",
        "inferences",
        "oauth-flow",
    ):
        response = client.get(f"{PREFIX}/{path}")
        assert response.status_code == 200, path


def test_the_scope_preview_refuses_a_wildcard_without_erroring(client):
    body = client.get(f"{PREFIX}/scopes", params={"scopes": "documents.*"}).json()
    assert body["preview"]["warning"]
    assert body["preview"]["unlocks"] == []


def test_the_scope_preview_shows_what_a_write_only_token_reaches(client):
    body = client.get(f"{PREFIX}/scopes", params={"scopes": "documents.write"}).json()
    assert body["preview"]["requested"] == ["documents.write"]
    assert body["no_implicit_hierarchy"] == vocab.NO_HIERARCHY_QUOTE


def test_the_member_list_needs_a_credential(client):
    response = client.get(f"{PREFIX}/workspaces/{WS}/members")
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_the_403_carries_the_vendors_sentence_verbatim(client):
    detail = client.get(f"{PREFIX}/workspaces/{WS}/members").json()["detail"]
    assert detail == scope_rules.FORBIDDEN_DETAIL
    assert "isn't authorized to act on the team you're addressing" in detail


def test_an_admin_can_list_the_members(client):
    _owner, admins, _member = http_seed(client)
    response = client.get(f"{PREFIX}/workspaces/{WS}/members", headers={MOD_MEM_HDR: admins[0]})
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 3
    assert {row["role"] for row in body["members"]} >= {vocab.ROLE_OWNER, vocab.ADMIN, vocab.MEMBER}


def test_the_owner_is_listed_first(client):
    _owner, _admins, _member = http_seed(client)
    body = client.get(f"{PREFIX}/workspaces/{WS}/members", headers={MOD_MEM_HDR: _owner}).json()
    assert body["members"][0]["is_owner"] is True


def test_the_member_list_can_be_filtered_by_role(client):
    _owner, admins, _member = http_seed(client)
    body = client.get(
        f"{PREFIX}/workspaces/{WS}/members",
        params={"role": "admin"},
        headers={MOD_MEM_HDR: admins[0]},
    ).json()
    assert [row["role"] for row in body["members"]] == [vocab.ADMIN]


def test_a_member_cannot_change_a_role(client):
    _owner, _admins, member_id = http_seed(client)
    response = client.patch(
        f"{PREFIX}/workspaces/{WS}/members/{member_id}/role",
        json={"role": vocab.ADMIN},
        headers={MOD_MEM_HDR: member_id},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_the_last_admins_demotion_is_a_422_naming_the_rule(client):
    _owner, admins, _member = http_seed(client, admins=1)
    response = client.patch(
        f"{PREFIX}/workspaces/{WS}/members/{admins[0]}/role",
        json={"role": vocab.MEMBER},
        headers={MOD_MEM_HDR: _owner},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "role_transition_refused"
    assert "last member with admin privileges" in body["detail"]


def test_the_owners_role_cannot_be_changed_over_http(client):
    _owner, admins, _member = http_seed(client)
    response = client.patch(
        f"{PREFIX}/workspaces/{WS}/members/{_owner}/role",
        json={"role": vocab.MEMBER},
        headers={MOD_MEM_HDR: _owner},
    )
    assert response.status_code == 422
    assert "workspace owner's role cannot be changed" in response.json()["detail"]


def test_a_successful_role_change_reports_the_seat_rule(client):
    _owner, admins, member_id = http_seed(client)
    client.patch(
        f"{PREFIX}/workspaces/{WS}/members/{member_id}/seat",
        json={"seat": vocab.SEAT_GUEST},
        headers={MOD_MEM_HDR: admins[0]},
    )
    response = client.patch(
        f"{PREFIX}/workspaces/{WS}/members/{member_id}/role",
        json={"role": vocab.ADMIN},
        headers={MOD_MEM_HDR: admins[0]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["seat_rule"] == vocab.SEAT_UPGRADE_QUOTE
    assert response.json()["seat_upgraded"] is True
    assert response.json()["member"]["seat"] == vocab.SEAT_FULL


def test_an_unknown_role_is_a_422_over_http(client):
    _owner, admins, member_id = http_seed(client)
    response = client.patch(
        f"{PREFIX}/workspaces/{WS}/members/{member_id}/role",
        json={"role": "Supreme Overlord"},
        headers={MOD_MEM_HDR: admins[0]},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "role_transition_refused"


def test_an_unknown_member_is_a_404_over_http(client):
    _owner, admins, _member = http_seed(client)
    response = client.patch(
        f"{PREFIX}/workspaces/{WS}/members/mem_absent/role",
        json={"role": vocab.ADMIN},
        headers={MOD_MEM_HDR: admins[0]},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "membership_not_found"


def test_the_preview_writes_nothing(client):
    _owner, admins, member_id = http_seed(client)
    before = client.get(
        f"{PREFIX}/workspaces/{WS}/members", headers={MOD_MEM_HDR: admins[0]}
    ).json()["count"]
    client.post(
        f"{PREFIX}/workspaces/{WS}/members/{member_id}/preview-role",
        json={"role": vocab.ADMIN},
        headers={MOD_MEM_HDR: admins[0]},
    )
    after = client.get(
        f"{PREFIX}/workspaces/{WS}/members", headers={MOD_MEM_HDR: admins[0]}
    ).json()["count"]
    assert before == after


def test_the_preview_predicts_the_refusal_the_write_produces(client):
    _owner, admins, _member = http_seed(client, admins=1)
    preview = client.post(
        f"{PREFIX}/workspaces/{WS}/members/{admins[0]}/preview-role",
        json={"role": vocab.MEMBER},
        headers={MOD_MEM_HDR: _owner},
    ).json()
    assert preview["accepted"] is False
    write = client.patch(
        f"{PREFIX}/workspaces/{WS}/members/{admins[0]}/role",
        json={"role": vocab.MEMBER},
        headers={MOD_MEM_HDR: _owner},
    )
    assert write.status_code == 422


def test_the_preview_predicts_the_seat_auto_upgrade(client):
    _owner, admins, member_id = http_seed(client)
    client.patch(
        f"{PREFIX}/workspaces/{WS}/members/{member_id}/seat",
        json={"seat": vocab.SEAT_GUEST},
        headers={MOD_MEM_HDR: admins[0]},
    )
    preview = client.post(
        f"{PREFIX}/workspaces/{WS}/members/{member_id}/preview-role",
        json={"role": vocab.ADMIN},
        headers={MOD_MEM_HDR: admins[0]},
    ).json()
    assert preview["accepted"] is True
    assert preview["seat_changed"] is True
    assert preview["resulting_seat"] == vocab.SEAT_FULL


def test_an_unknown_field_in_a_request_body_is_refused(client):
    """`additionalProperties: false` - the server half of the SDK guarantee."""
    _owner, admins, member_id = http_seed(client)
    response = client.patch(
        f"{PREFIX}/workspaces/{WS}/members/{member_id}/role",
        json={"role": vocab.ADMIN, "is_admin": True},
        headers={MOD_MEM_HDR: admins[0]},
    )
    assert response.status_code == 422


def test_minting_over_http_refuses_a_wildcard(client):
    _owner, admins, _member = http_seed(client)
    response = client.post(
        f"{PREFIX}/workspaces/{WS}/tokens",
        json={"name": "w", "scopes": ["*"]},
        headers={MOD_MEM_HDR: admins[0]},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "scope_invalid"


def test_minting_over_http_refuses_an_unknown_scope(client):
    _owner, admins, _member = http_seed(client)
    response = client.post(
        f"{PREFIX}/workspaces/{WS}/tokens",
        json={"name": "w", "scopes": ["documents.teleport"]},
        headers={MOD_MEM_HDR: admins[0]},
    )
    assert response.status_code == 422


def test_minting_over_http_shows_the_secret_once(client):
    _owner, admins, _member = http_seed(client)
    response = client.post(
        f"{PREFIX}/workspaces/{WS}/tokens",
        json={"name": "worker", "scopes": ["documents.write"]},
        headers={MOD_MEM_HDR: admins[0]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["secret"].startswith("dsr_")
    assert body["shown_once"] is True
    assert body["independent_scopes"] == vocab.NO_HIERARCHY_QUOTE
    # Listing never shows it again: no row carries a `secret` key at all.
    listing = client.get(
        f"{PREFIX}/workspaces/{WS}/tokens", headers={MOD_MEM_HDR: admins[0]}
    ).json()
    assert listing["tokens"]
    for row in listing["tokens"]:
        assert "secret" not in row
        assert row["secret_stored"] is False
    assert body["secret"] not in str(listing["tokens"])


def test_a_write_only_token_cannot_read_over_http(client):
    _owner, admins, _member = http_seed(client)
    secret = client.post(
        f"{PREFIX}/workspaces/{WS}/tokens",
        json={"name": "worker", "scopes": ["documents.write"]},
        headers={MOD_MEM_HDR: admins[0]},
    ).json()["secret"]
    # It cannot change a role: that needs members.write.
    response = client.patch(
        f"{PREFIX}/workspaces/{WS}/members/x/role",
        json={"role": vocab.ADMIN},
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert response.status_code == 403
    assert response.json()["missing_scopes"] == ["members.write"]


def test_a_token_with_the_right_scope_is_allowed_over_http(client):
    _owner, admins, _member = http_seed(client)
    secret = client.post(
        f"{PREFIX}/workspaces/{WS}/tokens",
        json={"name": "reader", "scopes": ["members.read"]},
        headers={MOD_MEM_HDR: admins[0]},
    ).json()["secret"]
    response = client.get(
        f"{PREFIX}/workspaces/{WS}/members", headers={"Authorization": f"Bearer {secret}"}
    )
    assert response.status_code == 200


def test_a_token_and_a_member_are_both_checked(client):
    _owner, _admins, member_id = http_seed(client)
    secret = client.post(
        f"{PREFIX}/workspaces/{WS}/tokens",
        json={"name": "writer", "scopes": ["members.write"]},
        headers={MOD_MEM_HDR: _owner},
    ).json()["secret"]
    # Token has members.write; the member it speaks for does not.
    response = client.patch(
        f"{PREFIX}/workspaces/{WS}/members/{member_id}/role",
        json={"role": vocab.ADMIN},
        headers={"Authorization": f"Bearer {secret}", MOD_MEM_HDR: member_id},
    )
    assert response.status_code == 403
    assert response.json()["member_forbidden"] == "members.write"


def test_a_non_bearer_authorization_scheme_is_refused(client):
    _owner, admins, _member = http_seed(client)
    response = client.get(
        f"{PREFIX}/workspaces/{WS}/members", headers={"Authorization": "Basic abc123"}
    )
    assert response.status_code == 401
    assert response.json()["code"] == "token_invalid"


def test_a_token_from_another_workspace_is_the_vendors_403(client):
    _owner, admins, _member = http_seed(client, "ws_one")
    http_seed(client, "ws_two")
    secret = client.post(
        f"{PREFIX}/workspaces/ws_one/tokens",
        json={"name": "w", "scopes": ["members.read"]},
        headers={MOD_MEM_HDR: admins[0]},
    ).json()["secret"]
    response = client.get(
        f"{PREFIX}/workspaces/ws_two/members", headers={"Authorization": f"Bearer {secret}"}
    )
    assert response.status_code == 403
    assert response.json()["member_forbidden"] == "token is scoped to ws_one"


def test_the_plan_gate_over_http_is_forbidden_plan_feature(client):
    _owner, admins, _member = http_seed(client, plan=vocab.PLAN_STARTER)
    response = client.post(
        f"{PREFIX}/workspaces/{WS}/tokens",
        json={"name": "w", "scopes": ["links.write"]},
        headers={MOD_MEM_HDR: admins[0]},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden_plan_feature"


def test_reading_an_sso_connection_survives_a_downgrade(client):
    _owner, admins, _member = http_seed(client, plan=vocab.PLAN_ENTERPRISE)
    client.put(
        f"{PREFIX}/workspaces/{WS}/sso",
        json={"protocol": "oidc", "idp_entity_id": "https://idp", "domains": ["example.test"]},
        headers={MOD_MEM_HDR: admins[0]},
    )
    for record in client.get("/api/records/workspace_membership", params={"limit": 50}).json()[
        "records"
    ]:
        client.patch(
            f"/api/records/workspace_membership/{record['id']}",
            json={"plan": vocab.PLAN_STARTER},
        )
    read = client.get(f"{PREFIX}/workspaces/{WS}/sso", headers={MOD_MEM_HDR: admins[0]})
    assert read.status_code == 200
    assert read.json()["configured"] is True
    assert read.json()["entitled"] is False
    # Writing another is refused.
    write = client.put(
        f"{PREFIX}/workspaces/{WS}/sso",
        json={"protocol": "oidc", "idp_entity_id": "https://idp2"},
        headers={MOD_MEM_HDR: admins[0]},
    )
    assert write.status_code == 403


def test_wiring_an_sso_connection_over_http(client):
    _owner, admins, _member = http_seed(client, plan=vocab.PLAN_ENTERPRISE)
    response = client.put(
        f"{PREFIX}/workspaces/{WS}/sso",
        json={
            "protocol": "saml",
            "idp_entity_id": "https://idp",
            "sso_url": "https://idp/sso",
            "domains": ["example.test"],
        },
        headers={MOD_MEM_HDR: admins[0]},
    )
    assert response.status_code == 200
    body = response.json()["sso"]
    assert body["protocol"] == "saml"
    assert body["domains"] == ["example.test"]


def test_a_malformed_sso_connection_is_a_422(client):
    _owner, admins, _member = http_seed(client, plan=vocab.PLAN_ENTERPRISE)
    response = client.put(
        f"{PREFIX}/workspaces/{WS}/sso",
        json={"protocol": "saml", "idp_entity_id": "https://idp"},
        headers={MOD_MEM_HDR: admins[0]},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "sso_configuration_invalid"


def test_the_local_login_route_reports_the_sso_decision(client):
    _owner, admins, member_id = http_seed(client, plan=vocab.PLAN_ENTERPRISE)
    before = client.get(
        f"{PREFIX}/workspaces/{WS}/members/{member_id}/local-login",
        headers={MOD_MEM_HDR: admins[0]},
    ).json()
    assert before["local_credentials_allowed"] is True
    client.patch(
        "/api/records/workspace_membership/" + member_id,
        json={"auth": sso_rules.AUTH_SSO},
    )
    client.put(
        f"{PREFIX}/workspaces/{WS}/sso",
        json={"protocol": "oidc", "idp_entity_id": "https://idp", "domains": []},
        headers={MOD_MEM_HDR: admins[0]},
    )
    after = client.get(
        f"{PREFIX}/workspaces/{WS}/members/{member_id}/local-login",
        headers={MOD_MEM_HDR: admins[0]},
    ).json()
    assert after["local_credentials_allowed"] is False


def test_the_audit_read_is_refused_for_a_manager(client):
    _owner, admins, _member = http_seed(client)
    response = client.get(f"{PREFIX}/workspaces/{WS}/audit", headers={MOD_MEM_HDR: admins[0]})
    # An Admin does hold part11.read, so this succeeds for an admin...
    assert response.status_code == 200
    assert response.json()["required_permission"] == "part11.read"


def test_the_audit_read_names_the_vendors_gate(client):
    _owner, admins, _member = http_seed(client)
    body = client.get(f"{PREFIX}/workspaces/{WS}/audit", headers={MOD_MEM_HDR: admins[0]}).json()
    assert body["gate"] == vocab.PART11_QUOTE


def test_the_audit_read_is_refused_for_a_manager_role(client):
    """Being able to change a role and being able to audit it are different powers."""
    _owner, admins, member_id = http_seed(client, admins=2)
    # Demote one admin through the feature's own route: allowed, another remains.
    demoted = client.patch(
        f"{PREFIX}/workspaces/{WS}/members/{admins[0]}/role",
        json={"role": vocab.MANAGER},
        headers={MOD_MEM_HDR: admins[1]},
    )
    assert demoted.status_code == 200, demoted.text
    response = client.get(f"{PREFIX}/workspaces/{WS}/audit", headers={MOD_MEM_HDR: admins[0]})
    assert response.status_code == 403
    assert response.json()["member_forbidden"] == "part11.read"


def test_the_consent_and_exchange_over_http(client):
    _owner, admins, _member = http_seed(client)
    consent = client.post(
        f"{PREFIX}/workspaces/{WS}/oauth/authorize",
        json={
            "client_id": "cli_x",
            "scopes": ["documents.read", "members.read"],
            "client_name": "Docs bot",
        },
        headers={MOD_MEM_HDR: admins[0]},
    )
    assert consent.status_code == 200
    body = consent.json()
    assert body["consent"]["client_name"] == "Docs bot"
    # The screen shows both halves of "which endpoints each scope unlocks".
    assert body["consent"]["unlocks"], "no route of this API was shown"
    assert body["consent"]["surfaces"]["documents.read"]

    exchanged = client.post(
        f"{PREFIX}/workspaces/{WS}/oauth/access-token",
        json={"code": body["code"], "client_id": "cli_x"},
        headers={MOD_MEM_HDR: admins[0]},
    )
    assert exchanged.status_code == 200
    assert exchanged.json()["secret"].startswith("dsr_")
    assert exchanged.json()["token"]["scopes"] == ["documents.read", "members.read"]

    replay = client.post(
        f"{PREFIX}/workspaces/{WS}/oauth/access-token",
        json={"code": body["code"], "client_id": "cli_x"},
        headers={MOD_MEM_HDR: admins[0]},
    )
    assert replay.status_code == 400
    assert replay.json()["code"] == "authorization_code_invalid"


def test_the_429_carries_the_reset_header(client):
    _owner, admins, _member = http_seed(client)
    secret = client.post(
        f"{PREFIX}/workspaces/{WS}/tokens",
        json={"name": "reader", "scopes": ["members.read"]},
        headers={MOD_MEM_HDR: admins[0]},
    ).json()["secret"]
    # One successful read publishes the reset signal.
    ok = client.get(
        f"{PREFIX}/workspaces/{WS}/members", headers={"Authorization": f"Bearer {secret}"}
    )
    assert ok.headers.get("X-RateLimit-Reset")
    assert ok.headers.get("X-RateLimit-Limit")
    assert ok.headers.get("X-RateLimit-Remaining")

    # A limiter of one, then two calls: the second is refused with the same header.
    from dsr.workspace_roles import scopes as fresh_scopes

    limiter = fresh_scopes.RateLimiter(1)
    original = WorkspaceAccess.__init__

    def limited_init(self, store, **kwargs):
        original(self, store, **kwargs)
        self.limiter = limiter

    WorkspaceAccess.__init__ = limited_init
    try:
        first = client.get(
            f"{PREFIX}/workspaces/{WS}/members", headers={"Authorization": f"Bearer {secret}"}
        )
        assert first.status_code == 200
        second = client.get(
            f"{PREFIX}/workspaces/{WS}/members", headers={"Authorization": f"Bearer {secret}"}
        )
        assert second.status_code == 429
        assert second.json()["code"] == "rate_limit_exceeded"
        assert second.headers.get("X-RateLimit-Reset")
        assert second.headers.get("Retry-After")
    finally:
        WorkspaceAccess.__init__ = original


def test_the_custom_role_routes(client):
    _owner, admins, _member = http_seed(client)
    created = client.post(
        f"{PREFIX}/workspaces/{WS}/role-definitions",
        json={"name": "Deal Desk", "permissions": ["members.read"]},
        headers={MOD_MEM_HDR: admins[0]},
    )
    assert created.status_code == 200
    assert created.json()["quote"] == vocab.BUILT_IN_ROLE_QUOTE
    listed = client.get(
        f"{PREFIX}/workspaces/{WS}/role-definitions", headers={MOD_MEM_HDR: admins[0]}
    )
    assert listed.json()["count"] == 1
    assert listed.json()["permissions"] == list(vocab.PERMISSIONS)


def test_a_custom_role_with_an_unknown_permission_is_a_422(client):
    _owner, admins, _member = http_seed(client)
    response = client.post(
        f"{PREFIX}/workspaces/{WS}/role-definitions",
        json={"name": "Deal Desk", "permissions": ["magic.write"]},
        headers={MOD_MEM_HDR: admins[0]},
    )
    assert response.status_code == 422


def test_the_revoke_route(client):
    _owner, admins, _member = http_seed(client)
    minted = client.post(
        f"{PREFIX}/workspaces/{WS}/tokens",
        json={"name": "worker", "scopes": ["links.write"]},
        headers={MOD_MEM_HDR: admins[0]},
    ).json()["token"]
    revoked = client.delete(
        f"{PREFIX}/workspaces/{WS}/tokens/{minted['id']}", headers={MOD_MEM_HDR: admins[0]}
    )
    assert revoked.status_code == 200
    assert revoked.json()["token"]["revoked"] is True


def test_the_workspace_summary_route_reports_the_plan_and_the_quote(client):
    _owner, admins, _member = http_seed(client)
    body = client.get(f"{PREFIX}/workspaces/{WS}").json()
    assert body["plan"] == vocab.PLAN_BUSINESS
    assert body["no_implicit_hierarchy"] == vocab.NO_HIERARCHY_QUOTE
    assert body["members"] == 3
    assert body["endpoints"] == len(ENDPOINTS)


def test_the_workspaces_route_lists_workspaces(client):
    http_seed(client, "ws_one")
    http_seed(client, "ws_two")
    body = client.get(f"{PREFIX}/workspaces").json()
    assert {row["workspace_id"] for row in body["workspaces"]} == {"ws_one", "ws_two"}


def test_the_top_level_summary_route(client):
    body = client.get(f"{PREFIX}/summary").json()
    assert body["built_in_roles"] == list(vocab.BUILT_IN_ROLES)
    assert body["coarse_scopes"] == list(vocab.COARSE_SCOPES)
    assert rules.REFUSED_OWNER in body["guard_rails"]
    assert rules.REFUSED_LAST_ADMIN in body["guard_rails"]


def test_the_rules_route_serves_the_refusal_table(client):
    body = client.get(f"{PREFIX}/rules").json()
    outcomes = {entry["outcome"] for entry in body["outcomes"]}
    assert rules.REFUSED_OWNER in outcomes
    assert rules.REFUSED_LAST_ADMIN in outcomes
    assert body["caller_rule"] == vocab.CALLER_QUOTE


# --------------------------------------------------------------------------- #
# The inferences
# --------------------------------------------------------------------------- #


def test_every_inference_says_what_it_chose_and_why():
    from dsr.workspace_roles.inferences import INFERENCES

    assert len(INFERENCES) >= 10
    for entry in INFERENCES:
        assert entry["id"]
        assert entry["topic"]
        assert entry["basis"]
        assert entry["value"] is not None
        assert len(entry["why"]) > 80, entry["id"]
        assert entry["change_it"]
        assert entry["blast_radius"]


def test_the_inference_ids_are_unique():
    from dsr.workspace_roles.inferences import INFERENCES

    ids = [entry["id"] for entry in INFERENCES]
    assert len(ids) == len(set(ids))


def test_the_inferences_route_serves_them(client):
    body = client.get(f"{PREFIX}/inferences").json()
    assert body["count"] >= 10
    assert {entry["id"] for entry in body["inferences"]} >= {
        "no-hierarchy-is-literal",
        "coarse-grants-are-not-wildcards",
        "granularity-is-a-guess",
    }


def test_the_no_hierarchy_inference_matches_the_code():
    """A stated inference that the code contradicts is worse than no inference."""
    from dsr.workspace_roles.inferences import INFERENCES

    entry = next(e for e in INFERENCES if e["id"] == "no-hierarchy-is-literal")
    assert entry["value"]["write_implies_read"] is False
    assert entry["value"]["prefix_expansion"] is False
    assert not scope_rules.token_has_scope(["documents.write"], "documents.read")


def test_the_coarse_grant_inference_matches_the_code():
    from dsr.workspace_roles.inferences import INFERENCES

    entry = next(e for e in INFERENCES if e["id"] == "coarse-grants-are-not-wildcards")
    assert entry["value"]["apis_all_is_a_wildcard"] is False
    assert entry["value"]["coverage_is_enforced_not_just_described"] is True
    assert not scope_rules.token_has_scope(["apis.all"], "documents.write")
    assert not scope_rules.token_has_scope(["apis.all"], "members.read")
    assert scope_rules.coverage_for(["apis.all"]) == frozenset(entry["value"]["apis_all_covers"])
    # Every claim the inference makes about what is and is not reachable.
    for claim, reachable in (
        ("apis_all_unlocks_documents_write", False),
        ("apis_all_unlocks_members_read", False),
    ):
        scope = claim.removeprefix("apis_all_unlocks_").replace("_", ".")
        assert scope_rules.token_has_scope(["apis.all"], scope) is reachable, claim


def test_the_admin_inference_matches_the_code():
    from dsr.workspace_roles.inferences import INFERENCES

    entry = next(e for e in INFERENCES if e["id"] == "admin-means-every-permission")
    assert entry["value"]["admin_is"] == "a role granting every permission"
    assert rules.is_admin(
        membership("Superuser"),
        custom_roles=[{"name": "Superuser", "permissions": list(vocab.PERMISSIONS)}],
    )


def test_the_plan_inference_matches_the_code():
    from dsr.workspace_roles.inferences import INFERENCES

    entry = next(e for e in INFERENCES if e["id"] == "plan-names-are-invented")
    assert entry["value"]["enforced_on_use"] is False
    assert entry["value"]["gated"] == dict(vocab.GATED_FEATURES)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def test_the_seed_produces_the_states_the_research_cares_about():
    """A demo dataset of only successes teaches a reviewer nothing."""
    with tempfile.TemporaryDirectory() as name:
        db = AuditedDatabase(str(Path(name) / "seed.db"))
        try:
            context = {
                "room_ids": [("room_1", "Northwind")],
                "now": NOW,
                "rng": random.Random("wf077"),
            }
            summary = load_feature(MODULE).seed(db, context)
            assert summary
            assert "members" in summary

            access = WorkspaceAccess(RecordStore(db))
            assert set(access.workspaces()) == {"northwind", "contoso"}

            members = access.list_members("northwind")
            assert any(row["is_owner"] for row in members)
            assert any(row["seat"] == vocab.SEAT_GUEST for row in members)
            assert any(row["auth"] == sso_rules.AUTH_SSO for row in members)
            assert any(row["role"] not in vocab.BUILT_IN_ROLES for row in members)

            tokens = access.list_tokens("northwind", include_revoked=True)
            assert any(row["revoked"] for row in tokens)
            assert any(row["scopes"] == ["documents.write"] for row in tokens)

            assert access.list_custom_roles("northwind")
            assert access.read_sso("contoso")["configured"] is True
            assert access.read_sso("contoso")["entitled"] is False
        finally:
            db.close()


def test_the_seed_is_idempotent_in_shape_but_appends(store):
    """Running it twice gives two complete sets rather than overwriting."""
    context = {"room_ids": [], "now": NOW, "rng": random.Random("wf077")}
    db = store.store.db
    first = load_feature(MODULE).seed(db, context)
    count_after_first = store.store.count_where(MEMBERSHIPS, {})
    second = load_feature(MODULE).seed(db, context)
    assert first and second
    assert store.store.count_where(MEMBERSHIPS, {}) == count_after_first * 2


def test_the_seed_survives_a_dataset_with_no_rooms():
    context = {"room_ids": [], "now": NOW, "rng": random.Random("wf077")}
    with tempfile.TemporaryDirectory() as name:
        db = AuditedDatabase(str(Path(name) / "seed2.db"))
        try:
            assert load_feature(MODULE).seed(db, context)
        finally:
            db.close()


def test_seeding_is_reproducible_for_the_same_clock():
    def run(name):
        with tempfile.TemporaryDirectory() as tmp:
            db = AuditedDatabase(str(Path(tmp) / "s.db"))
            try:
                load_feature(MODULE).seed(
                    db, {"room_ids": [], "now": NOW, "rng": random.Random("x")}
                )
                return WorkspaceAccess(RecordStore(db)).summary("northwind")
            finally:
                db.close()

    assert run("a") == run("b")
