"""The published vocabulary of WF-049, and the researched surfaces it reads.

Everything here is either quoted from
``docs/research/digital-sales-room-workflows/wf/WF-049.md`` or named as this
build's own. The HTTP layer serves :func:`vocabulary` verbatim so a client
renders its pickers from the same source the parser validates against, and no
client compiles a list that can drift.
"""

from __future__ import annotations

from typing import Any

from dsr.integ_monitor.errors import UnknownVendor

#: The three vendors this build has researched surfaces for. A fourth vendor is
#: registered with its own quota policy - the researched extensibility claim:
#: "because monitoring is fed by a connector-declared quota policy ... there is
#: exactly one place to teach the system a new vendor's numbers".
VENDORS = ("salesforce", "hubspot", "dataverse")

VENDOR_LABELS = {
    "salesforce": "Salesforce",
    "hubspot": "HubSpot",
    "dataverse": "Dataverse",
}

#: The researched error-class breakdown, in the research's own words:
#: "error-class breakdown (validation / throttle / auth / vendor-5xx)".
ERROR_CLASSES = ("validation", "throttle", "auth", "vendor_5xx")

ERROR_CLASS_LABELS = {
    "validation": "Validation",
    "throttle": "Throttle",
    "auth": "Auth",
    "vendor_5xx": "Vendor 5xx",
}

#: The metrics an alert rule can watch. Two families, in the research's own
#: words: "alert rules fire when remaining budget crosses a threshold or when
#: the change-stream lag exceeds N seconds". The two budget metrics are the
#: two halves of the normalised pair.
METRICS = ("daily_remaining", "window_remaining", "stream_lag")

METRIC_LABELS = {
    "daily_remaining": "Remaining today",
    "window_remaining": "Remaining this window",
    "stream_lag": "Change-stream lag",
}

#: The delivery channels the research names: "room alert rules
#: (Slack/email/webhook)".
CHANNELS = ("slack", "email", "webhook")

#: Budget metrics fire when remaining budget drops *below* the threshold;
#: the lag metric fires when the lag *exceeds* it. Two verbs, two families,
#: which is the research's own wording and not a generic operator set.
METRIC_COMPARISON = {
    "daily_remaining": "below",
    "window_remaining": "below",
    "stream_lag": "exceeds",
}

#: The collections this workflow owns.
CONNECTOR_COLLECTION = "monitor_connector"
QUOTA_COLLECTION = "quota_observation"
TELEMETRY_COLLECTION = "sync_telemetry"
STREAM_COLLECTION = "stream_observation"
CHANGE_COLLECTION = "change_tracking"
RULE_COLLECTION = "alert_rule"
ROOM_COLLECTION = "room"

#: The vendor surfaces this build can parse, keyed by vendor. Each entry is the
#: researched data source, with the evidence that produced it kept beside it so
#: a reviewer can read the fact without opening the research document.
QUOTA_SURFACES: dict[str, tuple[dict[str, Any], ...]] = {
    "salesforce": (
        {
            "id": "limits_resource",
            "label": "GET /services/data/vXX.X/limits/",
            "sourced": (
                "List information about limits in your org. For each limit, this resource "
                "returns the maximum allocation and the remaining allocation based on usage."
            ),
            "carries": ["daily"],
        },
        {
            "id": "limit_info_header",
            "label": "Sforce-Limit-Info response header",
            "sourced": (
                "This response header is returned in each REST API response... You can use "
                "the information to monitor your API usage."
            ),
            "carries": ["daily"],
        },
        {
            "id": "event_usage_metric",
            "label": "PlatformEventUsageMetric object",
            "sourced": (
                "Monitor Change Event Publishing and Delivery Usage - query the "
                "PlatformEventUsageMetric object."
            ),
            "carries": ["event_delivery"],
        },
    ),
    "hubspot": (
        {
            "id": "rate_limit_headers",
            "label": "X-HubSpot-RateLimit-* response headers",
            "sourced": (
                "X-HubSpot-RateLimit-Interval-Milliseconds | The window of time that the "
                "X-HubSpot-RateLimit-Max and X-HubSpot-RateLimit-Remaining headers apply to."
            ),
            "carries": ["window", "daily"],
        },
        {
            "id": "account_information",
            "label": "Account information API",
            "sourced": (
                "HubSpot's account information API endpoints provide account configuration and "
                "usage data ... the daily API usage and limits for legacy private apps."
            ),
            "carries": ["daily"],
        },
    ),
    "dataverse": (),
}

