"""Indicators: how specific a claim is, and whether the evidence supports it.

The research gives this workflow its sharpest piece of evidence, and it is a
correction rather than a specification. It contrasts two indicators::

    {"key": "spent_more_than_30s_on_site", "metadata": {"time_in_seconds": 42}}
    {"key": "time_spent_on_site",          "metadata": {"time_in_seconds": 30}}

and concludes "Indicators should be very specific." The difference between them
is not the metadata, which is the same field in both. It is that the first key
*claims a bound* - more than thirty seconds - and the number in the metadata is
the evidence that the bound was met. The second key names a measurement and
nothing more, so 30 seconds in the metadata supports "time spent: 30" and says
nothing about whether that is interesting.

That gives this module two jobs, and the split between them is the point:

* :func:`specificity_findings` is a **lint at registration time**. It says the
  key states no bound, and it does *not* refuse the registration. The research
  offers this as advice, and a lint that refuses would be inventing a
  requirement it does not state.
* :func:`evaluate` is a **check at emit time**. A key that *does* state a bound
  is held to it, because a specific claim that its own evidence contradicts is a
  sentence a seller would read and believe. A key that states no bound qualifies
  on trust, and the resulting signal says so, so the trust is visible rather than
  silent.

The claim grammar
-----------------
Deliberately tiny, and published at ``GET /api/wf-027/vocabulary`` so a
registration author can see exactly which forms are understood::

    <subject> <comparative> <number><unit?> <rest...>

for example ``spent_more_than_30s_on_site`` or ``watched_more_than_75_percent``.
The comparatives understood are :data:`COMPARATIVES`. Anything else is not a
claim this build can check, which is a different thing from a claim that failed,
and the two are reported separately.

The observation is resolved from ``metadata_shape`` rather than guessed from the
key. A shape that declares exactly one numeric field resolves to it; a shape with
several resolves to whichever name shares the most tokens with the key. If neither
produces an answer the claim is unresolvable, the indicator qualifies on trust,
and the signal is flagged - it does not silently pass as though it had been
checked.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.signals import schema

#: The two indicators the research contrasts, kept verbatim so the lint can point
#: a registrant at the pair rather than only at the rule.
SPECIFIC_EXAMPLE: dict[str, Any] = {
    "key": "spent_more_than_30s_on_site",
    "metadata": {"time_in_seconds": 42},
}
VAGUE_EXAMPLE: dict[str, Any] = {
    "key": "time_spent_on_site",
    "metadata": {"time_in_seconds": 30},
}

#: Comparison word to the operator it stands for.
COMPARATIVES: dict[str, str] = {
    "more_than": "gt",
    "over": "gt",
    "at_least": "ge",
    "at_most": "le",
    "under": "lt",
    "fewer_than": "lt",
}

#: Why each comparison exists, for the vocabulary endpoint. Nothing here is
#: sourced; the comparatives are the ordinary English ways to state a bound, and
#: the list is closed so a key is either checked or reported as unchecked rather
#: than interpreted inconsistently.
COMPARATIVE_MEANING: dict[str, str] = {
    "more_than": "strictly greater than the bound",
    "over": "strictly greater than the bound",
    "at_least": "greater than or equal to the bound",
    "at_most": "less than or equal to the bound",
    "under": "strictly less than the bound",
    "fewer_than": "strictly less than the bound",
}

#: Every reason an indicator can qualify or not, with what it means. Served at the
#: vocabulary endpoint so a client can label a decision without hard-coding it.
QUALIFICATION_REASONS: dict[str, str] = {
    "bound_met": "the observation satisfies the bound the key states",
    "bound_not_met": "the observation does not satisfy the bound the key states",
    "no_bound_claimed": "the key states no bound, so the indicator qualified on trust",
    "bound_unresolvable": "the key states a bound that no metadata field could be matched to",
    "bound_unverifiable": "the bound names a field the metadata did not carry, so it qualified on trust",
    "metadata_mismatch": "the metadata does not satisfy the indicator's metadata_shape",
    "undeclared_indicator": "the registration does not declare this indicator key",
}


def _split_bound(token: str) -> tuple[float | None, str]:
    """Split ``30s`` into ``(30.0, "s")`` and ``75`` into ``(75.0, "")``."""
    digits = 0
    while digits < len(token) and (
        token[digits].isdigit() or (digits == 0 and token[digits] in "+-")
    ):
        digits += 1
    if digits == 0:
        return None, ""
    try:
        number = float(token[:digits])
    except ValueError:
        return None, ""
    return number, token[digits:]


def _comparative_at(tokens: Sequence[str], index: int) -> tuple[str, int] | None:
    """The comparative starting at ``index``, as ``(word, token_count)``.

    A key is underscore-separated, so ``more_than`` arrives as two tokens. The
    longest match wins, which is what lets ``at_least`` be recognised without
    ``at`` also matching a prefix of it.
    """
    for width in (3, 2, 1):
        if index + width > len(tokens):
            continue
        word = "_".join(tokens[index : index + width])
        if word in COMPARATIVES:
            return word, width
    return None


def parse_claim(key: str) -> dict[str, Any] | None:
    """Read a bound out of an indicator key, or ``None`` when there is not one.

    ``None`` is a real answer and not a failure: a key such as
    ``viewed_pricing_page`` states no bound, so there is nothing to check it
    against, and that is reported as :data:`QUALIFICATION_REASONS` rather than as
    a parse error.
    """
    if not isinstance(key, str) or not key.strip():
        return None
    tokens = [token for token in key.strip().lower().replace("-", "_").split("_") if token]
    for index in range(len(tokens)):
        found = _comparative_at(tokens, index)
        if found is None:
            continue
        word, width = found
        bound_index = index + width
        if bound_index >= len(tokens):
            # "more_than" with nothing numeric after it is not a claim this
            # grammar can read. Keep looking, so a key that repeats a
            # comparative still resolves on the one that has a number.
            continue
        threshold, unit = _split_bound(tokens[bound_index])
        if threshold is None:
            continue
        return {
            "comparison": COMPARATIVES[word],
            "comparative": word,
            "threshold": threshold,
            "unit": unit,
            "subject": "_".join(tokens[:index]),
            "trailing": "_".join(tokens[bound_index + 1 :]),
            "key": key.strip(),
        }
    return None


def numeric_fields(metadata_shape: Mapping[str, Any] | None) -> list[str]:
    """Field names in a shape that a bound could be compared against."""
    if not isinstance(metadata_shape, Mapping):
        return []
    properties = metadata_shape.get("properties")
    if not isinstance(properties, Mapping):
        return []
    found: list[str] = []
    for name, sub in properties.items():
        if not isinstance(sub, Mapping):
            continue
        declared = sub.get("type")
        names = (
            [declared]
            if isinstance(declared, str)
            else list(declared)
            if isinstance(declared, list)
            else []
        )
        numeric = {"number", "integer"} & set(names)
        bounded = any(
            keyword in sub
            for keyword in (
                "minimum",
                "maximum",
                "exclusiveMinimum",
                "exclusiveMaximum",
                "multipleOf",
            )
        )
        if numeric or bounded:
            found.append(str(name))
    return sorted(found)


def resolve_observation_field(
    claim: Mapping[str, Any] | None, metadata_shape: Mapping[str, Any] | None
) -> str | None:
    """Which metadata field the claim is about, or ``None`` when it is ambiguous.

    Resolved from the declared shape, not from the key, because the shape is the
    contract and the key is prose.

    Only an *unambiguous* shape resolves. One numeric field is unambiguous. Two or
    more is not, and guessing between them would be guessing: the research's own
    good key, ``spent_more_than_30s_on_site``, shares no token with
    ``time_in_seconds``, so a name-similarity heuristic does not even work on the
    one example the research supplies. Rather than match badly, a shape with
    several numeric fields resolves to nothing, the indicator qualifies on trust,
    the signal is flagged ``bound_unresolvable``, and the registration lint tells
    the registrant at registration time rather than letting them discover it in a
    seller's feed.
    """
    candidates = numeric_fields(metadata_shape)
    if len(candidates) != 1 or claim is None:
        return None
    return candidates[0]


def _compare(value: float, threshold: float, comparison: str) -> bool:
    if comparison == "gt":
        return value > threshold
    if comparison == "ge":
        return value >= threshold
    if comparison == "le":
        return value <= threshold
    if comparison == "lt":
        return value < threshold
    return False


def evaluate(indicator: Mapping[str, Any], metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Decide whether ``metadata`` supports the claim ``indicator`` makes.

    Always returns a decision with a named ``reason``, including when the
    indicator is not declared or the metadata does not match the shape. There is
    no path through this function that returns nothing, because the caller's next
    step is either to emit a signal or to tell a person why it did not, and a
    silent no is a bug someone finds in production.
    """
    key = str(indicator.get("key", ""))
    shape = indicator.get("metadata_shape")
    values = dict(metadata or {})

    findings = schema.validate(shape, values) if isinstance(shape, Mapping) else []
    if findings:
        return {
            "key": key,
            "qualifies": False,
            "reason": "metadata_mismatch",
            "claim": parse_claim(key),
            "observation": None,
            "findings": findings,
        }

    claim = parse_claim(key)
    if claim is None:
        return {
            "key": key,
            "qualifies": True,
            "reason": "no_bound_claimed",
            "claim": None,
            "observation": None,
            "findings": [],
        }

    field = resolve_observation_field(claim, shape)
    if field is None:
        return {
            "key": key,
            "qualifies": True,
            "reason": "bound_unresolvable",
            "claim": claim,
            "observation": None,
            "findings": [],
        }

    raw = values.get(field)
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        # The shape let this through, so the field is either absent or not a
        # number. Either way the bound cannot be checked, and an uncheckable
        # specific claim is reported rather than quietly accepted.
        return {
            "key": key,
            "qualifies": True,
            "reason": "bound_unverifiable",
            "claim": claim,
            "observation": {"field": field, "value": raw},
            "findings": [],
        }

    met = _compare(float(raw), float(claim["threshold"]), str(claim["comparison"]))
    return {
        "key": key,
        "qualifies": met,
        "reason": "bound_met" if met else "bound_not_met",
        "claim": claim,
        "observation": {
            "field": field,
            "value": raw,
            "threshold": claim["threshold"],
            "comparison": claim["comparison"],
        },
        "findings": [],
    }


