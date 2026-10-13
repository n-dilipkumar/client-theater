"""WF-112: the published vocabulary for a Slack notification from a buyer signal.

Every constant here is either quoted from the research the issue cites, or is an
explicitly *derived* choice of this build. The two are kept apart by a ``sourced``
flag rather than left to a reader's judgement, and the derived half is also
recorded in :mod:`dsr.signal_slack.inferences` with what would change it.

Why this package owns its own vocabulary
----------------------------------------

This feature is a **consumer** of buyer intent signals. WF-027 owns the
``intent_signal`` collection and the urgency set, and this package reads those
records **by their published collection name** rather than importing
``dsr.signals``. Two reasons, and the second is the binding one:

* A domain package depends on nothing but :mod:`dsr.store` and the standard
  library. That is the rule ``docs/FEATURE-CONTRACT.md`` states, the rule
  ``backend/tests/test_features.py`` enforces for ``dsr.api``, and the rule
  ``backend/tests/test_wf024.py`` pins line by line for a domain module.
  Importing ``dsr.signals`` from here would couple two workflow-owned packages.
* The reuse the brief asks for happens at the **data boundary**: a signal is a
  row in ``intent_signal``, and this feature reads it. The XML-style tags, the
  rate limits and the refusal codes are this workflow's own, because no other
  workflow has them.

:data:`SIGNAL_COLLECTION` is therefore a naming contract with WF-027 rather than
an import. A reader who wants to check it is one constant away.

The sourced half
----------------

The research quotes seven things this build does not get to choose: the bot
scope, the channel at connect time, the mention syntax, the private-channel
invite, the no-conversion rule, and the two rate limits. Each is a module
constant with its sentence beside it.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections this workflow owns, and the one it reads
# --------------------------------------------------------------------------- #

#: One row per connected Slack workspace. The research requires a channel at
#: connect time, so a connection without one is not a connection.
CONNECTION_COLLECTION = "wf112_slack_connection"

#: One row per Slack block in an automation: the message, the destination
#: channel default, and the trigger that fires it.
AUTOMATION_COLLECTION = "wf112_slack_automation"

#: One row per notification decision. This is the product's output: what WOULD be
#: posted, to which channel, and the refusal code when a rule stops it. ``live``
#: is a signal-driven post and ``test`` is the Send-test modal.
DISPATCH_COLLECTION = "wf112_slack_dispatch"

#: WF-027's collection, read by name. A buyer signal this workflow notifies on is
#: a row here. Read, never imported: see the module docstring.
SIGNAL_COLLECTION = "intent_signal"

# --------------------------------------------------------------------------- #
# The Slack connection: bot scope and a channel, both required
# --------------------------------------------------------------------------- #

#: "When you configure Slack in Customer.io, you need to provide Slack credentials
#: with the 'bot' scope, letting Customer.io post to your Slack workspace."
BOT_SCOPE_QUOTE = (
    "When you configure Slack in Customer.io, you need to provide Slack credentials with the "
    "'bot' scope, letting Customer.io post to your Slack workspace."
)

#: The scope a credential must carry. It is not optional, so this is a hard rule
#: and not a default: :func:`~dsr.signal_slack.rules.require_bot_scope` refuses a
#: credential without it.
REQUIRED_SCOPE = "bot"

#: The scopes this build recognises. ``bot`` is the only required one; the rest are
#: the read scopes a bot needs to resolve a channel and a member, and they are
#: accepted rather than refused so a real OAuth grant is storable.
KNOWN_SCOPES: tuple[str, ...] = (
    "bot",
    "chat:write",
    "channels:read",
    "groups:read",
    "users:read",
)

#: The two kinds of channel the research names. A private channel needs the bot
#: invited before a post can land.
CHANNEL_KINDS: tuple[str, ...] = ("public", "private")

#: "select a workspace (and a channel, required at connect time)".
CHANNEL_REQUIRED_QUOTE = "select a workspace (and a channel, required at connect time)"

#: "If you want to post messages to a private channel, you must invite your bot to
#: a channel with the ``/invite @Customer.io`` command."
PRIVATE_CHANNEL_QUOTE = (
    "If you want to post messages to a private channel, you must invite your bot to a channel "
    "with the /invite @Customer.io command."
)

#: The states one connection has. ``revoked`` is a soft delete: the dispatches that
#: named it still name it.
CONNECTION_STATES: tuple[str, ...] = ("connected", "revoked")

#: The state a connection must be in for a notification to be recorded as
#: deliverable.
STATE_CONNECTED = "connected"
STATE_REVOKED = "revoked"

#: Longest a channel name may be, after normalisation. Slack caps a channel name at
#: 80 characters; this build refuses a longer one rather than storing a string no
#: channel could have.
MAX_CHANNEL_LENGTH = 80

# --------------------------------------------------------------------------- #
# The automation and its Slack block
# --------------------------------------------------------------------------- #

#: The two trigger paths the research names. The in-product path is a buyer
#: signal; the webhook-triggered path is the unusual one the spec calls out, and it
#: is recorded as a kind rather than implemented, because it depends on WF-114.
TRIGGER_KINDS: tuple[str, ...] = ("signal", "webhook")

#: "you can trigger a slack message. This provides a way to notify a group of
#: people via Slack when this workflow is triggered."
WEBHOOK_TRIGGER_QUOTE = (
    "you can trigger a slack message. This provides a way to notify a group of people via "
    "Slack when this workflow is triggered."
)

#: The two states one automation has, matching the builder's draft/live split.
AUTOMATION_STATES: tuple[str, ...] = ("draft", "live")
STATE_DRAFT = "draft"
STATE_LIVE = "live"

#: The default block kind. One block type is in scope: a Slack message.
BLOCK_KIND = "slack_message"

#: The longest message this build stores. Slack's own limit for a text block is
#: 3,000 characters for ``text`` and no hard limit for a block's ``text`` field;
#: 4,000 is this build's ceiling and it is a derived number, recorded in
#: :data:`~dsr.signal_slack.inferences.DERIVED_MESSAGE_MAX`.
MESSAGE_MAX_LENGTH = 4000

# --------------------------------------------------------------------------- #
# Liquid tags, mentions and links
# --------------------------------------------------------------------------- #

#: The two Liquid tags the research names, and what each renders from. Kept as a
#: mapping from path to the customer field so a picker and the renderer cannot
#: disagree.
TAG_PATHS: dict[str, str] = {
    "customer.username": "The buyer's name, as the person profile spells it.",
    "customer.channel": "The channel the buyer came in on, from the person profile.",
}

#: What an unset tag renders as, per path. The research does not say, so each is a
#: documented fallback rather than literal braces. Recorded as the
#: ``unset-tag-fallback`` inference.
TAG_FALLBACK: dict[str, str] = {
    "customer.username": "the buyer",
    "customer.channel": "the sales room",
}

#: What a tag naming a path this build does not publish renders as. A typo must not
#: leave ``{{...}}`` in a message a human reads, so it degrades to one word rather
#: than to the braces it was written with.
MISSING_TAG_FALLBACK = "unknown"

#: "To mention specific users in your slack message, follow this format: ``Hey
#: <@U012AB3CD>, thanks for submitting your report.``"
MENTION_QUOTE = (
    "To mention specific users in your slack message, follow this format: "
    "Hey <@U012AB3CD>, thanks for submitting your report."
)

#: The user-id prefixes Slack issues. A mention whose id does not carry one is
#: refused rather than stored as text no client will render as a mention.
MENTION_ID_PREFIXES: tuple[str, ...] = ("U", "W")

#: The longest a Slack user id may be. Slack ids are short; a longer one is a
#: string a human typed and not an id.
MAX_MENTION_ID_LENGTH = 32

#: The Slack formatting surface the research links, with the malformed trailing
#: backtick the spec carries removed. The spec's own Sources block writes
#: ``<https://api.slack.com/reference/surfaces/formatting`>`` with the backtick
#: inside the angle brackets; the fetchable URL is the one below, and the trailing
#: mark is recorded in :data:`SOURCE_MALFORMED_ENTRIES`.
SLACK_FORMATTING_URL = "https://api.slack.com/reference/surfaces/formatting"

#: The two malformed entries the spec's Sources block carries, kept as data so the
#: design document can say it did not cite them as fetchable sources.
SOURCE_MALFORMED_ENTRIES: tuple[str, ...] = (
    "https://api.slack.com/reference/surfaces/formatting`",
    "<https://\u2026|label>",
)

# --------------------------------------------------------------------------- #
# Rate limits: two named limits, an order of magnitude apart
# --------------------------------------------------------------------------- #

#: "App API 10 requests per second"
APP_API_QUOTE = "App API 10 requests per second"

#: "API-triggered broadcasts 1 request every 10 seconds"
BROADCAST_QUOTE = "API-triggered broadcasts 1 request every 10 seconds"

#: The two limits the research states, kept named. A live signal-driven post is a
#: broadcast and takes the slower limit; a Send-test is a direct app-API call and
#: takes the faster one. The numbers are quoted, not derived.
RATE_LIMITS: dict[str, dict[str, Any]] = {
    "app_api": {
        "requests": 10,
        "per_seconds": 1,
        "quote": APP_API_QUOTE,
        "sourced": True,
    },
    "broadcast": {
        "requests": 1,
        "per_seconds": 10,
        "quote": BROADCAST_QUOTE,
        "sourced": True,
    },
}

#: Which limit each dispatch kind is judged against. This is the derived half of
#: the two sourced numbers, and it is the decision that a live post is a broadcast.
LIMIT_FOR_KIND: dict[str, str] = {"live": "broadcast", "test": "app_api"}

# --------------------------------------------------------------------------- #
# Dispatch states and refusals
# --------------------------------------------------------------------------- #

#: The two dispatch kinds. ``live`` is fired by a buyer signal; ``test`` is the
#: Send-test modal, "pick a test channel/user, run it, and confirm the message
#: lands".
DISPATCH_KINDS: tuple[str, ...] = ("live", "test")

#: The two states a recorded decision has. ``recorded`` means every rule passed and
#: the message WOULD be posted; ``refused`` means a rule stopped it and
#: :data:`REFUSAL_CODES` names which. There is deliberately no ``sent`` state: this
#: product has no Slack transport, and a state called sent would be a claim
#: nothing in the codebase can support.
DISPATCH_STATES: tuple[str, ...] = ("recorded", "refused")
STATE_RECORDED = "recorded"
STATE_REFUSED = "refused"

#: The refusal codes the issue lists, plus the structural ones the record needs.
#: Every `refused` dispatch carries one of these in ``refusal``.
REFUSAL_CODES: tuple[str, ...] = (
    "missing_bot_scope",
    "channel_required",
    "bot_not_in_channel",
    "no_connection",
    "connection_revoked",
    "automation_not_live",
    "rate_limited",
)

#: The three refusals the issue names by hand, kept apart so a test can assert the
#: set the issue asks for is present and this build did not quietly drop one.
RESEARCHED_REFUSALS: tuple[str, ...] = (
    "missing_bot_scope",
    "channel_required",
    "bot_not_in_channel",
)

#: Why delivery is a stand-in. The record says what WOULD be posted; nothing leaves
#: the process.
DELIVERY = "not_sent"
DELIVERY_NOTE = (
    "This product has no Slack transport. The decision is recorded with the message that would "
    "be posted and the channel it would be posted to; nothing is sent."
)

#: "Slack and *Create or update person* actions are often internal or used for
#: analytics purposes; they don't always send messages to end-users. For that
#: reason, we don't attach conversions to them."
NO_CONVERSION_QUOTE = (
    "Slack and Create or update person actions are often internal or used for analytics "
    "purposes; they don't always send messages to end-users. For that reason, we don't attach "
    "conversions to them."
)

#: "Customer.io's Slack Action helps your teams work better together by passing
#: information directly into Slack triggered by user behavior in your app."
WHY_QUOTE = (
    "Customer.io's Slack Action helps your teams work better together by passing information "
    "directly into Slack triggered by user behavior in your app."
)


def rate_limit(kind: str) -> dict[str, Any]:
    """The named limit a dispatch kind is judged against.

    Unknown kinds fall back to the slower broadcast limit, because the failure
    this build must not make is posting faster than the slower sourced number
    allows.
    """
    return dict(RATE_LIMITS[LIMIT_FOR_KIND.get(str(kind), "broadcast")])


def describe() -> dict[str, Any]:
    """The whole published vocabulary, for clients and for the page's pickers."""
    return {
        "collections": {
            "connection": CONNECTION_COLLECTION,
            "automation": AUTOMATION_COLLECTION,
            "dispatch": DISPATCH_COLLECTION,
            "signal_read": SIGNAL_COLLECTION,
        },
        "required_scope": REQUIRED_SCOPE,
        "known_scopes": list(KNOWN_SCOPES),
        "channel_kinds": list(CHANNEL_KINDS),
        "connection_states": list(CONNECTION_STATES),
        "max_channel_length": MAX_CHANNEL_LENGTH,
        "trigger_kinds": list(TRIGGER_KINDS),
        "automation_states": list(AUTOMATION_STATES),
        "block_kind": BLOCK_KIND,
        "message_max_length": MESSAGE_MAX_LENGTH,
        "tag_paths": [{"path": path, "from": source} for path, source in TAG_PATHS.items()],
        "tag_fallback": dict(TAG_FALLBACK),
        "missing_tag_fallback": MISSING_TAG_FALLBACK,
        "mention_id_prefixes": list(MENTION_ID_PREFIXES),
        "dispatch_kinds": list(DISPATCH_KINDS),
        "dispatch_states": list(DISPATCH_STATES),
        "refusal_codes": list(REFUSAL_CODES),
        "researched_refusals": list(RESEARCHED_REFUSALS),
        "rate_limits": {
            name: {
                "requests": entry["requests"],
                "per_seconds": entry["per_seconds"],
                "quote": entry["quote"],
                "sourced": entry["sourced"],
            }
            for name, entry in RATE_LIMITS.items()
        },
        "limit_for_kind": dict(LIMIT_FOR_KIND),
        "delivery": DELIVERY,
        "delivery_note": DELIVERY_NOTE,
        "attribution_excluded": True,
        "no_conversion_quote": NO_CONVERSION_QUOTE,
        "why_quote": WHY_QUOTE,
        "formatting_url": SLACK_FORMATTING_URL,
        "sends_slack": False,
    }


