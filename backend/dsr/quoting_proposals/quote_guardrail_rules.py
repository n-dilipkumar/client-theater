"""WF-090: the quote-rules DSL, as a parser and a pure evaluator.

The grammar the evidence quotes is small and this module reads exactly that grammar
and no more:

    expression   := aggregate | quantifier | property-comparison
    aggregate    := AGG "(" "[name]" ")" "FROM" scope [ "WHERE" condition ]
                    comparator value
    quantifier   := ( "SOLD_TOGETHER" | "INCOMPATIBLE" ) "FROM" scope
                    [ "WHERE" condition ]
    property     := "[" scope "." name "]" comparator value
    condition    := property ( comparator value | "IN" "(" value, ... ")" )
    scope        := quote | line_item | deal | company | recipient_contact
                    | current_user

Everything here is pure. :mod:`dsr.quoting_proposals.quote_guardrail_engine`
turns a parsed rule into records; this module is what a test can call with two
lists and a number and check.

The one rule that must not be inverted
--------------------------------------

The specification's data flow says a SQL-like expression "evaluates to true/false"
and "**a true evaluation is a violation**". So :func:`evaluate_expression` returns
``violation: True`` when the comparison holds, and the publish gate blocks. Every
test in this workflow that claims a quote is blocked is written against that
sentence.

An unanswered question is not a violation
----------------------------------------

A rule reads a property the quote does not carry. That is not "the rule passed" and
it is not "the rule failed". :data:`~dsr.quoting_proposals.quote_guardrail_vocabulary.VERDICT_UNVERIFIABLE`
is a third answer, reported to the editor and never treated as a block, because
inventing a value the seller never supplied is a worse failure than admitting the
rule could not be checked. The same shape is used by WF-092's branch engine, so the
two workflows agree about what a missing property means.

The two stated limitations are enforced, not fixed
--------------------------------------------------

The specification records two things the vendor's DSL does not do: quote-level
discount properties are not addressable, and arithmetic inside an aggregate is not
supported. Both raise :class:`UnsupportedRuleForm` carrying the vendor's own
sentence, so the editor shows the limit instead of saving a rule that can never fire.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, NamedTuple

from dsr.quoting_proposals import quote_guardrail_vocabulary as vocab


def utcnow() -> datetime:
    """The clock the engine uses when a caller does not supply one.

    Timezone-aware, so a stored timestamp and an audit row agree about what "now"
    meant. A test supplies its own clock and never calls this.
    """
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class GuardrailRefusal(ValueError):
    """A guardrail refused the input.

    Every refusal carries a published reason code and a status, so the HTTP layer
    needs no table saying which refusal is which. Raised before any row is written,
    so a refusal never leaves a half-saved rule behind.
    """

    code = vocab.REASON_RULE_INVALID
    status = 422

    def __init__(
        self,
        detail: str,
        *,
        errors: Sequence[Any] | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.errors = list(errors or [])
        if code:
            self.code = code

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "detail": self.detail,
            "status": self.status,
            "reason": vocab.REASON_TEXTS.get(self.code, self.detail),
            "errors": self.errors,
        }


class RuleSyntaxError(GuardrailRefusal):
    """The definition is not a sentence the grammar reads."""

    code = vocab.REASON_RULE_UNPARSEABLE


class UnsupportedRuleForm(GuardrailRefusal):
    """The definition uses a form the researched DSL states it does not support."""

    code = vocab.REASON_UNSUPPORTED_FORM


class InvalidRule(GuardrailRefusal):
    """A rule field is missing or outside the published vocabulary."""

    code = vocab.REASON_RULE_INVALID


class RuleNotFound(GuardrailRefusal):
    """No rule with that id."""

    code = vocab.REASON_RULE_NOT_FOUND
    status = 404

    def __init__(self, detail: str, *, record_id: str | None = None) -> None:
        super().__init__(detail)
        self.record_id = record_id


class PublishBlocked(GuardrailRefusal):
    """A Block publish rule is violated, so the quote must not reach the buyer."""

    code = vocab.REASON_BLOCKED
    status = 409

    def __init__(self, detail: str, *, violations: Sequence[Any] | None = None) -> None:
        super().__init__(detail)
        self.violations = list(violations or [])


# --------------------------------------------------------------------------- #
# The tokeniser
# --------------------------------------------------------------------------- #

#: Arithmetic inside an aggregate, in any spacing. Matched before tokenising so the
#: refusal carries the vendor's limitation rather than "unexpected character '*'".
_ARITHMETIC_RE = re.compile(r"\b(?:" + "|".join(vocab.AGGREGATES) + r")\s*\(\s*\[[^\]]*\]\s*[-+*/]")

_TOKEN_RE = re.compile(
    r"""
      (?P<space>\s+)
    | (?P<property>\[[^\]]*\])
    | (?P<string>"(?:[^"\\]|\\.)*")
    | (?P<number>-?\d+(?:\.\d+)?)
    | (?P<operator>>=|<=|!=|<>|==|=|>|<)
    | (?P<paren>[()])
    | (?P<comma>,)
    | (?P<word>[A-Za-z_][A-Za-z0-9_]*)
    """,
    re.VERBOSE,
)


class _Token(NamedTuple):
    kind: str
    value: str
    position: int


def _tokenise(text: str) -> list[_Token]:
    """Split a definition into tokens, or refuse it."""
    tokens: list[_Token] = []
    position = 0
    length = len(text)
    while position < length:
        match = _TOKEN_RE.match(text, position)
        if match is None:
            raise RuleSyntaxError(
                f"Could not read {text[position : position + 24]!r} in the rule "
                f"definition. Check the brackets, the operators and the quoting."
            )
        position = match.end()
        kind = match.lastgroup
        if kind != "space":
            tokens.append(_Token(str(kind), match.group(), match.start()))
    if not tokens:
        raise RuleSyntaxError("The rule definition is empty.")
    return tokens


# --------------------------------------------------------------------------- #
# The parser
# --------------------------------------------------------------------------- #


class _Parser:
    def __init__(self, tokens: Sequence[_Token]) -> None:
        self.tokens = list(tokens)
        self.index = 0

    # -- cursor ------------------------------------------------------------- #

    def at_end(self) -> bool:
        return self.index >= len(self.tokens)

    def peek(self) -> _Token | None:
        return None if self.at_end() else self.tokens[self.index]

    def take(self) -> _Token:
        token = self.peek()
        if token is None:
            raise RuleSyntaxError("The rule definition ended before the sentence did.")
        self.index += 1
        return token

    def expect_kind(self, kind: str, what: str) -> _Token:
        token = self.take()
        if token.kind != kind:
            raise RuleSyntaxError(f"Expected {what} but found {token.value!r}.")
        return token

    def expect_word(self, word: str) -> None:
        token = self.expect_kind("word", f"{word!r}")
        if token.value.upper() != word:
            raise RuleSyntaxError(f"Expected {word!r} but found {token.value!r}.")

    # -- grammar ------------------------------------------------------------ #

    def parse(self) -> dict[str, Any]:
        first = self.take()
        if first.kind == "word" and first.value.upper() in vocab.AGGREGATES:
            expression = self.parse_aggregate(first.value.upper())
        elif first.kind == "word" and first.value.upper() in vocab.QUANTIFIERS:
            expression = self.parse_quantifier(first.value.upper())
        elif first.kind == "property":
            expression = self.parse_property_comparison(first)
        else:
            raise RuleSyntaxError(
                "A rule starts with an aggregate function (SUM, MIN, MAX, AVG, "
                "COUNT), a quantifier (SOLD_TOGETHER, INCOMPATIBLE), or a property "
                f"reference such as [quote.hs_quote_amount]. Found {first.value!r}."
            )
        if not self.at_end():
            raise RuleSyntaxError(
                f"Unexpected trailing token {self.peek().value!r} in the rule definition."
            )
        return expression

    def parse_property(self, token: _Token) -> dict[str, Any]:
        """One ``[scope.name]`` or ``[name]`` reference, with the scope resolved later."""
        inner = token.value[1:-1].strip()
        if not inner:
            raise RuleSyntaxError("A property reference is empty: [].")
        if "." in inner:
            scope, _, name = inner.partition(".")
            scope = scope.strip()
            if scope not in vocab.SCOPES:
                raise UnsupportedRuleForm(
                    f"[{inner}] addresses the scope {scope!r}, which is not one of "
                    f"{', '.join(vocab.SCOPES)}.",
                    code=vocab.REASON_UNKNOWN_SCOPE,
                )
        else:
            scope, name = None, inner
        name = name.strip()
        if not name:
            raise RuleSyntaxError(f"The property reference [{inner}] names no property.")
        if scope == vocab.SCOPE_QUOTE and vocab.QUOTE_DISCOUNT_FRAGMENT in name.lower():
            raise UnsupportedRuleForm(
                f"[{inner}] reads a quote-level discount property. The specification's "
                f'limitation is: "{vocab.LIMITATION_QUOTE_DISCOUNT}".',
                code=vocab.REASON_QUOTE_DISCOUNT_UNAVAILABLE,
            )
        return {"scope": scope, "name": name, "raw": inner}

    def parse_scope(self, what: str) -> str:
        token = self.expect_kind("word", f"a scope for the {what}")
        scope = token.value
        if scope not in vocab.SCOPES:
            raise UnsupportedRuleForm(
                f"{what} reads the scope {scope!r}, which is not one of {', '.join(vocab.SCOPES)}.",
                code=vocab.REASON_UNKNOWN_SCOPE,
            )
        return scope

    def parse_value(self) -> Any:
        token = self.take()
        if token.kind == "string":
            try:
                return json.loads(token.value)
            except json.JSONDecodeError as exc:  # pragma: no cover - regex keeps this closed
                raise RuleSyntaxError(
                    f"The quoted value {token.value} is malformed: {exc}"
                ) from exc
        if token.kind == "number":
            return float(token.value) if "." in token.value else int(token.value)
        if token.kind == "word":
            return token.value
        raise RuleSyntaxError(f"Expected a value but found {token.value!r}.")

    def parse_comparator(self) -> str:
        token = self.expect_kind("operator", "a comparison operator")
        return vocab.COMPARISON_ALIASES.get(token.value, token.value)

    def parse_condition(self, default_scope: str) -> dict[str, Any]:
        property_token = self.expect_kind("property", "a property reference after WHERE")
        prop = self.parse_property(property_token)
        if prop["scope"] is None:
            prop = {**prop, "scope": default_scope}
        following = self.peek()
        if following is not None and following.kind == "word":
            word = following.value.upper()
            not_in = word == "NOT"
            if word in ("IN", "NOT"):
                self.take()
                if not_in:
                    self.expect_word("IN")
                self.expect_kind("paren", "'(' after IN")
                values = [self.parse_value()]
                while self.peek() is not None and self.peek().kind == "comma":
                    self.take()
                    values.append(self.parse_value())
                self.expect_kind("paren", "')' closing the value list")
                return {
                    "property": prop,
                    "operator": "NOT IN" if not_in else "IN",
                    "values": values,
                }
        return {
            "property": prop,
            "operator": self.parse_comparator(),
            "value": self.parse_value(),
        }

    def maybe_where(self, scope: str) -> dict[str, Any] | None:
        following = self.peek()
        if (
            following is not None
            and following.kind == "word"
            and following.value.upper() == "WHERE"
        ):
            self.take()
            return self.parse_condition(scope)
        return None

    def parse_aggregate(self, function: str) -> dict[str, Any]:
        self.expect_kind("paren", f"'(' after {function}")
        property_token = self.expect_kind("property", "a property reference inside the aggregate")
        prop = self.parse_property(property_token)
        self.expect_kind("paren", f"')' closing {function}")
        self.expect_word("FROM")
        scope = self.parse_scope(function)
        condition = self.maybe_where(scope)
        comparator = self.parse_comparator()
        value = self.parse_value()
        return {
            "kind": "aggregate",
            "function": function,
            "property": prop,
            "scope": scope,
            "where": condition,
            "comparator": comparator,
            "value": value,
        }

    def parse_quantifier(self, quantifier: str) -> dict[str, Any]:
        self.expect_word("FROM")
        scope = self.parse_scope(quantifier)
        condition = self.maybe_where(scope)
        return {"kind": "quantifier", "quantifier": quantifier, "scope": scope, "where": condition}

    def parse_property_comparison(self, first: _Token) -> dict[str, Any]:
        prop = self.parse_property(first)
        if prop["scope"] is None:
            raise RuleSyntaxError(
                f"[{prop['name']}] names no scope. A top-level property comparison "
                "must be scoped, for example [quote.hs_quote_amount] > 100000."
            )
        return {
            "kind": "property",
            "property": prop,
            "comparator": self.parse_comparator(),
            "value": self.parse_value(),
        }


def parse(definition: str) -> dict[str, Any]:
    """Parse a rule definition into an expression tree, or raise a refusal.

    The returned tree is plain data so the engine can store only the author's text
    and still re-evaluate deterministically.
    """
    if not isinstance(definition, str) or not definition.strip():
        raise RuleSyntaxError("The rule definition is empty.")
    text = definition.strip()
    if _ARITHMETIC_RE.search(text):
        raise UnsupportedRuleForm(
            "The rule does arithmetic inside an aggregate function. The "
            f'specification\'s limitation is: "{vocab.LIMITATION_ARITHMETIC}".',
            code=vocab.REASON_ARITHMETIC_UNSUPPORTED,
        )
    parsed = _Parser(_tokenise(text)).parse()
    parsed["source"] = text
    parsed["normalised"] = normalise(parsed)
    return parsed


def _render_property(prop: Mapping[str, Any]) -> str:
    if prop.get("scope"):
        return f"[{prop['scope']}.{prop['name']}]"
    return f"[{prop['name']}]"


def _render_value(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    return str(value)


def _render_condition(condition: Mapping[str, Any]) -> str:
    prop = _render_property(condition["property"])
    if condition["operator"] in vocab.SET_OPERATORS:
        values = ", ".join(_render_value(one) for one in condition["values"])
        return f"{prop} {condition['operator']} ({values})"
    return f"{prop} {condition['operator']} {_render_value(condition['value'])}"


def normalise(parsed: Mapping[str, Any]) -> str:
    """Render a parsed expression back to one canonical sentence.

    Stored beside the author's text so a client can show what the engine actually
    read. The two disagreeing is a defect, and having both makes it findable.
    """
    kind = parsed["kind"]
    if kind == "aggregate":
        text = (
            f"{parsed['function']}({_render_property(parsed['property'])}) FROM {parsed['scope']}"
        )
        if parsed.get("where"):
            text += f" WHERE {_render_condition(parsed['where'])}"
        return f"{text} {parsed['comparator']} {_render_value(parsed['value'])}"
    if kind == "quantifier":
        text = f"{parsed['quantifier']} FROM {parsed['scope']}"
        if parsed.get("where"):
            text += f" WHERE {_render_condition(parsed['where'])}"
        return text
    return (
        f"{_render_property(parsed['property'])} {parsed['comparator']} "
        f"{_render_value(parsed['value'])}"
    )


# --------------------------------------------------------------------------- #
# Reading a record
# --------------------------------------------------------------------------- #


def read_property(record: Mapping[str, Any] | None, path: str) -> Any:
    """Read one property, resolving a dotted path.

    The DSL addresses `[line_item.product.sku]` as readily as `[line_item.sku]`
    because payloads are open JSON, so a dotted name is how a nested value is
    reached without a schema.
    """
    current: Any = record
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return None
    return current


def _as_number(value: Any) -> float | None:
    """A value is comparable as a number only if it really is one.

    A boolean is refused even though ``bool`` is a subclass of ``int``, because a
    flag compared against 100000 is a caller error and not a comparison.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _values_equal(left: Any, right: Any) -> bool:
    left_number, right_number = _as_number(left), _as_number(right)
    if left_number is not None and right_number is not None:
        return left_number == right_number
    return str(left).strip() == str(right).strip()


