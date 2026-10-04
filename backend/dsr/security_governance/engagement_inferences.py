"""Every judgement call WF-075 made, with the alternative it rejected.

The specification instructs an implementer directly: "An implementer who needs a flow
the evidence does not contain must derive it and record the derivation, not assume
it." This module is that record.

Each entry names the open question, the evidence that left it open, the options, the
one this build took, and what the rejected options would have cost. A derivation with no
rejected alternative recorded is a guess wearing a derivation's clothes.

The HTTP layer serves this table at ``GET /api/wf-075/decisions`` so the record is
readable by whoever reviews the feature, rather than buried in a docstring.
"""

from __future__ import annotations

from typing import Any

from dsr.security_governance import engagement as vocab

DECISIONS: dict[str, dict[str, Any]] = {
    "DERIVED_VIEW_TYPES": {
        "question": "What values can view_type hold?",
        "left_open_by": (
            "The specification names the field four times and never its values: a View "
            'row carries "link, document/dataroom, `viewer_email`, `view_type`, '
            '`viewed_at`, `downloaded_at`, `download_type`". The evidence gives no '
            "enumeration."
        ),
        "options": {
            "three_kinds": "document, dataroom and link, one per thing that can be viewed.",
            "document_only": "Every view is a document view, which is what a dataroom is made of.",
            "free_text": "Accept any string and show what the producer sent.",
        },
        "chosen": "three_kinds",
        "rejected_because": (
            "Free text is the worst of the three here, because the page has to group views "
            "and an unbounded set of groups cannot be rendered as a board. Document-only "
            "loses the distinction the specification itself draws: its product surfaces "
            'name "dataroom/document/link analytics dashboards" as three separate things. '
            "Collapsing them would make the per-kind rate limit and the per-kind cache "
            "indistinguishable, and the drill-down would report a room-level view as a "
            "document view."
        ),
        "cost_of_the_choice": (
            "A producer outside this build that writes a fourth view type would see it "
            "tallied as 'unknown' on the board rather than rejected, because this workflow "
            "is a read path and refuses nothing the store already holds."
        ),
    },
    "DERIVED_DOWNLOAD_TYPES": {
        "question": "What values can download_type hold, and what is the state of a view that downloaded nothing?",
        "left_open_by": (
            "The specification names `download_type` as a field of the View row and then "
            'says only that the fourth user-flow step reads "`view_type`, `viewed_at`, '
            '`downloaded_at`, `download_type`". No enumeration and no statement about '
            "the absence case."
        ),
        "options": {
            "null_when_absent": "No download is stored as a null download_type.",
            "explicit_states": (
                "Three download kinds plus a distinct not_downloaded state that is not a "
                "kind at all."
            ),
            "boolean_only": "Keep just a downloaded yes or no and drop the kind.",
        },
        "chosen": "explicit_states",
        "rejected_because": (
            "A null and an unknown must not render the same, which is what null_when_absent "
            "produces: a page reading a null cannot tell 'this view downloaded nothing' from "
            "'this view downloaded something this build cannot name', and those are different "
            "answers to a rep's question about whether files left the room. A boolean alone "
            "throws away `download_type`, which the specification names as a field a rep "
            "reads. So `not_downloaded` is a separate state that is deliberately not a "
            "download_type value, and cannot be written into the field by accident."
        ),
        "cost_of_the_choice": (
            "Four states is one more for the page to render than a null would be, and the "
            "extra state only earns its place when a producer writes an unrecognised kind."
        ),
    },
    "DERIVED_VERIFICATION_IS_THREE_STATES": {
        "question": "How does the page answer 'was the identity actually proven'?",
        "left_open_by": (
            'The user flow says the rep reads the flag "to confirm the identity was '
            'actually proven (not merely typed in)". The evidence fixes the stored type as '
            "`verified: boolean`. A boolean has two values and the question has three "
            "answers."
        ),
        "options": {
            "boolean_only": "Report true or false, defaulting a missing field to false.",
            "three_states": "verified, unverified and unknown, where unknown means no proof was recorded.",
            "string_enum": "Store verified, pending and rejected as three strings.",
        },
        "chosen": "three_states",
        "rejected_because": (
            "A stored boolean defaulting to false conflates 'we checked and it was not "
            "proven' with 'we never checked', and those are different facts about a buyer. "
            "Storing three strings was rejected because the evidence fixes the stored type "
            "as a boolean, and a migration or a typed column for a team's field is exactly "
            "what this product does not permit: the third state is computed from a stored "
            "boolean plus an absent key, so a team adding a field needs no coordination. "
            "The missing case is also the common one, since a row is written when a buyer "
            "is invited and the proof step usually comes later."
        ),
        "cost_of_the_choice": (
            "The stored field can still hold only two values, so a caller reading "
            "`verified_field` rather than `verification` loses the third answer. The page "
            "reads the word, and the tests assert that it does."
        ),
    },
    "DERIVED_ANONYMOUS_VIEWS_ARE_COUNTED": {
        "question": "Does a view with no visitor email count towards the board?",
        "left_open_by": (
            'The specification says views "never tied to a Visitor record are still '
            'reachable per-link via `GET /v1/links/{id}/views`". It requires them to stay '
            "reachable per link and is silent about whether they enter a room aggregate."
        ),
        "options": {
            "counted": "Every view event enters the totals, addressed or not.",
            "addressed_only": "Only views with an email enter the totals.",
            "separate_column": "Counted, but reported as its own number as well.",
        },
        "chosen": "counted",
        "rejected_because": (
            "Dropping anonymous views would understate engagement, and a seller deciding "
            "whether to chase a buyer would see a link as unopened when an unidentified "
            "browser read it for nine minutes. The specification treats an anonymous view "
            "as a real, reachable view event rather than as noise. They are also reported "
            "separately as `anonymous_views`, so the honest number and the addressable one "
            "are both on the board and a rep can read either."
        ),
        "cost_of_the_choice": (
            "Unique visitors is counted from the addresses behind the views, so an "
            "anonymous view raises the view total without raising it. That is a real "
            "asymmetry on the board and it is stated in the response rather than smoothed "
            "over."
        ),
    },
    "DERIVED_CACHE_SECONDS": {
        "question": "How long does an aggregate stay cached?",
        "left_open_by": (
            'The specification says analytics are "cheap (cached aggregates) so polling '
            'every minute or two is fine" and that a caller should "Cache the response if '
            "you're polling\". It gives no expiry."
        ),
        "options": {
            "one_minute": "Expire after sixty seconds of wall clock.",
            "five_minutes": "Expire after five minutes.",
            "no_expiry": "Cache until the next write.",
        },
        "chosen": "one_minute",
        "rejected_because": (
            "The documentation says polling every minute or two is fine, so an expiry "
            "shorter than the fastest documented poll would recompute on nearly every "
            "request and the cache would buy nothing. Five minutes is shorter than the "
            "cache in the vendor's own description implies and would make a rep watching "
            "a live room see their own view take two minutes to appear. Expiring only on "
            "write was rejected because the tighter per-minute rate limit is documented "
            "for the analytics surface specifically, so the cache has to do its work "
            "between writes rather than depend on them."
        ),
        "cost_of_the_choice": (
            "A view lands and the board does not move for up to sixty seconds. Every "
            "response therefore carries `cached` and `computed_at`, so a poller can tell "
            "a fresh answer from a cached one, and any write clears the cache."
        ),
    },
    "DERIVED_PAGE_SIZE": {
        "question": "How many rows does one read return?",
        "left_open_by": (
            'The specification calls the visitor list "paginated" and the per-link view '
            'list "cursor-paginated", so a page size is part of the contract. It supplies '
            "no number."
        ),
        "options": {
            "fifty": "Fifty rows by default, two hundred at most.",
            "twenty": "Twenty rows by default, a hundred at most.",
            "unbounded": "Return every row the store holds.",
        },
        "chosen": "fifty",
        "rejected_because": (
            "The per-link list is where a long-engaged link earns a large number of rows, "
            "and an unbounded read on that endpoint is the one shape that can take the "
            "process down, which is worse on this surface than anywhere else because the "
            "surface is rate limited more tightly than the rest of the API. Twenty is too "
            "small to be useful in one call for a room with a normal number of viewers."
        ),
        "cost_of_the_choice": (
            "A caller needing more than two hundred rows has to page, and this build "
            "exposes an offset-free page rather than a cursor token, so the cursor in the "
            "vendor's contract is modelled as a reverse-chronological cut. A room with tens "
            "of thousands of views therefore needs the same endpoint called repeatedly, "
            "which is the behaviour the vendor's own paging describes."
        ),
    },
    "DERIVED_FIRST_SEEN_FROM_INVITATION": {
        "question": "What does First Seen mean when a visitor was invited before they ever opened a link?",
        "left_open_by": (
            'The first user-flow step names two columns, "First Seen" and "Last Seen", on '
            "a list of persistent visitors. The Visitor schema gives `invited_at`, "
            "`last_viewed_at` and `total_views` and no `first_seen` field at all."
        ),
        "options": {
            "earliest_view": "First Seen is the earliest view of any of that buyer's links.",
            "invited_at": "First Seen is `invited_at`.",
            "earliest_of_both": "The earlier of `invited_at` and the earliest view.",
        },
        "chosen": "earliest_of_both",
        "rejected_because": (
            "Using `invited_at` alone would report a buyer as first seen on a date they "
            "never looked at anything, which is the same class of error as a verified flag "
            "defaulting true on a typed address: the column would answer a question about "
            "attention using a fact about an invitation. Using the earliest view alone "
            "would push the date later than the buyer's actual first contact with the room. "
            "The earlier of the two is the earliest moment this workflow has any evidence "
            "of contact, and when neither exists it reports nothing rather than the epoch."
        ),
        "cost_of_the_choice": (
            "First Seen is computed by reading that buyer's view rows rather than stored, so "
            "the list costs a pass over the view rows per visitor. That is why the list "
            "carries a researched page size rather than being unbounded."
        ),
    },
}


def describe() -> list[dict[str, Any]]:
    """Every recorded decision, in a stable order."""
    return [{"id": key, **value} for key, value in DECISIONS.items()]


def describe_one(decision_id: str) -> dict[str, Any]:
    """One decision by id, or an empty mapping the HTTP layer turns into a 404."""
    found = DECISIONS.get(decision_id)
    if found is None:
        return {}
    return {"id": decision_id, **found}


def count() -> int:
    return len(DECISIONS)


#: Served with the decisions so a reader can see which states the board reports
#: without having to open the vocabulary route as well.
VERIFICATION_STATES = list(vocab.VERIFICATION_STATES)
