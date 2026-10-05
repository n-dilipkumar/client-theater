"""WF-074: the one design question this finished change actually raises.

A general link with no link permission rows. What does the room show that viewer?

The evidence is quoted and it points both ways:

* The guide: "A new group sees **nothing** until you grant permissions." That sentence
  is scoped to a group.
* openapi.json on UpdateLinkPermissionsRequest: "The complete desired permission state
  for this link (full-replace semantics...) An empty array clears all overrides, which
  hides every item on links relying on this permission set."

The second sentence reads two ways. "Clears all overrides" implies the overrides are a
layer over a base the link already has, which means clearing them opens the room back up.
"Which hides every item on links relying on this permission set" reads as the clearing
itself hiding everything, which means the link has no base and default-deny applies.

The build has to pick one, and the choice decides what the view endpoint returns for a
general link that nobody has scoped.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

os.environ["JEV_AUDIT_LOG"] = str(ROOT / "orchestration" / "decisions" / "wf074-jev-audit.jsonl")

from jev import Jev  # noqa: E402

PROBLEM = (
    "WF-074 scopes what a dataroom audience can see. A group link takes its visibility "
    "from the group's per-item ACL and defaults to deny. A general link has its own "
    "full-replace ACL. What does a general link show a viewer when it carries no link "
    "permission rows at all, either because none was ever set or because an empty "
    "full-replace array cleared them?"
)

OPTIONS = {
    "no_overrides_means_no_narrowing": (
        "A general link's own ACL is an override layer over the room it points at. With no "
        "overrides stored, nothing is narrowed, so every item in the room is visible and "
        "downloadable. The default-deny sentence is scoped to groups, and a general link "
        "with no overrides behaves like the plain link other workflows already create."
    ),
    "default_deny_in_both_scopes": (
        "The default is deny for both scopes. A general link with no ACL row shows nothing, "
        "which is the plainest reading of 'an empty array clears all overrides, which hides "
        "every item on links relying on this permission set'. Every link in the product then "
        "needs an explicit grant before anybody sees a byte."
    ),
    "refuse_until_scoped": (
        "A general link carrying no ACL row is refused outright with a 403 until a rep "
        "grants at least one entry. This never grants by accident and never hides by "
        "accident, and it makes the unscoped state a visible error rather than a silent "
        "empty room."
    ),
}

CONTEXT = {
    "sourced_default_deny_sentence": (
        'The guide: "A new group sees nothing until you grant permissions." The sentence '
        "names a group and does not say what a general link does."
    ),
    "sourced_full_replace_sentence": (
        'openapi.json UpdateLinkPermissionsRequest.permissions: "The complete desired '
        'permission state for this link (full-replace semantics...) An empty array clears '
        'all overrides, which hides every item on links relying on this permission set."'
    ),
    "sourced_scope_conflict_rule": (
        'A link override on a group link is "Rejected with 422 on links with audience_type: '
        'group - their group determines visibility; switch the link to audience_type: '
        'general first." So a rep who wants per-item scoping on a general link gets it by '
        'switching the audience, which means the general path is the documented way to do '
        'exactly this workflow.'
    ),
    "item_types": ["dataroom_document", "dataroom_folder"],
    "flags_per_entry": ["can_view", "can_download"],
    "caps": {"domains": 100, "emails_per_call": 500, "permissions_per_call": 1000},
    "already_built_and_tested_in_this_branch": [
        "rules.require_link_scope refuses link overrides on a group link rather than picking a winner",
        "rules.apply_full_replace removes rows the payload omits and an empty payload clears all",
        "rules.decide_item has no wildcard and no inheritance for an absent row",
    ],
    "what_is_at_stake": (
        "One branch in the view endpoint and one rule in rules.py. Choosing deny-by-default "
        "in both scopes is the fail-closed answer and makes an unscoped general link useless "
        "by accident. Choosing no-narrowing is the fail-open answer and makes a cleared ACL "
        "silently reopen the room. Refusing turns the unscoped state into a visible error."
    ),
    "product_pressure": (
        "Other workflows in this repository already create general-audience links that show a "
        "room. If a general link with no ACL showed nothing here, a viewer admitted by one of "
        "those links would be refused by this workflow's view endpoint on the same link."
    ),
}


def main() -> int:
    client = Jev()
    record = client.choose_approach(
        problem=PROBLEM,
        options=OPTIONS,
        context=CONTEXT,
        threshold=0.75,
    )
    print(record.summary())
    print()
    print("chosen option:", record.selected)
    return 0 if record.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
