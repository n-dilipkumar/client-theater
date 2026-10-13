"""Every judgement call this workflow rests on, named and served as data.

The research fixes seven things by name - the bot scope, the required channel, the
mention syntax, the private-channel invite, the no-conversion rule and the two rate
limits - and leaves the rest of the workflow's behaviour to a reader. Each edge this
build had to decide is collected here rather than left as a comment in a function
body, so a reviewer can see where the sourced half stops and the judgement starts.

Served at ``GET /api/wf-112/inferences``, beside the sourced half, because the point
is to see the line.
"""

from __future__ import annotations

from typing import Any

#: One entry per decision. ``id`` is stable so a test can name it, ``change`` says
#: how to reverse the call, and ``risk`` says what happens if the call is wrong.
INFERENCES: tuple[dict[str, str], ...] = (
    {
        "id": "consume-signals-by-collection",
        "question": "How does this feature reuse WF-027's buyer-signal vocabulary?",
        "reading": (
            "By reading WF-027's published intent_signal collection by name, not by importing "
            "dsr.signals. A domain package depends on nothing but the store."
        ),
        "why": (
            "Importing dsr.signals would couple two workflow-owned packages and break the "
            "store-only rule that test_wf024 pins. The signal a workflow notifies on is a row, "
            "and a row is reached through the store."
        ),
        "change": "Add dsr.signals to the package imports and the store-only test stops passing.",
        "risk": "If WF-027 renames its collection, this feature reads nothing and refuses every live signal.",
    },
    {
        "id": "delivery-is-a-stand-in",
        "question": "Does anything leave the process when a notification is recorded?",
        "reading": (
            "No. The decision is recorded with the message that would be posted and the channel "
            "it would be posted to; nothing is sent."
        ),
        "why": (
            "The research describes the vendor's Slack Action and this product has no Slack "
            "transport. Recording what would be sent is the honest output; a record that claimed "
            "a send would be a claim nothing can support."
        ),
        "change": "Add a Slack transport and a state that can prove a send happened.",
        "risk": "A reader who assumes recorded means sent would over-report internal notifications.",
    },
    {
        "id": "unset-tag-fallback",
        "question": "What does an unset Liquid tag render as?",
        "reading": (
            "A documented word per path - 'the buyer' for {{customer.username}}, 'the sales room' "
            "for {{customer.channel}} - and 'unknown' for a path this build does not publish."
        ),
        "why": (
            "The research does not say. Literal braces in a message a human reads look broken, so "
            "the fallback is a word and the record lists which tags fell back."
        ),
        "change": "Change TAG_FALLBACK and MISSING_TAG_FALLBACK in vocabulary.py.",
        "risk": "A fallback that reads naturally could hide a profile field that was never set.",
    },
    {
        "id": "mention-id-prefix",
        "question": "What makes a mention well formed?",
        "reading": (
            "A U or W prefix followed by alphanumerics, which is the shape of the id in the "
            "research's own example <@U012AB3CD>."
        ),
        "why": (
            "The research quotes the syntax and one id. A mention whose id is not that shape is "
            "text no client renders as a mention, so it is refused at save time."
        ),
        "change": "Widen MENTION_ID_RE in liquid.py.",
        "risk": "A real Slack id with a shape this build did not anticipate is refused.",
    },
    {
        "id": "broadcast-limit-for-live-posts",
        "question": "Which of the two sourced rate limits does a live signal-driven post take?",
        "reading": (
            "A live post is a broadcast and takes 1 request per 10 seconds; a Send-test is a "
            "direct app-API call and takes 10 requests per second."
        ),
        "why": (
            "The research names both limits and says the broadcast path is the slower one. A "
            "signal that fires for a group of people is the broadcast case."
        ),
        "change": "Change LIMIT_FOR_KIND in vocabulary.py.",
        "risk": "Judging a live post against the faster limit would post ten times faster than the "
        "slower sourced number allows.",
    },
    {
        "id": "test-send-uses-app-api-limit",
        "question": "Does the Send-test path take a rate limit at all?",
        "reading": "Yes, the app-API limit: 10 requests per second.",
        "why": (
            "A test send is a single direct call and the research gives the app API a limit; "
            "leaving it unlimited would make the two documented limits decorative."
        ),
        "change": "Remove 'test' from LIMIT_FOR_KIND or raise its numbers.",
        "risk": "None material; a test send is low volume by nature.",
    },
    {
        "id": "channel-per-message-over-block-default",
        "question": "When a dispatch names a channel and the block also names one, which wins?",
        "reading": "The per-message channel wins, then the block's default, then the connection's.",
        "why": (
            "The user flow says 'Choose the destination channel per message', so the most "
            "specific setting is the one a seller expects to win."
        ),
        "change": "Reorder the candidates in rules.resolve_channel.",
        "risk": "A seller who set a block default and then an override would see the override win.",
    },
    {
        "id": "private-channel-refusal-is-a-value",
        "question": "Is a bot that is not in a private channel an error or a recorded refusal?",
        "reading": (
            "A recorded refusal: state 'refused', refusal 'bot_not_in_channel', with the invite "
            "command in the reason."
        ),
        "why": (
            "The issue asks for the failure message a seller sees. A bare 4xx shows a status; a "
            "recorded refusal shows the fix, and it is what the page lists."
        ),
        "change": "Raise BotNotInChannel from rules.decide instead of returning a refusal.",
        "risk": "A caller that wanted a hard error has to read the refusal code off the response.",
    },
    {
        "id": "rate-limit-refusal-is-a-value",
        "question": "Is a rate-limited post an error or a recorded refusal?",
        "reading": "A recorded refusal carrying retry_after_seconds.",
        "why": (
            "A refusal a page can show is more useful than a 429 a caller has to translate, and "
            "the record then answers why a busy signal did not notify."
        ),
        "change": "Raise RateLimited from rules.decide instead of returning a refusal.",
        "risk": "A caller that only checks the status code sees 201 and must read the state.",
    },
    {
        "id": "no-sent-state",
        "question": "Is there a dispatch state called sent?",
        "reading": "No. The two states are 'recorded' and 'refused'.",
        "why": (
            "This product has no Slack transport, so a state called sent would be a claim nothing "
            "in the codebase can support."
        ),
        "change": "Add a transport and a state it can prove.",
        "risk": "None; the absence is the point.",
    },
    {
        "id": "attribution-excluded-is-recorded",
        "question": "How is the no-conversion rule expressed?",
        "reading": (
            "The dispatch carries attribution_excluded: true and no conversion field, and the "
            "vocabulary serves the quote."
        ),
        "why": (
            "The research states internal actions 'don't attach conversions to them', so the "
            "record states the exclusion rather than silently omitting a field."
        ),
        "change": "Remove the field and the test that asserts no conversion key exists.",
        "risk": "A downstream reader could attach a conversion to a record that never said not to.",
    },
    {
        "id": "webhook-trigger-is-recorded-not-implemented",
        "question": "Is the webhook-triggered path built?",
        "reading": (
            "It is a stored trigger kind, 'webhook', and the flow is not implemented because it "
            "depends on WF-114, the external webhook entry the spec names."
        ),
        "why": (
            "The spec says the webhook path is the unusual one that 'provides a way to notify a "
            "group of people'. Recording the kind keeps the vocabulary complete without faking "
            "the entry point."
        ),
        "change": "Build the entry point when WF-114 lands; the kind is already stored.",
        "risk": "A seller can select a trigger that currently has no firing path.",
    },
    {
        "id": "message-length-ceiling",
        "question": "What is the longest message this build stores?",
        "reading": "4,000 characters.",
        "why": (
            "The research gives no ceiling. Slack's text block is generous, and 4,000 is this "
            "build's own bound, chosen to be past any internal notification and short of a payload "
            "no client should carry."
        ),
        "change": "Change MESSAGE_MAX_LENGTH in vocabulary.py.",
        "risk": "A very long notification is refused rather than truncated.",
    },
    {
        "id": "refusal-precedence",
        "question": "When more than one rule would refuse, which is reported?",
        "reading": (
            "In a fixed order: no connection, then revoked, then not live, then no channel, then "
            "the bot not in a private channel, then the rate limit."
        ),
        "why": (
            "One refusal a seller can fix is more useful than a list, and the order is the one "
            "where fixing the first clears the ones behind it."
        ),
        "change": "Reorder REFUSAL_PRECEDENCE in rules.py.",
        "risk": "A seller fixes one problem and is shown a second they could not see.",
    },
    {
        "id": "malformed-source-entries-not-cited",
        "question": "What about the two malformed entries in the spec's Sources block?",
        "reading": (
            "The trailing backtick on the Slack formatting URL is stripped to the fetchable URL; "
            "the <https://\u2026|label> entry is treated as a template, not a source."
        ),
        "why": (
            "One is a transcription artefact and the other is the mention/link template the issue "
            "reuses. Citing either as a fetchable source would be citing something that does not "
            "resolve."
        ),
        "change": "Nothing to change; both are recorded in vocabulary.SOURCE_MALFORMED_ENTRIES.",
        "risk": "Low; the rest of the source list is unaffected.",
    },
)

