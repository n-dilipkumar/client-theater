"""The test sync: the full pipeline run against a sandbox, and its assertions.

The researched flow (user flow step 4 and the assertions in step 5) is the
specification:

1. The room runs **Run test sync** — the full W3→W6 pipeline against the
   sandbox with synthetic buyers.
2. The room asserts: **object created**, **dedupe key honoured**, **rollback
   fired on an intentionally bad row**, and **quota headers behaved**.

The probes share one request builder with the shape a production connector
would exercise - a keyed upsert, an allOrNone chunk, quota headers read from
every answer - and every assertion is computed from what the sandbox actually
answered, never from a status the room writes by hand (see
``assertions_are_evaluated_from_the_vendors_own_answers`` in
:mod:`dsr.connector_sandbox.inferences`).

A run is atomic with its write-back: the run record, the connection's
``last_run_*`` fields and the promotion bookkeeping all land in one
transaction through the audited store, so a half-recorded run is impossible.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.connector_sandbox.errors import (
    SandboxError,
    TestSyncRefused,
)
from dsr.connector_sandbox.transport import (
    DEFAULT_BACKOFF_SECONDS,
    SandboxResponse,
    Transport,
    parse_quota,
)
from dsr.connector_sandbox.vocabulary import (
    ASSERTION_KINDS,
    POISON_MARKER,
    RUN_STATUS_FAILED,
    RUN_STATUS_PASSED,
)

COLLECTION_RUN = "connector_sandbox_run"

#: Steps of the run, in the order the pipeline executes them.
STEPS = ("create", "dedupe", "rollback")

#: Which researched assertion each step is scored as.
STEP_ASSERTIONS: dict[str, str] = {
    "create": "object_created",
    "dedupe": "dedupe_key_honoured",
    "rollback": "rollback_fired",
}


class RunResult:
    """The run's outcome as plain data, ready to store and to serve."""

    def __init__(
        self,
        *,
        run_id: str,
        connection_id: str,
        room_id: str | None,
        status: str,
        assertions: list[dict[str, Any]],
        requests: list[dict[str, Any]],
        quota: dict[str, Any],
        fixture: dict[str, Any],
        aborted_reason: str = "",
    ) -> None:
        self.run_id = run_id
        self.connection_id = connection_id
        self.room_id = room_id
        self.status = status
        self.assertions = assertions
        self.requests = requests
        self.quota = quota
        self.fixture = fixture
        self.aborted_reason = aborted_reason

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "connection_id": self.connection_id,
            "room_id": self.room_id,
            "status": self.status,
            "aborted_reason": self.aborted_reason,
            "assertions": self.assertions,
            "requests": self.requests,
            "quota": self.quota,
            "fixture": self.fixture,
        }


def default_fixture(connection: Mapping[str, Any]) -> dict[str, Any]:
    """The synthetic-buyer dataset the research's step 4 asks for.

    Three buyer identities drive the rows; the dedupe probe re-sends the
    create row through the same keyed upsert; the rollback chunk carries one
    good row and one intentionally bad one. The fixture is deterministic per
    connection, so two runs of one connection send the same keys and a
    re-run is the dedupe case by construction.
    """
    base = f"wf-048-fixture-{str(connection.get('id') or connection.get('connection_id') or '')[-12:]}"
    buyers = [f"synthetic.buyer-{i}@sandbox.example" for i in (1, 2, 3)]
    row = {
        "key": base,
        "fields": {
            "event_type": "viewed",
            "buyer_email": buyers[0],
            "account": str(connection.get("tenant") or "acme"),
        },
    }
    good = {
        "key": f"{base}-good",
        "fields": {"event_type": "downloaded", "buyer_email": buyers[1], "account": "acme"},
    }
    bad = {
        "key": f"{base}-bad",
        "fields": {"event_type": POISON_MARKER, "buyer_email": buyers[2], "account": "acme"},
        "poisoned": True,
    }
    return {
        "buyers": buyers,
        "key_field": str(connection.get("key_field") or "External_Engagement_Id__c"),
        "object_name": str(connection.get("object_name") or "Engagement__c"),
        "create_row": row,
        "dedupe_row": dict(row),
        "rollback_chunk": [good, bad],
    }


def build_upsert_request(
    connection: Mapping[str, Any], fixture: Mapping[str, Any], key_value: str, fields: Mapping[str, Any]
) -> dict[str, Any]:
    """The keyed-upsert shape the production connector exercises.

    ``PATCH {base_url}/{object}/{key_field}/{key_value}`` with the field
    payload and no id field: the key value is the address. The same builder
    serves the create probe and the dedupe probe, which is what makes the
    two probes "the same code path".
    """
    key_field = str(fixture.get("key_field") or connection.get("key_field") or "External_Engagement_Id__c")
    object_name = str(fixture.get("object_name") or connection.get("object_name") or "Engagement__c")
    base_url = str(connection.get("base_url") or "").rstrip("/")
    path = f"{base_url}/{object_name}/{key_field}/{key_value}"
    body = dict(fields)
    body.pop("id", None)
    body.pop("Id", None)
    return {"method": "PATCH", "url": path, "query": {}, "body": body}


