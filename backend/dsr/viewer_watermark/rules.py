"""The pure rules of WF-071: validate the config, interpolate the tokens, render
the overlay.

Nothing here touches the store. Every function takes plain values - the config a
rep saved, the ``data`` payload of a view - and returns plain data, so each rule
can be tested against a literal example rather than through a database, and
:mod:`.book` stays a thin read/write shell.

The two rules that are **not** this workflow's own, and are therefore imported
rather than re-declared:

* viewer identity is WF-121's :func:`~dsr.tracked_deal_link.vocabulary.require_viewer`
  (itself WF-069's ``normalize_email``), so ``Buyer@`` and ``buyer@`` are one
  viewer in both workflows;
* the plan tier a watermark needs is WF-077's ``PLAN_RANK``, and the rule quoted
  is WF-077's own ``PLAN_GATE_QUOTE``.

The rules that **are** this build's, because the research names the capability
without a value, are recorded in :mod:`.inferences`: the tiled default, the tier
a watermark requires, and the text length cap.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from dsr.link_gating import rules as gate_rules
from dsr.tracked_deal_link.vocabulary import require_viewer
from dsr.workspace_roles.vocabulary import PLAN_RANK

from . import vocabulary as vocab
from .errors import WatermarkPlanError, WatermarkValidationError

#: Input spellings accepted on the way in, mapped to the stored key. The stored
#: keys are the OpenAPI schema's, so a client that reads the vocabulary back can
#: send the same names.
_ALIASES: dict[str, str] = {
    "tiled": "is_tiled",
    "isTiled": "is_tiled",
    "fontSize": "font_size",
    "fontsize": "font_size",
    "fontSizePt": "font_size",
    "opacity_percent": "opacity",
    "rotation_deg": "rotation",
    "colour": "color",
    "token_text": "text",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aliased(payload: Mapping[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, value in dict(payload or {}).items():
        text = str(key)
        fields[_ALIASES.get(text, _ALIASES.get(text.lower(), text))] = value
    return fields


def _as_bool(value: Any, field: str, *, default: bool) -> bool:
    """Coerce a JSON boolean, refusing ``bool("false") is True``."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "on", "1"):
            return True
        if lowered in ("false", "no", "off", "0"):
            return False
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise WatermarkValidationError(
        f"{field} must be a boolean, got {value!r}", field=field, allowed=[True, False]
    )


