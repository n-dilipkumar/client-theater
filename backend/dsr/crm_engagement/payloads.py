"""Shaping one create per vendor, and finding the id in what comes back.

The research names three different create surfaces and, crucially, three different
answers to "how do I know it worked, and what is the new row's id?":

* **HubSpot** - ``POST /crm/v3/objects/contacts``, body is a ``properties`` object, and
  the id comes back in the body. The data flow says so outright: "response returns the
  new record id (HubSpot ``id``)".
* **Dataverse** - ``POST [Organization URI]/api/data/v9.2/accounts``, a "SOQL-shaped field
  body", and the id comes back in the **``OData-EntityId`` response header** while the
  status is 204 and there is no body to read. The quoted response is
  ``HTTP/1.1 204 No Content`` … ``OData-EntityId: [Organization URI]/api/data/v9.2/accounts(00aa00aa-…)``.
* **Salesforce** - ``POST /services/data/vXX.X/sobjects/{ObjectName}``, ``201`` Created.
  The research does **not** say where the id is; its own gap note admits the Salesforce
  half rests on the status-code reference rather than a ``sobjects`` create page. So the
  id is read when the body carries one and reported absent when it does not.

That third case is why :func:`extract_record_id` can come back empty on a success, and
why the engine treats "accepted, but no id" as a failure needing a human rather than
quietly marking the event synced. The researched step 5 requires an id to be written;
without one the step cannot complete, so the event was not synced.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import quote

from dsr.crm_engagement.vocabulary import (
    CREATE_ENDPOINTS,
    PREFERENCES,
    RECORD_ID_LOCATIONS,
    REDACTED,
    USER_AGENT,
    VENDORS,
)

#: The Dataverse entity id as it appears inside the returned entity URI. Dataverse
#: primary keys are GUIDs, so this is checked rather than assumed: a header carrying
#: something else is reported verbatim rather than mangled into a GUID shape.
_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_PARENTHESISED = re.compile(r"\(([^()]+)\)\s*$")


@dataclass
class CreateRequest:
    """One create, fully shaped: where it goes, what it carries, and what it says."""

    vendor: str
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)
    object_name: str = ""
    preferences: list[str] = field(default_factory=list)

    def to_dict(self, *, redacted: bool = True) -> dict[str, Any]:
        """The request as the Sync log keeps it.

        ``redacted`` replaces the ``Authorization`` value: the connector's token *is* that
        header, and a sync-log row is read by humans and exported through the generic
        records API.
        """
        headers = dict(self.headers)
        if redacted:
            for key in list(headers):
                if key.lower() == "authorization":
                    headers[key] = REDACTED
        return {
            "vendor": self.vendor,
            "method": self.method,
            "url": self.url,
            "headers": headers,
            "body": dict(self.body),
            "object": self.object_name,
            "preferences": list(self.preferences),
        }


def preference_list(connector: Mapping[str, Any]) -> list[str]:
    """The ``Prefer`` tokens this connector sends, normalised and de-duplicated.

    An open list, because the research frames these as the extension point: "Optional
    ``Prefer: return=representation`` / ``respond-async`` style preferences let a
    connector opt into returning created data."
    """
    raw = connector.get("preferences")
    tokens: list[str] = []
    if isinstance(raw, str):
        raw = [raw]
    for entry in raw or []:
        text = str(entry).strip()
        if not text:
            continue
        if text not in tokens:
            tokens.append(text)
    return tokens


def explain_preferences(tokens: list[str]) -> list[dict[str, Any]]:
    """What each configured token is documented to do, and whether it is sourced.

    An unrecognised token is still listed, flagged as unexplained, rather than dropped -
    a connector that sets something this build cannot describe is exactly the thing a
    reviewer should see.
    """
    rows: list[dict[str, Any]] = []
    for token in tokens:
        known = PREFERENCES.get(token)
        if known is None:
            rows.append(
                {
                    "token": token,
                    "known": False,
                    "effect": "Not one this build can describe. Sent as configured.",
                    "sourced": False,
                }
            )
        else:
            rows.append(
                {
                    "token": token,
                    "known": True,
                    "vendors": list(known["vendors"]),
                    "effect": known["effect"],
                    "sourced": known["sourced"] == "True",
                }
            )
    return rows


def build_create(
    vendor: str,
    connector: Mapping[str, Any],
    properties: Mapping[str, Any],
) -> CreateRequest:
    """Shape one create for ``vendor``.

    The three bodies are the researched ones: HubSpot gets the ``properties`` object its
    object API documents, Dataverse and Salesforce get the flat field body. ``{object}``
    in the path is URL-quoted, because a Dataverse entity set name or a Salesforce sObject
    name is caller-supplied and a slash in it would silently address a different route.
    """
    spec = CREATE_ENDPOINTS.get(vendor)
    if spec is None:
        raise KeyError(vendor)
    object_field = str(spec["object_field"])
    object_name = str(connector.get(object_field) or "").strip()
    base = str(connector.get("base_url") or "").strip().rstrip("/")
    path = str(spec["path_template"]).format(
        **{object_field: quote(object_name, safe="")},
    )
    if not base:
        raise ValueError(f"connector for {vendor} has no base_url")
    if not object_name:
        raise ValueError(f"connector for {vendor} has no {object_field}")

    fields = {str(key): value for key, value in properties.items()}
    body = {"properties": fields} if spec["body_style"] == "properties_object" else fields

    tokens = preference_list(connector)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    token = str(connector.get("token") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if tokens:
        # OData's Prefer header is a comma-separated list, in the order configured.
        headers["Prefer"] = ", ".join(tokens)

    return CreateRequest(
        vendor=vendor,
        method="POST",
        url=f"{base}{path}",
        headers=headers,
        body=body,
        object_name=object_name,
        preferences=tokens,
    )


def success_codes(vendor: str) -> tuple[int, ...]:
    """The codes that count as a create succeeding, per vendor.

    Deliberately per-vendor rather than "2xx": the Salesforce reference the research
    quotes puts 204 among the success codes *for DELETE and some PATCH*, so a 204 to a
    create is not this vendor's documented create success, and reading it as one would
    mark an event synced on a response that says nothing was returned.
    """
    spec = CREATE_ENDPOINTS.get(vendor) or {}
    return tuple(int(code) for code in spec.get("success_codes") or ())


def _header(headers: Mapping[str, str] | None, name: str) -> str:
    if not headers:
        return ""
    for key, value in headers.items():
        if str(key).lower() == name.lower():
            return str(value).strip()
    return ""


def id_from_entity_id(uri: str) -> str:
    """The record id inside a Dataverse ``OData-EntityId`` entity URI.

    ``.../api/data/v9.2/accounts(00aa00aa-1111-2222-3333-444444444444)`` yields the
    GUID. The parentheses are read as a pair rather than pattern-matched for a GUID, and
    the result is lower-cased only when it really is a GUID - so a header carrying
    something else is reported exactly as it arrived instead of being reshaped into a
    shape it never had.
    """
    match = _PARENTHESISED.search(uri or "")
    if not match:
        return ""
    candidate = match.group(1).strip()
    if not candidate:
        return ""
    return candidate.lower() if _GUID.match(candidate) else candidate


def extract_record_id(
    vendor: str,
    status: int | None,
    headers: Mapping[str, str] | None,
    body_text: str,
) -> dict[str, Any]:
    """Find the new row's id, and say where it came from.

    Returns ``{"id", "where", "sourced", "searched"}``. ``id`` is empty when none of the
    researched locations carried one, which is a real outcome on Salesforce and a real
    outcome on a Dataverse create whose ``OData-EntityId`` header went missing, and the
    engine treats both as a failure needing a human rather than as a sync.
    """
    searched: list[str] = []
    parsed: Any = None
    if body_text:
        try:
            parsed = json.loads(body_text)
        except json.JSONDecodeError:
            parsed = None
    if not isinstance(parsed, Mapping):
        parsed = {}

    for location in RECORD_ID_LOCATIONS.get(vendor, ()):
        if location["where"] == "header":
            searched.append(f"header:{location['name']}")
            raw = _header(headers, str(location["name"]))
            if not raw:
                continue
            found = id_from_entity_id(raw)
            if found:
                return {
                    "id": found,
                    "where": f"header:{location['name']}",
                    "sourced": bool(location["sourced"]),
                    "basis": location["basis"],
                    "searched": searched,
                }
            searched.append(f"header:{location['name']}=unparseable")
            continue

        path = str(location["path"])
        searched.append(f"body:{path}")
        present, value = _walk(parsed, path)
        if present and value not in (None, ""):
            return {
                "id": str(value),
                "where": f"body:{path}",
                "sourced": bool(location["sourced"]),
                "basis": location["basis"],
                "searched": searched,
            }

    return {"id": "", "where": None, "sourced": False, "basis": None, "searched": searched}


def _walk(payload: Any, path: str) -> tuple[bool, Any]:
    current = payload
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return False, None
    return True, current


def error_detail(body_text: str) -> dict[str, Any]:
    """The vendor's own explanation of a failure, when it sent one.

    This is where the researched ``Prefer: odata.include-annotations`` pays: with the
    preference set, a Dataverse failure body carries annotations, and a sync log that
    keeps them gives a rep the vendor's reason rather than a bare status code. The shape
    read here is the documented OData error envelope - ``{"error": {"code", "message",
    "innererror", "annotations"}}`` - and anything unrecognised is reported as a raw
    sample rather than dropped, because a body this build cannot parse is still the
    evidence.
    """
    detail: dict[str, Any] = {
        "code": None,
        "message": None,
        "annotations": [],
        "raw": (body_text or "")[:512],
    }
    if not body_text:
        return detail
    try:
        parsed = json.loads(body_text)
    except json.JSONDecodeError:
        return detail
    if not isinstance(parsed, Mapping):
        return detail

    # OData wraps in "error"; HubSpot answers with a list of {"message", ...}; Salesforce
    # answers with {"message", "errorCode"}. All three are read, and the first that says
    # something wins.
    error = parsed.get("error")
    if isinstance(error, Mapping):
        detail["code"] = error.get("code")
        detail["message"] = error.get("message")
        for annotation in error.get("annotations") or []:
            if isinstance(annotation, Mapping):
                detail["annotations"].append(
                    {
                        str(key): value
                        for key, value in annotation.items()
                        if key in ("message", "target", "code")
                    }
                )
        if not detail["message"]:
            inner = error.get("innererror")
            if isinstance(inner, Mapping):
                detail["message"] = inner.get("message")
    if not detail["message"] and isinstance(parsed.get("message"), str):
        detail["message"] = parsed["message"]
        detail["code"] = parsed.get("errorCode")
    if not detail["message"]:
        errors = parsed.get("errors")
        if isinstance(errors, (list, tuple)):
            messages = [row for row in errors if isinstance(row, Mapping)]
            if messages:
                detail["code"] = messages[0].get("category") or messages[0].get("errorType")
                detail["message"] = messages[0].get("message")
    if not detail["message"]:
        for row in parsed.get("errors") or []:
            if isinstance(row, Mapping) and row.get("message"):
                detail["code"] = row.get("code")
                detail["message"] = row["message"]
                break
    return detail


def describe_request_shape(vendor: str) -> dict[str, Any]:
    """What a create for ``vendor`` looks like, served as data for the page's reference."""
    spec = CREATE_ENDPOINTS.get(vendor)
    if spec is None:
        return {"vendor": vendor, "known": False}
    return {
        "vendor": vendor,
        "known": True,
        "label": spec["label"],
        "path_template": spec["path_template"],
        "body_style": spec["body_style"],
        "body_example": spec["body_example"],
        "object_field": spec["object_field"],
        "object_label": spec["object_label"],
        "object_example": spec["object_example"],
        "object_note": spec.get("object_note"),
        "success_codes": list(spec["success_codes"]),
        "success_basis": spec["success_basis"],
        "sourced_success": spec["sourced_success"],
        "record_id_locations": [dict(entry) for entry in RECORD_ID_LOCATIONS.get(vendor, ())],
        "sources": list(spec["sources"]),
    }


def known_vendors() -> list[str]:
    return list(VENDORS)
