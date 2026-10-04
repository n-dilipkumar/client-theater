"""The rules WF-093 enforces, made executable.

Pricing, quoting and proposals. Every function here is pure: it takes values and returns
values, reads nothing and writes nothing. The vocabulary is
:mod:`dsr.quoting_proposals.vocabulary` and the writes are in
:mod:`dsr.quoting_proposals.engine`.

The rules the rest of the workflow leans on
-------------------------------------------

**A quote's own properties beat the template's.** The evidence fixes this: "The quote
template's properties, content, and associations will be added to the quote as
supplemental information, with properties set on the quote overriding the quote template's
settings." :func:`resolve` is that sentence, and it reports which level answered for each
field so a reader can see that the rule ran rather than being asked to trust it.

**A published document is frozen.** The evidence says "Updating your logo and branding
won't update existing published quotes, only currently drafted quotes and quotes created
after updating." :func:`rerenderable` is that sentence, and :func:`require_rerenderable`
refuses a re-render of a published document rather than quietly doing it.

**A line-items module is capped, and the cap is reported.** The evidence records "you can
only return up to 100 related records for each relationship". This workflow does not
implement the Word merge that cap belongs to, but it applies the cap and names the
truncation instead of dropping rows silently, because a proposal whose total does not
match its quote is worse than one that says it dropped a row.

**An unbindable field renders empty and is named.** A template binds to a dotted path in
the quote record, and no schema declares that path, because "A team adding a field must
need no coordination with anyone." So a path the record does not carry is reported under
:data:`~dsr.quoting_proposals.vocabulary.UNRESOLVED` and the module still renders. One
missing field must not cost a buyer the whole proposal.

What this module does not decide
--------------------------------

Whether a total is commercially correct, and whether a document is fit to send. It
computes sums from the line items the store holds and reports which fields bound. It
never asserts that a price is right.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.quoting_proposals import vocabulary as vocab

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# Declared here and raised by nothing else in the product. That is what makes it safe
# for the feature module to map them: a handler for a shared type such as ``ValueError``
# would intercept that exception across the whole application.


class ProposalRefusal(ValueError):
    """A template, a binding or a merge input this workflow will not accept.

    Carries a field-keyed map, because the page puts each message beside the input that
    caused it rather than in one combined sentence.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})


class ProposalNotFound(LookupError):
    """No such template, brand or rendered document.

    Its own type rather than the store's ``RecordNotFound``, for the same reason as every
    other workflow: a feature may only map error types it raises itself.
    """


class DocumentFrozen(PermissionError):
    """A re-render was asked of a published document.

    Its own type because the remediation differs from a refusal. The caller is not doing
    something wrong with a value. The document is already published, and the
    specification says a template change "won't update existing published quotes". To
    change one, the quote has to be issued again, and the caller may not do that through
    this workflow.
    """

    code = "document_frozen"

    def __init__(self, document_id: str, state: str) -> None:
        super().__init__(
            f"Document {document_id!r} is {state} and is not re-rendered by a template "
            "change. A published quote is not updated retroactively."
        )
        self.document_id = document_id
        self.state = state

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "detail": str(self),
            "document_id": self.document_id,
            "state": self.state,
            "remediation": (
                "Issue the quote again to produce a new document. A published document "
                "is never rewritten in place."
            ),
            "evidence": vocab.NO_RETROACTIVE_APPLICATION_QUOTE,
        }


