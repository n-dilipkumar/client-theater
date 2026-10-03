"""Connection rows for WF-048, and the promote/revert gate.

The row is where the whole researched flow lands: a production connection is
registered once, a test environment is a second row cloned from it (see
:mod:`dsr.connector_sandbox.environments`), a run is executed against the test
row, and promotion - the researched "only after green does the admin switch
the connection to production" - is a stored decision that names the green run
it came from.

Every write goes through the audited store with the ``source`` that names the
route that served it, so the audit trail cannot drift from the data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.connector_sandbox.errors import (
    NotValidatedError,
    SandboxError,
    UnknownConnectionError,
    UnknownRoomError,
)
from dsr.connector_sandbox.runs import (
    COLLECTION_RUN,
    RUN_STATUS_FAILED,
    RUN_STATUS_PASSED,
    RunResult,
    run_test_sync,
)
from dsr.connector_sandbox.transport import Transport

COLLECTION_CONNECTION = "connector_sandbox_connection"

REQUIRED_CONNECTION_FIELDS = ("vendor", "base_url", "object_name", "key_field")

VENDORS = ("salesforce", "hubspot", "dataverse")


def require_room(store: Any, room_id: str) -> None:
    """The room a write is scoped to must exist."""
    if store.get(room_id) is None:
        raise UnknownRoomError(room_id)


def load_connection(store: Any, connection_id: str) -> dict[str, Any]:
    """Load one live connection row, refusing an unknown or deleted id."""
    record = store.get(connection_id)
    if record is None or record["collection"] != COLLECTION_CONNECTION:
        raise UnknownConnectionError(connection_id)
    return record


def validate_connection_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a connection create, refusing what a run could not use.

    Refused, not defaulted: a missing base URL or key field would fail a run
    three probes deep with an error that says nothing about which field was
    absent. The research parameterises the connector by base URL +
    credentials, so a connection without them is not a connection.
    """
    missing = [field for field in ("vendor", "base_url", "object_name", "key_field") if not str(payload.get(field) or "").strip()]
    if missing:
        raise SandboxError(
            "a connection needs " + ", ".join(missing) + "; a run without them cannot address the sandbox"
        )
    vendor = str(payload["vendor"]).strip().lower()
    if vendor not in VENDORS:
        raise SandboxError(f"vendor {vendor!r} is not one of {', '.join(VENDORS)}")
    return {
        "vendor": vendor,
        "base_url": str(payload["base_url"]).strip().rstrip("/"),
        "object_name": str(payload["object_name"]).strip(),
        "key_field": str(payload["key_field"]).strip(),
        "field_map": dict(payload.get("field_map") or {}),
        "tenant": str(payload.get("tenant") or "acme"),
        "label": str(payload.get("label") or f"{payload['vendor']} connector"),
        "environment": "production",
        "notes": str(payload.get("notes") or ""),
    }


def create_connection(
    store: Any,
    room_id: str,
    payload: Mapping[str, Any],
    *,
    actor: str,
    source: str,
) -> dict[str, Any]:
    """Register the production connection. 201.

    Nothing is auto-validated here beyond what a run needs to address a
    sandbox: a connection created without its test environment is a genuine
    state, and the page shows exactly what it is missing rather than the
    route refusing the create and leaving the admin with a form and no list.
    """
    require_room(store, room_id)
    data = validate_connection_payload(payload)
    return store.create(
        COLLECTION_CONNECTION,
        data,
        room_id=room_id,
        actor=actor,
        source=source,
    )


def list_connections(store: Any, room_id: str | None = None) -> list[dict[str, Any]]:
    """Every live connection, newest first, optionally room-scoped."""
    if room_id:
        return store.list(COLLECTION_CONNECTION, room_id=room_id, limit=200)
    return store.list(COLLECTION_CONNECTION, limit=200)


def connection_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    """A row as the page shows it, without leaking anything it does not need."""
    data = record["data"]
    return {
        "id": record["id"],
        "room_id": record["room_id"],
        "label": data.get("label") or record["id"],
        "vendor": data.get("vendor"),
        "environment": data.get("environment") or "production",
        "base_url": data.get("base_url"),
        "object_name": data.get("object_name"),
        "key_field": data.get("key_field"),
        "test_env_kind": data.get("test_env_kind"),
        "env_type": data.get("env_type"),
        "source_connection_id": data.get("source_connection_id"),
        "expires_at": data.get("expires_at"),
        "reverted_at": data.get("reverted_at"),
        "promoted_at": data.get("promoted_at"),
        "promoted_run_id": data.get("promoted_run_id"),
        "last_run_id": data.get("last_run_id"),
        "last_run_status": data.get("last_run_status"),
        "last_validated_at": data.get("last_validated_at"),
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
    }


def latest_run(store: Any, connection_id: str) -> dict[str, Any] | None:
    """The newest run for a connection, or None when it has never run."""
    rows = store.find(COLLECTION_RUN, {"connection_id": connection_id}, limit=1)
    return rows[0] if rows else None


