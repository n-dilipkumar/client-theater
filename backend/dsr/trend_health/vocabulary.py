"""The published vocabulary of WF-021, and the collections it owns.

Everything in this module is either quoted from the research or is the name this
build gives to something the research describes. Nothing here is a judgement
call; the judgement calls live in :mod:`dsr.trend_health.inferences`.

Two vocabularies, and they are not the same thing:

**The Trend column.** Dock's own definition of it, quoted in the research:

    "These are based on an algorithm of the engagement of a workspace. **Hot** =
    workspaces that have tons of recent engagement within the last 7 days.
    **Warm** = workspaces that have a decent amount of engagement within the last
    14 days. **Cooling** = workspaces that previously had engagement, but none
    within the last 14 days. **Cold** = workspace with no engagement within the
    last month."

    -- https://help.dock.us/en/articles/11664129-trends-in-workspace-views

That quote is also the source of the three windows, 7 / 14 / 30 days. The fourth
state, Cooling, is Dock's and not Liferay's: the Liferay DSR "Room Trend" widget
this workflow also cites describes only Cold, Warm and Hot, which is a different
product's widget and is why this feature is not the same classifier as the
three-state one in :mod:`dsr.analytics`.

**The engagement events.** The research names the data sources for the metric:
``workspace.viewed``, ``workspace.page.viewed``, ``workspace.file.viewed``,
``workspace.link.clicked`` and ``workspace.order_form.*``. The last is a prefix
in the source itself, so it is matched as one: an order form completing and an
order form being opened are both engagement.
"""

from __future__ import annotations

from typing import Any, Mapping

#: The record collection the webhook seam writes into. Arbitrary JSON in ``data``,
#: like every other collection in this product: a team adding a field to an
#: engagement event ships a record, not a migration.
ENGAGEMENT_COLLECTION = "workspace_engagement"

#: The stored override for the classification rules. A record rather than a
#: column, so a team can retune the thresholds without coordinating with anyone.
RULES_COLLECTION = "trend_health_rules"
RULES_RECORD_ID = "trend_health_rules_default"

#: The room collection, read only. Engagement never creates a room.
ROOM_COLLECTION = "room"

#: The four Trend values, hottest first, with the sourced sentence for each. The
#: order is also the decay order the research states: "a workspace decays from
#: Hot -> Warm -> Cooling -> Cold without any new activity".
TRENDS: tuple[dict[str, Any], ...] = (
    {
        "value": "hot",
        "label": "Hot",
        "window_days": 7,
        "rule": "workspaces that have tons of recent engagement within the last 7 days",
    },
    {
        "value": "warm",
        "label": "Warm",
        "window_days": 14,
        "rule": "workspaces that have a decent amount of engagement within the last 14 days",
    },
    {
        "value": "cooling",
        "label": "Cooling",
        "window_days": 14,
        "rule": "workspaces that previously had engagement, but none within the last 14 days",
    },
    {
        "value": "cold",
        "label": "Cold",
        "window_days": 30,
        "rule": "workspace with no engagement within the last month",
    },
)

#: Hottest to coldest. Used for ranking, for sorting the dashboard's Trend column
#: in the order a rep reads it, and for the decay ladder.
TREND_VALUES: tuple[str, ...] = tuple(str(entry["value"]) for entry in TRENDS)

#: Coldest last: the value a workspace reaches when nothing happens any more.
DECAY_PATH: tuple[str, ...] = TREND_VALUES

#: The label for each value, for clients that render the word rather than the
#: slug. Served as data so a client never hard-codes the vocabulary.
TREND_LABELS: dict[str, str] = {str(entry["value"]): str(entry["label"]) for entry in TRENDS}

#: The sourced sentence for each value.
TREND_RULES: dict[str, str] = {str(entry["value"]): str(entry["rule"]) for entry in TRENDS}

#: Rank per value, lowest is hottest. Lets a client sort without a lookup table.
TREND_RANK: dict[str, int] = {value: rank for rank, value in enumerate(TREND_VALUES)}

#: The windows the research names, as the key the rules record addresses them by.
#: ``hot`` is the window the Hot test counts in, ``warm`` the one the Warm test
#: counts in, and ``cold`` the window whose emptiness means Cold.
WINDOW_KEYS: tuple[str, ...] = ("hot", "warm", "cold")

#: The engagement events the research names, minus the prefixed family.
ENGAGEMENT_EVENT_TYPES: tuple[str, ...] = (
    "workspace.viewed",
    "workspace.page.viewed",
    "workspace.file.viewed",
    "workspace.link.clicked",
)

