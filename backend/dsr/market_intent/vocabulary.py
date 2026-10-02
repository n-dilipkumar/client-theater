"""Every published vocabulary this workflow accepts, served as data.

A client renders its pickers from :meth:`describe` rather than from a list
compiled into the page, so a value added here reaches every client at once and
cannot drift. Each entry says where it comes from, because several of these are
the research's own words and a reader should be able to check them.

The one entry that is *not* a closed list is deliberately open: the HubSpot CRM
filter is "Lifecycle stage, Deal Stage, Owner", and the research names no
vocabulary for any of the three. See :data:`LIFECYCLE_STAGE_ORDER` for how a
forward-only comparison is made when a team adds a stage the product has never
heard of.
"""

from __future__ import annotations

from typing import Any

from dsr.market_intent.criteria import PATH_OPERATOR_LABELS, PATH_OPERATORS

#: "When adding a company to your CRM from buyer intent, the company will have a
#: `Record source` property value of `Buyer-Intent`." Quoted in the research
#: including the hyphen, so it is a constant rather than a string built from a
#: label: a record source that rendered as "Buyer Intent" would not match what
#: the vendor writes into the property.
RECORD_SOURCE_BUYER_INTENT = "Buyer-Intent"

#: "In the left sidebar, under Automations, click Add new companies or Track
#: intent signals". The two toggles, as the product stores them.
AUTOMATION_ADD = "add_new_companies"
AUTOMATION_TRACK = "track_intent_signals"
AUTOMATION_TOGGLES: tuple[str, ...] = (AUTOMATION_ADD, AUTOMATION_TRACK)

AUTOMATION_LABELS: dict[str, str] = {
    AUTOMATION_ADD: "Add new companies",
    AUTOMATION_TRACK: "Track intent signals",
}

#: "Sort by Page views, Unique visitors, or Last visit (asc/desc)." Three keys,
#: and the direction is a separate choice rather than a second set of keys.
SORT_KEYS: tuple[str, ...] = ("page_views", "unique_visitors", "last_visit")

SORT_LABELS: dict[str, str] = {
    "page_views": "Page views",
    "unique_visitors": "Unique visitors",
    "last_visit": "Last visit",
}

DIRECTIONS: tuple[str, ...] = ("asc", "desc")

#: "Traffic source" is one of the researched filters, with no values attached to
#: it anywhere in the specification. These are the vendor's own default channels
#: and are an inference; see :mod:`dsr.market_intent.inferences`.
TRAFFIC_SOURCES: tuple[str, ...] = (
    "direct",
    "email",
    "organic_search",
    "paid_search",
    "paid_social",
    "referral",
    "social",
    "other",
)

TRAFFIC_SOURCE_LABELS: dict[str, str] = {
    "direct": "Direct",
    "email": "Email",
    "organic_search": "Organic search",
    "paid_search": "Paid search",
    "paid_social": "Paid social",
    "referral": "Referral",
    "social": "Social",
    "other": "Other",
}

#: "company news like funding, executive hires, layoffs, product launches, and
#: mergers" - the research's own enumeration, in its order.
NEWS_SIGNAL_TYPES: tuple[str, ...] = (
    "funding",
    "executive_hire",
    "layoff",
    "product_launch",
    "merger",
)

NEWS_SIGNAL_LABELS: dict[str, str] = {
    "funding": "Funding",
    "executive_hire": "Executive hire",
    "layoff": "Layoffs",
    "product_launch": "Product launch",
    "merger": "Merger",
}

#: What the "Research" tab's other half holds. The research says a company row
#: in that tab is either a topic match or a news signal, and the two are counted
#: separately on the Overview so a company researching your category is not
#: reported as a company that just raised money.
RESEARCH_KINDS: tuple[str, ...] = ("topic", "news")

#: ``lifecyclestage is forward-only`` - the research quotes this off the CRM API
#: it cites rather than off the buyer-intent page, so it is the one forward-only
#: rule in this workflow that has a primary source behind it.
#:
#: Inference: the order below is the CRM's own default progression. The research
#: names no order, and a wrong entry costs a stage its position rather than
#: breaking a rule, so the list is data a team can correct.
LIFECYCLE_STAGE_ORDER: tuple[str, ...] = (
    "subscriber",
    "lead",
    "marketing_qualified_lead",
    "sales_qualified_lead",
    "opportunity",
    "customer",
    "evangelist",
    "other",
)

#: "Tracking a company costs 10 credits. However, if a company is added and
#: tracked in the same billing period, you're only charged once for tracking (10
#: credits) - not for both actions separately. After that initial charge,
#: tracking continues to be charged monthly."
CREDIT_COST_ADD = 10
CREDIT_COST_TRACK = 10