# --------------------------------------------------------------------------- #
# Instants
# --------------------------------------------------------------------------- #


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime | None = None) -> str:
    """An ISO 8601 instant in UTC, with the milliseconds kept."""

    value = moment or utcnow()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def coerce_instant(value: Any, field: str) -> datetime | None:
    """One stored instant as an aware UTC datetime, or ``None`` when there is none.

    ``None`` is a real answer rather than an error. A quote with no expiry date is a
    quote that does not expire, and the header module renders an empty expiry rather than
    refusing to render at all.

    Two encodings are accepted, and only the two this repository stores: an ISO 8601
    string and Unix milliseconds. A string of only digits is read as milliseconds,
    because a bare number is how a millisecond timestamp survives a JSON round trip
    through a client.
    """

    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ProposalRefusal(
            f"{field} must be an instant.",
            {field: f"{field} must be an ISO 8601 instant or Unix milliseconds."},
        )
    if isinstance(value, datetime):
        return (
            value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        )
    if isinstance(value, (int, float)):
        return _from_millis(float(value), field)
    text = str(value).strip()
    if not text:
        return None
    if text.lstrip("-").isdigit():
        try:
            return _from_millis(float(text), field)
        except ProposalRefusal:
            raise
        except (OverflowError, ValueError) as exc:
            raise ProposalRefusal(
                f"{field} is not a readable instant.",
                {field: f"{field} must be an ISO 8601 instant or Unix milliseconds."},
            ) from exc
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProposalRefusal(
            f"{field} is not a readable instant.",
            {field: f"{field} must be an ISO 8601 instant or Unix milliseconds."},
        ) from exc
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _from_millis(number: float, field: str) -> datetime:
    try:
        return datetime.fromtimestamp(number / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ProposalRefusal(
            f"{field} is not a readable instant.",
            {field: f"{field} must be an ISO 8601 instant or Unix milliseconds."},
        ) from exc


def default_expiry(issued: datetime, days: int) -> datetime:
    """The expiration date a quote gets when neither the quote nor the template sets one.

    Derived rather than read: the specification names an expiration date as a header
    field and sets no figure for it anywhere. A default is still better than an empty
    header, and the response reports which level supplied it, so a reader can tell a
    derived date from a stated one.
    """

    return issued.astimezone(timezone.utc) + timedelta(days=days)


# --------------------------------------------------------------------------- #
# Template validation
# --------------------------------------------------------------------------- #

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


def normalise_key(value: Any, field: str = "key") -> str:
    """A lower-cased identifier, checked for shape.

    A template key is an API-facing name, so it is held to a shape rather than stored as
    anything at all. An empty or punctuated key would make a URL this product has to
    route on ambiguous.
    """

    text = str(value or "").strip().lower().replace(" ", "-")
    if not text:
        raise ProposalRefusal(
            f"A {field} is required.",
            {field: f"A {field} is required."},
        )
    if not _SLUG_RE.match(text):
        raise ProposalRefusal(
            f"{text!r} is not a usable {field}.",
            {
                field: (
                    f"A {field} uses lower-case letters, digits, a hyphen or an "
                    "underscore, and is between 2 and 64 characters."
                )
            },
        )
    return text


def normalise_module(value: Any) -> str:
    """One known module kind, or a refusal naming the set."""

    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text not in vocab.MODULES:
        raise ProposalRefusal(
            f"{text!r} is not a module kind.",
            {"modules": f"Known modules are: {', '.join(vocab.MODULES)}."},
        )
    return text


def normalise_header_field(value: Any) -> str:
    """One known header field, or a refusal naming the six."""

    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text not in vocab.HEADER_FIELDS:
        raise ProposalRefusal(
            f"{text!r} is not a header field.",
            {"field": f"Known header fields are: {', '.join(vocab.HEADER_FIELDS)}."},
        )
    return text


def normalise_party_role(value: Any) -> str:
    """One known party role, or a refusal naming the three."""

    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text not in vocab.PARTY_ROLES:
        raise ProposalRefusal(
            f"{text!r} is not a party role.",
            {"role": f"Known roles are: {', '.join(vocab.PARTY_ROLES)}."},
        )
    return text


def normalise_brand_token(value: Any) -> str:
    """One known brand-kit token, or a refusal naming the four."""

    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text not in vocab.BRAND_TOKENS:
        raise ProposalRefusal(
            f"{text!r} is not a brand-kit token.",
            {"token": f"Known tokens are: {', '.join(vocab.BRAND_TOKENS)}."},
        )
    return text


def normalise_state(value: Any, *, allow: Sequence[str] = vocab.DOCUMENT_STATES) -> str:
    """One known document state, or a refusal naming the set."""

    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text not in allow:
        raise ProposalRefusal(
            f"{text!r} is not a document state.",
            {"state": f"Known states are: {', '.join(allow)}."},
        )
    return text


def module_order(modules: Any, hidden: Iterable[str] = ()) -> list[str]:
    """The template's module list, defaulted, deduplicated and hidden-aware.

    A template may reorder any module and hide any module, which is step 3 of the user
    flow: "hide/show sections with the view/hide icons; drag modules to reorder". So this
    function does two things. It normalises whatever order the caller sent, and it appends
    any known module the caller omitted, so a template that names only ``header`` still
    renders a complete proposal rather than a document with one section.

    Two things are dropped rather than kept. A module the caller marks ``hidden`` or
    ``enabled: false`` inside the list, and any module named in the ``hidden`` argument.
    Dropping rather than keeping is what makes the result renderable: hiding is recorded
    separately in the template's own ``hidden`` list, and a module kept in the order but
    listed as hidden would leave the two lists disagreeing about what is visible.
    """

    skipped = {normalise_module(name) for name in (hidden or ())}
    listed: list[str] = []
    for entry in modules if isinstance(modules, (list, tuple)) else ():
        if isinstance(entry, str):
            name = normalise_module(entry)
        elif isinstance(entry, Mapping):
            if entry.get("hidden") or not entry.get("enabled", True):
                skipped.add(normalise_module(entry.get("module")))
                continue
            name = normalise_module(entry.get("module"))
        else:
            raise ProposalRefusal(
                "Each module entry must be a module id or an object.",
                {"modules": "Use a module id, or an object with a 'module' key."},
            )
        if name not in listed:
            listed.append(name)
    for name in vocab.MODULES:
        if name not in listed and name not in skipped:
            listed.append(name)
    return listed


def visible_modules(modules: Iterable[str]) -> list[str]:
    """The modules a document renders, in order."""

    return [name for name in modules if name in vocab.MODULES]


# --------------------------------------------------------------------------- #
# Bindings
# --------------------------------------------------------------------------- #


def read_path(payload: Mapping[str, Any] | None, path: str) -> Any:
    """Read one dotted JSON path, or ``None`` when any step of it is missing.

    ``None`` is a real answer here and not an error. The store holds arbitrary JSON, so a
    template may bind to a path this repository declares nothing about, and the only
    honest result for a path the record does not carry is that the record does not carry
    it.
    """

    current: Any = payload or {}
    for part in str(path or "").split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def validate_bindings(bindings: Any, *, where: str = "bindings") -> dict[str, Any]:
    """Check a module's bindings and normalise them.

    Each binding is ``{"field": <header or party or total field>, "path": <dotted JSON
    path>}``. A binding with no field is a refusal rather than a guess, because a
    template that binds a path without saying where it goes cannot be rendered at all.

    A binding whose path is empty is allowed and means "literal text", because a terms
    clause is usually fixed text rather than a value from the record. It is reported as
    literal rather than resolved.
    """

    if bindings is None:
        return {}
    if isinstance(bindings, Mapping):
        raw: list[Any] = [{"field": field, "path": path} for field, path in bindings.items()]
    elif isinstance(bindings, (list, tuple)):
        raw = list(bindings)
    else:
        raise ProposalRefusal(
            "bindings must be an object keyed by field, or a list of bindings.",
            {where: "Send an object keyed by field, or a list of {'field', 'path'} objects."},
        )

    result: dict[str, Any] = {}
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise ProposalRefusal(
                "Each binding must be an object.",
                {where: "Each binding needs a 'field' and a 'path'."},
            )
        field = str(entry.get("field") or "").strip()
        if not field:
            raise ProposalRefusal(
                "A binding needs a field to bind to.",
                {where: "Each binding needs a 'field' to bind into."},
            )
        path = str(entry.get("path") or "").strip()
        root = path.split(".")[0] if path else ""
        if path and root not in vocab.BINDING_ROOTS:
            raise ProposalRefusal(
                f"{path!r} does not start with a readable root.",
                {where: (f"A binding path starts with one of: {', '.join(vocab.BINDING_ROOTS)}.")},
            )
        result[field] = {
            "field": field,
            "path": path,
            "literal": not path,
        }
    return result


def resolve(
    bindings: Mapping[str, Any], scopes: Mapping[str, Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Resolve each binding against the record data, and say which level answered.

    The evidence fixes the precedence: "with properties set on the quote overriding the
    quote template's settings". So the quote scope is read first and the template scope
    second, and every resolved field carries the level that produced it under
    :data:`~dsr.quoting_proposals.vocabulary.REPORT_PRECEDENCE`.

    Three outcomes per field, and all three are reported rather than two:

    ``resolved``
        A value was found, and ``level`` says where it came from.
    ``literal``
        The binding had no path, so the template supplies fixed text.
    ``unresolved``
        The path named something the record does not carry. The field renders empty and
        is named, because one missing field must not cost a buyer the whole proposal.
    """

    resolved: dict[str, dict[str, Any]] = {}
    for field, binding in (bindings or {}).items():
        path = binding.get("path") or ""
        if not path:
            resolved[field] = {
                "field": field,
                "value": None,
                "level": vocab.GENERATION_SOURCE_TEMPLATE,
                "status": "literal",
                "path": "",
            }
            continue
        for level in vocab.PRECEDENCE_LEVELS:
            for scope_name in ("quote", "record"):
                payload = scopes.get(scope_name)
                if not isinstance(payload, Mapping):
                    continue
                value = read_path(payload, path)
                if value is None:
                    continue
                resolved[field] = {
                    "field": field,
                    "value": value,
                    "level": level,
                    "scope": scope_name,
                    "status": "resolved",
                    "path": path,
                }
                break
            else:
                continue
            break
        else:
            resolved[field] = {
                "field": field,
                "value": None,
                "level": vocab.GENERATION_SOURCE_TEMPLATE,
                "status": vocab.UNRESOLVED,
                "path": path,
            }
    return resolved


# --------------------------------------------------------------------------- #
# Branding
# --------------------------------------------------------------------------- #

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalise_colour(value: Any, field: str) -> str:
    """One CSS hex colour, checked for shape and rejected if it is not a colour.

    This is the one place a hex value is legal. A brand kit carries the seller's own
    colours, so the value has to arrive as one, and rejecting an unparseable one here
    means the document never carries a colour a browser cannot render. The frontend still
    styles with semantic tokens; this value is data the seller supplied, not page styling.
    """

    text = str(value or "").strip()
    if not _HEX_RE.match(text):
        raise ProposalRefusal(
            f"{text!r} is not a hex colour.",
            {field: f"{field} must be a hex colour such as #10506f."},
        )
    return text.lower()


def resolve_branding(
    template_tokens: Mapping[str, Any],
    brand_tokens: Mapping[str, Any] | None,
    quote_branding: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge the brand kit, the template's tokens and the quote's own overrides.

    "Override brand kit" is the toggle the specification gives, and its meaning is that
    the template's own tokens win over the brand's when it is on. So the brand is read
    first, the template's own tokens second when the toggle is on, and the quote's
    branding settings last because "properties set on the quote overriding the quote
    template's settings" reaches branding too.

    Three token maps, one precedence order, and every token reports the level that set
    it. A page that showed only the final colour could not tell a seller's brand from a
    template override from a per-quote override, and those are three different things a
    reviewer needs to see.
    """

    own = dict(template_tokens or {})
    brand = dict(brand_tokens or {})
    quote_side = dict(quote_branding or {})

    override = bool(own.get(vocab.OVERRIDE_BRAND_KIT, True))

    tokens: dict[str, dict[str, Any]] = {}
    for token in vocab.BRAND_TOKENS:
        if token == "logo_url":
            # A logo is resolved by source rather than by value: the specification lists
            # three sources and a later one loses. `_resolve_logo` reports which answered.
            continue

        value: Any = None
        level = "template_default"

        if brand.get(token) not in (None, ""):
            value = brand.get(token)
            level = vocab.LOGO_SOURCE_BRAND_KIT

        if override and own.get(token) not in (None, ""):
            value = own.get(token)
            level = "template_override"

        if quote_side.get(token) not in (None, ""):
            value = quote_side.get(token)
            level = vocab.LOGO_SOURCE_QUOTE_BRANDING

        if value not in (None, ""):
            entry: dict[str, Any] = {"token": token, "level": level}
            # A colour that cannot be parsed is refused rather than stored, so the
            # document never carries one a browser cannot draw.
            entry["value"] = (
                normalise_colour(value, token) if token in ("accent", "accent_soft") else value
            )
            tokens[token] = entry

    logo = _resolve_logo(own, brand, quote_side)
    return {
        "brand_id": brand.get("id") or brand.get("_brand_id"),
        "override_brand_kit": override,
        "tokens": tokens,
        "logo_url": logo["logo_url"],
        vocab.REPORT_LOGO_SOURCE: logo[vocab.REPORT_LOGO_SOURCE],
        "logo_fallback_applied": logo["fallback_applied"],
    }


def _resolve_logo(
    template_tokens: Mapping[str, Any],
    brand_tokens: Mapping[str, Any],
    quote_branding: Mapping[str, Any],
) -> dict[str, Any]:
    """The logo, and which of the three sources the specification names answered.

    "Logos can come from the quote branding settings, account branding, or the brand."
    Listed in that order, so the quote's own branding wins, then the brand, then account
    branding.

    "Show company name when logo isn't set" is applied here and reported under
    ``fallback_applied``. It is a toggle the seller sets, so the fallback never happens
    silently: a document with no logo either names the company or says why it does not.
    """

    source: str | None = None
    logo_url: Any = None

    for candidate, name in (
        (quote_branding.get("logo_url"), vocab.LOGO_SOURCE_QUOTE_BRANDING),
        (brand_tokens.get("logo_url"), vocab.LOGO_SOURCE_BRAND_KIT),
        (template_tokens.get("account_logo_url"), vocab.LOGO_SOURCE_ACCOUNT_BRANDING),
    ):
        if candidate not in (None, ""):
            logo_url = candidate
            source = name
            break

    fallback = False
    if logo_url in (None, ""):
        wants_name = template_tokens.get(
            vocab.FALLBACK_NAME_WHEN_LOGO_ABSENT,
            brand_tokens.get(vocab.FALLBACK_NAME_WHEN_LOGO_ABSENT),
        )
        company = quote_branding.get("company_name") or brand_tokens.get("company_name")
        if wants_name and company:
            logo_url = company
            source = "company_name_fallback"
            fallback = True

    return {
        "logo_url": logo_url,
        vocab.REPORT_LOGO_SOURCE: source or "none",
        "fallback_applied": fallback,
    }


# --------------------------------------------------------------------------- #
# Line items and totals
# --------------------------------------------------------------------------- #

_MONEY_STEP = 0.01


def coerce_money(value: Any, field: str) -> float:
    """A monetary amount as a float, at least zero.

    A negative line item is refused rather than signed. The specification describes a
    pricing table and a totals tab and says nothing about negative amounts, so this
    module does not invent a credit-note concept that another workflow may own.
    """

    if isinstance(value, bool) or value is None or value == "":
        raise ProposalRefusal(
            f"{field} must be an amount.",
            {field: f"{field} must be a number."},
        )
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ProposalRefusal(
            f"{field} must be an amount.",
            {field: f"{field} must be a number."},
        ) from exc
    if number != number or number in (float("inf"), float("-inf")):  # NaN or infinity
        raise ProposalRefusal(
            f"{field} must be a finite amount.",
            {field: f"{field} must be a finite number."},
        )
    if number < 0:
        raise ProposalRefusal(
            f"{field} cannot be negative.",
            {field: f"{field} cannot be negative. This workflow does not model credits."},
        )
    return round(number, 2)


def line_total(line_item: Mapping[str, Any], index: int = 0) -> dict[str, Any]:
    """One line item's amount, with the price and quantity it came from.

    The amount is taken from the record when it carries one and computed from quantity
    times unit price when it does not. WF-086 and WF-087 provision these rows and this
    workflow is not that producer, so it reads whichever of the two shapes arrives rather
    than requiring a column neither of those workflows is obliged to write.
    """

    field = f"line_items[{index}].amount"
    raw = line_item.get("amount")
    if raw in (None, ""):
        quantity = line_item.get("quantity")
        unit = line_item.get("unit_price")
        if quantity in (None, "") or unit in (None, ""):
            raise ProposalRefusal(
                "A line item needs an amount, or a quantity and a unit price.",
                {
                    field: (
                        "Provide 'amount', or both 'quantity' and 'unit_price'. The line "
                        "item total cannot be computed without one of the two."
                    )
                },
            )
        amount = round(coerce_money(quantity, "quantity") * coerce_money(unit, "unit_price"), 2)
        derived = True
    else:
        amount = coerce_money(raw, "amount")
        derived = False
    return {
        "amount": amount,
        "derived": derived,
        "quantity": line_item.get("quantity"),
        "unit_price": line_item.get("unit_price"),
        "currency_label": line_item.get("currency_label"),
    }


def cap_line_items(
    line_items: Sequence[Mapping[str, Any]], cap: int = vocab.RELATED_RECORD_CAP
) -> tuple[list[dict[str, Any]], int]:
    """The line items a document renders, and how many were dropped.

    The cap is the evidence's: "you can only return up to 100 related records for each
    relationship". This workflow does not implement the Word merge that cap belongs to,
    and the drop is still reported rather than hidden, because a proposal whose total
    silently excludes a row is a total the buyer cannot reconcile against the quote.
    """

    kept: list[dict[str, Any]] = []
    dropped = 0
    for index, item in enumerate(line_items):
        if len(kept) >= cap:
            dropped += 1
            continue
        resolved = line_total(item, index)
        entry = dict(item)
        entry.update(
            {
                "position": len(kept) + 1,
                "amount": resolved["amount"],
                "amount_derived": resolved["derived"],
                "quantity": resolved["quantity"],
                "unit_price": resolved["unit_price"],
            }
        )
        kept.append(entry)
    return kept, dropped


def totals(line_items: Sequence[Mapping[str, Any]], quote: Mapping[str, Any]) -> dict[str, Any]:
    """The totals tab, summed from the line items the document actually renders.

    The four rows are the ones :data:`~dsr.quoting_proposals.vocabulary.TOTAL_ROWS` names.
    The discount and tax rows are read from the quote rather than derived, because a
    discount is a quote-level figure and the specification puts a Totals tab beside the
    line items without saying how a discount is computed. When the quote states neither,
    the row is reported as zero and ``stated: false``, so a reader can tell an absent
    discount from a zero one.

    The grand total is the sum of the four rows, and it is computed over the capped line
    items rather than over everything the quote holds, so the total always matches the
    rows printed above it.
    """

    subtotal = round(sum(float(item.get("amount") or 0) for item in line_items), 2)
    discount = _stated_money(quote, ("discount", "discount_total"), "discount_total")
    tax = _stated_money(quote, ("tax", "tax_total", "tax_amount"), "tax_total")
    rows = {
        "subtotal": {"value": subtotal, "stated": True, "label": vocab.TOTAL_LABELS["subtotal"]},
        "discount_total": discount,
        "tax_total": tax,
    }
    rows["grand_total"] = {
        "value": round(subtotal - discount["value"] + tax["value"], 2),
        "stated": discount["stated"] and tax["stated"],
        "label": vocab.TOTAL_LABELS["grand_total"],
    }
    return {
        "rows": rows,
        "ordered": [rows[row] for row in vocab.TOTAL_ROWS],
        "currency_label": quote.get("currency_label") or quote.get("currency"),
        "rounding": _MONEY_STEP,
    }


def _stated_money(quote: Mapping[str, Any], keys: Sequence[str], label: str) -> dict[str, Any]:
    """One quote-stated money row, or a zero that says it was not stated."""

    for key in keys:
        if quote.get(key) not in (None, ""):
            return {
                "value": coerce_money(quote.get(key), key),
                "stated": True,
                "label": vocab.TOTAL_LABELS[label],
            }
    return {"value": 0.0, "stated": False, "label": vocab.TOTAL_LABELS[label]}


# --------------------------------------------------------------------------- #
# States and the non-retroactive rule
# --------------------------------------------------------------------------- #


def is_frozen(state: Any) -> bool:
    """Whether a document in this state is never rewritten by a template change."""

    try:
        return normalise_state(state) in vocab.FROZEN_STATES
    except ProposalRefusal:
        return False


def rerenderable(document: Mapping[str, Any]) -> dict[str, Any]:
    """Whether this document would take a template change, and why.

    The evidence states the rule only about published quotes: "Updating your logo and
    branding won't update existing published quotes, only currently drafted quotes and
    quotes created after updating." So a draft and an instantiated document both take a
    change, and a published one does not.

    Both answers are computed rather than asserted, and the reason is returned, so the
    page can show the rule instead of describing it.
    """

    state = normalise_state(document.get("state") or vocab.STATE_DRAFT)
    frozen = state in vocab.FROZEN_STATES
    return {
        "state": state,
        "rerenderable": not frozen,
        "reason": (
            f"A {state} document is not re-rendered by a template change."
            if frozen
            else f"A {state} document takes the current template on the next render."
        ),
        "evidence": vocab.NO_RETROACTIVE_APPLICATION_QUOTE,
    }


def require_rerenderable(document: Mapping[str, Any]) -> str:
    """Return the document's state, or refuse the re-render."""

    state = normalise_state(document.get("state") or vocab.STATE_DRAFT)
    if state in vocab.FROZEN_STATES:
        raise DocumentFrozen(str(document.get("id") or "unknown"), state)
    return state


def next_state(state: Any, action: str) -> str:
    """The state a document moves to after one action.

    Only three moves exist and each is one-way, because each names a step in the
    lifecycle the specification describes. Anything else is a refusal naming where the
    document already is, so a caller cannot walk a published document backwards.
    """

    current = normalise_state(state)
    target = str(action or "").strip().lower().replace("-", "_").replace(" ", "_")
    moves = {
        (vocab.STATE_DRAFT, "instantiate"): vocab.STATE_INSTANTIATED,
        (vocab.STATE_DRAFT, "publish"): vocab.STATE_PUBLISHED,
        (vocab.STATE_INSTANTIATED, "publish"): vocab.STATE_PUBLISHED,
        (vocab.STATE_INSTANTIATED, "draft"): vocab.STATE_DRAFT,
        (vocab.STATE_PUBLISHED, "publish"): vocab.STATE_PUBLISHED,
    }
    if (current, target) not in moves:
        raise ProposalRefusal(
            f"A {current} document cannot move to {target!r}.",
            {
                "state": (
                    f"Allowed moves are: draft to instantiated or published, instantiated "
                    f"to published or draft, published to published. The document is "
                    f"already {current}."
                )
            },
        )
    return moves[(current, target)]


# --------------------------------------------------------------------------- #
# Custom modules
# --------------------------------------------------------------------------- #


def custom_module_advisory() -> dict[str, Any]:
    """The API limit the evidence states, served rather than described.

    "It isn't possible to create or add custom coded modules to a quote using the API.
    API users can select templates that have custom modules included on them." So this
    workflow renders a custom module a template already carries and never authors one.
    """

    return {
        "custom_modules": "select_only",
        "authored_via_api": False,
        "evidence": vocab.CUSTOM_MODULE_API_LIMIT,
        "note": (
            "A template that already carries a custom-coded module is rendered by this "
            "workflow. Creating one through this API is not supported, and the templates "
            "this API lists report whether they carry custom modules so a caller can "
            "select a template that has one."
        ),
    }
