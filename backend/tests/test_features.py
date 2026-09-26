"""Tests for the feature plugin host.

The host is the mechanism that lets many features be authored in parallel and
merged without a conflict on a shared registration file, so what matters here is
not one feature's behaviour but the invariants that make parallel authorship
safe: a feature is mounted by discovery alone, a broken feature cannot take the
app down, and two features cannot silently claim the same route prefix.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import ModuleType

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

import dsr.features as host
from dsr.api import app


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "features.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


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


def test_prefix_collision_is_reported_not_mounted(clean_registry, monkeypatch):
    """Two features on one prefix would silently shadow each other."""

    def make_module(name, feature_id, prefix):
        module = ModuleType(name)
        module.FEATURE = {"id": feature_id, "name": feature_id}
        module.router = APIRouter(prefix=prefix)

        @module.router.get("/ping")
        def ping():
            return {"ok": True}

        return module

    first = make_module("dsr.features.alpha", "alpha", "/api/alpha")
    second = make_module("dsr.features.beta", "beta", "/api/alpha")

    monkeypatch.setattr(host, "_module_names", lambda: ["alpha", "beta"])
    monkeypatch.setattr(
        host.importlib,
        "import_module",
        lambda name: first if name.endswith("alpha") else second,
    )

    registry = host.load_features(app)
    assert [f.id for f in registry.features] == ["alpha"]
    assert len(registry.failed) == 1
    assert "already claimed" in registry.failed[0].error


def test_live_registry_has_no_prefix_collisions():
    """The real package must obey the rule the loader enforces."""
    prefixes = [f.prefix for f in host.REGISTRY.features if f.prefix]
    assert len(prefixes) == len(set(prefixes)), f"duplicate prefixes: {prefixes}"


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