def _as_int(value: Any, field: str, *, default: int, minimum: int, maximum: int) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise WatermarkValidationError(
            f"{field} must be a whole number, got {value!r}", field=field
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise WatermarkValidationError(
            f"{field} must be a whole number, got {value!r}", field=field
        ) from exc
    if parsed < minimum or parsed > maximum:
        raise WatermarkValidationError(
            f"{field} must be between {minimum} and {maximum}, got {parsed}",
            field=field,
            minimum=minimum,
            maximum=maximum,
        )
    return parsed


def _as_float(value: Any, field: str, *, default: float, minimum: float, maximum: float) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise WatermarkValidationError(f"{field} must be a number, got {value!r}", field=field)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise WatermarkValidationError(
            f"{field} must be a number, got {value!r}", field=field
        ) from exc
    if parsed < minimum or parsed > maximum:
        raise WatermarkValidationError(
            f"{field} must be between {minimum} and {maximum}, got {parsed}",
            field=field,
            minimum=minimum,
            maximum=maximum,
        )
    return parsed


def _as_choice(value: Any, field: str, choices: Sequence[Any], *, default: Any) -> Any:
    if value is None or value == "":
        return default
    if value not in choices:
        raise WatermarkValidationError(
            f"{field} must be one of {list(choices)}, got {value!r}",
            field=field,
            allowed=list(choices),
        )
    return value


def _text(value: Any, field: str, *, default: str, maximum: int) -> str:
    text = default if value is None else str(value).strip()
    if not text:
        raise WatermarkValidationError(f"{field} is required", field=field)
    if len(text) > maximum:
        raise WatermarkValidationError(
            f"{field} must be at most {maximum} characters, got {len(text)}",
            field=field,
            maximum=maximum,
        )
    return text


def tokens_used(text: str) -> list[str]:
    """Every ``{{token}}`` in a text, in first-seen order, de-duplicated."""
    seen: dict[str, None] = {}
    for match in vocab.TOKEN_RE.finditer(str(text or "")):
        seen.setdefault(match.group(1), None)
    return list(seen)


def _refuse_unknown_tokens(text: str) -> None:
    """Refuse a ``{{...}}`` that is not one of the five researched tokens.

    A typo'd token would otherwise render literally into every page of a buyer's
    view, which is the one place a seller cannot retract it. Refusing at authoring
    time is the only moment the mistake is still cheap.
    """
    unknown = [token for token in tokens_used(text) if token not in vocab.TOKENS]
    if unknown:
        raise WatermarkValidationError(
            f"unknown watermark token(s) {unknown}; the researched tokens are {list(vocab.TOKENS)}",
            field="text",
            unknown_tokens=unknown,
            allowed=list(vocab.TOKENS),
        )


def normalise_config(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Validate and normalise a watermark config, filling the documented defaults.

    The OpenAPI schema marks all seven fields required, and the research also
    names a default for six of them ("``Confidential`` at ``middle-center``, 45°,
    black, 24pt, 0.5 opacity"). Both hold at once here: a missing field is filled
    from its documented default, so the stored config always carries all seven
    values, and a field that *is* present is validated against the schema.
    """
    fields = _aliased(payload or {})
    defaults = vocab.default_config()

    text = _text(
        fields.get("text"),
        "text",
        default=defaults["text"],
        maximum=vocab.TEXT_MAX_CHARS,
    )
    _refuse_unknown_tokens(text)

    color = str(fields.get("color") or defaults["color"]).strip()
    if not vocab.COLOR_RE.match(color):
        raise WatermarkValidationError(
            f"color must match {vocab.COLOR_RE.pattern}, got {color!r}",
            field="color",
            pattern=vocab.COLOR_RE.pattern,
        )

    return {
        "text": text,
        "is_tiled": _as_bool(fields.get("is_tiled"), "is_tiled", default=defaults["is_tiled"]),
        "position": _as_choice(
            fields.get("position"), "position", vocab.POSITIONS, default=defaults["position"]
        ),
        "rotation": _as_choice(
            fields.get("rotation"), "rotation", vocab.ROTATIONS, default=defaults["rotation"]
        ),
        "color": color,
        "font_size": _as_int(
            fields.get("font_size"),
            "font_size",
            default=defaults["font_size"],
            minimum=vocab.FONT_SIZE_MIN,
            maximum=vocab.FONT_SIZE_MAX,
        ),
        "opacity": _as_float(
            fields.get("opacity"),
            "opacity",
            default=defaults["opacity"],
            minimum=vocab.OPACITY_MIN,
            maximum=vocab.OPACITY_MAX,
        ),
    }


# --------------------------------------------------------------------------- #
# The per-view render
# --------------------------------------------------------------------------- #


def _value_for(token: str, view: Mapping[str, Any]) -> Any:
    if token == "email":
        return view.get("viewer")
    if token == "ipAddress":
        return view.get("ip_address")
    if token == "link":
        return view.get("share_url")
    moment = view.get("occurred_at")
    if isinstance(moment, datetime):
        if token == "date":
            return moment.date().isoformat()
        if token == "time":
            return moment.timetz().replace(tzinfo=None).isoformat(timespec="seconds")
    return None


def interpolate(text: str, view: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Substitute the identity tokens for one view.

    Returns ``(rendered, unresolved)``. An identity token with no value bound to
    the view is replaced by the placeholder in :data:`vocabulary.PLACEHOLDERS`
    and its name is returned in ``unresolved`` - never left as a bare ``{{...}}``
    and never silently blanked, because either would erase the trace the watermark
    exists to carry while still looking like a watermark.
    """
    unresolved: list[str] = []

    def substitute(match: Any) -> str:
        token = match.group(1)
        value = _value_for(token, view)
        if value in (None, ""):
            unresolved.append(token)
            return vocab.PLACEHOLDERS.get(token, "unknown")
        return str(value)

    return (
        vocab.TOKEN_RE.sub(substitute, str(text or "")),
        list(dict.fromkeys(unresolved)),
    )


def normalise_view(
    payload: Mapping[str, Any] | None = None, *, now: datetime | None = None
) -> dict[str, Any]:
    """Validate the values a single view binds to the tokens.

    The viewer email is normalised through WF-121's own rule, so the address in
    the watermark and the address in WF-121's analytics are one spelling. The IP
    is stored as text and not parsed: a deployment may see IPv4, IPv6 or a
    forwarded header, and a strict parse would refuse a legitimate view.
    """
    fields = _aliased(payload or {})
    viewer = None
    raw_viewer = fields.get("viewer") or fields.get("email")
    if raw_viewer not in (None, ""):
        try:
            viewer = require_viewer(raw_viewer)
        except Exception as exc:  # WF-069's GateError, re-voiced as this workflow's
            raise WatermarkValidationError(
                "viewer must be an email address, so the watermark names a person",
                field="viewer",
            ) from exc

    ip_address = str(fields.get("ip_address") or fields.get("ip") or "").strip() or None
    share_url = str(fields.get("share_url") or "").strip() or None
    page_count = _as_int(
        fields.get("page_count"),
        "page_count",
        default=0,
        minimum=0,
        maximum=10_000,
    )
    occurred_at = gate_rules.parse_moment(fields.get("occurred_at")) or now or now_utc()
    return {
        "viewer": viewer,
        "ip_address": ip_address,
        "share_url": share_url,
        "page_count": page_count,
        "occurred_at": occurred_at,
    }


def render_pages(
    config: Mapping[str, Any], view: Mapping[str, Any], *, page_count: int = 0
) -> dict[str, Any]:
    """The overlay composited onto every page of one delivered view.

    The rendered text is identical on every page - a per-view stamp, not a
    per-page one - which is the researched model: the viewer interpolates the
    tokens per view and renders the watermark on every page at view time. The
    page list is returned rather than a count so a viewer has nothing left to
    compute and a test can assert the composite reached the last page.
    """
    rendered, unresolved = interpolate(str(config.get("text") or ""), view)
    overlay = {
        "text": rendered,
        "is_tiled": bool(config.get("is_tiled")),
        "position": config.get("position"),
        "rotation": config.get("rotation"),
        "color": config.get("color"),
        "font_size": config.get("font_size"),
        "opacity": config.get("opacity"),
    }
    pages = [
        {"page": number, "overlay": dict(overlay)}
        for number in range(1, max(1, int(page_count or 0)) + 1)
    ]
    return {
        "rendered_text": rendered,
        "overlay": overlay,
        "pages": pages,
        "page_count": len(pages),
        "unresolved_tokens": unresolved,
    }


# --------------------------------------------------------------------------- #
# Plan entitlement
# --------------------------------------------------------------------------- #


def plan_allows(plan: str | None) -> bool:
    """Whether ``plan`` entitles a watermark.

    An absent plan is not refused. The product has no team-plan context on this
    surface - the researched gate belongs to the workspace/plan layer - so the
    route accepts the caller's plan and refuses a *named* plan below the required
    tier. This asymmetry is recorded in :mod:`.inferences`.
    """
    if plan is None or str(plan).strip() == "":
        return True
    return PLAN_RANK.get(str(plan).strip().lower(), -1) >= PLAN_RANK[vocab.PLAN_REQUIRED]


def require_plan(plan: str | None) -> None:
    """Refuse a named plan that does not cover a watermark. 403."""
    if plan_allows(plan):
        return
    raise WatermarkPlanError(
        f"plan {plan!r} does not include a watermark; it requires the "
        f"{vocab.PLAN_REQUIRED} plan or above",
        plan=plan,
        feature=vocab.PLAN_FEATURE,
        requires=vocab.PLAN_REQUIRED,
        rule=vocab.PLAN_GATE_QUOTE,
    )


#: The config a fresh install serves. Exported so the page and the rules agree.
DEFAULT_CONFIG: dict[str, Any] = vocab.default_config()


__all__ = [
    "DEFAULT_CONFIG",
    "interpolate",
    "normalise_config",
    "normalise_view",
    "now_utc",
    "plan_allows",
    "render_pages",
    "require_plan",
    "tokens_used",
]
