"""The collection names this workflow owns.

One module, so a filter, a route, and a seed cannot disagree about which
collection a thing lives in. None of them has a migration, a typed column, or a
required field: every payload is arbitrary JSON in ``records.data``, and every
lookup goes through ``find()``, which resolves dotted paths through the dynamic
index.

The names are prefixed ``intent_`` because this is the twelfth workflow to land
in this store and several others write company-shaped rows of their own. A
shared collection name is not a collision in the host's sense - nothing would
fail - it is a silent one: a ``find`` that meant one workflow's companies would
return another's, and the only symptom would be a number that is too large.
"""

from __future__ import annotations

#: An intent criterion: a named set of pages, and the custom property it
#: derives onto a company record.
CRITERIA = "intent_criterion"

#: A research topic, matched against topic observations on the Research tab.
TOPICS = "intent_topic"

#: A target market. A company is "in my target markets" when its country or its
#: industry is one a market names.
MARKETS = "intent_market"

#: A domain excluded from the table and from every automation.
EXCLUSIONS = "intent_exclusion"

#: One tracked page view, with the facts the table's columns are computed from.
VISITS = "intent_visit"

#: A research observation: a topic match or a company news signal.
RESEARCH = "intent_research"

#: A company added to the CRM, carrying ``record_source: Buyer-Intent``.
COMPANIES = "intent_company"

#: A known contact at a tracked company, for the Contacts drill-down tab.
CONTACTS = "intent_contact"

#: A saved view: a name and the filter set behind it.
VIEWS = "intent_view"

#: The per-view automation: the Add new companies and Track intent signals
#: toggles, and when each was switched on.
AUTOMATIONS = "intent_automation"

#: The state of the four stock auto-add categories.
CATEGORIES = "intent_category"

#: A company under continuous tracking, with the billing period it started in.
TRACKING = "intent_tracking"

#: The credit ledger: one row per company per billing period, so the
#: "charged once, not for both actions separately" rule is structural.
CREDITS = "intent_credit"

#: A manual "Enroll in workflow" from the Visitors tab.
ENROLMENTS = "intent_enrolment"

#: The portal's gating state: whether HubSpot Credits are available, and who
#: carries the Data enrichment permission.
SETTINGS = "intent_setting"

#: Every collection, for the seeder's report and for a test that proves a
#: feature owns its own tables.
ALL: tuple[str, ...] = (
    CRITERIA,
    TOPICS,
    MARKETS,
    EXCLUSIONS,
    VISITS,
    RESEARCH,
    COMPANIES,
    CONTACTS,
    VIEWS,
    AUTOMATIONS,
    CATEGORIES,
    TRACKING,
    CREDITS,
    ENROLMENTS,
    SETTINGS,
)

#: How many records one read will scan per collection.
#:
#: A bound rather than an open read, and the response says when it bites: a
#: table that quietly stops at 1,000 rows is a table whose totals are wrong and
#: look right, which is the worst failure a number can have. The cap exists so
#: a workspace with a year of traffic cannot make a page take a minute.
SCAN_LIMIT = 1000

#: How many qualifying-evidence rows a company row carries.
EVIDENCE_LIMIT = 20

#: How many paths a "Top page views" column shows.
TOP_PAGE_LIMIT = 10
