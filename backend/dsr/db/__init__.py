"""Persistence layer for the Digital Sales Room.

``audited`` is the only supported write path.
"""

from dsr.db.audited import (
    AuditedDatabase,
    AuditError,
    RecordNotFound,
    SCHEMA_VERSION,
    new_id,
    utcnow,
)

__all__ = [
    "AuditedDatabase",
    "AuditError",
    "RecordNotFound",
    "SCHEMA_VERSION",
    "new_id",
    "utcnow",
]
