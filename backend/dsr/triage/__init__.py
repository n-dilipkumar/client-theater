"""WF-022: triage the pipeline with saved workspace views.

The domain layer, kept out of the feature module so the researched rules are
testable without FastAPI, a database, or an HTTP client. The feature module at
``backend/dsr/features/wf022_triage_the_pipeline_with_saved_workspa.py`` is the
three things a feature has to add: the route table, the error mapping, and the
demo rows.

The modules, and what each one owns:

:mod:`dsr.triage.vocabulary`
    The column catalog, the operator set, and the five default views. Data, not
    behaviour, so a client renders its pickers from the same list the validator
    enforces against.
:mod:`dsr.triage.fields`
    Scalar coercion and field resolution over arbitrary JSON, with synonym lists.
    No opinions about anyone's schema; a value it cannot read is ``None``.
:mod:`dsr.triage.filters`
    The filter and sort engine, including the OR that Active Pipeline's two arms
    need, and the asymmetric failure policy: an unreadable filter is dropped and
    reported, an unreadable sort is refused.
:mod:`dsr.triage.rows`
    The join. Workspace metadata, engagement metrics, CRM objects and order
    forms, all resolved on read, with CRM columns gated on the provider and the
    workspace type resolved through its template.
:mod:`dsr.triage.views`
    The saved view itself: add, clone, edit, the private/public boundary, the
    remembered open set, template type inheritance, and dynamic section
    visibility. Every write takes a required ``source``.
:mod:`dsr.triage.inferences`
    Every judgement call the research left open, named and served at
    ``/api/wf-022/inferences``.
:mod:`dsr.triage.errors`
    The one domain error type the feature maps to a response.
"""

from __future__ import annotations

from dsr.triage.errors import TriageError
from dsr.triage.views import (
    DASHBOARD_STATE_COLLECTION,
    SECTION_RULE_COLLECTION,
    VIEW_COLLECTION,
    TriageBoard,
    parse_properties,
)

__all__ = [
    "DASHBOARD_STATE_COLLECTION",
    "SECTION_RULE_COLLECTION",
    "TriageBoard",
    "TriageError",
    "VIEW_COLLECTION",
    "parse_properties",
]
