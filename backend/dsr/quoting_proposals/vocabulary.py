"""Every researched term WF-093 enforces against.

Pricing, quoting and proposals. The research is
``docs/research/raw/quoting-proposals.md`` section 8, quoted in full in issue 164, and
every constant below carries the sentence it came from.

What the specification fixes, and what it leaves open
------------------------------------------------------

The specification fixes four points outright and leaves the mechanism open at every
other joint:

* **The module set.** The template editor configures header, parties, cover letter or
  executive summary, line items with a totals tab, terms, and acceptance. That list is
  :data:`MODULES`.
* **Quote properties beat template settings.** "The quote template's properties, content,
  and associations will be added to the quote as supplemental information, with
  properties set on the quote overriding the quote template's settings."
* **Template changes do not retro-apply.** "Updating your logo and branding won't update
  existing published quotes, only currently drafted quotes and quotes created after
  updating." That is :data:`NO_RETROACTIVE_APPLICATION_QUOTE`.
* **Custom coded modules are not an API surface.** "It isn't possible to create or add
  custom coded modules to a quote using the API. API users can select templates that have
  custom modules included on them." That is :data:`CUSTOM_MODULE_API_LIMIT`.

The specification names three vendor document models and chooses none, and the choice is
not decided by the research. It was put to Jev as audit
``jev-20261004T212018-20336-18854``, which selected :data:`DOCUMENT_MODEL_HTML_JSON` at
confidence 1.00.

Nothing here reads or writes. These are the words, the caps and the evidence.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Ticket-prefixed, which is the uniform convention in this repository: I counted 59
# distinct prefixed collection strings under ``dsr/`` and none of them is a bare noun.
# The host has no collection-collision check, so the prefix is what keeps two features
# from quietly sharing one.

#: The template definitions this workflow owns. A template is layout plus branding plus
#: bindings, never a rendered document.
TEMPLATES = "wf093_template"

#: The brand kits a template associates with. "Associate a Brand with the template
#: (Enterprise); the brand appears top-right in the quote editor and drives logo/brand-kit
#: colours."
BRANDS = "wf093_brand"

#: The rendered document. One row per instantiation. This is "the quote's presentation
#: layer" and it is a stored snapshot rather than a live view, which is what makes the
#: non-retroactive rule work.
DOCUMENTS = "wf093_document"

#: The quote WF-086 provisions. Read as data and never written by this workflow's
#: routes. See ``DERIVED_QUOTE_IS_READ_AS_DATA``, put to Jev as audit
#: ``jev-20261004T212223-11296-43126``, which selected it at confidence 1.00.
QUOTES = "wf086_quote"

#: The quote's priced rows. Also WF-086's, and also read as data.
LINE_ITEMS = "wf086_line_item"

#: WF-087's product and price-book catalogue, which the line-items module resolves
#: against. The issue claims this dependency: "Without it, the template's line-items
#: module and its Totals tab have no priced rows to bind."
PRODUCTS = "wf087_product"

# --------------------------------------------------------------------------- #
# Modules
# --------------------------------------------------------------------------- #
#
# The six the template editor configures, in the order the editor lists them. The order
# is stored per template and a template may reorder or hide any module, so this tuple is
# the default order rather than a constraint on what a template may do.

MODULE_HEADER = "header"
MODULE_PARTIES = "parties"
MODULE_COVER_LETTER = "cover_letter"
MODULE_EXECUTIVE_SUMMARY = "executive_summary"
MODULE_LINE_ITEMS = "line_items"
MODULE_TOTALS = "totals"
MODULE_TERMS = "terms"
MODULE_ACCEPTANCE = "acceptance"

#: The six module kinds the specification's editor names, with the editor's own label for
#: each. ``cover_letter`` and ``executive_summary`` are two kinds rather than one because
#: the specification names them as two: "cover letter / executive summary", and says the
#: second can be generated from line items, deal activities, transcripts, notes and
#: emails.
MODULES: tuple[str, ...] = (
    MODULE_HEADER,
    MODULE_PARTIES,
    MODULE_COVER_LETTER,
    MODULE_EXECUTIVE_SUMMARY,
    MODULE_LINE_ITEMS,
    MODULE_TOTALS,
    MODULE_TERMS,
    MODULE_ACCEPTANCE,
)

MODULE_LABELS: dict[str, str] = {
    MODULE_HEADER: "Header",
    MODULE_PARTIES: "Parties",
    MODULE_COVER_LETTER: "Cover letter",
    MODULE_EXECUTIVE_SUMMARY: "Executive summary",
    MODULE_LINE_ITEMS: "Line items",
    MODULE_TOTALS: "Totals",
    MODULE_TERMS: "Terms",
    MODULE_ACCEPTANCE: "Acceptance",
}

#: The three fields the header module carries, from "header (quote reference label, issue
#: date, expiration date, currency label, PO number, logo)". ``logo`` is a header field
#: rather than a branding token because "Logos can come from the quote branding settings,
#: account branding, or the brand", and the three sources are resolved at merge time.
HEADER_FIELDS: tuple[str, ...] = (
    "quote_reference_label",
    "issue_date",
    "expiration_date",
    "currency_label",
    "po_number",
    "logo",
)

#: The three parties the parties module names: seller, buyer, bill-to.
PARTY_ROLES: tuple[str, ...] = ("seller", "buyer", "bill_to")

PARTY_LABELS: dict[str, str] = {
    "seller": "Seller",
    "buyer": "Buyer",
    "bill_to": "Bill to",
}

#: What the totals tab is made of. The specification names the Totals tab as its own
#: module and does not enumerate its rows, so these are the rows a quoted total needs and
#: no route invents a total the store cannot sum.
TOTAL_ROWS: tuple[str, ...] = ("subtotal", "discount_total", "tax_total", "grand_total")

TOTAL_LABELS: dict[str, str] = {
    "subtotal": "Subtotal",
    "discount_total": "Discount",
    "tax_total": "Tax",
    "grand_total": "Total",
}

# --------------------------------------------------------------------------- #
# Branding
# --------------------------------------------------------------------------- #

#: The brand-kit colour tokens a brand carries. "the brand appears top-right in the quote
#: editor and drives logo/brand-kit colours".
BRAND_TOKENS: tuple[str, ...] = ("accent", "accent_soft", "logo_url", "company_name")

BRAND_TOKEN_LABELS: dict[str, str] = {
    "accent": "Accent colour",
    "accent_soft": "Accent tint",
    "logo_url": "Logo",
    "company_name": "Company name",
}

#: Where a logo came from, in the precedence the specification gives. Step 2 reads: "Logos
#: can come from the quote branding settings, account branding, or the brand". A later
#: entry loses to an earlier one, and the resolved source is recorded on the document so
#: a reader can see which of the three answered.
LOGO_SOURCE_QUOTE_BRANDING = "quote_branding"
LOGO_SOURCE_BRAND_KIT = "brand_kit"
LOGO_SOURCE_ACCOUNT_BRANDING = "account_branding"

#: Highest precedence first.
LOGO_SOURCES: tuple[str, ...] = (
    LOGO_SOURCE_QUOTE_BRANDING,
    LOGO_SOURCE_BRAND_KIT,
    LOGO_SOURCE_ACCOUNT_BRANDING,
)

#: "Show company name when logo isn't set" is a toggle, not a fallback rule applied
#: silently. :data:`FALLBACK_NAME_WHEN_LOGO_ABSENT` is that toggle's default.
FALLBACK_NAME_WHEN_LOGO_ABSENT = "show_company_name_when_no_logo"

#: "Override brand kit" is the toggle that lets a template's own tokens beat the brand's.
OVERRIDE_BRAND_KIT = "override_brand_kit"

# --------------------------------------------------------------------------- #
# The document model
# --------------------------------------------------------------------------- #

#: The model the merge produces, chosen by Jev audit jev-20261004T212018-20336-18854 at
#: confidence 1.00 over the Word content-control merge and the PandaDoc
#: content-placeholder model. The research names all three and chooses none.
DOCUMENT_MODEL_HTML_JSON = "html_json_document_model"

#: The three models the research names, served so the page can say which one was chosen
#: and which two were not, rather than leaving the choice in a docstring.
DOCUMENT_MODELS: tuple[str, ...] = (
    DOCUMENT_MODEL_HTML_JSON,
    "word_content_control_xml_merge",
    "pandadoc_content_placeholders",
)

DOCUMENT_MODEL_LABELS: dict[str, str] = {
    DOCUMENT_MODEL_HTML_JSON: "HTML and JSON document model",
    "word_content_control_xml_merge": "Word content-control XML merge",
    "pandadoc_content_placeholders": "Content-placeholder merge",
}

#: Why each of the two models not chosen was not chosen. Served, not buried.
DOCUMENT_MODEL_REJECTIONS: dict[str, str] = {
    "word_content_control_xml_merge": (
        "Needs an entity XML schema beginning urn:microsoft-crm/document-template/ that "
        "this repository does not hold, and its 100-related-record cap per relationship "
        "would become a truncation rule this product would have to enforce."
    ),
    "pandadoc_content_placeholders": (
        "Needs a content library this ticket does not claim, and every placeholder must "
        "be replaced with 1 to 10 library items at creation, so an unstocked template "
        "fails to instantiate at all."
    ),
}

# --------------------------------------------------------------------------- #
# Precedence at merge time
# --------------------------------------------------------------------------- #

#: The evidence fixes it: "The quote template's properties, content, and associations
#: will be added to the quote as supplemental information, with properties set on the
#: quote overriding the quote template's settings."
PRECEDENCE_QUOTE_OVER_TEMPLATE = "quote_over_template"

PRECEDENCE_LEVELS: tuple[str, ...] = (PRECEDENCE_QUOTE_OVER_TEMPLATE, "template_default")

PRECEDENCE_LABELS: dict[str, str] = {
    PRECEDENCE_QUOTE_OVER_TEMPLATE: "A property set on the quote wins.",
    "template_default": "The template supplies the value. Nothing on the quote overrode it.",
}

# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #

STATE_DRAFT = "draft"
STATE_INSTANTIATED = "instantiated"
STATE_PUBLISHED = "published"

#: The three states a rendered document can be in.
#:
#: ``published`` exists because the non-retroactive rule is stated only about published
#: quotes: "Updating your logo and branding won't update existing published quotes, only
#: currently drafted quotes and quotes created after updating." A draft therefore
#: re-renders against a changed template and a published one does not, and
#: ``rerenderable`` on a document is that rule computed rather than asserted.
DOCUMENT_STATES: tuple[str, ...] = (STATE_DRAFT, STATE_INSTANTIATED, STATE_PUBLISHED)

DOCUMENT_STATE_LABELS: dict[str, str] = {
    STATE_DRAFT: "Draft",
    STATE_INSTANTIATED: "Instantiated",
    STATE_PUBLISHED: "Published",
}

#: A published document is frozen. A draft one re-renders.
FROZEN_STATES: tuple[str, ...] = (STATE_PUBLISHED,)

NO_RETROACTIVE_APPLICATION_QUOTE = (
    "Updating your logo and branding won't update existing published quotes, only "
    "currently drafted quotes and quotes created after updating."
)

#: "The APIs section fixes when the template id can be set: quote to template association
#: type id 286 settable only at quote creation."
ASSOCIATION_TYPE_ID = "286"

#: The association is read-only after the quote exists, which is why a re-render cannot
#: change which template a document came from.
TEMPLATE_ASSOC_TYPE_FIELD = "association_type_id"
TEMPLATE_ASSOC_ID_FIELD = "template_id"

#: "It isn't possible to create or add custom coded modules to a quote using the API. API
#: users can select templates that have custom modules included on them." So a custom
#: module may be read and rendered but never authored through this API.
CUSTOM_MODULE_API_LIMIT = (
    "It isn't possible to create or add custom coded modules to a quote using the API. "
    "API users can select templates that have custom modules included on them."
)

# --------------------------------------------------------------------------- #
# Caps the evidence fixes
# --------------------------------------------------------------------------- #

#: "you can only return up to 100 related records for each relationship." A cap from the
#: Dynamics model, which this workflow does not implement. It is recorded and enforced on
#: the line-items module anyway, because the record is the closest thing this product has
#: to that relationship cap and a proposal that silently dropped line items past 100 would
#: render a total that does not match the quote.
RELATED_RECORD_CAP = 100

#: "Each content placeholder must be replaced with at least 1 content library item. You
#: can add up to 10 content library items per content placeholder." A cap from the
#: PandaDoc model, which this workflow does not implement either. It is recorded because
#: the terms and acceptance modules bind reusable blocks, and the page reports how many
#: blocks bound to a placeholder rather than truncating them silently.
PLACEHOLDER_MIN_ITEMS = 1
PLACEHOLDER_MAX_ITEMS = 10

CAP_EVIDENCE: dict[str, str] = {
    "related_records": "you can only return up to 100 related records for each relationship",
    "placeholder_items": (
        "Each content placeholder must be replaced with at least 1 content library item. "
        "You can add up to 10 content library items per content placeholder."
    ),
}

# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #

#: The executive summary "can be generated by HubSpot's AI using data from line items,
#: deal activities, meeting transcripts, notes, and emails". Those five inputs are the
#: generation inputs this workflow names, and this repository generates no text: a
#: generated summary is a caller-supplied string, and the inputs it was derived from are
#: recorded beside it.
GENERATION_INPUTS: tuple[str, ...] = (
    "line_items",
    "deal_activities",
    "meeting_transcripts",
    "notes",
    "emails",
)

GENERATION_QUOTE = (
    "generated by HubSpot's AI \"using data from line items, deal activities, meeting "
    'transcripts, notes, and emails"'
)

#: A caller-supplied summary has to say what it was built from, because this repository
#: cannot verify that a string was generated from those five inputs and must not imply
#: that it can.
GENERATION_SOURCE_CALLER = "caller_supplied"
GENERATION_SOURCE_TEMPLATE = "template_default"

GENERATION_SOURCES: tuple[str, ...] = (GENERATION_SOURCE_CALLER, GENERATION_SOURCE_TEMPLATE)

# --------------------------------------------------------------------------- #
# Bindings
# --------------------------------------------------------------------------- #

#: The dotted record paths a module field may bind to. A binding names a path inside the
#: quote record or its line items, which is why a template may bind to a field no schema
#: declares: "Records are ordinary JSON in records.data. A team adding a field must need
#: no coordination with anyone."
BINDING_ROOTS: tuple[str, ...] = (
    "quote",
    "deal",
    "company",
    "contact",
    "line_item",
    "product",
)

#: What happens when a binding names a path the record does not carry. It renders empty
#: and is reported as unresolved rather than failing the whole instantiation: one
#: unbindable field must not cost a buyer the whole proposal.
UNRESOLVED = "unresolved"

#: The report field names.
REPORT_UNRESOLVED = "unresolved_bindings"
REPORT_TRUNCATED = "truncated_bindings"
REPORT_MODULES = "modules"
REPORT_TOTALS = "totals"
REPORT_LOGO_SOURCE = "logo_source"
REPORT_PRECEDENCE = "precedence"


def vocabulary() -> dict[str, Any]:
    """Every term on this page, served from one table.

    The frontend reads this rather than hard-coding a list, so a module added here
    appears on the page with no edit to the feature module.
    """

    return {
        "modules": [{"id": module, "label": MODULE_LABELS[module]} for module in MODULES],
        "module_fields": {
            "header": list(HEADER_FIELDS),
            "parties": list(PARTY_ROLES),
            "totals": list(TOTAL_ROWS),
        },
        "party_roles": [{"id": role, "label": PARTY_LABELS[role]} for role in PARTY_ROLES],
        "total_rows": [{"id": row, "label": TOTAL_LABELS[row]} for row in TOTAL_ROWS],
        "branding_tokens": [
            {"id": token, "label": BRAND_TOKEN_LABELS[token]} for token in BRAND_TOKENS
        ],
        "logo_sources": list(LOGO_SOURCES),
        "logo_source_note": (
            "The specification lists three: quote branding settings, account branding, "
            "and the brand. An earlier source in this list wins."
        ),
        "document_states": [
            {"id": state, "label": DOCUMENT_STATE_LABELS[state], "frozen": state in FROZEN_STATES}
            for state in DOCUMENT_STATES
        ],
        "frozen_states": list(FROZEN_STATES),
        "precedence_levels": [
            {"id": level, "label": PRECEDENCE_LABELS[level]} for level in PRECEDENCE_LEVELS
        ],
        "document_models": [
            {
                "id": model,
                "label": DOCUMENT_MODEL_LABELS[model],
                "chosen": model == DOCUMENT_MODEL_HTML_JSON,
                "rejection": DOCUMENT_MODEL_REJECTIONS.get(model, ""),
            }
            for model in DOCUMENT_MODELS
        ],
        "generation_inputs": list(GENERATION_INPUTS),
        "generation_sources": list(GENERATION_SOURCES),
        "binding_roots": list(BINDING_ROOTS),
        "related_record_cap": RELATED_RECORD_CAP,
        "placeholder_min_items": PLACEHOLDER_MIN_ITEMS,
        "placeholder_max_items": PLACEHOLDER_MAX_ITEMS,
        "caps": dict(CAP_EVIDENCE),
        "association_type_id": ASSOCIATION_TYPE_ID,
        "no_retroactive_application_quote": NO_RETROACTIVE_APPLICATION_QUOTE,
        "custom_module_api_limit": CUSTOM_MODULE_API_LIMIT,
        "generation_quote": GENERATION_QUOTE,
        "collections": {
            "templates": TEMPLATES,
            "brands": BRANDS,
            "documents": DOCUMENTS,
            "quotes_read_only": QUOTES,
            "line_items_read_only": LINE_ITEMS,
            "products": PRODUCTS,
        },
        "report_fields": [
            REPORT_MODULES,
            REPORT_TOTALS,
            REPORT_UNRESOLVED,
            REPORT_TRUNCATED,
            REPORT_LOGO_SOURCE,
            REPORT_PRECEDENCE,
        ],
    }
