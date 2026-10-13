"""The decisions the WF-127 research does not make, and how to change each one.

This workflow is built from the weakest-evidenced spec in the set. Its primary
sources are three pages of vendor marketing copy and two GitHub API records, and
the spec says so. The evidence names a vocabulary and names the value of none of
it. Everything below is therefore a **substitution**, not a port: the reference
implementation, Consensus, is closed source and "offers no self-host", so no
line of this build could be checked against it.

Each entry is one open decision, why this build decided it, and the single place
to change it. The register is served at ``GET /api/wf-127/inferences`` so a
reviewer reads the decisions beside the numbers they produce.
"""

from __future__ import annotations

from typing import Any

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "stakeholder_discovery_attribution",
        "decision": (
            "A viewer becomes a discovered stakeholder when their event carries an upstream "
            "share reference that names a viewer the demo already knows. The discovered "
            "stakeholder is attributed to that sender."
        ),
        "why": (
            "The evidence says discovery comes from 'the initial recipient's forward/intra-viral "
            "shares; downstream stakeholder identities' and 'Discover stakeholders with "
            "intra-viral sharing'. It does not say whether the viewer declares themselves or the "
            "system infers it. Inferring from the share reference is the reading that matches "
            "'surfaces the newly discovered stakeholders', because a viewer who declares "
            "themselves is not discovered."
        ),
        "change_it_by": "Change :func:`dsr.demo_analytics.rules.stakeholder_role`.",
        "kind": "inference",
    },
    {
        "id": "discovery_rate_denominator",
        "decision": "discovery_rate = discovered stakeholders / demos sent.",
        "why": (
            "The evidence names 'discovery rate' and no denominator. Demos sent is the one "
            "denominator the same sentence supplies ('demos sent'), so the rate is stated "
            "against it rather than against views, which would reward a demo nobody sent."
        ),
        "change_it_by": "Change :func:`dsr.demo_analytics.rules.metrics`.",
        "kind": "inference",
    },
    {
        "id": "view_rate_denominator",
        "decision": "view_rate = distinct viewers who opened / demos sent.",
        "why": (
            "The evidence names 'views' and 'view rate'. Views is the event count; the rate is "
            "the share of the initial send that opened at least once, which is the only reading "
            "of a *rate* that is not a restatement of the count."
        ),
        "change_it_by": "Change :func:`dsr.demo_analytics.rules.metrics`.",
        "kind": "inference",
    },
    {
        "id": "demos_sent_denominator",
        "decision": "demos sent = the distinct set of initial recipients.",
        "why": (
            "One recipient shared twice is one demo sent, not two. The evidence's 'demos sent' "
            "reads as a distribution count, and a count that double-counts a repeat send would "
            "make view_rate fall for a reason that is not buyer behaviour."
        ),
        "change_it_by": "Change :func:`dsr.demo_analytics.rules.metrics`.",
        "kind": "inference",
    },
    {
        "id": "heatmap_axes",
        "decision": "The heatmap rows are weekdays and its columns are four-hour slots.",
        "why": (
            "The evidence names 'activity heatmaps' and no axes. Weekday by hour is the axis a "
            "demo's own timestamps support with no extra data, and a bucket is a pure function of "
            "an event timestamp, so a test freezes the clock rather than the calendar."
        ),
        "change_it_by": (
            "Change :data:`dsr.demo_analytics.vocabulary.HEATMAP_BUCKET_HOURS` or "
            ":func:`dsr.demo_analytics.rules.heatmap`."
        ),
        "kind": "inference",
    },
    {
        "id": "dql_formula",
        "decision": (
            "DQL score = 30 for a view, 8 per distinct section selected up to 32, 18 for a "
            "download, 20 for a second view; capped at 100 and qualified at 60."
        ),
        "why": (
            "The evidence names the automation 'auto-track a DQL (demo-qualified lead) score' "
            "and gives no threshold and no formula. The weights are a stated substitution, "
            "weighted so a single view alone never qualifies a lead."
        ),
        "change_it_by": (
            "Change :data:`dsr.demo_analytics.vocabulary.DQL_WEIGHTS` or "
            ":func:`dsr.demo_analytics.rules.dql_score`."
        ),
        "kind": "inference",
    },
    {
        "id": "build_from_recording",
        "decision": (
            "A demo with no explicit click path is built from a default branching template; a "
            "payload with a tree is validated in full."
        ),
        "why": (
            "The evidence names 'auto-generate the interactive demo from a recording' and does "
            "not name the recording format. A real screen-capture ingest is out of scope, so the "
            "build converts a recording reference into a branching template and lets a caller "
            "supply the real tree when they have one."
        ),
        "change_it_by": "Change :data:`dsr.demo_analytics.vocabulary.DEFAULT_CLICK_PATH`.",
        "kind": "inference",
    },
    {
        "id": "out_of_scope_surfaces",
        "decision": (
            "Slack, the Microsoft 365 App Store, mobile (iPhone), the Excalidraw whiteboard, and "
            "the Apache Superset / Metabase roll-up are out of scope and named on the page."
        ),
        "why": (
            "The evidence names them as distribution and extensibility surfaces and specifies "
            "none of them. This product has no mail transport, no app-store packaging and no "
            "BI engine, so building a stub for any of them would be a claim this workflow cannot "
            "back with a measurement."
        ),
        "change_it_by": "A later workflow, or a deployment that registers the surface.",
        "kind": "scope",
    },
    {
        "id": "no_crm_write",
        "decision": (
            "The researched 'CRM push via integration or open API' is not built here; the "
            "analytics stay in the room."
        ),
        "why": (
            "The spec's dependency note says the CRM data flow 'names no CRM collection, object "
            "or property for another workflow to provision' and asks the implementer to record a "
            "follow-up rather than assume one. The engagement record this workflow would push is "
            "therefore left to the workflow that owns the CRM object."
        ),
        "change_it_by": "A follow-up issue that names the CRM object, then a push route here.",
        "kind": "dependency",
    },
    {
        "id": "vendor_claim_not_repeated",
        "decision": (
            "The claim that Consensus is '#1 Demo Platform on G2 and a leader in the Agentic GTM "
            "Platforms category' is not repeated as fact anywhere in this build."
        ),
        "why": (
            "The spec marks it 'vendor claim, not independently verified here'. It is recorded "
            "here so its absence reads as a decision rather than an omission."
        ),
        "change_it_by": "Independent evidence, if one is ever produced.",
        "kind": "provenance",
    },
)


def describe() -> dict[str, Any]:
    """Every open decision, and the place to change each one."""
    return {
        "workflow": "WF-127",
        "count": len(INFERENCES),
        "inferences": [dict(entry) for entry in INFERENCES],
        "note": (
            "Every entry is a substitution, not a port: the reference implementation is closed "
            "source and offers no self-host."
        ),
    }


__all__ = ["INFERENCES", "describe"]
