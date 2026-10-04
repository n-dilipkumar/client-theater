"""Every published value this workflow accepts, in one place.

A page renders its pickers from ``GET /api/wf-031/vocabulary`` rather than from a
list compiled into the component, so a client cannot drift from the server. The
values here are the researched ones, and each carries the sentence it comes from.

Three vocabularies matter:

* :data:`CAPTURE_PARAMETERS` - the public parameters the tracking script reads.
  "We check the IP address, the country, the network, and other publicly
  available parameters to stay GDPR compliant." Three are named. The fourth clause
  is not enumerated, so the list stops at three; see
  :mod:`dsr.visitor_identification.inferences`.
* :data:`MATCH_CONDITIONS` - "you can select which condition should be followed:
  Exact ... Contains ... Starts with". Exactly three, with the vendor's labels.
* :data:`COMPANY_FIELDS` - "The insights provided include the company's name,
  website, address, size, and a list of employees or contacts associated with the
  company". Five fields, in that order.

:data:`PERSONAL_PARAMETER_NAMES` is the fourth vocabulary and it is a refusal
list rather than an acceptance list. "Albacross focuses on exclusively
company-level identification rather than tracking individual users" - so a
capture carrying one of these is turned away by name.
"""

from __future__ import annotations

from typing import Any

from dsr.visitor_identification.errors import UnknownMatchCondition

#: The three public parameters the tracking script reads, in the order the
#: sentence names them. ``path`` is not in this list: a path is not a public
#: parameter, it is the thing being requested.
CAPTURE_PARAMETERS: tuple[str, ...] = ("ip_address", "country", "network")

CAPTURE_PARAMETER_LABELS: dict[str, str] = {
    "ip_address": "IP address",
    "country": "Country",
    "network": "Network",
}

#: The payload keys a capture accepts, beyond the three parameters: the page that
#: was requested, the client id the snippet was installed under, and the moment
#: the request arrived. ``actor`` is the caller, not the visitor.
CAPTURE_FIELDS: tuple[str, ...] = ("path", "client_id", "captured_at", "actor")

#: Every key a capture may carry. A key outside this set is refused, which is what
#: keeps the list closed at the three researched parameters.
ALLOWED_CAPTURE_KEYS: frozenset[str] = frozenset(CAPTURE_PARAMETERS + CAPTURE_FIELDS)

#: Keys whose whole purpose is to name a person. Refused with its own error so the
#: reason is legible from the response, and checked *before* the closed-list rule
#: so "you cannot identify people here" is what the caller reads.
PERSONAL_PARAMETER_NAMES: frozenset[str] = frozenset(
    {
        "cookie",
        "cookies",
        "contact_email",
        "email",
        "first_name",
        "full_name",
        "last_name",
        "person",
        "person_id",
        "user_id",
        "user_agent_id",
        "username",
        "visitor_id",
        "visitor_name",
    }
)

#: The three match conditions, with the vendor's own labels. Published at
#: ``GET /api/wf-031/vocabulary`` so a picker renders the researched words.
MATCH_CONDITIONS: tuple[str, ...] = ("exact", "contains", "starts_with")

MATCH_CONDITION_LABELS: dict[str, str] = {
    "exact": "Exact",
    "contains": "Contains",
    "starts_with": "Starts with",
}

MATCH_CONDITION_NOTES: dict[str, str] = {
    "exact": "The visited path is the defined path, character for character.",
    "contains": "The defined path appears anywhere inside the visited path.",
    "starts_with": "The visited path begins with the defined path.",
}

#: The five company fields the research names, in the order it names them.
COMPANY_FIELDS: tuple[str, ...] = ("name", "website", "address", "size", "contacts")

#: The two keys a contact may carry. ``name`` is the person; ``role`` is the
#: "Role" in the researched auto-engage target-group selector. An email address
#: is not named anywhere in this workflow's research, so it is not stored.
CONTACT_FIELDS: tuple[str, ...] = ("name", "role")

#: The lead-list filters, from "apply the Pages filter to isolate companies that
#: visited those pages; combine with Segment filters, tags, and the ICP", plus the
#: two capture parameters a company record carries and the Pages list itself.
LEAD_FILTERS: tuple[str, ...] = (
    "page",
    "segment",
    "tag",
    "icp",
    "country",
    "size",
)

