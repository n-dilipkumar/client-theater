"""Every published name, number and collection this workflow uses.

Nothing here reads or writes. The point of the module is that a client renders its
pickers from these values rather than from a list compiled into a page, so a
threshold changed here reaches every client at once. ``GET /api/wf-106/vocabulary``
serves this module as data.

Two kinds of value live here, and the difference between them is load bearing:

**Sourced.** The research quotes these. The three frequency modes of *Show workflow
until*, the session damper sentence, and the two trigger signals. They carry
``sourced: True`` and the quote they came from.

**Derived.** The research names a signal and states no number for it. The ticket
says "repeatedly browses a high-intent page" and the evidence says the trigger fires
on "time on page or visited URL", so a repeat count, a repeat window and a dwell
threshold are all needed and none is given. They carry ``sourced: False``, the
derivation in ``derivation``, and ``risk``. A reviewer who disagrees changes one
number in this file rather than hunting through an engine.

The vendor threshold was not sourced. Nothing in the four cited sources states how
many visits a buyer has to make, or how long they have to spend on one page. The
numbers here are this build's reading, and they are labelled as such everywhere
they are served.
"""

from __future__ import annotations

#: The four collections this workflow owns. Each name is prefixed so a filter over
#: the whole store can find this feature's rows without reading a record.
COLLECTIONS: dict[str, str] = {
    "workflows": "page_outreach_workflow",
    "views": "page_outreach_view",
    "deliveries": "page_outreach_delivery",
    "receipts": "page_outreach_receipt",
}

#: The three values the research gives for *Show workflow until*.
#:
#: ``seen`` is the default and the quote says a buyer is sent the workflow once and
#: never again "whether or not they interact with or dismiss it", so it is a count
#: of shows rather than a count of interactions. ``any_interaction`` stops on the
#: first interaction of any kind, including a dismissal, which the quote treats as
#: an interaction. ``engaged_with`` is the only one that reads the content of the
#: interaction: the quote says the buyer "engage[s] with it by selecting a Workflow
#: path", so a Messenger open is not enough and only a path selection stops it.
FREQUENCY_MODES: tuple[dict[str, object], ...] = (
    {
        "mode": "seen",
        "label": "Seen",
        "default": True,
        "stops_on": "shown",
        "sourced": True,
        "quote": (
            "Send the Workflow to customers once, then not again, whether or not they "
            "interact with or dismiss it."
        ),
        "reads": "One show is enough. A second matching visit never shows it again.",
    },
    {
        "mode": "any_interaction",
        "label": "Any interaction happens",
        "default": False,
        "stops_on": "interaction",
        "sourced": True,
        "quote": "Show workflow until: Any interaction happens.",
        "reads": (
            "A dismissal counts as an interaction, so it stops the workflow as surely as a "
            "path selection does."
        ),
    },
    {
        "mode": "engaged_with",
        "label": "Engaged with",
        "default": False,
        "stops_on": "path_selected",
        "sourced": True,
        "quote": (
            "Send the Workflow to customers consistently until they engage with it by "
            "selecting a Workflow path."
        ),
        "reads": (
            "Only a path selection stops it. A Messenger open or a dismissal hides it for the "
            "session and lets the next session show it again."
        ),
    },
)

#: The interactions this workflow records. The research names the receipt, goal,
#: open and click events on the ``content_stat`` webhook, and the data flow adds
#: path selection and dismissal as the two ways a buyer answers or leaves.
INTERACTIONS: tuple[dict[str, object], ...] = (
    {
        "kind": "path_selected",
        "label": "Path selected",
        "engagement": True,
        "content_stat": "receipt",
        "sourced": True,
        "reads": "The buyer chose a branch. This is the only engagement that stops Engaged with.",
    },
    {
        "kind": "messenger_opened",
        "label": "Messenger opened",
        "engagement": False,
        "content_stat": "open",
        "sourced": True,
        "reads": "Hides the workflow for the rest of the session, but is not an engagement.",
    },
    {
        "kind": "clicked",
        "label": "Clicked",
        "engagement": False,
        "content_stat": "click",
        "sourced": True,
        "reads": "A link or app inside the block was used. Recorded, not treated as a path.",
    },
    {
        "kind": "dismissed",
        "label": "Dismissed",
        "engagement": False,
        "content_stat": "receipt",
        "sourced": True,
        "reads": "The buyer closed the block. Hides it for the session.",
    },
    {
        "kind": "goal_reached",
        "label": "Goal reached",
        "engagement": True,
        "content_stat": "goal",
        "sourced": True,
        "reads": "The goal named on the Goal pane fired. Recorded against the workflow.",
    },
)