def specificity_findings(
    key: str, metadata_shape: Mapping[str, Any] | None, description: Any = None
) -> list[dict[str, Any]]:
    """Everything worth saying about how specific this indicator is.

    Warnings, never refusals. Each one names the research's own correction, so a
    registrant who is told their key is vague can see what a specific one looks
    like instead of guessing.
    """
    findings: list[dict[str, Any]] = []
    claim = parse_claim(key)

    if claim is None:
        findings.append(
            {
                "code": "indicator_key_states_no_bound",
                "severity": "warning",
                "detail": (
                    f"The key {key!r} names a measurement without saying what would make it "
                    "worth a seller's time. The research's own example of a specific "
                    f"indicator is {SPECIFIC_EXAMPLE['key']!r}, whose bound is in the key "
                    "and whose metadata is the evidence for it; the vague one is "
                    f"{VAGUE_EXAMPLE['key']!r}. Signals on this indicator will be emitted "
                    "on trust and flagged as such."
                ),
            }
        )
    else:
        if resolve_observation_field(claim, metadata_shape) is None:
            findings.append(
                {
                    "code": "indicator_bound_unresolvable",
                    "severity": "warning",
                    "detail": (
                        f"The key states a bound ({claim['comparative']} "
                        f"{claim['threshold']}{claim['unit']}) but metadata_shape does not "
                        f"identify a single numeric field to compare it against. Declared "
                        f"numeric fields: "
                        f"{', '.join(numeric_fields(metadata_shape)) or 'none'}. A bound is "
                        "only checked when there is exactly one field it could be about, "
                        "because picking between two would be a guess. Signals on this "
                        "indicator will be emitted on trust and flagged as such."
                    ),
                }
            )

    if not numeric_fields(metadata_shape):
        findings.append(
            {
                "code": "indicator_metadata_not_quantified",
                "severity": "warning",
                "detail": (
                    "metadata_shape declares no numeric field, so the metadata describes the "
                    "event but does not quantify it. The research matches interactions to "
                    "indicators with quantified metadata, so a value here is what makes a "
                    "sentence specific rather than merely true."
                ),
            }
        )

    if not description:
        findings.append(
            {
                "code": "indicator_description_missing",
                "severity": "warning",
                "detail": (
                    "The research describes an indicator as having metadata *and* a "
                    "description, and the description is what a seller reads in the Live "
                    "Feed. Without one the indicator falls back to the signal's own "
                    "description, which says less than it should."
                ),
            }
        )

    return findings


