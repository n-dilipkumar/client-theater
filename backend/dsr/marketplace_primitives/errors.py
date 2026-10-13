"""The WF-115 refusal hierarchy.

FastAPI takes exception handlers on the application object only, never on a
router, so the feature module exports ``EXCEPTION_HANDLERS`` and the plugin host
attaches it. One handler covers the whole family and dispatches on the subclass,
because a feature may map an error type no other feature has mapped and two
features may not map the same type.

Every refusal carries the HTTP status and the machine code its next step implies,
so the mapping lives with the error rather than in the route that raises it.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Status codes
# --------------------------------------------------------------------------- #

#: A request that was understood but conflicts with the module's current state:
#: an immutable key edited, a duplicate key, a transition the machine refuses, or
#: a delivery for a module that is not published.
STATUS_CONFLICT = 409

#: A malformed payload: a bad key, a second Dynamic filter, a bad sample payload,
#: a missing required field.
STATUS_UNPROCESSABLE = 422

#: The module does not resolve to a live record.
STATUS_NOT_FOUND = 404


class MarketplaceRefusal(RuntimeError):
    """Base class for every refusal this feature raises."""

    status = STATUS_UNPROCESSABLE
    code = "marketplace_refused"


class ModuleNotFound(MarketplaceRefusal):
    """The module id does not resolve, or the key names no live module."""

    status = STATUS_NOT_FOUND
    code = "module_not_found"


class ModuleNotPublished(MarketplaceRefusal):
    """A delivery or a resolution needs the module to be published and it is not."""

    status = STATUS_CONFLICT
    code = "module_not_published"


class KeyInvalid(MarketplaceRefusal):
    """The key does not match the immutable-key pattern."""

    status = STATUS_UNPROCESSABLE
    code = "key_invalid"


class KeyTaken(MarketplaceRefusal):
    """Another live module already holds this key."""

    status = STATUS_CONFLICT
    code = "key_taken"


class KeyImmutable(MarketplaceRefusal):
    """An edit tried to change the immutable key."""

    status = STATUS_CONFLICT
    code = "key_immutable"


class DynamicFilterLimitExceeded(MarketplaceRefusal):
    """More than one Dynamic filter was declared, for a trigger or an action."""

    status = STATUS_UNPROCESSABLE
    code = "dynamic_filter_limit"


class ModuleInvalid(MarketplaceRefusal):
    """One or more validation rules rejected the module payload.

    ``problems`` carries every problem found, so a builder fixes them in one
    pass instead of one per round trip, and the detail names them.
    """

    status = STATUS_UNPROCESSABLE
    code = "module_invalid"

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems) or "the module payload is invalid")


class TransitionRefused(MarketplaceRefusal):
    """The review state machine does not allow the requested transition."""

    status = STATUS_CONFLICT
    code = "transition_refused"


#: Type -> (status, code). The route handler dispatches on the concrete class so
#: the base class needs no per-instance override.
REFUSAL_STATUS: dict[type[MarketplaceRefusal], tuple[int, str]] = {
    ModuleNotFound: (STATUS_NOT_FOUND, "module_not_found"),
    ModuleNotPublished: (STATUS_CONFLICT, "module_not_published"),
    KeyInvalid: (STATUS_UNPROCESSABLE, "key_invalid"),
    KeyTaken: (STATUS_CONFLICT, "key_taken"),
    KeyImmutable: (STATUS_CONFLICT, "key_immutable"),
    DynamicFilterLimitExceeded: (STATUS_UNPROCESSABLE, "dynamic_filter_limit"),
    ModuleInvalid: (STATUS_UNPROCESSABLE, "module_invalid"),
    TransitionRefused: (STATUS_CONFLICT, "transition_refused"),
}


__all__ = [
    "DynamicFilterLimitExceeded",
    "KeyImmutable",
    "KeyInvalid",
    "KeyTaken",
    "MarketplaceRefusal",
    "ModuleInvalid",
    "ModuleNotFound",
    "ModuleNotPublished",
    "REFUSAL_STATUS",
    "STATUS_CONFLICT",
    "STATUS_NOT_FOUND",
    "STATUS_UNPROCESSABLE",
    "TransitionRefused",
]
