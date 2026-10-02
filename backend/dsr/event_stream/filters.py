"""Subscription filters: a small, *declared* JSONPath subset.

Why this exists
---------------
The research is explicit that the two vendors differ here:

* Dock: "Sub-filtering happens in the subscriber." No server-side filter exists.
* Seismic: "app-level webhook URL with JSONPath filter expressions (e.g.
  ``$.data..[?(@.teamSiteId == '1')]``)". Server-side, and documented with an
  example.

So a filter is a *Seismic-sourced capability* attached to a subscription, and a
subscription with no filter is the Dock behaviour: it receives everything it
subscribed to. Both are implemented, and the difference is visible in the
vocabulary payload rather than implied.

The grammar, in full
--------------------
This is a subset, and a subset is only safe if the *edges* are loud. Supported:

===========================  ============================================
``$``                       the root
``.name``  ``['name']``     a child
``..name``                  recursive descent to any ``name``
``..``                      every node at any depth
``[3]``  ``[-1]``           a list index
``[?(@.k == 'v')]``         a predicate over the current node
``['a','b']``  ``[a,b]``    a union of child names
``a|b``                     a top-level alternation of whole expressions
===========================  ============================================

A predicate's ``@`` is the node the expression has *reached*, so its key is read
off that node. ``$.associatedObjects.account[?(@.id == 'acc_1')]`` tests the
account's id; ``$.associatedObjects[?(@.id == 'acc_1')]`` tests whether
``associatedObjects`` has an ``id`` of its own, which it does not. That
distinction is the one a subscriber is most likely to get wrong, so it is
spelled out in :func:`describe`.

Predicates support ``==``, ``!=``, ``<``, ``<=``, ``>``, ``>=`` over scalars,
and ``@.key`` (a relative path) on the left. Anything else - an unsupported
operator, an unbalanced bracket, a function call, a slice, or a comparison
written outside a predicate - is a :func:`FilterError` raised when the filter is
*compiled*.

The two rules that matter more than the grammar
-----------------------------------------------

**A filter that cannot be parsed is refused, never ignored.** :func:`compile_filter`
raises. A filter that parsed to "matches nothing" would be indistinguishable from
"no activity happened", and the operator would spend a day debugging their
warehouse instead of their subscription. This is the single most important
behaviour in the module.

**A filter that parses and matches nothing is a real, quiet result.** It is
reported on the delivery row as ``filter_matched: false`` so the distinction
between "your filter excluded everything" and "the delivery failed" survives
the request, and the subscription's counters still count it as a delivery that
was *not* attempted - so a rep can see a subscription that is subscribed and
receiving nothing.

The ``$.data.`` root
--------------------
Seismic's own example is ``$.data..[?(@.teamSiteId == '1')]``, with the payload
under a ``data`` key. This product's ``webhook-event`` payload has its fields at
the top level - ``occurredAt``, ``associatedObjects`` and the rest - which is
what the researched data flow describes. Rather than publish a grammar that
makes the source's own example match nothing, a filter is evaluated against the
payload **and**, when that matches nothing, against ``{"data": payload}``. So the
researched example works verbatim against a payload whose top level has no
``data`` key, and a filter written for this product's own shape works unchanged.
Recorded as inference ``filter-root-alias``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from dsr.event_stream.errors import FilterError

#: Operators a predicate may use. Anything else is refused at compile time.
OPERATORS: tuple[str, ...] = ("==", "!=", ">=", "<=", ">", "<")

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


# --------------------------------------------------------------------------- #
# The value model
# --------------------------------------------------------------------------- #


def _child(node: Any, name: str) -> list[Any]:
    """Every value of ``node`` addressed by the child key ``name``."""
    if isinstance(node, Mapping):
        if name in node:
            return [node[name]]
        return []
    if isinstance(node, (list, tuple)):
        found = []
        for _index, item in enumerate(node):
            found.extend(_child(item, name))
        return found
    return []


def _index(node: Any, position: int) -> list[Any]:
    if isinstance(node, (list, tuple)):
        try:
            return [node[position]]
        except IndexError:
            return []
    return []


def _deep(node: Any) -> list[Any]:
    """The node and every value nested inside it, document order.

    Backs ``..`` used on its own, as in the researched
    ``$.data..[?(@.teamSiteId == '1')]``, where the descent is followed
    immediately by a predicate rather than by a name.
    """
    found = [node]
    if isinstance(node, Mapping):
        for value in node.values():
            found.extend(_deep(value))
    elif isinstance(node, (list, tuple)):
        for item in node:
            found.extend(_deep(item))
    return found


def _descend(node: Any, name: str) -> list[Any]:
    """Every value of ``name`` at any depth, including at the node itself.

    Both a direct hit and deeper ones are collected: ``$..email`` has to find
    the ``email`` on the payload *and* on the ``user`` object nested inside it,
    which is the whole reason a subscription filter wants recursive descent.
    """
    found: list[Any] = []
    if isinstance(node, Mapping):
        if name in node:
            found.append(node[name])
        for key, value in node.items():
            if key != name:
                found.extend(_descend(value, name))
    elif isinstance(node, (list, tuple)):
        for item in node:
            found.extend(_descend(item, name))
    return found


def _compare(left: Any, operator: str, right: Any) -> bool:
    """One predicate comparison.

    Ordering comparisons between incompatible types (a string against a number)
    are a predicate that simply does not hold, not an exception: a filter is a
    question, and "cannot be ordered" is a legitimate answer of "no". Equality
    is type-loose in one direction only, because the payloads hold JSON values
    and a number that arrived as ``1`` should still match a filter written as
    ``'1'`` when the vendor's example is a quoted string.
    """
    if operator == "==":
        return left == right or str(left) == str(right)
    if operator == "!=":
        return not (left == right or str(left) == str(right))
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    try:
        if operator == ">":
            return left > right  # type: ignore[operator]
        if operator == "<":
            return left < right  # type: ignore[operator]
        if operator == ">=":
            return left >= right  # type: ignore[operator]
        return left <= right  # type: ignore[operator]
    except TypeError:
        return False


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Predicate:
    """``[?(@.key op literal)]`` reduced to what the evaluator needs."""

    key: str
    operator: str
    literal: Any

    def holds(self, node: Any) -> bool:
        values = _child(node, self.key)
        return any(_compare(value, self.operator, self.literal) for value in values)


@dataclass(frozen=True)
class _Union:
    names: tuple[str, ...]


@dataclass(frozen=True)
class _Index:
    position: int


@dataclass(frozen=True)
class _Segment:
    """One step of a path.

    Exactly one of ``name`` / ``union`` / ``index`` / ``predicate`` is set, or
    ``any_depth`` alone for a bare ``..``. The parser only ever produces those
    five shapes, and :func:`_walk` dispatches on them.
    """

    name: str | None = None
    recursive: bool = False
    any_depth: bool = False
    union: "_Union | None" = None
    index: "_Index | None" = None
    predicate: "_Predicate | None" = None


Path = tuple[_Segment, ...]


class _Parser:
    """A recursive-descent parser over the documented subset.

    Hand-written rather than regex-driven because the subset has nesting
    (``[?()]`` contains paths) and because a filter that fails to parse has to
    fail *loudly* with a position, which is the whole point of the module.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.at = 0

    # -- helpers ------------------------------------------------------------ #

    def _fail(self, message: str) -> None:
        raise FilterError(
            f"{message} at position {self.at} in {self.text!r}",
            remediation=(
                "Supported: $.a.b, $..name, $[0], $['a','b'], $[?(@.key == 'value')], "
                "and alternation with |."
            ),
        )

    def _peek(self) -> str:
        return self.text[self.at] if self.at < len(self.text) else ""

    def _skip_ws(self) -> None:
        """Whitespace between the parts of a predicate is insignificant.

        ``[?(@.id == 'x')]`` and ``[?(@.id=='x')]`` are the same expression, and
        an operator is written with spaces by almost everyone.
        """
        while self.at < len(self.text) and self.text[self.at] in " \t":
            self.at += 1

    def _take(self, char: str) -> None:
        if self._peek() != char:
            self._fail(f"expected {char!r}")
        self.at += 1

    def _name(self) -> str:
        match = _NAME.match(self.text, self.at)
        if not match:
            self._fail("expected a field name")
        self.at = match.end()
        return match.group(0)

    def _quoted(self) -> str:
        quote = self._peek()
        if quote not in ("'", '"'):
            self._fail("expected a quoted name")
        self.at += 1
        end = self.text.find(quote, self.at)
        if end == -1:
            self._fail("unterminated quoted name")
        value = self.text[self.at : end]
        self.at = end + 1
        return value

    def _literal(self) -> Any:
        char = self._peek()
        if char in ("'", '"'):
            return self._quoted()
        match = _NUMBER.match(self.text, self.at)
        if match:
            self.at = match.end()
            raw = match.group(0)
            return float(raw) if "." in raw else int(raw)
        for word, value in (("true", True), ("false", False), ("null", None)):
            if self.text.startswith(word, self.at):
                self.at += len(word)
                return value
        self._fail("expected a literal")

    # -- grammar ------------------------------------------------------------ #

    def parse(self) -> Path:
        self._take("$")
        path = self._path(())
        self._skip_ws()
        if self.at != len(self.text):
            # A comparison written without its predicate is the most likely
            # mistake, because it reads like an ordinary expression. Say so,
            # rather than "trailing characters" at a position.
            if any(self.text.startswith(op, self.at) for op in OPERATORS):
                self._fail(
                    "a comparison has to sit inside a predicate - write "
                    "$[?(@.key == 'value')] rather than $.key == 'value'"
                )
            self._fail("trailing characters")
        return path

    def _path(self, path: Path) -> Path:
        while self.at < len(self.text):
            char = self._peek()
            if char == ".":
                self.at += 1
                if self._peek() == ".":
                    self.at += 1
                    # `..` on its own means "every node at any depth" and is
                    # normally followed straight by a bracket, as in the
                    # researched `$.data..[?(@.teamSiteId == '1')]`.
                    if self._peek() == "[":
                        path = path + (_Segment(any_depth=True),)
                    else:
                        path = path + (_Segment(name=self._name(), recursive=True),)
                else:
                    path = path + (_Segment(name=self._name()),)
            elif char == "[":
                path = self._bracket(path)
            else:
                return path
        return path

    def _bracket(self, path: Path) -> Path:
        self._take("[")
        self._skip_ws()
        if self._peek() == "?":
            self.at += 1
            self._take("(")
            self._skip_ws()
            self._take("@")
            self._skip_ws()
            if self._peek() == ".":
                self.at += 1
            self._skip_ws()
            key = self._quoted() if self._peek() in ("'", '"') else self._name()
            self._skip_ws()
            operator = next((op for op in OPERATORS if self.text.startswith(op, self.at)), None)
            if operator is None:
                self._fail("expected one of " + ", ".join(OPERATORS))
            self.at += len(operator)
            self._skip_ws()
            literal = self._literal()
            self._skip_ws()
            self._take(")")
            self._skip_ws()
            self._take("]")
            return path + (_Segment(predicate=_Predicate(key, operator, literal)),)

        names: list[str] = []
        while True:
            self._skip_ws()
            char = self._peek()
            if char in ("'", '"'):
                names.append(self._quoted())
            elif _NAME.match(self.text, self.at):
                names.append(self._name())
            else:
                match = _NUMBER.match(self.text, self.at)
                if not match:
                    self._fail("expected a name, a quoted name, or an index")
                self.at = match.end()
                self._take("]")
                return path + (_Segment(index=_Index(int(match.group(0)))),)
            self._skip_ws()
            if self._peek() == ",":
                self.at += 1
                continue
            self._take("]")
            return path + (_Segment(union=_Union(tuple(names))),)


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def _walk(node: Any, path: Path) -> list[Any]:
    """Every value ``path`` addresses, in document order."""
    if not path:
        return [node]
    segment, rest = path[0], path[1:]
    found: list[Any] = []
    if segment.predicate is not None:
        if segment.predicate.holds(node):
            found.extend(_walk(node, rest))
        return found
    if segment.index is not None:
        for value in _index(node, segment.index.position):
            found.extend(_walk(value, rest))
        return found
    if segment.union is not None:
        for name in segment.union.names:
            for value in _child(node, name):
                found.extend(_walk(value, rest))
        return found
    if segment.any_depth:
        for value in _deep(node):
            found.extend(_walk(value, rest))
        return found
    if segment.name is not None and segment.recursive:
        for value in _descend(node, segment.name):
            found.extend(_walk(value, rest))
        return found
    if segment.name is not None:
        for value in _child(node, segment.name):
            found.extend(_walk(value, rest))
    return found


