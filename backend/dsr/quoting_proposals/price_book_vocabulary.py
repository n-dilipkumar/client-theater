"""WF-088: every researched term for assigning a price book to a deal by rule.

The specification is ``docs/research/digital-sales-room-workflows/wf/WF-088.md``,
quoted in full in issue 174, and the underlying research is
``docs/research/raw/quoting-proposals.md`` section 3. Every constant below carries
the sentence it came from, because a reason code that fires against a deal somebody
is about to sign is a reason code somebody has to be able to trace to its source.

Nothing here reads or writes. These are the words, the switches, the operators and
the evidence.

Where the specification left a joint open, the gap is recorded in
:mod:`dsr.quoting_proposals.price_book_inferences` rather than quietly filled in
here, so the difference between what the evidence says and what this build chose
stays readable.

This module is one of four for WF-088 and it is owned by WF-088 alone. The package
it lives in is shared with WF-091, WF-093 and WF-097, so nothing here edits a
module another ticket owns. It adds four files: ``price_book_vocabulary`` (this
one), ``price_book_rules``, ``price_book_inferences`` and ``price_book_engine``.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Ticket-prefixed, which is the uniform convention in this repository. The host
# has no collection-collision check, so the prefix is what keeps two features from
# quietly sharing one.

#: One configured assignment rule: the price book it assigns, the filters that
#: decide, and the two switches.
#:
#: One row per rule, because the flow is a builder that supports "**+ Add filter**"
#: more than once and an **edit icon** beside the object. A rule is therefore an
#: addressable row with a switch, not a list buried inside a price book blob.
PRICE_BOOK_RULES = "wf088_price_book_rule"

#: One decision about which price book a deal has. Written by every evaluation,
#: including the ones that assign nothing.
#:
#: A row rather than a field on the deal, and it is both the record of the decision
#: and the log a reviewer reads. The research names no log for this workflow, so the
#: row exists because a write that changes what a buyer is quoted has to leave a
#: trace; every ``reason`` on it is one of :data:`ASSIGNMENT_REASONS` and each of
#: those is a published code, never a sentence assembled at the call site.
ASSIGNMENTS = "wf088_price_book_assignment"

#: The deal this workflow assigns a price book to. Read across the names this
#: product has used for one, and written only in the ``price_book`` field.
#:
#: A deal is whatever the CRM mirror stored, and the aliases are what WF-086
#: already reads a deal through. This workflow never creates a deal.
SOURCE_DEALS = "crm_deal"

#: Company properties are named by the research as a filter source: "deal-property
#: filters" over "HubSpot deal properties, company properties, price book object".
#: Resolved the same way a deal is, so a mirror that calls them accounts works.
SOURCE_COMPANIES = "crm_company"

#: The quote this workflow prices. Read as data for the inheritance rule; the price
#: book is set on the deal and the quote inherits it.
SOURCE_QUOTES = "wf086_quote"

#: The line items a price book scopes. Read so a change of book can honour the
#: sourced "any line items associated with the previous price book will be removed".
LINE_ITEMS = "wf086_line_item"

#: WF-087's catalogue, when it has shipped. Optional and never required: a rule
#: names a price book by id or by name, so this workflow works before the
#: catalogue exists. See ``PRICE_BOOKS_ARE_REFERENCED_NOT_RESOLVED``.
SOURCE_PRICE_BOOKS = "crm_price_book"

#: Candidate names for each concept, most specific first.
#:
#: Read by existence rather than by declaration, so a deployment that mirrors a
#: deal as ``crm_opportunity`` or a company as ``crm_account`` is read without this
#: module naming it as the primary. An empty result is the honest "there is none
#: yet" answer and the caller reports it rather than refusing.
DEAL_COLLECTION_ALIASES = (SOURCE_DEALS, "crm_opportunity", "deal", "opportunity")

COMPANY_COLLECTION_ALIASES = (SOURCE_COMPANIES, "crm_account", "company", "account")

LINE_ITEM_COLLECTION_ALIASES = (
    LINE_ITEMS,
    "crm_deal_line_item",
    "line_item",
)

PRICE_BOOK_COLLECTION_ALIASES = (
    SOURCE_PRICE_BOOKS,
    "crm_price_level",
    "price_book",
    "pricebook",
)


# --------------------------------------------------------------------------- #
# The properties a filter reads
# --------------------------------------------------------------------------- #
#
# "configure deal-property filters (`and` / `or` groups)". Two objects and any
# property, so the object is a stored field rather than a schema and the property is
# a dotted JSON path resolved against the object the filter names.

FILTER_OBJECT_DEAL = "deal"
FILTER_OBJECT_COMPANY = "company"

#: The two objects the research names as filter sources.
#:
#: A team may store more. Nothing here validates an object into this tuple, so a
#: rule that filters on a team-defined object works without a change to this module.
#: The tuple exists so the page can offer the two the research names.
FILTER_OBJECTS = (FILTER_OBJECT_DEAL, FILTER_OBJECT_COMPANY)

FILTER_OBJECT_LABELS: dict[str, str] = {
    FILTER_OBJECT_DEAL: (
        "Deal properties. Amount, stage, pipeline, close date, and the sales "
        "territory the owner works in."
    ),
    FILTER_OBJECT_COMPANY: (
        "Company properties. Industry, size, country, and any custom property the "
        "deal's company carries."
    ),
}

#: Where a deal points at its company, in the order a mirror is likely to spell it.
#:
#: A list rather than a schema, because a deal that embeds the company it belongs
#: to and a deal that names it are both real and both cheap to read.
COMPANY_REFERENCE_FIELDS: tuple[str, ...] = (
    "company",
    "company_id",
    "account",
    "account_id",
    "associated_company",
)


# --------------------------------------------------------------------------- #
# Filter operators
# --------------------------------------------------------------------------- #
#
# The flow says "configure the filter" and does not enumerate the operators, so this
# set is this build's, chosen because it is what a deal-property filter needs to
# express a segment rule and a territory rule. Served on the page and recorded as
# this build's set rather than as sourced vocabulary. See
# ``DERIVED_FILTER_OPERATORS`` in the inferences module.

OPERATOR_IS = "is"
OPERATOR_IS_NOT = "is_not"
OPERATOR_GREATER_THAN = "gt"
OPERATOR_GREATER_THAN_OR_EQUAL = "gte"
OPERATOR_LESS_THAN = "lt"
OPERATOR_LESS_THAN_OR_EQUAL = "lte"
OPERATOR_CONTAINS = "contains"
OPERATOR_DOES_NOT_CONTAIN = "does_not_contain"
OPERATOR_IN = "in"
OPERATOR_NOT_IN = "not_in"

OPERATORS = (
    OPERATOR_IS,
    OPERATOR_IS_NOT,
    OPERATOR_GREATER_THAN,
    OPERATOR_GREATER_THAN_OR_EQUAL,
    OPERATOR_LESS_THAN,
    OPERATOR_LESS_THAN_OR_EQUAL,
    OPERATOR_CONTAINS,
    OPERATOR_DOES_NOT_CONTAIN,
    OPERATOR_IN,
    OPERATOR_NOT_IN,
)

OPERATOR_LABELS: dict[str, str] = {
    OPERATOR_IS: "is",
    OPERATOR_IS_NOT: "is not",
    OPERATOR_GREATER_THAN: "is greater than",
    OPERATOR_GREATER_THAN_OR_EQUAL: "is at least",
    OPERATOR_LESS_THAN: "is less than",
    OPERATOR_LESS_THAN_OR_EQUAL: "is at most",
    OPERATOR_CONTAINS: "contains",
    OPERATOR_DOES_NOT_CONTAIN: "does not contain",
    OPERATOR_IN: "is any of",
    OPERATOR_NOT_IN: "is none of",
}

#: The operators whose right-hand side is a list rather than one value. "is any of"
#: with a scalar would match nothing and read as a broken rule.
LIST_OPERATORS = (OPERATOR_IN, OPERATOR_NOT_IN)

#: The operators that compare numbers.
NUMERIC_OPERATORS = (
    OPERATOR_GREATER_THAN,
    OPERATOR_GREATER_THAN_OR_EQUAL,
    OPERATOR_LESS_THAN,
    OPERATOR_LESS_THAN_OR_EQUAL,
)

FILTER_OPERATORS_CHOICE_NOTE = (
    "The research says 'configure the filter' and does not enumerate the operators. "
    "This set is this build's, chosen to express a deal segment and a territory."
)


# --------------------------------------------------------------------------- #
# How several filters combine
# --------------------------------------------------------------------------- #
#
# "configure deal-property filters (`and` / `or` groups)". Both conjunctions are
# named here, which is why neither is this build's choice, and `all` is the default
# because a rule that adds filters one at a time in the flow is an AND list.

FILTER_MATCH_ALL = "all"
FILTER_MATCH_ANY = "any"

FILTER_MATCH_MODES = (FILTER_MATCH_ALL, FILTER_MATCH_ANY)

#: `all` is the default, and the word in the source is `and`.
FILTER_MATCH_MODE_LABELS: dict[str, str] = {
    FILTER_MATCH_ALL: (
        "Every filter must be met. The research names and groups, so this is the default."
    ),
    FILTER_MATCH_ANY: "Any one filter is enough. The research names or groups too.",
}

FILTER_GROUP_QUOTE = "configure deal-property filters (`and` / `or` groups)"


# --------------------------------------------------------------------------- #
# The two switches
# --------------------------------------------------------------------------- #
#
# The flow is two toggles and both of them are on the rule, so both are fields here.

#: The price book's **Inactive** switch, off to activate. A rule that is inactive
#: reads as a rule that did not match, which is what
#: :data:`ASSIGNMENT_NO_MATCH_INACTIVE_RULE` names.
ENABLED = "enabled"

#: "toggle **Auto-assigned** on". The switch that separates the researched "test
#: first" mode from the mode this ticket names in its own title.
AUTO_ASSIGN = "auto_assign"

#: Both toggles default on: a rule a seller saved with the flow's defaults is one
#: whose **Auto-assigned** switch was left on and whose price book was activated.
DEFAULT_ENABLED = True
DEFAULT_AUTO_ASSIGN = True


# --------------------------------------------------------------------------- #
# The three modes the source offers
# --------------------------------------------------------------------------- #
#
# "*No assignment rules* (manual only), *Assignment rules without auto-assignment*
# (test first), *Assignment rules with auto-assignment*". The research names three
# and the specification does not say which to build. All three are reachable and
# none of them is a mode switch, because in the source each one is a configuration
# of rules rather than a setting: see ``DERIVED_ALL_THREE_MODES_ARE_RULES`` in the
# inferences module for why that is a reading and not a dodge.

MODE_NO_RULES = "no_assignment_rules"
MODE_TEST_ONLY = "assignment_rules_without_auto_assignment"
MODE_AUTO_ASSIGN = "assignment_rules_with_auto_assignment"

MODES = (MODE_NO_RULES, MODE_TEST_ONLY, MODE_AUTO_ASSIGN)

MODE_LABELS: dict[str, str] = {
    MODE_NO_RULES: (
        "No assignment rules. Manual only: nobody is assigned a price book "
        "automatically, and **Change price book** is the only way one arrives."
    ),
    MODE_TEST_ONLY: (
        "Assignment rules without auto-assignment. The rules are evaluated and "
        "reported, and nothing is written. This is the test-first mode."
    ),
    MODE_AUTO_ASSIGN: (
        "Assignment rules with auto-assignment. Exactly one matching rule writes "
        "its price book onto the deal."
    ),
}

#: The **Auto-assigned** toggle, quoted whole. It is the whole difference between
#: the second mode and the third.
AUTO_ASSIGNED_TOGGLE_QUOTE = "toggle **Auto-assigned** on"


# --------------------------------------------------------------------------- #
# The price book on the deal
# --------------------------------------------------------------------------- #

#: The payload key this workflow writes on the deal, in this build's spelling.
#:
#: The research does not name the field: it names the card ("**Price book: None**")
#: and the Dynamics column (``pricelevelid``). This is an ordinary JSON key, so a
#: team that already writes one of the aliases is read rather than overwritten. See
#: ``PRICE_BOOK_FIELD_ALIASES``.
PRICE_BOOK = "price_book"

#: The other spellings a CRM mirror may already use, read before :data:`PRICE_BOOK`.
#:
#: ``pricelevelid`` is the Dynamics column the research names by name: "the price
#: level is written onto the deal/quote header (``pricelevelid`` in Dynamics)". The
#: HubSpot spelling is an association rather than a column, so no field name is
#: sourced for it and this build names one.
PRICE_BOOK_FIELD_ALIASES: tuple[str, ...] = (
    PRICE_BOOK,
    "pricelevelid",
    "price_book_id",
    "pricebook_id",
    "priceLevelId",
)

PRICE_BOOK_NONE_LABEL = "None"

#: "**Change price book**". The researched override, on the deal's *Line items*
#: card. It is also how a deal owner resolves a deal that matched more than one rule.
CHANGE_PRICE_BOOK_QUOTE = "Change price book"

#: "If the price book is changed, any line items associated with the previous price
#: book will be removed." Quoted whole. See ``OVERRIDE_REMOVES_THE_PREVIOUS_BOOKS_LINES``
#: in the inferences module for what this build does with it.
LINE_ITEMS_REMOVED_QUOTE = "If the price book is changed, any line items associated with the previous price book will be removed."

#: The fields on a price-book reference. A rule and a deal both carry one, and both
#: may be written by hand, so both are read through the same function.
BOOK_ID = "id"
BOOK_NAME = "name"

#: The line-item fields that say which book a line belongs to. A line that names
#: none belongs to the deal's book, because the sourced data flow says the book is
#: written on the deal and the lines are then scoped to it.
LINE_BOOK_FIELDS: tuple[str, ...] = (
    PRICE_BOOK,
    "price_book_id",
    "pricelevelid",
    "pricebook_id",
)


# --------------------------------------------------------------------------- #
# The price book on the quote
# --------------------------------------------------------------------------- #

#: "Quotes inherit the price book from the associated deal. Users can't select a
#: price book when creating a quote; they must select it on the deal." Quoted whole.
#: The second sentence is why there is no route that writes a price book onto a
#: quote: see :func:`~dsr.quoting_proposals.price_book_rules.evaluate_inheritance`,
#: which refuses the attempt with the code ``price_book_is_set_on_the_deal``.
QUOTE_INHERITS_QUOTE = (
    "Quotes inherit the price book from the associated deal. Users can't select a "
    "price book when creating a quote; they must select it on the deal."
)

#: What the inheritance answer reports, so a reader can tell where the value came
#: from rather than assuming the quote carried it.
INHERITANCE_AUTHORITY_DEAL = "deal"
INHERITANCE_AUTHORITY_NONE = "none"


# --------------------------------------------------------------------------- #
# The decisions
# --------------------------------------------------------------------------- #
#
# Every assignment is one of these and each is a published code. The ones that
# assign nothing are as important as the one that assigns: "why does this deal have
# no price book" is the question a seller asks first.

ASSIGNMENT_ASSIGNED = "assigned"
ASSIGNMENT_NEEDS_CHOICE = "needs_choice"
ASSIGNMENT_NO_MATCH = "no_rule_matched"
ASSIGNMENT_NO_MATCH_INACTIVE_RULE = "matched_rule_is_inactive"
ASSIGNMENT_NO_AUTO_ASSIGN = "matched_rule_without_auto_assign"
ASSIGNMENT_ALREADY_ASSIGNED = "already_assigned"
ASSIGNMENT_NOT_ON_CREATE = "auto_assign_runs_on_create_only"
ASSIGNMENT_SET = "set_by_hand"
ASSIGNMENT_CHANGED = "changed_by_hand"

ASSIGNMENT_REASONS = (
    ASSIGNMENT_ASSIGNED,
    ASSIGNMENT_NEEDS_CHOICE,
    ASSIGNMENT_NO_MATCH,
    ASSIGNMENT_NO_MATCH_INACTIVE_RULE,
    ASSIGNMENT_NO_AUTO_ASSIGN,
    ASSIGNMENT_ALREADY_ASSIGNED,
    ASSIGNMENT_NOT_ON_CREATE,
    ASSIGNMENT_SET,
    ASSIGNMENT_CHANGED,
)

#: The reasons that write a price book onto the deal. The rest are evaluations that
#: wrote nothing, and each of those is still recorded.
WRITING_REASONS = (
    ASSIGNMENT_ASSIGNED,
    ASSIGNMENT_SET,
    ASSIGNMENT_CHANGED,
)

#: The reasons that mean "a rule matched and a human has to choose". Both sourced
#: sentences about a deal that matched more than one book land on this one code.
NEEDS_CHOICE_REASONS = (ASSIGNMENT_NEEDS_CHOICE,)

ASSIGNMENT_REASON_LABELS: dict[str, str] = {
    ASSIGNMENT_ASSIGNED: ("One rule matched exactly, so its price book was written onto the deal."),
    ASSIGNMENT_NEEDS_CHOICE: (
        "More than one price book matched, so nothing was written and the deal owner chooses."
    ),
    ASSIGNMENT_NO_MATCH: ("No configured filter matched this deal, so no price book was assigned."),
    ASSIGNMENT_NO_MATCH_INACTIVE_RULE: (
        "A rule matched this deal but its Inactive switch is on, so it is not active."
    ),
    ASSIGNMENT_NO_AUTO_ASSIGN: (
        "A rule matched this deal but its Auto-assigned switch is off, so the rule "
        "is being tested and nothing was written."
    ),
    ASSIGNMENT_ALREADY_ASSIGNED: (
        "This deal already has a price book. Auto-assignment does not run again."
    ),
    ASSIGNMENT_NOT_ON_CREATE: (
        "Price books are auto-assigned only when a deal is created, so an update assigns nothing."
    ),
    ASSIGNMENT_SET: ("A price book was set on the deal by hand, through Change price book."),
    ASSIGNMENT_CHANGED: (
        "The price book was changed on the deal, and the line items of the previous "
        "book were removed."
    ),
}

#: The state a deal is in for pricing, derived rather than stored, so it cannot
#: disagree with what is on the deal.
BOOK_STATE_ASSIGNED = "assigned"
BOOK_STATE_NEEDS_CHOICE = "needs_choice"
BOOK_STATE_UNASSIGNED = "unassigned"

BOOK_STATES = (BOOK_STATE_ASSIGNED, BOOK_STATE_NEEDS_CHOICE, BOOK_STATE_UNASSIGNED)

BOOK_STATE_LABELS: dict[str, str] = {
    BOOK_STATE_ASSIGNED: (
        "A price book is on the deal, so the product lookup and the line-item prices "
        "are scoped to it."
    ),
    BOOK_STATE_NEEDS_CHOICE: (
        "More than one price book matched and a person has to choose. The card reads "
        "**Price book: None** until they do."
    ),
    BOOK_STATE_UNASSIGNED: (
        "No price book is on the deal. Nothing is scoped, and adding a line item "
        "resolves a price outside any book."
    ),
}


# --------------------------------------------------------------------------- #
# When auto-assignment runs
# --------------------------------------------------------------------------- #

#: "Price books are auto-assigned only when a deal is created." Quoted whole. The
#: second half of the sourced sentence is why an already-assigned deal is never
#: re-assigned: "After a price book is auto-assigned, HubSpot won't run
#: auto-assignment again if the deal or associated company properties used in the
#: filter are updated".
TRIGGER_CREATE = "create"
TRIGGER_UPDATE = "update"

TRIGGERS = (TRIGGER_CREATE, TRIGGER_UPDATE)

TRIGGER_LABELS: dict[str, str] = {
    TRIGGER_CREATE: ("The deal row was created. This is the only moment auto-assignment runs."),
    TRIGGER_UPDATE: (
        "Something on the deal or its company changed. The research says "
        "auto-assignment does not run again, so this assigns nothing."
    ),
}

CREATE_ONLY_QUOTE = (
    "Price books are auto-assigned only when a deal is created. After a price book "
    "is auto-assigned, HubSpot won't run auto-assignment again if the deal or "
    "associated company properties used in the filter are updated."
)

#: The Dynamics message the research names, kept because it is the only vendor
#: identifier in this workflow's evidence and a client written against the Dynamics
#: documentation looks for it.
#:
#: "Dynamics 365 internally uses the ``GetDefaultPriceLevelRequest`` message to
#: determine the default price level" and "Register the plug-in on the
#: ``GetDefaultPriceLevel`` message". Served on the vocabulary route so the name is
#: in the product rather than only in a docstring. See ``AUTO_ASSIGNMENT_IS_CREATE_ONLY``
#: in the inferences module for the one place this workflow parts company with the
#: Dynamics sentence beside it.
DEFAULT_PRICE_LEVEL_MESSAGE = "GetDefaultPriceLevelRequest"

DEFAULT_PRICE_LEVEL_SETTING = "Organization.UseInbuiltRuleForDefaultPriceSelectionRule"

DEFAULT_PRICE_LEVEL_QUOTE = (
    "Dynamics 365 internally uses the GetDefaultPriceLevelRequest message to "
    "determine the default price level."
)

#: The connection role the research names for the Dynamics half of the rule, served
#: for the same reason as the message name above.
TERRITORY_PRICE_LEVEL_ROLE = "Territory Default Pricelist"

TERRITORY_ROLE_QUOTE = (
    "associate a price level with a Territory using the Territory Default Pricelist "
    "connection role, and assign the territory to the user"
)


# --------------------------------------------------------------------------- #
# Where the price book came from
# --------------------------------------------------------------------------- #

#: "the acting user's sales territory (Dynamics)". The territory is read off the
#: deal rather than off a user record, because in this product a deal's payload is
#: where a mirror puts the owner and the owner's territory. See
#: ``THE_TERRITORY_RULE_IS_A_DEAL_PROPERTY``.
TERRITORY_FIELDS: tuple[str, ...] = ("territory", "owner_territory", "sales_territory")

#: Who answered a state read, so a reader can tell the deal from the rule that set it.
AUTHORITY_DEAL_FIELD = "price_book_field"
AUTHORITY_ASSIGNMENT = "assignment_rule"
AUTHORITY_NONE = "none"


# --------------------------------------------------------------------------- #
# The errors
# --------------------------------------------------------------------------- #
#
# Each error this workflow raises, with the status it maps to and the specification's
# own sentence in the value, so a refusal returned by the API reads as the research
# rather than as a developer's paraphrase of it.

ERROR_CODES: dict[str, tuple[int, str]] = {
    "rule_needs_a_name": (422, "Give the rule a name so a page can list it."),
    "rule_needs_a_filter": (
        422,
        "A rule needs at least one filter. A rule that matches every deal assigns a "
        "price book to every deal.",
    ),
    "rule_needs_a_price_book": (
        422,
        "A rule assigns a price book, so it needs one. Open a price book, then save "
        "an assignment rule on it.",
    ),
    "unknown_filter_object": (
        422,
        "A filter names an object. The research names deal properties and company "
        "properties, and a team may add its own.",
    ),
    "filter_needs_a_property": (
        422,
        "Choose a property for the filter. The flow picks one from the object's property list.",
    ),
    "unknown_operator": (422, f"Known operators are: {', '.join(OPERATORS)}."),
    "filter_needs_a_value": (422, "A filter needs a value to compare the property with."),
    "numeric_filter_value_is_not_a_number": (
        422,
        "This operator compares numbers, so the value must be a number.",
    ),
    "list_operator_needs_a_list": (
        422,
        "An 'is any of' or 'is none of' filter needs a list of values.",
    ),
    "unknown_match_mode": (422, f"Combine filters with: {', '.join(FILTER_MATCH_MODES)}."),
    "unknown_trigger": (422, f"Auto-assignment runs on: {', '.join(TRIGGERS)}."),
    "price_book_needs_an_id_or_a_name": (
        422,
        "Name the price book to set. A rule and a deal both refer to one by id or by name.",
    ),
    "override_to_the_same_price_book": (
        409,
        "This deal already has that price book, so there is nothing to change and no "
        "line item to remove.",
    ),
    "quote_price_book_is_set_on_the_deal": (
        409,
        "Users can't select a price book when creating a quote; they must select it on the deal.",
    ),
    "quote_has_no_deal": (
        409,
        "This quote has no associated deal, and the quote inherits the price book from the deal.",
    ),
    "unknown_assignment_rule": (404, "No such assignment rule."),
    "unknown_assignment": (404, "No such price book assignment."),
    "unknown_deal": (404, "No such deal."),
    "unknown_quote": (404, "No such quote."),
}


# --------------------------------------------------------------------------- #
# What the page renders from
# --------------------------------------------------------------------------- #


def catalogue() -> dict[str, Any]:
    """Every researched term, as data a client can render from.

    Every value here is a constant above, so the served list and the enforced rule
    cannot disagree. The frontend reads this rather than hard-coding a list, so a
    term added here appears on the page with no edit to the feature module.
    """

    return {
        "collections": {
            "price_book_rules": PRICE_BOOK_RULES,
            "assignments": ASSIGNMENTS,
            "source_deals_read_and_price_book_written": SOURCE_DEALS,
            "source_companies_read_only": SOURCE_COMPANIES,
            "source_quotes_read_only": SOURCE_QUOTES,
            "line_items_read_only": LINE_ITEMS,
            "source_price_books_optional": SOURCE_PRICE_BOOKS,
            "deal_collection_aliases": list(DEAL_COLLECTION_ALIASES),
            "company_collection_aliases": list(COMPANY_COLLECTION_ALIASES),
            "line_item_collection_aliases": list(LINE_ITEM_COLLECTION_ALIASES),
            "price_book_collection_aliases": list(PRICE_BOOK_COLLECTION_ALIASES),
        },
        "filters": {
            "objects": [
                {"object": name, "label": FILTER_OBJECT_LABELS[name]} for name in FILTER_OBJECTS
            ],
            "operators": [{"operator": name, "label": OPERATOR_LABELS[name]} for name in OPERATORS],
            "list_operators": list(LIST_OPERATORS),
            "numeric_operators": list(NUMERIC_OPERATORS),
            "match_modes": [
                {"mode": name, "label": FILTER_MATCH_MODE_LABELS[name]}
                for name in FILTER_MATCH_MODES
            ],
            "default_match_mode": FILTER_MATCH_ALL,
            "operators_choice_note": FILTER_OPERATORS_CHOICE_NOTE,
            "group_quote": FILTER_GROUP_QUOTE,
            "company_reference_fields": list(COMPANY_REFERENCE_FIELDS),
        },
        "switches": {
            "enabled": ENABLED,
            "auto_assign": AUTO_ASSIGN,
            "defaults": {"enabled": DEFAULT_ENABLED, "auto_assign": DEFAULT_AUTO_ASSIGN},
            "auto_assigned_toggle_quote": AUTO_ASSIGNED_TOGGLE_QUOTE,
        },
        "modes": [{"mode": name, "label": MODE_LABELS[name]} for name in MODES],
        "price_book": {
            "field": PRICE_BOOK,
            "field_aliases": list(PRICE_BOOK_FIELD_ALIASES),
            "none_label": PRICE_BOOK_NONE_LABEL,
            "change_quote": CHANGE_PRICE_BOOK_QUOTE,
            "line_items_removed_quote": LINE_ITEMS_REMOVED_QUOTE,
            "line_book_fields": list(LINE_BOOK_FIELDS),
        },
        "quote_inheritance": {
            "quote": QUOTE_INHERITS_QUOTE,
            "authorities": [INHERITANCE_AUTHORITY_DEAL, INHERITANCE_AUTHORITY_NONE],
        },
        "assignments": [
            {
                "reason": name,
                "label": ASSIGNMENT_REASON_LABELS[name],
                "writes": name in WRITING_REASONS,
            }
            for name in ASSIGNMENT_REASONS
        ],
        "writing_reasons": list(WRITING_REASONS),
        "needs_choice_reasons": list(NEEDS_CHOICE_REASONS),
        "book_states": [{"state": name, "label": BOOK_STATE_LABELS[name]} for name in BOOK_STATES],
        "triggers": [{"trigger": name, "label": TRIGGER_LABELS[name]} for name in TRIGGERS],
        "create_only_quote": CREATE_ONLY_QUOTE,
        "dynamics": {
            "message": DEFAULT_PRICE_LEVEL_MESSAGE,
            "system_setting": DEFAULT_PRICE_LEVEL_SETTING,
            "message_quote": DEFAULT_PRICE_LEVEL_QUOTE,
            "connection_role": TERRITORY_PRICE_LEVEL_ROLE,
            "connection_role_quote": TERRITORY_ROLE_QUOTE,
            "territory_fields": list(TERRITORY_FIELDS),
        },
        "authorities": [
            AUTHORITY_DEAL_FIELD,
            AUTHORITY_ASSIGNMENT,
            AUTHORITY_NONE,
        ],
        "error_codes": {
            name: {"status": status, "detail": detail}
            for name, (status, detail) in ERROR_CODES.items()
        },
    }
