"""WF-048: validate the connector against a sandbox or test account.

The domain behind the researched workflow. The research document is the
specification: ``docs/research/digital-sales-room-workflows/wf/WF-048.md``
(source: ``docs/research/raw/crm-integration.md`` section 16).

The researched flow, as the spec states it
-------------------------------------------
1. Admin opens **Integrations → <connection> → Test environment** and points
   the connection at a non-production org.
2. HubSpot: admin (or CI) creates a **configurable test account** that
   simulates a specific subscription/tier.
3. Dataverse/Power Platform: admin converts or creates a **Sandbox**
   environment (copy + reset supported).
4. The room runs **Run test sync** — the full W3→W6 pipeline against the
   sandbox with synthetic buyers.
5. The room asserts: object created, dedupe key honoured, rollback fired on
   an intentionally bad row, and quota headers behaved.
6. Only after green does the admin switch the connection to production.

Sourced behaviour this package implements
------------------------------------------
* **A test environment is just another connection row.** "the open-source
  room can ship a 'self-test' command that creates both connection rows and
  runs the fixture" (:func:`dsr.connector_sandbox.automation.self_test`).
* **The Power Platform vocabulary, enforced.** Sandbox (copy + reset),
  Default (no backup guarantees; never for production), Trial (expires
  after 30 days, one per user), Developer, Production. Production cannot be
  a test environment, and converting production to sandbox cannot be
  blocked (:mod:`dsr.connector_sandbox.environments`).
* **HubSpot's version floors.** A configurable test account needs platform
  ``2025.2``+ and CLI ``8.3.0``+, and is created from a config file that
  simulates a subscription/tier - refused, not warned.
* **The four assertions, scored from the vendor's own answers.** object
  created, dedupe key honoured, rollback fired on an intentionally bad row,
  quota headers behaved (:mod:`dsr.connector_sandbox.runs`).
* **The promotion gate.** "Only after green does the admin switch the
  connection to production" - ``promote`` refuses without a green run, and
  the audit row names the run it came from.
* **The CI path.** GitHub Actions creates the test account from a config
  file on every push, then the sync smoke test runs
  (:func:`dsr.connector_sandbox.automation.ci_run`).

What is deliberately not implemented
-------------------------------------
Salesforce sandbox types and scratch orgs could not be sourced (the research
gap), so no Salesforce-specific sandbox behaviour is claimed: a Salesforce
connector validates through the generic sandbox rules.

Schema flexibility
-------------------
Everything this package stores is ordinary JSON in ``records.data``. A team
adding a field to a connection or a run adds it without coordination; the
only fixed vocabulary is the envelope.
"""

from __future__ import annotations

from dsr.connector_sandbox.automation import ci_run, self_test
from dsr.connector_sandbox.connections import (
    COLLECTION_CONNECTION,
    COLLECTION_RUN,
    REQUIRED_CONNECTION_FIELDS,
    VENDORS,
    connection_summary,
    create_connection,
    execute_run,
    latest_run,
    list_connections,
    load_connection,
    overview,
    promote,
    require_promotable,
    require_room,
    revert,
    run_summary,
    validate_connection_payload,
)
from dsr.connector_sandbox.environments import (
    TRIAL_LIFETIME_DAYS,
    connection_room,
    convert_production_to_sandbox,
    create_test_environment,
    list_test_environment_rows,
    meets_floor,
    production_row,
    version_tuple,
)
from dsr.connector_sandbox.errors import (
    CliTooOldError,
    NotValidatedError,
    PlatformTooOldError,
    ProductionEnvironmentRefused,
    SandboxError,
    TestSyncRefused,
    TrialLimitError,
    UnknownConnectionError,
    UnknownEnvironmentKind,
    UnknownRoomError,
    UnknownRunError,
    VendorRequestError,
)
from dsr.connector_sandbox.inferences import INFERENCES, describe as describe_inferences
from dsr.connector_sandbox.runs import (
    POISON_MARKER,
    STEPS,
    STEP_ASSERTIONS,
    RunResult,
    build_bulk_request,
    build_upsert_request,
    default_fixture,
    run_test_sync,
)
from dsr.connector_sandbox.transport import (
    DEFAULT_BACKOFF_SECONDS,
    SandboxResponse,
    ScriptedTransport,
    SimulatedTransport,
    Transport,
    UrllibTransport,
    parse_quota,
)
from dsr.connector_sandbox.vocabulary import describe as describe_vocabulary

__all__ = [
    # automation
    "ci_run",
    "self_test",
    # connections
    "COLLECTION_CONNECTION",
    "COLLECTION_RUN",
    "REQUIRED_CONNECTION_FIELDS",
    "VENDORS",
    "connection_summary",
    "create_connection",
    "execute_run",
    "latest_run",
    "list_connections",
    "load_connection",
    "overview",
    "promote",
    "require_promotable",
    "require_room",
    "revert",
    "run_summary",
    "validate_connection_payload",
    # environments
    "TRIAL_LIFETIME_DAYS",
    "connection_room",
    "convert_production_to_sandbox",
    "create_test_environment",
    "list_test_environment_rows",
    "meets_floor",
    "production_row",
    "version_tuple",
    # errors
    "CliTooOldError",
    "NotValidatedError",
    "PlatformTooOldError",
    "ProductionEnvironmentRefused",
    "SandboxError",
    "TestSyncRefused",
    "TrialLimitError",
    "UnknownConnectionError",
    "UnknownEnvironmentKind",
    "UnknownRoomError",
    "UnknownRunError",
    "VendorRequestError",
    # runs
    "POISON_MARKER",
    "STEPS",
    "STEP_ASSERTIONS",
    "RunResult",
    "build_bulk_request",
    "build_upsert_request",
    "default_fixture",
    "run_test_sync",
    # transport
    "DEFAULT_BACKOFF_SECONDS",
    "SandboxResponse",
    "ScriptedTransport",
    "SimulatedTransport",
    "Transport",
    "UrllibTransport",
    "parse_quota",
    # vocabulary / inferences
    "INFERENCES",
    "describe_inferences",
    "describe_vocabulary",
]