def evaluate(path: Path, document: Any) -> list[Any]:
    """Every value ``path`` addresses in ``document``."""
    return _walk(document, path)


# --------------------------------------------------------------------------- #
# The public surface
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CompiledFilter:
    """A parsed, ready-to-run subscription filter.

    Frozen because two requests can hold the same compiled filter and neither
    may mutate it, and because ``repr`` on a dataclass gives a test something
    readable to assert on.
    """

    expression: str
    paths: tuple[Path, ...]

    def matches(self, document: Any) -> bool:
        """Whether the filter holds for ``document``.

        Evaluated against the document as-is and, when that finds nothing,
        against ``{"data": document}`` - see the module docstring on the
        ``$.data.`` root. Either match is a match; this is an OR, so widening
        the root can never turn a working filter into a non-matching one.
        """
        if any(_walk(document, path) for path in self.paths):
            return True
        wrapped = {"data": document}
        return any(_walk(wrapped, path) for path in self.paths)

    def to_dict(self) -> dict[str, Any]:
        return {"expression": self.expression, "alternatives": len(self.paths)}


def compile_filter(expression: Any) -> CompiledFilter | None:
    """Parse a subscription filter, or return ``None`` for no filter.

    ``None``, empty, and whitespace-only all mean "no filter", which is the
    researched Dock behaviour: sub-filtering happens in the subscriber, so a
    subscription with no filter receives everything it subscribed to.

    Anything else is parsed or refused. It is never returned as a filter that
    matches nothing.
    """
    if expression is None:
        return None
    if not isinstance(expression, str):
        raise FilterError(
            f"filter must be a string, got {type(expression).__name__}",
            remediation="Send a JSONPath expression, for example $.associatedObjects.account.id == 'acc_1'.",
        )
    text = expression.strip()
    if not text:
        return None
    if len(text) > 512:
        raise FilterError(
            "filter expression is longer than 512 characters",
            remediation="Shorten the expression; a subscription filter addresses one payload, not a query language.",
        )

    paths: list[Path] = []
    for alternative in _split_alternatives(text):
        # Each side of a `|` carries whatever whitespace the author put around
        # it; the split must not make the parser care.
        side = alternative.strip()
        if not side:
            raise FilterError(
                f"empty alternative in {text!r}",
                remediation="Remove the stray | or give each side an expression.",
            )
        paths.append(_Parser(side).parse())
    return CompiledFilter(expression=text, paths=tuple(paths))


