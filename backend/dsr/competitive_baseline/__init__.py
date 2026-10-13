"""WF-122: the competitive-baseline domain for a tiered data room.

This package holds the pure rules, the vocabulary and the error types for one
workflow: a data room that reveals sections as trust grows. A seller assigns
each section a minimum tier, every recipient starts at tier zero, and the
seller unlocks financials, legal or security for specific recipients as
diligence progresses.

The package imports the store and its own modules. It imports no framework, it
opens no database connection, and it names no route. Everything here is
exercised without a server, which is what makes each rule below checkable at the
instant it is stated about.

The researched specification is
``docs/research/digital-sales-room-workflows/wf/WF-122.md`` and it is quoted in
full in issue 132. The design decisions the research left open are recorded in
:mod:`dsr.competitive_baseline.vocabulary` and served by the vocabulary route, so
a reviewer reads the decision rather than the source.

WF-125 names this same package in its own issue text. WF-125 is not dispatched,
so this build owns the package for now. A later WF-125 branch that needs the
package must rebase onto this one first.
"""

from __future__ import annotations

from .engine import DisclosureEngine
from .errors import DisclosureError, DisclosureNotFound, DisclosureRefused

__all__ = [
    "DisclosureEngine",
    "DisclosureError",
    "DisclosureNotFound",
    "DisclosureRefused",
]
