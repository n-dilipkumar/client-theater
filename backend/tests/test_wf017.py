"""Tests for WF-017, white-label rooms on a custom domain, through the API.

Ported from ``backend/tests/test_white_label_api.py`` on
``feature/WF-017-white-label-rooms-on-a-custom-domain`` and re-pointed at this
feature's own prefix. The pure rules are covered separately and without a
database in ``tests/test_domains.py``; this file covers the HTTP surface, the
audit trail, and the three properties the feature actually promises:

* every write lands in the audit log **naming the route that served it**, because
  the whole point of the store is that it cannot be bypassed and the bug this
  guards has already shipped once in this project;
* the link secret is non-removable, and a domain cannot be stolen;
* changing or releasing a domain does not break a link already shared.

One test on the branch is *not* carried over as written. On the branch the
non-removable-secret guarantee was implemented by editing ``dsr/api.py``,
which a feature must not edit, so the port shipped the assertion as a strict
xffail instead. The integrator has since wired ``guard_room_payload`` into
``update_record``, and the marker is gone -- see the comment on that test.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import dsr.features as host
import pytest
from dsr.api import app
from dsr.features import load_feature
from fastapi.testclient import TestClient

#: The host's own loader is the supported way to reach a feature module, and
#: using it here is also what proves discovery works: this module is never named
#: in `dsr/api.py`, so the registry finding it is the whole registration story.
MODULE = "wf_017_white_label"
feature = load_feature(MODULE)
PREFIX = feature.router.prefix

CNAME_TARGET = "cname.dsr.test"
BASE_URL = "http://127.0.0.1:8000"

# A domain whose CNAME points at us, one that points somewhere else, and one
# that has not propagated at all.
FIXTURES = {
    "proposals.acme.com": [CNAME_TARGET],
    "northwind.acme.com": [CNAME_TARGET],
    "wrong.acme.com": ["somewhere.else.test"],
    "notpropagated.acme.com": ["198.51.100.10"],
}


def _wl(path: str) -> str:
    """A path on this feature's own prefix.

    Every route in this file goes through here rather than being spelled out, so
    renaming the prefix is one edit and no test can quietly keep calling the
    branch's ``/api/white-label/...`` paths -- which the port no longer serves
    and which would 404 into the SPA catch-all rather than fail loudly.
    """
    return f"{PREFIX}{path}"


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "api.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setenv("DSR_CNAME_TARGET", CNAME_TARGET)
    monkeypatch.setenv("DSR_PUBLIC_BASE_URL", BASE_URL)
    # Inline resolver fixtures let the whole DNS flow run offline and
    # deterministically, which is why verification is behind a resolver seam.
    monkeypatch.setenv("DSR_CNAME_FIXTURES", json.dumps(FIXTURES))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


@pytest.fixture()
def room(client):
    return client.post(
        "/api/records/room", json={"name": "Proposal Name", "account": "Acme"}
    ).json()


def _propagate(monkeypatch, host_name, values):
    """Simulate a CNAME finishing propagation on the live resolver.

    The resolver is memoised on the environment it was built from rather than on
    first call, precisely so this is possible: changing ``DSR_CNAME_FIXTURES``
    changes the key, so the next request gets a resolver with the new view. That
    is the honest way to model "time passed and DNS changed" without the service
    being held on ``app.state``, which the branch needed and a feature may not do.
    """
    updated = dict(FIXTURES)
    updated[host_name] = list(values)
    monkeypatch.setenv("DSR_CNAME_FIXTURES", json.dumps(updated))


def _mounted_sources(*, room_id: str) -> set[str]:
    """Every ``"<METHOD> <path>"`` the running app serves, as an audit row would
    write it: path parameters replaced by concrete values.

    Two sources, because this version of FastAPI keeps an included router as a
    lazy wrapper in ``app.routes`` rather than flattening it, so the concrete
    paths of a feature are not enumerable from the app object:

    * the core routes, which are real ``APIRoute`` objects and *are* enumerable;
    * every mounted feature, read from the registry -- which is the same view the
      host's own collision check uses, so "mounted" means here exactly what it
      means to the loader.

    An audit row naming nothing in this set is naming a path no caller can reach,
    which is the defect this assertion exists to catch.
    """
    substitutions = {"{room_id}": room_id, "{collection}": "room", "{record_id}": room_id}

    def concretise(path: str) -> str:
        for placeholder, value in substitutions.items():
            path = path.replace(placeholder, value)
        return path

    sources: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue  # a lazily-included router, or a mounted StaticFiles
        for method in methods:
            if method not in ("HEAD", "OPTIONS"):
                sources.add(f"{method} {concretise(path)}")

    for feature_record in host.REGISTRY.features:
        for route in feature_record.routes:
            for method in route["methods"]:
                sources.add(f"{method} {concretise(route['path'])}")

    return sources


# --------------------------------------------------------------------------- #
# Registration: the feature is mounted by discovery, with its own prefix
# --------------------------------------------------------------------------- #


def test_the_feature_module_is_named_the_way_the_contract_names_it():
    """``docs/FEATURE-CONTRACT.md`` says ``<ticket>_<slug>.py``.

    Worth a test because the host will happily load a hyphenated file name --
    ``importlib.import_module`` takes a string, so a name like
    ``wf_017-white-label.py`` mounts its router and serves every route while
    being unreachable by any ordinary import statement, since a hyphen is not an
    identifier character. The failure is invisible until a reader tries to
    follow a reference or a tool tries to import it, so the convention is pinned
    here instead of left to a reviewer's eye.
    """
    name = Path(feature.__file__).name

    assert name == "wf_017_white_label.py"
    assert name.replace("_", "").replace(".py", "").isalnum(), (
        f"{name} contains a character that cannot appear in an import statement"
    )


def test_the_feature_is_registered_under_its_own_id_and_prefix():
    record = host.REGISTRY.by_id("wf-017-white-label")

    assert record is not None, "the host did not mount the feature"
    assert record.loaded is True
    assert record.prefix == PREFIX == "/api/wf-017-white-label"
    assert record.ticket == "WF-017"


def test_the_feature_owns_all_ten_of_its_routes():
    record = host.REGISTRY.by_id("wf-017-white-label")
    served = {(method, route["path"]) for route in record.routes for method in route["methods"]}

    assert served == {
        ("GET", f"{PREFIX}/config"),
        ("GET", f"{PREFIX}/rooms/{{room_id}}/white-label"),
        ("POST", f"{PREFIX}/rooms/{{room_id}}/white-label/link-secret"),
        ("POST", f"{PREFIX}/verify"),
        ("POST", f"{PREFIX}/rooms/{{room_id}}/white-label/domain"),
        ("DELETE", f"{PREFIX}/rooms/{{room_id}}/white-label/domain"),
        ("POST", f"{PREFIX}/rooms/{{room_id}}/white-label/recheck"),
        ("PATCH", f"{PREFIX}/rooms/{{room_id}}/white-label/branding"),
        ("GET", f"{PREFIX}/links/{{secret}}"),
        ("GET", f"{PREFIX}/resolve"),
    }


def test_the_prefix_is_ours_and_nobody_elses():
    """A feature that re-uses core vocabulary is a collision or dead code."""
    record = host.REGISTRY.by_id("wf-017-white-label")

    for route in record.routes:
        assert route["path"].startswith(PREFIX), route["path"]
        assert not route["path"].startswith("/api/rooms/"), route["path"]
        assert not route["path"].startswith("/api/white-label"), route["path"]


def test_the_feature_maps_its_own_two_error_types():
    """Both handlers are for types this workflow owns, so neither can intercept
    an exception raised anywhere else in the product."""
    record = host.REGISTRY.by_id("wf-017-white-label")

    assert record.exception_handlers == ["DomainError", "HostNotServed"]


def test_the_feature_never_imported_the_shared_app():
    source = Path(feature.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in source and "import dsr.api" not in source


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def test_config_surfaces_the_deployment_cname_target(client):
    body = client.get(_wl("/config")).json()

    assert body["cname_target"] == CNAME_TARGET
    assert body["record_type"] == "CNAME"
    assert body["base_url"] == BASE_URL


def test_config_carries_the_propagation_and_cloudflare_caveats(client):
    """Both are sourced: 24-hour propagation, and Cloudflare proxying off."""
    body = client.get(_wl("/config")).json()

    assert "24 hours" in body["propagation_note"]
    assert "default-host" in body["propagation_note"]
    assert "DNS only" in body["cloudflare_note"]
    assert "subdomain format" in body["format_note"]


def test_the_cname_target_defaults_to_something_that_can_never_resolve(client, monkeypatch):
    """The placeholder is RFC 2606 ``.invalid``; shipping a real hostname here
    would point real customers' DNS at a host nobody controls."""
    monkeypatch.delenv("DSR_CNAME_TARGET", raising=False)

    assert client.get(_wl("/config")).json()["cname_target"].endswith(".invalid")