def _split_alternatives(text: str) -> list[str]:
    """Split on ``|`` that is not inside a bracket or a quoted string."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    for char in text:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
            current.append(char)
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth < 0:
                raise FilterError(
                    f"unbalanced ']' in {text!r}", remediation="Close every bracket you open."
                )
        elif char == "|" and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    if quote:
        raise FilterError(f"unterminated quoted string in {text!r}", remediation="Close the quote.")
    if depth != 0:
        raise FilterError(
            f"unbalanced '[' in {text!r}", remediation="Close every bracket you open."
        )
    parts.append("".join(current))
    return parts


def matches(expression: str | None, document: Any) -> bool:
    """Compile-and-test in one call, for callers with nothing to cache."""
    compiled = compile_filter(expression)
    return True if compiled is None else compiled.matches(document)


def supports(document: Any, keys: Iterable[str]) -> list[str]:
    """The subset of ``keys`` a document can actually answer.

    Not part of the filter grammar: it exists so a UI can offer a picker of
    filterable fields for a payload it has actually seen, instead of a list of
    fields that were guessed.
    """
    present: list[str] = []
    for key in keys:
        if _walk(document, tuple(_Segment(name=part) for part in key.split(".") if part)):
            present.append(key)
    return present


def describe() -> dict[str, Any]:
    """The grammar, served so a client can validate before it sends.

    A filter editor that knows the supported operators can grey out the rest;
    one that does not will happily send something this package will refuse, and
    the refusal is at subscribe time rather than at typing time.
    """
    return {
        "root": "$",
        "operators": list(OPERATORS),
        "alternation": "|",
        "forms": [
            "$.associatedObjects.account.id",
            "$.associatedObjects.workspace.id",
            "$.associatedObjects.account[?(@.id == 'acc_1')]",
            "$.associatedObjects[?(@.object == 'user')]",
            "$..email",
            "$['associatedObjects']['user']['id']",
            "$.asset.tags[0]",
            "$.asset[?(@.trackingEnabled == true)]",
            "$.associatedObjects..[?(@.teamSiteId == '1')]",
        ],
        "worked_example": (
            "'only Northwind's activity' is "
            "$.associatedObjects.account[?(@.id == 'acc_northwind')] - the predicate sits on "
            "the account object, not on associatedObjects itself, because @.id reads the id of "
            "whatever node the expression has reached."
        ),
        "unsupported": ["slices", "function calls", "wildcards", "negation", "regex match"],
        "max_length": 512,
        "root_alias": (
            "a filter is also evaluated against {'data': payload}, so Seismic's documented "
            "example works verbatim against a payload whose fields are at the top level"
        ),
        "refuses_unparseable": True,
        "no_filter_means": "the subscription receives everything it subscribed to",
    }
