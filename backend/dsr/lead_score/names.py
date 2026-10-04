"""The six collections this workflow owns.

Named in one place because a collection name is a wire contract. The seeder, the
page, the audit trail and every test read the name from here rather than from a
string typed next to the code that uses it, so a collection that gets renamed
gets renamed once.

None of these is a migration and none has a typed column. Every record is
ordinary JSON in ``records.data`` with only the envelope fixed (``id``,
``collection``, ``room_id``, ``revision``, ``created_at``, ``updated_at``,
``deleted_at``), so a team adding a field to any of them needs no coordination
with anyone. Filter with ``find()`` / ``?where=...``, which resolves dotted JSON
paths through the dynamic index.
"""

from __future__ import annotations

#: Step 1 of the researched flow: "Confirm the Dock and HubSpot integration is
#: enabled and that the workspace is connected to a deal/account." One row per CRM
#: organisation. The token and its scopes live on the row, because the two scopes
#: the research names are what decide whether a criterion can be armed at all.
INTEGRATIONS = "lead_score_integration"

#: The Dock activity properties a criterion scores against. The researched flow
#: says "choose the Dock property you want to score against", and the issue records
#: that these come from provisioning the engagement object. A criterion naming a
#: property nobody provisioned matches nothing, which is the issue's own sentence
#: about the missing dependency made into a stored fact.
PROPERTIES = "lead_score_property"

#: One row per "Add criteria" row: a family, its filters, a bucket and a score.
CRITERIA = "lead_score_criterion"

#: The DSR activity events a criterion is evaluated against. Kept separately from
#: the core ``activity`` collection because these rows carry the fields the
#: refinement matrix reads (``link_name``, ``file_name``, ``task_name``) and the
#: core seeder's rows do not all carry them.
ACTIVITY = "lead_score_activity"

#: One row per (room, contact): the score as recomputed from that contact's whole
#: activity history, plus the contributions that produced it. This is the
#: ``HubSpot Score`` contact property as this product holds it.
CONTACTS = "lead_score_contact"

#: One row per evaluation. The researched rule is continuous, so a run is recorded
#: for every event that reached the scoring step whether or not the number moved,
#: and each run carries what would have been written to the CRM.
RUNS = "lead_score_run"