# --------------------------------------------------------------------------- #
# Link secrets
# --------------------------------------------------------------------------- #


def test_link_secret_is_minted_once_and_reused(room, client):
    first = client.post(_wl(f"/rooms/{room['id']}/white-label/link-secret")).json()
    second = client.post(_wl(f"/rooms/{room['id']}/white-label/link-secret")).json()

    assert first["has_link_secret"] is True
    assert first["slug"] == second["slug"]
    assert first["share_url"] == second["share_url"]


def test_share_url_uses_the_default_host_before_a_domain_is_set(room, client):
    """Sourced: default links can be shared during propagation and keep working."""
    body = client.post(_wl(f"/rooms/{room['id']}/white-label/link-secret")).json()
    secret = body["slug"].rsplit("-", 1)[1]

    assert body["share_url"] == f"{BASE_URL}/r/Proposal-Name-{secret}"
    assert body["domain"] is None


def test_share_url_is_stable_when_the_domain_changes(room, client):
    """The whole point of `secret_is_identity`: re-pointing breaks nothing.

    This is the hazard the research attributes to the vendor -- "all shared
    links would need to be reshared otherwise the links will appear broken".
    Because the secret is the identity, only the host moves.
    """
    client.post(_wl(f"/rooms/{room['id']}/white-label/link-secret"))
    before = client.get(_wl(f"/rooms/{room['id']}/white-label")).json()

    client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    )
    after = client.get(_wl(f"/rooms/{room['id']}/white-label")).json()

    assert after["slug"] == before["slug"]
    assert after["default_host_share_url"] == before["share_url"]
    assert after["share_url"] == f"https://proposals.acme.com{before['path']}"