def describe_vocabulary() -> dict[str, Any]:
    """The claim grammar, for the vocabulary endpoint and for the page's help text."""
    return {
        "comparatives": [
            {"word": word, "comparison": comparison, "meaning": COMPARATIVE_MEANING[word]}
            for word, comparison in COMPARATIVES.items()
        ],
        "claim_form": "<subject> <comparative> <number><unit?> <rest...>",
        "claim_examples": [
            {"key": SPECIFIC_EXAMPLE["key"], "claim": parse_claim(SPECIFIC_EXAMPLE["key"])},
            {
                "key": "watched_more_than_75_percent",
                "claim": parse_claim("watched_more_than_75_percent"),
            },
            {"key": VAGUE_EXAMPLE["key"], "claim": parse_claim(VAGUE_EXAMPLE["key"])},
        ],
        "quality_examples": {"specific": SPECIFIC_EXAMPLE, "vague": VAGUE_EXAMPLE},
        "qualification_reasons": QUALIFICATION_REASONS,
    }


def evaluate_all(
    declared: Sequence[Mapping[str, Any]], observations: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    """Evaluate every declared indicator against one set of observations.

    The same observation object goes to each indicator, because an interaction's
    measurements are one set of facts and each indicator's ``metadata_shape``
    decides which of them are its evidence. Narrowing per indicator first would
    mean deciding which shape owns which field before any shape had been read.

    Every declared indicator comes back, qualified or not. A caller matching an
    interaction against a registration needs to be able to say "none of these
    fired" and name why for each one, and a list carrying only the qualifiers
    could not do that.
    """
    values = dict(observations or {})
    return [evaluate(indicator, values) for indicator in declared]
