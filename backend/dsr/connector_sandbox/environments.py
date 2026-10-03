"""Test environments for WF-048: a sandbox, a HubSpot test account, a trial.

The researched rule that shapes this module is the extensibility line: a test
environment is just another connection row, because the connector is
parameterised by base URL + credentials. So creating one clones the production
connection's *mapping* (same object, same key field, same field map - the
"same mapping + same code path as production" half of the researched data
flow) and replaces only the environment coordinates: a sandbox base URL or a
separate OAuth client.

What is enforced rather than assumed, each with its source:

* ``production`` cannot be a test environment (the vocabulary separates them).
* A sandbox supports copy and reset; the row records what it was copied from.
* The default environment warns that it "doesn't provide any backup
  guarantees and shouldn't be used for production workloads".
* A trial expires after 30 days and is limited to one per user - both
  enforced, with the expiry computed and stored.
* A HubSpot configurable test account requires platform ``2025.2``+ and CLI
  ``8.3.0``+, and is created from a config file that simulates a
  subscription/tier.
* Converting production to sandbox "can't be blocked" - the conversion path
  is always available even where provisioning a sandbox is admin-restricted.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from dsr.connector_sandbox.errors import (
    CliTooOldError,
    PlatformTooOldError,
    ProductionEnvironmentRefused,
    SandboxError,
    TrialLimitError,
    UnknownConnectionError,
    UnknownEnvironmentKind,
    UnknownRoomError,
)
from dsr.connector_sandbox.vocabulary import (
    ENVIRONMENT_FACTS,
    HUBSPOT_CLI_COMMAND,
    HUBSPOT_MIN_CLI,
    HUBSPOT_MIN_PLATFORM,
    TEST_ENVIRONMENT_TYPES,
)

#: The sourced trial lifetime, as a number the expiry is computed from.
TRIAL_LIFETIME_DAYS = 30

COLLECTION_CONNECTION = "connector_sandbox_connection"


def version_tuple(version: str) -> tuple[int, ...]:
    """Parse ``2025.2`` / ``8.3.0`` into comparable numbers.

    Each segment takes its *leading* digits, so ``2025.2-beta1`` reads as
    ``2025.2`` rather than ``2025.21``, and a segment with no digits reads
    as 0. An empty string is older than everything, because an unreported
    version cannot be assumed to meet a floor.
    """
    parts: list[int] = []
    for chunk in str(version or "").strip().split("."):
        digits = ""
        for char in chunk:
            if char.isdigit():
                digits += char
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts) if parts else (0,)


def meets_floor(version: str, floor: str) -> bool:
    """``2025.2`` >= ``2025.2`` is True, ``2025.1`` is not, ``2025.2.1`` is."""
    return version_tuple(version) >= version_tuple(floor)


def is_expired(expires_at: str, now: datetime) -> bool:
    """A stored ISO expiry is past when the clock says now."""
    if not expires_at:
        return False
    try:
        parsed = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed < now


def _clone_mapping(connection: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    """The production mapping the clone must keep: same code path as production."""
    carried = (
        "vendor",
        "label",
        "tenant",
        "object_name",
        "key_field",
        "field_map",
        "required_properties",
    )
    cloned: dict[str, Any] = {key: connection[key] for key in carried if connection.get(key)}
    cloned.update(overrides)
    return cloned


def production_row(store: Any, connection_id: str, *, source: str) -> dict[str, Any]:
    """Load a live connection row, refusing an unknown id."""
    from dsr.connector_sandbox.errors import UnknownConnectionError

    record = store.get(connection_id)
    if record is None or record["collection"] != COLLECTION_CONNECTION:
        raise UnknownConnectionError(connection_id)
    if record["deleted_at"] is not None:
        raise UnknownConnectionError(connection_id)
    del source
    return record


def list_test_environment_rows(store: Any, connection_id: str) -> list[dict[str, Any]]:
    """Every non-reverted test-environment row cloned from this connection."""
    rows = store.find(
        COLLECTION_CONNECTION,
        {"source_connection_id": connection_id},
        limit=200,
    )
    return [row for row in rows if row["data"].get("reverted_at") is None]


def _require_room(store: Any, room_id: str) -> None:
    from dsr.connector_sandbox.errors import UnknownRoomError

    if store.get(room_id) is None:
        raise UnknownRoomError(room_id)


def _no_second_live_trial(store: Any, owner: str, now: datetime) -> None:
    """"limited to one per user" - refused, with the live trial named."""
    rows = store.find(COLLECTION_CONNECTION, {"test_env_kind": "power_platform"}, limit=500)
    for row in rows:
        data = row["data"]
        if data.get("env_type") != "trial":
            continue
        if data.get("owner") != owner:
            continue
        if data.get("reverted_at"):
            continue
        if not is_expired(str(data.get("expires_at") or ""), now):
            raise TrialLimitError(
                f"user {owner} already holds a live trial environment "
                f"({row['id']}); trial environments are limited to one per user"
            )


def _assert_hubspot_floors(platform_version: str, cli_version: str) -> None:
    if not meets_floor(platform_version, HUBSPOT_MIN_PLATFORM):
        raise PlatformTooOldError(
            f"creating a configurable test account needs platform version "
            f"{HUBSPOT_MIN_PLATFORM} or later; this project reports {platform_version or 'none'}"
        )
    if not meets_floor(cli_version, HUBSPOT_MIN_CLI):
        raise CliTooOldError(
            f"creating a configurable test account needs CLI version "
            f"{HUBSPOT_MIN_CLI} or later; this install reports {cli_version or 'none'}"
        )


def create_test_environment(
    store: Any,
    connection_id: str,
    spec: Mapping[str, Any],
    *,
    now: datetime,
    actor: str,
    source: str,
) -> dict[str, Any]:
    """Create the sandbox connection row a validation runs against. 201.

    ``kind`` is ``power_platform`` (a sandbox, default, trial or developer
    environment) or ``hubspot`` (a configurable test account). Everything the
    production row carries about *how to map* is cloned unchanged; everything
    about *where* is replaced by the test environment's coordinates.

    Returns the new row, with ``env_type``, the sourced facts it was created
    under, and - for a trial - the computed expiry.
    """
    _require_room(store, str(spec.get("room_id") or connection_room(store, connection_id)))
    connection = production_row(store, connection_id, source=source)
    kind = str(spec.get("kind") or "power_platform")
    if kind not in ("power_platform", "hubspot"):
        raise UnknownEnvironmentKind(
            f"test environment kind {kind!r} is not one of power_platform, hubspot"
        )

    room_id = str(connection["room_id"] or spec.get("room_id") or "")
    mapping = connection["data"]  # the cloned row reads the *data* payload
    if kind == "hubspot":
        platform_version = str(spec.get("platform_version") or "")
        cli_version = str(spec.get("cli_version") or "")
        _assert_hubspot_floors(platform_version, cli_version)
        config = dict(spec.get("config") or {})
        if not config:
            raise SandboxError(
                "a configurable test account is created from a config file that "
                "simulates a subscription/tier; supply it as 'config'"
            )
        payload: dict[str, Any] = _clone_mapping(
            mapping,
            {
                "environment": "test",
                "test_env_kind": "hubspot",
                "env_type": "hubspot_test_account",
                "base_url": str(spec.get("base_url") or f"https://testaccount-{now.strftime('%Y%m%d%H%M%S')}.hubapi.com"),
                "credential": str(spec.get("credential") or f"test-account-token-{now.strftime('%Y%m%d%H%M%S')}"),
                "config": config,
                "platform_version": platform_version,
                "cli_version": cli_version,
                "cli_command": HUBSPOT_CLI_COMMAND,
                "source_connection_id": connection_id,
                "reverted_at": None,
                "expires_at": None,
            },
        )
    else:
        env_type = str(spec.get("env_type") or "sandbox")
        if env_type not in TEST_ENVIRONMENT_TYPES:
            if env_type == "production":
                raise ProductionEnvironmentRefused(
                    "production cannot be a test environment: the researched "
                    "vocabulary separates the two so tests cannot be pointed "
                    "at a real org"
                )
            raise SandboxError(
                f"env_type {env_type!r} is not one of {', '.join(TEST_ENVIRONMENT_TYPES)}"
            )
        if env_type == "trial":
            owner = str(spec.get("owner") or actor)
            _no_second_live_trial(store, owner, now)
        facts = ENVIRONMENT_FACTS[env_type]
        expires_at = (
            (now + timedelta(days=TRIAL_LIFETIME_DAYS)).isoformat(timespec="seconds")
            if env_type == "trial"
            else None
        )
        payload = _clone_mapping(
            mapping,
            {
                "environment": "test",
                "test_env_kind": "power_platform",
                "env_type": env_type,
                "base_url": str(
                    spec.get("base_url") or f"https://{env_type}-{now.strftime('%Y%m%d%H%M%S')}.crm.dynamics.com"
                ),
                "credential": str(spec.get("credential") or f"sandbox-client-{now.strftime('%Y%m%d%H%M%S')}"),
                "owner": str(spec.get("owner") or actor),
                "copied_from": mapping.get("base_url") if env_type == "sandbox" else None,
                "resettable": bool(facts.get("supports_reset")),
                "warning": (
                    "the default environment doesn't provide any backup guarantees and "
                    "shouldn't be used for production workloads"
                    if env_type == "default"
                    else ""
                ),
                "expires_at": expires_at,
                "source_connection_id": connection_id,
                "reverted_at": None,
            },
        )

    label = str(spec.get("label") or f"{mapping.get('label') or 'Connection'} — {payload.get('env_type')}")
    created = store.create(
        COLLECTION_CONNECTION,
        {**payload, "label": label},
        room_id=room_id or None,
        actor=actor,
        source=source,
    )
    return created


def connection_room(store: Any, connection_id: str) -> str:
    """The room a connection serves, read off its row."""
    record = store.get(connection_id)
    if record is None:
        raise UnknownConnectionError(connection_id)
    return str(record["room_id"] or "")


def convert_production_to_sandbox(
    store: Any,
    connection_id: str,
    *,
    now: datetime,
    actor: str,
    source: str,
) -> dict[str, Any]:
    """Convert a production connection's environment to sandbox. Never refused.

    Sourced: "converting from a production to a sandbox environment can't be
    blocked", even where provisioning a sandbox can be admin-restricted. The
    conversion records who converted and when; refusing it here would
    implement a block the vendor explicitly says cannot be imposed.
    """
    record = production_row(store, connection_id, source=source)
    updated = store.update(
        connection_id,
        {
            "environment": "converted_sandbox",
            "converted_at": now.isoformat(timespec="seconds"),
            "previous_environment": record["data"].get("environment") or "production",
        },
        actor=actor,
        source=source,
    )
    return updated


