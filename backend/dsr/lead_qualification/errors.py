"""The refusals this workflow makes, each carrying its own status and code.

One base class for the whole hierarchy so the feature module can register a
single handler and still answer a malformed request differently from a conflict
with state that already exists. The two are different problems for the caller:
one is "you sent the wrong thing" and the other is "that thing already exists".

:mod:`dsr.db.audited.RecordNotFound` is deliberately **not** claimed here. The
core app already maps it to 404, and a second handler for one type is a
collision the feature host refuses.
"""

from __future__ import annotations


class QualificationError(RuntimeError):
    """Base of every refusal in :mod:`dsr.lead_qualification`.

    ``status`` and ``code`` ride on the exception rather than being decided at the
    route, so the same refusal answers the same way wherever it is raised -
    including from the seeder and from a domain test.
    """

    status: int = 400
    code: str = "qualification_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.detail = message

    def as_response(self) -> dict[str, object]:
        """The body a client sees. Kept here so no route can answer differently."""
        return {"error": self.code, "detail": self.detail, "status": self.status}


class IntervalSupplied(QualificationError):
    """The body carried an ``interval``, which makes it the other workflow.

    "The difference is whether you pass an ``interval``." A body with one would
    ask for a slot list, a second call and a consumed session, none of which this
    workflow does, so it is refused rather than quietly half-honoured.
    """

    status = 400
    code = "interval_supplied"


class PayloadRefused(QualificationError):
    """The request has no form payload to qualify.

    The research's access-pattern table says the request is "``form`` data (no
    ``interval``)", so an absent form is a malformed request rather than a lead
    that qualifies as nothing.
    """

    status = 400
    code = "form_payload_required"


class RouterNotFound(QualificationError):
    """No router answers to that slug. The slug is the researched path segment."""

    status = 404
    code = "router_not_found"


class RouterRefused(QualificationError):
    """The router declaration cannot be saved as it stands.

    Raised for a missing slug, a duplicate, a rule the validator cannot read, and
    a chain with no catch-all. Every one of them would otherwise leave a router
    that cannot route an inbound lead.
    """

    status = 422
    code = "router_refused"


class RouterAlreadyExists(QualificationError):
    """That slug is taken. 409, because the slug is the researched path segment."""

    status = 409
    code = "router_already_exists"


class RouterDisabled(QualificationError):
    """The router exists but is not published.

    "The router is published and deployed" before it serves a lead, so a disabled
    router answers rather than qualifying.
    """

    status = 409
    code = "router_disabled"


class AssigneeNotFound(QualificationError):
    """No assignee row answers to that user id."""

    status = 404
    code = "assignee_not_found"


class AssigneeRefused(QualificationError):
    """An assignee row needs a user id and a name before it can be declared."""

    status = 422
    code = "assignee_refused"


class VerdictNotFound(QualificationError):
    """No recorded verdict has that id."""

    status = 404
    code = "verdict_not_found"


class RoomRequired(QualificationError):
    """The room-scoped write path needs a room that exists.

    404 rather than 400: the request named a room, and the room is not there.
    """

    status = 404
    code = "room_not_found"
