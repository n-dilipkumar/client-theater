"""Tests for the feature plugin host.

The host is the mechanism that lets many features be authored in parallel and
merged without a conflict on a shared registration file, so what matters here is
not one feature's behaviour but the invariants that make parallel authorship
safe: a feature is mounted by discovery alone, a broken feature cannot take the
app down, and two features cannot silently claim the same route prefix.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import ModuleType

import dsr.features as host
import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.store import RecordStore
from fastapi import APIRouter
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def _entered_client(tmp_path_factory):
    """Enter one TestClient for the whole module.

    Entering a TestClient runs the FastAPI lifespan, which opens the database.
    That is the expensive part, and a test needs a fresh *database*, not a fresh
    *application*: ``dsr.deps.get_store`` reads ``request.app.state.store`` on
    every request, so replacing that attribute is enough to isolate a test. The
    client is entered once here and the store is swapped per test below, which
    keeps the same isolation for a fraction of the cost.

    Module scope rules out ``monkeypatch``, so the environment is set by hand and
    put back on the way out.
    """
    import dsr.api as api_module

    tmp = tmp_path_factory.mktemp("client")
    saved = {name: os.environ.get(name) for name in ("DSR_DB_PATH", "DSR_AUDIT_DIR")}
    saved_dist = api_module.FRONTEND_DIST
    os.environ["DSR_DB_PATH"] = str(tmp / "features.db")
    os.environ["DSR_AUDIT_DIR"] = str(tmp / "audit")
    # Static mounts are import-time, so point the module at a missing directory
    # to keep these tests focused on the API rather than the built frontend.
    api_module.FRONTEND_DIST = tmp / "absent-frontend"
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        api_module.FRONTEND_DIST = saved_dist


@pytest.fixture()
def client(_entered_client):
    """Give each test its own empty database, in memory.

    The previous store is put back on the way out. `app` is a module-level
    singleton shared by every test file in the process, so leaving a closed
    database on `app.state` would be state this fixture leaked into whatever
    module runs next.
    """
    app_ = _entered_client.app
    previous_db, previous_store = app_.state.db, app_.state.store
    db = AuditedDatabase(":memory:", actor="api")
    app_.state.db = db
    app_.state.store = RecordStore(db)
    try:
        yield _entered_client
    finally:
        app_.state.db = previous_db
        app_.state.store = previous_store
        db.close()


@pytest.fixture()
def clean_registry():
    """Give a loader test an empty registry and put the real one back after."""
    features, failed = list(host.REGISTRY.features), list(host.REGISTRY.failed)
    host.REGISTRY.features.clear()
    host.REGISTRY.failed.clear()
    yield host.REGISTRY
    host.REGISTRY.features[:] = features
    host.REGISTRY.failed[:] = failed


# -- discovery --------------------------------------------------------------- #


def test_core_app_still_serves_health(client):
    assert client.get("/api/health").status_code == 200


def test_installed_feature_is_mounted_by_discovery(client):
    """The registry feature was never named in api.py, yet its route resolves."""
    response = client.get("/api/features")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == len(body["features"])
    assert body["count"] >= 1


def test_registry_lists_itself(client):
    body = client.get("/api/features").json()
    ids = {feature["id"] for feature in body["features"]}
    assert "core-feature-registry" in ids


def test_single_feature_lookup(client):
    response = client.get("/api/features/core-feature-registry")
    assert response.status_code == 200
    assert response.json()["ticket"] == "CORE"


def test_unknown_feature_is_404(client):
    assert client.get("/api/features/not-a-feature").status_code == 404


def test_every_loaded_feature_reports_its_routes(client):
    body = client.get("/api/features").json()
    for feature in body["features"]:
        if feature["prefix"]:
            assert feature["routes"], f"{feature['id']} mounted a router but reported no routes"


# -- isolation guarantees ---------------------------------------------------- #


def test_load_features_is_idempotent():
    """A second load must not double-mount every route."""
    before = list(host.REGISTRY.features)
    again = host.load_features(app)
    assert again.features == before


def test_broken_feature_is_recorded_not_fatal(clean_registry, monkeypatch):
    """One bad module cannot take the product offline."""

    def fake_import(name):
        raise RuntimeError("this feature is broken on purpose")

    monkeypatch.setattr(host, "_module_names", lambda: ["broken_one"])
    monkeypatch.setattr(host.importlib, "import_module", fake_import)

    registry = host.load_features(app)
    assert registry.features == []
    assert len(registry.failed) == 1
    failure = registry.failed[0]
    assert failure.loaded is False
    assert "broken on purpose" in failure.error


def test_route_collision_is_reported_not_mounted(clean_registry, monkeypatch):
    """Two features on one concrete route would silently shadow each other."""

    def make_module(name, feature_id, routes):
        module = ModuleType(name)
        module.FEATURE = {"id": feature_id, "name": feature_id}
        module.router = APIRouter(prefix="/api/shared")
        for path in routes:

            @module.router.get(path)
            def handler():
                return {"ok": True}

        return module

    first = make_module("dsr.features.alpha", "alpha", ["/ping"])
    second = make_module("dsr.features.beta", "beta", ["/ping"])

    monkeypatch.setattr(host, "_module_names", lambda: ["alpha", "beta"])
    monkeypatch.setattr(
        host.importlib,
        "import_module",
        lambda name: first if name.endswith("alpha") else second,
    )

    registry = host.load_features(app)
    assert [f.id for f in registry.features] == ["alpha"]
    assert len(registry.failed) == 1
    assert "route collision" in registry.failed[0].error


def test_shared_prefix_with_disjoint_paths_is_allowed(clean_registry, monkeypatch):
    """WF-003 and WF-005 both sit under /api/rooms legitimately; that is not a clash."""

    def make_module(name, feature_id, path):
        module = ModuleType(name)
        module.FEATURE = {"id": feature_id, "name": feature_id}
        module.router = APIRouter(prefix="/api/rooms")

        @module.router.get(path)
        def handler():
            return {"ok": True}

        return module

    first = make_module("dsr.features.rooms", "rooms", "")
    second = make_module("dsr.features.documents", "documents", "/{room_id}/documents")

    monkeypatch.setattr(host, "_module_names", lambda: ["rooms", "documents"])
    monkeypatch.setattr(
        host.importlib,
        "import_module",
        lambda name: first if name.endswith("rooms") else second,
    )

    registry = host.load_features(app)
    assert {f.id for f in registry.features} == {"rooms", "documents"}
    assert registry.failed == []


def test_reusing_a_core_route_is_reported(clean_registry, monkeypatch):
    """A feature that re-registered a core path would be dead code, not a feature."""
    from types import ModuleType

    module = ModuleType("dsr.features.squatter")
    module.FEATURE = {"id": "squatter", "name": "Squatter"}
    module.router = APIRouter()

    @module.router.get("/api/health")
    def handler():
        return {"stolen": True}

    monkeypatch.setattr(host, "_module_names", lambda: ["squatter"])
    monkeypatch.setattr(host.importlib, "import_module", lambda name: module)

    registry = host.load_features(app)
    assert registry.features == []
    assert "core:health" in registry.failed[0].error


def test_exception_handlers_are_registered_by_the_host(clean_registry, monkeypatch):
    """FastAPI accepts exception handlers on the app only, so the host mounts them."""
    from types import ModuleType

    from fastapi import Request
    from fastapi.responses import JSONResponse

    class FeatureError(Exception):
        pass

    module = ModuleType("dsr.features.guarded")
    module.FEATURE = {"id": "guarded", "name": "Guarded"}
    module.router = APIRouter(prefix="/api/guarded")

    @module.router.get("/boom")
    def boom():
        raise FeatureError("no")

    # Starlette's handler signature, matching the core handlers in api.py.
    def handle(request: Request, exc: FeatureError):
        return JSONResponse(status_code=418, content={"error": "feature_error", "detail": str(exc)})

    module.EXCEPTION_HANDLERS = {FeatureError: handle}

    monkeypatch.setattr(host, "_module_names", lambda: ["guarded"])
    monkeypatch.setattr(host.importlib, "import_module", lambda name: module)

    registry = host.load_features(app)
    assert registry.features[0].exception_handlers == ["FeatureError"]

    # Mounting onto the real app would leak the handler into other tests, so the
    # behavioural check uses a throwaway app with the same module.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    probe = FastAPI()
    probe.include_router(module.router)
    probe.add_exception_handler(FeatureError, handle)
    response = TestClient(probe, raise_server_exceptions=False).get("/api/guarded/boom")
    assert response.status_code == 418
    assert response.json()["error"] == "feature_error"


def test_duplicate_exception_handler_is_refused(clean_registry, monkeypatch):
    """Two features mapping one error type would make the winner load-order dependent."""
    from types import ModuleType

    class Shared(Exception):
        pass

    def make(name, feature_id):
        module = ModuleType(name)
        module.FEATURE = {"id": feature_id, "name": feature_id}
        module.router = APIRouter(prefix=f"/api/{feature_id}")

        @module.router.get("/ping")
        def handler():
            return {"ok": True}

        module.EXCEPTION_HANDLERS = {Shared: lambda exc: None}
        return module

    first = make("dsr.features.one", "one")
    second = make("dsr.features.two", "two")

    monkeypatch.setattr(host, "_module_names", lambda: ["one", "two"])
    monkeypatch.setattr(
        host.importlib,
        "import_module",
        lambda name: first if name.endswith(".one") else second,
    )

    registry = host.load_features(app)
    assert [f.id for f in registry.features] == ["one"]
    assert "exception handler collision" in registry.failed[0].error


def test_live_registry_has_no_route_collisions():
    """The real package must obey the rule the loader enforces."""
    seen: set[tuple[str, str]] = set()
    for feature in host.REGISTRY.features:
        for route in feature.routes:
            for method in route["methods"]:
                key = (method, route["path"])
                assert key not in seen, f"{feature.id} duplicates {key}"
                seen.add(key)


def test_feature_modules_do_not_import_the_app():
    """Importing dsr.api from a feature would reintroduce the shared-file coupling."""
    package = Path(host.__file__).parent
    for module in package.glob("*.py"):
        if module.name == "__init__.py":
            continue
        text = module.read_text(encoding="utf-8")
        assert "from dsr.api" not in text and "import dsr.api" not in text, (
            f"{module.name} imports dsr.api; take dependencies from dsr.deps instead"
        )
