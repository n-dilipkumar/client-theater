"""WF-088: every judgement call the specification left open, and the reading taken.

The research for this workflow is quoted in full in issue 174. Eight points in it are
open, and this module records each one with the alternative that was rejected, what
the rejection would have cost, and the audit that chose it where one was asked for.

Nothing here reads or writes. It exists so the difference between what the evidence
says and what this build chose stays readable on the page rather than buried in a
docstring somebody has to go and find.

The rule the module exists for
-------------------------------

A reason code that decides what a buyer is quoted and cannot be traced to a decision
is a code somebody changes one day without knowing why. So each entry here names the
decision, the options that were on the table, the one that was chosen, the reason the
others lost, and the audit.
"""

from __future__ import annotations

from typing import Any

from dsr.quoting_proposals import price_book_vocabulary as vocab

#: The audits that chose the two readings the specification left genuinely open.
#: Both were asked as a choice between candidate readings of two sourced sentences
#: that do not agree, and both returned the reading this build implemented.
MULTIPLE_MATCHES_AUDIT = "jev-20261005T121136-18784-96276"
MULTIPLE_MATCHES_CONFIDENCE = 1.0

OVERRIDE_AUDIT = "jev-20261005T121136-18784-96564"
OVERRIDE_CONFIDENCE = 1.0


_DECISIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "MULTIPLE_MATCHES_NEED_A_CHOICE",
        "question": ("Two or more assignment rules match one deal. What is written onto the deal?"),
        "sourced": [
            "If multiple price books match, the deal owner can choose which matching price book to use.",
            "If multiple price levels are returned, the price level field isn't populated and the user must specify a price level.",
        ],
        "options": {
            "needs_choice": (
                "Write nothing. Report every matching price book as a candidate and let the "
                "deal owner choose through Change price book. The card reads Price book: None "
                "until they do."
            ),
            "first_match_wins": (
                "Write the price book of the first matching rule in creation order and ignore "
                "the rest."
            ),
            "most_specific_wins": (
                "Rank the matching rules by how many filters each has, and write the one with "
                "the most."
            ),
            "super_admin_picks": (
                "Write nothing and require a super admin, rather than the deal owner, to choose."
            ),
        },
        "chosen": "needs_choice",
        "rejected_because": (
            "The two sourced sentences do not disagree on the outcome: both say a person "
            "chooses and neither says a price book is written. first_match_wins and "
            "most_specific_wins both invent a ranking rule the research does not contain, and "
            "a silently arbitrary choice of price book is the worst outcome available here, "
            "because it is invisible: the deal looks priced and nobody chose it. "
            "super_admin_picks contradicts 'the deal owner can choose'."
        ),
        "cost_of_rejection": (
            "A deal priced by an invented tie-break would quote a buyer at a price nobody "
            "picked, and the price book on it would read as though a person had set it."
        ),
        "jev_audit_id": MULTIPLE_MATCHES_AUDIT,
        "jev_confidence": MULTIPLE_MATCHES_CONFIDENCE,
        "why_one_rule_satisfies_both": (
            "The two sentences describe the same moment from two vendors. HubSpot says the "
            "deal owner may choose between the matching books; Dynamics says the field is not "
            "populated and the user must specify one. Neither writes a book automatically, so "
            "the single code ASSIGNMENT_NEEDS_CHOICE is what both of them already were."
        ),
        "exposed_on": "assignments.needs_choice, and the candidates list beside it",
    },
    {
        "id": "OVERRIDE_REMOVES_THE_PREVIOUS_BOOKS_LINES",
        "question": (
            "A price book is changed on a deal. What happens to the line items priced under "
            "the previous one?"
        ),
        "sourced": [
            "If the price book is changed, any line items associated with the previous price book will be removed.",
        ],
        "options": {
            "remove_and_name": (
                "Soft-delete the line items associated with the previous book, record their ids "
                "and count on the assignment, and say so in the answer."
            ),
            "keep_them": "Leave the line items alone and let the new book re-price them.",
            "archive_only": "Mark them as belonging to a previous book without deleting them.",
        },
        "chosen": "remove_and_name",
        "rejected_because": (
            "The source states the removal outright, and the specification asks for a decision "
            "rather than permission to skip it. keep_them would leave lines priced under a "
            "book the deal no longer has, which is the state the removal exists to prevent. "
            "archive_only is the removal without the removal: a line still on the deal would "
            "still be browsable under the new book."
        ),
        "cost_of_rejection": (
            "Both rejected options quote a buyer a mix of two books' prices, and neither leaves "
            "a record that anything was removed, so the line count on the deal and the audit "
            "trail would disagree."
        ),
        "jev_audit_id": OVERRIDE_AUDIT,
        "jev_confidence": OVERRIDE_CONFIDENCE,
        "derived_detail": (
            "The source says 'associated with the previous price book' without saying how a "
            "line item is associated. This build counts two cases: a line that names the "
            "previous book, and a line that names no book at all. The second case follows from "
            "the sourced data flow, which puts the book on the deal header and then scopes the "
            "line items to it, so a line with no book of its own is a line of the deal's book. "
            "A line naming a different book is not removed, because it is not a line of the "
            "previous one. See rules.lines_for_book."
        ),
        "exposed_on": "assignments.line_items_removed, and rules.lines_for_book",
    },
    {
        "id": "AUTO_ASSIGNMENT_IS_CREATE_ONLY",
        "question": "Which moments run auto-assignment?",
        "sourced": [
            # Split across two literals rather than kept on one line. The `**/inferences.py`
            # entry in pyproject.toml waives E501 for files named exactly `inferences.py`,
            # and this one is `price_book_inferences.py`, so the waiver does not reach it.
            # A cited sentence stays whole whichever way the source formats it.
            "Price books are auto-assigned only when a deal is created. After a price book "
            "is auto-assigned, HubSpot won't run auto-assignment again if the deal or "
            "associated company properties used in the filter are updated",
            "Dynamics 365 internally uses the GetDefaultPriceLevelRequest message to determine the default price level.",
        ],
        "options": {
            "create_only": (
                "Auto-assignment runs when a deal row is created. An update evaluates and "
                "reports nothing, with ASSIGNMENT_NOT_ON_CREATE as its reason."
            ),
            "create_and_update": (
                "Re-evaluate on every update of the deal or its company, and re-assign when a "
                "different book now matches exactly one rule."
            ),
            "create_and_update_when_unpriced": (
                "Re-evaluate on update, but only write when the deal has no price book."
            ),
        },
        "chosen": "create_only",
        "rejected_because": (
            "The two sentences are about different systems and the specification's own data "
            "flow names HubSpot deal and company properties as this workflow's sources, so the "
            "HubSpot sentence is the one this product's trigger maps onto. The Dynamics message "
            "is served on the vocabulary route under its own name so a client written against "
            "that documentation still finds it. create_and_update contradicts the sourced "
            "sentence outright. create_and_update_when_unpriced is the same rule wearing a "
            "condition: it would let a company property update put a price book on a deal "
            "created under different circumstances, which is exactly the drift the source "
            "refuses."
        ),
        "cost_of_rejection": (
            "A deal whose segment is corrected after creation would keep the book it was "
            "created with. That is the sourced behaviour, and the override exists for it."
        ),
        "jev_audit_id": None,
        "jev_confidence": None,
        "exposed_on": "assignments.trigger, and rules.decide_assignment",
    },
    {
        "id": "ALL_THREE_MODES_ARE_RULES_NOT_A_SETTING",
        "question": (
            "The source offers three modes. Which one does this build implement, and are the "
            "others reachable?"
        ),
        "sourced": [
            "No assignment rules (manual only), Assignment rules without auto-assignment (test first), Assignment rules with auto-assignment",
            "toggle Auto-assigned on",
            "toggle the price book's Inactive switch off to activate",
        ],
        "options": {
            "all_three_as_configuration": (
                "Build the auto-assignment behaviour the ticket names, and reach the other two "
                "through the rules' own two switches: no active rules is manual only, and "
                "active rules with Auto-assigned off is test first."
            ),
            "auto_assignment_only": (
                "Build only the third mode and refuse to save a rule whose Auto-assigned switch "
                "is off."
            ),
            "one_mode_switch": ("Add a single workspace setting choosing between the three modes."),
        },
        "chosen": "all_three_as_configuration",
        "rejected_because": (
            "In the source each mode is a configuration of rules and none of them is a setting: "
            "the first is the absence of rules, the second is a rule with Auto-assigned off, "
            "and the third is the same rule with it on. A fourth switch choosing between them "
            "would duplicate a fact the rules already carry and could disagree with them. "
            "auto_assignment_only refuses a configuration the source tells an admin to use, "
            "and refuses it at save time, which is the failure mode the research's own UI "
            "avoids by accepting the toggle."
        ),
        "cost_of_rejection": (
            "A workspace that wants to test a rule before trusting it has nowhere to go, and a "
            "reviewer cannot read the mode off the rules."
        ),
        "jev_audit_id": None,
        "jev_confidence": None,
        "exposed_on": "vocabulary.modes, and rules.workspace_mode",
    },
    {
        "id": "THE_PRICE_BOOK_FIELD_BELONGS_TO_THIS_WORKFLOW",
        "question": "Does this workflow own the price book on a deal, or does it report one?",
        "sourced": [
            "one matching price book / price level is written onto the deal/quote header (pricelevelid in Dynamics) at creation",
            "On the deal's Line items card click Price book: None (or the price book name) -> Price book dropdown -> Change price book.",
        ],
        "options": {
            "write_the_deal_field": (
                "Write one ordinary JSON key onto the deal and treat every other workflow's "
                "price_book read as reading this workflow's value."
            ),
            "keep_the_assignment_off_the_deal": (
                "Record the assignment only on this workflow's own rows and leave the deal "
                "untouched."
            ),
            "own_the_whole_deal": "Create and version deals here as well as pricing them.",
        },
        "chosen": "write_the_deal_field",
        "rejected_because": (
            "The data flow names the write, and it is the whole point of the workflow: 'the "
            "product lookup and line-item price resolution are then scoped to that book, so "
            "only that book's products/prices/terms are browsable when adding line items'. A "
            "value nothing reads cannot scope anything. keep_the_assignment_off_the_deal leaves "
            "that scoping with no source, and own_the_whole_deal would give a CRM mirror this "
            "feature does not own a second writer over every field rather than one."
        ),
        "cost_of_rejection": (
            "One JSON key on one mirrored record, and no migration and no typed column. The "
            "aliases in PRICE_BOOK_FIELD_ALIASES mean a mirror that already writes the field "
            "is read rather than overwritten."
        ),
        "jev_audit_id": None,
        "jev_confidence": None,
        "exposed_on": "GET /deals/{deal_id}/price-book, and its authority field",
    },
    {
        "id": "THE_TERRITORY_RULE_IS_A_DEAL_PROPERTY",
        "question": (
            "The Dynamics half of the rule keys off a sales territory on the acting user. "
            "How is that expressed here?"
        ),
        "sourced": [
            "associate a price level with a Territory using the Territory Default Pricelist connection role, and assign the territory to the user.",
            "the acting user's sales territory (Dynamics)",
            "configure deal-property filters (and / or groups)",
        ],
        "options": {
            "deal_property_filter": (
                "Read the territory off the deal payload, as any other deal property, so a "
                "territory rule is a rule like every other."
            ),
            "a_territory_filter_object": (
                "Add a third filter object that resolves the acting user's territory from a "
                "user record."
            ),
            "a_separate_territory_table": (
                "Model territory-to-price-level as its own connection table, the way Dynamics "
                "models the connection role."
            ),
        },
        "chosen": "deal_property_filter",
        "rejected_because": (
            "The specification's filter builder is a deal-property builder: it configures "
            "deal properties and company properties and has no third object. A territory on "
            "this product's deal payload is a deal property, so a territory rule is a rule like "
            "every other and a team adding a territory field needs no change here. "
            "a_territory_filter_object invents an object the flow does not offer and gives it "
            "a resolution rule the research does not state. a_separate_territory_table is the "
            "Dynamics data model rather than the HubSpot one, and this workflow's own "
            "data_sources list names both, so recording the connection role name on the "
            "vocabulary route keeps the vendor's term available without building the other "
            "vendor's schema."
        ),
        "cost_of_rejection": (
            "The territory's own field names are this build's, served as "
            "TERRITORY_FIELDS so a deployment that spells it differently can be told which "
            "names are read."
        ),
        "jev_audit_id": None,
        "jev_confidence": None,
        "exposed_on": "vocabulary.dynamics.territory_fields",
    },
    {
        "id": "PRICE_BOOKS_ARE_REFERENCED_NOT_RESOLVED",
        "question": (
            "WF-087 provisions the price book catalogue and has not shipped. Does this "
            "workflow require it?"
        ),
        "sourced": [
            "Depends on #121 (WF-087, Curate a product and price-book catalogue with tiered pricing)",
            "Without it, the assignment rule has no price book to assign and no products for "
            "the scoped product lookup, so the rule can be saved but never fires usefully.",
        ],
        "options": {
            "reference_only": (
                "A rule and an override name a price book by id or by name. The catalogue is "
                "read when it exists, for a label, and never required."
            ),
            "hard_dependency": (
                "Refuse to save a rule whose price book cannot be resolved against the catalogue."
            ),
            "wait_for_the_catalogue": "Build nothing until WF-087 merges.",
        },
        "chosen": "reference_only",
        "rejected_because": (
            "A hard dependency on an unmerged sibling is a workflow with an empty page and "
            "unwritable tests, which is the failure WF-086 already documented when it read the "
            "catalogue as optional. The reference is what the data flow actually needs: it "
            "writes a price book onto the deal, and it is the scoped product lookup that needs "
            "the catalogue, and that lookup belongs to the workflow that owns it."
        ),
        "cost_of_rejection": (
            "Before WF-087 lands, a rule can be saved and can assign, and the products browsable "
            "under the assigned book are not yet scoped by this workflow. The vocabulary route "
            "reports whether a catalogue collection was found, so the page can say so rather "
            "than implying one exists."
        ),
        "jev_audit_id": None,
        "jev_confidence": None,
        "exposed_on": "GET /summary, in catalogue_found",
    },
    {
        "id": "FILTER_GROUPS_ARE_ONE_LEVEL",
        "question": "The source says and/or groups. Are the groups nested?",
        "sourced": [
            "configure deal-property filters (and / or groups)",
        ],
        "options": {
            "one_level": (
                "Each rule has one list of filters and one mode, all or any. An or group is a "
                "rule whose mode is any."
            ),
            "nested_groups": (
                "A recursive expression tree, so a rule can say (A and B) or (C and D)."
            ),
            "or_only_at_the_top": ("One list of and-groups, with an or between them."),
        },
        "chosen": "one_level",
        "rejected_because": (
            "The flow builds its groups with '+ Add filter' and the saved shape this product "
            "stores is arbitrary JSON, so a caller who genuinely needs (A and B) or (C and D) "
            "can write two rules and get the same pricing. A recursive language is a thing a "
            "seller has to learn before they can write the first rule, and the flow this is "
            "built from does not present one."
        ),
        "cost_of_rejection": (
            "Two rules where the research would have had one. Two rules that both match produce "
            "ASSIGNMENT_NEEDS_CHOICE rather than an automatic assignment, which is a visible "
            "and correct outcome and is the documented cost of the alternative."
        ),
        "jev_audit_id": None,
        "jev_confidence": None,
        "exposed_on": "rules.matchMode, served by vocabulary.filters.match_modes",
    },
)


