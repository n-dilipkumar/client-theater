"""The researched vocabulary of WF-041, and the header it builds.

Everything in this module is either quoted from the research or is a named
judgement call; the second kind is listed in :mod:`dsr.dedupe.inferences` and
served at ``/api/wf-041/inferences`` so a reviewer can disagree with it by name
rather than by reading a function body.

The sourced parts
-----------------

**The Duplicate Rule Header.** The research quotes Salesforce's page directly:
"Configure options for duplicate rules. Salesforce uses duplicate rules to see
if the record that is being created, updated, or upserted is a duplicate of an
existing record. Duplicate rules are part of Duplicate Management. This header is
available in API version 52.0 and later." Its three fields are ``allowSave``
("allow the user to acknowledge the alert and save the duplicate record"),
``includeRecordDetails`` ("return all fields in the duplicate record") and
``runAsCurrentUser`` ("use the current user's sharing rules"), and "The default
value for all fields is ``false``."

That last sentence is the reason :func:`build_duplicate_rule_header` *omits*
false fields instead of sending them: the researched default is false, so a
header that spelled out ``"allowSave": "false"`` would be sending something the
CRM already assumes.

**The 300.** "``300`` The value returned when an external ID exists in more than
one record. The response body contains the list of matching records." and "If
the external ID matches multiple existing records, then a 300 error is returned,
and no records are created or updated." That is the sourced hard block, and the
phrase "no records are created or updated" is why it overrides the policy rather
than being one of the policy's options.

**The unique index.** "The ``Unique`` attribute prevents the creation of
duplicates." A key carrying that attribute makes a create impossible, whatever
the policy says - which is why ``allow`` cannot force a duplicate through it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from dsr.dedupe.errors import DedupeError

# --------------------------------------------------------------------------- #
# Sourced constants
# --------------------------------------------------------------------------- #

#: "This header is available in API version 52.0 and later."
DUPLICATE_RULE_API_VERSION = 52.0

#: The three fields of Salesforce's ``Sforce-Duplicate-Rule-Header``, in the
#: order the research lists them.
DUPLICATE_RULE_HEADER: tuple[str, ...] = ("allowSave", "includeRecordDetails", "runAsCurrentUser")

#: "The default value for all fields is `false`."
DUPLICATE_RULE_HEADER_DEFAULTS: Mapping[str, bool] = {field: False for field in DUPLICATE_RULE_HEADER}

#: The header's own name, kept beside it so no caller hard-codes the string.
DUPLICATE_RULE_HEADER_NAME = "Sforce-Duplicate-Rule-Header"

#: Salesforce's multi-match status. Sourced from the error-code page.
MULTIPLE_MATCH_STATUS = 300

#: What the CRM's matching rule can answer. Step 3 of the researched user flow:
#: "returns either a clean create, a **duplicate alert with the matching record
#: id**, or a hard block."
CRM_RESULTS: tuple[str, ...] = ("clean", "match", "multiple")

#: The three policy outcomes step 4 of the flow names explicitly: "(a) blocks
#: the write and shows the existing record, (b) updates the existing record
#: instead, or (c) creates the duplicate anyway with an acknowledgement."
POLICIES: tuple[str, ...] = ("block", "update", "allow", "merge")

#: What the research's extensibility note adds: "Dedupe policy is a
#: per-connection enum (block / update / merge), so a deployment can escalate to
#: 'auto-merge' for high-confidence cases."
ESCALATION_POLICY = "merge"

#: Every terminal state a decision can land in. Each one is traceable to a line
#: of the research; ``OUTCOME_POLICY`` is the mapping.
OUTCOMES: tuple[str, ...] = (
    "created",
    "updated",
    "blocked",
    "created_duplicate",
    "hard_blocked",
    "escalated",
)

#: The vendors whose duplicate behaviour the research documents. Each one is a
#: different mechanism for the same rule, and the vocabulary publishes the
#: mechanism so a rep can see *why* a decision came out the way it did.
VENDORS: tuple[str, ...] = ("salesforce", "hubspot", "dataverse")

#: How each vendor enforces uniqueness, as the research describes it. Sourced
#: prose, kept as data so the page can show it rather than a reviewer having to
#: remember which sentence came from where.
VENDOR_MECHANISMS: Mapping[str, str] = {
    "salesforce": (
        "Duplicate Management rules, evaluated on every create, update or upsert without the "
        "caller asking. A duplicate alert returns the matching record id; an external ID matching "
        "more than one record answers 300 and creates or updates nothing."
    ),
    "hubspot": (
        "A property created with hasUniqueValue: true. Upsert by idProperty so a repeat write "
        "updates rather than duplicates. Email is the primary unique identifier for a contact."
    ),
    "dataverse": (
        "Alternate keys. 'Alternate keys use database indexes to enforce uniqueness and optimize "
        "lookup performance', so a duplicate key write fails."
    ),
}


# --------------------------------------------------------------------------- #
# Matching keys
# --------------------------------------------------------------------------- #


def normalise_exact(value: Any) -> str:
    """Trimmed, case-sensitive. External IDs and account numbers are opaque codes.

    Case-folding these would merge values the CRM considers distinct, so the
    safest reading of "unique index over this column" is byte equality.
    """
    return "" if value is None else str(value).strip()


def normalise_email(value: Any) -> str:
    """Case-insensitive. Two addresses differing only in case are one address."""
    return "" if value is None else str(value).strip().casefold()


def normalise_domain(value: Any) -> str:
    """Case-insensitive, without a leading ``www.`` or a trailing dot.

    ``WWW.Example.com.`` and ``example.com`` are the same company's domain in
    every registry that matters, and HubSpot treats the domain as a unique
    identifier, so a trailing dot that is invisible in a browser would otherwise
    create a duplicate company.
    """
    text = "" if value is None else str(value).strip().casefold()
    if text.startswith("www."):
        text = text[4:]
    return text.rstrip(".")


#: The matching keys the research's ``data_sources`` names: "CRM matching keys
#: (email, domain, external ID, account number)". Order is precedence - see
#: :data:`KEY_PRECEDENCE_RATIONALE` and the ``key-precedence`` inference.
MATCH_KEYS: tuple[dict[str, Any], ...] = (
    {
        "key": "external_id",
        "label": "External ID",
        "normalise": normalise_exact,
        "unique_by_default": False,
        "sourced_from": (
            "Salesforce: upsert by external ID, and a 300 when the external ID exists in more "
            "than one record. Dataverse: an alternate key over the same idea."
        ),
    },
    {
        "key": "email",
        "label": "Email",
        "normalise": normalise_email,
        "unique_by_default": True,
        "sourced_from": (
            "HubSpot: 'It's recommended to always include `email`, because email address is the "
            "primary unique identifier to avoid duplicate contacts in HubSpot.'"
        ),
    },
    {
        "key": "account_number",
        "label": "Account number",
        "normalise": normalise_exact,
        "unique_by_default": False,
        "sourced_from": (
            "Named in the research's data_sources as a CRM matching key. Dataverse alternate "
            "keys are the mechanism; no vendor page quoted for the value format."
        ),
    },
    {
        "key": "domain",
        "label": "Domain",
        "normalise": normalise_domain,
        "unique_by_default": False,
        "sourced_from": (
            "HubSpot: for contacts and companies 'there are additional unique identifiers, "
            "including a contact's email address (`email`) and a company's domain name (`domain`)'."
        ),
    },
)

#: The keys that carry ``hasUniqueValue`` / a ``Unique`` index out of the box.
#: Only email: HubSpot says so outright, and the other three are configurable
#: per connection rather than assumed.
DEFAULT_UNIQUE_KEYS: tuple[str, ...] = ("email",)

KEY_PRECEDENCE_RATIONALE = (
    "When several keys match different records, the most authoritative wins. An external ID is a "
    "value the connector itself wrote, so a match on it is a statement of identity; an email match "
    "is the CRM's own primary unique identifier; an account number is an org-level identifier; a "
    "domain is the weakest, since a domain is shared by every person at a company."
)

#: What each vendor's duplicate rule is called in its own admin UI. The research
#: names all three surfaces; they are settings a rep has to find, so the page
#: shows them rather than a reviewer having to remember.
VENDOR_FEATURES: Mapping[str, tuple[str, ...]] = {
    "salesforce": (
        "Settings > Duplicate Management (duplicate rules, matching rules, auto-merge)",
        f"{DUPLICATE_RULE_HEADER_NAME} REST request header (API {DUPLICATE_RULE_API_VERSION}+)",
    ),
    "hubspot": (
        "A property created with hasUniqueValue: true",
        "Settings > Duplicate management",
    ),
    "dataverse": (
        "Table > Keys (alternate keys)",
    ),
}

#: The policy this build applies when a connection does not say. Named in the
#: ``default-policy`` inference: blocking is the only choice that is reversible
#: after the fact, because a wrong ``update`` has already overwritten a CRM row.
DEFAULT_POLICY = "block"

#: The vendor assumed when a connection does not say. Salesforce is the vendor
#: the research quotes most precisely - it is the only one with a documented
#: header and a documented status code.
DEFAULT_VENDOR = "salesforce"

KEY_INDEX = {entry["key"]: entry for entry in MATCH_KEYS}


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def key_spec(key: str) -> dict[str, Any]:
    """The published spec for one matching key, or a refusal."""
    spec = KEY_INDEX.get(key)
    if spec is None:
        raise DedupeError(
            f"unknown matching key {key!r}; published keys are {', '.join(KEY_INDEX)}"
        )
    return spec


def require_keys(keys: Any) -> tuple[str, ...]:
    """Validate a requested key list, preserving the published precedence order.

    A caller may list the keys in any order and the same keys always resolve in
    the same precedence, so a connection cannot change the answer by reordering
    its own configuration.
    """
    if keys is None:
        return tuple(entry["key"] for entry in MATCH_KEYS)
    if isinstance(keys, str):
        raise DedupeError("keys must be a list of matching-key names, not a string")
    if not isinstance(keys, (list, tuple)):
        raise DedupeError("keys must be a list of matching-key names")
    requested = {str(name) for name in keys}
    for name in sorted(requested):
        key_spec(name)
    return tuple(entry["key"] for entry in MATCH_KEYS if entry["key"] in requested)


def require_policy(policy: Any) -> str:
    """Validate a dedupe policy against the published per-connection enum."""
    if policy is None:
        return DEFAULT_POLICY
    text = str(policy).strip().casefold()
    if text not in POLICIES:
        raise DedupeError(f"unknown dedupe policy {policy!r}; published policies are {', '.join(POLICIES)}")
    return text


def require_vendor(vendor: Any) -> str:
    if vendor is None:
        return DEFAULT_VENDOR
    text = str(vendor).strip().casefold()
    if text not in VENDORS:
        raise DedupeError(f"unknown CRM vendor {vendor!r}; published vendors are {', '.join(VENDORS)}")
    return text


def require_result(result: Any) -> str:
    text = str(result).strip().casefold()
    if text not in CRM_RESULTS:
        raise DedupeError(f"unknown CRM duplicate result {result!r}; published results are {', '.join(CRM_RESULTS)}")
    return text


# --------------------------------------------------------------------------- #
# The header
# --------------------------------------------------------------------------- #


def build_duplicate_rule_header(
    policy: str,
    *,
    run_as_current_user: bool = False,
) -> dict[str, bool]:
    """The ``Sforce-Duplicate-Rule-Header`` options this policy asks the CRM for.

    Each field is set by what the researched policy has to do, and the mapping is
    the interesting part:

    ``allowSave``
        Step 4(c) of the flow is "creates the duplicate anyway with an
        acknowledgement", which is exactly what the header's own description is -
        "allow the user to acknowledge the alert and save the duplicate record".
        So ``allowSave`` is true for ``allow`` and for nothing else.

    ``includeRecordDetails``
        "return all fields in the duplicate record". Steps 4(a) and 4(b) both
        need the duplicate's fields - one to *show* the existing record, one to
        *update* it - and so does the merge escalation, which has to see both
        sides before a human can decide. So it is true for every policy that is
        not ``allow``.

    ``runAsCurrentUser``
        "use the current user's sharing rules". This one is not about the
        duplicate decision at all, it is about visibility, so it is a per
        connection setting rather than a function of the policy. It defaults to
        false because the research says every field's default is false.

    Fields that end up false are *omitted*, not sent as ``"false"``, because the
    researched default for all three is false.
    """
    resolved = require_policy(policy)
    header: dict[str, bool] = {}
    if resolved == "allow":
        header["allowSave"] = True
    else:
        header["includeRecordDetails"] = True
    if run_as_current_user:
        header["runAsCurrentUser"] = True
    return header


def serialise_duplicate_rule_header(header: Mapping[str, bool]) -> str:
    """The header's wire form: a JSON object of strings, as Salesforce sends it.

    Kept beside :func:`build_duplicate_rule_header` so the mapping from a policy
    to a real header is testable without a network, and so a client rendering
    the decision can show the caller what would go on the wire.
    """
    return json.dumps({name: "true" for name, value in header.items() if value}, sort_keys=True)


def header_defaults() -> dict[str, bool]:
    """A copy of the researched defaults, so a caller cannot mutate the source."""
    return dict(DUPLICATE_RULE_HEADER_DEFAULTS)


def published_vocabulary() -> dict[str, Any]:
    """Every published term, served as data so a client renders its pickers from
    the same source the validator enforces against.

    Named ``published_vocabulary`` rather than ``vocabulary`` on purpose. This
    module is ``dsr.dedupe.vocabulary``, so a function called ``vocabulary``
    exported from the package ``__init__`` would shadow the submodule - and
    ``from dsr.dedupe import vocabulary`` would then hand back the *function* to
    anyone who meant the module. That is a real trap, not a hypothetical one: it
    is what this build's own feature module hit first.
    """
    return {
        "header_name": DUPLICATE_RULE_HEADER_NAME,
        "api_version": DUPLICATE_RULE_API_VERSION,
        "duplicate_rule_header": list(DUPLICATE_RULE_HEADER),
        "header_defaults": header_defaults(),
        "policies": list(POLICIES),
        "outcomes": list(OUTCOMES),
        "crm_results": list(CRM_RESULTS),
        "vendors": list(VENDORS),
        "vendor_mechanisms": dict(VENDOR_MECHANISMS),
        "vendor_features": {name: list(items) for name, items in VENDOR_FEATURES.items()},
        "match_keys": [
            {
                **{name: value for name, value in entry.items() if name != "normalise"},
                "unique": entry["key"] in DEFAULT_UNIQUE_KEYS,
            }
            for entry in MATCH_KEYS
        ],
        "default_unique_keys": list(DEFAULT_UNIQUE_KEYS),
        "default_policy": DEFAULT_POLICY,
        "default_vendor": DEFAULT_VENDOR,
        "multiple_match_status": MULTIPLE_MATCH_STATUS,
        "key_precedence": [entry["key"] for entry in MATCH_KEYS],
        "key_precedence_rationale": KEY_PRECEDENCE_RATIONALE,
    }
