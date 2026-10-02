"""Term matching, scoring and zero-hit broadening.

Pure functions over already-retrieved records. No store, no clock, no I/O.

The ranking here is deliberately simple and explainable: a record matches when
**every** token in the term appears somewhere in the selected search fields,
and its score is a weighted count of those hits with a bonus for a title
match. A sales rep assembling a room needs the right deck in the tray, not a
subtle ordering, and a score they cannot reproduce by hand is a score they
cannot debug.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from dsr.search.contract import LibrarySchema, get_path

_TOKEN = re.compile(r"[a-z0-9]+")

DEFAULT_FIELD_WEIGHTS: Mapping[str, float] = {
    "name": 6.0,
    "description": 3.0,
    "properties": 2.0,
    "body": 1.0,
}
"""A title match should outrank a body match, or every long deck crowds out the
one document actually called "Security Overview"."""

EXACT_MATCH_BONUS = 20.0
PREFIX_MATCH_BONUS = 10.0


def tokenize(value: Any, *, include_keys: bool = False) -> list[str]:
    """Lowercase alphanumeric tokens from any JSON value.

    Objects and arrays are walked, and with ``include_keys`` so are their keys.
    Custom-property search needs the keys: a record tagged
    ``properties: {"Region": "APAC"}`` matches a search for "region" whether the
    user typed the property name or its value.
    """
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, Mapping):
        tokens: list[str] = []
        for key, child in value.items():
            if include_keys:
                tokens.extend(_TOKEN.findall(str(key).lower()))
            tokens.extend(tokenize(child, include_keys=include_keys))
        return tokens
    if isinstance(value, (list, tuple)):
        tokens = []
        for child in value:
            tokens.extend(tokenize(child, include_keys=include_keys))
        return tokens
    if isinstance(value, (int, float)):
        return _TOKEN.findall(str(value).lower())
    return _TOKEN.findall(str(value).lower())


@dataclass(frozen=True)
class FieldText:
    """One logical field of one record, reduced to what matching needs."""

    tokens: frozenset[str]
    normalized: str
    """Whitespace-collapsed lowercase text, for exact and prefix comparison."""


def field_text(value: Any, *, include_keys: bool = False) -> FieldText:
    if isinstance(value, str):
        return FieldText(
            tokens=frozenset(_TOKEN.findall(value.lower())),
            normalized=" ".join(value.lower().split()),
        )
    return FieldText(tokens=frozenset(tokenize(value, include_keys=include_keys)), normalized="")


@dataclass(frozen=True)
class Match:
    """A record that matched, with the score that ranked it."""

    record: Mapping[str, Any]
    score: float
    matched_fields: tuple[str, ...]


class Scorer:
    """Scores records against a query term across a configurable field set.

    ``weights`` defaults to :data:`DEFAULT_FIELD_WEIGHTS`; an unmapped field
    scores at 1.0 so a team pointing the search at a field we have never heard
    of still gets sensible ordering.
    """

    def __init__(
        self,
        schema: LibrarySchema,
        weights: Mapping[str, float] | None = None,
    ) -> None:
        self.schema = schema
        self.weights = dict(DEFAULT_FIELD_WEIGHTS if weights is None else weights)

    def weight(self, field_name: str) -> float:
        return float(self.weights.get(field_name, 1.0))

    def read(self, record: Mapping[str, Any], field_name: str) -> FieldText:
        """The searchable text of one logical field on one record."""
        data = record.get("data") or {}
        path = self.schema.resolve(field_name)
        # Custom properties are searched by name as well as value, so keys count.
        return field_text(
            get_path(data, path), include_keys=path.startswith(self.schema.properties_root)
        )

    def score(
        self,
        record: Mapping[str, Any],
        term_tokens: Sequence[str],
        search_fields: Sequence[str],
    ) -> Match | None:
        """Score one record, or ``None`` when a token is missing everywhere.

        A record only matches when *all* term tokens are present. Partial
        matches are noise when someone is assembling a room: half a security
        deck is worse than no deck, because it looks like a result.
        """
        wanted = set(term_tokens)
        if not wanted:
            # An empty term is "query all content", per the documented
            # behaviour, so everything matches with a flat score and the
            # caller's sort decides the order.
            return Match(record=record, score=0.0, matched_fields=())

        score = 0.0
        matched_fields: list[str] = []
        seen: set[str] = set()
        for field_name in search_fields:
            text = self.read(record, field_name)
            if not text.tokens:
                continue
            hits = wanted & text.tokens
            if not hits:
                continue
            weight = self.weight(field_name)
            score += weight * (len(hits) / len(wanted))
            if hits == wanted:
                score += weight
            matched_fields.append(field_name)
            seen |= hits
            if field_name == self.schema.primary_field:
                if text.normalized == " ".join(term_tokens):
                    score += EXACT_MATCH_BONUS
                elif text.normalized.startswith(" ".join(term_tokens)):
                    score += PREFIX_MATCH_BONUS

        if seen != wanted:
            return None
        return Match(record=record, score=score, matched_fields=tuple(matched_fields))


def vocabulary(
    records: Iterable[Mapping[str, Any]], scorer: Scorer, search_fields: Sequence[str]
) -> Counter[str]:
    """Document frequency of every token across the searchable fields.

    Used only for zero-hit broadening. It is a plain counter over the same
    fields the search reads, so it costs one pass and no new index.
    """
    counts: Counter[str] = Counter()
    for record in records:
        for field_name in search_fields:
            counts.update(scorer.read(record, field_name).tokens)
    return counts


def _character_ngrams(token: str, size: int = 3) -> set[str]:
    if len(token) < size:
        return {token}
    return {token[i : i + size] for i in range(len(token) - size + 1)}


def suggest(term: str, counts: Mapping[str, int], *, limit: int = 3) -> list[str]:
    """Propose replacement terms for a query that found nothing.

    Two passes, best first: vocabulary terms that extend the query term by
    prefix (``secur`` -> ``security``), then terms sharing a character trigram,
    which catches typos and plural endings that a prefix cannot. Ranked by
    document frequency so the common reading of an ambiguous term wins, then
    alphabetically so the order is deterministic.

    The researched operation delegates this to the vendor's index and does not
    document how its suggestions are derived; the behaviour it does document is
    that a zero-hit query is retried with related terms and the term that
    actually matched is returned as ``actualSearchTerm``. The mechanism here is
    a local inference from the library's own vocabulary.
    """
    cleaned = " ".join(str(term).lower().split())
    if not cleaned:
        return []
    tokens = _TOKEN.findall(cleaned)
    if not tokens:
        return []

    suggestions: list[str] = []

    # Pass 1: terms that begin with the whole cleaned term.
    prefix = cleaned.replace(" ", "")
    for candidate in counts:
        if candidate != prefix and candidate.startswith(prefix) and len(candidate) > len(prefix):
            suggestions.append(candidate)

    # Pass 2: terms that share a character trigram with any query token.
    if not suggestions:
        grams: set[str] = set()
        for token in tokens:
            grams |= _character_ngrams(token)
        for candidate in counts:
            if candidate in tokens:
                continue
            if grams & _character_ngrams(candidate):
                suggestions.append(candidate)

    suggestions = [term for term in dict.fromkeys(suggestions)]
    suggestions.sort(key=lambda term: (-int(counts.get(term, 0)), len(term), term))
    return suggestions[: max(0, limit)]