def test_guard_room_payload_drops_the_product_owned_fields():
    """The guard ``api.py`` calls to close the gap below.

    Asserted directly against the exported function as well as through the
    route, because the two failures are different: this one says the guard
    stopped working, and the route one says the host stopped calling it.
    """
    patch = {
        "name": "Renamed",
        "domain": "evil.acme.com",
        "link_secret": None,
        "collaborator_token": "x",
    }

    assert feature.guard_room_payload(patch) == {"name": "Renamed"}


def test_the_generic_record_route_cannot_clear_the_link_secret(room, client):
    """Sourced: the secret is a "non-removable identifier".

    Named for the requirement, not for today's behaviour, so a reader scanning
    test names sees what must become true rather than what is currently true.
    The body asserts the secret *survives* a patch that tries to clear it.

    The branch closed this by editing ``update_record`` in ``dsr/api.py`` to run
    every room patch through ``DomainService.strip_reserved``. That file is
    shared, so the port could not carry the closure. The guard is now wired in
    by the integrator, which is why this test no longer carries the
    ``xfail(strict=True)`` marker it held while the gap was open.
    """
    client.post(_wl(f"/rooms/{room['id']}/white-label/link-secret"))
    secret = client.get(_wl(f"/rooms/{room['id']}/white-label")).json()["slug"].rsplit("-", 1)[1]

    client.patch(f"/api/records/room/{room['id']}", json={"link_secret": None, "name": "Renamed"})

    stored = client.get(f"/api/records/room/{room['id']}").json()["data"]
    assert stored["link_secret"] == secret
    # The rest of the patch is honoured; only the reserved field is dropped.
    assert stored["name"] == "Renamed"


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #


def test_verification_passes_for_a_propagated_cname(client):
    body = client.post(_wl("/verify"), json={"domain": "proposals.acme.com"}).json()

    assert body["ready"] is True
    assert body["status"] == "verified"
    assert body["observed"] == [CNAME_TARGET]
    assert {check["name"] for check in body["checks"]} == {"cname", "format", "available"}


def test_verification_fails_and_reports_what_it_saw_for_a_wrong_cname(client):
    body = client.post(_wl("/verify"), json={"domain": "wrong.acme.com"}).json()

    assert body["ready"] is False
    cname = next(check for check in body["checks"] if check["name"] == "cname")
    assert cname["ok"] is False
    # The operator has to be able to see what DNS actually returned.
    assert "somewhere.else.test" in cname["detail"]


def test_verification_fails_for_an_unpropagated_cname(client):
    body = client.post(_wl("/verify"), json={"domain": "notpropagated.acme.com"}).json()

    assert body["ready"] is False


def test_verification_rejects_a_bare_registrable_domain(client):
    response = client.post(_wl("/verify"), json={"domain": "acme.com"})

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_domain"
    assert "subdomain format" in response.json()["detail"]


def test_verification_requires_a_domain(client):
    assert client.post(_wl("/verify"), json={}).status_code == 400


def test_verification_normalises_pasted_input(client):
    body = client.post(
        _wl("/verify"), json={"domain": "https://Proposals.Acme.com/Proposal-Name"}
    ).json()

    assert body["domain"] == "proposals.acme.com"
    assert body["ready"] is True


# --------------------------------------------------------------------------- #
# Claiming
# --------------------------------------------------------------------------- #


def test_claiming_a_verified_domain_switches_the_share_link_host(room, client):
    claimed = client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    ).json()

    assert claimed["domain"] == "proposals.acme.com"
    assert claimed["domain_status"] == "verified"
    assert claimed["share_url"].startswith("https://proposals.acme.com/")


def test_claiming_preserves_the_slug_and_appends_the_mandatory_secret(room, client):
    claimed = client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    ).json()

    assert claimed["slug"] == f"Proposal-Name-{claimed['slug'].rsplit('-', 1)[1]}"
    assert claimed["share_url"].endswith(claimed["slug"])


