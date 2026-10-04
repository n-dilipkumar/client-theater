"""Every judgement call WF-042 rests on, named and served.

The research fixes the flow, the three APIs, the three paging shapes, the
display-label capability check and the vendor numbers. It leaves five things
open, and this module is where each is recorded rather than left for a reader to
reconstruct from a diff. It is served at the feature's ``/inferences`` route.

Each entry separates the *sourced* half from the *inferred* half, so the line
between "the vendor said this" and "this build decided this" stays visible.
"""

from __future__ import annotations

from typing import Any

from dsr.crm_integration import vocabulary

# --------------------------------------------------------------------------- #
# The sourced half
# --------------------------------------------------------------------------- #

#: The sentence that governs the field-scoped read.
FIELD_SCOPED_QUOTE = (
    "Room issues a read-only, field-scoped CRM query: only the mapped columns, paged."
)

#: The sentence that governs the display labels.
DISPLAY_LABEL_QUOTE = (
    "Room requests the display labels for option columns (stage, status) so the panel "
    'shows "Proposal sent", not the integer 2.'
)

#: The sentence that governs the capability check.
CAPABILITY_QUOTE = (
    'Vendors expose a capability flag for "display-label annotations" that the room uses '
    "when available and falls back to its own option-set map when not."
)

#: The sentence that governs the cache.
CACHE_QUOTE = "Room caches the result per buyer with a short TTL and renders the deal panel."

#: The sentence that governs the automation.
AUTOMATION_QUOTE = "Read-through cache refresh on a room scheduler; nothing pushes."

#: The sentence that governs the extensibility claim.
EXTENSIBILITY_QUOTE = (
    "The read set is derived from the same field map as writes, so a deployment that "
    "adds a field automatically gets it in the room's deal panel with no extra API code."
)

# --------------------------------------------------------------------------- #
# The inferred half
# --------------------------------------------------------------------------- #

#: "Room resolves the buyer's CRM identity (from W1/W2 mapping or a signed token)."
#: The research names two sources and chooses neither, so this build took the
#: room mapping and records why.
IDENTITY_SOURCE = {
    "decision": "room_mapping",
    "question": "The research names two identity sources and chooses neither. Which does this build use?",
    "chosen": "room_mapping",
    "rejected": "signed_token",
    "because": (
        "The room mapping is data this product already owns: the seller's own record of "
        "which buyer sits behind which buyer link. A signed token needs an issuer, a "
        "signing key and a verification path, none of which this workflow provisions, "
        "and inventing a token format here would put a security contract in a display "
        "feature. The mapping needs no new authority."
    ),
    "derivation": (
        "identity.records[].source == 'room_mapping', keyed by (room_id, buyer_email). "
        "Resolution order is an explicit identity id first, then the buyer email, because "
        "an id is what the caller already resolved and a re-read under a different key "
        "would be a second answer to one question."
    ),
    "claims_a_signed_token_would_need": list(vocabulary.SIGNED_TOKEN_CLAIMS),
    "source": "docs/research/raw/crm-integration.md section 9, user_flow step 2",
}

#: "a short TTL" with no number attached.
CACHE_TTL = {
    "decision": vocabulary.CACHE_TTL_SECONDS,
    "question": "The research says 'short TTL' and publishes no number. What does the room use?",
    "chosen": vocabulary.CACHE_TTL_SECONDS,
    "bounds": [vocabulary.CACHE_TTL_MIN_SECONDS, vocabulary.CACHE_TTL_MAX_SECONDS],
    "because": (
        "The panel shows a deal's current stage, so the window has to be short enough "
        "that a seller sees their own change. Five minutes is the largest gap a buyer "
        "would notice as 'stale' on a page nobody refreshes. The floor of 30s and the "
        "ceiling of 3600s are this build's; a room outside them is clamped and the "
        "clamp is reported on the snapshot rather than refused."
    ),
    "deviation_reported": "cache.fallback_fills and cache.state carry it; a clamped TTL is in cache.ttl_seconds",
    "source": "docs/research/raw/crm-integration.md section 9, user_flow step 4",
}

#: Pull or push. The research records both and prefers neither.
REFRESH_MODE = {
    "decision": vocabulary.REFRESH_MODE,
    "question": "Read-through pull or Dataverse change-tracking push?",
    "chosen": vocabulary.REFRESH_MODE,
    "rejected": vocabulary.REFRESH_MODE_ALTERNATIVE,
    "because": (
        "The research states the room's default as 'Read-through cache refresh on a room "
        "scheduler; nothing pushes', so pull is the researched behaviour rather than a "
        "choice made here. Push is a different workflow (W10) and it needs a channel "
        "this one does not open; a room that wants it turns on that workflow and this "
        "read path becomes its refill."
    ),
    "declared": vocabulary.REFRESH_MODE_ALTERNATIVE,
    "source": "docs/research/raw/crm-integration.md section 9, automations",
}

