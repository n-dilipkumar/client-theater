"""WF-041: detect and block duplicate records during sync.

The researched workflow, in the order the research states it: a lead or contact
arrives from the room, the connector sends the write with duplicate detection
enabled, the CRM's matching rule answers with a clean create / a duplicate alert
carrying the matching record id / a hard block, the connection's configured
policy decides what happens, and the decision plus the matched record id are
logged on the room row.

Module map, in dependency order:

``vocabulary``
    The researched terms and the ``Duplicate Rule Header`` the policy maps onto.
``matching``
    The registered matchers and the in-room pre-check the extensibility note
    describes. The seam a third party adds a fuzzy matcher through.
``rules``
    The state machine: the three CRM answers, the four policies, and the two
    researched hard blocks that outrank every policy.
``policy``
    The per-connection enum the research calls for, and connection validation.
``engine``
    The flow end to end over the audited store, with the CRM behind one class.
``inferences``
    Every judgement call, named and served over HTTP so a reviewer can disagree
    with one by name.
"""

from __future__ import annotations

from dsr.dedupe.engine import (
    CONNECTION_COLLECTION,
    DECISION_COLLECTION,
    RECORD_COLLECTION,
    ROOM_ANNOTATION_LIMIT,
    ROOM_FIELD,
    DedupeEngine,
    StoreCrm,
)
from dsr.dedupe.errors import AmbiguousMatch, DedupeError, UniqueIndexViolation
from dsr.dedupe.matching import REGISTRY, Match, Matcher, MatcherRegistry, decisive, match_rows
from dsr.dedupe.policy import POLICY_DETAILS, catalogue, describe, normalise_connection
from dsr.dedupe.rules import (
    BLOCKED,
    CREATED,
    CREATED_DUPLICATE,
    ESCALATED,
    HARD_BLOCKED,
    NEEDS_HUMAN,
    OUTCOMES,
    POLICY_OUTCOMES,
    UPDATED,
    Decision,
    DuplicateResult,
    clean,
    decide,
    matched,
    multiple,
)
from dsr.dedupe.vocabulary import (
    DEFAULT_POLICY,
    DEFAULT_UNIQUE_KEYS,
    DEFAULT_VENDOR,
    DUPLICATE_RULE_API_VERSION,
    DUPLICATE_RULE_HEADER,
    DUPLICATE_RULE_HEADER_DEFAULTS,
    DUPLICATE_RULE_HEADER_NAME,
    ESCALATION_POLICY,
    MATCH_KEYS,
    MULTIPLE_MATCH_STATUS,
    POLICIES,
    VENDORS,
    build_duplicate_rule_header,
    header_defaults,
    key_spec,
    normalise_domain,
    normalise_email,
    normalise_exact,
    published_vocabulary,
    require_keys,
    require_policy,
    require_result,
    require_vendor,
    serialise_duplicate_rule_header,
)

__all__ = [
    "AmbiguousMatch",
    "BLOCKED",
    "CONNECTION_COLLECTION",
    "CREATED",
    "CREATED_DUPLICATE",
    "DECISION_COLLECTION",
    "DEFAULT_POLICY",
    "DEFAULT_UNIQUE_KEYS",
    "DEFAULT_VENDOR",
    "DUPLICATE_RULE_API_VERSION",
    "DUPLICATE_RULE_HEADER",
    "DUPLICATE_RULE_HEADER_DEFAULTS",
    "DUPLICATE_RULE_HEADER_NAME",
    "DedupeEngine",
    "DedupeError",
    "Decision",
    "DuplicateResult",
    "ESCALATED",
    "ESCALATION_POLICY",
    "HARD_BLOCKED",
    "MATCH_KEYS",
    "MULTIPLE_MATCH_STATUS",
    "Match",
    "Matcher",
    "MatcherRegistry",
    "NEEDS_HUMAN",
    "OUTCOMES",
    "POLICIES",
    "POLICY_DETAILS",
    "POLICY_OUTCOMES",
    "RECORD_COLLECTION",
    "REGISTRY",
    "ROOM_ANNOTATION_LIMIT",
    "ROOM_FIELD",
    "StoreCrm",
    "UPDATED",
    "UniqueIndexViolation",
    "VENDORS",
    "build_duplicate_rule_header",
    "catalogue",
    "clean",
    "decisive",
    "decide",
    "describe",
    "header_defaults",
    "key_spec",
    "matched",
    "match_rows",
    "multiple",
    "normalise_connection",
    "normalise_domain",
    "normalise_email",
    "normalise_exact",
    "published_vocabulary",
    "require_keys",
    "require_policy",
    "require_result",
    "require_vendor",
    "serialise_duplicate_rule_header",
]
