"""Every decision the WF-071 research does not make, and how to change it.

The issue separates two kinds of statement: what the sources say, and what this
build had to choose where the sources are silent. The second kind lives here and
is served at ``GET /api/wf-071/inferences`` so a reviewer can see a chosen value
as a decision rather than mistaking it for a researched fact.

Each entry is ``{id, decision, why, change_it_by, basis}``. ``basis`` names the
source for the parts that *are* sourced, so a reader can tell the two apart.
"""

from __future__ import annotations

from typing import Any

INFERENCES: tuple[dict[str, str], ...] = (
    {
        "id": "storage_location",
        "decision": (
            "The watermark config lives in its own collection keyed by the WF-121 "
            "link id, and GET joins it onto the link, rather than being written "
            "onto the link row's data."
        ),
        "why": (
            "WF-121's update_link re-normalises the merged link payload, so any key "
            "it does not declare is dropped the next time a rep edits the link. A "
            "config on the link row would vanish silently, taking the leak trace "
            "with it. The join preserves the observable contract: GET always "
            "returns watermark_config or null beside the link."
        ),
        "change_it_by": (
            "If WF-121 ever makes its link payload pass-through, write the two "
            "fields onto the link row and drop the join; nothing else changes."
        ),
        "basis": "chosen by Jev, audit jev-20261013T231129-7936-89700, at confidence 1.00",
    },
    {
        "id": "email_token_without_a_gate",
        "decision": (
            "{{email}} resolves to the viewer's address when the view carries one. "
            "When it does not, the token renders as the word 'anonymous' and the "
            "response names 'email' in unresolved_tokens."
        ),
        "why": (
            "The token table says {{email}} 'requires email_protected', and the "
            "email gate is WF-069's, not this workflow's. Refusing the view instead "
            "would break the researched anonymous link, and a silently blanked "
            "token would lose the trace while still looking like a watermark. The "
            "explicit placeholder plus unresolved_tokens keeps both honest."
        ),
        "change_it_by": (
            "Read the gate from the link and refuse the stamp when the gate is on "
            "and no viewer is present, once WF-069 exposes the resolved gate here."
        ),
        "basis": "Papermark token table: '{{email}} The viewer's email (requires email_protected).'",
    },
    {
        "id": "ip_token_without_an_ip",
        "decision": (
            "{{ipAddress}} resolves to the view's IP address. When the view carries "
            "none, the token renders as the word 'unknown' and is named in "
            "unresolved_tokens."
        ),
        "why": (
            "The IP is the identity half the open-source layer controls entirely, "
            "and the researched guidance is to use it with {{email}} to make a leak "
            "identifiable to one viewer. A delivery path that strips the address "
            "(a proxy, a logged-out view) must be visible rather than papered over."
        ),
        "change_it_by": "vocabulary.PLACEHOLDERS['ipAddress'].",
        "basis": "Papermark: 'Use {{email}} and {{ipAddress}} together to make leaks identifiable.'",
    },
    {
        "id": "plan_tier",
        "decision": (
            "A watermark requires the 'business' plan or above; enabling it on a "
            "lower plan returns 403 forbidden_plan_feature on create/update only."
        ),
        "why": (
            "The researched gate names the response and the timing and not the tier. "
            "'business' is the tier WF-077 already charges for 'tokens', and a "
            "watermark is the same class of governance feature."
        ),
        "change_it_by": "vocabulary.PLAN_REQUIRED.",
        "basis": (
            "WF-077 vocabulary.PLAN_GATE_QUOTE: 'enabling a gated feature returns "
            "403 forbidden_plan_feature on create/update, while existing links keep "
            "working after a downgrade.'"
        ),
    },
    {
        "id": "plan_context_absent",
        "decision": (
            "An absent plan is not refused. The route refuses a *named* plan below "
            "the required tier and allows a request that names none."
        ),
        "why": (
            "This product has no team-plan row on the tracked-link surface, so a "
            "route that refused every unnamed request would make the feature "
            "unreachable in a local install and a demo. Naming the asymmetry is "
            "better than inventing a plan source."
        ),
        "change_it_by": (
            "Resolve the plan from dsr.workspace_roles.engine.plan(store, workspace_id) "
            "once a room carries its workspace id, and require it here."
        ),
        "basis": "house: no plan resolver is wired to a WF-121 link",
    },
    {
        "id": "tiled_default",
        "decision": "is_tiled defaults to false.",
        "why": (
            "The documented defaults name six of the seven fields - 'Confidential at "
            "middle-center, 45°, black, 24pt, 0.5 opacity' - and is_tiled is not one "
            "of them. The default position and rotation describe one anchored "
            "watermark, so a single overlay is the reading that matches."
        ),
        "change_it_by": "vocabulary.DEFAULT_TILED.",
        "basis": "Papermark default quoted in the issue evidence block",
    },
    {
        "id": "text_cap",
        "decision": "The watermark text is capped at 200 characters.",
        "why": (
            "The schema documents no cap. A watermark is a short label, and an "
            "unbounded string would be composited onto every page of every view; the "
            "cap keeps a pathological value out without constraining a realistic one."
        ),
        "change_it_by": "vocabulary.TEXT_MAX_CHARS.",
        "basis": "house guard rail; no sourced cap",
    },
    {
        "id": "team_locale_date_time",
        "decision": ("{{date}} and {{time}} render in UTC, from the view's own timestamp."),
        "why": (
            "The data flow says 'team-locale date/time', but no team locale exists on "
            "this surface. UTC is the one rendering that does not change with the "
            "viewing host's clock, which matters for a value whose purpose is to "
            "identify when a leak happened."
        ),
        "change_it_by": (
            "Pass a zone into rules.render_pages and format with it, once a team locale is stored."
        ),
        "basis": "Papermark data flow: 'team-locale date/time'",
    },
    {
        "id": "no_cli_flag_validation",
        "decision": (
            "The CLI's seven --watermark-* flags are not built. Server-side "
            "validation of the same seven fields is built instead."
        ),
        "why": (
            "The stack table in AGENTS.md has no CLI. The researched point - 'the CLI "
            "validates the set before contacting the API' - is preserved as the "
            "server validating all seven fields at once, which is the check that "
            "actually protects the stored config."
        ),
        "change_it_by": "Add a CLI that calls /api/wf-071 and reuse normalise_config.",
        "basis": "Papermark CLI note quoted in the issue evidence block",
    },
    {
        "id": "not_an_access_control",
        "decision": (
            "The watermark gates nothing. A link without a watermark stays viewable, "
            "and a link with one is not made viewable by it."
        ),
        "why": (
            "The spec's own Why field: 'Watermarking is a deterrent and a trace, not "
            "an access control.' Access is WF-069's gate and stays WF-069's."
        ),
        "change_it_by": "Do not. Gating access is a separate, separately-researched feature.",
        "basis": "WF-071 spec: 'Do not let this workflow gate access.'",
    },
    {
        "id": "client_side_composite",
        "decision": (
            "The overlay is returned as data for the viewer to composite. Nothing is "
            "written into a document, and this product stores no document bytes."
        ),
        "why": (
            "The spec is explicit: 'Nothing is burned into the stored PDF - the "
            "watermark exists only in the delivered view.' The library row remains "
            "the document, exactly as WF-121's link references it."
        ),
        "change_it_by": "Nothing. Writing into stored bytes is the rejected alternative.",
        "basis": "WF-071 data flow: 'composites the overlay onto the rasterised page in the viewer'",
    },
)

SOURCE_NOTE = (
    "The sourced half of this workflow is quoted on issue #133 and lives in "
    "docs/research/digital-sales-room-workflows/wf/WF-071.md. The two primary "
    "sources are the Papermark watermark-link-views guide and the Microsoft "
    "Purview dynamic-watermark documentation; the Papermark OpenAPI schema is the "
    "authority for the seven config fields."
)


def describe() -> dict[str, Any]:
    """The register as one payload, served at ``GET /api/wf-071/inferences``."""
    return {
        "ticket": "WF-071",
        "inferences": [dict(entry) for entry in INFERENCES],
        "count": len(INFERENCES),
        "source_note": SOURCE_NOTE,
    }


__all__ = ["INFERENCES", "SOURCE_NOTE", "describe"]
