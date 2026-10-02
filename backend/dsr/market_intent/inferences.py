"""Every judgement call this workflow rests on, named, and served.

The research is a vendor's help article. It is precise about the things a seller
sees - the filter names, the four stock auto-add categories, the 90-day ceiling,
the 10-credit charge - and silent about almost everything else: how a root
domain is found, which midnight the ninety days are counted from, what a
research topic matches on, when a billing period starts.

Those edges are where a build goes wrong quietly, so each one is written down
here with the reading taken, the reason, and what it would take to change it.
They are served over HTTP by ``GET /api/wf-033/inferences``, beside the sourced
half, because the point of the endpoint is to see where the line falls - not
for a reader to reconstruct from a diff.
"""

from __future__ import annotations

from typing import Any

#: Each entry: the decision, the reading taken, why, and the change that would
#: reverse it. ``inference: True`` on all of them, which is the point.
INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "public_suffix_list",
        "question": "How is a root domain found from a host?",
        "reading": (
            "The registrable domain is the last two labels, or the last three when the last two "
            "form one of 47 known second-level suffixes (co.uk, com.au, co.jp, ...). No "
            "public-suffix dependency is taken."
        ),
        "why": (
            "A dependency here would be a new shared thing every feature would eventually want, "
            "and a list that is one entry short costs a merge that should not have happened "
            "while a list with one wrong entry merges two companies."
        ),
        "change": "Replace MULTI_LABEL_SUFFIXES in dsr/market_intent/domains.py.",
        "risk": (
            "A domain under a ccTLD not on the list is not rolled up, so two hosts of one company "
            "read as two companies. Never the reverse."
        ),
    },
    {
        "id": "ip_is_not_a_domain",
        "question": "What is the root domain of a bare IP address?",
        "reading": "The address itself, flagged is_ip, and never reduced to its last two labels.",
        "why": (
            "The research says buyer intent 'connects anonymous web visitors to known companies' "
            "IP addresses', so an address reaches the domain model. Reduced naively, 203.0.113.7 "
            "becomes 113.7 and every visitor behind one address becomes one company."
        ),
        "change": "Remove the is_ip branch in dsr/market_intent/domains.py.",
        "risk": "None; the branch only ever prevents a wrong answer.",
    },
    {
        "id": "www_truncation_depth",
        "question": "How many levels of 'www' does 'truncates www for display purposes' remove?",
        "reading": (
            "All of them, and it comes for free: www is a subdomain label, so reducing "
            "www.a.com - or www.www.a.com - to its registrable domain drops every www on the way. "
            "The display value is the root, which never has one."
        ),
        "why": (
            "The research says the model 'truncates www for display purposes' and that subdomains "
            "roll up into the root, and those two statements together already remove any number of "
            "www labels. A separate special case would be a second rule for something the first one "
            "covers, and the two could disagree."
        ),
        "change": "root_domain in dsr/market_intent/domains.py.",
        "risk": (
            "A host whose registrable domain is literally 'www' would reduce to nothing useful. "
            "Such a domain cannot be registered, so the case does not arise."
        ),
    },
    {
        "id": "midnight_utc_window_boundary",
        "question": "Which midnight does the 90-day limit count from?",
        "reading": (
            "The earliest start any window may have is midnight UTC of the day 90 days before "
            "the request, so days=90 always fits and days=91 never does."
        ),
        "why": (
            "The other reading - a rolling ninety times twenty-four hours - would refuse the "
            "documented maximum for every request after 00:00 UTC, which makes 'you can only set "
            "timeframes within the last 90 days' false for ninety days a year."
        ),
        "change": "resolve_window in dsr/market_intent/timeframe.py.",
        "risk": (
            "A window is up to 24 hours longer than days suggests. Both ends are snapped to "
            "midnight, so the boundary is observable and a caller can see it in the response."
        ),
    },
    {
        "id": "explicit_start_is_snapped",
        "question": "Is a caller-supplied start time honoured exactly, or snapped to midnight?",
        "reading": "Snapped down to midnight UTC, and the response says the value it moved from.",
        "why": (
            "'This timeframe is based on midnight UTC' is the vendor's own statement about where "
            "the boundary is. Honouring 09:15 would make the stated basis a lie for every "
            "caller who supplies a time."
        ),
        "change": "resolve_window in dsr/market_intent/timeframe.py.",
        "risk": "A window can be up to 24 hours wider than asked for; snapped_from reports it.",
    },
    {
        "id": "window_endpoints_inclusive",
        "question": "Is a visit exactly on the start or end line inside the window?",
        "reading": "Both ends inclusive.",
        "why": (
            "A company whose last visit is exactly on the line the window is drawn from has "
            "demonstrated intent on that line. Excluding it would make the boundary an observable "
            "behaviour of the product with no rule behind it."
        ),
        "change": "Window.contains in dsr/market_intent/timeframe.py.",
        "risk": "One company, at one timestamp, on one midnight line.",
    },
    {
        "id": "naive_timestamps_refused",
        "question": "What happens to a timestamp with no timezone?",
        "reading": "Refused, with a message that says why.",
        "why": (
            "The window is drawn on midnight-UTC lines, so assuming a timezone for a naive "
            "timestamp would put a company in or out of a view on a difference of hours that "
            "nobody declared."
        ),
        "change": "as_utc in dsr/market_intent/timeframe.py.",
        "risk": "A caller sending local times is refused rather than silently shifted.",
    },
    {
        "id": "default_timeframe",
        "question": "What time frame does a view get when the request names none?",
        "reading": 'Thirty days, and an explicit "days": null asks for no time frame at all.',
        "why": (
            "The research names a 90-day ceiling and no default. Thirty days is short enough that "
            "a company that has genuinely gone quiet falls out of a view instead of being tracked "
            "forever."
        ),
        "change": "DEFAULT_DAYS in dsr/market_intent/timeframe.py.",
        "risk": "A view with no explicit time frame is narrower than a seller may expect.",
    },
    {
        "id": "path_filters_are_case_sensitive",
        "question": "Do the five path filters compare case-sensitively?",
        "reading": "Yes. All five compare exactly as written.",
        "why": (
            "The match is what gets tagged with Intent, and a filter that matched a differently "
            "cased path would tag a page the visitor never saw."
        ),
        "change": "_match_path in dsr/market_intent/criteria.py.",
        "risk": "A filter written /Pricing will not catch /pricing.",
    },
    {
        "id": "criteria_clauses_are_ored",
        "question": "How are several page filters in one criterion combined?",
        "reading": "Any of them. Across criteria the rule is also any: any criterion met is intent.",
        "why": (
            "A criterion describes a set of pages that mean the same thing - /pricing and "
            "/contact-sales both mean 'ready to talk' - and a product that allowed one page per "
            "criterion would make the feature useless."
        ),
        "change": "Criterion.matches in dsr/market_intent/criteria.py.",
        "risk": "A criterion cannot express 'this page but not that one'.",
    },
    {
        "id": "intent_tag_is_computed",
        "question": "Is the Intent tag written on a page view when it arrives?",
        "reading": (
            "No. It is recomputed on every read against the current criteria, and nothing derived "
            "is stored."
        ),
        "why": (
            "The research says a custom intent property 'will automatically update to reflect "
            "whether an existing company meets or no longer meets' the criteria. A tag written at "
            "ingest would leave yesterday's visits untagged by a criterion added today, and would "
            "never be removed when one is withdrawn."
        ),
        "change": "qualify_view in dsr/market_intent/criteria.py, called from table._row_for.",
        "risk": "The table costs a criteria pass per read; there is no cache to go stale.",
    },
    {
        "id": "viewer_intent_off_is_unconstrained",
        "question": "What does 'Showing visitor intent' mean when it is off?",
        "reading": "Unconstrained - do not narrow by intent - not 'only companies with no intent'.",
        "why": (
            "It is a switch on a filter panel. Reading it as the negation would make a view the "
            "user turned the filter *off* on return exactly the companies they were filtering out."
        ),
        "change": "FilterSet.visitor_intent in dsr/market_intent/views.py.",
        "risk": "None; the negation reading is available by inverting the filter in a client.",
    },
    {
        "id": "website_only_views",
        "question": "Can a company known only from a topic match satisfy a saved view?",
        "reading": (
            "Only when the view has no page filter and no traffic-source filter. Every researched "
            "path filter is a site path and a research observation has none."
        ),
        "why": (
            "A view built on 'Specific page views' is by construction about the seller's own site, "
            "and a company that has never visited has no page to be equal to."
        ),
        "change": "FilterSet.is_website_only in dsr/market_intent/views.py.",
        "risk": "A research-only company is invisible to any path-filtered view, by design.",
    },
    {
        "id": "research_intent_includes_news",
        "question": "Does a company news signal set research intent?",
        "reading": (
            "Yes. The research groups them: 'broader intent signals beyond your website, such as "
            "companies researching topics across the web or company news'. research_evidence says "
            "which kind each one was."
        ),
        "why": (
            "Leaving news out would drop a researched requirement - the four stock categories "
            "count research intent, and a company that just raised money is in-market whether or "
            "not it also read about a topic. Splitting the two out on the Overview keeps a "
            "funding round from being reported as topic research."
        ),
        "change": "table._row_for, research_intent.",
        "risk": "A buyer count includes companies whose only signal was news.",
    },
    {
        "id": "research_topic_matching",
        "question": "How is a topic observation matched to a configured research topic?",
        "reading": "Case-insensitive substring over the topic's terms; a topic with no terms matches its own name.",
        "why": (
            "The research says 'set up research topics' and never says how one is matched. "
            "Substring is what makes a term list useful - 'cspm' finding 'evaluating cspm "
            "vendors' is the case the feature exists for."
        ),
        "change": "mark_topics in dsr/market_intent/observations.py.",
        "risk": "A short term can match more than intended; terms are the seller's to choose.",
    },
    {
        "id": "billing_period_is_a_utc_month",
        "question": "What is 'the same billing period'?",
        "reading": "The UTC calendar month, as YYYY-MM.",
        "why": (
            "The research says 'the same billing period' and 'charged monthly' without defining "
            "one, and the only time boundary it names anywhere is midnight UTC. A UTC calendar "
            "month is the reading consistent with the rest of the specification."
        ),
        "change": "period_for in dsr/market_intent/credits.py.",
        "risk": "A seller billed on the 15th gets a boundary on the 1st here.",
    },
    {
        "id": "both_actions_cost_ten",
        "question": "Does adding a company cost credits as well as tracking it?",
        "reading": (
            "Yes: 10 for an add, 10 for a track, and 10 once for both in the same period with 10 "
            "recorded as waived."
        ),
        "why": (
            "The research prices tracking at 10 and then says the combined case costs 10 'not for "
            "both actions separately', which only parses if the add is priced too."
        ),
        "change": "CREDIT_COST_ADD and CREDIT_COST_TRACK in dsr/market_intent/vocabulary.py.",
        "risk": "None; the ledger records both the charge and the saving.",
    },
    {
        "id": "tracking_uses_the_same_watermark",
        "question": "Does the auto-add watermark also gate tracking?",
        "reading": (
            "Yes. Tracking a company also requires it to have entered the view after the toggle "
            "was switched on."
        ),
        "why": (
            "The research states the watermark for auto-add only. Applying it to tracking too is "
            "inferred from the billing rule: charging 10 credits a month for companies that "
            "entered before anyone switched tracking on would be charging for a decision nobody "
            "made."
        ),
        "change": "MarketIntentEngine.run in dsr/market_intent/engine.py.",
        "risk": (
            "If this is wrong, tracking is strictly later to start than the research requires. "
            "The per-company outcome in the run response says which companies were held back and "
            "why, so it is visible rather than silent."
        ),
    },
    {
        "id": "derived_property_is_the_trailing_run",
        "question": "Does a derived intent property mean 'ever matched' or 'matches now'?",
        "reading": (
            "Matches now: the property is true while the company's most recent page view "
            "qualifies, and false the moment its newest one does not."
        ),
        "why": (
            "The research says the property reflects whether a company 'meets or no longer meets' "
            "the criteria. 'Ever matched' cannot express the second half, so the property would be "
            "stuck on true for every company that ever read the page - which is the exact sentence a "
            "seller would read and believe. The rule is the same trailing run the saved-view entry "
            "time uses, so there is one rule rather than two."
        ),
        "change": "derived_properties in dsr/market_intent/criteria.py.",
        "risk": (
            "The row's visitor_intent answers the other question - 'has this company shown intent' - "
            "and stays true once it has. A company can therefore be a visitor-intent company whose "
            "showing_smb_intent is false, which is the state the demo seeds on purpose."
        ),
    },
    {
        "id": "category_entry_is_last_seen",
        "question": "What does a stock category compare against its watermark?",
        "reading": (
            "The company's most recent activity of any kind, last_seen_at, rather than a trailing "
            "run of observations the way a saved view does it."
        ),
        "why": (
            "The two answer different questions. A view asks 'has this company been continuously "
            "in this filter since?', which a trailing run answers exactly. A stock category asks "
            "'is this company in market now?', which is an attribute re-evaluated from the "
            "current criteria, and it has no filter set to be continuously inside of. The "
            "consequence is visible and deliberate: a company the view automation held back can "
            "still be added by a stock category, because the two disagree about when it arrived."
        ),
        "change": "MarketIntentEngine.run_category in dsr/market_intent/engine.py.",
        "risk": (
            "The same company can be reported as 'entered before auto-add was enabled' by a view "
            "and added by a category. Both answers are correct for their own question, and the "
            "run responses say which rule each one used."
        ),
    },
    {
        "id": "entry_time_is_derived_not_observed",
        "question": "When did a company enter a view?",
        "reading": (
            "The timestamp of the oldest observation in the trailing run of qualifying "
            "observations, walked from the newest backwards."
        ),
        "why": (
            "Stamping the entry at run time breaks the researched note in the case that matters "
            "most: a company that entered last month, before the automation was switched on, "
            "would be stamped today, be after the switch-on time, and be auto-added - exactly what "
            "'It will not add all existing companies in your saved views' says will not happen."
        ),
        "change": "entered_at in dsr/market_intent/views.py.",
        "risk": "None; the derivation is exact given the stored observations.",
    },
    {
        "id": "watermark_is_strict",
        "question": "Does an observation exactly at the switch-on time count as 'after enabling'?",
        "reading": "No. Strictly after.",
        "why": (
            "'auto-add will only add companies that enter your saved views after enabling the "
            "auto-add'. Ties are the switch-on instant itself, and a company already qualifying at "
            "that instant is one of the 'existing companies' the research says will not be added."
        ),
        "change": "The comparison in MarketIntentEngine.run.",
        "risk": "One company, at one instant.",
    },
    {
        "id": "category_target_market_asymmetry",
        "question": "Does 'in-CRM with visitor intent' require a target market?",
        "reading": "No. The other three require one; this one does not.",
        "why": (
            "The research's own wording is not uniform: 'companies that are in your target markets "
            "and visiting high-intent pages' names the market for the net-new categories, and "
            "'in-CRM with visitor intent' does not. Adding the requirement would silently stop "
            "tracking every existing customer who visits."
        ),
        "change": "CATEGORY_REQUIREMENTS in dsr/market_intent/vocabulary.py.",
        "risk": "If the asymmetry is wrong, one category over-matches; the per-company outcome "
        "shows which category matched.",
    },
    {
        "id": "lifecycle_stage_order",
        "question": "How is 'lifecyclestage is forward-only' checked without a vocabulary?",
        "reading": (
            "Against the CRM's own default progression, listed explicitly. A stage the product "
            "does not know the position of is allowed through and reported as unchecked."
        ),
        "why": (
            "The rule is sourced but the order is not, and a team adding a stage must not need "
            "coordination. Refusing a change the product cannot order would make an unfamiliar "
            "stage unwriteable."
        ),
        "change": "LIFECYCLE_STAGE_ORDER in dsr/market_intent/vocabulary.py.",
        "risk": "An unknown stage cannot be shown to be forward-only; the response says so.",
    },
    {
        "id": "enrichment_permission_is_a_granted_set",
        "question": "How is 'Super Admin must assign users with Data enrichment permissions' modelled?",
        "reading": (
            "The portal holds a list of actors carrying the permission, changed through the "
            "settings route, and the three signal-driven CRM writes check it."
        ),
        "why": (
            "The research says a Super Admin *assigns* the permission to users, so the actor "
            "doing the work is an assignee rather than an administrator. This product's own "
            "dsr/permissions.py is a document-library role model with no such tier, and borrowing "
            "it would gate a different thing."
        ),
        "change": "The settings route in dsr/market_intent/engine.py.",
        "risk": "The gate is a list, so it is as strong as the route that writes it; that route is "
        "itself a write and therefore audited.",
    },
    {
        "id": "no_outbound_crm_client",
        "question": "Where do the cited CRM API calls go?",
        "reading": (
            "Nowhere. The research notes its article is UI-first and documents no public "
            "buyer-intent REST endpoint, and the CRM primitives it lists are recorded as a "
            "documented outbound plan on each added company rather than called."
        ),
        "why": (
            "Calling them needs credentials this workflow does not have, and a network call in a "
            "route that must be auditable and deterministic would make the audit row depend on a "
            "third party."
        ),
        "change": "The crm_plan field written by MarketIntentEngine._add_company.",
        "risk": "None in this build; the plan is data, so wiring it up later is additive.",
    },
    {
        "id": "one_row_per_company",
        "question": "Is a company added to the CRM once, or once per qualifying signal?",
        "reading": (
            "Once. A second qualifying signal for an already-added company updates its derived "
            "properties and its CRM fields, and is reported as already_added."
        ),
        "why": (
            "'When adding a company to your CRM from buyer intent, the company will have a Record "
            "source property value of Buyer-Intent' - one company, one record, one credit."
        ),
        "change": "MarketIntentEngine._add_company.",
        "risk": "None; the second signal is still reported.",
    },
    {
        "id": "table_is_computed",
        "question": "Are the table's columns stored aggregates?",
        "reading": (
            "No. Every column is computed from the visits and research observations behind it on "
            "each read, bounded by a scan limit that is reported when it bites."
        ),
        "why": (
            "A cached aggregate is a number that can disagree with the rows behind it, and there "
            "is no way to tell which one is lying. The cost is a bounded read per page load."
        ),
        "change": "table.build_rows.",
        "risk": (
            "Past SCAN_LIMIT visits a table's totals understate reality. The response says so "
            "rather than reporting a number that looks complete."
        ),
    },
    {
        "id": "views_are_portal_scoped",
        "question": "Are saved views, markets, and criteria scoped to a room?",
        "reading": "No. They are portal-scoped, and no route under this prefix takes a room id.",
        "why": (
            "The research has no room concept: a target market, an intent criterion, a research "
            "topic, an exclusion list, and a saved view are all seller settings, and the stock "
            "auto-add categories run over the whole account. Scoping them to a room would make the "
            "same view name registerable twice and would imply a view only sees one room's "
            "buyers, which the research never says."
        ),
        "change": "The router in dsr/features/wf033_auto_add_and_continuously_track_in_mar.py.",
        "risk": (
            "A product that later wants per-room intent configuration would need new routes. "
            "Adding them is additive; retrofitting a scope onto these is not."
        ),
    },
)


def by_id(identifier: str) -> dict[str, Any] | None:
    """One inference by its id, or ``None``."""
    for entry in INFERENCES:
        if entry["id"] == identifier:
            return entry
    return None


def describe() -> dict[str, Any]:
    """Every inference, for ``GET /inferences``."""
    return {
        "count": len(INFERENCES),
        "note": (
            "Each entry is a decision the research does not make. The sourced half of this "
            "workflow is the filter vocabulary, the four stock categories, the 90-day ceiling, "
            "the 10-credit charge, and the record-source value; everything below is where it "
            "stops being sourced."
        ),
        "inferences": [dict(entry) for entry in INFERENCES],
    }
