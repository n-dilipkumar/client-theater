"""Schema-flexible record storage over :class:`AuditedDatabase`.

This layer exists so the HTTP surface stays generic. Teams add their own fields
to ``data`` without a migration, and the discovery helpers let a client find out
what fields actually exist in a collection at runtime.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from dsr.db.audited import AuditedDatabase


class RecordStore:
    """Thin, schema-flexible façade over the audited database."""

    def __init__(self, db: AuditedDatabase) -> None:
        self.db = db

    # -- collections -------------------------------------------------------- #

    def collections(self) -> list[dict[str, Any]]:
        """Every collection that currently holds at least one live record."""
        with self.db._lock:  # noqa: SLF001 - single-writer design, same process
            rows = self.db._conn.execute(  # noqa: SLF001
                "SELECT collection,"
                "       COUNT(*) AS live,"
                "       SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END) AS deleted,"
                "       MAX(updated_at) AS last_updated"
                "  FROM records GROUP BY collection ORDER BY collection"
            ).fetchall()
        return [dict(row) for row in rows]

    def fields(self, collection: str) -> list[dict[str, Any]]:
        """Discover the JSON paths actually in use for a collection.

        This is what makes the store schema-flexible in practice: a client can
        ask what fields exist instead of hard-coding a schema that would drift.
        """
        with self.db._lock:  # noqa: SLF001
            rows = self.db._conn.execute(  # noqa: SLF001
                "SELECT i.path AS path, COUNT(DISTINCT r.id) AS records"
                "  FROM records r JOIN record_index i ON i.record_id = r.id"
                " WHERE r.collection = ? AND r.deleted_at IS NULL"
                " GROUP BY i.path ORDER BY i.path",
                (collection,),
            ).fetchall()
        return [dict(row) for row in rows]

    def sample(self, collection: str, limit: int = 1) -> list[dict[str, Any]]:
        return self.db.list(collection, limit=limit, order_by="updated_at", descending=False)

    # -- passthrough -------------------------------------------------------- #

    def create(self, collection: str, data: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        return self.db.create(collection, data, **kwargs)

    def get(self, record_id: str) -> dict[str, Any] | None:
        return self.db.get(record_id)

    def require(self, record_id: str) -> dict[str, Any]:
        return self.db.require(record_id)

    def list(self, collection: str, **kwargs: Any) -> list[dict[str, Any]]:
        return self.db.list(collection, **kwargs)

    def find(self, collection: str, where: Mapping[str, Any], **kwargs: Any) -> list[dict[str, Any]]:
        return self.db.find(collection, where, **kwargs)

    def update(self, record_id: str, patch: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        return self.db.update(record_id, patch, **kwargs)

    def delete(self, record_id: str, **kwargs: Any) -> dict[str, Any]:
        return self.db.delete(record_id, **kwargs)

    def restore(self, record_id: str, **kwargs: Any) -> dict[str, Any]:
        return self.db.restore(record_id, **kwargs)

    def bulk_create(self, collection: str, items: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return self.db.bulk_create(collection, items, **kwargs)

    def audit(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.db.audit(**kwargs)

    def stats(self) -> dict[str, Any]:
        return self.db.stats()


def parse_where(raw: str | None) -> dict[str, Any]:
    """Parse a ``where`` query parameter expressed as JSON.

    Accepts either a JSON object (``{"stage":"demo"}``) or a compact
    comma-separated form (``stage=demo,seats=10``) so the API stays pleasant to
    call from a browser as well as from code.
    """
    if not raw:
        return {}
    text = raw.strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"where is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("where must be a JSON object")
        return parsed

    result: dict[str, Any] = {}
    for pair in text.split(","):
        if not pair.strip():
            continue
        key, separator, value = pair.partition("=")
        if not separator:
            raise ValueError(f"malformed where pair {pair!r}; expected key=value")
        result[key.strip()] = _coerce(value.strip())

    return result


def _coerce(value: str) -> Any:
    lowered = value.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "none"):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value
