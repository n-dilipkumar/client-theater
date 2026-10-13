"""The rules: block validation, filter evaluation, and the send-window decision.

Everything here is a rule, not a framework. It reads a mapping and returns a mapping, raises
one of the errors in :mod:`dsr.nurture_series.errors`, and knows nothing about HTTP, SQLite or
a request. That is what makes every rule below testable without a server, and it is why the
HTTP layer in ``dsr.features.wf104_nurture_series`` is a thin translation of what happens here.

The rules the research fixes exactly
------------------------------------

**Four block kinds and no others.** "There are four kinds of block" - rule / content / wait /
tag. :func:`validate_block` refuses a fifth by name.

**The send window is a retry window, not a delay.** "For each message block, you can define how
long a message should *try* to send for". :func:`decide_channel` decides whether the primary
channel can deliver now; a block that cannot deliver inside its window does not sleep, it goes
on to the offline branch.

**The offline branch is a timeout.** "If the person still hasn't come online after 30d, they
will leave this path". :func:`fallback_reason` returns the timeout reason when the buyer is
still offline once the window has elapsed, and a channel reason when they are online but the
primary channel is unavailable.

**Two rule wait modes.** "'Try to match for x time' ... will wait up to that amount of time";
"'Try to match once' ... as soon as they reach that block ... they will leave the path". Both
live on the rule block, and :func:`rule_satisfied` is the single match test.

What this module will not do
----------------------------

**It will not resolve a buyer.** The audience is caller-supplied - Chosen by Jev, audit
``jev-20261013T150622-7908-82240`` - so a contact is a mapping the caller hands in and this
module filters it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from dsr.nurture_series import vocabulary as vocab
from dsr.nurture_series.errors import SeriesRefused

# --------------------------------------------------------------------------- #
# Instants: every time this workflow handles is derived, never a literal
# --------------------------------------------------------------------------- #


def to_iso(moment: datetime) -> str:
    """An instant as ISO-8601 UTC with a ``Z`` designator."""

    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_instant(value: Any) -> datetime | None:
    """Parse an ISO-8601 instant, requiring a UTC designator.

    Returns ``None`` for anything unparsable rather than guessing, because a wait's time is the
    one value in this workflow where a wrong guess is worse than no value.
    """

    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
# Bounds
# --------------------------------------------------------------------------- #


def require_positive_seconds(
    value: Any,
    *,
    field: str,
    default: float | None = None,
    maximum: float | None = None,
) -> float:
    """A duration in seconds: positive, numeric, and within its bound.

    A zero or negative duration is refused because a window that has already elapsed, or a
    wait that ends at the instant it began, is not a window or a wait at all.
    """

    if value in (None, ""):
        if default is not None:
            return float(default)
        raise SeriesRefused(
            f"{field} is required",
            code="duration_out_of_range",
            errors={field: "required"},
        )
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise SeriesRefused(
            f"{field} must be a number of seconds",
            code="duration_out_of_range",
            errors={field: "not a number"},
        ) from exc
    if seconds <= 0:
        raise SeriesRefused(
            f"{field} must be a positive number of seconds",
            code="duration_out_of_range",
            errors={field: "must be positive"},
        )
    if maximum is not None and seconds > maximum:
        raise SeriesRefused(
            f"{field} must be at most {maximum} seconds",
            code="duration_out_of_range",
            errors={field: f"at most {maximum}"},
        )
    return seconds


# --------------------------------------------------------------------------- #
# The audience
# --------------------------------------------------------------------------- #


def normalise_contact(entry: Mapping[str, Any]) -> dict[str, Any]:
    """One buyer, as the filter evaluator wants it.

    The audience is caller-supplied: this package stores no contact rows, and the CRM that owns
    them (WF-039) is the caller's to read. A contact carries its ``attributes``, its
    ``company``, whether it is ``online`` right now, and which ``channels`` can reach it.
    """

    contact_id = str(entry.get("contact_id") or entry.get("id") or "").strip()
    if not contact_id:
        raise SeriesRefused(
            "a contact needs a contact_id",
            code="bad_audience",
            errors={"contact_id": "required"},
        )
    channels = entry.get("channels")
    if channels is None:
        channels = list(vocab.CHANNELS)
    if isinstance(channels, str):
        channels = [channels]
    if not isinstance(channels, Sequence):
        raise SeriesRefused(
            "a contact's channels must be a list",
            code="bad_audience",
            errors={"channels": "not a list"},
        )
    attributes = dict(entry.get("attributes") or {})
    tags = entry.get("tags")
    if tags is not None and "tags" not in attributes:
        attributes["tags"] = list(tags)
    return {
        "contact_id": contact_id,
        "attributes": attributes,
        "company": dict(entry.get("company") or {}),
        "online": bool(entry.get("online", False)),
        "channels": [str(c).strip().lower().replace("-", "_") for c in channels if str(c).strip()],
    }


def normalise_contacts(audience: Any) -> list[dict[str, Any]]:
    """A whole audience, each contact normalised, refusing anything that is not a list."""

    if audience in (None, ""):
        return []
    if not isinstance(audience, Sequence) or isinstance(audience, (str, bytes)):
        raise SeriesRefused(
            "audience must be a list of contacts",
            code="bad_audience",
            errors={"audience": "not a list"},
        )
    out: list[dict[str, Any]] = []
    for entry in audience:
        if not isinstance(entry, Mapping):
            raise SeriesRefused(
                "each contact must be an object",
                code="bad_audience",
                errors={"audience": "an entry is not an object"},
            )
        out.append(normalise_contact(entry))
    return out


# --------------------------------------------------------------------------- #
# Filter evaluation over contact and company attributes
# --------------------------------------------------------------------------- #


def resolve_path(mapping: Mapping[str, Any], dotted: str) -> tuple[bool, Any]:
    """Resolve a dotted JSON path inside a mapping, reporting whether it was present."""

    node: Any = mapping
    for part in dotted.split("."):
        if isinstance(node, Mapping) and part in node:
            node = node[part]
        else:
            return (False, None)
    return (True, node)


def filter_value(contact: Mapping[str, Any], field: str) -> tuple[bool, Any]:
    """The value a filter field reads from a contact.

    A field starting with ``company.`` resolves inside the contact's ``company`` object; every
    other field resolves inside its ``attributes``. That split is the whole of the research's
    "Contact attributes / company attributes" distinction.
    """

    if field.startswith(vocab.COMPANY_FIELD_PREFIX):
        return resolve_path(contact.get("company") or {}, field[len(vocab.COMPANY_FIELD_PREFIX) :])
    return resolve_path(contact.get("attributes") or {}, field)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _equal(value: Any, expected: Any) -> bool:
    """Value equality with numeric coercion, so ``40`` and ``40.0`` are the same score."""

    if _is_number(value) and _is_number(expected):
        return float(value) == float(expected)
    if isinstance(value, bool) or isinstance(expected, bool):
        return value is expected
    return value == expected


def _contains(value: Any, expected: Any) -> bool:
    if isinstance(value, (list, tuple, set)):
        return any(_equal(item, expected) for item in value)
    if isinstance(value, str):
        return str(expected) in value
    return False


def matches_filter(contact: Mapping[str, Any], rule: Mapping[str, Any]) -> tuple[bool, str]:
    """Whether one contact matches one filter, with the reason either way.

    Returns ``(matched, reason)``. The reason names the field and the operator, so a page can
    say why a buyer did not enter instead of showing a bare count.
    """

    field = str(rule.get("field") or "")
    operator = str(rule.get("op") or "")
    expected = rule.get("value")
    found, value = filter_value(contact, field)

    if operator == "exists":
        want = expected if isinstance(expected, bool) else True
        present = found and value is not None
        return (present == want, f"{field} {'is present' if want else 'is absent'}")

    if operator == "in":
        options = expected if isinstance(expected, (list, tuple, set)) else [expected]
        matched = any(_equal(value, option) for option in options)
        return (matched, f"{field} in {list(options)}")

    if operator == "contains":
        return (_contains(value, expected), f"{field} contains {expected!r}")

    if operator in ("gt", "gte", "lt", "lte"):
        if not (_is_number(value) and _is_number(expected)):
            return (False, f"{field} is not comparable to {expected!r}")
        left, right = float(value), float(expected)
        matched = {
            "gt": left > right,
            "gte": left >= right,
            "lt": left < right,
            "lte": left <= right,
        }[operator]
        return (matched, f"{field} {operator} {expected}")

    # eq / neq
    equal = _equal(value, expected)
    return (equal if operator == "eq" else not equal, f"{field} {operator} {expected!r}")


def filter_match(
    contact: Mapping[str, Any], filters: Iterable[Mapping[str, Any]], match: str = "all"
) -> tuple[bool, int, int]:
    """Whether a contact matches a filter list, returning ``(passed, matched, total)``.

    An empty list passes: a rule block with no filters gates nobody, which is the researched
    default entry state. ``match`` is ``all`` or ``any``.
    """

    rules = list(filters)
    if not rules:
        return (True, 0, 0)
    results = [matches_filter(contact, rule)[0] for rule in rules]
    passed = any(results) if match == "any" else all(results)
    return (passed, sum(1 for r in results if r), len(results))


# --------------------------------------------------------------------------- #
# Block validation
# --------------------------------------------------------------------------- #


def validate_filter(rule: Any, index: int) -> dict[str, Any]:
    """One filter: a published field, a published operator, and a value for it."""

    if not isinstance(rule, Mapping):
        raise SeriesRefused(
            f"filter {index} must be an object",
            code="bad_filter",
            errors={f"filters.{index}": "not an object"},
        )
    field = vocab.require_filter_field(rule.get("field"))
    operator = vocab.require_filter_operator(rule.get("op"))
    if operator != "exists" and "value" not in rule:
        raise SeriesRefused(
            f"filter {index} on {field} with {operator} needs a value",
            code="bad_filter",
            errors={f"filters.{index}.value": "required"},
        )
    return {"field": field, "op": operator, "value": rule.get("value")}


def validate_filters(raw: Any) -> list[dict[str, Any]]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise SeriesRefused(
            "filters must be a list",
            code="bad_filter",
            errors={"filters": "not a list"},
        )
    return [validate_filter(rule, index) for index, rule in enumerate(raw)]


def validate_block(block: Any, index: int, *, is_entry: bool) -> dict[str, Any]:
    """One block, normalised and validated against its kind's rules.

    The four kinds are a closed set, so an unknown kind is refused by name. An entry rule
    filters who enters; every other rule block gates progression and needs a condition, a
    filter, or both.
    """

    if not isinstance(block, Mapping):
        raise SeriesRefused(
            f"block {index} must be an object",
            code="bad_block",
            errors={f"blocks.{index}": "not an object"},
        )
    kind = vocab.require_block_kind(block.get("kind"))
    out: dict[str, Any] = {
        "id": str(block.get("id") or f"b{index}"),
        "kind": kind,
        "index": index,
    }

    if kind == "rule":
        filters = validate_filters(block.get("filters"))
        out["filters"] = filters
        out["match"] = "any" if str(block.get("match") or "all").strip().lower() == "any" else "all"
        if is_entry:
            out["role"] = "entry"
            out["wait_mode"] = None
            out["condition"] = None
            out["match_within_seconds"] = None
            return out
        out["role"] = "branch"
        condition = block.get("condition")
        if condition in (None, "") and not filters:
            raise SeriesRefused(
                f"rule block {index} needs a condition or at least one filter",
                code="bad_filter",
                errors={f"blocks.{index}": "needs a condition or filters"},
            )
        out["condition"] = vocab.require_rule_condition(condition) if condition else None
        mode = vocab.require_rule_match_mode(block.get("wait_mode") or "once")
        out["wait_mode"] = mode
        if mode == "for":
            out["match_within_seconds"] = require_positive_seconds(
                block.get("match_within_seconds"),
                field="match_within_seconds",
                default=vocab.DEFAULT_MATCH_WITHIN_SECONDS,
            )
        else:
            out["match_within_seconds"] = None
        return out

    if kind == "content":
        out["role"] = "content"
        out["channel"] = vocab.require_channel(block.get("channel") or "in_app")
        out["content_type"] = vocab.require_content_type(block.get("content_type") or "post")
        out["body"] = str(block.get("body") or "")
        out["goal"] = str(block.get("goal") or "")
        out["scheduling"] = dict(block.get("scheduling") or {})
        fallback_enabled = block.get("fallback_enabled")
        if fallback_enabled is None:
            fallback_enabled = bool(block.get("fallback_channel"))
        out["fallback_enabled"] = bool(fallback_enabled)
        if out["fallback_enabled"]:
            out["fallback_channel"] = vocab.require_channel(
                block.get("fallback_channel") or vocab.FALLBACK_CHANNEL_DEFAULT
            )
            window = block.get("send_window_seconds")
            if window is not None and _is_number(window) and float(window) <= 0:
                raise SeriesRefused(
                    f"content block {index} has a fallback but no send window to retry for",
                    code="send_window_required",
                    errors={f"blocks.{index}.send_window_seconds": "required"},
                )
            out["send_window_seconds"] = require_positive_seconds(
                window,
                field="send_window_seconds",
                default=vocab.DEFAULT_SEND_WINDOW_SECONDS,
            )
        else:
            out["fallback_channel"] = None
            out["send_window_seconds"] = None
        return out

    if kind == "wait":
        out["role"] = "wait"
        out["duration_seconds"] = require_positive_seconds(
            block.get("duration_seconds"), field="duration_seconds"
        )
        return out

    # tag
    tag = str(block.get("tag") or "").strip()
    if not tag:
        raise SeriesRefused(
            f"tag block {index} needs a tag",
            code="bad_block",
            errors={f"blocks.{index}.tag": "required"},
        )
    out["role"] = "tag"
    out["tag"] = tag
    return out


def validate_series(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The whole series: a name and an ordered, non-empty list of blocks."""

    name = str(payload.get("name") or "").strip()
    if not name:
        raise SeriesRefused(
            "a series needs a name",
            code="series_name_required",
            errors={"name": "required"},
        )
    raw_blocks = payload.get("blocks")
    if not isinstance(raw_blocks, Sequence) or isinstance(raw_blocks, (str, bytes)):
        raise SeriesRefused(
            "a series needs an ordered list of blocks",
            code="no_blocks",
            errors={"blocks": "required, as a list"},
        )
    if not raw_blocks:
        raise SeriesRefused(
            "a series needs at least one block",
            code="no_blocks",
            errors={"blocks": "at least one block is required"},
        )
    blocks: list[dict[str, Any]] = []
    for index, block in enumerate(raw_blocks):
        is_entry = index == 0 and isinstance(block, Mapping) and block.get("kind") == "rule"
        blocks.append(validate_block(block, index, is_entry=is_entry))
    entry_rule = (
        blocks[0]
        if blocks and blocks[0]["kind"] == "rule" and blocks[0]["role"] == "entry"
        else None
    )
    return {
        "name": name,
        "blocks": blocks,
        "entry_rule": entry_rule,
        "start_cursor": 1 if entry_rule else 0,
        "audience_mechanism": vocab.AUDIENCE_MECHANISM,
    }


