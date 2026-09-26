#!/usr/bin/env python3
"""One-off architecture decision for WF-004 (run from the repo root).

    ./.venv/Scripts/python tools/wf004_decide.py

Kept in the tree so the reasoning behind the record shape is reproducible
rather than a memory of a chat. Every call appends to the shared audit log.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jev import Jev  # noqa: E402

PROBLEM = """
WF-004 must represent three related things, all stored as arbitrary JSON in
records.data (no migrations, no typed columns):

  (a) a pending email invitation carrying a 48-hour acceptance window;
  (b) an active access grant carrying {role, accessValidUntil}, which lapses
      silently at the end of the expiration date in UTC and then stops
      appearing in the "Who Has Access" list;
  (c) the documented rule that an invitee who already belongs to the room
      gains access immediately on send, while a new invitee gains it only on
      accepting the emailed invitation.

It must also enforce the documented delegation rule: Room Collaborators and
Content Contributors may assign Content Contributor and Viewer only, while only
the room owner/administrator may assign Room Collaborator, and the Owner can
never be changed or removed.

How should the records be shaped?
"""

OPTIONS = {
    "two_collections": (
        "Two collections. `room_invitation` holds the emailed invitation "
        "(state pending/accepted/expired, token, 48h window). `room_access` "
        "holds the grant ({role, accessValidUntil, principal}) and is created "
        "either at send time (known member) or at accept time (new invitee). "
        "The invitation records the provenance of a grant via invitation_id."
    ),
    "single_lifecycle": (
        "One collection. A `room_access` row is created at send time carrying "
        "state pending/active/removed alongside role and expiry, and accepting "
        "the invitation simply flips state to active. Grants and invitations "
        "are the same row at different points in one lifecycle."
    ),
    "invitation_only": (
        "No dedicated grant collection. Only `room_invitation` rows are stored "
        "and the Who Has Access list is derived by joining invitations against "
        "a separate identity record at read time."
    ),
}

CONTEXT = {
    "hard_constraint": (
        "AGENTS.md: record payloads are arbitrary JSON in records.data; a team "
        "adding a field must not need coordination. The envelope (id, "
        "collection, room_id, revision, created_at, updated_at, deleted_at) is "
        "the only fixed vocabulary."
    ),
    "audit_requirement": (
        "Every mutation must write an audit row in the same transaction, via "
        "AuditedDatabase. Read paths must not mutate."
    ),
    "silent_expiry": (
        "Access ends at the end of the expiration date in UTC and the person "
        "then no longer appears in the Who Has Access list. This is a silent "
        "read-time cut-off, not a background job."
    ),
    "same_role_and_date_for_all": (
        "Documented: one role and one expiration date apply to the whole "
        "invitation; per-person differences are made afterwards in the "
        "Who Has Access list."
    ),
    "filtering": (
        "find() resolves dotted JSON paths through the dynamic index, so "
        "role and state are filterable without a schema change."
    ),
}

if __name__ == "__main__":
    client = Jev()
    record = client.choose_approach(
        problem=PROBLEM.strip(), options=OPTIONS, context=CONTEXT
    )
    print(record.summary())
