"""WF-133: real-time buyer-intent alerting and routing.

The researched workflow, in full. A target account's stakeholder returns to the
room and reads the pricing section for ninety seconds. The platform raises a
signal. The account owner is told, with which pages, how long and which
stakeholder. A follow-up task lands on the opportunity. The rep acts while the
interest is live.

What is in here
---------------

:mod:`dsr.intent_routing.thresholds`
    Threshold evaluation. Pure. Five inputs, one of which has a sourced number and
    four of which have a recorded derivation.

:mod:`dsr.intent_routing.resolution`
    Company to opportunity. Reads the company WF-031 identified and the records
    WF-042 imported, and joins them on website host or company name.

:mod:`dsr.intent_routing.routing`
    Who hears, and who has already been told. Four rule kinds and a twenty-four
    hour suppression window.

:mod:`dsr.intent_routing.payload`
    The three researched facts, and the email that carries them.

:mod:`dsr.intent_routing.engine`
    The only module that writes. Everything it writes goes through the store, and
    every write takes ``source`` as a required keyword so the audit row names the
    route that served it.

:mod:`dsr.intent_routing.inferences`
    Fourteen decisions the research left open, each with the reading taken, the
    reason and what would change it. Served at ``GET /api/wf-133/inferences``.

The package imports nothing but the store and the standard library. It has no
framework in it, which is what lets the rules be tested by reading them.

What this build deliberately does not do
-----------------------------------------

It calls no vendor. The research states that reverse-IP company resolution "is the
one piece with no clean OSS equivalent", and this build's answer is to refuse an
account it cannot resolve rather than to buy a guess. It sends no mail and no
Slack message: both dispatches are recorded, one as queued and one as held, because
the product has no outbound transport and a state called sent would be a claim
nothing could support.

Both refusals are recorded in :mod:`dsr.intent_routing.inferences` rather than left
as omissions, and the alert itself carries the reason, so neither is invisible to a
rep reading it.
"""

from __future__ import annotations

from dsr.intent_routing.engine import IntentRouter
from dsr.intent_routing.errors import (
    DuplicateRule,
    DuplicateWatchlist,
    IntentRoutingError,
    InvalidAction,
    InvalidEngagement,
    InvalidRule,
    InvalidWatchlist,
    UnknownAlert,
    UnknownCompany,
    UnknownEngagementField,
    UnknownRule,
    UnknownSignal,
    UnknownTask,
    UnknownWatchlist,
    UnresolvedAccount,
)
from dsr.intent_routing.inferences import INFERENCES
from dsr.intent_routing.payload import body_for, payload_for, subject_for
from dsr.intent_routing.resolution import Account, host_of, normalise_name, resolve
from dsr.intent_routing.routing import Recipient, recipients_for, suppressed_until
from dsr.intent_routing.thresholds import Crossing, Engagement, evaluate, parse_engagement
from dsr.intent_routing.vocabulary import (
    ACTIONS,
    ALERTS,
    COLLECTIONS,
    NOTIFICATION_CHANNELS,
    RULES,
    SIGNALS,
    SUPPRESSION_HOURS,
    TASKS,
    THRESHOLDS,
    THRESHOLDS_TO_ALERT,
    WATCHLISTS,
    WINDOW_HOURS,
)

__all__ = [
    "ACTIONS",
    "ALERTS",
    "Account",
    "COLLECTIONS",
    "Crossing",
    "DuplicateRule",
    "DuplicateWatchlist",
    "Engagement",
    "INFERENCES",
    "IntentRouter",
    "IntentRoutingError",
    "InvalidAction",
    "InvalidEngagement",
    "InvalidRule",
    "InvalidWatchlist",
    "NOTIFICATION_CHANNELS",
    "Recipient",
    "RULES",
    "SIGNALS",
    "SUPPRESSION_HOURS",
    "TASKS",
    "THRESHOLDS",
    "THRESHOLDS_TO_ALERT",
    "UnknownAlert",
    "UnknownCompany",
    "UnknownEngagementField",
    "UnknownRule",
    "UnknownSignal",
    "UnknownTask",
    "UnknownWatchlist",
    "UnresolvedAccount",
    "InvalidWatchlist",
    "WATCHLISTS",
    "WINDOW_HOURS",
    "body_for",
    "evaluate",
    "host_of",
    "normalise_name",
    "parse_engagement",
    "payload_for",
    "recipients_for",
    "resolve",
    "subject_for",
    "suppressed_until",
]