#: The research writes this one as a glob: ``workspace.order_form.*``. Matched as
#: a prefix so a form being opened, completed or abandoned all count, which is
#: what the source's own spelling asks for.
ORDER_FORM_PREFIX = "workspace.order_form."

#: The family name itself, accepted alongside its members. A glob conventionally
#: does not match its own stem, but a sender integrating against a family name
#: should get a classification rather than a 400 for the difference.
ORDER_FORM_FAMILY = "workspace.order_form"

#: The event that means "a client opened the workspace". This is what the
#: dashboard's researched "Last Client View" column reports; the page, file and
#: link events are engagement, but they are not an opening of the room.
CLIENT_VIEW_EVENT = "workspace.viewed"

#: Who performed the interaction. The research describes the Trend metric as
#: giving "a quick pulse on workspace health and external engagement", so an
#: internal view is recorded but does not count toward the bucket.
AUDIENCES: tuple[str, ...] = ("external", "internal")


def is_engagement_event(event_type: str) -> bool:
    """Is this one of the researched workspace activity events?

    Refuses-by-default is the point: an unrecognised type is a sender using an
    event this workflow does not model, and dropping it quietly would corrupt the
    bucket. See :func:`require_event_type` for the refusal the caller sees.
    """
    return (
        event_type in ENGAGEMENT_EVENT_TYPES
        or event_type == ORDER_FORM_FAMILY
        or event_type.startswith(ORDER_FORM_PREFIX)
    )


def require_event_type(value: Any) -> str:
    """Validate an engagement event type, or say exactly what is accepted."""
    from dsr.trend_health.errors import UnknownEventType

    text = str(value or "").strip()
    if not text:
        accepted = ", ".join(ENGAGEMENT_EVENT_TYPES + (f"{ORDER_FORM_PREFIX}*",))
        raise UnknownEventType(f"event type is required; this workflow models {accepted}")
    if not is_engagement_event(text):
        accepted = ", ".join(ENGAGEMENT_EVENT_TYPES + (f"{ORDER_FORM_PREFIX}*",))
        raise UnknownEventType(
            f"{text!r} is not a researched engagement event; this workflow models {accepted}"
        )
    return text


def require_audience(value: Any = None, *, internal: Any = None) -> str:
    """Normalise the audience, accepting the boolean spelling a sender may use.

    ``internal: true`` is the shape a webhook integration is most likely to
    already carry, and it is the flag that decides whether the event counts
    toward the bucket, so it is read here rather than at each call site.

    The boolean is checked rather than coerced. ``internal: "maybe"`` is a sender
    that misunderstood the field, and ``bool("maybe")`` would silently read as
    "yes, internal" - which is the one answer that makes an event stop counting
    toward the bucket it is supposed to move.
    """
    from dsr.trend_health.errors import TrendError

    if internal is not None and not isinstance(internal, bool):
        raise TrendError(f"internal must be true or false; got {internal!r}")
    if value is None and internal is not None:
        return "internal" if internal else "external"
    text = str(value or "").strip().lower()
    if not text:
        return "external"
    if text not in AUDIENCES:
        raise TrendError(f"audience must be one of {', '.join(AUDIENCES)}; got {value!r}")
    return text


def is_client_view(event_type: str) -> bool:
    return event_type == CLIENT_VIEW_EVENT


def vocabulary() -> dict[str, Any]:
    """Every published name, served as data so a client renders from this."""
    return {
        "trends": [dict(entry) for entry in TRENDS],
        "trend_values": list(TREND_VALUES),
        "trend_labels": dict(TREND_LABELS),
        "trend_rank": dict(TREND_RANK),
        "decay_path": list(DECAY_PATH),
        "engagement_event_types": list(ENGAGEMENT_EVENT_TYPES),
        "order_form_prefix": ORDER_FORM_PREFIX,
        "client_view_event": CLIENT_VIEW_EVENT,
        "audiences": list(AUDIENCES),
        "collections": {
            "engagement": ENGAGEMENT_COLLECTION,
            "rules": RULES_COLLECTION,
        },
    }


def dashboard_fields(mapping: Mapping[str, Any], *names: str, default: Any = "") -> Any:
    """Read the first present key from a room payload.

    The Trend column filters and sorts "by owner, workspace creation date, recent
    client activity", and the room record's own field names are not this
    workflow's to fix. A team that calls it ``rep`` rather than ``owner`` should
    get a working filter rather than a blank column.
    """
    for name in names:
        if name in mapping and mapping[name] not in (None, ""):
            return mapping[name]
    return default
