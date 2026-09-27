"""Tests for the WF-004 plugin port: the decisions the port made, pinned.

The workflow's behaviour is tested in ``test_roles.py`` (the domain, against the
pure :mod:`dsr.roles` with an injected clock) and ``test_roles_api.py`` (the HTTP
surface: status codes, envelope, refusals). This file is about the things the
*port* decided, which are the things a reviewer most needs to be able to check
later:

* the feature is mounted by discovery alone, under a prefix it owns, and its
  domain error handler is one the host attaches rather than one that had to be
  written into ``dsr/api.py``;
* the module is ``dsr.roles`` and not ``dsr.access``, which is a recorded
  decision about two features claiming one module path, not a naming preference;
* every write's audit row names the path this router actually serves, and the
  branch's hard-coded paths are gone;
* the demo rows come from this feature's own ``seed`` hook rather than an edit to
  ``backend/seed.py``.

No test here is marked ``xfail``. Every gap this port could not close without
editing a shared file is reported in the final report instead, because unlike the
WF-013 no-bypass gap it is not a behavioural hole in this feature: this feature
serves its own routes and every one of them enforces the rules.
"""

from __future__ import annotations

import random
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.roles import ACCESS, INVITATIONS, AccessError, AccessService

PREFIX = "/api/wf-004-invite-buyer"
MODULE = "wf004_roles"
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def router_source():
    """The HTTP surface's own source, read without going through the host.

    ``load_feature`` only reaches modules inside ``dsr/features/``; the router
    lives beside the domain module in ``dsr/``, so it is imported directly.
    """
    import dsr.roles_api

    return Path(dsr.roles_api.__file__)


@pytest.fixture()
def client(monkeypatch):
    # A temporary database, exactly as test_features.py does it: the env var is
    # read at call time by dsr.deps, so every test gets its own store.
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf004.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


# -- helpers -------------------------------------------------------------------- #


def make_room(client, name="Acme", owner="dana"):
    return client.post("/api/records/room", json={"name": name, "owner": owner}).json()["id"]


def grant(client, room_id, principal, role="viewer", access_valid_until=None):
    return client.post(
        "/api/records/room_access",
        params={"room_id": room_id},
        json={
            "principal": principal,
            "role": role,
            "access_valid_until": access_valid_until,
            "source": "manual",
        },
    ).json()


def audit(client, **params):
    return client.get("/api/audit", params=params).json()["entries"]


# -- registration ---------------------------------------------------------------- #


def test_the_feature_is_mounted_by_discovery(client):
    """No file in the host names this feature; discovery mounts it anyway."""
    body = client.get("/api/features").json()
    installed = {feature["id"]: feature for feature in body["features"]}

    assert "wf-004-invite-buyer" in installed
    record = installed["wf-004-invite-buyer"]
    assert record["prefix"] == PREFIX
    assert record["ticket"] == "WF-004"
    # The error mapping moved out of dsr/api.py and into the feature's export.
    assert record["exception_handlers"] == ["AccessError"]
    assert record["routes"], "the feature mounted a router but reported no routes"


def test_the_feature_did_not_collide_with_anything(client):
    body = client.get("/api/features").json()
    assert body["failed_count"] == 0, body["failed"]


def test_the_whole_share_surface_sits_under_one_prefix(client):
    """Every route this feature serves, on one prefix, and no other."""
    record = client.get(f"/api/features/wf-004-invite-buyer").json()
    paths = {route["path"] for route in record["routes"]}

    assert paths == {
        f"{PREFIX}/access/roles",
        f"{PREFIX}/rooms/{{room_id}}/access",
        f"{PREFIX}/rooms/{{room_id}}/invitations",
        f"{PREFIX}/invitations/{{invitation_id}}/accept",
        f"{PREFIX}/access/{{access_id}}",
    }


def test_no_module_of_this_feature_imports_the_shared_app():
    """A guard exists in test_features.py; this states the reason locally.

    Checked on both modules rather than only the plugin: the plugin re-exports
    the router, so the coupling would be one import away either way.
    """
    for path in (Path(load_feature(MODULE).__file__), router_source()):
        source = path.read_text(encoding="utf-8")
        assert "from dsr.api" not in source and "import dsr.api" not in source, path.name


