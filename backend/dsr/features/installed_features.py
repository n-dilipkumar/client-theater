"""The feature registry API: what is installed, and what failed to load.

Other teams integrating with this product need to discover which workflows
exist without reading the source tree, so the plugin host is itself exposed
over HTTP. This module also doubles as the reference implementation of the
feature contract: a ``FEATURE`` block, a prefixed router, and nothing else.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from dsr.features import REGISTRY

FEATURE = {
    "id": "core-feature-registry",
    "name": "Feature registry",
    "ticket": "CORE",
    "description": "Lists installed workflow features and their routes over HTTP.",
}

router = APIRouter(prefix="/api/features", tags=["features"])


@router.get("", summary="List installed features")
def list_features(
    include_failed: bool = Query(default=True),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict[str, Any]:
    """Every feature the host loaded, with its routes and nav entries."""
    payload = REGISTRY.to_dict()
    payload["features"] = payload["features"][:limit]
    if not include_failed:
        payload["failed"] = []
    return payload


@router.get("/{feature_id}", summary="Inspect one feature")
def get_feature(feature_id: str) -> dict[str, Any]:
    record = REGISTRY.by_id(feature_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"feature {feature_id} not installed")
    return record.to_dict()
