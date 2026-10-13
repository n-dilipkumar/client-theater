"""The message renderer: Liquid tags, Slack mentions and Slack links.

Three surfaces, and each is a rule rather than a string replace:

* **Liquid tags.** ``{{customer.username}}`` and ``{{customer.channel}}`` are
  rendered against the buyer's person profile. An unset tag renders as a
  *documented fallback* rather than as literal braces, because a message a human
  reads must never show ``{{customer.username}}`` to the reader.
* **Mentions.** ``<@U012AB3CD>`` is the syntax the research quotes. A mention whose
  id does not carry a Slack user-id prefix is refused rather than stored, because
  ``<@dana>`` is text no client renders as a mention.
* **Links.** ``<https://example/pricing|Pricing>`` embeds a labelled link. A token
  that is not a URL and not a mention is refused, because Slack shows an unknown
  angle-bracket token literally and a seller would see it in the notification.

The Slack formatting surface the research links is published as
:data:`~dsr.signal_slack.vocabulary.SLACK_FORMATTING_URL`; this module implements
exactly the two surfaces the issue names and no more.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from dsr.signal_slack.errors import InvalidAutomation
from dsr.signal_slack.vocabulary import (
    MAX_MENTION_ID_LENGTH,
    MENTION_ID_PREFIXES,
    MISSING_TAG_FALLBACK,
    TAG_FALLBACK,
)

#: ``{{ path }}`` with optional inner spaces. Only dotted paths are matched, so a
#: stray single brace is left alone rather than half-consumed.
TAG_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)\s*\}\}")

#: Any ``<...>`` token. The angle brackets are Slack's own delimiter for both a
#: mention and an embedded link.
SYNTAX_RE = re.compile(r"<([^<>]*)>")

#: A Slack user id: one of the prefixes the research's example uses, then
#: alphanumerics. Case is preserved, because an id is an id.
MENTION_ID_RE = re.compile(
    r"[" + "".join(MENTION_ID_PREFIXES) + r"][A-Za-z0-9]{1," + str(MAX_MENTION_ID_LENGTH - 1) + r"}"
)


def _value_of(customer: Mapping[str, Any], path: str) -> str | None:
    """Read ``customer.username`` off a nested mapping, or ``None`` when unset.

    A value that is not a non-empty string counts as unset: a number where a name
    belongs is not a name, and rendering ``42`` into a greeting is worse than
    rendering the fallback.
    """
    node: Any = customer
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    if isinstance(node, str) and node.strip():
        return node.strip()
    return None


def render_tags(template: str, customer: Mapping[str, Any] | None = None) -> tuple[str, list[str]]:
    """Render every tag in ``template``, and report the paths that fell back.

    Returns the rendered text and the list of tag paths that were unset. The list
    is the honest half: a caller can show a seller that a message went out with a
    fallback rather than the buyer's name, which is the difference between a
    message that reads oddly and one that reads as broken.
    """
    profile: Mapping[str, Any] = customer or {}
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        path = match.group(1)
        value = _value_of(profile, path)
        if value is not None:
            return value
        missing.append(path)
        return TAG_FALLBACK.get(path, MISSING_TAG_FALLBACK)

    return TAG_RE.sub(replace, template), missing


def is_mention_id(user_id: Any) -> bool:
    """Whether ``user_id`` names a Slack user."""
    return isinstance(user_id, str) and MENTION_ID_RE.fullmatch(user_id.strip()) is not None


def render_mention(user_id: Any) -> str:
    """``U012AB3CD`` to ``<@U012AB3CD>``, or a refusal naming the rule."""
    if not is_mention_id(user_id):
        raise InvalidAutomation(
            f"{user_id!r} is not a Slack user id. A mention must carry a Slack id - "
            f"{'/'.join(MENTION_ID_PREFIXES)} followed by alphanumerics - so that a client "
            "renders it as a mention rather than as text."
        )
    return f"<@{str(user_id).strip()}>"


def is_link(url: Any) -> bool:
    """Whether ``url`` is an absolute http(s) address Slack can embed."""
    if not isinstance(url, str):
        return False
    text = url.strip()
    return text.startswith("http://") or text.startswith("https://")


def render_link(url: Any, label: Any = None) -> str:
    """A URL and an optional label to the ``<url|label>`` Slack form.

    A bare URL is embedded as ``<url>``; that is the same surface with no label,
    not a second one.
    """
    if not is_link(url):
        raise InvalidAutomation(
            f"{url!r} is not an absolute link. An embedded link must start with http:// or "
            "https://, or Slack shows the angle brackets literally."
        )
    target = str(url).strip()
    if label is None or str(label).strip() == "":
        return f"<{target}>"
    return f"<{target}|{str(label).strip()}>"


def scan_syntax(text: str) -> dict[str, Any]:
    """Find every ``<...>`` token and judge it as a mention or a link.

    Returns the mentions, the links and any token that is neither. The invalid
    list is not an exception here, because this is the classifier: the caller that
    wants a refusal calls :func:`require_syntax`.
    """
    mentions: list[str] = []
    links: list[str] = []
    invalid: list[str] = []
    for token in SYNTAX_RE.findall(text):
        if token.startswith("@"):
            if is_mention_id(token[1:]):
                mentions.append(f"<{token}>")
            else:
                invalid.append(f"<{token}>")
            continue
        head, separator, tail = token.partition("|")
        if is_link(head) and (separator == "" or tail.strip() != ""):
            links.append(f"<{token}>")
            continue
        invalid.append(f"<{token}>")
    return {"mentions": mentions, "links": links, "invalid": invalid}


def require_syntax(text: str) -> dict[str, Any]:
    """Refuse a message that carries a token which is neither a mention nor a link."""
    found = scan_syntax(text)
    if found["invalid"]:
        raise InvalidAutomation(
            "the message carries "
            + ", ".join(found["invalid"])
            + " - not a Slack mention and not a link. A mention is <@U012AB3CD>; a link is "
            "<https://example/path|label>. Slack shows any other angle-bracket token literally."
        )
    return found


def render_message(template: str, customer: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Render one message: tags first, then the Slack syntax it carries.

    Tags are rendered before the syntax scan so a tag whose value happens to look
    like a URL cannot smuggle a token past the check.
    """
    if not isinstance(template, str) or not template.strip():
        raise InvalidAutomation("a Slack block needs a message")
    text, missing = render_tags(template, customer)
    found = require_syntax(text)
    return {"text": text, "missing_tags": missing, **found}


__all__ = [
    "MENTION_ID_RE",
    "SYNTAX_RE",
    "TAG_RE",
    "is_link",
    "is_mention_id",
    "render_link",
    "render_mention",
    "render_message",
    "render_tags",
    "require_syntax",
    "scan_syntax",
]
