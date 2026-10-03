"""The researched contract for WF-048, served as data.

Everything here is quoted from ``docs/research/digital-sales-room-workflows/wf/WF-048.md``
and the three sources it cites. Nothing below is a design opinion; the parts
the research left open are recorded in :mod:`dsr.connector_sandbox.inferences`
instead, so a reader can tell the two apart.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# The three sources the research cites, verbatim
# --------------------------------------------------------------------------- #

SOURCES = (
    "https://learn.microsoft.com/en-us/power-platform/admin/environments-overview",
    "https://developers.hubspot.com/docs/developer-tooling/local-development/configurable-test-accounts",
    "https://developers.hubspot.com/docs/developer-tooling/local-development/agent-cli/guide",
)

# --------------------------------------------------------------------------- #
# The researched environment vocabulary (Power Platform)
# --------------------------------------------------------------------------- #

ENVIRONMENT_TYPES: tuple[str, ...] = ("sandbox", "default", "trial", "developer", "production")

#: The environment types the research allows a test environment to be. Production
#: is a real org, and the researched flow exists to keep tests OUT of one.
TEST_ENVIRONMENT_TYPES: tuple[str, ...] = ("sandbox", "default", "trial", "developer")

#: What each type means, in the source's own words, so a picker can render its
#: options from the same facts the validator enforces against.
ENVIRONMENT_FACTS: dict[str, dict[str, Any]] = {
    "sandbox": {
        "label": "Sandbox",
        "source": SOURCES[0],
        "quote": (
            "These are nonproduction environments, which offer features like "
            "copy and reset. Sandbox environments are used for development and "
            "testing, separate from production."
        ),
        "supports_copy": True,
        "supports_reset": True,
        "expires": False,
        "notes": (
            "Provisioning sandbox environments can be restricted to admins "
            "(because production environment creation can be blocked), but "
            "converting from a production to a sandbox environment can't be "
            "blocked."
        ),
    },
    "default": {
        "label": "Default",
        "source": SOURCES[0],
        "quote": (
            "This is a predefined type of environment intended for "
            "experimentation, exploration, and lightweight, app trial "
            "development. The default environment doesn't provide any backup "
            "guarantees and shouldn't be used for production workloads."
        ),
        "supports_copy": False,
        "supports_reset": False,
        "expires": False,
        "note": "usable for a smoke test, never for production data",
    },
    "trial": {
        "label": "Trial",
        "source": SOURCES[0],
        "quote": ("Trial environments … expire after 30 days and are limited to one per user."),
        "supports_copy": False,
        "supports_reset": False,
        "expires": True,
        "lifetime_days": 30,
        "per_user_limit": 1,
    },
    "developer": {
        "label": "Developer",
        "source": SOURCES[0],
        "quote": "single-user environment for lightweight development",
        "supports_copy": False,
        "supports_reset": False,
        "expires": False,
        "note": "one owner, cheap to throw away",
    },
    "production": {
        "label": "Production",
        "source": SOURCES[0],
        "quote": "a real org; the researched flow switches to it only after green",
        "is_test_environment": False,
    },
}

# --------------------------------------------------------------------------- #
# HubSpot configurable test accounts
# --------------------------------------------------------------------------- #

HUBSPOT_MIN_PLATFORM = "2025.2"
HUBSPOT_MIN_CLI = "8.3.0"
HUBSPOT_CLI_COMMAND = "hs test-account create"

HUBSPOT_FACTS = {
    "source": SOURCES[1],
    "quotes": [
        (
            "You can use configurable test accounts to simulate different "
            "HubSpot subscription and tier combinations, enabling you to test "
            "your apps more comprehensively before rolling changes out to "
            "production."
        ),
        (
            "Configurable test accounts can be created and managed in HubSpot "
            "or from the CLI. You can also choose to create your configurable "
            "test accounts from scratch or by uploading a config file, which "
            "enables fully automated CI/CD workflows."
        ),
        (
            "To create a configurable test account, you'll need to be "
            "developing a project on platform version `2025.2` or later and "
            "using CLI version `8.3.0` or later."
        ),
        "In the terminal run the command below: `hs test-account create`",
    ],
    "min_platform": HUBSPOT_MIN_PLATFORM,
    "min_cli": HUBSPOT_MIN_CLI,
    "cli_command": HUBSPOT_CLI_COMMAND,
}

# --------------------------------------------------------------------------- #
# The automation the research names
# --------------------------------------------------------------------------- #

AUTOMATION_FACTS = {
    "source": SOURCES[1],
    "quote": (
        "CI/CD — GitHub Actions creates the test account from a config file on "
        "every push, then the sync smoke test runs."
    ),
    "note": "the research states this as the vendor-documented automation path, not an invention",
}

# --------------------------------------------------------------------------- #
# The assertion kinds, and the run states
# --------------------------------------------------------------------------- #

#: The marker a fixture's poisoned row carries. The sandbox's own validation
#: is what must reject it; the room only asks.
POISON_MARKER = "__wf048_poison__"

ASSERTION_KINDS: dict[str, str] = {
    "object_created": (
        "the sandbox created the CRM object the fixture asked for, and returned its record id"
    ),
    "dedupe_key_honoured": (
        "resending the same dedupe key updated the same record instead of creating a second one"
    ),
    "rollback_fired": (
        "an intentionally bad row in an allOrNone chunk was rejected AND the good row beside it "
        "was not written either"
    ),
    "quota_headers_behaved": (
        "the run stopped sending on the first 429 / exhausted-quota signal and recorded the backoff"
    ),
}

RUN_STATUS_PASSED = "passed"
RUN_STATUS_FAILED = "failed"
RUN_STATES = (RUN_STATUS_PASSED, RUN_STATUS_FAILED)

CONNECTION_KIND_PRODUCTION = "production"

# --------------------------------------------------------------------------- #
# The research gap, stated rather than papered over
# --------------------------------------------------------------------------- #

GAPS = {
    "salesforce_sandbox_types": (
        "Salesforce sandbox types and scratch orgs could not be sourced - the "
        "research records the attempts (cookie banner, 404s) and explicitly "
        "does not use the Limits page as a sandbox-lifecycle reference. No "
        "Salesforce-specific sandbox behaviour is claimed or implemented; a "
        "Salesforce connector validates through the generic sandbox rules "
        "this feature serves."
    ),
}

# --------------------------------------------------------------------------- #
# The user flow, as the spec states it
# --------------------------------------------------------------------------- #

USER_FLOW = (
    "Admin opens Integrations → <connection> → Test environment and points the "
    "connection at a non-production org; the room runs the full W3→W6 pipeline "
    "against the sandbox with synthetic buyers; the room asserts object created, "
    "dedupe key honoured, rollback fired on an intentionally bad row, and quota "
    "headers behaved; only after green does the admin switch the connection to "
    "production.",
)

DATA_FLOW = (
    "integration config → environment switch (sandbox base URL / separate OAuth "
    "client) → test buyer dataset → same mapping + same code path as production "
    "→ assertion report → promote or revert.",
)


def describe() -> dict[str, Any]:
    """The whole researched contract, as data."""
    return {
        "ticket": "WF-048",
        "name": "Validate the connector against a sandbox or test account",
        "domain": "crm-integration",
        "user_flow": USER_FLOW,
        "data_flow": DATA_FLOW,
        "environment_types": ENVIRONMENT_TYPES,
        "test_environment_types": TEST_ENVIRONMENT_TYPES,
        "environment_facts": ENVIRONMENT_FACTS,
        "hubspot": HUBSPOT_FACTS,
        "automation": AUTOMATION_FACTS,
        "assertions": ASSERTION_KINDS,
        "run_states": (RUN_STATUS_PASSED, RUN_STATUS_FAILED),
        "sources": SOURCES,
        "gaps": GAPS,
    }
