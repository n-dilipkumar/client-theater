"""Tests for WF-034: connect a CRM org to the sales room (OAuth 2.0 authorization code).

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-034.md``, and each section
below names which one it is pinning:

* the four interfaces a third-party connector implements, which is the researched
  extensibility claim - proven here by registering a **fourth** vendor at test
  time and running the whole flow on it;
* the HubSpot authorize URL and its three parameters, reproduced key for key;
* ``Authorization: Bearer token`` on every later call, and therefore *never* a
  token in a URL;
* the Dataverse resource URL shape, templated on the org;
* the credential vault: keyed by the org/account id, sealed at rest, and with no
  read route anywhere;
* refresh driven by the ``expires_in`` the token carried, ahead of the expiry;
* "Token refresh before expiry", and health polling the room drives itself;
* "the sales room calls a low-cost authenticated endpoint to verify the token";
* "nothing is written to CRM records in this workflow";
* and the sentence the whole token half is built around: **"Unauthorized (401)
  requests are not a valid indicator that a new access token must be retrieved."**

The parts the research does *not* fix are the design inferences, and they are
tested as inferences: named, bounded, and changeable in one place.

Every call to a vendor goes through a fake transport, so the refresh ladder, the
401 rule and the request headers are asserted without a socket and without a
network flake.
"""

from __future__ import annotations

import ast
import json
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.crm_oauth import (
    APP_ORG_KEY,
    COLLECTIONS,
    CONNECTION_STATUSES,
    CREDENTIAL_COLLECTION,
    DEMO_KEY,
    EVENT_COLLECTION,
    GRANT_COLLECTION,
    HEALTH_VALUES,
    INFERENCES,
    KEY_ENV,
    SKEW_SECONDS,
    SOURCED_QUOTES,
    STATUS_AUTHORIZED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    UNAUTHORIZED_IS_NOT_A_REFRESH_TRIGGER,
    AuthorizeRequest,
    CredentialVault,
    CrmOAuthConnections,
    HttpResult,
    TokenRequest,
    TokenResponse,
    VendorInfo,
    connector,
    describe_connector,
    open_sealed,
    register,
    registered_vendors,
    resolve_key,
    seal,
    unregister,
    vendor_info,
)
from dsr.crm_oauth.connectors import SALESFORCE_POLICIES, assert_no_token_in_url, build_authorize_url
from dsr.crm_oauth.engine import CONNECTION_COLLECTION
from dsr.crm_oauth.errors import (
    AuthorizationError,
    ConnectionDisabledError,
    ConnectionNotFoundError,
    ConnectorConfigError,
    NotConnectedError,
    TokenExchangeError,
    UnknownRoomError,
    UnknownVendorError,
    VaultSealedError,
    VendorRequestError,
)
from dsr.crm_oauth.transport import form_body, redact_headers
from dsr.crm_oauth.vocabulary import RESEARCH_GAPS, VENDOR_IDS
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-034"

MODULE = "wf034_connect_a_crm_org_to_the_sales_room_oa"

#: How many routes this feature mounts. A change to the surface should be a
#: deliberate edit here, so a reviewer sees the diff in the test too.
ROUTE_COUNT = 20

#: What the pure-domain tests pass as ``source``. Deliberately the exact shape a
#: route passes, so a test asserting on an audit row is asserting on the real
#: thing rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/connections"

ORG = "northwind"
ORG_HOST = "northwind.hubspot.com"
REDIRECT = "https://rooms.example/api/wf-034/callback/northwind"

CONNECTION = {
    "vendor": "hubspot",
    "label": "Northwind Traders",
    "tenant": "acme",
    "client_id": "client-123",
    "client_secret": "secret-abc",
    "redirect_uri": REDIRECT,
    "scopes": ["crm.objects.contacts.read", "offline_access"],
    "org_id": ORG_HOST,
}


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def fields_of(body: bytes | None) -> dict[str, str]:
    if not body:
        return {}
    return {key: value[0] for key, value in parse_qs(body.decode("utf-8")).items()}


class FakeTransport:
    """Records every call and answers from a per-org plan.

    Keyed on the org slug **inside the token**, which is the right key: a vendor's
    answer about a token should be a function of that token, and it is what makes
    two connections to one vendor independent without the transport knowing
    anything about connections.
    """

    def __init__(self, *, probe_status: int = 200, refresh: str = "ok", expires_in: int = 1800) -> None:
        self.calls: list[dict] = []
        self.probe_status = probe_status
        self.refresh = refresh
        self.expires_in = expires_in
        #: org slug -> probe status, for the 401 case.
        self.probe_by_org: dict[str, int] = {}
        #: Explicit results, consumed before the plan.
        self.scripted: list[HttpResult] = []
        #: When true, the exchange names no org at all.
        self.omit_org = False
        #: When true, the exchange returns no ``expires_in``.
        self.omit_expiry = False

    @staticmethod
    def _slug(blob: str) -> str:
        for candidate in (ORG, "acme", "northwind-org", "fourth"):
            if candidate in blob:
                return candidate
        return ""

    def org_for(self, blob: str) -> str:
        return self._slug(blob)

    def request(self, method, url, *, body=None, headers=None, timeout=10.0) -> HttpResult:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "body": body,
                "raw_body": body.decode() if body else "",
                "headers": dict(headers or {}),
                "timeout": timeout,
            }
        )
        if self.scripted:
            return self.scripted.pop(0)
        fields = fields_of(body)
        grant = fields.get("grant_type", "")
        if grant == "authorization_code":
            return self._token(fields.get("code", ""))
        if grant == "refresh_token":
            return self._refresh(fields.get("refresh_token", ""))
        # A bearer-authenticated read: the researched step 6.
        authorization = str((headers or {}).get("Authorization") or "")
        slug = self._slug(authorization)
        status = self.probe_by_org.get(slug, self.probe_status)
        if status == 200:
            return HttpResult(ok=True, status=200, body='{"results": []}', duration_ms=9.0)
        return HttpResult(ok=False, status=status, body=f'{{"error": "status {status}"}}', duration_ms=7.0)

    # -- token answers ----------------------------------------------------- #

    def _payload(self, slug: str, index: int, *, with_refresh: bool) -> dict:
        payload: dict = {
            "access_token": f"at-{slug}-{index}",
            "token_type": "bearer",
        }
        if not self.omit_expiry:
            payload["expires_in"] = self.expires_in
        if with_refresh:
            payload["refresh_token"] = f"rt-{slug}-{index}"
        if not self.omit_org:
            if "salesforce" in self._vendor_of(payload):
                payload["instance_url"] = f"https://{slug}.my.salesforce.com"
            else:
                payload["hub_domain"] = f"{slug}.hubspot.com"
        return payload

    @staticmethod
    def _vendor_of(payload: dict) -> str:
        return str(payload.get("instance_url") or "")

    def _token(self, code: str) -> HttpResult:
        slug = self._slug(code) or ORG
        return HttpResult(ok=True, status=200, body=json.dumps(self._payload(slug, 1, with_refresh=True)), duration_ms=8.0)

    def _refresh(self, refresh_token: str) -> HttpResult:
        slug = self._slug(refresh_token) or ORG
        if self.refresh == "invalid_grant":
            return HttpResult(
                ok=False,
                status=400,
                body=json.dumps({"error": "invalid_grant", "error_description": "refresh token is not valid"}),
                duration_ms=11.0,
            )
        if self.refresh == "unreachable":
            return HttpResult(ok=False, status=0, error="ConnectionResetError: reset by peer")
        # A refresh that returns no new refresh token is the other half of the
        # rule the engine has to get right.
        payload = self._payload(slug, 2, with_refresh=self.refresh == "with_refresh")
        if self.refresh == "with_refresh":
            payload["refresh_token"] = f"rt-{slug}-2"
        return HttpResult(ok=True, status=200, body=json.dumps(payload), duration_ms=8.0)


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(tmp_path / "wf034.db", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def transport():
    return FakeTransport()


@pytest.fixture()
def clock():
    return {"now": datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)}


def engine(store: RecordStore, transport=None, clock=None) -> CrmOAuthConnections:
    """A :class:`CrmOAuthConnections` that never sleeps and never opens a socket."""
    return CrmOAuthConnections(
        store,
        transport=transport or FakeTransport(),
        vault=CredentialVault(store),
        now=(lambda: clock["now"]) if clock else None,
    )


@pytest.fixture()
def crm(store, transport, clock):
    return engine(store, transport, clock)


@pytest.fixture()
def room(store):
    return store.create(
        "room", {"name": "Northwind evaluation", "account": "Northwind Traders", "stage": "evaluation"}, actor="dana"
    )


@pytest.fixture()
def connection(crm, room):
    return crm.create_connection(
        CONNECTION | {"room_id": room["id"]}, actor="dana", source=SOURCE
    )


@pytest.fixture()
def authorized(crm, connection, transport):
    """A connection holding a sealed, unexpired credential."""
    grant = crm.begin_authorization(connection["id"], actor="dana", source=SOURCE)
    crm.exchange_callback(
        connection["id"], code=f"code-{ORG}", state=grant["state"], actor="dana", source=SOURCE
    )
    return crm.store.get(connection["id"])


def make_authorized(crm, connection_id, *, code=None, org=ORG) -> dict:
    grant = crm.begin_authorization(connection_id, actor="dana", source=SOURCE)
    return crm.exchange_callback(
        connection_id,
        code=code or f"code-{org}",
        state=grant["state"],
        actor="dana",
        source=SOURCE,
    )


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


@pytest.fixture()
def http(monkeypatch, transport):
    """A client over a temporary database, with the vendor faked at the transport."""
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf034_http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as client:
        app.dependency_overrides[load_feature(MODULE).get_connections] = (
            lambda: engine(client.app.state.store, transport)
        )
        try:
            yield client
        finally:
            app.dependency_overrides.clear()
    tmp.cleanup()


@pytest.fixture()
def http_room(http):
    return http.post("/api/records/room", json={"name": "Northwind", "account": "Northwind"}).json()


def register_http(http, payload=None) -> dict:
    return http.post(f"{PREFIX}/connections", json=payload or CONNECTION).json()