def test_the_router_reaches_the_store_through_the_shared_dependency_seam():
    """``StoreDep``, not ``request.app.state.store`` reached the long way round.

    The branch built its service from the request object directly. The contract's
    seam is ``dsr.deps``, and the difference matters: a feature that reaches past
    the seam is a feature that cannot be mounted by anything but the core app.

    Checked on the parsed module with docstrings stripped, because this module's
    own docstring names ``request.app.state.store`` while explaining the branch it
    replaced.
    """
    import ast

    import dsr.roles_api

    tree = ast.parse(router_source().read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                node.body.pop(0)
    code = ast.unparse(tree)

    assert "from dsr.deps import" in code
    assert "app.state.store" not in code

    # Assert the shape rather than a rendered string: the service dependency
    # takes its store from the seam, so a feature built this way can be mounted
    # by anything that can supply a RecordStore.
    factory = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "get_service"
    )
    parameter = factory.args.args[0]
    default = factory.args.defaults[0] if factory.args.defaults else None
    assert parameter.arg == "store"
    assert isinstance(parameter.annotation, ast.Name) and parameter.annotation.id == "RecordStore"
    assert isinstance(default, ast.Name) and default.id == "StoreDep"
    assert dsr.roles_api.ServiceDep is not None


# -- the module rename, which is a decision and not a preference ------------------ #
#
# Two workflows in this pair both wrote `backend/dsr/access.py`. Two features
# cannot own one module path: that is the exact failure the plugin host exists to
# end. This branch is about granting roles, so the module is `dsr.roles`; the
# sibling keeps `dsr.access` because it verifies identity. The two files were
# measured at 3.2% overlap in the domain module and 2 shared symbols out of 74,
# and the two-workflow landing was gated at confidence 1.00 (jev-20260927T052837).
# These tests exist so that a later merge cannot quietly reintroduce the
# collision, and so a reviewer can see the decision was deliberate.


def test_this_branch_owns_roles_and_not_access():
    """WF-004 claims `dsr.roles`. It must not also claim `dsr.access`.

    Stated as a property of THIS feature, not of the package as a whole. The first
    version of this test asserted `not (package / "access.py").exists()` - that no
    `access.py` exists anywhere - which reads as the right guard and is a much
    stronger claim. It passes only while WF-015 is absent, and WF-015 legitimately
    ships `access.py`: the whole point of the rename was that WF-015 keeps that
    name. So the assertion went false the moment the sibling landed, and took a
    test red in a suite that has nothing to do with the failure.

    The guard the comment above asks for is "a later merge cannot quietly
    reintroduce the collision". That is about what THIS feature imports and owns,
    so that is what is checked: its module exists, and nothing of this feature's
    reaches for `dsr.access`.
    """
    package = Path(load_feature(MODULE).__file__).resolve().parent.parent
    assert (package / "roles.py").exists(), "WF-004's domain module is dsr.roles"

    feature_file = Path(load_feature(MODULE).__file__).resolve()
    mine = [feature_file] + sorted(
        p for p in (package / "features").glob("*.py") if "wf004" in p.name
    )
    for path in mine:
        text = path.read_text(encoding="utf-8", errors="replace")
        offenders = [
            line.strip()
            for line in text.splitlines()
            if ("dsr.access" in line or "dsr\\.access" in line or "from .access" in line)
            and not line.strip().startswith("#")
        ]
        assert not offenders, (
            f"{path.name} must not import dsr.access - that module belongs to "
            f"WF-015: {offenders}"
        )


def test_the_access_module_is_not_ours_to_rename_or_delete():
    """`dsr.access` is WF-015's. WF-004 must leave it alone.

    The other half of the same guard, and the reason the first version of it was
    wrong in a way that mattered: a test that insists a file does not exist
    invites someone to "fix" a red suite by deleting it, which would take WF-015's
    domain with it. So assert this feature neither creates nor claims it.
    """
    package = Path(load_feature(MODULE).__file__).resolve().parent.parent
    access = package / "access.py"
    if not access.exists():
        return  # WF-015 has not landed in this tree; nothing to leave alone yet

    import dsr.access as access_module

    # The module is a real, importable, non-trivial one - i.e. it is somebody
    # else's working code and not an empty husk.
    assert Path(access_module.__file__).resolve() == access.resolve()
    source = access.read_text(encoding="utf-8", errors="replace")
    assert "def " in source, "dsr.access exists but has no behaviour in it"
    # And it is about verifying identity, which is the workflow that owns it.
    assert "verif" in source.lower() or "session" in source.lower() or "policy" in source.lower()


