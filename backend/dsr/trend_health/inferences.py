"""Every judgement call WF-021 rests on, in one inspectable place.

The research for this workflow is unusually honest about its own edges, and the
distinction is worth keeping rather than blurring:

* It states the four buckets and the three windows as a quotation from Dock's own
  help article. Those are **sourced**, and they are the product.
* It states the automation as "recomputes continuously from activity; no user
  action" and names the decay order. **Sourced**, and it is why nothing is stored
  per workspace.
* It names the engagement events and the ``workspace.*`` webhook seam, and the
  camelCase ``occurredAt`` those webhooks carry. **Sourced.**
* It says "**tons**" and "**a decent amount of**" and then gives no number for
  either. It says nothing about internal views, about clock skew, about where a
  window's edge falls, or about what a "workspace" is called in this product.

So the sourced half is quoted in the modules that implement it, and everything
else is here. Each entry is:

* **named**, so it can be argued with by name rather than found in a diff;
* **traceable** - ``basis`` quotes what the research does and does not say;
* **bounded** - ``value`` is what this build chose and ``change_it`` says how to
  change it without editing a function body;
* **visible** - :func:`describe` is served at ``/api/wf-021/inferences``, so a
  reviewer or a client reads the list instead of inferring it from the code.

This is a list of ordinary JSON. It is not a migration, not a typed column, and
not a mechanism that enforces anything: a disagreement with an entry is settled by
reading the research, and then either the entry or the code moves.
"""

from __future__ import annotations

from typing import Any