#: "The four stock auto-add categories", each with the research's own
#: description. They are stock, so their definitions are served rather than
#: accepted: a caller cannot redefine what "net-new with visitor intent" means.
#:
#: ``requires_target_market`` is read off the research's wording, which is not
#: uniform. "Net-new companies with visitor intent: companies that are in your
#: target markets and visiting high-intent pages, but aren't in your CRM yet"
#: names the target market; "in-CRM with visitor intent" does not, and adding
#: that requirement to it would silently stop tracking every existing customer
#: who visits - which is the opposite of what the category is for.
CATEGORY_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "net_new_visitor_intent": {
        "label": "Net-new companies with visitor intent",
        "description": (
            "companies that are in your target markets and visiting high-intent pages, "
            "but aren't in your CRM yet"
        ),
        "requires_target_market": True,
        "requires_visitor_intent": True,
        "requires_research_intent": False,
        "requires_in_crm": False,
    },
    "net_new_research_intent": {
        "label": "Net-new companies with research intent",
        "description": (
            "companies that are in your target markets and researching topics across the "
            "web, but aren't in your CRM yet"
        ),
        "requires_target_market": True,
        "requires_visitor_intent": False,
        "requires_research_intent": True,
        "requires_in_crm": False,
    },
    "in_crm_visitor_intent": {
        "label": "In-CRM companies with visitor intent",
        "description": (
            "companies already in your CRM that are showing visitor intent; no target "
            "market requirement, because the research's wording for this one omits it"
        ),
        "requires_target_market": False,
        "requires_visitor_intent": True,
        "requires_research_intent": False,
        "requires_in_crm": True,
    },
    "net_new_both_intents": {
        "label": "Net-new companies with visitor and research intent",
        "description": (
            "companies that are in your target markets and showing both visitor and "
            "research intent, but aren't in your CRM yet"
        ),
        "requires_target_market": True,
        "requires_visitor_intent": True,
        "requires_research_intent": True,
        "requires_in_crm": False,
    },
}

CATEGORY_IDS: tuple[str, ...] = tuple(CATEGORY_REQUIREMENTS)

#: The capabilities the research gates on HubSpot Credits: "To access buyer
#: intent features like filtering by segments and excluding companies, you need
#: HubSpot Credits."
CREDIT_GATED_CAPABILITIES: tuple[str, ...] = ("segment_filter", "exclusions")

#: "To add and enrich companies from buyer intent, Super Admin must assign users
#: with Data enrichment permissions." One named permission, applied to the three
#: operations that create or modify a CRM record on the strength of a signal.
ENRICHMENT_PERMISSION = "data_enrichment"

ENRICHMENT_GATED_OPERATIONS: tuple[str, ...] = ("auto_add", "auto_track", "manual_enrol")


def describe() -> dict[str, Any]:
    """The whole vocabulary, as JSON."""
    return {
        "path_operators": [
            {"id": key, "label": PATH_OPERATOR_LABELS[key]} for key in PATH_OPERATORS
        ],
        "sort_keys": [{"id": key, "label": SORT_LABELS[key]} for key in SORT_KEYS],
        "directions": list(DIRECTIONS),
        "traffic_sources": [
            {"id": key, "label": TRAFFIC_SOURCE_LABELS[key]} for key in TRAFFIC_SOURCES
        ],
        "news_signal_types": [
            {"id": key, "label": NEWS_SIGNAL_LABELS[key]} for key in NEWS_SIGNAL_TYPES
        ],
        "research_kinds": list(RESEARCH_KINDS),
        "lifecycle_stages": list(LIFECYCLE_STAGE_ORDER),
        "automation_toggles": [
            {"id": key, "label": AUTOMATION_LABELS[key]} for key in AUTOMATION_TOGGLES
        ],
        "auto_add_categories": [{"id": key, **CATEGORY_REQUIREMENTS[key]} for key in CATEGORY_IDS],
        "record_source": RECORD_SOURCE_BUYER_INTENT,
        "credit_cost_add": CREDIT_COST_ADD,
        "credit_cost_track": CREDIT_COST_TRACK,
        "credit_gated_capabilities": list(CREDIT_GATED_CAPABILITIES),
        "enrichment_permission": ENRICHMENT_PERMISSION,
        "enrichment_gated_operations": list(ENRICHMENT_GATED_OPERATIONS),
        "root_domain_model": {
            "rolls_up_subdomains": True,
            "truncates": "www",
            "note": (
                "Buyer intent uses a hierarchical root-domain model and truncates 'www' for "
                "display purposes. Activity from subdomains is rolled up into the root domain."
            ),
        },
        "card_fields": [
            {
                "id": "website_visits",
                "label": "Website visits",
                "note": "the count of sessions of website visits from this company",
            },
            {"id": "unique_visitors", "label": "Unique visitors"},
            {"id": "last_seen", "label": "Last seen"},
            {
                "id": "top_page_views",
                "label": "Top page views",
                "note": "the pages with the most visits from visitors from this company",
            },
        ],
    }


def lifecycle_rank(stage: Any) -> int | None:
    """A stage's position in the progression, or ``None`` if it is not in it.

    ``None`` rather than a large number, so a caller can tell "this stage is
    later than everything the product knows" from "this stage is the last one".
    """
    if not isinstance(stage, str):
        return None
    try:
        return LIFECYCLE_STAGE_ORDER.index(stage.strip().lower())
    except ValueError:
        return None