__all__ = [
    "APP_API_QUOTE",
    "AUTOMATION_COLLECTION",
    "AUTOMATION_STATES",
    "BLOCK_KIND",
    "BOT_SCOPE_QUOTE",
    "BROADCAST_QUOTE",
    "CHANNEL_KINDS",
    "CHANNEL_REQUIRED_QUOTE",
    "CONNECTION_COLLECTION",
    "CONNECTION_STATES",
    "DELIVERY",
    "DELIVERY_NOTE",
    "DISPATCH_COLLECTION",
    "DISPATCH_KINDS",
    "DISPATCH_STATES",
    "KNOWN_SCOPES",
    "LIMIT_FOR_KIND",
    "MAX_CHANNEL_LENGTH",
    "MAX_MENTION_ID_LENGTH",
    "MENTION_ID_PREFIXES",
    "MENTION_QUOTE",
    "MESSAGE_MAX_LENGTH",
    "MISSING_TAG_FALLBACK",
    "NO_CONVERSION_QUOTE",
    "PRIVATE_CHANNEL_QUOTE",
    "RATE_LIMITS",
    "REFUSAL_CODES",
    "REQUIRED_SCOPE",
    "RESEARCHED_REFUSALS",
    "SIGNAL_COLLECTION",
    "SLACK_FORMATTING_URL",
    "SOURCE_MALFORMED_ENTRIES",
    "STATE_CONNECTED",
    "STATE_DRAFT",
    "STATE_LIVE",
    "STATE_RECORDED",
    "STATE_REFUSED",
    "STATE_REVOKED",
    "TAG_FALLBACK",
    "TAG_PATHS",
    "TRIGGER_KINDS",
    "WEBHOOK_TRIGGER_QUOTE",
    "WHY_QUOTE",
    "describe",
    "rate_limit",
]
