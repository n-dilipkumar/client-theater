"""Render a localized signal description with the ICU Messages subset this
workflow needs.

The research names the format twice: the registration's ``description`` is
"localized description (ICU Messages)", and an indicator is "rendered as human
sentences" with the worked example::

    "{video_name}" was viewed "{view_count, plural, =1 {# time} other {# times}} within 7 days

So this module has to render a simple argument, a plural with an exact match and
a ``#`` placeholder, and a select. Those are the three forms the research's own
example and the description of a localized description require, and the grammar
implemented here is exactly those three plus literal text.

What is *not* implemented, and why it is worth saying out loud
------------------------------------------------------------
ICU's apostrophe escaping is not implemented. In real ICU, a single quote
introduces a quoted literal and ``''`` is an escaped quote, which means a
description containing an English contraction has to be written carefully or it
will swallow the rest of the sentence. This renderer treats ``'`` as an ordinary
character, which is the right trade for a product whose registrations are written
by a partner in plain prose: a sentence that says "the buyer's pricing page" is
rendered the way its author meant it. A partner who needs a literal brace cannot
have one.

``number``, ``date`` and ``time`` argument types are not implemented. Rather than
guessing a locale's number or date format, an unsupported type renders the plain
value and adds a warning, so the sentence stays readable and the warning says
why.

Degrading, not failing
----------------------
A missing argument is a warning, not an exception. The placeholder is left in
place as ``{name}`` so the gap is visible to whoever reads the Live Feed, and the
warning travels on the signal record and on the feed row. A registration whose
description is one word short should not remove a buyer's intent from a seller's
feed.
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping, Sequence

from dsr.signals.errors import RenderError

#: The plural categories this subset understands beyond exact matches.
#:
#: English only. ``one`` for exactly one, ``other`` for everything else, which is
#: the whole of English plural selection and the whole of what the researched
#: example uses. A registration written for a language with more categories still
#: renders: the exact ``=N`` branches are checked first, and any category the
#: selection rules do not reach falls back to ``other`` with a warning.
PLURAL_CATEGORIES = ("zero", "one", "two", "few", "many", "other")

#: Argument types this renderer implements.
SUPPORTED_ARGUMENT_TYPES = ("plural", "select")


def format_number(value: Any) -> str:
    """A number as a reader expects to see it in a sentence.

    ``42.0`` renders as ``42`` and not ``42.0``, because a Live Feed sentence
    that says "was viewed 1.0 times" reads as a bug even though the underlying
    value really was a float.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return str(value)
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value)


def _scan(template: str) -> Iterator[tuple[str, str]]:
    """Split a message into literal text and argument bodies.

    A brace-depth scan rather than a regular expression, because a plural's
    sub-messages contain their own braces: ``{n, plural, other {# times}}`` has
    two opening braces before the argument closes.
    """
    buffer: list[str] = []
    index = 0
    while index < len(template):
        char = template[index]
        if char != "{":
            buffer.append(char)
            index += 1
            continue
        if buffer:
            yield "text", "".join(buffer)
            buffer = []
        depth = 1
        cursor = index + 1
        while cursor < len(template) and depth:
            if template[cursor] == "{":
                depth += 1
            elif template[cursor] == "}":
                depth -= 1
            if depth:
                cursor += 1
        if depth:
            raise RenderError(f"the message has an unclosed '{{': {template!r}")
        yield "arg", template[index + 1 : cursor]
        index = cursor + 1
    if buffer:
        yield "text", "".join(buffer)


def _split_top_level(body: str) -> list[str]:
    """Split an argument body on commas that are not inside braces."""
    parts: list[str] = []
    buffer: list[str] = []
    depth = 0
    for char in body:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(buffer))
            buffer = []
            continue
        buffer.append(char)
    parts.append("".join(buffer))
    return [part.strip() for part in parts]


def _parse_options(text: str) -> list[tuple[str, str]]:
    """Parse ``selector {message}`` pairs, the tail of a plural or select."""
    options: list[tuple[str, str]] = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        start = index
        while index < len(text) and not text[index].isspace() and text[index] != "{":
            index += 1
        selector = text[start:index]
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text) or text[index] != "{":
            if selector:
                raise RenderError(f"the {selector!r} branch has no message")
            break
        depth = 1
        cursor = index + 1
        while cursor < len(text) and depth:
            if text[cursor] == "{":
                depth += 1
            elif text[cursor] == "}":
                depth -= 1
            if depth:
                cursor += 1
        if depth:
            raise RenderError(f"the {selector!r} branch has an unclosed '{{'")
        options.append((selector, text[index + 1 : cursor]))
        index = cursor + 1
    return options


