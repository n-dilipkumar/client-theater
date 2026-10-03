"""The two automations the research names for WF-048.

* **Self-test** (extensibility): "the open-source room can ship a 'self-test'
  command that creates both connection rows and runs the fixture, making
  sandbox validation a first-class feature rather than an ops chore."
* **CI run** (automations): "GitHub Actions creates the test account from a
  config file on every push, then the sync smoke test runs."

Both end in the same assertion report a manual run produces, so a CI job and
an admin see the same truth about a connector.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.connector_sandbox.connections import (
    COLLECTION_CONNECTION,
    connection_summary,
    execute_run,
    list_connections,
    require_room,
)
from dsr.connector_sandbox.environments import create_test_environment
from dsr.connector_sandbox.errors import SandboxError
from dsr.connector_sandbox.transport import Transport


def self_test(
    store: Any,
    room_id: str,
    payload: Mapping[str, Any],
    *,
    transport: Transport,
    now: datetime | None = None,
    actor: str = "system",
    source: str,
) -> dict[str, Any]:
    """Create both connection rows and run the fixture. 201 with the report.

    The production row is created from the payload when it is absent and
    reused when it is there (keyed on vendor + object + base URL), so a
    second self-test re-validates the connector instead of piling up rows.
    The test environment row is always fresh: a sandbox is cheap to throw
    away and expensive to trust on the second run.
    """
    now = now or datetime.now(timezone.utc)
    require_room(store, room_id)
    production = _find_or_create_production(store, room_id, payload, actor=actor, source=source)
    spec = dict(
        payload.get("test_environment") or {"kind": "power_platform", "env_type": "sandbox"}
    )
    spec["room_id"] = room_id
    sandbox = create_test_environment(
        store, production["id"], spec, now=now, actor=actor, source=source
    )
    result = execute_run(
        store, sandbox["id"], transport=transport, now=now, actor=actor, source=source
    )
    return {
        "production": connection_summary(production),
        "test_environment": connection_summary(sandbox),
        "run": {"run_id": result.run_id, "status": result.status, "assertions": result.assertions},
    }


def _find_or_create_production(
    store: Any,
    room_id: str,
    payload: Mapping[str, Any],
    *,
    actor: str,
    source: str,
) -> dict[str, Any]:
    vendor = str(payload.get("vendor") or "")
    object_name = str(payload.get("object_name") or "")
    base_url = str(payload.get("base_url") or "")
    if not (vendor and object_name and base_url):
        raise SandboxError(
            "a self-test needs vendor, object_name and base_url to know what connector it is validating"
        )
    for record in list_connections(store, room_id):
        data = record["data"]
        if (
            data.get("environment") == "production"
            and data.get("vendor") == vendor
            and data.get("object_name") == object_name
            and data.get("base_url", "").rstrip("/") == base_url.rstrip("/")
        ):
            return record
    created = store.create(
        COLLECTION_CONNECTION,
        {
            "vendor": vendor,
            "base_url": base_url,
            "object_name": object_name,
            "key_field": str(payload.get("key_field") or "External_Engagement_Id__c"),
            "field_map": dict(payload.get("field_map") or {}),
            "tenant": str(payload.get("tenant") or "acme"),
            "label": str(payload.get("label") or f"{vendor} connector"),
            "environment": "production",
            "notes": "created by the self-test command",
        },
        room_id=room_id,
        actor=actor,
        source=source,
    )
    return created


def ci_run(
    store: Any,
    room_id: str,
    payload: Mapping[str, Any],
    *,
    transport: Transport,
    now: datetime | None = None,
    actor: str = "ci",
    source: str,
) -> dict[str, Any]:
    """The researched CI path: create the test account from the config, then smoke-test. 200.

    Every push creates a fresh test account from the config file - the
    vendor-documented automation path - and the report is shaped for a job
    step: ``ci.pass``, the run id to read, and the assertions verbatim.
    """
    now = now or datetime.now(timezone.utc)
    require_room(store, room_id)
    connection_id = str(payload.get("connection_id") or "")
    if not connection_id:
        raise SandboxError("a CI run names the connection it validates; supply 'connection_id'")
    kind = str(payload.get("kind") or "power_platform")
    spec = dict(payload.get("spec") or {"kind": kind})
    spec["kind"] = kind
    spec["room_id"] = room_id
    spec.setdefault("owner", actor)
    sandbox = create_test_environment(
        store, connection_id, spec, now=now, actor=actor, source=source
    )
    result = execute_run(
        store, sandbox["id"], transport=transport, now=now, actor=actor, source=source
    )
    return {
        "ci": {
            "pass": result.passed,
            "status": result.status,
            "run_id": result.run_id,
            "connection_id": connection_id,
            "test_environment_id": sandbox["id"],
            "aborted_reason": result.aborted_reason,
            "summary": (
                f"test sync {result.status}"
                + (f" (stopped: {result.aborted_reason})" if result.aborted_reason else "")
            ),
        },
        "run": {"run_id": result.run_id, "status": result.status, "assertions": result.assertions},
    }
