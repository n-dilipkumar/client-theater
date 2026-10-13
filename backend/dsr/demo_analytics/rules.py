"""The pure rules of WF-127: the branching demo, the heatmap, the roll-up, the
DQL score, and the stakeholder-discovery attribution.

Nothing in this module touches persistence or HTTP. It takes plain dicts and
returns plain dicts, so the whole workflow is testable without a database and a
test can freeze the clock by passing its own ``now``.

Three rules the research under-specifies, each decided here and recorded as an
inference in :mod:`.inferences`:

**Stakeholder discovery is inferred, not declared.** A viewer becomes a
*discovered* stakeholder when their event carries an upstream share reference
(``shared_from``) that names a viewer already known to the demo. The evidence
says discovery comes from "the initial recipient's forward/intra-viral shares;
downstream stakeholder identities", so the attribution is a join on the share
reference rather than a checkbox the viewer ticks.

**A heatmap bucket is a pure function of an event timestamp.** The row is the
weekday and the column is a fixed-width hour slot, both read off the event's own
``occurred_at``. That keeps a test deterministic under a frozen clock, which the
build brief makes a hard rule.

**The metric denominators are stated.** "demos sent" is the distinct set of
initial recipients, so ``view_rate`` is viewers over demos sent and
``discovery_rate`` is discovered stakeholders over demos sent. The evidence
gives the names and no denominators, so each one is defined once here and served
beside the vocabulary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from .errors import DemoValidationError, EventRefused, ShareRefused
from .vocabulary import (
    DEFAULT_CLICK_PATH,
    DEFAULT_DQL_THRESHOLD,
    DEMO_STATES,
    DEMO_TYPES,
    DQL_MAX,
    DQL_WEIGHTS,
    EVENT_TYPES,
    HEATMAP_BUCKET_HOURS,
    LEVELS,
    NODE_KINDS,
    REFUSED_PAYLOAD_KEYS,
    WEEKDAY_NAMES,
)

#: The event spellings a caller may send, mapped onto the one stored name. The
#: stored names are the researched metric vocabulary; the aliases exist so a
#: viewer emitting "download" is not a different event from one emitting
#: "doc_downloaded".
EVENT_ALIASES: dict[str, str] = {
    "view": "view",
    "open": "view",
    "visit": "view",
    "section": "section_selected",
    "select": "section_selected",
    "section_selected": "section_selected",
    "feature_selected": "section_selected",
    "download": "doc_downloaded",
    "doc_downloaded": "doc_downloaded",
    "document_downloaded": "doc_downloaded",
}


def now_utc() -> datetime:
    """The clock, as a function so a caller can replace it in a test."""
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Scalar helpers
# --------------------------------------------------------------------------- #


def parse_instant(
    value: Any, *, default: datetime | None = None, field: str = "occurred_at"
) -> datetime:
    """A timezone-aware instant from an ISO string, a ``datetime``, or nothing.

    A naive value is read as UTC, which is the convention the sibling workflows
    use and what the research's per-viewer event stream assumes.
    """
    if value is None or value == "":
        if default is None:
            raise DemoValidationError(f"{field} is required", field=field)
        return default
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise DemoValidationError(
                f"{field} is not an ISO timestamp: {value!r}", field=field
            ) from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _email(value: Any, *, error: type[Exception], field: str = "viewer") -> str:
    """A viewer identity, normalised to one spelling.

    The researched identity source is the buyer's email address, so the value is
    case-folded: ``Buyer@`` and ``buyer@`` are one stakeholder, not two. A value
    without an ``@`` is refused rather than stored as an opaque id.
    """
    text = str(value or "").strip().lower()
    if not text or "@" not in text or text.startswith("@") or text.endswith("@"):
        raise error(f"{field} must be an email address, got {value!r}", field=field)
    return text


def normalise_email(value: Any, *, field: str = "viewer") -> str:
    """A viewer email, or :class:`DemoValidationError`."""
    return _email(value, error=DemoValidationError, field=field)


def rate(numerator: int, denominator: int) -> float:
    """A ratio rounded to four places, and zero when the denominator is zero.

    A rate with no denominator is not 100 percent and not an error: it is a rate
    this demo cannot compute yet, so it reads as zero rather than crashing.
    """
    if not denominator:
        return 0.0
    return round(numerator / denominator, 4)


def _refuse_payload_keys(data: Mapping[str, Any], *, error: type[Exception]) -> None:
    for key in data:
        if str(key).lower() in REFUSED_PAYLOAD_KEYS:
            raise error(
                f"{key!r} is refused: this workflow records telemetry, not document bodies",
                field=str(key),
            )


# --------------------------------------------------------------------------- #
# The branching demo
# --------------------------------------------------------------------------- #


def normalise_nodes(raw: Any) -> list[dict[str, Any]]:
    """Validate and normalise the click-path tree, or return the default one.

    A node is ``{id, label, kind, next}``. ``next`` is the branch, so the tree is
    what makes the demo branching rather than a linear video. The rules are the
    minimum that keeps ``resolve_path`` total: unique ids, a known kind, and
    every ``next`` naming a node that exists.
    """
    if raw is None:
        return [dict(node) for node in DEFAULT_CLICK_PATH]
    if isinstance(raw, str):
        raw = [line for line in raw.splitlines() if line.strip()]
    if not isinstance(raw, (list, tuple)) or not raw:
        raise DemoValidationError("a click path needs at least one node", field="nodes")

    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw:
        if isinstance(entry, str):
            node: dict[str, Any] = {"id": entry}
        elif isinstance(entry, Mapping):
            node = dict(entry)
        else:
            raise DemoValidationError(
                f"a node must be an object or a string, got {entry!r}", field="nodes"
            )
        node_id = _optional_text(node.get("id"))
        if not node_id:
            raise DemoValidationError("every node needs an id", field="nodes")
        if node_id in seen:
            raise DemoValidationError(f"duplicate node id {node_id!r}", field="nodes", node=node_id)
        seen.add(node_id)
        kind = str(node.get("kind") or "screen").strip().lower()
        if kind not in NODE_KINDS:
            raise DemoValidationError(
                f"node kind must be one of {list(NODE_KINDS)}, got {kind!r}",
                field="nodes",
                node=node_id,
            )
        following = node.get("next") or []
        if isinstance(following, str):
            following = [part.strip() for part in following.split(",") if part.strip()]
        if not isinstance(following, (list, tuple)):
            raise DemoValidationError(
                f"node {node_id!r} next must be a list", field="nodes", node=node_id
            )
        nodes.append(
            {
                "id": node_id,
                "label": _optional_text(node.get("label")) or node_id,
                "kind": kind,
                "next": [str(part).strip() for part in following if str(part).strip()],
            }
        )

    known = {node["id"] for node in nodes}
    for node in nodes:
        for reference in node["next"]:
            if reference not in known:
                raise DemoValidationError(
                    f"node {node['id']!r} branches to unknown node {reference!r}",
                    field="nodes",
                    node=node["id"],
                    ref=reference,
                )
    return nodes


def normalise_demo(payload: Mapping[str, Any] | None, *, now: datetime) -> dict[str, Any]:
    """The stored shape of one demo, built from a recording or from a click path.

    The researched flow is "screen capture + click-path -> interactive demo
    build", so a payload with no explicit tree is converted from the default
    branching template rather than rejected. A payload *with* a tree is validated
    in full.
    """
    data = dict(payload or {})
    title = _optional_text(data.get("title"))
    if not title:
        raise DemoValidationError("a demo needs a title", field="title")
    demo_type = str(data.get("demo_type") or data.get("type") or "sandbox").strip().lower()
    if demo_type not in DEMO_TYPES:
        raise DemoValidationError(
            f"demo_type must be one of {list(DEMO_TYPES)}, got {demo_type!r}",
            field="demo_type",
        )
    state = str(data.get("state") or "draft").strip().lower()
    if state not in DEMO_STATES:
        raise DemoValidationError(
            f"state must be one of {list(DEMO_STATES)}, got {state!r}", field="state"
        )
    nodes = normalise_nodes(data.get("nodes"))
    known = {node["id"] for node in nodes}
    entry = _optional_text(data.get("entry")) or nodes[0]["id"]
    if entry not in known:
        raise DemoValidationError(f"entry {entry!r} is not a node in the click path", field="entry")

    published_at = data.get("published_at")
    if state == "published" and not published_at:
        published_at = now.isoformat()
    return {
        "title": title,
        "demo_type": demo_type,
        "state": state,
        "recording_ref": _optional_text(data.get("recording_ref") or data.get("recording")),
        "team": _optional_text(data.get("team")),
        "owner": _optional_text(data.get("owner")),
        "entry": entry,
        "nodes": nodes,
        "sections": [node["id"] for node in nodes],
        "published_at": published_at,
        "archived_at": data.get("archived_at"),
        "created_at": data.get("created_at") or now.isoformat(),
    }


def node_index(demo: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The click path keyed by node id."""
    return {node["id"]: node for node in demo.get("nodes") or []}


