"""WF-112: notify the internal team in Slack from a buyer signal.

A buyer signal fires. The Slack block renders its message against the buyer's
person profile, resolves the destination channel, and the product records what
WOULD be posted - the message, the channel, the workspace - or refuses it with one
of the named codes. Nothing leaves the process: this product has no Slack transport,
so the record is the output and the page says so.

The module layout, and why each piece is separate:

``vocabulary``
    Every published name, number, tag and refusal, with the research quote beside
    each sourced one and the derivation recorded for each derived one.
``errors``
    One hierarchy, so the feature module registers a single handler, with the three
    refusals the issue names carried as their own types.
``liquid``
    The three message surfaces: Liquid tags, Slack mentions and Slack links. Pure
    string rules, no store and no framework.
``rules``
    The pure rules. A connection, a block, a signal, a customer, a moment and the
    previous dispatch moments go in; one decision comes out. No store, no clock.
``inferences``
    Fifteen judgement calls, each with what would change it and the risk of having
    got it wrong, served at ``GET /api/wf-112/inferences``.
``engine``
    The only module that writes, through the audited store.

The package imports nothing but :mod:`dsr.store` and the standard library. WF-027's
buyer signals are read from its ``intent_signal`` collection by its published name,
not by importing :mod:`dsr.signals`; that decision carries a Jev audit id and is
recorded in :mod:`dsr.signal_slack.inferences`.

What this build deliberately does not do
----------------------------------------

**It posts nothing.** The research describes the vendor's Slack Action; this product
has no Slack transport. A dispatch records the message that would be posted and the
channel it would go to, and the vocabulary, the page and the record all say so.

**It attributes no conversion.** "we don't attach conversions to them", so a
dispatch carries ``attribution_excluded`` and no conversion field, and a test
asserts no conversion key exists.

**It does not build the webhook entry point.** The spec's unusual webhook-triggered
path depends on WF-114; this build stores ``webhook`` as a trigger kind and records
the dependency rather than faking the entry point.
"""

from __future__ import annotations

from dsr.signal_slack.engine import SlackNotifier, present
from dsr.signal_slack.errors import (
    BotNotInChannel,
    ChannelRequired,
    DuplicateAutomation,
    DuplicateConnection,
    InvalidAutomation,
    InvalidConnection,
    InvalidDispatch,
    MissingBotScope,
    SignalSlackError,
    UnknownAutomation,
    UnknownConnection,
    UnknownDispatch,
    UnknownSignal,
)
from dsr.signal_slack.inferences import INFERENCES, SOURCED, describe as describe_inferences
from dsr.signal_slack.liquid import (
    is_link,
    is_mention_id,
    render_link,
    render_mention,
    render_message,
    render_tags,
    require_syntax,
    scan_syntax,
)
from dsr.signal_slack.rules import (
    REFUSAL_PRECEDENCE,
    check_rate_limit,
    decide,
    normalise_automation,
    normalise_channel,
    normalise_connection,
    normalise_dispatch_kind,
    normalise_workspace,
    require_bot_scope,
    resolve_channel,
    signal_summary,
)
from dsr.signal_slack.vocabulary import (
    APP_API_QUOTE,
    AUTOMATION_COLLECTION,
    AUTOMATION_STATES,
    BLOCK_KIND,
    BOT_SCOPE_QUOTE,
    BROADCAST_QUOTE,
    CHANNEL_KINDS,
    CHANNEL_REQUIRED_QUOTE,
    CONNECTION_COLLECTION,
    CONNECTION_STATES,
    DELIVERY,
    DELIVERY_NOTE,
    DISPATCH_COLLECTION,
    DISPATCH_KINDS,
    DISPATCH_STATES,
    KNOWN_SCOPES,
    LIMIT_FOR_KIND,
    MAX_CHANNEL_LENGTH,
    MAX_MENTION_ID_LENGTH,
    MENTION_ID_PREFIXES,
    MENTION_QUOTE,
    MESSAGE_MAX_LENGTH,
    MISSING_TAG_FALLBACK,
    NO_CONVERSION_QUOTE,
    PRIVATE_CHANNEL_QUOTE,
    RATE_LIMITS,
    REFUSAL_CODES,
    REQUIRED_SCOPE,
    RESEARCHED_REFUSALS,
    SIGNAL_COLLECTION,
    SLACK_FORMATTING_URL,
    SOURCE_MALFORMED_ENTRIES,
    STATE_CONNECTED,
    STATE_DRAFT,
    STATE_LIVE,
    STATE_RECORDED,
    STATE_REFUSED,
    STATE_REVOKED,
    TAG_FALLBACK,
    TAG_PATHS,
    TRIGGER_KINDS,
    WEBHOOK_TRIGGER_QUOTE,
    WHY_QUOTE,
    describe,
    rate_limit,
)

__all__ = [
    "APP_API_QUOTE",
    "AUTOMATION_COLLECTION",
    "AUTOMATION_STATES",
    "BLOCK_KIND",
    "BOT_SCOPE_QUOTE",
    "BROADCAST_QUOTE",
    "BotNotInChannel",
    "CHANNEL_KINDS",
    "CHANNEL_REQUIRED_QUOTE",
    "CONNECTION_COLLECTION",
    "CONNECTION_STATES",
    "ChannelRequired",
    "DELIVERY",
    "DELIVERY_NOTE",
    "DISPATCH_COLLECTION",
    "DISPATCH_KINDS",
    "DISPATCH_STATES",
    "DuplicateAutomation",
    "DuplicateConnection",
    "INFERENCES",
    "InvalidAutomation",
    "InvalidConnection",
    "InvalidDispatch",
    "KNOWN_SCOPES",
    "LIMIT_FOR_KIND",
    "MAX_CHANNEL_LENGTH",
    "MAX_MENTION_ID_LENGTH",
    "MENTION_ID_PREFIXES",
    "MENTION_QUOTE",
    "MESSAGE_MAX_LENGTH",
    "MISSING_TAG_FALLBACK",
    "MissingBotScope",
    "NO_CONVERSION_QUOTE",
    "PRIVATE_CHANNEL_QUOTE",
    "RATE_LIMITS",
    "REFUSAL_CODES",
    "REFUSAL_PRECEDENCE",
    "REQUIRED_SCOPE",
    "RESEARCHED_REFUSALS",
    "SIGNAL_COLLECTION",
    "SLACK_FORMATTING_URL",
    "SOURCE_MALFORMED_ENTRIES",
    "SOURCED",
    "STATE_CONNECTED",
    "STATE_DRAFT",
    "STATE_LIVE",
    "STATE_RECORDED",
    "STATE_REFUSED",
    "STATE_REVOKED",
    "SignalSlackError",
    "SlackNotifier",
    "TAG_FALLBACK",
    "TAG_PATHS",
    "TRIGGER_KINDS",
    "UnknownAutomation",
    "UnknownConnection",
    "UnknownDispatch",
    "UnknownSignal",
    "WEBHOOK_TRIGGER_QUOTE",
    "WHY_QUOTE",
    "check_rate_limit",
    "decide",
    "describe",
    "describe_inferences",
    "is_link",
    "is_mention_id",
    "normalise_automation",
    "normalise_channel",
    "normalise_connection",
    "normalise_dispatch_kind",
    "normalise_workspace",
    "present",
    "rate_limit",
    "render_link",
    "render_mention",
    "render_message",
    "render_tags",
    "require_bot_scope",
    "require_syntax",
    "resolve_channel",
    "scan_syntax",
    "signal_summary",
]