def test_claiming_mints_the_link_secret_if_the_room_has_none(room, client):
    assert "link_secret" not in client.get(f"/api/records/room/{room['id']}").json()["data"]

    claimed = client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    ).json()

    assert claimed["has_link_secret"] is True
    assert claimed["has_collaborator_token"] is True


def test_claiming_an_unpropagated_domain_is_refused(room, client):
    response = client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "notpropagated.acme.com"}
    )

    assert response.status_code == 409
    assert "not ready" in response.json()["detail"]


def test_force_saves_a_domain_that_has_not_propagated_yet(room, client):
    """The researched flow has a real waiting period; the operator must be able
    to set the binding first and poll afterwards."""
    response = client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"),
        json={"domain": "notpropagated.acme.com"},
        params={"force": True},
    )

    assert response.status_code == 200
    assert response.json()["domain_status"] == "unverified"
    # An unverified domain must never appear in a share link.
    assert response.json()["share_url"].startswith(BASE_URL)


def test_force_cannot_hand_one_customers_domain_to_another(client, room):
    other = client.post("/api/records/room", json={"name": "Second"}).json()
    client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    )

    response = client.post(
        _wl(f"/rooms/{other['id']}/white-label/domain"),
        json={"domain": "proposals.acme.com"},
        params={"force": True},
    )

    assert response.status_code == 409
    assert "already in use" in response.json()["detail"]


def test_a_domain_already_used_by_another_room_is_refused(client, room):
    other = client.post("/api/records/room", json={"name": "Second"}).json()
    client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    )

    response = client.post(
        _wl(f"/rooms/{other['id']}/white-label/domain"),
        json={"domain": "proposals.acme.com"},
        params={"force": True},
    )

    assert response.status_code == 409
    assert "already in use" in response.json()["detail"]


def test_verification_reports_a_domain_that_is_already_taken(client, room):
    client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    )

    body = client.post(_wl("/verify"), json={"domain": "proposals.acme.com"}).json()

    available = next(check for check in body["checks"] if check["name"] == "available")
    assert available["ok"] is False
    assert room["id"] in available["detail"]


def test_a_room_can_reclaim_its_own_domain(client, room):
    client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    )

    response = client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    )

    assert response.status_code == 200


def test_claiming_a_domain_for_an_unknown_room_is_404(client):
    assert (
        client.post(
            _wl("/rooms/room_nope/white-label/domain"), json={"domain": "proposals.acme.com"}
        ).status_code
        == 404
    )


def test_claiming_requires_a_domain(client, room):
    assert client.post(_wl(f"/rooms/{room['id']}/white-label/domain"), json={}).status_code == 400


# --------------------------------------------------------------------------- #
# Changing and releasing
# --------------------------------------------------------------------------- #


def test_changing_the_domain_records_history_and_keeps_the_secret(room, client):
    client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    )
    client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "northwind.acme.com"}
    )

    data = client.get(f"/api/records/room/{room['id']}").json()["data"]
    assert data["domain"] == "northwind.acme.com"
    assert [entry["domain"] for entry in data["domain_history"]] == ["proposals.acme.com"]


def test_releasing_the_domain_returns_to_the_default_host_without_breaking_links(room, client):
    client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    )
    before = client.get(_wl(f"/rooms/{room['id']}/white-label")).json()

    after = client.delete(_wl(f"/rooms/{room['id']}/white-label/domain")).json()

    assert after["domain"] is None
    assert after["domain_status"] == "unverified"
    assert after["slug"] == before["slug"]
    assert after["share_url"] == before["default_host_share_url"]


def test_a_released_domain_can_be_claimed_by_another_room(client, room):
    other = client.post("/api/records/room", json={"name": "Second"}).json()
    client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    )
    client.delete(_wl(f"/rooms/{room['id']}/white-label/domain"))

    response = client.post(
        _wl(f"/rooms/{other['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    )

    assert response.status_code == 200


def test_releasing_when_there_is_no_domain_is_422(room, client):
    response = client.delete(_wl(f"/rooms/{room['id']}/white-label/domain"))

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_domain"


def test_recheck_promotes_an_unverified_domain_once_it_propagates(client, room, monkeypatch):
    client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"),
        json={"domain": "notpropagated.acme.com"},
        params={"force": True},
    )
    assert (
        client.get(_wl(f"/rooms/{room['id']}/white-label")).json()["domain_status"] == "unverified"
    )

    _propagate(monkeypatch, "notpropagated.acme.com", [CNAME_TARGET])
    rechecked = client.post(_wl(f"/rooms/{room['id']}/white-label/recheck")).json()

    # Re-checking alone must be enough to promote the room, so an operator can
    # poll through the researched propagation wait instead of retyping anything.
    assert rechecked["domain_status"] == "verified"
    assert rechecked["share_url"].startswith("https://notpropagated.acme.com/")
    assert rechecked["domain_activated_at"]


def test_recheck_without_a_domain_is_422(room, client):
    assert client.post(_wl(f"/rooms/{room['id']}/white-label/recheck")).status_code == 422


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def test_a_secret_resolves_to_its_room(room, client):
    claimed = client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    ).json()
    secret = claimed["slug"].rsplit("-", 1)[1]

    resolved = client.get(_wl(f"/links/{secret}"), params={"host": "proposals.acme.com"}).json()

    assert resolved["room_id"] == room["id"]
    assert resolved["name"] == "Proposal Name"
    assert resolved["served_on_custom_domain"] is True


