"""One error hierarchy for the panel-time package (WF-057).

Every refusal this package makes is a caller's to fix, so they share a base class
and the HTTP layer registers one handler per *distinct HTTP answer* rather than
one per message. Anything that is not a :class:`PanelTimeError` is a bug and
must propagate.

The split is driven by what the caller can do about it:

``PanelTimeError``
    The search cannot be run as written. ``400``.
``CalendarShapeError``
    A calendar row is not the shape a free/busy read needs - no email, a group
    with members that are not addresses, a busy block that is not an interval.
    Structural: the record is not the thing the research describes.
``PanelShapeError``
    A panel declares no organizer, no attendees, or a time constraint with no
    time slots. The same structural class as above, for the other record type,
    kept separate so a message names which record is wrong.
``ConstraintError``
    A parameter is outside its documented range: a ``meetingDuration`` that is
    not a positive ISO 8601 duration, a ``minAttendeePercentage`` above 100, a
    time slot that ends before it starts.
``LimitExceeded``
    The search is larger than a documented capacity - more than 50 calendars
    after group expansion, or a ``groupExpansionMax`` above 100. Refusing before
    the read is the point: the vendor would reject the request anyway, and a
    refusal that names the researched cap is more use than a 400 from Google.
``SlotUnavailable``
    The slot asked for cannot be booked: it is not one the search returned, it
    has since been taken, or it is already booked. Distinct from a ``400``
    because the request is well formed and the *state* is what refuses.
``PanelTimeNotConfigured``
    Well formed, but this installation cannot answer it yet - no calendars
    registered at all. ``428``, so a client can say "finish the setup" rather
    than "you got the request wrong".
``NotFound``
    A room, panel, search, booking or calendar id does not resolve, or does not
    resolve *for that room*. ``404``. One type carrying the resource name rather
    than five near-identical ones, because the only thing that differs is a word
    and the word belongs in the message.

The two error types the *core* app already maps - ``RecordNotFound`` and
``AuditError`` - are deliberately not claimed here. Two handlers for one type is
a collision the feature host refuses, and the core mappings are already correct.
"""

from __future__ import annotations

from typing import Any


class PanelTimeError(ValueError):
    """The search cannot be run as written."""


class CalendarShapeError(PanelTimeError):
    """A calendar record is not the shape a free/busy read needs."""


class PanelShapeError(PanelTimeError):
    """A panel record is not the shape the researched flow needs."""


class ConstraintError(PanelTimeError):
    """A parameter is outside its documented range."""


class LimitExceeded(PanelTimeError):
    """The search is larger than a documented capacity."""


class SlotUnavailable(PanelTimeError):
    """The slot asked for cannot be booked in the state it is in now."""


class PanelTimeNotConfigured(PanelTimeError):
    """This installation is not set up to answer the request yet."""


class NotFound(LookupError):
    """A record id does not resolve - here, or under the room asked for.

    Carries the resource name and the id as attributes rather than only in the
    message, so a handler can put them in the body instead of parsing prose.
    """

    def __init__(self, resource: str, record_id: Any, room_id: str | None = None) -> None:
        self.resource = resource
        self.record_id = str(record_id)
        self.room_id = str(room_id) if room_id is not None else None
        where = f" in room {self.room_id}" if self.room_id else ""
        super().__init__(f"{resource} {self.record_id} not found{where}")
