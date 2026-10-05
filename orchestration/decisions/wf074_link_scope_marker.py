"""WF-074: the narrowed question, asked after the first gate came back uncertain.

The first question (audit jev-20261005T051741-9920-61850) came back ``uncertain`` at
confidence 0.58. It asked what a general link shows with no link permission rows, and it
offered three answers. That was the wrong question, because it conflated two states the
source separates, so the options were closer than they needed to be.

The evidence, fetched from https://www.papermark.com/docs/cli/commands/links.mdx, section
``permissions``, separates them explicitly:

* "Per-item file permissions on a dataroom link: control which documents and folders this
  specific link shows, without creating a group. **With no overrides, viewers see the full
  dataroom.**"
* "``--clear``  Remove all overrides and hide every item"

So "a link that was never scoped" and "a link whose scope was cleared" are different states
with opposite outcomes, and a build that stores only rows cannot tell them apart. The real
question is what the build stores so that it can.

The same section also settles two things this build must honour, recorded here as evidence
rather than as arguments:

* "Ancestor folders of visible items stay visible automatically." That applies to link
  permissions as well as group permissions, so the ancestor auto-open is not group-only.
* "On links with a group audience these overrides are ignored; the group's permissions take
  precedence." The CLI says ignored. The OpenAPI description of the same endpoint says
  rejected with 422. This build keeps the refusal, because the CLI sentence describes a
  client that does not report the refusal and the API sentence describes what the server
  does. A build that ignored the override would let a rep believe the grid they are editing
  is the grid in force.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

os.environ["JEV_AUDIT_LOG"] = str(ROOT / "orchestration" / "decisions" / "wf074-jev-audit.jsonl")

from jev import Jev  # noqa: E402

PROBLEM = (
    "WF-074 stores per-item permissions as one row per grant. A link's own permissions use "
    "full-replace semantics, so the stored row set can legitimately be empty. The vendor's "
    "own CLI distinguishes two empty states with opposite outcomes: a link that was never "
    "scoped shows the full dataroom, and a link whose scope was explicitly cleared shows "
    "nothing. What does the build store so those two empty states stay distinguishable?"
)

OPTIONS = {
    "link_carries_a_derived_scope_marker": (
        "The link row carries a marker recording that a link-permission write has happened "
        "on it. The engine derives the marker and never accepts it from a caller. No marker "
        "means the full room, which is the sourced behaviour for an unscoped link. A marker "
        "with zero rows means an explicit clear, which hides every item. Both sourced "
        "sentences become reachable and one extra boolean on one row carries the difference."
    ),
    "absence_always_means_the_full_room": (
        "No marker. An empty row set always means the full room. The documented --clear "
        "behaviour is then unreachable, and the recorded consequence is that a rep who "
        "clears a link's scope believes they hid the room and the room opens up instead."
    ),
    "reconcile_by_writing_a_full_room_scope_on_read": (
        "Treat every existing general link as already scoped by writing an explicit "
        "full-room ACL the first time it is read, so absence never has to be interpreted. "
        "The cost is a write inside a read, which breaks this product's contract that a read "
        "writes nothing and makes an audit row describe work the caller never asked for."
    ),
}

CONTEXT = {
    "sourced_never_scoped_shows_the_full_dataroom": (
        'CLI links.mdx, section permissions: "Per-item file permissions on a dataroom link: '
        "control which documents and folders this specific link shows, without creating a "
        'group. With no overrides, viewers see the full dataroom."'
    ),
    "sourced_explicit_clear_hides_every_item": (
        'CLI links.mdx, same section: "--clear  Remove all overrides and hide every item" and '
        '"--clear ... which hides every item on the link".'
    ),
    "sourced_group_scope_is_default_deny": (
        'The share-dataroom-with-group guide: "A new group sees nothing until you grant '
        'permissions." The default-deny sentence is scoped to a group and does not reach a '
        "general link."
    ),
    "sourced_ancestor_auto_open_is_not_group_only": (
        'CLI links.mdx, same section: "Ancestor folders of visible items stay visible '
        'automatically." So the ancestor write applies to link overrides too.'
    ),
    "group_link_rule_this_build_keeps": (
        "rules.require_link_scope refuses a link override on a group link rather than picking "
        "a winner, on the OpenAPI sentence 'Rejected with 422 on links with audience_type: "
        "group'. The CLI page is looser and says overrides are ignored. The refusal is kept "
        "because it is the stronger of the two sourced statements and because it tells the "
        "rep the truth."
    ),
    "already_built_and_tested_in_this_branch": [
        "rules.apply_full_replace drops rows the payload omits and an empty payload clears all",
        "rules.apply_delta upserts only the rows sent and returns untouched keys",
        "rules.decide_item has no wildcard and no inheritance for an absent row",
        "rules.plan_ancestor_grants returns view-only rows for ancestors needing them",
    ],
    "envelope_is_fixed_and_payload_is_open": (
        "Every record is JSON in records.data with the envelope reserved. The marker is a "
        "payload key on the link row this workflow already writes, so it needs no migration "
        "and no typed column."
    ),
    "what_is_at_stake": (
        "One boolean on the link row and one branch in the read path. Getting it wrong in the "
        "fail-open direction means a rep who cleared a link's scope opens the room instead of "
        "hiding it, which is the over-grant this workflow exists to prevent."
    ),
}

REQUIRED_COSTS = {
    "link_carries_a_derived_scope_marker": "cost: two names for one fact, the marker and the row count",
    "absence_always_means_the_full_room": "cost: the sourced --clear behaviour becomes unreachable",
    "reconcile_by_writing_a_full_room_scope_on_read": "cost: a write inside a read, and an audit row nobody asked for",
}


def main() -> int:
    client = Jev()
    record = client.choose_approach(
        problem=PROBLEM,
        options=OPTIONS,
        context={**CONTEXT, "cost_of_each_option": REQUIRED_COSTS},
        threshold=0.75,
    )
    print(record.summary())
    print()
    print("chosen option:", record.selected)
    return 0 if record.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
