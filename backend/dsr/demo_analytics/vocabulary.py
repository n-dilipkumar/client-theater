"""The vocabulary WF-127 accepts, and the inferences that fill its gaps.

The research this workflow is built from is vendor marketing copy: three pages of
goconsensus.com plus two GitHub API records. It names the metric vocabulary and
the six drill-down levels exactly, and it names the *values* of nothing. So this
module holds the two kinds of constant apart:

* the **sourced** half - the metric names, the six levels, the stakeholder roles
  - is quoted from the evidence and is not this build's invention;
* the **inferred** half - the DQL weights, the heatmap bucket width, the
  discovery-rate denominator - is collected in :mod:`.inferences`, served at
  ``GET /api/wf-127/inferences``, and marked as a substitution rather than a
  port, because "Consensus itself is **not** open source and offers no
  self-host".

Nothing here imports anything but the standard library. The store-facing half
lives in :mod:`.book`.
"""

from __future__ import annotations

from typing import Any

#: The collections this workflow owns. Namespaced, because every feature shares
#: one ``records`` table and ``find()`` matches on collection first.
DEMO_COLLECTION = "wf127_demo"
EVENT_COLLECTION = "wf127_demo_event"
SHARE_COLLECTION = "wf127_demo_share"
STAKEHOLDER_COLLECTION = "wf127_demo_stakeholder"

#: The room reference, carried in ``data`` rather than left to the envelope.
#: ``room_id`` is part of the record *envelope*, and the audited store strips it
#: out of ``data`` before the dynamic index is built, so a demo that stored its
#: room there could not be filtered by ``find()``. The sibling workflows keep the
#: same twin key for the same reason.
ROOM_REF = "room_ref"

#: A demo is built from a recording, published, then withdrawn. ``archived`` is
#: the only state that refuses a new event.
DEMO_STATES: tuple[str, ...] = ("draft", "published", "archived")

#: The researched distribution surfaces, reduced to the demo kinds this build
#: can actually render. "branded interactive tours" and "on-demand video
#: variants" are named in the evidence; a whiteboard layer (Excalidraw) is
#: explicitly out of scope and is named on the page instead.
DEMO_TYPES: tuple[str, ...] = ("sandbox", "tour", "video")

#: The kinds of node in the demo's click-path tree. The tree is what makes the
#: demo branching rather than linear, so a node's ``next`` list is the branch.
NODE_KINDS: tuple[str, ...] = ("screen", "choice", "end")

#: The per-viewer event stream, quoted from the evidence: "views ... features or
#: sections selected, and docs downloaded".
EVENT_TYPES: tuple[str, ...] = ("view", "section_selected", "doc_downloaded")

#: A stakeholder is either one of the initial recipients ("demos sent"), one the
#: intra-viral re-share discovered ("organic stakeholders discovered"), or a
#: direct open that no share attributed.
STAKEHOLDER_ROLES: tuple[str, ...] = ("initial_recipient", "discovered", "direct")

#: How a discovered stakeholder was attributed. Both are the researched
#: "initial recipient's forward/intra-viral shares".
DISCOVERY_SOURCES: tuple[str, ...] = ("forward", "intra_viral")

#: The metric vocabulary, in the order the evidence names it: "demos sent,
#: organic stakeholders discovered, discovery rate, views, view rate,
#: features/sections selected, and docs downloaded".
METRIC_NAMES: tuple[str, ...] = (
    "demos_sent",
    "stakeholders_discovered",
    "discovery_rate",
    "views",
    "view_rate",
    "features_selected",
    "docs_downloaded",
)

#: The six drill-down levels, in the order the evidence gives them.
LEVELS: tuple[str, ...] = (
    "buyer_group",
    "top_level",
    "team",
    "individual",
    "recently_shared",
    "all_demos",
)

#: The heatmap axes. Weekday is the row and a fixed-width hour slot is the
#: column, both derived from each event's own timestamp, so a bucket is a pure
#: function of the event and a test never depends on today's date.
WEEKDAY_NAMES: tuple[str, ...] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

#: The hours per heatmap column. Six slots of four hours, which is the coarsest
#: axis that still separates working hours from overnight.
HEATMAP_BUCKET_HOURS = 4