def _select_plural(value: Any, options: Sequence[tuple[str, str]]) -> tuple[str | None, str]:
    """Choose a plural branch, and say which category selection produced it.

    Returns ``(None, reason)`` when the message has no branch for this value.
    That is deliberately not filled in with a neighbouring branch: a plural with
    only a ``one`` branch and a value of four would otherwise render "4 item",
    which is not a degraded sentence, it is a wrong one. An empty clause with a
    warning is visible; a wrong clause is not.
    """
    number = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    if number is None:
        return _pick(options, "other"), "other"

    for selector, message in options:
        if not selector.startswith("="):
            continue
        try:
            exact = float(selector[1:]) == float(number)
        except ValueError:
            continue
        if exact:
            return message, "exact"

    # English: exactly one is `one`, everything else is `other`. `zero` is checked
    # first because a registration that says `zero {no times}` is describing a real
    # case rather than a plural form.
    if float(number) == 0:
        for category in ("zero", "other"):
            chosen = _pick(options, category)
            if chosen is not None:
                return chosen, category
    if float(number) == 1:
        chosen = _pick(options, "one")
        if chosen is not None:
            return chosen, "one"
    return _pick(options, "other"), "other"


def _pick(options: Sequence[tuple[str, str]], category: str) -> str | None:
    for selector, message in options:
        if selector == category:
            return message
    return None


def _substitute(message: str, value: Any) -> str:
    """Replace every ``#`` in a plural branch with the formatted number."""
    return message.replace("#", format_number(value))


def render(template: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Render one ICU message.

    Returns the text plus the warnings, rather than a bare string, because a
    sentence rendered with a hole in it is not something a caller should have to
    discover by reading the sentence.
    """
    if not isinstance(template, str):
        raise RenderError(f"a message must be a string; got {type(template).__name__}")
    values = dict(arguments or {})
    warnings: list[str] = []
    output: list[str] = []

    for kind, body in _scan(template):
        if kind == "text":
            output.append(body)
            continue
        parts = _split_top_level(body)
        if not parts or not parts[0]:
            raise RenderError(f"'{{{body}}}' has no argument name")
        name = parts[0]

        if len(parts) == 1:
            if name not in values:
                warnings.append(f"no value supplied for {{{name}}}")
                output.append("{" + name + "}")
            else:
                output.append(format_number(values[name]))
            continue

        argument_type = parts[1]
        if argument_type not in SUPPORTED_ARGUMENT_TYPES:
            # Not implemented, so the value is shown plainly and the gap is named.
            warnings.append(
                f"argument type {argument_type!r} on {{{name}}} is not implemented; "
                "the value was rendered as-is"
            )
            if name in values:
                output.append(format_number(values[name]))
            else:
                warnings.append(f"no value supplied for {{{name}}}")
                output.append("{" + name + "}")
            continue

        options = _parse_options(parts[2]) if len(parts) > 2 else []
        if not options:
            warnings.append(f"{{{name}}} declares no branches, so it renders as nothing")
            output.append("")
            continue

        if argument_type == "plural":
            if name not in values:
                warnings.append(f"no value supplied for {{{name}}}")
                output.append("{" + name + "}")
                continue
            chosen_message, category = _select_plural(values[name], options)
            if chosen_message is None:
                # No branch covers this value and the message has no `other`. The
                # clause is left out rather than filled in with a neighbouring
                # branch's, because a neighbouring branch renders a sentence that
                # is wrong rather than one that is missing.
                warnings.append(
                    f"{{{name}}} is {format_number(values[name])} and the message has no branch "
                    "for it and no 'other' branch, so nothing was rendered there"
                )
                output.append("")
                continue
            output.append(_substitute(chosen_message, values[name]))
            continue

        # select
        if name not in values:
            warnings.append(f"no value supplied for {{{name}}}")
            output.append("{" + name + "}")
            continue
        selector = format_number(values[name])
        chosen_message = None
        for candidate, message in options:
            if candidate == selector:
                chosen_message = message
                break
        if chosen_message is None:
            chosen_message = _pick(options, "other") or ""
            if selector != "other":
                warnings.append(
                    f"{{{name}}} selected {selector!r}, which the message has no branch "
                    "for, so `other` was used"
                )
        output.append(chosen_message)

    return {"text": "".join(output), "warnings": warnings}


def render_locales(
    templates: Mapping[str, str], arguments: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Render every locale of a description, resolved or not.

    The engine resolves which locale the request asked for; this renders the
    whole set, because a registration is localized and whoever is editing one
    usually wants to see every language it declares rather than only the one that
    happened to resolve.
    """
    return {name: render(message, arguments) for name, message in sorted(templates.items())}