#: What the research says each vendor's quota model is, in the words of the
#: transformation the workflow is built around: "heterogeneous vendor quota
#: models (daily+burst, per-10s, per-app, per-account) normalise into one
#: 'remaining today / remaining this window' pair".
QUOTA_MODEL_NOTE = {
    "salesforce": "daily org-wide request caps, reported on every call",
    "hubspot": "per-10s windows plus a daily cap, per app",
    "dataverse": "no numeric limits sourced - see the research's own gap",
}

#: The research's own statement of what it does not know, carried verbatim so a
#: reader of ``/vocabulary`` sees it without opening the research file.
RESEARCH_GAPS = (
    "Dataverse's per-service numeric limit table (Service Protection API Limits) was not "
    "found at a readable URL.",
    "HubSpot's newer GraphQL/account-information usage fields were not enumerated in the "
    "page read.",
)


def require_vendor(vendor: Any) -> str:
    """The vendor id, or :class:`UnknownVendor`."""
    text = str(vendor or "").strip().lower()
    if text not in VENDORS:
        raise UnknownVendor(
            f"vendor {vendor!r} is not one of {', '.join(VENDORS)}; a connector outside the "
            "researched three needs its own quota policy before the room can read its surfaces"
        )
    return text


def describe_quotas() -> dict[str, Any]:
    """The per-vendor quota surfaces, as served at ``/vocabulary``."""
    return {
        "model_note": dict(QUOTA_MODEL_NOTE),
        "surfaces": {vendor: list(surfaces) for vendor, surfaces in QUOTA_SURFACES.items()},
        "gaps": list(RESEARCH_GAPS),
    }


def vocabulary() -> dict[str, Any]:
    """The whole published vocabulary, served verbatim at ``GET /vocabulary``."""
    return {
        "vendors": list(VENDORS),
        "vendor_labels": dict(VENDOR_LABELS),
        "error_classes": list(ERROR_CLASSES),
        "error_class_labels": dict(ERROR_CLASS_LABELS),
        "metrics": list(METRICS),
        "metric_labels": dict(METRIC_LABELS),
        "metric_comparison": dict(METRIC_COMPARISON),
        "channels": list(CHANNELS),
        "collections": {
            "connector": CONNECTOR_COLLECTION,
            "quota": QUOTA_COLLECTION,
            "telemetry": TELEMETRY_COLLECTION,
            "stream": STREAM_COLLECTION,
            "change_tracking": CHANGE_COLLECTION,
            "alert_rule": RULE_COLLECTION,
        },
        "quotas": describe_quotas(),
    }


__all__ = [
    "VENDORS",
    "VENDOR_LABELS",
    "ERROR_CLASSES",
    "ERROR_CLASS_LABELS",
    "METRICS",
    "METRIC_LABELS",
    "METRIC_COMPARISON",
    "CHANNELS",
    "CONNECTOR_COLLECTION",
    "QUOTA_COLLECTION",
    "TELEMETRY_COLLECTION",
    "STREAM_COLLECTION",
    "CHANGE_COLLECTION",
    "RULE_COLLECTION",
    "ROOM_COLLECTION",
    "QUOTA_SURFACES",
    "QUOTA_MODEL_NOTE",
    "RESEARCH_GAPS",
    "require_vendor",
    "describe_quotas",
    "vocabulary",
]
