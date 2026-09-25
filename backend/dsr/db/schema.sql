-- Schema for the open-source Digital Sales Room.
--
-- Design notes
-- ------------
-- * Schema flexibility is a hard requirement: every team must be able to add
--   fields without a migration and without coordinating with anyone else. So
--   entities live in one `records` table with an open JSON `data` column, and
--   `record_index` provides dynamic indexing over arbitrary JSON paths.
-- * Everything is audited. `audit_log` is append-only and is written inside the
--   same transaction as the change it describes, so an audit row can never
--   exist without its change, nor a change without its audit row.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- One row per entity. `collection` is the logical type ("room", "document",
-- "meeting", ...). `data` is an open JSON object owned by the calling team.
CREATE TABLE IF NOT EXISTS records (
    id          TEXT PRIMARY KEY,
    collection  TEXT    NOT NULL,
    room_id     TEXT,
    data        TEXT    NOT NULL DEFAULT '{}',
    revision    INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    deleted_at  TEXT,
    actor       TEXT,
    source      TEXT
);

CREATE INDEX IF NOT EXISTS idx_records_collection   ON records (collection, deleted_at);
CREATE INDEX IF NOT EXISTS idx_records_room         ON records (room_id, collection, deleted_at);
CREATE INDEX IF NOT EXISTS idx_records_updated      ON records (updated_at DESC);

-- Dynamic index over any JSON path in `data`, so schema-flexible queries stay
-- fast. `value_text` holds the string form; numeric and boolean values are
-- also projected into REAL for range queries.
CREATE TABLE IF NOT EXISTS record_index (
    record_id  TEXT NOT NULL,
    path       TEXT NOT NULL,
    value_text TEXT,
    value_num  REAL,
    PRIMARY KEY (record_id, path)
);

CREATE INDEX IF NOT EXISTS idx_record_index_path  ON record_index (path, value_text);
CREATE INDEX IF NOT EXISTS idx_record_index_num   ON record_index (path, value_num);

-- Append-only audit trail. Written in the same transaction as the change.
CREATE TABLE IF NOT EXISTS audit_log (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL,
    action       TEXT    NOT NULL,   -- insert | update | delete | restore | ddl
    collection   TEXT,
    record_id    TEXT,
    room_id      TEXT,
    actor        TEXT,
    source       TEXT,
    request_id   TEXT,
    summary      TEXT,
    before_state TEXT,               -- JSON, NULL for insert
    after_state  TEXT,               -- JSON, NULL for delete
    diff         TEXT,               -- JSON map of changed top-level data keys
    duration_ms  REAL
);

CREATE INDEX IF NOT EXISTS idx_audit_ts         ON audit_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_collection ON audit_log (collection, seq DESC);
CREATE INDEX IF NOT EXISTS idx_audit_record     ON audit_log (record_id, seq DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor      ON audit_log (actor, seq DESC);
