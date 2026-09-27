"""Matching an inbound row against existing rows, and the seam to add matchers.

The research's extensibility note is specific: "A third party can register
additional matchers (fuzzy domain + name) evaluated in the room *before* calling
the CRM, reducing wasted API calls."

Two things follow from that sentence and they shape this module:

1. **Matchers are registered, not hard-coded.** A matcher is an object with an
   ``id``, a ``compare`` callable and a ``threshold``, so a fuzzy scorer can be
   added without editing this file. The built-in matchers are registered the
   same way a third party's would be, which is the only way to be sure the seam
   actually is one.

2. **Matching is separated from the CRM call.** :func:`match_rows` is pure - it
   takes rows and returns matches. The engine runs it over the room's own rows
   first and only then over the CRM's, so a matcher that resolves the question
   locally can stop the round trip. See the ``local-precheck`` inference for what
   "in the room" is taken to mean here.

Precedence
----------

When several keys match *different* records, only one answer is reported. The
order comes from :data:`~dsr.dedupe.vocabulary.KEY_PRECEDENCE_RATIONALE`:
external ID, then email, then account number, then domain. A match on the
first key that fired wins outright - the lower-precedence matches are still
returned in ``all_matches`` so nothing is hidden, but they do not decide.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from dsr.dedupe.errors import DedupeError
from dsr.dedupe.vocabulary import KEY_PRECEDENCE_RATIONALE, MATCH_KEYS, key_spec

#: An exact match on a normalised value. Fuzzy matchers score below this.
EXACT_SCORE = 1.0

#: The highest score a *fuzzy* matcher may report, however well it agrees.
#:
#: Containment can be complete - "Jose A. Ramirez" contains every token of "Jose
#: Ramirez" - and reporting that as 1.0 would claim a fuzzy matcher is exactly as
#: certain as an exact key match. It is not: full containment says nothing about
#: the tokens that differ. Keeping fuzzy scores strictly below 1.0 also means a
#: connection's ``min_score`` of 1.0 means what it says, which is that only exact
#: matches count.
FUZZY_CEILING = 0.95

#: A matcher compares the single field its key names.
SCOPE_VALUE = "value"

#: A matcher compares whole rows, because the signal is a *combination* of
#: fields. The research's own example is one of these: "fuzzy domain + name".
SCOPE_ROW = "row"


@dataclass(frozen=True)
class Match:
    """One key on one row that matched an inbound value."""

    key: str
    value: str
    record_id: str
    score: float
    matcher: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "record_id": self.record_id,
            "score": self.score,
            "matcher": self.matcher,
        }


@dataclass(frozen=True)
class Matcher:
    """A registered way of deciding that two values are the same thing.

    ``compare`` returns a score in ``[0, 1]`` or ``None`` when the two values
    cannot be compared at all - a missing email is not a 0.0 match, it is not a
    comparison, and conflating the two would make every record without an email
    look like a match on it.

    ``scope`` says what it is handed: :data:`SCOPE_VALUE` for the single field
    its ``key`` names, or :data:`SCOPE_ROW` for both whole rows, which is what a
    matcher combining two fields needs.
    """

    id: str
    label: str
    key: str
    compare: Callable[[Any, Any], float | None]
    scope: str = SCOPE_VALUE
    threshold: float = EXACT_SCORE
    builtin: bool = False
    description: str = ""

    def score(self, inbound_row: Mapping[str, Any], record_row: Mapping[str, Any]) -> float | None:
        """Score this matcher against one row pair, or ``None`` for no comparison."""
        if self.scope == SCOPE_ROW:
            left: Any = inbound_row
            right: Any = record_row
        else:
            left = inbound_row.get(self.key)
            right = record_row.get(self.key)
        try:
            score = self.compare(left, right)
        except Exception:  # noqa: BLE001 - a third-party matcher must not break a decision
            return None
        if score is None:
            return None
        return max(0.0, min(float(score), 1.0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "key": self.key,
            "scope": self.scope,
            "threshold": self.threshold,
            "builtin": self.builtin,
            "description": self.description,
        }


def _exact_comparer(key: str) -> Callable[[Any, Any], float | None]:
    """The built-in comparer for a key: equal after that key's normalisation."""
    spec = key_spec(key)
    normalise = spec["normalise"]

    def compare(inbound_value: Any, record_value: Any) -> float | None:
        left = normalise(inbound_value)
        right = normalise(record_value)
        if not left or not right:
            return None
        return EXACT_SCORE if left == right else 0.0

    return compare