#: The kinds of interaction that hide a block for the remainder of the session.
#:
#: The quote is: "If they dismiss the Workflow or open the Messenger, it will be
#: hidden for the remainder of their session. When they start a new session, the
#: Workflow will be shown again, until they engage with it." Two kinds hide the
#: block, and the quote names exactly those two.
SESSION_HIDING_INTERACTIONS: tuple[str, ...] = ("dismissed", "messenger_opened")

#: The one interaction that counts as engaging for the ``engaged_with`` mode.
ENGAGEMENT_INTERACTIONS: tuple[str, ...] = ("path_selected", "goal_reached")

#: The two trigger signals the evidence names: "time on page or visited URL".
TRIGGER_SIGNALS: tuple[str, ...] = ("visited_url", "time_on_page")

#: How a page view is matched against a workflow's URL rules. ``exact`` is the whole
#: path. ``prefix`` is a path prefix with a slash boundary, so ``/pricing`` matches
#: ``/pricing/plans`` and not ``/pricing-archive``. ``contains`` is a substring, and
#: it is the mode the research implies for a seller who targets on a word in the
#: URL rather than on a path shape.
URL_MATCH_MODES: tuple[str, ...] = ("exact", "prefix", "contains")

#: The four rule kinds a targeting rule may carry. A rule matches when its kind and
#: its value both hold, so a seller can require a URL *and* a dwell time.
TARGET_RULE_KINDS: tuple[str, ...] = ("url", "dwell", "utm_source", "utm_campaign")

#: The panes the flow names. The research says "Configure the trigger's When to
#: send, Where to send, Audience, Scheduling, Goal panes" and specifies the contents
#: of none of them. All five are modelled. See ``inferences.py`` for what each one
#: holds here and what that costs.
TRIGGER_PANES: tuple[dict[str, object], ...] = (
    {
        "pane": "when_to_send",
        "label": "When to send",
        "fields": ("frequency", "repeat_window_days"),
        "sourced": False,
        "reading": (
            "Holds the frequency mode and the repeat window. Both decide whether a "
            "matching visit shows the block."
        ),
    },
    {
        "pane": "where_to_send",
        "label": "Where to send",
        "fields": ("channel",),
        "sourced": False,
        "reading": (
            "Holds one channel, in_app. The research names the Messenger as the surface "
            "and the API equivalent as POST /messages with message_type in_app."
        ),
    },
    {
        "pane": "audience",
        "label": "Audience",
        "fields": ("company_keys", "tags", "segments"),
        "sourced": False,
        "reading": (
            "Holds company keys, tags and segments. The research names Segments and "
            "Contact plus Company attributes as data sources and states no rule."
        ),
    },
    {
        "pane": "scheduling",
        "label": "Scheduling",
        "fields": ("state", "starts_at", "ends_at"),
        "sourced": False,
        "reading": (
            "Holds draft or live, and an optional window. The flow's last step is to set "
            "the workflow live, so state belongs here."
        ),
    },
    {
        "pane": "goal",
        "label": "Goal",
        "fields": ("goal_name",),
        "sourced": False,
        "reading": (
            "Holds one goal name. The research lists a Goal pane and a content_stat goal "
            "topic but never says what a goal is for this trigger."
        ),
    },
)

#: The only channel this build can honour.
#:
#: The research names ``POST /messages`` with ``message_type: in_app`` as the API
#: equivalent of the in-app block, and this product has no outbound mail transport.
#: A channel this build cannot honour is refused at save time rather than stored and
#: silently never delivered.
CHANNELS: tuple[str, ...] = ("in_app",)

#: The two states a workflow is in. The flow ends with "set it live", and a workflow
#: that is not live is a draft a seller has not published.
WORKFLOW_STATES: tuple[str, ...] = ("draft", "live")

#: The block kinds the flow names: a welcome message carrying video and apps, a
#: question that branches, and a closing block.
BLOCK_KINDS: tuple[str, ...] = ("message", "question", "app", "close")

#: The app kinds a block may carry. The flow names a video and a booking app, and
#: the extensibility note adds a Data Connector action and a Wait for Webhook action.
APP_KINDS: tuple[str, ...] = ("video", "booking", "article", "connector", "webhook")

#: The states a delivery is in. ``hidden_for_session`` is the researched damper: the
#: buyer dismissed it or opened the Messenger, so it stays out of the way until the
#: next session.
DELIVERY_STATES: tuple[str, ...] = ("shown", "interacted", "engaged", "hidden_for_session")

#: How many matching visits a buyer has to make before the block is shown, and how
#: long those visits have to be spread over.
#:
#: The ticket title says "repeatedly" and the spec states no repeat count and no
#: repeat window. Two visits is a coincidence and five is a campaign. Three inside
#: seven days is the reading taken: enough visits that the pattern is intent rather
#: than one page opened twice, and a window long enough to span a weekend and a
#: return trip to the buyer's desk.
REPEAT_VISITS: int = 3

