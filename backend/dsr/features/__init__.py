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

* A feature owns its own module and its own prefix. It must not add routes
  outside its prefix, and must not import another feature.
* A feature that fails to import is reported and skipped, not fatal. With this
  many independently-authored modules, one broken file must be able to take the
  product offline.
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
                "methods": sorted(getattr(route, "methods", set()) or []),
                "name": getattr(route, "name", ""),
            }
        )
    return sorted(shapes, key=lambda r: (r["path"], r["methods"]))


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

    seen_prefixes: dict[str, str] = {}

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

        router: APIRouter | None = getattr(module, "router", None)
        meta: dict[str, Any] = getattr(module, "FEATURE", {}) or {}

        if router is None:
            # A helper module living alongside features is legitimate; it just
            # has nothing to mount. Record it as loaded with no routes.
            if meta:
                REGISTRY.features.append(
                    FeatureRecord(
                        id=str(meta.get("id", module_name)),
                        name=str(meta.get("name", module_name)),
                        ticket=str(meta.get("ticket", "")),
                        description=str(meta.get("description", "")),
                        module=full_name,
                        nav=list(meta.get("nav", [])),
                    )
                )
            continue

        prefix = router.prefix or ""
        owner = seen_prefixes.get(prefix)
        if owner:
            REGISTRY.failed.append(
                FeatureRecord(
                    id=str(meta.get("id", module_name)),
                    name=str(meta.get("name", module_name)),
                    module=full_name,
                    prefix=prefix,
                    loaded=False,
                    error=f"route prefix {prefix!r} is already claimed by {owner}",
                )
            )
            continue
        seen_prefixes[prefix] = full_name

        app.include_router(router)
        REGISTRY.features.append(
            FeatureRecord(
                id=str(meta.get("id", module_name)),
                name=str(meta.get("name", module_name)),
                ticket=str(meta.get("ticket", "")),
                description=str(meta.get("description", getattr(module, "__doc__", "") or "").strip()),
                module=full_name,
                prefix=prefix,
                routes=_route_shapes(router),
                nav=list(meta.get("nav", [])),
            )
        )

    return REGISTRY


def load_feature(module_name: str):
    """Import one feature module directly, for its own tests."""
    return importlib.import_module(f"{PACKAGE}.{module_name}")
