"""Every judgement call WF-049 rests on, in one inspectable place.

The research for this workflow is specific about the surfaces quota is read
from - Salesforce's ``Sforce-Limit-Info`` header and ``GET /limits/``, the
HubSpot rate-limit headers and account-information endpoint, the Dataverse
change-tracking audit and its ``globalmetadataversion`` drift annotation - and
about the two automations: quota polling on a fixed interval, and alert rules
on remaining budget and change-stream lag.

What the research does **not** say falls in two piles, and this build is
explicit about which pile each decision came from.

*Sourced but not implemented here.* The HubSpot and Salesforce *UI* pages -
Development → Monitoring, Setup → Monitor → API Usage - are where the research
says a vendor's own operator looks. The room serves the same numbers as data
instead; it does not scrape a vendor's UI, because that is not something the
research's API surface supports and this product holds no vendor credentials.

*Genuinely open.* The dashboard's window, the alert cooldown, the concurrency
default, the status-to-class mapping, and what a Dataverse quota reading even
is (the research's own gap). Each entry below says so in its own ``basis``.

Every entry is:

* **named**, so it can be argued with by name rather than found in a diff;
* **traceable** - ``basis`` quotes what the research does and does not say;
* **bounded** - ``value`` is what this build chose and ``change_it`` says how
  to change it without editing a function body;
* **visible** - :func:`describe` is served at ``/api/wf-049/inferences``.
"""

from __future__ import annotations

from typing import Any

SOURCED_AUTOMATION = (
    "Quota polling on a fixed interval; alert rules fire when remaining budget crosses a "
    "threshold or when the change-stream lag exceeds N seconds."
)

SOURCED_EXTENSIBILITY = (
    "Because monitoring is fed by a connector-declared quota policy (the same object used "
    "in W13), there is exactly one place to teach the system a new vendor's numbers."
)

