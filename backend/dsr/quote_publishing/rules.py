"""WF-094 rules: the pure decisions, with no store and no framework.

Every function here is total and takes plain values. Nothing reads or writes a
record. That is what makes each rule testable on its own, and it is why this
module imports no storage at all: the engine in :mod:`.publishing` is the only
place in the package that touches the store.

Source: ``docs/research/digital-sales-room-workflows/wf/WF-094.md``.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from dsr.quote_publishing.vocabulary import (
    CC_LIMIT,
    DEFAULT_QUOTE_DOMAIN,
    EMAIL_ATTACHMENT_CAP_BYTES,
    KNOWN_STATUSES,
    LANGUAGE_UNSET,
    LOCALE_UNSET,
    PDF_LOCATION_TEMPLATE,
    PUBLISHABLE_FROM,
    TIMEZONE_UNSET,
    UNLOCK_TARGETS,
)

_SLUG_ALLOWED = re.compile(r"[^a-z0-9]+")


class QuoteRuleError(ValueError):
    """A rule refused the request. The HTTP layer maps this to 422."""


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #


def normalise_status(raw: Any) -> str:
    """Read a quote's status as one of :data:`KNOWN_STATUSES`.

    The researched vocabulary is upper case (``DRAFT``, ``PUBLISHED``), but
    seeded records in this project carry lower case, so the comparison is
    case-insensitive. A status this feature does not recognise is returned as
    upper-cased text rather than raising, so the caller can refuse on the
    specific rule that applies instead of failing on a parse.
    """
    return str(raw or "").strip().upper()


def may_publish(status: Any) -> bool:
    """May a quote in this status be published?

    Publishing is a state transition, so it only makes sense from a state that
    is not already published or shared. Anything the research does not name as a
    publishable source is refused, including a status this feature never wrote.
    """
    return normalise_status(status) in PUBLISHABLE_FROM


def may_unlock(status: Any, target: Any) -> bool:
    """May a quote move to ``target`` as its way of releasing frozen totals?

    ``target`` is checked against the three names the research lists rather than
    trusted, because a caller that can name any target can also name one that
    keeps the quote frozen, which would be an unlock that does not unlock.

    ``status`` is accepted so a caller can ask the whole question in one place,
    but it is not part of the test. A quote already at an unlock target is
    already editable, so moving it there again is legal and the engine records it
    as a no-op that released nothing rather than as a release.
    """
    return normalise_status(target) in UNLOCK_TARGETS


def require_unlock_target(target: Any) -> str:
    """Validate an unlock target and return it normalised.

    Raises :class:`QuoteRuleError` naming the allowed set, because the research
    names three targets and a fourth is a guess a caller should not be allowed
    to make silently.
    """
    candidate = normalise_status(target)
    if candidate not in UNLOCK_TARGETS:
        allowed = ", ".join(UNLOCK_TARGETS)
        raise QuoteRuleError(
            f"an unlock target must be one of {allowed}; received {candidate or 'nothing'}"
        )
    return candidate


def require_publishable(status: Any) -> str:
    """Validate that a quote may be published and return its normalised status."""
    candidate = normalise_status(status)
    if candidate not in KNOWN_STATUSES:
        raise QuoteRuleError(f"quote status {candidate or 'is missing'} is not a quote status")
    if candidate not in PUBLISHABLE_FROM:
        raise QuoteRuleError(f"a quote in status {candidate} cannot be published")
    return candidate


# --------------------------------------------------------------------------- #
# Totals
# --------------------------------------------------------------------------- #


def compute_total(line_items: Iterable[Mapping[str, Any]]) -> float:
    """Total a quote's line items.

    The research does not define the arithmetic, so this states the choice:
    each item contributes ``quantity * price`` when both are present, otherwise
    its own amount, and rounding happens once at the end rather than per line.
    Rounding per line would let a four-line quote lose a cent to rounding and
    then disagree with a buyer who adds the printed lines up.
    """
    total = 0.0
    for item in line_items or ():
        if not isinstance(item, Mapping):
            continue
        quantity = _as_float(item.get("quantity"))
        price = _as_float(item.get("price"))
        if quantity is not None and price is not None:
            total += quantity * price
            continue
        amount = _as_float(item.get("amount"))
        if amount is not None:
            total += amount
    return round(total, 2)


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Domain and slug
# --------------------------------------------------------------------------- #


def resolve_domain(configured: Any) -> str:
    """The domain a published quote is served from.

    "By default, quotes are hosted on the landing page primary domain connected
    to your account", and a subdomain such as ``billing.website.com`` may be used
    instead. A workspace with no domain configured falls back to
    :data:`DEFAULT_QUOTE_DOMAIN`, and the caller records which answer it got, so
    a link that was already handed out cannot be repointed by a later settings
    change.
    """
    text = str(configured or "").strip().lower()
    text = text.replace("https://", "").replace("http://", "").strip("/")
    if not text:
        return DEFAULT_QUOTE_DOMAIN
    return text


def build_slug(quote_number: Any, fallback: Any) -> str:
    """A URL-safe slug for a quote.

    The researched ``hs_slug`` is set by state and is not supplied by the
    caller, so this derives it: the quote number lower-cased with everything that
    is not a letter or a digit collapsed to a single hyphen. An empty quote
    number falls back to the record id, which is always unique.
    """
    base = str(quote_number or "").strip().lower()
    base = _SLUG_ALLOWED.sub("-", base).strip("-")
    if base:
        return base
    seed = str(fallback or "").strip().lower()
    seed = _SLUG_ALLOWED.sub("-", seed).strip("-")
    return seed or "quote"


def public_link(domain: Any, slug: Any) -> str:
    """The quote's public URL: a domain plus a slug.

    "``hs_quote_link`` - The quote's publicly accessible URL." It is a computed
    property, so this function is the only thing that ever produces one and no
    caller can supply a link of their own.
    """
    host = str(domain or DEFAULT_QUOTE_DOMAIN).strip().strip("/")
    return f"https://{host}/{str(slug or '').strip()}"


def pdf_download_link(public: Any, record_id: Any) -> str:
    """The PDF link beside the public URL.

    The research names ``hs_pdf_download_link`` as one of the properties publish
    computes and does not document its shape. It is derived from the public URL
    and the record id, so it cannot point somewhere the public link does not.
    """
    base = str(public or "").strip().rstrip("/")
    return f"{base}/download/{str(record_id or '').strip()}"


def pdf_location(record_name: Any, record_id: Any) -> str:
    """The PDF's output location, by the documented naming convention.

    "the generated PDF file is always saved to the default location:
    ``<record_name>_<record_id>``". It is a name, not a path this workflow
    resolves, and it is recorded so a reader can see where the file landed.
    """
    return PDF_LOCATION_TEMPLATE.format(
        record_name=_SLUG_ALLOWED.sub("-", str(record_name or "quote").lower()).strip("-")
        or "quote",
        record_id=str(record_id or ""),
    )


# --------------------------------------------------------------------------- #
# Locale
# --------------------------------------------------------------------------- #


def resolve_locale(
    configured: Mapping[str, Any] | None,
    *,
    allowed_languages: Sequence[str],
    allowed_locales: Sequence[str],
    allowed_timezones: Sequence[str],
) -> dict[str, str]:
    """The language, locale and timezone a published quote renders in.

    The research lists ``hs_language``, ``hs_locale`` and ``hs_timezone`` among
    the computed publish properties and names locale and language as the control
    over the public surface.

    An unrecognised value falls back to the English default rather than to the
    first entry of the allowed list. That is a decision worth stating: the
    lists are ordered by locale preference, not by correctness, so a workspace
    that has cleared the languages list down to ``de`` would otherwise publish
    an English-language quote under a German configuration, silently. Falling
    back to English for an unknown value keeps the answer predictable.
    """
    settings = configured or {}

    def pick(key: str, allowed: Sequence[str], fallback: str) -> str:
        raw = str(settings.get(key) or "").strip()
        if raw and raw in allowed:
            return raw
        if fallback in allowed:
            return fallback
        return allowed[0] if allowed else fallback

    return {
        "language": pick("language", allowed_languages, LANGUAGE_UNSET),
        "locale": pick("locale", allowed_locales, LOCALE_UNSET),
        "timezone": pick("timezone", allowed_timezones, TIMEZONE_UNSET),
    }


# --------------------------------------------------------------------------- #
# Sharing
# --------------------------------------------------------------------------- #


def may_attach_pdf(size_bytes: Any) -> bool:
    """Should an email carry the generated PDF as an attachment?

    "when you share a quote by email, HubSpot doesn't attach the generated quote
    PDF if it's larger than 20 MB." The email still sends. The cap is silent,
    so :func:`attachment_note` is what tells the caller it happened.
    """
    size = _as_float(size_bytes)
    if size is None:
        return False
    return size <= EMAIL_ATTACHMENT_CAP_BYTES


def attachment_note(size_bytes: Any) -> str | None:
    """The sentence to record when the PDF was dropped, or ``None``.

    The cap is silent in the researched platform, so the reason is recorded on
    the event instead of being lost. A caller that wants to know why an email
    carried no attachment reads this.
    """
    if may_attach_pdf(size_bytes):
        return None
    size = _as_float(size_bytes) or 0.0
    return (
        f"The generated PDF is {size / (1024 * 1024):.1f} MB, above the "
        f"{EMAIL_ATTACHMENT_CAP_BYTES // (1024 * 1024)} MB attachment cap, so the email "
        "was sent without it. The buyer can still download it from the link."
    )


# --------------------------------------------------------------------------- #
# Addresses
# --------------------------------------------------------------------------- #


def normalise_addresses(raw: Any) -> list[str]:
    """Read an address list, dropping blanks and duplicates, order preserved."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, (str, bytes)):
        candidates: list[Any] = [raw]
    elif isinstance(raw, Sequence):
        candidates = list(raw)
    else:
        candidates = [raw]

    seen: set[str] = set()
    addresses: list[str] = []
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        addresses.append(text)
    return addresses


def require_cc(addresses: Sequence[str]) -> list[str]:
    """Validate the Cc list against the researched cap of nine.

    "**Cc** up to nine addresses". The cap is refused rather than truncated: a
    caller that silently dropped the tenth address would send an email that
    reaches people the caller believes it reached.
    """
    if len(addresses) > CC_LIMIT:
        raise QuoteRuleError(
            f"a quote email accepts at most {CC_LIMIT} Cc addresses; {len(addresses)} were given"
        )
    return list(addresses)


def require_to(addresses: Sequence[str], contact: Any = None) -> str:
    """The single To address for a quote email.

    "**To** auto-fills from the associated contact (changeable; a new email
    auto-creates a contact)". So the contact's address is the default, the
    caller may override it, and an override that is not a single address is
    refused rather than picked from.
    """
    if addresses:
        if len(addresses) > 1:
            raise QuoteRuleError(f"a quote email takes one To address; {len(addresses)} were given")
        return addresses[0]
    if contact:
        return str(contact)
    raise QuoteRuleError("a quote email needs a To address")
