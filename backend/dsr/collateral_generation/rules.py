"""The pure assembly engine for WF-130.

This module takes plain dicts and returns plain dicts. It holds no store, opens
no connection, and reads no clock of its own: the deal record and the template
set are the only inputs, so a test asserts the whole generation without a
network call and without a fixture. That is the property the rules-first
decision depends on.

The researched behaviour and where it lives:

=================================  ==========================================
Researched behaviour               Where it lives
=================================  ==========================================
template, not a blank page          :func:`normalise_template`, :data:`vocabulary.DEFAULT_TEMPLATES`
personalised from the deal          :func:`merge_tokens`, :func:`normalise_deal`
content blocks                      :func:`assemble`, :func:`render_section`
synced sections                     :func:`render_section` (``asset_ref``)
hidden sections, revealed by stage  :func:`is_revealed`, :func:`apply_stage`
=================================  ==========================================

Every value the evidence does not give is a decision in
:data:`vocabulary.DECISIONS`, not a silent guess.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from .errors import CollateralValidationError
from .vocabulary import (
    ALWAYS_VISIBLE,
    BRAND_TOKENS,
    DEAL_STAGES,
    DEFAULT_STAGE,
    DEFAULT_TEMPLATE_KEY,
    KINDS,
    SECTION_KINDS,
    TOKENS,
    template as shipped_template,
)

#: A ``{{token}}`` reference. The name is the token set's vocabulary; anything
#: else is refused rather than left in the output.
_TOKEN_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

#: A section key: letters, digits, underscores. Stable, because an edit and a
#: refresh both address a section by its key.
_KEY_RE = re.compile(r"^[A-Za-z0-9_]+$")


def now_utc() -> datetime:
    """The one clock. A caller may inject another, and a test always does."""
    return datetime.now(timezone.utc)


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def token_values(deal: Mapping[str, Any]) -> dict[str, str]:
    """The deal values a ``{{token}}`` resolves to.

    ``contact_first`` is derived from the contact, so a template can greet a
    person by name without the caller computing it.
    """
    contact = _as_text(deal.get("contact")).strip()
    return {
        "account": _as_text(deal.get("account")).strip(),
        "contact": contact,
        "contact_first": contact.split()[0] if contact else "",
        "product": _as_text(deal.get("product")).strip(),
        "stage": _as_text(deal.get("stage")).strip(),
        "seller": _as_text(deal.get("seller")).strip(),
        "deal_id": _as_text(deal.get("deal_id")).strip(),
    }


def merge_tokens(
    text: str, values: Mapping[str, str]
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Substitute ``{{token}}`` references in ``text``.

    Returns ``(rendered, used, unresolved)``. An unresolved token - one the deal
    has no value for - renders as an empty string but is *named* in the result,
    so a page can warn rather than quietly ship a blank greeting. A token that
    is not in the vocabulary is refused outright: a template with a typo must
    fail at generation time, not produce a literal ``{{accout}}`` for a buyer.
    """
    used: list[str] = []
    unresolved: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in TOKENS:
            raise CollateralValidationError(
                f"unknown personalisation token {name!r}",
                field="body",
                token=name,
                known=list(TOKENS),
            )
        value = _as_text(values.get(name)).strip()
        if name not in used:
            used.append(name)
            if not value:
                unresolved.append(name)
        return value

    rendered = _TOKEN_RE.sub(replace, _as_text(text))
    return rendered, tuple(used), tuple(unresolved)