def test_the_same_secret_resolves_on_the_default_host(room, client):
    """Sourced: default-host links "will continue working" after setup."""
    claimed = client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    ).json()
    secret = claimed["slug"].rsplit("-", 1)[1]

    resolved = client.get(_wl(f"/links/{secret}"), params={"host": "127.0.0.1:8000"}).json()

    assert resolved["room_id"] == room["id"]
    assert resolved["served_on_custom_domain"] is False


def test_a_secret_resolves_on_a_host_we_do_not_serve_with_a_404(room, client):
    """Routing, not identity: the secret is real, the host is not ours."""
    claimed = client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    ).json()
    secret = claimed["slug"].rsplit("-", 1)[1]

    response = client.get(_wl(f"/links/{secret}"), params={"host": "evil.example.net"})

    # 404 rather than 421, so the answer does not confirm the secret exists.
    assert response.status_code == 404
    assert response.json()["error"] == "host_not_served"


def test_an_unknown_secret_is_404(client):
    assert client.get(_wl("/links/zzzzzzzzzz")).status_code == 404


def test_a_path_resolves_to_its_secret(room, client):
    claimed = client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    ).json()

    resolved = client.get(
        _wl("/resolve"),
        params={"path": claimed["path"], "host": "proposals.acme.com"},
    ).json()

    assert resolved["room_id"] == room["id"]


def test_a_path_without_a_secret_is_404(client):
    assert client.get(_wl("/resolve"), params={"path": "/r/nothing-here"}).status_code == 404


# --------------------------------------------------------------------------- #
# Branding
# --------------------------------------------------------------------------- #


def test_branding_tokens_round_trip(room, client):
    updated = client.patch(
        _wl(f"/rooms/{room['id']}/white-label/branding"),
        json={
            "primary": "#0f172a",
            "accent": "#22c55e",
            "heading_font": "Fira Code, monospace",
            "body_font": "'Fira Sans', sans-serif",
        },
    ).json()

    branding = updated["branding"]
    assert branding["primary"] == "#0f172a"
    assert branding["heading_font"] == "Fira Code, monospace"


def test_branding_merges_rather_than_replaces(room, client):
    """`branding` is team-owned: a partial write must not delete another field."""
    client.patch(
        _wl(f"/rooms/{room['id']}/white-label/branding"),
        json={"logo_url": "https://cdn.acme.com/logo.svg", "accent": "#22c55e"},
    )
    client.patch(_wl(f"/rooms/{room['id']}/white-label/branding"), json={"accent": "#0ea5e9"})

    branding = client.get(_wl(f"/rooms/{room['id']}/white-label")).json()["branding"]
    assert branding["accent"] == "#0ea5e9"
    assert branding["logo_url"] == "https://cdn.acme.com/logo.svg"


def test_branding_accepts_a_field_it_does_not_know_about(room, client):
    """The schema-flexibility promise: no migration, no coordination."""
    client.patch(
        _wl(f"/rooms/{room['id']}/white-label/branding"),
        json={"email_footer": "Confidential - Acme Ltd", "typekit_id": "abc123"},
    )

    branding = client.get(_wl(f"/rooms/{room['id']}/white-label")).json()["branding"]
    assert branding["email_footer"] == "Confidential - Acme Ltd"
    assert branding["typekit_id"] == "abc123"


@pytest.mark.parametrize(
    "token",
    [
        "url(https://evil.example.net/beacon.png)",
        "red; background-image: url(https://evil.example.net/x)",
        "expression(alert(1))",
    ],
)
def test_a_colour_that_could_inject_css_is_refused(room, client, token):
    response = client.patch(
        _wl(f"/rooms/{room['id']}/white-label/branding"), json={"accent": token}
    )

    assert response.status_code == 422


def test_a_font_stack_that_could_escape_its_property_is_refused(room, client):
    response = client.patch(
        _wl(f"/rooms/{room['id']}/white-label/branding"),
        json={"body_font": "@import url(https://evil.example.net/x.css)"},
    )

    assert response.status_code == 422