def require_promotable(store: Any, connection_id: str, *, source: str) -> dict[str, Any]:
    """The green-run gate the researched promotion rule demands.

    Raised as :class:`NotValidatedError` when there is no run, or when the
    newest run failed - "only after green does the admin switch the
    connection to production" is a gate, not advice.
    """
    record = load_connection(store, connection_id)
    if record["data"].get("environment") == "production" and not record["data"].get("source_connection_id"):
        raise SandboxError(
            "a production connection is already the destination; promote a "
            "test-environment row that validates it"
        )
    run = latest_run(store, connection_id)
    if run is None:
        raise NotValidatedError(
            f"connection {connection_id} has no test sync behind it; run one before promoting"
        )
    if run["data"].get("status") != RUN_STATUS_PASSED:
        raise NotValidatedError(
            f"connection {connection_id} has no green run: the latest run is "
            f"{run['data'].get('status')}, and promotion needs the researched green gate"
        )
    return {"connection": record, "run": run}


def promote(
    store: Any,
    connection_id: str,
    *,
    now: datetime | None = None,
    actor: str,
    source: str,
) -> dict[str, Any]:
    """Switch the connection to production, naming the green run it came from. 200.

    The promotion is written in the same transaction as the connection
    update: either the run reference and the promotion both land, or neither
    does, so an audit row never promotes without the evidence.
    """
    now = now or datetime.now(timezone.utc)
    gate = require_promotable(store, connection_id, source=source)
    run_id = str(gate["run"]["id"])
    with store.db.transaction(actor=actor, source=source) as tx:
        updated = tx.update(
            connection_id,
            {
                "promoted_at": now.isoformat(timespec="seconds"),
                "promoted_run_id": run_id,
                "last_validated_at": now.isoformat(timespec="seconds"),
            },
        )
    return {
        "connection": connection_summary(updated),
        "promoted": True,
        "promoted_run_id": run_id,
        "promoted_at": updated["data"]["promoted_at"],
    }


def revert(
    store: Any,
    connection_id: str,
    *,
    now: datetime | None = None,
    actor: str,
    source: str,
) -> dict[str, Any]:
    """Abandon the test environment: "promote or revert", with production untouched.

    The research's data flow ends at "promote or revert" - the sandbox row
    was never the production row, so reverting it changes nothing on the row
    it was cloned from.
    """
    now = now or datetime.now(timezone.utc)
    record = load_connection(store, connection_id)
    if record["data"].get("environment") == "production" and not record["data"].get("source_connection_id"):
        raise SandboxError(
            "a production connection is not a test environment; there is nothing to revert"
        )
    updated = store.update(
        connection_id,
        {"reverted_at": now.isoformat(timespec="seconds")},
        actor=actor,
        source=source,
    )
    return {"connection": connection_summary(updated), "reverted": True}


def execute_run(
    store: Any,
    connection_id: str,
    *,
    transport: Transport,
    fixture: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    actor: str = "system",
    source: str = "test",
    request_id: str | None = None,
) -> RunResult:
    """Run the pipeline and store the run atomically with its write-back. 200.

    The run record and the connection's ``last_run_*`` pointers land in one
    transaction, so a page can never show a run the connection does not know
    about, or a pointer to a run that did not land.
    """
    now = now or datetime.now(timezone.utc)
    record = load_connection(store, connection_id)
    result = run_test_sync(
        store, record, transport=transport, fixture=fixture, now=now, actor=actor, source=source
    )
    run_at = now.isoformat(timespec="seconds")
    with store.db.transaction(actor=actor, source=source, request_id=request_id) as tx:
        run = tx.create(
            COLLECTION_RUN,
            {
                "connection_id": connection_id,
                "room_id": result.room_id,
                "run_at": run_at,
                "status": result.status,
                "aborted_reason": result.aborted_reason,
                "assertions": result.assertions,
                "requests": result.requests,
                "quota": result.quota,
                "fixture": result.fixture,
                "transport": getattr(transport, "transport_name", type(transport).__name__),
            },
            room_id=result.room_id or None,
        )
        patch: dict[str, Any] = {
            "last_run_id": run["id"],
            "last_run_status": result.status,
        }
        if result.passed:
            patch["last_validated_at"] = run_at
        tx.update(connection_id, patch)
    result.run_id = run["id"]
    return result


def run_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    """A run as the page and the CI job read it."""
    return {
        "id": record["id"],
        "connection_id": record["data"].get("connection_id"),
        "room_id": record["room_id"],
        "run_at": record["data"].get("run_at"),
        "status": record["data"].get("status"),
        "aborted_reason": record["data"].get("aborted_reason"),
        "assertions": record["data"].get("assertions") or [],
        "requests": record["data"].get("requests") or [],
        "quota": record["data"].get("quota") or {},
        "transport": record["data"].get("transport"),
    }


def overview(store: Any) -> dict[str, Any]:
    """Counts for the page header, including the states a demo must show."""
    connections = list_connections(store)
    runs = store.list(COLLECTION_RUN, limit=200)
    test_rows = [row for row in connections if row["data"].get("environment") != "production"]
    promoted = [row for row in connections if row["data"].get("promoted_at")]
    return {
        "connections": len(connections),
        "test_environments": len(test_rows),
        "runs": len(runs),
        "green_runs": sum(1 for row in runs if row["data"].get("status") == RUN_STATUS_PASSED),
        "failed_runs": sum(1 for row in runs if row["data"].get("status") == RUN_STATUS_FAILED),
        "promoted": len(promoted),
    }