#: How the lead list is ranked. The research says "a ranked list of in-market
#: companies" and names no sort keys; the reading is recorded in
#: :mod:`dsr.visitor_identification.inferences` and the ordering is a total one,
#: so two runs over the same data return the same list.
RANKING: tuple[str, ...] = ("page_views", "last_visit_at", "company_key")

#: The identified-company collection, and the schema-flexible JSON payload that
#: goes in ``records.data``. Named here so the field map is discoverable from one
#: place rather than from a string repeated in six call sites.
COMPANIES = "identified_company"
VISITS = "company_page_visit"
PAGES = "intent_page"
ICP_PROFILES = "ideal_customer_profile"
INSTALLATIONS = "tracking_installation"

COLLECTIONS: dict[str, str] = {
    "companies": COMPANIES,
    "visits": VISITS,
    "pages": PAGES,
    "icps": ICP_PROFILES,
    "installations": INSTALLATIONS,
}

#: The identification stance, quoted, because it is the rule that shapes this
#: whole package and a reader should not have to go looking for it.
IDENTIFICATION_STANCE = (
    "Albacross focuses on exclusively company-level identification rather than "
    "tracking individual users, ensuring respect for user privacy."
)

#: The two surfaces this workflow hands a company list to, by the names the
#: research gives them. Neither is built here; see
#: :mod:`dsr.visitor_identification.inferences`.
DOWNSTREAM_SURFACES: tuple[dict[str, str], ...] = (
    {
        "surface": "Workflows",
        "scope": "section 17 of the research file",
        "note": (
            "The research points at Webhooks and Workflows for streaming an identified company to "
            "another system. Section 17 of the same file is a separate workflow and has its own "
            "feature; it is not a ticket reference and it is not built here."
        ),
    },
    {
        "surface": "Auto-engage",
        "scope": "campaign builder",
        "note": (
            "Campaigns target a group by Role, Industry and Location, generate a sequence, and "
            "send it. This workflow identifies companies and filters the list a campaign would be "
            "pointed at. No campaign, sequence or send belongs here."
        ),
    },
)


def normalise_condition(value: Any) -> str:
    """The canonical name of a match condition, or a refusal.

    Case and spacing are forgiven because a picker sends what a human read off a
    label: "Starts with" and "starts_with" are the same researched choice. What
    is not forgiven is a fourth value, because the three are the whole
    enumeration.
    """
    text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if text in MATCH_CONDITION_LABELS:
        return text
    # "startswith" and "starts_with" are both how the same label gets typed.
    if text in {"startswith", "start_with", "begins_with"}:
        return "starts_with"
    raise UnknownMatchCondition(
        f"match condition {value!r} is not one of "
        + ", ".join(MATCH_CONDITION_LABELS[condition] for condition in MATCH_CONDITIONS)
    )


def describe() -> dict[str, Any]:
    """Every published value, as data, for ``GET /api/wf-031/vocabulary``."""
    return {
        "identification_stance": IDENTIFICATION_STANCE,
        "company_level_only": True,
        "capture_parameters": [
            {
                "name": name,
                "label": CAPTURE_PARAMETER_LABELS[name],
                "required": False,
                "note": (
                    "At least one of the IP address or the network must be present, because one of "
                    "them is what identifies the company."
                ),
            }
            for name in CAPTURE_PARAMETERS
        ],
        "capture_fields": list(CAPTURE_FIELDS),
        "allowed_capture_keys": sorted(ALLOWED_CAPTURE_KEYS),
        "personal_parameter_names": sorted(PERSONAL_PARAMETER_NAMES),
        "match_conditions": [
            {
                "name": condition,
                "label": MATCH_CONDITION_LABELS[condition],
                "note": MATCH_CONDITION_NOTES[condition],
            }
            for condition in MATCH_CONDITIONS
        ],
        "company_fields": list(COMPANY_FIELDS),
        "contact_fields": list(CONTACT_FIELDS),
        "lead_filters": list(LEAD_FILTERS),
        "ranking": list(RANKING),
        "collections": dict(COLLECTIONS),
        "downstream_surfaces": [dict(entry) for entry in DOWNSTREAM_SURFACES],
    }