def test_a_rejected_brand_token_changes_nothing(room, client):
    client.patch(_wl(f"/rooms/{room['id']}/white-label/branding"), json={"accent": "#22c55e"})

    client.patch(
        _wl(f"/rooms/{room['id']}/white-label/branding"), json={"accent": "url(https://x.test)"}
    )
    client.patch(_wl(f"/rooms/{room['id']}/white-label/branding"), json={"primary": "#0ea5e9"})

    branding = client.get(_wl(f"/rooms/{room['id']}/white-label")).json()["branding"]
    assert branding["accent"] == "#22c55e"
    assert branding["primary"] == "#0ea5e9"


# --------------------------------------------------------------------------- #
# Schema flexibility
# --------------------------------------------------------------------------- #


def test_domain_state_lives_in_data_with_no_migration(client, room):
    client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    )

    stored = client.get(f"/api/records/room/{room['id']}").json()
    assert stored["collection"] == "room"
    assert stored["data"]["domain"] == "proposals.acme.com"
    assert stored["data"]["domain_status"] == "verified"


def test_rooms_can_be_grouped_by_domain_through_the_dynamic_index(client, room):
    """`domain` is not a declared column anywhere; `find` still resolves it."""
    client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    )

    found = client.get(
        "/api/records/room", params={"where": json.dumps({"domain": "proposals.acme.com"})}
    ).json()

    assert [r["id"] for r in found["records"]] == [room["id"]]


def test_domains_appear_in_the_schema_discovery_endpoint(client, room):
    client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    )
    # A second, different domain so the change history exists to discover too.
    client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "northwind.acme.com"}
    )

    body = client.get("/api/collections").json()
    room_fields = {
        f["path"]
        for f in next(c for c in body["collections"] if c["collection"] == "room")["fields"]
    }

    assert {
        "domain",
        "domain_status",
        "link_secret",
        "collaborator_token",
        # A list of retired domains is indexed by position, which is what makes
        # a history queryable without a column.
        "domain_history.0.domain",
    } <= room_fields


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #


def test_every_white_label_mutation_is_audited(client, room):
    client.post(_wl(f"/rooms/{room['id']}/white-label/link-secret"))
    client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    )
    client.patch(_wl(f"/rooms/{room['id']}/white-label/branding"), json={"accent": "#22c55e"})
    client.post(_wl(f"/rooms/{room['id']}/white-label/recheck"))
    client.delete(_wl(f"/rooms/{room['id']}/white-label/domain"))

    sources = [
        e["source"] for e in client.get("/api/audit", params={"limit": 50}).json()["entries"]
    ]
    room_id = room["id"]
    assert f"POST {PREFIX}/rooms/{room_id}/white-label/link-secret" in sources
    assert f"POST {PREFIX}/rooms/{room_id}/white-label/domain" in sources
    assert f"PATCH {PREFIX}/rooms/{room_id}/white-label/branding" in sources
    assert f"POST {PREFIX}/rooms/{room_id}/white-label/recheck" in sources
    assert f"DELETE {PREFIX}/rooms/{room_id}/white-label/domain" in sources


def test_an_audit_source_names_a_route_the_app_actually_serves(client, room):
    """Hard rule 4 of the port brief, asserted as an invariant rather than a list.

    The branch hard-coded ``source="claim custom domain {domain} for {room}"``
    inside the service, which kept recording a path the app had stopped serving
    -- the same bug that shipped once already in this project. Rather than
    asserting five literal strings, this collects every (method, path) the app
    actually has mounted, core routes and every feature, and checks each audit
    row against that set. A future rename of this feature's prefix cannot break
    the promise without breaking this.
    """
    client.post(_wl(f"/rooms/{room['id']}/white-label/link-secret"))
    client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    )
    client.patch(_wl(f"/rooms/{room['id']}/white-label/branding"), json={"accent": "#22c55e"})
    client.post(_wl(f"/rooms/{room['id']}/white-label/recheck"))
    client.delete(_wl(f"/rooms/{room['id']}/white-label/domain"))

    mounted = _mounted_sources(room_id=room["id"])
    written = [
        e["source"]
        for e in client.get("/api/audit", params={"record_id": room["id"], "limit": 50}).json()[
            "entries"
        ]
    ]

    assert written, "expected audit rows for the room"
    for source in written:
        assert source in mounted, f"audit row names a route the app does not serve: {source}"


def test_no_audit_row_records_a_path_from_the_branch_we_did_not_merge(client, room):
    """The concrete regression this port exists to prevent, named explicitly.

    The branch served ``/api/white-label/*`` and ``/api/rooms/{id}/white-label*``
    and recorded ``mint link secret for {room_id}``. Neither path is mounted
    here, so neither string may appear in an audit row.
    """
    client.post(_wl(f"/rooms/{room['id']}/white-label/link-secret"))
    client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    )

    sources = [
        e["source"] for e in client.get("/api/audit", params={"limit": 50}).json()["entries"]
    ]

    for banned in (
        "mint link secret",
        "claim custom domain",
        "update branding for",
        "/api/white-label",
    ):
        assert not any(banned in source for source in sources), banned
    assert not any(
        source.startswith(f"POST /api/rooms/{room['id']}/white-label") for source in sources
    )


