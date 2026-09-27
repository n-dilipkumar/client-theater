"""The published vocabularies for buyer intent signals.

The research fixes these sets by name, so they live in one place and are served
as data at ``GET /api/wf-027/vocabulary``. A client renders its pickers from the
same source the validator enforces against, so a value added here reaches every
client at once and cannot drift away from what the API accepts.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.signals.errors import EmissionError, SignalError

#: "urgency - A way for the application to define the urgency level. Accepted
#: values are high, medium, and low."
URGENCIES: tuple[str, ...] = ("high", "medium", "low")

#: What each urgency means for the order of the Live Feed.
#:
#: The research says urgency "drives priority" and nothing more, so this is the
#: only thing urgency is allowed to decide here. It deliberately does not decide
#: actionability: the research is explicit that a user's Play configuration
#: governs that, not the sender.
URGENCY_MEANING: dict[str, str] = {
    "high": "outranks everything else in the feed; the buyer did something that should be answered today",
    "medium": "worth a seller's attention today, below anything high",
    "low": "recorded and shown last; the buyer is engaging, but not urgently",
}

#: "urgency (high/medium/low) drives priority". Lower rank sorts first.
URGENCY_RANK: dict[str, int] = {name: index for index, name in enumerate(URGENCIES)}

#: Used when an emit omits ``urgency``.
#:
#: The research lists ``urgency`` among the fields a signal carries and fixes its
#: permitted values, but does not say what a sender gets for omitting it. Medium
#: is the middle of the three the research names, so a signal nobody classified
#: lands in the middle of the feed rather than at either extreme. Recorded as
#: the ``default-urgency`` inference.
DEFAULT_URGENCY = "medium"

#: "Possible attribution choices include Person, Account, User, Opportunity and
#: Email Content." The research spells the wire names out in its data flow, and
#: those are the keys the API accepts.
ATTRIBUTION_TYPES: tuple[str, ...] = (
    "person_id",
    "account_id",
    "opportunity_id",
    "email_tracked_content_id",
    "user_guid",
)

#: Which Salesloft object each attribution value names. Sourced from the same
#: sentence; kept beside the key so a form can label a field without a second
#: lookup table.
ATTRIBUTION_OBJECT: dict[str, str] = {
    "person_id": "Person",
    "account_id": "Account",
    "opportunity_id": "Opportunity",
    "email_tracked_content_id": "Email Content",
    "user_guid": "User",
}

#: The order in which the receiving seller is derived from the attribution
#: values present on a signal.
#:
#: Four of these five are ordered by the research corpus itself. Section 13 of
#: ``docs/research/raw/analytics-intent.md`` states the assignment precedence as
#: "User, Content, Person, Account" for the task a signal generates. That is a
#: different workflow from this one and this build does not implement it, but it
#: is the only sourced statement about which of these objects is consulted first,
#: so this package follows it rather than inventing an order of its own.
#:
#: ``opportunity_id`` has no place in that sentence. It is slotted directly after
#: ``user_guid`` because an opportunity names one deal and one owner, which is
#: more specific than Email Content, a Person, or an Account. Recorded as the
#: ``attribution-precedence`` inference; change the tuple, nothing else.
ATTRIBUTION_PRECEDENCE: tuple[str, ...] = (
    "user_guid",
    "opportunity_id",
    "email_tracked_content_id",
    "person_id",
    "account_id",
)

#: "users may choose to not take action on a signal. Actionability depends on the
#: end user's governance (Play) configurations and settings within Salesloft."
#:
#: Every signal carries this and every Live Feed row repeats it, because the
#: single most misleading thing this product could do is imply that emitting a
#: signal causes a seller to be given a task. It does not. The seller decides.
ACTIONABILITY_NOTE = (
    "This signal does not make anyone do anything. Whether a seller is given a task, a "
    "call, or a cadence step is governed by the end user's own Play configuration in "
    "Salesloft, not by the sender of the signal."
)

#: The locale assumed when a caller does not ask for one.
DEFAULT_LOCALE = "en"

#: "localized description (ICU Messages)". The fallback chain below is what makes
#: "localized" mean something at read time rather than at registration time.
LOCALE_FALLBACK = "en"


def require_urgency(value: Any) -> str:
    """Normalise and check an urgency level.

    Case and surrounding space are forgiven, because this is a value a human
    types into a form; the vocabulary itself is not loosened.
    """
    if value is None:
        return DEFAULT_URGENCY
    if not isinstance(value, str):
        raise EmissionError(f"urgency must be one of {', '.join(URGENCIES)}; got {type(value).__name__}")
    normalised = value.strip().lower()
    if normalised not in URGENCY_RANK:
        raise EmissionError(
            f"urgency must be one of {', '.join(URGENCIES)}; got {value!r}"
        )
    return normalised


def urgency_rank(value: str) -> int:
    """Sort key for the Live Feed. An unknown urgency sorts last, never first."""
    return URGENCY_RANK.get(str(value).strip().lower(), len(URGENCIES))


def require_attribution_type(value: Any) -> str:
    if not isinstance(value, str) or value.strip() not in ATTRIBUTION_TYPES:
        raise EmissionError(
            "attribution must name Salesloft objects from "
            f"{', '.join(ATTRIBUTION_TYPES)}; got {value!r}"
        )
    return value.strip()


def normalise_locale(value: Any) -> str:
    """Accept ``en``, ``en-GB``, ``EN_gb`` and store one spelling.

    Region tags are lowercased and the separator folded, so ``en-GB`` and
    ``en_GB`` are one locale rather than two that happen to look alike.
    """
    if value is None:
        return DEFAULT_LOCALE
    if not isinstance(value, str) or not value.strip():
        raise EmissionError(f"locale must be a string; got {value!r}")
    text = value.strip().replace("_", "-")
    parts = text.split("-")
    head = parts[0].lower()
    tail = [part.upper() for part in parts[1:]]
    return "-".join([head, *tail]) if tail else head


def resolve_locale(requested: str, available: Mapping[str, Any]) -> tuple[str | None, bool]:
    """Pick the locale to render in, and say whether it was a fallback.

    The chain is exact tag, then the bare language, then the default locale.
    Returning the fallback flag rather than hiding it matters: a seller reading a
    German sentence because that is the only German registration is fine, and a
    seller reading English without knowing why is not.
    """
    wanted = normalise_locale(requested)
    keys = {normalise_locale(key) for key in available}
    if wanted in keys:
        return wanted, False
    language = wanted.split("-")[0]
    if language in keys:
        return language, True
    if LOCALE_FALLBACK in keys:
        return LOCALE_FALLBACK, True
    first = sorted(keys)[0] if keys else None
    return first, first != wanted


def require_locale_map(value: Any, what: str) -> dict[str, str]:
    """Coerce a localized description into ``{locale: template}``.

    A bare string is accepted as a single ``en`` template. That is a
    convenience, not a loosening: the research requires a localized description
    and this still produces one, it just produces exactly one locale.
    """
    if isinstance(value, str) and value.strip():
        return {DEFAULT_LOCALE: value.strip()}
    if not isinstance(value, Mapping) or not value:
        raise SignalError(
            f"{what} must be a non-empty map of locale to message, "
            f"for example {{\"en\": \"...\"}}"
        )
    result: dict[str, str] = {}
    for locale, template in value.items():
        if not isinstance(template, str) or not template.strip():
            raise SignalError(f"{what} for locale {locale!r} must be a non-empty string")
        result[normalise_locale(locale)] = template.strip()
    return result


def describe() -> dict[str, Any]:
    """The whole published vocabulary, for clients and for the page's pickers."""
    return {
        "urgencies": [
            {"value": name, "rank": URGENCY_RANK[name], "meaning": URGENCY_MEANING[name]}
            for name in URGENCIES
        ],
        "default_urgency": DEFAULT_URGENCY,
        "attribution": [
            {
                "key": key,
                "object": ATTRIBUTION_OBJECT[key],
                "precedence": ATTRIBUTION_PRECEDENCE.index(key),
            }
            for key in ATTRIBUTION_TYPES
        ],
        "attribution_precedence": list(ATTRIBUTION_PRECEDENCE),
        "default_locale": DEFAULT_LOCALE,
        "locale_fallback": LOCALE_FALLBACK,
        "actionability_note": ACTIONABILITY_NOTE,
    }
