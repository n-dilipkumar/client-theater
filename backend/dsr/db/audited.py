"""The single write path for the whole application.

Every SQLite mutation in this project goes through :class:`AuditedDatabase`. That
is a deliberate constraint, not an accident of layering: the product promise is
that the audit log is complete, and the only way to keep that promise as the
codebase grows across many teams is to make bypassing the audit log impossible
from the ordinary path.

Guarantees
----------
* **Atomic audit.** The audit row is written in the same transaction as the
  change it describes. Either both land or neither does, so the log can never
  drift from the data.
* **Atomic multi-record writes.** :meth:`AuditedDatabase.transaction` groups
  several writes into one transaction, so a workflow that must produce two
  related records cannot half-succeed. Every write inside the block is audited
  individually, exactly as if it had been made on its own.
* **One writer.** A single connection guarded by a re-entrant lock, with WAL
  journalling and a busy timeout. This was chosen over per-writer databases and
  over letting callers write directly; see the recorded decision in
  ``orchestration/decisions/jev-audit.jsonl`` (``single_writer_wal``).
* **Schema flexibility.** Entities are stored as an open JSON ``data`` object.
  Any team can add, rename, or reshape fields without a migration, and
  :meth:`query` can still filter on them through the dynamic index.
* **Honest failures.** Errors raise; the transaction is rolled back and no audit
  row is written for work that did not happen.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

SCHEMA_VERSION = 1
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Keys that live in dedicated columns are not duplicated into the JSON payload
# on the way out, so callers get one consistent dict shape.
_RESERVED = frozenset({"id", "collection", "room_id", "revision", "created_at", "updated_at", "deleted_at"})


def utcnow() -> str:
    """Timestamp in a sortable, unambiguous format."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex
    return f"{prefix}_{raw}" if prefix else raw


class AuditError(RuntimeError):
    """Raised when a change could not be completed or audited."""


