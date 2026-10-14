"""WF-130: generated, personalised collateral from deal data.

A seller describes a deal, or supplies the CRM record, and the platform
assembles a personalised document or deck - demos, docs and a mutual action
plan, populated from the deal - which the seller reviews and edits and then
publishes to the room. A repeat deal starts from a template, not a blank page.

The specification is ``docs/research/digital-sales-room-workflows/wf/WF-130.md``,
quoted in full on issue #142. It is nine vendor pages plus two GitHub API
records, so it names the surfaces and the values of almost none of them. Every
value the evidence does not give is a decision in :mod:`.vocabulary`, not a
silent substitution.

==============================  ===============================================
Researched behaviour            Where it lives
==============================  ===============================================
AI content generation           :func:`.rules.assemble`
AI document generation          :func:`.rules.render_section`
template, not a blank page      :data:`.vocabulary.DEFAULT_TEMPLATES`
personalised from the deal      :func:`.rules.merge_tokens`
synced sections                 :func:`.rules.render_section`, :meth:`.book.CollateralBook.refresh_from_asset`
hidden sections revealed        :func:`.rules.is_revealed`, :func:`.rules.apply_stage`
publish to the room             :meth:`.book.CollateralBook.publish`, plus the
                                ``dsr.pages`` seam the feature module calls
==============================  ===============================================

The assembly engine is the rules path. The data flow says "LLM **or** rules
assembly"; the rules path is the one a test can assert without a network call,
so it ships and no LLM is called. See ``DECISIONS['assembly_engine']`` and
``DECISIONS['llm_provider']``.

No migration, no typed column, no required field. Three open collections of
arbitrary JSON (collateral, source assets, events), filtered with ``find()`` over
dotted paths. A team that adds ``buyer_group`` to a block stores it the same day.
"""

from __future__ import annotations

from .book import MAX_ROWS, CollateralBook
from .errors import (
    CollateralError,
    CollateralNotFound,
    CollateralRefused,
    CollateralValidationError,
)
from .rules import (
    apply_stage,
    assemble,
    is_revealed,
    merge_tokens,
    normalise_deal,
    normalise_section_spec,
    normalise_template,
    render_section,
    stage_rank,
    summarise,
    token_values,
    visible_section_keys,
)
from .vocabulary import (
    ASSET_COLLECTION,
    BRAND_TOKENS,
    COLLATERAL_COLLECTION,
    DEAL_STAGES,
    DECISIONS,
    DEFAULT_STAGE,
    DEFAULT_TEMPLATE_KEY,
    DEFAULT_TEMPLATES,
    EVENT_COLLECTION,
    EVENT_KINDS,
    KINDS,
    LIVE_STATE,
    NOT_IMPLEMENTED,
    ROOM_REF,
    SECTION_KINDS,
    STATES,
    TOKENS,
    describe as describe_vocabulary,
    template as shipped_template,
)

__all__ = [
    "ASSET_COLLECTION",
    "BRAND_TOKENS",
    "COLLATERAL_COLLECTION",
    "CollateralBook",
    "CollateralError",
    "CollateralNotFound",
    "CollateralRefused",
    "CollateralValidationError",
    "DEFAULT_STAGE",
    "DEFAULT_TEMPLATES",
    "DEFAULT_TEMPLATE_KEY",
    "DEAL_STAGES",
    "DECISIONS",
    "EVENT_COLLECTION",
    "EVENT_KINDS",
    "KINDS",
    "LIVE_STATE",
    "MAX_ROWS",
    "NOT_IMPLEMENTED",
    "ROOM_REF",
    "SECTION_KINDS",
    "STATES",
    "TOKENS",
    "apply_stage",
    "assemble",
    "describe_vocabulary",
    "is_revealed",
    "merge_tokens",
    "normalise_deal",
    "normalise_section_spec",
    "normalise_template",
    "render_section",
    "shipped_template",
    "stage_rank",
    "summarise",
    "token_values",
    "visible_section_keys",
]