def count() -> int:
    """How many decisions this module records."""
    return len(_DECISIONS)


def describe() -> list[dict[str, Any]]:
    """Every decision with its options, its rejection and its audit."""
    return [dict(entry) for entry in _DECISIONS]


def describe_one(decision_id: str) -> dict[str, Any] | None:
    """One decision, or ``None`` rather than a guess."""
    for entry in _DECISIONS:
        if entry["id"] == decision_id:
            return dict(entry)
    return None


def multiple_matches() -> dict[str, Any]:
    """The multiple-match reading on its own, for a page to show beside a deal.

    Served separately rather than only inside the list of eight, because it is the one
    decision a seller meets on their first deal and the one whose answer is "nothing
    was written", which needs the evidence next to it.
    """
    entry = describe_one("MULTIPLE_MATCHES_NEED_A_CHOICE")
    assert entry is not None  # the tuple above always holds this id
    return {
        "code": vocab.ASSIGNMENT_NEEDS_CHOICE,
        "outcome": vocab.ASSIGNMENT_NEEDS_CHOICE,
        "chosen": entry["chosen"],
        "jev_audit_id": entry["jev_audit_id"],
        "jev_confidence": entry["jev_confidence"],
        "sourced": list(entry["sourced"]),
        "why": entry["why_one_rule_satisfies_both"],
        "rejected": entry["rejected_because"],
        "cost_of_rejection": entry["cost_of_rejection"],
    }


def override_removes_lines() -> dict[str, Any]:
    """The destructive-override reading on its own, for a page to show beside the override."""
    entry = describe_one("OVERRIDE_REMOVES_THE_PREVIOUS_BOOKS_LINES")
    assert entry is not None  # the tuple above always holds this id
    return {
        "quote": vocab.LINE_ITEMS_REMOVED_QUOTE,
        "chosen": entry["chosen"],
        "jev_audit_id": entry["jev_audit_id"],
        "jev_confidence": entry["jev_confidence"],
        "why": entry["derived_detail"],
        "rejected": entry["rejected_because"],
    }
