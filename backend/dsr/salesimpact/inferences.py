"""Every decision this workflow makes that the research does not make, in one list.

The research for WF-023 fixes the *vocabulary* of the report and quotes one formula
exactly. It does not define ``total pipeline touched``, it does not define how a
days-to-close average is measured, it does not mention currency, and it does not say what
a caller should see when the data is incomplete. Those are our decisions, and a judgement
call left as a comment in a function body is one nobody re-reads - a wrong one becomes
product behaviour without anyone noticing.

So each entry is:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` says what the research does and does not say, quoting it;
* **bounded** - ``value`` is what this build chose and ``change_it`` says how to change
  it without editing a function body;
* **visible** - :func:`describe` is served at ``GET /api/wf-023/inferences`` with the
  sourced quotes beside it, so a reviewer can see exactly where the line falls.

Nothing here is a migration, a typed column, or a new required field. It is a list of
ordinary JSON served from code, and it is a *record* of a judgement rather than a
mechanism that enforces one: every entry points at the constant or function that would
have to change, so disagreeing with an entry is a one-line edit rather than an argument
about a diff.
"""

from __future__ import annotations

from typing import Any

from dsr.salesimpact.vocabulary import LOST_STAGES, STAGE_CLASSES, VIEW_ACTIONS, WON_STAGES

#: The five researched sentences the inferred behaviour is contrasted against. Quoted
#: from ``docs/research/digital-sales-room-workflows/wf/WF-023.md`` verbatim, so a reader
#: can check the inference against the source without opening anything else.
SOURCED_QUOTES: dict[str, str] = {
    "inclusion": (
        "The Sales Impact report pulls in any workspace designated as a 'Sales' type that "
        "has a CRM opportunity."
    ),
    "tiles": (
        "Sales Impact (synced to your CRM): Total deals, Total pipeline touched, Active "
        "deals, Active pipeline, Closed won deals, Revenue (closed won revenue), Close "
        "rate, Days to close (average) ... Buyer Engagement: Views, actions, and average "
        "buyers per workspace, Most engaged buyers."
    ),
    "close_rate": (
        "Close rate - How many workspaces with deals/opportunities that have been closed "
        "won, divided by the total (closed won + closed lost)."
    ),
    "join": (
        "This report combines your CRM data with Dock workspace data to show the impact "
        "Dock has on your pipeline and close rates."
    ),
    "incomplete": (
        "CRM integration must be on and deals attached or the report is incomplete - "
        "unless you are requiring reps attach a deal to each space, it's possible this "
        "report is missing data."
    ),
    "type_step": (
        "To populate this report, remember to set the workspace type for your sales "
        "workspaces from the Settings in the workspace's Internal tab."
    ),
}


INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "pipeline-touched-vs-active",
        "topic": "the difference between 'total pipeline touched' and 'active pipeline'",
        "basis": (
            "The research names both tiles and defines neither. It gives one formula "
            "exactly (close rate) and no formula for either money tile, so the "
            "relationship between them is ours to set."
        ),
        "value": {
            "total_pipeline_touched": "the summed amount of every in-scope deal, closed included",
            "active_pipeline": "the summed amount of the in-scope deals that have not closed",
            "active_deals": "the count of that same open subset",
        },
        "why": (
            "The two tiles are adjacent in the researched list and say 'touched' and "
            "'active' respectively. If both were the open subset they would be the same "
            "number and the report would not carry two tiles for it; 'touched' has to "
            "mean the whole population the room reached for the pair to mean anything."
        ),
        "change_it": "tiles() in dsr/salesimpact/rollup.py, and the two inferences below it.",
        "blast_radius": "Two of the eight tiles, and the deals-by-owner panel's amount column.",
    },
    {
        "id": "close-rate-over-deals",
        "topic": "what the researched close-rate fraction counts",
        "basis": (
            "The quote says 'How many workspaces with deals/opportunities that have been "
            "closed won, divided by the total (closed won + closed lost)'. The "
            "denominator is unambiguous - closed won plus closed lost, open deals in "
            "neither arm. The numerator's unit is stated as workspaces, and the tiles "
            "around it are counts of deals."
        ),
        "value": {
            "numerator": "in-scope deals whose stage classifies as closed-won",
            "denominator": "in-scope deals whose stage classifies as closed-won or closed-lost",
            "excluded": "deals in an open or unclassifiable stage",
            "when_nothing_has_closed": None,
        },
        "why": (
            "A workspace can carry more than one deal, so the two readings diverge there. "
            "Counting deals is the more precise measure of the same population and keeps "
            "the rate reconcilable with the funnel panel, which groups by stage over the "
            "same deals. The researched denominator is preserved exactly: open deals are "
            "in neither arm."
        ),
        "change_it": "tiles() in dsr/salesimpact/rollup.py.",
        "blast_radius": "The close-rate tile and its reconciliation against the funnel.",
    },
    {
        "id": "undefined-is-null-not-zero",
        "topic": "what a ratio with an empty denominator reports",
        "basis": (
            "The research gives the close rate as a fraction and does not say what it "
            "reports when no deal has closed. Its own vocabulary treats an absent value "
            "as absent: a buyer with no invite 'shows up by their email' rather than as a "
            "blank row."
        ),
        "value": {
            "close_rate_with_no_closed_deals": None,
            "days_to_close_with_no_closed_deals": None,
        },
        "why": (
            "0/0 is not 0%. Reporting 0% asserts that every deal was lost, which is a "
            "different and wrong claim, and a leadership report is read as a claim. null "
            "reads as 'not yet', which is what it is."
        ),
        "change_it": "tiles() in dsr/salesimpact/rollup.py.",
        "blast_radius": "The close-rate and days-to-close tiles, and the page's empty treatment for them.",
    },
    {
        "id": "days-to-close-measurement",
        "topic": "how the days-to-close average is measured",
        "basis": (
            "The research names 'Days to close (average)' and defines neither its start "
            "point nor its population. It does separately name a 'Deals Created Over "
            "Time' panel, so a deal's own creation date is a date the report already "
            "shows."
        ),
        "value": {
            "start": "the deal's created date",
            "end": "the deal's closed or close date",
            "population": "every in-scope deal classified closed-won or closed-lost",
            "excluded": "a close date earlier than the created date, reported in data_warnings",
            "when_nothing_has_closed": None,
        },
        "why": (
            "Pairing the interval with the panel the report already publishes fixes the "
            "start at the only date a reader can see the start of. Won and lost both, "
            "because 'days to close' is a cycle length and the researched close rate "
            "already treats both as closed. A backwards interval is a data error, and a "
            "negative day in an average is arithmetic swallowing an error rather than a "
            "finding - so it is excluded and named."
        ),
        "change_it": "days_between() and tiles() in dsr/salesimpact/rollup.py.",
        "blast_radius": "The days-to-close tile, and the data_warnings a reader sees beside it.",
    },
    {
        "id": "money-currency",
        "topic": "which currency the money tiles are summed in",
        "basis": (
            "The research never mentions currency. It names 'Revenue (closed won "
            "revenue)' and 'Active pipeline' as sums and nothing about the unit they are "
            "summed in."
        ),
        "value": {
            "report_currency": "the currency on the most in-scope deals; ties broken lexicographically",
            "deal_with_no_currency": "joins the report currency",
            "money_split": "every figure is also returned per currency under 'money'",
            "mixed_currency_warning": "a warning row naming the split, and the figure on every money tile",
        },
        "why": (
            "A single Revenue number that silently adds EUR to USD is worse than no "
            "number, because nobody knows to distrust it. Choosing one currency, saying so "
            "on every figure, and returning the split keeps the tile usable without making "
            "it false. This is a correctness guard on a researched tile, not a new "
            "requirement."
        ),
        "change_it": "choose_currency() and money_by_currency() in dsr/salesimpact/rollup.py.",
        "blast_radius": "Four money figures, the per-currency split, and one warning row.",
    },
    {
        "id": "engagement-is-scoped",
        "topic": "which rooms the buyer-engagement counts cover",
        "basis": (
            "The research says the report 'combines your CRM data with Dock workspace "
            "data to show the impact Dock has on your pipeline and close rates'. The "
            "inclusion rule it gives is about Sales-typed workspaces with a CRM "
            "opportunity; it does not restate the rule for the engagement half."
        ),
        "value": {
            "counted": "buyer events in in-scope workspaces only",
            "not_counted": "buyer events in a workspace that is untyped or has no deal attached",
            "average_buyers_per_workspace_divides_by": "every in-scope workspace, not only the ones with engagement",
        },
        "why": (
            "Without this, Buyer Views answers a different question than the Revenue tile "
            "beside it, and the report stops being a join. Dividing by only the rooms that "
            "were touched is the specific way to make a thinly engaged pipeline look "
            "densely engaged."
        ),
        "change_it": "The in_scope_ids filter in report() in dsr/salesimpact/rollup.py.",
        "blast_radius": "Buyer views, buyer actions, unique buyers, average buyers per workspace, most engaged buyers.",
    },
    {
        "id": "actions-include-views",
        "topic": "whether an action count includes a view count",
        "basis": (
            "The research's insights list names 'Views, actions, and average buyers per "
            "workspace' without defining the relationship. The same research corpus "
            "glosses a client action as 'how many times a client has interacted with a "
            "space ... clicking into pages, embedded content', and clicking into a page is "
            "a view."
        ),
        "value": {
            "buyer_actions": "every buyer event in scope, views included",
            "buyer_views": "only the events whose action classifies as a view",
            "view_actions": sorted(VIEW_ACTIONS),
        },
        "why": (
            "Two counters that silently differ by an unexplainable number are worse than "
            "two counters with one stated relationship. The action gloss settles it: "
            "interacting with a space includes opening one of its pages."
        ),
        "change_it": "VIEW_ACTIONS in dsr/salesimpact/vocabulary.py, and engagement() in rollup.py.",
        "blast_radius": "The two engagement tiles and the most-engaged-buyer ranking.",
    },
    {
        "id": "unknown-stage-is-a-bucket",
        "topic": "what a stage this build cannot classify does",
        "basis": (
            "The research gives stage names from two CRMs and defines the close rate over "
            "closed won and closed lost. It does not say what happens to a stage outside "
            "both sets, and a stage string is a third party's data."
        ),
        "value": {
            "class": "unknown",
            "counted_as": "an active deal",
            "in_the_funnel": True,
            "in_the_close_rate_denominator": False,
            "stored": "verbatim, never refused",
        },
        "why": (
            "Refusing to store a team's own stage string is a migration by the back door, "
            "and schema flexibility is a hard requirement of this product. Bucketing it "
            "keeps the researched fraction exact - closed won over closed won plus closed "
            "lost - and the funnel shows the bucket so it cannot hide."
        ),
        "change_it": "classify_stage() in dsr/salesimpact/vocabulary.py, or fields.stage in the config record.",
        "blast_radius": "The active-deal count, the funnel, and the close rate's denominator.",
    },
    {
        "id": "lost-before-won",
        "topic": "the order the two closed sets are matched in",
        "basis": (
            "The research quotes a stage vocabulary from Salesforce and HubSpot without "
            "giving a matching rule, so both the match levels and their order are ours."
        ),
        "value": {
            "levels": ["exact", "startswith", "substring"],
            "order": "term-major: every level against the lost set, then every level against the won set",
            "won_stages": sorted(WON_STAGES),
            "lost_stages": sorted(LOST_STAGES),
        },
        "why": (
            "'won/lost' is a label a CRM really does use, and it has to land on lost. "
            "Normalised it is 'won lost', so a prefix match for 'won' would claim it - "
            "putting a lost deal into Revenue - while 'lost' matches it only as a "
            "substring. Trying the whole lost set first lets the more specific signal "
            "beat the more general one. Level-major ordering was the first attempt and is "
            "wrong for exactly this reason; it is the one place where the obvious reading "
            "of 'lost first' does not achieve it."
        ),
        "change_it": "classify_stage() in dsr/salesimpact/vocabulary.py.",
        "blast_radius": "Every classification, and therefore four of the eight tiles.",
    },
    {
        "id": "owner-fallback",
        "topic": "whose name a deal with no owner is reported under",
        "basis": (
            "The research names 'Deals By Owner' as a panel and lists owner among the "
            "CRM fields, without saying what a deal that carries no owner reports as."
        ),
        "value": {
            "resolution": "the deal's own owner, otherwise its workspace's owner",
            "owner_source": ["deal", "room", "unassigned"],
            "reported_per_row": True,
        },
        "why": (
            "A panel where half the rows read 'unassigned' because a payload omitted the "
            "field is unreadable, and a borrowed owner that does not say it was borrowed "
            "is a lie. Reporting the provenance is what keeps the panel honest while still "
            "being readable."
        ),
        "change_it": "classify_deal() in dsr/salesimpact/rollup.py.",
        "blast_radius": "The owner filter, the deals-by-owner panel, and the owner filter's hit count.",
    },
    {
        "id": "date-range-ranges-two-dates",
        "topic": "what a single date range ranges over",
        "basis": (
            "The research names a date range filter once and does not say what it ranges "
            "over. A deal has a creation date; a buyer event has a time it happened."
        ),
        "value": {
            "deal_side": "the deal's created date",
            "engagement_side": "the event's occurred-at",
            "undated_row": "kept in every total, absent from every bucket, named in data_warnings",
        },
        "why": (
            "One range over two populations means each side ranges on the date that means "
            "something for it. Dropping an undated row would quietly shrink a total, and a "
            "total that shrinks for a reason nobody can see is exactly the failure the "
            "coverage panel exists to prevent."
        ),
        "change_it": "ReportFilter.in_date_range in dsr/salesimpact/filters.py, and the report loop in rollup.py.",
        "blast_radius": "Every figure, the two time series, and the data_warnings list.",
    },
    {
        "id": "series-bucketing",
        "topic": "the granularity of the two 'over time' panels",
        "basis": (
            "The research names 'Deals Created Over Time' and 'Buyer Views Over Time' and "
            "defines neither a granularity nor a default range."
        ),
        "value": {
            "buckets": ["day", "week", "month"],
            "default": "day",
            "week_starts_on": "Monday, matching ISO-8601",
            "labelled_by": "the first date of the bucket, so labels sort as plain strings",
            "beyond_ten_years": "an empty list rather than thousands of rows",
        },
        "why": (
            "Day is the default because it is the finest reading of 'over time' and so "
            "loses nothing; a caller with a six-month range asks for weeks, because a "
            "chart of 180 daily bars is not a chart anybody reads. Weeks start on Monday "
            "so a label does not depend on the locale of whoever rendered it, and buckets "
            "are labelled by a real date so a reader can check them against a calendar."
        ),
        "change_it": "BUCKETS in dsr/salesimpact/filters.py, and the default on ReportFilter.bucket.",
        "blast_radius": "The length and shape of both time series, and nothing else.",
    },
    {
        "id": "incomplete-is-reported-not-refused",
        "topic": "what the report does when the data is incomplete",
        "basis": (
            "The research says the report is 'incomplete' without an attached deal, and "
            "that a workspace's type must be set for it to appear. It does not say the "
            "report refuses, and it does not say the report hides anything."
        ),
        "value": {
            "refuses": False,
            "complete_flag": True,
            "coverage_panel": "names every Sales-typed workspace with no deal, and every workspace that is not Sales-typed",
            "complete_requires": "the CRM integration on and every Sales-typed workspace carrying a deal",
        },
        "why": (
            "A refusal would hide the very rooms a reader needs in order to fix the "
            "report, and the researched failure mode is a number that is quietly too "
            "small rather than an error. Naming the rooms is the only version of this that "
            "somebody can act on."
        ),
        "change_it": "coverage() in dsr/salesimpact/rollup.py, and the coverage route in the feature module.",
        "blast_radius": "The coverage panel, the complete flag, and the banner the page shows above the tiles.",
    },
    {
        "id": "duplicate-deal-is-a-conflict",
        "topic": "what re-registering a CRM deal id does",
        "basis": (
            "The research says stage and amount change by sync, which is an update to an "
            "existing deal. It does not say what happens when the same deal is registered "
            "twice."
        ),
        "value": {
            "second_post": "409 with the existing record id and its workspace",
            "instead_of": "a second row counting the same money twice in every rollup",
        },
        "why": (
            "A mistyped id, or a client that re-posts instead of patching, would otherwise "
            "double a deal's amount into Revenue and Active pipeline - and the resulting "
            "report would be wrong in the direction a leadership reader trusts. Returning "
            "the existing id makes the fix a single PATCH."
        ),
        "change_it": "DealBook.create_deal in dsr/salesimpact/deals.py, and the DealConflict handler.",
        "blast_radius": "POST /deals only.",
    },
    {
        "id": "room-scoped-report-is-not-a-404",
        "topic": "what a room-scoped read returns for a workspace that is simply out of scope",
        "basis": (
            "The research makes setting the workspace type the user's first step, and the "
            "inclusion rule then depends on it. So the most likely reason to open a room's "
            "impact is 'why is mine not in the numbers'."
        ),
        "value": {
            "unknown_room": "404 unknown_workspace",
            "existing_room_out_of_scope": "200 with in_scope false and a reason of not_sales or no_deal",
        },
        "why": (
            "A 404 says 'no such room', which is a different and wrong answer, and it "
            "would be the answer in the case a reader is most likely to hit. The reason is "
            "the product here; not having it is the defect."
        ),
        "change_it": "room_report() in dsr/salesimpact/rollup.py and the room route in the feature module.",
        "blast_radius": "GET /report/rooms/{room_id} and the room drill-in on the page.",
    },
)


def by_id(inference_id: str) -> dict[str, Any] | None:
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return entry
    return None


def describe() -> dict[str, Any]:
    """The whole registry, beside the sourced half it is contrasted against.

    Both halves in one payload on purpose. The point of the endpoint is that a reader can
    see where the line falls, and that means showing what was quoted next to what was
    chosen rather than only the latter.
    """
    return {
        "count": len(INFERENCES),
        "sourced_quotes": dict(SOURCED_QUOTES),
        "sourced": {
            "stage_classes": list(STAGE_CLASSES),
            "won_stages": sorted(WON_STAGES),
            "lost_stages": sorted(LOST_STAGES),
            "close_rate_formula": "closed_won / (closed_won + closed_lost)",
        },
        "inferences": [dict(entry) for entry in INFERENCES],
    }