def branch_options(demo: Mapping[str, Any], node_id: str) -> list[dict[str, Any]]:
    """The branches out of one node, as label-bearing options."""
    nodes = node_index(demo)
    node = nodes.get(node_id)
    if node is None:
        raise DemoValidationError(f"unknown node {node_id!r}", field="node_id")
    return [
        {"id": reference, "label": nodes[reference]["label"]}
        for reference in node["next"]
        if reference in nodes
    ]


def resolve_path(demo: Mapping[str, Any], clicked: Sequence[Any]) -> list[str]:
    """Walk a viewer's clicks through the tree, refusing a step the tree lacks.

    This is what makes the demo *branching* rather than a recording: a click that
    is not reachable from the current node is a refusal, not a silent jump.
    """
    nodes = node_index(demo)
    current = demo.get("entry") or (next(iter(nodes), ""))
    path = [current]
    for raw_step in clicked or []:
        step = _optional_text(raw_step)
        node = nodes.get(current)
        if node is None or step is None:
            raise EventRefused(f"{raw_step!r} is not a step in this demo", field="step")
        if step not in node["next"]:
            raise EventRefused(
                f"{step!r} is not reachable from {current!r}", field="step", at=current
            )
        current = step
        path.append(step)
    return path


def reach_counts(
    demo: Mapping[str, Any], events: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """How many viewers reached each node, in click-path order.

    The entry is reached by every view, so it is seeded with the view count; the
    other nodes are reached by the ``section_selected`` events that name them.
    """
    viewers_by_node: dict[str, set[str]] = {node["id"]: set() for node in demo.get("nodes") or []}
    entry = demo.get("entry")
    for event in events:
        if event.get("is_internal"):
            continue
        if event.get("event") == "view" and entry in viewers_by_node:
            viewers_by_node[entry].add(str(event.get("viewer")))
        if event.get("event") == "section_selected":
            section = str(event.get("section") or "")
            if section in viewers_by_node:
                viewers_by_node[section].add(str(event.get("viewer")))
    rows = []
    for node in demo.get("nodes") or []:
        reached = viewers_by_node.get(node["id"], set())
        rows.append(
            {
                "section": node["id"],
                "label": node["label"],
                "kind": node["kind"],
                "viewers": len(reached),
                "reached": len(reached),
            }
        )
    return rows


def section_counts(demo: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The "features/sections selected" metric, one row per node."""
    counts: dict[str, int] = {node["id"]: 0 for node in demo.get("nodes") or []}
    viewers: dict[str, set[str]] = {node["id"]: set() for node in demo.get("nodes") or []}
    for event in events:
        if event.get("is_internal") or event.get("event") != "section_selected":
            continue
        section = str(event.get("section") or "")
        if section in counts:
            counts[section] += 1
            viewers[section].add(str(event.get("viewer")))
    rows = [
        {
            "section": node["id"],
            "label": node["label"],
            "count": counts[node["id"]],
            "viewers": len(viewers[node["id"]]),
        }
        for node in demo.get("nodes") or []
    ]
    return {
        "sections": rows,
        "total": sum(row["count"] for row in rows),
        "most_selected": max(rows, key=lambda row: (row["count"], row["section"]))
        if rows
        else None,
    }


# --------------------------------------------------------------------------- #
# The viewer event stream
# --------------------------------------------------------------------------- #


def normalise_event(payload: Mapping[str, Any] | None, *, now: datetime) -> dict[str, Any]:
    """One per-viewer event, validated and normalised.

    A section selection must name a section. The upstream share reference is the
    one field that makes the event attributable to a re-share, so it is kept as a
    normalised email and dropped when it names the viewer themselves.
    """
    data = dict(payload or {})
    _refuse_payload_keys(data, error=EventRefused)
    raw = str(data.get("event") or data.get("type") or "view").strip().lower()
    event = EVENT_ALIASES.get(raw)
    if event is None or event not in EVENT_TYPES:
        raise EventRefused(f"event must be one of {list(EVENT_TYPES)}, got {raw!r}", field="event")
    try:
        viewer = normalise_email(data.get("viewer") or data.get("email"))
    except DemoValidationError as exc:
        raise EventRefused(str(exc), field="viewer") from exc
    section = _optional_text(data.get("section") or data.get("node"))
    if event == "section_selected" and not section:
        raise EventRefused("a section_selected event needs a section", field="section")
    upstream = data.get("shared_from") or data.get("from_viewer")
    shared_from: str | None = None
    if upstream:
        try:
            shared_from = normalise_email(upstream, field="shared_from")
        except DemoValidationError as exc:
            raise EventRefused(str(exc), field="shared_from") from exc
        if shared_from == viewer:
            shared_from = None
    occurred = parse_instant(data.get("occurred_at"), default=now)
    return {
        "viewer": viewer,
        "event": event,
        "section": section,
        "shared_from": shared_from,
        "is_internal": _as_bool(data.get("is_internal")),
        "occurred_at": occurred.isoformat(),
    }


def normalise_share(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """A distribution: an initial send, or a forward re-share.

    A forward share must name its sender, because that sender is what the
    discovery attribution joins on. Recipients are normalised and deduplicated,
    so one address shared twice is one recipient.
    """
    data = dict(payload or {})
    kind = str(data.get("kind") or "initial").strip().lower()
    if kind not in ("initial", "forward"):
        raise ShareRefused(f"kind must be 'initial' or 'forward', got {kind!r}", field="kind")
    raw = data.get("recipients")
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ShareRefused("a share needs at least one recipient", field="recipients")
    recipients: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        try:
            address = normalise_email(entry, field="recipients")
        except DemoValidationError as exc:
            raise ShareRefused(str(exc), field="recipients") from exc
        if address in seen:
            continue
        seen.add(address)
        recipients.append(address)
    sender: str | None = None
    if data.get("sender"):
        try:
            sender = normalise_email(data["sender"], field="sender")
        except DemoValidationError as exc:
            raise ShareRefused(str(exc), field="sender") from exc
    if kind == "forward" and not sender:
        raise ShareRefused("a forward share needs a sender", field="sender")
    return {
        "kind": kind,
        "recipients": recipients,
        "sender": sender,
        "channel": _optional_text(data.get("channel")) or "link",
    }


def stakeholder_role(viewer: str, *, known: set[str], shared_from: str | None) -> str:
    """The role a viewer earns when the demo first sees them.

    A viewer already known is not re-roled. Otherwise: a known upstream sender
    makes the viewer *discovered* - the inferred intra-viral attribution - and
    anything else is a direct open.
    """
    if viewer in known:
        return "known"
    if shared_from and shared_from in known and shared_from != viewer:
        return "discovered"
    return "direct"


# --------------------------------------------------------------------------- #
# Metrics, heatmap, DQL, roll-up
# --------------------------------------------------------------------------- #


def _share_recipients(share: Mapping[str, Any]) -> list[str]:
    """The recipients of one share row.

    A share row holds one recipient (``recipient``), and a payload may hand the
    metrics a whole list (``recipients``). Both spellings resolve here so a
    caller cannot undercount ``demos_sent`` by sending the other one.
    """
    raw = share.get("recipients")
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw]
    single = share.get("recipient")
    return [str(single)] if single else []


def internal_only_viewers(events: Sequence[Mapping[str, Any]]) -> set[str]:
    """The viewers whose only events are internal previews.

    A seller previewing their own demo is not a buyer signal. This is the rule
    the sibling analytics workflows apply to every metric they compute, and it
    is computed here rather than stored so no counter can drift.
    """
    internal = {str(event.get("viewer")) for event in events if event.get("is_internal")}
    external = {str(event.get("viewer")) for event in events if not event.get("is_internal")}
    return internal - external


def metrics(
    events: Sequence[Mapping[str, Any]],
    stakeholders: Sequence[Mapping[str, Any]],
    shares: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """The researched metric block, with every denominator stated.

    Buyers only: an internal preview is recorded and excluded, which is the rule
    the sibling analytics workflows use for every metric they compute. The
    denominators are ``demos_sent`` for both rates, and ``demos_sent`` is the
    distinct set of initial recipients rather than the number of share rows.
    """
    external = [event for event in events if not event.get("is_internal")]
    views = [event for event in external if event.get("event") == "view"]
    initial = [share for share in shares if share.get("kind") == "initial"]
    demos_sent = len({str(r) for share in initial for r in _share_recipients(share)})
    discovered = [row for row in stakeholders if row.get("role") == "discovered"]
    viewers = {str(event.get("viewer")) for event in views}
    sections = [event for event in external if event.get("event") == "section_selected"]
    downloads = [event for event in external if event.get("event") == "doc_downloaded"]
    return {
        "demos_sent": demos_sent,
        "stakeholders_discovered": len(discovered),
        "discovery_rate": rate(len(discovered), demos_sent),
        "views": len(views),
        "view_rate": rate(len(viewers), demos_sent),
        "features_selected": len(sections),
        "docs_downloaded": len(downloads),
        "stakeholders": len(stakeholders),
        "viewers": len(viewers),
        "internal_previews": len(internal_only_viewers(events)),
        "denominators": {
            "discovery_rate": "stakeholders_discovered / demos_sent",
            "view_rate": "distinct viewers / demos_sent",
            "demos_sent": "distinct initial recipients",
        },
    }


def heatmap(
    events: Sequence[Mapping[str, Any]], *, bucket_hours: int = HEATMAP_BUCKET_HOURS
) -> dict[str, Any]:
    """Activity by weekday and hour slot, from each event's own timestamp.

    Internal previews are excluded, because the heatmap is a buyer-activity
    chart. A bucket is derived only from ``occurred_at``, so the result is
    deterministic under a frozen clock.
    """
    if bucket_hours < 1 or 24 % bucket_hours:
        raise DemoValidationError("bucket_hours must divide 24", field="bucket_hours")
    slots = 24 // bucket_hours
    buckets = [
        {
            "index": index,
            "start_hour": index * bucket_hours,
            "label": f"{index * bucket_hours:02d}:00-{(index + 1) * bucket_hours:02d}:00",
        }
        for index in range(slots)
    ]
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    for event in events:
        if event.get("is_internal"):
            continue
        moment = parse_instant(event.get("occurred_at"))
        weekday = moment.weekday()
        index = moment.hour // bucket_hours
        cell = cells.setdefault(
            (weekday, index),
            {
                "weekday": WEEKDAY_NAMES[weekday],
                "weekday_index": weekday,
                "bucket": index,
                "start_hour": index * bucket_hours,
                "count": 0,
                "viewers": set(),
            },
        )
        cell["count"] += 1
        cell["viewers"].add(str(event.get("viewer")))
    rows = [{**cell, "viewers": len(cell["viewers"])} for cell in cells.values()]
    rows.sort(key=lambda row: (row["weekday_index"], row["bucket"]))
    total = sum(row["count"] for row in rows)
    busiest = max(rows, key=lambda row: (row["count"], -row["weekday_index"])) if rows else None
    return {
        "bucket_hours": bucket_hours,
        "weekdays": list(WEEKDAY_NAMES),
        "buckets": buckets,
        "cells": rows,
        "max_count": max((row["count"] for row in rows), default=0),
        "total": total,
        "busiest": busiest,
    }


def dql_score(
    events: Sequence[Mapping[str, Any]], *, threshold: int = DEFAULT_DQL_THRESHOLD
) -> dict[str, Any]:
    """The demo-qualified-lead score for one viewer's events.

    The evidence names the automation and no formula, so this is an inference:
    a view is worth :data:`~.vocabulary.DQL_WEIGHTS` points, each distinct
    section selected adds points up to a cap, a download adds points, and a
    second view is the "returned" signal. The score is capped at 100 and is
    qualified at the threshold the vocabulary publishes.
    """
    external = [event for event in events if not event.get("is_internal")]
    views = [event for event in external if event.get("event") == "view"]
    sections = {
        str(event.get("section")) for event in external if event.get("event") == "section_selected"
    }
    downloaded = any(event.get("event") == "doc_downloaded" for event in external)
    components = {
        "viewed": DQL_WEIGHTS["viewed"] if views else 0,
        "sections": min(len(sections) * DQL_WEIGHTS["per_section"], DQL_WEIGHTS["section_cap"]),
        "downloaded": DQL_WEIGHTS["downloaded"] if downloaded else 0,
        "returned": DQL_WEIGHTS["returned"] if len(views) >= 2 else 0,
    }
    score = min(DQL_MAX, sum(components.values()))
    return {
        "score": score,
        "max": DQL_MAX,
        "threshold": threshold,
        "qualified": score >= threshold,
        "components": components,
    }


def buyer_group(event: Mapping[str, Any]) -> str:
    """The buyer group a viewer belongs to.

    The researched six levels start at "buyer-group analysis", so a viewer needs
    a group. The event may carry one; otherwise the email domain is the group,
    which is the only grouping a bare event stream supports.
    """
    explicit = _optional_text(event.get("buyer_group"))
    if explicit:
        return explicit
    viewer = str(event.get("viewer") or "")
    _, _, domain = viewer.partition("@")
    return domain or "unknown"


def _scope(
    demo_ids: set[str],
    events: Sequence[Mapping[str, Any]],
    stakeholders: Sequence[Mapping[str, Any]],
    shares: Sequence[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    scoped_events = [event for event in events if str(event.get("demo_id")) in demo_ids]
    scoped_stakeholders = [row for row in stakeholders if str(row.get("demo_id")) in demo_ids]
    scoped_shares = [share for share in shares if str(share.get("demo_id")) in demo_ids]
    return scoped_events, scoped_stakeholders, scoped_shares


def _group_rows(
    level: str,
    demos: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    stakeholders: Sequence[Mapping[str, Any]],
    shares: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """The groups a level drills into, as ``(key, label, demo_ids)`` triples."""
    if level == "top_level":
        return [{"key": "all", "label": "All demos", "demo_ids": [str(d["id"]) for d in demos]}]
    if level == "all_demos":
        return [
            {
                "key": str(demo["id"]),
                "label": demo.get("title") or str(demo["id"]),
                "demo_ids": [str(demo["id"])],
            }
            for demo in demos
        ]
    if level in ("team", "individual"):
        field = "team" if level == "team" else "owner"
        groups: dict[str, list[str]] = {}
        for demo in demos:
            key = _optional_text(demo.get(field)) or "unassigned"
            groups.setdefault(key, []).append(str(demo["id"]))
        return [{"key": key, "label": key, "demo_ids": ids} for key, ids in sorted(groups.items())]
    if level == "recently_shared":
        if not shares:
            return []
        latest = max(shares, key=lambda share: str(share.get("shared_at") or ""))
        demo_id = str(latest.get("demo_id"))
        demo = next((row for row in demos if str(row["id"]) == demo_id), None)
        return [
            {
                "key": demo_id,
                "label": (demo or {}).get("title") or demo_id,
                "demo_ids": [demo_id],
            }
        ]
    # buyer_group is grouped on the event, not on the demo, so its demo_ids are
    # filled per group below.
    return []


def rollup(
    level: str,
    demos: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    stakeholders: Sequence[Mapping[str, Any]],
    shares: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """One of the six researched drill-down levels, as a list of metric groups."""
    if level not in LEVELS:
        raise DemoValidationError(
            f"level must be one of {list(LEVELS)}, got {level!r}", field="level"
        )

    groups: list[dict[str, Any]] = []
    if level == "buyer_group":
        buckets: dict[str, list[Mapping[str, Any]]] = {}
        for event in events:
            buckets.setdefault(buyer_group(event), []).append(event)
        for key in sorted(buckets):
            group_events = buckets[key]
            group_viewers = {str(event.get("viewer")) for event in group_events}
            group_stakeholders = [
                row for row in stakeholders if str(row.get("viewer")) in group_viewers
            ]
            groups.append(
                {
                    "key": key,
                    "label": key,
                    "demo_ids": sorted({str(event.get("demo_id")) for event in group_events}),
                    "events": group_events,
                    "stakeholders": group_stakeholders,
                    "shares": [],
                }
            )
    else:
        for spec in _group_rows(level, demos, events, stakeholders, shares):
            scoped_events, scoped_stakeholders, scoped_shares = _scope(
                set(spec["demo_ids"]), events, stakeholders, shares
            )
            groups.append(
                {
                    "key": spec["key"],
                    "label": spec["label"],
                    "demo_ids": spec["demo_ids"],
                    "events": scoped_events,
                    "stakeholders": scoped_stakeholders,
                    "shares": scoped_shares,
                }
            )

    for group in groups:
        group["metrics"] = metrics(
            group.pop("events"), group.pop("stakeholders"), group.pop("shares")
        )
        group["demos"] = len(group["demo_ids"])
    return {"level": level, "levels": list(LEVELS), "count": len(groups), "groups": groups}


__all__ = [
    "EVENT_ALIASES",
    "branch_options",
    "buyer_group",
    "dql_score",
    "heatmap",
    "internal_only_viewers",
    "metrics",
    "node_index",
    "normalise_demo",
    "normalise_email",
    "normalise_event",
    "normalise_nodes",
    "normalise_share",
    "now_utc",
    "parse_instant",
    "rate",
    "reach_counts",
    "resolve_path",
    "rollup",
    "section_counts",
    "stakeholder_role",
]
