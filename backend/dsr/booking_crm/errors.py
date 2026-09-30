"""One error hierarchy for the booking writeback package (WF-065).

Every refusal this package makes is a caller's to fix, so they share a base class
and the HTTP layer registers one handler per *distinct HTTP answer* rather than
one per message. Anything that is not a :class:`WritebackError` is a bug and must
propagate.

The split is driven by what the caller can do about it:

``WritebackError``
    The flow cannot be honoured as declared. ``400``. Almost always the sourced
    ordering rule: a CRM node that must follow ``create_or_update_record`` and
    does not.
``NodeOrderError``
    A subclass carrying the quoted sentence, so the message a rep reads is the
    documented rule rather than a paraphrase of it.
``InvalidNode``
    A node this build does not know, or one named in a way the vendor's palette
    does not use. ``400``.
``InvalidConfig``
    A node declared with a setting this build cannot honour - an update mode and
    a create mode that contradict, a field map with no target, a CampaignMember
    status that is not the one the research fixes. ``400``.
``NotConfigured``
    Well formed, but this installation cannot answer it yet: no meeting type
    with the toggle on, or no flow for the path the booking took. ``428``, so a
    client can say "finish the setup" rather than "you got the request wrong".
``NotFound``
    A room, flow, meeting type, run or event id that does not resolve, or does
    not resolve *for that room*. ``404``.

``RecordNotFound`` and ``AuditError`` are deliberately not claimed here. The core
app already maps them, and two handlers for one type is a collision the feature
host refuses, which would take this whole feature offline.
"""

from __future__ import annotations

from typing import Any


class WritebackError(ValueError):
    """The flow cannot be honoured as declared."""


class NodeOrderError(WritebackError):
    """A CRM node is declared before the record node it depends on.

    Raised only by the ordering check, and it always quotes
    :data:`~dsr.booking_crm.vocabulary.ORDERING_QUOTE`, because the research
    states the rule as a single sentence and a rep who breaks it needs the
    sentence, not a paraphrase.
    """


class InvalidNode(WritebackError):
    """A node this build does not know, or a node of the wrong vendor."""


class InvalidConfig(WritebackError):
    """A node declared with a setting this build cannot honour."""


class NotConfigured(WritebackError):
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


__all__ = [
    "InvalidConfig",
    "InvalidNode",
    "NodeOrderError",
    "NotConfigured",
    "NotFound",
    "WritebackError",
]
