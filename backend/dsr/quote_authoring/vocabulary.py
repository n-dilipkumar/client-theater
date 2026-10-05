"""The words WF-086 speaks, in one place.

Every status, discount type, module key and collection name this workflow uses is
declared here rather than written as a literal at its use site. The reason is the
one this repository keeps relearning: a literal at the use site is a value the
vocabulary endpoint cannot report, so a reviewer cannot tell a deliberate
decision from a typo.

``vocabulary()`` is served over HTTP so the page reads its words from the server
instead of hard-coding them. A change to the rules then cannot leave the page
calling a state the API does not serve.

The ``not_implemented`` list
---------------------------

The research names several things an API cannot do. They are recorded here
rather than silently absent, because a reader who cannot tell "we decided not
to" from "we forgot to" has to open the source, and that is a cost paid every
time the question comes up. Each entry says what the research recorded and what
this feature does instead.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

#: The quote itself. One record per quote, carrying its header, its module list
#: and its recomputed totals.
#:
#: The name is WF-086's because this workflow provisions the row. Three already
#: merged packages read it and none of them writes it:
#: ``dsr/quoting_proposals`` (WF-093) reads it as the scope a proposal template
#: binds against, ``dsr/quoting_proposals/quote_expiry_vocabulary`` reads it as
#: its source, and ``dsr/renewal_quotes`` reads it read-only. All three name it
#: ``wf086_quote`` and each says in its own source that this workflow provisions
#: it. The rename was put to Jev as audit ``jev-20261005T080141-11308-01758``,
#: which chose ``adopt_the_expected_names`` at confidence 0.96 against
#: ``keep_own_names`` at 0.00. Keeping this workflow's own shorter name would have
#: left three merged readers pointing at a collection nobody writes, which reads
#: as an empty quote rather than as a naming disagreement.
QUOTE_COLLECTION = "wf086_quote"

#: Quote-specific line items. Their record ids are their own, never the deal's.
#: WF-093 matches a row on any of ``quote_id``, ``quote``, ``parent_id`` or
#: ``quote_record_id``, and sorts on ``position`` then ``name``, so this workflow
#: writes ``quote_id`` and ``position`` and the reader finds them.
LINE_ITEM_COLLECTION = "wf086_line_item"

#: The quote template dropdown. Provisioned, not authored through this API.
TEMPLATE_COLLECTION = "quote_template"

#: Where a deal lives in this product. Owned by the CRM workflows; this feature
#: only reads it, except for the amount and the line items on publish.
DEAL_COLLECTION = "crm_deal"

#: Searched in order when a deal is named rather than fetched by id. An alias
#: list rather than one name because the CRM workflows that mirror a deal and
#: the workflows that call it an opportunity do not agree on the spelling, and
#: this feature must read whichever one is present rather than fail.
DEAL_COLLECTION_ALIASES = (DEAL_COLLECTION, "crm_opportunity", "deal", "opportunity")

#: Deal line items, held apart from the deal record. HubSpot associates them
#: through the line item object type, so a separate collection is the honest
#: shape rather than an array inside the deal.
DEAL_LINE_ITEM_COLLECTION = "crm_deal_line_item"
DEAL_LINE_ITEM_COLLECTION_ALIASES = (
    DEAL_LINE_ITEM_COLLECTION,
    "crm_line_item",
    "deal_line_item",
)

#: The product library and its price books. This is WF-087's collection, and it
#: does not exist yet. Every read of it is therefore optional: an empty
#: catalogue degrades the quote to the unit price the deal already carried, and
#: says so in the response rather than failing.
PRODUCT_COLLECTION = "crm_product"
PRODUCT_COLLECTION_ALIASES = (PRODUCT_COLLECTION, "crm_price_book_item", "product")

# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #

#: A quote being written. Line items may change. Nothing has reached the deal.
STATUS_DRAFT = "draft"

#: A quote that has been sent. Its line items are frozen and its totals have
#: been written to the deal amount.
STATUS_PUBLISHED = "published"

STATUSES = (STATUS_DRAFT, STATUS_PUBLISHED)

# --------------------------------------------------------------------------- #
# Discount
# --------------------------------------------------------------------------- #

#: A percentage off the unit price. The research names both forms.
DISCOUNT_PERCENTAGE = "percentage"

#: A fixed amount off the whole line.
DISCOUNT_CURRENCY = "currency"

DISCOUNT_TYPES = (DISCOUNT_PERCENTAGE, DISCOUNT_CURRENCY)

# --------------------------------------------------------------------------- #
# Where a unit price came from
# --------------------------------------------------------------------------- #

#: Carried over from the deal's line item.
PRICE_FROM_DEAL = "deal"

#: Resolved from the product library and its tiers.
PRICE_FROM_CATALOG = "catalog"

#: Typed by the seller on the quote, with no catalogue behind it.
PRICE_FROM_MANUAL = "manual"

PRICE_SOURCES = (PRICE_FROM_DEAL, PRICE_FROM_CATALOG, PRICE_FROM_MANUAL)

# --------------------------------------------------------------------------- #
# Quote modules
# --------------------------------------------------------------------------- #

#: A module this product knows how to describe.
KIND_BUILTIN = "builtin"

#: A module a person authored in the quote editor.
KIND_CUSTOM = "custom"

MODULE_KINDS = (KIND_BUILTIN, KIND_CUSTOM)

#: The keys a builtin module may carry. A quote template selects from this set,
#: and the page's module editor offers exactly these.
BUILTIN_MODULE_KEYS = ("header", "line_items", "totals", "terms", "signatures")

#: Written to every quote created from a template. It mirrors HubSpot's
#: hs_template_type of CPQ_QUOTE, the value that tells the CRM this is a
#: configurable quote rather than a fixed document.
QUOTE_TEMPLATE_TYPE = "CPQ_QUOTE"

#: HubSpot writes hs_title and hs_expiration_date on create. The same two
#: fields, spelled the way this product spells them, because a record envelope
#: is JSON and there is no property table to conform to.
HEADER_FIELDS = ("title", "expires_on")

#: Fields a merged reader looks for under a second name, carried beside this
#: workflow's own.
#:
#: WF-093's quote view reads ``deal_name or deal``, ``company_name or company``,
#: ``currency_label or currency``, and ``expiration_date``. This workflow writes
#: ``deal_id``, ``account``, ``currency`` and ``expires_on``, so three of those
#: four lookups would resolve to empty and a proposal would render a blank
#: counterparty.
#:
#: These are duplicated values, not aliases, and that is a cost worth stating
#: plainly: two names for one figure can drift. The alternative is a merged
#: reader's expectations dictating this workflow's vocabulary, which is the
#: coupling the plugin contract exists to avoid. The drift is bounded because
#: :func:`dsr.quote_authoring.engine.QuoteEngine._recompute` rewrites all of them
#: from the same derived values on every write, so there is no path that updates
#: one and not the others.
READER_SYNONYMS = {
    "deal_name": "deal_name",
    "company_name": "company_name",
    "currency_label": "currency_label",
    "expiration_date": "expiration_date",
    "issue_date": "issue_date",
}

# --------------------------------------------------------------------------- #
# Refusal reasons
# --------------------------------------------------------------------------- #

#: The research records that it "isn't possible to create or add custom coded
#: modules to a quote using the API". A request to author coded module content
#: through this API is therefore refused with this reason rather than stored.
REASON_CUSTOM_MODULE_NOT_API_AUTHORABLE = "custom_module_not_api_authorable"

#: A custom module is a real thing a person can build in the editor, so it is
#: accepted. It must say where it came from, because nothing else in the product
#: can tell an editor-authored module from one an API invented.
REASON_CUSTOM_MODULE_NEEDS_UI_PROVENANCE = "custom_module_needs_ui_provenance"

#: A builtin module key that is not in :data:`BUILTIN_MODULE_KEYS`.
REASON_MODULE_KEY_UNKNOWN = "module_key_unknown"

#: Publishing a quote that has already been published.
REASON_ALREADY_PUBLISHED = "already_published"

#: Publishing a quote whose expiration date has passed.
REASON_QUOTE_EXPIRED = "quote_expired"

#: Publishing a quote with no line items. Its total contract value is zero, so
#: publishing it would write zero onto the deal.
REASON_QUOTE_IS_EMPTY = "quote_is_empty"

#: Changing the line items of a published quote.
REASON_QUOTE_FROZEN = "quote_frozen"

#: The deal a quote claims does not exist in any known collection.
REASON_DEAL_NOT_FOUND = "deal_not_found"

# --------------------------------------------------------------------------- #
# The documented rules
# --------------------------------------------------------------------------- #


def totals_contract() -> dict[str, str]:
    """How each figure is derived, in words.

    Served rather than described in a comment, because a figure whose meaning is
    only in the source is a figure the page cannot label and a reviewer cannot
    check against the research.
    """
    return {
        "subtotal": "The sum of quantity times unit price over every line, before any discount.",
        "discount": "The sum of every line discount.",
        "tax": "The sum of each line's net amount times its tax rate. The net amount is the "
        "line subtotal minus the line discount, so a discount reduces the tax as well.",
        "total": "Subtotal minus discount plus tax.",
        "future_payments": "The sum of the scheduled payments dated after today.",
        "total_contract_value": "Total plus future payments. This is the only figure written back "
        "to the deal amount.",
        "rounding": "Every figure is rounded half up to two decimals, at the line and at the total.",
    }


def not_implemented() -> list[dict[str, str]]:
    """What the research records as impossible, and what this feature does instead."""
    return [
        {
            "item": "Custom-coded quote modules",
            "source": (
                "HubSpot knowledge base, quoted: custom-coded modules can be built in the HubSpot "
                "CMS developer project but \"isn't possible to create or add custom coded modules "
                'to a quote using the API".'
            ),
            "decision": (
                "A quote stores an ordered list of modules. A builtin module's key must come from "
                "the fixed vocabulary. A custom module is accepted only when it declares "
                "authored_via of ui, and a request that asks the API to author coded module "
                "content is refused with the reason custom_module_not_api_authorable."
            ),
        },
        {
            "item": "Product library and tiered price books",
            "source": (
                "This workflow depends on WF-087, which provisions the catalogue. It is not built."
            ),
            "decision": (
                "The catalogue is read when it is present. Every read is optional, so an empty "
                "catalogue degrades a quote to the unit price the deal carried and reports "
                "catalogue_available false rather than failing the write."
            ),
        },
        {
            "item": "Quote template authoring",
            "source": (
                "HubSpot CRM API offers GET and search on quote templates, and no create "
                "endpoint. Templates are created in the user interface."
            ),
            "decision": (
                "Templates are stored as records and are provisioned with authored_via of ui. "
                "There is no route that authors template content the way a person would."
            ),
        },
        {
            "item": "Desktop and mobile preview toggles",
            "source": "HubSpot quote editor offers a desktop and a mobile preview icon.",
            "decision": (
                "The quote stores its modules, so the page can offer one preview. The second "
                "preview is a rendering concern of the quote editor and is not modelled."
            ),
        },
        {
            "item": "Sending the quote to the buyer",
            "source": "HubSpot create and send quotes, Dynamics Activate Quote.",
            "decision": (
                "Publishing is modelled because it is the step that writes back to the deal. "
                "Delivery to the buyer is a vendor channel and nothing is sent."
            ),
        },
    ]


def vocabulary() -> dict[str, Any]:
    """Everything the page needs to label this workflow, and nothing it must guess."""
    return {
        "ticket": "WF-086",
        "statuses": list(STATUSES),
        "discount_types": list(DISCOUNT_TYPES),
        "price_sources": list(PRICE_SOURCES),
        "module_kinds": list(MODULE_KINDS),
        "builtin_module_keys": list(BUILTIN_MODULE_KEYS),
        "template_type": QUOTE_TEMPLATE_TYPE,
        "header_fields": list(HEADER_FIELDS),
        "collections": {
            "quote": QUOTE_COLLECTION,
            "line_item": LINE_ITEM_COLLECTION,
            "template": TEMPLATE_COLLECTION,
            "deal": DEAL_COLLECTION,
            "deal_line_item": DEAL_LINE_ITEM_COLLECTION,
            "product": PRODUCT_COLLECTION,
        },
        "totals": totals_contract(),
        "refusal_reasons": {
            REASON_CUSTOM_MODULE_NOT_API_AUTHORABLE: (
                "A custom coded module cannot be added through an API. Build it in the quote "
                "editor and record it here."
            ),
            REASON_CUSTOM_MODULE_NEEDS_UI_PROVENANCE: (
                "A custom module must declare authored_via of ui, so nothing here can be "
                "mistaken for a module the editor built."
            ),
            REASON_MODULE_KEY_UNKNOWN: "That is not a builtin module key.",
            REASON_ALREADY_PUBLISHED: "This quote has already been published.",
            REASON_QUOTE_EXPIRED: "This quote's expiration date has passed.",
            REASON_QUOTE_IS_EMPTY: "A quote with no line items cannot be published.",
            REASON_QUOTE_FROZEN: "A published quote's line items cannot change.",
            REASON_DEAL_NOT_FOUND: "No deal record matches that reference.",
        },
        "not_implemented": not_implemented(),
    }