def test_the_claim_is_audited_with_before_and_after_state(client, room):
    client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "proposals.acme.com"}
    )

    entry = client.get(
        "/api/audit", params={"record_id": room["id"], "action": "update", "limit": 50}
    ).json()["entries"][0]

    assert entry["before_state"].get("domain") is None
    assert entry["after_state"]["domain"] == "proposals.acme.com"
    assert entry["diff"]["domain"] == {"from": None, "to": "proposals.acme.com"}


def test_reads_and_verification_do_not_appear_in_the_audit_log(client, room):
    client.post(_wl("/verify"), json={"domain": "proposals.acme.com"})
    client.get(_wl(f"/rooms/{room['id']}/white-label"))
    client.get(_wl("/config"))

    sources = [
        e["source"] for e in client.get("/api/audit", params={"limit": 50}).json()["entries"]
    ]
    # Only the room creation from the fixture. A verification is a read: it
    # looks at DNS and reports, and must not manufacture an audit row.
    assert sources == ["POST /api/records/room"]


def test_a_failed_claim_writes_nothing(client, room):
    client.post(
        _wl(f"/rooms/{room['id']}/white-label/domain"), json={"domain": "notpropagated.acme.com"}
    )

    data = client.get(f"/api/records/room/{room['id']}").json()["data"]
    assert data.get("domain") is None
    # Only the room creation itself is in the log.
    assert client.get("/api/audit").json()["count"] == 1


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def test_seed_gives_every_room_a_readable_white_label_state(tmp_path, monkeypatch):
    """A feature whose page is empty in the demo is a feature nobody can review."""
    from datetime import datetime, timezone

    from dsr.db.audited import AuditedDatabase

    monkeypatch.setenv("DSR_CNAME_TARGET", CNAME_TARGET)
    monkeypatch.setenv("DSR_PUBLIC_BASE_URL", BASE_URL)
    monkeypatch.setenv("DSR_DB_PATH", str(tmp_path / "seed.db"))

    db = AuditedDatabase(tmp_path / "seed.db", mirror_dir=str(tmp_path / "audit"), actor="test")
    try:
        room_ids = [
            (db.create("room", {"name": f"Room {i}"}, actor="test", source="seed")["id"], "Acme")
            for i in range(4)
        ]

        summary = feature.seed(
            db, {"room_ids": room_ids, "now": datetime.now(timezone.utc), "rng": None}
        )

        assert "4 rooms white-labelled" in summary
        service = feature.DomainService(feature.RecordStore(db))
        described = [service.describe(service.get_room(room_id)) for room_id, _ in room_ids]

        # One room live on its own domain, one recorded but mid-propagation, two
        # with no domain at all. `describe` reports "unverified" for a room that
        # has never had a domain, so the states are told apart by `domain`, not
        # by the status string.
        assert [state["domain_status"] for state in described].count("verified") == 1
        assert [state for state in described if state["domain"]][1]["domain_status"] == "unverified"
        assert sum(1 for state in described if not state["domain"]) == 2

        # Every seeded room has a working link on the default host, which is the
        # researched guarantee that a link works before a domain is configured.
        for state in described:
            assert state["default_host_share_url"].startswith(BASE_URL)
            assert state["has_link_secret"] is True
        # And the one room on a custom domain is served on it, with the slug and
        # the secret preserved so the same path works on either host.
        verified = next(state for state in described if state["domain_status"] == "verified")
        assert verified["share_url"] == f"https://proposals.northwind.example{verified['path']}"
        assert verified["path"].endswith(verified["slug"].rsplit("-", 1)[1])

        # And the demo is audited like everything else.
        sources = [e["source"] for e in db.audit(limit=50)]
        assert "seed" in sources
    finally:
        db.close()


