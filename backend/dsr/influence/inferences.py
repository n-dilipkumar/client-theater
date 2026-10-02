"""Every design inference WF-019 rests on, and how a deployment changes each one.

The research for this workflow is unusually explicit about what it does *not*
claim, and the gaps it lists are exactly the places where an implementation has
to choose. A choice hidden in an arithmetic expression cannot be disagreed with;
a choice with a name and a switch can. That is the whole reason this module
exists, and it is served at ``GET /api/wf-019/inferences`` so a reviewer reads
the choices as data rather than having to find them in the code.

Each entry carries:

``id``
    a stable name, so a review comment can say "inference ``two_windows`` is
    wrong" instead of quoting arithmetic.
``decision``
    what this build does.
``basis``
    the researched sentence it rests on, quoted or paraphrased closely, or
    ``inference`` where the sources say nothing at all.
``changeable_by``
    what a deployment can do differently without a code change - a field name, a
    query parameter, an environment variable - or ``nothing`` when the choice is
    structural.

Nothing here is a hidden setting. Where a switch exists, the code reads it.
"""

from __future__ import annotations

from typing import Any

#: The researched grains, quoted by the user flow: "Content engagement over time
#: graph (day / week / month / quarter / year)".
GRAINS_QUOTED = "Content engagement over time graph (day / week / month / quarter / year)"

DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "utilization_and_engagement_denominator",
        "question": "The two researched rates are a percentage of 'content'. Of what?",
        "decision": (
            "The filtered asset set: after the collection filter, before any event is "
            "counted. An asset that was never shared is still in the denominator."
        ),
        "basis": (
            "'Utilization rate: the % of content that has been shared at least once.' and "
            "'Engagement rate: the % of content has been viewed at least once.' Neither "
            "source states the denominator."
        ),
        "changeable_by": (
            "Nothing. Every response carries assets_in_scope plus each rate's numerator, so "
            "a reader can divide it themselves and see which denominator was used."
        ),
    },
    {
        "id": "empty_library_reports_no_rate",
        "question": "What does utilization rate say when the library has no assets?",
        "decision": (
            "null, not 0.0, and library_built_out is false. The researched report is "
            "'assumed' to have a built-out library, and a confident 0% over an empty "
            "library reads as a content failure rather than a missing library."
        ),
        "basis": "'Please note, this report relies on your Content Management Library having been built out!'",
        "changeable_by": "Nothing. It is the difference between 'no assets' and 'no sharing'.",
    },
    {
        "id": "client_views_are_external",
        "question": "Which views count toward 'Content client views'?",
        "decision": (
            "Views whose audience is external. Internal views are counted separately as "
            "internal_views, and an internal view never becomes a client view."
        ),
        "basis": (
            "The researched content-analytics definitions: 'Views: the number of times an "
            "external person viewed the asset. Shares: the number of times an internal "
            "person shared the asset a new time.'"
        ),
        "changeable_by": "The ingest payload's `audience` or `internal` field, per event.",
    },
    {
        "id": "two_independent_windows",
        "question": (
            "The filters offer a client-activity range and a shares range. Does one bound "
            "the other?"
        ),
        "decision": (
            "No. Each range bounds the events it names: the shares window bounds shares, "
            "the client-activity window bounds views and downloads. A view outside the "
            "shares window still counts as a view."
        ),
        "basis": (
            "Step 6 of the researched user flow lists them as two filters: 'Filter by "
            "collection, client-activity time range, and shares time range.' The sources do "
            "not say how they interact."
        ),
        "changeable_by": "The query parameters, and the Filters dataclass for a caller in-process.",
    },
    {
        "id": "buckets_are_utc_and_a_week_starts_monday",
        "question": "Which timezone and which week boundary does the trend graph use?",
        "decision": (
            "UTC calendar buckets throughout, and a week starts on Monday. A timestamp "
            "written without an offset is read as UTC."
        ),
        "basis": "inference - the research names the grains and nothing about their boundaries.",
        "changeable_by": (
            "Nothing structural. A deployment that needs local-time buckets needs one "
            "function changed, bucket_key in dsr/influence/vocab.py."
        ),
    },
    {
        "id": "revenue_is_associated_not_caused",
        "question": "How does an asset end up next to a number of dollars?",
        "decision": (
            "Three things must all hold: the deal is linked to a workspace, the deal names "
            "the asset, and that asset was shared, viewed or downloaded in that workspace. "
            "The evidence travels with every row. A deal naming an asset with no engagement "
            "in its own workspace is reported as an unassociated link with the reason, and "
            "a workspace with engagement whose deal names no asset is reported as an "
            "unattributed workspace."
        ),
        "basis": (
            "'For each piece of content, you will see the breakdown of revenue and deals "
            "associated with the asset' - a breakdown, not a model. The research records "
            "this vendor's own limitation as gap 6: 'Content-to-revenue association exists "
            "only as a per-asset breakdown, not as a model.'"
        ),
        "changeable_by": "The `assets` list on the link, which is what a human fills in.",
    },
    {
        "id": "revenue_is_never_summed_across_currencies",
        "question": "What is the revenue total when deals are in more than one currency?",
        "decision": (
            "No single number. `revenue` is null and `revenue_by_currency` carries the "
            "per-currency sums, with revenue_mixed_currencies set."
        ),
        "basis": "inference - no source documents a conversion rate, and inventing one would be a number nobody could audit.",
        "changeable_by": "The `currency` field on each link, and DSR_CRM_CURRENCY for the default.",
    },
    {
        "id": "webhook_redelivery_is_deduplicated_on_a_supplied_id",
        "question": "What happens when the same asset.shared arrives twice?",
        "decision": (
            "It is one occurrence, provided the caller supplies the same event_id. The "
            "second call returns the original row, writes nothing, and says duplicate: true. "
            "An event with no event_id is never deduplicated."
        ),
        "basis": "inference - no source documents redelivery, and guessing an identity the caller did not provide would merge two real shares.",
        "changeable_by": "The `event_id` field in the ingest payload.",
    },
    {
        "id": "a_collection_is_a_filter_not_a_taxonomy",
        "question": "Which fields count as a collection, given the store declares no schema?",
        "decision": (
            "collections, collection, tags and tag are all read, and a filter matches if the "
            "name is in any of them. A library filed under one tag is still reachable by "
            "filtering."
        ),
        "basis": (
            "The research names 'Library Views & Tags organisation' as part of the same "
            "surface as the collection filter, and AGENTS.md forbids a typed column for a "
            "team's own field."
        ),
        "changeable_by": "Adding the field to an asset's JSON. No migration, no code change.",
    },
    {
        "id": "a_truncated_report_says_so",
        "question": "What happens when the event log is bigger than one report will read?",
        "decision": (
            "Scanning stops at 20000 events and the response carries truncated: true. The "
            "alternative - returning a short read as if it were the whole log - reports "
            "confident numbers that are wrong."
        ),
        "basis": "inference - no source documents a volume this report must survive, and the choice is only defensible if it is visible.",
        "changeable_by": "MAX_EVENTS in dsr/influence/vocab.py.",
    },
    {
        "id": "a_room_scoped_report_is_a_different_division",
        "question": ("What does the same report mean when it is scoped to one workspace?"),
        "decision": (
            "Two changes, both forced by coherence. A share made straight from the library "
            "has no workspace, so it is excluded. And the denominator becomes the assets "
            "that had activity in that workspace, not the whole library: the report answers "
            "'of the content this workspace used, how much was shared', and a room that "
            "shared two of twenty-three assets must not be reported as 0% utilized."
        ),
        "basis": (
            "inference for the denominator. The portfolio report's own scope is researched: "
            "'analyzes activity of library assets across all workspaces.' Nothing in the "
            "sources describes a per-workspace variant of the report, which is the "
            "workspace-scoped route the feature contract requires."
        ),
        "changeable_by": "Nothing. The portfolio report is the same compilation without room_id.",
    },
    {
        "id": "one_occurrence_may_be_spelled_several_ways",
        "question": (
            "The product writes 'viewed' into its activity collection and another feature "
            "writes 'viewed_document' into the same collection. Are those two events?"
        ),
        "decision": (
            "No - they are the same occurrence, and both map onto 'viewed'. The researched "
            "webhook spellings (asset.viewed), the plain ones (viewed) and the "
            "object-in-the-verb ones (viewed_document) are all accepted on ingest and on "
            "the activity backfill."
        ),
        "basis": (
            "inference - the research names the three webhook events and says nothing about "
            "this codebase's own activity vocabulary. Mapping them separately would make the "
            "backfill drop a third of the activity in the room a reader is looking at."
        ),
        "changeable_by": "ACTION_ALIASES in dsr/influence/vocab.py, and /vocabulary at runtime.",
    },
)


def describe() -> dict[str, Any]:
    """Every inference, as data. A read with no side effect, so no store."""
    return {
        "count": len(DEFINITIONS),
        "inferences": [dict(entry) for entry in DEFINITIONS],
        "note": (
            "Entries whose basis is 'inference' are choices this build made because the "
            "researched sources say nothing about them. Everything else is a decision the "
            "research already made, quoted so a change of source is visible as a change of "
            "this list."
        ),
        "grains_quoted": GRAINS_QUOTED,
    }
