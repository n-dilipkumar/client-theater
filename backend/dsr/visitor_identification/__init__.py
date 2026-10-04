"""WF-031: identify anonymous web visitors as companies and filter by pages visited.

The workflow, as a package rather than as one module, so no two features can claim
one path and so the pieces can be tested on their own:

============================  =========================================
:mod:`~dsr.visitor_identification.capture`   one anonymous request, read and checked
:mod:`~dsr.visitor_identification.paths`     page paths and the three match conditions
:mod:`~dsr.visitor_identification.pages`     the Pages list
:mod:`~dsr.visitor_identification.company`   the company record, and how one is found
:mod:`~dsr.visitor_identification.leads`     the filtered, ranked lead list
:mod:`~dsr.visitor_identification.vocabulary`  every published value, served as data
:mod:`~dsr.visitor_identification.inferences`  every decision the research does not make
:mod:`~dsr.visitor_identification.errors`     the refusals, and their statuses
:mod:`~dsr.visitor_identification.engine`     the facade the HTTP layer calls
============================  =========================================

The specification is ``docs/research/digital-sales-room-workflows/wf/WF-031.md``,
which is section 16 of ``docs/research/raw/analytics-intent.md``. Every rule in
this package cites the sentence it implements, in the docstring of the function
that implements it, so a reviewer can check the behaviour against the
specification without reading the tests first.

The one rule that shapes the whole package is the vendor's own privacy stance:
"Albacross focuses on exclusively company-level identification rather than
tracking individual users, ensuring respect for user privacy." It is not a
disclaimer in the documentation. It is the shape of the code:

* the capture vocabulary is **closed** at the three public parameters the research
  names, so a person-level field cannot be accepted by configuration;
* every person-level key is **refused by name**, with its own error;
* the only thing a capture can resolve to is a network, and therefore a company;
* the contact list carries a name and a role and **no email address**.

Nothing in this package can create a record about an individual, and
``backend/tests/test_wf031.py`` proves it by counting the records a capture leaves
behind rather than by quoting this docstring.
"""

from __future__ import annotations

from dsr.visitor_identification.capture import (
    DEFAULT_ACTOR,
    MAX_ADDRESS_LENGTH,
    MAX_CLIENT_ID_LENGTH,
    MAX_COUNTRY_LENGTH,
    MAX_NETWORK_LENGTH,
    Capture,
    parse_capture,
)
from dsr.visitor_identification.company import (
    blank_company,
    counters_of,
    derive_company_key,
    detail_of,
    find_company,
    keyed,
    parse_contacts,
    parse_detail,
    require_company,
    resolve,
    slug,
    with_capture,
)
from dsr.visitor_identification.engine import VisitorEngine
from dsr.visitor_identification.errors import (
    CompanyAlreadyIdentified,
    CompanyKeyRequired,
    InvalidCapture,
    InvalidCompanyDetail,
    InvalidIcp,
    InvalidInstallation,
    InvalidPageDefinition,
    PathCarriesADomain,
    PersonalDataRefused,
    UnknownCaptureParameter,
    UnknownCompany,
    UnknownIcp,
    UnknownInstallation,
    UnknownLeadFilter,
    UnknownMatchCondition,
    UnknownPage,
    VisitorIdentificationError,
)
from dsr.visitor_identification.inferences import INFERENCES, describe as describe_inferences
from dsr.visitor_identification.leads import (
    DEFAULT_LIMIT,
    ICP_FIELDS,
    MAX_LIMIT,
    LeadFilters,
    build_rows,
    icp_matches,
    matched_page_ids,
    parse_filters,
    parse_icp,
    summarise,
)
from dsr.visitor_identification.pages import IntentPage, amend, of as page_of, parse_page
from dsr.visitor_identification.paths import looks_like_host, matches, normalise_path, qualify
from dsr.visitor_identification.vocabulary import (
    ALLOWED_CAPTURE_KEYS,
    CAPTURE_PARAMETER_LABELS,
    CAPTURE_PARAMETERS,
    COLLECTIONS,
    COMPANIES,
    COMPANY_FIELDS,
    CONTACT_FIELDS,
    DOWNSTREAM_SURFACES,
    ICP_PROFILES,
    IDENTIFICATION_STANCE,
    INSTALLATIONS,
    LEAD_FILTERS,
    MATCH_CONDITION_LABELS,
    MATCH_CONDITIONS,
    PAGES,
    PERSONAL_PARAMETER_NAMES,
    RANKING,
    VISITS,
    describe as describe_vocabulary,
    normalise_condition,
)

__all__ = [
    "ALLOWED_CAPTURE_KEYS",
    "CAPTURE_PARAMETERS",
    "CAPTURE_PARAMETER_LABELS",
    "COLLECTIONS",
    "COMPANIES",
    "COMPANY_FIELDS",
    "CONTACT_FIELDS",
    "DEFAULT_ACTOR",
    "DEFAULT_LIMIT",
    "CompanyAlreadyIdentified",
    "CompanyKeyRequired",
    "Capture",
    "DOWNSTREAM_SURFACES",
    "IDENTIFICATION_STANCE",
    "ICP_FIELDS",
    "ICP_PROFILES",
    "INFERENCES",
    "INSTALLATIONS",
    "IntentPage",
    "InvalidCapture",
    "InvalidCompanyDetail",
    "InvalidInstallation",
    "InvalidIcp",
    "InvalidPageDefinition",
    "LeadFilters",
    "MAX_ADDRESS_LENGTH",
    "MAX_CLIENT_ID_LENGTH",
    "MAX_COUNTRY_LENGTH",
    "MAX_LIMIT",
    "MAX_NETWORK_LENGTH",
    "MATCH_CONDITIONS",
    "MATCH_CONDITION_LABELS",
    "PAGES",
    "PERSONAL_PARAMETER_NAMES",
    "PathCarriesADomain",
    "PersonalDataRefused",
    "RANKING",
    "VISITS",
    "UnknownCaptureParameter",
    "UnknownCompany",
    "UnknownIcp",
    "UnknownInstallation",
    "UnknownLeadFilter",
    "UnknownMatchCondition",
    "UnknownPage",
    "VisitorEngine",
    "VisitorIdentificationError",
    "LEAD_FILTERS",
    "amend",
    "blank_company",
    "build_rows",
    "counters_of",
    "derive_company_key",
    "describe_inferences",
    "describe_vocabulary",
    "detail_of",
    "find_company",
    "icp_matches",
    "keyed",
    "looks_like_host",
    "matched_page_ids",
    "matches",
    "normalise_condition",
    "normalise_path",
    "page_of",
    "parse_capture",
    "parse_contacts",
    "parse_detail",
    "parse_filters",
    "parse_icp",
    "parse_page",
    "qualify",
    "require_company",
    "resolve",
    "slug",
    "summarise",
    "with_capture",
]