def _compare(left: Any, operator: str, right: Any) -> bool:
    if operator == "=":
        return _values_equal(left, right)
    if operator == "!=":
        return not _values_equal(left, right)
    left_number, right_number = _as_number(left), _as_number(right)
    if left_number is not None and right_number is not None:
        left_value, right_value = left_number, right_number
    else:
        left_value, right_value = str(left), str(right)
    if operator == ">":
        return left_value > right_value
    if operator == ">=":
        return left_value >= right_value
    if operator == "<":
        return left_value < right_value
    if operator == "<=":
        return left_value <= right_value
    raise RuleSyntaxError(f"The comparison operator {operator!r} is not supported.")


# --------------------------------------------------------------------------- #
# The evaluator
# --------------------------------------------------------------------------- #


def _scope_records(context: Mapping[str, Any], scope: str) -> list[Mapping[str, Any]]:
    """Every record a scope holds, normalised to a list.

    A single record scope arrives as a mapping; a collection scope as a list. Both
    become a list here so the aggregate and quantifier paths have one shape to walk.
    """
    value = context.get(scope)
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [one for one in value if isinstance(one, Mapping)]
    return []


def _condition_matches(
    context: Mapping[str, Any],
    record: Mapping[str, Any],
    condition: Mapping[str, Any],
) -> bool | None:
    """Whether one record satisfies a WHERE condition.

    ``None`` means the record does not carry the property, which is unverifiable
    rather than false. The condition's scope is normally the iterated record's own
    scope; an explicitly different scope is read from the context instead, because
    `[quote.hs_quote_amount]` inside a line-item WHERE reads one record, not each line.
    """
    prop = condition["property"]
    scope = prop.get("scope")
    default_scope = condition.get("_default_scope")
    if scope is None or scope == default_scope:
        observed = read_property(record, prop["name"])
    else:
        candidates = _scope_records(context, scope)
        if len(candidates) == 1:
            observed = read_property(candidates[0], prop["name"])
        else:
            observed = next(
                (
                    value
                    for candidate in candidates
                    if (value := read_property(candidate, prop["name"])) is not None
                ),
                None,
            )
    if observed is None:
        return None
    if condition["operator"] in vocab.SET_OPERATORS:
        present = any(_values_equal(observed, one) for one in condition["values"])
        return present if condition["operator"] == "IN" else not present
    return _compare(observed, condition["operator"], condition["value"])