class RecordNotFound(LookupError):
    """Raised when a record id does not resolve to a live record."""


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _loads(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _diff(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> dict[str, Any]:
    """Shallow top-level diff of the ``data`` payloads."""
    before = before or {}
    after = after or {}
    changed: dict[str, Any] = {}
    for key in set(before) | set(after):
        old, new = before.get(key), after.get(key)
        if old != new:
            changed[key] = {"from": old, "to": new}
    return changed


def _flatten(data: Any, prefix: str = "") -> Iterator[tuple[str, str, float | None]]:
    """Yield ``(path, text, number)`` for every scalar in a JSON payload.

    Nested objects and arrays get dotted paths so that, for example,
    ``{"a": {"b": 1}}`` is queryable as ``a.b``.
    """
    if isinstance(data, Mapping):
        for key, value in data.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            yield from _flatten(value, child)
    elif isinstance(data, (list, tuple)):
        for position, value in enumerate(data):
            child = f"{prefix}.{position}" if prefix else str(position)
            yield from _flatten(value, child)
    elif data is None:
        yield prefix, "", None
    elif isinstance(data, bool):
        yield prefix, ("true" if data else "false"), (1.0 if data else 0.0)
    elif isinstance(data, (int, float)):
        yield prefix, str(data), float(data)
    else:
        yield prefix, str(data), None


class AuditedDatabase:
    """Audited, schema-flexible SQLite access.

    Use as a context manager to close cleanly, or hold a single long-lived
    instance for the process (recommended: the backend owns exactly one).
    """

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        busy_timeout_ms: int = 5_000,
        mirror_dir: str | Path | None = None,
        actor: str = "system",
    ) -> None:
        self.path = str(path)
        self.actor = actor
        self._lock = threading.RLock()
        self._closed = False
        # Depth of open `transaction()` blocks. A non-zero value means a writer
        # handle is in play, so the single-record methods must refuse rather than
        # quietly open a second, independent transaction.
        self._tx_depth = 0
        # An in-memory database must share one connection to stay coherent.
        self._shared_memory = self.path == ":memory:"
        self._conn = self._connect(busy_timeout_ms)
        self.mirror_dir = Path(mirror_dir) if mirror_dir else None
        self._apply_schema()

    # -- lifecycle ---------------------------------------------------------- #

    def _connect(self, busy_timeout_ms: int) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            timeout=busy_timeout_ms / 1000,
            check_same_thread=False,
            uri=self.path.startswith("file:"),
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        if not self._shared_memory:
            # WAL cannot be used for :memory:, and is what lets the UI read
            # while the backend writes.
            conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _apply_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
            self._conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._conn.close()
                self._closed = True

    def __enter__(self) -> "AuditedDatabase":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- internals ---------------------------------------------------------- #

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """A write transaction. Rolls back on any exception."""
        with self._lock:
            if self._closed:
                raise AuditError("database is closed")
            if self._tx_depth:
                raise AuditError(
                    "a write was attempted while a transaction() block is open; use the writer "
                    "handle passed into the block so the change joins the same transaction"
                )
            try:
                self._conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                raise AuditError(f"could not begin transaction: {exc}") from exc
            try:
                yield self._conn
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    @contextmanager
    def transaction(
        self,
        *,
        actor: str | None = None,
        source: str | None = None,
        request_id: str | None = None,
    ) -> Iterator["AuditedWriter"]:
        """Group several audited writes into one all-or-nothing transaction.

        A workflow that must produce more than one record — a room and the site
        it is bound to, say — cannot half-succeed through this handle: either
        every write and every audit row commits, or none of them do. Each write
        still gets its own audit row, so the log describes the work at the same
        granularity as single-record writes.

            with db.transaction(actor="api") as tx:
                room = tx.create("room", {...})
                tx.create("site", {...}, room_id=room["id"])

        ``actor``/``source``/``request_id`` become the default for every write in
        the block; any single call can override them.
        """
        with self._write() as conn:
            self._tx_depth += 1
            writer = AuditedWriter(self, conn, actor=actor, source=source, request_id=request_id)
            try:
                yield writer
            finally:
                self._tx_depth -= 1
        # Reached only after the commit succeeded, so the mirror cannot describe
        # a change that was rolled back.
        writer.flush_mirrors()

    def _audit(
        self,
        conn: sqlite3.Connection,
        *,
        action: str,
        collection: str | None,
        record_id: str | None,
        room_id: str | None,
        actor: str | None,
        source: str | None,
        request_id: str | None,
        summary: str,
        before: Mapping[str, Any] | None,
        after: Mapping[str, Any] | None,
        started: float,
    ) -> None:
        conn.execute(
            """
            INSERT INTO audit_log
                (ts, action, collection, record_id, room_id, actor, source,
                 request_id, summary, before_state, after_state, diff, duration_ms)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                utcnow(),
                action,
                collection,
                record_id,
                room_id,
                actor or self.actor,
                source,
                request_id,
                summary,
                _dumps(before) if before is not None else None,
                _dumps(after) if after is not None else None,
                _dumps(_diff(before, after)),
                round((time.perf_counter() - started) * 1000, 3),
            ),
        )

    def _mirror(self, record: Mapping[str, Any]) -> None:
        """Append a JSONL mirror of the audit row for external tooling.

        The database is the source of truth; a failure here must never fail the
        transaction that already committed, so problems are swallowed.
        """
        if not self.mirror_dir:
            return
        try:
            self.mirror_dir.mkdir(parents=True, exist_ok=True)
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            with (self.mirror_dir / f"audit-{day}.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    def _reindex(self, conn: sqlite3.Connection, record_id: str, data: Mapping[str, Any]) -> None:
        conn.execute("DELETE FROM record_index WHERE record_id = ?", (record_id,))
        rows = [
            (record_id, path, text, number)
            for path, text, number in _flatten(data)
            if path and path not in _RESERVED
        ]
        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO record_index (record_id, path, value_text, value_num)"
                " VALUES (?,?,?,?)",
                rows,
            )

    @staticmethod
    def _hydrate(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["data"] = _loads(record.get("data"), {}) or {}
        return record

    # -- reads -------------------------------------------------------------- #

    def get(self, record_id: str, *, include_deleted: bool = False) -> dict[str, Any] | None:
        """Fetch one record by id."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM records WHERE id = ?" + ("" if include_deleted else " AND deleted_at IS NULL"),
                (record_id,),
            ).fetchone()
            return self._hydrate(row) if row else None

    def require(self, record_id: str) -> dict[str, Any]:
        record = self.get(record_id)
        if record is None:
            raise RecordNotFound(record_id)
        return record

    def count(self, collection: str, *, room_id: str | None = None, include_deleted: bool = False) -> int:
        sql = "SELECT COUNT(*) AS n FROM records WHERE collection = ?"
        params: list[Any] = [collection]
        if room_id is not None:
            sql += " AND room_id = ?"
            params.append(room_id)
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        with self._lock:
            return int(self._conn.execute(sql, params).fetchone()["n"])

    def list(
        self,
        collection: str,
        *,
        room_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        order_by: str = "updated_at",
        descending: bool = True,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        """List records in a collection with optional room scoping."""
        allowed_order = {"updated_at", "created_at", "id", "revision"}
        if order_by not in allowed_order:
            raise ValueError(f"order_by must be one of {sorted(allowed_order)}")
        sql = f"SELECT * FROM records WHERE collection = ?"
        params: list[Any] = [collection]
        if room_id is not None:
            sql += " AND room_id = ?"
            params.append(room_id)
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        direction = "DESC" if descending else "ASC"
        sql += f" ORDER BY {order_by} {direction} LIMIT ? OFFSET ?"
        params.extend([max(1, min(int(limit), 1000)), max(0, int(offset))])
        with self._lock:
            return [self._hydrate(r) for r in self._conn.execute(sql, params).fetchall()]

    def find(
        self,
        collection: str,
        where: Mapping[str, Any],
        *,
        limit: int = 100,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        """Find records whose ``data`` fields match, using the dynamic index.

        ``where`` keys are dotted JSON paths inside the record's ``data``
        object, so this works for fields no schema declares. Values are compared
        with the right SQLite affinity: booleans and numbers numerically,
        everything else as text.
        """
        if not where:
            return self.list(collection, limit=limit, include_deleted=include_deleted)
        # One correlated EXISTS per condition. A single JOIN alias cannot express
        # this: each condition constrains a different `path`, and one index row
        # can only carry one path, so the join would demand a row matching them all.
        conditions: list[str] = []
        params: list[Any] = [collection]
        for path, expected in where.items():
            if isinstance(expected, bool):
                # Booleans are indexed numerically as 1.0 / 0.0.
                conditions.append(
                    "EXISTS (SELECT 1 FROM record_index i"
                    " WHERE i.record_id = r.id AND i.path = ? AND i.value_num = ?)"
                )
                params.extend([path, 1.0 if expected else 0.0])
            elif isinstance(expected, (int, float)):
                conditions.append(
                    "EXISTS (SELECT 1 FROM record_index i"
                    " WHERE i.record_id = r.id AND i.path = ? AND i.value_num = ?)"
                )
                params.extend([path, float(expected)])
            else:
                conditions.append(
                    "EXISTS (SELECT 1 FROM record_index i"
                    " WHERE i.record_id = r.id AND i.path = ? AND i.value_text = ?)"
                )
                params.extend(
                    [path, "true" if expected is True else "false" if expected is False else str(expected)]
                )
        sql = "SELECT r.* FROM records r WHERE r.collection = ?"
        if not include_deleted:
            sql += " AND r.deleted_at IS NULL"
        if conditions:
            sql += " AND " + " AND ".join(conditions)
        sql += " ORDER BY r.updated_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        with self._lock:
            return [self._hydrate(r) for r in self._conn.execute(sql, params).fetchall()]

    def query_index(self, path: str, value: Any) -> list[str]:
        """Record ids whose ``data`` path equals ``value``.

        ``path`` is the dotted path as stored by the dynamic index, for example
        ``status`` or ``branding.theme``.
        """
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        if numeric:
            column, needle = "value_num", float(value)
        else:
            column, needle = "value_text", ("true" if value is True else "false" if value is False else str(value))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT record_id FROM record_index WHERE path = ? AND {column} = ?",  # noqa: S608
                (path, needle),
            ).fetchall()
        return [r["record_id"] for r in rows]


    # -- writes (always audited) -------------------------------------------- #

    def _insert_record(
        self,
        conn: sqlite3.Connection,
        collection: str,
        data: Mapping[str, Any] | None,
        *,
        record_id: str | None,
        room_id: str | None,
        actor: str | None,
        source: str | None,
        request_id: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Insert one record on ``conn``, audit it, and return it with its mirror.

        Shared by :meth:`create` and :class:`AuditedWriter` so a record written
        inside a transaction is indexed and audited exactly like one written on
        its own. There is deliberately no second implementation of this.
        """
        if not collection:
            raise ValueError("collection is required")
        payload = dict(data or {})
        for reserved in _RESERVED:
            payload.pop(reserved, None)
        rid = record_id or new_id(collection)
        now = utcnow()
        started = time.perf_counter()

        try:
            conn.execute(
                "INSERT INTO records"
                " (id, collection, room_id, data, revision, created_at, updated_at, actor, source)"
                " VALUES (?,?,?,?,1,?,?,?,?)",
                (rid, collection, room_id, _dumps(payload), now, now, actor or self.actor, source),
            )
        except sqlite3.IntegrityError as exc:
            raise AuditError(f"record {rid!r} already exists: {exc}") from exc
        self._reindex(conn, rid, payload)
        self._audit(
            conn,
            action="insert",
            collection=collection,
            record_id=rid,
            room_id=room_id,
            actor=actor,
            source=source,
            request_id=request_id,
            summary=f"created {collection} {rid}",
            before=None,
            after=payload,
            started=started,
        )
        row = conn.execute("SELECT * FROM records WHERE id = ?", (rid,)).fetchone()
        return self._hydrate(row), {
            "action": "insert",
            "collection": collection,
            "record_id": rid,
            "after": payload,
        }

    def _update_record(
        self,
        conn: sqlite3.Connection,
        record_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None,
        source: str | None,
        request_id: str | None,
        expected_revision: int | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Merge-patch one live record on ``conn``, audit it, return it and its mirror."""
        started = time.perf_counter()
        row = conn.execute(
            "SELECT * FROM records WHERE id = ? AND deleted_at IS NULL", (record_id,)
        ).fetchone()
        if row is None:
            raise RecordNotFound(record_id)
        current = _loads(row["data"], {}) or {}
        if expected_revision is not None and int(row["revision"]) != int(expected_revision):
            raise AuditError(
                f"revision conflict on {record_id}: expected {expected_revision}, found {row['revision']}"
            )
        merged = {**current, **dict(patch)}
        for reserved in _RESERVED:
            merged.pop(reserved, None)
        now = utcnow()
        conn.execute(
            "UPDATE records SET data = ?, revision = revision + 1, updated_at = ?, actor = ? WHERE id = ?",
            (_dumps(merged), now, actor or self.actor, record_id),
        )
        self._reindex(conn, record_id, merged)
        self._audit(
            conn,
            action="update",
            collection=row["collection"],
            record_id=record_id,
            room_id=row["room_id"],
            actor=actor,
            source=source,
            request_id=request_id,
            summary=f"updated {row['collection']} {record_id}",
            before=current,
            after=merged,
            started=started,
        )
        fresh = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
        return self._hydrate(fresh), {
            "action": "update",
            "collection": row["collection"],
            "record_id": record_id,
            "diff": _diff(current, merged),
        }

    def create(
        self,
        collection: str,
        data: Mapping[str, Any] | None = None,
        *,
        record_id: str | None = None,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a record and audit it atomically."""
        with self._write() as conn:
            record, mirror = self._insert_record(
                conn,
                collection,
                data,
                record_id=record_id,
                room_id=room_id,
                actor=actor,
                source=source,
                request_id=request_id,
            )
        self._mirror(mirror)
        return record

    def update(
        self,
        record_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str | None = None,
        request_id: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Apply a shallow merge patch to ``data`` and audit the change.

        ``expected_revision`` enables optimistic concurrency: the update fails
        if the record has moved on since the caller read it.
        """
        with self._write() as conn:
            record, mirror = self._update_record(
                conn,
                record_id,
                patch,
                actor=actor,
                source=source,
                request_id=request_id,
                expected_revision=expected_revision,
            )
        self._mirror(mirror)
        return record

    def delete(
        self,
        record_id: str,
        *,
        actor: str | None = None,
        source: str | None = None,
        request_id: str | None = None,
        hard: bool = False,
    ) -> dict[str, Any]:
        """Soft-delete (default) or hard-delete a record and audit it.

        Soft delete keeps the row so history and audit references stay intact.
        """
        started = time.perf_counter()
        with self._write() as conn:
            row = conn.execute(
                "SELECT * FROM records WHERE id = ?" + ("" if hard else " AND deleted_at IS NULL"),
                (record_id,),
            ).fetchone()
            if row is None:
                raise RecordNotFound(record_id)
            current = _loads(row["data"], {}) or {}
            if hard:
                conn.execute("DELETE FROM record_index WHERE record_id = ?", (record_id,))
                conn.execute("DELETE FROM records WHERE id = ?", (record_id,))
            else:
                # The dynamic index is deliberately left intact on a soft delete
                # so that `find(..., include_deleted=True)` can still reach the
                # record and a restore does not have to rebuild the index.
                conn.execute(
                    "UPDATE records SET deleted_at = ?, updated_at = ?, revision = revision + 1 WHERE id = ?",
                    (utcnow(), utcnow(), record_id),
                )
            self._audit(
                conn,
                action="delete",
                collection=row["collection"],
                record_id=record_id,
                room_id=row["room_id"],
                actor=actor,
                source=source,
                request_id=request_id,
                summary=f"{'hard-deleted' if hard else 'deleted'} {row['collection']} {record_id}",
                before=current,
                after=None,
                started=started,
            )
            result = {
                "id": record_id,
                "collection": row["collection"],
                "room_id": row["room_id"],
                "hard": hard,
            }
        self._mirror({"action": "delete", "collection": result["collection"], "record_id": record_id, "before": current})
        return result

    def restore(
        self,
        record_id: str,
        *,
        actor: str | None = None,
        source: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Undo a soft delete and audit it."""
        started = time.perf_counter()
        with self._write() as conn:
            row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
            if row is None:
                raise RecordNotFound(record_id)
            if row["deleted_at"] is None:
                raise AuditError(f"record {record_id} is not deleted")
            current = _loads(row["data"], {}) or {}
            conn.execute(
                "UPDATE records SET deleted_at = NULL, updated_at = ?, revision = revision + 1 WHERE id = ?",
                (utcnow(), record_id),
            )
            self._reindex(conn, record_id, current)
            self._audit(
                conn,
                action="restore",
                collection=row["collection"],
                record_id=record_id,
                room_id=row["room_id"],
                actor=actor,
                source=source,
                request_id=request_id,
                summary=f"restored {row['collection']} {record_id}",
                before=None,
                after=current,
                started=started,
            )
            fresh = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
        record = self._hydrate(fresh)
        self._mirror({"action": "restore", "collection": record["collection"], "record_id": record_id})
        return record

    def bulk_create(
        self,
        collection: str,
        items: Sequence[Mapping[str, Any]],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
        request_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Create many records in ONE transaction with ONE audit row.

        Either every item lands or none does. One audit row (not N) keeps the
        log readable when a workflow seeds or imports in bulk.
        """
        if not items:
            return []
        started = time.perf_counter()
        created: list[dict[str, Any]] = []
        with self._write() as conn:
            now = utcnow()
            for item in items:
                payload = {k: v for k, v in dict(item).items() if k not in _RESERVED}
                rid = new_id(collection)
                conn.execute(
                    "INSERT INTO records"
                    " (id, collection, room_id, data, revision, created_at, updated_at, actor, source)"
                    " VALUES (?,?,?,?,1,?,?,?,?)",
                    (rid, collection, room_id, _dumps(payload), now, now, actor or self.actor, source),
                )
                self._reindex(conn, rid, payload)
                created.append({"id": rid, "collection": collection, "room_id": room_id, "data": payload,
                                "revision": 1, "created_at": now, "updated_at": now, "deleted_at": None})
            self._audit(
                conn,
                action="insert",
                collection=collection,
                record_id=None,
                room_id=room_id,
                actor=actor,
                source=source,
                request_id=request_id,
                summary=f"bulk-created {len(created)} {collection} record(s)",
                before=None,
                after={"count": len(created), "ids": [r["id"] for r in created]},
                started=started,
            )
        self._mirror({"action": "bulk_insert", "collection": collection, "count": len(created)})
        return created

    # -- audit reads -------------------------------------------------------- #

    def audit(
        self,
        *,
        collection: str | None = None,
        record_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        request_id: str | None = None,
        since: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Read the audit trail, newest first.

        ``request_id`` is how one caller's several writes are read back together,
        which is what makes a multi-record workflow traceable as a unit.
        """
        sql = "SELECT * FROM audit_log WHERE 1=1"
        params: list[Any] = []
        for column, value in (
            ("collection", collection),
            ("record_id", record_id),
            ("actor", actor),
            ("action", action),
            ("request_id", request_id),
        ):
            if value is not None:
                sql += f" AND {column} = ?"  # noqa: S608 - column from a fixed allowlist
                params.append(value)
        if since:
            sql += " AND ts >= ?"
            params.append(since)
        sql += " ORDER BY seq DESC LIMIT ? OFFSET ?"
        params.extend([max(1, min(int(limit), 1000)), max(0, int(offset))])
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        entries = []
        for row in rows:
            entry = dict(row)
            for key in ("before_state", "after_state", "diff"):
                entry[key] = _loads(entry.get(key))
            entries.append(entry)
        return entries

    def audit_count(
        self,
        *,
        collection: str | None = None,
        record_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        request_id: str | None = None,
    ) -> int:
        """Count audit rows matching the same filters as :meth:`audit`."""
        sql = "SELECT COUNT(*) AS n FROM audit_log WHERE 1=1"
        params: list[Any] = []
        for column, value in (
            ("collection", collection),
            ("record_id", record_id),
            ("actor", actor),
            ("action", action),
            ("request_id", request_id),
        ):
            if value is not None:
                sql += f" AND {column} = ?"  # noqa: S608 - column from a fixed allowlist
                params.append(value)
        with self._lock:
            return int(self._conn.execute(sql, params).fetchone()["n"])

    def stats(self) -> dict[str, Any]:
        """Summary counts used by the dashboard header."""
        with self._lock:
            conn = self._conn
            live = conn.execute("SELECT COUNT(*) AS n FROM records WHERE deleted_at IS NULL").fetchone()["n"]
            deleted = conn.execute("SELECT COUNT(*) AS n FROM records WHERE deleted_at IS NOT NULL").fetchone()["n"]
            audits = conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]
            by_collection = {
                r["collection"]: r["n"]
                for r in conn.execute(
                    "SELECT collection, COUNT(*) AS n FROM records"
                    " WHERE deleted_at IS NULL GROUP BY collection ORDER BY n DESC"
                ).fetchall()
            }
            by_action = {
                r["action"]: r["n"]
                for r in conn.execute(
                    "SELECT action, COUNT(*) AS n FROM audit_log GROUP BY action ORDER BY n DESC"
                ).fetchall()
            }
        return {
            "records": live,
            "deleted": deleted,
            "audit_entries": audits,
            "by_collection": by_collection,
            "by_action": by_action,
            "schema_version": SCHEMA_VERSION,
        }


class AuditedWriter:
    """Audited write handle for one open transaction.

    Yielded by :meth:`AuditedDatabase.transaction`. Every method writes on the
    transaction's connection, so the whole block shares one commit or one
    rollback, and every write still produces its own audit row and index entry.
    The single-record methods on :class:`AuditedDatabase` share their
    implementation with these, so there is no way for a transactional write to
    be indexed or audited differently from a standalone one.
    """

    def __init__(
        self,
        db: AuditedDatabase,
        conn: sqlite3.Connection,
        *,
        actor: str | None = None,
        source: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self._db = db
        self._conn = conn
        self._actor = actor
        self._source = source
        self._request_id = request_id
        self._pending_mirrors: list[dict[str, Any]] = []

    def _resolve(
        self, actor: str | None, source: str | None, request_id: str | None
    ) -> tuple[str | None, str | None, str | None]:
        """Per-call values, falling back to the block's defaults."""
        return (
            actor if actor is not None else self._actor,
            source if source is not None else self._source,
            request_id if request_id is not None else self._request_id,
        )

    def create(
        self,
        collection: str,
        data: Mapping[str, Any] | None = None,
        *,
        record_id: str | None = None,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a record that commits with the rest of the transaction."""
        actor, source, request_id = self._resolve(actor, source, request_id)
        record, mirror = self._db._insert_record(  # noqa: SLF001 - one implementation, shared
            self._conn,
            collection,
            data,
            record_id=record_id,
            room_id=room_id,
            actor=actor,
            source=source,
            request_id=request_id,
        )
        self._pending_mirrors.append(mirror)
        return record

    def update(
        self,
        record_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str | None = None,
        request_id: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Merge-patch a record that commits with the rest of the transaction."""
        actor, source, request_id = self._resolve(actor, source, request_id)
        record, mirror = self._db._update_record(  # noqa: SLF001 - one implementation, shared
            self._conn,
            record_id,
            patch,
            actor=actor,
            source=source,
            request_id=request_id,
            expected_revision=expected_revision,
        )
        self._pending_mirrors.append(mirror)
        return record

    def flush_mirrors(self) -> None:
        """Write the queued JSONL mirror rows. Called only after a commit.

        Mirroring is best-effort by design: the database is the source of truth,
        so a mirror failure must not fail work that has already committed.
        """
        pending, self._pending_mirrors = self._pending_mirrors, []
        for mirror in pending:
            self._db._mirror(mirror)  # noqa: SLF001