def blocks_for(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The validated blocks from a stored series or a fresh payload."""

    blocks = payload.get("blocks")
    if not isinstance(blocks, Sequence):
        return []
    return [dict(block) for block in blocks if isinstance(block, Mapping)]


def entry_matches(series: Mapping[str, Any], contact: Mapping[str, Any]) -> tuple[bool, int, int]:
    """Whether a contact passes the entry rule, returning ``(passed, matched, total)``."""

    entry = series.get("entry_rule") or {}
    if not entry:
        return (True, 0, 0)
    return filter_match(contact, entry.get("filters") or [], entry.get("match") or "all")


def condition_event(condition: Any) -> str | None:
    """The engagement event a rule condition reads, or ``None`` for no condition."""

    if condition in (None, ""):
        return None
    return vocab.CONDITION_EVENTS.get(str(condition))


def rule_satisfied(
    contact: Mapping[str, Any],
    events: Iterable[str],
    block: Mapping[str, Any],
) -> tuple[bool, str]:
    """Whether a branch rule is satisfied, by its condition and its filters.

    Both must hold where both are present. The condition is the seller's word (``clicked_link``)
    and the event is the machine value (``click``); the filters are the same evaluator the entry
    rule uses, so a branch can gate on a contact attribute as well as on an engagement event.
    """

    filters = block.get("filters") or []
    passed, matched, total = filter_match(contact, filters, block.get("match") or "all")
    if not passed:
        return (False, f"filters not matched ({matched}/{total})")
    wanted = condition_event(block.get("condition"))
    if wanted is None:
        return (True, "no condition")
    if wanted in set(events):
        return (True, f"{wanted} recorded")
    return (False, f"{wanted} not recorded")


# --------------------------------------------------------------------------- #
# The channel decision: deliver now, retry, or fall back
# --------------------------------------------------------------------------- #


def decide_channel(block: Mapping[str, Any], contact: Mapping[str, Any]) -> dict[str, Any]:
    """Whether a content block can deliver on its primary channel right now.

    A recorded stand-in, not a send: no provider is called anywhere in this build, and every
    dispatch row this decides for carries ``sent_by_this_product: false``.
    """

    channel = str(block.get("channel") or vocab.CHANNELS[0])
    channels = set(contact.get("channels") or [])
    online = bool(contact.get("online"))
    if online and channel in channels:
        return {"can_deliver": True, "channel": channel, "reason": None}
    if not online:
        return {"can_deliver": False, "channel": channel, "reason": "buyer_offline"}
    return {"can_deliver": False, "channel": channel, "reason": "channel_unavailable"}


def fallback_reason(online: bool, elapsed_seconds: float, window_seconds: float) -> str:
    """Why the offline branch fired.

    The offline timeout is the researched one: once the window has elapsed while the buyer is
    still offline, "they will leave this path". When the buyer is online but the primary channel
    cannot reach them, the reason is the channel, not the timeout.
    """

    if not online and elapsed_seconds >= window_seconds:
        return vocab.OFFLINE_FALLBACK_REASON
    if not online:
        return "buyer_offline_retrying"
    return "channel_unavailable"


def describe_snapshot(series: Mapping[str, Any]) -> dict[str, Any]:
    """The series' definition as an export snapshot: name, state, and its blocks."""

    return {
        "name": series.get("name"),
        "state": series.get("state"),
        "blocks": [dict(block) for block in blocks_for(series)],
        "created_at": series.get("created_at"),
        "went_live_at": series.get("went_live_at"),
    }


def embedded_rules(series: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every rule block in a series, flattened for the export's ``embedded_rules`` field."""

    return [
        {
            "id": block.get("id"),
            "role": block.get("role"),
            "match": block.get("match"),
            "filters": [dict(rule) for rule in block.get("filters") or []],
            "condition": block.get("condition"),
            "wait_mode": block.get("wait_mode"),
            "match_within_seconds": block.get("match_within_seconds"),
        }
        for block in blocks_for(series)
        if block.get("kind") == "rule"
    ]


def attribute_paths_used(series: Mapping[str, Any]) -> list[str]:
    """Every filter field a series reads, de-duplicated and sorted.

    This is the export's ``attributes`` field: the contact and company attributes the series
    depends on, which is exactly what a data-subject export has to name.
    """

    used: set[str] = set()
    for block in blocks_for(series):
        for rule in block.get("filters") or []:
            if rule.get("field"):
                used.add(str(rule["field"]))
    return sorted(used)