#: Only Salesforce publishes `done`. The other two do not.
DERIVED_DONE = {
    "decision": "derive_done_from_page_length",
    "question": "Dataverse and HubSpot publish no 'done' flag. When is a read finished?",
    "chosen": "a page shorter than the page asked for is the last page",
    "because": (
        "Salesforce states it ('done') and the other two do not. Both publish a "
        "continuation instead, so the room takes the continuation as authoritative and "
        "uses the page length only to answer the case where there is no continuation. "
        "A page exactly as long as the requested limit with no continuation is the one "
        "ambiguous case, and it is resolved towards finished, which is what the vendor's "
        "own behaviour does when it returns fewer records than the limit."
    ),
    "risk": (
        "A vendor that returns a full page and stops without a continuation would be read "
        "as finished. That is recorded on the view model as done with the page length "
        "beside it, so the reading is auditable rather than silent."
    ),
    "source": "docs/research/raw/crm-integration.md section 9, data_flow",
}

#: The room holds the vendor's tables rather than calling them.
LOCAL_SOURCE = {
    "decision": "local_vendor_tables",
    "question": "This product holds no OAuth connection. What does a read actually read?",
    "chosen": "the room's own copy of the vendor's three tables",
    "because": (
        "Connecting an org is a different researched workflow, and a feature that opened "
        "a socket would put a network call in a render path and be untestable. The wire "
        "shapes are the researched ones - totalSize/done/nextRecordsUrl, @odata.nextLink, "
        "paging.next.after, and Dataverse's FormattedValue annotation - so a room pointed "
        "at a live vendor is a change to dsr.crm_integration.sources alone."
    ),
    "enforced": "a row is cut down to the plan's select list before it is serialised",
    "source": "docs/research/raw/crm-integration.md section 9, data_sources",
}

#: The research publishes page ceilings and no default page.
DEFAULT_PAGE = {
    "decision": vocabulary.DEFAULT_PAGE,
    "question": "The research publishes each vendor's page ceiling and no default. What page does a read ask for?",
    "chosen": vocabulary.DEFAULT_PAGE,
    "because": (
        "A ceiling is the largest answer a vendor can give, not the right one to ask for. "
        "Salesforce will return 2,000 rows in one synchronous request, and a panel read "
        "that asks for 2,000 to show one deal pays the vendor's largest-page cost for the "
        "smallest answer. 200 is the research's own HubSpot page maximum, so it is inside "
        "every researched ceiling, and it is the page Dataverse's own example uses ($top=1 "
        "is the same idea taken further)."
    ),
    "clamped_to": "the vendor ceiling, so an elastic table still reads 500 at most",
    "source": "docs/research/raw/crm-integration.md section 9, evidence",
}

#: A free-text condition in a language this package cannot parse.
UNAPPLIED_CONDITIONS = {
    "decision": "report_rather_than_guess",
    "question": "What happens to a filter condition written in one vendor's grammar and issued to another?",
    "chosen": "apply it when it is the structured clause, report it otherwise",
    "because": (
        "SOQL, OData and HubSpot each spell a filter differently, and re-parsing a string "
        "to guess which grammar it was written in would invent a read. A narrower read "
        "that says it was narrowed is safe; a wrong one that looks right is not."
    ),
    "reported_on": "view_model.unapplied_conditions, and the vendor response under the same key",
    "source": "docs/research/raw/crm-integration.md section 9, data_flow",
}

#: The research's own recorded gap, carried here so it is not rediscovered.
RESEARCH_GAP = {
    "decision": "not_built",
    "question": "The research records a gap about Dataverse query options. Does this build need it?",
    "quoted": (
        'The Dataverse "unsupported query options" list ($skip, $search, $format) and the '
        "FetchXml fallbacks were noted in the source but are not needed for the room's read "
        "path and are not expanded here."
    ),
    "because": (
        "The gap is about the room's read path not needing them, and this is the room's "
        "read path. $skip is not used because the room continues with the vendor's own "
        "continuation instead of an offset, and $search and $format are not query options a "
        "panel read depends on."
    ),
    "source": "docs/research/raw/crm-integration.md section 9, gaps",
}

INFERENCES: tuple[dict[str, Any], ...] = (
    IDENTITY_SOURCE,
    CACHE_TTL,
    REFRESH_MODE,
    DERIVED_DONE,
    LOCAL_SOURCE,
    DEFAULT_PAGE,
    UNAPPLIED_CONDITIONS,
    RESEARCH_GAP,
)


def describe() -> dict[str, Any]:
    """Every judgement call, with the sourced sentences it sits beside."""
    return {
        "sourced": {
            "field_scoped_read": FIELD_SCOPED_QUOTE,
            "display_labels": DISPLAY_LABEL_QUOTE,
            "display_label_capability": CAPABILITY_QUOTE,
            "cache": CACHE_QUOTE,
            "automation": AUTOMATION_QUOTE,
            "extensibility": EXTENSIBILITY_QUOTE,
        },
        "inferred": [dict(entry) for entry in INFERENCES],
        "count": len(INFERENCES),
    }


__all__ = [
    "AUTOMATION_QUOTE",
    "CACHE_QUOTE",
    "CAPABILITY_QUOTE",
    "CACHE_TTL",
    "DEFAULT_PAGE",
    "DERIVED_DONE",
    "DISPLAY_LABEL_QUOTE",
    "EXTENSIBILITY_QUOTE",
    "FIELD_SCOPED_QUOTE",
    "IDENTITY_SOURCE",
    "INFERENCES",
    "LOCAL_SOURCE",
    "REFRESH_MODE",
    "RESEARCH_GAP",
    "UNAPPLIED_CONDITIONS",
    "describe",
]