def builtin_matchers() -> tuple[Matcher, ...]:
    """One exact matcher per published key, plus the domain+name one the research
    names as the example a third party would add.

    The fuzzy matcher is registered here rather than left out so the seam is
    exercised by the product's own demo: if a third-party matcher can be added,
    the suite should prove it against one that already works.
    """
    matchers: list[Matcher] = [
        Matcher(
            id=f"exact:{entry['key']}",
            label=f"{entry['label']} (exact)",
            key=entry["key"],
            compare=_exact_comparer(entry["key"]),
            threshold=EXACT_SCORE,
            builtin=True,
            description=f"Equal after the {entry['label'].lower()} normalisation.",
        )
        for entry in MATCH_KEYS
    ]
    matchers.append(
        Matcher(
            id="fuzzy:domain_name",
            label="Domain and name (fuzzy)",
            key="domain",
            compare=_domain_name_score,
            scope=SCOPE_ROW,
            threshold=0.85,
            builtin=False,
            description=(
                "The matcher the research names as the third-party example: a same-domain "
                "contact whose name overlaps a known one. A domain alone is never enough, "
                "because one company has hundreds of people."
            ),
        )
    )
    return tuple(matchers)


def _name_tokens(value: Any) -> frozenset[str]:
    text = "" if value is None else str(value)
    cleaned = "".join(character if character.isalnum() else " " for character in text.casefold())
    return frozenset(token for token in cleaned.split() if token)


def _domain_name_score(inbound: Any, record: Any) -> float | None:
    """The research's own example matcher: fuzzy domain plus name.

    ``inbound`` and ``record`` are whole row mappings here rather than two
    scalars, because the example the research gives is a *pair* of fields. A
    domain alone is never enough - one company has hundreds of people - so the
    score is the name overlap and it is only produced when the domains agree.

    The score is **containment**, not Jaccard: the fraction of the shorter name
    that appears in the longer one. Jaccard punishes exactly the case this
    matcher exists for - a person who now appears with a middle initial, a
    suffix, or a second surname scores 0.67 on Jaccard and is the same human. A
    shorter name of at least two tokens has to be wholly contained, and anything
    shorter than that is refused outright, so "Jose" cannot match every Ramirez
    at a company.
    """
    if not isinstance(inbound, Mapping) or not isinstance(record, Mapping):
        return None
    left_domain = key_spec("domain")["normalise"](inbound.get("domain"))
    right_domain = key_spec("domain")["normalise"](record.get("domain"))
    if not left_domain or left_domain != right_domain:
        return None
    left = _name_tokens(inbound.get("name"))
    right = _name_tokens(record.get("name"))
    if not left or not right:
        return None
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if len(shorter) < 2 or len(longer) == len(shorter):
        # A single token is a surname, not a person, so refuse rather than match
        # every holder of that name at the domain. Identical token counts means
        # the two names are the same set, which is the *exact* matcher's job - a
        # fuzzy matcher claiming it would be double-counting the same evidence.
        return None
    score = len(shorter & longer) / len(shorter)
    if score == 0.0:
        return None
    return round(min(score, FUZZY_CEILING), 4)


class MatcherRegistry:
    """The registered matchers, and the one place a third party adds one.

    Registration is by ``id``: re-registering an id replaces the matcher, which
    is what makes "override the built-in email matcher with a domain-aware one"
    possible without editing this file.
    """

    def __init__(self, matchers: Sequence[Matcher] | None = None) -> None:
        self._matchers: dict[str, Matcher] = {}
        for matcher in matchers if matchers is not None else builtin_matchers():
            self.register(matcher)

    def register(self, matcher: Matcher) -> Matcher:
        if not isinstance(matcher, Matcher):
            raise DedupeError("a matcher must be a Matcher, not " + type(matcher).__name__)
        if not matcher.id:
            raise DedupeError("a matcher needs an id")
        key_spec(matcher.key)
        if not 0.0 <= matcher.threshold <= 1.0:
            raise DedupeError(f"matcher {matcher.id!r} threshold must be between 0 and 1")
        self._matchers[matcher.id] = matcher
        return matcher

    def unregister(self, matcher_id: str) -> None:
        self._matchers.pop(matcher_id, None)

    def reset(self) -> None:
        """Restore the built-in set. Used by tests and by the seed."""
        self._matchers.clear()
        for matcher in builtin_matchers():
            self.register(matcher)

    def get(self, matcher_id: str) -> Matcher:
        matcher = self._matchers.get(matcher_id)
        if matcher is None:
            raise DedupeError(f"unknown matcher {matcher_id!r}")
        return matcher

    def all(self) -> tuple[Matcher, ...]:
        """Registered matchers, built-ins first, then by id.

        A stable order so two runs over the same rows produce the same match
        list, and so a decision is reproducible.
        """
        return tuple(
            sorted(self._matchers.values(), key=lambda matcher: (not matcher.builtin, matcher.id))
        )

    def for_key(self, key: str) -> tuple[Matcher, ...]:
        return tuple(matcher for matcher in self.all() if matcher.key == key)


