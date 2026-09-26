"""Shared dependencies for the core API and for feature plugins.

Feature modules import what they need from here, never from ``dsr.api``. That
keeps the dependency direction one-way (api -> features -> deps) so a feature
can be imported and unit-tested on its own, and so two features never need to
edit a file they share with each other.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, Request

from dsr.store import RecordStore

ROOT = Path(__file__).resolve().parents[2]


def db_path() -> str:
    """Resolve the database path at call time, not import time.

    Reading the environment lazily matters for test isolation: a module-level
    constant would be captured on first import and every test would silently
    share one database.
    """
    return os.environ.get("DSR_DB_PATH", str(ROOT / "data" / "dsr.db"))


def mirror_dir() -> str:
    return os.environ.get("DSR_AUDIT_DIR", str(ROOT / "data" / "audit"))


# Static file mounting is inherently import-time, so this one stays a constant.
FRONTEND_DIST = Path(os.environ.get("DSR_FRONTEND_DIST", str(ROOT / "frontend" / "dist")))


def get_store(request: Request) -> RecordStore:
    """FastAPI dependency yielding the process-wide store."""
    return request.app.state.store


StoreDep = Depends(get_store)