def build_bulk_request(
    connection: Mapping[str, Any], fixture: Mapping[str, Any], chunk: list[Mapping[str, Any]]
) -> dict[str, Any]:
    """The allOrNone chunk the rollback probe sends.

    ``POST {base_url}/{object}/composite?allOrNone=true`` with the two rows:
    the good one beside the intentionally bad one, because the researched
    assertion is about what happened to the good row when the bad one was
    rejected.
    """
    object_name = str(fixture.get("object_name") or connection.get("object_name") or "Engagement__c")
    base_url = str(connection.get("base_url") or "").rstrip("/")
    path = f"{base_url}/{object_name}/composite"
    key_field = str(fixture.get("key_field") or "External_Engagement_Id__c")
    records = []
    for row in chunk:
        record = dict(row["fields"])
        record[key_field] = row["key"]
        records.append(record)
    return {
        "method": "POST",
        "url": path,
        "query": {"allOrNone": "true"},
        "body": {"records": records},
    }


def _assertion(kind: str, outcome: str, detail: str) -> dict[str, Any]:
    return {"kind": kind, "outcome": outcome, "detail": detail}


def _record_id(response: SandboxResponse) -> str:
    body = response.json()
    if isinstance(body, dict):
        for key in ("id", "Id", "record_id"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _created_flag(response: SandboxResponse) -> bool:
    body = response.json()
    if isinstance(body, dict):
        return bool(body.get("created"))
    return False


def run_test_sync(
    store: Any,
    connection_record: Mapping[str, Any],
    *,
    transport: Transport,
    fixture: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    actor: str = "system",
    source: str = "test",
    request_id: str | None = None,
) -> RunResult:
    """Run the full pipeline against one connection and score the four assertions.

    The connection must be a test environment: running against a production
    connection is refused (:class:`TestSyncRefused`), which is the researched
    safety property, not an extra rule.

    The run's conduct on quota is the quota assertion: the first 429 or
    exhausted-remaining answer stops the run, records the ``Retry-After``
    (or the default back-off) and marks the probes that never ran as
    ``skipped``. The assertion is then scored against the recorded request
    log: nothing may go out after the first signal, and the back-off must be
    recorded.
    """
    if connection_record.get("collection") != "connector_sandbox_connection":
        raise SandboxError("run_test_sync expects a connector_sandbox_connection record")
    connection_id = str(connection_record["id"])
    environment = str(connection_record["data"].get("environment") or "production")
    if environment == "production":
        raise TestSyncRefused(
            "the full pipeline runs against the sandbox with synthetic buyers; "
            "refusing to point a test sync at a production connection"
        )

    now = now or datetime.now(timezone.utc)
    fixture = dict(fixture) if fixture is not None else default_fixture(connection_record)
    assertions: list[dict[str, Any]] = []
    requests_log: list[dict[str, Any]] = []
    quota: dict[str, Any] = {
        "used": None,
        "total": None,
        "remaining": None,
        "retry_after": None,
        "exhausted": False,
        "hit_at": None,
        "backoff_seconds": None,
        "signal_statuses": [],
    }
    stopped = {"now": False}

    def call(step: str, request: Mapping[str, Any]) -> SandboxResponse:
        started = time.perf_counter()
        response = transport.request(
            request["method"], request["url"], body=request["body"], timeout=10.0, step=step
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        signal = parse_quota(response.headers, response.status)
        entry = {
            "step": step,
            "method": request["method"],
            "url": request["url"],
            "status": response.status,
            "duration_ms": round(elapsed_ms, 3),
            "quota_signal": bool(signal["exhausted"]),
        }
        requests_log.append(entry)
        for key in ("used", "total", "remaining", "retry_after"):
            if signal.get(key) is not None:
                quota[key] = signal[key]
        if signal["exhausted"]:
            quota["hit_at"] = now.isoformat(timespec="seconds")
            quota["backoff_seconds"] = float(signal["retry_after"] or DEFAULT_BACKOFF_SECONDS)
            quota["signal_statuses"].append(response.status)
            quota["exhausted"] = True
            stopped["now"] = True
        return response

    first_record_id = ""

    # Step 1: create. The sandbox must create the object and hand back its id.
    create_row = fixture["create_row"]
    if not stopped["now"]:
        response = call(
            "create",
            build_upsert_request(connection_record, fixture, create_row["key"], create_row["fields"]),
        )
        if stopped["now"]:
            # The create probe itself drew the 429 / exhausted signal: nothing
            # about the object was proven, so the assertion is skipped.
            assertions.append(
                _assertion("object_created", "skipped", "skipped: the create probe drew the quota signal")
            )
        elif response.ok and _record_id(response):
            first_record_id = _record_id(response)
            assertions.append(
                _assertion("object_created", "passed", f"the sandbox created the object and returned {first_record_id}")
            )
        else:
            assertions.append(
                _assertion("object_created", "failed", f"the create probe was not honoured: HTTP {response.status}")
            )

    # Step 2: dedupe. The same key again through the same upsert.
    if stopped["now"]:
        assertions.append(
            _assertion("dedupe_key_honoured", "skipped", "skipped: the run stopped on a quota signal")
        )
    elif not first_record_id:
        assertions.append(
            _assertion(
                "dedupe_key_honoured",
                "skipped",
                "skipped: the create probe failed, so there is nothing to dedupe against",
            )
        )
    else:
        dedupe_row = fixture["dedupe_row"]
        response = call(
            "dedupe", build_upsert_request(connection_record, fixture, dedupe_row["key"], dedupe_row["fields"])
        )
        if _created_flag(response):
            # The vendor says it *created* on a resend of an existing key: a
            # second record exists, which is exactly what the dedupe key is
            # supposed to prevent.
            assertions.append(
                _assertion(
                    "dedupe_key_honoured",
                    "failed",
                    f"resending the dedupe key created a second record "
                    f"({_record_id(response) or 'no id returned'} instead of {first_record_id})",
                )
            )
        elif not response.ok:
            assertions.append(
                _assertion(
                    "dedupe_key_honoured",
                    "failed",
                    f"resending the dedupe key did not resolve to the existing record: HTTP {response.status}",
                )
            )
        else:
            second_id = _record_id(response)
            if second_id and second_id != first_record_id:
                assertions.append(
                    _assertion(
                        "dedupe_key_honoured",
                        "failed",
                        f"resending the dedupe key resolved to a different record "
                        f"({second_id}, not {first_record_id})",
                    )
                )
            else:
                assertions.append(
                    _assertion(
                        "dedupe_key_honoured",
                        "passed",
                        f"resending the dedupe key updated the same record {first_record_id}",
                    )
                )

    # Step 3: rollback. One good row beside one intentionally bad one, allOrNone.
    if stopped["now"]:
        assertions.append(_assertion("rollback_fired", "skipped", "skipped: the run stopped on a quota signal"))
    else:
        request = build_bulk_request(connection_record, fixture, fixture["rollback_chunk"])
        response = call("rollback", request)
        body = response.json() if response.body else None
        items = None
        if isinstance(body, dict) and isinstance(body.get("records"), list):
            items = body["records"]
        # "Rollback fired" means BOTH halves: the bad row was rejected, and
        # the good row beside it was not written either. A wholesale HTTP
        # rejection with no per-item results counts as the first half (the
        # vendor refused the request outright); per-item results are checked
        # for written rows, because a status alone does not prove the chunk
        # rolled back.
        written = (
            any(bool(item.get("success")) for item in items) if items is not None else None
        )
        if written is False or (written is None and response.status >= 400):
            assertions.append(
                _assertion(
                    "rollback_fired",
                    "passed",
                    f"the bad row was rejected with HTTP {response.status}"
                    + (" and no row in the chunk was written" if written is False else ""),
                )
            )
        elif written is True:
            assertions.append(
                _assertion(
                    "rollback_fired",
                    "failed",
                    f"the chunk answered HTTP {response.status} but a row in it was written; "
                    "the rollback did not fire",
                )
            )
        else:
            assertions.append(
                _assertion(
                    "rollback_fired",
                    "failed",
                    f"the chunk was accepted (HTTP {response.status}); the bad row was not rejected, "
                    "so the rollback did not fire",
                )
            )

    # Step 4: quota behaviour, scored on the recorded request log.
    signal_index = next(
        (index for index, entry in enumerate(requests_log) if entry["quota_signal"]),
        None,
    )
    if signal_index is None:
        assertions.append(
            _assertion(
                "quota_headers_behaved",
                "passed",
                "the vendor never signalled a quota limit, so there was nothing to honour",
            )
        )
    else:
        after = len(requests_log) - 1 - signal_index
        if quota["backoff_seconds"] and after == 0:
            assertions.append(
                _assertion(
                    "quota_headers_behaved",
                    "passed",
                    f"the run stopped on the first quota signal and recorded a "
                    f"{quota['backoff_seconds']:g}s back-off",
                )
            )
        else:
            assertions.append(
                _assertion(
                    "quota_headers_behaved",
                    "failed",
                    f"the run made {after} further request(s) after the quota signal",
                )
            )

    # Green means all four assertions passed: a skipped probe is not evidence.
    green = all(entry["outcome"] == "passed" for entry in assertions)
    status = RUN_STATUS_PASSED if green else RUN_STATUS_FAILED
    return RunResult(
        run_id="",
        connection_id=connection_id,
        room_id=str(connection_record["room_id"] or ""),
        status=status,
        assertions=assertions,
        requests=requests_log,
        quota=quota,
        fixture=fixture,
        aborted_reason="quota_exhausted" if signal_index is not None else "",
    )
