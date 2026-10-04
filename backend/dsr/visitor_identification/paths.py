"""Page paths, and the three ways a captured path is matched against one.

The research is precise here and the rule is unusual, so both halves are quoted
in full:

* "Give the page a name and input the URL. You can now select this page when
  using the Pages filter."
* "When you type in the web page URL do not include the domain."

So an intent page is a *path*, not a URL, and a definition that carries a domain
is refused rather than stored. A capture records the path that was requested and
nothing else about the request, so a definition of ``https://acme.example/pricing``
would sit in the Pages list looking configured and never match a visit. That is
the failure :class:`~dsr.visitor_identification.errors.PathCarriesADomain`
exists to stop, at the point of definition.

And the match condition is a closed set of three: "you can select which condition
should be followed: Exact ... Contains ... Starts with".
"""

from __future__ import annotations

import re

from dsr.visitor_identification.errors import InvalidPageDefinition, PathCarriesADomain
from dsr.visitor_identification.vocabulary import MATCH_CONDITIONS, normalise_condition

#: A label that could be a top-level domain. Deliberately narrow: it has to be
#: letters only, so a path segment that merely contains a full stop - ``v1.0``,
#: ``chapter.1`` - is not mistaken for a host.
_TLD = re.compile(r"[A-Za-z]{2,24}")

#: Two or more slashes in a row. Collapsed rather than refused, because a browser
#: normalises them and a capture can carry whatever the request line carried.
_REPEATED_SLASH = re.compile(r"/{2,}")


def looks_like_host(segment: str) -> bool:
    """Does this first path segment look like a domain rather than a directory?

    A host has a full stop followed by an alphabetic label. Anything else is a
    directory name that happens to contain punctuation, and treating it as a
    domain would refuse a page the seller defined correctly.
    """
    if "." not in segment:
        return False
    labels = segment.split(".")
    if any(not label for label in labels):
        # An empty label is not a host: ".example" is a typo, and "example." is a
        # fully qualified name a browser resolves but a seller did not type.
        return False
    if segment.startswith("-") or segment.endswith("."):
        return False
    return bool(_TLD.fullmatch(labels[-1]))


def normalise_path(value: object) -> str:
    """A URL path, or the reason this is not one.

    The rules, each one a reading rather than a sourced rule, so they are named in
    :mod:`dsr.visitor_identification.inferences` too:

    1. A scheme or a protocol-relative prefix means a domain was included.
    2. A first segment shaped like a host means a domain was included.
    3. What is left must begin with a slash. "the URL path without the domain"
       describes an absolute path, and a value with no leading slash is ambiguous
       between a relative path and a host with the scheme left off.
    4. The query string and the fragment are dropped: the research defines a page,
       and neither varies which page was served.
    5. Repeated slashes collapse and a trailing slash is dropped, so
       ``/newsroom/`` and ``/newsroom`` are one page. The root path stays ``/``.

    Case is *not* folded. A URL path is case-sensitive, and the research never
    says otherwise.
    """
    raw = str(value or "").strip()
    if not raw:
        raise InvalidPageDefinition("a page is defined by a URL path; no path was given")

    lowered = raw.lower()
    if lowered.startswith(("http://", "https://", "//")):
        raise PathCarriesADomain(
            f"{raw!r} carries a domain. Enter the path without the domain, "
            "for example /newsroom/converting-the-unconverted-article"
        )

    body = raw.split("#", 1)[0].split("?", 1)[0]
    segment = body[1:].partition("/")[0] if body.startswith("/") else body.partition("/")[0]
    if looks_like_host(segment):
        raise PathCarriesADomain(
            f"{raw!r} carries a domain. Enter the path without the domain, "
            "for example /newsroom/converting-the-unconverted-article"
        )
    if not body:
        raise InvalidPageDefinition(f"{raw!r} has a path with nothing in it")
    if not body.startswith("/"):
        raise InvalidPageDefinition(
            f"{raw!r} does not start with a slash. Enter the path only, "
            "for example /newsroom/converting-the-unconverted-article"
        )

    path = _REPEATED_SLASH.sub("/", body)
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    return path


def matches(condition: object, page_path: object, visit_path: object) -> bool:
    """Does a captured path satisfy an intent page's match condition?

    The three researched conditions, with the vendor's labels:

    * ``Exact`` - the visited path is the defined path.
    * ``Contains`` - the defined path appears anywhere inside the visited path.
    * ``Starts with`` - the visited path begins with the defined path.

    Both sides are normalised first, so the comparison is path against path. A
    visit captured as ``/newsroom/converting-the-unconverted-article?utm_source=x``
    still matches a page defined as ``/newsroom/converting-the-unconverted-article``.
    """
    name = normalise_condition(condition)
    page = normalise_path(page_path)
    visit = normalise_path(visit_path)
    if name == "exact":
        return visit == page
    if name == "starts_with":
        return visit.startswith(page)
    return page in visit


def qualify(path: object, pages: list[dict]) -> list[dict]:
    """The pages of a list that a captured path satisfies, in list order.

    Evaluated rather than stored: a page defined today must tag a visit captured
    last week, and a page removed yesterday must stop tagging anything. Storing
    the answer would make both of those wrong until someone re-ran a sweep.
    """
    return [page for page in pages if matches(page.get("condition"), page.get("path"), path)]


def describe_conditions() -> list[str]:
    """The three condition names, for a caller that renders them."""
    return list(MATCH_CONDITIONS)
