"""The WF-118 refusal hierarchy.

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

#: A request that was understood but conflicts with current state: a duplicate or
#: immutable key, or an edit to a guide that is public.
STATUS_CONFLICT = 409

#: A malformed payload: a bad key, an interval out of range, a stop threshold
#: below the floor, a delay past 60 seconds.
STATUS_UNPROCESSABLE = 422

#: The identifier does not resolve to a live record.
STATUS_NOT_FOUND = 404


class GuidePromptsRefusal(RuntimeError):
    """Base class for every refusal this feature raises."""

    status = STATUS_UNPROCESSABLE
    code = "guide_prompts_refused"


class GuideNotFound(GuidePromptsRefusal):
    """The guide key or the ordering application does not resolve."""

    status = STATUS_NOT_FOUND
    code = "guide_not_found"


class KeyInvalid(GuidePromptsRefusal):
    """The key does not match the immutable-key pattern."""

    status = STATUS_UNPROCESSABLE
    code = "key_invalid"


class KeyTaken(GuidePromptsRefusal):
    """Another live guide already holds this key."""

    status = STATUS_CONFLICT
    code = "key_taken"


class KeyImmutable(GuidePromptsRefusal):
    """An edit tried to change the immutable key."""

    status = STATUS_CONFLICT
    code = "key_immutable"


class GuideInvalid(GuidePromptsRefusal):
    """One or more validation rules rejected the guide payload.

    ``problems`` carries every problem found, so an author fixes them in one pass
    instead of one per round trip, and the detail names them.
    """

    status = STATUS_UNPROCESSABLE
    code = "guide_invalid"

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems) or "the guide payload is invalid")


class OrderInvalid(GuidePromptsRefusal):
    """One or more validation rules rejected the ordering document.

    This covers a bad throttle interval, a list that names an unknown guide, and a
    move that is not a known arrow.
    """

    status = STATUS_UNPROCESSABLE
    code = "order_invalid"

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems) or "the ordering document is invalid")


class EventInvalid(GuidePromptsRefusal):
    """An interaction event is not one of the events the research names."""

    status = STATUS_UNPROCESSABLE
    code = "event_invalid"


#: Type -> (status, code). The route handler dispatches on the concrete class so
#: the base class needs no per-instance override.
REFUSAL_STATUS: dict[type[GuidePromptsRefusal], tuple[int, str]] = {
    GuideNotFound: (STATUS_NOT_FOUND, "guide_not_found"),
    KeyInvalid: (STATUS_UNPROCESSABLE, "key_invalid"),
    KeyTaken: (STATUS_CONFLICT, "key_taken"),
    KeyImmutable: (STATUS_CONFLICT, "key_immutable"),
    GuideInvalid: (STATUS_UNPROCESSABLE, "guide_invalid"),
    OrderInvalid: (STATUS_UNPROCESSABLE, "order_invalid"),
    EventInvalid: (STATUS_UNPROCESSABLE, "event_invalid"),
}


__all__ = [
    "EventInvalid",
    "GuideInvalid",
    "GuideNotFound",
    "GuidePromptsRefusal",
    "KeyImmutable",
    "KeyInvalid",
    "KeyTaken",
    "OrderInvalid",
    "REFUSAL_STATUS",
    "STATUS_CONFLICT",
    "STATUS_NOT_FOUND",
    "STATUS_UNPROCESSABLE",
]
