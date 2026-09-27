"""Every judgement call WF-018 makes, in one inspectable place.

The research is unusually candid about its own limits, and the build brief asks
for that line to be kept rather than blurred:

* "**apis_hit:** No documented per-page analytics endpoint."
* "**extensibility:** A third party can build its own per-page scoring by
  consuming ``asset.viewed`` events and joining to its own viewer telemetry;
  there is no documented Dock endpoint for per-page timing."

So the *inputs* here are sourced - the asset snapshot fields, the webhook event
names, the three analytics and what each one is called, the external-only rule,
the multi-page and self-hosted restrictions - and the *mechanics* are not. This
module is where that difference is written down. Every entry is:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` says what the research does and does not say;
* **bounded** - ``value`` is what this build chose, and ``change_it`` says how to
  change it without editing a function body;
* **visible** - :func:`describe` is served at ``/api/wf-018/inferences``, so a
  reviewer reads the list rather than inferring it from diffs.

Nothing here is a migration, a typed column, or a new required field. It is a
list of ordinary JSON, like everything else this product stores, and it is a
*record* of a judgement rather than a mechanism that enforces one.
"""

from __future__ import annotations

from typing import Any

from .vocabulary import (
    ASSET_SNAPSHOT_FIELDS,
    ASSET_TYPES,
    MIN_PAGES_FOR_PDF_ANALYTICS,
    WEBHOOK_EVENT_TYPES,
)

#: The three lines of the research that govern everything in this module.
SOURCED_QUOTES: tuple[str, ...] = (
    "PDF Analytics - For multi-page PDFs, we're able to show two additional metics: "
    "Time spent per page: the average amount of time that's spent per page. This shows you "
    "what content resonates most with your audience. / Drop off per page: understand when "
    "someone stops looking at your content. This gives you a sense of where people are "
    "falling off and what may be less valuable to share.",
    "Video Analytics - For self-hosted videos, we're able to show the average watch time "
    "of the video.",
    "Dock's analytics only show engagement from external users (i.e. buyers and customers). "
    "The one exception is 'Shares' which is an internal metric showing how often the "
    "internal team shares a specific asset.",
)


INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "per-page-ingest-is-ours",
        "topic": "where per-page timing comes from",
        "topic_note": "the largest inference in the package",
        "basis": (
            "'No documented per-page analytics endpoint' and 'there is no documented Dock "
            "endpoint for per-page timing'. The research describes the viewer emitting the "
            "timing and says a third party joins it to its own telemetry; it does not define "
            "the request that carries it."
        ),
        "value": {
            "endpoint": "POST /api/wf-018/assets/{asset_id}/timings",
            "shape": "one reading session: {viewer, session_id, room_id, isInternal, timings: [{page, seconds}]}",
            "batch": "one transaction, one audit row",
        },
        "why": (
            "The research's data flow - 'Dock's viewer emits per-page timing' - is a claim "
            "about who observes the timing, not about a contract. The contract is ours to "
            "write, and writing it as one session per request is the shape the described flow "
            "actually has: a viewer opened one document and reported the pages of that one "
            "open. A per-row envelope would let a batch mix readers, and the drop-off curve is "
            "only meaningful per reader."
        ),
        "change_it": (
            "AnalyticsBook.record_timings in backend/dsr/pdf_analytics/book.py. Moving the "
            "envelope to per row is a change in that method alone; the metrics read the row "
            "fields either way."
        ),
        "blast_radius": "Every dwell figure and the whole drop-off curve. Nothing else reads these rows.",
    },
    {
        "id": "drop-off-is-monotone",
        "topic": "what counts as reaching a page",
        "basis": (
            "'Drop off per page: understand when someone stops looking at your content.' The "
            "research names the metric and its purpose; it does not define the denominator."
        ),
        "value": {
            "reached[p]": "sessions whose deepest page is p or greater",
            "drop_off_rate[p]": "(reached[p] - reached[p+1]) / reached[p], 0 on the last page",
            "retained_rate[p]": "reached[p] / reached[1]",
            "attributed_to": "the last page the reader reached, not the one they never opened",
        },
        "why": (
            "A reader who scrolled from page 1 to page 5 did not stop three times on the way; "
            "counting only the rows that name a page would report a 100% collapse at pages 2, "
            "3 and 4 for a reader who read the whole document. Monotone reach is the only "
            "reading under which the curve starts at 1.0. Attributing the loss to the last "
            "page reached rather than the first page missed is the second half of the same "
            "problem: a reader who stops on the cover is not a collapse on page 2, and a "
            "seller acting on that number would cut the wrong page. The cost is that a "
            "genuinely skipped page cannot appear as a drop-off - so the per-page read counts "
            "are published alongside, and that is where skipping shows up."
        ),
        "change_it": "drop_off_per_page in backend/dsr/pdf_analytics/metrics.py, the `dropped` expression.",
        "blast_radius": "The drop-off curve and every headline number derived from it.",
    },
    {
        "id": "reading-session-gap",
        "topic": "when two page timings are the same reader reading",
        "basis": (
            "The research says the viewer emits per-page timing and that the result is an "
            "average per page. It says nothing about session boundaries, and the per-page "
            "timing is emitted with no session id in the source product."
        ),
        "value": {
            "preferred_key": "sessionId, when the viewer sends one",
            "fallback_key": "viewer",
            "gap_seconds": 1800,
        },
        "why": (
            "The curve is over readers, so the rows have to be attributable. An explicit id is "
            "better than anything inferred, so it wins when present; otherwise the same viewer "
            "with a gap over half an hour has started a new reading. A reader who closed the "
            "tab for lunch must not be counted as reaching every page in between, which is the "
            "flattering failure this threshold exists to prevent."
        ),
        "change_it": (
            "SESSION_GAP_SECONDS in backend/dsr/pdf_analytics/vocabulary.py, or the keyword on "
            "AnalyticsBook.record_timings and group_sessions."
        ),
        "blast_radius": "Session counts and the drop-off curve. Dwell per page is unaffected: it averages rows.",
    },
    {
        "id": "self-hosted-flag",
        "topic": "which videos have a watch time at all",
        "basis": (
            "'Video Analytics - For self-hosted videos, we're able to show the average watch "
            "time of the video.' The restriction is sourced; the field that expresses it is not "
            "- the asset snapshot the research lists has no such member."
        ),
        "value": {
            "field": "selfHosted",
            "default": True,
            "unavailable_reason": "not_self_hosted",
        },
        "why": (
            "A video embedded from a third party is timed by that player, not by ours, so no "
            "watch row is ever produced and an average over zero rows would read as "
            "'nobody watched'. The default is True because a video record in this library is "
            "self-hosted unless it says otherwise, and because defaulting the other way would "
            "silently hide the analytics on every asset that omitted the field."
        ),
        "change_it": "normalise_snapshot in backend/dsr/pdf_analytics/vocabulary.py, and VIDEO_UNAVAILABLE_REASONS beside it.",
        "blast_radius": "Which assets expose Video Analytics. One line each way.",
    },
    {
        "id": "asset-shared-event",
        "topic": "the third event type",
        "basis": (
            "WF-018's own apis_hit line names 'asset.viewed / asset.downloaded'. Its own "
            "evidence quote names a metric those two cannot produce: 'The one exception is "
            "'Shares' which is an internal metric showing how often the internal team shares "
            "a specific asset.' The metric is sourced; the event name is not."
        ),
        "value": {
            "accepted": list(WEBHOOK_EVENT_TYPES),
            "added": "asset.shared",
        },
        "why": (
            "The external-only rule is the most specific sentence WF-018 quotes, and it is "
            "only meaningful if the internal counter exists. Refusing asset.shared would make "
            "the exception uncomputable, and accepting only the two named types would drop a "
            "metric the research explicitly documents. The name matches the asset.* family the "
            "research does name; the sibling workflow on content influence uses it too."
        ),
        "change_it": "WEBHOOK_EVENT_TYPES in backend/dsr/pdf_analytics/vocabulary.py, and EVENT_TO_METRIC in metrics.py.",
        "blast_radius": "The shares counter, and the external-only rule's one exception.",
    },
    {
        "id": "absent-is-internal-means-external",
        "topic": "whose engagement counts when isInternal is not sent",
        "basis": (
            "'Dock's analytics only show engagement from external users (i.e. buyers and "
            "customers).' The payload carries isInternal. Whether an interaction that omits it "
            "is external is not stated."
        ),
        "value": {"missing_isInternal": "external", "explicit_true": "internal"},
        "why": (
            "The documented entry point is a trackable asset link opened by a buyer, who is by "
            "definition not the internal team, so the default has to be external or the "
            "documented flow would contribute nothing. An internal preview always says so, and "
            "a caller that means internal has one field to set."
        ),
        "change_it": "audience_of in backend/dsr/pdf_analytics/vocabulary.py, used by every ingest and every read.",
        "blast_radius": "Every metric in the package. This is the one default worth arguing with.",
    },
    {
        "id": "untracked-timings-are-refused",
        "topic": "what happens to page timings for an asset with trackingEnabled=false",
        "basis": (
            "The asset snapshot carries trackingEnabled. The research names it as a field the "
            "payload has and says nothing about what this product must do with it."
        ),
        "value": {"behaviour": "422, refused", "not": "stored and ignored"},
        "why": (
            "A dropped row leaves a hole in the drop-off curve that is indistinguishable from "
            "'readers skipped this page', which is the exact distinction this workflow exists "
            "to make. Refusing is loud and fixable; dropping is quiet and not."
        ),
        "change_it": (
            "The trackingEnabled guard at the top of AnalyticsBook.record_timings in "
            "backend/dsr/pdf_analytics/book.py."
        ),
        "blast_radius": "Only callers posting timings for an untracked asset.",
    },
    {
        "id": "core-analytics-grain",
        "topic": "the horizontal axis of the Core Analytics bar charts",
        "basis": (
            "'Core Analytics bar charts' is named as a feature. No grain, bucket, or axis "
            "vocabulary is documented anywhere in this workflow."
        ),
        "value": {"grains": ["day", "week", "month"], "default": "day", "week_starts": "Monday"},
        "why": (
            "A bar chart needs an axis and a stored timestamp needs a binning. Days are the "
            "default because that is the finest grain the timestamps support without being "
            "noise. Weeks start on Monday and months on the 1st because a chart that puts a "
            "monthly bar on the 29th has silently merged two periods."
        ),
        "change_it": "GRAINS in backend/dsr/pdf_analytics/vocabulary.py, floor() and _advance() in metrics.py.",
        "blast_radius": "The series on the Core Analytics block, and the default grain of its route.",
    },
    {
        "id": "dwell-has-no-ceiling",
        "topic": "what is not done to a suspicious dwell time",
        "basis": (
            "'the average amount of time that's spent per page' is a plain average, and the "
            "research documents no cap, no idle detection, and no bot filter."
        ),
        "value": {
            "applies": "no cap on seconds",
            "published_instead": "the per-page total, the read count, and the longest single watch",
        },
        "why": (
            "A cap would quietly change the researched metric into a different one, and a rep "
            "comparing this number with the source product's would get a different answer for "
            "the same readers. Publishing the total and the read count lets a reader see the "
            "outlier for what it is instead of having it hidden by the number that matters."
        ),
        "change_it": "dwell_per_page in backend/dsr/pdf_analytics/metrics.py. Nothing enforces a cap anywhere, so nothing has to be removed.",
        "blast_radius": "Nothing. This entry records a decision not to build.",
    },
    {
        "id": "event-snapshot-bootstraps-an-asset",
        "topic": "what an event does to the asset it names",
        "basis": (
            "The researched webhook payload 'embeds the asset snapshot (name, type, shareUrl, "
            "isInternal, tags, downloadEnabled, trackingEnabled)', and lists GET /v1/assets as "
            "an adjacent surface. It does not say what a receiver does with the embedded copy."
        ),
        "value": {
            "asset_unknown": "register it from the embedded snapshot",
            "asset_known": "leave it alone, and store the snapshot on the event row",
        },
        "why": (
            "The snapshot is embedded precisely so the receiver does not need a second call, "
            "and this product's own ingest is that second call. Storing the snapshot on the "
            "event either way is what makes the researched extensibility path real: a third "
            "party joins its viewer telemetry to asset.viewed rows on the stored snapshot, "
            "which is exactly what the research says they do."
        ),
        "change_it": "AnalyticsBook.record_event in backend/dsr/pdf_analytics/book.py.",
        "blast_radius": "Which assets exist, and what is on every event row.",
    },
)


def by_id(inference_id: str) -> dict[str, Any] | None:
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return entry
    return None


def describe() -> dict[str, Any]:
    """The whole registry, alongside the half of the workflow that is sourced.

    Both halves in one payload on purpose. The point of the endpoint is that a
    reader can see where the line falls, which means showing the sourced
    vocabulary next to the inferred mechanics rather than only the latter.
    """
    return {
        "count": len(INFERENCES),
        "sourced_quotes": list(SOURCED_QUOTES),
        "sourced": {
            "asset_types": list(ASSET_TYPES),
            "asset_snapshot_fields": list(ASSET_SNAPSHOT_FIELDS),
            "webhook_event_types": list(WEBHOOK_EVENT_TYPES),
            "min_pages_for_pdf_analytics": MIN_PAGES_FOR_PDF_ANALYTICS,
        },
        "inferences": [dict(entry) for entry in INFERENCES],
    }