#: The DQL (demo-qualified lead) score. The evidence names the automation
#: ("auto-track a DQL score") and no formula, so every weight below is an
#: inference recorded in :mod:`.inferences`.
DQL_WEIGHTS: dict[str, int] = {
    "viewed": 30,
    "per_section": 8,
    "section_cap": 32,
    "downloaded": 18,
    "returned": 20,
}
DQL_MAX = 100
DEFAULT_DQL_THRESHOLD = 60

#: The default click-path a recording is converted into. The researched flow is
#: "screen capture + click-path -> interactive demo build", so a demo built with
#: no explicit tree gets this branching template rather than an empty one.
DEFAULT_CLICK_PATH: tuple[dict[str, Any], ...] = (
    {"id": "overview", "label": "Overview", "kind": "screen", "next": ["dashboard", "reports"]},
    {"id": "dashboard", "label": "Dashboard", "kind": "screen", "next": ["alerts", "export"]},
    {"id": "reports", "label": "Reports", "kind": "screen", "next": ["export"]},
    {"id": "alerts", "label": "Alerts", "kind": "screen", "next": ["export"]},
    {"id": "export", "label": "Export", "kind": "end", "next": []},
)

#: The keys a viewer event must never carry. The researched privacy analogue for
#: this workflow is a "custom event schema like DeckLens' for section-level
#: telemetry", and DeckLens' own boundary is that it does not transmit document
#: bodies or raw form values. Refusing the keys by name makes the boundary a
#: rule rather than a promise.
REFUSED_PAYLOAD_KEYS: tuple[str, ...] = (
    "body",
    "content",
    "document_body",
    "file",
    "file_bytes",
    "form_values",
    "raw_form",
    "text",
    "recording",
    "recording_bytes",
    "screen_capture",
)


def describe() -> dict[str, Any]:
    """The whole accepted vocabulary, as one payload.

    Served at ``GET /api/wf-127/vocabulary`` so the page renders its pickers
    from the same source the rules enforce against, and so the inferred half is
    visibly labelled rather than passed off as sourced.
    """
    return {
        "demo_states": list(DEMO_STATES),
        "demo_types": list(DEMO_TYPES),
        "node_kinds": list(NODE_KINDS),
        "event_types": list(EVENT_TYPES),
        "stakeholder_roles": list(STAKEHOLDER_ROLES),
        "discovery_sources": list(DISCOVERY_SOURCES),
        "metric_names": list(METRIC_NAMES),
        "levels": list(LEVELS),
        "weekday_names": list(WEEKDAY_NAMES),
        "heatmap_bucket_hours": HEATMAP_BUCKET_HOURS,
        "refused_payload_keys": list(REFUSED_PAYLOAD_KEYS),
        "defaults": {
            "dql_threshold": DEFAULT_DQL_THRESHOLD,
            "dql_max": DQL_MAX,
        },
        "dql_weights": dict(DQL_WEIGHTS),
        "collections": {
            "demos": DEMO_COLLECTION,
            "events": EVENT_COLLECTION,
            "shares": SHARE_COLLECTION,
            "stakeholders": STAKEHOLDER_COLLECTION,
        },
        "sourced": {
            "metric_names_source": "goconsensus.com/overview/demolytics, quoted on issue #152",
            "levels_source": "goconsensus.com/overview/demolytics, quoted on issue #152",
            "discovery_source": "intra-viral sharing, goconsensus.com/overview/demolytics",
        },
        "reused": {
            "audited_store": "dsr.store.RecordStore over dsr.db.audited.AuditedDatabase",
            "schema": "schema-flexible JSON in records.data; no migration, no typed column",
        },
    }


__all__ = [
    "DEFAULT_CLICK_PATH",
    "DEFAULT_DQL_THRESHOLD",
    "DEMO_COLLECTION",
    "DEMO_STATES",
    "DEMO_TYPES",
    "DISCOVERY_SOURCES",
    "DQL_MAX",
    "DQL_WEIGHTS",
    "EVENT_COLLECTION",
    "EVENT_TYPES",
    "HEATMAP_BUCKET_HOURS",
    "LEVELS",
    "METRIC_NAMES",
    "NODE_KINDS",
    "REFUSED_PAYLOAD_KEYS",
    "ROOM_REF",
    "SHARE_COLLECTION",
    "STAKEHOLDER_COLLECTION",
    "STAKEHOLDER_ROLES",
    "WEEKDAY_NAMES",
    "describe",
]
