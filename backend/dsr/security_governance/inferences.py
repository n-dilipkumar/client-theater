"""Every judgement call WF-073 made, with the alternative it rejected.

The specification for this workflow instructs an implementer directly: "An
implementer who needs a flow the evidence does not contain must derive it and record
the derivation, not assume it." This module is that record.

Each entry names the open question, the evidence that left it open, the options, the
one this build took, and - the part that matters - what the rejected options would
have cost. A derivation with no rejected alternative recorded is a guess wearing a
derivation's clothes, and a reviewer cannot tell the two apart.

The HTTP layer serves this table at ``GET /api/wf-073/decisions`` so the record is
readable by whoever reviews the feature, rather than buried in a docstring that
nobody opens. ``GET /api/wf-073/decisions/{id}`` returns one.
"""

from __future__ import annotations

from typing import Any

DECISIONS: dict[str, dict[str, Any]] = {
    "INFERRED_ACCESS_CONTROLS_PANEL": {
        "question": (
            "The specification marks the link access-controls panel as inferred. What "
            "exactly does this build put in it?"
        ),
        "left_open_by": (
            "The specification's own product-surfaces line: \"Link access-controls panel "
            '(Confidential view, Screenshot protection) `[inferred from field set]`". The '
            "inference marker is the specification's, not this build's."
        ),
        "options": {
            "two_toggles": (
                "Two labelled switches, one per flag, each with the vendor's flag name "
                "and the vendor's own description beside it."
            ),
            "no_panel": (
                "Serve the flags only over the API and let the seller toggle them from "
                "the command line, which is where the specification's CLI surface lives."
            ),
            "combined_toggle": (
                "One switch that turns both controls on together, on the reasoning that "
                "a seller who wants confidentiality wants both."
            ),
        },
        "chosen": "two_toggles",
        "rejected_because": (
            "The evidence names two independent booleans, two independent CLI flags with "
            "their own on|off spellings, and an API that creates one and patches either. A "
            "combined toggle would make a state the evidence supports - confidential view "
            "on, screenshot protection off - unreachable from the page. Shipping no panel "
            "was rejected because the specification names the panel as a product surface, "
            "and a control a reviewer cannot see is a control nobody reviews."
        ),
        "cost_of_the_choice": (
            "The panel's layout, order and placement are not researched either, so only "
            "its contents are a derivation and its appearance follows the design system "
            "rather than a vendor screenshot."
        ),
    },
    "DERIVED_BAND_FRACTION": {
        "question": "How narrow is the focus band?",
        "left_open_by": (
            'The specification says only "a narrow band of each page at a time" and, in '
            'the user flow, "content only resolves inside the focus band". It names no '
            "number, in either the flow or the evidence."
        ),
        "options": {
            "fifth": "One fifth of the page's height is sharp at a time.",
            "quarter": "One quarter of the page's height is sharp at a time.",
            "tenth": "One tenth of the page's height is sharp at a time.",
        },
        "chosen": "fifth",
        "rejected_because": (
            "A tenth is narrow enough to stop a whole-page capture but too narrow to read a "
            "line of body text without it being split as the band moves, which would make "
            "the control annoying rather than protective and would push a seller to turn it "
            "off. A quarter reads a whole paragraph sharply at once, which weakens the one "
            'property the specification states the control has: that "a single screenshot '
            'cannot capture a whole page" - a quarter page is not a whole page, so the '
            "letter of the claim survives, but the spirit of it is a paragraph, and a "
            "paragraph is what a buyer shares. A fifth keeps both: the sharp region is too "
            "narrow to be the unit of sharing and wide enough to read."
        ),
        "cost_of_the_choice": (
            "The number is a derivation from two sentences, not a measured reading speed. A "
            "reviewer who prefers another value changes one constant and the arithmetic "
            "follows, because the fraction and the overlap are the only two numbers in the "
            "geometry."
        ),
    },
    "DERIVED_BAND_OVERLAP": {
        "question": "How much do adjacent focus bands overlap?",
        "left_open_by": (
            'The specification names a band but no overlap. It says content "only resolves '
            'inside the focus band", which implies boundaries, and says nothing about what '
            "happens at one."
        ),
        "options": {
            "no_overlap": "Bands tile the page end to end with no overlap.",
            "third": "Each band overlaps its neighbour by a third of its height.",
            "half": "Each band overlaps its neighbour by half its height.",
        },
        "chosen": "third",
        "rejected_because": (
            "No overlap makes the boundary between two bands a hard edge, and a reader "
            "scrolling across it watches a line of text disappear and reappear. Half an "
            "overlap puts two full bands sharp simultaneously for half the page, which "
            "quietly doubles what a single capture can get and undoes the work the fraction "
            "did. A third keeps the text continuous through the transition while leaving "
            "exactly one band sharp at any position."
        ),
        "cost_of_the_choice": (
            "A third overlap means the stack of bands is longer than the page by a factor of "
            "one and a half, so a long document yields more bands than a reader scrolls "
            "through. That is metadata rather than payload, so it costs a few hundred bytes "
            "per page and no glyphs."
        ),
    },
    "DERIVED_SMALL_PAGE": {
        "question": "What does confidential view do to a page too short to have an out-of-band region?",
        "left_open_by": (
            "Nothing in the specification. It describes a band of a page and does not "
            "consider a page shorter than one band."
        ),
        "options": {
            "blur_all": "A short page has no sharp band, so the viewer sees nothing sharp.",
            "short_page_whole": (
                "A page shorter than one band is delivered whole, with no blurred region."
            ),
            "pad_to_band": "Extend the page with whitespace until it is long enough to band.",
        },
        "chosen": "short_page_whole",
        "rejected_because": (
            "Blurring all of it would show a buyer an empty document and read as a broken "
            "product, and padding with whitespace would invent page height the document does "
            "not have so that a control has something to hide. Delivering a short page whole "
            "is the honest answer: there is no out-of-band region on it, so there is nothing "
            "this control can withhold. The response says so in a ``reason`` field rather "
            "than returning the geometry as though banding had happened."
        ),
        "cost_of_the_choice": (
            "A seller who turns confidential view on and has one-page documents gets no "
            "effect from it. The page states the threshold and names that case, so the seller "
            "can see it rather than conclude the control is broken."
        ),
    },
    "DERIVED_DELIVERY_SHAPE": {
        "question": "The specification names two delivery shapes and chooses neither. Which is built?",
        "left_open_by": (
            "The specification's extensibility note: \"Both are expressible in an open-source "
            "stack as (a) a viewport-driven tile/shard renderer for confidential view and (b) "
            "a `keydown`/`visibilitychange` capture guard plus a `blur` on out-of-viewport "
            'tiles for screenshot protection." Named, not chosen - the issue quotes it as a '
            "decision the research left open."
        ),
        "options": {
            "viewport_bands": (
                "One vertical stack of overlapping bands per page. The band containing the "
                "viewer's scroll position is delivered sharp; the others are delivered "
                "blurred, and the blurred ones carry no glyphs at all."
            ),
            "tile_shards": (
                "A two-dimensional grid of tiles, sharp only for the tiles the viewport "
                "overlaps. Finer control near the edges, and four times as many regions to "
                "deliver and to compute."
            ),
        },
        "chosen": "viewport_bands",
        "rejected_because": (
            "Both satisfy the specification's stated property, which is that out-of-band "
            "content never arrives sharp. The difference is what a reader sees while the "
            "control is on. A document is read top to bottom, so a horizontal band follows "
            "the reading direction and the reader's own scroll decides which band is sharp - "
            "the control and the gesture are the same gesture. A tile grid sharpens a "
            "rectangle that tracks the viewport, which for a text document puts sharp lines "
            "on the left and right edges with blurred text between them mid-paragraph, so the "
            "reader loses lines rather than pages. The grid is the better shape for a "
            "spreadsheet or a plan and the worse shape for prose, and the specification names "
            "documents."
        ),
        "cost_of_the_choice": (
            "A wide viewport gets a band as tall as the page is short enough to be one band "
            "tall, so a short page on a tall screen is the whole page sharp. That case is the "
            "one above, and the geometry reports it rather than hiding it. Both shapes are "
            "kept in the vocabulary, so a later workflow can add the grid beside this one "
            "without changing this one's data."
        ),
    },
    "DERIVED_SHORTCUT_LIST": {
        "question": "Which capture shortcuts does the control intercept?",
        "left_open_by": (
            'The specification says "common screenshot / screen-recording shortcuts while '
            'viewing" and enumerates none of them. The CLI flag description says the same.'
        ),
        "options": {
            "blockable_only": (
                "List only the keys a page can intercept, and say the control blocks those."
            ),
            "full_list_with_honest_flags": (
                "List the capture shortcuts including the ones a page cannot intercept, and "
                "mark each one with whether it can be."
            ),
            "no_list": "Intercept a fixed set silently and report only a count.",
        },
        "chosen": "full_list_with_honest_flags",
        "rejected_because": (
            "Listing only the blockable keys would understate what a buyer can do, and the "
            "omission is exactly the one that matters: Print Screen cannot be intercepted by "
            'any web page, so a list without it would let a seller read "this link blocks '
            'screenshots" as true when the very first thing a buyer tries still works. A '
            "silent count is worse, because a number with no names cannot be audited. Naming "
            "every capture shortcut with a blockable flag answers the question a seller is "
            "actually asking, which is which of them this stops."
        ),
        "cost_of_the_choice": (
            "The list is derived from each operating system's default bindings rather than "
            "from the research, so it is complete for the platforms named and silent about "
            "the ones not. A binding a platform changes later would not be caught by it. The "
            "response marks every entry ``blockable``, so the stale one would read as such "
            "rather than as a guarantee."
        ),
    },
    "DERIVED_CAPTURE_REPORT": {
        "question": "What does this product record when the viewer's browser reports a capture attempt?",
        "left_open_by": (
            'The specification lists "client browser input events" among the data sources '
            "for screenshot protection, and says nothing about what follows an attempt."
        ),
        "options": {
            "count_only": "Keep a counter per link and record nothing about the attempt itself.",
            "attempts_recorded": (
                "Record one row per attempt, with the shortcut that matched, whether it could "
                "be blocked, and when it happened."
            ),
            "warn_only": "Show the viewer a notice and store nothing.",
        },
        "chosen": "attempts_recorded",
        "rejected_because": (
            "A count cannot be audited and a notice leaves no evidence, and this product's "
            "guarantee is that the audit row is written in the same transaction as the "
            "change - a governance control whose only record is a number nobody can trace is "
            "the one kind of record that defeats the purpose. One row per attempt keeps the "
            "chain, and each row names the shortcut and whether this product could intercept "
            "it, so the log cannot be read as a claim that every attempt was stopped."
        ),
        "cost_of_the_choice": (
            "One row per attempt means a buyer pressing the same key repeatedly writes "
            "repeatedly. That is left as it is on purpose: a rate limit would be a number "
            "the research does not supply, and a guessed cap that dropped rows would make the "
            "log understate the attempts, which is the failure mode the audit guarantee "
            "exists to prevent."
        ),
    },
    "DERIVED_ROOM_SCOPE": {
        "question": "How is a link identified, given that the specification writes about a Link row and this product's links are per room?",
        "left_open_by": (
            "The specification names ``Link`` rows and ``/v1/links`` endpoints, and this "
            "product's core dataset has rooms and documents rather than a Link table. The "
            'issue records the same gap: "No pending workflow in the shard packet declares '
            "a provisioning claim for a link object, so no order is claimed here. This "
            'workflow owns its own two fields inside records.data."'
        ),
        "options": {
            "own_link_rows": (
                "Store a wf073_confidential_link record per governed link, room-scoped, and "
                "treat it as this workflow's own link object."
            ),
            "adopt_other_rows": (
                "Read and write the flags on WF-069's wf069_gated_link records instead."
            ),
            "no_link_object": "Store one settings record per room rather than per link.",
        },
        "chosen": "own_link_rows",
        "rejected_because": (
            "Adopting WF-069's rows would couple this workflow to another ticket's schema "
            "and its idea of what a link is, and WF-069's own rules name these two fields as "
            "belonging to a confidentiality workflow rather than to the gate - it stores them "
            "and does not enforce them. A settings record per room would make the two "
            "controls room-wide, which is not what the specification describes: both are "
            "properties of a link, so one room can carry a governed link and an open one at "
            "the same time."
        ),
        "cost_of_the_choice": (
            "Two workflows can now hold rows that both call themselves a link, and a reader "
            "looking for one seller's link settings has to know which workflow wrote it. That "
            "is why every collection is namespaced with the ticket, and why the page states "
            "the workflow's own link count rather than claiming a product-wide one."
        ),
    },
    "DERIVED_URL_STABILITY": {
        "question": 'What exactly does "the URL and existing viewers are unaffected" mean for a toggle?',
        "left_open_by": (
            'The user flow says a toggle is "``links update --confidential-view on|off`` / '
            "``--screenshot-protection on|off`` - the URL and existing viewers are "
            'unaffected", and names no mechanism.'
        ),
        "options": {
            "push_to_open_viewers": (
                "Push the new setting to every viewer holding the link right now, by holding a "
                "session registry."
            ),
            "read_per_request": (
                "Read the two flags on every viewer request, so a viewer already looking at the "
                "link sees the new setting on their next request and nobody is pushed anything."
            ),
        },
        "chosen": "read_per_request",
        "rejected_because": (
            'The specification says the controls are continuous "for the life of the view '
            'session" and that they are inherited by every link created from a preset - a '
            "property of the link, read when it is used. A push registry would make a toggle "
            "depend on this product holding live connections, would need a new collection to "
            "track who is connected, and would have to define what happens to a viewer who "
            "never reconnects. Reading per request needs none of that and gives the stronger "
            "guarantee: there is no cached copy of the setting for a toggle to miss."
        ),
        "cost_of_the_choice": (
            "A viewer who is mid-page when the toggle happens sees the old setting until their "
            "next band resolves, which is at most one scroll. The specification calls the URL "
            "and existing viewers unaffected, and this reading keeps that promise literally: "
            "nothing a viewer holds is discarded."
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
