"""WF-130: the vocabulary of generated, personalised collateral, and the
decisions the research left open.

The research for this workflow (``docs/research/digital-sales-room-workflows/wf/
WF-130.md``, quoted on issue #142) is nine vendor pages plus two GitHub API
records. It names the surfaces - AI content generation, AI document generation,
company-wide room templates, synced sections, hidden sections revealed as the
deal progresses, version history - and it names the *values* of none of them.
So this module keeps two kinds of constant apart:

* the **sourced** half - the two artefact kinds, the notion of a template, the
  synced section and the revealed section - is quoted from the evidence;
* the **decided** half - the deal-stage vocabulary, the section kinds, the
  personalisation token set, the readiness rule - is collected in
  :data:`DECISIONS` with the alternative each one rejected, served at
  ``GET /api/wf-130/decisions``, and marked as a decision rather than a port.

Nothing here imports anything but the standard library. The store-facing half
lives in :mod:`.book`.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections. Every feature shares one ``records`` table, so each name is
# namespaced per ticket and ``find()`` matches on the collection first.
# --------------------------------------------------------------------------- #

#: One generated artefact: a document or a deck, with its content blocks.
COLLATERAL_COLLECTION = "wf130_collateral"

#: One entry of the seller's content library (a case study, a deck, pricing).
#: A synced section draws its body from here by ``key``.
ASSET_COLLECTION = "wf130_source_asset"

#: One generation, edit, publish, withdrawal or refresh. The spec's
#: "auto-refresh content in every live room when the source asset changes".
EVENT_COLLECTION = "wf130_collateral_event"

#: The payload-side twin of the envelope's ``room_id``. ``room_id`` is stripped
#: from ``data`` before the dynamic index is built, so a payload that stored its
#: room there would be unfilterable by ``find()``. Sibling workflows keep the
#: same twin key for the same reason.
ROOM_REF = "room_ref"

#: Keys the store owns. A payload that sets them changes nothing.
RESERVED_KEYS = frozenset(
    {"id", "collection", "room_id", "revision", "created_at", "updated_at", "deleted_at"}
)

# --------------------------------------------------------------------------- #
# The artefact
# --------------------------------------------------------------------------- #

#: The two artefact kinds the evidence names: a document and a deck.
KINDS: tuple[str, ...] = ("document", "deck")

#: The artefact lifecycle. ``draft`` is the assembled, un-reviewed artefact;
#: ``published`` is live in the room; ``withdrawn`` is taken back and accepts no
#: further edit, which is the non-success state the seeder prints.
STATES: tuple[str, ...] = ("draft", "published", "withdrawn")

#: A published artefact is "live in the room". The spec's auto-refresh pushes a
#: changed source asset across "all active deals", and this is what "active"
#: means here: published, not withdrawn.
LIVE_STATE = "published"

# --------------------------------------------------------------------------- #
# The sections and the deal stages
# --------------------------------------------------------------------------- #

#: The kinds of content block a template may carry. Each is rendered from text
#: plus, optionally, one source asset, so no block kind needs a binary.
SECTION_KINDS: tuple[str, ...] = (
    "narrative",
    "case_study",
    "pricing",
    "mutual_action_plan",
    "proof",
)

#: The deal-stage vocabulary. The evidence names no stage and no rule, only that
#: reps "unhide pre-made sections like pricing, legal, or security only when the
#: buyer's ready". This is a deliberate five-stage ladder, ordered, so "ready"
#: is a comparison rather than a guess. See :data:`DECISIONS`.
DEAL_STAGES: tuple[str, ...] = (
    "discovery",
    "technical_validation",
    "business_case",
    "procurement",
    "closed_won",
)

#: The stage a deal is assumed to be at when the payload names none.
DEFAULT_STAGE = "discovery"

#: A section that names no ``reveal_at_stage`` is visible at every stage.
ALWAYS_VISIBLE: str | None = None

# --------------------------------------------------------------------------- #
# Personalisation
# --------------------------------------------------------------------------- #

#: The personalisation tokens a template body may carry, written ``{{name}}``.
#: The values come from the deal record, never from a network call.
TOKENS: tuple[str, ...] = (
    "account",
    "contact",
    "contact_first",
    "product",
    "stage",
    "seller",
    "deal_id",
)

# --------------------------------------------------------------------------- #
# Brand
# --------------------------------------------------------------------------- #

#: The semantic token names the collateral's presentation applies. The *values*
#: live in ``frontend/src/index.css``, which is shared and read-only; this
#: workflow reads them at render time and never stores a hex. Storing a hex here
#: would opt the artefact out of every future theme change, which is the one
#: thing the design system forbids.
BRAND_TOKENS: tuple[str, ...] = (
    "accent",
    "background",
    "surface",
    "border-subtle",
    "font-display",
    "font-sans",
    "font-mono",
)

# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #

EVENT_GENERATED = "generated"
EVENT_EDITED = "edited"
EVENT_REVEALED = "revealed"
EVENT_PUBLISHED = "published"
EVENT_WITHDRAWN = "withdrawn"
EVENT_REFRESHED = "refreshed"
EVENT_PAGE_PUBLISHED = "page_published"

EVENT_KINDS: tuple[str, ...] = (
    EVENT_GENERATED,
    EVENT_EDITED,
    EVENT_REVEALED,
    EVENT_PUBLISHED,
    EVENT_WITHDRAWN,
    EVENT_REFRESHED,
    EVENT_PAGE_PUBLISHED,
)

# --------------------------------------------------------------------------- #
# The template set. "Repeat deals start from a template, not a blank page."
# --------------------------------------------------------------------------- #

#: The buyer-visible sections a proposal document carries, in order. The pricing
#: and proof sections are the evidence's own examples of a section a rep unhides
#: "only when the buyer's ready".
PROPOSAL_SECTIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "overview",
        "title": "Overview",
        "kind": "narrative",
        "body": (
            "Prepared for {{account}}. This proposal sets out how we help "
            "{{contact_first}} and the {{account}} team reach their goals."
        ),
    },
    {
        "key": "value",
        "title": "Value proposition",
        "kind": "narrative",
        "body": (
            "{{product}} is configured for {{account}}'s {{stage}} stage, with "
            "{{seller}} as the named point of contact."
        ),
    },
    {
        "key": "case_study",
        "title": "Reference customer",
        "kind": "case_study",
        "body": "A comparable team ran the same evaluation.",
        "asset_ref": "northwind-case-study",
    },
    {
        "key": "pricing",
        "title": "Pricing",
        "kind": "pricing",
        "body": "Commercial terms for {{account}}.",
        "reveal_at_stage": "procurement",
    },
    {
        "key": "plan",
        "title": "Mutual action plan",
        "kind": "mutual_action_plan",
        "body": "The steps {{account}} and we take together to close.",
    },
)

#: The slides a discovery deck carries. Shorter than a document, and the pricing
#: slide is the gated one.
DECK_SECTIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "title",
        "title": "Title",
        "kind": "narrative",
        "body": "{{account}} and {{product}}.",
    },
    {
        "key": "value",
        "title": "Why {{product}}",
        "kind": "narrative",
        "body": "What {{account}} gets, and by when.",
    },
    {
        "key": "proof",
        "title": "Proof",
        "kind": "proof",
        "body": "Evidence that this works.",
        "asset_ref": "northwind-case-study",
        "reveal_at_stage": "business_case",
    },
    {
        "key": "pricing",
        "title": "Pricing",
        "kind": "pricing",
        "body": "The commercials.",
        "reveal_at_stage": "procurement",
    },
)

#: The shipped template set, keyed. A template is data, so a team adds its own
#: through the create route rather than waiting for a code change.
DEFAULT_TEMPLATES: dict[str, dict[str, Any]] = {
    "proposal_document": {
        "key": "proposal_document",
        "name": "Personalised proposal",
        "kind": "document",
        "sections": [dict(section) for section in PROPOSAL_SECTIONS],
    },
    "discovery_deck": {
        "key": "discovery_deck",
        "name": "Discovery deck",
        "kind": "deck",
        "sections": [dict(section) for section in DECK_SECTIONS],
    },
}

#: The template a create request starts from when it names none.
DEFAULT_TEMPLATE_KEY = "proposal_document"

# --------------------------------------------------------------------------- #
# The decisions the issue left open, each with the alternative it rejected.
# --------------------------------------------------------------------------- #

DECISIONS: dict[str, str] = {
    "assembly_engine": (
        "The rules engine is built first. The data flow says 'LLM or rules "
        "assembly'; the rules path is the one the tests can assert without a "
        "network call, so it ships and the LLM path is not built. The rejected "
        "alternative is asking an LLM first, which would make the acceptance "
        "tests depend on a provider that the spec does not name."
    ),
    "llm_provider": (
        "No LLM provider is chosen and none is called. The spec says 'LLM "
        "provider' and nothing more. The rejected alternative is picking one "
        "vendor's API and key, which would invent a dependency the issue's "
        "dependency note does not give."
    ),
    "artifacts": (
        "The generated artefact is stored as JSON content blocks in "
        "records.data. The PDF and PPTX are binaries and the store holds JSON, "
        "so no blob goes into a record. The rejected alternative is storing a "
        "base64 file, which would bloat the audit log and break the "
        "schema-flexible contract. Rendering the JSON to DOCX/PDF/PPTX, and the "
        "pdf.js preview, are out of scope; preview is the room page."
    ),
    "readiness": (
        "'Ready' means the deal's stage is at or past the section's "
        "reveal_at_stage, compared on an ordered five-stage ladder. The "
        "evidence gives no stage and no rule, so the ladder is this build's "
        "decision. The rejected alternative is a boolean the seller flips by "
        "hand, which is the manual unhide the evidence already describes and "
        "adds nothing."
    ),
    "version_history": (
        "In scope as a monotonic version counter on the artefact plus the "
        "immutable room page revision that the publishing seam writes on each "
        "publish. Branch and merge of a collateral is not built. The rejected "
        "alternative is a full branching model, which appears in the product "
        "surface list but nowhere in the data flow."
    ),
    "synced_sections": (
        "A section may name an asset_ref from the content library. A refresh "
        "re-renders every published artefact that carries a synced section, so "
        "the change reaches the room. The rejected alternative is copying the "
        "asset text at generation time and never updating it, which is the "
        "behaviour the evidence calls out as the problem Dock's Synced Sections "
        "exists to fix."
    ),
    "publish_seam": (
        "Publishing to the room goes through dsr.pages.PageService, the seam "
        "White-label Pages (WF-002) already wraps: this feature creates a text "
        "block per visible section and calls the page publish, which writes the "
        "immutable revision. The rejected alternatives are re-implementing the "
        "page and revision writes here, and importing the publishing feature "
        "directly, which the feature host forbids."
    ),
    "crm": (
        "No CRM connection is built and nothing external is read. The evidence "
        "names Salesforce and HubSpot as data sources, but this repository owns "
        "no CRM connection, so the deal record arrives in the request body and "
        "is stored as ordinary JSON. The rejected alternative is an outbound "
        "CRM call, which would make a deterministic generator depend on a "
        "remote system."
    ),
    "brand": (
        "The artefact names the semantic brand tokens it applies and the page "
        "renders with them; the values are read from frontend/src/index.css at "
        "render time and never stored. Copying the hex values into a record "
        "would opt the artefact out of every theme change, which is the "
        "design-system rule."
    ),
    "truncated_quote": (
        "The Walnut evidence quote is truncated in the source ('You review, "
        "edit and p...'). It is quoted only as far as it is given and the "
        "missing words are not reconstructed."
    ),
}

#: What this build deliberately does not do, named so a reviewer does not have
#: to guess whether a gap is a decision or an oversight.
NOT_IMPLEMENTED: tuple[str, ...] = (
    "An LLM assembly path. The rules engine is the implementation.",
    "Rendering content blocks to DOCX, PDF or PPTX, and the pdf.js preview.",
    "A CRM read. The deal record arrives in the request body.",
    "Branch and merge of a collateral; only a version counter and page revisions.",
    "Object storage for uploaded source assets; an asset carries text, not a file.",
)


def template(key: str) -> dict[str, Any] | None:
    """A shipped template, copied so a caller cannot mutate the constant."""
    found = DEFAULT_TEMPLATES.get(str(key))
    if found is None:
        return None
    return {
        "key": found["key"],
        "name": found["name"],
        "kind": found["kind"],
        "sections": [dict(section) for section in found["sections"]],
    }


def describe() -> dict[str, Any]:
    """The whole accepted vocabulary, as one payload.

    Served at ``GET /api/wf-130/vocabulary`` so the page renders its pickers
    from the same source the rules enforce against, and so the decided half is
    visibly labelled rather than passed off as the research's.
    """
    return {
        "kinds": list(KINDS),
        "states": list(STATES),
        "live_state": LIVE_STATE,
        "section_kinds": list(SECTION_KINDS),
        "deal_stages": list(DEAL_STAGES),
        "default_stage": DEFAULT_STAGE,
        "tokens": list(TOKENS),
        "brand_tokens": list(BRAND_TOKENS),
        "event_kinds": list(EVENT_KINDS),
        "templates": [
            {"key": item["key"], "name": item["name"], "kind": item["kind"]}
            for item in DEFAULT_TEMPLATES.values()
        ],
        "default_template": DEFAULT_TEMPLATE_KEY,
        "collections": {
            "collateral": COLLATERAL_COLLECTION,
            "assets": ASSET_COLLECTION,
            "events": EVENT_COLLECTION,
        },
        "sourced": {
            "kinds": "AI content generation and AI document generation, issue #142",
            "templates": "Company-wide room templates, issue #142",
            "synced_sections": "Dock Synced Sections, issue #142",
            "hidden_sections": "Dock Hidden Sections, issue #142",
        },
        "reused": {
            "audited_store": "dsr.store.RecordStore over dsr.db.audited.AuditedDatabase",
            "publish_seam": "dsr.pages.PageService, wrapped by WF-002",
            "schema": "schema-flexible JSON in records.data; no migration, no typed column",
        },
    }


__all__ = [
    "ALWAYS_VISIBLE",
    "ASSET_COLLECTION",
    "BRAND_TOKENS",
    "COLLATERAL_COLLECTION",
    "DEFAULT_STAGE",
    "DEFAULT_TEMPLATE_KEY",
    "DEFAULT_TEMPLATES",
    "DEAL_STAGES",
    "DECISIONS",
    "EVENT_COLLECTION",
    "EVENT_EDITED",
    "EVENT_GENERATED",
    "EVENT_KINDS",
    "EVENT_PAGE_PUBLISHED",
    "EVENT_PUBLISHED",
    "EVENT_REFRESHED",
    "EVENT_REVEALED",
    "EVENT_WITHDRAWN",
    "KINDS",
    "LIVE_STATE",
    "NOT_IMPLEMENTED",
    "RESERVED_KEYS",
    "ROOM_REF",
    "SECTION_KINDS",
    "STATES",
    "TOKENS",
    "describe",
    "template",
]
