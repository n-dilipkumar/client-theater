"""Domain errors for WF-054: distribute bookings across a team by round robin.

One error type is registered with the host and mapped once by the feature module.
Starlette resolves a handler by walking the raised class's MRO, so the three
subclasses below are covered by the same registration and stay distinguishable
by their ``status``.

The statuses draw the line the rest of this product draws:

* **400** - the request cannot be honoured as written. A link type this workflow
  does not route, a distribution with no eligible member, a slot that was never
  offered.
* **404** - nothing to act on. The team, distribution, route or booking does not
  exist.
* **409** - the request is well formed and conflicts with state that already
  exists. A route that has already been booked, a distribution whose team has no
  licensed member left.

``RecordNotFound`` is deliberately *not* claimed. The core app already maps it to
404, and two handlers for one type is a collision the feature host refuses.
"""

from __future__ import annotations


class RoundRobinError(ValueError):
    """A round robin evaluation cannot be honoured as written.

    The status rides on the exception rather than being chosen per raise site, so
    "this is a conflict, not a typo" is decided once where the distinction is
    drawn rather than at twenty call sites.
    """

    status = 400
    code = "round_robin_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class RoundRobinNotFound(RoundRobinError):
    """The named team, distribution, route or booking does not exist."""

    status = 404
    code = "round_robin_not_found"


class RoundRobinConflict(RoundRobinError):
    """The request is well formed but conflicts with current state.

    A routing session that has already been booked, a no-show already recorded
    against a booking, a distribution whose cursor has moved past the member the
    caller named.
    """

    status = 409
    code = "round_robin_conflict"


class NoEligibleMember(RoundRobinError):
    """Every member of the distribution's team is excluded from assignment.

    The researched licensing rule is a hard gate rather than a warning:
    "if any prospects match to an unlicensed user, they will not be able to book
    a meeting and route to the Not Scheduled path". A team of only unlicensed
    members has no one to route to, which is the Not Scheduled path with nothing
    left on it. It is a conflict rather than a bad request because the
    distribution is legal to declare; it is the team that cannot answer.
    """

    status = 409
    code = "no_eligible_member"
