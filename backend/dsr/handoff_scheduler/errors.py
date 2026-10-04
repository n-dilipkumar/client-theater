"""Domain errors for WF-055: hand a lead off from an SDR scheduler to an AE.

One error type is registered with the host and mapped once by the feature module.
Starlette resolves a handler by walking the raised class's MRO, so the three
subclasses below are covered by the same registration and stay distinguishable
by their ``status``.

The statuses draw the line the rest of this product draws:

* **400** - the request cannot be honoured as written. A request that names
  neither a guest email nor a CRM record id, a router whose path has no AE, a
  start time the routing never offered.
* **404** - nothing to act on. The workspace, router, routing or meeting does not
  exist.
* **409** - the request is well formed and conflicts with state that already
  exists. A routing that has already been booked, an AE who took another meeting
  between the routing opening and the booking landing.

``RecordNotFound`` is deliberately *not* claimed. The core app already maps it to
404, and two handlers for one type is a collision the feature host refuses.
"""

from __future__ import annotations


class HandoffError(ValueError):
    """A handoff cannot be honoured as written.

    The status rides on the exception rather than being chosen per raise site, so
    "this is a conflict, not a typo" is decided once where the distinction is
    drawn rather than at twenty call sites.
    """

    status = 400
    code = "handoff_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class HandoffNotFound(HandoffError):
    """The named workspace, router, routing or meeting does not exist."""

    status = 404
    code = "handoff_not_found"


class HandoffConflict(HandoffError):
    """The request is well formed but conflicts with current state.

    A routing that has already been booked, a router that does not belong to the
    routing's workspace, a start time the routing offered but which the AE has
    since taken.
    """

    status = 409
    code = "handoff_conflict"
