"""WF-110: escalate a converted conversation into an owned ticket.

Automations and engagement. The research is
``docs/research/digital-sales-room-workflows/wf/WF-110.md``, quoted in full in
issue 197.

The researched workflow: a conversation is converted to a ticket (or a ticket is
created via API or inbound); the ticket carries a type, a state, and, when
converted, a linked conversation; the ``ticket is created`` trigger fires; the
actions run - notify the owning team in Slack, change the assigned team, apply an
SLA, tag the linked conversation; and the ticket's state transitions emit
``ticket.state.updated`` / ``ticket.resolved`` / ``ticket.closed`` webhooks. A
second, optional trigger fires when a teammate changes the ticket's state.

Five modules carry the workflow, and none of them knows about HTTP:

``vocabulary``
    Every researched term this workflow enforces against: the teams, the ticket
    types and states, how a ticket came to exist, the two trigger kinds, the four
    actions, the two documented limits and the tracker exclusion, the webhook
    topics and their two payload shapes, and every refusal code. Every researched
    constant carries the sentence it came from.
``errors``
    The one error type the host registers, and the subclasses that draw the line
    between "you typed it wrong" and "the state refuses it".
``rules``
    The pure rules: the small validators, the conversion predicate, trigger
    matching, the converted-conversation gate, the two payload shapes, and one
    reader that handles both.
``engine``
    The reads and the writes, over the audited store: tickets, triggers, tags, SLA
    policies, notifications, webhooks, evaluations and the activity log.
``inferences``
    The judgement calls the specification left open, each recorded with the
    alternative rejected, the cost of the rejection, and the change to make.

Nothing here imports ``dsr.api``, and nothing here opens SQLite. Every read and
write goes through the :class:`~dsr.store.RecordStore` the HTTP layer hands in, so
the audit row is written in the same transaction as the change.

Conversation vocabulary is reused, not re-declared
--------------------------------------------------

WF-107 (``dsr.conversation_chase``) owns conversation records, and this workflow
converts one of those records into a ticket. This package reads the conversation
collection name and the conversation states from WF-107, so the two features
cannot disagree about what a converted conversation is.

What this workflow does not claim
---------------------------------

**That a Slack message left this product.** The research names the Slack app in
Intercom as the delivery path. WF-112 owns Slack delivery and may not be present,
so every notification is written as a queued row with
``delivered_by_this_product: False``.

**That it owns SLA timers.** WF-108 owns SLA timers and may not be present, so an
SLA policy here stores a target in hours and a computed due instant. Nothing in
this feature depends on WF-108.

**That the tracker ticket is refused.** "the workflow will not trigger from
tracker ticket creation" excludes the trigger, not the ticket. A tracker ticket is
created and stored; the evaluation records why no trigger fired.
"""

from __future__ import annotations

from dsr.conversation_tickets import (  # noqa: F401
    engine,
    errors,
    inferences,
    rules,
    vocabulary,
)
from dsr.conversation_tickets.engine import TicketEngine  # noqa: F401
from dsr.conversation_tickets.errors import (  # noqa: F401
    ERROR_TYPES,
    ConversationNotFound,
    TagNotInWorkspace,
    TeamUnknown,
    TicketConflict,
    TicketError,
    TicketNotFound,
    TicketRefused,
)

__all__ = [
    "ERROR_TYPES",
    "ConversationNotFound",
    "TagNotInWorkspace",
    "TeamUnknown",
    "TicketConflict",
    "TicketEngine",
    "TicketError",
    "TicketNotFound",
    "TicketRefused",
    "engine",
    "errors",
    "inferences",
    "rules",
    "vocabulary",
]