#: The process-wide registry. A feature module could hold its own, but the
#: research describes matchers as something "a third party can register", which
#: implies one shared set rather than one per request.
REGISTRY = MatcherRegistry()


def match_rows(
    inbound: Mapping[str, Any],
    rows: Iterable[Mapping[str, Any]],
    keys: Sequence[str],
    registry: MatcherRegistry | None = None,
    *,
    min_score: float | None = None,
) -> list[Match]:
    """Every match between ``inbound`` and ``rows``, best first.

    Pure: no store, no clock, no network. A row with no id cannot be matched -
    there would be nothing to log as "the matching record id" - so it is skipped
    rather than matched against an empty id.

    ``min_score`` overrides each matcher's own threshold when given. It is the
    per-connection knob behind "high-confidence cases", and it *replaces* the
    thresholds rather than combining with them: a connection that wants exact
    matches only raises it to 1.0, which is the one value a fuzzy scorer can
    never reach.
    """
    active = registry or REGISTRY
    matches: list[Match] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        record_id = str(row.get("id") or "")
        if not record_id:
            continue
        for key in keys:
            spec = key_spec(key)
            inbound_value = inbound.get(key)
            if inbound_value is None or not spec["normalise"](inbound_value):
                continue
            for matcher in active.for_key(key):
                score = matcher.score(inbound, row)
                if score is None:
                    continue
                threshold = matcher.threshold if min_score is None else min_score
                if score < threshold:
                    continue
                fingerprint = (key, record_id, matcher.id)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                matches.append(
                    Match(
                        key=key,
                        value=spec["normalise"](inbound_value),
                        record_id=record_id,
                        score=round(score, 4),
                        matcher=matcher.id,
                    )
                )
    return sorted(matches, key=lambda match: (-match.score, match.key, match.record_id))


def decisive(
    matches: Sequence[Match],
    keys: Sequence[str],
) -> tuple[str | None, list[Match]]:
    """Which key decides, and the matches under it.

    The distinction that matters is **how many records** the matches point at,
    not how many keys fired. Two keys matching *the same* record is a stronger
    result, not an ambiguous one - a lead whose email and domain both point at
    one existing contact has certainly been seen before, and answering that with
    a hard block would be wrong in the way that actually loses leads.

    Returns:

    * ``(None, [])`` - nothing matched, so the answer is a clean create.
    * ``(key, [one match])`` - one record, and the highest-precedence key that
      found it. This is the researched "duplicate alert with the matching record
      id".
    * ``(key, matches)`` with one key - several records on a single key. The
      caller turns this into the 300 hard block.
    * ``(None, matches)`` - several keys pointing at *different* records, which
      is the ambiguous case, and also a hard block. Only this case is ambiguous,
      because there is no single "matching record id" to act on.
    """
    if not matches:
        return None, []
    records = {match.record_id for match in matches}
    by_key: dict[str, list[Match]] = {}
    for match in matches:
        by_key.setdefault(match.key, []).append(match)

    if len(records) == 1:
        # One record, however many keys or matchers found it. Report it under the
        # highest-precedence key, so the decision names the strongest evidence.
        for key in keys:
            if key in by_key:
                return key, list(by_key[key])
        return next(iter(by_key)), list(next(iter(by_key.values())))

    if len(by_key) == 1:
        return next(iter(by_key)), list(next(iter(by_key.values())))

    return None, list(matches)


def match_summary() -> dict[str, Any]:
    """The registry, served as data, so a client can show what is registered."""
    matchers = REGISTRY.all()
    return {
        "count": len(matchers),
        "precedence": [entry["key"] for entry in MATCH_KEYS],
        "precedence_rationale": KEY_PRECEDENCE_RATIONALE,
        "matchers": [matcher.to_dict() for matcher in matchers],
    }
