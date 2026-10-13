"""WF-072: enforce view-only access and block bulk download.

A build from a researched specification, not a port. The specification is
``docs/research/digital-sales-room-workflows/wf/WF-072.md``, quoted in full in issue 134.
The rules live in this package and are not restated in the feature module.

Four modules carry the workflow, and none of them knows about HTTP:

``vocabulary``
    Every researched term, quoted from the specification, with the evidence it came from:
    the three controls and their safe defaults, the collections this workflow owns, the
    two collections it only reads, the view-record shape the OpenAPI schema fixes, and
    the download-type values this build derives.
``rules``
    The pure rules: coercing the CLI's ``on``/``off`` spelling, validating a room policy
    and a link policy, reading a per-item grant out of the permission rows another
    workflow owns, and the three-way AND that decides a download.
``inferences``
    The judgement calls the specification left open, each recorded with the alternative
    rejected and why.
``engine``
    The reads and the writes: the room policy, the link policy, the per-item grant it
    reads, the decision, the board, and the synchronous download path that writes a view
    row only when the three controls agree.

What this package owns, and what it only reads
----------------------------------------------

WF-074 is merged and already owns ``wf074_link`` (carrying ``allow_download``) and the
per-item ACL rows ``wf074_link_permission`` / ``wf074_dataroom_group_permission``
(carrying ``can_download``). The collision ruling in
``orchestration/decisions/WF-072-PACKAGE-COLLISION.md`` (``jev-20261004T145853-27760-33511``)
renamed this workflow's package to ``view_only_access`` and requires it to respect that
boundary. So this package owns its own room policy and its own link policy, and **reads**
the per-item ``can_download`` grant out of WF-074's permission rows by collection name. It
never imports ``dsr.audience_permissions`` and never writes those rows on the request path.
The ownership split is the design decision recorded as ``jev-20261013T150724-2296-44663``
at confidence 0.98.

Nothing here imports ``dsr.api``, and nothing here opens SQLite. Every read and write goes
through the :class:`~dsr.store.RecordStore` the HTTP layer hands in, so the audit row is
written in the same transaction as the change.
"""

from dsr.view_only_access import (  # noqa: F401
    engine,
    inferences,
    rules,
    vocabulary,
)

__all__ = [
    "engine",
    "inferences",
    "rules",
    "vocabulary",
]
