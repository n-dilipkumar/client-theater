"""Feature plugin host: drop a file in, get a mounted router.

Every workflow feature is one module in this package that exposes a FastAPI
``router``. The core app includes all of them at startup by walking the package,
so adding a feature never edits ``dsr/api.py``. That single property is what
lets a hundred features, written by a hundred separate agents in separate git
worktrees, merge without a three-way conflict on a shared registration file.

A feature module looks like this::

    from fastapi import APIRouter
    from dsr.deps import StoreDep

    FEATURE = {
        "id": "wf-014-access-windows",
        "ticket": "WF-014",
        "name": "Expire or cap access to a room",
    }

    router = APIRouter(prefix="/api/wf-014", tags=["WF-014"])

    @router.get("/summary")
    def summary(store: RecordStore = StoreDep):
        ...

Two rules make the isolation hold:

* A feature owns its own module and its own routes. It must not import another
  feature, and it must not reuse a concrete route that is already taken. Two
  features may share a prefix while their paths differ; one may not silently
  shadow another's route, or a core route, or be shadowed by one.
* A feature that fails to import is reported and skipped, not fatal. With this
  many independently-authored modules, one broken file must be able to take the
  product offline.

A feature may also export ``EXCEPTION_HANDLERS`` mapping its own domain error
types to handlers. FastAPI only accepts exception handlers on the app object,
so without this a feature would have to edit ``api.py`` to describe how its
errors become responses.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, FastAPI

PACKAGE = __name__


@dataclass
class FeatureRecord:
    """One feature as the host saw it, including load failures."""

    id: str
    name: str
    ticket: str = ""
    description: str = ""
    module: str = ""
    prefix: str = ""
    routes: list[dict[str, Any]] = field(default_factory=list)
    exception_handlers: list[str] = field(default_factory=list)
    nav: list[dict[str, Any]] = field(default_factory=list)
    loaded: bool = True
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "ticket": self.ticket,
            "description": self.description,
            "module": self.module,
            "prefix": self.prefix,
            "routes": self.routes,
            "exception_handlers": self.exception_handlers,
            "nav": self.nav,
            "loaded": self.loaded,
            "error": self.error,
        }


@dataclass
class FeatureRegistry:
    """Everything the host learned while loading features."""

    features: list[FeatureRecord] = field(default_factory=list)
    failed: list[FeatureRecord] = field(default_factory=list)

    def by_id(self, feature_id: str) -> FeatureRecord | None:
        for record in self.features:
            if record.id == feature_id:
                return record
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": len(self.features),
            "failed_count": len(self.failed),
            "features": [f.to_dict() for f in self.features],
            "failed": [f.to_dict() for f in self.failed],
        }


REGISTRY = FeatureRegistry()


def _route_shapes(router: APIRouter) -> list[dict[str, Any]]:
    shapes: list[dict[str, Any]] = []
    for route in router.routes:
        shapes.append(
            {
                "path": getattr(route, "path", ""),
                "methods": sorted(_route_methods(route)),
                "name": getattr(route, "name", ""),
            }
        )
    return sorted(shapes, key=lambda r: (r["path"], r["methods"]))


def _route_methods(route: Any) -> set[str]:
    """The concrete HTTP methods a route answers, excluding HEAD auto-adds."""
    return {m for m in (getattr(route, "methods", None) or set()) if m not in ("HEAD", "OPTIONS")}


def _occupied(app: FastAPI) -> dict[tuple[str, str], str]:
    """Every (method, path) already claimed on the app, and who claimed it.

    Core routes are registered before features are loaded, so without this a
    feature that re-used a core path would be silently dead: Starlette matches
    in registration order and the core route would always win. Reporting it
    turns a no-op into a visible failure.
    """
    taken: dict[tuple[str, str], str] = {}
    for route in app.routes:
        owner = getattr(route, "name", "") or type(route).__name__
        for method in _route_methods(route):
            taken[(method, getattr(route, "path", ""))] = f"core:{owner}"
    return taken


def _module_names() -> list[str]:
    """Every sibling module, sorted, skipping private ones and the host."""
    found = []
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        found.append(info.name)
    return sorted(found)


def load_features(app: FastAPI) -> FeatureRegistry:
    """Import every feature module and mount its router onto ``app``.

    Idempotent per process: mounting twice would duplicate every route, so a
    second call returns the registry built the first time.
    """
    if REGISTRY.features or REGISTRY.failed:
        return REGISTRY

    taken = _occupied(app)
    handled_errors: dict[type, str] = {}

    for module_name in _module_names():
        full_name = f"{PACKAGE}.{module_name}"
        try:
            module = importlib.import_module(full_name)
        except Exception as exc:  # a broken feature must not take the app down
            REGISTRY.failed.append(
                FeatureRecord(
                    id=module_name,
                    name=module_name,
                    module=full_name,
                    loaded=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        meta: dict[str, Any] = getattr(module, "FEATURE", {}) or {}
        feature_id = str(meta.get("id", module_name))
        feature_name = str(meta.get("name", module_name))
        router: APIRouter | None = getattr(module, "router", None)

        if router is None:
            # A helper module living alongside features is legitimate; it just
            # has nothing to mount. Record it as loaded if it declares itself.
            if meta:
                REGISTRY.features.append(
                    FeatureRecord(
                        id=feature_id,
                        name=feature_name,
                        ticket=str(meta.get("ticket", "")),
                        description=str(meta.get("description", "")),
                        module=full_name,
                        nav=list(meta.get("nav", [])),
                    )
                )
            continue

        # Reject on any concrete route collision before mounting, so a feature
        # is never half-registered.
        shapes = _route_shapes(router)
        keys = [(method, shape["path"]) for shape in shapes for method in shape["methods"]]
        clashes = sorted({f"{m} {p} (already {taken[(m, p)]})" for m, p in keys if (m, p) in taken})
        if clashes:
            REGISTRY.failed.append(
                FeatureRecord(
                    id=feature_id,
                    name=feature_name,
                    ticket=str(meta.get("ticket", "")),
                    module=full_name,
                    prefix=router.prefix,
                    routes=shapes,
                    loaded=False,
                    error="route collision: " + "; ".join(clashes),
                )
            )
            continue

        handlers: dict[type, Any] = dict(getattr(module, "EXCEPTION_HANDLERS", {}) or {})
        repeat = sorted(
            f"{exc.__name__} (already handled by {handled_errors[exc]})"
            for exc in handlers
            if exc in handled_errors
        )
        if repeat:
            REGISTRY.failed.append(
                FeatureRecord(
                    id=feature_id,
                    name=feature_name,
                    ticket=str(meta.get("ticket", "")),
                    module=full_name,
                    prefix=router.prefix,
                    routes=shapes,
                    loaded=False,
                    error="exception handler collision: " + "; ".join(repeat),
                )
            )
            continue

        app.include_router(router)
        for error_type, handler in handlers.items():
            app.add_exception_handler(error_type, handler)
            handled_errors[error_type] = feature_id
        for method, path in keys:
            taken[(method, path)] = feature_id

        REGISTRY.features.append(
            FeatureRecord(
                id=feature_id,
                name=feature_name,
                ticket=str(meta.get("ticket", "")),
                description=str(meta.get("description", getattr(module, "__doc__", "") or "").strip()),
                module=full_name,
                prefix=router.prefix,
                routes=shapes,
                exception_handlers=sorted(exc.__name__ for exc in handlers),
                nav=list(meta.get("nav", [])),
            )
        )

    return REGISTRY


def load_feature(module_name: str):
    """Import one feature module directly, for its own tests."""
    return importlib.import_module(f"{PACKAGE}.{module_name}")
