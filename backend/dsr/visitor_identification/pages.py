"""The Pages list: the pages that signal intent, and the condition each matches on.

The flow's steps three and four, in the vendor's own words:

* "Open **Pages** (Account name -> top-right -> **Pages**) and add the pages that
  signal intent, entering a name and the URL **path without the domain** (e.g.
  ``/newsroom/converting-the-unconverted-article``)."
* "Choose the match condition: **Exact**, **Contains**, or **Starts with**."

So an intent page is three fields and no more: a name to show in the Pages filter,
a path, and one of three conditions. Every one of those three is load-bearing.
Without a name the Pages filter is a list of paths. Without a path it matches
every request. Without a condition it has no defined meaning at all.

A page belongs to the tracking snippet's client, because the Pages list is an
account-level list in the flow ("In Albacross, open the dashboard") and the
account here is the client id the snippet was installed under.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dsr.visitor_identification.errors import InvalidPageDefinition
from dsr.visitor_identification.paths import normalise_path
from dsr.visitor_identification.vocabulary import (
    MATCH_CONDITION_LABELS,
    PAGES,
    normalise_condition,
)

#: How long a page name may be. A name is a label in a filter, not prose.
MAX_NAME_LENGTH = 120


@dataclass(frozen=True)
class IntentPage:
    """One page that signals intent, as the Pages list holds it."""

    client_id: str
    name: str
    path: str
    condition: str

    @property
    def condition_label(self) -> str:
        """The vendor's own label for the condition, for a picker."""
        return MATCH_CONDITION_LABELS[self.condition]


def parse_page(payload: dict[str, Any], *, client_id: str = "") -> IntentPage:
    """Read a page definition, or refuse it.

    ``client_id`` on the body wins over the one supplied by the route, because a
    body that names a different client is a body the caller meant to send and a
    silent override would put the page in the wrong account's list.
    """
    body = dict(payload or {})

    name = body.get("name")
    if name is None or isinstance(name, (dict, list, bool)):
        raise InvalidPageDefinition(f"a page needs a name. Got {name!r}.")
    name = str(name).strip()
    if not name:
        raise InvalidPageDefinition("a page needs a name. The Pages filter shows it.")
    if len(name) > MAX_NAME_LENGTH:
        raise InvalidPageDefinition(f"the page name is longer than {MAX_NAME_LENGTH} characters")

    path = normalise_path(body.get("path"))
    condition = normalise_condition(body.get("condition", "exact"))

    client = str(body.get("client_id") or client_id or "").strip()
    if not client:
        raise InvalidPageDefinition(
            "a page belongs to a client id. Send client_id, or define the page under an "
            "installed snippet."
        )

    return IntentPage(client_id=client, name=name, path=path, condition=condition)


def amend(existing: dict[str, Any], payload: dict[str, Any], *, client_id: str) -> dict[str, Any]:
    """A page definition after an edit, checked the same way a new one is.

    Step four is a choice the seller may change, so the condition is editable. An
    edit that carries no recognised field is refused rather than treated as a
    no-op, because a client that thinks it renamed a page and did not is worse
    than an error.
    """
    body = dict(payload or {})
    known = {"name", "path", "condition", "client_id"}
    unknown = sorted(str(key) for key in body if key not in known)
    if unknown:
        raise InvalidPageDefinition(f"{unknown[0]!r} is not a field of a page definition.")
    if not body:
        raise InvalidPageDefinition("nothing to change. Send a name, a path or a condition.")

    merged = {
        "name": existing.get("name"),
        "path": existing.get("path"),
        "condition": existing.get("condition"),
        "client_id": existing.get("client_id") or client_id,
    }
    merged.update(body)
    return {
        "client_id": merged["client_id"],
        "name": merged["name"],
        "path": merged["path"],
        "condition": merged["condition"],
    }


def to_record(page: IntentPage) -> dict[str, Any]:
    """The stored payload for a page definition."""
    return {
        "client_id": page.client_id,
        "name": page.name,
        "path": page.path,
        "condition": page.condition,
        "condition_label": page.condition_label,
    }


def of(record: dict[str, Any]) -> IntentPage:
    """Read a stored page back into an :class:`IntentPage`."""
    data = record.get("data") if isinstance(record, dict) else {}
    data = data or {}
    return IntentPage(
        client_id=str(data.get("client_id") or ""),
        name=str(data.get("name") or ""),
        path=str(data.get("path") or ""),
        condition=str(data.get("condition") or "exact"),
    )


def row(record: dict[str, Any]) -> dict[str, Any]:
    """One page as the Pages list serves it."""
    page = of(record)
    return {
        "id": record.get("id"),
        "revision": record.get("revision"),
        "client_id": page.client_id,
        "name": page.name,
        "path": page.path,
        "condition": page.condition,
        "condition_label": page.condition_label,
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def for_client(store, client_id: str, *, limit: int = 1000) -> list[dict[str, Any]]:
    """Every page defined for a client, oldest first so the list does not shuffle."""
    records = store.find(PAGES, {"client_id": client_id}, limit=limit)
    return sorted(records, key=lambda record: (str(record.get("created_at") or ""), record["id"]))
