"""Every published name for WF-133, in one place.

The research quotes one number and names five threshold inputs. This module holds
the quoted number, the four derived ones, and the reason each derivation took the
value it took. A caller reads its pickers from ``GET /api/wf-133/vocabulary``
rather than from a list compiled into a page, so a threshold changed here reaches
every client at once.

Nothing here imports the framework. It is names, numbers and sentences, which is
what makes the rules in :mod:`dsr.intent_routing.thresholds` testable without a
database.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

#: A named list of target accounts. Only an account on a watchlist alerts, which
#: is how the research's "the target-account list" becomes a rule rather than a
#: hope.
WATCHLISTS = "wf133_watchlist"

#: One link in the alert routing chain. Rules are read in ``position`` order and
#: the first match wins unless it says otherwise.
RULES = "wf133_routing_rule"

#: The result of evaluating one company's engagement against the thresholds. A
#: signal is the fact that the thresholds were crossed, kept whether or not it
#: went on to produce an alert.
SIGNALS = "wf133_intent_signal"

#: One dispatch of a signal to its recipients, carrying the three researched
#: facts and the decision made for each named channel.
ALERTS = "wf133_alert"

#: The follow-up task created on the opportunity. The research names this as a
#: step in the data flow rather than as an automation, so it is its own record.
TASKS = "wf133_crm_task"

#: The append-only log of what a rep did about a signal. This is the last step of
#: the researched data flow, and nothing follows it, so nothing closes it for the
#: rep.
ACTIONS = "wf133_signal_action"

COLLECTIONS: dict[str, str] = {
    "watchlists": WATCHLISTS,
    "rules": RULES,
    "signals": SIGNALS,
    "alerts": ALERTS,
    "tasks": TASKS,
    "actions": ACTIONS,
}

# --------------------------------------------------------------------------- #
# The threshold inputs
# --------------------------------------------------------------------------- #

#: The only number the research quotes. "A target account's stakeholder returns
#: to the room and reads the pricing section for 90 seconds."
DWELL_SECONDS = 90

#: Derived. Four distinct pages. A single section read cannot reach four pages by
#: scrolling within one section, so four is the smallest count that separates
#: breadth from depth inside one section.
DISTINCT_PAGES = 4

#: Derived. Three returns to the same page. A return is weaker evidence than a
#: first read, so it takes three to count; two are a reload.
REVISITS = 3

#: Derived. One download. A download is a deliberate act of taking material away,
#: so one is enough and the value is a count of one rather than a range.
DOWNLOADS = 1

#: Derived. One demo interaction. Interacting with a demo is an explicit act of
#: evaluation, so one is enough on the same grounds as a download.
DEMO_INTERACTIONS = 1

#: The five threshold inputs the research names in ``data_flow``: "pages, dwell,
#: revisit, download, demo interaction". The order is the order the field quotes.
THRESHOLDS: tuple[dict[str, Any], ...] = (
    {
        "kind": "dwell",
        "label": "Time on one page",
        "unit": "seconds",
        "threshold": DWELL_SECONDS,
        "measure": "dwell_seconds",
        "reads": "The longest single page read in the window, in whole seconds.",
        "sourced": True,
        "derivation": (
            "Quoted. The worked example is a stakeholder who reads the pricing section for 90 "
            "seconds, so 90 is the threshold and it is the one number in this workflow that is "
            "not inferred."
        ),
    },
    {
        "kind": "pages",
        "label": "Distinct pages",
        "unit": "pages",
        "threshold": DISTINCT_PAGES,
        "measure": "pages",
        "reads": "How many distinct pages the company read inside the window.",
        "sourced": False,
        "derivation": (
            "The research lists pages as a threshold input and gives it no number. Four distinct "
            "pages is the smallest count a reader cannot reach by scrolling within the one section "
            "the worked example describes, so it measures breadth rather than depth."
        ),
    },
    {
        "kind": "revisit",
        "label": "Returns to one page",
        "unit": "visits",
        "threshold": REVISITS,
        "measure": "revisits",
        "reads": "How many times the company came back to the same page in the window.",
        "sourced": False,
        "derivation": (
            "The research lists revisit as a threshold input and gives it no number. Three is the "
            "count at which a return reads as interest rather than as a page failing to load, "
            "because two returns are equally consistent with a reload."
        ),
    },
    {
        "kind": "download",
        "label": "Downloads",
        "unit": "downloads",
        "threshold": DOWNLOADS,
        "measure": "downloads",
        "reads": "How many documents the company downloaded in the window.",
        "sourced": False,
        "derivation": (
            "The research lists download as a threshold input and gives it no number. A download "
            "is a deliberate act of taking material away from the room, so one is enough and the "
            "value is a count of one rather than a range."
        ),
    },
    {
        "kind": "demo_interaction",
        "label": "Demo interactions",
        "unit": "interactions",
        "threshold": DEMO_INTERACTIONS,
        "measure": "demo_interactions",
        "reads": "How many times the company interacted with a demo in the window.",
        "sourced": False,
        "derivation": (
            "The research lists demo interaction as a threshold input and gives it no number. "
            "Interacting with a demo is an explicit act of evaluation rather than a passive read, "
            "so one is enough on the same grounds as a download."
        ),
    },
)

THRESHOLD_KINDS: tuple[str, ...] = tuple(entry["kind"] for entry in THRESHOLDS)

#: How many thresholds had to be crossed for the signal to be raised at all. One,
#: because the research describes a single qualifying action raising a signal, and
#: a workflow that made a rep clear several bars would lose the moment the research
#: is describing.
THRESHOLDS_TO_ALERT = 1

# --------------------------------------------------------------------------- #
# The evaluation window
# --------------------------------------------------------------------------- #

#: How far back an evaluation reaches, in hours. Seven days, because the research's
#: own worked example is a stakeholder who "returns to the room", and a return is
#: slower than a visit.
WINDOW_HOURS = 24 * 7

# --------------------------------------------------------------------------- #
# Contact suppression
# --------------------------------------------------------------------------- #

#: How long an account stays suppressed after an alert reaches its recipients, in
#: hours. The research names "auto-suppress contacted accounts" and states no
#: window. Twenty-four hours is one working day, which is the shortest span in
#: which a second alert about the same account is noise rather than news.
SUPPRESSION_HOURS = 24

# --------------------------------------------------------------------------- #
# Notification channels
# --------------------------------------------------------------------------- #

EMAIL_CHANNEL = "email"
SLACK_CHANNEL = "slack"
NOTIFICATION_CHANNELS: tuple[str, ...] = (EMAIL_CHANNEL, SLACK_CHANNEL)

#: The channel the research quotes as a working recipient. It is the only one this
#: build delivers a queued message for.
PRIMARY_CHANNEL = EMAIL_CHANNEL

# What happened to one channel on one alert. There is no "sent" value, and that
# absence is the design rather than an omission: this product has no outbound mail
# or Slack transport, so a state that claimed delivery would be a claim nothing
# could support.
DISPATCH_QUEUED = "queued"
DISPATCH_HELD = "held_for_integration"
DISPATCH_SUPPRESSED = "suppressed"
DISPATCH_SKIPPED = "skipped"
DISPATCH_STATES: tuple[str, ...] = (
    DISPATCH_QUEUED,
    DISPATCH_HELD,
    DISPATCH_SUPPRESSED,
    DISPATCH_SKIPPED,
)

# --------------------------------------------------------------------------- #
# Recipient routing
# --------------------------------------------------------------------------- #

#: Route to the opportunity owner named on the deal. This is the researched flow:
#: "the account owner gets an email and a Slack DM". It is tried first and it is
#: the only rule the build creates by itself.
RULE_CRM_OWNER = "crm_owner"

#: Route to a named team. The research names "CRM ownership and territory rules"
#: as a data source and the product has no territory field of its own, so a team
#: is read from the paths an importing team may have put on a CRM record.
RULE_TEAM = "team"

#: Route to recipients the watchlist itself names. This is how a seller watches a
#: market nobody owns yet.
RULE_WATCHLIST = "watchlist"

#: Route to a default recipient when nothing above matched. Without it an alert
#: with no owner would have nowhere to go and would be lost rather than deferred.
RULE_FALLBACK = "fallback"

RULE_KINDS: tuple[str, ...] = (RULE_CRM_OWNER, RULE_TEAM, RULE_WATCHLIST, RULE_FALLBACK)

#: The paths a team may be found on, most specific first. WF-042 copies unknown
#: payload keys through untouched, so a team is whatever an importing team put
#: there, and these are the two spellings this product has seen.
TEAM_PATHS: tuple[tuple[str, ...], ...] = (("crm_owner", "team"), ("team",))

# --------------------------------------------------------------------------- #
# Signal and alert states
# --------------------------------------------------------------------------- #

ALERT_OPEN = "open"
ALERT_ACKNOWLEDGED = "acknowledged"
ALERT_CONTACTED = "contacted"
ALERT_DISMISSED = "dismissed"

#: Ordered by how far the rep has got. The alert's state is the furthest of these
#: the log contains, so a rep who acknowledges and then contacts leaves the alert
#: at contacted without a second write.
ALERT_STATES: tuple[str, ...] = (
    ALERT_OPEN,
    ALERT_ACKNOWLEDGED,
    ALERT_DISMISSED,
    ALERT_CONTACTED,
)

#: What a rep may record. "Rep action logged" is the last step of the researched
#: data flow and nothing follows it, so the workflow does not decide what a rep
#: does next. It records that something happened.
ACTION_KINDS: tuple[str, ...] = ("acknowledged", "dismissed", "contacted", "noted")

#: A dismissal without a reason is an alert a rep could not be bothered with. The
#: note is required for this one kind and optional for the rest.
NOTE_REQUIRED_FOR: tuple[str, ...] = ("dismissed",)

# --------------------------------------------------------------------------- #
# Watchlists
# --------------------------------------------------------------------------- #

TIER_STRATEGIC = "strategic"
TIER_NAMED = "named"
TIER_MARKET = "market"
WATCHLIST_TIERS: tuple[str, ...] = (TIER_STRATEGIC, TIER_NAMED, TIER_MARKET)

#: What the research's "who is engaged and who is not" view reports per account.
ENGAGED = "engaged"
UNENGAGED = "not_engaged"
ENGAGEMENT_STATES: tuple[str, ...] = (ENGAGED, UNENGAGED)

__all__ = [
    "ACTIONS",
    "ACTION_KINDS",
    "ALERTS",
    "ALERT_ACKNOWLEDGED",
    "ALERT_CONTACTED",
    "ALERT_DISMISSED",
    "ALERT_OPEN",
    "ALERT_STATES",
    "COLLECTIONS",
    "DEMO_INTERACTIONS",
    "DISPATCH_HELD",
    "DISPATCH_QUEUED",
    "DISPATCH_SKIPPED",
    "DISPATCH_STATES",
    "DISPATCH_SUPPRESSED",
    "DISTINCT_PAGES",
    "DOWNLOADS",
    "DWELL_SECONDS",
    "EMAIL_CHANNEL",
    "ENGAGED",
    "ENGAGEMENT_STATES",
    "NOTIFICATION_CHANNELS",
    "NOTE_REQUIRED_FOR",
    "PRIMARY_CHANNEL",
    "REVISITS",
    "RULES",
    "RULE_CRM_OWNER",
    "RULE_FALLBACK",
    "RULE_KINDS",
    "RULE_TEAM",
    "RULE_WATCHLIST",
    "SIGNALS",
    "SLACK_CHANNEL",
    "SUPPRESSION_HOURS",
    "TASKS",
    "TEAM_PATHS",
    "THRESHOLDS",
    "THRESHOLD_KINDS",
    "THRESHOLDS_TO_ALERT",
    "TIER_MARKET",
    "TIER_NAMED",
    "TIER_STRATEGIC",
    "UNENGAGED",
    "WATCHLISTS",
    "WATCHLIST_TIERS",
    "WINDOW_HOURS",
]
