"""Domain errors for WF-063 meeting reassignment.

Two types, mapped once each by the feature module, because the research
distinguishes the two situations and the HTTP status has to say which one
happened:

* :class:`ReassignError` - the *request* cannot be honoured as written. The
  caller sent something the researched rules forbid: a change to the Meeting
  Type or the Workspace ("You cannot change the Meeting Type or Workspace"), a
  target outside the Distribution settings of the meeting booked, a host who is
  not free, or ``reassign/auto`` on a booking that is not round robin
  ("Currently only supports reassigning host for round robin bookings").
  400 - the request is wrong and the caller fixes it.

* :class:`MeetingStateError` - the *meeting* is in a state that cannot be
  reassigned at all, such as a cancelled meeting or one already handed to the
  host being asked for. Nothing about the request would have been wrong. 409 -
  the request was well formed and conflicts with current state, which is the
  same split the core app makes for its own ``AuditError``.

:class:`MeetingStateError` subclasses :class:`ReassignError` so a caller that
catches the base still catches both, but the feature module registers a handler
for each, and the subclass is listed first so FastAPI resolves the more specific
type to the 409.
"""

from __future__ import annotations


class ReassignError(ValueError):
    """A reassignment request cannot be honoured as written. Answers 400."""


class MeetingStateError(ReassignError):
    """The meeting's own state forbids the reassignment. Answers 409.

    Kept distinct from a plain :class:`ReassignError` because "you asked for a
    host who is not free" and "this meeting was cancelled an hour ago" are
    different problems for the person looking at the failure, and collapsing
    them would tell a rep to change the host when the real answer is that the
    meeting is over.
    """
