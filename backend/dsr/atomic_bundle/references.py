"""Resolving the two documented reference syntaxes.

The research's data flow is the whole point of this module:

    one request body containing ordered subrequests with ``referenceId`` /
    ``Content-ID`` placeholders → CRM resolves ``$1``/``@{refAccount.id}`` into
    real record URIs as it creates each row

So a reference is *a pointer into the result of an earlier subrequest*, and the
two syntaxes name the same thing differently:

* ``@{referenceId.FieldName}`` - Salesforce. The reference names a subrequest and
  the path names a field of the record that subrequest created.
* ``$1`` - Dataverse. The reference is the ``Content-ID`` of the changeset part,
  which the server assigns by position, starting at 1.

Both resolvers are pure: they take the value to rewrite plus the ids already
resolved, and return a new value. Nothing here writes, and nothing here knows
about the store, so the reference rules can be tested without a database.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from dsr.atomic_bundle.vocabulary import (
    DV_REFERENCE_PATTERN,
    SF_REFERENCE_PATTERN,
)

#: A ``@{ref.path}`` occurrence: which subrequest it points at, and which path
#: inside that subrequest's result.
SFReference = tuple[str, str]

#: A ``$n`` occurrence: which 1-based Content-ID it points at.
DVReference = tuple[int]


def sf_references(value: Any) -> list[SFReference]:
    """Every ``@{ref.path}`` in ``value``, in document order, duplicates included.

    Walks nested objects and arrays because a reference is just a string
    wherever it lands, and a bundle body is arbitrary JSON.
    """
    found: list[SFReference] = []
    _walk(value, lambda text: found.extend(sf_references_in_text(text)))
    return found


def sf_references_in_text(text: str) -> list[SFReference]:
    """Just the string level, for the places that already hold text."""
    return [(match["reference"], match["path"]) for match in SF_REFERENCE_PATTERN.finditer(text)]


def dv_references(value: Any) -> list[DVReference]:
    """Every ``$n`` in ``value``, in document order, duplicates included."""
    found: list[DVReference] = []
    _walk(value, lambda text: found.extend(dv_references_in_text(text)))
    return found


def dv_references_in_text(text: str) -> list[DVReference]:
    return [int(match["index"]) for match in DV_REFERENCE_PATTERN.finditer(text)]


def _walk(value: Any, visit: Any) -> None:
    """Apply ``visit`` to every string in a JSON-shaped value."""
    if isinstance(value, str):
        visit(value)
    elif isinstance(value, Mapping):
        for key, child in value.items():
            visit(key) if isinstance(key, str) else None
            _walk(child, visit)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _walk(child, visit)


# --------------------------------------------------------------------------- #
# Salesforce: @{ref.path}
# --------------------------------------------------------------------------- #


def resolve_sf(
    value: Any,
    resolved: Mapping[str, Any],
    *,
    where: str = "body",
) -> Any:
    """Replace every ``@{ref.path}`` in ``value`` from ``resolved``.

    ``resolved`` maps a subrequest's ``referenceId`` to the record body that
    subrequest produced. A reference to a subrequest that has not produced a
    result yet is a bug in the plan, not a runtime condition, so it raises
    :class:`KeyError` with the offending reference named - a bundle that
    references the future must be refused before it is sent, and
    :mod:`dsr.atomic_bundle.planner` is where that check lives.
    """
    return _rewrite(value, _sf_resolver(resolved, where))


def _sf_resolver(resolved: Mapping[str, Any], where: str) -> Any:
    def resolve(reference: str, path: str) -> Any:
        if reference not in resolved:
            raise KeyError(f"{where} references @{reference}, which has no result yet")
        return _pluck(resolved[reference], path, f"@{reference}.{path}")

    return resolve


def _pluck(record: Any, path: str, label: str) -> Any:
    """Follow a documented field path, including ``name[0]`` index steps.

    The research quotes two shapes it has to support, ``@{NewAccount.BillingAddress.city}``
    and ``@{AccountInfo.recentItems[0].Id}``, so an index step is part of the
    grammar rather than an extension of it.
    """
    current = record
    for segment in _path_segments(path):
        if isinstance(segment, int):
            if not isinstance(current, (list, tuple)) or len(current) <= segment:
                raise KeyError(f"{label} indexes past the end of the response")
            current = current[segment]
            continue
        if isinstance(current, Mapping) and segment in current:
            current = current[segment]
            continue
        # A path into a field the result did not carry resolves to null rather
        # than to a crash: a reference is a claim about a record, and a record
        # that has no such field legitimately has no value for it.
        return None
    return current


def _path_segments(path: str) -> Iterable[str | int]:
    for part in path.split("."):
        index = re.fullmatch(r"([A-Za-z0-9_]+)\[([0-9]+)\]", part)
        if index:
            yield index[1]
            yield int(index[2])
        else:
            yield part


# --------------------------------------------------------------------------- #
# Dataverse: $1
# --------------------------------------------------------------------------- #


def content_id(position: int) -> str:
    """The ``Content-ID`` of the part at 1-based ``position``.

    [sourced] the research numbers them: ``$1``, ``$2`` … So the first step's
    Content-ID is ``1`` and the number is the same as the position in the
    declared order. Zero-based ``position`` here to keep it a list index.
    """
    return str(position + 1)


def content_id_for(reference_id: str, order: Iterable[str]) -> int:
    """The 1-based Content-ID a ``reference_id`` gets, given the declared order."""
    for position, candidate in enumerate(order):
        if candidate == reference_id:
            return position + 1
    raise KeyError(f"{reference_id} is not in the declared order")


def resolve_dv(value: Any, uris: Mapping[int, str]) -> Any:
    """Replace every ``$n`` in ``value`` with the URI of the ``n``-th part.

    ``uris`` maps the 1-based Content-ID to the URI the server returned. A
    reference to a part with no URI is a bug in the plan, so it raises rather
    than leaving ``$1`` in a body that would be sent as-is.
    """
    return _rewrite(value, _dv_resolver(uris))


def _dv_resolver(uris: Mapping[int, str]) -> Any:
    def resolve(index: int) -> str:
        if index not in uris:
            raise KeyError(f"body references ${index}, which has no URI yet")
        return uris[index]

    return resolve


# --------------------------------------------------------------------------- #
# The shared rewriter
# --------------------------------------------------------------------------- #


def _rewrite(value: Any, resolve: Any) -> Any:
    """Rebuild ``value`` with every reference in every string replaced.

    A string that is *entirely* one reference becomes the referenced value, so
    ``"AccountId": "@{refAccount.id}"`` yields the id rather than the string
    ``"001…"`` embedded in text - which is what every documented example needs,
    since ``originatingleadid@odata.bind`` takes a bare URI. A string that only
    *contains* a reference, such as a billing city inside a sentence, is
    substituted in place and stays a string.
    """
    if isinstance(value, str):
        return _rewrite_text(value, resolve)
    if isinstance(value, Mapping):
        return {key: _rewrite(child, resolve) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rewrite(child, resolve) for child in value]
    return value


def _rewrite_text(text: str, resolve: Any) -> Any:
    from dsr.atomic_bundle.vocabulary import DV_REFERENCE_PATTERN, SF_REFERENCE_PATTERN

    exact = SF_REFERENCE_PATTERN.fullmatch(text)
    if exact:
        return resolve(exact["reference"], exact["path"])

    exact = DV_REFERENCE_PATTERN.fullmatch(text)
    if exact:
        return resolve(int(exact["index"]))

    def replace_sf(match: re.Match[str]) -> str:
        return _stringify(resolve(match["reference"], match["path"]))

    def replace_dv(match: re.Match[str]) -> str:
        return _stringify(resolve(int(match["index"])))

    rewritten = SF_REFERENCE_PATTERN.sub(replace_sf, text)
    return DV_REFERENCE_PATTERN.sub(replace_dv, rewritten)


def _stringify(value: Any) -> str:
    """Render a resolved value inside a larger string."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def substitute_dv_uri(text: str, uri: str) -> str:
    """Replace a ``$n`` in a *URL*, where the whole path segment is the reference.

    A changeset part's own ``url`` is where ``$1`` appears as a path, not as a
    JSON value, and the substitution there is textual by construction.
    """
    return DV_REFERENCE_PATTERN.sub(lambda match: uri, text)