def _matching_records(
    context: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    condition: Mapping[str, Any] | None,
    default_scope: str,
) -> tuple[list[Mapping[str, Any]], bool]:
    if condition is None:
        return list(records), False
    scoped = {**condition, "_default_scope": default_scope}
    matched: list[Mapping[str, Any]] = []
    unverifiable = False
    for record in records:
        verdict = _condition_matches(context, record, scoped)
        if verdict is None:
            unverifiable = True
        elif verdict:
            matched.append(record)
    return matched, unverifiable


def _result(
    verdict: bool,
    *,
    reason_code: str,
    reason: str,
    observed: Any = None,
    threshold: Any = None,
    count: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "reason_code": reason_code,
        "reason": reason,
        "observed": observed,
        "threshold": threshold,
        "count": count,
        **(extra or {}),
    }


def evaluate_expression(parsed: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate one parsed expression against a quote context.

    Returns ``violation`` as a boolean plus the reason code, the observed value and
    the threshold, so a page can show a seller what the rule read rather than only
    that it fired. ``unverifiable`` is set when a property the rule needs is absent.
    """
    kind = parsed["kind"]
    if kind == "aggregate":
        return _evaluate_aggregate(parsed, context)
    if kind == "quantifier":
        return _evaluate_quantifier(parsed, context)
    return _evaluate_property(parsed, context)


def _evaluate_property(parsed: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    prop = parsed["property"]
    scope = prop["scope"]
    records = _scope_records(context, scope)
    if not records:
        return _result(
            False,
            reason_code=vocab.REASON_UNVERIFIABLE,
            reason=(
                f"The quote has no {scope} record for {_render_property(prop)} to read, "
                "so the rule could not be checked."
            ),
            threshold=parsed["value"],
            unverifiable=True,
        )
    observed = None
    for record in records:
        candidate = read_property(record, prop["name"])
        if candidate is not None:
            observed = candidate
            break
    if observed is None:
        return _result(
            False,
            reason_code=vocab.REASON_MISSING_PROPERTY,
            reason=(
                f"The {scope} record does not carry {prop['name']}, so the rule could "
                "not be checked. An unanswered question is not a violation."
            ),
            threshold=parsed["value"],
            unverifiable=True,
        )
    holds = _compare(observed, parsed["comparator"], parsed["value"])
    return _result(
        holds,
        reason_code=vocab.REASON_SATISFIED if not holds else vocab.REASON_WARNING,
        reason=(
            f"{_render_property(prop)} is {observed!r}, and the rule requires "
            f"{parsed['comparator']} {parsed['value']!r}."
        ),
        observed=observed,
        threshold=parsed["value"],
    )


def _evaluate_aggregate(parsed: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    function = parsed["function"]
    scope = parsed["scope"]
    records = _scope_records(context, scope)
    matched, unverifiable = _matching_records(context, records, parsed.get("where"), scope)
    if function == "COUNT":
        count = len(matched)
        holds = _compare(count, parsed["comparator"], parsed["value"])
        return _result(
            holds,
            reason_code=vocab.REASON_SATISFIED if not holds else vocab.REASON_WARNING,
            reason=(
                f"COUNT over {scope} is {count}, and the rule requires "
                f"{parsed['comparator']} {parsed['value']!r}."
            ),
            observed=count,
            threshold=parsed["value"],
            count=count,
            unverifiable=unverifiable and not matched,
        )
    numbers: list[float] = []
    for record in matched:
        number = _as_number(read_property(record, parsed["property"]["name"]))
        if number is not None:
            numbers.append(number)
    if not numbers:
        return _result(
            False,
            reason_code=vocab.REASON_UNVERIFIABLE,
            reason=(
                f"No {scope} record carried a numeric "
                f"{parsed['property']['name']} for {function}, so the rule could not "
                "be checked."
            ),
            threshold=parsed["value"],
            count=len(matched),
            unverifiable=True,
        )
    if function == "SUM":
        observed: float = sum(numbers)
    elif function == "MIN":
        observed = min(numbers)
    elif function == "MAX":
        observed = max(numbers)
    else:
        observed = sum(numbers) / len(numbers)
    holds = _compare(observed, parsed["comparator"], parsed["value"])
    return _result(
        holds,
        reason_code=vocab.REASON_SATISFIED if not holds else vocab.REASON_WARNING,
        reason=(
            f"{function} of {parsed['property']['name']} over {len(numbers)} {scope} "
            f"record(s) is {observed}, and the rule requires {parsed['comparator']} "
            f"{parsed['value']!r}."
        ),
        observed=observed,
        threshold=parsed["value"],
        count=len(numbers),
    )


def _evaluate_quantifier(parsed: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    quantifier = parsed["quantifier"]
    scope = parsed["scope"]
    records = _scope_records(context, scope)
    condition = parsed.get("where")
    if not records:
        return _result(
            False,
            reason_code=vocab.REASON_UNVERIFIABLE,
            reason=f"The quote has no {scope} records for {quantifier} to read.",
            unverifiable=True,
        )
    if condition and condition["operator"] in vocab.SET_OPERATORS:
        prop = condition["property"]
        wanted = list(condition["values"])
        present = [
            value
            for value in wanted
            if any(_values_equal(read_property(record, prop["name"]), value) for record in records)
        ]
        if quantifier == vocab.QUANTIFIER_SOLD_TOGETHER:
            holds = len(present) == len(wanted)
            reason = (
                f"{len(present)} of {len(wanted)} named {scope} values are present "
                f"({', '.join(_render_value(one) for one in present) or 'none'}). "
                "SOLD_TOGETHER holds only when every named value is present."
            )
        else:
            holds = len(present) >= 2
            reason = (
                f"{len(present)} of {len(wanted)} named {scope} values are present. "
                "INCOMPATIBLE holds when two or more of the named values are mixed."
            )
        return _result(
            holds,
            reason_code=vocab.REASON_SATISFIED if not holds else vocab.REASON_WARNING,
            reason=reason,
            observed=present,
            threshold=wanted,
            count=len(present),
        )
    matched, _ = _matching_records(context, records, condition, scope)
    if quantifier == vocab.QUANTIFIER_SOLD_TOGETHER:
        holds = bool(matched)
        reason = (
            f"{len(matched)} {scope} record(s) satisfy the WHERE condition. "
            "SOLD_TOGETHER holds when at least one is present."
        )
    else:
        prop = condition["property"] if condition else None
        seen = {
            str(read_property(record, prop["name"]))
            for record in matched
            if prop is not None and read_property(record, prop["name"]) is not None
        }
        holds = len(seen) >= 2
        reason = (
            f"The matching {scope} records carry {len(seen)} distinct "
            f"{prop['name'] if prop else 'value'}(s). INCOMPATIBLE holds when two or "
            "more distinct values are mixed."
        )
    return _result(
        holds,
        reason_code=vocab.REASON_SATISFIED if not holds else vocab.REASON_WARNING,
        reason=reason,
        observed=len(matched),
    )


# --------------------------------------------------------------------------- #
# Rules as records
# --------------------------------------------------------------------------- #


def rule_payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """The rule's own fields, whether a raw row or a hydrated record is passed."""
    data = record.get("data")
    return data if isinstance(data, Mapping) else record


def is_enabled(record: Mapping[str, Any]) -> bool:
    """Whether the rule's Status switch is on.

    "Rules can be toggled on/off per rule", and "a disabled rule must not evaluate".
    The status string is authoritative; the boolean is kept beside it for clients that
    read one.
    """
    payload = rule_payload(record)
    if payload.get(vocab.FIELD_STATUS) in vocab.STATUSES:
        return payload[vocab.FIELD_STATUS] == vocab.STATUS_ENABLED
    return bool(payload.get(vocab.FIELD_ENABLED, False))


def evaluate_rule(record: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate one rule record against a quote context.

    A disabled rule is reported as ``skipped`` with its published reason code, so a
    page can show the switch being off rather than an absent rule.
    """
    payload = rule_payload(record)
    definition = str(payload.get(vocab.FIELD_DEFINITION) or "")
    outcome = payload.get(vocab.FIELD_OUTCOME) or vocab.OUTCOME_WARNING
    base: dict[str, Any] = {
        "rule_id": record.get("id") or payload.get("id"),
        "rule_name": payload.get(vocab.FIELD_NAME) or "",
        "outcome": outcome,
        "message": payload.get(vocab.FIELD_MESSAGE) or "",
        "definition": definition,
        "expression": payload.get(vocab.FIELD_NORMALISED) or definition,
    }
    if not is_enabled(record):
        return {
            **base,
            "verdict": vocab.VERDICT_SKIPPED,
            "reason_code": vocab.REASON_RULE_DISABLED,
            "reason": vocab.REASON_TEXTS[vocab.REASON_RULE_DISABLED],
            "violation": False,
            "unverifiable": False,
        }
    try:
        parsed = parse(definition)
    except GuardrailRefusal as exc:
        return {
            **base,
            "verdict": vocab.VERDICT_UNVERIFIABLE,
            "reason_code": exc.code,
            "reason": exc.detail,
            "violation": False,
            "unverifiable": True,
        }
    evaluation = evaluate_expression(parsed, context)
    if evaluation.get("unverifiable"):
        verdict = vocab.VERDICT_UNVERIFIABLE
    elif evaluation["verdict"]:
        verdict = vocab.VERDICT_VIOLATION
    else:
        verdict = vocab.VERDICT_CLEAR
    reason_code = evaluation["reason_code"]
    if verdict == vocab.VERDICT_VIOLATION:
        reason_code = (
            vocab.REASON_BLOCKED if outcome == vocab.OUTCOME_BLOCK else vocab.REASON_WARNING
        )
    return {
        **base,
        "verdict": verdict,
        "reason_code": reason_code,
        "reason": evaluation["reason"]
        if verdict != vocab.VERDICT_VIOLATION
        else (f"{base['message'] or 'This rule is violated.'} ({evaluation['reason']})"),
        "observed": evaluation.get("observed"),
        "threshold": evaluation.get("threshold"),
        "expression": parsed["normalised"],
        "violation": verdict == vocab.VERDICT_VIOLATION,
        "unverifiable": verdict == vocab.VERDICT_UNVERIFIABLE,
    }


def evaluate_rules(
    records: Sequence[Mapping[str, Any]], context: Mapping[str, Any]
) -> dict[str, Any]:
    """Evaluate every enabled rule and split the verdicts by what they cost.

    ``blocked`` is true only when a ``Block publish`` rule is violated. A warning is
    not a block and an unverifiable rule is not a violation, so both leave the quote
    publishable.
    """
    verdicts = [evaluate_rule(record, context) for record in records]
    violations = [one for one in verdicts if one["verdict"] == vocab.VERDICT_VIOLATION]
    blocking = [one for one in violations if one["outcome"] == vocab.OUTCOME_BLOCK]
    warnings = [one for one in violations if one["outcome"] == vocab.OUTCOME_WARNING]
    unverifiable = [one for one in verdicts if one["unverifiable"]]
    skipped = [one for one in verdicts if one["verdict"] == vocab.VERDICT_SKIPPED]
    if blocking:
        reason_code = vocab.REASON_BLOCKED
    elif warnings:
        reason_code = vocab.REASON_WARNING
    else:
        reason_code = vocab.REASON_SATISFIED
    return {
        "verdicts": verdicts,
        "violations": violations,
        "blocking": blocking,
        "warnings": warnings,
        "unverifiable": unverifiable,
        "skipped": skipped,
        "blocked": bool(blocking),
        "publishable": not blocking,
        "reason_code": reason_code,
        "reason": vocab.REASON_TEXTS[reason_code],
    }


def refusal_message(blocking: Sequence[Mapping[str, Any]]) -> str:
    """The sentence a blocked publish carries: the first rule's message, and a count.

    The seller needs the rule's own message, not a generic refusal, and the count so
    they know more than one rule is standing in the way.
    """
    first = blocking[0] if blocking else None
    opening = (
        first.get("message")
        if first and first.get("message")
        else "A quote rule blocks publishing."
    )
    if len(blocking) > 1:
        return f"{opening} ({len(blocking)} blocking rules are violated.)"
    return str(opening)


def describe_grammar() -> dict[str, Any]:
    """The grammar's examples and its limits, served for the editor's error panel."""
    return {
        "examples": list(vocab.DSL_EXAMPLES),
        "limitations": [
            {
                "code": vocab.REASON_QUOTE_DISCOUNT_UNAVAILABLE,
                "text": vocab.LIMITATION_QUOTE_DISCOUNT,
            },
            {
                "code": vocab.REASON_ARITHMETIC_UNSUPPORTED,
                "text": vocab.LIMITATION_ARITHMETIC,
            },
        ],
    }
