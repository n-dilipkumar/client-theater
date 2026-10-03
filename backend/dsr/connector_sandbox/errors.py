"""Domain errors for WF-048, validating a connector against a sandbox.

Each type is this feature's own, which matters: the plugin host refuses two
features mapping the same exception type, and a global handler for a shared
type would intercept an error raised anywhere else in the product. Nothing
here subclasses a core error, so registering these handlers cannot capture a
``RecordNotFound`` from another feature.

The hierarchy is the researched shape of the failure, not a convenience:

``SandboxError``
    A well-formed request asking this validator to do something it will not
    do. The caller's to fix, so it answers 400.
``UnknownRoomError`` / ``UnknownConnectionError`` / ``UnknownRunError``
    The thing named does not exist. 404, deliberately distinct from an empty
    success so a client cannot mistake a wrong id for an empty list.
``TestSyncRefused``
    The room asks the connector to run a test sync somewhere the research
    says it must not: against a production environment. 400.
``NotValidatedError``
    Promotion was asked for without a green run behind it. 422: the request
    is well-formed, and the missing thing is evidence, not permission.
``VendorRequestError``
    The vendor's own endpoint failed in a way this layer cannot interpret.
    502, retryable by the caller.
"""

from __future__ import annotations


class SandboxError(ValueError):
    """A well-formed request this validator will not act on. Answers 400."""


class UnknownRoomError(LookupError):
    """No such room. Answers 404."""

    def __init__(self, room_id: str) -> None:
        super().__init__(room_id)
        self.room_id = room_id

    def __str__(self) -> str:  # pragma: no cover - exercised through HTTP
        return f"room {self.room_id} not found"


class UnknownConnectionError(LookupError):
    """No such sandbox connection. Answers 404."""

    def __init__(self, connection_id: str) -> None:
        super().__init__(connection_id)
        self.connection_id = connection_id

    def __str__(self) -> str:  # pragma: no cover - exercised through HTTP
        return f"connection {self.connection_id} not found"


class UnknownRunError(LookupError):
    """No such test-sync run. Answers 404."""

    def __init__(self, run_id: str) -> None:
        super().__init__(run_id)
        self.run_id = run_id

    def __str__(self) -> str:  # pragma: no cover - exercised through HTTP
        return f"run {self.run_id} not found"


class TestSyncRefused(SandboxError):
    """A test sync was asked for against an environment the research forbids.

    The researched flow is explicit that the full pipeline runs "against the
    sandbox with synthetic buyers" - step 4 of the user flow - and that the
    connection goes to production only at step 6, after green. Pointing a
    run at a production connection would write synthetic buyers into a real
    org, which is the failure this validation exists to prevent.
    """

    # pytest collects any module-level class named Test*; this is a domain
    # error, not a test class.
    __test__ = False


class ProductionEnvironmentRefused(SandboxError):
    """``production`` cannot be a test environment.

    The researched Power Platform vocabulary separates Production from the
    non-production types (Sandbox, Default, Trial, Developer) precisely so
    tests cannot be pointed at production. Refused rather than relabelled.
    """


class TrialLimitError(SandboxError):
    """A second trial environment for one user is refused.

    Sourced: "Trial environments … expire after 30 days and are limited to
    one per user." A limit stated as a number is enforced, not warned about.
    """


class PlatformTooOldError(SandboxError):
    """A HubSpot configurable test account needs platform 2025.2 or later.

    Sourced: "To create a configurable test account, you'll need to be
    developing a project on platform version ``2025.2`` or later."
    """


class CliTooOldError(SandboxError):
    """A HubSpot configurable test account needs CLI 8.3.0 or later.

    Sourced: same sentence as :class:`PlatformTooOldError` - "and using CLI
    version ``8.3.0`` or later."
    """


class NotValidatedError(SandboxError):
    """Promotion was asked for without a green test sync behind it. 422.

    Distinct from 400 on purpose: the request is well formed and the
    connection exists - what is missing is a *green run*, which is the
    researched promotion rule ("Only after green does the admin switch the
    connection to production"), not a fixable request shape.
    """


class VendorRequestError(RuntimeError):
    """The vendor's own endpoint failed in a way this layer cannot interpret.

    502, retryable by the caller: nothing about the request was wrong.
    """


class UnknownEnvironmentKind(SandboxError):
    """The test environment kind is not one this validator serves."""