#: The repeat window in days. See ``REPEAT_VISITS`` for the derivation.
REPEAT_WINDOW_DAYS: int = 7

#: How long a buyer has to sit on a matching page for the time-on-page signal to
#: count.
#:
#: The evidence says the trigger fires on "time on page or visited URL" and states
#: no threshold. Sixty seconds is the reading taken. It is above the time anyone
#: spends on a page they are not reading, and below the ninety seconds WF-133 uses
#: for the same behaviour, so the two workflows do not disagree about the same
#: visitor on the same page.
DWELL_SECONDS: int = 60

#: The thresholds, as one table, with the derivation beside each number.
THRESHOLDS: tuple[dict[str, object], ...] = (
    {
        "kind": "repeat_visits",
        "label": "Matching visits before the block is shown",
        "unit": "visits",
        "threshold": REPEAT_VISITS,
        "sourced": False,
        "derivation": (
            "The ticket title says repeatedly. The spec states no repeat count. Three is "
            "the reading taken: two visits is a page opened twice and five is a campaign."
        ),
        "risk": "A buyer who genuinely returns three times still sees nothing if they do it faster.",
    },
    {
        "kind": "repeat_window",
        "label": "Window the visits have to fall inside",
        "unit": "days",
        "threshold": REPEAT_WINDOW_DAYS,
        "sourced": False,
        "derivation": (
            "Long enough to span a weekend and a return trip to the buyer's desk. A "
            "shorter window splits one buying cycle into two."
        ),
        "risk": "A slower cycle re-qualifies a buyer and the seller sees the block twice.",
    },
    {
        "kind": "dwell",
        "label": "Time on one matching page",
        "unit": "seconds",
        "threshold": DWELL_SECONDS,
        "sourced": False,
        "derivation": (
            "Above the time anyone spends on a page they are not reading, and below the "
            "ninety seconds WF-133 uses for the same behaviour."
        ),
        "risk": "A buyer who reads fast is treated the same as one who skimmed.",
    },
)

#: The two rules the target parser reads that are not the repeat threshold. A view
#: matches when every one of the workflow's rules holds, so the workflow's rule list
#: is an AND and each rule's own list is an OR.
THRESHOLDS_TO_SHOW: int = 1

#: One record of the vendor research that this build cannot act on, published so a
#: reader of the page does not have to hold it in their head.
UNSOURCED_LIMITS: tuple[dict[str, str], ...] = (
    {
        "limit": "repeat_count",
        "detail": (
            "None of the four cited sources states how many page views a buyer has to "
            "make. REPEAT_VISITS is this build's derivation."
        ),
    },
    {
        "limit": "repeat_window",
        "detail": (
            "No cited source states a window. REPEAT_WINDOW_DAYS is this build's derivation."
        ),
    },
    {
        "limit": "dwell_threshold",
        "detail": (
            "The evidence names time on page as a signal and gives no number. "
            "DWELL_SECONDS is this build's derivation."
        ),
    },
    {
        "limit": "trigger_pane_contents",
        "detail": (
            "The flow names five panes and specifies the contents of none. What each pane "
            "holds here is recorded in TRIGGER_PANES and in inferences.py."
        ),
    },
    {
        "limit": "ingest_contract",
        "detail": (
            "The page view is collected by an external JavaScript snippet. This workflow "
            "owns the rules and the receipts, not the snippet, so the ingest route takes "
            "one page view per call and a caller posts one call per view."
        ),
    },
)

#: What this build does not do, published as data. A page that says it only in its
#: docstring is a page read by someone who has not read the docstring.
DELIVERY_LIMITS: dict[str, object] = {
    "calls_vendor": False,
    "sends_email": False,
    "sends_sms": False,
    "renders_in_browser_messenger": False,
    "reads": (
        "This workflow records what it would show and records what the buyer did with it. "
        "It posts no message to any vendor and opens no browser messenger. The channel is "
        "in_app because that is the only surface the research names and the only one this "
        "product can honour."
    ),
}

__all__ = [
    "APP_KINDS",
    "BLOCK_KINDS",
    "CHANNELS",
    "COLLECTIONS",
    "DELIVERY_LIMITS",
    "DELIVERY_STATES",
    "DWELL_SECONDS",
    "ENGAGEMENT_INTERACTIONS",
    "FREQUENCY_MODES",
    "INTERACTIONS",
    "REPEAT_VISITS",
    "REPEAT_WINDOW_DAYS",
    "SESSION_HIDING_INTERACTIONS",
    "TARGET_RULE_KINDS",
    "THRESHOLDS",
    "THRESHOLDS_TO_SHOW",
    "TRIGGER_PANES",
    "TRIGGER_SIGNALS",
    "UNSOURCED_LIMITS",
    "URL_MATCH_MODES",
    "WORKFLOW_STATES",
]