def test_the_workflow_really_is_about_granting():
    """The vocabulary is the evidence that the two branches are not the same work."""
    import dsr.roles as roles

    granting = [
        "assignable_roles",
        "role_vocabulary",
        "can_share",
        "end_of_day_utc",
        "ConfirmationRequired",
        "INVITATION_TTL_HOURS",
    ]
    for name in granting:
        assert hasattr(roles, name), name
    # The verbs that make this workflow distinct from a verification one.
    for verb in ("invite", "accept", "update_access", "remove_access"):
        assert callable(getattr(roles.AccessService, verb)), verb


def test_the_collections_are_unchanged_by_the_module_rename():
    """``room_invitation`` and ``room_access`` are data, not code.

    Renaming the module must not rename the collections: other features and any
    stored data refer to them, and this port has no migration to offer.
    """
    assert INVITATIONS == "room_invitation"
    assert ACCESS == "room_access"


# -- error mapping ---------------------------------------------------------------- #


@pytest.mark.parametrize(
    "call, expected",
    [
        pytest.param(
            lambda c, room: c.post(
                f"{PREFIX}/rooms/{room}/invitations",
                params={"actor": "stranger@example.com"},
                json={"emails": ["a@example.com"]},
            ),
            403,
            id="denied",
        ),
        pytest.param(
            lambda c, room: c.post(
                f"{PREFIX}/rooms/{room}/invitations",
                params={"actor": "dana"},
                json={"emails": ["not-an-email"]},
            ),
            400,
            id="invalid",
        ),
        pytest.param(
            lambda c, room: c.get(f"{PREFIX}/rooms/room_missing/access"),
            404,
            id="missing",
        ),
    ],
)
def test_a_domain_refusal_becomes_its_own_status_code(client, call, expected):
    """The handler the host attaches, not one written into ``dsr/api.py``.

    The status travels on the exception, so a client can tell "you may not" from
    "that is not valid" from "that is gone". If these stop being distinct the
    handler stopped being registered.
    """
    room = make_room(client)
    response = call(client, room)
    assert response.status_code == expected
    assert response.json()["error"].endswith(("Denied", "Invalid", "Missing"))


def test_a_destructive_change_needs_confirmation_and_gets_428(client):
    """The one place this implementation knowingly diverges from the vendor.

    The research records that the mirrored product makes a role change or a
    removal "without asking for confirmation". That is a safety gap, so this
    implementation asks, and asks with a status code a client can branch on.
    """
    room = make_room(client)
    row = grant(client, room, "a@example.com")

    response = client.patch(
        f"{PREFIX}/access/{row['id']}",
        params={"actor": "dana"},
        json={"role": "content_contributor"},
    )

    assert response.status_code == 428
    assert "needs confirmation" in response.json()["detail"]


def test_the_handler_maps_the_base_class_only():
    """One entry, so a second feature mapping a subclass cannot lose to load order."""
    module = load_feature(MODULE)
    assert list(module.EXCEPTION_HANDLERS) == [AccessError]


# -- audit sources ---------------------------------------------------------------- #


def test_every_write_audits_the_path_this_router_serves(client):
    """Hard rule 4 of the port brief, as a test.

    The audit row must name the route that actually served the write. The branch
    recorded ``POST /api/rooms/{id}/invitations`` and ``PATCH /api/access/{id}``,
    which name nothing anyone can call any more; if the prefix and the recorded
    source ever drift apart, this fails.
    """
    room = make_room(client)
    invited = client.post(
        f"{PREFIX}/rooms/{room}/invitations",
        params={"actor": "dana"},
        json={"emails": ["a@example.com"], "role": "content_contributor"},
    )
    invitation = client.get(
        "/api/records/room_invitation", params={"room_id": room}
    ).json()["records"][0]

    client.post(f"{PREFIX}/invitations/{invitation['id']}/accept")
    row = grant(client, room, "b@example.com")
    client.patch(
        f"{PREFIX}/access/{row['id']}",
        params={"actor": "dana", "confirm": True},
        json={"role": "viewer"},
    )
    client.delete(f"{PREFIX}/access/{row['id']}", params={"actor": "dana", "confirm": True})

    sources = {entry["source"] for entry in audit(client) if PREFIX in (entry["source"] or "")}
    assert sources == {
        f"POST {PREFIX}/rooms/{room}/invitations",
        f"POST {PREFIX}/invitations/{invitation['id']}/accept",
        f"PATCH {PREFIX}/access/{row['id']}",
        f"DELETE {PREFIX}/access/{row['id']}",
    }
    # The branch's paths are not merely absent, they are the specific strings the
    # branch used, so this cannot pass by the feature being silent.
    assert invited.status_code == 201
    for dead in ("/api/access/", "/api/rooms/", "/api/invitations/"):
        assert not any(dead in source for source in sources)