def test_the_seed_never_invents_a_route_in_the_audit_log(tmp_path, monkeypatch):
    """A demo write is served by no route, so it must not name one.

    An earlier cut of the seed wrote
    ``POST {prefix}/rooms/{id}/white-label/seed-demo``, which reads convincingly
    and is the exact defect hard rule 4 names: the audit log recorded a path the
    app had never served. Every other feature's ``seed()`` writes ``source="seed"``
    for the same reason.
    """
    from datetime import datetime, timezone

    from dsr.db.audited import AuditedDatabase

    monkeypatch.setenv("DSR_CNAME_TARGET", CNAME_TARGET)
    monkeypatch.setenv("DSR_PUBLIC_BASE_URL", BASE_URL)

    db = AuditedDatabase(tmp_path / "seed.db", mirror_dir=str(tmp_path / "audit"), actor="test")
    try:
        room_ids = [
            (db.create("room", {"name": f"Room {i}"}, actor="test", source="seed")["id"], "Acme")
            for i in range(4)
        ]
        feature.seed(db, {"room_ids": room_ids, "now": datetime.now(timezone.utc), "rng": None})

        mounted = _mounted_sources(room_id=room_ids[0][0])
        # The placeholder pattern is substituted before the f-string is built,
        # not inside it: a backslash inside an f-string expression is a 3.12
        # syntax, and this package still supports 3.11.
        placeholder = re.compile(r"\{[^}]+\}")
        templated = {
            f"{head} {placeholder.sub('*', tail)}"
            for head, _, tail in (source.partition(" ") for source in mounted)
        }

        for source in (e["source"] for e in db.audit(limit=50)):
            concrete = re.sub(r"/room_[0-9a-f]+", "/*", source)
            assert concrete not in templated, f"seed invented a route: {source}"
    finally:
        db.close()


def test_every_seeded_secret_is_recoverable_from_its_own_path(tmp_path, monkeypatch):
    """A seeded share link has to resolve, or the demo mints a dead link.

    ``link_secret_from_path`` returns None for a suffix containing a character
    the secret alphabet excludes -- ``0``, ``1``, ``O``, ``I`` and ``l`` are
    excluded on purpose, because they are the glyphs people mistype when
    re-reading a link from a PDF. An earlier cut of this seed used tokens
    ending ``01``/``02``/``03``/``04`` and every seeded link 404'd for a buyer.

    The failure was invisible to the URL-shape assertions: the link *looked*
    right. It only showed up when a real request went through ``GET /resolve``.
    """
    from datetime import datetime, timezone

    from dsr.db.audited import AuditedDatabase
    from dsr.domains import _SECRET_ALPHABET, link_secret_from_path

    monkeypatch.setenv("DSR_CNAME_TARGET", CNAME_TARGET)
    monkeypatch.setenv("DSR_PUBLIC_BASE_URL", BASE_URL)

    db = AuditedDatabase(tmp_path / "seed.db", mirror_dir=str(tmp_path / "audit"), actor="test")
    try:
        room_ids = [
            (db.create("room", {"name": f"Room {i}"}, actor="test", source="seed")["id"], "Acme")
            for i in range(4)
        ]
        feature.seed(db, {"room_ids": room_ids, "now": datetime.now(timezone.utc), "rng": None})

        service = feature.DomainService(feature.RecordStore(db))
        for (room_id, _), expected in zip(room_ids, feature.SEED_SECRETS, strict=False):
            described = service.describe(service.get_room(room_id))

            assert len(expected) == 10, f"{expected} is not a ten-character secret"
            assert set(expected) <= set(_SECRET_ALPHABET), f"{expected} uses an excluded glyph"
            # The exact claim: the path this room is shared on resolves back to it.
            assert link_secret_from_path(described["path"]) == expected
            assert described["slug"].endswith(f"-{expected}")
    finally:
        db.close()


def test_seed_says_so_rather_than_raising_when_there_are_no_rooms():
    assert "no rooms" in feature.seed(None, {"room_ids": [], "now": None, "rng": None})


def test_seed_never_claims_more_rooms_than_the_demo_has(tmp_path, monkeypatch):
    """One state per room, and the count has to be true.

    Taking ``room_ids[index % len(room_ids)]`` with four states and two demo
    rooms would overwrite the ``verified`` room with ``awaiting_dns`` -- so the
    demo would look complete and would not be, and the seeder would print four
    rooms when it wrote two. That is the failure mode this asserts against.
    """
    from datetime import datetime, timezone

    from dsr.db.audited import AuditedDatabase

    monkeypatch.setenv("DSR_CNAME_TARGET", CNAME_TARGET)
    monkeypatch.setenv("DSR_PUBLIC_BASE_URL", BASE_URL)

    db = AuditedDatabase(tmp_path / "seed.db", mirror_dir=str(tmp_path / "audit"), actor="test")
    try:
        room_ids = [
            (db.create("room", {"name": f"Room {i}"}, actor="test", source="seed")["id"], "Acme")
            for i in range(2)
        ]

        summary = feature.seed(
            db, {"room_ids": room_ids, "now": datetime.now(timezone.utc), "rng": None}
        )

        assert summary.startswith("2 rooms white-labelled")
        # And the two states written are the first two, not the last two.
        assert "2 state(s) skipped" in summary

        service = feature.DomainService(feature.RecordStore(db))
        first, second = (service.describe(service.get_room(room_id)) for room_id, _ in room_ids)
        assert first["domain_status"] == "verified"
        assert first["domain"] == "proposals.northwind.example"
        assert second["domain"] is None
        # The verified room's history names the room it belongs to, not a literal.
        assert first["domain_history"][0]["room_id"] == room_ids[0][0]
    finally:
        db.close()
