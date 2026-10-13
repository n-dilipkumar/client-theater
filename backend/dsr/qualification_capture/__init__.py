"""WF-123: in-room qualification capture.

A short qualifying form renders inside a room, captures the buyer's answers
without leaving the room, and attaches them to the deal record. The answers are
scored into a qualification band, and the band drives a routing rule. The
qualification summary is visible to the seller only.

Module map, in dependency order:

``vocabulary``
    The researched terms: the four dimensions and their scale, the bands, the
    visibility rule, the collections, and every sourced quote. It re-exports the
    room vocabulary from :mod:`dsr.room_lifecycle` and the visitor vocabulary from
    :mod:`dsr.access` rather than restating either.
``errors``
    One error per distinct answer, each with a stable code.
``rules``
    The pure rules: form validation, answer validation, the score and the band,
    and the routing target. No store, no framework.
``engine``
    :class:`~dsr.qualification_capture.engine.QualificationEngine` - the only
    module that reads or writes. Everything goes through the audited store.
``inferences``
    Every judgement call the research left open, named and served over HTTP.

Nothing here imports the framework application module or its dependency seam.
The store seam is :class:`~dsr.store.RecordStore`.
"""

from __future__ import annotations

from dsr.qualification_capture import errors, inferences, rules, vocabulary
from dsr.qualification_capture.engine import QualificationEngine
from dsr.qualification_capture.errors import (
    CaptureConflict,
    CaptureNotFound,
    FormValidationError,
    QualificationError,
)
from dsr.qualification_capture.inferences import INFERENCES, by_id, describe_inferences
from dsr.qualification_capture.rules import (
    band_for,
    form_dimensions,
    form_is_live,
    max_score,
    missing_required,
    normalise_answers,
    normalise_form_payload,
    parse_timestamp,
    qualify,
    required_dimensions,
    routing_target,
    score_answers,
    to_iso,
    weight_of,
)
from dsr.qualification_capture.vocabulary import (
    ACTIVE,
    ARCHIVED,
    BAND_DEVELOPING,
    BAND_LABELS,
    BAND_QUALIFIED,
    BAND_UNQUALIFIED,
    BANDS,
    COLLECTIONS,
    DIMENSIONS,
    EVENT_COLLECTION,
    FEATURE_ID,
    FORM_COLLECTION,
    FORM_STATUS_DRAFT,
    FORM_STATUS_LIVE,
    FORM_STATUSES,
    NAME,
    PREFIX,
    ROOM_COLLECTION,
    ROOM_REF,
    ROUTE_COLLECTION,
    ROUTE_TARGET_BY_BAND,
    SELLER_ONLY_FIELDS,
    SUBMISSION_COLLECTION,
    SUBMISSION_STATUS_DRAFT,
    SUBMISSION_STATUS_SUBMITTED,
    SUBMISSION_STATUSES,
    TICKET,
    VISIBILITY_BUYER,
    VISIBILITY_SELLER_ONLY,
    VISITOR_SESSION_COLLECTION,
    VISITOR_SESSION_REF,
    dimension_payload,
    published_vocabulary,
    status_of,
)

__all__ = [
    "ACTIVE",
    "ARCHIVED",
    "BANDS",
    "BAND_DEVELOPING",
    "BAND_LABELS",
    "BAND_QUALIFIED",
    "BAND_UNQUALIFIED",
    "COLLECTIONS",
    "CaptureConflict",
    "CaptureNotFound",
    "DIMENSIONS",
    "EVENT_COLLECTION",
    "FEATURE_ID",
    "FORM_COLLECTION",
    "FORM_STATUSES",
    "FORM_STATUS_DRAFT",
    "FORM_STATUS_LIVE",
    "FormValidationError",
    "INFERENCES",
    "NAME",
    "PREFIX",
    "QualificationEngine",
    "QualificationError",
    "ROOM_COLLECTION",
    "ROOM_REF",
    "ROUTE_COLLECTION",
    "ROUTE_TARGET_BY_BAND",
    "SELLER_ONLY_FIELDS",
    "SUBMISSION_COLLECTION",
    "SUBMISSION_STATUSES",
    "SUBMISSION_STATUS_DRAFT",
    "SUBMISSION_STATUS_SUBMITTED",
    "TICKET",
    "VISIBILITY_BUYER",
    "VISIBILITY_SELLER_ONLY",
    "VISITOR_SESSION_COLLECTION",
    "VISITOR_SESSION_REF",
    "band_for",
    "by_id",
    "describe_inferences",
    "dimension_payload",
    "errors",
    "form_dimensions",
    "form_is_live",
    "inferences",
    "max_score",
    "missing_required",
    "normalise_answers",
    "normalise_form_payload",
    "parse_timestamp",
    "published_vocabulary",
    "qualify",
    "required_dimensions",
    "routing_target",
    "rules",
    "score_answers",
    "status_of",
    "to_iso",
    "vocabulary",
    "weight_of",
]