def test_reads_write_nothing(client):
    """The snapshot is a read. The seven-day banner is a read-time count.

    Nothing in the expiry path is allowed to persist: the documented cut-off is
    silent, so a sweep would have to either mutate rows or leave them anyway.
    """
    room = make_room(client)
    soon = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")
    grant(client, room, "a@example.com", access_valid_until=soon)

    before = client.get("/api/audit").json()["count"]
    for _ in range(3):
        client.get(f"{PREFIX}/rooms/{room}/access", params={"actor": "dana"})

    assert client.get("/api/audit").json()["count"] == before
    assert client.get(f"{PREFIX}/rooms/{room}/access", params={"actor": "dana"}).json()[
        "banner"
    ] == "1 user has access expiring within 7 days."


def test_a_lapsed_grant_is_filtered_out_and_left_in_storage(client):
    """Access ends at the end of the expiration date in UTC, silently.

    Past that instant the person is simply no longer in *Who Has Access*. The row
    stays, so the audit trail and the invitation that produced it remain
    readable - which is why this is a read-time filter and not a delete.
    """
    room = make_room(client)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    row = grant(client, room, "a@example.com", access_valid_until=yesterday)

    body = client.get(f"{PREFIX}/rooms/{room}/access", params={"actor": "dana"}).json()

    assert body["members"] == []
    assert client.get(f"/api/records/room_access/{row['id']}").status_code == 200


# -- schema flexibility ------------------------------------------------------------ #


def test_a_team_can_add_its_own_field_to_a_grant_over_http(client):
    """No migration, no typed column, no code change in this feature."""
    room = make_room(client)
    row = client.post(
        "/api/records/room_access",
        params={"room_id": room},
        json={"principal": "a@example.com", "role": "viewer", "cost_centre": "CC-4417"},
    ).json()

    assert row["data"]["cost_centre"] == "CC-4417"

    hits = client.get(
        "/api/records/room_access", params={"where": '{"cost_centre":"CC-4417"}'}
    ).json()
    assert [record["id"] for record in hits["records"]] == [row["id"]]


# -- the frontend half -------------------------------------------------------------- #


def test_frontend_descriptor_id_matches_the_backend_feature_id():
    """The two halves of a feature are findable by one name, so they must agree."""
    descriptor = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / "wf-004-invite-buyer"
        / "index.jsx"
    )
    text = descriptor.read_text(encoding="utf-8")
    module = load_feature(MODULE)

    assert descriptor.exists()
    assert f'id: {module.FEATURE["id"]!r}' in text
    # The shared nav file must not have learned this feature's name.
    assert "wf-004-invite-buyer" not in (
        Path(__file__).resolve().parents[2] / "frontend" / "src" / "App.jsx"
    ).read_text(encoding="utf-8")


def test_the_frontend_prefix_matches_the_router_prefix():
    """The one string both halves write down, asserted equal rather than hoped for."""
    wrapper = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / "wf-004-invite-buyer"
        / "api.js"
    ).read_text(encoding="utf-8")

    # api.js writes the path without the `/api` that apiRequest prepends.
    assert f"PREFIX = '{PREFIX[len('/api'):]}'" in wrapper

    import dsr.roles_api

    assert dsr.roles_api.router.prefix == PREFIX


def test_the_frontend_does_not_reach_for_a_shared_api_method():
    """``apiRequest``, not a method appended to the shared ``api`` object."""
    folder = Path(__file__).resolve().parents[2] / "frontend" / "src" / "features" / "wf-004-invite-buyer"
    for module in folder.glob("*.js*"):
        text = module.read_text(encoding="utf-8")
        assert "from '@/lib/api'" in text or "apiRequest" not in text
        # No relative import may climb out of the feature folder.
        assert "'../" not in text and 'from "../' not in text, module.name
        assert "'../../" not in text and 'from "../../' not in text, module.name


# -- demo data ------------------------------------------------------------------------ #