#: The sourced statements, kept beside the inferences so the endpoint shows the line.
SOURCED: tuple[dict[str, str], ...] = (
    {
        "id": "bot-scope",
        "statement": "Slack credentials with the 'bot' scope are required to post.",
        "source": "docs/customer.io/messaging/channels/slack/get-started.md",
    },
    {
        "id": "channel-at-connect",
        "statement": "A workspace and a channel are chosen at connect time.",
        "source": "WF-112 user flow, step 1",
    },
    {
        "id": "mention-syntax",
        "statement": "A user mention is <@U012AB3CD>.",
        "source": "https://api.slack.com/reference/surfaces/formatting",
    },
    {
        "id": "private-channel-invite",
        "statement": "A bot must be invited to a private channel with /invite.",
        "source": "docs/customer.io/messaging/channels/slack/get-started.md",
    },
    {
        "id": "no-conversion",
        "statement": "Internal Slack actions do not carry a conversion.",
        "source": "docs/customer.io/messaging/send/automations/data-workflows/webhook-triggered-automations.md",
    },
    {
        "id": "app-api-limit",
        "statement": "The App API is 10 requests per second.",
        "source": "docs/customer.io/integrations/api/customerio-apis.md",
    },
    {
        "id": "broadcast-limit",
        "statement": "API-triggered broadcasts are 1 request every 10 seconds.",
        "source": "docs/customer.io/integrations/api/customerio-apis.md",
    },
)


def describe() -> dict[str, Any]:
    """Every inference and every sourced statement, served as data."""
    return {
        "count": len(INFERENCES),
        "sourced_count": len(SOURCED),
        "sourced": [dict(entry) for entry in SOURCED],
        "inferences": [dict(entry) for entry in INFERENCES],
    }


__all__ = ["INFERENCES", "SOURCED", "describe"]