SOURCED_GAP = (
    "Dataverse's per-service numeric limit table (Service Protection API Limits) was not "
    "found at a readable URL. HubSpot's newer GraphQL/account-information usage fields were "
    "not enumerated in the page read."
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "oauth-daily-is-unknown-not-zero",
        "topic": "what a HubSpot OAuth connection's daily quota reads as",
        "basis": (
            "Sourced: X-HubSpot-RateLimit-Daily 'is not included in the response to API "
            "requests authorized using OAuth'. Not sourced: anything else the room should do "
            "in its place."
        ),
        "value": {"daily_known": False, "never": "zero", "note_travels": True},
        "why": (
            "Zero and unknown are different facts, and confusing them here is the worst "
            "version: a daily quota reported as zero would fire every budget rule on every "
            "healthy OAuth connection, and a rule that cries wolf is muted, which is worse "
            "than no rule. Unknown keeps the window half - which OAuth does carry - live, "
            "and the observation carries the reason the daily half is blank."
        ),
        "change_it": "No knob. empty_half() in dsr/integ_monitor/quota.py is the mechanism.",
        "effect_on_default": "an OAuth connection shows a live window pair and an unknown daily pair, and says why.",
    },
    {
        "id": "salesforce-limits-are-daily",
        "topic": "which half of the pair a Salesforce reading lands in",
        "basis": (
            "Sourced: Sforce-Limit-Info rides every REST call and the /limits/ resource "
            "returns max and remaining per limit. Not sourced: any burst-window model for "
            "Salesforce API calls in the source set."
        ),
        "value": {"daily": "read", "window": "always unknown"},
        "why": (
            "Salesforce's API request limits are daily org-wide caps, so the reading belongs "
            "in 'remaining today' and the window half is reported unknown rather than left "
            "null-by-accident. The alternative - putting the header into the window half "
            "because it refreshed every call - would make a lag rule and a budget rule "
            "disagree about what a day is."
        ),
        "change_it": "The surface branches in dsr/integ_monitor/quota.py normalise().",
        "effect_on_default": "a Salesforce connection monitors 'remaining today' and leaves the window pair unknown.",
    },
    {
        "id": "the-room-holds-no-vendor-credentials",
        "topic": "who reads the vendor's surfaces",
        "basis": (
            "Sourced: the surfaces are documented, and the data flow reads 'vendor quota "
            "metadata ... -> room metrics store'. Not sourced: any statement that this "
            "product holds vendor credentials."
        ),
        "value": {"room_transports_nothing": True, "connector_hands_in": "the reading"},
        "why": (
            "The connectors are the ones already making calls against the vendor - every "
            "one of them carries the quota surfaces on responses they were going to make "
            "anyway. A second reader with its own credential would double-spend the very "
            "quota this feature monitors, which is the ironic failure. So the room parses "
            "and stores what the connector hands in, and never opens a socket."
        ),
        "change_it": "No knob. POST /api/wf-049/connectors/{id}/quota takes the answer, and never fetches.",
        "effect_on_default": "polling is the connector's job; the room is the metrics store and the dashboard.",
    },
    {
        "id": "throttle-is-429-and-the-vendors-word-wins",
        "topic": "how a call lands in one of the four researched error classes",
        "basis": (
            "Sourced: the breakdown names validation / throttle / auth / vendor-5xx. Not "
            "sourced: any status-to-class mapping. WF-040's research quotes Salesforce's "
            "403 REQUEST_LIMIT_EXCEEDED, which is a 403 behaving as a throttle."
        ),
        "value": {
            "401": "auth",
            "429": "throttle",
            "5xx": "vendor_5xx",
            "other_4xx": "validation",
            "explicit_class": "wins",
        },
        "why": (
            "403 is the one status that is genuinely ambiguous - Salesforce uses it for "
            "REQUEST_LIMIT_EXCEEDED and HubSpot for FORBIDDEN - so the mapping deliberately "
            "does not guess it. A connector that knows which it was passes error_class "
            "explicitly and that wins; a 403 with no class falls to validation, which is the "
            "conservative reading, and the sample carries the status so the gap is visible."
        ),
        "change_it": "Pass error_class on the call sample; STATUS_CLASSES in dsr/integ_monitor/health.py holds the rest.",
        "effect_on_default": "a bare 403 lands in validation and says so; a 403 named as throttle counts as throttle.",
    },
    {
        "id": "success-window-is-24h",
        "topic": "how far back the dashboard's success rate and mean latency reach",
        "basis": (
            "Not sourced. The research says the dashboard shows a sync success rate and a "
            "mean latency and names no window for either."
        ),
        "value": {
            "window_seconds": 86400,
            "param": "window_seconds",
            "bounds": "1 minute to 30 days",
        },
        "why": (
            "Twenty-four hours is the only span every vendor in the source set already "
            "speaks of - Salesforce's usage data covers 'the last 24 hours' without Enhanced "
            "Usage Metrics - so a room default that matches the vendors' own reporting is "
            "one a reading can be compared against. It is a parameter, not a constant, and "
            "the response reports the window it used."
        ),
        "change_it": "GET /api/wf-049/rooms/{room_id}/dashboard?window_seconds=N.",
        "effect_on_default": "the dashboard reads one day of telemetry by default and labels it.",
    },
    {
        "id": "alert-cooldown-30-minutes",
        "topic": "how long a rule stays quiet after it fires",
        "basis": (
            "Sourced: rules fire when a threshold is crossed. Not sourced: what the second "
            "poll that sees the same crossing does."
        ),
        "value": {"cooldown_minutes": 30, "fires_on": "crossing, not state", "per_rule": True},
        "why": (
            "A starved connector is starved for hours; without a cooldown the budget rule "
            "fires on every poll, the operator mutes the room, and the alert that mattered "
            "goes with it. Firing on the crossing rather than the state keeps the alert an "
            "event. The cooldown is per rule and stored on the rule, so a deployment that "
            "wants page-on-every-poll sets it to zero."
        ),
        "change_it": "POST /api/wf-049/rooms/{room_id}/alerts/rules with cooldown_minutes, or PATCH the rule.",
        "effect_on_default": "one fire per crossing per half hour, with the suppressed evaluations counted and reasoned.",
    },
    {
        "id": "concurrency-default-4",
        "topic": "the concurrency an operator lowers when a connector is starved",
        "basis": (
            "Sourced: 'If a connector is starved, the operator lowers its concurrency or "
            "pauses it from the same page.' Not sourced: any starting value."
        ),
        "value": {"default": 4, "bounds": "1 to 64"},
        "why": (
            "The research names the control and not the number, so the number is shipped as "
            "a bounded default rather than a hidden constant: it is on the connector record, "
            "patched through the same route the pause button uses, and the bounds are "
            "refusals rather than silent clamps so an operator typing 0 finds out now."
        ),
        "change_it": 'PATCH /api/wf-049/connectors/{id} with {"concurrency": N}.',
        "effect_on_default": "every monitored connector starts at 4 and says so on the record.",
    },
    {
        "id": "lag-is-observed-minus-source",
        "topic": "how the change-stream lag is computed",
        "basis": (
            "Sourced: 'the live change-stream lag' and the automation's threshold in "
            "seconds. Not sourced: how a connector measures the lag, or which of the two "
            "timestamps to prefer."
        ),
        "value": {
            "direct": "lag_seconds",
            "computed": "observed_at - source_event_at",
            "negative": "clamped to 0",
        },
        "why": (
            "Both shapes exist in real streams: some connectors are told the lag by their "
            "client, some can only subtract the vendor's event time from their own receipt "
            "time. Accepting both keeps the metric fed either way. A receiver ahead of its "
            "source is clock skew, and recording a negative lag would make a lag rule "
            "un-fireable, so the computed value clamps at zero and the raw timestamps stay "
            "on the observation."
        ),
        "change_it": "POST /api/wf-049/connectors/{id}/stream with either shape.",
        "effect_on_default": "the dashboard reads the latest lag either way, and a lag rule works on both.",
    },
    {
        "id": "schema-drift-is-a-signal-not-a-failure",
        "topic": "what a globalmetadataversion change means",
        "basis": (
            "Sourced: 'The value changes when any schema change occurs, indicating that you "
            "might need to refresh any schema data that your application cached.'"
        ),
        "value": {"on_change": "drift flag", "is_failure": False, "verb": "might"},
        "why": (
            "The researched sentence is advisory - 'you might need to refresh' - and coding "
            "it as a failure would alert on every harmless metadata change. Coding it as "
            "nothing would defeat the point of reading the annotation. So a version change "
            "is recorded as a drift signal on the room's change-tracking view: a flag, a "
            "timestamp and the two versions, for the operator to decide."
        ),
        "change_it": "No knob. record_change_tracking in dsr/integ_monitor/engine.py computes it.",
        "effect_on_default": "the change-tracking view shows drift: true with the two versions when the vendor moved.",
    },
    {
        "id": "dataverse-has-no-sourced-numbers",
        "topic": "what a Dataverse quota reading is, given the research's gap",
        "basis": (
            "Sourced, as a gap: 'Dataverse's per-service numeric limit table (Service "
            "Protection API Limits) was not found at a readable URL.'"
        ),
        "value": {"quota_readings": "refused", "change_tracking": "served", "reason_travels": True},
        "why": (
            "The room could answer a Dataverse quota question with invented numbers, or with "
            "an unknown pair that says why. The first produces a dashboard that looks "
            "finished and is wrong; the second is the honest state and names the exact thing "
            "a future source update would fix. A refused reading also keeps a typo'd vendor "
            "or surface from writing a row nobody can act on."
        ),
        "change_it": "Register the surface when the research lands its numbers; QUOTA_SURFACES in vocabulary.py is the one place.",
        "effect_on_default": "POST quota for dataverse is a 400 quoting the gap; its change-tracking audit still works.",
    },
    {
        "id": "unreachable-vendor-entries-are-not-zeros",
        "topic": "what a limit row the connector could not read becomes",
        "basis": (
            "Sourced: Salesforce's DataStorageMB row 'requires the Manage Users permission'. "
            "Not sourced: what to record when the connector cannot read a row."
        ),
        "value": {"unreachable": "unknown with a note", "never": "zero"},
        "why": (
            "A permission the connector lacks is not an allocation of zero, and recording "
            "zero would compute a 0% remaining that fires every budget rule for an org that "
            "is perfectly healthy. The row the connector could read forms the pair; the rest "
            "of the table is kept verbatim on the observation so the gap is on the record."
        ),
        "change_it": "No knob. The connector's declared limit_name picks the row; the full table rides the observation.",
        "effect_on_default": "an unreadable org limit is unknown-with-a-reason, never a fired alert.",
    },
    {
        "id": "evaluation-runs-on-read",
        "topic": "when the alert rules actually evaluate",
        "basis": (
            "Sourced: 'Quota polling on a fixed interval' is the automation, and the "
            "operator's page shows all of this at once. Not sourced: any scheduler this "
            "product owns."
        ),
        "value": {
            "on": "every dashboard load and every evaluate call",
            "endpoint": "POST /rooms/{room_id}/alerts/evaluate",
        },
        "why": (
            "The room has no background job to hide an evaluation behind, and a rule that "
            "only a timer can see is a rule nobody can debug. Evaluating on read also makes "
            "the demo and the tests exercise the real rule rather than a scheduled "
            "shadow of it. The cost is that the fire only happens when someone looks or a "
            "scheduler calls the endpoint - which is the seam a delivery integration uses."
        ),
        "change_it": "No knob. It is the same trade every read-computed workflow in this product makes.",
        "effect_on_default": "an alert fires when the room is asked; the fire is recorded and the cooldown starts then.",
    },
)


def describe() -> dict[str, Any]:
    """The whole registry, served verbatim at ``/api/wf-049/inferences``."""
    return {
        "sourced_automation": SOURCED_AUTOMATION,
        "sourced_extensibility": SOURCED_EXTENSIBILITY,
        "sourced_gap": SOURCED_GAP,
        "count": len(INFERENCES),
        "inferences": [dict(entry) for entry in INFERENCES],
        "ids": [str(entry["id"]) for entry in INFERENCES],
    }


def by_id(inference_id: str) -> dict[str, Any] | None:
    return next((entry for entry in INFERENCES if entry["id"] == inference_id), None)


__all__ = [
    "SOURCED_AUTOMATION",
    "SOURCED_EXTENSIBILITY",
    "SOURCED_GAP",
    "INFERENCES",
    "describe",
    "by_id",
]