def normalise_deal(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate the deal record the collateral is personalised from.

    The deal arrives as ordinary JSON: the CRM read is out of scope, so nothing
    here reaches a remote system. Only the account is required, because every
    other value may legitimately be unknown at generation time.
    """
    source = dict(payload or {})
    account = _as_text(source.get("account")).strip()
    if not account:
        raise CollateralValidationError("a deal needs an account", field="account")

    raw_stage = _as_text(source.get("stage")).strip().lower().replace(" ", "_")
    stage = raw_stage or DEFAULT_STAGE
    if stage not in DEAL_STAGES:
        raise CollateralValidationError(
            f"unknown deal stage {stage!r}",
            field="stage",
            known=list(DEAL_STAGES),
        )

    contact = _as_text(source.get("contact")).strip()
    deal = {
        "account": account,
        "contact": contact,
        "contact_first": contact.split()[0] if contact else "",
        "product": _as_text(source.get("product")).strip(),
        "stage": stage,
        "seller": _as_text(source.get("seller")).strip(),
        "deal_id": _as_text(source.get("deal_id")).strip(),
    }
    return deal


def stage_rank(stage: str) -> int:
    """A stage's position on the ladder. Unknown stages rank below all others."""
    try:
        return DEAL_STAGES.index(str(stage))
    except ValueError:
        return -1


def is_revealed(stage: str, reveal_at_stage: str | None) -> bool:
    """Is a section visible at this stage?

    ``None`` means the section is always visible. Otherwise the deal must be at
    the section's stage or past it. This is the whole of the readiness rule:
    a comparison, not a guess. See ``DECISIONS['readiness']``.
    """
    if reveal_at_stage is None or reveal_at_stage == ALWAYS_VISIBLE:
        return True
    return stage_rank(stage) >= stage_rank(str(reveal_at_stage))


def normalise_section_spec(raw: Mapping[str, Any], index: int) -> dict[str, Any]:
    """Validate one section of a template."""
    if not isinstance(raw, Mapping):
        raise CollateralValidationError(
            f"section {index} must be a JSON object", field=f"sections[{index}]"
        )
    key = _as_text(raw.get("key")).strip()
    if not key or not _KEY_RE.match(key):
        raise CollateralValidationError(
            f"section {index} needs a key of letters, digits or underscores",
            field=f"sections[{index}].key",
        )
    title = _as_text(raw.get("title")).strip()
    if not title:
        raise CollateralValidationError(
            f"section {key!r} needs a title", field=f"sections[{index}].title"
        )

    kind = _as_text(raw.get("kind")).strip() or "narrative"
    if kind not in SECTION_KINDS:
        raise CollateralValidationError(
            f"unknown section kind {kind!r}",
            field=f"sections[{index}].kind",
            known=list(SECTION_KINDS),
        )

    reveal = raw.get("reveal_at_stage")
    if reveal is not None:
        reveal = _as_text(reveal).strip().lower().replace(" ", "_")
        if reveal not in DEAL_STAGES:
            raise CollateralValidationError(
                f"section {key!r} reveals at an unknown stage {reveal!r}",
                field=f"sections[{index}].reveal_at_stage",
                known=list(DEAL_STAGES),
            )

    asset_ref = _as_text(raw.get("asset_ref")).strip() or None
    synced = bool(raw.get("synced", asset_ref is not None))
    return {
        "key": key,
        "title": title,
        "kind": kind,
        "body": _as_text(raw.get("body")),
        "reveal_at_stage": reveal,
        "asset_ref": asset_ref,
        "synced": synced,
    }


def normalise_template(payload: Mapping[str, Any] | str | None) -> dict[str, Any]:
    """Validate a template, or load a shipped one by key.

    A create request names a shipped template by ``key``, or supplies its own
    section list. Both routes end here, so a team's template is exactly as
    well-formed as a shipped one.
    """
    if payload is None:
        payload = DEFAULT_TEMPLATE_KEY
    if isinstance(payload, str):
        payload = {"key": payload}
    if not isinstance(payload, Mapping):
        raise CollateralValidationError("a template must be a JSON object", field="template")

    key = _as_text(payload.get("key")).strip()
    name = _as_text(payload.get("name")).strip()
    kind = _as_text(payload.get("kind")).strip()
    raw_sections = payload.get("sections")

    shipped = shipped_template(key) if key and raw_sections is None else None
    if shipped is not None:
        # Run the shipped sections through the same validation a caller's list
        # gets, so a synced section is marked synced and a missing section kind
        # is caught here rather than at render time.
        shipped["sections"] = [
            normalise_section_spec(raw, index) for index, raw in enumerate(shipped["sections"])
        ]
        return shipped
    if key and raw_sections is None:
        raise CollateralValidationError(
            f"unknown template {key!r}", field="template", known=[DEFAULT_TEMPLATE_KEY]
        )
    if not key:
        key = "custom"

    kind = kind or "document"
    if kind not in KINDS:
        raise CollateralValidationError(
            f"unknown artefact kind {kind!r}", field="kind", known=list(KINDS)
        )
    if not isinstance(raw_sections, Iterable) or isinstance(raw_sections, (str, bytes)):
        raise CollateralValidationError("a template needs a list of sections", field="sections")

    sections = [normalise_section_spec(raw, index) for index, raw in enumerate(raw_sections)]
    if not sections:
        raise CollateralValidationError("a template needs at least one section", field="sections")
    keys = [section["key"] for section in sections]
    if len(set(keys)) != len(keys):
        raise CollateralValidationError("two sections share a key", field="sections", keys=keys)

    return {"key": key, "name": name or key, "kind": kind, "sections": sections}


def render_section(
    spec: Mapping[str, Any],
    deal: Mapping[str, Any],
    asset: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Render one section's body from the spec, the deal and its source asset.

    A synced section appends the asset's current body, so the asset's own text
    is personalised too and the section carries the asset's version with it.
    """
    values = token_values(deal)
    body, used, unresolved = merge_tokens(_as_text(spec.get("body")), values)

    asset_key = spec.get("asset_ref")
    asset_version: Any = None
    if asset_key:
        if asset is None:
            raise CollateralValidationError(
                f"section {spec.get('key')!r} names source asset {asset_key!r}, which is not registered",
                field="asset_ref",
                asset_ref=asset_key,
            )
        asset_version = asset.get("version")
        asset_body, asset_used, asset_unresolved = merge_tokens(_as_text(asset.get("body")), values)
        if asset_body:
            body = f"{body}\n\n{asset_body}" if body else asset_body
        used = (*used, *(name for name in asset_used if name not in used))
        unresolved = (*unresolved, *(name for name in asset_unresolved if name not in unresolved))

    return {
        "key": spec["key"],
        "title": spec["title"],
        "kind": spec["kind"],
        "body": body,
        # The un-rendered template text. A refresh re-renders from this rather
        # than from ``body``, which already carries the previous asset text, so
        # the asset is not appended twice.
        "template_body": _as_text(spec.get("body")),
        "reveal_at_stage": spec.get("reveal_at_stage"),
        "asset_ref": asset_key,
        "asset_version": asset_version,
        "synced": bool(spec.get("synced")),
        "tokens_used": list(used),
        "unresolved_tokens": list(unresolved),
    }


def assemble(
    template: Mapping[str, Any],
    deal: Mapping[str, Any],
    assets: Mapping[str, Mapping[str, Any]],
    *,
    brand: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble a whole artefact from a template and a deal.

    The output is the collateral record's ``data`` payload: content blocks plus
    the deal they were personalised from, ready to be reviewed, edited and
    published. No binary is produced and no network call is made.
    """
    sections = [
        render_section(spec, deal, assets.get(str(spec.get("asset_ref"))))
        for spec in template["sections"]
    ]
    for section in sections:
        section["visible"] = is_revealed(deal["stage"], section.get("reveal_at_stage"))

    applied = dict(brand or {})
    applied.setdefault("applied", True)
    applied.setdefault("tokens", list(BRAND_TOKENS))
    applied.setdefault("source", "frontend/src/index.css (read at render time)")

    account = deal["account"]
    title = f"{account} {template['name'].lower()}"
    data: dict[str, Any] = {
        "title": title,
        "kind": template["kind"],
        "template_key": template["key"],
        "template_name": template["name"],
        "deal": dict(deal),
        "brand": applied,
        "engine": "rules",
        "state": "draft",
        "version": 1,
        "sections": sections,
    }
    data.update(summarise(sections))
    return data


def apply_stage(sections: Iterable[Mapping[str, Any]], stage: str) -> list[dict[str, Any]]:
    """Recompute every section's visibility for a stage, without mutating input.

    This is what makes a hidden section reveal itself "as the deal progresses":
    the stored sections keep their ``reveal_at_stage`` and the visibility is
    derived at read time from the deal's current stage.
    """
    result: list[dict[str, Any]] = []
    for section in sections:
        copy = dict(section)
        copy["visible"] = is_revealed(stage, copy.get("reveal_at_stage"))
        result.append(copy)
    return result


def summarise(sections: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """The counts a summary reads: total, visible, hidden and synced sections."""
    rows = list(sections)
    visible = sum(1 for section in rows if section.get("visible"))
    synced = sum(1 for section in rows if section.get("synced"))
    return {
        "section_count": len(rows),
        "visible_count": visible,
        "hidden_count": len(rows) - visible,
        "synced_count": synced,
    }


def visible_section_keys(sections: Iterable[Mapping[str, Any]]) -> list[str]:
    """The keys of the sections a buyer would see at the artefact's stage."""
    return [str(section["key"]) for section in sections if section.get("visible")]


__all__ = [
    "apply_stage",
    "assemble",
    "is_revealed",
    "merge_tokens",
    "normalise_deal",
    "normalise_section_spec",
    "normalise_template",
    "now_utc",
    "render_section",
    "stage_rank",
    "summarise",
    "token_values",
    "visible_section_keys",
]