def test_seed_leaves_a_readable_share_dialog():
    """The feature's own ``seed(db, context)``, which replaced its seed.py edit.

    A feature whose page is empty in the demo is a feature nobody can review. This
    one has four different ways to be empty, so the hook has to cover all of them:
    a grant that is inside the seven-day warning window, one safely dated, one
    with no date at all so *No Expiration* renders, and one invitation still in
    flight so the 48-hour window is on screen.
    """
    module = load_feature(MODULE)

    tmp = tempfile.TemporaryDirectory()
    try:
        db = AuditedDatabase(str(Path(tmp.name) / "seeded.db"), actor="seed")
        try:
            accounts = ("Northwind Traders", "Contoso Health", "Fabrikam Logistics", "Adventure Works")
            rooms = [
                db.create("room", {"name": name, "owner": "dana"}, actor="dana", source="seed")
                for name in accounts
            ]

            summary = module.seed(
                db,
                {
                    "room_ids": [(room["id"], account) for room, account in zip(rooms, accounts)],
                    "now": NOW,
                    "rng": random.Random(MODULE),
                },
            )

            assert "7 access grants" in summary
            assert "1 pending invitation" in summary

            # Read back through the same domain object the API uses, with the
            # clock the seed used. Without the injected clock the assertions
            # below would depend on what day the test happens to run, which is
            # how a seeded demo quietly goes stale.
            service = AccessService(db, clock=lambda: NOW)

            # The seven-day banner has something to count, and says so.
            first = service.snapshot(rooms[0]["id"], "dana")
            assert first["expiring_soon_count"] == 1
            assert first["banner"] == "1 user has access expiring within 7 days."

            # Every expiry outcome is on screen at once, so the *No Expiration*
            # row the research documents is visible without any interaction:
            # one dated inside the warning window, one safely dated, one undated.
            expiries = {member["access_valid_until"] for member in first["members"]}
            assert len(expiries) == 3
            assert None in expiries
            # The UTC cut-off instant is derived on read, never stored, so there
            # is no second copy to drift when someone edits the date.
            dated = [m for m in first["members"] if m["access_valid_until"]]
            assert dated and all(m["expires_at_utc"].endswith("Z") for m in dated)

            # A pending invitation is waiting, with the documented 48 hours left.
            pending = first["pending_invitations"]
            assert [item["email"] for item in pending] == ["new.buyer@northwind.example"]
            assert pending[0]["hours_until_expiry"] == 48.0
            assert pending[0]["expired"] is False

            # The owner is the owner: the demo row must not lock them out of the
            # very dialog the seed exists to make reviewable.
            assert first["actor"]["can_share"] is True

            # A grant seeded inside the window is labelled, not just counted.
            flagged = [m for m in first["members"] if m["expiring_soon"]]
            assert flagged and flagged[0]["days_until_expiry"] == pytest.approx(3.5, abs=0.2)

            # Every seeded row went through the audited database.
            collections = {entry["collection"] for entry in db.audit()}
            assert {ACCESS, INVITATIONS} <= collections
        finally:
            db.close()
    finally:
        tmp.cleanup()


def test_seed_derives_its_dates_from_now():
    """Re-running the seed a year later must still produce a lapsing grant.

    Hard-coded dates would make the seven-day banner quietly empty as soon as the
    code aged, which is the failure mode a demo that nobody reviews never
    notices.
    """
    module = load_feature(MODULE)
    much_later = datetime(2027, 6, 1, 9, 0, tzinfo=timezone.utc)

    tmp = tempfile.TemporaryDirectory()
    try:
        db = AuditedDatabase(str(Path(tmp.name) / "late.db"), actor="seed")
        try:
            room = db.create("room", {"name": "Northwind", "owner": "dana"}, actor="dana", source="seed")
            module.seed(db, {"room_ids": [(room["id"], "Northwind Traders")], "now": much_later, "rng": random.Random(1)})

            snapshot = AccessService(db, clock=lambda: much_later).snapshot(room["id"], "dana")
            assert snapshot["banner"] is not None
            assert snapshot["expiring_soon_count"] >= 1
        finally:
            db.close()
    finally:
        tmp.cleanup()


def test_seed_reports_when_there_is_nothing_to_attach_to():
    module = load_feature(MODULE)
    tmp = tempfile.TemporaryDirectory()
    try:
        db = AuditedDatabase(str(Path(tmp.name) / "empty.db"), actor="seed")
        try:
            assert module.seed(
                db, {"room_ids": [], "now": NOW, "rng": random.Random("x")}
            )
        finally:
            db.close()
    finally:
        tmp.cleanup()