#: The one quotation the whole workflow rests on, kept here so the entries below
#: can point at it rather than paraphrase it four times.
SOURCED_QUOTE = (
    "These are based on an algorithm of the engagement of a workspace. Hot = workspaces that "
    "have tons of recent engagement within the last 7 days. Warm = workspaces that have a decent "
    "amount of engagement within the last 14 days. Cooling = workspaces that previously had "
    "engagement, but none within the last 14 days. Cold = workspace with no engagement within the "
    "last month."
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "volume-floor",
        "topic": 'what "tons" and "a decent amount of" come to in numbers',
        "basis": (
            "Sourced: the four rules, and the words that qualify the first two of them. Not "
            "sourced: any number. The research quotes the buckets from a help article, and no "
            "source in the set states a threshold, a count, or a percentile - the Liferay Room "
            "Trend widget it also cites names three states and no arithmetic at all. So the "
            'distinction between "tons" and "a decent amount" is real and sourced, and the '
            "numbers implementing it are not."
        ),
        "value": {"min_events": {"hot": 5, "warm": 2}},
        "why": (
            "A volume floor modelled as a separate branch would leave the ladder with a gap: a "
            "workspace with one view in the fortnight matches no bucket, and the research's four "
            "states are a partition. So the floor is a qualifier on counting instead - activity "
            "below the floor does not count as engagement in that window, and the ladder falls "
            "through to the recency test. That keeps the ladder total, keeps the two quantified "
            "sentences doing the work that distinguishes Hot from Warm, and makes a single "
            "stray view read as Cooling rather than as a healthy deal. The cost is stated plainly: "
            "a workspace under the floor reports a bucket whose sourced sentence is not literally "
            "true of it, which is why the response carries the counts in every window next to the "
            "value, and why reasons[] says which floor suppressed what."
        ),
        "change_it": 'PATCH /api/wf-021/rules with {"min_events": {"hot": N, "warm": M}}.',
        "effect_on_default": "5 events in 7 days reads Hot; 2 in 14 reads Warm; fewer falls through.",
    },
    {
        "id": "window-boundary",
        "topic": "which side of a window edge an event falls on",
        "basis": (
            "Sourced: the windows themselves (7, 14, 30 days) and the decay claim. Not sourced: "
            'what happens to an event stamped exactly on an edge. "Within the last 7 days" is '
            "ordinary English, not a specification, and the two readings differ for exactly one "
            "instant in every window."
        ),
        "value": {"test": "0 <= (now - occurred_at) < window", "at_the_edge_counts": False},
        "why": (
            "Exclusive at the lower edge, because the alternative makes the researched decay "
            "claim awkward to answer. If an event exactly 7 days old still counted, then at the "
            "instant the 7-day window edge reaches it nothing about the value would change - it "
            'would change one second later. A rep asking "when does this room go Warm?" would '
            "be told a time at which the product still says Hot, which is the kind of answer that "
            "makes a metric get ignored. With the edge excluded, the decay instants are exactly "
            "the window lengths, which is also what the research describes."
        ),
        "change_it": "There is no knob; it is a property of in_window() and is asserted in the tests.",
        "effect_on_default": "an event 6d23h old counts in the 7-day window; one stamped 7d00m does not.",
    },
    {
        "id": "external-engagement-only",
        "topic": "whether a rep opening their own room counts as engagement",
        "basis": (
            'Sourced: the metric is described as giving "a quick pulse on workspace health and '
            'external engagement", and the researched event list is workspace activity. Not '
            "sourced: an explicit rule about internal sessions. The same research corpus "
            "separates Dock's Internal view from the external one, so the distinction exists in "
            "the product being modelled."
        ),
        "value": {"count_only_external": True, "default_audience": "external"},
        "why": (
            "The trend column drives two decisions the research names - re-engage Cold rooms, "
            "protect Hot ones - and both are about what the buyer is doing. A rep previewing "
            "their own room before a call is a real event that says nothing about buyer intent, "
            "and counting it would let a room look Hot on the seller's own activity alone. "
            "Internal events are still recorded, so the flag is auditable and a reader can see "
            "the room was opened - they are just not what the bucket is built from."
        ),
        "change_it": 'PATCH /api/wf-021/rules with {"count_only_external": false}.',
        "effect_on_default": "an internal view is stored and counted in events, not in qualifying.",
    },
    {
        "id": "client-view-definition",
        "topic": 'which event the dashboard\'s "Last Client View" column reports',
        "basis": (
            'Sourced: the user flow sorts and filters on "Trend plus Last Client View" as two '
            "different things, and the data sources list four distinct event types. Not sourced: "
            'which of them "Last Client View" is.'
        ),
        "value": {"client_view_event": "workspace.viewed"},
        "why": (
            "Read as a buyer opening the workspace, not as any interaction inside it. The column "
            "sits next to Trend in the research's own flow, and a column that changed on a "
            "link click would duplicate half of what the bucket already reports. Reading a page "
            "inside the room is engagement; it is not the room being opened."
        ),
        "change_it": "Vocabulary constant CLIENT_VIEW_EVENT; the stored client_view flag makes the rest queryable.",
        "effect_on_default": "page, file, link and order-form events move the bucket but not the column.",
    },
    {
        "id": "clock-skew-tolerance",
        "topic": "how far ahead of the server clock an occurredAt may be stamped",
        "basis": (
            "Sourced: the webhook seam and the ``occurredAt`` field on it. Not sourced: any "
            "tolerance, or any statement about clock agreement."
        ),
        "value": {"tolerance_seconds": 300, "clamped": False},
        "why": (
            "Webhook delivery is not instantaneous and the sender's clock is not ours, so a small "
            "positive skew is routine and refusing it would drop real events. A large one is a "
            "fault, and an event dated next month falls outside every window this workflow reads, "
            "so a broken sender could push a live deal to Cooling with nothing visible to explain "
            "it. Stored skewed events are refused rather than clamped: clamping writes a time the "
            "sender never claimed, and the audit row would then describe something that did not "
            "happen."
        ),
        "change_it": "Constant CLOCK_SKEW_TOLERANCE_SECONDS; the refusal names the skew it saw.",
        "effect_on_default": "up to 5 minutes ahead is stored as sent; beyond that is a 400.",
    },
    {
        "id": "workspace-equals-room",
        "topic": "what a Dock workspace is called here, and what happens to an unknown one",
        "basis": (
            "Sourced: the metric is per workspace, and the extensibility note names "
            "``workspace.*`` webhooks. Not sourced: a mapping onto this product's envelope, which "
            "has exactly one scoping field, ``room_id``."
        ),
        "value": {
            "maps_to": "room_id",
            "accepts": ["room_id", "workspaceId", "workspace_id"],
            "unknown_room": "404",
        },
        "why": (
            "Dock's workspace and this product's room are the same thing - the same deal, the same "
            "shared surface - so the researched webhook maps onto ``room_id`` and nothing else. "
            "An event for a room that does not exist is refused rather than stored: a webhook "
            "forwarder sending events for workspaces this product has not been provisioned for is "
            "a sender that wants to know, and an answer of 404 is the one that tells it. Silently "
            "storing it would accumulate an event history for a deal the dashboard will never show."
        ),
        "change_it": "Room resolution is require_room() in health.py; both spellings are read on the way in.",
        "effect_on_default": "the room must exist; the response echoes the room id that was used.",
    },
    {
        "id": "nothing-stored-per-workspace",
        "topic": "why no workspace holds a cached trend value",
        "basis": (
            'Sourced: "The classification recomputes continuously from activity; no user action", '
            'and "Buckets are time-window based, so a workspace decays from Hot -> Warm -> Cooling '
            '-> Cold without any new activity". Not sourced: any refresh cadence, or any statement '
            "about how long a stored value stays good - the research describes a property of the "
            "value, not a job that maintains it."
        ),
        "value": {"stored_per_workspace": False, "as_of_parameter": True},
        "why": (
            "A stored bucket needs something to age it, and a job that stops running leaves rooms "
            "reading Hot forever with no signal that anything is wrong. A value derived on read "
            "from the newest event and the clock cannot go stale, and it makes the researched decay "
            "claim true by construction rather than by maintenance. The cost is that an "
            "``as_of`` parameter is needed to ask about the past, which is why it exists on every "
            'read - "what was this room last Tuesday" is a question a pipeline triage view asks.'
        ),
        "change_it": "No knob. classify() is pure in (events, rules, instant).",
        "effect_on_default": "GET .../dashboard?as_of=2026-09-01T00:00:00Z answers the same question for the past.",
    },
    {
        "id": "unknown-events-refused",
        "topic": "what the webhook seam does with an event it does not model",
        "basis": (
            "Sourced: the five event shapes the research lists. Not sourced: a policy for a "
            "sender that emits something else."
        ),
        "value": {
            "unknown_event_type": "400",
            "accepted": "the researched list plus workspace.order_form.*",
        },
        "why": (
            "Refused, with the accepted list in the error. A silently ignored event is "
            "indistinguishable from an event that never happened, and the consequence lands on a "
            "sales rep rather than on the sender: a room reads Cooling because the integration was "
            "emitting a name this workflow does not know. A 400 naming the accepted shapes lets "
            "the sender fix it in one edit."
        ),
        "change_it": "ENGAGEMENT_EVENT_TYPES and ORDER_FORM_PREFIX in vocabulary.py; require_event_type() refuses.",
        "effect_on_default": "the error message lists every accepted shape.",
    },
)


def describe() -> dict[str, Any]:
    """The whole registry, served verbatim at ``/api/wf-021/inferences``."""
    return {
        "sourced_quote": SOURCED_QUOTE,
        "count": len(INFERENCES),
        "inferences": [dict(entry) for entry in INFERENCES],
        "ids": [str(entry["id"]) for entry in INFERENCES],
    }


def by_id(inference_id: str) -> dict[str, Any] | None:
    return next((entry for entry in INFERENCES if entry["id"] == inference_id), None)
