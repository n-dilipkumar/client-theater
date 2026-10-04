"""Every judgement call WF-093 made, with the alternative it rejected.

The specification leaves several joints open, and its own issue says so twice: "Decide the
order in the design doc and record the decision rather than assuming one", and "The spec
names three vendor document models and does not choose between them. The choice is not
decided by the research."

A derivation with no rejected alternative is a guess wearing a derivation's clothes, so
every entry here names at least two options and says which one was taken and what the
rejection would have cost.

The HTTP layer serves this table at ``GET /api/wf-093/decisions`` so the record is
readable by whoever reviews the feature, rather than buried in a docstring.
"""

from __future__ import annotations

from typing import Any

DECISIONS: dict[str, dict[str, Any]] = {
    "DERIVED_DOCUMENT_MODEL_IS_HTML_JSON": {
        "question": ("Which of the three vendor document models does the merge produce?"),
        "left_open_by": (
            "The specification names all three and chooses none. It describes a HubSpot "
            "HTML/JSON document model rendered in its quote editor and rasterised to PDF "
            "on publish, a Dynamics Word content-control XML merge against a schema "
            "beginning urn:microsoft-crm/document-template/, and a PandaDoc "
            "content-placeholder merge."
        ),
        "options": {
            "html_json_document_model": (
                "Produce an ordered JSON document model: the template's modules in order, "
                "each rendered from its bindings plus record data, plus the resolved "
                "branding token block. Stored as the quote's presentation layer."
            ),
            "word_content_control_xml_merge": (
                "Emit a Word document part with repeating content controls for the 1:N "
                "line items, against an entity XML schema."
            ),
            "pandadoc_content_placeholders": (
                "Replace each declared content placeholder with 1 to 10 content library "
                "items at document creation."
            ),
            "implement_all_three_behind_a_mode": (
                "Ship all three and let a template declare which one it renders with."
            ),
        },
        "chosen": "html_json_document_model",
        "rejected_because": (
            "The Word merge needs an entity XML schema this repository does not hold, and "
            "its 100-related-record cap would arrive as a vendor truncation rather than a "
            "rule this product can state and test. The content-placeholder merge needs a "
            "content library this ticket does not claim, and the evidence makes every "
            "placeholder mandatory at creation: an unstocked template would fail to "
            "instantiate at all, which is a worse failure than rendering a section empty. "
            "Shipping all three would leave three partial implementations behind one "
            "field and none of them verified. The JSON model is a natural fit for a store "
            "that already holds arbitrary JSON with a dynamic index, and it is fully "
            "derivable from the template and record data with no vendor credential, so "
            "it is the only one of the three that can be verified over localhost. Put to "
            "Jev as audit jev-20261004T212018-20336-18854, which selected it at confidence "
            "1.00 with a margin of 1.00 over the runner-up."
        ),
        "cost_of_the_choice": (
            "This workflow renders a document model rather than a rasterised PDF, so it "
            "does not produce the vendor's PDF artefact. The evidence says the quote "
            "editor rasterises on publish; this workflow records the published state and "
            "stops there. The two caps from the models not chosen are still enforced and "
            "reported, so nothing is dropped silently."
        ),
    },
    "DERIVED_QUOTE_IS_READ_AS_DATA": {
        "question": (
            "Does WF-093 own the quote record it merges into, or read one provisioned by WF-086?"
        ),
        "left_open_by": (
            "The issue states that WF-086 provisions the quote record and its line item "
            "records, that WF-086's own user flow picks a quote template from a dropdown, "
            "so the two read each other's surface, and instructs: decide the order and "
            "record the decision. It also declines to claim WF-086 as a dependency to "
            "avoid a two-ticket cycle."
        ),
        "options": {
            "read_wf086_quote_as_data": (
                "Own templates, brands and the rendered document. Read quote rows from "
                "wf086_quote and line items from wf086_line_item as data. Never create a "
                "quote through this workflow's routes."
            ),
            "own_the_quote_record_too": (
                "Create the quote, its line items and the rendered document, so the page "
                "and the demo are self-contained with no other writer."
            ),
            "read_from_a_generic_quote_collection": (
                "Read from a plainly named 'quote' collection so whoever provisions "
                "quotes first owns the name."
            ),
        },
        "chosen": "read_wf086_quote_as_data",
        "rejected_because": (
            "Owning the quote would give one collection two writers, and the field the "
            "evidence says is settable only at quote creation, the quote to template "
            "association type id 286, would then be set by whichever feature ran last. "
            "A generic collection name was rejected because I counted 59 distinct "
            "ticket-prefixed collection strings under dsr/ and none of them is a bare "
            "noun, so a bare 'quote' would be the one collection in this repository "
            "named against the convention and would collide the moment WF-086 shipped. "
            "Reading as data follows a pattern this repository has already merged and "
            "reviewed: WF-085 reads wf075_view and wf075_visitor rows that WF-075 owns, "
            "and other packages already carry foreign ticket prefixes such as "
            "wf078_owns_gate and wf095_owns_gate. The host's collision check covers "
            "routes and exception handlers, not collections, so a shared read is "
            "permitted by design. Put to Jev first as audit "
            "jev-20261004T212044-29964-44792, which selected it but came back uncertain "
            "at 0.72; re-asked with the measured evidence as audit "
            "jev-20261004T212223-11296-43126, it selected it at confidence 1.00."
        ),
        "cost_of_the_choice": (
            "If WF-086 has not merged, the collection is empty and the page renders its "
            "empty state, which is a supported state rather than a failure. This "
            "workflow's own seed writes a quote row and its line items as seed data so "
            "the demo is reviewable either way, and it reads the line-items module "
            "against whatever rows the store holds rather than requiring a shape WF-086 "
            "is obliged to write."
        ),
    },
    "DERIVED_PUBLISHED_DOCUMENT_IS_FROZEN": {
        "question": "What does a template or branding change do to a document that exists?",
        "left_open_by": (
            "The automations note says: 'Updating your logo and branding won't update "
            "existing published quotes, only currently drafted quotes and quotes created "
            "after updating.' It names published quotes and drafted quotes and says "
            "nothing about an instantiated-but-unpublished one."
        ),
        "options": {
            "freeze_published_only": (
                "A published document is never rewritten. A draft and an instantiated "
                "document both take the current template and branding on the next render."
            ),
            "freeze_all_documents": (
                "A document is a snapshot the moment it is rendered, so no template change "
                "ever touches it."
            ),
            "re_render_everything": (
                "Always re-render every document against the current template."
            ),
        },
        "chosen": "freeze_published_only",
        "rejected_because": (
            "Freezing everything would contradict the evidence's own 'currently drafted "
            "quotes', which the vendor does update, and would make the draft state "
            "pointless. Re-rendering everything contradicts the quoted sentence directly "
            "and would rewrite a document the buyer has already received. Freezing only "
            "the published state is the reading the sentence supports, and it is the only "
            "one of the three that does not require guessing about the unmentioned "
            "middle state."
        ),
        "cost_of_the_choice": (
            "A document needs a published state distinct from drafted for the rule to "
            "have anything to act on, so this workflow records a lifecycle rather than a "
            "boolean. Re-rendering a published document is refused with a remediation "
            "rather than silently performed, and the refusal carries the evidence."
        ),
    },
    "DERIVED_BINDING_ROOTS_ARE_A_PRESENCE_CHECK_NOT_A_SCHEMA": {
        "question": "How is a template binding to an undeclared field validated?",
        "left_open_by": (
            "The store holds arbitrary JSON and this product requires that 'a team adding "
            "a field must need no coordination with anyone'. So no schema declares which "
            "paths exist, and a binding cannot be checked against one."
        ),
        "options": {
            "validate_root_presence_only": (
                "Check that a binding path starts with a readable root such as quote, deal, "
                "company, contact, line_item or product, and resolve the rest against the "
                "record at merge time. A path the record does not carry is reported as "
                "unresolved and renders empty."
            ),
            "declare_a_binding_schema": (
                "Validate every binding against a declared list of known paths, and refuse "
                "a path that is not on it."
            ),
            "accept_any_path_without_checking": (
                "Store whatever path a template sends and resolve it at render time."
            ),
        },
        "chosen": "validate_root_presence_only",
        "rejected_because": (
            "A declared schema is exactly the coordination requirement this product "
            "forbids: adding a field to the quote record would then need a change to this "
            "workflow's schema, which is the failure the rule exists to prevent. "
            "Accepting any path without a check means a typo silently renders an empty "
            "section forever, and the page would have no way to tell that from a record "
            "that genuinely has no value. The root check is the only option that keeps "
            "schema freedom while still naming a binding whose root cannot resolve to "
            "anything."
        ),
        "cost_of_the_choice": (
            "A root typo is caught at save time and a leaf typo is caught at merge time. "
            "The report therefore carries unresolved bindings rather than a save-time "
            "error, so a template may legitimately bind a field that a particular quote "
            "does not carry."
        ),
    },
    "DERIVED_CAPS_FROM_UNIMPLEMENTED_MODELS_ARE_STILL_ENFORCED": {
        "question": "Should the caps belonging to the two document models not chosen apply?",
        "left_open_by": (
            "The evidence records two caps: 'you can only return up to 100 related records "
            "for each relationship', and 'Each content placeholder must be replaced with at "
            "least 1 content library item. You can add up to 10 content library items per "
            "content placeholder.' Both belong to models this workflow does not implement."
        ),
        "options": {
            "enforce_and_report_both": (
                "Cap the line-items module at 100 related records and report what was "
                "dropped. Record the 1-to-10 placeholder bound on reusable blocks and "
                "report how many bound."
            ),
            "ignore_both": ("Apply no cap, because neither model is implemented."),
            "enforce_silently": ("Apply both caps without reporting anything."),
        },
        "chosen": "enforce_and_report_both",
        "rejected_because": (
            "Ignoring them would mean a proposal could render a line-items module of any "
            "size, and the researched cap exists because a relationship that returns "
            "everything is what the vendor limits. Enforcing silently is the failure this "
            "workflow is built against: a total that silently excludes a row is a number "
            "the buyer cannot reconcile against the quote, and a reviewer would have no "
            "way to know why. The content-placeholder bound is recorded and reported rather "
            "than enforced as a refusal, because this workflow has no content library and "
            "so has nothing to enforce it against."
        ),
        "cost_of_the_choice": (
            "A quote with more than 100 line items renders a truncated module and says so "
            "in the report. That is the honest rendering: the alternative is a total that "
            "does not match the printed rows."
        ),
    },
    "DERIVED_DEFAULT_EXPIRY_IS_DERIVED_AND_REPORTED": {
        "question": "What expiration date does a quote get when none is stated?",
        "left_open_by": (
            "The header module names an expiration date as a field. The specification sets "
            "no figure for it anywhere and gives no default."
        ),
        "options": {
            "render_empty": (
                "Leave the expiration field empty unless the quote or the template sets it."
            ),
            "derive_a_default_and_report_it": (
                "Derive a default from the issue date, and report which level supplied it."
            ),
            "require_an_expiry": ("Refuse to render a quote that states no expiration date."),
        },
        "chosen": "derive_a_default_and_report_it",
        "rejected_because": (
            "Rendering empty leaves the header with a hole in the one field a buyer "
            "checks first, and the specification puts the expiration date in the header "
            "module for a reason. Refusing to render would cost a buyer the entire "
            "proposal over a header field, which is a far worse outcome than a derived "
            "date the reader can see is derived. Deriving and reporting keeps the field "
            "populated and keeps the derivation visible."
        ),
        "cost_of_the_choice": (
            "A document may carry an expiration date no one set. The report says so under "
            "the precedence level, so the page distinguishes a stated date from a derived "
            "one rather than presenting both identically."
        ),
    },
    "DERIVED_GENERATED_SUMMARY_IS_CALLER_SUPPLIED": {
        "question": "What generates the executive summary?",
        "left_open_by": (
            "The specification says the cover letter or executive summary 'can be generated "
            "by HubSpot's AI using data from line items, deal activities, meeting "
            "transcripts, notes, and emails'. It names a vendor capability this repository "
            "does not have."
        ),
        "options": {
            "caller_supplied_and_inputs_recorded": (
                "Accept a caller-supplied string and record the five inputs it was derived "
                "from. This workflow generates no text."
            ),
            "generate_a_summary_from_the_line_items": (
                "Compose a summary from the line items and call it generated."
            ),
            "omit_the_executive_summary_module": (
                "Drop the module rather than ship a field this product cannot fill."
            ),
        },
        "chosen": "caller_supplied_and_inputs_recorded",
        "rejected_because": (
            "Composing text from line items would be a summary this product invented, "
            "presented under a label that says a model wrote it. That is a claim about a "
            "capability this repository does not have, and it is the same defect the "
            "vendor-claim handling in other workflows exists to avoid. Omitting the "
            "module loses a section the editor specifies. Accepting a caller-supplied "
            "string and recording the inputs keeps the module and makes the provenance "
            "visible."
        ),
        "cost_of_the_choice": (
            "The module renders the template's own text until a caller supplies a "
            "summary. The page reports which of the two it rendered, so a reader is never "
            "told text was generated when this product did not generate it."
        ),
    },
    "DERIVED_TOTAL_ROWS_ARE_QUOTED_OR_DERIVED_NOT_GUESSED": {
        "question": "What rows does the totals tab carry when the quote states no discount?",
        "left_open_by": (
            "The specification names a Totals tab and does not enumerate its rows or say "
            "how a discount is computed."
        ),
        "options": {
            "stated_or_zero_flagged": (
                "Read discount and tax from the quote. When neither is stated, report the "
                "row as zero with stated: false. The grand total sums the rendered rows."
            ),
            "derive_discount_from_line_items": (
                "Compute a discount from a per-line-item discount field."
            ),
            "omit_absent_rows": ("Print only the rows the quote has values for."),
        },
        "chosen": "stated_or_zero_flagged",
        "rejected_because": (
            "Deriving a discount would invent a pricing rule the specification does not "
            "state, and a pricing rule is not this workflow's to invent. Omitting absent "
            "rows would make two quotes with the same totals print different documents, "
            "and a reader could not tell an absent discount from a forgotten one. The "
            "stated flag is the only option that keeps an absent value distinguishable "
            "from a zero one."
        ),
        "cost_of_the_choice": (
            "The grand total is marked stated: false when the quote stated neither a "
            "discount nor tax, because it then sums a derived subtotal with two "
            "unreported zero rows. The page shows that flag rather than presenting the "
            "number as fully stated."
        ),
    },
}


def count() -> int:
    return len(DECISIONS)


def describe() -> list[dict[str, Any]]:
    """Every recorded decision, sorted by id so the page is stable."""

    return [dict(decision, id=key) for key, decision in sorted(DECISIONS.items())]


def describe_one(inference_id: str) -> dict[str, Any] | None:
    return dict(DECISIONS[inference_id], id=inference_id) if inference_id in DECISIONS else None