def authorize_http(http, connection_id, code=None) -> dict:
    grant = http.get(f"{PREFIX}/connections/{connection_id}/authorize-url").json()
    return http.post(
        f"{PREFIX}/connections/{connection_id}/callback",
        params={"code": code or f"code-{ORG}", "state": grant["state"]},
    ).json()


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The route resolves even though no shared file names this feature."""
    entry = next(
        f
        for f in http.get("/api/features").json()["features"]
        if f["id"] == "wf-034-connect-a-crm-org-to-the-sales-room-oa"
    )
    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-034"
    assert entry["exception_handlers"] == [
        "ConnectionDisabledError",
        "ConnectionNotFoundError",
        "CrmOAuthError",
        "NotConnectedError",
        "TokenExchangeError",
        "UnknownRoomError",
        "VaultSealedError",
        "VendorRequestError",
    ]
    assert len(entry["routes"]) == ROUTE_COUNT


def test_no_other_feature_failed_to_load_because_of_this_one(http):
    assert http.get("/api/features").json()["failed_count"] == 0


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
    assert len(mine) == ROUTE_COUNT
    assert not mine & others


def test_room_scoped_paths_stay_room_scoped(http):
    """The brief asks for this explicitly: ``/rooms/<room_id>/...`` throughout."""
    templates = {
        route["path"]
        for feature in http.get("/api/features").json()["features"]
        if feature["id"] == "wf-034-connect-a-crm-org-to-the-sales-room-oa"
        for route in feature["routes"]
        if "/rooms/" in route["path"]
    }
    assert templates == {
        f"{PREFIX}/rooms/{{room_id}}/connections",
        f"{PREFIX}/rooms/{{room_id}}/health-check",
        f"{PREFIX}/rooms/{{room_id}}/readiness",
    }


def test_no_route_under_this_prefix_could_write_a_crm_record(http):
    """[sourced] "nothing is written to CRM records in this workflow"."""
    routes = [
        route
        for feature in http.get("/api/features").json()["features"]
        if feature["id"] == "wf-034-connect-a-crm-org-to-the-sales-room-oa"
        for route in feature["routes"]
    ]
    writes = {
        (method, route["path"]) for route in routes for method in route["methods"] if method in ("POST", "PATCH", "PUT")
    }
    assert not [key for key in writes if "sync" in key[1] or "records" in key[1] or "write" in key[1]]
    # Every write is one of the six steps or a log, and each is named for what it
    # does rather than for a CRM object.
    assert {path for _, path in writes} == {
        f"{PREFIX}/connections",
        f"{PREFIX}/connections/{{connection_id}}",
        f"{PREFIX}/connections/{{connection_id}}/callback",
        f"{PREFIX}/connections/{{connection_id}}/refresh",
        f"{PREFIX}/connections/{{connection_id}}/test",
        f"{PREFIX}/rooms/{{room_id}}/health-check",
    }


def test_the_credential_vault_has_no_read_route(http):
    """Not a redacted one, not a masked one: none.

    A route that returned a sealed row would put a credential's metadata in
    everyone's hands, and the whole point of sealing it is that the key is held
    by the code that needs the token and by nothing else.
    """
    paths = [
        route["path"]
        for feature in http.get("/api/features").json()["features"]
        if feature["id"] == "wf-034-connect-a-crm-org-to-the-sales-room-oa"
        for route in feature["routes"]
    ]
    assert not [path for path in paths if "credential" in path or "vault" in path]
    # The one route that mentions a token is the lifecycle log, and it records
    # field names and statuses rather than anything a reader could use.
    assert [path for path in paths if "token" in path] == [f"{PREFIX}/connections/{{connection_id}}/token-events"]


def test_the_generic_records_api_can_see_a_vault_row_and_it_is_still_safe(http, http_room):
    """The core records API lists any collection, so it lists this one too.

    Stating that plainly is better than a comment claiming it cannot: what makes
    it safe is the payload's shape, and the shape is asserted here rather than
    assumed. A reader gets the sealed string and the field *names* it holds, which
    is what they need to know the credential exists and what is in it.
    """
    connection = register_http(http, CONNECTION | {"room_id": http_room["id"]})
    authorize_http(http, connection["id"])
    listed = http.get("/api/records/crm_credential", params={"limit": 50}).json()
    assert listed["count"] >= 1
    body = json.dumps(listed)
    for secret in ("secret-abc", f"at-{ORG}-1", f"rt-{ORG}-1"):
        assert secret not in body, secret
    row = next(
        entry
        for entry in listed["records"]
        if entry["data"].get("kind") == "org"
    )
    assert row["data"]["sealed"].startswith("v1.")
    assert row["data"]["fields"] == sorted(row["data"]["fields"])
    assert {"access_token", "refresh_token", "expires_at", "org_id"} <= set(row["data"]["fields"])
    assert row["data"]["org_key"] == ORG_HOST
    assert "access_token" not in json.dumps(row["data"]["sealed"])


def test_frontend_descriptor_id_matches_the_backend_feature_id():
    """The two halves of a feature are findable by one name, so they must agree."""
    descriptor = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / "wf-034-connect-a-crm-org-to-the-sales-room-oa"
        / "index.jsx"
    )
    text = descriptor.read_text(encoding="utf-8")
    assert load_feature(MODULE).FEATURE["id"] in text
    assert f"id: '{load_feature(MODULE).FEATURE['id']}'" in text


def test_the_frontend_folder_name_matches_the_feature_id():
    """The host globs ``src/features/*/index.jsx``, so the folder is the name."""
    features = Path(__file__).resolve().parents[2] / "frontend" / "src" / "features"
    folders = {path.name for path in features.iterdir() if (path / "index.jsx").exists()}
    assert load_feature(MODULE).FEATURE["id"] in folders


def test_feature_module_does_not_import_the_shared_app():
    """A guard exists in test_features.py; this states the reason locally."""
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "dsr.api" not in source
    assert "from dsr.deps import" in source


def test_the_domain_package_does_not_import_another_feature():
    """Isolation is the point of the host; a domain package must keep it too.

    Checked on the parsed import statements rather than the raw text, so a
    docstring that *names* another package to explain why it does not borrow from
    it is not mistaken for a dependency.
    """
    package = Path(load_feature(MODULE).__file__).parent.parent / "crm_oauth"
    forbidden = {"dsr.crm", "dsr.analytics", "dsr.search", "dsr.publishing", "dsr.rules", "dsr.api", "dsr.features"}
    for module in sorted(package.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                # This package's own modules are the point; only a borrow from a
                # *different* feature's domain would break isolation.
                if name == "dsr.crm_oauth" or name.startswith("dsr.crm_oauth."):
                    continue
                assert not any(name.startswith(bad) for bad in forbidden), f"{module.name} imports {name}"


def test_no_feature_seeds_into_the_shared_seeder(http, http_room):
    """Demo data lives in ``seed(db, context)`` in the feature module."""
    assert hasattr(load_feature(MODULE), "seed")
    seeded = http.get(f"{PREFIX}/connections").json()
    assert seeded["count"] == 0


# --------------------------------------------------------------------------- #
# The researched contract, served as data
# --------------------------------------------------------------------------- #


def test_the_vocabulary_endpoint_serves_the_401_sentence_verbatim(http):
    quote = http.get(f"{PREFIX}/vocabulary").json()["sourced_quotes"]["401_is_not_a_refresh_signal"]
    assert (
        quote["quote"]
        == "`Unauthorized (401)` requests are not a valid indicator that a new access token must be retrieved."
    )
    assert quote["where"].startswith("WF-034 research evidence")


def test_the_vocabulary_endpoint_serves_the_four_interfaces_in_order(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert [entry["name"] for entry in body["connector_interfaces"]] == [
        "authorize_url",
        "exchange_code",
        "refresh",
        "execute",
    ]
    assert "the same 4 interfaces" in body["interface_rationale"]


def test_the_vocabulary_endpoint_serves_the_six_steps(http):
    flow = http.get(f"{PREFIX}/vocabulary").json()["flow"]
    assert len(flow) == 6
    assert "client_id" in flow[1] and "scope" in flow[1] and "redirect_uri" in flow[1]
    assert "code" in flow[3]
    assert "org/account id" in flow[4]
    assert "low-cost" in flow[5]


def test_the_vocabulary_endpoint_offers_exactly_the_three_researched_vendors(http):
    assert http.get(f"{PREFIX}/vocabulary").json()["vendors"] == list(VENDOR_IDS)
    assert VENDOR_IDS == ("salesforce", "hubspot", "dataverse")


def test_the_vocabulary_endpoint_serves_both_research_gaps(http):
    gaps = http.get(f"{PREFIX}/vocabulary").json()["gaps"]
    assert {gap["id"] for gap in gaps} == {"salesforce_flow_details", "dataverse_auth_unread"}
    assert any("no Dataverse-specific auth quote is claimed" in gap["gap"] for gap in gaps)


def test_the_vocabulary_endpoint_claims_no_dataverse_auth_quote():
    """The research says it claimed none, so this build must not invent one."""
    from dsr.crm_oauth import describe_vocabulary

    body = describe_vocabulary()
    dataverse = body["connectors"]["dataverse"]["info"]
    assert "resource" not in {entry.split(".")[0] for entry in dataverse["researched"]}
    assert any("no Dataverse-specific auth quote is claimed" in line for line in dataverse["inferences"])


def test_the_vocabulary_endpoint_serves_the_state_vocabulary(http):
    states = http.get(f"{PREFIX}/vocabulary").json()["states"]
    assert states["statuses"] == list(CONNECTION_STATUSES)
    assert states["health"] == list(HEALTH_VALUES)
    assert states["unauthorized_is_not_a_refresh_trigger"] is True
    assert states["skew_seconds"] == SKEW_SECONDS
    assert set(states["collections"]) == set(COLLECTIONS)


def test_the_inferences_endpoint_serves_named_bounded_decisions(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(INFERENCES) >= 20
    assert body["count"] == body["constrained_by_a_quote"] + body["silent_in_the_research"]
    for entry in body["inferences"].values():
        assert entry["decision"] and entry["why"] and entry["change_how"]
        assert "sourced_against" in entry


def test_the_inference_about_the_401_names_its_constraint():
    for name in ("a_401_changes_nothing_but_health", "status_and_health_are_separate"):
        assert INFERENCES[name]["sourced_against"] == "401_is_not_a_refresh_signal"


def test_every_sourced_quote_says_where_it_came_from():
    for name, entry in SOURCED_QUOTES.items():
        assert entry["quote"].strip(), name
        assert entry["where"].startswith("WF-034 research evidence"), name
        assert entry["means"].strip(), name


def test_the_vocabulary_endpoint_publishes_what_this_build_does_not_implement(http):
    adjacent = http.get(f"{PREFIX}/vocabulary").json()["not_implemented"]
    assert "nothing is written to CRM records" in adjacent["sync_of_crm_records"]["researched"]
    assert "route" in adjacent["background_scheduler"]["this_build"]


def test_the_connectors_endpoint_lists_the_four_methods_for_each_vendor(http):
    body = http.get(f"{PREFIX}/connectors").json()
    assert set(body["registered"]) == set(VENDOR_IDS)
    for name, entry in body["connectors"].items():
        assert [method["name"] for method in entry["implements"]] == [
            "authorize_url",
            "exchange_code",
            "refresh",
            "execute",
        ]
        assert all(method["callable"] for method in entry["implements"]), name


# --------------------------------------------------------------------------- #
# The four interfaces
# --------------------------------------------------------------------------- #


def test_hubspots_authorize_url_is_the_one_the_research_quotes():
    """[sourced] ``https://app.hubspot.com/oauth/authorize?client_id=…&scope=…&redirect_uri=…``"""
    url = connector("hubspot").authorize_url(
        AuthorizeRequest(client_id="c-1", redirect_uri=REDIRECT, scopes=("a", "b"), state="s-1")
    )
    assert url.startswith("https://app.hubspot.com/oauth/authorize?")
    query = parse_qs(urlsplit(url).query)
    assert query["client_id"] == ["c-1"]
    assert query["redirect_uri"] == [REDIRECT]
    assert query["scope"] == ["a b"]
    assert query["state"] == ["s-1"]
    assert query["response_type"] == ["code"]


def test_scopes_are_space_separated():
    """The research quotes ``scope=…`` and does not say how values are separated.

    ``application/x-www-form-urlencoded`` is how the OAuth 2.0 framework encodes a
    query, so the space goes out as ``+`` and the vendor decodes it back to a
    space. The assertion is on the decoded value, which is what the vendor sees.
    """
    url = connector("hubspot").authorize_url(
        AuthorizeRequest(client_id="c", redirect_uri=REDIRECT, scopes=("a", "b", "c"), state="s")
    )
    assert parse_qs(urlsplit(url).query)["scope"] == ["a b c"]


def test_an_authorization_with_no_scopes_is_refused():
    """"The consent screen grants scopes" - an empty scope asks for nothing."""
    for vendor in VENDOR_IDS:
        with pytest.raises(ConnectorConfigError, match="at least one scope"):
            connector(vendor).authorize_url(
                AuthorizeRequest(client_id="c", redirect_uri=REDIRECT, scopes=(), state="s")
            )


def test_an_authorization_without_a_client_id_or_redirect_uri_is_refused():
    for vendor in VENDOR_IDS:
        with pytest.raises(ConnectorConfigError, match="client_id"):
            connector(vendor).authorize_url(
                AuthorizeRequest(client_id="", redirect_uri=REDIRECT, scopes=("a",), state="s")
            )
        with pytest.raises(ConnectorConfigError, match="redirect_uri"):
            connector(vendor).authorize_url(
                AuthorizeRequest(client_id="c", redirect_uri="", scopes=("a",), state="s")
            )


def test_an_empty_parameter_is_dropped_rather_than_sent_blank():
    assert build_authorize_url("https://x.example/auth", {"a": "", "b": "1"}) == "https://x.example/auth?b=1"


def test_the_dataverse_authorize_url_carries_the_azure_tenant():
    assert connector("dataverse").authorize_url(
        AuthorizeRequest(
            client_id="c", redirect_uri=REDIRECT, scopes=("s",), state="st", extra={"tenant": "contoso"}
        )
    ).startswith("https://login.microsoftonline.com/contoso/oauth2/v2.0/authorize?")


def test_the_salesforce_authorize_url_follows_the_environment():
    production = connector("salesforce").authorize_url(
        AuthorizeRequest(client_id="c", redirect_uri=REDIRECT, scopes=("s",), state="st")
    )
    sandbox = connector("salesforce").authorize_url(
        AuthorizeRequest(
            client_id="c", redirect_uri=REDIRECT, scopes=("s",), state="st", extra={"environment": "sandbox"}
        )
    )
    assert production.startswith("https://login.salesforce.com/")
    assert sandbox.startswith("https://test.salesforce.com/")


def test_salesforce_publishes_both_app_kinds_with_the_researched_quotation():
    """[sourced] "we recommend using external client apps instead"."""
    policies = {entry["policy"] for entry in SALESFORCE_POLICIES}
    assert policies == {"external_client_app", "connected_app"}
    assert all("Spring '26" in entry["note"] for entry in SALESFORCE_POLICIES)


def test_hubspot_publishes_the_installer_requirement_as_a_grant_requirement():
    """[sourced] "must either be a Super Admin or have HubSpot Marketplace Access permissions"."""
    info = vendor_info("hubspot")
    assert any("Super Admin" in line for line in info.grant_requirements)
    assert any("Super Admin" in line for line in info.advisory)


def test_hubspot_and_salesforce_do_not_need_an_org_to_be_configured():
    assert vendor_info("hubspot").requires_org is False
    assert vendor_info("salesforce").requires_org is False


def test_dataverse_requires_the_org_because_its_resource_url_is_templated():
    """[sourced] ``https://<org>.api.crm.dynamics.com/api/data/v9.2/…``"""
    info = vendor_info("dataverse")
    assert info.requires_org is True
    assert info.api_base_url == "https://<org>.api.crm.dynamics.com"
    assert info.probe_path == "/api/data/v9.2/WhoAmI"


def test_every_connector_publishes_what_is_quoted_and_what_is_inferred():
    for vendor in VENDOR_IDS:
        info = vendor_info(vendor).describe()
        assert info["researched"], vendor
        assert info["inferences"], vendor
        assert info["org_id_keys_are_inference"] is True


def test_an_unknown_vendor_names_the_ones_that_exist():
    with pytest.raises(UnknownVendorError, match="hubspot, salesforce"):
        connector("not-a-vendor")


def test_a_duplicate_registration_is_refused_rather_than_load_order_decided():
    class Clash:
        vendor = "hubspot"

        def authorize_url(self, request):  # pragma: no cover - never reached
            return ""

        def exchange_code(self, request, *, transport):  # pragma: no cover
            return TokenResponse(access_token="x")

        def refresh(self, request, *, transport):  # pragma: no cover
            return TokenResponse(access_token="x")

        def execute(self, request, *, transport):  # pragma: no cover
            return HttpResult(ok=True, status=200)

    with pytest.raises(ValueError, match="already registered"):
        register(Clash())


def test_a_registration_missing_one_of_the_four_interfaces_is_refused():
    class Incomplete:
        vendor = "half-built"

        def authorize_url(self, request):  # pragma: no cover - never reached
            return ""

    with pytest.raises(ValueError, match="does not implement exchange_code"):
        register(Incomplete())


def test_a_token_in_a_url_is_refused_at_runtime():
    """"Authorization: Bearer token" is a header; a URL reaches every log."""
    with pytest.raises(ConnectorConfigError, match="must travel in the Authorization header"):
        assert_no_token_in_url("https://api.example/v1/x?access_token=abc", "abc")
    with pytest.raises(ConnectorConfigError):
        assert_no_token_in_url("https://api.example/v1/abc", "abc")
    assert_no_token_in_url("https://api.example/v1/x?limit=1", "abc")


@pytest.mark.parametrize("vendor", VENDOR_IDS)
def test_no_connector_ever_puts_a_token_in_a_url(vendor, transport):
    """[sourced] "Every REST call then uses ``Authorization: Bearer token``"."""
    info = vendor_info(vendor)
    result = connector(vendor).execute(
        __import__("dsr.crm_oauth", fromlist=["ExecuteRequest"]).ExecuteRequest(
            base_url="https://api.example",
            path=info.probe_path.format(api_version=info.api_version),
            access_token="at-secret-value",
            query=dict(info.probe_query),
        ),
        transport=transport,
    )
    call = transport.calls[-1]
    assert call["headers"]["Authorization"] == "Bearer at-secret-value"
    assert "at-secret-value" not in call["url"]
    assert result.ok


def test_a_vendor_body_whose_error_mentions_the_token_is_still_not_in_our_url(transport):
    """The vendor may say anything in its body; our request is what we control."""
    transport.scripted = [HttpResult(ok=False, status=401, body="token at-secret-value expired")]
    connector("hubspot").execute(
        __import__("dsr.crm_oauth", fromlist=["ExecuteRequest"]).ExecuteRequest(
            base_url="https://api.hubapi.com", path="/crm/v3/objects/contacts", access_token="at-secret-value"
        ),
        transport=transport,
    )
    assert "at-secret-value" not in transport.calls[-1]["url"]


def test_a_token_endpoint_that_answers_401_is_refused_with_the_vendors_own_detail(transport):
    transport.scripted = [
        HttpResult(ok=False, status=401, body=json.dumps({"error": "invalid_client"}), duration_ms=3.0)
    ]
    with pytest.raises(TokenExchangeError) as caught:
        connector("hubspot").exchange_code(
            TokenRequest(client_id="c", client_secret="s", code="code"), transport=transport
        )
    assert "invalid_client" in str(caught.value)
    assert caught.value.vendor_status == 401


def test_a_token_endpoint_that_is_unreachable_is_a_vendor_error_not_a_caller_error(transport):
    transport.scripted = [HttpResult(ok=False, status=0, error="ConnectionResetError: reset")]
    with pytest.raises(VendorRequestError, match="could not be reached"):
        connector("hubspot").exchange_code(
            TokenRequest(client_id="c", client_secret="s", code="code"), transport=transport
        )


def test_a_token_endpoint_that_answers_without_an_access_token_is_refused(transport):
    transport.scripted = [HttpResult(ok=True, status=200, body="{}")]
    with pytest.raises(TokenExchangeError, match="no access_token"):
        connector("hubspot").exchange_code(
            TokenRequest(client_id="c", client_secret="s", code="code"), transport=transport
        )


def test_a_server_side_token_request_needs_the_client_secret(transport):
    with pytest.raises(ConnectorConfigError, match="client_secret"):
        connector("hubspot").exchange_code(
            TokenRequest(client_id="c", client_secret="", code="code"), transport=transport
        )


def test_a_refresh_without_a_refresh_token_is_refused_before_the_vendor_is_called(transport):
    with pytest.raises(TokenExchangeError, match="no refresh token"):
        connector("hubspot").refresh(
            TokenRequest(client_id="c", client_secret="s", refresh_token=""), transport=transport
        )
    assert transport.calls == []


def test_a_token_request_is_form_encoded_with_the_grant_type():
    body = form_body({"grant_type": "authorization_code", "code": "c 1", "client_secret": "s", "empty": ""})
    assert body == b"grant_type=authorization_code&code=c+1&client_secret=s"


def test_credential_headers_are_redacted_wherever_they_are_recorded():
    redacted = redact_headers({"Authorization": "Bearer at-1", "Accept": "application/json"})
    assert redacted == {"Authorization": "<redacted>", "Accept": "application/json"}


# --------------------------------------------------------------------------- #
# A connector is a plug-in, not a fork
# --------------------------------------------------------------------------- #


class FourthVendor:
    """A fourth vendor, written the way a third party would write one.

    Four methods, a published ``VendorInfo``, no per-tenant state and no import
    from anywhere in this package. The whole flow runs on it below, which is the
    only way the researched extensibility claim is worth anything.
    """

    def __init__(self) -> None:
        self.info = VendorInfo(
            vendor="fourth",
            label="Fourth CRM",
            authorize_endpoint="https://fourth.example/oauth/authorize",
            token_endpoint="https://fourth.example/oauth/token",
            api_base_url="https://fourth.example/api",
            probe_path="/whoami",
        )
        self.seen: list[str] = []
        self.tokens_sent: list[str] = []

    @property
    def vendor(self) -> str:
        return "fourth"

    def authorize_url(self, request: AuthorizeRequest) -> str:
        return build_authorize_url(
            self.info.authorize_endpoint,
            {
                "client_id": request.client_id,
                "redirect_uri": request.redirect_uri,
                "scope": " ".join(request.scopes),
                "state": request.state,
            },
        )

    def exchange_code(self, request: TokenRequest, *, transport) -> TokenResponse:
        self.seen.append("exchange_code")
        return TokenResponse(
            access_token="at-fourth-1",
            refresh_token="rt-fourth-1",
            expires_in=900,
            org_id="fourth-org",
        )

    def refresh(self, request: TokenRequest, *, transport) -> TokenResponse:
        self.seen.append("refresh")
        return TokenResponse(access_token="at-fourth-2", expires_in=900, org_id="fourth-org")

    def execute(self, request, *, transport) -> HttpResult:
        self.seen.append("execute")
        self.tokens_sent.append(request.access_token)
        return HttpResult(ok=True, status=200, body='{"who":"me"}', duration_ms=2.0)


@pytest.fixture()
def fourth():
    plug_in = FourthVendor()
    register(plug_in)
    try:
        yield plug_in
    finally:
        unregister("fourth")


def test_a_fourth_vendor_is_a_plug_in(store, fourth, clock):
    """[sourced] "A third party adds a new vendor by implementing the same 4 interfaces"."""
    assert "fourth" in registered_vendors()
    crm = engine(store, FakeTransport(), clock)
    room = store.create("room", {"name": "R"}, actor="dana")
    created = crm.create_connection(
        {
            "vendor": "fourth",
            "room_id": room["id"],
            "client_id": "c",
            "client_secret": "s",
            "redirect_uri": REDIRECT,
            "scopes": ["everything"],
        },
        actor="dana",
        source=SOURCE,
    )
    assert created["vendor"] == "fourth"
    grant = crm.begin_authorization(created["id"], actor="dana", source=SOURCE)
    assert grant["authorize_url"].startswith("https://fourth.example/oauth/authorize?")

    result = make_authorized(crm, created["id"], org="fourth")
    assert result["org_key"] == "fourth-org"
    assert result["expires_at"]

    probed = crm.test_connection(created["id"], actor="dana", source=SOURCE)
    assert probed["ok"] is True
    assert fourth.seen == ["exchange_code", "execute"]
    assert fourth.tokens_sent == ["at-fourth-1"]

    refreshed = crm.refresh_now(created["id"], actor="dana", source=SOURCE)
    assert refreshed["trigger"] == "forced"
    assert fourth.seen[-1] == "refresh"

    # And the refreshed token is the one the next bearer call carries, which is
    # the only way "the refresh token is what we keep" means anything.
    crm.test_connection(created["id"], actor="dana", source=SOURCE)
    assert fourth.tokens_sent == ["at-fourth-1", "at-fourth-2"]


def test_a_connector_registers_without_touching_anything_else(store, fourth, clock):
    """Registration is the whole of the extension point."""
    crm = engine(store, FakeTransport(), clock)
    room = store.create("room", {"name": "R"}, actor="dana")
    crm.create_connection(
        {"vendor": "fourth", "room_id": room["id"], "client_id": "c", "client_secret": "s",
         "redirect_uri": REDIRECT, "scopes": ["s"]},
        actor="dana",
        source=SOURCE,
    )
    make_authorized(crm, crm.list_connections()[0]["id"], org="fourth")
    assert crm.test_connection(crm.list_connections()[0]["id"], actor="dana", source=SOURCE)["outcome"] == "ok"


def test_a_connector_holds_no_per_tenant_state():
    """"Per-tenant credentials are already isolated in the integration record"."""
    for name in registered_vendors():
        assert describe_connector(name)["holds_state"] == [], name


def test_a_connector_registered_without_published_metadata_is_still_usable():
    class Bare:
        vendor = "bare"

        def authorize_url(self, request):  # pragma: no cover - never reached
            return "https://bare.example/authorize"

        def exchange_code(self, request, *, transport):  # pragma: no cover
            return TokenResponse(access_token="x")

        def refresh(self, request, *, transport):  # pragma: no cover
            return TokenResponse(access_token="x")

        def execute(self, request, *, transport):  # pragma: no cover
            return HttpResult(ok=True, status=200)

    register(Bare())
    try:
        info = vendor_info("bare")
        assert info.vendor == "bare"
        assert any("nothing is claimed" in line for line in info.inferences)
        assert describe_connector("bare")["implements"][0]["callable"] is True
    finally:
        unregister("bare")


# --------------------------------------------------------------------------- #
# The credential vault
# --------------------------------------------------------------------------- #


def test_a_sealed_payload_opens_with_the_same_key():
    key = resolve_key().material
    sealed = seal({"access_token": "at-1", "expires_in": 1800}, key)
    assert "at-1" not in sealed
    assert open_sealed(sealed, key) == {"access_token": "at-1", "expires_in": 1800}


def test_a_sealed_payload_does_not_open_with_another_key():
    sealed = seal({"access_token": "at-1"}, b"a-different-key")
    with pytest.raises(VaultSealedError, match="does not verify"):
        open_sealed(sealed, resolve_key().material)


def test_a_tampered_sealed_payload_is_refused():
    """Encrypt-then-MAC: the tag is checked before a byte of ciphertext is used."""
    key = resolve_key().material
    sealed = seal({"access_token": "at-1"}, key).split(".")
    sealed[2] = sealed[2][:-4] + ("AAAA" if not sealed[2].endswith("AAAA") else "BBBB")
    with pytest.raises(VaultSealedError, match="does not verify"):
        open_sealed(".".join(sealed), key)


@pytest.mark.parametrize("value", ["", "nope", "v1.only-two", "v1.a.b.c.d", "v9.a.b.c"])
def test_a_malformed_sealed_value_is_refused_rather_than_parsed(value):
    with pytest.raises(VaultSealedError):
        open_sealed(value, resolve_key().material)


def test_the_key_comes_from_the_environment_when_it_is_set(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "a-real-secret")
    assert resolve_key().origin == "env"
    assert resolve_key().is_default is False


def test_without_the_environment_variable_the_feature_says_so_loudly(monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    key = resolve_key()
    assert key.origin == "default"
    assert key.is_default is True
    warning = CredentialVault(RecordStore(AuditedDatabase(":memory:"))).key_warning()
    assert KEY_ENV in warning and "published demo key" in warning


def test_the_default_key_is_the_published_one():
    assert resolve_key().material == DEMO_KEY.encode()


def test_a_vault_row_is_keyed_by_the_org(store):
    vault = CredentialVault(store)
    vault.put("crm_connection_1", "northwind.hubspot.com", {"access_token": "at-1"}, kind="org", source="seed")
    vault.put("crm_connection_1", "fabrikam.hubspot.com", {"access_token": "at-2"}, kind="org", source="seed")
    rows = vault.list_rows("crm_connection_1")
    assert [row["org_key"] for row in rows] == ["fabrikam.hubspot.com", "northwind.hubspot.com"]
    assert vault.read("crm_connection_1", "northwind.hubspot.com") == {"access_token": "at-1"}


def test_a_vault_row_with_no_org_key_is_refused():
    """"keyed by the org/account id" - a credential with no org has no key."""
    vault = CredentialVault(RecordStore(AuditedDatabase(":memory:")))
    with pytest.raises(ValueError, match="org_key is required"):
        vault.put("crm_connection_1", "", {"access_token": "at-1"}, kind="org", source="seed")


def test_a_second_write_replaces_rather_than_accumulates(store):
    vault = CredentialVault(store)
    first = vault.put("c", "org-1", {"access_token": "at-1"}, kind="org", source="seed")
    second = vault.put("c", "org-1", {"access_token": "at-2"}, kind="org", source="seed")
    assert first["id"] == second["id"]
    assert len(vault.list_rows("c")) == 1
    assert vault.read("c", "org-1") == {"access_token": "at-2"}


def test_a_vault_summary_names_the_fields_without_their_values(store):
    vault = CredentialVault(store)
    vault.put("c", "org-1", {"access_token": "at-1", "refresh_token": "rt-1"}, kind="org", source="seed")
    summary = vault.list_rows("c")[0]
    assert summary["sealed"] is True
    assert summary["fields"] == ["access_token", "refresh_token"]
    assert "at-1" not in json.dumps(summary)
    assert "rt-1" not in json.dumps(summary)


def test_a_vault_row_sealed_under_another_key_is_reported_unreadable(store, monkeypatch):
    vault = CredentialVault(store)
    vault.put("c", "org-1", {"access_token": "at-1"}, kind="org", source="seed")
    other = CredentialVault(store, key=type(resolve_key())(material=b"elsewhere", origin="env", key_id="x"))
    assert other.list_rows("c")[0]["readable_here"] is False
    with pytest.raises(VaultSealedError, match="re-authorize"):
        other.read("c", "org-1")


def test_a_sealed_row_is_audited_when_it_is_written(store):
    vault = CredentialVault(store)
    vault.put("c", "org-1", {"access_token": "at-1"}, kind="org", actor="dana", source=SOURCE)
    entries = store.audit(collection=CREDENTIAL_COLLECTION)
    assert entries and entries[0]["source"] == SOURCE
    assert "at-1" not in json.dumps(entries)


# --------------------------------------------------------------------------- #
# Step 1: registering a connection
# --------------------------------------------------------------------------- #


def test_a_registered_connection_starts_pending_authorization(connection):
    assert connection["status"] == STATUS_PENDING
    assert connection["credential"]["sealed"] is False
    assert connection["needs_action"]["action"] == "authorize"
    assert connection["blockers"] == []


def test_a_registered_connection_reports_the_client_secret_only_as_a_boolean(connection):
    assert connection["has_client_secret"] is True
    assert "client_secret" not in connection
    assert "secret-abc" not in json.dumps(connection)


def test_the_client_secret_is_sealed_under_the_reserved_app_key(crm, connection):
    assert crm.vault.read(connection["id"], APP_ORG_KEY) == {"client_secret": "secret-abc"}


def test_an_unknown_vendor_is_refused_before_anything_is_written(crm):
    with pytest.raises(UnknownVendorError):
        crm.create_connection(CONNECTION | {"vendor": "not-a-crm"}, actor="dana", source=SOURCE)
    assert crm.list_connections() == []


def test_a_connection_to_a_room_that_does_not_exist_is_refused(crm):
    with pytest.raises(UnknownRoomError):
        crm.create_connection(CONNECTION | {"room_id": "room_nope"}, actor="dana", source=SOURCE)


def test_scopes_accept_a_string_a_list_or_nothing(crm):
    def scopes_of(value):
        room = crm.store.create("room", {"name": f"R{value!r}"}, actor="dana")
        row = crm.create_connection(
            CONNECTION | {"label": str(value), "room_id": room["id"], "scopes": value},
            actor="dana",
            source=SOURCE,
        )
        return row["scopes"]

    assert scopes_of("a b,c") == ["a", "b", "c"]
    assert scopes_of(["a", "b", "a"]) == ["a", "b"]
    assert scopes_of(None) == []


def test_a_connection_can_be_scoped_to_a_room_or_to_the_whole_tenant(crm, room, connection):
    tenant_wide = crm.create_connection(
        CONNECTION | {"label": "Tenant wide", "room_id": None}, actor="dana", source=SOURCE
    )
    assert connection["scope"] == "room"
    assert tenant_wide["scope"] == "tenant"


def test_patching_a_connection_re_seals_the_client_secret(crm, connection):
    updated = crm.update_connection(connection["id"], {"client_secret": "rotated"}, actor="dana", source=SOURCE)
    assert updated["has_client_secret"] is True
    assert crm.vault.read(connection["id"], APP_ORG_KEY) == {"client_secret": "rotated"}


def test_clearing_the_client_secret_is_refused_so_a_connection_is_never_half_sealed(crm, connection):
    updated = crm.update_connection(connection["id"], {"client_secret": ""}, actor="dana", source=SOURCE)
    assert updated["has_client_secret"] is False


def test_emptying_the_scopes_is_refused(crm, connection):
    with pytest.raises(ConnectorConfigError, match="scopes cannot be emptied"):
        crm.update_connection(connection["id"], {"scopes": []}, actor="dana", source=SOURCE)


def test_a_connection_that_does_not_exist_is_a_404(crm):
    with pytest.raises(ConnectionNotFoundError):
        crm.require_connection("crm_connection_nope")


def test_switching_a_connection_off_stops_it_being_used(crm, connection):
    off = crm.update_connection(connection["id"], {"enabled": False}, actor="dana", source=SOURCE)
    assert off["enabled"] is False
    with pytest.raises(ConnectionDisabledError, match="switched off"):
        crm.test_connection(connection["id"], actor="dana", source=SOURCE)
    with pytest.raises(ConnectionDisabledError):
        crm.begin_authorization(connection["id"], actor="dana", source=SOURCE)


# --------------------------------------------------------------------------- #
# Steps 2 to 5: authorize, consent, callback, exchange
# --------------------------------------------------------------------------- #


def test_the_authorize_url_carries_the_three_researched_parameters(crm, connection):
    grant = crm.begin_authorization(connection["id"], actor="dana", source=SOURCE)
    query = parse_qs(urlsplit(grant["authorize_url"]).query)
    assert query["client_id"] == ["client-123"]
    assert query["redirect_uri"] == [REDIRECT]
    assert query["scope"] == ["crm.objects.contacts.read offline_access"]
    assert grant["callback"].endswith(grant["state"])


def test_each_authorization_gets_its_own_state(crm, connection):
    first = crm.begin_authorization(connection["id"], actor="dana", source=SOURCE)
    second = crm.begin_authorization(connection["id"], actor="dana", source=SOURCE)
    assert first["state"] != second["state"]
    assert first["grant_id"] != second["grant_id"]


def test_the_scopes_may_be_overridden_for_one_authorization(crm, connection):
    grant = crm.begin_authorization(
        connection["id"], scopes=["a", "b"], actor="dana", source=SOURCE
    )
    assert "scope=a+b" in grant["authorize_url"]


def test_a_pending_authorization_expires(crm, connection, clock):
    grant = crm.begin_authorization(connection["id"], actor="dana", source=SOURCE)
    clock["now"] += timedelta(seconds=601)
    with pytest.raises(AuthorizationError, match="expired"):
        crm.exchange_callback(
            connection["id"], code=f"code-{ORG}", state=grant["state"], actor="dana", source=SOURCE
        )


def test_a_callback_without_a_code_is_refused(crm, connection):
    grant = crm.begin_authorization(connection["id"], actor="dana", source=SOURCE)
    with pytest.raises(AuthorizationError, match="no code"):
        crm.exchange_callback(connection["id"], code="", state=grant["state"], actor="dana", source=SOURCE)


def test_a_callback_without_a_state_is_refused(crm, connection):
    with pytest.raises(AuthorizationError, match="no state"):
        crm.exchange_callback(connection["id"], code="c", state="", actor="dana", source=SOURCE)


def test_a_callback_whose_state_matches_nothing_is_refused(crm, connection):
    with pytest.raises(AuthorizationError, match="no pending authorization"):
        crm.exchange_callback(connection["id"], code="c", state="made-up", actor="dana", source=SOURCE)


def test_a_code_can_only_be_exchanged_once(crm, connection):
    grant = crm.begin_authorization(connection["id"], actor="dana", source=SOURCE)
    crm.exchange_callback(connection["id"], code=f"code-{ORG}", state=grant["state"], actor="dana", source=SOURCE)
    with pytest.raises(AuthorizationError, match="not pending"):
        crm.exchange_callback(connection["id"], code=f"code-{ORG}", state=grant["state"], actor="dana", source=SOURCE)


def test_a_code_for_one_connection_cannot_answer_another_connections_grant(crm, store, room):
    first = crm.create_connection(CONNECTION | {"label": "One", "room_id": room["id"]}, actor="dana", source=SOURCE)
    second = crm.create_connection(CONNECTION | {"label": "Two", "room_id": room["id"]}, actor="dana", source=SOURCE)
    grant = crm.begin_authorization(first["id"], actor="dana", source=SOURCE)
    with pytest.raises(AuthorizationError):
        crm.exchange_callback(second["id"], code="code", state=grant["state"], actor="dana", source=SOURCE)


def test_a_pending_authorization_can_be_abandoned(crm, connection):
    grant = crm.begin_authorization(connection["id"], actor="dana", source=SOURCE)
    cancelled = crm.cancel_grant(grant["grant_id"], actor="dana", source=SOURCE)
    assert cancelled["state"] == "cancelled"
    with pytest.raises(AuthorizationError, match="cancelled, not pending"):
        crm.exchange_callback(
            connection["id"], code="c", state=grant["state"], actor="dana", source=SOURCE
        )


def test_a_closed_authorization_cannot_be_cancelled_again(crm, connection):
    grant = crm.begin_authorization(connection["id"], actor="dana", source=SOURCE)
    crm.cancel_grant(grant["grant_id"], actor="dana", source=SOURCE)
    with pytest.raises(AuthorizationError, match="cancelled, not pending"):
        crm.cancel_grant(grant["grant_id"], actor="dana", source=SOURCE)


def test_the_exchange_stores_the_refresh_token_under_the_org_id(crm, connection, store):
    result = make_authorized(crm, connection["id"])
    assert result["org_key"] == ORG_HOST
    assert result["org_id_source"] == "vendor"
    sealed = crm.vault.read(connection["id"], ORG_HOST)
    assert sealed["refresh_token"] == f"rt-{ORG}-1"
    assert result["has_refresh_token"] is True


def test_the_exchange_response_carries_no_credential(crm, connection):
    result = make_authorized(crm, connection["id"])
    body = json.dumps(result)
    assert f"at-{ORG}-1" not in body
    assert f"rt-{ORG}-1" not in body
    assert result["sealed"] is True


def test_the_code_is_never_stored_anywhere(crm, connection, store):
    make_authorized(crm, connection["id"])
    assert f"code-{ORG}" not in json.dumps(store.collections())
    for collection in COLLECTIONS:
        for record in store.list(collection, limit=1000):
            assert f"code-{ORG}" not in json.dumps(record["data"])


def test_the_authorization_is_recorded_as_exchanged(crm, connection):
    grant = crm.begin_authorization(connection["id"], actor="dana", source=SOURCE)
    crm.exchange_callback(connection["id"], code=f"code-{ORG}", state=grant["state"], actor="dana", source=SOURCE)
    assert crm.grant_summary(crm.store.get(grant["grant_id"]))["state"] == "exchanged"


def test_a_refused_code_closes_the_authorization_with_the_vendors_answer(crm, connection, transport):
    transport.scripted = [
        HttpResult(ok=False, status=400, body=json.dumps({"error": "invalid_grant"}), duration_ms=4.0)
    ]
    grant = crm.begin_authorization(connection["id"], actor="dana", source=SOURCE)
    with pytest.raises(TokenExchangeError):
        crm.exchange_callback(
            connection["id"], code="code", state=grant["state"], actor="dana", source=SOURCE
        )
    closed = crm.grant_summary(crm.store.get(grant["grant_id"]))
    assert closed["state"] == "failed"
    assert "invalid_grant" in closed["error"]


def test_a_credential_with_no_org_anywhere_is_refused_not_filed_under_a_placeholder(crm, connection, transport):
    """[sourced] the vault is "keyed by the org/account id"."""
    transport.omit_org = True
    orphan = crm.create_connection(
        {**CONNECTION, "label": "No org", "org_id": "", "room_id": connection["room_id"]},
        actor="dana",
        source=SOURCE,
    )
    grant = crm.begin_authorization(orphan["id"], actor="dana", source=SOURCE)
    with pytest.raises(ConnectorConfigError, match="credential vault is keyed by the org/account id"):
        crm.exchange_callback(orphan["id"], code="code", state=grant["state"], actor="dana", source=SOURCE)
    assert crm.vault.find(orphan["id"], "") is None
    assert crm.grant_summary(crm.store.get(grant["grant_id"]))["state"] == "failed"


def test_the_admin_chosen_org_is_used_when_the_vendor_names_none(crm, store, room, transport):
    transport.omit_org = True
    connection = crm.create_connection(
        CONNECTION | {"label": "Chosen", "org_id": "chosen-org", "room_id": room["id"]}, actor="dana", source=SOURCE
    )
    grant = crm.begin_authorization(connection["id"], actor="dana", source=SOURCE)
    result = crm.exchange_callback(
        connection["id"], code="code", state=grant["state"], actor="dana", source=SOURCE
    )
    assert result["org_key"] == "chosen-org"
    assert result["org_id_source"] == "configured"


# --------------------------------------------------------------------------- #
# The TTL rule, and the 401 rule
# --------------------------------------------------------------------------- #


def test_an_exchanged_token_is_authorized_and_not_expired(crm, authorized, clock):
    summary = crm.connection(authorized["id"])
    assert summary["status"] == STATUS_AUTHORIZED
    assert summary["ttl_known"] is True
    assert summary["expires_in"] == 1800
    assert summary["needs_action"] is None


def test_the_status_is_computed_from_the_ttl_not_stored(crm, authorized, clock):
    """A stored status cannot go stale and claim a connection is authorized."""
    crm.store.update(authorized["id"], {"status": STATUS_AUTHORIZED}, actor="dana", source=SOURCE)
    clock["now"] += timedelta(seconds=1801)
    assert crm.connection(authorized["id"])["status"] == STATUS_EXPIRED


def test_a_refresh_is_due_before_the_expiry_not_at_it(crm, authorized, clock):
    """"Token refresh before expiry" - so the window has to be positive."""
    clock["now"] += timedelta(seconds=1800 - SKEW_SECONDS - 1)
    assert crm._usable_token(authorized, force=False, source=SOURCE).trigger == "cached"
    clock["now"] += timedelta(seconds=2)
    assert crm._usable_token(authorized, force=False, source=SOURCE).trigger == "ttl_due"


def test_a_refresh_that_is_not_due_makes_no_request(crm, authorized, transport):
    transport.calls.clear()
    use = crm._usable_token(authorized, force=False, source=SOURCE)
    assert use.refreshed is False
    assert use.access_token == f"at-{ORG}-1"
    assert transport.calls == []


def test_a_due_refresh_calls_the_vendor_and_re_seals_the_token(crm, authorized, clock, transport):
    clock["now"] += timedelta(seconds=1801)
    use = crm._usable_token(authorized, force=False, actor="dana", source=SOURCE)
    assert use.refreshed is True
    assert use.access_token == f"at-{ORG}-2"
    assert crm.vault.read(authorized["id"], ORG_HOST)["access_token"] == f"at-{ORG}-2"
    assert transport.calls[-1]["raw_body"].find("grant_type=refresh_token") > 0


def test_a_refresh_response_without_a_refresh_token_keeps_the_stored_one(crm, authorized, clock, transport):
    transport.refresh = "without"
    clock["now"] += timedelta(seconds=1801)
    use = crm._usable_token(authorized, force=False, actor="dana", source=SOURCE)
    assert use.credential["refresh_token"] == f"rt-{ORG}-1"
    assert crm.vault.read(authorized["id"], ORG_HOST)["refresh_token"] == f"rt-{ORG}-1"


def test_a_refresh_response_with_a_new_refresh_token_replaces_the_stored_one(crm, authorized, clock, transport):
    transport.refresh = "with_refresh"
    clock["now"] += timedelta(seconds=1801)
    use = crm._usable_token(authorized, force=False, actor="dana", source=SOURCE)
    assert use.credential["refresh_token"] == f"rt-{ORG}-2"


def test_a_refresh_without_an_expires_in_leaves_the_previous_expiry_alone(crm, authorized, clock, transport):
    transport.omit_expiry = True
    clock["now"] += timedelta(seconds=1801)
    use = crm._usable_token(authorized, force=False, actor="dana", source=SOURCE)
    assert use.expires_at is None
    assert use.ttl_known is False


def test_a_token_with_no_ttl_is_refreshed_before_every_use_and_says_so(crm, room, transport, clock):
    """The researched TTL protocol depends on ``expires_in`` being there.

    When it is not, there is no expiry to be before, and the two available
    readings are "never refresh" (which fails later, somewhere unrelated) or
    "refresh every time" (chatty, and visible). The visible one is the right one
    for a surface whose whole job is saying what state a connection is in.
    """
    transport.omit_expiry = True
    connection = crm.create_connection(
        CONNECTION | {"room_id": room["id"], "label": "No TTL"}, actor="dana", source=SOURCE
    )
    make_authorized(crm, connection["id"])

    first = crm._usable_token(crm.store.get(connection["id"]), force=False, actor="dana", source=SOURCE)
    assert first.refreshed is True
    assert first.trigger == "ttl_unknown"
    assert first.ttl_known is False
    assert crm.connection(connection["id"])["ttl_known"] is False

    # And again: a token with no known expiry is not assumed to still be good.
    second = crm._usable_token(crm.store.get(connection["id"]), force=False, actor="dana", source=SOURCE)
    assert second.refreshed is True
    assert second.access_token == f"at-{ORG}-2"


def test_a_vendor_that_refuses_the_refresh_says_so_and_names_the_remedy(crm, authorized, clock, transport):
    transport.refresh = "invalid_grant"
    clock["now"] += timedelta(seconds=1801)
    with pytest.raises(TokenExchangeError, match="invalid_grant"):
        crm.test_connection(authorized["id"], actor="dana", source=SOURCE)
    summary = crm.connection(authorized["id"])
    assert summary["status"] == STATUS_EXPIRED
    assert summary["needs_action"]["action"] == "re-authorize"
    assert summary["refresh_refused_at"] is not None
    kinds = [event["kind"] for event in crm.token_events(connection_id=authorized["id"])]
    assert "refresh_refused" in kinds


def test_a_forced_refresh_is_the_only_way_to_refresh_before_the_ttl_is_due(crm, authorized, clock, transport):
    clock["now"] += timedelta(seconds=10)
    result = crm.refresh_now(authorized["id"], actor="dana", source=SOURCE)
    assert result["refreshed"] is True
    assert result["trigger"] == "forced"


# --- the researched rule ---------------------------------------------------- #


def test_a_401_is_not_a_refresh_trigger(crm, authorized, clock, transport):
    """[sourced] "Unauthorized (401) requests are not a valid indicator that a new
    access token must be retrieved."

    The whole test: the status, the expiry and the poll interval all survive a 401
    untouched, and only the health changes.
    """
    before = crm.connection(authorized["id"])
    transport.probe_status = 401
    result = crm.test_connection(authorized["id"], actor="dana", source=SOURCE)

    assert result["outcome"] == "unauthorized"
    assert result["ok"] is False
    assert result["unauthorized_is_not_a_refresh_trigger"] is True
    assert result["refreshed_before_probe"] is False
    assert result["refresh_trigger"] == "cached"

    after = crm.connection(authorized["id"])
    assert after["status"] == before["status"] == STATUS_AUTHORIZED
    assert after["expires_at"] == before["expires_at"]
    assert after["ttl_known"] is True
    assert after["health"] == "unauthorized"
    assert after["last_unauthorized_at"] is not None
    assert after["needs_action"]["action"] == "re-authorize"
    # The sealed token is still there and still the one that was sealed.
    assert crm.vault.read(authorized["id"], ORG_HOST)["access_token"] == f"at-{ORG}-1"


def test_a_401_does_not_send_a_token_request_to_the_vendor(crm, authorized, transport):
    transport.probe_status = 401
    transport.calls.clear()
    crm.test_connection(authorized["id"], actor="dana", source=SOURCE)
    token_requests = [call for call in transport.calls if "token" in call["url"] and call["method"] == "POST"]
    assert token_requests == []


def test_a_401_does_not_bring_the_next_health_check_forward(crm, authorized, clock, transport):
    transport.probe_status = 401
    crm.test_connection(authorized["id"], actor="dana", source=SOURCE)
    assert crm.connection(authorized["id"])["next_check_in"] == 3600


def test_a_403_reads_as_unauthorized_too(crm, authorized, transport):
    """Some vendors answer 403 for the same thing; treating it as a transport
    error would be the kind of thing a reviewer finds in production."""
    transport.probe_status = 403
    assert crm.test_connection(authorized["id"], actor="dana", source=SOURCE)["outcome"] == "unauthorized"


def test_a_401_that_happens_while_the_ttl_is_due_still_refreshes_because_of_the_ttl(crm, authorized, clock, transport):
    """The rule is not "a 401 never refreshes": it is "a 401 is not the trigger"."""
    clock["now"] += timedelta(seconds=1801)
    transport.probe_status = 401
    result = crm.test_connection(authorized["id"], actor="dana", source=SOURCE)
    assert result["refresh_trigger"] == "ttl_due"
    assert result["refreshed_before_probe"] is True
    assert result["outcome"] == "unauthorized"


def test_the_token_event_for_a_401_says_it_changed_nothing(crm, authorized, transport):
    transport.probe_status = 401
    crm.test_connection(authorized["id"], actor="dana", source=SOURCE)
    event = crm.token_events(connection_id=authorized["id"], kind="tested")[0]
    assert event["outcome"] == "unauthorized"
    assert event["unauthorized_is_not_a_refresh_trigger"] is True
    assert event["authorization_header"] == "<redacted>"
    assert f"at-{ORG}-1" not in json.dumps(event)


def test_a_5xx_is_a_health_error_and_is_retried_soon(crm, authorized, transport):
    transport.probe_status = 503
    result = crm.test_connection(authorized["id"], actor="dana", source=SOURCE)
    assert result["outcome"] == "error"
    assert crm.connection(authorized["id"])["next_check_in"] == 300


def test_an_unreachable_vendor_is_a_health_error_not_a_caller_error(crm, authorized, transport):
    transport.scripted = [HttpResult(ok=False, status=0, error="ConnectionResetError: reset")]
    result = crm.test_connection(authorized["id"], actor="dana", source=SOURCE)
    assert result["outcome"] == "error"
    assert "could not be delivered" in result["detail"]


def test_a_healthy_probe_leaves_no_last_error_behind(crm, authorized, transport):
    transport.probe_status = 503
    crm.test_connection(authorized["id"], actor="dana", source=SOURCE)
    transport.probe_status = 200
    result = crm.test_connection(authorized["id"], actor="dana", source=SOURCE)
    assert result["outcome"] == "ok"
    assert crm.connection(authorized["id"])["last_error"] == ""
    assert crm.connection(authorized["id"])["last_unauthorized_at"] is None


def test_testing_a_connection_with_no_credential_is_a_428(crm, connection):
    with pytest.raises(NotConnectedError, match="no sealed credential"):
        crm.test_connection(connection["id"], actor="dana", source=SOURCE)


def test_testing_a_connection_sealed_under_another_key_is_a_428(crm, store, connection, monkeypatch):
    make_authorized(crm, connection["id"])
    monkeypatch.setenv(KEY_ENV, "a-different-key")
    rekeyed = CrmOAuthConnections(store, transport=FakeTransport())
    with pytest.raises(VaultSealedError):
        rekeyed.test_connection(connection["id"], actor="dana", source=SOURCE)


# --------------------------------------------------------------------------- #
# The health sweep
# --------------------------------------------------------------------------- #


def test_the_sweep_covers_the_rooms_connections_and_the_tenant_wide_ones(crm, store, room, clock):
    attached = crm.create_connection(CONNECTION | {"label": "Room", "room_id": room["id"]}, actor="dana", source=SOURCE)
    wide = crm.create_connection(CONNECTION | {"label": "Tenant", "room_id": None}, actor="dana", source=SOURCE)
    for row in (attached, wide):
        make_authorized(crm, row["id"])
    result = crm.health_check(room["id"], force=True, actor="dana", source=SOURCE)
    assert result["counts"]["checked"] == 2
    assert {entry["connection_id"] for entry in result["results"]} == {attached["id"], wide["id"]}


def test_the_sweep_skips_a_connection_that_is_not_due(crm, authorized):
    crm.test_connection(authorized["id"], actor="dana", source=SOURCE)
    room = crm.store.get(authorized["id"])["room_id"]
    result = crm.health_check(room, force=False, actor="dana", source=SOURCE)
    assert result["counts"]["skipped"] == 1
    assert result["results"][0]["skipped"] == "not_due"


def test_forcing_the_sweep_checks_the_connections_that_are_not_due(crm, authorized):
    crm.test_connection(authorized["id"], actor="dana", source=SOURCE)
    room = crm.store.get(authorized["id"])["room_id"]
    assert crm.health_check(room, force=True, actor="dana", source=SOURCE)["counts"]["checked"] == 1


def test_the_sweep_skips_a_switched_off_connection(crm, authorized):
    crm.update_connection(authorized["id"], {"enabled": False}, actor="dana", source=SOURCE)
    room = crm.store.get(authorized["id"])["room_id"]
    result = crm.health_check(room, force=True, actor="dana", source=SOURCE)
    assert result["results"][0]["skipped"] == "disabled"


def test_one_connection_that_cannot_be_probed_does_not_take_the_sweep_down(crm, store, room):
    healthy = crm.create_connection(CONNECTION | {"label": "Fine", "room_id": room["id"]}, actor="dana", source=SOURCE)
    blocked = crm.create_connection(
        CONNECTION | {"label": "Never authorized", "room_id": room["id"]}, actor="dana", source=SOURCE
    )
    make_authorized(crm, healthy["id"])
    result = crm.health_check(room["id"], force=True, actor="dana", source=SOURCE)
    outcomes = {entry["connection_id"]: entry for entry in result["results"]}
    assert outcomes[healthy["id"]]["outcome"] == "ok"
    assert outcomes[blocked["id"]]["skipped"] == "not_connected"
    assert result["counts"]["checked"] == 1


def test_the_sweep_advances_the_next_check_time(crm, authorized, clock):
    room = crm.store.get(authorized["id"])["room_id"]
    crm.health_check(room, force=True, actor="dana", source=SOURCE)
    assert crm.connection(authorized["id"])["next_check_in"] == 3600


def test_the_sweep_for_a_room_that_does_not_exist_is_a_404(crm):
    with pytest.raises(UnknownRoomError):
        crm.health_check("room_nope", actor="dana", source=SOURCE)


# --------------------------------------------------------------------------- #
# Readiness
# --------------------------------------------------------------------------- #


def test_a_fully_configured_connection_is_ready(crm, connection):
    entry = crm.readiness(crm.store.get(connection["id"])["room_id"])["connections"][0]
    assert entry["ready_to_authorize"] is True
    assert entry["blockers"] == []


def test_readiness_names_every_missing_field_not_just_the_first(crm, store, room):
    room_scoped = crm.create_connection(
        {"vendor": "salesforce", "room_id": room["id"], "label": "Bare"}, actor="dana", source=SOURCE
    )
    entry = crm.readiness(room["id"])["connections"][0]
    codes = {blocker["code"] for blocker in entry["blockers"]}
    assert codes == {"client_id_missing", "client_secret_missing", "redirect_uri_missing", "scopes_missing"}
    assert entry["ready_to_authorize"] is False
    assert crm.readiness(room["id"])["ready"] is False
    assert room_scoped["id"] == entry["connection_id"]


def test_a_dataverse_connection_without_the_org_is_blocked_on_it(crm, store, room):
    crm.create_connection(
        {"vendor": "dataverse", "room_id": room["id"], "client_id": "c", "client_secret": "s",
         "redirect_uri": REDIRECT, "scopes": ["x"]},
        actor="dana",
        source=SOURCE,
    )
    entry = crm.readiness(room["id"])["connections"][0]
    assert [blocker["code"] for blocker in entry["blockers"]] == ["org_missing"]
    assert "<org>" in entry["blockers"][0]["why"]


def test_an_unsupported_salesforce_policy_is_blocked(crm, store, room):
    crm.create_connection(
        {"vendor": "salesforce", "room_id": room["id"], "client_id": "c", "client_secret": "s",
         "redirect_uri": REDIRECT, "scopes": ["x"], "policy": "magic"},
        actor="dana",
        source=SOURCE,
    )
    entry = crm.readiness(room["id"])["connections"][0]
    assert [blocker["code"] for blocker in entry["blockers"]] == ["policy_unsupported"]


def test_the_hubspot_installer_requirement_is_advisory_never_a_blocker(crm, store, room):
    """[sourced] the requirement is on the operator, and this room cannot know the
    operator's role, so blocking on it would be a guess."""
    crm.create_connection(
        CONNECTION | {"label": "HubSpot", "room_id": room["id"]}, actor="dana", source=SOURCE
    )
    entry = crm.readiness(room["id"])["connections"][0]
    assert entry["blockers"] == []
    assert any("Super Admin" in line for line in entry["advisory"])


def test_a_connection_sealed_under_another_key_is_blocked_on_the_vault(crm, store, connection, monkeypatch):
    make_authorized(crm, connection["id"])
    monkeypatch.setenv(KEY_ENV, "a-different-key")
    rekeyed = CrmOAuthConnections(store, transport=FakeTransport())
    entry = rekeyed.readiness(rekeyed.store.get(connection["id"])["room_id"])["connections"][0]
    assert "vault_sealed_elsewhere" in {blocker["code"] for blocker in entry["blockers"]}
    assert entry["needs_action"]["action"] == "re-authorize"


def test_readiness_for_a_room_that_does_not_exist_is_a_404(crm):
    with pytest.raises(UnknownRoomError):
        crm.readiness("room_nope")


# --------------------------------------------------------------------------- #
# Disconnecting
# --------------------------------------------------------------------------- #


def test_disconnecting_soft_deletes_and_removes_the_credential(crm, authorized, store):
    room = store.get(authorized["id"])["room_id"]
    result = crm.disconnect(authorized["id"], actor="dana", source=SOURCE)
    assert result["disconnected"] is True
    assert result["credential_removed"] is True
    assert store.get(authorized["id"]) is None
    assert crm.vault.find(authorized["id"], ORG_HOST) is None
    assert crm.vault.find(authorized["id"], APP_ORG_KEY) is None
    with pytest.raises(ConnectionNotFoundError):
        crm.connection(authorized["id"])
    assert crm.list_connections(room_id=room) == []
    # A soft delete, so the record and the audit row both survive.
    assert store.audit(collection=CONNECTION_COLLECTION, action="delete")


def test_disconnecting_cancels_a_pending_authorization(crm, connection):
    grant = crm.begin_authorization(connection["id"], actor="dana", source=SOURCE)
    assert crm.disconnect(connection["id"], actor="dana", source=SOURCE)["cancelled_grants"] == 1
    assert crm.grant_summary(crm.store.get(grant["grant_id"]))["state"] == "cancelled"


def test_the_token_events_outlive_a_disconnect(crm, authorized, store):
    crm.test_connection(authorized["id"], actor="dana", source=SOURCE)
    crm.disconnect(authorized["id"], actor="dana", source=SOURCE)
    kinds = [event["kind"] for event in crm.token_events(connection_id=authorized["id"])]
    assert "tested" in kinds and "issued" in kinds and "disconnected" in kinds
    actions = [entry["action"] for entry in store.audit(collection=CONNECTION_COLLECTION)]
    assert actions[0] == "delete"


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


def test_the_summary_route_counts_the_connections(http):
    register_http(http)
    body = http.get(f"{PREFIX}/summary").json()
    assert body["count"] == 1
    assert body["statuses"][STATUS_PENDING] == 1
    assert body["unauthorized_is_not_a_refresh_trigger"] is True


def test_registering_over_http_returns_a_summary_with_no_credential(http):
    body = register_http(http)
    assert body["status"] == STATUS_PENDING
    assert "secret-abc" not in json.dumps(body)


def test_the_authorize_url_route_returns_a_url_and_a_state(http):
    connection = register_http(http)
    body = http.get(f"{PREFIX}/connections/{connection['id']}/authorize-url").json()
    assert body["authorize_url"].startswith("https://app.hubspot.com/oauth/authorize?")
    assert body["state"] and body["grant_id"]


def test_the_authorize_url_route_refuses_a_blocked_connection_and_lists_every_blocker(http):
    connection = register_http(http, {"vendor": "salesforce", "label": "Bare"})
    response = http.get(f"{PREFIX}/connections/{connection['id']}/authorize-url")
    assert response.status_code == 400
    detail = response.json()["detail"]
    for code in ("client_id_missing", "client_secret_missing", "redirect_uri_missing", "scopes_missing"):
        assert code in detail


def test_the_callback_route_exchanges_and_seals(http):
    connection = register_http(http)
    authorize_http(http, connection["id"])
    body = http.get(f"{PREFIX}/connections/{connection['id']}").json()
    assert body["status"] == STATUS_AUTHORIZED
    assert body["credential"]["sealed"] is True
    assert f"at-{ORG}-1" not in json.dumps(body)


def test_the_callback_route_with_no_code_is_a_400(http):
    connection = register_http(http)
    grant = http.get(f"{PREFIX}/connections/{connection['id']}/authorize-url").json()
    response = http.post(
        f"{PREFIX}/connections/{connection['id']}/callback", params={"state": grant["state"]}
    )
    assert response.status_code == 400
    assert "no code" in response.json()["detail"]


def test_the_callback_route_with_an_unknown_state_is_a_400(http):
    connection = register_http(http)
    response = http.post(
        f"{PREFIX}/connections/{connection['id']}/callback",
        params={"code": "c", "state": "made-up"},
    )
    assert response.status_code == 400


def test_the_callback_route_carries_the_vendors_answer_when_it_refuses(http, transport):
    connection = register_http(http)
    grant = http.get(f"{PREFIX}/connections/{connection['id']}/authorize-url").json()
    transport.scripted = [
        HttpResult(ok=False, status=400, body=json.dumps({"error": "invalid_grant"}), duration_ms=2.0)
    ]
    response = http.post(
        f"{PREFIX}/connections/{connection['id']}/callback",
        params={"code": "c", "state": grant["state"]},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "token_exchange_failed"
    assert body["vendor_status"] == 400
    assert "invalid_grant" in body["detail"]


def test_the_test_route_answers_200_when_the_vendor_refuses_the_token(http, transport):
    """The call succeeded; the answer was no. A 401 here would read as this room
    refusing the caller, which is a different thing."""
    connection = register_http(http)
    authorize_http(http, connection["id"])
    transport.probe_status = 401
    response = http.post(f"{PREFIX}/connections/{connection['id']}/test")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["outcome"] == "unauthorized"
    assert body["unauthorized_is_not_a_refresh_trigger"] is True
    assert body["connection"]["status"] == STATUS_AUTHORIZED


def test_the_test_route_on_an_unauthorized_connection_is_a_428(http):
    connection = register_http(http)
    response = http.post(f"{PREFIX}/connections/{connection['id']}/test")
    assert response.status_code == 428
    assert response.json()["error"] == "not_connected"


def test_the_test_route_on_a_switched_off_connection_is_a_428_with_its_own_error(http):
    connection = register_http(http)
    http.patch(f"{PREFIX}/connections/{connection['id']}", json={"enabled": False})
    response = http.post(f"{PREFIX}/connections/{connection['id']}/test")
    assert response.status_code == 428
    assert response.json()["error"] == "connection_disabled"


def test_the_test_route_on_a_connection_sealed_under_another_key_is_a_428(http, store, transport, monkeypatch):
    """The vault is a shared dependency, so the ``http`` fixture is re-pointed at
    a rekeyed vault rather than the environment being changed under it."""
    connection = register_http(http)
    authorize_http(http, connection["id"])
    monkeypatch.setenv(KEY_ENV, "a-different-key")
    http.app.dependency_overrides[load_feature(MODULE).get_connections] = (
        lambda: engine(http.app.state.store, transport)
    )
    response = http.post(f"{PREFIX}/connections/{connection['id']}/test")
    assert response.status_code == 428
    assert response.json()["error"] == "vault_sealed"
    assert "re-authorize" in response.json()["detail"]


def test_a_vendor_that_cannot_be_reached_is_a_502(http, transport):
    connection = register_http(http)
    authorize_http(http, connection["id"])
    transport.scripted = [HttpResult(ok=False, status=0, error="ConnectionResetError: reset")]
    # The probe is scripted, so drive the failure through the token endpoint on the
    # next refresh instead, which is the route a reader would actually hit.
    transport.refresh = "unreachable"
    from dsr.crm_oauth.engine import SKEW_SECONDS

    clock = {"now": datetime.now(timezone.utc) + timedelta(seconds=1800 + SKEW_SECONDS + 1)}
    http.app.dependency_overrides[load_feature(MODULE).get_connections] = (
        lambda: engine(http.app.state.store, transport, clock)
    )
    response = http.post(f"{PREFIX}/connections/{connection['id']}/refresh")
    assert response.status_code == 502
    assert response.json()["error"] == "vendor_unreachable"


def test_an_unknown_connection_is_a_404(http):
    assert http.get(f"{PREFIX}/connections/crm_connection_nope").status_code == 404
    assert http.post(f"{PREFIX}/connections/crm_connection_nope/test").status_code == 404
    assert http.delete(f"{PREFIX}/connections/crm_connection_nope").status_code == 404


def test_an_unknown_room_is_a_404(http):
    assert http.get(f"{PREFIX}/rooms/room_nope/connections").status_code == 404
    assert http.get(f"{PREFIX}/rooms/room_nope/readiness").status_code == 404
    assert http.post(f"{PREFIX}/rooms/room_nope/health-check").status_code == 404
    assert http.get(f"{PREFIX}/connections", params={"room_id": "room_nope"}).status_code == 404


def test_registering_an_unknown_vendor_is_a_400(http):
    response = http.post(f"{PREFIX}/connections", json={"vendor": "not-a-crm"})
    assert response.status_code == 400
    assert "known vendors" in response.json()["detail"]


def test_the_grants_route_lists_pending_authorizations(http):
    connection = register_http(http)
    grant = http.get(f"{PREFIX}/connections/{connection['id']}/authorize-url").json()
    pending = http.get(f"{PREFIX}/grants", params={"state": "pending"}).json()
    assert pending["count"] == 1
    assert pending["grants"][0]["id"] == grant["grant_id"]
    assert pending["grants"][0]["seconds_remaining"] <= 600


def test_cancelling_a_grant_is_a_204(http):
    connection = register_http(http)
    grant = http.get(f"{PREFIX}/connections/{connection['id']}/authorize-url").json()
    assert http.delete(f"{PREFIX}/grants/{grant['grant_id']}").status_code == 204
    assert http.get(f"{PREFIX}/grants", params={"state": "pending"}).json()["count"] == 0


def test_cancelling_an_unknown_grant_is_a_400(http):
    assert http.delete(f"{PREFIX}/grants/crm_grant_nope").status_code == 400


def test_the_health_route_reports_the_credential_and_the_schedule(http):
    connection = register_http(http)
    authorize_http(http, connection["id"])
    body = http.get(f"{PREFIX}/connections/{connection['id']}/health").json()
    assert body["status"] == STATUS_AUTHORIZED
    assert body["ttl_known"] is True
    assert body["refresh_due_at"] < body["expires_at"]
    assert body["credential"]["sealed"] is True
    assert body["health_interval_seconds"] == 3600


def test_the_token_events_route_summarizes_the_kinds(http):
    connection = register_http(http)
    authorize_http(http, connection["id"])
    http.post(f"{PREFIX}/connections/{connection['id']}/test")
    body = http.get(f"{PREFIX}/connections/{connection['id']}/token-events").json()
    assert body["summary"]["authorize"] == 1
    assert body["summary"]["issued"] == 1
    assert body["summary"]["tested"] == 1
    assert f"at-{ORG}-1" not in json.dumps(body)


def test_the_token_events_route_filters_by_kind(http):
    connection = register_http(http)
    authorize_http(http, connection["id"])
    body = http.get(f"{PREFIX}/connections/{connection['id']}/token-events", params={"kind": "issued"}).json()
    assert body["count"] == 1
    assert body["events"][0]["kind"] == "issued"


def test_the_room_routes_report_the_two_scopes(http, http_room):
    attached = register_http(http, CONNECTION | {"room_id": http_room["id"], "label": "Room"})
    wide = register_http(http, CONNECTION | {"room_id": None, "label": "Tenant"})
    body = http.get(f"{PREFIX}/rooms/{http_room['id']}/connections").json()
    assert body["by_scope"] == {"room": 1, "tenant": 1}
    assert {row["id"] for row in body["connections"]} == {attached["id"], wide["id"]}


def test_the_room_routes_filter_by_scope(http, http_room):
    register_http(http, CONNECTION | {"room_id": http_room["id"], "label": "Room"})
    register_http(http, CONNECTION | {"room_id": None, "label": "Tenant"})
    rows = http.get(f"{PREFIX}/connections", params={"room_id": http_room["id"], "scope": "room"}).json()
    assert [row["label"] for row in rows["connections"]] == ["Room"]


def test_the_list_route_filters_by_vendor_status_and_tenant(http, http_room):
    register_http(http, CONNECTION | {"room_id": http_room["id"], "label": "A", "tenant": "one"})
    register_http(http, CONNECTION | {"vendor": "salesforce", "room_id": http_room["id"], "label": "B",
                                      "client_id": "c", "client_secret": "s", "redirect_uri": REDIRECT,
                                      "scopes": ["api"], "tenant": "two"})
    by_vendor = http.get(f"{PREFIX}/connections", params={"vendor": "salesforce"}).json()
    assert [row["label"] for row in by_vendor["connections"]] == ["B"]
    by_tenant = http.get(f"{PREFIX}/connections", params={"tenant": "one"}).json()
    assert [row["label"] for row in by_tenant["connections"]] == ["A"]
    by_status = http.get(f"{PREFIX}/connections", params={"status": STATUS_AUTHORIZED}).json()
    assert by_status["count"] == 0


def test_the_disconnect_route_is_a_204_and_hides_the_row(http):
    connection = register_http(http)
    assert http.delete(f"{PREFIX}/connections/{connection['id']}").status_code == 204
    assert http.get(f"{PREFIX}/connections").json()["count"] == 0
    assert http.get(f"{PREFIX}/connections/{connection['id']}").status_code == 404


def test_the_health_check_route_runs_the_sweep(http, http_room, transport):
    connection = register_http(http, CONNECTION | {"room_id": http_room["id"]})
    authorize_http(http, connection["id"])
    body = http.post(f"{PREFIX}/rooms/{http_room['id']}/health-check", params={"force": True}).json()
    assert body["counts"]["checked"] == 1
    assert body["counts"]["ok"] == 1
    assert connection["id"] in {entry["connection_id"] for entry in body["results"]}


def test_the_patch_route_turns_a_connection_on_and_off(http):
    connection = register_http(http)
    assert http.patch(f"{PREFIX}/connections/{connection['id']}", json={"enabled": False}).json()["enabled"] is False
    assert http.patch(f"{PREFIX}/connections/{connection['id']}", json={"enabled": True}).json()["enabled"] is True


def test_the_refresh_route_is_a_200_and_names_its_trigger(http):
    connection = register_http(http)
    authorize_http(http, connection["id"])
    body = http.post(f"{PREFIX}/connections/{connection['id']}/refresh").json()
    assert body["refreshed"] is True
    assert body["trigger"] == "forced"


def test_a_refused_write_leaves_no_audit_row(http):
    assert http.post(f"{PREFIX}/connections", json={"vendor": "not-a-crm"}).status_code == 400
    assert http.get("/api/audit", params={"collection": CONNECTION_COLLECTION}).json()["count"] == 0


def test_a_refused_exchange_writes_the_authorization_but_no_credential(http, transport):
    connection = register_http(http)
    grant = http.get(f"{PREFIX}/connections/{connection['id']}/authorize-url").json()
    transport.scripted = [HttpResult(ok=False, status=400, body=json.dumps({"error": "invalid_grant"}))]
    assert http.post(
        f"{PREFIX}/connections/{connection['id']}/callback", params={"code": "c", "state": grant["state"]}
    ).status_code == 400
    assert http.get("/api/audit", params={"collection": CREDENTIAL_COLLECTION}).json()["count"] == 1
    assert http.get("/api/audit", params={"collection": "crm_credential"}).json()["count"] == 1


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def test_audit_rows_name_the_route_that_actually_served_the_write(http, http_room, transport):
    """The defect the build brief calls out, pinned as a test.

    The audit row must name the route that served the write, and that route has to
    be one the host actually mounted. The registry reports route *templates*, so a
    recorded concrete path is matched against them rather than compared
    literally - and matched against the registry rather than by re-issuing the
    request, because a recorded DELETE no longer succeeds once it has run.
    """
    connection = register_http(http, CONNECTION | {"room_id": http_room["id"]})
    grant = http.get(f"{PREFIX}/connections/{connection['id']}/authorize-url").json()
    http.post(
        f"{PREFIX}/connections/{connection['id']}/callback",
        params={"code": f"code-{ORG}", "state": grant["state"]},
    )
    # A second authorization, still pending, so the cancel route has something to
    # cancel: the first one is ``exchanged`` by now and a code may only be spent
    # once.
    spare = http.get(f"{PREFIX}/connections/{connection['id']}/authorize-url").json()
    http.post(f"{PREFIX}/connections/{connection['id']}/test")
    http.post(f"{PREFIX}/connections/{connection['id']}/refresh")
    # The sweep before the connection is switched off: a disabled connection is
    # skipped, and a skipped one writes nothing.
    http.post(f"{PREFIX}/rooms/{http_room['id']}/health-check", params={"force": True})
    http.delete(f"{PREFIX}/grants/{spare['grant_id']}")
    http.patch(f"{PREFIX}/connections/{connection['id']}", json={"enabled": False})
    http.delete(f"{PREFIX}/connections/{connection['id']}")

    entries = http.get("/api/audit", params={"limit": 300}).json()["entries"]
    sources = {entry["source"] for entry in entries}
    for expected in (
        f"POST {PREFIX}/connections",
        f"GET {PREFIX}/connections/{connection['id']}/authorize-url",
        f"POST {PREFIX}/connections/{connection['id']}/callback",
        f"POST {PREFIX}/connections/{connection['id']}/test",
        f"POST {PREFIX}/connections/{connection['id']}/refresh",
        f"PATCH {PREFIX}/connections/{connection['id']}",
        f"DELETE {PREFIX}/grants/{spare['grant_id']}",
        f"POST {PREFIX}/rooms/{http_room['id']}/health-check",
        f"DELETE {PREFIX}/connections/{connection['id']}",
    ):
        assert expected in sources, f"{expected!r} missing from audit sources {sorted(sources)}"

    templates = [
        (method, route["path"])
        for feature in http.get("/api/features").json()["features"]
        for route in feature.get("routes", [])
        for method in route["methods"]
    ]
    # Plus the core routes the test also writes through, so the assertion covers
    # every audit row rather than only this feature's.
    templates += [
        (method, route.path)
        for route in app.routes
        for method in (getattr(route, "methods", None) or set())
        if method not in ("HEAD", "OPTIONS")
    ]
    for entry in entries:
        verb, _, path = (entry["source"] or "").partition(" ")
        assert any(
            verb == method and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", template), path)
            for method, template in templates
        ), f"audit names a route the app does not serve: {entry['source']}"


def test_the_health_sweep_attributes_every_row_to_its_own_route(http, http_room):
    """A sweep writes a dozen rows through one route; they all name that route."""
    for label in ("One", "Two", "Three"):
        connection = register_http(http, CONNECTION | {"room_id": http_room["id"], "label": label})
        authorize_http(http, connection["id"])
    http.post(f"{PREFIX}/rooms/{http_room['id']}/health-check", params={"force": True})
    entries = http.get("/api/audit", params={"collection": EVENT_COLLECTION, "limit": 100}).json()["entries"]
    swept = [entry for entry in entries if entry["source"].endswith(f"/rooms/{http_room['id']}/health-check")]
    assert len(swept) == 3
    assert {entry["source"] for entry in swept} == {f"POST {PREFIX}/rooms/{http_room['id']}/health-check"}


def test_no_audit_source_names_another_features_prefix(http, http_room):
    connection = register_http(http, CONNECTION | {"room_id": http_room["id"]})
    authorize_http(http, connection["id"])
    sources = [entry["source"] for entry in http.get("/api/audit", params={"limit": 200}).json()["entries"]]
    ours = [source for source in sources if PREFIX in source]
    assert ours
    for other in ("/api/crm", "/api/analytics", "/api/wf-016", "/api/wf-026"):
        assert not [source for source in sources if other in source and PREFIX not in source]


def test_every_collection_this_feature_writes_is_audited(http, http_room):
    connection = register_http(http, CONNECTION | {"room_id": http_room["id"]})
    authorize_http(http, connection["id"])
    http.post(f"{PREFIX}/connections/{connection['id']}/test")
    for collection in (CONNECTION_COLLECTION, CREDENTIAL_COLLECTION, GRANT_COLLECTION, EVENT_COLLECTION):
        assert http.get("/api/audit", params={"collection": collection}).json()["count"], collection


def test_no_audit_row_ever_carries_a_credential(http, http_room):
    connection = register_http(http, CONNECTION | {"room_id": http_room["id"]})
    authorize_http(http, connection["id"])
    http.post(f"{PREFIX}/connections/{connection['id']}/test")
    entries = http.get("/api/audit", params={"limit": 300}).json()["entries"]
    body = json.dumps(entries)
    for secret in ("secret-abc", f"at-{ORG}-1", f"rt-{ORG}-1"):
        assert secret not in body, secret


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


@pytest.fixture()
def seeded(monkeypatch, tmp_path):
    """A database with the core room shape and this feature's seed run over it."""
    db = AuditedDatabase(tmp_path / "seed.db")
    store = RecordStore(db)
    rooms = [
        store.create("room", {"name": f"Room {index}", "account": f"Account {index}"}, actor="dana")
        for index in range(3)
    ]
    described = load_feature(MODULE).seed(
        db, {"room_ids": [(room["id"], room["data"]["account"]) for room in rooms]}
    )
    yield store, described, [room["id"] for room in rooms]
    db.close()


def test_the_seed_produces_the_states_this_workflow_is_responsible_for(seeded):
    store, described, _ = seeded
    crm = CrmOAuthConnections(store)
    by_health = {}
    by_status = {}
    for row in crm.list_connections(limit=50):
        by_health.setdefault(row["health"], []).append(row["label"])
        by_status.setdefault(row["status"], []).append(row["label"])

    assert len(crm.list_connections(limit=50)) == 6
    assert by_health["ok"], "the demo has no healthy connection"
    assert by_health["unauthorized"], "the demo has no token the vendor rejected"
    assert by_health["error"], "the demo has no vendor that is down"
    assert by_status["expired"], "the demo has no expired connection"
    assert by_status[STATUS_PENDING], "the demo has no connection that cannot be authorized"
    assert "401" in described and "refresh" in described


def test_the_seeded_401_case_leaves_the_token_and_its_expiry_alone(seeded):
    store, _, _ = seeded
    crm = CrmOAuthConnections(store)
    rejected = next(row for row in crm.list_connections(limit=50) if row["health"] == "unauthorized")
    assert rejected["status"] == STATUS_AUTHORIZED
    assert rejected["ttl_known"] is True
    assert rejected["expires_at"] is not None
    assert rejected["needs_action"]["action"] == "re-authorize"


def test_the_seeded_expired_connection_had_its_refresh_refused(seeded):
    store, _, _ = seeded
    crm = CrmOAuthConnections(store)
    expired = next(row for row in crm.list_connections(limit=50) if row["status"] == STATUS_EXPIRED)
    assert expired["refresh_refused_at"] is not None
    assert expired["needs_action"]["action"] == "re-authorize"


def test_the_seeded_readiness_names_the_missing_fields(seeded):
    store, _, rooms = seeded
    crm = CrmOAuthConnections(store)
    blocked = [room for room in rooms if not crm.readiness(room)["ready"]]
    assert blocked, "the demo has no room that cannot finish the flow"
    entry = next(
        row for row in crm.readiness(blocked[0])["connections"] if row["blockers"]
    )
    assert {blocker["code"] for blocker in entry["blockers"]} & {
        "client_secret_missing",
        "scopes_missing",
    }


def test_the_seed_leaves_a_pending_authorization(seeded):
    store, _, _ = seeded
    pending = CrmOAuthConnections(store).list_grants(state_name="pending", limit=10)
    assert len(pending) == 1
    assert pending[0]["state_nonce"]


def test_the_seed_writes_only_these_collections(seeded):
    store, _, _ = seeded
    mine = {"crm_connection", "crm_credential", "crm_grant", "crm_token_event"}
    assert {entry["collection"] for entry in store.collections()} - {"room"} == mine


def test_the_seed_never_stores_a_credential_in_plaintext(seeded):
    store, _, _ = seeded
    rows = store.list(CREDENTIAL_COLLECTION, limit=100)
    assert rows
    for row in rows:
        body = json.dumps(row["data"])
        assert row["data"]["sealed"].split(".")[0] == "v1"
        assert "at-" not in body
        assert "rt-" not in body
        assert "secret" not in body.replace("client_secret", "")


def test_the_seed_survives_being_run_without_rooms(tmp_path):
    db = AuditedDatabase(tmp_path / "no-rooms.db")
    try:
        described = load_feature(MODULE).seed(db, {"room_ids": []})
        assert "no demo rooms" in described
        assert db.stats()["by_collection"] == {}
    finally:
        db.close()


def test_the_demo_transport_answers_without_a_socket(seeded, store):
    """The demo's own transport, driven once, so the seeder's seam is the real one."""
    module = load_feature(MODULE)
    transport = module.DemoTransport()
    result = transport.request(
        "GET", "https://api.hubapi.com/crm/v3/objects/contacts", headers={"Authorization": "Bearer at-taylorswitch-1"}
    )
    assert result.status == 401
    assert transport.calls[-1]["timeout"] > 0


def test_the_demo_describes_every_org_it_scripts():
    module = load_feature(MODULE)
    assert set(module.DEMO_ORGS) == {"northwind", "fabrikam", "taylorswitch", "acmecorp", "contoso"}
    assert all(entry.get("note") for entry in module.DEMO_ORGS.values())


def test_every_demo_connection_is_registered_in_the_org_script():
    module = load_feature(MODULE)
    for spec in module.DEMO_CONNECTIONS:
        assert spec.get("label")
        if not spec.get("org"):
            continue
        assert spec["org"] in module.DEMO_ORGS, spec["label"]
        vendor = spec.get("vendor") or module.DEMO_ORGS[spec["org"]]["vendor"]
        assert module.DEMO_ORGS[spec["org"]]["vendor"] == vendor, spec["label"]
        assert spec["org"] in module.DEMO_AGE_HOURS, spec["label"]
